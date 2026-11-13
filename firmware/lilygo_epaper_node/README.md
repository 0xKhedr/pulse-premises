# LilyGO 4.2-inch black/white/red e-paper node

Standalone Arduino firmware for the existing **TTGO LoRa32-OLED V2.1 / T3
V1.6.1 (ESP32 + SX1276)** and a **Waveshare 4.2-inch (B), 400 x 300,
black/white/red** SPI module. No sensor is needed. Other LilyGO boards need
their own verified pin mapping and potentially another radio driver.

The node sends an unconfirmed LoRaWAN uplink about every **60 seconds**.
This opens Class A RX1/RX2 windows for updates queued on the network server;
the gateway relays the traffic. There is no HTTP request, direct gateway API,
or continuous receive mode. Duty-cycle scheduling and reception failures can
extend delivery time. Wi-Fi is enabled only for initial naming/credentials.

## Wiring

Disconnect power before wiring. Use the GPIO numbers printed on the LilyGO.
Power the module from **3.3 V**, so its supply and signal levels match the
ESP32. Keep the LoRa antenna connected when the board transmits.

| Waveshare module pin | LilyGO connection |
| --- | --- |
| VCC | 3V3 |
| GND | GND |
| DIN | GPIO13 |
| CLK | GPIO14 |
| CS | GPIO15 |
| DC | GPIO25 |
| RST | GPIO4 |
| BUSY | GPIO34 |

The e-paper has its own **HSPI** controller. The SX1276 stays on its existing
SPI bus (SCK 5, MISO 19, MOSI 27, CS 18; reset 23; DIO 26/33/32). The onboard
OLED stays on SDA 21 / SCL 22. Do not attach another sensor to GPIO13, and
remove an SD card if fitted: this wiring uses pins normally used by SD.
GPIO34 is input-only, which is suitable for BUSY. GPIO15 is a boot strap pin;
the display's CS input must not be externally forced low during reset.

## Select the panel

**Rev 2.1 on the adapter PCB does not identify the panel/controller.**
Check the panel model on the ribbon/back label and set `SBMS_EPAPER_PANEL`
in `EpaperConfig.h` before uploading:

| Setting | Panel | GxEPD2 driver |
| --- | --- | --- |
| `1` (default) | GDEW042Z15 / IL0398, older 4.2-inch (B) | `GxEPD2_420c` |
| `2` | GDEQ042Z21 / UC8276 | `GxEPD2_420c_Z21` |
| `3` | GDEY042Z98 / SSD1683 | `GxEPD2_420c_GDEY042Z98` |

These are distinct drivers, not automatic panel detection. A blank display or
BUSY timeout can mean the selected controller is wrong. If the model is
unreadable, identify it from the module's exact product/version or run the
matching Waveshare example before assuming the default is compatible.

## Build and commission

In Arduino Library Manager install **GxEPD2** (installed version: 1.6.9),
**MCCI LoRaWAN LMIC library**, **Adafruit SSD1306**, **Adafruit GFX Library**,
and the **Adafruit BusIO** dependency. No CayenneLPP or sensor libraries are
needed. Keep the project's LMIC **EU868 / SX1276** configuration; the firmware
checks that configuration at compile time. ADR is enabled. Use the frequency
plan already configured for your gateway and hardware.

1. Generate this folder's gitignored credential header from the existing
   project configuration: `python tools/generate_lorawan_credentials.py`.
   This uses the existing key; do not use `--generate-app-key` for this step.
2. Open `lilygo_epaper_node.ino` in Arduino IDE. Select the existing LilyGO
   board/revision and USB port. CLI FQBN:
   `esp32:esp32:ttgo-lora32:Revision=TTGO_LoRa32_v21new`.
3. Upload and open Serial Monitor at **115200**. An unnamed board starts the
   existing setup AP, with its SSID/password on the OLED and Serial. Open
   **192.168.4.1**, set a nickname, and optionally enter OTAA credentials.
   Blank credential fields retain their current values. Naming and credentials
   use the existing `sbms` NVS keys. Reusing a board retains its identity/name;
   it does not create a second device automatically.
4. In the SBMS portal open discovery, then accept the new join candidate.
   The node keeps retrying OTAA while it waits. The nickname uses FPort 10.
