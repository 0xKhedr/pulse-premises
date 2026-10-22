/*
 * LilyGO T3 / LoRa32 V2.1 (1.6.1): standalone GPS UART diagnostic.
 * Upload to the LilyGO. Only the ESP32 board package is required.
 * Serial Monitor: 115200 baud. GPS TX -> IO34, GND -> GND.
 * Keep the existing GPS power arrangement. GPS RX and PPS stay disconnected.
 * No radio, OLED, Wi-Fi, nickname, credentials or GPS parser library.
 * Starts with a GPIO signal test, independent of UART baud or parsing.
 * See README.md for optional loopback and read-only receiver query wiring.
 */

#include <Arduino.h>
#include <esp_arduino_version.h>
#include <string.h>

constexpr int GPS_RX_PIN = 34;
constexpr int TEST_TX_PIN = 12;
constexpr int QUERY_TX_PIN = 4; // Optional IO4 -> GPS RX, only for command q.
constexpr uint32_t BAUD_RATES[] = {9600, 4800, 19200, 38400, 57600, 115200};
constexpr uint32_t SCAN_INTERVAL_MS = 8000;
constexpr char LOOPBACK_TEXT[] = "SBMS_LOOPBACK_12345";
HardwareSerial gpsPort(2);

static size_t baudIndex = 0;
static bool scanning = false;
static bool loopback = false;
static int loopbackTxPin = TEST_TX_PIN;
static bool signalTest = false;
static bool inverted = false;
static bool queryMode = false;
static uint32_t baudStartedMs = 0;
static uint32_t lastReportMs = 0;
static uint32_t lastLoopbackMs = 0;
static uint32_t receivedBytes = 0;
static uint32_t validNmea = 0;
static uint32_t loopbackMatches = 0;
static uint32_t validUbx = 0;
static uint32_t lastQueryMs = 0;
static char line[128];
static size_t lineLength = 0;
static bool lineOverflow = false;
static uint8_t sample[96];
static size_t sampleLength = 0;

static portMUX_TYPE edgeMux = portMUX_INITIALIZER_UNLOCKED;
static volatile uint32_t signalEdges = 0;
static volatile uint32_t shortestIntervalUs = UINT32_MAX;
static volatile uint32_t lastEdgeUs = 0;
static volatile bool havePreviousEdge = false;

static void ARDUINO_ISR_ATTR onSignalEdge() {
    const uint32_t now = micros();
    portENTER_CRITICAL_ISR(&edgeMux);
    if (havePreviousEdge) {
        const uint32_t interval = now - lastEdgeUs;
        if (interval && interval < shortestIntervalUs) shortestIntervalUs = interval;
    }
    lastEdgeUs = now;
    havePreviousEdge = true;
    ++signalEdges;
    portEXIT_CRITICAL_ISR(&edgeMux);
}

static void stopSignalTest() {
    if (signalTest) detachInterrupt(digitalPinToInterrupt(GPS_RX_PIN));
    signalTest = false;
}

static void startSignalTest() {
    stopSignalTest();
    gpsPort.end(); // GPIO mode and UART mode are deliberately separate.
    pinMode(GPS_RX_PIN, INPUT); // IO34 has no internal pull resistor.
    portENTER_CRITICAL(&edgeMux);
    signalEdges = 0;
    shortestIntervalUs = UINT32_MAX;
    havePreviousEdge = false;
    portEXIT_CRITICAL(&edgeMux);
    scanning = loopback = queryMode = inverted = false;
    signalTest = true;
    lastReportMs = millis();
    attachInterrupt(digitalPinToInterrupt(GPS_RX_PIN), onSignalEdge, CHANGE);
    Serial.println("\n[SIGNAL TEST] Watching IO34 transitions; no GPS commands sent.");
    Serial.println("Each report covers 2 seconds. HIGH/LOW is a digital level, not a voltage measurement.");
}

// Streaming UBX checksum parser. Stores only the first 40 payload bytes,
// enough for MON-VER's software/hardware strings; validates the whole frame.
static uint8_t ubxState = 0, ubxClass = 0, ubxId = 0;
static uint8_t ubxCkA = 0, ubxCkB = 0, ubxReceivedA = 0;
static uint16_t ubxLength = 0, ubxPosition = 0;
static char ubxVersion[40];

static void addUbxChecksum(uint8_t b) {
    ubxCkA += b;
    ubxCkB += ubxCkA;
}

