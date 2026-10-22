/*
 * LilyGO TTGO LoRa32-OLED V2.1 (1.6.1) - DHT11 provisioning debug copy.
 * Based on the provisioning template and the user's pasted DHT11 code.
 *
 * First flash: connect to SBMS-xxxx using the OLED/Serial password, then
 * open http://192.168.4.1 and save a nickname BEFORE LoRaWAN can join.
 * Serial Monitor at 115200 baud prints every startup stage and LMIC events.
 * The OLED displays the stage before potentially blocking initialization.
 *
 * Sensor configuration matches the pasted code: DHT11 DATA on GPIO4.
 * This is not the I2C sensor configuration on SDA21/SCL22; those pins are
 * used here by the onboard OLED. Change the sensor code for I2C hardware.
 *
 * Install DHT sensor library by Adafruit, Adafruit Unified Sensor, CayenneLPP,
 * and the provisioning template's existing display/LMIC libraries.
 * The existing credentials are embedded below. Earlier sketches are retained.
 *
 * The startup path matches the working environment sketch: a nickname is
 * queued before joining, triggering OTAA and sending the name after joining.
 * Sensor data uses Cayenne LPP temperature channel 1 and humidity channel 2
 * on port 1, matching the environment sketch's TTN formatter/cloud profile.
 * Nickname uses port 10 as before. Join diagnostics include frequency,
 * data rate, and DevNonce; keys are never printed.
 */

#include <Arduino.h>
#include <WiFi.h>
#include <lmic.h>
#include <hal/hal.h>
#include <SPI.h>
#include <Wire.h>
#include <esp_mac.h>
#include <esp_system.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Preferences.h>
#include <WebServer.h>

// ==================== USER CODE START: INCLUDES ====================
// Add your sensor and payload library includes here, for example:
#include <DHT.h>
#include <math.h>
#include <CayenneLPP.h>
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
constexpr unsigned TX_INTERVAL_SECONDS = 60;
constexpr uint8_t USER_DATA_PORT = 1;
constexpr uint8_t USER_PAYLOAD_CAPACITY = 51;
#define BENCH_RADIO_TEST 1  // 0 enables ADR; 1 keeps the original bench SF7.
#define LORA_PORT_NICKNAME 10
#define NICKNAME_MAX_LENGTH 48
#define NICKNAME_REFRESH_UPLINKS 60
#define PROVISION_PASSWORD_LENGTH 8
// GPIO0 is BOOT, not RST. RST holds the CPU in reset and cannot be polled.
#define PIN_BOOT_NICKNAME 0
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
static bool initialNicknameQueued = false;
static uint32_t joinAttemptCount = 0;
static const char* dataStatus = "COLLECTING DATA";

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
constexpr uint8_t DHT_PIN = 4;
#define DHT_TYPE DHT11
DHT dht(DHT_PIN, DHT_TYPE);
static float cachedAvgTemp = NAN;
static float cachedAvgHum = NAN;
static bool dataReadyToTransmit = false;
// ===================== USER CODE END: GLOBALS ======================

static void userSetup() {
    // ================= USER CODE START: SETUP ======================
    Serial.println(F("[SENSOR] Initializing DHT11 on GPIO4."));
    dht.begin();
    dataStatus = "COLLECTING DATA";
    // ================== USER CODE END: SETUP =======================
}

