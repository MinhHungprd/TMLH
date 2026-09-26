import numpy as np

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
    assert [check.signal_name for check in checks] == [
        "s1",
        "s2",
        "s3",
    ]
    assert checks[1].coordinates == (284, 167)
    assert checks[2].coordinates == (160, 151)



def test_native_asset_scan_crops_small_regions_without_full_frame_resize():
    from profile_models import ProfileRuntimeContext

    raw = np.zeros(
        (180, 320),
        dtype=np.uint8,
    )

    detector = GameStateDetector(
        capture=lambda hwnd: raw,
    )

    seen = []

    def match_region(
        region,
        asset_name,
        search_roi,
    ):
        seen.append(
            (
                region.shape,
                asset_name,
                search_roi,
            )
        )
        return "x742_y437" in asset_name

    detector._match_region = match_region

    ctx = ProfileRuntimeContext(
        "id",
        "p",
        "path",
        "trom_cho",
        320,
        180,
    )
    ctx.window_handle = 9

    checks = detector.check_signals(ctx)

    assert len(seen) == 3
    assert all(
        height < raw.shape[0]
        and width < raw.shape[1]
        for (height, width), _asset, _roi
        in seen
    )
    assert checks[0].detected is False
    assert checks[1].action == CLICK_CENTER
    assert checks[1].coordinates == (
        284,
        167,
    )
    assert checks[2].detected is False



def test_check_single_signal_scales_login_ready_marker():
    from profile_models import ProfileRuntimeContext

    ctx = ProfileRuntimeContext(
        "id",
        "p",
        "path",
        "trom_cho",
        480,
        270,
    )
    ctx.window_handle = 9

    detector = GameStateDetector(
        lambda name, roi:
        name == "s1"
    )

    check = detector.check_signal(
        ctx,
        "s1",
    )

    assert check.detected is True
    assert check.action is None
    assert check.signal_name == "s1"


def test_check_single_start_signal_returns_scaled_center():
    from profile_models import ProfileRuntimeContext

    ctx = ProfileRuntimeContext(
        "id",
        "p",
        "path",
        "trom_cho",
        480,
        270,
    )
    ctx.window_handle = 9

    detector = GameStateDetector(
        lambda name, roi:
        name == "s3"
    )

    check = detector.check_signal(
        ctx,
        "s3",
    )

    assert check.detected is True
    assert check.action == CLICK_CENTER
    assert check.signal_name == "s3"
    assert check.coordinates == (
        239,
        226,
    )
