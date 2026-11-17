"""Day counts, yield bases and the market conventions of each Indian rates instrument.

Sources, quotes and the reading behind each rule are in ``docs/conventions.md``.
The short version:

* Dated G-secs and SDLs accrue on FIMMDA's "European 30/360" (30E/360): the
  31st counts as the 30th and the end of February is left alone (FIMMDA
  Handbook of Market Practices 2026, 6.6.1). ``days_30e_360`` reproduces every
  row of the handbook's broken-period table.
* T-bills, cash management bills, CP and CD are priced on a rear-ended
  Actual/365 simple yield, prices rounded to 4 decimals (handbook 6.4, 6.5,
  6.7 and 7.3.4).
* ``days_30_360_us`` (30/360 US with the end-of-February rules, close to
  Excel's basis 0) is kept because RBI's G-sec primer reportedly uses basis 0.
  The handbook's table rules it out for G-secs.
* Actual/365 fixed and Actual/Actual (ICMA) are here for corporate bonds,
  which are not built yet.

``convert_yield`` and ``money_market_to_annual`` convert between yield bases.
They moved here from ``curves``, which re-exports them.

Each instrument type has a frozen conventions object: ``GSEC``, ``SDL``,
``STRIPS`` (``BondConventions``) and ``TBILL``, ``CMB``, ``CP``, ``CD``
(``DiscountConventions``). Rules no source settles are named fields whose
value is the market-standard reading: ``final_period``, ``fraction_rule`` and
``time_basis`` for bonds, ``price_rounding`` for discount instruments. Make a
variant with ``dataclasses.replace(GSEC, final_period="compound")``.

Pricing takes the settlement date as given. ``settlement_lag`` and
``calendar_name`` are applied by ``calendars.settlement_date``; ``quote``, and
a bond's ``price_decimals`` and ``price_tick``, only describe how the market
quotes (``round_to_tick`` gives a quotable price).

Sources cited in the field comments:

* Handbook: FIMMDA Handbook of Market Practices 2026,
  https://www.fimmda.org/UploadPopupPageFiles/Fimmda_Handbook_2026_Final_Ver.pdf
* CP guidelines: FIMMDA circular FIMCIR/2024-25/44 (25 Mar 2025),
  https://www.fimmda.org/UploadPopupPageFiles/FIMCIR2024_25_44_25Mar2025.pdf
* CD guidelines: FIMMDA circular FIMCIR/2023-24/36 (29 Dec 2023),
  https://www.fimmda.org/UploadPopupPageFiles/FIMCIR2023_24_36_FIMMDA_CD_Operational_GuidelinesDated%2029Dec23.pdf
* Auction pairs: FIMMDA G-sec auction files for 2025-26 and 2026-27, see
  ``tests/test_bonds.py``.
"""

from __future__ import annotations

import calendar
import math
import numbers
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

import numpy as np
import pandas as pd

COMPOUNDING = ("annual", "semiannual")
COMPOUNDING_FREQUENCY = {"annual": 1, "semiannual": 2}
DAY_COUNTS = ("30E/360", "30/360 US", "ACT/365F", "ACT/ACT ICMA")
THIRTY_360 = ("30E/360", "30/360 US")
ROLLS = ("following", "preceding", "modified_following", "modified_preceding", "unadjusted")
FINAL_PERIOD_RULES = ("simple", "compound")
FRACTION_RULES = ("excel", "days_to_next")
TIME_BASES = ("periods", "act/365")
COUPON_FREQUENCIES = (1, 2, 4, 12)
_YEAR_DAYS = {"30E/360": 360, "30/360 US": 360, "ACT/365F": 365}


