import argparse
import random
import sys
from datetime import date, timedelta

from budget import config, db
from budget.db import get_conn
from budget.services import budgets
from budget.utils import month_of, shift_month
from budget.services.accounts import ASSET_KINDS

MONTHS_OF_HISTORY = 6
RNG_SEED = 20260725
DEMO_MARKER = "demo_instance"

ACCOUNT_CARD = "Visa ...4021"
ACCOUNT_BANK = "Checking ...8890"
ACCOUNT_CASH = "Cash"

DEMO_ACCOUNTS = [
    (ACCOUNT_BANK, "checking", "8890", "Lakeside Credit Union", 318450),
    (ACCOUNT_CARD, "credit", "4021", "Lakeside Credit Union", -64217),
    (ACCOUNT_CASH, "cash", "", "", 14000),
]

CASH_MERCHANTS = {"The Corner Diner"}

PAYCHECK_MERCHANT = "Northwind Logistics"
PAYCHECK_CENTS = 214300

FIXED_MONTHLY = [
    ("Riverbend Apartments", "Housing / Rent", 145000, 1),
    ("City Power & Water", "Utilities", 12800, 8),
    ("Netflix", "Subscriptions", 1799, 12),
    ("Spotify", "Subscriptions", 1199, 19),
    ("Planet Fitness", "Health / Fitness", 2499, 5),
]

VARIABLE_MONTHLY = [
    (["Trader Joe's", "Kroger", "Whole Foods"], "Groceries", (6, 10), (2200, 9500)),
    (["Chipotle", "Thai Garden", "The Corner Diner"], "Dining Out", (4, 9), (1100, 4800)),
    (["Shell", "Chevron", "Costco Gas"], "Transportation / Gas", (2, 4), (3200, 7400)),
    (["Amazon", "Target", "REI"], "Shopping", (2, 5), (1500, 12000)),
    (["AMC Theatres", "Steam", "Barnes & Noble"], "Entertainment", (1, 4), (900, 6000)),
]

MONTHLY_BUDGETS = {
    "Housing / Rent": 145000,
    "Utilities": 15000,
    "Groceries": 65000,
    "Dining Out": 30000,
    "Transportation / Gas": 22000,
    "Subscriptions": 4000,
    "Health / Fitness": 3500,
    "Entertainment": 15000,
    "Shopping": 25000,
}


def _month_starts(today, months):
    starts = []
    year, month = today.year, today.month
    for _ in range(months):
        starts.append(date(year, month, 1))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(starts))


DEMO_GROUPS = [
    ("Essentials", 1, ["Housing / Rent", "Utilities", "Groceries",
                       "Transportation / Gas"]),
    ("Living", 2, ["Dining Out", "Entertainment", "Shopping", "Subscriptions",
                   "Health / Fitness"]),
    ("Now and then", 3, ["Travel", "Gifts", "Miscellaneous"]),
]

DEMO_GOALS = [
    ("Travel", 15000, 200000, 8),
    ("Gifts", 5000, 60000, 2),
]

DEMO_BUDGET_CHANGES = [
    ("Groceries", 2, 55000),
    ("Subscriptions", 1, 5500),
]

DEMO_RECURRING = [
    ("Riverbend Apartments", -145000, "Housing / Rent", 1),
    ("City Power & Water", -12800, "Utilities", 8),
    ("Northstar Fiber", -6500, "Utilities", 12),
    ("Spotify", -1199, "Subscriptions", 19),
    ("Lumen Fitness", -2499, "Health / Fitness", 22),
]

PRICE_RISE = {"Netflix": (1799, 1999)}
PRICE_RISE_AFTER_MONTHS = 3


def _fixed_price(merchant, cents, month_start, rise_from):
    rise = PRICE_RISE.get(merchant)
    if rise is None:
        return cents
    was, now = rise
    if cents != was:
        return cents
    return now if month_start >= rise_from else was


