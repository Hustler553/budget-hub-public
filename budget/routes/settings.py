import logging
import sqlite3
import tempfile
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from budget.services.sources import email_ingest, old_db, plaid
from budget import config
from budget.routes import came_from
from budget.services import accounts
from budget.services import alerts
from budget.services import branding
from budget.services import budgets
from budget.services import features
from budget.services import glyphs
from budget.services import recurring as recurring_mod
from budget.services import theme
from budget.services import txns
from budget.services import vault
from budget.db import (get_conn, get_all_settings, get_secret, get_setting,
                      set_secret, set_setting)
from budget.utils import (PAY_FREQUENCIES, current_pay_period, month_of,
                   parse_amount_to_cents, parse_iso_date, to_int)

log = logging.getLogger(__name__)

bp = Blueprint("settings", __name__, url_prefix="/settings")

SECTIONS = {
    "general": "General",
    "connections": "Connections",
    "budgeting": "Budgeting",
    "emails": "Emails",
}


@bp.after_request
def stay_in_section(response):
    location = response.headers.get("Location", "")
    if response.status_code not in (301, 302, 303) or not location:
        return response
    section = parse_qs(urlsplit(request.referrer or "").query).get("section", [""])[0]
    target = urlsplit(location)
    if (section in SECTIONS and target.path == url_for("settings.index")
            and not target.query and not target.netloc):
        response.headers["Location"] = (
            f"{target.path}?section={section}"
            + (f"#{target.fragment}" if target.fragment else ""))
    return response


@bp.route("/")
def index():
    section = request.args.get("section", "")
    if section not in SECTIONS:
        section = ""
    show = {key: section in ("", key) for key in SECTIONS}
    with get_conn() as conn:
        this_month = month_of(date.today())
        base_income = budgets.prior_month_income(conn, this_month)
        specs = budgets.effective_budgets(conn, this_month)
        categories = [
            {**glyphs.decorate(row, kind_field=None),
             "budget_cents": budgets.budget_for_month(
                 specs.get(row["id"], budgets.NO_BUDGET), base_income)}
            for row in conn.execute(
                "SELECT * FROM categories WHERE archived = 0 "
                "ORDER BY kind DESC, name")
        ]
        rules = conn.execute(
            """SELECT r.*, c.name AS category_name
               FROM merchant_rules r JOIN categories c ON c.id = r.category_id
               ORDER BY r.priority DESC, LENGTH(r.match_text) DESC, r.id"""
        ).fetchall()
        recurring_items = conn.execute(
            """SELECT r.*, c.name AS category_name
               FROM recurring r LEFT JOIN categories c ON c.id = r.category_id
               ORDER BY r.active DESC, r.next_date"""
        ).fetchall()
        email_recent = conn.execute(
            "SELECT * FROM email_log ORDER BY id DESC LIMIT 15"
        ).fetchall()

    return render_template(
        "settings.html",
        section=section, sections=SECTIONS, show=show,
        groups=_groups(),
        categories=categories,
        rules=rules,
        recurring_items=recurring_items,
        email_recent=email_recent,
        accounts=accounts.active(),
        s=get_all_settings(),
        today=str(date.today()),
        palettes=theme.PALETTES,
        modes=theme.MODES,
        owner_name=branding.owner_name(),
        max_owner_name=branding.MAX_OWNER_NAME,
        feature_registry=features.REGISTRY,
        digest_preview=_digest_preview(),
        plaid_key_set=vault.configured(),
        plaid_env=config.PLAID_ENV,
        plaid_ready=plaid.configured(),
        plaid_items=plaid.items() if features.enabled("plaid") else [],
    )


def _digest_preview():
    if not features.enabled("money_alerts"):
        return None
    try:
        items = alerts.pending()
    except Exception:
        log.exception("Could not work out the digest preview")
        return {"lines": [], "broken": True}
    lines = [line.strip() for _, found in items.items() for _, line in found]
    return {"lines": lines, "broken": False}


def _back_to():
    target = request.form.get("next", "")
    if target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("settings.index")


