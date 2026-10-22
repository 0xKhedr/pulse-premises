"""Run the SR04 sketch's actual rename logic with simulated buttons and resets."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("enable_oled", [0, 1])
def test_sr04_rename_and_serial_feedback(tmp_path, enable_oled):
    compiler = shutil.which("g++")
    if not compiler:
        pytest.skip("g++ is required for the firmware reset simulation")
    sketch = (ROOT / "firmware/lilygo_sr04_lpp_node/lilygo_sr04_lpp_node.ino").read_text(
        encoding="utf-8"
    )
    start = sketch.index("static bool renameRequested() {")
    end = sketch.index("\n#if ENABLE_OLED\n// EU868", start)
    definitions = "\n".join(
        re.search(rf"^#define {name} .*$", sketch, re.M).group(0)
        for name in ("PIN_RESET_NICKNAME", "RENAME_HOLD_MS", "RENAME_WINDOW_MS")
    )
    harness = r'''
#include <cassert>
#include <climits>
#include <cstdio>
#include <cstring>
#include <string>
#include <utility>
#include <vector>
#define F(x) x
#define LOW 0
#define HIGH 1
struct HardwareReset {};
unsigned long now = 0, resetAt = 0;
enum { ESP_RST_POWERON, ESP_RST_SW, ESP_RST_PANIC, ESP_RST_WDT,
       ESP_RST_INT_WDT, ESP_RST_TASK_WDT, ESP_RST_BROWNOUT, ESP_RST_DEEPSLEEP };
int reason = ESP_RST_POWERON;
unsigned long millis() { return now; }
void delay(unsigned long duration) {
    now += duration;
    if (resetAt && now >= resetAt) throw HardwareReset();
}
int esp_reset_reason() { return reason; }
std::vector<std::pair<unsigned long, unsigned long>> presses;
int digitalRead(int) {
    for (const auto &press : presses)
        if (now >= press.first && now < press.second) return LOW;
    return HIGH;
}
struct {
    std::string log;
    void println(const char* value) { log += value; log += '\n'; }
    template<typename... Args> void printf(const char* format, Args... args) {
        char line[256]; std::snprintf(line, sizeof(line), format, args...); log += line;
    }
} Serial;
#if ENABLE_OLED
bool oledReady = false; // Serial must work when the configured screen is absent.
struct {
    void clearDisplay() {}
    void setCursor(int, int) {}
    void println(const char*) {}
    void display() {}
} display;
#endif
char nickname[49] = "distance_tank_01";
struct Preferences {
    bool open = false, marker = false, available = true;
    bool writeFails = false, removeFails = false;
    bool begin(const char*, bool) { assert(!open); return open = available; }
    void end() { assert(open); open = false; }
    bool getBool(const char*, bool) { assert(open); return marker; }
    unsigned putBool(const char*, bool value) {
        assert(open); if (writeFails) return 0; marker = value; return 1;
    }
    bool remove(const char*) {
        assert(open); if (removeFails) return false; marker = false; return true;
    }
} preferences;
void fresh() {
    preferences = Preferences(); now = resetAt = 0; reason = ESP_RST_POWERON;
    presses.clear(); Serial.log.clear(); std::strcpy(nickname, "distance_tank_01");
}
bool logged(const char* value) { return Serial.log.find(value) != std::string::npos; }
'''
    scenarios = r'''
int main() {
    fresh(); assert(!renameRequested());
    assert(now == RENAME_WINDOW_MS && !preferences.marker && !preferences.open);
    assert(std::strcmp(nickname, "distance_tank_01") == 0);
    assert(logged("Press RST again within 4 seconds"));
    for (int seconds = 4; seconds >= 1; --seconds)
        assert(logged((std::to_string(seconds) + " second(s) remaining.").c_str()));
    assert(logged("Window closed. Keeping the saved nickname"));
    now = 0; assert(!renameRequested()); // A later ordinary RST keeps the name.

    fresh(); resetAt = 1020;
    try { renameRequested(); assert(false); } catch (const HardwareReset&) {}
    assert(preferences.marker && !preferences.open);
    now = resetAt = 0;
    assert(renameRequested());
    assert(!preferences.marker && !preferences.open && now == 0);
    assert(logged("Second RST detected. Opening naming mode."));

    fresh(); presses = {{0, 2100}};
    assert(renameRequested() && now == RENAME_HOLD_MS && !preferences.marker);
    assert(logged("BOOT pressed; keep holding"));
    assert(logged("BOOT held for 2 seconds. Opening naming mode."));

    fresh(); presses = {{0, 1980}};
    assert(!renameRequested() && !preferences.marker);
    assert(logged("BOOT released too soon; hold cancelled."));
    fresh(); presses = {{0, 1000}, {1020, 2500}};
    assert(!renameRequested() && !preferences.marker); // Holds do not accumulate.

    for (int restart : {ESP_RST_SW, ESP_RST_PANIC, ESP_RST_WDT, ESP_RST_INT_WDT,
                       ESP_RST_TASK_WDT, ESP_RST_BROWNOUT, ESP_RST_DEEPSLEEP}) {
        fresh(); preferences.marker = true; reason = restart;
        assert(!renameRequested() && !preferences.marker && !preferences.open);
        assert(!logged("Second RST detected"));
    }

    fresh(); nickname[0] = 0; preferences.marker = true;
    assert(!renameRequested() && now == 0 && !preferences.marker);
    fresh(); preferences.available = false;
    assert(!renameRequested() && !preferences.open);
    assert(logged("RST rename unavailable"));
    fresh(); preferences.available = false; presses = {{0, 2100}};
    assert(renameRequested()); // BOOT still works without the RST marker.
    fresh(); preferences.writeFails = true;
    assert(!renameRequested() && !preferences.marker);
    fresh(); preferences.marker = true; preferences.removeFails = true;
    assert(!renameRequested() && !preferences.open);
    assert(logged("Could not clear rename marker; keeping the saved nickname."));

    fresh(); now = ULONG_MAX - 1000;
    assert(!renameRequested() && !preferences.marker && now == 2999);
#if ENABLE_OLED
    fresh(); oledReady = true; assert(!renameRequested());
    assert(logged("Press RST again within 4 seconds"));
#endif
}
'''
    source = tmp_path / "sr04_rename.cpp"
    binary = tmp_path / "sr04_rename.exe"
    source.write_text(
        f"#define ENABLE_OLED {enable_oled}\n" + harness + definitions
        + "\n" + sketch[start:end] + scenarios,
        encoding="utf-8",
    )
    compiled = subprocess.run(
        [compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror", str(source), "-o", str(binary)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert compiled.returncode == 0, compiled.stderr
    result = subprocess.run(
        [str(binary)], capture_output=True, text=True, timeout=10, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
