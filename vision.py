from dataclasses import dataclass
from pathlib import Path
import threading

import cv2
import numpy as np

from automation_constants import BASE_HEIGHT, BASE_WIDTH
from capture_broker import SCREEN_CAPTURE_BROKER
from perf_metrics import perf_timer

# PIL is only a fallback now. Keep fallback capture fully serialized so a
# failed MSS backend cannot create a burst of concurrent GDI captures.
_SCREEN_CAPTURE_SEMAPHORE = threading.BoundedSemaphore(1)


def scale_roi(base_roi, width: int, height: int):
    x, y, w, h = base_roi
    sx, sy = width / BASE_WIDTH, height / BASE_HEIGHT
    return round(x * sx), round(y * sy), round(w * sx), round(h * sy)


def roi_center(roi):
    x, y, w, h = roi
    return x + w // 2, y + h // 2


class ScaledAssetCache:
    def __init__(self, assets_dir):
        self.assets_dir = Path(assets_dir)
        self._cache = {}

    def get(self, asset_name: str, width: int, height: int):
        key = (asset_name, width, height)
        if key not in self._cache:
            source = cv2.imread(str(self.assets_dir / asset_name), cv2.IMREAD_GRAYSCALE)
            if source is None:
                raise FileNotFoundError(self.assets_dir / asset_name)
            target_width = max(1, round(source.shape[1] * width / BASE_WIDTH))
            target_height = max(1, round(source.shape[0] * height / BASE_HEIGHT))
            self._cache[key] = cv2.resize(source, (target_width, target_height), interpolation=cv2.INTER_AREA)
        return self._cache[key]


@dataclass(frozen=True)
class MatchResult:
    matched: bool
    score: float
    location: tuple[int, int]


def get_client_size(hwnd: int) -> tuple[int, int]:
    import win32gui

    rect = win32gui.GetClientRect(hwnd)
    return rect[2], rect[3]


def _capture_rect_pil(
    left: int,
    top: int,
    width: int,
    height: int,
) -> np.ndarray:
    """Serialized PIL fallback used only when MSS is unavailable."""
    from PIL import ImageGrab

    with _SCREEN_CAPTURE_SEMAPHORE:
        image = ImageGrab.grab(
            (
                left,
                top,
                left + width,
                top + height,
            )
        )

    return cv2.cvtColor(
        np.array(image),
        cv2.COLOR_RGB2GRAY,
    )


def capture_client(hwnd: int) -> np.ndarray:
    """
    Capture one client frame on demand through the shared MSS broker.

    Startup/respawn asset scans use the same single capture worker as boss
    scans, eliminating concurrent screen-grab bursts. PIL remains a safe,
    fully serialized fallback.
    """
    import win32gui

    with perf_timer("capture_ms"):
        left, top = win32gui.ClientToScreen(
            hwnd,
            (0, 0),
        )
        width, height = get_client_size(
            hwnd
        )

        try:
            return SCREEN_CAPTURE_BROKER.capture_rect(
                (
                    left,
                    top,
                    width,
                    height,
                )
            )
        except Exception:
            return _capture_rect_pil(
                left,
                top,
                width,
                height,
            )


def _capture_client_roi_pil(
    hwnd: int,
    base_roi,
) -> np.ndarray:
    """Original ROI path kept as a serialized fallback."""
    import win32gui

    client_width, client_height = get_client_size(
        hwnd
    )
    x, y, w, h = scale_roi(
        base_roi,
        client_width,
        client_height,
    )

    x = max(
        0,
        min(
            x,
            client_width - 1,
        ),
    )
    y = max(
        0,
        min(
            y,
            client_height - 1,
        ),
    )
    w = max(
        1,
        min(
            w,
            client_width - x,
        ),
    )
    h = max(
        1,
        min(
            h,
            client_height - y,
        ),
    )

    left, top = win32gui.ClientToScreen(
        hwnd,
        (x, y),
    )

    return _capture_rect_pil(
        left,
        top,
        w,
        h,
    )


def capture_client_roi(
    hwnd: int,
    base_roi,
) -> np.ndarray:
    """
    Capture one base-space ROI on demand through the shared MSS broker.

    Boss detection logic is unchanged. Only the screen-capture backend and
    scheduling are centralized to reduce resource spikes.
    """
    import win32gui

    with perf_timer("capture_ms"):
        client_width, client_height = get_client_size(
            hwnd
        )
        x, y, w, h = scale_roi(
            base_roi,
            client_width,
            client_height,
        )

        x = max(
            0,
            min(
                x,
                client_width - 1,
            ),
        )
        y = max(
            0,
            min(
                y,
                client_height - 1,
            ),
        )
        w = max(
            1,
            min(
                w,
                client_width - x,
            ),
        )
        h = max(
            1,
            min(
                h,
                client_height - y,
            ),
        )

        left, top = win32gui.ClientToScreen(
            hwnd,
            (x, y),
        )

        try:
            return SCREEN_CAPTURE_BROKER.capture_rect(
                (
                    left,
                    top,
                    w,
                    h,
                )
            )
        except Exception:
            return _capture_client_roi_pil(
                hwnd,
                base_roi,
            )


def normalize_roi_to_base(
    image: np.ndarray,
    base_roi,
) -> np.ndarray:
    """Resize one captured ROI back to its canonical base size."""
    _x, _y, target_width, target_height = base_roi
    height, width = image.shape[:2]

    if (
        width == target_width
        and height == target_height
    ):
        return image

    interpolation = (
        cv2.INTER_CUBIC
        if width < target_width
        or height < target_height
        else cv2.INTER_AREA
    )

    return cv2.resize(
        image,
        (target_width, target_height),
        interpolation=interpolation,
    )


def normalize_to_base(image: np.ndarray) -> np.ndarray:
    """
    Đưa mọi screenshot về canonical 860x484.

    Vision từ đây trở đi luôn chạy trên hệ tọa độ gốc.
    """
    height, width = image.shape[:2]

    if width == BASE_WIDTH and height == BASE_HEIGHT:
        return image

    if width < BASE_WIDTH or height < BASE_HEIGHT:
        interpolation = cv2.INTER_CUBIC
    else:
        interpolation = cv2.INTER_AREA

    return cv2.resize(
        image,
        (BASE_WIDTH, BASE_HEIGHT),
        interpolation=interpolation,
    )


def base_point_to_client(
    point: tuple[int, int],
    client_width: int,
    client_height: int,
) -> tuple[int, int]:
    x, y = point

    return (
        round(x * client_width / BASE_WIDTH),
        round(y * client_height / BASE_HEIGHT),
    )


def expand_roi(
    roi,
    padding: int = 6,
    image_width: int = BASE_WIDTH,
    image_height: int = BASE_HEIGHT,
):
    x, y, w, h = roi

    left = max(0, x - padding)
    top = max(0, y - padding)

    right = min(
        image_width,
        x + w + padding,
    )

    bottom = min(
        image_height,
        y + h + padding,
    )

    return (
        left,
        top,
        right - left,
        bottom - top,
    )