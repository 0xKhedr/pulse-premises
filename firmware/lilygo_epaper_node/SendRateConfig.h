#pragma once

#include <stddef.h>
#include <stdint.h>

// FPort 42: version 1, command 1, then uint32 big-endian interval in seconds.
// Other application ports remain available to the downlink observer.
namespace SendRateConfig {
    static const uint8_t Port = 42;
    static const uint32_t DefaultSeconds = 60;
    static const uint32_t MinSeconds = 30;
    static const uint32_t MaxSeconds = 86400;

    inline bool valid(uint32_t seconds) {
        return seconds >= MinSeconds && seconds <= MaxSeconds;
    }

    inline bool decode(uint8_t port, const uint8_t* bytes, size_t length, uint32_t& seconds) {
        if (port != Port || !bytes || length != 6 || bytes[0] != 1 || bytes[1] != 1) return false;
        const uint32_t value = (uint32_t(bytes[2]) << 24) | (uint32_t(bytes[3]) << 16)
                             | (uint32_t(bytes[4]) << 8) | uint32_t(bytes[5]);
        if (!valid(value)) return false;
        seconds = value;
        return true;
    }
}
