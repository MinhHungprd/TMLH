from threading import Event

from automation_constants import GAME_STATE_POLL_INTERVAL
from game_automation import AutomationWorker, interruptible_wait
from game_state import SignalCheck, CLICK_CENTER
from input_manager import InputManager
from profile_models import ProfileRuntimeContext


def context():
    return ProfileRuntimeContext("p", "P", "clone", "trom_cho", 480, 270)


class FakeLifecycle:
    def open(self, ctx):
        return 202, 303, True

    def resize(self, ctx):
        pass

    def valid(self, ctx):
        return True

    def ensure_size(self, ctx):
        return False


class FakeDetector:
    def __init__(self, results):
        self.results = iter(results)

    def check_signals(self, ctx):
        return next(self.results, [])


class FakeBoss:
    def __init__(self, text):
        self.text = iter(text)

    def read_hp(self, ctx):
        return next(self.text)

    def is_alive(self, text):
        return any(c in "0123456789" for c in text)


def test_interruptible_wait_returns_immediately_when_stopped():
    event = Event()
    event.set()
    assert interruptible_wait(10, event) is True


def test_worker_runs_boss_cycle_on_actual_pid_and_revalidates_game():
    ctx = context()
    states, commands, clicks = [], [], []
    detector = FakeDetector([[SignalCheck(True, CLICK_CENTER, (12, 13))], [], [], [], []])

    def wait(seconds, event):
        if seconds == 16:
            event.set()
            return True
        return False

    worker = AutomationWorker(
        ctx, on_status=states.append, lifecycle=FakeLifecycle(), detector=detector,
        boss_detector=FakeBoss(["", "123", "", ""]),
        input_manager=InputManager(lambda hwnd, x, y: clicks.append((hwnd, x, y))),
        enter=lambda pid, kind: commands.append(("enter", pid, kind)),
        exit=lambda pid: commands.append(("exit", pid)), wait=wait,
        now=iter([0, 1, 4]).__next__,
    )
    worker.run()
    assert ctx.process_id == 202 and ctx.window_handle == 303
    assert clicks == [(303, 12, 13)]
    assert commands == [("enter", 202, "trom_cho"), ("exit", 202)]
    assert states.count("BOSS_DEAD") == 1
    assert "BOSS_CHECK_1_2_FAILED" in states
    assert ctx.state == "STOPPED"


def test_stop_during_each_wait_prevents_new_external_actions():
    for stop_at in (10, GAME_STATE_POLL_INTERVAL, 3, 2, 16):
        ctx = context()
        commands = []
        errors = []

        def wait(seconds, event):
            if seconds == stop_at:
                event.set()
                return True
            return False

        worker = AutomationWorker(
            ctx, on_error=errors.append, lifecycle=FakeLifecycle(), detector=FakeDetector([[], [], []]),
            boss_detector=FakeBoss(["123", "", ""]),
            input_manager=InputManager(lambda *args: commands.append("click")),
            enter=lambda *args: commands.append("enter"),
            exit=lambda *args: commands.append("exit"), wait=wait,
            now=iter([0, 4]).__next__,
        )
        worker.run()
        assert ctx.state == "STOPPED", (stop_at, errors)
        if stop_at in (10, GAME_STATE_POLL_INTERVAL):
            assert commands == []
        if stop_at == 3:
            assert commands == ["enter"]


def test_input_manager_serializes_click():
    calls = []
    InputManager(lambda hwnd, x, y: calls.append((hwnd, x, y))).click_center(7, (3, 4))
    assert calls == [(7, 3, 4)]


def test_input_manager_does_not_interleave_two_profiles():
    from threading import Thread
    import time
    entered, release = Event(), Event()
    calls = []

    def click(hwnd, x, y):
        calls.append(("start", hwnd))
        if hwnd == 1:
            entered.set()
            release.wait(1)
        calls.append(("end", hwnd))

    manager = InputManager(click)
    first = Thread(target=lambda: manager.click_center(1, (2, 3)))
    second = Thread(target=lambda: manager.click_center(2, (4, 5)))
    first.start()
    assert entered.wait(1)
    second.start()
    time.sleep(0.02)
    assert calls == [("start", 1)]
    release.set()
    first.join(1)
    second.join(1)
    assert calls == [("start", 1), ("end", 1), ("start", 2), ("end", 2)]


def test_input_manager_skips_click_after_stop():
    stopped = Event()
    stopped.set()
    calls = []
    manager = InputManager(lambda *args: calls.append(args))
    assert manager.click_center(7, (2, 3), stopped) is False
    assert calls == []



def test_game_lifecycle_rebinds_zero_size_unity_window():
    from unittest.mock import patch
    from game_automation import GameLifecycle

    ctx = context()
    ctx.process_id = 42
    ctx.window_handle = 100

    with patch(
        "game_automation.get_client_size",
        side_effect=[
            (0, 0),
            (480, 270),
        ],
    ), patch(
        "game_automation.find_window_for_pid",
        return_value=200,
    ), patch(
        "game_automation.set_profile_window_title",
    ) as set_title, patch(
        "game_automation.ensure_client_size",
        return_value=False,
    ):
        resized = GameLifecycle().ensure_size(
            ctx
        )

    assert resized is False
    assert ctx.window_handle == 200
    set_title.assert_called_once_with(
        200,
        "P",
    )


def test_game_lifecycle_treats_zero_size_without_replacement_as_transient():
    from unittest.mock import patch
    from game_automation import GameLifecycle

    ctx = context()
    ctx.process_id = 42
    ctx.window_handle = 100

    with patch(
        "game_automation.get_client_size",
        return_value=(0, 0),
    ), patch(
        "game_automation.find_window_for_pid",
        return_value=None,
    ):
        assert (
            GameLifecycle().ensure_size(ctx)
            is None
        )
