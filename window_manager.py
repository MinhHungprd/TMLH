from contextlib import contextmanager
import subprocess
import threading
import time
from pathlib import Path

import psutil
import win32con
import win32gui
import win32process

from automation_constants import BASE_HEIGHT, BOSS_HP
PROFILE_LAUNCH_LOCK = threading.Lock()

_STACK_ORDER_LOCK = threading.RLock()
_BOSS_STACK_ORDER = ()


class _ScreenVisibilityGate:
    """Many boss readers may capture together; asset raise is exclusive."""

    def __init__(self):
        self._condition = threading.Condition()
        self._readers = 0
        self._writer = False
        self._waiting_writers = 0

    @contextmanager
    def shared(self):
        with self._condition:
            while (
                self._writer
                or self._waiting_writers > 0
            ):
                self._condition.wait()
            self._readers += 1

        try:
            yield
        finally:
            with self._condition:
                self._readers -= 1
                if self._readers == 0:
                    self._condition.notify_all()

    @contextmanager
    def exclusive(self):
        with self._condition:
            self._waiting_writers += 1
            try:
                while (
                    self._writer
                    or self._readers > 0
                ):
                    self._condition.wait()
                self._writer = True
            finally:
                self._waiting_writers -= 1

        try:
            yield
        finally:
            with self._condition:
                self._writer = False
                self._condition.notify_all()


_SCREEN_VISIBILITY_GATE = _ScreenVisibilityGate()

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


def boss_scan_reveal_height(
    hwnd: int,
    padding: int = 4,
) -> int:
    """
    Outer-window height that must remain visible so the boss HP ROI stays
    fully exposed while windows are overlapped.
    """
    if not hwnd or not win32gui.IsWindow(hwnd):
        raise ValueError(f"Invalid HWND: {hwnd}")

    _left, outer_top, _right, outer_bottom = (
        win32gui.GetWindowRect(hwnd)
    )
    _client_width, client_height = get_client_size(hwnd)

    _x, base_y, _w, base_h = BOSS_HP
    roi_y = round(
        base_y * client_height / BASE_HEIGHT
    )
    roi_h = max(
        1,
        round(
            base_h * client_height / BASE_HEIGHT
        ),
    )

    _client_left, client_top = (
        win32gui.ClientToScreen(
            hwnd,
            (0, 0),
        )
    )

    reveal = (
        client_top
        - outer_top
        + roi_y
        + roi_h
        + padding
    )

    return max(
        1,
        min(
            reveal,
            outer_bottom - outer_top,
        ),
    )


def set_boss_stack_order(hwnds) -> None:
    """Remember back-to-front stack order for temporary interaction raises."""
    valid = tuple(
        hwnd
        for hwnd in hwnds
        if hwnd and win32gui.IsWindow(hwnd)
    )

    with _STACK_ORDER_LOCK:
        global _BOSS_STACK_ORDER
        _BOSS_STACK_ORDER = valid


def clear_boss_stack_order() -> None:
    with _STACK_ORDER_LOCK:
        global _BOSS_STACK_ORDER
        _BOSS_STACK_ORDER = ()


def _restore_stacked_window(hwnd: int, order) -> None:
    if not win32gui.IsWindow(hwnd):
        return

    try:
        index = order.index(hwnd)
    except ValueError:
        return

    flags = (
        win32con.SWP_NOMOVE
        | win32con.SWP_NOSIZE
        | win32con.SWP_NOACTIVATE
    )

    # Placement order is back-to-front: every lower window is above the
    # previous one so only the previous top strip remains visible.
    if index + 1 < len(order):
        above = order[index + 1]
        if win32gui.IsWindow(above):
            win32gui.SetWindowPos(
                hwnd,
                above,
                0,
                0,
                0,
                0,
                flags,
            )
            return

    win32gui.SetWindowPos(
        hwnd,
        win32con.HWND_TOPMOST,
        0,
        0,
        0,
        0,
        flags,
    )


@contextmanager
def boss_capture_visibility():
    """Allow concurrent boss captures unless an asset window is raised."""
    with _SCREEN_VISIBILITY_GATE.shared():
        yield


