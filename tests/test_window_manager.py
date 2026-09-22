from types import SimpleNamespace
from unittest.mock import patch

from window_manager import resolve_actual_game_pid


def test_resolve_actual_game_pid_walks_descendants():
    processes = {
        10: SimpleNamespace(children=lambda recursive=False: [processes[11]]),
        11: SimpleNamespace(pid=11, children=lambda recursive=False: [processes[12]]),
        12: SimpleNamespace(pid=12, children=lambda recursive=False: []),
    }
    processes[10].pid = 10
    with patch("window_manager.psutil.Process", return_value=processes[10]):
        assert resolve_actual_game_pid(10) == 12


def test_find_window_for_pid_filters_by_owner():
    from window_manager import find_window_for_pid

    with patch(
        "window_manager.win32gui.EnumWindows"
    ) as enum, patch(
        "window_manager.win32gui.IsWindowVisible",
        return_value=True,
    ), patch(
        "window_manager.win32process.GetWindowThreadProcessId",
        return_value=(0, 42),
    ), patch(
        "window_manager.win32gui.GetClientRect",
        return_value=(0, 0, 480, 270),
    ):
        enum.side_effect = (
            lambda callback, _:
            callback(100, None)
        )

        assert find_window_for_pid(42) == 100


def test_find_window_for_pid_ignores_zero_area_and_prefers_largest():
    from window_manager import find_window_for_pid

    rects = {
        100: (0, 0, 0, 0),
        101: (0, 0, 320, 180),
        102: (0, 0, 480, 270),
    }

    def enum_windows(callback, _):
        for hwnd in rects:
            callback(
                hwnd,
                None,
            )

    with patch(
        "window_manager.win32gui.EnumWindows",
        side_effect=enum_windows,
    ), patch(
        "window_manager.win32gui.IsWindowVisible",
        return_value=True,
    ), patch(
        "window_manager.win32process.GetWindowThreadProcessId",
        return_value=(0, 42),
    ), patch(
        "window_manager.win32gui.GetClientRect",
        side_effect=lambda hwnd: rects[hwnd],
    ):
        assert find_window_for_pid(42) == 102


def test_resolver_prefers_game_executable_inside_profile_clone():
    from pathlib import Path
    clone = Path("C:/profiles/P")
    launcher = SimpleNamespace(pid=10)
    wrong = SimpleNamespace(pid=11, name=lambda: "other.exe", exe=lambda: str(clone / "other.exe"))
    game = SimpleNamespace(pid=12, name=lambda: "ThienMenhLacHong.exe", exe=lambda: str(clone / "Data" / "ThienMenhLacHong.exe"))
    launcher.children = lambda recursive=False: [wrong, game]
    with patch("window_manager.psutil.Process", return_value=launcher):
        assert resolve_actual_game_pid(10, clone) == 12


def test_resolver_does_not_treat_launcher_as_actual_game():
    from pathlib import Path
    launcher = SimpleNamespace(pid=10, children=lambda recursive=False: [])
    with patch("window_manager.psutil.Process", return_value=launcher):
        assert resolve_actual_game_pid(10, Path("C:/profiles/P")) is None



def test_set_profile_window_title_uses_profile_name():
    from window_manager import set_profile_window_title

    with patch(
        "window_manager.win32gui.IsWindow",
        return_value=True,
    ), patch(
        "window_manager.win32gui.SetWindowText"
    ) as set_text:
        set_profile_window_title(
            123,
            "Acc 01",
        )

    set_text.assert_called_once_with(
        123,
        "Acc 01",
    )



def test_boss_scan_reveal_height_includes_client_top_and_hp_roi():
    from window_manager import boss_scan_reveal_height

    with patch(
        "window_manager.win32gui.IsWindow",
        return_value=True,
    ), patch(
        "window_manager.win32gui.GetWindowRect",
        return_value=(10, 20, 350, 240),
    ), patch(
        "window_manager.get_client_size",
        return_value=(320, 180),
    ), patch(
        "window_manager.win32gui.ClientToScreen",
        return_value=(14, 50),
    ):
        reveal = boss_scan_reveal_height(
            123,
            padding=4,
        )

    # title/client offset 30 + scaled max scan bottom (55 -> 20) + padding 4
    assert reveal == 54


def test_non_boss_window_visible_is_noop_when_stack_mode_is_off():
    from window_manager import (
        clear_boss_stack_order,
        non_boss_window_visible,
    )

    clear_boss_stack_order()

    with patch(
        "window_manager.win32gui.SetWindowPos"
    ) as set_pos:
        with non_boss_window_visible(123):
            pass

    set_pos.assert_not_called()


def test_non_boss_window_visible_raises_then_restores_stack_position():
    from window_manager import (
        clear_boss_stack_order,
        non_boss_window_visible,
        set_boss_stack_order,
    )

    with patch(
        "window_manager.win32gui.IsWindow",
        return_value=True,
    ), patch(
        "window_manager.win32gui.SetWindowPos"
    ) as set_pos:
        set_boss_stack_order(
            [101, 102, 103]
        )

        with non_boss_window_visible(102):
            pass

        assert set_pos.call_count == 2

        # First call raises 102 to topmost.
        assert set_pos.call_args_list[0].args[0] == 102

        # Restore puts 102 behind the window that should stay above it (103).
        assert set_pos.call_args_list[1].args[0] == 102
        assert set_pos.call_args_list[1].args[1] == 103

    clear_boss_stack_order()
