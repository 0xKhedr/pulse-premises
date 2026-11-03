#include "config.h"
#include "creds.h"

#include <Preferences.h>
#include <SPI.h>

#include <lmic.h>



const lmic_pinmap lmic_pins = {
    .nss = LORA_SPI_NSS_PIN,
    .rxtx = LMIC_UNUSED_PIN,
    .rst = LORA_RST_PIN,
    .dio = {LORA_DIO0_PIN, LORA_DIO1_PIN, LORA_DIO2_PIN},
};

static const u1_t PROGMEM JOIN_EUI[8] = LORAWAN_JOIN_EUI;
static const u1_t PROGMEM DEV_EUI[8] = LORAWAN_DEV_EUI;
static const u1_t PROGMEM APP_KEY[16] = LORAWAN_APP_KEY;

void os_getArtEui(u1_t* buf) memcpy_P(buf, JOIN_EUI, 8);
void os_getDevEui(u1_t* buf) memcpy_P(buf, DEV_EUI, 8);
void os_getDevKey(u1_t* buf) memcpy_P(buf, APP_KEY, 16);



void setup() {
    Serial.begin(115200);
    delay(2000);
    Serial.println(("[INIT] Pulse Premises — LoRaWAN Node"));

    SPI.begin(LORA_SPI_SCK_PIN, LORA_SPI_MISO, LORA_SPI_MOSI, LORA_SPI_NSS);
    if (!os_init()) {
        Serial.println("[ERROR] LoRa radio initialization failed\n[DEBUG] Check config LoRa pins");
        while (1) delay(1000);
    }

    LMIC_reset();
    LMIC_startJoining();
    Serial.println("[INFO] Joining LoRaWAN network");
}

void loop() {
    os_runloop_once();
}



// Omitted F() that stores string literals in the flash rather than RAM because ESP32 does that by default
