import re
import sqlite3
from datetime import date, timedelta

from budget.db import get_conn
from budget.services import transfers
from budget.services.txns import add_transaction
from budget.utils import add_months, parse_iso_date

FREQUENCIES = ("weekly", "biweekly", "semimonthly", "monthly", "yearly")

MAX_CATCHUP = 500


def advance(next_date, frequency, anchor_day):
    if frequency == "weekly":
        return next_date + timedelta(days=7)
    if frequency == "biweekly":
        return next_date + timedelta(days=14)
    if frequency == "semimonthly":
        if next_date.day < 15:
            return next_date.replace(day=15)
        return add_months(next_date.replace(day=1), 1)
    if frequency == "monthly":
        return add_months(next_date, 1, anchor_day=anchor_day)
    if frequency == "yearly":
        return add_months(next_date, 12, anchor_day=anchor_day)
    raise ValueError(f"Unknown frequency: {frequency}")


MATCH_WINDOW_DAYS = 5

FEED_SOURCES = ("plaid", "email", "csv")

INCOME_TOLERANCE = 0.10

OCCURRENCE_SQL = (
    "(t.source = 'recurring' OR EXISTS (SELECT 1 FROM recurring_matches rm "
    "WHERE rm.transaction_id = t.id))")
OCCURRENCE_SQL_VIEW = (
    "(t.source = 'recurring' OR EXISTS (SELECT 1 FROM recurring_matches rm "
    "WHERE rm.transaction_id = t.transaction_id))")


