/*
 * ==============================================================================
 * Smart Building Management System - LoRaWAN Cayenne LPP Sensor Node
 * Hardware: LILYGO TTGO LoRa32-OLED V2.1 (ESP32 + SX1276)
 * ==============================================================================
 *
 * FEATURES:
 *  1. 100% Zero-Touch: LoRaWAN only (Wi-Fi is completely disabled for long battery life).
 *  2. Auto-Generated DevEUI: Derived from ESP32 factory MAC address. Every board
 *     automatically gets a globally unique DevEUI!
 *  3. Cayenne LPP Encoding: Uses universal Cayenne Low Power Payload. TTN and the
 *     Smart Building Cloud decode all sensor values automatically with zero decoder code!
 *  4. Cloud Auto-Discovery: The moment this device transmits, it appears in the
 *     "Discovered Devices" inbox on the Cloud Map Editor.
 *
 * REQUIRED ARDUINO LIBRARIES (Install via Arduino Library Manager):
 *  - "MCCI LoRaWAN LMIC library" by IBM, Matthijs Kooijman, Terry Moore
 *  - "CayenneLPP" by ElectronicCats (v1.1+, for the extended LPP types)
 *  - "Adafruit SSD1306" and "Adafruit GFX Library" (onboard OLED)
 *  - "Adafruit SHT31 Library" (Optional, for SHT30/SHT31 Temp & Humidity)
 * ==============================================================================
 */

#include <Arduino.h>
#include <WiFi.h>
#include <lmic.h>
#include <hal/hal.h>
#include <SPI.h>
#include <Wire.h>
#include <CayenneLPP.h>
#include <esp_mac.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Preferences.h>
#include <WebServer.h>

// Optional: Adafruit SHT31 (I2C SDA=21, SCL=22)
// #include <Adafruit_SHT31.h>
// Adafruit_SHT31 sht31 = Adafruit_SHT31();

// ==============================================================================
// 1. LORAWAN OTAA CREDENTIALS
// ==============================================================================

#if __has_include("lorawan_credentials.h")
#include "lorawan_credentials.h"
#else
#error "Missing firmware/lorawan_credentials.h. Run: python tools/generate_lorawan_credentials.py"
#endif

// AppEUI / JoinEUI (8 bytes, Little-Endian format for TTN)
static const u1_t PROGMEM APPEUI[8] = { SBMS_JOIN_EUI_LSB_BYTES };
void os_getArtEui (u1_t* buf) { memcpy_P(buf, APPEUI, 8); }

// AppKey (16 bytes, Big-Endian format from TTN Application)
static const u1_t PROGMEM APPKEY[16] = { SBMS_APP_KEY_BYTES };
void os_getDevKey (u1_t* buf) { memcpy_P(buf, APPKEY, 16); }

// DevEUI is generated dynamically from the ESP32 chip's unique MAC address!
// (Little-Endian format required by LMIC)
static u1_t DEVEUI[8];
static const u1_t PROGMEM DEV_EUI_XOR[8] = { SBMS_DEV_EUI_XOR_LSB_BYTES };
void os_getDevEui (u1_t* buf) { memcpy(buf, DEVEUI, 8); }

// ==============================================================================
// 2. PIN MAPPING FOR LILYGO ESP32 LORA BOARDS
// ==============================================================================
// Verified pinout for LILYGO TTGO LoRa32-OLED V2.1 (board revision 1.6.1).
constexpr int SBMS_LORA_SCK = 5;
constexpr int SBMS_LORA_MISO = 19;
constexpr int SBMS_LORA_MOSI = 27;
constexpr int SBMS_LORA_NSS = 18;

const lmic_pinmap lmic_pins = {
    .nss = SBMS_LORA_NSS,
    .rxtx = LMIC_UNUSED_PIN,
    .rst = 23, // Use 14 on some T-Beam revisions
    .dio = {26, 33, 32},
};

// ==============================================================================
// 3. HARDWARE CONFIGURATION & INTERVALS
// ==============================================================================
// Bench testing: 30 s so changes show up quickly. The 25-byte payload is
// ~62 ms of airtime at SF7, so 30 s is 0.21% duty cycle -- well inside the
// EU868 1% limit. Raise to 300+ for a deployed battery node, and note that
// The Things Network's public cloud fair-use budget (30 s airtime/day)
// would not tolerate this rate; a local stack has no such limit.
const unsigned TX_INTERVAL_SECONDS = 30;
static osjob_t sendjob;
CayenneLPP lpp(51); // 51 bytes buffer

// ------------------------------------------------------------------------------
// BENCH TEST MODE
// ------------------------------------------------------------------------------
// Set DUMMY_SENSORS to 1 to transmit simulated readings for every channel the
// CAYENNE_LPP_V1 cloud profile understands, with no sensors wired up at all.
// This exercises the full path -- LoRaWAN join, gateway, TTN decoder, webhook,
// auto-discovery, floor map tiles and alarm thresholds -- on a bare board.
// Set it back to 0 once real sensors are attached.
#define DUMMY_SENSORS 1

