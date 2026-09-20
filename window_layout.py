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



def stack_windows_for_boss(items, working_area):
    """
    Stack windows vertically while leaving only the top strip required for
    boss HP scanning visible. When one column no longer fits, continue in
    the next column.

    items:
        iterable of (hwnd, width, height, reveal_height)
    """
    left, top, area_width, area_height = working_area
    right = left + area_width
    bottom = top + area_height

    x = left
    y = top
    column_width = 0
    first_in_column = True
    placements = []

    for hwnd, width, height, reveal_height in items:
        reveal_height = max(
            1,
            min(
                int(reveal_height),
                int(height),
            ),
        )

        # The current window itself must still fit vertically because its
        # lower area is covered by the next stacked window, not clipped.
        if (
            not first_in_column
            and y + height > bottom
        ):
            x += column_width
            y = top
            column_width = 0
            first_in_column = True

        overlap = (
            x + width > right
            or y + height > bottom
            or width > area_width
            or height > area_height
        )

        placements.append(
            WindowPlacement(
                hwnd,
                x,
                y,
                width,
                height,
                overlap,
            )
        )

        y += reveal_height
        column_width = max(
            column_width,
            width,
        )
        first_in_column = False

    return placements
