/**
 * @file tests/unit/test_config_defaults.cpp
 * @brief Guard the input_t defaults against aggregate-initializer drift.
 *
 * A positional aggregate initializer silently value-initializes any trailing
 * member that has no matching value, so adding a field to input_t used to
 * flip the defaults of every field after it. src/config.cpp uses designated
 * initializers now; these tests fail loudly if that ever regresses.
 */
#include "../tests_common.h"

#include <src/config.h>

TEST(ConfigDefaults, InputBoolsMatchDocumentedDefaults) {
  EXPECT_TRUE(config::input.keyboard);
  EXPECT_TRUE(config::input.mouse);
  EXPECT_TRUE(config::input.controller);
  EXPECT_TRUE(config::input.always_send_scancodes);
  EXPECT_TRUE(config::input.high_resolution_scrolling);
  EXPECT_TRUE(config::input.native_pen_touch);
  EXPECT_TRUE(config::input.clipboard_sync);

  // Documented default is disabled; it was silently enabled by initializer drift.
  EXPECT_FALSE(config::input.key_rightalt_to_key_win);
}

TEST(ConfigDefaults, GamepadBoolsMatchDocumentedDefaults) {
  EXPECT_TRUE(config::input.ds4_back_as_touchpad_click);
  EXPECT_TRUE(config::input.motion_as_ds4);
  EXPECT_TRUE(config::input.touchpad_as_ds4);
  EXPECT_TRUE(config::input.ds5_inputtino_randomize_mac);
}
