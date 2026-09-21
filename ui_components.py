"""Compact CustomTkinter widgets for the TMLH dashboard."""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from ui_dialogs import keep_above_game
from ui_theme import BOSS_LABELS, COLORS, status_palette


# Shared by the list header and every profile row so columns never drift.
# checkbox, account, server, boss, size, status, menu
PROFILE_COLUMN_WIDTHS = (24, 132, 42, 92, 76, 118, 34)


class CompactCard(ctk.CTkFrame):
    def __init__(self, master, **kwargs):
        super().__init__(
            master,
            fg_color=COLORS["surface"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=12,
            **kwargs,
        )


class StatusBadge(ctk.CTkLabel):
    def __init__(self, master, status: str = "Stopped", **kwargs):
        fg, bg = status_palette(status)
        super().__init__(
            master,
            text=status,
            text_color=fg,
            fg_color=bg,
            corner_radius=8,
            font=ctk.CTkFont(size=11, weight="bold"),
            height=22,
            width=106,
            **kwargs,
        )

    def set_status(self, status: str):
        fg, bg = status_palette(status)
        self.configure(text=status, text_color=fg, fg_color=bg)


class ProfileRow(ctk.CTkFrame):
    """Account-centric row. Batch actions live outside the row."""

    SERVER_NUMBERS = {
        "van_lang": "1",
        "au_lac": "2",
        "server_3": "3",
    }

    def __init__(
        self,
        master,
        profile,
        runtime_status: str,
        *,
        checked: bool,
        focused: bool,
        on_toggle,
        on_focus,
        on_more,
    ):
        super().__init__(
            master,
            height=42,
            fg_color=COLORS["surface_alt"],
            border_width=2 if focused else 1,
            border_color=(
                COLORS["cyan"]
                if focused
                else COLORS["border"]
            ),
            corner_radius=9,
        )
        self.grid_propagate(False)
        self.profile_id = profile.profile_id

        for index, width in enumerate(
            PROFILE_COLUMN_WIDTHS
        ):
            self.grid_columnconfigure(
                index,
                minsize=width,
                weight=1 if index == 1 else 0,
            )

        self.check_var = tk.BooleanVar(
            value=checked
        )
        ctk.CTkCheckBox(
            self,
            text="",
            variable=self.check_var,
            command=lambda: on_toggle(
                profile.profile_id,
                self.check_var.get(),
            ),
            width=16,
            checkbox_width=16,
            checkbox_height=16,
            corner_radius=5,
            fg_color=COLORS["cyan"],
            hover_color=COLORS["blue_hover"],
            border_color=COLORS["border_bright"],
        ).grid(
            row=0,
            column=0,
            padx=(5, 2),
            pady=10,
        )

        username = (
            getattr(
                profile,
                "account_username",
                "",
            ).strip()
            or profile.profile_name
        )

        account = ctk.CTkLabel(
            self,
            text=username,
            text_color=COLORS["text"],
            font=ctk.CTkFont(
                size=12,
                weight="bold",
            ),
            anchor="w",
        )
        account.grid(
            row=0,
            column=1,
            sticky="ew",
            padx=4,
        )

        server = self.SERVER_NUMBERS.get(
            getattr(
                profile,
                "server",
                "van_lang",
            ),
            "?",
        )
        ctk.CTkLabel(
            self,
            text=server,
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=12),
        ).grid(
            row=0,
            column=2,
            sticky="w",
            padx=3,
        )

        boss = BOSS_LABELS.get(
            profile.selected_boss,
            profile.selected_boss,
        )
        ctk.CTkLabel(
            self,
            text=boss,
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=11),
            anchor="w",
        ).grid(
            row=0,
            column=3,
            sticky="ew",
            padx=3,
        )

        ctk.CTkLabel(
            self,
            text=(
                f"{profile.window_width}×"
                f"{profile.window_height}"
            ),
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=11),
            anchor="w",
        ).grid(
            row=0,
            column=4,
            sticky="ew",
            padx=3,
        )

        self.status_badge = StatusBadge(
            self,
            runtime_status,
        )
        self.status_badge.grid(
            row=0,
            column=5,
            sticky="w",
            padx=3,
        )

        ctk.CTkButton(
            self,
            text="⋯",
            width=28,
            height=26,
            corner_radius=7,
            fg_color=COLORS["surface_soft"],
            hover_color=COLORS["border_bright"],
            text_color=COLORS["text"],
            font=ctk.CTkFont(
                size=14,
                weight="bold",
            ),
            command=lambda: on_more(
                profile.profile_id
            ),
        ).grid(
            row=0,
            column=6,
            padx=(2, 5),
        )

        for widget in (
            self,
            account,
            self.status_badge,
        ):
            widget.bind(
                "<Button-1>",
                lambda _event, pid=profile.profile_id:
                on_focus(pid),
            )

    def set_status(self, status: str):
        self.status_badge.set_status(
            status
        )