def generate_transactions(today, months=MONTHS_OF_HISTORY, rng=None):
    rng = rng or random.Random(RNG_SEED)
    rows = []

    month_starts = _month_starts(today, months)
    rise_from = month_starts[min(PRICE_RISE_AFTER_MONTHS, len(month_starts) - 1)]

    def add(when, cents, merchant, category, account_name):
        if when <= today:
            rows.append({
                "txn_date": when.isoformat(),
                "amount_cents": cents,
                "merchant": merchant,
                "category": category,
                "account_name": (ACCOUNT_CASH if merchant in CASH_MERCHANTS
                                 else account_name),
            })

    for start in month_starts:
        for merchant, category, cents, day in FIXED_MONTHLY:
            add(start.replace(day=day),
                -_fixed_price(merchant, cents, start, rise_from),
                merchant, category, ACCOUNT_CARD)

        for merchants, category, count_range, cents_range in VARIABLE_MONTHLY:
            for _ in range(rng.randint(*count_range)):
                add(start.replace(day=rng.randint(1, 28)),
                    -rng.randint(*cents_range),
                    rng.choice(merchants), category, ACCOUNT_CARD)

        for day in (1, 15):
            add(start.replace(day=day), PAYCHECK_CENTS,
                PAYCHECK_MERCHANT, "Paycheck", ACCOUNT_BANK)

    rows.sort(key=lambda r: r["txn_date"])
    return rows


def _demo_settings(today):
    return {
        DEMO_MARKER: "1",
        "email_enabled": "0",
        "pay_frequency": "semimonthly",
        "pay_amount_cents": str(PAYCHECK_CENTS),
        "pay_anchor_date": date(today.year, today.month, 1).isoformat(),
        "theme_palette": "cobalt",
        "theme_mode": "light",
    }


def _next_occurrence(today, day_of_month):
    if day_of_month > today.day:
        return date(today.year, today.month, day_of_month)
    year = today.year + (today.month == 12)
    month = today.month % 12 + 1
    return date(year, month, day_of_month)


def snapshot_history(rows, account_ids, demo_accounts, today):
    per_day = {}
    for row in rows:
        key = (account_ids[row["account_name"]], row["txn_date"])
        per_day[key] = per_day.get(key, 0) + row["amount_cents"]

    earliest = min((r["txn_date"] for r in rows), default=today.isoformat())
    start = date.fromisoformat(earliest)

    out = []
    for name, _kind, _mask, _inst, stated in demo_accounts:
        account_id = account_ids[name]
        balance = stated
        day = today
        while day >= start:
            out.append((account_id, day.isoformat(), balance))
            balance -= per_day.get((account_id, day.isoformat()), 0)
            day -= timedelta(days=1)
    return out


