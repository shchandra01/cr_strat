"""Listed corporate bonds (non-convertible debentures, NCDs): SEBI actual/actual coupons, record dates, workouts.

``CorporateBond`` is a ``bonds.Bond``: every price, yield, duration, DV01 and convexity comes from ``Bond``'s
methods over one list of (time, amount) flows built by ``_flows``. What differs from a G-sec is how that list is
built.

**Schedule.** Coupons are counted forward from the deemed date of allotment (``issue_date``), every 12/f months,
with the maturity as the last date (SEBI's 2016 circular works an issue of 1 Jul 2016 maturing on 30 Jun 2018 this
way; a G-sec style schedule counted back from maturity would put the coupons on 30 Dec and 30 Jun). Other
generators: ``backward`` (G-sec style), ``month_start`` (monthly retail series paid on the 1st of each month; the
first, short period can be ``clubbed`` with the next) and ``explicit`` (the due dates, and optionally payment and
record dates, from the term sheet or, from 18 Aug 2025, SEBI's centralised corporate bond database, which holds
every listed ISIN's cash-flow schedule). ``first_coupon_date`` starts a forward schedule on that date.

**Interest.** ``year_fraction_sebi``: actual days over a year of 365 or 366 days. SEBI's 2016 circular counts 366
for the whole one-year period holding 29 February, whatever the coupon frequency (twice for half-yearly coupons,
twelve times for monthly ones). Which one-year period is a reading (``leap_year_rule``): ``anniversary`` (default)
takes the years between anniversaries of the deemed date of allotment and reproduces SEBI's NCS master circular
illustration (8.95% on Rs 10 lakh, 89,500 every year, 14,47,500 in all); ``calendar`` splits at 1 January (the ISDA
reading, which reproduces one issuer's monthly table of 98 coupons but gives 89,487.94 and 89,512.06 in SEBI's
illustration); ``fixed365`` is actual/365 (the default for issues before Dec 2013, UNVERIFIED). A coupon is
``face x rate x yf(start, end)`` per 100 original face, piecewise over rate steps; an annual coupon is the full
rate. Amortised bonds pay interest on the outstanding face.

**Holidays.** A coupon due on a holiday is paid the next working day and the schedule does not move; a redemption
due on a holiday is paid the previous working day (SEBI 2013, 2016; NCS master circular Ch. III). For issues from
1 Jan 2017 a late coupon's interest runs only to the due date (``postponed_coupon="scheduled"``); for issues of
Dec 2013 to Dec 2016 (``NCD_2013``) it runs to the payment date and the next period starts there (SEBI 2013 Annex A:
367 and 364 days). Interest on an early redemption: the full scheduled coupon (default, SEBI's illustration) or to
the payment date (``redemption_interest="to_payment"``, as two 2025-26 term sheets say). Payment dates roll on
``calendar``; without one (the default, as in ``bonds``) nothing rolls. The conventions name the calendar
(``payment_calendar_name``); ``corporate_from_terms`` loads it, lenient beyond the years its list covers.

**Record dates and the ex-interest window.** The record date is ``record_days`` (default
``conventions.ex_coupon_days``, 15) calendar days before each due date, rolled on ``record_calendar`` (default the
payment calendar) by ``record_roll``, and never before the allotment (its holder is the first on record). A
settlement after a coupon's record date and before its due date is ex-interest: that coupon (and any principal due
with it) is left out and the accrued interest is negative,
``-F r yf(s, end)``, so the dirty price equals the cum-interest dirty price less the coupon's present value. A
settlement on the record date is cum-interest. No exchange or FIMMDA rule for these trades was found: UNVERIFIED.
Trading stops between the redemption's record date and the redemption (term sheets read): a settlement there is
refused. A partial redemption's record date makes the bond trade ex-principal (UNVERIFIED).

**Price and yield.** ``P = sum CF_i (1 + y/m)^(-m tau_i)``, ``tau_i`` actual days / 365 to the flow and ``m`` the
compounding frequency (annual by default: issuers' printed yields reproduce on Excel's XIRR, within 0.6 bp, with
no rule text found). Flows are discounted on due dates for settlements from 18 Aug 2025 (SEBI 2025 for
yield-to-price) and on payment dates before (``flow_dates="by_date"``). With a single flow left less than a year
away the money-market rule applies: ``P = CF / (1 + y d / 365)`` (FIMMDA handbook 6.9.1(c)). Zero-coupon,
deep-discount and cumulative bonds are a single flow (``frequency=0``, ``redemption`` the amount per 100 face) with
no accrued interest (UNVERIFIED). ``with_compounding`` prices on any basis ``conventions.convert_yield`` knows.

**Workouts.** ``options`` lists calls and puts (``CallPut``). ``to_workout`` truncates the schedule at an exercise
date, redeeming the outstanding face at the option price, with interest to that date if it falls between coupon
dates (UNVERIFIED). ``yields_to_workouts``, ``yield_to_worst`` (lowest of the yields to maturity and to each call;
puts separate unless asked), ``price_to_worst`` (lowest value over calls and maturity, SEBI's mutual-fund rule for
callable bonds), ``price_to_best_put`` (highest over puts and maturity) and ``sebi_valuation`` (SEBI's master
circular for mutual funds of 20 Mar 2026, 10.3.1: the call trigger date has the lowest value among calls and
maturity, the put trigger date the highest among puts and maturity; with both the earlier one; a put and a call on
one date at one price is a deemed maturity; AT1 bonds to the first call). An exercise counts only once its notice
is public (``effective_bond``). ``perpetual_bond`` deems a perpetual to mature 100 years after issue (10.4.2).

**Steps.** ``rate_steps`` changes the coupon from given dates (rating-linked steps, corporate FRB fixings from
``floating``); ``apply_rating_steps`` builds them from rating actions, each used only from its ``public_from``.

Not built: banking calendars with working Saturdays, exchange and depository calendars (record dates roll on the
payment calendar), coupon discretion and write-down of AT1 bonds (in the spread), default interest, TDS, trade-yield
checks and spread measures (the next build). Sources and the open choices: ``docs/conventions.md``.
"""

from __future__ import annotations

import bisect
import calendar as _cal
import difflib
import math
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from itertools import pairwise
from pathlib import Path

import pandas as pd

from cr_strat.bonds import ANALYTICS_COLUMNS, Bond, CashFlow, _Flows, _shift_months
from cr_strat.calendars import BusinessCalendar, calendar_for
from cr_strat.conventions import (
    CORPORATE_CONVENTIONS,
    FLOW_DATE_RULES,
    LEAP_YEAR_RULES,
    NCD,
    PERIOD_END_RULES,
    ROLLS,
    CorporateConventions,
    as_date,
    as_int,
    ncd_conventions,
    round_half_up,
)
from cr_strat.rating_scale import LT_GRADES, LT_NOTCH, parse_rating

SCHEDULES = ("forward", "backward", "month_start", "explicit")
FIRST_PERIODS = ("separate", "clubbed")
BOND_FREQUENCIES = (0, 1, 2, 4, 12)
OPTION_KINDS = ("call", "put")
TIERS = ("", "at1", "tier2")
FLOW_KINDS = ("coupon", "principal", "redemption")
STEP_EFFECTIVE = ("next_period", "from_event")
STEP_COMBINE = ("worst", "best", "second_best")
DEEMED_PERPETUAL_YEARS = 100  # SEBI MF master circular 10.4.2: perpetuals deemed to mature 100 years after issue
PUT_FAVOUR_BP = 30.0  # SEBI MF master circular 10.3.4: a put favours the holder 30 bp above the coupon
DATE_ONLY_TIME = time(23, 59, 59)  # UNVERIFIED: a notice or rating known only by its date counts from day's end
EXPLICIT_GAP_DAYS = 3  # an explicit schedule's gaps may differ from whole months by this (month ends, Feb)
RECORD_PAYMENT_DAYS = 7  # record dates this close to their due date are kept before the payment date
INDIA = timezone(timedelta(hours=5, minutes=30))
_EPS = 1e-9


# ---------------------------------------------------------------- day count


def _anniversary_index(anchor: date, d: date) -> int:
    """``k`` with the ``k``-th anniversary of ``anchor`` on or before ``d`` and the next one after it."""
    k = d.year - anchor.year - 1
    while _shift_months(anchor, 12 * (k + 1)) <= d:
        k += 1
    while _shift_months(anchor, 12 * k) > d:
        k -= 1
    return k


def _year_days(lo: date, hi: date, anchor: date) -> int:
    """Days in the coupon year ``[lo, hi)``: its length, which is 366 exactly when it holds a 29 February. A 29
    February anchor's anniversaries fall on 28 February in common years, so its years are counted 366 when they
    hold a 29 February and 365 otherwise (SEBI 2016 para 3(b): 366 for the year holding 29 February; UNVERIFIED
    reading for such an anchor, whose year lengths then differ from 365 and 366 by a day)."""
    if not (anchor.month == 2 and anchor.day == 29):
        return (hi - lo).days
    holds = any(_cal.isleap(y) and lo <= date(y, 2, 29) < hi for y in range(lo.year, hi.year + 1))
    return 366 if holds else 365


def year_fraction_sebi(start, end, *, rule: str = "anniversary", anchor=None) -> float:
    """Years from ``start`` (included) to ``end`` (excluded) on SEBI's actual/actual, under ``rule``.

    ``anniversary``: the sum, over the years between anniversaries of ``anchor`` (the deemed date of allotment)
    that the span overlaps, of the days in each over that year's 365 or 366 days (a 29 February anchor falls on
    28 February in common years, and its years count 366 only when they hold a 29 February: ``_year_days``).
    ``calendar``: the same over calendar years. ``fixed365``: days / 365. The rules agree for bonds allotted on 1
    January (SEBI 2016's example: 182/366 and 184/366 for the two half-years of 2016).
    """
    a, b = as_date(start), as_date(end)
    if rule not in LEAP_YEAR_RULES:
        raise ValueError(f"rule must be one of {LEAP_YEAR_RULES}, not {rule!r}")
    if b < a:
        raise ValueError(f"end {b} is before start {a}")
    if a == b:
        return 0.0
    if rule == "fixed365":
        return (b - a).days / 365
    total, x = 0.0, a
    if rule == "calendar":
        while x < b:
            nxt = min(b, date(x.year + 1, 1, 1))
            total += (nxt - x).days / (366 if _cal.isleap(x.year) else 365)
            x = nxt
        return total
    if anchor is None:
        raise ValueError("the anniversary rule needs anchor=, the deemed date of allotment")
    anchor = as_date(anchor)
    k = _anniversary_index(anchor, a)
    while x < b:
        lo, hi = _shift_months(anchor, 12 * k), _shift_months(anchor, 12 * (k + 1))
        nxt = min(b, hi)
        total += (nxt - x).days / (hi - lo).days
        x, k = nxt, k + 1
    return total


