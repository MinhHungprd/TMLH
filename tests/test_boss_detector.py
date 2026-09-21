import numpy as np
import pytest

from automation_constants import BOSS_NAME_ROI
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


def test_legacy_hp_alive_helper_is_kept():
    detector = BossDetector()
    assert detector.is_alive("HP 12") is False
    assert detector.is_alive("HP 128") is True
    assert detector.update_dead_streak("") is False
    assert detector.update_dead_streak("128") is False
    assert detector.update_dead_streak("") is False
    assert detector.update_dead_streak("") is True


def test_normalize_boss_name_removes_vietnamese_accents():
    assert (
        BossDetector.normalize_boss_name(
            "Trộm chó"
        )
        == "tromcho"
    )
    assert (
        BossDetector.normalize_boss_name(
            "Ngáo ộp"
        )
        == "ngaoop"
    )
    assert (
        BossDetector.normalize_boss_name(
            "Đại thợ săn"
        )
        == "daithosan"
    )


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
        ("Dai tho sa", "dai_tho_san"),
    ],
)
def test_fuzzy_name_match_tolerates_missing_ocr_characters(
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


def test_fuzzy_name_match_rejects_different_selected_boss():
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


def test_too_short_ocr_fragment_is_not_enough():
    (
        matched,
        _ratio,
        _coverage,
        observed,
        _expected,
    ) = BossDetector.match_selected_boss(
        "cho",
        "trom_cho",
    )

    assert observed == "cho"
    assert matched is False


def test_name_ocr_uses_requested_box_and_matches_selected_boss():
    seen_rois = []
    ocr_calls = []

    class OCR:
        def read_text(
            self,
            image,
        ):
            ocr_calls.append(
                image.shape
            )
            return "Trm cho"

    def capture_roi(
        hwnd,
        base_roi,
    ):
        seen_rois.append(
            base_roi
        )
        return np.zeros(
            (
                base_roi[3],
                base_roi[2],
            ),
            dtype=np.uint8,
        )

    detector = BossDetector(
        ocr=OCR(),
        roi_capture=capture_roi,
    )

    text = detector.read_hp(
        _context("trom_cho")
    )

    assert text == "Trm cho"
    assert seen_rois == [
        BOSS_NAME_ROI,
    ]
    assert len(ocr_calls) == 1
    assert detector.last_debug[
        "name_alive"
    ] is True
    assert detector.last_debug[
        "name_expected"
    ] == "tromcho"
    assert detector.last_debug[
        "name_normalized"
    ] == "trmcho"


def test_name_ocr_does_not_accept_wrong_boss_name():
    class OCR:
        def read_text(
            self,
            image,
        ):
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
    )

    detector.read_hp(
        _context("trom_cho")
    )

    assert detector.last_debug[
        "name_alive"
    ] is False


def test_identical_name_frame_reuses_ocr_cache():
    calls = []

    class OCR:
        def read_text(
            self,
            image,
        ):
            calls.append(
                image.shape
            )
            return "Trom cho"

    roi = np.zeros(
        (
            BOSS_NAME_ROI[3],
            BOSS_NAME_ROI[2],
        ),
        dtype=np.uint8,
    )

    detector = BossDetector(
        ocr=OCR(),
        roi_capture=(
            lambda hwnd, base_roi:
            roi.copy()
        ),
    )

    context = _context(
        "trom_cho"
    )

    assert detector.read_hp(
        context
    ) == "Trom cho"
    assert detector.read_hp(
        context
    ) == "Trom cho"

    assert len(calls) == 1
    assert detector.last_debug[
        "cache_hit"
    ] is True
    assert detector.last_debug[
        "name_alive"
    ] is True


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


def test_ocr_service_default_reports_configuration_not_type_error(
    monkeypatch,
):
    from ocr_service import (
        OcrConfigurationError,
        OcrService,
    )

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
        match=(
            "Tesseract executable not found"
        ),
    ):
        OcrService()



def test_negative_name_ocr_is_not_cached():
    outputs = iter(
        [
            "",
            "Trm cho",
        ]
    )
    calls = []

    class OCR:
        def read_text(
            self,
            image,
        ):
            calls.append(
                image.shape
            )
            return next(outputs)

    roi = np.zeros(
        (
            BOSS_NAME_ROI[3],
            BOSS_NAME_ROI[2],
        ),
        dtype=np.uint8,
    )

    detector = BossDetector(
        ocr=OCR(),
        roi_capture=(
            lambda hwnd, base_roi:
            roi.copy()
        ),
    )

    context = _context(
        "trom_cho"
    )

    assert detector.read_hp(
        context
    ) == ""
    assert detector.last_debug[
        "name_alive"
    ] is False
    assert detector.last_debug[
        "cache_hit"
    ] is False

    assert detector.read_hp(
        context
    ) == "Trm cho"
    assert detector.last_debug[
        "name_alive"
    ] is True

    # Same pixels were OCR'd again because the first negative result was not
    # cached.
    assert len(calls) == 2


def test_multi_variant_block_chooses_matching_candidate():
    calls = []

    class OCR:
        def read_text_block(
            self,
            image,
        ):
            calls.append(
                image.shape
            )
            return (
                "panes\n"
                "Trm cho\n"
                "qqmae\n"
            )

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
    )

    assert detector.read_hp(
        _context("trom_cho")
    ) == "Trm cho"

    assert len(calls) == 1
    assert detector.last_debug[
        "name_alive"
    ] is True
    assert detector.last_debug[
        "ocr_candidates"
    ] == [
        "panes",
        "Trm cho",
        "qqmae",
        "panes Trm cho qqmae",
    ]


def test_local_fuzzy_match_handles_junk_around_missing_name():
    (
        matched,
        ratio,
        coverage,
        observed,
        expected,
    ) = BossDetector.match_selected_boss(
        "__ Trm cho |",
        "trom_cho",
    )

    assert matched is True
    assert observed == "trmcho"
    assert expected == "tromcho"
    assert ratio > 0.8
    assert coverage > 0.7
