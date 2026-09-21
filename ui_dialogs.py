"""UI helpers that keep TMLH Bot dialogs above topmost game windows."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox


def keep_above_game(window) -> None:
    if window is None:
        return

    try:
        window.attributes(
            "-topmost",
            True,
        )
        window.lift()
        window.after_idle(
            window.lift
        )
    except (
        tk.TclError,
        AttributeError,
    ):
        pass


def _prepare_parent(parent):
    keep_above_game(
        parent
    )
    return parent


def showinfo(
    title,
    message,
    *,
    parent=None,
    **kwargs,
):
    return messagebox.showinfo(
        title,
        message,
        parent=_prepare_parent(parent),
        **kwargs,
    )


def showwarning(
    title,
    message,
    *,
    parent=None,
    **kwargs,
):
    return messagebox.showwarning(
        title,
        message,
        parent=_prepare_parent(parent),
        **kwargs,
    )


def showerror(
    title,
    message,
    *,
    parent=None,
    **kwargs,
):
    return messagebox.showerror(
        title,
        message,
        parent=_prepare_parent(parent),
        **kwargs,
    )


def askyesno(
    title,
    message,
    *,
    parent=None,
    **kwargs,
):
    return messagebox.askyesno(
        title,
        message,
        parent=_prepare_parent(parent),
        **kwargs,
    )
