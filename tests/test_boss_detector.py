from unittest.mock import patch
from pathlib import Path

import cv2
import numpy as np
import pytest

from automation_constants import (
    BOSS_ALIVE_MARKER,
    BOSS_ASSET_DIRS,
    BOSS_ASSET_SCAN_ROI,
    BOSS_NAME_ROI,
)
from boss_detector import BossDetector
from vision import scale_roi


def _context(
    selected_boss="trom_cho",
    width=860,
    height=484,
):
    return type(
        "Context",
        (),
        {
            "window_handle": 7,
            "window_width": width,
            "window_height": height,
            "profile_id": "p1",
            "selected_boss": selected_boss,
        },
    )()


def _boss_search_roi():
    return BOSS_ASSET_SCAN_ROI


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


def test_uploaded_boss_assets_fit_configured_scan_box():
    detector = BossDetector()

    for boss_key, folder_name in (
        BOSS_ASSET_DIRS.items()
    ):
        templates = detector._load_boss_templates(
            boss_key
        )

        assert templates, folder_name

        for asset_name, template in templates:
            height, width = template.shape[:2]

            assert width <= BOSS_ASSET_SCAN_ROI[2], (
                boss_key,
                asset_name,
                width,
            )
            assert height <= BOSS_ASSET_SCAN_ROI[3], (
                boss_key,
                asset_name,
                height,
            )


def test_selected_boss_uses_its_own_asset_directory():
    detector = BossDetector()

    trom = detector._load_boss_templates(
        "trom_cho"
    )
    ngao = detector._load_boss_templates(
        "ngao_op"
    )
    dai = detector._load_boss_templates(
        "dai_tho_san"
    )

    assert trom[0][0] == "1.jpg"
    assert ngao[0][0] == "1.jpg"
    assert dai[0][0] == "1.jpg"

    assert trom[0][1].shape == (40, 34)
    assert ngao[0][1].shape == (37, 34)
    assert dai[0][1].shape == (37, 36)


def test_asset_match_is_immediate_alive_and_skips_ocr():
    template = _marker_template()
    calls = []

    class OCR:
        def read_text(self, image):
            calls.append(image.shape)
            return "Trm cho"

    def capture_roi(hwnd, base_roi):
        if base_roi == _boss_search_roi():
            search = np.zeros(
                (
                    base_roi[3],
                    base_roi[2],
                ),
                dtype=np.uint8,
            )
            x = 8
            y = 5
            search[
                y:y + template.shape[0],
                x:x + template.shape[1],
            ] = template
            return search
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


def test_asset_match_tolerates_marker_position_shift_inside_search_roi():
    template = _marker_template()
    search_roi = _boss_search_roi()

    search = np.zeros(
        (
            search_roi[3],
            search_roi[2],
        ),
        dtype=np.uint8,
    )

    # Put the template away from the expected origin to prove the detector
    # searches the whole configured box instead of one fixed pixel location.
    x = 12
    y = 3
    search[
        y:y + template.shape[0],
        x:x + template.shape[1],
    ] = template

    detector = BossDetector(
        roi_capture=(
            lambda hwnd, base_roi:
            search.copy()
        ),
        marker_template=template,
    )

    detector.read_hp(
        _context()
    )

    assert detector.last_debug[
        "asset_alive"
    ] is True
    assert detector.last_debug[
        "asset_score"
    ] >= 0.99


def test_asset_match_at_320x180_uses_native_scaled_template():
    template = _marker_template()
    search_roi = _boss_search_roi()

    canonical = np.zeros(
        (
            search_roi[3],
            search_roi[2],
        ),
        dtype=np.uint8,
    )
    x = 9
    y = 5
    canonical[
        y:y + template.shape[0],
        x:x + template.shape[1],
    ] = template

    (
        _x,
        _y,
        search_width,
        search_height,
    ) = scale_roi(
        search_roi,
        320,
        180,
    )

    native_search = cv2.resize(
        canonical,
        (
            search_width,
            search_height,
        ),
        interpolation=cv2.INTER_AREA,
    )

    detector = BossDetector(
        roi_capture=(
            lambda hwnd, base_roi:
            native_search.copy()
        ),
        marker_template=template,
    )

    detector.read_hp(
        _context(
            width=320,
            height=180,
        )
    )

    assert detector.last_debug[
        "asset_alive"
    ] is True
    assert detector.last_debug[
        "asset_score"
    ] >= 0.7


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

        if base_roi == _boss_search_roi():
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
        _boss_search_roi(),
        _boss_search_roi(),
        _boss_search_roi(),
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
