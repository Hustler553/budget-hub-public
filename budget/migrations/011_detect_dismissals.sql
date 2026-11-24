CREATE TABLE recurring_dismissed (
    merchant     TEXT PRIMARY KEY COLLATE NOCASE,
    dismissed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
