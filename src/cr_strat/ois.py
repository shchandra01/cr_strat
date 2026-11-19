"""MIBOR overnight indexed swaps: conventions, schedules, compounding, the discount curve and swap valuation.

Strategies S1 (G-secs against MIBOR-OIS), S2 and S7 price off this module: ``asset_swaps`` measures bonds against
its curve, ``funding`` adds repo funding and carry, and ``policy_path`` reads the policy rate the swaps imply.

**The contract.** Rules from FIMMDA's Handbook of Market Practices 2026, section 6.10.2 ("the handbook",
https://www.fimmda.org/UploadPopupPageFiles/Fimmda_Handbook_2026_Final_Ver.pdf), stated here in our own words;
the C numbers are the conventions table of the analytics spec of 19 Nov 2026:

* Floating leg (C1, C2): FBIL's overnight MIBOR compounded on Mumbai business days, simple interest over the
  days between them, ``A(S, E) = prod_i (1 + R_i n_i / 36500)`` over the business days ``i`` in ``[S, E)``,
  ``n_i`` the calendar days from ``i`` to the next business day (or to ``E``). The rate published on day ``i``
  accrues from that day: no lookback, lag or rate cut-off onshore. Floating rate ``(A - 1) 36500 / d``,
  ``d = E - S`` days.
* Both legs Actual/365 fixed; the fixed rate is a simple rate for each period (C4).
* Effective date the first Mumbai business day after the trade date (T+1; never a Saturday) (C5).
* A single exchange at maturity up to 12 months; semi-annual periods on both legs beyond (C6).
* Modified Following for accrual and payment dates (C7; that accrual dates are adjusted too is stated only in
  FIMMDA's draft for the offshore contract, FIMCIR/2026-27/16); payment on the period end (C10, partly verified).
* Business days: Mumbai, Saturdays excluded, i.e. ``calendars.mumbai_settlement_calendar`` (C8; the equivalence
  with the Mumbai settlement calendar is reasoned, not stated by FBIL or FIMMDA).
* UNVERIFIED (C9; no source found): periods counted forward from the effective date on its day of the month
  (clipped to the month's end), no end-of-month rule, no stub for standard tenors. A tenor above 12 months must be
  a whole number of 6-month periods: with no known stub rule, none is guessed.
* Rounding (C3): the compounded rate to 0.0001%, each amount to the paisa, the net to the rupee; applied only by
  ``settlement_amounts``, never in valuation. The handbook's own one-week example matches the unrounded rate
  (``rate_decimals=None`` reproduces it), against its text: a decision for the user.
* Days without a fixing (C12). Handbook 7.4 gives rules for unscheduled holidays, and says that section applies
  to trades not settled through CCIL: when MIBOR is not published on an unscheduled holiday known the working day
  before, the day accrues like any other holiday, simple interest across it (``missing="holiday"``); when the
  holiday is declared on the day before MIBOR is published, the previous day's rate is that day's
  (``"previous"``). For FBIL not publishing at all, 6.10.2(k)(1) points to the ISDA fallbacks, which are not
  built. The default, ``"raise"``, names the dates instead: a gap in the fixings given is not evidence of either
  case.

``MIBOR_OIS`` holds these rules; ``SORR_OIS`` the same on FBIL's secured overnight rate (handbook 6.16);
``FCS_MIBOR_OIS`` the offshore, foreign-currency-settled contract of FIMMDA's draft of 8 Jul 2026 (a rate cut-off
of one business day, payments on the Mumbai and New York calendars). No New York calendar is built and the cut-off
is not, so its schedules and cash flows are refused; it is kept as a record of the draft.

**FBIL's MIBOR-OIS** (C13): rates for 1, 2, 3, 6 and 9 months and 1 to 5 years each Mumbai business day, the
volume-weighted average of the day's trades reported to CCIL by 5 PM after outliers, to two decimals. They are read
as par rates of standard spot-starting swaps, simple up to a year and semi-annual beyond (partly verified: FBIL's
own statement of the quote basis was not read). ``fbil.load_fbil_money_market`` keeps the quote in
``rate_quoted_pct`` beside an annual-compounding conversion on 365/12-day months: the bootstrap uses the quote and
the real schedule dates, never the conversion. No FBIL rate exists beyond 5 years: longer quotes come from the user
(CCIL's trade prints, broker screens or offshore prints, all licensed).

**The curve** (``bootstrap_ois``). Discount factors ``P(t)`` from the trade date ``T``, ``t`` in Act/365F years:
``P(S0) = 1 / (1 + R_ON (S0 - T) / 36500)`` with the trade date's overnight fixing; a single-period quote ``K``
gives ``P(E) = P(S0) / (1 + K tau / 100)``; a semi-annual quote solves ``ln P(E_n)`` (Newton inside a bracket,
falling back to bisection) so that ``K/100 sum_k tau_k P(E_k) = P(S0) - P(E_n)``, the period ends between nodes
interpolated. Interpolation is linear in ``ln P``, i.e. flat forward rates between nodes (C17, UNVERIFIED: the
standard choice, no Indian source). Past the last node ``t_max`` the curve gives NaN unless
``max_extrapolation_years`` allows the last forward rate to run on, and ``extrapolated`` says where it does. One
curve forwards and discounts: the handbook values OIS at the MIBOR-OIS rates themselves (6.10.5; C16).

**Valuation** (``ois_npv``): fixed leg ``N K/100 sum_k tau_k P(E_k)``; floating leg ``N (P(S_k) - P(E_k))`` for a
period to come and ``N (A_realised(S_k, t) - P(E_k))`` for the running one, the handbook's unwind rule (6.10.5:
the floating leg with its notional is worth the notional compounded to date). Fixings before the valuation date are
realised; the valuation date's own fixing sits in the curve's first node. ``dv01`` is the value lost when the
curve's continuously compounded zero rates rise by 1 bp, ``dv01_par`` when every par quote does and the curve is
rebuilt; both are positive for a receiver of fixed, as a long bond's DV01 is.

**Limits.** No real OIS quote or MIBOR fixing has been used. FBIL's terms ask for a paid subscription to store or
back-test its rates, and clause 5.1 of its Terms of Use (13 Oct 2026) for a Derived Product License for any
valuation model that uses FBIL data, which a curve bootstrapped from its quotes is on the clause's words
(``docs/rates_data_sources.md``, FBIL): everything is tested on synthetic quotes. The strict calendar covers 2015 to
2026: later dates need ``calendar.lenient()``, and the curve then says ``calendar_projected``. One curve, no basis
(MIBOR against TREPS or repo, term funding). The rules marked UNVERIFIED, the rate rounding and FBIL's quote basis
are open questions (``docs/analytics.md``).
"""

from __future__ import annotations

import calendar as _cal
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from cr_strat.calendars import CALENDARS, BusinessCalendar, calendar_for
from cr_strat.conventions import ROLLS, as_date, as_int, round_half_up

INDEXES = ("FBIL-MIBOR", "FBIL-SORR")
MISSING_RULES = ("raise", "holiday", "previous")
ZERO_BASES = ("continuous", "semiannual", "annual", "simple")
QUOTE_KINDS = ("single_period", "semiannual")
QUOTE_COLUMNS = ("tenor", "months", "rate_pct", "quote_kind", "source")
NODE_COLUMNS = ("label", "months", "end_date", "t_years", "discount", "zero_cc_pct", "zero_annual_pct",
                "quote_pct", "model_pct", "error_bp")
CURVE_COLUMNS = ("tenor", "months", "end_date", "t_years", "discount", "zero_cc_pct", "zero_annual_pct",
                 "zero_simple_pct", "forward_simple_pct", "par_pct", "extrapolated")
REPRICE_TOL_PCT = 1e-8  # a bootstrapped quote must reprice to within this (percent)
_PERIOD_MONTHS = (1, 2, 3, 4, 6, 12)
_YEAR = 365.0


