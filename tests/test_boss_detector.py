from pathlib import Path

import cv2
import numpy as np
import pytest

from boss_detector import BossDetector


def test_boss_dead_requires_two_failures():
    detector = BossDetector()
    assert detector.is_alive("HP 12") is False
    assert detector.is_alive("HP 128") is True
    assert detector.update_dead_streak("") is False
    assert detector.update_dead_streak("128") is False
    assert detector.update_dead_streak("") is False
    assert detector.update_dead_streak("") is True


def test_read_hp_keeps_injected_full_frame_capture_contract():
    seen = []

    detector = BossDetector(
        ocr=type(
            "OCR",
            (),
            {
                "read": (
                    lambda self, image:
                    seen.append(image.shape)
                    or "HP 128"
                )
            },
        )(),
        capture=lambda hwnd: np.zeros(
            (180, 320),
            dtype=np.uint8,
        ),
        # Missing marker asset exercises the preserved immediate OCR
        # compatibility fallback.
        assets_dir=Path("missing-assets"),
        ocr_fallback_enabled=True,
    )

    context = type(
        "Context",
        (),
        {
            "window_handle": 7,
        },
    )()

    assert detector.read_hp(context) == "HP 128"
    assert seen == [(88, 264)]


def test_visual_alive_skips_ocr_on_roi_capture_fast_path():
    roi = np.zeros((22, 66), dtype=np.uint8)

    # Three separated bright glyph-like components.
    roi[5:10, 5:7] = 255
    roi[5:10, 15:17] = 255
    roi[5:10, 25:27] = 255

    class OCR:
        def read(self, image):
            raise AssertionError("OCR must be skipped when visual HP is alive")

    detector = BossDetector(
        ocr=OCR(),
        capture=lambda hwnd: np.zeros(
            (484, 860),
            dtype=np.uint8,
        ),
        roi_capture=lambda hwnd, base_roi: roi,
    )

    context = type(
        "Context",
        (),
        {
            "window_handle": 7,
            "window_width": 860,
            "window_height": 484,
            "profile_id": "p1",
        },
    )()

    assert detector.read_hp(context) == ""
    assert detector.last_debug["visual_alive"] is True
    assert detector.last_debug["visual_glyphs"] >= 3
    assert detector.last_debug["chosen"] == "visual"
    assert detector.last_debug["attempts"] == []


def test_identical_roi_reuses_previous_ocr_result():
    roi = np.zeros((22, 66), dtype=np.uint8)
    calls = []

    class OCR:
        def read(self, image):
            calls.append(image.shape)
            return "HP 128"

    detector = BossDetector(
        ocr=OCR(),
        capture=lambda hwnd: np.zeros(
            (484, 860),
            dtype=np.uint8,
        ),
        roi_capture=lambda hwnd, base_roi: roi.copy(),
        ocr_fallback_enabled=True,
    )

    context = type(
        "Context",
        (),
        {
            "window_handle": 7,
            "window_width": 860,
            "window_height": 484,
            "profile_id": "p1",
        },
    )()

    assert detector.read_hp(context) == "HP 128"
    assert detector.read_hp(context) == "HP 128"

    # First scan OCRs once because the first grayscale attempt succeeds.
    # Second scan is byte-identical and should use the cached OCR result.
    assert calls == [(88, 264)]
    assert detector.last_debug["chosen"] == "cache"


def test_ocr_service_reports_missing_windows_executable():
    from ocr_service import OcrConfigurationError, OcrService

    with pytest.raises(
        OcrConfigurationError,
        match="Tesseract executable not found",
    ):
        OcrService(
            "Z:/definitely-missing/tesseract.exe"
        )


def test_ocr_service_default_reports_configuration_not_type_error(monkeypatch):
    from ocr_service import OcrConfigurationError, OcrService

    monkeypatch.setattr(
        "shutil.which",
        lambda name: None,
    )
    monkeypatch.setattr(
        "ocr_service.Path.is_file",
        lambda path: False,
    )

    with pytest.raises(
        OcrConfigurationError,
        match="Tesseract executable not found",
    ):
        OcrService()



