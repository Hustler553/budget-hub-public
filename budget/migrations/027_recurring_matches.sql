CREATE TABLE recurring_matches (
    recurring_id   INTEGER NOT NULL REFERENCES recurring(id) ON DELETE CASCADE,
    due_date       TEXT    NOT NULL,
    transaction_id INTEGER NOT NULL UNIQUE
                           REFERENCES transactions(id) ON DELETE CASCADE,
    matched_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (recurring_id, due_date)
);
