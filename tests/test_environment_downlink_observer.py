"""Compile the real observer/capture/display functions against a simulated radio."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_environment_observer_preserves_and_pages_class_a_downlinks(tmp_path):
    compiler = shutil.which("g++")
    if not compiler:
        pytest.skip("g++ is required for the local observer simulation")
    folder = Path(__file__).resolve().parents[1] / "firmware/lilygo_environment_lpp_node"
    sketch = (folder / "lilygo_environment_lpp_node.ino").read_text(encoding="utf-8")
    capture = sketch[sketch.index("static void captureApplicationDownlink() {"):
                     sketch.index("static void logObservedDownlink() {")]
    render_start = sketch.index("static void renderDisplay() {")
    render = sketch[render_start:sketch.index("\n#endif", render_start)]
    harness = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <string>
#include <sstream>
#include <vector>
#include "DownlinkObserver.h"
#define ENABLE_OLED 1
#define F(x) x
#define TXRX_PORT 0x10
#define SSD1306_WHITE 1
#define OLED_WIDTH 128
struct { uint8_t frame[64] = {}; uint8_t dataBeg = 0, dataLen = 0, txrxFlags = 0; } LMIC;
DownlinkObserver downlinkObserver;
bool downlinkLogPending = false, displayDirty = false, oledReady = true, observerJoined = true;
size_t observerPage = 0;
unsigned long lastObserverPageAt = 0;
unsigned long millis() { return 10000; }
unsigned uplinkCount = 2, lastPayloadLen = 7;
const char* nickname = "Environment lobby";
const char* devEuiText = "70B3D57ED0008801";
const char* joinState = "JOINING";
const char* sensorName = "SHT4X";
bool environmentalReadingValid = true;
float lastTemp = 20, lastHum = 50;
uint8_t currentSpreadingFactor() { return 7; }
struct Screen {
    std::string text;
    unsigned flushes = 0;
    void clearDisplay() { text.clear(); }
    void setTextSize(int) {}
    void setTextColor(int) {}
    void setTextWrap(bool) {}
    void setCursor(int, int) { text += "\n"; }
    void drawFastHLine(int, int, int, int) {}
    void print(const char* value) { text += value; }
    void print(float value, int) { std::ostringstream output; output << value; text += output.str(); }
    template<typename T> void print(T value) { std::ostringstream output; output << value; text += output.str(); }
    template<typename T> void println(T value) { print(value); text += "\n"; }
    void display() { ++flushes; }
} display;
'''
    scenarios = r'''
int main() {
    renderDisplay();
    assert(display.text.find("DOWNLINK / CLASS A") != std::string::npos);
    assert(display.text.find("No downlink yet.") != std::string::npos);
    unsigned before = display.flushes;

    // The application port byte precedes the decrypted data in LMIC's frame.
    LMIC.txrxFlags = TXRX_PORT; LMIC.dataBeg = 2; LMIC.dataLen = 4;
    LMIC.frame[1] = 15; LMIC.frame[2] = 'H'; LMIC.frame[3] = 'i';
    LMIC.frame[4] = 0; LMIC.frame[5] = 0xff;
    captureApplicationDownlink();
    assert(display.flushes == before); // No blocking I2C in the radio callback.
    assert(downlinkObserver.count == 1 && downlinkObserver.port == 15);
    assert(downlinkObserver.length == 4 && downlinkObserver.bytes[3] == 0xff);
    assert(downlinkLogPending && displayDirty && observerPage == 0);
    memset(LMIC.frame, 0, sizeof(LMIC.frame)); // Radio frame reuse cannot erase the snapshot.
    renderDisplay();
    assert(display.text.find("Hi..") != std::string::npos);
    observerPage = downlinkObserver.textPages(); renderDisplay();
    assert(display.text.find("48 69 00 FF") != std::string::npos);

    // An ACK-only or MAC-only RX never becomes an application message.
    LMIC.txrxFlags = 0; LMIC.dataBeg = 0; LMIC.dataLen = 0;
    captureApplicationDownlink(); assert(downlinkObserver.count == 1);
    LMIC.txrxFlags = TXRX_PORT; LMIC.dataBeg = 2; LMIC.frame[1] = 0;
    captureApplicationDownlink(); assert(downlinkObserver.count == 1);
    LMIC.frame[1] = 1; LMIC.dataBeg = 63; LMIC.dataLen = 4;
    captureApplicationDownlink(); assert(downlinkObserver.count == 1);

    // Large binary payloads round-trip through every HEX page, with no clipping.
    uint8_t binary[255]; for (unsigned i = 0; i < 255; ++i) binary[i] = uint8_t(i);
    assert(downlinkObserver.capture(223, binary, sizeof(binary)));
    std::vector<unsigned> restored;
    struct { char row[22]; char guard; } line;
    for (size_t page = downlinkObserver.textPages(); page < downlinkObserver.pageCount(); ++page) {
        for (size_t row = 0; row < 4; ++row) {
            memset(line.row, 'X', sizeof(line.row)); line.guard = '!';
            downlinkObserver.formatRow(page, row, line.row);
            assert(line.guard == '!' && strlen(line.row) <= 21);
            std::istringstream input(line.row); unsigned value;
            while (input >> std::hex >> value) restored.push_back(value);
        }
    }
    assert(restored.size() == 255);
    for (unsigned i = 0; i < 255; ++i) assert(restored[i] == i);
    assert(!downlinkObserver.capture(1, binary, 256));
    assert(!downlinkObserver.capture(224, binary, 1));

    // Replacing a long message with a short one cannot display stale bytes.
    assert(downlinkObserver.capture(1, binary, 1));
    downlinkObserver.formatRow(0, 1, line.row); assert(line.row[0] == 0);
    assert(downlinkObserver.capture(1, nullptr, 0));
    downlinkObserver.formatRow(0, 0, line.row);
    assert(std::string(line.row) == "(empty payload)");
    observerJoined = false; renderDisplay();
    assert(display.text.find("State JOINING") != std::string::npos);
}
'''
    source = tmp_path / "observer.cpp"
    binary = tmp_path / "observer.exe"
    source.write_text(harness + capture + render + scenarios, encoding="utf-8")
    subprocess.run(
        [compiler, "-std=c++11", "-Wall", "-Wextra", "-pedantic", "-I", str(folder), str(source), "-o", str(binary)],
        check=True, capture_output=True, text=True,
    )
    subprocess.run([str(binary)], check=True, capture_output=True, text=True)
