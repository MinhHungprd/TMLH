"""Visual theme for the TMLH Bot dashboard."""

APP_NAME = "TMLH Bot"
APP_VERSION = "v1.1.0"

COLORS = {
    "bg": "#050D17",
    "sidebar": "#071321",
    "surface": "#0A1828",
    "surface_alt": "#0D2035",
    "surface_soft": "#10263E",
    "border": "#1A3A5A",
    "border_bright": "#285B88",
    "text": "#EAF3FF",
    "muted": "#89A3C2",
    "muted_dark": "#58718F",
    "blue": "#2D8CFF",
    "blue_hover": "#1675E8",
    "cyan": "#22D3EE",
    "purple": "#8B5CF6",
    "purple_hover": "#7445E8",
    "green": "#20D994",
    "green_hover": "#16B77C",
    "red": "#FF4D67",
    "red_hover": "#DD354F",
    "amber": "#F5A524",
    "input": "#071522",
    "black": "#02070D",
}

STATUS_COLORS = {
    "ready": (COLORS["blue"], "#0C2B4B"),
    "in game": (COLORS["green"], "#0A352B"),
    "boss alive": (COLORS["green"], "#0A352B"),
    "checking boss": (COLORS["purple"], "#291C4C"),
    "waiting boss": (COLORS["amber"], "#412B0A"),
    "waiting boss load": (COLORS["amber"], "#412B0A"),
    "waiting game": (COLORS["amber"], "#412B0A"),
    "waiting gameplay socket": (COLORS["amber"], "#412B0A"),
    "waiting startup": (COLORS["amber"], "#412B0A"),
    "waiting login": (COLORS["amber"], "#412B0A"),
    "launching game": (COLORS["purple"], "#291C4C"),
    "creating": (COLORS["purple"], "#291C4C"),
    "copying game": (COLORS["purple"], "#291C4C"),
    "entering boss": (COLORS["purple"], "#291C4C"),
    "exiting boss": (COLORS["purple"], "#291C4C"),
    "waiting respawn": (COLORS["amber"], "#412B0A"),
    "boss dead confirming": (COLORS["amber"], "#412B0A"),
    "boss dead": (COLORS["amber"], "#412B0A"),
    "boss signal error": (COLORS["red"], "#42141D"),
    "stopping": (COLORS["amber"], "#412B0A"),
    "stopped": (COLORS["muted"], "#142235"),
    "error": (COLORS["red"], "#42141D"),
    "missing files": (COLORS["red"], "#42141D"),
}

BOSS_LABELS = {
    "trom_cho": "Trộm chó",
    "ngao_op": "Ngáo ộp",
    "dai_tho_san": "Đại thợ săn",
}

BOSS_KEYS = {
    label: key
    for key, label in BOSS_LABELS.items()
}


def status_palette(status: str):
    normalized = (status or "").strip().casefold()

    if "error" in normalized or "missing" in normalized:
        return STATUS_COLORS["error"]

    if normalized in STATUS_COLORS:
        return STATUS_COLORS[normalized]

    if "waiting" in normalized:
        return STATUS_COLORS["waiting game"]

    if "boss" in normalized:
        return STATUS_COLORS["checking boss"]

    return COLORS["muted"], "#142235"
