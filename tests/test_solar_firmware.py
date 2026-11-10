"""Exercise the solar sketch's actual sampling and transmit functions on a host."""

from pathlib import Path
import shutil
import subprocess

import pytest


def test_solar_sampling_and_uplinks(tmp_path):
    compiler = shutil.which("g++")
    if not compiler:
        pytest.skip("g++ is required for the firmware simulation")
    folder = Path(__file__).resolve().parents[1] / "firmware/lilygo_solar_lpp_node"
    sketch = (folder / "lilygo_solar_lpp_node.ino").read_text(encoding="utf-8")
    poll = sketch[sketch.index("static void pollSolarSensors() {"):
                  sketch.index("void loadNickname() {")]
    send_start = sketch.index("void do_send(osjob_t* j) {")
    send = sketch[send_start:sketch.index("void onEvent (ev_t ev)", send_start)]
    harness = r'''
#include <cassert>
#include <cstring>
#include <vector>
#include "SolarReadings.h"
#define ENABLE_OLED 0
#define F(x) x
#define OP_TXRXPEND 0x80
#define LORA_PORT_NICKNAME 10
#define LORA_PORT_DATA 1
struct { unsigned opmode = 0; } LMIC;
struct {
    void println(const char*) {}
    template<typename T> void print(T) {}
    void printf(const char*, ...) {}
} Serial;
uint32_t now = 0;
uint32_t millis() { return now; }
bool critical = false;
uint32_t ms2osticks(uint32_t ms) { return ms; }
bool os_queryTimeCriticalJobs(uint32_t ms) { assert(ms == 100); return critical; }
const uint8_t MPU_ADDRESS = 0x68;
const uint32_t SENSOR_INTERVAL_MS = 2000, CONVERSION_MS = 750;
bool mpuAvailable = true, probe1Powered = true, probe2Powered = true;
bool conversionPending = false, connected = true;
uint32_t conversionStartedAt = 0;
SolarReadings solar;
struct Probe {
    unsigned requests = 0, reads = 0;
    float value = 25;
    void requestTemperatures() { ++requests; }
    float getTempCByIndex(int index) { assert(index == 0); ++reads; return value; }
} sensor1, sensor2;
struct sensors_event_t { struct { float x = 0, y = 0, z = 0; } acceleration; };
struct {
    unsigned reads = 0;
    bool getEvent(sensors_event_t* a, sensors_event_t*, sensors_event_t*) {
        ++reads; a->acceleration.y = 9.81f; return true;
    }
} mpu;
bool i2cAddressResponds(uint8_t address) { assert(address == MPU_ADDRESS); return connected; }
struct Payload {
    struct Field { int channel, type; float value; };
    std::vector<Field> fields;
    uint8_t bytes[51] = {};
    void reset() { fields.clear(); }
    void addTemperature(int ch, float v) { fields.push_back({ch, 103, v}); }
    void addAnalogInput(int ch, float v) { fields.push_back({ch, 2, v}); }
    void addDigitalInput(int ch, float v) { fields.push_back({ch, 0, v}); }
    uint8_t* getBuffer() { return bytes; }
    unsigned getSize() const {
        unsigned size = 0;
        for (const auto& f : fields) size += f.type == 0 ? 3 : 4;
        return size;
    }
} lpp;
struct osjob_t {} sendjob;
bool nicknameUplinkPending = false;
char nickname[49] = "solar_roof";
unsigned queued = 0, lastPort = 0, lastSize = 0;
void LMIC_setTxData2(unsigned port, uint8_t*, unsigned size, int confirmed) {
    assert(confirmed == 0); ++queued; lastPort = port; lastSize = size;
}
'''
    scenarios = r'''
int main() {
    // No unmeasured temperature/angle zeroes, but RX opportunities continue.
    do_send(&sendjob);
    assert(lastPort == 1 && lastSize == 3 && lpp.fields[0].value == 0);
    LMIC.opmode = OP_TXRXPEND; pollSolarSensors(); do_send(&sendjob);
    assert(sensor1.requests == 0 && queued == 1);
    LMIC.opmode = 0; critical = true; pollSolarSensors();
    assert(sensor1.requests == 0);
    critical = false; pollSolarSensors();
    assert(conversionPending && sensor1.requests == 1 && sensor2.requests == 1);
    now = 749; pollSolarSensors(); assert(sensor1.reads == 0);
    now = 750; pollSolarSensors();
    assert(!conversionPending && sensor1.reads == 1 && sensor2.reads == 1);
    assert(solar.validMask(now) == 7 && fabs(solar.angleX - 90) < 0.001);
    do_send(&sendjob);
    assert(lastSize == 19 && lpp.fields.size() == 5);
    assert(lpp.fields[0].channel == 1 && lpp.fields[1].channel == 2);
    assert(lpp.fields[2].channel == 3 && lpp.fields[2].type == 2);
    assert(lpp.fields[3].channel == 4 && lpp.fields[4].value == 7);
    now = 1999; pollSolarSensors(); assert(sensor1.requests == 1);
    now = 2000; pollSolarSensors(); assert(sensor1.requests == 2);
    sensor1.value = -127; sensor2.value = NAN; connected = false;
    now = 2750; pollSolarSensors(); do_send(&sendjob);
    assert(solar.validMask(now) == 0 && lastSize == 3);
    // Partial failure preserves the other sensor's measured value.
    solar.temp2 = 30; do_send(&sendjob);
    assert(lastSize == 7 && lpp.fields[0].channel == 2 && lpp.fields[1].value == 2);
    nicknameUplinkPending = true; do_send(&sendjob);
    assert(lastPort == 10 && lastSize == strlen(nickname) && !nicknameUplinkPending);
    // Old cached readings never masquerade as fresh sensor data.
    now = solar.sampledAt + 10001; do_send(&sendjob);
    assert(lastPort == 1 && lastSize == 3 && lpp.fields[0].value == 0);
    // Timestamp rollover is handled with unsigned elapsed arithmetic.
    solar.sampledAt = 0xfffffff0; now = 16;
    assert(solar.validMask(now) == 2);
    solar.setAcceleration(0, 0, 0); assert(!isfinite(solar.angleX));
    solar.setAcceleration(NAN, 0, 9.81f); assert(!isfinite(solar.angleY));
    solar.setAcceleration(9.81f, 0, 0); assert(fabs(solar.angleY + 90) < 0.001);
    solar.setAcceleration(0, 0, -9.81f); assert(fabs(fabs(solar.angleX) - 180) < 0.001);
    assert(!SolarReadings::validTemperature(-127));
    assert(!SolarReadings::validTemperature(126));
    assert(SolarReadings::validTemperature(-55));
    assert(SolarReadings::validTemperature(125));
}
'''
    source = tmp_path / "solar.cpp"
    binary = tmp_path / "solar.exe"
    readings = sketch.split("// BEGIN INLINE SolarReadings\n", 1)[1].split("// END INLINE SolarReadings", 1)[0]
    harness = harness.replace('#include "SolarReadings.h"', readings)
    source.write_text(harness + poll + send + scenarios, encoding="utf-8")
    result = subprocess.run(
        [compiler, "-std=c++11", "-Wall", "-Wextra", "-pedantic", "-I", str(folder),
         str(source), "-o", str(binary)], capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    result = subprocess.run([str(binary)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
