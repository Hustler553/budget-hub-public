CREATE TABLE accounts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    kind            TEXT NOT NULL DEFAULT 'other' CHECK (kind IN (
                        'checking', 'savings', 'credit', 'loan',
                        'investment', 'property', 'vehicle', 'cash', 'other')),
    mask            TEXT NOT NULL DEFAULT '',
    institution     TEXT NOT NULL DEFAULT '',
    is_asset        INTEGER NOT NULL DEFAULT 1 CHECK (is_asset IN (0, 1)),
    balance_cents   INTEGER NOT NULL DEFAULT 0,
    balance_as_of   TEXT,
    archived        INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1)),
    sort_order      INTEGER NOT NULL DEFAULT 0,
    external_source TEXT NOT NULL DEFAULT 'manual',
    external_id     TEXT
);

CREATE INDEX idx_accounts_archived ON accounts(archived, sort_order, name);

CREATE TABLE balance_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id    INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    taken_on      TEXT NOT NULL,
    balance_cents INTEGER NOT NULL,
    UNIQUE(account_id, taken_on)
);

CREATE INDEX idx_snapshots_date ON balance_snapshots(taken_on);

ALTER TABLE transactions RENAME COLUMN account TO legacy_account;

ALTER TABLE transactions
    ADD COLUMN account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL;

CREATE INDEX idx_txn_account ON transactions(account_id);

INSERT INTO accounts (name)
SELECT DISTINCT legacy_account
  FROM transactions
 WHERE legacy_account <> '';

UPDATE transactions
   SET account_id = (SELECT a.id FROM accounts a WHERE a.name = legacy_account)
 WHERE legacy_account <> '';

UPDATE accounts
   SET mask = substr(name, -4)
 WHERE name GLOB '*...[0-9][0-9][0-9][0-9]';

UPDATE accounts
   SET mask = ''
 WHERE mask <> ''
   AND mask IN (SELECT mask FROM accounts WHERE mask <> ''
                 GROUP BY mask HAVING COUNT(*) > 1);
