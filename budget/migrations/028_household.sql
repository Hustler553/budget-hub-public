CREATE TABLE household_members (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    password_hash   TEXT    NOT NULL,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until    TEXT    NOT NULL DEFAULT '',
    epoch           INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    last_login_at   TEXT
);

ALTER TABLE device_tokens
    ADD COLUMN member_id INTEGER REFERENCES household_members(id) ON DELETE CASCADE;
