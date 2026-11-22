"""Floating-rate bonds: GoI FRBs (182-day T-bill yields plus a fixed spread) and benchmark-linked corporate FRBs.

**GoI FRB coupons.** FRB 2031 and later carry a base rate plus a spread fixed at the auction for the bond's life;
the base is the average of the weighted average yields (WAY) of the last three 182-day T-bill auctions counted back
from the rate fixing day, the first day of each half-year (RBI's FRB 2034 release of 28 Oct 2022, read in a copy at
https://www.simpliance.in; RBI's G-sec primer, saved copy). FRB 2024 averaged the yields implied by the three
cut-off prices instead (``index="tbill182_cutoff_avg3"``). The WAY on a 365-day basis is
``(100 / WAP - 1) x 365 / 182 x 100`` (``way_from_wap``: 6.505886 from the 11 Nov 2026 WAP of 96.8579, RBI printed
6.5059). Cut-off yields do not reproduce RBI's coupons (FRB 2034 from 30 Oct 2022: 7.72 against RBI's 7.69).
``goi_frb_fixing`` computes one coupon: the base rounded half up to 2 decimals (the primer's rule for FRB 2024;
the rounding mode for later bonds is UNVERIFIED), an auction held on the fixing day itself left out (UNVERIFIED,
``include_fixing_day``). FRB 2035 resets every five years at a five-year G-sec yield
(``index="gsec5y_reset5y"``): only announced coupons are used for it.

**As known at a time.** Every T-bill result carries ``public_from`` (``tbill_way_table``: the RBI store's feed
time, else the end of the release date; a stated rule otherwise, or refused). ``goi_frb_rates`` gives each coupon
period's rate as known at ``as_of``: ``announced`` (RBI's coupon release, from ``goi_frb_coupons.csv`` or a table
of the same columns, public from its release time; a release date alone counts from 23:59:59; an unknown time is
estimated as 00:00 on the fixing day and flagged, UNVERIFIED), else ``computed`` (the three results public by
``as_of``, complete once no weekly auction can still fall before the fixing day: the last used auction is within
seven days of it, UNVERIFIED rule of thumb from the weekly auction calendar; public from the latest of the three
results), else ``projected``, else ``unknown`` for a past period nothing fixes. Projection (UNVERIFIED choice):
``flat`` (default) holds every later coupon at the latest computable base (rounded as the coupon rule rounds) plus
the spread, or at the base implied by the latest announced coupon when the T-bill results are too few; ``forward``
uses a discount curve's 182-day forward rate from each fixing day, as a T-bill yield (simple, actual/365),
unrounded. Fewer than three results are refused unless ``allow_fewer`` (flagged).

**The bond.** ``GoiFloatingRateBond`` composes an unchanged ``bonds.FixedRateBond`` with a 1% coupon on the G-sec
conventions (``GOI_FRB``: 30E/360 periods, semi-annual, the last period simple): its schedule, flow times and unit
accrued interest are the FRB's, each coupon the period's rate times the unit coupon, the accrued interest the
current rate times the unit accrued interest. Its yield is the G-sec yield of those flows.

**Discount margin.** Index form (default): the ``dm`` with ``P_dirty = sum CF_j / (1 + (I + dm)/2)^t_j``, ``t_j``
the G-sec half-year times and ``I`` the index the projection used, i.e. the FRB's yield less ``I``; on a reset date
with a flat projection a price of 100 gives ``dm`` equal to the spread. Curve form: ``P_dirty = sum CF_j DF(d_j)
exp(-dm tau_j)``, ``tau`` actual days / 365 from settlement, ``DF`` relative to settlement. ``price_from_dm`` is the
reverse; ``spread_dv01`` the fall in price for a one basis point rise in ``dm``; ``index_dv01`` the change for a
one basis point rise in the projected index with ``dm`` held (the projected coupons move, the known ones do not);
``spread_duration`` the spread DV01 per unit of dirty price. Reset dates are not deemed maturities (SEBI MF master
circular 6.18.18).

**Corporate FRBs** are ``corporates.CorporateBond`` objects whose ``rate_steps`` hold the fixings:
``corporate_frb_rates`` reads a ``FloatingTerms`` (benchmark, spread, reset interval, fixing lag, look-back average,
floor, cap, rounding: the fields SEBI's NCS master circular asks the database to hold) and a fixings table (each
value with its ``public_from``) and returns one rate per reset, ``fixed`` once its fixing date is over and the
fixing public, else ``projected`` (flat at the latest public fixing, or forward). MIBOR is FBIL's and licensed: none
is shipped. Day count as for NCDs (SEBI actual/actual).

Shipped facts: ``data/goi_frb_terms.csv`` (the five outstanding GoI FRBs) and ``data/goi_frb_coupons.csv`` (coupons
typed in from RBI releases read through copies and search summaries, each row with its status). Not built: RBI's
FRB coupon releases are not parsed (no saved sample), the meaning of RBI's printed FRB auction yield is not known,
FRB DV01 in the event ledger and FRBs in the return engine come next.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise

import pandas as pd

from cr_strat.bonds import Bond, CashFlow, FixedRateBond, _Flows, _shift_months
from cr_strat.calendars import DATA_DIR, BusinessCalendar
from cr_strat.conventions import GOI_FRB, BondConventions, CorporateConventions, as_date, as_int, round_half_up
from cr_strat.corporates import CorporateBond, public_time, read_term_table

FRB_TERMS_CSV = DATA_DIR / "goi_frb_terms.csv"
FRB_COUPONS_CSV = DATA_DIR / "goi_frb_coupons.csv"
FRB_TERMS_COLUMNS = ("isin", "name", "issue_date", "maturity_date", "first_coupon_date", "index", "spread_pct",
                     "decimals", "reset_months", "source", "status", "notes")
FRB_COUPON_COLUMNS = ("isin", "period_start", "period_end", "rate_pct", "announced", "source", "status")
FRB_INDICES = ("tbill182_way_avg3", "tbill182_cutoff_avg3", "gsec5y_reset5y")
TBILL_COLUMNS = ("auction_date", "tenor_days", "way_pct", "cutoff_pct", "wap", "cutoff_price", "public_from",
                 "public_from_source", "way_source")
FRB_RATE_COLUMNS = ("isin", "period_start", "period_end", "fixing_day", "rate_pct", "base_pct", "spread_pct", "status",
                    "public_from", "public_from_source", "auctions", "notes")
PROJECTIONS = ("flat", "forward")
DM_FORMS = ("index", "curve")
YIELD_KINDS = ("way", "cutoff")
FLOATING_COLUMNS = ("isin", "benchmark", "spread_pct", "reset_months", "fixing_lag_days", "lookback", "floor_pct",
                    "cap_pct", "decimals", "source")
FIXING_COLUMNS = ("benchmark", "date", "value_pct", "public_from", "source")
CORPORATE_RATE_COLUMNS = ("period_start", "fixing_date", "fixings_used", "fixing_pct", "rate_pct", "status",
                          "public_from", "notes")
TBILL_TENOR_DAYS = 182
WAY_TOLERANCE = 2e-4  # printed WAY against the WAY from the printed WAP: both have 4 decimals
AUCTION_WEEK_DAYS = 7  # UNVERIFIED: 182-day T-bills are auctioned weekly; a later auction can fall within 7 days
GAP_NOTE_DAYS = 10  # consecutive auctions used further apart than this are noted (an incomplete table?)
_TOL = 1e-12


def _present(value) -> bool:
    if value is None or value is pd.NaT:
        return False
    if isinstance(value, float) and math.isnan(value):
        return False
    return not (isinstance(value, str) and not value.strip())


def _num(value, what: str) -> float | None:
    if not _present(value):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a number, not {value!r}") from None
    if not math.isfinite(out):
        raise ValueError(f"{what} must be finite, not {value!r}")
    return out


# ---------------------------------------------------------------- T-bill yields


def way_from_wap(wap: float, tenor_days: int = TBILL_TENOR_DAYS) -> float:
    """A T-bill yield (percent, simple, 365 days) from a price per 100: ``(100 / P - 1) x 365 / days x 100``.

    For the weighted average price this is the WAY; for the cut-off price the cut-off yield.
    """
    p = float(wap)
    days = as_int(tenor_days, "tenor_days", minimum=1)
    if not (math.isfinite(p) and 0 < p):
        raise ValueError(f"a T-bill price must be a positive number, not {wap!r}")
    return (100 / p - 1) * 365 / days * 100


def _result_time(rec: dict, default_time: time | None, where: str) -> tuple[datetime, str]:
    for column in ("public_from", "published"):
        if _present(rec.get(column)):
            return public_time(rec[column], column), column
    if _present(rec.get("release_date")):
        return datetime.combine(as_date(rec["release_date"]), time(23, 59, 59)), "release_date"
    if default_time is not None:
        return datetime.combine(as_date(rec["auction_date"]), default_time), "rule"
    raise ValueError(f"{where}: no public_from (nor published or release_date); a T-bill result is used only from "
                     "when it was public, so give the time or a default_time rule")


def tbill_way_table(results, *, tenor_days: int = TBILL_TENOR_DAYS, default_time: time | None = None) -> pd.DataFrame:
    """One row per auction of ``tenor_days`` T-bills: WAY and cut-off yield (percent) and when each was public.

    ``results``: a frame or CSV with ``auction_date``, ``tenor_days`` and any of ``way_pct``/``way``, ``wap``,
    ``cutoff_pct``/``cutoff_yield``, ``cutoff_price``; the time from ``public_from``, else the RBI store's
    ``published`` (feed time), else ``release_date`` (23:59:59 that day), else ``default_time`` on the auction
    date (a rule, flagged ``rule``), else refused. A missing WAY is computed from the WAP (``way_source``
    ``from_wap``); a printed WAY that disagrees with the printed WAP is refused. An auction listed twice with the
    same yields keeps the earliest time; with different yields it is refused.
    """
    frame = results.copy() if isinstance(results, pd.DataFrame) else pd.read_csv(results, dtype=str,
                                                                                 keep_default_na=False)
    frame.columns = [str(c).strip().lower() for c in frame.columns]
    missing = [c for c in ("auction_date", "tenor_days") if c not in frame]
    if missing:
        raise ValueError(f"T-bill results need columns {missing}")
    days = as_int(tenor_days, "tenor_days", minimum=1)
    rows: dict[date, dict] = {}
    for i, rec in enumerate(frame.to_dict("records")):
        if not _present(rec.get("auction_date")):
            continue
        where = f"T-bill result row {i + 2}"
        tenor = _num(rec.get("tenor_days"), f"{where} tenor_days")
        if tenor is None or int(tenor) != days:
            continue
        auction = as_date(rec["auction_date"])
        way = _num(rec.get("way_pct", rec.get("way")), f"{where} WAY")
        wap = _num(rec.get("wap"), f"{where} WAP")
        cut = _num(rec.get("cutoff_pct", rec.get("cutoff_yield")), f"{where} cut-off yield")
        cut_price = _num(rec.get("cutoff_price"), f"{where} cut-off price")
        source = "printed" if way is not None else "none"
        if wap is not None:
            computed = way_from_wap(wap, days)
            if way is None:
                way, source = computed, "from_wap"
            elif abs(computed - way) > WAY_TOLERANCE:
                raise ValueError(f"{where} ({auction}): printed WAY {way} and WAP {wap} disagree (the WAP gives "
                                 f"{computed:.6f})")
        if cut is None and cut_price is not None:
            cut = way_from_wap(cut_price, days)
        when, how = _result_time(rec, default_time, f"{where} ({auction})")
        row = {"auction_date": auction, "tenor_days": days, "way_pct": math.nan if way is None else way,
               "cutoff_pct": math.nan if cut is None else cut, "wap": math.nan if wap is None else wap,
               "cutoff_price": math.nan if cut_price is None else cut_price, "public_from": when,
               "public_from_source": how, "way_source": source}
        if auction in rows:
            old = rows[auction]
            same = all(old[c] == row[c] or (isinstance(old[c], float) and math.isnan(old[c]) and math.isnan(row[c]))
                       for c in ("way_pct", "cutoff_pct"))
            if not same:
                raise ValueError(f"{where}: the {days}-day auction of {auction} is listed twice with different "
                                 f"yields (WAY/cut-off {old['way_pct']}/{old['cutoff_pct']} and "
                                 f"{row['way_pct']}/{row['cutoff_pct']})")
            if row["public_from"] < old["public_from"]:
                rows[auction] = row
            continue
        rows[auction] = row
    return pd.DataFrame([rows[d] for d in sorted(rows)], columns=list(TBILL_COLUMNS))


def _tbills(tbills) -> pd.DataFrame:
    if isinstance(tbills, pd.DataFrame) and list(tbills.columns) == list(TBILL_COLUMNS):
        return tbills
    return tbill_way_table(tbills)


@dataclass(frozen=True)
class FrbFixing:
    """One GoI FRB coupon from T-bill results: the auctions used, their yields, the base and the coupon (percent).

    ``status``: ``computed`` (the last ``n`` auctions before the fixing day, all public, none still to come) or
    ``projected`` (from the latest public results). ``public_from``: when every result used was public.
    """

    fixing_day: date
    auctions: tuple[date, ...]
    yields: tuple[float, ...]
    base_unrounded: float
    base_pct: float
    spread_pct: float
    coupon_pct: float
    status: str
    public_from: datetime | None
    yield_kind: str
    notes: str = ""


def _as_datetime(value) -> datetime | None:
    if value is None or value is pd.NaT:
        return None
    return value.to_pydatetime() if isinstance(value, pd.Timestamp) else value


def goi_frb_fixing(fixing_day, spread_pct: float, tbills, *, as_of=None, n: int = 3, decimals: int | None = 2,
                   include_fixing_day: bool = False, yield_kind: str = "way", allow_fewer: bool = False,
                   yield_decimals: int | None = 4) -> FrbFixing:
    """The coupon fixed on ``fixing_day``: the mean of the last ``n`` 182-day yields before it, rounded, plus spread.

    Only results public by ``as_of`` are used (all of them without it). Fewer than ``n`` are refused unless
    ``allow_fewer``. Each yield is taken as RBI prints it, to ``yield_decimals`` (4; a WAY computed from a WAP
    is rounded so, UNVERIFIED), and the mean is exact in decimals. ``decimals``: the base's rounding, half up
    (``None``: unrounded). The coupon is ``computed`` when ``n`` results are used and the last of them is within
    a week of the fixing day (no weekly auction can still come before it), else ``projected``.
    """
    day = as_date(fixing_day)
    if yield_kind not in YIELD_KINDS:
        raise ValueError(f"yield_kind must be one of {YIELD_KINDS}, not {yield_kind!r}")
    n = as_int(n, "n", minimum=1)
    spread = _num(spread_pct, "spread_pct") or 0.0
    table = _tbills(tbills)
    column = "way_pct" if yield_kind == "way" else "cutoff_pct"
    cutoff = None if as_of is None else public_time(as_of, "as_of")
    usable = [r for r in table.to_dict("records")
              if (r["auction_date"] <= day if include_fixing_day else r["auction_date"] < day)
              and math.isfinite(r[column]) and (cutoff is None or _as_datetime(r["public_from"]) <= cutoff)]
    used = usable[-n:]
    notes = []
    if len(used) < n:
        if not allow_fewer or not used:
            raise ValueError(f"fixing {day}: {len(used)} {TBILL_TENOR_DAYS}-day results public"
                             f"{'' if cutoff is None else f' by {cutoff:%Y-%m-%d %H:%M}'} before the fixing day; "
                             f"{n} are needed (allow_fewer=True projects from fewer, flagged)")
        notes.append(f"only {len(used)} of {n} auctions")
    for a, b in pairwise(used):
        if (b["auction_date"] - a["auction_date"]).days > GAP_NOTE_DAYS:
            notes.append(f"{(b['auction_date'] - a['auction_date']).days} days between the auctions of "
                         f"{a['auction_date']} and {b['auction_date']}: is the table complete?")
    places = None if yield_decimals is None else as_int(yield_decimals, "yield_decimals", minimum=0)
    yields = tuple(float(r[column]) if places is None else round_half_up(float(r[column]), places) for r in used)
    exact = sum(Decimal(repr(y)) for y in yields) / Decimal(len(yields))
    mean = float(exact)
    if decimals is None:
        base = mean
    else:
        base = float(exact.quantize(Decimal(1).scaleb(-as_int(decimals, "decimals", minimum=0)),
                                    rounding=ROUND_HALF_UP))
    last = used[-1]["auction_date"]
    gap = (day - last).days
    complete = len(used) == n and (gap <= AUCTION_WEEK_DAYS if not include_fixing_day else gap < AUCTION_WEEK_DAYS)
    return FrbFixing(day, tuple(r["auction_date"] for r in used), yields, mean, base, spread,
                     round(base + spread, 10), "computed" if complete else "projected",
                     _as_datetime(max(r["public_from"] for r in used)), yield_kind, "; ".join(notes))


def forward_tbill_yield(curve, fixing_day, tenor_days: int = TBILL_TENOR_DAYS) -> float:
    """A discount curve's ``tenor_days`` forward rate from ``fixing_day`` as a T-bill yield (simple, 365 days)."""
    day = as_date(fixing_day)
    days = as_int(tenor_days, "tenor_days", minimum=1)
    if day < curve.valuation_date:
        raise ValueError(f"fixing day {day} is before the curve's valuation date {curve.valuation_date}")
    start, end = float(curve.discount(day)), float(curve.discount(day + timedelta(days=days)))
    return (start / end - 1) * 365 / days * 100


