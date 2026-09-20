from dataclasses import dataclass


@dataclass(frozen=True)
class WindowPlacement:
    hwnd: int
    x: int
    y: int
    width: int
    height: int
    overlap: bool = False


def arrange_windows(items, working_area):
    left, top, area_width, area_height = working_area
    x, y, row_height = left, top, 0
    placements = []
    for hwnd, width, height in items:
        overlap = False
        if x != left and x + width > left + area_width:
            x, y, row_height = left, y + row_height, 0
        if y + height > top + area_height:
            y = top
            overlap = True
        if width > area_width or height > area_height:
            overlap = True
        placements.append(WindowPlacement(hwnd, x, y, width, height, overlap))
        x += width
        row_height = max(row_height, height)
    return placements