# ---------------------------------------------------------------- times known at a moment


def public_time(value, what: str = "public_from") -> datetime | None:
    """A time (India, no zone) from a datetime, Timestamp or ISO text; a bare date counts from 23:59:59 that day.

    Blank gives ``None``. A time with a zone is converted to India time. The end-of-day rule for a date without a
    time is UNVERIFIED (ratings and exercise notices are often known only by their date).
    """
    if value is None or (isinstance(value, float) and math.isnan(value)) or value is pd.NaT:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if len(text) == 10:
            return datetime.combine(date.fromisoformat(text), DATE_ONLY_TIME)
        try:
            value = datetime.fromisoformat(text)
        except ValueError:
            raise ValueError(f"{what} must be an ISO date or date and time, not {value!r}") from None
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        return value.astimezone(INDIA).replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, date):
        return datetime.combine(value, DATE_ONLY_TIME)
    raise TypeError(f"{what} must be a date or time, not {value!r}")


# ---------------------------------------------------------------- records


@dataclass(frozen=True)
class CouponPeriod:
    """One period of a corporate bond's schedule; amounts per 100 original face.

    ``start``/``end``: accrual dates (``end`` is the due date, or the payment date under a ``to_payment`` rule);
    ``face``: outstanding face during the period; ``principal``: principal paid on ``due`` (the final redemption
    for the last period); ``rate_pct``: the rate at ``start``; ``amount``: the coupon, piecewise over rate steps;
    ``kind``: ``coupon``, ``principal`` (a partial redemption date) or ``redemption``.
    """

    index: int
    start: date
    end: date
    due: date
    payment: date
    record: date
    face: float
    principal: float
    rate_pct: float
    amount: float
    kind: str


@dataclass(frozen=True)
class CallPut:
    """An issuer's call or an investor's put: on ``date`` at ``price`` per 100 of the outstanding face.

    ``notice_days``: notice the terms require (descriptive). ``portion``: share of the outstanding face it
    redeems (only 1 is built for exercise). ``exercised``: ``True`` once exercised, ``False`` once known not
    to be, ``None`` unknown; ``exercised_public_from``: when that became public (a bare date counts from
    23:59:59, UNVERIFIED). ``source``: where the terms come from. ``record_date``: the record date of the
    redemption by this option, when the notice gives one (default: the bond's rule, ``record_days`` before
    ``date``); an exercise made public after it is refused.
    """

    kind: str
    date: date
    price: float = 100.0
    notice_days: int | None = None
    portion: float = 1.0
    exercised: bool | None = None
    exercised_public_from: datetime | None = None
    source: str = ""
    record_date: date | None = None

    def __post_init__(self) -> None:
        if self.kind not in OPTION_KINDS:
            raise ValueError(f"option kind must be one of {OPTION_KINDS}, not {self.kind!r}")
        object.__setattr__(self, "date", as_date(self.date))
        if self.record_date is not None:
            object.__setattr__(self, "record_date", as_date(self.record_date))
            if self.record_date >= self.date:
                raise ValueError(f"{self.kind} on {self.date}: its record date {self.record_date} must be before it")
        price = float(self.price)
        if not (math.isfinite(price) and price > 0):
            raise ValueError(f"{self.kind} on {self.date}: price must be a positive number, not {self.price!r}")
        object.__setattr__(self, "price", price)
        portion = float(self.portion)
        if not (math.isfinite(portion) and 0 < portion <= 1):
            raise ValueError(f"{self.kind} on {self.date}: portion must be in (0, 1], not {self.portion!r}")
        object.__setattr__(self, "portion", portion)
        if self.notice_days is not None:
            object.__setattr__(self, "notice_days", as_int(self.notice_days, "notice_days", minimum=0))
        if self.exercised not in (None, True, False):
            raise ValueError(f"exercised must be True, False or None, not {self.exercised!r}")
        object.__setattr__(self, "exercised_public_from", public_time(self.exercised_public_from,
                                                                     "exercised_public_from"))
        if self.exercised is not None and self.exercised_public_from is None:
            raise ValueError(f"{self.kind} on {self.date}: an exercise decision needs exercised_public_from, the time "
                             "its notice became public")


@dataclass(frozen=True)
class WorkoutYield:
    """A workout (``maturity``, ``call`` or ``put``) with its redemption price and the yield to it (percent)."""

    kind: str
    date: date
    price: float
    yield_pct: float


@dataclass(frozen=True)
class SebiValuation:
    """A bond's value by SEBI's mutual-fund rule for bonds with calls and puts (MF master circular 10.3.1).

    ``clean_price`` and ``dirty_price`` per 100 at ``yield_pct`` to ``date``, the workout valued to (``kind``:
    ``maturity``, ``call``, ``put``, ``deemed_maturity`` or ``first_call`` for AT1). ``call_trigger``: the call
    with the lowest value among calls and maturity (``None`` when maturity is lowest); ``put_trigger``: the put
    with the highest value among puts and maturity. ``prices``: (kind, date, clean price) of every workout.
    ``put_favours_holder``: the yield exceeds the coupon by more than 30 bp (10.3.4), reported, not enforced.
    """

    settlement: date
    yield_pct: float
    clean_price: float
    dirty_price: float
    date: date
    kind: str
    call_trigger: date | None
    put_trigger: date | None
    prices: tuple[tuple[str, date, float], ...]
    put_favours_holder: bool
    notes: str = ""


# ---------------------------------------------------------------- the bond


def _month_firsts(after: date, before: date) -> list[date]:
    out, d = [], date(after.year + (after.month == 12), after.month % 12 + 1, 1)
    while d < before:
        out.append(d)
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return out


def _option(item) -> CallPut:
    if isinstance(item, CallPut):
        return item
    if isinstance(item, (tuple, list)) and len(item) in (2, 3):
        kind, when, *price = item
        return CallPut(kind, when, *price)
    raise TypeError(f"an option must be a CallPut or (kind, date[, price]), not {item!r}")


