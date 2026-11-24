from functools import wraps
from typing import NamedTuple

from flask import abort

from budget import db
from budget.db import get_setting, set_setting


class Feature(NamedTuple):
    name: str
    label: str
    description: str
    default: bool


REGISTRY: dict[str, Feature] = {}


def register(feature):
    if feature.name in REGISTRY:
        raise ValueError(f"feature {feature.name!r} is already registered")
    REGISTRY[feature.name] = feature


def _key(name):
    return f"feature.{name}"


def enabled(name):
    feature = REGISTRY[name]
    stored = get_setting(_key(name), "")
    if stored == "":
        return feature.default
    return stored == "1"


def set_enabled(name, value):
    if name not in REGISTRY:
        raise KeyError(name)
    set_setting(_key(name), "1" if value else "0")


def all_states():
    stored = db.get_all_settings()
    return {
        name: (feature.default if stored.get(_key(name), "") == ""
               else stored.get(_key(name)) == "1")
        for name, feature in REGISTRY.items()
    }


def require(name):
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not enabled(name):
                abort(404)
            return view(*args, **kwargs)
        return wrapper
    return decorator


register(Feature(
    name="importer",
    label="Import from an old Budget Tool database",
    description="Bring categories, rules, recurring items and transactions "
                "across from a previous version of this app. It never writes "
                "to the old database. Turn it off once you have finished "
                "importing: it reads a file path you type, so there is no "
                "reason to leave that door open afterwards.",
    default=False,
))

register(Feature(
    name="email_import",
    label="Import transactions from bank alert emails",
    description="Reads a Gmail inbox every two hours and turns bank alert "
                "emails into transactions. The older way in, for an account "
                "no bank connection covers: connecting the bank, or uploading "
                "its CSV, is more reliable, because an alert email is prose "
                "this app has to guess at. Already on if you were using it.",
    default=False,
))

register(Feature(
    name="security_email",
    label="Email me about security events",
    description="Sends an email when repeated wrong passwords lock this "
                "instance. Needs the Gmail connection set up above.",
    default=False,
))

register(Feature(
    name="plaid",
    label="Connect banks through Plaid",
    description="Pull transactions and balances straight from your bank "
                "-- the most reliable way in, and the one to use where you "
                "can. Needs a Plaid client id and secret below, and "
                "BUDGET_VAULT_KEY set in the environment.",
    default=False,
))

register(Feature(
    name="money_math",
    label="Loan and savings calculators",
    description="Work out what a loan really costs, what paying extra saves, "
                "and what savings grow to. Pure arithmetic -- it reads none of "
                "your accounts and changes nothing.",
    default=False,
))

register(Feature(
    name="offline",
    label="Install on a phone and start instantly",
    description="On to begin with, unlike the rest of these. Keeps the app's "
                "stylesheet, font and icons on the device, so an installed "
                "copy paints at once instead of flashing white, and shows a "
                "page saying so when it cannot reach this computer. It does "
                "not work offline: your accounts are here, not on the phone. "
                "No balance is ever stored on a device -- a figure from an "
                "earlier hour looks exactly like a current one, and you would "
                "act on it. Turning this off clears what was stored, next "
                "time the app is opened there.",
    default=True,
))

register(Feature(
    name="money_alerts",
    label="Email me a daily digest",
    description="One message a day, and only on a day that has something in "
                "it: bills due soon, a subscription whose price went up, a "
                "category over budget, or email ingestion having stopped. "
                "Needs the Gmail connection set up above.",
    default=False,
))
