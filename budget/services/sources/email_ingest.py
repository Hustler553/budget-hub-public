import email
import email.utils
import imaplib
import logging
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from email.header import decode_header, make_header

from budget.services.sources import csv_import
from budget.services import accounts, features
from budget.db import get_conn, get_secret, get_setting
from budget.services.txns import add_transaction
from budget.utils import parse_iso_date

log = logging.getLogger(__name__)


AMOUNT_RE = re.compile(r"\$\s?([\d,]+\.\d{2})")

_STOP = r"(?:\s+(?:on|was|when|date|card|of|for)\b|[,;\n]|\.(?:\s|$)|$)"

MERCHANT_PATTERNS = [
    re.compile(r"(?:merchant|where|paid to|payee)\s*[:\-]\s*(.+?)" + _STOP, re.IGNORECASE),
    re.compile(r"transaction (?:of \$[\d,.]+\s*)?(?:at|with|to)\s+(.+?)" + _STOP, re.IGNORECASE),
    re.compile(r"\$[\d,]+\.\d{2}\s+(?:at|to|with)\s+(.+?)" + _STOP, re.IGNORECASE),
    re.compile(r"(?:purchase|charge|payment)\s+(?:at|to|from)\s+(.+?)" + _STOP, re.IGNORECASE),
]

CARD_RE = re.compile(r"(?:card|account)\s*(?:ending|x+|\*+)?\s*(?:in)?\s*:?\s*(\d{4})", re.IGNORECASE)

ALERT_HINTS = ("transaction", "purchase", "charge", "spent", "debit",
               "payment", "withdrawal", "was used", "card ending")


def parse_alert(subject, body):
    text = subject + "\n" + body
    looks_like_alert = any(hint in text.lower() for hint in ALERT_HINTS)

    amount_cents = None
    match = AMOUNT_RE.search(text)
    if match:
        amount_cents = round(float(match.group(1).replace(",", "")) * 100)

    merchant = None
    for pattern in MERCHANT_PATTERNS:
        m = pattern.search(text)
        if m:
            candidate = m.group(1).strip().strip(".").strip()
            if candidate and len(candidate) < 60 and not AMOUNT_RE.fullmatch(candidate):
                merchant = re.sub(r"\s+", " ", candidate)
                break

    mask = ""
    m = CARD_RE.search(text)
    if m:
        mask = m.group(1)

    return {"looks_like_alert": looks_like_alert, "amount_cents": amount_cents,
            "merchant": merchant, "mask": mask}


def _decode(value):
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (LookupError, UnicodeError, ValueError):
        return str(value)


