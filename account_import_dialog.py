"""Single popup used to import many accounts at once."""

from __future__ import annotations

import customtkinter as ctk
import tkinter as tk

from account_import import parse_account_list
from ui_dialogs import keep_above_game
from ui_theme import COLORS


class AccountImportDialog(ctk.CTkToplevel):
    def __init__(
        self,
        master,
        *,
        existing_usernames,
        on_import,
    ):
        super().__init__(master)

        self.existing_usernames = tuple(
            existing_usernames
        )
        self.on_import = on_import
        self.auto_login_var = tk.BooleanVar(
            value=True
        )
        self._parsed_entries = ()

        self.title("Nhập danh sách tài khoản")
        self.geometry("620x500")
        self.minsize(560, 440)
        self.configure(
            fg_color=COLORS["bg"],
        )
        self.transient(master)
        self.grab_set()
        keep_above_game(self)

        self.grid_columnconfigure(
            0,
            weight=1,
        )
        self.grid_rowconfigure(
            2,
            weight=1,
        )

        ctk.CTkLabel(
            self,
            text="Nhập danh sách tài khoản",
            text_color=COLORS["text"],
            font=ctk.CTkFont(
                size=18,
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
                "Mỗi dòng: tài khoản | mật khẩu | sv   •   "
                "sv: 1 = Văn Lang, 2 = Âu Lạc, 3 = Server 3"
            ),
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=12),
        ).grid(
            row=1,
            column=0,
            sticky="w",
            padx=18,
            pady=(0, 10),
        )

        self.input_box = ctk.CTkTextbox(
            self,
            fg_color=COLORS["black"],
            border_width=1,
            border_color=COLORS["border_bright"],
            text_color=COLORS["text"],
            font=("Consolas", 12),
            wrap="none",
        )
        self.input_box.grid(
            row=2,
            column=0,
            sticky="nsew",
            padx=18,
            pady=(0, 10),
        )
        self.input_box.insert(
            "1.0",
            "",
        )

        status_card = ctk.CTkFrame(
            self,
            fg_color=COLORS["surface"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=10,
        )
        status_card.grid(
            row=3,
            column=0,
            sticky="ew",
            padx=18,
            pady=(0, 10),
        )
        status_card.grid_columnconfigure(
            0,
            weight=1,
        )

        self.status_label = ctk.CTkLabel(
            status_card,
            text="Bấm Kiểm tra trước khi thêm.",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=12),
            justify="left",
            anchor="w",
            wraplength=560,
        )
        self.status_label.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=10,
            pady=(8, 4),
        )

        self.issue_label = ctk.CTkLabel(
            status_card,
            text="",
            text_color=COLORS["amber"],
            font=ctk.CTkFont(size=11),
            justify="left",
            anchor="w",
            wraplength=560,
        )
        self.issue_label.grid(
            row=1,
            column=0,
            sticky="ew",
            padx=10,
            pady=(0, 8),
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
        actions.grid_columnconfigure(
            1,
            weight=1,
        )

        ctk.CTkCheckBox(
            actions,
            text="Tự đăng nhập sau khi tạo",
            variable=self.auto_login_var,
            text_color=COLORS["text"],
            fg_color=COLORS["cyan"],
            hover_color=COLORS["blue"],
            border_color=COLORS["border_bright"],
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
            actions,
            text="Kiểm tra",
            width=92,
            height=32,
            fg_color=COLORS["surface_soft"],
            hover_color=COLORS["border_bright"],
            font=ctk.CTkFont(
                size=12,
                weight="bold",
            ),
            command=self._validate,
        ).grid(
            row=0,
            column=2,
            padx=5,
        )

        self.import_button = ctk.CTkButton(
            actions,
            text="Thêm tài khoản",
            width=128,
            height=32,
            fg_color=COLORS["green"],
            hover_color=COLORS["green_hover"],
            text_color=COLORS["black"],
            font=ctk.CTkFont(
                size=12,
                weight="bold",
            ),
            state="disabled",
            command=self._submit,
        )
        self.import_button.grid(
            row=0,
            column=3,
        )

    def _validate(self):
        entries, issues = parse_account_list(
            self.input_box.get(
                "1.0",
                "end",
            ),
            existing_usernames=(
                self.existing_usernames
            ),
        )

        self._parsed_entries = entries

        if entries:
            self.status_label.configure(
                text=(
                    f"✓ {len(entries)} tài khoản hợp lệ"
                    + (
                        f" • {len(issues)} dòng cần sửa"
                        if issues
                        else ""
                    )
                ),
                text_color=COLORS["green"],
            )
            self.import_button.configure(
                state="normal",
                text=f"Thêm {len(entries)} tài khoản",
            )
        else:
            self.status_label.configure(
                text="Không có tài khoản hợp lệ để thêm.",
                text_color=COLORS["red"],
            )
            self.import_button.configure(
                state="disabled",
                text="Thêm tài khoản",
            )

        issue_lines = [
            f"Dòng {item.line_number}: {item.message}"
            for item in issues[:6]
        ]

        if len(issues) > 6:
            issue_lines.append(
                f"... và {len(issues) - 6} lỗi khác"
            )

        self.issue_label.configure(
            text="\n".join(issue_lines)
        )

        return entries, issues

    def _submit(self):
        entries, issues = self._validate()

        if not entries:
            return

        self.on_import(
            entries,
            bool(
                self.auto_login_var.get()
            ),
        )
        self.destroy()
