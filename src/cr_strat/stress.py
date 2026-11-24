"""Stress tests and liquidity: scenarios revalued in full through the pricers, liquidation horizons and costs, and
the funding liquidity of each legal form.

**Scenarios** (R3.1-R3.6 of the risk specification of 24 Nov 2026). A scenario is a set of rows of a table
(``SCENARIO_COLUMNS``; the shipped ``src/cr_strat/data/stress_scenarios.csv``, extended or replaced by scenario id
with a user table), each row a **component** applied with the others: rate shocks to the zero curves
(``all_rate_curves`` moves the G-sec, SDL, OIS and any other reference curve by the same shift, the basis held;
``gsec_curve`` and ``ois_curve`` one of them), spread shocks (``sdl_spread``, ``corp_spread``, ``rating_migration``
through a peer spread matrix or the rating ladder), GoI FRB shocks (``frb_index``, ``frb_dm``), funding shocks
(``repo_rate``, ``haircut``, ``treps_cap``, ``market_repo``) and liquidity shocks (``bid_offer``, ``adv``). Shapes:
``parallel``; ``short`` (``size x e^(-t/x)``, ``x`` the row's ``tenor_years`` or 4, BCBS's decay) and ``long``
(``size x (1 - e^(-t/x))``); ``steepener`` and ``flattener`` (BCBS's rotations ``-0.65 |short| + 0.9 |long|`` and
``+0.8 |short| - 0.6 |long|`` on the INR sizes, times the row's ``multiplier``); ``key_rate`` (a point at
``tenor_years``: a scenario's points on one component joined by the triangles of the risk layer and held flat
beyond the first and last); ``yield_parallel`` (each bond's own yield moved: the classic price-at-the-yield-plus-one
check, exact by construction); ``add`` (bp) and ``scale`` (a multiplier of the bond's z-spread, of a half spread or of
a volume); ``notch`` (``rating_migration``: ``size_bp`` notches down); ``earlier_rule`` (``treps_cap``: CCIL's caps
before 2 Jun 2026); ``off`` (``market_repo``). A ``selector`` (``key=value`` pairs joined by ``;``, alternatives by
``|``: ``rating_bucket=AA+|AA``, ``sector=financials``, ``kind=corporate``, ``tenor_bucket=3-5y``, ``frtb_sector``,
``seniority``, ``issuer``, ``book``, ``isin``) narrows a row to some positions. ``available_from`` (an episode's end)
and ``effective_from`` (when the fund adopted it) keep a scenario out of a run as of an earlier date (rule 10: knowing
2020 in 2019 is look-ahead in the choice of scenarios). Every row has its ``source`` and ``status`` (V verified, P
partly, U UNVERIFIED, ``coarse`` from the free auction table); sizes are in our own words.

**Revaluation** (R3.6, F14). Bonds on their reference curve seen from their settlement, ``P' = sum CF_i D(t_i)
e^(-shift(t_i) t_i) e^(-(z + dz) t_i)``, the shift floored so that the curve's zero rate plus the shift stays at or
above ``floor_pct`` (default 0%, U: the standard lets a supervisor floor at no more than zero); a bond without its
curve at its yield moved by the shift at its Macaulay duration (flagged ``yield_proxy``); a GoI FRB with its projected
coupons and discount rate moved by the rate shift at its next reset plus ``frb_index``, its margin by ``frb_dm``; an
OIS through ``ois.ois_npv`` on the shifted OIS curve, fixings as known; pending trades as the bonds they will be. Each
scenario's P&L is given in total, for each category alone (rates, spread, FRB, funding, liquidity) and as the
interaction (total less the sum alone), per position, book, strategy and fund (``STRESS_POSITION_COLUMNS``,
``STRESS_COLUMNS``). Funding P&L is the extra interest of a repo-rate shock on each long's share of the night's
borrowing (``positions.funded``); liquidity P&L the exit cost at the scenario's half spreads. Under a preset that
cannot borrow (``unregulated_company``) funding scenarios are ``not_applicable``, with the reason, never zero.

**Liquidity** (R4). Days to liquidate ``|F| / (participation x ADV)`` (20% of the day's volume, U), ADV from the marks'
``volume_cr`` over the last 20 business days (a day the marks cover without a traded mark counts as zero volume: U, the
conservative reading), else the user's ADV table, else proxies (``ADV_PROXIES``, U examples to be replaced); the exit
cost ``|F| / 100 x h x DV01`` with ``h`` the half spread of ``costs.cost_rule`` (the engine's fill rule), stressed
``STRESS_SPREAD_MULTIPLIER`` (3, U) times; shares of NAV saleable in 1, 5 and 20 business days, the longest and the
value-weighted days, the illiquid share (CCIL class illiquid or none, SDLs, corporate bonds (kind ``corporate``) below
AA- or unrated, more than 20 days; CP and CD are not illiquid by rating: U) and the liquidity-adjusted VaR (VaR plus the
stressed cost, U). Positions are netted by ISIN before their days are counted (a sale to settle reduces the holding it
sells; one ISIN in two books of a scope is one position), and a scope's net cash counts as saleable at day 0. The ADV
from the marks is averaged over the days they cover (from the file's first traded mark or the bond's issue date),
flagged ``short_adv_window`` below 20; below 5 days the ADV table or a proxy is used where one applies. **Funding
liquidity** (R4.4): for an unregistered company, the settlement cover of purchases still to settle and the share of NAV
in cash, T-bills, CMBs and CP (``liquid_asset_share``, both presets); for an NBFC, the night's borrowing against its
limit, the haircut equity, the funding stresses and the structural liquidity ratios of RBI's ALM Directions (cumulative
negative mismatches at most 10%, 10% and 20% of cumulative outflows in 1-7, 8-14 and 15-30 days: verified notes
NR:S18-S20; securities slotted by their days to liquidate, U).

**Limits.** Tested on synthetic books and the specification's vectors only; the scenario sizes marked U are examples
to be replaced by the fund's own policy, AMFI's stress parameters were not found (search summaries only), RBI's IRRBB
guidelines for banks were not read, and the historical episodes are typed-in moves from search summaries and the free
auction table, not a history of fitted curves. Not built: the mandate-threshold forced-selling scenarios of S9 beyond a
notch downgrade with a multiplier, a liquidity-horizon stress, reverse stress tests, intraday margin calls.
"""

from __future__ import annotations

import difflib
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from cr_strat import costs as cost_rules
from cr_strat import returns as rt
from cr_strat import risk as rk
from cr_strat.floating import GoiFloatingRateBond
from cr_strat.funding import haircut as funding_haircut
from cr_strat.funding import read_haircuts, treps_class, treps_shares
from cr_strat.ois import ois_npv
from cr_strat.rating_scale import LT_GRADES, LT_NOTCH, rating_bucket

DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_SCENARIOS_CSV = DATA_DIR / "stress_scenarios.csv"
SCENARIO_COLUMNS = ("scenario_id", "name", "family", "component", "selector", "shape", "size_bp", "tenor_years",
                    "multiplier", "horizon_days", "floor_pct", "available_from", "effective_from", "source", "status",
                    "notes")
FAMILIES = ("rates_irrbb", "rates", "credit", "frb", "funding", "liquidity", "historical")
RATE_COMPONENTS = ("all_rate_curves", "gsec_curve", "ois_curve")
SPREAD_COMPONENTS = ("sdl_spread", "corp_spread", "rating_migration")
FRB_COMPONENTS = ("frb_index", "frb_dm")
FUNDING_COMPONENTS = ("repo_rate", "haircut", "treps_cap", "market_repo")
LIQUIDITY_COMPONENTS = ("bid_offer", "adv")
COMPONENTS = (*RATE_COMPONENTS, *SPREAD_COMPONENTS, *FRB_COMPONENTS, *FUNDING_COMPONENTS, *LIQUIDITY_COMPONENTS)
CATEGORIES = {"rates": RATE_COMPONENTS, "spread": SPREAD_COMPONENTS, "frb": FRB_COMPONENTS,
              "funding": FUNDING_COMPONENTS, "liquidity": LIQUIDITY_COMPONENTS}
SHAPES = {"all_rate_curves": ("parallel", "short", "long", "steepener", "flattener", "key_rate", "yield_parallel"),
          "gsec_curve": ("parallel", "short", "long", "steepener", "flattener", "key_rate", "yield_parallel"),
          "ois_curve": ("parallel", "short", "long", "steepener", "flattener", "key_rate"),
          "sdl_spread": ("add", "scale"), "corp_spread": ("add", "scale"), "rating_migration": ("notch",),
          "frb_index": ("add",), "frb_dm": ("add",), "repo_rate": ("add",), "haircut": ("add", "scale"),
          "treps_cap": ("earlier_rule",), "market_repo": ("off",), "bid_offer": ("scale", "add"),
          "adv": ("scale",)}
STATUSES = ("V", "P", "U", "coarse")
SELECTOR_KEYS = ("rating_bucket", "sector", "frtb_sector", "kind", "tenor_bucket", "issuer", "group", "book", "isin",
                 "seniority", "ig")
