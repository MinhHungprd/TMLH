from pathlib import Path
import queue

from launcher_gui import LauncherApp, ProfileController
from profile_manager import ProfileManager
from profile_storage import ProfileStorage
from app_settings import AppSettingsStorage


def source(tmp_path):
    path = tmp_path / "source"
    path.mkdir()
    (path / "ThienMenhLacHong_Launcher.exe").write_bytes(b"exe")
    return path


class FakeWorker:
    def __init__(self, context, on_status=None, on_error=None):
        self.context = context
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


def controller(tmp_path):
    return ProfileController(
        tmp_path / "bot", ProfileStorage(tmp_path / "profiles.json"),
        AppSettingsStorage(tmp_path / "settings.json"), worker_factory=FakeWorker,
    )


def test_creation_is_not_ready_until_explicit_login_confirmation(tmp_path):
    model = controller(tmp_path)
    game = source(tmp_path)
    profile = model.prepare_profile("P", game)
    assert profile.login_ready is False
    assert model.profile_store.load()[0].login_ready is False
    model.manager.clone_profile(profile, game)
    assert model.profile_store.load()[0].login_ready is False
    model.confirm_login(profile.profile_id)
    assert model.profile_store.load()[0].login_ready is True


def test_start_and_stop_selected_isolate_three_profiles(tmp_path):
    model = controller(tmp_path)
    game = source(tmp_path)
    profiles = []
    for name in ("A", "B", "C"):
        item = model.prepare_profile(name, game)
        model.manager.clone_profile(item, game)
        model.confirm_login(item.profile_id)
        profiles.append(item)
    model.start_selected(["A", "B", "C"], {"A": ("trom_cho", "320x180"), "B": ("ngao_op", "480x270"), "C": ("dai_tho_san", "640x360")})
    assert len(model.workers) == 3
    assert all(worker.started for worker in model.workers.values())
    assert len({id(worker.context.stop_event) for worker in model.workers.values()}) == 3
    model.stop_selected(["B"])
    assert model.workers["B"].stopped is True
    assert model.workers["A"].stopped is False
    assert model.workers["C"].stopped is False


def test_repair_restores_clone_only_after_explicit_action(tmp_path):
    model = controller(tmp_path)
    game = source(tmp_path)
    profile = model.prepare_profile("P", game)
    model.manager.clone_profile(profile, game)
    model.confirm_login("P")
    Path(profile.game_path, "ThienMenhLacHong_Launcher.exe").unlink()
    assert Path(profile.game_path, "ThienMenhLacHong_Launcher.exe").exists() is False
    model.repair_profile("P", game)
    assert Path(profile.game_path, "ThienMenhLacHong_Launcher.exe").is_file()
    assert model.profile_store.load()[0].login_ready is False


def test_failed_worker_can_restart_without_stop_event(tmp_path):
    model = controller(tmp_path)
    game = source(tmp_path)
    profile = model.prepare_profile("P", game)
    model.manager.clone_profile(profile, game)
    model.confirm_login("P")
    model.start_selected(["P"])
    first = model.workers["P"]
    first.context.state = "ERROR"
    assert not first.context.stop_event.is_set()
    assert model.start_selected(["P"]) == ["P"]
    assert model.workers["P"] is not first



def test_edit_and_delete_profile_crud(tmp_path):
    model = controller(tmp_path)
    game = source(tmp_path)

    profile = model.prepare_profile("Old", game)
    model.manager.clone_profile(profile, game)

    updated = model.edit_profile(
        "Old",
        "Renamed",
        "ngao_op",
        "480x270",
    )

    assert updated.profile_id == "Renamed"
    assert updated.profile_name == "Renamed"
    assert updated.selected_boss == "ngao_op"
    assert (updated.window_width, updated.window_height) == (480, 270)
    assert Path(updated.game_path).is_dir()
    assert model.profile_store.load()[0].profile_id == "Renamed"

    deleted = model.delete_profile("Renamed")
    assert deleted.profile_name == "Renamed"
    assert model.profiles == []
    assert model.profile_store.load() == []
    assert Path(updated.game_path).exists() is False


def test_worker_callbacks_only_enqueue_and_do_not_touch_tk():
    class Dummy:
        def __init__(self):
            self._worker_event_queue = (
                queue.SimpleQueue()
            )
            self._log_queue = (
                queue.SimpleQueue()
            )

    app = Dummy()
    callbacks = (
        LauncherApp._worker_callbacks(
            app
        )
    )

    callbacks["on_status"](
        "p1",
        "BOSS_ALIVE",
    )
    callbacks["on_error"](
        "p2",
        RuntimeError("boom"),
    )
    callbacks["on_log"](
        "p3",
        "hello",
    )

    assert (
        app._worker_event_queue
        .get_nowait()
    ) == (
        "status",
        "p1",
        "BOSS_ALIVE",
    )

    kind, profile_id, exc = (
        app._worker_event_queue
        .get_nowait()
    )
    assert kind == "error"
    assert profile_id == "p2"
    assert isinstance(
        exc,
        RuntimeError,
    )

    assert (
        app._log_queue
        .get_nowait()
    ) == (
        "p3",
        "hello",
    )


def test_queue_ui_call_is_ignored_after_close():
    called = []

    class Dummy:
        _closing = True
        _worker_event_queue = (
            queue.SimpleQueue()
        )

    LauncherApp._queue_ui_call(
        Dummy(),
        called.append,
        "x",
    )

    assert called == []


def test_worker_event_flush_survives_one_bad_ui_callback():
    called = []

    class Dummy:
        def __init__(self):
            self._closing = False
            self._worker_event_queue = (
                queue.SimpleQueue()
            )
            self._log_queue = (
                queue.SimpleQueue()
            )
            self._batch_worker_ui = False
            self._batch_refresh_needed = False
            self._batch_layout_needed = False
            self._worker_event_flush_after_id = None

        def _worker_status(
            self,
            profile_id,
            state,
        ):
            called.append(
                (
                    "status",
                    profile_id,
                    state,
                )
            )

        def _worker_error(
            self,
            profile_id,
            exc,
        ):
            called.append(
                (
                    "error",
                    profile_id,
                    str(exc),
                )
            )

        def _refresh(self):
            called.append("refresh")

        def _update_header_status(self):
            called.append("header")

        def _apply_window_layout(self):
            called.append("layout")

        def winfo_exists(self):
            return False

    app = Dummy()

    def broken():
        raise RuntimeError(
            "bad callback"
        )

    app._worker_event_queue.put(
        (
            "call",
            broken,
            (),
        )
    )
    app._worker_event_queue.put(
        (
            "call",
            called.append,
            ("after-bad",),
        )
    )

    LauncherApp._flush_worker_events(
        app
    )

    assert "after-bad" in called

    profile, message = (
        app._log_queue.get_nowait()
    )
    assert profile == "App"
    assert "UI event skipped" in message
    assert "bad callback" in message
