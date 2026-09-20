from dataclasses import dataclass

import cv2

from automation_constants import SIGNAL_1, SIGNAL_2, SIGNAL_3
from vision import ScaledAssetCache, capture_client, scale_roi


CLICK_CENTER = "CLICK_CENTER"


@dataclass(frozen=True)
class SignalCheck:
    detected: bool
    action: str | None
    coordinates: tuple[int, int] | None = None


class GameStateDetector:
    SIGNALS = (
        ("s1", "asset__x795_y29_w29_h38.png", SIGNAL_1),
        ("s2", "asset__x742_y437_w45_h24.png", SIGNAL_2),
        ("s3", "asset__x392_y389_w75_h35.png", SIGNAL_3),
    )

    def __init__(self, matcher=None, assets_dir=None, capture=None):
        self.matcher = matcher
        self.capture = capture or capture_client
        self.assets = ScaledAssetCache(assets_dir or __import__("pathlib").Path(__file__).resolve().parent / "assets")

    def inspect(self, name, roi, image=None, asset_name=None) -> SignalCheck:
        detected = bool(self.matcher(name, roi) if self.matcher is not None else
                        self._match(image, asset_name, roi))
        if not detected:
            return SignalCheck(False, None)
        if name == "s1":
            return SignalCheck(True, None)
        x, y, w, h = roi
        return SignalCheck(True, CLICK_CENTER, (x + w // 2, y + h // 2))

    def _match(self, image, asset_name, roi):
        height, width = image.shape[:2]
        x, y, w, h = roi
        template = self.assets.get(asset_name, width, height)
        region = image[y:y + h, x:x + w]
        if region.shape[0] < template.shape[0] or region.shape[1] < template.shape[1]:
            return False
        score = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED).max()
        return score >= 0.90

    def check_signals(self, context):
        image = (
            None
            if self.matcher is not None
            else self.capture(context.window_handle)
        )

        width, height = (
            (context.window_width, context.window_height)
            if image is None
            else (image.shape[1], image.shape[0])
        )

        return [
            self.inspect(
                name,
                scale_roi(roi, width, height),
                image,
                asset,
            )
            for name, asset, roi in self.SIGNALS
        ]
