"""Proxy settings dialog for TMLH Bot."""

from __future__ import annotations

from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from proxy_manager import (
    ProxySettings,
    parse_proxy_lines,
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
    ):
        super().__init__(master)

        self.on_save = on_save
        self.on_apply = on_apply

        self.title("SOCKS5 Proxy")
        self.geometry("520x430")
        self.minsize(500, 410)
        self.configure(
            fg_color=COLORS["bg"],
        )
        self.transient(master)
        self.grab_set()

        self.path_var = ctk.StringVar(
            value=settings.proxifyre_path
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

        ctk.CTkLabel(
            proxy_card,
            text=(
                "Cấu hình TMLH được mã hóa bằng Windows DPAPI. "
                "ProxiFyre bắt buộc lưu user/pass dạng plaintext "
                "trong app-config.json của chính ProxiFyre."
            ),
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=8),
            wraplength=460,
            justify="left",
        ).grid(
            row=2,
            column=0,
            sticky="w",
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

    def _browse(self):
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
        )

    def _save(self):
        try:
            settings = self._settings()
            self.on_save(settings)
            messagebox.showinfo(
                "Proxy",
                (
                    f"Đã lưu {len(settings.proxies)} proxy.\n"
                    "Thiết lập sẽ được áp dụng khi bấm Áp dụng."
                ),
                parent=self,
            )
        except (
            ValueError,
            OSError,
            RuntimeError,
        ) as exc:
            messagebox.showerror(
                "Proxy",
                str(exc),
                parent=self,
            )

    def _apply(self):
        try:
            settings = self._settings()
            self.on_save(settings)
            self.on_apply(settings)

            messagebox.showinfo(
                "Proxy",
                (
                    "Đã tạo cấu hình route và gửi yêu cầu "
                    "restart ProxiFyre. Nếu Windows hiện UAC, "
                    "hãy cho phép để áp dụng."
                ),
                parent=self,
            )
        except (
            ValueError,
            OSError,
            RuntimeError,
        ) as exc:
            messagebox.showerror(
                "Proxy",
                str(exc),
                parent=self,
            )