def _squash(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").casefold())


def _names_agree(item_name, merchant):
    a, b = _squash(item_name), _squash(merchant)
    if len(a) < 4 or len(b) < 4:
        return False
    return a in b or b in a


def _fit(name, amount_cents, row):
    if (row["amount_cents"] > 0) != (amount_cents > 0) or row["amount_cents"] == 0:
        return None
    if _names_agree(name, row["merchant"]):
        return 0
    if row["amount_cents"] == amount_cents:
        return 1
    if (amount_cents > 0 and row["source"] in FEED_SOURCES
            and abs(row["amount_cents"] - amount_cents)
            <= INCOME_TOLERANCE * amount_cents):
        return 2
    return None


def _window(day):
    return (str(day - timedelta(days=MATCH_WINDOW_DAYS)),
            str(day + timedelta(days=MATCH_WINDOW_DAYS)))


def _claim(conn, recurring_id, due_date, txn_id, category_id):
    conn.execute(
        "INSERT INTO recurring_matches (recurring_id, due_date, transaction_id) "
        "VALUES (?, ?, ?)", (recurring_id, str(due_date), txn_id))
    if category_id is not None:
        conn.execute(
            """UPDATE transactions SET category_id = ?
                WHERE id = ? AND category_id IS NULL
                  AND NOT EXISTS (SELECT 1 FROM transaction_splits s
                                   WHERE s.transaction_id = transactions.id)
                  AND EXISTS (SELECT 1 FROM categories c WHERE c.id = ?)""",
            (category_id, txn_id, category_id))


def settle_from_existing(conn, item, due_date):
    low, high = _window(due_date)
    best = None
    for row in conn.execute(
            """SELECT t.id, t.txn_date, t.amount_cents, t.merchant, t.source
                 FROM transactions t
                WHERE t.source != 'recurring'
                  AND t.txn_date BETWEEN ? AND ?
                  AND t.transfer_pair_id IS NULL
                  AND NOT EXISTS (SELECT 1 FROM recurring_matches rm
                                   WHERE rm.transaction_id = t.id)""",
            (low, high)):
        fit = _fit(item["name"], item["amount_cents"], row)
        if fit is None:
            continue
        distance = abs((parse_iso_date(row["txn_date"]) - due_date).days)
        key = (fit, distance, row["id"])
        if best is None or key < best[0]:
            best = (key, row["id"])
    if best is None:
        return None
    _claim(conn, item["id"], due_date, best[1], item["category_id"])
    return best[1]


def absorb_into(txn_id):
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        real = conn.execute(
            """SELECT t.id, t.txn_date, t.amount_cents, t.merchant, t.source,
                      t.transfer_pair_id
                 FROM transactions t WHERE t.id = ?""", (txn_id,)).fetchone()
        if (real is None or real["source"] not in FEED_SOURCES
                or real["transfer_pair_id"] is not None
                or conn.execute("SELECT 1 FROM recurring_matches "
                                "WHERE transaction_id = ?", (txn_id,)).fetchone()):
            return None
        day = parse_iso_date(real["txn_date"])
        if day is None:
            return None
        low, high = _window(day)

        best = None
        for posted in conn.execute(
                """SELECT id, txn_date, amount_cents, merchant, category_id,
                          external_id
                     FROM transactions
                    WHERE source = 'recurring' AND txn_date BETWEEN ? AND ?""",
                (low, high)):
            fit = _fit(posted["merchant"], posted["amount_cents"], real)
            if fit is None:
                continue
            distance = abs((parse_iso_date(posted["txn_date"]) - day).days)
            key = (fit, distance, posted["id"])
            if best is None or key < best[0]:
                best = (key, posted)
        if best is None:
            return None
        posted = best[1]

        parts = (posted["external_id"] or "").split(":")
        item_id = int(parts[1]) if len(parts) == 3 and parts[1].isdigit() else None
        due = parse_iso_date(parts[2]) if len(parts) == 3 else None

        transfers.release_pair(conn, posted["id"])
        conn.execute("DELETE FROM transactions WHERE id = ?", (posted["id"],))

        item_exists = item_id is not None and conn.execute(
            "SELECT 1 FROM recurring WHERE id = ?", (item_id,)).fetchone()
        already = item_exists and due is not None and conn.execute(
            "SELECT 1 FROM recurring_matches WHERE recurring_id = ? AND due_date = ?",
            (item_id, str(due))).fetchone()
        if item_exists and due is not None and not already:
            _claim(conn, item_id, due, txn_id, posted["category_id"])
        elif posted["category_id"] is not None:
            conn.execute(
                "UPDATE transactions SET category_id = ? "
                "WHERE id = ? AND category_id IS NULL",
                (posted["category_id"], txn_id))
        return posted["id"]


def post_due_recurring(today=None):
    today = today or date.today()
    with get_conn() as conn:
        items = conn.execute("SELECT * FROM recurring WHERE active = 1").fetchall()

    posted = 0
    for item in items:
        next_date = parse_iso_date(item["next_date"])
        if next_date is None:
            continue
        occurrences = 0
        while next_date <= today and occurrences < MAX_CATCHUP:
            occurrences += 1
            with get_conn() as conn:
                conn.execute("BEGIN IMMEDIATE")
                settled = conn.execute(
                    "SELECT 1 FROM recurring_matches "
                    "WHERE recurring_id = ? AND due_date = ?",
                    (item["id"], str(next_date))).fetchone()
                if not settled:
                    try:
                        settled = settle_from_existing(conn, item, next_date)
                    except sqlite3.IntegrityError:
                        settled = None
            if settled:
                next_date = advance(next_date, item["frequency"], item["anchor_day"])
                continue
            created = add_transaction(
                txn_date=next_date,
                amount_cents=item["amount_cents"],
                merchant=item["name"],
                note="(recurring)",
                category_id=item["category_id"],
                source="recurring",
                external_id=f"recurring:{item['id']}:{next_date}",
            )
            if created:
                posted += 1
            next_date = advance(next_date, item["frequency"], item["anchor_day"])

        if str(next_date) != item["next_date"]:
            with get_conn() as conn:
                conn.execute(
                    "UPDATE recurring SET next_date = ? WHERE id = ?",
                    (str(next_date), item["id"]),
                )
    return posted


def upcoming_bills(start, end):
    with get_conn() as conn:
        items = conn.execute(
            """SELECT r.*, c.name AS category_name, c.icon AS category_icon,
                      c.colour AS category_colour
                 FROM recurring r LEFT JOIN categories c ON c.id = r.category_id
                WHERE r.active = 1 AND r.amount_cents < 0"""
        ).fetchall()

    bills = []
    for item in items:
        d = parse_iso_date(item["next_date"])
        if d is None:
            continue
        steps = 0
        while d < end and steps < MAX_CATCHUP:
            steps += 1
            if d >= start:
                bills.append({"id": item["id"], "name": item["name"], "date": d,
                              "amount_cents": item["amount_cents"],
                              "category": item["category_name"],
                              "category_icon": item["category_icon"] or "",
                              "category_colour": item["category_colour"] or ""})
            d = advance(d, item["frequency"], item["anchor_day"])
    bills.sort(key=lambda b: b["date"])
    return bills
