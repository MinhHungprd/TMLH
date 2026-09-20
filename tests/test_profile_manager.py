from pathlib import Path

import pytest

from profile_manager import (
    MissingGameFilesError,
    ProfileManager,
    profile_clone_path,
    validate_profile_name,
    validate_source_path,
)


def test_validate_source_path_requires_launcher(tmp_path):
    with pytest.raises(FileNotFoundError):
        validate_source_path(tmp_path)
    launcher = tmp_path / "ThienMenhLacHong_Launcher.exe"
    launcher.write_bytes(b"exe")
    assert validate_source_path(tmp_path) == tmp_path.resolve()


def test_profile_name_validation_and_clone_path(tmp_path):
    assert validate_profile_name("Acc: 01", []) == "Acc 01"
    assert profile_clone_path(tmp_path, "Acc 01") == tmp_path / "Game" / "profiles" / "Acc 01"
    with pytest.raises(ValueError):
        validate_profile_name("", [])
    with pytest.raises(ValueError):
        validate_profile_name("CON", [])


def test_create_copies_complete_source_and_missing_clone_is_error(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "ThienMenhLacHong_Launcher.exe").write_bytes(b"exe")
    (source / "cache.bin").write_bytes(b"cache")
    manager = ProfileManager(tmp_path / "bot")
    profile = manager.create_profile("Acc 01", source)
    assert Path(profile.game_path).joinpath("cache.bin").read_bytes() == b"cache"
    clone = Path(profile.game_path)
    clone.joinpath("ThienMenhLacHong_Launcher.exe").unlink()
    with pytest.raises(MissingGameFilesError):
        manager.check_clone(profile)


def test_prepare_profile_records_incomplete_before_copy(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "ThienMenhLacHong_Launcher.exe").write_bytes(b"exe")
    manager = ProfileManager(tmp_path / "bot")
    profile = manager.prepare_profile("Acc 01", source, [])
    assert profile.login_ready is False
    assert not Path(profile.game_path).exists()
    manager.clone_profile(profile, source)
    assert Path(profile.game_path, "ThienMenhLacHong_Launcher.exe").is_file()


def test_explicit_repair_restores_missing_launcher_preserving_extra_data(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "ThienMenhLacHong_Launcher.exe").write_bytes(b"exe")
    manager = ProfileManager(tmp_path / "bot")
    profile = manager.create_profile("Acc 01", source)
    clone = Path(profile.game_path)
    (clone / "player-save.bin").write_bytes(b"session")
    (clone / "ThienMenhLacHong_Launcher.exe").unlink()
    manager.repair_profile(profile, source)
    manager.check_clone(profile)
    assert (clone / "player-save.bin").read_bytes() == b"session"