def _num(value, name: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number, not {value!r}") from None
    if not math.isfinite(out):
        raise ValueError(f"{name} must be a finite number, not {value!r}")
    return out


def _blank(value) -> bool:
    """True for None, NaN, NaT and empty text."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def as_time(value, what: str = "time") -> datetime:
    """``value`` as a naive datetime in India time, as the stores write ``public_from``.

    A datetime or pandas Timestamp, a date (its midnight) or ISO 8601 text (``2026-11-17T10:00``); day-first or
    month-first text, blanks and times with a zone are refused.
    """
    if isinstance(value, str):
        try:
            stamp = datetime.fromisoformat(value.strip())
        except ValueError:
            raise ValueError(f"{what} {value!r} is not an ISO time like 2026-11-17T10:00") from None
    elif _blank(value):
        raise ValueError(f"{what} is blank")
    elif isinstance(value, pd.Timestamp):
        stamp = value.to_pydatetime()
    elif isinstance(value, datetime):
        stamp = value
    elif isinstance(value, date):
        stamp = datetime(value.year, value.month, value.day)
    elif isinstance(value, np.datetime64):
        stamp = pd.Timestamp(value).to_pydatetime()
    else:
        raise TypeError(f"{what} must be a datetime, a date or ISO text, not {type(value).__name__}")
    if stamp.tzinfo is not None:
        raise ValueError(f"{what} {value!r} carries a time zone; give India time without one, as the stores write it")
    return stamp


def _choice(value, allowed, name: str) -> None:
    if value not in allowed:
        raise ValueError(f"{name} must be one of {allowed}, not {value!r}")


# ---------------------------------------------------------------- conventions


@dataclass(frozen=True, kw_only=True)
class OisConventions:
    """Conventions of an overnight indexed swap; each rule's source is in the module docstring.

    ``index``: the overnight rate compounded (``INDEXES``). ``day_count`` and ``year_days``: Act/365 fixed, both
    legs. ``effective_lag``: business days from the trade date to the effective date. ``roll``: how a date off a
    business day moves (``conventions.ROLLS``); ``adjust_accrual``: whether periods accrue between adjusted dates
    (else between the unadjusted ones). ``single_period_max_months``: the longest tenor paid in one exchange;
    ``period_months``: the period length beyond it. ``schedule_from`` and ``eom``: where periods are counted from
    and whether an end-of-month rule applies (only ``"effective"`` and ``False`` are built; both UNVERIFIED).
    ``payment_lag``: business days from a period's end to its payment; ``fixing_lag``: business days between a
    fixing and the day it accrues for; ``rate_cutoff``: business days at a period's end that take an earlier
    fixing. Only 0 is built for each. ``calendar_name``: the business-day calendar (``calendars.CALENDARS``);
    ``payment_calendars``: those payments must fall on. ``rate_decimals``, ``amount_decimals`` and
    ``net_decimals``: rounding of the compounded rate (percent), of each leg's amount and of the net settlement in
    ``settlement_amounts``; ``None`` leaves a value unrounded. Variants: ``dataclasses.replace(MIBOR_OIS, ...)``.
    """

    name: str
    index: str
    day_count: str
    year_days: int
    effective_lag: int
    roll: str
    adjust_accrual: bool
    single_period_max_months: int
    period_months: int
    schedule_from: str
    eom: bool
    payment_lag: int
    fixing_lag: int
    rate_cutoff: int
    calendar_name: str
    payment_calendars: tuple[str, ...]
    rate_decimals: int | None
    amount_decimals: int | None
    net_decimals: int | None

    def __post_init__(self) -> None:
        _choice(self.index, INDEXES, "index")
        _choice(self.day_count, ("ACT/365F",), "day_count")
        _choice(self.roll, ROLLS, "roll")
        _choice(self.schedule_from, ("effective",), "schedule_from")
        for name in ("year_days", "single_period_max_months"):
            object.__setattr__(self, name, as_int(getattr(self, name), name, minimum=1))
        for name in ("effective_lag", "payment_lag", "fixing_lag", "rate_cutoff"):
            object.__setattr__(self, name, as_int(getattr(self, name), name, minimum=0))
        period = as_int(self.period_months, "period_months", minimum=1)
        _choice(period, _PERIOD_MONTHS, "period_months")
        object.__setattr__(self, "period_months", period)
        for name in ("adjust_accrual", "eom"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be True or False, not {getattr(self, name)!r}")
        if self.eom:
            raise NotImplementedError("an end-of-month rule is not built (none was found for INR OIS); eom must be "
                                      "False")
        if self.fixing_lag:
            raise NotImplementedError(f"a fixing lag of {self.fixing_lag} business days is not built; MIBOR-OIS has "
                                      "none (each day's own fixing accrues for that day)")
        calendars = tuple(str(c) for c in self.payment_calendars)
        if not calendars or not self.calendar_name:
            raise ValueError("calendar_name and payment_calendars must name calendars")
        object.__setattr__(self, "payment_calendars", calendars)
        for name in ("rate_decimals", "amount_decimals", "net_decimals"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, as_int(value, name, minimum=0))


MIBOR_OIS = OisConventions(
    name="MIBOR-OIS",
    index="FBIL-MIBOR",  # Handbook 6.10.2(k)(1): FBIL-MIBOR-OIS-COMPOUND, daily compounding of overnight MIBOR
    day_count="ACT/365F",  # Handbook 6.10.2(c), (i): Actual/365 fixed on both legs
    year_days=365,
    effective_lag=1,  # Handbook 6.10.2(e): the first Mumbai business day after the trade date, never a Saturday
    roll="modified_following",  # Handbook 6.10.2(f): Modified Following for all rupee swaps
    adjust_accrual=True,  # PARTLY VERIFIED: stated for the offshore contract only (FIMCIR/2026-27/16)
    single_period_max_months=12,  # Handbook swap terminology: one exchange up to a year (6.16 item 9 likewise)
    period_months=6,  # Handbook swap terminology: semi-annual on both legs beyond a year
    schedule_from="effective",  # UNVERIFIED: periods counted forward from the effective date, no stub
    eom=False,  # UNVERIFIED: no end-of-month rule found
    payment_lag=0,  # PARTLY VERIFIED: confirmations reset on a period's last day; the offshore draft has no delay
    fixing_lag=0,  # FIMCIR/2026-27/16 "Reset Fixing Lag 0"; handbook 6.10.2(k)(1): the day's own fixing
    rate_cutoff=0,  # Onshore none: MIBOR is published the same day, payment is the next business day
    calendar_name="mumbai_settlement",  # Handbook 6.10.2(g), (h): Mumbai business days, Saturdays excluded
    payment_calendars=("mumbai_settlement",),
    rate_decimals=4,  # Handbook 6.10.2(k)(1)(d): the rate to 0.0001%; its own example matches the unrounded rate
    amount_decimals=2,  # Handbook 6.10.2(k)(1)(e): amounts to the paisa
    net_decimals=0,  # Handbook 6.10.2(k)(1)(e): net settlement to the nearest rupee
)

# Handbook 6.16: SORR-OIS on the lines of MIBOR-OIS (FBIL's Secured Overnight Rupee Rate, published from 7 Jul 2025).
SORR_OIS = replace(MIBOR_OIS, name="SORR-OIS", index="FBIL-SORR")

# FIMMDA's draft conventions for foreign-currency-settled MIBOR OIS (FIMCIR/2026-27/16, 8 Jul 2026; comments were
# due 29 Jul 2026, no final version seen): a rate cut-off of one business day and payments on the Mumbai and New
# York calendars. Neither is built, so schedules and cash flows of this contract are refused.
FCS_MIBOR_OIS = replace(MIBOR_OIS, name="FCS-OIS (FIMMDA draft, Jul 2026)", rate_cutoff=1,
                        payment_calendars=("mumbai_settlement", "new_york"))


def _buildable(conventions: OisConventions) -> OisConventions:
    """``conventions`` if its schedules and cash flows can be built here, else a ``ValueError`` saying why not."""
    if not isinstance(conventions, OisConventions):
        raise TypeError(f"conventions must be OisConventions (MIBOR_OIS, SORR_OIS or a variant), not "
                        f"{type(conventions).__name__}")
    unknown = [c for c in (conventions.calendar_name, *conventions.payment_calendars) if c not in CALENDARS]
    if unknown:
        raise ValueError(f"{conventions.name}: no calendar named {unknown[0]!r} is built (known: {sorted(CALENDARS)}),"
                         " so its schedules and cash flows are refused")
    if conventions.rate_cutoff:
        raise ValueError(f"{conventions.name}: a rate cut-off of {conventions.rate_cutoff} business day(s) is not "
                         "built")
    if conventions.payment_lag:
        raise ValueError(f"{conventions.name}: a payment lag of {conventions.payment_lag} business day(s) is not "
                         "built")
    return conventions


def _calendar(calendar: BusinessCalendar | None, conventions: OisConventions) -> BusinessCalendar:
    if calendar is None:
        return calendar_for(conventions.calendar_name)
    if not isinstance(calendar, BusinessCalendar):
        raise TypeError(f"calendar must be a BusinessCalendar, not {type(calendar).__name__}")
    return calendar


def beyond_calendar(calendar: BusinessCalendar, dates: Sequence[date]) -> bool:
    """True if any of ``dates`` falls in a year ``calendar``'s holiday list does not cover (weekdays assumed open)."""
    return any(not calendar.covers(d) for d in dates)


# ---------------------------------------------------------------- MIBOR's history


MIBOR_HISTORY = (
    {"from": None, "to": date(2015, 7, 21), "regime": "polled", "window": "", "published_by": "",
     "note": "FIMMDA-NSE MIBID/MIBOR from a poll of banks (partly verified: secondary sources)"},
    {"from": date(2015, 7, 22), "to": date(2025, 8, 3), "regime": "traded 09:00-10:00", "window": "09:00-10:00",
     "published_by": "10:45",
     "note": "FBIL overnight MIBOR: volume-weighted average of NDS-Call overnight trades; FBIL revised the method "
             "on 16 Aug 2017 (content not read)"},
    {"from": date(2025, 8, 4), "to": None, "regime": "traded 09:00-12:00", "window": "09:00-12:00",
     "published_by": "12:45",
     "note": "published by 12:45 IST from 4 Aug 2025 (FBIL press release of 18 Jul 2025, title read); the three-hour "
             "window is from news reports (partly verified)"},
)
MIBOR_BREAKS = (date(2015, 7, 22), date(2017, 8, 16), date(2025, 8, 4))  # do not splice a series across these


def fixing_regime(day) -> dict[str, Any]:
    """How overnight MIBOR was set on ``day``: ``regime``, trading ``window`` and ``published_by`` (IST), a ``note``.

    A history with a break inside it (``MIBOR_BREAKS``) should be marked, not spliced
    (``docs/rates_data_sources.md``, Pitfalls). The fixing window decides how a policy decision announced at about
    10:00 enters the day's fixing (``policy_path``).
    """
    d = as_date(day)
    for row in MIBOR_HISTORY:
        if (row["from"] is None or d >= row["from"]) and (row["to"] is None or d <= row["to"]):
            return dict(row)
    raise AssertionError("MIBOR_HISTORY leaves a gap")  # pragma: no cover


# ---------------------------------------------------------------- tenors and schedules


_TENOR = re.compile(r"(\d+)\s*(m|mo|mos|months?|y|yrs?|years?)")


def tenor_months(name) -> int:
    """Months in a tenor name: ``1M``, ``9M``, ``12M``, ``1Y``, ``5Y``, FBIL's ``1 Month``, ``12 Months``, ``5 Years``.

    Days and weeks are refused: MIBOR-OIS tenors are whole months or years.
    """
    text = " ".join(str(name).split()).lower()
    m = _TENOR.fullmatch(text)
    if not m:
        raise ValueError(f"tenor {name!r} is not understood: give months or years, such as 1M, 9M, 2Y, '12 Months' "
                         "or '5 Years'")
    count = int(m.group(1))
    if count <= 0:
        raise ValueError(f"tenor {name!r} must be at least one month")
    return count if m.group(2).startswith("m") else 12 * count


def tenor_label(months: int) -> str:
    """``months`` as a tenor name: ``1M`` to ``11M``, then ``1Y``, ``2Y``, ... where whole years, else ``18M``."""
    months = as_int(months, "months", minimum=1)
    return f"{months // 12}Y" if months % 12 == 0 else f"{months}M"


def _months(tenor) -> int:
    if isinstance(tenor, str):
        return tenor_months(tenor)
    return as_int(tenor, "months", minimum=1)


def add_months(day, months: int) -> date:
    """``day`` moved by ``months`` on the same day of the month, clipped to the month's end (no end-of-month rule)."""
    d = as_date(day)
    total = d.year * 12 + d.month - 1 + as_int(months, "months")
    year, month0 = divmod(total, 12)
    return date(year, month0 + 1, min(d.day, _cal.monthrange(year, month0 + 1)[1]))


def effective_date(trade_date, conventions: OisConventions = MIBOR_OIS, calendar: BusinessCalendar | None = None
                   ) -> date:
    """The effective date of a spot-starting swap traded on ``trade_date``: T+``effective_lag`` business days."""
    if not isinstance(conventions, OisConventions):
        raise TypeError(f"conventions must be OisConventions, not {type(conventions).__name__}")
    cal = _calendar(calendar, conventions)
    return cal.add_business_days(as_date(trade_date), conventions.effective_lag)


@dataclass(frozen=True)
class Period:
    """One calculation period: accrual from ``start`` to ``end`` (``days``, ``year_fraction`` = days / 365), paid on
    ``payment``; ``unadjusted_end`` is the roll date before the business-day adjustment."""

    start: date
    end: date
    payment: date
    days: int
    year_fraction: float
    unadjusted_end: date


def ois_schedule(effective, months, conventions: OisConventions = MIBOR_OIS,
                 calendar: BusinessCalendar | None = None) -> tuple[Period, ...]:
    """Calculation periods of a swap starting on ``effective`` (a business day) and running ``months`` (or a tenor).

    One period up to ``single_period_max_months``; beyond, ``period_months`` periods (UNVERIFIED rule: counted
    forward from the effective date on its day of the month, each end adjusted, no stub). A tenor that is not a
    whole number of periods is refused. A strict calendar refuses dates outside its years
    (``CalendarCoverageError``, naming the year): pass ``calendar.lenient()`` to project weekends-only.
    """
    conv = _buildable(conventions)
    cal = _calendar(calendar, conv)
    s0 = as_date(effective)
    months = _months(months)
    if not cal.is_business_day(s0):
        raise ValueError(f"effective date {s0} is not a business day on calendar {cal.name!r}; a spot swap starts on "
                         "effective_date(trade_date)")
    if months <= conv.single_period_max_months:
        step = months
    elif months % conv.period_months:
        raise ValueError(f"a {tenor_label(months)} swap is not a whole number of {conv.period_months}-month periods; "
                         "no stub rule is known for INR OIS (UNVERIFIED), so none is built")
    else:
        step = conv.period_months
    rolls = [add_months(s0, k) for k in range(step, months + 1, step)]
    ends = [cal.adjust(d, conv.roll) for d in rolls]
    out, start, unadjusted_start = [], s0, s0
    for roll, end in zip(rolls, ends):
        a, b = (start, end) if conv.adjust_accrual else (unadjusted_start, roll)
        if not b > a:
            raise ValueError(f"the period ending {end} does not end after it starts ({a})")
        days = (b - a).days
        out.append(Period(a, b, end, days, days / conv.year_days, roll))
        start, unadjusted_start = end, roll
    return tuple(out)


# ---------------------------------------------------------------- compounding


def fixing_map(fixings) -> dict[date, float]:
    """Fixings (a mapping of dates to percent, or a Series indexed by date) as ``{date: percent}``; NaN values are
    missing, two different values for one date are refused, naming it."""
    if fixings is None:
        return {}
    if isinstance(fixings, pd.Series):
        items = list(fixings.items())
    elif isinstance(fixings, Mapping):
        items = list(fixings.items())
    else:
        raise TypeError(f"fixings must be a mapping of dates to rates in percent, or a Series indexed by date, not "
                        f"{type(fixings).__name__}")
    out: dict[date, float] = {}
    for key, value in items:
        d = as_date(key)
        try:
            rate = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"the fixing for {d} is not a number: {value!r}") from None
        if not math.isfinite(rate):
            continue
        if d in out and out[d] != rate:
            raise ValueError(f"two different fixings for {d}: {out[d]} and {rate}")
        out[d] = rate
    return out