def as_date(value) -> date:
    """``datetime.date`` from a date, datetime, pandas Timestamp, numpy datetime64 or ISO string.

    Strings must be ISO 8601: a date (``2026-11-16``) or a date and time
    (``2026-11-16 00:00:00``, whose date is taken). Day-first or month-first
    forms such as ``06-02-2024`` are refused, since they are ambiguous.
    """
    if isinstance(value, str):
        text = value.strip()
        try:
            return date.fromisoformat(text)
        except ValueError as exc:
            try:
                return datetime.fromisoformat(text).date()
            except ValueError:
                raise exc from None
    if isinstance(value, np.datetime64):
        value = pd.Timestamp(value)
    if value is None or value is pd.NaT or (isinstance(value, float) and math.isnan(value)):
        raise ValueError("missing date")
    if isinstance(value, datetime):  # pandas Timestamp included
        return value.date()
    if isinstance(value, date):
        return value
    raise TypeError(f"not a date: {value!r}")


def as_int(value, name: str, *, minimum: int | None = None) -> int:
    """``value`` as an ``int``, for counts of days, periods or decimal places.

    Integers (numpy's too) and whole floats such as ``2.0`` pass. Booleans and
    non-numbers raise ``TypeError``; fractions, NaN and values below
    ``minimum`` raise ``ValueError``. Messages name ``name``.
    """
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise TypeError(f"{name} must be an integer, not {value!r}")
    if not isinstance(value, numbers.Integral) and not (math.isfinite(value) and float(value).is_integer()):
        raise ValueError(f"{name} must be a whole number, not {value!r}")
    out = int(value)
    if minimum is not None and out < minimum:
        raise ValueError(f"{name} must be >= {minimum}, not {out}")
    return out


# ---------------------------------------------------------------- day counts


def days_30e_360(start, end) -> int:
    """30E/360 (Eurobond basis) days: both day numbers capped at 30, February left alone.

    FIMMDA's G-sec rule (handbook 6.6.1): the 31st counts as the 30th, and
    28 or 29 February "will be treated as any other day of the month".
    Counted from ``start`` (inclusive) to ``end`` (exclusive); Excel's basis 4.
    """
    s, e = as_date(start), as_date(end)
    return 360 * (e.year - s.year) + 30 * (e.month - s.month) + min(e.day, 30) - min(s.day, 30)


def _last_of_february(d: date) -> bool:
    return d.month == 2 and d.day == calendar.monthrange(d.year, 2)[1]


def days_30_360_us(start, end) -> int:
    """30/360 US (SIA) days, with the end-of-February rules; close to Excel's basis 0.

    Not FIMMDA's rule: it gives 17 for 28 Feb to 17 Mar where the handbook
    counts 19. Kept to test the RBI primer's reported use of Excel basis 0.
    """
    s, e = as_date(start), as_date(end)
    d1, d2 = s.day, e.day
    if _last_of_february(s) and _last_of_february(e):
        d2 = 30
    if _last_of_february(s):
        d1 = 30
    if d2 == 31 and d1 >= 30:
        d2 = 30
    if d1 == 31:
        d1 = 30
    return 360 * (e.year - s.year) + 30 * (e.month - s.month) + d2 - d1


def days_actual(start, end) -> int:
    """Calendar days from ``start`` to ``end``."""
    return (as_date(end) - as_date(start)).days


def _check_day_count(day_count: str) -> None:
    if day_count not in DAY_COUNTS:
        raise ValueError(f"day_count must be one of {DAY_COUNTS}, not {day_count!r}")


def day_count_days(day_count: str, start, end) -> int:
    """Days from ``start`` to ``end`` under ``day_count`` (30-day months, or actual days)."""
    _check_day_count(day_count)
    if day_count == "30E/360":
        return days_30e_360(start, end)
    if day_count == "30/360 US":
        return days_30_360_us(start, end)
    return days_actual(start, end)


def coupon_period_days(day_count: str, period_start, period_end, frequency: int) -> float:
    """Length ``E`` of a coupon period in days: 360/f or 365/f, or the actual days for ACT/ACT ICMA."""
    _check_day_count(day_count)
    if day_count == "ACT/ACT ICMA":
        return float(days_actual(period_start, period_end))
    return _YEAR_DAYS[day_count] / frequency


