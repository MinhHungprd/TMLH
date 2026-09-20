"""Non-blocking per-window login automation using client-relative assets."""

from dataclasses import dataclass
import json
import subprocess
import threading
import time
from pathlib import Path
import tkinter as tk

import ctypes


def _enable_dpi_awareness():
    """Keep Win32 client coordinates and screenshot pixels in the same space."""
    if hasattr(ctypes, "windll"):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except (AttributeError, OSError):
                pass


_enable_dpi_awareness()

import win32api
import win32con
import win32gui
import win32process
from PIL import ImageGrab

from asset_cropper import DEFAULT_GAME, is_game_process


@dataclass
class LoginPlan:
    username: str
    password: str
    server: str = "Văn Lang"
    step_sleep: float = 0.5


class GameWorker:
    """Owns one game process and can run independently from other workers."""

    def __init__(self, plan: LoginPlan, assets_dir: str | Path = "assets", on_status=None):
        self.plan = plan
        self.assets_dir = Path(assets_dir)
        self.on_status = on_status or (lambda _text: None)
        self.process = None
        self.hwnd = None
        self.stop_event = threading.Event()

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()

    def stop(self):
        self.stop_event.set()

    def run(self):
        try:
            self.status("Đang mở game")
            self.process = subprocess.Popen([str(DEFAULT_GAME)])
            self.hwnd = self._wait_for_window()
            if not self.hwnd:
                raise RuntimeError("Không tìm thấy cửa sổ game")
            if self._login_flow():
                self.status("Đã đăng nhập")
            else:
                self.status("Timeout: chưa xác nhận đăng nhập")
        except Exception as exc:
            self.status(f"Lỗi: {exc}")

    def status(self, text):
        self.on_status(text)

    def _wait_for_window(self, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not self.stop_event.is_set():
            found = self._find_window()
            if found:
                return found
            time.sleep(0.25)
        return None

    def _find_window(self):
        found = []
        def callback(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd) or not win32gui.GetWindowText(hwnd):
                return
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                process = __import__("psutil").Process(pid)
                # The launcher often replaces itself with a Unity child process.
                if is_game_process(process.exe()) or (self.process and pid == self.process.pid):
                    found.append(hwnd)
            except Exception:
                pass
        win32gui.EnumWindows(callback, None)
        return found[0] if found else None

    def _ensure_window(self):
        if self.hwnd and win32gui.IsWindow(self.hwnd):
            return True
        self.status("Cửa sổ chưa sẵn sàng, chờ game khởi động lại")
        self.hwnd = self._wait_for_window(timeout=30)
        return bool(self.hwnd)

    def _sleep(self, seconds=None):
        self.stop_event.wait(self.plan.step_sleep if seconds is None else seconds)

    def _client_size(self):
        if not self._ensure_window():
            raise RuntimeError("Không còn cửa sổ game hợp lệ")
        rect = win32gui.GetClientRect(self.hwnd)
        return rect[2], rect[3]

    def _screen_point(self, x, y):
        if not self._ensure_window():
            raise RuntimeError("Không còn cửa sổ game hợp lệ")
        return win32gui.ClientToScreen(self.hwnd, (x, y))

    def tap(self, x, y):
        self.status(f"tap client ({x}, {y})")
        sx, sy = self._screen_point(x, y)
        self._send_input_click(sx, sy)
        self._sleep()

    def _send_input_click(self, screen_x, screen_y):
        """Send a real OS-level synthetic click for Unity/Raw Input games."""
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.SetForegroundWindow(self.hwnd)
        time.sleep(0.08)
        screen_width = user32.GetSystemMetrics(0)
        screen_height = user32.GetSystemMetrics(1)

        class MouseInput(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]
        class Input(ctypes.Structure):
            class U(ctypes.Union):
                _fields_ = [("mi", MouseInput)]
            _anonymous_ = ("u",)
            _fields_ = [("type", wintypes.DWORD), ("u", U)]

        absolute_x = round(screen_x * 65535 / max(1, screen_width - 1))
        absolute_y = round(screen_y * 65535 / max(1, screen_height - 1))
        extra = ctypes.pointer(wintypes.ULONG(0))
        inputs = (Input * 3)(
            Input(0, Input.U(mi=MouseInput(absolute_x, absolute_y, 0, 0x0001 | 0x8000, 0, extra))),
            Input(0, Input.U(mi=MouseInput(0, 0, 0, 0x0002, 0, extra))),
            Input(0, Input.U(mi=MouseInput(0, 0, 0, 0x0004, 0, extra))),
        )
        sent = user32.SendInput(3, ctypes.byref(inputs), ctypes.sizeof(Input))
        self.status(f"SendInput screen ({screen_x}, {screen_y}) -> {sent}/3")
        if sent != 3:
            raise ctypes.WinError(ctypes.get_last_error())

    def paste(self, text):
        self.status(f"paste {len(text)} ký tự")
        clipboard = tk.Tk()
        clipboard.withdraw()
        clipboard.clipboard_clear()
        clipboard.clipboard_append(text)
        clipboard.update()
        win32gui.PostMessage(self.hwnd, win32con.WM_PASTE, 0, 0)
        clipboard.destroy()
        self._sleep()

    def scan(self, asset_name, threshold=0.90):
        self.status(f"scan {asset_name}")
        path = self.assets_dir / asset_name
        if not path.exists():
            raise FileNotFoundError(f"Thiếu asset: {path}")
        try:
            import cv2
            import numpy as np
        except ImportError as exc:
            raise RuntimeError("Cần cài opencv-python và numpy để scan asset") from exc
        left, top = self._screen_point(0, 0)
        width, height = self._client_size()
        template = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if template is None:
            raise RuntimeError(f"Không đọc được asset: {path}")
        # Assets created by asset_cropper have a JSON sidecar containing the
        # client-relative box. Match inside that exact box first; this avoids
        # false negatives caused by searching the whole client area.
        metadata_path = path.with_suffix(".json")
        region_x, region_y = 0, 0
        region_width, region_height = width, height
        if metadata_path.exists():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            box = metadata.get("box", {})
            region_x = int(box.get("x", 0))
            region_y = int(box.get("y", 0))
            region_width = int(box.get("width", template.shape[1]))
            region_height = int(box.get("height", template.shape[0]))
            self.status(f"scan box client x={region_x}, y={region_y}, w={region_width}, h={region_height}")
        region_width = min(region_width, width - region_x)
        region_height = min(region_height, height - region_y)
        if region_width < template.shape[1] or region_height < template.shape[0]:
            return False, 0.0, (region_x, region_y)
        screenshot = np.array(ImageGrab.grab(bbox=(left + region_x, top + region_y,
                                                    left + region_x + region_width,
                                                    top + region_y + region_height)))
        self.status(f"capture live {screenshot.shape[1]}x{screenshot.shape[0]}")
        screen = cv2.cvtColor(screenshot, cv2.COLOR_RGB2GRAY)
        result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, location = cv2.minMaxLoc(result)
        matched = score >= threshold
        self.status(f"scan {asset_name}: {'MATCH' if matched else 'MISS'} score={score:.3f}")
        return matched, score, (location[0] + region_x, location[1] + region_y)

    def _tap_asset_if_present(self, name):
        present, _, _ = self.scan(name)
        if present:
            self.tap_asset(name)
        return present

    def wait_for_asset(self, name, timeout=60):
        """Poll until an asset appears; polling interval follows the plan sleep."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not self.stop_event.is_set():
            self._exception_pass()
            matched, score, _ = self.scan(name)
            if matched:
                return True
            self.status(f"Chờ {name} ({int(max(0, deadline - time.monotonic()))}s)")
            self._sleep()
        return False

    def _exception_pass(self):
        """Run the exceptional actions on every polling cycle."""
        for asset in (
            "asset__x329_y396_w56_h30.png",
            "asset__x742_y437_w45_h24.png",
        ):
            try:
                self.tap_asset(asset)
            except FileNotFoundError:
                continue

    def tap_asset(self, name):
        present, _, (x, y) = self.scan(name)
        if not present:
            return False
        import cv2
        image = cv2.imread(str(self.assets_dir / name), cv2.IMREAD_GRAYSCALE)
        self.tap(x + image.shape[1] // 2, y + image.shape[0] // 2)
        return True

    def _login_flow(self):
        outside = "asset__x795_y29_w29_h38.png"
        intro = "asset__x742_y437_w45_h24.png"
        start = "asset__x392_y389_w75_h35.png"
        self.status("Đang scan màn hình")
        if self.wait_for_asset(outside, timeout=30):
            self.status("Bước 1/3: chọn server")
            self.tap(429, 354)
            self.tap(439, 200 if self.plan.server == "Văn Lang" else 245)

            self.status("Bước 2/3: nhập tài khoản và mật khẩu")
            if not self.wait_for_asset(outside, timeout=30):
                return False
            self.tap(444, 265)
            self.tap(418, 186)
            self.paste(self.plan.username)
            self.tap(429, 238)
            self.paste(self.plan.password)
            self.tap(439, 307)
        else:
            self.status("Không thấy màn hình ngoài game; kiểm tra nút bắt đầu")

        self.status("Bước 3/3: chờ nút bắt đầu")
        if self.wait_for_asset(start, timeout=60):
            self.tap_asset(start)
            return True
        return False
