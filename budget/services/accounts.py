import sqlite3
from datetime import date

from budget.db import get_conn, get_setting

KINDS = ("checking", "savings", "cash", "credit", "loan",
         "investment", "property", "vehicle", "other")

ASSET_KINDS = frozenset({"checking", "savings", "cash", "investment",
                         "property", "vehicle", "other"})

_EDITABLE = ("name", "kind", "mask", "institution", "is_asset",
             "balance_cents", "balance_as_of", "sort_order")

_SOURCE_SETTING = {
    "csv": "csv_default_account_id",
    "email": "email_default_account_id",
}


def _check_name(name):
    name = (name or "").strip()
    if not name:
        raise ValueError("An account needs a name.")
    return name


def _check_kind(kind):
    if kind not in KINDS:
        raise ValueError(f"{kind!r} is not an account kind. One of: "
                         + ", ".join(KINDS))
    return kind


def create(name, kind="other", mask="", institution="", is_asset=None,
           balance_cents=None, balance_as_of=None):
    name = _check_name(name)
    kind = _check_kind(kind)
    if balance_cents is None:
        balance_cents = 0
    elif balance_as_of is None:
        balance_as_of = date.today().isoformat()
    if is_asset is None:
        is_asset = 1 if kind in ASSET_KINDS else 0
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO accounts "
                "(name, kind, mask, institution, is_asset, balance_cents, "
                " balance_as_of) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name, kind, (mask or "").strip(), (institution or "").strip(),
                 1 if is_asset else 0, balance_cents, balance_as_of),
            )
            return cur.lastrowid
    except sqlite3.IntegrityError as exc:
        if exc.sqlite_errorname != "SQLITE_CONSTRAINT_UNIQUE":
            raise
        raise ValueError(f"There is already an account called {name!r}.") from exc


def update(account_id, **fields):
    unknown = set(fields) - set(_EDITABLE)
    if unknown:
        raise ValueError(f"Not editable: {', '.join(sorted(unknown))}")
    if not fields:
        if get(account_id) is None:
            raise LookupError(f"No account with id {account_id}.")
        return
    if "name" in fields:
        fields["name"] = _check_name(fields["name"])
    if "kind" in fields:
        fields["kind"] = _check_kind(fields["kind"])
    if "is_asset" in fields:
        fields["is_asset"] = 1 if fields["is_asset"] else 0
    for text_field in ("mask", "institution"):
        if text_field in fields:
            fields[text_field] = (fields[text_field] or "").strip()

    assignments = ", ".join(f"{column} = ?" for column in fields)
    try:
        with get_conn() as conn:
            cur = conn.execute(
                f"UPDATE accounts SET {assignments} WHERE id = ?",
                (*fields.values(), account_id),
            )
            if cur.rowcount == 0:
                raise LookupError(f"No account with id {account_id}.")
    except sqlite3.IntegrityError as exc:
        if exc.sqlite_errorname != "SQLITE_CONSTRAINT_UNIQUE":
            raise
        raise ValueError("Another account already has that name.") from exc


def _as_of_text(as_of):
    if isinstance(as_of, str):
        as_of = as_of.strip()
        if not as_of:
            return date.today().isoformat()
        try:
            return date.fromisoformat(as_of).isoformat()
        except ValueError:
            raise ValueError(f"{as_of!r} is not a date (expected YYYY-MM-DD).") from None
    return (as_of or date.today()).isoformat()


def set_balance(account_id, balance_cents, as_of=None):
    update(account_id, balance_cents=balance_cents, balance_as_of=_as_of_text(as_of))


def archive(account_id, archived=True):
    with get_conn() as conn:
        cur = conn.execute("UPDATE accounts SET archived = ? WHERE id = ?",
                           (1 if archived else 0, account_id))
        if cur.rowcount == 0:
            raise LookupError(f"No account with id {account_id}.")


def get(account_id):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM accounts WHERE id = ?",
                            (account_id,)).fetchone()


def active():
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM accounts WHERE archived = 0 "
            "ORDER BY sort_order, name").fetchall()


def all_accounts():
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM accounts ORDER BY archived, sort_order, name"
        ).fetchall()


def net_worth_cents():
    with get_conn() as conn:
        return conn.execute(
            "SELECT COALESCE(SUM(balance_cents), 0) FROM accounts "
            "WHERE archived = 0").fetchone()[0]


def grouped():
    rows = active()
    return ([r for r in rows if r["is_asset"]],
            [r for r in rows if not r["is_asset"]])


def holdings_by_account():
    grouped = {}
    with get_conn() as conn:
        for row in conn.execute(
                """SELECT h.account_id, h.quantity_e8, h.value_cents,
                          h.cost_basis_cents, h.price_as_of,
                          s.ticker, s.name, s.security_type
                     FROM holdings h
                     JOIN securities s ON s.security_id = h.security_id
                    ORDER BY h.account_id, h.value_cents DESC, s.name"""):
            grouped.setdefault(row["account_id"], []).append(dict(row))
    return grouped


def liability_terms():
    with get_conn() as conn:
        return {row["account_id"]: dict(row) for row in conn.execute(
            "SELECT * FROM account_liabilities")}


def needs_a_balance():
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM accounts WHERE archived = 0 AND balance_as_of IS NULL "
            "ORDER BY sort_order, name").fetchall()


def resolve_for_source(source, mask=""):
    mask = (mask or "").strip()
    if mask:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT id FROM accounts WHERE mask = ? AND archived = 0 "
                "ORDER BY sort_order, name LIMIT 1", (mask,)).fetchone()
        if row:
            return row["id"]

    setting = _SOURCE_SETTING.get(source)
    if setting is None:
        return None
    raw = get_setting(setting, "")
    try:
        account_id = int(raw)
    except (TypeError, ValueError):
        return None
    if not 0 < account_id < 2 ** 63:
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM accounts WHERE id = ? AND archived = 0",
            (account_id,)).fetchone()
    return row["id"] if row else None


def is_plaid_backed(account_id):
    if not account_id:
        return False
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM accounts WHERE id = ? AND external_source = 'plaid'",
            (account_id,)).fetchone()
    return row is not None


def take_balance_snapshots(on=None):
    taken_on = (on or date.today()).isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO balance_snapshots "
            "(account_id, taken_on, balance_cents) "
            "SELECT id, ?, balance_cents FROM accounts "
            "WHERE archived = 0 AND balance_as_of IS NOT NULL",
            (taken_on,),
        )
        return cur.rowcount
