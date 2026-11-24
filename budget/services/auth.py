import hashlib
import hmac
import json
import math
import secrets
import threading
from datetime import datetime, timedelta

from budget import config
from budget.db import get_conn, get_setting, set_setting
from budget.services import notify

SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1

_SALT_BYTES = 16
_KEY_BYTES = 32


def hash_password_with(password, n, r, p):
    salt = secrets.token_bytes(_SALT_BYTES)
    key = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
                         dklen=_KEY_BYTES)
    return f"scrypt${n}${r}${p}${salt.hex()}${key.hex()}"


def hash_password(password):
    return hash_password_with(password, SCRYPT_N, SCRYPT_R, SCRYPT_P)


def verify_password(password, encoded):
    try:
        scheme, n, r, p, salt_hex, key_hex = encoded.split("$")
        if scheme != "scrypt":
            return False
        n, r, p = int(n), int(r), int(p)
        if not (1 < n <= 2 ** 20 and 0 < r <= 64 and 0 < p <= 16):
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(key_hex)
        if len(expected) != _KEY_BYTES:
            return False
        candidate = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                                   n=n, r=r, p=p,
                                   dklen=len(expected))
    except (ValueError, TypeError, AttributeError):
        return False
    return hmac.compare_digest(candidate, expected)


PASSWORD_KEY = "auth_password_hash"
FAILURES_KEY = "auth_failed_attempts"
LOCKED_UNTIL_KEY = "auth_locked_until"
EPOCH_KEY = "auth_credential_epoch"

MIN_PASSWORD_LENGTH = 8
LOCKOUT_AFTER = 5
LOCKOUT_SECONDS = 60

LOCKOUT_MAX_SECONDS = 900


def lockout_seconds(failures):
    over = max(0, failures - LOCKOUT_AFTER)
    return min(LOCKOUT_SECONDS * (2 ** over), LOCKOUT_MAX_SECONDS)

_failure_lock = threading.Lock()

_hash_slots = threading.Semaphore(2)


def password_is_set():
    return bool(get_setting(PASSWORD_KEY))


def set_password(password):
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    set_setting(EPOCH_KEY, _current_epoch() + 1)
    set_setting(PASSWORD_KEY, hash_password(password))
    _reset_failures()
    revoke_all_devices()


def clear_password():
    set_setting(EPOCH_KEY, _current_epoch() + 1)
    with get_conn() as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (PASSWORD_KEY,))
    _reset_failures()
    revoke_all_devices()


def claim_password(password):
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    with get_conn() as conn:
        cursor = conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO NOTHING",
            (PASSWORD_KEY, hash_password(password)),
        )
        return cursor.rowcount == 1


def lockout_remaining(now=None):
    locked_until = get_setting(LOCKED_UNTIL_KEY)
    if not locked_until:
        return 0
    now = now or datetime.now()
    try:
        until = datetime.fromisoformat(locked_until)
    except ValueError:
        return 0
    remaining = (until - now).total_seconds()
    return math.ceil(remaining) if remaining > 0 else 0


def check_password(password, now=None, source_ip=None):
    now = now or datetime.now()
    if lockout_remaining(now=now):
        return False
    encoded = get_setting(PASSWORD_KEY)
    if not encoded:
        return False
    with _hash_slots:
        correct = verify_password(password, encoded)
    if correct:
        _reset_failures()
        return True
    with _failure_lock:
        failures = _to_int(get_setting(FAILURES_KEY, "0")) + 1
        set_setting(FAILURES_KEY, failures)
    if failures >= LOCKOUT_AFTER:
        until = now + timedelta(seconds=lockout_seconds(failures))
        set_setting(LOCKED_UNTIL_KEY, until.isoformat())
        if failures == LOCKOUT_AFTER:
            notify.send_lockout_alert(now, source_ip, failures=failures,
                                      lock_seconds=LOCKOUT_SECONDS)
    return False


def _reset_failures():
    set_setting(FAILURES_KEY, 0)
    set_setting(LOCKED_UNTIL_KEY, "")


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _current_epoch():
    return _to_int(get_setting(EPOCH_KEY, "0"))


