#include "config.h"

#include <Preferences.h>



Preferences preferences;
String input = "";



void setup() {
    Serial.begin(115200);
    delay(2000);

    if (!preferences.begin("config", 0)) {
        Serial.println("[ERROR] Cannot open config storage");
        while (1) delay(1000);
    }
    
    bool reset = preferences.getBool("reset", 1);
    String config = preferences.getString("config", "default");

    if (reset) {
        preferences.putBool("reset", 0);
        Serial.printf("[CONFIG] Current value: %s, please enter a new value\n", config.c_str());
    } else {
        preferences.putBool("reset", 1);

        Serial.printf("[BOOT] Press RST within %d seconds to configure\n", RESET_WINDOW_MS / 1000);
        delay(RESET_WINDOW_MS);

        preferences.putBool("reset", 0);
        Serial.printf("[CONFIG] Current value: %s\n", config.c_str());

        vTaskSuspend(NULL);
    }
}

void loop() {
    if (!Serial.available()) {
        delay(100);
        return;
    }
    
    const char c = Serial.read();
    if (c == '\n') {
        preferences.putString("config", input);
        Serial.printf("[CONFIG] Current value: %s\n", input.c_str());

        vTaskSuspend(NULL);
    } else {
        input += c;
    }

    delay(100);
}
