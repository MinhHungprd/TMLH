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
import pywintypes

from account_import_dialog import AccountImportDialog
from app_settings import AppSettings, AppSettingsStorage
from auto_login import AutoLoginRunner
from automation_constants import RESOLUTIONS
from boss.enter_boss import BOSSES
from game_automation import AutomationWorker
from profile_auth import (
    clear_current_auth_values,
    clear_profile_auth_if_current,
    save_profile_auth,
)
from profile_credentials import (
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
from ui_dialogs import keep_above_game
from ui_components import CompactCard, ProfileRow, PROFILE_COLUMN_WIDTHS
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

    def prepare_account(
        self,
        username,
        server,
        source_path,
    ):
        username = str(
            username
        ).strip()

        with self._storage_lock:
            if any(
                (
                    item.account_username
                    or item.profile_name
                ).strip().casefold()
                == username.casefold()
                for item in self.profiles
            ):
                raise ValueError(
                    f"Tài khoản đã tồn tại: {username}"
                )

            profile = self.manager.prepare_profile(
                username,
                Path(source_path),
                self.profiles,
                account_username=username,
                server=server,
            )
            self.profiles.append(profile)
            self.profile_store.save(
                self.profiles
            )
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
                except (
                    pywintypes.error,
                    ValueError,
                    OSError,
                ):
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
        self.sort_mode = tk.StringVar(value="A→Z")
        self.search_text = tk.StringVar()
        self.status_filter = tk.StringVar(
            value="Tất cả"
        )
        self.batch_size = tk.StringVar(
            value="480x270"
        )
        self.batch_boss = tk.StringVar(
            value=BOSS_LABELS.get(
                "trom_cho",
                "Trộm chó",
            )
        )
        self.layout_mode_var = tk.StringVar(
            value=(
                "Chồng"
                if settings.window_layout_mode
                == "stack"
                else "Xếp"
            )
        )

        self.statuses = {}
        self.checked = set()
        self.creation_events = {}
        self.profile_rows = {}
        self.selected_profile_id = None
        self.window_layout_mode = (
            settings.window_layout_mode
        )
        self._last_layout_signature = None
        self._suspend_keep_above = False
        self._auto_login_active = set()
        self._login_fields_profile_id = None
        self._login_contexts = {}
        self._login_queue = []
        self._login_queue_active = False
        self._pending_delete_ids = ()
        self._log_expanded = False

        # Worker threads never call Tk APIs directly. Status/error events
        # and verbose logs cross into the GUI through thread-safe queues and
        # are consumed only by the Tk main thread.
        self._worker_event_queue = queue.SimpleQueue()
        self._worker_event_flush_after_id = None
        self._batch_worker_ui = False
        self._batch_refresh_needed = False
        self._batch_layout_needed = False

        self._log_queue = queue.SimpleQueue()
        self._log_line_count = 0
        self._log_flush_after_id = None
        self._closing = False

        self.title(f"{APP_NAME} - Profile Bot")
        self.geometry("640x700")
        self.minsize(600, 640)
        self.configure(fg_color=COLORS["bg"])
        # Keep the dashboard above TOPMOST game windows. We no longer call
        # lift() periodically, which was the part that covered CTk dropdowns.
        self.attributes("-topmost", True)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        self._build_header()
        self._build_source_bar()
        self._build_create_bar()
        self._build_notice_bar()
        self._build_profile_panel()
        self._build_action_bar()
        self._build_log_panel()
        self._build_footer()

        self._worker_event_flush_after_id = self.after(
            50,
            self._flush_worker_events,
        )
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

    def _begin_dropdown_interaction(
        self,
        _event=None,
    ):
        """
        CTk dropdown menus are separate native windows. Temporarily release
        launcher TOPMOST while a dropdown is open so the launcher cannot
        cover its own menu above TOPMOST game windows.
        """
        try:
            self.attributes(
                "-topmost",
                False,
            )
        except tk.TclError:
            pass

    def _end_dropdown_interaction(
        self,
    ):
        try:
            if self.winfo_exists():
                self.attributes(
                    "-topmost",
                    True,
                )
        except tk.TclError:
            pass

    def _bind_dropdown_safety(
        self,
        combo,
    ):
        combo.bind(
            "<Button-1>",
            self._begin_dropdown_interaction,
            add="+",
        )

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
            font=ctk.CTkFont(size=11),
        ).pack(side="left", padx=(6, 0), pady=(4, 0))

        self.header_status = ctk.CTkLabel(
            header,
            text="●  Sẵn sàng",
            text_color=COLORS["green"],
            font=ctk.CTkFont(size=12, weight="bold"),
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
            font=ctk.CTkFont(size=12, weight="bold"),
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
            font=ctk.CTkFont(size=12),
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
            font=ctk.CTkFont(size=11, weight="bold"),
            width=50,
            anchor="w",
        )
        self.source_status.grid(row=0, column=3, padx=(4, 8), pady=7)

    def _build_create_bar(self):
        card = CompactCard(
            self,
            height=58,
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
            1,
            weight=1,
        )

        ctk.CTkButton(
            card,
            text="+ Nhập tài khoản",
            width=132,
            height=34,
            fg_color=COLORS["green"],
            hover_color=COLORS["green_hover"],
            text_color=COLORS["black"],
            font=ctk.CTkFont(
                size=12,
                weight="bold",
            ),
            command=self._open_account_import,
        ).grid(
            row=0,
            column=0,
            padx=(10, 8),
            pady=11,
        )

        self.account_summary = ctk.CTkLabel(
            card,
            text=(
                "Tài khoản tự tạo profile riêng. "
                "Xóa tài khoản sẽ xóa luôn clone."
            ),
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=11),
            anchor="w",
        )
        self.account_summary.grid(
            row=0,
            column=1,
            sticky="ew",
            padx=4,
        )

        ctk.CTkButton(
            card,
            text="Proxy",
            width=68,
            height=30,
            fg_color=COLORS["surface_soft"],
            hover_color=COLORS["border_bright"],
            font=ctk.CTkFont(
                size=11,
                weight="bold",
            ),
            command=self._open_proxy_dialog,
        ).grid(
            row=0,
            column=2,
            padx=(6, 10),
        )

    def _build_notice_bar(self):
        self.notice_card = ctk.CTkFrame(
            self,
            height=42,
            fg_color=COLORS["surface_alt"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=9,
        )
        self.notice_card.grid(
            row=3,
            column=0,
            sticky="ew",
            padx=8,
            pady=3,
        )
        self.notice_card.grid_propagate(False)
        self.notice_card.grid_columnconfigure(
            0,
            weight=1,
        )

        self.notice_label = ctk.CTkLabel(
            self.notice_card,
            text="Sẵn sàng.",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(
                size=11,
                weight="bold",
            ),
            anchor="w",
        )
        self.notice_label.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=(12, 6),
            pady=9,
        )

        self.notice_actions = ctk.CTkFrame(
            self.notice_card,
            fg_color="transparent",
        )
        self.notice_actions.grid(
            row=0,
            column=1,
            sticky="e",
            padx=(4, 10),
            pady=6,
        )

    def _build_profile_panel(self):
        panel = CompactCard(self)
        panel.grid(
            row=4,
            column=0,
            sticky="nsew",
            padx=8,
            pady=3,
        )
        panel.grid_columnconfigure(
            0,
            weight=1,
        )
        panel.grid_rowconfigure(
            2,
            weight=1,
        )

        top = ctk.CTkFrame(
            panel,
            fg_color="transparent",
            height=42,
        )
        top.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=10,
            pady=(8, 4),
        )
        top.grid_columnconfigure(
            1,
            weight=1,
        )

        self.profile_title = ctk.CTkLabel(
            top,
            text="Tài khoản (0)",
            text_color=COLORS["text"],
            font=ctk.CTkFont(
                size=13,
                weight="bold",
            ),
        )
        self.profile_title.grid(
            row=0,
            column=0,
            sticky="w",
            padx=(2, 8),
        )

        self.search_entry = ctk.CTkEntry(
            top,
            textvariable=self.search_text,
            height=30,
            fg_color=COLORS["input"],
            border_color=COLORS["border_bright"],
            placeholder_text="Tìm tài khoản...",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=12),
        )
        self.search_entry.grid(
            row=0,
            column=1,
            sticky="ew",
            padx=4,
        )
        self.search_text.trace_add(
            "write",
            lambda *_args: self._refresh(),
        )

        self.status_filter_combo = ctk.CTkComboBox(
            top,
            variable=self.status_filter,
            values=(
                "Tất cả",
                "Ready",
                "Đang chạy",
                "Chưa đăng nhập",
                "Lỗi",
            ),
            width=116,
            height=30,
            fg_color=COLORS["input"],
            border_color=COLORS["border_bright"],
            dropdown_fg_color=COLORS["surface_alt"],
            dropdown_hover_color=COLORS["surface_soft"],
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=11),
            dropdown_font=ctk.CTkFont(size=11),
            command=lambda _value: (
                self._refresh(),
                self.after(
                    80,
                    self._end_dropdown_interaction,
                ),
            ),
        )
        self._bind_dropdown_safety(
            self.status_filter_combo
        )
        self.status_filter_combo.grid(
            row=0,
            column=2,
            sticky="e",
            padx=(5, 3),
        )

        self.sort_combo = ctk.CTkComboBox(
            top,
            variable=self.sort_mode,
            values=("A→Z", "Z→A", "TT"),
            width=78,
            height=30,
            fg_color=COLORS["input"],
            border_color=COLORS["border_bright"],
            dropdown_fg_color=COLORS["surface_alt"],
            dropdown_hover_color=COLORS["surface_soft"],
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=11),
            dropdown_font=ctk.CTkFont(size=11),
            command=lambda _value: (
                self._refresh(),
                self.after(
                    80,
                    self._end_dropdown_interaction,
                ),
            ),
        )
        self._bind_dropdown_safety(
            self.sort_combo
        )
        self.sort_combo.grid(
            row=0,
            column=3,
            sticky="e",
            padx=(3, 2),
        )

        header = ctk.CTkFrame(
            panel,
            fg_color=COLORS["surface_soft"],
            corner_radius=8,
            height=34,
        )
        header.grid(
            row=1,
            column=0,
            sticky="ew",
            padx=8,
            pady=(0, 4),
        )
        header.grid_propagate(False)

        columns = (
            ("", PROFILE_COLUMN_WIDTHS[0]),
            ("Tài khoản", PROFILE_COLUMN_WIDTHS[1]),
            ("SV", PROFILE_COLUMN_WIDTHS[2]),
            ("Boss", PROFILE_COLUMN_WIDTHS[3]),
            ("Size", PROFILE_COLUMN_WIDTHS[4]),
            ("Trạng thái", PROFILE_COLUMN_WIDTHS[5]),
            ("", PROFILE_COLUMN_WIDTHS[6]),
        )

        for index, (label, width) in enumerate(
            columns
        ):
            header.grid_columnconfigure(
                index,
                minsize=width,
                weight=1 if index == 1 else 0,
            )
            ctk.CTkLabel(
                header,
                text=label,
                text_color=COLORS["muted"],
                font=ctk.CTkFont(
                    size=11,
                    weight="bold",
                ),
                anchor="w",
            ).grid(
                row=0,
                column=index,
                sticky="ew",
                padx=3,
                pady=7,
            )

        self.rows_frame = ctk.CTkScrollableFrame(
            panel,
            fg_color="transparent",
            corner_radius=0,
            scrollbar_button_color=COLORS["surface_soft"],
            scrollbar_button_hover_color=COLORS["border_bright"],
        )
        self.rows_frame.grid(
            row=2,
            column=0,
            sticky="nsew",
            padx=4,
            pady=1,
        )
        self.rows_frame.grid_columnconfigure(
            0,
            weight=1,
        )

        bulk = ctk.CTkFrame(
            panel,
            fg_color="transparent",
            height=38,
        )
        bulk.grid(
            row=3,
            column=0,
            sticky="ew",
            padx=10,
            pady=(2, 6),
        )
        bulk.grid_columnconfigure(
            0,
            weight=1,
        )

        self.selected_label = ctk.CTkLabel(
            bulk,
            text="Đã chọn 0",
            text_color=COLORS["text"],
            font=ctk.CTkFont(
                size=11,
                weight="bold",
            ),
        )
        self.selected_label.grid(
            row=0,
            column=0,
            sticky="w",
        )

        ctk.CTkButton(
            bulk,
            text="Chọn tất cả",
            width=82,
            height=25,
            fg_color=COLORS["surface_soft"],
            hover_color=COLORS["border_bright"],
            font=ctk.CTkFont(size=11),
            command=self._select_all,
        ).grid(
            row=0,
            column=1,
            padx=3,
        )

        ctk.CTkButton(
            bulk,
            text="Bỏ chọn",
            width=68,
            height=25,
            fg_color=COLORS["surface_soft"],
            hover_color=COLORS["border_bright"],
            font=ctk.CTkFont(size=11),
            command=self._clear_selection,
        ).grid(
            row=0,
            column=2,
            padx=3,
        )

    def _build_action_bar(self):
        bar = CompactCard(
            self,
            height=82,
        )
        bar.grid(
            row=5,
            column=0,
            sticky="ew",
            padx=8,
            pady=3,
        )
        bar.grid_propagate(False)

        for index in range(8):
            bar.grid_columnconfigure(
                index,
                weight=1
                if index in (1, 3)
                else 0,
            )

        ctk.CTkLabel(
            bar,
            text="Boss",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(
                size=11,
                weight="bold",
            ),
        ).grid(
            row=0,
            column=0,
            padx=(10, 3),
            pady=(8, 3),
        )

        self.batch_boss_combo = ctk.CTkComboBox(
            bar,
            variable=self.batch_boss,
            values=tuple(
                BOSS_LABELS.get(key, key)
                for key in BOSSES
            ),
            height=28,
            fg_color=COLORS["input"],
            border_color=COLORS["border_bright"],
            dropdown_fg_color=COLORS["surface_alt"],
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=11),
            dropdown_font=ctk.CTkFont(size=11),
        )
        self._bind_dropdown_safety(
            self.batch_boss_combo
        )
        self.batch_boss_combo.configure(
            command=lambda _value:
            self.after(
                80,
                self._end_dropdown_interaction,
            )
        )
        self.batch_boss_combo.grid(
            row=0,
            column=1,
            sticky="ew",
            padx=3,
            pady=(8, 3),
        )

        ctk.CTkLabel(
            bar,
            text="Size",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(
                size=11,
                weight="bold",
            ),
        ).grid(
            row=0,
            column=2,
            padx=(6, 3),
            pady=(8, 3),
        )

        self.batch_size_combo = ctk.CTkComboBox(
            bar,
            variable=self.batch_size,
            values=SIZES,
            height=28,
            fg_color=COLORS["input"],
            border_color=COLORS["border_bright"],
            dropdown_fg_color=COLORS["surface_alt"],
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=11),
            dropdown_font=ctk.CTkFont(size=11),
        )
        self._bind_dropdown_safety(
            self.batch_size_combo
        )
        self.batch_size_combo.configure(
            command=lambda _value:
            self.after(
                80,
                self._end_dropdown_interaction,
            )
        )
        self.batch_size_combo.grid(
            row=0,
            column=3,
            sticky="ew",
            padx=3,
            pady=(8, 3),
        )

        self.layout_combo = ctk.CTkComboBox(
            bar,
            variable=self.layout_mode_var,
            values=("Xếp", "Chồng"),
            width=84,
            height=28,
            fg_color=COLORS["input"],
            border_color=COLORS["border_bright"],
            dropdown_fg_color=COLORS["surface_alt"],
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=11),
            dropdown_font=ctk.CTkFont(size=11),
            command=lambda value: (
                self._layout_mode_changed(
                    value
                ),
                self.after(
                    80,
                    self._end_dropdown_interaction,
                ),
            ),
        )
        self._bind_dropdown_safety(
            self.layout_combo
        )
        self.layout_combo.grid(
            row=0,
            column=4,
            padx=(6, 3),
            pady=(8, 3),
        )

        ctk.CTkButton(
            bar,
            text="Áp dụng",
            width=76,
            height=28,
            fg_color=COLORS["purple"],
            hover_color=COLORS["purple_hover"],
            font=ctk.CTkFont(
                size=11,
                weight="bold",
            ),
            command=self._apply_batch_options,
        ).grid(
            row=0,
            column=5,
            padx=3,
            pady=(8, 3),
        )

        actions = ctk.CTkFrame(
            bar,
            fg_color="transparent",
        )
        actions.grid(
            row=1,
            column=0,
            columnspan=8,
            sticky="ew",
            padx=8,
            pady=(3, 8),
        )

        for text_value, color, command in (
            ("▶ Login", COLORS["green"], self._login_selected_accounts),
            ("▶ Start", COLORS["cyan"], self._start_selected),
            ("■ Stop", COLORS["surface_soft"], self._stop_selected),
            ("Xóa", "#35121B", self._delete_selected),
        ):
            ctk.CTkButton(
                actions,
                text=text_value,
                height=28,
                fg_color=color,
                hover_color=COLORS["border_bright"],
                text_color=(
                    COLORS["black"]
                    if color in (
                        COLORS["green"],
                        COLORS["cyan"],
                    )
                    else COLORS["text"]
                ),
                font=ctk.CTkFont(
                    size=11,
                    weight="bold",
                ),
                command=command,
            ).pack(
                side="left",
                fill="x",
                expand=True,
                padx=3,
            )

    def _open_proxy_dialog(self):
        try:
            settings = (
                self.proxy_settings_store.load()
            )
        except RuntimeError as exc:
            self._notify(
                f"Proxy: {exc}",
                "error",
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
        self.log_panel = CompactCard(
            self,
            height=42,
        )
        self.log_panel.grid(
            row=6,
            column=0,
            sticky="ew",
            padx=8,
            pady=3,
        )
        self.log_panel.grid_propagate(
            False
        )
        self.log_panel.grid_columnconfigure(
            0,
            weight=1,
        )

        top = ctk.CTkFrame(
            self.log_panel,
            fg_color="transparent",
            height=34,
        )
        top.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=12,
            pady=(5, 3),
        )
        top.grid_columnconfigure(
            0,
            weight=1,
        )

        ctk.CTkLabel(
            top,
            text="Nhật ký",
            text_color=COLORS["text"],
            font=ctk.CTkFont(
                size=12,
                weight="bold",
            ),
        ).grid(
            row=0,
            column=0,
            sticky="w",
        )

        ctk.CTkButton(
            top,
            text="Xóa",
            width=46,
            height=24,
            fg_color=COLORS["surface_soft"],
            hover_color=COLORS["border_bright"],
            font=ctk.CTkFont(size=11),
            command=self._clear_log,
        ).grid(
            row=0,
            column=1,
            padx=4,
        )

        self.log_toggle_button = (
            ctk.CTkButton(
                top,
                text="Mở",
                width=46,
                height=24,
                fg_color=COLORS["surface_soft"],
                hover_color=COLORS["border_bright"],
                font=ctk.CTkFont(size=11),
                command=self._toggle_log_panel,
            )
        )
        self.log_toggle_button.grid(
            row=0,
            column=2,
        )

        self.log = ctk.CTkTextbox(
            self.log_panel,
            height=86,
            fg_color=COLORS["black"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=8,
            text_color=COLORS["muted"],
            font=("Consolas", 11),
            wrap="word",
        )
        self.log.grid(
            row=1,
            column=0,
            sticky="ew",
            padx=10,
            pady=(0, 10),
        )
        self.log.grid_remove()
        self.log.configure(
            state="disabled"
        )

        try:
            self.log._textbox.tag_configure(
                "info",
                foreground=COLORS["blue"],
            )
            self.log._textbox.tag_configure(
                "success",
                foreground=COLORS["green"],
            )
            self.log._textbox.tag_configure(
                "warning",
                foreground=COLORS["amber"],
            )
            self.log._textbox.tag_configure(
                "error",
                foreground=COLORS["red"],
            )
        except Exception:
            pass

    def _toggle_log_panel(self):
        self._log_expanded = (
            not self._log_expanded
        )

        if self._log_expanded:
            self.log_panel.configure(
                height=142
            )
            self.log.grid()
            self.log_toggle_button.configure(
                text="Đóng"
            )
        else:
            self.log.grid_remove()
            self.log_panel.configure(
                height=42
            )
            self.log_toggle_button.configure(
                text="Mở"
            )

    def _build_footer(self):
        footer = ctk.CTkFrame(
            self,
            height=24,
            corner_radius=0,
            fg_color=COLORS["sidebar"],
        )
        footer.grid(row=7, column=0, sticky="ew", pady=(3, 0))
        footer.grid_propagate(False)
        footer.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            footer,
            text="◇  TMLH Bot",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=11, weight="bold"),
        ).grid(row=0, column=0, padx=12)

        ctk.CTkLabel(
            footer,
            text="Smart play",
            text_color=COLORS["muted_dark"],
            font=ctk.CTkFont(size=11),
        ).grid(row=0, column=1)

        ctk.CTkLabel(
            footer,
            text="TMLH",
            text_color=COLORS["muted_dark"],
            font=ctk.CTkFont(size=11),
        ).grid(row=0, column=2, padx=12)

    # ------------------------------------------------------------------
    # ACCOUNT LIST / BATCH WORKFLOW
    # ------------------------------------------------------------------

    def _runtime_status(self, profile):
        runtime = self.statuses.get(
            profile.profile_id
        )
        if runtime:
            return runtime

        try:
            self.controller.manager.check_clone(
                profile
            )
            return (
                "Ready"
                if profile.login_ready
                else "Chưa đăng nhập"
            )
        except MissingGameFilesError:
            return "Error / Missing Files"

    @staticmethod
    def _account_name(profile):
        return (
            getattr(
                profile,
                "account_username",
                "",
            ).strip()
            or profile.profile_name
        )

    def _sorted_profiles(self):
        profiles = list(
            self.controller.profiles
        )
        query = (
            self.search_text.get()
            .strip()
            .casefold()
        )

        if query:
            profiles = [
                profile
                for profile in profiles
                if (
                    query
                    in self._account_name(
                        profile
                    ).casefold()
                    or query
                    in self._runtime_status(
                        profile
                    ).casefold()
                )
            ]

        status_filter = (
            self.status_filter.get()
            .strip()
        )

        if status_filter != "Tất cả":
            def include_status(profile):
                status = self._runtime_status(
                    profile
                ).casefold()

                if status_filter == "Ready":
                    return status == "ready"

                if status_filter == "Đang chạy":
                    return status not in {
                        "ready",
                        "stopped",
                        "chưa đăng nhập",
                        "error",
                        "error / missing files",
                    }

                if status_filter == "Chưa đăng nhập":
                    return (
                        "chưa đăng nhập"
                        in status
                    )

                if status_filter == "Lỗi":
                    return (
                        "error"
                        in status
                        or "missing"
                        in status
                    )

                return True

            profiles = [
                profile
                for profile in profiles
                if include_status(
                    profile
                )
            ]

        mode = self.sort_mode.get()

        if mode == "Z→A":
            return sorted(
                profiles,
                key=lambda item:
                self._account_name(
                    item
                ).casefold(),
                reverse=True,
            )

        if mode == "TT":
            return sorted(
                profiles,
                key=lambda item: (
                    self._runtime_status(
                        item
                    ).casefold(),
                    self._account_name(
                        item
                    ).casefold(),
                ),
            )

        return sorted(
            profiles,
            key=lambda item:
            self._account_name(
                item
            ).casefold(),
        )

    def _refresh(self):
        if not hasattr(
            self,
            "rows_frame",
        ):
            return

        valid_ids = {
            profile.profile_id
            for profile
            in self.controller.profiles
        }
        self.checked.intersection_update(
            valid_ids
        )

        if self.selected_profile_id not in valid_ids:
            self.selected_profile_id = None

        for child in (
            self.rows_frame
            .winfo_children()
        ):
            child.destroy()

        self.profile_rows.clear()
        profiles = self._sorted_profiles()

        self.profile_title.configure(
            text=(
                f"Tài khoản "
                f"({len(self.controller.profiles)})"
            )
        )
        self.selected_label.configure(
            text=(
                f"Đã chọn "
                f"{len(self.checked)}"
            )
        )

        for index, profile in enumerate(
            profiles
        ):
            row = ProfileRow(
                self.rows_frame,
                profile,
                self._runtime_status(
                    profile
                ),
                checked=(
                    profile.profile_id
                    in self.checked
                ),
                focused=(
                    profile.profile_id
                    == self.selected_profile_id
                ),
                on_toggle=self._toggle_profile,
                on_focus=self._focus_profile,
                on_more=self._account_more,
            )
            row.grid(
                row=index,
                column=0,
                sticky="ew",
                pady=2,
            )
            self.profile_rows[
                profile.profile_id
            ] = row

        if not profiles:
            message = (
                "Không tìm thấy tài khoản."
                if self.controller.profiles
                else (
                    "Chưa có tài khoản. "
                    "Bấm '+ Nhập tài khoản' để bắt đầu."
                )
            )
            ctk.CTkLabel(
                self.rows_frame,
                text=message,
                text_color=COLORS["muted"],
                font=ctk.CTkFont(size=12),
            ).grid(
                row=0,
                column=0,
                pady=34,
            )

        self.account_summary.configure(
            text=(
                f"{len(self.controller.profiles)} tài khoản • "
                "profile được tạo/xóa tự động theo tài khoản"
            )
        )
        self._update_header_status()

    def _update_header_status(self):
        running = sum(
            1
            for worker
            in self.controller.workers.values()
            if not worker.context.stop_event.is_set()
            and worker.context.state
            not in (
                "STOPPED",
                "ERROR",
            )
        )
        logging_in = len(
            self._auto_login_active
        )

        if running:
            text_value = (
                f"● {running} đang chạy"
            )
        elif logging_in:
            text_value = (
                f"● {logging_in} đang login"
            )
        else:
            text_value = "● Sẵn sàng"

        self.header_status.configure(
            text=text_value,
            text_color=COLORS["green"],
        )

    def _apply_runtime_status(
        self,
        profile_id,
        status,
    ):
        self.statuses[
            profile_id
        ] = status

        row = self.profile_rows.get(
            profile_id
        )

        if (
            self.sort_mode.get()
            == "TT"
            or row is None
        ):
            if self._batch_worker_ui:
                self._batch_refresh_needed = True
            else:
                self._refresh()
        else:
            row.set_status(
                status
            )

            if not self._batch_worker_ui:
                self._update_header_status()

    def _focus_profile(
        self,
        profile_id,
    ):
        self.selected_profile_id = (
            profile_id
        )
        self._refresh()

    def _toggle_profile(
        self,
        profile_id,
        enabled,
    ):
        if enabled:
            self.checked.add(
                profile_id
            )
            self.selected_profile_id = (
                profile_id
            )
        else:
            self.checked.discard(
                profile_id
            )

        self._refresh()

    def _select_all(self):
        # Select the currently visible/filter-matched accounts.
        self.checked.update(
            profile.profile_id
            for profile
            in self._sorted_profiles()
        )
        self._refresh()

    def _clear_selection(self):
        self.checked.clear()
        self._refresh()

    def _selected_ids(self):
        return tuple(
            profile.profile_id
            for profile
            in self.controller.profiles
            if profile.profile_id
            in self.checked
        )

    def _clear_notice_actions(self):
        for child in (
            self.notice_actions
            .winfo_children()
        ):
            child.destroy()

    def _notify(
        self,
        message,
        level="info",
        *,
        actions=(),
    ):
        palette = {
            "success": COLORS["green"],
            "warning": COLORS["amber"],
            "error": COLORS["red"],
            "info": COLORS["cyan"],
        }
        self.notice_label.configure(
            text=message,
            text_color=palette.get(
                level,
                COLORS["text"],
            ),
        )
        self._clear_notice_actions()

        for label, callback, color in actions:
            ctk.CTkButton(
                self.notice_actions,
                text=label,
                width=72,
                height=27,
                fg_color=color,
                hover_color=COLORS["border_bright"],
                font=ctk.CTkFont(
                    size=11,
                    weight="bold",
                ),
                command=callback,
            ).pack(
                side="left",
                padx=3,
            )

    def _account_more(
        self,
        profile_id,
    ):
        try:
            profile = self.controller.get(
                profile_id
            )
        except StopIteration:
            return

        self.selected_profile_id = (
            profile_id
        )
        username = self._account_name(
            profile
        )

        self._notify(
            f"{username} • thao tác riêng",
            "info",
            actions=(
                (
                    "Login",
                    lambda pid=profile_id:
                    self._login_accounts(
                        (pid,)
                    ),
                    COLORS["green"],
                ),
                (
                    "Xóa",
                    lambda pid=profile_id:
                    self._request_delete(
                        (pid,)
                    ),
                    "#35121B",
                ),
            ),
        )
        self._refresh()

    def _open_account_import(self):
        source = (
            self.source.get()
            .strip()
        )

        if not source:
            self._notify(
                "Hãy chọn thư mục Game gốc trước khi nhập tài khoản.",
                "warning",
            )
            return

        existing = [
            self._account_name(
                profile
            )
            for profile
            in self.controller.profiles
        ]

        AccountImportDialog(
            self,
            existing_usernames=existing,
            on_import=self._import_accounts,
        )

    def _import_accounts(
        self,
        entries,
        auto_login,
    ):
        source = (
            self.source.get()
            .strip()
        )

        if not source:
            self._notify(
                "Chưa có thư mục Game gốc.",
                "error",
            )
            return

        self.controller.settings_store.save(
            AppSettings(
                source,
                self.window_layout_mode,
            )
        )

        # Read Tk variables before starting the worker thread.
        import_size = self.batch_size.get()
        import_boss = BOSS_KEYS.get(
            self.batch_boss.get(),
            self.batch_boss.get(),
        )

        self._notify(
            (
                f"Đang tạo {len(entries)} "
                "tài khoản/profile..."
            ),
            "info",
        )

        def work():
            created_ids = []
            failed = []

            for entry in entries:
                profile = None

                try:
                    profile = (
                        self.controller
                        .prepare_account(
                            entry.username,
                            entry.server,
                            source,
                        )
                    )

                    profile = (
                        self.controller
                        .set_options(
                            profile.profile_id,
                            import_boss,
                            import_size,
                        )
                    )

                    self._queue_ui_call(
                        self._status,
                        profile.profile_id,
                        "Copying Game",
                    )

                    self.controller.manager.clone_profile(
                        profile,
                        Path(source),
                    )
                    save_login_credentials(
                        profile.game_path,
                        entry.credentials(),
                    )
                    created_ids.append(
                        profile.profile_id
                    )

                    self._queue_ui_call(
                        self._status,
                        profile.profile_id,
                        "Chưa đăng nhập",
                    )

                except Exception as exc:
                    # Do not leave a broken account/profile record behind when
                    # clone or credential creation fails halfway through.
                    if profile is not None:
                        try:
                            self.controller.delete_profile(
                                profile.profile_id
                            )
                        except Exception:
                            pass

                    failed.append(
                        (
                            entry.username,
                            str(exc),
                        )
                    )

            self._queue_ui_call(
                self._finish_account_import,
                tuple(created_ids),
                tuple(failed),
                bool(auto_login),
            )

        threading.Thread(
            target=work,
            daemon=True,
            name="account-import",
        ).start()

    def _finish_account_import(
        self,
        created_ids,
        failed,
        auto_login,
    ):
        self.checked.update(
            created_ids
        )
        self._refresh()

        if failed:
            first = "; ".join(
                f"{name}: {error}"
                for name, error
                in failed[:3]
            )
            self._notify(
                (
                    f"Đã tạo {len(created_ids)} tài khoản; "
                    f"{len(failed)} lỗi. {first}"
                ),
                "warning",
            )
        else:
            self._notify(
                (
                    f"Đã tạo {len(created_ids)} "
                    "tài khoản và profile tương ứng."
                ),
                "success",
            )

        if (
            auto_login
            and created_ids
        ):
            self._login_accounts(
                created_ids
            )

    def _login_selected_accounts(self):
        ids = self._selected_ids()

        if not ids:
            self._notify(
                "Chọn ít nhất một tài khoản để Login.",
                "warning",
            )
            return

        self._login_accounts(
            ids
        )

    @staticmethod
    def _running_profile_games(
        profiles,
    ):
        """Return configured profiles that already have a game process open."""
        import psutil

        profile_roots = [
            (
                profile.profile_id,
                Path(
                    profile.game_path
                ).resolve(),
            )
            for profile in profiles
        ]
        running = []

        for process in psutil.process_iter(
            [
                "name",
                "exe",
            ]
        ):
            try:
                if (
                    str(
                        process.info.get(
                            "name"
                        )
                        or ""
                    ).casefold()
                    != "thienmenhlachong.exe"
                ):
                    continue

                executable = process.info.get(
                    "exe"
                )

                if not executable:
                    continue

                executable = Path(
                    executable
                ).resolve()

                for (
                    profile_id,
                    root,
                ) in profile_roots:
                    if executable.is_relative_to(
                        root
                    ):
                        running.append(
                            profile_id
                        )
                        break

            except (
                psutil.Error,
                OSError,
                ValueError,
            ):
                continue

        return tuple(
            dict.fromkeys(
                running
            )
        )

    def _login_accounts(
        self,
        profile_ids,
    ):
        if self._login_queue_active:
            self._notify(
                "Đang có hàng đợi Login. Hãy chờ hàng đợi hiện tại hoàn tất.",
                "warning",
            )
            return

        active_workers = [
            profile.profile_id
            for profile
            in self.controller.profiles
            if self.controller._worker_is_active(
                profile.profile_id
            )
        ]

        if active_workers:
            self._notify(
                (
                    "Không thể Login account mới khi bot đang chạy profile khác. "
                    "Auth game dùng chung Registry Windows; hãy Stop toàn bộ trước "
                    "để tránh account của profile này đè profile khác."
                ),
                "warning",
            )
            return

        open_games = self._running_profile_games(
            self.controller.profiles
        )

        if open_games:
            shown = ", ".join(
                open_games[:4]
            )
            suffix = (
                ""
                if len(open_games) <= 4
                else f" +{len(open_games) - 4}"
            )
            self._notify(
                (
                    "Đang có tab game mở: "
                    f"{shown}{suffix}. Đóng các tab game trước khi Login hàng loạt. "
                    "Đây là bắt buộc vì auth của game dùng chung Registry Windows."
                ),
                "warning",
            )
            return

        self._login_queue = list(
            profile_ids
        )
        self._login_queue_active = True
        self._suspend_keep_above = True

        self._notify(
            (
                f"Login an toàn {len(self._login_queue)} tài khoản • "
                "mỗi account được mở riêng, lưu auth, đóng game rồi mới tới account kế tiếp"
            ),
            "info",
        )
        self._run_next_login()

    def _run_next_login(self):
        if not self._login_queue:
            self._login_queue_active = False
            self._suspend_keep_above = False
            self._notify(
                "Hàng đợi Login đã hoàn tất.",
                "success",
            )
            self._update_header_status()
            return

        profile_id = (
            self._login_queue.pop(0)
        )

        try:
            profile = self.controller.get(
                profile_id
            )
            self.controller.manager.check_clone(
                profile
            )
            credentials = load_login_credentials(
                profile.game_path
            )

            if credentials is None:
                raise RuntimeError(
                    "Không có credential đã lưu cho tài khoản"
                )

            if profile_id in self._auto_login_active:
                raise RuntimeError(
                    "Tài khoản đang Login"
                )

            self._auto_login_active.add(
                profile_id
            )
            self._run_auto_login(
                profile,
                credentials,
                on_complete=self._run_next_login,
            )

        except Exception as exc:
            self._error(
                profile_id,
                exc,
            )
            self._notify(
                (
                    f"{profile_id}: {exc} • "
                    "tiếp tục tài khoản kế tiếp"
                ),
                "warning",
            )
            self.after(
                100,
                self._run_next_login,
            )

    @staticmethod
    def _close_login_process(
        context,
        *,
        timeout=4.0,
    ):
        """
        Close the game used only for credential enrollment.

        TMLH auth lives in one HKCU Registry path shared by every clone.
        Leaving account A running while logging account B allows A to write
        shared auth again and contaminate B's saved profile auth.
        """
        import psutil
        import win32con
        import win32gui

        hwnd = context.window_handle
        pid = context.process_id

        if (
            hwnd
            and win32gui.IsWindow(
                hwnd
            )
        ):
            try:
                win32gui.PostMessage(
                    hwnd,
                    win32con.WM_CLOSE,
                    0,
                    0,
                )
            except Exception:
                pass

        if not pid:
            return

        try:
            process = psutil.Process(
                pid
            )
        except psutil.Error:
            return

        try:
            process.wait(
                timeout=timeout
            )
            return
        except psutil.TimeoutExpired:
            pass
        except psutil.Error:
            return

        # This process is an enrollment/login client, not an active bot
        # session. Terminate it if Unity ignores WM_CLOSE so the next account
        # cannot inherit a live Registry writer from the previous account.
        try:
            process.terminate()
            process.wait(
                timeout=2.0
            )
        except psutil.TimeoutExpired:
            try:
                process.kill()
                process.wait(
                    timeout=1.0
                )
            except psutil.Error:
                pass
        except psutil.Error:
            pass

    def _run_auto_login(
        self,
        profile,
        credentials,
        *,
        on_complete=None,
    ):
        context = (
            ProfileRuntimeContext
            .from_profile(
                profile
            )
        )
        cancel = context.stop_event
        self._login_contexts[
            profile.profile_id
        ] = context
        self.creation_events[
            profile.profile_id
        ] = cancel

        def post_status(state):
            self._queue_ui_call(
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
            updated = None

            try:
                post_log(
                    (
                        "Auto login isolated: "
                        f"account={credentials.username!r} "
                        f"server={credentials.server!r}"
                    )
                )
                self._queue_ui_call(
                    self._status,
                    profile.profile_id,
                    "Launching Game",
                )

                with PROFILE_LAUNCH_LOCK:
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

                    # The window joins the chosen layout before credential
                    # typing starts, so foreground clicks remain predictable
                    # even with many accounts.
                    self._queue_ui_call(
                        self._apply_window_layout,
                    )
                    cancel.wait(0.25)

                    runner = AutoLoginRunner(
                        on_status=post_status,
                        on_log=post_log,
                    )
                    runner.run(
                        context,
                        credentials,
                    )

                    post_log(
                        "Auto login: đang xác nhận auth..."
                    )
                    deadline = (
                        time.monotonic()
                        + AUTO_LOGIN_AUTH_CONFIRM_TIMEOUT
                    )
                    last_error = None

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
                                "Chưa thấy auth mới trong Registry"
                                + (
                                    f": {last_error}"
                                    if last_error
                                    else ""
                                )
                            )
                        )

                self._queue_ui_call(
                    self._auto_login_success,
                    profile.profile_id,
                    self._account_name(
                        updated
                    ),
                )

            except InterruptedError:
                self._queue_ui_call(
                    self._status,
                    profile.profile_id,
                    "Stopped",
                )

            except Exception as exc:
                self._queue_ui_call(
                    self._error,
                    profile.profile_id,
                    exc,
                )
                self._queue_ui_call(
                    self._notify,
                    (
                        f"{self._account_name(profile)}: "
                        f"{exc}"
                    ),
                    "error",
                )

            finally:
                if callable(
                    on_complete
                ):
                    # Shutdown may write HKCU auth again. Serialize the whole
                    # close/cleanup sequence with normal profile launches so it
                    # cannot race another profile's restore.
                    with PROFILE_LAUNCH_LOCK:
                        try:
                            self._close_login_process(
                                context
                            )
                        except Exception as exc:
                            post_log(
                                (
                                    "Auto login cleanup warning: "
                                    f"{exc}"
                                )
                            )
                        finally:
                            try:
                                cleared = (
                                    clear_profile_auth_if_current(
                                        profile.game_path
                                    )
                                )

                                if not cleared:
                                    post_log(
                                        (
                                            "Registry cleanup skipped: "
                                            "auth belongs to another "
                                            "profile or is already empty"
                                        )
                                    )
                            except Exception as exc:
                                post_log(
                                    (
                                        "Registry cleanup warning: "
                                        f"{exc}"
                                    )
                                )

                            context.window_handle = None
                            context.process_id = None

                self._queue_ui_call(
                    self._auto_login_finished,
                    profile.profile_id,
                    on_complete,
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
        account_name,
    ):
        self._status(
            profile_id,
            "Ready",
        )
        self._log(
            account_name,
            "SUCCESS",
            "Auto login thành công • auth đã lưu",
        )
        self._notify(
            f"✓ {account_name}: đăng nhập thành công.",
            "success",
        )

    def _auto_login_finished(
        self,
        profile_id,
        on_complete=None,
    ):
        self.creation_events.pop(
            profile_id,
            None,
        )
        # Keep the opened game context after login so later accounts can be
        # arranged/chồng together with earlier logged-in windows.
        self._auto_login_active.discard(
            profile_id
        )

        if callable(
            on_complete
        ):
            self._login_contexts.pop(
                profile_id,
                None,
            )

        self._apply_window_layout()

        if callable(on_complete):
            self.after(
                100,
                on_complete,
            )
        else:
            self._suspend_keep_above = bool(
                self._auto_login_active
            )

        self._update_header_status()

    def _apply_batch_options(self):
        ids = self._selected_ids()

        if not ids:
            self._notify(
                "Chọn tài khoản trước khi áp dụng Boss/Size.",
                "warning",
            )
            return

        boss = BOSS_KEYS.get(
            self.batch_boss.get(),
            self.batch_boss.get(),
        )
        size = self.batch_size.get()
        changed = 0
        failures = []

        for profile_id in ids:
            try:
                updated = (
                    self.controller
                    .set_options(
                        profile_id,
                        boss,
                        size,
                    )
                )
                changed += 1

                context = (
                    self._login_contexts
                    .get(profile_id)
                )
                if context is not None:
                    width, height = (
                        int(part)
                        for part
                        in size.split("x")
                    )
                    context.selected_boss = boss
                    context.window_width = width
                    context.window_height = height

                    if context.window_handle:
                        resize_client(
                            context.window_handle,
                            width,
                            height,
                        )

            except Exception as exc:
                failures.append(
                    f"{profile_id}: {exc}"
                )

        self._apply_window_layout()
        self._refresh()

        if failures:
            self._notify(
                (
                    f"Đã áp dụng {changed}/{len(ids)}. "
                    f"Lỗi: {'; '.join(failures[:2])}"
                ),
                "warning",
            )
        else:
            self._notify(
                (
                    f"Đã áp dụng Boss/Size cho "
                    f"{changed} tài khoản."
                ),
                "success",
            )

    def _request_delete(
        self,
        profile_ids,
    ):
        ids = tuple(
            profile_id
            for profile_id
            in profile_ids
            if profile_id
            in {
                item.profile_id
                for item
                in self.controller.profiles
            }
        )

        if not ids:
            self._notify(
                "Không có tài khoản để xóa.",
                "warning",
            )
            return

        self._pending_delete_ids = ids
        self._notify(
            (
                f"Xóa {len(ids)} tài khoản? "
                "Clone game và credential tương ứng cũng sẽ bị xóa."
            ),
            "warning",
            actions=(
                (
                    "Xóa",
                    self._confirm_delete,
                    "#35121B",
                ),
                (
                    "Hủy",
                    self._cancel_delete,
                    COLORS["surface_soft"],
                ),
            ),
        )

    def _delete_selected(self):
        ids = self._selected_ids()

        if not ids:
            self._notify(
                "Chọn ít nhất một tài khoản để xóa.",
                "warning",
            )
            return

        self._request_delete(
            ids
        )

    def _cancel_delete(self):
        self._pending_delete_ids = ()
        self._notify(
            "Đã hủy xóa.",
            "info",
        )

    def _confirm_delete(self):
        ids = self._pending_delete_ids
        self._pending_delete_ids = ()

        if not ids:
            return

        # Request all associated workers/login jobs to stop first.
        self._stop_profile_ids(
            ids
        )

        try:
            import win32con
            import win32gui

            for profile_id in ids:
                context = (
                    self._login_contexts
                    .get(profile_id)
                )
                hwnd = (
                    context.window_handle
                    if context is not None
                    else None
                )

                if (
                    hwnd
                    and win32gui.IsWindow(
                        hwnd
                    )
                ):
                    win32gui.PostMessage(
                        hwnd,
                        win32con.WM_CLOSE,
                        0,
                        0,
                    )
        except Exception:
            pass

        self.after(
            700,
            self._finish_delete,
            ids,
        )

    def _login_window_open(
        self,
        profile_id,
    ):
        context = self._login_contexts.get(
            profile_id
        )

        if (
            context is None
            or not context.window_handle
        ):
            return False

        try:
            import win32gui

            return bool(
                win32gui.IsWindow(
                    context.window_handle
                )
            )
        except Exception:
            return False

    def _finish_delete(
        self,
        ids,
        attempt=0,
    ):
        waiting = [
            profile_id
            for profile_id in ids
            if (
                self.controller
                ._worker_is_active(
                    profile_id
                )
                or profile_id
                in self._auto_login_active
                or self._login_window_open(
                    profile_id
                )
            )
        ]

        if waiting and attempt < 8:
            self._notify(
                (
                    f"Đang dừng {len(waiting)} tài khoản "
                    "trước khi xóa..."
                ),
                "info",
            )
            self.after(
                400,
                self._finish_delete,
                ids,
                attempt + 1,
            )
            return

        deleted = 0
        failures = []

        for profile_id in ids:
            try:
                self.controller.delete_profile(
                    profile_id
                )
                self.statuses.pop(
                    profile_id,
                    None,
                )
                self.checked.discard(
                    profile_id
                )
                self._login_contexts.pop(
                    profile_id,
                    None,
                )
                deleted += 1
            except Exception as exc:
                failures.append(
                    f"{profile_id}: {exc}"
                )

        if self.selected_profile_id in ids:
            self.selected_profile_id = None

        self._refresh()

        if failures:
            self._notify(
                (
                    f"Đã xóa {deleted}/{len(ids)}. "
                    f"{'; '.join(failures[:2])}"
                ),
                "warning",
            )
        else:
            self._notify(
                (
                    f"Đã xóa {deleted} tài khoản "
                    "và clone tương ứng."
                ),
                "success",
            )

    # ------------------------------------------------------------------
    # SOURCE / START / STOP
    # ------------------------------------------------------------------

    def _update_source_status(self):
        value = self.source.get().strip()
        launcher = (
            Path(value)
            / "ThienMenhLacHong_Launcher.exe"
            if value
            else None
        )

        if (
            launcher
            and launcher.is_file()
        ):
            self.source_status.configure(
                text="● OK",
                text_color=COLORS["green"],
            )
        elif value:
            self.source_status.configure(
                text="● Sai",
                text_color=COLORS["red"],
            )
        else:
            self.source_status.configure(
                text="● Chưa",
                text_color=COLORS["muted"],
            )

    def _browse(self):
        selected = filedialog.askdirectory(
            parent=self
        )

        if selected:
            self.source.set(
                selected
            )
            self.controller.settings_store.save(
                AppSettings(
                    selected,
                    self.window_layout_mode,
                )
            )
            self._update_source_status()

    def _queue_ui_call(
        self,
        callback,
        *args,
    ):
        """Queue a Tk/UI callback from any background thread."""
        if self._closing:
            return

        self._worker_event_queue.put(
            (
                "call",
                callback,
                args,
            )
        )

    def _worker_callbacks(self):
        # Tkinter is not thread-safe. Worker callbacks run on automation
        # threads, so they only enqueue plain Python data here. The Tk main
        # thread applies all UI mutations in _flush_worker_events().
        return {
            "on_status":
            lambda pid, state:
            self._worker_event_queue.put(
                (
                    "status",
                    pid,
                    state,
                )
            ),
            "on_error":
            lambda pid, exc:
            self._worker_event_queue.put(
                (
                    "error",
                    pid,
                    exc,
                )
            ),
            "on_log":
            lambda pid, message:
            self._log_queue.put(
                (
                    pid,
                    message,
                )
            ),
        }

    def _start_profile_ids(
        self,
        profile_ids,
    ):
        self._ensure_proxy_routing_current()
        self.controller.start_selected(
            profile_ids,
            **self._worker_callbacks(),
        )
        self._refresh()
        self._apply_window_layout()

    def _start_selected(self):
        if self._login_queue_active:
            self._notify(
                (
                    "Đang Login tài khoản. Chờ hàng đợi Login hoàn tất rồi mới Start "
                    "để không ghi đè Registry auth."
                ),
                "warning",
            )
            return

        ids = self._selected_ids()

        if not ids:
            self._notify(
                "Chọn ít nhất một tài khoản để Start.",
                "warning",
            )
            return

        try:
            self._start_profile_ids(
                ids
            )
            self._notify(
                f"Đã Start {len(ids)} tài khoản.",
                "success",
            )
        except Exception as exc:
            self._notify(
                f"Start lỗi: {exc}",
                "error",
            )

    def _stop_selected(self):
        ids = self._selected_ids()

        if not ids:
            self._notify(
                "Chọn ít nhất một tài khoản để Stop.",
                "warning",
            )
            return

        self._stop_profile_ids(
            ids
        )
        self._notify(
            f"Đang Stop {len(ids)} tài khoản...",
            "info",
        )

    def _stop_single(
        self,
        profile_id,
    ):
        self._stop_profile_ids(
            (profile_id,)
        )

    def _stop_profile_ids(
        self,
        profile_ids,
    ):
        self.controller.stop_selected(
            profile_ids
        )

        for profile_id in profile_ids:
            pending = (
                self.creation_events
                .get(profile_id)
            )
            if pending is not None:
                pending.set()

            context = (
                self._login_contexts
                .get(profile_id)
            )
            if context is not None:
                context.stop_event.set()

            worker = (
                self.controller.workers
                .get(profile_id)
            )

            if worker is None:
                if pending is not None:
                    self.statuses[
                        profile_id
                    ] = "Stopping"
                continue

            hwnd = (
                worker.context
                .window_handle
            )
            if hwnd:
                try:
                    set_window_topmost(
                        hwnd,
                        False,
                    )
                except (
                    pywintypes.error,
                    ValueError,
                    OSError,
                ):
                    pass

            self.statuses[
                profile_id
            ] = "Stopping"

        self._refresh()

    # ------------------------------------------------------------------
    # WINDOW LAYOUT
    # ------------------------------------------------------------------

    def _layout_mode_changed(
        self,
        value,
    ):
        self.window_layout_mode = (
            "stack"
            if value == "Chồng"
            else "arrange"
        )
        self.controller.settings_store.save(
            AppSettings(
                self.source.get().strip(),
                self.window_layout_mode,
            )
        )
        self._apply_window_layout()
        self._notify(
            (
                "Bố cục mặc định: "
                + (
                    "Chồng"
                    if self.window_layout_mode
                    == "stack"
                    else "Xếp"
                )
            ),
            "success",
        )

    def _window_items(self, include_reveal=False):
        import win32gui

        items = []

        contexts = [
            worker.context
            for worker
            in self.controller.workers.values()
        ]
        contexts.extend(
            self._login_contexts.values()
        )

        seen_hwnds = set()

        for context in contexts:
            hwnd = context.window_handle

            if (
                not hwnd
                or hwnd in seen_hwnds
                or not win32gui.IsWindow(hwnd)
            ):
                continue

            seen_hwnds.add(hwnd)

            try:
                left, top, right, bottom = (
                    win32gui.GetWindowRect(hwnd)
                )

                width = right - left
                height = bottom - top

                reveal = (
                    boss_scan_reveal_height(
                        hwnd
                    )
                    if include_reveal
                    else None
                )
            except (
                pywintypes.error,
                OSError,
                ValueError,
            ):
                # Unity can recreate/destroy HWNDs between IsWindow() and the
                # following native calls. Skip that transient handle this pass.
                continue

            if include_reveal:
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
            if not win32gui.IsWindow(
                place.hwnd
            ):
                continue

            try:
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
            except (
                pywintypes.error,
                OSError,
            ):
                # Window was recreated/closed after the validity check.
                continue

    def _arrange_windows(self, remember=True):
        if remember:
            self.window_layout_mode = "arrange"
            self.layout_mode_var.set(
                "Xếp"
            )
            self.controller.settings_store.save(
                AppSettings(
                    self.source.get().strip(),
                    self.window_layout_mode,
                )
            )

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
            self.layout_mode_var.set(
                "Chồng"
            )
            self.controller.settings_store.save(
                AppSettings(
                    self.source.get().strip(),
                    self.window_layout_mode,
                )
            )

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
        if self._closing:
            return

        try:
            if self.window_layout_mode == "stack":
                self._stack_windows(
                    remember=False,
                )
            else:
                self._arrange_windows(
                    remember=False,
                )
        except (
            pywintypes.error,
            OSError,
            ValueError,
        ) as exc:
            # Game windows are volatile while Unity starts/recreates them.
            # A transient HWND/monitor failure should skip one layout pass,
            # not terminate the Tk callback or the management UI.
            self._last_layout_signature = None
            self._log_queue.put(
                (
                    "App",
                    (
                        "Layout skipped after transient "
                        f"window error: {exc}"
                    ),
                )
            )

    # ------------------------------------------------------------------
    # LOG / STATE
    # ------------------------------------------------------------------

    def _flush_worker_events(self):
        if self._closing:
            return

        events = []

        while len(events) < 500:
            try:
                events.append(
                    self._worker_event_queue.get_nowait()
                )
            except queue.Empty:
                break

        self._batch_worker_ui = True
        self._batch_refresh_needed = False
        self._batch_layout_needed = False

        ui_failures = []

        try:
            for kind, target, payload in events:
                try:
                    if kind == "status":
                        self._worker_status(
                            target,
                            payload,
                        )
                    elif kind == "error":
                        self._worker_error(
                            target,
                            payload,
                        )
                    elif kind == "call":
                        target(
                            *payload
                        )
                except tk.TclError:
                    if self._closing:
                        break
                    ui_failures.append(
                        f"{kind}: TclError"
                    )
                except Exception as exc:
                    # Keep the queue alive even if one profile/widget update
                    # races with deletion or a transient native operation.
                    ui_failures.append(
                        (
                            f"{kind}: "
                            f"{type(exc).__name__}: "
                            f"{exc}"
                        )
                    )
        finally:
            self._batch_worker_ui = False

        for message in ui_failures[:10]:
            self._log_queue.put(
                (
                    "App",
                    f"UI event skipped: {message}",
                )
            )

        try:
            if self._batch_refresh_needed:
                self._refresh()
            elif events:
                self._update_header_status()

            if self._batch_layout_needed:
                self._apply_window_layout()
        except tk.TclError:
            if self._closing:
                return
            raise
        finally:
            self._batch_refresh_needed = False
            self._batch_layout_needed = False

        if not self._closing:
            try:
                if self.winfo_exists():
                    self._worker_event_flush_after_id = self.after(
                        50,
                        self._flush_worker_events,
                    )
            except tk.TclError:
                self._worker_event_flush_after_id = None

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
        if (
            not entries
            or self._closing
        ):
            return

        try:
            self.log.configure(
                state="normal"
            )

            inserted = 0

            for line, tag in entries:
                try:
                    self.log._textbox.insert(
                        "end",
                        line,
                        tag,
                    )
                except (
                    AttributeError,
                    tk.TclError,
                ):
                    try:
                        self.log.insert(
                            "end",
                            line,
                        )
                    except tk.TclError:
                        break

                inserted += 1

            self._log_line_count += inserted

            # Trim in chunks so long-running multi-profile sessions never make
            # the Tk Text widget grow without bound.
            while self._log_line_count > 1000:
                self.log.delete(
                    "1.0",
                    "201.0",
                )
                self._log_line_count -= 200

            self.log.see("end")
            self.log.configure(
                state="disabled"
            )
        except tk.TclError:
            # The widget may be destroyed while the periodic flush is pending.
            return

    def _flush_worker_logs(self):
        if self._closing:
            return

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

        self._append_log_batch(
            entries
        )

        if not self._closing:
            try:
                if self.winfo_exists():
                    self._log_flush_after_id = self.after(
                        100,
                        self._flush_worker_logs,
                    )
            except tk.TclError:
                self._log_flush_after_id = None

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

    @staticmethod
    def _friendly_status(
        value,
    ):
        normalized = str(
            value
        ).replace(
            "_",
            " ",
        ).strip().casefold()

        labels = {
            "copying game": "Copy game",
            "launching game": "Mở game",
            "waiting startup": "Chờ startup",
            "waiting game": "Chờ game",
            "waiting gameplay socket": "Chờ socket",
            "login select server": "Chọn server",
            "login enter credentials": "Nhập TK/MK",
            "login submitting": "Đăng nhập",
            "login waiting start": "Chờ Start",
            "entering boss": "Vào boss",
            "waiting boss load": "Load boss",
            "checking boss": "Check boss",
            "boss alive": "Boss sống",
            "boss dead confirming": "Xác nhận chết",
            "boss dead": "Boss chết",
            "exiting boss": "Thoát boss",
            "waiting respawn": "Chờ hồi sinh",
            "in game": "Đang chạy",
            "stopping": "Đang dừng",
            "stopped": "Đã dừng",
            "ready": "Ready",
            "error": "Lỗi",
            "chưa đăng nhập": "Chưa đăng nhập",
        }

        return labels.get(
            normalized,
            str(value).replace(
                "_",
                " ",
            ).title(),
        )

    def _worker_status(self, profile_id, state):
        if state not in (
            "STOPPED",
            "ERROR",
        ):
            self._login_contexts.pop(
                profile_id,
                None,
            )

        pretty = self._friendly_status(
            state
        )
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
            if self._batch_worker_ui:
                self._batch_layout_needed = True
            else:
                self._apply_window_layout()

    def _worker_error(self, profile_id, exc):
        self._apply_runtime_status(
            profile_id,
            "Error",
        )
        self._log(
            profile_id,
            "ERROR",
            str(exc),
        )
        self._notify(
            f"{profile_id}: {exc}",
            "error",
        )

    def _status(self, profile_id, state):
        self._apply_runtime_status(
            profile_id,
            self._friendly_status(
                state
            ),
        )
        self._log(profile_id, state)

    def _error(self, profile_id, exc):
        self._apply_runtime_status(
            profile_id,
            "Error",
        )
        self._log(
            profile_id,
            "ERROR",
            str(exc),
        )
        self._notify(
            f"{profile_id}: {exc}",
            "error",
        )

    def _close(self):
        if self._closing:
            return

        self._closing = True
        clear_boss_stack_order()

        if self._worker_event_flush_after_id is not None:
            try:
                self.after_cancel(
                    self._worker_event_flush_after_id
                )
            except tk.TclError:
                pass
            self._worker_event_flush_after_id = None

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
                except (
                    pywintypes.error,
                    ValueError,
                    OSError,
                ):
                    pass

        for context in self._login_contexts.values():
            hwnd = context.window_handle
            if hwnd:
                try:
                    set_window_topmost(
                        hwnd,
                        False,
                    )
                except (
                    pywintypes.error,
                    ValueError,
                    OSError,
                ):
                    pass

        self.destroy()


if __name__ == "__main__":
    LauncherApp().mainloop()
