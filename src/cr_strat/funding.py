"""Funding a long G-sec in repo: daily rates from the RBI store, haircuts kept by the user, and carry (strategy S1).

**Rates** (``funding_rates``). RBI's Money Market Operations, as ``rbi_store``'s ``money_market_daily`` table holds
them (``rbi_store.read_table(store, "money_market_daily")``): one row per as-on date, weekends and holidays
included. ``source`` picks the overnight segment: ``triparty`` (TREPS, CCIL's tri-party repo, the default),
``market_repo`` (basket and special repo together), ``call`` (the weighted average call rate, WACR: the operating
target and the free stand-in for overnight MIBOR, a whole-day average where FBIL's MIBOR averages a morning window)
or ``overnight`` (all segments). A row is a dealing day when it carries a rate; weekend and holiday rows carry none
and are never filled forward. A dealing day's rate runs to the next dealing day: a Friday's covers the weekend, and a
Saturday row with a rate and a volume (money markets deal on some Saturdays) is a dealing day of its own. The SDF and
MSF rates of each row bound the corridor (SDF = repo - 25 bp, MSF = repo + 25 bp, symmetric since the SDF replaced the
fixed-rate reverse repo as the floor on 8 Apr 2022, RBI press release 2022-2023/41; the floor was 65 bp below repo in
the pandemic years before; the framework of 30 Sep 2025 kept the corridor); a rate outside it is flagged ``below_sdf``
or ``above_msf``, never clipped (lenders without SDF access have pushed overnight rates below it).
``quarter_end_day`` and ``fy_end_day`` come from the store: advance tax and GST outflows drain liquidity around
quarter-ends and 31 March, and no stored history covers one yet.

**When a rate was public.** RBI publishes a day's Money Market Operations the next working day at about 9:00 India
time; the store's ``public_from`` says from when each row was public. ``funding_rates(..., as_of=t)`` keeps only
rows public by ``t`` (a row with no time is dropped, and so is one ``revised`` after ``t``: the store keeps a revised
row's latest values only), and nothing after the first row that was not, so a signal or an expected carry computed at
``t`` never sees a later rate. ``carry(..., as_of=t)`` cuts its funding tables the same way and uses only the MIBOR
fixings published by ``t`` (FBIL publishes by 12:45 on the day, ``ois.fixing_regime``; ``fixing_times`` for a
stand-in published later, such as the call rate, ``fixing_times_from``). Realised funding P&L may use each day's
rate, whenever it was published: leave ``as_of`` out.

**Haircuts** (``read_haircuts``, ``haircut``). RBI sets minimum haircuts only for corporate bonds, CP, CD and
local-authority securities; for government securities the clearing house decides (RBI Repo Directions 2025,
para 12). CCIL's TREPS list (marked restricted, so never shipped here) gives every T-bill 1%, every SDL a uniform
10% (15% in Feb 2026) and dated G-secs a haircut by security and liquidity class. The user keeps a dated table:
``effective_date``, optional ``isin``, ``kind``, residual-maturity bucket (``min_years`` < years <= ``max_years``,
365.25-day years) and ``liquidity`` class, ``haircut_pct`` and ``source``. The latest notification that matches
wins, then the most specific row (an ISIN over a bucket over a kind); two equally specific rows that differ, or no
row at all, raise. Market repo (CROMS) haircuts were not found (UNVERIFIED): use the TREPS table. Since 1 Jun 2026
CCIL caps the borrowing limit against illiquid G-secs and SDLs at 50% (SDLs 30%) of the haircut-adjusted value of
the liquid and semi-liquid G-secs deposited (CCIL RMD/SS/26/13), illiquid G-secs served first: an SDL-only book has
no TREPS limit at all. ``carry_table`` applies the caps to the long positions of a book funded in TREPS
(``treps_shares``) and funds the rest at ``excess_funding``'s rates (market repo, say); a G-sec without a liquidity
class counts as illiquid (conservative), T-bills and CMBs (listed by CCIL without a class) as uncapped and outside the
caps' base (UNVERIFIED), and CP, CD and special securities are refused (not on CCIL's list). Before 2 Jun 2026 the
earlier rule (RMD/SS/21/21 of 16 Apr 2021, not read) is taken as CCIL's TREPS FAQ states it through a web-search
summary (UNVERIFIED): illiquid G-secs and SDLs together up to 20% of that base, illiquid first; before 16 Apr 2021 no
rule is known and TREPS is taken to lend nothing against them (conservative).

**Carry** (``carry``) over ``[start, end)``, per position, in rupees (analytics spec 4.3, F14):

* ``income``: coupons due in ``(start, end]`` plus the change in accrued interest (``bonds``: 30E/360 for G-secs
  and SDLs), i.e. ``F c/100 x days30E/360`` over regular half-years;
* ``pull_to_par``: the clean price's move from ``start`` to ``end`` at the purchase yield (on by default);
* ``repo``: interest on the cash borrowed, ``B = (1 - h) P F / 100``, at each day's rate,
  ``B sum_d R_d n_d / 36500`` over the dealing days (simple: the interest is paid out at each overnight roll;
  UNVERIFIED, ``repo_compounding="compound"`` rolls it into the next deal). TREPS and market repo interest are
  simple Actual/365 (CCIL's TREPS FAQ; the RBI G-sec FAQ for money markets);
* ``equity_cost``: the haircut ``h P F / 100`` funded with own cash at ``equity_rate_pct`` (default: the same daily
  funding rates; UNVERIFIED, an opportunity cost);
* with an OIS (``swap``): ``fixed`` accrued at ``N K days / 36500`` and ``floating`` calculation period by period,
  ``N (A(p.start, hi) - A(p.start, lo))`` over each overlap ``[lo, hi)`` of a period ``p`` with the horizon, from the
  MIBOR fixings of its Mumbai business days (``ois.compound_overnight``; the last cut at ``end``): a running period's
  interest compounds from its own start, so the fixings since then are needed, and interest paid at a period's end is
  not compounded on. ``swap_carry`` is their difference for the swap's side. Business days after the last fixing
  known take ``projected_fixings`` (``OisCurve.overnight_forwards``) or, with ``projection="last"``, the last fixing
  held (``projected_fixings`` counts them).
* ``side="short"``: the bond sold and borrowed in a reverse repo at its special repo rate (``reverse_repo_rate_pct``,
  given by the user: security-wise repo rates are CCIL's licensed data). ``income`` and ``pull_to_par`` change sign
  (coupons are passed to the bond's lender), ``repo`` is the (negative) interest earned on the cash lent, ``(1 - h)``
  of the sale proceeds, and ``equity_cost`` the (negative) return on the proceeds kept.

``net = income + pull_to_par - repo - equity_cost + swap_carry`` and ``net_bp_annual`` its rate on face
(365-day year). ``spread_bp`` adds a spread to every repo rate of a long: what a fund that is not a CCIL member (CCIL
asks for Rs 200 crore of net worth) pays the bank or primary dealer it deals through (UNVERIFIED default 0). It does
not touch a short, whose reverse repo is at the special rate the user gives (any spread is in that rate) and whose
proceeds kept earn the plain funding rates; a note says so. Roll-down
along an unchanged curve and the swap's change in value are not carry and are left out. A horizon running past the
last row of the funding table is refused unless ``projection="last"`` holds the last dealing rate flat (counted in
``projected_days`` and noted); a calendar day missing inside the table is always refused (a missing row cannot be
told from a dealing day).

**Limits.** The demonstration (scratch, not shipped) used four stored days of 13 to 16 Nov 2026; nothing here has
seen a quarter-end, special repo, a security's own repo rate (CCIL, licensed) or a real haircut table. The fund's
own access to TREPS (CCIL's net-worth bar for membership) and the spread it pays as a constituent are open questions;
no bid-offer, CCIL settlement charge, fee or tax enters.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from cr_strat.asset_swaps import instrument_from_row
from cr_strat.bonds import DiscountInstrument, FixedRateBond
from cr_strat.calendars import BusinessCalendar, CalendarCoverageError, calendar_for
from cr_strat.conventions import as_date, days_30e_360
from cr_strat.ois import (
    OisSwap,
    as_time,
    beyond_calendar,
    compound_overnight,
    fixing_map,
    fixing_regime,
    ois_schedule,
)
from cr_strat.policy_path import policy_rate_on, read_policy_table

FUNDING_SOURCES = ("triparty", "market_repo", "call", "overnight")
FUNDING_COLUMNS = ("date", "dealing_day", "rate_pct", "volume_cr", "low_pct", "high_pct", "sdf_pct", "msf_pct",
                   "repo_pct", "below_sdf", "above_msf", "business_day", "quarter_end_day", "fy_end_day",
                   "public_from", "revised", "source", "notes")
HAIRCUT_COLUMNS = ("effective_date", "isin", "kind", "min_years", "max_years", "liquidity", "haircut_pct", "source")
LIQUIDITY_CLASSES = ("liquid", "semi_liquid", "illiquid")
REPO_COMPOUNDING = ("simple", "compound")
PROJECTIONS = ("last",)
CARRY_COLUMNS = ("isin", "kind", "side", "coupon_pct", "maturity_date", "start", "end", "days", "face", "clean_price",
                 "dirty_price", "haircut_pct", "cash_borrowed", "funding_share", "income", "pull_to_par", "repo",
                 "repo_other", "equity_cost", "fixed", "floating", "swap_carry", "net", "net_bp_annual", "dealing_days",
                 "projected_days", "projected_fixings", "quarter_end_days", "calendar_projected", "notes", "error")
SIDES = ("long", "short")
TREPS_CAP_FROM = date(2026, 6, 2)  # CCIL RMD/SS/26/13 (29 Apr 2026): effective from the end of the day on 1 Jun 2026
TREPS_ILLIQUID_CAP, TREPS_SDL_CAP = 0.50, 0.30  # shares of the liquid and semi-liquid GoI base (RMD/SS/26/13)
# UNVERIFIED: the rule before 2 Jun 2026, CCIL's RMD/SS/21/21 of 16 Apr 2021 (not read), as CCIL's TREPS FAQ states
# it through a web-search summary (19 Nov 2026): illiquid GoI securities and SDLs together up to 20% of the
# haircut-adjusted liquid and semi-liquid base, illiquid first, nothing on a stand-alone basis; taken to apply from the
# notice's date
TREPS_EARLIER_FROM = date(2021, 4, 16)
TREPS_EARLIER_CAP = 0.20
DAILY_COLUMNS = ("date", "rate_pct", "rate_from", "deal_day", "projected", "quarter_end_day", "fy_end_day")
_YEAR = 365.0
_BOOL = {"true": True, "false": False, "yes": True, "no": False, "1": True, "0": False}


def _blank(value) -> bool:
    if value is None or (isinstance(value, str) and not value.strip()):
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _bool_or_none(value, where: str):
    if _blank(value):
        return None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    text = str(value).strip().lower()
    if text in _BOOL:
        return _BOOL[text]
    raise ValueError(f"{where}: {value!r} is not True or False")


def _float_or_nan(value, where: str) -> float:
    if _blank(value):
        return math.nan
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{where}: {value!r} is not a number") from None
    if not math.isfinite(out):
        raise ValueError(f"{where}: {value!r} is not a finite number")
    return out


def _time(value, where: str):
    """A ``public_from`` cell as a naive datetime (India time, as the stores write it), or ``None`` if blank."""
    return None if _blank(value) else as_time(value, f"{where}: public_from")


def _public_at(row: dict, cutoff) -> bool:
    """Whether a funding row's values were all public at ``cutoff``: published by then and not revised since
    (``rbi_store`` keeps only a revised row's latest values, so before its revision they are not known)."""
    if row["public_from"] is None or row["public_from"] > cutoff:
        return False
    return row.get("revised") is None or row["revised"] <= cutoff


def _cut(rows: list[dict], cutoff) -> list[dict]:
    """The rows up to the first one not public at ``cutoff`` (sorted by date)."""
    kept = []
    for row in rows:
        if not _public_at(row, cutoff):
            break
        kept.append(row)
    return kept


def funding_rates(money_market_daily: pd.DataFrame, source: str = "triparty", *, as_of=None,
                  policy: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per as-on date of ``money_market_daily`` (``FUNDING_COLUMNS``): the chosen segment's rate and checks.

    ``as_of`` (a datetime, India time; a date means its midnight) keeps only rows public by then and none after the
    first that was not. A row ``revised`` after ``as_of`` counts as not yet public: the store keeps only its latest
    values (the earlier ones are in ``revisions.csv``; rebuilding them is not built). ``policy`` (``effective_date``,
    ``repo_pct``) gives ``repo_pct``; without it the table's ``repo_rate_pct`` is used where present. A rate printed
    with a volume of zero is not a dealing day (noted).
    """
    if source not in FUNDING_SOURCES:
        raise ValueError(f"source must be one of {FUNDING_SOURCES}, not {source!r}")
    if not isinstance(money_market_daily, pd.DataFrame):
        raise TypeError("money_market_daily must be a DataFrame (rbi_store.read_table(store, 'money_market_daily'))")
    need = ["as_on_date", f"{source}_rate_pct", "public_from"]
    missing = [c for c in need if c not in money_market_daily]
    if missing:
        raise ValueError(f"money_market_daily lacks {missing}: is it rbi_store's money_market_daily table?")
    rules = read_policy_table(policy) if policy is not None else None
    cutoff = None if as_of is None else as_time(as_of, "as_of")
    rows, seen = [], set()
    for n, rec in enumerate(money_market_daily.to_dict("records"), start=1):
        where = f"money_market_daily row {n}"
        day = as_date(rec["as_on_date"])
        if day in seen:
            raise ValueError(f"{where}: as_on_date {day} appears twice")
        seen.add(day)
        rate = _float_or_nan(rec.get(f"{source}_rate_pct"), f"{where}: {source}_rate_pct")
        volume = _float_or_nan(rec.get(f"{source}_volume_cr"), f"{where}: {source}_volume_cr")
        notes = []
        if not math.isnan(rate) and volume == 0:
            notes.append(f"a {source} rate of {rate:g}% with no volume: not a dealing day")
            rate = math.nan
        sdf = _float_or_nan(rec.get("sdf_rate_pct"), f"{where}: sdf_rate_pct")
        msf = _float_or_nan(rec.get("msf_rate_pct"), f"{where}: msf_rate_pct")
        repo = policy_rate_on(rules, day) if rules is not None else _float_or_nan(rec.get("repo_rate_pct"),
                                                                                    f"{where}: repo_rate_pct")
        below = bool(not math.isnan(rate) and not math.isnan(sdf) and rate < sdf - 1e-9)
        above = bool(not math.isnan(rate) and not math.isnan(msf) and rate > msf + 1e-9)
        if below:
            notes.append(f"{source} rate {rate:g}% below the SDF rate {sdf:g}% (kept)")
        if above:
            notes.append(f"{source} rate {rate:g}% above the MSF rate {msf:g}% (kept)")
        rows.append({"date": day, "dealing_day": not math.isnan(rate), "rate_pct": rate, "volume_cr": volume,
                     "low_pct": _float_or_nan(rec.get(f"{source}_low_pct"), f"{where}: {source}_low_pct"),
                     "high_pct": _float_or_nan(rec.get(f"{source}_high_pct"), f"{where}: {source}_high_pct"),
                     "sdf_pct": sdf, "msf_pct": msf, "repo_pct": repo, "below_sdf": below, "above_msf": above,
                     "business_day": _bool_or_none(rec.get("business_day"), f"{where}: business_day"),
                     "quarter_end_day": _bool_or_none(rec.get("quarter_end_day"), f"{where}: quarter_end_day"),
                     "fy_end_day": _bool_or_none(rec.get("fy_end_day"), f"{where}: fy_end_day"),
                     "public_from": _time(rec.get("public_from"), where),
                     "revised": None if _blank(rec.get("revised")) else as_time(rec["revised"], f"{where}: revised"),
                     "source": source, "notes": "; ".join(notes)})
    rows.sort(key=lambda r: r["date"])
    if cutoff is not None:
        rows = _cut(rows, cutoff)
    frame = pd.DataFrame(rows, columns=list(FUNDING_COLUMNS))
    for column in ("business_day", "quarter_end_day", "fy_end_day"):
        frame[column] = frame[column].astype("boolean")
    frame["dealing_day"] = frame["dealing_day"].astype(bool)
    return frame


def _funding_days(funding: pd.DataFrame) -> dict[date, dict]:
    if not isinstance(funding, pd.DataFrame):
        raise TypeError("funding must be a frame from funding_rates")
    missing = [c for c in ("date", "dealing_day", "rate_pct") if c not in funding]
    if missing:
        raise ValueError(f"funding lacks {missing}: build it with funding_rates")
    out = {}
    for rec in funding.to_dict("records"):
        day = as_date(rec["date"])
        if day in out:
            raise ValueError(f"funding lists {day} twice")
        rate = _float_or_nan(rec["rate_pct"], f"funding {day}: rate_pct")
        dealing = bool(rec["dealing_day"]) and not math.isnan(rate)
        out[day] = {"rate": rate if dealing else math.nan, "dealing": dealing,
                    "quarter_end_day": _bool_or_none(rec.get("quarter_end_day"), f"funding {day}"),
                    "fy_end_day": _bool_or_none(rec.get("fy_end_day"), f"funding {day}")}
    return out


def fixings_from(funding: pd.DataFrame, *, calendar: BusinessCalendar | None = None) -> dict[date, float]:
    """The dealing-day rates of a ``funding_rates`` frame on Mumbai business days, as fixings for
    ``ois.compound_overnight``: WACR (``source="call"``) as a stand-in for overnight MIBOR, in demonstrations only.

    A dealing Saturday is left out: MIBOR is never fixed on one.
    """
    cal = calendar if calendar is not None else calendar_for("mumbai_settlement")
    return {d: v["rate"] for d, v in sorted(_funding_days(funding).items()) if v["dealing"] and cal.is_business_day(d)}


def fixing_times_from(funding: pd.DataFrame) -> dict[date, datetime | None]:
    """When each rate of a ``funding_rates`` frame became public (its ``public_from``), for ``carry(as_of=...,
    fixing_times=...)`` when ``fixings_from`` stands in for MIBOR (RBI publishes a day's rates the next morning, FBIL
    its MIBOR the same day)."""
    if not isinstance(funding, pd.DataFrame) or "public_from" not in funding or "date" not in funding:
        raise ValueError("fixing times need a funding_rates frame (date, public_from)")
    return {as_date(r["date"]): _time(r["public_from"], f"funding {r['date']}") for r in funding.to_dict("records")}


# ---------------------------------------------------------------- haircuts


def read_haircuts(table) -> pd.DataFrame:
    """A haircut table kept by the user, checked (``HAIRCUT_COLUMNS``; ``isin``, ``kind``, the bucket bounds and
    ``liquidity`` may be blank). ``table`` is a DataFrame or a CSV path."""
    frame = pd.read_csv(table, dtype=str, keep_default_na=False) if not isinstance(table, pd.DataFrame) else table
    frame = frame.copy()
    frame.columns = [str(c).strip().lower() for c in frame.columns]
    missing = [c for c in ("effective_date", "haircut_pct", "source") if c not in frame]
    if missing:
        raise ValueError(f"the haircut table lacks {missing} (columns: {', '.join(HAIRCUT_COLUMNS)})")
    rows = []
    for n, rec in enumerate(frame.to_dict("records"), start=2):
        where = f"haircut table line {n}"
        try:
            effective = as_date(rec["effective_date"])
        except (ValueError, TypeError):
            raise ValueError(f"{where}: effective_date {rec['effective_date']!r} is not a date") from None
        cut = _float_or_nan(rec["haircut_pct"], f"{where}: haircut_pct")
        if math.isnan(cut) or not 0 <= cut < 100:
            raise ValueError(f"{where}: haircut_pct must be a percentage from 0 to below 100, not "
                             f"{rec['haircut_pct']!r}")
        lo = _float_or_nan(rec.get("min_years"), f"{where}: min_years")
        hi = _float_or_nan(rec.get("max_years"), f"{where}: max_years")
        if not math.isnan(lo) and not math.isnan(hi) and not lo < hi:
            raise ValueError(f"{where}: min_years {lo:g} must be below max_years {hi:g}")
        liquidity = "" if _blank(rec.get("liquidity")) else str(rec["liquidity"]).strip().lower().replace("-", "_")
        if liquidity and liquidity not in LIQUIDITY_CLASSES:
            raise ValueError(f"{where}: liquidity must be one of {LIQUIDITY_CLASSES} or blank, not "
                             f"{rec['liquidity']!r}")
        isin = "" if _blank(rec.get("isin")) else str(rec["isin"]).strip().upper()
        kind = "" if _blank(rec.get("kind")) else str(rec["kind"]).strip().lower()
        if not isin and not kind:
            raise ValueError(f"{where}: give an isin or a kind")
        if _blank(rec.get("source")):
            raise ValueError(f"{where}: source is empty; say which notification the haircut comes from")
        rows.append({"effective_date": effective, "isin": isin, "kind": kind, "min_years": lo, "max_years": hi,
                     "liquidity": liquidity, "haircut_pct": cut, "source": str(rec["source"]).strip()})
    out = pd.DataFrame(rows, columns=list(HAIRCUT_COLUMNS))
    key = ["effective_date", "isin", "kind", "min_years", "max_years", "liquidity"]
    dupes = out[out.duplicated(key, keep=False)]
    if not dupes.empty:
        row = dupes.iloc[0]
        raise ValueError(f"the haircut table lists one rule twice: {row['effective_date']} "
                         f"{row['isin'] or row['kind']}")
    return out.sort_values(key, kind="mergesort", na_position="first").reset_index(drop=True)


def haircut(table, *, kind: str, maturity, on, isin: str | None = None, liquidity: str | None = None) -> float:
    """The haircut (percent) on ``on`` for a security of ``kind`` maturing ``maturity`` (and ``isin``, ``liquidity``).

    The matching rows of the latest ``effective_date`` on or before ``on`` decide; among them an ISIN row beats a
    kind row, and a kind row with a bucket and a class beats one with fewer. Raises when nothing matches (no default
    haircut is assumed) or when equally specific rows disagree.
    """
    rules = read_haircuts(table)
    d, m = as_date(on), as_date(maturity)
    years = (m - d).days / 365.25
    kind = str(kind).strip().lower()
    isin = None if isin is None else str(isin).strip().upper()
    liquidity = None if liquidity is None else str(liquidity).strip().lower().replace("-", "_")
    matches = []
    for rec in rules.to_dict("records"):
        if rec["effective_date"] > d:
            continue
        if rec["isin"]:
            if rec["isin"] != isin:
                continue
            score = 10
        else:
            if rec["kind"] != kind:
                continue
            if not math.isnan(rec["min_years"]) and not years > rec["min_years"]:
                continue
            if not math.isnan(rec["max_years"]) and not years <= rec["max_years"]:
                continue
            if rec["liquidity"] and rec["liquidity"] != liquidity:
                continue
            score = 1 + (not (math.isnan(rec["min_years"]) and math.isnan(rec["max_years"]))) + bool(rec["liquidity"])
        matches.append((rec["effective_date"], score, rec))
    who = f"{isin} (a {kind} maturing {m})" if isin else f"a {kind} maturing {m}"
    if not matches:
        raise ValueError(f"no haircut for {who} on {d}, {years:.2f} years to run, liquidity {liquidity or 'not given'}:"
                         " add its row to the haircut table (no default is assumed)")
    latest = max(e for e, _, _ in matches)
    best = max(s for e, s, _ in matches if e == latest)
    chosen = {r["haircut_pct"] for e, s, r in matches if e == latest and s == best}
    if len(chosen) > 1:
        raise ValueError(f"the haircut table gives {who} on {d} several haircuts ({sorted(chosen)}) in its "
                         f"{latest} rows")
    return chosen.pop()


# ---------------------------------------------------------------- carry


@dataclass(frozen=True, eq=False)
class CarryBreakdown:
    """``carry``'s answer, in rupees for the whole position (see the module docstring for each line).

    ``days``: calendar days of the horizon; ``days_30e``: its 30E/360 days. ``side``: ``long`` or ``short``.
    ``cash_borrowed`` and ``equity``: the repo's first leg and the haircut paid from own cash (for a short, the cash
    lent in the reverse repo that borrows the bond and the sale proceeds kept, both negative). ``funding_share``:
    the share of the cash borrowed at ``funding``'s rates, the rest at ``other_funding``'s (``repo_other`` of
    ``repo``). ``dealing_days`` counts the funding rates used, and ``projected_days`` the calendar days held at the
    last known rate; ``projected_fixings`` the business days whose MIBOR was not known (projected). ``quarter_end_days``
    lists the quarter- and year-end days in the horizon. ``calendar_projected``: some date of the horizon or the swap
    lies in a year the calendar's holiday list does not cover. ``daily`` has one row per calendar day
    (``DAILY_COLUMNS``): the rate it accrued at, the dealing day it came from and the overnight deal it belongs to (a
    business day for projected days).
    """

    start: date
    end: date
    days: int
    days_30e: int
    side: str
    face: float
    clean_price: float
    dirty_price: float
    haircut_pct: float
    cash_borrowed: float
    equity: float
    funding_share: float
    income: float
    pull_to_par: float
    repo: float
    repo_other: float
    equity_cost: float
    fixed: float
    floating: float
    swap_carry: float
    net: float
    net_bp_annual: float
    dealing_days: int
    projected_days: int
    projected_fixings: int
    quarter_end_days: tuple[date, ...]
    calendar_projected: bool
    notes: tuple[str, ...]
    daily: pd.DataFrame


def _daily_rates(funding: pd.DataFrame, start: date, end: date, projection: str | None,
                 calendar: BusinessCalendar) -> tuple[list[dict], list[str]]:
    table = _funding_days(funding)
    if not table:
        raise ValueError("the funding table is empty")
    first, last = min(table), max(table)
    dealing = sorted(d for d, v in table.items() if v["dealing"] and d <= min(start, last))
    if not dealing:
        raise ValueError(f"the funding table has no dealing day on or before {start} (it starts {first})")
    source = dealing[-1]
    out, notes, projected = [], [], 0
    day = source
    while day < end:
        if day <= last:
            row = table.get(day)
            if row is None:
                raise ValueError(f"the funding table has no row for {day}; a day without a row cannot be told from a "
                                 "dealing day (rbi-store-status lists such gaps)")
            if row["dealing"]:
                source = day
            if day >= start:
                out.append({"date": day, "rate_pct": table[source]["rate"], "rate_from": source, "deal_day": source,
                            "projected": False, "quarter_end_day": bool(row["quarter_end_day"]),
                            "fy_end_day": bool(row["fy_end_day"])})
        else:
            if projection is None:
                raise ValueError(f"the funding table ends {last} (as known when it was read), before the horizon ends "
                                 f"{end}: pass projection='last' to hold the last rate, or a later table")
            if day >= start:
                quarter, march = _period_end(day, calendar)
                rolled = calendar.lenient().adjust(day, "preceding")  # the assumed overnight deal of a projected day
                out.append({"date": day, "rate_pct": table[source]["rate"], "rate_from": source,
                            "deal_day": rolled if rolled > last else source, "projected": True,
                            "quarter_end_day": quarter, "fy_end_day": march})
                projected += 1
        day += timedelta(days=1)
    if projected:
        notes.append(f"{projected} day(s) after {last} held at the {source} rate of {table[source]['rate']:g}% "
                     "(projection; no quarter-end spike assumed)")
    return out, notes


def _period_end(day: date, calendar: BusinessCalendar) -> tuple[bool, bool]:
    """Whether ``day`` is the last business day of a quarter, and of March (weekdays alone where not covered)."""
    if day.month % 3:
        return False, False
    cal = calendar if calendar.covers(day) else calendar.lenient()
    month_end = (date(day.year + day.month // 12, day.month % 12 + 1, 1) - timedelta(days=1))
    try:
        last = cal.adjust(month_end, "preceding")
    except CalendarCoverageError:  # pragma: no cover - lenient never raises
        return False, False
    return day == last, day == last and day.month == 3


def _funding_at(funding: pd.DataFrame, cutoff, what: str) -> pd.DataFrame:
    """``funding`` cut as ``funding_rates(as_of=cutoff)`` cuts it: up to the first row not public at ``cutoff``."""
    if "public_from" not in funding:
        raise ValueError(f"as_of needs the {what} table's public_from (funding_rates gives it)")
    rows = []
    for pos, rec in enumerate(funding.to_dict("records")):
        rows.append({"pos": pos, "date": as_date(rec["date"]), "public_from": _time(rec.get("public_from"), what),
                     "revised": None if _blank(rec.get("revised")) else as_time(rec["revised"], f"{what}: revised")})
    rows.sort(key=lambda r: r["date"])
    keep = [r["pos"] for r in _cut(rows, cutoff)]
    return funding.iloc[keep]


def fbil_published_at(day) -> datetime | None:
    """When FBIL's overnight MIBOR of ``day`` was public: its publication time that day (``ois.fixing_regime``'s
    ``published_by``, 12:45 now), ``None`` where that is not known."""
    d = as_date(day)
    regime = fixing_regime(d)
    if not regime["published_by"]:
        return None
    hour, minute = (int(v) for v in regime["published_by"].split(":"))
    return datetime.combine(d, time(hour, minute))


def _published_at(day: date, times: dict | None) -> datetime | None:
    """When the overnight fixing of ``day`` was public. With ``times`` (a mapping for a stand-in such as the call rate,
    ``fixing_times_from``), ``times[day]``: a day it lacks or gives no time counts as not known, hence never public,
    since FBIL's time would look ahead for a rate published the next morning. Without it, FBIL's publication time that
    day (``fbil_published_at``; ``None`` where not known). A fixings file's blank cell is FBIL's time: the command line
    fills it in (``cli._read_fixings``)."""
    if times is not None:
        value = times.get(day)
        return None if _blank(value) else as_time(value, f"publication time of the fixing of {day}")
    return fbil_published_at(day)


def _floating_fixings(fixings, projected_fixings, start: date, end: date, cal: BusinessCalendar, cutoff,
                      fixing_times, projection: str | None) -> tuple[dict[date, float], int, list[str]]:
    """The fixings a floating leg compounds over ``[start, end)``: the given ones (only those public by ``cutoff``
    when it is set), then for business days after the last usable one ``projected_fixings`` or, with
    ``projection="last"``, the last usable fixing held. Days before the last usable fixing without one are left to
    ``compound_overnight``'s ``missing`` rule."""
    given = fixing_map(fixings)
    notes = []
    if cutoff is not None:
        times = None if fixing_times is None else {as_date(k): v for k, v in fixing_times.items()}
        late = sorted(d for d in given if (_published_at(d, times) or datetime.max) > cutoff)
        if late:
            notes.append(f"{len(late)} fixing(s) from {late[0]} not public by {cutoff} left out")
        given = {d: r for d, r in given.items() if d not in set(late)}
    days = cal.business_days(start, end - timedelta(days=1))
    usable = sorted(d for d in given if start <= d < end or d < start)
    last = max(usable) if usable else None
    future = [d for d in days if last is None or d > last]
    if not future:
        return given, 0, notes
    extra = {} if projected_fixings is None else fixing_map(projected_fixings)
    out = dict(given)
    held = 0
    for d in future:
        if d in extra:
            out[d] = extra[d]
        elif projection == "last" and last is not None:
            out[d] = given[last]
            held += 1
        else:
            when = f"public by {cutoff}" if cutoff is not None else "given"
            raise ValueError(f"no fixing {when} for {len(future)} business day(s) from {future[0]} to {future[-1]}: "
                             "pass projected_fixings (a curve's overnight_forwards) or projection='last' to hold the "
                             "last one")
    if held:
        notes.append(f"{held} business day(s) from {future[0]} with no fixing known: held at the {last} fixing of "
                     f"{given[last]:g}% (projection)")
    if len(future) > held:
        notes.append(f"{len(future) - held} business day(s) from {future[0]} on projected fixings")
    return out, len(future), notes