static void userLoop() {
    // ================= USER CODE START: LOOP =======================
    static unsigned long lastSampleTime = 0;
    static float tempSum = 0, humSum = 0;
    static unsigned validReadings = 0, sampleCount = 0;
    if (millis() - lastSampleTime < 3000) return;
    lastSampleTime = millis();
    const float temperature = dht.readTemperature();
    const float humidity = dht.readHumidity();
    if (isfinite(temperature) && isfinite(humidity)) {
        tempSum += temperature;
        humSum += humidity;
        ++validReadings;
        Serial.printf("[SENSOR] Sample %u/20: %.1f C, %.1f %%\n", sampleCount + 1, temperature, humidity);
    } else {
        Serial.println(F("[SENSOR] DHT11 read failed; check DATA on GPIO4 and sensor type."));
    }
    if (++sampleCount >= 20) {
        dataReadyToTransmit = validReadings > 0;
        if (dataReadyToTransmit) {
            cachedAvgTemp = tempSum / validReadings;
            cachedAvgHum = humSum / validReadings;
            dataStatus = "DATA READY";
            Serial.printf("[SENSOR] Average of %u valid readings: %.1f C, %.1f %%\n",
                          validReadings, cachedAvgTemp, cachedAvgHum);
        } else {
            dataStatus = "DHT ERROR";
        }
        tempSum = humSum = 0;
        validReadings = sampleCount = 0;
#if ENABLE_OLED
        displayDirty = true;
#endif
    }
    // ================== USER CODE END: LOOP ========================
}

