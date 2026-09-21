"""Independent, interruptible automation for one game profile."""

import threading
import time
from pathlib import Path

import win32gui
import win32process

from automation_constants import (
    BOSS_DEAD_CONFIRM_SECONDS,
    BOSS_ENTER_WAIT,
    BOSS_OCR_INTERVAL,
    BOSS_RESPAWN_WAIT,
    BOSS_SCAN_STAGGER_SLOTS,
    BOSS_SCAN_STAGGER_STEP,
    GAME_START_WAIT,
    IN_GAME_CONFIRM_SECONDS,
)

from boss.enter_boss import (
    enter_boss,
    exit_boss,
    has_gameplay_socket,
)
from boss_detector import BossDetector
from game_state import CLICK_CENTER, GameStateDetector
from input_manager import InputManager
from perf_metrics import PERF_METRICS, format_perf_report
from profile_manager import MissingGameFilesError
from profile_auth import restore_profile_auth

from window_manager import (
    PROFILE_LAUNCH_LOCK,
    acquire_profile_window,
    ensure_client_size,
    non_boss_window_visible,
    resize_client,
)


def interruptible_wait(seconds, stop_event):
    return stop_event.wait(seconds)


_BOSS_SCAN_STAGGER_LOCK = threading.Lock()
_BOSS_SCAN_STAGGER_NEXT_SLOT = 0


def reserve_boss_scan_stagger() -> float:
    """
    Assign workers a small one-time scan phase offset.

    With the default 10 slots, workers are spread across one second:
    0.0s, 0.1s, ... 0.9s. Each worker still scans at the same
    BOSS_OCR_INTERVAL afterwards.
    """
    global _BOSS_SCAN_STAGGER_NEXT_SLOT

    with _BOSS_SCAN_STAGGER_LOCK:
        slot = (
            _BOSS_SCAN_STAGGER_NEXT_SLOT
            % BOSS_SCAN_STAGGER_SLOTS
        )
        _BOSS_SCAN_STAGGER_NEXT_SLOT += 1

    return slot * BOSS_SCAN_STAGGER_STEP


class GameLifecycle:
    def open(self, context):
        launcher = (
            Path(context.game_path)
            / "ThienMenhLacHong_Launcher.exe"
        )

        if not launcher.is_file():
            raise MissingGameFilesError(
                context.game_path
            )

        return acquire_profile_window(
            context.game_path,
            context.stop_event,
            before_launch=lambda: restore_profile_auth(
                context.game_path
            ),
            window_title=context.profile_name,
        )

    def resize(self, context):
        resize_client(
            context.window_handle,
            context.window_width,
            context.window_height,
        )

    def valid(self, context):
        if (
            context.process_id is None
            or context.window_handle is None
        ):
            return False

        if not win32gui.IsWindow(
            context.window_handle
        ):
            return False

        owner_pid = (
            win32process
            .GetWindowThreadProcessId(
                context.window_handle
            )[1]
        )

        return (
            owner_pid
            == context.process_id
        )

    def ensure_size(self, context):
        return ensure_client_size(
            context.window_handle,
            context.window_width,
            context.window_height,
        )