def year_fraction(day_count: str, start, end, *, period_start=None, period_end=None,
                  frequency: int | None = None) -> float:
    """Years from ``start`` to ``end`` under ``day_count``.

    ACT/ACT ICMA needs the regular coupon period that holds the dates and the
    coupon frequency: the fraction is days / (frequency x days in the period).
    Bonds split irregular periods into regular ones themselves.
    """
    _check_day_count(day_count)
    if day_count == "ACT/ACT ICMA":
        if period_start is None or period_end is None or not frequency:
            raise ValueError("ACT/ACT ICMA needs period_start, period_end and frequency")
        return days_actual(start, end) / (frequency * days_actual(period_start, period_end))
    return day_count_days(day_count, start, end) / _YEAR_DAYS[day_count]


# ---------------------------------------------------------------- yield bases


def convert_yield(yield_pct, from_basis: str, to_basis: str):
    """Convert yields in percent between compounding bases (``COMPOUNDING``).

    ``m2 * ((1 + y / m1) ** (m1 / m2) - 1)`` with ``m1`` and ``m2`` the bases'
    periods a year, so a basis added to ``COMPOUNDING_FREQUENCY`` converts correctly.
    """
    if from_basis not in COMPOUNDING or to_basis not in COMPOUNDING:
        raise ValueError(f"compounding must be one of {COMPOUNDING}")
    if from_basis == to_basis:
        return yield_pct
    m1, m2 = COMPOUNDING_FREQUENCY[from_basis], COMPOUNDING_FREQUENCY[to_basis]
    y = np.asarray(yield_pct, dtype=float) / 100
    return ((1 + y / m1) ** (m1 / m2) - 1) * m2 * 100


def money_market_to_annual(rate_pct, days):
    """Annual-compounding equivalent (percent) of a simple Act/365 rate for a term of ``days``."""
    r = np.asarray(rate_pct, dtype=float) / 100
    d = np.asarray(days, dtype=float)
    return ((1 + r * d / 365) ** (365 / d) - 1) * 100


# ---------------------------------------------------------------- rounding


def _quantize(value: float, decimals: int, mode: str) -> float:
    if not math.isfinite(value):
        return value
    return float(Decimal(repr(value)).quantize(Decimal(1).scaleb(-decimals), rounding=mode))


def round_half_up(value: float, decimals: int) -> float:
    """Round half away from zero on the decimal digits ``repr`` shows: "rounded off" as a dealer would."""
    return _quantize(float(value), decimals, ROUND_HALF_UP)


def truncate(value: float, decimals: int) -> float:
    """Cut to ``decimals`` places without rounding."""
    return _quantize(float(value), decimals, ROUND_DOWN)


def round_to_tick(price: float, tick: float = 0.0025) -> float:
    """Nearest multiple of ``tick`` (G-sec secondary prices move in 0.0025, handbook 6.6.1)."""
    steps = (Decimal(repr(float(price))) / Decimal(repr(float(tick)))).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return float(steps * Decimal(repr(float(tick))))


# ---------------------------------------------------------------- instrument conventions


def _choice(value, allowed, field_name: str) -> None:
    if value not in allowed:
        raise ValueError(f"{field_name} must be one of {allowed}, not {value!r}")


