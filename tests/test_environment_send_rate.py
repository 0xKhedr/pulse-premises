"""Compile the real configuration handler/timer with simulated NVS and LMIC."""

from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize("node", ["lilygo_environment_lpp_node", "lilygo_solar_lpp_node"])
def test_environment_send_rate_persists_validates_and_schedules_without_overflow(tmp_path, node):
    compiler = shutil.which("g++")
    if not compiler:
        pytest.skip("g++ is required for the firmware simulation")
    folder = Path(__file__).resolve().parents[1] / "firmware" / node
    sketch = (folder / f"{node}.ino").read_text(encoding="utf-8")
    functions = sketch[sketch.index("void do_send(osjob_t* j);"):sketch.index("bool initUniqueDevEUI() {")]
    harness = r'''
#include <cassert>
#include <cstring>
#include <map>
#include <string>
#include "SendRateConfig.h"
#include "DownlinkObserver.h"
#define F(x) x
#define OP_TXRXPEND 0x80
struct { unsigned opmode = 0; } LMIC;
struct { void println(const char*) {} void printf(const char*, ...) {} } Serial;
struct osjob_t {} sendjob;
uint32_t txIntervalSeconds = SendRateConfig::DefaultSeconds;
DownlinkObserver downlinkObserver;
bool failOpen = false, failWrite = false;
std::map<std::string, uint32_t> flash;
unsigned writes = 0, ends = 0, scheduled = 0, sent = 0;
struct Preferences {
    bool begin(const char* ns, bool) { assert(std::string(ns) == "sbms"); return !failOpen; }
    uint32_t getUInt(const char* key, uint32_t fallback) { return flash.count(key) ? flash[key] : fallback; }
    size_t putUInt(const char* key, uint32_t value) {
        ++writes;
        if (failWrite) return 0;
        flash[key] = value; return sizeof(uint32_t);
    }
    void end() { ++ends; }
};
uint32_t now = 0, deadline = 0;
void (*callback)(osjob_t*) = nullptr;
uint32_t os_getTime() { return now; }
uint32_t sec2osticks(uint32_t seconds) { assert(seconds <= 3600); return seconds * 32768; }
void os_setTimedCallback(osjob_t* job, uint32_t at, void (*fn)(osjob_t*)) {
    assert(job == &sendjob); deadline = at; callback = fn; ++scheduled;
}
void do_send(osjob_t*) { ++sent; }
'''
    scenarios = r'''
void receive(uint32_t seconds, uint8_t port = 42, uint8_t version = 1, uint8_t command = 1) {
    uint8_t bytes[] = {version, command, uint8_t(seconds >> 24), uint8_t(seconds >> 16),
                       uint8_t(seconds >> 8), uint8_t(seconds)};
    assert(downlinkObserver.capture(port, bytes, sizeof(bytes)));
    applyObservedConfiguration();
}
int main() {
    loadSendRate(); assert(txIntervalSeconds == 30);
    uint32_t seconds = 123;
    const uint8_t fiveMinutes[] = {1, 1, 0, 0, 1, 0x2c};
    assert(!SendRateConfig::decode(42, nullptr, 6, seconds));
    for (size_t length = 0; length < 6; ++length)
        assert(!SendRateConfig::decode(42, fiveMinutes, length, seconds));
    uint8_t longer[] = {1, 1, 0, 0, 1, 0x2c, 0};
    assert(!SendRateConfig::decode(42, longer, 7, seconds));
    assert(seconds == 123);
    assert(SendRateConfig::decode(42, fiveMinutes, 6, seconds) && seconds == 300);

    scheduleNextUplink(); assert(deadline == 30 * 32768);
    receive(300);
    assert(txIntervalSeconds == 300 && flash["send_interval"] == 300 && writes == 1);
    assert(deadline == 300 * 32768 && sent == 0);
    unsigned before = scheduled;
    applyObservedConfiguration(); receive(300);
    assert(writes == 1 && scheduled == before); // Duplicate is idempotent.
    txIntervalSeconds = 30; loadSendRate(); assert(txIntervalSeconds == 300); // Reboot.

    receive(0); receive(29); receive(86401); receive(0xffffffff);
    receive(600, 1); receive(600, 10); receive(600, 42, 2); receive(600, 42, 1, 2);
    assert(txIntervalSeconds == 300 && writes == 1 && scheduled == before);
    assert(downlinkObserver.capture(42, nullptr, 0)); applyObservedConfiguration();
    assert(txIntervalSeconds == 300 && writes == 1);

    LMIC.opmode = OP_TXRXPEND; receive(600);
    assert(txIntervalSeconds == 300 && writes == 1);
    LMIC.opmode = 0; applyObservedConfiguration(); assert(txIntervalSeconds == 600 && writes == 2);
    before = scheduled;
    failOpen = true; receive(900); assert(txIntervalSeconds == 600 && scheduled == before);
    failOpen = false; failWrite = true; receive(900);
    assert(txIntervalSeconds == 600 && flash["send_interval"] == 600 && scheduled == before);
    failWrite = false; receive(900); assert(txIntervalSeconds == 900);

    receive(86400); assert(txIntervalSeconds == 86400);
    // All timer chunks stay in signed 32-bit tick range, including across rollover.
    now = 0xffff0000; scheduleNextUplink();
    uint64_t elapsed = 0;
    for (unsigned hour = 0; hour < 24; ++hour) {
        assert(sent == 0 && deadline - now == 3600 * 32768);
        elapsed += deadline - now; now = deadline; callback(&sendjob);
    }
    assert(sent == 1 && elapsed == uint64_t(86400) * 32768);
    txIntervalSeconds = 30; flash["send_interval"] = 5; loadSendRate();
    assert(txIntervalSeconds == 30); // Invalid flash falls back to default on boot.
    receive(30); assert(txIntervalSeconds == 30);
}
'''
    source = tmp_path / "send-rate.cpp"
    binary = tmp_path / "send-rate.exe"
    if node == "lilygo_solar_lpp_node":
        for name in ("SendRateConfig", "DownlinkObserver"):
            inline = sketch.split(f"// BEGIN INLINE {name}\n", 1)[1].split(f"// END INLINE {name}", 1)[0]
            harness = harness.replace(f'#include "{name}.h"', inline)
    source.write_text(harness + functions + scenarios, encoding="utf-8")
    result = subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-pedantic", "-I", str(folder),
                             str(source), "-o", str(binary)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    result = subprocess.run([str(binary)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
