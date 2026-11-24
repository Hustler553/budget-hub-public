from datetime import date

from budget.db import get_conn, get_setting, set_setting
from budget.services import transfers
from budget.utils import to_int

ENABLED_KEY = "paycheck_confirm_enabled"
CONFIRMED_KEY = "paycheck_confirmed_through"
RECURRING_ID_KEY = "paycheck_recurring_id"


def confirm_enabled():
    return get_setting(ENABLED_KEY, "0") == "1"


def set_confirm_enabled(enabled):
    set_setting(ENABLED_KEY, "1" if enabled else "0")


def pending_paycheck(today=None):
    if not confirm_enabled():
        return None
    item_id = to_int(get_setting(RECURRING_ID_KEY))
    if item_id is None:
        return None

    today = today or date.today()
    confirmed_through = get_setting(CONFIRMED_KEY, "")
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM transactions
               WHERE external_id LIKE ? AND amount_cents > 0
                 AND txn_date <= ? AND txn_date > ?
               ORDER BY txn_date DESC, id DESC LIMIT 1""",
            (f"recurring:{item_id}:%", str(today), confirmed_through),
        ).fetchone()


def posted_amount(period_start, period_end):
    item_id = to_int(get_setting(RECURRING_ID_KEY))
    if item_id is None:
        return None
    with get_conn() as conn:
        row = conn.execute(
            """SELECT amount_cents FROM transactions
               WHERE (external_id LIKE ?
                      OR id IN (SELECT transaction_id FROM recurring_matches
                                 WHERE recurring_id = ?))
                 AND amount_cents > 0
                 AND txn_date >= ? AND txn_date < ?
               ORDER BY txn_date DESC, id DESC LIMIT 1""",
            (f"recurring:{item_id}:%", item_id, str(period_start),
             str(period_end)),
        ).fetchone()
    return row["amount_cents"] if row else None


def confirm(txn_id, amount_cents):
    if amount_cents <= 0:
        return False
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT txn_date, amount_cents FROM transactions WHERE id = ?",
                           (txn_id,)).fetchone()
        if row is None:
            return False
        if row["amount_cents"] != amount_cents:
            transfers.release_pair(conn, txn_id)
        conn.execute("UPDATE transactions SET amount_cents = ? WHERE id = ?",
                     (amount_cents, txn_id))
    set_setting(CONFIRMED_KEY, row["txn_date"])
    return True
