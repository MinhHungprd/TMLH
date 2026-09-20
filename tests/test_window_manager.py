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
    with patch("window_manager.win32gui.EnumWindows") as enum, patch("window_manager.win32gui.IsWindowVisible", return_value=True), patch("window_manager.win32process.GetWindowThreadProcessId", return_value=(0, 42)):
        enum.side_effect = lambda callback, _: callback(100, None)
        assert find_window_for_pid(42) == 100


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
