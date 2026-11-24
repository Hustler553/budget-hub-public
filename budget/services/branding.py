from budget.db import get_setting, set_setting

APP_NAME = "Tally"

_OWNER_KEY = "owner_name"

MAX_OWNER_NAME = 24


def owner_name():
    return get_setting(_OWNER_KEY, "").strip()


def set_owner_name(value):
    set_setting(_OWNER_KEY, (value or "").strip()[:MAX_OWNER_NAME])


def wordmark():
    name = owner_name()
    return f"{name}'s Finances" if name else APP_NAME
