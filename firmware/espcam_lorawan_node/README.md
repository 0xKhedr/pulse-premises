# ESP32-CAM LoRaWAN sender

This sketch runs on the LilyGO TTGO LoRa32-OLED board, which receives frames
from the ESP32-CAM over UART. The reset gesture below uses the **LilyGO RST
button**, not the separate camera board's reset button.

## Rename a deployed device

Upload the updated `espcam_lorawan_node.ino`, keeping the existing credentials
header and board/LMIC settings.

1. Press and release **RST** on the LilyGO board.
2. Wait for **Press RST again** on the OLED, then press and release RST again
   within the displayed four-second window.
3. The saved nickname and deployment flag are cleared. The OLED shows
   **SETUP - NAME ME**, the `SBMS-CAM-xxxx` Wi-Fi network, and its password.
4. Connect to that network and open `http://192.168.4.1`. Enter a new nickname
   on the camera's existing calibration page and click **Deploy (LoRaWAN)**.
   Deploy becomes available when the camera is locked and recognition is on.
   If needed, use the page's existing camera controls first.

The renamed device keeps its DevEUI and sends the new name on port 10 after
joining. The website's existing device label updates when that uplink arrives.

An ordinary reset resumes deployment after the prompt. To recalibrate while
keeping the current name, hold **BOOT / GPIO0** for two seconds during the
prompt. Do not hold BOOT while releasing RST; that can enter the bootloader.

Holding RST for two seconds cannot be measured because it holds the processor
in reset. The reset gesture uses a temporary NVS marker, cleared before
normal operation. Software, watchdog, panic, brownout and deep-sleep resets
do not trigger renaming. ESP32 reports RST and power-on alike, so cutting power
during the four-second prompt also opens naming mode on the next boot.

The website sensor profile for this sketch is `ESPCAM_METER_V1`; changing the
nickname does not change that profile or the camera's payload format.

## Upload verification fails with an MD5 mismatch

An MD5 mismatch means the uploaded file and the data read back from flash
disagree. This check happens before the new sketch starts running.

1. Power off and temporarily disconnect the ESP32-CAM and external wiring
   from the LilyGO board. Upload with the LilyGO connected alone over USB.
   The camera link includes GPIO12; external circuitry holding that pin high
   during reset can select the wrong flash voltage.
2. Select the LilyGO **TTGO LoRa32-OLED** board and the matching V2.1 revision,
   set **Upload Speed** to **115200**, close Serial Monitor, and retry.
3. If needed, try a different USB data cable and a direct computer USB port.

After a successful upload, power off before restoring the camera wiring.
If verification still fails, retain the complete upload log, including the
flash addresses and progress preceding the MD5 error, for diagnosis.

See Espressif's [upload troubleshooting](https://docs.espressif.com/projects/esptool/en/latest/esp32/troubleshooting.html)
and [boot pin guidance](https://docs.espressif.com/projects/esptool/en/latest/esp32/advanced-topics/boot-mode-selection.html).
