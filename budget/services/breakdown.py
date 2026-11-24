from datetime import date, timedelta

from budget.db import get_conn
from budget.utils import add_months, month_label

GROUPS = {
    "category": "Category",
    "merchant": "Merchant",
    "account": "Account",
    "tag": "Tag",
    "month": "Month",
}

PERIODS = {
    "this-month": ("This month", lambda t: (t.replace(day=1), t)),
    "last-month": ("Last month", lambda t: (
        add_months(t.replace(day=1), -1), t.replace(day=1) - timedelta(days=1))),
    "90-days": ("Last 90 days", lambda t: (t - timedelta(days=89), t)),
    "this-year": ("This year", lambda t: (t.replace(month=1, day=1), t)),
    "12-months": ("Last 12 months", lambda t: (
        add_months(t.replace(day=1), -11), t)),
    "all": ("All time", lambda t: (None, None)),
}
DEFAULT_PERIOD = "this-month"

MAX_ROWS = 100


def resolve_period(period, date_from, date_to, today=None):
    today = today or date.today()
    if date_from or date_to:
        return date_from, date_to, "custom"
    if period not in PERIODS:
        period = DEFAULT_PERIOD
    start, end = PERIODS[period][1](today)
    return start, end, period


def _key_and_label(group):
    if group == "merchant":
        return ("LOWER(t.merchant)", "MAX(t.merchant)", "")
    if group == "account":
        return ("t.account_id", "a.name",
                " LEFT JOIN accounts a ON a.id = t.account_id")
    if group == "tag":
        return ("tg.id", "tg.name",
                " JOIN transaction_tags tt ON tt.transaction_id = t.transaction_id"
                " JOIN tags tg ON tg.id = tt.tag_id")
    if group == "month":
        return ("substr(t.txn_date, 1, 7)", "substr(t.txn_date, 1, 7)", "")
    return ("t.category_id", "c.name", "")


def _ledger_filter(group, key, label):
    if group == "category":
        return {"category": "none" if key is None else str(key)}
    if group == "account":
        return {"account": "none" if key is None else str(key)}
    if group == "tag":
        return {"tag": label}
    if group == "month":
        return {"month": key}
    return {"q": label} if label else {}


def _blank_label(group):
    return {"category": "Uncategorized", "account": "Unassigned",
            "merchant": "(no merchant)"}.get(group, "—")


def breakdown(start=None, end=None, group="category", account=None, tag=""):
    if group not in GROUPS:
        group = "category"
    key_sql, label_sql, joins = _key_and_label(group)

    where = ["t.transfer_pair_id IS NULL", "(c.kind IS NULL OR c.kind != 'transfer')"]
    params = []
    if start is not None:
        where.append("t.txn_date >= ?")
        params.append(str(start))
    if end is not None:
        where.append("t.txn_date <= ?")
        params.append(str(end))
    if account == "none":
        where.append("t.account_id IS NULL")
    elif account is not None:
        where.append("t.account_id = ?")
        params.append(account)
    if tag:
        where.append("EXISTS (SELECT 1 FROM transaction_tags ft"
                     "         JOIN tags fg ON fg.id = ft.tag_id"
                     "        WHERE ft.transaction_id = t.transaction_id"
                     "          AND fg.name = ?)")
        params.append(tag)
    clause = " AND ".join(where)
    base = ("FROM category_amounts t"
            " LEFT JOIN categories c ON c.id = t.category_id")
    sums = ("COALESCE(SUM(CASE WHEN t.amount_cents < 0 THEN -t.amount_cents END), 0) AS spent,"
            " COALESCE(SUM(CASE WHEN t.amount_cents > 0 THEN t.amount_cents END), 0) AS income,"
            " COUNT(DISTINCT t.transaction_id) AS n")
    order = "k" if group == "month" else "spent DESC, income DESC, label"

    with get_conn() as conn:
        total = conn.execute(f"SELECT {sums} {base} WHERE {clause}", params).fetchone()
        found = conn.execute(
            f"""SELECT {key_sql} AS k, {label_sql} AS label, {sums}
                  {base}{joins}
                 WHERE {clause}
                 GROUP BY {key_sql}
                 ORDER BY {order}
                 LIMIT ?""", params + [MAX_ROWS + 1]).fetchall()

    rows = []
    for row in found[:MAX_ROWS]:
        label = row["label"] or _blank_label(group)
        if group == "month":
            label = month_label(row["k"])
        rows.append({
            "label": label,
            "spent": row["spent"],
            "income": row["income"],
            "count": row["n"],
            "share": (round(100 * row["spent"] / total["spent"], 1)
                      if total["spent"] else 0),
            "ledger": _ledger_filter(group, row["k"], row["label"] or ""),
        })
    return {"rows": rows, "spent": total["spent"], "income": total["income"],
            "count": total["n"], "truncated": len(found) > MAX_ROWS}
