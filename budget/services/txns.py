import logging
import re
import sqlite3

from budget.db import get_conn
from budget.services import transfers

log = logging.getLogger(__name__)


def normalize_merchant(merchant):
    cleaned = re.sub(r"#\s*\d+", "", merchant)
    cleaned = re.sub(r"\b\d{3,}\b", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -*.,")
    return cleaned if len(cleaned) >= 3 else merchant.strip()


def valid_category_id(conn, category_id):
    if category_id is None:
        return None
    row = conn.execute(
        "SELECT 1 FROM categories WHERE id = ?", (category_id,)
    ).fetchone()
    return category_id if row else None


def matching_rule(conn, merchant, amount_cents=None):
    if not merchant:
        return None
    merchant_upper = merchant.upper()
    rules = conn.execute(
        """SELECT id, match_text, category_id, add_tags,
                  amount_min_cents, amount_max_cents
             FROM merchant_rules
            ORDER BY priority DESC, LENGTH(match_text) DESC, id"""
    ).fetchall()
    for rule in rules:
        if rule["match_text"].upper() not in merchant_upper:
            continue
        low, high = rule["amount_min_cents"], rule["amount_max_cents"]
        if low is None and high is None:
            return rule
        if amount_cents is None:
            continue
        size = abs(amount_cents)
        if low is not None and size < low:
            continue
        if high is not None and size > high:
            continue
        return rule
    return None


def find_category_for_merchant(conn, merchant, amount_cents=None):
    rule = matching_rule(conn, merchant, amount_cents)
    return rule["category_id"] if rule else None


def add_transaction(txn_date, amount_cents, merchant="", note="", account_id=None,
                    category_id=None, source="manual", external_id=None,
                    fallback_category_id=None):
    with get_conn() as conn:
        if external_id is not None:
            exists = conn.execute(
                "SELECT 1 FROM transactions WHERE external_id = ?", (external_id,)
            ).fetchone()
            if exists:
                return None

        category_id = valid_category_id(conn, category_id)
        rule = None
        if category_id is None:
            rule = matching_rule(conn, merchant, amount_cents)
            if rule is not None:
                category_id = rule["category_id"]
            else:
                category_id = valid_category_id(conn, fallback_category_id)

        try:
            cur = conn.execute(
                "INSERT INTO transactions "
                "(txn_date, amount_cents, merchant, note, account_id, category_id, "
                "source, external_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (str(txn_date), amount_cents, merchant.strip(), note.strip(),
                 account_id, category_id, source, external_id),
            )
        except sqlite3.IntegrityError as exc:
            if exc.sqlite_errorname == "SQLITE_CONSTRAINT_UNIQUE":
                return None
            raise
        txn_id = cur.lastrowid

    if rule is not None and rule["add_tags"]:
        from budget.services import tags as tags_service
        try:
            tags_service.set_tags(txn_id,
                                  tags_service.tags_for(txn_id)
                                  + tags_service.parse_names(rule["add_tags"]))
        except sqlite3.Error as exc:
            log.warning("Could not tag transaction %s from a rule: %s", txn_id, exc)

    if source in ("plaid", "email", "csv"):
        from budget.services import recurring as recurring_service
        try:
            recurring_service.absorb_into(txn_id)
        except sqlite3.Error as exc:
            log.warning("Could not reconcile transaction %s with recurring "
                        "items: %s", txn_id, exc)

    try:
        transfers.match_new(txn_id)
    except sqlite3.Error as exc:
        log.warning("Could not look for a transfer to pair with transaction %s: %s",
                    txn_id, exc)
    return txn_id


def add_merchant_rule(match_text, category_id, recategorize_existing=True,
                      amount_min_cents=None, amount_max_cents=None,
                      add_tags="", priority=0):
    match_text = match_text.strip()
    if not match_text or category_id is None:
        return False
    if (amount_min_cents is not None and amount_max_cents is not None
            and amount_min_cents > amount_max_cents):
        amount_min_cents, amount_max_cents = amount_max_cents, amount_min_cents
    with get_conn() as conn:
        if valid_category_id(conn, category_id) is None:
            return False
        existing = conn.execute(
            """SELECT id FROM merchant_rules
                WHERE match_text = ? COLLATE NOCASE
                  AND amount_min_cents IS ? AND amount_max_cents IS ?""",
            (match_text, amount_min_cents, amount_max_cents),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE merchant_rules SET category_id = ?, add_tags = ?, "
                "priority = ? WHERE id = ?",
                (category_id, add_tags, priority, existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO merchant_rules (match_text, category_id, "
                "amount_min_cents, amount_max_cents, add_tags, priority) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (match_text, category_id, amount_min_cents, amount_max_cents,
                 add_tags, priority),
            )
        if recategorize_existing:
            escaped = (match_text.replace("\\", "\\\\")
                       .replace("%", "\\%").replace("_", "\\_"))
            conn.execute(
                "UPDATE transactions SET category_id = ? "
                "WHERE category_id IS NULL AND merchant LIKE ? ESCAPE '\\' "
                "  AND (? IS NULL OR ABS(amount_cents) >= ?) "
                "  AND (? IS NULL OR ABS(amount_cents) <= ?) "
                "  AND NOT EXISTS (SELECT 1 FROM transaction_splits s "
                "                   WHERE s.transaction_id = transactions.id)",
                (category_id, f"%{escaped}%",
                 amount_min_cents, amount_min_cents,
                 amount_max_cents, amount_max_cents),
            )
    return True