# ---------------------------------------------------------------- GoI FRB terms and coupons


def load_frb_terms(path=FRB_TERMS_CSV) -> pd.DataFrame:
    """The GoI FRB terms table (``FRB_TERMS_COLUMNS``; the shipped file by default), one row per ISIN."""
    frame = read_term_table(path, FRB_TERMS_COLUMNS, ("isin", "name", "issue_date", "maturity_date", "index"),
                            ("isin",), "GoI FRB terms")
    bad = sorted(set(frame["index"]) - set(FRB_INDICES))
    if bad:
        raise ValueError(f"GoI FRB terms: index must be one of {FRB_INDICES}, not {bad}")
    return frame


def load_frb_coupons(path=FRB_COUPONS_CSV) -> pd.DataFrame:
    """Announced GoI FRB coupons (``FRB_COUPON_COLUMNS``; the shipped file by default), one row per period start."""
    return read_term_table(path, FRB_COUPON_COLUMNS, ("isin", "period_start", "rate_pct"), ("isin", "period_start"),
                           "GoI FRB coupons")


def _unit_bond(terms: dict) -> FixedRateBond:
    return FixedRateBond(1.0, as_date(terms["maturity_date"]), GOI_FRB, issue_date=as_date(terms["issue_date"]),
                         first_coupon_date=as_date(terms["first_coupon_date"]) if _present(terms.get(
                             "first_coupon_date")) else None)


