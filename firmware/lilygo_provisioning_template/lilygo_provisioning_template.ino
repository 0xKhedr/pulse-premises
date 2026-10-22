/*
 * LILYGO TTGO LoRa32-OLED V2.1 (1.6.1): reusable SBMS provisioning template.
 *
 * Open this file as its own Arduino sketch. It contains the existing LoRaWAN
 * JoinEUI, AppKey, and application namespace mask as literal byte arrays.
 * No lorawan_credentials.h or sensor-specific library is needed.
 * The unique DevEUI still comes from this board's ESP32 MAC plus the mask.
 *
 * Install: MCCI LoRaWAN LMIC library, Adafruit SSD1306, Adafruit GFX Library
 * (and Adafruit BusIO when prompted). Keep the existing EU868/SX1276 LMIC
 * configuration and select the same TTGO LoRa32 V2.1 board as the other sketch.
 *
 * Provisioning: an unnamed board opens SBMS-xxxx Wi-Fi; its password appears
 * on the OLED and at 115200 baud. Open http://192.168.4.1 to name it. The name
 * is saved in flash, Wi-Fi turns off, and the board joins LoRaWAN via OTAA.
 * Hold BOOT for two seconds AFTER reset to reopen naming. Serial commands:
 * nick <name> | nick | nick -
 *
 * EDIT THE FIVE SECTIONS MARKED "USER CODE START":
 *  1. INCLUDES: sensor libraries.
 *  2. GLOBALS: sensor objects, pins, cached readings, and application variables.
 *  3. SETUP: initialize your hardware once, after provisioning.
 *  4. LOOP: short, non-blocking application/sensor work while radio is idle.
 *  5. PAYLOAD: fill the byte buffer using YOUR data and encoder.
 *
 * As supplied, this joins and sends the nickname on port 10, but sends NO
 * sensor data until you implement userBuildPayload(). It does not invent data.
 * Use port 1 and matching Cayenne LPP channels for the existing cloud profile,
 * or configure the TTN formatter/cloud profile for your custom payload format.
 */

#include <Arduino.h>
#include <WiFi.h>
#include <lmic.h>
#include <hal/hal.h>
#include <SPI.h>
#include <Wire.h>
#include <esp_mac.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Preferences.h>
#include <WebServer.h>

// ==================== USER CODE START: INCLUDES ====================
// Add your sensor and payload library includes here, for example:
// #include <Adafruit_SHT31.h>
// #include <CayenneLPP.h>
// ===================== USER CODE END: INCLUDES =====================

// Hardcoded OTAA credentials from the existing local firmware header.
// JoinEUI is little-endian for LMIC; AppKey is big-endian.
static const u1_t PROGMEM APPEUI[8] = { 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00 };
static const u1_t PROGMEM APPKEY[16] = { 0xC8, 0x30, 0x60, 0x4B, 0x60, 0xB6, 0xF9, 0x4F, 0x2A, 0x23, 0x61, 0x21, 0xC6, 0xA1, 0x89, 0xD2 };
static const u1_t PROGMEM DEV_EUI_XOR[8] = { 0xA2, 0x92, 0x97, 0x36, 0xBD, 0x5B, 0xEA, 0xA1 };
static u1_t DEVEUI[8];
void os_getArtEui(u1_t* buf) { memcpy_P(buf, APPEUI, sizeof(APPEUI)); }
void os_getDevKey(u1_t* buf) { memcpy_P(buf, APPKEY, sizeof(APPKEY)); }
void os_getDevEui(u1_t* buf) { memcpy(buf, DEVEUI, sizeof(DEVEUI)); }

// Verified LoRa pins for TTGO LoRa32-OLED V2.1 (1.6.1).
constexpr int SBMS_LORA_SCK = 5;
constexpr int SBMS_LORA_MISO = 19;
constexpr int SBMS_LORA_MOSI = 27;
constexpr int SBMS_LORA_NSS = 18;
const lmic_pinmap lmic_pins = {
    .nss = SBMS_LORA_NSS,
    .rxtx = LMIC_UNUSED_PIN,
    .rst = 23,
    .dio = {26, 33, 32},
};

