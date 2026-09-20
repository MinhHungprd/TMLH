from boss_detector import BossDetector


def test_boss_dead_requires_two_failures():
    detector = BossDetector()
    assert detector.is_alive("HP 12")
    assert detector.update_dead_streak("") is False
    assert detector.update_dead_streak("128") is False
    assert detector.update_dead_streak("") is False
    assert detector.update_dead_streak("") is True


def test_read_hp_crops_scaled_roi_and_upscales_for_ocr():
    import numpy as np
    seen = []
    detector = BossDetector(
        ocr=type("OCR", (), {"read": lambda self, image: seen.append(image.shape) or "HP 12"})(),
        capture=lambda hwnd: np.zeros((180, 320), dtype=np.uint8),
    )
    context = type("Context", (), {"window_handle": 7})()
    assert detector.read_hp(context) == "HP 12"
    assert seen == [(22, 66)]


def test_ocr_service_reports_missing_windows_executable():
    from ocr_service import OcrConfigurationError, OcrService
    import pytest
    with pytest.raises(OcrConfigurationError, match="Tesseract executable not found"):
        OcrService("Z:/definitely-missing/tesseract.exe")


def test_ocr_service_default_reports_configuration_not_type_error(monkeypatch):
    from ocr_service import OcrConfigurationError, OcrService
    import pytest
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr("ocr_service.Path.is_file", lambda path: False)
    with pytest.raises(OcrConfigurationError, match="Tesseract executable not found"):
        OcrService()