def frb_periods(terms) -> list[tuple[date, date]]:
    """(start, end) of every coupon period of a GoI FRB from its terms row (the first from the issue date)."""
    unit = _unit_bond(dict(terms))
    ends = unit.coupon_dates()
    return list(zip([unit.issue_date, *ends[:-1]], ends))


def _announced_rows(announced, isin: str) -> list[dict]:
    if announced is None:
        return []
    table = announced if isinstance(announced, pd.DataFrame) and "rate_pct" in announced else load_frb_coupons(
        announced)
    out = []
    for rec in table.to_dict("records"):
        if str(rec["isin"]).strip() != isin:
            continue
        start = as_date(rec["period_start"])
        end = as_date(rec["period_end"]) if _present(rec.get("period_end")) else None
        text = rec.get("announced")
        if not _present(text):
            when, how = None, "estimated"
        else:
            when = public_time(text, f"{isin} {start} announced")
            date_only = (isinstance(text, str) and len(text.strip()) == 10) or (
                isinstance(text, date) and not isinstance(text, datetime))
            how = "release_date" if date_only else "release"
        out.append({"start": start, "end": end, "rate": _num(rec["rate_pct"], f"{isin} {start} rate_pct"),
                    "when": when, "how": how, "source": str(rec.get("source", "") or "")})
    return sorted(out, key=lambda r: r["start"])


