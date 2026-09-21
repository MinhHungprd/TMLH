from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from auto_login import (
    AutoLoginRunner,
    LOGIN_FORM_POINT,
    LOGIN_SIGNAL_POINT,
    LOGIN_SUBMIT_POINT,
    PASSWORD_POINT,
    SERVER_MENU_POINT,
    SERVER_POINTS,
    USERNAME_POINT,
)
from automation_constants import SIGNAL_3
from game_state import SignalCheck
from profile_credentials import LoginCredentials
from vision import (
    base_point_to_client,
    roi_center,
)


class Detector:
    def __init__(self):
        self.calls = []

    def check_signal(
        self,
        context,
        name,
    ):
        self.calls.append(name)

        if name == "s2":
            return SignalCheck(
                False,
                None,
            )

        return SignalCheck(
            True,
            None,
        )


class Input:
    def __init__(self):
        self.calls = []

    def click_center(
        self,
        hwnd,
        coordinates,
        stop_event=None,
        expected_pid=None,
    ):
        self.calls.append(
            (
                "click",
                coordinates,
            )
        )
        return True

    def click_and_paste(
        self,
        hwnd,
        coordinates,
        text,
        stop_event=None,
        expected_pid=None,
    ):
        self.calls.append(
            (
                "paste",
                coordinates,
                text,
            )
        )
        return True


def test_auto_login_scales_all_base_coordinates_to_client():
    detector = Detector()
    inputs = Input()
    states = []

    context = SimpleNamespace(
        window_handle=10,
        process_id=20,
        stop_event=Event(),
    )

    runner = AutoLoginRunner(
        detector=detector,
        input_manager=inputs,
        wait=lambda seconds, event: False,
        on_status=states.append,
    )

    width = 480
    height = 270

    with patch(
        "auto_login.get_client_size",
        return_value=(
            width,
            height,
        ),
    ), patch(
        "auto_login.set_window_topmost",
    ):
        clicked_start = runner.run(
            context,
            LoginCredentials(
                username="account",
                password="secret",
                server="van_lang",
            ),
        )

    scale = lambda point: (
        base_point_to_client(
            point,
            width,
            height,
        )
    )

    assert clicked_start is True
    assert detector.calls == [
        "s2",
        "s1",
        "s2",
        "s1",
        "s2",
        "s3",
    ]

    assert inputs.calls == [
        (
            "click",
            scale(SERVER_MENU_POINT),
        ),
        (
            "click",
            scale(
                SERVER_POINTS[
                    "van_lang"
                ]
            ),
        ),
        (
            "click",
            scale(LOGIN_SIGNAL_POINT),
        ),
        (
            "click",
            scale(LOGIN_FORM_POINT),
        ),
        (
            "paste",
            scale(USERNAME_POINT),
            "account",
        ),
        (
            "paste",
            scale(PASSWORD_POINT),
            "secret",
        ),
        (
            "click",
            scale(LOGIN_SUBMIT_POINT),
        ),
        (
            "click",
            scale(
                roi_center(
                    SIGNAL_3
                )
            ),
        ),
    ]

    assert states == [
        "LOGIN_SELECT_SERVER",
        "LOGIN_ENTER_CREDENTIALS",
        "LOGIN_SUBMITTING",
        "LOGIN_WAITING_START",
    ]


def test_auto_login_uses_au_lac_server_coordinate():
    detector = Detector()
    inputs = Input()

    context = SimpleNamespace(
        window_handle=10,
        process_id=20,
        stop_event=Event(),
    )

    runner = AutoLoginRunner(
        detector=detector,
        input_manager=inputs,
        wait=lambda seconds, event: False,
    )

    with patch(
        "auto_login.get_client_size",
        return_value=(860, 484),
    ), patch(
        "auto_login.set_window_topmost",
    ):
        runner.run(
            context,
            LoginCredentials(
                username="u",
                password="p",
                server="au_lac",
            ),
        )

    assert inputs.calls[1] == (
        "click",
        SERVER_POINTS["au_lac"],
    )



def test_auto_login_skips_intro_before_processing_target_signal():
    class IntroDetector:
        def __init__(self):
            self.calls = 0

        def check_signals(
            self,
            context,
        ):
            self.calls += 1

            if self.calls == 1:
                return [
                    SignalCheck(
                        True,
                        None,
                    ),
                    SignalCheck(
                        True,
                        "CLICK_CENTER",
                        (426, 251),
                    ),
                    SignalCheck(
                        False,
                        None,
                    ),
                ]

            return [
                SignalCheck(
                    True,
                    None,
                ),
                SignalCheck(
                    False,
                    None,
                ),
                SignalCheck(
                    False,
                    None,
                ),
            ]

    detector = IntroDetector()
    inputs = Input()
    logs = []

    context = SimpleNamespace(
        window_handle=10,
        process_id=20,
        stop_event=Event(),
    )

    runner = AutoLoginRunner(
        detector=detector,
        input_manager=inputs,
        wait=lambda seconds, event: False,
        now=iter(
            [
                0.0,
                0.0,
                0.2,
                0.2,
            ]
        ).__next__,
        on_log=logs.append,
    )

    with patch(
        "auto_login.set_window_topmost",
    ):
        result = runner._wait_for_signal(
            context,
            "s1",
            5.0,
        )

    assert result.detected is True
    assert detector.calls == 2
    assert inputs.calls == [
        (
            "click",
            (426, 251),
        )
    ]
    assert any(
        "Skip"
        in message
        for message in logs
    )
