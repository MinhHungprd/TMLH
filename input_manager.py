import threading
import time

import win32api
import win32clipboard
import win32con
import win32gui
import win32process


FOREGROUND_RETRY_COUNT = 8
FOREGROUND_RETRY_DELAY = 0.04
CLIPBOARD_RETRY_COUNT = 10
CLIPBOARD_RETRY_DELAY = 0.02


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
    def _foreground_is_target(
        hwnd,
    ):
        try:
            return (
                win32gui.GetForegroundWindow()
                == hwnd
            )
        except Exception:
            return False

    @classmethod
    def _bring_game_forward(
        cls,
        hwnd,
    ):
        """
        Bring the exact game HWND to foreground and verify it before any
        system-wide mouse/keyboard input is emitted.

        SetForegroundWindow can be rejected transiently by Windows. The old
        implementation ignored that failure and still sent Ctrl+A/Ctrl+V,
        which could land in the management UI instead.
        """
        last_error = None

        for _attempt in range(
            FOREGROUND_RETRY_COUNT
        ):
            if not win32gui.IsWindow(
                hwnd
            ):
                raise RuntimeError(
                    "Target game window is closed"
                )

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
                win32gui.BringWindowToTop(
                    hwnd
                )
                win32gui.SetForegroundWindow(
                    hwnd
                )
            except Exception as exc:
                last_error = exc

            if cls._foreground_is_target(
                hwnd
            ):
                return

            time.sleep(
                FOREGROUND_RETRY_DELAY
            )

        detail = (
            f": {last_error}"
            if last_error is not None
            else ""
        )
        raise RuntimeError(
            (
                "Không thể đưa cửa sổ game lên foreground"
                + detail
            )
        )

    @classmethod
    def _physical_click(
        cls,
        hwnd,
        x,
        y,
    ):
        # Verify foreground BEFORE clicking. Coordinates are screen-global;
        # clicking while another TOPMOST window covers the game can hit the
        # management UI instead.
        cls._bring_game_forward(
            hwnd
        )

        sx, sy = (
            win32gui.ClientToScreen(
                hwnd,
                (x, y),
            )
        )

        if not cls._foreground_is_target(
            hwnd
        ):
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
    def _open_clipboard_with_retry():
        last_error = None

        for _attempt in range(
            CLIPBOARD_RETRY_COUNT
        ):
            try:
                win32clipboard.OpenClipboard()
                return
            except Exception as exc:
                last_error = exc
                time.sleep(
                    CLIPBOARD_RETRY_DELAY
                )

        raise RuntimeError(
            (
                "Không mở được Windows clipboard"
                + (
                    f": {last_error}"
                    if last_error is not None
                    else ""
                )
            )
        )

    @classmethod
    def _read_clipboard_text(
        cls,
    ):
        previous = None

        cls._open_clipboard_with_retry()

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

    @classmethod
    def _set_clipboard_text(
        cls,
        value,
    ):
        cls._open_clipboard_with_retry()

        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(
                win32con.CF_UNICODETEXT,
                str(value),
            )
        finally:
            win32clipboard.CloseClipboard()

    @staticmethod
    def _send_ctrl_combo(
        virtual_key,
    ):
        """
        Send one Ctrl+key chord and always attempt to release both keys.
        A transient Win32 failure must never leave Ctrl logically pressed.
        """
        try:
            win32api.keybd_event(
                win32con.VK_CONTROL,
                0,
                0,
                0,
            )
            win32api.keybd_event(
                virtual_key,
                0,
                0,
                0,
            )
        finally:
            try:
                win32api.keybd_event(
                    virtual_key,
                    0,
                    win32con.KEYEVENTF_KEYUP,
                    0,
                )
            finally:
                win32api.keybd_event(
                    win32con.VK_CONTROL,
                    0,
                    win32con.KEYEVENTF_KEYUP,
                    0,
                )

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
            # Clipboard restore is best-effort. Failure to read the previous
            # user clipboard must not prevent login.
            previous = None

        try:
            cls._set_clipboard_text(
                text
            )

            if not cls._foreground_is_target(
                hwnd
            ):
                cls._bring_game_forward(
                    hwnd
                )

            # Replace any remembered/autofilled field content.
            cls._send_ctrl_combo(
                ord("A")
            )
            time.sleep(0.03)

            if not cls._foreground_is_target(
                hwnd
            ):
                cls._bring_game_forward(
                    hwnd
                )

            cls._send_ctrl_combo(
                ord("V")
            )

            # Give the Unity input field time to consume keyboard events before
            # restoring the user's clipboard text.
            time.sleep(0.10)

        finally:
            try:
                if previous is not None:
                    cls._set_clipboard_text(
                        previous
                    )
                else:
                    cls._open_clipboard_with_retry()
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