def _reset_day(start: date, issue: date, reset_months: int) -> date:
    if reset_months == 6:
        return start
    k = 0
    while _shift_months(issue, (k + 1) * reset_months) <= start:
        k += 1
    return _shift_months(issue, k * reset_months)


def goi_frb_rates(terms, tbills=None, announced=FRB_COUPONS_CSV, *, as_of=None, projection: str = "flat",
                  curve=None, n: int = 3, include_fixing_day: bool = False, allow_fewer: bool = False) -> pd.DataFrame:
    """Each coupon period's rate of one GoI FRB as known at ``as_of`` (``FRB_RATE_COLUMNS``).

    ``terms``: a row of ``load_frb_terms``. ``tbills``: 182-day results (``tbill_way_table`` or its input).
    ``announced``: announced coupons (the shipped file by default; ``None`` for none). Each period is
    ``announced`` (its release public by ``as_of``), else ``computed`` from the T-bill results, else
    ``projected`` (``flat`` or ``forward`` with ``curve``), else ``unknown`` (a past period nothing fixes: NaN).
    An unknown release time is estimated as 00:00 on the fixing day (``public_from_source`` ``estimated``).
    A computed coupon that differs from the announced one is noted. Without ``as_of`` everything known is used.
    """
    terms = dict(terms)
    isin = str(terms["isin"]).strip()
    index = terms["index"]
    if index not in FRB_INDICES:
        raise ValueError(f"{isin}: index must be one of {FRB_INDICES}, not {index!r}")
    if projection not in PROJECTIONS:
        raise ValueError(f"projection must be one of {PROJECTIONS}, not {projection!r}")
    if projection == "forward" and (curve is None or index == "gsec5y_reset5y"):
        raise ValueError(f"{isin}: a forward projection needs curve= and a T-bill index")
    spread = _num(terms.get("spread_pct"), f"{isin} spread_pct") or 0.0
    decimals = _num(terms.get("decimals"), f"{isin} decimals")
    decimals = 2 if decimals is None else as_int(decimals, "decimals", minimum=0)
    reset_months = as_int(_num(terms.get("reset_months"), "reset_months") or 6, "reset_months", minimum=1)
    issue = as_date(terms["issue_date"])
    cutoff = None if as_of is None else public_time(as_of, "as_of")
    table = None if tbills is None else _tbills(tbills)
    kind = "way" if index == "tbill182_way_avg3" else "cutoff"
    rows_announced = _announced_rows(announced, isin)
    for a in rows_announced:  # an unknown release time is estimated as 00:00 on the fixing day (UNVERIFIED)
        a["public"] = a["when"] or datetime.combine(_reset_day(a["start"], issue, reset_months), time(0, 0))
    flat_base, flat_note = None, ""
    if projection == "flat" and index != "gsec5y_reset5y" and table is not None and not table.empty:
        try:
            tomorrow = (cutoff.date() if cutoff else table["auction_date"].max()) + timedelta(days=1)
            f = goi_frb_fixing(tomorrow, spread, table, as_of=cutoff, n=n, decimals=decimals, yield_kind=kind,
                               allow_fewer=allow_fewer, include_fixing_day=True)
            flat_base = f.base_pct
            flat_note = (f"flat at the base of the auctions of {', '.join(str(a) for a in f.auctions)}"
                         + (f" ({f.notes})" if f.notes else ""))
        except ValueError as exc:
            flat_note = f"no T-bill base: {exc}"
    out = []
    for start, end in frb_periods(terms):
        fixing = _reset_day(start, issue, reset_months)
        rec = {"isin": isin, "period_start": start, "period_end": end, "fixing_day": fixing, "rate_pct": math.nan,
               "base_pct": math.nan, "spread_pct": spread, "status": "unknown", "public_from": None,
               "public_from_source": "", "auctions": "", "notes": ""}
        notes = []
        known = [a for a in rows_announced if a["start"] <= start and (start < a["end"] if a["end"] else
                                                                        start == a["start"])]
        ann = known[-1] if known else None
        if ann is not None:
            when = ann["public"]
            if cutoff is None or when <= cutoff:
                rec.update(rate_pct=ann["rate"], base_pct=ann["rate"] - spread if index != "gsec5y_reset5y"
                           else math.nan, status="announced", public_from=when,
                           public_from_source=ann["how"])
                if ann["how"] == "estimated":
                    notes.append("release time unknown: estimated as 00:00 on the fixing day")
        if index != "gsec5y_reset5y" and table is not None:
            try:
                f = goi_frb_fixing(fixing, spread, table, as_of=cutoff, n=n, decimals=decimals, yield_kind=kind,
                                   include_fixing_day=include_fixing_day)
            except ValueError:
                f = None
            if f is not None and f.status == "computed":
                if rec["status"] == "announced":
                    if abs(f.coupon_pct - rec["rate_pct"]) > 1e-9:
                        notes.append(f"the T-bill results give {f.coupon_pct:.2f}, not the announced "
                                     f"{rec['rate_pct']:.2f}")
                else:
                    rec.update(rate_pct=f.coupon_pct, base_pct=f.base_pct, status="computed",
                               public_from=f.public_from, public_from_source="auctions",
                               auctions=";".join(str(a) for a in f.auctions))
                    if f.notes:
                        notes.append(f.notes)
        if rec["status"] == "unknown":
            started = cutoff is not None and datetime.combine(fixing, time(0, 0)) <= cutoff
            ended = cutoff is not None and end <= cutoff.date()
            if not ended:
                rate, note = _projected_rate(index, projection, curve, fixing, spread, flat_base, flat_note,
                                             rows_announced, cutoff)
                if rate is not None:
                    rec.update(rate_pct=rate[0], base_pct=rate[1], status="projected")
                    notes.append(note)
                    if started:
                        notes.append(f"the coupon fixed on {fixing} is not in the data: projected")
        rec["notes"] = "; ".join(notes)
        out.append(rec)
    return pd.DataFrame(out, columns=list(FRB_RATE_COLUMNS))