@dataclass(frozen=True)
class CorporateBond(Bond):
    """A listed corporate bond (NCD) on ``conventions`` (default ``NCD``); prices per 100 original face.

    ``coupon_pct``: coupon, percent a year (0 with ``frequency=0`` for zero-coupon and cumulative bonds, which
    redeem ``redemption`` per 100 face). ``issue_date``: the deemed date of allotment. ``frequency``: 1, 2, 4 or
    12 coupons a year, or 0. ``schedule``, ``first_coupon_date``, ``due_dates``, ``first_period``: how due dates
    are made (module docstring); an explicit schedule may carry ``payment_dates`` and ``record_dates`` aligned
    with ``due_dates``. ``amortisation``: (date, principal per 100 original face) on due dates before maturity;
    the rest is redeemed at maturity at ``redemption`` per 100 of the outstanding face. ``rate_steps``:
    (effective date, rate percent[, status]); the rate holds from that date until the next step. ``options``:
    calls and puts. ``perpetual``: the maturity is a deemed one. ``tier``: ``at1`` or ``tier2`` for banks'
    capital bonds. ``record_days``, ``record_roll``, ``flow_dates``, ``leap_year_rule``, ``postponed_coupon``,
    ``redemption_interest``: per-issue overrides of the conventions. ``anchor``: the date coupon years are
    counted from (default from ``conventions.anniversary_anchor``). ``calendar``: rolls payment dates (none
    roll without it); ``record_calendar``: rolls record dates (default ``calendar``). ``face_value_rs``: rupees
    per security, for cash amounts.
    """

    coupon_pct: float
    maturity: date
    issue_date: date
    conventions: CorporateConventions = NCD
    frequency: int = 1
    schedule: str = "forward"
    first_coupon_date: date | None = None
    due_dates: tuple[date, ...] = ()
    payment_dates: tuple[date, ...] = ()
    record_dates: tuple[date, ...] = ()
    first_period: str = "separate"
    redemption: float = 100.0
    amortisation: tuple[tuple[date, float], ...] = ()
    rate_steps: tuple[tuple, ...] = ()
    options: tuple[CallPut, ...] = ()
    perpetual: bool = False
    tier: str = ""
    record_days: int | None = None
    record_roll: str | None = None
    flow_dates: str | None = None
    leap_year_rule: str | None = None
    anchor: date | None = None
    postponed_coupon: str | None = None
    redemption_interest: str | None = None
    calendar: BusinessCalendar | None = None
    record_calendar: BusinessCalendar | None = None
    face_value_rs: float | None = None
    isin: str = ""
    name: str = ""

    # ---- construction

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        c = self.conventions
        if not isinstance(c, CorporateConventions):
            raise TypeError("conventions must be CorporateConventions (NCD, NCD_2013, NCD_PRE2013 or a variant)")
        for name in ("maturity", "issue_date"):
            set_(self, name, as_date(getattr(self, name)))
        for name in ("first_coupon_date", "anchor"):
            if getattr(self, name) is not None:
                set_(self, name, as_date(getattr(self, name)))
        for name in ("due_dates", "payment_dates", "record_dates"):
            set_(self, name, tuple(as_date(d) for d in getattr(self, name)))
        where = self.isin or self.name or f"{self.coupon_pct:g}% {self.maturity}"
        set_(self, "_where", where)
        coupon = float(self.coupon_pct)
        if not (math.isfinite(coupon) and coupon >= 0):
            raise ValueError(f"{where}: coupon_pct must be a number >= 0, not {self.coupon_pct!r}")
        set_(self, "coupon_pct", coupon)
        set_(self, "frequency", as_int(self.frequency, "frequency", minimum=0))
        if self.frequency not in BOND_FREQUENCIES:
            raise ValueError(f"{where}: frequency must be one of {BOND_FREQUENCIES}, not {self.frequency}")
        if self.frequency == 0 and (coupon or self.rate_steps):
            raise ValueError(f"{where}: frequency 0 is a zero-coupon or cumulative bond: pass coupon_pct=0 and the "
                             "redemption amount per 100 face (e.g. redemption=278.05)")
        if not self.issue_date < self.maturity:
            raise ValueError(f"{where}: issue_date {self.issue_date} is not before maturity {self.maturity}")
        redemption = float(self.redemption)
        if not (math.isfinite(redemption) and redemption > 0):
            raise ValueError(f"{where}: redemption must be a positive amount per 100 face, not {self.redemption!r}")
        set_(self, "redemption", redemption)
        for name, value, allowed in (("schedule", self.schedule, SCHEDULES),
                                     ("first_period", self.first_period, FIRST_PERIODS),
                                     ("tier", self.tier, TIERS)):
            if value not in allowed:
                raise ValueError(f"{where}: {name} must be one of {allowed}, not {value!r}")
        for name in ("calendar", "record_calendar"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, BusinessCalendar):
                raise TypeError(f"{where}: {name} must be a BusinessCalendar")
        if self.face_value_rs is not None:
            face_rs = float(self.face_value_rs)
            if not (math.isfinite(face_rs) and face_rs > 0):
                raise ValueError(f"{where}: face_value_rs must be a positive amount, not {self.face_value_rs!r}")
            set_(self, "face_value_rs", face_rs)
        self._resolve_rules()
        dues = self._generate_dues()
        set_(self, "_dues", tuple(dues))
        set_(self, "_n", len(dues))
        self._check_aligned()
        self._build_principal()
        self._build_steps()
        set_(self, "options", tuple(sorted((_option(o) for o in self.options), key=lambda o: (o.date, o.kind))))
        for o in self.options:
            if not self.issue_date < o.date < self.maturity:
                raise ValueError(f"{where}: {o.kind} date {o.date} is not between issue {self.issue_date} and "
                                 f"maturity {self.maturity}")
        keys = [(o.kind, o.date) for o in self.options]
        if len(set(keys)) != len(keys):
            raise ValueError(f"{where}: an option is listed twice on one date")

    def _resolve_rules(self) -> None:
        c, where = self.conventions, self._where
        rule = c.leap_year_rule if self.leap_year_rule is None else self.leap_year_rule
        if rule not in LEAP_YEAR_RULES:
            raise ValueError(f"{where}: leap_year_rule must be one of {LEAP_YEAR_RULES}, not {rule!r}")
        anchor = self.anchor or (self.issue_date if c.anniversary_anchor == "issue" else self.maturity)
        record_days = c.ex_coupon_days if self.record_days is None else as_int(self.record_days, "record_days",
                                                                               minimum=0)
        record_roll = c.record_roll if self.record_roll is None else self.record_roll
        if record_roll not in ROLLS:
            raise ValueError(f"{where}: record_roll must be one of {ROLLS}, not {record_roll!r}")
        flow_dates = c.flow_dates if self.flow_dates is None else self.flow_dates
        if flow_dates not in FLOW_DATE_RULES:
            raise ValueError(f"{where}: flow_dates must be one of {FLOW_DATE_RULES}, not {flow_dates!r}")
        postponed = c.postponed_coupon if self.postponed_coupon is None else self.postponed_coupon
        red_interest = c.redemption_interest if self.redemption_interest is None else self.redemption_interest
        for name, value in (("postponed_coupon", postponed), ("redemption_interest", red_interest)):
            if value not in PERIOD_END_RULES:
                raise ValueError(f"{where}: {name} must be one of {PERIOD_END_RULES}, not {value!r}")
        for name, value in (("_yf_rule", rule), ("_anchor", anchor), ("_record_days", record_days),
                            ("_record_roll", record_roll), ("_flow_rule", flow_dates), ("_postponed", postponed),
                            ("_red_interest", red_interest)):
            object.__setattr__(self, name, value)

    def _generate_dues(self) -> list[date]:
        where, mat, issue, fc = self._where, self.maturity, self.issue_date, self.first_coupon_date
        if fc is not None and not issue < fc <= mat:
            raise ValueError(f"{where}: first_coupon_date {fc} must fall after issue {issue} and on or before "
                             f"maturity {mat}")
        if self.frequency == 0:
            if self.schedule == "explicit" and self.due_dates not in ((), (mat,)):
                raise ValueError(f"{where}: a zero-coupon or cumulative bond has one flow, at maturity")
            return [mat]
        months = 12 // self.frequency
        if self.schedule == "explicit":
            if self.first_period == "clubbed":
                raise ValueError(f"{where}: first_period 'clubbed' does not apply to an explicit schedule; list the "
                                 "due dates as they are paid")
            dues = list(self.due_dates)
            if not dues:
                raise ValueError(f"{where}: an explicit schedule needs due_dates")
            bad = [(a, b) for a, b in pairwise(dues) if not a < b]
            if bad:
                raise ValueError(f"{where}: due dates must increase: {bad[0][1]} follows {bad[0][0]}")
            if dues[0] <= issue:
                raise ValueError(f"{where}: the first due date {dues[0]} is not after issue {issue}")
            if dues[-1] != mat:
                raise ValueError(f"{where}: the last due date {dues[-1]} is not the maturity {mat}")
            # every gap between due dates but the last (a stub) is one coupon period of the frequency: a frequency
            # that disagrees with the dates would put coupons and the asset swap's matched rate on the wrong basis
            for a, b in pairwise(dues[:-1]):
                if abs((b - _shift_months(a, months)).days) > EXPLICIT_GAP_DAYS:
                    raise ValueError(f"{where}: the due dates {a} and {b} are {(b - a).days} days apart, not one "
                                     f"period of frequency {self.frequency} ({months} months): give the frequency "
                                     "the dates follow (an irregular schedule is not built; a first or last stub "
                                     "is)")
            return dues
        if self.due_dates:
            raise ValueError(f"{where}: due_dates are given but schedule is {self.schedule!r}; pass "
                             "schedule='explicit'")
        if self.schedule == "forward":
            start, k = (fc, 0) if fc is not None else (issue, 1)
            dues = []
            while (d := _shift_months(start, k * months)) < mat:
                dues.append(d)
                k += 1
            dues.append(mat)
        elif self.schedule == "backward":
            dues, k = [], 0
            while (d := _shift_months(mat, -k * months)) > issue:
                dues.append(d)
                k += 1
            dues.reverse()
            if fc is not None:
                if fc not in dues:
                    raise ValueError(f"{where}: first_coupon_date {fc} is not on the schedule counted back from "
                                     f"maturity {mat}")
                dues = dues[dues.index(fc):]
        else:  # month_start
            if self.frequency != 12:
                raise ValueError(f"{where}: a month_start schedule is monthly: frequency must be 12, not "
                                 f"{self.frequency}")
            if fc is not None and fc.day != 1 and fc != mat:
                raise ValueError(f"{where}: first_coupon_date {fc} of a month_start schedule must be a 1st")
            firsts = _month_firsts(issue, mat)
            dues = [d for d in firsts if fc is None or d >= fc] + [mat]
        if self.first_period == "clubbed" and len(dues) > 1:
            dues = dues[1:]
        return dues

    def _check_aligned(self) -> None:
        where, dues = self._where, self._dues
        for name in ("payment_dates", "record_dates"):
            values = getattr(self, name)
            if not values:
                continue
            if self.schedule != "explicit" and self.frequency:
                raise ValueError(f"{where}: {name} need an explicit schedule (schedule='explicit', due_dates=...)")
            if len(values) != len(dues):
                raise ValueError(f"{where}: {len(values)} {name} for {len(dues)} due dates")
            for due, other in zip(dues, values):
                if name == "payment_dates" and abs((other - due).days) > 31:
                    raise ValueError(f"{where}: payment date {other} is more than 31 days from its due date {due}")
                if name == "record_dates" and not other <= due:
                    raise ValueError(f"{where}: record date {other} is after its due date {due}")
            bad = [(a, b) for a, b in pairwise(values) if not a < b]
            if bad:
                raise ValueError(f"{where}: {name} must increase with the due dates: {bad[0][1]} follows {bad[0][0]}")
        if self.payment_dates and self.record_dates:
            for due, pay, record in zip(dues, self.payment_dates, self.record_dates):
                if not record < pay:
                    raise ValueError(f"{where}: the record date {record} of the flow due {due} is not before its "
                                     f"payment date {pay}")

    def _build_principal(self) -> None:
        where, dues, mat = self._where, self._dues, self.maturity
        amort: dict[date, float] = {}
        for item in self.amortisation:
            if not isinstance(item, (tuple, list)) or len(item) != 2:
                raise TypeError(f"{where}: amortisation rows are (date, principal per 100 face), not {item!r}")
            d, p = as_date(item[0]), float(item[1])
            if not (math.isfinite(p) and p > 0):
                raise ValueError(f"{where}: amortisation on {d} must be a positive principal, not {item[1]!r}")
            if d in amort:
                raise ValueError(f"{where}: amortisation date {d} listed twice")
            if d not in dues:
                raise ValueError(f"{where}: amortisation date {d} is not a due date of the schedule; partial "
                                 "redemptions between due dates are not built")
            amort[d] = p
        before = sum(p for d, p in amort.items() if d < mat)
        if before >= 100 - _EPS:
            raise ValueError(f"{where}: amortisation before maturity redeems {before:g} of 100")
        if mat in amort and abs(before + amort[mat] - 100) > 1e-6:
            raise ValueError(f"{where}: amortisation sums to {before + amort[mat]:g}, not 100")
        faces, principal, face = [], [], 100.0
        for d in dues:
            faces.append(face)
            if d == mat:
                principal.append(face * self.redemption / 100)
            else:
                p = amort.get(d, 0.0)
                principal.append(p)
                face -= p
        object.__setattr__(self, "amortisation", tuple(sorted(amort.items())))
        object.__setattr__(self, "_faces", tuple(faces))
        object.__setattr__(self, "_principal", tuple(principal))

    def _build_steps(self) -> None:
        where, steps = self._where, []
        for item in self.rate_steps:
            if not isinstance(item, (tuple, list)) or len(item) not in (2, 3):
                raise TypeError(f"{where}: rate steps are (effective date, rate percent[, status]), not {item!r}")
            d, rate = as_date(item[0]), float(item[1])
            status = str(item[2]) if len(item) == 3 else ""
            if not (math.isfinite(rate) and rate >= 0):
                raise ValueError(f"{where}: rate step on {d} must be a rate >= 0, not {item[1]!r}")
            if d < self.issue_date:
                raise ValueError(f"{where}: rate step on {d} is before issue {self.issue_date}")
            steps.append((d, rate, status))
        steps.sort(key=lambda s: s[0])
        dupes = [a[0] for a, b in pairwise(steps) if a[0] == b[0]]
        if dupes:
            raise ValueError(f"{where}: two rate steps on {dupes[0]}")
        object.__setattr__(self, "rate_steps", tuple(steps))
        object.__setattr__(self, "_step_dates", tuple(s[0] for s in steps))
        object.__setattr__(self, "_step_rates", tuple(s[1] for s in steps))

    # ---- rates, dates and amounts

    def rate_on(self, d) -> float:
        """Coupon rate (percent a year) in force on ``d``."""
        i = bisect.bisect_right(self._step_dates, as_date(d)) - 1
        return self.coupon_pct if i < 0 else self._step_rates[i]

    def _yf(self, a: date, b: date) -> float:
        return year_fraction_sebi(a, b, rule=self._yf_rule, anchor=self._anchor)

    def _interest(self, face: float, a: date, b: date) -> float:
        """Interest per 100 original face on ``face`` outstanding from ``a`` to ``b`` (negative if ``b < a``)."""
        if b < a:
            return -self._interest(face, b, a)
        if a == b or not face:
            return 0.0
        i = bisect.bisect_right(self._step_dates, a)
        total, x, rate = 0.0, a, self.rate_on(a)
        while i < len(self._step_dates) and self._step_dates[i] < b:
            e = self._step_dates[i]
            total += rate * self._yf(x, e)
            x, rate, i = e, self._step_rates[i], i + 1
        total += rate * self._yf(x, b)
        return face / 100 * total

    def _payment(self, k: int) -> date:
        if self.payment_dates:
            return self.payment_dates[k]
        due = self._dues[k]
        if self.calendar is None:
            return due
        c = self.conventions
        return self.calendar.adjust(due, c.redemption_roll if self._principal[k] > 0 else c.coupon_roll)

    def _record(self, k: int) -> date:
        """Record date of period ``k``; never before the allotment, whose holder is the first on record, and
        always before the payment date (a redemption paid early with a record date at the due date, say)."""
        if self.record_dates:
            return max(self.record_dates[k], self.issue_date)
        due = self._dues[k]
        day = due - timedelta(days=self._record_days)
        cal = self.record_calendar or self.calendar
        record = max(day if cal is None or not self._record_days else cal.adjust(day, self._record_roll),
                     self.issue_date)
        if (due - record).days <= RECORD_PAYMENT_DAYS:  # only near the due date can a payment rolled back precede it
            record = min(record, self._payment(k) - timedelta(days=1))
        return record

    def _end(self, k: int) -> date:
        rule = self._red_interest if k == self._n - 1 else self._postponed
        return self._payment(k) if rule == "to_payment" else self._dues[k]

    def _start(self, k: int) -> date:
        return self.issue_date if k == 0 else self._end(k - 1)

    def _amount(self, k: int) -> float:
        if not self.frequency:
            return 0.0
        return self._interest(self._faces[k], self._start(k), self._end(k))

    def _kind(self, k: int) -> str:
        if k == self._n - 1:
            return "redemption"
        return "principal" if self._principal[k] else "coupon"

    def record_date(self, due) -> date:
        """Record date of the flow due on ``due`` (a due date of the schedule, or any date by the same rule)."""
        d = as_date(due)
        if d in self._dues:
            return self._record(self._dues.index(d))
        if not self._record_days:
            return d
        day = d - timedelta(days=self._record_days)
        cal = self.record_calendar or self.calendar
        return day if cal is None else cal.adjust(day, self._record_roll)

    def periods(self) -> tuple[CouponPeriod, ...]:
        """Every period of the schedule. With a strict calendar every rolled date must fall in a covered year."""
        return tuple(self._period(k) for k in range(self._n))

    def _period(self, k: int) -> CouponPeriod:
        start = self._start(k)
        return CouponPeriod(k, start, self._end(k), self._dues[k], self._payment(k), self._record(k), self._faces[k],
                            self._principal[k], self.rate_on(start) if self.frequency else 0.0, self._amount(k),
                            self._kind(k))

    @property
    def due_schedule(self) -> tuple[date, ...]:
        """The due dates, from the first after issue to maturity."""
        return self._dues

    # ---- settlement

    def _current(self, s: date) -> int:
        """Index of the first period due after ``s``, refusing settlements the bond cannot have."""
        where = self._where
        if s < self.issue_date:
            raise ValueError(f"{where}: settlement {s} is before the issue date (deemed allotment) {self.issue_date}")
        if s >= self.maturity:
            raise ValueError(f"{where}: settlement {s} is on or after maturity {self.maturity}")
        last = self._n - 1
        record = self._record(last)
        if record < s:
            raise ValueError(f"{where}: settlement {s} is after the redemption record date {record}: trading is "
                             f"suspended until the redemption due {self.maturity}")
        return bisect.bisect_right(self._dues, s)

    def _ex_count(self, k0: int, s: date) -> int:
        """How many periods from ``k0`` on have record dates before ``s`` (their flows go to the seller)."""
        k = k0
        while k < self._n - 1 and self._record(k) < s:
            k += 1
        return k - k0

    def _use_due(self, s: date) -> bool:
        rule = self._flow_rule
        return rule == "due" or (rule == "by_date" and s >= self.conventions.due_dates_from)

    def uses_due_dates(self, settlement) -> bool:
        """True when a settlement on ``settlement`` discounts the flows on their due dates (``flow_dates``: due, or
        by date from ``due_dates_from``), False when on their payment dates."""
        return self._use_due(as_date(settlement))

    def last_cum_settlement(self) -> date:
        """The last settlement date whose buyer receives the redemption: the redemption's record date (a
        settlement on it is cum), or the day before maturity with no record date. Trading stops after it."""
        return min(self._record(self._n - 1), self.maturity - timedelta(days=1))

    def _accrued(self, k0: int, ex: int, s: date) -> float:
        if not self.frequency:
            return 0.0
        accrued = self._interest(self._faces[k0], self._start(k0), s)
        return accrued - sum(self._amount(k) for k in range(k0, k0 + ex))

    def _flows(self, settlement) -> _Flows:
        s = as_date(settlement)
        k0 = self._current(s)
        ex = self._ex_count(k0, s)
        use_due = self._use_due(s)
        by_date: dict[date, float] = {}
        for k in range(k0 + ex, self._n):
            amount = self._amount(k) + self._principal[k]
            if not amount:
                continue
            when = self._dues[k] if use_due else self._payment(k)
            if when <= s:
                raise ValueError(f"{self._where}: the flow due {self._dues[k]} is paid on {when}, not after "
                                 f"settlement {s}")
            by_date[when] = by_date.get(when, 0.0) + amount
        dates = sorted(by_date)
        c = self.conventions
        m = c.compounding_frequency
        simple = c.final_period == "simple" and len(dates) == 1 and (dates[0] - s).days < c.mm_final_days
        return _Flows(tuple(m * (d - s).days / 365 for d in dates), tuple(by_date[d] for d in dates), m, simple,
                      self._accrued(k0, ex, s))

    def cash_flows(self, settlement, until=None) -> list[CashFlow]:
        """Flows a buyer settling on ``settlement`` receives (up to due date ``until``), per 100 original face.

        Kinds ``coupon``, ``principal`` (a partial redemption) and ``redemption``; ``periods`` is the time in
        compounding periods to the date the flow is discounted on. With a strict calendar every payment date
        must fall in a covered year: pass ``until`` or a ``lenient()`` calendar.
        """
        s = as_date(settlement)
        until = None if until is None else as_date(until)
        k0 = self._current(s)
        ex = self._ex_count(k0, s)
        use_due, m = self._use_due(s), self.conventions.compounding_frequency
        out = []
        for k in range(k0 + ex, self._n):
            due = self._dues[k]
            if until is not None and due > until:
                break
            pay = self._payment(k)
            t = m * ((due if use_due else pay) - s).days / 365
            amount = self._amount(k)
            if amount:
                out.append(CashFlow(due, pay, amount, "coupon", t, self._start(k), self._end(k)))
            if self._principal[k]:
                out.append(CashFlow(due, pay, self._principal[k], self._kind(k), t))
        return out

    def cash_schedule(self, face_value_rs: float | None = None, *, decimals: int | None = None) -> pd.DataFrame:
        """Every flow of the bond from issue, with amounts per 100 face and in rupees for one security.

        ``face_value_rs`` defaults to the bond's; ``decimals`` (default ``conventions.amount_decimals``) rounds
        the rupee amounts half up into ``amount_rs_rounded`` (cash only; valuation stays unrounded).
        """
        face_rs = self.face_value_rs if face_value_rs is None else float(face_value_rs)
        decimals = self.conventions.amount_decimals if decimals is None else as_int(decimals, "decimals", minimum=0)
        rows = []
        for p in self.periods():
            flows = [("coupon", p.amount)] if p.amount else []
            if p.principal:
                flows.append((p.kind, p.principal))
            for kind, amount in flows:
                rs = math.nan if face_rs is None else amount * face_rs / 100
                rows.append({"kind": kind, "due_date": p.due, "payment_date": p.payment, "record_date": p.record,
                             "accrual_start": p.start if kind == "coupon" else None,
                             "accrual_end": p.end if kind == "coupon" else None,
                             "days": (p.end - p.start).days if kind == "coupon" else None,
                             "face_per100": p.face, "rate_pct": p.rate_pct if kind == "coupon" else None,
                             "amount_per100": amount, "amount_rs": rs,
                             "amount_rs_rounded": round_half_up(rs, decimals) if decimals is not None
                             and math.isfinite(rs) else math.nan})
        return pd.DataFrame(rows, columns=["kind", "due_date", "payment_date", "record_date", "accrual_start",
                                           "accrual_end", "days", "face_per100", "rate_pct", "amount_per100",
                                           "amount_rs", "amount_rs_rounded"])

    def previous_coupon_date(self, settlement) -> date | None:
        """The last due date on or before ``settlement`` (the issue date in the first period)."""
        k0 = self._current(as_date(settlement))
        return self.issue_date if k0 == 0 else self._dues[k0 - 1]

    def next_coupon_date(self, settlement) -> date | None:
        """The first due date after ``settlement`` (the maturity for a zero-coupon or cumulative bond)."""
        return self._dues[self._current(as_date(settlement))]

    def is_ex_interest(self, settlement) -> bool:
        """True between a coupon's record date and its due date (the seller gets that coupon)."""
        s = as_date(settlement)
        return self._ex_count(self._current(s), s) > 0

    def in_shut_period(self, settlement) -> bool:
        """True after the redemption's record date (trading suspended) or in a coupon shut period, if any."""
        s = as_date(settlement)
        if self.issue_date <= s < self.maturity and self._record(self._n - 1) < s:
            return True
        days = self.conventions.shut_days_before_coupon
        if not days or not self.issue_date <= s < self.maturity:
            return False
        nxt = self._dues[bisect.bisect_right(self._dues, s)]
        return nxt != self.maturity and 0 < (nxt - s).days <= days

    def outstanding_face(self, settlement) -> float:
        """Face outstanding (per 100 original) that a buyer settling on ``settlement`` holds."""
        s = as_date(settlement)
        k0 = self._current(s)
        return self._faces[k0 + self._ex_count(k0, s)]

    def _guess(self) -> float:
        return self.coupon_pct / 100 if self.coupon_pct else 0.07

    # ---- variants

    def with_rates(self, steps) -> CorporateBond:
        """The same bond with ``rate_steps`` replaced (rows of (effective date, rate[, status]))."""
        return replace(self, rate_steps=tuple(tuple(s) for s in steps))

    def with_compounding(self, basis: str) -> CorporateBond:
        """The same bond with its yield on another compounding basis (``conventions.COMPOUNDING``)."""
        return replace(self, conventions=replace(self.conventions, compounding=basis))

    def with_calendar(self, calendar: BusinessCalendar | None, record_calendar: BusinessCalendar | None = None
                      ) -> CorporateBond:
        return replace(self, calendar=calendar, record_calendar=record_calendar)

    # ---- workouts

    def workouts(self, settlement=None, *, as_of=None) -> list[tuple[str, date, float]]:
        """(kind, date, price) of the maturity and every option, by date; after ``settlement`` if given.

        An option is left out once its record date is before ``settlement`` (a buyer can no longer be paid by
        it) or once it is known by ``as_of`` not to be exercised (``exercised=False`` with
        ``exercised_public_from`` on or before ``as_of``). Without ``as_of`` no decision is used: an option
        stays a workout until the time a decision was public is given (rule 10: nothing before it was public).
        """
        s = None if settlement is None else as_date(settlement)
        when = None if as_of is None else public_time(as_of, "as_of")
        out = []
        for o in self.options:
            if o.exercised is False and when is not None and o.exercised_public_from <= when:
                continue
            if s is not None and not (o.date > s and self.option_record_date(o) >= s):
                continue
            out.append((o.kind, o.date, o.price))
        out.append(("maturity", self.maturity, self.redemption))
        return sorted(out, key=lambda w: (w[1], OPTION_KINDS.index(w[0]) if w[0] in OPTION_KINDS else 2))

    def to_workout(self, when, price: float | None = None, *, record_date=None) -> CorporateBond:
        """The bond redeemed on ``when`` at ``price`` per 100 of the outstanding face.

        ``price`` defaults to the price of an option on that date (else 100). Coupons before ``when`` are
        unchanged; an exercise date between due dates pays the interest accrued to it (UNVERIFIED). The coupon
        years keep the original anchor; options and amortisation from ``when`` on are dropped. ``record_date``:
        the redemption's record date (default the bond's rule, or the option's own ``record_date``).
        """
        d = as_date(when)
        if record_date is None:
            given = [o.record_date for o in self.options if o.date == d and o.record_date is not None]
            record_date = given[0] if given else None
        if d == self.maturity and price is None and record_date is None:
            return self
        if not self.issue_date < d <= self.maturity:
            raise ValueError(f"{self._where}: workout date {d} is not after issue {self.issue_date} and on or before "
                             f"maturity {self.maturity}")
        if price is None:
            prices = [o.price for o in self.options if o.date == d]
            price = prices[0] if prices else 100.0
        keep = [k for k in range(self._n) if self._dues[k] < d]
        dues = tuple(self._dues[k] for k in keep) + (d,)
        payments, records = (), ()
        if self.payment_dates:
            last = self.payment_dates[self._dues.index(d)] if d in self._dues else (
                d if self.calendar is None else self.calendar.adjust(d, self.conventions.redemption_roll))
            payments = tuple(self.payment_dates[k] for k in keep) + (last,)
        if record_date is not None:
            record_date = as_date(record_date)
            if not record_date < d:
                raise ValueError(f"{self._where}: the record date {record_date} of the redemption on {d} must be "
                                 "before it")
            records = tuple(self._record(k) for k in keep) + (record_date,)
        elif self.record_dates:
            last_record = self.record_dates[self._dues.index(d)] if d in self._dues else min(
                self.record_date(d), d)
            records = tuple(self.record_dates[k] for k in keep) + (last_record,)
        if not self.frequency:
            dues, payments = (d,), ()
            records = () if record_date is None else (record_date,)
        return replace(self, maturity=d, schedule="explicit", first_coupon_date=None, due_dates=dues,
                       payment_dates=payments, record_dates=records, first_period="separate", redemption=float(price),
                       amortisation=tuple((a, p) for a, p in self.amortisation if a < d), options=(),
                       perpetual=False, anchor=self._anchor,
                       name=f"{self.name or self._where} to {d}")

    def yields_to_workouts(self, settlement, clean_price: float, *, as_of=None) -> list[WorkoutYield]:
        """Yield (percent, on the bond's basis) to maturity and to each option date still ahead, at a clean price
        (``workouts``: a decision not to exercise is used only from ``as_of`` on)."""
        s = as_date(settlement)
        return [WorkoutYield(kind, when, price, self.to_workout(when, price).yield_from_price(clean_price, s))
                for kind, when, price in self.workouts(s, as_of=as_of)]

    def yield_to_worst(self, settlement, clean_price: float, *, include_puts: bool = False,
                       as_of=None) -> WorkoutYield:
        """The lowest yield among maturity and the calls (and the puts with ``include_puts``); ties to the earliest."""
        rows = [w for w in self.yields_to_workouts(settlement, clean_price, as_of=as_of)
                if include_puts or w.kind != "put"]
        return min(rows, key=lambda w: (w.yield_pct, w.date))

    def _prices(self, s: date, yield_pct: float, kinds: tuple[str, ...], as_of=None) -> list[tuple[str, date, float]]:
        return [(kind, when, self.to_workout(when, price).clean_price(yield_pct, s))
                for kind, when, price in self.workouts(s, as_of=as_of) if kind in kinds]

    def price_to_worst(self, settlement, yield_pct: float, *, as_of=None) -> tuple[float, date, str]:
        """(clean price, date, kind): the lowest value at ``yield_pct`` over maturity and the calls."""
        kind, when, price = min(self._prices(as_date(settlement), yield_pct, ("maturity", "call"), as_of),
                                key=lambda r: (r[2], r[1]))
        return price, when, kind

    def price_to_best_put(self, settlement, yield_pct: float, *, as_of=None) -> tuple[float, date, str]:
        """(clean price, date, kind): the highest value at ``yield_pct`` over maturity and the puts."""
        kind, when, price = max(self._prices(as_date(settlement), yield_pct, ("maturity", "put"), as_of),
                                key=lambda r: (r[2], -r[1].toordinal()))
        return price, when, kind

    def sebi_valuation(self, settlement, yield_pct: float, *, as_of=None) -> SebiValuation:
        """Value at ``yield_pct`` by SEBI's rule for bonds with calls and puts (MF master circular 10.3.1).

        A put and a call on one date at one price make that date a deemed maturity. Else the call trigger is the
        call with the lowest value among calls and maturity, the put trigger the put with the highest value among
        puts and maturity: with neither the bond is valued to maturity, with one to it, with both to the earlier.
        AT1 bonds (``tier="at1"``) are valued to their first call ahead (SEBI since Aug 2024). Values per 100
        original face, at the same yield to every date. ``as_of``: as ``workouts`` (decisions from then on).
        """
        s = as_date(settlement)
        y = float(yield_pct)
        works = self.workouts(s, as_of=as_of)
        notes = []
        end_kind, end_date, end_price = "maturity", self.maturity, self.redemption
        call_terms = {(d, p) for k, d, p in works if k == "call"}
        same = sorted((d, p) for k, d, p in works if k == "put" and (d, p) in call_terms)
        if same:
            (end_date, end_price), end_kind = same[0], "deemed_maturity"
            notes.append(f"put and call on {end_date} at {end_price:g}: deemed maturity")
        options = [(k, d, p) for k, d, p in works if k != "maturity" and d < end_date]
        price_of = {(k, d): p for k, d, p in options} | {(end_kind, end_date): end_price}
        value = {key: self.to_workout(key[1], p).clean_price(y, s) for key, p in price_of.items()}
        end_value = value[(end_kind, end_date)]
        prices = tuple(sorted(((k, d, v) for (k, d), v in value.items()), key=lambda r: (r[1], r[0])))
        call_trigger = put_trigger = None
        if self.tier == "at1":
            calls = [d for k, d, _ in options if k == "call"]
            key = ("call", calls[0]) if calls else (end_kind, end_date)
            kind = "first_call" if calls else end_kind
            notes.append("AT1: valued to its first call ahead" if calls else "AT1 with no call ahead: to maturity")
        else:
            low = min([(v, d) for (k, d), v in value.items() if k == "call"] + [(end_value, end_date)])
            high = max([(v, -d.toordinal()) for (k, d), v in value.items() if k == "put"]
                       + [(end_value, -end_date.toordinal())])
            call_trigger = None if low[1] == end_date else low[1]
            put_date = date.fromordinal(-high[1])
            put_trigger = None if put_date == end_date else put_date
            triggers = sorted([(d, k) for d, k in ((call_trigger, "call"), (put_trigger, "put")) if d is not None])
            key = (triggers[0][1], triggers[0][0]) if triggers else (end_kind, end_date)
            kind = key[0]
        bond = self.to_workout(key[1], price_of[key])
        clean = value[key]
        has_put = any(k == "put" for k, _, _ in works)
        favours = has_put and (y - self.rate_on(s)) * 100 > PUT_FAVOUR_BP
        return SebiValuation(s, y, clean, clean + bond.accrued_interest(s), key[1], kind, call_trigger, put_trigger,
                             prices, favours, "; ".join(notes))

    def option_record_date(self, option: CallPut) -> date:
        """The record date of a redemption by ``option``: its own ``record_date``, else the bond's rule."""
        return option.record_date if option.record_date is not None else self.record_date(option.date)

    def check_exercises(self) -> None:
        """Refuse an exercise whose notice became public after the record date of its redemption (the holder on
        record must be known when it is made) or on or after the exercise date itself: either the time or the
        record date is wrong, and the bond cannot be valued through it. A notice dated later than the bond's
        rule allows needs the option's own ``record_date``."""
        for o in self.options:
            if not o.exercised:
                continue
            if o.exercised_public_from.date() >= o.date:
                raise ValueError(f"{self._where}: the exercise of the {o.kind} on {o.date} is public only from "
                                 f"{o.exercised_public_from:%Y-%m-%d %H:%M}, not before the exercise date")
            record = self.option_record_date(o)
            if o.exercised_public_from > datetime.combine(record, DATE_ONLY_TIME):
                rule = ("its record_date" if o.record_date is not None else
                        f"the bond's rule ({self._record_days} days before the exercise date)")
                raise ValueError(f"{self._where}: the exercise of the {o.kind} on {o.date} is public only from "
                                 f"{o.exercised_public_from:%Y-%m-%d %H:%M}, after the redemption's record date "
                                 f"{record} by {rule}: give the record date the notice sets (record_date in "
                                 "corporate_options) or check the notice's time")

    def exercise_known(self, as_of) -> CallPut | None:
        """The earliest option whose exercise was public by ``as_of`` (``check_exercises`` refuses a late one)."""
        when = public_time(as_of, "as_of")
        if when is None:
            raise ValueError("as_of is required")
        done = [o for o in self.options if o.exercised and o.exercised_public_from <= when]
        if not done:
            return None
        first = min(done, key=lambda o: o.date)
        if first.portion < 1:
            raise NotImplementedError(f"{self._where}: a partial exercise ({first.portion:g} on {first.date}) is not "
                                      "built; enter it as amortisation at par if the price is 100")
        self.check_exercises()
        return first

    def effective_bond(self, as_of) -> CorporateBond:
        """The bond as known at ``as_of``: redeemed at the earliest option whose exercise was public by then."""
        first = self.exercise_known(as_of)
        if first is None:
            return self
        return self.to_workout(first.date, first.price, record_date=first.record_date)

    def as_known(self, as_of) -> CorporateBond:
        """The bond as known at ``as_of``: redeemed at an option whose exercise was public by then
        (``effective_bond``), else without the options known by then not to be exercised. A decision made public
        later stays in the terms unused (``workouts`` uses a decision only with an ``as_of``)."""
        when = public_time(as_of, "as_of")
        if when is None:
            raise ValueError("as_of is required")
        bond = self.effective_bond(when)
        if bond is not self:
            return bond
        keep = tuple(o for o in self.options if not (o.exercised is False and o.exercised_public_from <= when))
        return self if keep == self.options else replace(self, options=keep)

    def calendar_projected(self, settlement=None) -> bool:
        """True when a payment or record date of a flow after ``settlement`` (all flows without it) falls in a year
        the payment calendar's holiday list does not cover: a lenient calendar rolls it on weekends only there."""
        cals = [c for c in (self.calendar, self.record_calendar) if c is not None]
        if not cals:
            return False
        k0 = 0 if settlement is None else bisect.bisect_right(self._dues, as_date(settlement))
        if k0 >= self._n:
            return False
        first = self._dues[k0] - timedelta(days=self._record_days + 7)  # the first record date, rolls allowed
        last = self._dues[-1] + timedelta(days=7)
        return any(y not in c.years for c in cals for y in range(first.year, last.year + 1))


