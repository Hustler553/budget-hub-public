from datetime import date, timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for

from budget.services import accounts, glyphs, net_worth
from budget.utils import parse_amount_to_cents, to_int

bp = Blueprint("accounts", __name__)

RANGES = {"1m": 30, "6m": 182, "1y": 365, "all": None}
DEFAULT_RANGE = "6m"

NOT_AN_AMOUNT = "That balance didn't look like an amount."
NO_SUCH_ACCOUNT = "That account couldn't be found."

_MINUS_SIGNS = ("-", "\u2212")


def _say(message):
    flash(message)
    return redirect(url_for("accounts.index"))


def _form_account():
    account_id = to_int(request.form.get("account_id"))
    if account_id is None or not 0 < account_id < 2 ** 63:
        return None
    return accounts.get(account_id)


def _balance_from_form():
    raw_balance = request.form.get("balance", "").strip()
    if not raw_balance:
        return None
    cents = parse_amount_to_cents(raw_balance)
    typed_minus = any(sign in raw_balance for sign in _MINUS_SIGNS)
    return -cents if request.form.get("owed") or typed_minus else cents


@bp.route("/accounts")
def index():
    assets, liabilities = accounts.grouped()
    assets = [glyphs.decorate(a) for a in assets]
    liabilities = [glyphs.decorate(a) for a in liabilities]

    chosen = request.args.get("range", DEFAULT_RANGE).lower()
    if chosen not in RANGES:
        chosen = DEFAULT_RANGE
    today = date.today()
    days = RANGES[chosen]
    start = today - timedelta(days=days) if days else date(2000, 1, 1)
    history = net_worth.series(start, today)

    return render_template(
        "accounts.html",
        net_worth_cents=accounts.net_worth_cents(),
        assets=assets,
        liabilities=liabilities,
        assets_total=sum(a["balance_cents"] for a in assets),
        liabilities_total=sum(a["balance_cents"] for a in liabilities),
        liability_terms=accounts.liability_terms(),
        holdings=accounts.holdings_by_account(),
        needs_balance=accounts.needs_a_balance(),
        archived=[a for a in accounts.all_accounts() if a["archived"]],
        kinds=accounts.KINDS,
        nw_chart=net_worth.chart(history),
        nw_summary=net_worth.summary(history),
        nw_range=chosen,
        nw_ranges=list(RANGES),
    )


@bp.route("/accounts/create", methods=["POST"])
def create():
    try:
        balance_cents = _balance_from_form()
    except ValueError:
        return _say(NOT_AN_AMOUNT)

    name = request.form.get("name", "").strip()
    try:
        accounts.create(
            name,
            kind=request.form.get("kind", ""),
            institution=request.form.get("institution", ""),
            mask=request.form.get("mask", ""),
            balance_cents=balance_cents,
        )
    except ValueError as exc:
        return _say(str(exc))
    return _say(f"Added {name}.")


@bp.route("/accounts/update", methods=["POST"])
def update():
    account = _form_account()
    if account is None:
        return _say(NO_SUCH_ACCOUNT)
    try:
        balance_cents = _balance_from_form()
    except ValueError:
        return _say(NOT_AN_AMOUNT)

    name = request.form.get("name", "").strip()
    kind = request.form.get("kind", "")
    fields = {
        "name": name,
        "kind": kind,
        "institution": request.form.get("institution", ""),
        "mask": request.form.get("mask", ""),
    }
    if kind != account["kind"]:
        fields["is_asset"] = kind in accounts.ASSET_KINDS

    try:
        accounts.update(account["id"], **fields)
        if balance_cents is not None and (account["balance_as_of"] is None
                                          or balance_cents != account["balance_cents"]):
            accounts.set_balance(account["id"], balance_cents)
    except (LookupError, ValueError) as exc:
        return _say(str(exc))
    return _say(f"Saved {name}.")


@bp.route("/accounts/archive", methods=["POST"])
def archive():
    account = _form_account()
    if account is None:
        return _say(NO_SUCH_ACCOUNT)
    try:
        accounts.archive(account["id"])
    except LookupError as exc:
        return _say(str(exc))
    return _say(f"Archived {account['name']}. It no longer counts toward net worth.")


@bp.route("/accounts/restore", methods=["POST"])
def restore():
    account = _form_account()
    if account is None:
        return _say(NO_SUCH_ACCOUNT)
    try:
        accounts.archive(account["id"], archived=False)
    except LookupError as exc:
        return _say(str(exc))
    return _say(f"Restored {account['name']}. It counts toward net worth again.")
