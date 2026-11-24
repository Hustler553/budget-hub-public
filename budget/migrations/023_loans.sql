CREATE TABLE loans (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT '',

    account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,

    kind TEXT NOT NULL DEFAULT 'personal',

    principal_cents INTEGER NOT NULL,
    annual_rate_e4  INTEGER NOT NULL,

    payment_cents INTEGER NOT NULL,
    escrow_cents  INTEGER NOT NULL DEFAULT 0,
    extra_cents   INTEGER NOT NULL DEFAULT 0,

    opened_on  TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),

    CHECK (principal_cents >= 0),
    CHECK (annual_rate_e4 >= 0),
    CHECK (payment_cents >= 0),
    CHECK (escrow_cents >= 0),
    CHECK (extra_cents >= 0)
);

CREATE UNIQUE INDEX idx_loans_account
    ON loans(account_id) WHERE account_id IS NOT NULL;
