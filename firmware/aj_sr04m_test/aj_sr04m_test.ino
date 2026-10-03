/*
 * ==============================================================================
 * AJ-SR04M ultrasonic distance sensor - bench test
 * Hardware: LILYGO TTGO LoRa32-OLED V2.1 (1.6.1) + AJ-SR04M / JSN-SR04T
 * ==============================================================================
 *
 * A standalone test: no LoRaWAN, no Wi-Fi, no cloud. It reads the sensor and
 * puts the result on the OLED and the serial console, so the wiring and the
 * sensor can be proven before any of this is folded into the node firmware.
 *
 * WIRING
 * ------
 *   AJ-SR04M          TTGO LoRa32 V2.1
 *   --------          ----------------
 *   VCC        ->     3.3V first, 5V only if that fails - see below
 *   GND        ->     GND
 *   Trig       ->     GPIO 4
 *   Echo       ->     GPIO 34  THROUGH A DIVIDER - see below
 *
 * TRY 3.3V FIRST
 * --------------
 * Echo idles low and pulses up to whatever VCC is. Powered from 3.3V, it
 * drives a safe 3.3V logic level and no divider is needed at all.
 *
 * These modules vary: many AJ-SR04M and JSN-SR04T units run anywhere from 3.0V
 * to 5.5V, while some are specified for 5V only. If 3.3V gives sensible
 * readings, stop here. If it reports NO ECHO or nonsense, it needs 5V, and
 * then the divider below is required.
 *
 * IF IT NEEDS 5V, THE ECHO PIN NEEDS A VOLTAGE DIVIDER
 * ----------------------------------------------------
 * ESP32 GPIOs are NOT 5V tolerant: the absolute maximum input is about
 * VDD + 0.3V. Feeding 5V in usually appears to work, because the pin's
 * protection diode clamps the excess into the 3.3V rail -- which is precisely
 * why this gets skipped. The damage is cumulative rather than immediate.
 * Two resistors remove the question:
 *
 *     Echo ----[ 1k ]----+---- GPIO 34
 *                        |
 *                     [ 2k ]
 *                        |
 *                       GND
 *
 * That divides 5V down to 5 * 2/(1+2) = 3.3V. Any pair in the same ratio works
 * (10k/20k is common and draws less current). With only one resistor to hand,
 * a single 1k in series is a real improvement over bare wire, because it
 * limits the current through the protection diode.
 *
 * Trig is driven BY the ESP32 at 3.3V, which the module accepts, so it needs
 * no divider.
 *
 * IF YOU GET NO READINGS
 * ----------------------
 * These modules have a mode-select pad, usually marked R27, on the back:
 *
 *   open (no resistor)  trigger/echo, HC-SR04 compatible  <- assumed here
 *   47k                 automatic serial output at 9600 baud
 *   120k                serial, responds to a command byte
 *
 * If your board shipped with a resistor fitted it will never answer a trigger
 * pulse. Set SENSOR_MODE_SERIAL to 1 below to listen for serial frames
 * instead; wire the module's Echo/TX line to GPIO 34 the same way.
 *
 * LIBRARIES
 * ---------
 * Only the two OLED libraries, which the node firmware already uses:
 *   - "Adafruit SSD1306"
 *   - "Adafruit GFX Library"
 * The ranging itself uses pulseIn() from the Arduino core - nothing to install.
 * ==============================================================================
 */

#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

// ==============================================================================
// CONFIGURATION
// ==============================================================================

#define PIN_TRIG 4    // output to the module's Trig
#define PIN_ECHO 34   // input-only pin, fed through the divider above

// Set to 1 if the module is in one of the serial modes (see the header).
#define SENSOR_MODE_SERIAL 0

// The datasheet blind zone is around 20 cm and the useful ceiling about 6 m.
// Readings outside this are reported as out of range rather than as numbers,
// because the module returns plausible-looking nonsense there.
#define MIN_VALID_CM 20.0f
#define MAX_VALID_CM 600.0f

// Ultrasonic readings are noisy, so each result is the median of several.
#define SAMPLES_PER_READING 5

// The module needs roughly 60 ms between pings for the previous burst to decay.
#define SETTLE_MS 70

// Speed of sound varies by about 0.6 m/s per degree C. At 20 C it is 343 m/s,
// which is 58.3 microseconds per centimetre of round trip. Adjust if the
// sensor lives somewhere much hotter or colder and absolute accuracy matters.
#define AMBIENT_C 20.0f

#define OLED_WIDTH   128
#define OLED_HEIGHT   64
#define OLED_ADDRESS 0x3C
#define SBMS_OLED_SDA 21
#define SBMS_OLED_SCL 22

Adafruit_SSD1306 display(OLED_WIDTH, OLED_HEIGHT, &Wire, -1);
static bool oledReady = false;

static uint32_t readingCount = 0;
static uint32_t failureCount = 0;

// ==============================================================================
// MEASUREMENT
// ==============================================================================

static float microsecondsPerCm() {
    // Round trip, so the one-way speed is halved.
    float speed_m_s = 331.3f + 0.606f * AMBIENT_C;
    return 20000.0f / speed_m_s;
}

