#include <Arduino.h>

#include "Encoder.h"
#include "MotorPwm.h"

void setup() {
  Serial.begin(115200);
  delay(300);

  Encoder::begin();
  MotorPwm::begin();
  Serial.println("Encoder and MCPWM ready");
}

void loop() {
  static uint32_t lastReportMs = 0;

  if (millis() - lastReportMs >= 500) {
    lastReportMs = millis();
    Serial.print("encoder_ticks=");
    Serial.println(Encoder::getTicks());
  }
}
