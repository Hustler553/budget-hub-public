ALTER TABLE merchant_rules ADD COLUMN amount_min_cents INTEGER;
ALTER TABLE merchant_rules ADD COLUMN amount_max_cents INTEGER;
ALTER TABLE merchant_rules ADD COLUMN add_tags TEXT NOT NULL DEFAULT '';
ALTER TABLE merchant_rules ADD COLUMN priority INTEGER NOT NULL DEFAULT 0;
