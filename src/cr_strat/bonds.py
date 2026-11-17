"""Pricing of fixed-coupon bonds, zero-coupon bonds (STRIPS) and discount instruments.

Prices are per 100 face value and yields in percent. Dates may be
``datetime.date``, pandas Timestamps or ISO strings.

**Fixed-coupon bonds** (``FixedRateBond``: G-secs, SDLs) are priced the way
Excel's PRICE and YIELD functions price them, on the conventions' day count.
NSE Data & Analytics values G-secs and SDLs with an "Excel based formula", and
this form reproduces RBI's cut-off yield from the cut-off price for every
published FY2025-27 auction pair tested (``tests/test_bonds.py``). With ``f``
coupons a year, ``C`` the coupon per period, ``A`` the days from the previous
coupon date to settlement, ``E`` the days in a coupon period (180 under
30E/360) and ``w = DSC / E``::

    dirty = sum_k CF_k / (1 + y/f) ** (k - 1 + w)    more than one period left
    dirty = (100 + C) / (1 + w * y/f)                one period or less ("simple")
    accrued = C * A / E                              clean = dirty - accrued

``DSC`` is ``E - A`` (Excel's COUPDAYSNC on its 30/360 bases) while more than
one period is left, and the days from settlement to redemption in the last
period (Excel's DSR in YIELD), so the price there is the exact inverse of the
yield. Both are floored at one day. Under FIMMDA's 30E/360 a half-year from
28 or 29 February to the 29th-31st of August has 181 or 182 days, so
``E - A`` reaches 0 or less in its last days, and the 30th to the 31st counts
0 days; the floor keeps every future flow at a positive time, with positive
duration and DV01. ``A`` is not capped: in those periods accrued interest can
exceed the coupon by up to 2/180 of it (an open question in
``docs/conventions.md``).

Coupons fall every 12/f months on the maturity's day of the month (clipped to
the month's end), counted back from maturity. A buyer settling on a coupon
date gets neither accrued interest nor that coupon. Cash flows are discounted
on their scheduled dates. With a calendar, ``CashFlow.payment_date`` shows when
the money moves: a coupon on a holiday the next working day, the redemption
and last coupon the previous one, amounts unchanged (FIMMDA Handbook 4.10.1).

``issue_date`` and ``first_coupon_date`` are optional. A first period that is
not a regular coupon period (short or long) accrues from the issue date and
pays the coupon rate on the day count for its actual length.

**Zero-coupon bonds** (``ZeroCouponBond``: STRIPS) use the same machinery with
no coupons: ``100 / (1 + y/2) ** (n - 1 + w)`` with ``n`` the half-years left.

**Discount instruments** (``DiscountInstrument``: T-bills, CMBs, CP, CD) use
their conventions' simple yield, rear-ended by default:
``P = 100 / (1 + y d / 36500)``, ``d`` calendar days from settlement to
maturity, price rounded to 4 decimals.

Duration, DV01 and convexity are exact derivatives of the price formula in
use, on the dirty price with the yield as a decimal: modified duration
``-P'/P`` in years, DV01 the fall in price per 100 face for a one basis point
rise, convexity ``P''/P`` in years squared. Macaulay duration is the
present-value-weighted time to the cash flows, in years.

Inputs are checked: yields must be finite and above the level where a
discount factor stops being defined (-200% on semi-annual compounding), and
prices positive and finite; anything else raises ``ValueError``.

Corporate bonds (calls, puts, amortisation, record dates) and floating-rate
bonds are not built. They fit as further ``Bond`` subclasses: every price,
yield and risk number comes from ``_flows``, a list of (time, amount) pairs,
so a call date, an amortising principal or projected floating coupons change
only how that list is built. A flow at a time of zero or less is refused.
"""

from __future__ import annotations

import calendar as _cal
import math
from dataclasses import dataclass
from datetime import date
from typing import ClassVar

import pandas as pd

from cr_strat.calendars import BusinessCalendar
from cr_strat.conventions import (
    GSEC,
    STRIPS,
    TBILL,
    THIRTY_360,
    BondConventions,
    DiscountConventions,
    as_date,
    as_int,
    coupon_period_days,
    day_count_days,
    round_half_up,
    truncate,
)

