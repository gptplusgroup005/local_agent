#pragma once

#include <Arduino.h>

namespace Config {
constexpr uint8_t kEncoderAPin = 32;
constexpr uint8_t kEncoderBPin = 33;
constexpr uint8_t kRightPwmPin = 25;
constexpr uint8_t kLeftPwmPin = 26;

constexpr uint32_t kPwmResolutionHz = 10000000;
constexpr uint32_t kPwmFrequencyHz = 20000;
constexpr uint32_t kPwmPeriodTicks = kPwmResolutionHz / kPwmFrequencyHz;
}  // namespace Config
