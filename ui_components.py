"""Compact CustomTkinter widgets for the TMLH dashboard."""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from ui_theme import BOSS_LABELS, COLORS, status_palette


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
            font=ctk.CTkFont(size=8, weight="bold"),
            height=20,
            width=68,
            **kwargs,
        )

    def set_status(self, status: str):
        fg, bg = status_palette(status)
        self.configure(text=status, text_color=fg, fg_color=bg)


class ProfileRow(ctk.CTkFrame):
    """Compact profile row used by the main list."""

    def __init__(
        self,
        master,
        profile,
        runtime_status: str,
        *,
        checked: bool,
        focused: bool,
        boss_values,
        size_values,
        on_toggle,
        on_focus,
        on_change,
        on_start,
        on_stop,
        on_edit,
        on_delete,
    ):
        super().__init__(
            master,
            height=40,
            fg_color=COLORS["surface_alt"],
            border_width=2 if focused else 1,
            border_color=COLORS["cyan"] if focused else COLORS["border"],
            corner_radius=9,
        )
        self.grid_propagate(False)
        self.profile_id = profile.profile_id
        self.on_change = on_change

        # Fits comfortably in an ~800 px window.
        widths = (24, 86, 72, 78, 62, 112)
        for index, width in enumerate(widths):
            self.grid_columnconfigure(index, minsize=width, weight=1 if index == 1 else 0)

        self.check_var = tk.BooleanVar(value=checked)
        ctk.CTkCheckBox(
            self,
            text="",
            variable=self.check_var,
            command=lambda: on_toggle(profile.profile_id, self.check_var.get()),
            width=16,
            checkbox_width=16,
            checkbox_height=16,
            corner_radius=5,
            fg_color=COLORS["cyan"],
            hover_color=COLORS["blue_hover"],
            border_color=COLORS["border_bright"],
        ).grid(row=0, column=0, padx=(5, 1), pady=9)

        name = ctk.CTkLabel(
            self,
            text=profile.profile_name,
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=9, weight="bold"),
            anchor="w",
        )
        name.grid(row=0, column=1, sticky="ew", padx=5)
        name.bind("<Button-1>", lambda _event: on_focus(profile.profile_id))

        StatusBadge(self, runtime_status).grid(
            row=0,
            column=2,
            padx=2,
            pady=9,
            sticky="w",
        )

        boss_label = BOSS_LABELS.get(profile.selected_boss, profile.selected_boss)
        self.boss_combo = ctk.CTkComboBox(
            self,
            values=list(boss_values),
            width=74,
            height=24,
            font=ctk.CTkFont(size=8),
            dropdown_font=ctk.CTkFont(size=8),
            fg_color=COLORS["input"],
            border_color=COLORS["border_bright"],
            button_color=COLORS["surface_soft"],
            button_hover_color=COLORS["blue_hover"],
            dropdown_fg_color=COLORS["surface_alt"],
            dropdown_hover_color=COLORS["surface_soft"],
            text_color=COLORS["text"],
            command=lambda _value: self._changed(),
        )
        self.boss_combo.set(boss_label)
        self.boss_combo.grid(row=0, column=3, padx=4)

        self.size_combo = ctk.CTkComboBox(
            self,
            values=list(size_values),
            width=58,
            height=24,
            font=ctk.CTkFont(size=8),
            dropdown_font=ctk.CTkFont(size=8),
            fg_color=COLORS["input"],
            border_color=COLORS["border_bright"],
            button_color=COLORS["surface_soft"],
            button_hover_color=COLORS["blue_hover"],
            dropdown_fg_color=COLORS["surface_alt"],
            dropdown_hover_color=COLORS["surface_soft"],
            text_color=COLORS["text"],
            command=lambda _value: self._changed(),
        )
        self.size_combo.set(f"{profile.window_width}x{profile.window_height}")
        self.size_combo.grid(row=0, column=4, padx=4)

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=0, column=5, padx=(2, 4), sticky="e")

        self._button(actions, "▶", COLORS["blue"], lambda: on_start(profile.profile_id)).pack(side="left", padx=2)
        self._button(actions, "■", COLORS["red"], lambda: on_stop(profile.profile_id)).pack(side="left", padx=2)
        self._button(actions, "✎", COLORS["purple"], lambda: on_edit(profile.profile_id)).pack(side="left", padx=2)
        self._button(actions, "×", COLORS["surface_soft"], lambda: on_delete(profile.profile_id)).pack(side="left", padx=2)

        self.bind("<Button-1>", lambda _event: on_focus(profile.profile_id))

    @staticmethod
    def _button(master, text, color, command):
        return ctk.CTkButton(
            master,
            text=text,
            width=22,
            height=24,
            corner_radius=7,
            fg_color=color,
            hover_color=COLORS["border_bright"],
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=9, weight="bold"),
            command=command,
        )

    def _changed(self):
        self.on_change(
            self.profile_id,
            self.boss_combo.get(),
            self.size_combo.get(),
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

        self.name_var = tk.StringVar(value=profile.profile_name)

        ctk.CTkLabel(
            self,
            text="Sửa profile",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=20, weight="bold"),
        ).pack(anchor="w", padx=20, pady=(18, 14))

        ctk.CTkLabel(
            self,
            text="Tên profile",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=10),
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
            font=ctk.CTkFont(size=10),
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            fields,
            text="Kích thước",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=10),
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
