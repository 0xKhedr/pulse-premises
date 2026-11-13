/*
 * ==============================================================================
 * Smart Building Management System - Real I2C Temperature/Humidity Node
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
 *  - "arduino-sht" by Sensirion (SHT2x, SHT3x, SHT4x)
 *  - "Adafruit AHTX0" (AHT10/AHT20)
 *  - "Adafruit BME280 Library" (BME280; BMP280 has no humidity sensor)
 *  - "Adafruit Unified Sensor" and "Adafruit BusIO" (dependencies)
 *
 * Connect sensor SDA to GPIO21, SCL to GPIO22, and GND to GND.
 * The sensor shares the OLED's I2C bus. Its family is detected at startup;
 * Serial Monitor at 115200 baud lists bus addresses and the detected family.
 * Only measured temperature (LPP channel 1) and humidity (channel 2) are sent.
 * A missing, unsupported, or failing sensor skips the data uplink and retries
 * after the configured send interval. No simulated readings are ever sent.
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

#include <math.h>
#include <SHTSensor.h>
#include <Adafruit_AHTX0.h>
#include <Adafruit_BME280.h>

// An address alone does not identify a sensor: each driver validates it.
enum class EnvironmentalSensor { NONE, SHT, AHT, BME280 };
static EnvironmentalSensor environmentalSensor = EnvironmentalSensor::NONE;
static SHTSensor sht4x(SHTSensor::SHT4X);
static SHTSensor sht3x(SHTSensor::SHT3X);
static SHTSensor sht3xAlt(SHTSensor::SHT3X_ALT);
static SHTSensor sht2x(SHTSensor::SHT2X);
static SHTSensor* activeSht = nullptr;
static Adafruit_AHTX0 aht;
static Adafruit_BME280 bme;
static const char* sensorName = "NOT DETECTED";
static uint8_t sensorAddress = 0;
static bool environmentalReadingValid = false;

// ==============================================================================
// 1. LORAWAN OTAA CREDENTIALS
// ==============================================================================

#if __has_include("lorawan_credentials.h")
#include "lorawan_credentials.h"
#else
#error "Missing firmware/lorawan_credentials.h. Run: python tools/generate_lorawan_credentials.py"
#endif

// Defaults from the local header, optionally overridden by the setup portal.
// EUIs use LMIC's little-endian order; AppKey uses the displayed byte order.
struct OtaaCredentials {
    u1_t joinEui[8];
    u1_t appKey[16];
    u1_t devEui[8];
};
static OtaaCredentials otaaCredentials = {
    { SBMS_JOIN_EUI_LSB_BYTES }, { SBMS_APP_KEY_BYTES }, {}
};
void os_getArtEui (u1_t* buf) { memcpy(buf, otaaCredentials.joinEui, 8); }
void os_getDevKey (u1_t* buf) { memcpy(buf, otaaCredentials.appKey, 16); }

// DevEUI is generated dynamically from the ESP32 chip's unique MAC address!
// (Little-Endian format required by LMIC)
static u1_t (&DEVEUI)[8] = otaaCredentials.devEui;
static const u1_t PROGMEM DEV_EUI_XOR[8] = { SBMS_DEV_EUI_XOR_LSB_BYTES };
void os_getDevEui (u1_t* buf) { memcpy(buf, DEVEUI, 8); }

static void loadOtaaCredentials() {
    Preferences config;
    if (!config.begin("sbms", /*readOnly=*/true)) return;
    OtaaCredentials stored;
    if (config.getBytesLength("otaa_creds") == sizeof(stored)
        && config.getBytes("otaa_creds", &stored, sizeof(stored)) == sizeof(stored)) {
        otaaCredentials = stored;
    }
    config.end();
}

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
#include "SendRateConfig.h"
// Restored from flash at boot; updated by a configuration downlink on FPort 42.
static uint32_t txIntervalSeconds = SendRateConfig::DefaultSeconds;
static osjob_t sendjob;
CayenneLPP lpp(51);

// Keep the original sketch's short-range SF7 radio setting for bench testing.
// Set to 0 for deployment so adaptive data rate can select the radio settings.
#define BENCH_RADIO_TEST 1

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

// Press RST during this startup window to forget the nickname on the next
// boot. The marker lives in flash because hardware reset can clear RTC RAM.
// A power interruption during the window has the same effect as pressing RST.
#define RESET_WINDOW_MS 3000