def _repo_interest(cash: float, daily: list[dict], compounding: str, spread_bp: float) -> float:
    rates = np.array([r["rate_pct"] for r in daily], dtype=float) + spread_bp / 100
    if compounding == "simple":
        return cash * float(rates.sum()) / (100 * _YEAR)
    growth = 1.0
    for rate, n in _segments(daily):
        growth *= 1 + (rate + spread_bp / 100) * n / (100 * _YEAR)
    return cash * (growth - 1)


def carry(bond, face: float, clean_price: float, start, end, *, funding: pd.DataFrame, haircut_pct: float,
          swap: OisSwap | None = None, fixings: Mapping | pd.Series | None = None,
          equity_rate_pct: float | None = None, calendar: BusinessCalendar | None = None,
          repo_compounding: str = "simple", projection: str | None = None, pull_to_par: bool = True,
          missing: str = "raise", as_of=None, fixing_times: Mapping | None = None,
          projected_fixings: Mapping | None = None, spread_bp: float = 0.0, funding_share: float = 1.0,
          other_funding: pd.DataFrame | None = None, side: str = "long",
          reverse_repo_rate_pct: float | None = None) -> CarryBreakdown:
    """Carry from ``start`` (the purchase's settlement, a business day) to ``end`` of ``face`` rupees of ``bond``
    bought at ``clean_price``, funded in repo at the rates of ``funding`` (``funding_rates``) with ``haircut_pct``.

    ``swap`` adds an OIS (pay fixed for the S1 asset swap) whose floating leg compounds ``fixings`` (overnight MIBOR)
    period by period: the running period's interest from its start, a new period's from its own, so the interest of a
    payment inside the horizon is not compounded on; ``missing`` as in ``ois.compound_overnight``. ``as_of`` keeps
    what a signal could know then: the funding tables cut as ``funding_rates(as_of=...)`` cuts them, and only the
    fixings published by then (``fixing_times``, or FBIL's publication time on the day). Business days after the last
    usable fixing take ``projected_fixings`` (e.g. ``OisCurve.overnight_forwards``) or, with ``projection="last"``,
    the last fixing held; otherwise they are refused. A day that ``fixing_times`` lacks, or gives no time, counts as
    not yet public (FBIL's time would look ahead for a stand-in published the next morning). ``spread_bp`` is added
    to every repo rate of a long (a constituent's spread over the market rate; UNVERIFIED default 0), not to a
    short's special rate. ``funding_share`` of the cash borrowed is funded at
    ``funding``'s rates and the rest at ``other_funding``'s (``carry_table`` sets them from CCIL's TREPS caps).
    ``side="short"``: the bond is sold and borrowed in a reverse repo at ``reverse_repo_rate_pct`` (its special repo
    rate, which only the user can give): coupons and the pull to par are paid away, interest is earned on the cash
    lent and on the sale proceeds kept.
    """
    if not isinstance(bond, (FixedRateBond, DiscountInstrument)):
        raise TypeError("bond must be a bonds.FixedRateBond or DiscountInstrument")
    if repo_compounding not in REPO_COMPOUNDING:
        raise ValueError(f"repo_compounding must be one of {REPO_COMPOUNDING}, not {repo_compounding!r}")
    if projection is not None and projection not in PROJECTIONS:
        raise ValueError(f"projection must be None or one of {PROJECTIONS}, not {projection!r}")
    if side not in SIDES:
        raise ValueError(f"side must be one of {SIDES}, not {side!r}")
    s, e = as_date(start), as_date(end)
    if not e > s:
        raise ValueError(f"end {e} must be after start {s}")
    if e >= bond.maturity:
        raise ValueError(f"the horizon ends {e}, on or after the bond's maturity {bond.maturity}: carry to redemption "
                         "is not built")
    f = float(face)
    if not (math.isfinite(f) and f > 0):
        raise ValueError(f"face must be a positive number of rupees, not {face!r} (a short is side='short')")
    h = float(haircut_pct)
    if not (math.isfinite(h) and 0 <= h < 100):
        raise ValueError(f"haircut_pct must be from 0 to below 100, not {haircut_pct!r}")
    spread = float(spread_bp)
    if not math.isfinite(spread):
        raise ValueError(f"spread_bp must be a finite number, not {spread_bp!r}")
    share = float(funding_share)
    if not (math.isfinite(share) and 0 <= share <= 1):
        raise ValueError(f"funding_share must be from 0 to 1, not {funding_share!r}")
    if share < 1 and other_funding is None:
        raise ValueError(f"a funding_share of {share:g} leaves {1 - share:.1%} of the cash to other_funding: give it")
    if side == "short":
        if reverse_repo_rate_pct is None:
            raise ValueError("a short needs reverse_repo_rate_pct, the special repo rate at which the bond is borrowed "
                             "(security-wise repo rates are CCIL's licensed data: give your own)")
        if share < 1:
            raise ValueError("a short borrows the bond in a reverse repo: it has no repo funding to split")
    cal = calendar if calendar is not None else calendar_for("mumbai_settlement")
    if not cal.is_business_day(s):
        raise ValueError(f"start {s} is not a business day: a purchase settles on one")
    cutoff = None if as_of is None else as_time(as_of, "as_of")
    if cutoff is not None:
        funding = _funding_at(funding, cutoff, "funding")
        if other_funding is not None:
            other_funding = _funding_at(other_funding, cutoff, "other funding")
    clean = float(clean_price)
    if isinstance(bond, DiscountInstrument):
        dirty, accrued0, accrued1, coupons = clean, 0.0, 0.0, 0.0
        y0 = bond.yield_from_price(clean, s)
        clean_end = bond.price(y0, e, rounded=False)
    else:
        a = bond.analytics(s, clean_price=clean)
        dirty, accrued0, y0 = a.dirty_price, a.accrued_interest, a.yield_pct
        accrued1 = bond.accrued_interest(e)
        coupons = sum(fl.amount for fl in bond.cash_flows(s) if fl.kind == "coupon" and fl.date <= e)
        clean_end = bond.clean_price(y0, e)
    sign = 1.0 if side == "long" else -1.0
    income = sign * f / 100 * (coupons + accrued1 - accrued0)
    pull = sign * f / 100 * (clean_end - clean) if pull_to_par else 0.0
    cash, equity = (1 - h / 100) * dirty * f / 100, h / 100 * dirty * f / 100
    daily, notes = _daily_rates(funding, s, e, projection, cal)
    days = (e - s).days
    repo_other = 0.0
    if side == "long":
        repo = _repo_interest(share * cash, daily, repo_compounding, spread) if share > 0 else 0.0
        if share < 1:
            other, other_notes = _daily_rates(other_funding, s, e, projection, cal)
            repo_other = _repo_interest((1 - share) * cash, other, repo_compounding, spread)
            repo += repo_other
            notes.extend(f"other funding: {n}" for n in other_notes)
            notes.append(f"{1 - share:.1%} of the cash borrowed funded at the other funding's rates")
    else:
        rr = float(reverse_repo_rate_pct)
        if not math.isfinite(rr):
            raise ValueError(f"reverse_repo_rate_pct must be a finite number, not {reverse_repo_rate_pct!r}")
        repo = -cash * rr * days / (100 * _YEAR)  # interest earned on the cash lent against the borrowed bond
        notes.append(f"short: the bond borrowed in a reverse repo at {rr:g}% (special repo rate given), the sale "
                     "proceeds lent there less the haircut")
    if equity_rate_pct is None:
        equity_cost = sign * _repo_interest(equity, daily, "simple", 0.0)
    else:
        r_e = float(equity_rate_pct)
        if not math.isfinite(r_e):
            raise ValueError(f"equity_rate_pct must be a finite number, not {equity_rate_pct!r}")
        equity_cost = sign * equity * r_e * days / (100 * _YEAR)
    fixed = floating = swap_carry = 0.0
    projected_fix = 0
    used_dates = [s, e - timedelta(days=1)]
    if swap is not None:
        if not isinstance(swap, OisSwap):
            raise TypeError("swap must be an ois.OisSwap")
        if fixings is None and projected_fixings is None:
            raise ValueError("a swap needs fixings for its floating leg (overnight MIBOR, or a curve's "
                             "overnight_forwards for an expected carry)")
        if swap.effective > s:
            raise ValueError(f"the swap starts {swap.effective}, after the carry starts {s}: it accrues nothing before")
        periods = ois_schedule(swap.effective, swap.months, swap.conventions, cal.lenient())
        if periods[-1].end < e:
            raise ValueError(f"the swap ends {periods[-1].end}, before the horizon ends {e}")
        overlap = [p for p in periods if p.start < e and p.end > s]
        used_dates.extend(p.end for p in overlap)
        fix, projected_fix, fix_notes = _floating_fixings(fixings, projected_fixings, overlap[0].start, e, cal,
                                                         cutoff, fixing_times, projection)
        notes.extend(fix_notes)
        ignored: set[date] = set()
        for p in overlap:  # interest within each calculation period, compounded from the period's start
            lo, hi = max(p.start, s), min(p.end, e)
            upper = compound_overnight(fix, p.start, hi, calendar=cal, missing=missing, conventions=swap.conventions)
            lower = (compound_overnight(fix, p.start, lo, calendar=cal, missing=missing,
                                        conventions=swap.conventions).factor if lo > p.start else 1.0)
            floating += swap.notional * (upper.factor - lower)
            fixed += swap.notional * swap.fixed_rate_pct * (hi - lo).days / (100 * swap.conventions.year_days)
            ignored.update(d for d in upper.ignored if d >= lo)
        swap_carry = floating - fixed if swap.pay_fixed else fixed - floating
        if ignored:
            notes.append(f"fixings on non-business days ignored: {', '.join(d.isoformat() for d in sorted(ignored))}")
    net = income + pull - repo - equity_cost + swap_carry
    quarter_ends = tuple(r["date"] for r in daily if r["quarter_end_day"] or r["fy_end_day"])
    if quarter_ends:
        notes.append(f"the horizon spans quarter- or year-end day(s) {', '.join(d.isoformat() for d in quarter_ends)}")
    if spread and side == "long":
        notes.append(f"repo rates {spread:+g} bp (funding spread)")
    elif spread:
        notes.append(f"funding spread of {spread:+g} bp not applied: a short earns the special repo rate given on the "
                     "cash it lends, and the haircut's return is at the plain funding rates")
    return CarryBreakdown(
        start=s, end=e, days=days, days_30e=days_30e_360(s, e), side=side, face=f, clean_price=clean,
        dirty_price=dirty, haircut_pct=h, cash_borrowed=sign * cash, equity=sign * equity, funding_share=share,
        income=income, pull_to_par=pull, repo=repo, repo_other=repo_other, equity_cost=equity_cost, fixed=fixed,
        floating=floating, swap_carry=swap_carry, net=net, net_bp_annual=net / f * _YEAR / days * 1e4,
        dealing_days=len({r["rate_from"] for r in daily}), projected_days=sum(r["projected"] for r in daily),
        projected_fixings=projected_fix, quarter_end_days=quarter_ends,
        calendar_projected=beyond_calendar(cal, used_dates), notes=tuple(notes),
        daily=pd.DataFrame(daily, columns=list(DAILY_COLUMNS)))