5. If a Cayenne LPP formatter was assigned to this device, replace it with
   `ttn_uplink_formatter.js` in TTN (JavaScript uplink formatter), or disable
   its uplink formatter. Display polls are not Cayenne LPP sensor packets.
   Do not assign this device a sensor profile that reinstalls that formatter.

For CLI builds using the existing helper, run from Git Bash:

```bash
SKETCH=lilygo_epaper_node ./tools/node_firmware.sh build
SKETCH=lilygo_epaper_node ./tools/node_firmware.sh flash COM14
```

On a named board, reset once, then press RST again within the three-second
startup window to clear its nickname and reopen setup. A power interruption
in that window has the same effect. Saved OTAA credentials remain. The Serial
command `nick <name>` also changes the nickname.

## Send a display update

Use **Device Management → Send downlink** for the joined device:

- **FPort 20**: replace the whole message body with **black text**.
- **FPort 21**: replace the whole message body with **red text**.
- Format: **UTF-8 text**, using **ASCII characters** only; at most **51 bytes**.
- Example: `Room 308` followed by a newline and `Meeting at 14:00`.
- Leave **confirmed** off, then queue the message and wait for an uplink.

The title is red, the nickname is black, and the body uses the selected color
on a white background. Body text wraps at 30 characters and supports up to
eight lines, including explicit newlines. CRLF is normalized. Oversize text,
too many lines, non-ASCII glyphs (including Arabic/accented text), and other
control bytes are rejected without replacing the last valid message. Other
ports do not change the body. Hex/Base64 input must decode to the same text
bytes. This version sends text, not arbitrary images or mixed-color markup.

The same operation is available through the existing administrator API:

```http
POST /devices/{device_id}/downlinks
Content-Type: application/json

{"f_port":20,"encoding":"text","payload":"Room 308\nMeeting at 14:00","confirmed":false}
```

Queue success does not prove receipt or display completion. Serial prints
`[EPAPER] New text saved; refresh pending.` and then refresh start/completion
messages. The three-byte FPort 20 uplink is `[1, has_rendered, body_port]`;
it is status only, not a message-specific acknowledgement. The nickname
announcement replaces a poll periodically but opens the same receive windows.

## Polling, refresh, and persistence

Polling defaults to 60 seconds after each completed uplink/RX transaction.
Use the existing **Devices → Configuration → Queue send-rate change** to
change it (FPort 42, existing six-byte protocol, 30–86,400 seconds). The setting
uses `sbms/epaper_interval`, separate from a sensor sketch's send interval.

A red-panel full refresh takes many seconds. The code starts it only after
joining, outside pending transmit/receive operations, and services the LMIC
run loop while the display is busy on its separate SPI bus. It hibernates the
panel after a full refresh. The ESP32 stays awake to maintain its LoRaWAN
session; this is not a whole-board deep-sleep implementation.

There is at least **180 seconds between refreshes**, independently of polls.
Identical text/color does not trigger another refresh or flash write. Updates
received during that interval replace the pending message; the latest one is
displayed when eligible. An unchanged screen gets a maintenance refresh after
24 hours of continuous operation. A restart redraws the saved message after
joining. New messages are stored in `sbms_epaper/message`; if a flash save
fails they still work in RAM and Serial reports the persistence failure.
The initial waiting screen does not overwrite the panel before joining.

TTN's public Sandbox allows **30 seconds uplink airtime and 10 downlinks per
day per node**. One-minute polling is intended for the existing private/test
network; select a longer interval for public TTN. Even on a private network,
radio/gateway duty-cycle limits can delay transmissions. No duty-cycle
enforcement is disabled by this sketch.

## Validation status

Host tests exercise the actual payload parser, refresh timing (including timer
rollover), poll bytes, and rejected/busy transmit retries. A full ESP32 build
was attempted but blocked by sandbox toolchain access, and elevated access was
declined. The sketch has not been compiled for the board or tested on the
physical display. Run the build above after selecting the matching panel.

## References

- [Waveshare 4.2-inch (B) manual and refresh guidance](https://www.waveshare.com/wiki/4.2inch_e-Paper_Module_%28B%29_Manual)
- [GxEPD2 panel drivers and API](https://github.com/ZinggJM/GxEPD2)
- [LilyGO LoRa32 documentation](https://wiki.lilygo.cc/products/t3-series/t3-lora32/)
- [TTN duty cycle and Sandbox fair use](https://www.thethingsnetwork.org/docs/lorawan/duty-cycle/)
