import re
from pathlib import Path

import cv2

from automation_constants import BOSS_HP
from ocr_service import OcrService
from vision import (
    capture_client,
    normalize_to_base,
)


class BossDetector:
    def __init__(self, ocr=None, capture=None):
        self.dead_streak = 0
        self.ocr = ocr
        self.capture = capture or capture_client
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
            x, y, w, h, area = stats[i]

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
        Chỉ ghi đè ảnh debug cuối cùng.
        Không tạo hàng nghìn screenshot.
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

        # Full frame + khung ROI.
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

    def read_hp(self, context) -> str:
        raw = self.capture(
            context.window_handle
        )

        raw_height, raw_width = raw.shape[:2]

        # Vision canonical 860x484.
        normalized = normalize_to_base(raw)

        x, y, w, h = BOSS_HP
        roi = normalized[
            y:y + h,
            x:x + w
        ]

        if roi.size == 0:
            raise RuntimeError(
                "Boss HP ROI is outside "
                "game client area"
            )

        visual_glyphs = self.count_visual_glyphs(
            roi
        )

        visual_alive = visual_glyphs >= 3

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

        # Ưu tiên grayscale.
        # Nếu không thấy số mới thử threshold.
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

            # Có số rồi thì không cần gọi
            # Tesseract thêm lần nữa.
            if self.is_alive(text):
                break

        # Nếu không candidate nào có số,
        # chọn output có nhiều digit nhất,
        # rồi mới xét độ dài.
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

        # OCR fail -> lưu ảnh để kiểm tra.
        if not alive and not visual_alive:
            debug_dir = self._save_debug(
                context,
                normalized,
                roi,
                upscaled,
                binary,
                inverted,
            )

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

        return chosen_text