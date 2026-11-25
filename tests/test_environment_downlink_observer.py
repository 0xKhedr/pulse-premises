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
    helpers = sketch.split("// BEGIN OLED UI HELPERS\n", 1)[1].split("// END OLED UI HELPERS", 1)[0]
    setup_start = sketch.index("static void renderProvisioningScreen(")
    setup = sketch[setup_start:sketch.index("\n#endif", setup_start)]
    harness = r'''
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cmath>
using std::isfinite;
#include <string>
#include <sstream>
#include "DownlinkObserver.h"
#define ENABLE_OLED 1
#define F(x) x
#define TXRX_PORT 0x10
#define SSD1306_WHITE 1
#define SSD1306_BLACK 0
#define OLED_WIDTH 128
#define RESET_WINDOW_MS 3000
struct { uint8_t frame[64] = {}; uint8_t dataBeg = 0, dataLen = 0, txrxFlags = 0; } LMIC;
DownlinkObserver downlinkObserver;
bool downlinkLogPending = false, displayDirty = false, oledReady = true, observerJoined = true;
size_t observerPage = 0;
unsigned long lastObserverPageAt = 0;
uint32_t now = 10000;
unsigned long millis() { return now; }
bool nextUplinkScheduled = true;
uint32_t nextUplinkScheduledAt = 10000, txIntervalSeconds = 30;
unsigned uplinkCount = 2, lastPayloadLen = 7;
const char* nickname = "Environment lobby";
const char* devEuiText = "70B3D57ED0008801";
const char* joinState = "JOINING";
const char* sensorName = "SHT4X";
bool environmentalReadingValid = true;
float lastTemp = 20, lastHum = 50;
const char* apSsid = "SBMS-8801";
const char* apPassword = "AbcD2345";
uint8_t spreadingFactor = 7;
uint8_t currentSpreadingFactor() { return spreadingFactor; }
struct Screen {
    std::string text;
    unsigned flushes = 0;
    int x = 0, y = 0, scale = 1, color = 1;
    std::string drawing;
    void clearDisplay() { text.clear(); drawing.clear(); }
    void setTextSize(int value) { scale = value; }
    void setTextColor(int value) { color = value; }
    void setTextWrap(bool) {}
    void setCursor(int left, int top) { x = left; y = top; text += "\n"; }
    void rectangle(char type, int left, int top, int width, int height, int value) {
        assert(left >= 0 && top >= 0 && width >= 0 && height >= 0);
        assert(left + width <= 128 && top + height <= 64);
        drawing += std::string(1, type) + " " + std::to_string(left) + " " + std::to_string(top)
            + " " + std::to_string(width) + " " + std::to_string(height) + " " + std::to_string(value) + "\n";
    }
    void fillRect(int left, int top, int width, int height, int value) { rectangle('F', left, top, width, height, value); }
    void drawRect(int left, int top, int width, int height, int value) { rectangle('R', left, top, width, height, value); }
    void drawFastHLine(int left, int top, int width, int value) { rectangle('F', left, top, width, 1, value); }
    void drawFastVLine(int left, int top, int height, int value) { rectangle('F', left, top, 1, height, value); }
    void write(char value) {
        assert(x >= 0 && y >= 0 && x + 5 * scale <= 128 && y + 7 * scale <= 64);
        // The main screen's lower panels must never overlap the divider.
        if (y >= 21 && text.find("EUI ") != std::string::npos)
            assert((x < 65 && x + 5 * scale <= 63) || x >= 69);
        drawing += "C " + std::to_string(x) + " " + std::to_string(y) + " " + std::to_string(scale)
            + " " + std::to_string(color) + " " + std::to_string(static_cast<unsigned char>(value)) + "\n";
        text += value; x += 6 * scale;
    }
    void print(const char* value) { while (*value) write(*value++); }
    template<typename T> void print(T value) { std::ostringstream output; output << value; print(output.str().c_str()); }
    void display() { ++flushes; }
    void dump(const char* name) {
        if (!std::getenv("OLED_PREVIEW_DIR")) return;
        const std::string path = std::string(std::getenv("OLED_PREVIEW_DIR")) + "/" + name + ".txt";
        FILE* file = std::fopen(path.c_str(), "w"); assert(file);
        std::fputs(drawing.c_str(), file); std::fclose(file);
    }
} display;
'''
    scenarios = r'''
int main() {
    renderDisplay();
    assert(display.text.find("Environment lobby") != std::string::npos);
    assert(display.text.find("70B3D57ED0008801") != std::string::npos);
    assert(display.text.find("LORA SF7") != std::string::npos);
    assert(display.text.find("ONLINE") != std::string::npos);
    assert(display.text.find("UP 00:30") != std::string::npos);
    assert(display.text.find("None yet") != std::string::npos);
    display.dump("dashboard");
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
    assert(display.text.find("P15 4B") != std::string::npos);
    observerPage = 1; renderDisplay();
    assert(display.text.find("T:Hi..") != std::string::npos);
    assert(display.text.find("ONLINE") != std::string::npos);
    display.dump("downlink");
    assert(downlinkPanelPages() == 2);
    observerPage = 2; renderDisplay();
    assert(display.text.find("P15 4B") != std::string::npos); // Wrap straight back to metadata.

    // An ACK-only or MAC-only RX never becomes an application message.
    LMIC.txrxFlags = 0; LMIC.dataBeg = 0; LMIC.dataLen = 0;
    captureApplicationDownlink(); assert(downlinkObserver.count == 1);
    LMIC.txrxFlags = TXRX_PORT; LMIC.dataBeg = 2; LMIC.frame[1] = 0;
    captureApplicationDownlink(); assert(downlinkObserver.count == 1);
    LMIC.frame[1] = 1; LMIC.dataBeg = 63; LMIC.dataLen = 4;
    captureApplicationDownlink(); assert(downlinkObserver.count == 1);

    // Capture preserves binary bytes; the OLED pages only sanitized text.
    uint8_t binary[255]; for (unsigned i = 0; i < 255; ++i) binary[i] = uint8_t(i);
    assert(downlinkObserver.capture(223, binary, sizeof(binary)));
    assert(memcmp(downlinkObserver.bytes, binary, sizeof(binary)) == 0);
    std::string restored;
    assert(downlinkPanelPages() == 33); // Metadata plus 32 eight-character text segments.
    struct { char text[11]; char guard = '!'; } compact;
    for (size_t page = 0; page < downlinkPanelPages(); ++page) {
        downlinkPanelText(compact.text, page);
        assert(compact.guard == '!' && strlen(compact.text) <= 10);
        observerPage = page; renderDisplay(); // Validate all panel coordinates.
        assert(display.text.find("H:") == std::string::npos);
        if (!page) continue;
        assert(strncmp(compact.text, "T:", 2) == 0);
        restored += compact.text + 2;
    }
    assert(restored.size() == 255);
    for (unsigned i = 0; i < 255; ++i)
        assert(restored[i] == (i >= 32 && i <= 126 ? char(i) : '.'));
    assert(!downlinkObserver.capture(1, binary, 256));
    assert(!downlinkObserver.capture(224, binary, 1));

    // Replacing a long message with a short one cannot display stale bytes.
    assert(downlinkObserver.capture(1, binary, 1));
    downlinkPanelText(compact.text, 1); assert(std::string(compact.text) == "T:.");
    assert(downlinkObserver.capture(1, nullptr, 0));
    spreadingFactor = 12; renderDisplay();
    assert(display.text.find("LORA SF12") != std::string::npos);
    spreadingFactor = 7;
    assert(downlinkPanelPages() == 1);
    observerPage = 1000; renderDisplay();
    assert(display.text.find("P1 0B") != std::string::npos);
    observerJoined = false; renderDisplay();
    assert(display.text.find("JOINING") != std::string::npos);
    joinState = "WAITING JOIN"; renderDisplay();
    assert(display.text.find("WAIT JOIN") != std::string::npos);

    // Countdown uses elapsed milliseconds, rounds up, and survives millis rollover.
    now = 10999; assert(remainingUplinkSeconds() == 30);
    now = 11000; assert(remainingUplinkSeconds() == 29);
    now = 40000; renderDisplay();
    assert(display.text.find("UP DUE") != std::string::npos);
    now = 90000; assert(remainingUplinkSeconds() == 0);
    nextUplinkScheduledAt = 0xfffffff0u; now = 984;
    assert(remainingUplinkSeconds() == 29);
    nextUplinkScheduledAt = now;
    txIntervalSeconds = 86400; renderDisplay();
    assert(display.text.find("UP 24h00") != std::string::npos);
    txIntervalSeconds = 3600; renderDisplay();
    assert(display.text.find("UP 1h00") != std::string::npos);
    txIntervalSeconds = 3599; renderDisplay();
    assert(display.text.find("UP 59:59") != std::string::npos);
    nextUplinkScheduled = false; observerJoined = true; renderDisplay();
    assert(display.text.find("UP QUEUED") != std::string::npos);
    observerJoined = false; renderDisplay();
    assert(display.text.find("UP --:--") != std::string::npos);

    // Extreme valid readings and errors cannot overflow the sensor panel.
    lastTemp = -40.0f; lastHum = 100.0f; sensorName = "BME280";
    renderDisplay();
    assert(display.text.find("-40.0") != std::string::npos);
    assert(display.text.find("100") != std::string::npos);
    display.dump("extreme-readings");
    environmentalReadingValid = false; renderDisplay();
    assert(display.text.find("BME280 !") != std::string::npos);
    assert(display.text.find("-40.0") == std::string::npos);
    sensorName = "NOT DETECTED"; renderDisplay();
    assert(display.text.find("NOT FOUND") != std::string::npos);
    display.dump("sensor-missing");
    nickname = "123456789012345678901234567890123456789012345678";
    for (now = 0; now < 35000; now += 1000) renderDisplay();
    downlinkObserver.count = 0xffffffffu; renderDisplay();
    assert(display.text.find("DOWN #999+") != std::string::npos);

    renderProvisioningScreen("1 phone connected"); display.dump("setup");
    assert(display.text.find(apSsid) != std::string::npos);
    assert(display.text.find(apPassword) != std::string::npos);
    assert(display.text.find("192.168.4.1") != std::string::npos);
    nickname = "Environment lobby";
    for (unsigned ms = 3000; ms >= 100; ms -= 100) renderResetScreen(ms);
    renderResetScreen(2000); display.dump("reset");
    assert(display.text.find("2s") != std::string::npos);
    renderMessageScreen("SETUP COMPLETE", "Name saved", nickname, "Joining LoRaWAN...");
    display.dump("saved");
    renderMessageScreen("RADIO ERROR", "Check board revision", "and SX1276 pins", "Restart after fixing");
    display.dump("radio-error");
    before = display.flushes; oledReady = false;
    renderDisplay(); renderResetScreen(1000); renderProvisioningScreen("test");
    assert(display.flushes == before);
}
'''
    source = tmp_path / "observer.cpp"
    binary = tmp_path / "observer.exe"
    source.write_text(harness + capture + helpers + setup + render + scenarios, encoding="utf-8")
    subprocess.run(
        [compiler, "-std=c++11", "-Wall", "-Wextra", "-pedantic", "-I", str(folder), str(source), "-o", str(binary)],
        check=True, capture_output=True, text=True,
    )
    subprocess.run([str(binary)], check=True, capture_output=True, text=True)
