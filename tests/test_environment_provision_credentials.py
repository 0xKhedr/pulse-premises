"""Exercise the real setup handler and OTAA callbacks with simulated ESP32 NVS."""

from pathlib import Path
import shutil
import subprocess

import pytest


def test_optional_credentials_preserve_values_validate_and_survive_reboot(tmp_path):
    compiler = shutil.which("g++")
    if not compiler:
        pytest.skip("g++ is required for the firmware simulation")
    sketch = (Path(__file__).resolve().parents[1] /
              "firmware/lilygo_environment_lpp_node/lilygo_environment_lpp_node.ino").read_text()
    credentials = sketch[sketch.index("// Defaults from the local header"):
                         sketch.index("// 2. PIN MAPPING")]
    validator = sketch[sketch.index("static bool nicknameIsAcceptable("):
                       sketch.index("static const char PROVISION_PAGE")]
    helpers = sketch[sketch.index("static int credentialHexDigit("):
                     sketch.index("static void handleProvisionRoot()")]
    handler = sketch[sketch.index("static void handleProvisionSave()"):
                     sketch.index("// Blocks until the board has been named.")]
    harness = r'''
#include <cassert>
#include <cctype>
#include <cstdint>
#include <cstring>
#include <map>
#include <string>
#include <vector>
using u1_t = uint8_t;
#define ENABLE_OLED 0
#define PROGMEM
#define NICKNAME_MAX_LENGTH 48
#define SBMS_JOIN_EUI_LSB_BYTES 8,7,6,5,4,3,2,1
#define SBMS_APP_KEY_BYTES 0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15
#define SBMS_DEV_EUI_XOR_LSB_BYTES 0,0,0,0,0,0,0,0
class String {
    std::string value;
public:
    String(const char* text = "") : value(text) {}
    String(const std::string& text) : value(text) {}
    size_t length() const { return value.length(); }
    char operator[](size_t index) const { return value[index]; }
    void trim() {
        auto first = value.find_first_not_of(" \r\n\t");
        value = first == std::string::npos ? "" :
            value.substr(first, value.find_last_not_of(" \r\n\t") - first + 1);
    }
    String& operator+=(const String& other) { value += other.value; return *this; }
    operator std::string() const { return value; }
};
std::map<std::string, std::vector<uint8_t>> flash;
bool failOpen = false, failWrite = false;
unsigned writes = 0, opens = 0;
struct Preferences {
    bool begin(const char* ns, bool) {
        assert(std::string(ns) == "sbms"); ++opens; return !failOpen;
    }
    size_t getBytesLength(const char* key) { return flash.count(key) ? flash[key].size() : 0; }
    size_t getBytes(const char* key, void* output, size_t length) {
        assert(flash[key].size() == length);
        memcpy(output, flash[key].data(), length); return length;
    }
    size_t putBytes(const char* key, const void* input, size_t length) {
        ++writes;
        if (failWrite) return 0;
        const auto* bytes = static_cast<const uint8_t*>(input);
        flash[key] = std::vector<uint8_t>(bytes, bytes + length); return length;
    }
    void end() {}
};
struct {
    std::map<std::string, String> args;
    int status = 0;
    std::string response;
    String arg(const char* key) { return args[key]; }
    void send(int code, const char*, String body) { status = code; response = std::string(body); }
} provisioningServer;
bool provisioningComplete = false;
std::string nickname;
unsigned nicknameSaves = 0;
void saveNickname(String value) { nickname = std::string(value); ++nicknameSaves; }
void delay(unsigned) {}
'''
    scenarios = r'''
void submit(const char* dev = "", const char* join = "", const char* key = "",
            const char* name = "Lobby") {
    provisioningServer.args.clear();
    provisioningServer.args["nickname"] = name;
    if (dev) provisioningServer.args["dev_eui"] = dev;
    if (join) provisioningServer.args["join_eui"] = join;
    if (key) provisioningServer.args["app_key"] = key;
    provisioningComplete = false;
    handleProvisionSave();
}
bool same(const OtaaCredentials& expected) {
    return memcmp(&expected, &otaaCredentials, sizeof(expected)) == 0;
}
int main() {
    // Simulate the hardware-derived identity before loading saved overrides.
    for (unsigned i = 0; i < 8; ++i) DEVEUI[i] = 0x80 + i;
    const OtaaCredentials defaults = otaaCredentials;
    loadOtaaCredentials(); assert(same(defaults));

    // Old clients omit every credential; new clients may send empty/whitespace fields.
    submit(nullptr, nullptr, nullptr);
    assert(provisioningServer.status == 200 && provisioningComplete && same(defaults));
    submit("", " \t", "");
    assert(provisioningServer.status == 200 && same(defaults) && writes == 0 && flash.empty());

    // Only the supplied field changes. EUIs are converted from display order to LMIC.
    submit("0123456789aBCDef");
    assert(provisioningServer.status == 200 && provisioningComplete && writes == 1);
    const uint8_t reversed[] = {0xef,0xcd,0xab,0x89,0x67,0x45,0x23,0x01};
    assert(memcmp(DEVEUI, reversed, 8) == 0);
    assert(memcmp(otaaCredentials.joinEui, defaults.joinEui, 8) == 0);
    assert(memcmp(otaaCredentials.appKey, defaults.appKey, 16) == 0);
    submit("", "1122334455667788");
    assert(provisioningServer.status == 200 && writes == 2);
    assert(otaaCredentials.joinEui[0] == 0x88 && otaaCredentials.joinEui[7] == 0x11);
    assert(memcmp(DEVEUI, reversed, 8) == 0);
    submit("", "", "FFEEDDCCBBAA99887766554433221100");
    assert(provisioningServer.status == 200 && writes == 3);
    assert(otaaCredentials.appKey[0] == 0xff && otaaCredentials.appKey[15] == 0);
    OtaaCredentials saved = otaaCredentials;
    const auto savedFlash = flash;

    // Nickname-only edits and identical credentials do not touch credential flash.
    submit("", "", "", "Renamed lobby");
    submit("0123456789abcdef", "1122334455667788", "ffeeddccbbaa99887766554433221100");
    assert(same(saved) && writes == 3 && flash == savedFlash);

    // Wrong lengths and malformed hex reject the whole submission, even after
    // an earlier field has parsed. Nickname and provisioning state remain intact.
    const char* bad[] = {"0", "0123456789ABCDE", "0123456789ABCDEFF",
                         "0123456789ABCDEG", "01:23:45:67:89:ab", "01234567 9abcdef"};
    for (const char* value : bad) {
        const unsigned before = nicknameSaves;
        submit(value); assert(provisioningServer.status == 400);
        submit("FFEEDDCCBBAA9988", value); assert(provisioningServer.status == 400);
        submit("FFEEDDCCBBAA9988", "8899AABBCCDDEEFF", value);
        assert(provisioningServer.status == 400 && !provisioningComplete);
        assert(same(saved) && flash == savedFlash && writes == 3 && nicknameSaves == before);
    }
    unsigned before = nicknameSaves;
    submit("FFEEDDCCBBAA9988", "", "", "invalid <name>");
    assert(provisioningServer.status == 400 && same(saved) && writes == 3);

    // Storage failure cannot apply credentials, save the nickname, or close setup.
    failOpen = true;
    submit("FFEEDDCCBBAA9988");
    assert(provisioningServer.status == 500 && same(saved) && !provisioningComplete);
    failOpen = false; failWrite = true;
    submit("FFEEDDCCBBAA9988");
    assert(provisioningServer.status == 500 && same(saved) && !provisioningComplete);
    assert(flash == savedFlash && nicknameSaves == before);
    failWrite = false;

    // All three updates persist together; whitespace around a value is ignored.
    submit(" FFEEDDCCBBAA9988 ", "8899AABBCCDDEEFF", "102030405060708090A0B0C0D0E0F000");
    assert(provisioningServer.status == 200 && provisioningComplete);
    saved = otaaCredentials;
    otaaCredentials = defaults; loadOtaaCredentials(); assert(same(saved));
    uint8_t output[16];
    os_getDevEui(output); assert(memcmp(output, saved.devEui, 8) == 0);
    os_getArtEui(output); assert(memcmp(output, saved.joinEui, 8) == 0);
    os_getDevKey(output); assert(memcmp(output, saved.appKey, 16) == 0);

    // Unavailable or malformed stored data retains the firmware defaults.
    otaaCredentials = defaults; failOpen = true; loadOtaaCredentials(); assert(same(defaults));
    failOpen = false; flash["otaa_creds"] = {1,2,3}; loadOtaaCredentials(); assert(same(defaults));
    flash.clear(); loadOtaaCredentials(); assert(same(defaults));
}
'''
    source = tmp_path / "provision_credentials.cpp"
    binary = tmp_path / "provision_credentials.exe"
    source.write_text(harness + credentials + validator + helpers + handler + scenarios)
    result = subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-pedantic",
                             str(source), "-o", str(binary)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    result = subprocess.run([str(binary)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