static void readUbxByte(uint8_t b) {
    switch (ubxState) {
        case 0: if (b == 0xB5) ubxState = 1; break;
        case 1: ubxState = b == 0x62 ? 2 : (b == 0xB5 ? 1 : 0); break;
        case 2:
            ubxCkA = ubxCkB = 0;
            ubxClass = b; addUbxChecksum(b); ubxState = 3; break;
        case 3: ubxId = b; addUbxChecksum(b); ubxState = 4; break;
        case 4: ubxLength = b; addUbxChecksum(b); ubxState = 5; break;
        case 5:
            ubxLength |= (uint16_t)b << 8;
            addUbxChecksum(b);
            ubxPosition = 0;
            // Reject corrupt lengths instead of waiting indefinitely for data.
            ubxState = ubxLength > 1024 ? 0 : (ubxLength ? 6 : 7);
            break;
        case 6:
            if (ubxPosition < sizeof(ubxVersion)) ubxVersion[ubxPosition] = (char)b;
            ++ubxPosition;
            addUbxChecksum(b);
            if (ubxPosition == ubxLength) ubxState = 7;
            break;
        case 7: ubxReceivedA = b; ubxState = 8; break;
        case 8:
            if (ubxReceivedA == ubxCkA && b == ubxCkB) {
                if (ubxClass == 0x0A && ubxId == 0x04 && ubxLength < 40) {
                    Serial.println("[UBX] MON-VER poll/short packet observed; not an identification reply.");
                    ubxState = 0;
                    break; // A looped-back poll must not count as a GPS reply.
                }
                ++validUbx;
                Serial.printf("[UBX] class=0x%02X id=0x%02X length=%u checksum=OK\n",
                              (unsigned)ubxClass, (unsigned)ubxId, (unsigned)ubxLength);
                if (ubxClass == 0x0A && ubxId == 0x04 && ubxLength >= 40) {
                    Serial.printf("[RECEIVER] software=%.30s | hardware=%.10s\n",
                                  ubxVersion, ubxVersion + 30);
                }
                if (scanning) {
                    scanning = queryMode = false;
                    Serial.printf("[FOUND] Valid UBX at %lu baud | inverted=%u. Scan stopped.\n",
                                  (unsigned long)BAUD_RATES[baudIndex], (unsigned)inverted);
                }
            }
            ubxState = 0;
            break;
    }
}

static int hexDigit(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    return -1;
}

// Validate the complete NMEA sentence, independent of TinyGPSPlus.
static bool nmeaChecksumMatches(const char* text) {
    if (text[0] != '$') return false;
    const char* star = strchr(text, '*');
    if (!star || strlen(star + 1) != 2) return false;
    int hi = hexDigit(star[1]);
    int lo = hexDigit(star[2]);
    if (hi < 0 || lo < 0) return false;
    uint8_t checksum = 0;
    for (const char* p = text + 1; p < star; ++p) checksum ^= (uint8_t)*p;
    return checksum == ((hi << 4) | lo);
}

static void startBaud() {
    stopSignalTest();
    gpsPort.end();
    gpsPort.setRxBufferSize(2048);
#if ESP_ARDUINO_VERSION >= ESP_ARDUINO_VERSION_VAL(3, 3, 11)
    // This core enables an RX pull by default, but IO34 has no internal pulls.
    gpsPort.enableRxInternalPull(false);
#endif
    gpsPort.begin(BAUD_RATES[baudIndex], SERIAL_8N1, GPS_RX_PIN,
                  loopback ? loopbackTxPin : QUERY_TX_PIN, inverted);
    receivedBytes = validNmea = loopbackMatches = validUbx = 0;
    ubxState = 0;
    lineLength = sampleLength = 0;
    lineOverflow = false;
    baudStartedMs = lastReportMs = millis();
    lastQueryMs = millis();
    Serial.printf("\n[TEST] RX IO%d | TX IO%d | %lu baud | %s | inverted=%u | query=%u\n", GPS_RX_PIN,
                  loopback ? loopbackTxPin : QUERY_TX_PIN,
                  (unsigned long)BAUD_RATES[baudIndex],
                  loopback ? "LOOPBACK" : "GPS baud scan",
                  (unsigned)inverted, (unsigned)queryMode);
}

static void readInput() {
    for (unsigned i = 0; i < 512 && gpsPort.available(); ++i) {
        int value = gpsPort.read();
        if (value < 0) break;
        char c = (char)value;
        ++receivedBytes;
        if (!loopback) readUbxByte((uint8_t)value);
        if (sampleLength < sizeof(sample)) sample[sampleLength++] = (uint8_t)c;
        if (c == '\r') continue;
        // Resynchronize at the start of a new NMEA sentence after noise.
        if (c == '$' && !loopback) {
            lineLength = 0;
            lineOverflow = false;
        }
        if (c == '\n') {
            line[lineLength] = '\0';
            if (!lineOverflow && loopback && strcmp(line, LOOPBACK_TEXT) == 0) {
                ++loopbackMatches;
            } else if (!lineOverflow && !loopback && nmeaChecksumMatches(line)) {
                ++validNmea;
                if (validNmea <= 2) Serial.printf("[NMEA] %s\n", line);
                if (scanning && validNmea >= 2) {
                    scanning = queryMode = false;
                    Serial.printf("[FOUND] Valid NMEA at %lu baud | inverted=%u. Scan stopped.\n",
                                  (unsigned long)BAUD_RATES[baudIndex], (unsigned)inverted);
                }
            }
            lineLength = 0;
            lineOverflow = false;
        } else if (lineLength < sizeof(line) - 1) {
            line[lineLength++] = c;
        } else {
            lineOverflow = true;
        }
    }
}

