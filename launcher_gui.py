"""Compact dark-gaming dashboard for TMLH Bot."""

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
from ui_components import CompactCard, EditProfileDialog, ProfileRow
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
        self.controller = controller or ProfileController(root)
        settings = self.controller.settings_store.load()

        self.source = tk.StringVar(value=settings.game_source_path)
        self.profile_name = tk.StringVar()
        self.sort_mode = tk.StringVar(value="Tên A → Z")

        self.statuses = {}
        self.checked = set()
        self.creation_events = {}
        self.profile_rows = {}
        self.selected_profile_id = None

        self.title(f"{APP_NAME} - Profile Bot")
        self.geometry("500x620")
        self.minsize(480, 560)
        self.configure(fg_color=COLORS["bg"])

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        self._build_header()
        self._build_source_bar()
        self._build_create_bar()
        self._build_profile_panel()
        self._build_action_bar()
        self._build_log_panel()
        self._build_footer()

        self._refresh()
        self._update_source_status()
        self.protocol("WM_DELETE_WINDOW", self._close)

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
            text="🎮  Game",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=10, weight="bold"),
            width=72,
            anchor="w",
        ).grid(row=0, column=0, padx=(10, 4), pady=7)

        self.source_entry = ctk.CTkEntry(
            card,
            textvariable=self.source,
            height=28,
            fg_color=COLORS["input"],
            border_color=COLORS["border_bright"],
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=10),
        )
        self.source_entry.grid(row=0, column=1, sticky="ew", padx=3, pady=6)
        self.source_entry.bind("<FocusOut>", lambda _event: self._update_source_status())

        ctk.CTkButton(
            card,
            text="Chọn",
            width=48,
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
            width=62,
            anchor="w",
        )
        self.source_status.grid(row=0, column=3, padx=(4, 8), pady=7)

    def _build_create_bar(self):
        card = CompactCard(self, height=48)
        card.grid(row=2, column=0, sticky="ew", padx=8, pady=3)
        card.grid_propagate(False)
        card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            card,
            text="👤+  Profile",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=10, weight="bold"),
            width=72,
            anchor="w",
        ).grid(row=0, column=0, padx=(10, 4), pady=7)

        self.profile_entry = ctk.CTkEntry(
            card,
            textvariable=self.profile_name,
            height=28,
            fg_color=COLORS["input"],
            border_color=COLORS["border_bright"],
            placeholder_text="Tên profile...",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=10),
        )
        self.profile_entry.grid(row=0, column=1, sticky="ew", padx=3, pady=6)

        ctk.CTkButton(
            card,
            text="+",
            width=34,
            height=28,
            fg_color=COLORS["blue"],
            hover_color=COLORS["blue_hover"],
            command=self._create,
        ).grid(row=0, column=2, padx=4)

        ctk.CTkButton(
            card,
            text="Mở",
            width=42,
            height=28,
            fg_color=COLORS["purple"],
            hover_color=COLORS["purple_hover"],
            command=self._continue_login,
        ).grid(row=0, column=3, padx=4)

        ctk.CTkButton(
            card,
            text="Login ✓",
            width=52,
            height=28,
            fg_color=COLORS["surface_soft"],
            hover_color=COLORS["border_bright"],
            command=self._confirm_login,
        ).grid(row=0, column=4, padx=(3, 8))

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
            text="Sort:",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=8),
        ).grid(row=0, column=1, padx=(8, 4))

        self.sort_combo = ctk.CTkComboBox(
            top,
            variable=self.sort_mode,
            values=("Tên A → Z", "Tên Z → A", "Trạng thái"),
            width=82,
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
            ("", 24),
            ("Profile", 86),
            ("Trạng thái", 72),
            ("Boss", 78),
            ("Size", 62),
            ("Thao tác", 112),
        )
        for index, (label, width) in enumerate(columns):
            header.grid_columnconfigure(index, minsize=width, weight=1 if index == 1 else 0)
            ctk.CTkLabel(
                header,
                text=label,
                text_color=COLORS["muted"],
                font=ctk.CTkFont(size=8, weight="bold"),
                anchor="w",
            ).grid(row=0, column=index, sticky="ew", padx=4, pady=8)

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
            width=58,
            height=24,
            fg_color=COLORS["surface_soft"],
            hover_color=COLORS["border_bright"],
            command=self._select_all,
        ).grid(row=0, column=1, padx=3)

        ctk.CTkButton(
            bulk,
            text="Bỏ",
            width=46,
            height=24,
            fg_color=COLORS["surface_soft"],
            hover_color=COLORS["border_bright"],
            command=self._clear_selection,
        ).grid(row=0, column=2, padx=3)

        ctk.CTkButton(
            bulk,
            text="Xóa",
            width=42,
            height=24,
            fg_color="#35121B",
            hover_color=COLORS["red_hover"],
            text_color=COLORS["red"],
            command=self._delete_selected,
        ).grid(row=0, column=3, padx=(3, 0))

    def _build_action_bar(self):
        bar = CompactCard(self, height=46)
        bar.grid(row=4, column=0, sticky="ew", padx=8, pady=3)
        bar.grid_propagate(False)
        bar.grid_columnconfigure((0, 1, 2), weight=1)

        ctk.CTkButton(
            bar,
            text="▶  Bắt đầu",
            height=24,
            fg_color=COLORS["cyan"],
            hover_color=COLORS["blue"],
            text_color=COLORS["black"],
            font=ctk.CTkFont(size=10, weight="bold"),
            command=self._start_selected,
        ).grid(row=0, column=0, sticky="ew", padx=(10, 5), pady=10)

        ctk.CTkButton(
            bar,
            text="■  Dừng",
            height=24,
            fg_color=COLORS["surface_soft"],
            hover_color=COLORS["red_hover"],
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=10, weight="bold"),
            command=self._stop_selected,
        ).grid(row=0, column=1, sticky="ew", padx=5, pady=10)

        ctk.CTkButton(
            bar,
            text="▣  Sắp xếp cửa sổ",
            height=24,
            fg_color=COLORS["purple"],
            hover_color=COLORS["purple_hover"],
            font=ctk.CTkFont(size=9, weight="bold"),
            command=self._arrange_windows,
        ).grid(row=0, column=2, sticky="ew", padx=(5, 10), pady=10)

    def _build_log_panel(self):
        panel = CompactCard(self, height=106)
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
            height=62,
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
            return "Ready" if profile.login_ready else "Waiting Login"
        except MissingGameFilesError:
            return "Error / Missing Files"

    def _sorted_profiles(self):
        profiles = list(self.controller.profiles)
        mode = self.sort_mode.get()

        if mode == "Tên Z → A":
            return sorted(
                profiles,
                key=lambda item: item.profile_name.casefold(),
                reverse=True,
            )

        if mode == "Trạng thái":
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
            self._arrange_windows()
        except (ValueError, OSError) as exc:
            messagebox.showerror("Profile options", str(exc), parent=self)
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
            messagebox.showerror("Sửa profile", str(exc), parent=self)

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
            messagebox.showerror("Sửa profile", str(exc), parent=self)
            raise

    def _delete_single(self, profile_id):
        try:
            profile = self.controller.get(profile_id)
        except StopIteration:
            return

        if not messagebox.askyesno(
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
            messagebox.showerror("Xóa profile", str(exc), parent=self)

    def _delete_selected(self):
        ids = [
            profile.profile_id
            for profile in self.controller.profiles
            if profile.profile_id in self.checked
        ]

        if not ids:
            self._log("App", "WARN", "Chưa chọn profile để xóa")
            return

        if not messagebox.askyesno(
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
            messagebox.showwarning(
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
                _pid, _hwnd, launched = acquire_profile_window(
                    profile.game_path,
                    cancel,
                )
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
                    "Hãy mở profile và chờ game khởi động trước khi xác nhận đăng nhập"
                )

            profile = self.controller.confirm_login(profile_id)
            self._status(profile_id, "Ready")
            self._log(
                profile.profile_name,
                "SUCCESS",
                "Đã lưu auth riêng cho profile",
            )

        except (ValueError, OSError) as exc:
            messagebox.showerror("Xác nhận đăng nhập", str(exc), parent=self)

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
            "on_log": lambda pid, message: self.after(
                0,
                self._worker_log,
                pid,
                message,
            ),
        }

    def _start_profile_ids(self, profile_ids):
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

    # ------------------------------------------------------------------
    # WINDOW LAYOUT
    # ------------------------------------------------------------------

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
            (
                left,
                top,
                right - left,
                bottom - top,
            ),
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
                "Không đủ diện tích để xếp tất cả cửa sổ không chồng lấn",
            )

    # ------------------------------------------------------------------
    # LOG / STATE
    # ------------------------------------------------------------------

    def _log(self, profile, state, message=""):
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

    def _worker_log(self, profile_id, message):
        self._log(profile_id, "INFO", message)

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
