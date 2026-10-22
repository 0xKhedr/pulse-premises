# LilyGO AJ-SR04M LoRaWAN distance node

Open `lilygo_sr04_lpp_node.ino` in Arduino IDE. This sketch uses the same
**LilyGO TTGO LoRa32-OLED V2.1 / 1.6.1 (ESP32 + SX1276)** as
`lilygo_cayenne_lpp_node`, with real AJ-SR04M distance readings, the OLED,
OTAA credentials, MAC-derived DevEUI, naming portal and BOOT rename gesture,
plus an RST rename gesture with Serial Monitor feedback.

Upload this sketch instead of `aj_sr04m_test` to send distance over LoRaWAN.
The existing TRIG=GPIO4 and ECHO=GPIO34 wiring stays the same. The local
credential header is already present; select your usual board/port, click
**Verify**, then **Upload**, and use Serial Monitor at **115200 baud**.
If `NO ECHO` persists, this sketch still joins, announces its nickname and
sends status packets every nominal 30 seconds. It omits the distance field
until valid sensor readings are available.

## Wiring with a 5 V sensor supply

Disconnect power before wiring. Use the pin labels on the sensor PCB.

| AJ-SR04M pin | LilyGO connection |
| --- | --- |
| VCC | Regulated **5 V**; the board's **5V/VBUS** pin when USB powered, after verifying its voltage |
| GND | **GND** |
| TRIG / RX | **GPIO4**, directly |
| ECHO / TX | **GPIO34**, through the divider below |

With your **three 1 kohm resistors**, use this arrangement at regulated 5 V:

```text
AJ-SR04M ECHO --[1k]--+--[1k]--[1k]-- GND
                     |
                 LilyGO GPIO34
```

The two resistors after the junction are in series. This makes a 1 kohm /
2 kohm divider, producing approximately 3.33 V from 5 V. Use it only with a
regulated 5 V supply, not 5.5 V. Join sensor GND and LilyGO GND.

An alternative divider with more voltage margin is:

```text
AJ-SR04M ECHO ----[ 2.2 kohm ]----+---- LilyGO GPIO34
                                |
                            [ 3.3 kohm ]
                                |
AJ-SR04M GND --------------------+---- LilyGO GND
```

Use ordinary 1/4 W resistors (1% preferred). The divider produces
`5 * 3.3 / (2.2 + 3.3) = 3.0 V` at GPIO34, with margin below 3.3 V.
**Do not connect a 5 V ECHO directly to GPIO34. A single series resistor
does not replace the divider.** ESP32 inputs are not 5 V tolerant.
TRIG normally accepts the ESP32's 3.3 V output directly, so no divider or
series resistor is needed there. If a particular revision does not recognize
3.3 V TRIG, use a suitable 3.3-to-5 V logic buffer on TRIG.

The AJ-SR04M manual specifies a 3–5.5 V supply. If your exact module is rated
for 3.3 V and works reliably there, VCC can use LilyGO **3V3** and ECHO can
connect **directly** to GPIO34, provided its output stays within 3.3 V.
Remove the 5 V divider in that arrangement: it would reduce a 3.3 V echo to
about 2.0 V, below the ESP32's guaranteed HIGH threshold. The 5 V/divider
arrangement above is the documented bench setup.

The USB 5 V rail is not a regulated 5 V battery output. For battery operation
use a suitable regulated supply or a verified 3.3 V sensor arrangement.
With an external sensor supply, join its GND to LilyGO GND; do not connect
its positive output to USB VBUS while USB is connected. GPIO34 is input-only.
These pins are for this board revision; the separate GPS sketch also uses
GPIO34, so disconnect the GPS before attaching this sensor. Connect the LoRa
antenna before powering the transmitting board.

## Sensor mode and range

Use **mode 1, ordinary pulse-width TRIG/ECHO**. The referenced AJ-SR04M manual
selects it with **R19 open**. It lists 300 kohm for low-power pulse mode,
120 kohm for automatic UART, 47 kohm for triggered UART and 0 ohm for ASCII.
PCB revisions differ: confirm your exact module's manual and markings before
changing any components. Do not apply JSN-SR04T R27 values to an AJ-SR04M.
This LoRaWAN sketch does not read UART mode or wake the low-power modes.

The default accepted range is **20–600 cm**. Keep targets outside the 20 cm
blind zone. Some AJ-SR04M manuals claim up to 8 m; this sketch deliberately
uses a conservative 6 m ceiling. Change `MAX_VALID_CM` and, if necessary,
`ECHO_TIMEOUT_US` only after confirming your module's range. Start testing
with a flat, hard target around 50–100 cm from the probe, facing it directly.

The sketch measures once per second when the radio has a safe idle period,
using a 20 microsecond trigger and a 50 ms echo timeout. It skips measurements
during pending LoRa TX/RX and near scheduled radio jobs. It never waits for
an echo inside LMIC callbacks. Missing echoes and out-of-range values
invalidate the reading immediately; values older than 5 seconds are not sent.
There are no simulated sensor values. Missing or stale distance produces a
status-only heartbeat on the normal 30-second schedule; joining and nickname
announcements still work without the sensor.
Distance conversion assumes 20 degrees C; adjust `AMBIENT_C` if needed.

## Arduino setup and commissioning

1. Use the existing **esp32 by Espressif Systems** board package. Select
   **TTGO LoRa32-OLED**, revision **TTGO LoRa32 V2.1 (1.6.1)**, as for the
   original node. The revision may appear in a separate Tools menu.