def _projected_rate(index: str, projection: str, curve, fixing: date, spread: float, flat_base: float | None,
                    flat_note: str, announced: list[dict], cutoff: datetime | None
                    ) -> tuple[tuple[float, float] | None, str]:
    """((rate, base), note) of a projected coupon, or (None, "") when nothing to project from is known."""
    if index == "gsec5y_reset5y":
        public = [a for a in announced if cutoff is None or a["public"] <= cutoff]
        if not public:
            return None, ""
        return (public[-1]["rate"], math.nan), f"flat at the coupon announced for {public[-1]['start']}"
    if projection == "forward" and fixing >= curve.valuation_date:
        base = forward_tbill_yield(curve, fixing)
        return (round(base + spread, 10), base), "forward projection, unrounded"
    if flat_base is not None:
        return (round(flat_base + spread, 10), flat_base), flat_note
    public = [a for a in announced if cutoff is None or a["public"] <= cutoff]
    if not public:
        return None, ""
    base = public[-1]["rate"] - spread
    note = f"flat at the base implied by the coupon announced for {public[-1]['start']}"
    return (round(base + spread, 10), base), note + (f"; {flat_note}" if flat_note else "")


# ---------------------------------------------------------------- the GoI FRB


@dataclass(frozen=True)
class GoiFloatingRateBond(Bond):
    """A GoI FRB: the schedule of ``FixedRateBond(1.0, ...)`` on ``conventions`` with a rate per coupon period.

    ``rates``: rows of (period start, rate percent[, status[, public_from]]), the period start being the issue
    date or a coupon date; ``goi_frb_rates`` gives them. Every period from settlement on needs a rate.
    ``index_pct``: the index the projected coupons stand on, the default for the index-form discount margin.
    ``calendar``: rolls the payment dates of ``cash_flows`` only.
    """

    maturity: date
    issue_date: date
    spread_pct: float
    rates: tuple[tuple, ...]
    first_coupon_date: date | None = None
    conventions: BondConventions = GOI_FRB
    calendar: BusinessCalendar | None = None
    index_pct: float | None = None
    isin: str = ""
    name: str = ""

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        set_(self, "maturity", as_date(self.maturity))
        set_(self, "issue_date", as_date(self.issue_date))
        if self.first_coupon_date is not None:
            set_(self, "first_coupon_date", as_date(self.first_coupon_date))
        if not isinstance(self.conventions, BondConventions) or isinstance(self.conventions, CorporateConventions):
            raise TypeError("conventions must be G-sec BondConventions (GOI_FRB or a variant)")
        where = self.isin or self.name or f"FRB {self.maturity}"
        set_(self, "_where", where)
        spread = _num(self.spread_pct, f"{where} spread_pct")
        set_(self, "spread_pct", 0.0 if spread is None else spread)
        unit = FixedRateBond(1.0, self.maturity, self.conventions, issue_date=self.issue_date,
                             first_coupon_date=self.first_coupon_date)
        set_(self, "_unit", unit)
        set_(self, "_unit_cal", unit if self.calendar is None else replace(unit, calendar=self.calendar))
        starts = {self.issue_date, *unit.coupon_dates()[:-1]}
        table: dict[date, tuple[float, str, datetime | None]] = {}
        clean = []
        for item in self.rates:
            if not isinstance(item, (tuple, list)) or not 2 <= len(item) <= 4:
                raise TypeError(f"{where}: rates rows are (period start, rate[, status[, public_from]]), not {item!r}")
            start, rate = as_date(item[0]), _num(item[1], f"{where} rate")
            status = str(item[2]) if len(item) > 2 and item[2] is not None else ""
            when = public_time(item[3], "public_from") if len(item) > 3 else None
            if start not in starts:
                raise ValueError(f"{where}: {start} is not the start of a coupon period (the issue date or a coupon "
                                 "date before maturity)")
            if rate is None or rate < 0:
                raise ValueError(f"{where}: the rate for the period from {start} must be a number >= 0, not "
                                 f"{item[1]!r}")
            if start in table:
                raise ValueError(f"{where}: two rates for the period from {start}")
            table[start] = (rate, status, when)
            clean.append((start, rate, status, when))
        set_(self, "rates", tuple(sorted(clean, key=lambda r: r[0])))
        set_(self, "_rates", table)
        if self.index_pct is not None:
            set_(self, "index_pct", _num(self.index_pct, f"{where} index_pct"))

    def _rate(self, start: date) -> float:
        try:
            return self._rates[start][0]
        except KeyError:
            raise ValueError(f"{self._where}: no rate for the coupon period from {start}; pass it in rates "
                             "(goi_frb_rates gives them)") from None

    def coupon_rate(self, settlement) -> float:
        """The rate (percent a year) of the coupon period holding ``settlement``."""
        return self._rate(self._unit.previous_coupon_date(settlement))

    def rate_status(self, settlement) -> str:
        """How the current period's rate is known (``announced``, ``computed``, ``projected`` or as given)."""
        start = self._unit.previous_coupon_date(settlement)
        self._rate(start)
        return self._rates[start][1]

    def _flows(self, settlement) -> _Flows:
        s = as_date(settlement)
        unit = self._unit._flows(s)
        times, amounts, last = [], [], None
        for cf in self._unit.cash_flows(s):
            amount = self._rate(cf.accrual_start) * cf.amount if cf.kind == "coupon" else cf.amount
            if cf.date == last:
                amounts[-1] += amount
            else:
                times.append(cf.periods)
                amounts.append(amount)
                last = cf.date
        accrued = self.coupon_rate(s) * unit.accrued
        return _Flows(tuple(times), tuple(amounts), unit.m, unit.simple, accrued)

    def cash_flows(self, settlement, until=None) -> list[CashFlow]:
        """The unit bond's flows with each coupon at its period's rate (payment dates rolled on ``calendar``)."""
        return [replace(cf, amount=self._rate(cf.accrual_start) * cf.amount) if cf.kind == "coupon" else cf
                for cf in self._unit_cal.cash_flows(settlement, until)]

    def previous_coupon_date(self, settlement) -> date | None:
        return self._unit.previous_coupon_date(settlement)

    def next_coupon_date(self, settlement) -> date | None:
        return self._unit.next_coupon_date(settlement)

    def _guess(self) -> float:
        known = [r[1] for r in self.rates]
        return known[-1] / 100 if known else 0.07

    def with_rates(self, rates, index_pct: float | None = None) -> GoiFloatingRateBond:
        return replace(self, rates=tuple(tuple(r) for r in rates), index_pct=index_pct)


