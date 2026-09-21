"""Proxy settings dialog for TMLH Bot."""

from __future__ import annotations

from pathlib import Path
import threading
import tkinter as tk
from tkinter import filedialog

import customtkinter as ctk

from proxy_manager import (
    ProxySettings,
    parse_proxy_lines,
)
from ui_dialogs import (
    keep_above_game,
    showerror,
    showinfo,
    showwarning,
)
from ui_theme import COLORS


class ProxySettingsDialog(ctk.CTkToplevel):
    def __init__(
        self,
        master,
        settings: ProxySettings,
        *,
        on_save,
        on_apply,
        on_verify,
    ):
        super().__init__(master)

        self.on_save = on_save
        self.on_apply = on_apply
        self.on_verify = on_verify

        self.title("SOCKS5 Proxy")
        self.geometry("540x520")
        self.minsize(520, 500)
        self.configure(
            fg_color=COLORS["bg"],
        )
        self.transient(master)
        self.grab_set()
        keep_above_game(self)

        self.path_var = tk.StringVar(
            value=settings.proxifyre_path
        )
        self.test_mode_var = tk.BooleanVar(
            value=bool(settings.test_mode)
        )

        self.grid_columnconfigure(
            0,
            weight=1,
        )
        self.grid_rowconfigure(
            3,
            weight=1,
        )

        ctk.CTkLabel(
            self,
            text="SOCKS5 theo nhóm 30 profile",
            text_color=COLORS["text"],
            font=ctk.CTkFont(
                size=17,
                weight="bold",
            ),
        ).grid(
            row=0,
            column=0,
            sticky="w",
            padx=18,
            pady=(16, 4),
        )

        ctk.CTkLabel(
            self,
            text=(
                "1–30: mạng gốc  •  31–60: Proxy 1  •  "
                "61–90: Proxy 2  •  vượt số proxy: dùng proxy cuối"
            ),
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=9),
            wraplength=480,
            justify="left",
        ).grid(
            row=1,
            column=0,
            sticky="w",
            padx=18,
            pady=(0, 10),
        )

        path_card = ctk.CTkFrame(
            self,
            fg_color=COLORS["surface"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=10,
        )
        path_card.grid(
            row=2,
            column=0,
            sticky="ew",
            padx=18,
            pady=(0, 10),
        )
        path_card.grid_columnconfigure(
            1,
            weight=1,
        )

        ctk.CTkLabel(
            path_card,
            text="ProxiFyre",
            text_color=COLORS["text"],
            font=ctk.CTkFont(
                size=9,
                weight="bold",
            ),
            width=72,
            anchor="w",
        ).grid(
            row=0,
            column=0,
            padx=(10, 4),
            pady=8,
        )

        self.path_entry = ctk.CTkEntry(
            path_card,
            textvariable=self.path_var,
            height=28,
            fg_color=COLORS["input"],
            border_color=COLORS["border_bright"],
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=9),
            placeholder_text="Đường dẫn ProxiFyre.exe",
        )
        self.path_entry.grid(
            row=0,
            column=1,
            sticky="ew",
            padx=4,
            pady=8,
        )

        ctk.CTkButton(
            path_card,
            text="Chọn",
            width=54,
            height=28,
            fg_color=COLORS["surface_soft"],
            hover_color=COLORS["border_bright"],
            command=self._browse,
        ).grid(
            row=0,
            column=2,
            padx=(4, 10),
            pady=8,
        )

        proxy_card = ctk.CTkFrame(
            self,
            fg_color=COLORS["surface"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=10,
        )
        proxy_card.grid(
            row=3,
            column=0,
            sticky="nsew",
            padx=18,
            pady=(0, 10),
        )
        proxy_card.grid_columnconfigure(
            0,
            weight=1,
        )
        proxy_card.grid_rowconfigure(
            1,
            weight=1,
        )

        ctk.CTkLabel(
            proxy_card,
            text=(
                "Proxy — mỗi dòng: "
                "ip:port:user:pass"
            ),
            text_color=COLORS["text"],
            font=ctk.CTkFont(
                size=10,
                weight="bold",
            ),
        ).grid(
            row=0,
            column=0,
            sticky="w",
            padx=10,
            pady=(9, 5),
        )

        self.proxy_text = ctk.CTkTextbox(
            proxy_card,
            fg_color=COLORS["black"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=8,
            text_color=COLORS["text"],
            font=("Consolas", 10),
            wrap="none",
        )
        self.proxy_text.grid(
            row=1,
            column=0,
            sticky="nsew",
            padx=10,
            pady=(0, 8),
        )

        self.proxy_text.insert(
            "1.0",
            "\n".join(
                proxy.as_line()
                for proxy in settings.proxies
            ),
        )

        self.test_mode_check = ctk.CTkCheckBox(
            proxy_card,
            text=(
                "Test proxy với ít tab — Profile 1 Direct, "
                "Profile 2 → Proxy 1, Profile 3 → Proxy 2..."
            ),
            variable=self.test_mode_var,
            text_color=COLORS["text"],
            fg_color=COLORS["cyan"],
            hover_color=COLORS["blue"],
            border_color=COLORS["border_bright"],
            font=ctk.CTkFont(size=9, weight="bold"),
            checkbox_width=16,
            checkbox_height=16,
        )
        self.test_mode_check.grid(
            row=2,
            column=0,
            sticky="w",
            padx=10,
            pady=(0, 6),
        )

        ctk.CTkLabel(
            proxy_card,
            text=(
                "Khi bật Test: chỉ cần 2 profile để thử route thật của game. "
                "Sau khi test xong hãy bỏ chọn và bấm Áp dụng để quay về "
                "1–30 Direct, 31–60 Proxy 1. "
                "Cấu hình TMLH được mã hóa bằng Windows DPAPI; "
                "ProxiFyre vẫn lưu user/pass plaintext trong app-config.json."
            ),
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=8),
            wraplength=490,
            justify="left",
        ).grid(
            row=3,
            column=0,
            sticky="w",
            padx=10,
            pady=(0, 6),
        )

        self.verify_status = ctk.CTkLabel(
            proxy_card,
            text=(
                "Chưa kiểm tra • bấm Kiểm tra để xác thực SOCKS5 "
                "và app-config ProxiFyre."
            ),
            text_color=COLORS["muted"],
            font=ctk.CTkFont(
                size=9,
                weight="bold",
            ),
            wraplength=490,
            justify="left",
            anchor="w",
        )
        self.verify_status.grid(
            row=4,
            column=0,
            sticky="ew",
            padx=10,
            pady=(0, 9),
        )

        actions = ctk.CTkFrame(
            self,
            fg_color="transparent",
        )
        actions.grid(
            row=4,
            column=0,
            sticky="ew",
            padx=18,
            pady=(0, 16),
        )

        ctk.CTkButton(
            actions,
            text="Hủy",
            width=80,
            height=30,
            fg_color=COLORS["surface_soft"],
            hover_color=COLORS["border_bright"],
            command=self.destroy,
        ).pack(
            side="right",
        )

        ctk.CTkButton(
            actions,
            text="Lưu",
            width=84,
            height=30,
            fg_color=COLORS["purple"],
            hover_color=COLORS["purple_hover"],
            command=self._save,
        ).pack(
            side="right",
            padx=(0, 7),
        )

        ctk.CTkButton(
            actions,
            text="Áp dụng",
            width=96,
            height=30,
            fg_color=COLORS["cyan"],
            hover_color=COLORS["blue"],
            text_color=COLORS["black"],
            command=self._apply,
        ).pack(
            side="right",
            padx=(0, 7),
        )

        self.verify_button = ctk.CTkButton(
            actions,
            text="✓ Kiểm tra",
            width=100,
            height=30,
            fg_color=COLORS["green"],
            hover_color=COLORS["green_hover"],
            text_color=COLORS["black"],
            command=self._verify,
        )
        self.verify_button.pack(
            side="left",
        )

    def _browse(self):
        keep_above_game(self)
        selected = filedialog.askopenfilename(
            parent=self,
            title="Chọn ProxiFyre.exe",
            filetypes=(
                ("ProxiFyre", "ProxiFyre.exe"),
                ("Executable", "*.exe"),
            ),
        )

        if selected:
            self.path_var.set(selected)

    def _settings(self) -> ProxySettings:
        path = self.path_var.get().strip()
        proxies = parse_proxy_lines(
            self.proxy_text.get(
                "1.0",
                "end",
            )
        )

        return ProxySettings(
            proxifyre_path=path,
            proxies=proxies,
            test_mode=bool(
                self.test_mode_var.get()
            ),
        )

    def _save(self):
        try:
            settings = self._settings()
            self.on_save(settings)
            showinfo(
                "Proxy",
                (
                    f"Đã lưu {len(settings.proxies)} proxy.\n"
                    f"Chế độ: {'TEST ít tab' if settings.test_mode else 'BÌNH THƯỜNG'}.\n"
                    "Thiết lập sẽ được áp dụng khi bấm Áp dụng."
                ),
                parent=self,
            )
        except (
            ValueError,
            OSError,
            RuntimeError,
        ) as exc:
            showerror(
                "Proxy",
                str(exc),
                parent=self,
            )

    def _verify(self):
        try:
            settings = self._settings()

            if not settings.proxies:
                raise ValueError(
                    "Chưa có SOCKS5 proxy để kiểm tra"
                )

            self.verify_button.configure(
                state="disabled",
                text="Đang kiểm tra...",
            )
            self.verify_status.configure(
                text=(
                    "Đang kiểm tra SOCKS5 auth/CONNECT "
                    "và app-config ProxiFyre..."
                ),
                text_color=COLORS["amber"],
            )

            threading.Thread(
                target=self._verify_worker,
                args=(settings,),
                name="proxy-verification",
                daemon=True,
            ).start()

        except (
            ValueError,
            OSError,
            RuntimeError,
        ) as exc:
            showerror(
                "Kiểm tra proxy",
                str(exc),
                parent=self,
            )

    def _verify_worker(
        self,
        settings,
    ):
        try:
            result = self.on_verify(
                settings
            )
            self.after(
                0,
                lambda: self._show_verification(
                    result
                ),
            )
        except Exception as exc:
            self.after(
                0,
                lambda exc=exc:
                self._show_verify_error(
                    exc
                ),
            )

    def _show_verification(
        self,
        result,
    ):
        self.verify_button.configure(
            state="normal",
            text="✓ Kiểm tra",
        )

        mode = (
            "TEST MODE"
            if result.test_mode
            else "BÌNH THƯỜNG"
        )
        config_text = (
            "ProxiFyre config: OK"
            if result.config_applied
            else "ProxiFyre config: CHƯA KHỚP"
        )

        proxy_lines = []

        for index, item in enumerate(
            result.proxy_results,
            start=1,
        ):
            if item.ok:
                proxy_lines.append(
                    (
                        f"Proxy {index}: OK "
                        f"({item.latency_ms:.0f} ms)"
                    )
                )
            else:
                proxy_lines.append(
                    (
                        f"Proxy {index}: LỖI — "
                        f"{item.detail}"
                    )
                )

        all_proxy_ok = (
            bool(result.proxy_results)
            and all(
                item.ok
                for item in result.proxy_results
            )
        )
        fully_ok = (
            result.config_applied
            and all_proxy_ok
        )

        summary = (
            f"{mode} • {config_text}"
            + (
                " • "
                + " | ".join(
                    proxy_lines
                )
                if proxy_lines
                else ""
            )
        )

        self.verify_status.configure(
            text=summary,
            text_color=(
                COLORS["green"]
                if fully_ok
                else (
                    COLORS["amber"]
                    if all_proxy_ok
                    else COLORS["red"]
                )
            ),
        )

        keep_above_game(self)

        if fully_ok:
            showinfo(
                "Proxy OK",
                (
                    "SOCKS5 auth + CONNECT thành công và app-config "
                    "ProxiFyre đang khớp với route hiện tại.\n\n"
                    "Nếu vừa đổi route, hãy restart tab game để socket "
                    "mới dùng cấu hình vừa áp dụng."
                ),
                parent=self,
            )
        elif all_proxy_ok:
            showwarning(
                "Proxy dùng được nhưng chưa áp dụng",
                (
                    "SOCKS5 kết nối thành công nhưng app-config "
                    "ProxiFyre chưa khớp. Bấm Áp dụng, cho phép UAC "
                    "nếu có, rồi Kiểm tra lại."
                ),
                parent=self,
            )
        else:
            showerror(
                "Proxy lỗi",
                "\n".join(
                    proxy_lines
                ),
                parent=self,
            )

    def _show_verify_error(
        self,
        exc,
    ):
        self.verify_button.configure(
            state="normal",
            text="✓ Kiểm tra",
        )
        self.verify_status.configure(
            text=f"Kiểm tra lỗi: {exc}",
            text_color=COLORS["red"],
        )
        showerror(
            "Kiểm tra proxy",
            str(exc),
            parent=self,
        )

    def _apply(self):
        try:
            settings = self._settings()
            self.on_save(settings)
            self.on_apply(settings)

            showinfo(
                "Proxy",
                (
                    (
                        "Đã áp dụng TEST MODE: Profile 1 Direct, "
                        "Profile 2 → Proxy 1, Profile 3 → Proxy 2...\n\n"
                        "Nên restart các tab game đang mở để tạo connection mới. "
                        "Test xong bỏ chọn Test và bấm Áp dụng để khôi phục route chuẩn."
                        if settings.test_mode
                        else
                        "Đã tạo cấu hình route chuẩn và gửi yêu cầu restart ProxiFyre. "
                        "Nếu Windows hiện UAC, hãy cho phép để áp dụng."
                    )
                ),
                parent=self,
            )

            self.after(
                300,
                self._verify,
            )
        except (
            ValueError,
            OSError,
            RuntimeError,
        ) as exc:
            showerror(
                "Proxy",
                str(exc),
                parent=self,
            )
