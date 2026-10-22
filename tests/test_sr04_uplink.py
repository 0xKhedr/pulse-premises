"""Exercise actual SR04 send decisions with an idle simulated LMIC radio."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_sr04_sends_status_without_distance(tmp_path):
    compiler = shutil.which("g++")
    if not compiler:
        pytest.skip("g++ is required for the firmware uplink simulation")
    sketch = (
        Path(__file__).resolve().parents[1]
        / "firmware/lilygo_sr04_lpp_node/lilygo_sr04_lpp_node.ino"
    ).read_text(encoding="utf-8")
    fresh_start = sketch.index("static bool distanceIsFresh() {")
    fresh_end = sketch.index("\nstatic void pollDistance()", fresh_start)
    send_start = sketch.index("void do_send(osjob_t* j) {")
    send_end = sketch.index("\nvoid onEvent", send_start)
    events_end = sketch.index("\nvoid setup()", send_end)
    harness = r'''
#include <cassert>
#include <cmath>
#include <cstring>
#include <vector>
#define ENABLE_OLED 0
#define F(x) x
#define OP_TXRXPEND 1
#define OP_JOINING 2
#define OP_TXDATA 4
#define OP_POLL 8
#define LMIC_ERROR_SUCCESS 0
#define LORA_PORT_NICKNAME 10
#define LORA_PORT_DATA 1
#define NICKNAME_REFRESH_UPLINKS 60
using uint8_t = unsigned char;
enum ev_t { EV_JOINING, EV_JOINED, EV_JOIN_FAILED, EV_JOIN_TXCOMPLETE,
            EV_TXSTART, EV_TXCANCELED, EV_RXSTART, EV_TXCOMPLETE, EV_RESET, EV_RXCOMPLETE };
struct osjob_t {};
osjob_t sendjob;
struct { int opmode = 0; unsigned devaddr = 1; } LMIC;
const unsigned RADIO_RETRY_SECONDS = 5;
unsigned long queuedPacketCount = 0, completedPacketCount = 0;
uint8_t lastQueuedPort = 0;
unsigned uplinksSinceNickname = 0;
unsigned long now = 10000, lastValidReadingMs = 10000;
const unsigned long READING_MAX_AGE_MS = 5000;
const unsigned TX_INTERVAL_SECONDS = 30;
float lastDistanceCm = 125;
bool distanceValid = false, nicknameUplinkPending = false;
const char* sensorState = "NO ECHO";
char nickname[49] = "";
unsigned long millis() { return now; }
unsigned long os_getTime() { return now; }
unsigned long sec2osticks(unsigned seconds) { return seconds * 1000UL; }
unsigned long nextDeadline = 0;
void (*nextSend)(osjob_t*) = nullptr;
void os_setTimedCallback(osjob_t*, unsigned long deadline, void (*callback)(osjob_t*)) {
    nextDeadline = deadline; nextSend = callback;
}
bool LMIC_queryTxReady() { return !(LMIC.opmode & (OP_JOINING | OP_TXDATA | OP_TXRXPEND | OP_POLL)); }
void LMIC_startJoining() { LMIC.opmode |= OP_JOINING; }
void LMIC_setLinkCheckMode(int) {}
void LMIC_setAdrMode(int) {}
struct {
    template<typename T> void print(T) {}
    template<typename T> void println(T) {}
    template<typename... Args> void printf(const char*, Args...) {}
} Serial;
struct Field { int channel; bool analog; float value; };
struct {
    std::vector<Field> fields;
    uint8_t bytes[7] = {};
    unsigned size = 0;
    void reset() { fields.clear(); size = 0; }
    bool addDigitalInput(int channel, int value) {
        fields.push_back({channel, false, float(value)}); size += 3; return size <= 7;
    }
    bool addAnalogInput(int channel, float value) {
        fields.push_back({channel, true, value}); size += 4; return size <= 7;
    }
    unsigned getSize() { return size; }
    uint8_t* getBuffer() { return bytes; }
} lpp;
unsigned queued = 0, lastPort = 0, lastSize = 0;
int rejectQueue = 0;
int LMIC_setTxData2(unsigned port, uint8_t*, unsigned size, int) {
    if (!LMIC_queryTxReady()) return -1;
    if (rejectQueue) return rejectQueue;
    ++queued; lastPort = port; lastSize = size; LMIC.opmode |= OP_TXDATA;
    return LMIC_ERROR_SUCCESS;
}
'''
    scenarios = r'''
int main() {
    // The installed MCCI library rejects packets during joining. No packet
    // may be counted/forgotten, and a future send must remain scheduled.
    std::strcpy(nickname, "distance_tank_01"); nicknameUplinkPending = true;
    LMIC.devaddr = 0; LMIC_startJoining(); do_send(&sendjob);
    assert(queued == 0 && nicknameUplinkPending && queuedPacketCount == 0);
    assert(nextSend == do_send && nextDeadline == now + 5000);
    LMIC.devaddr = 1; LMIC.opmode = 0; onEvent(EV_JOINED);
    assert(nextSend == do_send && nextDeadline == now + 1000);
    nextSend(&sendjob);
    assert(queued == 1 && lastPort == 10 && !nicknameUplinkPending);
    LMIC.opmode = 0; onEvent(EV_TXCOMPLETE);
    assert(nextDeadline == now + 30000 && completedPacketCount == 1);
    queued = 0; queuedPacketCount = 0; // Begin the sensor payload checks.

    do_send(&sendjob); // No echo still produces a LoRaWAN status packet.
    assert(queued == 1 && lastPort == 1 && lastSize == 3);
    assert(lpp.fields.size() == 1 && lpp.fields[0].channel == 8);
    assert(!lpp.fields[0].analog && lpp.fields[0].value == 0);

    LMIC.opmode = 0;
    distanceValid = true;
    do_send(&sendjob); // Fresh 125 cm produces status + 1.25 meters.
    assert(queued == 2 && lastSize == 7 && lpp.fields.size() == 2);
    assert(lpp.fields[0].value == 1);
    assert(lpp.fields[1].analog && lpp.fields[1].channel == 7);
    assert(std::fabs(lpp.fields[1].value - 1.25f) < 0.0001f);

    LMIC.opmode = 0; now += READING_MAX_AGE_MS + 1;
    do_send(&sendjob); // A previously valid but stale distance is omitted.
    assert(queued == 3 && lastSize == 3 && lpp.fields.size() == 1);
    assert(lpp.fields[0].value == 0);

    LMIC.opmode = OP_TXRXPEND; do_send(&sendjob);
    assert(queued == 3); // Never interfere with a pending TX/RX cycle.
    assert(nextSend == do_send && nextDeadline == now + 5000);
    LMIC.opmode = OP_TXDATA; do_send(&sendjob);
    assert(queued == 3 && nextDeadline == now + 5000);
    LMIC.opmode = 0;
    std::strcpy(nickname, "distance_tank_01"); nicknameUplinkPending = true;
    rejectQueue = -1; do_send(&sendjob);
    assert(queued == 3 && nicknameUplinkPending && nextDeadline == now + 5000);
    rejectQueue = 0;
    do_send(&sendjob);
    assert(queued == 4 && lastPort == 10 && !nicknameUplinkPending);
    LMIC.opmode = 0;
    do_send(&sendjob);
    assert(queued == 5 && lastPort == 1 && lastSize == 3);
    LMIC.opmode = 0; rejectQueue = -3;
    do_send(&sendjob);
    assert(queued == 5 && queuedPacketCount == 5 && nextDeadline == now + 5000);
    rejectQueue = 0;
    onEvent(EV_TXCANCELED);
    assert(nextSend == do_send && nextDeadline == now + 5000);
    lastQueuedPort = 10; nicknameUplinkPending = false;
    onEvent(EV_TXCANCELED); assert(nicknameUplinkPending);
    // Complete the cycle and actually run the scheduled 30-second callback.
    nicknameUplinkPending = false;
    onEvent(EV_TXCOMPLETE); assert(nextDeadline == now + 30000);
    now = nextDeadline; nextSend(&sendjob);
    assert(queued == 6 && lastPort == 1 && lastSize == 3);
}
'''
    source = tmp_path / "sr04_uplink.cpp"
    binary = tmp_path / "sr04_uplink.exe"
    source.write_text(
        harness + sketch[fresh_start:fresh_end]
        + sketch[send_start:events_end] + scenarios,
        encoding="utf-8",
    )
    compiled = subprocess.run(
        [compiler, "-std=c++11", "-Wall", "-Wextra", "-Wno-unused-parameter",
         "-Werror", str(source), "-o", str(binary)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert compiled.returncode == 0, compiled.stderr
    result = subprocess.run(
        [str(binary)], capture_output=True, text=True, timeout=10, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
