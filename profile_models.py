from dataclasses import dataclass, field
from threading import Event


@dataclass(frozen=True)
class Profile:
    profile_id: str
    profile_name: str
    game_path: str
    login_ready: bool
    selected_boss: str
    window_width: int
    window_height: int
    created_at: str
    account_username: str = ""
    server: str = "van_lang"


@dataclass
class ProfileRuntimeContext:
    profile_id: str
    profile_name: str
    game_path: str
    selected_boss: str
    window_width: int
    window_height: int
    state: str = "STOPPED"
    process_id: int | None = None
    window_handle: int | None = None
    stop_event: Event = field(default_factory=Event)
    boss_dead_streak: int = 0

    @classmethod
    def from_profile(cls, profile: Profile) -> "ProfileRuntimeContext":
        return cls(
            profile_id=profile.profile_id,
            profile_name=profile.profile_name,
            game_path=profile.game_path,
            selected_boss=profile.selected_boss,
            window_width=profile.window_width,
            window_height=profile.window_height,
        )
