CREATE TABLE category_groups (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL COLLATE NOCASE UNIQUE,
    sort_order INTEGER NOT NULL DEFAULT 0
);

ALTER TABLE categories
    ADD COLUMN group_id INTEGER REFERENCES category_groups(id) ON DELETE SET NULL;

CREATE INDEX idx_categories_group ON categories(group_id);
