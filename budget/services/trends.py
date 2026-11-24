from budget.utils import month_bounds, month_label, shift_month

PLOT_LEFT = 44
PLOT_RIGHT = 614
PLOT_FLOOR = 165
PLOT_HEIGHT = 155
BAR_WIDTH_RATIO = 0.19


def month_history(conn, end_month, months):
    earliest = shift_month(end_month, -(months - 1)) + "-01"
    latest = month_bounds(end_month)[1]
    by_month = {row["month"]: row for row in conn.execute(
        """SELECT substr(t.txn_date, 1, 7) AS month,
             COALESCE(SUM(CASE WHEN t.amount_cents > 0 THEN t.amount_cents END), 0) AS income,
             COALESCE(SUM(CASE WHEN t.amount_cents < 0 THEN -t.amount_cents END), 0) AS expenses
           FROM category_amounts t
           LEFT JOIN categories c ON c.id = t.category_id
           WHERE t.txn_date BETWEEN ? AND ?
             AND t.transfer_pair_id IS NULL
             AND (c.kind IS NULL OR c.kind != 'transfer')
           GROUP BY month""",
        (earliest, latest),
    ).fetchall()}

    out = []
    for i in range(months - 1, -1, -1):
        m = shift_month(end_month, -i)
        row = by_month.get(m)
        income = row["income"] if row else 0
        expenses = row["expenses"] if row else 0
        out.append({
            "month": m,
            "name": month_label(m),
            "label": month_label(m)[:3].upper(),
            "income": income,
            "expenses": expenses,
            "net": income - expenses,
            "is_current": i == 0,
        })
    return out


def chart_series(history):
    ceiling = max([max(e["income"], e["expenses"]) for e in history] or [0])
    step = (PLOT_RIGHT - PLOT_LEFT) / len(history) if history else 0
    bar_w = step * BAR_WIDTH_RATIO

    out = []
    for i, e in enumerate(history):
        entry = dict(e)
        if ceiling > 0:
            entry["income_frac"] = e["income"] / ceiling
            entry["expenses_frac"] = e["expenses"] / ceiling
            entry["net_frac"] = max(0.0, e["net"] / ceiling)
        else:
            entry["income_frac"] = 0.0
            entry["expenses_frac"] = 0.0
            entry["net_frac"] = 0.0

        cx = PLOT_LEFT + step * (i + 0.5)
        entry["cx"] = round(cx, 2)
        entry["bar_w"] = round(bar_w, 2)
        entry["in_x"] = round(cx - bar_w - 1, 2)
        entry["out_x"] = round(cx + 1, 2)
        entry["in_h"] = round(entry["income_frac"] * PLOT_HEIGHT, 2)
        entry["out_h"] = round(entry["expenses_frac"] * PLOT_HEIGHT, 2)
        entry["in_y"] = round(PLOT_FLOOR - entry["in_h"], 2)
        entry["out_y"] = round(PLOT_FLOOR - entry["out_h"], 2)
        entry["net_y"] = round(PLOT_FLOOR - entry["net_frac"] * PLOT_HEIGHT, 2)
        out.append(entry)
    return out


def chart_summary(history):
    if not history:
        return {"avg_income": 0, "avg_spending": 0, "avg_kept": 0,
                "kept_pct": 0, "total_kept": 0, "months": 0,
                "best_label": "—", "best_kept": 0}

    n = len(history)
    avg_income = round(sum(e["income"] for e in history) / n)
    avg_spending = round(sum(e["expenses"] for e in history) / n)
    avg_kept = avg_income - avg_spending
    kept_pct = round(avg_kept / avg_income * 100) if avg_income else 0
    total_kept = sum(e["net"] for e in history)

    base = {"avg_income": avg_income, "avg_spending": avg_spending,
            "avg_kept": avg_kept, "kept_pct": kept_pct,
            "total_kept": total_kept, "months": n}

    complete = [e for e in history
                if not e["is_current"] and (e["income"] or e["expenses"])]
    if not complete:
        return {**base, "best_label": "—", "best_kept": 0}
    best = max(complete, key=lambda e: e["net"])
    return {**base, "best_label": best["label"], "best_kept": best["net"]}


def running_totals(history):
    running = 0
    rows = []
    for e in history:
        running += e["net"]
        rows.append({**e, "running_kept": running})
    rows.reverse()
    return rows
