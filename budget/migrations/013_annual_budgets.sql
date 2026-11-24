-- requires: foreign_keys_off

CREATE TABLE categories_new (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    name                 TEXT NOT NULL UNIQUE,
    kind                 TEXT NOT NULL DEFAULT 'expense'
                              CHECK (kind IN ('expense', 'income', 'transfer')),
    monthly_budget_cents INTEGER NOT NULL DEFAULT 0,
    archived             INTEGER NOT NULL DEFAULT 0,
    budget_kind          TEXT NOT NULL DEFAULT 'fixed'
                              CHECK (budget_kind IN ('fixed', 'percent', 'annual')),
    budget_percent_bp    INTEGER NOT NULL DEFAULT 0,
    rollover_enabled     INTEGER NOT NULL DEFAULT 0,
    group_id             INTEGER REFERENCES category_groups(id) ON DELETE SET NULL,
    annual_cents         INTEGER NOT NULL DEFAULT 0
);

INSERT INTO categories_new
    (id, name, kind, monthly_budget_cents, archived, budget_kind,
     budget_percent_bp, rollover_enabled, group_id)
SELECT id, name, kind, monthly_budget_cents, archived, budget_kind,
       budget_percent_bp, rollover_enabled, group_id
  FROM categories;

DELETE FROM sqlite_sequence WHERE name = 'categories_new';
INSERT INTO sqlite_sequence (name, seq)
    SELECT 'categories_new', seq FROM sqlite_sequence WHERE name = 'categories';

DROP TABLE categories;
ALTER TABLE categories_new RENAME TO categories;

CREATE INDEX idx_categories_group ON categories(group_id);