YIELD_TOLERANCE_PCT = 1e-10
ANALYTICS_COLUMNS = (
    "accrued_interest", "dirty_price", "clean_price", "yield_pct", "macaulay_duration",
    "modified_duration", "dv01", "convexity",
)
_TOL = YIELD_TOLERANCE_PCT / 100  # in decimal yield
_PRICE_RTOL = 1e-9  # a solved yield must price back to the target within this, relative
_MAX_ITER = 200


@dataclass(frozen=True)
class CashFlow:
    """One payment per 100 face. ``periods``: time from settlement in compounding periods."""

    date: date
    payment_date: date
    amount: float
    kind: str
    periods: float
    accrual_start: date | None = None
    accrual_end: date | None = None


@dataclass(frozen=True)
class BondAnalytics:
    """Price, yield and risk of a bond on one settlement date (per 100 face)."""

    settlement: date
    clean_price: float
    dirty_price: float
    accrued_interest: float
    yield_pct: float
    macaulay_duration: float
    modified_duration: float
    dv01: float
    convexity: float
    previous_coupon_date: date | None
    next_coupon_date: date | None


@dataclass(frozen=True)
class _Flows:
    times: tuple[float, ...]  # compounding periods from settlement
    amounts: tuple[float, ...]
    m: int  # compounding periods a year
    simple: bool  # one payment date left and final_period == "simple"
    accrued: float

    def __post_init__(self) -> None:
        # A flow at time <= 0 would be compounded forward: price rising with yield, negative
        # duration and DV01. Schedules never produce one; this guards new Bond subclasses.
        if not self.times or len(self.times) != len(self.amounts):
            raise ValueError("a bond needs at least one cash flow after settlement")
        bad = [t for t in self.times if not (math.isfinite(t) and t > 0)]
        if bad:
            raise ValueError(f"cash flow at time {bad[0]} periods from settlement; every flow must be after it")
        if self.simple and len(self.times) != 1:
            raise ValueError("simple interest applies to a single remaining payment date")


def _check_price(price, what: str) -> float:
    value = float(price)
    if not (math.isfinite(value) and value > 0):
        raise ValueError(f"{what} must be a positive number, not {value}")
    return value


def _check_yield(flows: _Flows, yield_pct) -> float:
    """Decimal yield from a percent one, refused unless finite and above ``_lowest_yield``."""
    value = float(yield_pct)
    if not math.isfinite(value):
        raise ValueError(f"yield must be a finite number, not {value}")
    floor = _lowest_yield(flows)
    if not value / 100 > floor:
        raise ValueError(f"yield must be above {floor * 100:g}% for these cash flows, not {value}%")
    return value / 100


def _value(flows: _Flows, y: float) -> tuple[float, float, float]:
    """Dirty price and its first and second derivatives in ``y`` (decimal)."""
    m = flows.m
    try:
        if flows.simple:
            t, cf = flows.times[0], sum(flows.amounts)
            g = 1.0 + y * t / m
            if g <= 0:
                return math.inf, -math.inf, math.inf
            tm = t / m
            return cf / g, -cf * tm / g**2, 2.0 * cf * tm * tm / g**3
        base = 1.0 + y / m
        if base <= 0:
            return math.inf, -math.inf, math.inf
        p = d1 = d2 = 0.0
        for t, cf in zip(flows.times, flows.amounts):
            v = cf * base**-t
            p += v
            d1 -= v * t / (m * base)
            d2 += v * t * (t + 1) / (m * m * base * base)
        return p, d1, d2
    except OverflowError:
        return math.inf, -math.inf, math.inf


def _finite_value(flows: _Flows, y: float) -> tuple[float, float, float]:
    """``_value`` at an allowed yield, refusing a price too large to represent."""
    p, d1, d2 = _value(flows, y)
    if not math.isfinite(p):
        raise ValueError(f"a yield of {y * 100}% gives a price too large to represent")
    return p, d1, d2


def _macaulay(flows: _Flows, y: float) -> float:
    if not y > _lowest_yield(flows):
        raise ValueError(f"no discount factor at a yield of {y * 100}%")
    if flows.simple:
        return flows.times[0] / flows.m
    base = 1.0 + y / flows.m
    pv = [cf * base**-t for t, cf in zip(flows.times, flows.amounts)]
    return sum(t / flows.m * v for t, v in zip(flows.times, pv)) / sum(pv)


