"""Reusable CustomTkinter widgets for the TMLH dashboard."""

from __future__ import annotations

import customtkinter as ctk

from ui_theme import BOSS_LABELS, COLORS, status_palette


class SectionCard(ctk.CTkFrame):
    def __init__(self, master, title: str, subtitle: str | None = None, **kwargs):
        super().__init__(
            master,
            fg_color=COLORS["surface"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=14,
            **kwargs,
        )
        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(14, 8))
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text=title,
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=16, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        if subtitle:
            ctk.CTkLabel(
                header,
                text=subtitle,
                text_color=COLORS["muted"],
                font=ctk.CTkFont(size=11),
                anchor="e",
            ).grid(row=0, column=1, sticky="e", padx=(12, 0))

        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))
        self.grid_rowconfigure(1, weight=1)


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
            height=28,
            padx=10,
            **kwargs,
        )

    def set_status(self, status: str):
        fg, bg = status_palette(status)
        self.configure(text=status, text_color=fg, fg_color=bg)


class SummaryChip(ctk.CTkFrame):
    def __init__(self, master, label: str, value: str = "0", accent: str | None = None):
        super().__init__(
            master,
            fg_color=COLORS["surface_alt"],
            border_width=1,
            border_color=COLORS["border"],
            corner_radius=12,
            height=76,
        )
        self.grid_propagate(False)
        accent = accent or COLORS["blue"]

        dot = ctk.CTkLabel(
            self,
            text="●",
            text_color=accent,
            font=ctk.CTkFont(size=16, weight="bold"),
            width=20,
        )
        dot.grid(row=0, column=0, rowspan=2, padx=(12, 6), pady=10)

        ctk.CTkLabel(
            self,
            text=label,
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=11),
            anchor="w",
        ).grid(row=0, column=1, sticky="sw", pady=(10, 0))

        self.value_label = ctk.CTkLabel(
            self,
            text=value,
            text_color=accent,
            font=ctk.CTkFont(size=20, weight="bold"),
            anchor="w",
        )
        self.value_label.grid(row=1, column=1, sticky="nw", pady=(0, 10), padx=(0, 12))

    def set_value(self, value):
        self.value_label.configure(text=str(value))


class ProfileRow(ctk.CTkFrame):
    """One visual profile row; no business logic lives here."""

    def __init__(
        self,
        master,
        profile,
        runtime_status: str,
        *,
        selected: bool,
        boss_values,
        size_values,
        on_toggle,
        on_focus,
        on_change,
        on_start,
        on_stop,
        on_login,
        on_repair,
    ):
        super().__init__(
            master,
            fg_color=COLORS["surface_alt"],
            border_width=1,
            border_color=COLORS["border_bright"] if selected else COLORS["border"],
            corner_radius=12,
            height=66,
        )
        self.grid_propagate(False)
        self.profile_id = profile.profile_id
        self.on_focus = on_focus
        self.on_change = on_change

        columns = (38, 155, 125, 135, 118, 170)
        for index, width in enumerate(columns):
            self.grid_columnconfigure(index, minsize=width, weight=1 if index == 1 else 0)

        self.check_var = ctk.BooleanVar(value=selected)
        self.check = ctk.CTkCheckBox(
            self,
            text="",
            variable=self.check_var,
            command=lambda: on_toggle(profile.profile_id, self.check_var.get()),
            width=24,
            checkbox_width=20,
            checkbox_height=20,
            fg_color=COLORS["blue"],
            hover_color=COLORS["blue_hover"],
            border_color=COLORS["border_bright"],
        )
        self.check.grid(row=0, column=0, padx=(12, 4), pady=18)

        name_box = ctk.CTkFrame(self, fg_color="transparent")
        name_box.grid(row=0, column=1, sticky="ew", padx=4)
        name_box.grid_columnconfigure(1, weight=1)

        avatar = ctk.CTkLabel(
            name_box,
            text=(profile.profile_name[:1] or "P").upper(),
            width=34,
            height=34,
            corner_radius=17,
            fg_color=COLORS["surface_soft"],
            text_color=COLORS["cyan"],
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        avatar.grid(row=0, column=0, padx=(0, 8))

        ctk.CTkLabel(
            name_box,
            text=profile.profile_name,
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        ).grid(row=0, column=1, sticky="ew")

        self.badge = StatusBadge(self, runtime_status)
        self.badge.grid(row=0, column=2, padx=6, pady=16, sticky="w")

        boss_label = BOSS_LABELS.get(profile.selected_boss, profile.selected_boss)
        self.boss_combo = ctk.CTkComboBox(
            self,
            values=list(boss_values),
            width=128,
            height=32,
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
        self.boss_combo.grid(row=0, column=3, padx=6)

        self.size_combo = ctk.CTkComboBox(
            self,
            values=list(size_values),
            width=108,
            height=32,
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
        self.size_combo.grid(row=0, column=4, padx=6)

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=0, column=5, padx=(6, 10), sticky="e")

        self._action_button(actions, "▶", COLORS["blue"], lambda: on_start(profile.profile_id), "Start").pack(side="left", padx=3)
        self._action_button(actions, "■", COLORS["red"], lambda: on_stop(profile.profile_id), "Stop").pack(side="left", padx=3)
        self._action_button(actions, "↗", COLORS["purple"], lambda: on_login(profile.profile_id), "Login").pack(side="left", padx=3)
        self._action_button(actions, "↻", COLORS["surface_soft"], lambda: on_repair(profile.profile_id), "Repair").pack(side="left", padx=3)

        for widget in (self, name_box, avatar):
            widget.bind("<Button-1>", lambda _event, pid=profile.profile_id: on_focus(pid))

    @staticmethod
    def _action_button(master, text, color, command, tooltip):
        return ctk.CTkButton(
            master,
            text=text,
            width=34,
            height=32,
            corner_radius=8,
            fg_color=color,
            hover_color=COLORS["border_bright"],
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=13, weight="bold"),
            command=command,
        )

    def _changed(self):
        self.on_change(
            self.profile_id,
            self.boss_combo.get(),
            self.size_combo.get(),
        )