@dataclass(frozen=True)
class Compounded:
    """The overnight rate compounded from ``start`` to ``end`` (``compound_overnight``).

    ``factor`` is ``A(start, end)``; ``rate_pct`` the floating rate ``(A - 1) 36500 / days``, unrounded, and
    ``rate_rounded_pct`` rounded as the conventions say (``None`` if they do not round). ``business_days`` counts
    the fixings' days; ``missing`` lists business days without a fixing (handled by ``rule``) and ``ignored`` the
    given fixings on days that are not business days (a money-market Saturday, say), which never count.
    """

    start: date
    end: date
    factor: float
    rate_pct: float
    rate_rounded_pct: float | None
    days: int
    business_days: int
    missing: tuple[date, ...]
    ignored: tuple[date, ...]
    rule: str


def compound_overnight(fixings, start, end, *, calendar: BusinessCalendar | None = None, missing: str = "raise",
                       conventions: OisConventions = MIBOR_OIS) -> Compounded:
    """``A(start, end) = prod_i (1 + R_i n_i / 36500)`` over the business days ``i`` in ``[start, end)``.

    ``fixings`` maps dates to rates in percent. ``n_i`` runs from ``i`` to the next business day, or to ``end``, so
    a Friday's fixing covers the weekend and the last one is cut at ``end``. ``start`` must be a business day.
    ``missing``: ``"raise"`` (default) names the business days without a fixing; ``"holiday"`` treats such a day
    as a holiday, the earlier fixing accruing across it (handbook 7.4: an unscheduled holiday known the day before,
    with no MIBOR published); and ``"previous"`` gives it the latest earlier fixing (handbook 7.4: an unscheduled
    holiday declared on the day, before MIBOR was published). 7.4 is written for trades not settled through CCIL;
    FBIL not publishing at all falls to the ISDA fallbacks (6.10.2(k)(1)), not built. Fixings on other days are
    ignored and listed.
    """
    conv = _buildable(conventions)
    _choice(missing, MISSING_RULES, "missing")
    cal = _calendar(calendar, conv)
    s, e = as_date(start), as_date(end)
    if not e > s:
        raise ValueError(f"end {e} must be after start {s}")
    if not cal.is_business_day(s):
        raise ValueError(f"start {s} is not a business day on calendar {cal.name!r}; compounding starts on one")
    fix = fixing_map(fixings)
    days = cal.business_days(s, e - timedelta(days=1))
    known = sorted(d for d in fix if d < s)
    segments: list[list[float]] = []  # [rate, days]
    absent = []
    for k, day in enumerate(days):
        n = ((days[k + 1] if k + 1 < len(days) else e) - day).days
        rate = fix.get(day)
        if rate is None:
            absent.append(day)
            if missing == "holiday":
                if not segments:
                    raise ValueError(f"no fixing for {day}, the first business day from {s}: it cannot be treated as a "
                                     "holiday, as no earlier fixing of the period accrues across it")
                segments[-1][1] += n
                continue
            if missing == "previous":
                earlier = [d for d in days[:k] if d in fix] or known
                if not earlier:
                    raise ValueError(f"no fixing for {day} and none before it to carry forward")
                rate = fix[earlier[-1]]
            else:
                continue
        segments.append([rate, n])
    if missing == "raise" and absent:
        shown = ", ".join(d.isoformat() for d in absent[:10]) + (" ..." if len(absent) > 10 else "")
        raise ValueError(f"no fixing for {len(absent)} business day(s) from {s} to {e}: {shown}")
    factor = 1.0
    for rate, n in segments:
        g = 1.0 + rate * n / (100 * conv.year_days)
        if not g > 0:
            raise ValueError(f"a fixing of {rate}% over {n} days leaves no positive growth factor")
        factor *= g
    total = (e - s).days
    rate_pct = (factor - 1.0) * 100 * conv.year_days / total
    rounded = None if conv.rate_decimals is None else round_half_up(rate_pct, conv.rate_decimals)
    ignored = tuple(sorted(d for d in fix if s <= d < e and not cal.is_business_day(d)))
    return Compounded(s, e, factor, rate_pct, rounded, total, len(days), tuple(absent), ignored, missing)


