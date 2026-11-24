-- requires: foreign_keys_off

CREATE TABLE transactions_new (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    txn_date     TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    merchant     TEXT NOT NULL DEFAULT '',
    note         TEXT NOT NULL DEFAULT '',
    legacy_account TEXT NOT NULL DEFAULT '',
    category_id  INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    source       TEXT NOT NULL DEFAULT 'manual'
                      CHECK (source IN ('manual', 'email', 'recurring', 'csv',
                                        'plaid')),
    external_id  TEXT UNIQUE,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    account_id   INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
    transfer_pair_id INTEGER,
    transfer_declined INTEGER NOT NULL DEFAULT 0,
    reviewed     INTEGER NOT NULL DEFAULT 0
);

INSERT INTO transactions_new
    (id, txn_date, amount_cents, merchant, note, legacy_account, category_id,
     source, external_id, created_at, account_id, transfer_pair_id,
     transfer_declined, reviewed)
SELECT id, txn_date, amount_cents, merchant, note, legacy_account, category_id,
       source, external_id, created_at, account_id, transfer_pair_id,
       transfer_declined, reviewed
  FROM transactions;

DELETE FROM sqlite_sequence WHERE name = 'transactions_new';
INSERT INTO sqlite_sequence (name, seq)
    SELECT 'transactions_new', seq FROM sqlite_sequence WHERE name = 'transactions';

DROP VIEW category_amounts;

DROP TABLE transactions;
ALTER TABLE transactions_new RENAME TO transactions;

CREATE INDEX idx_txn_date ON transactions(txn_date);
CREATE INDEX idx_txn_category ON transactions(category_id);
CREATE INDEX idx_txn_account ON transactions(account_id);
CREATE INDEX idx_txn_transfer_pair ON transactions(transfer_pair_id);
CREATE INDEX idx_txn_amount ON transactions(amount_cents);
CREATE INDEX idx_txn_unreviewed ON transactions(txn_date) WHERE reviewed = 0;

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