// Configuration. Port 10 is reserved for the SBMS device nickname.
constexpr unsigned TX_INTERVAL_SECONDS = 30;
constexpr uint8_t USER_DATA_PORT = 1;
constexpr uint8_t USER_PAYLOAD_CAPACITY = 51;
#define BENCH_RADIO_TEST 1  // 0 enables ADR; 1 keeps the original bench SF7.
#define LORA_PORT_NICKNAME 10
#define NICKNAME_MAX_LENGTH 48
#define NICKNAME_REFRESH_UPLINKS 60
#define PROVISION_PASSWORD_LENGTH 8
#define PIN_RESET_NICKNAME 0
#define RENAME_HOLD_MS 2000
#define RENAME_WINDOW_MS 4000
#define ENABLE_OLED 1
#define OLED_WIDTH 128
#define OLED_HEIGHT 64
#define OLED_ADDRESS 0x3C
#define SBMS_I2C_SDA 21
#define SBMS_I2C_SCL 22
#define OLED_RESET -1

Preferences preferences;
static char nickname[NICKNAME_MAX_LENGTH + 1] = "";
static bool nicknameUplinkPending = false;
static uint16_t uplinksSinceNickname = 0;
static osjob_t sendjob;
static bool loraJoined = false;
static bool sendDue = false;
static bool lastTxWasNickname = false;
static const char* dataStatus = "ADD PAYLOAD CODE";

#if ENABLE_OLED
Adafruit_SSD1306 display(OLED_WIDTH, OLED_HEIGHT, &Wire, OLED_RESET);
static bool oledReady = false;
static char devEuiText[17] = "................";
static const char* joinState = "BOOTING";
static uint32_t uplinkCount = 0;
static uint16_t lastPayloadLen = 0;
static bool displayDirty = true;
#endif

// ==================== USER CODE START: GLOBALS =====================
// Add sensor objects, GPIO definitions, cached readings, and other variables.
// I2C on SDA=21/SCL=22 is already initialized and shared with the OLED.
// Do not reuse the LoRa pins or BOOT GPIO0 for your sensor.
// ===================== USER CODE END: GLOBALS ======================

static void userSetup() {
    // ================= USER CODE START: SETUP ======================
    // Initialize your sensors/hardware here. Called once after naming and
    // before LoRaWAN starts, so setup delays here do not affect RX windows.
    // Example: sensor.begin(...); pinMode(...);
    // Do not call Wire.begin() again; use the existing Wire bus.
    // ================== USER CODE END: SETUP =======================
}

static void userLoop() {
    // ================= USER CODE START: LOOP =======================
    // Add short, non-blocking application work here. Called repeatedly once
    // joined, while no radio transaction is pending. Use millis() for timing;
    // cache sensor readings for userBuildPayload(). Avoid delay(), busy waits,
    // long I2C operations, and Serial.readString()/readStringUntil().
    // ================== USER CODE END: LOOP ========================
}

static bool userBuildPayload(uint8_t* payload, uint8_t capacity,
                             uint8_t& length, uint8_t& port) {
    length = 0;
    port = USER_DATA_PORT;
    // ================= USER CODE START: PAYLOAD =====================
    // Read your cached sensor values and encode them into payload[0..length).
    // Never write beyond capacity. Set length and port, then return true.
    // Return false when data is unavailable or a sensor reading failed.
    // Choose a port from 1..223, excluding 10 (reserved for the nickname).
    // The framework handles transmission and the next scheduled attempt.
    //
    // Cayenne LPP example (uncomment its include and add real sensor values):
    // CayenneLPP lpp(capacity);
    // lpp.addTemperature(1, measuredTemperature);
    // lpp.addRelativeHumidity(2, measuredHumidity);
    // if (!lpp.getSize() || lpp.getSize() > capacity) return false;
    // length = lpp.getSize();
    // memcpy(payload, lpp.getBuffer(), length);
    // return true;
    //
    // Default: no data until your own code is added.
    return false;
    // ================== USER CODE END: PAYLOAD ======================
}

