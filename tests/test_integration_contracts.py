from game_automation import AutomationWorker
from profile_models import ProfileRuntimeContext


def test_three_workers_keep_pid_hwnd_and_boss_commands_isolated():
    contexts = [ProfileRuntimeContext(str(i), f"P{i}", f"clone{i}", "trom_cho", 320, 180)
                for i in range(3)]
    commands = []

    for index, context in enumerate(contexts):
        class Lifecycle:
            def open(self, _context):
                return 100 + index, 200 + index, False

            def resize(self, _context):
                pass

            def valid(self, _context):
                return True

        class Detector:
            def check_signals(self, _context):
                return []

        class Boss:
            def read_hp(self, _context):
                return ""

            def is_alive(self, text):
                return False

        def wait(seconds, stop_event):
            if seconds == 16:
                stop_event.set()
                return True
            return False

        worker = AutomationWorker(
            context, lifecycle=Lifecycle(), detector=Detector(), boss_detector=Boss(),
            enter=lambda pid, boss: commands.append(("enter", pid)),
            exit=lambda pid: commands.append(("exit", pid)),
            wait=wait, now=iter([0, 4]).__next__,
        )
        worker.run()

    assert [context.process_id for context in contexts] == [100, 101, 102]
    assert [context.window_handle for context in contexts] == [200, 201, 202]
    assert len({id(context.stop_event) for context in contexts}) == 3
    assert commands == [("enter", 100), ("exit", 100), ("enter", 101), ("exit", 101),
                        ("enter", 102), ("exit", 102)]