// Cayenne LPP defines more data types than most decoders implement. The
// Things Stack's built-in decoder handles only the standard set, and it
// rejects the WHOLE payload -- logging "cayennelpp: invalid output" and
// producing no decoded_payload at all -- if it meets even one type it does not
// know. It is not a partial decode.
//
// Setting this to 1 adds voltage, current, power and concentration. Leave it
// at 0 for The Things Stack's built-in formatter; turn it on only with a
// decoder you have confirmed supports them (a custom JavaScript formatter, or
// ChirpStack).
#define DUMMY_EXTENDED_TYPES 0

// ------------------------------------------------------------------------------
// DEVICE NICKNAME
// ------------------------------------------------------------------------------
// Fifty identical boards are indistinguishable in the cloud's discovery inbox:
// each is identified by a DevEUI derived from its chip MAC, which says nothing
// about where it was mounted. A nickname fixes that for humans. It changes
// nothing about identity -- the device is still addressed by its DevEUI.
//
// Set it over USB serial at 115200 baud, at any time, by typing:
//
//     nick temp_c_floor3_308
//
// It is stored in flash and survives reboots and reflashes. "nick" on its own
// prints the current one; "nick -" clears it.
//
// Cayenne LPP has no string type, so the nickname cannot share the sensor
// payload; it is sent as its own uplink on LORA_PORT_NICKNAME, which the cloud
// reads straight from the raw frame.
#define LORA_PORT_DATA      1
#define LORA_PORT_NICKNAME 10
#define NICKNAME_MAX_LENGTH 48

// Re-send the nickname periodically so a single missed uplink is not permanent.
// The cloud ignores an unchanged one, so this costs nothing but airtime.
#define NICKNAME_REFRESH_UPLINKS 60

// Length of the generated access-point password shown on the OLED. WPA2
// requires at least 8 characters.
#define PROVISION_PASSWORD_LENGTH 8

// The BOOT button, used to forget the nickname and reopen the portal so a
// board can be renamed in the field without a cable.
//
// It is checked for a hold shortly AFTER boot, never through a reset: GPIO0 is
// the ESP32's bootstrap pin, and holding it low through reset puts the chip
// into flash-download mode instead of running this sketch at all.
#define PIN_RESET_NICKNAME 0
#define RENAME_HOLD_MS     2000
#define RENAME_WINDOW_MS   4000

Preferences preferences;
static char nickname[NICKNAME_MAX_LENGTH + 1] = "";
static bool nicknameUplinkPending = false;
static uint16_t uplinksSinceNickname = 0;

#define PIN_PIR_MOTION  13  // Motion or door contact digital input
#define PIN_BATTERY_ADC 35  // Battery ADC voltage divider pin

// ------------------------------------------------------------------------------
// ONBOARD OLED (SSD1306 128x64 over I2C)
// ------------------------------------------------------------------------------
// Set to 0 to compile the display out entirely.
#define ENABLE_OLED 1

#define OLED_WIDTH   128
#define OLED_HEIGHT   64
#define OLED_ADDRESS 0x3C  // Standard address for the TTGO onboard SSD1306
// Prefixed, because the board variant defines OLED_SDA/OLED_SCL itself and
// redefining those produces a warning and depends on include order. The V2.1
// variant happens to agree with these values; the V1 variant uses 4 and 15.
#define SBMS_OLED_SDA  21
#define SBMS_OLED_SCL  22
#define OLED_RESET     -1  // No dedicated reset line on this board

#if ENABLE_OLED
Adafruit_SSD1306 display(OLED_WIDTH, OLED_HEIGHT, &Wire, OLED_RESET);
static bool oledReady = false;

// Snapshot of what the screen should show. Written from the LMIC callbacks and
// from do_send(), which must stay fast -- rendering happens later, in loop().
static char     devEuiText[17] = "................";
static const char *joinState   = "BOOTING";
static uint32_t uplinkCount    = 0;
static uint16_t lastPayloadLen = 0;
static float    lastTemp       = 0.0f;
static float    lastHum        = 0.0f;
static float    lastVbat       = 0.0f;
static bool     displayDirty   = true;
#endif

