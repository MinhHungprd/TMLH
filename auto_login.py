"""Automatic login flow for Thiên Mệnh Lạc Hồng."""

from __future__ import annotations

import time

from automation_constants import SIGNAL_1
from game_state import GameStateDetector
from input_manager import InputManager
from profile_credentials import (
    LoginCredentials,
    SERVER_LABELS,
)
from vision import (
    base_point_to_client,
    get_client_size,
    roi_center,
)
from window_manager import set_window_topmost


SERVER_MENU_POINT = (429, 354)
SERVER_POINTS = {
    "van_lang": (439, 200),
    "au_lac": (438, 245),
}

LOGIN_SIGNAL_POINT = roi_center(
    SIGNAL_1
)
LOGIN_FORM_POINT = (444, 265)
USERNAME_POINT = (418, 186)
PASSWORD_POINT = (429, 238)
LOGIN_SUBMIT_POINT = (439, 307)

LOGIN_SIGNAL_TIMEOUT = 45.0
LOGIN_SECOND_SIGNAL_TIMEOUT = 25.0
LOGIN_START_OPTIONAL_TIMEOUT = 12.0
LOGIN_SCAN_INTERVAL = 0.5


class AutoLoginRunner:
    def __init__(
        self,
        *,
        detector=None,
        input_manager=None,
        wait=None,
        now=None,
        on_status=None,
        on_log=None,
    ):
        self.detector = (
            detector
            or GameStateDetector()
        )
        self.input = (
            input_manager
            or InputManager()
        )
        self.wait = (
            wait
            or (
                lambda seconds, event:
                event.wait(seconds)
            )
        )
        self.now = (
            now
            or time.monotonic
        )
        self.on_status = (
            on_status
            or (lambda _state: None)
        )
        self.on_log = (
            on_log
            or (lambda _message: None)
        )

    @staticmethod
    def _scaled_point(
        context,
        base_point,
    ):
        width, height = get_client_size(
            context.window_handle
        )

        if (
            width <= 0
            or height <= 0
        ):
            raise RuntimeError(
                "Game client chưa sẵn sàng để thao tác đăng nhập"
            )

        return base_point_to_client(
            base_point,
            width,
            height,
        )

    def _prepare_window(
        self,
        context,
    ):
        set_window_topmost(
            context.window_handle,
            True,
        )

    def _click(
        self,
        context,
        base_point,
    ):
        self._prepare_window(
            context
        )

        coordinates = self._scaled_point(
            context,
            base_point,
        )

        if not self.input.click_center(
            context.window_handle,
            coordinates,
            context.stop_event,
            context.process_id,
        ):
            raise InterruptedError(
                "Đã dừng auto login"
            )

    def _click_and_paste(
        self,
        context,
        base_point,
        value,
    ):
        self._prepare_window(
            context
        )

        coordinates = self._scaled_point(
            context,
            base_point,
        )

        if not self.input.click_and_paste(
            context.window_handle,
            coordinates,
            value,
            context.stop_event,
            context.process_id,
        ):
            raise InterruptedError(
                "Đã dừng auto login"
            )

    def _wait_short(
        self,
        context,
        seconds,
    ):
        if self.wait(
            seconds,
            context.stop_event,
        ):
            raise InterruptedError(
                "Đã dừng auto login"
            )

    def _wait_for_signal(
        self,
        context,
        signal_name,
        timeout,
        *,
        optional=False,
    ):
        deadline = (
            self.now()
            + timeout
        )

        while (
            self.now() < deadline
            and not context.stop_event.is_set()
        ):
            self._prepare_window(
                context
            )

            check = (
                self.detector
                .check_signal(
                    context,
                    signal_name,
                )
            )

            if check.detected:
                return check

            self._wait_short(
                context,
                LOGIN_SCAN_INTERVAL,
            )

        if context.stop_event.is_set():
            raise InterruptedError(
                "Đã dừng auto login"
            )

        if optional:
            return None

        raise TimeoutError(
            (
                f"Không thấy tín hiệu {signal_name} "
                f"trong {timeout:.0f}s"
            )
        )

    def run(
        self,
        context,
        credentials: LoginCredentials,
    ) -> bool:
        """
        Execute the requested login flow.

        Returns True when the optional Start button was found and clicked.
        """
        credentials = (
            credentials.validate()
        )

        server_label = (
            SERVER_LABELS[
                credentials.server
            ]
        )

        self.on_status(
            "LOGIN_SELECT_SERVER"
        )
        self.on_log(
            (
                "Auto login: chờ tín hiệu chọn server "
                f"({server_label})"
            )
        )

        self._wait_for_signal(
            context,
            "s1",
            LOGIN_SIGNAL_TIMEOUT,
        )

        self._click(
            context,
            SERVER_MENU_POINT,
        )
        self._wait_short(
            context,
            0.45,
        )

        self._click(
            context,
            SERVER_POINTS[
                credentials.server
            ],
        )
        self._wait_short(
            context,
            0.8,
        )

        self.on_status(
            "LOGIN_ENTER_CREDENTIALS"
        )
        self.on_log(
            "Auto login: chờ form tài khoản"
        )

        self._wait_for_signal(
            context,
            "s1",
            LOGIN_SECOND_SIGNAL_TIMEOUT,
        )

        # User-specified flow: tap the matched signal first, then open form.
        self._click(
            context,
            LOGIN_SIGNAL_POINT,
        )
        self._wait_short(
            context,
            0.3,
        )

        self._click(
            context,
            LOGIN_FORM_POINT,
        )
        self._wait_short(
            context,
            0.4,
        )

        self._click_and_paste(
            context,
            USERNAME_POINT,
            credentials.username,
        )
        self._wait_short(
            context,
            0.2,
        )

        self._click_and_paste(
            context,
            PASSWORD_POINT,
            credentials.password,
        )
        self._wait_short(
            context,
            0.2,
        )

        self.on_status(
            "LOGIN_SUBMITTING"
        )

        self._click(
            context,
            LOGIN_SUBMIT_POINT,
        )
        self._wait_short(
            context,
            1.0,
        )

        self.on_status(
            "LOGIN_WAITING_START"
        )

        start = self._wait_for_signal(
            context,
            "s3",
            LOGIN_START_OPTIONAL_TIMEOUT,
            optional=True,
        )

        if start is None:
            self.on_log(
                "Auto login: không thấy nút Bắt đầu, bỏ qua bước tùy chọn"
            )
            return False

        # S3 already reports its correctly scaled center, but using the base
        # signal center keeps the flow deterministic across all resolutions.
        from automation_constants import SIGNAL_3

        self._click(
            context,
            roi_center(
                SIGNAL_3
            ),
        )
        self.on_log(
            "Auto login: đã bấm Bắt đầu"
        )

        return True
