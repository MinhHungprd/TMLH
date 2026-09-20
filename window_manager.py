import subprocess
import time
from pathlib import Path

import psutil
import win32con
import win32gui
import win32process


def launch_profile(profile):
    path = Path(profile.game_path)
    return subprocess.Popen([str(path / "ThienMenhLacHong_Launcher.exe")], cwd=str(path))


def _inside_clone(process, game_path):
    try:
        candidate = Path(process.exe()).resolve()
        return candidate.is_relative_to(Path(game_path).resolve())
    except (psutil.Error, OSError, ValueError):
        return False


def resolve_actual_game_pid(launcher_pid: int, game_path=None) -> int:
    root = psutil.Process(launcher_pid)
    descendants = root.children(recursive=True)
    if not descendants:
        return None if game_path is not None else launcher_pid
    if game_path is not None:
        for child in descendants:
            if _inside_clone(child, game_path) and child.name().lower() == "thienmenhlachong.exe":
                return child.pid
        return None
    current = descendants[-1]
    while True:
        children = current.children()
        if not children:
            return current.pid
        current = children[-1]


def find_window_for_pid(pid: int):
    found = []

    def callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            _, owner = win32process.GetWindowThreadProcessId(hwnd)
            if owner == pid:
                found.append(hwnd)

    win32gui.EnumWindows(callback, None)
    return found[0] if found else None

def set_window_topmost(hwnd: int, enabled: bool = True) -> None:
    """
    Đặt cửa sổ game thành Always On Top mà không giành focus.

    enabled=True:
        cửa sổ nằm trên các cửa sổ bình thường.

    enabled=False:
        trả lại z-order bình thường.
    """
    if not hwnd or not win32gui.IsWindow(hwnd):
        raise ValueError(f"Invalid HWND: {hwnd}")

    insert_after = (
        win32con.HWND_TOPMOST
        if enabled
        else win32con.HWND_NOTOPMOST
    )

    win32gui.SetWindowPos(
        hwnd,
        insert_after,
        0,
        0,
        0,
        0,
        win32con.SWP_NOMOVE
        | win32con.SWP_NOSIZE
        | win32con.SWP_NOACTIVATE,
    )
def acquire_profile_window(game_path, stop_event, timeout=30):
    """Find a process inside this clone or launch it, then bind its own HWND."""
    clone = Path(game_path).resolve()
    launcher = clone / "ThienMenhLacHong_Launcher.exe"
    if not launcher.is_file():
        raise FileNotFoundError(launcher)
    own_processes = [p for p in psutil.process_iter() if _inside_clone(p, clone)]
    game_process = next((p for p in own_processes if p.name().lower() == "thienmenhlachong.exe"), None)
    launcher_process = next((p for p in own_processes if p.name().lower() == launcher.name.lower()), None)
    launched = game_process is None and launcher_process is None
    if launched:
        launcher_pid = subprocess.Popen([str(launcher)], cwd=str(clone)).pid
    else:
        launcher_pid = launcher_process.pid if launcher_process else None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not stop_event.is_set():
        if game_process is not None and game_process.is_running():
            hwnd = find_window_for_pid(game_process.pid)
            if hwnd:
                return game_process.pid, hwnd, launched
        if launcher_pid is not None:
            try:
                actual_pid = resolve_actual_game_pid(launcher_pid, clone)
                if actual_pid is not None:
                    game_process = psutil.Process(actual_pid)
            except psutil.Error:
                launcher_pid = None
        # A launcher may exit before the child window appears; the clone path
        # still identifies the profile without confusing it with another clone.
        if launcher_pid is None:
            game_process = next((p for p in psutil.process_iter() if _inside_clone(p, clone)
                                 and p.name().lower() == "thienmenhlachong.exe"), None)
        stop_event.wait(0.25)
    if stop_event.is_set():
        raise InterruptedError("Profile launch cancelled")
    raise TimeoutError(f"No game window for clone: {clone}")


def resize_client(hwnd: int, width: int, height: int) -> None:
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    client = win32gui.GetClientRect(hwnd)

    outer_width = right - left + width - client[2]
    outer_height = bottom - top + height - client[3]

    win32gui.SetWindowPos(
        hwnd,
        0,
        left,
        top,
        outer_width,
        outer_height,
        win32con.SWP_NOZORDER
        | win32con.SWP_NOACTIVATE,
    )