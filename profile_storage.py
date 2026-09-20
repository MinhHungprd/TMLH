import json
import os
from dataclasses import asdict
from pathlib import Path

from profile_models import Profile


class DuplicateProfileError(ValueError):
    pass


class ProfileStorage:
    def __init__(self, path: str | Path = "profiles.json"):
        self.path = Path(path)

    def load(self) -> list[Profile]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid profile JSON: {self.path}") from exc
        if not isinstance(data, list):
            raise ValueError("Profile JSON must contain a list")
        return [Profile(**item) for item in data]

    def save(self, profiles: list[Profile]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps([asdict(profile) for profile in profiles], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

    def upsert(self, profile: Profile) -> None:
        profiles = self.load()
        if any(item.profile_id == profile.profile_id for item in profiles):
            raise DuplicateProfileError(profile.profile_id)
        profiles.append(profile)
        self.save(profiles)

    def remove(self, profile_id: str) -> None:
        self.save([profile for profile in self.load() if profile.profile_id != profile_id])