def settlement_amounts(notional, fixed_rate_pct, compounded: Compounded, *,
                       conventions: OisConventions = MIBOR_OIS) -> dict[str, Any]:
    """The cash flows of one period as a settlement statement shows them (handbook 6.10.2(k)(1)(d)-(e)).

    Fixed ``N K d / 36500`` and floating ``N r d / 36500``, ``r`` the compounded rate rounded to ``rate_decimals``
    (unrounded if ``None``), each to ``amount_decimals``; ``net_to_fixed_payer`` = floating - fixed, the settlement
    rounded to ``net_decimals``, and ``payer`` says who pays it. Valuation never rounds.
    """
    if not isinstance(compounded, Compounded):
        raise TypeError("compounded must come from compound_overnight")
    conv = conventions
    n = _num(notional, "notional")
    k = _num(fixed_rate_pct, "fixed_rate_pct")
    rate = compounded.rate_pct if conv.rate_decimals is None else round_half_up(compounded.rate_pct,
                                                                                 conv.rate_decimals)
    d = compounded.days

    def rounded(value: float, decimals: int | None) -> float:
        return value if decimals is None else round_half_up(value, decimals)

    fixed = rounded(n * k * d / (100 * conv.year_days), conv.amount_decimals)
    floating = rounded(n * rate * d / (100 * conv.year_days), conv.amount_decimals)
    net = floating - fixed
    settled = rounded(abs(net), conv.net_decimals)
    payer = "none" if settled == 0 else ("fixed receiver" if net > 0 else "fixed payer")
    return {"days": d, "fixed_rate_pct": k, "floating_rate_pct": rate, "fixed_amount": fixed,
            "floating_amount": floating, "net_to_fixed_payer": net, "net_settlement": settled, "payer": payer}


# ---------------------------------------------------------------- quotes