def _lowest_yield(flows: _Flows) -> float:
    """Yields must stay above this (decimal) for the discount factors to be defined."""
    if flows.simple:
        return -flows.m / flows.times[0]
    return -float(flows.m)


def _solve_yield(flows: _Flows, target: float, guess: float) -> float:
    """Decimal yield at which ``flows`` are worth the dirty price ``target``.

    In closed form for a single flow; otherwise Newton's method kept inside a
    bracket, falling back to bisection, to ``YIELD_TOLERANCE_PCT``. Every flow
    is at a positive time, so the price falls as the yield rises.
    """
    target = _check_price(target, "dirty price")
    y = _solve_unchecked(flows, target, guess)
    floor = _lowest_yield(flows)
    # A price far above flows due within days has a yield so close to the floor that a double
    # cannot hold it precisely (or at all). Refuse it rather than return a yield that does not
    # price back. Sane inputs reprice to about 1e-14.
    if not (y > floor and abs(_value(flows, y)[0] - target) <= _PRICE_RTOL * target):
        raise ValueError(f"no yield reprices these cash flows to {target}: "
                         f"it is too close to {floor * 100:g}% to represent")
    return y


def _solve_unchecked(flows: _Flows, target: float, guess: float) -> float:
    if len(flows.times) == 1:
        t, cf = flows.times[0], flows.amounts[0]
        try:
            if flows.simple:
                return flows.m * (cf / target - 1) / t
            return flows.m * ((cf / target) ** (1 / t) - 1)
        except OverflowError:
            raise ValueError(f"no finite yield prices these cash flows at {target}") from None
    floor = _lowest_yield(flows)  # -m: two or more flows are always compounded
    lo, hi = max(-0.5, floor / 2), 0.5
    for _ in range(80):
        if _value(flows, hi)[0] <= target:
            break
        hi *= 2
    else:
        raise ValueError(f"no yield prices these cash flows at {target}")
    for _ in range(_MAX_ITER):
        if _value(flows, lo)[0] >= target:
            break
        lo = (lo + floor) / 2
    else:
        raise ValueError(f"no yield prices these cash flows at {target}")
    y = min(max(guess, lo), hi)
    for _ in range(_MAX_ITER):
        p, d1, _ = _value(flows, y)
        diff = p - target
        if diff == 0:
            return y
        if diff > 0:
            lo = y
        else:
            hi = y
        y_next = y - diff / d1 if d1 < 0 and math.isfinite(d1) else math.nan
        if not lo < y_next < hi:
            y_next = 0.5 * (lo + hi)
        if abs(y_next - y) < _TOL:
            return y_next
        y = y_next
    raise RuntimeError("yield did not converge")


def _shift_months(anchor: date, months: int) -> date:
    """``anchor`` moved by ``months``, on ``anchor``'s day of the month clipped to the month's end."""
    total = anchor.year * 12 + anchor.month - 1 + months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    return date(year, month, min(anchor.day, _cal.monthrange(year, month)[1]))


