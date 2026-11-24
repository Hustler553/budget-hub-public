from datetime import date

from flask import Blueprint, render_template

from budget.services import budgets, glyphs, pending, reports
from budget.services import paychecks
from budget.services import trends
from budget.db import get_conn
from budget.utils import month_of, month_bounds, month_label

bp = Blueprint("dashboard", __name__)

HISTORY_MONTHS = 6
LONG_HISTORY_MONTHS = 12


def spending_by_category(conn, start, end):
    grouped = {}
    for row in conn.execute(
        """SELECT t.category_id, t.merchant, t.note, t.amount_cents
           FROM category_amounts t
           LEFT JOIN categories c ON c.id = t.category_id
           WHERE t.amount_cents < 0 AND t.category_id IS NOT NULL
             AND t.txn_date BETWEEN ? AND ?
             AND t.transfer_pair_id IS NULL
             AND (c.kind IS NULL OR c.kind != 'transfer')
           ORDER BY t.txn_date DESC, t.transaction_id DESC""",
        (start, end),
    ):
        grouped.setdefault(row["category_id"], []).append(row)
    return grouped


@bp.route("/")
def index():
    this_month = month_of(date.today())
    start, end = month_bounds(this_month)

    with get_conn() as conn:
        totals = budgets.month_totals(conn, start, end)

        budget_rows = budgets.sort_worst_first(
            budgets.category_rows(conn, start, end))
        uncat_spend = budgets.uncategorized_spend(conn, start, end)
        breakdown = spending_by_category(conn, start, end)

        recent = conn.execute(
            """SELECT t.*, c.name AS category_name,
                      c.icon AS category_icon, c.colour AS category_colour
               FROM transactions t LEFT JOIN categories c ON c.id = t.category_id
               ORDER BY t.txn_date DESC, t.id DESC LIMIT 8"""
        ).fetchall()
        recent = [{**dict(row),
                   "cat_icon": glyphs.icon_for(row["category_name"],
                                               row["category_icon"] or ""),
                   "cat_colour": glyphs.colour_for(row["category_name"],
                                                   row["category_colour"] or "")}
                  for row in recent]

        uncategorized_count = conn.execute(
            "SELECT COUNT(*) FROM category_amounts"
            " WHERE category_id IS NULL AND transfer_pair_id IS NULL"
        ).fetchone()[0]
        review_count = conn.execute(
            "SELECT COUNT(*) FROM email_log WHERE status = 'review'"
        ).fetchone()[0]

        history = trends.month_history(conn, this_month, HISTORY_MONTHS)

        pending_cents = pending.month_spending_cents(conn, start, end)

    over_rows = [r for r in budget_rows if r["status"] == "over"]

    return render_template(
        "dashboard.html",
        month_name=month_label(this_month),
        income=totals["income"],
        spending=-totals["expenses"],
        net=totals["income"] + totals["expenses"],
        budget_rows=budget_rows,
        over_rows=over_rows,
        uncat_spend=uncat_spend,
        breakdown=breakdown,
        chart=trends.chart_series(history),
        chart_stats=trends.chart_summary(history),
        recent=recent,
        uncategorized_count=uncategorized_count,
        review_count=review_count,
        pending_paycheck=paychecks.pending_paycheck(),
        pending_cents=pending_cents,
    )


@bp.route("/history")
def history():
    this_month = month_of(date.today())
    with get_conn() as conn:
        months = trends.month_history(conn, this_month, LONG_HISTORY_MONTHS)

    rate = reports.savings_rate(months)
    trend = reports.category_trend(this_month, LONG_HISTORY_MONTHS)
    flow = reports.sankey(this_month)

    return render_template(
        "history.html",
        chart=trends.chart_series(months),
        chart_stats=trends.chart_summary(months),
        rows=trends.running_totals(months),
        month_count=LONG_HISTORY_MONTHS,
        this_month_name=month_label(this_month),
        rate=rate,
        rate_chart=reports.savings_rate_chart(rate),
        trend=trend,
        trend_chart=reports.category_trend_chart(trend),
        flow=flow,
        flow_chart=reports.sankey_chart(flow),
        pending_paycheck=paychecks.pending_paycheck(),
    )
