ALTER TABLE transactions
    ADD COLUMN reviewed INTEGER NOT NULL DEFAULT 0;

CREATE INDEX idx_txn_unreviewed ON transactions(txn_date) WHERE reviewed = 0;