SESSION_ID_KEY = "sid"
SESSION_EPOCH_KEY = "epoch"
REVOKED_SESSIONS_KEY = "auth_revoked_sessions"
SESSION_DAYS = 7

_revoked_lock = threading.Lock()


SESSION_MEMBER_KEY = "member"
SESSION_NAME_KEY = "member_name"


def start_session(session, member_id=None):
    session.permanent = True
    session["authed"] = True
    session.pop(SESSION_MEMBER_KEY, None)
    session.pop(SESSION_NAME_KEY, None)
    if member_id is None:
        session[SESSION_EPOCH_KEY] = _current_epoch()
    else:
        member = get_member(member_id)
        session[SESSION_MEMBER_KEY] = member["id"]
        session[SESSION_NAME_KEY] = member["name"]
        session[SESSION_EPOCH_KEY] = member["epoch"]
    session[SESSION_ID_KEY] = secrets.token_urlsafe(16)


def session_is_live(session):
    if not session.get("authed"):
        return False
    member_id = session.get(SESSION_MEMBER_KEY)
    if member_id is None:
        epoch = _current_epoch()
    else:
        member = get_member(member_id)
        if member is None:
            return False
        epoch = member["epoch"]
    if _to_int(session.get(SESSION_EPOCH_KEY, 0)) != epoch:
        return False
    sid = session.get(SESSION_ID_KEY)
    return not sid or sid not in _revoked_sessions()


def session_member_id(session):
    return session.get(SESSION_MEMBER_KEY)


def _revoked_sessions(now=None):
    now = (now or datetime.now()).isoformat()
    try:
        stored = json.loads(get_setting(REVOKED_SESSIONS_KEY) or "{}")
    except ValueError:
        return {}
    if not isinstance(stored, dict):
        return {}
    return {sid: until for sid, until in stored.items()
            if isinstance(until, str) and until > now}


def revoke_session(session, now=None):
    sid = session.get(SESSION_ID_KEY)
    if not sid:
        return
    now = now or datetime.now()
    with _revoked_lock:
        revoked = _revoked_sessions(now)
        revoked[sid] = (now + timedelta(days=SESSION_DAYS)).isoformat()
        set_setting(REVOKED_SESSIONS_KEY, json.dumps(revoked))


DEVICE_TOKEN_DAYS = 90
DEVICE_COOKIE = config.DEVICE_COOKIE

_TOKEN_BYTES = 32


def _token_hash(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _epoch_of(member_id):
    if member_id is None:
        return _current_epoch()
    member = get_member(member_id)
    return member["epoch"] if member is not None else -1


def issue_device_token(label, now=None, member_id=None):
    now = now or datetime.now()
    raw = secrets.token_urlsafe(_TOKEN_BYTES)
    expires = now + timedelta(days=DEVICE_TOKEN_DAYS)
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO device_tokens
                   (token_hash, label, created_at, last_seen_at, expires_at,
                    epoch, member_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (_token_hash(raw), label, now.isoformat(), now.isoformat(),
             expires.isoformat(), _epoch_of(member_id), member_id),
        )
    return raw


def device_member(raw, now=None):
    if not raw:
        return False, None
    now = now or datetime.now()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, epoch, expires_at, member_id FROM device_tokens "
            "WHERE token_hash = ?",
            (_token_hash(raw),),
        ).fetchone()
        if row is None or row["expires_at"] <= now.isoformat():
            return False, None
    if row["epoch"] != _epoch_of(row["member_id"]):
        return False, None
    with get_conn() as conn:
        conn.execute("UPDATE device_tokens SET last_seen_at = ? WHERE id = ?",
                     (now.isoformat(), row["id"]))
    return True, row["member_id"]


def verify_device_token(raw, now=None):
    return device_member(raw, now=now)[0]


def list_devices(now=None, member_id=None):
    now = now or datetime.now()
    with get_conn() as conn:
        return conn.execute(
            "SELECT id, label, created_at, last_seen_at, expires_at "
            "FROM device_tokens WHERE epoch = ? AND expires_at > ? "
            "AND member_id IS ? "
            "ORDER BY created_at DESC, id DESC",
            (_epoch_of(member_id), now.isoformat(), member_id),
        ).fetchall()