@dataclass(frozen=True, kw_only=True)
class BondConventions:
    """Conventions of a coupon-bearing or zero-coupon bond; prices are per 100 face value.

    ``day_count``: accrued interest and the first-period fraction.
    ``frequency``: coupons a year; ``compounding``: the yield's compounding basis.
    ``settlement_lag``: business days from trade to settlement, on the calendar
    named by ``calendar_name``. Applied by ``calendars.settlement_date``; pricing
    takes the settlement date as given.
    ``quote``: what a trade is agreed on (``clean_price`` or ``yield``).
    ``price_decimals``, ``price_tick``: how prices are quoted. Descriptive: the
    engine's prices are unrounded (``round_to_tick`` gives a quotable price).
    ``coupon_roll``, ``redemption_roll``: how a payment date on a holiday moves
    (amounts never change). Cash flows are discounted on their scheduled dates.
    ``final_period``: ``simple`` interest when one period or less is left (Excel's
    PRICE and YIELD) or ``compound`` to the end.
    ``fraction_rule``: the part of the current period still to run, ``DSC / E``.
    ``excel`` takes ``DSC = E - A`` for the 30/360 day counts (Excel's
    COUPDAYSNC) and the actual days to the next coupon for the actual ones;
    ``days_to_next`` counts the day count from settlement to the next coupon.
    They differ only in periods of other than ``E`` days on the day count
    (30E/360: 28 or 29 February to or from the 29th to 31st). Under both, the
    last period runs on the day count from settlement to redemption (Excel's
    DSR in YIELD), and ``DSC`` is never less than one day: 30E/360 counts 0
    from the 30th to the 31st, and ``E - A`` reaches 0 or less in the last days
    of a 181- or 182-day period.
    ``time_basis``: ``periods`` counts time to each flow in coupon periods (whole
    periods plus that fraction); ``act/365`` uses actual days / 365.
    ``ex_coupon_days``: days before a coupon when the bond trades without it.
    Only 0 is built (record dates come with corporate bonds).
    ``shut_days_before_coupon``, ``shut_days_before_redemption``: calendar days
    in which transfers stop; ``in_shut_period`` flags them, pricing ignores them.
    """

    name: str
    day_count: str
    frequency: int
    compounding: str
    settlement_lag: int
    quote: str
    price_decimals: int
    price_tick: float
    coupon_roll: str
    redemption_roll: str
    final_period: str
    fraction_rule: str
    time_basis: str
    ex_coupon_days: int
    shut_days_before_coupon: int
    shut_days_before_redemption: int
    calendar_name: str

    def __post_init__(self) -> None:
        for name in ("frequency", "settlement_lag", "price_decimals", "ex_coupon_days",
                     "shut_days_before_coupon", "shut_days_before_redemption"):
            object.__setattr__(self, name, as_int(getattr(self, name), name, minimum=0))
        _choice(self.day_count, DAY_COUNTS, "day_count")
        _choice(self.frequency, COUPON_FREQUENCIES, "frequency")
        _choice(self.compounding, COMPOUNDING, "compounding")
        _choice(self.coupon_roll, ROLLS, "coupon_roll")
        _choice(self.redemption_roll, ROLLS, "redemption_roll")
        _choice(self.final_period, FINAL_PERIOD_RULES, "final_period")
        _choice(self.fraction_rule, FRACTION_RULES, "fraction_rule")
        _choice(self.time_basis, TIME_BASES, "time_basis")
        _choice(self.quote, ("clean_price", "yield"), "quote")
        if isinstance(self.price_tick, bool) or not isinstance(self.price_tick, numbers.Real) \
                or not (math.isfinite(self.price_tick) and self.price_tick > 0):
            raise ValueError(f"price_tick must be a positive number, not {self.price_tick!r}")
        if self.time_basis == "periods" and self.compounding_frequency != self.frequency:
            raise ValueError("time_basis 'periods' needs the yield compounded at the coupon frequency; "
                             "use time_basis 'act/365' for other bases")

    @property
    def compounding_frequency(self) -> int:
        return COMPOUNDING_FREQUENCY[self.compounding]


