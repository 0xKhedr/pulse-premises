# LilyGO NEO-6M GPS LoRaWAN node

This sketch targets the same **TTGO LoRa32-OLED V2.1 / 1.6.1 (ESP32 +
SX1276)** as `lilygo_cayenne_lpp_node` and the temperature node. It keeps the
generated DevEUI, shared OTAA credentials, phone naming portal, stored
nickname, BOOT-button rename and port-10 nickname announcements. Wi-Fi turns
off after naming. It sends measured GPS coordinates only.

## Breadboard wiring

Unplug USB and disconnect any battery before wiring. These instructions are
for a **GPS breakout/carrier board with labeled VCC, GND, TX and RX headers**,
not the bare 24-pad NEO-6M-0-001 module. That marking identifies the receiver
under the metal cover; it does not identify the carrier's input voltage.
A bare module needs a carrier PCB, antenna circuitry and the manufacturer's
support circuitry; it cannot plug directly into a breadboard.

Use the GPIO numbers printed on the LilyGO, not header position numbers.

| GPS breakout pin | LilyGO connection | Purpose |
| --- | --- | --- |
| GND | GND | Common ground |
| TX / TXD | IO34 / GPIO34 | GPS transmits NMEA; ESP32 receives on UART2 RX |
| RX / RXD | Leave disconnected | Optional: IO12 / GPIO12 (UART2 TX) for future configuration |
| VCC / VIN | 3V3 **only if the carrier accepts 3.3 V input** | See power note below |
| PPS / TIMEPULSE, if present | Leave disconnected | Not used |

**Power depends on your breakout.** The bare NEO-6M receiver requires
2.7-3.6 V. Some carriers accept 3.3 V on VCC, while a carrier with a regulator
may require a higher input voltage. If your carrier specifies **5 V input**,
use the LilyGO's verified **5V/VBUS** supply while powered by USB, instead of
3V3. Check the carrier's documentation/silkscreen first; never apply 5 V to
the bare receiver or to ESP32 GPIO. Use **3.3 V UART signal levels**; a carrier
with 5 V TX output requires level translation before GPIO34.

1. If headers are not installed, solder them first. Loose pins pressed against
   the board do not make reliable connections.
2. Place the LilyGO so its two header rows occupy separate breadboard strips.
   If it is too wide to leave accessible holes, use two breadboards or jumper
   wires directly to its headers. Keep the GPS on separate strips.
3. Wire GPS GND to LilyGO GND, GPS TX to IO34, and GPS VCC to the appropriate
   supply above. These **three wires are sufficient** for this firmware.
4. If using breadboard power rails, check for a break halfway along the rail
   and bridge it if needed. Keep all grounds connected.
5. Connect the GPS antenna to its GPS connector. Attach the correct LoRa
   antenna to the LilyGO before powering it. They are separate antennas.
6. Reconnect USB. For first acquisition put the GPS antenna outdoors with an
   unobstructed sky view, ceramic patch facing upward. Allow several minutes;
   indoor reception may never produce a fix.

GPIO34 and GPIO12 are exposed headers and match LilyGO's official T3 V1.6.1
GPS wiring guide. GPIO34 is input-only, suitable for receiving the GPS TX
signal. UART2 is explicitly remapped to these pins; its ESP32 default pins
are not needed. Leave GPS RX disconnected for this sketch. For another
LilyGO revision, check its schematic and change `GPS_RX_PIN`, `GPS_TX_PIN`
and the LoRa/OLED pins as needed.

## Arduino IDE setup

1. In Boards Manager use the **esp32 by Espressif Systems** package already
   used for the temperature node. Keep the same board and revision selection:
   **TTGO LoRa32-OLED**, revision **TTGO LoRa32 V2.1 (1.6.1)**. Depending on the
   installed core, the revision appears in a separate Tools menu.
2. In Library Manager install **TinyGPSPlus by Mikal Hart** (1.0.3 or newer).
   Keep/install **MCCI LoRaWAN LMIC library**, **CayenneLPP by Electronic Cats**,
   **Adafruit SSD1306**, and **Adafruit GFX Library**. Accept their dependency
   installations (including ArduinoJson and Adafruit BusIO when requested).
3. Keep the same working LMIC regional/radio configuration as the existing
   node: **EU868 / SX1276**. The sketch's bench SF7 setting assumes EU868.
