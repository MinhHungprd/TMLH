"""Independent, interruptible automation for one game profile."""

import threading
import time
from pathlib import Path

import win32gui
import win32process

from automation_constants import (
    ALIVE_DEBUG_LOG_INTERVAL,
    BOSS_ALIVE_SIGNAL_TIMEOUT_SECONDS,
    BOSS_DEAD_CONFIRM_SECONDS,
    BOSS_ENTER_WAIT,
    BOSS_OCR_INTERVAL,
    BOSS_RESPAWN_WAIT,
    BOSS_SCAN_STAGGER_SLOTS,
    BOSS_SCAN_STAGGER_STEP,
    GAME_START_WAIT,
    GAME_STATE_POLL_INTERVAL,
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
    find_window_for_pid,
    get_client_size,
    non_boss_window_visible,
    resize_client,
    set_profile_window_title,
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

    def _rebind_window(self, context):
        if context.process_id is None:
            return None

        hwnd = find_window_for_pid(
            context.process_id
        )

        if hwnd is None:
            return None

        context.window_handle = hwnd

        if context.profile_name:
            set_profile_window_title(
                hwnd,
                context.profile_name,
            )

        return hwnd

    def valid(self, context):
        if context.process_id is None:
            return False

        hwnd = context.window_handle

        if (
            hwnd is not None
            and win32gui.IsWindow(hwnd)
        ):
            owner_pid = (
                win32process
                .GetWindowThreadProcessId(
                    hwnd
                )[1]
            )

            if owner_pid == context.process_id:
                return True

        return (
            self._rebind_window(
                context
            )
            is not None
        )

    def ensure_size(self, context):
        hwnd = context.window_handle

        try:
            width, height = get_client_size(
                hwnd
            )
        except Exception:
            width = height = 0

        if width <= 0 or height <= 0:
            hwnd = self._rebind_window(
                context
            )

            if hwnd is None:
                # Unity can briefly expose only a 0x0 helper window while
                # creating/recreating the real render window. This is a
                # transient "not ready", not a fatal automation error.
                return None

            width, height = get_client_size(
                hwnd
            )

            if width <= 0 or height <= 0:
                return None

        return ensure_client_size(
            hwnd,
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
        # every cycle; stable alive scans are sampled less often to keep
        # the GUI/log queue quiet when many profiles are running.
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

            if resized is None:
                # Window exists but Unity render client is still transient
                # (for example 0x0 while recreating). Wait and retry instead
                # of treating this as a profile failure.
                if self.wait(
                    GAME_STATE_POLL_INTERVAL,
                    self.context.stop_event,
                ):
                    return False
                continue

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
                    # ngoài signal biến mất còn yêu cầu đúng PID có ít nhất
                    # một TCP connection ESTABLISHED. Không hard-code :1002
                    # vì server/route khác có thể dùng remote port khác.
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
                GAME_STATE_POLL_INTERVAL,
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
                # Asset là tín hiệu chính. Miss 3 scan liên tiếp mới mở OCR
                # fallback. Nếu cả hai vẫn không thấy boss liên tục >=3 giây
                # thì xác nhận boss chết.
                # ======================================

                self.context.boss_dead_streak = 0

                dead_candidate_since = None
                boss_alive_since = None

                reset_cycle = getattr(
                    self.boss_detector,
                    "reset_cycle",
                    None,
                )
                if callable(reset_cycle):
                    reset_cycle()

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

                    if resized is None:
                        # Do not feed a transient 0x0 Unity frame into boss
                        # detection or the 3-second death timer.
                        if self.wait(
                            BOSS_OCR_INTERVAL,
                            self.context.stop_event,
                        ):
                            break
                        continue

                    if resized:
                        if self.wait(
                            0.15,
                            self.context.stop_event,
                        ):
                            break

                    if self._halted():
                        break

                    # ==================================
                    # Asset scan; OCR chỉ chạy khi fallback cần thiết.
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
                    # Boss-name OCR detector
                    # ==================================

                    asset_alive = bool(
                        debug.get(
                            "asset_alive",
                            False,
                        )
                    )

                    if "name_alive" in debug:
                        name_alive = bool(
                            debug.get(
                                "name_alive",
                                False,
                            )
                        )
                    else:
                        # Compatibility for injected/legacy detectors used
                        # by tests or custom integrations.
                        name_alive = (
                            self.boss_detector
                            .is_alive(text)
                        )

                    # Asset match is the primary signal. OCR remains only as
                    # the safety fallback after repeated asset misses.
                    ocr_alive = name_alive
                    alive = (
                        asset_alive
                        or ocr_alive
                    )

                    now = self.now()

                    # ==================================
                    # BOSS ALIVE
                    # ==================================

                    if alive:

                        # Một lần detect lại được boss
                        # => hủy toàn bộ dead candidate.
                        dead_candidate_since = None

                        self.context.boss_dead_streak = 0

                        if boss_alive_since is None:
                            boss_alive_since = now

                        alive_for = (
                            now
                            - boss_alive_since
                        )

                        if (
                            alive_for
                            >= BOSS_ALIVE_SIGNAL_TIMEOUT_SECONDS
                        ):
                            self.on_log(
                                (
                                    "BOSS SIGNAL ERROR "
                                    f"alive_for={alive_for:.1f}s/"
                                    f"{BOSS_ALIVE_SIGNAL_TIMEOUT_SECONDS:.0f}s "
                                    "-> force outboss"
                                )
                            )
                            self._state(
                                "BOSS_SIGNAL_ERROR"
                            )
                            break

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
                        # sau 3 giây liên tục không có tín hiệu hợp lệ
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

                    alive_for = (
                        0.0
                        if boss_alive_since is None
                        else (
                            now
                            - boss_alive_since
                        )
                    )

                    should_log_debug = (
                        not alive
                        or (
                            now
                            - self._last_alive_debug_log_at
                            >= ALIVE_DEBUG_LOG_INTERVAL
                        )
                    )

                    if should_log_debug:
                        self.on_log(
                            (
                                f"asset_alive={asset_alive} "
                                f"asset_score="
                                f"{debug.get('asset_score')} "
                                f"asset_miss_streak="
                                f"{debug.get('asset_miss_streak')} "
                                f"fallback_ocr="
                                f"{debug.get('fallback_ocr')} "
                                f"text={text!r} "
                                f"ocr_raw="
                                f"{debug.get('ocr_raw')!r} "
                                f"ocr_candidates="
                                f"{debug.get('ocr_candidates')!r} "
                                f"ocr_alive={ocr_alive} "
                                f"name_norm="
                                f"{debug.get('name_normalized')!r} "
                                f"name_expected="
                                f"{debug.get('name_expected')!r} "
                                f"name_ratio="
                                f"{debug.get('name_ratio')} "
                                f"name_coverage="
                                f"{debug.get('name_coverage')} "
                                f"cache_hit="
                                f"{debug.get('cache_hit')} "
                                f"final_alive={alive} "
                                f"alive_for={alive_for:.1f}s/"
                                f"{BOSS_ALIVE_SIGNAL_TIMEOUT_SECONDS:.0f}s "
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