def goi_frb_bond(terms, rates: pd.DataFrame, *, calendar: BusinessCalendar | None = None) -> GoiFloatingRateBond:
    """A ``GoiFloatingRateBond`` from a terms row and ``goi_frb_rates``' rows (periods without a rate are left out).

    ``index_pct`` is the base of the projected periods when they share one (the flat projection).
    """
    terms = dict(terms)
    known = rates.loc[rates["rate_pct"].notna()]
    projected = known.loc[known["status"] == "projected", "base_pct"].dropna()
    index = float(projected.iloc[-1]) if not projected.empty and projected.nunique() == 1 else None
    rows = tuple((r["period_start"], float(r["rate_pct"]), r["status"], r["public_from"])
                 for r in known.to_dict("records"))
    return GoiFloatingRateBond(as_date(terms["maturity_date"]), as_date(terms["issue_date"]),
                               _num(terms.get("spread_pct"), "spread_pct") or 0.0, rows,
                               first_coupon_date=as_date(terms["first_coupon_date"]) if _present(
                                   terms.get("first_coupon_date")) else None,
                               calendar=calendar, index_pct=index, isin=str(terms["isin"]).strip(),
                               name=str(terms.get("name", "") or ""))


# ---------------------------------------------------------------- discount margin


def _index(bond: GoiFloatingRateBond, index_pct: float | None) -> float:
    if index_pct is not None:
        return float(index_pct)
    if bond.index_pct is not None:
        return bond.index_pct
    raise ValueError(f"{bond._where}: pass index_pct (the index the projected coupons stand on)")


def _curve_terms(bond: Bond, s: date, curve) -> list[tuple[float, float, float]]:
    """(amount, discount factor from settlement, years) of each flow on its scheduled date."""
    if as_date(curve.valuation_date) > s:
        raise ValueError(f"the curve's valuation date {curve.valuation_date} is after settlement {s}")
    base = float(curve.discount(s))
    return [(cf.amount, float(curve.discount(cf.date)) / base, (cf.date - s).days / 365)
            for cf in bond.cash_flows(s)]


def _curve_value(terms, dm: float) -> tuple[float, float]:
    p = d1 = 0.0
    for amount, df, tau in terms:
        v = amount * df * math.exp(-dm * tau)
        p += v
        d1 -= v * tau
    return p, d1


def price_from_dm(bond: GoiFloatingRateBond, settlement, dm_bp: float, *, index_pct: float | None = None,
                  curve=None, form: str = "index") -> tuple[float, float]:
    """(clean, dirty) price per 100 at a discount margin of ``dm_bp`` (index or curve form, module docstring)."""
    s = as_date(settlement)
    dm = float(dm_bp)
    if form not in DM_FORMS:
        raise ValueError(f"form must be one of {DM_FORMS}, not {form!r}")
    accrued = bond.accrued_interest(s)
    if form == "index":
        dirty = bond.dirty_price(_index(bond, index_pct) + dm / 100, s)
    else:
        if curve is None:
            raise ValueError("the curve form needs curve=")
        dirty = _curve_value(_curve_terms(bond, s, curve), dm / 1e4)[0]
    return dirty - accrued, dirty


def discount_margin_bp(bond: GoiFloatingRateBond, settlement, clean_price: float, *, index_pct: float | None = None,
                       curve=None, form: str = "index") -> float:
    """The discount margin (basis points) at which the bond is worth ``clean_price`` (module docstring)."""
    s = as_date(settlement)
    if form not in DM_FORMS:
        raise ValueError(f"form must be one of {DM_FORMS}, not {form!r}")
    if form == "index":
        return (bond.yield_from_price(clean_price, s) - _index(bond, index_pct)) * 100
    if curve is None:
        raise ValueError("the curve form needs curve=")
    price = float(clean_price)
    if not (math.isfinite(price) and price > 0):
        raise ValueError(f"clean price must be a positive number, not {clean_price!r}")
    target = price + bond.accrued_interest(s)
    terms = _curve_terms(bond, s, curve)
    lo, hi = -0.5, 0.5
    while _curve_value(terms, hi)[0] > target:
        hi *= 2
        if hi > 64:
            raise ValueError(f"no discount margin prices the bond at {price}")
    while _curve_value(terms, lo)[0] < target:
        lo *= 2
        if lo < -64:
            raise ValueError(f"no discount margin prices the bond at {price}")
    dm = 0.0
    for _ in range(200):
        p, d1 = _curve_value(terms, dm)
        if p > target:
            lo = dm
        else:
            hi = dm
        step = dm - (p - target) / d1 if d1 < 0 else math.nan
        nxt = step if lo < step < hi else 0.5 * (lo + hi)
        if abs(nxt - dm) < _TOL:
            return nxt * 1e4
        dm = nxt
    raise RuntimeError("discount margin did not converge")


