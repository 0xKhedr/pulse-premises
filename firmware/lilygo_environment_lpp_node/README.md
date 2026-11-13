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
the sketch retries after the configured send interval (30 seconds by default).
Unknown sensor types need another driver.

Temperature uses Cayenne LPP channel **1** and humidity channel **2**, on port
**1**. The seven-byte payload works with TTN's built-in Cayenne LPP formatter.
Other channels from the dummy sketch are omitted because their sensors are
not connected. Existing historical dummy readings in the cloud are unaffected.

The original DevEUI derivation, OTAA credentials, nickname storage, naming
portal, and nickname uplinks on port **10** are kept.
Generate credentials with `python tools/generate_lorawan_credentials.py`;
the new sketch folder gets its own gitignored header. A local copy of the
original sketch's credential header can also be used with this sketch.

The Wi-Fi setup page at **192.168.4.1** also has optional **DevEUI**,
**JoinEUI / AppEUI**, and **AppKey** fields. Leave any field blank to keep
its current value (the saved value, or the firmware default if no credentials
have been saved). You can update one field without re-entering the others.
Enter DevEUI and JoinEUI as **16 hexadecimal characters**, and AppKey as
**32 hexadecimal characters**, without spaces or separators, in the normal
order displayed by your network server. The firmware converts the EUIs to
LMIC's byte order automatically. Invalid input rejects the submission before
saving the nickname or credentials. Credential fields are never prefilled.

Credential changes are stored together in ESP32 NVS (`sbms` / `otaa_creds`)
and used for the subsequent OTAA join. They survive restarts, nickname resets,
and ordinary reflashing without a flash erase. Once saved, they take precedence
over the credential header, including for fields retained from defaults.
Submitting all credential fields blank leaves credential storage untouched.
Ensure your network server has matching credentials for the device.

To rename an already named node, restart it and press **RST** again during
the **three-second window** shown on Serial and the OLED. On the next boot,
the stored marker clears the nickname and reopens the Wi-Fi naming portal.
Save the new name in the portal, or type `nick <name>` over Serial at 115200
baud; the node then continues into normal LoRaWAN operation. Letting the
window expire keeps the nickname. An unnamed node opens the portal directly.
The marker uses flash and is written twice per ordinary named startup; a
power interruption during the window also requests a rename.

For a new node, open **Scan for new devices** in the web portal, then power
on the board and save its nickname. The board's verified join request appears
as **Pending approval**. Select **Accept** to register it on the network;
LMIC retries OTAA automatically, so no power cycle is needed. Until a join is
accepted, the OLED shows `WAITING JOIN` after an unanswered request. This can
also mean missing coverage or incorrect credentials; it is not proof that a
gateway heard the request. After joining, the nickname uplink on port 10 lets
the portal confirm **Online**, even before the first sensor reading.

The approval gate is enforced by the backend and also works with the previous
firmware. Reflash this sketch to get the updated OLED/serial explanations.

## Class A downlink observer

After joining, the OLED switches to **DOWNLINK / CLASS A**. It initially shows
that no downlink has arrived. Each application downlink replaces the displayed
message, with its receive count, FPort, and byte length. TEXT and HEX pages
rotate every four seconds; long payloads use multiple pages rather than being
clipped. TEXT shows printable ASCII and dots for other bytes; HEX preserves
the full binary message. The same bytes are logged on Serial at 115200 baud.
The last message is retained in RAM until another downlink or a restart.

In the portal's **Device Management** or **Device Capabilities** page, open
**Send downlink**, choose the joined node, use FPort **1**, and enter a message
such as `Hello`. Leave the format as **UTF-8 text**, then select **Queue
downlink**. Hexadecimal and Base64 input are also supported, with a maximum
of 51 decoded bytes. These messages are displayed as bytes. Configuration
commands use the separate port described below.

The device remains **Class A**: LMIC receives during RX1/RX2 after an uplink.
Queueing a message does not transmit it immediately or prove receipt. With
the configured sensor interval, wait for the next successfully received
uplink and its receive windows; retries, duty-cycle limits, and gateway traffic
can delay delivery further. A missing sensor can stop sensor uplinks, so it
can also prevent new receive opportunities after the nickname announcement.
ACK-only and MAC-only downlinks do not replace the observed application message.
Optional confirmed downlinks request a LoRaWAN acknowledgement in a later
uplink; they do not make the radio listen continuously.

OLED rendering and Serial output run in `loop()`, outside the LMIC receive
callback. No Class B or Class C receive mode is enabled.

## Send-rate configuration

Flash this updated sketch, then open **Devices → Configuration** on the
desired device's card. Set **Send interval (seconds)** and select **Queue
send-rate change**. Accepted values are whole seconds from **30 to 86,400**
(24 hours). The page keeps the last queued value across reloads; it does not
claim that value is active on the device. Existing firmware must be reflashed
once to support this command. Other sketches currently only observe downlinks.

The downlink waits for RX1/RX2 after an uplink at the **old** rate. On receipt,
the firmware validates it, saves it to ESP32 NVS (`sbms` / `send_interval`),
and schedules the next uplink using the new interval. It also uses this rate
for missing/invalid sensor retries and nickname uplinks. The value survives
restart, nickname reset, and ordinary reflashing without flash erase. The
default is 30 seconds when no valid saved value exists. Serial at 115200 baud
prints `[CONFIG] Send interval saved: ... seconds` when applied, and prints
the loaded interval at startup. No application acknowledgement is sent to the
website. Radio duty-cycle scheduling can delay actual transmissions further.

The binary protocol is **FPort 42**, exactly six bytes:

| Bytes | Meaning |
| --- | --- |
| `01` | Protocol version 1 |
| `01` | Set send interval command |
| Four bytes | Unsigned 32-bit interval in seconds, big-endian |

For example, five minutes (300 seconds) is `01 01 00 00 01 2C` (Base64
`AQEAAAEs`). Malformed commands, unsupported versions/commands, and values
outside the range are ignored. Other ports never change the interval.
Duplicate settings avoid another flash write. A flash write failure leaves
the previous active interval in place. OLED/Serial downlink observation still
works for configuration messages. Long intervals use bounded timer chunks
to avoid LMIC tick overflow.

The default 30-second interval and fixed SF7 radio setting match the original
bench sketch. For deployment, choose a longer interval in Configuration and set
`BENCH_RADIO_TEST` to `0` to enable adaptive data rate.
