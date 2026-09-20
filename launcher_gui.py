"""Tkinter profile management and per-profile automation controls."""

from dataclasses import replace
from datetime import datetime
from pathlib import Path
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from app_settings import AppSettings, AppSettingsStorage
from automation_constants import RESOLUTIONS
from boss.enter_boss import BOSSES
from game_automation import AutomationWorker
from profile_manager import MissingGameFilesError, ProfileManager
from profile_models import ProfileRuntimeContext
from profile_storage import ProfileStorage
from window_layout import arrange_windows

from window_manager import (
    PROFILE_LAUNCH_LOCK,
    acquire_profile_window,
    resize_client,
    set_window_topmost,
)
from profile_auth import save_profile_auth

SIZES = tuple(f"{w}x{h}" for w, h in RESOLUTIONS)


class ProfileController:
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
        self.auth_saver = (
            auth_saver
            or save_profile_auth
        )

    def get(self, profile_id):
        with self._storage_lock:
            return next(profile for profile in self.profiles if profile.profile_id == profile_id)

    def _replace(self, updated):
        with self._storage_lock:
            self.profiles = [updated if p.profile_id == updated.profile_id else p for p in self.profiles]
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

        # Account hiện đang login trong game được lưu
        # thành auth riêng của profile.
        self.auth_saver(
            profile.game_path
        )

        return self._replace(
            replace(
                profile,
                login_ready=True,
            )
        )

    def repair_profile(self, profile_id, source_path):
        profile = self.get(profile_id)
        self._replace(replace(profile, login_ready=False))
        self.manager.repair_profile(profile, Path(source_path))
        return self.get(profile_id)

    def set_options(self, profile_id, boss, size):
        if boss not in BOSSES or size not in SIZES:
            raise ValueError("Invalid boss or resolution")

        profile = self.get(profile_id)

        width, height = (
            int(part)
            for part in size.split("x")
        )

        updated = self._replace(
            replace(
                profile,
                selected_boss=boss,
                window_width=width,
                window_height=height,
            )
        )

        # Nếu profile đang chạy thì cập nhật runtime ngay.
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
                    resize_client(
                        hwnd,
                        width,
                        height,
                    )

                    # Đây luôn là resolution mong muốn.
                    context.window_width = width
                    context.window_height = height

                    set_window_topmost(hwnd, True)

                except (ValueError, OSError):
                    pass

        return updated
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
                        # Nếu vừa Stop rồi Start lại quá nhanh,
                        # chờ worker cũ thoát hoàn toàn trước.
                        if old_worker.context.stop_event.is_set():
                            old_thread.join(timeout=3.0)

                        # Sau 3 giây vẫn chưa chết thì tuyệt đối
                        # không tạo worker thứ hai trên cùng profile.
                        if old_thread.is_alive():
                            raise RuntimeError(
                                f"{profile.profile_name}: "
                                f"worker cũ vẫn đang dừng, "
                                f"không thể Start worker mới"
                            )

                    # Worker cũ đã hoàn toàn kết thúc.
                    self.workers.pop(profile_id, None)
                if choices and profile_id in choices:
                    profile = self.set_options(profile_id, *choices[profile_id])
                if not profile.login_ready:
                    raise ValueError(f"{profile.profile_name}: login confirmation required")
                self.manager.check_clone(profile)
                context = ProfileRuntimeContext.from_profile(profile)
                worker = self.worker_factory(
                    context,

                    on_status=(
                        lambda state, pid=profile_id:
                        on_status(pid, state)
                    ) if on_status else None,

                    on_error=(
                        lambda exc, pid=profile_id:
                        on_error(pid, exc)
                    ) if on_error else None,

                    on_log=(
                        lambda message, pid=profile_id:
                        on_log(pid, message)
                    ) if on_log else None,
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


class LauncherApp(tk.Tk):
    def __init__(self, controller=None):
        super().__init__()
        root = Path(__file__).resolve().parent
        self.controller = controller or ProfileController(root)
        self.source = tk.StringVar(value=self.controller.settings_store.load().game_source_path)
        self.profile_name = tk.StringVar()
        self.boss = tk.StringVar(value=next(iter(BOSSES)))
        self.size = tk.StringVar(value="320x180")
        self.statuses = {}
        self.checked = set()
        self.creation_events = {}
        self.title("Thiên Mệnh Lạc Hồng - Profiles")
        self.geometry("850x520")
        self._build_ui()
        self._refresh()
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _build_ui(self):
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)
        path = ttk.Frame(root)
        path.pack(fill="x")
        ttk.Label(path, text="Game Source Path").pack(side="left")
        ttk.Entry(path, textvariable=self.source).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(path, text="Browse", command=self._browse).pack(side="left")
        form = ttk.Frame(root)
        form.pack(fill="x", pady=8)
        ttk.Label(form, text="Profile Name").pack(side="left")
        ttk.Entry(form, textvariable=self.profile_name).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(form, text="Create Profile", command=self._create).pack(side="left")

        self.table = ttk.Treeview(
            root, columns=("check", "profile", "status", "boss", "size", "runtime"),
            show="headings", selectmode="browse",
        )
        for key, label, width in (("check", "✓", 35), ("profile", "Profile", 160),
                                  ("status", "Status", 130), ("boss", "Boss", 105),
                                  ("size", "Size", 100), ("runtime", "Runtime", 170)):
            self.table.heading(key, text=label)
            self.table.column(key, width=width, stretch=key in ("profile", "runtime"))
        self.table.pack(fill="both", expand=True)
        self.table.bind("<ButtonRelease-1>", self._table_click)
        options = ttk.Frame(root)
        options.pack(fill="x", pady=4)
        ttk.Label(options, text="Selected profile Boss").pack(side="left")
        ttk.Combobox(options, textvariable=self.boss, values=tuple(BOSSES), state="readonly", width=16).pack(side="left", padx=5)
        ttk.Label(options, text="Size").pack(side="left")
        self.size_combo = ttk.Combobox(
            options,
            textvariable=self.size,
            values=SIZES,
            state="readonly",
            width=10,
        )

        self.size_combo.pack(
            side="left",
            padx=5,
        )

        self.size_combo.bind(
            "<<ComboboxSelected>>",
            self._resolution_changed,
        )
        ttk.Button(options, text="Save options", command=self._save_options).pack(side="left")
        actions = ttk.Frame(root)
        actions.pack(fill="x", pady=5)
        ttk.Button(actions, text="Start Selected", command=self._start_selected).pack(side="left")
        ttk.Button(actions, text="Stop Selected", command=self._stop_selected).pack(side="left", padx=5)
        ttk.Button(actions, text="Continue Login", command=self._continue_login).pack(side="left")
        ttk.Button(actions, text="Đã đăng nhập", command=self._confirm_login).pack(side="left", padx=5)
        ttk.Button(actions, text="Repair/Recreate", command=self._repair).pack(side="left")
        self.log = tk.Text(root, height=5, state="disabled")
        self.log.pack(fill="x")

    def _log(self, profile, state, message=""):
        self.log.configure(state="normal")
        self.log.insert("end", f"[{datetime.now():%H:%M:%S}] [{profile}] [{state}] {message}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _refresh(self):
        selected = self.table.selection()
        self.table.delete(*self.table.get_children())
        for profile in self.controller.profiles:
            runtime = self.statuses.get(profile.profile_id, "Stopped")
            if runtime in {"Creating", "Copying Game", "Launching Game", "Waiting Startup", "Waiting Login"}:
                status = runtime
            else:
                try:
                    self.controller.manager.check_clone(profile)
                    status = "Ready" if profile.login_ready else "Waiting Login"
                except MissingGameFilesError:
                    status = "ERROR / Missing Game Files"
            self.table.insert("", "end", iid=profile.profile_id,
                              values=("☑" if profile.profile_id in self.checked else "☐",
                                      profile.profile_name, status, profile.selected_boss,
                                      f"{profile.window_width}x{profile.window_height}", runtime))
        if selected and self.table.exists(selected[0]):
            self.table.selection_set(selected[0])

    def _table_click(self, event):
        row = self.table.identify_row(event.y)
        if not row:
            return
        if self.table.identify_column(event.x) == "#1":
            if row in self.checked:
                self.checked.remove(row)
            else:
                self.checked.add(row)
            self._refresh()
        else:
            profile = self.controller.get(row)
            self.boss.set(profile.selected_boss)
            self.size.set(f"{profile.window_width}x{profile.window_height}")

    def _selected_profile(self):
        selection = self.table.selection()
        if not selection:
            raise ValueError("Select a profile row")
        return selection[0]

    def _browse(self):
        selected = filedialog.askdirectory(parent=self)
        if selected:
            self.source.set(selected)
            self.controller.settings_store.save(AppSettings(selected))

    def _create(self):
        try:
            source = self.source.get()
            profile = self.controller.prepare_profile(self.profile_name.get(), source)
            self.controller.settings_store.save(AppSettings(source))
            self.profile_name.set("")
            self.statuses[profile.profile_id] = "Creating"
            self._refresh()
            self._run_creation(profile, source, repair=False)
        except (ValueError, OSError) as exc:
            messagebox.showerror("Profile error", str(exc), parent=self)

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

        threading.Thread(target=work, daemon=True, name=f"create-{profile.profile_id}").start()

    def _continue_login(self):
        try:
            profile_id = self._selected_profile()
            profile = self.controller.get(profile_id)

            self.controller.manager.check_clone(profile)

            self._open_for_login(profile)

        except (ValueError, OSError) as exc:
            messagebox.showerror(
                "Continue login",
                str(exc),
                parent=self,
            )
    def _open_for_login(self, profile):
        cancel = threading.Event()
        self.creation_events[profile.profile_id] = cancel

        def work():
            try:
                self.after(0, self._status, profile.profile_id, "Launching Game")
                with PROFILE_LAUNCH_LOCK:
                    _pid, _hwnd, launched = (
                        acquire_profile_window(
                            profile.game_path,
                            cancel,
                        )
                    )

                    set_window_topmost(
                        _hwnd,
                        True,
                    )

                    if launched:
                        self.after(
                            0,
                            self._status,
                            profile.profile_id,
                            "Waiting Startup",
                        )

                        if cancel.wait(10):
                            return
                if not cancel.is_set():
                    self.after(0, self._status, profile.profile_id, "Waiting Login")
            except Exception as exc:
                self.after(0, self._error, profile.profile_id, exc)

        threading.Thread(target=work, daemon=True).start()

    def _confirm_login(self):
        try:
            profile_id = self._selected_profile()
            if self.statuses.get(profile_id) != "Waiting Login":
                raise ValueError("Open the profile and wait for startup before confirming login")
            self.controller.confirm_login(profile_id)
            self._status(profile_id, "Ready")
        except (ValueError, OSError) as exc:
            messagebox.showerror("Login confirmation", str(exc), parent=self)

    def _save_options(self):
        try:
            profile_id = self._selected_profile()

            profile = self.controller.set_options(
                profile_id,
                self.boss.get(),
                self.size.get(),
            )

            self._refresh()
            self._arrange_windows()

            worker = self.controller.workers.get(profile_id)

            if worker:
                self._log(
                    profile.profile_name,
                    "Options",
                    (
                        f"Boss={profile.selected_boss}, "
                        f"Size={worker.context.window_width}"
                        f"x{worker.context.window_height}"
                    ),
                )

        except (ValueError, OSError) as exc:
            messagebox.showerror(
                "Profile options",
                str(exc),
                parent=self,
            )

    def _start_selected(self):
        if not self.checked:
            self._log("App", "Start", "Check one or more profiles")
            return
        profile_ids = tuple(profile.profile_id for profile in self.controller.profiles
                            if profile.profile_id in self.checked)
        try:
            selected_row = self.table.selection()
            if selected_row and selected_row[0] in self.checked:
                self.controller.set_options(selected_row[0], self.boss.get(), self.size.get())
            self.controller.start_selected(
                profile_ids,

                on_status=lambda pid, state:
                    self.after(
                        0,
                        self._worker_status,
                        pid,
                        state,
                    ),

                on_error=lambda pid, exc:
                    self.after(
                        0,
                        self._worker_error,
                        pid,
                        exc,
                    ),

                on_log=lambda pid, message:
                    self.after(
                        0,
                        self._worker_log,
                        pid,
                        message,
                    ),
            )
            self._refresh()
        except (ValueError, OSError) as exc:
            messagebox.showerror("Start profiles", str(exc), parent=self)
    def _worker_log(
        self,
        profile_id,
        message,
    ):
        self._log(
            profile_id,
            "OCR",
            message,
        )
    def _worker_status(self, profile_id, state):
        self.statuses[profile_id] = state.replace("_", " ").title()
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
        self._log(profile_id, "Error", str(exc))
        self._refresh()

    def _arrange_windows(self):
        import win32api
        import win32con
        import win32gui

        items = []

        for worker in self.controller.workers.values():
            context = worker.context
            hwnd = context.window_handle

            if not hwnd:
                continue

            if not win32gui.IsWindow(hwnd):
                continue

            left, top, right, bottom = win32gui.GetWindowRect(hwnd)

            items.append(
                (
                    hwnd,
                    right - left,
                    bottom - top,
                )
            )

        if not items:
            return

        monitor = win32api.MonitorFromWindow(
            items[0][0],
            win32con.MONITOR_DEFAULTTONEAREST,
        )

        left, top, right, bottom = (
            win32api.GetMonitorInfo(monitor)["Work"]
        )

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
                win32con.SWP_NOSIZE
                | win32con.SWP_NOACTIVATE,
            )

        if any(
            place.overlap
            or place.y + place.height > bottom
            or place.x + place.width > right
            for place in placements
        ):
            self._log(
                "App",
                "Layout",
                "Warning: windows do not all fit in the working area",
            )

    def _stop_selected(self):
        self.controller.stop_selected(tuple(self.checked))

        for profile_id in self.checked:
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

    def _repair(self):
        try:
            profile_id = self._selected_profile()
            profile = self.controller.get(profile_id)
            source = self.source.get()
            if not source:
                raise ValueError("Choose a valid Game Source Path")
            if profile_id in self.controller.workers and not self.controller.workers[profile_id].context.stop_event.is_set():
                raise ValueError("Stop this profile before repair")
            self._run_creation(profile, source, repair=True)
        except (ValueError, OSError) as exc:
            messagebox.showerror("Repair profile", str(exc), parent=self)

    def _status(self, profile_id, state):
        self.statuses[profile_id] = state
        self._log(profile_id, state)
        self._refresh()

    def _error(self, profile_id, exc):
        self._status(profile_id, "Error")
        self._log(profile_id, "Error", str(exc))

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
    def _resolution_changed(self, _event=None):
        try:
            profile_id = self._selected_profile()

            profile = self.controller.set_options(
                profile_id,
                self.boss.get(),
                self.size.get(),
            )

            worker = self.controller.workers.get(profile_id)

            if worker is not None:
                context = worker.context

                self._log(
                    profile.profile_name,
                    "Resize",
                    (
                        f"requested="
                        f"{profile.window_width}x{profile.window_height}, "
                        f"runtime="
                        f"{context.window_width}x{context.window_height}"
                    ),
                )

            self._refresh()

            # Sau resize phải arrange lại tất cả game.
            self._arrange_windows()

        except (ValueError, OSError) as exc:
            messagebox.showerror(
                "Resolution",
                str(exc),
                parent=self,
            )

if __name__ == "__main__":
    LauncherApp().mainloop()