def spread_dv01(bond: GoiFloatingRateBond, settlement, dm_bp: float, *, index_pct: float | None = None, curve=None,
                form: str = "index") -> float:
    """Fall in price per 100 face for a one basis point rise in the discount margin (an exact derivative)."""
    s = as_date(settlement)
    if form == "index":
        return bond.dv01(_index(bond, index_pct) + float(dm_bp) / 100, s)
    if form != "curve" or curve is None:
        raise ValueError("form must be 'index', or 'curve' with curve=")
    return -_curve_value(_curve_terms(bond, s, curve), float(dm_bp) / 1e4)[1] * 1e-4


def spread_duration(bond: GoiFloatingRateBond, settlement, dm_bp: float, **kwargs) -> float:
    """Spread DV01 per unit of dirty price, in years (``spread_dv01 x 1e4 / dirty``)."""
    dirty = price_from_dm(bond, settlement, dm_bp, **kwargs)[1]
    return spread_dv01(bond, settlement, dm_bp, **kwargs) * 1e4 / dirty


def index_dv01(bond: GoiFloatingRateBond, settlement, dm_bp: float, *, index_pct: float | None = None, curve=None,
               form: str = "index") -> float:
    """Fall in price for a one basis point rise in the projected index with the discount margin held.

    Projected coupons (status ``projected``) move by the bump; announced and computed ones do not. In index
    form the discount rate ``I + dm`` moves too. A central difference of half a basis point each way.
    """
    s = as_date(settlement)
    base = _index(bond, index_pct) if form == "index" else 0.0

    def priced(h: float) -> float:
        rates = [(st, r + h if status == "projected" else r, status, when) for st, r, status, when in bond.rates]
        bumped = bond.with_rates(rates, bond.index_pct)
        if form == "index":
            return price_from_dm(bumped, s, dm_bp, index_pct=base + h)[1]
        return price_from_dm(bumped, s, dm_bp, curve=curve, form="curve")[1]

    return -(priced(0.005) - priced(-0.005))


# ---------------------------------------------------------------- corporate FRBs


@dataclass(frozen=True)
class FloatingTerms:
    """A corporate FRB's coupon terms (the fields SEBI's NCS master circular, Annex XIV-A item 42, lists).

    ``benchmark``: a name in the fixings table. ``spread_pct``: added to the benchmark. ``reset_months``: how
    often the coupon resets (a multiple of the coupon period). ``fixing_lag_days``: calendar days before each
    reset the benchmark is read (UNVERIFIED when the terms are silent: 0). ``lookback``: how many of the latest
    fixings on or before that date are averaged. ``floor_pct``, ``cap_pct``: bounds on the coupon; ``decimals``:
    its rounding (half up; ``None`` unrounded).
    """

    benchmark: str
    spread_pct: float
    reset_months: int
    fixing_lag_days: int = 0
    lookback: int = 1
    floor_pct: float | None = None
    cap_pct: float | None = None
    decimals: int | None = None

    def __post_init__(self) -> None:
        if not str(self.benchmark).strip():
            raise ValueError("benchmark must be named")
        object.__setattr__(self, "benchmark", str(self.benchmark).strip())
        object.__setattr__(self, "spread_pct", float(_num(self.spread_pct, "spread_pct")))
        object.__setattr__(self, "reset_months", as_int(self.reset_months, "reset_months", minimum=1))
        object.__setattr__(self, "fixing_lag_days", as_int(self.fixing_lag_days, "fixing_lag_days", minimum=0))
        object.__setattr__(self, "lookback", as_int(self.lookback, "lookback", minimum=1))
        for name in ("floor_pct", "cap_pct"):
            value = getattr(self, name)
            object.__setattr__(self, name, None if value is None else _num(value, name))
        if self.floor_pct is not None and self.cap_pct is not None and self.floor_pct > self.cap_pct:
            raise ValueError(f"floor {self.floor_pct} is above cap {self.cap_pct}")
        if self.decimals is not None:
            object.__setattr__(self, "decimals", as_int(self.decimals, "decimals", minimum=0))

    def coupon(self, fixing_pct: float) -> float:
        rate = fixing_pct + self.spread_pct
        if self.floor_pct is not None:
            rate = max(rate, self.floor_pct)
        if self.cap_pct is not None:
            rate = min(rate, self.cap_pct)
        return round(rate, 10) if self.decimals is None else round_half_up(rate, self.decimals)


def read_floating_terms(source) -> pd.DataFrame:
    """``corporate_floating.csv``: one row per ISIN (``FLOATING_COLUMNS``)."""
    return read_term_table(source, FLOATING_COLUMNS, ("isin", "benchmark", "spread_pct", "reset_months"), ("isin",),
                           "corporate floating terms")


def floating_terms_from_row(row) -> FloatingTerms:
    rec = dict(row)
    where = rec.get("isin", "floating terms")

    def opt(name):
        return _num(rec.get(name), f"{where} {name}")

    return FloatingTerms(rec["benchmark"], opt("spread_pct"), as_int(opt("reset_months"), "reset_months"),
                         as_int(opt("fixing_lag_days") or 0, "fixing_lag_days"),
                         as_int(opt("lookback") or 1, "lookback"), opt("floor_pct"), opt("cap_pct"),
                         None if opt("decimals") is None else as_int(opt("decimals"), "decimals"))


def read_fixings(source) -> pd.DataFrame:
    """A fixings table: ``benchmark``, ``date``, ``value_pct``, ``public_from`` (required: rule 10), ``source``.

    One value per benchmark and date; ``public_from`` a time (a bare date counts from 23:59:59).
    """
    frame = read_term_table(source, FIXING_COLUMNS, ("benchmark", "date", "value_pct", "public_from"),
                            ("benchmark", "date"), "fixings")
    rows = []
    for i, rec in enumerate(frame.to_dict("records")):
        where = f"fixings row {i + 2} ({rec['benchmark']} {rec['date']})"
        rows.append({"benchmark": rec["benchmark"], "date": as_date(rec["date"]),
                     "value_pct": _num(rec["value_pct"], f"{where} value_pct"),
                     "public_from": public_time(rec["public_from"], f"{where} public_from"),
                     "source": rec.get("source", "")})
    return pd.DataFrame(rows, columns=list(FIXING_COLUMNS))


