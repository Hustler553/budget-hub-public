import csv
import hashlib
import io
import json
import math
import re
from datetime import datetime

from budget.db import get_setting, set_setting
from budget.services import accounts
from budget.services.txns import add_transaction

REQUIRED_COLUMNS = {"Date", "Cardholder", "Amount", "Merchant"}

CREDIT_TYPES = ("refund", "return", "credit", "reward")
SKIP_TYPES = ("payment",)


def looks_like_robinhood_csv(text):
    stripped = text.strip()
    if not stripped:
        return False
    try:
        header = next(csv.reader(io.StringIO(stripped)))
    except (csv.Error, StopIteration):
        return False
    return REQUIRED_COLUMNS.issubset(col.strip() for col in header)


def _iso_date(date_str):
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(date_str, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def import_csv_text(text):
    ignored_names = [n.strip().lower() for n in
                     get_setting("csv_ignored_cardholders").split(",") if n.strip()]

    counts = {"imported": 0, "ignored_cardholder": 0, "duplicate": 0, "skipped": 0}

    account_id = accounts.resolve_for_source("csv")

    for row in csv.DictReader(io.StringIO(text)):
        status = (row.get("Status") or "").strip().lower()
        row_type = (row.get("Type") or "").strip().lower()
        cardholder = (row.get("Cardholder") or "").strip()
        merchant = (row.get("Merchant") or row.get("Description") or "").strip()
        date_str = (row.get("Date") or "").strip()

        iso_date = _iso_date(date_str) if date_str else None
        if iso_date is None or (status and status != "posted") or row_type in SKIP_TYPES:
            counts["skipped"] += 1
            continue
        if any(name in cardholder.lower() for name in ignored_names):
            counts["ignored_cardholder"] += 1
            continue

        try:
            amount = float((row.get("Amount") or "").replace("$", "").replace(",", ""))
        except ValueError:
            counts["skipped"] += 1
            continue
        if not math.isfinite(amount):
            counts["skipped"] += 1
            continue
        cents = round(abs(amount) * 100)
        if row_type not in CREDIT_TYPES and amount >= 0:
            cents = -cents

        fingerprint = "|".join([date_str, row.get("Time") or "", cardholder,
                                row.get("Amount") or "", merchant])
        external_id = "rhcsv:" + hashlib.sha1(fingerprint.encode()).hexdigest()[:16]

        created = add_transaction(
            txn_date=iso_date,
            amount_cents=cents,
            merchant=merchant,
            note=cardholder,
            account_id=account_id,
            source="csv",
            external_id=external_id,
        )
        if created:
            counts["imported"] += 1
        else:
            counts["duplicate"] += 1

    return counts


def summarize(counts):
    parts = [f"{counts['imported']} imported"]
    if counts["duplicate"]:
        parts.append(f"{counts['duplicate']} already imported")
    if counts["ignored_cardholder"]:
        parts.append(f"{counts['ignored_cardholder']} skipped (ignored cardholder)")
    if counts["skipped"]:
        parts.append(f"{counts['skipped']} skipped (pending, payment, or unreadable)")
    return "CSV: " + ", ".join(parts) + "."


PREVIEW_ROWS = 5

_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%Y/%m/%d",
                 "%d-%b-%Y", "%d %b %Y", "%b %d, %Y", "%B %d, %Y", "%Y%m%d")
_DAY_FIRST_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d.%m.%Y",
                      "%Y/%m/%d", "%d-%b-%Y", "%d %b %Y", "%b %d, %Y", "%Y%m%d")

_ROLE_HINTS = {
    "date": ("transaction date", "posting date", "post date", "posted date", "date"),
    "amount": ("amount", "value"),
    "debit": ("debit", "withdrawal", "money out", "paid out"),
    "credit": ("credit", "deposit", "money in", "paid in"),
    "merchant": ("description", "merchant", "payee", "name", "details", "memo"),
    "note": ("memo", "note", "reference", "category"),
}
ROLES = tuple(_ROLE_HINTS)


class CsvRefused(ValueError):
    pass


