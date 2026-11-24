import calendar as _calendar
from datetime import date

from budget.db import get_conn
from budget.services import glyphs, recurring
from budget.utils import month_bounds, parse_iso_date

_CAL = _calendar.Calendar(firstweekday=6)


def month_bills(month):
    start_iso, end_iso = month_bounds(month)
    start = parse_iso_date(start_iso)
    end_exclusive = date(start.year + (start.month == 12),
                         start.month % 12 + 1, 1)

    with get_conn() as conn:
        posted = conn.execute(
            """SELECT t.txn_date, t.merchant, t.amount_cents, c.name AS category,
                      c.icon AS category_icon, c.colour AS category_colour
                 FROM transactions t
                 LEFT JOIN categories c ON c.id = t.category_id
                WHERE %s AND t.amount_cents < 0
                  AND t.txn_date BETWEEN ? AND ?
                  AND t.transfer_pair_id IS NULL
                  AND (c.kind IS NULL OR c.kind != 'transfer')
                ORDER BY t.txn_date""" % recurring.OCCURRENCE_SQL,
            (start_iso, end_iso),
        ).fetchall()

    bills = [{"date": parse_iso_date(row["txn_date"]), "name": row["merchant"],
              "amount_cents": row["amount_cents"], "paid": True,
              "category": row["category"],
              "category_icon": row["category_icon"] or "",
              "category_colour": row["category_colour"] or ""}
             for row in posted]

    for item in recurring.upcoming_bills(start, end_exclusive):
        bills.append({**item, "paid": False})

    for bill in bills:
        name = bill.get("category")
        bill["cat_icon"] = glyphs.icon_for(name, bill.get("category_icon", ""))
        bill["cat_colour"] = glyphs.colour_for(name,
                                               bill.get("category_colour", ""))

    bills.sort(key=lambda b: (b["date"], b["name"]))
    return bills


def month_grid(month, bills, today=None):
    today = today or date.today()
    start = parse_iso_date(month_bounds(month)[0])

    by_day = {}
    for bill in bills:
        by_day.setdefault(bill["date"], []).append(bill)

    weeks = []
    for week in _CAL.monthdatescalendar(start.year, start.month):
        cells = []
        for day in week:
            outside = day.month != start.month
            cells.append({
                "date": day,
                "day": day.day,
                "outside": outside,
                "today": day == today,
                "bills": [] if outside else by_day.get(day, []),
            })
        weeks.append(cells)
    return weeks


def month_summary(bills):
    paid = sum(-b["amount_cents"] for b in bills if b["paid"])
    due = sum(-b["amount_cents"] for b in bills if not b["paid"])
    return {"paid_cents": paid, "due_cents": due, "total_cents": paid + due,
            "count": len(bills), "paid_count": sum(1 for b in bills if b["paid"])}
