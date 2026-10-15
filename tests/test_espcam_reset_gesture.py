"""Exercise the sketch's actual startup recovery code across simulated resets."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_espcam_reset_and_calibration_gestures(tmp_path):
    compiler = shutil.which("g++")
    if not compiler:
        pytest.skip("g++ is required for the firmware recovery simulation")
    sketch = (ROOT / "firmware/espcam_lorawan_node/espcam_lorawan_node.ino").read_text(encoding="utf-8")
    start = sketch.index("static bool calibrationRequested(bool& forgetNickname) {")
    end = sketch.index("\nstatic void buildAccessPointCredentials()", start)
    definitions = "\n".join(
        re.search(rf"^#define {name} .*$", sketch, re.M).group(0)
        for name in ("PIN_BOOT_NICKNAME", "RENAME_HOLD_MS", "RENAME_WINDOW_MS")
    )
    prefix = r'''
#include <cassert>
#include <climits>
#include <cstring>
#include <iostream>
#include <utility>
#include <vector>
#define LOW 0
#define HIGH 1
#define INPUT_PULLUP 2
#define SSD1306_WHITE 1
#define F(x) x
struct HardwareReset {};
unsigned long now = 0, resetAt = 0;
unsigned framesDrained = 0, nicknameClears = 0;
bool wasDeployed = true, deployedStored = true;
bool resetDuringNameClear = false, heldContinuously = false;
bool oledReady = true;
char nickname[49] = "Meter Camera";
enum { ESP_RST_POWERON, ESP_RST_SW, ESP_RST_PANIC, ESP_RST_WDT,
       ESP_RST_INT_WDT, ESP_RST_TASK_WDT, ESP_RST_BROWNOUT, ESP_RST_DEEPSLEEP };
int reason = ESP_RST_POWERON;
std::vector<std::pair<unsigned long, unsigned long>> presses;
unsigned long millis() { return now; }
void delay(unsigned long duration) {
    now += duration;
    if (resetAt && now >= resetAt) throw HardwareReset();
}
void receiveFrame() { ++framesDrained; }
void pinMode(int pin, int mode) { assert(pin == 0 && mode == INPUT_PULLUP); }
int digitalRead(int pin) {
    assert(pin == 0);
    if (heldContinuously) return LOW;
    for (const auto &press : presses)
        if (now >= press.first && now < press.second) return LOW;
    return HIGH;
}
int esp_reset_reason() { return reason; }
struct { void println(const char*) {} } Serial;
struct {
    void clearDisplay() {}
    void setTextSize(int) {}
    void setTextColor(int) {}
    void setCursor(int, int) {}
    void println(const char*) {}
    void drawFastHLine(int, int, int, int) {}
    void display() {}
} display;
struct Preferences {
    bool open = false, marker = false;
    bool available = true, writeFails = false, removeFails = false;
    bool begin(const char*, bool) { assert(!open); return open = available; }
    void end() { assert(open); open = false; }
    bool getBool(const char*, bool) { assert(open); return marker; }
    unsigned putBool(const char*, bool value) {
        assert(open);
        if (writeFails) return 0;
        marker = value;
        return 1;
    }
    bool remove(const char*) {
        assert(open);
        if (removeFails) return false;
        marker = false;
        return true;
    }
} preferences;
void saveDeployedFlag(bool value) { deployedStored = value; }
void saveNickname(const char* value) {
    assert(std::strcmp(value, "-") == 0);
    assert(!deployedStored); // An interrupted rename must reboot into setup.
    if (resetDuringNameClear) throw HardwareReset();
    nickname[0] = 0;
    ++nicknameClears;
}
void fresh() {
    preferences = Preferences();
    now = resetAt = 0; framesDrained = nicknameClears = 0;
    wasDeployed = deployedStored = oledReady = true;
    resetDuringNameClear = heldContinuously = false;
    reason = ESP_RST_POWERON; presses.clear();
    std::strcpy(nickname, "Meter Camera");
}
void assertDeployed() {
    assert(wasDeployed && deployedStored && nicknameClears == 0);
    assert(std::strcmp(nickname, "Meter Camera") == 0);
    assert(!preferences.open);
}
'''
    scenarios = r'''
int main() {
    unsigned scenarios = 0;
    fresh(); applyStartupRequest();
    assertDeployed();
    assert(now == RENAME_WINDOW_MS && !preferences.marker && framesDrained > 0);
    now = 0; applyStartupRequest(); // A later single reset is still ordinary.
    assertDeployed(); assert(!preferences.marker);
    ++scenarios;

    fresh(); resetAt = 1020; // First prompt interrupted by the second RST.
    try { applyStartupRequest(); assert(false); } catch (const HardwareReset&) {}
    assertDeployed(); assert(preferences.marker && framesDrained > 0);
    now = resetAt = 0; applyStartupRequest();
    assert(!wasDeployed && !deployedStored && nickname[0] == 0 && nicknameClears == 1);
    assert(!preferences.marker && !preferences.open && now == 0);
    applyStartupRequest(); // Setup boot must not re-arm a rename window.
    assert(!preferences.marker && now == 0);
    ++scenarios;

    fresh(); heldContinuously = true; applyStartupRequest();
    assert(!wasDeployed && !deployedStored && nicknameClears == 0);
    assert(std::strcmp(nickname, "Meter Camera") == 0);
    assert(now == RENAME_HOLD_MS && !preferences.marker && framesDrained > 0);
    ++scenarios;

    fresh(); presses = {{0, 1980}}; applyStartupRequest();
    assertDeployed(); assert(!preferences.marker); ++scenarios;
    fresh(); presses = {{0, 1000}, {1020, 2500}}; applyStartupRequest();
    assertDeployed(); assert(!preferences.marker); ++scenarios;

    for (int restart : {ESP_RST_SW, ESP_RST_PANIC, ESP_RST_WDT, ESP_RST_INT_WDT,
                       ESP_RST_TASK_WDT, ESP_RST_BROWNOUT, ESP_RST_DEEPSLEEP}) {
        fresh(); preferences.marker = true; reason = restart;
        applyStartupRequest(); assertDeployed(); assert(!preferences.marker);
        ++scenarios;
    }

    fresh(); wasDeployed = deployedStored = false; preferences.marker = true;
    applyStartupRequest();
    assert(!wasDeployed && !deployedStored && !preferences.marker && now == 0);
    assert(nicknameClears == 0); ++scenarios;
    fresh(); wasDeployed = false; nickname[0] = 0;
    applyStartupRequest(); assert(!preferences.marker && now == 0);
    ++scenarios;

    fresh(); oledReady = false; preferences.marker = true;
    applyStartupRequest();
    assert(!wasDeployed && !deployedStored && nickname[0] == 0);
    ++scenarios;

    fresh(); preferences.available = false; applyStartupRequest();
    assertDeployed(); ++scenarios;
    fresh(); preferences.available = false; heldContinuously = true;
    applyStartupRequest();
    assert(!wasDeployed && nicknameClears == 0); ++scenarios;
    fresh(); preferences.writeFails = true; applyStartupRequest();
    assertDeployed(); assert(!preferences.marker); ++scenarios;
    fresh(); preferences.writeFails = true; heldContinuously = true;
    applyStartupRequest();
    assert(!wasDeployed && nicknameClears == 0 && !preferences.marker); ++scenarios;
    fresh(); preferences.marker = true; preferences.removeFails = true;
    applyStartupRequest(); assertDeployed(); ++scenarios;

    fresh(); preferences.marker = true; resetDuringNameClear = true;
    try { applyStartupRequest(); assert(false); } catch (const HardwareReset&) {}
    assert(!deployedStored && !preferences.marker);
    wasDeployed = deployedStored && std::strlen(nickname); // Simulate next boot.
    resetDuringNameClear = false; applyStartupRequest();
    assert(!wasDeployed && now == 0); ++scenarios;

    fresh(); now = ULONG_MAX - 1000; applyStartupRequest();
    assertDeployed(); assert(!preferences.marker && now == 2999);
    ++scenarios;
    std::cout << scenarios << " ESPCAM reset/calibration scenarios passed.\n";
}
'''
    source = tmp_path / "espcam_recovery.cpp"
    binary = tmp_path / "espcam_recovery.exe"
    source.write_text(prefix + definitions + "\n" + sketch[start:end] + scenarios, encoding="utf-8")
    compiled = subprocess.run(
        [compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror", str(source), "-o", str(binary)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert compiled.returncode == 0, compiled.stderr
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "22 ESPCAM reset/calibration scenarios passed." in result.stdout
