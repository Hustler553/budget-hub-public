import logging
from datetime import date, datetime, timedelta

from budget.db import get_conn, get_setting
from budget.services import budgets, detect, features, notify, recurring
from budget.utils import month_bounds, month_of

log = logging.getLogger(__name__)

DUE_SOON_DAYS = 5

STALLED_AFTER_HOURS = 24

FORGET_AFTER_DAYS = 400


def _already_sent(conn, keys):
    if not keys:
        return set()
    marks = ", ".join("?" * len(keys))
    return {row["alert_key"] for row in conn.execute(
        f"SELECT alert_key FROM alert_log WHERE alert_key IN ({marks})",
        tuple(keys))}


def _bills_due(today):
    horizon = today + timedelta(days=DUE_SOON_DAYS)
    out = []
    for bill in recurring.upcoming_bills(today, horizon):
        when = "today" if bill["date"] == today else bill["date"].isoformat()
        out.append((
            f"bill:{bill['id']}:{bill['date'].isoformat()}",
            f"  {bill['name']} - {_money(bill['amount_cents'])} due {when}",
        ))
    return out


def _price_rises(today):
    out = []
    for change in detect.price_changes(today):
        if change["change_cents"] >= 0:
            continue
        out.append((
            f"price:{change['merchant']}:{change['changed_on'].isoformat()}",
            f"  {change['merchant']} - {_money(change['was_cents'])} to "
            f"{_money(change['now_cents'])} since "
            f"{change['changed_on'].isoformat()}",
        ))
    return out


def _over_budget(conn, today):
    month = month_of(today)
    start, end = month_bounds(month)
    out = []
    for row in budgets.category_rows(conn, start, end):
        if row["status"] != "over":
            continue
        over_by = row["spent_cents"] - row["available_cents"]
        out.append((
            f"over:{row['id']}:{month}",
            f"  {row['name']} - {_money(row['spent_cents'])} spent of "
            f"{_money(row['available_cents'])}, over by {_money(over_by)}",
        ))
    return out


def _ingestion_stalled(now):
    if get_setting("email_enabled") != "1":
        return []
    last = get_setting("email_last_poll").strip()
    if not last:
        return []
    try:
        when = datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return []
    hours = (now - when).total_seconds() / 3600
    if hours < STALLED_AFTER_HOURS:
        return []
    line = (f"  Email ingestion last succeeded {last} "
            f"({int(hours)} hours ago). New transactions are not arriving.")
    error = get_setting("email_last_error").strip()
    if error:
        line += f"\n    Last error: {error}"
    return [(f"stalled:{last}", line)]


def _money(cents):
    return f"-${abs(cents) / 100:,.2f}" if cents < 0 else f"${cents / 100:,.2f}"


SECTIONS = (
    ("stalled", "Needs looking at"),
    ("bill", "Due in the next few days"),
    ("price", "A subscription went up"),
    ("over", "Over budget this month"),
)


def pending(today=None, now=None):
    today = today or date.today()
    now = now or datetime.now()
    found = {
        "bill": _bills_due(today),
        "price": _price_rises(today),
        "stalled": _ingestion_stalled(now),
    }
    with get_conn() as conn:
        found["over"] = _over_budget(conn, today)
        seen = _already_sent(
            conn, [key for items in found.values() for key, _ in items])
    return {kind: [(k, line) for k, line in items if k not in seen]
            for kind, items in found.items()}


def compose(items, today):
    sections = [(label, items.get(kind, [])) for kind, label in SECTIONS]
    total = sum(len(lines) for _, lines in sections)
    if not total:
        return None

    parts = [f"Your budget for {today.isoformat()}.", ""]
    for label, lines in sections:
        if not lines:
            continue
        parts.append(f"{label}:")
        parts.extend(line for _, line in lines)
        parts.append("")
    parts.append("You are getting this because the money alerts feature is on. "
                 "Settings has the switch.")
    thing = "thing" if total == 1 else "things"
    return f"Budget digest: {total} {thing} to know about", "\n".join(parts)


def send_digest(today=None, now=None):
    try:
        if not features.enabled("money_alerts"):
            return None
        owner = notify.owner_address()
        if not owner:
            return None

        today = today or date.today()
        items = pending(today, now)
        composed = compose(items, today)
        if composed is None:
            return None
        subject, body = composed

        notify.deliver([owner], subject, body, thread_name="budget-digest")
        _record(items, today)
        return subject
    except Exception:
        log.exception("Could not put together the budget digest")
        return None


def _record(items, today):
    rows = [(key, kind, today.isoformat())
            for kind, found in items.items() for key, _ in found]
    if not rows:
        return
    with get_conn() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO alert_log (alert_key, kind, sent_on) "
            "VALUES (?, ?, ?)", rows)
        cutoff = (today - timedelta(days=FORGET_AFTER_DAYS)).isoformat()
        conn.execute("DELETE FROM alert_log WHERE sent_on < ?", (cutoff,))
