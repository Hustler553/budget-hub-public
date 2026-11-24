-- requires: foreign_keys_off
ALTER TABLE transactions ADD COLUMN transfer_pair_id INTEGER;
ALTER TABLE transactions ADD COLUMN transfer_declined INTEGER NOT NULL DEFAULT 0;

CREATE INDEX idx_txn_transfer_pair ON transactions(transfer_pair_id);

CREATE TABLE categories_new (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    name                 TEXT NOT NULL UNIQUE,
    kind                 TEXT NOT NULL DEFAULT 'expense'
                              CHECK (kind IN ('expense', 'income', 'transfer')),
    monthly_budget_cents INTEGER NOT NULL DEFAULT 0,
    archived             INTEGER NOT NULL DEFAULT 0,
    budget_kind          TEXT NOT NULL DEFAULT 'fixed'
                              CHECK (budget_kind IN ('fixed', 'percent')),
    budget_percent_bp    INTEGER NOT NULL DEFAULT 0
);

INSERT INTO categories_new
    (id, name, kind, monthly_budget_cents, archived, budget_kind, budget_percent_bp)
SELECT id, name, kind, monthly_budget_cents, archived, budget_kind, budget_percent_bp
  FROM categories;

DELETE FROM sqlite_sequence WHERE name = 'categories_new';
INSERT INTO sqlite_sequence (name, seq)
    SELECT 'categories_new', seq FROM sqlite_sequence WHERE name = 'categories';

DROP TABLE categories;
ALTER TABLE categories_new RENAME TO categories;
