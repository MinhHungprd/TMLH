import re
import time
from pathlib import Path

import cv2
import numpy as np

from automation_constants import (
    BASE_HEIGHT,
    BASE_WIDTH,
    BOSS_ALIVE_ASSET,
    BOSS_ALIVE_FALLBACK_MISSES,
    BOSS_ALIVE_MARKER,
    BOSS_ALIVE_MATCH_PADDING,
    BOSS_ALIVE_MATCH_THRESHOLD,
    BOSS_OCR_FALLBACK_ENABLED,
    BOSS_HP,
    BOSS_SCAN_ROI,
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


def _expand_roi(
    roi,
    padding: int,
):
    x, y, w, h = roi
    left = max(
        0,
        x - padding,
    )
    top = max(
        0,
        y - padding,
    )
    right = min(
        BASE_WIDTH,
        x + w + padding,
    )
    bottom = min(
        BASE_HEIGHT,
        y + h + padding,
    )
    return (
        left,
        top,
        right - left,
        bottom - top,
    )


BOSS_ALIVE_SEARCH_ROI = _expand_roi(
    BOSS_ALIVE_MARKER,
    BOSS_ALIVE_MATCH_PADDING,
)


def _crop_global_roi(
    image,
    outer_roi,
    inner_roi,
):
    outer_x, outer_y, _outer_w, _outer_h = outer_roi
    inner_x, inner_y, inner_w, inner_h = inner_roi

    x = inner_x - outer_x
    y = inner_y - outer_y

    return image[
        y:y + inner_h,
        x:x + inner_w,
    ]


class BossDetector:
    """
    Boss presence detector.

    Primary production path:
    - capture one combined boss ROI
    - template-match the lightweight alive marker
    - keep the existing visual HP detector
    - optionally use OCR as a safety fallback after 3 consecutive marker misses

    The worker's 1-second scan cadence and 3-second death confirmation are
    unchanged.
    """

    def __init__(
        self,
        ocr=None,
        capture=None,
        roi_capture=None,
        *,
        assets_dir=None,
        marker_template=None,
        ocr_fallback_enabled=None,
        debug_interval=10.0,
        now=None,
        full_debug=False,
    ):
        self.dead_streak = 0
        self.ocr = ocr
        self.capture = capture or capture_client

        # Default production path can capture the combined BOSS_SCAN_ROI once.
        # Explicit roi_capture injection keeps the previous BOSS_HP-only
        # contract for tests/custom callers.
        self._combined_roi_capture = (
            capture is None
            and roi_capture is None
        )

        if roi_capture is not None:
            self.roi_capture = roi_capture
        elif capture is None:
            self.roi_capture = capture_client_roi
        else:
            self.roi_capture = None

        self.assets_dir = (
            Path(assets_dir)
            if assets_dir is not None
            else (
                Path(__file__)
                .resolve()
                .parent
                / "assets"
            )
        )
        self._marker_template = marker_template
        self._marker_template_loaded = (
            marker_template is not None
        )
        self._marker_template_error = None
        self._asset_miss_streak = 0
        self.ocr_fallback_enabled = (
            BOSS_OCR_FALLBACK_ENABLED
            if ocr_fallback_enabled is None
            else bool(ocr_fallback_enabled)
        )

        self.debug_interval = float(
            debug_interval
        )
        self.now = now or time.monotonic
        self.full_debug = bool(
            full_debug
        )
        self._last_debug_at = None
        self.last_debug = {}

        # Exact-frame OCR cache. If the canonical HP ROI is byte-identical
        # to the previous OCR input, repeating Tesseract cannot add value.
        self._last_ocr_roi = None
        self._last_ocr_text = None

    @staticmethod
    def count_visual_glyphs(roi) -> int:
        _, mask = cv2.threshold(
            roi,
            170,
            255,
            cv2.THRESH_BINARY,
        )

        count, _labels, stats, _centroids = (
            cv2.connectedComponentsWithStats(
                mask,
                connectivity=8,
            )
        )

        glyphs = 0

        for i in range(1, count):
            _x, _y, w, h, area = stats[i]

            if (
                area >= 6
                and h >= 5
                and h <= 18
                and w <= 16
            ):
                glyphs += 1

        return glyphs

    @staticmethod
    def is_alive(text: str) -> bool:
        digits = re.sub(
            r"\D",
            "",
            text or "",
        )

        # A single OCR digit is too easy to be noise.
        return len(digits) >= 3

    def update_dead_streak(
        self,
        text: str,
    ) -> bool:
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
    def _digit_count(text: str) -> int:
        return sum(
            char.isdigit()
            for char in (text or "")
        )

    def _load_marker_template(self):
        if self._marker_template_loaded:
            return self._marker_template

        self._marker_template_loaded = True

        path = (
            self.assets_dir
            / BOSS_ALIVE_ASSET
        )

        template = cv2.imread(
            str(path),
            cv2.IMREAD_GRAYSCALE,
        )

        if template is None:
            self._marker_template_error = (
                f"Boss marker asset not found: {path}"
            )
            return None

        expected_w = BOSS_ALIVE_MARKER[2]
        expected_h = BOSS_ALIVE_MARKER[3]

        if template.shape != (
            expected_h,
            expected_w,
        ):
            self._marker_template_error = (
                "Boss marker asset has unexpected "
                f"shape {template.shape!r}; "
                f"expected {(expected_h, expected_w)!r}"
            )
            return None

        self._marker_template = template
        return template

    def _match_alive_marker(
        self,
        search_region,
    ):
        if (
            search_region is None
            or search_region.size == 0
        ):
            return (
                False,
                False,
                0.0,
                "marker search region unavailable",
            )

        template = (
            self._load_marker_template()
        )

        if template is None:
            return (
                False,
                False,
                0.0,
                self._marker_template_error,
            )

        if (
            search_region.shape[0]
            < template.shape[0]
            or search_region.shape[1]
            < template.shape[1]
        ):
            return (
                True,
                False,
                0.0,
                "marker search region too small",
            )

        result = cv2.matchTemplate(
            search_region,
            template,
            cv2.TM_CCOEFF_NORMED,
        )

        score = float(
            result.max()
        )

        return (
            True,
            score
            >= BOSS_ALIVE_MATCH_THRESHOLD,
            score,
            None,
        )

    def _debug_due(self) -> bool:
        now = self.now()

        if self._last_debug_at is None:
            self._last_debug_at = now
            return True

        if (
            now
            - self._last_debug_at
            >= self.debug_interval
        ):
            self._last_debug_at = now
            return True

        return False

    def _save_debug(
        self,
        context,
        normalized,
        roi,
        upscaled,
        binary,
        inverted,
    ):
        profile_id = getattr(
            context,
            "profile_id",
            "unknown",
        )

        debug_dir = (
            Path(__file__)
            .resolve()
            .parent
            / "debug"
            / "ocr"
            / str(profile_id)
        )

        with perf_timer(
            "debug_io_ms"
        ):
            debug_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            cv2.imwrite(
                str(
                    debug_dir
                    / "last_roi.png"
                ),
                roi,
            )

            if self.full_debug:
                if normalized is None:
                    raw = self.capture(
                        context.window_handle
                    )
                    normalized = (
                        normalize_to_base(
                            raw
                        )
                    )

                frame = normalized.copy()

                x, y, w, h = BOSS_HP

                cv2.rectangle(
                    frame,
                    (x, y),
                    (x + w, y + h),
                    255,
                    1,
                )

                cv2.imwrite(
                    str(
                        debug_dir
                        / "last_frame.png"
                    ),
                    frame,
                )
                cv2.imwrite(
                    str(
                        debug_dir
                        / "last_upscaled.png"
                    ),
                    upscaled,
                )
                cv2.imwrite(
                    str(
                        debug_dir
                        / "last_binary.png"
                    ),
                    binary,
                )
                cv2.imwrite(
                    str(
                        debug_dir
                        / "last_inverted.png"
                    ),
                    inverted,
                )

        return debug_dir

    def _capture_regions(
        self,
        context,
    ):
        """
        Return:
            HP ROI (66x22 canonical),
            alive-marker search ROI (canonical or None),
            normalized full frame for optional debug,
            raw client width,
            raw client height.
        """
        if self.roi_capture is not None:
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

            if self._combined_roi_capture:
                raw_scan = self.roi_capture(
                    context.window_handle,
                    BOSS_SCAN_ROI,
                )
                scan = normalize_roi_to_base(
                    raw_scan,
                    BOSS_SCAN_ROI,
                )

                roi = _crop_global_roi(
                    scan,
                    BOSS_SCAN_ROI,
                    BOSS_HP,
                )
                marker_search = (
                    _crop_global_roi(
                        scan,
                        BOSS_SCAN_ROI,
                        BOSS_ALIVE_SEARCH_ROI,
                    )
                )

                return (
                    roi,
                    marker_search,
                    None,
                    raw_width,
                    raw_height,
                )

            # Legacy injected ROI capture: preserve the previous BOSS_HP-only
            # contract and fall back to visual/OCR without marker matching.
            raw_roi = self.roi_capture(
                context.window_handle,
                BOSS_HP,
            )
            roi = normalize_roi_to_base(
                raw_roi,
                BOSS_HP,
            )

            return (
                roi,
                None,
                None,
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

        x, y, w, h = BOSS_HP
        roi = normalized[
            y:y + h,
            x:x + w,
        ]

        sx, sy, sw, sh = (
            BOSS_ALIVE_SEARCH_ROI
        )
        marker_search = normalized[
            sy:sy + sh,
            sx:sx + sw,
        ]

        return (
            roi,
            marker_search,
            normalized,
            raw_width,
            raw_height,
        )

    def _set_debug(
        self,
        *,
        raw_width,
        raw_height,
        roi,
        chosen_name,
        chosen_text,
        alive,
        attempts,
        visual_glyphs,
        visual_alive,
        debug_dir,
        asset_available=False,
        asset_alive=False,
        asset_score=0.0,
        asset_error=None,
    ):
        self.last_debug = {
            "raw_size": (
                raw_width,
                raw_height,
            ),
            "roi": BOSS_HP,
            "roi_shape": (
                roi.shape[1],
                roi.shape[0],
            ),
            "roi_min": int(
                roi.min()
            ),
            "roi_max": int(
                roi.max()
            ),
            "roi_mean": round(
                float(
                    roi.mean()
                ),
                2,
            ),
            "chosen": chosen_name,
            "text": chosen_text,
            "alive": alive,
            "attempts": attempts,
            "visual_glyphs": (
                visual_glyphs
            ),
            "visual_alive": (
                visual_alive
            ),
            "asset_available": (
                asset_available
            ),
            "asset_alive": (
                asset_alive
            ),
            "asset_score": round(
                float(asset_score),
                4,
            ),
            "asset_miss_streak": (
                self._asset_miss_streak
            ),
            "asset_error": (
                asset_error
            ),
            "debug_dir": (
                str(debug_dir)
                if debug_dir
                else None
            ),
        }

    def read_hp(
        self,
        context,
    ) -> str:
        with perf_timer(
            "vision_ms"
        ):
            return self._read_hp_impl(
                context
            )

    def _read_hp_impl(
        self,
        context,
    ) -> str:
        (
            roi,
            marker_search,
            normalized,
            raw_width,
            raw_height,
        ) = self._capture_regions(
            context
        )

        if roi.size == 0:
            raise RuntimeError(
                "Boss HP ROI is outside "
                "game client area"
            )

        (
            asset_available,
            asset_alive,
            asset_score,
            asset_error,
        ) = self._match_alive_marker(
            marker_search
        )

        # Primary fast path: the requested marker exists => boss is alive.
        if asset_available:
            if asset_alive:
                self._asset_miss_streak = 0
                self._last_ocr_roi = None
                self._last_ocr_text = None

                self._set_debug(
                    raw_width=raw_width,
                    raw_height=raw_height,
                    roi=roi,
                    chosen_name="asset",
                    chosen_text="",
                    alive=True,
                    attempts=[],
                    visual_glyphs=0,
                    # Worker already consumes visual_alive as the fast
                    # non-OCR alive signal, so expose the marker there too
                    # without changing worker decision logic.
                    visual_alive=True,
                    debug_dir=None,
                    asset_available=True,
                    asset_alive=True,
                    asset_score=asset_score,
                )
                return ""

            self._asset_miss_streak += 1
        else:
            # Missing/corrupt asset or legacy injected capture: immediately
            # retain the old visual + OCR behavior as a safety fallback.
            self._asset_miss_streak = 0

        visual_glyphs = (
            self.count_visual_glyphs(
                roi
            )
        )
        visual_alive = (
            visual_glyphs >= 3
        )

        # Keep the existing cheap visual detector as another fast safety net.
        if visual_alive:
            self._asset_miss_streak = 0
            self._last_ocr_roi = None
            self._last_ocr_text = None

            self._set_debug(
                raw_width=raw_width,
                raw_height=raw_height,
                roi=roi,
                chosen_name="visual",
                chosen_text="",
                alive=True,
                attempts=[],
                visual_glyphs=visual_glyphs,
                visual_alive=True,
                debug_dir=None,
                asset_available=asset_available,
                asset_alive=False,
                asset_score=asset_score,
                asset_error=asset_error,
            )
            return ""

        # The new marker is deliberately allowed to miss for 3 consecutive
        # 1-second scans before Tesseract is used. This removes OCR process
        # churn during isolated bad frames while still keeping OCR available
        # before the existing 3-second death decision can complete.
        if (
            asset_available
            and self._asset_miss_streak
            < BOSS_ALIVE_FALLBACK_MISSES
        ):
            self._set_debug(
                raw_width=raw_width,
                raw_height=raw_height,
                roi=roi,
                chosen_name="asset_miss",
                chosen_text="",
                alive=False,
                attempts=[],
                visual_glyphs=visual_glyphs,
                visual_alive=False,
                debug_dir=None,
                asset_available=True,
                asset_alive=False,
                asset_score=asset_score,
            )
            return ""

        if not self.ocr_fallback_enabled:
            # Marker validation mode: keep all OCR code available but do not
            # invoke Tesseract. This makes marker misses visible in behavior
            # instead of being rescued by OCR.
            self._set_debug(
                raw_width=raw_width,
                raw_height=raw_height,
                roi=roi,
                chosen_name="ocr_disabled",
                chosen_text="",
                alive=False,
                attempts=[],
                visual_glyphs=visual_glyphs,
                visual_alive=False,
                debug_dir=None,
                asset_available=asset_available,
                asset_alive=False,
                asset_score=asset_score,
                asset_error=asset_error,
            )
            return ""

        # If the same canonical HP ROI was already OCR'd, reuse its result.
        if (
            self._last_ocr_roi
            is not None
            and self._last_ocr_text
            is not None
            and np.array_equal(
                roi,
                self._last_ocr_roi,
            )
        ):
            cached_text = (
                self._last_ocr_text
            )
            cached_alive = (
                self.is_alive(
                    cached_text
                )
            )

            if cached_alive:
                self._asset_miss_streak = 0

            self._set_debug(
                raw_width=raw_width,
                raw_height=raw_height,
                roi=roi,
                chosen_name="cache",
                chosen_text=cached_text,
                alive=cached_alive,
                attempts=[
                    (
                        "cache",
                        cached_text,
                    )
                ],
                visual_glyphs=visual_glyphs,
                visual_alive=False,
                debug_dir=None,
                asset_available=asset_available,
                asset_alive=False,
                asset_score=asset_score,
                asset_error=asset_error,
            )

            return cached_text

        x, y, w, h = BOSS_HP

        # Existing OCR fallback is intentionally kept unchanged:
        # 66x22 -> 264x88, then gray/binary/inverted attempts.
        upscaled = cv2.resize(
            roi,
            (
                w * 4,
                h * 4,
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

        inverted = cv2.bitwise_not(
            binary
        )

        if self.ocr is None:
            self.ocr = OcrService()

        attempts = []

        for name, image in (
            ("gray", upscaled),
            ("binary", binary),
            ("inverted", inverted),
        ):
            text = self._clean_text(
                self.ocr.read(
                    image
                )
            )

            attempts.append(
                (
                    name,
                    text,
                )
            )

            if self.is_alive(text):
                break

        (
            chosen_name,
            chosen_text,
        ) = max(
            attempts,
            key=lambda item: (
                self._digit_count(
                    item[1]
                ),
                len(item[1]),
            ),
        )

        alive = self.is_alive(
            chosen_text
        )

        if alive:
            # OCR successfully rescued a marker/visual miss. Start a fresh
            # 3-scan marker window rather than OCR'ing again next second.
            self._asset_miss_streak = 0

        self._last_ocr_roi = roi.copy()
        self._last_ocr_text = (
            chosen_text
        )

        debug_dir = None

        if (
            not alive
            and not visual_alive
            and self._debug_due()
        ):
            debug_dir = self._save_debug(
                context,
                normalized,
                roi,
                upscaled,
                binary,
                inverted,
            )

        self._set_debug(
            raw_width=raw_width,
            raw_height=raw_height,
            roi=roi,
            chosen_name=chosen_name,
            chosen_text=chosen_text,
            alive=alive,
            attempts=attempts,
            visual_glyphs=visual_glyphs,
            visual_alive=visual_alive,
            debug_dir=debug_dir,
            asset_available=asset_available,
            asset_alive=False,
            asset_score=asset_score,
            asset_error=asset_error,
        )

        return chosen_text