def read_table(text):
    stripped = text.strip()
    if not stripped:
        raise CsvRefused("That file is empty.")
    try:
        dialect = csv.Sniffer().sniff(stripped[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    try:
        table = [row for row in csv.reader(io.StringIO(stripped), dialect)
                 if any(cell.strip() for cell in row)]
    except csv.Error as exc:
        raise CsvRefused(f"That file could not be read as a CSV ({exc}).") from None
    if len(table) < 2 or len(table[0]) < 2:
        raise CsvRefused("That file has no rows of columns to import. It needs "
                         "a header row and at least one row beneath it.")
    headers = [cell.strip() for cell in table[0]]
    return headers, table[1:]


def parse_money(text):
    raw = (text or "").strip()
    if not raw:
        return None
    negative = raw.startswith("(") and raw.endswith(")")
    negative = negative or raw.startswith("-") or raw.endswith("-") or "\u2212" in raw
    cleaned = re.sub(r"[^0-9.]", "", raw)
    if not cleaned or cleaned.count(".") > 1:
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if not math.isfinite(value) or value >= 10 ** 13:
        return None
    cents = round(value * 100)
    return -cents if negative else cents


def parse_date(text, day_first=False):
    raw = (text or "").strip()
    raw = re.split(r"[T ](?=\d{1,2}:)", raw)[0]
    for fmt in (_DAY_FIRST_FORMATS if day_first else _DATE_FORMATS):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def header_key(headers):
    joined = "|".join(h.strip().casefold() for h in headers)
    return "csv_mapping." + hashlib.sha1(joined.encode()).hexdigest()[:16]


def guess_mapping(headers, rows):
    stored = get_setting(header_key(headers), "")
    if stored:
        try:
            saved = json.loads(stored)
            if isinstance(saved, dict):
                return saved
        except ValueError:
            pass

    lowered = [h.casefold() for h in headers]
    mapping = {}
    taken = set()
    for role in ROLES:
        mapping[role] = None
        for hint in _ROLE_HINTS[role]:
            found = next((i for i, name in enumerate(lowered)
                          if hint in name and i not in taken), None)
            if found is not None:
                mapping[role] = found
                taken.add(found)
                break
    if mapping["debit"] is not None and mapping["credit"] is not None:
        mapping["amount"] = None

    negatives = positives = 0
    if mapping["amount"] is not None:
        for row in rows[:200]:
            if mapping["amount"] < len(row):
                cents = parse_money(row[mapping["amount"]])
                if cents:
                    negatives += cents < 0
                    positives += cents > 0
    mapping["spending"] = "negative" if negatives >= positives else "positive"
    mapping["day_first"] = False
    return mapping


def clean_mapping(form, column_count):
    def column(role):
        raw = (form.get(role) or "").strip()
        if not raw.isascii() or not raw.isdigit():
            return None
        index = int(raw)
        return index if index < column_count else None

    mapping = {role: column(role) for role in ROLES}
    if mapping["date"] is None:
        raise CsvRefused("Say which column holds the date.")
    if mapping["merchant"] is None:
        raise CsvRefused("Say which column holds the description or merchant.")
    if mapping["amount"] is None and (mapping["debit"] is None
                                      and mapping["credit"] is None):
        raise CsvRefused("Say which column holds the amount, or which hold the "
                         "money out and money in.")
    mapping["spending"] = ("positive" if form.get("spending") == "positive"
                           else "negative")
    mapping["day_first"] = bool(form.get("day_first"))
    return mapping


def import_mapped(text, mapping, account_id):
    headers, rows = read_table(text)
    counts = {"imported": 0, "duplicate": 0, "skipped": 0,
              "ignored_cardholder": 0}
    seen = {}

    def cell(row, index):
        return row[index].strip() if index is not None and index < len(row) else ""

    for row in rows:
        iso = parse_date(cell(row, mapping["date"]), mapping["day_first"])
        if mapping["amount"] is not None:
            cents = parse_money(cell(row, mapping["amount"]))
            if cents is not None and mapping["spending"] == "positive":
                cents = -cents
        else:
            out = parse_money(cell(row, mapping["debit"]))
            came_in = parse_money(cell(row, mapping["credit"]))
            if out:
                cents = -abs(out)
            elif came_in:
                cents = abs(came_in)
            else:
                cents = None
        if iso is None or not cents:
            counts["skipped"] += 1
            continue

        merchant = cell(row, mapping["merchant"])
        triple = (iso, cents, merchant.casefold())
        seen[triple] = seen.get(triple, 0) + 1
        fingerprint = f"{account_id}|{iso}|{cents}|{merchant.casefold()}|{seen[triple]}"
        created = add_transaction(
            txn_date=iso,
            amount_cents=cents,
            merchant=merchant,
            note=cell(row, mapping["note"]),
            account_id=account_id,
            source="csv",
            external_id="csv:" + hashlib.sha1(fingerprint.encode()).hexdigest()[:20],
        )
        counts["imported" if created else "duplicate"] += 1

    if counts["imported"] or counts["duplicate"]:
        set_setting(header_key(headers), json.dumps(mapping))
    return counts