# ---------------------------------------------------------------- constructors


def perpetual_bond(issue_date, coupon_pct: float, calls=(), *, frequency: int = 1,
                   deemed_years: int = DEEMED_PERPETUAL_YEARS, tier: str = "at1", **kwargs) -> CorporateBond:
    """A perpetual bond deemed to mature ``deemed_years`` after issue (SEBI MF master circular 10.4.2: 100 years).

    ``calls``: ``CallPut`` objects or (date[, price]) pairs, taken as calls. ``tier``: ``at1`` (valued to the
    first call by ``sebi_valuation``), ``tier2`` or blank. Other keywords go to ``CorporateBond``.
    """
    issue = as_date(issue_date)
    years = as_int(deemed_years, "deemed_years", minimum=1)
    options = []
    for item in calls:
        if isinstance(item, CallPut):
            options.append(item)
        elif isinstance(item, (tuple, list)) and len(item) in (1, 2):
            options.append(CallPut("call", *item))
        else:
            options.append(CallPut("call", item))
    return CorporateBond(coupon_pct, _shift_months(issue, 12 * years), issue, frequency=frequency,
                         options=tuple(options), perpetual=True, tier=tier, **kwargs)


@dataclass(frozen=True)
class StepRule:
    """A rating-linked coupon step: ``step_bp_per_notch`` for each notch below ``threshold``.

    ``threshold``: the lowest grade with no step (``AA`` steps up at ``AA-``), or its notch. ``max_step_bp``:
    the cap. ``agencies``: agencies counted (default all); ``combine``: ``worst``, ``best`` or ``second_best``
    of their latest ratings. ``effective``: ``next_period`` (from the next coupon period after the action is
    public, the default when the terms are silent, UNVERIFIED) or ``from_event`` (from the day after it became
    public). ``step_down``: whether an upgrade steps the coupon back down.
    """

    threshold: str | int
    step_bp_per_notch: float
    max_step_bp: float | None = None
    agencies: tuple[str, ...] | None = None
    combine: str = "worst"
    effective: str = "next_period"
    step_down: bool = True

    def __post_init__(self) -> None:
        if isinstance(self.threshold, str):
            grade = self.threshold.strip().upper()
            if grade not in LT_NOTCH:
                raise ValueError(f"threshold must be a long-term grade {LT_GRADES} or a notch, not {self.threshold!r}")
            notch = LT_NOTCH[grade]
        else:
            notch = as_int(self.threshold, "threshold", minimum=1)
        object.__setattr__(self, "threshold", notch)
        step = float(self.step_bp_per_notch)
        if not (math.isfinite(step) and step >= 0):
            raise ValueError(f"step_bp_per_notch must be >= 0, not {self.step_bp_per_notch!r}")
        object.__setattr__(self, "step_bp_per_notch", step)
        if self.max_step_bp is not None:
            cap = float(self.max_step_bp)
            if not (math.isfinite(cap) and cap >= 0):
                raise ValueError(f"max_step_bp must be >= 0, not {self.max_step_bp!r}")
            object.__setattr__(self, "max_step_bp", cap)
        if self.agencies is not None:
            object.__setattr__(self, "agencies", tuple(str(a).strip().upper() for a in self.agencies))
        if self.combine not in STEP_COMBINE:
            raise ValueError(f"combine must be one of {STEP_COMBINE}, not {self.combine!r}")
        if self.effective not in STEP_EFFECTIVE:
            raise ValueError(f"effective must be one of {STEP_EFFECTIVE}, not {self.effective!r}")

    def step_bp(self, notch: int) -> float:
        step = max(0, notch - self.threshold) * self.step_bp_per_notch
        return step if self.max_step_bp is None else min(step, self.max_step_bp)


