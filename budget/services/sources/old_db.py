import hashlib
import sqlite3
import tempfile
from pathlib import Path

from budget.db import get_conn
from budget.services import budgets

EXPECTED_TABLES = ("transactions", "categories")

ALLOWED_SOURCES = ("manual", "email", "recurring", "csv")


class ImportFailed(Exception):
    pass


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _snapshot(source_path):
    source = Path(source_path).expanduser()
    if not source.is_file():
        raise ImportFailed(f"No database found at {source}.")

    handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    handle.close()
    destination = Path(handle.name)
    try:
        src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        destination.unlink(missing_ok=True)
        raise ImportFailed(f"Could not open {source}: {exc}") from exc
    try:
        dst = sqlite3.connect(destination)
        try:
            src.backup(dst)
        finally:
            dst.close()
    except sqlite3.Error as exc:
        destination.unlink(missing_ok=True)
        raise ImportFailed(f"Could not read {source}: {exc}") from exc
    finally:
        src.close()
    return destination


def _fingerprint(source_path):
    return hashlib.sha256(
        str(Path(source_path).expanduser().resolve()).encode()).hexdigest()[:12]


def inspect(source_path):
    snapshot = _snapshot(source_path)
    try:
        conn = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            tables = {r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'")}
            if not set(EXPECTED_TABLES) <= tables:
                missing = ", ".join(sorted(set(EXPECTED_TABLES) - tables))
                raise ImportFailed(
                    f"That file is a database, but not a Budget Tool one -- it has "
                    f"no {missing} table.")
            counts = {}
            for table in ("transactions", "categories", "merchant_rules",
                          "recurring", "accounts"):
                if table in tables:
                    counts[table] = conn.execute(
                        f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            span = conn.execute(
                "SELECT MIN(txn_date), MAX(txn_date) FROM transactions").fetchone()
            return {"counts": counts, "earliest": span[0], "latest": span[1],
                    "tables": sorted(tables)}
        finally:
            conn.close()
    finally:
        snapshot.unlink(missing_ok=True)


def import_from(source_path):
    snapshot = _snapshot(source_path)
    fingerprint = _fingerprint(source_path)
    try:
        src = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
        src.row_factory = sqlite3.Row
        try:
            return _copy(src, fingerprint)
        finally:
            src.close()
    finally:
        snapshot.unlink(missing_ok=True)


def _copy(src, fingerprint):
    tables = {r["name"] for r in src.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    if not set(EXPECTED_TABLES) <= tables:
        missing = ", ".join(sorted(set(EXPECTED_TABLES) - tables))
        raise ImportFailed(
            f"That file is a database, but not a Budget Tool one -- it has no "
            f"{missing} table.")

    done = {"transactions": 0, "skipped": 0, "categories": 0, "accounts": 0,
            "rules": 0, "recurring": 0}

    txn_columns = _columns(src, "transactions")
    account_column = ("legacy_account" if "legacy_account" in txn_columns
                      else "account" if "account" in txn_columns else None)

    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        category_map = _copy_categories(src, conn, done)
        account_map = _copy_accounts(src, conn, done, account_column, txn_columns)
        _copy_rules(src, conn, done, tables, category_map)
        _copy_recurring(src, conn, done, tables, category_map)
        _copy_transactions(src, conn, done, fingerprint, txn_columns,
                           account_column, category_map, account_map)
    return done


def _copy_categories(src, conn, done):
    available = _columns(src, "categories")
    optional = [c for c in ("budget_kind", "budget_percent_bp",
                            "monthly_budget_cents", "archived")
                if c in available]
    rows = src.execute(
        f"SELECT id, name, kind{''.join(', ' + c for c in optional)} "
        "FROM categories").fetchall()

    existing = {r["name"].casefold(): r["id"] for r in
                conn.execute("SELECT id, name FROM categories")}
    mapping = {}
    for row in rows:
        key = row["name"].casefold()
        if key in existing:
            mapping[row["id"]] = existing[key]
            continue
        kind = row["kind"] if row["kind"] in ("expense", "income", "transfer") else "expense"
        new_id = conn.execute(
            "INSERT INTO categories (name, kind, archived) VALUES (?, ?, ?)",
            (row["name"], kind,
             row["archived"] if "archived" in optional else 0),
        ).lastrowid
        cents = row["monthly_budget_cents"] if "monthly_budget_cents" in optional else 0
        budget_kind = row["budget_kind"] if "budget_kind" in optional else "fixed"
        percent_bp = row["budget_percent_bp"] if "budget_percent_bp" in optional else 0
        if budget_kind not in ("fixed", "percent", "annual"):
            budget_kind = "fixed"
        if cents or percent_bp or budget_kind != "fixed":
            conn.execute(
                """INSERT INTO category_budgets (category_id, effective_from,
                       budget_kind, monthly_budget_cents, budget_percent_bp)
                   VALUES (?, ?, ?, ?, ?)""",
                (new_id, budgets.BEFORE_ANY_MONTH, budget_kind, cents, percent_bp))
        existing[key] = new_id
        mapping[row["id"]] = new_id
        done["categories"] += 1
    return mapping


def _copy_accounts(src, conn, done, account_column, txn_columns):
    if account_column is None:
        return {}
    names = [r[0] for r in src.execute(
        f"SELECT DISTINCT {account_column} FROM transactions "
        f"WHERE {account_column} IS NOT NULL AND {account_column} <> ''")]

    existing = {r["name"].casefold(): r["id"] for r in
                conn.execute("SELECT id, name FROM accounts")}
    mapping = {}
    for name in names:
        key = name.casefold()
        if key not in existing:
            existing[key] = conn.execute(
                "INSERT INTO accounts (name) VALUES (?)", (name,)).lastrowid
            done["accounts"] += 1
        mapping[name] = existing[key]
    return mapping


def _copy_rules(src, conn, done, tables, category_map):
    if "merchant_rules" not in tables:
        return
    existing = {r["match_text"].casefold() for r in
                conn.execute("SELECT match_text FROM merchant_rules")}
    for row in src.execute("SELECT match_text, category_id FROM merchant_rules"):
        target = category_map.get(row["category_id"])
        if target is None or row["match_text"].casefold() in existing:
            continue
        conn.execute(
            "INSERT INTO merchant_rules (match_text, category_id) VALUES (?, ?)",
            (row["match_text"], target))
        existing.add(row["match_text"].casefold())
        done["rules"] += 1


def _copy_recurring(src, conn, done, tables, category_map):
    if "recurring" not in tables:
        return
    available = _columns(src, "recurring")
    existing = {r["name"].casefold() for r in
                conn.execute("SELECT name FROM recurring")}
    for row in src.execute("SELECT * FROM recurring"):
        if row["name"].casefold() in existing:
            continue
        frequency = row["frequency"] if "frequency" in available else "monthly"
        if frequency not in ("weekly", "biweekly", "semimonthly", "monthly", "yearly"):
            frequency = "monthly"
        conn.execute(
            """INSERT INTO recurring (name, amount_cents, category_id, frequency,
                                      next_date, anchor_day, active)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (row["name"], row["amount_cents"],
             category_map.get(row["category_id"]) if "category_id" in available else None,
             frequency,
             row["next_date"],
             row["anchor_day"] if "anchor_day" in available else 1,
             row["active"] if "active" in available else 1),
        )
        existing.add(row["name"].casefold())
        done["recurring"] += 1


def _copy_transactions(src, conn, done, fingerprint, txn_columns, account_column,
                       category_map, account_map):
    optional = {c for c in ("note", "source", "external_id", "created_at")
                if c in txn_columns}
    select = ["id", "txn_date", "amount_cents", "merchant", "category_id"]
    select += sorted(optional)
    if account_column:
        select.append(account_column)

    have = {r[0] for r in conn.execute(
        "SELECT external_id FROM transactions WHERE external_id IS NOT NULL")}

    for row in src.execute(f"SELECT {', '.join(select)} FROM transactions "
                           "ORDER BY txn_date, id"):
        external = row["external_id"] if "external_id" in optional else None
        if not external:
            external = f"oldimport:{fingerprint}:{row['id']}"
        if external in have:
            done["skipped"] += 1
            continue

        conn.execute(
            """INSERT INTO transactions (txn_date, amount_cents, merchant, note,
                   legacy_account, account_id, category_id, source, external_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (row["txn_date"], row["amount_cents"], row["merchant"] or "",
             (row["note"] or "") if "note" in optional else "",
             (row[account_column] or "") if account_column else "",
             account_map.get(row[account_column]) if account_column else None,
             category_map.get(row["category_id"]),
             _source_of(row, optional),
             external),
        )
        have.add(external)
        done["transactions"] += 1


def _source_of(row, optional):
    if "source" not in optional:
        return "manual"
    value = row["source"]
    return value if value in ALLOWED_SOURCES else "manual"