@dataclass(frozen=True, kw_only=True)
class DiscountConventions:
    """Conventions of a discount instrument (T-bill, CMB, CP, CD); prices per 100 face value.

    ``discount``: ``rear`` (yield earned on the price paid,
    ``P = 100 / (1 + y d / (100 year_days))``) or ``front`` (discount on face,
    ``P = 100 (1 - y d / (100 year_days))``). ``d`` is calendar days from
    settlement to maturity. ``price_decimals`` and ``price_rounding``: how the
    price from a yield is rounded (``None`` keeps it unrounded).
    ``settlement_lag`` and ``calendar_name``: as for bonds, applied by
    ``calendars.settlement_date``. ``quote`` is descriptive.
    """

    name: str
    year_days: int
    discount: str
    price_decimals: int | None
    price_rounding: str
    settlement_lag: int
    quote: str
    maturity_roll: str
    calendar_name: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "year_days", as_int(self.year_days, "year_days", minimum=1))
        object.__setattr__(self, "settlement_lag", as_int(self.settlement_lag, "settlement_lag", minimum=0))
        if self.price_decimals is not None:
            object.__setattr__(self, "price_decimals", as_int(self.price_decimals, "price_decimals", minimum=0))
        _choice(self.discount, ("rear", "front"), "discount")
        _choice(self.price_rounding, ("half_up", "truncate"), "price_rounding")
        _choice(self.maturity_roll, ROLLS, "maturity_roll")
        _choice(self.quote, ("price", "yield"), "quote")


GSEC = BondConventions(
    name="Dated G-sec",
    day_count="30E/360",  # Handbook 6.6.1(a): "Interest to be calculated on a European 30/360-day basis"
    frequency=2,  # Semi-annual on the maturity day and month: every FY2025-27 auction pair reproduces on it
    compounding="semiannual",  # RBI's cut-off yields reproduce only on semi-annual compounding
    settlement_lag=1,  # Handbook 4.7.1: "settled on T+1 basis" (T+2 allowed when an FPI is a counterparty)
    quote="clean_price",  # Handbook 7.3.6: consideration is "clean price + accrued interest"
    price_decimals=4,  # Handbook 6.6.1 note: "up to a maximum of four decimal places"
    price_tick=0.0025,  # Handbook 6.6.1 note: "with a tick size of 0.0025"
    coupon_roll="following",  # Handbook 4.10.1: coupon on a holiday paid "on the next working day"
    redemption_roll="preceding",  # Handbook 4.10.1: redemption on a holiday paid "on the previous Mumbai working day"
    final_period="simple",  # NOT FOUND. Excel PRICE/YIELD; NSE D&A prices G-secs with an "Excel based formula"
    fraction_rule="excel",  # NOT FOUND. Excel's E - A; no published pair separates it from days_to_next
    time_basis="periods",  # Whole periods plus the first-period fraction: reproduces the auction pairs
    ex_coupon_days=0,  # 5.91% GS 2028 settling 29 Dec 2025, a day before its coupon, priced cum-coupon
    shut_days_before_coupon=0,  # UNVERIFIED: pre-coupon shut period ended in 2014 (Reuters, via a search summary)
    shut_days_before_redemption=1,  # UNVERIFIED: one-day shut period before redemption (same report)
    calendar_name="mumbai_settlement",  # Handbook 4.10.1 (Maharashtra holidays), 4.10.3 (1 April); no Saturdays
)

# Handbook 6.6: "Government Securities are coupon-bearing debt instruments issued by the
# Government of India / State Government", so SDLs share 6.6.1. No priced SDL pair has been
# tested: the two SDL new issues in the tests are smoke tests (at par on the issue date, any
# day count returns the coupon), and no priced SDL reissue was available.
SDL = replace(GSEC, name="SDL")