def ois_quotes(quotes, *, trade_date=None, conventions: OisConventions = MIBOR_OIS) -> pd.DataFrame:
    """Par quotes as ``QUOTE_COLUMNS``, sorted by tenor: one date, each tenor once, each rate a number.

    ``quotes``: rows as ``fbil.load_fbil_money_market`` gives them (``tenor_name``, ``rate_quoted_pct``,
    ``quote_convention``, ``date``), or ``tenor`` and ``rate_pct`` (optional ``date``, ``source``), or a mapping
    of tenor to rate. ``quote_kind`` is ``single_period`` up to ``single_period_max_months`` and ``semiannual``
    beyond; FBIL's ``quote_convention`` must agree (``money_market`` with the first). A frame with only a
    converted ``yield_pct`` is refused: the bootstrap needs the quoted par rate.
    """
    if isinstance(quotes, Mapping):
        frame = pd.DataFrame({"tenor": [str(k) for k in quotes], "rate_pct": list(quotes.values())})
    elif isinstance(quotes, pd.DataFrame):
        frame = quotes.copy()
        frame.columns = [str(c).strip().lower() for c in frame.columns]
    else:
        raise TypeError(f"quotes must be a DataFrame or a mapping of tenor to rate, not {type(quotes).__name__}")
    if "rate_quoted_pct" in frame:
        rate_col = "rate_quoted_pct"
    elif "rate_pct" in frame:
        rate_col = "rate_pct"
    elif "yield_pct" in frame:
        raise ValueError("the quotes have yield_pct but no quoted rate: bootstrap from the par rate as quoted "
                         "(rate_quoted_pct, as fbil.load_fbil_money_market keeps it, or rate_pct), never from a yield "
                         "converted to another basis")
    else:
        raise ValueError("the quotes need a rate column (rate_pct or rate_quoted_pct); they have "
                         f"{list(frame.columns)}")
    tenor_col = "tenor" if "tenor" in frame else ("tenor_name" if "tenor_name" in frame else None)
    if tenor_col is None:
        raise ValueError(f"the quotes need a tenor column (tenor or tenor_name); they have {list(frame.columns)}")
    if frame.empty:
        raise ValueError("no quotes given")
    if "date" in frame:
        found = {as_date(v) for v in frame["date"] if not _blank(v)}
        if len(found) > 1:
            raise ValueError(f"quotes of more than one date: {sorted(d.isoformat() for d in found)}; give one day's")
        if trade_date is not None and found and found != {as_date(trade_date)}:
            raise ValueError(f"the quotes are dated {found.pop()}, not the trade date {as_date(trade_date)}")
    rows, seen = [], {}
    for rec in frame.to_dict("records"):
        months = tenor_months(rec[tenor_col])
        label = tenor_label(months)
        rate = rec[rate_col]
        try:
            rate = float(rate)
        except (TypeError, ValueError):
            raise ValueError(f"the {label} quote is not a number: {rate!r}") from None
        if not math.isfinite(rate):
            raise ValueError(f"the {label} quote is missing")
        kind = "single_period" if months <= conventions.single_period_max_months else "semiannual"
        if "quote_convention" in rec and not _blank(rec["quote_convention"]):
            expected = "money_market" if kind == "single_period" else "semiannual"
            if str(rec["quote_convention"]).strip() != expected:
                raise ValueError(f"the {label} quote is marked {rec['quote_convention']!r}, but {conventions.name} "
                                 f"pays {kind.replace('_', ' ')} at that tenor ({expected!r})")
        if months in seen:
            raise ValueError(f"the {label} tenor is quoted twice ({seen[months]} and {rate})")
        seen[months] = rate
        source = rec.get("source", rec.get("source_file", ""))
        rows.append({"tenor": label, "months": months, "rate_pct": rate, "quote_kind": kind,
                     "source": "" if _blank(source) else str(source)})
    return pd.DataFrame(rows, columns=list(QUOTE_COLUMNS)).sort_values("months").reset_index(drop=True)


# ---------------------------------------------------------------- the curve


def _times(t) -> tuple[np.ndarray, tuple[int, ...]]:
    try:
        arr = np.asarray(t, dtype=float)
    except (TypeError, ValueError):
        raise TypeError(f"times must be numbers of years, not {t!r}") from None
    flat = arr.reshape(-1)
    if flat.size:
        if not np.all(np.isfinite(flat)):
            raise ValueError(f"times must be finite numbers of years, not {flat[~np.isfinite(flat)][0]}")
        if flat.min() < 0:
            raise ValueError(f"times must be zero or more years after the valuation date, not {flat.min()}")
    return flat, arr.shape


def _from_continuous(z: np.ndarray, t: np.ndarray, basis: str) -> np.ndarray:
    """Decimal continuous zero rates to percent on ``basis`` (``simple``: Act/365 money-market rate to ``t``)."""
    if basis == "continuous":
        return z * 100
    if basis == "semiannual":
        return 2 * np.expm1(z / 2) * 100
    if basis == "annual":
        return np.expm1(z) * 100
    out = z.copy()
    pos = t > 0
    out[pos] = np.expm1(z[pos] * t[pos]) / t[pos]
    return out * 100


def _date_list(when) -> tuple[list[date], bool]:
    scalar = isinstance(when, (str, date, np.datetime64)) or isinstance(when, pd.Timestamp)
    return ([as_date(when)] if scalar else [as_date(w) for w in when]), scalar


