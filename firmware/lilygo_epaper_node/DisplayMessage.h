#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

// Plain ASCII text on port 20 (black) or 21 (red). Each packet replaces the
// complete body. Keep within the portal's 51-byte downlink limit.
namespace EpaperMessage {
constexpr uint8_t BlackPort = 20;
constexpr uint8_t RedPort = 21;
constexpr size_t Capacity = 51;
constexpr size_t Columns = 30;
constexpr size_t Rows = 8;
constexpr uint32_t MinimumRefreshMs = 180000;
constexpr uint32_t MaintenanceRefreshMs = 86400000;

struct Message {
    uint8_t port = BlackPort;
    char text[Capacity + 1] = {};
};

inline bool decode(uint8_t port, const uint8_t* bytes, size_t length, Message& output) {
    if ((port != BlackPort && port != RedPort) || length > Capacity || (length && !bytes)) return false;
    Message candidate;
    candidate.port = port;
    size_t written = 0, row = 0, column = 0;
    for (size_t i = 0; i < length; ++i) {
        const uint8_t c = bytes[i];
        // Normalize CRLF to LF; reject unsupported glyphs and control bytes.
        if (c == '\r' && i + 1 < length && bytes[i + 1] == '\n') continue;
        if (c == '\n') {
            ++row;
            column = 0;
        } else {
            if (c < 32 || c > 126) return false;
            if (column == Columns) { ++row; column = 0; }
            ++column;
        }
        if (row >= Rows) return false;
        candidate.text[written++] = char(c);
    }
    output = candidate;
    return true;
}

inline bool equal(const Message& a, const Message& b) {
    return a.port == b.port && strcmp(a.text, b.text) == 0;
}

struct State {
    Message desired;
    Message shown;
    bool hasShown = false;
    uint32_t lastRefreshAt = 0;

    bool due(uint32_t now) const {
        if (!hasShown) return true;
        const uint32_t elapsed = now - lastRefreshAt;
        return elapsed >= MaintenanceRefreshMs ||
               (!equal(desired, shown) && elapsed >= MinimumRefreshMs);
    }

    void rendered(const Message& snapshot, uint32_t now) {
        shown = snapshot;
        hasShown = true;
        lastRefreshAt = now;
    }
};
}