def _segments(daily: list[dict]) -> list[tuple[float, int]]:
    """The days of each overnight deal, as (rate, days): consecutive days with one ``deal_day``."""
    out: list[list] = []
    for row in daily:
        if out and out[-1][2] == row["deal_day"]:
            out[-1][1] += 1
        else:
            out.append([row["rate_pct"], 1, row["deal_day"]])
    return [(rate, n) for rate, n, _ in out]


def treps_class(kind: str, liquidity: str | None) -> str:
    """Where a security stands under CCIL's TREPS collateral rules: ``base`` (a liquid or semi-liquid GoI security,
    which backs its full borrowing limit and the caps' base), ``illiquid`` (an illiquid GoI security: capped),
    ``sdl`` (capped), ``bill`` (T-bills and CMBs, listed by CCIL without a liquidity class: taken as uncapped and
    outside the base, UNVERIFIED) or ``ineligible`` (CP, CD and special securities, which CCIL's list does not
    hold). A GoI security without a class counts as illiquid (the conservative reading)."""
    kind = str(kind).strip().lower()
    if kind in ("gsec", "green"):
        return "base" if liquidity in ("liquid", "semi_liquid") else "illiquid"
    if kind == "sdl":
        return "sdl"
    if kind in ("tbill", "cmb"):
        return "bill"
    return "ineligible"