static void report() {
    if (signalTest) {
        portENTER_CRITICAL(&edgeMux);
        const uint32_t edges = signalEdges;
        const uint32_t shortest = shortestIntervalUs;
        signalEdges = 0;
        shortestIntervalUs = UINT32_MAX;
        havePreviousEdge = false;
        portEXIT_CRITICAL(&edgeMux);
        Serial.printf("[SIGNAL] IO%d | level=%s | edges_2s=%lu | shortest_interval_us=%lu\n",
                      GPS_RX_PIN, digitalRead(GPS_RX_PIN) == HIGH ? "HIGH" : "LOW",
                      (unsigned long)edges,
                      (unsigned long)(shortest == UINT32_MAX ? 0 : shortest));
        return;
    }
    Serial.printf("[RX] IO%d | baud=%lu | bytes=%lu | valid_NMEA=%lu | valid_UBX=%lu | loopback_matches=%lu\n",
                  GPS_RX_PIN, (unsigned long)BAUD_RATES[baudIndex],
                  (unsigned long)receivedBytes, (unsigned long)validNmea,
                  (unsigned long)validUbx,
                  (unsigned long)loopbackMatches);
    if (sampleLength) {
        Serial.print("[ASCII] ");
        for (size_t i = 0; i < sampleLength; ++i) {
            uint8_t c = sample[i];
            Serial.write(c >= 32 && c <= 126 ? c : '.');
        }
        Serial.print("\n[HEX] ");
        for (size_t i = 0; i < sampleLength && i < 16; ++i) {
            Serial.printf("%02X ", (unsigned)sample[i]);
        }
        Serial.println();
        sampleLength = 0;
    }
}

void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.println("\nSBMS GPS UART diagnostic - upload to LILYGO only");
    Serial.println("GPS TX -> IO34; common GND; GPS RX/PPS disconnected.");
    Serial.println("Commands: e = signal edges; s = normal scan; i = inverted scan;");
    Serial.println("          q = read-only MON-VER query scan; l = IO12 loopback; t = IO4 loopback.");
    Serial.println("Before q: power off and connect IO4 -> GPS RX. No GPS configuration is written.");
    Serial.println("Before l: disconnect GPS TX, then jumper IO12 -> IO34.");
    Serial.println("Before t: disconnect GPS completely, then jumper IO4 -> IO34.");
    startSignalTest();
}

void loop() {
    if (!signalTest) readInput();
    if (Serial.available()) {
        char command = (char)Serial.read();
        if (command == 'e') {
            startSignalTest();
        } else if (command == 's' || command == 'i' || command == 'q' || command == 'l' || command == 't') {
            loopback = command == 'l' || command == 't';
            loopbackTxPin = command == 't' ? QUERY_TX_PIN : TEST_TX_PIN;
            inverted = command == 'i';
            queryMode = command == 'q';
            scanning = !loopback;
            baudIndex = 0;
            startBaud();
        }
    }
    if (loopback && millis() - lastLoopbackMs >= 1000) {
        lastLoopbackMs = millis();
        gpsPort.println(LOOPBACK_TEXT);
    }
    if (queryMode && millis() - lastQueryMs >= 2000) {
        lastQueryMs = millis();
        // UBX-MON-VER poll: class 0A, id 04, zero-length payload, checksum 0E 34.
        // Requests identification only. Does not enable messages, change baud,
        // reset the receiver, or write its saved configuration.
        static const uint8_t poll[] = {0xB5, 0x62, 0x0A, 0x04, 0x00, 0x00, 0x0E, 0x34};
        gpsPort.write(poll, sizeof(poll));
        Serial.println("[POLL] Read-only UBX-MON-VER request sent on IO4.");
    }
    if (millis() - lastReportMs >= 2000) {
        lastReportMs = millis();
        report();
    }
    if (scanning && millis() - baudStartedMs >= SCAN_INTERVAL_MS) {
        report();
        if (baudIndex + 1 < sizeof(BAUD_RATES) / sizeof(BAUD_RATES[0])) {
            ++baudIndex;
            startBaud();
        } else {
            scanning = false;
            queryMode = false;
            Serial.println("[SCAN COMPLETE] No tested baud produced valid NMEA/UBX.");
            Serial.println("Bytes with no NMEA may be binary output or noise.");
            Serial.println("Zero bytes: use e for signal activity or q for an optional read-only query.");
        }
    }
}
