CREATE TABLE alert_log (
    alert_key TEXT PRIMARY KEY,
    kind      TEXT NOT NULL,
    sent_on   TEXT NOT NULL DEFAULT (date('now'))
);

CREATE INDEX idx_alert_log_sent ON alert_log(sent_on);
