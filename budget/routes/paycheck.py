from datetime import date

from flask import (Blueprint, flash, redirect, render_template, request,
                   url_for)

from budget.routes import came_from
from budget.services import paychecks
from budget.services import recurring
from budget.db import get_conn, get_setting
from budget.utils import (PAY_FREQUENCIES, current_pay_period, parse_amount_to_cents,
                   parse_iso_date, to_int)

bp = Blueprint("paycheck", __name__)


@bp.route("/paycheck")
def index():
    frequency = get_setting("pay_frequency")
    amount = to_int(get_setting("pay_amount_cents", "0"), 0)
    anchor = parse_iso_date(get_setting("pay_anchor_date"))

    if frequency not in PAY_FREQUENCIES or anchor is None or amount <= 0:
        return render_template("paycheck.html", configured=False)

    today = date.today()
    period_start, next_payday = current_pay_period(today, frequency, anchor)

    posted = paychecks.posted_amount(period_start, next_payday)
    if posted is not None:
        amount = posted

    with get_conn() as conn:
        posted_bills = conn.execute(
            """SELECT t.merchant, t.txn_date, t.amount_cents FROM transactions t
               LEFT JOIN categories c ON c.id = t.category_id
               WHERE %s AND t.amount_cents < 0
                 AND t.txn_date >= ? AND t.txn_date < ?
                 AND t.transfer_pair_id IS NULL
                 AND (c.kind IS NULL OR c.kind != 'transfer')"""
            % recurring.OCCURRENCE_SQL,
            (str(period_start), str(next_payday)),
        ).fetchall()
    bills = [{"name": b["merchant"], "date": date.fromisoformat(b["txn_date"]),
              "amount_cents": b["amount_cents"], "paid": True} for b in posted_bills]
    for b in recurring.upcoming_bills(period_start, next_payday):
        if b["date"] > today:
            b["paid"] = False
            bills.append(b)
    bills.sort(key=lambda b: b["date"])
    bills_total = sum(-b["amount_cents"] for b in bills)

    with get_conn() as conn:
        variable_spent = conn.execute(
            """SELECT COALESCE(SUM(-t.amount_cents), 0) FROM category_amounts t
               LEFT JOIN categories c ON c.id = t.category_id
               WHERE t.amount_cents < 0 AND NOT %s
                 AND t.txn_date >= ? AND t.txn_date < ?
                 AND t.transfer_pair_id IS NULL
                 AND (c.kind IS NULL OR c.kind != 'transfer')"""
            % recurring.OCCURRENCE_SQL_VIEW,
            (str(period_start), str(next_payday)),
        ).fetchone()[0]
        extra_income = conn.execute(
            """SELECT COALESCE(SUM(t.amount_cents), 0) FROM category_amounts t
               LEFT JOIN categories c ON c.id = t.category_id
               WHERE t.amount_cents > 0 AND NOT %s
                 AND t.txn_date >= ? AND t.txn_date < ?
                 AND t.transfer_pair_id IS NULL
                 AND (c.kind IS NULL OR c.kind != 'transfer')"""
            % recurring.OCCURRENCE_SQL_VIEW,
            (str(period_start), str(next_payday)),
        ).fetchone()[0]

    observed_income = extra_income + (posted or 0)
    income_used = max(amount, observed_income)
    income_is_observed = observed_income >= amount
    safe_to_spend = income_used - bills_total - variable_spent
    days_left = (next_payday - today).days
    period_days = (next_payday - period_start).days

    return render_template(
        "paycheck.html",
        configured=True,
        period_start=period_start,
        next_payday=next_payday,
        days_left=days_left,
        pct_elapsed=round((period_days - days_left) / period_days * 100) if period_days else 0,
        paycheck=amount,
        paycheck_is_actual=posted is not None,
        extra_income=extra_income,
        observed_income=observed_income,
        income_used=income_used,
        income_is_observed=income_is_observed,
        bills=bills,
        bills_total=bills_total,
        variable_spent=variable_spent,
        safe_to_spend=safe_to_spend,
        per_day=safe_to_spend // days_left if days_left > 0 else safe_to_spend,
        confirm_enabled=paychecks.confirm_enabled(),
        pending_paycheck=paychecks.pending_paycheck(today),
    )


@bp.route("/paycheck/confirm-setting", methods=["POST"])
def confirm_setting():
    paychecks.set_confirm_enabled(bool(request.form.get("enabled")))
    flash("Payday check-in on — you'll be asked to confirm each paycheck "
          "amount on payday." if paychecks.confirm_enabled()
          else "Payday check-in off — paychecks post at their usual amount.")
    return redirect(url_for("paycheck.index"))


@bp.route("/paycheck/confirm", methods=["POST"])
def confirm():
    back = redirect(came_from(url_for("paycheck.index")))
    try:
        cents = parse_amount_to_cents(request.form.get("amount", ""))
    except ValueError:
        flash("Please enter a valid paycheck amount.")
        return back

    pending = paychecks.pending_paycheck()
    if pending is None or pending["id"] != to_int(request.form.get("txn_id")):
        flash("That payday has already been confirmed — nothing was changed.")
    elif paychecks.confirm(pending["id"], cents):
        flash("Paycheck confirmed.")
    else:
        flash("That paycheck is no longer there — nothing was changed.")
    return back
