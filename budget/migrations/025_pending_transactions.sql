CREATE TABLE pending_transactions (
    plaid_txn_id TEXT PRIMARY KEY,

    account_id   INTEGER REFERENCES accounts(id) ON DELETE SET NULL,

    item_id      TEXT NOT NULL DEFAULT '',

    txn_date     TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    merchant     TEXT NOT NULL DEFAULT '',

    first_seen   TEXT NOT NULL DEFAULT (datetime('now')),
    refreshed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_pending_date ON pending_transactions(txn_date DESC);

CREATE INDEX idx_pending_item ON pending_transactions(item_id);
