"""Exercise the firmware's actual reset-window function across simulated boots."""

from pathlib import Path
import re
import shutil
import subprocess

import pytest


def test_reset_window_survives_reset_and_is_consumed_once(tmp_path):
    compiler = shutil.which("g++")
    if not compiler:
        pytest.skip("g++ is required for the reset-window simulation")
    sketch = (Path(__file__).resolve().parents[1] /
              "firmware/lilygo_environment_lpp_node/lilygo_environment_lpp_node.ino").read_text()
    start = sketch.index("static bool renameRequested() {")
    function = sketch[start:sketch.index("\n#if ENABLE_OLED\n// EU868", start)]
    window = re.search(r"^#define RESET_WINDOW_MS\s+\d+", sketch, re.MULTILINE).group()
    harness = r'''
#include <cassert>
#include <cstddef>
#include <map>
#include <string>
#define ENABLE_OLED 1
#define F(value) value
bool oledReady = true;
struct {
    void clearDisplay() {}
    void setCursor(int, int) {}
    void println(const char*) {}
    void display() {}
} display;
struct {
    void println(const char*) {}
    void printf(const char*, ...) {}
} Serial;
std::map<std::string, bool> flash;
bool failOpen = false, failWrite = false, resetDuringWindow = false;
unsigned elapsed = 0;
struct PowerReset {};
struct Preferences {
    bool begin(const char*, bool) { return !failOpen; }
    bool getBool(const char* key, bool fallback) {
        return flash.count(key) ? flash[key] : fallback;
    }
    size_t putBool(const char* key, bool value) {
        if (failWrite) return 0;
        flash[key] = value;
        return 1;
    }
    void end() {}
};
void delay(unsigned ms) {
    assert(flash["rename_pending"]);
    if (resetDuringWindow) throw PowerReset{};
    elapsed += ms;
}
'''
    scenarios = r'''
int main() {
    // An uninterrupted startup keeps the name and clears the marker.
    assert(!renameRequested());
    assert(elapsed == 3000 && !flash["rename_pending"]);

    // Reset kills execution during the wait, while flash retains the marker.
    resetDuringWindow = true;
    try { renameRequested(); assert(false); } catch (PowerReset&) {}
    assert(flash["rename_pending"]);
    resetDuringWindow = false;
    elapsed = 0;
    assert(renameRequested());
    assert(elapsed == 0 && !flash["rename_pending"]);

    // Reboot after configuring cannot trigger another rename from a stale flag.
    assert(!renameRequested());
    assert(elapsed == 3000 && !flash["rename_pending"]);

    // Storage failures must not erase an existing nickname or pretend to arm.
    elapsed = 0;
    failOpen = true;
    assert(!renameRequested() && elapsed == 0);
    failOpen = false;
    failWrite = true;
    assert(!renameRequested() && elapsed == 0 && !flash["rename_pending"]);
}
'''
    source = tmp_path / "reset_window.cpp"
    binary = tmp_path / "reset_window.exe"
    source.write_text(harness + window + "\n" + function + scenarios)
    subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-pedantic",
                    str(source), "-o", str(binary)],
                   check=True, capture_output=True, text=True)
    subprocess.run([str(binary)], check=True, capture_output=True, text=True)