# BCBS, Interest rate risk in the banking book (Apr 2016), Annex 2 Table 1 (INR) and formulas (i)-(iv): verified
IRRBB_INR = {"parallel": 400.0, "short": 500.0, "long": 300.0}
IRRBB_DECAY = 4.0
ROTATIONS = {"steepener": (-0.65, 0.9), "flattener": (0.8, -0.6)}
# FRTB CSR sector buckets (MAR21.51 Table 3) under the names a user's sector column must carry (U mapping)
FRTB_SECTORS = rk.FRTB_SECTORS  # FRTB's credit-spread buckets (MAR21.51 Table 3) by the issuer table's names
CS_RATING_LADDER = {"AAA": 50.0, "AA+": 100.0, "AA": 100.0, "AA-": 150.0, "A+": 200.0, "A": 200.0, "A-": 200.0,
                    "BBB": 300.0, "SUB_IG": 500.0, "D": 500.0, "unrated": 500.0}  # U examples (R3.3)
PARTICIPATION = 0.20  # U: share of a day's volume the fund can sell
STRESS_SPREAD_MULTIPLIER = 3.0  # U
ADV_WINDOW_DAYS = 20  # U
MIN_ADV_DAYS = 5  # U: fewer business days of marks than this: the ADV table or a proxy before the marks' few days
ILLIQUID_DAYS = 20  # U
# U example average daily volumes, rupees (R4.1): to be replaced by the fund's own volumes
ADV_PROXIES = {"gsec_liquid": 1000e7, "gsec_semi_liquid": 100e7, "gsec_illiquid": 10e7, "sdl": 5e7, "tbill": 500e7,
               "cp": 25e7, "cd": 25e7, "frb": 10e7, "strips": 10e7}
CORPORATE_ADV_SHARE = {"AAA": 0.002, "AA": 0.001, "A": 0.0005}  # of the issue's outstanding a day, by rating band (U)
ALM_BUCKETS = ((1, 7, "alm_1_7d"), (8, 14, "alm_8_14d"), (15, 30, "alm_15_30d"))
ALM_LIMITS = {"alm_1_7d": 0.10, "alm_8_14d": 0.10, "alm_15_30d": 0.20}  # ALM Directions (verified notes NR:S18-S19)

STRESS_COLUMNS = ("date", "as_of", "scenario_id", "family", "status", "scope_type", "scope", "component", "pnl",
                  "pnl_pct_nav", "positions", "flags")
STRESS_POSITION_COLUMNS = ("date", "as_of", "scenario_id", "book", "strategy", "isin", "kind", "pnl", "pnl_rates",
                           "pnl_spread", "pnl_frb", "pnl_funding", "pnl_liquidity", "interaction", "method", "flags")
STRESS_WORST_COLUMNS = ("date", "as_of", "scope_type", "scope", "family", "scenario_id", "pnl", "pnl_pct_nav")
ADV_COLUMNS = ("date", "isin", "kind", "liquidity", "adv_cr", "public_from", "source")
LIQUIDITY_COLUMNS = ("date", "as_of", "book", "strategy", "isin", "kind", "face", "value", "adv", "adv_source",
                     "participation", "days_to_liquidate", "half_spread_bp", "liquidation_cost", "stressed_cost",
                     "illiquid", "flags")
LIQUIDITY_SUMMARY_COLUMNS = ("date", "as_of", "scope_type", "scope", "share_1d", "share_5d", "share_20d", "max_days",
                             "mean_days", "illiquid_share", "liquidation_cost", "stressed_cost", "lvar")
FUNDING_LIQUIDITY_COLUMNS = ("date", "as_of", "book", "legal_form", "applicable", "borrowed", "limit", "headroom",
                             "haircut_equity", "margin", "idle_cash", "pending_purchases", "settlement_cover",
                             "scenario_id", "extra_interest", "extra_equity", "treps_lost", "shortfall", "alm_1_7d",
                             "alm_8_14d", "alm_15_30d", "notes", "liquid_assets", "liquid_asset_share")
LIQUID_ASSET_KINDS = ("tbill", "cmb", "cp")  # R4.4: cash, T-bills (and CMBs) and CP, as a share of the book's NAV
_YEAR = 365.0


def _blank(value) -> bool:
    return rk._blank(value)


def _text(value) -> str:
    return rk._text(value)


# ---------------------------------------------------------------- the scenario table (R3.1)


@dataclass(frozen=True)
class ScenarioRow:
    """One component of a scenario, as read and checked."""

    scenario_id: str
    name: str
    family: str
    component: str
    selector: tuple[tuple[str, tuple[str, ...]], ...]
    shape: str
    size_bp: float
    tenor_years: float
    multiplier: float
    horizon_days: int
    floor_pct: float
    available_from: date | None
    effective_from: date | None
    source: str
    status: str
    notes: str
    line: int = 0

    @property
    def category(self) -> str:
        return next(c for c, comps in CATEGORIES.items() if self.component in comps)


def _closest(value: str, allowed: Sequence[str]) -> str:
    close = difflib.get_close_matches(value, allowed, n=1)
    return f" (did you mean {close[0]!r}?)" if close else ""


