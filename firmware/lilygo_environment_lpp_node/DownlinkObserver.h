#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

// Own the bytes: LMIC reuses its frame buffer after the receive callback.
// No Arduino/display dependency, heap allocation, or command execution.
struct DownlinkObserver {
    static const size_t Capacity = 255;
    uint8_t bytes[Capacity] = {};
    size_t length = 0;
    uint8_t port = 0;
    uint32_t count = 0;

    bool capture(uint8_t receivedPort, const uint8_t *payload, size_t size) {
        if (receivedPort == 0 || receivedPort > 223 || size > Capacity || (size && !payload)) return false;
        if (size) memcpy(bytes, payload, size);
        length = size;
        port = receivedPort;
        ++count;
        return true;
    }
};
