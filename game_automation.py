"""Independent, interruptible automation for one game profile."""

import threading
import time
from pathlib import Path

import win32gui
import win32process

from automation_constants import (
    BOSS_ENTER_WAIT, BOSS_OCR_INTERVAL, BOSS_RESPAWN_WAIT,
    GAME_START_WAIT, IN_GAME_CONFIRM_SECONDS,
)
from boss.enter_boss import (
    enter_boss,
    exit_boss,
    has_gameplay_socket,
)
from boss_detector import BossDetector
from game_state import CLICK_CENTER, GameStateDetector
from input_manager import InputManager
from profile_manager import MissingGameFilesError

from profile_auth import restore_profile_auth

from window_manager import (
    PROFILE_LAUNCH_LOCK,
    acquire_profile_window,
    ensure_client_size,
    resize_client,
    wait_for_remote_port,
)
GAME_SERVER_IP = "14.225.213.205"
GAMEPLAY_PORT = 1002
def interruptible_wait(seconds, stop_event):
    return stop_event.wait(seconds)


class GameLifecycle:
    def open(self, context):
        if not (
            Path(context.game_path)
            / "ThienMenhLacHong_Launcher.exe"
        ).is_file():
            raise MissingGameFilesError(
                context.game_path
            )

        return acquire_profile_window(
            context.game_path,
            context.stop_event,
            before_launch=lambda: restore_profile_auth(
                context.game_path
            ),
        )

    def resize(self, context):
        resize_client(context.window_handle, context.window_width, context.window_height)

    def valid(self, context):
        if context.process_id is None or context.window_handle is None:
            return False
        if not win32gui.IsWindow(context.window_handle):
            return False
        return win32process.GetWindowThreadProcessId(context.window_handle)[1] == context.process_id
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
    ):
        self.gameplay_ready = (
            gameplay_ready
            or has_gameplay_socket
        )
        self.on_log = (
            on_log
            or (lambda _message: None)
        )
        self.context = context
        self.on_status = on_status or (lambda _state: None)
        self.on_error = on_error or (lambda _exc: None)
        self.lifecycle = lifecycle or GameLifecycle()
        self.detector = detector or GameStateDetector()
        self.boss_detector = boss_detector or BossDetector()
        self.input_manager = input_manager or InputManager()
        self.enter = enter or (lambda pid, boss: enter_boss(pid, boss, stop_event=context.stop_event))
        self.exit = exit or (lambda pid: exit_boss(pid, stop_event=context.stop_event))
        self.wait = wait or interruptible_wait
        self.now = now or time.monotonic
        self.thread = None

    def start(self):
        if self.thread and self.thread.is_alive():
            raise RuntimeError("Profile worker already running")
        self.thread = threading.Thread(target=self.run, name=f"profile-{self.context.profile_id}", daemon=True)
        self.thread.start()

    def stop(self):
        self.context.stop_event.set()
    def wait_stopped(self, timeout=3.0) -> bool:
        thread = self.thread

        if thread is None:
            return True

        if thread is threading.current_thread():
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
        return self.context.stop_event.is_set()

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

        self._state("WAITING_GAME")

        while not self._halted():
            if (
                deadline is not None
                and self.now() >= deadline
            ):
                raise RuntimeError(
                    f"{self.context.profile_name}: "
                    "không thể hoàn tất quá trình vào game "
                    f"trong {timeout:.0f}s"
                )

            if not self.lifecycle.valid(self.context):
                raise RuntimeError(
                    "Game window closed or changed owner"
                )

            # Unity có thể đổi resolution khi đổi scene.
            resized = self.lifecycle.ensure_size(
                self.context
            )

            if resized:
                if self.wait(
                    0.15,
                    self.context.stop_event,
                ):
                    return False

            # QUAN TRỌNG:
            # Detector phải tiếp tục chạy trong lúc chờ :1002,
            # để s2/s3 xuất hiện thì bot còn click được.
            checks = self.detector.check_signals(
                self.context
            )

            if self._halted():
                return False

            detected_checks = [
                check
                for check in checks
                if check.detected
            ]

            if detected_checks:
                absent_since = None

                self._state("WAITING_GAME")

                for check in detected_checks:
                    if self._halted():
                        return False

                    if (
                        check.action == CLICK_CENTER
                        and check.coordinates is not None
                    ):
                        self.input_manager.click_center(
                            self.context.window_handle,
                            check.coordinates,
                            self.context.stop_event,
                            self.context.process_id,
                        )

            else:
                observed_at = self.now()

                if absent_since is None:
                    absent_since = observed_at

                if (
                    observed_at - absent_since
                    >= IN_GAME_CONFIRM_SECONDS
                ):
                    # Normal boss cycle:
                    # giữ hành vi cũ.
                    if not require_gameplay_socket:
                        self._state("IN_GAME")
                        return True

                    # Startup đặc biệt:
                    # signal biến mất CHƯA đủ.
                    # PID còn phải có gameplay socket :1002.
                    if self.gameplay_ready(
                        self.context.process_id
                    ):
                        self._state("IN_GAME")
                        return True

                    # Chưa :1002 thì KHÔNG return.
                    # Tiếp tục screenshot để nếu signal tiếp theo
                    # xuất hiện thì bot vẫn click được.
                    self._state(
                        "WAITING_GAMEPLAY_SOCKET"
                    )

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
            self._state("LAUNCHING_GAME")

            initial_in_game = False

            # Account Registry là global.
            #
            # Phải giữ lock từ lúc:
            # restore account
            # → launch
            # → click startup UI
            # → có socket :1002
            #
            # thì profile sau mới được phép restore Registry.
            with PROFILE_LAUNCH_LOCK:
                pid, hwnd, launched = self.lifecycle.open(
                    self.context
                )

                self.context.process_id = pid
                self.context.window_handle = hwnd

                if self._halted():
                    return

                self.lifecycle.resize(
                    self.context
                )

                if launched:
                    self._state("WAITING_STARTUP")

                    if self.wait(
                        GAME_START_WAIT,
                        self.context.stop_event,
                    ):
                        return

                # Đây mới là chỗ xử lý startup.
                #
                # Nó vừa detect/click asset,
                # vừa chờ cho đến khi đúng PID có :1002.
                if not self._ensure_in_game(
                    require_gameplay_socket=True,
                    timeout=90.0,
                ):
                    return

                initial_in_game = True
                if self._halted():
                    return

                self.lifecycle.resize(self.context)

                if launched:
                    self._state("WAITING_STARTUP")

                    # Không được restore account của profile tiếp theo
                    # cho tới khi profile này thực sự vào gameplay.
                    ready = wait_for_remote_port(
                        pid,
                        GAME_SERVER_IP,
                        GAMEPLAY_PORT,
                        self.context.stop_event,
                        timeout=35.0,
                    )

                    if self._halted():
                        return

                    if not ready:
                        raise RuntimeError(
                            f"{self.context.profile_name}: "
                            f"không vào được ingame socket "
                            f"{GAME_SERVER_IP}:{GAMEPLAY_PORT} "
                            f"sau khi restore account"
                        )
            while not self._halted():

                # Lần đầu đã được _ensure_in_game()
                # xử lý bên trong PROFILE_LAUNCH_LOCK.
                if initial_in_game:
                    initial_in_game = False

                else:
                    if not self._ensure_in_game():
                        break

                if self._halted():
                    break

                self._state("ENTERING_BOSS")

                self.enter(
                    self.context.process_id,
                    self.context.selected_boss,
                )
                if self._halted():
                    break

                if self._halted():
                    break
                self._state("WAITING_BOSS_LOAD")
                if self.wait(BOSS_ENTER_WAIT, self.context.stop_event):
                    break
                self.context.boss_dead_streak = 0
                self._state("CHECKING_BOSS")
                while not self._halted():
                    if not self.lifecycle.valid(self.context):
                        raise RuntimeError("Game window closed or changed owner")
                    resized = self.lifecycle.ensure_size(self.context)

                    if resized:
                        if self.wait(0.15, self.context.stop_event):
                            break

                    text = self.boss_detector.read_hp(
                        self.context
                    )

                    if self._halted():
                        break

                    debug = getattr(
                        self.boss_detector,
                        "last_debug",
                        {},
                    )

                    ocr_alive = (
                        self.boss_detector.is_alive(text)
                    )

                    visual_alive = debug.get(
                        "visual_alive",
                        False,
                    )

                    # Boss chỉ bị coi là không còn HP khi
                    # cả OCR và kiểm tra hình ảnh đều fail.
                    alive = (
                        ocr_alive
                        or visual_alive
                    )

                    if alive:
                        self.context.boss_dead_streak = 0

                    else:
                        self.context.boss_dead_streak += 1

                    debug = getattr(
                        self.boss_detector,
                        "last_debug",
                        {},
                    )

                    attempts = "; ".join(
                        f"{name}={value!r}"
                        for name, value
                        in debug.get("attempts", [])
                    )

                    self.on_log(
                        (
                            f"raw="
                            f"{debug.get('raw_size')} "
                            f"roi="
                            f"{debug.get('roi')} "
                            f"roi_shape="
                            f"{debug.get('roi_shape')} "
                            f"range="
                            f"{debug.get('roi_min')}"
                            f"..{debug.get('roi_max')} "
                            f"mean="
                            f"{debug.get('roi_mean')} "
                            f"attempts=[{attempts}] "
                            f"chosen="
                            f"{debug.get('chosen')} "
                            f"text={text!r} "
                            f"alive={alive} "
                            f"dead_streak="
                            f"{self.context.boss_dead_streak} "
                            f"debug="
                            f"{debug.get('debug_dir')}"
                        )
                    )

                    if alive:
                        self._state("BOSS_ALIVE")

                    else:
                        if (
                            self.context.boss_dead_streak
                            >= 2
                        ):
                            self._state("BOSS_DEAD")
                            break

                        self._state(
                            "BOSS_CHECK_1_2_FAILED"
                        )
                if self._halted():
                    break
                self._state("EXITING_BOSS")
                self.exit(self.context.process_id)
                if self._halted():
                    break
                self._state("WAITING_RESPAWN")
                if self.wait(BOSS_RESPAWN_WAIT, self.context.stop_event):
                    break
        except Exception as exc:
            if not self._halted():
                self._state("ERROR")
                self.on_error(exc)
        finally:
            if self.context.state != "ERROR":
                self._state("STOPPED")