@bp.route("/categories/add", methods=["POST"])
def category_add():
    name = request.form.get("name", "").strip()
    kind = request.form.get("kind", "expense")
    if kind not in ("expense", "income"):
        kind = "expense"
    if name:
        with get_conn() as conn:
            existing = conn.execute(
                "SELECT id, archived FROM categories WHERE name = ?", (name,)
            ).fetchone()
            if existing is None:
                try:
                    conn.execute("INSERT INTO categories (name, kind) VALUES (?, ?)",
                                 (name, kind))
                    flash(f'Category "{name}" added.')
                except sqlite3.IntegrityError:
                    flash(f'Category "{name}" already exists.')
            elif existing["archived"]:
                conn.execute("UPDATE categories SET archived = 0 WHERE id = ?",
                             (existing["id"],))
                flash(f'Category "{name}" was archived — restored it.')
            else:
                flash(f'Category "{name}" already exists.')
    return redirect(_back_to())


@bp.route("/categories/<int:cat_id>/archive", methods=["POST"])
def category_archive(cat_id):
    with get_conn() as conn:
        conn.execute("UPDATE categories SET archived = 1 WHERE id = ?", (cat_id,))
    flash("Category archived (its past transactions are unchanged).")
    return redirect(_back_to())


@bp.route("/rules/add", methods=["POST"])
def rule_add():
    match_text = request.form.get("match_text", "").strip()
    category_id = to_int(request.form.get("category_id"))

    def _bound(field):
        raw = request.form.get(field, "").strip()
        if not raw:
            return None
        try:
            return abs(parse_amount_to_cents(raw))
        except ValueError:
            return None

    if txns.add_merchant_rule(
            match_text, category_id,
            amount_min_cents=_bound("amount_min"),
            amount_max_cents=_bound("amount_max"),
            add_tags=request.form.get("add_tags", ""),
            priority=to_int(request.form.get("priority")) or 0):
        flash("Rule added and applied to uncategorized transactions.")
    else:
        flash("Couldn't save the rule — enter match text and pick a category.")
    return redirect(url_for("settings.index") + "#rules")


@bp.route("/rules/<int:rule_id>/delete", methods=["POST"])
def rule_delete(rule_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM merchant_rules WHERE id = ?", (rule_id,))
    return redirect(url_for("settings.index"))


@bp.route("/recurring/add", methods=["POST"])
def recurring_add():
    name = request.form.get("name", "").strip()
    if not name:
        flash("Please enter a name for the recurring item.")
        return redirect(url_for("settings.index"))
    try:
        cents = parse_amount_to_cents(request.form["amount"])
    except ValueError:
        flash("Please enter a valid amount.")
        return redirect(url_for("settings.index"))
    if request.form.get("kind") != "income":
        cents = -cents

    next_date = parse_iso_date(request.form.get("next_date")) or date.today()
    frequency = request.form.get("frequency", "monthly")
    if frequency not in recurring_mod.FREQUENCIES:
        frequency = "monthly"
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO recurring (name, amount_cents, category_id, frequency,
                                      next_date, anchor_day)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, cents,
             txns.valid_category_id(conn, to_int(request.form.get("category_id"))),
             frequency, str(next_date), next_date.day),
        )
    recurring_mod.post_due_recurring()
    flash("Recurring item added.")
    return redirect(url_for("settings.index"))


@bp.route("/recurring/<int:item_id>/toggle", methods=["POST"])
def recurring_toggle(item_id):
    with get_conn() as conn:
        conn.execute("UPDATE recurring SET active = 1 - active WHERE id = ?", (item_id,))
    return redirect(url_for("settings.index"))


