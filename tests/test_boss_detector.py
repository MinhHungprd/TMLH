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
