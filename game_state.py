from dataclasses import dataclass

import cv2

from automation_constants import (
    BASE_HEIGHT,
    BASE_WIDTH,
    SIGNAL_1,
    SIGNAL_2,
    SIGNAL_3,
)

from vision import (
    ScaledAssetCache,
    base_point_to_client,
    capture_client,
    expand_roi,
    normalize_to_base,
    roi_center,
)

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

    def _match(
        self,
        image,
        asset_name,
        roi,
    ):
        # image ở đây luôn là canonical 860x484.
        template = self.assets.get(
            asset_name,
            BASE_WIDTH,
            BASE_HEIGHT,
        )

        # Không match đúng khít ROI.
        # Cho phép Unity lệch vài pixel sau scale/render.
        search_roi = expand_roi(
            roi,
            padding=6,
            image_width=BASE_WIDTH,
            image_height=BASE_HEIGHT,
        )

        x, y, w, h = search_roi

        region = image[
            y:y + h,
            x:x + w
        ]

        if (
            region.shape[0] < template.shape[0]
            or region.shape[1] < template.shape[1]
        ):
            return False

        result = cv2.matchTemplate(
            region,
            template,
            cv2.TM_CCOEFF_NORMED,
        )

        score = float(result.max())

        return score >= 0.82

    def check_signals(self, context):
        if self.matcher is not None:
            client_width = context.window_width
            client_height = context.window_height

            results = []

            for name, asset, roi in self.SIGNALS:
                detected = bool(
                    self.matcher(
                        name,
                        roi,
                    )
                )

                if not detected:
                    results.append(
                        SignalCheck(
                            False,
                            None,
                        )
                    )
                    continue

                if name == "s1":
                    results.append(
                        SignalCheck(
                            True,
                            None,
                        )
                    )
                    continue

                click = base_point_to_client(
                    roi_center(roi),
                    client_width,
                    client_height,
                )

                results.append(
                    SignalCheck(
                        True,
                        CLICK_CENTER,
                        click,
                    )
                )

            return results

        # Screenshot ở resolution THỰC TẾ.
        raw = self.capture(
            context.window_handle
        )

        actual_height, actual_width = (
            raw.shape[:2]
        )

        # Vision luôn chạy tại 860x484.
        image = normalize_to_base(raw)

        results = []

        for name, asset, roi in self.SIGNALS:
            detected = self._match(
                image,
                asset,
                roi,
            )

            if not detected:
                results.append(
                    SignalCheck(
                        False,
                        None,
                    )
                )
                continue

            if name == "s1":
                results.append(
                    SignalCheck(
                        True,
                        None,
                    )
                )
                continue

            # ROI đang là hệ 860x484.
            base_center = roi_center(roi)

            # Click phải chuyển trở lại client hiện tại.
            click = base_point_to_client(
                base_center,
                actual_width,
                actual_height,
            )

            results.append(
                SignalCheck(
                    True,
                    CLICK_CENTER,
                    click,
                )
            )

        return results