from itertools import count

from automation_constants import BOSS_RESPAWN_WAIT
from game_automation import AutomationWorker
from profile_models import ProfileRuntimeContext


def test_three_workers_keep_pid_hwnd_and_boss_commands_isolated():
    contexts = [
        ProfileRuntimeContext(
            str(i),
            f"P{i}",
            f"clone{i}",
            "trom_cho",
            320,
            180,
        )
        for i in range(3)
    ]
    commands = []

    for index, context in enumerate(
        contexts
    ):
        class Lifecycle:
            def open(self, _context):
                return (
                    100 + index,
                    200 + index,
                    False,
                )

            def resize(self, _context):
                pass

            def valid(self, _context):
                return True

            def ensure_size(self, _context):
                return False

        class Detector:
            def check_signals(self, _context):
                return []

        class Boss:
            def __init__(self):
                self.values = iter(
                    ["123", "", ""]
                )

            def read_hp(self, _context):
                return next(self.values)

            def is_alive(self, text):
                return bool(text)

        def wait(seconds, stop_event):
            if seconds == BOSS_RESPAWN_WAIT:
                stop_event.set()
                return True
            return False

        worker = AutomationWorker(
            context,
            lifecycle=Lifecycle(),
            detector=Detector(),
            boss_detector=Boss(),
            enter=lambda pid, boss:
            commands.append(("enter", pid)),
            exit=lambda pid:
            commands.append(("exit", pid)),
            wait=wait,
            now=count(0, 4).__next__,
            gameplay_ready=lambda _pid: True,
            boss_scan_stagger=0,
        )
        worker.run()

    assert [
        context.process_id
        for context in contexts
    ] == [
        100,
        101,
        102,
    ]
    assert [
        context.window_handle
        for context in contexts
    ] == [
        200,
        201,
        202,
    ]
    assert len(
        {
            id(context.stop_event)
            for context in contexts
        }
    ) == 3
    assert commands == [
        ("enter", 100),
        ("exit", 100),
        ("enter", 101),
        ("exit", 101),
        ("enter", 102),
        ("exit", 102),
    ]
