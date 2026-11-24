CREATE TABLE account_liabilities (
    account_id   INTEGER PRIMARY KEY
                     REFERENCES accounts(id) ON DELETE CASCADE,
    liability_kind TEXT NOT NULL,

    apr_bp       INTEGER,
    apr_type     TEXT NOT NULL DEFAULT '',

    principal_owed_cents       INTEGER,
    origination_principal_cents INTEGER,
    credit_limit_cents         INTEGER,

    minimum_payment_cents INTEGER,
    next_payment_cents    INTEGER,
    next_payment_due      TEXT,
    last_payment_cents    INTEGER,
    last_payment_on       TEXT,

    maturity_date      TEXT,
    ytd_interest_cents INTEGER,

    past_due_cents INTEGER,
    is_overdue     INTEGER NOT NULL DEFAULT 0 CHECK (is_overdue IN (0, 1)),

    refreshed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_liabilities_due ON account_liabilities(next_payment_due);