def corporate_frb_rates(bond: CorporateBond, terms: FloatingTerms, fixings: pd.DataFrame, *, as_of=None,
                        projection: str = "flat", curve=None, tenor_days: int | None = None) -> pd.DataFrame:
    """One rate per reset of a corporate FRB as known at ``as_of`` (``CORPORATE_RATE_COLUMNS``).

    Resets fall on every ``reset_months // coupon months``-th period start from issue. The fixing date is the
    reset less ``fixing_lag_days``; its value the mean of the ``lookback`` latest fixings dated on or before it
    and public by ``as_of``. A reset is ``fixed`` once its fixing date is over (before ``as_of``'s date) and
    its fixings public; else ``projected``: ``flat`` at the latest public fixings, or ``forward`` from
    ``curve`` over ``tenor_days`` (simple, 365 days). ``rate_steps`` turns the rows into the bond's steps.
    """
    if not isinstance(bond, CorporateBond):
        raise TypeError("bond must be a corporates.CorporateBond")
    if not isinstance(terms, FloatingTerms):
        raise TypeError("terms must be FloatingTerms")
    if projection not in PROJECTIONS:
        raise ValueError(f"projection must be one of {PROJECTIONS}, not {projection!r}")
    if not bond.frequency:
        raise ValueError(f"{bond._where}: a zero-coupon bond has no floating coupon")
    months = 12 // bond.frequency
    if terms.reset_months % months:
        raise ValueError(f"{bond._where}: reset_months {terms.reset_months} is not a multiple of the coupon period "
                         f"({months} months)")
    step = terms.reset_months // months
    starts = [bond.issue_date, *bond.due_schedule[:-1]][::step]
    cutoff = None if as_of is None else public_time(as_of, "as_of")
    table = read_fixings(fixings)
    mine = table.loc[table["benchmark"] == terms.benchmark].sort_values("date")
    public = [r for r in mine.to_dict("records") if cutoff is None or r["public_from"] <= cutoff]
    ends = [*starts[1:], bond.maturity]
    out = []
    for start, end in zip(starts, ends):
        fixing_date = start - timedelta(days=terms.fixing_lag_days)
        used = [r for r in public if r["date"] <= fixing_date][-terms.lookback:]
        over = cutoff is None or cutoff.date() > fixing_date
        rec = {"period_start": start, "fixing_date": fixing_date, "fixings_used": "", "fixing_pct": math.nan,
               "rate_pct": math.nan, "status": "unknown", "public_from": None, "notes": ""}
        if over and len(used) == terms.lookback:
            value = sum(r["value_pct"] for r in used) / len(used)
            rec.update(fixings_used=";".join(str(r["date"]) for r in used), fixing_pct=value,
                       rate_pct=terms.coupon(value), status="fixed",
                       public_from=_as_datetime(max(r["public_from"] for r in used)))
        elif cutoff is not None and end <= cutoff.date():
            rec["notes"] = "the period has ended and its fixing is not in the table"
        elif projection == "forward":
            if curve is None or tenor_days is None:
                raise ValueError("a forward projection needs curve= and tenor_days=")
            if fixing_date >= curve.valuation_date:
                value = forward_tbill_yield(curve, fixing_date, tenor_days)
                rec.update(fixing_pct=value, rate_pct=terms.coupon(value), status="projected",
                           notes="forward projection")
        elif public:
            latest = public[-terms.lookback:]
            value = sum(r["value_pct"] for r in latest) / len(latest)
            note = f"flat at the fixings public by {cutoff:%Y-%m-%d %H:%M}" if cutoff else "flat"
            if over:
                note += f"; the fixing of {fixing_date} is not in the table"
            rec.update(fixings_used=";".join(str(r["date"]) for r in latest), fixing_pct=value,
                       rate_pct=terms.coupon(value), status="projected", notes=note)
        out.append(rec)
    return pd.DataFrame(out, columns=list(CORPORATE_RATE_COLUMNS))


def rate_steps(rates: pd.DataFrame) -> tuple[tuple[date, float, str], ...]:
    """``CorporateBond.rate_steps`` from ``corporate_frb_rates``' rows.

    Leading resets without a rate (periods that ended before ``as_of`` with no fixing in the table) are left out:
    the bond's ``coupon_pct`` stands for them, so value it only after them. A reset without a rate after one
    with a rate is refused.
    """
    rows = rates.to_dict("records")
    while rows and pd.isna(rows[0]["rate_pct"]):
        rows.pop(0)
    missing = [r["period_start"] for r in rows if pd.isna(r["rate_pct"])]
    if missing or not rows:
        raise ValueError(f"no rate for the resets from {missing[0] if missing else 'the first'}: pass the fixings "
                         "or a projection")
    return tuple((r["period_start"], float(r["rate_pct"]), r["status"]) for r in rows)


__all__ = [
    "CORPORATE_RATE_COLUMNS",
    "DM_FORMS",
    "FIXING_COLUMNS",
    "FLOATING_COLUMNS",
    "FRB_COUPONS_CSV",
    "FRB_COUPON_COLUMNS",
    "FRB_INDICES",
    "FRB_RATE_COLUMNS",
    "FRB_TERMS_COLUMNS",
    "FRB_TERMS_CSV",
    "PROJECTIONS",
    "TBILL_COLUMNS",
    "FloatingTerms",
    "FrbFixing",
    "GoiFloatingRateBond",
    "corporate_frb_rates",
    "discount_margin_bp",
    "floating_terms_from_row",
    "forward_tbill_yield",
    "frb_periods",
    "goi_frb_bond",
    "goi_frb_fixing",
    "goi_frb_rates",
    "index_dv01",
    "load_frb_coupons",
    "load_frb_terms",
    "price_from_dm",
    "rate_steps",
    "read_fixings",
    "read_floating_terms",
    "spread_duration",
    "spread_dv01",
    "tbill_way_table",
    "way_from_wap",
]