class AutomationWorker:
    def __init__(
        self,
        context,
        on_status=None,
        on_error=None,
        *,
        lifecycle=None,
        detector=None,
        boss_detector=None,
        input_manager=None,
        enter=None,
        exit=None,
        wait=None,
        now=None,
        gameplay_ready=None,
        on_log=None,
        boss_scan_stagger=None,
    ):
        self.context = context

        self.on_status = (
            on_status
            or (lambda _state: None)
        )

        self.on_error = (
            on_error
            or (lambda _exc: None)
        )

        self.on_log = (
            on_log
            or (lambda _message: None)
        )

        self.lifecycle = (
            lifecycle
            or GameLifecycle()
        )

        self.detector = (
            detector
            or GameStateDetector()
        )

        self.boss_detector = (
            boss_detector
            or BossDetector()
        )

        self.input_manager = (
            input_manager
            or InputManager()
        )

        self.gameplay_ready = (
            gameplay_ready
            or has_gameplay_socket
        )

        self.enter = (
            enter
            or (
                lambda pid, boss:
                enter_boss(
                    pid,
                    boss,
                    stop_event=context.stop_event,
                )
            )
        )

        self.exit = (
            exit
            or (
                lambda pid:
                exit_boss(
                    pid,
                    stop_event=context.stop_event,
                )
            )
        )

        self.wait = (
            wait
            or interruptible_wait
        )

        self.now = (
            now
            or time.monotonic
        )

        self.thread = None

        self.boss_scan_stagger = (
            reserve_boss_scan_stagger()
            if boss_scan_stagger is None
            else max(
                0.0,
                float(boss_scan_stagger),
            )
        )
        self._boss_scan_stagger_applied = False

        # Diagnostic OCR logs are useful, but logging every alive scan from
        # many profiles wastes GUI/queue work. Dead-candidate scans still log
        # every cycle; stable alive scans are sampled every 3 seconds.
        self._last_alive_debug_log_at = float("-inf")

    def start(self):
        if (
            self.thread
            and self.thread.is_alive()
        ):
            raise RuntimeError(
                "Profile worker already running"
            )

        self.thread = threading.Thread(
            target=self.run,
            name=(
                f"profile-"
                f"{self.context.profile_id}"
            ),
            daemon=True,
        )

        self.thread.start()

    def stop(self):
        self.context.stop_event.set()

    def wait_stopped(
        self,
        timeout=3.0,
    ) -> bool:
        thread = self.thread

        if thread is None:
            return True

        if (
            thread
            is threading.current_thread()
        ):
            return False

        thread.join(timeout)

        return not thread.is_alive()

    def is_running(self) -> bool:
        return (
            self.thread is not None
            and self.thread.is_alive()
        )

    def _state(self, name):
        if self.context.state != name:
            self.context.state = name
            self.on_status(name)

    def _halted(self):
        return (
            self.context
            .stop_event
            .is_set()
        )


    def _emit_perf_if_due(self):
        report = PERF_METRICS.drain_if_due()

        if report:
            self.on_log(
                format_perf_report(
                    report
                )
            )

    def _ensure_in_game(
        self,
        require_gameplay_socket=False,
        timeout=None,
    ):
        absent_since = None

        deadline = (
            self.now() + timeout
            if timeout is not None
            else None
        )

        self._state(
            "WAITING_GAME"
        )

        while not self._halted():

            # ==========================================
            # Timeout
            # ==========================================

            if (
                deadline is not None
                and self.now() >= deadline
            ):
                raise RuntimeError(
                    (
                        f"{self.context.profile_name}: "
                        "không thể hoàn tất quá trình "
                        "vào game "
                        f"trong {timeout:.0f}s"
                    )
                )

            # ==========================================
            # Validate PID/HWND
            # ==========================================

            if not self.lifecycle.valid(
                self.context
            ):
                raise RuntimeError(
                    (
                        "Game window closed "
                        "or changed owner"
                    )
                )

            # ==========================================
            # Unity có thể tự đổi resolution
            # ==========================================

            resized = (
                self.lifecycle.ensure_size(
                    self.context
                )
            )

            if resized:
                if self.wait(
                    0.15,
                    self.context.stop_event,
                ):
                    return False

            # ==========================================
            # Detect startup assets
            #
            # In overlap mode, only non-boss asset capture/click needs the
            # whole game window visible. Temporarily raise this exact HWND,
            # perform capture + click, then restore its stack position.
            # Boss HP scanning never enters this context.
            # ==========================================

            with non_boss_window_visible(
                self.context.window_handle
            ):
                checks = (
                    self.detector
                    .check_signals(
                        self.context
                    )
                )

                self._emit_perf_if_due()

                if self._halted():
                    return False

                detected_checks = [
                    check
                    for check in checks
                    if check.detected
                ]

                # ======================================
                # Có signal -> chưa vào game
                # ======================================

                if detected_checks:

                    absent_since = None

                    self._state(
                        "WAITING_GAME"
                    )

                    for check in detected_checks:

                        if self._halted():
                            return False

                        if (
                            check.action
                            == CLICK_CENTER
                            and
                            check.coordinates
                            is not None
                        ):
                            self.input_manager.click_center(
                                self.context.window_handle,
                                check.coordinates,
                                self.context.stop_event,
                                self.context.process_id,
                            )

            # ==========================================
            # Không còn startup signal
            # ==========================================

            if not detected_checks:
                observed_at = self.now()

                if absent_since is None:
                    absent_since = observed_at

                if (
                    observed_at
                    - absent_since
                    >= IN_GAME_CONFIRM_SECONDS
                ):

                    # Sau boss cycle:
                    # không cần check socket startup.
                    if not require_gameplay_socket:

                        self._state(
                            "IN_GAME"
                        )

                        return True

                    # Startup:
                    # ngoài signal biến mất
                    # còn yêu cầu PID có socket :1002.
                    if self.gameplay_ready(
                        self.context.process_id
                    ):
                        self._state(
                            "IN_GAME"
                        )

                        return True

                    self._state(
                        "WAITING_GAMEPLAY_SOCKET"
                    )

            # ==========================================
            # Poll startup UI
            # ==========================================

            if self.wait(
                0.25,
                self.context.stop_event,
            ):
                return False

        return False

    def run(self):
        try:
            if self._halted():
                return

            self._state(
                "LAUNCHING_GAME"
            )

            initial_in_game = False

            # ==========================================
            # START PROFILE
            #
            # Registry auth là global theo Windows user.
            #
            # Phải serialize:
            #
            # restore account
            # -> launch
            # -> click startup UI
            # -> socket :1002
            # -> release lock
            #
            # Sau đó profile tiếp theo mới được restore
            # Registry.
            # ==========================================

            with PROFILE_LAUNCH_LOCK:

                pid, hwnd, launched = (
                    self.lifecycle.open(
                        self.context
                    )
                )

                self.context.process_id = pid
                self.context.window_handle = hwnd

                if self._halted():
                    return

                self.lifecycle.resize(
                    self.context
                )

                # ======================================
                # Chờ Unity startup
                # ======================================

                if launched:

                    self._state(
                        "WAITING_STARTUP"
                    )

                    if self.wait(
                        GAME_START_WAIT,
                        self.context.stop_event,
                    ):
                        return

                # ======================================
                # Detect/click startup assets
                # cho đến khi đúng PID có :1002
                # ======================================

                if not self._ensure_in_game(
                    require_gameplay_socket=True,
                    timeout=90.0,
                ):
                    return

                initial_in_game = True

            # ==========================================
            # MAIN AUTOMATION LOOP
            # ==========================================

            while not self._halted():

                # ======================================
                # Sau startup lần đầu đã IN_GAME rồi
                # ======================================

                if initial_in_game:
                    initial_in_game = False

                else:
                    if not self._ensure_in_game():
                        break

                if self._halted():
                    break

                # ======================================
                # ENTER BOSS
                # ======================================

                self._state(
                    "ENTERING_BOSS"
                )

                self.enter(
                    self.context.process_id,
                    self.context.selected_boss,
                )

                self._emit_perf_if_due()

                if self._halted():
                    break

                # ======================================
                # WAIT BOSS LOAD
                # ======================================

                self._state(
                    "WAITING_BOSS_LOAD"
                )

                if self.wait(
                    BOSS_ENTER_WAIT,
                    self.context.stop_event,
                ):
                    break

                if self._halted():
                    break

                # ======================================
                # CHECK BOSS
                #
                # Không thấy boss liên tục >= 3 giây
                # => xác nhận boss chết.
                #
                # Chỉ cần OCR hoặc visual detect lại
                # => reset timer chết ngay.
                # ======================================

                self.context.boss_dead_streak = 0

                dead_candidate_since = None

                # Apply the phase offset only once for this worker. After
                # that, the existing 1-second scan cadence keeps profiles
                # naturally separated without changing death confirmation.
                if (
                    not self._boss_scan_stagger_applied
                    and self.boss_scan_stagger > 0
                ):
                    if self.wait(
                        self.boss_scan_stagger,
                        self.context.stop_event,
                    ):
                        break

                    self._boss_scan_stagger_applied = True

                    if self._halted():
                        break
                else:
                    self._boss_scan_stagger_applied = True

                self._state(
                    "CHECKING_BOSS"
                )

                while not self._halted():

                    # ==================================
                    # Validate game
                    # ==================================

                    if not self.lifecycle.valid(
                        self.context
                    ):
                        raise RuntimeError(
                            (
                                "Game window closed "
                                "or changed owner"
                            )
                        )

                    # ==================================
                    # Ensure resolution
                    # ==================================

                    resized = (
                        self.lifecycle
                        .ensure_size(
                            self.context
                        )
                    )

                    if resized:
                        if self.wait(
                            0.15,
                            self.context.stop_event,
                        ):
                            break

                    if self._halted():
                        break

                    # ==================================
                    # OCR đúng MỘT lần / chu kỳ
                    # ==================================

                    text = (
                        self.boss_detector
                        .read_hp(
                            self.context
                        )
                    )

                    self._emit_perf_if_due()

                    if self._halted():
                        break

                    debug = getattr(
                        self.boss_detector,
                        "last_debug",
                        {},
                    )

                    # ==================================
                    # OCR detector
                    # ==================================

                    ocr_alive = (
                        self.boss_detector
                        .is_alive(text)
                    )

                    # ==================================
                    # Visual detector
                    # ==================================

                    visual_alive = bool(
                        debug.get(
                            "visual_alive",
                            False,
                        )
                    )

                    # ==================================
                    # Chỉ cần một detector thấy boss
                    # => boss sống
                    # ==================================

                    alive = (
                        ocr_alive
                        or visual_alive
                    )

                    now = self.now()

                    # ==================================
                    # BOSS ALIVE
                    # ==================================

                    if alive:

                        # Một lần detect lại được HP
                        # => hủy toàn bộ dead candidate.
                        dead_candidate_since = None

                        self.context.boss_dead_streak = 0

                        self._state(
                            "BOSS_ALIVE"
                        )

                    # ==================================
                    # KHÔNG THẤY BOSS
                    # ==================================

                    else:

                        # Frame fail đầu tiên.
                        if dead_candidate_since is None:
                            dead_candidate_since = now

                        self.context.boss_dead_streak += 1

                        dead_for = (
                            now
                            - dead_candidate_since
                        )

                        # Không yêu cầu phải từng
                        # detect boss sống.
                        #
                        # Nếu vào map mà boss vốn đã chết,
                        # sau 3 giây liên tục không thấy HP
                        # vẫn phải out boss.
                        if (
                            dead_for
                            >= BOSS_DEAD_CONFIRM_SECONDS
                        ):

                            self.on_log(
                                (
                                    "DEAD CONFIRMED "
                                    f"dead_for="
                                    f"{dead_for:.1f}s "
                                    f"streak="
                                    f"{self.context.boss_dead_streak}"
                                )
                            )

                            self._state(
                                "BOSS_DEAD"
                            )

                            break

                        self._state(
                            "BOSS_DEAD_CONFIRMING"
                        )

                    # ==================================
                    # DEBUG LOG
                    # ==================================

                    digits = "".join(
                        char
                        for char in (text or "")
                        if char.isdigit()
                    )

                    dead_for = (
                        0.0
                        if dead_candidate_since is None
                        else (
                            now
                            - dead_candidate_since
                        )
                    )

                    should_log_debug = (
                        not alive
                        or (
                            now
                            - self._last_alive_debug_log_at
                            >= 3.0
                        )
                    )

                    if should_log_debug:
                        self.on_log(
                            (
                                f"text={text!r} "
                                f"digits={digits!r} "
                                f"ocr_alive={ocr_alive} "
                                f"visual_glyphs="
                                f"{debug.get('visual_glyphs')} "
                                f"visual_alive={visual_alive} "
                                f"final_alive={alive} "
                                f"dead_streak="
                                f"{self.context.boss_dead_streak} "
                                f"dead_for="
                                f"{dead_for:.1f}s/"
                                f"{BOSS_DEAD_CONFIRM_SECONDS:.1f}s"
                            )
                        )

                        if alive:
                            self._last_alive_debug_log_at = now

                    # ==================================
                    # Quét lại theo interval.
                    #
                    # Yêu cầu hiện tại:
                    # BOSS_OCR_INTERVAL = 1.0
                    # ==================================

                    if self.wait(
                        BOSS_OCR_INTERVAL,
                        self.context.stop_event,
                    ):
                        break

                # ======================================
                # QUAN TRỌNG:
                #
                # Nếu thoát boss loop vì Stop,
                # không được gửi exit_boss().
                # ======================================

                if self._halted():
                    break

                # ======================================
                # EXIT BOSS
                # ======================================

                self._state(
                    "EXITING_BOSS"
                )

                self.exit(
                    self.context.process_id
                )

                self._emit_perf_if_due()

                if self._halted():
                    break

                # ======================================
                # WAIT RESPAWN
                # ======================================

                self._state(
                    "WAITING_RESPAWN"
                )

                if self.wait(
                    BOSS_RESPAWN_WAIT,
                    self.context.stop_event,
                ):
                    break

        except Exception as exc:

            if not self._halted():
                self._state(
                    "ERROR"
                )

                self.on_error(exc)

        finally:

            if self.context.state != "ERROR":
                self._state(
                    "STOPPED"
                )