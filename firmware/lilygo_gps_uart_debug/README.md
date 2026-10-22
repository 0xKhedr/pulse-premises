# LilyGO GPS UART diagnostic

Use this when the GPS LoRaWAN sketch reports `NO UART DATA`. Upload this
sketch to the **LilyGO**, using the same TTGO LoRa32 V2.1 / 1.6.1 board setting.
Only the installed ESP32 board package is needed. No extra libraries or GPS
USB driver are needed. It does not change GPS configuration or the stored
nickname. It temporarily replaces the running
LoRaWAN sketch; upload that sketch again after diagnosing the serial link.

Keep GPS **TX -> IO34**, GPS **GND -> LilyGO GND**, and the current GPS power
arrangement. Leave GPS RX and PPS disconnected. Open Serial Monitor at
**115200 baud**, then press RESET. The sketch starts in **signal test** mode.

## First test: watch electrical activity without decoding UART

Every two seconds the sketch prints a line such as:

```text
[SIGNAL] IO34 | level=HIGH | edges_2s=1450 | shortest_interval_us=104
```

`edges_2s` counts rising/falling transitions using a GPIO interrupt while the
UART is stopped. This test does not depend on the baud rate or GPS parser.
Keep the wire connected to the powered GPS TX; a disconnected IO34 floats
and may produce misleading levels or transitions.

| Observation over several reports | Interpretation |
| --- | --- |
| Edges above zero | Electrical activity reaches IO34. It could be serial data or noise; try `s` to decode it. |
| Zero edges, level HIGH | No transitions were observed. HIGH can be ordinary UART idle or a floating/undriven line; it does not prove that the receiver is working. |
| Zero edges, level LOW | No transitions were observed. The line reads low; this alone does not identify a power, wiring or receiver fault. |

The reported level is a logic reading, **not a voltage measurement**. The
shortest interval is approximate interrupt timing, not a reliable baud-rate
measurement. These tests cannot establish that the GPS power rails or logic
voltages meet their specifications.

On ESP32 core 3.3.11 and newer, the sketch disables the UART's automatic RX
pull resistor request because IO34 has no internal pull resistor. Older
versions of this diagnostic may print `gpio_pullup_en ... input-only pad has
no internal PU` at baud changes. That message describes an unsupported pull
request; it does not by itself establish a UART receive failure.

## Serial Monitor commands

Send one lowercase letter. Any Serial Monitor line-ending setting is fine.

| Command | Action |
| --- | --- |
| `e` | Return to the GPIO edge/level test; sends nothing to GPS. |
| `s` | Scan normal UART polarity at six common baud rates. |
| `i` | Scan the same rates using inverted UART polarity. |
| `q` | Scan normal polarity while sending read-only receiver identification polls; requires the extra RX wire below. |
| `l` | Enter the IO12-to-IO34 loopback test described below. |
| `t` | Test the query transmit pin: disconnect the GPS completely, connect IO4 to IO34, then send `t`. |

In scan mode the sketch reads raw bytes directly from UART2. It tries 9600, 4800, 19200,
38400, 57600 and 115200 baud for eight seconds each. After two NMEA sentences
with valid checksums, or one UBX frame with a valid checksum, it stops scanning
and reports the detected baud/polarity. It prints total bytes, valid NMEA/UBX
counts, an ASCII sample and a hex sample.
The GPS does not need a position fix to test the serial connection.

| Result | What it establishes / next step |
| --- | --- |
| `[FOUND] Valid NMEA at ...` | The GPS-to-LilyGO serial link works. Set `GPS_BAUD` to this rate in the LoRaWAN sketch. If it is already 9600 with `inverted=0`, report this result so the integration can be investigated. If only `inverted=1` works, share the result before updating the LoRaWAN sketch, which currently expects normal polarity. |
| `[UBX] ... checksum=OK` / `[RECEIVER] ...` | Valid binary receiver traffic was received. An identification reply can work even if periodic NMEA output is disabled. Share the output; the LoRaWAN sketch still needs NMEA position/altitude messages enabled. |
| Bytes arrive but no valid NMEA at any tested rate | Something reaches IO34. It may be binary-only receiver output, an untested rate, electrical noise or framing errors. Share the ASCII/HEX samples before changing GPS configuration. |
| `bytes=0` at every rate | No UART bytes reached IO34. This does not establish a power fault; test the LilyGO receiver and jumper independently below. |

## Optional read-only query: request a reply from a quiet GPS

