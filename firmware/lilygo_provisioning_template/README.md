# Sensor-independent provisioning sketch

Open `lilygo_provisioning_template.ino` as its own sketch in Arduino IDE and
select the same LilyGO TTGO LoRa32-OLED V2.1 (1.6.1) board as the existing node.
The local sketch has the existing JoinEUI, AppKey, and application namespace
mask embedded directly. Its DevEUI is still derived separately for each board.
The credential-bearing `.ino` is gitignored.

Required libraries: **MCCI LoRaWAN LMIC library**, **Adafruit SSD1306**, and
**Adafruit GFX Library**, with **Adafruit BusIO** when prompted. Keep the
existing LMIC EU868/SX1276 configuration. Sensor and encoder libraries are
only needed when you add your own code.

The sketch retains the Wi-Fi naming portal, generated access-point password,
nickname persistence, BOOT-button rename, serial nickname commands, OTAA
join, OLED status, and nickname announcements on LoRaWAN port 10.

Search for `USER CODE START` to find the five insertion points:

| Section | Put your code here |
| --- | --- |
| `INCLUDES` | Sensor and payload-encoder library includes |
| `GLOBALS` | Sensor objects, pins, cached values, and application variables |
| `SETUP` | Initialize hardware once, after naming and before OTAA starts |
| `LOOP` | Short, non-blocking application or sensor work while radio is idle |
| `PAYLOAD` | Encode your readings into the supplied byte buffer |

`userBuildPayload(payload, capacity, length, port)` starts with `length = 0`
and `port = 1`. Write at most `capacity` bytes, set `length`, and return `true`
to send. Return `false` when no valid data is available. Allowed data ports
are 1 through 223, excluding 10, which is reserved for the nickname.

The framework schedules the next attempt and transmits the buffer. It skips
empty payloads, rejected lengths/ports, and unavailable readings. As supplied,
the template joins and sends the nickname but sends no sensor data.

I2C is already initialized on SDA 21 / SCL 22 and shared with the OLED. Avoid
reinitializing `Wire` and using the LoRa pins or BOOT GPIO0 for sensors. Keep
`userLoop()` and `userBuildPayload()` short so LMIC can service radio timing;
cache readings or use an asynchronous sensor driver when necessary.

The payload hook contains a commented Cayenne LPP example with real-reading
variable names. Add its library include if you use it. Port 1 plus the same
Cayenne LPP channels works with the existing formatter/profile. For another
encoding, configure the TTN formatter and cloud profile to match your bytes.

The 30-second send interval and fixed SF7 match the original bench sketch.
For deployment, increase `TX_INTERVAL_SECONDS` and set `BENCH_RADIO_TEST = 0`
(the macro is a `#define`) to enable adaptive data rate.
