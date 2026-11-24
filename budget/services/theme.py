from budget.db import get_setting, set_setting

PALETTES = ("tide", "ember", "cobalt", "iris", "plum", "slate")
MODES = ("light", "dark")

DEFAULT_PALETTE = "tide"
DEFAULT_MODE = "dark"

_PALETTE_KEY = "theme_palette"
_MODE_KEY = "theme_mode"


def normalize_palette(value):
    return value if value in PALETTES else DEFAULT_PALETTE


def normalize_mode(value):
    return value if value in MODES else DEFAULT_MODE


def current_theme():
    return {
        "mode": normalize_mode(get_setting(_MODE_KEY)),
        "palette": normalize_palette(get_setting(_PALETTE_KEY)),
    }


def save_theme(mode, palette):
    set_setting(_MODE_KEY, normalize_mode(mode))
    set_setting(_PALETTE_KEY, normalize_palette(palette))
