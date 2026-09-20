from automation_constants import SIGNAL_1, SIGNAL_2, SIGNAL_3
from game_state import CLICK_CENTER, GameStateDetector


def test_signals_return_actions_without_clicking():
    detector = GameStateDetector(lambda asset, roi: True)
    assert detector.inspect("s1", SIGNAL_1).action is None
    assert detector.inspect("s2", SIGNAL_2).action == CLICK_CENTER
    assert detector.inspect("s3", SIGNAL_3).action == CLICK_CENTER


def test_check_signals_scales_actions_per_profile_without_mouse_input():
    from profile_models import ProfileRuntimeContext
    ctx = ProfileRuntimeContext("id", "p", "path", "trom_cho", 320, 180)
    ctx.window_handle = 9
    detector = GameStateDetector(lambda name, roi: name in ("s2", "s3"))
    checks = detector.check_signals(ctx)
    assert [check.action for check in checks] == [None, CLICK_CENTER, CLICK_CENTER]
    assert checks[1].coordinates == (284, 167)
    assert checks[2].coordinates == (160, 151)