4. From the project root generate the sketch-local credentials with
   `python tools/generate_lorawan_credentials.py`, using the project's Python
   environment. Alternatively copy the existing temperature/Cayenne sketch's
   local `lorawan_credentials.h` into this GPS sketch folder. Do not paste
   credentials into the tracked `.ino` or share that header.
5. Open `lilygo_gps_lpp_node.ino` in this folder. Choose your LilyGO's USB port
   and click **Verify**, then **Upload**. Open Serial Monitor at **115200 baud**.

## Naming, join and readings

For a new board, connect your phone to the `SBMS-xxxx` Wi-Fi network using the
password on the OLED, open `http://192.168.4.1`, and save a name such as
`gps_roof_01`. USB serial also accepts `nick gps_roof_01`. A previously named
board retains its name. To rename using the portal, hold BOOT for two seconds
in the first four seconds **after boot**, not during reset.

The board joins via OTAA even while GPS is acquiring a fix. Follow your
existing TTN registration/automatic enrollment procedure using the DevEUI
shown on serial/OLED. A name alone does not register a device in TTN unless
your gateway enrollment service is configured.

In TTN, select the built-in **Cayenne LPP uplink formatter**. GPS data uses
**FPort 1**, **channel 1**, standard GPS type **136 / 0x88**. Nicknames remain
raw text on **FPort 10**, handled separately by the existing backend.

The 11-byte GPS payload contains latitude/longitude in decimal degrees and
altitude in meters above mean sea level. LPP encodes coordinates in steps of
0.0001 degree and altitude in steps of 0.01 m; encoding precision does not
imply GPS measurement accuracy. TTN produces, for example:

```json
{"gps_1": {"latitude": 30.0444, "longitude": 31.2357, "altitude": 23.5}}
```

The backend flattens valid GPS objects into `latitude`, `longitude` and
`altitude`, retaining channel-specific `latitude_1`, `longitude_1` and
`altitude_1` values for telemetry and automatic profile learning. Restart the
backend after updating it. GPS telemetry does not change the existing manual
floor-map placement or turn floor coordinates into geographic coordinates.

Both position and altitude must be valid and at most **5 seconds** old. If a
fix is missing/stale, no coordinates are transmitted; the sketch retries in
five seconds without blocking LMIC. No placeholder `(0,0)` or invented
altitude is sent. Genuine zero coordinates are allowed. RMC and GGA NMEA
output must be enabled (the NEO-6M defaults include them).

The OLED shows coordinates or `WAITING FOR GPS FIX`; serial reports satellite
count/HDOP and distinguishes absent UART bytes from missing GPS reception.
`NO UART DATA` means check power, common ground, TX/RX direction and baud.
Bytes without valid NMEA suggest a baud mismatch or binary-only output.
The default GPS UART setting is **9600 baud, 8N1**; change `GPS_BAUD` if the
receiver was previously configured differently. GPS RX can stay unwired:
this sketch does not send configuration commands.

For persistent `NO UART DATA`, use the standalone
[`lilygo_gps_uart_debug`](../lilygo_gps_uart_debug/README.md) sketch to inspect
raw UART bytes, scan common baud rates and test the LilyGO UART with a wired
loopback independently of the GPS. Its GPIO signal test detects transitions
without decoding UART, and its optional read-only query requests receiver
identification through an additional IO4-to-GPS-RX wire. Zero received bytes
alone does not identify a power fault.

The default **30-second** interval and fixed SF7 match the temperature bench
sketch. For deployment increase `TX_INTERVAL_SECONDS` and set
`BENCH_RADIO_TEST` to `0` for ADR. Choose an interval appropriate to your
network's airtime policy and operating data rate. GPS stays powered and the
ESP32 stays awake; this is not a deep-sleep tracker. Unconfirmed uplinks do
not prove gateway delivery: confirm reception in TTN Live Data/backend.

## Hardware and protocol references

- [u-blox NEO-6 datasheet: module voltage, UART and defaults](https://content.u-blox.com/sites/default/files/products/documents/NEO-6_DataSheet_(GPS.G6-HW-09005).pdf)
- [LilyGO LoRa32 board documentation and schematics](https://wiki.lilygo.cc/products/t3-series/t3-lora32/)
- [LilyGO T3 V1.6.1 GPS wiring guide](https://github.com/Xinyuan-LilyGO/LilyGo-LoRa-Series/wiki/T3-V1.6.1-how-connect-gps-module%3F)
- [TinyGPSPlus library](https://github.com/mikalhart/TinyGPSPlus)
- [Cayenne LPP specification and GPS encoding](https://github.com/myDevicesIoT/CayenneLPP)
