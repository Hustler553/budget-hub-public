STALE_DAYS = 14


def record(conn, txn, account_id, item_id):
    conn.execute(
        """INSERT INTO pending_transactions
               (plaid_txn_id, account_id, item_id, txn_date, amount_cents, merchant)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT (plaid_txn_id) DO UPDATE SET
               account_id   = excluded.account_id,
               item_id      = excluded.item_id,
               txn_date     = excluded.txn_date,
               amount_cents = excluded.amount_cents,
               merchant     = excluded.merchant,
               refreshed_at = datetime('now')""",
        (txn["plaid_txn_id"], account_id, item_id, txn["txn_date"],
         txn["amount_cents"], txn["merchant"]),
    )


def drop(conn, plaid_txn_id):
    if not plaid_txn_id:
        return 0
    return conn.execute(
        "DELETE FROM pending_transactions WHERE plaid_txn_id = ?",
        (plaid_txn_id,)).rowcount


def drop_for_item(conn, item_id):
    return conn.execute(
        "DELETE FROM pending_transactions WHERE item_id = ?", (item_id,)).rowcount


def sweep_stale(conn, item_id=None, older_than_days=STALE_DAYS):
    sql = ("DELETE FROM pending_transactions "
           "WHERE first_seen < datetime('now', ?)")
    params = ["-%d days" % int(older_than_days)]
    if item_id is not None:
        sql += " AND item_id = ?"
        params.append(item_id)
    return conn.execute(sql, params).rowcount


def for_month(conn, start, end):
    return conn.execute(
        """SELECT p.*, a.name AS account_name
           FROM pending_transactions p
           LEFT JOIN accounts a ON a.id = p.account_id
           WHERE p.txn_date BETWEEN ? AND ?
           ORDER BY p.txn_date DESC, p.plaid_txn_id DESC""",
        (start, end)).fetchall()


def month_spending_cents(conn, start, end):
    return -conn.execute(
        """SELECT COALESCE(SUM(amount_cents), 0)
           FROM pending_transactions
           WHERE txn_date BETWEEN ? AND ? AND amount_cents < 0""",
        (start, end)).fetchone()[0]
