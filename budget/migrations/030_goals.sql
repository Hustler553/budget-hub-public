CREATE TABLE goals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL,
    target_cents INTEGER NOT NULL CHECK (target_cents > 0),
    target_date  TEXT,
    account_id   INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
    start_cents  INTEGER NOT NULL DEFAULT 0,
    created_on   TEXT    NOT NULL DEFAULT (date('now'))
);
