import bisect

from budget.services import glyphs
from budget.utils import BASIS_POINTS_PER_PERCENT, month_bounds, shift_month

TIGHT_THRESHOLD = 80

_STATUS_ORDER = {"over": 0, "tight": 1, "ontrack": 2, "nobudget": 3}


def pct_used(budget_cents, spent_cents):
    if budget_cents <= 0:
        return 0
    return min(100, round(spent_cents / budget_cents * 100))


def status_for(budget_cents, spent_cents):
    if budget_cents <= 0:
        return "nobudget"
    if spent_cents > budget_cents:
        return "over"
    if pct_used(budget_cents, spent_cents) >= TIGHT_THRESHOLD:
        return "tight"
    return "ontrack"


def month_totals(conn, start, end):
    return conn.execute(
        """SELECT
             COALESCE(SUM(CASE WHEN t.amount_cents > 0 THEN t.amount_cents END), 0) AS income,
             COALESCE(SUM(CASE WHEN t.amount_cents < 0 THEN t.amount_cents END), 0) AS expenses
           FROM category_amounts t
           LEFT JOIN categories c ON c.id = t.category_id
           WHERE t.txn_date BETWEEN ? AND ?
             AND t.transfer_pair_id IS NULL
             AND (c.kind IS NULL OR c.kind != 'transfer')""",
        (start, end),
    ).fetchone()


def income_for_month(conn, month):
    start, end = month_bounds(month)
    return conn.execute(
        """SELECT COALESCE(SUM(t.amount_cents), 0) FROM category_amounts t
           JOIN categories c ON c.id = t.category_id
           WHERE c.kind = 'income' AND t.amount_cents > 0
             AND t.txn_date BETWEEN ? AND ?
             AND t.transfer_pair_id IS NULL""",
        (start, end),
    ).fetchone()[0]


def prior_month_income(conn, month):
    return income_for_month(conn, shift_month(month, -1))


def percent_budget_cents(percent_bp, income_cents):
    if percent_bp <= 0 or income_cents <= 0:
        return 0
    return round(income_cents * percent_bp / (100 * BASIS_POINTS_PER_PERCENT))



