"""Crop image assets from the game client area for later image matching."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

WINDOW_SIZES = ("320x180", "480x270", "640x360", "800x450", "860x484")
DEFAULT_GAME = Path(r"D:\2026_Part2\2026_Part2\Source\Bot_Thien_Menh_Lac_Hong\Game\ThienMenhLacHong_Launcher\ThienMenhLacHong_Launcher.exe")


def is_game_process(process_path: str | Path) -> bool:
    """Accept the launcher or the Unity child process it starts."""
    try:
        candidate = Path(process_path).resolve()
        launcher = DEFAULT_GAME.resolve()
        return candidate == launcher or candidate.parent == launcher.parent / "Data"
    except (OSError, RuntimeError, ValueError):
        return False


@dataclass(frozen=True)
class CropBox:
    x: int
    y: int
    width: int
    height: int


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip()).strip("_") or "asset"


def asset_filename(label: str, box: CropBox) -> str:
    return f"{_safe_name(label)}__x{box.x}_y{box.y}_w{box.width}_h{box.height}.png"


def metadata_for(label: str, box: CropBox, client_size: tuple[int, int], source: str) -> dict:
    return {
        "label": label,
        "box": {"x": box.x, "y": box.y, "width": box.width, "height": box.height},
        "client_size": {"width": client_size[0], "height": client_size[1]},
        "source": source,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


class CropperApp(tk.Tk):
    BG = "#111820"
    PANEL = "#18232d"
    ACCENT = "#d6a84f"
    TEXT = "#d9e2e9"
    MUTED = "#8ea0ad"

    def __init__(self):
        super().__init__()
        self.title("Asset Cropper - Thien Menh Lac Hong")
        self.geometry("980x720")
        self.configure(bg=self.BG)
        self.image = None
        self.tk_image = None
        self.scale = 1.0
        self.start = None
        self.rect = None
        self.current_box = None
        self.source_name = tk.StringVar(value="Chưa chọn ảnh/cửa sổ")
        self.label = tk.StringVar(value="asset")
        self.size = tk.StringVar(value=WINDOW_SIZES[0])
        self._style()
        self._ui()

    def _style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=self.BG)
        style.configure("TLabel", background=self.BG, foreground=self.TEXT)
        style.configure("Muted.TLabel", background=self.BG, foreground=self.MUTED)
        style.configure("TButton", padding=(8, 4))

    def _ui(self):
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="CROP ASSET THEO VỊ TRÍ CLIENT", foreground=self.ACCENT, font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(top, text="Kéo-thả trên vùng game; tọa độ lưu là tọa độ bên trong client, không phải màn hình.", style="Muted.TLabel").pack(anchor="w")
        controls = ttk.Frame(top)
        controls.pack(fill="x", pady=(10, 0))
        ttk.Button(controls, text="Chụp cửa sổ game", command=self.capture_game).pack(side="left")
        ttk.Button(controls, text="Mở screenshot", command=self.open_image).pack(side="left", padx=(6, 0))
        ttk.Label(controls, text="Tên asset").pack(side="left", padx=(15, 4))
        ttk.Entry(controls, textvariable=self.label, width=22).pack(side="left")
        ttk.Label(controls, text="Preset").pack(side="left", padx=(15, 4))
        ttk.Combobox(controls, textvariable=self.size, values=WINDOW_SIZES, state="readonly", width=11).pack(side="left")
        ttk.Button(controls, text="Đổi size game", command=self.resize_game).pack(side="left", padx=(12, 4))
        ttk.Button(controls, text="Copy box client", command=self.copy_box_client).pack(side="left", padx=(0, 4))
        ttk.Button(controls, text="Lưu asset", command=self.save_crop).pack(side="left")
        ttk.Label(top, textvariable=self.source_name, style="Muted.TLabel").pack(anchor="w", pady=(6, 0))
        canvas_frame = tk.Frame(self, bg="#0c1116", bd=1, relief="sunken")
        canvas_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.canvas = tk.Canvas(canvas_frame, bg="#0c1116", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<ButtonPress-1>", self.begin_crop)
        self.canvas.bind("<B1-Motion>", self.move_crop)
        self.canvas.bind("<ButtonRelease-1>", self.end_crop)
        self.info = tk.StringVar(value="Chưa có vùng crop")
        ttk.Label(self, textvariable=self.info, style="Muted.TLabel").pack(anchor="w", padx=10, pady=(0, 8))

    def open_image(self):
        path = filedialog.askopenfilename(filetypes=[("PNG/JPEG", "*.png *.jpg *.jpeg"), ("All files", "*.*")])
        if not path:
            return
        try:
            from PIL import Image, ImageTk
            self.image = Image.open(path).convert("RGB")
            self._image_tk = ImageTk.PhotoImage(self.image)
        except ImportError:
            messagebox.showerror("Thiếu thư viện", "Cài Pillow bằng: python -m pip install Pillow", parent=self)
            return
        self.scale = min(1.0, 900 / self.image.width, 600 / self.image.height)
        preview = self.image.resize((int(self.image.width * self.scale), int(self.image.height * self.scale)))
        self._image_tk = ImageTk.PhotoImage(preview)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self._image_tk, anchor="nw")
        self.canvas.config(scrollregion=(0, 0, preview.width, preview.height))
        self.source_name.set(f"Nguồn: {path} | client {self.image.width}×{self.image.height}")
        self.info.set("Kéo-thả để chọn vùng crop")

    def capture_game(self):
        try:
            import win32gui
            import win32process
            from PIL import ImageGrab
        except ImportError:
            messagebox.showerror("Thiếu thư viện", "Cần pywin32 và Pillow để chụp cửa sổ game.", parent=self)
            return
        target = self._find_game_window(win32gui, win32process)
        if target is None:
            messagebox.showwarning("Không tìm thấy game", f"Hãy mở game trước:\n{DEFAULT_GAME}", parent=self)
            return
        try:
            left, top = win32gui.ClientToScreen(target, (0, 0))
            right, bottom = win32gui.ClientToScreen(target, win32gui.GetClientRect(target)[2:])
            from PIL import ImageGrab
            self.image = ImageGrab.grab(bbox=(left, top, right, bottom)).convert("RGB")
            self._show_image("Cửa sổ game (client area)")
        except Exception as exc:
            messagebox.showerror("Không thể chụp cửa sổ", str(exc), parent=self)

    def _find_game_window(self, win32gui, win32process):
        import psutil
        result = None
        expected = DEFAULT_GAME.name.lower()
        def collect(hwnd, _):
            nonlocal result
            if result or not win32gui.IsWindowVisible(hwnd) or not win32gui.GetWindowText(hwnd):
                return
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                process = psutil.Process(pid)
                if is_game_process(process.exe()) or process.name().lower() == expected:
                    result = hwnd
            except (OSError, psutil.Error):
                pass
        win32gui.EnumWindows(collect, None)
        return result

    def resize_game(self):
        try:
            import win32con
            import win32gui
            import win32process
        except ImportError:
            messagebox.showerror("Thiếu thư viện", "Cần pywin32 để đổi kích thước cửa sổ.", parent=self)
            return
        hwnd = self._find_game_window(win32gui, win32process)
        if hwnd is None:
            messagebox.showwarning("Không tìm thấy game", "Hãy mở game trước khi đổi kích thước.", parent=self)
            return
        try:
            width, height = (int(value) for value in self.size.get().split("x"))
            style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
            ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            outer_width, outer_height = self._outer_size_for_client(width, height, style, ex_style)
            left, top, _, _ = win32gui.GetWindowRect(hwnd)
            win32gui.SetWindowPos(hwnd, 0, left, top, outer_width, outer_height,
                                  win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)
            self._log_size(width, height)
        except Exception as exc:
            messagebox.showerror("Không thể đổi kích thước", str(exc), parent=self)

    def _log_size(self, width, height):
        self.info.set(f"Đã đổi vùng client game thành {width}×{height} (tỷ lệ 16:9)")

    @staticmethod
    def _outer_size_for_client(width, height, style, ex_style):
        """Use user32 directly; pywin32 does not expose AdjustWindowRectEx everywhere."""
        import ctypes
        from ctypes import wintypes
        rect = wintypes.RECT(0, 0, width, height)
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.AdjustWindowRectEx.argtypes = [
            ctypes.POINTER(wintypes.RECT), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD
        ]
        user32.AdjustWindowRectEx.restype = wintypes.BOOL
        if not user32.AdjustWindowRectEx(ctypes.byref(rect), style, False, ex_style):
            raise ctypes.WinError(ctypes.get_last_error())
        return rect.right - rect.left, rect.bottom - rect.top

    def _show_image(self, source_label):
        from PIL import ImageTk
        self.scale = min(1.0, 900 / self.image.width, 600 / self.image.height)
        preview = self.image.resize((int(self.image.width * self.scale), int(self.image.height * self.scale)))
        self._image_tk = ImageTk.PhotoImage(preview)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self._image_tk, anchor="nw")
        self.canvas.config(scrollregion=(0, 0, preview.width, preview.height))
        self.source_name.set(f"Nguồn: {source_label} | client {self.image.width}×{self.image.height}")
        self.info.set("Kéo-thả để chọn vùng crop")

    def begin_crop(self, event):
        if self.image is None:
            return
        self.start = (event.x, event.y)
        if self.rect:
            self.canvas.delete(self.rect)
        self.rect = self.canvas.create_rectangle(event.x, event.y, event.x, event.y, outline=self.ACCENT, width=2)

    def move_crop(self, event):
        if self.start and self.rect:
            self.canvas.coords(self.rect, self.start[0], self.start[1], event.x, event.y)

    def end_crop(self, event):
        if not self.start or self.image is None:
            return
        x1, y1 = self.start
        x2, y2 = event.x, event.y
        left, top = max(0, min(x1, x2)), max(0, min(y1, y2))
        right, bottom = min(self.image.width * self.scale, max(x1, x2)), min(self.image.height * self.scale, max(y1, y2))
        box = CropBox(round(left / self.scale), round(top / self.scale), round((right - left) / self.scale), round((bottom - top) / self.scale))
        self.current_box = box
        self.info.set(f"Box client: x={box.x}, y={box.y}, w={box.width}, h={box.height}")

    def copy_box_client(self):
        if self.current_box is None:
            messagebox.showwarning("Chưa có box", "Hãy kéo chọn vùng crop trước.", parent=self)
            return
        box = self.current_box
        text = f"x={box.x}, y={box.y}, w={box.width}, h={box.height}"
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()
        self.info.set(f"Đã copy box client: {text}")

    def save_crop(self):
        if self.image is None or not self.rect:
            messagebox.showwarning("Chưa có vùng crop", "Hãy mở ảnh và kéo chọn vùng crop trước.", parent=self)
            return
        x1, y1, x2, y2 = self.canvas.coords(self.rect)
        box = CropBox(round(min(x1, x2) / self.scale), round(min(y1, y2) / self.scale), round(abs(x2 - x1) / self.scale), round(abs(y2 - y1) / self.scale))
        self.current_box = box
        if box.width < 2 or box.height < 2:
            messagebox.showwarning("Vùng crop quá nhỏ", "Hãy kéo một vùng lớn hơn.", parent=self)
            return
        out_dir = Path("assets")
        out_dir.mkdir(exist_ok=True)
        path = out_dir / asset_filename(self.label.get(), box)
        self.image.crop((box.x, box.y, box.x + box.width, box.y + box.height)).save(path)
        path.with_suffix(".json").write_text(json.dumps(metadata_for(self.label.get(), box, self.image.size, "screenshot"), ensure_ascii=False, indent=2), encoding="utf-8")
        self.info.set(f"Đã lưu: {path}")


if __name__ == "__main__":
    CropperApp().mainloop()
