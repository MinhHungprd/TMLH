import json

import pytest

from app_settings import AppSettings, AppSettingsStorage
from automation_constants import RESOLUTIONS, parse_resolution
from profile_models import Profile, ProfileRuntimeContext
from profile_storage import DuplicateProfileError, ProfileStorage


def make_profile(profile_id="p1"):
    return Profile(
        profile_id=profile_id,
        profile_name="Profile 1",
        game_path="Game/profiles/Profile 1/ThienMenhLacHong_Launcher",
        login_ready=False,
        selected_boss="trom_cho",
        window_width=480,
        window_height=270,
        created_at="2026-09-20T00:00:00Z",
        account_username="account-1",
        server="au_lac",
    )


def test_profile_json_round_trip_excludes_runtime_fields(tmp_path):
    storage = ProfileStorage(tmp_path / "profiles.json")
    storage.save([make_profile()])

    loaded = storage.load()

    assert loaded == [make_profile()]
    raw = json.loads((tmp_path / "profiles.json").read_text(encoding="utf-8"))
    assert "process_id" not in raw[0]
    assert "window_handle" not in raw[0]
    assert "stop_event" not in raw[0]


def test_duplicate_profile_id_is_rejected(tmp_path):
    storage = ProfileStorage(tmp_path / "profiles.json")
    storage.save([make_profile()])

    with pytest.raises(DuplicateProfileError):
        storage.upsert(make_profile())


def test_corrupt_json_is_reported(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(ValueError):
        ProfileStorage(path).load()


def test_runtime_context_is_not_persisted():
    context = ProfileRuntimeContext.from_profile(make_profile())
    assert context.process_id is None
    assert context.window_handle is None
    assert context.stop_event.is_set() is False


def test_resolution_validation():
    assert parse_resolution("320x180") == (320, 180)
    assert set(RESOLUTIONS) == {(320, 180), (480, 270), (640, 360), (800, 450), (860, 484)}
    with pytest.raises(ValueError):
        parse_resolution("123x456")


def test_app_settings_round_trip(tmp_path):
    storage = AppSettingsStorage(tmp_path / "settings.json")
    storage.save(
        AppSettings(
            game_source_path="D:/Game",
            window_layout_mode="stack",
        )
    )
    assert storage.load() == AppSettings(
        game_source_path="D:/Game",
        window_layout_mode="stack",
    )
