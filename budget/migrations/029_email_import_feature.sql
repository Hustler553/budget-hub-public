INSERT INTO settings (key, value)
SELECT 'feature.email_import', '1'
 WHERE EXISTS (SELECT 1 FROM settings
                WHERE (key = 'email_enabled' AND value = '1')
                   OR (key = 'email_address' AND value <> ''))
ON CONFLICT (key) DO NOTHING;
