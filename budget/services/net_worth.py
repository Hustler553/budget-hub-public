from datetime import date, timedelta

from budget.db import get_conn

PLOT_LEFT = 44.0
PLOT_RIGHT = 614.0
PLOT_TOP = 10.0
PLOT_BOTTOM = 165.0

MAX_DOTS = 70


def series(start, end, kind=None):
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT s.account_id, s.taken_on, s.balance_cents,
                      a.archived,
                      MAX(s.taken_on) OVER (PARTITION BY s.account_id) AS last_on
                 FROM balance_snapshots s
                 JOIN accounts a ON a.id = s.account_id
                WHERE s.taken_on <= ?
                  AND (? IS NULL OR a.kind = ?)
                ORDER BY s.taken_on, s.account_id""",
            (end.isoformat(), kind, kind),
        ).fetchall()

    if not rows:
        return []

    held = {}
    cursor = 0
    first_iso = rows[0]["taken_on"]

    def absorb(up_to_iso):
        nonlocal cursor
        while cursor < len(rows) and rows[cursor]["taken_on"] <= up_to_iso:
            row = rows[cursor]
            held[row["account_id"]] = (
                row["balance_cents"],
                row["last_on"] if row["archived"] else None,
            )
            cursor += 1

    day = max(start, date.fromisoformat(first_iso))
    absorb((day - timedelta(days=1)).isoformat())

    out = []
    while day <= end:
        iso = day.isoformat()
        absorb(iso)
        live = [(cents, stops) for cents, stops in held.values()
                if stops is None or iso <= stops]
        if live:
            out.append({"on": day,
                        "cents": sum(cents for cents, _ in live),
                        "accounts": len(live)})
        day += timedelta(days=1)
    return out


def chart(points):
    if not points:
        return {"points": [], "area": "", "line": "", "zero_y": None,
                "dots": False, "labels": []}

    values = [p["cents"] for p in points]
    low, high = min(values), max(values)
    if low == high:
        pad = max(abs(low) // 20, 100)
        low, high = low - pad, high + pad
    span = high - low

    width = PLOT_RIGHT - PLOT_LEFT
    height = PLOT_BOTTOM - PLOT_TOP
    step = width / (len(points) - 1) if len(points) > 1 else 0.0

    placed = []
    for i, p in enumerate(points):
        x = PLOT_LEFT + (i * step if len(points) > 1 else width / 2)
        y = PLOT_BOTTOM - (p["cents"] - low) / span * height
        placed.append({**p, "x": round(x, 2), "y": round(y, 2)})

    line = " ".join(f"{p['x']},{p['y']}" for p in placed)
    area = (f"M{placed[0]['x']},{PLOT_BOTTOM} "
            + " ".join(f"L{p['x']},{p['y']}" for p in placed)
            + f" L{placed[-1]['x']},{PLOT_BOTTOM} Z")

    zero_y = None
    if low < 0 < high:
        zero_y = round(PLOT_BOTTOM - (0 - low) / span * height, 2)

    return {"points": placed, "area": area, "line": line, "zero_y": zero_y,
            "dots": len(placed) <= MAX_DOTS, "labels": _labels(placed)}


def _labels(placed):
    out, seen = [], set()
    for p in placed:
        key = (p["on"].year, p["on"].month)
        if key not in seen:
            seen.add(key)
            out.append({"x": p["x"], "text": p["on"].strftime("%b")})
    if len(out) == 1 and len(placed) > 1:
        out = [{"x": placed[0]["x"], "text": placed[0]["on"].strftime("%b %d")},
               {"x": placed[-1]["x"], "text": placed[-1]["on"].strftime("%b %d")}]
    return out


def summary(points):
    if not points:
        return {"days": 0, "change": 0, "first": 0, "last": 0,
                "high": 0, "low": 0, "accounts": 0}
    values = [p["cents"] for p in points]
    return {"days": len(points),
            "first": values[0],
            "last": values[-1],
            "change": values[-1] - values[0],
            "high": max(values),
            "low": min(values),
            "accounts": points[-1]["accounts"]}
