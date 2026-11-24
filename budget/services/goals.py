from datetime import date, timedelta

from budget.db import get_conn
from budget.utils import parse_iso_date

MAX_NAME = 60

PACE_DAYS = 30
DAYS_PER_MONTH = 30.44


class GoalRefused(ValueError):
    pass


def create(name, target_cents, account_id, target_date=None,
           count_existing=True):
    name = " ".join((name or "").split())[:MAX_NAME]
    if not name:
        raise GoalRefused("Give the goal a name.")
    if not target_cents or target_cents <= 0:
        raise GoalRefused("A goal needs a target amount above zero.")
    with get_conn() as conn:
        account = conn.execute(
            "SELECT id, balance_cents, is_asset FROM accounts "
            "WHERE id = ? AND archived = 0", (account_id,)).fetchone()
        if account is None:
            raise GoalRefused("Choose the account this goal is saved in.")
        if not account["is_asset"]:
            raise GoalRefused("A goal has to be saved in an account you own, "
                              "not one you owe on.")
        start = 0 if count_existing else max(account["balance_cents"] or 0, 0)
        return conn.execute(
            "INSERT INTO goals (name, target_cents, target_date, account_id, "
            "start_cents) VALUES (?, ?, ?, ?, ?)",
            (name, target_cents, str(target_date) if target_date else None,
             account_id, start)).lastrowid


def delete(goal_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM goals WHERE id = ?", (goal_id,))


def _balance_on(conn, account_id, day):
    row = conn.execute(
        "SELECT balance_cents FROM balance_snapshots "
        "WHERE account_id = ? AND taken_on <= ? ORDER BY taken_on DESC LIMIT 1",
        (account_id, str(day))).fetchone()
    return row["balance_cents"] if row else None


def progress(today=None):
    today = today or date.today()
    out = []
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT g.*, a.name AS account_name, a.balance_cents AS balance,
                      a.archived AS account_archived
                 FROM goals g LEFT JOIN accounts a ON a.id = g.account_id
                ORDER BY g.id""").fetchall()
        for row in rows:
            goal = dict(row)
            linked = row["account_id"] is not None
            balance = row["balance"] or 0
            saved = max(balance - row["start_cents"], 0) if linked else 0
            remaining = max(row["target_cents"] - saved, 0)
            goal.update(
                linked=linked, saved=saved, remaining=remaining,
                percent=min(round(100 * saved / row["target_cents"]), 100),
                reached=saved >= row["target_cents"],
                months_left=None, needed_monthly=None, recent_monthly=None,
                on_track=None)

            due = parse_iso_date(row["target_date"]) if row["target_date"] else None
            if due is not None:
                days = (due - today).days
                goal["months_left"] = max(days / DAYS_PER_MONTH, 0)
                if remaining and days > 0:
                    goal["needed_monthly"] = round(
                        remaining / max(days / DAYS_PER_MONTH, 1))

            if linked:
                before = _balance_on(conn, row["account_id"],
                                     today - timedelta(days=PACE_DAYS))
                if before is not None:
                    goal["recent_monthly"] = round(
                        (balance - before) * DAYS_PER_MONTH / PACE_DAYS)

            if goal["reached"]:
                goal["on_track"] = True
            elif due is not None and (due - today).days <= 0:
                goal["on_track"] = False
            elif (goal["needed_monthly"] is not None
                  and goal["recent_monthly"] is not None):
                goal["on_track"] = goal["recent_monthly"] >= goal["needed_monthly"]
            out.append(goal)
    return out
