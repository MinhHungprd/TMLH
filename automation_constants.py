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

# Boss-alive detector.
#
# Scan one canonical search box and match against every image in the selected
# boss asset directory. Assets are authored against the same 860x484 base
# coordinate system and are scaled to the current game client before matching.
BOSS_ASSET_SCAN_ROI = (288, 4, 57, 51)
BOSS_ALIVE_MATCH_THRESHOLD = 0.7

# Runtime boss key -> asset subdirectory. Keep the existing public boss key
# "dai_tho_san" even though the uploaded folder is named "dai_son_tac".
BOSS_ASSET_DIRS = {
    "trom_cho": "trom_cho",
    "ngao_op": "ngao_op",
    "dai_tho_san": "dai_son_tac",
}
BOSS_ASSET_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
)

# Legacy marker constants are kept for compatibility with older integrations
# and tests, but the runtime detector no longer uses the fixed marker asset.
BOSS_ALIVE_MARKER = (443, 27, 12, 18)
BOSS_ALIVE_ASSET = "asset__x443_y27_w12_h18.png"

# OCR implementation is retained, but disabled at runtime for the current
# asset-only validation mode.
BOSS_OCR_ENABLED = False

# If OCR is re-enabled later, only invoke it after this many consecutive
# 1-second asset misses.
BOSS_ASSET_MISSES_BEFORE_OCR = 3

# A boss that is continuously reported alive for too long is treated as a
# stuck/invalid signal and forcibly exited.
BOSS_ALIVE_SIGNAL_TIMEOUT_SECONDS = 300.0

# Boss-name OCR box in canonical 860x484 coordinates.
BOSS_NAME_ROI = (361, 27, 91, 18)
BOSS_NAME_LABELS = {
    "trom_cho": "Trộm chó",
    "ngao_op": "Ngáo ộp",
    "dai_tho_san": "Đại thợ săn",
}
# Fuzzy matching tolerates missing/misread OCR characters while still
# requiring the selected boss to be the best matching known name.
BOSS_NAME_MATCH_RATIO = 0.62
BOSS_NAME_MATCH_COVERAGE = 0.55
BOSS_NAME_MIN_CHARS = 4

GAME_START_WAIT = 10.0
IN_GAME_CONFIRM_SECONDS = 3.0

# Fresh launches must reach the s2 startup marker
# (asset__x742_y437_w45_h24.png) before the in-game timeout starts.
# This keeps the shared-account launch lock held during long Unity loading.
STARTUP_INGAME_GATE_SIGNAL = "s2"
STARTUP_INGAME_TIMEOUT_SECONDS = 90.0

# UI/startup polling is intentionally relaxed. Automation decisions are
# unchanged; a slightly slower poll avoids unnecessary capture churn.
GAME_STATE_POLL_INTERVAL = 0.5

# Stable boss-alive diagnostic logs do not need to update every few seconds.
ALIVE_DEBUG_LOG_INTERVAL = 10.0

BOSS_ENTER_WAIT = 3.0

# Quét boss mỗi 1 giây.
BOSS_OCR_INTERVAL = 1.0

# Phân tán thời điểm scan giữa nhiều profile để tránh 10 cửa sổ
# cùng gọi screen capture đúng một thời điểm.
# 10 worker đầu tiên nhận phase 0.0, 0.1, ... 0.9 giây.
BOSS_SCAN_STAGGER_SLOTS = 10
BOSS_SCAN_STAGGER_STEP = 0.1

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
