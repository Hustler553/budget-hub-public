import calendar
import re
from datetime import date, timedelta


def cents_to_str(cents, with_sign=False):
    if cents is None:
        cents = 0
    sign = "-" if cents < 0 else ("+" if with_sign else "")
    dollars = abs(cents) / 100
    return f"{sign}${dollars:,.2f}"


def parse_amount_to_cents(text):
    raw = str(text)
    if re.search(r",(?!\d{3}(?!\d))", raw) or re.search(r"\d[A-Za-z]+\d", raw):
        raise ValueError(f"Not an amount: {text!r}")
    cleaned = re.sub(r"[^\d.]", "", raw)
    if not cleaned:
        raise ValueError(f"Not an amount: {text!r}")
    try:
        cents = round(float(cleaned) * 100)
    except (ValueError, OverflowError):
        raise ValueError(f"Not an amount: {text!r}") from None
    if not -10**15 < cents < 10**15:
        raise ValueError(f"Amount out of range: {text!r}")
    return cents


BASIS_POINTS_PER_PERCENT = 100
MAX_PERCENT_BP = 100 * BASIS_POINTS_PER_PERCENT

RATE_E4_PER_PERCENT = 10_000


def parse_percent_to_bp(text):
    cleaned = re.sub(r"[^\d.]", "", str(text))
    if not cleaned:
        return 0
    try:
        bp = round(float(cleaned) * BASIS_POINTS_PER_PERCENT)
    except (ValueError, OverflowError):
        return 0
    return min(max(bp, 0), MAX_PERCENT_BP)


def e4_to_rate_str(units):
    if units is None:
        return ""
    return f"{units / RATE_E4_PER_PERCENT:g}"


def parse_percent_to_e4(text):
    cleaned = re.sub(r"[^\d.]", "", str(text))
    if not cleaned:
        return 0
    try:
        units = round(float(cleaned) * RATE_E4_PER_PERCENT)
    except (ValueError, OverflowError):
        return 0
    return min(max(units, 0), 100 * RATE_E4_PER_PERCENT)


E8_SCALE = 100_000_000


def e8_to_str(value):
    if value is None:
        return ""
    sign = "-" if value < 0 else ""
    whole, frac = divmod(abs(value), E8_SCALE)
    if frac == 0:
        return f"{sign}{whole:,}"
    return f"{sign}{whole:,}.{frac:08d}".rstrip("0")


def bp_to_percent_str(bp):
    if not bp:
        return ""
    return f"{bp / BASIS_POINTS_PER_PERCENT:g}"


def bp_to_rate_str(bp):
    if bp is None:
        return ""
    return f"{bp / BASIS_POINTS_PER_PERCENT:g}"


def to_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_iso_date(value):
    try:
        return date.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        return None


def month_of(d):
    return d.strftime("%Y-%m")


def normalize_month(value):
    if value and re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
        return value
    return month_of(date.today())


def month_bounds(month_str):
    year, month = int(month_str[:4]), int(month_str[5:7])
    last_day = calendar.monthrange(year, month)[1]
    return f"{month_str}-01", f"{month_str}-{last_day:02d}"


def shift_month(month_str, delta):
    year, month = int(month_str[:4]), int(month_str[5:7])
    total = year * 12 + (month - 1) + delta
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def month_label(month_str):
    return date(int(month_str[:4]), int(month_str[5:7]), 1).strftime("%B %Y")


def add_months(d, months, anchor_day=None):
    if anchor_day is None:
        anchor_day = d.day
    total = d.year * 12 + (d.month - 1) + months
    year, month = total // 12, total % 12 + 1
    day = min(anchor_day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


PAY_FREQUENCIES = ("weekly", "biweekly", "semimonthly", "monthly")


def current_pay_period(today, frequency, anchor):
    if frequency in ("weekly", "biweekly"):
        step = 7 if frequency == "weekly" else 14
        periods_elapsed = (today - anchor).days // step
        start = anchor + timedelta(days=periods_elapsed * step)
        return start, start + timedelta(days=step)

    if frequency == "semimonthly":
        if today.day >= 15:
            start = today.replace(day=15)
            end = add_months(today.replace(day=1), 1)
        else:
            start = today.replace(day=1)
            end = today.replace(day=15)
        return start, end

    this_month_payday = add_months(today.replace(day=1), 0, anchor_day=anchor.day)
    if today >= this_month_payday:
        return this_month_payday, add_months(this_month_payday, 1, anchor_day=anchor.day)
    prev = add_months(this_month_payday, -1, anchor_day=anchor.day)
    return prev, this_month_payday
