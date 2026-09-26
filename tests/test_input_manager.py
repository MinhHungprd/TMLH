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


def test_physical_paste_types_without_windows_clipboard():
    with patch.object(
        InputManager,
        "_bring_game_forward",
    ), patch.object(
        InputManager,
        "_foreground_is_target",
        return_value=True,
    ), patch.object(
        InputManager,
        "_send_ctrl_combo",
    ) as select_all, patch.object(
        InputManager,
        "_send_unicode_text",
    ) as send_text, patch(
        "input_manager.time.sleep",
    ):
        InputManager._physical_paste(
            99,
            "123123",
        )

    select_all.assert_called_once_with(
        ord("A")
    )
    send_text.assert_called_once_with(
        "123123"
    )


def test_direct_input_accepts_problem_account_credentials():
    with patch.object(
        InputManager,
        "_bring_game_forward",
    ), patch.object(
        InputManager,
        "_foreground_is_target",
        return_value=True,
    ), patch.object(
        InputManager,
        "_send_ctrl_combo",
    ), patch.object(
        InputManager,
        "_send_unicode_text",
    ) as send_text, patch(
        "input_manager.time.sleep",
    ):
        InputManager._physical_paste(
            99,
            "dyplvrrg2",
        )
        InputManager._physical_paste(
            99,
            "123123",
        )

    assert [
        call.args[0]
        for call in send_text.call_args_list
    ] == [
        "dyplvrrg2",
        "123123",
    ]


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