@dataclass(frozen=True, kw_only=True)
class OisCurve:
    """Discount factors from ``valuation_date`` (the trade date), linear in ``ln P`` between nodes.

    ``times`` (Act/365F years, the first 0) and ``log_dfs`` (the first 0) are the nodes; ``node_dates`` and
    ``labels`` name them (``ON`` for the effective date, then each tenor). ``t_max`` is the last node: beyond it
    every rate is NaN unless ``max_extrapolation_years`` lets the last forward rate run on. ``calendar_projected``:
    some schedule date fell in a year the calendar's holiday list does not cover. ``quotes``, ``overnight_fixing_pct``,
    ``conventions``, ``calendar`` and ``effective`` record what the curve was bootstrapped from (``bumped`` rebuilds
    it). Satisfies ``curvefit``'s ``DiscountCurveLike`` by structure.
    """

    valuation_date: date
    times: tuple[float, ...]
    log_dfs: tuple[float, ...]
    node_dates: tuple[date, ...] = ()
    labels: tuple[str, ...] = ()
    max_extrapolation_years: float = 0.0
    calendar_projected: bool = False
    source: str = ""
    conventions: OisConventions = MIBOR_OIS
    calendar: BusinessCalendar | None = None
    quotes: tuple[tuple[str, int, float], ...] = ()
    overnight_fixing_pct: float | None = None
    effective: date | None = None
    t_max: float = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "valuation_date", as_date(self.valuation_date))
        times = tuple(float(v) for v in self.times)
        logs = tuple(float(v) for v in self.log_dfs)
        if len(times) < 2 or len(times) != len(logs):
            raise ValueError(f"a curve needs at least two nodes with one log discount factor each, not {len(times)} "
                             f"times and {len(logs)} values")
        if times[0] != 0.0 or logs[0] != 0.0:
            raise ValueError("the first node must be the valuation date: time 0, log discount factor 0")
        if not all(math.isfinite(v) for v in times + logs):
            raise ValueError("node times and log discount factors must be finite")
        if any(b <= a for a, b in zip(times, times[1:])):
            raise ValueError("node times must increase")
        object.__setattr__(self, "times", times)
        object.__setattr__(self, "log_dfs", logs)
        if self.node_dates:
            dates = tuple(as_date(d) for d in self.node_dates)
            if len(dates) != len(times) or dates[0] != self.valuation_date:
                raise ValueError("node_dates must hold one date per node, starting at the valuation date")
            if any(abs((d - self.valuation_date).days / _YEAR - t) > 1e-12 for d, t in zip(dates, times)):
                raise ValueError("node_dates and times disagree")
            object.__setattr__(self, "node_dates", dates)
        labels = tuple(str(v) for v in self.labels) if self.labels else ("",) * len(times)
        if len(labels) != len(times):
            raise ValueError("labels must hold one name per node")
        object.__setattr__(self, "labels", labels)
        extra = _num(self.max_extrapolation_years, "max_extrapolation_years")
        if extra < 0:
            raise ValueError(f"max_extrapolation_years must be zero or more, not {extra}")
        object.__setattr__(self, "max_extrapolation_years", extra)
        if self.effective is not None:
            object.__setattr__(self, "effective", as_date(self.effective))
        object.__setattr__(self, "quotes", tuple((str(a), int(b), float(c)) for a, b, c in self.quotes))
        object.__setattr__(self, "t_max", times[-1])

    # -- evaluation

    def _log_df(self, flat: np.ndarray) -> np.ndarray:
        times, logs = np.asarray(self.times), np.asarray(self.log_dfs)
        out = np.interp(flat, times, logs)
        beyond = flat > times[-1]
        if beyond.any():
            slope = (logs[-1] - logs[-2]) / (times[-1] - times[-2])
            ext = logs[-1] + slope * (flat[beyond] - times[-1])
            ext[flat[beyond] > times[-1] + self.max_extrapolation_years + 1e-12] = np.nan
            out[beyond] = ext
        return out

    def _segment_forward(self, flat: np.ndarray) -> np.ndarray:
        """Continuously compounded forward rate (decimal) of the node segment holding each time."""
        times, logs = np.asarray(self.times), np.asarray(self.log_dfs)
        slopes = -np.diff(logs) / np.diff(times)
        idx = np.clip(np.searchsorted(times, flat, side="right") - 1, 0, len(slopes) - 1)
        out = slopes[idx]
        out[flat > times[-1] + self.max_extrapolation_years + 1e-12] = np.nan
        return out

    def year_fraction(self, day) -> float:
        """Act/365F years from the valuation date to ``day``."""
        return (as_date(day) - self.valuation_date).days / _YEAR

    def discount_t(self, t) -> np.ndarray:
        """Discount factor ``t`` years (Act/365F) after the valuation date; NaN beyond the allowed range."""
        flat, shape = _times(t)
        return np.exp(self._log_df(flat)).reshape(shape)

    def discount(self, when) -> np.ndarray:
        """Discount factor on a date (or each of a sequence of dates); NaN beyond the allowed range."""
        dates, scalar = _date_list(when)
        for d in dates:
            if d < self.valuation_date:
                raise ValueError(f"{d} is before the curve's valuation date {self.valuation_date}")
        out = self.discount_t(np.array([(d - self.valuation_date).days for d in dates], dtype=float) / _YEAR)
        return out.reshape(()) if scalar else out

    def zero_rate(self, t, basis: str = "continuous") -> np.ndarray:
        """Zero rate (percent) at ``t`` years on ``basis`` (``ZERO_BASES``); at ``t = 0`` the first forward rate."""
        _choice(basis, ZERO_BASES, "basis")
        flat, shape = _times(t)
        log = self._log_df(flat)
        z = np.empty_like(flat)
        pos = flat > 0
        z[pos] = -log[pos] / flat[pos]
        if (~pos).any():
            z[~pos] = self._segment_forward(flat[~pos])
        return _from_continuous(z, flat, basis).reshape(shape)

    def forward_rate(self, t) -> np.ndarray:
        """Instantaneous forward rate (percent, continuous) at ``t`` years: constant between nodes."""
        flat, shape = _times(t)
        return (self._segment_forward(flat) * 100).reshape(shape)

    def forward(self, start, end) -> float:
        """Simple Act/365 forward rate (percent) from ``start`` to ``end``: ``(P(a)/P(b) - 1) 36500 / days``."""
        a, b = as_date(start), as_date(end)
        if not b > a:
            raise ValueError(f"end {b} must be after start {a}")
        pa, pb = self.discount([a, b])
        return float((pa / pb - 1) * 100 * _YEAR / (b - a).days)

    def extrapolated(self, t) -> np.ndarray:
        """True where ``t`` lies beyond the last node (``t_max``)."""
        flat, shape = _times(t)
        return (flat > self.t_max).reshape(shape)

    def overnight_forwards(self, start, end, calendar: BusinessCalendar | None = None) -> dict[date, float]:
        """The curve's simple overnight forward rate (percent) for each business day in ``[start, end)``.

        Each runs to the next business day (the last to ``end``), so ``compound_overnight`` of these fixings
        gives back ``P(start) / P(end)``: projected fixings for a floating leg or a carry estimate.
        """
        cal = calendar if calendar is not None else (self.calendar or calendar_for(self.conventions.calendar_name))
        s, e = as_date(start), as_date(end)
        if not e > s:
            raise ValueError(f"end {e} must be after start {s}")
        days = cal.business_days(s, e - timedelta(days=1))
        if not days:
            return {}
        ends = days[1:] + [e]
        dfs = _discounts(self, [days[0], *ends], "overnight forwards")
        return {d: float((dfs[k] / dfs[k + 1] - 1) * 100 * _YEAR / (n - d).days)
                for k, (d, n) in enumerate(zip(days, ends))}

    # -- derived curves

    def shifted(self, bp: float) -> OisCurve:
        """The curve with every continuously compounded zero rate ``bp`` basis points higher (no quotes kept)."""
        shift = _num(bp, "bp") / 1e4
        return replace(self, log_dfs=tuple(v - shift * t for t, v in zip(self.times, self.log_dfs)), quotes=(),
                       source=f"{self.source} shifted {bp:+g} bp".strip())

    def bumped(self, bp: float = 1.0, tenors: Sequence[str] | None = None) -> OisCurve:
        """The curve rebuilt with the par quotes (all, or those of ``tenors``) ``bp`` basis points higher.

        The overnight fixing of the trade date is a published fixing, not a quote, and stays.
        """
        if not self.quotes or self.overnight_fixing_pct is None:
            raise ValueError("this curve was not bootstrapped from quotes here, so it cannot be rebuilt with them "
                             "bumped")
        shift = _num(bp, "bp") / 100
        wanted = None if tenors is None else {tenor_label(_months(t)) for t in tenors}
        if wanted is not None:
            unknown = wanted - {label for label, _, _ in self.quotes}
            if unknown:
                raise ValueError(f"no quote for {sorted(unknown)} in this curve")
        quotes = {label: rate + (shift if wanted is None or label in wanted else 0.0) for label, _, rate in self.quotes}
        return bootstrap_ois(quotes, self.valuation_date, self.overnight_fixing_pct, conventions=self.conventions,
                             calendar=self.calendar, max_extrapolation_years=self.max_extrapolation_years,
                             source=f"{self.source} bumped {bp:+g} bp".strip())

    def rebase(self, day) -> OisCurve:
        """The same forward rates seen from ``day`` (a later date on the curve): ``P'(x) = P(x) / P(day)``."""
        d = as_date(day)
        if d < self.valuation_date:
            raise ValueError(f"{d} is before the curve's valuation date {self.valuation_date}")
        if d == self.valuation_date:
            return self
        delta = (d - self.valuation_date).days / _YEAR
        if delta >= self.t_max:
            raise ValueError(f"{d} is on or beyond the curve's last node")
        base = float(self._log_df(np.array([delta]))[0])
        keep = [k for k, t in enumerate(self.times) if t > delta]
        times = (0.0, *((self.node_dates[k] - d).days / _YEAR if self.node_dates else self.times[k] - delta
                        for k in keep))
        logs = (0.0, *(self.log_dfs[k] - base for k in keep))
        dates = (d, *(self.node_dates[k] for k in keep)) if self.node_dates else ()
        return replace(self, valuation_date=d, times=times, log_dfs=logs, node_dates=dates,
                       labels=("", *(self.labels[k] for k in keep)), quotes=(), overnight_fixing_pct=None,
                       effective=None, source=f"{self.source} seen from {d}".strip())

    def nodes(self) -> pd.DataFrame:
        """One row per node (``NODE_COLUMNS``): its date, discount factor, zero rates and the quote it reprices."""
        quotes = {label: (months, rate) for label, months, rate in self.quotes}
        rows = []
        for k, (t, log) in enumerate(zip(self.times, self.log_dfs)):
            if k == 0:
                continue
            label = self.labels[k]
            months, rate = quotes.get(label, (None, math.nan))
            model = math.nan
            if months is not None and self.effective is not None:
                model = ois_par_rate(self, self.effective, months, self.conventions, self.calendar)
            rows.append({"label": label, "months": months, "end_date": self.node_dates[k] if self.node_dates else None,
                         "t_years": t, "discount": math.exp(log), "zero_cc_pct": -log / t * 100,
                         "zero_annual_pct": math.expm1(-log / t) * 100, "quote_pct": rate, "model_pct": model,
                         "error_bp": (model - rate) * 100})
        return pd.DataFrame(rows, columns=list(NODE_COLUMNS))


def _discounts(curve, dates: Sequence[date], what: str) -> np.ndarray:
    """Discount factors on ``dates`` from any ``DiscountCurveLike``, refusing dates before it or beyond its range."""
    for d in dates:
        if d < curve.valuation_date:
            raise ValueError(f"{what}: {d} is before the curve's valuation date {curve.valuation_date}")
    values = np.asarray(curve.discount(list(dates)), dtype=float).reshape(-1)
    bad = [d for d, v in zip(dates, values) if not (math.isfinite(v) and v > 0)]
    if bad:
        ends = getattr(curve, "node_dates", ())
        last = ends[-1] if ends else curve.valuation_date + timedelta(days=round(curve.t_max * _YEAR))
        allowed = getattr(curve, "max_extrapolation_years", 0.0)
        raise ValueError(f"{what}: {bad[0]} is beyond the curve, whose last node is {last} ({curve.t_max:.4f} years), "
                         f"with {allowed:g} years of extrapolation allowed; give longer quotes or allow extrapolation")
    return values


