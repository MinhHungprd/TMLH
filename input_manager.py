import threading
import time

import win32api
import win32clipboard
import win32con
import win32gui
import win32process


class GlobalInputLock:
    _lock = threading.Lock()

    def __enter__(self):
        self._lock.acquire()
        return self

    def __exit__(
        self,
        exc_type,
        exc,
        tb,
    ):
        self._lock.release()


class InputManager:
    def __init__(
        self,
        click=None,
        paste=None,
    ):
        self.click = (
            click
            or self._physical_click
        )
        self.paste = (
            paste
            or self._physical_paste
        )

    @staticmethod
    def _bring_game_forward(
        hwnd,
    ):
        try:
            win32gui.SetWindowPos(
                hwnd,
                win32con.HWND_TOPMOST,
                0,
                0,
                0,
                0,
                win32con.SWP_NOMOVE
                | win32con.SWP_NOSIZE
                | win32con.SWP_NOACTIVATE,
            )
        except Exception:
            pass

        try:
            win32gui.SetForegroundWindow(
                hwnd
            )
        except Exception:
            pass

    @classmethod
    def _physical_click(
        cls,
        hwnd,
        x,
        y,
    ):
        sx, sy = (
            win32gui.ClientToScreen(
                hwnd,
                (x, y),
            )
        )

        cls._bring_game_forward(
            hwnd
        )

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

    @staticmethod
    def _read_clipboard_text():
        previous = None

        win32clipboard.OpenClipboard()

        try:
            if win32clipboard.IsClipboardFormatAvailable(
                win32con.CF_UNICODETEXT
            ):
                previous = (
                    win32clipboard
                    .GetClipboardData(
                        win32con.CF_UNICODETEXT
                    )
                )
        finally:
            win32clipboard.CloseClipboard()

        return previous

    @staticmethod
    def _set_clipboard_text(
        value,
    ):
        win32clipboard.OpenClipboard()

        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(
                win32con.CF_UNICODETEXT,
                str(value),
            )
        finally:
            win32clipboard.CloseClipboard()

    @classmethod
    def _physical_paste(
        cls,
        hwnd,
        text,
    ):
        cls._bring_game_forward(
            hwnd
        )

        previous = None

        try:
            previous = (
                cls._read_clipboard_text()
            )
        except Exception:
            previous = None

        try:
            cls._set_clipboard_text(
                text
            )

            # Replace any remembered/autofilled field content.
            win32api.keybd_event(
                win32con.VK_CONTROL,
                0,
                0,
                0,
            )
            win32api.keybd_event(
                ord("A"),
                0,
                0,
                0,
            )
            win32api.keybd_event(
                ord("A"),
                0,
                win32con.KEYEVENTF_KEYUP,
                0,
            )
            win32api.keybd_event(
                win32con.VK_CONTROL,
                0,
                win32con.KEYEVENTF_KEYUP,
                0,
            )
            time.sleep(0.03)

            win32api.keybd_event(
                win32con.VK_CONTROL,
                0,
                0,
                0,
            )
            win32api.keybd_event(
                ord("V"),
                0,
                0,
                0,
            )
            win32api.keybd_event(
                ord("V"),
                0,
                win32con.KEYEVENTF_KEYUP,
                0,
            )
            win32api.keybd_event(
                win32con.VK_CONTROL,
                0,
                win32con.KEYEVENTF_KEYUP,
                0,
            )

            # Give the Unity input field time to consume WM/keyboard events
            # before restoring the user's clipboard text.
            time.sleep(0.08)

        finally:
            try:
                if previous is not None:
                    cls._set_clipboard_text(
                        previous
                    )
                else:
                    win32clipboard.OpenClipboard()
                    try:
                        win32clipboard.EmptyClipboard()
                    finally:
                        win32clipboard.CloseClipboard()
            except Exception:
                pass

    @staticmethod
    def _validate_target(
        hwnd,
        expected_pid=None,
    ):
        if not win32gui.IsWindow(
            hwnd
        ):
            raise RuntimeError(
                "Target game window is closed"
            )

        if expected_pid is None:
            return

        owner_pid = (
            win32process
            .GetWindowThreadProcessId(
                hwnd
            )[1]
        )

        if owner_pid != expected_pid:
            raise RuntimeError(
                "Target game window changed process owner"
            )

    @staticmethod
    def _acquire(
        stop_event=None,
    ):
        while not GlobalInputLock._lock.acquire(
            timeout=0.1
        ):
            if (
                stop_event is not None
                and stop_event.is_set()
            ):
                return False

        return True

    def click_center(
        self,
        hwnd,
        coordinates,
        stop_event=None,
        expected_pid=None,
    ):
        if not self._acquire(
            stop_event
        ):
            return False

        try:
            if (
                stop_event is not None
                and stop_event.is_set()
            ):
                return False

            if self.click == self._physical_click:
                self._validate_target(
                    hwnd,
                    expected_pid,
                )

            self.click(
                hwnd,
                *coordinates,
            )
            return True

        finally:
            GlobalInputLock._lock.release()

    def click_and_paste(
        self,
        hwnd,
        coordinates,
        text,
        stop_event=None,
        expected_pid=None,
    ):
        """
        Atomically focus one game input field and paste text into it.

        The global input lock covers click + clipboard paste so another
        profile cannot steal foreground between the two actions.
        """
        if not self._acquire(
            stop_event
        ):
            return False

        try:
            if (
                stop_event is not None
                and stop_event.is_set()
            ):
                return False

            if self.click == self._physical_click:
                self._validate_target(
                    hwnd,
                    expected_pid,
                )

            self.click(
                hwnd,
                *coordinates,
            )
            time.sleep(0.08)

            self.paste(
                hwnd,
                text,
            )
            return True

        finally:
            GlobalInputLock._lock.release()
