import threading

import win32api
import win32con
import win32gui
import win32process


class GlobalInputLock:
    _lock = threading.Lock()

    def __enter__(self):
        self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._lock.release()


class InputManager:
    def __init__(self, click=None):
        self.click = click or self._physical_click

    @staticmethod
    def _physical_click(
        hwnd,
        x,
        y,
    ):
        sx, sy = win32gui.ClientToScreen(
            hwnd,
            (x, y),
        )

        # Windows có thể từ chối foreground
        # nếu process hiện tại không có quyền
        # giành focus.
        #
        # Không để lỗi này giết toàn worker.
        try:
            win32gui.SetForegroundWindow(
                hwnd
            )
        except Exception:
            pass

        win32api.SetCursorPos(
            (sx, sy)
        )

        win32api.mouse_event(
            win32con.MOUSEEVENTF_LEFTDOWN,
            0,
            0,
        )

        win32api.mouse_event(
            win32con.MOUSEEVENTF_LEFTUP,
            0,
            0,
        )

    def click_center(self, hwnd, coordinates, stop_event=None, expected_pid=None):
        while not GlobalInputLock._lock.acquire(timeout=0.1):
            if stop_event is not None and stop_event.is_set():
                return False
        try:
            if stop_event is not None and stop_event.is_set():
                return False
            if self.click == self._physical_click:
                if not win32gui.IsWindow(hwnd):
                    raise RuntimeError("Target game window is closed")
                if expected_pid is not None and win32process.GetWindowThreadProcessId(hwnd)[1] != expected_pid:
                    raise RuntimeError("Target game window changed process owner")
            self.click(hwnd, *coordinates)
            return True
        finally:
            GlobalInputLock._lock.release()