def _solve_node(times: list[float], logs: list[float], periods: list[tuple[float, float]], t_end: float, k: float,
                log_p0: float, label: str) -> float:
    """``ln P(t_end)`` that makes a semi-annual swap at ``k`` (decimal) worth par; dates past the last node
    are interpolated between it and the new node."""
    t_a, x_a = times[-1], logs[-1]
    fixed = 0.0
    moving: list[tuple[float, float]] = []
    for tau, t in periods:
        if t <= t_a:
            fixed += tau * math.exp(float(np.interp(t, times, logs)))
        else:
            moving.append((tau, (t - t_a) / (t_end - t_a)))
    p0 = math.exp(log_p0)

    def f(x: float) -> tuple[float, float]:
        value, slope = fixed, 0.0
        for tau, w in moving:
            p = math.exp(x_a + w * (x - x_a))
            value += tau * p
            slope += tau * w * p
        px = math.exp(x)
        return k * value - (p0 - px), k * slope + px

    x = x_a - k * (t_end - t_a)
    lo, hi = x - 0.5, x + 0.5
    for _ in range(60):
        if f(lo)[0] < 0:
            break
        lo -= 1.0
    else:
        raise ValueError(f"no discount factor reprices the {label} quote of {k * 100:g}%")
    for _ in range(60):
        if f(hi)[0] > 0:
            break
        hi += 1.0
    else:
        raise ValueError(f"no discount factor reprices the {label} quote of {k * 100:g}%")
    x = min(max(x, lo), hi)
    for _ in range(200):
        value, slope = f(x)
        if value == 0:
            return x
        if value > 0:
            hi = x
        else:
            lo = x
        step = x - value / slope if slope > 0 else math.nan
        nxt = step if lo < step < hi else 0.5 * (lo + hi)
        if abs(nxt - x) < 1e-15:
            return nxt
        x = nxt
    raise RuntimeError(f"the {label} node did not converge")  # pragma: no cover


def bootstrap_ois(quotes, trade_date, overnight_fixing_pct, *, conventions: OisConventions = MIBOR_OIS,
                  calendar: BusinessCalendar | None = None, max_extrapolation_years: float = 0.0,
                  source: str = "") -> OisCurve:
    """The discount curve of one day's OIS par quotes (``ois_quotes`` reads them) and that day's overnight fixing.

    ``trade_date`` must be a business day (FBIL publishes on no other). Quotes are taken shortest first; each
    reprices to within ``REPRICE_TOL_PCT`` or the bootstrap fails. A strict calendar refuses schedule dates in
    years it does not cover: pass ``calendar.lenient()``, and the curve says ``calendar_projected``.
    """
    conv = _buildable(conventions)
    cal = _calendar(calendar, conv)
    t0 = as_date(trade_date)
    if not cal.is_business_day(t0):
        raise ValueError(f"trade date {t0} is not a business day on calendar {cal.name!r}")
    table = ois_quotes(quotes, trade_date=t0, conventions=conv)
    r_on = _num(overnight_fixing_pct, "overnight_fixing_pct")
    s0 = effective_date(t0, conv, cal)
    growth = 1.0 + r_on * (s0 - t0).days / (100 * conv.year_days)
    if not growth > 0:
        raise ValueError(f"an overnight fixing of {r_on}% leaves no positive discount factor")
    times, logs = [0.0, (s0 - t0).days / _YEAR], [0.0, -math.log(growth)]
    dates, labels, used = [t0, s0], ["", "ON"], [t0, s0]
    schedules = []
    for row in table.itertuples(index=False):
        periods = ois_schedule(s0, row.months, conv, cal)
        end = periods[-1].end
        used.extend(p.end for p in periods)
        t_end = (end - t0).days / _YEAR
        if not t_end > times[-1]:
            raise ValueError(f"the {row.tenor} quote ends {end}, not after the previous node ({dates[-1]})")
        k = row.rate_pct / 100
        if len(periods) == 1:
            g = 1.0 + k * periods[0].year_fraction
            if not g > 0:
                raise ValueError(f"the {row.tenor} quote of {row.rate_pct}% leaves no positive discount factor")
            x = logs[1] - math.log(g)
        else:
            x = _solve_node(times, logs, [(p.year_fraction, (p.end - t0).days / _YEAR) for p in periods], t_end, k,
                            logs[1], row.tenor)
        times.append(t_end)
        logs.append(x)
        dates.append(end)
        labels.append(row.tenor)
        schedules.append((row.tenor, row.rate_pct, periods))
    curve = OisCurve(valuation_date=t0, times=tuple(times), log_dfs=tuple(logs), node_dates=tuple(dates),
                     labels=tuple(labels), max_extrapolation_years=max_extrapolation_years,
                     calendar_projected=beyond_calendar(cal, used), source=source, conventions=conv, calendar=cal,
                     quotes=tuple((r.tenor, r.months, r.rate_pct) for r in table.itertuples(index=False)),
                     overnight_fixing_pct=r_on, effective=s0)
    for tenor, rate, periods in schedules:
        model = _par_from_periods(curve, periods, "bootstrap check")
        if abs(model - rate) > REPRICE_TOL_PCT:
            raise RuntimeError(f"the {tenor} quote reprices to {model}%, not {rate}%")  # pragma: no cover
    return curve


# ---------------------------------------------------------------- valuation


def _par_from_periods(curve, periods: Sequence[Period], what: str) -> float:
    dfs = _discounts(curve, [periods[0].start, *(p.end for p in periods)], what)
    annuity = sum(p.year_fraction * v for p, v in zip(periods, dfs[1:]))
    return float((dfs[0] - dfs[-1]) / annuity * 100)


def ois_par_rate(curve, effective, months, conventions: OisConventions | None = None,
                 calendar: BusinessCalendar | None = None) -> float:
    """Par fixed rate (percent) of a swap from ``effective`` for ``months`` (or a tenor) on ``curve``.

    ``100 (P(S0) - P(E_n)) / sum_k tau_k P(E_k)``; for one period that is the simple rate
    ``(P(S0) / P(E) - 1) / tau``. Any ``DiscountCurveLike`` will do; ``conventions`` and ``calendar`` default to
    the curve's. A swap starting before the curve's valuation date is refused (value it with ``ois_npv``).
    """
    conv = conventions if conventions is not None else getattr(curve, "conventions", MIBOR_OIS)
    cal = calendar if calendar is not None else getattr(curve, "calendar", None)
    periods = ois_schedule(effective, months, conv, cal)
    return _par_from_periods(curve, periods, f"the {tenor_label(_months(months))} swap from {as_date(effective)}")


@dataclass(frozen=True, kw_only=True)
class OisSwap:
    """An overnight indexed swap: ``notional`` (rupees), ``fixed_rate_pct``, from ``effective`` for ``months``.

    ``pay_fixed=True`` pays fixed and receives floating (the S1 hedge of a long bond).
    """

    effective: date
    months: int
    fixed_rate_pct: float
    notional: float
    pay_fixed: bool = True
    conventions: OisConventions = MIBOR_OIS

    def __post_init__(self) -> None:
        object.__setattr__(self, "effective", as_date(self.effective))
        object.__setattr__(self, "months", _months(self.months))
        object.__setattr__(self, "fixed_rate_pct", _num(self.fixed_rate_pct, "fixed_rate_pct"))
        notional = _num(self.notional, "notional")
        if not notional > 0:
            raise ValueError(f"notional must be above zero, not {notional}")
        object.__setattr__(self, "notional", notional)
        if not isinstance(self.pay_fixed, bool):
            raise TypeError(f"pay_fixed must be True or False, not {self.pay_fixed!r}")
        _buildable(self.conventions)

    @classmethod
    def spot(cls, trade_date, tenor, fixed_rate_pct: float, notional: float, *, pay_fixed: bool = True,
             conventions: OisConventions = MIBOR_OIS, calendar: BusinessCalendar | None = None) -> OisSwap:
        """A spot-starting swap traded on ``trade_date``: effective T+1."""
        return cls(effective=effective_date(trade_date, conventions, calendar), months=_months(tenor),
                   fixed_rate_pct=fixed_rate_pct, notional=notional, pay_fixed=pay_fixed, conventions=conventions)


