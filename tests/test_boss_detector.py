from unittest.mock import patch
from pathlib import Path

import numpy as np
import pytest

from automation_constants import (
    BOSS_ALIVE_MARKER,
    BOSS_NAME_ROI,
)
from boss_detector import BossDetector


def _context(
    selected_boss="trom_cho",
):
    return type(
        "Context",
        (),
        {
            "window_handle": 7,
            "window_width": 860,
            "window_height": 484,
            "profile_id": "p1",
            "selected_boss": selected_boss,
        },
    )()


def _marker_template():
    image = np.zeros(
        (
            BOSS_ALIVE_MARKER[3],
            BOSS_ALIVE_MARKER[2],
        ),
        dtype=np.uint8,
    )
    image[2:16:3, 1:11:2] = 255
    image[5:14, 5:7] = 180
    return image


def test_marker_template_is_shared_across_workers():
    first = BossDetector()
    second = BossDetector()

    first_template = first._load_marker_template()
    second_template = second._load_marker_template()

    assert first_template is not None
    assert first_template is second_template


def test_asset_match_is_immediate_alive_and_skips_ocr():
    template = _marker_template()
    calls = []

    class OCR:
        def read_text(self, image):
            calls.append(image.shape)
            return "Trm cho"

    def capture_roi(hwnd, base_roi):
        if base_roi == BOSS_ALIVE_MARKER:
            return template.copy()
        raise AssertionError(
            "OCR ROI must not be captured when asset matches"
        )

    detector = BossDetector(
        ocr=OCR(),
        roi_capture=capture_roi,
        marker_template=template,
    )

    assert detector.read_hp(
        _context()
    ) == ""

    assert calls == []
    assert detector.last_debug[
        "asset_alive"
    ] is True
    assert detector.last_debug[
        "asset_score"
    ] >= 0.99
    assert detector.last_debug[
        "fallback_ocr"
    ] is False


def test_asset_must_miss_three_scans_before_ocr_fallback():
    template = _marker_template()
    ocr_calls = []
    seen_rois = []

    class OCR:
        def read_text(self, image):
            ocr_calls.append(image.shape)
            return "Trm cho"

    def capture_roi(hwnd, base_roi):
        seen_rois.append(base_roi)

        if base_roi == BOSS_ALIVE_MARKER:
            return np.zeros(
                (
                    base_roi[3],
                    base_roi[2],
                ),
                dtype=np.uint8,
            )

        if base_roi == BOSS_NAME_ROI:
            return np.zeros(
                (
                    base_roi[3],
                    base_roi[2],
                ),
                dtype=np.uint8,
            )

        raise AssertionError(base_roi)

    detector = BossDetector(
        ocr=OCR(),
        roi_capture=capture_roi,
        marker_template=template,
    )
    context = _context()

    ocr_enabled = patch(
        "boss_detector.BOSS_OCR_ENABLED",
        True,
    )
    ocr_enabled.start()

    assert detector.read_hp(context) == ""
    assert detector.last_debug[
        "asset_miss_streak"
    ] == 1
    assert detector.last_debug[
        "fallback_ocr"
    ] is False
    assert ocr_calls == []

    assert detector.read_hp(context) == ""
    assert detector.last_debug[
        "asset_miss_streak"
    ] == 2
    assert detector.last_debug[
        "fallback_ocr"
    ] is False
    assert ocr_calls == []

    assert detector.read_hp(
        context
    ) == "Trm cho"
    assert detector.last_debug[
        "fallback_ocr"
    ] is True
    assert detector.last_debug[
        "name_alive"
    ] is True

    # Successful OCR rescue restarts the asset miss window.
    assert detector.last_debug[
        "asset_miss_streak"
    ] == 0
    assert len(ocr_calls) == 1

    assert seen_rois == [
        BOSS_ALIVE_MARKER,
        BOSS_ALIVE_MARKER,
        BOSS_ALIVE_MARKER,
        BOSS_NAME_ROI,
    ]
    ocr_enabled.stop()


