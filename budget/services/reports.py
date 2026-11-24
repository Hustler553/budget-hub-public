from budget.db import get_conn
from budget.utils import month_bounds, month_label, shift_month

PLOT_LEFT = 44.0
PLOT_RIGHT = 614.0
PLOT_TOP = 10.0
PLOT_BOTTOM = 165.0

TOP_CATEGORIES = 6

BANDS = ("#2B6CB0", "#0B7A57", "#B7791F", "#8E4BA8", "#C2410C", "#0E7490")
BAND_REST = "#94A3B8"


def savings_rate(history):
    points = []
    for entry in history:
        if entry["income"] <= 0:
            points.append({**entry, "rate": None})
            continue
        points.append({**entry,
                       "rate": round(entry["net"] / entry["income"] * 100)})
    rated = [p["rate"] for p in points if p["rate"] is not None]
    return {
        "points": points,
        "average": round(sum(rated) / len(rated)) if rated else None,
        "best": max(rated) if rated else None,
        "worst": min(rated) if rated else None,
        "months_counted": len(rated),
    }


def savings_rate_chart(summary):
    rated = [p for p in summary["points"] if p["rate"] is not None]
    if len(rated) < 2:
        return {"points": [], "line": "", "zero_y": None, "labels": []}

    values = [p["rate"] for p in rated]
    low, high = min(values), max(values)
    pad = max((high - low) // 6, 2)
    low, high = low - pad, high + pad
    span = high - low

    width = PLOT_RIGHT - PLOT_LEFT
    height = PLOT_BOTTOM - PLOT_TOP
    step = width / (len(rated) - 1)

    placed = []
    for i, p in enumerate(rated):
        placed.append({**p,
                       "x": round(PLOT_LEFT + i * step, 2),
                       "y": round(PLOT_BOTTOM - (p["rate"] - low) / span * height, 2)})

    zero_y = None
    if low < 0 < high:
        zero_y = round(PLOT_BOTTOM - (0 - low) / span * height, 2)

    return {
        "points": placed,
        "line": " ".join(f"{p['x']},{p['y']}" for p in placed),
        "zero_y": zero_y,
        "labels": [{"x": p["x"], "text": p["label"]} for p in placed],
    }


def category_trend(end_month, months):
    window = [shift_month(end_month, -i) for i in range(months - 1, -1, -1)]
    earliest = month_bounds(window[0])[0]
    latest = month_bounds(window[-1])[1]

    with get_conn() as conn:
        rows = conn.execute(
            """SELECT c.name AS name, substr(t.txn_date, 1, 7) AS m,
                      COALESCE(SUM(-t.amount_cents), 0) AS spent
                 FROM category_amounts t
                 JOIN categories c ON c.id = t.category_id
                WHERE t.amount_cents < 0
                  AND t.txn_date BETWEEN ? AND ?
                  AND t.transfer_pair_id IS NULL
                  AND c.kind = 'expense'
                GROUP BY c.name, m""",
            (earliest, latest),
        ).fetchall()

    by_category = {}
    for row in rows:
        by_category.setdefault(row["name"], {})[row["m"]] = row["spent"]

    ranked = sorted(by_category.items(),
                    key=lambda kv: sum(kv[1].values()), reverse=True)
    series = []
    for i, (name, per_month) in enumerate(ranked[:TOP_CATEGORIES]):
        totals = [per_month.get(m, 0) for m in window]
        series.append({"name": name, "colour": BANDS[i % len(BANDS)],
                       "totals": totals, "total": sum(totals)})

    rest = ranked[TOP_CATEGORIES:]
    if rest:
        totals = [sum(per_month.get(m, 0) for _n, per_month in rest) for m in window]
        if any(totals):
            series.append({"name": "Everything else", "colour": BAND_REST,
                           "totals": totals, "total": sum(totals)})

    return {
        "months": [{"month": m, "label": month_label(m)[:3].upper()} for m in window],
        "series": series,
        "monthly_totals": [sum(s["totals"][i] for s in series)
                           for i in range(len(window))],
    }


def category_trend_chart(trend):
    totals = trend["monthly_totals"]
    if not totals or not any(totals):
        return {"columns": [], "ceiling": 0}

    ceiling = max(totals)
    width = PLOT_RIGHT - PLOT_LEFT
    height = PLOT_BOTTOM - PLOT_TOP
    slot = width / len(totals)
    bar_w = slot * 0.62

    columns = []
    for i, month in enumerate(trend["months"]):
        x = PLOT_LEFT + slot * i + (slot - bar_w) / 2
        y = PLOT_BOTTOM
        bands = []
        for s in trend["series"]:
            cents = s["totals"][i]
            if cents <= 0:
                continue
            band_h = cents / ceiling * height
            y -= band_h
            bands.append({"name": s["name"], "colour": s["colour"],
                          "cents": cents, "y": round(y, 2),
                          "height": round(band_h, 2)})
        columns.append({"label": month["label"], "month": month["month"],
                        "x": round(x, 2), "width": round(bar_w, 2),
                        "total": totals[i], "bands": bands})
    return {"columns": columns, "ceiling": ceiling}


def sankey(month):
    start, end = month_bounds(month)
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT c.name AS name, c.kind AS kind,
                      COALESCE(SUM(t.amount_cents), 0) AS total
                 FROM category_amounts t
                 JOIN categories c ON c.id = t.category_id
                WHERE t.txn_date BETWEEN ? AND ?
                  AND t.transfer_pair_id IS NULL
                  AND c.kind != 'transfer'
                GROUP BY c.name, c.kind""",
            (start, end),
        ).fetchall()

    sources = sorted(((r["name"], r["total"]) for r in rows
                      if r["kind"] == "income" and r["total"] > 0),
                     key=lambda kv: kv[1], reverse=True)
    spends = sorted(((r["name"], -r["total"]) for r in rows
                     if r["kind"] == "expense" and r["total"] < 0),
                    key=lambda kv: kv[1], reverse=True)

    income = sum(amount for _n, amount in sources)
    spent = sum(amount for _n, amount in spends)
    kept = income - spent

    destinations = list(spends[:TOP_CATEGORIES])
    tail = spends[TOP_CATEGORIES:]
    if tail:
        destinations.append(("Everything else", sum(a for _n, a in tail)))
    if kept > 0:
        destinations.append(("Kept", kept))

    return {
        "month": month,
        "sources": [{"name": n, "cents": a} for n, a in sources],
        "destinations": [{"name": n, "cents": a} for n, a in destinations],
        "income_cents": income,
        "spent_cents": spent,
        "kept_cents": kept,
        "overspent": kept < 0,
    }


SANKEY_W = 620.0
SANKEY_H = 300.0
SANKEY_BAR = 14.0
SANKEY_GAP = 4.0


def sankey_chart(flow):
    sources, destinations = flow["sources"], flow["destinations"]
    if not sources or not destinations:
        return {"sources": [], "destinations": [], "ribbons": []}

    def stack(items, x):
        total = sum(i["cents"] for i in items)
        if total <= 0:
            return []
        room = SANKEY_H - SANKEY_GAP * max(len(items) - 1, 0)
        out, y = [], 0.0
        for item in items:
            h = item["cents"] / total * room
            out.append({**item, "x": x, "y": round(y, 2),
                        "height": round(h, 2),
                        "mid": round(y + h / 2, 2)})
            y += h + SANKEY_GAP
        return out

    left = stack(sources, 0.0)
    right = stack(destinations, SANKEY_W - SANKEY_BAR)

    ribbons = []
    x1 = SANKEY_BAR
    x2 = SANKEY_W - SANKEY_BAR
    curve = (x2 - x1) * 0.42
    for s in left:
        for d in right:
            share = d["cents"] / sum(i["cents"] for i in destinations)
            h = s["height"] * share
            if h < 0.5:
                continue
            y1 = s["y"] + sum(
                s["height"] * (o["cents"] / sum(i["cents"] for i in destinations))
                for o in right[:right.index(d)])
            y2 = d["y"] + sum(
                d["height"] * (o["cents"] / sum(i["cents"] for i in sources))
                for o in left[:left.index(s)])
            h2 = d["height"] * (s["cents"] / sum(i["cents"] for i in sources))
            ribbons.append({
                "source": s["name"], "destination": d["name"],
                "colour": d.get("colour") or BANDS[right.index(d) % len(BANDS)],
                "path": (f"M{x1},{round(y1, 2)} "
                         f"C{round(x1 + curve, 2)},{round(y1, 2)} "
                         f"{round(x2 - curve, 2)},{round(y2, 2)} "
                         f"{x2},{round(y2, 2)} "
                         f"L{x2},{round(y2 + h2, 2)} "
                         f"C{round(x2 - curve, 2)},{round(y2 + h2, 2)} "
                         f"{round(x1 + curve, 2)},{round(y1 + h, 2)} "
                         f"{x1},{round(y1 + h, 2)} Z"),
            })

    for i, d in enumerate(right):
        d["colour"] = ("#0B7A57" if d["name"] == "Kept"
                       else BANDS[i % len(BANDS)])
    return {"sources": left, "destinations": right, "ribbons": ribbons,
            "width": SANKEY_W, "height": SANKEY_H, "bar": SANKEY_BAR}