bool initUniqueDevEUI() {
    uint8_t mac[6];
    if (esp_read_mac(mac, ESP_MAC_WIFI_STA) != ESP_OK) {
        Serial.println(F("[FATAL] Could not read the ESP32 hardware MAC; DevEUI was not generated."));
        return false;
    }

    // Form an IEEE EUI-64: [MAC0, MAC1, MAC2, 0xFF, 0xFE, MAC3, MAC4, MAC5]
    // Stored Little-Endian for LMIC:
    DEVEUI[0] = mac[5];
    DEVEUI[1] = mac[4];
    DEVEUI[2] = mac[3];
    DEVEUI[3] = 0xFE;
    DEVEUI[4] = 0xFF;
    DEVEUI[5] = mac[2];
    DEVEUI[6] = mac[1];
    DEVEUI[7] = mac[0];

    // Namespace the hardware EUI for this TTN application. This prevents a
    // board previously registered in another TTN app from claiming the same
    // globally unique DevEUI while keeping derivation fully deterministic.
    for (int i = 0; i < 8; i++) {
        DEVEUI[i] ^= pgm_read_byte(&DEV_EUI_XOR[i]);
    }

    Serial.print(F("[INFO] Unique Hardware DevEUI (MSB): "));
    for (int i = 7; i >= 0; i--) {
        if (DEVEUI[i] < 0x10) Serial.print("0");
        Serial.print(DEVEUI[i], HEX);
        if (i > 0) Serial.print(":");
    }
    Serial.println();

#if ENABLE_OLED
    // Same MSB order as the serial line, without separators so all 16 hex
    // characters fit one 21-character OLED row. This is the value you register
    // in The Things Stack, so showing it on screen saves needing a serial cable.
    for (int i = 0; i < 8; i++) {
        snprintf(&devEuiText[i * 2], 3, "%02X", DEVEUI[7 - i]);
    }
    displayDirty = true;
#endif
    return true;
}

float readBatteryVoltage() {
    // 1:1 voltage divider (100k / 100k) on ESP32 ADC
    int raw = analogRead(PIN_BATTERY_ADC);
    float voltage = ((float)raw / 4095.0) * 3.3 * 2.0;
    if (voltage < 2.0 || voltage > 4.5) {
        voltage = 3.85; // Simulated fallback if pin is floating
    }
    return voltage;
}

void loadNickname() {
    preferences.begin("sbms", /*readOnly=*/true);
    String stored = preferences.getString("nick", "");
    preferences.end();
    stored.trim();
    stored.toCharArray(nickname, sizeof(nickname));
    if (strlen(nickname)) {
        Serial.print(F("[INFO] Device nickname: "));
        Serial.println(nickname);
        nicknameUplinkPending = true;
    } else {
        Serial.println(F("[INFO] No nickname set. Type: nick <name>"));
    }
}

void saveNickname(const String& value) {
    String clean = value;
    clean.trim();
    if (clean.length() > NICKNAME_MAX_LENGTH) {
        clean = clean.substring(0, NICKNAME_MAX_LENGTH);
        Serial.print(F("[WARN] Nickname truncated to "));
        Serial.print(NICKNAME_MAX_LENGTH);
        Serial.println(F(" characters."));
    }

    preferences.begin("sbms", /*readOnly=*/false);
    if (clean == "-") {
        preferences.remove("nick");
        nickname[0] = '\0';
        Serial.println(F("[OK] Nickname cleared."));
    } else {
        preferences.putString("nick", clean);
        clean.toCharArray(nickname, sizeof(nickname));
        Serial.print(F("[OK] Nickname saved: "));
        Serial.println(nickname);
        // Send it at the next opportunity rather than waiting for the refresh.
        nicknameUplinkPending = true;
    }
    preferences.end();
#if ENABLE_OLED
    displayDirty = true;
#endif
}

// Read "nick <name>" from the serial console. Non-blocking: the LMIC run loop
// must keep being serviced, so this never waits for input.
void pollSerialConsole() {
    static String line;
    while (Serial.available()) {
        char c = (char)Serial.read();
        if (c == '\r') continue;
        if (c != '\n') {
            if (line.length() < 120) line += c;
            continue;
        }
        String command = line;
        line = "";
        command.trim();
        if (!command.startsWith("nick")) {
            if (command.length()) {
                Serial.println(F("[?] Commands: nick <name> | nick | nick -"));
            }
            continue;
        }
        String argument = command.substring(4);
        argument.trim();
        if (!argument.length()) {
            Serial.print(F("[INFO] Nickname: "));
            Serial.println(strlen(nickname) ? nickname : "(none)");
            continue;
        }
        saveNickname(argument);
    }
}

// ==============================================================================
// PROVISIONING PORTAL
// ==============================================================================
// A board is flashed in one place and installed in another, by somebody
// carrying only a phone. Until it has been named it stays here: it does not
// join, so it never appears in the cloud as an anonymous device and never
// consumes an enrollment slot. Naming it is what commissions it.
//
// The access point is named after the last four characters of this board's
// DevEUI, and the OLED shows the same four, so an installer standing in front
// of thirty powered-up boards can tell which network belongs to the one in
// their hand.

WebServer provisioningServer(80);
static char apSsid[20] = "";
static char apPassword[PROVISION_PASSWORD_LENGTH + 1] = "";
static bool provisioningComplete = false;

// Deliberately excludes characters that are easy to misread off a small
// screen: no O/0, no I/l/1.
static const char PROVISION_PASSWORD_ALPHABET[] =
    "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";

