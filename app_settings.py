import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppSettings:
    game_source_path: str = ""


class AppSettingsStorage:
    def __init__(self, path: str | Path = "settings.json"):
        self.path = Path(path)

    def load(self) -> AppSettings:
        if not self.path.exists():
            return AppSettings()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid settings JSON: {self.path}") from exc
        return AppSettings(game_source_path=str(data.get("game_source_path", "")))

    def save(self, settings: AppSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
        os.replace(temporary, self.path)
