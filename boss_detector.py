import re
import time
import unicodedata
from difflib import SequenceMatcher

import cv2
import numpy as np

from automation_constants import (
    BOSS_NAME_LABELS,
    BOSS_NAME_MATCH_COVERAGE,
    BOSS_NAME_MATCH_RATIO,
    BOSS_NAME_MIN_CHARS,
    BOSS_NAME_ROI,
)
from ocr_service import OcrService
from perf_metrics import perf_timer
from vision import (
    capture_client,
    capture_client_roi,
    get_client_size,
    normalize_roi_to_base,
    normalize_to_base,
)


class BossDetector:
    """
    Boss presence detector based on OCR of the boss-name box.

    Production scans only BOSS_NAME_ROI=(361, 27, 91, 18), OCRs one text
    line, normalizes Vietnamese diacritics/common OCR confusions, then fuzzy
    matches the result against the selected boss name.

    The worker still owns the existing 1-second scan cadence and >=3-second
    continuous miss confirmation before declaring the boss dead.
    """

    def __init__(
        self,
        ocr=None,
        capture=None,
        roi_capture=None,
        *,
        now=None,
    ):
        self.dead_streak = 0
        self.ocr = ocr
        self.capture = capture or capture_client

        if roi_capture is not None:
            self.roi_capture = roi_capture
        elif capture is None:
            self.roi_capture = capture_client_roi
        else:
            self.roi_capture = None

        self.now = now or time.monotonic
        self.last_debug = {}

        # Cache the already-preprocessed OCR image. Boss names are static, so
        # this avoids spawning another Tesseract process when the name pixels
        # are unchanged between scans.
        self._last_name_image = None
        self._last_name_text = None

    @staticmethod
    def is_alive(text: str) -> bool:
        """Legacy HP-text helper kept for compatibility/tests."""
        digits = re.sub(
            r"\D",
            "",
            text or "",
        )
        return len(digits) >= 3

    def update_dead_streak(
        self,
        text: str,
    ) -> bool:
        """Legacy helper kept for compatibility/tests."""
        if self.is_alive(text):
            self.dead_streak = 0
            return False

        self.dead_streak += 1
        return self.dead_streak >= 2

    @staticmethod
    def _clean_text(text: str) -> str:
        return " ".join(
            (text or "")
            .replace("\r", " ")
            .replace("\n", " ")
            .split()
        )

    @staticmethod
    def normalize_boss_name(text: str) -> str:
        """
        Normalize OCR/name text for fuzzy comparison.

        - remove Vietnamese accents
        - map đ -> d
        - collapse spaces/punctuation
        - fix a few common OCR digit/letter confusions
        """
        value = (
            BossDetector._clean_text(text)
            .casefold()
            .replace("đ", "d")
        )

        value = "".join(
            char
            for char in unicodedata.normalize(
                "NFKD",
                value,
            )
            if not unicodedata.combining(char)
        )

        value = value.translate(
            str.maketrans(
                {
                    "0": "o",
                    "1": "i",
                    "5": "s",
                }
            )
        )

        return "".join(
            char
            for char in value
            if char.isalnum()
        )

    @staticmethod
    def _match_metrics(
        observed: str,
        expected: str,
    ) -> tuple[float, float]:
        if not observed or not expected:
            return 0.0, 0.0

        matcher = SequenceMatcher(
            None,
            observed,
            expected,
            autojunk=False,
        )

        ratio = float(
            matcher.ratio()
        )

        matched_chars = sum(
            block.size
            for block in matcher.get_matching_blocks()
        )

        coverage = (
            matched_chars
            / len(expected)
        )

        return ratio, coverage

    @classmethod
    def match_selected_boss(
        cls,
        text: str,
        selected_boss: str,
    ) -> tuple[
        bool,
        float,
        float,
        str,
        str,
    ]:
        """
        Fuzzy-match OCR output to the selected boss.

        Missing characters are allowed. To reduce false positives, the
        selected boss must both pass the ratio/coverage thresholds and be the
        best-scoring known boss name.
        """
        expected_label = (
            BOSS_NAME_LABELS.get(
                selected_boss,
                selected_boss or "",
            )
        )

        observed = cls.normalize_boss_name(
            text
        )
        expected = cls.normalize_boss_name(
            expected_label
        )

        if (
            len(observed)
            < BOSS_NAME_MIN_CHARS
            or not expected
        ):
            return (
                False,
                0.0,
                0.0,
                observed,
                expected,
            )

        ratio, coverage = (
            cls._match_metrics(
                observed,
                expected,
            )
        )

        # Strong containment handles OCR dropping a prefix/suffix while still
        # requiring a meaningful fraction of the expected name.
        containment = (
            (
                observed in expected
                or expected in observed
            )
            and len(observed)
            / len(expected)
            >= BOSS_NAME_MATCH_COVERAGE
        )

        all_scores = {}

        for key, label in (
            BOSS_NAME_LABELS.items()
        ):
            candidate = (
                cls.normalize_boss_name(
                    label
                )
            )
            candidate_ratio, _ = (
                cls._match_metrics(
                    observed,
                    candidate,
                )
            )
            all_scores[key] = (
                candidate_ratio
            )

        best_key = max(
            all_scores,
            key=all_scores.get,
        )

        selected_is_best = (
            best_key == selected_boss
        )

        matched = (
            selected_is_best
            and (
                containment
                or (
                    ratio
                    >= BOSS_NAME_MATCH_RATIO
                    and coverage
                    >= BOSS_NAME_MATCH_COVERAGE
                )
            )
        )

        return (
            matched,
            ratio,
            coverage,
            observed,
            expected,
        )

    @staticmethod
    def _prepare_name_image(
        roi,
    ):
        # 91x18 -> 364x72. A single preprocessed OCR attempt is intentionally
        # used to avoid the former gray/binary/inverted multi-process burst.
        upscaled = cv2.resize(
            roi,
            (
                BOSS_NAME_ROI[2] * 4,
                BOSS_NAME_ROI[3] * 4,
            ),
            interpolation=cv2.INTER_CUBIC,
        )

        blurred = cv2.GaussianBlur(
            upscaled,
            (3, 3),
            0,
        )

        _, binary = cv2.threshold(
            blurred,
            0,
            255,
            cv2.THRESH_BINARY
            + cv2.THRESH_OTSU,
        )

        # Tesseract generally prefers dark text on a light background.
        if float(binary.mean()) < 127.0:
            binary = cv2.bitwise_not(
                binary
            )

        return cv2.copyMakeBorder(
            binary,
            8,
            8,
            12,
            12,
            cv2.BORDER_CONSTANT,
            value=255,
        )

    def _capture_name_roi(
        self,
        context,
    ):
        raw_width = int(
            getattr(
                context,
                "window_width",
                0,
            )
            or 0
        )
        raw_height = int(
            getattr(
                context,
                "window_height",
                0,
            )
            or 0
        )

        if self.roi_capture is not None:
            if (
                raw_width <= 0
                or raw_height <= 0
            ):
                (
                    raw_width,
                    raw_height,
                ) = get_client_size(
                    context.window_handle
                )

            raw_roi = self.roi_capture(
                context.window_handle,
                BOSS_NAME_ROI,
            )

            roi = normalize_roi_to_base(
                raw_roi,
                BOSS_NAME_ROI,
            )

            return (
                roi,
                raw_width,
                raw_height,
            )

        raw = self.capture(
            context.window_handle
        )

        raw_height, raw_width = (
            raw.shape[:2]
        )

        normalized = normalize_to_base(
            raw
        )

        x, y, w, h = BOSS_NAME_ROI

        roi = normalized[
            y:y + h,
            x:x + w,
        ]

        return (
            roi,
            raw_width,
            raw_height,
        )

    def read_hp(
        self,
        context,
    ) -> str:
        # Keep the public method name to avoid changing worker integration.
        with perf_timer(
            "vision_ms"
        ):
            return self._read_name_impl(
                context
            )

    def _read_name_impl(
        self,
        context,
    ) -> str:
        (
            roi,
            raw_width,
            raw_height,
        ) = self._capture_name_roi(
            context
        )

        if roi.size == 0:
            raise RuntimeError(
                "Boss name ROI is outside "
                "game client area"
            )

        ocr_image = (
            self._prepare_name_image(
                roi
            )
        )

        cache_hit = (
            self._last_name_image
            is not None
            and self._last_name_text
            is not None
            and np.array_equal(
                ocr_image,
                self._last_name_image,
            )
        )

        if cache_hit:
            text = self._last_name_text
            source = "name_cache"
        else:
            if self.ocr is None:
                self.ocr = OcrService()

            if hasattr(
                self.ocr,
                "read_text",
            ):
                raw_text = self.ocr.read_text(
                    ocr_image
                )
            else:
                # Compatibility with lightweight injected OCR test doubles.
                raw_text = self.ocr.read(
                    ocr_image
                )

            text = self._clean_text(
                raw_text
            )

            self._last_name_image = (
                ocr_image.copy()
            )
            self._last_name_text = text
            source = "name_ocr"

        selected_boss = getattr(
            context,
            "selected_boss",
            "",
        )

        (
            name_alive,
            name_ratio,
            name_coverage,
            normalized_text,
            expected_name,
        ) = self.match_selected_boss(
            text,
            selected_boss,
        )

        self.last_debug = {
            "raw_size": (
                raw_width,
                raw_height,
            ),
            "roi": BOSS_NAME_ROI,
            "roi_shape": (
                roi.shape[1],
                roi.shape[0],
            ),
            "chosen": source,
            "text": text,
            "name_text": text,
            "name_normalized": (
                normalized_text
            ),
            "name_expected": (
                expected_name
            ),
            "name_ratio": round(
                name_ratio,
                4,
            ),
            "name_coverage": round(
                name_coverage,
                4,
            ),
            "name_alive": name_alive,
            "cache_hit": cache_hit,
            # Keep old debug keys present so logging/custom tooling does not
            # crash while the detector strategy changes.
            "asset_alive": False,
            "asset_score": 0.0,
            "asset_miss_streak": 0,
            "visual_glyphs": 0,
            "visual_alive": False,
            "alive": name_alive,
            "attempts": [
                (
                    source,
                    text,
                )
            ],
            "debug_dir": None,
        }

        return text