@contextmanager
def non_boss_window_visible(hwnd: int):
    """
    In overlap mode only, temporarily raise the target window for startup
    asset capture/click, then restore its stack position.

    Boss HP scans never use this helper, so their hot path gets no extra
    z-order work.
    """
    with _STACK_ORDER_LOCK:
        order = _BOSS_STACK_ORDER

    if hwnd not in order:
        yield
        return

    with _SCREEN_VISIBILITY_GATE.exclusive():
        if not win32gui.IsWindow(hwnd):
            yield
            return

        flags = (
            win32con.SWP_NOMOVE
            | win32con.SWP_NOSIZE
            | win32con.SWP_NOACTIVATE
        )

        win32gui.SetWindowPos(
            hwnd,
            win32con.HWND_TOPMOST,
            0,
            0,
            0,
            0,
            flags,
        )

        try:
            yield
        finally:
            with _STACK_ORDER_LOCK:
                current_order = _BOSS_STACK_ORDER

            if hwnd in current_order:
                _restore_stacked_window(
                    hwnd,
                    current_order,
                )

def set_profile_window_title(hwnd: int, profile_name: str) -> None:
    """Set the visible game window title to the configured profile name."""
    if not hwnd or not win32gui.IsWindow(hwnd):
        raise ValueError(f"Invalid HWND: {hwnd}")

    title = str(profile_name).strip()
    if not title:
        raise ValueError("Profile name cannot be empty")

    win32gui.SetWindowText(
        hwnd,
        title,
    )


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
def acquire_profile_window(
    game_path,
    stop_event,
    timeout=30,
    before_launch=None,
    window_title=None,
):

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
        if before_launch is not None:
            before_launch()

        if stop_event.is_set():
            raise InterruptedError(
                "Profile launch cancelled"
            )

        launcher_pid = subprocess.Popen(
            [str(launcher)],
            cwd=str(clone),
        ).pid
    else:
        launcher_pid = launcher_process.pid if launcher_process else None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not stop_event.is_set():
        if game_process is not None and game_process.is_running():
            hwnd = find_window_for_pid(game_process.pid)
            if hwnd:
                if window_title:
                    set_profile_window_title(
                        hwnd,
                        window_title,
                    )
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

def get_client_size(hwnd: int) -> tuple[int, int]:
    if not hwnd or not win32gui.IsWindow(hwnd):
        raise ValueError(f"Invalid HWND: {hwnd}")

    left, top, right, bottom = win32gui.GetClientRect(hwnd)

    return right - left, bottom - top

def resize_client(hwnd: int, width: int, height: int) -> tuple[int, int]:
    if not hwnd or not win32gui.IsWindow(hwnd):
        raise ValueError(f"Invalid HWND: {hwnd}")

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)

    client_left, client_top, client_right, client_bottom = (
        win32gui.GetClientRect(hwnd)
    )

    current_client_width = client_right - client_left
    current_client_height = client_bottom - client_top

    current_outer_width = right - left
    current_outer_height = bottom - top

    border_width = current_outer_width - current_client_width
    border_height = current_outer_height - current_client_height

    target_outer_width = width + border_width
    target_outer_height = height + border_height

    win32gui.SetWindowPos(
        hwnd,
        0,
        left,
        top,
        target_outer_width,
        target_outer_height,
        win32con.SWP_NOZORDER
        | win32con.SWP_NOACTIVATE,
    )

    # Quan trọng: đọc lại kích thước client THỰC TẾ sau resize.
    return get_client_size(hwnd)

def ensure_client_size(
    hwnd: int,
    width: int,
    height: int,
    tolerance: int = 1,
) -> bool:
    """
    Đảm bảo client area luôn đúng resolution mong muốn.

    Returns:
        True  -> vừa phải resize
        False -> kích thước đã đúng
    """
    actual_width, actual_height = get_client_size(hwnd)

    if (
        abs(actual_width - width) <= tolerance
        and abs(actual_height - height) <= tolerance
    ):
        return False

    resize_client(hwnd, width, height)
    set_window_topmost(hwnd, True)

    return True
def has_remote_port(
    pid: int,
    remote_ip: str,
    remote_port: int,
) -> bool:
    try:
        process = psutil.Process(pid)

        for conn in process.net_connections(kind="tcp"):
            if not conn.raddr:
                continue

            if conn.status != psutil.CONN_ESTABLISHED:
                continue

            if (
                conn.raddr.ip == remote_ip
                and conn.raddr.port == remote_port
            ):
                return True

    except psutil.Error:
        return False

    return False


def wait_for_remote_port(
    pid: int,
    remote_ip: str,
    remote_port: int,
    stop_event,
    timeout: float = 35.0,
) -> bool:
    deadline = time.monotonic() + timeout

    while (
        time.monotonic() < deadline
        and not stop_event.is_set()
    ):
        if has_remote_port(
            pid,
            remote_ip,
            remote_port,
        ):
            return True

        stop_event.wait(0.25)

    return False