STRIPS = BondConventions(
    name="STRIPS",
    day_count="30E/360",  # UNVERIFIED: taken from the parent G-sec; no STRIPS rule found
    frequency=2,  # Semi-annual quasi-coupon dates back from maturity (the parent's coupon dates)
    compounding="semiannual",  # FBIL's zero curve is "Zero Coupon% (Semi-annual)" (fbil.py)
    settlement_lag=1,  # UNVERIFIED: as G-secs
    quote="yield",  # Search summary of RBI STRIPS guidelines: priced as a zero-coupon bond, quoted in yield
    price_decimals=4,  # As G-secs
    price_tick=0.0025,  # As G-secs
    coupon_roll="following",  # Not used: a STRIP has no coupons
    redemption_roll="preceding",  # UNVERIFIED: the parent's coupon or redemption date rule
    final_period="compound",  # NOT FOUND. A zero discounts its face value at the zero yield to the end
    fraction_rule="excel",  # NOT FOUND. As G-secs: E - A, the last period on days to maturity, never < 1 day
    time_basis="periods",  # NOT FOUND. Excel-style periods; "act/365" counts actual days / 182.5
    ex_coupon_days=0,  # No coupons
    shut_days_before_coupon=0,  # No coupons
    shut_days_before_redemption=1,  # UNVERIFIED: as G-secs
    calendar_name="mumbai_settlement",
)

TBILL = DiscountConventions(
    name="T-bill",
    year_days=365,  # Handbook 6.7.1(c): "Discount is calculated on Actual / 365-day basis"
    discount="rear",  # Handbook 6.7.1(d): "Discount to be calculated on a rear-ended basis"
    price_decimals=4,  # Handbook 6.7.1(e): "up to a maximum of four decimal places"
    price_rounding="half_up",  # Handbook 6.7.1(e): "rounded off to the 4th decimal place"
    settlement_lag=1,  # Handbook 4.7.1 and 6.7.2; auctions settle T+1 (27 May 2026 settled 29 May, after Bakri Id)
    quote="yield",  # Handbook 6.7: quoted in yield; "the complete price should be used while confirming deals"
    maturity_roll="preceding",  # Handbook 4.10.1 general rule; RBI sets the date for unscheduled holidays (7.3.5)
    calendar_name="mumbai_settlement",
)

# Handbook 6.7.3: CMBs "have the generic character of T-Bills but are issued for maturities
# less than 91 days". Auction settlement lag: not found, taken as T+1.
CMB = replace(TBILL, name="Cash management bill")

CP = DiscountConventions(
    name="Commercial paper",
    year_days=365,  # CP guidelines: "Day Count Convention: Actual / 365"
    discount="rear",  # Handbook 6.4.1(a): "Discount to be calculated on a rear-ended basis"
    price_decimals=4,  # Handbook 6.4.1(b): "up to a maximum of four decimal places"
    price_rounding="half_up",  # Handbook 7.3.4: CP/CD price "up to four decimals – rounded off"
    settlement_lag=1,  # CP guidelines: "T+0 or T+1"; only T+0 on the working day before maturity
    quote="yield",  # CP guidelines: "The CP / NCD are quoted in terms of yield"
    maturity_roll="preceding",  # CP guidelines: paid "on the immediately preceding Mumbai Business Day"
    calendar_name="mumbai_settlement",  # CP guidelines: a Mumbai Business Day excludes "any Saturday"
)

CD = DiscountConventions(
    name="Certificate of deposit",
    year_days=365,  # Handbook 6.5.2: Price = 100 / (1 + Yield x days / (365 x 100))
    discount="rear",  # Handbook 6.5.2(a): "Discount to be calculated on a rear-ended basis"
    price_decimals=4,  # Handbook 6.5.2(b): "up to a maximum of four decimal places"
    price_rounding="half_up",  # Handbook 6.5.2(b): "rounded off to the 4th decimal place"
    settlement_lag=1,  # CD guidelines: "settlement cycle for OTC trades in CDs shall be T+0 or T +1"
    quote="yield",  # As CP (handbook 7.3.4 works the price from the "contracted rate of yield")
    maturity_roll="preceding",  # CD guidelines: Saturday, Sunday or Mumbai holiday paid on the preceding business day
    calendar_name="mumbai_settlement",
)

CONVENTIONS: dict[str, BondConventions | DiscountConventions] = {
    "gsec": GSEC, "sdl": SDL, "strips": STRIPS, "tbill": TBILL, "cmb": CMB, "cp": CP, "cd": CD,
}