def treps_shares(book: list[tuple[str, str | None, float]], on) -> tuple[list[float], list[str]]:
    """The share of each position's cash that TREPS lends under CCIL's concentration caps.

    ``book``: (kind, liquidity, haircut-adjusted value) per long position deposited. From 2 Jun 2026 (RMD/SS/26/13,
    effective at the end of 1 Jun 2026) the borrowing limit against illiquid GoI securities and SDLs together is
    capped at ``TREPS_ILLIQUID_CAP`` (50%), and against SDLs at ``TREPS_SDL_CAP`` (30%), of the haircut-adjusted value
    of the liquid and semi-liquid GoI securities deposited, illiquid GoI securities served first. From
    ``TREPS_EARLIER_FROM`` (16 Apr 2021) to then the earlier rule (RMD/SS/21/21) is taken as CCIL's FAQ states it, read
    only through a search summary (UNVERIFIED): both together up to ``TREPS_EARLIER_CAP`` (20%) of that base, illiquid
    first, with no separate SDL cap. Before that no rule is known, and TREPS is taken to lend nothing against them (the
    conservative reading). Each capped position gets its class's pro-rata share (an allocation choice; CCIL caps the
    member's total).
    """
    d = as_date(on)
    classes = [treps_class(k, liq) for k, liq, _ in book]
    values = [float(v) for _, _, v in book]
    notes = []
    if any(c == "ineligible" for c in classes):
        raise ValueError("CP, CD and special securities are not TREPS collateral (CCIL's eligible list holds "
                         "T-bills, CMBs, GoI dated securities and SDLs): fund them at market repo")
    base = sum(v for c, v in zip(classes, values) if c == "base")
    illiquid = sum(v for c, v in zip(classes, values) if c == "illiquid")
    sdl = sum(v for c, v in zip(classes, values) if c == "sdl")
    if d >= TREPS_CAP_FROM:
        combined, sdl_cap, rule = TREPS_ILLIQUID_CAP, TREPS_SDL_CAP, "RMD/SS/26/13"
    elif d >= TREPS_EARLIER_FROM:
        combined, sdl_cap = TREPS_EARLIER_CAP, TREPS_EARLIER_CAP
        rule = (f"RMD/SS/21/21 as CCIL's FAQ summarises it, UNVERIFIED: both together {TREPS_EARLIER_CAP:.0%} of the "
                "base")
    else:
        combined, sdl_cap = 0.0, 0.0
        rule = f"no rule known before {TREPS_EARLIER_FROM}: nothing lent against them, the conservative reading"
    lend_illiquid = min(illiquid, combined * base)
    lend_sdl = min(sdl, sdl_cap * base, combined * base - lend_illiquid)
    share = {"base": 1.0, "bill": 1.0, "illiquid": lend_illiquid / illiquid if illiquid else 1.0,
             "sdl": lend_sdl / sdl if sdl else 1.0}
    if illiquid or sdl:
        notes.append(f"CCIL's TREPS caps ({rule}) on a base of Rs {base / 1e7:,.2f} crore of liquid and "
                     f"semi-liquid G-secs: illiquid G-secs Rs {illiquid / 1e7:,.2f} crore, {share['illiquid']:.1%} "
                     f"lent; SDLs Rs {sdl / 1e7:,.2f} crore, {share['sdl']:.1%} lent")
    return [share[c] for c in classes], notes


