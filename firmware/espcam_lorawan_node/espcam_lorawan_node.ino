/*
 * ==============================================================================
 * ESP32-CAM reader node - LoRaWAN edition
 * Hardware: LILYGO TTGO LoRa32-OLED V2.1 (1.6.1) + ESP32-CAM over UART
 * ==============================================================================
 *
 * This is sender_lilygo_wifi.ino brought into the Smart Building system. The
 * camera link, the frame wire format, the calibration page and the command set
 * are all unchanged -- that part works, and it is left alone. What changed is
 * everything below the radio and everything about how the board is set up.
 *
 * WHAT CHANGED, AND WHY
 * ---------------------
 * 1. Raw LoRa  ->  LoRaWAN (OTAA).
 *    Packets no longer go point-to-point to a private gateway sketch. The node
 *    joins The Things Stack and its uplinks arrive through the same webhook,
 *    auto-discovery and telemetry pipeline as every other device.
 *
 * 2. The MAC field is gone from the payload.
 *    It existed so one shared gateway could tell cameras apart. LoRaWAN already
 *    does that: every uplink is identified by its DevEUI, which this board
 *    derives from its own chip MAC exactly as the other node firmware does. Six
 *    bytes of every packet saved, and no chance of two boards colliding.
 *
 * 3. Hard-coded Wi-Fi credentials  ->  the board hosts its own access point.
 *    Nothing about a customer's network is compiled in, nothing needs a router
 *    to be reachable, and no two boards need different firmware. The OLED shows
 *    the SSID and password; an installer connects a phone and calibrates from
 *    there. mDNS is gone with it -- on the board's own AP the address is always
 *    192.168.4.1, so there is nothing to look up.
 *
 * 4. Deploy also names the board.
 *    The calibration page carries a nickname field. Until a name is given the
 *    board will not join, so it never appears in the cloud as an anonymous
 *    device and never consumes an enrollment slot: naming it is what
 *    commissions it.
 *
 * 5. Uplinks are rate-limited, and this is the big behavioural change.
 *    The old firmware radioed a packet on every captured frame. That is fine
 *    point-to-point and impossible on LoRaWAN: the EU868 duty cycle and the
 *    network's fair-use budget are both far below a packet per frame. Uplinks
 *    are now sent on CHANGE -- new recognized text, or a change in the camera's
 *    status bits -- with a floor on how often, plus a periodic heartbeat so the
 *    device still reports in when nothing is happening. Calibration is
 *    unaffected: the page reads frames over the local link at full rate.
 *
 * WHAT GOES OUT
 * -------------
 *   port 1   the reading: one status byte, then the recognized text as UTF-8
 *   port 10  the nickname, raw UTF-8 -- fills the device label in the cloud
 *
 * Cayenne LPP cannot carry a string, and a string is this device's whole
 * point, so the reading uses a small custom payload. Nothing in the cloud
 * needed changing for that: the sensor profile carries a JavaScript uplink
 * formatter, the network runs it, and the webhook receives an ordinary
 * decoded payload it already knows how to store. The formatter to paste
 * into the profile is at the bottom of this file.
 *
 * Status and text travel in ONE payload on purpose. Splitting them across
 * two ports was tried and is wrong: each uplink replaces the whole of a
 * device's latest telemetry, so the text was erased every time the status
 * changed, and the status every time the text did.
 *
 * LIBRARIES
 *   MCCI LoRaWAN LMIC library, Adafruit SSD1306, Adafruit GFX
 * ==============================================================================
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <SPI.h>
#include <lmic.h>
#include <hal/hal.h>
#include <Wire.h>
#include <Preferences.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <esp_mac.h>

// ==============================================================================
// 1. LORAWAN IDENTITY
// ==============================================================================

#if __has_include("lorawan_credentials.h")
#include "lorawan_credentials.h"
#else
#error "Missing lorawan_credentials.h. Run: python tools/generate_lorawan_credentials.py"
#endif

static const u1_t PROGMEM APPEUI[8] = { SBMS_JOIN_EUI_LSB_BYTES };
void os_getArtEui(u1_t* buf) { memcpy_P(buf, APPEUI, 8); }

static const u1_t PROGMEM APPKEY[16] = { SBMS_APP_KEY_BYTES };
void os_getDevKey(u1_t* buf) { memcpy_P(buf, APPKEY, 16); }

static u1_t DEVEUI[8];
static const u1_t PROGMEM DEV_EUI_XOR[8] = { SBMS_DEV_EUI_XOR_LSB_BYTES };
void os_getDevEui(u1_t* buf) { memcpy(buf, DEVEUI, 8); }

// ==============================================================================
// 2. PINS
// ==============================================================================

constexpr int SBMS_LORA_SCK  = 5;
constexpr int SBMS_LORA_MISO = 19;
constexpr int SBMS_LORA_MOSI = 27;
constexpr int SBMS_LORA_NSS  = 18;

const lmic_pinmap lmic_pins = {
    .nss = SBMS_LORA_NSS,
    .rxtx = LMIC_UNUSED_PIN,
    .rst = 23,
    .dio = {26, 33, 32},
};

#define SBMS_OLED_SDA 21
#define SBMS_OLED_SCL 22
#define OLED_ADDRESS  0x3C
Adafruit_SSD1306 display(128, 64, &Wire, -1);
static bool oledReady = false;

// UART to the ESP32-CAM. Unchanged from the original wiring.
// GPIO 34 is input-only, which suits an RX line. GPIO 12 is a bootstrap pin
// (MTDI): it must not be pulled high while the board resets, or the ESP32
// selects the wrong flash voltage and will not boot. Idle-low UART TX is fine,
// but do not add a pull-up to it.
#define CAM_RX_PIN 34
#define CAM_TX_PIN 12

// ==============================================================================
// 3. RADIO SCHEDULE
// ==============================================================================

#define LORA_PORT_DATA      1
#define LORA_PORT_NICKNAME 10

// Never transmit more often than this, whatever the camera is doing. The
// camera can change state many times a second; the radio cannot.
#define MIN_UPLINK_GAP_SECONDS 30

// Report in even when nothing changes, so a silent node is distinguishable
// from a dead one.
#define HEARTBEAT_SECONDS 600

// Re-send the nickname occasionally so a missed uplink is not permanent.
#define NICKNAME_REFRESH_UPLINKS 60

static osjob_t sendjob;

// ==============================================================================
// 4. NICKNAME AND PROVISIONING
// ==============================================================================

#define NICKNAME_MAX_LENGTH 48
#define PROVISION_PASSWORD_LENGTH 8

Preferences preferences;
static char nickname[NICKNAME_MAX_LENGTH + 1] = "";
static char apSsid[24] = "";
static char apPassword[PROVISION_PASSWORD_LENGTH + 1] = "";
static char devEuiText[17] = "................";

static bool deploymentMode = false;   // false = calibration (AP + page), true = LoRaWAN
static bool joined = false;

static const char PROVISION_PASSWORD_ALPHABET[] =
    "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";

// ==============================================================================
// 5. CAMERA LINK  (unchanged from sender_lilygo_wifi.ino)
// ==============================================================================

#define CMD_SYNC              0xC0
#define CMD_DEPLOY            7
#define CMD_SET_PANEL_THRESH  9

#define BUF_SIZE      9600

// Must match MAX_RECOGNIZED_CHARS in the camera firmware.
#define TEXT_MAX_LEN  48

// Status bits the camera sets. Bit 4 marks a mask-preview frame, and the
// camera writes the status byte as a bare 0x10 in that case rather than
// OR-ing it in, so bits 0-3 are all clear and the frame carries no reading.
#define CAM_STATUS_LOCKED        0x01
#define CAM_STATUS_SEGMENTED     0x02
#define CAM_STATUS_RECOGNIZED    0x04
#define CAM_STATUS_STREAMING     0x08
#define CAM_STATUS_MASK_PREVIEW  0x10

static uint8_t  frameBuf[BUF_SIZE];
static uint16_t frameW = 0, frameH = 0;
static uint8_t  frameStatus = 0;
static uint8_t  framePanelThresh = 0;
static char     frameText[TEXT_MAX_LEN + 1] = {0};
static uint8_t  frameTextLen = 0;
static volatile bool frameReady = false;
static volatile uint32_t framesReceived = 0;
static portMUX_TYPE frameMux = portMUX_INITIALIZER_UNLOCKED;

// What was last put on the radio, so only changes are transmitted.
static uint8_t lastSentStatus = 0xFF;
static char    lastSentText[TEXT_MAX_LEN + 1] = {0};
static bool    statusUplinkPending = false;
static bool    textUplinkPending = false;
static bool    nicknameUplinkPending = false;
static uint16_t uplinksSinceNickname = 0;
static uint32_t uplinkCount = 0;

enum RxState {
    RX_SYNC1, RX_SYNC2, RX_STATUS, RX_PANEL_THRESH, RX_W_HI, RX_W_LO,
    RX_H_HI, RX_H_LO, RX_DATA, RX_TEXT_LEN, RX_TEXT_DATA
};
static RxState  rxState = RX_SYNC1;
static uint8_t  rxStatus, rxPanelThresh;
static uint16_t rxW, rxH;
static uint32_t rxExpected, rxReceived;
static uint8_t  rxStaging[BUF_SIZE];
static uint8_t  rxTextLen, rxTextReceived;
static uint8_t  rxTextStaging[TEXT_MAX_LEN];

static void commitFrame(uint8_t textLen) {
    portENTER_CRITICAL(&frameMux);
    memcpy(frameBuf, rxStaging, rxExpected);
    frameW = rxW; frameH = rxH; frameStatus = rxStatus;
    framePanelThresh = rxPanelThresh;
    frameTextLen = textLen;
    if (textLen > 0) memcpy(frameText, rxTextStaging, textLen);
    frameText[textLen] = '\0';
    frameReady = true;
    framesReceived++;
    portEXIT_CRITICAL(&frameMux);

    // The calibration page shows the last frame it was given, so a camera that
    // has gone quiet looks identical to one that is working. Logging every
    // frame is what tells the two apart from a serial cable.
    static uint8_t lastLoggedStatus = 0xFF;
    if (frameStatus != lastLoggedStatus) {
        Serial.printf("[CAM] frame %lu  status=0x%02X  locked=%d segmented=%d "
                      "recognized=%d streaming=%d mask=%d  text=\"%s\"\n",
                      (unsigned long)framesReceived, frameStatus,
                      (frameStatus & CAM_STATUS_LOCKED) ? 1 : 0,
                      (frameStatus & CAM_STATUS_SEGMENTED) ? 1 : 0,
                      (frameStatus & CAM_STATUS_RECOGNIZED) ? 1 : 0,
                      (frameStatus & CAM_STATUS_STREAMING) ? 1 : 0,
                      (frameStatus & CAM_STATUS_MASK_PREVIEW) ? 1 : 0,
                      frameText);
        lastLoggedStatus = frameStatus;
    }

    // A mask-preview frame carries no reading at all -- the camera writes the
    // status byte as a bare 0x10 and sends textLen 0 -- so it is never worth an
    // uplink. It only occurs while searching with the mask view on, which is a
    // calibration activity, but the check costs nothing and keeps a stray one
    // from spending airtime.
    if (frameStatus & CAM_STATUS_MASK_PREVIEW) return;

    // Only a change is worth the airtime. Everything else is the camera doing
    // its job at a rate the radio cannot match: it free-runs a frame roughly
    // every 800 ms.
    if (frameStatus != lastSentStatus) statusUplinkPending = true;
    if (strncmp(frameText, lastSentText, TEXT_MAX_LEN) != 0) textUplinkPending = true;
}

void receiveFrame() {
    while (Serial2.available()) {
        uint8_t b = Serial2.read();
        switch (rxState) {
            case RX_SYNC1: rxState = (b == 0xAA) ? RX_SYNC2 : RX_SYNC1; break;
            case RX_SYNC2: rxState = (b == 0x55) ? RX_STATUS : RX_SYNC1; break;
            case RX_STATUS: rxStatus = b; rxState = RX_PANEL_THRESH; break;
            case RX_PANEL_THRESH: rxPanelThresh = b; rxState = RX_W_HI; break;
            case RX_W_HI: rxW = (uint16_t)b << 8; rxState = RX_W_LO; break;
            case RX_W_LO: rxW |= b; rxState = RX_H_HI; break;
            case RX_H_HI: rxH = (uint16_t)b << 8; rxState = RX_H_LO; break;
            case RX_H_LO:
                rxH |= b;
                rxExpected = ((rxW + 7) / 8) * (uint32_t)rxH;
                rxReceived = 0;
                if (rxExpected == 0 || rxExpected > BUF_SIZE) { rxState = RX_SYNC1; break; }
                rxState = RX_DATA;
                break;
            case RX_DATA:
                rxStaging[rxReceived++] = b;
                if (rxReceived >= rxExpected) rxState = RX_TEXT_LEN;
                break;
            case RX_TEXT_LEN:
                rxTextLen = b;
                if (rxTextLen == 0) { commitFrame(0); rxState = RX_SYNC1; }
                else if (rxTextLen > TEXT_MAX_LEN) { rxState = RX_SYNC1; }
                else { rxTextReceived = 0; rxState = RX_TEXT_DATA; }
                break;
            case RX_TEXT_DATA:
                rxTextStaging[rxTextReceived++] = b;
                if (rxTextReceived >= rxTextLen) { commitFrame(rxTextLen); rxState = RX_SYNC1; }
                break;
        }
    }
}

void relayCommandToCam(uint8_t cmd, uint8_t val = 0) {
    if (cmd == 0 || cmd == CMD_DEPLOY || cmd > 11) return;
    Serial2.write(CMD_SYNC);
    Serial2.write(cmd);
    if (cmd == CMD_SET_PANEL_THRESH) Serial2.write(val);
}

// ==============================================================================
// 6. IDENTITY AND STORAGE
// ==============================================================================

bool initUniqueDevEUI() {
    uint8_t mac[6];
    if (esp_read_mac(mac, ESP_MAC_WIFI_STA) != ESP_OK) {
        Serial.println(F("[FATAL] Could not read the hardware MAC."));
        return false;
    }
    DEVEUI[0] = mac[5]; DEVEUI[1] = mac[4]; DEVEUI[2] = mac[3];
    DEVEUI[3] = 0xFE;   DEVEUI[4] = 0xFF;
    DEVEUI[5] = mac[2]; DEVEUI[6] = mac[1]; DEVEUI[7] = mac[0];
    for (int i = 0; i < 8; i++) DEVEUI[i] ^= pgm_read_byte(&DEV_EUI_XOR[i]);

    for (int i = 0; i < 8; i++) snprintf(&devEuiText[i * 2], 3, "%02X", DEVEUI[7 - i]);
    Serial.print(F("[INFO] DevEUI: "));
    Serial.println(devEuiText);
    return true;
}

// Whether this board has already been commissioned. Persisted, because a
// deployed camera that loses power must come back reporting -- not sitting on
// an open access point waiting for someone to visit it again.
static bool wasDeployed = false;

bool loadDeployedFlag() {
    preferences.begin("sbms", true);
    bool value = preferences.getBool("deployed", false);
    preferences.end();
    return value;
}

void saveDeployedFlag(bool value) {
    preferences.begin("sbms", false);
    preferences.putBool("deployed", value);
    preferences.end();
}

void loadNickname() {
    preferences.begin("sbms", true);
    String stored = preferences.getString("nick", "");
    preferences.end();
    stored.trim();
    stored.toCharArray(nickname, sizeof(nickname));
}

bool nicknameIsAcceptable(const String& value) {
    if (!value.length() || value.length() > NICKNAME_MAX_LENGTH) return false;
    for (unsigned int i = 0; i < value.length(); i++) {
        char c = value[i];
        bool ok = isalnum((unsigned char)c) || c == ' ' || c == '_' ||
                  c == '.' || c == '-' || c == '/' || c == '#';
        if (!ok) return false;
    }
    return true;
}

void saveNickname(const String& value) {
    String clean = value;
    clean.trim();
    preferences.begin("sbms", false);
    if (clean == "-") {
        preferences.remove("nick");
        nickname[0] = '\0';
    } else {
        if (clean.length() > NICKNAME_MAX_LENGTH) clean = clean.substring(0, NICKNAME_MAX_LENGTH);
        preferences.putString("nick", clean);
        clean.toCharArray(nickname, sizeof(nickname));
        nicknameUplinkPending = true;
    }
    preferences.end();
    Serial.print(F("[OK] Nickname: "));
    Serial.println(strlen(nickname) ? nickname : "(cleared)");
}

static void buildAccessPointCredentials() {
    snprintf(apSsid, sizeof(apSsid), "SBMS-CAM-%02X%02X", DEVEUI[1], DEVEUI[0]);
    const size_t alphabet = sizeof(PROVISION_PASSWORD_ALPHABET) - 1;
    for (int i = 0; i < PROVISION_PASSWORD_LENGTH; i++) {
        apPassword[i] = PROVISION_PASSWORD_ALPHABET[esp_random() % alphabet];
    }
    apPassword[PROVISION_PASSWORD_LENGTH] = 0;
}

// ==============================================================================
// 7. DISPLAY
// ==============================================================================

void showCalibrationScreen() {
    if (!oledReady) return;
    display.clearDisplay();
    display.setTextSize(1);
    display.setTextColor(SSD1306_WHITE);
    display.setCursor(0, 0);
    display.println(F("CAM CALIBRATION"));
    display.drawFastHLine(0, 10, 128, SSD1306_WHITE);
    display.setCursor(0, 14);
    display.print(F("WiFi "));
    display.println(apSsid);
    display.setCursor(0, 24);
    display.print(F("Pass "));
    display.println(apPassword);
    display.setCursor(0, 34);
    display.println(F("Open 192.168.4.1"));
    display.drawFastHLine(0, 45, 128, SSD1306_WHITE);
    display.setCursor(0, 49);
    display.print(F("EUI "));
    display.println(devEuiText);
    display.display();
}

void showDeployedScreen() {
    if (!oledReady) return;
    display.clearDisplay();
    display.setTextSize(1);
    display.setTextColor(SSD1306_WHITE);
    display.setCursor(0, 0);
    display.println(strlen(nickname) ? nickname : "DEPLOYED");
    display.drawFastHLine(0, 10, 128, SSD1306_WHITE);

    display.setCursor(0, 14);
    display.print(F("LoRaWAN "));
    display.println(joined ? "JOINED" : "joining...");

    display.setCursor(0, 24);
    display.print(F("TX "));
    display.print(uplinkCount);
    display.print(F("  st 0x"));
    display.println(frameStatus, HEX);

    display.setCursor(0, 36);
    display.println(F("Reading:"));
    display.setCursor(0, 46);
    display.println(frameTextLen ? frameText : "--");
    display.display();
}

// ==============================================================================
// 8. CALIBRATION WEB PAGE
// ==============================================================================

WebServer server(80);

void handleCmd() {
    if (!server.hasArg("action")) { server.send(400, "text/plain", "missing action"); return; }
    String a = server.arg("action");
    uint8_t cmd = 0, val = 0;
    if      (a == "lock")          cmd = 1;
    else if (a == "unlock")        cmd = 2;
    else if (a == "segment_on")    cmd = 3;
    else if (a == "segment_off")   cmd = 4;
    else if (a == "recognize_on")  cmd = 5;
    else if (a == "recognize_off") cmd = 6;
    else if (a == "deploy")        cmd = 7;
    else if (a == "stream")        cmd = 8;
    else if (a == "set_panel_thresh") {
        if (!server.hasArg("value")) { server.send(400, "text/plain", "missing value"); return; }
        int v = server.arg("value").toInt();
        if (v < 0 || v > 255) { server.send(400, "text/plain", "value out of range"); return; }
        val = (uint8_t)v;
        cmd = 9;
    } else { server.send(400, "text/plain", "unknown action"); return; }

    if (cmd == CMD_DEPLOY) {
        // Deploying without a name would put an anonymous device on the
        // network, which is exactly what the naming step exists to prevent.
        String requested = server.hasArg("nickname") ? server.arg("nickname") : String("");
        requested.trim();
        if (!nicknameIsAcceptable(requested)) {
            server.send(400, "text/plain",
                        "A name is required before deploying. Letters, numbers, "
                        "space _ . - / # only, up to 48 characters.");
            return;
        }
        saveNickname(requested);
        server.send(200, "text/plain", "ok");
        delay(1200);              // let the phone receive the reply first
        enterDeploymentMode();
        return;
    }

    relayCommandToCam(cmd, val);
    server.send(200, "text/plain", "ok");
}

void handleStatus() {
    uint8_t st, panelThresh;
    bool ready;
    char textCopy[TEXT_MAX_LEN + 1];

    portENTER_CRITICAL(&frameMux);
    st = frameStatus;
    panelThresh = framePanelThresh;
    ready = frameReady;
    strncpy(textCopy, frameText, TEXT_MAX_LEN);
    textCopy[TEXT_MAX_LEN] = '\0';
    portEXIT_CRITICAL(&frameMux);

    String textJson;
    for (const char* p = textCopy; *p; p++) {
        if (*p == '\n') textJson += "\\n";
        else if (*p == '"') textJson += "\\\"";
        else textJson += *p;
    }

    String json = String("{\"frameReady\":") + (ready ? "true" : "false") +
                  ",\"locked\":" + ((st & 0x01) ? "true" : "false") +
                  ",\"segmented\":" + ((st & 0x02) ? "true" : "false") +
                  ",\"recognized\":" + ((st & 0x04) ? "true" : "false") +
                  ",\"streaming\":" + ((st & 0x08) ? "true" : "false") +
                  ",\"panelThresh\":" + String((int)panelThresh) +
                  ",\"text\":\"" + textJson + "\"" +
                  ",\"nickname\":\"" + String(nickname) + "\"" +
                  ",\"eui\":\"" + String(devEuiText) + "\"" +
                  ",\"frames\":" + String((unsigned long)framesReceived) + "}";
    server.sendHeader("Cache-Control", "no-cache");
    server.send(200, "application/json", json);
}

void handleFrameHex() {
    if (!frameReady) { server.send(503, "text/plain", ""); return; }
    static uint8_t localBuf[BUF_SIZE];
    uint16_t w, h;
    uint32_t ps;

    portENTER_CRITICAL(&frameMux);
    w = frameW; h = frameH;
    ps = ((w + 7) / 8) * (uint32_t)h;
    // The parser bounds what it writes into frameBuf, but this recomputes the
    // size from the stored dimensions, so it has to bound what it reads too.
    // Unlock is precisely where that matters: the camera switches from a fixed
    // full frame to a variable crop, and a torn read of frameW/frameH here
    // would memcpy past the end of a 9600-byte buffer.
    if (ps > BUF_SIZE) ps = 0;
    if (ps > 0) memcpy(localBuf, frameBuf, ps);
    portEXIT_CRITICAL(&frameMux);

    if (ps == 0) {
        server.send(503, "text/plain", "");
        return;
    }

    String resp = String(w) + "," + String(h) + ",";
    resp.reserve(resp.length() + ps * 2);
    char tmp[3];
    for (uint32_t i = 0; i < ps; i++) { sprintf(tmp, "%02x", localBuf[i]); resp += tmp; }
    server.sendHeader("Cache-Control", "no-cache");
    server.send(200, "text/plain", resp);
}

const char VIEWER_HTML[] PROGMEM = R"html(
<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ESP32-CAM Calibration</title>
<style>
body{background:#111;display:flex;flex-direction:column;align-items:center;
     min-height:100vh;margin:0;padding:16px 8px;font-family:monospace;color:#eee;}
h2{margin:0 0 2px;letter-spacing:2px;font-size:17px;}
#euiLabel{font-size:11px;color:#666;margin-bottom:10px;}
canvas{image-rendering:pixelated;border:2px solid #444;max-width:100%;}
#status{margin-top:10px;font-size:12px;color:#888;}
#mode{margin-top:6px;font-size:13px;color:#6cf;}
#recognizedText{margin-top:12px;font-size:26px;letter-spacing:3px;color:#9f6;
                min-height:32px;white-space:pre-line;line-height:1.3;text-align:center;}
#controls{margin-top:16px;display:flex;gap:8px;flex-wrap:wrap;justify-content:center;max-width:380px;}
button{background:#222;color:#eee;border:1px solid #555;border-radius:4px;
       padding:10px 14px;font-family:monospace;font-size:13px;}
button:disabled{opacity:.35;}
button.active{border-color:#6cf;color:#6cf;}
#btnDeploy{border-color:#f93;color:#f93;}
#nameRow{margin-top:18px;width:100%;max-width:380px;}
#nameRow label{display:block;font-size:12px;color:#aaa;margin-bottom:5px;}
#nickname{width:100%;box-sizing:border-box;padding:12px;font-size:16px;border-radius:6px;
          border:1px solid #334155;background:#1e293b;color:#f1f5f9;font-family:monospace;}
.err{color:#f87171;font-size:12px;margin-top:6px;min-height:16px;}
</style></head><body>
<h2>ESP32-CAM Calibration</h2>
<div id="euiLabel"></div>
<canvas id="bw-canvas"></canvas>
<div id="recognizedText">Reading: —</div>
<div id="status">Waiting for first frame...</div>
<div id="mode">Mode: —</div>
<div id="threshRow" style="margin-top:14px;display:flex;align-items:center;gap:8px;">
  <label for="panelThresh" style="font-size:13px;color:#aaa;">Panel Thresh</label>
  <input type="range" id="panelThresh" min="0" max="255" value="185">
  <span id="panelThreshVal" style="font-size:13px;color:#6cf;width:32px;">185</span>
</div>
<div id="controls">
  <button id="btnStream">Stream (Raw)</button>
  <button id="btnLock">Lock Coordinates</button>
  <button id="btnUnlock">Unlock / Resume</button>
  <button id="btnSegOn">Segment On</button>
  <button id="btnSegOff">Segment Off</button>
  <button id="btnRecOn">Recognize On</button>
  <button id="btnRecOff">Recognize Off</button>
</div>
<div id="nameRow">
  <label for="nickname">Name this device (required before deploying)</label>
  <input id="nickname" maxlength="48" autocomplete="off" autocapitalize="none"
         placeholder="cam_lobby_north">
  <div class="err" id="nameErr"></div>
  <button id="btnDeploy" style="width:100%;padding:14px;">Deploy (LoRaWAN)</button>
</div>
<script>
const $=id=>document.getElementById(id);
const canvas=$('bw-canvas'),ctx=canvas.getContext('2d');
let threshDragging=false;
function sendCmd(action,value){let u='/cmd?action='+action;
  if(value!==undefined)u+='&value='+encodeURIComponent(value);return fetch(u);}
$('panelThresh').oninput=()=>{$('panelThreshVal').textContent=$('panelThresh').value;};
$('panelThresh').onmousedown=$('panelThresh').ontouchstart=()=>{threshDragging=true;};
$('panelThresh').onchange=()=>{sendCmd('set_panel_thresh',$('panelThresh').value);threshDragging=false;};
$('btnStream').onclick=()=>sendCmd('stream');
$('btnLock').onclick=()=>sendCmd('lock');
$('btnUnlock').onclick=()=>sendCmd('unlock');
$('btnSegOn').onclick=()=>sendCmd('segment_on');
$('btnSegOff').onclick=()=>sendCmd('segment_off');
$('btnRecOn').onclick=()=>sendCmd('recognize_on');
$('btnRecOff').onclick=()=>sendCmd('recognize_off');
$('btnDeploy').onclick=()=>{
  const n=$('nickname').value.trim();
  if(!/^[A-Za-z0-9 _.\-\/#]+$/.test(n)){
    $('nameErr').textContent='Name required: letters, numbers, space _ . - / # only';return;}
  if(!confirm('Deploy names this device "'+n+'", turns Wi-Fi off and joins LoRaWAN. '+
              'This page stops responding. Power-cycle to calibrate again. Continue?'))return;
  $('btnDeploy').disabled=true;$('btnDeploy').textContent='Deploying...';
  fetch('/cmd?action=deploy&nickname='+encodeURIComponent(n))
    .then(r=>r.ok?(document.body.innerHTML='<h2 style="color:#4ade80;margin-top:40px">Deployed</h2>'+
      '<p style="color:#94a3b8">Named <b>'+n+'</b>. Wi-Fi is shutting down and the device '+
      'is joining LoRaWAN. You can disconnect.</p>'):r.text().then(t=>{
        $('nameErr').textContent=t;$('btnDeploy').disabled=false;
        $('btnDeploy').textContent='Deploy (LoRaWAN)';}))
    .catch(()=>{});
};
function refreshFrame(){
  fetch('/frame.hex?t='+Date.now()).then(r=>{if(!r.ok)throw 0;return r.text();}).then(hex=>{
    const p=hex.split(','),W=+p[0],H=+p[1],d=p[2],RB=Math.ceil(W/8);
    canvas.width=W;canvas.height=H;
    canvas.style.width=Math.min(W*8,360)+'px';canvas.style.height=Math.min(H*8,480)+'px';
    const img=ctx.createImageData(W,H);
    for(let y=0;y<H;y++)for(let x=0;x<W;x++){
      const bv=parseInt(d.substr((y*RB+(x>>3))*2,2),16);
      const v=((bv>>(7-(x%8)))&1)?255:0,px=(y*W+x)*4;
      img.data[px]=img.data[px+1]=img.data[px+2]=v;img.data[px+3]=255;}
    ctx.putImageData(img,0,0);
    $('status').textContent='Updated: '+new Date().toLocaleTimeString();
  }).catch(()=>{$('status').textContent='Waiting for frame...';});
}
function refreshStatus(){
  fetch('/status?t='+Date.now()).then(r=>r.json()).then(s=>{
    $('euiLabel').textContent='DevEUI '+(s.eui||'');
    if(!threshDragging&&typeof s.panelThresh==='number'){
      $('panelThresh').value=s.panelThresh;$('panelThreshVal').textContent=s.panelThresh;}
    if(s.nickname&&!$('nickname').value)$('nickname').value=s.nickname;
    const extra=[];if(s.segmented)extra.push('SEGMENTED');if(s.recognized)extra.push('RECOGNIZED');
    const label=s.streaming?'STREAMING (raw preview)':(s.locked?'LOCKED':'SEARCHING');
    $('mode').textContent='Mode: '+label+(s.locked?(extra.length?' + '+extra.join(' + '):' (plain)'):'');
    const searching=!s.locked&&!s.streaming;
    $('btnStream').disabled=s.streaming;
    $('btnLock').disabled=s.locked||s.streaming;
    $('btnUnlock').disabled=searching;
    $('btnSegOn').disabled=!s.locked||s.segmented;
    $('btnSegOff').disabled=!s.locked||!s.segmented;
    $('btnRecOn').disabled=!s.locked||s.recognized;
    $('btnRecOff').disabled=!s.locked||!s.recognized;
    $('btnDeploy').disabled=!s.locked||!s.recognized;
    $('btnStream').classList.toggle('active',s.streaming);
    $('btnLock').classList.toggle('active',s.locked);
    $('btnSegOn').classList.toggle('active',s.segmented);
    $('btnRecOn').classList.toggle('active',s.recognized);
    $('recognizedText').textContent=(s.recognized&&s.text)?('Reading:\n'+s.text):'Reading: —';
  }).catch(()=>{$('mode').textContent='Mode: unknown';});
}
refreshFrame();refreshStatus();
setInterval(refreshFrame,6000);setInterval(refreshStatus,500);
</script></body></html>
)html";

void handleRoot() { server.send_P(200, "text/html", VIEWER_HTML); }

// ==============================================================================
// 9. LORAWAN
// ==============================================================================

void do_send(osjob_t* j) {
    if (LMIC.opmode & OP_TXRXPEND) return;

    // One uplink at a time, most-identifying first: a device the cloud cannot
    // name is less useful than one whose reading is a few seconds stale.
    if (nicknameUplinkPending && strlen(nickname)) {
        nicknameUplinkPending = false;
        LMIC_setTxData2(LORA_PORT_NICKNAME, (uint8_t*)nickname, strlen(nickname), 0);
        Serial.print(F("[TX] nickname: "));
        Serial.println(nickname);
        return;
    }

    // One self-contained reading: the status byte followed by whatever the
    // camera currently reads. An empty text is meaningful -- it says the
    // camera reads nothing right now -- so it goes out as a bare status
    // byte rather than being skipped.
    statusUplinkPending = false;
    textUplinkPending = false;
    lastSentStatus = frameStatus;
    strncpy(lastSentText, frameText, TEXT_MAX_LEN);
    lastSentText[TEXT_MAX_LEN] = '\0';

    uint8_t payload[1 + TEXT_MAX_LEN];
    payload[0] = frameStatus;
    uint8_t length = 1;
    if (frameTextLen > 0) {
        memcpy(&payload[1], frameText, frameTextLen);
        length += frameTextLen;
    }
    LMIC_setTxData2(LORA_PORT_DATA, payload, length, 0);

    Serial.print(F("[TX] status 0x"));
    Serial.print(frameStatus, HEX);
    Serial.print(F("  text: "));
    Serial.println(frameTextLen ? frameText : "(none)");
}

void scheduleNextUplink() {
    os_setTimedCallback(&sendjob,
                        os_getTime() + sec2osticks(MIN_UPLINK_GAP_SECONDS),
                        do_send);
}

void onEvent(ev_t ev) {
    switch (ev) {
        case EV_JOINING:
            Serial.println(F("EV_JOINING"));
            break;
        case EV_JOINED:
            Serial.println(F("EV_JOINED"));
            joined = true;
            LMIC_setLinkCheckMode(0);
            nicknameUplinkPending = strlen(nickname) > 0;
            statusUplinkPending = true;
            break;
        case EV_JOIN_TXCOMPLETE:
            Serial.println(F("EV_JOIN_TXCOMPLETE (no JoinAccept - is this DevEUI registered?)"));
            break;
        case EV_JOIN_FAILED:
            Serial.println(F("EV_JOIN_FAILED"));
            break;
        case EV_TXSTART:
            Serial.println(F("EV_TXSTART"));
            break;
        case EV_TXCANCELED:
            Serial.println(F("EV_TXCANCELED (duty cycle exhausted)"));
            break;
        case EV_TXCOMPLETE:
            Serial.println(F("EV_TXCOMPLETE"));
            uplinkCount++;
            if (++uplinksSinceNickname >= NICKNAME_REFRESH_UPLINKS) {
                uplinksSinceNickname = 0;
                nicknameUplinkPending = strlen(nickname) > 0;
            }
            scheduleNextUplink();
            break;
        default:
            break;
    }
}

void enterDeploymentMode() {
    Serial.println(F("[DEPLOY] Wi-Fi off, starting LoRaWAN."));
    deploymentMode = true;
    saveDeployedFlag(true);
    server.stop();
    WiFi.softAPdisconnect(true);
    WiFi.mode(WIFI_OFF);

    showDeployedScreen();

    SPI.begin(SBMS_LORA_SCK, SBMS_LORA_MISO, SBMS_LORA_MOSI, SBMS_LORA_NSS);
    if (!os_init_ex((const void*)&lmic_pins)) {
        Serial.println(F("[FATAL] SX1276 init failed."));
        if (oledReady) {
            display.clearDisplay();
            display.setCursor(0, 0);
            display.println(F("LoRa INIT FAILED"));
            display.display();
        }
        while (true) delay(1000);
    }
    LMIC_reset();
    LMIC_setClockError(MAX_CLOCK_ERROR * 10 / 100);
    do_send(&sendjob);
}

// ==============================================================================
// 10. SETUP / LOOP
// ==============================================================================

void setup() {
    // Sequence deliberately mirrors sender_lilygo_wifi.ino, which is known
    // good on this hardware. My earlier ordering crashed inside the WiFi PHY
    // before the access point ever came up, so the differences are gone:
    // btStop() and the GPIO 0 rename button are no longer touched at all,
    // and the radio bus is brought up before WiFi exactly as it was.
    Serial.begin(115200);

    // A full frame is ~9.6 kB, about 830 ms of solid data at this baud. The
    // default 256-byte receive buffer would need draining every ~22 ms, and
    // serving the page's hex frame takes far longer than that.
    Serial2.setRxBufferSize(2048);
    Serial2.begin(115200, SERIAL_8N1, CAM_RX_PIN, CAM_TX_PIN);
    delay(200);
    Serial.println(F("\n=== ESP32-CAM LoRaWAN node ==="));

    Wire.begin(SBMS_OLED_SDA, SBMS_OLED_SCL);
    oledReady = display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDRESS, true, false);
    if (!oledReady) Serial.println(F("[WARN] OLED not found."));

    // The working sketch calls initLoRa() here, before WiFi, which leaves the
    // SX1276 bus initialised and the radio deselected. LMIC is not started
    // until Deploy, but the bus is brought up in the same place.
    SPI.begin(SBMS_LORA_SCK, SBMS_LORA_MISO, SBMS_LORA_MOSI, SBMS_LORA_NSS);
    pinMode(SBMS_LORA_NSS, OUTPUT);
    digitalWrite(SBMS_LORA_NSS, HIGH);

    if (!initUniqueDevEUI()) { while (true) delay(1000); }

    randomSeed(esp_random());
    loadNickname();
    wasDeployed = loadDeployedFlag() && strlen(nickname);

    // A board that was already commissioned goes straight back to reporting.
    // Holding BOOT for two seconds now is the way back to calibration, and the
    // only way -- otherwise a deployed camera on a wall could never be
    // re-aimed without a cable.
    if (wasDeployed) {
        Serial.print(F("[SETUP] Previously deployed as '"));
        Serial.print(nickname);
        Serial.println(F("'. Hold BOOT 2s to re-calibrate."));
        if (oledReady) {
            display.clearDisplay();
            display.setCursor(0, 0);
            display.println(nickname);
            display.drawFastHLine(0, 10, 128, SSD1306_WHITE);
            display.setCursor(0, 20);
            display.println(F("Deployed."));
            display.setCursor(0, 36);
            display.println(F("Hold BOOT 2s to"));
            display.println(F("re-calibrate."));
            display.display();
        }
        pinMode(0, INPUT_PULLUP);
        unsigned long start = millis(), held = 0;
        while (millis() - start < 3000) {
            if (digitalRead(0) == LOW) {
                if (!held) held = millis();
                if (millis() - held >= 2000) {
                    Serial.println(F("[SETUP] BOOT held: returning to calibration."));
                    saveDeployedFlag(false);
                    wasDeployed = false;
                    break;
                }
            } else held = 0;
            delay(20);
        }
    }

    if (wasDeployed) {
        enterDeploymentMode();
        return;
    }

    buildAccessPointCredentials();

    WiFi.mode(WIFI_AP);
    WiFi.softAP(apSsid, apPassword);
    delay(300);
    Serial.printf("[SETUP] AP %s / %s  ->  http://192.168.4.1\n", apSsid, apPassword);

    server.on("/", handleRoot);
    server.on("/frame.hex", handleFrameHex);
    server.on("/cmd", handleCmd);
    server.on("/status", handleStatus);
    server.onNotFound(handleRoot);
    server.begin();
    Serial.println(F("[SETUP] Calibration page ready."));

    showCalibrationScreen();
}

void loop() {
    receiveFrame();

    if (!deploymentMode) {
        server.handleClient();
        return;
    }

    os_runloop_once();

    // A change noticed between scheduled uplinks still has to wait for the
    // gap to elapse; EV_TXCOMPLETE is what re-arms the timer.
    static unsigned long lastScreen = 0;
    if (millis() - lastScreen > 1000) {
        lastScreen = millis();
        if (!(LMIC.opmode & OP_TXRXPEND)) showDeployedScreen();
    }
}

/*
 * ==============================================================================
 * TTN UPLINK FORMATTER FOR THIS PAYLOAD
 * ==============================================================================
 * Paste this into the sensor profile's TTN formatter field (type: JavaScript)
 * and assign that profile to the camera nodes. The network runs it, so the
 * cloud receives named fields and needs no knowledge of this payload at all.
 *
 * It lives here, next to the code that produces the bytes, so the two cannot
 * drift apart unnoticed.
 *
 * function decodeUplink(input) {
 *   var b = input.bytes;
 *   if (!b || b.length < 1) {
 *     return { errors: ["empty camera payload"] };
 *   }
 *
 *   var status = b[0];
 *
 *   // The camera emits digits, '.', '?' for a glyph it could not read,
 *   // and '
' between the rows of a multi-row panel. Nothing else.
 *   var text = "";
 *   for (var i = 1; i < b.length; i++) {
 *     text += String.fromCharCode(b[i]);
 *   }
 *
 *   var out = {
 *     cam_locked:       (status & 0x01) !== 0,
 *     cam_segmented:    (status & 0x02) !== 0,
 *     cam_recognized:   (status & 0x04) !== 0,
 *     cam_streaming:    (status & 0x08) !== 0,
 *     // Bit 4 is a mask-preview frame. The camera writes the status byte
 *     // as a bare 0x10 in that case, so bits 0-3 are all clear and the
 *     // frame carries no reading at all.
 *     cam_mask_preview: (status & 0x10) !== 0,
 *     cam_reading_text: text
 *   };
 *
 *   // A multi-row panel arrives as two independent readings joined by a
 *   // newline, so there is no single number to report for one.
 *   var rows = text.split("\\n").filter(function (r) { return r.length; });
 *   out.cam_reading_rows = rows.length;
 *
 *   // Strict on purpose. The camera has no confidence threshold and marks
 *   // a glyph it could not read with '?', and parseFloat stops at the
 *   // first bad character rather than failing:
 *   //
 *   //     parseFloat('233?0.44')  ->  233
 *   //
 *   // One unreadable digit would otherwise become a number wrong by orders
 *   // of magnitude that looks entirely plausible on a graph. A gap in the
 *   // series is obvious; a wrong value is not. The raw text goes out
 *   // either way, so nothing is lost by refusing to guess.
 *   var numeric = rows.length === 1 && /^[0-9]+(\.[0-9]+)?$/.test(rows[0]);
 *   out.cam_reading_ok = numeric;
 *   if (numeric) {
 *     out.cam_reading = parseFloat(rows[0]);
 *   }
 *
 *   // NOTE: cam_reading_ok means the string was well formed, NOT that the
 *   // reading is correct. The classifier takes an unconditional argmax
 *   // with no confidence threshold, so a misread digit arrives as a
 *   // perfectly ordinary one with nothing to mark it.
 *
 *   return { data: out };
 * }
 *
 * The nickname on port 10 is deliberately NOT decoded here: the cloud reads
 * that one straight from the raw frame, the same way it does for every other
 * device, so a formatter that mangled it would break naming.
 * ==============================================================================
 */
