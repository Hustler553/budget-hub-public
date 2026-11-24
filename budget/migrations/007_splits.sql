CREATE TABLE transaction_splits (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    category_id    INTEGER NOT NULL REFERENCES categories(id) ON DELETE RESTRICT,
    amount_cents   INTEGER NOT NULL,
    note           TEXT NOT NULL DEFAULT ''
);

CREATE INDEX idx_splits_txn ON transaction_splits(transaction_id);
CREATE INDEX idx_splits_category ON transaction_splits(category_id);

CREATE VIEW category_amounts AS
    SELECT t.id              AS transaction_id,
           t.txn_date        AS txn_date,
           t.category_id     AS category_id,
           t.amount_cents    AS amount_cents,
           t.amount_cents    AS txn_amount_cents,
           t.merchant        AS merchant,
           t.note            AS note,
           t.account_id      AS account_id,
           t.transfer_pair_id AS transfer_pair_id,
           t.source          AS source,
           0                 AS is_split
      FROM transactions t
     WHERE NOT EXISTS (SELECT 1 FROM transaction_splits s
                        WHERE s.transaction_id = t.id)
    UNION ALL
    SELECT t.id, t.txn_date, s.category_id, s.amount_cents, t.amount_cents,
           t.merchant, s.note, t.account_id, t.transfer_pair_id, t.source, 1
      FROM transactions t
      JOIN transaction_splits s ON s.transaction_id = t.id;