def _combined_notch(latest: dict[str, int], combine: str) -> int | None:
    notches = sorted(latest.values())
    if not notches:
        return None
    if combine == "worst":
        return notches[-1]
    if combine == "best":
        return notches[0]
    return notches[1] if len(notches) > 1 else notches[0]


def apply_rating_steps(bond: CorporateBond, ratings: pd.DataFrame, rule: StepRule, *, as_of) -> CorporateBond:
    """The bond with the coupon steps ``rule`` gives from the rating actions public by ``as_of``.

    ``ratings``: rows with ``agency``, ``rating`` (a rating string or grade) and ``public_from`` (a time; a bare
    date counts from 23:59:59). A row without ``public_from`` is refused: a step may act only from when its
    rating was public. Each change of the combined rating sets the coupon to ``coupon_pct`` plus the step,
    effective from the next period start after it became public (``next_period``) or the day after
    (``from_event``). The bond must not already have rate steps.
    """
    if not isinstance(rule, StepRule):
        raise TypeError("rule must be a StepRule")
    if bond.rate_steps:
        raise ValueError(f"{bond._where}: the bond already has rate steps; combine fixings and rating steps by hand")
    cutoff = public_time(as_of, "as_of")
    if cutoff is None:
        raise ValueError("as_of is required")
    missing = [c for c in ("agency", "rating", "public_from") if c not in ratings]
    if missing:
        raise ValueError(f"ratings need columns {missing}")
    events = []
    for i, rec in enumerate(ratings.to_dict("records")):
        when = public_time(rec["public_from"], f"ratings row {i} public_from")
        if when is None:
            raise ValueError(f"ratings row {i} ({rec['agency']} {rec['rating']}): public_from is missing; a step acts "
                             "only from when its rating was public")
        agency = str(rec["agency"]).strip().upper()
        if rule.agencies is not None and agency not in rule.agencies:
            continue
        parsed = parse_rating(rec["rating"])
        if parsed.scale != "LT" or parsed.notch is None:
            raise ValueError(f"ratings row {i}: {rec['rating']!r} is not a long-term rating")
        if when <= cutoff:
            events.append((when, agency, parsed.notch))
    events.sort()
    latest: dict[str, int] = {}
    steps, current = [], 0.0
    for when, agency, notch in events:
        latest[agency] = notch
        combined = _combined_notch(latest, rule.combine)
        step = rule.step_bp(combined)
        if not rule.step_down:
            step = max(step, current)
        if step == current:
            continue
        current = step
        day = when.date()
        if rule.effective == "from_event":
            effective = day + timedelta(days=1)
        else:
            later = [bond.issue_date] + list(bond.due_schedule[:-1])
            starts = [d for d in later if d > day]
            if not starts:
                continue
            effective = starts[0]
        effective = max(effective, bond.issue_date)
        if effective >= bond.maturity:
            continue
        steps = [s for s in steps if s[0] < effective]
        steps.append((effective, bond.coupon_pct + step / 100, f"rating step {step:g} bp ({agency}, {when:%Y-%m-%d})"))
    return bond.with_rates(steps)