def _bytes_to_text(payload, charset):
    try:
        return payload.decode(charset or "utf-8", errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _strip_html(html):
    html = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<[^>]+>", " ", html)
    html = re.sub(r"&nbsp;|&#160;", " ", html)
    html = re.sub(r"&amp;", "&", html)
    return re.sub(r"[ \t]+", " ", html)


def _get_body_text(msg):
    def to_text(part):
        payload = part.get_payload(decode=True)
        if payload is None:
            return ""
        return _bytes_to_text(payload, part.get_content_charset())

    plain = ""
    html = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_filename():
                continue
            ctype = part.get_content_type()
            if ctype == "text/plain" and not plain:
                plain = to_text(part)
            elif ctype == "text/html" and not html:
                html = to_text(part)
    elif msg.get_content_type() == "text/html":
        html = to_text(msg)
    else:
        plain = to_text(msg)

    if plain and AMOUNT_RE.search(plain):
        return plain
    if html:
        return _strip_html(html)
    return plain


def _get_csv_attachments(msg):
    attachments = []
    for part in msg.walk():
        filename = (part.get_filename() or "").lower()
        ctype = part.get_content_type()
        if filename.endswith(".csv") or ctype in ("text/csv", "application/csv"):
            payload = part.get_payload(decode=True)
            if payload:
                attachments.append(payload.decode("utf-8-sig", errors="replace"))
    return attachments


CSV_LINK_RE = re.compile(
    r'href="(https://(?:[a-z0-9.-]+\.)?robinhood\.com/[^"]*\.csv[^"]*)"',
    re.IGNORECASE,
)


def _get_csv_links(msg):
    links = []
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            payload = part.get_payload(decode=True)
            if payload:
                html = _bytes_to_text(payload, part.get_content_charset())
                links.extend(CSV_LINK_RE.findall(html))
    return links


def _is_robinhood(host):
    return host == "robinhood.com" or host.endswith(".robinhood.com")


class _RefuseForeignRedirects(urllib.request.HTTPRedirectHandler):

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or not _is_robinhood(host):
            raise urllib.error.HTTPError(
                newurl, code,
                f"CSV link tried to redirect to {parsed.scheme}://{host} — "
                "refused before connecting",
                headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download_csv(url, max_bytes=5_000_000):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not _is_robinhood(parsed.hostname or ""):
        raise ValueError(f"CSV link is not an https robinhood.com URL ({url!r})")
    req = urllib.request.Request(url, headers={"User-Agent": "budget-tool"})
    opener = urllib.request.build_opener(_RefuseForeignRedirects)
    with opener.open(req, timeout=30) as resp:
        host = urllib.parse.urlparse(resp.url).hostname or ""
        if not _is_robinhood(host):
            raise ValueError(f"CSV link redirected away from robinhood.com ({host})")
        data = resp.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("CSV download exceeds the size safety limit")
    return data.decode("utf-8-sig", errors="replace")


_IMAP_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _imap_date(d):
    return f"{d.day:02d}-{_IMAP_MONTHS[d.month - 1]}-{d.year}"


def _search_start(today, lookback_days, last_poll):
    start = today - timedelta(days=lookback_days)
    last = parse_iso_date(str(last_poll)[:10])
    if last is not None:
        start = min(start, last - timedelta(days=2))
    return start


def fetch_new_alerts():
    address = get_setting("email_address")
    password = get_secret("email_app_password")
    host = get_setting("imap_host", "imap.gmail.com").strip() or "imap.gmail.com"
    senders = [s.strip().replace('"', "")
               for s in get_setting("email_senders").split(",") if s.strip()]
    try:
        lookback_days = max(1, int(get_setting("email_lookback_days", "5")))
    except ValueError:
        lookback_days = 5

    if not address or not password:
        return "Email not configured (set address and app password in Settings)."

    since_day = _search_start(date.today(), lookback_days,
                              get_setting("email_last_poll"))
    since = _imap_date(since_day)

    mail = imaplib.IMAP4_SSL(host, ssl_context=ssl.create_default_context())
    try:
        mail.login(address, password)
        mail.select("INBOX", readonly=True)

        uids = set()
        if senders:
            for sender in senders:
                status, data = mail.uid("SEARCH", None, "SINCE", since, "FROM", f'"{sender}"')
                if status == "OK" and data[0]:
                    uids.update(data[0].split())
        else:
            status, data = mail.uid("SEARCH", None, "SINCE", since)
            if status == "OK" and data[0]:
                uids.update(data[0].split())

        account = address.lower()
        with get_conn() as conn:
            seen = {row["uid"] for row in conn.execute("SELECT uid FROM email_log").fetchall()}
        new_uids = sorted((u for u in uids if f"{account}:{u.decode()}" not in seen),
                          key=int)

        parsed = queued = ignored = 0
        for uid in new_uids:
            status, data = mail.uid("FETCH", uid, "(BODY.PEEK[])")
            if status != "OK" or not data or not isinstance(data[0], tuple):
                continue
            try:
                counts = _handle_message(uid, data[0][1], account, senders)
            except Exception as exc:
                log.warning("Could not process one email: %s",
                            exc.__class__.__name__)
                with get_conn() as conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO email_log "
                        "(uid, subject, sender, received_at, status, body_snippet) "
                        "VALUES (?, '(unreadable email)', '', ?, 'review', ?)",
                        (f"{account}:{uid.decode()}", str(date.today()),
                         "This email could not be read automatically "
                         f"({exc.__class__.__name__}). Open it in your mailbox "
                         "and add the transaction by hand if it is one."))
                counts = (0, 1, 0)
            parsed += counts[0]
            queued += counts[1]
            ignored += counts[2]

        summary = (f"Checked {len(new_uids)} new email(s) since {since_day}: "
                   f"{parsed} imported, {queued} need review, {ignored} ignored.")
    finally:
        try:
            mail.logout()
        except Exception:
            pass

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('email_last_poll', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),),
        )
        conn.execute("DELETE FROM settings WHERE key = 'email_last_error'")
    return summary


