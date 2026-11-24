from budget.utils import BASIS_POINTS_PER_PERCENT
from budget.utils import RATE_E4_PER_PERCENT as _RATE_E4_PER_PERCENT

RATE_E4_PER_PERCENT = _RATE_E4_PER_PERCENT

MONTHS_PER_YEAR = 12

MAX_PERIODS = 600


class ImpossibleLoan(ValueError):
    pass


def bp_to_e4(basis_points):
    if basis_points is None:
        return None
    return basis_points * (RATE_E4_PER_PERCENT // BASIS_POINTS_PER_PERCENT)


def _round_half_away(numerator, denominator):
    if denominator < 0:
        numerator, denominator = -numerator, -denominator
    if numerator >= 0:
        return (2 * numerator + denominator) // (2 * denominator)
    return -((-2 * numerator + denominator) // (2 * denominator))


def period_interest_cents(balance_cents, annual_rate_e4,
                          periods_per_year=MONTHS_PER_YEAR):
    if balance_cents == 0 or annual_rate_e4 == 0:
        return 0
    return _round_half_away(
        balance_cents * annual_rate_e4,
        100 * RATE_E4_PER_PERCENT * periods_per_year)


def level_payment_cents(principal_cents, annual_rate_e4, periods,
                        periods_per_year=MONTHS_PER_YEAR):
    if periods <= 0:
        raise ValueError("A loan needs at least one payment.")
    if periods > MAX_PERIODS:
        raise ValueError(f"{periods} payments is longer than this models "
                         f"({MAX_PERIODS} maximum).")
    if principal_cents <= 0:
        return 0
    if annual_rate_e4 == 0:
        return -(-principal_cents // periods)

    rate = annual_rate_e4 / (100 * RATE_E4_PER_PERCENT * periods_per_year)
    exact = principal_cents * rate / (1 - (1 + rate) ** -periods)
    return _round_half_away(round(exact * 1000), 1000)


def schedule(principal_cents, annual_rate_e4, periods=None, payment_cents=None,
             periods_per_year=MONTHS_PER_YEAR, extra_cents=0):
    if principal_cents <= 0:
        return []
    term = None
    if payment_cents is None:
        if periods is None:
            raise ValueError("Give a term or a payment.")
        payment_cents = level_payment_cents(
            principal_cents, annual_rate_e4, periods, periods_per_year)
        term = periods
    if extra_cents < 0:
        raise ValueError("To pay less, give a smaller payment_cents.")
    payment_cents += extra_cents
    if payment_cents <= 0:
        raise ImpossibleLoan("A payment of nothing never clears a debt.")

    first_interest = period_interest_cents(
        principal_cents, annual_rate_e4, periods_per_year)
    if payment_cents <= first_interest and principal_cents > payment_cents:
        raise ImpossibleLoan(
            f"A payment of {payment_cents} cents does not cover the "
            f"{first_interest} cents of interest the first period accrues, so "
            "the balance grows instead of falling.")

    rows = []
    balance = principal_cents
    while balance > 0:
        if len(rows) >= MAX_PERIODS:
            raise ImpossibleLoan(
                f"Still owing after {MAX_PERIODS} payments.")
        interest = period_interest_cents(balance, annual_rate_e4,
                                         periods_per_year)
        last = (balance + interest <= payment_cents
                or (term is not None and len(rows) + 1 >= term))
        if last:
            paid, principal_part, balance = balance + interest, balance, 0
        else:
            paid = payment_cents
            principal_part = paid - interest
            balance -= principal_part
        rows.append({
            "period": len(rows) + 1,
            "payment_cents": paid,
            "interest_cents": interest,
            "principal_cents": principal_part,
            "balance_cents": balance,
        })
    return rows


def summarise(rows):
    if not rows:
        return {"periods": 0, "total_paid_cents": 0, "total_interest_cents": 0,
                "final_payment_cents": 0}
    return {
        "periods": len(rows),
        "total_paid_cents": sum(r["payment_cents"] for r in rows),
        "total_interest_cents": sum(r["interest_cents"] for r in rows),
        "final_payment_cents": rows[-1]["payment_cents"],
    }


def by_year(rows):
    years = []
    for row in rows:
        index = (row["period"] - 1) // MONTHS_PER_YEAR
        if index == len(years):
            years.append({"year": index + 1, "payments": 0,
                          "interest_cents": 0, "principal_cents": 0,
                          "payment_cents": 0, "balance_cents": 0})
        year = years[index]
        year["payments"] += 1
        year["interest_cents"] += row["interest_cents"]
        year["principal_cents"] += row["principal_cents"]
        year["payment_cents"] += row["payment_cents"]
        year["balance_cents"] = row["balance_cents"]
    return years


def compare_extra_payment(principal_cents, annual_rate_e4, payment_cents,
                          extra_cents, periods_per_year=MONTHS_PER_YEAR):
    plain = summarise(schedule(principal_cents, annual_rate_e4,
                               payment_cents=payment_cents,
                               periods_per_year=periods_per_year))
    faster = summarise(schedule(principal_cents, annual_rate_e4,
                                payment_cents=payment_cents,
                                periods_per_year=periods_per_year,
                                extra_cents=extra_cents))
    return {
        "without": plain,
        "with": faster,
        "periods_saved": plain["periods"] - faster["periods"],
        "interest_saved_cents": (plain["total_interest_cents"]
                                 - faster["total_interest_cents"]),
    }


COMPOUNDING = {"annually": 1, "semiannually": 2, "quarterly": 4, "monthly": 12}

DEFAULT_COMPOUNDING = "annually"

GROWTH_E12 = 10 ** 12


def _monthly_growth_e12(annual_rate_e4, compounds_per_year):
    if annual_rate_e4 == 0:
        return 0
    nominal = annual_rate_e4 / (100 * RATE_E4_PER_PERCENT)
    monthly = (1 + nominal / compounds_per_year) ** (
        compounds_per_year / MONTHS_PER_YEAR) - 1
    return int(round(monthly * GROWTH_E12))


CARRY = 10 ** 6


def month_interest_cents(balance_cents, monthly_growth_e12):
    if balance_cents == 0 or monthly_growth_e12 == 0:
        return 0
    return _round_half_away(balance_cents * monthly_growth_e12, GROWTH_E12)


def _compounds_per_year(compounding):
    if isinstance(compounding, str):
        try:
            return COMPOUNDING[compounding]
        except KeyError:
            raise ValueError(
                f"{compounding!r} is not a compounding frequency. One of: "
                + ", ".join(COMPOUNDING)) from None
    if compounding not in COMPOUNDING.values():
        raise ValueError(f"Interest cannot be compounded {compounding} times a "
                         "year in whole months.")
    return compounding


def growth_rows(initial_cents, contribution_cents, annual_rate_e4, months,
                compounding=DEFAULT_COMPOUNDING, at_period_start=False):
    if months < 0:
        raise ValueError("A projection needs a period count of at least zero.")
    if months > MAX_PERIODS:
        raise ValueError(f"{months} periods is longer than this models "
                         f"({MAX_PERIODS} maximum).")
    growth = _monthly_growth_e12(annual_rate_e4,
                                 _compounds_per_year(compounding))

    rows = []
    carried = initial_cents * CARRY
    contribution = contribution_cents * CARRY
    paid_in = initial_cents
    for month in range(1, months + 1):
        if at_period_start:
            carried += contribution
            paid_in += contribution_cents
        interest = _round_half_away(carried * growth, GROWTH_E12)
        carried += interest
        if not at_period_start:
            carried += contribution
            paid_in += contribution_cents
        balance_cents = _round_half_away(carried, CARRY)
        rows.append({
            "period": month,
            "interest_cents": _round_half_away(interest, CARRY),
            "paid_in_cents": paid_in,
            "balance_cents": balance_cents,
            "earned_cents": balance_cents - paid_in,
        })
    return rows


def future_value_cents(initial_cents, contribution_cents, annual_rate_e4,
                       months, compounding=DEFAULT_COMPOUNDING,
                       at_period_start=False):
    rows = growth_rows(initial_cents, contribution_cents, annual_rate_e4,
                       months, compounding, at_period_start)
    return rows[-1]["balance_cents"] if rows else initial_cents
