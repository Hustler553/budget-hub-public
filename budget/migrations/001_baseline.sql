CREATE TABLE categories (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    name                 TEXT NOT NULL UNIQUE,
    kind                 TEXT NOT NULL DEFAULT 'expense' CHECK (kind IN ('expense', 'income')),
    monthly_budget_cents INTEGER NOT NULL DEFAULT 0,
    archived             INTEGER NOT NULL DEFAULT 0,
    budget_kind          TEXT NOT NULL DEFAULT 'fixed' CHECK (budget_kind IN ('fixed', 'percent')),
    budget_percent_bp    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE transactions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    txn_date     TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    merchant     TEXT NOT NULL DEFAULT '',
    note         TEXT NOT NULL DEFAULT '',
    account      TEXT NOT NULL DEFAULT '',
    category_id  INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    source       TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'email', 'recurring', 'csv')),
    external_id  TEXT UNIQUE,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_txn_date ON transactions(txn_date);
CREATE INDEX idx_txn_category ON transactions(category_id);

CREATE TABLE merchant_rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    match_text  TEXT NOT NULL,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE recurring (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    category_id  INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    frequency    TEXT NOT NULL CHECK (frequency IN ('weekly', 'biweekly', 'semimonthly', 'monthly', 'yearly')),
    next_date    TEXT NOT NULL,
    anchor_day   INTEGER NOT NULL DEFAULT 1,
    active       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE email_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    uid          TEXT NOT NULL UNIQUE,
    subject      TEXT NOT NULL DEFAULT '',
    sender       TEXT NOT NULL DEFAULT '',
    received_at  TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL CHECK (status IN ('parsed', 'review', 'ignored', 'dismissed')),
    body_snippet TEXT NOT NULL DEFAULT '',
    amount_cents INTEGER,
    merchant     TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