# ---------------------------------------------------------------- term tables

TERMS_COLUMNS = ("isin", "issuer_id", "name", "kind", "instrument_class", "coupon_pct", "frequency", "schedule",
                 "first_period", "issue_date", "maturity_date", "first_coupon_date", "redemption_per100",
                 "face_value_rs", "conventions", "record_days", "record_roll", "leap_year_rule", "postponed_coupon",
                 "redemption_interest", "flow_dates", "tier", "sector", "rating_bucket", "source", "status")
TERMS_REQUIRED = ("isin", "kind", "coupon_pct", "frequency", "issue_date")
TERM_KINDS = ("corporate", "perpetual", "frb_corp")
SCHEDULE_COLUMNS = ("isin", "due_date", "payment_date", "record_date", "kind", "amount_per100", "source")
OPTION_COLUMNS = ("isin", "kind", "date", "price_per100", "notice_days", "portion", "exercised",
                  "exercised_public_from", "record_date", "source")
AMORTISATION_COLUMNS = ("isin", "date", "principal_per100", "basis", "source")
STEP_COLUMNS = ("isin", "threshold", "step_bp_per_notch", "max_step_bp", "agencies", "combine", "effective",
                "step_down", "source")
AMORTISATION_BASES = ("face", "quantity")
AMOUNT_TOLERANCE = 1e-6  # a schedule's typed amount must match the computed one within this, per 100 face
PRICED_COLUMNS = (*ANALYTICS_COLUMNS, "workout_date", "workout_kind", "call_trigger", "put_trigger", "error")
_YES = {"yes", "y", "true", "1"}
_NO = {"no", "n", "false", "0"}