Preferences preferences;
static char nickname[NICKNAME_MAX_LENGTH + 1] = "";
static bool nicknameUplinkPending = false;
static uint16_t uplinksSinceNickname = 0;

#include "DownlinkObserver.h"
static DownlinkObserver downlinkObserver;
static bool downlinkLogPending = false;

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
#define SBMS_I2C_SDA  21
#define SBMS_I2C_SCL  22
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
static float    lastTemp       = NAN;
static float    lastHum        = NAN;
static bool     displayDirty   = true;
static bool     observerJoined = false;
static size_t   observerPage = 0;
static unsigned long lastObserverPageAt = 0;
#endif

// Class A application data is available when EV_TXCOMPLETE closes RX1/RX2.
// ACK-only/MAC-only frames have no application port and are not messages.
static void captureApplicationDownlink() {
    if (!(LMIC.txrxFlags & TXRX_PORT) || LMIC.dataBeg == 0
        || size_t(LMIC.dataBeg) + LMIC.dataLen > sizeof(LMIC.frame)) return;
    if (!downlinkObserver.capture(LMIC.frame[LMIC.dataBeg - 1],
                                  LMIC.frame + LMIC.dataBeg, LMIC.dataLen)) return;
    downlinkLogPending = true;
#if ENABLE_OLED
    observerPage = 0;
    lastObserverPageAt = millis();
    displayDirty = true;
#endif
}

static void logObservedDownlink() {
    if (!downlinkLogPending || (LMIC.opmode & OP_TXRXPEND)) return;
    downlinkLogPending = false;
    Serial.printf("[DOWNLINK] #%lu FPort %u, %u bytes\nHEX: ",
                  (unsigned long)downlinkObserver.count, downlinkObserver.port,
                  (unsigned)downlinkObserver.length);
    for (size_t i = 0; i < downlinkObserver.length; ++i) {
        Serial.printf("%02X ", downlinkObserver.bytes[i]);
    }
    Serial.print(F("\nTEXT: "));
    for (size_t i = 0; i < downlinkObserver.length; ++i) {
        uint8_t value = downlinkObserver.bytes[i];
        Serial.print(value >= 32 && value <= 126 ? char(value) : '.');
    }
    Serial.println();
}

// Keep each LMIC timer below the signed tick rollover limit, even at 24 hours.
// A single sec2osticks(86400) can overflow a 32-bit ostime_t.
void do_send(osjob_t* j);
static uint32_t sendWaitRemainingSeconds = 0;

static void waitForNextUplink(osjob_t* job) {
    if (!sendWaitRemainingSeconds) {
        do_send(job);
        return;
    }
    const uint32_t chunk = sendWaitRemainingSeconds > 3600 ? 3600 : sendWaitRemainingSeconds;
    sendWaitRemainingSeconds -= chunk;
    os_setTimedCallback(&sendjob, os_getTime() + sec2osticks(chunk), waitForNextUplink);
}

static void scheduleNextUplink() {
    sendWaitRemainingSeconds = txIntervalSeconds;
    waitForNextUplink(&sendjob);
}

static void loadSendRate() {
    Preferences config;
    if (config.begin("sbms", /*readOnly=*/true)) {
        const uint32_t stored = config.getUInt("send_interval", SendRateConfig::DefaultSeconds);
        config.end();
        if (SendRateConfig::valid(stored)) txIntervalSeconds = stored;
    }
    Serial.printf("[CONFIG] Send interval: %lu seconds\n", (unsigned long)txIntervalSeconds);
}

// Process the observer's owned copy outside the radio callback. No flash writes
// or logging can block LMIC while a transmission/receive window is pending.
static void applyObservedConfiguration() {
    static uint32_t processedCount = 0;
    if ((LMIC.opmode & OP_TXRXPEND) || processedCount == downlinkObserver.count) return;
    processedCount = downlinkObserver.count;
    if (downlinkObserver.port != SendRateConfig::Port) return;
    uint32_t seconds = 0;
    if (!SendRateConfig::decode(downlinkObserver.port, downlinkObserver.bytes,
                               downlinkObserver.length, seconds)) {
        Serial.println(F("[CONFIG] Invalid send-rate command ignored."));
        return;
    }
    // Repeated delivery is idempotent and does not wear flash or reset timing.
    if (seconds == txIntervalSeconds) return;
    Preferences config;
    if (!config.begin("sbms", /*readOnly=*/false)) {
        Serial.println(F("[CONFIG] Cannot open flash; send interval unchanged."));
        return;
    }
    const bool saved = config.putUInt("send_interval", seconds) == sizeof(uint32_t);
    config.end();
    if (!saved) {
        Serial.println(F("[CONFIG] Flash write failed; send interval unchanged."));
        return;
    }
    txIntervalSeconds = seconds;
    scheduleNextUplink();
    Serial.printf("[CONFIG] Send interval saved: %lu seconds\n", (unsigned long)seconds);
}

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

    // Load after deriving the default DevEUI, before displaying it or naming
    // the access point. A nickname reset never removes these credentials.
    loadOtaaCredentials();

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