def _selector(text: str, where: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    out = []
    for part in [p.strip() for p in text.split(";") if p.strip()]:
        key, sep, value = part.partition("=")
        key = key.strip().lower()
        if not sep or not value.strip():
            raise ValueError(f"{where}: selector part {part!r} is not key=value")
        if key not in SELECTOR_KEYS:
            raise ValueError(f"{where}: selector key {key!r} is not one of "
                             f"{SELECTOR_KEYS}{_closest(key, SELECTOR_KEYS)}")
        out.append((key, tuple(v.strip() for v in value.split("|") if v.strip())))
    return tuple(out)


def _rows(frame: pd.DataFrame, origin: str) -> list[ScenarioRow]:
    missing = [c for c in ("scenario_id", "family", "component", "shape") if c not in frame]
    if missing:
        raise ValueError(f"the scenario table {origin} lacks {missing} (columns: {', '.join(SCENARIO_COLUMNS)})")
    rows = []
    for n, rec in rk._lines(frame):
        where = f"scenario table {origin} line {n}"
        sid = _text(rec.get("scenario_id"))
        if not sid:
            raise ValueError(f"{where}: scenario_id is blank")
        family = _text(rec.get("family")).lower()
        if family not in FAMILIES:
            raise ValueError(f"{where}: family {rec.get('family')!r} is not one of {FAMILIES}"
                             f"{_closest(family, FAMILIES)}")
        component = _text(rec.get("component")).lower()
        if component not in COMPONENTS:
            raise ValueError(f"{where}: component {rec.get('component')!r} is not one the code knows"
                             f"{_closest(component, COMPONENTS)}: {', '.join(COMPONENTS)}")
        shape = _text(rec.get("shape")).lower()
        if shape not in SHAPES[component]:
            raise ValueError(f"{where}: shape {rec.get('shape')!r} does not apply to {component} (one of "
                             f"{SHAPES[component]}){_closest(shape, SHAPES[component])}")
        status = _text(rec.get("status"))
        if status not in STATUSES:
            raise ValueError(f"{where}: status {rec.get('status')!r} must be one of {STATUSES}")
        size = rk._num(rec.get("size_bp"), f"{where}: size_bp", blank=math.nan)
        if shape in ("parallel", "short", "long", "key_rate", "yield_parallel", "add") and not math.isfinite(size):
            raise ValueError(f"{where}: a {shape} {component} row needs size_bp")
        tenor = rk._num(rec.get("tenor_years"), f"{where}: tenor_years", blank=math.nan)
        if shape == "key_rate" and not (math.isfinite(tenor) and tenor > 0):
            raise ValueError(f"{where}: a key_rate row needs a positive tenor_years")
        if shape in ("short", "long") and math.isfinite(tenor) and tenor <= 0:
            raise ValueError(f"{where}: the decay tenor_years must be positive")
        multiplier = rk._num(rec.get("multiplier"), f"{where}: multiplier", blank=1.0)
        if shape == "scale" and not multiplier >= 0:
            raise ValueError(f"{where}: a scale row needs a multiplier of 0 or more")
        horizon = rk._num(rec.get("horizon_days"), f"{where}: horizon_days", blank=1.0)
        if horizon < 0 or horizon != int(horizon):
            raise ValueError(f"{where}: horizon_days must be a whole number of days")
        rows.append(ScenarioRow(
            scenario_id=sid, name=_text(rec.get("name")) or sid, family=family, component=component,
            selector=_selector(_text(rec.get("selector")), where), shape=shape, size_bp=size, tenor_years=tenor,
            multiplier=multiplier, horizon_days=int(horizon),
            floor_pct=rk._num(rec.get("floor_pct"), f"{where}: floor_pct", blank=0.0),
            available_from=rk._opt_date(rec.get("available_from"), f"{where}: available_from"),
            effective_from=rk._opt_date(rec.get("effective_from"), f"{where}: effective_from"),
            source=_text(rec.get("source")), status=status, notes=_text(rec.get("notes")), line=n))
    families = {}
    for r in rows:
        if families.setdefault(r.scenario_id, r.family) != r.family:
            raise ValueError(f"scenario table {origin} line {r.line}: scenario {r.scenario_id} has rows of two "
                             "families")
    return rows


def read_scenarios(source=DEFAULT_SCENARIOS_CSV, *, as_of=None, extend=None) -> list[ScenarioRow]:
    """The scenario rows (``SCENARIO_COLUMNS``) of ``source`` (default the shipped table), with ``extend`` (a user
    table) replacing the shipped rows of each scenario id it names and adding the others; with ``as_of``, only the
    scenarios available (``available_from``) and effective (``effective_from``) by that date. Unknown families,
    components, shapes, selector keys and statuses are refused, naming the line and the closest value known."""
    base = _rows(rk._frame(source, "the scenario table"), str(source) if not isinstance(source, pd.DataFrame)
                 else "(frame)")
    if extend is not None:
        user = _rows(rk._frame(extend, "the user's scenario table"), str(extend) if not isinstance(extend,
                                                                                                  pd.DataFrame)
                     else "(user frame)")
        ids = {r.scenario_id for r in user}
        base = [r for r in base if r.scenario_id not in ids] + user
    if as_of is not None:
        day = rk.as_time(as_of, "as_of").date()
        keep = {}
        for r in base:
            ok = (r.available_from is None or r.available_from <= day) and (r.effective_from is None
                                                                             or r.effective_from <= day)
            keep[r.scenario_id] = keep.get(r.scenario_id, True) and ok
        base = [r for r in base if keep[r.scenario_id]]
    return sorted(base, key=lambda r: (r.scenario_id, r.line))


def scenarios_by_id(rows: Iterable[ScenarioRow]) -> dict[str, list[ScenarioRow]]:
    out: dict[str, list[ScenarioRow]] = {}
    for r in rows:
        out.setdefault(r.scenario_id, []).append(r)
    return dict(sorted(out.items()))


# ---------------------------------------------------------------- shapes (R3.2, F14)


def irrbb_shift(shape: str, t, *, sizes: Mapping[str, float] = IRRBB_INR, decay: float = IRRBB_DECAY,
                sign: float = 1.0) -> np.ndarray:
    """A BCBS IRRBB shock in bp at times ``t`` (years, F14): ``parallel`` ``R_par``; ``short`` ``R_short S(t)`` with
    ``S(t) = e^(-t/decay)``; ``long`` ``R_long (1 - S(t))``; ``steepener`` ``-0.65 |short| + 0.9 |long|``;
    ``flattener`` ``+0.8 |short| - 0.6 |long|``; ``sign`` -1 for the down shocks (parallel, short, long)."""
    arr = np.asarray(t, dtype=float)
    s = np.exp(-arr / decay)
    short = sizes["short"] * s
    long = sizes["long"] * (1 - s)
    if shape == "parallel":
        return sign * np.full_like(arr, sizes["parallel"])
    if shape == "short":
        return sign * short
    if shape == "long":
        return sign * long
    if shape in ROTATIONS:
        a, b = ROTATIONS[shape]
        return a * np.abs(short) + b * np.abs(long)
    raise ValueError(f"IRRBB shape must be parallel, short, long, steepener or flattener, not {shape!r}")


def key_rate_shift(points: Mapping[float, float], t) -> np.ndarray:
    """A shift in bp at times ``t`` from key-rate points ``{tenor: bp}``: the triangles of the given tenors, held flat
    beyond the first and last (R3.5's rule for points without data)."""
    return rk.key_rate_shift(points, t)


def _rate_shift_bp(rows: Sequence[ScenarioRow], t: np.ndarray) -> np.ndarray:
    """The rate shift (bp) at times ``t`` of a scenario's rate rows of one curve: the shapes summed, the key-rate
    points of each selector joined first."""
    out = np.zeros_like(np.asarray(t, dtype=float))
    points: dict[tuple, dict[float, float]] = {}
    for r in rows:
        if r.shape == "key_rate":
            group = points.setdefault((r.component, r.selector), {})
            group[r.tenor_years] = group.get(r.tenor_years, 0.0) + r.size_bp * r.multiplier
        elif r.shape == "parallel":
            out = out + r.size_bp * r.multiplier
        elif r.shape in ("short", "long"):
            decay = r.tenor_years if math.isfinite(r.tenor_years) else IRRBB_DECAY
            sizes = {"parallel": 0.0, "short": abs(r.size_bp), "long": abs(r.size_bp)}
            out = out + math.copysign(1.0, r.size_bp) * r.multiplier * irrbb_shift(r.shape, t, sizes=sizes,
                                                                                   decay=decay)
        elif r.shape in ROTATIONS:
            decay = r.tenor_years if math.isfinite(r.tenor_years) else IRRBB_DECAY
            out = out + r.multiplier * irrbb_shift(r.shape, t, decay=decay)
    for group in points.values():
        out = out + key_rate_shift(group, t)
    return out


def scenario_shift(rows: Sequence[ScenarioRow], curve_name: str, curve=None):
    """``shift(t)`` (decimal) of a scenario's rate rows that move ``curve_name`` (``all_rate_curves``, and
    ``gsec_curve`` or ``ois_curve`` for that curve), floored when ``curve`` is given so that its zero rate plus the
    shift stays at or above the rows' ``floor_pct`` (the lowest floor of the rows; F14)."""
    own = [r for r in rows if r.component == "all_rate_curves" or (r.component == "gsec_curve" and curve_name
                                                                   in ("gsec",))
           or (r.component == "ois_curve" and curve_name == "ois")]
    own = [r for r in own if r.shape != "yield_parallel"]
    floor = min((r.floor_pct for r in own), default=0.0)

    def shift(t):
        arr = np.asarray(t, dtype=float)
        bp = _rate_shift_bp(own, arr.reshape(-1)).reshape(arr.shape)
        out = bp / 1e4
        if curve is not None and own:
            safe = np.where(arr > 0, arr, 1e-6)
            z = np.asarray(curve.zero_rate(safe.reshape(-1), "continuous"), dtype=float).reshape(arr.shape) / 100
            out = np.maximum(out, floor / 100 - z)
        return out
    return shift


# ---------------------------------------------------------------- selectors and position attributes


def _attributes(item) -> dict[str, str]:
    if isinstance(item, rk.RiskSwap):
        return {"kind": "ois", "book": item.book, "isin": item.isin, "tenor_bucket": "", "rating_bucket": "",
                "sector": "", "frtb_sector": "", "issuer": "", "group": "", "seniority": "", "ig": ""}
    bucket = item.rating_bucket or ("sovereign" if item.rating_source == "sovereign" else "unrated")
    sector = item.sector or ""
    # FRTB's bucket: the issuer table's frtb_sector, else the sector when it is one of FRTB's names or a fund name
    # read as one (a public sector bank, a bank, an NBFC, an HFC, a public financial institution: financials, U)
    frtb = item.frtb_sector or (sector if sector in FRTB_SECTORS else rk.SECTOR_FRTB.get(sector, "other"))
    if item.seniority == "covered":
        frtb = "covered"
    return {"kind": item.kind, "book": item.book, "isin": item.isin, "tenor_bucket": item.tenor_bucket,
            "rating_bucket": bucket, "sector": sector, "frtb_sector": frtb, "issuer": item.issuer_id,
            "group": item.group_id, "seniority": item.seniority,
            "ig": "yes" if bucket in rk.IG_BUCKETS else ("no" if bucket in ("SUB_IG", "D", "unrated") else "")}


def selects(row: ScenarioRow, item) -> bool:
    """Whether a row's selector takes in ``item`` (every key=value pair must hold; alternatives by ``|``)."""
    if not row.selector:
        return True
    attrs = _attributes(item)
    return all(attrs.get(key, "") in values for key, values in row.selector)


# ---------------------------------------------------------------- applying a scenario (R3.6)


@dataclass(frozen=True)
class StressContext:
    """What a scenario needs besides the book: the risk context, the cost table, the participation rate, the stressed
    spread multiplier and the ADV of each position (``adv_of``), and the haircut table for funding stresses."""

    risk: rk.RiskContext
    participation: float = PARTICIPATION
    multiplier: float = STRESS_SPREAD_MULTIPLIER

    @property
    def costs(self):
        c = self.risk.settings.costs
        return c if isinstance(c, cost_rules.CostTable) else cost_rules._as_table(
            cost_rules.DEFAULT_COSTS_CSV if c is None else c)


def _migration_bp(row: ScenarioRow, item, matrix: pd.DataFrame | None) -> float:
    """A downgrade's spread change (bp): ``size_bp`` notches down (default 1) through the user's peer matrix
    (``credit_spreads.read_spread_matrix``: the matrix spread of the lower bucket less the own bucket's at the bond's
    tenor), else the rating ladder's step (``CS_RATING_LADDER``: buckets the ladder treats alike give no step), times
    the row's ``multiplier`` (forced selling, U)."""
    bucket = item.rating_bucket
    worst = {"BBB": LT_NOTCH["BBB-"], "SUB_IG": LT_NOTCH["C-"]}  # a bucket's worst notch: BBB- and C-
    if bucket is None or (bucket not in LT_NOTCH and bucket not in worst) or bucket == "D":
        return 0.0
    notch = worst.get(bucket, LT_NOTCH.get(bucket))
    steps = int(row.size_bp) if math.isfinite(row.size_bp) and row.size_bp >= 1 else 1
    lower = rating_bucket(min(notch + steps, len(LT_GRADES)))
    if matrix is not None and "rating_bucket" in matrix:
        from cr_strat.credit_spreads import matrix_spread_bp
        years = max((item.maturity - item.settlement).days / _YEAR, 0.0) if item.maturity else 0.0
        own = matrix_spread_bp(matrix, tenor_years=years, rating_bucket=bucket)
        low = matrix_spread_bp(matrix, tenor_years=years, rating_bucket=lower)
        if math.isfinite(own) and math.isfinite(low):
            return (low - own) * row.multiplier
    return (CS_RATING_LADDER.get(lower, 500.0) - CS_RATING_LADDER.get(bucket, 500.0)) * row.multiplier


def _ladder_no_step(rows: Sequence[ScenarioRow], item, matrix) -> bool:
    """Whether a downgrade row selecting ``item`` read through the ladder (no peer matrix for its tenor) gives no
    spread step: the ladder treats AA+ and AA, and A+, A and A-, alike (give a peer matrix: flagged ladder_no_step)."""
    if item.kind not in rk.CREDIT_KINDS or item.rating_bucket in (None, "D") or not (
            item.rating_bucket in LT_NOTCH or item.rating_bucket in ("BBB", "SUB_IG")):
        return False
    for r in rows:
        if r.component != "rating_migration" or not selects(r, item):
            continue
        if _migration_bp(r, item, None) == 0 and (matrix is None or _migration_bp(r, item, matrix) == 0):
            return True
    return False


def _spread_bp(rows: Sequence[ScenarioRow], item, matrix) -> float:
    """The z-spread change (bp) of a bond under a scenario's spread rows that select it."""
    total = 0.0
    for r in rows:
        if not selects(r, item):
            continue
        if r.component == "corp_spread" and item.kind in rk.CREDIT_KINDS:
            total += r.size_bp * r.multiplier if r.shape == "add" else (r.multiplier - 1) * item.z_spread * 1e4
        elif r.component == "sdl_spread" and item.kind == "sdl":
            total += r.size_bp * r.multiplier if r.shape == "add" else (r.multiplier - 1) * item.z_spread * 1e4
        elif r.component == "rating_migration" and item.kind in rk.CREDIT_KINDS:
            total += _migration_bp(r, item, matrix)
    return total if math.isfinite(total) else 0.0


def _bond_pnl(item: rk.RiskPosition, rate_rows: Sequence[ScenarioRow], spread_bp: float, frb_rows,
              settings: rk.RiskSettings) -> tuple[float, str]:
    """Full revaluation of one bond position under rate rows, a spread change and FRB rows: (P&L, method)."""
    bond, units = item.instrument.bond, item.units
    yield_rows = [r for r in rate_rows if r.shape == "yield_parallel"]
    if isinstance(bond, GoiFloatingRateBond):
        t_reset = max((item.next_reset - item.settlement).days / _YEAR, 0.0)
        gsec_rows = [r for r in rate_rows if r.component in ("all_rate_curves", "gsec_curve")
                     and r.shape != "yield_parallel"]
        h = float(_rate_shift_bp(gsec_rows, np.array([t_reset]))[0]) if gsec_rows else 0.0
        h += sum(r.size_bp * r.multiplier for r in frb_rows if r.component == "frb_index")
        # its own yield moved (yield_parallel, a rate scenario): the index moves, coupons and discount rate with it,
        # as a rate shock moves a floater; its margin moves only under frb_dm (U)
        h += sum(r.size_bp * r.multiplier for r in yield_rows)
        dm = sum(r.size_bp * r.multiplier for r in frb_rows if r.component == "frb_dm")
        if not (h or dm):
            return 0.0, "frb_full"
        base = rk._frb_dirty(bond, item.settlement, item.yield_pct, 0.0, 0.0)
        return units * (rk._frb_dirty(bond, item.settlement, item.yield_pct, h, dm) - base), "frb_full"
    if yield_rows or item.curve is None:
        if yield_rows:
            dy_bp = sum(r.size_bp * r.multiplier for r in yield_rows) + spread_bp
            method = "yield"
        else:
            name = next((sn.curve for sn in item.sens if sn.method == "yield_proxy"), "gsec")
            rows = [r for r in rate_rows if r.component == "all_rate_curves" or (r.component == "gsec_curve"
                    and name == "gsec") or (r.component == "ois_curve" and name == "ois")]
            dy_bp = float(_rate_shift_bp(rows, np.array([item.macaulay]))[0]) + spread_bp
            method = "yield_proxy"
        if not dy_bp:
            return 0.0, method
        base = rk._dirty(bond, item.yield_pct, item.settlement)
        return units * (rk._dirty(bond, item.yield_pct + dy_bp / 100, item.settlement) - base), method
    t, cf, disc, z = item.times, item.amounts, item.discount, item.z_spread
    shift = scenario_shift(rate_rows, item.reference, item.curve)(t)
    base = float(cf @ (disc * np.exp(-z * t)))
    value = float(cf @ (disc * np.exp(-shift * t) * np.exp(-(z + spread_bp * 1e-4) * t)))
    return units * (value - base), "full"


def _swap_pnl(item: rk.RiskSwap, rate_rows: Sequence[ScenarioRow]) -> float:
    rows = [r for r in rate_rows if r.component in ("all_rate_curves", "ois_curve")]
    if not rows:
        return 0.0
    yield_rows = [r for r in rows if r.shape == "yield_parallel"]
    rest = [r for r in rows if r.shape != "yield_parallel"]
    plain = rk._plain(item.curve)
    base_shift = scenario_shift(rest, "ois", plain) if rest else (lambda t: np.zeros_like(np.asarray(t, float)))
    extra = sum(r.size_bp * r.multiplier for r in yield_rows) / 1e4  # a swap's "own yield": a parallel move (U)

    def shift(t):
        return np.asarray(base_shift(t)) + extra
    return item.npv(rt.ShiftedCurve(plain, shift)) - item.npv(item.curve)


def _funding_pnl(item, rows: Sequence[ScenarioRow], borrows: bool) -> float:
    if not borrows or isinstance(item, rk.RiskSwap):
        return 0.0
    funded = item.funded if math.isfinite(item.funded) else 0.0
    total = 0.0
    for r in rows:
        if r.component == "repo_rate" and selects(r, item):
            total -= funded * r.size_bp * r.multiplier / 1e4 * max(r.horizon_days, 1) / _YEAR
    return total


def _liquidity_pnl(item, rows: Sequence[ScenarioRow], sctx: StressContext, base_cost: float) -> float:
    """The exit cost at the scenario's half spreads (each selected ``bid_offer`` row scales or adds to the half
    spread), as a loss; 0 when the scenario has no bid-offer row."""
    spread_rows = [r for r in rows if r.component == "bid_offer"]
    if not spread_rows or not math.isfinite(base_cost):
        return 0.0
    factor, add_bp = 1.0, 0.0
    for r in spread_rows:
        if selects(r, item):
            if r.shape == "scale":
                factor *= r.multiplier
            else:
                add_bp += r.size_bp * r.multiplier
    h, cost = _exit_parameters(item, sctx)
    if not math.isfinite(h) or not h:
        return -base_cost * factor
    return -(base_cost * factor + cost / h * add_bp)


def apply_scenario(book: rk.RiskBook, rows: Sequence[ScenarioRow], sctx: StressContext) -> list[dict]:
    """``STRESS_POSITION_COLUMNS`` rows of one scenario: every position and swap revalued in full with all its rows
    (``pnl``), each category alone (``pnl_rates``, ``pnl_spread``, ``pnl_frb``, ``pnl_funding``, ``pnl_liquidity``)
    and the interaction (``pnl`` less their sum)."""
    if not rows:
        return []
    sid = rows[0].scenario_id
    ctx = sctx.risk
    borrows = ctx.settings.access.repo or ctx.settings.access.treps
    by_cat = {c: [r for r in rows if r.component in comps] for c, comps in CATEGORIES.items()}
    matrix = None
    if ctx.spread_matrix is not None:
        from cr_strat.credit_spreads import read_spread_matrix
        matrix = read_spread_matrix(ctx.spread_matrix)
    out = []
    for item in book.items:
        flags = list(item.flags)
        rate_rows = [r for r in by_cat["rates"] if selects(r, item)]
        frb_rows = [r for r in by_cat["frb"] if selects(r, item)]
        if isinstance(item, rk.RiskSwap):
            p_rates = _swap_pnl(item, rate_rows)
            p_spread = p_frb = 0.0
            p_total_market = p_rates
            method = "full"
        else:
            spread = _spread_bp(by_cat["spread"], item, matrix)
            if _ladder_no_step(by_cat["spread"], item, matrix):
                flags.append("ladder_no_step")
            p_rates, method = _bond_pnl(item, rate_rows, 0.0, [], ctx.risk)
            p_spread = _bond_pnl(item, [], spread, [], ctx.risk)[0] if spread else 0.0
            p_frb = _bond_pnl(item, [], 0.0, frb_rows, ctx.risk)[0] if frb_rows else 0.0
            p_total_market = _bond_pnl(item, rate_rows, spread, frb_rows, ctx.risk)[0] \
                if (rate_rows or spread or frb_rows) else 0.0
            if method in ("yield_proxy",) and rate_rows:
                flags.append("yield_proxy")
        p_funding = _funding_pnl(item, by_cat["funding"], borrows)
        base_cost = _exit_cost(item, sctx)
        p_liq = _liquidity_pnl(item, by_cat["liquidity"], sctx, base_cost)
        total = p_total_market + p_funding + p_liq
        alone = p_rates + p_spread + p_frb + p_funding + p_liq
        if by_cat["funding"] and not borrows:
            flags.append(f"funding not_applicable: {ctx.settings.access.name} does not borrow")
        out.append({"date": book.date, "as_of": rk._iso(book.as_of), "scenario_id": sid, "book": item.book,
                    "strategy": item.strategy, "isin": item.isin,
                    "kind": "ois" if isinstance(item, rk.RiskSwap) else item.kind, "pnl": total, "pnl_rates": p_rates,
                    "pnl_spread": p_spread, "pnl_frb": p_frb, "pnl_funding": p_funding, "pnl_liquidity": p_liq,
                    "interaction": total - alone, "method": method, "flags": rk._flags(flags)})
    return out


def run_stress(book: rk.RiskBook, scenarios: Sequence[ScenarioRow], sctx: StressContext
               ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Every scenario on the book: ``(STRESS_COLUMNS, STRESS_POSITION_COLUMNS)``. Per scope (fund, each book, each
    strategy) and scenario the total, each category and the interaction; a funding scenario under a preset that
    cannot borrow is ``not_applicable`` (NaN, with the reason)."""
    ctx = sctx.risk
    borrows = ctx.settings.access.repo or ctx.settings.access.treps
    pos_rows, rows = [], []
    for sid, srows in scenarios_by_id(scenarios).items():
        prow = apply_scenario(book, srows, sctx)
        pos_rows.extend(prow)
        family, status = srows[0].family, rk._flags(sorted({r.status for r in srows}))
        funding_only = all(r.component in FUNDING_COMPONENTS for r in srows)
        for scope_type, scope in book.scopes():
            mine = [r for r in prow if (scope_type == "fund" or (scope_type == "book" and r["book"] == scope)
                                        or (scope_type == "strategy" and r["strategy"] == scope))]
            nav = book.nav_of(scope_type, scope)
            flags = []
            if funding_only and not borrows:
                flags.append(f"not_applicable: {ctx.settings.access.name} does not borrow (no repo or TREPS; RBI "
                             "counts any outside liability as public funds: a question for counsel)")
            parts = {"total": "pnl", "rates": "pnl_rates", "spread": "pnl_spread", "frb": "pnl_frb",
                     "funding": "pnl_funding", "liquidity": "pnl_liquidity", "interaction": "interaction"}
            for component, column in parts.items():
                if component not in ("total", "interaction") and not any(r.component in CATEGORIES[component]
                                                                          for r in srows):
                    continue
                value = rk._fsum(r[column] for r in mine)
                mark = list(flags)
                if component in ("total", "spread") and any("ladder_no_step" in r["flags"].split(";") for r in mine):
                    mark.append("ladder_no_step: a downgrade the rating ladder gives no step (give a peer matrix)")
                if (funding_only or component == "funding") and not borrows:
                    value = math.nan
                    if component == "funding" and not funding_only:
                        mark.append(f"not_applicable: {ctx.settings.access.name} does not borrow")
                rows.append({"date": book.date, "as_of": rk._iso(book.as_of), "scenario_id": sid, "family": family,
                             "status": status, "scope_type": scope_type, "scope": scope, "component": component,
                             "pnl": value, "pnl_pct_nav": rk._pct(value, nav), "positions": len(mine),
                             "flags": rk._flags(mark)})
    stress = pd.DataFrame(rows, columns=list(STRESS_COLUMNS))
    positions = pd.DataFrame(pos_rows, columns=list(STRESS_POSITION_COLUMNS))
    positions = positions.sort_values(["scenario_id", "book", "isin"], kind="mergesort").reset_index(drop=True)
    return stress, positions


def stress_worst(stress: pd.DataFrame) -> pd.DataFrame:
    """The worst scenario of each family per scope (``STRESS_WORST_COLUMNS``: the lowest total P&L; ties to the
    first scenario id), the figure a family's stress limit reads."""
    total = stress[(stress["component"] == "total") & stress["pnl"].notna()]
    rows = []
    for (scope_type, scope, family), group in total.groupby(["scope_type", "scope", "family"], sort=True):
        g = group.sort_values(["pnl", "scenario_id"], kind="mergesort")
        r = g.iloc[0]
        rows.append({"date": r["date"], "as_of": r["as_of"], "scope_type": scope_type, "scope": scope,
                     "family": family, "scenario_id": r["scenario_id"], "pnl": r["pnl"],
                     "pnl_pct_nav": r["pnl_pct_nav"]})
    return pd.DataFrame(rows, columns=list(STRESS_WORST_COLUMNS))


# ---------------------------------------------------------------- liquidity (R4)


def read_adv(table) -> pd.DataFrame:
    """The user's average daily volumes (``ADV_COLUMNS``): rows by ``isin`` or by ``kind`` (and CCIL ``liquidity``),
    ``adv_cr`` crore a day, with their ``date`` and ``public_from``; bad cells refused naming the line."""
    frame = rk._frame(table, "the ADV table")
    rk._check_columns(frame, ("date", "adv_cr"), ADV_COLUMNS, "the ADV table")
    rows = []
    for n, rec in rk._lines(frame):
        where = f"ADV table line {n}"
        isin, kind = _text(rec.get("isin")).upper(), _text(rec.get("kind")).lower()
        if not isin and not kind:
            raise ValueError(f"{where}: give an isin or a kind")
        adv = rk._num(rec.get("adv_cr"), f"{where}: adv_cr")
        if adv < 0:
            raise ValueError(f"{where}: adv_cr must be zero or more")
        rows.append({"date": rk._date(rec.get("date"), f"{where}: date"), "isin": isin, "kind": kind,
                     "liquidity": _text(rec.get("liquidity")).lower().replace("-", "_"), "adv_cr": adv,
                     "public_from": rk.public_time(rec.get("public_from"), f"{where}: public_from"),
                     "source": _text(rec.get("source"))})
    return pd.DataFrame(rows, columns=list(ADV_COLUMNS))


def _marks_adv(ctx: rk.RiskContext, d: date) -> tuple[dict[str, float], date | None]:
    """``(traded volume by ISIN in rupees over the last ADV_WINDOW_DAYS business days to d, the file's first day)``:
    the marks' ``volume_cr`` of traded rows public by ``as_of``, and the first day any traded row of the file covers
    (a day before it is no data, never zero volume)."""
    marks = ctx.market.marks
    if marks is None or not len(marks):
        return {}, None
    frame = rk._frame(marks, "the marks")
    if "volume_cr" not in frame:
        return {}, None
    cal = ctx.calendar.lenient()
    start = cal.add_business_days(d, -(ADV_WINDOW_DAYS - 1))
    default = ctx.default_time
    sums: dict[str, float] = {}
    first = None
    for _, rec in rk._lines(frame):
        source = _text(rec.get("source")).lower() or "traded"
        if source != "traded" or _blank(rec.get("volume_cr")):
            continue
        day = rk._date(rec.get("date"), "the marks' date")
        if day > d:
            continue
        public = rk.public_time(rec.get("public_from"), "the marks' public_from") if not _blank(
            rec.get("public_from")) else (datetime.combine(day, default) if default is not None else None)
        if public is None or public > ctx.as_of:
            continue
        first = day if first is None or day < first else first
        if day < start:
            continue
        isin = _text(rec.get("isin")).upper()
        sums[isin] = sums.get(isin, 0.0) + rk._num(rec.get("volume_cr"), "the marks' volume_cr") * 1e7
    return sums, first


def _covered_days(cal, d: date, first: date | None, issue: date | None) -> int:
    """Business days of the ADV window to ``d`` that the marks cover: from the latest of the window's start, the
    file's first traded day and the bond's issue date (at most ADV_WINDOW_DAYS)."""
    start = cal.add_business_days(d, -(ADV_WINDOW_DAYS - 1))
    for floor in (first, issue):
        if floor is not None and floor > start:
            start = floor
    return min(len(cal.business_days(start, d)), ADV_WINDOW_DAYS) if start <= d else 0


def _stock_on(ctx: rk.RiskContext, isin: str, d: date) -> float | None:
    table = ctx.market.stock
    if table is None:
        return None
    frame = rk._frame(table, "the stock table")
    rows = frame[frame["isin"].map(_text).str.upper() == isin] if "isin" in frame else frame.iloc[0:0]
    best = None
    for rec in rows.to_dict("records"):
        day = rk._date(rec.get("date"), "the stock date")
        if day <= d and (best is None or day > best[0]):
            best = (day, rk._num(rec.get("outstanding_cr"), "outstanding_cr") * 1e7)
    return None if best is None else best[1]


def adv_of(book: rk.RiskBook, ctx: rk.RiskContext) -> dict[tuple, tuple[float, str, tuple[str, ...]]]:
    """Per position (by ``key``): ``(ADV in rupees a day, source, flags)``: the marks' volumes, else the user's ADV
    table (the ISIN's latest row public by ``as_of``, else its kind's and CCIL class's), else a proxy
    (``ADV_PROXIES`` by kind and class; corporate bonds ``CORPORATE_ADV_SHARE`` of the issue's outstanding by rating,
    from ``MarketData.stock``), flagged ``proxy_adv``. NaN where nothing applies (``no_adv``). Swaps have none."""
    volumes, first = _marks_adv(ctx, book.date)
    cal = ctx.calendar.lenient()
    table = read_adv(ctx.adv) if ctx.adv is not None else None
    if table is not None:
        table = table[[(r["date"] <= book.date) and (r["public_from"] is None or r["public_from"] <= ctx.as_of)
                       for r in table.to_dict("records")]]
    out = {}
    for item in book.positions:
        flags: list[str] = []
        short = None
        if volumes.get(item.isin):
            covered = _covered_days(cal, book.date, first, item.instrument.issue_date)
            adv_marks = volumes[item.isin] / covered if covered else math.nan
            if covered >= MIN_ADV_DAYS:
                out[item.key] = (adv_marks, "marks", () if covered == ADV_WINDOW_DAYS else
                                 (f"short_adv_window:{covered}d",))
                continue
            # fewer marked days than MIN_ADV_DAYS: the table or a proxy if one applies, else these few days
            short = (adv_marks, "marks", (f"short_adv_window:{covered}d",))
            flags.append(f"short_adv_window:{covered}d")
        if table is not None and len(table):
            rows = table[table["isin"] == item.isin]
            if not len(rows):
                rows = table[(table["isin"] == "") & (table["kind"] == item.kind)
                             & ((table["liquidity"] == "") | (table["liquidity"] == (item.liquidity or "")))]
            if len(rows):
                row = rows.sort_values(["date", "liquidity"], kind="mergesort").iloc[-1]
                out[item.key] = (float(row["adv_cr"]) * 1e7, "table", tuple(flags))
                continue
        adv, source = math.nan, "proxy_class"
        kind = item.kind
        if kind in ("gsec", "green", "special"):
            adv = ADV_PROXIES[f"gsec_{item.liquidity}"] if item.liquidity in ("liquid", "semi_liquid", "illiquid") \
                else ADV_PROXIES["gsec_illiquid"]
            if item.liquidity not in ("liquid", "semi_liquid", "illiquid"):
                flags.append("no_class_taken_illiquid")
        elif kind in ("tbill", "cmb"):
            adv = ADV_PROXIES["tbill"]
        elif kind in ("sdl", "cp", "cd", "frb", "strips"):
            adv = ADV_PROXIES[kind]
        elif kind == "corporate":
            stock = _stock_on(ctx, item.isin, book.date)
            band = "AAA" if item.rating_bucket == "AAA" else ("AA" if item.rating_bucket in ("AA+", "AA", "AA-")
                                                              else "A")
            if stock is None:
                flags.append("no_stock")
            else:
                adv, source = stock * CORPORATE_ADV_SHARE[band], "proxy_issue"
        if not math.isfinite(adv) and short is not None:
            out[item.key] = short
            continue
        if not math.isfinite(adv):
            flags.append("no_adv")
        else:
            flags.append("proxy_adv")
        out[item.key] = (adv, source, tuple(flags))
    return out


def _venue(kind: str) -> str:
    return "otc" if kind in ("cp", "cd", "corporate") else "ndsom"


def _exit_parameters(item, sctx: StressContext) -> tuple[float, float]:
    """(half spread bp, exit cost at that half spread) of a position or swap (``costs.cost_rule``, the engine's
    fill rule): ``|F| / 100 x h x DV01 per 100`` for a bond at its own yield, ``h x |PV01|`` for an OIS."""
    ctx = sctx.risk
    d = ctx.as_of.date()
    if isinstance(item, rk.RiskSwap):
        years = max((item.end - d).days / 365.25, 0.0) if item.end else None
        rule = cost_rules.cost_rule(sctx.costs, "half_spread_bp", kind="ois", on=d, years=years)
        val = ois_npv(rk._plain(item.curve), item.swap, fixings=item.fixings, calendar=item.calendar)
        return rule.value, rule.value * abs(val.pv01)
    years = max((item.maturity - d).days / 365.25, 0.0) if item.maturity else None
    rule = cost_rules.cost_rule(sctx.costs, "half_spread_bp", kind=item.kind, on=d, venue=_venue(item.kind),
                                liquidity=item.liquidity, years=years, isin=item.isin,
                                rating_bucket=item.rating_bucket if item.kind == "corporate" else None)
    bond = item.instrument.bond
    dv01 = bond.dv01(item.yield_pct, item.settlement)
    return rule.value, abs(item.face) / 100 * rule.value * dv01


def _exit_cost(item, sctx: StressContext) -> float:
    try:
        return _exit_parameters(item, sctx)[1]
    except ValueError:
        return math.nan


def _static_illiquid(item) -> bool:
    """Illiquid by kind, CCIL class or rating, whatever its size: a G-sec of CCIL's illiquid class or none, an SDL, a
    corporate bond (kind ``corporate``) rated below AA- or unrated (CP and CD are not counted by rating; U)."""
    return (item.kind in ("gsec", "green", "special") and item.liquidity not in ("liquid", "semi_liquid")) \
        or item.kind == "sdl" or (item.kind == "corporate" and (item.rating_bucket or "unrated")
                                  not in ("AAA", "AA+", "AA", "AA-"))


def _days(face: float, participation: float, adv: float) -> float:
    if not face:
        return 0.0
    return abs(face) / (participation * adv) if math.isfinite(adv) and adv > 0 else math.inf


def scope_liquidity(rows: Iterable[Mapping], *, cash: float = 0.0, days_limit: float = ILLIQUID_DAYS) -> dict:
    """A scope's liquidity from its bond rows (``LIQUIDITY_COLUMNS`` records; swaps left out), netted by ISIN across
    the scope's books and its settled and pending rows (a sale to settle reduces the holding it sells; one ISIN in
    two books is one position): per ISIN the net face and value and its days ``|net face| / (participation x ADV)``;
    ``liquid(n)`` the value saleable within ``n`` business days, ``sum min(1, n / days) x net value`` over net longs
    (a net short counts its full value) plus ``cash`` at day 0 (the scope's net cash on a trade-date view); the
    longest days, the value-weighted mean, the value of the illiquid ISINs (by kind, class or rating, flagged
    ``illiquid_static`` on a row, or past ``days_limit`` days)."""
    by: dict[str, list[Mapping]] = {}
    for r in rows:
        if _text(r.get("kind")) == "ois":
            continue
        by.setdefault(_text(r.get("isin")), []).append(r)
    isins = []
    for isin, members in sorted(by.items()):
        face = rk._fsum(float(m.get("face") or 0.0) for m in members)
        value = rk._fsum(float(m.get("value") or 0.0) for m in members)
        adv = next((float(m["adv"]) for m in members if _finite(m.get("adv"))), math.nan)
        part = next((float(m["participation"]) for m in members if _finite(m.get("participation"))), PARTICIPATION)
        days = _days(face, part, adv)
        static = any("illiquid_static" in _text(m.get("flags")).split(";") for m in members)
        isins.append({"isin": isin, "face": face, "value": value, "days": days,
                      "illiquid": static or days > days_limit})

    def liquid(n: float) -> float:
        total = [cash]
        for x in isins:
            if x["face"] > 0:
                total.append(x["value"] if x["days"] <= 0 else (min(1.0, n / x["days"]) * x["value"]
                                                               if math.isfinite(x["days"]) else 0.0))
            else:
                total.append(x["value"])
        return rk._fsum(total)

    longs = [x for x in isins if x["face"] > 0]
    weights = rk._fsum(abs(x["value"]) for x in longs)
    if any(not math.isfinite(x["days"]) for x in longs if abs(x["value"]) > 0):
        mean = math.inf
    else:
        mean = rk._fsum(abs(x["value"]) * x["days"] for x in longs) / weights if weights else math.nan
    worst = max(longs, key=lambda x: (x["days"], x["isin"])) if longs else None
    return {"isins": isins, "liquid": liquid, "max_days": worst["days"] if worst else math.nan,
            "max_isin": worst["isin"] if worst else "", "mean_days": mean,
            "illiquid_value": rk._fsum(abs(x["value"]) for x in isins if x["illiquid"])}


def _finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _book_cash(ctx: rk.RiskContext, book: rk.RiskBook) -> dict[str, float]:
    """Each book's net cash on a trade-date view: the NAV row's cash less borrowing plus money lent and receivables,
    less the purchases still to settle and plus the sales still to settle (their market value), so that a pending
    trade moves money between cash and the bond it is. Without a NAV table (a back-test's snapshot) the book's NAV
    less the value of its positions and swaps, the same trade-date cash; NaN when neither is known."""
    rows: dict[str, Mapping] = {}
    if ctx.nav is not None:
        frame = rk._frame(ctx.nav, "the NAV table")
        for _, rec in rk._lines(frame):
            if "date" in rec and rk._date(rec["date"], "the NAV date") == book.date:
                rows[_text(rec.get("book"))] = rec
    out = {}
    for name in sorted({p.book for p in book.items} | set(rows) - {"fund"}):
        rec = rows.get(name)
        if rec is None:
            nav = book.nav.get(("book", name), math.nan) if ctx.nav is None else math.nan
            held = rk._fsum([*(p.market_value for p in book.positions if p.book == name),
                             *(s.value for s in book.swaps if s.book == name)])
            out[name] = nav - held if math.isfinite(nav) else math.nan
            continue
        net = rk._fsum(rk._num(rec.get(k), k, blank=0.0) * s for k, s in (("cash", 1), ("repo_borrowed", -1),
                                                                          ("lent", 1), ("receivables", 1)))
        pending = rk._fsum(p.market_value for p in book.positions if p.book == name and p.state == "pending")
        out[name] = net - pending
    return out


def liquidity(book: rk.RiskBook, ctx: rk.RiskContext, *, participation: float = PARTICIPATION,
              multiplier: float = STRESS_SPREAD_MULTIPLIER, var_by_scope: Mapping[tuple[str, str], float] | None = None
              ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """``(LIQUIDITY_COLUMNS, LIQUIDITY_SUMMARY_COLUMNS)``. Per position the ADV and its source, the half spread and
    the illiquid flag; its days and exit costs are those of its book's net position in the ISIN (settled and pending
    rows netted: a sale to settle shortens the days of the holding it sells), the cost allocated to the rows by face.
    Per scope (``scope_liquidity``, netted by ISIN across the scope's books): the shares of NAV saleable within 1, 5
    and 20 business days, the scope's net cash (trade-date view) counted at day 0, the longest and the value-weighted
    days, the illiquid share, the costs of the net positions (stressed ``multiplier`` times) and the
    liquidity-adjusted VaR (the scope's one-day VaR of ``var_by_scope`` plus the stressed cost)."""
    if not 0 < participation <= 1:
        raise ValueError(f"participation must be above 0 and at most 1, not {participation}")
    if not multiplier >= 1:
        raise ValueError(f"the stressed spread multiplier must be at least 1, not {multiplier}")
    sctx = StressContext(risk=ctx, participation=participation, multiplier=multiplier)
    advs = adv_of(book, ctx)
    net_book: dict[tuple[str, str], list[float]] = {}
    for item in book.positions:
        acc = net_book.setdefault((item.book, item.isin), [0.0, 0.0])
        acc[0] += item.face
        acc[1] += abs(item.face)
    rows, gross = [], []
    for item in book.items:
        if isinstance(item, rk.RiskSwap):
            try:
                h, cost = _exit_parameters(item, sctx)
            except ValueError:
                h, cost = math.nan, math.nan
            rows.append({"date": book.date, "as_of": rk._iso(book.as_of), "book": item.book,
                         "strategy": item.strategy, "isin": item.isin, "kind": "ois",
                         "face": -item.swap.notional if item.swap.pay_fixed else item.swap.notional,
                         "value": item.value, "adv": math.nan, "adv_source": "", "participation": participation,
                         "days_to_liquidate": math.nan, "half_spread_bp": h, "liquidation_cost": cost,
                         "stressed_cost": cost * multiplier, "illiquid": False, "flags": "no_adv_ois"})
            gross.append(cost)
            continue
        adv, source, flags = advs[item.key]
        net, total = net_book[(item.book, item.isin)]
        days = _days(net, participation, adv)
        try:
            h, cost = _exit_parameters(item, sctx)
        except ValueError as exc:
            h, cost = math.nan, math.nan
            flags = (*flags, f"no_cost_rule: {exc}")
        static = _static_illiquid(item)
        if static:
            flags = (*flags, "illiquid_static")
        share = abs(net) / total if total else 0.0  # the book's net position's cost, allocated to its rows by face
        rows.append({"date": book.date, "as_of": rk._iso(book.as_of), "book": item.book, "strategy": item.strategy,
                     "isin": item.isin, "kind": item.kind, "face": item.face, "value": item.market_value, "adv": adv,
                     "adv_source": source, "participation": participation, "days_to_liquidate": days,
                     "half_spread_bp": h, "liquidation_cost": cost * share, "stressed_cost": cost * share * multiplier,
                     "illiquid": bool(static or days > ILLIQUID_DAYS), "flags": rk._flags(flags)})
        gross.append(cost)
    detail = pd.DataFrame(rows, columns=list(LIQUIDITY_COLUMNS))
    cash = _book_cash(ctx, book)
    summary = []
    for scope_type, scope in book.scopes():
        idx = [k for k, r in enumerate(rows) if scope_type == "fund" or (scope_type == "book" and r["book"] == scope)
               or (scope_type == "strategy" and r["strategy"] == scope)]
        mine = [rows[k] for k in idx]
        books = sorted({b for b in cash if scope_type == "fund" or (scope_type == "book" and b == scope)
                        or (scope_type == "strategy" and book.strategies.get(b, b) == scope)})
        scope_cash = rk._fsum(cash[b] for b in books) if all(math.isfinite(cash[b]) for b in books) else math.nan
        figures = scope_liquidity(mine, cash=scope_cash if math.isfinite(scope_cash) else 0.0)
        nav = book.nav_of(scope_type, scope)
        ok = math.isfinite(nav) and nav > 0 and math.isfinite(scope_cash)
        # the scope's exit cost on its net positions: each ISIN's gross cost per unit of face times its net face
        unit: dict[str, list[float]] = {}
        for k in idx:
            r = rows[k]
            if r["kind"] == "ois":
                continue
            acc = unit.setdefault(r["isin"], [0.0, 0.0])
            acc[0] += gross[k] if math.isfinite(gross[k]) else 0.0
            acc[1] += abs(r["face"])
        cost = rk._fsum((c / f * abs(x["face"]) if f else 0.0) for x in figures["isins"]
                        for c, f in [unit.get(x["isin"], [0.0, 0.0])])
        cost += rk._fsum(gross[k] for k in idx if rows[k]["kind"] == "ois" and math.isfinite(gross[k]))
        var = (var_by_scope or {}).get((scope_type, scope), math.nan)
        summary.append({"date": book.date, "as_of": rk._iso(book.as_of), "scope_type": scope_type, "scope": scope,
                        "share_1d": figures["liquid"](1) / nav if ok else math.nan,
                        "share_5d": figures["liquid"](5) / nav if ok else math.nan,
                        "share_20d": figures["liquid"](20) / nav if ok else math.nan,
                        "max_days": figures["max_days"], "mean_days": figures["mean_days"],
                        "illiquid_share": figures["illiquid_value"] / nav if math.isfinite(nav) and nav > 0
                        else math.nan, "liquidation_cost": cost, "stressed_cost": cost * multiplier,
                        "lvar": var + cost * multiplier})
    return detail, pd.DataFrame(summary, columns=list(LIQUIDITY_SUMMARY_COLUMNS))


# ---------------------------------------------------------------- funding liquidity (R4.4)


def _funding_rows(funding, d: date) -> list[dict]:
    if funding is None:
        return []
    frame = rk._frame(funding, "the funding table")
    rk._check_columns(frame, ("date", "book", "segment", "borrowed"), rt.FUNDING_COLUMNS, "the funding table")
    return [rec for _, rec in rk._lines(frame) if rk._date(rec["date"], "the funding date") == d]


def funding_liquidity(funding, positions, book: rk.RiskBook, ctx: rk.RiskContext,
                      scenarios: Sequence[ScenarioRow] = (), *, nav=None) -> pd.DataFrame:
    """``FUNDING_LIQUIDITY_COLUMNS`` per book: under a preset that cannot borrow (``unregulated_company``) the
    settlement cover of the purchases still to settle (cash over their value; borrowing ``not_applicable``, with the
    reason); under one that borrows the night's borrowing (the engine's ``funding.csv``) against its limit, the
    haircut equity, TREPS margin and idle cash, the ALM ratios (cumulative negative mismatch over cumulative outflows
    in 1-7, 8-14 and 15-30 days; overnight repo an outflow on day 1, purchases on their settlement, cash on day 1,
    securities in the bucket of their days to liquidate: U slotting) and one row per funding scenario (extra interest
    ``borrowed x dr x H / 365``, extra equity ``dh x value``, TREPS money lost under CCIL's earlier caps, the shortfall
    when market repo is off). Under either preset every row carries the book's liquid assets (R4.4: the NAV row's cash
    on a trade-date view, less the trades still to settle at their value, plus the T-bills, CMBs and CP it holds or has
    bought) and their share of its NAV."""
    d = book.date
    access = ctx.settings.access
    borrows = access.repo or access.treps
    nights = _funding_rows(funding if funding is not None else ctx.funding, d)
    navs = {}
    nav = nav if nav is not None else ctx.nav
    if nav is not None:
        frame = rk._frame(nav, "the NAV table")
        for _, rec in rk._lines(frame):
            if rk._date(rec["date"], "the NAV date") == d:
                navs[_text(rec["book"])] = rec
    liq, _ = liquidity(book, ctx)
    days_of = {(r["book"], r["isin"]): r["days_to_liquidate"] for r in liq.to_dict("records")}
    rows = []
    books = sorted({p.book for p in book.items})
    for name in books:
        mine = [p for p in book.positions if p.book == name]
        pending_buys = rk._fsum(p.units * p.dirty for p in mine if p.state == "pending" and p.face > 0)
        cash = rk._num(navs.get(name, {}).get("cash"), f"the cash of {name}", blank=math.nan)
        cover = cash / pending_buys if pending_buys > 0 and math.isfinite(cash) else math.nan
        # R4.4: cash on a trade-date view (less what the trades to settle pay, plus what they receive) plus the
        # T-bills, CMBs and CP held or bought, over the book's NAV
        pending = rk._fsum(p.market_value for p in mine if p.state == "pending")
        liquid = cash - pending + rk._fsum(p.market_value for p in mine if p.kind in LIQUID_ASSET_KINDS) \
            if math.isfinite(cash) else math.nan
        book_nav = rk._num(navs.get(name, {}).get("nav"), f"the NAV of {name}", blank=math.nan)
        base = {"date": d, "as_of": rk._iso(book.as_of), "book": name, "legal_form": access.name,
                "pending_purchases": pending_buys, "settlement_cover": cover, "liquid_assets": liquid,
                "liquid_asset_share": liquid / book_nav if math.isfinite(liquid) and book_nav > 0 else math.nan}
        if not borrows:
            rows.append({**base, "applicable": False, "borrowed": 0.0, "limit": math.nan, "headroom": math.nan,
                         "haircut_equity": math.nan, "margin": math.nan, "idle_cash": cash, "scenario_id": "",
                         "extra_interest": math.nan, "extra_equity": math.nan, "treps_lost": math.nan,
                         "shortfall": math.nan, "alm_1_7d": math.nan, "alm_8_14d": math.nan, "alm_15_30d": math.nan,
                         "notes": f"not_applicable: {access.name} does not borrow (no repo or TREPS; RBI counts any "
                                  "outside liability as public funds: a question for counsel); settlement cover is "
                                  "cash over the purchases still to settle (sales still to settle not counted)"})
            continue
        night = [r for r in nights if _text(r.get("book")) == name]
        borrowed = rk._fsum(rk._num(r.get("borrowed"), "borrowed", blank=0.0) for r in night)
        limits = [rk._num(r.get("limit"), "limit", blank=math.nan) for r in night]
        limit = max((v for v in limits if math.isfinite(v)), default=math.nan)
        eq = rk._fsum(rk._num(r.get("haircut_equity"), "haircut_equity", blank=0.0) for r in night)
        margin = rk._fsum(rk._num(r.get("margin"), "margin", blank=0.0) for r in night)
        idle = rk._fsum(rk._num(r.get("idle_cash"), "idle_cash", blank=0.0) for r in night)
        treps = rk._fsum(rk._num(r.get("borrowed"), "borrowed", blank=0.0) for r in night
                         if _text(r.get("segment")) == "triparty")
        market = rk._fsum(rk._num(r.get("borrowed"), "borrowed", blank=0.0) for r in night
                          if _text(r.get("segment")) == "market_repo")
        alm = _alm(mine, days_of, borrowed, cash if math.isfinite(cash) else 0.0, d)
        rows.append({**base, "applicable": True, "borrowed": borrowed, "limit": limit,
                     "headroom": limit - borrowed if math.isfinite(limit) else math.nan, "haircut_equity": eq,
                     "margin": margin, "idle_cash": idle, "scenario_id": "", "extra_interest": math.nan,
                     "extra_equity": math.nan, "treps_lost": math.nan, "shortfall": math.nan, **alm,
                     "notes": "ALM: securities slotted by their days to liquidate (U); limits 10%, 10%, 20% "
                              "(ALM Directions, verified notes NR:S18-S19)"})
        for sid, srows in scenarios_by_id([r for r in scenarios if r.component in FUNDING_COMPONENTS]).items():
            extra_interest = extra_equity = lost = shortfall = 0.0
            for r in srows:
                if r.component == "repo_rate":
                    extra_interest += borrowed * r.size_bp * r.multiplier / 1e4 * max(r.horizon_days, 1) / _YEAR
                elif r.component == "haircut":
                    for p in mine:
                        if p.state != "settled" or p.face <= 0:
                            continue
                        h = _haircut_of(ctx, p, d)
                        if not math.isfinite(h):
                            continue
                        dh = r.size_bp / 1e4 * r.multiplier if r.shape == "add" else (r.multiplier - 1) * h / 100
                        extra_equity += dh * p.market_value
                elif r.component == "treps_cap":
                    lost += max(treps - _treps_lendable(ctx, mine, d, date(2026, 6, 1)), 0.0)
                elif r.component == "market_repo":
                    shortfall += market
            if any(r.component == "market_repo" for r in srows):
                shortfall += lost
            rows.append({**base, "applicable": True, "borrowed": borrowed, "limit": limit,
                         "headroom": limit - borrowed if math.isfinite(limit) else math.nan, "haircut_equity": eq,
                         "margin": margin, "idle_cash": idle, "scenario_id": sid, "extra_interest": extra_interest,
                         "extra_equity": extra_equity, "treps_lost": lost, "shortfall": shortfall, **alm,
                         "notes": rk._flags(sorted({r.status for r in srows}))})
    return pd.DataFrame(rows, columns=list(FUNDING_LIQUIDITY_COLUMNS))


def _haircut_of(ctx: rk.RiskContext, p: rk.RiskPosition, d: date) -> float:
    if ctx.market.haircuts is None:
        return math.nan
    try:
        return funding_haircut(read_haircuts(ctx.market.haircuts), kind=p.kind, maturity=p.maturity, on=d,
                               isin=p.isin, liquidity=p.liquidity)
    except ValueError:
        return math.nan


def _treps_lendable(ctx: rk.RiskContext, positions: Sequence[rk.RiskPosition], d: date, rule_day: date) -> float:
    """What TREPS would lend against the book's longs under CCIL's caps as they stood on ``rule_day``
    (``funding.treps_shares``; a date before 2 Jun 2026 takes the earlier rule as summarised, UNVERIFIED)."""
    longs = []
    for p in positions:
        if p.state != "settled" or p.face <= 0 or treps_class(p.kind, p.liquidity) == "ineligible":
            continue
        h = _haircut_of(ctx, p, d)
        if math.isfinite(h):
            longs.append((p.kind, p.liquidity, (1 - h / 100) * p.market_value))
    if not longs:
        return 0.0
    shares, _ = treps_shares(longs, rule_day)
    return rk._fsum(s * v for s, (_, _, v) in zip(shares, longs))


def _alm(positions: Sequence[rk.RiskPosition], days_of: Mapping, borrowed: float, cash: float, d: date) -> dict:
    """The three ALM ratios: cumulative negative mismatch over cumulative outflows in each bucket (0 when no
    mismatch; NaN with no outflow)."""
    inflow = {k: 0.0 for _, _, k in ALM_BUCKETS}
    outflow = {k: 0.0 for _, _, k in ALM_BUCKETS}
    first = ALM_BUCKETS[0][2]
    inflow[first] += max(cash, 0.0)
    outflow[first] += borrowed

    def bucket(days: float) -> str | None:
        for lo, hi, key in ALM_BUCKETS:
            if days <= hi:
                return key
        return None
    for p in positions:
        if p.state == "pending":
            key = bucket(max((p.settlement - d).days, 1))
            if key is None:
                continue
            if p.face > 0:
                outflow[key] += p.units * p.dirty
            else:
                inflow[key] += -p.units * p.dirty
            continue
        days = days_of.get((p.book, p.isin), math.inf)
        key = bucket(math.ceil(days)) if math.isfinite(days) else None  # no ADV: not saleable within 30 days
        if key is not None and p.face > 0:
            inflow[key] += p.market_value
    out, cum_in, cum_out = {}, 0.0, 0.0
    for _, _, key in ALM_BUCKETS:
        cum_in += inflow[key]
        cum_out += outflow[key]
        out[key] = max(cum_out - cum_in, 0.0) / cum_out if cum_out > 0 else math.nan
    return out