class EditProfileDialog(ctk.CTkToplevel):
    """Small modal used to edit profile name, boss and resolution."""

    def __init__(
        self,
        master,
        profile,
        boss_values,
        size_values,
        on_save,
    ):
        super().__init__(master)
        self.profile = profile
        self.on_save = on_save

        self.title("Sửa profile")
        self.geometry("360x280")
        self.resizable(False, False)
        self.configure(fg_color=COLORS["bg"])
        self.transient(master)
        self.grab_set()
        keep_above_game(self)

        self.name_var = tk.StringVar(value=profile.profile_name)

        ctk.CTkLabel(
            self,
            text="Sửa profile",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(anchor="w", padx=20, pady=(18, 14))

        ctk.CTkLabel(
            self,
            text="Tên profile",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", padx=20)

        self.name_entry = ctk.CTkEntry(
            self,
            textvariable=self.name_var,
            height=34,
            fg_color=COLORS["input"],
            border_color=COLORS["border_bright"],
        )
        self.name_entry.pack(fill="x", padx=20, pady=(4, 10))

        fields = ctk.CTkFrame(self, fg_color="transparent")
        fields.pack(fill="x", padx=20)
        fields.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkLabel(
            fields,
            text="Boss",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=12),
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            fields,
            text="Kích thước",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=12),
        ).grid(row=0, column=1, sticky="w", padx=(8, 0))

        self.boss_combo = ctk.CTkComboBox(
            fields,
            values=list(boss_values),
            height=34,
            fg_color=COLORS["input"],
            border_color=COLORS["border_bright"],
            dropdown_fg_color=COLORS["surface_alt"],
            text_color=COLORS["text"],
        )
        self.boss_combo.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.boss_combo.set(BOSS_LABELS.get(profile.selected_boss, profile.selected_boss))

        self.size_combo = ctk.CTkComboBox(
            fields,
            values=list(size_values),
            height=34,
            fg_color=COLORS["input"],
            border_color=COLORS["border_bright"],
            dropdown_fg_color=COLORS["surface_alt"],
            text_color=COLORS["text"],
        )
        self.size_combo.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(4, 0))
        self.size_combo.set(f"{profile.window_width}x{profile.window_height}")

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=20, pady=(22, 16))

        ctk.CTkButton(
            actions,
            text="Hủy",
            width=96,
            height=34,
            fg_color=COLORS["surface_soft"],
            hover_color=COLORS["border_bright"],
            command=self.destroy,
        ).pack(side="right")

        ctk.CTkButton(
            actions,
            text="Lưu thay đổi",
            width=130,
            height=34,
            fg_color=COLORS["blue"],
            hover_color=COLORS["blue_hover"],
            command=self._save,
        ).pack(side="right", padx=(0, 8))

        self.after(100, self.name_entry.focus_set)

    def _save(self):
        self.on_save(
            self.profile.profile_id,
            self.name_var.get().strip(),
            self.boss_combo.get(),
            self.size_combo.get(),
        )
        self.destroy()
