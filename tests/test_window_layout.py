from window_layout import arrange_windows


def test_arrange_windows_wraps_left_to_right():
    result = arrange_windows([(1, 100, 50), (2, 100, 50), (3, 100, 50)], (0, 0, 200, 100))
    assert [(item.x, item.y) for item in result] == [(0, 0), (100, 0), (0, 50)]


def test_arrange_windows_keeps_extra_rows_in_working_area():
    result = arrange_windows([(1, 100, 70), (2, 100, 70)], (0, 0, 100, 100))
    assert result[1].y + result[1].height <= 100
