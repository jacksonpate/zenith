"""Client mode resolution and CVT-RB timing sanity."""

from zenith_display.modes import Mode, client_mode, cvt_rb


def test_client_mode_reads_sunshine_env():
    env = {
        "SUNSHINE_CLIENT_WIDTH": "2420",
        "SUNSHINE_CLIENT_HEIGHT": "1668",
        "SUNSHINE_CLIENT_FPS": "120",
    }
    assert client_mode(env) == Mode(2420, 1668, 120)


def test_client_mode_falls_back_per_field():
    env = {"SUNSHINE_CLIENT_WIDTH": "not-a-number", "SUNSHINE_CLIENT_HEIGHT": "-4"}
    mode = client_mode(env)
    assert mode == Mode(1920, 1080, 60)


def test_cvt_rb_1080p60_matches_reference():
    # Reference CVT-RB values for 1920x1080@60: 138.50 MHz? (spec quantizes
    # to 0.25 MHz); htotal is always width+160 under reduced blanking.
    t = cvt_rb(Mode(1920, 1080, 60))
    assert t.h_total == 2080
    assert 138_000 <= t.pixel_clock_khz <= 139_000
    assert t.v_total > 1080
    assert t.v_front == 3


def test_cvt_rb_blanking_budget_holds_at_high_refresh():
    t = cvt_rb(Mode(2420, 1668, 120))
    assert t.h_total == 2420 + 160
    assert t.v_back >= 6  # CVT-RB minimum back porch
    # Vertical blanking must cover the 460µs minimum.
    line_time_us = t.h_total / (t.pixel_clock_khz / 1000.0)
    assert (t.v_total - t.mode.height) * line_time_us >= 459


def test_xrandr_modeline_shape():
    args = cvt_rb(Mode(1920, 1080, 60)).xrandr_modeline()
    assert args[0] == "zvdd_1920x1080_60"
    assert len(args) == 12
    assert args[-2:] == ["+hsync", "-vsync"]