// Everything below handles identity, provisioning, display, and LoRaWAN.
static bool nicknameIsAcceptable(const String& value);
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
        if (argument != "-" && !nicknameIsAcceptable(argument)) {
            Serial.println(F("[WARN] Invalid nickname; use the portal's accepted characters."));
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
    if (value == "-" || !value.length() || value.length() > NICKNAME_MAX_LENGTH) return false;
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
    provisioningComplete = false;
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
#if ENABLE_OLED
static void renderDisplay() {
    if (!oledReady) return;
    display.clearDisplay();
    display.setTextSize(1);
    display.setTextColor(SSD1306_WHITE);
    display.setCursor(0, 0);
    display.println(strlen(nickname) ? nickname : "SBMS Provisioning");
    display.drawFastHLine(0, 10, OLED_WIDTH, SSD1306_WHITE);
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
    display.println(F("B"));
    display.drawFastHLine(0, 45, OLED_WIDTH, SSD1306_WHITE);
    display.setCursor(0, 49);
    display.println(dataStatus);
    display.display();
}
#endif

// LMIC callbacks only set flags; your sensor code runs from loop().
static void markSendDue(osjob_t*) { sendDue = true; }

static void scheduleNextSend() {
    sendDue = false;
    os_setTimedCallback(&sendjob, os_getTime() + sec2osticks(TX_INTERVAL_SECONDS), markSendDue);
}

static void sendNextUplink() {
    if (!loraJoined || !LMIC_queryTxReady() || (LMIC.opmode & (OP_TXRXPEND | OP_JOINING))) return;
    sendDue = false;
    uint8_t payload[USER_PAYLOAD_CAPACITY];
    // Respect both this template's buffer and the configured LMIC buffer.
    const uint8_t capacity = sizeof(payload) < sizeof(LMIC.pendTxData)
                              ? sizeof(payload) : sizeof(LMIC.pendTxData);
    uint8_t length = 0;
    uint8_t port = USER_DATA_PORT;
    const bool isNickname = nicknameUplinkPending && strlen(nickname);
    uint8_t* bytes = payload;
    if (isNickname) {
        bytes = reinterpret_cast<uint8_t*>(nickname);
        length = strlen(nickname);
        port = LORA_PORT_NICKNAME;
    } else {
        const bool ready = userBuildPayload(payload, capacity, length, port);
        if (!ready || !length) {
            dataStatus = "NO USER DATA";
            Serial.println(F("[DATA] No user payload; retrying at the next interval."));
#if ENABLE_OLED
            displayDirty = true;
#endif
            scheduleNextSend();
            return;
        }
        if (length > capacity || port == 0 || port > 223 || port == LORA_PORT_NICKNAME) {
            dataStatus = "BAD USER PAYLOAD";
            Serial.println(F("[DATA] Invalid payload length/port; uplink skipped."));
#if ENABLE_OLED
            displayDirty = true;
#endif
            scheduleNextSend();
            return;
        }
    }
    const lmic_tx_error_t result = LMIC_setTxData2(port, bytes, length, 0);
    if (result != LMIC_ERROR_SUCCESS) {
        Serial.printf("[TX] Queue rejected (%d); will retry.\n", (int)result);
        scheduleNextSend();
        return;
    }
    lastTxWasNickname = isNickname;
    if (isNickname) nicknameUplinkPending = false;
    else dataStatus = "USER DATA QUEUED";
#if ENABLE_OLED
    lastPayloadLen = length;
    displayDirty = true;
#endif
    Serial.printf("[TX] %s queued: port %u, %u bytes\n",
                  isNickname ? "Nickname" : "User data", port, length);
}

void onEvent(ev_t ev) {
    switch (ev) {
        case EV_JOINING:
            Serial.println(F("[LMIC] Joining via OTAA..."));
#if ENABLE_OLED
            joinState = "JOINING";
#endif
            break;
        case EV_JOINED:
            Serial.println(F("[LMIC] Joined."));
            loraJoined = true;
            LMIC_setLinkCheckMode(0);
#if BENCH_RADIO_TEST
            LMIC_setAdrMode(0);
            LMIC_setDrTxpow(DR_SF7, 14);
#else
            LMIC_setAdrMode(1);
#endif
            nicknameUplinkPending = strlen(nickname) > 0;
            sendDue = true;
#if ENABLE_OLED
            joinState = "JOINED";
#endif
            break;
        case EV_TXCOMPLETE:
            Serial.println(F("[LMIC] Transmit/receive cycle complete."));
            if (++uplinksSinceNickname >= NICKNAME_REFRESH_UPLINKS) {
                uplinksSinceNickname = 0;
                nicknameUplinkPending = strlen(nickname) > 0;
            }
#if ENABLE_OLED
            ++uplinkCount;
            joinState = "ONLINE";
#endif
            scheduleNextSend();
            break;
        case EV_TXCANCELED:
            if (lastTxWasNickname) nicknameUplinkPending = strlen(nickname) > 0;
            Serial.println(F("[LMIC] Uplink canceled; retry scheduled."));
            scheduleNextSend();
            break;
        case EV_JOIN_FAILED:
            loraJoined = false;
            Serial.println(F("[LMIC] Join failed. Check gateway and OTAA credentials."));
#if ENABLE_OLED
            joinState = "JOIN FAIL";
#endif
            break;
        case EV_JOIN_TXCOMPLETE:
            Serial.println(F("[LMIC] Join request sent; no join accept received."));
#if ENABLE_OLED
            joinState = "NO ACCEPT";
#endif
            break;
        case EV_RESET:
            loraJoined = false;
            sendDue = false;
            break;
        default:
            break;
    }
#if ENABLE_OLED
    displayDirty = true;
#endif
}

void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.println(F("\nSBMS - Sensor-independent provisioning template"));
    Wire.begin(SBMS_I2C_SDA, SBMS_I2C_SCL);
    Wire.setTimeOut(50);
#if ENABLE_OLED
    oledReady = display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDRESS, true, false);
    if (oledReady) renderDisplay();
    else Serial.println(F("[WARN] OLED unavailable; use Serial Monitor for provisioning."));