class Bond:
    """Price, yield and risk for bonds valued as a list of fixed cash flows.

    Subclasses provide ``_flows(settlement)``, ``cash_flows(settlement)``,
    ``previous_coupon_date`` and ``next_coupon_date``; ``conventions`` and
    ``maturity`` are attributes.
    """

    conventions: BondConventions
    maturity: date

    def _flows(self, settlement: date) -> _Flows:
        raise NotImplementedError

    def _guess(self) -> float:
        return 0.07

    def _at_yield(self, yield_pct: float, settlement) -> tuple[_Flows, float, float, float, float]:
        """(flows, decimal yield, dirty price and its two derivatives), with the yield checked."""
        flows = self._flows(as_date(settlement))
        y = _check_yield(flows, yield_pct)
        return (flows, y, *_finite_value(flows, y))

    @staticmethod
    def _dirty_from_clean(flows: _Flows, clean_price: float) -> float:
        return _check_price(clean_price, "clean price") + flows.accrued

    def accrued_interest(self, settlement) -> float:
        return self._flows(as_date(settlement)).accrued

    def dirty_price(self, yield_pct: float, settlement) -> float:
        return self._at_yield(yield_pct, settlement)[2]

    def clean_price(self, yield_pct: float, settlement) -> float:
        flows, _, p, _, _ = self._at_yield(yield_pct, settlement)
        return p - flows.accrued

    def yield_from_dirty_price(self, dirty_price: float, settlement) -> float:
        flows = self._flows(as_date(settlement))
        return _solve_yield(flows, _check_price(dirty_price, "dirty price"), self._guess()) * 100

    def yield_from_price(self, clean_price: float, settlement) -> float:
        """Yield (percent) from a clean price; converges to ``YIELD_TOLERANCE_PCT``."""
        flows = self._flows(as_date(settlement))
        return _solve_yield(flows, self._dirty_from_clean(flows, clean_price), self._guess()) * 100

    def macaulay_duration(self, yield_pct: float, settlement) -> float:
        flows, y, _, _, _ = self._at_yield(yield_pct, settlement)
        return _macaulay(flows, y)

    def modified_duration(self, yield_pct: float, settlement) -> float:
        _, _, p, d1, _ = self._at_yield(yield_pct, settlement)
        return -d1 / p

    def dv01(self, yield_pct: float, settlement) -> float:
        """Fall in price per 100 face for a one basis point rise in yield."""
        return -self._at_yield(yield_pct, settlement)[3] * 1e-4

    def convexity(self, yield_pct: float, settlement) -> float:
        _, _, p, _, d2 = self._at_yield(yield_pct, settlement)
        return d2 / p

    def analytics(self, settlement, *, yield_pct: float | None = None,
                  clean_price: float | None = None) -> BondAnalytics:
        """Everything at once, from either a yield or a clean price."""
        if (yield_pct is None) == (clean_price is None):
            raise ValueError("pass exactly one of yield_pct and clean_price")
        s = as_date(settlement)
        flows = self._flows(s)
        if yield_pct is None:
            y = _solve_yield(flows, self._dirty_from_clean(flows, clean_price), self._guess())
        else:
            y = _check_yield(flows, yield_pct)
        p, d1, d2 = _finite_value(flows, y)
        return BondAnalytics(
            settlement=s,
            clean_price=p - flows.accrued,
            dirty_price=p,
            accrued_interest=flows.accrued,
            yield_pct=y * 100,
            macaulay_duration=_macaulay(flows, y),
            modified_duration=-d1 / p,
            dv01=-d1 * 1e-4,
            convexity=d2 / p,
            previous_coupon_date=self.previous_coupon_date(s),
            next_coupon_date=self.next_coupon_date(s),
        )

    def previous_coupon_date(self, settlement) -> date | None:
        raise NotImplementedError

    def next_coupon_date(self, settlement) -> date | None:
        raise NotImplementedError

    def in_shut_period(self, settlement) -> bool:
        """True if ``settlement`` falls in a shut period before the next coupon or the redemption.

        Uses the conventions' shut days (calendar days). The G-sec values are unverified.
        """
        s = as_date(settlement)
        c = self.conventions
        if 0 < (self.maturity - s).days <= c.shut_days_before_redemption:
            return True
        nxt = self.next_coupon_date(s)
        return nxt is not None and nxt != self.maturity and 0 < (nxt - s).days <= c.shut_days_before_coupon