static void buildAccessPointCredentials() {
    // DEVEUI is stored little-endian, so index 0 holds the last displayed byte.
    snprintf(apSsid, sizeof(apSsid), "SBMS-%02X%02X", DEVEUI[1], DEVEUI[0]);

    const size_t alphabet = sizeof(PROVISION_PASSWORD_ALPHABET) - 1;
    for (int i = 0; i < PROVISION_PASSWORD_LENGTH; i++) {
        apPassword[i] = PROVISION_PASSWORD_ALPHABET[esp_random() % alphabet];
    }
    apPassword[PROVISION_PASSWORD_LENGTH] = 0;
}

#if ENABLE_OLED
static void renderProvisioningScreen(const char* note) {
    if (!oledReady) return;
    display.clearDisplay();
    display.setTextSize(1);
    display.setTextColor(SSD1306_WHITE);

    display.setCursor(0, 0);
    display.println(F("SETUP - NAME ME"));
    display.drawFastHLine(0, 10, OLED_WIDTH, SSD1306_WHITE);

    display.setCursor(0, 14);
    display.print(F("WiFi "));
    display.println(apSsid);

    display.setCursor(0, 24);
    display.print(F("Pass "));
    display.println(apPassword);

    display.setCursor(0, 34);
    display.println(F("Open 192.168.4.1"));

    display.drawFastHLine(0, 45, OLED_WIDTH, SSD1306_WHITE);
    display.setCursor(0, 49);
    display.println(note);
    display.display();
}
#endif

// Mirrors the character set the cloud accepts, so a nickname that is typed
// here cannot be silently dropped later by the server's own validation.
static bool nicknameIsAcceptable(const String& value) {
    if (!value.length() || value.length() > NICKNAME_MAX_LENGTH) return false;
    for (unsigned int i = 0; i < value.length(); i++) {
        char c = value[i];
        bool ok = isalnum((unsigned char)c) || c == ' ' || c == '_' ||
                  c == '.' || c == '-' || c == '/' || c == '#';
        if (!ok) return false;
    }
    return true;
}

