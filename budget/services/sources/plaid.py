import json
import logging
import urllib.error
import urllib.request
from datetime import date

from cryptography.exceptions import InvalidTag

from budget import config
from budget.db import get_conn, get_secret, get_setting
from budget.services import branding, features, pending, transfers, vault
from budget.services.txns import add_transaction
from budget.utils import BASIS_POINTS_PER_PERCENT, E8_SCALE

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 30

PRODUCTS = ("transactions",)

class PlaidError(Exception):

    def __init__(self, message, code="", request_id=""):
        super().__init__(message)
        self.code = code
        self.request_id = request_id


class NotConfigured(PlaidError):
    pass


def credentials():
    return get_setting("plaid_client_id").strip(), get_secret("plaid_secret").strip()


def configured():
    client_id, secret = credentials()
    return bool(client_id and secret and vault.configured())


def seal(token, item_id):
    try:
        return vault.seal_bytes(token, item_id)
    except vault.NoKey as exc:
        raise NotConfigured(str(exc)) from exc


def unseal(blob, item_id):
    try:
        return vault.unseal_bytes(blob, item_id)
    except vault.NoKey as exc:
        raise NotConfigured(str(exc)) from exc
    except InvalidTag as exc:
        raise PlaidError(
            "A stored Plaid token could not be decrypted. The usual cause is "
            "BUDGET_PLAID_KEY having changed since the bank was linked; "
            "re-linking the institution will store a fresh token.",
            code="DECRYPT_FAILED") from exc


def _post(path, payload):
    client_id, secret = credentials()
    if not (client_id and secret):
        raise NotConfigured(
            "Plaid has no credentials yet: put the client id and secret in "
            "Settings.")
    if not vault.configured():
        raise NotConfigured(
            "BUDGET_VAULT_KEY is not set in the environment, so no access "
            "token could be stored safely. Nothing was sent to Plaid.")

    body = dict(payload)
    body["client_id"] = client_id
    body["secret"] = secret

    url = f"{config.PLAID_HOSTS[config.PLAID_ENV]}{path}"
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail, code, request_id = "", "", ""
        try:
            parsed = json.loads(exc.read().decode())
            detail = parsed.get("error_message") or ""
            code = parsed.get("error_code") or ""
            request_id = parsed.get("request_id") or ""
        except Exception:
            pass
        raise PlaidError(
            detail or f"Plaid returned HTTP {exc.code} for {path}",
            code=code, request_id=request_id) from exc
    except urllib.error.URLError as exc:
        raise PlaidError(f"Could not reach Plaid: {exc.reason}") from exc


def create_link_token(user_id, client_name=branding.APP_NAME):
    result = _post("/link/token/create", {
        "user": {"client_user_id": str(user_id)},
        "client_name": client_name,
        "products": list(PRODUCTS),
        "country_codes": ["US"],
        "language": "en",
    })
    return result["link_token"]


def exchange_public_token(public_token):
    result = _post("/item/public_token/exchange", {"public_token": public_token})
    return result["item_id"], result["access_token"]


def institution_name(institution_id):
    try:
        result = _post("/institutions/get_by_id", {
            "institution_id": institution_id,
            "country_codes": ["US"],
        })
        return result["institution"]["name"]
    except PlaidError:
        log.warning("Could not read the institution name for %s", institution_id)
        return ""


def accounts_for(access_token):
    return _post("/accounts/get", {"access_token": access_token})["accounts"]


def sync_transactions(access_token, cursor=None):
    payload = {"access_token": access_token}
    if cursor:
        payload["cursor"] = cursor
    result = _post("/transactions/sync", payload)
    return (
        result.get("added", []),
        result.get("modified", []),
        result.get("removed", []),
        result.get("next_cursor"),
        bool(result.get("has_more")),
    )


def normalize_amount(plaid_amount):
    return -int(round(float(plaid_amount) * 100))


def normalize_date(txn):
    return txn.get("authorized_date") or txn.get("date") or date.today().isoformat()


def normalize_pending(txn):
    return {
        "plaid_txn_id": txn.get("transaction_id"),
        "txn_date": normalize_date(txn),
        "amount_cents": normalize_amount(txn.get("amount") or 0),
        "merchant": _merchant_of(txn),
    }


