"""The return engine: positions, daily valuation, funding, OIS hedges, NAV and a P&L attribution that adds up.

For the strategies of ``docs/rates_data_sources.md`` whose instruments ``bonds``, ``corporates`` and ``floating`` price
(S1 asset swaps first, then S3, S5 and S7; S2's CP, CD and corporate bonds, S4's auction trades, S6's SDLs, GoI FRBs):
a ledger of bond and OIS trades kept by book, valued every Mumbai business day from a table of marks, funded overnight
in TREPS or market repo at the RBI store's rates, charged the costs of ``costs``, with each day's change in NAV split
into lines that sum to it exactly. ``run_book`` runs a list of trades; ``run_backtest`` runs a strategy that decides
each evening on what was public then and trades the next business day. Every price, accrued interest, yield, DV01 and
convexity comes from ``bonds`` (or ``corporates`` and ``floating``), every OIS value from ``ois.ois_npv`` and
``ois.settlement_amounts``, every haircut and TREPS cap from ``funding``, every curve from ``curvefit`` (any
``DiscountCurveLike``); nothing here builds a cash flow. The rules are those of the return-engine
specification of 19 Nov 2026 (its section numbers in brackets); each rule no source confirms is a named parameter marked
UNVERIFIED here and in ``docs/analytics.md``.

**Positions** (A1.2-A1.4). A bond is held from its settlement date, T+1 on the Mumbai calendar for G-secs, SDLs and
T-bills (``calendars.settlement_date``; T+2 with an FPI by the trade's ``settlement_date``), T+0 or T+1 for CP and
CD; a T+0 G-sec trade is refused unless ``allow_t0`` (the handbook lists no T+0 window for G-secs: partly verified).
Between trade and settlement the trade is a forward worth ``F/100 (P_dirty(y_d, S) - P_trade - AI(S))`` on mark
date ``d`` (``y_d`` the day's market yield; clean on clean for the usual T+1 trade; discounting from ``S`` to ``d``
ignored, UNVERIFIED). A settled bond is worth ``F/100 (clean mark for standard settlement + accrued interest to the
mark date)`` (UNVERIFIED book convention; on the last days before redemption, when standard settlement would fall on
or after maturity, the mark is restated to settlement on the day itself). A coupon is income on its scheduled date
to the holder settled the day before and a receivable until paid (``bonds.cash_flows``: a coupon on a holiday paid
the next working day, the redemption and last coupon the previous one, amounts unchanged); on the redemption's
payment date the position closes with ``accrual = F/100 (C - AI)`` and ``pull_to_par = F/100 (100 - clean)``. A
trade settling in the one-day shut period before redemption (UNVERIFIED rule of ``conventions``), on or after the
redemption's payment date (the working day before a maturity on a holiday: the holder is redeemed at the start of that
day), on a non-business day, before issue or on or after maturity is refused, as is a duplicated trade id. A
when-issued trade settles on the auction's settlement date (default: the new issue's issue date) and is a forward
until then; a security not yet issued is marked for its issue date.

**Marks** (A1.8). ``MARK_COLUMNS``: a clean price or yield per ISIN and day for the standard settlement of that day
(or a ``settlement_date`` given, restated at its own yield), its ``source`` (``traded``: the day's VWAP, else the
last trade; ``valuation``: FBIL or another valuation price) and ``public_from``; a row is used only from its
``public_from`` (blank: 18:00 on its date, UNVERIFIED), the NAV of a day being struck at ``valuation_time`` (20:00,
UNVERIFIED). Priority: traded, valuation, then ``model`` (the day's reference curve plus the bond's last observed
residual), then ``stale`` (the last mark's clean price held), each flagged with its age; model and stale marks older
than ``max_stale_days`` (5 business days, UNVERIFIED) are flagged ``stale_warning``. A position with no mark at all
yet, or traded today when its only mark is older, is valued at the fund's own fill of the day (``fill``, flagged).
Fills never use model or stale prices.

**Corporate bonds and FRBs.** An ISIN listed in ``MarketData.corporate_terms`` is a corporate bond priced by
``corporates.CorporateBond`` (with its schedule, calls and puts, amortisation, rating-linked steps and, for
``frb_corp``, benchmark fixings); a GoI FRB (``frb``) is priced by ``floating.GoiFloatingRateBond`` from ``frb_terms``
(the shipped table by default). A corporate trade settles on the exchange, T+1 unless its ``settlement_date`` says
otherwise (T+0 allowed, handbook 3.5.1); an FRB as a G-sec. Each day at the valuation time, and at each trade's
settlement check, the bond is rebuilt as known then (``Instrument.known``): a ``frb_corp`` fixing, a rating action and
a call's or put's exercise only from their ``public_from``; an FRB's coupon as ``floating.goi_frb_rates`` knows it
(announced, computed from the three latest 182-day T-bill results public by then, or projected flat and noted; a period
it cannot project is refused unless ``frb_allow_fewer``). When the bond as known changes, the previous close's mark is
restated on it (its yield from the same clean price) and the interest accrued to that close is restated at the new
coupon in the ``coupon_reset`` line, ``F/100 (AI_new(prev) - AI_old(prev))``, nonzero only when a coupon becomes known
after its period began (a fixing or rating action published late). A corporate coupon goes to the holder at the close
of its record date (``CorporateBond.record_date``: 15 days before the due date under the SEBI conventions of
``corporates``, a holiday moved to the preceding day; at the latest the day before the due date; a record date on a
holiday takes the previous business day's close) and is a receivable until its payment date; a settlement after the
record date is ex-interest, its accrued interest negative to the due date. A partial redemption is entitled the same
way (``pull_to_par``: the principal booked at face against the clean price's fall), the mark of its record date,
whose standard settlement is ex the principal, is restated to settle that day, and a stale mark held across that
record date holds its yield rather than its price (which still holds the principal); positions keep the original face
(``corporates`` prices per 100 original face; the notes give the outstanding face). The redemption closes the position
at its record date's close into a receivable: trading is suspended after it, so a later settlement is refused and a
mark whose standard settlement falls after it is restated to settle on the day itself. An exercised call or put
redeems at its date and price once the exercise is public; an unexercised option changes no flow (the bond is valued
to maturity and the mark carries the option). Costs (``costs``): the debenture stamp duty (0.0001% of the
consideration, on the buyer), the exchange's fee (Rs 5 a crore of value, Rs 25 to Rs 500, GST extra), no CCIL charge,
the half spread by the trade's ``rating_bucket`` (else the terms', else the widest, all UNVERIFIED); FRBs take the
G-sec rows. Funding: neither is TREPS collateral here (corporate bonds are not on CCIL's list; FRBs are GoI dated
securities but are taken as ineligible, UNVERIFIED and conservative); with a row for its kind in the user's haircut
table a bond is lent against in market repo (``excess_segment``), without one not at all (equity-funded). Attribution:
a corporate bond's yield moves against the G-sec curve (``rich_cheap`` is its spread's change); an FRB has no default
reference curve, so its price move is ``price_unattributed`` (a fixed-rate DV01 on projected coupons would overstate
its rate risk).

**Attribution** (A2). For each position and day, market lines on the start-of-day book from close to close: ``accrual``
(accrued interest gained plus coupons entitled), ``pull_to_par`` (``F/100 (clean(y0, s_d) - clean(y0, s_prev))``), and
the yield move ``y3 - y0 = dy_roll + dy_curve + de`` exactly, with ``dy_roll = m(R) - m(C_prev)`` (``R`` the previous
curve moved to the valuation date of today's curve with zero rates unchanged as a function of time, then seen from
today's settlement: settlement to settlement for a G-sec curve, trade date to trade date for an OIS curve, as the OIS
lines roll; UNVERIFIED standard definition), ``dy_curve = m(C_d) - m(R)`` split into ``curve_parallel``,
``curve_slope``, ``curve_curvature`` (Legendre shapes in log tenor fitted by least squares to the continuous zero-rate
changes at 11 nodes from 0.5 to 40 years, each revalued from the rolled curve, UNVERIFIED basis; on a money-market curve
with fewer usable nodes than factors only the leading shapes are fitted; key rates as the alternative) and
``curve_other``, and ``de`` the change of the bond's residual (``rich_cheap``). Each first-order line is ``-F/100 x DV01
x 100 dy`` at the carried state ``(y0, s_d)``; ``convexity = F/100 x 0.5 CX P ((y3 - y0)/100)^2``; ``unexplained`` is
the rest of the clean move (third order, plus whatever a stale mark holds), flagged above ``max(1e-3 |move|, Rs 1)``.
Without a reference curve on either day the first-order move is ``price_unattributed``. Trades of the day add
``trading`` (close against the fill's mid) and ``spread_cost`` (mid against the fill); ``fees``, ``brokerage``, ``gst``
and ``stamp_duty`` come from ``costs``. Funding lines are the night's interest at each source (``funding_repo``,
``funding_excess``, ``funding_overdraft``), ``cash_interest`` and ``repo_charges``; the haircut's and the TREPS margin's
opportunity costs and a risk-free benchmark are memo lines (``MEMO_LINES``), so the lines still sum to the NAV change.
OIS lines by revaluation with ``ois_npv``: ``ois_carry`` (accrued floating less fixed), ``ois_roll_down`` (the
constant-curve rest), the curve factors on shifted rolled curves and ``ois_settlement_rounding`` (the rounded payment
less the unrounded accrual). Identity I1: for every book and day the non-memo lines sum to ``NAV_d - NAV_prev - flow_d``
within ``1e-12 x (gross value + 1)`` rupees (``checks``).

**Funding** (A1.5). Each business day a book borrows for the night the cash it needs after the day's settlements,
coupons, costs and flows, rounded up to TREPS's lot of Rs 5 lakh (CCIL; the sizing itself UNVERIFIED; ``sizing="fixed"``
keeps the amount borrowed at a purchase, as ``funding.carry`` assumes); interest simple Act/365, paid with the second
leg, every night of a deal at its deal day's rate (``rate_basis="deal_day"``, the default: the engine deals on business
days only) or each calendar day at the rate of the latest dealing day on or before it (``"calendar_day"``, as
``funding.carry``; the two differ over a Saturday money markets dealt on; UNVERIFIED choice). The limit is the
haircut-adjusted value of the long bonds at the previous close (``funding.haircut`` from the user's dated table) less
TREPS's initial margin (``costs``: 0.50% of the second leg from 5 Jul 2025); CCIL's TREPS caps on SDLs and illiquid
G-secs (``funding.treps_shares``) send the capped part to ``excess_segment`` (market repo), TREPS taken first
(UNVERIFIED). A shortfall is refused, or funded at an overdraft rate the user gives. Shorts borrow the bond in a reverse
repo at a special rate the user gives, lending ``(1 - h)`` of the bond's value; coupons are paid away. Idle cash earns
``idle_cash_rate_pct`` (UNVERIFIED default 0). Funding or risk-free nights past the end of a funding table are refused
unless ``projection="last"`` (then noted, counted as calendar nights, once whatever the books and segments).
``positions.funded`` is each long's share of the night's borrowing, pro rata of its lendable value ``(1 - h) x value``
at the deal; the shares sum to the amount borrowed.

**Access** (A1.9). An ``AccessPolicy`` is required: the presets ``nbfc_non_pd`` (repo and TREPS yes, no shorts, OIS for
any purpose), ``unregulated_company`` (no repo unless listed, no shorts, OIS for hedging only) and ``bank_or_pd``
(shorts in central government dated securities within the larger of 2% of the stock or Rs 500 crore for liquid
securities and 1% or Rs 250 crore for others, covered within three months), with the when-issued limits of 25% long and
10% short of the notified amount (25% both for banks and PDs), from RBI's Repo Directions 2025, Short Sale and When
Issued Directions 2018 and Rupee IRD Directions 2025 as the verified notes record them. A when-issued sale is not a
short sale: it is held to the when-issued limits, and a short left open at the close of the auction day is refused
unless the preset may carry it (banks and PDs); one carried past that close settles on the issue date as a short sale,
so it must then meet the short-sale rules (the right, the kinds, the limit, a special repo rate) or is refused at the
close. Under ``hedge_only`` the book's OIS must offset its bonds' DV01 (at most ``HEDGE_TOLERANCE``) on a hedge's trade
day (else refused, unless the swap reduces the OIS DV01) and every business day after (else a ``hedge_only`` breach row
in ``checks`` and a note). A trade the policy forbids is refused, naming the rule.

**Performance** (A4). Daily return with start-of-day flows ``r = (NAV - NAV_prev - F) / (NAV_prev + F)``, chained
into a time-weighted return and an index; excess over TREPS for the calendar days of each step; annualisation by the
business days observed a year (UNVERIFIED); Sharpe with ddof 1; drawdowns on the index; leverage; DV01 by tenor
bucket; turnover; costs as a share of gross P&L.

**Back-tests** (A5). A strategy's ``decide`` gets a ``Snapshot`` as of its decision time (20:00, UNVERIFIED; never
before the valuation time, which ``EngineSettings`` refuses, so the day's NAV and positions it sees hold no later mark):
marks, curves, funding rates and fixings only as public by then (each table's ``public_from``; a funding row without a
time counts as not public, a mark or curve without one as public at 18:00 on its date (UNVERIFIED), a fixing without one
at FBIL's 12:45), and through its accessors the event ledger (``event_ledger.events_as_of``), the auction table (a
security's ISIN only once it was issued or its result public), the RBI store's tables, macro series, the MPC calendar,
the residual history and the master (the securities issued or with a result public by then, their fixed terms only),
each cut at its time (``Snapshot``). Orders execute the next business day at that day's VWAP plus or minus the half
spread (``execution="next_close"`` at the close), only at traded prices unless ``fill_sources`` says otherwise, waiting
up to ``max_wait_days`` (5, UNVERIFIED) business days, then cancelled and logged; OIS orders at the day's curve par rate
plus or minus the half spread; auction orders at the cut-off (or, non-competitive, the weighted average price) of the
market data's auction table on the auction day, refused when decided on or after the auction day, before its
announcement was public or without an announcement time, or naming a new issue by its ISIN before the result (a bid
names such a line by its ``line``, ``new GS 2036``), a non-competitive bid above Rs 2 crore (GoI dated) or 1% of the
notified amount (SDLs) refused, a competitive one filled up to ``auction_max_share`` (10%, UNVERIFIED) of the amount
accepted. The strict Mumbai calendar refuses dates past 2026 unless ``project_calendar``; every row then says
``calendar_projected``, as do the rows of a covered day whose next business day or settlement falls past the list (31
Dec 2026: taken as the next weekday).

**Determinism.** No clock is read; inputs are sorted (a day's trades: purchases without a time first, then the timed
trades by time, then sales without a time, each group in the natural order of their ids, T9 before T10, so a sale
whose time is not known is checked against all the day's purchases; books, ISINs and swaps by name), a day's flows
summed exactly (``math.fsum``), and reference rows given twice with different values (master, liquidity classes,
auctions, stock, residual history) refused, so the same inputs in any row order give byte-identical outputs. All-blank
CSV rows are dropped; messages keep the file's line numbers.

**Limits.** Built and tested on synthetic data only (bonds priced off known NSS curves, synthetic OIS quotes and
fixings, the RBI store's four days of Nov 2026 for funding): no licensed mark, curve, fixing or OIS quote has been
used. Unverified: the valuation convention, the roll-down definition, the factor basis, the missing-mark rules, the
funding sizing and rate basis, the TREPS-first split, the margin's form, a hedge's DV01 allowed up to
``HEDGE_TOLERANCE`` (105%) of its bonds' under ``hedge_only``, the auction allotment share, the costs marked U in
``costs``, the decision and mark times; for corporate bonds and FRBs, the exchange's T+1, the record-date rules of
``corporates``, FRBs as not TREPS collateral, the half spreads by rating, the flat projection of an unknown coupon.
Not built: inflation-indexed bonds (no pricer: refused); a corporate bond's default or missed payment (every scheduled
flow is paid), a partial exercise of a call or put, a ``frb_corp`` bond with rating steps as well, an FRB's split into
index and spread risk (``asset_swaps.frb_measures`` gives the measures); CSA, clearing margin or charges on OIS;
security-wise repo rates (a short's special rate is the user's, constant over the short); the key-rate DV01 table (DV01
is by tenor bucket); the funding lines allocated to positions (they stay at book level; ``positions.funded`` gives each
long's share for reporting); the tradable universe from the issuance ledger (a strategy's own job); an auction's own
allotment rules beyond the limits above (pro-rata at the cut-off, the 5% non-competitive pool); intraday timing beyond
the decision time; tax.
"""

from __future__ import annotations

import bisect
import calendar as _calendar
import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from typing import Any, Protocol

import numpy as np
import pandas as pd

from cr_strat import costs as cost_rules
from cr_strat.bonds import DiscountInstrument, FixedRateBond, ZeroCouponBond
from cr_strat.calendars import BusinessCalendar, CalendarCoverageError, mumbai_settlement_calendar
from cr_strat.conventions import CONVENTIONS, as_date
from cr_strat.corporates import (
    CorporateBond,
    apply_rating_steps,
    corporate_from_terms,
    read_amortisation,
    read_corporate_options,
    read_corporate_schedule,
    read_corporate_terms,
    read_step_rules,
    step_rule_from_row,
)
from cr_strat.curvefit import FittedCurve, NssCurve
from cr_strat.floating import (
    FRB_COUPONS_CSV,
    GoiFloatingRateBond,
    corporate_frb_rates,
    floating_terms_from_row,
    goi_frb_bond,
    goi_frb_rates,
    load_frb_coupons,
    load_frb_terms,
    rate_steps,
    read_fixings,
    read_floating_terms,
    tbill_way_table,
)
from cr_strat.funding import FUNDING_SOURCES, haircut, read_haircuts, treps_class, treps_shares
from cr_strat.issuance import TENOR_BUCKETS, tenor_bucket
from cr_strat.ois import (
    MIBOR_OIS,
    OisCurve,
    OisSwap,
    as_time,
    compound_overnight,
    effective_date,
    ois_npv,
    ois_par_rate,
    ois_schedule,
    settlement_amounts,
)

# ---------------------------------------------------------------- layouts

TRADE_COLUMNS = ("trade_id", "book", "isin", "kind", "side", "face", "price", "yield_pct", "trade_date", "trade_time",
                 "settlement_date", "venue", "mid_price", "liquidity", "broker", "special_repo_pct", "notified_cr",
                 "coupon_pct", "maturity_date", "issue_date", "first_coupon_date", "rating_bucket", "note")
SWAP_TRADE_COLUMNS = ("trade_id", "book", "trade_date", "effective", "months", "fixed_rate_pct", "notional",
                      "pay_fixed", "mid_rate_pct", "purpose", "note")
FLOW_COLUMNS = ("date", "book", "amount")
MARK_COLUMNS = ("date", "isin", "clean_price", "yield_pct", "source", "settlement_date", "vwap", "last_price",
                "volume_cr", "trade_count", "public_from")
MARK_SOURCES = ("traded", "valuation")
MARK_ORDER = ("traded", "valuation", "model", "stale")
POSITION_COLUMNS = ("date", "book", "isin", "kind", "state", "face", "settlement_date", "clean_mark", "mark_source",
                    "mark_age_days", "yield_pct", "accrued", "value", "dv01", "bucket", "liquidity", "haircut_pct",
                    "funded", "stale_warning", "extrapolated", "calendar_projected", "notes")
BUCKETS = tuple(label for _, _, label in TENOR_BUCKETS)
NAV_COLUMNS = ("date", "book", "bonds", "forwards", "ois", "cash", "repo_borrowed", "lent", "receivables", "nav",
               "flow", "pnl", "return", "index", "rf_return", "gross_long", "gross_short", "ois_notional",
               "leverage", "repo_leverage", "dv01", *(f"dv01_{b}" for b in BUCKETS), "calendar_projected", "notes")
ATTRIBUTION_COLUMNS = ("date", "book", "instrument", "line", "amount", "memo")
FILL_COLUMNS = ("date", "order_id", "trade_id", "book", "instrument", "side", "face", "mid", "fill", "venue",
                "settlement_date", "spread_cost", "fees", "brokerage", "gst", "stamp_duty", "rules", "status", "notes")
FUNDING_COLUMNS = ("date", "book", "segment", "borrowed", "rate_pct", "repay_date", "interest", "charges", "limit",
                   "haircut_equity", "margin", "lent_short", "idle_cash", "projected_days", "notes")
CHECK_COLUMNS = ("date", "book", "instrument", "check", "value", "tolerance", "status", "detail")
PERFORMANCE_COLUMNS = ("book", "start", "end", "business_days", "calendar_days", "obs_per_year", "twr",
                       "annual_return", "mean_daily", "vol_daily", "annual_vol", "excess_twr", "sharpe",
                       "max_drawdown", "drawdown_peak", "drawdown_trough", "drawdown_recovery", "drawdown_days",
                       "recovery_days", "avg_nav", "avg_leverage", "max_leverage", "avg_repo_leverage",
                       "avg_ois_leverage", "avg_dv01", *(f"avg_dv01_{b}" for b in BUCKETS), "traded_face",
                       "turnover", "traded_dv01", "dv01_turnover", "pnl", "costs", "costs_share", "costs_bp_nav",
                       "notes")
DRAWDOWN_COLUMNS = ("date", "book", "index", "peak", "drawdown")

CARRY_LINES = ("accrual", "pull_to_par")
RESET_LINE = "coupon_reset"  # a coupon as known changing in the period it is accrued (FRBs, rating steps)
FACTORS = ("parallel", "slope", "curvature")
TRADE_LINES = ("trading", "spread_cost")
COST_LINES = ("fees", "brokerage", "gst", "stamp_duty")
FUNDING_LINES = ("funding_repo", "funding_excess", "funding_overdraft", "cash_interest", "repo_charges")
OIS_BASE_LINES = ("ois_carry", "ois_roll_down")
MEMO_LINES = ("memo_haircut_cost", "memo_margin_cost", "memo_cash_benchmark")
COSTS_IN_PNL = ("spread_cost", "fees", "brokerage", "gst", "stamp_duty", "repo_charges")
DEFAULT_NODES = (0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 15.0, 20.0, 30.0, 40.0)  # UNVERIFIED node choice
DEFAULT_REFERENCE = (("gsec", "gsec"), ("green", "gsec"), ("special", "gsec"), ("tbill", "gsec"), ("cmb", "gsec"),
                     ("strips", "gsec"), ("sdl", "gsec"), ("cp", "ois"), ("cd", "ois"), ("corporate", "gsec"))
FACTOR_BASES = ("legendre3", "key_rates")
ROLL_BASES = ("constant_curve", "forwards")
PNL_BASES = ("actual", "excess_of_cash")
SIZINGS = ("cash_need", "fixed")
RATE_BASES = ("deal_day", "calendar_day")
SHORTFALL_RULES = ("raise", "overdraft")
EXECUTIONS = ("next_vwap", "next_close")
OIS_PURPOSES = ("none", "hedge_only", "any")
COUPON_KINDS = frozenset({"gsec", "green", "special", "sdl"})
DISCOUNT_KINDS = frozenset({"tbill", "cmb", "cp", "cd"})
TERMS_KINDS = frozenset({"corporate", "frb"})  # priced from their own terms tables: corporates, floating
PRICED_KINDS = COUPON_KINDS | DISCOUNT_KINDS | {"strips"} | TERMS_KINDS
NO_PRICER_KINDS = frozenset({"iib"})
CENTRAL_DATED = frozenset({"gsec", "green"})
T0_KINDS = frozenset({"cp", "cd", "corporate"})  # Handbook 3.5.1: T+0 windows for CP, CD and corporate bonds (to
# 14:30), none for G-secs (partly verified)
_KIND_CONVENTIONS = {"gsec": "gsec", "green": "gsec", "special": "gsec", "sdl": "sdl", "strips": "strips",
                     "tbill": "tbill", "cmb": "cmb", "cp": "cp", "cd": "cd"}
_YEAR = 365.0
_TRUE = {"true", "yes", "y", "1", "t"}
_FALSE = {"false", "no", "n", "0", "f"}
IDENTITY_RTOL = 1e-12
_ROLL_LOOKBACK_DAYS = 31  # a redemption on a holiday is paid at most this many days early


# ---------------------------------------------------------------- small helpers


def _blank(value) -> bool:
    if value is None or (isinstance(value, str) and not value.strip()):
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _text(value) -> str:
    return "" if _blank(value) else str(value).strip()


def _num(value, where: str, *, blank: float | None = None) -> float:
    if _blank(value):
        if blank is not None:
            return blank
        raise ValueError(f"{where} is blank")
    if isinstance(value, bool):
        raise ValueError(f"{where}: {value!r} is not a number")
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{where}: {value!r} is not a number") from None
    if not math.isfinite(out):
        raise ValueError(f"{where}: {value!r} is not a finite number")
    return out


def _opt_num(value, where: str) -> float | None:
    return None if _blank(value) else _num(value, where)


