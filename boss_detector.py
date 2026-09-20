import re
import time
from pathlib import Path

import cv2

from automation_constants import BOSS_HP
from ocr_service import OcrService
from vision import (
    capture_client,
    capture_client_roi,
    get_client_size,
    normalize_roi_to_base,
    normalize_to_base,
)


class BossDetector:
    """
    Boss presence detector.

    Production path captures only the HP ROI. Tests/custom captures still
    use the legacy full-frame path so existing injection contracts remain
    compatible.
    """

    def __init__(
        self,
        ocr=None,
        capture=None,
        roi_capture=None,
        *,
        debug_interval=10.0,
        now=None,
    ):
        self.dead_streak = 0
        self.ocr = ocr
        self.capture = capture or capture_client

        # Preserve the old injected capture contract. ROI capture is enabled
        # automatically only for the normal production capture path.
        if roi_capture is not None:
            self.roi_capture = roi_capture
        elif capture is None:
            self.roi_capture = capture_client_roi
        else:
            self.roi_capture = None

        self.debug_interval = float(debug_interval)
        self.now = now or time.monotonic
        self._last_debug_at = None
        self.last_debug = {}

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

        # Một ký tự số đơn lẻ rất dễ là OCR noise.
        # HP boss thực tế phải chứa nhiều chữ số.
        return len(digits) >= 3

    def update_dead_streak(self, text: str) -> bool:
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
        """
        Overwrite only the latest diagnostic images.

        The normal boss scan is ROI-only. A full-frame screenshot is captured
        here only when a throttled failure diagnostic is actually written.
        """
        profile_id = getattr(
            context,
            "profile_id",
            "unknown",
        )

        debug_dir = (
            Path(__file__).resolve().parent
            / "debug"
            / "ocr"
            / str(profile_id)
        )

        debug_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        if normalized is None:
            raw = self.capture(
                context.window_handle
            )
            normalized = normalize_to_base(
                raw
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
            str(debug_dir / "last_frame.png"),
            frame,
        )
        cv2.imwrite(
            str(debug_dir / "last_roi.png"),
            roi,
        )
        cv2.imwrite(
            str(debug_dir / "last_upscaled.png"),
            upscaled,
        )
        cv2.imwrite(
            str(debug_dir / "last_binary.png"),
            binary,
        )
        cv2.imwrite(
            str(debug_dir / "last_inverted.png"),
            inverted,
        )

        return debug_dir

    def _capture_roi(self, context):
        """
        Return canonical 66x22 HP ROI plus raw client size.

        Custom/injected full-frame captures intentionally keep the previous
        behavior for tests and compatibility.
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
                raw_width, raw_height = get_client_size(
                    context.window_handle
                )

            raw_roi = self.roi_capture(
                context.window_handle,
                BOSS_HP,
            )
            roi = normalize_roi_to_base(
                raw_roi,
                BOSS_HP,
            )
            return roi, None, raw_width, raw_height

        raw = self.capture(
            context.window_handle
        )
        raw_height, raw_width = raw.shape[:2]
        normalized = normalize_to_base(raw)

        x, y, w, h = BOSS_HP
        roi = normalized[
            y:y + h,
            x:x + w,
        ]

        return (
            roi,
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
            "roi_min": int(roi.min()),
            "roi_max": int(roi.max()),
            "roi_mean": round(
                float(roi.mean()),
                2,
            ),
            "chosen": chosen_name,
            "text": chosen_text,
            "alive": alive,
            "attempts": attempts,
            "visual_glyphs": visual_glyphs,
            "visual_alive": visual_alive,
            "debug_dir": (
                str(debug_dir)
                if debug_dir
                else None
            ),
        }

    def read_hp(self, context) -> str:
        roi, normalized, raw_width, raw_height = (
            self._capture_roi(context)
        )

        if roi.size == 0:
            raise RuntimeError(
                "Boss HP ROI is outside "
                "game client area"
            )

        visual_glyphs = self.count_visual_glyphs(
            roi
        )
        visual_alive = visual_glyphs >= 3

        # The worker already treats visual_alive OR ocr_alive as alive.
        # Therefore OCR adds no decision value when the visual detector
        # has positively found the HP digits. Skip expensive Tesseract.
        if visual_alive:
            self._set_debug(
                raw_width=raw_width,
                raw_height=raw_height,
                roi=roi,
                chosen_name="visual",
                chosen_text="",
                alive=False,
                attempts=[],
                visual_glyphs=visual_glyphs,
                visual_alive=True,
                debug_dir=None,
            )
            return ""

        x, y, w, h = BOSS_HP

        # 66x22 -> 264x88.
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

        # Keep the existing fallback order and OCR semantics unchanged.
        for name, image in (
            ("gray", upscaled),
            ("binary", binary),
            ("inverted", inverted),
        ):
            text = self._clean_text(
                self.ocr.read(image)
            )

            attempts.append(
                (name, text)
            )

            if self.is_alive(text):
                break

        chosen_name, chosen_text = max(
            attempts,
            key=lambda item: (
                self._digit_count(item[1]),
                len(item[1]),
            ),
        )

        alive = self.is_alive(
            chosen_text
        )

        debug_dir = None

        # Disk diagnostics do not affect automation logic. Limit writes
        # to avoid I/O spikes when several profiles lose the boss together.
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
        )

        return chosen_text