@dataclass(frozen=True)
class OisValuation:
    """``ois_npv``'s answer, in rupees at the curve's valuation date (signs for the swap's own side).

    ``npv`` = ``pv_fixed`` - ``pv_float`` for a receiver of fixed, the reverse for a payer (the legs as received or
    paid, with their notional). ``annuity`` = ``sum tau_k P(E_k)`` over the periods left; ``pv01`` the value of
    1 bp on the fixed rate (``notional x annuity x 1e-4``, positive); ``fair_rate_pct`` the fixed rate that makes
    the swap worth nothing. ``dv01`` and ``dv01_par``: value lost for a 1 bp rise in zero rates, or in every par
    quote (NaN for a curve without quotes). ``accrued_fixed`` and ``accrued_float``: the running period's interest
    to date; ``realised``: its compounded fixings (``None`` before the first period starts).
    """

    valuation_date: date
    npv: float
    pv_fixed: float
    pv_float: float
    annuity: float
    pv01: float
    fair_rate_pct: float
    dv01: float
    dv01_par: float
    accrued_fixed: float
    accrued_float: float
    periods_left: int
    realised: Compounded | None
    calendar_projected: bool


def _legs(curve, swap: OisSwap, periods: Sequence[Period], fixings, cal, missing) -> tuple[float, float, Any]:
    """(annuity, floating leg per unit notional, compounded fixings of the running period)."""
    t = curve.valuation_date
    live = [p for p in periods if p.end > t]
    dfs = _discounts(curve, [p.end for p in live], f"the swap from {swap.effective}")
    annuity = float(sum(p.year_fraction * v for p, v in zip(live, dfs)))
    floating, realised = 0.0, None
    starts = [p.start for p in live if p.start >= t]
    start_dfs = dict(zip(starts, _discounts(curve, starts, f"the swap from {swap.effective}"))) if starts else {}
    for p, pe in zip(live, dfs):
        if p.start >= t:
            floating += start_dfs[p.start] - pe
        else:
            realised = compound_overnight(fixings, p.start, t, calendar=cal, missing=missing,
                                          conventions=swap.conventions)
            floating += realised.factor - pe
    return annuity, floating, realised


def ois_npv(curve, swap: OisSwap, *, fixings=None, calendar: BusinessCalendar | None = None,
            missing: str = "raise") -> OisValuation:
    """Value ``swap`` on ``curve`` (any ``DiscountCurveLike``) at the curve's valuation date.

    Periods that ended on or before the valuation date are settled and left out. A running period needs the
    fixings of its business days before the valuation date (``fixings``, ``missing`` as in
    ``compound_overnight``).
    """
    if not isinstance(swap, OisSwap):
        raise TypeError("swap must be an OisSwap")
    conv = swap.conventions
    cal = _calendar(calendar if calendar is not None else getattr(curve, "calendar", None), conv)
    periods = ois_schedule(swap.effective, swap.months, conv, cal)
    t = curve.valuation_date
    if periods[-1].end <= t:
        raise ValueError(f"the swap ended on {periods[-1].end}, on or before the valuation date {t}")
    n, k = swap.notional, swap.fixed_rate_pct / 100
    sign = -1.0 if swap.pay_fixed else 1.0

    def npv_on(c) -> float:
        annuity, floating, _ = _legs(c, swap, periods, fixings, cal, missing)
        return sign * n * (k * annuity - floating)

    annuity, floating, realised = _legs(curve, swap, periods, fixings, cal, missing)
    npv = sign * n * (k * annuity - floating)
    accrued_fixed = accrued_float = 0.0
    if realised is not None:
        accrued_fixed = n * k * realised.days / conv.year_days
        accrued_float = n * (realised.factor - 1.0)
    dv01 = npv - npv_on(_Shifted(curve, 1.0))
    dv01_par = math.nan
    if isinstance(curve, OisCurve) and curve.quotes:
        dv01_par = npv - npv_on(curve.bumped(1.0))
    return OisValuation(valuation_date=t, npv=npv, pv_fixed=n * k * annuity, pv_float=n * floating, annuity=annuity,
                        pv01=n * annuity * 1e-4, fair_rate_pct=floating / annuity * 100, dv01=dv01, dv01_par=dv01_par,
                        accrued_fixed=accrued_fixed, accrued_float=accrued_float,
                        periods_left=sum(p.end > t for p in periods), realised=realised,
                        calendar_projected=bool(getattr(curve, "calendar_projected", False))
                        or beyond_calendar(cal, [p.end for p in periods]))


class _Shifted:
    """Any ``DiscountCurveLike`` with its continuously compounded zero rates ``bp`` higher."""

    def __init__(self, curve, bp: float):
        self._curve, self._shift = curve, bp / 1e4
        self.valuation_date, self.t_max = curve.valuation_date, curve.t_max

    def discount(self, when) -> np.ndarray:
        dates, scalar = _date_list(when)
        base = np.asarray(self._curve.discount(dates), dtype=float).reshape(-1)
        t = np.array([(d - self.valuation_date).days for d in dates], dtype=float) / _YEAR
        out = base * np.exp(-self._shift * t)
        return out.reshape(()) if scalar else out


def curve_table(curve: OisCurve, *, step_months: int = 1) -> pd.DataFrame:
    """The curve every ``step_months`` from the effective date to its last node (``CURVE_COLUMNS``).

    Each row's date is the effective date plus that many months, adjusted Modified Following; the forward rate is
    the simple rate from the previous row's date, and ``par_pct`` the par rate of a standard swap of that tenor
    (blank where the tenor is not a whole number of periods).
    """
    if not isinstance(curve, OisCurve) or curve.effective is None:
        raise TypeError("curve_table needs an OisCurve from bootstrap_ois")
    step = as_int(step_months, "step_months", minimum=1)
    cal = curve.calendar or calendar_for(curve.conventions.calendar_name)
    rows, prev, months = [], curve.effective, step
    limit = curve.t_max + curve.max_extrapolation_years
    while True:
        end = cal.adjust(add_months(curve.effective, months), curve.conventions.roll)
        t = curve.year_fraction(end)
        if t > limit + 1e-12:
            break
        p = float(curve.discount(end))
        z = float(curve.zero_rate(t))
        par = math.nan
        try:
            par = ois_par_rate(curve, curve.effective, months)
        except ValueError:
            pass
        rows.append({"tenor": tenor_label(months), "months": months, "end_date": end, "t_years": t, "discount": p,
                     "zero_cc_pct": z, "zero_annual_pct": float(curve.zero_rate(t, "annual")),
                     "zero_simple_pct": float(curve.zero_rate(t, "simple")),
                     "forward_simple_pct": curve.forward(prev, end), "par_pct": par,
                     "extrapolated": bool(t > curve.t_max)})
        prev, months = end, months + step
    return pd.DataFrame(rows, columns=list(CURVE_COLUMNS))


__all__ = [
    "CURVE_COLUMNS", "FCS_MIBOR_OIS", "INDEXES", "MIBOR_BREAKS", "MIBOR_HISTORY", "MIBOR_OIS", "MISSING_RULES",
    "NODE_COLUMNS", "QUOTE_COLUMNS", "QUOTE_KINDS", "REPRICE_TOL_PCT", "SORR_OIS", "ZERO_BASES", "Compounded",
    "OisConventions", "OisCurve", "OisSwap", "OisValuation", "Period", "add_months", "as_time", "bootstrap_ois",
    "compound_overnight", "curve_table", "effective_date", "fixing_map", "fixing_regime", "ois_npv", "ois_par_rate",
    "ois_quotes",
    "ois_schedule", "beyond_calendar", "settlement_amounts", "tenor_label", "tenor_months",
]
