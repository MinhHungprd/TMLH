import numpy as np

from automation_constants import SIGNAL_1, SIGNAL_2, SIGNAL_3
from vision import ScaledAssetCache, normalize_roi_to_base, roi_center, scale_roi


def test_scale_roi_reference_values():
    assert scale_roi(SIGNAL_1, 320, 180) == (296, 11, 11, 14)
    assert scale_roi(SIGNAL_2, 480, 270) == (414, 244, 25, 13)
    assert scale_roi(SIGNAL_3, 640, 360) == (292, 289, 56, 26)
    assert roi_center((296, 11, 11, 14)) == (301, 18)


def test_scaled_asset_cache_reuses_template():
    cache = ScaledAssetCache("assets")
    first = cache.get("asset__x795_y29_w29_h38.png", 320, 180)
    second = cache.get("asset__x795_y29_w29_h38.png", 320, 180)
    assert first.shape == (14, 11)
    assert first is second


def test_scaled_asset_cache_is_shared_across_workers():
    first_cache = ScaledAssetCache("assets")
    second_cache = ScaledAssetCache("assets")

    first = first_cache.get(
        "asset__x795_y29_w29_h38.png",
        320,
        180,
    )
    second = second_cache.get(
        "asset__x795_y29_w29_h38.png",
        320,
        180,
    )

    assert first is second



def test_normalize_roi_to_base_only_resizes_the_roi():
    small = np.zeros((8, 25), dtype=np.uint8)
    normalized = normalize_roi_to_base(
        small,
        (375, 8, 66, 22),
    )
    assert normalized.shape == (22, 66)
