import re
import shutil
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from profile_models import Profile


class MissingGameFilesError(FileNotFoundError):
    pass


def validate_source_path(source_path: Path) -> Path:
    path = Path(source_path).expanduser().resolve()
    if not path.is_dir():
        raise FileNotFoundError(path)
    if not (path / "ThienMenhLacHong_Launcher.exe").is_file():
        raise FileNotFoundError(path / "ThienMenhLacHong_Launcher.exe")
    return path


def validate_profile_name(name: str, existing: list[Profile]) -> str:
    safe = re.sub(r'[<>:"/\\|?*]', "", name.strip())
    safe = re.sub(r"\s+", " ", safe).strip().rstrip(".")
    if not safe:
        raise ValueError("Profile name is empty")
    if safe.split(".", 1)[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        raise ValueError("Profile name is reserved by Windows")
    if any(profile.profile_name.casefold() == safe.casefold() for profile in existing):
        raise ValueError("Duplicate profile name")
    return safe


def profile_clone_path(bot_root: Path, profile_name: str) -> Path:
    return Path(bot_root) / "Game" / "profiles" / profile_name


class ProfileManager:
    def __init__(self, bot_root: Path):
        self.bot_root = Path(bot_root)

    def _checked_destination(self, profile: Profile) -> Path:
        destination = Path(profile.game_path)
        expected = profile_clone_path(self.bot_root, profile.profile_name)
        root = (self.bot_root / "Game" / "profiles").resolve()
        if destination.absolute() != expected.absolute() or not destination.resolve().is_relative_to(root):
            raise ValueError("Profile game path is outside its assigned clone directory")
        return destination

    def prepare_profile(self, name: str, source_path: Path, existing: list[Profile]) -> Profile:
        source = validate_source_path(source_path)
        safe_name = validate_profile_name(name, existing)
        destination = profile_clone_path(self.bot_root, safe_name)
        if destination.exists():
            raise FileExistsError(destination)
        return Profile(
            profile_id=safe_name,
            profile_name=safe_name,
            game_path=str(destination.resolve()),
            login_ready=False,
            selected_boss="trom_cho",
            window_width=320,
            window_height=180,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def clone_profile(self, profile: Profile, source_path: Path) -> None:
        source = validate_source_path(source_path)
        destination = self._checked_destination(profile)
        if source.is_relative_to(destination) or destination.is_relative_to(source):
            raise ValueError("Game source and clone must be separate directories")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            source,
            destination,
            ignore=shutil.ignore_patterns(
                ".tmlh_profile_auth.bin",
            ),
        )

    def create_profile(self, name: str, source_path: Path) -> Profile:
        profile = self.prepare_profile(name, source_path, [])
        self.clone_profile(profile, source_path)
        return profile

    def check_clone(self, profile: Profile) -> None:
        path = Path(profile.game_path)
        if not (path / "ThienMenhLacHong_Launcher.exe").is_file():
            raise MissingGameFilesError(profile.game_path)

    def repair_profile(self, profile: Profile, source_path: Path) -> None:
        source = validate_source_path(source_path)
        destination = self._checked_destination(profile)
        shutil.copytree(
            source,
            destination,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(
                ".tmlh_profile_auth.bin",
            ),
        )

    def rename_profile(
        self,
        profile: Profile,
        new_name: str,
        existing: list[Profile],
    ) -> Profile:
        """Rename a stopped profile and its clone directory safely."""
        safe_name = validate_profile_name(new_name, existing)

        if safe_name == profile.profile_name:
            return profile

        current = self._checked_destination(profile)
        target = profile_clone_path(self.bot_root, safe_name)

        if target.exists():
            raise FileExistsError(target)

        target.parent.mkdir(parents=True, exist_ok=True)

        if current.exists():
            # Windows can be awkward for case-only renames, so use a temporary hop.
            if current.name.casefold() == target.name.casefold():
                temporary = current.with_name(current.name + ".__rename_tmp__")
                if temporary.exists():
                    raise FileExistsError(temporary)
                current.rename(temporary)
                temporary.rename(target)
            else:
                current.rename(target)

        return replace(
            profile,
            profile_id=safe_name,
            profile_name=safe_name,
            game_path=str(target.resolve()),
        )

    def delete_profile(self, profile: Profile) -> None:
        """Delete only this profile clone. The original game source is untouched."""
        destination = self._checked_destination(profile)

        if destination.exists():
            shutil.rmtree(destination)

