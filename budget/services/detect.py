from datetime import date

from budget.db import get_conn
from budget.utils import parse_iso_date

MIN_OCCURRENCES = 3

CADENCES = (
    ("weekly", 6, 8),
    ("biweekly", 12, 16),
    ("semimonthly", 14, 17),
    ("monthly", 27, 34),
    ("yearly", 358, 372),
)

AMOUNT_TOLERANCE = 0.12

PRICE_CHANGE_TOLERANCE = 0.02

LOOKBACK_DAYS = 800

MAX_OVERDUE_CADENCES = 1.5

RECENT_CHANGE_DAYS = 150


def _runs(conn, since_iso):
    rows = conn.execute(
        """SELECT t.merchant, t.txn_date, t.amount_cents, t.category_id
             FROM transactions t
             LEFT JOIN categories c ON c.id = t.category_id
            WHERE t.amount_cents < 0
              AND t.merchant <> ''
              AND t.txn_date >= ?
              AND t.transfer_pair_id IS NULL
              AND (c.kind IS NULL OR c.kind != 'transfer')
            ORDER BY t.merchant COLLATE NOCASE, t.txn_date""",
        (since_iso,),
    ).fetchall()

    runs = {}
    for row in rows:
        runs.setdefault(row["merchant"].casefold(), []).append(row)
    return runs


def _cadence(dates):
    if len(dates) < MIN_OCCURRENCES:
        return None
    gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
    for name, low, high in CADENCES:
        if all(low <= gap <= high for gap in gaps):
            return name
    return None


def _amounts_agree(cents):
    biggest, smallest = max(cents), min(cents)
    if biggest == 0:
        return False
    return (biggest - smallest) / abs(biggest) <= AMOUNT_TOLERANCE


def candidates(today=None):
    today = today or date.today()
    since = (today.toordinal() - LOOKBACK_DAYS)
    since_iso = date.fromordinal(since).isoformat()

    with get_conn() as conn:
        runs = _runs(conn, since_iso)
        known = {r["name"].casefold() for r in
                 conn.execute("SELECT name FROM recurring")}
        dismissed = {r["merchant"].casefold() for r in
                     conn.execute("SELECT merchant FROM recurring_dismissed")}

    out = []
    for key, rows in runs.items():
        if key in known or key in dismissed:
            continue
        dates = [parse_iso_date(r["txn_date"]) for r in rows]
        if any(d is None for d in dates):
            continue
        cadence = _cadence(dates)
        if cadence is None:
            continue
        cents = [r["amount_cents"] for r in rows]
        if not _amounts_agree(cents):
            continue

        gap = round(sum((b - a).days for a, b in zip(dates, dates[1:]))
                    / (len(dates) - 1))
        next_expected = date.fromordinal(dates[-1].toordinal() + gap)
        if (today - next_expected).days > gap * MAX_OVERDUE_CADENCES:
            continue

        out.append({
            "merchant": rows[-1]["merchant"],
            "cadence": cadence,
            "amount_cents": cents[-1],
            "occurrences": len(rows),
            "last_seen": dates[-1],
            "next_expected": next_expected,
            "category_id": rows[-1]["category_id"],
        })
    out.sort(key=lambda c: (c["last_seen"], c["merchant"]), reverse=True)
    return out


def price_changes(today=None):
    today = today or date.today()
    since_iso = date.fromordinal(today.toordinal() - LOOKBACK_DAYS).isoformat()

    with get_conn() as conn:
        runs = _runs(conn, since_iso)

    out = []
    for rows in runs.values():
        dates = [parse_iso_date(r["txn_date"]) for r in rows]
        if any(d is None for d in dates):
            continue
        cadence = _cadence(dates)
        if cadence is None:
            continue
        cents = [r["amount_cents"] for r in rows]

        at = None
        for i in range(len(cents) - 1, 0, -1):
            if cents[i] == cents[i - 1]:
                continue
            if cents[i - 1] == 0:
                break
            if (abs(cents[i] - cents[i - 1]) / abs(cents[i - 1])
                    <= PRICE_CHANGE_TOLERANCE):
                continue
            at = i
            break
        if at is None:
            continue

        changed_on = dates[at]
        if (today - changed_on).days > RECENT_CHANGE_DAYS:
            continue

        out.append({
            "merchant": rows[-1]["merchant"],
            "cadence": cadence,
            "was_cents": cents[at - 1],
            "now_cents": cents[at],
            "change_cents": cents[at] - cents[at - 1],
            "changed_on": changed_on,
            "occurrences": len(rows),
        })
    out.sort(key=lambda c: (c["changed_on"], c["merchant"]), reverse=True)
    return out


def dismiss(merchant):
    merchant = " ".join((merchant or "").split())
    if not merchant:
        return None
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO recurring_dismissed (merchant) VALUES (?)",
            (merchant,))
    return merchant


def undismiss(merchant):
    with get_conn() as conn:
        return conn.execute(
            "DELETE FROM recurring_dismissed WHERE merchant = ?", (merchant,)).rowcount


def dismissed():
    with get_conn() as conn:
        return [r["merchant"] for r in conn.execute(
            "SELECT merchant FROM recurring_dismissed ORDER BY merchant COLLATE NOCASE")]
