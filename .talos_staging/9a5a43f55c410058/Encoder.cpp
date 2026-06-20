#include <Arduino.h>

#include "Config.h"
#include "Encoder.h"

namespace {
volatile int32_t encoderTicks = 0;
volatile uint8_t encoderState = 0;

uint8_t IRAM_ATTR readEncoderState() {
  return (digitalRead(Config::kEncoderAPin) << 1) | digitalRead(Config::kEncoderBPin);
}

void IRAM_ATTR handleEncoderChange() {
  static constexpr int8_t transitionTable[16] = {
      0, -1, 1, 0,
      1, 0, 0, -1,
      -1, 0, 0, 1,
      0, 1, -1, 0};

  const uint8_t newState = readEncoderState();
  const uint8_t transition = (encoderState << 2) | newState;
  encoderTicks += transitionTable[transition];
  encoderState = newState;
}
}  // namespace

namespace Encoder {
void begin() {
  pinMode(Config::kEncoderAPin, INPUT_PULLUP);
  pinMode(Config::kEncoderBPin, INPUT_PULLUP);

  encoderState = readEncoderState();
  // Decode both edges of both channels: x4 quadrature counting.
  attachInterrupt(digitalPinToInterrupt(Config::kEncoderAPin), handleEncoderChange, CHANGE);
  attachInterrupt(digitalPinToInterrupt(Config::kEncoderBPin), handleEncoderChange, CHANGE);
}

int32_t getTicks() {
  noInterrupts();
  const int32_t ticks = encoderTicks;
  interrupts();
  return ticks;
}

void resetTicks() {
  noInterrupts();
  encoderTicks = 0;
  interrupts();
}
}  // namespace Encoder