def test_wrong_ocr_fallback_does_not_rescue_asset_miss():
    template = _marker_template()

    class OCR:
        def read_text(self, image):
            return "Ngáo ộp"

    detector = BossDetector(
        ocr=OCR(),
        roi_capture=(
            lambda hwnd, base_roi:
            np.zeros(
                (
                    base_roi[3],
                    base_roi[2],
                ),
                dtype=np.uint8,
            )
        ),
        marker_template=template,
    )

    context = _context(
        "trom_cho"
    )

    with patch(
        "boss_detector.BOSS_OCR_ENABLED",
        True,
    ):
        detector.read_hp(context)
        detector.read_hp(context)
        detector.read_hp(context)

    assert detector.last_debug[
        "asset_alive"
    ] is False
    assert detector.last_debug[
        "fallback_ocr"
    ] is True
    assert detector.last_debug[
        "name_alive"
    ] is False


def test_missing_marker_asset_falls_back_to_ocr_immediately():
    calls = []

    class OCR:
        def read_text(self, image):
            calls.append(image.shape)
            return "Trom cho"

    detector = BossDetector(
        ocr=OCR(),
        roi_capture=(
            lambda hwnd, base_roi:
            np.zeros(
                (
                    base_roi[3],
                    base_roi[2],
                ),
                dtype=np.uint8,
            )
        ),
        assets_dir=Path(
            "definitely-missing-assets"
        ),
    )

    with patch(
        "boss_detector.BOSS_OCR_ENABLED",
        True,
    ):
        assert detector.read_hp(
            _context()
        ) == "Trom cho"

    assert len(calls) == 1
    assert detector.last_debug[
        "fallback_ocr"
    ] is True
    assert detector.last_debug[
        "name_alive"
    ] is True
    assert detector.last_debug[
        "asset_error"
    ]


def test_reset_cycle_clears_asset_miss_state():
    template = _marker_template()

    detector = BossDetector(
        ocr=type(
            "OCR",
            (),
            {
                "read_text": (
                    lambda self, image:
                    ""
                )
            },
        )(),
        roi_capture=(
            lambda hwnd, base_roi:
            np.zeros(
                (
                    base_roi[3],
                    base_roi[2],
                ),
                dtype=np.uint8,
            )
        ),
        marker_template=template,
    )

    detector.read_hp(
        _context()
    )

    assert detector.last_debug[
        "asset_miss_streak"
    ] == 1

    detector.reset_cycle()

    assert detector._asset_miss_streak == 0


@pytest.mark.parametrize(
    ("ocr_text", "selected_boss"),
    [
        ("Trộm chó", "trom_cho"),
        ("Trom cho", "trom_cho"),
        ("Trm cho", "trom_cho"),
        ("Trom ch", "trom_cho"),
        ("Ngáo ộp", "ngao_op"),
        ("Nga op", "ngao_op"),
        ("Ngao p", "ngao_op"),
        ("Đại thợ săn", "dai_tho_san"),
        ("Dai tho san", "dai_tho_san"),
        ("Dai th san", "dai_tho_san"),
    ],
)
def test_fuzzy_ocr_fallback_tolerates_missing_characters(
    ocr_text,
    selected_boss,
):
    (
        matched,
        ratio,
        coverage,
        _observed,
        _expected,
    ) = BossDetector.match_selected_boss(
        ocr_text,
        selected_boss,
    )

    assert matched is True
    assert ratio > 0
    assert coverage > 0


def test_fuzzy_ocr_rejects_other_boss():
    (
        matched,
        _ratio,
        _coverage,
        _observed,
        _expected,
    ) = BossDetector.match_selected_boss(
        "Ngáo ộp",
        "trom_cho",
    )

    assert matched is False


def test_ocr_service_reports_missing_windows_executable():
    from ocr_service import (
        OcrConfigurationError,
        OcrService,
    )

    with pytest.raises(
        OcrConfigurationError,
        match=(
            "Tesseract executable not found"
        ),
    ):
        OcrService(
            "Z:/definitely-missing/"
            "tesseract.exe"
        )



def test_runtime_asset_only_mode_never_calls_ocr():
    template = _marker_template()
    calls = []

    class OCR:
        def read_text(self, image):
            calls.append(image.shape)
            return "Trom cho"

    detector = BossDetector(
        ocr=OCR(),
        roi_capture=(
            lambda hwnd, base_roi:
            np.zeros(
                (
                    base_roi[3],
                    base_roi[2],
                ),
                dtype=np.uint8,
            )
        ),
        marker_template=template,
    )

    context = _context()

    for _ in range(5):
        assert detector.read_hp(
            context
        ) == ""

    assert calls == []
    assert detector.last_debug[
        "chosen"
    ] == "ocr_disabled"
    assert detector.last_debug[
        "fallback_ocr"
    ] is False
    assert detector.last_debug[
        "asset_alive"
    ] is False
