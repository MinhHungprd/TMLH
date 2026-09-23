from dataclasses import dataclass

import cv2

from automation_constants import (
    BASE_HEIGHT,
    BASE_WIDTH,
    SIGNAL_1,
    SIGNAL_2,
    SIGNAL_3,
)
from perf_metrics import perf_timer
from vision import (
    ScaledAssetCache,
    base_point_to_client,
    capture_client,
    expand_roi,
    normalize_roi_to_base,
    roi_center,
    scale_roi,
)

CLICK_CENTER = "CLICK_CENTER"


@dataclass(frozen=True)
class SignalCheck:
    detected: bool
    action: str | None
    coordinates: tuple[int, int] | None = None
    signal_name: str | None = None


class GameStateDetector:
    SIGNALS = (
        ("s1", "asset__x795_y29_w29_h38.png", SIGNAL_1),
        ("s2", "asset__x742_y437_w45_h24.png", SIGNAL_2),
        ("s3", "asset__x392_y389_w75_h35.png", SIGNAL_3),
    )

    def __init__(self, matcher=None, assets_dir=None, capture=None):
        self.matcher = matcher
        self.capture = capture or capture_client
        self.assets = ScaledAssetCache(
            assets_dir
            or __import__("pathlib").Path(
                __file__
            ).resolve().parent
            / "assets"
        )

    def inspect(
        self,
        name,
        roi,
        image=None,
        asset_name=None,
    ) -> SignalCheck:
        detected = bool(
            self.matcher(name, roi)
            if self.matcher is not None
            else self._match(
                image,
                asset_name,
                roi,
            )
        )

        if not detected:
            return SignalCheck(
                False,
                None,
                signal_name=name,
            )

        if name == "s1":
            return SignalCheck(
                True,
                None,
                signal_name=name,
            )

        x, y, w, h = roi

        return SignalCheck(
            True,
            CLICK_CENTER,
            (
                x + w // 2,
                y + h // 2,
            ),
            signal_name=name,
        )

    def _match_region(
        self,
        region,
        asset_name,
        search_roi,
    ):
        """
        Match one small search region using the existing canonical template.

        The native frame is cropped first and only the small padded search
        area is resized back to canonical coordinates. This keeps the same
        template, padding and 0.7 threshold without resizing the full frame.
        """
        template = self.assets.get(
            asset_name,
            BASE_WIDTH,
            BASE_HEIGHT,
        )

        canonical_region = normalize_roi_to_base(
            region,
            search_roi,
        )

        if (
            canonical_region.shape[0]
            < template.shape[0]
            or canonical_region.shape[1]
            < template.shape[1]
        ):
            return False

        result = cv2.matchTemplate(
            canonical_region,
            template,
            cv2.TM_CCOEFF_NORMED,
        )

        score = float(
            result.max()
        )

        return score >= 0.7

    def _match(
        self,
        image,
        asset_name,
        roi,
    ):
        # Compatibility path for callers/tests already providing a canonical
        # 860x484 image.
        search_roi = expand_roi(
            roi,
            padding=6,
            image_width=BASE_WIDTH,
            image_height=BASE_HEIGHT,
        )

        x, y, w, h = search_roi

        region = image[
            y:y + h,
            x:x + w,
        ]

        return self._match_region(
            region,
            asset_name,
            search_roi,
        )

    def check_signal(
        self,
        context,
        signal_name: str,
    ) -> SignalCheck:
        """
        Check exactly one startup/login signal using canonical coordinates.

        This is used by automatic login so it does not waste work matching
        all startup assets on every polling iteration.
        """
        signal = next(
            (
                item
                for item in self.SIGNALS
                if item[0] == signal_name
            ),
            None,
        )

        if signal is None:
            raise ValueError(
                f"Unknown signal: {signal_name}"
            )

        name, asset, roi = signal

        if self.matcher is not None:
            detected = bool(
                self.matcher(
                    name,
                    roi,
                )
            )

            if not detected:
                return SignalCheck(
                    False,
                    None,
                )

            client_width = (
                context.window_width
            )
            client_height = (
                context.window_height
            )

            if name == "s1":
                return SignalCheck(
                    True,
                    None,
                )

            return SignalCheck(
                True,
                CLICK_CENTER,
                base_point_to_client(
                    roi_center(roi),
                    client_width,
                    client_height,
                ),
                signal_name=name,
            )

        raw = self.capture(
            context.window_handle
        )
        actual_height, actual_width = (
            raw.shape[:2]
        )

        search_roi = expand_roi(
            roi,
            padding=6,
            image_width=BASE_WIDTH,
            image_height=BASE_HEIGHT,
        )

        x, y, w, h = scale_roi(
            search_roi,
            actual_width,
            actual_height,
        )

        x = max(
            0,
            min(
                x,
                actual_width - 1,
            ),
        )
        y = max(
            0,
            min(
                y,
                actual_height - 1,
            ),
        )
        w = max(
            1,
            min(
                w,
                actual_width - x,
            ),
        )
        h = max(
            1,
            min(
                h,
                actual_height - y,
            ),
        )

        region = raw[
            y:y + h,
            x:x + w,
        ]

        detected = self._match_region(
            region,
            asset,
            search_roi,
        )

        if not detected:
            return SignalCheck(
                False,
                None,
                signal_name=name,
            )

        if name == "s1":
            return SignalCheck(
                True,
                None,
                signal_name=name,
            )

        return SignalCheck(
            True,
            CLICK_CENTER,
            base_point_to_client(
                roi_center(roi),
                actual_width,
                actual_height,
            ),
            signal_name=name,
        )

    def check_signals(self, context):
        with perf_timer(
            "asset_scan_ms"
        ):
            return self._check_signals_impl(
                context
            )

    def _check_signals_impl(
        self,
        context,
    ):
        if self.matcher is not None:
            client_width = (
                context.window_width
            )
            client_height = (
                context.window_height
            )

            results = []

            for name, _asset, roi in self.SIGNALS:
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
                            signal_name=name,
                        )
                    )
                    continue

                if name == "s1":
                    results.append(
                        SignalCheck(
                            True,
                            None,
                            signal_name=name,
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
                        signal_name=name,
                    )
                )

            return results

        # Capture once at the real client resolution.
        raw = self.capture(
            context.window_handle
        )

        actual_height, actual_width = (
            raw.shape[:2]
        )

        results = []

        for name, asset, roi in self.SIGNALS:
            # Same canonical search area as before: signal ROI + 6px padding.
            search_roi = expand_roi(
                roi,
                padding=6,
                image_width=BASE_WIDTH,
                image_height=BASE_HEIGHT,
            )

            # Crop only that search area from the native screenshot.
            x, y, w, h = scale_roi(
                search_roi,
                actual_width,
                actual_height,
            )

            x = max(
                0,
                min(
                    x,
                    actual_width - 1,
                ),
            )
            y = max(
                0,
                min(
                    y,
                    actual_height - 1,
                ),
            )
            w = max(
                1,
                min(
                    w,
                    actual_width - x,
                ),
            )
            h = max(
                1,
                min(
                    h,
                    actual_height - y,
                ),
            )

            region = raw[
                y:y + h,
                x:x + w,
            ]

            detected = self._match_region(
                region,
                asset,
                search_roi,
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

            base_center = roi_center(
                roi
            )

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
