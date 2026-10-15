# LilyGO temperature/humidity node

Open `lilygo_environment_lpp_node.ino` in Arduino IDE. Select the same ESP32
board and LoRa32 revision used for `lilygo_cayenne_lpp_node` (TTGO LoRa32-OLED
V2.1 / 1.6.1). This is a separate sketch; the original dummy sketch is retained.

In Library Manager install:

- **arduino-sht** by Sensirion
- **Adafruit AHTX0**
- **Adafruit BME280 Library**

Accept dependency installation for **Adafruit Unified Sensor** and
**Adafruit BusIO**. The original **MCCI LoRaWAN LMIC library**, **CayenneLPP**,
**Adafruit SSD1306**, and **Adafruit GFX Library** are also required.

The sensor shares the onboard OLED's I2C bus: SDA **GPIO21**, SCL **GPIO22**,
and common GND. Use a power supply suitable for the sensor module and
ESP32-compatible 3.3 V I2C signal levels.

The sketch probes these supported families using their drivers:

| Sensor family | I2C addresses |
| --- | --- |
| SHT20 / SHT21 / SHT25 | `0x40` |
| SHT30 / SHT31 / SHT35 | `0x44`, `0x45` |
| SHT4x | `0x44` |
| AHT10 / AHT20 | `0x38`, `0x39` |
| BME280 | `0x76`, `0x77` |

An address alone cannot identify the sensor. The OLED at `0x3C` is excluded
from sensor detection. BMP280 has no humidity sensor and is not accepted.
If several supported sensors are attached, the first successfully detected
family in the sketch's probe order is used.

Open Serial Monitor at **115200 baud** to see the I2C scan, detected family,
address, and measured values. If detection fails, the OLED shows `SENSOR ERROR`
and Serial Monitor lists the detected addresses. No simulated data is sent;
the sketch retries after 30 seconds. Unknown sensor types need another driver.

Temperature uses Cayenne LPP channel **1** and humidity channel **2**, on port
**1**. The seven-byte payload works with TTN's built-in Cayenne LPP formatter.
Other channels from the dummy sketch are omitted because their sensors are
not connected. Existing historical dummy readings in the cloud are unaffected.

The original DevEUI derivation, OTAA credentials, nickname storage, naming
portal, BOOT-button rename flow, and nickname uplinks on port **10** are kept.
Generate credentials with `python tools/generate_lorawan_credentials.py`;
the new sketch folder gets its own gitignored header. A local copy of the
original sketch's credential header can also be used with this sketch.

The 30-second interval and fixed SF7 radio setting match the original bench
sketch. For deployment, increase `TX_INTERVAL_SECONDS` and set
`BENCH_RADIO_TEST` to `0` to enable adaptive data rate.
