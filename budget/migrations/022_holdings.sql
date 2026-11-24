CREATE TABLE securities (
    security_id  TEXT PRIMARY KEY,
    ticker       TEXT NOT NULL DEFAULT '',
    name         TEXT NOT NULL DEFAULT '',
    security_type TEXT NOT NULL DEFAULT '',
    currency     TEXT NOT NULL DEFAULT '',
    close_price_e8    INTEGER,
    close_price_as_of TEXT
);

CREATE TABLE holdings (
    account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    security_id TEXT NOT NULL REFERENCES securities(security_id) ON DELETE CASCADE,

    quantity_e8 INTEGER NOT NULL,
    price_e8    INTEGER,
    price_as_of TEXT,
    value_cents INTEGER,
    cost_basis_cents INTEGER,

    refreshed_at TEXT NOT NULL DEFAULT (datetime('now')),

    PRIMARY KEY (account_id, security_id)
);

CREATE INDEX idx_holdings_account ON holdings(account_id, value_cents DESC);
