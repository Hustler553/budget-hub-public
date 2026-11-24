CREATE TABLE plaid_items (
    item_id      TEXT PRIMARY KEY,
    access_token BLOB NOT NULL,
    institution  TEXT NOT NULL DEFAULT '',
    cursor       TEXT,
    linked_on    TEXT NOT NULL DEFAULT (date('now')),
    last_sync    TEXT,
    last_error   TEXT
);

CREATE UNIQUE INDEX idx_accounts_external
    ON accounts(external_source, external_id)
 WHERE external_id IS NOT NULL;