def _date(value, where: str) -> date:
    try:
        return as_date(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{where}: {value!r} is not a date ({exc})") from None


def _opt_date(value, where: str) -> date | None:
    return None if _blank(value) else _date(value, where)


def _bool(value, where: str, default: bool) -> bool:
    if _blank(value):
        return default
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ValueError(f"{where}: {value!r} is not true or false")


def _time_of(value, where: str) -> datetime | None:
    if _blank(value):
        return None
    return as_time(value, where)


def _at(day: date, clock: time) -> datetime:
    return datetime.combine(day, clock)


def _choice(value, allowed, name: str) -> None:
    if value not in allowed:
        raise ValueError(f"{name} must be one of {allowed}, not {value!r}")


def _frame(table, what: str) -> pd.DataFrame:
    """A DataFrame from one, from a list of row dicts or from a CSV path; columns lower-cased and stripped."""
    if isinstance(table, pd.DataFrame):
        out = table.copy()
    elif isinstance(table, (list, tuple)):
        out = pd.DataFrame(list(table))
    else:
        from pathlib import Path
        if not Path(table).exists():
            raise FileNotFoundError(f"{what} not found: {table}")
        out = pd.read_csv(table, dtype=str, keep_default_na=False)
    out.columns = [str(c).strip().lower() for c in out.columns]
    return out


# ---------------------------------------------------------------- policies


@dataclass(frozen=True)
class FundingPolicy:
    """How each book is funded overnight (A1.5).

    ``segment``: the ``funding.funding_rates`` source of the main borrowing (``triparty``: TREPS); ``excess_segment``
    for what CCIL's TREPS caps leave (``market_repo``). ``spread_bp`` over every rate of the cash borrowed (UNVERIFIED
    default 0, as ``funding.carry``). ``sizing``: ``cash_need`` (borrow what the day's cash needs, rounded up to
    ``lot_rs``, Rs 5 lakh, TREPS's lot) or ``fixed`` (keep the amount borrowed at each purchase, ``(1 - h)`` of its
    consideration, as ``funding.carry``; cash may go negative at 0%). ``idle_cash_rate_pct`` on cash left over
    (UNVERIFIED default 0). ``margin_pct``: TREPS initial margin as a percentage of the second leg (``None``: the cost
    table's ``treps_margin_pct``; 0 to leave it out). ``on_shortfall``: ``raise`` when the cash need exceeds the
    borrowing limit, or ``overdraft`` at ``overdraft_rate_pct`` (no default). ``projection="last"`` holds the last
    dealing rate past the end of the funding table (counted and noted), for the funding and for the risk-free rate;
    otherwise such a night is refused. ``rate_basis``: ``deal_day`` (default) charges every night of a deal the rate of
    the day it was struck (the engine deals only on business days, so a deal struck on Thursday for Monday bears
    Thursday's rate for four nights); ``calendar_day`` prices each calendar day at the rate of the latest dealing day
    on or before it, as ``funding.carry`` does (a Saturday with a money-market rate prices itself and the Sunday;
    UNVERIFIED choice: the two differ only on such Saturdays).
    """

    segment: str = "triparty"
    excess_segment: str = "market_repo"
    spread_bp: float = 0.0
    sizing: str = "cash_need"
    lot_rs: float = 500_000.0
    idle_cash_rate_pct: float = 0.0
    margin_pct: float | None = None
    treps_caps: bool = True
    on_shortfall: str = "raise"
    overdraft_rate_pct: float | None = None
    projection: str | None = None
    rate_basis: str = "deal_day"

    def __post_init__(self) -> None:
        _choice(self.segment, FUNDING_SOURCES, "segment")
        _choice(self.excess_segment, FUNDING_SOURCES, "excess_segment")
        _choice(self.sizing, SIZINGS, "sizing")
        _choice(self.rate_basis, RATE_BASES, "rate_basis")
        _choice(self.on_shortfall, SHORTFALL_RULES, "on_shortfall")
        if self.projection not in (None, "last"):
            raise ValueError(f"projection must be None or 'last', not {self.projection!r}")
        for name in ("spread_bp", "lot_rs", "idle_cash_rate_pct"):
            _num(getattr(self, name), name)
        if self.lot_rs < 0:
            raise ValueError(f"lot_rs must be zero or more, not {self.lot_rs}")
        if self.margin_pct is not None and not 0 <= _num(self.margin_pct, "margin_pct") < 100:
            raise ValueError(f"margin_pct must be from 0 to below 100, not {self.margin_pct}")
        if self.on_shortfall == "overdraft" and self.overdraft_rate_pct is None:
            raise ValueError("on_shortfall='overdraft' needs overdraft_rate_pct (no default is assumed)")


@dataclass(frozen=True)
class AccessPolicy:
    """What the fund's legal form lets it do (A1.9); required, never defaulted.

    ``repo`` and ``treps``: may borrow in market repo and in TREPS; ``short_sale``: may sell bonds it does not hold,
    only of ``short_kinds`` (central government dated securities), within the larger of ``short_limit_*_pct`` of the
    stock (when a stock is known) and ``short_limit_*_rs``, covered within ``short_cover_months``; ``ois``:
    ``none``, ``hedge_only`` (a swap must offset the book's bond DV01 and say purpose ``hedge``, checked on its trade
    day and every business day after: a breach is a ``hedge_only`` row of ``checks``) or ``any``;
    ``when_issued_long_pct`` and ``when_issued_short_pct`` of the notified amount. A when-issued sale is not a short
    sale (When Issued Directions 2018 para 4 lets every entity eligible to bid in the auction sell when issued);
    ``when_issued_short_overnight`` says whether a when-issued short may stay open past the close of the auction day
    (only scheduled commercial banks and primary dealers: para 4, verified notes S08 and P23); carried, it settles on
    the issue date as a short sale and must meet the short-sale rules above at that close.
    """

    name: str
    repo: bool
    treps: bool
    short_sale: bool
    ois: str
    short_kinds: tuple[str, ...] = ("gsec", "green")
    short_limit_liquid_pct: float = 2.0
    short_limit_liquid_rs: float = 500e7
    short_limit_other_pct: float = 1.0
    short_limit_other_rs: float = 250e7
    short_cover_months: int = 3
    when_issued_long_pct: float = 25.0
    when_issued_short_pct: float = 10.0
    when_issued_short_overnight: bool = False
    source: str = ""

    def __post_init__(self) -> None:
        _choice(self.ois, OIS_PURPOSES, "ois")
        for name in ("repo", "treps", "short_sale", "when_issued_short_overnight"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be True or False")


ACCESS_PRESETS: dict[str, AccessPolicy] = {
    "nbfc_non_pd": AccessPolicy(
        name="nbfc_non_pd", repo=True, treps=True, short_sale=False, ois="any",
        source="RBI Repo Directions 2025 para 4(1)(a) (an RBI-regulated entity may repo; TREPS as a CCIL constituent "
               "below Rs 200 crore net worth); Short Sale Directions 2018 para 3 (no approval route for an "
               "RBI-regulated non-PD NBFC); Rupee IRD Directions 2025 paras 5.2(ii), 5.4(ii) (a non-retail user, OIS "
               "for any purpose); When Issued Directions 2018 paras 4, 6 (25% long, 10% short, shorts closed by the "
               "auction day's close): verified notes"),
    "unregulated_company": AccessPolicy(
        name="unregulated_company", repo=False, treps=False, short_sale=False, ois="hedge_only",
        source="RBI Repo Directions 2025 para 4(1)(b), (e) (no repo unless listed or approved: make a variant with "
               "repo=True if listed); Short Sale Directions 2018 para 3; Rupee IRD Directions 2025 paras 5.2(iii), "
               "5.4(i) (a retail user below Rs 500 crore net worth and Rs 1,000 crore turnover hedges only): "
               "verified notes"),
    "bank_or_pd": AccessPolicy(
        name="bank_or_pd", repo=True, treps=True, short_sale=True, ois="any", when_issued_short_pct=25.0,
        when_issued_short_overnight=True,
        source="Short Sale Directions 2018 paras 3-5 (central government dated securities only; 2% of the stock or "
               "Rs 500 crore for liquid securities, 1% or Rs 250 crore for others, whichever is higher; covered within "
               "three months); When Issued Directions 2018 paras 4, 6 (25% long and short, shorts may be carried past "
               "the auction day): verified notes"),
}


@dataclass(frozen=True)
class MarkPolicy:
    """Which mark values a bond (A1.8): ``order`` of sources, ``max_stale_days`` (business days) before a model or
    stale mark is flagged, ``fill_sources`` a fill may use (traded only by default: never a valuation price unless
    asked), and ``default_time``, the public time of a mark row without one (18:00 on its date, UNVERIFIED; ``None``:
    such a row is never public)."""

    order: tuple[str, ...] = MARK_ORDER
    max_stale_days: int = 5
    fill_sources: tuple[str, ...] = ("traded",)
    default_time: time | None = time(18, 0)

    def __post_init__(self) -> None:
        bad = [s for s in self.order if s not in MARK_ORDER]
        if bad or len(set(self.order)) != len(self.order):
            raise ValueError(f"order must list sources of {MARK_ORDER} once each, not {self.order}")
        bad = [s for s in self.fill_sources if s not in MARK_SOURCES]
        if bad or not self.fill_sources:
            raise ValueError(f"fill_sources must be some of {MARK_SOURCES}, not {self.fill_sources}")


@dataclass(frozen=True)
class AttributionSettings:
    """How the market move is split (A2.2-A2.4).

    ``factor_basis``: ``legendre3`` (parallel, slope and curvature as Legendre polynomials in log tenor, fitted to the
    zero-rate changes at ``nodes``) or ``key_rates`` (the linear-interpolation triangles of the nodes). ``reference``:
    (kind, curve name) pairs; curve names are looked up in the market data (``ois`` is the OIS curve). ``roll_basis``:
    ``constant_curve`` (zero rates by time unchanged, the default) or ``forwards`` (forward rates realised: no
    roll-down). ``basis``: ``actual`` P&L, or ``excess_of_cash`` with the risk-free benchmark as a line.
    ``unexplained_rel`` and ``unexplained_abs_rs``: a day's unexplained is flagged above ``max(rel x |move|, abs)``.
    """

    factor_basis: str = "legendre3"
    nodes: tuple[float, ...] = DEFAULT_NODES
    reference: tuple[tuple[str, str], ...] = DEFAULT_REFERENCE
    roll_basis: str = "constant_curve"
    basis: str = "actual"
    unexplained_rel: float = 1e-3
    unexplained_abs_rs: float = 1.0

    def __post_init__(self) -> None:
        _choice(self.factor_basis, FACTOR_BASES, "factor_basis")
        _choice(self.roll_basis, ROLL_BASES, "roll_basis")
        _choice(self.basis, PNL_BASES, "basis")
        nodes = tuple(float(n) for n in self.nodes)
        if len(nodes) < 3 or any(b <= a for a, b in zip(nodes, nodes[1:])) or nodes[0] <= 0:
            raise ValueError(f"nodes must be at least three increasing positive tenors, not {self.nodes}")
        object.__setattr__(self, "nodes", nodes)
        object.__setattr__(self, "reference", tuple((str(k), str(v)) for k, v in self.reference))

    @property
    def reference_map(self) -> dict[str, str]:
        return dict(self.reference)

    @property
    def curve_lines(self) -> tuple[str, ...]:
        if self.factor_basis == "legendre3":
            return (*(f"curve_{f}" for f in FACTORS), "curve_other")
        return (*(f"krd_{_tenor_label(t)}" for t in self.nodes), "curve_other")

    @property
    def ois_curve_lines(self) -> tuple[str, ...]:
        return tuple(f"ois_{line}" for line in self.curve_lines)


def _tenor_label(t: float) -> str:
    return f"{t:g}y"


@dataclass(frozen=True)
class EngineSettings:
    """Everything a run needs besides its trades and market data; ``access`` is required.

    ``calendar`` (default the strict Mumbai settlement calendar; ``project_calendar`` makes it lenient past its years
    and flags the rows), ``decision_time`` and ``valuation_time`` (20:00 India time, UNVERIFIED; a decision before the
    valuation time is refused, since the day's NAV and positions it would see are struck from marks published after
    it: move ``valuation_time`` to the decision time or earlier), ``execution`` (``next_vwap`` or ``next_close``),
    ``max_wait_days`` (5), ``allow_t0`` (T+0 G-sec trades), the funding, mark and attribution settings, ``costs`` (a
    cost table, ``costs.read_cost_table``'s frame or a ``CostTable``; default the shipped defaults), ``rf_segment``
    (the risk-free rate's funding source, TREPS), and for a back-test's auction orders ``auction_max_share`` (a
    competitive order fills at most this share of the amount accepted: 10%, UNVERIFIED) and
    ``auction_nc_limit_rs`` (a non-competitive bid in a GoI dated auction: Rs 2 crore, RBI's auction notifications,
    verified; an SDL's is 1% of the notified amount, verified; T-bill non-competitive bids are not modelled).
    ``frb_allow_fewer``: project a GoI FRB's coupons from fewer than three public T-bill results (flagged), as
    ``floating.goi_frb_rates`` allows; otherwise such a projection is refused and the coupon stays unknown.
    """

    access: AccessPolicy
    calendar: BusinessCalendar | None = None
    decision_time: time = time(20, 0)
    valuation_time: time = time(20, 0)
    execution: str = "next_vwap"
    max_wait_days: int = 5
    allow_t0: bool = False
    funding: FundingPolicy = field(default_factory=FundingPolicy)
    marks: MarkPolicy = field(default_factory=MarkPolicy)
    attribution: AttributionSettings = field(default_factory=AttributionSettings)
    costs: Any = None
    project_calendar: bool = False
    rf_segment: str = "triparty"
    auction_max_share: float = 0.10
    auction_nc_limit_rs: float = 2e7
    frb_allow_fewer: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.access, AccessPolicy):
            raise TypeError("access must be an AccessPolicy (ACCESS_PRESETS has nbfc_non_pd, unregulated_company and "
                            "bank_or_pd): no default is assumed, as it decides whether S1, S3, S5 and S7 can run")
        _choice(self.execution, EXECUTIONS, "execution")
        _choice(self.rf_segment, FUNDING_SOURCES, "rf_segment")
        if isinstance(self.max_wait_days, bool) or not isinstance(self.max_wait_days, int) or self.max_wait_days < 0:
            raise ValueError(f"max_wait_days must be a whole number of days >= 0, not {self.max_wait_days!r}")
        for name in ("decision_time", "valuation_time"):
            if not isinstance(getattr(self, name), time):
                raise TypeError(f"{name} must be a datetime.time, not {getattr(self, name)!r}")
        if self.decision_time < self.valuation_time:
            raise ValueError(f"decision_time {self.decision_time:%H:%M} is before valuation_time "
                             f"{self.valuation_time:%H:%M}: the day's NAV and positions a decision sees are struck at "
                             "the valuation time, from marks that may be published after the decision; set "
                             "valuation_time to the decision time or earlier")
        if not 0 < _num(self.auction_max_share, "auction_max_share") <= 1:
            raise ValueError(f"auction_max_share must be above 0 and at most 1, not {self.auction_max_share!r}")
        if not _num(self.auction_nc_limit_rs, "auction_nc_limit_rs") > 0:
            raise ValueError(f"auction_nc_limit_rs must be a positive number of rupees, not "
                             f"{self.auction_nc_limit_rs!r}")


def access_policy(name: str) -> AccessPolicy:
    """The preset called ``name`` (``ACCESS_PRESETS``)."""
    try:
        return ACCESS_PRESETS[name]
    except KeyError:
        raise ValueError(f"no access preset {name!r}; one of {sorted(ACCESS_PRESETS)}") from None


# ---------------------------------------------------------------- curves: rolled, shifted, rebased, model yields


class _Rebased:
    """A ``DiscountCurveLike`` seen from a later date with the same forward rates: ``DF'(t) = DF(t + a) / DF(a)``."""

    def __init__(self, curve, day: date):
        self._curve = curve
        self.valuation_date = day
        self._a = (day - curve.valuation_date).days / _YEAR
        self._base = float(np.asarray(curve.discount_t(np.array([self._a])), dtype=float)[0])
        self.t_max = max(float(curve.t_max) - self._a, 0.0)
        self.t_min = max(float(getattr(curve, "t_min", 0.0)) - self._a, 0.0)

    def discount_t(self, t) -> np.ndarray:
        arr = np.asarray(t, dtype=float)
        return np.asarray(self._curve.discount_t(arr + self._a), dtype=float) / self._base

    def discount(self, when) -> np.ndarray:
        scalar = isinstance(when, (str, date, np.datetime64, pd.Timestamp))
        dates = [as_date(when)] if scalar else [as_date(w) for w in when]
        t = np.array([(d - self.valuation_date).days for d in dates], dtype=float) / _YEAR
        out = self.discount_t(t)
        return out.reshape(()) if scalar else out

    def zero_rate(self, t, basis: str = "continuous") -> np.ndarray:
        if basis != "continuous":
            raise ValueError("a rebased curve gives continuous zero rates only")
        arr = np.asarray(t, dtype=float)
        safe = np.where(arr > 0, arr, 1e-9)
        return -np.log(self.discount_t(safe)) / safe * 100

    def in_data_range(self, t) -> np.ndarray:
        arr = np.asarray(t, dtype=float)
        return (arr >= self.t_min - 1e-9) & (arr <= self.t_max + 1e-9)


def seen_from(curve, day) -> Any:
    """``curve`` (valued on an earlier date) seen from ``day`` with its forward rates kept: ``OisCurve.rebase`` for an
    OIS curve, a rebased wrapper otherwise. The curve itself when ``day`` is its valuation date."""
    d = as_date(day)
    if d == curve.valuation_date:
        return curve
    if d < curve.valuation_date:
        raise ValueError(f"a curve valued on {curve.valuation_date} cannot be seen from the earlier {d}")
    if isinstance(curve, OisCurve):
        return curve.rebase(d)
    return _Rebased(curve, d)


class _Moved:
    """Any ``DiscountCurveLike`` moved to a later valuation date with its zero rates unchanged as a function of time."""

    def __init__(self, curve, day: date):
        self.base = curve
        self.valuation_date = day
        self.t_max = curve.t_max
        self.calendar = getattr(curve, "calendar", None)
        self.conventions = getattr(curve, "conventions", MIBOR_OIS)

    def discount_t(self, t) -> np.ndarray:
        return np.asarray(self.base.discount_t(np.asarray(t, dtype=float)), dtype=float)

    def discount(self, when) -> np.ndarray:
        scalar = isinstance(when, (str, date, np.datetime64, pd.Timestamp))
        dates = [as_date(when)] if scalar else [as_date(w) for w in when]
        out = self.discount_t(np.array([(d - self.valuation_date).days for d in dates], dtype=float) / _YEAR)
        return out.reshape(()) if scalar else out

    def zero_rate(self, t, basis: str = "continuous") -> np.ndarray:
        return self.base.zero_rate(t, basis)


def rolled(curve, day):
    """The constant-curve roll (A2.3): ``curve`` moved to ``day`` with its zero rates unchanged as a function of time.

    A ``curvefit.FittedCurve`` (NSS, spline, flat) keeps its parameters with the new valuation date; an
    ``ois.OisCurve`` keeps its node times, its node dates shifted by the same days (quotes, fixing and effective date
    dropped, as it is no longer a bootstrap of them); any other curve is wrapped so that its discount factors by time
    stay the same. UNVERIFIED standard definition of roll-down; ``roll_basis="forwards"`` uses ``seen_from`` (forwards
    realised) instead.
    """
    d = as_date(day)
    if isinstance(curve, FittedCurve):
        return replace(curve, valuation_date=d)
    if isinstance(curve, OisCurve):
        k = (d - curve.valuation_date).days
        return replace(curve, valuation_date=d, node_dates=tuple(x + timedelta(days=k) for x in curve.node_dates),
                       quotes=(), overnight_fixing_pct=None, effective=None,
                       source=f"{curve.source} rolled to {d}".strip())
    return _Moved(curve, d)


class ShiftedCurve:
    """``base`` with its continuously compounded zero rates raised by ``shift(t)`` (a decimal function of years):
    ``DF(t) = DF_base(t) exp(-shift(t) t)``. Satisfies ``DiscountCurveLike`` and what ``ois.ois_npv`` asks of a curve
    (``discount`` of dates), like ``ois``'s private parallel shift."""

    def __init__(self, base, shift: Callable[[np.ndarray], np.ndarray]):
        self.base = base
        self.shift = shift
        self.valuation_date = base.valuation_date
        self.t_max = base.t_max
        self.calendar = getattr(base, "calendar", None)
        self.conventions = getattr(base, "conventions", MIBOR_OIS)
        self.max_extrapolation_years = getattr(base, "max_extrapolation_years", 0.0)

    def discount_t(self, t) -> np.ndarray:
        arr = np.asarray(t, dtype=float)
        return np.asarray(self.base.discount_t(arr), dtype=float) * np.exp(-np.asarray(self.shift(arr)) * arr)

    def discount(self, when) -> np.ndarray:
        scalar = isinstance(when, (str, date, np.datetime64, pd.Timestamp))
        dates = [as_date(when)] if scalar else [as_date(w) for w in when]
        t = np.array([(d - self.valuation_date).days for d in dates], dtype=float) / _YEAR
        base = np.asarray(self.base.discount(dates), dtype=float).reshape(-1)
        out = base * np.exp(-np.asarray(self.shift(t)) * t)
        return out.reshape(()) if scalar else out

    def zero_rate(self, t, basis: str = "continuous") -> np.ndarray:
        if basis != "continuous":
            raise ValueError("a shifted curve gives continuous zero rates only")
        arr = np.asarray(t, dtype=float)
        return np.asarray(self.base.zero_rate(arr, "continuous"), dtype=float) + np.asarray(self.shift(arr)) * 100


def _flows(bond, s: date) -> tuple[np.ndarray, np.ndarray]:
    """Act/365F times from ``s`` and amounts (per 100) of a ``bonds`` instrument's scheduled flows after ``s``."""
    if isinstance(bond, DiscountInstrument):
        bond.days_to_maturity(s)
        return np.array([(bond.maturity - s).days / _YEAR]), np.array([100.0])
    flows = bond.cash_flows(s)
    return (np.array([(f.date - s).days / _YEAR for f in flows], dtype=float),
            np.array([f.amount for f in flows], dtype=float))


def model_yield(curve, bond, settlement, *, flows: tuple[np.ndarray, np.ndarray] | None = None) -> float:
    """``bond``'s yield (percent, its own conventions in ``bonds``) at its price on ``curve`` (A2.2's ``m(C)``).

    The curve's valuation date must be ``settlement``: the flows from ``bonds`` are discounted at Act/365F times from
    it on their scheduled dates, as ``curvefit`` fits them. ``flows`` (times and amounts from ``bonds`` for this
    settlement) saves recomputing them when one bond is priced on several curves.
    """
    s = as_date(settlement)
    if curve.valuation_date != s:
        raise ValueError(f"the curve is valued on {curve.valuation_date}, not on the settlement date {s}")
    t, amounts = flows if flows is not None else _flows(bond, s)
    dirty = float(amounts @ np.asarray(curve.discount_t(t), dtype=float))
    if not (math.isfinite(dirty) and dirty > 0):
        raise ValueError(f"the curve gives no price for a bond maturing {bond.maturity} on {s} (beyond its range?)")
    if isinstance(bond, DiscountInstrument):
        return bond.yield_from_price(dirty, s)
    return bond.yield_from_dirty_price(dirty, s)


def _x_of(t, nodes: Sequence[float]) -> np.ndarray:
    lo, hi = math.log(nodes[0]), math.log(nodes[-1])
    clipped = np.clip(np.asarray(t, dtype=float), nodes[0], nodes[-1])
    return 2 * (np.log(clipped) - lo) / (hi - lo) - 1


def factor_shapes(basis: str = "legendre3", nodes: Sequence[float] = DEFAULT_NODES) -> dict[str, Callable]:
    """The shapes of the curve factors as functions of years (A2.3): Legendre polynomials ``1``, ``x`` and ``(3x^2 -
    1)/2`` of ``x = 2 (ln T - ln T_0) / (ln T_n - ln T_0) - 1`` (``T`` clipped to the nodes), or key-rate triangles
    (linear interpolation between the nodes, flat beyond them). Keys are the line suffixes (``parallel``, ...,
    ``1y``, ...)."""
    nodes = tuple(float(n) for n in nodes)
    if basis == "legendre3":
        return {"parallel": lambda t: np.ones_like(np.asarray(t, dtype=float)),
                "slope": lambda t: _x_of(t, nodes),
                "curvature": lambda t: 1.5 * _x_of(t, nodes) ** 2 - 0.5}
    if basis == "key_rates":
        out = {}
        for k in range(len(nodes)):
            unit = np.zeros(len(nodes))
            unit[k] = 1.0
            out[_tenor_label(nodes[k])] = (lambda t, u=unit: np.interp(np.asarray(t, dtype=float), nodes, u))
        return out
    raise ValueError(f"factor basis must be one of {FACTOR_BASES}, not {basis!r}")


def factor_moves(curve_today, curve_rolled, *, nodes: Sequence[float] = DEFAULT_NODES,
                 basis: str = "legendre3", report: dict | None = None) -> dict[str, float]:
    """Factor sizes in bp of continuous zero rate between ``curve_rolled`` and ``curve_today`` (both seen from the
    same date): least squares of the node changes ``dz_k = z_today(T_k) - z_rolled(T_k)`` on the shapes (Legendre),
    or the node changes themselves (key rates). Nodes where either curve has no rate (beyond a money-market OIS curve
    that ends at one or two years) are left out. With fewer usable nodes than factors only the leading shapes the
    nodes can fix are fitted, the others set to 0 so that the rest of the move falls in ``curve_other``: parallel
    alone on one node, parallel and slope on two, nothing on none (UNVERIFIED choice, as the basis itself).
    ``report``, if given, receives ``usable_nodes`` and ``fitted`` (the number of shapes fitted)."""
    nodes = tuple(float(n) for n in nodes)
    t = np.array(nodes)
    dz = (np.asarray(curve_today.zero_rate(t, "continuous"), dtype=float)
          - np.asarray(curve_rolled.zero_rate(t, "continuous"), dtype=float)) * 100
    shapes = factor_shapes(basis, nodes)
    ok = np.isfinite(dz)
    usable = int(ok.sum())
    if basis == "key_rates":
        if report is not None:
            report.update(usable_nodes=usable, fitted=usable)
        return {name: (float(dz[k]) if ok[k] else 0.0) for k, name in enumerate(shapes)}
    names = list(shapes)
    fitted = names[:min(len(names), usable)]
    if report is not None:
        report.update(usable_nodes=usable, fitted=len(fitted))
    out = dict.fromkeys(names, 0.0)
    if fitted:
        design = np.column_stack([shapes[name](t[ok]) for name in fitted])
        coef, *_ = np.linalg.lstsq(design, dz[ok], rcond=None)
        out.update({name: float(c) for name, c in zip(fitted, coef)})
    return out


def _shift_function(sizes: Mapping[str, float], shapes: Mapping[str, Callable]) -> Callable:
    def shift(t):
        arr = np.asarray(t, dtype=float)
        total = np.zeros_like(arr)
        for name, size in sizes.items():
            if size:
                total = total + size / 1e4 * shapes[name](arr)
        return total
    return shift


def factor_curves(curve_rolled, sizes: Mapping[str, float], *, nodes: Sequence[float] = DEFAULT_NODES,
                  basis: str = "legendre3") -> dict[str, ShiftedCurve]:
    """One shifted curve per factor: ``curve_rolled`` moved by that factor alone (A2.3's ``shift(R, a_j f_j)``)."""
    shapes = factor_shapes(basis, nodes)
    return {name: ShiftedCurve(curve_rolled, _shift_function({name: size}, shapes)) for name, size in sizes.items()}


@dataclass(frozen=True)
class CurveEntry:
    """A curve of a mark date with the time from which it was public (``None``: the marks' default time)."""

    curve: Any
    public_from: datetime | None = None


def read_nss_params(table, *, calendar: BusinessCalendar | None = None) -> dict[str, dict[date, CurveEntry]]:
    """NSS curves from a table: one row per mark ``date`` (and curve name): ``beta0`` ... ``tau2``, ``t_min``,
    ``t_max``, optional ``settlement_date`` (default the T+1 settlement of the date on ``calendar``), ``curve``
    (default ``gsec``) and ``public_from``. Returns ``{name: {date: CurveEntry}}``; the curve's valuation date is the
    settlement date, as ``curvefit.fit_curve`` makes it."""
    frame = _frame(table, "the NSS parameters")
    need = ("date", "beta0", "beta1", "beta2", "beta3", "tau1", "tau2")
    missing = [c for c in need if c not in frame]
    if missing:
        raise ValueError(f"the NSS parameter table lacks {missing} (columns: date, settlement_date, beta0..beta3, "
                         "tau1, tau2, t_min, t_max, curve, public_from)")
    cal = calendar if calendar is not None else mumbai_settlement_calendar()
    out: dict[str, dict[date, CurveEntry]] = {}
    for n, rec in _lines(frame):
        where = f"NSS parameter line {n}"
        day = _date(rec["date"], f"{where}: date")
        settle = _opt_date(rec.get("settlement_date"), f"{where}: settlement_date") or cal.settlement_date(day, 1)
        params = tuple(_num(rec[c], f"{where}: {c}") for c in ("beta0", "beta1", "beta2", "beta3", "tau1", "tau2"))
        t_min = _num(rec.get("t_min"), f"{where}: t_min", blank=0.0)
        t_max = _num(rec.get("t_max"), f"{where}: t_max", blank=50.0)
        name = _text(rec.get("curve")) or "gsec"
        try:
            curve = NssCurve(valuation_date=settle, t_min=t_min, t_max=t_max, method="nss", params=params,
                             curve_id=f"{name}:{day}")
        except ValueError as exc:
            raise ValueError(f"{where}: {exc}") from None
        book = out.setdefault(name, {})
        if day in book:
            raise ValueError(f"{where}: two curves {name!r} for {day}")
        book[day] = CurveEntry(curve, _time_of(rec.get("public_from"), f"{where}: public_from"))
    return out


NSS_PARAMS_COLUMNS = ("date", "settlement_date", "beta0", "beta1", "beta2", "beta3", "tau1", "tau2", "t_min", "t_max",
                      "curve", "public_from")


def nss_params_row(curve: NssCurve, day, *, name: str = "gsec", public_from=None) -> dict:
    """One row of ``read_nss_params``' table (``NSS_PARAMS_COLUMNS``) from a fitted NSS curve (``curvefit.fit_curve``'s
    ``FitResult.curve``, valued on the settlement date) and the mark ``day`` its prices are for. ``public_from``: when
    the fit was public (blank: the marks' default time on ``day``); never earlier than the latest ``public_from`` of
    the prices fitted."""
    if not isinstance(curve, NssCurve):
        raise TypeError(f"an NSS parameter row needs an NssCurve (fit with method='nss'), not {type(curve).__name__}")
    d = as_date(day)
    if d > curve.valuation_date:
        raise ValueError(f"the mark date {d} is after the curve's settlement date {curve.valuation_date}")
    row = {"date": d.isoformat(), "settlement_date": curve.valuation_date.isoformat(),
           **{k: repr(float(v)) for k, v in zip(("beta0", "beta1", "beta2", "beta3", "tau1", "tau2"), curve.params)},
           "t_min": repr(float(curve.t_min)), "t_max": repr(float(curve.t_max)), "curve": str(name).strip() or "gsec",
           "public_from": "" if public_from is None else as_time(public_from, "public_from").isoformat()}
    return row


def append_nss_params(table, row: Mapping) -> pd.DataFrame:
    """``table`` (a ``read_nss_params`` table, a CSV path that may not exist yet, or None) with ``row`` added, a row of
    the same date and curve replaced (a refit of the day), sorted by curve and date."""
    from pathlib import Path
    if table is None or (not isinstance(table, pd.DataFrame) and not Path(table).exists()):
        frame = pd.DataFrame(columns=list(NSS_PARAMS_COLUMNS))
    else:
        frame = _frame(table, "the NSS parameters")
    for column in NSS_PARAMS_COLUMNS:
        if column not in frame:
            frame[column] = ""
    frame = frame.astype(str)
    keep = ~((frame["date"] == row["date"]) & (frame["curve"].replace("", "gsec") == row["curve"]))
    out = pd.concat([frame[keep], pd.DataFrame([row])], ignore_index=True).loc[:, list(NSS_PARAMS_COLUMNS)]
    return out.sort_values(["curve", "date"], kind="mergesort").reset_index(drop=True)


# ---------------------------------------------------------------- pricing through bonds


def _accrued(bond, d: date) -> float:
    return 0.0 if isinstance(bond, DiscountInstrument) else bond.accrued_interest(d)


def _clean(bond, y: float, s: date) -> float:
    if isinstance(bond, DiscountInstrument):
        return bond.price(y, s, rounded=False)
    return bond.clean_price(y, s)


def _yield(bond, clean: float, s: date) -> float:
    if isinstance(bond, DiscountInstrument):
        return bond.yield_from_price(clean, s)
    return bond.yield_from_price(clean, s)


def _dirty(bond, y: float, s: date) -> float:
    if isinstance(bond, DiscountInstrument):
        return bond.price(y, s, rounded=False)
    return bond.dirty_price(y, s)


def _risk(bond, y: float, s: date) -> tuple[float, float, float]:
    """(DV01 per 100 per bp, convexity, dirty price) at yield ``y`` for settlement ``s``."""
    return bond.dv01(y, s), bond.convexity(y, s), _dirty(bond, y, s)


@dataclass(frozen=True)
class Instrument:
    """A priced security of the book: its ``bonds`` object without a calendar (``bond``, for every price and yield)
    and the same with the engine's calendar (``paying``, only for payment dates near the day asked).

    A corporate bond or an FRB has ``known``: the bond as known at a time (coupons fixed or announced by then,
    exercises and rating steps public by then); ``bond`` and ``paying`` are then the bond as known at the current
    day's valuation time, ``version`` counts its changes, ``maturity`` is the effective one (a call exercised) and
    ``rating_bucket`` the terms table's, for the costs. A corporate bond keeps its own lenient payment calendar.
    """

    isin: str
    kind: str
    bond: Any
    paying: Any
    maturity: date
    issue_date: date | None
    conventions: Any
    liquidity: str | None = None
    known: Callable[[datetime], Any] | None = None
    version: int = 0
    rating_bucket: str | None = None

    @property
    def lag(self) -> int:
        return self.conventions.settlement_lag

    @property
    def last_settlement(self) -> date:
        """The last settlement date that receives the redemption: a corporate bond's redemption record date (trading
        stops after it), else the day before maturity."""
        if isinstance(self.bond, CorporateBond):
            return self.bond.last_cum_settlement()
        return self.maturity - timedelta(days=1)

    def redemption_payment(self) -> date:
        if isinstance(self.paying, DiscountInstrument):
            return self.paying.payment_date()
        if isinstance(self.paying, CorporateBond):
            return self.paying.periods()[-1].payment
        return [f for f in self.paying.cash_flows(self._before_maturity()) if f.kind == "redemption"][0].payment_date

    def redemption_amount(self) -> float:
        """Per 100 face: the redemption plus the last coupon (a bill: 100)."""
        if isinstance(self.bond, DiscountInstrument):
            return 100.0
        if isinstance(self.bond, CorporateBond):
            last = self.bond.periods()[-1]
            return last.amount + last.principal
        s = self._before_maturity()
        last = [f for f in self.bond.cash_flows(s) if f.date == self.maturity]
        return sum(f.amount for f in last)

    def _before_maturity(self) -> date:
        return self.maturity - timedelta(days=1)


def make_instrument(isin: str, terms: Mapping, calendar: BusinessCalendar, where: str) -> Instrument:
    """An ``Instrument`` from terms (``kind``, ``coupon_pct``, ``maturity_date``, ``issue_date``,
    ``first_coupon_date``): G-secs, green bonds, special securities and SDLs as ``bonds.FixedRateBond``, STRIPS as
    ``ZeroCouponBond``, T-bills, CMBs, CP and CD as ``DiscountInstrument``. Inflation-indexed bonds have no pricer and
    are refused; corporate bonds and FRBs are refused here too: their terms tables build them (``MarketData``'s
    ``corporate_terms`` and ``frb_terms``)."""
    kind = _text(terms.get("kind")).lower()
    if kind in NO_PRICER_KINDS:
        raise ValueError(f"{where}: {isin} is a {kind}, which has no pricer in bonds.py (inflation-indexed bonds are "
                         "not built)")
    if kind in TERMS_KINDS:
        table = "corporate_terms (corporates.read_corporate_terms)" if kind == "corporate" else (
            "frb_terms (floating.load_frb_terms; the shipped table holds the five outstanding GoI FRBs)")
        raise ValueError(f"{where}: {isin} is a {kind}, which has no pricer in bonds.py: its terms come from "
                         f"MarketData.{table}, which does not list it")
    if kind not in PRICED_KINDS:
        raise ValueError(f"{where}: {isin}: kind {terms.get('kind')!r} is not one of {sorted(PRICED_KINDS)}")
    maturity = _opt_date(terms.get("maturity_date"), f"{where}: maturity_date")
    if maturity is None:
        raise ValueError(f"{where}: {isin} has no maturity_date (give it in the trade or the security master)")
    issue = _opt_date(terms.get("issue_date"), f"{where}: issue_date")
    conv = CONVENTIONS[_KIND_CONVENTIONS[kind]]
    try:
        if kind in COUPON_KINDS:
            coupon = _opt_num(terms.get("coupon_pct"), f"{where}: coupon_pct")
            if coupon is None:
                raise ValueError(f"{where}: {isin}, a {kind}, needs coupon_pct")
            bond = FixedRateBond(coupon, maturity, conv, issue_date=issue,
                                 first_coupon_date=_opt_date(terms.get("first_coupon_date"),
                                                             f"{where}: first_coupon_date"),
                                 calendar=calendar, name=isin)
        elif kind == "strips":
            bond = ZeroCouponBond(maturity, conv, issue_date=issue, calendar=calendar, name=isin)
        else:
            bond = DiscountInstrument(maturity, conv, issue_date=issue, calendar=calendar, name=isin)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{where}: {isin}: {exc}") from None
    liquidity = _text(terms.get("liquidity")).lower().replace("-", "_") or None
    return Instrument(isin=isin, kind=kind, bond=replace(bond, calendar=None), paying=bond, maturity=maturity,
                      issue_date=issue, conventions=conv, liquidity=liquidity)


# ---------------------------------------------------------------- attribution primitives


@dataclass(frozen=True)
class MarkState:
    """A bond's mark on one day: the clean price (bills: the price) for ``settlement``, its yield and source, and the
    reference curve seen from ``settlement`` with the bond's model yield on it (``None`` and NaN without a curve).
    ``raw_curve`` is the reference curve as given, on its own valuation date (a G-sec curve's is the settlement date,
    an OIS curve's the trade date); ``None``: ``curve`` itself."""

    date: date
    settlement: date
    clean: float
    yield_pct: float
    source: str = "traded"
    curve: Any = None
    model_yield_pct: float = math.nan
    age_days: int = 0
    observed: date | None = None
    raw_curve: Any = None

    @property
    def residual_pct(self) -> float:
        return self.yield_pct - self.model_yield_pct

    @property
    def base_curve(self) -> Any:
        return self.raw_curve if self.raw_curve is not None else self.curve


def roll_reference(prev: MarkState, today: MarkState, roll_basis: str = "constant_curve"):
    """The previous day's reference curve carried to today's mark (A2.3), seen from today's settlement.

    ``constant_curve``: the previous curve as given moved to the valuation date of today's curve at unchanged zero
    rates by time (``rolled``), then seen from today's settlement (``seen_from``). A G-sec curve is valued on its
    settlement date, so this is the settlement-to-settlement roll; an OIS curve is valued on its trade date, so a CP
    or CD against it rolls as the OIS lines do, trade date to trade date, and a curve rolled at constant zero rates
    from one trade date to the next gives no curve lines over a weekend. ``forwards``: the previous curve seen from
    today's settlement, its forward rates realised (no roll-down)."""
    base = prev.base_curve
    if roll_basis == "constant_curve":
        target = today.base_curve.valuation_date if today.base_curve is not None else today.settlement
        target = min(as_date(target), today.settlement)
        if target < base.valuation_date:
            target = base.valuation_date
        return seen_from(rolled(base, target), today.settlement)
    return seen_from(base, today.settlement)


def mark_state(bond, day, settlement, *, clean: float | None = None, yield_pct: float | None = None,
               source: str = "traded", curve=None) -> MarkState:
    """A ``MarkState`` from a clean price or a yield for ``settlement``, with the model yield on ``curve`` if given."""
    d, s = as_date(day), as_date(settlement)
    if (clean is None) == (yield_pct is None):
        raise ValueError("give exactly one of clean and yield_pct")
    if clean is not None:
        y = _yield(bond, clean, s)
        c = float(clean)
    else:
        y = float(yield_pct)
        c = _clean(bond, y, s)
    m = model_yield(curve, bond, s) if curve is not None else math.nan
    return MarkState(d, s, c, y, source, curve, m)


def _yield_moves(bond, prev: MarkState, today: MarkState, settings: AttributionSettings,
                 flows: tuple[np.ndarray, np.ndarray] | None = None, sizes: dict | None = None) -> dict[str, Any]:
    """The split of ``today.yield_pct - prev.yield_pct`` (percent) into roll, factor, other and residual moves.
    ``flows``: the bond's flows for today's settlement; ``sizes``: the factor sizes when already known."""
    out: dict[str, Any] = {"have_curve": False, "dy": today.yield_pct - prev.yield_pct, "factors": {}}
    if prev.curve is None or today.curve is None or math.isnan(prev.model_yield_pct) \
            or math.isnan(today.model_yield_pct):
        return out
    roll = roll_reference(prev, today, settings.roll_basis)
    flows = flows if flows is not None else _flows(bond, today.settlement)
    m_roll = model_yield(roll, bond, today.settlement, flows=flows)
    if sizes is None:
        sizes = factor_moves(today.curve, roll, nodes=settings.nodes, basis=settings.factor_basis)
    factor_dy = {}
    for name, curve in factor_curves(roll, sizes, nodes=settings.nodes, basis=settings.factor_basis).items():
        factor_dy[name] = (model_yield(curve, bond, today.settlement, flows=flows) - m_roll) if sizes[name] else 0.0
    dy_curve = today.model_yield_pct - m_roll
    out.update(have_curve=True, dy_roll=m_roll - prev.model_yield_pct, dy_curve=dy_curve, factors=factor_dy,
               factor_sizes=sizes, dy_other=dy_curve - sum(factor_dy.values()),
               de=today.residual_pct - prev.residual_pct, m_roll=m_roll)
    return out


def _market_lines(units: float, y0: float, y3: float, clean_prev: float, risk: tuple[float, float, float],
                  clean0_at_v: float, clean3_at_v: float, moves: Mapping[str, Any],
                  settings: AttributionSettings) -> dict[str, float]:
    """Lines of one position from the carried state: pull to par, first-order moves, convexity, unexplained.

    ``units`` = F/100 (signed). ``clean_prev``: its clean value at the previous close; ``clean0_at_v`` and
    ``clean3_at_v`` the clean prices at the previous and today's yields for today's valuation settlement; ``risk`` the
    DV01, convexity and dirty price at ``(y0, v)``.
    """
    dv01, cx, p0 = risk
    out = {"pull_to_par": units * (clean0_at_v - clean_prev)}
    move = units * (clean3_at_v - clean0_at_v)
    scale = -units * dv01 * 100  # rupees per percent of yield
    first = 0.0
    if moves["have_curve"]:
        out["roll_down"] = scale * moves["dy_roll"]
        names = settings.curve_lines[:-1]
        for line, factor in zip(names, moves["factors"]):
            out[line] = scale * moves["factors"][factor]
        out["curve_other"] = scale * moves["dy_other"]
        out["rich_cheap"] = scale * moves["de"]
        first = out["roll_down"] + sum(out[n] for n in names) + out["curve_other"] + out["rich_cheap"]
    else:
        out["price_unattributed"] = scale * (y3 - y0)
        first = out["price_unattributed"]
    out["convexity"] = units * 0.5 * cx * p0 * ((y3 - y0) / 100) ** 2
    out["unexplained"] = move - first - out["convexity"]
    out["_move"] = move
    return out


def attribute_bond(bond, face: float, prev: MarkState, today: MarkState, *,
                   settings: AttributionSettings | None = None, coupons: float = 0.0) -> dict[str, float]:
    """The lines (rupees) of ``face`` rupees of ``bond`` held from the close of ``prev.date`` to that of ``today.date``.

    ``prev.clean`` and ``today.clean`` are the marks for their settlement dates; accrual runs to the mark dates
    (``AI(today.date) - AI(prev.date)`` plus ``coupons`` entitled in between, per 100). The lines are A2.2's:
    ``accrual``, ``pull_to_par``, ``roll_down``, the curve lines, ``rich_cheap`` (or ``price_unattributed`` without
    both curves), ``convexity`` and ``unexplained``; they sum to ``V_today - V_prev`` plus the coupons exactly, where
    ``V = F/100 (clean + AI(mark date))``.
    """
    st = settings if settings is not None else AttributionSettings()
    units = float(face) / 100
    moves = _yield_moves(bond, prev, today, st)
    v = today.settlement
    risk = _risk(bond, prev.yield_pct, v)
    lines = _market_lines(units, prev.yield_pct, today.yield_pct, prev.clean, risk,
                          _clean(bond, prev.yield_pct, v), today.clean, moves, st)
    lines.pop("_move")
    out = {"accrual": units * (_accrued(bond, today.date) - _accrued(bond, prev.date) + coupons)}
    out.update(lines)
    return out


def attribute_ois(swap: OisSwap, curve_prev, curve_today, *, fixings=None, calendar: BusinessCalendar | None = None,
                  settings: AttributionSettings | None = None, settled: float = 0.0,
                  settled_unrounded: float = 0.0) -> dict[str, float]:
    """The lines (rupees) of ``swap`` from ``curve_prev``'s valuation date to ``curve_today``'s (A2.7).

    ``settled`` is the rounded net cash of a period paid in between (positive: received), ``settled_unrounded`` its
    unrounded accrual. Lines: ``ois_carry`` (accrued floating less fixed, for the swap's side), ``ois_roll_down``
    (the rest of the move on the curve rolled at constant zero rates), one line per curve factor (revalued from the
    rolled curve), ``ois_curve_other`` and ``ois_settlement_rounding``; they sum to ``N_today - N_prev + settled``.
    """
    st = settings if settings is not None else AttributionSettings()
    v0 = ois_npv(curve_prev, swap, fixings=fixings, calendar=calendar)
    day = curve_today.valuation_date
    alive = ois_schedule(swap.effective, swap.months, swap.conventions, calendar)[-1].end > day
    v1 = ois_npv(curve_today, swap, fixings=fixings, calendar=calendar) if alive else None
    n1 = v1.npv if v1 is not None else 0.0
    sign = 1.0 if swap.pay_fixed else -1.0
    acc1 = (v1.accrued_float - v1.accrued_fixed) if v1 is not None else 0.0
    carry = sign * (acc1 - (v0.accrued_float - v0.accrued_fixed)) + settled_unrounded
    lines = {"ois_carry": carry}
    if v1 is None:
        lines["ois_roll_down"] = n1 - v0.npv + settled_unrounded - carry
        for line in st.ois_curve_lines:
            lines[line] = 0.0
    else:
        roll_curve = rolled(curve_prev, day) if st.roll_basis == "constant_curve" else seen_from(curve_prev, day)
        n_roll = ois_npv(roll_curve, swap, fixings=fixings, calendar=calendar).npv
        lines["ois_roll_down"] = n_roll - v0.npv + settled_unrounded - carry
        sizes = factor_moves(curve_today, roll_curve, nodes=st.nodes, basis=st.factor_basis)
        names = st.ois_curve_lines[:-1]
        shifted = factor_curves(roll_curve, sizes, nodes=st.nodes, basis=st.factor_basis)
        total = 0.0
        for line, factor in zip(names, sizes):
            value = (ois_npv(shifted[factor], swap, fixings=fixings, calendar=calendar).npv - n_roll
                     if sizes[factor] else 0.0)
            lines[line] = value
            total += value
        lines["ois_curve_other"] = n1 - n_roll - total
    lines["ois_settlement_rounding"] = settled - settled_unrounded
    return lines


# ---------------------------------------------------------------- inputs


def _table(table, columns: Sequence[str], required: Sequence[str], what: str) -> pd.DataFrame:
    """``table`` (a DataFrame or CSV path, ``None`` for none) with ``columns`` (missing optional ones blank). Rows whose
    every cell is blank (a spreadsheet's trailing ``,,,,`` lines) are dropped; line numbers in later messages still
    count them (the CSV's own line numbers, header line 1)."""
    if table is None:
        return pd.DataFrame(columns=list(columns))
    frame = _frame(table, what)
    missing = [c for c in required if c not in frame]
    if missing:
        raise ValueError(f"{what} lacks {missing} (columns: {', '.join(columns)})")
    lines = list(range(2, len(frame) + 2))
    if len(frame):
        blank = frame.apply(lambda row: all(_blank(v) for v in row), axis=1).to_numpy()
        if blank.any():
            lines = [n for n, gone in zip(lines, blank) if not gone]
            frame = frame[~blank].reset_index(drop=True)
    for column in columns:
        if column not in frame:
            frame[column] = ""
    frame.attrs["lines"] = lines
    return frame


def _records(frame: pd.DataFrame) -> Iterable[tuple[int, dict]]:
    """``(line, row)`` pairs of a table read by ``_table``: the CSV's own line numbers (header line 1), blank lines
    dropped by ``_table`` still counted."""
    lines = frame.attrs.get("lines") or list(range(2, len(frame) + 2))
    return zip(lines, frame.to_dict("records"))


def _lines(frame: pd.DataFrame) -> Iterable[tuple[int, dict]]:
    """``(line, row)`` pairs of a reference table read by ``_frame`` (the CSV's own line numbers, header line 1), rows
    whose every cell is blank (a spreadsheet's trailing ``,,,`` lines) skipped, as ``_table`` drops them."""
    for n, rec in enumerate(frame.to_dict("records"), start=2):
        if not all(_blank(v) for v in rec.values()):
            yield n, rec


def read_trades(table) -> pd.DataFrame:
    """A bond trade file (``TRADE_COLUMNS``; ``trade_id``, ``book``, ``isin``, ``side``, ``face``, ``trade_date``
    required; a blank ``price`` and ``yield_pct`` mean a fill at the day's traded mid plus or minus the half spread)."""
    return _table(table, TRADE_COLUMNS, ("trade_id", "book", "isin", "side", "face", "trade_date"), "the trades")


def read_swaps(table) -> pd.DataFrame:
    """An OIS trade file (``SWAP_TRADE_COLUMNS``; ``trade_id``, ``book``, ``trade_date``, ``months``, ``notional``,
    ``pay_fixed`` required; a blank ``fixed_rate_pct`` means the day's curve par rate plus or minus the half spread;
    ``effective`` defaults to T+1)."""
    return _table(table, SWAP_TRADE_COLUMNS, ("trade_id", "book", "trade_date", "months", "notional", "pay_fixed"),
                  "the OIS trades")


def read_flows(table) -> pd.DataFrame:
    """Capital flows (``FLOW_COLUMNS``): rupees into (positive) or out of a book at the start of a business day."""
    return _table(table, FLOW_COLUMNS, FLOW_COLUMNS, "the flows")


def read_marks(table) -> pd.DataFrame:
    """A marks file (``MARK_COLUMNS``; ``date``, ``isin`` and a ``clean_price`` or ``yield_pct`` (or, for a traded
    row, a ``vwap`` or ``last_price``) required; ``source`` defaults to ``traded``)."""
    frame = _table(table, MARK_COLUMNS, ("date", "isin"), "the marks")
    if not any(c in frame for c in ("clean_price", "yield_pct")):
        raise ValueError("the marks need a clean_price or a yield_pct column")
    return frame


def marks_from_prices(frame: pd.DataFrame, day, *, source: str = "traded", public_from=None) -> pd.DataFrame:
    """Marks (``MARK_COLUMNS``) from one day's generic price file, the layout ``curvefit.prepare_prices`` reads
    (``isin`` and ``clean_price`` or ``yield_pct``, optional ``volume_cr``, ``trade_count``, ``traded``): how a loader
    of CCIL's trades or FBIL's valuation prices plugs in. Rows flagged ``traded = false`` become ``valuation`` marks."""
    prices = _frame(frame, "the price file")
    if "isin" not in prices or not any(c in prices for c in ("clean_price", "yield_pct")):
        raise ValueError("the price file needs isin and clean_price or yield_pct")
    d = as_date(day)
    rows = []
    for rec in prices.to_dict("records"):
        traded = _bool(rec.get("traded"), f"price row {rec.get('isin')}: traded", True)
        rows.append({"date": d, "isin": _text(rec.get("isin")), "clean_price": rec.get("clean_price", ""),
                     "yield_pct": rec.get("yield_pct", ""), "source": source if traded else "valuation",
                     "settlement_date": rec.get("settlement_date", ""), "vwap": "", "last_price": "",
                     "volume_cr": rec.get("volume_cr", ""), "trade_count": rec.get("trade_count", ""),
                     "public_from": "" if public_from is None else as_time(public_from, "public_from")})
    return pd.DataFrame(rows, columns=list(MARK_COLUMNS))


@dataclass
class MarketData:
    """What a run values with, each table with the time from which its rows were public.

    ``marks`` (``MARK_COLUMNS``), ``curves`` (``{name: {mark date: curve or CurveEntry}}``, each curve for the
    standard settlement of its date, e.g. ``read_nss_params``), ``ois_curves`` (``{trade date: OisCurve or
    CurveEntry}``, valued on that date as ``ois.bootstrap_ois`` makes them), ``fixings`` (overnight MIBOR,
    ``{date: percent}``), ``funding`` (``{source: funding.funding_rates frame}``, from the RBI store's
    ``money_market_daily``), ``haircuts`` (the user's dated table, ``funding.read_haircuts``), ``liquidity`` (CCIL
    classes kept by the user: ``effective_date``, ``isin``, ``liquidity``), ``master`` (``sovereign.read_master``:
    terms by ISIN), ``residual_history`` (``curvefit.append_history``'s panel, for model marks), ``stock``
    (``isin``, ``date``, ``outstanding_cr``: for the short-sale limits in percent of the stock) and ``auctions``
    (``isin``, ``auction_date``, ``settlement_date``, ``cutoff_price``, ``wap``, and for a back-test's orders
    ``notified_cr``, ``accepted_cr``, ``announcement_public_from``, ``result_public_from`` and ``line``, the line's
    name for a bid before its ISIN is public: ``auctions_from_events`` builds it from the event ledger). For a
    back-test's ``Snapshot`` only: ``events`` (an event ledger, ``event_ledger``'s frame),
    ``store`` (an RBI store directory, for ``Snapshot.store_table``), ``macro_store`` (a macro store directory, for
    ``Snapshot.macro``) and ``mpc_meetings`` (a meetings file for ``policy_path.load_mpc_calendar``; default the
    shipped one). Only ``marks`` and, for a book that borrows, ``funding`` and ``haircuts`` are needed.

    Corporate bonds and FRBs are priced from their own terms (each table a frame or CSV): ``corporate_terms``
    (``corporates.read_corporate_terms``; an ISIN in it is a corporate bond), with its ``corporate_schedule``,
    ``corporate_options`` (calls and puts, each exercise counted from its ``exercised_public_from``) and
    ``corporate_amortisation``; ``corporate_steps`` (``corporates.read_step_rules``: rating-linked coupon steps) with
    ``ratings`` (``agency``, ``rating``, ``public_from`` and ``isin`` or ``issuer_id``: each action used only from its
    time); ``corporate_floating`` (``floating.read_floating_terms``: a ``frb_corp`` bond's benchmark terms) with
    ``benchmark_fixings`` (``floating.read_fixings``, each fixing with its ``public_from``). GoI FRBs: ``frb_terms``
    (default the shipped ``goi_frb_terms.csv``), ``frb_coupons`` (announced coupons, default the shipped table;
    ``None`` for none) and ``tbills`` (182-day T-bill results, ``floating.tbill_way_table`` or its input: each with
    its ``public_from``). Every day the bond is rebuilt as known at the valuation time.
    """

    marks: pd.DataFrame | None = None
    curves: Mapping[str, Mapping[date, Any]] = field(default_factory=dict)
    ois_curves: Mapping[date, Any] = field(default_factory=dict)
    fixings: Mapping[date, float] = field(default_factory=dict)
    fixing_times: Mapping[date, datetime | None] | None = None
    funding: Mapping[str, pd.DataFrame] = field(default_factory=dict)
    haircuts: pd.DataFrame | None = None
    liquidity: pd.DataFrame | None = None
    master: pd.DataFrame | None = None
    residual_history: pd.DataFrame | None = None
    stock: pd.DataFrame | None = None
    auctions: pd.DataFrame | None = None
    events: pd.DataFrame | None = None
    store: Any = None
    macro_store: Any = None
    mpc_meetings: Any = None
    corporate_terms: Any = None
    corporate_schedule: Any = None
    corporate_options: Any = None
    corporate_amortisation: Any = None
    corporate_steps: Any = None
    ratings: Any = None
    corporate_floating: Any = None
    benchmark_fixings: Any = None
    frb_terms: Any = None
    frb_coupons: Any = FRB_COUPONS_CSV
    tbills: Any = None


AUCTION_COLUMNS = ("isin", "auction_date", "settlement_date", "cutoff_price", "wap", "notified_cr", "accepted_cr",
                   "announcement_public_from", "result_public_from", "kind", "line")
# a security's fixed terms, known from its announcement (a new issue's coupon from its result): what Snapshot.master
# shows; every other column of a master (the stock, RBI's lists, FAR, the CCIL class, sources) is its state when the
# master was built, not point in time
MASTER_FIXED_COLUMNS = ("isin", "name", "name_key", "kind", "coupon_pct", "maturity_year", "maturity_date",
                        "issue_date", "first_coupon_date", "state_code", "isin_fy", "isin_fy_matches")


def _line_name(event_id) -> str:
    """An auction line's name: the key part of its event id (``gsec_auction:2026-05-08:new GS 2036`` gives ``new GS
    2036``), without the ``#2`` of a numbered copy, so a line read twice stays one line."""
    text = _text(event_id)
    return re.sub(r"#\d+$", "", text.split(":", 2)[2]) if text.count(":") >= 2 else ""


def auctions_from_events(events: pd.DataFrame) -> pd.DataFrame:
    """``MarketData.auctions`` (``AUCTION_COLUMNS``) from an event ledger: one row per auction line with an ISIN, its
    settlement, cut-off price, weighted average price, notified and accepted amounts, the times from which its
    announcement and result were public (estimated times included, as the ledger flags them) and its ``line``, the key
    part of its ``event_id`` (coupon-free for a new issue, ``new GS 2036``): the name a back-test bids by while the
    line's ISIN is not yet public. Lines without an ISIN or a cut-off price are left out: a back-test cannot bid in
    them."""
    need = ("kind", "isin", "event_date", "settlement_date", "cutoff_price", "wap", "notified_cr", "accepted_cr",
            "announcement_public_from", "result_public_from", "supply_kind")
    missing = [c for c in need if c not in events]
    if missing:
        raise ValueError(f"the event ledger lacks {missing}")
    rows = []
    for rec in events.to_dict("records"):
        if not str(rec["kind"]).endswith("_auction") or _blank(rec["isin"]) or _blank(rec["cutoff_price"]):
            continue
        rows.append({"isin": _text(rec["isin"]).upper(), "auction_date": as_date(rec["event_date"]),
                     "settlement_date": as_date(rec["settlement_date"]), "cutoff_price": float(rec["cutoff_price"]),
                     "wap": None if _blank(rec["wap"]) else float(rec["wap"]),
                     "notified_cr": None if _blank(rec["notified_cr"]) else float(rec["notified_cr"]),
                     "accepted_cr": None if _blank(rec["accepted_cr"]) else float(rec["accepted_cr"]),
                     "announcement_public_from": None if _blank(rec["announcement_public_from"]) else as_time(
                         rec["announcement_public_from"], "announcement_public_from"),
                     "result_public_from": None if _blank(rec["result_public_from"]) else as_time(
                         rec["result_public_from"], "result_public_from"),
                     "kind": _text(rec["supply_kind"]), "line": _line_name(rec.get("event_id"))})
    out = pd.DataFrame(rows, columns=list(AUCTION_COLUMNS))
    return out.sort_values(["auction_date", "isin"], kind="mergesort").reset_index(drop=True)


def funding_from_store(store, sources: Iterable[str] = ("triparty", "market_repo")) -> dict[str, pd.DataFrame]:
    """``funding.funding_rates`` of the RBI store's ``money_market_daily`` for each source (realised: no ``as_of``)."""
    from cr_strat import rbi_store
    from cr_strat.funding import funding_rates
    daily = rbi_store.read_table(store, "money_market_daily")
    return {s: funding_rates(daily, s) for s in sources}


class _RateTable:
    """A ``funding_rates`` frame as daily rates: each calendar day at the rate of the latest dealing day on or before
    it, as ``funding.carry`` computes repo interest; a day inside the table without a row is refused."""

    def __init__(self, frame: pd.DataFrame, source: str):
        if not isinstance(frame, pd.DataFrame) or not {"date", "dealing_day", "rate_pct"} <= set(frame.columns):
            raise ValueError(f"funding for {source} must be a funding.funding_rates frame (date, dealing_day, "
                             "rate_pct)")
        self.source = source
        self.rows: dict[date, float] = {}
        self.public: dict[date, datetime | None] = {}
        for rec in frame.to_dict("records"):
            day = as_date(rec["date"])
            if day in self.rows:
                raise ValueError(f"the {source} funding table lists {day} twice")
            rate = rec["rate_pct"]
            dealing = bool(rec["dealing_day"]) and not _blank(rate)
            self.rows[day] = float(rate) if dealing else math.nan
            self.public[day] = None if "public_from" not in rec or _blank(rec["public_from"]) else as_time(
                rec["public_from"], f"{source} funding {day}: public_from")
        if not self.rows:
            raise ValueError(f"the {source} funding table is empty")
        self.first, self.last = min(self.rows), max(self.rows)
        self.dealing = sorted(d for d, r in self.rows.items() if not math.isnan(r))

    def rate_on(self, day: date, projection: str | None) -> tuple[float, date, bool]:
        """(rate, the dealing day it comes from, projected) for one calendar day."""
        if day > self.last:
            if projection != "last":
                raise ValueError(f"the {self.source} funding table ends {self.last}, before {day}: pass "
                                 "FundingPolicy(projection='last') to hold the last rate, or a later table")
            src = self.dealing[-1] if self.dealing else None
            if src is None:
                raise ValueError(f"the {self.source} funding table has no dealing day")
            return self.rows[src], src, True
        if day not in self.rows and day >= self.first:
            raise ValueError(f"the {self.source} funding table has no row for {day}; a day without a row cannot be "
                             "told from a dealing day (rbi-store-status lists such gaps)")
        k = bisect.bisect_right(self.dealing, day) - 1
        if k < 0:
            raise ValueError(f"the {self.source} funding table has no dealing day on or before {day} (it starts "
                             f"{self.first})")
        src = self.dealing[k]
        return self.rows[src], src, False

    def night(self, start: date, end: date, projection: str | None) -> tuple[float, int, int]:
        """(sum of the daily rates in percent, calendar days, projected days) over ``[start, end)``."""
        total, days, projected = 0.0, 0, 0
        day = start
        while day < end:
            rate, _, proj = self.rate_on(day, projection)
            total += rate
            days += 1
            projected += proj
            day += timedelta(days=1)
        return total, days, projected


class _MarkBook:
    """The marks by ISIN, each row with its price for its settlement and the time it was public."""

    def __init__(self, marks: pd.DataFrame | None, default_time: time | None):
        self.by_isin: dict[str, list[dict]] = {}
        self.dates: dict[str, list[date]] = {}
        if marks is None or not len(marks):
            return
        frame = read_marks(marks)
        seen = set()
        for n, rec in _records(frame):
            where = f"marks line {n}"
            isin = _text(rec.get("isin")).upper()
            if not isin:
                raise ValueError(f"{where}: isin is blank")
            day = _date(rec["date"], f"{where}: date")
            source = _text(rec.get("source")).lower() or "traded"
            if source not in MARK_SOURCES:
                raise ValueError(f"{where}: source {rec.get('source')!r} is not one of {MARK_SOURCES}")
            key = (isin, day, source)
            if key in seen:
                raise ValueError(f"{where}: two {source} marks for {isin} on {day}")
            seen.add(key)
            clean = _opt_num(rec.get("clean_price"), f"{where}: clean_price")
            yld = _opt_num(rec.get("yield_pct"), f"{where}: yield_pct")
            vwap = _opt_num(rec.get("vwap"), f"{where}: vwap")
            last = _opt_num(rec.get("last_price"), f"{where}: last_price")
            if source == "traded":
                price = vwap if vwap is not None else (last if last is not None else clean)
            else:
                price = clean
            if price is None and yld is None:
                raise ValueError(f"{where}: {isin} on {day} has no price or yield")
            if price is not None and not price > 0:
                raise ValueError(f"{where}: {isin} on {day}: a price must be positive, not {price}")
            public = _time_of(rec.get("public_from"), f"{where}: public_from")
            if public is None and default_time is not None:
                public = _at(day, default_time)
            self.by_isin.setdefault(isin, []).append({
                "date": day, "source": source, "price": price, "yield": yld if price is None else None,
                "settlement": _opt_date(rec.get("settlement_date"), f"{where}: settlement_date"),
                "vwap": vwap, "last": last if last is not None else price, "public_from": public})
        for rows in self.by_isin.values():
            rows.sort(key=lambda r: (r["date"], MARK_SOURCES.index(r["source"])))
        self.dates = {isin: [r["date"] for r in rows] for isin, rows in self.by_isin.items()}

    def on(self, isin: str, day: date, as_of: datetime | None) -> list[dict]:
        """Rows of ``day`` public by ``as_of`` (all of them when ``as_of`` is None)."""
        rows = self.by_isin.get(isin, [])
        k = bisect.bisect_left(self.dates.get(isin, []), day)
        out = []
        while k < len(rows) and rows[k]["date"] == day:
            r = rows[k]
            if as_of is None or (r["public_from"] is not None and r["public_from"] <= as_of):
                out.append(r)
            k += 1
        return out

    def latest_before(self, isin: str, day: date, as_of: datetime) -> dict | None:
        """The latest row dated before ``day`` that was public by ``as_of``."""
        rows = self.by_isin.get(isin, [])
        k = bisect.bisect_left(self.dates.get(isin, []), day) - 1
        while k >= 0:
            r = rows[k]
            if r["public_from"] is not None and r["public_from"] <= as_of:
                return r
            k -= 1
        return None


def _entry(value, default_time: time | None, day: date) -> CurveEntry:
    entry = value if isinstance(value, CurveEntry) else CurveEntry(value)
    if entry.public_from is None and default_time is not None:
        return CurveEntry(entry.curve, _at(day, default_time))
    return entry


class _Curves:
    """Curves by name and mark date, each with its public time."""

    def __init__(self, market: MarketData, default_time: time | None):
        self.named: dict[str, dict[date, CurveEntry]] = {}
        for name, by_day in (market.curves or {}).items():
            self.named[str(name)] = {as_date(d): _entry(v, default_time, as_date(d)) for d, v in by_day.items()}
        self.ois: dict[date, CurveEntry] = {as_date(d): _entry(v, default_time, as_date(d))
                                            for d, v in (market.ois_curves or {}).items()}
        self._dates = {name: sorted(v) for name, v in self.named.items()}
        self._ois_dates = sorted(self.ois)

    def get(self, name: str, day: date, as_of: datetime | None):
        """The curve ``name`` of mark date ``day`` if public by ``as_of`` (the OIS curve for ``ois``), else None."""
        table = self.ois if name == "ois" else self.named.get(name, {})
        entry = table.get(day)
        if entry is None:
            return None
        if as_of is not None and (entry.public_from is None or entry.public_from > as_of):
            return None
        return entry.curve

    def latest(self, name: str, as_of: datetime):
        """The latest curve ``name`` public by ``as_of`` (a back-test's view), or None."""
        days = self._ois_dates if name == "ois" else self._dates.get(name, [])
        table = self.ois if name == "ois" else self.named.get(name, {})
        for day in reversed(days):
            if day > as_of.date():
                continue
            entry = table[day]
            if entry.public_from is not None and entry.public_from <= as_of:
                return day, entry.curve
        return None


class _HaircutCache:
    """``funding.haircut`` looked up once per security for each period in which its rule cannot change (the table's
    effective dates and maturity bounds), so a long run does not re-read the table every day."""

    def __init__(self, table):
        self.rules = read_haircuts(table)
        self.dates = sorted(set(self.rules["effective_date"]))
        bounds = set(self.rules["min_years"].dropna()) | set(self.rules["max_years"].dropna())
        self.bounds = sorted(float(b) for b in bounds)
        self.memo: dict[tuple, float] = {}

    def get(self, inst: Instrument, day: date, liquidity: str | None) -> float:
        k = bisect.bisect_right(self.dates, day)
        eff = self.dates[k - 1] if k else None
        years = (inst.maturity - day).days / 365.25
        key = (inst.isin, inst.kind, liquidity, eff, bisect.bisect_left(self.bounds, years))
        if key not in self.memo:
            self.memo[key] = haircut(self.rules, kind=inst.kind, maturity=inst.maturity, on=day, isin=inst.isin,
                                     liquidity=liquidity)
        return self.memo[key]


# ---------------------------------------------------------------- orders, snapshots, results


@dataclass(frozen=True)
class Order:
    """An order a strategy decides: a bond (``isin``, ``side`` buy or sell, ``face`` rupees) or an OIS (``isin`` blank,
    ``side`` pay or receive fixed, ``face`` the notional, ``months`` its tenor). ``venue`` (``ndsom``; ``auction`` or
    ``auction_nc`` fill at the cut-off or the weighted average price of ``auction_date`` from the market data's
    auctions: ``isin`` is then the line's ISIN as ``Snapshot.auctions`` shows it, or, for a new issue whose ISIN is
    blank there until its result, the line's ``line``, such as ``new GS 2036``), an optional ``limit`` (clean price,
    or rate for an OIS), ``purpose`` (an OIS: ``hedge`` or ``view``), ``special_repo_pct`` (a short's special repo
    rate)."""

    book: str
    side: str
    face: float
    isin: str = ""
    months: int | None = None
    venue: str = "ndsom"
    limit: float | None = None
    purpose: str = "hedge"
    special_repo_pct: float | None = None
    auction_date: date | None = None
    order_id: str = ""


class Strategy(Protocol):
    """A strategy: each business day at the decision time it sees a ``Snapshot`` and returns orders for the next day."""

    def decide(self, snapshot: Snapshot) -> list[Order]: ...


@dataclass
class BookResult:
    """A run's output frames (``POSITION_COLUMNS``, ``NAV_COLUMNS``, ``ATTRIBUTION_COLUMNS``, ``FILL_COLUMNS``,
    ``FUNDING_COLUMNS``, ``CHECK_COLUMNS``, ``PERFORMANCE_COLUMNS``, ``DRAWDOWN_COLUMNS``) and its notes."""

    positions: pd.DataFrame
    nav: pd.DataFrame
    attribution: pd.DataFrame
    fills: pd.DataFrame
    funding: pd.DataFrame
    checks: pd.DataFrame
    performance: pd.DataFrame
    drawdowns: pd.DataFrame
    notes: tuple[str, ...] = ()

    def lines(self, book: str | None = None, *, memo: bool = False) -> pd.DataFrame:
        """The attribution summed by date and line (one book, or all), memo lines left out unless ``memo``."""
        frame = self.attribution
        if book is not None:
            frame = frame[frame["book"] == book]
        if not memo:
            frame = frame[~frame["memo"].astype(bool)]
        return frame.groupby(["date", "line"], sort=True)["amount"].sum().unstack(fill_value=0.0)


@dataclass
class _Pending:
    trade_id: str
    isin: str
    side: int
    face: float
    price: float
    settlement: date
    trade_date: date
    venue: str
    clean_prev: float = math.nan
    notified_cr: float | None = None
    auction_day: date | None = None


@dataclass
class _Receivable:
    isin: str
    amount: float
    scheduled: date
    payment: date


@dataclass
class _SwapState:
    trade_id: str
    swap: OisSwap
    trade_date: date
    purpose: str
    periods: tuple
    npv: float = 0.0
    accrued_fixed: float = 0.0
    accrued_float: float = 0.0
    curve: Any = None
    alive: bool = True
    observed: date | None = None


@dataclass
class _Deal:
    day: date
    repay: date
    parts: dict[str, float]
    lent: dict[str, tuple[float, float]]
    idle: float
    haircut_equity: float
    margin: float
    fixed: bool = False
    bases: dict[str, float] = field(default_factory=dict)  # each long's lendable value, (1 - h) x value, at the deal


@dataclass
class _Book:
    name: str
    cash: float = 0.0
    holdings: dict[str, float] = field(default_factory=dict)
    clean_prev: dict[str, float] = field(default_factory=dict)
    dirty_prev: dict[str, float] = field(default_factory=dict)
    entry: dict[str, float] = field(default_factory=dict)
    pending: list[_Pending] = field(default_factory=list)
    receivables: list[_Receivable] = field(default_factory=list)
    swaps: dict[str, _SwapState] = field(default_factory=dict)
    deal: _Deal | None = None
    nav: float = 0.0
    index: float = 1.0
    started: bool = False
    special: dict[str, float] = field(default_factory=dict)
    short_since: dict[str, date] = field(default_factory=dict)
    traded_face: float = 0.0
    traded_dv01: float = 0.0
    ai_prev: dict[str, float] = field(default_factory=dict)  # accrued interest at the close, on that day's terms


class Snapshot:
    """What a decision at ``as_of`` may see (A5.1): rows public by then, and the books as of the day's close.

    ``nav`` and ``positions`` are the day's valuation at ``valuation_time``, which is never after the decision time
    (``EngineSettings`` refuses that), so they hold only marks and curves public by ``as_of``. The accessors cut each
    table at ``as_of``: ``marks``, ``curve``, ``funding`` and ``fixings`` (the run's market data), ``events`` (the event
    ledger through ``event_ledger.events_as_of``: a result's fields, flags and notes blank until it was public),
    ``auctions`` (the market data's auction table: lines announced by then, their result blank until it was public,
    and the ISIN of a security not yet issued too: such a line is named by its ``line``), ``store_table`` (an RBI
    store table, rows public and unrevised by then), ``macro`` (``macro_store.as_of``), ``mpc_calendar``
    (``policy_path.load_mpc_calendar`` as known at the end of the day before: a schedule counts from the end of the
    day it was announced, as the event ledger dates it), ``residual_history`` (rows of fits whose marks were public by
    then) and ``master`` (the securities issued by the decision day or with an auction result public by then, with
    their fixed terms only: ``MASTER_FIXED_COLUMNS``, and ``matured`` as of the day; the other columns, the stock,
    RBI's lists, FAR, the CCIL class, are the master's state when it was built and come blank). A table the market
    data lacks raises, naming the field to fill.
    """

    def __init__(self, engine: _Engine, day: date):
        self._engine = engine
        self.day = day
        self.as_of = _at(day, engine.settings.decision_time)
        self.nav = {name: b.nav for name, b in sorted(engine.books.items())}
        self.positions = engine.positions_of(day)

    def events(self) -> pd.DataFrame:
        """The event ledger as known at ``as_of`` (``event_ledger.events_as_of``)."""
        from cr_strat.event_ledger import events_as_of
        events = self._engine.market.events
        if events is None:
            raise ValueError("no event ledger in the market data: give MarketData.events (event_ledger's frame)")
        return events_as_of(events, self.as_of)

    def auctions(self) -> pd.DataFrame:
        """The market data's auction lines announced by ``as_of`` (``AUCTION_COLUMNS``), the cut-off, weighted average
        price and accepted amount blank until the result was public (a line without a result time: blank), as is the
        ISIN of a security not issued by the decision day (``_Engine.isin_public``: a new issue's ISIN comes with its
        result, as ``event_ledger.events_as_of`` shows it); its ``line`` (``new GS 2036``) names it for a bid."""
        rows = []
        for (isin, day), rec in sorted(self._engine.auctions.items()):
            if rec["announced"] is None or rec["announced"] > self.as_of:
                continue
            known = rec["resulted"] is not None and rec["resulted"] <= self.as_of
            shown = known or self._engine.isin_public(isin, self.as_of)
            rows.append({"isin": isin if shown else "", "auction_date": day, "settlement_date": rec["settlement"],
                         "cutoff_price": rec["cutoff"] if known else math.nan,
                         "wap": rec["wap"] if known and rec["wap"] is not None else math.nan,
                         "notified_cr": rec["notified_cr"], "accepted_cr": rec["accepted_cr"] if known else math.nan,
                         "announcement_public_from": rec["announced"], "result_public_from": rec["resulted"] if known
                         else None, "kind": "", "line": rec["line"]})
        frame = pd.DataFrame(rows, columns=list(AUCTION_COLUMNS))
        return frame.sort_values(["auction_date", "line", "isin"], kind="mergesort").reset_index(drop=True)

    def store_table(self, name: str) -> pd.DataFrame:
        """An RBI store table (``rbi_store.read_table``) cut at ``as_of``: a derived table's rows whose ``public_from``
        is by then and that were not revised after it; a release table's rows whose release was public by then (its
        feed time, else the end of its release date, the store's rule). A row without a time is not public."""
        from cr_strat import rbi_store
        store = self._engine.market.store
        if store is None:
            raise ValueError("no RBI store in the market data: give MarketData.store (the store's directory)")
        if name in rbi_store._ROOT_TABLES:
            raise ValueError(f"{name} is the store's own log, not a table of releases: no point-in-time view")
        frame = rbi_store.read_table(store, name)
        when = pd.Timestamp(self.as_of)
        if "public_from" in frame:
            public = pd.to_datetime(frame["public_from"])
        elif "published" in frame or "release_date" in frame:
            published = pd.to_datetime(frame["published"]) if "published" in frame else pd.Series(pd.NaT,
                                                                                                    index=frame.index)
            ends = (pd.to_datetime(frame["release_date"]) + pd.Timedelta(hours=23, minutes=59, seconds=59)
                    if "release_date" in frame else pd.Series(pd.NaT, index=frame.index))
            public = published.fillna(ends)
        else:
            raise ValueError(f"the store table {name} has no public_from, published or release_date column")
        keep = public.notna() & (public <= when)
        if "revised" in frame:
            revised = pd.to_datetime(frame["revised"], errors="coerce")
            keep &= revised.isna() | (revised <= when)
        return frame[keep].reset_index(drop=True)

    def macro(self, series_id: str) -> pd.DataFrame:
        """A macro series as known at ``as_of`` (``macro_store.as_of`` on ``MarketData.macro_store``)."""
        from cr_strat import macro_store
        store = self._engine.market.macro_store
        if store is None:
            raise ValueError("no macro store in the market data: give MarketData.macro_store (its directory)")
        return macro_store.as_of(store, series_id, self.as_of)

    def mpc_calendar(self) -> pd.DataFrame:
        """MPC meetings as known at the end of the day before (``policy_path.load_mpc_calendar(as_of=...)`` on
        ``MarketData.mpc_meetings``, default the shipped file)."""
        from cr_strat import policy_path
        path = self._engine.market.mpc_meetings or policy_path.DEFAULT_MPC_CSV
        return policy_path.load_mpc_calendar(path, as_of=self.day - timedelta(days=1))

    def residual_history(self) -> pd.DataFrame:
        """``MarketData.residual_history`` cut at ``as_of``: rows of fits public by then (their ``public_from``, else
        the marks' default time on the trade date, the business day before the fit's settlement ``date``)."""
        frame = self._engine.market.residual_history
        if frame is None:
            raise ValueError("no residual history in the market data: give MarketData.residual_history")
        table = _frame(frame, "the residual history")
        default = self._engine.settings.marks.default_time
        keep = []
        for rec in table.to_dict("records"):
            if all(_blank(v) for v in rec.values()):
                keep.append(False)
                continue
            public = _time_of(rec.get("public_from"), "residual history public_from")
            if public is None and default is not None:
                settle = _date(rec["date"], "residual history date")
                trade = self._engine.calendar.lenient().add_business_days(settle, -1)
                public = _at(trade, default)
            keep.append(public is not None and public <= self.as_of)
        return table[keep].reset_index(drop=True)

    def master(self) -> pd.DataFrame:
        """The security master cut at ``as_of``: the rows of securities issued by the decision day or with an auction
        result public by then (``_Engine.isin_public``; a row without an issue date needs a line of the auction table
        or the event ledger, or a mark of it public by then), with their fixed terms (``MASTER_FIXED_COLUMNS``) and
        ``matured`` as of the day; every other column (the stock, RBI's lists, FAR, the CCIL class, sources) is the
        master's state when it was built and comes blank (None)."""
        frame = self._engine.market.master
        if frame is None:
            raise ValueError("no security master in the market data: give MarketData.master")
        table = _frame(frame, "the security master")
        if "isin" not in table:
            raise ValueError("the security master needs an isin column")
        keep = [bool(_text(rec.get("isin"))) and self._engine.isin_public(_text(rec.get("isin")).upper(), self.as_of,
                                                                          rec.get("issue_date"))
                for rec in table.to_dict("records")]
        out = table[keep].reset_index(drop=True)
        for column in out.columns:
            if column not in MASTER_FIXED_COLUMNS and column != "matured":
                out[column] = pd.Series([None] * len(out), index=out.index, dtype=object)
        if "matured" in out:
            due = [None if "maturity_date" not in out or _blank(m) else
                   _date(m, f"the security master's maturity_date of {i}") <= self.day
                   for i, m in zip(out["isin"], out["maturity_date"] if "maturity_date" in out else [None] * len(out))]
            out["matured"] = pd.Series(due, index=out.index, dtype=object)
        return out

    def marks(self, isin: str | None = None) -> pd.DataFrame:
        """Mark rows dated on or before the day and public by ``as_of`` (one ISIN or all)."""
        rows = []
        book = self._engine.marks
        for code in ([isin] if isin else sorted(book.by_isin)):
            for r in book.by_isin.get(code, []):
                if r["date"] <= self.day and r["public_from"] is not None and r["public_from"] <= self.as_of:
                    rows.append({"date": r["date"], "isin": code, "source": r["source"], "price": r["price"],
                                 "yield_pct": r["yield"], "vwap": r["vwap"], "public_from": r["public_from"]})
        return pd.DataFrame(rows, columns=["date", "isin", "source", "price", "yield_pct", "vwap", "public_from"])

    def curve(self, name: str = "gsec"):
        """The latest curve ``name`` public by ``as_of`` as ``(mark date, curve)``, or None (``ois``: the OIS curve)."""
        return self._engine.curves.latest(name, self.as_of)

    def funding(self, source: str = "triparty") -> pd.DataFrame:
        """The funding rates public by ``as_of`` (up to the first row that was not), as ``funding.funding_rates``."""
        frame = self._engine.market.funding.get(source)
        if frame is None:
            raise ValueError(f"no {source} funding rates in the market data")
        keep = []
        for rec in frame.sort_values("date").to_dict("records"):
            public = rec.get("public_from")
            if _blank(public) or as_time(public, "public_from") > self.as_of:
                break
            revised = rec.get("revised")
            if not _blank(revised) and as_time(revised, "revised") > self.as_of:
                break
            keep.append(rec)
        return pd.DataFrame(keep, columns=frame.columns)

    def fixings(self) -> dict[date, float]:
        """Overnight fixings public by ``as_of`` (FBIL's 12:45 on the day when no time is given)."""
        from cr_strat.funding import fbil_published_at
        times = self._engine.market.fixing_times
        out = {}
        for day, rate in sorted(self._engine.fixings.items()):
            public = times.get(day) if times is not None else fbil_published_at(day)
            if public is not None and public <= self.as_of:
                out[day] = rate
        return out


# ---------------------------------------------------------------- the engine


def _natural(text: str) -> tuple:
    """A sort key that orders the digits in ids as numbers (``T9`` before ``T10``)."""
    return tuple((0, int(part), part) if part.isdigit() else (1, 0, part) for part in re.split(r"(\d+)", text) if part)


def _order_key(rec: dict) -> tuple:
    """The order of a day's trades: purchases without a ``trade_time`` first, then the timed trades by their time, then
    sales without a time, each group in the natural order of the ids. An untimed trade's place in the day is not
    known, so a sale is checked against all the day's purchases, timed or not, and a purchase is never refused for
    a sale made after it; trades with times keep the order the times give. The file's row order never matters
    (rule 5)."""
    timed = rec["trade_time"] is not None
    group = 1 if timed else (0 if rec["side"] > 0 else 2)
    return (rec["trade_date"], group, rec["trade_time"] or datetime.min, _natural(rec["trade_id"]), rec["trade_id"])


class _Engine:
    """The daily cycle of A2.8 over a run's business days."""

    def __init__(self, market: MarketData, settings: EngineSettings, trades=None, swaps=None, flows=None,
                 strategy: Strategy | None = None):
        if not isinstance(settings, EngineSettings):
            raise TypeError("settings must be an EngineSettings (with an AccessPolicy)")
        if not isinstance(market, MarketData):
            raise TypeError("market must be a MarketData")
        self.settings = settings
        self.market = market
        cal = settings.calendar if settings.calendar is not None else mumbai_settlement_calendar()
        self.calendar = cal.lenient() if settings.project_calendar else cal
        self.strict_years = frozenset(cal.years)
        self.projected_days: set[date] = set()
        self._pay_cache: dict[str, tuple[date, bool]] = {}
        self.rf_projected: set[date] = set()  # calendar nights of the risk-free rate held at its table's last rate
        self.hedge_breaches: dict[str, int] = {}
        self.wi_open: list[str] = []
        self.short_fits: set[date] = set()
        costs = settings.costs
        self.costs = costs if isinstance(costs, cost_rules.CostTable) else cost_rules._as_table(
            cost_rules.DEFAULT_COSTS_CSV if costs is None else costs)
        self.marks = _MarkBook(market.marks, settings.marks.default_time)
        self.curves = _Curves(market, settings.marks.default_time)
        self.rates = {source: _RateTable(frame, source) for source, frame in (market.funding or {}).items()}
        self.haircuts = _HaircutCache(market.haircuts) if market.haircuts is not None else None
        self.fixings = {as_date(d): float(v) for d, v in (market.fixings or {}).items() if not _blank(v)}
        self.master = self._master(market.master)
        self.liquidity = self._liquidity(market.liquidity)
        self.history = self._history(market.residual_history)
        self.stock = self._stock(market.stock)
        self.auctions = self._auctions(market.auctions)
        self.issue_evidence = self._issue_evidence(market.events)
        self.strategy = strategy
        self._terms_tables(market)
        self._changed: set[str] = set()  # instruments whose terms as known changed at today's valuation time
        self._period_cache: dict[tuple[str, int], tuple] = {}
        self._base_maturity: dict[str, date] = {}
        self.frb_projected: dict[str, date] = {}  # FRBs held while their current coupon was projected: first day
        self.instruments: dict[str, Instrument] = {}
        self.books: dict[str, _Book] = {}
        self.trades = self._prepare_trades(read_trades(trades) if trades is not None else None)
        self.swap_trades = self._prepare_swaps(read_swaps(swaps) if swaps is not None else None)
        self.flows = self._prepare_flows(read_flows(flows) if flows is not None else None)
        self.prev_marks: dict[str, MarkState] = {}
        self.today_marks: dict[str, MarkState] = {}
        self.last_resid: dict[str, tuple[date, float]] = {}
        self.queue: list[dict] = []
        self._flow_cache: dict[tuple[str, date], tuple[np.ndarray, np.ndarray]] = {}
        self.rows: dict[str, list[dict]] = {k: [] for k in ("positions", "nav", "attribution", "fills", "funding",
                                                              "checks")}
        self.notes: list[str] = []
        self.unverified: set[str] = set()
        self.projected_nights: set[date] = set()  # calendar nights of funding held at a table's last rate
        self.order_seq = 0
        st = settings.attribution
        self.line_order = (*CARRY_LINES, RESET_LINE, "roll_down", *st.curve_lines, "rich_cheap", "convexity",
                           "price_unattributed", *TRADE_LINES, *COST_LINES, *FUNDING_LINES, *OIS_BASE_LINES,
                           *st.ois_curve_lines, "ois_settlement_rounding", "cash_benchmark", "unexplained",
                           *MEMO_LINES)

    # -- corporate bonds and FRBs: their terms tables, and each bond as known at a time

    def _terms_tables(self, market: MarketData) -> None:
        """Read the corporate and FRB tables once (each refused at once when malformed)."""
        def opt(table, reader):
            return None if table is None else reader(table)

        self.corp_terms = {} if market.corporate_terms is None else {
            r["isin"].upper(): r for r in read_corporate_terms(market.corporate_terms).to_dict("records")}
        self.corp_schedule = opt(market.corporate_schedule, read_corporate_schedule)
        self.corp_options = opt(market.corporate_options, read_corporate_options)
        self.corp_amortisation = opt(market.corporate_amortisation, read_amortisation)
        steps = opt(market.corporate_steps, read_step_rules)
        self.corp_steps = {} if steps is None else {r["isin"].upper(): r for r in steps.to_dict("records")}
        floating = opt(market.corporate_floating, read_floating_terms)
        self.corp_floating = {} if floating is None else {r["isin"].upper(): r for r in floating.to_dict("records")}
        self.benchmark_fixings = opt(market.benchmark_fixings, read_fixings)
        self.ratings = None if market.ratings is None else _frame(market.ratings, "the ratings")
        self._frb_terms: dict[str, dict] | None = None  # read when an FRB is first met
        self._frb_coupons = None
        self._tbills = None

    def _frb_tables(self) -> dict[str, dict]:
        if self._frb_terms is None:
            market = self.market
            table = load_frb_terms() if market.frb_terms is None else load_frb_terms(market.frb_terms)
            self._frb_terms = {r["isin"].upper(): r for r in table.to_dict("records")}
            self._frb_coupons = None if market.frb_coupons is None else load_frb_coupons(market.frb_coupons)
            self._tbills = None if market.tbills is None else tbill_way_table(market.tbills)
        return self._frb_terms

    def _ratings_of(self, isin: str, row: Mapping) -> pd.DataFrame:
        """The rating actions behind a bond's steps: rows of its ISIN, else of its issuer (``issuer_id``)."""
        if self.ratings is None:
            raise ValueError(f"{isin} has rating-step terms (corporate_steps) but MarketData.ratings is not given")
        frame = self.ratings
        if "isin" in frame and frame["isin"].map(_text).str.upper().eq(isin).any():
            return frame[frame["isin"].map(_text).str.upper() == isin].reset_index(drop=True)
        issuer = _text(row.get("issuer_id"))
        if issuer and "issuer_id" in frame:
            return frame[frame["issuer_id"].map(_text) == issuer].reset_index(drop=True)
        return frame.iloc[0:0]

    def _corporate_instrument(self, isin: str, row: Mapping, where: str) -> Instrument:
        """A corporate bond from its terms row and tables; ``known(t)`` gives it as known at ``t``: fixings of a
        ``frb_corp`` bond public by then, rating steps public by then, exercises whose notice was public by then."""
        base = corporate_from_terms(row, schedule=self.corp_schedule, options=self.corp_options,
                                    amortisation=self.corp_amortisation, where=where)
        floating = self.corp_floating.get(isin)
        if row["kind"] == "frb_corp" and floating is None:
            raise ValueError(f"{where}: {isin} is a frb_corp bond without a corporate_floating row (its benchmark)")
        if floating is not None and self.benchmark_fixings is None:
            raise ValueError(f"{where}: {isin} floats on {floating['benchmark']}: give MarketData.benchmark_fixings")
        steps = self.corp_steps.get(isin)
        if steps is not None and floating is not None:
            raise ValueError(f"{where}: {isin} has both benchmark fixings and rating steps: not built")
        rule = step_rule_from_row(steps) if steps is not None else None
        ratings = self._ratings_of(isin, row) if rule is not None else None
        terms = floating_terms_from_row(floating) if floating is not None else None
        fixings = self.benchmark_fixings

        def known(t: datetime) -> CorporateBond:
            bond = base
            if terms is not None:
                bond = bond.with_rates(rate_steps(corporate_frb_rates(base, terms, fixings, as_of=t)))
            if rule is not None:
                bond = apply_rating_steps(bond, ratings, rule, as_of=t)
            return bond.effective_bond(t)

        rating = _text(row.get("rating_bucket")).upper() or None
        self._base_maturity[isin] = base.maturity
        return Instrument(isin=isin, kind="corporate", bond=base, paying=base, maturity=base.maturity,
                          issue_date=base.issue_date, conventions=base.conventions, known=known,
                          rating_bucket=rating)

    def _frb_instrument(self, isin: str, row: Mapping, where: str) -> Instrument:
        """A GoI FRB from its terms row; ``known(t)`` its coupons as known at ``t`` (``floating.goi_frb_rates``:
        announced, computed from T-bill results public by then, or projected flat), every period from ``t`` on."""
        coupons, tbills, allow = self._frb_coupons, self._tbills, self.settings.frb_allow_fewer

        def known(t: datetime) -> GoiFloatingRateBond:
            rates = goi_frb_rates(row, tbills, coupons, as_of=t, allow_fewer=allow)
            gaps = rates[rates["rate_pct"].isna() & (rates["period_end"] > t.date())]
            if not gaps.empty:
                first = gaps["period_start"].iloc[0]
                raise ValueError(f"{isin}: the coupon of the period from {first} is not known at {t:%Y-%m-%d %H:%M} "
                                 "and cannot be projected: give the announced coupons (MarketData.frb_coupons) or the "
                                 "182-day T-bill results (MarketData.tbills), or EngineSettings(frb_allow_fewer=True)")
            return goi_frb_bond(row, rates)

        try:
            unit = goi_frb_bond(row, goi_frb_rates(row, None, None))
        except ValueError as exc:
            raise ValueError(f"{where}: {isin}: {exc}") from None
        return Instrument(isin=isin, kind="frb", bond=unit, paying=unit, maturity=unit.maturity,
                          issue_date=unit.issue_date, conventions=unit.conventions, known=known)

    def _bond_known(self, inst: Instrument, t: datetime) -> Any:
        try:
            return inst.known(t)
        except (ValueError, NotImplementedError) as exc:
            text = str(exc)
            raise ValueError(text if text.startswith(inst.isin) else f"{inst.isin}: {text}") from None

    def _refresh_known(self, t: datetime) -> None:
        """Rebuild each corporate bond and FRB the books hold, wait for or trade today as known at ``t``; a bond whose
        terms as known changed gets a new version, and the previous close's mark is restated on it (its yield and
        model yield from the same clean price), so that the day's moves compare like with like."""
        self._changed = set()
        wanted = {isin for b in self.books.values() for isin in (*b.holdings, *(t_.isin for t_ in b.pending))}
        wanted |= {r["isin"] for r in self.trades.get(t.date(), [])} | {o["isin"] for o in self.queue if o["isin"]}
        for isin in sorted(wanted):
            inst = self.instruments.get(isin)
            if inst is None or inst.known is None:
                continue
            bond = self._bond_known(inst, t)
            if bond == inst.bond:
                continue
            paying = replace(bond, calendar=self.calendar) if isinstance(bond, GoiFloatingRateBond) else bond
            new = replace(inst, bond=bond, paying=paying, maturity=bond.maturity, version=inst.version + 1)
            self.instruments[isin] = new
            self._pay_cache.pop(isin, None)
            self._changed.add(isin)
            prev = self.prev_marks.get(isin)
            if prev is not None:
                try:
                    y = _yield(bond, prev.clean, prev.settlement)
                    m = model_yield(prev.curve, bond, prev.settlement) if prev.curve is not None else math.nan
                except ValueError as exc:
                    raise ValueError(f"{isin}: the terms as known on {t:%Y-%m-%d} cannot price the previous close's "
                                     f"mark for {prev.settlement} ({exc})") from None
                self.prev_marks[isin] = replace(prev, yield_pct=y, model_yield_pct=m)

    def _periods(self, inst: Instrument) -> tuple:
        key = (inst.isin, inst.version)
        if key not in self._period_cache:
            self._period_cache[key] = inst.bond.periods()
        return self._period_cache[key]

    def _principal_straddled(self, inst: Instrument, d: date, s: date) -> bool:
        """Whether the holder at ``d``'s close is still owed a partial redemption (amortisation) that a settlement on
        ``s`` no longer receives: its record date falls in ``[d, s)``. The clean price falls by the principal at the
        record date (accrued interest carries only the coupon), so such a day's mark is restated to settlement on
        ``d``, as the redemption's is."""
        return any(per.principal and per.kind != "redemption" and d <= min(per.record, per.due - timedelta(days=1)) < s
                   for per in self._periods(inst))

    # -- reference data

    @staticmethod
    def _master(master) -> dict[str, dict]:
        """The master by ISIN; an ISIN listed twice with different terms (kind, coupon, dates, class) is refused, an
        exact repeat kept once (the result must not depend on which row comes first)."""
        if master is None:
            return {}
        frame = _frame(master, "the security master")
        if "isin" not in frame:
            raise ValueError("the security master needs an isin column")
        out: dict[str, dict] = {}
        lines: dict[str, int] = {}
        for n, rec in _lines(frame):
            isin = _text(rec.get("isin")).upper()
            if not isin:
                continue
            if isin in out:
                differ = [k for k in _MASTER_TERMS if not _same_term(rec.get(k), out[isin].get(k))]
                if differ:
                    raise ValueError(f"the security master lists {isin} twice with different {', '.join(differ)} "
                                     f"(lines {lines[isin]} and {n}): keep one row per ISIN")
                continue
            out[isin], lines[isin] = rec, n
        return out

    @staticmethod
    def _liquidity(table) -> dict[str, list[tuple[date, str]]]:
        if table is None:
            return {}
        frame = _frame(table, "the liquidity table")
        missing = [c for c in ("effective_date", "isin", "liquidity") if c not in frame]
        if missing:
            raise ValueError(f"the liquidity table lacks {missing} (columns: effective_date, isin, liquidity)")
        out: dict[str, list[tuple[date, str]]] = {}
        seen: dict[tuple[str, date], tuple[str, int]] = {}
        for n, rec in _lines(frame):
            cls = _text(rec["liquidity"]).lower().replace("-", "_")
            if cls not in cost_rules.LIQUIDITY_CLASSES:
                raise ValueError(f"liquidity table line {n}: {rec['liquidity']!r} is not one of "
                                 f"{cost_rules.LIQUIDITY_CLASSES}")
            isin = _text(rec["isin"]).upper()
            day = _date(rec["effective_date"], f"liquidity table line {n}: effective_date")
            if (isin, day) in seen:
                if seen[(isin, day)][0] != cls:
                    raise ValueError(f"the liquidity table gives {isin} two classes from {day} "
                                     f"({seen[(isin, day)][0]} on line {seen[(isin, day)][1]}, {cls} on line {n})")
                continue
            seen[(isin, day)] = (cls, n)
            out.setdefault(isin, []).append((day, cls))
        for rows in out.values():
            rows.sort()
        return out

    @staticmethod
    def _history(history) -> dict[str, list[tuple[date, float]]]:
        if history is None:
            return {}
        frame = _frame(history, "the residual history")
        missing = [c for c in ("date", "isin", "resid_yield_bp") if c not in frame]
        if missing:
            raise ValueError(f"the residual history lacks {missing} (curvefit.HISTORY_COLUMNS)")
        out: dict[str, list[tuple[date, float]]] = {}
        seen: dict[tuple[str, date], tuple[float, int]] = {}
        for n, rec in _lines(frame):
            value = _opt_num(rec["resid_yield_bp"], f"residual history line {n}: resid_yield_bp")
            if value is None:
                continue
            isin = _text(rec["isin"]).upper()
            day = _date(rec["date"], f"residual history line {n}: date")
            if (isin, day) in seen:
                if seen[(isin, day)][0] != value:
                    raise ValueError(f"the residual history gives {isin} two residuals on {day} (lines "
                                     f"{seen[(isin, day)][1]} and {n})")
                continue
            seen[(isin, day)] = (value, n)
            out.setdefault(isin, []).append((day, value / 100))
        for rows in out.values():
            rows.sort()
        return out

    @staticmethod
    def _stock(table) -> dict[str, list[tuple[date, float]]]:
        if table is None:
            return {}
        frame = _frame(table, "the stock table")
        out: dict[str, list[tuple[date, float]]] = {}
        seen: dict[tuple[str, date], tuple[float, int]] = {}
        for n, rec in _lines(frame):
            isin = _text(rec["isin"]).upper()
            day = _date(rec["date"], f"stock line {n}: date")
            value = _num(rec["outstanding_cr"], f"stock line {n}") * 1e7
            if (isin, day) in seen:
                if seen[(isin, day)][0] != value:
                    raise ValueError(f"the stock table gives {isin} two amounts on {day} (lines "
                                     f"{seen[(isin, day)][1]} and {n})")
                continue
            seen[(isin, day)] = (value, n)
            out.setdefault(isin, []).append((day, value))
        for rows in out.values():
            rows.sort()
        return out

    @staticmethod
    def _auctions(table) -> dict[tuple[str, date], dict]:
        """The auction table by (ISIN, auction date): settlement, cut-off price, weighted average price and, where
        given, the notified and accepted amounts (crore) and the times the announcement and the result were public
        (``AUCTION_COLUMNS``). A key given twice with different values is refused; an exact repeat kept once."""
        if table is None:
            return {}
        frame = _frame(table, "the auctions")
        missing = [c for c in ("isin", "auction_date", "settlement_date", "cutoff_price") if c not in frame]
        if missing:
            raise ValueError(f"the auction table lacks {missing} (columns: {', '.join(AUCTION_COLUMNS)})")
        out: dict[tuple[str, date], dict] = {}
        lines: dict[tuple[str, date], int] = {}
        for n, rec in _lines(frame):
            where = f"auctions line {n}"
            key = (_text(rec["isin"]).upper(), _date(rec["auction_date"], f"{where}: auction_date"))
            row = {"settlement": _date(rec["settlement_date"], f"{where}: settlement_date"),
                   "cutoff": _num(rec["cutoff_price"], f"{where}: cutoff_price"),
                   "wap": _opt_num(rec.get("wap"), f"{where}: wap"),
                   "notified_cr": _opt_num(rec.get("notified_cr"), f"{where}: notified_cr"),
                   "accepted_cr": _opt_num(rec.get("accepted_cr"), f"{where}: accepted_cr"),
                   "announced": _time_of(rec.get("announcement_public_from"), f"{where}: announcement_public_from"),
                   "resulted": _time_of(rec.get("result_public_from"), f"{where}: result_public_from"),
                   "line": _text(rec.get("line"))}
            if key in out:
                if out[key] != row:
                    raise ValueError(f"the auction table lists {key[0]} auctioned on {key[1]} twice with different "
                                     f"values (lines {lines[key]} and {n})")
                continue
            out[key], lines[key] = row, n
        return out

    def _issue_evidence(self, events) -> dict[str, tuple[date | None, datetime | None]]:
        """Per ISIN, from the auction table and the event ledger's auction rows: the first settlement of a line of it
        (the security is issued from then) and the first time one of its results was public (a new issue's coupon and
        ISIN from then). What ``isin_public`` reads besides the master's issue date."""
        out: dict[str, list] = {}

        def note(isin: str, settled: date | None, resulted: datetime | None) -> None:
            seen = out.setdefault(isin, [None, None])
            if settled is not None and (seen[0] is None or settled < seen[0]):
                seen[0] = settled
            if resulted is not None and (seen[1] is None or resulted < seen[1]):
                seen[1] = resulted

        for (isin, _), rec in self.auctions.items():
            note(isin, rec["settlement"], rec["resulted"])
        if events is not None:
            frame = _frame(events, "the event ledger")
            if "kind" in frame and "isin" in frame:
                for n, rec in _lines(frame):
                    isin = _text(rec.get("isin")).upper()
                    if not isin or not _text(rec.get("kind")).endswith("_auction"):
                        continue
                    where = f"event ledger row {n} ({_text(rec.get('event_id'))})"
                    note(isin, _opt_date(rec.get("settlement_date"), f"{where}: settlement_date"),
                         _time_of(rec.get("result_public_from"), f"{where}: result_public_from"))
        return {isin: (seen[0], seen[1]) for isin, seen in out.items()}

    def isin_public(self, isin: str, as_of: datetime, issue_date=None) -> bool:
        """Whether a security and its terms were public at ``as_of``: issued by that day (``issue_date``, else the
        master's, or a line of the auction table or the event ledger settled by then), one of its auction results
        public by then, or, when no issue date is given, a mark of it dated by the day and public by then (it was
        priced, so it existed; with an issue date still to come such a mark is a when-issued price, and the terms
        wait for the result). A security with no such evidence is not."""
        day = as_of.date()
        issue = issue_date if not _blank(issue_date) else self.master.get(isin, {}).get("issue_date")
        if not _blank(issue):
            if _date(issue, f"the security master's issue_date of {isin}") <= day:
                return True
        elif any(r["date"] <= day and r["public_from"] is not None and r["public_from"] <= as_of
                 for r in self.marks.by_isin.get(isin, [])):
            return True
        settled, resulted = self.issue_evidence.get(isin, (None, None))
        return (settled is not None and settled <= day) or (resulted is not None and resulted <= as_of)

    def liquidity_on(self, inst: Instrument, day: date) -> str | None:
        rows = self.liquidity.get(inst.isin)
        if rows:
            k = bisect.bisect_right(rows, (day, "~")) - 1
            if k >= 0:
                return rows[k][1]
        return inst.liquidity

    # -- dates past the holiday list: projected on the calendar's lenient copy and flagged, never a late crash

    def _projected(self, day: date, flag: date | None) -> None:
        if day.year not in self.strict_years and flag is not None:
            self.projected_days.add(flag)

    def _next_business_day(self, d: date) -> date:
        """The business day after ``d``; past the holiday list's years the next weekday (``d``'s rows flagged
        ``calendar_projected``), so a run may end on 31 Dec of the list's last year."""
        try:
            nxt = self.calendar.add_business_days(d, 1)
        except CalendarCoverageError:
            nxt = self.calendar.lenient().add_business_days(d, 1)
        self._projected(nxt, d)
        return nxt

    def _standard_settlement(self, d: date, lag: int, flag: date | None) -> date:
        try:
            s = self.calendar.settlement_date(d, lag)
        except CalendarCoverageError:
            s = self.calendar.lenient().settlement_date(d, lag)
        self._projected(s, flag)
        return s

    def _mark_settlement(self, inst: Instrument, d: date, flag: date | None = None) -> date:
        """The settlement a mark of ``d`` is for: the standard one (T+1 for G-secs), or the issue date of a security
        not yet issued (a when-issued price)."""
        s = self._standard_settlement(d, inst.lag, flag if flag is not None else d)
        if inst.issue_date is not None and s < inst.issue_date:
            return inst.issue_date
        return s

    def _payment_date(self, inst: Instrument) -> tuple[date, bool]:
        """The redemption's payment date (the working day before a holiday maturity) and whether it was projected
        past the holiday list."""
        if inst.isin not in self._pay_cache:
            try:
                pay = inst.redemption_payment()
            except CalendarCoverageError:
                pay = replace(inst, paying=replace(inst.paying, calendar=self.calendar.lenient())).redemption_payment()
            self._pay_cache[inst.isin] = (pay, pay.year not in self.strict_years)
        return self._pay_cache[inst.isin]

    def instrument(self, isin: str, terms: Mapping | None, where: str) -> Instrument:
        """The ISIN's instrument, built once from the trade's terms filled from the master (a conflict refused)."""
        given = {k: (terms or {}).get(k) for k in ("kind", "coupon_pct", "maturity_date", "issue_date",
                                                   "first_coupon_date", "liquidity")}
        master = self.master.get(isin, {})
        merged = {}
        for key, value in given.items():
            known = master.get(key)
            if _blank(value):
                merged[key] = known
            else:
                if not _blank(known) and key != "liquidity" and not _same_term(value, known):
                    raise ValueError(f"{where}: {isin}'s {key} is {value!r} in the trades but {known!r} in the "
                                     "security master")
                merged[key] = value
        if isin in self.instruments:
            inst = self.instruments[isin]
            if inst.known is not None:
                self._check_given_terms(inst, given, where)
                return inst
            for key in ("kind", "coupon_pct", "maturity_date"):
                if not _blank(given[key]) and not _same_term(given[key], _term_of(inst, key)):
                    raise ValueError(f"{where}: {isin}'s {key} {given[key]!r} differs from an earlier trade's "
                                     f"{_term_of(inst, key)!r}")
            return inst
        kind = _text(merged.get("kind")).lower()
        if isin in self.corp_terms:
            if kind not in ("", "corporate"):
                raise ValueError(f"{where}: {isin} is in the corporate terms, but its kind is given as {kind!r}")
            inst = self._corporate_instrument(isin, self.corp_terms[isin], where)
        elif kind in ("", "frb") and isin in self._frb_tables():
            inst = self._frb_instrument(isin, self._frb_tables()[isin], where)
        else:
            inst = make_instrument(isin, merged, self.calendar, where)
        if inst.known is not None:
            self._check_given_terms(inst, given, where)
        self.instruments[isin] = inst
        return inst

    def _check_given_terms(self, inst: Instrument, given: Mapping, where: str) -> None:
        """A trade's coupon and maturity of a corporate bond (or an FRB's maturity) must agree with its terms table:
        the original maturity, not an exercised call's."""
        if inst.kind == "corporate":
            row = self.corp_terms[inst.isin]
            coupon, maturity = row.get("coupon_pct"), row.get("maturity_date")
        else:
            row = self._frb_tables()[inst.isin]
            coupon, maturity = None, row.get("maturity_date")
        for key, known in (("coupon_pct", coupon), ("maturity_date", maturity)):
            value = given.get(key)
            if not _blank(value) and not _blank(known) and not _same_term(value, known):
                raise ValueError(f"{where}: {inst.isin}'s {key} is {value!r} in the trades but {known!r} in its "
                                 f"{inst.kind} terms")

    # -- inputs

    def _prepare_trades(self, frame: pd.DataFrame | None) -> dict[date, list[dict]]:
        out: dict[date, list[dict]] = {}
        if frame is None:
            return out
        seen: set[str] = set()
        for n, rec in _records(frame):
            where = f"trades line {n}"
            tid = _text(rec.get("trade_id"))
            if not tid:
                raise ValueError(f"{where}: trade_id is blank")
            if tid in seen:
                raise ValueError(f"{where}: trade_id {tid} is used twice")
            seen.add(tid)
            where = f"trade {tid}"
            book = _text(rec.get("book"))
            if not book:
                raise ValueError(f"{where}: book is blank")
            isin = _text(rec.get("isin")).upper()
            if not isin:
                raise ValueError(f"{where}: isin is blank")
            side_text = _text(rec.get("side")).lower()
            if side_text not in ("buy", "sell"):
                raise ValueError(f"{where}: side must be buy or sell, not {rec.get('side')!r}")
            face = _num(rec.get("face"), f"{where}: face")
            if not face > 0:
                raise ValueError(f"{where}: face must be a positive number of rupees, not {rec.get('face')!r}")
            day = _date(rec.get("trade_date"), f"{where}: trade_date")
            inst = self.instrument(isin, rec, where)
            venue = _text(rec.get("venue")).lower() or ("otc" if inst.kind in T0_KINDS else "ndsom")
            if venue not in cost_rules.VENUES:
                raise ValueError(f"{where}: venue {rec.get('venue')!r} is not one of {cost_rules.VENUES}")
            price = _opt_num(rec.get("price"), f"{where}: price")
            yld = _opt_num(rec.get("yield_pct"), f"{where}: yield_pct")
            if price is not None and yld is not None:
                raise ValueError(f"{where}: give a price or a yield_pct, not both")
            if price is not None and not price > 0:
                raise ValueError(f"{where}: price must be positive, not {price}")
            trade_time = None
            if not _blank(rec.get("trade_time")):
                text = _text(rec.get("trade_time"))
                trade_time = as_time(text if "T" in text or " " in text else f"{day.isoformat()}T{text}",
                                     f"{where}: trade_time")
            settle = _opt_date(rec.get("settlement_date"), f"{where}: settlement_date")
            settle = self._settlement(inst, day, settle, venue, where)
            special = _opt_num(rec.get("special_repo_pct"), f"{where}: special_repo_pct")
            out.setdefault(day, []).append({
                "trade_id": tid, "book": book, "isin": isin, "side": 1 if side_text == "buy" else -1, "face": face,
                "price": price, "yield": yld, "trade_date": day, "trade_time": trade_time, "settlement": settle,
                "venue": venue, "mid": _opt_num(rec.get("mid_price"), f"{where}: mid_price"),
                "liquidity": _text(rec.get("liquidity")).lower().replace("-", "_") or None,
                "broker": _text(rec.get("broker")) or None, "special": special,
                "notified_cr": _opt_num(rec.get("notified_cr"), f"{where}: notified_cr"),
                "rating_bucket": _text(rec.get("rating_bucket")).upper() or None, "note": _text(rec.get("note"))})
        for rows in out.values():
            rows.sort(key=_order_key)
        return out

    def _settlement(self, inst: Instrument, day: date, settle: date | None, venue: str, where: str) -> date:
        cal = self.calendar
        if inst.known is not None and cal.is_business_day(day):  # the bond as known on the trade day
            bond = self._bond_known(inst, _at(day, self.settings.valuation_time))
            paying = replace(bond, calendar=cal) if isinstance(bond, GoiFloatingRateBond) else bond
            inst = replace(inst, bond=bond, paying=paying, maturity=bond.maturity)
        if not cal.is_business_day(day):
            raise ValueError(f"{where}: trade date {day} is not a Mumbai business day (the engine trades on business "
                             "days)")
        if settle is None and venue == "when_issued":
            standard = cal.settlement_date(day, inst.lag)
            if inst.issue_date is None or inst.issue_date <= standard:
                raise ValueError(f"{where}: a when-issued trade settles on the auction's settlement date: give "
                                 "settlement_date (or the new issue's issue_date)")
            settle = inst.issue_date
        s = settle if settle is not None else cal.settlement_date(day, inst.lag)
        if s < day:
            raise ValueError(f"{where}: settlement {s} is before the trade date {day}")
        if not cal.is_business_day(s):
            raise ValueError(f"{where}: settlement {s} is not a Mumbai business day")
        if s == day and inst.kind not in T0_KINDS and venue not in ("auction", "auction_nc", "omo", "switch") \
                and not self.settings.allow_t0:
            raise ValueError(f"{where}: a T+0 trade in a {inst.kind} is refused (the handbook's trading hours list a "
                             "T+1 window only for G-secs, SDLs and T-bills; partly verified): pass allow_t0=True")
        if s >= inst.maturity:
            raise ValueError(f"{where}: settlement {s} is on or after {inst.isin}'s maturity {inst.maturity}")
        if inst.issue_date is not None and s < inst.issue_date:
            raise ValueError(f"{where}: settlement {s} is before {inst.isin}'s issue date {inst.issue_date}")
        if inst.kind == "corporate":
            last = inst.last_settlement
            if s > last:
                raise ValueError(f"{where}: settlement {s} is after {inst.isin}'s redemption record date {last}: "
                                 f"trading is suspended until the redemption due {inst.maturity}")
            return s
        if hasattr(inst.bond, "in_shut_period") and inst.bond.in_shut_period(s):
            raise ValueError(f"{where}: settlement {s} falls in {inst.isin}'s shut period before redemption on "
                             f"{inst.maturity} (one day, UNVERIFIED rule of conventions)")
        if inst.maturity - timedelta(days=_ROLL_LOOKBACK_DAYS) <= s:
            pay, _ = self._payment_date(inst)
            if s >= pay:
                raise ValueError(f"{where}: settlement {s} is on or after {inst.isin}'s redemption payment on {pay} "
                                 f"(maturity {inst.maturity}, a holiday, paid the working day before): the holder "
                                 "is redeemed at the start of that day, so the trade would settle against a "
                                 "redeemed bond")
        return s

    def _prepare_swaps(self, frame: pd.DataFrame | None) -> dict[date, list[dict]]:
        out: dict[date, list[dict]] = {}
        if frame is None:
            return out
        ids = {t["trade_id"] for rows in self.trades.values() for t in rows}
        for n, rec in _records(frame):
            where = f"OIS trades line {n}"
            tid = _text(rec.get("trade_id"))
            if not tid:
                raise ValueError(f"{where}: trade_id is blank")
            if tid in ids:
                raise ValueError(f"{where}: trade_id {tid} is used twice")
            ids.add(tid)
            where = f"OIS trade {tid}"
            book = _text(rec.get("book"))
            if not book:
                raise ValueError(f"{where}: book is blank")
            day = _date(rec.get("trade_date"), f"{where}: trade_date")
            if not self.calendar.is_business_day(day):
                raise ValueError(f"{where}: trade date {day} is not a Mumbai business day")
            months_text = _text(rec.get("months"))
            try:
                months = int(float(months_text))
            except ValueError:
                raise ValueError(f"{where}: months {rec.get('months')!r} is not a whole number of months") from None
            notional = _num(rec.get("notional"), f"{where}: notional")
            if not notional > 0:
                raise ValueError(f"{where}: notional must be positive, not {notional}")
            pay = _bool(rec.get("pay_fixed"), f"{where}: pay_fixed", True)
            eff = _opt_date(rec.get("effective"), f"{where}: effective") or effective_date(day, MIBOR_OIS,
                                                                                           self.calendar)
            if eff <= day:
                raise ValueError(f"{where}: effective {eff} must be after the trade date {day}")
            purpose = _text(rec.get("purpose")).lower() or "hedge"
            if purpose not in ("hedge", "view"):
                raise ValueError(f"{where}: purpose must be hedge or view, not {rec.get('purpose')!r}")
            try:
                ois_schedule(eff, months, MIBOR_OIS, self.calendar)
            except ValueError as exc:
                raise ValueError(f"{where}: {exc}") from None
            out.setdefault(day, []).append({
                "trade_id": tid, "book": book, "trade_date": day, "effective": eff, "months": months,
                "rate": _opt_num(rec.get("fixed_rate_pct"), f"{where}: fixed_rate_pct"), "notional": notional,
                "pay_fixed": pay, "mid": _opt_num(rec.get("mid_rate_pct"), f"{where}: mid_rate_pct"),
                "purpose": purpose, "note": _text(rec.get("note"))})
        for rows in out.values():
            rows.sort(key=lambda r: r["trade_id"])
        return out

    def _prepare_flows(self, frame: pd.DataFrame | None) -> dict[date, dict[str, float]]:
        """The flows by day and book, several on one day summed exactly (``math.fsum``), so that the sum does not
        depend on the rows' order (rule 5)."""
        parts: dict[date, dict[str, list[float]]] = {}
        if frame is None:
            return {}
        for n, rec in _records(frame):
            where = f"flows line {n}"
            day = _date(rec.get("date"), f"{where}: date")
            book = _text(rec.get("book"))
            if not book:
                raise ValueError(f"{where}: book is blank")
            if not self.calendar.is_business_day(day):
                raise ValueError(f"{where}: {day} is not a Mumbai business day (flows arrive at the start of one)")
            amount = _num(rec.get("amount"), f"{where}: amount")
            parts.setdefault(day, {}).setdefault(book, []).append(amount)
        return {day: {book: math.fsum(sorted(amounts)) for book, amounts in sorted(by_book.items())}
                for day, by_book in sorted(parts.items())}

    # -- the run

    def run(self, start, end) -> None:
        firsts = [d for d in (*self.trades, *self.swap_trades, *self.flows)]
        s = as_date(start) if start is not None else (min(firsts) if firsts else None)
        if s is None:
            raise ValueError("nothing to run: no trades, OIS trades or flows, and no start date")
        e = as_date(end) if end is not None else max([*firsts, s])
        if e < s:
            raise ValueError(f"end {e} is before start {s}")
        early = sorted(d for d in firsts if d < s)
        if early:
            raise ValueError(f"trades or flows on {early[0]} fall before the run's start {s}: start the run on or "
                             "before the first of them")
        late = sorted({d for d in firsts if d > e})
        if late:
            self.notes.append(f"trades or flows after the end {e} were not run ({len(late)} day(s) from {late[0]})")
        days = self.calendar.business_days(s, e)
        if not days:
            raise ValueError(f"no Mumbai business day from {s} to {e}")
        prev = None
        for d in days:
            self._day(d, prev)
            prev = d
        if self.queue:
            for order in self.queue:
                self._fill_row(order["date_decided"], order, status="open at the end", notes="not filled by the end")
        if self.projected_nights:
            self.notes.append(f"{len(self.projected_nights)} night(s) of funding held at the last rate of a funding "
                              f"table, from {min(self.projected_nights)} "
                              "(FundingPolicy.projection='last')")
        if self.rf_projected:
            self.notes.append(f"{len(self.rf_projected)} night(s) of the risk-free rate ({self.settings.rf_segment}) "
                              f"held at the last rate of its table, from {min(self.rf_projected)} "
                              "(FundingPolicy.projection='last'): excess returns and the Sharpe ratio use it")
        if any(d.year not in self.strict_years for d in days):
            self.notes.append("calendar_projected: dates past the holiday list's years were treated as business days "
                              "on weekdays (project_calendar)")
        edge = sorted(d for d in self.projected_days if d.year in self.strict_years)
        if edge:
            self.notes.append(f"calendar_projected on {len(edge)} day(s) inside the holiday list's years ("
                              f"{', '.join(str(d) for d in edge[:3])}): their next business day, settlement or a "
                              "redemption's payment fell past the list and was taken as a weekday")
        if self.short_fits:
            self.notes.append(f"curve factors: on {len(self.short_fits)} day(s) from {min(self.short_fits)} a curve "
                              "gave fewer usable nodes than factors (a money-market curve): only the leading shapes "
                              "were fitted, the rest of its move is in curve_other or ois_curve_other")
        for name, count in sorted(self.hedge_breaches.items()):
            self.notes.append(f"hedge_only: book {name}'s OIS did not hedge its bonds on {count} business day(s) "
                              f"(the access policy {self.settings.access.name!r} allows OIS for hedging only; "
                              "checks.csv has a row per day)")
        if self.unverified:
            self.notes.append("costs used UNVERIFIED default rules: " + ", ".join(sorted(self.unverified)))
        for isin, first in sorted(self.frb_projected.items()):
            self.notes.append(f"FRB {isin}: its current coupon was projected, not announced or computed, from {first} "
                              "(UNVERIFIED flat projection: floating.goi_frb_rates; positions.notes per day)")
        corporates = sorted(i for i, inst in self.instruments.items() if inst.kind == "corporate")
        if corporates:
            self.notes.append(f"corporate bonds ({len(corporates)}): coupons and principal entitled at the close of "
                              "their record dates, payment and record dates rolled on the lenient settlement calendar "
                              "(weekends only past its years); a TREPS book cannot lend against them (market repo with "
                              "a haircut row, else equity)")

    def _day(self, d: date, p: date | None) -> None:
        t_val = _at(d, self.settings.valuation_time)
        self._refresh_known(t_val)  # corporate bonds and FRBs as known at today's valuation time
        lines: dict[tuple[str, str, str], float] = {}
        self._positions_today: dict[tuple[date, str], list[dict]] = {}  # the day's rows, for the decision's snapshot
        names = set(self.flows.get(d, {})) | {t["book"] for t in self.trades.get(d, [])} \
            | {t["book"] for t in self.swap_trades.get(d, [])} | {o["book"] for o in self.queue}
        for name in sorted(names):
            if name not in self.books:
                self.books[name] = _Book(name)
        books = [self.books[k] for k in sorted(self.books)]
        flows = self.flows.get(d, {})
        sod: dict[str, dict] = {}
        for book in books:
            sod[book.name] = {"nav": book.nav, "holdings": dict(book.holdings), "pending": list(book.pending),
                              "flow": flows.get(book.name, 0.0), "redeemed": {}, "coupons": {}, "fills": [],
                              "swap_fills": [], "settled": [], "notes": []}
            book.cash += flows.get(book.name, 0.0)
        for book in books:  # 2. the night's funding, repaid with interest
            self._settle_deal(book, p, d, sod[book.name], lines)
        for book in books:  # 3. coupons, redemptions, receivables
            if p is not None:
                self._entitlements(book, p, d, sod[book.name], lines)
        for book in books:  # 4. settlements
            self._settle_pending(book, d, sod[book.name])
        self._fill_trades(d, sod, lines)  # 5. fills of the day
        self._fill_orders(d, sod, lines)
        self._check_when_issued_close(d)
        for book in books:  # OIS periods ending today: cash
            self._swap_settlements(book, p, d, sod[book.name])
        for book in books:  # 6. funding for the night
            self._fund(book, d, sod[book.name], lines)
        self._marks_of_day(d, t_val, books, sod)  # 7. marks
        for book in books:  # 8. valuation, attribution, NAV, checks
            self._value(book, p, d, t_val, sod[book.name], lines)
        self._emit_lines(d, lines)
        self.prev_marks = {k: v for k, v in self.today_marks.items() if self._exposed(k)}
        if self.strategy is not None:  # 9. the decision on what was public by the decision time
            self._decide(d)

    def _exposed(self, isin: str) -> bool:
        return any(b.holdings.get(isin) or any(pd_.isin == isin for pd_ in b.pending) for b in self.books.values())

    # -- funding repaid, interest of the night

    def _rate_table(self, source: str) -> _RateTable:
        table = self.rates.get(source)
        if table is None:
            raise ValueError(f"no {source} funding rates: give MarketData.funding[{source!r}] "
                             "(funding.funding_rates of the RBI store's money_market_daily)")
        return table

    def _rate_sum(self, source: str, start: date, end: date) -> tuple[float, list[date]]:
        """(sum of the daily rates in percent, the nights held at the table's last rate) of a deal struck on ``start``
        and repaid on ``end``: the deal day's rate for every night (``rate_basis="deal_day"``: all its nights are held
        when the deal day is past the table), or each calendar day at the rate of the latest dealing day on or before
        it (``"calendar_day"``, as ``funding.carry``: the nights past the table are held). A night is the calendar day
        it starts on."""
        pol = self.settings.funding
        table = self._rate_table(source)
        nights = [start + timedelta(days=k) for k in range((end - start).days)]
        if pol.rate_basis == "calendar_day":
            total, _, projected = table.night(start, end, pol.projection)
            return total, [n for n in nights if n > table.last] if projected else []
        rate, _, projected = table.rate_on(start, pol.projection)
        return rate * len(nights), nights if projected else []

    def _risk_free(self, p: date, d: date) -> float:
        """The risk-free return of the night(s) from ``p`` to ``d``: TREPS (``rf_segment``) on the funding policy's
        rate basis, past the end of its table only with ``projection="last"`` (counted and noted)."""
        if self.settings.rf_segment not in self.rates:
            return math.nan
        try:
            total, projected = self._rate_sum(self.settings.rf_segment, p, d)
        except ValueError as exc:
            raise ValueError(f"the risk-free rate ({self.settings.rf_segment}) for the night of {p}: {exc}") from None
        self.rf_projected.update(projected)
        return total / 36500

    def _settle_deal(self, book: _Book, p: date | None, d: date, sod: dict, lines: dict) -> None:
        deal = book.deal
        pol = self.settings.funding
        sod["rf"] = self._risk_free(p, d) if p is not None else math.nan
        if deal is None or p is None:
            return
        n = (d - deal.day).days
        main_sum, projected = self._rate_sum(pol.segment, deal.day, d) \
            if (deal.parts.get("main") or deal.haircut_equity or deal.margin) else (0.0, [])
        self.projected_nights.update(projected)
        for part, line in (("main", "funding_repo"), ("excess", "funding_excess"), ("overdraft", "funding_overdraft")):
            amount = deal.parts.get(part, 0.0)
            if not amount:
                continue
            if part == "main":
                total = main_sum + pol.spread_bp / 100 * n
            elif part == "excess":
                r_sum, proj = self._rate_sum(pol.excess_segment, deal.day, d)
                self.projected_nights.update(proj)
                total = r_sum + pol.spread_bp / 100 * n
            else:
                total = float(pol.overdraft_rate_pct) * n
            interest = amount * total / 36500
            book.cash -= amount + interest
            _add(lines, book.name, "", line, -interest)
        for isin, (amount, rate) in sorted(deal.lent.items()):
            interest = amount * rate * n / 36500
            book.cash += amount + interest
            _add(lines, book.name, isin, "cash_interest", interest)
        if deal.idle and pol.idle_cash_rate_pct:
            _add(lines, book.name, "", "cash_interest", deal.idle * pol.idle_cash_rate_pct * n / 36500)
            book.cash += deal.idle * pol.idle_cash_rate_pct * n / 36500
        if deal.haircut_equity:
            _add(lines, book.name, "", "memo_haircut_cost", -deal.haircut_equity * main_sum / 36500)
        if deal.margin:
            _add(lines, book.name, "", "memo_margin_cost", -deal.margin * main_sum / 36500)
        book.deal = None

    # -- coupons and redemptions

    def _entitlements(self, book: _Book, p: date, d: date, sod: dict, lines: dict) -> None:
        for isin in sorted(sod["holdings"]):
            face = sod["holdings"][isin]
            inst = self.instruments[isin]
            units = face / 100
            if inst.kind == "corporate":
                self._corporate_entitlements(book, inst, units, p, d, sod, lines)
                continue
            # the payment rolls back at most a few days from maturity: only look it up near it (a strict calendar
            # refuses years without a holiday list)
            pay = None
            if inst.maturity - timedelta(days=_ROLL_LOOKBACK_DAYS) <= d:
                pay, projected = self._payment_date(inst)
                if projected and p < pay <= d:
                    self.projected_days.add(d)
            if pay is not None and p < pay <= d:
                amount = inst.redemption_amount()
                book.cash += units * amount
                last_coupon = amount - 100.0
                ai_prev = book.ai_prev[isin] if inst.known is not None else _accrued(inst.bond, p)
                _add(lines, book.name, isin, "accrual", units * (last_coupon - ai_prev))
                _add(lines, book.name, isin, "pull_to_par", units * (100.0 - book.clean_prev[isin]))
                sod["redeemed"][isin] = units * amount
                del book.holdings[isin]
                book.clean_prev.pop(isin, None)
                book.dirty_prev.pop(isin, None)
                book.entry.pop(isin, None)
                book.short_since.pop(isin, None)
                book.ai_prev.pop(isin, None)
                continue
            if isinstance(inst.bond, DiscountInstrument) or (
                    not isinstance(inst.bond, GoiFloatingRateBond) and not inst.bond.coupon_pct):
                continue
            coupons = 0.0
            for flow in inst.paying.cash_flows(p, until=d):
                if flow.kind != "coupon" or flow.date == inst.maturity or not p < flow.date <= d:
                    continue
                coupons += flow.amount
                book.receivables.append(_Receivable(isin, units * flow.amount, flow.date, flow.payment_date))
            if coupons:
                sod["coupons"][isin] = coupons
        keep = []
        for rec in book.receivables:
            if rec.payment <= d:
                book.cash += rec.amount
            else:
                keep.append(rec)
        book.receivables = keep

    def _corporate_entitlements(self, book: _Book, inst: Instrument, units: float, p: date, d: date, sod: dict,
                                lines: dict) -> None:
        """A corporate bond's flows go to the holder at the close of their record date: the last settlement date that
        receives a flow is its record date (a settlement on it is cum), or the day before it is due without one, and
        a record date on a holiday uses the previous business day's close. So on business day ``d`` the holding of
        the previous close ``p`` is entitled to the flows whose last cum settlement falls in ``[p, d)``: coupons
        (accrual) and principal (pull to par) as receivables until their payment dates; the redemption closes the
        position into a receivable, trading being suspended until it is paid."""
        isin = inst.isin
        coupons = principal = 0.0
        closing = None
        for per in self._periods(inst):
            cut = min(per.record, per.due - timedelta(days=1))
            if not p <= cut < d:
                continue
            if per.kind == "redemption":
                closing = per
                continue
            if per.amount:
                coupons += per.amount
                book.receivables.append(_Receivable(isin, units * per.amount, per.due, per.payment))
            if per.principal:
                principal += per.principal
                book.receivables.append(_Receivable(isin, units * per.principal, per.due, per.payment))
        if closing is not None:
            amount = closing.amount + closing.principal
            book.receivables.append(_Receivable(isin, units * amount, closing.due, closing.payment))
            _add(lines, book.name, isin, "accrual", units * (coupons + closing.amount - book.ai_prev[isin]))
            _add(lines, book.name, isin, "pull_to_par",
                 units * (principal + closing.principal - book.clean_prev[isin]))
            sod["redeemed"][isin] = units * amount
            del book.holdings[isin]
            for table in (book.clean_prev, book.dirty_prev, book.entry, book.short_since, book.ai_prev):
                table.pop(isin, None)
            return
        if coupons:
            sod["coupons"][isin] = coupons
        if principal:
            sod.setdefault("principal", {})[isin] = principal

    def _settle_pending(self, book: _Book, d: date, sod: dict) -> None:
        keep = []
        for trade in book.pending:
            if trade.settlement > d:
                keep.append(trade)
                continue
            if trade.settlement < d:
                raise RuntimeError(f"trade {trade.trade_id} was not settled on {trade.settlement}")
            inst = self.instruments[trade.isin]
            self._settle_into_holding(book, inst, trade.side, trade.face, trade.price, d, trade.trade_id)
            sod["settled"].append(trade)
        book.pending = keep

    def _settle_into_holding(self, book: _Book, inst: Instrument, side: int, face: float, price: float, d: date,
                             trade_id: str) -> None:
        dirty = price + _accrued(inst.bond, d)
        book.cash -= side * face / 100 * dirty
        before = book.holdings.get(inst.isin, 0.0)
        after = before + side * face
        if abs(after) < 1e-6:
            book.holdings.pop(inst.isin, None)
            book.entry.pop(inst.isin, None)
            book.short_since.pop(inst.isin, None)
        else:
            book.holdings[inst.isin] = after
            if after > 0 and side > 0:
                book.entry[inst.isin] = (max(before, 0.0) * book.entry.get(inst.isin, dirty) + face * dirty) / (
                    max(before, 0.0) + face)
            elif after > 0 and inst.isin not in book.entry:
                book.entry[inst.isin] = dirty
            if after < 0 and before >= 0:
                book.short_since[inst.isin] = d
        collateral = book.dirty_prev.get(inst.isin)
        if collateral is None or before == 0:
            book.dirty_prev[inst.isin] = dirty

    # -- fills

    def _fill_trades(self, d: date, sod: dict, lines: dict) -> None:
        for rec in self.trades.get(d, []):
            book = self.books[rec["book"]]
            inst = self.instruments[rec["isin"]]
            where = f"trade {rec['trade_id']}"
            price = rec["price"]
            if price is None and rec["yield"] is not None:
                price = _clean(inst.bond, rec["yield"], rec["settlement"])
            mid = rec["mid"]
            if price is None and mid is None:
                mid = self._execution_mid(inst, d, rec["settlement"], rec["venue"])
                if mid is None:
                    raise ValueError(f"{where}: no price given and no {'/'.join(self.settings.marks.fill_sources)} "
                                     f"price of {inst.isin} on {d} to fill at (fills never use model or stale prices)")
            elif mid is None:
                mid = price
            self._fill_bond(book, inst, sod[book.name], lines, trade_id=rec["trade_id"], order_id="", side=rec["side"],
                            face=rec["face"], d=d, settle=rec["settlement"], venue=rec["venue"], mid=mid,
                            fill=price, broker=rec["broker"], liquidity=rec["liquidity"], special=rec["special"],
                            notified=rec["notified_cr"], given_mid=rec["mid"] is not None or rec["price"] is None,
                            rating_bucket=rec.get("rating_bucket"))
        for rec in self.swap_trades.get(d, []):
            book = self.books[rec["book"]]
            self._fill_swap(book, sod[book.name], lines, rec, d)

    def _execution_mid(self, inst: Instrument, d: date, settle: date, venue: str) -> float | None:
        """The day's traded mid (VWAP, or the close with ``execution="next_close"``) restated to ``settle``."""
        rows = self.marks.on(inst.isin, d, None)
        for source in self.settings.marks.fill_sources:
            for row in rows:
                if row["source"] != source:
                    continue
                if source == "traded":
                    price = row["vwap"] if self.settings.execution == "next_vwap" and row["vwap"] is not None \
                        else row["last"]
                else:
                    price = row["price"]
                s_row = row["settlement"] or self._mark_settlement(inst, d)
                if inst.kind == "corporate":
                    s_row = min(s_row, inst.last_settlement)
                if price is None:
                    y = row["yield"]
                    return _clean(inst.bond, y, settle)
                if s_row != settle:
                    return _clean(inst.bond, _yield(inst.bond, price, s_row), settle)
                return price
        return None

    def _fill_bond(self, book: _Book, inst: Instrument, sod: dict, lines: dict, *, trade_id: str, order_id: str,
                   side: int, face: float, d: date, settle: date, venue: str, mid: float, fill: float | None,
                   broker: str | None, liquidity: str | None, special: float | None, notified: float | None,
                   given_mid: bool, rating_bucket: str | None = None) -> None:
        where = f"trade {trade_id or order_id}"
        liq = liquidity or self.liquidity_on(inst, d)
        self._check_access(book, inst, side, face, d, venue, notified, special, where)
        y_mid = _yield(inst.bond, mid, settle)
        dv01 = inst.bond.dv01(y_mid, settle)
        breakdown = cost_rules.bond_trade_costs(
            self.costs, kind=inst.kind, side=side, face=face, mid=mid, dv01_per100=dv01, trade_date=d, venue=venue,
            liquidity=liq, years=cost_rules.years_between(d, inst.maturity), accrued=_accrued(inst.bond, settle),
            broker=broker, isin=inst.isin, fill=fill, rating_bucket=rating_bucket or inst.rating_bucket)
        self.unverified.update(breakdown.unverified)
        price = breakdown.fill
        book.cash -= breakdown.total
        for line, amount in (("fees", breakdown.fees), ("brokerage", breakdown.brokerage), ("gst", breakdown.gst),
                             ("stamp_duty", breakdown.stamp_duty)):
            if amount:
                _add(lines, book.name, inst.isin, line, -amount)
        if special is not None:
            book.special[inst.isin] = special
        if settle == d:
            self._settle_into_holding(book, inst, side, face, price, d, trade_id)
        else:
            auction_day = None
            if venue == "when_issued":  # the auction is the business day before the issue's T+1 settlement
                auction_day = max(d, self.calendar.add_business_days(settle, -1))
            book.pending.append(_Pending(trade_id or order_id, inst.isin, side, face, price, settle, d, venue,
                                         notified_cr=notified, auction_day=auction_day))
        book.traded_face += face
        book.traded_dv01 += face / 100 * dv01
        notes = list(breakdown.notes)
        if not given_mid:
            notes.append("no mid given: the fill is its own mid (no spread cost measured)")
        sod["fills"].append({"isin": inst.isin, "side": side, "face": face, "mid": mid, "fill": price,
                             "settlement": settle, "trade_id": trade_id})
        self.rows["fills"].append({
            "date": d, "order_id": order_id, "trade_id": trade_id, "book": book.name, "instrument": inst.isin,
            "side": "buy" if side > 0 else "sell", "face": face, "mid": mid, "fill": price, "venue": venue,
            "settlement_date": settle, "spread_cost": breakdown.spread_cost, "fees": breakdown.fees,
            "brokerage": breakdown.brokerage, "gst": breakdown.gst, "stamp_duty": breakdown.stamp_duty,
            "rules": ";".join(breakdown.rules), "status": "filled", "notes": "; ".join(notes)})

    def _check_access(self, book: _Book, inst: Instrument, side: int, face: float, d: date, venue: str,
                      notified: float | None, special: float | None, where: str) -> None:
        access = self.settings.access
        position = book.holdings.get(inst.isin, 0.0) + sum(t.side * t.face for t in book.pending
                                                            if t.isin == inst.isin)
        after = position + side * face
        # a when-issued sale is not a short sale (When Issued Directions 2018 para 4): it is held to the when-issued
        # limits below and must be covered by the auction day's close, or, where it may be carried past it, meets the
        # short-sale rules then (``_check_when_issued_close``)
        if side < 0 and after < -1e-6 and venue != "when_issued":
            self._short_sale_rules(book, inst, -after, d, special, where,
                                   f"selling {face:,.0f} of {inst.isin} would leave the book short {-after:,.0f}")
        if venue == "when_issued":
            if notified is None:
                raise ValueError(f"{where}: a when-issued trade needs notified_cr, the auction's notified amount")
            wi = sum(t.side * t.face for t in book.pending if t.isin == inst.isin and t.venue == "when_issued")
            wi += side * face
            cap = (access.when_issued_long_pct if wi > 0 else access.when_issued_short_pct) / 100 * notified * 1e7
            if abs(wi) > cap + 1e-6:
                raise ValueError(f"{where}: a when-issued position of Rs {wi / 1e7:,.2f} crore in {inst.isin} exceeds "
                                 f"{cap / 1e7:,.2f} crore (When Issued Directions 2018 para 6)")

    def _short_sale_rules(self, book: _Book, inst: Instrument, short: float, d: date, special: float | None,
                          where: str, lead: str) -> None:
        """The Short Sale Directions 2018 for a short of ``short`` rupees of face in ``inst`` on ``d``: the access
        policy's right to short (para 3), its kinds (central government dated securities, para 1(b)), the limit (the
        larger of a percentage of the stock on ``d``, when the stock table gives one, and an amount; a liquid
        security's are larger: para 4) and a special repo rate to borrow the bond at. ``lead`` says what makes the
        short; a breach is refused, naming the rule."""
        access = self.settings.access
        if not access.short_sale:
            raise ValueError(f"{where}: {lead}, and the access policy {access.name!r} allows no short sales (Short "
                             "Sale Directions 2018 para 3)")
        if inst.kind not in access.short_kinds:
            raise ValueError(f"{where}: {inst.isin} is a {inst.kind}; short sales are allowed only in "
                             f"{access.short_kinds} (central government dated securities: Short Sale Directions 2018 "
                             "para 1(b))")
        liquid = self.liquidity_on(inst, d) == "liquid"
        limit_rs = access.short_limit_liquid_rs if liquid else access.short_limit_other_rs
        pct = access.short_limit_liquid_pct if liquid else access.short_limit_other_pct
        stock = self._stock_on(inst.isin, d)
        limit = max(limit_rs, pct / 100 * stock) if stock is not None else limit_rs
        if short > limit + 1e-6:
            raise ValueError(f"{where}: a short of Rs {short / 1e7:,.2f} crore in {inst.isin} exceeds the limit of Rs "
                             f"{limit / 1e7:,.2f} crore (Short Sale Directions 2018 para 4: the larger of {pct:g}% of "
                             f"the stock and Rs {limit_rs / 1e7:,.0f} crore)")
        if special is None and inst.isin not in book.special:
            raise ValueError(f"{where}: a short of {inst.isin} needs special_repo_pct, the rate at which the bond is "
                             "borrowed in a reverse repo (CCIL's repo by security is licensed: give your own)")

    def _check_when_issued_close(self, d: date) -> None:
        """At the close of an auction day the book's when-issued short in that security must be covered (the auction
        allotment and any purchase settling on the issue date count), unless the access policy may carry it past the
        close (scheduled commercial banks and primary dealers: When Issued Directions 2018 para 4). A short carried
        past the close settles on the issue date as a short sale, so it must then meet the Short Sale Directions as an
        ordinary short does (``_short_sale_rules``: the right, the kinds, the limit against the stock on the auction
        day, a special repo rate given with the sale); a breach is refused at the close."""
        access = self.settings.access
        for name in sorted(self.books):
            book = self.books[name]
            for isin in sorted({t.isin for t in book.pending if t.venue == "when_issued" and t.auction_day == d}):
                net = book.holdings.get(isin, 0.0) + sum(t.side * t.face for t in book.pending if t.isin == isin)
                if net >= -1e-6:
                    continue
                if not access.when_issued_short_overnight:
                    raise ValueError(f"book {name}: a when-issued short of Rs {-net / 1e7:,.2f} crore in {isin} is "
                                     f"still open at the close of its auction day {d}; the access policy "
                                     f"{access.name!r} may not carry it overnight (When Issued Directions 2018 para 4: "
                                     "only scheduled commercial banks and primary dealers may): cover it in the "
                                     "auction or by a purchase settling on the issue date")
                settle = min(t.settlement for t in book.pending if t.isin == isin and t.venue == "when_issued"
                             and t.auction_day == d)
                self._short_sale_rules(book, self.instruments[isin], -net, d, None,
                                       f"book {name}: the when-issued short in {isin} carried past the close of its "
                                       f"auction day {d} is a short sale from the issue date {settle}",
                                       f"a short of {-net:,.0f} of {isin} is carried past the auction")

    def _stock_on(self, isin: str, d: date) -> float | None:
        rows = self.stock.get(isin)
        if not rows:
            return None
        k = bisect.bisect_right(rows, (d, math.inf)) - 1
        return rows[k][1] if k >= 0 else None

    def _fill_swap(self, book: _Book, sod: dict, lines: dict, rec: dict, d: date) -> None:
        access = self.settings.access
        where = f"OIS trade {rec['trade_id']}"
        if access.ois == "none":
            raise ValueError(f"{where}: the access policy {access.name!r} allows no OIS")
        if access.ois == "hedge_only" and rec["purpose"] != "hedge":
            raise ValueError(f"{where}: the access policy {access.name!r} allows OIS for hedging only (Rupee IRD "
                             "Directions 2025 para 5.4(i)); this one says purpose "
                             f"{rec['purpose']!r}")
        curve = self.curves.get("ois", d, None)
        if curve is None:
            raise ValueError(f"{where}: no OIS curve on {d} to value it (MarketData.ois_curves)")
        periods = ois_schedule(rec["effective"], rec["months"], MIBOR_OIS, self.calendar)
        mid = rec["mid"]
        if mid is None:
            mid = rec["rate"] if rec["rate"] is not None else ois_par_rate(curve, rec["effective"], rec["months"],
                                                                           MIBOR_OIS, self.calendar)
        side = 1 if rec["pay_fixed"] else -1
        probe = OisSwap(effective=rec["effective"], months=rec["months"], fixed_rate_pct=mid,
                        notional=rec["notional"], pay_fixed=rec["pay_fixed"])
        pv01 = ois_npv(curve, probe, fixings=self.fixings, calendar=self.calendar).pv01
        breakdown = cost_rules.ois_trade_costs(self.costs, side=side, notional=rec["notional"], pv01=pv01,
                                               tenor_years=rec["months"] / 12, trade_date=d, mid_rate_pct=mid,
                                               fill_rate_pct=rec["rate"])
        self.unverified.update(breakdown.unverified)
        swap = OisSwap(effective=rec["effective"], months=rec["months"], fixed_rate_pct=breakdown.fill,
                       notional=rec["notional"], pay_fixed=rec["pay_fixed"])
        book.cash -= breakdown.total
        key = f"OIS:{rec['trade_id']}"
        for line, amount in (("fees", breakdown.fees), ("gst", breakdown.gst), ("stamp_duty", breakdown.stamp_duty)):
            if amount:
                _add(lines, book.name, key, line, -amount)
        state = _SwapState(rec["trade_id"], swap, d, rec["purpose"], periods)
        book.swaps[rec["trade_id"]] = state
        sod["swap_fills"].append({"id": rec["trade_id"], "mid": mid, "probe": probe})
        self.rows["fills"].append({
            "date": d, "order_id": "", "trade_id": rec["trade_id"], "book": book.name, "instrument": key,
            "side": "pay" if rec["pay_fixed"] else "receive", "face": rec["notional"], "mid": mid,
            "fill": breakdown.fill, "venue": "otc", "settlement_date": rec["effective"],
            "spread_cost": breakdown.spread_cost, "fees": breakdown.fees, "brokerage": 0.0, "gst": breakdown.gst,
            "stamp_duty": breakdown.stamp_duty, "rules": ";".join(breakdown.rules), "status": "filled",
            "notes": "" if rec["mid"] is not None or rec["rate"] is None else
            "no mid given: the fill is its own mid (no spread cost measured)"})

    # -- back-test orders

    def _fill_orders(self, d: date, sod: dict, lines: dict) -> None:
        waiting = []
        for order in self.queue:
            if order["from"] > d:
                waiting.append(order)
                continue
            done = self._try_order(order, d, sod, lines)
            if not done:
                if order["waited"] >= self.settings.max_wait_days or order["venue"] in ("auction", "auction_nc"):
                    self._fill_row(d, order, status="cancelled",
                                   notes=f"no {'/'.join(self.settings.marks.fill_sources)} price within "
                                         f"{self.settings.max_wait_days} business day(s)" if order["venue"] not in
                                   ("auction", "auction_nc") else "no auction result for it")
                else:
                    order["waited"] += 1
                    waiting.append(order)
        self.queue = waiting

    def _try_order(self, order: dict, d: date, sod: dict, lines: dict) -> bool:
        book = self.books.setdefault(order["book"], _Book(order["book"]))
        if book.name not in sod:
            sod[book.name] = {"nav": book.nav, "holdings": dict(book.holdings), "pending": list(book.pending),
                              "flow": 0.0, "redeemed": {}, "coupons": {}, "fills": [], "swap_fills": [],
                              "settled": [], "notes": [], "rf": math.nan}
        if not order["isin"]:
            curve = self.curves.get("ois", d, None)
            if curve is None:
                return False
            eff = effective_date(d, MIBOR_OIS, self.calendar)
            par = ois_par_rate(curve, eff, order["months"], MIBOR_OIS, self.calendar)
            if order["limit"] is not None:
                h = cost_rules.cost_rule(self.costs, "half_spread_bp", kind="ois", on=d, venue="otc",
                                         years=order["months"] / 12).value
                fill = par + order["sign"] * h / 100
                if (order["sign"] > 0 and fill > order["limit"]) or (order["sign"] < 0 and fill < order["limit"]):
                    return False
            rec = {"trade_id": order["order_id"], "book": book.name, "trade_date": d, "effective": eff,
                   "months": order["months"], "rate": None, "notional": order["face"],
                   "pay_fixed": order["sign"] > 0, "mid": par, "purpose": order["purpose"], "note": ""}
            self._fill_swap(book, sod[book.name], lines, rec, d)
            self.rows["fills"][-1]["order_id"] = order["order_id"]
            return True
        inst = self.instruments[order["isin"]]
        if order["venue"] in ("auction", "auction_nc"):
            if order["auction_date"] != d:
                return False
            result = self.auctions.get((inst.isin, d))
            if result is None:
                return False
            face, why = self._auction_allotment(order, inst, result)
            if not face:
                self._fill_row(d, order, status="refused", notes=why)
                return True
            price = result["cutoff"] if order["venue"] == "auction" or result["wap"] is None else result["wap"]
            self._fill_bond(book, inst, sod[book.name], lines, trade_id="", order_id=order["order_id"],
                            side=order["sign"], face=face, d=d, settle=result["settlement"],
                            venue=order["venue"], mid=price, fill=price, broker=None, liquidity=None,
                            special=order["special"], notified=None, given_mid=True)
            if why:
                row = self.rows["fills"][-1]
                row["notes"] = "; ".join(n for n in (row["notes"], why) if n)
            return True
        try:
            settle = self._settlement(inst, d, None, order["venue"], f"order {order['order_id']}")
        except ValueError as exc:
            self._fill_row(d, order, status="refused", notes=str(exc))
            return True
        mid = self._execution_mid(inst, d, settle, order["venue"])
        if mid is None:
            return False
        if order["limit"] is not None:
            y_mid = _yield(inst.bond, mid, settle)
            h = cost_rules.cost_rule(self.costs, "half_spread_bp", kind=inst.kind, on=d, venue=order["venue"],
                                     liquidity=self.liquidity_on(inst, d),
                                     years=cost_rules.years_between(d, inst.maturity), isin=inst.isin).value
            fill = cost_rules.fill_price(mid, order["sign"], h, inst.bond.dv01(y_mid, settle))
            if (order["sign"] > 0 and fill > order["limit"]) or (order["sign"] < 0 and fill < order["limit"]):
                return False
        self._fill_bond(book, inst, sod[book.name], lines, trade_id="", order_id=order["order_id"],
                        side=order["sign"], face=order["face"], d=d, settle=settle, venue=order["venue"], mid=mid,
                        fill=None, broker=None, liquidity=None, special=order["special"], notified=None,
                        given_mid=True)
        return True

    def _auction_allotment(self, order: dict, inst: Instrument, result: dict) -> tuple[float, str]:
        """The face an auction order is allotted and a note: competitive bids fill at the cut-off up to
        ``auction_max_share`` of the amount accepted (UNVERIFIED share: the fund's bid is taken to be at the cut-off and
        within RBI's limits); non-competitive bids at the weighted average price up to Rs 2 crore a bid in a GoI dated
        auction and 1% of the notified amount in an SDL's (verified rules of RBI's notifications); a rejected auction
        allots nothing. Returns ``(0, why)`` for a refusal."""
        face = order["face"]
        notified, accepted = result.get("notified_cr"), result.get("accepted_cr")
        if accepted is not None and accepted <= 0:
            return 0.0, "the auction accepted nothing"
        if order["venue"] == "auction_nc":
            if inst.kind in ("gsec", "green"):
                limit = self.settings.auction_nc_limit_rs
                rule = f"Rs {limit / 1e7:g} crore a bid in a GoI dated auction"
            elif inst.kind == "sdl" and notified is not None:
                limit = 0.01 * notified * 1e7
                rule = "1% of the notified amount a bid in an SDL auction"
            else:
                return 0.0, f"non-competitive bids in a {inst.kind} auction are not modelled (or no notified amount)"
            if face > limit + 1e-6:
                return 0.0, f"a non-competitive bid of Rs {face / 1e7:,.2f} crore exceeds {rule}"
            return face, ""
        base = accepted if accepted is not None else notified
        if base is None:
            return 0.0, "the auction table gives no accepted or notified amount to size the allotment by"
        cap = self.settings.auction_max_share * base * 1e7
        if face > cap + 1e-6:
            return cap, (f"allotted Rs {cap / 1e7:,.2f} crore of Rs {face / 1e7:,.2f} crore: at most "
                         f"{self.settings.auction_max_share:.0%} of the amount accepted (UNVERIFIED share)")
        return face, ""

    def _fill_row(self, d: date, order: dict, *, status: str, notes: str) -> None:
        self.rows["fills"].append({
            "date": d, "order_id": order["order_id"], "trade_id": "", "book": order["book"],
            "instrument": order["isin"] or "OIS", "side": order["side"], "face": order["face"], "mid": math.nan,
            "fill": math.nan, "venue": order["venue"], "settlement_date": None, "spread_cost": 0.0, "fees": 0.0,
            "brokerage": 0.0, "gst": 0.0, "stamp_duty": 0.0, "rules": "", "status": status, "notes": notes})

    def _decide(self, d: date) -> None:
        snap = Snapshot(self, d)
        orders = self.strategy.decide(snap) or []
        nxt = self._next_business_day(d)
        for order in orders:
            if not isinstance(order, Order):
                raise TypeError(f"a strategy must return Order objects, not {type(order).__name__}")
            self.order_seq += 1
            oid = order.order_id or f"O{self.order_seq:06d}"
            side = order.side.strip().lower()
            by_line = False
            if order.isin:
                if side not in ("buy", "sell"):
                    raise ValueError(f"order {oid}: a bond order's side is buy or sell, not {order.side!r}")
                isin = order.isin.strip().upper()
                if order.venue in ("auction", "auction_nc") and order.auction_date is not None:
                    isin, by_line = self._auction_isin(order.isin, as_date(order.auction_date))
            else:
                if side not in ("pay", "receive"):
                    raise ValueError(f"order {oid}: an OIS order's side is pay or receive, not {order.side!r}")
                if not order.months:
                    raise ValueError(f"order {oid}: an OIS order needs months (its tenor)")
                isin = ""
            if not (math.isfinite(order.face) and order.face > 0):
                raise ValueError(f"order {oid}: face must be a positive number of rupees, not {order.face!r}")
            when = order.auction_date if order.venue in ("auction", "auction_nc") and order.auction_date else nxt
            entry = {"order_id": oid, "book": order.book, "isin": isin, "side": side,
                     "sign": 1 if side in ("buy", "pay") else -1, "face": float(order.face), "months": order.months,
                     "venue": order.venue, "limit": order.limit, "purpose": order.purpose,
                     "special": order.special_repo_pct, "auction_date": order.auction_date, "from": as_date(when),
                     "waited": 0, "date_decided": d, "by_line": by_line}
            refusal = self._auction_order_refusal(entry, snap.as_of) if order.venue in ("auction", "auction_nc") \
                else None
            if refusal:
                self._fill_row(d, entry, status="refused", notes=refusal)
                continue
            if isin:
                self.instrument(isin, None, f"order {oid}")
            self.queue.append(entry)

    def _auction_isin(self, name: str, day: date) -> tuple[str, bool]:
        """The ISIN an auction order of ``day`` names, and whether it named the line (``line``, such as ``new GS
        2036``) rather than the ISIN; a name that is neither comes back as given, and the order is refused."""
        code = name.strip().upper()
        if (code, day) in self.auctions:
            return code, False
        wanted = name.strip().lower()
        named = sorted(isin for (isin, when), rec in self.auctions.items()
                       if when == day and rec["line"] and rec["line"].lower() == wanted)
        return (named[0], True) if len(named) == 1 else (code, False)

    def _auction_order_refusal(self, order: dict, as_of: datetime) -> str | None:
        """Why a back-test may not bid in an auction (A5.2), or None: it must name an auction of the market data's
        table held after the decision day, whose announcement was public by the decision (``announcement_public_from``,
        required: an order cannot otherwise be checked for look-ahead), only buy, and name a line whose ISIN was not
        yet public (a new issue's, before its result) by its ``line``, as ``Snapshot.auctions`` shows it."""
        day = order["auction_date"]
        if day is None:
            return "an auction order needs auction_date"
        day = as_date(day)
        if day <= order["date_decided"]:
            return f"decided on {order['date_decided']}, not before the auction of {day} (bids close at 11:30 that day)"
        if order["sign"] < 0:
            return "an auction order buys; a sale in the auction is not possible"
        result = self.auctions.get((order["isin"], day))
        if result is None:
            return f"no auction of {order['isin']} on {day} in MarketData.auctions"
        if result["announced"] is None:
            return ("the auction table gives no announcement_public_from for it, so the order cannot be checked "
                    "against look-ahead (auctions_from_events takes it from the event ledger)")
        if result["announced"] > as_of:
            return (f"decided at {as_of:%Y-%m-%d %H:%M}, before the auction was announced "
                    f"({result['announced']:%Y-%m-%d %H:%M})")
        public = result["resulted"] is not None and result["resulted"] <= as_of
        if not order["by_line"] and not public and not self.isin_public(order["isin"], as_of):
            name = f"its line {result['line']!r}" if result["line"] else \
                "its line (the auction table has no line column)"
            return (f"{order['isin']} was not issued by the decision at {as_of:%Y-%m-%d %H:%M} and no result of it was "
                    f"public: a new issue's ISIN is not known before its result (Snapshot.auctions shows it blank); "
                    f"name {name}, or give the master's issue_date if it was issued")
        return None

    # -- OIS cash

    def _swap_settlements(self, book: _Book, p: date | None, d: date, sod: dict) -> None:
        sod["swap_cash"] = {}
        if p is None:
            return
        for tid in sorted(book.swaps):
            state = book.swaps[tid]
            if state.trade_date >= d or not state.alive:
                continue
            swap = state.swap
            x = x_unr = 0.0
            for period in state.periods:
                if not p < period.end <= d:
                    continue
                try:
                    comp = compound_overnight(self.fixings, period.start, period.end, calendar=self.calendar,
                                              conventions=swap.conventions)
                except ValueError as exc:
                    raise ValueError(f"OIS {tid}: the period {period.start} to {period.end}: {exc}") from None
                amounts = settlement_amounts(swap.notional, swap.fixed_rate_pct, comp, conventions=swap.conventions)
                to_payer = {"fixed receiver": 1.0, "fixed payer": -1.0}.get(amounts["payer"], 0.0)
                sign = 1.0 if swap.pay_fixed else -1.0
                x += sign * to_payer * amounts["net_settlement"]
                fixed = swap.notional * swap.fixed_rate_pct * comp.days / (100 * swap.conventions.year_days)
                x_unr += sign * (swap.notional * (comp.factor - 1.0) - fixed)
            if x or x_unr:
                book.cash += x
                sod["swap_cash"][tid] = (x, x_unr)

    # -- funding for the night

    def _margin_rate(self, d: date) -> float:
        pol = self.settings.funding
        if pol.margin_pct is not None:
            return float(pol.margin_pct)
        rule = cost_rules.cost_rule(self.costs, "treps_margin_pct", kind="treps", on=d)
        if rule.status == "U" and rule.origin == "default":
            self.unverified.add(rule.rule_id)
        return rule.value

    def _haircut(self, inst: Instrument, d: date, where: str) -> float:
        if inst.kind in TERMS_KINDS:  # not TREPS collateral here: market repo with a haircut row, else equity-funded
            if self.haircuts is None:
                return 100.0
            try:
                return self.haircuts.get(inst, d, self.liquidity_on(inst, d))
            except ValueError:
                return 100.0
        if self.haircuts is None:
            raise ValueError(f"{where}: no haircut table (MarketData.haircuts, the user's dated table: "
                             "funding.read_haircuts); no default haircut is assumed")
        return self.haircuts.get(inst, d, self.liquidity_on(inst, d))

    def _fund(self, book: _Book, d: date, sod: dict, lines: dict) -> None:
        pol = self.settings.funding
        access = self.settings.access
        nxt = self._next_business_day(d)
        nights = (nxt - d).days
        where = f"book {book.name} on {d}"
        lent: dict[str, tuple[float, float]] = {}
        longs = []
        for isin in sorted(book.holdings):
            face = book.holdings[isin]
            inst = self.instruments[isin]
            dirty = book.dirty_prev.get(isin)
            if dirty is None:
                continue
            value = face / 100 * dirty
            if face < 0:
                h = self._haircut(inst, d, f"{where}: the short in {isin}")
                rate = book.special.get(isin)
                if rate is None:
                    raise ValueError(f"{where}: the short in {isin} has no special repo rate")
                lent[isin] = ((1 - h / 100) * -value, rate)
            else:
                longs.append((inst, face, value))
        lent_total = sum(a for a, _ in lent.values())
        parts: dict[str, float] = {}
        bases: dict[str, float] = {}
        equity = margin = charges = 0.0
        limit = math.nan
        notes: list[str] = []
        if pol.sizing == "fixed":
            cuts = [self._haircut(inst, d, where) for inst, _, _ in longs] if longs else []
            want = sum((1 - h / 100) * face / 100 * book.entry.get(inst.isin, value * 100 / face)
                       for (inst, face, value), h in zip(longs, cuts))
            equity_fixed = sum(h / 100 * face / 100 * book.entry.get(inst.isin, value * 100 / face)
                               for (inst, face, value), h in zip(longs, cuts))
        else:
            want = max(0.0, lent_total - book.cash)
            equity_fixed = 0.0
            if want > 0 and pol.lot_rs:
                want = math.ceil(want / pol.lot_rs - 1e-12) * pol.lot_rs
        if want > 0:
            cuts = [self._haircut(inst, d, where) for inst, _, _ in longs]
            caps = [(1 - h / 100) * value for (_, _, value), h in zip(longs, cuts)]
            bases = {inst.isin: c for (inst, _, _), c in zip(longs, caps)}
            on_treps = access.repo and access.treps and pol.segment == "triparty"
            # a share is the part of a bond's lendable value TREPS takes; without TREPS all of it is market repo
            shares = [1.0 if on_treps else 0.0] * len(longs)
            if on_treps:
                eligible = [k for k, (inst, _, _) in enumerate(longs) if treps_class(inst.kind, self.liquidity_on(
                    inst, d)) != "ineligible"]
                for k in range(len(longs)):
                    if k not in eligible:
                        shares[k] = 0.0
                if pol.treps_caps and eligible:
                    got, cap_notes = treps_shares([(longs[k][0].kind, self.liquidity_on(longs[k][0], d), caps[k])
                                                   for k in eligible], d)
                    for k, share in zip(eligible, got):
                        shares[k] = share
                    notes.extend(cap_notes)
            if not access.repo:
                cap_main = cap_excess = 0.0
                notes.append(f"the access policy {access.name!r} allows no repo")
            elif on_treps:
                cap_main = sum(s * c for s, c in zip(shares, caps))
                cap_excess = sum((1 - s) * c for s, c in zip(shares, caps))
            else:
                cap_main, cap_excess = 0.0, sum(caps)
            main_rate = self._rate_table(pol.segment).rate_on(d, pol.projection)[0] if cap_main else 0.0
            growth = 1 + (main_rate + pol.spread_bp / 100) * nights / 36500
            m = self._margin_rate(d) / 100 if on_treps else 0.0
            for _ in range(3):
                b_main = min(want, cap_main / (1 + m * growth)) if cap_main else 0.0
                b_excess = min(want - b_main, cap_excess)
                over = want - b_main - b_excess
                charges = 0.0
                if b_main:
                    seg = "treps" if on_treps else "market_repo"
                    face_main = self._face_share(longs, shares, caps, b_main, cap_main, main=True)
                    cb = cost_rules.repo_deal_costs(self.costs, segment=seg, face=face_main, cash=b_main,
                                                    repayment=b_main * growth, deal_date=d)
                    charges += cb.total
                    self.unverified.update(cb.unverified)
                if b_excess:
                    excess_rate = self._rate_table(pol.excess_segment).rate_on(d, pol.projection)[0]
                    face_x = self._face_share(longs, shares, caps, b_excess, cap_excess, main=False)
                    cb = cost_rules.repo_deal_costs(
                        self.costs, segment="market_repo", face=face_x, cash=b_excess,
                        repayment=b_excess * (1 + (excess_rate + pol.spread_bp / 100) * nights / 36500), deal_date=d)
                    charges += cb.total
                    self.unverified.update(cb.unverified)
                if pol.sizing == "fixed" or book.cash + want - lent_total - charges >= -1e-9 or not pol.lot_rs:
                    break
                want += pol.lot_rs
            limit = cap_main / (1 + m * growth) + cap_excess if cap_main else cap_excess
            if over > 1e-6:
                if pol.on_shortfall == "raise":
                    why = (f"the access policy {access.name!r} allows no repo (RBI Repo Directions 2025 para 4(1)(b), "
                           "(e): make a variant with repo=True if the fund is listed or approved)" if not access.repo
                           else "haircut-adjusted collateral at the previous close, less the TREPS margin")
                    unlent = [inst.isin for (inst, _, _), h in zip(longs, cuts)
                              if inst.kind in TERMS_KINDS and h >= 100]
                    if unlent:
                        why += (f"; {', '.join(unlent)} lend nothing: corporate bonds and FRBs are not TREPS "
                                "collateral here, and without a haircut row they are not lent against in market repo "
                                "either (equity-funded)")
                    raise ValueError(f"funding_shortfall: {where} needs Rs {want:,.2f} for the night but can borrow Rs "
                                     f"{want - over:,.2f} ({why}); add capital or pass "
                                     "FundingPolicy(on_shortfall='overdraft', overdraft_rate_pct=...)")
                parts["overdraft"] = over
                notes.append(f"Rs {over:,.2f} beyond the borrowing limit funded at the overdraft rate "
                             f"{pol.overdraft_rate_pct:g}%")
            if b_main:
                parts["main"] = b_main
            if b_excess:
                parts["excess"] = b_excess
            margin = m * b_main * growth
            weighted = sum(caps)
            if pol.sizing == "fixed":
                equity = equity_fixed
            elif weighted:
                equity = (b_main + b_excess) * sum(h / 100 * v for (_, _, v), h in zip(longs, cuts)) / weighted
            if charges:
                book.cash -= charges
                _add(lines, book.name, "", "repo_charges", -charges)
        borrowed = sum(parts.values())
        book.cash += borrowed - lent_total
        book.deal = _Deal(d, nxt, parts, lent, book.cash, equity, margin, pol.sizing == "fixed", bases)
        if borrowed or lent_total:
            first = min(parts) if parts else None  # the night's charges on one row only, whatever the parts
            for part, amount in sorted(parts.items()):
                segment = {"main": pol.segment, "excess": pol.excess_segment, "overdraft": "overdraft"}[part]
                rate = (float(pol.overdraft_rate_pct) if part == "overdraft" else
                        self._rate_table(segment).rate_on(d, pol.projection)[0] + pol.spread_bp / 100)
                self.rows["funding"].append({
                    "date": d, "book": book.name, "segment": segment, "borrowed": amount, "rate_pct": rate,
                    "repay_date": nxt, "interest": amount * rate * nights / 36500,
                    "charges": charges if part == first else 0.0, "limit": limit, "haircut_equity": equity,
                    "margin": margin if part == "main" else 0.0, "lent_short": lent_total, "idle_cash": book.cash,
                    "projected_days": int(d > self._rate_table(segment).last) if part != "overdraft" else 0,
                    "notes": "; ".join(notes)})
            if lent_total and not parts:
                self.rows["funding"].append({
                    "date": d, "book": book.name, "segment": "reverse_repo", "borrowed": 0.0, "rate_pct": math.nan,
                    "repay_date": nxt, "interest": 0.0, "charges": 0.0, "limit": limit, "haircut_equity": 0.0,
                    "margin": 0.0, "lent_short": lent_total, "idle_cash": book.cash, "projected_days": 0,
                    "notes": "; ".join(notes)})

    @staticmethod
    def _face_share(longs, shares, caps, amount: float, capacity: float, *, main: bool) -> float:
        """Face of the collateral behind ``amount`` of a segment's borrowing (for CCIL's per-crore charges)."""
        if not capacity:
            return 0.0
        face = sum((s if main else 1 - s) * f for (_, f, _), s in zip(longs, shares))
        return face * amount / capacity

    # -- marks

    def _marks_of_day(self, d: date, t_val: datetime, books: list[_Book], sod: dict) -> None:
        wanted: dict[str, float | None] = {}
        for book in books:
            s = sod[book.name]
            for isin in s["holdings"]:
                if isin not in s["redeemed"]:
                    wanted.setdefault(isin, None)
            for trade in s["pending"]:
                wanted.setdefault(trade.isin, None)
            for isin in book.holdings:
                wanted.setdefault(isin, None)
            for trade in book.pending:
                wanted.setdefault(trade.isin, None)
            for fill in s["fills"]:
                if wanted.get(fill["isin"]) is None:
                    wanted[fill["isin"]] = (fill["fill"], fill["settlement"])
        self.today_marks = {}
        self._curve_cache: dict[tuple, Any] = {}
        self._moves_today: dict[str, dict] = {}
        self._sizes_today: dict[tuple, dict] = {}
        for isin in sorted(wanted):
            self.today_marks[isin] = self._mark(self.instruments[isin], d, t_val, self.prev_marks.get(isin),
                                                wanted[isin])

    def _reference_curve(self, inst: Instrument, d: date, v: date, t_val: datetime):
        """``(curve seen from v, the curve as given)`` of the bond's reference curve of mark date ``d`` public by
        ``t_val``, or ``(None, None)``."""
        name = self.settings.attribution.reference_map.get(inst.kind)
        if not name:
            return None, None
        key = (name, d, v)
        if key not in self._curve_cache:
            raw = self.curves.get(name, d, t_val)
            if raw is None or raw.valuation_date > v:
                self._curve_cache[key] = (None, None)
            else:
                self._curve_cache[key] = (seen_from(raw, v), raw)
        return self._curve_cache[key]

    def _restate(self, inst: Instrument, row: dict, d: date, v: date) -> tuple[float, float]:
        s_row = row["settlement"] or self._mark_settlement(inst, row["date"], flag=d)
        if s_row >= inst.maturity or (inst.kind == "corporate" and s_row > inst.last_settlement):
            s_row = v
        if row["price"] is None:
            y = row["yield"]
            return _clean(inst.bond, y, v), y
        if s_row == v:
            return row["price"], _yield(inst.bond, row["price"], v)
        y = _yield(inst.bond, row["price"], s_row)
        return _clean(inst.bond, y, v), y

    def _held_settlement(self, inst: Instrument, row: dict, d: date) -> date:
        """The settlement the clean price ``_held_clean`` holds is for."""
        if row["price"] is None:
            s_row = row["settlement"] or self._mark_settlement(inst, row["date"], flag=d)
            return min(s_row, inst.last_settlement if inst.kind == "corporate" else inst.maturity - timedelta(days=1))
        s_row = row["settlement"] or self._mark_settlement(inst, row["date"], flag=d)
        return min(s_row, inst.last_settlement) if inst.kind == "corporate" else s_row

    def _held_clean(self, inst: Instrument, row: dict, d: date) -> float:
        if row["price"] is not None:
            return row["price"]
        s_row = row["settlement"] or self._mark_settlement(inst, row["date"], flag=d)
        return _clean(inst.bond, row["yield"], min(s_row, inst.last_settlement if inst.kind == "corporate"
                                                   else inst.maturity - timedelta(days=1)))

    def _age(self, observed: date, d: date) -> int:
        if observed >= d:
            return 0
        return len(self.calendar.business_days(observed + timedelta(days=1), d))

    def _last_residual(self, isin: str, v: date) -> tuple[date, float] | None:
        best = self.last_resid.get(isin)
        rows = self.history.get(isin)
        if rows:
            k = bisect.bisect_left(rows, (v, -math.inf)) - 1
            if k >= 0 and (best is None or rows[k][0] > best[0]):
                best = rows[k]
        return best

    def _mark(self, inst: Instrument, d: date, t_val: datetime, prev: MarkState | None, fill_hint) -> MarkState:
        s_std = self._mark_settlement(inst, d)
        if inst.kind == "corporate":  # past the redemption record date nothing settles: the mark is restated to today,
            # as it is when a partial redemption's record date falls between today and the standard settlement
            straddled = s_std <= inst.last_settlement and self._principal_straddled(inst, d, s_std)
            v = s_std if s_std <= inst.last_settlement and not straddled else d
        else:
            v = s_std if s_std < inst.maturity else d
        curve, raw, m = None, None, math.nan
        if v == s_std:
            curve, raw = self._reference_curve(inst, d, v, t_val)
            if curve is not None:
                try:
                    m = model_yield(curve, inst.bond, v, flows=self._bond_flows(inst, v))
                except ValueError:
                    curve, raw, m = None, None, math.nan
        rows = self.marks.on(inst.isin, d, t_val)
        for source in self.settings.marks.order:
            if source in MARK_SOURCES:
                row = next((r for r in rows if r["source"] == source), None)
                if row is None:
                    continue
                clean, y = self._restate(inst, row, d, v)
                if curve is not None:
                    self.last_resid[inst.isin] = (v, y - m)
                return MarkState(d, v, clean, y, source, curve, m, 0, d, raw)
            if source == "model":
                if curve is None:
                    continue
                resid = self._last_residual(inst.isin, v)
                if resid is None:
                    continue
                y = m + resid[1]
                return MarkState(d, v, _clean(inst.bond, y, v), y, "model", curve, m,
                                 self._age(resid[0], v), resid[0], raw)
            if source == "stale":
                row = self.marks.latest_before(inst.isin, d, t_val)
                if row is None and prev is None:
                    continue
                if row is not None and (prev is None or prev.observed is None or row["date"] > prev.observed):
                    clean, observed = self._held_clean(inst, row, d), row["date"]
                    held = self._held_settlement(inst, row, d)
                else:
                    clean, observed = prev.clean, prev.observed or prev.date
                    held = prev.settlement
                if fill_hint is not None and observed < d:
                    continue  # the fund's own fill of today is newer than any mark held: it values the position
                if inst.kind == "corporate" and self._principal_straddled(inst, held, v):
                    # a partial redemption paid since the price held: hold its yield instead (the clean price falls
                    # by the principal at its record date), restating it to today's settlement
                    clean = _clean(inst.bond, _yield(inst.bond, clean, held), v)
                y = _yield(inst.bond, clean, v)
                return MarkState(d, v, clean, y, "stale", curve, m, self._age(observed, d), observed, raw)
        if fill_hint is not None:
            price, settle = fill_hint
            clean = price if settle == v else _clean(inst.bond, _yield(inst.bond, price, settle), v)
            return MarkState(d, v, clean, _yield(inst.bond, clean, v), "fill", curve, m, 0, d, raw)
        raise ValueError(f"no mark for {inst.isin} on {d} public by {t_val}: give a traded or valuation mark, or a "
                         "reference curve and a residual for a model mark")

    def _moves(self, isin: str, inst: Instrument) -> dict:
        if isin not in self._moves_today:
            prev, today = self.prev_marks.get(isin), self.today_marks.get(isin)
            if prev is None or today is None:
                moves = {"have_curve": False, "factors": {}}
            else:
                sizes = None
                st = self.settings.attribution
                if prev.curve is not None and today.curve is not None:
                    key = (id(prev.base_curve), id(today.base_curve), prev.settlement, today.settlement)
                    if key not in self._sizes_today:
                        roll = roll_reference(prev, today, st.roll_basis)
                        report: dict = {}
                        self._sizes_today[key] = factor_moves(today.curve, roll, nodes=st.nodes,
                                                              basis=st.factor_basis, report=report)
                        self._note_factor_fit(report, today.date)
                    sizes = self._sizes_today[key]
                flows = self._bond_flows(inst, today.settlement) if today.curve is not None else None
                moves = _yield_moves(inst.bond, prev, today, st, flows=flows, sizes=sizes)
            self._moves_today[isin] = moves
        return self._moves_today[isin]

    def _note_factor_fit(self, report: dict, d: date) -> None:
        """Count the days on which fewer curve nodes than factors were usable (a short money-market curve)."""
        if report.get("fitted", 3) < len(self.settings.attribution.curve_lines) - 1 and \
                self.settings.attribution.factor_basis == "legendre3":
            self.short_fits.add(d)

    def _bond_flows(self, inst: Instrument, s: date) -> tuple[np.ndarray, np.ndarray]:
        key = (inst.isin, s) if inst.known is None else (inst.isin, s, inst.version)
        if key not in self._flow_cache:
            if len(self._flow_cache) > 50_000:
                self._flow_cache.clear()
            self._flow_cache[key] = _flows(inst.bond, s)
        return self._flow_cache[key]

    # -- valuation, attribution, NAV

    def _component(self, book: _Book, inst: Instrument, units: float, c_prev: float, v: date, moves: dict,
                   lines: dict, acc: dict) -> None:
        prev, today = self.prev_marks[inst.isin], self.today_marks[inst.isin]
        y0, y3 = prev.yield_pct, today.yield_pct
        clean3 = today.clean if v == today.settlement else _clean(inst.bond, y3, v)
        out = _market_lines(units, y0, y3, c_prev, _risk(inst.bond, y0, v), _clean(inst.bond, y0, v), clean3,
                            moves, self.settings.attribution)
        acc["move"] += abs(out.pop("_move"))
        acc["unexplained"] += out["unexplained"]
        for line, amount in out.items():
            _add(lines, book.name, inst.isin, line, amount)

    def _value(self, book: _Book, p: date | None, d: date, t_val: datetime, sod: dict, lines: dict) -> None:
        st = self.settings.attribution
        isins = sorted(set(sod["holdings"]) | {t.isin for t in sod["pending"]} | {f["isin"] for f in sod["fills"]}
                       | set(book.holdings) | {t.isin for t in book.pending})
        bonds = forwards = gross_long = gross_short = dv01_total = 0.0
        buckets = dict.fromkeys(BUCKETS, 0.0)
        positions = []
        for isin in isins:
            inst = self.instruments[isin]
            today = self.today_marks.get(isin)
            acc = {"move": 0.0, "unexplained": 0.0}
            h0 = sod["holdings"].get(isin, 0.0)
            if p is not None and isin in self.prev_marks and today is not None:
                moves = self._moves(isin, inst)
                if h0 and isin not in sod["redeemed"]:
                    ai_p = _accrued(inst.bond, p)
                    _add(lines, book.name, isin, "accrual", h0 / 100 * (
                        _accrued(inst.bond, d) - ai_p + sod["coupons"].get(isin, 0.0)))
                    if isin in self._changed:  # the accrued interest of the close, on today's terms as known
                        _add(lines, book.name, isin, RESET_LINE, h0 / 100 * (ai_p - book.ai_prev[isin]))
                    principal = sod.get("principal", {}).get(isin, 0.0)
                    if principal:  # an amortisation entitled: its cash offsets the clean price's fall
                        _add(lines, book.name, isin, "pull_to_par", h0 / 100 * principal)
                    self._component(book, inst, h0 / 100, book.clean_prev[isin], today.settlement, moves, lines, acc)
                for trade in sod["pending"]:
                    if trade.isin != isin:
                        continue
                    units = trade.side * trade.face / 100
                    v = today.settlement if trade.settlement == d else trade.settlement
                    self._component(book, inst, units, trade.clean_prev, v, moves, lines, acc)
            if today is not None:
                for fill in sod["fills"]:
                    if fill["isin"] != isin:
                        continue
                    self._trade_lines(book, inst, today, fill, d, lines)
            if acc["move"] or acc["unexplained"]:
                tol = max(st.unexplained_rel * acc["move"], st.unexplained_abs_rs)
                if abs(acc["unexplained"]) > tol:
                    self.rows["checks"].append({"date": d, "book": book.name, "instrument": isin,
                                                "check": "attribution_check", "value": acc["unexplained"],
                                                "tolerance": tol, "status": "flag",
                                                "detail": f"unexplained beyond max({st.unexplained_rel:g} x |move|, "
                                                          f"Rs {st.unexplained_abs_rs:g}); mark {today.source}"})
            face = book.holdings.get(isin, 0.0)
            if face and today is not None:
                ai = _accrued(inst.bond, d)
                value = face / 100 * (today.clean + ai)
                dv01 = face / 100 * inst.bond.dv01(today.yield_pct, today.settlement)
                book.clean_prev[isin] = today.clean
                book.dirty_prev[isin] = today.clean + ai
                book.ai_prev[isin] = ai
                bonds += value
                gross_long += max(value, 0.0)
                gross_short += max(-value, 0.0)
                dv01_total += dv01
                bucket = tenor_bucket(inst.maturity, d)
                buckets[bucket] = buckets.get(bucket, 0.0) + dv01
                positions.append(self._position_row(book, inst, d, "settled", face, today.settlement, today, ai,
                                                    value, dv01, bucket, self._terms_note(inst, today)))
            for trade in book.pending:
                if trade.isin != isin or today is None:
                    continue
                clean_s = _clean(inst.bond, today.yield_pct, trade.settlement)
                units = trade.side * trade.face / 100
                value = units * (clean_s - trade.price)
                trade.clean_prev = clean_s
                dv01 = units * inst.bond.dv01(today.yield_pct, trade.settlement)
                forwards += value
                dv01_total += dv01
                bucket = tenor_bucket(inst.maturity, d)
                buckets[bucket] = buckets.get(bucket, 0.0) + dv01
                positions.append(self._position_row(book, inst, d, "pending", trade.side * trade.face,
                                                    trade.settlement, today, 0.0, value, dv01, bucket,
                                                    f"trade {trade.trade_id} at {trade.price:.6f}"))
        ois_value, ois_notional, ois_dv01 = self._swap_values(book, p, d, t_val, sod, lines, positions, buckets,
                                                              dv01_total)
        dv01_total += ois_dv01
        for isin, since in sorted(book.short_since.items()):
            months = self.settings.access.short_cover_months
            if d > _add_months(since, months):
                self.rows["checks"].append({"date": d, "book": book.name, "instrument": isin, "check": "short_cover",
                                            "value": (d - since).days, "tolerance": months, "status": "flag",
                                            "detail": f"a short open since {since} is not covered within {months} "
                                                      "months (Short Sale Directions 2018 para 5(b))"})
        receivables = sum(r.amount for r in book.receivables)
        borrowed = sum(book.deal.parts.values()) if book.deal else 0.0
        lent = sum(a for a, _ in book.deal.lent.values()) if book.deal else 0.0
        nav = book.cash + bonds + forwards + ois_value + receivables - borrowed + lent
        flow = sod["flow"]
        base = sod["nav"] + flow
        rf = sod.get("rf", math.nan)
        if not math.isnan(rf) and p is not None:
            bench = base * rf
            if st.basis == "excess_of_cash":
                _add(lines, book.name, "", "cash_benchmark", -bench)
            else:
                _add(lines, book.name, "", "memo_cash_benchmark", bench)
        change = nav - sod["nav"] - flow
        summed = sum(v for (b, _, line), v in lines.items() if b == book.name and line not in MEMO_LINES)
        expected = change - (base * rf if st.basis == "excess_of_cash" and not math.isnan(rf) and p is not None
                             else 0.0)
        gross = abs(book.cash) + gross_long + gross_short + abs(forwards) + abs(ois_value) + borrowed + lent + abs(
            receivables)
        tol = IDENTITY_RTOL * (gross + 1)
        diff = summed - expected
        self.rows["checks"].append({"date": d, "book": book.name, "instrument": "", "check": "identity",
                                    "value": diff, "tolerance": tol, "status": "ok" if abs(diff) <= tol else "breach",
                                    "detail": "sum of the non-memo lines less the NAV change net of flows"
                                    + (" and the risk-free benchmark" if st.basis == "excess_of_cash" else "")})
        ret = (nav - sod["nav"] - flow) / base if base > 0 else math.nan
        if math.isfinite(ret):
            book.index *= 1 + ret
        book.nav = nav
        projected = d.year not in self.strict_years or d in self.projected_days
        self.rows["nav"].append({
            "date": d, "book": book.name, "bonds": bonds, "forwards": forwards, "ois": ois_value, "cash": book.cash,
            "repo_borrowed": borrowed, "lent": lent, "receivables": receivables, "nav": nav, "flow": flow,
            "pnl": change, "return": ret, "index": book.index, "rf_return": rf, "gross_long": gross_long,
            "gross_short": gross_short, "ois_notional": ois_notional,
            "leverage": (gross_long + gross_short) / nav if nav > 0 else math.nan,
            "repo_leverage": borrowed / nav if nav > 0 else math.nan, "dv01": dv01_total,
            **{f"dv01_{b}": buckets.get(b, 0.0) for b in BUCKETS}, "calendar_projected": projected,
            "notes": "; ".join(sod["notes"])})
        for row in positions:
            row["calendar_projected"] = projected
        self.rows["positions"].extend(positions)
        self._positions_today[(d, book.name)] = positions

    def _terms_note(self, inst: Instrument, mark: MarkState) -> str:
        """What a corporate bond's or FRB's position row says of its terms as known (blank for other kinds)."""
        if inst.known is None:
            return ""
        bond, s, notes = inst.bond, mark.settlement, []
        if isinstance(bond, GoiFloatingRateBond):  # the coupon accruing on the position's date
            status = bond.rate_status(mark.date)
            notes.append(f"coupon {bond.coupon_rate(mark.date):.4f}% {status}")
            if status == "projected":
                self.frb_projected.setdefault(inst.isin, mark.date)
            return "; ".join(notes)
        if bond.is_ex_interest(s):
            notes.append(f"the mark's settlement {s} is ex-interest")
        face = bond.outstanding_face(s)
        if face < 100:
            notes.append(f"outstanding {face:g} per 100 original face (prices per 100 original)")
        if bond.maturity != self._base_maturity[inst.isin]:
            notes.append(f"redeemed on {bond.maturity} at {bond.redemption:g} (an exercise public)")
        if bond.rate_steps:
            notes.append(f"coupon {bond.rate_on(s):g}% from its steps")
        return "; ".join(notes)

    def _trade_lines(self, book: _Book, inst: Instrument, today: MarkState, fill: dict, d: date,
                     lines: dict) -> None:
        units = fill["side"] * fill["face"] / 100
        s, v = fill["settlement"], today.settlement
        if s == v:
            mid_v, fill_v = fill["mid"], fill["fill"]
        else:
            mid_v = _clean(inst.bond, _yield(inst.bond, fill["mid"], s), v)
            fill_v = _clean(inst.bond, _yield(inst.bond, fill["fill"], s), v)
        trading = units * (today.clean - mid_v)
        spread = units * (mid_v - fill_v)
        if s == d:
            total = units * (today.clean - fill["fill"])
        else:
            total = units * (_clean(inst.bond, today.yield_pct, s) - fill["fill"])
        _add(lines, book.name, inst.isin, "trading", trading)
        _add(lines, book.name, inst.isin, "spread_cost", spread)
        _add(lines, book.name, inst.isin, "pull_to_par", total - trading - spread)

    def _position_row(self, book: _Book, inst: Instrument, d: date, state: str, face: float, settle: date,
                      mark: MarkState, accrued: float, value: float, dv01: float, bucket: str, notes: str) -> dict:
        warn = mark.source in ("model", "stale") and mark.age_days > self.settings.marks.max_stale_days
        if warn:
            self.rows["checks"].append({"date": d, "book": book.name, "instrument": inst.isin,
                                        "check": "stale_warning", "value": mark.age_days,
                                        "tolerance": self.settings.marks.max_stale_days, "status": "flag",
                                        "detail": f"{mark.source} mark {mark.age_days} business days old"})
        extrapolated = False
        if mark.curve is not None:
            t = (inst.maturity - mark.settlement).days / _YEAR
            inside = getattr(mark.curve, "in_data_range", None)
            if inside is not None:
                extrapolated = not bool(np.asarray(inside(t)).reshape(-1)[0])
            elif isinstance(mark.curve, OisCurve):
                extrapolated = bool(mark.curve.extrapolated(t))
        haircut_pct = math.nan
        if self.haircuts is not None and face > 0:
            try:
                haircut_pct = self.haircuts.get(inst, d, self.liquidity_on(inst, d))
            except ValueError:
                haircut_pct = math.nan
        funded = math.nan  # the night's borrowing pro rata of each long's lendable value at the deal (A2.6)
        if book.deal is not None and state == "settled" and face > 0 and book.deal.parts:
            base = book.deal.bases.get(inst.isin)
            weight = math.fsum(book.deal.bases[k] for k in sorted(book.deal.bases))
            if base is not None and weight > 0:
                funded = sum(book.deal.parts.values()) * base / weight
        return {"date": d, "book": book.name, "isin": inst.isin, "kind": inst.kind, "state": state, "face": face,
                "settlement_date": settle, "clean_mark": mark.clean, "mark_source": mark.source,
                "mark_age_days": mark.age_days, "yield_pct": mark.yield_pct, "accrued": accrued, "value": value,
                "dv01": dv01, "bucket": bucket, "liquidity": self.liquidity_on(inst, d) or "",
                "haircut_pct": haircut_pct, "funded": funded, "stale_warning": warn, "extrapolated": extrapolated,
                "calendar_projected": False, "notes": notes}

    def _swap_values(self, book: _Book, p: date | None, d: date, t_val: datetime, sod: dict, lines: dict,
                     positions: list, buckets: dict, bond_dv01: float) -> tuple[float, float, float]:
        st = self.settings.attribution
        total = notional = dv01_total = 0.0
        hedge_dv01 = 0.0
        new_ids = {f["id"]: f for f in sod["swap_fills"]}
        for tid in sorted(book.swaps):
            state = book.swaps[tid]
            swap = state.swap
            key = f"OIS:{tid}"
            curve = self.curves.get("ois", d, t_val)
            source, observed = "curve", d
            if curve is None:
                if state.curve is None:
                    raise ValueError(f"OIS {tid}: no OIS curve on {d} public by {t_val} and none before to hold")
                curve, source, observed = rolled(state.curve, d), "stale", state.observed
            alive = state.periods[-1].end > d
            v1 = ois_npv(curve, swap, fixings=self.fixings, calendar=self.calendar) if alive else None
            n1 = v1.npv if v1 is not None else 0.0
            if tid in new_ids:
                probe = new_ids[tid]["probe"]
                n_mid = ois_npv(curve, probe, fixings=self.fixings, calendar=self.calendar).npv
                _add(lines, book.name, key, "trading", n_mid)
                _add(lines, book.name, key, "spread_cost", n1 - n_mid)
                if state.purpose == "hedge":
                    hedge_dv01 += v1.dv01
            elif p is not None:
                x, x_unr = sod["swap_cash"].get(tid, (0.0, 0.0))
                sign = 1.0 if swap.pay_fixed else -1.0
                acc1 = (v1.accrued_float - v1.accrued_fixed) if v1 is not None else 0.0
                carry = sign * (acc1 - (state.accrued_float - state.accrued_fixed)) + x_unr
                _add(lines, book.name, key, "ois_carry", carry)
                names = st.ois_curve_lines[:-1]
                if v1 is None:
                    _add(lines, book.name, key, "ois_roll_down", n1 - state.npv + x_unr - carry)
                else:
                    base = state.curve
                    roll_curve = rolled(base, d) if st.roll_basis == "constant_curve" else seen_from(base, d)
                    n_roll = ois_npv(roll_curve, swap, fixings=self.fixings, calendar=self.calendar).npv
                    _add(lines, book.name, key, "ois_roll_down", n_roll - state.npv + x_unr - carry)
                    factor_total = 0.0
                    if source == "curve":
                        sizes = self._ois_sizes(curve, roll_curve, d)
                        shifted = factor_curves(roll_curve, sizes, nodes=st.nodes, basis=st.factor_basis)
                        for line, factor in zip(names, sizes):
                            if not sizes[factor]:
                                continue
                            value = ois_npv(shifted[factor], swap, fixings=self.fixings,
                                            calendar=self.calendar).npv - n_roll
                            _add(lines, book.name, key, line, value)
                            factor_total += value
                    _add(lines, book.name, key, "ois_curve_other", n1 - n_roll - factor_total)
                _add(lines, book.name, key, "ois_settlement_rounding", x - x_unr)
            state.npv = n1
            state.accrued_fixed = v1.accrued_fixed if v1 is not None else 0.0
            state.accrued_float = v1.accrued_float if v1 is not None else 0.0
            state.curve = curve if source == "curve" else state.curve
            state.observed = observed
            state.alive = alive
            total += n1
            if alive:
                notional += swap.notional
                dv01_total += v1.dv01
                bucket = tenor_bucket(state.periods[-1].end, d)
                buckets[bucket] = buckets.get(bucket, 0.0) + v1.dv01
                age = self._age(observed, d) if observed else 0
                warn = source == "stale" and age > self.settings.marks.max_stale_days
                positions.append({
                    "date": d, "book": book.name, "isin": key, "kind": "ois", "state": "ois",
                    "face": swap.notional if not swap.pay_fixed else -swap.notional,
                    "settlement_date": swap.effective, "clean_mark": swap.fixed_rate_pct, "mark_source": source,
                    "mark_age_days": age, "yield_pct": v1.fair_rate_pct, "accrued": v1.accrued_fixed
                    - v1.accrued_float if not swap.pay_fixed else v1.accrued_float - v1.accrued_fixed,
                    "value": n1, "dv01": v1.dv01, "bucket": bucket, "liquidity": "", "haircut_pct": math.nan,
                    "funded": math.nan, "stale_warning": warn,
                    "extrapolated": bool(isinstance(curve, OisCurve) and curve.extrapolated(
                        curve.year_fraction(state.periods[-1].end))), "calendar_projected": False,
                    "notes": f"{'pay' if swap.pay_fixed else 'receive'} {swap.fixed_rate_pct:.6f}% for "
                             f"{swap.months} months from {swap.effective}"})
        live = [r for r in positions if r["kind"] == "ois"]
        if live and self.settings.access.ois == "hedge_only":
            swaps_dv01 = sum(r["dv01"] for r in live)
            offset = abs(swaps_dv01) <= 0.01 * sum(abs(r["dv01"]) for r in live)  # swaps unwound against each other
            hedges = offset or (bond_dv01 != 0 and swaps_dv01 * bond_dv01 <= 0
                                and abs(swaps_dv01) <= HEDGE_TOLERANCE * abs(bond_dv01))
            if not hedges:
                detail = (f"OIS DV01 of Rs {swaps_dv01:,.0f} per bp does not hedge its bonds' Rs {bond_dv01:,.0f} "
                          f"per bp (the access policy {self.settings.access.name!r} allows OIS for hedging only: "
                          f"opposite sign, at most {HEDGE_TOLERANCE:.0%} of the bonds' DV01)")
                # a swap traded today that leaves the book unhedged is refused, unless it reduces the swaps' DV01 (an
                # unwind of a hedge whose bonds were sold)
                if hedge_dv01 and abs(swaps_dv01) >= abs(swaps_dv01 - hedge_dv01) - 1e-9:
                    raise ValueError(f"book {book.name} on {d}: {detail}")
                self.hedge_breaches[book.name] = self.hedge_breaches.get(book.name, 0) + 1
                self.rows["checks"].append({"date": d, "book": book.name, "instrument": "", "check": "hedge_only",
                                            "value": swaps_dv01, "tolerance": HEDGE_TOLERANCE * abs(bond_dv01),
                                            "status": "breach",
                                            "detail": detail + "; unwind the swap or rebuy the bonds"})
        return total, notional, dv01_total

    def _ois_sizes(self, curve, roll_curve, d: date) -> dict[str, float]:
        key = (id(curve), id(roll_curve), d)
        cache = getattr(self, "_ois_size_cache", {})
        if key not in cache:
            st = self.settings.attribution
            report: dict = {}
            cache = {key: factor_moves(curve, roll_curve, nodes=st.nodes, basis=st.factor_basis, report=report)}
            self._ois_size_cache = cache
            self._note_factor_fit(report, d)
        return cache[key]

    def _emit_lines(self, d: date, lines: dict) -> None:
        order = {name: k for k, name in enumerate(self.line_order)}
        for (book, instrument, line), amount in sorted(lines.items(), key=lambda kv: (
                kv[0][0], kv[0][1], order.get(kv[0][2], len(order)), kv[0][2])):
            if amount == 0.0:
                continue
            self.rows["attribution"].append({"date": d, "book": book, "instrument": instrument, "line": line,
                                             "amount": float(amount), "memo": line in MEMO_LINES})

    def positions_of(self, day: date) -> pd.DataFrame:
        rows = [r for (d, _), group in sorted(self._positions_today.items()) if d == day for r in group]
        return pd.DataFrame(rows, columns=list(POSITION_COLUMNS))

    # -- the result

    def result(self) -> BookResult:
        frames = {}
        columns = {"positions": POSITION_COLUMNS, "nav": NAV_COLUMNS, "attribution": ATTRIBUTION_COLUMNS,
                   "fills": FILL_COLUMNS, "funding": FUNDING_COLUMNS, "checks": CHECK_COLUMNS}
        for name, cols in columns.items():
            frames[name] = pd.DataFrame(self.rows[name], columns=list(cols))
        nav = frames["nav"]
        if len(set(nav["book"])) > 1:
            nav = pd.concat([nav, _fund_rows(nav)], ignore_index=True)
        sort = {"positions": ["date", "book", "isin", "state", "settlement_date"],
                "nav": ["date", "book"], "attribution": None, "fills": None, "funding": ["date", "book", "segment"],
                "checks": None}
        for name, keys in sort.items():
            frame = nav if name == "nav" else frames[name]
            if keys:
                frame = frame.sort_values(keys, kind="mergesort").reset_index(drop=True)
            frames[name] = frame
        traded = {name: (b.traded_face, b.traded_dv01) for name, b in self.books.items()}
        perf = performance(frames["nav"], attribution=frames["attribution"], traded=traded)
        dd = drawdowns(frames["nav"])
        return BookResult(positions=frames["positions"], nav=frames["nav"], attribution=frames["attribution"],
                          fills=frames["fills"], funding=frames["funding"], checks=frames["checks"],
                          performance=perf, drawdowns=dd, notes=tuple(self.notes))


HEDGE_TOLERANCE = 1.05  # UNVERIFIED: a hedge's DV01 may exceed its bonds' by 5% (par and zero DV01 differ)
_MASTER_TERMS = ("kind", "coupon_pct", "maturity_date", "issue_date", "first_coupon_date", "liquidity")


def _add(lines: dict, book: str, instrument: str, line: str, amount: float) -> None:
    key = (book, instrument, line)
    lines[key] = lines.get(key, 0.0) + float(amount)


def _same_term(a, b) -> bool:
    if _blank(a) and _blank(b):
        return True
    try:
        return math.isclose(float(a), float(b), rel_tol=0, abs_tol=1e-9)
    except (TypeError, ValueError):
        pass
    try:
        return as_date(a) == as_date(b)
    except (TypeError, ValueError):
        return str(a).strip().lower() == str(b).strip().lower()


def _term_of(inst: Instrument, key: str):
    if key == "kind":
        return inst.kind
    if key == "maturity_date":
        return inst.maturity
    return getattr(inst.bond, "coupon_pct", None)


def _add_months(day: date, months: int) -> date:
    total = day.year * 12 + day.month - 1 + months
    year, month0 = divmod(total, 12)
    return date(year, month0 + 1, min(day.day, _calendar.monthrange(year, month0 + 1)[1]))


def _fund_rows(nav: pd.DataFrame) -> pd.DataFrame:
    """The fund as the sum of its books, day by day, with its own return and index."""
    numeric = ["bonds", "forwards", "ois", "cash", "repo_borrowed", "lent", "receivables", "nav", "flow", "pnl",
               "gross_long", "gross_short", "ois_notional", "dv01", *(f"dv01_{b}" for b in BUCKETS)]
    rows, prev_nav, index = [], 0.0, 1.0
    for day, group in nav.groupby("date", sort=True):
        row = {c: float(group[c].sum()) for c in numeric}
        base = prev_nav + row["flow"]
        ret = (row["nav"] - prev_nav - row["flow"]) / base if base > 0 else math.nan
        if math.isfinite(ret):
            index *= 1 + ret
        prev_nav = row["nav"]
        row.update(date=day, book="fund", **{"return": ret}, index=index, rf_return=group["rf_return"].iloc[0],
                   leverage=(row["gross_long"] + row["gross_short"]) / row["nav"] if row["nav"] > 0 else math.nan,
                   repo_leverage=row["repo_borrowed"] / row["nav"] if row["nav"] > 0 else math.nan,
                   calendar_projected=bool(group["calendar_projected"].any()), notes="sum of the books")
        rows.append(row)
    return pd.DataFrame(rows, columns=list(NAV_COLUMNS))


# ---------------------------------------------------------------- running


def run_book(trades=None, swaps=None, flows=None, *, market: MarketData, settings: EngineSettings, start=None,
             end=None) -> BookResult:
    """Run a list of bond trades (``TRADE_COLUMNS``), OIS trades (``SWAP_TRADE_COLUMNS``) and capital flows
    (``FLOW_COLUMNS``) from ``start`` (default: the first trade or flow) to ``end`` (default: the last) on ``market``.

    Each business day follows A2.8: flows, the night's funding repaid with interest, coupon and redemption entitlements,
    settlements, the day's fills with their costs, OIS settlements, funding for the next night, marks, valuation,
    attribution and the identity check. Bad input is refused with a ``ValueError`` naming the trade, file line, date or
    ISIN and what was wrong.
    """
    engine = _Engine(market, settings, trades, swaps, flows)
    engine.run(start, end)
    return engine.result()


def run_backtest(strategy: Strategy, market: MarketData, settings: EngineSettings, *, start, end, capital: float,
                 book: str = "main") -> BookResult:
    """Run ``strategy`` from ``start`` to ``end`` with ``capital`` rupees put into ``book`` at the start (A5).

    Each business day at ``settings.decision_time`` the strategy's ``decide`` gets a ``Snapshot`` of what was public
    then and returns ``Order`` objects; they execute from the next business day at that day's traded VWAP (or close)
    plus or minus the half spread, waiting up to ``max_wait_days`` for a traded price, then cancelled and logged.
    """
    cal = settings.calendar if settings.calendar is not None else mumbai_settlement_calendar()
    cal = cal.lenient() if settings.project_calendar else cal
    first = cal.adjust(as_date(start), "following")
    capital = _num(capital, "capital")
    if not capital > 0:
        raise ValueError(f"capital must be a positive number of rupees, not {capital}")
    flows = pd.DataFrame([{"date": first, "book": book, "amount": capital}])
    engine = _Engine(market, settings, None, None, flows, strategy=strategy)
    engine.run(first, end)
    return engine.result()


# ---------------------------------------------------------------- performance


def _returns_of(group: pd.DataFrame) -> pd.Series:
    if "return" in group and group["return"].notna().any():
        return group["return"].astype(float)
    nav = group["nav"].astype(float).to_numpy()
    flow = group["flow"].astype(float).to_numpy() if "flow" in group else np.zeros(len(nav))
    out = [math.nan]
    for k in range(1, len(nav)):
        base = nav[k - 1] + flow[k]
        out.append((nav[k] - nav[k - 1] - flow[k]) / base if base > 0 else math.nan)
    return pd.Series(out, index=group.index)


def drawdowns(nav: pd.DataFrame) -> pd.DataFrame:
    """The index, its running peak and the drawdown ``index / peak - 1`` per book and day (``DRAWDOWN_COLUMNS``)."""
    rows = []
    for book, group in nav.groupby("book", sort=True):
        group = group.sort_values("date", kind="mergesort")
        ret = _returns_of(group)
        index, peak = 1.0, 1.0
        for day, r in zip(group["date"], ret):
            if math.isfinite(r):
                index *= 1 + r
            peak = max(peak, index)
            rows.append({"date": day, "book": book, "index": index, "peak": peak, "drawdown": index / peak - 1})
    return pd.DataFrame(rows, columns=list(DRAWDOWN_COLUMNS))


def performance(nav: pd.DataFrame, *, attribution: pd.DataFrame | None = None,
                traded: Mapping[str, tuple[float, float]] | None = None, rf=None) -> pd.DataFrame:
    """Performance and risk per book (A4, ``PERFORMANCE_COLUMNS``) from a NAV frame (``NAV_COLUMNS``, or just
    ``date``, ``book``, ``nav`` and ``flow``: the first row is then the base).

    Daily returns with start-of-day flows; the time-weighted return ``prod(1 + r) - 1``; ``obs_per_year`` ``A`` =
    business days observed / (calendar days / 365.25) (UNVERIFIED annualisation), the annual return ``(1 + TWR)^(A /
    n) - 1`` and volatility ``sd sqrt(A)``; excess over ``rf`` (a Series by date, or the frame's ``rf_return``, the
    TREPS rate over each step's calendar days); Sharpe ``mean / sd x sqrt(A)`` (ddof 1); the largest drawdown with its
    peak, trough and recovery; leverage; DV01 by bucket; turnover (``traded`` bond face and DV01 by book, over the
    average NAV and DV01, annualised; OIS trades are not counted); costs (``spread_cost``, ``fees``, ``brokerage``,
    ``gst``, ``stamp_duty``, ``repo_charges``) as a share of the P&L before them, and in bp of the average NAV a year.
    """
    if not isinstance(nav, pd.DataFrame) or not {"date", "book", "nav"} <= set(nav.columns):
        raise ValueError("performance needs a NAV frame with date, book and nav")
    rows = []
    for book, group in nav.groupby("book", sort=True):
        group = group.sort_values("date", kind="mergesort")
        ret = _returns_of(group)
        ok = ret[np.isfinite(ret.to_numpy(dtype=float))]
        dates = [as_date(d) for d in group["date"]]
        n = len(ok)
        cal_days = (dates[-1] - dates[0]).days + 1
        per_year = n / (cal_days / 365.25) if n and cal_days else math.nan
        twr = float(np.prod(1 + ok.to_numpy())) - 1 if n else math.nan
        if rf is not None:
            rf_series = pd.Series(rf)
            rf_ret = pd.Series([float(rf_series.get(d, math.nan)) for d in group["date"]], index=group.index)
        elif "rf_return" in group:
            rf_ret = group["rf_return"].astype(float)
        else:
            rf_ret = pd.Series(0.0, index=group.index)
        excess = (ret - rf_ret.fillna(0.0))[np.isfinite(ret.to_numpy(dtype=float))]
        mean = float(ok.mean()) if n else math.nan
        sd = float(ok.std(ddof=1)) if n > 1 else math.nan
        ex_sd = float(excess.std(ddof=1)) if n > 1 else math.nan
        sharpe = float(excess.mean()) / ex_sd * math.sqrt(per_year) if n > 1 and ex_sd > 0 else math.nan
        dd = drawdowns(group.assign(book=book))
        worst = dd["drawdown"].idxmin() if len(dd) else None
        dd_info = {"max_drawdown": math.nan, "drawdown_peak": None, "drawdown_trough": None,
                   "drawdown_recovery": None, "drawdown_days": math.nan, "recovery_days": math.nan}
        notes = []
        if worst is not None:
            trough_pos = list(dd.index).index(worst)
            peak_value = dd["peak"].iloc[trough_pos]
            closes = [k for k in range(trough_pos + 1) if dd["index"].iloc[k] == peak_value]
            if closes:
                peak_pos, peak_days = closes[-1], trough_pos - closes[-1]
            else:  # the peak is the starting capital, before the first return: the start of that day
                finite = np.isfinite(ret.to_numpy(dtype=float))
                peak_pos = int(np.argmax(finite)) if finite.any() else 0
                peak_days = trough_pos - peak_pos + 1
                if float(dd["drawdown"].min()) < 0:
                    notes.append(f"the largest drawdown runs from the starting capital at the start of "
                                 f"{dd['date'].iloc[peak_pos]} (drawdown_peak is that day, before its return)")
            rec_pos = next((k for k in range(trough_pos + 1, len(dd)) if dd["index"].iloc[k] >= peak_value), None)
            dd_info = {"max_drawdown": float(dd["drawdown"].min()), "drawdown_peak": dd["date"].iloc[peak_pos],
                       "drawdown_trough": dd["date"].iloc[trough_pos],
                       "drawdown_recovery": dd["date"].iloc[rec_pos] if rec_pos is not None else None,
                       "drawdown_days": peak_days,
                       "recovery_days": (rec_pos - trough_pos) if rec_pos is not None else math.nan}
        navs = group["nav"].astype(float)
        avg_nav = float(navs.mean()) if len(navs) else math.nan

        def col(name, group=group):
            return group[name].astype(float) if name in group else pd.Series(math.nan, index=group.index)

        lev = col("leverage")
        dv01 = col("dv01")
        face, face_dv01 = math.nan, math.nan
        if traded is not None:
            if book in traded:
                face, face_dv01 = traded[book]
            elif book == "fund":
                face = sum(v[0] for v in traded.values())
                face_dv01 = sum(v[1] for v in traded.values())
        scale = per_year / n if n else math.nan
        pnl = float(col("pnl").sum()) if "pnl" in group else float(navs.iloc[-1] - navs.iloc[0] - col("flow").sum())
        costs = math.nan
        if attribution is not None and len(attribution):
            mine = attribution if book == "fund" else attribution[attribution["book"] == book]
            costs = -float(mine[mine["line"].isin(COSTS_IN_PNL)]["amount"].sum())
        avg_abs_dv01 = float(dv01.abs().mean()) if dv01.notna().any() else math.nan
        if n == 0:
            notes.append("no return: no capital at the start of any day")
        rows.append({
            "book": book, "start": dates[0], "end": dates[-1], "business_days": n, "calendar_days": cal_days,
            "obs_per_year": per_year, "twr": twr,
            "annual_return": (1 + twr) ** (per_year / n) - 1 if n and twr > -1 else math.nan,
            "mean_daily": mean, "vol_daily": sd, "annual_vol": sd * math.sqrt(per_year) if n > 1 else math.nan,
            "excess_twr": float(np.prod(1 + excess.to_numpy())) - 1 if n else math.nan, "sharpe": sharpe,
            **dd_info, "avg_nav": avg_nav, "avg_leverage": float(lev.mean()) if lev.notna().any() else math.nan,
            "max_leverage": float(lev.max()) if lev.notna().any() else math.nan,
            "avg_repo_leverage": float(col("repo_leverage").mean()) if col("repo_leverage").notna().any()
            else math.nan,
            "avg_ois_leverage": float((col("ois_notional") / navs).mean()) if col("ois_notional").notna().any()
            else math.nan,
            "avg_dv01": float(dv01.mean()) if dv01.notna().any() else math.nan,
            **{f"avg_dv01_{b}": float(col(f"dv01_{b}").mean()) if f"dv01_{b}" in group else math.nan
               for b in BUCKETS},
            "traded_face": face, "turnover": face / avg_nav * scale if avg_nav else math.nan,
            "traded_dv01": face_dv01,
            "dv01_turnover": face_dv01 / avg_abs_dv01 * scale if avg_abs_dv01 else math.nan,
            "pnl": pnl, "costs": costs,
            "costs_share": costs / (pnl + costs) if math.isfinite(costs) and pnl + costs else math.nan,
            "costs_bp_nav": costs / avg_nav * 1e4 * 365.25 / cal_days if math.isfinite(costs) and avg_nav
            else math.nan,
            "notes": "; ".join(notes)})
    return pd.DataFrame(rows, columns=list(PERFORMANCE_COLUMNS))


# ---------------------------------------------------------------- writing


RESULT_FILES = ("positions", "nav", "attribution", "fills", "funding", "checks", "performance", "drawdowns")


def write_result(result: BookResult, out_dir) -> list:
    """Write a run's frames as CSVs (``RESULT_FILES``) and its notes to ``notes.txt`` in ``out_dir``; the same result
    gives the same bytes. Returns the paths written."""
    from pathlib import Path
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in RESULT_FILES:
        path = out / f"{name}.csv"
        getattr(result, name).to_csv(path, index=False, date_format="%Y-%m-%d")
        paths.append(path)
    notes = out / "notes.txt"
    notes.write_text("".join(f"{n}\n" for n in result.notes), encoding="utf-8")
    paths.append(notes)
    return paths


__all__ = [
    "ACCESS_PRESETS", "ATTRIBUTION_COLUMNS", "AUCTION_COLUMNS", "BUCKETS", "CHECK_COLUMNS", "COSTS_IN_PNL",
    "DEFAULT_NODES", "DEFAULT_REFERENCE", "DRAWDOWN_COLUMNS", "FACTORS", "FILL_COLUMNS", "FLOW_COLUMNS",
    "FUNDING_COLUMNS", "MARK_COLUMNS", "MARK_ORDER", "MARK_SOURCES", "MEMO_LINES", "NAV_COLUMNS", "NSS_PARAMS_COLUMNS",
    "PERFORMANCE_COLUMNS", "POSITION_COLUMNS", "RATE_BASES", "RESULT_FILES", "SWAP_TRADE_COLUMNS", "TRADE_COLUMNS",
    "AccessPolicy", "AttributionSettings", "BookResult", "CurveEntry", "EngineSettings", "FundingPolicy", "Instrument",
    "MarkPolicy", "MarkState", "MarketData", "Order", "ShiftedCurve", "Snapshot", "Strategy", "access_policy",
    "append_nss_params", "attribute_bond", "attribute_ois", "auctions_from_events", "drawdowns", "factor_curves",
    "factor_moves", "factor_shapes", "funding_from_store", "make_instrument", "mark_state", "marks_from_prices",
    "model_yield", "nss_params_row", "performance", "read_flows", "read_marks", "read_nss_params", "read_swaps",
    "read_trades", "roll_reference", "rolled", "run_backtest", "run_book", "seen_from", "write_result",
]
