#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

// Own the bytes: LMIC reuses its frame buffer after the receive callback.
// No Arduino/display dependency, heap allocation, or command execution.
struct DownlinkObserver {
    static const size_t Capacity = 255;
    static const size_t Columns = 21;
    static const size_t Rows = 4;
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

    size_t textPages() const {
        return length ? (length + Columns * Rows - 1) / (Columns * Rows) : 1;
    }

    size_t hexPages() const {
        return length ? (length + 7 * Rows - 1) / (7 * Rows) : 1;
    }

    size_t pageCount() const { return textPages() + hexPages(); }

    // Four 21-character lines per page. Non-ASCII/control bytes become dots
    // in TEXT; HEX preserves every byte, including embedded zeroes.
    void formatRow(size_t page, size_t row, char *output) const {
        output[0] = '\0';
        if (row >= Rows) return;
        if (!length) {
            if (row == 0) strcpy(output, "(empty payload)");
            return;
        }
        page %= pageCount();
        const bool text = page < textPages();
        const size_t perRow = text ? Columns : 7;
        const size_t localPage = text ? page : page - textPages();
        const size_t start = (localPage * Rows + row) * perRow;
        size_t written = 0;
        static const char Hex[] = "0123456789ABCDEF";
        for (size_t i = start; i < length && i < start + perRow; ++i) {
            if (text) {
                output[written++] = bytes[i] >= 32 && bytes[i] <= 126 ? char(bytes[i]) : '.';
            } else {
                if (i != start) output[written++] = ' ';
                output[written++] = Hex[bytes[i] >> 4];
                output[written++] = Hex[bytes[i] & 15];
            }
        }
        output[written] = '\0';
    }
};
