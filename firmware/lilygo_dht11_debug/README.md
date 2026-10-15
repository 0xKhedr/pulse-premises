# DHT11 debug sketch

Open the local `lilygo_dht11_debug.ino` in Arduino IDE. It contains the existing
hardcoded LoRaWAN credentials, so the `.ino` is gitignored. Existing environment,
display-only, and provisioning-template sketches are retained.

This sketch follows the user's DHT11 configuration: DATA is on GPIO4. The
OLED uses SDA21/SCL22. An I2C temperature/humidity sensor on SDA21/SCL22 needs
the environment sketch's I2C driver instead of DHT11 code.

Install the provisioning template's existing display/LMIC libraries, the
Adafruit DHT sensor library, Adafruit Unified Sensor, and CayenneLPP.
Keep the same TTGO LoRa32 V2.1 board revision and LMIC region configuration
as the working environment sketch.

Save a nickname in the board's Wi-Fi portal before the node starts joining.
To reopen naming mode on a named board, press and release **RST / RESET**,
wait until the OLED says **Press RST again**, then press and release RST
again within the displayed four-second window. The sketch forgets the old
nickname and shows the `SBMS-xxxx` Wi-Fi network and password; connect and
open `http://192.168.4.1` to save the new name. Reflash this updated sketch
before using the gesture.

Holding RST for two seconds cannot be detected: it holds the processor in
reset. The former code watched **BOOT / GPIO0**, only during startup. Holding
BOOT for two seconds during the startup prompt remains supported. Do not
hold BOOT while releasing RST, because that can enter the ESP32 bootloader.

The reset gesture uses a temporary NVS marker, cleared before normal
operation. Watchdog, panic, brownout, and software resets do not trigger
renaming. ESP32 cannot distinguish RST from a fresh power-on, so interrupting
power during the four-second prompt also opens naming mode on the next boot.

The web app's enrollment window should be open while the node sends OTAA
join requests; it needs received gateway traffic to discover an unknown node.

Startup now matches the environment sketch: queue a nickname on port 10
before a session exists, allowing LMIC to perform OTAA and then transmit it.
Sensor data uses seven-byte Cayenne LPP: temperature channel 1 and humidity
channel 2 on port 1. Twenty samples at three-second intervals are averaged;
sampling pauses while the radio is busy. Invalid sensor readings do not
produce invented telemetry. Data readiness is consumed only after LMIC
accepts the transmission, and restored if that transmission is canceled.

Serial Monitor at 115200 baud prints boot stages and each join attempt's
frequency in Hz, data-rate index, and DevNonce. It never prints the AppKey.
`NO ACCEPT` means no valid join accept reached the device; it does not by
itself distinguish a missing gateway reception, network rejection, or a
failed downlink.

The supplied gateway export contains twelve `gs.status.receive` events and
one UI `synthetic.status.cleared` event. It contains no `gs.up.receive`, raw
join request, join rejection, or downlink event. All twelve status reports
have `rxok = 0` and `txok = 0`. The export does not identify a rejected key.

To diagnose further, view all traffic in the TTN gateway's Live data, with
event filters cleared. Verify the observing account/key can read gateway
traffic, and that the web enrollment listener watches the correct gateway.
Its key is configured by `AUTO_ENROLLMENT_GATEWAY_API_KEY`; a status-only
or application-only key cannot supply received join frames to the listener.
Compare the debug sketch's join frequency/data rate with the gateway's
configured frequency plan and with a working environment-sketch run.

The local hardcoded JoinEUI, AppKey, namespace mask, identity derivation, and
radio pins were compared with the environment sketch and match. Static
checks passed. A board build and on-air verification remain necessary.