def _cell(value) -> str:
    if value is None or value is pd.NaT or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value).strip()


def _rows_for(frame: pd.DataFrame | None, isin: str) -> list[dict]:
    if frame is None or frame.empty:
        return []
    rows = frame.loc[frame["isin"].map(_cell).str.upper() == isin.strip().upper()].to_dict("records")
    return [{k: _cell(v) for k, v in r.items()} for r in rows]


def normalise_isin(value) -> str:
    """An ISIN as the term tables key it: stripped and upper-case (blank for a missing cell)."""
    return _cell(value).upper()


def read_term_table(source, columns: tuple[str, ...], required: tuple[str, ...], key: tuple[str, ...],
                    what: str) -> pd.DataFrame:
    """A CSV (path) or frame of text cells with ``columns``, blank rows dropped, one row per ``key``.

    Headers are matched case-insensitively; optional columns missing from the file are added blank. A column
    the table does not read is refused, naming the nearest known one (a misspelt optional column would
    otherwise fall back to its default silently); a blank, unnamed column (a trailing comma) is dropped. ISINs
    are stripped and upper-cased. A key repeated with the same values is kept once; repeated with different
    values it is refused, naming the key and both rows.
    """
    if isinstance(source, pd.DataFrame):
        frame, name = source.copy(), what
    else:
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"{what} CSV not found: {path}")
        frame, name = pd.read_csv(path, dtype=str, keep_default_na=False), str(path)
    frame.columns = [str(c).strip().lower() for c in frame.columns]
    blank = [c for c in frame.columns if (not c or c.startswith("unnamed:"))
             and all(not _cell(v) for v in frame[c])]
    frame = frame.drop(columns=blank)
    unknown = [c for c in frame.columns if c not in columns]
    if unknown:
        hints = []
        for c in unknown:
            close = difflib.get_close_matches(c, columns, n=1, cutoff=0.6)
            hints.append(repr(c) + (f" (did you mean {close[0]!r}?)" if close else ""))
        raise ValueError(f"{name}: unknown column {', '.join(hints)}: the {what} table reads only "
                         f"{', '.join(columns)}")
    missing = [c for c in required if c not in frame]
    if missing:
        raise ValueError(f"{name}: needs columns {missing}")
    for c in columns:
        if c not in frame:
            frame[c] = ""
    for c in frame.columns:
        frame[c] = [_cell(v) for v in frame[c]]
    if "isin" in frame:
        frame["isin"] = frame["isin"].str.upper()
    frame = frame[frame.ne("").any(axis=1)].reset_index(drop=True)
    for c in required:
        blank = frame.index[frame[c] == ""].tolist()
        if blank:
            raise ValueError(f"{name}: row {blank[0] + 2} has no {c}")
    seen: dict[tuple, int] = {}
    keep = []
    for i, rec in enumerate(frame.to_dict("records")):
        k = tuple(rec[c] for c in key)
        if k in seen:
            first = frame.iloc[seen[k]].to_dict()
            if first != rec:
                diff = sorted(c for c in rec if rec[c] != first[c])
                raise ValueError(f"{name}: key {k} is given twice with different values (rows {seen[k] + 2} "
                                 f"and {i + 2} differ in {diff})")
            continue
        seen[k] = i
        keep.append(i)
    return frame.iloc[keep].reset_index(drop=True)


def read_corporate_terms(source) -> pd.DataFrame:
    """``corporate_terms.csv``: one row per ISIN (``TERMS_COLUMNS``; required ``TERMS_REQUIRED``)."""
    frame = read_term_table(source, TERMS_COLUMNS, TERMS_REQUIRED, ("isin",), "corporate terms")
    bad = sorted(set(frame["kind"]) - set(TERM_KINDS))
    if bad:
        raise ValueError(f"corporate terms: kind must be one of {TERM_KINDS}, not {bad}")
    return frame


def read_corporate_schedule(source) -> pd.DataFrame:
    """``corporate_schedule.csv``: due dates (and payment and record dates, amounts) per ISIN."""
    frame = read_term_table(source, SCHEDULE_COLUMNS, ("isin", "due_date"), ("isin", "due_date", "kind"),
                            "corporate schedule")
    bad = sorted(set(frame["kind"]) - {"", *FLOW_KINDS})
    if bad:
        raise ValueError(f"corporate schedule: kind must be one of {FLOW_KINDS} or blank, not {bad}")
    return frame


def read_corporate_options(source) -> pd.DataFrame:
    """``corporate_options.csv``: calls and puts per ISIN (``OPTION_COLUMNS``)."""
    return read_term_table(source, OPTION_COLUMNS, ("isin", "kind", "date"), ("isin", "kind", "date"),
                           "corporate options")


def read_amortisation(source) -> pd.DataFrame:
    """``corporate_amortisation.csv``: principal per 100 original face by date (``AMORTISATION_COLUMNS``)."""
    return read_term_table(source, AMORTISATION_COLUMNS, ("isin", "date", "principal_per100"), ("isin", "date"),
                           "corporate amortisation")


def read_step_rules(source) -> pd.DataFrame:
    """``corporate_steps.csv``: one rating-step rule per ISIN (``STEP_COLUMNS``)."""
    return read_term_table(source, STEP_COLUMNS, ("isin", "threshold", "step_bp_per_notch"), ("isin",),
                           "corporate steps")


def _number(text: str, what: str, *, default: float | None = None) -> float | None:
    if text is None or str(text).strip() == "":
        return default
    try:
        value = float(text)
    except ValueError:
        raise ValueError(f"{what} must be a number, not {text!r}") from None
    if not math.isfinite(value):
        raise ValueError(f"{what} must be finite, not {text!r}")
    return value


def _flag(text: str, what: str) -> bool | None:
    t = str(text).strip().lower()
    if t == "":
        return None
    if t in _YES:
        return True
    if t in _NO:
        return False
    raise ValueError(f"{what} must be yes, no or blank, not {text!r}")




def step_rule_from_row(row) -> StepRule:
    """A ``StepRule`` from a ``corporate_steps.csv`` row."""
    isin = row.get("isin", "")
    threshold = str(row["threshold"]).strip()
    agencies = tuple(a for a in (x.strip() for x in str(row.get("agencies", "")).split(";")) if a) or None
    step_down = _flag(row.get("step_down", ""), f"{isin} step_down")
    return StepRule(int(threshold) if threshold.isdigit() else threshold,
                    _number(row["step_bp_per_notch"], f"{isin} step_bp_per_notch"),
                    _number(row.get("max_step_bp", ""), f"{isin} max_step_bp"), agencies,
                    row.get("combine", "") or "worst", row.get("effective", "") or "next_period",
                    True if step_down is None else step_down)


def default_calendar(conventions: CorporateConventions) -> BusinessCalendar:
    """The conventions' payment calendar, lenient beyond the years its holiday list covers (weekends only there)."""
    return calendar_for(conventions.payment_calendar_name).lenient()


_WEEKDAYS = BusinessCalendar("weekdays", strict=False)


def default_as_of(bond: CorporateBond, settlement, trade_date=None) -> datetime:
    """00:00 on the trade date: ``trade_date`` if given, else ``settlement`` less the bond's settlement lag in
    business days on its payment calendar (weekdays without one).

    The default ``as_of`` of the batch functions (``price_corporates``, ``credit_spreads.spread_table``,
    ``trades.check_trade_yields``, ``asset_swaps.asset_swap_table``): an exercise, or a decision not to
    exercise, is used only once public before trading began that day, a trade's own time being rarely known
    (UNVERIFIED default, conservative: a notice made public during the trade day counts from the next day; a
    T+0 trade taken as T+1 counts from a day earlier still).
    """
    if trade_date is not None and not (isinstance(trade_date, float) and math.isnan(trade_date)) \
            and trade_date is not pd.NaT and str(trade_date).strip():
        return datetime.combine(as_date(trade_date), time(0, 0))
    s = as_date(settlement)
    cal = _WEEKDAYS if bond.calendar is None else bond.calendar.lenient()
    return datetime.combine(cal.add_business_days(s, -bond.conventions.settlement_lag)
                            if bond.conventions.settlement_lag else s, time(0, 0))


def _as_of_for(bond: CorporateBond, rec: dict, s: date, as_of) -> datetime:
    """A row's ``as_of``: the argument, else the row's ``as_of`` cell, else ``default_as_of`` (its trade date)."""
    if as_of is not None:
        return public_time(as_of, "as_of")
    cell = rec.get("as_of")
    if cell is not None and _cell(cell):
        return public_time(_cell(cell), "as_of")
    return default_as_of(bond, s, rec.get("trade_date"))