@bp.route("/recurring/<int:item_id>/delete", methods=["POST"])
def recurring_delete(item_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM recurring WHERE id = ?", (item_id,))
    flash("Recurring item deleted (already-posted transactions are kept).")
    return redirect(url_for("settings.index"))


@bp.route("/csv/save", methods=["POST"])
def csv_save():
    set_setting("csv_ignored_cardholders",
                request.form.get("csv_ignored_cardholders", "").strip())
    flash("CSV import settings saved.")
    return redirect(url_for("settings.index"))


@bp.route("/accounts/save", methods=["POST"])
def accounts_save():
    live = {str(account["id"]) for account in accounts.active()}
    for key in ("csv_default_account_id", "email_default_account_id"):
        if (key == "email_default_account_id"
                and not features.enabled("email_import")):
            continue
        chosen = request.form.get(key, "")
        set_setting(key, chosen if chosen in live else "")
    flash("Import account settings saved.")
    return redirect(url_for("settings.index"))


@bp.route("/paycheck/save", methods=["POST"])
def paycheck_save():
    try:
        cents = parse_amount_to_cents(request.form["pay_amount"])
    except ValueError:
        flash("Please enter a valid paycheck amount.")
        return redirect(url_for("settings.index"))
    frequency = request.form.get("pay_frequency", "biweekly")
    if frequency not in PAY_FREQUENCIES:
        frequency = "biweekly"
    anchor = parse_iso_date(request.form.get("pay_anchor_date")) or date.today()
    set_setting("pay_frequency", frequency)
    set_setting("pay_amount_cents", cents)
    set_setting("pay_anchor_date", str(anchor))
    _sync_paycheck_recurring(cents, frequency, anchor)
    recurring_mod.post_due_recurring()
    flash("Paycheck settings saved — paydays post automatically as income "
          "(manage this under Recurring items).")
    return redirect(url_for("settings.index"))


def _sync_paycheck_recurring(cents, frequency, anchor):
    item_id = to_int(get_setting("paycheck_recurring_id"))

    if cents <= 0:
        if item_id is not None:
            with get_conn() as conn:
                conn.execute("UPDATE recurring SET active = 0 WHERE id = ?",
                             (item_id,))
        return

    today = date.today()
    if anchor > today:
        next_date = anchor
    else:
        next_date, _ = current_pay_period(today, frequency, anchor)
    with get_conn() as conn:
        category = conn.execute(
            "SELECT id FROM categories WHERE kind = 'income' AND name = 'Paycheck'"
        ).fetchone()
        exists = item_id is not None and conn.execute(
            "SELECT 1 FROM recurring WHERE id = ?", (item_id,)
        ).fetchone()
        if exists:
            conn.execute(
                """UPDATE recurring SET amount_cents = ?, frequency = ?,
                       next_date = ?, anchor_day = ?, active = 1
                   WHERE id = ?""",
                (cents, frequency, str(next_date), anchor.day, item_id),
            )
        else:
            cur = conn.execute(
                """INSERT INTO recurring (name, amount_cents, category_id,
                                          frequency, next_date, anchor_day)
                   VALUES ('Paycheck', ?, ?, ?, ?, ?)""",
                (cents, category["id"] if category else None,
                 frequency, str(next_date), anchor.day),
            )
            item_id = cur.lastrowid
    if not exists:
        set_setting("paycheck_recurring_id", item_id)


@bp.route("/email/save", methods=["POST"])
def email_save():
    set_setting("email_address", request.form.get("email_address", "").strip())
    new_password = request.form.get("email_app_password", "").strip()
    unsealable = None
    if new_password:
        try:
            set_secret("email_app_password", new_password.replace(" ", ""))
        except vault.NoKey as exc:
            unsealable = str(exc)
    set_setting("imap_host",
                request.form.get("imap_host", "").strip() or "imap.gmail.com")
    if features.enabled("email_import"):
        set_setting("email_senders", request.form.get("email_senders", "").strip())
        set_setting("email_enabled", "1" if request.form.get("email_enabled") else "0")
        lookback = to_int(request.form.get("email_lookback_days"), 5) or 5
        set_setting("email_lookback_days", min(max(lookback, 1), 365))
    if unsealable:
        flash(f"Email settings saved, but the app password was NOT: {unsealable}.")
    elif new_password:
        flash("Email settings saved, including a new app password.")
    elif get_setting("email_app_password"):
        flash("Email settings saved. The stored app password was left "
              "unchanged — paste a new one into the field to replace it.")
    else:
        flash("Email settings saved.")
    return redirect(url_for("settings.index"))


@bp.route("/plaid/save", methods=["POST"])
@features.require("plaid")
def plaid_save():
    set_setting("plaid_client_id", request.form.get("plaid_client_id", "").strip())
    secret = request.form.get("plaid_secret", "").strip()
    if secret:
        try:
            set_secret("plaid_secret", secret)
        except vault.NoKey as exc:
            flash(f"Client id saved. The secret was NOT: {exc}.")
            return redirect(url_for("settings.index"))
        flash("Plaid settings saved, including a new secret.")
    elif get_setting("plaid_secret"):
        flash("Plaid settings saved. The stored secret was left unchanged "
              "— paste a new one into the secret field to replace it.")
    else:
        flash("Plaid settings saved.")
    return redirect(url_for("settings.index"))


@bp.route("/plaid/connect")
@features.require("plaid")
def plaid_connect_page():
    try:
        link_token = plaid.create_link_token(_plaid_user_id())
    except plaid.PlaidError as exc:
        flash(f"Plaid could not start a connection: {exc}")
        return redirect(url_for("settings.index") + "#plaid")
    return render_template("plaid_connect.html", link_token=link_token,
                           plaid_env=config.PLAID_ENV)


@bp.route("/plaid/connect", methods=["POST"])
@features.require("plaid")
def plaid_connect():
    public_token = request.form.get("public_token", "").strip()
    if not public_token:
        flash("Plaid sent nothing back, so nothing was connected.")
        return redirect(url_for("settings.index") + "#plaid")

    try:
        item_id, access_token = plaid.exchange_public_token(public_token)
        name = request.form.get("institution_name", "").strip()
        if not name and request.form.get("institution_id", "").strip():
            name = plaid.institution_name(request.form["institution_id"].strip())
        plaid.save_item(item_id, access_token, institution=name)
        created, updated = plaid.link_accounts(access_token, item_id,
                                               institution=name)
    except plaid.PlaidError as exc:
        flash(f"Connecting failed: {exc}")
        return redirect(url_for("settings.index") + "#plaid")

    synced = plaid.sync_item(item_id)
    message = (f"Connected {name or 'the institution'}: "
               f"{created} account(s) added, {updated} already here.")
    if synced["error"]:
        message += (f" The first sync did not finish ({synced['error']}) — "
                    f"use Sync now to try again.")
    else:
        message += f" {synced['added']} transaction(s) imported."
    flash(message)
    return redirect(url_for("settings.index") + "#plaid")


@bp.route("/plaid/sync", methods=["POST"])
@features.require("plaid")
def plaid_sync():
    item_id = request.form.get("item_id", "").strip()
    summary = plaid.sync_item(item_id)
    if summary["error"]:
        flash(f"Sync failed: {summary['error']}")
    else:
        parts = [f"{summary['added']} added"]
        if summary["updated"]:
            parts.append(f"{summary['updated']} updated")
        if summary["removed"]:
            parts.append(f"{summary['removed']} removed by the bank")
        if summary["pending"]:
            parts.append(f"{summary['pending']} still pending, listed separately")
        if summary["unlinked"]:
            parts.append(f"{summary['unlinked']} with no account here")
        if summary["accounts"]:
            parts.append(f"{summary['accounts']} new account(s)")
        parts.append("balances refreshed")
        if summary["liabilities"]:
            parts.append(f"{summary['liabilities']} rate(s) and due date(s)")
        if summary["holdings"]:
            parts.append(f"{summary['holdings']} holding(s)")
        if summary["sold"]:
            parts.append(f"{summary['sold']} position(s) no longer held")
        message = "Synced: " + ", ".join(parts) + "."
        if summary["liabilities_error"]:
            message += (" This bank does not report loan and card terms "
                        f"({summary['liabilities_error']}).")
        if summary["holdings_error"]:
            message += (" It does not report investment holdings "
                        f"({summary['holdings_error']}).")
        flash(message)
    return redirect(url_for("settings.index") + "#plaid")


@bp.route("/plaid/unlink", methods=["POST"])
@features.require("plaid")
def plaid_unlink():
    item_id = request.form.get("item_id", "").strip()
    try:
        told_plaid = plaid.disconnect(item_id)
    except plaid.PlaidError as exc:
        flash(f"Not disconnected: Plaid could not be told to remove this "
              f"connection ({exc}). It is still listed, so try again.")
        return redirect(url_for("settings.index") + "#plaid")
    if told_plaid:
        flash("Disconnected, and removed at Plaid. Its accounts and their "
              "transactions are still here, and bank emails for them will be "
              "imported again.")
    else:
        flash("Disconnected here, but Plaid could NOT be told: this instance "
              "has no working Plaid credentials or key for that connection. "
              "Remove the Item from your Plaid dashboard, or it stays active "
              "(and billed) there.")
    return redirect(url_for("settings.index") + "#plaid")


def _plaid_user_id():
    import hashlib

    return hashlib.sha256(
        str(config.DB_PATH.resolve()).encode()).hexdigest()[:24]


@bp.route("/email/fetch", methods=["POST"])
@features.require("email_import")
def email_fetch():
    try:
        summary = email_ingest.fetch_new_alerts()
        flash(summary)
    except Exception as exc:
        flash(f"Email fetch failed: {exc}")
    return redirect(url_for("settings.index"))


@bp.route("/notify/save", methods=["POST"])
@features.require("security_email")
def notify_save():
    set_setting("notify_email", request.form.get("notify_email", "").strip())
    set_setting("notify_operator_email",
                request.form.get("notify_operator_email", "").strip())
    set_setting("smtp_host",
                request.form.get("smtp_host", "").strip() or "smtp.gmail.com")
    port = to_int(request.form.get("smtp_port"), 587)
    set_setting("smtp_port", port if 1 <= port <= 65535 else 587)
    flash("Security email settings saved.")
    return redirect(url_for("settings.index") + "#security-email")


@bp.route("/theme/toggle", methods=["POST"])
def theme_toggle_mode():
    current = theme.current_theme()
    theme.save_theme(
        "dark" if current["mode"] == "light" else "light",
        current["palette"],
    )
    return redirect(came_from(url_for("dashboard.index")))


@bp.route("/theme", methods=["POST"])
def theme_save():
    theme.save_theme(request.form.get("mode"), request.form.get("palette"))
    flash("Theme saved.")
    return redirect(url_for("settings.index") + "#theme")


@bp.route("/name", methods=["POST"])
def owner_name_save():
    branding.set_owner_name(request.form.get("owner_name", ""))
    flash("Name saved." if branding.owner_name() else "Name cleared.")
    return redirect(url_for("settings.index") + "#theme")


@bp.route("/features", methods=["POST"])
def toggle_feature():
    name = request.form.get("name", "")
    try:
        features.set_enabled(name, request.form.get("enabled") == "1")
    except KeyError:
        abort(400, description="No such feature.")
    flash("Saved.", "ok")
    return redirect(url_for("settings.index"))


def _groups():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name FROM category_groups ORDER BY sort_order, name").fetchall()
        members = conn.execute(
            "SELECT group_id, COUNT(*) AS n FROM categories "
            "WHERE archived = 0 GROUP BY group_id").fetchall()
    counts = {r["group_id"]: r["n"] for r in members}
    return [{"id": r["id"], "name": r["name"], "count": counts.get(r["id"], 0)}
            for r in rows]


@bp.route("/groups/add", methods=["POST"])
def group_add():
    name = " ".join(request.form.get("name", "").split())[:40]
    if not name:
        flash("Give the group a name.")
        return redirect(url_for("settings.index") + "#groups")
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT 1 FROM category_groups WHERE name = ?", (name,)).fetchone()
        if existing:
            flash(f"There is already a group called {name}.")
            return redirect(url_for("settings.index") + "#groups")
        after = conn.execute(
            "SELECT COALESCE(MAX(sort_order), 0) + 1 FROM category_groups").fetchone()[0]
        conn.execute("INSERT INTO category_groups (name, sort_order) VALUES (?, ?)",
                     (name, after))
    flash(f"Group {name} added.")
    return redirect(url_for("settings.index") + "#groups")


@bp.route("/groups/<int:group_id>/delete", methods=["POST"])
def group_delete(group_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM category_groups WHERE id = ?", (group_id,))
    flash("Group removed. Its categories are now ungrouped.")
    return redirect(url_for("settings.index") + "#groups")


@bp.route("/groups/assign", methods=["POST"])
def group_assign():
    moved = 0
    with get_conn() as conn:
        valid = {r["id"] for r in conn.execute("SELECT id FROM category_groups")}
        for key, value in request.form.items():
            if not key.startswith("group_for_"):
                continue
            category_id = to_int(key.removeprefix("group_for_"))
            if category_id is None:
                continue
            group_id = to_int(value)
            if group_id is not None and group_id not in valid:
                group_id = None
            moved += conn.execute(
                "UPDATE categories SET group_id = ? WHERE id = ? AND group_id IS NOT ?",
                (group_id, category_id, group_id)).rowcount
    flash(f"Moved {moved} categor{'y' if moved == 1 else 'ies'}." if moved
          else "Nothing moved.")
    return redirect(url_for("settings.index") + "#groups")


@bp.route("/import/inspect", methods=["POST"])
@features.require("importer")
def import_inspect():
    try:
        found = old_db.inspect(request.form.get("source_path", "").strip())
    except old_db.ImportFailed as refused:
        flash(str(refused))
        return redirect(url_for("settings.index") + "#import")
    label = {"transactions": "transactions", "categories": "categories",
             "merchant_rules": "merchant rules", "recurring": "recurring items",
             "accounts": "accounts"}
    counts = ", ".join(f"{n} {label.get(table, table)}"
                       for table, n in found["counts"].items())
    flash(f"That database holds {counts}"
          + (f", from {found['earliest']} to {found['latest']}."
             if found["earliest"] else "."))
    return redirect(url_for("settings.index") + "#import")


@bp.route("/import/run", methods=["POST"])
@features.require("importer")
def import_run():
    upload = request.files.get("source_file")
    temp_path = None
    try:
        if upload is not None and upload.filename:
            handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
            try:
                upload.save(handle)
            finally:
                handle.close()
            temp_path = handle.name
            source = temp_path
        else:
            source = request.form.get("source_path", "").strip()
            if not source:
                flash("Give a path to the old database, or choose a file to upload.")
                return redirect(url_for("settings.index") + "#import")

        try:
            done = old_db.import_from(source)
        except old_db.ImportFailed as refused:
            flash(str(refused))
            return redirect(url_for("settings.index") + "#import")
    finally:
        if temp_path:
            Path(temp_path).unlink(missing_ok=True)

    if done["transactions"] == 0 and done["skipped"]:
        flash(f"Nothing new — all {done['skipped']} transactions were already "
              "imported from that database.")
    else:
        parts = [f"{done['transactions']} transactions"]
        for key, label in (("categories", "categories"), ("accounts", "accounts"),
                           ("rules", "merchant rules"), ("recurring", "recurring items")):
            if done[key]:
                parts.append(f"{done[key]} {label}")
        flash("Imported " + ", ".join(parts)
              + (f". {done['skipped']} were already here." if done["skipped"] else "."))
    return redirect(url_for("settings.index") + "#import")


_STYLEABLE = {
    "category": ("categories", "settings.index"),
    "account": ("accounts", "accounts.index"),
}


@bp.route("/style/<kind>/<int:row_id>", methods=["GET", "POST"])
def style_picker(kind, row_id):
    if kind not in _STYLEABLE:
        abort(404)
    table, home = _STYLEABLE[kind]

    raw_next = request.values.get("next", "")
    back = (raw_next if raw_next.startswith("/") and not raw_next.startswith("//")
            else url_for(home))

    with get_conn() as conn:
        row = conn.execute(
            f"SELECT id, name, kind, icon, colour FROM {table} WHERE id = ?",
            (row_id,)).fetchone()
        if row is None:
            abort(404)

        if request.method == "POST":
            conn.execute(
                f"UPDATE {table} SET icon = ?, colour = ? WHERE id = ?",
                (glyphs.normalize_icon(request.form.get("icon", "")),
                 glyphs.normalize_hue(request.form.get("colour", "")),
                 row_id))
            flash(f'Updated the icon and colour for "{row["name"]}".')
            return redirect(back)

    return render_template(
        "style_picker.html", row=row, kind=kind, back=back,
        noun=("Category" if kind == "category" else "Account"))