class _ScheduledBond(Bond):
    """Schedule, accrual and cash flows for a bullet bond with fixed coupons (possibly none)."""

    coupon_pct: float
    issue_date: date | None
    first_coupon_date: date | None
    redemption: float
    calendar: BusinessCalendar | None

    def _setup(self) -> None:
        for name in ("maturity", "issue_date", "first_coupon_date"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, as_date(value))
        c = self.conventions
        if not isinstance(c, BondConventions):
            raise TypeError("conventions must be BondConventions (GSEC, SDL, STRIPS or a variant)")
        if c.ex_coupon_days:
            raise NotImplementedError("ex-coupon trading (record dates) is not built; ex_coupon_days must be 0")
        if not (math.isfinite(self.coupon_pct) and self.coupon_pct >= 0):
            raise ValueError(f"coupon_pct must be >= 0, not {self.coupon_pct}")
        if self.calendar is not None and not isinstance(self.calendar, BusinessCalendar):
            raise TypeError("calendar must be a BusinessCalendar")
        if self.issue_date is not None and self.issue_date >= self.maturity:
            raise ValueError(f"issue_date {self.issue_date} is not before maturity {self.maturity}")
        if self.first_coupon_date is not None:
            fc = self.first_coupon_date
            if self.issue_date is None:
                raise ValueError("first_coupon_date needs issue_date")
            if not self.issue_date < fc <= self.maturity:
                raise ValueError("first_coupon_date must fall after issue_date and on or before maturity")
            if self._quasi(self._index_on_or_before(fc)) != fc:
                raise ValueError(f"first_coupon_date {fc} is not on the coupon schedule counted back from "
                                 f"maturity {self.maturity}; irregular last periods are not supported")

    # ---- schedule: quasi-coupon date j is the maturity moved back j periods (j = 0 is maturity)

    @property
    def _months(self) -> int:
        return 12 // self.conventions.frequency

    def _quasi(self, j: int) -> date:
        return _shift_months(self.maturity, -j * self._months)

    def _index_on_or_before(self, d: date) -> int:
        """Smallest j >= 0 with quasi-date j on or before ``d``."""
        months = (self.maturity.year - d.year) * 12 + self.maturity.month - d.month
        j = max(0, months // self._months - 1)
        while self._quasi(j) > d:
            j += 1
        while j > 0 and self._quasi(j - 1) <= d:
            j -= 1
        return j

    @property
    def _first_coupon_index(self) -> int | None:
        """Quasi index of the first coupon; ``None`` when the bond has no issue date."""
        if self.first_coupon_date is not None:
            return self._index_on_or_before(self.first_coupon_date)
        if self.issue_date is not None:
            return self._index_on_or_before(self.issue_date) - 1
        return None

    def _irregular_first(self, j_first: int | None) -> bool:
        return j_first is not None and self.issue_date != self._quasi(j_first + 1)

    def _periods_between(self, start: date, end: date) -> float:
        """Accrual from ``start`` to ``end`` in coupon periods, on the conventions' day count."""
        c = self.conventions
        if c.day_count != "ACT/ACT ICMA":
            return day_count_days(c.day_count, start, end) / coupon_period_days(c.day_count, start, end, c.frequency)
        total, j = 0.0, self._index_on_or_before(start)
        while True:  # split into regular (quasi) periods, ICMA style
            p0, p1 = self._quasi(j), self._quasi(j - 1)
            lo, hi = max(start, p0), min(end, p1)
            if lo < hi:
                total += (hi - lo).days / (p1 - p0).days
            if p1 >= end:
                return total
            j -= 1

    def _coupon_amount(self, j: int, j_first: int | None) -> float:
        per_period = self.coupon_pct / self.conventions.frequency
        if j == j_first and self._irregular_first(j_first):
            return per_period * self._periods_between(self.issue_date, self._quasi(j))
        return per_period

    def _check_settlement(self, s: date) -> None:
        if s >= self.maturity:
            raise ValueError(f"settlement {s} is on or after maturity {self.maturity}")
        if self.issue_date is not None and s < self.issue_date:
            raise ValueError(f"settlement {s} is before issue date {self.issue_date}")

    def _schedule(self, s: date):
        """(n, j_first, accrual start, w): quasi-date n is on or before ``s``, n - 1 after it.

        ``w = DSC / E``, the part of the current quasi period still to run. ``DSC`` is
        ``E - A`` under the ``excel`` rule on a 30/360 day count while more than one
        period is left, else the day count from ``s`` to the next quasi date (in the
        last period, to redemption). It is at least one day: 30E/360 counts 0 from
        the 30th to the 31st, and ``E - A`` falls to 0 or below in the last days of a
        181- or 182-day period (28 or 29 February to the 29th-31st of August).
        """
        self._check_settlement(s)
        c = self.conventions
        n = self._index_on_or_before(s)
        j_first = self._first_coupon_index
        in_first = j_first is not None and j_first < n
        start = self.issue_date if in_first else self._quasi(n)
        q_prev, q_next = self._quasi(n), self._quasi(n - 1)
        e = coupon_period_days(c.day_count, q_prev, q_next, c.frequency)
        if n > 1 and c.fraction_rule == "excel" and c.day_count in THIRTY_360:
            dsc = e - day_count_days(c.day_count, q_prev, s)
        else:
            dsc = day_count_days(c.day_count, s, q_next)
        return n, j_first, start, max(dsc, 1) / e

    def _time(self, s: date, i: int, w: float, when: date) -> float:
        """Compounding periods from ``s`` to the ``i``-th remaining quasi date ``when``."""
        c = self.conventions
        if c.time_basis == "periods":
            return i - 1 + w
        return c.compounding_frequency * (when - s).days / 365

    def _flows(self, settlement: date) -> _Flows:
        s = as_date(settlement)
        n, j_first, start, w = self._schedule(s)
        c = self.conventions
        times, amounts = [], []
        for i in range(1, n + 1):
            j = n - i
            amount = self.redemption if j == 0 else 0.0
            if self.coupon_pct and (j_first is None or j <= j_first):
                amount += self._coupon_amount(j, j_first)
            if amount:
                times.append(self._time(s, i, w, self._quasi(j)))
                amounts.append(amount)
        accrued = 0.0
        if self.coupon_pct:
            accrued = self.coupon_pct / c.frequency * self._periods_between(start, s)
        simple = n == 1 and c.final_period == "simple"
        return _Flows(tuple(times), tuple(amounts), c.compounding_frequency, simple, accrued)

    def _pay_date(self, d: date, roll: str) -> date:
        return d if self.calendar is None else self.calendar.adjust(d, roll)

    def cash_flows(self, settlement, until=None) -> list[CashFlow]:
        """Payments after ``settlement`` (up to ``until`` if given), with scheduled and payment dates.

        With a strict calendar every payment date must fall in a year its list
        covers: pass ``until`` to stop inside them, or a ``lenient()`` calendar
        to project further on weekends alone.
        """
        s = as_date(settlement)
        until = None if until is None else as_date(until)
        n, j_first, _, w = self._schedule(s)
        c = self.conventions
        out = []
        for i in range(1, n + 1):
            j = n - i
            when = self._quasi(j)
            if until is not None and when > until:
                break
            t = self._time(s, i, w, when)
            roll = c.redemption_roll if j == 0 else c.coupon_roll
            if self.coupon_pct and (j_first is None or j <= j_first):
                accrual_start = self.issue_date if j == j_first else self._quasi(j + 1)
                out.append(CashFlow(when, self._pay_date(when, roll), self._coupon_amount(j, j_first),
                                    "coupon", t, accrual_start, when))
            if j == 0:
                out.append(CashFlow(when, self._pay_date(when, roll), self.redemption, "redemption", t))
        return out

    def coupon_dates(self, after=None) -> list[date]:
        """Scheduled coupon dates after ``after`` (default: the issue date) up to maturity."""
        if after is None:
            if self.issue_date is None:
                raise ValueError("pass after= for a bond without an issue_date")
            after = self.issue_date
        if not self.coupon_pct:
            return []
        n, j_first = self._index_on_or_before(as_date(after)), self._first_coupon_index
        return [self._quasi(j) for j in range(n - 1, -1, -1) if j_first is None or j <= j_first]

    def previous_coupon_date(self, settlement) -> date | None:
        """Last coupon date on or before ``settlement``; the issue date in the first period.

        Raises, as pricing does, for a settlement before the issue date or on or after maturity.
        """
        s = as_date(settlement)
        self._check_settlement(s)
        n, j_first = self._index_on_or_before(s), self._first_coupon_index
        if j_first is not None and j_first < n:
            return self.issue_date
        return self._quasi(n)

    def next_coupon_date(self, settlement) -> date | None:
        """First coupon date after ``settlement`` (for a zero, the next quasi-coupon date).

        Raises, as pricing does, for a settlement before the issue date or on or after maturity.
        """
        s = as_date(settlement)
        self._check_settlement(s)
        j, j_first = self._index_on_or_before(s) - 1, self._first_coupon_index
        if j_first is not None and j > j_first:
            j = j_first
        return self._quasi(j)

    def _guess(self) -> float:
        return self.coupon_pct / 100 if self.coupon_pct else 0.07


@dataclass(frozen=True)
class FixedRateBond(_ScheduledBond):
    """Bullet bond with a fixed coupon (percent a year) and conventions (default ``GSEC``).

    Pass ``calendar`` to get holiday-adjusted ``CashFlow.payment_date``s; prices do not use it.
    """

    coupon_pct: float
    maturity: date
    conventions: BondConventions = GSEC
    issue_date: date | None = None
    first_coupon_date: date | None = None
    redemption: float = 100.0
    calendar: BusinessCalendar | None = None
    name: str = ""

    def __post_init__(self) -> None:
        self._setup()


@dataclass(frozen=True)
class ZeroCouponBond(_ScheduledBond):
    """Zero-coupon bond such as a STRIP; conventions default to ``STRIPS``.

    Time to maturity runs on the quasi-coupon dates counted back from maturity
    (the parent G-sec's coupon dates).
    """

    maturity: date
    conventions: BondConventions = STRIPS
    issue_date: date | None = None
    redemption: float = 100.0
    calendar: BusinessCalendar | None = None
    name: str = ""
    coupon_pct: ClassVar[float] = 0.0
    first_coupon_date: ClassVar[None] = None

    def __post_init__(self) -> None:
        self._setup()


@dataclass(frozen=True)
class DiscountInstrument:
    """T-bill, cash management bill, CP or CD; conventions default to ``TBILL``.

    ``days`` is calendar days from settlement to ``maturity`` (FIMMDA's "No. of
    days to maturity"; not defined there, read as maturity minus settlement).
    Pass ``days=`` instead of a settlement date to override it.
    """

    maturity: date
    conventions: DiscountConventions = TBILL
    issue_date: date | None = None
    calendar: BusinessCalendar | None = None
    name: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "maturity", as_date(self.maturity))
        if self.issue_date is not None:
            object.__setattr__(self, "issue_date", as_date(self.issue_date))
            if self.issue_date >= self.maturity:
                raise ValueError(f"issue_date {self.issue_date} is not before maturity {self.maturity}")
        if not isinstance(self.conventions, DiscountConventions):
            raise TypeError("conventions must be DiscountConventions (TBILL, CMB, CP, CD or a variant)")
        if self.calendar is not None and not isinstance(self.calendar, BusinessCalendar):
            raise TypeError("calendar must be a BusinessCalendar")

    def days_to_maturity(self, settlement) -> int:
        s = as_date(settlement)
        if self.issue_date is not None and s < self.issue_date:
            raise ValueError(f"settlement {s} is before issue date {self.issue_date}")
        days = (self.maturity - s).days
        if days <= 0:
            raise ValueError(f"settlement {s} is on or after maturity {self.maturity}")
        return days

    def payment_date(self) -> date:
        """Date the face value is paid: maturity, moved by the conventions' roll if a calendar is set."""
        return self.maturity if self.calendar is None else self.calendar.adjust(
            self.maturity, self.conventions.maturity_roll)

    def _days(self, settlement, days) -> int:
        if days is None:
            if settlement is None:
                raise ValueError("pass a settlement date or days=")
            return self.days_to_maturity(settlement)
        if settlement is not None:
            raise ValueError("pass a settlement date or days=, not both")
        return as_int(days, "days", minimum=1)

    def _tau(self, settlement, days) -> float:
        return self._days(settlement, days) / self.conventions.year_days

    def _tau_yield(self, yield_pct: float, settlement, days) -> tuple[float, float]:
        """(years to maturity, decimal yield), refusing yields that leave no positive price."""
        y = float(yield_pct)
        if not math.isfinite(y):
            raise ValueError(f"yield must be a finite number, not {y}")
        tau, y = self._tau(settlement, days), y / 100
        x = y * tau
        if self.conventions.discount == "rear" and not 1 + x > 0:
            raise ValueError(f"a yield of {y * 100}% over {tau * self.conventions.year_days:g} days "
                             "makes 1 + y d / year_days zero or negative")
        if self.conventions.discount == "front" and not x < 1:
            raise ValueError(f"a front-ended discount of {y * 100}% over {tau * self.conventions.year_days:g} "
                             "days is the whole face value or more, leaving no positive price")
        return tau, y

    def price(self, yield_pct: float, settlement=None, *, days: int | None = None,
              rounded: bool = True) -> float:
        """Price per 100 from a yield, rounded as the conventions say unless ``rounded=False``."""
        c = self.conventions
        tau, y = self._tau_yield(yield_pct, settlement, days)
        x = y * tau
        if c.discount == "rear":
            p = 100 / (1 + x)
        else:
            p = 100 * (1 - x)
        if rounded and c.price_decimals is not None:
            p = (round_half_up if c.price_rounding == "half_up" else truncate)(p, c.price_decimals)
        return p

    def yield_from_price(self, price: float, settlement=None, *, days: int | None = None) -> float:
        """Yield (percent) from a price per 100: ``(100 - P) * year_days * 100 / (P * d)``, or on face if front-ended."""
        price = _check_price(price, "price")
        on = price if self.conventions.discount == "rear" else 100.0
        return (100 - price) / on / self._tau(settlement, days) * 100

    def modified_duration(self, yield_pct: float, settlement=None, *, days: int | None = None) -> float:
        tau, y = self._tau_yield(yield_pct, settlement, days)
        return tau / (1 + y * tau) if self.conventions.discount == "rear" else tau / (1 - y * tau)

    def dv01(self, yield_pct: float, settlement=None, *, days: int | None = None) -> float:
        """Fall in the (unrounded) price per 100 face for a one basis point rise in yield."""
        tau, y = self._tau_yield(yield_pct, settlement, days)
        slope = 100 * tau / (1 + y * tau) ** 2 if self.conventions.discount == "rear" else 100 * tau
        return slope * 1e-4

    def convexity(self, yield_pct: float, settlement=None, *, days: int | None = None) -> float:
        tau, y = self._tau_yield(yield_pct, settlement, days)
        return 2 * tau * tau / (1 + y * tau) ** 2 if self.conventions.discount == "rear" else 0.0


