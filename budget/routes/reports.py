from datetime import date, timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for

from budget.services import accounts, breakdown, goals, investments, net_worth, tags
from budget.utils import parse_amount_to_cents, parse_iso_date, to_int

bp = Blueprint("reports", __name__)

RANGES = {"1m": 30, "6m": 182, "1y": 365, "all": None}
DEFAULT_RANGE = "6m"


def _account_filter(raw):
    if raw == "none":
        return "none"
    if raw.isascii() and raw.isdigit() and len(raw) <= 18:
        return int(raw)
    return None


@bp.route("/reports")
def index():
    group = request.args.get("by", "category")
    if group not in breakdown.GROUPS:
        group = "category"
    start, end, period = breakdown.resolve_period(
        request.args.get("period", ""),
        parse_iso_date(request.args.get("from")),
        parse_iso_date(request.args.get("to")))
    raw_account = request.args.get("account", "")
    account = _account_filter(raw_account)
    tag = request.args.get("tag", "").strip()

    report = breakdown.breakdown(start, end, group, account=account, tag=tag)

    carried = {}
    if start is not None:
        carried["from"] = str(start)
    if end is not None:
        carried["to"] = str(end)
    if account is not None:
        carried["account"] = str(account)
    if tag:
        carried["tag"] = tag

    for row in report["rows"]:
        link = dict(carried)
        if "month" in row["ledger"]:
            link.pop("from", None)
            link.pop("to", None)
        link.update(row["ledger"])
        row["link"] = link

    return render_template(
        "reports.html",
        report=report, group=group, groups=breakdown.GROUPS,
        period=period, periods=breakdown.PERIODS,
        start=start, end=end,
        account=raw_account if account is not None else "",
        tag=tag, account_choices=accounts.active(), all_tags=tags.all_tags(),
        kept={k: v for k, v in {
            "period": period if period != "custom" else None,
            "from": str(start) if period == "custom" and start else None,
            "to": str(end) if period == "custom" and end else None,
            "account": raw_account if account is not None else None,
            "tag": tag or None}.items() if v},
    )


@bp.route("/investments")
def investments_page():
    held = investments.positions()
    chosen = request.args.get("range", DEFAULT_RANGE).lower()
    if chosen not in RANGES:
        chosen = DEFAULT_RANGE
    today = date.today()
    days = RANGES[chosen]
    start = today - timedelta(days=days) if days else date(2000, 1, 1)
    history = net_worth.series(start, today, kind="investment")

    return render_template(
        "investments.html",
        held=held,
        totals=investments.totals(held),
        allocation=investments.allocation(held),
        by_account=investments.by_account(held),
        has_accounts=investments.has_investment_accounts(),
        nw_chart=net_worth.chart(history),
        nw_summary=net_worth.summary(history),
        nw_label="Investments",
        nw_range=chosen, nw_ranges=list(RANGES),
    )


@bp.route("/goals")
def goals_page():
    return render_template(
        "goals.html",
        goals=goals.progress(),
        account_choices=[a for a in accounts.active() if a["is_asset"]],
        today=str(date.today()),
    )


@bp.route("/goals/add", methods=["POST"])
def goal_add():
    try:
        target = parse_amount_to_cents(request.form.get("target", ""))
    except ValueError:
        flash("That target didn't look like an amount.")
        return redirect(url_for("reports.goals_page"))
    try:
        goals.create(
            request.form.get("name", ""), target,
            to_int(request.form.get("account_id")),
            target_date=parse_iso_date(request.form.get("target_date")),
            count_existing=request.form.get("count_existing") != "no")
    except goals.GoalRefused as refused:
        flash(str(refused))
        return redirect(url_for("reports.goals_page"))
    flash("Goal added.")
    return redirect(url_for("reports.goals_page"))


@bp.route("/goals/<int:goal_id>/delete", methods=["POST"])
def goal_delete(goal_id):
    goals.delete(goal_id)
    flash("Goal removed. The account and its money are untouched.")
    return redirect(url_for("reports.goals_page"))
