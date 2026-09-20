from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from automation_constants import BASE_HEIGHT, BASE_WIDTH


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


def capture_client(hwnd: int) -> np.ndarray:
    from PIL import ImageGrab
    import win32gui

    left, top = win32gui.ClientToScreen(hwnd, (0, 0))
    rect = win32gui.GetClientRect(hwnd)
    image = ImageGrab.grab((left, top, left + rect[2], top + rect[3]))
    return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2GRAY)
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