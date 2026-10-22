/*
 * SBMS GPS LoRaWAN node: LilyGO TTGO LoRa32-OLED V2.1 / 1.6.1 + u-blox NEO-6M.
 * Retains the Cayenne node's OTAA identity, naming portal, BOOT rename,
 * persistent nickname and port-10 announcements. See README.md for wiring.
 * Libraries: MCCI LoRaWAN LMIC, CayenneLPP (Electronic Cats), TinyGPSPlus
 * (Mikal Hart), Adafruit SSD1306 and Adafruit GFX, with their dependencies.
 * LMIC configuration must match the existing node: EU868 and SX1276.
 * Sends real, fresh latitude/longitude/altitude on LPP channel 1, port 1.
 */

#include <Arduino.h>
#include <esp_arduino_version.h>
#include <WiFi.h>
#include <lmic.h>
#include <hal/hal.h>
#include <SPI.h>
#include <Wire.h>
#include <CayenneLPP.h>
#include <TinyGPS++.h>
#include <math.h>
#include <esp_mac.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Preferences.h>
#include <WebServer.h>

// ==============================================================================
// 1. LORAWAN OTAA CREDENTIALS
// ==============================================================================

#if __has_include("lorawan_credentials.h")
#include "lorawan_credentials.h"
#else
#error "Missing sketch-local lorawan_credentials.h. Run: python tools/generate_lorawan_credentials.py"
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
// Same short-range bench settings as the temperature node. For deployment,
// increase TX_INTERVAL_SECONDS and set BENCH_RADIO_TEST to 0 for ADR.
const unsigned TX_INTERVAL_SECONDS = 30;
#define BENCH_RADIO_TEST 1
static osjob_t sendjob;
CayenneLPP lpp(11); // channel + GPS type + three signed 24-bit values

// UART2 is independent of USB serial, OLED, SD and the SX1276 radio pins.
// Exposed T3 V1.6.1 headers, matching LilyGO's GPS wiring guide.
// GPS TX -> GPIO34; leave GPS RX disconnected (optional GPIO12 TX).
// GPIO34 is input-only, which is suitable for receiving GPS data.
constexpr int GPS_RX_PIN = 34;
constexpr int GPS_TX_PIN = 12;
constexpr uint32_t GPS_BAUD = 9600;
constexpr uint32_t GPS_MAX_AGE_MS = 5000;
constexpr unsigned GPS_RETRY_SECONDS = 5;
HardwareSerial gpsSerial(2);
TinyGPSPlus gps;
static double lastLatitude = 0.0;
static double lastLongitude = 0.0;
static double lastAltitude = 0.0;
static bool gpsReadingValid = false;
static uint32_t gpsBytesReceived = 0;
static uint32_t lastGpsByteMs = 0;

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
//     nick gps_roof_01
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

// TinyGPSPlus keeps isValid() true after signal loss. Require fresh position
// AND altitude so an old fix, or a made-up altitude, never goes over the air.
static bool readGpsFix() {
    if (!gps.location.isValid() || !gps.altitude.isValid()
        || gps.location.age() > GPS_MAX_AGE_MS
        || gps.altitude.age() > GPS_MAX_AGE_MS) return false;

    const double lat = gps.location.lat();
    const double lon = gps.location.lng();
    const double alt = gps.altitude.meters();
    if (!isfinite(lat) || !isfinite(lon) || !isfinite(alt)
        || lat < -90.0 || lat > 90.0 || lon < -180.0 || lon > 180.0
        || alt < -83886.0 || alt > 83886.0) return false;
    lastLatitude = lat;
    lastLongitude = lon;
    lastAltitude = alt;
    return true;
}

