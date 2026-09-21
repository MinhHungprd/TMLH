"""Automatic login flow for Thiên Mệnh Lạc Hồng."""

from __future__ import annotations

import time

from automation_constants import SIGNAL_1, SIGNAL_3
from boss.enter_boss import has_gameplay_socket
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
from window_manager import (
    find_window_for_pid,
    set_profile_window_title,
    set_window_topmost,
)


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
# After submit, the Start button normally appears quickly. Keep this optional
# window short so a missing S3 does not delay auth confirmation for 12s.
LOGIN_START_OPTIONAL_TIMEOUT = 4.0
LOGIN_GAMEPLAY_READY_TIMEOUT = 25.0
LOGIN_SCAN_INTERVAL = 0.35
LOGIN_POST_SUBMIT_SETTLE = 0.35


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
        hwnd = context.window_handle

        if not hwnd:
            hwnd = None

        try:
            set_window_topmost(
                hwnd,
                True,
            )
            return
        except Exception:
            pass

        # Unity may recreate its top-level render window during login. Rebind
        # the runtime context to the current drawable HWND of the same PID
        # instead of failing with ClientToScreen/invalid-handle errors.
        replacement = find_window_for_pid(
            context.process_id
        )

        if replacement is None:
            raise RuntimeError(
                "Không tìm thấy cửa sổ game hợp lệ trong lúc auto login"
            )

        context.window_handle = replacement

        if getattr(
            context,
            "profile_name",
            None,
        ):
            try:
                set_profile_window_title(
                    replacement,
                    context.profile_name,
                )
            except Exception:
                pass

        set_window_topmost(
            replacement,
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

    def _scan_login_cycle(
        self,
        context,
        signal_name,
    ):
        """
        Scan one login loop from a single screenshot.

        S2 (asset__x742_y437_w45_h24.png) is an exceptional intro/skip
        overlay and is checked on EVERY login polling cycle. If it appears,
        click its scaled center first and do not act on any other signal from
        the same stale frame.
        """
        signal_index = {
            "s1": 0,
            "s2": 1,
            "s3": 2,
        }

        if signal_name not in signal_index:
            raise ValueError(
                f"Unknown login signal: {signal_name}"
            )

        if hasattr(
            self.detector,
            "check_signals",
        ):
            checks = (
                self.detector
                .check_signals(
                    context
                )
            )

            skip = checks[1]

            if skip.detected:
                if skip.coordinates is None:
                    raise RuntimeError(
                        "Tín hiệu skip giới thiệu không có tọa độ click"
                    )

                if not self.input.click_center(
                    context.window_handle,
                    skip.coordinates,
                    context.stop_event,
                    context.process_id,
                ):
                    raise InterruptedError(
                        "Đã dừng auto login"
                    )

                self.on_log(
                    "Auto login: phát hiện giới thiệu → Skip"
                )

                return (
                    None,
                    True,
                )

            return (
                checks[
                    signal_index[
                        signal_name
                    ]
                ],
                False,
            )

        # Compatibility path for injected/custom detectors.
        skip = (
            self.detector
            .check_signal(
                context,
                "s2",
            )
        )

        if skip.detected:
            if skip.coordinates is None:
                raise RuntimeError(
                    "Tín hiệu skip giới thiệu không có tọa độ click"
                )

            if not self.input.click_center(
                context.window_handle,
                skip.coordinates,
                context.stop_event,
                context.process_id,
            ):
                raise InterruptedError(
                    "Đã dừng auto login"
                )

            self.on_log(
                "Auto login: phát hiện giới thiệu → Skip"
            )

            return (
                None,
                True,
            )

        return (
            self.detector
            .check_signal(
                context,
                signal_name,
            ),
            False,
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

            check, skipped_intro = (
                self._scan_login_cycle(
                    context,
                    signal_name,
                )
            )

            if skipped_intro:
                # Give the intro overlay a moment to disappear before the
                # next one-capture login scan.
                self._wait_short(
                    context,
                    0.35,
                )
                continue

            if (
                check is not None
                and check.detected
            ):
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
            LOGIN_POST_SUBMIT_SETTLE,
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

        if start is not None:
            self._click(
                context,
                roi_center(
                    SIGNAL_3
                ),
            )
            self.on_log(
                "Auto login: đã bấm Bắt đầu"
            )
        else:
            self.on_log(
                "Auto login: chưa thấy nút Bắt đầu, tiếp tục chờ gameplay socket"
            )

        # Do not mark/save the profile as successfully logged in until the
        # exact game PID has opened gameplay :1002. Previously Registry auth
        # alone could mark READY while the client was still on a login/menu
        # screen, causing the next normal Start to hang at
        # WAITING_GAMEPLAY_SOCKET.
        self.on_status(
            "LOGIN_WAITING_GAMEPLAY"
        )
        self.on_log(
            "Auto login: chờ gameplay socket :1002"
        )

        deadline = (
            self.now()
            + LOGIN_GAMEPLAY_READY_TIMEOUT
        )

        while (
            self.now() < deadline
            and not context.stop_event.is_set()
        ):
            if has_gameplay_socket(
                context.process_id
            ):
                self.on_log(
                    "Auto login: gameplay socket :1002 đã sẵn sàng"
                )
                return True

            self._prepare_window(
                context
            )

            # Keep handling intro/start overlays while waiting for gameplay.
            checks = (
                self.detector
                .check_signals(
                    context
                )
            )

            skip = checks[1]
            start = checks[2]

            if skip.detected:
                if (
                    skip.coordinates
                    is not None
                ):
                    self.input.click_center(
                        context.window_handle,
                        skip.coordinates,
                        context.stop_event,
                        context.process_id,
                    )
                    self.on_log(
                        "Auto login: phát hiện giới thiệu → Skip"
                    )
                    self._wait_short(
                        context,
                        0.35,
                    )
                    continue

            if start.detected:
                self._click(
                    context,
                    roi_center(
                        SIGNAL_3
                    ),
                )
                self.on_log(
                    "Auto login: phát hiện nút Bắt đầu → bấm"
                )
                self._wait_short(
                    context,
                    0.35,
                )
                continue

            self._wait_short(
                context,
                LOGIN_SCAN_INTERVAL,
            )

        if context.stop_event.is_set():
            raise InterruptedError(
                "Đã dừng auto login"
            )

        raise TimeoutError(
            (
                "Đăng nhập chưa hoàn tất: không thấy gameplay socket "
                f":1002 trong {LOGIN_GAMEPLAY_READY_TIMEOUT:.0f}s"
            )
        )