def test_default_debug_does_not_capture_full_frame(monkeypatch):
    roi = np.zeros(
        (22, 66),
        dtype=np.uint8,
    )
    full_capture_calls = []

    class OCR:
        def read(self, image):
            return ""

    detector = BossDetector(
        ocr=OCR(),
        capture=lambda hwnd: (
            full_capture_calls.append(hwnd)
            or np.zeros(
                (484, 860),
                dtype=np.uint8,
            )
        ),
        roi_capture=lambda hwnd, base_roi: roi.copy(),
        ocr_fallback_enabled=True,
    )

    context = type(
        "Context",
        (),
        {
            "window_handle": 7,
            "window_width": 860,
            "window_height": 484,
            "profile_id": "p1",
        },
    )()

    monkeypatch.setattr(
        "boss_detector.cv2.imwrite",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        "boss_detector.Path.mkdir",
        lambda *args, **kwargs: None,
    )

    assert detector.read_hp(context) == ""
    assert full_capture_calls == []
    assert detector.last_debug["debug_dir"] is not None



def test_boss_alive_marker_match_skips_ocr():
    from automation_constants import BOSS_ALIVE_ASSET, BOSS_ALIVE_MARKER

    template = cv2.imread(
        str(
            Path("assets")
            / BOSS_ALIVE_ASSET
        ),
        cv2.IMREAD_GRAYSCALE,
    )
    assert template is not None

    frame = np.zeros(
        (484, 860),
        dtype=np.uint8,
    )

    x, y, w, h = BOSS_ALIVE_MARKER
    frame[
        y:y + h,
        x:x + w,
    ] = template

    class OCR:
        def read(self, image):
            raise AssertionError(
                "OCR must not run when boss marker matches"
            )

    detector = BossDetector(
        ocr=OCR(),
        capture=lambda hwnd: frame.copy(),
    )

    context = type(
        "Context",
        (),
        {
            "window_handle": 7,
            "profile_id": "p1",
        },
    )()

    assert detector.read_hp(context) == ""
    assert detector.last_debug["chosen"] == "asset"
    assert detector.last_debug["asset_alive"] is True
    assert detector.last_debug["asset_score"] >= 0.82
    assert detector.last_debug["asset_miss_streak"] == 0


def test_boss_marker_must_miss_three_scans_before_ocr_fallback():
    calls = []

    class OCR:
        def read(self, image):
            calls.append(
                image.shape
            )
            return "HP 128"

    detector = BossDetector(
        ocr=OCR(),
        capture=lambda hwnd: np.zeros(
            (484, 860),
            dtype=np.uint8,
        ),
        ocr_fallback_enabled=True,
    )

    context = type(
        "Context",
        (),
        {
            "window_handle": 7,
            "profile_id": "p1",
        },
    )()

    # First two consecutive marker misses stay entirely in the cheap path.
    assert detector.read_hp(context) == ""
    assert detector.last_debug["chosen"] == "asset_miss"
    assert detector.last_debug["asset_miss_streak"] == 1
    assert calls == []

    assert detector.read_hp(context) == ""
    assert detector.last_debug["chosen"] == "asset_miss"
    assert detector.last_debug["asset_miss_streak"] == 2
    assert calls == []

    # Third consecutive miss enables the existing OCR safety fallback.
    assert detector.read_hp(context) == "HP 128"
    assert calls == [
        (88, 264),
    ]
    assert detector.last_debug["asset_miss_streak"] == 0



def test_runtime_default_does_not_call_ocr_after_marker_misses():
    calls = []

    class OCR:
        def read(self, image):
            calls.append(image.shape)
            return "HP 128"

    detector = BossDetector(
        ocr=OCR(),
        capture=lambda hwnd: np.zeros(
            (484, 860),
            dtype=np.uint8,
        ),
    )

    context = type(
        "Context",
        (),
        {
            "window_handle": 7,
            "profile_id": "p1",
        },
    )()

    for _ in range(5):
        assert detector.read_hp(context) == ""

    assert calls == []
    assert detector.last_debug["chosen"] == "ocr_disabled"
    assert detector.last_debug["asset_alive"] is False
    assert detector.last_debug["asset_miss_streak"] >= 3