static bool userBuildPayload(uint8_t* payload, uint8_t capacity,
                             uint8_t& length, uint8_t& port) {
    length = 0;
    port = USER_DATA_PORT;
    // ================= USER CODE START: PAYLOAD =====================
    // Use the readiness flag, not a pointer comparison with a string literal.
    if (!dataReadyToTransmit || capacity < 7) return false;
    if (!isfinite(cachedAvgTemp) || !isfinite(cachedAvgHum)
        || cachedAvgHum < 0 || cachedAvgHum > 100) return false;
    CayenneLPP lpp(capacity);
    lpp.addTemperature(1, cachedAvgTemp);
    lpp.addRelativeHumidity(2, cachedAvgHum);
    length = lpp.getSize();
    if (length != 7 || length > capacity) return false;
    memcpy(payload, lpp.getBuffer(), length);
    // Keep the reading ready until LMIC has accepted the queued transmission.
    return true;
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

#if ENABLE_OLED
    // Show the AP details BEFORE initializing Wi-Fi. If Wi-Fi stalls, the
    // screen and Serial Monitor identify that exact startup stage.
    renderProvisioningScreen("Starting WiFi...");
#endif
    Serial.println(F("[SETUP] Starting Wi-Fi AP mode..."));
    Serial.flush();
    const bool apModeReady = WiFi.mode(WIFI_AP);
    Serial.printf("[SETUP] Wi-Fi AP mode: %s\n", apModeReady ? "OK" : "FAILED");
    const bool apReady = apModeReady && WiFi.softAP(apSsid, apPassword);
    if (!apReady) {
        Serial.println(F("[SETUP] Access point failed. Type nick <name> over USB to continue."));
#if ENABLE_OLED
        renderProvisioningScreen("WiFi fail: USB nick");
#endif
        while (!strlen(nickname)) {
            pollSerialConsole();
            delay(10);
        }
        WiFi.mode(WIFI_OFF);
        return;
    }
    Serial.print(F("[SETUP] AP ready; portal address: "));
    Serial.println(WiFi.softAPIP());
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


// RST cannot report its hold time: code stops while it is pressed. Instead,
// remember this startup window in NVS, which survives an EN/RST reset. A
// second reset while the prompt is visible requests naming. GPIO0/BOOT still
// supports a two-second hold during the same window.
static bool renameRequested() {
    bool resetWindowArmed = false;
    const bool storageReady = preferences.begin("sbms", /*readOnly=*/false);
    if (storageReady) {
        resetWindowArmed = preferences.getBool("renameArmed", false);
        if (resetWindowArmed && !preferences.remove("renameArmed")) {
            Serial.println(F("[WARN] Could not clear rename marker; skipping reset gesture."));
            preferences.end();
            return false;
        }
    }

    // Clear a stale marker even when no name is stored. Unnamed boards go
    // straight to the portal without opening another reset window.
    if (!strlen(nickname)) {
        if (storageReady) preferences.end();
        return false;
    }

    // ESP32 reports EN/RST resets as POWERON. Software restarts, watchdogs,
    // brownouts, and panic recovery must not erase the saved nickname.
    if (resetWindowArmed && esp_reset_reason() == ESP_RST_POWERON) {
        preferences.end();
        Serial.println(F("[RENAME] Second reset detected. Opening naming mode."));
        return true;
    }

    const bool resetGestureReady = storageReady && preferences.putBool("renameArmed", true) != 0;
    if (storageReady) preferences.end();
    if (resetGestureReady) {
        Serial.println(F("[RENAME] Press RST again during the next 4s to rename, or hold BOOT 2s."));
    } else {
        Serial.println(F("[WARN] Reset gesture unavailable (NVS write failed). Hold BOOT 2s to rename."));
    }

    unsigned long heldSince = 0;
    bool bootHeld = false;
    bool requested = false;

#if ENABLE_OLED
    if (oledReady) {
        display.clearDisplay();
        display.setCursor(0, 0);
        display.println(F("SBMS LoRaWAN Node"));
        display.setCursor(0, 16);
        if (resetGestureReady) {
            display.println(F("Press RST again"));
            display.println(F("within 4s to rename"));
        }
        display.setCursor(0, 40);
        display.println(F("Or hold BOOT 2s"));
        display.display();
    }
#endif

    const unsigned long start = millis();
    while (millis() - start < RENAME_WINDOW_MS) {
        if (digitalRead(PIN_BOOT_NICKNAME) == LOW) {
            if (!bootHeld) {
                heldSince = millis();
                bootHeld = true;
            }
            if (millis() - heldSince >= RENAME_HOLD_MS) {
                requested = true;
                break;
            }
        } else {
            bootHeld = false;
        }
        delay(20);
    }
    // Expire the marker before sensor/radio initialization, even after a
    // BOOT hold, so an ordinary reset later does not reopen naming mode.
    if (resetGestureReady) {
        if (preferences.begin("sbms", /*readOnly=*/false)) {
            if (!preferences.remove("renameArmed")) {
                Serial.println(F("[WARN] Rename marker cleanup failed."));
            }
            preferences.end();
        } else {
            Serial.println(F("[WARN] Could not reopen NVS to expire rename marker."));
        }
    }
    if (requested) Serial.println(F("[RENAME] BOOT held for 2s. Opening naming mode."));
    return requested;
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

static void startJoinWithNickname() {
    // Match the environment sketch: queue the nickname BEFORE a session
    // exists. LMIC starts OTAA and holds this uplink until joining succeeds.
    initialNicknameQueued = true;
    lastTxWasNickname = true;
    nicknameUplinkPending = false;
    const lmic_tx_error_t result = LMIC_setTxData2(
        LORA_PORT_NICKNAME, reinterpret_cast<uint8_t*>(nickname), strlen(nickname), 0);
    if (result != LMIC_ERROR_SUCCESS) {
        initialNicknameQueued = false;
        nicknameUplinkPending = true;
        Serial.printf("[JOIN] Initial nickname queue failed (%d); starting OTAA directly.\n", (int)result);
        LMIC_startJoining();
    } else {
        Serial.println(F("[JOIN] Nickname queued; OTAA started using the environment sketch's startup path."));
    }
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
    else {
        dataReadyToTransmit = false;
        dataStatus = "USER DATA QUEUED";
    }
#if ENABLE_OLED
    lastPayloadLen = length;
    displayDirty = true;
#endif
    Serial.printf("[TX] %s queued: port %u, %u bytes\n",
                  isNickname ? "Nickname" : "User data", port, length);
}

void onEvent(ev_t ev) {
    Serial.printf("[LMIC] Event %u, opmode 0x%04X\n", (unsigned)ev, (unsigned)LMIC.opmode);
    switch (ev) {
        case EV_JOINING:
            Serial.println(F("[LMIC] Joining via OTAA..."));
#if ENABLE_OLED
            joinState = "JOINING";
#endif
            break;
        case EV_TXSTART:
            if (LMIC.opmode & OP_JOINING) {
                Serial.printf("[JOIN] Attempt %lu | Frequency %lu Hz | DR %u | DevNonce %u\n",
                              (unsigned long)++joinAttemptCount,
                              (unsigned long)LMIC.freq,
                              (unsigned)LMIC.datarate,
                              (unsigned)LMIC.devNonce);
            }
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
            // LMIC already holds the startup nickname. Wait for its TX
            // completion; queue a nickname only if the initial queue failed.
            nicknameUplinkPending = !initialNicknameQueued && strlen(nickname) > 0;
            sendDue = !initialNicknameQueued;
#if ENABLE_OLED
            joinState = "JOINED";
#endif
            break;
        case EV_TXCOMPLETE:
            initialNicknameQueued = false;
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
            initialNicknameQueued = false;
            if (lastTxWasNickname) nicknameUplinkPending = strlen(nickname) > 0;
            else dataReadyToTransmit = true;
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
            initialNicknameQueued = false;
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
    Serial.println(F("\n[BOOT] DHT11 provisioning debug sketch starting."));
    Serial.println(F("[BOOT] Initializing I2C on SDA21/SCL22..."));
    Wire.begin(SBMS_I2C_SDA, SBMS_I2C_SCL);
    Wire.setTimeOut(50);
#if ENABLE_OLED
    Serial.println(F("[BOOT] Initializing OLED..."));
    oledReady = display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDRESS, true, false);
    if (oledReady) {
        joinState = "INIT IDENTITY";
        renderDisplay();
    } else Serial.println(F("[WARN] OLED unavailable; use Serial Monitor."));
#endif
    Serial.println(F("[BOOT] Disabling Bluetooth..."));
    btStop();
    pinMode(PIN_BOOT_NICKNAME, INPUT_PULLUP);
    Serial.println(F("[BOOT] Generating DevEUI..."));
    if (!initUniqueDevEUI()) {
        Serial.println(F("[FATAL] ESP32 MAC/DevEUI initialization failed."));
#if ENABLE_OLED
        joinState = "EUI FAILED";
        renderDisplay();
#endif
        while (true) delay(1000);
    }
    Serial.println(F("[BOOT] Loading saved nickname..."));
    loadNickname();
    if (renameRequested()) saveNickname("-");
    if (!strlen(nickname)) {
        Serial.println(F("[BOOT] No nickname. Naming is required before joining LoRaWAN."));
#if ENABLE_OLED
        joinState = "NAME REQUIRED";
        renderDisplay();
#endif
        runProvisioningPortal();
    }
    Serial.println(F("[BOOT] Nickname is set. Turning Wi-Fi off..."));
    WiFi.mode(WIFI_OFF);
    // Always initialize the DHT, including subsequent boots with a saved name.
    userSetup();
    Serial.println(F("[BOOT] Initializing SPI and SX1276..."));
#if ENABLE_OLED
    joinState = "RADIO INIT";
    renderDisplay();
#endif
    SPI.begin(SBMS_LORA_SCK, SBMS_LORA_MISO, SBMS_LORA_MOSI, SBMS_LORA_NSS);
    if (!os_init_ex((const void*)&lmic_pins)) {
        Serial.println(F("[FATAL] SX1276 initialization failed. Check board revision and LoRa pins."));
#if ENABLE_OLED
        joinState = "RADIO FAILED";
        renderDisplay();
#endif
        while (true) delay(1000);
    }
    Serial.println(F("[BOOT] Resetting LMIC..."));
    LMIC_reset();
    LMIC_setClockError(MAX_CLOCK_ERROR * 10 / 100);
    // Render before starting: radio receive windows may defer OLED updates.
#if ENABLE_OLED
    joinState = "JOINING";
    renderDisplay();
#endif
    Serial.println(F("[BOOT] Starting OTAA join; waiting for the gateway/network..."));
    startJoinWithNickname();
    Serial.println(F("[BOOT] setup() complete; servicing LMIC in loop()."));
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