static bool i2cAddressResponds(uint8_t address) {
    Wire.beginTransmission(address);
    return Wire.endTransmission() == 0;
}

static void scanI2CBus() {
    Serial.println(F("[I2C] Scanning SDA=21, SCL=22:"));
    unsigned found = 0;
    for (uint8_t address = 1; address < 127; ++address) {
        if (!i2cAddressResponds(address)) continue;
        ++found;
        Serial.printf("[I2C] Found 0x%02X%s\n", address,
                      address == OLED_ADDRESS ? " (onboard OLED)" : "");
    }
    if (!found) Serial.println(F("[I2C] No devices found. Check power and wiring."));
}

static bool detectEnvironmentalSensor() {
    environmentalSensor = EnvironmentalSensor::NONE;
    activeSht = nullptr;
    sensorAddress = 0;
    sensorName = "NOT DETECTED";

    // SHT4x and SHT3x share 0x44. Sensirion recommends probing SHT4x first.
    // init() must produce a valid, CRC-checked sample before we select it.
    if (i2cAddressResponds(0x44) && sht4x.init(Wire)) {
        activeSht = &sht4x;
        sensorName = "SHT4x";
        sensorAddress = 0x44;
    } else if (i2cAddressResponds(0x44) && sht3x.init(Wire)) {
        activeSht = &sht3x;
        sensorName = "SHT30/31/35";
        sensorAddress = 0x44;
    } else if (i2cAddressResponds(0x45) && sht3xAlt.init(Wire)) {
        activeSht = &sht3xAlt;
        sensorName = "SHT30/31/35";
        sensorAddress = 0x45;
    } else if (i2cAddressResponds(0x40) && sht2x.init(Wire)) {
        activeSht = &sht2x;
        sensorName = "SHT20/21/25";
        sensorAddress = 0x40;
    }
    if (activeSht) {
        environmentalSensor = EnvironmentalSensor::SHT;
    } else {
        const uint8_t ahtAddresses[] = {0x38, 0x39};
        for (uint8_t address : ahtAddresses) {
            if (i2cAddressResponds(address) && aht.begin(&Wire, 0, address)) {
                environmentalSensor = EnvironmentalSensor::AHT;
                sensorName = "AHT10/20";
                sensorAddress = address;
                break;
            }
        }
        if (environmentalSensor == EnvironmentalSensor::NONE) {
            const uint8_t bmeAddresses[] = {0x76, 0x77};
            for (uint8_t address : bmeAddresses) {
                // begin() checks BME280's chip ID, excluding BMP280.
                if (i2cAddressResponds(address) && bme.begin(address, &Wire)) {
                    environmentalSensor = EnvironmentalSensor::BME280;
                    sensorName = "BME280";
                    sensorAddress = address;
                    break;
                }
            }
        }
    }
    if (environmentalSensor == EnvironmentalSensor::NONE) {
        Serial.println(F("[SENSOR] No supported temperature/humidity sensor detected."));
        Serial.println(F("[SENSOR] Supported: SHT2x/3x/4x, AHT10/20, BME280."));
        return false;
    }
    Serial.printf("[SENSOR] Detected %s at 0x%02X\n", sensorName, sensorAddress);
    return true;
}