def monthly_from_annual(annual_cents):
    if annual_cents <= 0:
        return 0
    return -(-annual_cents // 12)


NO_BUDGET = {"budget_kind": "fixed", "monthly_budget_cents": 0,
             "budget_percent_bp": 0, "annual_cents": 0}

BEFORE_ANY_MONTH = "0000-01"


def budget_for_month(spec, base_income):
    kind = spec["budget_kind"]
    if kind == "percent":
        return percent_budget_cents(spec["budget_percent_bp"], base_income)
    if kind == "annual":
        return monthly_from_annual(spec["annual_cents"])
    return spec["monthly_budget_cents"]


def effective_budgets(conn, month):
    return {
        row["category_id"]: row
        for row in conn.execute(
            """SELECT cb.category_id, cb.budget_kind, cb.monthly_budget_cents,
                      cb.budget_percent_bp, cb.annual_cents
                 FROM category_budgets cb
                WHERE cb.effective_from = (
                          SELECT MAX(effective_from) FROM category_budgets
                           WHERE category_id = cb.category_id
                             AND effective_from <= ?)""",
            (month,),
        )
    }


def allocation(conn, month):
    base_income = prior_month_income(conn, month)
    specs = effective_budgets(conn, month)

    savings = committed = 0
    percent_set = False
    for row in conn.execute(
            "SELECT id FROM categories WHERE kind = 'expense' AND archived = 0"):
        spec = specs.get(row["id"], NO_BUDGET)
        if spec["budget_kind"] == "percent":
            if spec["budget_percent_bp"] > 0:
                percent_set = True
            savings += budget_for_month(spec, base_income)
        else:
            committed += budget_for_month(spec, base_income)

    remainder = base_income - savings
    return {
        "income_cents": base_income,
        "savings_cents": savings,
        "remainder_cents": remainder,
        "committed_cents": committed,
        "unallocated_cents": remainder - committed,
        "awaiting_income": percent_set and base_income <= 0,
    }


def budget_timeline(conn, category_ids):
    timeline = {cid: ([], []) for cid in category_ids}
    for row in conn.execute(
            """SELECT category_id, effective_from, budget_kind,
                      monthly_budget_cents, budget_percent_bp, annual_cents
                 FROM category_budgets ORDER BY category_id, effective_from"""):
        entry = timeline.get(row["category_id"])
        if entry is not None:
            entry[0].append(row["effective_from"])
            entry[1].append(row)
    return timeline


def spec_at(entry, month):
    months, specs = entry
    index = bisect.bisect_right(months, month)
    return specs[index - 1] if index else NO_BUDGET


def _goal_progress(row, saved_cents, month, budget_cents):
    goal = row["goal_cents"]
    if goal <= 0:
        return None

    progress = {
        "target_cents": goal,
        "saved_cents": saved_cents,
        "short_by_cents": max(goal - saved_cents, 0),
        "pct": min(round(saved_cents / goal * 100), 100) if saved_cents > 0 else 0,
        "reached": saved_cents >= goal,
        "by_month": row["goal_date"],
        "months_left": None,
        "needed_per_month": None,
        "on_track": None,
    }
    if not row["goal_date"]:
        return progress

    months_left = _months_between(month, row["goal_date"])
    progress["months_left"] = months_left
    if progress["reached"]:
        progress["on_track"] = True
        progress["needed_per_month"] = 0
        return progress
    if months_left <= 0:
        progress["needed_per_month"] = progress["short_by_cents"]
        progress["on_track"] = False
        return progress

    needed = -(-progress["short_by_cents"] // months_left)
    progress["needed_per_month"] = needed
    progress["on_track"] = needed <= budget_cents or needed <= 0
    return progress


def _months_between(start_month, end_month):
    try:
        sy, sm = (int(part) for part in start_month.split("-")[:2])
        ey, em = (int(part) for part in end_month.split("-")[:2])
    except (ValueError, AttributeError):
        return 0
    return (ey - sy) * 12 + (em - sm)


def category_rows(conn, start, end):
    month = start[:7]
    base_income = prior_month_income(conn, month)
    specs = effective_budgets(conn, month)
    rows = conn.execute(
        """SELECT c.id, c.name, c.rollover_enabled,
                  c.goal_cents, c.goal_date, c.icon, c.colour,
                  COALESCE(SUM(-t.amount_cents), 0) AS spent_cents
           FROM categories c
           LEFT JOIN category_amounts t
             ON t.category_id = c.id
            AND t.amount_cents < 0
            AND t.txn_date BETWEEN ? AND ?
            AND t.transfer_pair_id IS NULL
           WHERE c.kind = 'expense' AND c.archived = 0
           GROUP BY c.id ORDER BY c.name""",
        (start, end),
    ).fetchall()

    carried = rollover_carried_in(conn, month)

    out = []
    for r in rows:
        spec = specs.get(r["id"], NO_BUDGET)
        is_percent = spec["budget_kind"] == "percent"
        is_annual = spec["budget_kind"] == "annual"
        budget = budget_for_month(spec, base_income)
        spent = r["spent_cents"]
        carried_in = carried.get(r["id"], 0)
        available = budget + carried_in
        out.append({
            "id": r["id"],
            "name": r["name"],
            "icon": glyphs.icon_for(r["name"], r["icon"]),
            "colour": glyphs.colour_for(r["name"], r["colour"]),
            "budget_cents": budget,
            "rollover_enabled": bool(r["rollover_enabled"]),
            "annual_cents": spec["annual_cents"],
            "is_annual": is_annual,
            "goal_cents": r["goal_cents"],
            "goal_date": r["goal_date"],
            "goal": _goal_progress(r, available - spent, month, budget),
            "carried_in_cents": carried_in,
            "available_cents": available,
            "budget_kind": spec["budget_kind"],
            "percent_bp": spec["budget_percent_bp"],
            "fixed_cents": spec["monthly_budget_cents"],
            "awaiting_income": is_percent and spec["budget_percent_bp"] > 0
                               and base_income <= 0,
            "spent_cents": spent,
            "remaining_cents": available - spent,
            "pct": pct_used(available, spent),
            "status": status_for(available, spent),
        })
    return out


def sort_worst_first(rows):
    return sorted(
        rows,
        key=lambda r: (_STATUS_ORDER.get(r["status"], 9), -r["pct"], r.get("name", "")),
    )


def uncategorized_spend(conn, start, end):
    return conn.execute(
        """SELECT COALESCE(SUM(-amount_cents), 0) FROM category_amounts
           WHERE category_id IS NULL AND amount_cents < 0
             AND txn_date BETWEEN ? AND ?
             AND transfer_pair_id IS NULL""",
        (start, end),
    ).fetchone()[0]


MAX_ROLLOVER_MONTHS = 120


def rollover_carried_in(conn, month):
    categories = conn.execute(
        "SELECT id FROM categories WHERE kind = 'expense' AND rollover_enabled = 1"
    ).fetchall()
    if not categories:
        return {}

    first = conn.execute("SELECT MIN(substr(txn_date, 1, 7)) FROM transactions").fetchone()[0]
    if first is None or first >= month:
        return {cat["id"]: 0 for cat in categories}

    months, cursor = [], first
    while cursor < month and len(months) < MAX_ROLLOVER_MONTHS:
        months.append(cursor)
        cursor = shift_month(cursor, 1)

    spent = {}
    for row in conn.execute(
            """SELECT t.category_id AS cid, substr(t.txn_date, 1, 7) AS m,
                      COALESCE(SUM(-t.amount_cents), 0) AS spent
                 FROM category_amounts t
                WHERE t.amount_cents < 0
                  AND t.transfer_pair_id IS NULL
                  AND substr(t.txn_date, 1, 7) >= ? AND substr(t.txn_date, 1, 7) < ?
                GROUP BY t.category_id, m""", (months[0], month)):
        spent[(row["cid"], row["m"])] = row["spent"]

    timeline = budget_timeline(conn, [cat["id"] for cat in categories])

    carried = {cat["id"]: 0 for cat in categories}
    for m in months:
        base_income = prior_month_income(conn, m)
        for cat in categories:
            budget = budget_for_month(spec_at(timeline[cat["id"]], m), base_income)
            if budget <= 0:
                continue
            carried[cat["id"]] += budget - spent.get((cat["id"], m), 0)
    return carried


UNGROUPED = "Ungrouped"


def grouped_rows(conn, rows):
    order = {r["id"]: (r["sort_order"], r["name"]) for r in conn.execute(
        "SELECT id, name, sort_order FROM category_groups")}
    names = {r["id"]: r["name"] for r in conn.execute(
        "SELECT id, name FROM category_groups")}
    membership = {r["id"]: r["group_id"] for r in conn.execute(
        "SELECT id, group_id FROM categories")}

    buckets = {}
    for row in rows:
        buckets.setdefault(membership.get(row["id"]), []).append(row)

    out = []
    for group_id in sorted((g for g in buckets if g is not None),
                           key=lambda g: order.get(g, (0, ""))):
        out.append(_bucket(names.get(group_id, UNGROUPED), group_id, buckets[group_id]))
    if None in buckets:
        out.append(_bucket(UNGROUPED, None, buckets[None]))
    return out


def _bucket(name, group_id, rows):
    return {
        "name": name,
        "id": group_id,
        "rows": rows,
        "budget_cents": sum(r["budget_cents"] for r in rows),
        "available_cents": sum(r["available_cents"] for r in rows),
        "spent_cents": sum(r["spent_cents"] for r in rows),
        "remaining_cents": sum(r["remaining_cents"] for r in rows),
        "carried_in_cents": sum(r["carried_in_cents"] for r in rows),
    }
