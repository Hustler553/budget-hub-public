from flask import Blueprint, flash, redirect, render_template, request, url_for

from budget.services import budgets
from budget.db import get_conn
from budget.utils import (month_bounds, shift_month, month_label, normalize_month,
                   parse_amount_to_cents, parse_percent_to_bp, to_int)

bp = Blueprint("budget", __name__)

BUDGET_KINDS = ("fixed", "percent", "annual")


@bp.route("/budget")
def index():
    month = normalize_month(request.args.get("month"))
    start, end = month_bounds(month)
    prior_month = shift_month(month, -1)

    with get_conn() as conn:
        categories = budgets.category_rows(conn, start, end)
        uncategorized = budgets.uncategorized_spend(conn, start, end)
        base_income = budgets.prior_month_income(conn, month)
        allocation = budgets.allocation(conn, month)
        all_categories = conn.execute(
            "SELECT * FROM categories WHERE archived = 0 ORDER BY kind DESC, name"
        ).fetchall()
        groups = budgets.grouped_rows(conn, categories)

    total_budget = sum(c["budget_cents"] for c in categories)
    total_spent = sum(c["spent_cents"] for c in categories) + uncategorized

    return render_template(
        "budget.html",
        month=month,
        month_name=month_label(month),
        groups=groups,
        prev_month=shift_month(month, -1),
        next_month=shift_month(month, 1),
        categories=categories,
        all_categories=all_categories,
        uncategorized=uncategorized,
        base_income=base_income,
        allocation=allocation,
        base_month_name=month_label(prior_month),
        total_budget=total_budget,
        total_spent=total_spent,
        total_remaining=total_budget - total_spent,
    )


@bp.route("/budget/save", methods=["POST"])
def save():
    month = normalize_month(request.form.get("month"))
    with get_conn() as conn:
        in_force = budgets.effective_budgets(conn, month)
        for key, value in request.form.items():
            if not key.startswith("budget_"):
                continue
            category_id = to_int(key.removeprefix("budget_"))
            if category_id is None:
                continue
            try:
                cents = parse_amount_to_cents(value) if value.strip() else 0
            except ValueError:
                continue

            percent_bp = parse_percent_to_bp(
                request.form.get(f"percent_{category_id}", ""))
            kind = request.form.get(f"kind_{category_id}", "fixed")
            if kind not in BUDGET_KINDS:
                kind = "fixed"

            annual_cents = 0
            raw_annual = request.form.get(f"annual_{category_id}", "")
            if raw_annual.strip():
                try:
                    annual_cents = parse_amount_to_cents(raw_annual)
                except ValueError:
                    annual_cents = 0
            if kind == "annual" and annual_cents <= 0:
                kind = "fixed"
            if kind == "percent" and percent_bp <= 0:
                kind = "fixed"

            goal_cents = 0
            raw_goal = request.form.get(f"goal_{category_id}", "")
            if raw_goal.strip():
                try:
                    goal_cents = abs(parse_amount_to_cents(raw_goal))
                except ValueError:
                    goal_cents = 0
            goal_date = normalize_month(request.form.get(f"goaldate_{category_id}", ""))                 if request.form.get(f"goaldate_{category_id}", "").strip() else None

            rollover = 1 if f"rollover_{category_id}" in request.form else 0
            if goal_cents > 0:
                rollover = 1
            if kind == "annual":
                rollover = 1

            conn.execute(
                """UPDATE categories SET rollover_enabled = ?,
                       goal_cents = ?, goal_date = ? WHERE id = ?""",
                (rollover, goal_cents, goal_date, category_id),
            )

            current = in_force.get(category_id, budgets.NO_BUDGET)
            if (current["budget_kind"] == kind
                    and current["monthly_budget_cents"] == cents
                    and current["budget_percent_bp"] == percent_bp
                    and current["annual_cents"] == annual_cents):
                continue
            conn.execute(
                """INSERT INTO category_budgets (category_id, effective_from,
                       budget_kind, monthly_budget_cents, budget_percent_bp,
                       annual_cents)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT (category_id, effective_from) DO UPDATE SET
                       budget_kind = excluded.budget_kind,
                       monthly_budget_cents = excluded.monthly_budget_cents,
                       budget_percent_bp = excluded.budget_percent_bp,
                       annual_cents = excluded.annual_cents""",
                (category_id, month, kind, cents, percent_bp, annual_cents),
            )
    flash("Budgets saved.")
    return redirect(url_for("budget.index", month=month))
