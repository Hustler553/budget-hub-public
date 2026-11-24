import sqlite3
from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, url_for

from budget.routes import came_from
from budget.services.sources import csv_import
from budget.services import accounts, glyphs, pending, splits, tags, transfers, txns
from budget.db import get_conn
from budget.utils import (month_bounds, shift_month, month_label, normalize_month,
                   parse_amount_to_cents, parse_iso_date, to_int)

bp = Blueprint("transactions", __name__)

NO_SUCH_TRANSACTION = "That transaction couldn't be found."

BLANK_SPLIT_ROWS = 3

HAS_SPLITS = (
    "EXISTS (SELECT 1 FROM transaction_splits s WHERE s.transaction_id = t.id)")


def _unreviewed_count():
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE reviewed = 0").fetchone()[0]


def _active_categories(conn):
    return conn.execute(
        "SELECT * FROM categories WHERE archived = 0 ORDER BY kind DESC, name"
    ).fetchall()


def _form_account_id():
    account_id = to_int(request.form.get("account_id"))
    if account_id is None:
        return None
    try:
        known = accounts.get(account_id) is not None
    except OverflowError:
        known = False
    return account_id if known else None


def _account_choices(current_id=None):
    choices = list(accounts.active())
    if current_id is not None and all(a["id"] != current_id for a in choices):
        current = accounts.get(current_id)
        if current is not None:
            choices.append(current)
    return choices


PAGE_SIZE = 100


