"""Exercise actual display payload validation and refresh scheduling on a host."""

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_epaper_message_and_refresh_state(tmp_path):
    compiler = shutil.which("g++")
    if not compiler:
        pytest.skip("g++ is required for the firmware simulation")
    source = tmp_path / "epaper_test.cpp"
    source.write_text(r'''
#include <cassert>
#include <cstring>
#include "DisplayMessage.h"
using namespace EpaperMessage;
bool text(uint8_t port, const char* value, Message& output) {
    return decode(port, reinterpret_cast<const uint8_t*>(value), strlen(value), output);
}
int main() {
    Message m;
    assert(text(20, "Room 308\r\nMeeting at 14:00", m));
    assert(m.port == 20 && strcmp(m.text, "Room 308\nMeeting at 14:00") == 0);
    assert(text(21, "Closed", m) && m.port == 21);
    const Message saved = m;
    assert(!text(42, "Closed", m) && equal(m, saved));
    assert(!decode(20, nullptr, 1, m) && equal(m, saved));
    assert(!text(20, "bad\ttext", m) && equal(m, saved));
    assert(!text(20, "bad\rtext", m));
    assert(!text(20, "\xD8\xB9", m)); // unsupported UTF-8
    const uint8_t zero[] = {'a', 0, 'b'};
    assert(!decode(20, zero, sizeof(zero), m));
    char maximum[53]; memset(maximum, 'A', 52); maximum[52] = 0;
    assert(!text(20, maximum, m));
    maximum[51] = 0;
    assert(text(20, maximum, m) && strlen(m.text) == 51);
    assert(text(20, "1\n2\n3\n4\n5\n6\n7\n8", m));
    assert(!text(20, "1\n2\n3\n4\n5\n6\n7\n8\n9", m));
    assert(text(20, "123456789012345678901234567890\n2\n3\n4\n5\n6\n7\n8", m));
    assert(!text(20, "123456789012345678901234567890X\n2\n3\n4\n5\n6\n7\n8", m));
    State state;
    assert(state.due(0));
    state.rendered(state.desired, 1000);
    assert(!state.due(1000 + MinimumRefreshMs)); // unchanged
    text(20, "New message", state.desired);
    assert(!state.due(1000 + MinimumRefreshMs - 1));
    assert(state.due(1000 + MinimumRefreshMs));
    state.rendered(state.desired, 200000);
    assert(!state.due(200000 + MinimumRefreshMs)); // duplicate
    text(21, "New message", state.desired); // color alone is a change
    assert(state.due(200000 + MinimumRefreshMs));
    state.desired = state.shown; // pending change cancelled
    assert(!state.due(200000 + MinimumRefreshMs));
    assert(state.due(200000 + MaintenanceRefreshMs));
    state.rendered(state.desired, UINT32_MAX - 1000);
    text(20, "Across timer wrap", state.desired);
    assert(!state.due(1000));
    assert(state.due(MinimumRefreshMs));
    // If another message arrives while refreshing, rendered() records the
    // actual snapshot rather than falsely acknowledging the new desired body.
    Message snapshot = state.desired;
    text(21, "Later message", state.desired);
    state.rendered(snapshot, 300000);
    assert(!equal(state.desired, state.shown));
    assert(state.due(300000 + MinimumRefreshMs));
    assert(decode(20, nullptr, 0, m) && m.text[0] == 0);
}
''', encoding="utf-8")
    executable = tmp_path / "epaper_test.exe"
    subprocess.run([
        compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
        "-I", str(ROOT / "firmware/lilygo_epaper_node"),
        str(source), "-o", str(executable),
    ], check=True, capture_output=True, text=True)
    subprocess.run([str(executable)], check=True, capture_output=True, text=True)


def test_epaper_polls_without_sensors_and_retries_rejected_uplinks(tmp_path):
    compiler = shutil.which("g++")
    if not compiler:
        pytest.skip("g++ is required for the firmware simulation")
    folder = ROOT / "firmware/lilygo_epaper_node"
    sketch = (folder / "lilygo_epaper_node.ino").read_text(encoding="utf-8")
    start = sketch.index("void do_send(osjob_t* j) {")
    send = sketch[start:sketch.index("void onEvent (ev_t ev)", start)]
    source = tmp_path / "epaper_poll.cpp"
    harness = r'''
#include <cassert>
#include <cstring>
#include <vector>
#include "DisplayMessage.h"
#define ENABLE_OLED 0
#define F(x) x
#define LORA_PORT_NICKNAME 10
#define LORA_PORT_DATA 20
#define LMIC_ERROR_SUCCESS 0
struct osjob_t {};
osjob_t sendjob;
bool ready = true;
bool nicknameUplinkPending = false;
char nickname[49] = "display_308";
EpaperMessage::State epaperState;
unsigned retries = 0, schedules = 0, sends = 0;
uint8_t sentPort = 0;
std::vector<uint8_t> sent;
int result = 0;
bool LMIC_queryTxReady() { return ready; }
int os_getTime() { return 100; }
int sec2osticks(int seconds) { return seconds * 1000; }
void os_setTimedCallback(osjob_t*, int when, void (*)(osjob_t*)) {
    assert(when == 1100); ++retries;
}
void scheduleNextUplink() { ++schedules; }
int LMIC_setTxData2(uint8_t port, uint8_t* bytes, size_t size, uint8_t confirmed) {
    assert(confirmed == 0); ++sends; sentPort = port;
    sent.assign(bytes, bytes + size); return result;
}
struct {
    void println(const char*) {}
    template<typename T> void print(T) {}
} Serial;
'''
    scenarios = r'''
int main() {
    do_send(&sendjob);
    assert(sends == 1 && sentPort == 20 && sent == std::vector<uint8_t>({1, 0, 20}));
    epaperState.hasShown = true; epaperState.shown.port = 21;
    do_send(&sendjob);
    assert(sent == std::vector<uint8_t>({1, 1, 21}));
    ready = false; do_send(&sendjob);
    assert(sends == 2 && retries == 1);
    ready = true; nicknameUplinkPending = true; result = -1;
    do_send(&sendjob);
    assert(nicknameUplinkPending && schedules == 1);
    result = 0; do_send(&sendjob);
    assert(!nicknameUplinkPending && sentPort == 10);
    assert(std::string(sent.begin(), sent.end()) == nickname);
    result = -1; do_send(&sendjob);
    assert(schedules == 2 && sentPort == 20);
}
'''
    source.write_text('#include <string>\n' + harness + send + scenarios, encoding="utf-8")
    executable = tmp_path / "epaper_poll.exe"
    subprocess.run([
        compiler, "-std=c++11", "-Wall", "-Wextra", "-Wno-unused-parameter", "-Werror",
        "-I", str(folder), str(source), "-o", str(executable),
    ], check=True, capture_output=True, text=True)
    subprocess.run([str(executable)], check=True, capture_output=True, text=True)
