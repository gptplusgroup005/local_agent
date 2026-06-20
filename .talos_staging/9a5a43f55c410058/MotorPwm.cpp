#include <Arduino.h>
#include "driver/mcpwm_prelude.h"

#include "Config.h"
#include "MotorPwm.h"

namespace {
mcpwm_timer_handle_t pwmTimer = nullptr;
mcpwm_oper_handle_t pwmOperator = nullptr;
mcpwm_cmpr_handle_t rightComparator = nullptr;
mcpwm_cmpr_handle_t leftComparator = nullptr;
mcpwm_gen_handle_t rightGenerator = nullptr;
mcpwm_gen_handle_t leftGenerator = nullptr;

void checkEsp(esp_err_t err, const char *label) {
  if (err == ESP_OK) {
    return;
  }

  Serial.print(label);
  Serial.print(" failed: ");
  Serial.println(esp_err_to_name(err));
  while (true) {
    delay(1000);
  }
}

uint32_t dutyToTicks(float dutyPercent) {
  dutyPercent = constrain(dutyPercent, 0.0f, 100.0f);
  return static_cast<uint32_t>((dutyPercent / 100.0f) * Config::kPwmPeriodTicks);
}
}  // namespace

namespace MotorPwm {
void setPower(float powerPercent) {
  powerPercent = constrain(powerPercent, -100.0f, 100.0f);

  const float rightDuty = powerPercent > 0.0f ? powerPercent : 0.0f;
  const float leftDuty = powerPercent < 0.0f ? -powerPercent : 0.0f;

  checkEsp(mcpwm_comparator_set_compare_value(rightComparator, dutyToTicks(rightDuty)), "set R_PWM duty");
  checkEsp(mcpwm_comparator_set_compare_value(leftComparator, dutyToTicks(leftDuty)), "set L_PWM duty");
}

void begin() {
  mcpwm_timer_config_t timerConfig = {};
  timerConfig.group_id = 0;
  timerConfig.clk_src = MCPWM_TIMER_CLK_SRC_DEFAULT;
  timerConfig.resolution_hz = Config::kPwmResolutionHz;
  timerConfig.period_ticks = Config::kPwmPeriodTicks;
  timerConfig.count_mode = MCPWM_TIMER_COUNT_MODE_UP;
  checkEsp(mcpwm_new_timer(&timerConfig, &pwmTimer), "mcpwm_new_timer");

  mcpwm_operator_config_t operatorConfig = {};
  operatorConfig.group_id = 0;
  checkEsp(mcpwm_new_operator(&operatorConfig, &pwmOperator), "mcpwm_new_operator");
  checkEsp(mcpwm_operator_connect_timer(pwmOperator, pwmTimer), "mcpwm_operator_connect_timer");

  mcpwm_comparator_config_t comparatorConfig = {};
  comparatorConfig.flags.update_cmp_on_tez = true;
  checkEsp(mcpwm_new_comparator(pwmOperator, &comparatorConfig, &rightComparator), "mcpwm_new_comparator R_PWM");
  checkEsp(mcpwm_new_comparator(pwmOperator, &comparatorConfig, &leftComparator), "mcpwm_new_comparator L_PWM");

  mcpwm_generator_config_t rightGeneratorConfig = {};
  rightGeneratorConfig.gen_gpio_num = Config::kRightPwmPin;
  checkEsp(mcpwm_new_generator(pwmOperator, &rightGeneratorConfig, &rightGenerator), "mcpwm_new_generator R_PWM");

  mcpwm_generator_config_t leftGeneratorConfig = {};
  leftGeneratorConfig.gen_gpio_num = Config::kLeftPwmPin;
  checkEsp(mcpwm_new_generator(pwmOperator, &leftGeneratorConfig, &leftGenerator), "mcpwm_new_generator L_PWM");

  checkEsp(mcpwm_generator_set_action_on_timer_event(
               rightGenerator,
               MCPWM_GEN_TIMER_EVENT_ACTION(MCPWM_TIMER_DIRECTION_UP, MCPWM_TIMER_EVENT_EMPTY, MCPWM_GEN_ACTION_HIGH)),
           "R_PWM timer action");
  checkEsp(mcpwm_generator_set_action_on_compare_event(
               rightGenerator,
               MCPWM_GEN_COMPARE_EVENT_ACTION(MCPWM_TIMER_DIRECTION_UP, rightComparator, MCPWM_GEN_ACTION_LOW)),
           "R_PWM compare action");

  checkEsp(mcpwm_generator_set_action_on_timer_event(
               leftGenerator,
               MCPWM_GEN_TIMER_EVENT_ACTION(MCPWM_TIMER_DIRECTION_UP, MCPWM_TIMER_EVENT_EMPTY, MCPWM_GEN_ACTION_HIGH)),
           "L_PWM timer action");
  checkEsp(mcpwm_generator_set_action_on_compare_event(
               leftGenerator,
               MCPWM_GEN_COMPARE_EVENT_ACTION(MCPWM_TIMER_DIRECTION_UP, leftComparator, MCPWM_GEN_ACTION_LOW)),
           "L_PWM compare action");

  setPower(0.0f);
  checkEsp(mcpwm_timer_enable(pwmTimer), "mcpwm_timer_enable");
  checkEsp(mcpwm_timer_start_stop(pwmTimer, MCPWM_TIMER_START_NO_STOP), "mcpwm_timer_start_stop");
}
}  // namespace MotorPwm