static const char PROVISION_PAGE[] PROGMEM = R"rawliteral(
<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Name this device</title><style>
body{font-family:system-ui,sans-serif;margin:0;padding:24px;background:#0f172a;color:#e2e8f0}
h1{font-size:20px;margin:0 0 4px}p{color:#94a3b8;font-size:14px;margin:0 0 20px}
label{display:block;font-size:13px;margin-bottom:6px;color:#cbd5e1}
input{width:100%;box-sizing:border-box;padding:14px;font-size:17px;border-radius:8px;
border:1px solid #334155;background:#1e293b;color:#f1f5f9}
button{width:100%;margin-top:16px;padding:16px;font-size:17px;font-weight:600;
border:0;border-radius:8px;background:#2563eb;color:#fff}
button:disabled{background:#475569}
.eui{margin-top:20px;font-size:12px;color:#64748b}
.err{color:#f87171;font-size:13px;margin-top:8px;min-height:18px}
</style></head><body>
<h1>Name this device</h1>
<p>It will not join the network until you confirm.</p>
<form method="POST" action="/save" onsubmit="return check()">
<label for="n">Nickname</label>
<input id="n" name="nickname" autocomplete="off" autocapitalize="none"
       maxlength="48" placeholder="temp_c_floor3_308" required>
<div class="err" id="e"></div>
<button type="submit" id="b">Confirm</button>
</form>
<div class="eui">Device __EUI__</div>
<script>
function check(){
  var v=document.getElementById('n').value.trim();
  if(!/^[A-Za-z0-9 _.\-\/#]+$/.test(v)){
    document.getElementById('e').textContent='Use letters, numbers, space _ . - / # only';
    return false;
  }
  document.getElementById('b').disabled=true;
  document.getElementById('b').textContent='Saving...';
  return true;
}
</script></body></html>
)rawliteral";

static void handleProvisionRoot() {
    String page = FPSTR(PROVISION_PAGE);
    char eui[17];
    for (int i = 0; i < 8; i++) snprintf(&eui[i * 2], 3, "%02X", DEVEUI[7 - i]);
    page.replace("__EUI__", eui);
    provisioningServer.send(200, "text/html", page);
}

static void handleProvisionSave() {
    String value = provisioningServer.arg("nickname");
    value.trim();

    if (!nicknameIsAcceptable(value)) {
        provisioningServer.send(400, "text/html",
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<body style='font-family:system-ui;padding:24px;background:#0f172a;color:#f87171'>"
            "<h2>Rejected</h2><p>Use letters, numbers, space _ . - / # only, "
            "up to 48 characters.</p><a style='color:#60a5fa' href='/'>Back</a></body>");
        return;
    }

    saveNickname(value);

    String done = String(
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<body style='font-family:system-ui;padding:24px;background:#0f172a;color:#e2e8f0'>"
        "<h2 style='color:#4ade80'>Saved</h2><p>This device is now named<br><b>");
    done += value;
    done += "</b></p><p style='color:#94a3b8'>Wi-Fi is shutting down and the "
            "device is joining the network. You can disconnect.</p></body>";
    provisioningServer.send(200, "text/html", done);

    // Give the phone time to actually receive the page before the radio goes
    // away. Deliberately not client().flush(): on this core that discards the
    // receive buffer rather than waiting for the response to be sent, which is
    // the opposite of what is needed here.
    delay(1200);
    provisioningComplete = true;
}

// Blocks until the board has been named. That is the point: an unnamed board
// must not reach the network.
void runProvisioningPortal() {
    buildAccessPointCredentials();

    Serial.println(F("[SETUP] No nickname stored. Starting provisioning portal."));
    Serial.print(F("[SETUP] SSID: "));
    Serial.print(apSsid);
    Serial.print(F("  Password: "));
    Serial.println(apPassword);
    Serial.println(F("[SETUP] Connect, then open http://192.168.4.1"));

    WiFi.mode(WIFI_AP);
    WiFi.softAP(apSsid, apPassword);
    delay(300);

#if ENABLE_OLED
    renderProvisioningScreen("Waiting...");
#endif

    provisioningServer.on("/", HTTP_GET, handleProvisionRoot);
    provisioningServer.on("/save", HTTP_POST, handleProvisionSave);
    // Phones probe these to decide whether a network has internet. Answering
    // with the form is what makes the page pop up on its own.
    provisioningServer.onNotFound(handleProvisionRoot);
    provisioningServer.begin();

    unsigned long lastScreen = 0;
    while (!provisioningComplete) {
        provisioningServer.handleClient();
        // Serial remains available throughout, so a bench operator can still
        // type "nick <name>" instead of using the portal.
        pollSerialConsole();
        if (strlen(nickname)) break;

        if (millis() - lastScreen > 2000) {
            lastScreen = millis();
#if ENABLE_OLED
            char note[22];
            snprintf(note, sizeof(note), "%u client%s",
                     WiFi.softAPgetStationNum(),
                     WiFi.softAPgetStationNum() == 1 ? "" : "s");
            renderProvisioningScreen(note);
#endif
        }
        delay(10);
    }

    provisioningServer.stop();
    WiFi.softAPdisconnect(true);
    WiFi.mode(WIFI_OFF);
    Serial.print(F("[SETUP] Named '"));
    Serial.print(nickname);
    Serial.println(F("'. Wi-Fi off, joining LoRaWAN."));

#if ENABLE_OLED
    if (oledReady) {
        display.clearDisplay();
        display.setCursor(0, 0);
        display.println(F("SAVED"));
        display.drawFastHLine(0, 10, OLED_WIDTH, SSD1306_WHITE);
        display.setCursor(0, 16);
        display.println(nickname);
        display.setCursor(0, 40);
        display.println(F("WiFi off."));
        display.println(F("Joining LoRaWAN..."));
        display.display();
    }
#endif
    delay(1500);
}


// Watch the BOOT button for a sustained press in the first few seconds of
// running. Returns true if it was held long enough to mean "rename me".
static bool renameRequested() {
    unsigned long start = millis();
    unsigned long heldSince = 0;

#if ENABLE_OLED
    if (oledReady) {
        display.clearDisplay();
        display.setCursor(0, 0);
        display.println(F("SBMS LoRaWAN Node"));
        display.setCursor(0, 24);
        display.println(F("Hold BOOT 2s"));
        display.println(F("to rename."));
        display.display();
    }
#endif

    while (millis() - start < RENAME_WINDOW_MS) {
        if (digitalRead(PIN_RESET_NICKNAME) == LOW) {
            if (!heldSince) heldSince = millis();
            if (millis() - heldSince >= RENAME_HOLD_MS) return true;
        } else {
            heldSince = 0;
        }
        delay(20);
    }
    return false;
}

// A small bounded random walk. Simulated values drift like a real sensor
// instead of jumping randomly, so the dashboard graphs and the alarm
// thresholds get something realistic to work against.
static float drift(float value, float step, float low, float high) {
    value += (random(-100, 101) / 100.0) * step;
    if (value < low)  value = low;
    if (value > high) value = high;
    return value;
}

#if ENABLE_OLED
// EU868 maps DR0..DR5 onto SF12..SF7.
static uint8_t currentSpreadingFactor() {
    uint8_t dr = LMIC.datarate;
    return (dr <= 5) ? (12 - dr) : 0;
}

// Redraw the status screen. Called only from loop(), never from an LMIC
// callback: pushing the 1 KB framebuffer over I2C takes long enough that doing
// it inside a callback risks missing an RX window.
static void renderDisplay() {
    if (!oledReady) return;

    display.clearDisplay();
    display.setTextSize(1);
    display.setTextColor(SSD1306_WHITE);

    // The nickname is the whole point of having one, so it takes the title
    // line when set; otherwise the generic name stands in.
    display.setCursor(0, 0);
    display.println(strlen(nickname) ? nickname : "SBMS LoRaWAN Node");
    display.drawFastHLine(0, 10, OLED_WIDTH, SSD1306_WHITE);

    // The DevEUI you need in order to register this board.
    display.setCursor(0, 14);
    display.print(F("EUI "));
    display.println(devEuiText);

    display.setCursor(0, 25);
    display.print(F("State "));
    display.println(joinState);

    display.setCursor(0, 35);
    display.print(F("TX "));
    display.print(uplinkCount);
    display.print(F("  "));
    display.print(lastPayloadLen);
    display.print(F("B"));
    uint8_t sf = currentSpreadingFactor();
    if (sf) {
        display.print(F("  SF"));
        display.print(sf);
    }

    display.drawFastHLine(0, 45, OLED_WIDTH, SSD1306_WHITE);

    display.setCursor(0, 49);
    display.print(lastTemp, 1);
    display.print(F("C "));
    display.print(lastHum, 0);
    display.print(F("% "));
    display.print(lastVbat, 2);
    display.print(F("V"));

#if DUMMY_SENSORS
    display.setCursor(0, 57);
    display.print(F("SIMULATED DATA"));
#endif

    display.display();
}
#endif

void do_send(osjob_t* j) {
    if (LMIC.opmode & OP_TXRXPEND) {
        Serial.println(F("[LMIC] OP_TXRXPEND, not sending now"));
        return;
    }

    // The nickname goes out as its own uplink on its own port. It carries no
    // sensor data, and the cloud handles it before profile validation, so it
    // never displaces a reading. One data uplink is skipped in its favour;
    // the next one follows on the normal schedule.
    if (nicknameUplinkPending && strlen(nickname)) {
        nicknameUplinkPending = false;
        LMIC_setTxData2(LORA_PORT_NICKNAME, (uint8_t*)nickname,
                        strlen(nickname), 0);
        Serial.print(F("[TX] Nickname uplink on port "));
        Serial.print(LORA_PORT_NICKNAME);
        Serial.print(F(": "));
        Serial.println(nickname);
        return;
    }

    lpp.reset();

#if DUMMY_SENSORS
    // ---------------------------------------------------------------------
    // Simulated payload. No sensors required.
    //
    // Each channel below is named for the cloud telemetry field it lands in
    // after TTN decodes it (see CAYENNE_CHANNEL_PREFIX_MAP in app/main.py).
    // ---------------------------------------------------------------------
    static float temp     = 22.5f;   // -> temperature
    static float hum      = 50.0f;   // -> humidity
    static float vbat     = 4.05f;   // -> voltage
    static float lux      = 420.0f;  // -> lux
    static float pressure = 1013.0f; // -> pressure
    static float analogIn = 6.5f;    // -> analog_in
#if DUMMY_EXTENDED_TYPES
    static float current  = 0.65f;   // -> current
    static float power    = 145.0f;  // -> power
    static float co2      = 620.0f;  // -> co2
#endif

    temp     = drift(temp,     0.4f,  18.0f,   28.0f);
    hum      = drift(hum,      1.5f,  30.0f,   70.0f);
    vbat     = drift(vbat,     0.02f,  3.35f,   4.20f);
    lux      = drift(lux,     40.0f,   0.0f, 2000.0f);
    pressure = drift(pressure, 0.8f, 980.0f, 1040.0f);
    analogIn = drift(analogIn, 0.3f,   0.0f,   10.0f);
#if DUMMY_EXTENDED_TYPES
    current  = drift(current,  0.05f,  0.0f,    2.0f);
    power    = drift(power,    8.0f,   0.0f,  500.0f);
    co2      = drift(co2,     35.0f, 400.0f, 1800.0f);
#endif

    // Motion trips roughly 1 uplink in 4; the door contact roughly 1 in 8.
    uint8_t motion   = (random(0, 4) == 0) ? 1 : 0;   // -> motion
    uint8_t doorOpen = (random(0, 8) == 0) ? 1 : 0;   // -> digital_in

    // Standard Cayenne LPP types. Every decoder implements these, including
    // The Things Stack's built-in one. 25 bytes total.
    lpp.addTemperature(1, temp);
    lpp.addRelativeHumidity(2, hum);
    lpp.addPresence(3, motion);
    lpp.addLuminosity(5, (uint16_t)lux);
    lpp.addBarometricPressure(6, pressure);
    lpp.addAnalogInput(7, analogIn);
    lpp.addDigitalInput(8, doorOpen);

#if DUMMY_EXTENDED_TYPES
    // Extended types. Channel 4 is battery voltage, which is as unsupported by
    // the built-in decoder as channels 9-11 are, so it lives here rather than
    // above. Adds 16 bytes.
    lpp.addVoltage(4, vbat);
    lpp.addCurrent(9, current);
    lpp.addPower(10, (uint16_t)power);
    lpp.addConcentration(11, (uint16_t)co2);
#endif

#if ENABLE_OLED
    lastTemp = temp;
    lastHum  = hum;
    lastVbat = vbat;
    lastPayloadLen = lpp.getSize();
    displayDirty = true;
#endif

    Serial.print(F("[TX] SIMULATED Cayenne LPP packet queued: "));
    Serial.print(lpp.getSize());
    Serial.print(F(" bytes | T "));
    Serial.print(temp, 1);
    Serial.print(F("C | RH "));
    Serial.print(hum, 1);
    Serial.print(F("% | Motion "));
    Serial.print(motion);
    Serial.print(F(" | VBat "));
    Serial.print(vbat, 2);
    Serial.println(F("V"));

#else
    // ---------------------------------------------------------------------
    // Real sensor payload.
    // ---------------------------------------------------------------------

    // 1. Read Temperature & Humidity (Channel 1 & 2)
    // If SHT31 is connected: float t = sht31.readTemperature(); float h = sht31.readHumidity();
    float temp = 22.5 + (random(-15, 20) / 10.0);
    float hum  = 50.0 + (random(-20, 20) / 10.0);
    lpp.addTemperature(1, temp);
    lpp.addRelativeHumidity(2, hum);

    // 2. Read PIR Motion / Presence (Channel 3)
    bool motion = (digitalRead(PIN_PIR_MOTION) == HIGH);
    lpp.addPresence(3, motion ? 1 : 0);

    // 3. Read Battery Voltage (Channel 4)
    float vbat = readBatteryVoltage();
    lpp.addVoltage(4, vbat);

#if ENABLE_OLED
    lastTemp = temp;
    lastHum  = hum;
    lastVbat = vbat;
    lastPayloadLen = lpp.getSize();
    displayDirty = true;
#endif

    Serial.print(F("[TX] Cayenne LPP packet queued: "));
    Serial.print(lpp.getSize());
    Serial.print(F(" bytes | Temp: "));
    Serial.print(temp);
    Serial.print(F(" C | Hum: "));
    Serial.print(hum);
    Serial.print(F(" % | VBat: "));
    Serial.print(vbat);
    Serial.println(F(" V"));
#endif

    LMIC_setTxData2(LORA_PORT_DATA, lpp.getBuffer(), lpp.getSize(), 0);
}

void onEvent (ev_t ev) {
    Serial.print(F("[LMIC Event] "));
    Serial.print(os_getTime());
    Serial.print(F(": "));
    switch(ev) {
        case EV_JOINING:
            Serial.println(F("EV_JOINING (Sending OTAA Join Request over LoRa...)"));
#if ENABLE_OLED
            joinState = "JOINING";
            displayDirty = true;
#endif
            break;
        case EV_JOINED:
            Serial.println(F("EV_JOINED! Device is connected to LoRaWAN Gateway!"));
            // Disable link check validation once joined
            LMIC_setLinkCheckMode(0);
#if DUMMY_SENSORS
            // The simulated payload is 25 bytes (41 with extended types). At
            // SF12 even the smaller one is ~1.2 s of
            // airtime per uplink, which the EU868 1% duty cycle and TTN's
            // 30 s/day fair-use budget cannot sustain at TX_INTERVAL_SECONDS.
            // Bench testing is done next to the gateway, so pin the fastest
            // data rate: ~0.12 s per uplink, and a 60 s interval works.
            // Remove this (or set DUMMY_SENSORS to 0) before deploying a node
            // that has to reach a gateway at real range.
            LMIC_setAdrMode(0);
            LMIC_setDrTxpow(DR_SF7, 14);
            Serial.println(F("[BENCH] ADR off, data rate pinned to SF7 for short-range testing."));
#endif
            // Announce the nickname as soon as there is a session to send it on.
            nicknameUplinkPending = strlen(nickname) > 0;
#if ENABLE_OLED
            joinState = "JOINED";
            displayDirty = true;
#endif
            break;
        case EV_JOIN_FAILED:
            Serial.println(F("EV_JOIN_FAILED. Check AppKey / Gateway signal."));
#if ENABLE_OLED
            joinState = "JOIN FAIL";
            displayDirty = true;
#endif
            break;
        case EV_JOIN_TXCOMPLETE:
            // The join request went out but no join-accept came back. This is
            // the useful one: it separates "no gateway heard me" from "a
            // gateway heard me but the JoinEUI/AppKey did not match".
            Serial.println(F("EV_JOIN_TXCOMPLETE (Join request sent, no JoinAccept received)"));
#if ENABLE_OLED
            joinState = "NO ACCEPT";
            displayDirty = true;
#endif
            break;
        case EV_TXSTART:
            // Fired by MCCI LMIC as the radio keys up, once per uplink. Not an
            // error -- it simply has no case in most example sketches, which is
            // why it shows up as "Unknown event: 17".
            Serial.println(F("EV_TXSTART (Radio transmitting...)"));
            break;
        case EV_TXCANCELED:
            // A queued transmission was dropped before it went out, usually
            // because the duty-cycle budget for the sub-band was exhausted.
            Serial.println(F("EV_TXCANCELED (Transmission aborted before sending)"));
            break;
        case EV_RXSTART:
            // Fires twice per uplink, opening the RX1 and RX2 windows. Comment
            // this line out if it makes the serial log too noisy.
            Serial.println(F("EV_RXSTART (Opening receive window)"));
            break;
        case EV_TXCOMPLETE:
            Serial.println(F("EV_TXCOMPLETE (Uplink delivered successfully)"));
            // Re-send the nickname occasionally so a missed uplink is not
            // permanent. An unchanged nickname is a no-op at the other end.
            if (++uplinksSinceNickname >= NICKNAME_REFRESH_UPLINKS) {
                uplinksSinceNickname = 0;
                nicknameUplinkPending = strlen(nickname) > 0;
            }
#if ENABLE_OLED
            uplinkCount++;
            joinState = "ONLINE";
            displayDirty = true;
#endif
            // Schedule next uplink after TX_INTERVAL_SECONDS
            os_setTimedCallback(&sendjob, os_getTime() + sec2osticks(TX_INTERVAL_SECONDS), do_send);
            break;
        case EV_RESET:
            Serial.println(F("EV_RESET"));
            break;
        case EV_RXCOMPLETE:
            Serial.println(F("EV_RXCOMPLETE"));
            break;
        default:
            Serial.print(F("Unknown event: "));
            Serial.println((unsigned) ev);
            break;
    }
}

void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.println(F("\n======================================================="));
    Serial.println(F("  Smart Building Management System - LoRaWAN Node"));
    Serial.println(F("======================================================="));

#if ENABLE_OLED
    // Bring the screen up before anything else can fail, so a fatal error in
    // setup() is visible without a serial cable attached.
    Wire.begin(SBMS_OLED_SDA, SBMS_OLED_SCL);
    oledReady = display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDRESS,
                              /*reset=*/true, /*periphBegin=*/false);
    if (!oledReady) {
        Serial.println(F("[WARN] SSD1306 not found; continuing without the display."));
    } else {
        display.clearDisplay();
        display.setTextSize(1);
        display.setTextColor(SSD1306_WHITE);
        display.setCursor(0, 0);
        display.println(F("SBMS LoRaWAN Node"));
        display.println();
        display.println(F("Starting up..."));
        display.display();
    }
#endif

    // Bluetooth is never used. Wi-Fi is left alone for now: it is needed if
    // this board still has to be named, and is switched off immediately after.
    btStop();

    pinMode(PIN_PIR_MOTION, INPUT_PULLDOWN);
    pinMode(PIN_RESET_NICKNAME, INPUT_PULLUP);

    // Seed the PRNG from the hardware RNG so simulated readings are not
    // identical on every boot.
    randomSeed(esp_random());

    // The DevEUI names the provisioning access point, so it has to exist
    // before the portal can start.
    if (!initUniqueDevEUI()) {
        while (true) {
            delay(1000);
        }
    }

    loadNickname();
    if (strlen(nickname) && renameRequested()) {
        Serial.println(F("[SETUP] BOOT held: forgetting nickname."));
        saveNickname("-");
    }

    // An unnamed board stops here. It does not join, so it never reaches the
    // cloud as an anonymous device and never consumes an enrollment slot --
    // naming it is what commissions it.
    if (!strlen(nickname)) {
        runProvisioningPortal();
    }

    WiFi.mode(WIFI_OFF);
    Serial.println(F("[INFO] Wi-Fi & Bluetooth off (Pure LoRa Mode)"));

    // Initialize the radio bus explicitly. The TTGO LoRa32 does not use the
    // ESP32 default VSPI MISO/MOSI mapping.
    SPI.begin(SBMS_LORA_SCK, SBMS_LORA_MISO, SBMS_LORA_MOSI, SBMS_LORA_NSS);

    // Initialize LMIC with the verified pin table and stop cleanly if the
    // SX1276 cannot be initialized.
    if (!os_init_ex((const void*)&lmic_pins)) {
        Serial.println(F("[FATAL] SX1276 initialization failed. Check board revision and LoRa pins."));
#if ENABLE_OLED
        if (oledReady) {
            display.clearDisplay();
            display.setCursor(0, 0);
            display.println(F("LoRa INIT FAILED"));
            display.println();
            display.println(F("Check board revision"));
            display.println(F("and SX1276 pins."));
            display.display();
        }
#endif
        while (true) {
            delay(1000);
        }
    }
    LMIC_reset();

    // Optional: Relax clock tolerance for ESP32 internal RC oscillator
    LMIC_setClockError(MAX_CLOCK_ERROR * 10 / 100);

    // Start OTAA Join and initial transmission
    Serial.println(F("[INFO] Starting LoRaWAN transmission..."));
    do_send(&sendjob);
}

void loop() {
    os_runloop_once();
    pollSerialConsole();

#if ENABLE_OLED
    // Refresh at most every 250 ms, and never while a transmission or receive
    // window is pending -- os_runloop_once() has to be serviced promptly, and
    // the I2C framebuffer push is comparatively slow.
    static unsigned long lastRender = 0;
    if (displayDirty && !(LMIC.opmode & OP_TXRXPEND)
        && (millis() - lastRender) > 250) {
        lastRender = millis();
        displayDirty = false;
        renderDisplay();
    }
#endif
}