def save_item(item_id, access_token, institution=""):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO plaid_items (item_id, access_token, institution)
               VALUES (?, ?, ?)
               ON CONFLICT (item_id) DO UPDATE SET
                   access_token = excluded.access_token,
                   institution = excluded.institution,
                   last_error = NULL""",
            (item_id, seal(access_token, item_id), institution))


def reseal_tokens():
    if not vault.configured():
        return 0
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT item_id, access_token FROM plaid_items").fetchall()
    except Exception:
        return 0

    resealed = 0
    for row in rows:
        item_id = row["item_id"]
        try:
            token, version = vault.open_bytes(row["access_token"], item_id)
        except Exception:
            continue
        if version == 2 or token is None:
            continue
        try:
            with get_conn() as conn:
                conn.execute(
                    "UPDATE plaid_items SET access_token = ? WHERE item_id = ?",
                    (vault.seal_bytes(token, item_id), item_id))
            resealed += 1
        except Exception:
            continue
    if resealed:
        log.info("Re-sealed %d Plaid token(s) to the current scheme", resealed)
    return resealed


def items():
    with get_conn() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT item_id, institution, cursor, linked_on, last_sync, "
            "last_error FROM plaid_items ORDER BY linked_on, institution")]


def token_for(item_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT access_token FROM plaid_items WHERE item_id = ?",
            (item_id,)).fetchone()
    return unseal(row["access_token"], item_id) if row else None


_SUBTYPE_KINDS = {
    "checking": "checking",
    "savings": "savings",
    "money market": "savings",
    "cd": "savings",
    "cash management": "checking",
    "credit card": "credit",
    "paypal": "cash",
    "prepaid": "cash",
    "student": "loan",
    "mortgage": "loan",
    "auto": "loan",
    "home equity": "loan",
    "line of credit": "loan",
}
_TYPE_KINDS = {
    "depository": "checking",
    "credit": "credit",
    "loan": "loan",
    "investment": "investment",
    "brokerage": "investment",
}

_TYPE_SUBTYPE_KINDS = {
    ("credit", "paypal"): "credit",
}

LIABILITY_KINDS = frozenset({"credit", "loan"})
INVESTMENT_KINDS = frozenset({"investment"})


def account_kind(plaid_account):
    account_type = (plaid_account.get("type") or "").lower()
    subtype = (plaid_account.get("subtype") or "").lower()
    pair = _TYPE_SUBTYPE_KINDS.get((account_type, subtype))
    if pair is not None:
        return pair
    if subtype in _SUBTYPE_KINDS:
        return _SUBTYPE_KINDS[subtype]
    return _TYPE_KINDS.get(account_type, "other")


def balance_cents(plaid_account):
    balances = plaid_account.get("balances") or {}
    current = balances.get("current")
    if current is None:
        return None
    cents = int(round(float(current) * 100))
    owed = account_kind(plaid_account) in ("credit", "loan")
    return -cents if owed else cents


def link_accounts(access_token, item_id, institution=""):
    created = updated = 0
    with get_conn() as conn:
        for account in accounts_for(access_token):
            external_id = account.get("account_id")
            if not external_id:
                continue
            cents = balance_cents(account)
            existing = conn.execute(
                "SELECT id FROM accounts WHERE external_source = 'plaid' "
                "AND external_id = ?", (external_id,)).fetchone()
            if existing:
                if cents is not None:
                    conn.execute(
                        "UPDATE accounts SET balance_cents = ?, "
                        "balance_as_of = date('now'), external_item = ? "
                        "WHERE id = ?",
                        (cents, item_id, existing["id"]))
                updated += 1
                continue
            kind = account_kind(account)
            conn.execute(
                """INSERT INTO accounts
                       (name, kind, mask, institution, is_asset, balance_cents,
                        balance_as_of, external_source, external_id,
                        external_item)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'plaid', ?, ?)""",
                (
                    account.get("name") or "Account",
                    kind,
                    account.get("mask") or "",
                    institution,
                    0 if kind in ("credit", "loan") else 1,
                    cents or 0,
                    date.today().isoformat() if cents is not None else None,
                    external_id,
                    item_id,
                ))
            created += 1
    return created, updated


def unlink(item_id):
    with get_conn() as conn:
        conn.execute(
            "UPDATE accounts SET external_source = 'manual', external_id = NULL, "
            "external_item = NULL WHERE external_item = ?", (item_id,))
        pending.drop_for_item(conn, item_id)
        conn.execute("DELETE FROM plaid_items WHERE item_id = ?", (item_id,))


_ALREADY_GONE = frozenset({"ITEM_NOT_FOUND", "INVALID_ACCESS_TOKEN"})


def disconnect(item_id):
    try:
        token = token_for(item_id)
    except PlaidError:
        unlink(item_id)
        return False
    if token is None:
        unlink(item_id)
        return True
    try:
        _post("/item/remove", {"access_token": token})
    except NotConfigured:
        unlink(item_id)
        return False
    except PlaidError as exc:
        if exc.code not in _ALREADY_GONE:
            raise
    unlink(item_id)
    return True


def _linked_accounts(conn, item_id):
    return {row["external_id"]: row["id"] for row in conn.execute(
        "SELECT id, external_id FROM accounts WHERE external_source = 'plaid' "
        "AND external_item = ?", (item_id,))}


def _item_kinds(conn, item_id):
    return {row["kind"] for row in conn.execute(
        "SELECT DISTINCT kind FROM accounts WHERE external_source = 'plaid' "
        "AND external_item = ?", (item_id,))}


def _merchant_of(txn):
    return (txn.get("merchant_name") or txn.get("name") or "").strip()


_DETAILED_CATEGORY = {
    "FOOD_AND_DRINK_GROCERIES": "Groceries",
    "RENT_AND_UTILITIES_RENT": "Housing / Rent",
    "LOAN_PAYMENTS_MORTGAGE_PAYMENT": "Housing / Rent",
    "INCOME_WAGES": "Paycheck",
    "PERSONAL_CARE_GYMS_AND_FITNESS_CENTERS": "Health / Fitness",
    "GENERAL_MERCHANDISE_GIFTS_AND_NOVELTIES": "Gifts",
    "GOVERNMENT_AND_NON_PROFIT_DONATIONS": "Gifts",
}
_PRIMARY_CATEGORY = {
    "FOOD_AND_DRINK": "Dining Out",
    "TRANSPORTATION": "Transportation / Gas",
    "RENT_AND_UTILITIES": "Utilities",
    "MEDICAL": "Health / Fitness",
    "ENTERTAINMENT": "Entertainment",
    "GENERAL_MERCHANDISE": "Shopping",
    "HOME_IMPROVEMENT": "Shopping",
    "TRAVEL": "Travel",
    "INCOME": "Other Income",
    "PERSONAL_CARE": "Miscellaneous",
    "GENERAL_SERVICES": "Miscellaneous",
    "GOVERNMENT_AND_NON_PROFIT": "Miscellaneous",
    "BANK_FEES": "Miscellaneous",
}


def bank_category_name(txn):
    filed = txn.get("personal_finance_category")
    if not isinstance(filed, dict):
        return None
    primary = str(filed.get("primary") or "").upper()
    name = (_DETAILED_CATEGORY.get(str(filed.get("detailed") or "").upper())
            or _PRIMARY_CATEGORY.get(primary))
    if primary == "INCOME" and normalize_amount(txn.get("amount") or 0) <= 0:
        return None
    return name


def _category_ids(conn):
    return {row["name"].casefold(): row["id"] for row in conn.execute(
        "SELECT id, name FROM categories WHERE archived = 0")}


PRINCIPAL_KEYS = ("principal_balance", "last_statement_balance")

PREFERRED_APR_TYPE = "purchase_apr"


def _magnitude_cents(value):
    if value is None:
        return None
    try:
        return abs(int(round(float(value) * 100)))
    except (TypeError, ValueError):
        return None


def _percent_bp(value):
    if value is None:
        return None
    try:
        return int(round(float(value) * BASIS_POINTS_PER_PERCENT))
    except (TypeError, ValueError):
        return None


def _rate_of(row):
    rate = row.get("interest_rate")
    if isinstance(rate, dict) and rate.get("percentage") is not None:
        return _percent_bp(rate.get("percentage")), str(rate.get("type") or "")
    if row.get("interest_rate_percentage") is not None:
        return _percent_bp(row.get("interest_rate_percentage")), ""

    aprs = [a for a in (row.get("aprs") or [])
            if isinstance(a, dict) and a.get("apr_percentage") is not None]
    if not aprs:
        return None, ""
    preferred = [a for a in aprs if a.get("apr_type") == PREFERRED_APR_TYPE]
    chosen = (preferred[0] if preferred
              else max(aprs, key=lambda a: float(a["apr_percentage"])))
    return _percent_bp(chosen["apr_percentage"]), str(chosen.get("apr_type") or "")


def _first_cents(row, *keys):
    for key in keys:
        cents = _magnitude_cents(row.get(key))
        if cents is not None:
            return cents
    return None


def credit_limit_cents(plaid_account):
    balances = plaid_account.get("balances") or {}
    return _magnitude_cents(balances.get("limit"))


def normalize_liability(kind, row, limit_cents=None):
    apr_bp, apr_type = _rate_of(row)
    past_due = _magnitude_cents(row.get("past_due_amount"))
    return {
        "liability_kind": str(kind),
        "apr_bp": apr_bp,
        "apr_type": apr_type,
        "principal_owed_cents": _first_cents(row, *PRINCIPAL_KEYS),
        "origination_principal_cents": _magnitude_cents(
            row.get("origination_principal_amount")),
        "credit_limit_cents": limit_cents,
        "minimum_payment_cents": _magnitude_cents(
            row.get("minimum_payment_amount")),
        "next_payment_cents": _first_cents(
            row, "next_payment_amount", "next_monthly_payment",
            "minimum_payment_amount"),
        "next_payment_due": row.get("next_payment_due_date") or None,
        "last_payment_cents": _magnitude_cents(row.get("last_payment_amount")),
        "last_payment_on": row.get("last_payment_date") or None,
        "maturity_date": (row.get("maturity_date")
                          or row.get("expected_payoff_date") or None),
        "ytd_interest_cents": _magnitude_cents(row.get("ytd_interest_paid")),
        "past_due_cents": past_due,
        "is_overdue": 1 if (row.get("is_overdue") or (past_due or 0) > 0) else 0,
    }


def liabilities_for(access_token):
    result = _post("/liabilities/get", {"access_token": access_token})
    return result.get("liabilities") or {}


def refresh_liabilities(access_token, item_id):
    limits = {a.get("account_id"): credit_limit_cents(a)
              for a in accounts_for(access_token)}

    written = 0
    with get_conn() as conn:
        accounts_by_plaid_id = _linked_accounts(conn, item_id)
        for kind, rows in liabilities_for(access_token).items():
            for row in (rows or []):
                account_id = accounts_by_plaid_id.get(row.get("account_id"))
                if account_id is None:
                    continue
                values = normalize_liability(
                    kind, row, limits.get(row.get("account_id")))
                columns = ["account_id"] + list(values)
                marks = ", ".join("?" * len(columns))
                updates = ", ".join(f"{c} = excluded.{c}" for c in values)
                conn.execute(
                    f"""INSERT INTO account_liabilities ({', '.join(columns)})
                        VALUES ({marks})
                        ON CONFLICT (account_id) DO UPDATE SET {updates},
                            refreshed_at = datetime('now')""",
                    [account_id] + list(values.values()))
                written += 1
    return written


E8 = E8_SCALE


def _to_e8(value):
    if value is None:
        return None
    try:
        return int(round(float(value) * E8))
    except (TypeError, ValueError):
        return None


def _value_cents(value):
    if value is None:
        return None
    try:
        return int(round(float(value) * 100))
    except (TypeError, ValueError):
        return None


def holdings_for(access_token):
    result = _post("/investments/holdings/get", {"access_token": access_token})
    return result.get("holdings") or [], result.get("securities") or []


def refresh_holdings(access_token, item_id):
    holdings, securities = holdings_for(access_token)
    stored = sold = 0
    with get_conn() as conn:
        accounts_by_plaid_id = _linked_accounts(conn, item_id)

        known_securities = set()
        for security in securities:
            security_id = security.get("security_id")
            if not security_id:
                continue
            known_securities.add(security_id)
            conn.execute(
                """INSERT INTO securities (security_id, ticker, name,
                       security_type, currency, close_price_e8, close_price_as_of)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (security_id) DO UPDATE SET
                       ticker = excluded.ticker, name = excluded.name,
                       security_type = excluded.security_type,
                       currency = excluded.currency,
                       close_price_e8 = excluded.close_price_e8,
                       close_price_as_of = excluded.close_price_as_of""",
                (security_id,
                 (security.get("ticker_symbol") or "").strip(),
                 (security.get("name") or "").strip(),
                 (security.get("type") or "").strip(),
                 (security.get("iso_currency_code")
                  or security.get("unofficial_currency_code") or "").strip(),
                 _to_e8(security.get("close_price")),
                 security.get("close_price_as_of") or None))

        seen = {}
        for holding in holdings:
            account_id = accounts_by_plaid_id.get(holding.get("account_id"))
            security_id = holding.get("security_id")
            quantity = _to_e8(holding.get("quantity"))
            if account_id is None or not security_id or quantity is None:
                continue
            if security_id not in known_securities:
                log.info("Plaid sent a holding for an undescribed security")
                continue
            conn.execute(
                """INSERT INTO holdings (account_id, security_id, quantity_e8,
                       price_e8, price_as_of, value_cents, cost_basis_cents)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (account_id, security_id) DO UPDATE SET
                       quantity_e8 = excluded.quantity_e8,
                       price_e8 = excluded.price_e8,
                       price_as_of = excluded.price_as_of,
                       value_cents = excluded.value_cents,
                       cost_basis_cents = excluded.cost_basis_cents,
                       refreshed_at = datetime('now')""",
                (account_id, security_id, quantity,
                 _to_e8(holding.get("institution_price")),
                 holding.get("institution_price_as_of") or None,
                 _value_cents(holding.get("institution_value")),
                 _value_cents(holding.get("cost_basis"))))
            seen.setdefault(account_id, set()).add(security_id)
            stored += 1

        for account_id, security_ids in seen.items():
            marks = ", ".join("?" * len(security_ids))
            sold += conn.execute(
                f"DELETE FROM holdings WHERE account_id = ? "
                f"AND security_id NOT IN ({marks})",
                [account_id] + list(security_ids)).rowcount
    return stored, sold


def _record_failure(item_id, message):
    try:
        with get_conn() as conn:
            conn.execute("UPDATE plaid_items SET last_error = ? WHERE item_id = ?",
                         (message[:400], item_id))
    except Exception:
        log.warning("Could not record a Plaid failure against item")


def _apply_revision(conn, txn):
    external = "plaid:%s" % txn.get("transaction_id")
    was = conn.execute(
        "SELECT id, txn_date, amount_cents FROM transactions WHERE external_id = ?",
        (external,)).fetchone()
    if was is None:
        return 0

    txn_date = normalize_date(txn)
    amount = normalize_amount(txn.get("amount") or 0)
    touched_the_rule = (was["txn_date"] != txn_date
                        or was["amount_cents"] != amount)

    partner = None
    if touched_the_rule:
        partner = transfers.other_half(conn, was["id"])
        transfers.release_pair(conn, was["id"])

    if was["amount_cents"] != amount:
        dropped = conn.execute(
            "DELETE FROM transaction_splits WHERE transaction_id = ?",
            (was["id"],)).rowcount
        if dropped:
            log.info("A bank revision changed a split transaction's amount; "
                     "its split was removed")

    revised = conn.execute(
        "UPDATE transactions SET txn_date = ?, amount_cents = ?, merchant = ? "
        "WHERE id = ?",
        (txn_date, amount, _merchant_of(txn), was["id"])).rowcount

    if touched_the_rule:
        found = transfers.find_match(conn, was["id"])
        if found is not None and (partner is None or found == partner):
            transfers.pair(conn, was["id"], found)
    return revised


def _apply_removal(conn, txn):
    row = conn.execute(
        "SELECT id FROM transactions WHERE external_id = ?",
        ("plaid:%s" % txn.get("transaction_id"),)).fetchone()
    if row is None:
        return 0
    transfers.release_pair(conn, row["id"])
    return conn.execute("DELETE FROM transactions WHERE id = ?",
                        (row["id"],)).rowcount


def sync_item(item_id, pages=25):
    summary = {"added": 0, "updated": 0, "removed": 0, "pending": 0,
               "settled": 0, "dropped": 0, "stale": 0,
               "unlinked": 0, "accounts": 0, "liabilities": 0,
               "liabilities_error": None, "liabilities_skipped": False,
               "holdings": 0, "sold": 0, "holdings_error": None,
               "holdings_skipped": False, "error": None}
    try:
        token = token_for(item_id)
        if token is None:
            summary["error"] = "not linked"
            return summary

        with get_conn() as conn:
            row = conn.execute("SELECT cursor, institution FROM plaid_items "
                               "WHERE item_id = ?", (item_id,)).fetchone()
        cursor = row["cursor"] if row else None

        created, _refreshed = link_accounts(
            token, item_id, row["institution"] if row else "")
        summary["accounts"] = created

        with get_conn() as conn:
            kinds = _item_kinds(conn, item_id)

        if kinds & LIABILITY_KINDS:
            try:
                summary["liabilities"] = refresh_liabilities(token, item_id)
            except PlaidError as exc:
                summary["liabilities_error"] = exc.code or str(exc)
                log.info("Plaid liabilities unavailable for one item: %s",
                         exc.code or "no code")
        else:
            summary["liabilities_skipped"] = True

        if kinds & INVESTMENT_KINDS:
            try:
                summary["holdings"], summary["sold"] = refresh_holdings(
                    token, item_id)
            except PlaidError as exc:
                summary["holdings_error"] = exc.code or str(exc)
                log.info("Plaid investments unavailable for one item: %s",
                         exc.code or "no code")
        else:
            summary["holdings_skipped"] = True

        with get_conn() as conn:
            accounts_by_plaid_id = _linked_accounts(conn, item_id)
            category_ids = _category_ids(conn)

        for _ in range(pages):
            added, modified, removed, cursor, more = sync_transactions(token, cursor)

            held = [txn for txn in added if txn.get("pending")]
            posted = [txn for txn in added if not txn.get("pending")]
            summary["pending"] += len(held)

            for txn in posted:
                account_id = accounts_by_plaid_id.get(txn.get("account_id"))
                if account_id is None:
                    summary["unlinked"] += 1
                if add_transaction(
                        txn_date=normalize_date(txn),
                        amount_cents=normalize_amount(txn.get("amount") or 0),
                        merchant=_merchant_of(txn),
                        account_id=account_id,
                        source="plaid",
                        external_id=f"plaid:{txn.get('transaction_id')}",
                        fallback_category_id=category_ids.get(
                            (bank_category_name(txn) or "").casefold()),
                ) is not None:
                    summary["added"] += 1

            with get_conn() as conn:
                conn.execute("BEGIN IMMEDIATE")

                revisions = []
                for txn in modified:
                    (held if txn.get("pending") else revisions).append(txn)

                for txn in revisions:
                    summary["updated"] += _apply_revision(conn, txn)

                for txn in held:
                    pending.record(
                        conn, normalize_pending(txn),
                        accounts_by_plaid_id.get(txn.get("account_id")), item_id)

                for txn in posted + revisions:
                    summary["settled"] += pending.drop(
                        conn, txn.get("pending_transaction_id"))

                for txn in removed:
                    summary["removed"] += _apply_removal(conn, txn)
                    summary["dropped"] += pending.drop(
                        conn, txn.get("transaction_id"))

                conn.execute("UPDATE plaid_items SET cursor = ? WHERE item_id = ?",
                             (cursor, item_id))
            if not more:
                break

        with get_conn() as conn:
            summary["stale"] = pending.sweep_stale(conn, item_id)
            conn.execute("UPDATE plaid_items SET last_sync = datetime('now'), "
                         "last_error = NULL WHERE item_id = ?", (item_id,))
    except PlaidError as exc:
        summary["error"] = str(exc)
        _record_failure(item_id, f"{exc.code}: {exc}" if exc.code else str(exc))
        log.warning("Plaid sync failed for one item: %s", exc.code or "no code")
    except Exception as exc:
        summary["error"] = str(exc) or exc.__class__.__name__
        _record_failure(item_id, f"{exc.__class__.__name__}: {exc}")
        log.warning("Plaid sync failed for one item: %s", exc.__class__.__name__)
    return summary


def sync_all():
    totals = {"added": 0, "updated": 0, "removed": 0, "accounts": 0,
              "liabilities": 0, "holdings": 0, "items": 0, "failed": 0,
              "pending": 0, "settled": 0, "dropped": 0, "stale": 0}
    if not features.enabled("plaid") or not configured():
        return totals
    for item in items():
        result = sync_item(item["item_id"])
        totals["items"] += 1
        if result["error"]:
            totals["failed"] += 1
        for key in ("added", "updated", "removed", "accounts", "liabilities",
                    "holdings", "pending", "settled", "dropped", "stale"):
            totals[key] += result[key]
    return totals