def seed(today=None, months=MONTHS_OF_HISTORY, rng_seed=RNG_SEED):
    today = today or date.today()
    rows = generate_transactions(today, months, random.Random(rng_seed))

    with get_conn() as conn:
        conn.execute("DELETE FROM transactions")
        conn.execute("DELETE FROM accounts")
        conn.execute("DELETE FROM recurring")
        conn.execute("DELETE FROM merchant_rules")
        conn.execute("DELETE FROM email_log")
        conn.execute("DELETE FROM pending_transactions")
        conn.execute("DELETE FROM category_budgets")
        conn.execute("UPDATE categories SET rollover_enabled = 0, "
                     "goal_cents = 0, goal_date = NULL")

        ids = {row["name"]: row["id"]
               for row in conn.execute("SELECT id, name FROM categories")}
        account_ids = {}
        for sort_order, (name, kind, mask, institution, balance) in enumerate(DEMO_ACCOUNTS):
            cur = conn.execute(
                "INSERT INTO accounts (name, kind, mask, institution, is_asset, "
                "balance_cents, balance_as_of, sort_order) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (name, kind, mask, institution, 1 if kind in ASSET_KINDS else 0,
                 balance, today.isoformat(), sort_order),
            )
            account_ids[name] = cur.lastrowid

        for category, monthly, target, months_out in DEMO_GOALS:
            year, month = today.year, today.month + months_out
            year, month = year + (month - 1) // 12, (month - 1) % 12 + 1
            conn.execute(
                """UPDATE categories
                      SET rollover_enabled = 1, goal_cents = ?, goal_date = ?
                    WHERE name = ?""",
                (target, f"{year:04d}-{month:02d}", category))
            conn.execute(
                """INSERT INTO category_budgets (category_id, effective_from,
                       budget_kind, monthly_budget_cents)
                   SELECT id, ?, 'fixed', ? FROM categories WHERE name = ?
                   ON CONFLICT (category_id, effective_from) DO UPDATE SET
                       monthly_budget_cents = excluded.monthly_budget_cents""",
                (budgets.BEFORE_ANY_MONTH, monthly, category))

        conn.execute("DELETE FROM category_groups")
        for group_name, order, members in DEMO_GROUPS:
            group_id = conn.execute(
                "INSERT INTO category_groups (name, sort_order) VALUES (?, ?)",
                (group_name, order)).lastrowid
            for category in members:
                conn.execute("UPDATE categories SET group_id = ? WHERE name = ?",
                             (group_id, category))

        recurring_ids = {}
        for name, cents, category, day in DEMO_RECURRING:
            cur = conn.execute(
                """INSERT INTO recurring (name, amount_cents, category_id, frequency,
                                          next_date, anchor_day, active)
                   VALUES (?, ?, ?, 'monthly', ?, ?, 1)""",
                (name, cents, ids.get(category),
                 _next_occurrence(today, day).isoformat(), day))
            recurring_ids[name] = cur.lastrowid

        conn.executemany(
            "INSERT INTO transactions "
            "(txn_date, amount_cents, merchant, category_id, account_id, source, "
            " external_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(r["txn_date"], r["amount_cents"], r["merchant"],
              ids.get(r["category"]), account_ids[r["account_name"]],
              "recurring" if r["merchant"] in recurring_ids else "manual",
              (f"recurring:{recurring_ids[r['merchant']]}:{r['txn_date']}"
               if r["merchant"] in recurring_ids else None))
             for r in rows],
        )

        conn.executemany(
            "INSERT INTO balance_snapshots (account_id, taken_on, balance_cents) "
            "VALUES (?, ?, ?)",
            snapshot_history(rows, account_ids, DEMO_ACCOUNTS, today),
        )

        changed = {name: (ago, before) for name, ago, before in DEMO_BUDGET_CHANGES}
        this_month = month_of(today)
        for name, cents in MONTHLY_BUDGETS.items():
            if name not in ids:
                continue
            opening_cents = cents
            if name in changed:
                months_ago, opening_cents = changed[name]
                conn.execute(
                    """INSERT INTO category_budgets (category_id,
                           effective_from, budget_kind, monthly_budget_cents)
                       VALUES (?, ?, 'fixed', ?)
                       ON CONFLICT (category_id, effective_from) DO UPDATE SET
                           monthly_budget_cents = excluded.monthly_budget_cents""",
                    (ids[name], shift_month(this_month, -months_ago), cents))
                conn.execute(
                    "UPDATE categories SET rollover_enabled = 1 WHERE id = ?",
                    (ids[name],))
            conn.execute(
                """INSERT INTO category_budgets (category_id, effective_from,
                       budget_kind, monthly_budget_cents)
                   VALUES (?, ?, 'fixed', ?)
                   ON CONFLICT (category_id, effective_from) DO UPDATE SET
                       monthly_budget_cents = excluded.monthly_budget_cents""",
                (ids[name], budgets.BEFORE_ANY_MONTH, opening_cents))

        for key, value in _demo_settings(today).items():
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    return len(rows)


def is_safe_to_seed():
    with get_conn() as conn:
        marked = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (DEMO_MARKER,)
        ).fetchone()
        if marked:
            return True
        count = conn.execute(
            "SELECT (SELECT COUNT(*) FROM transactions) + (SELECT COUNT(*) FROM accounts)"
        ).fetchone()[0]
    return count == 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Fill this instance's database with fictional demo data."
    )
    parser.add_argument("--force", action="store_true",
                        help="reseed even if this database holds real data")
    args = parser.parse_args(argv)

    db.init_db()
    if not args.force and not is_safe_to_seed():
        print(f"Refusing to seed: {config.DB_PATH} holds transactions or accounts "
              f"and is not marked as a demo.\nRerun with --force if you really mean "
              f"to erase it.", file=sys.stderr)
        return 1

    count = seed()
    print(f"Seeded {count} demo transactions into {config.DB_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
