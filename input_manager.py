import ctypes
from ctypes import wintypes
import threading
import time

import win32api
import win32con
import win32gui
import win32process


FOREGROUND_RETRY_COUNT = 8
FOREGROUND_RETRY_DELAY = 0.04
UNICODE_KEY_DELAY = 0.002

INPUT_KEYBOARD = 1
KEYEVENTF_UNICODE = 0x0004


ULONG_PTR = (
    ctypes.c_ulonglong
    if ctypes.sizeof(ctypes.c_void_p) == 8
    else ctypes.c_ulong
)


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = (
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    )


class _INPUTUNION(ctypes.Union):
    _fields_ = (
        ("mi", _MOUSEINPUT),
        ("ki", _KEYBDINPUT),
        ("hi", _HARDWAREINPUT),
    )


class _INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = (
        ("type", wintypes.DWORD),
        ("union", _INPUTUNION),
    )


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

    @staticmethod
    def _unicode_input(
        code_unit,
        *,
        key_up=False,
    ):
        flags = KEYEVENTF_UNICODE

        if key_up:
            flags |= win32con.KEYEVENTF_KEYUP

        return _INPUT(
            type=INPUT_KEYBOARD,
            union=_INPUTUNION(
                ki=_KEYBDINPUT(
                    wVk=0,
                    wScan=code_unit,
                    dwFlags=flags,
                    time=0,
                    dwExtraInfo=0,
                )
            ),
        )

    @classmethod
    def _send_unicode_text(
        cls,
        text,
    ):
        """
        Type text without touching the Windows clipboard.

        UTF-16 code units are sent as KEYEVENTF_UNICODE key-down/key-up pairs.
        This avoids pywin32 SetClipboardData, which was observed causing
        process-wide heap corruption (0xc0000374) during the second credential
        field paste on Python 3.14/Windows.
        """
        encoded = str(text).encode(
            "utf-16-le"
        )

        if not encoded:
            return

        code_units = [
            int.from_bytes(
                encoded[index:index + 2],
                "little",
            )
            for index in range(
                0,
                len(encoded),
                2,
            )
        ]

        user32 = ctypes.windll.user32

        for code_unit in code_units:
            events = (
                _INPUT * 2
            )(
                cls._unicode_input(
                    code_unit,
                    key_up=False,
                ),
                cls._unicode_input(
                    code_unit,
                    key_up=True,
                ),
            )

            sent = user32.SendInput(
                len(events),
                events,
                ctypes.sizeof(_INPUT),
            )

            if sent != len(events):
                raise ctypes.WinError(
                    ctypes.get_last_error()
                )

            if UNICODE_KEY_DELAY:
                time.sleep(
                    UNICODE_KEY_DELAY
                )

    @classmethod
    def _physical_paste(
        cls,
        hwnd,
        text,
    ):
        """
        Replace the focused field and type the value directly.

        Kept under the historical "paste" method name for API compatibility,
        but the implementation intentionally does NOT use the clipboard.
        """
        cls._bring_game_forward(
            hwnd
        )

        if not cls._foreground_is_target(
            hwnd
        ):
            cls._bring_game_forward(
                hwnd
            )

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

        cls._send_unicode_text(
            text
        )
        time.sleep(0.05)

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
        Atomically focus one game input field and enter text into it.

        The global input lock covers click + direct Unicode keyboard input so
        another profile cannot steal foreground between the two actions.
        No Windows clipboard APIs are used.
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
