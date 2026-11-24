import sqlite3

from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   url_for)

from budget.services import accounts, features, loans, moneymath
from budget.utils import parse_amount_to_cents, parse_percent_to_e4, to_int

bp = Blueprint("moneymath", __name__)

DEFAULTS = {"initial": "1000", "monthly": "250", "rate": "7", "years": "30"}

COMPOUNDING_LABELS = {"annually": "once a year",
                      "semiannually": "twice a year",
                      "quarterly": "four times a year",
                      "monthly": "every month"}

MAX_YEARS = moneymath.MAX_PERIODS // moneymath.MONTHS_PER_YEAR


def _amount(field, fallback):
    raw = request.args.get(field)
    if raw is None or not raw.strip():
        return parse_amount_to_cents(fallback)
    try:
        return parse_amount_to_cents(raw)
    except ValueError:
        return parse_amount_to_cents(fallback)


@bp.route("/projector")
@features.require("money_math")
def projector():
    initial_cents = _amount("initial", DEFAULTS["initial"])
    monthly_cents = _amount("monthly", DEFAULTS["monthly"])
    rate_e4 = parse_percent_to_e4(
        request.args.get("rate") or DEFAULTS["rate"])
    years = to_int(request.args.get("years"), None)
    if years is None or years < 1:
        years = int(DEFAULTS["years"])
    years = min(years, MAX_YEARS)
    at_start = request.args.get("timing") == "start"
    compounding = request.args.get("compounding")
    if compounding not in moneymath.COMPOUNDING:
        compounding = moneymath.DEFAULT_COMPOUNDING

    periods = years * moneymath.MONTHS_PER_YEAR
    rows = moneymath.growth_rows(initial_cents, monthly_cents, rate_e4,
                                 periods, compounding,
                                 at_period_start=at_start)

    milestones = [r for r in rows
                  if r["period"] % moneymath.MONTHS_PER_YEAR == 0]

    final = rows[-1] if rows else {"balance_cents": initial_cents,
                                   "paid_in_cents": initial_cents,
                                   "earned_cents": 0}
    crossover = next((r for r in rows
                      if r["earned_cents"] > r["paid_in_cents"]), None)

    return render_template(
        "projector.html",
        fields={"initial": request.args.get("initial", DEFAULTS["initial"]),
                "monthly": request.args.get("monthly", DEFAULTS["monthly"]),
                "rate": request.args.get("rate", DEFAULTS["rate"]),
                "years": str(years)},
        at_start=at_start,
        compounding=compounding,
        compounding_labels=COMPOUNDING_LABELS,
        years=years,
        max_years=MAX_YEARS,
        milestones=milestones,
        final=final,
        crossover=crossover,
    )


LOAN_DEFAULTS = {"amount": "25000", "rate": "6.5", "years": "5", "extra": "0"}

SCHEDULE_VIEWS = ("annual", "monthly")


def _schedule_view():
    asked = request.args.get("schedule")
    return asked if asked in SCHEDULE_VIEWS else "annual"


@bp.route("/loans")
@features.require("money_math")
def loans_page():
    amount_cents = _amount("amount", LOAN_DEFAULTS["amount"])
    rate_e4 = parse_percent_to_e4(request.args.get("rate")
                                  or LOAN_DEFAULTS["rate"])
    years = to_int(request.args.get("years"), None)
    if years is None or years < 1:
        years = int(LOAN_DEFAULTS["years"])
    years = min(years, MAX_YEARS)
    extra_cents = _amount("extra", LOAN_DEFAULTS["extra"])

    periods = years * moneymath.MONTHS_PER_YEAR
    quote = None
    if amount_cents > 0:
        payment_cents = moneymath.level_payment_cents(amount_cents, rate_e4,
                                                      periods)
        quote = loans.model(amount_cents, rate_e4, payment_cents, extra_cents,
                            periods=periods)
        quote["payment_cents"] = payment_cents

    view = _schedule_view()
    if quote and not quote["impossible"]:
        quote["annual"] = moneymath.by_year(quote["schedule"])

    stored = loans.all_loans()
    return render_template(
        "loans.html",
        schedule_view=view,
        fields={"amount": request.args.get("amount", LOAN_DEFAULTS["amount"]),
                "rate": request.args.get("rate", LOAN_DEFAULTS["rate"]),
                "years": str(years),
                "extra": request.args.get("extra", LOAN_DEFAULTS["extra"])},
        max_years=MAX_YEARS,
        quote=quote,
        loans=[(loan, loans.model_loan(loan)) for loan in stored],
        kinds=loans.KINDS,
        account_choices=_loan_account_choices(stored),
    )


def _loan_account_choices(stored):
    taken = {loan["account_id"] for loan in stored if loan["account_id"]}
    return [a for a in accounts.all_accounts()
            if not a["archived"] and a["kind"] in ("credit", "loan")
            and a["id"] not in taken]


def _loan_fields_from_form():
    return {
        "name": request.form.get("name", "").strip(),
        "purpose": request.form.get("purpose", "").strip(),
        "kind": (request.form.get("kind") or "personal"),
        "principal_cents": _form_amount("principal"),
        "annual_rate_e4": parse_percent_to_e4(request.form.get("rate")),
        "payment_cents": _form_amount("payment"),
        "escrow_cents": _form_amount("escrow"),
        "extra_cents": _form_amount("extra"),
    }


def _form_amount(field):
    raw = (request.form.get(field) or "").strip()
    if not raw:
        return 0
    try:
        return abs(parse_amount_to_cents(raw))
    except ValueError:
        return 0


@bp.route("/loans/add", methods=["POST"])
@features.require("money_math")
def loans_add():
    fields = _loan_fields_from_form()
    account_id = to_int(request.form.get("account_id"), None)
    if account_id:
        fields["account_id"] = account_id
    try:
        loans.create(fields.pop("name"), fields.pop("principal_cents"),
                     fields.pop("annual_rate_e4"), fields.pop("payment_cents"),
                     **fields)
    except ValueError as exc:
        flash(str(exc))
        return redirect(url_for("moneymath.loans_page"))
    except sqlite3.IntegrityError:
        flash("That account already has a loan on it.")
        return redirect(url_for("moneymath.loans_page"))
    flash("Loan added.")
    return redirect(url_for("moneymath.loans_page"))


@bp.route("/loans/<int:loan_id>/schedule")
@features.require("money_math")
def loans_schedule(loan_id):
    loan = loans.with_balance(loan_id)
    if loan is None:
        abort(404)
    model = loans.model_loan(loan)
    return render_template(
        "loan_schedule.html",
        loan=loan,
        model=model,
        schedule_view=_schedule_view(),
        annual=moneymath.by_year(model["schedule"]),
    )


@bp.route("/loans/<int:loan_id>/update", methods=["POST"])
@features.require("money_math")
def loans_update(loan_id):
    if loans.get(loan_id) is None:
        abort(404)
    try:
        loans.update(loan_id, **_loan_fields_from_form())
    except ValueError as exc:
        flash(str(exc))
        return redirect(url_for("moneymath.loans_page"))
    flash("Loan updated.")
    return redirect(url_for("moneymath.loans_page"))


@bp.route("/loans/<int:loan_id>/delete", methods=["POST"])
@features.require("money_math")
def loans_delete(loan_id):
    if loans.get(loan_id) is None:
        abort(404)
    loans.delete(loan_id)
    flash("Loan removed. Its account and transactions are untouched.")
    return redirect(url_for("moneymath.loans_page"))