2. Keep/install **MCCI LoRaWAN LMIC library**, **CayenneLPP by Electronic Cats**,
   **Adafruit SSD1306** and **Adafruit GFX Library**, including their
   dependencies. No ultrasonic library is required. Keep the working LMIC
   **EU868 / SX1276** configuration from the original node.
3. From the project root run `python tools/generate_lorawan_credentials.py`
   using the project's Python environment. This writes the new sketch's
   gitignored `lorawan_credentials.h`. Alternatively copy the existing
   Cayenne sketch's local credential header into this sketch folder. Do not
   share the header or paste its credentials into the tracked sketch.
4. Verify and upload `lilygo_sr04_lpp_node.ino`. Open Serial Monitor at
   **115200 baud**. It reports distances, `NO ECHO`, or `OUT OF RANGE`.
5. For a new board, join the `SBMS-xxxx` Wi-Fi network with the OLED password,
   open `http://192.168.4.1`, and save a name such as `distance_tank_01`.
   Serial also accepts `nick distance_tank_01`. A named board retains its
   nickname. To reopen setup using **RST**, press and release RST, wait for
   `[RENAME] Press RST again within 4 seconds to rename.`, then press and
   release RST again before the countdown ends. Alternatively hold BOOT for
   two seconds during that window **after boot**, not during reset.
6. Follow the existing TTN registration/enrollment procedure using the DevEUI
   shown on serial/OLED. Select the built-in **Cayenne LPP uplink formatter**.
   Naming alone does not register a board unless automatic enrollment is set up.

All renaming works without seeing the OLED. At **115200 baud**, Serial
Monitor shows the countdown, BOOT press/release feedback, gesture detection,
and whether startup continues with the old nickname. Naming mode prints the
Wi-Fi SSID, password, `http://192.168.4.1` and the serial naming command,
repeating the instructions every 10 seconds until a name is saved. Use
**Newline** or **Both NL & CR** when sending commands. You can rename
directly at any time with `nick distance_tank_01` and query the saved name
with `nick`; serial renaming does not require the portal or an RST gesture.

A single RST followed by the full countdown keeps the stored name. The
pending RST marker ignores software resets, crashes and watchdog recovery.
On this board a power interruption during the four-second prompt looks like
a second RST, so it also opens naming mode. The gesture is available only
when a nickname exists; unnamed boards go straight to setup.

With the local CLI, the same board can be built without uploading:

```powershell
arduino-cli compile --fqbn "esp32:esp32:ttgo-lora32:Revision=TTGO_LoRa32_v21new" --output-dir build/lilygo_sr04_lpp_node firmware/lilygo_sr04_lpp_node
```

## TTN and cloud readings

Distance uses **FPort 1**, **channel 7**, standard **Analog Input type 2**,
in **meters**. The distance field works with TTN's built-in Cayenne LPP
formatter; the extended `addDistance()` type is not used because the built-in
formatter does not support it. Sensor validity uses **Digital Input channel 8**:
`digital_in_8 = 1` means the distance in this packet is valid, and `0` means
there is no fresh valid distance. A valid packet is seven bytes; a status-only
packet is three bytes. For example, 125 cm encodes as `08 00 01 07 02 00 7D`:

```json
{"digital_in_8": 1, "analog_in_7": 1.25}
```

With no usable echo the payload is `08 00 00`, decoded as `{"digital_in_8": 0}`.
This is sensor status, not a zero distance or a door-contact reading. Historical
distance in the cloud must not be treated as fresh when this flag is zero.

The existing `CAYENNE_LPP_V1` backend retains `analog_in_7` and exposes
`analog_in`. Use **analog_in_7**, with the label **Distance** and unit **m**,
when selecting the telemetry field. It is a digital ultrasonic measurement
carried in an LPP Analog Input field; it does not use the ESP32 ADC. The field
name is not automatically changed to `distance`. Wire resolution is 0.01 m
(1 cm); that does not imply 1 cm sensor accuracy. Old dummy or other-sensor
history on a reused board is retained. Nicknames remain raw text on FPort 10.

The default **30-second** interval and fixed SF7 setting are for bench use.
The next uplink is scheduled 30 seconds after the previous TX/RX cycle
completes, so actual transmission times also include radio-cycle duration and
any network duty-cycle delays. Nickname announcements use their own slots.
Sensor lines beginning `[SR04]` are local measurements, normally once per
second. They are not radio transmissions. `[LORA]` reports joined state and
accepted/completed packet counts every 10 seconds. `[TX]` reports successful
queueing, `[RADIO]` timestamps the actual radio start, and `EV_TXCOMPLETE`
confirms completion of the TX/RX cycle. The first application packet is
scheduled after `EV_JOINED`; busy or rejected queues retry in 5 seconds.
For deployment, increase `TX_INTERVAL_SECONDS` and set `BENCH_RADIO_TEST` to
`0` for ADR, choosing an interval suitable for the network's airtime policy.
The ESP32 and sensor stay powered; this sketch does not use deep sleep.
Confirm actual reception in TTN Live Data and the backend: completion of an
unconfirmed TX/RX cycle does not prove delivery to the gateway.

## References

- [AJ-SR04M user manual: supply, modes and blind zone](https://altronics.cl/uploads/manual-aj-sr04m.pdf)
- [LilyGO T3 V1.6.1 hardware and pin map](https://github.com/Xinyuan-LilyGO/LilyGo-LoRa-Series/blob/master/docs/en/t3_v161_sx1276/t3_v161_sx1276_hw.md)
- [ESP32 datasheet: GPIO input limits and thresholds](https://documentation.espressif.com/esp32_datasheet_en.html)