def _amount_bound(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return parse_amount_to_cents(raw)
    except ValueError:
        return None


def _ledger_filters(args):
    where, params, shown = [], [], {}

    date_from = parse_iso_date(args.get("from"))
    date_to = parse_iso_date(args.get("to"))
    raw_month = args.get("month", "")
    if date_from or date_to:
        if date_from:
            where.append("t.txn_date >= ?")
            params.append(str(date_from))
            shown["from"] = str(date_from)
        if date_to:
            where.append("t.txn_date <= ?")
            params.append(str(date_to))
            shown["to"] = str(date_to)
    elif raw_month and normalize_month(raw_month) == raw_month:
        start, end = month_bounds(raw_month)
        where.append("t.txn_date BETWEEN ? AND ?")
        params += [start, end]
        shown["month"] = raw_month

    if args.get("reviewed") == "no":
        where.append("t.reviewed = 0")
        shown["reviewed"] = "no"

    tag = args.get("tag", "").strip()
    if tag:
        where.append("EXISTS (SELECT 1 FROM transaction_tags tt"
                     "         JOIN tags tg ON tg.id = tt.tag_id"
                     "        WHERE tt.transaction_id = t.id AND tg.name = ?)")
        params.append(tag)
        shown["tag"] = tag

    category = args.get("category", "")
    if category == "none":
        where.append("t.category_id IS NULL AND NOT %s" % HAS_SPLITS)
        shown["category"] = "none"
    elif category.isascii() and category.isdigit() and len(category) <= 18:
        where.append("(t.category_id = ?"
                     " OR EXISTS (SELECT 1 FROM transaction_splits s"
                     "             WHERE s.transaction_id = t.id"
                     "               AND s.category_id = ?))")
        params += [int(category), int(category)]
        shown["category"] = category

    account = args.get("account", "")
    if account == "none":
        where.append("t.account_id IS NULL")
        shown["account"] = "none"
    elif account.isascii() and account.isdigit() and len(account) <= 18:
        where.append("t.account_id = ?")
        params.append(int(account))
        shown["account"] = account

    low, high = _amount_bound(args.get("min")), _amount_bound(args.get("max"))
    if low is not None and high is not None and low > high:
        low, high = high, low
    if low is not None:
        where.append("ABS(t.amount_cents) >= ?")
        params.append(low)
        shown["min"] = f"{low / 100:.2f}"
    if high is not None:
        where.append("ABS(t.amount_cents) <= ?")
        params.append(high)
        shown["max"] = f"{high / 100:.2f}"

    search = args.get("q", "").strip()
    if search:
        escaped = (search.replace("\\", "\\\\")
                   .replace("%", "\\%").replace("_", "\\_"))
        where.append("(t.merchant LIKE ? ESCAPE '\\' OR t.note LIKE ? ESCAPE '\\')")
        params += [f"%{escaped}%", f"%{escaped}%"]
        shown["q"] = search

    return where, params, shown


def _period_label(shown):
    if "month" in shown:
        return month_label(shown["month"])
    if "from" in shown and "to" in shown:
        return f"{shown['from']} to {shown['to']}"
    if "from" in shown:
        return f"Since {shown['from']}"
    if "to" in shown:
        return f"Up to {shown['to']}"
    return "All time"


@bp.route("/transactions")
def index():
    where, params, shown = _ledger_filters(request.args)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    month = shown.get("month", "")
    this_month = normalize_month(None)

    with get_conn() as conn:
        totals = conn.execute(
            f"""SELECT COUNT(*) AS n,
                       COALESCE(SUM(CASE WHEN t.amount_cents > 0
                                         THEN t.amount_cents END), 0) AS money_in,
                       COALESCE(SUM(CASE WHEN t.amount_cents < 0
                                         THEN t.amount_cents END), 0) AS money_out
                  FROM transactions t{clause}""", params).fetchone()
        pages = max(1, -(-totals["n"] // PAGE_SIZE))
        page = min(max(to_int(request.args.get("page"), 1) or 1, 1), pages)

        rows = conn.execute(
            f"""SELECT t.*, c.name AS category_name, a.name AS account_name,
                       c.icon AS category_icon, c.colour AS category_colour
                  FROM transactions t
                  LEFT JOIN categories c ON c.id = t.category_id
                  LEFT JOIN accounts a ON a.id = t.account_id{clause}
                 ORDER BY t.txn_date DESC, t.id DESC
                 LIMIT ? OFFSET ?""",
            params + [PAGE_SIZE, (page - 1) * PAGE_SIZE]).fetchall()
        rows = [{**dict(row),
                 "cat_icon": glyphs.icon_for(row["category_name"],
                                             row["category_icon"] or ""),
                 "cat_colour": glyphs.colour_for(row["category_name"],
                                                 row["category_colour"] or "")}
                for row in rows]
        categories = _active_categories(conn)
        uncategorized = conn.execute(
            """SELECT t.*, c.name AS category_name
               FROM transactions t LEFT JOIN categories c ON c.id = t.category_id
               WHERE t.category_id IS NULL AND t.transfer_pair_id IS NULL
                 AND NOT EXISTS (SELECT 1 FROM transaction_splits s WHERE s.transaction_id = t.id)
               ORDER BY t.txn_date DESC LIMIT 50"""
        ).fetchall()
        review_emails = conn.execute(
            "SELECT * FROM email_log WHERE status = 'review' ORDER BY received_at DESC"
        ).fetchall()
        if "from" in shown or "to" in shown:
            pending_rows, pending_cents = [], 0
        else:
            start, end = month_bounds(month or this_month)
            pending_rows = pending.for_month(conn, start, end)
            pending_cents = pending.month_spending_cents(conn, start, end)

    kept = {k: v for k, v in shown.items() if k not in ("month", "from", "to")}
    return render_template(
        "transactions.html",
        rows=rows, categories=categories, account_choices=_account_choices(),
        split_marks=splits.split_counts(r["id"] for r in rows),
        row_tags=tags.tags_for_many(r["id"] for r in rows),
        all_tags=tags.all_tags(),
        tag_filter=shown.get("tag", ""),
        unreviewed_only="reviewed" in shown,
        unreviewed_count=_unreviewed_count(),
        uncategorized=uncategorized, review_emails=review_emails,
        pending_rows=pending_rows, pending_cents=pending_cents,
        month=month, month_name=_period_label(shown),
        pending_month_name=month_label(month or this_month),
        prev_month=shift_month(month or this_month, -1),
        next_month=shift_month(month or this_month, 1),
        this_month=this_month,
        category_filter=shown.get("category", ""),
        account_filter=shown.get("account", ""),
        search=shown.get("q", ""),
        shown=shown, kept=kept,
        filtered=bool(kept),
        total=totals["n"], money_in=totals["money_in"],
        money_out=totals["money_out"],
        page=page, pages=pages,
        today=str(date.today()),
    )


@bp.route("/transactions/add", methods=["POST"])
def add():
    try:
        cents = parse_amount_to_cents(request.form["amount"])
    except ValueError:
        flash("Please enter a valid amount.")
        return redirect(url_for("transactions.index"))

    if request.form.get("kind") != "income":
        cents = -cents

    txns.add_transaction(
        txn_date=parse_iso_date(request.form.get("date")) or date.today(),
        amount_cents=cents,
        merchant=request.form.get("merchant", ""),
        note=request.form.get("note", ""),
        account_id=_form_account_id(),
        category_id=to_int(request.form.get("category_id")),
    )
    flash("Transaction added.")
    return redirect(url_for("transactions.index"))


@bp.route("/transactions/<int:txn_id>/categorize", methods=["POST"])
def categorize(txn_id):
    category_id = to_int(request.form.get("category_id"))
    with get_conn() as conn:
        category_id = txns.valid_category_id(conn, category_id)
        if category_id is None:
            return redirect(came_from(url_for("transactions.index")))
        conn.execute("UPDATE transactions SET category_id = ? WHERE id = ?",
                     (category_id, txn_id))
        row = conn.execute(
            "SELECT merchant FROM transactions WHERE id = ?", (txn_id,)
        ).fetchone()
    merchant = row["merchant"] if row else ""

    if request.form.get("remember") and merchant:
        match_text = txns.normalize_merchant(merchant)
        txns.add_merchant_rule(match_text, category_id)
        flash(f'Rule saved — future "{match_text}" transactions will auto-categorize.')
    return redirect(came_from(url_for("transactions.index")))


@bp.route("/transactions/<int:txn_id>/delete", methods=["POST"])
def delete(txn_id):
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        transfers.release_pair(conn, txn_id)
        conn.execute("DELETE FROM transactions WHERE id = ?", (txn_id,))
    flash("Transaction deleted.")
    return redirect(came_from(url_for("transactions.index")))


@bp.route("/transactions/<int:txn_id>/edit", methods=["GET", "POST"])
def edit(txn_id):
    with get_conn() as conn:
        txn = conn.execute("SELECT * FROM transactions WHERE id = ?", (txn_id,)).fetchone()
        categories = _active_categories(conn)
    if txn is None:
        flash("Transaction not found.")
        return redirect(url_for("transactions.index"))

    if request.method == "POST":
        try:
            cents = parse_amount_to_cents(request.form["amount"])
        except ValueError:
            flash("Please enter a valid amount.")
            return redirect(url_for("transactions.edit", txn_id=txn_id))
        txn_date = parse_iso_date(request.form.get("date"))
        if txn_date is None:
            flash("Please enter a valid date.")
            return redirect(url_for("transactions.edit", txn_id=txn_id))
        if request.form.get("kind") != "income":
            cents = -cents
        account_id = _form_account_id()
        with get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            was = conn.execute(
                "SELECT txn_date, amount_cents, account_id FROM transactions WHERE id = ?",
                (txn_id,)).fetchone()
            if (was is not None and was["amount_cents"] != cents
                    and conn.execute(
                        "SELECT 1 FROM transaction_splits WHERE transaction_id = ?",
                        (txn_id,)).fetchone()):
                flash("This transaction is split, and its parts add up to the "
                      "old amount. Remove the split, change the amount, then "
                      "split it again.")
                return redirect(url_for("transactions.edit", txn_id=txn_id))
            partner = None
            touched_the_rule = was is not None and (
                was["txn_date"] != str(txn_date)
                or was["amount_cents"] != cents
                or was["account_id"] != account_id)
            if touched_the_rule:
                partner = transfers.other_half(conn, txn_id)
                transfers.release_pair(conn, txn_id)
            conn.execute(
                """UPDATE transactions SET txn_date = ?, amount_cents = ?, merchant = ?,
                   note = ?, account_id = ?, category_id = ? WHERE id = ?""",
                (str(txn_date), cents, request.form.get("merchant", "").strip(),
                 request.form.get("note", "").strip(), account_id,
                 None if conn.execute(
                     "SELECT 1 FROM transaction_splits WHERE transaction_id = ?",
                     (txn_id,)).fetchone()
                 else txns.valid_category_id(conn, to_int(request.form.get("category_id"))),
                 txn_id),
            )

            if touched_the_rule:
                found = transfers.find_match(conn, txn_id)
                if found is not None and (partner is None or found == partner):
                    transfers.pair(conn, txn_id, found)

        try:
            tags.set_tags(txn_id, request.form.get("tags", ""))
        except sqlite3.IntegrityError:
            flash(NO_SUCH_TRANSACTION)
            return redirect(url_for("transactions.index"))

        flash("Transaction updated.")
        return redirect(url_for("transactions.index"))

    return render_template("edit_transaction.html", txn=txn, categories=categories,
                           account_choices=_account_choices(txn["account_id"]),
                           txn_splits=splits.splits_for(txn_id),
                           txn_tags=", ".join(tags.tags_for(txn_id)),
                           blank_split_rows=range(BLANK_SPLIT_ROWS))


@bp.route("/transactions/import-csv", methods=["POST"])
def import_csv():
    file = request.files.get("csv_file")
    if file is None or not file.filename:
        flash("Choose a CSV file first.")
        return redirect(url_for("transactions.index"))

    text = file.read().decode("utf-8-sig", errors="replace")
    if csv_import.looks_like_robinhood_csv(text):
        flash(csv_import.summarize(csv_import.import_csv_text(text)))
        return redirect(url_for("transactions.index"))

    try:
        headers, rows = csv_import.read_table(text)
    except csv_import.CsvRefused as refused:
        flash(str(refused))
        return redirect(url_for("transactions.index"))
    return _csv_mapping_page(text, headers, rows,
                             csv_import.guess_mapping(headers, rows))


def _csv_mapping_page(text, headers, rows, mapping, account_id=None):
    return render_template(
        "csv_map.html", csv_text=text, headers=headers,
        preview=rows[:csv_import.PREVIEW_ROWS], row_count=len(rows),
        mapping=mapping, account_choices=_account_choices(),
        account_id=account_id if account_id is not None
        else accounts.resolve_for_source("csv"),
        plaid_backed={a["id"] for a in _account_choices()
                      if accounts.is_plaid_backed(a["id"])})


@bp.route("/transactions/import-csv/mapped", methods=["POST"])
def import_csv_mapped():
    text = request.form.get("csv_text", "")
    try:
        headers, rows = csv_import.read_table(text)
    except csv_import.CsvRefused as refused:
        flash(str(refused))
        return redirect(url_for("transactions.index"))

    account_id = _form_account_id()
    try:
        mapping = csv_import.clean_mapping(request.form, len(headers))
    except csv_import.CsvRefused as refused:
        flash(str(refused))
        chosen = {role: to_int(request.form.get(role)) for role in csv_import.ROLES}
        chosen["spending"] = request.form.get("spending", "negative")
        chosen["day_first"] = bool(request.form.get("day_first"))
        return _csv_mapping_page(text, headers, rows, chosen, account_id)

    counts = csv_import.import_mapped(text, mapping, account_id)
    if not counts["imported"] and not counts["duplicate"]:
        flash("Nothing in that file could be read with those columns — check "
              "which one is the date and which is the amount.")
        return _csv_mapping_page(text, headers, rows, mapping, account_id)
    flash(csv_import.summarize(counts))
    return redirect(url_for("transactions.index"))


@bp.route("/review/<int:log_id>/accept", methods=["POST"])
def review_accept(log_id):
    try:
        cents = parse_amount_to_cents(request.form["amount"])
    except ValueError:
        flash("Please enter a valid amount.")
        return redirect(url_for("transactions.index"))

    with get_conn() as conn:
        log = conn.execute("SELECT * FROM email_log WHERE id = ?", (log_id,)).fetchone()
    if log is None:
        return redirect(url_for("transactions.index"))

    created = txns.add_transaction(
        txn_date=parse_iso_date(log["received_at"]) or date.today(),
        amount_cents=-cents,
        merchant=request.form.get("merchant", "").strip(),
        account_id=accounts.resolve_for_source("email", mask=log["mask"]),
        category_id=to_int(request.form.get("category_id")),
        source="email",
        external_id=f"email:{log['uid']}",
    )
    with get_conn() as conn:
        conn.execute("UPDATE email_log SET status = 'parsed' WHERE id = ?", (log_id,))
    flash("Transaction created from email." if created else
          "That email was already imported — nothing new was added.")
    return redirect(url_for("transactions.index"))


@bp.route("/review/<int:log_id>/dismiss", methods=["POST"])
def review_dismiss(log_id):
    with get_conn() as conn:
        conn.execute("UPDATE email_log SET status = 'dismissed' WHERE id = ?", (log_id,))
    return redirect(url_for("transactions.index"))


@bp.route("/transactions/<int:txn_id>/split", methods=["POST"])
def split(txn_id):
    parts, incomplete = [], False
    for raw_category, raw_amount in zip(request.form.getlist("split_category"),
                                        request.form.getlist("split_amount")):
        has_category = bool(raw_category.strip())
        has_amount = bool(raw_amount.strip())
        if not has_category and not has_amount:
            continue
        if not (has_category and has_amount):
            incomplete = True
            continue
        try:
            cents = parse_amount_to_cents(raw_amount)
        except ValueError:
            flash("One of those split amounts didn't look like an amount.")
            return redirect(url_for("transactions.edit", txn_id=txn_id))
        parts.append((to_int(raw_category), cents))

    if incomplete:
        flash("Every part of a split needs both a category and an amount.")
        return redirect(url_for("transactions.edit", txn_id=txn_id))

    with get_conn() as conn:
        row = conn.execute("SELECT amount_cents FROM transactions WHERE id = ?",
                           (txn_id,)).fetchone()
    if row is None:
        flash(NO_SUCH_TRANSACTION)
        return redirect(url_for("transactions.index"))
    if row["amount_cents"] < 0:
        parts = [(c, -abs(a)) for c, a in parts]

    try:
        written = splits.set_splits(txn_id, parts)
    except splits.SplitError as refused:
        flash(str(refused))
        return redirect(url_for("transactions.edit", txn_id=txn_id))

    flash(f"Split across {written} categories.")
    return redirect(url_for("transactions.edit", txn_id=txn_id))


@bp.route("/transactions/<int:txn_id>/unsplit", methods=["POST"])
def unsplit(txn_id):
    gone = splits.clear(txn_id)
    if gone:
        flash("Split removed — the transaction needs a category again.")
    return redirect(url_for("transactions.edit", txn_id=txn_id))


BULK_ACTIONS = ("categorize", "tag", "review", "unreview", "delete")
BULK_LIMIT = 500


@bp.route("/transactions/bulk", methods=["POST"])
def bulk():
    action = request.form.get("action", "")
    if action not in BULK_ACTIONS:
        flash("That bulk action isn't one this page offers.")
        return redirect(came_from(url_for("transactions.index")))

    ids = [to_int(raw) for raw in request.form.getlist("selected")]
    ids = [i for i in ids if i is not None]
    if not ids:
        flash("Nothing was selected.")
        return redirect(came_from(url_for("transactions.index")))
    if len(ids) > BULK_LIMIT:
        flash(f"That is more than {BULK_LIMIT} transactions at once. "
              "Filter the list down and do it in batches.")
        return redirect(came_from(url_for("transactions.index")))

    marks = ",".join("?" * len(ids))

    if action == "delete":
        with get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for txn_id in ids:
                transfers.release_pair(conn, txn_id)
            removed = conn.execute(
                f"DELETE FROM transactions WHERE id IN ({marks})", ids).rowcount
        flash(f"Deleted {removed} transaction{'s' if removed != 1 else ''}.")
        return redirect(came_from(url_for("transactions.index")))

    if action in ("review", "unreview"):
        value = 1 if action == "review" else 0
        with get_conn() as conn:
            changed = conn.execute(
                f"UPDATE transactions SET reviewed = ? WHERE id IN ({marks})",
                [value, *ids]).rowcount
        flash(f"Marked {changed} transaction{'s' if changed != 1 else ''} "
              f"{'reviewed' if value else 'unreviewed'}.")
        return redirect(came_from(url_for("transactions.index")))

    if action == "categorize":
        with get_conn() as conn:
            category_id = txns.valid_category_id(
                conn, to_int(request.form.get("bulk_category_id")))
            if category_id is None:
                flash("Pick a category to apply.")
                return redirect(came_from(url_for("transactions.index")))
            changed = conn.execute(
                f"""UPDATE transactions SET category_id = ?
                     WHERE id IN ({marks})
                       AND NOT EXISTS (SELECT 1 FROM transaction_splits s
                                        WHERE s.transaction_id = transactions.id)""",
                [category_id, *ids]).rowcount
        skipped = len(ids) - changed
        flash(f"Categorized {changed} transaction{'s' if changed != 1 else ''}."
              + (f" {skipped} already split, so left alone." if skipped else ""))
        return redirect(came_from(url_for("transactions.index")))

    names = tags.parse_names(request.form.get("bulk_tags", ""))
    if not names:
        flash("Type a tag to add.")
        return redirect(came_from(url_for("transactions.index")))
    with get_conn() as conn:
        ids = [r["id"] for r in conn.execute(
            f"SELECT id FROM transactions WHERE id IN ({marks})", ids)]
    for txn_id in ids:
        tags.set_tags(txn_id, tags.tags_for(txn_id) + names)
    flash(f"Tagged {len(ids)} transaction{'s' if len(ids) != 1 else ''} "
          f"{', '.join(names)}.")
    return redirect(came_from(url_for("transactions.index")))
