#!/usr/bin/env bash
#
# Build, flash and monitor the LoRaWAN node firmware without the Arduino IDE.
#
# The IDE recompiles for every board it flashes, which is wasted work when
# thirty identical boards are being prepared: the firmware is the same for all
# of them, and each one is named afterwards from a phone rather than at build
# time. So this builds once and uploads the prebuilt binaries repeatedly, which
# takes seconds per board instead of minutes.
#
#   ./tools/node_firmware.sh build              compile once
#   ./tools/node_firmware.sh ports              list attached boards
#   ./tools/node_firmware.sh flash COM14        upload the existing build
#   ./tools/node_firmware.sh monitor COM14      serial console (nick command)
#   ./tools/node_firmware.sh all COM14          build, flash, then monitor
#
# Set SKETCH to work on a different sketch in firmware/:
#
#   SKETCH=aj_sr04m_test ./tools/node_firmware.sh all COM68
#
# Requires arduino-cli. The ESP32 core and the four libraries are shared with
# the Arduino IDE's own installation, so nothing is downloaded twice.

set -euo pipefail

# The bare ttgo-lora32 FQBN builds for the V1 board, whose OLED sits on
# different pins. These boards are the V2.1 (1.6.1) revision.
FQBN="esp32:esp32:ttgo-lora32:Revision=TTGO_LoRa32_v21new"
# Which sketch to work on. The node firmware by default; SKETCH=aj_sr04m_test
# selects the ultrasonic bench test, which shares the same board and toolchain.
SKETCH_NAME="${SKETCH:-lilygo_cayenne_lpp_node}"
SKETCH="firmware/$SKETCH_NAME"
BUILD_DIR="build/$SKETCH_NAME"
BAUD=115200

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Installed by winget outside the default PATH on Windows.
export PATH="$PATH:/c/Program Files/Arduino CLI"

if ! command -v arduino-cli >/dev/null 2>&1; then
    echo "arduino-cli not found. Install it with:" >&2
    echo "    winget install --id ArduinoSA.CLI" >&2
    exit 1
fi

require_port() {
    if [ -z "${1:-}" ]; then
        echo "A port is required, for example COM14." >&2
        echo "Run './tools/node_firmware.sh ports' to list them." >&2
        exit 1
    fi
}

cmd_build() {
    echo "Compiling $SKETCH for $FQBN..."
    [ -d "$SKETCH" ] || { echo "No such sketch: $SKETCH" >&2; exit 1; }
    arduino-cli compile --fqbn "$FQBN" --output-dir "$BUILD_DIR" "$SKETCH"
    echo
    echo "Built into $BUILD_DIR. Flash any number of boards from it with:"
    echo "    ./tools/node_firmware.sh flash <PORT>"
}

cmd_ports() {
    arduino-cli board list
}

cmd_flash() {
    require_port "${1:-}"
    if [ ! -d "$BUILD_DIR" ]; then
        echo "No build found. Run './tools/node_firmware.sh build' first." >&2
        exit 1
    fi
    # --input-dir uploads what was already compiled, so preparing the next
    # board costs an upload rather than a rebuild.
    echo "Uploading the existing build to $1..."
    arduino-cli upload --fqbn "$FQBN" --port "$1" --input-dir "$BUILD_DIR"
    echo
    echo "Done. The board raises a Wi-Fi access point until it is named;"
    echo "its SSID and password are shown on the OLED."
}

cmd_monitor() {
    require_port "${1:-}"
    echo "Serial console on $1 at $BAUD. Type 'nick <name>' to name this board."
    echo "Ctrl-C to exit."
    arduino-cli monitor --port "$1" --config "baudrate=$BAUD"
}

case "${1:-}" in
    build)   cmd_build ;;
    ports)   cmd_ports ;;
    flash)   cmd_flash "${2:-}" ;;
    monitor) cmd_monitor "${2:-}" ;;
    all)
        require_port "${2:-}"
        cmd_build
        cmd_flash "$2"
        cmd_monitor "$2"
        ;;
    *)
        sed -n '3,25p' "$0" | sed 's/^# \{0,1\}//'
        exit 1
        ;;
esac
