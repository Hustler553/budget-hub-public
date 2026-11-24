from budget.db import get_conn

MAX_NAME = 40


def parse_names(text):
    seen, out = set(), []
    for raw in (text or "").split(","):
        name = " ".join(raw.split())[:MAX_NAME]
        if not name:
            continue
        if name.casefold() in seen:
            continue
        seen.add(name.casefold())
        out.append(name)
    return out


def set_tags(txn_id, names):
    names = parse_names(",".join(names)) if not isinstance(names, str) else parse_names(names)
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        ids = []
        for name in names:
            row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
            if row is None:
                row_id = conn.execute(
                    "INSERT INTO tags (name) VALUES (?)", (name,)).lastrowid
            else:
                row_id = row["id"]
            ids.append(row_id)

        conn.execute("DELETE FROM transaction_tags WHERE transaction_id = ?", (txn_id,))
        conn.executemany(
            "INSERT OR IGNORE INTO transaction_tags (transaction_id, tag_id) VALUES (?, ?)",
            [(txn_id, tag_id) for tag_id in ids])
        conn.execute(
            "DELETE FROM tags WHERE NOT EXISTS "
            "(SELECT 1 FROM transaction_tags tt WHERE tt.tag_id = tags.id)")
        return names


def tags_for(txn_id):
    with get_conn() as conn:
        return [r["name"] for r in conn.execute(
            """SELECT t.name FROM tags t
                 JOIN transaction_tags tt ON tt.tag_id = t.id
                WHERE tt.transaction_id = ? ORDER BY t.name COLLATE NOCASE""",
            (txn_id,))]


def tags_for_many(txn_ids):
    ids = list(txn_ids)
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    out = {}
    with get_conn() as conn:
        for row in conn.execute(
                f"""SELECT tt.transaction_id, t.name FROM transaction_tags tt
                      JOIN tags t ON t.id = tt.tag_id
                     WHERE tt.transaction_id IN ({marks})
                     ORDER BY t.name COLLATE NOCASE""", ids):
            out.setdefault(row["transaction_id"], []).append(row["name"])
    return out


def all_tags():
    with get_conn() as conn:
        return conn.execute(
            """SELECT t.id, t.name, COUNT(tt.transaction_id) AS n
                 FROM tags t
                 LEFT JOIN transaction_tags tt ON tt.tag_id = t.id
                GROUP BY t.id ORDER BY t.name COLLATE NOCASE""").fetchall()


def rename(tag_id, new_name):
    names = parse_names(new_name)
    if not names:
        return None
    name = names[0]
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        clash = conn.execute(
            "SELECT 1 FROM tags WHERE name = ? AND id != ?", (name, tag_id)).fetchone()
        if clash:
            return None
        changed = conn.execute(
            "UPDATE tags SET name = ? WHERE id = ?", (name, tag_id)).rowcount
        return name if changed else None
