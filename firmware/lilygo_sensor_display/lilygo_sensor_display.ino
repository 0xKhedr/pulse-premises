/*
 * LilyGO TTGO LoRa32-OLED V2.1 (1.6.1) - local temperature/humidity display.
 *
 * Standalone sketch derived from lilygo_environment_lpp_node's sensor code.
 * Displays actual sensor readings on the onboard OLED and Serial Monitor.
 * No Wi-Fi, LoRaWAN, network setup, nickname, or credentials are used.
 *
 * Wiring: sensor SDA -> GPIO21, SCL -> GPIO22, GND -> common GND.
 * The sensor shares the OLED's I2C bus. Use ESP32-compatible I2C levels.
 * Supported: SHT20/21/25, SHT30/31/35, SHT4x, AHT10/20, and BME280.
 * Unsupported or missing sensors are reported, never replaced with dummy data.
 *
 * Arduino IDE Library Manager (already used by the environment sketch):
 * - arduino-sht by Sensirion
 * - Adafruit AHTX0
 * - Adafruit BME280 Library
 * - Adafruit SSD1306 and Adafruit GFX Library
 * - Adafruit Unified Sensor and Adafruit BusIO (dependencies)
 *
 * Open this file in its own sketch folder. Select your usual TTGO LoRa32 V2.1
 * board. Serial Monitor: 115200 baud. No LMIC or CayenneLPP library required.
 */

#include <Arduino.h>
#include <Wire.h>
#include <math.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <SHTSensor.h>
#include <Adafruit_AHTX0.h>
#include <Adafruit_BME280.h>

constexpr int SBMS_I2C_SDA = 21;
constexpr int SBMS_I2C_SCL = 22;
constexpr int OLED_WIDTH = 128;
constexpr int OLED_HEIGHT = 64;
constexpr uint8_t OLED_ADDRESS = 0x3C;
constexpr int OLED_RESET = -1;

// Adjust these intervals to change how often readings/retries occur.
constexpr unsigned long READING_INTERVAL_MS = 1000;
constexpr unsigned long SENSOR_RETRY_INTERVAL_MS = 5000;

Adafruit_SSD1306 display(OLED_WIDTH, OLED_HEIGHT, &Wire, OLED_RESET);
static bool oledReady = false;
static float lastTemperature = NAN;
static float lastHumidity = NAN;
static unsigned long lastReadingAt = 0;

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

static void renderReadings() {
    if (!oledReady) return;
    display.clearDisplay();
    display.setTextColor(SSD1306_WHITE);
    display.setTextSize(1);
    display.setCursor(0, 0);
    display.println(F("TEMP & HUMIDITY"));
    display.setCursor(0, 12);
    display.print(sensorName);
    if (sensorAddress) {
        display.print(F(" 0x"));
        display.print(sensorAddress, HEX);
    }
    display.drawFastHLine(0, 22, OLED_WIDTH, SSD1306_WHITE);

    if (environmentalReadingValid) {
        display.setTextSize(2);
        display.setCursor(0, 26);
        display.print(F("T: "));
        display.print(lastTemperature, 1);
        display.print(F("C"));
        display.setCursor(0, 46);
        display.print(F("H: "));
        display.print(lastHumidity, 1);
        display.print(F("%"));
    } else {
        display.setCursor(0, 28);
        display.println(sensorAddress ? F("Sensor reading failed") : F("Sensor not detected"));
        display.setCursor(0, 40);
        display.println(F("Check power / wiring"));
        display.setCursor(0, 52);
        display.println(F("Retrying in 5 sec..."));
    }
    display.display();
}

static void updateReadings() {
    environmentalReadingValid = readEnvironmentalSensor(lastTemperature, lastHumidity);
    // Timestamp after sampling, so driver delays do not shorten the interval.
    lastReadingAt = millis();
    if (environmentalReadingValid) {
        Serial.printf("[READING] %s at 0x%02X | Temperature: %.1f C | Humidity: %.1f %%\n",
                      sensorName, sensorAddress, lastTemperature, lastHumidity);
    } else {
        Serial.println(F("[SENSOR] No valid reading. Check I2C addresses below; retrying in 5 seconds."));
        scanI2CBus();
    }
    renderReadings();
}

void setup() {
    Serial.begin(115200);
    delay(200);
    Serial.println(F("\nLilyGO - Local temperature/humidity display"));

    // Bring up I2C once for both the sensor and onboard OLED.
    Wire.begin(SBMS_I2C_SDA, SBMS_I2C_SCL);
    Wire.setTimeOut(50);
    oledReady = display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDRESS,
                              /*reset=*/true, /*periphBegin=*/false);
    if (oledReady) {
        display.clearDisplay();
        display.setTextSize(1);
        display.setTextColor(SSD1306_WHITE);
        display.setCursor(0, 0);
        display.println(F("TEMP & HUMIDITY"));
        display.setCursor(0, 24);
        display.println(F("Detecting sensor..."));
        display.display();
    } else {
        Serial.println(F("[WARN] OLED not available; readings will still appear in Serial Monitor."));
    }

    scanI2CBus();
    updateReadings();
}

void loop() {
    const unsigned long interval = environmentalReadingValid
                                    ? READING_INTERVAL_MS : SENSOR_RETRY_INTERVAL_MS;
    if (millis() - lastReadingAt >= interval) updateReadings();
    delay(10);
}
