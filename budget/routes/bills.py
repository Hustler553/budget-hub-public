from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, url_for

from budget.services import bills as bills_service
from budget.services import detect, txns
from budget.db import get_conn
from budget.utils import (month_label, normalize_month, parse_iso_date,
                          shift_month, to_int)

bp = Blueprint("bills", __name__)

ALLOWED_CADENCES = ("weekly", "biweekly", "semimonthly", "monthly", "yearly")


@bp.route("/bills")
def index():
    month = normalize_month(request.args.get("month")) or date.today().strftime("%Y-%m")
    items = bills_service.month_bills(month)
    return render_template(
        "bills.html",
        month=month,
        month_name=month_label(month),
        prev_month=shift_month(month, -1),
        next_month=shift_month(month, 1),
        bills=items,
        weeks=bills_service.month_grid(month, items),
        summary=bills_service.month_summary(items),
        suggestions=detect.candidates(),
        price_changes=detect.price_changes(),
        skipped=detect.dismissed(),
    )


@bp.route("/bills/suggestions/accept", methods=["POST"])
def accept_suggestion():
    merchant = " ".join(request.form.get("merchant", "").split())
    cents = to_int(request.form.get("amount_cents"))
    frequency = request.form.get("cadence", "")
    next_date = parse_iso_date(request.form.get("next_expected"))

    if not merchant or cents is None or cents >= 0 or next_date is None:
        flash("That suggestion couldn't be read. Add it by hand in Settings.")
        return redirect(url_for("bills.index"))
    if frequency not in ALLOWED_CADENCES:
        flash("That suggestion had a schedule this app doesn't store.")
        return redirect(url_for("bills.index"))

    with get_conn() as conn:
        conn.execute(
            """INSERT INTO recurring (name, amount_cents, category_id, frequency,
                                      next_date, anchor_day)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (merchant, cents,
             txns.valid_category_id(conn, to_int(request.form.get("category_id"))),
             frequency, next_date.isoformat(), next_date.day),
        )
    flash(f"{merchant} added as a {frequency} bill.")
    return redirect(url_for("bills.index"))


@bp.route("/bills/suggestions/dismiss", methods=["POST"])
def dismiss_suggestion():
    name = detect.dismiss(request.form.get("merchant", ""))
    if name:
        flash(f"Not suggesting {name} again.")
    return redirect(url_for("bills.index"))


@bp.route("/bills/suggestions/restore", methods=["POST"])
def restore_suggestion():
    detect.undismiss(request.form.get("merchant", ""))
    return redirect(url_for("bills.index"))