def _handle_message(uid, raw, account, senders):
    parsed = queued = ignored = 0
    msg = email.message_from_bytes(raw)
    subject = _decode(msg.get("Subject"))
    sender = _decode(msg.get("From"))
    body = _get_body_text(msg)

    try:
        msg_date = email.utils.parsedate_to_datetime(msg.get("Date")).date()
    except Exception:
        msg_date = date.today()

    uid_str = f"{account}:{uid.decode()}"

    csv_texts = _get_csv_attachments(msg)
    csv_links = _get_csv_links(msg)
    for link in csv_links:
        try:
            csv_texts.append(_download_csv(link))
        except Exception:
            pass

    csv_counts = None
    for attachment in csv_texts:
        if csv_import.looks_like_robinhood_csv(attachment):
            counts = csv_import.import_csv_text(attachment)
            if csv_counts is None:
                csv_counts = counts
            else:
                for key in counts:
                    csv_counts[key] += counts[key]
    if csv_counts is not None:
        parsed += csv_counts["imported"]
        with get_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO email_log "
                "(uid, subject, sender, received_at, status, body_snippet) "
                "VALUES (?, ?, ?, ?, 'parsed', ?)",
                (uid_str, subject[:200], sender[:200], str(msg_date),
                 csv_import.summarize(csv_counts)),
            )
        return parsed, queued, ignored

    if csv_links:
        instructions = (
            "Robinhood export email. Automatic import only works if the "
            "tool fetches this within 7 days of it arriving — the "
            "download link expires after that. If it's expired, open the "
            "link below, download the CSV, then use “Import Robinhood "
            "CSV” on the Transactions page:\n\n" + "\n".join(csv_links)
        )
        with get_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO email_log "
                "(uid, subject, sender, received_at, status, body_snippet) "
                "VALUES (?, ?, ?, ?, 'review', ?)",
                (uid_str, subject[:200], sender[:200], str(msg_date),
                 instructions[:1500]),
            )
        queued += 1
        return parsed, queued, ignored

    result = parse_alert(subject, body)

    trusted = bool(senders) or result["looks_like_alert"]
    fully_parsed = bool(result["amount_cents"] and result["merchant"])
    preauth_hold = result["amount_cents"] == 100

    review_prefix = ""
    account_id = accounts.resolve_for_source("email", mask=result["mask"])
    plaid_covers_it = accounts.is_plaid_backed(account_id)

    if trusted and fully_parsed and not preauth_hold and plaid_covers_it:
        log_status = "ignored"
        ignored += 1
    elif trusted and fully_parsed and not preauth_hold:
        add_transaction(
            txn_date=msg_date,
            amount_cents=-abs(result["amount_cents"]),
            merchant=result["merchant"],
            account_id=account_id,
            source="email",
            external_id=f"email:{uid_str}",
        )
        log_status = "parsed"
        parsed += 1
    elif preauth_hold and trusted and result["merchant"]:
        log_status = "review"
        queued += 1
        review_prefix = (
            "[$1.00 hold — likely a gas station or pre-authorization. "
            "The final amount posts in a few days; update the amount "
            "below before importing.]\n\n"
        )
    elif result["looks_like_alert"] and (senders or result["amount_cents"]):
        log_status = "review"
        queued += 1
    else:
        log_status = "ignored"
        ignored += 1

    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO email_log "
            "(uid, subject, sender, received_at, status, body_snippet, amount_cents, "
            " merchant, mask) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (uid_str, subject[:200], sender[:200], str(msg_date), log_status,
             (review_prefix + body)[:1500], result["amount_cents"], result["merchant"],
             result["mask"]),
        )
    return parsed, queued, ignored


def poll_safely():
    if not features.enabled("email_import") or get_setting("email_enabled") != "1":
        return
    try:
        fetch_new_alerts()
    except Exception as exc:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('email_last_error', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (f"{datetime.now():%Y-%m-%d %H:%M} — {exc}",),
            )
