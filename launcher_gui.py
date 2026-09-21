"""Compact dark-gaming dashboard for TMLH Bot."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
import inspect
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog

import customtkinter as ctk

from app_settings import AppSettings, AppSettingsStorage
from auto_login import AutoLoginRunner
from automation_constants import RESOLUTIONS
from boss.enter_boss import BOSSES
from game_automation import AutomationWorker
from profile_auth import (
    clear_current_auth_values,
    save_profile_auth,
)
from profile_credentials import (
    LoginCredentials,
    SERVER_KEYS,
    SERVER_LABELS,
    load_login_credentials,
    save_login_credentials,
)
from profile_manager import MissingGameFilesError, ProfileManager
from profile_models import ProfileRuntimeContext
from profile_storage import ProfileStorage
from proxy_manager import (
    PROXY_SETTINGS_FILENAME,
    ProxySettingsStorage,
    apply_proxy_routing,
    routing_config_matches,
    verify_proxy_setup,
)
from proxy_ui import ProxySettingsDialog
from ui_dialogs import (
    askyesno,
    keep_above_game,
    showerror,
    showinfo,
    showwarning,
)
from ui_components import CompactCard, EditProfileDialog, ProfileRow, PROFILE_COLUMN_WIDTHS
from ui_theme import APP_NAME, APP_VERSION, BOSS_KEYS, BOSS_LABELS, COLORS
from window_layout import arrange_windows, stack_windows_for_boss
from window_manager import (
    PROFILE_LAUNCH_LOCK,
    acquire_profile_window,
    boss_scan_reveal_height,
    clear_boss_stack_order,
    resize_client,
    set_boss_stack_order,
    set_window_topmost,
)

SIZES = tuple(f"{w}x{h}" for w, h in RESOLUTIONS)

# Fast auth confirmation after the automated submit. Registry writes usually
# appear quickly; poll more often and avoid keeping the UI in a pending state
# for the previous 20 seconds.
AUTO_LOGIN_AUTH_CONFIRM_TIMEOUT = 10.0
AUTO_LOGIN_AUTH_CONFIRM_POLL = 0.2


def get_app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


class ProfileController:
    """Business layer for profile lifecycle and automation workers."""

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

    def _replace(self, old_profile_id, updated):
        with self._storage_lock:
            self.profiles = [
                updated if profile.profile_id == old_profile_id else profile
                for profile in self.profiles
            ]
            self.profile_store.save(self.profiles)
        return updated

    def _worker_is_active(self, profile_id) -> bool:
        worker = self.workers.get(profile_id)
        if worker is None:
            return False

        thread = getattr(worker, "thread", None)
        if thread is not None and hasattr(thread, "is_alive") and thread.is_alive():
            return True

        context = getattr(worker, "context", None)
        if context is None:
            return False

        if context.stop_event.is_set():
            return False

        return context.state not in ("STOPPED", "ERROR")

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
        return self._replace(
            profile_id,
            replace(profile, login_ready=True),
        )

    def repair_profile(self, profile_id, source_path):
        profile = self.get(profile_id)
        self._replace(profile_id, replace(profile, login_ready=False))
        self.manager.repair_profile(profile, Path(source_path))
        return self.get(profile_id)

    def set_options(self, profile_id, boss, size):
        if boss not in BOSSES or size not in SIZES:
            raise ValueError("Invalid boss or resolution")

        profile = self.get(profile_id)
        width, height = (int(part) for part in size.split("x"))
        updated = self._replace(
            profile_id,
            replace(
                profile,
                selected_boss=boss,
                window_width=width,
                window_height=height,
            ),
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

    def edit_profile(self, profile_id, new_name, boss, size):
        """Rename/edit a profile. Running profiles must be stopped first."""
        if self._worker_is_active(profile_id):
            raise ValueError("Stop this profile before editing it")

        if boss not in BOSSES or size not in SIZES:
            raise ValueError("Invalid boss or resolution")

        profile = self.get(profile_id)
        others = [
            item
            for item in self.profiles
            if item.profile_id != profile_id
        ]

        renamed = self.manager.rename_profile(
            profile,
            new_name,
            others,
        )
        width, height = (int(part) for part in size.split("x"))
        updated = replace(
            renamed,
            selected_boss=boss,
            window_width=width,
            window_height=height,
        )

        self._replace(profile_id, updated)

        if updated.profile_id != profile_id:
            self.workers.pop(profile_id, None)

        return updated

    def delete_profile(self, profile_id):
        """Delete a stopped profile record and only its cloned game folder."""
        if self._worker_is_active(profile_id):
            raise ValueError("Stop this profile before deleting it")

        profile = self.get(profile_id)
        self.manager.delete_profile(profile)

        with self._storage_lock:
            self.profiles = [
                item
                for item in self.profiles
                if item.profile_id != profile_id
            ]
            self.profile_store.save(self.profiles)

        self.workers.pop(profile_id, None)
        return profile

    def _make_worker(self, context, on_status, on_error, on_log):
        kwargs = {
            "on_status": on_status,
            "on_error": on_error,
        }

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
                        lambda state, pid=profile_id:
                        on_status(pid, state) if on_status else None
                    ),
                    (
                        lambda exc, pid=profile_id:
                        on_error(pid, exc) if on_error else None
                    ),
                    (
                        lambda message, pid=profile_id:
                        on_log(pid, message) if on_log else None
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
    """Compact, no-sidebar TMLH desktop dashboard."""

    def __init__(self, controller=None):
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")
        super().__init__()

        root = get_app_root()
        self.app_root = root
        self.controller = controller or ProfileController(root)
        self.proxy_settings_store = ProxySettingsStorage(
            root / PROXY_SETTINGS_FILENAME
        )
        settings = self.controller.settings_store.load()

        self.source = tk.StringVar(value=settings.game_source_path)
        self.profile_name = tk.StringVar()
        self.login_profile = tk.StringVar()
        self.login_username = tk.StringVar()
        self.login_password = tk.StringVar()
        self.login_server = tk.StringVar(
            value=SERVER_LABELS["van_lang"]
        )
        self.sort_mode = tk.StringVar(value="A→Z")

        self.statuses = {}
        self.checked = set()
        self.creation_events = {}
        self.profile_rows = {}
        self.selected_profile_id = None
        self.window_layout_mode = "arrange"
        self._last_layout_signature = None
        self._suspend_keep_above = False
        self._auto_login_active = set()
        self._login_fields_profile_id = None

        # Worker OCR/debug logs are batched onto the Tk thread instead of
        # scheduling one GUI callback per profile per second.
        self._log_queue = queue.SimpleQueue()
        self._log_line_count = 0
        self._log_flush_after_id = None

        self.title(f"{APP_NAME} - Profile Bot")
        self.geometry("480x640")
        self.minsize(460, 600)
        self.configure(fg_color=COLORS["bg"])
        self.attributes("-topmost", True)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        self._build_header()
        self._build_source_bar()
        self._build_create_bar()
        self._build_profile_panel()
        self._build_action_bar()
        self._build_log_panel()
        self._build_footer()

        self._log_flush_after_id = self.after(
            100,
            self._flush_worker_logs,
        )

        self._refresh()
        self._update_source_status()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(
            500,
            self._keep_tool_above_games,
        )

    def _keep_tool_above_games(self):
        """
        Keep real dialogs above TOPMOST game windows, but never periodically
        lift the launcher itself. Re-lifting the launcher can cover
        CustomTkinter combobox dropdowns while they are open.
        """
        try:
            if not self.winfo_exists():
                return

            if not self._suspend_keep_above:
                for child in self.winfo_children():
                    if not isinstance(
                        child,
                        tk.Toplevel,
                    ):
                        continue

                    try:
                        if child.winfo_viewable():
                            keep_above_game(
                                child
                            )
                    except tk.TclError:
                        pass

            self.after(
                750,
                self._keep_tool_above_games,
            )
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # BUILD UI
    # ------------------------------------------------------------------

    def _build_header(self):
        header = ctk.CTkFrame(
            self,
            height=46,
            corner_radius=0,
            fg_color=COLORS["sidebar"],
            border_width=0,
        )
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            header,
            text="◇",
            text_color=COLORS["cyan"],
            font=ctk.CTkFont(size=20, weight="bold"),
            width=30,
        ).grid(row=0, column=0, padx=(10, 2), pady=6)

        title = ctk.CTkFrame(header, fg_color="transparent")
        title.grid(row=0, column=1, sticky="w")

        ctk.CTkLabel(
            title,
            text=APP_NAME,
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(side="left")

        ctk.CTkLabel(
            title,
            text=APP_VERSION,
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=8),
        ).pack(side="left", padx=(6, 0), pady=(4, 0))

        self.header_status = ctk.CTkLabel(
            header,
            text="●  Sẵn sàng",
            text_color=COLORS["green"],
            font=ctk.CTkFont(size=9, weight="bold"),
        )
        self.header_status.grid(row=0, column=2, padx=8, sticky="e")

    def _build_source_bar(self):
        card = CompactCard(self, height=48)
        card.grid(row=1, column=0, sticky="ew", padx=8, pady=(6, 3))
        card.grid_propagate(False)
        card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            card,
            text="🎮 Game",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=9, weight="bold"),
            width=64,
            anchor="w",
        ).grid(row=0, column=0, padx=(10, 4), pady=7)

        self.source_entry = ctk.CTkEntry(
            card,
            textvariable=self.source,
            height=28,
            fg_color=COLORS["input"],
            border_color=COLORS["border_bright"],
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=9),
        )
        self.source_entry.grid(row=0, column=1, sticky="ew", padx=3, pady=6)
        self.source_entry.bind("<FocusOut>", lambda _event: self._update_source_status())

        ctk.CTkButton(
            card,
            text="Chọn",
            width=46,
            height=28,
            fg_color=COLORS["surface_soft"],
            hover_color=COLORS["border_bright"],
            border_width=1,
            border_color=COLORS["border_bright"],
            command=self._browse,
        ).grid(row=0, column=2, padx=3, pady=7)

        self.source_status = ctk.CTkLabel(
            card,
            text="● Chưa",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=8, weight="bold"),
            width=50,
            anchor="w",
        )
        self.source_status.grid(row=0, column=3, padx=(4, 8), pady=7)

    def _build_create_bar(self):
        card = CompactCard(
            self,
            height=132,
        )
        card.grid(
            row=2,
            column=0,
            sticky="ew",
            padx=8,
            pady=3,
        )
        card.grid_propagate(False)
        card.grid_columnconfigure(
            0,
            minsize=76,
            weight=0,
        )
        card.grid_columnconfigure(
            1,
            weight=1,
        )
        card.grid_columnconfigure(
            2,
            weight=1,
        )
        card.grid_columnconfigure(
            3,
            minsize=142,
            weight=0,
        )

        ctk.CTkLabel(
            card,
            text="1. Tạo",
            text_color=COLORS["cyan"],
            font=ctk.CTkFont(
                size=9,
                weight="bold",
            ),
            anchor="w",
        ).grid(
            row=0,
            column=0,
            padx=(10, 4),
            pady=(7, 3),
            sticky="w",
        )

        self.profile_entry = ctk.CTkEntry(
            card,
            textvariable=self.profile_name,
            height=27,
            fg_color=COLORS["input"],
            border_color=COLORS["border_bright"],
            placeholder_text="Tên profile mới...",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=8),
        )
        self.profile_entry.grid(
            row=0,
            column=1,
            columnspan=2,
            sticky="ew",
            padx=3,
            pady=(6, 3),
        )

        ctk.CTkButton(
            card,
            text="Tạo profile",
            width=86,
            height=27,
            fg_color=COLORS["blue"],
            hover_color=COLORS["blue_hover"],
            command=self._create,
        ).grid(
            row=0,
            column=3,
            sticky="ew",
            padx=(4, 10),
            pady=(6, 3),
        )

        ctk.CTkLabel(
            card,
            text="2. Login",
            text_color=COLORS["green"],
            font=ctk.CTkFont(
                size=9,
                weight="bold",
            ),
            anchor="w",
        ).grid(
            row=1,
            column=0,
            padx=(10, 4),
            pady=3,
            sticky="w",
        )

        self.login_profile_combo = ctk.CTkComboBox(
            card,
            variable=self.login_profile,
            values=(),
            height=27,
            fg_color=COLORS["input"],
            border_color=COLORS["border_bright"],
            button_color=COLORS["surface_soft"],
            button_hover_color=COLORS["blue_hover"],
            dropdown_fg_color=COLORS["surface_alt"],
            dropdown_hover_color=COLORS["surface_soft"],
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=8),
            dropdown_font=ctk.CTkFont(size=8),
            command=self._choose_login_profile,
        )
        self.login_profile_combo.grid(
            row=1,
            column=1,
            sticky="ew",
            padx=3,
            pady=3,
        )

        self.login_server_combo = ctk.CTkComboBox(
            card,
            variable=self.login_server,
            values=tuple(
                SERVER_LABELS.values()
            ),
            height=27,
            fg_color=COLORS["input"],
            border_color=COLORS["border_bright"],
            button_color=COLORS["surface_soft"],
            button_hover_color=COLORS["blue_hover"],
            dropdown_fg_color=COLORS["surface_alt"],
            dropdown_hover_color=COLORS["surface_soft"],
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=8),
            dropdown_font=ctk.CTkFont(size=8),
        )
        self.login_server_combo.grid(
            row=1,
            column=2,
            sticky="ew",
            padx=3,
            pady=3,
        )

        ctk.CTkLabel(
            card,
            text="Chọn profile + server",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=8),
            anchor="w",
        ).grid(
            row=1,
            column=3,
            sticky="w",
            padx=(6, 10),
            pady=3,
        )

        ctk.CTkLabel(
            card,
            text="TK / MK",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(
                size=8,
                weight="bold",
            ),
            anchor="w",
        ).grid(
            row=2,
            column=0,
            padx=(10, 4),
            pady=3,
            sticky="w",
        )

        self.login_username_entry = ctk.CTkEntry(
            card,
            textvariable=self.login_username,
            height=27,
            fg_color=COLORS["input"],
            border_color=COLORS["border_bright"],
            placeholder_text="Tài khoản",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=8),
        )
        self.login_username_entry.grid(
            row=2,
            column=1,
            sticky="ew",
            padx=3,
            pady=3,
        )

        self.login_password_entry = ctk.CTkEntry(
            card,
            textvariable=self.login_password,
            height=27,
            fg_color=COLORS["input"],
            border_color=COLORS["border_bright"],
            placeholder_text="Mật khẩu",
            show="•",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=8),
        )
        self.login_password_entry.grid(
            row=2,
            column=2,
            sticky="ew",
            padx=3,
            pady=3,
        )

        ctk.CTkButton(
            card,
            text="▶ Tự đăng nhập + lưu",
            height=27,
            fg_color=COLORS["green"],
            hover_color=COLORS["green_hover"],
            text_color=COLORS["black"],
            font=ctk.CTkFont(
                size=8,
                weight="bold",
            ),
            command=self._auto_login_selected,
        ).grid(
            row=2,
            column=3,
            sticky="ew",
            padx=(4, 10),
            pady=3,
        )

        ctk.CTkLabel(
            card,
            text="Thủ công",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=8),
            anchor="w",
        ).grid(
            row=3,
            column=0,
            padx=(10, 4),
            pady=(2, 7),
            sticky="w",
        )

        ctk.CTkLabel(
            card,
            text=(
                "Tự động đã bao gồm xác nhận auth."
            ),
            text_color=COLORS["muted_dark"],
            font=ctk.CTkFont(size=7),
            anchor="w",
        ).grid(
            row=3,
            column=1,
            columnspan=2,
            sticky="w",
            padx=3,
            pady=(2, 7),
        )

        manual = ctk.CTkFrame(
            card,
            fg_color="transparent",
        )
        manual.grid(
            row=3,
            column=3,
            sticky="e",
            padx=(4, 10),
            pady=(2, 7),
        )

        ctk.CTkButton(
            manual,
            text="Mở tay",
            width=58,
            height=23,
            fg_color=COLORS["surface_soft"],
            hover_color=COLORS["border_bright"],
            command=self._continue_login,
        ).pack(
            side="left",
            padx=(0, 3),
        )

        ctk.CTkButton(
            manual,
            text="Xác nhận",
            width=66,
            height=23,
            fg_color=COLORS["surface_soft"],
            hover_color=COLORS["border_bright"],
            command=self._confirm_login,
        ).pack(
            side="left",
        )

    def _build_profile_panel(self):
        panel = CompactCard(self)
        panel.grid(row=3, column=0, sticky="nsew", padx=8, pady=3)
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(2, weight=1)

        top = ctk.CTkFrame(panel, fg_color="transparent", height=42)
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        top.grid_columnconfigure(0, weight=1)

        self.profile_title = ctk.CTkLabel(
            top,
            text="👥  Danh sách profile (0)",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=9, weight="bold"),
        )
        self.profile_title.grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            top,
            text="Sort",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=8),
        ).grid(row=0, column=1, padx=(8, 4))

        self.sort_combo = ctk.CTkComboBox(
            top,
            variable=self.sort_mode,
            values=("A→Z", "Z→A", "TT"),
            width=70,
            height=24,
            fg_color=COLORS["input"],
            border_color=COLORS["border_bright"],
            dropdown_fg_color=COLORS["surface_alt"],
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=8),
            command=lambda _value: self._refresh(),
        )
        self.sort_combo.grid(row=0, column=2, sticky="e")

        header = ctk.CTkFrame(
            panel,
            fg_color=COLORS["surface_soft"],
            corner_radius=8,
            height=34,
        )
        header.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))
        header.grid_propagate(False)

        columns = (
            ("", PROFILE_COLUMN_WIDTHS[0]),
            ("Profile / TT", PROFILE_COLUMN_WIDTHS[1]),
            ("Boss", PROFILE_COLUMN_WIDTHS[2]),
            ("Size", PROFILE_COLUMN_WIDTHS[3]),
            ("Ctrl", PROFILE_COLUMN_WIDTHS[4]),
        )
        for index, (label, width) in enumerate(columns):
            header.grid_columnconfigure(
                index,
                minsize=width,
                weight=1 if index == 1 else 0,
            )
            ctk.CTkLabel(
                header,
                text=label,
                text_color=COLORS["muted"],
                font=ctk.CTkFont(size=8, weight="bold"),
                anchor="w",
            ).grid(row=0, column=index, sticky="ew", padx=3, pady=8)

        self.rows_frame = ctk.CTkScrollableFrame(
            panel,
            fg_color="transparent",
            corner_radius=0,
            scrollbar_button_color=COLORS["surface_soft"],
            scrollbar_button_hover_color=COLORS["border_bright"],
        )
        self.rows_frame.grid(row=2, column=0, sticky="nsew", padx=4, pady=1)
        self.rows_frame.grid_columnconfigure(0, weight=1)

        bulk = ctk.CTkFrame(panel, fg_color="transparent", height=42)
        bulk.grid(row=3, column=0, sticky="ew", padx=8, pady=(2, 6))
        bulk.grid_columnconfigure(0, weight=1)

        self.selected_label = ctk.CTkLabel(
            bulk,
            text="Đã chọn 0",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=8),
        )
        self.selected_label.grid(row=0, column=0, sticky="w")

        ctk.CTkButton(
            bulk,
            text="Tất cả",
            width=50,
            height=22,
            fg_color=COLORS["surface_soft"],
            hover_color=COLORS["border_bright"],
            command=self._select_all,
        ).grid(row=0, column=1, padx=3)

        ctk.CTkButton(
            bulk,
            text="Bỏ",
            width=34,
            height=22,
            fg_color=COLORS["surface_soft"],
            hover_color=COLORS["border_bright"],
            command=self._clear_selection,
        ).grid(row=0, column=2, padx=3)

        ctk.CTkButton(
            bulk,
            text="Xóa",
            width=38,
            height=22,
            fg_color="#35121B",
            hover_color=COLORS["red_hover"],
            text_color=COLORS["red"],
            command=self._delete_selected,
        ).grid(row=0, column=3, padx=(3, 0))

    def _build_action_bar(self):
        bar = CompactCard(self, height=42)
        bar.grid(row=4, column=0, sticky="ew", padx=8, pady=3)
        bar.grid_propagate(False)
        bar.grid_columnconfigure(0, weight=1)

        actions = ctk.CTkFrame(bar, fg_color="transparent")
        actions.grid(row=0, column=0, pady=8)

        ctk.CTkButton(
            actions,
            text="▶ Start",
            width=70,
            height=24,
            fg_color=COLORS["cyan"],
            hover_color=COLORS["blue"],
            text_color=COLORS["black"],
            font=ctk.CTkFont(size=9, weight="bold"),
            command=self._start_selected,
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            actions,
            text="■ Stop",
            width=64,
            height=24,
            fg_color=COLORS["surface_soft"],
            hover_color=COLORS["red_hover"],
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=9, weight="bold"),
            command=self._stop_selected,
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            actions,
            text="▣ Xếp",
            width=74,
            height=24,
            fg_color=COLORS["purple"],
            hover_color=COLORS["purple_hover"],
            font=ctk.CTkFont(size=9, weight="bold"),
            command=self._arrange_windows,
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            actions,
            text="▤ Chồng",
            width=80,
            height=24,
            fg_color=COLORS["surface_soft"],
            hover_color=COLORS["border_bright"],
            font=ctk.CTkFont(size=9, weight="bold"),
            command=self._stack_windows,
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            actions,
            text="⇄ Proxy",
            width=78,
            height=24,
            fg_color=COLORS["surface_soft"],
            hover_color=COLORS["border_bright"],
            font=ctk.CTkFont(size=9, weight="bold"),
            command=self._open_proxy_dialog,
        ).pack(side="left", padx=2)

    def _open_proxy_dialog(self):
        try:
            settings = (
                self.proxy_settings_store.load()
            )
        except RuntimeError as exc:
            showerror(
                "Proxy",
                str(exc),
                parent=self,
            )
            return

        ProxySettingsDialog(
            self,
            settings,
            on_save=self._save_proxy_settings,
            on_apply=self._apply_proxy_settings,
            on_verify=self._verify_proxy_settings,
        )

    def _verify_proxy_settings(
        self,
        settings,
    ):
        result = verify_proxy_setup(
            tuple(
                self.controller.profiles
            ),
            settings,
            config_wait_seconds=8.0,
            proxy_timeout=5.0,
        )

        proxy_ok = (
            bool(result.proxy_results)
            and all(
                item.ok
                for item
                in result.proxy_results
            )
        )

        details = ", ".join(
            (
                f"Proxy {index}: "
                + (
                    f"OK {item.latency_ms:.0f}ms"
                    if item.ok
                    else f"LỖI {item.detail}"
                )
            )
            for index, item
            in enumerate(
                result.proxy_results,
                start=1,
            )
        )

        self._log_queue.put(
            (
                "Proxy",
                (
                    "VERIFY "
                    + (
                        "OK"
                        if (
                            result.config_applied
                            and proxy_ok
                        )
                        else "CHƯA OK"
                    )
                    + " | config="
                    + (
                        "OK"
                        if result.config_applied
                        else "CHƯA KHỚP"
                    )
                    + (
                        f" | {details}"
                        if details
                        else ""
                    )
                ),
            )
        )

        return result

    def _ensure_proxy_routing_current(self):
        """
        Proxy is opt-in.

        Saving/editing proxy settings must never block normal game launch.
        Until the user explicitly presses Apply, profiles continue using the
        machine's normal network. If an already-applied config still matches,
        it remains active as usual.
        """
        settings = self.proxy_settings_store.load()

        if (
            not settings.proxies
            and not settings.proxifyre_path.strip()
        ):
            return False

        return routing_config_matches(
            tuple(self.controller.profiles),
            settings,
        )

    def _save_proxy_settings(self, settings):
        self.proxy_settings_store.save(
            settings
        )
        self._log(
            "Proxy",
            "READY",
            (
                (
                    f"Đã lưu {len(settings.proxies)} SOCKS5 proxy"
                    + (
                        " • TEST MODE"
                        if settings.test_mode
                        else ""
                    )
                )
            ),
        )

    def _apply_proxy_settings(self, settings):
        if not settings.proxifyre_path.strip():
            raise ValueError(
                "Hãy chọn ProxiFyre.exe trước khi áp dụng"
            )

        if settings.test_mode:
            if not settings.proxies:
                raise ValueError(
                    "Test proxy cần ít nhất 1 SOCKS5 proxy"
                )

            if len(self.controller.profiles) < 2:
                raise ValueError(
                    "Test proxy cần ít nhất 2 profile: "
                    "Profile 1 Direct, Profile 2 qua Proxy 1"
                )

        if not settings.proxies:
            route_message = (
                "Đang tắt route SOCKS5; tất cả profile sẽ Direct"
            )
        elif settings.test_mode:
            route_message = (
                "TEST MODE: Profile 1 Direct, Profile 2 -> Proxy 1, "
                "Profile 3 -> Proxy 2; vượt số proxy dùng proxy cuối"
            )
        else:
            route_message = (
                "Route chuẩn: 1-30 Direct, 31+ qua SOCKS5 theo nhóm 30"
            )

        self._log(
            "Proxy",
            "INFO",
            route_message,
        )

        executable = apply_proxy_routing(
            self.app_root,
            tuple(self.controller.profiles),
            settings,
        )

        self._log(
            "Proxy",
            "SUCCESS",
            (
                f"Đã gửi cấu hình tới {executable.name}; "
                "đang chờ kiểm tra config + SOCKS5"
            ),
        )

    def _build_log_panel(self):
        panel = CompactCard(self, height=96)
        panel.grid(row=5, column=0, sticky="ew", padx=8, pady=3)
        panel.grid_propagate(False)
        panel.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(panel, fg_color="transparent", height=34)
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 3))
        top.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            top,
            text="▤  Nhật ký",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=10, weight="bold"),
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkButton(
            top,
            text="Xóa log",
            width=56,
            height=22,
            fg_color=COLORS["surface_soft"],
            hover_color=COLORS["border_bright"],
            command=self._clear_log,
        ).grid(row=0, column=1, sticky="e")

        self.log = ctk.CTkTextbox(
            panel,
            height=54,
            fg_color=COLORS["black"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=8,
            text_color=COLORS["muted"],
            font=("Consolas", 8),
            wrap="word",
        )
        self.log.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        self.log.configure(state="disabled")

        try:
            self.log._textbox.tag_configure("info", foreground=COLORS["blue"])
            self.log._textbox.tag_configure("success", foreground=COLORS["green"])
            self.log._textbox.tag_configure("warning", foreground=COLORS["amber"])
            self.log._textbox.tag_configure("error", foreground=COLORS["red"])
        except Exception:
            pass

    def _build_footer(self):
        footer = ctk.CTkFrame(
            self,
            height=24,
            corner_radius=0,
            fg_color=COLORS["sidebar"],
        )
        footer.grid(row=6, column=0, sticky="ew", pady=(3, 0))
        footer.grid_propagate(False)
        footer.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            footer,
            text="◇  TMLH Bot",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=8, weight="bold"),
        ).grid(row=0, column=0, padx=12)

        ctk.CTkLabel(
            footer,
            text="Smart play",
            text_color=COLORS["muted_dark"],
            font=ctk.CTkFont(size=8),
        ).grid(row=0, column=1)

        ctk.CTkLabel(
            footer,
            text="TMLH",
            text_color=COLORS["muted_dark"],
            font=ctk.CTkFont(size=8),
        ).grid(row=0, column=2, padx=12)

    # ------------------------------------------------------------------
    # PROFILE LIST / CRUD
    # ------------------------------------------------------------------

    def _runtime_status(self, profile):
        runtime = self.statuses.get(profile.profile_id)
        if runtime:
            return runtime

        try:
            self.controller.manager.check_clone(profile)
            return "Ready" if profile.login_ready else "Chưa đăng nhập"
        except MissingGameFilesError:
            return "Error / Missing Files"

    def _sorted_profiles(self):
        profiles = list(self.controller.profiles)
        mode = self.sort_mode.get()

        if mode == "Z→A":
            return sorted(
                profiles,
                key=lambda item: item.profile_name.casefold(),
                reverse=True,
            )

        if mode == "TT":
            return sorted(
                profiles,
                key=lambda item: (
                    self._runtime_status(item).casefold(),
                    item.profile_name.casefold(),
                ),
            )

        return sorted(
            profiles,
            key=lambda item: item.profile_name.casefold(),
        )

    def _refresh(self):
        if not hasattr(self, "rows_frame"):
            return

        valid_ids = {profile.profile_id for profile in self.controller.profiles}
        self.checked.intersection_update(valid_ids)

        if self.selected_profile_id not in valid_ids:
            self.selected_profile_id = None

        for child in self.rows_frame.winfo_children():
            child.destroy()

        self.profile_rows.clear()
        profiles = self._sorted_profiles()

        self.profile_title.configure(
            text=f"👥  Danh sách profile ({len(profiles)})"
        )
        self.selected_label.configure(
            text=f"Đã chọn {len(self.checked)} profile"
        )

        profile_names = [
            profile.profile_name
            for profile in profiles
        ]

        if hasattr(
            self,
            "login_profile_combo",
        ):
            self.login_profile_combo.configure(
                values=profile_names,
            )

            current_login_profile = (
                self.login_profile.get()
                .strip()
            )

            if current_login_profile not in profile_names:
                selected = next(
                    (
                        profile
                        for profile in profiles
                        if profile.profile_id
                        == self.selected_profile_id
                    ),
                    None,
                )

                if selected is not None:
                    self.login_profile.set(
                        selected.profile_name
                    )
                elif profiles:
                    self.login_profile.set(
                        profiles[0].profile_name
                    )
                else:
                    self.login_profile.set("")

            selected_login_name = (
                self.login_profile.get()
                .strip()
            )
            selected_login = next(
                (
                    profile
                    for profile in profiles
                    if profile.profile_name
                    == selected_login_name
                ),
                None,
            )

            if (
                selected_login is not None
                and self._login_fields_profile_id
                != selected_login.profile_id
            ):
                self._load_login_fields(
                    selected_login.profile_id
                )

        boss_values = tuple(
            BOSS_LABELS.get(key, key)
            for key in BOSSES
        )

        for index, profile in enumerate(profiles):
            row = ProfileRow(
                self.rows_frame,
                profile,
                self._runtime_status(profile),
                checked=profile.profile_id in self.checked,
                focused=profile.profile_id == self.selected_profile_id,
                boss_values=boss_values,
                size_values=SIZES,
                on_toggle=self._toggle_profile,
                on_focus=self._focus_profile,
                on_change=self._change_profile_options,
                on_start=self._start_single,
                on_stop=self._stop_single,
                on_edit=self._open_edit_dialog,
                on_delete=self._delete_single,
            )
            row.grid(row=index, column=0, sticky="ew", pady=2)
            self.profile_rows[profile.profile_id] = row

        if not profiles:
            ctk.CTkLabel(
                self.rows_frame,
                text="Chưa có profile. Tạo profile ở phía trên.",
                text_color=COLORS["muted"],
                font=ctk.CTkFont(size=11),
            ).grid(row=0, column=0, pady=30)

        self._update_header_status()

    def _update_header_status(self):
        running = sum(
            1
            for worker in self.controller.workers.values()
            if not worker.context.stop_event.is_set()
            and worker.context.state not in ("STOPPED", "ERROR")
        )
        self.header_status.configure(
            text=f"●  {running} đang chạy" if running else "●  Sẵn sàng",
            text_color=COLORS["green"],
        )

    def _apply_runtime_status(self, profile_id, status):
        self.statuses[profile_id] = status

        # Status sorting requires a rebuild. Otherwise update just one row,
        # avoiding destroy/recreate of every CustomTkinter widget.
        row = self.profile_rows.get(profile_id)
        if (
            self.sort_mode.get() == "TT"
            or row is None
        ):
            self._refresh()
        else:
            row.set_status(status)
            self._update_header_status()

    def _focus_profile(self, profile_id):
        self.selected_profile_id = profile_id

        try:
            profile = self.controller.get(
                profile_id
            )
            self.login_profile.set(
                profile.profile_name
            )
        except StopIteration:
            pass

        self._load_login_fields(
            profile_id
        )
        self._refresh()

    def _toggle_profile(self, profile_id, enabled):
        if enabled:
            self.checked.add(profile_id)
            self.selected_profile_id = profile_id
            try:
                profile = self.controller.get(
                    profile_id
                )
                self.login_profile.set(
                    profile.profile_name
                )
            except StopIteration:
                pass
            self._load_login_fields(
                profile_id
            )
        else:
            self.checked.discard(profile_id)
        self._refresh()

    def _select_all(self):
        self.checked = {
            profile.profile_id
            for profile in self.controller.profiles
        }
        self._refresh()

    def _clear_selection(self):
        self.checked.clear()
        self._refresh()

    def _selected_profile(self):
        if not self.selected_profile_id:
            raise ValueError("Hãy chọn một profile trước")
        return self.selected_profile_id


    def _choose_login_profile(
        self,
        profile_name,
    ):
        profile = next(
            (
                item
                for item
                in self.controller.profiles
                if item.profile_name
                == profile_name
            ),
            None,
        )

        if profile is None:
            return

        self.selected_profile_id = (
            profile.profile_id
        )
        self._load_login_fields(
            profile.profile_id
        )
        self._refresh()

    def _selected_login_profile_id(
        self,
    ):
        requested = (
            self.login_profile.get()
            .strip()
        )

        if requested:
            profile = next(
                (
                    item
                    for item
                    in self.controller.profiles
                    if item.profile_name
                    == requested
                ),
                None,
            )

            if profile is not None:
                self.selected_profile_id = (
                    profile.profile_id
                )
                return profile.profile_id

        if self.selected_profile_id:
            try:
                profile = self.controller.get(
                    self.selected_profile_id
                )
                self.login_profile.set(
                    profile.profile_name
                )
                return profile.profile_id
            except StopIteration:
                pass

        if len(
            self.controller.profiles
        ) == 1:
            profile = (
                self.controller.profiles[0]
            )
            self.selected_profile_id = (
                profile.profile_id
            )
            self.login_profile.set(
                profile.profile_name
            )
            return profile.profile_id

        raise ValueError(
            "Hãy chọn profile trong ô '2. Đăng nhập' trước"
        )

    def _load_login_fields(
        self,
        profile_id,
    ):
        self._login_fields_profile_id = (
            profile_id
        )

        try:
            profile = self.controller.get(
                profile_id
            )
            credentials = (
                load_login_credentials(
                    profile.game_path
                )
            )
        except (
            StopIteration,
            RuntimeError,
        ) as exc:
            self.login_username.set("")
            self.login_password.set("")
            self.login_server.set(
                SERVER_LABELS["van_lang"]
            )

            if isinstance(
                exc,
                RuntimeError,
            ):
                self._log(
                    profile_id,
                    "WARN",
                    str(exc),
                )
            return

        if credentials is None:
            self.login_username.set("")
            self.login_password.set("")
            self.login_server.set(
                SERVER_LABELS["van_lang"]
            )
            return

        self.login_username.set(
            credentials.username
        )
        self.login_password.set(
            credentials.password
        )
        self.login_server.set(
            SERVER_LABELS[
                credentials.server
            ]
        )

    def _login_credentials_from_ui(
        self,
    ):
        server_label = (
            self.login_server.get()
            .strip()
        )
        server = SERVER_KEYS.get(
            server_label
        )

        if server is None:
            raise ValueError(
                f"Server không hợp lệ: {server_label}"
            )

        return LoginCredentials(
            username=(
                self.login_username
                .get()
                .strip()
            ),
            password=(
                self.login_password
                .get()
            ),
            server=server,
        ).validate()

    def _auto_login_selected(self):
        try:
            profile_id = (
                self._selected_login_profile_id()
            )
            profile = self.controller.get(
                profile_id
            )

            self.controller.manager.check_clone(
                profile
            )
            self._ensure_proxy_routing_current()

            credentials = (
                self._login_credentials_from_ui()
            )

            save_login_credentials(
                profile.game_path,
                credentials,
            )

            if profile_id in self._auto_login_active:
                raise ValueError(
                    "Profile này đang auto login"
                )

            self._auto_login_active.add(
                profile_id
            )
            self._suspend_keep_above = True

            self._run_auto_login(
                profile,
                credentials,
            )

        except (
            ValueError,
            OSError,
            RuntimeError,
        ) as exc:
            showerror(
                "Auto Login",
                str(exc),
                parent=self,
            )

    def _run_auto_login(
        self,
        profile,
        credentials,
    ):
        context = (
            ProfileRuntimeContext
            .from_profile(
                profile
            )
        )
        cancel = context.stop_event

        self.creation_events[
            profile.profile_id
        ] = cancel

        def post_status(state):
            self.after(
                0,
                self._status,
                profile.profile_id,
                state.replace(
                    "_",
                    " ",
                ).title(),
            )

        def post_log(message):
            self._log_queue.put(
                (
                    profile.profile_id,
                    message,
                )
            )

        def work():
            try:
                self.after(
                    0,
                    self._status,
                    profile.profile_id,
                    "Launching Game",
                )

                with PROFILE_LAUNCH_LOCK:
                    # Registry auth is global per Windows user. Clear only the
                    # TMLH auth keys so a stale previous account cannot make a
                    # failed fresh login look successful.
                    clear_current_auth_values()

                    (
                        process_id,
                        hwnd,
                        _launched,
                    ) = acquire_profile_window(
                        profile.game_path,
                        cancel,
                        window_title=(
                            profile.profile_name
                        ),
                    )

                    context.process_id = (
                        process_id
                    )
                    context.window_handle = hwnd

                    resize_client(
                        hwnd,
                        profile.window_width,
                        profile.window_height,
                    )
                    set_window_topmost(
                        hwnd,
                        True,
                    )

                    runner = AutoLoginRunner(
                        on_status=post_status,
                        on_log=post_log,
                    )
                    runner.run(
                        context,
                        credentials,
                    )

                    # Persist the registry auth only after the automated login
                    # interaction has completed. Retry briefly because the
                    # game may write its auth values asynchronously.
                    post_log(
                        "Auto login: đang xác nhận auth..."
                    )

                    deadline = (
                        time.monotonic()
                        + AUTO_LOGIN_AUTH_CONFIRM_TIMEOUT
                    )
                    last_error = None
                    updated = None

                    while (
                        time.monotonic()
                        < deadline
                        and not cancel.is_set()
                    ):
                        try:
                            updated = (
                                self.controller
                                .confirm_login(
                                    profile.profile_id
                                )
                            )
                            break
                        except RuntimeError as exc:
                            last_error = exc

                        if cancel.wait(
                            AUTO_LOGIN_AUTH_CONFIRM_POLL
                        ):
                            break

                    if cancel.is_set():
                        raise InterruptedError(
                            "Auto login đã dừng"
                        )

                    if updated is None:
                        raise RuntimeError(
                            (
                                "Đã thao tác đăng nhập nhưng chưa thấy "
                                "auth mới của game trong Registry"
                                + (
                                    f": {last_error}"
                                    if last_error
                                    else ""
                                )
                            )
                        )

                self.after(
                    0,
                    self._auto_login_success,
                    profile.profile_id,
                    updated.profile_name,
                )

            except InterruptedError:
                self.after(
                    0,
                    self._status,
                    profile.profile_id,
                    "Stopped",
                )

            except Exception as exc:
                self.after(
                    0,
                    self._error,
                    profile.profile_id,
                    exc,
                )
                self.after(
                    0,
                    lambda message=str(exc):
                    showerror(
                        "Auto Login",
                        message,
                        parent=self,
                    ),
                )

            finally:
                self.after(
                    0,
                    self._auto_login_finished,
                    profile.profile_id,
                )

        threading.Thread(
            target=work,
            daemon=True,
            name=(
                f"auto-login-"
                f"{profile.profile_id}"
            ),
        ).start()

    def _auto_login_success(
        self,
        profile_id,
        profile_name,
    ):
        self._status(
            profile_id,
            "Ready",
        )
        self._log(
            profile_name,
            "SUCCESS",
            (
                "Auto login thành công • "
                "đã lưu auth riêng cho profile"
            ),
        )
        showinfo(
            "Auto Login",
            (
                f"{profile_name}: đăng nhập thành công.\n"
                "Auth của profile đã được lưu riêng."
            ),
            parent=self,
        )

    def _auto_login_finished(
        self,
        profile_id,
    ):
        self.creation_events.pop(
            profile_id,
            None,
        )
        self._auto_login_active.discard(
            profile_id
        )
        self._suspend_keep_above = bool(
            self._auto_login_active
        )
        keep_above_game(
            self
        )


    def _change_profile_options(self, profile_id, boss_label, size):
        try:
            boss = BOSS_KEYS.get(boss_label, boss_label)
            profile = self.controller.set_options(profile_id, boss, size)
            self.selected_profile_id = profile_id
            self._log(
                profile.profile_name,
                "OPTIONS",
                f"{BOSS_LABELS.get(profile.selected_boss, profile.selected_boss)} • {size}",
            )
            self._apply_window_layout()
        except (ValueError, OSError) as exc:
            showerror("Profile options", str(exc), parent=self)
            self._refresh()

    def _open_edit_dialog(self, profile_id):
        try:
            profile = self.controller.get(profile_id)
            self.selected_profile_id = profile_id

            EditProfileDialog(
                self,
                profile,
                tuple(BOSS_LABELS.get(key, key) for key in BOSSES),
                SIZES,
                self._save_profile_edit,
            )
        except (ValueError, OSError) as exc:
            showerror("Sửa profile", str(exc), parent=self)

    def _save_profile_edit(self, old_profile_id, new_name, boss_label, size):
        try:
            boss = BOSS_KEYS.get(boss_label, boss_label)
            updated = self.controller.edit_profile(
                old_profile_id,
                new_name,
                boss,
                size,
            )

            old_status = self.statuses.pop(old_profile_id, None)
            if old_status is not None:
                self.statuses[updated.profile_id] = old_status

            was_checked = old_profile_id in self.checked
            self.checked.discard(old_profile_id)
            if was_checked:
                self.checked.add(updated.profile_id)

            self.selected_profile_id = updated.profile_id
            self._log(
                updated.profile_name,
                "SUCCESS",
                "Đã lưu thay đổi profile",
            )
            self._refresh()

        except (ValueError, OSError) as exc:
            showerror("Sửa profile", str(exc), parent=self)
            raise

    def _delete_single(self, profile_id):
        try:
            profile = self.controller.get(profile_id)
        except StopIteration:
            return

        if not askyesno(
            "Xóa profile",
            (
                f"Xóa profile “{profile.profile_name}”?\n\n"
                "Thư mục clone của profile cũng sẽ bị xóa.\n"
                "Game gốc không bị ảnh hưởng."
            ),
            parent=self,
        ):
            return

        try:
            deleted = self.controller.delete_profile(profile_id)
            self.checked.discard(profile_id)
            self.statuses.pop(profile_id, None)

            if self.selected_profile_id == profile_id:
                self.selected_profile_id = None

            self._log(
                deleted.profile_name,
                "SUCCESS",
                "Đã xóa profile và clone",
            )
            self._refresh()

        except (ValueError, OSError) as exc:
            showerror("Xóa profile", str(exc), parent=self)

    def _delete_selected(self):
        ids = [
            profile.profile_id
            for profile in self.controller.profiles
            if profile.profile_id in self.checked
        ]

        if not ids:
            self._log("App", "WARN", "Chưa chọn profile để xóa")
            return

        if not askyesno(
            "Xóa nhiều profile",
            (
                f"Xóa {len(ids)} profile đã chọn?\n\n"
                "Các thư mục clone tương ứng cũng sẽ bị xóa."
            ),
            parent=self,
        ):
            return

        failed = []

        for profile_id in ids:
            try:
                self.controller.delete_profile(profile_id)
                self.statuses.pop(profile_id, None)
                self.checked.discard(profile_id)
            except (ValueError, OSError) as exc:
                failed.append(f"{profile_id}: {exc}")

        if self.selected_profile_id in ids:
            self.selected_profile_id = None

        self._refresh()

        if failed:
            showwarning(
                "Xóa profile",
                "\n".join(failed),
                parent=self,
            )

    # ------------------------------------------------------------------
    # SOURCE / CREATE / LOGIN
    # ------------------------------------------------------------------

    def _update_source_status(self):
        value = self.source.get().strip()
        launcher = (
            Path(value) / "ThienMenhLacHong_Launcher.exe"
            if value
            else None
        )

        if launcher and launcher.is_file():
            self.source_status.configure(
                text="● Đã tìm thấy",
                text_color=COLORS["green"],
            )
        elif value:
            self.source_status.configure(
                text="● Sai thư mục",
                text_color=COLORS["red"],
            )
        else:
            self.source_status.configure(
                text="● Chưa",
                text_color=COLORS["muted"],
            )

    def _browse(self):
        selected = filedialog.askdirectory(parent=self)

        if selected:
            self.source.set(selected)
            self.controller.settings_store.save(AppSettings(selected))
            self._update_source_status()

    def _create(self):
        try:
            source = self.source.get().strip()
            profile = self.controller.prepare_profile(
                self.profile_name.get(),
                source,
            )
            self.controller.settings_store.save(AppSettings(source))
            self.profile_name.set("")
            self.statuses[profile.profile_id] = "Creating"
            self.selected_profile_id = profile.profile_id
            self.login_profile.set(
                profile.profile_name
            )
            self._login_fields_profile_id = (
                profile.profile_id
            )
            self.login_username.set("")
            self.login_password.set("")
            self.login_server.set(
                SERVER_LABELS["van_lang"]
            )
            self._refresh()
            self._run_creation(profile, source, repair=False)

        except (ValueError, OSError) as exc:
            showerror("Tạo profile", str(exc), parent=self)

    def _run_creation(
        self,
        profile,
        source,
        repair,
    ):
        cancel = threading.Event()
        self.creation_events[
            profile.profile_id
        ] = cancel

        def work():
            try:
                self.after(
                    0,
                    self._status,
                    profile.profile_id,
                    "Copying Game",
                )

                if repair:
                    self.controller.repair_profile(
                        profile.profile_id,
                        source,
                    )
                else:
                    self.controller.manager.clone_profile(
                        profile,
                        source,
                    )

                if cancel.is_set():
                    return

                self.after(
                    0,
                    self._creation_ready_for_login,
                    profile.profile_id,
                    profile.profile_name,
                )

            except Exception as exc:
                self.after(
                    0,
                    self._error,
                    profile.profile_id,
                    exc,
                )
            finally:
                self.creation_events.pop(
                    profile.profile_id,
                    None,
                )

        threading.Thread(
            target=work,
            daemon=True,
            name=(
                f"create-"
                f"{profile.profile_id}"
            ),
        ).start()

    def _creation_ready_for_login(
        self,
        profile_id,
        profile_name,
    ):
        self.selected_profile_id = (
            profile_id
        )
        self.login_profile.set(
            profile_name
        )
        self._status(
            profile_id,
            "Chưa đăng nhập",
        )
        self._log(
            profile_name,
            "SUCCESS",
            (
                "Profile đã tạo xong. "
                "Nhập tài khoản + mật khẩu ở mục '2. Đăng nhập' "
                "rồi bấm 'Tự đăng nhập + tự lưu'."
            ),
        )
        self._refresh()

    def _continue_login(self):
        try:
            self._continue_login_for(
                self._selected_login_profile_id()
            )
        except (ValueError, OSError, RuntimeError) as exc:
            showerror("Mở game", str(exc), parent=self)

    def _continue_login_for(self, profile_id):
        try:
            profile = self.controller.get(profile_id)
            self.selected_profile_id = profile_id
            self.controller.manager.check_clone(profile)
            self._ensure_proxy_routing_current()
            self._open_for_login(profile)
            self._refresh()

        except (ValueError, OSError) as exc:
            showerror("Mở game", str(exc), parent=self)

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
                        window_title=profile.profile_name,
                    )
                    set_window_topmost(_hwnd, True)

                    if launched:
                        self.after(0, self._status, profile.profile_id, "Waiting Startup")
                        if cancel.wait(10):
                            return

                if not cancel.is_set():
                    self.after(0, self._status, profile.profile_id, "Đăng nhập thủ công")

            except Exception as exc:
                self.after(0, self._error, profile.profile_id, exc)

        threading.Thread(
            target=work,
            daemon=True,
            name=f"login-{profile.profile_id}",
        ).start()

    def _confirm_login(self):
        try:
            profile_id = self._selected_login_profile_id()

            if self.statuses.get(profile_id) != "Đăng nhập thủ công":
                raise ValueError(
                    "Bước thủ công: bấm 'Mở thủ công', đăng nhập trong game, "
                    "sau đó bấm 'Xác nhận đã đăng nhập'"
                )

            profile = self.controller.confirm_login(profile_id)
            self._status(profile_id, "Ready")
            self._log(
                profile.profile_name,
                "SUCCESS",
                "Đã lưu auth riêng cho profile",
            )

        except (
            ValueError,
            OSError,
            RuntimeError,
        ) as exc:
            showerror(
                "Xác nhận đăng nhập",
                str(exc),
                parent=self,
            )

    # ------------------------------------------------------------------
    # START / STOP
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
            "on_log": lambda pid, message: self._log_queue.put(
                (pid, message)
            ),
        }

    def _start_profile_ids(self, profile_ids):
        self._ensure_proxy_routing_current()

        self.controller.start_selected(
            profile_ids,
            **self._worker_callbacks(),
        )
        self._refresh()

    def _start_selected(self):
        if not self.checked:
            self._log("App", "WARN", "Hãy chọn ít nhất một profile")
            return

        try:
            ids = tuple(
                profile.profile_id
                for profile in self.controller.profiles
                if profile.profile_id in self.checked
            )
            self._start_profile_ids(ids)

        except (ValueError, OSError, RuntimeError) as exc:
            showerror("Start profiles", str(exc), parent=self)

    def _start_single(self, profile_id):
        try:
            self.checked.add(profile_id)
            self.selected_profile_id = profile_id
            self._start_profile_ids((profile_id,))

        except (ValueError, OSError, RuntimeError) as exc:
            showerror("Start profile", str(exc), parent=self)

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
            pending = self.creation_events.get(
                profile_id
            )
            if pending is not None:
                pending.set()

            worker = self.controller.workers.get(profile_id)

            if worker is None:
                if pending is not None:
                    self.statuses[
                        profile_id
                    ] = "Stopping"
                continue

            hwnd = worker.context.window_handle
            if hwnd:
                try:
                    set_window_topmost(hwnd, False)
                except (ValueError, OSError):
                    pass

            self.statuses[profile_id] = "Stopping"

        self._refresh()

    # ------------------------------------------------------------------
    # WINDOW LAYOUT
    # ------------------------------------------------------------------

    def _window_items(self, include_reveal=False):
        import win32gui

        items = []

        for worker in self.controller.workers.values():
            context = worker.context
            hwnd = context.window_handle

            if not hwnd or not win32gui.IsWindow(hwnd):
                continue

            left, top, right, bottom = (
                win32gui.GetWindowRect(hwnd)
            )

            width = right - left
            height = bottom - top

            if include_reveal:
                reveal = boss_scan_reveal_height(
                    hwnd
                )
                items.append(
                    (
                        hwnd,
                        width,
                        height,
                        reveal,
                    )
                )
            else:
                items.append(
                    (
                        hwnd,
                        width,
                        height,
                    )
                )

        return items

    @staticmethod
    def _working_area(hwnd):
        import win32api
        import win32con

        monitor = win32api.MonitorFromWindow(
            hwnd,
            win32con.MONITOR_DEFAULTTONEAREST,
        )
        left, top, right, bottom = (
            win32api.GetMonitorInfo(
                monitor
            )["Work"]
        )

        return (
            left,
            top,
            right - left,
            bottom - top,
        )

    @staticmethod
    def _move_placements(placements):
        import win32con
        import win32gui

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
                win32con.SWP_NOSIZE
                | win32con.SWP_NOACTIVATE,
            )

    def _arrange_windows(self, remember=True):
        if remember:
            self.window_layout_mode = "arrange"

        clear_boss_stack_order()

        items = self._window_items(
            include_reveal=False,
        )

        if not items:
            return

        working_area = self._working_area(
            items[0][0]
        )

        signature = (
            "arrange",
            tuple(items),
            working_area,
        )

        if (
            not remember
            and signature
            == self._last_layout_signature
        ):
            return

        placements = arrange_windows(
            items,
            working_area,
        )

        self._move_placements(
            placements
        )
        self._last_layout_signature = signature

        left, top, width, height = working_area
        right = left + width
        bottom = top + height

        if any(
            place.overlap
            or place.y + place.height > bottom
            or place.x + place.width > right
            for place in placements
        ):
            self._log(
                "App",
                "WARN",
                "Không đủ diện tích để xếp tất cả cửa sổ không chồng lấn",
            )

    def _stack_windows(self, remember=True):
        if remember:
            self.window_layout_mode = "stack"

        items = self._window_items(
            include_reveal=True,
        )

        if not items:
            clear_boss_stack_order()
            return

        working_area = self._working_area(
            items[0][0]
        )

        signature = (
            "stack",
            tuple(items),
            working_area,
        )

        if (
            not remember
            and signature
            == self._last_layout_signature
        ):
            return

        placements = stack_windows_for_boss(
            items,
            working_area,
        )

        # Move in back-to-front order. Every following window covers the
        # lower area of the previous window while leaving its boss strip.
        self._move_placements(
            placements
        )

        set_boss_stack_order(
            [
                place.hwnd
                for place in placements
            ]
        )
        self._last_layout_signature = signature

        if any(
            place.overlap
            for place in placements
        ):
            self._log(
                "App",
                "WARN",
                "Không đủ diện tích cho toàn bộ cụm chồng; một số cửa sổ có thể tràn màn hình",
            )

    def _apply_window_layout(self):
        if self.window_layout_mode == "stack":
            self._stack_windows(
                remember=False,
            )
        else:
            self._arrange_windows(
                remember=False,
            )

    # ------------------------------------------------------------------
    # LOG / STATE
    # ------------------------------------------------------------------

    def _format_log_entry(self, profile, state, message=""):
        state_text = str(state).upper()
        line = f"[{datetime.now():%H:%M:%S}] [{state_text}]  {profile}"
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

        return line, tag

    def _append_log_batch(self, entries):
        if not entries:
            return

        self.log.configure(state="normal")

        for line, tag in entries:
            try:
                self.log._textbox.insert(
                    "end",
                    line,
                    tag,
                )
            except Exception:
                self.log.insert(
                    "end",
                    line,
                )

        self._log_line_count += len(entries)

        # Trim in chunks so long-running multi-profile sessions never make
        # the Tk Text widget grow without bound.
        while self._log_line_count > 1000:
            self.log.delete(
                "1.0",
                "201.0",
            )
            self._log_line_count -= 200

        self.log.see("end")
        self.log.configure(state="disabled")

    def _flush_worker_logs(self):
        entries = []

        while len(entries) < 200:
            try:
                profile_id, message = (
                    self._log_queue.get_nowait()
                )
            except queue.Empty:
                break

            entries.append(
                self._format_log_entry(
                    profile_id,
                    "INFO",
                    message,
                )
            )

        self._append_log_batch(entries)

        if self.winfo_exists():
            self._log_flush_after_id = self.after(
                100,
                self._flush_worker_logs,
            )

    def _log(self, profile, state, message=""):
        self._append_log_batch(
            [
                self._format_log_entry(
                    profile,
                    state,
                    message,
                )
            ]
        )

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self._log_line_count = 0

    def _worker_log(self, profile_id, message):
        self._log(profile_id, "INFO", message)

    def _worker_status(self, profile_id, state):
        pretty = state.replace("_", " ").title()
        self._apply_runtime_status(
            profile_id,
            pretty,
        )
        self._log(profile_id, state)

        if state in (
            "WAITING_STARTUP",
            "WAITING_GAME",
            "CONFIRMING_IN_GAME",
        ):
            self._apply_window_layout()

    def _worker_error(self, profile_id, exc):
        self._apply_runtime_status(
            profile_id,
            "Error",
        )
        self._log(profile_id, "ERROR", str(exc))

    def _status(self, profile_id, state):
        self._apply_runtime_status(
            profile_id,
            state,
        )
        self._log(profile_id, state)

    def _error(self, profile_id, exc):
        self._apply_runtime_status(
            profile_id,
            "Error",
        )
        self._log(profile_id, "ERROR", str(exc))

    def _close(self):
        clear_boss_stack_order()

        if self._log_flush_after_id is not None:
            try:
                self.after_cancel(
                    self._log_flush_after_id
                )
            except tk.TclError:
                pass
            self._log_flush_after_id = None

        for event in self.creation_events.values():
            event.set()

        self.controller.stop_selected(
            tuple(self.controller.workers)
        )

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