// One ping. Returns the echo pulse width in microseconds, or 0 on timeout.
static unsigned long pingOnce() {
    digitalWrite(PIN_TRIG, LOW);
    delayMicroseconds(4);
    digitalWrite(PIN_TRIG, HIGH);
    delayMicroseconds(10);
    digitalWrite(PIN_TRIG, LOW);

    // 6 m round trip is about 35 ms; 40 ms leaves margin without hanging long
    // when nothing is connected.
    return pulseIn(PIN_ECHO, HIGH, 40000UL);
}

static int compareUnsigned(const void* a, const void* b) {
    unsigned long left = *(const unsigned long*)a;
    unsigned long right = *(const unsigned long*)b;
    return (left > right) - (left < right);
}

// Median of SAMPLES_PER_READING pings. The median is used rather than the mean
// because ultrasonic failures are outliers, not noise: a missed echo reads 0
// and a double bounce reads roughly twice the true distance, and either one
// would drag an average badly.
static float readDistanceCm() {
    unsigned long samples[SAMPLES_PER_READING];
    int valid = 0;

    for (int i = 0; i < SAMPLES_PER_READING; i++) {
        unsigned long width = pingOnce();
        if (width > 0) samples[valid++] = width;
        delay(SETTLE_MS);
    }
    if (valid == 0) return -1.0f;

    qsort(samples, valid, sizeof(unsigned long), compareUnsigned);
    unsigned long median = samples[valid / 2];
    return (float)median / microsecondsPerCm();
}

#if SENSOR_MODE_SERIAL
// Serial-mode frame: 0xFF, distance high byte, low byte, checksum. Distance
// arrives in millimetres.
static float readDistanceCmSerial() {
    static HardwareSerial sensorSerial(2);
    static bool started = false;
    if (!started) {
        sensorSerial.begin(9600, SERIAL_8N1, PIN_ECHO, -1);
        started = true;
    }
    unsigned long deadline = millis() + 1000;
    while (millis() < deadline) {
        if (sensorSerial.available() >= 4 && sensorSerial.read() == 0xFF) {
            uint8_t high = sensorSerial.read();
            uint8_t low = sensorSerial.read();
            uint8_t checksum = sensorSerial.read();
            if (((0xFF + high + low) & 0xFF) != checksum) continue;
            return ((high << 8) | low) / 10.0f;   // mm -> cm
        }
        delay(5);
    }
    return -1.0f;
}
#endif

// ==============================================================================
// DISPLAY
// ==============================================================================

static void showReading(float cm, const char* status) {
    if (!oledReady) return;
    display.clearDisplay();
    display.setTextColor(SSD1306_WHITE);

    display.setTextSize(1);
    display.setCursor(0, 0);
    display.println(F("AJ-SR04M TEST"));
    display.drawFastHLine(0, 10, OLED_WIDTH, SSD1306_WHITE);

    if (cm > 0) {
        display.setTextSize(3);
        display.setCursor(0, 18);
        display.print(cm, 1);
        display.setTextSize(1);
        display.println(F(" cm"));

        display.setTextSize(1);
        display.setCursor(0, 46);
        display.print(cm / 100.0f, 2);
        display.println(F(" m"));
    } else {
        display.setTextSize(2);
        display.setCursor(0, 22);
        display.println(status);
    }

    display.drawFastHLine(0, 55, OLED_WIDTH, SSD1306_WHITE);
    display.setTextSize(1);
    display.setCursor(0, 57);
    display.print(F("ok "));
    display.print(readingCount);
    display.print(F("  fail "));
    display.print(failureCount);
    display.display();
}

// ==============================================================================
// SETUP / LOOP
// ==============================================================================

void setup() {
    Serial.begin(115200);
    delay(600);
    Serial.println(F("\n=== AJ-SR04M ultrasonic test ==="));
    Serial.print(F("Trig GPIO "));
    Serial.print(PIN_TRIG);
    Serial.print(F(", Echo GPIO "));
    Serial.println(PIN_ECHO);
    Serial.println(F("Echo must reach the ESP32 through a 1k/2k divider."));

    Wire.begin(SBMS_OLED_SDA, SBMS_OLED_SCL);
    oledReady = display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDRESS,
                              /*reset=*/true, /*periphBegin=*/false);
    if (!oledReady) {
        Serial.println(F("[WARN] SSD1306 not found; serial output only."));
    }

    pinMode(PIN_TRIG, OUTPUT);
    digitalWrite(PIN_TRIG, LOW);
    // GPIO 34-39 are input-only and have no internal pull resistors; the
    // divider defines the idle level.
    pinMode(PIN_ECHO, INPUT);

    showReading(-1, "START");
    delay(500);
}

void loop() {
#if SENSOR_MODE_SERIAL
    float cm = readDistanceCmSerial();
#else
    float cm = readDistanceCm();
#endif

    if (cm < 0) {
        failureCount++;
        Serial.println(F("no echo  (check 5V supply, wiring, and R27 mode)"));
        showReading(-1, "NO ECHO");
    } else if (cm < MIN_VALID_CM || cm > MAX_VALID_CM) {
        failureCount++;
        Serial.print(F("out of range: "));
        Serial.print(cm, 1);
        Serial.println(F(" cm"));
        showReading(-1, "RANGE");
    } else {
        readingCount++;
        Serial.print(F("distance "));
        Serial.print(cm, 1);
        Serial.print(F(" cm  ("));
        Serial.print(cm / 100.0f, 2);
        Serial.print(F(" m)   ok "));
        Serial.print(readingCount);
        Serial.print(F("  fail "));
        Serial.println(failureCount);
        showReading(cm, "");
    }

    delay(500);
}
