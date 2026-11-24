-- requires: foreign_keys_off

CREATE TABLE category_budgets (
    category_id          INTEGER NOT NULL
                              REFERENCES categories(id) ON DELETE CASCADE,
    effective_from       TEXT NOT NULL CHECK (effective_from LIKE '____-__'),
    budget_kind          TEXT NOT NULL DEFAULT 'fixed'
                              CHECK (budget_kind IN ('fixed', 'percent', 'annual')),
    monthly_budget_cents INTEGER NOT NULL DEFAULT 0,
    budget_percent_bp    INTEGER NOT NULL DEFAULT 0,
    annual_cents         INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (category_id, effective_from)
);

INSERT INTO category_budgets
    (category_id, effective_from, budget_kind, monthly_budget_cents,
     budget_percent_bp, annual_cents)
SELECT id, '0000-01', budget_kind, monthly_budget_cents,
       budget_percent_bp, annual_cents
  FROM categories;

CREATE TABLE categories_new (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    name                 TEXT NOT NULL UNIQUE,
    kind                 TEXT NOT NULL DEFAULT 'expense'
                              CHECK (kind IN ('expense', 'income', 'transfer')),
    archived             INTEGER NOT NULL DEFAULT 0,
    rollover_enabled     INTEGER NOT NULL DEFAULT 0,
    group_id             INTEGER REFERENCES category_groups(id) ON DELETE SET NULL,
    goal_cents           INTEGER NOT NULL DEFAULT 0,
    goal_date            TEXT
);

INSERT INTO categories_new
    (id, name, kind, archived, rollover_enabled, group_id, goal_cents, goal_date)
SELECT id, name, kind, archived, rollover_enabled, group_id, goal_cents, goal_date
  FROM categories;

DELETE FROM sqlite_sequence WHERE name = 'categories_new';
INSERT INTO sqlite_sequence (name, seq)
    SELECT 'categories_new', seq FROM sqlite_sequence WHERE name = 'categories';

DROP TABLE categories;
ALTER TABLE categories_new RENAME TO categories;

CREATE INDEX idx_categories_group ON categories(group_id);
