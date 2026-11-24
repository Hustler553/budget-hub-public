import os
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DB_PATH = Path(os.environ.get("BUDGET_DB_PATH") or BASE_DIR / "budget.db")

BACKUP_DIR = Path(os.environ.get("BUDGET_BACKUP_DIR") or DB_PATH.parent / "backups")
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

BACKUP_KEEP = 14

PORT = int(os.environ.get("BUDGET_PORT") or "5000")

COOKIE_PREFIX = re.sub(r"[^A-Za-z0-9_-]", "",
                       os.environ.get("BUDGET_COOKIE_PREFIX", "")) or "tally"
SESSION_COOKIE = f"{COOKIE_PREFIX}_session"
DEVICE_COOKIE = f"{COOKIE_PREFIX}_device"

HOST = os.environ.get("BUDGET_HOST", "").strip() or "127.0.0.1"

_LOOPBACK = ("127.0.0.1", "localhost", "::1")


def warn_if_exposed(out=None):
    import sys

    if HOST in _LOOPBACK:
        return None
    message = (
        f"  NOTE: BUDGET_HOST={HOST!r} accepts connections from other machines,\n"
        f"  so anything on this network can reach the login page, and a session\n"
        f"  cookie sent back over it crosses the network in clear text.\n"
        f"  Serving through Tailscale? Then 127.0.0.1 is enough -- Serve\n"
        f"  connects to loopback."
    )
    print(message, file=out or sys.stderr)
    return message

PLAID_KEY = os.environ.get("BUDGET_PLAID_KEY", "")

VAULT_KEY = os.environ.get("BUDGET_VAULT_KEY", "")

HTTPS_ONLY = os.environ.get("BUDGET_HTTPS_ONLY", "") == "1"

PLAID_HOSTS = {
    "sandbox": "https://sandbox.plaid.com",
    "production": "https://production.plaid.com",
}
PLAID_ENV = os.environ.get("BUDGET_PLAID_ENV", "sandbox")
if PLAID_ENV not in PLAID_HOSTS:
    PLAID_ENV = "sandbox"