static bool readEnvironmentalSensor(float& temperature, float& humidity) {
    temperature = NAN;
    humidity = NAN;
    if (environmentalSensor == EnvironmentalSensor::NONE && !detectEnvironmentalSensor()) {
        return false;
    }
    bool success = i2cAddressResponds(sensorAddress);
    if (success) {
        switch (environmentalSensor) {
            case EnvironmentalSensor::SHT:
                success = activeSht->readSample();
                if (success) {
                    temperature = activeSht->getTemperature();
                    humidity = activeSht->getHumidity();
                }
                break;
            case EnvironmentalSensor::AHT: {
                sensors_event_t humidityEvent = {}, temperatureEvent = {};
                success = aht.getEvent(&humidityEvent, &temperatureEvent);
                if (success) {
                    temperature = temperatureEvent.temperature;
                    humidity = humidityEvent.relative_humidity;
                }
                break;
            }
            case EnvironmentalSensor::BME280:
                temperature = bme.readTemperature();
                humidity = bme.readHumidity();
                break;
            default:
                success = false;
                break;
        }
    }
    success = success && isfinite(temperature) && isfinite(humidity)
              && temperature >= -45.0f && temperature <= 130.0f
              && humidity >= 0.0f && humidity <= 100.0f;
    if (!success) {
        temperature = NAN;
        humidity = NAN;
        // Retry initialization at the next scheduled attempt after a failure.
        environmentalSensor = EnvironmentalSensor::NONE;
    }
    return success;
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
<title>Set up this device</title><style>
body{font-family:system-ui,sans-serif;margin:0;padding:24px;background:#0f172a;color:#e2e8f0}
h1{font-size:20px;margin:0 0 4px}p{color:#94a3b8;font-size:14px;margin:0 0 20px}
label{display:block;font-size:13px;margin-bottom:6px;color:#cbd5e1}
label:not(:first-child){margin-top:16px}
input{width:100%;box-sizing:border-box;padding:14px;font-size:17px;border-radius:8px;
border:1px solid #334155;background:#1e293b;color:#f1f5f9}
button{width:100%;margin-top:16px;padding:16px;font-size:17px;font-weight:600;
border:0;border-radius:8px;background:#2563eb;color:#fff}
button:disabled{background:#475569}
.eui{margin-top:20px;font-size:12px;color:#64748b}
.err{color:#f87171;font-size:13px;margin-top:8px;min-height:18px}
</style></head><body>
<h1>Set up this device</h1>
<p>It will not join the network until you confirm.</p>
<form method="POST" action="/save" onsubmit="return check()">
<label for="n">Nickname</label>
<input id="n" name="nickname" autocomplete="off" autocapitalize="none"
       maxlength="48" placeholder="temp_c_floor3_308" required>
<p style="margin:20px 0 12px">Optional LoRaWAN credentials. Leave any field blank
to keep its current value. Enter hexadecimal values in the order shown by your network server.</p>
<label for="d">DevEUI (optional, 16 hex characters)</label>
<input id="d" name="dev_eui" autocomplete="off" autocapitalize="none" spellcheck="false"
       maxlength="16" pattern="[0-9A-Fa-f]{16}" placeholder="Leave blank to keep current">
<label for="j">JoinEUI / AppEUI (optional, 16 hex characters)</label>
<input id="j" name="join_eui" autocomplete="off" autocapitalize="none" spellcheck="false"
       maxlength="16" pattern="[0-9A-Fa-f]{16}" placeholder="Leave blank to keep current">
<label for="k">AppKey (optional, 32 hex characters)</label>
<input id="k" name="app_key" type="password" autocomplete="new-password" autocapitalize="none"
       spellcheck="false" maxlength="32" pattern="[0-9A-Fa-f]{32}"
       placeholder="Leave blank to keep current">
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

static int credentialHexDigit(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

// Missing, empty, or whitespace-only fields leave the candidate unchanged.
// Parse into a copy: invalid input must never change active or saved values.
static bool parseOptionalCredential(String text, u1_t* output, size_t length, bool reverse) {
    text.trim();
    if (!text.length()) return true;
    if (text.length() != length * 2) return false;
    for (size_t i = 0; i < length; ++i) {
        const int high = credentialHexDigit(text[i * 2]);
        const int low = credentialHexDigit(text[i * 2 + 1]);
        if (high < 0 || low < 0) return false;
        output[reverse ? length - 1 - i : i] = (high << 4) | low;
    }
    return true;
}

static bool saveProvisionCredentials() {
    OtaaCredentials candidate = otaaCredentials;
    if (!parseOptionalCredential(provisioningServer.arg("dev_eui"), candidate.devEui, 8, true)
        || !parseOptionalCredential(provisioningServer.arg("join_eui"), candidate.joinEui, 8, true)
        || !parseOptionalCredential(provisioningServer.arg("app_key"), candidate.appKey, 16, false)) {
        provisioningServer.send(400, "text/plain",
            "Invalid credentials. DevEUI and JoinEUI need 16 hex characters; AppKey needs 32. "
            "Leave fields blank to keep their current values. Go back and try again.");
        return false;
    }
    if (memcmp(&candidate, &otaaCredentials, sizeof(candidate)) == 0) return true;

    Preferences config;
    if (!config.begin("sbms", /*readOnly=*/false)) {
        provisioningServer.send(500, "text/plain", "Cannot open credential storage. Go back and try again.");
        return false;
    }
    // One NVS blob keeps a multi-field update together. Do not apply it to
    // LMIC until the complete candidate has been persisted successfully.
    const bool saved = config.putBytes("otaa_creds", &candidate, sizeof(candidate)) == sizeof(candidate);
    config.end();
    if (!saved) {
        provisioningServer.send(500, "text/plain", "Cannot save credentials. Go back and try again.");
        return false;
    }
    otaaCredentials = candidate;
#if ENABLE_OLED
    for (int i = 0; i < 8; ++i) snprintf(&devEuiText[i * 2], 3, "%02X", DEVEUI[7 - i]);
    displayDirty = true;
#endif
    return true;
}

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

    if (!saveProvisionCredentials()) return;
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


// Arm a persistent marker for three seconds. If reset interrupts the wait,
// the next boot consumes the marker and reopens the existing naming portal.
// This runs before LMIC starts; the normal radio loop is never suspended.
static bool renameRequested() {
    Preferences resetPreferences;
    if (!resetPreferences.begin("sbms", /*readOnly=*/false)) {
        Serial.println(F("[ERROR] Cannot open reset-window storage."));
        return false;
    }

    const bool interrupted = resetPreferences.getBool("rename_pending", false);
    if (resetPreferences.putBool("rename_pending", !interrupted) != 1) {
        Serial.println(F("[ERROR] Cannot update reset-window marker."));
        resetPreferences.end();
        return false;
    }
    if (interrupted) {
        resetPreferences.end();
        return true;
    }

    Serial.printf("[BOOT] Press RST within %lu seconds to rename.\n",
                  static_cast<unsigned long>(RESET_WINDOW_MS / 1000));

#if ENABLE_OLED
    if (oledReady) {
        display.clearDisplay();
        display.setCursor(0, 0);
        display.println(F("SBMS LoRaWAN Node"));
        display.setCursor(0, 24);
        display.println(F("Press RST within 3s"));
        display.println(F("to rename."));
        display.display();
    }
#endif

    delay(RESET_WINDOW_MS);
    if (resetPreferences.putBool("rename_pending", false) != 1) {
        Serial.println(F("[ERROR] Cannot clear reset-window marker."));
    }
    resetPreferences.end();
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

    if (observerJoined) {
        display.setTextWrap(false);
        display.setCursor(0, 0);
        display.print(F("DOWNLINK / CLASS A"));
        display.drawFastHLine(0, 9, OLED_WIDTH, SSD1306_WHITE);
        if (!downlinkObserver.count) {
            display.setCursor(0, 14);
            display.print(F("No downlink yet."));
            display.setCursor(0, 26);
            display.print(F("RX after each uplink"));
            display.setCursor(0, 38);
            display.print(F("TX #"));
            display.print(uplinkCount);
            display.setCursor(0, 52);
            display.print(nickname);
        } else {
            char line[DownlinkObserver::Columns + 1];
            display.setCursor(0, 12);
            snprintf(line, sizeof(line), "#%lu Port:%u", (unsigned long)downlinkObserver.count,
                     downlinkObserver.port);
            display.print(line);
            const bool textPage = observerPage < downlinkObserver.textPages();
            const size_t page = textPage ? observerPage : observerPage - downlinkObserver.textPages();
            display.setCursor(0, 22);
            snprintf(line, sizeof(line), "%uB %s %u/%u", (unsigned)downlinkObserver.length,
                     textPage ? "TEXT" : "HEX", (unsigned)page + 1,
                     (unsigned)(textPage ? downlinkObserver.textPages() : downlinkObserver.hexPages()));
            display.print(line);
            for (size_t row = 0; row < DownlinkObserver::Rows; ++row) {
                downlinkObserver.formatRow(observerPage, row, line);
                display.setCursor(0, 32 + row * 8);
                display.print(line);
            }
        }
        display.display();
        display.setTextWrap(true);
        return;
    }

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
    if (environmentalReadingValid) {
        display.print(lastTemp, 1);
        display.print(F("C "));
        display.print(lastHum, 1);
        display.print(F("%"));
    } else {
        display.print(F("SENSOR ERROR"));
    }
    display.setCursor(0, 57);
    display.print(sensorName);

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

    float temp = NAN, hum = NAN;
    environmentalReadingValid = readEnvironmentalSensor(temp, hum);
#if ENABLE_OLED
    lastTemp = temp;
    lastHum = hum;
    displayDirty = true;
#endif
    if (!environmentalReadingValid) {
        Serial.println(F("[SENSOR] Missing sensor or invalid reading; data uplink skipped."));
        scanI2CBus();
        // No TX means EV_TXCOMPLETE will not schedule the next attempt.
        scheduleNextUplink();
        return;
    }

    // Standard Cayenne LPP types for TTN's built-in formatter: seven bytes.
    // Send only channels supplied by the attached temperature/humidity sensor.
    lpp.reset();
    lpp.addTemperature(1, temp);
    lpp.addRelativeHumidity(2, hum);
#if ENABLE_OLED
    lastPayloadLen = lpp.getSize();
#endif
    Serial.printf("[TX] %s at 0x%02X | T %.1f C | RH %.1f %% | %u bytes\n",
                  sensorName, sensorAddress, temp, hum, (unsigned)lpp.getSize());

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
            observerJoined = false;
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
            observerJoined = true;
            joinState = "JOINED";
            displayDirty = true;
#endif
            break;
        case EV_JOIN_FAILED:
            Serial.println(F("EV_JOIN_FAILED. New node? Open discovery and accept it in the web portal. Also check credentials / gateway signal."));
#if ENABLE_OLED
            joinState = "JOIN FAIL";
            displayDirty = true;
#endif
            break;
        case EV_JOIN_TXCOMPLETE:
            // No response cannot distinguish pending approval from RF loss or
            // incorrect credentials. Let LMIC keep its normal join retry timing.
            Serial.println(F("EV_JOIN_TXCOMPLETE: no JoinAccept yet. New node? Accept it in the web portal. OTAA retries continue."));
#if ENABLE_OLED
            joinState = "WAITING JOIN";
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
            Serial.println(F("EV_TXCOMPLETE (Uplink and receive windows complete)"));
            captureApplicationDownlink();
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
            scheduleNextUplink();
            break;
        case EV_RESET:
            Serial.println(F("EV_RESET"));
#if ENABLE_OLED
            observerJoined = false;
            displayDirty = true;
#endif
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

    // Initialize I2C even with the OLED disabled; the sensor needs this bus.
    // display.begin() below must not reinitialize it.
    Wire.begin(SBMS_I2C_SDA, SBMS_I2C_SCL);
    Wire.setTimeOut(50);
#if ENABLE_OLED
    // Bring the screen up before anything else can fail, so a fatal error in
    // setup() is visible without a serial cable attached.
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

    // The DevEUI names the provisioning access point, so it has to exist
    // before the portal can start.
    if (!initUniqueDevEUI()) {
        while (true) {
            delay(1000);
        }
    }

    loadSendRate();
    loadNickname();
    if (strlen(nickname) && renameRequested()) {
        Serial.println(F("[SETUP] Startup reset detected: forgetting nickname."));
        saveNickname("-");
    }

    // An unnamed board stops here. It does not join, so it never reaches the
    // cloud as an anonymous device and never consumes an enrollment slot --
    // naming it is what commissions it.
    if (!strlen(nickname)) {
        runProvisioningPortal();
    }

    // Probe sensors after the reset window and naming portal so slow or
    // missing hardware cannot postpone the rename prompt.
    scanI2CBus();
    float initialTemp = NAN, initialHum = NAN;
    environmentalReadingValid = readEnvironmentalSensor(initialTemp, initialHum);
#if ENABLE_OLED
    lastTemp = initialTemp;
    lastHum = initialHum;
    displayDirty = true;
#endif

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
    applyObservedConfiguration();
    pollSerialConsole();
    logObservedDownlink();

#if ENABLE_OLED
    if (observerJoined && downlinkObserver.count && (millis() - lastObserverPageAt) >= 4000) {
        observerPage = (observerPage + 1) % downlinkObserver.pageCount();
        lastObserverPageAt = millis();
        displayDirty = true;
    }
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