def corporate_from_terms(row, *, schedule: pd.DataFrame | None = None, options: pd.DataFrame | None = None,
                         amortisation: pd.DataFrame | None = None, calendar="default",
                         record_calendar: BusinessCalendar | None = None, check_amounts: bool = True,
                         where: str | None = None) -> CorporateBond:
    """A ``CorporateBond`` from a terms row and the ISIN's rows of the schedule, options and amortisation tables.

    ``conventions``: ``ncd``, ``ncd_2013`` or ``ncd_pre2013`` (blank: by the issue date, ``ncd_conventions``).
    A ``perpetual`` row without a maturity is deemed to mature 100 years after issue. Schedule rows make the
    schedule explicit; their ``principal`` rows are amortisation, and every ``amount_per100`` typed is checked
    against the computed amount (beyond ``AMOUNT_TOLERANCE`` refused, naming both, unless ``check_amounts`` is
    off). ``calendar``: ``"default"`` loads the conventions' payment calendar, lenient beyond its years;
    ``None`` rolls nothing. ``frb_corp`` rows give the initial coupon; ``floating.corporate_frb_rates`` adds
    the fixings.
    """
    rec = {k: ("" if v is None or (isinstance(v, float) and math.isnan(v)) else str(v).strip())
           for k, v in dict(row).items()}
    isin = rec.get("isin", "")
    where = where or isin or "terms row"
    try:
        kind = rec.get("kind", "") or "corporate"
        if kind not in TERM_KINDS:
            raise ValueError(f"kind must be one of {TERM_KINDS}, not {kind!r}")
        issue = as_date(rec["issue_date"])
        name = rec.get("conventions", "")
        if name:
            if name not in CORPORATE_CONVENTIONS or name == "frb":
                raise ValueError(f"conventions must be ncd, ncd_2013 or ncd_pre2013, not {name!r}")
            conventions = CORPORATE_CONVENTIONS[name]
        else:
            conventions = ncd_conventions(issue)
        frequency = as_int(_number(rec.get("frequency"), "frequency"), "frequency", minimum=0)
        coupon = _number(rec.get("coupon_pct"), "coupon_pct")
        maturity_text = rec.get("maturity_date", "")
        perpetual = kind == "perpetual"
        if maturity_text:
            maturity = as_date(maturity_text)
        elif perpetual:
            maturity = _shift_months(issue, 12 * DEEMED_PERPETUAL_YEARS)
        else:
            raise ValueError("maturity_date is required (blank only for a perpetual)")
        kwargs = {}
        for column in ("record_roll", "leap_year_rule", "postponed_coupon", "redemption_interest", "flow_dates"):
            if rec.get(column):
                kwargs[column] = rec[column]
        if rec.get("record_days"):
            kwargs["record_days"] = as_int(_number(rec["record_days"], "record_days"), "record_days", minimum=0)
        cal = default_calendar(conventions) if isinstance(calendar, str) and calendar == "default" else calendar
        opts = []
        for o in _rows_for(options, isin):
            opts.append(CallPut(o["kind"], o["date"], _number(o.get("price_per100", ""), "price_per100", default=100.0),
                                None if not o.get("notice_days") else as_int(_number(o["notice_days"], "notice_days"),
                                                                             "notice_days", minimum=0),
                                _number(o.get("portion", ""), "portion", default=1.0),
                                _flag(o.get("exercised", ""), "exercised"), o.get("exercised_public_from") or None,
                                o.get("source", "")))
        amort = []
        for a in _rows_for(amortisation, isin):
            basis = a.get("basis", "") or "face"
            if basis not in AMORTISATION_BASES:
                raise ValueError(f"amortisation basis must be one of {AMORTISATION_BASES}, not {basis!r}")
            amort.append((as_date(a["date"]), _number(a["principal_per100"], "principal_per100")))
        sched = sorted(_rows_for(schedule, isin), key=lambda r: as_date(r["due_date"]))
        due_dates, payments, records, typed = [], [], [], []
        for r in sched:
            d = as_date(r["due_date"])
            if r.get("kind") == "principal":
                if not r.get("amount_per100"):
                    raise ValueError(f"the principal row due {d} needs amount_per100")
                if d not in dict(amort):
                    amort.append((d, _number(r["amount_per100"], "amount_per100")))
            if r.get("amount_per100"):
                typed.append((d, r.get("kind") or "coupon", _number(r["amount_per100"], "amount_per100")))
            if due_dates and due_dates[-1] == d:
                continue
            due_dates.append(d)
            payments.append(as_date(r["payment_date"]) if r.get("payment_date") else None)
            records.append(as_date(r["record_date"]) if r.get("record_date") else None)
        for label, values in (("payment_date", payments), ("record_date", records)):
            if any(v is not None for v in values) and any(v is None for v in values):
                raise ValueError(f"schedule {label} is given for some due dates but not all")
        if due_dates:
            kwargs.update(schedule="explicit", due_dates=tuple(due_dates),
                          payment_dates=tuple(payments) if payments and payments[0] is not None else (),
                          record_dates=tuple(records) if records and records[0] is not None else ())
        else:
            kwargs["schedule"] = rec.get("schedule", "") or "forward"
            if rec.get("first_period"):
                kwargs["first_period"] = rec["first_period"]
        bond = CorporateBond(coupon, maturity, issue, conventions, frequency=frequency,
                             first_coupon_date=as_date(rec["first_coupon_date"]) if rec.get("first_coupon_date")
                             else None,
                             redemption=_number(rec.get("redemption_per100", ""), "redemption_per100", default=100.0),
                             amortisation=tuple(amort), options=tuple(opts), perpetual=perpetual,
                             tier=rec.get("tier", ""), calendar=cal, record_calendar=record_calendar,
                             face_value_rs=_number(rec.get("face_value_rs", ""), "face_value_rs"), isin=isin,
                             name=rec.get("name", ""), **kwargs)
        if check_amounts and typed:
            _check_typed_amounts(bond, typed)
        return bond
    except KeyError as exc:
        raise ValueError(f"{where}: missing {exc}") from None
    except (ValueError, TypeError, NotImplementedError) as exc:
        text = str(exc)
        raise type(exc)(text if text.startswith(f"{where}:") else f"{where}: {text}") from None


def _check_typed_amounts(bond: CorporateBond, typed: list[tuple[date, str, float]]) -> None:
    periods = {p.due: p for p in bond.periods()}
    for d, kind, amount in typed:
        p = periods[d]
        computed = p.amount if kind == "coupon" else p.principal
        if abs(computed - amount) > AMOUNT_TOLERANCE:
            raise ValueError(f"the schedule's {kind} due {d} is {amount:.8f} per 100 face, the rules give "
                             f"{computed:.8f} ({(p.end - p.start).days} days from {p.start}); check leap_year_rule, "
                             "postponed_coupon and redemption_interest")


def price_corporates(frame: pd.DataFrame, terms: pd.DataFrame, *, schedule: pd.DataFrame | None = None,
                     options: pd.DataFrame | None = None, amortisation: pd.DataFrame | None = None,
                     given: str = "clean_price", column: str | None = None, valuation: str = "ytm",
                     calendar="default", rates: dict | None = None) -> pd.DataFrame:
    """Analytics for every row of ``frame`` (``isin``, ``settlement_date`` and the ``given`` quantity).

    ``valuation``: ``ytm`` (to maturity), ``worst`` (from a clean price the yield to worst over maturity and
    calls; from a yield the price to worst) or ``sebi`` (from a yield, SEBI's trigger rule). ``rates``: rate
    steps by ISIN for ``frb_corp`` rows (``floating.corporate_frb_rates``). A row that cannot be priced gets
    NaN and the reason in ``error``; results replace columns of the same name.
    """
    if given not in ("clean_price", "yield_pct"):
        raise ValueError("given must be 'clean_price' or 'yield_pct'")
    if valuation not in ("ytm", "worst", "sebi"):
        raise ValueError(f"valuation must be ytm, worst or sebi, not {valuation!r}")
    column = given if column is None else column
    missing = [c for c in ("isin", "settlement_date", column) if c not in frame]
    if missing:
        raise ValueError(f"missing columns: {missing}")
    terms = read_corporate_terms(terms)
    schedule = None if schedule is None else read_corporate_schedule(schedule)
    options = None if options is None else read_corporate_options(options)
    amortisation = None if amortisation is None else read_amortisation(amortisation)
    by_isin = {r["isin"]: r for r in terms.to_dict("records")}
    bonds: dict[str, CorporateBond | Exception] = {}
    outputs = [c for c in PRICED_COLUMNS if c != given]
    rows = []
    for rec in frame.to_dict("records"):
        isin = str(rec["isin"]).strip()
        try:
            if isin not in bonds:
                try:
                    if isin not in by_isin:
                        raise ValueError(f"{isin}: no terms")
                    bond = corporate_from_terms(by_isin[isin], schedule=schedule, options=options,
                                                amortisation=amortisation, calendar=calendar)
                    if by_isin[isin]["kind"] == "frb_corp":
                        if not rates or isin not in rates:
                            raise ValueError(f"{isin}: a frb_corp bond needs its fixings (rates=, from "
                                             "floating.corporate_frb_rates)")
                        bond = bond.with_rates(rates[isin])
                    bonds[isin] = bond
                except (ValueError, TypeError, NotImplementedError) as exc:
                    bonds[isin] = exc
            bond = bonds[isin]
            if isinstance(bond, Exception):
                raise bond
            value = rec[column]
            if value is None or (isinstance(value, float) and math.isnan(value)) or str(value).strip() == "":
                raise ValueError(f"{column} is missing")
            rows.append(_price_row(bond, as_date(rec["settlement_date"]), given, float(value), valuation, outputs))
        except (ValueError, TypeError, NotImplementedError) as exc:
            rows.append(dict.fromkeys(outputs, math.nan) | {"error": str(exc)})
    out = frame.copy()
    computed = pd.DataFrame(rows, columns=outputs, index=frame.index)
    for col in computed:
        out[col] = computed[col]
    return out


def _workout_price(bond: CorporateBond, s: date, kind: str, when: date) -> float:
    """Redemption price of the workout of ``kind`` on ``when`` (a deemed maturity takes the put's price)."""
    if kind == "maturity" and when == bond.maturity:
        return bond.redemption
    kind = {"first_call": "call", "deemed_maturity": "put"}.get(kind, kind)
    prices = {(k, d): p for k, d, p in bond.workouts(s)}
    return prices[(kind, when)]


def _price_row(bond: CorporateBond, s: date, given: str, value: float, valuation: str, outputs) -> dict:
    extra = {"workout_date": bond.maturity, "workout_kind": "maturity", "call_trigger": None, "put_trigger": None}
    target = bond
    if valuation == "worst":
        if given == "clean_price":
            w = bond.yield_to_worst(s, value)
            target = bond.to_workout(w.date, w.price)
        else:
            _, when, kind = bond.price_to_worst(s, value)
            target = bond.to_workout(when, _workout_price(bond, s, kind, when))
        extra.update(workout_date=target.maturity, workout_kind=w.kind if given == "clean_price" else kind)
    elif valuation == "sebi":
        if given != "yield_pct":
            raise ValueError("the SEBI valuation works from a yield: pass given='yield_pct'")
        v = bond.sebi_valuation(s, value)
        target = bond.to_workout(v.date, _workout_price(bond, s, v.kind, v.date))
        extra.update(workout_date=v.date, workout_kind=v.kind, call_trigger=v.call_trigger, put_trigger=v.put_trigger)
    a = target.analytics(s, **{given: value})
    return {c: getattr(a, c) for c in ANALYTICS_COLUMNS if c in outputs} | extra | {"error": ""}