#endif
    btStop();
    pinMode(PIN_RESET_NICKNAME, INPUT_PULLUP);
    if (!initUniqueDevEUI()) {
        while (true) delay(1000);
    }
    loadNickname();
    if (strlen(nickname) && renameRequested()) saveNickname("-");
    if (!strlen(nickname)) runProvisioningPortal();
    WiFi.mode(WIFI_OFF);
    userSetup();
    SPI.begin(SBMS_LORA_SCK, SBMS_LORA_MISO, SBMS_LORA_MOSI, SBMS_LORA_NSS);
    if (!os_init_ex((const void*)&lmic_pins)) {
        Serial.println(F("[FATAL] SX1276 initialization failed; check LoRa pins/board."));
#if ENABLE_OLED
        joinState = "RADIO FAIL";
        renderDisplay();
#endif
        while (true) delay(1000);
    }
    LMIC_reset();
    LMIC_setClockError(MAX_CLOCK_ERROR * 10 / 100);
    // Join explicitly: provisioning works even when the user payload is empty.
    LMIC_startJoining();
}

void loop() {
    os_runloop_once();
    pollSerialConsole();
    if (loraJoined && !(LMIC.opmode & (OP_TXRXPEND | OP_JOINING)) && LMIC_queryTxReady()) {
        userLoop();
        if (sendDue) sendNextUplink();
    }
#if ENABLE_OLED
    static unsigned long lastRender = 0;
    if (displayDirty && !(LMIC.opmode & OP_TXRXPEND) && millis() - lastRender > 250) {
        lastRender = millis();
        displayDirty = false;
        renderDisplay();
    }
#endif
}