// Bounded, non-blocking input even during TX/RX; never wait for a GPS fix.
// NMEA continues arriving while LMIC waits for its receive windows.
static void pollGps() {
    for (unsigned i = 0; i < 256 && gpsSerial.available(); ++i) {
        int c = gpsSerial.read();
        if (c < 0) break;
        gps.encode((char)c);
        ++gpsBytesReceived;
        lastGpsByteMs = millis();
    }
    gpsReadingValid = readGpsFix();
#if ENABLE_OLED
    static uint32_t lastGpsDisplayMs = 0;
    if (millis() - lastGpsDisplayMs >= 1000) {
        lastGpsDisplayMs = millis();
        displayDirty = true;
    }
#endif
    static uint32_t lastDiagnosticMs = 0;
    if (!(LMIC.opmode & OP_TXRXPEND) && millis() - lastDiagnosticMs >= 10000) {
        lastDiagnosticMs = millis();
        if (!gpsBytesReceived || millis() - lastGpsByteMs > GPS_MAX_AGE_MS) {
            Serial.println(F("[GPS] NO UART DATA: check power, common GND, GPS TX -> GPIO34 and baud rate."));
        } else if (!gps.passedChecksum()) {
            Serial.println(F("[GPS] UART bytes but no valid NMEA: check baud rate and NMEA output configuration."));
        } else {
            Serial.printf("[GPS] %s | satellites %lu | HDOP %.2f | checksum failures %lu\n",
                          gpsReadingValid ? "FRESH FIX" : "WAITING FOR FIX (GGA altitude required)",
                          (unsigned long)(gps.satellites.isValid() ? gps.satellites.value() : 0),
                          gps.hdop.isValid() ? gps.hdop.hdop() : -1.0,
                          (unsigned long)gps.failedChecksum());
        }
    }
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
        display.println(F("SBMS GPS Node"));
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
    display.println(strlen(nickname) ? nickname : "SBMS GPS Node");
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
    if (gpsReadingValid) {
        display.print(F("Lat "));
        display.print(lastLatitude, 4);
        display.setCursor(0, 57);
        display.print(F("Lon "));
        display.print(lastLongitude, 4);
    } else {
        display.println(F("WAITING FOR GPS FIX"));
        display.setCursor(0, 57);
        if (!gpsBytesReceived || millis() - lastGpsByteMs > GPS_MAX_AGE_MS) {
            display.print(F("NO UART DATA"));
        } else {
            display.print(F("Sats "));
            display.print(gps.satellites.isValid() ? gps.satellites.value() : 0);
            display.print(F(" - try outdoors"));
        }
    }

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

    gpsReadingValid = readGpsFix();
    if (!gpsReadingValid) {
        // No EV_TXCOMPLETE occurs on a skipped send: schedule our own retry.
        os_setTimedCallback(&sendjob, os_getTime() + sec2osticks(GPS_RETRY_SECONDS), do_send);
        return;
    }

    lpp.reset();
    if (!lpp.addGPS(1, lastLatitude, lastLongitude, lastAltitude)) {
        Serial.println(F("[GPS] Could not encode GPS payload; uplink skipped."));
        os_setTimedCallback(&sendjob, os_getTime() + sec2osticks(GPS_RETRY_SECONDS), do_send);
        return;
    }
#if ENABLE_OLED
    lastPayloadLen = lpp.getSize();
    displayDirty = true;
#endif
    Serial.printf("[TX] GPS | lat %.6f | lon %.6f | altitude %.2f m | %u bytes\n",
                  lastLatitude, lastLongitude, lastAltitude, (unsigned)lpp.getSize());

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
#if BENCH_RADIO_TEST
            LMIC_setAdrMode(0);
            LMIC_setDrTxpow(DR_SF7, 14);
            Serial.println(F("[BENCH] ADR off, data rate pinned to SF7 for short-range testing."));
#else
            LMIC_setAdrMode(1);
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
            Serial.println(F("EV_TXCOMPLETE (TX/RX cycle complete; unconfirmed uplink)"));
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
    Serial.println(F("  Smart Building Management System - GPS LoRaWAN Node"));
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
        display.println(F("SBMS GPS Node"));
        display.println();
        display.println(F("Starting up..."));
        display.display();
    }
#endif

    // Bluetooth is never used. Wi-Fi is left alone for now: it is needed if
    // this board still has to be named, and is switched off immediately after.
    btStop();

    pinMode(PIN_RESET_NICKNAME, INPUT_PULLUP);

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

    gpsSerial.setRxBufferSize(1024);
#if ESP_ARDUINO_VERSION >= ESP_ARDUINO_VERSION_VAL(3, 3, 11)
    // IO34 has no internal pull resistor. Let the GPS TX drive the input.
    gpsSerial.enableRxInternalPull(false);
#endif
    gpsSerial.begin(GPS_BAUD, SERIAL_8N1, GPS_RX_PIN, GPS_TX_PIN);
    Serial.println(F("[GPS] NEO-6M UART2 9600 8N1: GPS TX -> GPIO34, leave GPS RX disconnected (optional GPIO12)."));

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
    LMIC_startJoining(); // Join independently of whether a GPS fix exists.
    do_send(&sendjob);
}

void loop() {
    os_runloop_once();
    pollGps();
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
