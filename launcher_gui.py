"""Dark gaming dashboard for TMLH Bot."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
import inspect
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

from app_settings import AppSettings, AppSettingsStorage
from automation_constants import RESOLUTIONS
from boss.enter_boss import BOSSES
from game_automation import AutomationWorker
from profile_auth import save_profile_auth
from profile_manager import MissingGameFilesError, ProfileManager
from profile_models import ProfileRuntimeContext
from profile_storage import ProfileStorage
from ui_components import ProfileRow, SectionCard, SummaryChip
from ui_theme import APP_NAME, APP_VERSION, BOSS_KEYS, BOSS_LABELS, COLORS
from window_layout import arrange_windows
from window_manager import (
    PROFILE_LAUNCH_LOCK,
    acquire_profile_window,
    resize_client,
    set_window_topmost,
)

SIZES = tuple(f"{w}x{h}" for w, h in RESOLUTIONS)


def get_app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


class ProfileController:
    """Business/controller layer retained independently from the visual dashboard."""

    def __init__(
        self,
        bot_root,
        profile_store=None,
        settings_store=None,
        worker_factory=AutomationWorker,
        auth_saver=None,
    ):
        self.manager = ProfileManager(Path(bot_root))
        self.profile_store = profile_store or ProfileStorage(Path(bot_root) / "profiles.json")
        self.settings_store = settings_store or AppSettingsStorage(Path(bot_root) / "settings.json")
        self.profiles = self.profile_store.load()
        self.workers = {}
        self.worker_factory = worker_factory
        self._storage_lock = threading.RLock()
        self.auth_saver = auth_saver or save_profile_auth

    def get(self, profile_id):
        with self._storage_lock:
            return next(profile for profile in self.profiles if profile.profile_id == profile_id)

    def _replace(self, updated):
        with self._storage_lock:
            self.profiles = [
                updated if profile.profile_id == updated.profile_id else profile
                for profile in self.profiles
            ]
            self.profile_store.save(self.profiles)
        return updated

    def prepare_profile(self, name, source_path):
        with self._storage_lock:
            profile = self.manager.prepare_profile(name, Path(source_path), self.profiles)
            self.profiles.append(profile)
            self.profile_store.save(self.profiles)
        return profile

    def confirm_login(self, profile_id):
        profile = self.get(profile_id)
        self.manager.check_clone(profile)
        self.auth_saver(profile.game_path)
        return self._replace(replace(profile, login_ready=True))

    def repair_profile(self, profile_id, source_path):
        profile = self.get(profile_id)
        self._replace(replace(profile, login_ready=False))
        self.manager.repair_profile(profile, Path(source_path))
        return self.get(profile_id)

    def set_options(self, profile_id, boss, size):
        if boss not in BOSSES or size not in SIZES:
            raise ValueError("Invalid boss or resolution")

        profile = self.get(profile_id)
        width, height = (int(part) for part in size.split("x"))
        updated = self._replace(
            replace(
                profile,
                selected_boss=boss,
                window_width=width,
                window_height=height,
            )
        )

        worker = self.workers.get(profile_id)
        if worker is not None:
            context = worker.context
            context.selected_boss = boss
            context.window_width = width
            context.window_height = height
            hwnd = context.window_handle

            if (
                hwnd
                and not context.stop_event.is_set()
                and context.state not in ("STOPPED", "ERROR")
            ):
                try:
                    resize_client(hwnd, width, height)
                    set_window_topmost(hwnd, True)
                except (ValueError, OSError):
                    pass

        return updated

    def _make_worker(self, context, on_status, on_error, on_log):
        kwargs = {
            "on_status": on_status,
            "on_error": on_error,
        }

        # Keeps lightweight test fakes/backward-compatible worker factories working.
        try:
            parameters = inspect.signature(self.worker_factory).parameters
            if "on_log" in parameters:
                kwargs["on_log"] = on_log
        except (TypeError, ValueError):
            kwargs["on_log"] = on_log

        return self.worker_factory(context, **kwargs)

    def start_selected(
        self,
        profile_ids,
        choices=None,
        on_status=None,
        on_error=None,
        on_log=None,
    ):
        started = []

        for profile_id in profile_ids:
            try:
                profile = self.get(profile_id)
                old_worker = self.workers.get(profile_id)

                if old_worker is not None:
                    old_thread = getattr(old_worker, "thread", None)

                    if old_thread is not None and old_thread.is_alive():
                        if old_worker.context.stop_event.is_set():
                            old_thread.join(timeout=3.0)

                        if old_thread.is_alive():
                            raise RuntimeError(
                                f"{profile.profile_name}: worker cũ vẫn đang dừng, "
                                "không thể Start worker mới"
                            )

                    self.workers.pop(profile_id, None)

                if choices and profile_id in choices:
                    profile = self.set_options(profile_id, *choices[profile_id])

                if not profile.login_ready:
                    raise ValueError(f"{profile.profile_name}: login confirmation required")

                self.manager.check_clone(profile)
                context = ProfileRuntimeContext.from_profile(profile)

                worker = self._make_worker(
                    context,
                    (
                        lambda state, pid=profile_id: on_status(pid, state)
                        if on_status
                        else None
                    ),
                    (
                        lambda exc, pid=profile_id: on_error(pid, exc)
                        if on_error
                        else None
                    ),
                    (
                        lambda message, pid=profile_id: on_log(pid, message)
                        if on_log
                        else None
                    ),
                )

                self.workers[profile_id] = worker
                worker.start()
                started.append(profile_id)

            except Exception as exc:
                if on_error:
                    on_error(profile_id, exc)
                else:
                    raise

        return started

    def stop_selected(self, profile_ids):
        for profile_id in profile_ids:
            if profile_id in self.workers:
                self.workers[profile_id].stop()


class LauncherApp(ctk.CTk):
    """9:16 dark-gaming dashboard while preserving the existing automation backend."""

    def __init__(self, controller=None):
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")
        super().__init__()

        root = get_app_root()
        self.controller = controller or ProfileController(root)
        settings = self.controller.settings_store.load()

        self.source = tk.StringVar(value=settings.game_source_path)
        self.profile_name = tk.StringVar()
        self.search_text = tk.StringVar()
        self.statuses = {}
        self.checked = set()
        self.creation_events = {}
        self.profile_rows = {}
        self.selected_profile_id = None

        self.title(f"{APP_NAME} - Quản lý profile & săn boss")
        self.configure(fg_color=COLORS["bg"])
        self.geometry("920x1540")
        self.minsize(820, 900)

        self._build_ui()
        self._refresh()
        self._update_source_status()
        self.protocol("WM_DELETE_WINDOW", self._close)

    # ------------------------------------------------------------------
    # UI BUILD
    # ------------------------------------------------------------------

    def _build_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()

        self.main = ctk.CTkScrollableFrame(
            self,
            fg_color=COLORS["bg"],
            corner_radius=0,
            scrollbar_button_color=COLORS["surface_soft"],
            scrollbar_button_hover_color=COLORS["border_bright"],
        )
        self.main.grid(row=0, column=1, sticky="nsew")
        self.main.grid_columnconfigure(0, weight=1)

        self._build_header()
        self._build_source_card()
        self._build_create_card()
        self._build_profiles_card()
        self._build_action_bar()
        self._build_summary()
        self._build_log()
        self._build_footer()

    def _build_sidebar(self):
        sidebar = ctk.CTkFrame(
            self,
            width=152,
            corner_radius=0,
            fg_color=COLORS["sidebar"],
            border_width=0,
        )
        sidebar.grid(row=0, column=0, sticky="nsw")
        sidebar.grid_propagate(False)
        sidebar.grid_rowconfigure(8, weight=1)

        logo = ctk.CTkLabel(
            sidebar,
            text="◈",
            text_color=COLORS["cyan"],
            font=ctk.CTkFont(size=34, weight="bold"),
        )
        logo.grid(row=0, column=0, pady=(28, 2))

        ctk.CTkLabel(
            sidebar,
            text="TMLH",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=20, weight="bold"),
        ).grid(row=1, column=0, pady=(0, 22))

        items = [
            ("⌂", "Tổng quan"),
            ("▣", "Quản lý profile"),
            ("⚙", "Cài đặt"),
            ("⌘", "Công cụ"),
            ("≡", "Nhật ký"),
            ("?", "Hỗ trợ"),
        ]

        for index, (icon, label) in enumerate(items, start=2):
            active = index == 2
            button = ctk.CTkButton(
                sidebar,
                text=f"{icon}  {label}",
                height=42,
                width=132,
                anchor="w",
                corner_radius=10,
                fg_color=COLORS["surface_soft"] if active else "transparent",
                hover_color=COLORS["surface_soft"],
                border_width=1 if active else 0,
                border_color=COLORS["blue"],
                text_color=COLORS["text"] if active else COLORS["muted"],
                font=ctk.CTkFont(size=12, weight="bold" if active else "normal"),
                command=lambda name=label: self._sidebar_action(name),
            )
            button.grid(row=index, column=0, padx=10, pady=4)

        slogan = ctk.CTkLabel(
            sidebar,
            text="PLAY\nAUTOMATE\nCONQUER",
            justify="left",
            anchor="sw",
            text_color=COLORS["muted_dark"],
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        slogan.grid(row=8, column=0, sticky="sw", padx=24, pady=(20, 34))

    def _build_header(self):
        header = ctk.CTkFrame(self.main, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 8))
        header.grid_columnconfigure(0, weight=1)

        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            title_box,
            text=APP_NAME,
            text_color=COLORS["cyan"],
            font=ctk.CTkFont(size=32, weight="bold"),
            anchor="w",
        ).pack(anchor="w")

        ctk.CTkLabel(
            title_box,
            text="Quản lý profile & săn boss",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=13),
            anchor="w",
        ).pack(anchor="w", pady=(2, 0))

        right = ctk.CTkFrame(header, fg_color="transparent")
        right.grid(row=0, column=1, sticky="e")

        ctk.CTkLabel(
            right,
            text="GAME • AUTOMATE • CONQUER",
            text_color=COLORS["muted_dark"],
            font=ctk.CTkFont(size=10, weight="bold"),
        ).pack(anchor="e")

        ctk.CTkLabel(
            right,
            text=APP_VERSION,
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=11),
        ).pack(anchor="e", pady=(4, 0))

    def _build_source_card(self):
        card = SectionCard(self.main, "▣  Thư mục game gốc", "Nguồn dùng để tạo profile")
        card.grid(row=1, column=0, sticky="ew", padx=18, pady=8)

        body = card.body
        body.grid_columnconfigure(0, weight=1)

        self.source_entry = ctk.CTkEntry(
            body,
            textvariable=self.source,
            height=38,
            fg_color=COLORS["input"],
            border_color=COLORS["border_bright"],
            text_color=COLORS["text"],
            placeholder_text="D:\\Game\\TMLH",
        )
        self.source_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10))

        ctk.CTkButton(
            body,
            text="▣  Chọn thư mục",
            height=38,
            width=138,
            corner_radius=9,
            fg_color=COLORS["blue"],
            hover_color=COLORS["blue_hover"],
            command=self._browse,
        ).grid(row=0, column=1)

        self.source_status = ctk.CTkLabel(
            body,
            text="● Chưa chọn thư mục game",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=11, weight="bold"),
            anchor="w",
        )
        self.source_status.grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 0))

        self.source_entry.bind("<FocusOut>", lambda _event: self._update_source_status())

    def _build_create_card(self):
        card = SectionCard(self.main, "✦  Tạo & xác nhận profile", "Thiết lập account theo từng clone game")
        card.grid(row=2, column=0, sticky="ew", padx=18, pady=8)

        body = card.body
        body.grid_columnconfigure(0, weight=1)

        self.profile_entry = ctk.CTkEntry(
            body,
            textvariable=self.profile_name,
            height=40,
            fg_color=COLORS["input"],
            border_color=COLORS["border_bright"],
            placeholder_text="Tên profile mới...",
            text_color=COLORS["text"],
        )
        self.profile_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10))

        ctk.CTkButton(
            body,
            text="+  Tạo profile",
            height=40,
            width=128,
            fg_color=COLORS["purple"],
            hover_color=COLORS["purple_hover"],
            command=self._create,
        ).grid(row=0, column=1, padx=4)

        ctk.CTkButton(
            body,
            text="▶  Mở game",
            height=40,
            width=118,
            fg_color=COLORS["blue"],
            hover_color=COLORS["blue_hover"],
            command=self._continue_login,
        ).grid(row=0, column=2, padx=4)

        ctk.CTkButton(
            body,
            text="✓  Đã đăng nhập",
            height=40,
            width=138,
            fg_color=COLORS["surface_soft"],
            hover_color=COLORS["border_bright"],
            border_width=1,
            border_color=COLORS["border_bright"],
            command=self._confirm_login,
        ).grid(row=0, column=3, padx=(4, 0))

        ctk.CTkLabel(
            body,
            text="Mẹo: chọn một profile trong danh sách trước khi dùng “Mở game” hoặc “Đã đăng nhập”.",
            text_color=COLORS["muted_dark"],
            font=ctk.CTkFont(size=10),
            anchor="w",
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(9, 0))

    def _build_profiles_card(self):
        self.profiles_card = SectionCard(self.main, "◫  Danh sách profile", "0 profile")
        self.profiles_card.grid(row=3, column=0, sticky="nsew", padx=18, pady=8)

        body = self.profiles_card.body
        body.grid_columnconfigure(0, weight=1)

        tools = ctk.CTkFrame(body, fg_color="transparent")
        tools.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        tools.grid_columnconfigure(0, weight=1)

        self.search_entry = ctk.CTkEntry(
            tools,
            textvariable=self.search_text,
            height=34,
            width=250,
            fg_color=COLORS["input"],
            border_color=COLORS["border"],
            placeholder_text="Tìm kiếm profile...",
        )
        self.search_entry.grid(row=0, column=0, sticky="e", padx=(0, 8))
        self.search_entry.bind("<KeyRelease>", lambda _event: self._refresh())

        ctk.CTkButton(
            tools,
            text="↻",
            width=36,
            height=34,
            fg_color=COLORS["surface_soft"],
            hover_color=COLORS["border_bright"],
            command=self._refresh,
        ).grid(row=0, column=1)

        header = ctk.CTkFrame(
            body,
            fg_color=COLORS["surface_soft"],
            corner_radius=9,
            height=38,
        )
        header.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        header.grid_propagate(False)

        headers = [
            ("", 38),
            ("Tên profile", 155),
            ("Trạng thái", 125),
            ("Boss", 135),
            ("Kích thước", 118),
            ("Thao tác", 170),
        ]

        for col, (label, width) in enumerate(headers):
            header.grid_columnconfigure(col, minsize=width, weight=1 if col == 1 else 0)
            ctk.CTkLabel(
                header,
                text=label,
                text_color=COLORS["muted"],
                font=ctk.CTkFont(size=10, weight="bold"),
                anchor="w",
            ).grid(row=0, column=col, sticky="ew", padx=6, pady=10)

        self.rows_frame = ctk.CTkFrame(body, fg_color="transparent")
        self.rows_frame.grid(row=2, column=0, sticky="ew")
        self.rows_frame.grid_columnconfigure(0, weight=1)

    def _build_action_bar(self):
        actions = ctk.CTkFrame(self.main, fg_color="transparent")
        actions.grid(row=4, column=0, sticky="ew", padx=18, pady=(10, 8))

        for col in range(3):
            actions.grid_columnconfigure(col, weight=1)

        ctk.CTkButton(
            actions,
            text="▶  START\nBắt đầu profile đã chọn",
            height=72,
            corner_radius=12,
            fg_color=COLORS["green"],
            hover_color=COLORS["green_hover"],
            text_color="#041B13",
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._start_selected,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 6))

        ctk.CTkButton(
            actions,
            text="■  STOP\nDừng profile đã chọn",
            height=72,
            corner_radius=12,
            fg_color=COLORS["red"],
            hover_color=COLORS["red_hover"],
            text_color="#260308",
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._stop_selected,
        ).grid(row=0, column=1, sticky="ew", padx=6)

        ctk.CTkButton(
            actions,
            text="▣  SẮP XẾP CỬA SỔ\nTự động arrange game",
            height=72,
            corner_radius=12,
            fg_color=COLORS["purple"],
            hover_color=COLORS["purple_hover"],
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._arrange_windows,
        ).grid(row=0, column=2, sticky="ew", padx=(6, 0))

    def _build_summary(self):
        card = SectionCard(self.main, "▥  Trạng thái tổng quan")
        card.grid(row=5, column=0, sticky="ew", padx=18, pady=8)

        body = card.body
        for col in range(5):
            body.grid_columnconfigure(col, weight=1)

        self.summary_running = SummaryChip(body, "Đang chạy", "0", COLORS["green"])
        self.summary_ready = SummaryChip(body, "Ready", "0", COLORS["blue"])
        self.summary_ingame = SummaryChip(body, "In Game", "0", COLORS["green"])
        self.summary_waiting = SummaryChip(body, "Waiting", "0", COLORS["amber"])
        self.summary_error = SummaryChip(body, "Error", "0", COLORS["red"])

        for col, widget in enumerate(
            (
                self.summary_running,
                self.summary_ready,
                self.summary_ingame,
                self.summary_waiting,
                self.summary_error,
            )
        ):
            widget.grid(row=0, column=col, sticky="ew", padx=4)

    def _build_log(self):
        card = SectionCard(self.main, "≡  Nhật ký", "Realtime automation log")
        card.grid(row=6, column=0, sticky="ew", padx=18, pady=8)

        body = card.body
        body.grid_columnconfigure(0, weight=1)

        log_toolbar = ctk.CTkFrame(body, fg_color="transparent")
        log_toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        log_toolbar.grid_columnconfigure(0, weight=1)

        ctk.CTkButton(
            log_toolbar,
            text="Xóa nhật ký",
            width=106,
            height=30,
            fg_color=COLORS["surface_soft"],
            hover_color=COLORS["border_bright"],
            command=self._clear_log,
        ).grid(row=0, column=1, sticky="e")

        self.log = ctk.CTkTextbox(
            body,
            height=250,
            fg_color=COLORS["black"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=10,
            text_color=COLORS["muted"],
            font=("Consolas", 11),
            wrap="word",
        )
        self.log.grid(row=1, column=0, sticky="ew")
        self.log.configure(state="disabled")

        # Native text tags underneath CTkTextbox provide useful log colors.
        try:
            self.log._textbox.tag_configure("info", foreground=COLORS["blue"])
            self.log._textbox.tag_configure("success", foreground=COLORS["green"])
            self.log._textbox.tag_configure("warning", foreground=COLORS["amber"])
            self.log._textbox.tag_configure("error", foreground=COLORS["red"])
        except Exception:
            pass

    def _build_footer(self):
        footer = ctk.CTkFrame(
            self.main,
            fg_color=COLORS["sidebar"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=10,
        )
        footer.grid(row=7, column=0, sticky="ew", padx=18, pady=(8, 22))
        footer.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            footer,
            text="●  Kết nối OK",
            text_color=COLORS["green"],
            font=ctk.CTkFont(size=10, weight="bold"),
        ).grid(row=0, column=0, padx=14, pady=10, sticky="w")

        ctk.CTkLabel(
            footer,
            text=f"{APP_NAME}  •  Made for Gamers",
            text_color=COLORS["muted_dark"],
            font=ctk.CTkFont(size=10),
        ).grid(row=0, column=1)

        ctk.CTkLabel(
            footer,
            text="Boss luôn chờ.",
            text_color=COLORS["muted_dark"],
            font=ctk.CTkFont(size=10),
        ).grid(row=0, column=2, padx=14, sticky="e")

    # ------------------------------------------------------------------
    # UI HELPERS
    # ------------------------------------------------------------------

    def _sidebar_action(self, name):
        if name == "Quản lý profile":
            self.search_entry.focus_set()
        elif name == "Nhật ký":
            self.log.focus_set()
        elif name in {"Cài đặt", "Công cụ", "Hỗ trợ"}:
            self._log("App", "INFO", f"{name}: chức năng sẽ được mở rộng ở phiên bản sau")

    def _update_source_status(self):
        value = self.source.get().strip()
        launcher = Path(value) / "ThienMenhLacHong_Launcher.exe" if value else None

        if launcher and launcher.is_file():
            self.source_status.configure(
                text="● Đã tìm thấy ThienMenhLacHong_Launcher.exe",
                text_color=COLORS["green"],
            )
        elif value:
            self.source_status.configure(
                text="● Chưa tìm thấy launcher trong thư mục này",
                text_color=COLORS["red"],
            )
        else:
            self.source_status.configure(
                text="● Chưa chọn thư mục game",
                text_color=COLORS["muted"],
            )

    def _selected_profile(self):
        if not self.selected_profile_id:
            raise ValueError("Hãy chọn một profile trong danh sách")
        return self.selected_profile_id

    def _focus_profile(self, profile_id):
        self.selected_profile_id = profile_id
        self._refresh()

    def _toggle_profile(self, profile_id, enabled):
        if enabled:
            self.checked.add(profile_id)
            self.selected_profile_id = profile_id
        else:
            self.checked.discard(profile_id)
        self._refresh()

    def _change_profile_options(self, profile_id, boss_label, size):
        try:
            boss_key = BOSS_KEYS.get(boss_label, boss_label)
            profile = self.controller.set_options(profile_id, boss_key, size)
            self.selected_profile_id = profile_id
            self._log(
                profile.profile_name,
                "OPTIONS",
                f"Boss={BOSS_LABELS.get(profile.selected_boss, profile.selected_boss)}, Size={size}",
            )
            self._arrange_windows()
        except (ValueError, OSError) as exc:
            messagebox.showerror("Profile options", str(exc), parent=self)
            self._refresh()

    def _runtime_status(self, profile):
        runtime = self.statuses.get(profile.profile_id)

        if runtime:
            return runtime

        try:
            self.controller.manager.check_clone(profile)
            return "Ready" if profile.login_ready else "Waiting Login"
        except MissingGameFilesError:
            return "Error / Missing Files"

    def _refresh(self):
        if not hasattr(self, "rows_frame"):
            return

        for child in self.rows_frame.winfo_children():
            child.destroy()
        self.profile_rows.clear()

        query = self.search_text.get().strip().casefold()
        visible = [
            profile
            for profile in self.controller.profiles
            if not query or query in profile.profile_name.casefold()
        ]

        self.profiles_card.body.master.master  # keep card alive for frozen builds
        try:
            header = self.profiles_card.winfo_children()[0]
            # subtitle lives in header child 1 when present
            labels = header.winfo_children()
            if len(labels) > 1:
                labels[1].configure(text=f"{len(self.controller.profiles)} profile")
        except Exception:
            pass

        boss_values = tuple(BOSS_LABELS.get(key, key) for key in BOSSES)

        for row_index, profile in enumerate(visible):
            status = self._runtime_status(profile)
            selected = profile.profile_id in self.checked

            row = ProfileRow(
                self.rows_frame,
                profile,
                status,
                selected=selected,
                boss_values=boss_values,
                size_values=SIZES,
                on_toggle=self._toggle_profile,
                on_focus=self._focus_profile,
                on_change=self._change_profile_options,
                on_start=self._start_single,
                on_stop=self._stop_single,
                on_login=self._continue_login_for,
                on_repair=self._repair_for,
            )
            row.grid(row=row_index, column=0, sticky="ew", pady=4)
            self.profile_rows[profile.profile_id] = row

            if self.selected_profile_id == profile.profile_id:
                row.configure(border_color=COLORS["cyan"], border_width=2)

        if not visible:
            ctk.CTkLabel(
                self.rows_frame,
                text="Chưa có profile phù hợp.",
                text_color=COLORS["muted"],
                font=ctk.CTkFont(size=12),
            ).grid(row=0, column=0, pady=24)

        self._update_summary()

    def _update_summary(self):
        statuses = {
            profile.profile_id: self._runtime_status(profile).casefold()
            for profile in self.controller.profiles
        }

        running = sum(
            1
            for profile_id, worker in self.controller.workers.items()
            if not worker.context.stop_event.is_set()
            and worker.context.state not in ("STOPPED", "ERROR")
        )
        ready = sum(1 for value in statuses.values() if value == "ready")
        ingame = sum(
            1
            for value in statuses.values()
            if value in {"in game", "boss alive", "checking boss"}
        )
        waiting = sum(1 for value in statuses.values() if "waiting" in value)
        errors = sum(1 for value in statuses.values() if "error" in value or "missing" in value)

        self.summary_running.set_value(running)
        self.summary_ready.set_value(ready)
        self.summary_ingame.set_value(ingame)
        self.summary_waiting.set_value(waiting)
        self.summary_error.set_value(errors)

    def _log(self, profile, state, message=""):
        if not hasattr(self, "log"):
            return

        state_text = str(state).upper()
        line = f"[{datetime.now():%H:%M:%S}] [{profile}] [{state_text}]"
        if message:
            line += f"  {message}"
        line += "\n"

        tag = "info"
        if "ERROR" in state_text:
            tag = "error"
        elif "WARN" in state_text:
            tag = "warning"
        elif state_text in {"SUCCESS", "READY", "IN_GAME", "BOSS_ALIVE"}:
            tag = "success"

        self.log.configure(state="normal")
        try:
            self.log._textbox.insert("end", line, tag)
        except Exception:
            self.log.insert("end", line)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    # ------------------------------------------------------------------
    # PROFILE CREATION / LOGIN
    # ------------------------------------------------------------------

    def _browse(self):
        selected = filedialog.askdirectory(parent=self)
        if selected:
            self.source.set(selected)
            self.controller.settings_store.save(AppSettings(selected))
            self._update_source_status()

    def _create(self):
        try:
            source = self.source.get().strip()
            profile = self.controller.prepare_profile(self.profile_name.get(), source)
            self.controller.settings_store.save(AppSettings(source))
            self.profile_name.set("")
            self.statuses[profile.profile_id] = "Creating"
            self.selected_profile_id = profile.profile_id
            self._refresh()
            self._run_creation(profile, source, repair=False)
        except (ValueError, OSError) as exc:
            messagebox.showerror("Tạo profile", str(exc), parent=self)

    def _run_creation(self, profile, source, repair):
        cancel = threading.Event()
        self.creation_events[profile.profile_id] = cancel

        def work():
            try:
                self.after(0, self._status, profile.profile_id, "Copying Game")

                if repair:
                    self.controller.repair_profile(profile.profile_id, source)
                else:
                    self.controller.manager.clone_profile(profile, source)

                if cancel.is_set():
                    return

                self.after(0, self._status, profile.profile_id, "Launching Game")
                _pid, _hwnd, launched = acquire_profile_window(profile.game_path, cancel)
                set_window_topmost(_hwnd, True)

                if cancel.is_set():
                    return

                if launched:
                    self.after(0, self._status, profile.profile_id, "Waiting Startup")
                    if cancel.wait(10):
                        return

                self.after(0, self._status, profile.profile_id, "Waiting Login")

            except Exception as exc:
                self.after(0, self._error, profile.profile_id, exc)

        threading.Thread(
            target=work,
            daemon=True,
            name=f"create-{profile.profile_id}",
        ).start()

    def _continue_login(self):
        try:
            self._continue_login_for(self._selected_profile())
        except (ValueError, OSError) as exc:
            messagebox.showerror("Mở game", str(exc), parent=self)

    def _continue_login_for(self, profile_id):
        try:
            profile = self.controller.get(profile_id)
            self.selected_profile_id = profile_id
            self.controller.manager.check_clone(profile)
            self._open_for_login(profile)
            self._refresh()
        except (ValueError, OSError) as exc:
            messagebox.showerror("Mở game", str(exc), parent=self)

    def _open_for_login(self, profile):
        cancel = threading.Event()
        self.creation_events[profile.profile_id] = cancel

        def work():
            try:
                self.after(0, self._status, profile.profile_id, "Launching Game")

                with PROFILE_LAUNCH_LOCK:
                    _pid, _hwnd, launched = acquire_profile_window(
                        profile.game_path,
                        cancel,
                    )
                    set_window_topmost(_hwnd, True)

                    if launched:
                        self.after(0, self._status, profile.profile_id, "Waiting Startup")
                        if cancel.wait(10):
                            return

                if not cancel.is_set():
                    self.after(0, self._status, profile.profile_id, "Waiting Login")

            except Exception as exc:
                self.after(0, self._error, profile.profile_id, exc)

        threading.Thread(
            target=work,
            daemon=True,
            name=f"login-{profile.profile_id}",
        ).start()

    def _confirm_login(self):
        try:
            profile_id = self._selected_profile()

            if self.statuses.get(profile_id) != "Waiting Login":
                raise ValueError(
                    "Hãy mở profile, chờ game khởi động xong rồi mới xác nhận đăng nhập"
                )

            profile = self.controller.confirm_login(profile_id)
            self._status(profile_id, "Ready")
            self._log(profile.profile_name, "SUCCESS", "Đã lưu auth riêng cho profile")

        except (ValueError, OSError) as exc:
            messagebox.showerror("Xác nhận đăng nhập", str(exc), parent=self)

    # ------------------------------------------------------------------
    # AUTOMATION ACTIONS
    # ------------------------------------------------------------------

    def _worker_callbacks(self):
        return {
            "on_status": lambda pid, state: self.after(
                0,
                self._worker_status,
                pid,
                state,
            ),
            "on_error": lambda pid, exc: self.after(
                0,
                self._worker_error,
                pid,
                exc,
            ),
            "on_log": lambda pid, message: self.after(
                0,
                self._worker_log,
                pid,
                message,
            ),
        }

    def _start_profile_ids(self, profile_ids):
        callbacks = self._worker_callbacks()
        self.controller.start_selected(profile_ids, **callbacks)
        self._refresh()

    def _start_selected(self):
        if not self.checked:
            self._log("App", "WARN", "Hãy chọn ít nhất một profile")
            return

        profile_ids = tuple(
            profile.profile_id
            for profile in self.controller.profiles
            if profile.profile_id in self.checked
        )

        try:
            self._start_profile_ids(profile_ids)
        except (ValueError, OSError) as exc:
            messagebox.showerror("Start profiles", str(exc), parent=self)

    def _start_single(self, profile_id):
        try:
            self.checked.add(profile_id)
            self.selected_profile_id = profile_id
            self._start_profile_ids((profile_id,))
        except (ValueError, OSError) as exc:
            messagebox.showerror("Start profile", str(exc), parent=self)

    def _stop_selected(self):
        if not self.checked:
            self._log("App", "WARN", "Không có profile nào được chọn")
            return
        self._stop_profile_ids(tuple(self.checked))

    def _stop_single(self, profile_id):
        self._stop_profile_ids((profile_id,))

    def _stop_profile_ids(self, profile_ids):
        self.controller.stop_selected(profile_ids)

        for profile_id in profile_ids:
            worker = self.controller.workers.get(profile_id)

            if worker is None:
                continue

            hwnd = worker.context.window_handle

            if hwnd:
                try:
                    set_window_topmost(hwnd, False)
                except (ValueError, OSError):
                    pass

            self.statuses[profile_id] = "Stopping"

        self._refresh()

    def _worker_log(self, profile_id, message):
        self._log(profile_id, "OCR", message)

    def _worker_status(self, profile_id, state):
        pretty = state.replace("_", " ").title()
        self.statuses[profile_id] = pretty
        self._log(profile_id, state)
        self._refresh()

        if state in (
            "WAITING_STARTUP",
            "WAITING_GAME",
            "CONFIRMING_IN_GAME",
        ):
            self._arrange_windows()

    def _worker_error(self, profile_id, exc):
        self.statuses[profile_id] = "Error"
        self._log(profile_id, "ERROR", str(exc))
        self._refresh()

    # ------------------------------------------------------------------
    # REPAIR / WINDOW LAYOUT
    # ------------------------------------------------------------------

    def _repair(self):
        try:
            self._repair_for(self._selected_profile())
        except (ValueError, OSError) as exc:
            messagebox.showerror("Repair profile", str(exc), parent=self)

    def _repair_for(self, profile_id):
        try:
            profile = self.controller.get(profile_id)
            source = self.source.get().strip()

            if not source:
                raise ValueError("Hãy chọn Game Source Path hợp lệ")

            if (
                profile_id in self.controller.workers
                and not self.controller.workers[profile_id].context.stop_event.is_set()
            ):
                raise ValueError("Hãy dừng profile trước khi Repair")

            self.selected_profile_id = profile_id
            self._run_creation(profile, source, repair=True)

        except (ValueError, OSError) as exc:
            messagebox.showerror("Repair profile", str(exc), parent=self)

    def _arrange_windows(self):
        import win32api
        import win32con
        import win32gui

        items = []

        for worker in self.controller.workers.values():
            context = worker.context
            hwnd = context.window_handle

            if not hwnd or not win32gui.IsWindow(hwnd):
                continue

            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            items.append((hwnd, right - left, bottom - top))

        if not items:
            return

        monitor = win32api.MonitorFromWindow(
            items[0][0],
            win32con.MONITOR_DEFAULTTONEAREST,
        )

        left, top, right, bottom = win32api.GetMonitorInfo(monitor)["Work"]
        placements = arrange_windows(
            items,
            (left, top, right - left, bottom - top),
        )

        for place in placements:
            if not win32gui.IsWindow(place.hwnd):
                continue

            win32gui.SetWindowPos(
                place.hwnd,
                win32con.HWND_TOPMOST,
                place.x,
                place.y,
                0,
                0,
                win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE,
            )

        if any(
            place.overlap
            or place.y + place.height > bottom
            or place.x + place.width > right
            for place in placements
        ):
            self._log(
                "App",
                "WARN",
                "Không đủ diện tích màn hình để xếp tất cả cửa sổ không chồng lấn",
            )

    # ------------------------------------------------------------------
    # STATE / CLOSE
    # ------------------------------------------------------------------

    def _status(self, profile_id, state):
        self.statuses[profile_id] = state
        self._log(profile_id, state)
        self._refresh()

    def _error(self, profile_id, exc):
        self.statuses[profile_id] = "Error"
        self._log(profile_id, "ERROR", str(exc))
        self._refresh()

    def _close(self):
        for event in self.creation_events.values():
            event.set()

        self.controller.stop_selected(tuple(self.controller.workers))

        for worker in self.controller.workers.values():
            hwnd = worker.context.window_handle
            if hwnd:
                try:
                    set_window_topmost(hwnd, False)
                except (ValueError, OSError):
                    pass

        self.destroy()


if __name__ == "__main__":
    LauncherApp().mainloop()
