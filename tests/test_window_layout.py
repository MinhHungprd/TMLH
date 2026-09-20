from window_layout import arrange_windows, stack_windows_for_boss


def test_arrange_windows_wraps_left_to_right():
    result = arrange_windows([(1, 100, 50), (2, 100, 50), (3, 100, 50)], (0, 0, 200, 100))
    assert [(item.x, item.y) for item in result] == [(0, 0), (100, 0), (0, 50)]


def test_arrange_windows_keeps_extra_rows_in_working_area():
    result = arrange_windows([(1, 100, 70), (2, 100, 70)], (0, 0, 100, 100))
    assert result[1].y + result[1].height <= 100



def test_stack_windows_for_boss_exposes_only_reveal_strip():
    result = stack_windows_for_boss(
        [
            (1, 300, 200, 40),
            (2, 300, 200, 40),
            (3, 300, 200, 40),
        ],
        (0, 0, 800, 600),
    )

    assert [
        (item.x, item.y)
        for item in result
    ] == [
        (0, 0),
        (0, 40),
        (0, 80),
    ]


def test_stack_windows_for_boss_wraps_to_next_column():
    result = stack_windows_for_boss(
        [
            (1, 300, 200, 60),
            (2, 300, 200, 60),
            (3, 300, 200, 60),
        ],
        (0, 0, 800, 250),
    )

    assert [
        (item.x, item.y)
        for item in result
    ] == [
        (0, 0),
        (0, 60),
        (300, 0),
    ]
