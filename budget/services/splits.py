from budget.db import get_conn


class SplitError(ValueError):
    pass


MIN_PARTS = 2


def set_splits(txn_id, parts):
    parts = [tuple(p) for p in parts]
    if len(parts) < MIN_PARTS:
        raise SplitError(
            f"A split needs at least {MIN_PARTS} parts. To put the whole "
            "transaction in one category, set its category instead.")

    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT amount_cents FROM transactions WHERE id = ?", (txn_id,)).fetchone()
        if row is None:
            raise SplitError("That transaction couldn't be found.")
        total = row["amount_cents"]

        if total == 0:
            raise SplitError("A transaction of zero can't be split.")

        for category_id, amount, *_rest in parts:
            if amount == 0:
                raise SplitError("A split part can't be zero.")
            if (amount > 0) != (total > 0):
                raise SplitError(
                    "Every part of a split goes the same way as the "
                    "transaction: all spending, or all income.")
            if conn.execute("SELECT 1 FROM categories WHERE id = ?",
                            (category_id,)).fetchone() is None:
                raise SplitError("One of those categories couldn't be found.")

        got = sum(amount for _c, amount, *_r in parts)
        if got != total:
            raise SplitError(
                f"The parts add up to {_money(got)}, but the transaction is "
                f"{_money(total)}. They have to match exactly.")

        conn.execute("DELETE FROM transaction_splits WHERE transaction_id = ?", (txn_id,))
        conn.executemany(
            "INSERT INTO transaction_splits (transaction_id, category_id, amount_cents, note) "
            "VALUES (?, ?, ?, ?)",
            [(txn_id, c, amount, (rest[0] if rest else "")) for c, amount, *rest in parts],
        )
        conn.execute("UPDATE transactions SET category_id = NULL WHERE id = ?", (txn_id,))
        return len(parts)


def clear(txn_id):
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM transaction_splits WHERE transaction_id = ?", (txn_id,))
        return cur.rowcount


def splits_for(txn_id):
    with get_conn() as conn:
        return conn.execute(
            """SELECT s.id, s.category_id, s.amount_cents, s.note, c.name AS category_name
                 FROM transaction_splits s
                 JOIN categories c ON c.id = s.category_id
                WHERE s.transaction_id = ?
                ORDER BY s.id""", (txn_id,)).fetchall()


def split_counts(txn_ids):
    ids = list(txn_ids)
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    with get_conn() as conn:
        return {r["transaction_id"]: r["n"] for r in conn.execute(
            f"""SELECT transaction_id, COUNT(*) AS n FROM transaction_splits
                 WHERE transaction_id IN ({marks}) GROUP BY transaction_id""", ids)}


def unbalanced():
    with get_conn() as conn:
        return conn.execute(
            """SELECT t.id, t.txn_date, t.merchant,
                      t.amount_cents, SUM(s.amount_cents) AS split_total
                 FROM transactions t
                 JOIN transaction_splits s ON s.transaction_id = t.id
                GROUP BY t.id
               HAVING SUM(s.amount_cents) != t.amount_cents
                ORDER BY t.txn_date DESC""").fetchall()


def _money(cents):
    return f"{'-' if cents < 0 else ''}${abs(cents) / 100:,.2f}"
