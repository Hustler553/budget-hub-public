import logging
import re
import sqlite3
from contextlib import contextmanager
from datetime import date

from budget import config, migrations
from budget.services import vault

log = logging.getLogger(__name__)

DEFAULT_CATEGORIES = [
    ("Housing / Rent", "expense"),
    ("Groceries", "expense"),
    ("Dining Out", "expense"),
    ("Transportation / Gas", "expense"),
    ("Utilities", "expense"),
    ("Subscriptions", "expense"),
    ("Health / Fitness", "expense"),
    ("Entertainment", "expense"),
    ("Shopping", "expense"),
    ("Travel", "expense"),
    ("Gifts", "expense"),
    ("Miscellaneous", "expense"),
    ("Paycheck", "income"),
    ("Other Income", "income"),
]


@contextmanager
def get_conn():
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.execute("PRAGMA journal_mode = WAL")

    try:
        seal_stored_secrets()
    except sqlite3.Error:
        pass

    migrations.run_pending()

    seal_stored_secrets()

    from budget.services.sources import plaid as _plaid
    _plaid.reseal_tokens()

    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
        if count == 0:
            conn.executemany(
                "INSERT INTO categories (name, kind) VALUES (?, ?)",
                DEFAULT_CATEGORIES,
            )


def get_setting(key, default=""):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


SEALED_SETTINGS = ("plaid_secret", "email_app_password")


def seal_stored_secrets():
    if not vault.configured():
        return 0
    sealed = 0
    for key in SEALED_SETTINGS:
        stored = get_setting(key, "")
        if not stored or vault.is_current(stored):
            continue
        try:
            plain = vault.unseal_text(stored, key)
        except vault.NoKey as exc:
            log.warning("A stored secret could not be re-sealed: %s", exc)
            continue
        set_setting(key, vault.seal_text(plain, key))
        sealed += 1
    if sealed:
        log.info("Re-sealed %d stored secret(s) to the current scheme", sealed)
    return sealed


def get_secret(key, default=""):
    stored = get_setting(key, "")
    if not stored:
        return default
    try:
        return vault.unseal_text(stored, key)
    except vault.NoKey as exc:
        log.warning("A stored secret could not be read: %s", exc)
        return default


def set_secret(key, value):
    set_setting(key, vault.seal_text(str(value), key))


def set_setting(key, value):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )


def get_all_settings():
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {row["key"]: row["value"] for row in rows}


_DATED_SNAPSHOT = re.compile(r"^budget-\d{4}-\d{2}-\d{2}\.db$")


_UNSAFE_IN_SNAPSHOT = ("secret_key",)


def _unsealed_credentials(conn):
    marks = ", ".join("?" * len(SEALED_SETTINGS))
    rows = conn.execute(
        f"SELECT key, value FROM settings WHERE key IN ({marks})",
        SEALED_SETTINGS).fetchall()
    return tuple(key for key, value in rows
                 if value and not vault.is_sealed(value))


def _strip_secrets(snapshot):
    conn = sqlite3.connect(snapshot, isolation_level=None)
    try:
        present = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'"
        ).fetchone()
        if not present:
            return
        leaking = _unsealed_credentials(conn)
        if leaking:
            log.warning(
                "Stored in the clear, so left out of the backup: %s. Set "
                "BUDGET_VAULT_KEY and restart to seal them; until then a "
                "restore from this snapshot will ask for them again.",
                ", ".join(leaking))
        doomed = _UNSAFE_IN_SNAPSHOT + leaking
        marks = ", ".join("?" * len(doomed))
        conn.execute(f"DELETE FROM settings WHERE key IN ({marks})", doomed)
        conn.execute("VACUUM")
    finally:
        conn.close()


def backup_db(keep=None, label=None):
    keep = config.BACKUP_KEEP if keep is None else keep
    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stem = date.today().isoformat() if label is None else label
    dest = config.BACKUP_DIR / f"budget-{stem}.db"
    if dest.exists():
        return None
    src = sqlite3.connect(config.DB_PATH)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    _strip_secrets(dest)
    if label is None:
        dailies = sorted(
            p for p in config.BACKUP_DIR.iterdir() if _DATED_SNAPSHOT.match(p.name)
        )
        for old in dailies[:-keep]:
            for part in (old, old.with_name(old.name + "-wal"),
                         old.with_name(old.name + "-shm")):
                part.unlink(missing_ok=True)
    return dest
