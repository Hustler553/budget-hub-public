from budget.db import get_conn

_CANDIDATE = """
    SELECT other.id
      FROM transactions AS other, transactions AS self
     WHERE self.id = ?
       AND other.id != self.id
       AND other.transfer_pair_id IS NULL
       AND self.transfer_pair_id IS NULL
       AND other.transfer_declined = 0
       AND self.transfer_declined = 0
       AND other.amount_cents = -self.amount_cents
       AND self.amount_cents != 0
       AND other.account_id IS NOT NULL
       AND self.account_id IS NOT NULL
       AND other.account_id != self.account_id
       AND ABS(julianday(other.txn_date) - julianday(self.txn_date)) <= 3
     ORDER BY ABS(julianday(other.txn_date) - julianday(self.txn_date)), other.id
     LIMIT 1
"""

_WAITING = """
    SELECT id
      FROM transactions
     WHERE transfer_pair_id IS NULL
       AND transfer_declined = 0
       AND account_id IS NOT NULL
     ORDER BY txn_date, id
"""

_PAIRED = """
    SELECT p.pair_id AS pair_id,
           l.id AS left_id, l.txn_date AS left_date, l.amount_cents AS left_amount_cents,
           l.merchant AS left_merchant, l.note AS left_note,
           l.account_id AS left_account_id, la.name AS left_account_name,
           r.id AS right_id, r.txn_date AS right_date, r.amount_cents AS right_amount_cents,
           r.merchant AS right_merchant, r.note AS right_note,
           r.account_id AS right_account_id, ra.name AS right_account_name
      FROM (SELECT transfer_pair_id AS pair_id, MIN(id) AS left_id, MAX(id) AS right_id,
                   COUNT(*) AS halves, MAX(txn_date) AS newest
              FROM transactions
             WHERE transfer_pair_id IS NOT NULL
             GROUP BY transfer_pair_id) AS p
      JOIN transactions AS l ON l.id = p.left_id
      LEFT JOIN accounts AS la ON la.id = l.account_id
      LEFT JOIN transactions AS r ON r.id = p.right_id AND p.halves > 1
      LEFT JOIN accounts AS ra ON ra.id = r.account_id
     ORDER BY p.newest DESC, p.pair_id DESC
     LIMIT ?
"""


class AlreadyPaired(ValueError):
    pass


def find_match(conn, txn_id):
    try:
        row = conn.execute(_CANDIDATE, (txn_id,)).fetchone()
    except OverflowError:
        return None
    return row[0] if row else None


def pair(conn, left_id, right_id):
    if left_id == right_id:
        raise ValueError("A transaction cannot be paired with itself.")
    try:
        found = {row[0]: row for row in conn.execute(
            "SELECT id, account_id, transfer_pair_id FROM transactions WHERE id IN (?, ?)",
            (left_id, right_id))}
    except OverflowError:
        found = {}
    for txn_id in (left_id, right_id):
        if txn_id not in found:
            raise ValueError(f"There is no transaction with id {txn_id}.")
    left, right = found[left_id], found[right_id]
    if left[2] is not None or right[2] is not None:
        raise AlreadyPaired("One of those transactions is already part of a transfer.")
    if left[1] == right[1]:
        raise ValueError("Those two transactions are in the same account, or neither has an "
                         "account yet, so there is nothing to show they are different ones.")

    pair_id = min(left_id, right_id)
    cur = conn.execute(
        "UPDATE transactions SET transfer_pair_id = ?, transfer_declined = 0"
        " WHERE id IN (?, ?)"
        "   AND (SELECT COUNT(*) FROM transactions"
        "         WHERE id IN (?, ?) AND transfer_pair_id IS NULL) = 2",
        (pair_id, left_id, right_id, left_id, right_id))
    if cur.rowcount != 2:
        raise AlreadyPaired("One of those transactions was paired a moment ago.")
    return pair_id


def _pair_with_best_match(conn, txn_id):
    conn.execute("BEGIN IMMEDIATE")
    match = find_match(conn, txn_id)
    if match is None:
        conn.rollback()
        return None
    return pair(conn, txn_id, match)


def match_new(txn_id):
    with get_conn() as conn:
        if find_match(conn, txn_id) is None:
            return None
        return _pair_with_best_match(conn, txn_id)


def unpair(conn, txn_id):
    try:
        row = conn.execute(
            "SELECT transfer_pair_id FROM transactions WHERE id = ?", (txn_id,)).fetchone()
    except OverflowError:
        return 0
    if row is None or row[0] is None:
        return 0
    cur = conn.execute(
        "UPDATE transactions SET transfer_pair_id = NULL, transfer_declined = 1"
        " WHERE transfer_pair_id = ?", (row[0],))
    return cur.rowcount


def other_half(conn, txn_id):
    try:
        row = conn.execute(
            "SELECT transfer_pair_id FROM transactions WHERE id = ?", (txn_id,)).fetchone()
    except OverflowError:
        return None
    if row is None or row[0] is None:
        return None
    other = conn.execute(
        "SELECT id FROM transactions WHERE transfer_pair_id = ? AND id != ?",
        (row[0], txn_id)).fetchone()
    return other[0] if other else None


def release_pair(conn, txn_id):
    try:
        row = conn.execute(
            "SELECT transfer_pair_id FROM transactions WHERE id = ?", (txn_id,)).fetchone()
    except OverflowError:
        return 0
    if row is None or row[0] is None:
        return 0
    cur = conn.execute(
        "UPDATE transactions SET transfer_pair_id = NULL WHERE transfer_pair_id = ?",
        (row[0],))
    return cur.rowcount


def rescan():
    made = 0
    with get_conn() as conn:
        waiting = [row[0] for row in conn.execute(_WAITING)]
        for txn_id in waiting:
            if find_match(conn, txn_id) is None:
                continue
            if _pair_with_best_match(conn, txn_id) is None:
                continue
            made += 1
            conn.commit()
    return made


def paired_rows(conn, limit=50):
    return conn.execute(_PAIRED, (limit,)).fetchall()
