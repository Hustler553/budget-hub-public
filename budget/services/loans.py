from datetime import date

from budget.db import get_conn
from budget.services import moneymath

_EDITABLE = ("name", "purpose", "account_id", "kind", "principal_cents",
             "annual_rate_e4", "payment_cents", "escrow_cents", "extra_cents",
             "opened_on", "principal_as_of")

FROM_LENDER = "lender"
FROM_OWNER = "you"

KINDS = ("personal", "car", "mortgage", "student")


def _check_name(name):
    name = (name or "").strip()
    if not name:
        raise ValueError("A loan needs a name.")
    return name


def create(name, principal_cents, annual_rate_e4, payment_cents, **fields):
    fields.setdefault("principal_as_of", date.today().isoformat())
    values = {
        "name": _check_name(name),
        "principal_cents": int(principal_cents),
        "annual_rate_e4": int(annual_rate_e4),
        "payment_cents": int(payment_cents),
    }
    for key, value in fields.items():
        if key not in _EDITABLE:
            raise ValueError(f"{key!r} is not a field of a loan.")
        values[key] = value
    columns = ", ".join(values)
    marks = ", ".join("?" * len(values))
    with get_conn() as conn:
        cur = conn.execute(f"INSERT INTO loans ({columns}) VALUES ({marks})",
                           list(values.values()))
        return cur.lastrowid


def update(loan_id, **fields):
    for key in fields:
        if key not in _EDITABLE:
            raise ValueError(f"{key!r} is not a field of a loan.")
    if "name" in fields:
        fields["name"] = _check_name(fields["name"])
    if "principal_cents" in fields and "principal_as_of" not in fields:
        stored = get(loan_id)
        if stored and stored["principal_cents"] != int(fields["principal_cents"]):
            fields["principal_as_of"] = date.today().isoformat()
    if not fields:
        return
    assignments = ", ".join(f"{key} = ?" for key in fields)
    with get_conn() as conn:
        conn.execute(f"UPDATE loans SET {assignments} WHERE id = ?",
                     list(fields.values()) + [loan_id])


def delete(loan_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM loans WHERE id = ?", (loan_id,))


def get(loan_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM loans WHERE id = ?",
                           (loan_id,)).fetchone()
    return dict(row) if row else None


_BALANCE_SQL = """
    SELECT l.*, a.name AS account_name,
           CASE
             WHEN al.principal_owed_cents IS NOT NULL
               THEN al.principal_owed_cents
             WHEN a.external_source = 'plaid' AND a.balance_cents IS NOT NULL
               THEN ABS(a.balance_cents)
             ELSE l.principal_cents
           END AS balance_cents,
           CASE
             WHEN al.principal_owed_cents IS NOT NULL
               OR (a.external_source = 'plaid' AND a.balance_cents IS NOT NULL)
               THEN ? ELSE ?
           END AS balance_source,
           CASE
             WHEN al.principal_owed_cents IS NOT NULL
               THEN date(al.refreshed_at)
             WHEN a.external_source = 'plaid' AND a.balance_cents IS NOT NULL
               THEN date(a.balance_as_of)
             ELSE date(l.principal_as_of)
           END AS balance_as_of
      FROM loans l
      LEFT JOIN accounts a ON a.id = l.account_id
      LEFT JOIN account_liabilities al ON al.account_id = l.account_id
"""


def all_loans():
    with get_conn() as conn:
        return [dict(row) for row in conn.execute(
            _BALANCE_SQL + " ORDER BY l.name", (FROM_LENDER, FROM_OWNER))]


def with_balance(loan_id):
    with get_conn() as conn:
        row = conn.execute(_BALANCE_SQL + " WHERE l.id = ?",
                           (FROM_LENDER, FROM_OWNER, loan_id)).fetchone()
    return dict(row) if row else None


def model(principal_cents, annual_rate_e4, payment_cents, extra_cents=0,
          escrow_cents=0, periods=None):
    empty = {
        "impossible": None, "periods": 0, "years": 0.0,
        "total_paid_cents": 0, "total_interest_cents": 0,
        "final_payment_cents": 0, "monthly_outlay_cents": payment_cents
                                                          + extra_cents
                                                          + escrow_cents,
        "schedule": [], "saved": None,
    }
    if principal_cents <= 0:
        return empty

    try:
        if periods is not None:
            rows = moneymath.schedule(principal_cents, annual_rate_e4, periods,
                                      extra_cents=extra_cents)
        else:
            rows = moneymath.schedule(principal_cents, annual_rate_e4,
                                      payment_cents=payment_cents,
                                      extra_cents=extra_cents)
    except moneymath.ImpossibleLoan as exc:
        return {**empty, "impossible": str(exc)}

    summary = moneymath.summarise(rows)

    saved = None
    if extra_cents:
        try:
            comparison = moneymath.compare_extra_payment(
                principal_cents, annual_rate_e4, payment_cents, extra_cents)
            saved = {
                "periods": comparison["periods_saved"],
                "interest_cents": comparison["interest_saved_cents"],
            }
        except moneymath.ImpossibleLoan:
            saved = {"periods": None, "interest_cents": None}

    return {
        "impossible": None,
        "periods": summary["periods"],
        "years": round(summary["periods"] / moneymath.MONTHS_PER_YEAR, 1),
        "total_paid_cents": summary["total_paid_cents"],
        "total_interest_cents": summary["total_interest_cents"],
        "final_payment_cents": summary["final_payment_cents"],
        "monthly_outlay_cents": payment_cents + extra_cents + escrow_cents,
        "schedule": rows,
        "saved": saved,
    }


def model_loan(loan):
    return model(loan.get("balance_cents", loan["principal_cents"]),
                 loan["annual_rate_e4"], loan["payment_cents"],
                 loan["extra_cents"], loan["escrow_cents"])


def from_connected_account(account_id):
    with get_conn() as conn:
        row = conn.execute(
            """SELECT l.apr_bp, l.principal_owed_cents, l.next_payment_cents,
                      l.liability_kind, a.name
                 FROM account_liabilities l JOIN accounts a ON a.id = l.account_id
                WHERE l.account_id = ?""", (account_id,)).fetchone()
    if row is None:
        return None
    return {
        "name": row["name"],
        "kind": ("mortgage" if row["liability_kind"] == "mortgage"
                 else "student" if row["liability_kind"] == "student"
                 else "personal"),
        "principal_cents": row["principal_owed_cents"],
        "annual_rate_e4": moneymath.bp_to_e4(row["apr_bp"]),
        "payment_cents": row["next_payment_cents"],
    }
