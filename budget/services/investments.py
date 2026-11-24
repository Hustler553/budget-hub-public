from budget.db import get_conn

_TYPE_LABELS = {
    "equity": "Stocks",
    "etf": "ETFs",
    "mutual fund": "Mutual funds",
    "fixed income": "Bonds",
    "cash": "Cash",
    "cryptocurrency": "Crypto",
    "derivative": "Derivatives",
    "": "Other",
}


def type_label(security_type):
    key = (security_type or "").strip().lower()
    return _TYPE_LABELS.get(key) or key.title()


def positions():
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT h.account_id, a.name AS account_name,
                      h.quantity_e8, h.value_cents, h.cost_basis_cents,
                      h.price_as_of, s.ticker, s.name, s.security_type
                 FROM holdings h
                 JOIN securities s ON s.security_id = h.security_id
                 JOIN accounts a ON a.id = h.account_id
                WHERE a.archived = 0
                ORDER BY h.value_cents DESC, s.name""").fetchall()
    out = []
    for row in rows:
        position = dict(row)
        value, cost = row["value_cents"], row["cost_basis_cents"]
        gain = value - cost if value is not None and cost is not None else None
        position["gain_cents"] = gain
        position["gain_percent"] = (round(100 * gain / cost, 1)
                                    if gain is not None and cost else None)
        position["type_label"] = type_label(row["security_type"])
        out.append(position)
    return out


def totals(held):
    value = sum(p["value_cents"] or 0 for p in held)
    costed = [p for p in held if p["gain_cents"] is not None]
    cost = sum(p["cost_basis_cents"] for p in costed)
    gain = sum(p["gain_cents"] for p in costed)
    return {
        "value": value,
        "cost": cost,
        "gain": gain if costed else None,
        "gain_percent": round(100 * gain / cost, 1) if costed and cost else None,
        "costed_value": sum(p["value_cents"] for p in costed),
        "uncosted": len(held) - len(costed),
        "count": len(held),
    }


def allocation(held):
    by_type = {}
    for position in held:
        label = position["type_label"]
        by_type[label] = by_type.get(label, 0) + (position["value_cents"] or 0)
    whole = sum(v for v in by_type.values() if v > 0)
    rows = [{"label": label, "value": value,
             "share": round(100 * value / whole, 1) if whole and value > 0 else 0}
            for label, value in by_type.items()]
    rows.sort(key=lambda r: (-r["value"], r["label"]))
    return rows


def by_account(held):
    sums = {}
    for position in held:
        entry = sums.setdefault(position["account_id"],
                                {"name": position["account_name"], "value": 0,
                                 "count": 0})
        entry["value"] += position["value_cents"] or 0
        entry["count"] += 1
    return sorted(sums.values(), key=lambda r: (-r["value"], r["name"]))


def has_investment_accounts():
    with get_conn() as conn:
        return conn.execute(
            "SELECT 1 FROM accounts WHERE kind = 'investment' AND archived = 0 "
            "LIMIT 1").fetchone() is not None