def carry_table(bonds: pd.DataFrame, start, end, *, funding: pd.DataFrame, haircuts, face: float = 1e9,
                swap_rate_pct: float | None = None, swap_months: int | None = None, fixings=None,
                excess_funding: pd.DataFrame | None = None, treps_caps: bool = True, **options) -> pd.DataFrame:
    """``carry`` for every bond of ``bonds`` (``CARRY_COLUMNS``), sorted by maturity and ISIN.

    ``bonds``: ``isin`` (each once), ``kind``, ``maturity_date``, ``coupon_pct``, ``clean_price``, optional ``face``
    (rupees; default ``face``), ``liquidity``, ``haircut_pct`` (else looked up in ``haircuts`` on ``start``), ``side``
    (``long`` or ``short``) and, for a short, ``special_repo_pct``. ``swap_rate_pct`` adds an OIS on each bond's
    face, effective ``start``, for ``swap_months``, floating on ``fixings``: paying fixed against a long bond,
    receiving against a short. With TREPS funding (``funding``'s ``source`` is ``triparty``) and ``treps_caps`` the
    long positions are one book deposited with CCIL: its concentration caps (``treps_shares``) decide what TREPS
    lends against each SDL and illiquid G-sec, and the rest is funded at ``excess_funding``'s rates (market repo,
    say); without ``excess_funding`` such a row is refused. CP and CD are not TREPS collateral and are refused there.
    Other keywords go to ``carry``. A bond that cannot be carried keeps the reason in ``error``.
    """
    if not isinstance(bonds, pd.DataFrame):
        raise TypeError("bonds must be a DataFrame")
    if swap_rate_pct is not None and swap_months is None:
        raise ValueError("a swap rate needs swap_months, its tenor")
    if "isin" in bonds:
        names = bonds["isin"].astype(str).str.strip()
        dup = sorted(set(names[names.duplicated()]))
        if dup:
            raise ValueError(f"the bonds list ISIN {dup[0]} more than once: give each position once")
    rules = read_haircuts(haircuts) if haircuts is not None else None
    sources = set(funding["source"]) if isinstance(funding, pd.DataFrame) and "source" in funding else set()
    on_treps = treps_caps and sources == {"triparty"}
    prepared, rows = [], []
    for rec in bonds.to_dict("records"):
        where = f"bond {rec.get('isin')}"
        out = dict.fromkeys(CARRY_COLUMNS, math.nan)
        out.update(isin=rec.get("isin"), kind=rec.get("kind") or "gsec", coupon_pct=rec.get("coupon_pct", math.nan),
                   maturity_date=rec.get("maturity_date"), start=as_date(start), end=as_date(end), notes="", error="",
                   quarter_end_days="", side="long")
        rows.append(out)
        try:
            bond, kind = instrument_from_row(rec, where)
            out.update(kind=kind, maturity_date=bond.maturity)
            side = "long" if _blank(rec.get("side")) else str(rec["side"]).strip().lower()
            if side not in SIDES:
                raise ValueError(f"{where}: side must be one of {SIDES}, not {rec['side']!r}")
            out["side"] = side
            size = face if _blank(rec.get("face")) else _float_or_nan(rec["face"], f"{where}: face")
            if _blank(rec.get("clean_price")):
                raise ValueError(f"{where}: clean_price is missing")
            liquidity = None if _blank(rec.get("liquidity")) else str(rec["liquidity"]).strip().lower().replace(
                "-", "_")
            if _blank(rec.get("haircut_pct")):
                if rules is None:
                    raise ValueError(f"{where}: no haircut_pct and no haircut table")
                cut = haircut(rules, kind=kind, maturity=bond.maturity, on=start, isin=rec.get("isin"),
                              liquidity=liquidity)
            else:
                cut = _float_or_nan(rec["haircut_pct"], f"{where}: haircut_pct")
            special = None if _blank(rec.get("special_repo_pct")) else _float_or_nan(rec["special_repo_pct"],
                                                                                     f"{where}: special_repo_pct")
            clean = _float_or_nan(rec["clean_price"], f"{where}: clean_price")
            out.update(face=size, clean_price=clean, haircut_pct=cut)  # known even if the carry is refused
            if side == "long" and on_treps and treps_class(kind, liquidity) == "ineligible":
                raise ValueError(f"{where}: a {kind} is not TREPS collateral (CCIL's eligible list holds T-bills, "
                                 "CMBs, GoI dated securities and SDLs): fund it at market repo")
            dirty = clean if isinstance(bond, DiscountInstrument) else bond.analytics(as_date(start),
                                                                                      clean_price=clean).dirty_price
            prepared.append({"out": out, "bond": bond, "kind": kind, "liquidity": liquidity, "size": size,
                             "clean": clean, "cut": cut, "side": side, "special": special,
                             "value": (1 - cut / 100) * dirty * size / 100})
        except (ValueError, TypeError) as exc:
            out["error"] = str(exc)
    shares = [1.0] * len(prepared)
    cap_notes: list[str] = []
    longs = [k for k, p in enumerate(prepared) if p["side"] == "long"]
    if on_treps and longs:
        values, cap_notes = treps_shares([(prepared[k]["kind"], prepared[k]["liquidity"], prepared[k]["value"])
                                          for k in longs], start)
        for k, v in zip(longs, values):
            shares[k] = v
    for k, p in enumerate(prepared):
        out = p["out"]
        try:
            swap = None
            if swap_rate_pct is not None:
                swap = OisSwap(effective=start, months=swap_months, fixed_rate_pct=swap_rate_pct, notional=p["size"],
                               pay_fixed=p["side"] == "long")
            notes = []
            if p["side"] == "long" and on_treps and treps_class(p["kind"], p["liquidity"]) in ("illiquid", "sdl"):
                notes = list(cap_notes)
                if p["kind"] in ("gsec", "green") and p["liquidity"] is None:
                    notes.append("no liquidity class given: taken as illiquid for CCIL's TREPS caps")
                if shares[k] < 1 and excess_funding is None:
                    raise ValueError(f"bond {out['isin']}: CCIL's TREPS caps (RMD/SS/26/13) lend {shares[k]:.1%} of "
                                     "its cash: give excess_funding (market repo rates) for the rest")
            c = carry(p["bond"], p["size"], p["clean"], start, end, funding=funding, haircut_pct=p["cut"], swap=swap,
                      fixings=fixings, funding_share=shares[k], other_funding=excess_funding if shares[k] < 1 else None,
                      side=p["side"], reverse_repo_rate_pct=p["special"], **options)
            for name in CARRY_COLUMNS:
                if hasattr(c, name):
                    out[name] = getattr(c, name)
            out.update(quarter_end_days=";".join(d.isoformat() for d in c.quarter_end_days),
                       notes="; ".join([*notes, *c.notes]))
        except (ValueError, TypeError) as exc:
            out["error"] = str(exc)
    table = pd.DataFrame(rows, columns=list(CARRY_COLUMNS))
    order = table.assign(_m=pd.to_datetime(table["maturity_date"], errors="coerce"), _i=table["isin"].astype(str))
    return table.loc[order.sort_values(["_m", "_i"], kind="mergesort").index].reset_index(drop=True)


__all__ = ["CARRY_COLUMNS", "DAILY_COLUMNS", "FUNDING_COLUMNS", "FUNDING_SOURCES", "HAIRCUT_COLUMNS",
           "LIQUIDITY_CLASSES", "PROJECTIONS", "REPO_COMPOUNDING", "SIDES", "TREPS_CAP_FROM", "TREPS_EARLIER_CAP",
           "TREPS_EARLIER_FROM", "TREPS_ILLIQUID_CAP", "TREPS_SDL_CAP", "CarryBreakdown", "carry", "carry_table",
           "fbil_published_at", "fixing_times_from", "fixings_from", "funding_rates", "haircut", "read_haircuts",
           "treps_class", "treps_shares"]
