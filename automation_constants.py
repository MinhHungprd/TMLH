BASE_WIDTH = 860
BASE_HEIGHT = 484

RESOLUTIONS = (
    (320, 180),
    (480, 270),
    (640, 360),
    (800, 450),
    (860, 484),
)

SIGNAL_1 = (795, 29, 29, 38)
SIGNAL_2 = (742, 437, 45, 24)
SIGNAL_3 = (392, 389, 75, 35)
BOSS_HP = (375, 8, 66, 22)

GAME_START_WAIT = 10.0
IN_GAME_CONFIRM_SECONDS = 3.0

BOSS_ENTER_WAIT = 3.0

# Quét HP mỗi 1 giây.
BOSS_OCR_INTERVAL = 1.0

# Không thấy boss liên tục 3 giây
# mới xác nhận boss chết.
BOSS_DEAD_CONFIRM_SECONDS = 3.0

BOSS_RESPAWN_WAIT = 20.0


def parse_resolution(value: str) -> tuple[int, int]:
    try:
        width, height = (int(part) for part in value.lower().split("x", 1))
    except (ValueError, AttributeError):
        raise ValueError(f"Invalid resolution: {value!r}") from None
    if (width, height) not in RESOLUTIONS:
        raise ValueError(f"Unsupported resolution: {value!r}")
    return width, height
