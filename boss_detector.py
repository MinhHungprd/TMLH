import re

import cv2

from automation_constants import BOSS_HP
from ocr_service import OcrService
from vision import capture_client, scale_roi


class BossDetector:
    def __init__(self, ocr=None, capture=None):
        self.dead_streak = 0
        self.ocr = ocr
        self.capture = capture or capture_client

    @staticmethod
    def is_alive(text: str) -> bool:
        return bool(re.search(r"[0-9]", text or ""))

    def update_dead_streak(self, text: str) -> bool:
        if self.is_alive(text):
            self.dead_streak = 0
            return False
        self.dead_streak += 1
        return self.dead_streak >= 2

    def read_hp(self, context) -> str:
        image = self.capture(context.window_handle)
        height, width = image.shape[:2]
        x, y, w, h = scale_roi(BOSS_HP, width, height)
        roi = image[y:y + h, x:x + w]
        if roi.size == 0:
            raise RuntimeError("Boss HP ROI is outside game client area")
        roi = cv2.resize(roi, (max(w, BOSS_HP[2]), max(h, BOSS_HP[3])), interpolation=cv2.INTER_CUBIC)
        if self.ocr is None:
            self.ocr = OcrService()
        return self.ocr.read(roi)
