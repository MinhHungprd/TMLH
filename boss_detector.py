import re
import threading
import time
import unicodedata
from pathlib import Path
from difflib import SequenceMatcher

import cv2
import numpy as np

from automation_constants import (
    BOSS_ALIVE_MATCH_THRESHOLD,
    BOSS_ASSET_DIRS,
    BOSS_ASSET_EXTENSIONS,
    BOSS_ASSET_MISSES_BEFORE_OCR,
    BOSS_ASSET_SCAN_ROI,
    BOSS_NAME_LABELS,
    BOSS_OCR_ENABLED,
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
    scale_roi,
)


_BOSS_TEMPLATE_CACHE = {}
_BOSS_TEMPLATE_CACHE_LOCK = threading.Lock()


class BossDetector:
    """
    Asset-first boss presence detector with OCR safety fallback.

    Runtime detection scans one canonical boss box and matches every image in
    the selected boss asset directory. Any template reaching the configured
    threshold means the boss is alive. OCR code is retained behind
    BOSS_OCR_ENABLED, but the current runtime mode keeps it disabled.
    """

    def __init__(
        self,
        ocr=None,
        capture=None,
        roi_capture=None,
        *,
        assets_dir=None,
        marker_template=None,
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

        self.assets_dir = (
            Path(assets_dir)
            if assets_dir is not None
            else (
                Path(__file__).resolve().parent
                / "assets"
            )
        )
        # marker_template is retained only as a test/backward-compatible
        # single-template injection. Normal runtime loads every image from the
        # selected boss folder.
        self._marker_template = marker_template
        self._marker_template_error = None
        self._scaled_marker_templates = {}
        self._last_asset_name = None
        self._last_asset_scores = {}
        self._last_asset_shapes = {}
        self._asset_miss_streak = 0

        # Positive-only OCR cache.
        self._last_name_image = None
        self._last_name_text = None
        self._last_name_candidates = None
        self._last_name_raw = None

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
    def _basic_match_metrics(
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
    def _match_metrics(
        cls,
        observed: str,
        expected: str,
    ) -> tuple[float, float]:
        """
        Direct fuzzy score plus a small sliding-window recovery.

        The local window handles OCR that adds junk before/after a partly
        missing boss name, e.g. "_ Trm cho |" instead of "Trộm chó".
        """
        best_ratio, best_coverage = (
            cls._basic_match_metrics(
                observed,
                expected,
            )
        )

        if len(observed) <= len(expected) + 1:
            return (
                best_ratio,
                best_coverage,
            )

        min_len = max(
            BOSS_NAME_MIN_CHARS,
            len(expected) - 2,
        )
        max_len = min(
            len(observed),
            len(expected) + 2,
        )

        for window_len in range(
            min_len,
            max_len + 1,
        ):
            for start in range(
                0,
                len(observed)
                - window_len
                + 1,
            ):
                part = observed[
                    start:
                    start + window_len
                ]

                ratio, coverage = (
                    cls._basic_match_metrics(
                        part,
                        expected,
                    )
                )

                if (
                    ratio,
                    coverage,
                ) > (
                    best_ratio,
                    best_coverage,
                ):
                    best_ratio = ratio
                    best_coverage = coverage

        return (
            best_ratio,
            best_coverage,
        )

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

        containment = (
            (
                observed in expected
                or expected in observed
            )
            and min(
                len(observed),
                len(expected),
            )
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
    def _white_background(
        image,
    ):
        if float(
            image.mean()
        ) < 127.0:
            return cv2.bitwise_not(
                image
            )
        return image

    @classmethod
    def _prepare_name_variants(
        cls,
        roi,
    ):
        """
        Build three OCR views from the same tiny ROI.

        All variants are handled inside one Tesseract process later, so this
        improves robustness without reintroducing the old 3-process OCR burst.
        """
        scale = 6

        upscaled = cv2.resize(
            roi,
            (
                BOSS_NAME_ROI[2] * scale,
                BOSS_NAME_ROI[3] * scale,
            ),
            interpolation=cv2.INTER_CUBIC,
        )

        # Variant 1: contrast-enhanced grayscale.
        clahe = cv2.createCLAHE(
            clipLimit=2.0,
            tileGridSize=(4, 4),
        )
        enhanced = clahe.apply(
            upscaled
        )

        # Variant 2: Otsu threshold.
        blurred = cv2.GaussianBlur(
            enhanced,
            (3, 3),
            0,
        )
        _, otsu = cv2.threshold(
            blurred,
            0,
            255,
            cv2.THRESH_BINARY
            + cv2.THRESH_OTSU,
        )
        otsu = cls._white_background(
            otsu
        )

        # Variant 3: adaptive threshold is often better when the name has a
        # glow/gradient or uneven UI background.
        adaptive = cv2.adaptiveThreshold(
            enhanced,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            31,
            7,
        )
        adaptive = cls._white_background(
            adaptive
        )

        # Keep grayscale as a third genuinely different input. Invert only
        # when the overall UI patch is dark so text remains dark-on-light.
        gray = cls._white_background(
            enhanced
        )

        variants = []

        for image in (
            gray,
            otsu,
            adaptive,
        ):
            variants.append(
                cv2.copyMakeBorder(
                    image,
                    10,
                    10,
                    14,
                    14,
                    cv2.BORDER_CONSTANT,
                    value=255,
                )
            )

        return variants

    @staticmethod
    def _stack_name_variants(
        variants,
    ):
        width = max(
            image.shape[1]
            for image in variants
        )
        separator_h = 18
        rows = []

        for index, image in enumerate(
            variants
        ):
            if image.shape[1] < width:
                image = cv2.copyMakeBorder(
                    image,
                    0,
                    0,
                    0,
                    width - image.shape[1],
                    cv2.BORDER_CONSTANT,
                    value=255,
                )

            rows.append(image)

            if index + 1 < len(
                variants
            ):
                rows.append(
                    np.full(
                        (
                            separator_h,
                            width,
                        ),
                        255,
                        dtype=np.uint8,
                    )
                )

        return np.vstack(rows)

    @classmethod
    def _ocr_candidates(
        cls,
        raw_text: str,
    ):
        candidates = []
        seen = set()

        for line in (
            raw_text
            or ""
        ).splitlines():
            cleaned = cls._clean_text(
                line
            )
            normalized = (
                cls.normalize_boss_name(
                    cleaned
                )
            )

            if (
                not cleaned
                or not normalized
                or normalized in seen
            ):
                continue

            seen.add(
                normalized
            )
            candidates.append(
                cleaned
            )

        whole = cls._clean_text(
            raw_text
        )
        whole_norm = (
            cls.normalize_boss_name(
                whole
            )
        )

        if (
            whole
            and whole_norm
            and whole_norm not in seen
        ):
            candidates.append(
                whole
            )

        return candidates

    @classmethod
    def _choose_candidate(
        cls,
        candidates,
        selected_boss,
    ):
        if not candidates:
            expected = (
                cls.normalize_boss_name(
                    BOSS_NAME_LABELS.get(
                        selected_boss,
                        selected_boss or "",
                    )
                )
            )
            return (
                "",
                False,
                0.0,
                0.0,
                "",
                expected,
            )

        ranked = []

        for candidate in candidates:
            (
                matched,
                ratio,
                coverage,
                observed,
                expected,
            ) = cls.match_selected_boss(
                candidate,
                selected_boss,
            )

            ranked.append(
                (
                    bool(matched),
                    ratio,
                    coverage,
                    len(observed),
                    candidate,
                    observed,
                    expected,
                )
            )

        best = max(
            ranked,
            key=lambda item: (
                item[0],
                item[1],
                item[2],
                item[3],
            ),
        )

        return (
            best[4],
            best[0],
            best[1],
            best[2],
            best[5],
            best[6],
        )

    def reset_cycle(self):
        """Reset per-boss-cycle state; keep both asset and OCR capability."""
        self._asset_miss_streak = 0
        self._last_name_image = None
        self._last_name_text = None
        self._last_name_candidates = None
        self._last_name_raw = None

    def _load_boss_templates(
        self,
        selected_boss: str,
    ):
        if self._marker_template is not None:
            return (
                ("<injected>", self._marker_template),
            )

        folder_name = BOSS_ASSET_DIRS.get(
            selected_boss
        )
        if not folder_name:
            raise RuntimeError(
                f"No boss asset directory mapping for {selected_boss!r}"
            )

        folder = self.assets_dir / folder_name
        cache_key = (
            str(folder.resolve()),
            selected_boss,
        )

        with _BOSS_TEMPLATE_CACHE_LOCK:
            cached = _BOSS_TEMPLATE_CACHE.get(
                cache_key
            )
            if cached is not None:
                return cached

            if not folder.is_dir():
                raise RuntimeError(
                    f"Boss asset directory not found: {folder}"
                )

            paths = sorted(
                path
                for path in folder.iterdir()
                if (
                    path.is_file()
                    and path.suffix.lower()
                    in BOSS_ASSET_EXTENSIONS
                )
            )

            templates = []
            invalid = []

            for path in paths:
                template = cv2.imread(
                    str(path),
                    cv2.IMREAD_GRAYSCALE,
                )
                if template is None:
                    invalid.append(
                        f"{path.name}: unreadable"
                    )
                    continue

                height, width = (
                    template.shape[:2]
                )
                if (
                    width > BOSS_ASSET_SCAN_ROI[2]
                    or height > BOSS_ASSET_SCAN_ROI[3]
                ):
                    invalid.append(
                        (
                            f"{path.name}: "
                            f"{width}x{height} exceeds "
                            f"{BOSS_ASSET_SCAN_ROI[2]}x"
                            f"{BOSS_ASSET_SCAN_ROI[3]}"
                        )
                    )
                    continue

                templates.append(
                    (
                        path.name,
                        template,
                    )
                )

            if not templates:
                detail = (
                    "; ".join(invalid)
                    if invalid
                    else "no image files"
                )
                raise RuntimeError(
                    (
                        f"No usable boss assets in {folder}: "
                        f"{detail}"
                    )
                )

            cached = tuple(templates)
            _BOSS_TEMPLATE_CACHE[
                cache_key
            ] = cached
            return cached

    # Backward-compatible helper kept for older tests/integrations.
    def _load_marker_template(self):
        try:
            return self._load_boss_templates(
                "trom_cho"
            )[0][1]
        except RuntimeError as exc:
            self._marker_template_error = str(exc)
            return None

    @staticmethod
    def _asset_search_roi():
        return BOSS_ASSET_SCAN_ROI

    def _capture_asset_roi(
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
        search_roi = self._asset_search_roi()

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
                search_roi,
            )
            return (
                raw_roi,
                raw_width,
                raw_height,
            )

        raw = self.capture(
            context.window_handle
        )
        raw_height, raw_width = raw.shape[:2]
        x, y, w, h = scale_roi(
            search_roi,
            raw_width,
            raw_height,
        )
        roi = raw[
            y:y + h,
            x:x + w,
        ]

        return (
            roi,
            raw_width,
            raw_height,
        )

    def _scaled_boss_template(
        self,
        selected_boss,
        asset_name,
        template,
        raw_width,
        raw_height,
    ):
        (
            _x,
            _y,
            target_width,
            target_height,
        ) = scale_roi(
            (
                0,
                0,
                template.shape[1],
                template.shape[0],
            ),
            raw_width,
            raw_height,
        )

        target_width = max(
            1,
            target_width,
        )
        target_height = max(
            1,
            target_height,
        )

        key = (
            selected_boss,
            asset_name,
            target_width,
            target_height,
        )
        scaled = self._scaled_marker_templates.get(
            key
        )

        if scaled is not None:
            return scaled

        if (
            template.shape[1] == target_width
            and template.shape[0] == target_height
        ):
            scaled = template
        else:
            interpolation = (
                cv2.INTER_AREA
                if (
                    target_width
                    < template.shape[1]
                    or target_height
                    < template.shape[0]
                )
                else cv2.INTER_CUBIC
            )
            scaled = cv2.resize(
                template,
                (
                    target_width,
                    target_height,
                ),
                interpolation=interpolation,
            )

        self._scaled_marker_templates[
            key
        ] = scaled
        return scaled

    def _match_asset(
        self,
        roi,
        raw_width,
        raw_height,
        selected_boss,
    ):
        self._last_asset_name = None
        self._last_asset_scores = {}
        self._last_asset_shapes = {}

        if roi is None or roi.size == 0:
            return (
                False,
                0.0,
                "boss asset search ROI unavailable",
            )

        try:
            templates = self._load_boss_templates(
                selected_boss
            )
        except RuntimeError as exc:
            self._marker_template_error = str(exc)
            return (
                False,
                0.0,
                self._marker_template_error,
            )

        best_score = -1.0
        best_name = None
        usable = 0

        for asset_name, template in templates:
            scaled = self._scaled_boss_template(
                selected_boss,
                asset_name,
                template,
                raw_width,
                raw_height,
            )

            self._last_asset_shapes[
                asset_name
            ] = (
                scaled.shape[1],
                scaled.shape[0],
            )

            if (
                roi.shape[0] < scaled.shape[0]
                or roi.shape[1] < scaled.shape[1]
            ):
                self._last_asset_scores[
                    asset_name
                ] = None
                continue

            usable += 1

            score = float(
                cv2.matchTemplate(
                    roi,
                    scaled,
                    cv2.TM_CCOEFF_NORMED,
                ).max()
            )
            self._last_asset_scores[
                asset_name
            ] = round(
                score,
                4,
            )

            if score > best_score:
                best_score = score
                best_name = asset_name

        self._last_asset_name = best_name

        if usable == 0:
            return (
                False,
                0.0,
                (
                    "boss asset search ROI is smaller "
                    "than every scaled template"
                ),
            )

        return (
            best_score
            >= BOSS_ALIVE_MATCH_THRESHOLD,
            best_score,
            None,
        )

    def _set_asset_debug(
        self,
        *,
        raw_width,
        raw_height,
        asset_score,
        asset_alive,
        asset_error,
        fallback_ocr=False,
    ):
        (
            _scan_x,
            _scan_y,
            native_scan_width,
            native_scan_height,
        ) = scale_roi(
            BOSS_ASSET_SCAN_ROI,
            raw_width,
            raw_height,
        )

        self.last_debug = {
            "raw_size": (
                raw_width,
                raw_height,
            ),
            "roi": BOSS_ASSET_SCAN_ROI,
            "search_roi": self._asset_search_roi(),
            "roi_shape": (
                BOSS_ASSET_SCAN_ROI[2],
                BOSS_ASSET_SCAN_ROI[3],
            ),
            "native_roi_shape": (
                max(
                    1,
                    native_scan_width,
                ),
                max(
                    1,
                    native_scan_height,
                ),
            ),
            "asset_name": self._last_asset_name,
            "asset_scores": dict(
                self._last_asset_scores
            ),
            "asset_shapes": dict(
                self._last_asset_shapes
            ),
            "chosen": "asset",
            "text": "",
            "asset_alive": asset_alive,
            "asset_score": round(
                float(asset_score),
                4,
            ),
            "asset_miss_streak": (
                self._asset_miss_streak
            ),
            "asset_error": asset_error,
            "fallback_ocr": fallback_ocr,
            "name_alive": False,
            "name_normalized": "",
            "name_expected": "",
            "name_ratio": 0.0,
            "name_coverage": 0.0,
            "ocr_raw": "",
            "ocr_candidates": [],
            "cache_hit": False,
            "alive": asset_alive,
            "attempts": [],
            "debug_dir": None,
        }

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
        # Public method name is kept for worker compatibility.
        with perf_timer(
            "vision_ms"
        ):
            return self._read_asset_first_impl(
                context
            )

    def _read_asset_first_impl(
        self,
        context,
    ) -> str:
        (
            asset_roi,
            raw_width,
            raw_height,
        ) = self._capture_asset_roi(
            context
        )

        selected_boss = getattr(
            context,
            "selected_boss",
            "",
        )

        (
            asset_alive,
            asset_score,
            asset_error,
        ) = self._match_asset(
            asset_roi,
            raw_width,
            raw_height,
            selected_boss,
        )

        if asset_alive:
            self._asset_miss_streak = 0
            self._set_asset_debug(
                raw_width=raw_width,
                raw_height=raw_height,
                asset_score=asset_score,
                asset_alive=True,
                asset_error=asset_error,
                fallback_ocr=False,
            )
            return ""

        self._asset_miss_streak += 1

        # Give the cheap template three consecutive scans before invoking the
        # heavier OCR safety path. The worker's existing >=3-second continuous
        # miss confirmation remains unchanged.
        if (
            asset_error is None
            and self._asset_miss_streak
            < BOSS_ASSET_MISSES_BEFORE_OCR
        ):
            self._set_asset_debug(
                raw_width=raw_width,
                raw_height=raw_height,
                asset_score=asset_score,
                asset_alive=False,
                asset_error=None,
                fallback_ocr=False,
            )
            return ""

        if not BOSS_OCR_ENABLED:
            self._set_asset_debug(
                raw_width=raw_width,
                raw_height=raw_height,
                asset_score=asset_score,
                asset_alive=False,
                asset_error=asset_error,
                fallback_ocr=False,
            )
            self.last_debug["chosen"] = "ocr_disabled"
            return ""

        text = self._read_name_impl(
            context
        )

        debug = dict(
            self.last_debug
        )
        name_alive = bool(
            debug.get(
                "name_alive",
                False,
            )
        )

        if name_alive:
            # OCR rescued a template miss; restart the three-scan asset window.
            self._asset_miss_streak = 0

        debug.update(
            {
                "asset_alive": False,
                "asset_score": round(
                    float(asset_score),
                    4,
                ),
                "asset_miss_streak": (
                    self._asset_miss_streak
                ),
                "asset_error": asset_error,
                "fallback_ocr": True,
                "alive": name_alive,
            }
        )
        self.last_debug = debug

        return text

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

        variants = (
            self._prepare_name_variants(
                roi
            )
        )
        composite = (
            self._stack_name_variants(
                variants
            )
        )

        selected_boss = getattr(
            context,
            "selected_boss",
            "",
        )

        cache_hit = (
            self._last_name_image
            is not None
            and self._last_name_text
            is not None
            and np.array_equal(
                composite,
                self._last_name_image,
            )
        )

        if cache_hit:
            raw_text = (
                self._last_name_raw
                or self._last_name_text
            )
            candidates = list(
                self._last_name_candidates
                or [
                    self._last_name_text
                ]
            )
            source = "name_cache"
        else:
            if self.ocr is None:
                self.ocr = OcrService()

            if hasattr(
                self.ocr,
                "read_text_block",
            ):
                raw_text = (
                    self.ocr
                    .read_text_block(
                        composite
                    )
                )
                source = "name_ocr_block"
            elif hasattr(
                self.ocr,
                "read_text",
            ):
                # Compatibility with injected OCR test doubles.
                raw_text = self.ocr.read_text(
                    variants[0]
                )
                source = "name_ocr"
            else:
                raw_text = self.ocr.read(
                    variants[0]
                )
                source = "name_ocr"

            candidates = (
                self._ocr_candidates(
                    raw_text
                )
            )

        (
            text,
            name_alive,
            name_ratio,
            name_coverage,
            normalized_text,
            expected_name,
        ) = self._choose_candidate(
            candidates,
            selected_boss,
        )

        if name_alive:
            # Cache only a confirmed positive. A bad/empty OCR result is
            # deliberately NOT cached, so the next scan gets a fresh read.
            self._last_name_image = (
                composite.copy()
            )
            self._last_name_text = text
            self._last_name_candidates = (
                list(candidates)
            )
            self._last_name_raw = (
                raw_text
            )
        elif not cache_hit:
            self._last_name_image = None
            self._last_name_text = None
            self._last_name_candidates = None
            self._last_name_raw = None

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
            "ocr_raw": self._clean_text(
                raw_text
            ),
            "ocr_candidates": list(
                candidates
            ),
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
            "alive": name_alive,
            "attempts": [
                (
                    source,
                    candidate,
                )
                for candidate
                in candidates
            ],
            "debug_dir": None,
        }

        return text
