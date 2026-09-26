from unittest.mock import patch

import pytest

from input_manager import InputManager


def test_physical_click_does_not_emit_mouse_when_foreground_fails():
    with patch.object(
        InputManager,
        "_bring_game_forward",
        side_effect=RuntimeError("focus failed"),
    ), patch(
        "input_manager.win32api.SetCursorPos",
    ) as set_cursor, patch(
        "input_manager.win32api.mouse_event",
    ) as mouse_event:
        with pytest.raises(
            RuntimeError,
            match="focus failed",
        ):
            InputManager._physical_click(
                99,
                10,
                20,
            )

    set_cursor.assert_not_called()
    mouse_event.assert_not_called()


def test_clipboard_open_retries_transient_lock():
    attempts = []

    def open_clipboard():
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError(
                "clipboard busy"
            )

    with patch(
        "input_manager.win32clipboard.OpenClipboard",
        side_effect=open_clipboard,
    ), patch(
        "input_manager.time.sleep",
    ):
        InputManager._open_clipboard_with_retry()

    assert len(attempts) == 3


def test_ctrl_combo_attempts_key_release_after_press_failure():
    calls = []

    def keybd_event(
        key,
        scan,
        flags,
        extra,
    ):
        calls.append(
            (
                key,
                flags,
            )
        )

        if len(calls) == 2:
            raise RuntimeError(
                "keydown failure"
            )

    with patch(
        "input_manager.win32api.keybd_event",
        side_effect=keybd_event,
    ):
        with pytest.raises(
            RuntimeError,
            match="keydown failure",
        ):
            InputManager._send_ctrl_combo(
                ord("V")
            )

    # Even after the second key-down fails, both key-up calls are attempted.
    assert len(calls) == 4