def revoke_device(device_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM device_tokens WHERE id = ?", (device_id,))


def revoke_token(raw):
    if not raw:
        return
    with get_conn() as conn:
        conn.execute("DELETE FROM device_tokens WHERE token_hash = ?",
                     (_token_hash(raw),))


def device_belongs_to(device_id, member_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT 1 FROM device_tokens WHERE id = ? AND member_id IS ?",
            (device_id, member_id)).fetchone() is not None


def revoke_all_devices(member_id=None):
    with get_conn() as conn:
        conn.execute("DELETE FROM device_tokens WHERE member_id IS ?",
                     (member_id,))


MAX_MEMBER_NAME = 40


def _clean_name(name):
    return " ".join((name or "").split())[:MAX_MEMBER_NAME]


def members():
    with get_conn() as conn:
        return conn.execute(
            "SELECT id, name, created_at, last_login_at, locked_until "
            "FROM household_members ORDER BY id").fetchall()


def has_members():
    with get_conn() as conn:
        return conn.execute(
            "SELECT 1 FROM household_members LIMIT 1").fetchone() is not None


def get_member(member_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM household_members WHERE id = ?", (member_id,)).fetchone()


def find_member(name):
    name = _clean_name(name)
    if not name:
        return None
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM household_members WHERE name = ?", (name,)).fetchone()


def add_member(name, password, owner_name=""):
    name = _clean_name(name)
    if not name:
        raise ValueError("Give them a name to log in with.")
    if owner_name and name.casefold() == _clean_name(owner_name).casefold():
        raise ValueError("That is the owner's name. Choose another.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if find_member(name) is not None:
        raise ValueError(f"There is already somebody called {name}.")
    with get_conn() as conn:
        return conn.execute(
            "INSERT INTO household_members (name, password_hash) VALUES (?, ?)",
            (name, hash_password(password))).lastrowid


def remove_member(member_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM household_members WHERE id = ?", (member_id,))


def set_member_password(member_id, password):
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    with get_conn() as conn:
        changed = conn.execute(
            "UPDATE household_members SET password_hash = ?, epoch = epoch + 1, "
            "failed_attempts = 0, locked_until = '' WHERE id = ?",
            (hash_password(password), member_id)).rowcount
    if changed:
        revoke_all_devices(member_id)
    return bool(changed)


def member_lockout_remaining(member, now=None):
    if not member["locked_until"]:
        return 0
    now = now or datetime.now()
    try:
        until = datetime.fromisoformat(member["locked_until"])
    except ValueError:
        return 0
    remaining = (until - now).total_seconds()
    return math.ceil(remaining) if remaining > 0 else 0


def check_member_password(member_id, password, now=None, source_ip=None):
    now = now or datetime.now()
    member = get_member(member_id)
    if member is None or member_lockout_remaining(member, now=now):
        return False
    with _hash_slots:
        correct = verify_password(password, member["password_hash"])
    if correct:
        with get_conn() as conn:
            conn.execute(
                "UPDATE household_members SET failed_attempts = 0, "
                "locked_until = '', last_login_at = ? WHERE id = ?",
                (now.isoformat(timespec="seconds"), member_id))
        return True
    with _failure_lock:
        with get_conn() as conn:
            conn.execute(
                "UPDATE household_members SET failed_attempts = failed_attempts + 1 "
                "WHERE id = ?", (member_id,))
            failures = conn.execute(
                "SELECT failed_attempts FROM household_members WHERE id = ?",
                (member_id,)).fetchone()[0]
            if failures >= LOCKOUT_AFTER:
                until = now + timedelta(seconds=lockout_seconds(failures))
                conn.execute(
                    "UPDATE household_members SET locked_until = ? WHERE id = ?",
                    (until.isoformat(), member_id))
    if failures == LOCKOUT_AFTER:
        notify.send_lockout_alert(now, source_ip, failures=failures,
                                  lock_seconds=LOCKOUT_SECONDS)
    return False


def change_own_password(member_id, current, new, now=None):
    if len(new) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if member_id is None:
        if not check_password(current, now=now):
            raise ValueError("That is not your current password.")
        set_password(new)
    else:
        if not check_member_password(member_id, current, now=now):
            raise ValueError("That is not your current password.")
        set_member_password(member_id, new)