def _optional_date(value) -> date | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str) and pd.isna(value):
        return None
    return as_date(value)


def price_bonds(frame: pd.DataFrame, *, conventions: BondConventions = GSEC,
                given: str = "clean_price", column: str | None = None) -> pd.DataFrame:
    """``analytics`` for every row of ``frame``, a copy with the results added.

    Needs ``coupon_pct``, ``maturity_date``, ``settlement_date`` and the
    ``given`` quantity (``clean_price`` or ``yield_pct``), read from ``column``
    (default: the column named ``given``; ``column="price"`` for frames from
    ``trades``). ``issue_date`` and ``first_coupon_date`` are used when
    present. Dates may be dates, Timestamps or ISO strings. The results replace
    any columns of the same name: rename a reported ``yield_pct`` first to keep
    it. A row that cannot be priced gets NaN and the reason in ``error``.
    """
    if given not in ("clean_price", "yield_pct"):
        raise ValueError("given must be 'clean_price' or 'yield_pct'")
    column = given if column is None else column
    missing = [c for c in ("coupon_pct", "maturity_date", "settlement_date", column) if c not in frame]
    if missing:
        raise ValueError(f"missing columns: {missing}")
    outputs = [c for c in ANALYTICS_COLUMNS if c != given]
    if column in outputs:
        raise ValueError(f"column {column!r} would be overwritten by a result; given={given!r} reads it as input")
    rows = []
    for rec in frame.to_dict("records"):
        try:
            if pd.isna(rec[column]):
                raise ValueError(f"{column} is missing")
            bond = FixedRateBond(float(rec["coupon_pct"]), as_date(rec["maturity_date"]), conventions,
                                 issue_date=_optional_date(rec.get("issue_date")),
                                 first_coupon_date=_optional_date(rec.get("first_coupon_date")))
            result = bond.analytics(rec["settlement_date"], **{given: float(rec[column])})
            rows.append({c: getattr(result, c) for c in outputs} | {"error": ""})
        except (ValueError, TypeError) as exc:
            rows.append(dict.fromkeys(outputs, math.nan) | {"error": str(exc)})
    out = frame.copy()
    computed = pd.DataFrame(rows, columns=[*outputs, "error"], index=frame.index)
    for col in computed:
        out[col] = computed[col]
    return out