The ordinary scans only listen. A receiver with periodic output disabled can
remain silent even when its UART is working. This optional test sends a
UBX-MON-VER identification request at each normal baud rate. It does not reset
the receiver, enable messages, change its baud rate or save configuration.

1. Turn off both boards and remove any IO12-to-IO34 loopback jumper.
2. Keep **GPS TX -> LilyGO IO34** and **GND -> GND**.
3. Add **LilyGO IO4 -> GPS RX**. IO4 is used for this query; IO12 is reserved
   for the previous loopback test. Use the board's GPIO numbers, not header
   positions. Keep PPS disconnected and retain the existing GPS supply.
4. Power the boards normally. Send **`q`** in Serial Monitor at 115200 baud.
5. Watch for `[POLL]`, `[UBX]`, `[RECEIVER]` and `[FOUND]`. Allow 50 seconds
   for all six rates if there is no reply. `[POLL]` means a request was sent;
   only a checksum-valid response establishes receiver communication.

The IO4 connection requires a GPS input compatible with ESP32 3.3 V logic.
The receiver module's UART and the carrier's power-input voltage are separate
specifications. No reply is inconclusive: it can result from a connection,
signal-level, baud/configuration, module, or power issue. Some receivers may
not implement UBX. Share the logs before changing persistent settings.

The transmitted request is `B5 62 0A 04 00 00 0E 34`. Receiving this same
zero-length request back is an echo, not a receiver identification reply; the
parser deliberately rejects it as evidence of receiver communication. Partial
matches, particularly `00 00 0E 34`, can suggest echo/coupling but do not prove
it. To compare, power off and remove the IO4-to-GPS-RX wire, keep GPS TX on
IO34, and run `q` again. IO4 then transmits into an unconnected pin. Record
whether the unwanted bytes still appear. This test writes no GPS settings.

## Optional loopback: test IO34 and a jumper without the GPS

1. Turn off both boards. **Disconnect the GPS TX wire from IO34.** Do not
   connect GPS TX and LilyGO IO12 together: they are both outputs.
2. Connect a female-to-female jumper from **LilyGO IO12 to LilyGO IO34**.
   GPS RX and PPS stay disconnected. The GPS can be disconnected entirely.
3. Power the LilyGO by USB. In Serial Monitor at 115200 baud, send the single
   letter **`l`**. The sketch sends a known string from IO12 once per second
   and listens for it on IO34 at 9600 baud.
4. `loopback_matches` increasing verifies UART2 transmission/reception through
   those headers and that jumper. It does not verify the GPS output or power.
   If it stays zero, try another jumper and check that the correct header pins
   are soldered. Share the result before changing the main firmware.
5. Turn power off, remove the IO12-to-IO34 jumper, and reconnect GPS TX to IO34.
   Reboot for the GPIO signal test, or send **`s`** after powering both boards
   normally. If IO4 was connected for a query, it can be removed for scans.

The loopback jumper must be removed before reconnecting GPS TX. No GPS
programming is performed by this diagnostic.

## Verify the query transmit pin separately

The original `l` loopback uses IO12, while the receiver query uses IO4.
Passing the IO12 loopback does not test IO4 or its jumper.

1. Turn power off and disconnect the GPS completely.
2. Connect a jumper from **LilyGO IO4 to IO34**. Leave IO12 disconnected.
3. Power the LilyGO through USB. Open Serial Monitor at **115200 baud** and
   send **`t`**. The test internally uses 9600 baud; leave Serial Monitor at
   115200.
4. `loopback_matches` should increase once per second. If it does, IO4 TX,
   IO34 RX and that jumper have been tested together. If it stays zero, try
   another jumper and confirm the physical pin labels before querying GPS.
5. Power off and remove the loopback jumper before reconnecting the GPS.

If both loopbacks pass but the GPS produces no signal or checksum-valid reply,
the software tests cannot determine the remaining cause. A clear photo of the
carrier and connections, voltage measurements, or a known-good compatible
receiver/adapter is then needed to distinguish a silent/configured receiver,
carrier/connector issue, signal-level mismatch or supply issue. A silent TX
line alone does not prove the GPS is faulty.

References: [LilyGO's GPS header wiring](https://github.com/Xinyuan-LilyGO/LilyGo-LoRa-Series/wiki/T3-V1.6.1-how-connect-gps-module%3F),
[Espressif HardwareSerial API](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/serial.html).
