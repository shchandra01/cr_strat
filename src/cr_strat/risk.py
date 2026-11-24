"""Risk measurement: sensitivities, factor histories, value at risk and expected shortfall, and their back-tests.

**The book** (R1 of the risk specification of 24 Nov 2026). The return engine's positions (``positions.csv`` of
``cr-strat returns``, ``returns.POSITION_COLUMNS``; or a ``returns.Snapshot`` through ``book_from_snapshot``) of one
day, read as of a time ``as_of``: each bond as it was known then (``returns.instruments_as_known``: terms, announced
FRB coupons, ratings and curves public by ``as_of``), priced for the row's settlement at the row's yield, a purchase
still to settle as the bond it will be; each OIS swap (the run's swap trades) valued on the day's OIS curve public by
``as_of``, rolled to the day as the engine rolls it. A row whose terms cannot be had or whose bond cannot be priced
as known then refuses the run, naming the row; a bond without its curve is measured at its yield (``no_curve``); a
curve older than the day is used and flagged ``stale_curve``.

**Sensitivities** (R1.1-R1.7), in rupees lost per bp rise (positive for a long bond): the yield DV01 (the engine's
``dv01``, recomputed and flagged ``dv01_differs`` when it is not); the curve DV01 and key-rate DV01s on the zero rates
of the position's reference curve seen from its settlement (the fitted G-sec NSS curve, the attribution's reference
map for other kinds, the OIS curve for swaps), with triangle weights between ``KEY_TENORS`` held flat beyond the end
nodes so that the key rates sum to the parallel curve DV01: analytic for fixed flows, central differences of +-0.5 bp
for swaps, and also by OIS par quote for swaps (``swap_par_krd``, central; one-sided ``up`` on request); the CS01 on
the z-spread to the reference curve (``asset_swaps.z_spread``; corporate bonds, CP and CDs), with spread factors
``corp:<rating bucket>:<tenor bucket>``, ``cd:<tenor bucket>`` and, for SDLs on the G-sec curve, ``sdl_spread:<tenor
bucket>``; a GoI FRB's index DV01 (``floating.index_dv01``) placed on the G-sec key rates at its next reset (or on the
``tbill182`` factor when its history is given) and its margin DV01 (``floating.spread_dv01``) on ``frb_dm:<isin>``;
the convexity at the yield and on the curve (rupees per bp squared); the jump to default ``max(LGD x N + (MV - N), 0)``
with Basel's LGDs (MAR22: 75% senior, 100% subordinated (AT1 and tier 2), 25% covered; senior when nothing says
otherwise), for corporate bonds, CP and CDs (SDLs with ``jtd_states``; GoI securities never), without netting. A bond
without its curve has its yield DV01 placed at its Macaulay duration (``yield_proxy``). Tables: by position
(``POSITION_RISK_COLUMNS``), every sensitivity (``SENSITIVITY_COLUMNS``), key rates by scope (fund, book, strategy;
``KEY_RATE_COLUMNS``), CS01 by issuer, group, sector, rating bucket, tenor bucket and rating by tenor
(``CREDIT_COLUMNS``), JTD by issuer and group (``JTD_COLUMNS``) and every number summed by kind, tenor bucket, issuer,
group, sector and rating bucket (``SUMMARY_COLUMNS``), each also in years of NAV and percent of NAV where the NAV is
known.

**Factor histories** (R2.1). Daily changes (bp) of each factor from levels public by ``as_of``: key rates of the
history of NSS fits (each fit counted from its ``public_from``, else 18:00 on its date, flagged ``default_time``; a
tenor outside a fit's range takes the nearest in-range key rate's change, flagged), OIS zero rates at the OIS key
tenors (no fill), and the user's factor table (``FACTOR_HISTORY_COLUMNS``: ``corp:``, ``cd:``, ``sdl_spread:``,
``frb_dm:``, ``tbill182``, ``resid:`` levels with public times). A change over a gap of 2 to ``max_gap_days`` business
days is kept scaled by ``1/sqrt(g)`` (``gap_rule``), a longer gap breaks the series. ``factor_changes.csv`` writes them
(``FACTOR_CHANGE_COLUMNS``).

**VaR and ES** (R2.2-R2.7). Per scope (fund, each book, each strategy) and method: ``hs_sensitivity`` (historical
simulation, P&L the sensitivities times each day's factor changes, with half the convexity on request), ``hs_full``
(each scenario revalued in full on the shifted curves and spreads) and ``parametric`` (normal, the sensitivities on an
EWMA covariance, lambda 0.94 over at most 550 observations with normalised weights, or the sample covariance). A
scope's factors with at least ``min_obs_hard`` changes are used and the window is the latest ``window`` dates on which
all of them have one; the others are left out (``coverage`` below one: ``incomplete``) unless the proxy table maps them
to a factor with history. Fewer than ``min_obs_hard`` observations: refused, with the count; fewer than ``min_obs``:
flagged ``short_history``. VaR at ``a`` is the ``ceil(n(1 - a))``-th largest loss (``quantile_rule``) and ES the
Acerbi-Tasche tail mean with the tail ``m = n(1 - a)`` counted exactly (``Fraction``); both are reported at 99% and
97.5%. The 10-day figures sum overlapping runs of 10 changes (``horizon_rule``; the parametric method scales by the
square root of time). With ``windows`` including ``stressed``, the stressed ES (MAR33.6) searches the history for the
window of ``window`` observations with the largest ES on the reduced set of factors that have the longest history
(those starting within 30 days of the earliest) and scales it by the current full-to-reduced ratio, floored at one;
``liquidity_horizons`` adds the MAR33.4 cascade of the 10-day ES over the liquidity horizons of each factor class
(``LIQUIDITY_HORIZONS``). Euler components split each VaR and ES by position and by factor (``VAR_COMPONENT_COLUMNS``).

**Back-tests** (R2.8). The one-day VaR of business day ``d`` against the P&L of ``d + 1``: actual (the engine's
``nav.csv`` ``pnl``) and hypothetical (``hypothetical_pnl``: the market lines of the attribution on positions held from
the previous close), counted apart and the larger count used; an exception when the P&L is strictly below minus the
VaR; a missing VaR or P&L counts as an exception. Kupiec's proportion-of-failures test, Christoffersen's independence
and conditional-coverage tests (chi-squared tails from ``math.erfc``) and the Basel traffic light (zones from the
cumulative binomial probability, 95% and 99.99%; MAR32's multipliers at exactly 250 observations).
``append_var_history`` keeps the daily VaR history the back-test reads (``VAR_HISTORY_COLUMNS``).

**Sources** (cited in our own words; nothing copied): the Basel Framework's market risk standard
(https://www.bis.org/committees/bcbs/basel-framework/MAR/standard.pdf): MAR21 key tenors, MAR22 LGDs, MAR32
back-testing and multipliers, MAR33 ES, liquidity horizons and the stressed window, MAR99 the zones for other sample
sizes; J.P. Morgan/Reuters, RiskMetrics Technical Document, 4th ed. (1996), sec. 5.3, for lambda 0.94 and 550
observations; Kupiec (J. Derivatives, 1995) and Christoffersen (International Economic Review, 1998) for the tests;
Acerbi and Tasche (J. Banking & Finance, 2002) for the ES of a finite sample. The conventions marked UNVERIFIED in the constants above and in ``UNVERIFIED_RULES`` (listed
in every run's ``notes.txt``) are choices for the fund to confirm or change, all settable in ``RiskSettings``.

**Limits.** Tested on synthetic books and the specification's vectors only: no history of real fitted curves or
spreads has been run through it (the free demo uses the auction table's coarse cut-off yields). Callable bonds are
valued to maturity (no option model); FRB index risk is first-order at the next reset; the hypothetical P&L reads the
engine's attribution, whose lines are themselves approximations; the stressed window needs a history covering a stress
period, which none of the shipped data is. Library code never reads the clock: the same inputs give the same bytes.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from fractions import Fraction
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np
import pandas as pd

from cr_strat import returns as rt
from cr_strat.asset_swaps import z_spread as _z_spread
from cr_strat.bonds import DiscountInstrument
from cr_strat.calendars import BusinessCalendar, mumbai_settlement_calendar
from cr_strat.conventions import as_date
from cr_strat.corporates import CorporateBond, public_time
from cr_strat.floating import GoiFloatingRateBond, index_dv01, next_reset, price_from_dm, spread_dv01
from cr_strat.issuance import tenor_bucket
from cr_strat.ois import OisCurve, OisSwap, as_time, ois_npv, ois_schedule
from cr_strat.rating_scale import parse_rating, rating_bucket

# ---------------------------------------------------------------- conventions (each UNVERIFIED unless said)

KEY_TENORS = (0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 15.0, 20.0, 30.0, 40.0, 50.0)  # UNVERIFIED choice: FRTB's
# GIRR vertices 0.25-30y (MAR21.8, verified) plus the engine's 7 and 40y nodes and 50y (GoI issues 50-year bonds)
OIS_KEY_TENORS = (0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0)  # used up to the OIS curve's last node
BUMP_BP = 1.0
VAR_ALPHA = 0.99  # MAR32.9 (verified): the back-tested one-day VaR
ES_ALPHA = 0.975  # MAR33.3 (verified)
WINDOW = 250  # MAR32.18: at least one year of observations (verified)
MIN_OBS = 250  # below: flagged short_history
MIN_OBS_HARD = 60  # UNVERIFIED: below, a VaR is refused
MAX_GAP_DAYS = 5  # UNVERIFIED: a change over 2-5 business days is scaled by 1/sqrt(g); a longer gap breaks the series
EWMA_LAMBDA = 0.94  # RiskMetrics Technical Document 1996, Table 5.9 (verified)
EWMA_MAX_OBS = 550  # RiskMetrics: 550 daily observations used in production (verified)
LGD = {"senior": 0.75, "subordinated": 1.0, "covered": 0.25}  # MAR22.9-22.12 (verified); senior by default (U)
SENIORITIES = tuple(LGD)
METHODS = ("hs_sensitivity", "hs_full", "parametric")
HORIZON_RULES = ("overlapping", "sqrt_time")
QUANTILE_RULES = ("ceil", "floor_plus_one", "interpolate")
GAP_RULES = ("scale", "drop", "raise")
COV_METHODS = ("ewma", "sample")
SCENARIO_SHAPES = ("key_rates", "full_curve")
SDL_MODES = ("spread", "own_curve")
RESIDUAL_CHOICES = ("in_fit", "loo")
WINDOWS = ("current", "stressed")
FACTOR_CLASSES = ("gsec", "sdl", "sdl_spread", "ois", "corp", "cd", "frb_dm", "tbill182", "resid")
SPREAD_CLASSES = ("sdl_spread", "corp", "cd", "frb_dm", "tbill182", "resid")
CREDIT_KINDS = frozenset({"corporate", "cp", "cd"})  # kinds with a credit spread factor, CS01 and JTD
GOI_KINDS = frozenset({"gsec", "green", "special", "tbill", "cmb", "strips", "frb"})
# the market lines of positions held from the previous close: the hypothetical P&L of a back-test (MAR32.18; this
# engine's reading UNVERIFIED); every other line (trades, costs, funding, the cash benchmark, memo lines) is left out
HYPOTHETICAL_LINES = ("accrual", "pull_to_par", "coupon_reset", "roll_down", "rich_cheap", "convexity",
                      "price_unattributed", "unexplained", "ois_carry", "ois_roll_down", "ois_settlement_rounding")
HYPOTHETICAL_PREFIXES = ("curve_", "krd_", "ois_curve_", "ois_krd_")
# liquidity horizons of MAR33.12 Table 2 (verified) mapped to the factor classes here (UNVERIFIED mapping)
LIQUIDITY_HORIZONS = {"gsec": 10, "sdl": 10, "ois": 10, "sdl_spread": 10, "tbill182": 10, "resid": 10, "frb_dm": 20,
                      "cd": 40, "corp": 40, "corp_hy": 60}
LH_STEPS = (10, 20, 40, 60, 120)  # MAR33.12 (verified)
MAR32_MULTIPLIERS = {0: 1.50, 1: 1.50, 2: 1.50, 3: 1.50, 4: 1.50, 5: 1.70, 6: 1.76, 7: 1.83, 8: 1.88, 9: 1.92}
MAR32_RED_MULTIPLIER = 2.00  # 10 or more exceptions at 250 observations (MAR32.9 Table 1, verified)
GREEN_LIMIT, AMBER_LIMIT = 0.95, 0.9999  # MAR99.17 (verified): zones from the cumulative binomial probability
IG_BUCKETS = ("AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB")  # national scale AAA to BBB- read as IG (U)
DEFAULT_TIME = time(18, 0)  # a curve or factor row without a public time counts from 18:00 on its date (the engine's)

# ---------------------------------------------------------------- layouts

POSITION_RISK_COLUMNS = ("date", "as_of", "book", "strategy", "isin", "kind", "state", "face", "settlement_date",
                         "yield_pct", "dirty_price", "value", "reference_curve", "z_spread_bp", "dv01_yield",
                         "dv01_curve", "cs01", "frb_index_dv01", "frb_spread_dv01", "next_reset", "convexity_yield",
                         "convexity_curve", "maturity_date", "tenor_bucket", "issuer_id", "group_id", "sector",
                         "rating_bucket", "rating_source", "seniority", "lgd", "jtd", "liquidity", "mark_source",
                         "mark_age_days", "flags")
SENSITIVITY_COLUMNS = ("date", "as_of", "book", "strategy", "isin", "kind", "factor", "factor_class", "curve", "tenor",
                       "sensitivity", "method", "basis", "flags")
KEY_RATE_COLUMNS = ("date", "as_of", "scope_type", "scope", "curve", "tenor", "krd", "krd_years")
CREDIT_COLUMNS = ("date", "as_of", "scope_type", "scope", "dimension", "key", "cs01", "cs01_years", "market_value",
                  "pct_nav", "jtd", "jtd_pct_nav", "positions", "flags")
CREDIT_DIMENSIONS = ("issuer", "group", "sector", "rating_bucket", "tenor_bucket", "rating_tenor")
JTD_COLUMNS = ("date", "as_of", "scope_type", "scope", "issuer_id", "group_id", "face", "market_value", "lgd", "jtd",
               "jtd_pct_nav")
SUMMARY_COLUMNS = ("date", "as_of", "scope_type", "scope", "dimension", "key", "positions", "face", "market_value",
                   "pct_nav", "dv01_yield", "dv01_years", "dv01_curve", "cs01", "frb_index_dv01", "frb_spread_dv01",
                   "convexity_yield", "convexity_curve", "jtd", "flags")
SUMMARY_DIMENSIONS = ("total", "kind", "tenor_bucket", "issuer", "group", "sector", "rating_bucket")
FACTOR_HISTORY_COLUMNS = ("date", "factor", "value", "unit", "public_from", "source")
FACTOR_CHANGE_COLUMNS = ("date", "previous_date", "factor", "change_bp", "gap_days", "flag")
VAR_COLUMNS = ("date", "as_of", "scope_type", "scope", "method", "horizon_days", "horizon_rule", "window",
               "window_start", "window_end", "n_obs", "alpha_var", "var", "var_pct_nav", "alpha_es", "es",
               "es_pct_nav", "coverage", "uncovered_rs_per_bp", "status", "flags")
VAR_COMPONENT_COLUMNS = ("date", "as_of", "scope_type", "scope", "method", "horizon_days", "component_type",
                         "component", "var_contribution", "es_contribution")
VAR_HISTORY_COLUMNS = ("date", "as_of", "scope_type", "scope", "method", "horizon_days", "alpha_var", "var",
                       "alpha_es", "es", "n_obs", "coverage", "status")
BACKTEST_COLUMNS = ("var_date", "pnl_date", "scope_type", "scope", "method", "var", "pnl_actual", "pnl_hypothetical",
                    "exception_actual", "exception_hypothetical", "missing")
BACKTEST_SUMMARY_COLUMNS = ("end", "scope_type", "scope", "method", "alpha", "observations", "exceptions_actual",
                            "exceptions_hypothetical", "exceptions", "expected", "kupiec_lr", "kupiec_p", "n00",
                            "n01", "n10", "n11", "christoffersen_lr", "christoffersen_p", "lr_cc", "lr_cc_p",
                            "cumulative_probability", "zone", "multiplier", "notes")
# tables kept by the user (data/README.md)
ISSUER_COLUMNS = ("issuer_id", "name", "group_id", "group_name", "sector", "seniority", "effective_from",
                  "effective_to", "source")
STRATEGY_BOOK_COLUMNS = ("book", "strategy", "effective_from")
PROXY_COLUMNS = ("factor", "proxy", "scale", "source")
SENIORITY_COLUMNS = ("isin", "seniority", "source")
POSITION_REQUIRED = ("date", "book", "isin", "kind", "state", "face", "settlement_date", "clean_mark", "yield_pct",
                     "value", "dv01")

_YEAR = 365.0
_N = NormalDist()


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


def _date(value, where: str) -> date:
    try:
        return as_date(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{where}: {value!r} is not a date ({exc})") from None


def _opt_date(value, where: str) -> date | None:
    return None if _blank(value) else _date(value, where)


def _choice(value, allowed, name: str) -> None:
    if value not in allowed:
        raise ValueError(f"{name} must be one of {allowed}, not {value!r}")


def _frame(table, what: str) -> pd.DataFrame:
    """A DataFrame from one, from a list of row dicts or from a CSV path (text cells); columns lower-cased."""
    if isinstance(table, pd.DataFrame):
        out = table.copy()
    elif isinstance(table, (list, tuple)):
        out = pd.DataFrame(list(table))
    else:
        if not Path(table).exists():
            raise FileNotFoundError(f"{what} not found: {table}")
        out = pd.read_csv(table, dtype=str, keep_default_na=False)
    out.columns = [str(c).strip().lower() for c in out.columns]
    return out


def _lines(frame: pd.DataFrame) -> Iterable[tuple[int, dict]]:
    """(CSV line, row) pairs, header line 1; rows whose every cell is blank skipped (their lines still counted)."""
    for n, rec in enumerate(frame.to_dict("records"), start=2):
        if not all(_blank(v) for v in rec.values()):
            yield n, rec


def tenor_label(t: float) -> str:
    """A key tenor's label in factor names: ``0.25y``, ``10y`` (as the engine's ``krd_<tenor>`` lines)."""
    return f"{float(t):g}y"


def key_weights(t, nodes: Sequence[float]) -> np.ndarray:
    """Triangle weights ``w_k(t)`` (F1) as a ``len(nodes) x len(t)`` array: a unit vector interpolated linearly
    between neighbouring nodes, flat beyond the first and last; each column sums to one, so key-rate sensitivities sum
    to the parallel one. The same shapes as ``returns.factor_shapes("key_rates", nodes)``."""
    flat = np.asarray(t, dtype=float).reshape(-1)
    nodes = np.asarray(nodes, dtype=float)
    eye = np.eye(len(nodes))
    return np.vstack([np.interp(flat, nodes, eye[k]) for k in range(len(nodes))]) if len(nodes) else \
        np.zeros((0, flat.size))


def key_rate_shift(points: Mapping[float, float], t) -> np.ndarray:
    """A shift in bp at times ``t`` from key-rate moves ``{tenor: bp}``: the triangles of the given tenors, held flat
    beyond the first and last (F1)."""
    tenors = sorted(float(k) for k in points)
    if not tenors:
        return np.zeros_like(np.asarray(t, dtype=float), dtype=float)
    values = np.array([float(points[k]) for k in sorted(points, key=float)])
    flat = np.asarray(t, dtype=float)
    return np.interp(flat, np.array(tenors), values)


def _flags(*items) -> str:
    out: list[str] = []
    for item in items:
        for part in ([item] if isinstance(item, str) else list(item or ())):
            if part and part not in out:
                out.append(part)
    return ";".join(out)


def _fsum(values) -> float:
    return math.fsum(float(v) for v in values)


# ---------------------------------------------------------------- settings and context


@dataclass(frozen=True)
class RiskSettings:
    """How risk is measured; every default is UNVERIFIED unless the constants above say otherwise.

    ``key_tenors`` and ``ois_key_tenors`` (years; the OIS set used up to the curve's last node); ``bump_bp`` for the
    central differences; ``var_alpha`` and ``es_alpha`` (99% VaR, 97.5% ES: both reported at both levels); ``window``
    (observations of the current window), ``min_obs`` (fewer: ``short_history``) and ``min_obs_hard`` (fewer: refused);
    ``gap_rule`` (``scale``: a change over ``g <= max_gap_days`` business days kept scaled by ``1/sqrt(g)``; ``drop``;
    ``raise``); ``quantile_rule`` (``ceil``: VaR the ``ceil(n(1-a))``-th largest loss; ``floor_plus_one``;
    ``interpolate``); ``ewma_lambda`` and ``ewma_max_obs`` with ``cov_method`` (``ewma`` or ``sample``) for the
    parametric VaR; ``horizons`` (days) and ``horizon_rule`` for the historical methods (``overlapping`` 10-day sums or
    ``sqrt_time``; the parametric method always scales by the square root of time); ``scenario_shape`` (``key_rates``:
    curve scenarios are the key-rate changes joined by the triangles; ``full_curve``: the whole fitted change, full
    revaluation only); ``delta_gamma`` (the sensitivity method adds half the curve convexity times the scenario's
    duration-weighted parallel move squared); ``jtd_states`` (SDLs get a JTD); ``residual`` (``in_fit`` or ``loo``:
    ``resid:<isin>`` factors from the residual history); ``sdl_mode`` (``spread``: SDLs on the G-sec curve plus an
    ``sdl_spread`` factor; ``own_curve``: on the curve named ``sdl``); ``stressed_step`` (observations between the
    starts of the windows the stressed search tries); ``methods``; ``windows`` (``current`` and, when asked,
    ``stressed``); ``liquidity_horizons`` (the MAR33.4 cascade of the 10-day ES, reported as its own rows).
    """

    key_tenors: tuple[float, ...] = KEY_TENORS
    ois_key_tenors: tuple[float, ...] = OIS_KEY_TENORS
    bump_bp: float = BUMP_BP
    var_alpha: float = VAR_ALPHA
    es_alpha: float = ES_ALPHA
    window: int = WINDOW
    min_obs: int = MIN_OBS
    min_obs_hard: int = MIN_OBS_HARD
    gap_rule: str = "scale"
    max_gap_days: int = MAX_GAP_DAYS
    quantile_rule: str = "ceil"
    ewma_lambda: float = EWMA_LAMBDA
    ewma_max_obs: int = EWMA_MAX_OBS
    cov_method: str = "ewma"
    horizons: tuple[int, ...] = (1, 10)
    horizon_rule: str = "overlapping"
    scenario_shape: str = "key_rates"
    delta_gamma: bool = False
    jtd_states: bool = False
    residual: str | None = None
    sdl_mode: str = "spread"
    stressed_step: int = 1
    methods: tuple[str, ...] = METHODS
    windows: tuple[str, ...] = ("current",)
    liquidity_horizons: bool = False

    def __post_init__(self) -> None:
        for name in ("key_tenors", "ois_key_tenors"):
            tenors = tuple(float(t) for t in getattr(self, name))
            if not tenors or tenors[0] <= 0 or any(b <= a for a, b in zip(tenors, tenors[1:])):
                raise ValueError(f"{name} must be increasing positive tenors (years), not {getattr(self, name)}")
            object.__setattr__(self, name, tenors)
        if not (_num(self.bump_bp, "bump_bp") > 0):
            raise ValueError(f"bump_bp must be positive, not {self.bump_bp}")
        for name in ("var_alpha", "es_alpha"):
            if not 0.5 < _num(getattr(self, name), name) < 1:
                raise ValueError(f"{name} must be between 0.5 and 1, not {getattr(self, name)}")
        for name in ("window", "min_obs", "min_obs_hard", "max_gap_days", "ewma_max_obs", "stressed_step"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a whole number of at least 1, not {value!r}")
        if self.window < self.min_obs_hard or self.min_obs < self.min_obs_hard:
            raise ValueError(f"window ({self.window}) and min_obs ({self.min_obs}) must be at least min_obs_hard "
                             f"({self.min_obs_hard})")
        _choice(self.gap_rule, GAP_RULES, "gap_rule")
        _choice(self.quantile_rule, QUANTILE_RULES, "quantile_rule")
        _choice(self.cov_method, COV_METHODS, "cov_method")
        _choice(self.horizon_rule, HORIZON_RULES, "horizon_rule")
        _choice(self.scenario_shape, SCENARIO_SHAPES, "scenario_shape")
        _choice(self.sdl_mode, SDL_MODES, "sdl_mode")
        if self.residual is not None:
            _choice(self.residual, RESIDUAL_CHOICES, "residual")
        if not 0 < _num(self.ewma_lambda, "ewma_lambda") < 1:
            raise ValueError(f"ewma_lambda must be between 0 and 1, not {self.ewma_lambda}")
        horizons = tuple(self.horizons)
        if not horizons or any(isinstance(h, bool) or not isinstance(h, int) or h < 1 for h in horizons):
            raise ValueError(f"horizons must be whole numbers of days of at least 1, not {self.horizons}")
        object.__setattr__(self, "horizons", tuple(sorted(set(horizons))))
        methods = tuple(self.methods)
        for m in methods:
            _choice(m, METHODS, "a method")
        object.__setattr__(self, "methods", tuple(m for m in METHODS if m in methods))
        windows = tuple(self.windows)
        for w in windows:
            _choice(w, WINDOWS, "a window")
        object.__setattr__(self, "windows", tuple(w for w in WINDOWS if w in windows))
        for name in ("delta_gamma", "jtd_states", "liquidity_horizons"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be True or False")


@dataclass(frozen=True)
class RiskContext:
    """What a risk run reads besides the positions: the run's market data and engine settings (access preset,
    calendar, mark and attribution settings, costs), the risk settings and the time ``as_of`` everything is known at;
    and the tables the user keeps: ``swaps`` (``returns.SWAP_TRADE_COLUMNS``, for the OIS rows of a positions file),
    ``terms`` (trade rows giving bond terms, else the master), ``issuers`` (``ISSUER_COLUMNS``), ``strategy_books``
    (``STRATEGY_BOOK_COLUMNS``; default: each book is its own strategy), ``factor_history``
    (``FACTOR_HISTORY_COLUMNS``: spread and other factor levels with their public times), ``proxies``
    (``PROXY_COLUMNS``), ``adv`` (``stress.ADV_COLUMNS``), ``seniority`` (``SENIORITY_COLUMNS``), ``nss_table``
    (``returns.read_nss_params``' output, the curve history; default the market data's curves), ``nav`` (the engine's
    ``NAV_COLUMNS`` rows: NAV by book for the percentages), ``funding`` (the engine's ``FUNDING_COLUMNS`` rows, for the
    funding liquidity of a preset that borrows) and ``spread_matrix`` (``credit_spreads.read_spread_matrix``: peer
    spreads by rating, for the migration scenarios)."""

    market: rt.MarketData
    settings: rt.EngineSettings
    risk: RiskSettings
    as_of: datetime
    swaps: pd.DataFrame | None = None
    terms: Any = None
    issuers: pd.DataFrame | None = None
    strategy_books: pd.DataFrame | None = None
    factor_history: pd.DataFrame | None = None
    proxies: pd.DataFrame | None = None
    adv: pd.DataFrame | None = None
    seniority: pd.DataFrame | None = None
    nss_table: Any = None
    nav: pd.DataFrame | None = None
    funding: pd.DataFrame | None = None
    spread_matrix: pd.DataFrame | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.market, rt.MarketData):
            raise TypeError("market must be a returns.MarketData")
        if not isinstance(self.settings, rt.EngineSettings):
            raise TypeError("settings must be a returns.EngineSettings (with the fund's access preset)")
        if not isinstance(self.risk, RiskSettings):
            raise TypeError("risk must be a RiskSettings")
        object.__setattr__(self, "as_of", as_time(self.as_of, "as_of"))

    @property
    def calendar(self) -> BusinessCalendar:
        """The run's calendar: the engine's (lenient past the holiday list's years with ``project_calendar``)."""
        cal = self.settings.calendar if self.settings.calendar is not None else mumbai_settlement_calendar()
        return cal.lenient() if self.settings.project_calendar else cal

    @property
    def default_time(self) -> time | None:
        return self.settings.marks.default_time


# ---------------------------------------------------------------- the user's tables


def _check_columns(frame: pd.DataFrame, required: Sequence[str], columns: Sequence[str], what: str) -> None:
    missing = [c for c in required if c not in frame]
    if missing:
        raise ValueError(f"{what} lacks {missing} (columns: {', '.join(columns)})")


def read_issuers(table) -> pd.DataFrame:
    """The issuer table (``ISSUER_COLUMNS``): each row with ``issuer_id`` and ``effective_from`` (blank: always), an
    optional ``effective_to`` (excluded), ``group_id`` (blank: the issuer is its own group), ``sector`` and
    ``seniority`` (the default rank of its bonds: senior, subordinated or covered). Two rows of an issuer in force on
    one day are refused, as are unknown seniorities, naming the line."""
    frame = _frame(table, "the issuer table")
    _check_columns(frame, ("issuer_id",), ISSUER_COLUMNS, "the issuer table")
    rows = []
    for n, rec in _lines(frame):
        where = f"issuer table line {n}"
        issuer = _text(rec.get("issuer_id"))
        if not issuer:
            raise ValueError(f"{where}: issuer_id is blank")
        start = _opt_date(rec.get("effective_from"), f"{where}: effective_from") or date.min
        end = _opt_date(rec.get("effective_to"), f"{where}: effective_to") or date.max
        if end <= start:
            raise ValueError(f"{where}: effective_to {end} is not after effective_from {start}")
        seniority = _text(rec.get("seniority")).lower()
        if seniority and seniority not in SENIORITIES:
            raise ValueError(f"{where}: seniority {rec.get('seniority')!r} is not one of {SENIORITIES}")
        rows.append({"issuer_id": issuer, "name": _text(rec.get("name")), "group_id": _text(rec.get("group_id")),
                     "group_name": _text(rec.get("group_name")), "sector": _text(rec.get("sector")).lower(),
                     "seniority": seniority, "effective_from": start, "effective_to": end,
                     "source": _text(rec.get("source")), "line": n})
    out = pd.DataFrame(rows, columns=[*ISSUER_COLUMNS, "line"])
    for issuer, group in out.groupby("issuer_id", sort=True):
        spans = sorted(zip(group["effective_from"], group["effective_to"], group["line"]))
        for (a0, a1, la), (b0, _, lb) in zip(spans, spans[1:]):
            if b0 < a1:
                raise ValueError(f"the issuer table gives {issuer} two rows in force from {b0} (lines {la} and {lb})")
    return out.sort_values(["issuer_id", "effective_from"], kind="mergesort").reset_index(drop=True)


def read_strategy_books(table) -> pd.DataFrame:
    """The book-to-strategy map (``STRATEGY_BOOK_COLUMNS``): a book belongs to the strategy of its latest row with
    ``effective_from`` on or before the day (blank: always); a book without a row is its own strategy."""
    frame = _frame(table, "the strategy books")
    _check_columns(frame, ("book", "strategy"), STRATEGY_BOOK_COLUMNS, "the strategy books")
    rows, seen = [], {}
    for n, rec in _lines(frame):
        where = f"strategy books line {n}"
        book, strategy = _text(rec.get("book")), _text(rec.get("strategy"))
        if not book or not strategy:
            raise ValueError(f"{where}: book and strategy must both be given")
        start = _opt_date(rec.get("effective_from"), f"{where}: effective_from") or date.min
        if (book, start) in seen and seen[(book, start)] != strategy:
            raise ValueError(f"{where}: book {book} is mapped to two strategies from {start}")
        seen[(book, start)] = strategy
        rows.append({"book": book, "strategy": strategy, "effective_from": start})
    out = pd.DataFrame(rows, columns=list(STRATEGY_BOOK_COLUMNS)).drop_duplicates()
    return out.sort_values(["book", "effective_from"], kind="mergesort").reset_index(drop=True)


def read_seniority(table) -> pd.DataFrame:
    """Seniority by ISIN (``SENIORITY_COLUMNS``: senior, subordinated or covered), for bonds whose terms do not say."""
    frame = _frame(table, "the seniority table")
    _check_columns(frame, ("isin", "seniority"), SENIORITY_COLUMNS, "the seniority table")
    rows, seen = [], {}
    for n, rec in _lines(frame):
        isin, value = _text(rec.get("isin")).upper(), _text(rec.get("seniority")).lower()
        if not isin or value not in SENIORITIES:
            raise ValueError(f"seniority table line {n}: give an isin and one of {SENIORITIES}, not "
                             f"{rec.get('isin')!r}, {rec.get('seniority')!r}")
        if isin in seen and seen[isin] != value:
            raise ValueError(f"seniority table line {n}: {isin} is given two seniorities")
        seen[isin] = value
        rows.append({"isin": isin, "seniority": value, "source": _text(rec.get("source"))})
    return pd.DataFrame(rows, columns=list(SENIORITY_COLUMNS)).drop_duplicates("isin").reset_index(drop=True)


def read_proxies(table) -> pd.DataFrame:
    """Proxies (``PROXY_COLUMNS``): a factor without history mapped to one with it, times ``scale`` (default 1)."""
    frame = _frame(table, "the proxy table")
    _check_columns(frame, ("factor", "proxy"), PROXY_COLUMNS, "the proxy table")
    rows, seen = [], set()
    for n, rec in _lines(frame):
        factor, proxy = _text(rec.get("factor")), _text(rec.get("proxy"))
        if not factor or not proxy or factor == proxy:
            raise ValueError(f"proxy table line {n}: give a factor and a different proxy")
        if factor in seen:
            raise ValueError(f"proxy table line {n}: {factor} has two proxies")
        seen.add(factor)
        rows.append({"factor": factor, "proxy": proxy, "scale": _num(rec.get("scale"), f"proxy table line {n}: scale",
                                                                     blank=1.0), "source": _text(rec.get("source"))})
    return pd.DataFrame(rows, columns=list(PROXY_COLUMNS))


def read_positions(table) -> pd.DataFrame:
    """The engine's positions (``returns.POSITION_COLUMNS``, as ``cr-strat returns`` writes them or
    ``BookResult.positions``); the columns risk needs must be there."""
    frame = _frame(table, "the positions")
    _check_columns(frame, POSITION_REQUIRED, rt.POSITION_COLUMNS, "the positions")
    return frame


def _strategy_map(table, books: Iterable[str], d: date) -> dict[str, str]:
    out = {b: b for b in books}
    if table is None:
        return out
    frame = read_strategy_books(table)
    for book in out:
        rows = frame[(frame["book"] == book) & (frame["effective_from"] <= d)]
        if len(rows):
            out[book] = rows["strategy"].iloc[-1]
    return out


def _issuer_rows(table, d: date) -> dict[str, dict]:
    if table is None:
        return {}
    frame = read_issuers(table)
    live = frame[(frame["effective_from"] <= d) & (frame["effective_to"] > d)]
    return {r["issuer_id"]: r for r in live.to_dict("records")}


# ---------------------------------------------------------------- curves and ratings as known at a time


def _entry_time(value, default_time: time | None, day: date) -> tuple[Any, datetime | None, bool]:
    """(curve, public time, estimated) of a curve entry: a ``CurveEntry``'s time, else the default time on its date."""
    entry = value if isinstance(value, rt.CurveEntry) else rt.CurveEntry(value)
    if entry.public_from is not None:
        return entry.curve, entry.public_from, False
    if default_time is None:
        return entry.curve, None, False
    return entry.curve, datetime.combine(day, default_time), True


def curves_as_of(table: Mapping | None, as_of: datetime, default_time: time | None = DEFAULT_TIME,
                 ) -> list[tuple[date, Any, bool]]:
    """``[(mark date, curve, estimated time)]`` of a ``{mark date: curve or CurveEntry}`` table: the entries dated by
    ``as_of``'s day and public by ``as_of`` (an entry without a time counts from ``default_time`` on its date, an
    estimate), in date order."""
    out = []
    for day, value in sorted(((as_date(k), v) for k, v in (table or {}).items()), key=lambda kv: kv[0]):
        if day > as_of.date():
            continue
        curve, public, estimated = _entry_time(value, default_time, day)
        if public is not None and public <= as_of:
            out.append((day, curve, estimated))
    return out


def _curve_of_day(table: Mapping | None, d: date, as_of: datetime, default_time: time | None
                  ) -> tuple[Any, date | None, tuple[str, ...]]:
    """The curve of mark date ``d`` public by ``as_of``, else the latest earlier one (flagged ``stale_curve``); a
    time estimated from the default is flagged ``default_time``."""
    public = [e for e in curves_as_of(table, as_of, default_time) if e[0] <= d]
    if not public:
        return None, None, ()
    day, curve, estimated = public[-1]
    flags = (("stale_curve",) if day < d else ()) + (("default_time",) if estimated else ())
    return curve, day, flags


def _ratings_frame(ratings) -> pd.DataFrame | None:
    if ratings is None:
        return None
    frame = _frame(ratings, "the ratings")
    _check_columns(frame, ("agency", "rating", "public_from"), ("agency", "rating", "public_from", "isin",
                                                                "issuer_id"), "the ratings")
    return frame


def rating_as_known(ratings: pd.DataFrame | None, isin: str, issuer_id: str, as_of: datetime
                    ) -> tuple[str | None, int | None]:
    """(bucket, notch) of the worst of each agency's latest long-term rating public by ``as_of``: rows of the ISIN, else
    of its issuer (the engine's rule); a rating known only by its date counts from 23:59:59 that day (UNVERIFIED,
    ``corporates.public_time``). ``(None, None)`` without one."""
    if ratings is None or not len(ratings):
        return None, None
    frame = ratings
    rows = frame.iloc[0:0]
    if "isin" in frame:
        rows = frame[frame["isin"].map(_text).str.upper() == isin]
    if not len(rows) and issuer_id and "issuer_id" in frame:
        rows = frame[frame["issuer_id"].map(_text) == issuer_id]
    latest: dict[str, tuple[datetime, int]] = {}
    for rec in rows.to_dict("records"):
        when = public_time(rec.get("public_from"), "ratings public_from")
        if when is None or when > as_of:
            continue
        parsed = parse_rating(rec.get("rating"))
        if parsed.scale != "LT" or parsed.notch is None:
            continue
        agency = _text(rec.get("agency")).upper()
        if agency not in latest or when >= latest[agency][0]:
            latest[agency] = (when, parsed.notch)
    if not latest:
        return None, None
    notch = max(n for _, n in latest.values())
    return rating_bucket(notch), notch


# ---------------------------------------------------------------- the book


@dataclass(frozen=True, eq=False)
class Sensitivity:
    """One sensitivity of a position: rupees lost per bp rise of ``factor`` (positive for a long bond)."""

    factor: str
    factor_class: str
    curve: str
    tenor: float
    value: float
    method: str
    basis: str


@dataclass(frozen=True, eq=False)
class RiskPosition:
    """A bond position (settled, or pending to its settlement) as risk sees it at ``as_of``: the bond as known then
    (``instrument``), priced for ``settlement`` at ``yield_pct``, its reference curve seen from the settlement
    (``curve``, None for the yield proxy) and z-spread to it (decimal), its static data and every risk number
    (``sens``: the factor sensitivities; the scalars in rupees, sensitivities in rupees per bp, convexities in rupees
    per bp squared)."""

    book: str
    strategy: str
    isin: str
    kind: str
    state: str
    face: float
    settlement: date
    yield_pct: float
    dirty: float
    value: float
    instrument: rt.Instrument
    reference: str | None
    curve: Any
    z_spread: float
    issuer_id: str
    group_id: str
    sector: str
    rating_bucket: str | None
    rating_source: str
    seniority: str
    liquidity: str | None
    mark_source: str
    mark_age_days: int
    flags: tuple[str, ...]
    maturity: date | None = None
    tenor_bucket: str = ""
    times: np.ndarray | None = None
    amounts: np.ndarray | None = None
    discount: np.ndarray | None = None
    market_value: float = math.nan
    dv01_yield: float = math.nan
    dv01_curve: float = math.nan
    cs01: float = math.nan
    frb_index_dv01: float = math.nan
    frb_spread_dv01: float = math.nan
    next_reset: date | None = None
    convexity_yield: float = math.nan
    convexity_curve: float = math.nan
    lgd: float = math.nan
    jtd: float = math.nan
    engine_dv01: float = math.nan
    funded: float = math.nan
    macaulay: float = math.nan
    sens: tuple[Sensitivity, ...] = ()

    @property
    def key(self) -> tuple[str, str, str, date]:
        return (self.book, self.isin, self.state, self.settlement)

    @property
    def units(self) -> float:
        return self.face / 100


@dataclass(frozen=True, eq=False)
class RiskSwap:
    """An OIS swap of a book: the swap, its value on the day's OIS curve public by ``as_of`` (``curve``), the fixings
    known then, the run's calendar and its sensitivities (zero-rate key rates, central; OIS par quotes, central)."""

    book: str
    strategy: str
    trade_id: str
    swap: OisSwap
    purpose: str
    value: float
    curve: Any = None
    fixings: Mapping[date, float] | None = None
    calendar: Any = None
    flags: tuple[str, ...] = ()
    engine_dv01: float = math.nan
    dv01_curve: float = math.nan
    convexity_curve: float = math.nan
    par_rate_pct: float = math.nan
    end: date | None = None
    mark_source: str = ""
    mark_age_days: int = 0
    sens: tuple[Sensitivity, ...] = ()

    @property
    def isin(self) -> str:
        return f"OIS:{self.trade_id}"

    @property
    def key(self) -> tuple[str, str, str, date]:
        return (self.book, self.isin, "ois", self.swap.effective)

    def npv(self, curve) -> float:
        """The swap's value on ``curve`` (``ois.ois_npv`` with the fixings and calendar of the run)."""
        return ois_npv(_plain(curve), self.swap, fixings=self.fixings, calendar=self.calendar).npv


@dataclass(frozen=True, eq=False)
class RiskBook:
    """One day's book for risk: its positions and swaps as of ``as_of``, the NAV by scope (``(scope_type, scope)``:
    ``fund``, each ``book`` and ``strategy``), the reference curves used (raw, by name), the OIS curve, the run's
    calendar and settings, and notes."""

    date: date
    as_of: datetime
    positions: tuple[RiskPosition, ...]
    swaps: tuple[RiskSwap, ...]
    nav: Mapping[tuple[str, str], float]
    ois_curve: Any
    notes: tuple[str, ...]
    curves: Mapping[str, Any] = field(default_factory=dict)
    settings: RiskSettings = field(default_factory=RiskSettings)
    calendar: Any = None
    strategies: Mapping[str, str] = field(default_factory=dict)
    access: str = ""

    @property
    def items(self) -> tuple:
        """Positions then swaps, in their sorted order: the columns of every scenario P&L matrix."""
        return (*self.positions, *self.swaps)

    def scopes(self) -> list[tuple[str, str]]:
        """``("fund", "fund")``, then each book, then each strategy, sorted."""
        books = sorted({i.book for i in self.items} | {s for t, s in self.nav if t == "book"})
        strategies = sorted({self.strategies.get(b, b) for b in books})
        return [("fund", "fund"), *(("book", b) for b in books), *(("strategy", s) for s in strategies)]

    def in_scope(self, item, scope_type: str, scope: str) -> bool:
        if scope_type == "fund":
            return True
        if scope_type == "book":
            return item.book == scope
        return item.strategy == scope

    def nav_of(self, scope_type: str, scope: str) -> float:
        return float(self.nav.get((scope_type, scope), math.nan))


def _plain(curve):
    """An OIS curve without its quotes, so that ``ois_npv`` does not rebuild it to report ``dv01_par`` (its value and
    zero-rate DV01 are unchanged); any other curve as it is."""
    if isinstance(curve, OisCurve) and curve.quotes:
        return replace(curve, quotes=())
    return curve


def _bond_flows(bond, s: date) -> tuple[list[date], np.ndarray]:
    """Dates and amounts (per 100) of the flows after ``s`` on the dates the bond is priced on (a corporate bond's due
    or payment dates, ``CorporateBond.uses_due_dates``), one per date, as ``asset_swaps`` takes them."""
    if isinstance(bond, DiscountInstrument):
        bond.days_to_maturity(s)
        return [bond.maturity], np.array([100.0])
    due = bond.uses_due_dates(s) if isinstance(bond, CorporateBond) else True
    by_date: dict[date, float] = {}
    for flow in bond.cash_flows(s):
        when = flow.date if due else flow.payment_date
        by_date[when] = by_date.get(when, 0.0) + flow.amount
    dates = sorted(by_date)
    return dates, np.array([by_date[d] for d in dates], dtype=float)


def _dirty(bond, y: float, s: date) -> float:
    if isinstance(bond, DiscountInstrument):
        return bond.price(y, s, rounded=False)
    return bond.dirty_price(y, s)


def _macaulay(bond, y: float, s: date) -> float:
    if isinstance(bond, DiscountInstrument):
        return bond.days_to_maturity(s) / _YEAR
    return bond.macaulay_duration(y, s)


def _in_range(curve, t: float) -> bool:
    inside = getattr(curve, "in_data_range", None)
    if inside is not None:
        return bool(np.asarray(inside(t)).reshape(-1)[0])
    flag = getattr(curve, "extrapolated", None)
    if flag is not None:
        return not bool(np.asarray(flag(t)).reshape(-1)[0])
    return t <= float(curve.t_max) + 1e-9


def _curve_tenors(name: str, curve, settings: RiskSettings) -> tuple[float, ...]:
    """The key tenors of a curve: the OIS set up to the curve's last node (at least its first tenor), else the
    G-sec set."""
    if name == "ois":
        t_max = float(getattr(curve, "t_max", math.inf)) if curve is not None else math.inf
        tenors = tuple(t for t in settings.ois_key_tenors if t <= t_max + 1e-9)
        return tenors or settings.ois_key_tenors[:1]
    return settings.key_tenors


def _factor_class(factor: str) -> str:
    head = factor.split(":", 1)[0]
    return head


def _curve_sens(name: str, tenors: Sequence[float], values: np.ndarray, method: str, basis: str
                ) -> list[Sensitivity]:
    return [Sensitivity(f"{name}:{tenor_label(t)}", name, name, float(t), float(v), method, basis)
            for t, v in zip(tenors, values) if v != 0.0]


def _spread_factor(kind: str, rating: str | None, bucket: str, curve_name: str | None, isin: str,
                   settings: RiskSettings) -> tuple[str, str] | None:
    """The spread factor a bond's z-spread moves with, and its class: corporate bonds and CP of a corporate issuer
    on ``corp:<rating bucket>:<tenor bucket>``, CDs (bank credit) on ``cd:<tenor bucket>``, an SDL on the G-sec curve
    on ``sdl_spread:<tenor bucket>``; None for the rest (GoI securities: their residual factor is optional)."""
    if kind in ("corporate", "cp"):
        return f"corp:{rating or 'unrated'}:{bucket}", "corp"
    if kind == "cd":
        return f"cd:{bucket}", "cd"
    if kind == "sdl" and curve_name == "gsec" and settings.sdl_mode == "spread":
        return f"sdl_spread:{bucket}", "sdl_spread"
    return None


def _reference_name(kind: str, ctx: RiskContext) -> str | None:
    if kind == "frb":
        return None  # its index risk is placed on the G-sec key rates at its next reset (R1.5)
    if kind == "sdl" and ctx.risk.sdl_mode == "own_curve":
        return "sdl"
    return ctx.settings.attribution.reference_map.get(kind)


def _issuer_of(kind: str, isin: str, terms_row: Mapping | None, master_row: Mapping | None
               ) -> tuple[str, tuple[str, ...]]:
    """The issuer of a position: GoI for its securities, the state of an SDL (the master's ``state_code``, else the
    ISIN's two state digits), the corporate terms' ``issuer_id``, else the first seven characters of an Indian
    corporate ISIN (``INE`` and the issuer's code: UNVERIFIED reading, flagged), else the ISIN itself (flagged)."""
    if kind in GOI_KINDS:
        return "GOI", ()
    if kind == "sdl":
        state = _text((master_row or {}).get("state_code"))
        return f"STATE:{state or isin[2:4]}", (() if state else ("issuer_from_isin",))
    issuer = _text((terms_row or {}).get("issuer_id"))
    if issuer:
        return issuer, ()
    if isin.startswith("INE") and len(isin) == 12:
        return isin[:7], ("issuer_from_isin",)
    return isin, ("no_issuer",)


def _jtd(face: float, mv: float, lgd: float) -> float:
    """MAR22's gross jump-to-default of one position (F7): ``max(LGD N + (MV - N), 0)`` long, ``min(...)`` short."""
    raw = lgd * face + (mv - face)
    return max(raw, 0.0) if face > 0 else min(raw, 0.0)


def _krd_analytic(units: float, t: np.ndarray, pv: np.ndarray, tenors: Sequence[float]) -> np.ndarray:
    """F3: ``KRD_k = F/100 sum_i CF_i t_i w_k(t_i) D(t_i) e^{-z t_i} 1e-4`` (rupees per bp), ``pv`` the discounted
    flows per 100."""
    return units * (key_weights(t, tenors) @ (pv * t)) * 1e-4


def _value_on(curve, t: np.ndarray, cf: np.ndarray, z: float) -> float:
    """``sum_i CF_i D(t_i) e^{-z t_i}`` per 100 (F2) on ``curve`` seen from the settlement."""
    d = np.asarray(curve.discount_t(t), dtype=float).reshape(-1)
    return float(cf @ (d * np.exp(-z * t)))


def _position(rec: Mapping, inst: rt.Instrument, ctx: RiskContext, d: date, strategy: str, curves: dict,
              statics: dict) -> RiskPosition:
    """One bond row of the positions with its risk numbers (R1.1-R1.7)."""
    where = f"position {rec.get('book')} {inst.isin} ({rec.get('state')}, {rec.get('settlement_date')})"
    settings = ctx.risk
    bond = inst.bond
    face = _num(rec.get("face"), f"{where}: face")
    s = _date(rec.get("settlement_date"), f"{where}: settlement_date")
    y = _num(rec.get("yield_pct"), f"{where}: yield_pct")
    units = face / 100
    state = _text(rec.get("state")).lower()
    flags: list[str] = []
    mark_source = _text(rec.get("mark_source"))
    if mark_source in ("model", "stale"):
        flags.append("stale_mark")
    if str(rec.get("extrapolated")).strip().lower() in ("true", "1", "yes"):
        flags.append("extrapolated")
    try:
        dirty = _dirty(bond, y, s)
    except ValueError as exc:
        raise ValueError(f"{where}: the bond as known at {ctx.as_of:%Y-%m-%d %H:%M} cannot be priced ({exc})") from None
    value = _num(rec.get("value"), f"{where}: value")
    mv = value if state == "settled" else units * dirty
    kind = inst.kind
    issuers, ratings, seniority, master = (statics[k] for k in ("issuers", "ratings", "seniority", "master"))
    terms_row = statics["corp_terms"].get(inst.isin) if kind == "corporate" else None
    issuer_id, issuer_flags = _issuer_of(kind, inst.isin, terms_row, master.get(inst.isin))
    flags.extend(issuer_flags)
    issuer = issuers.get(issuer_id, {})
    group_id = _text(issuer.get("group_id"))
    if kind in CREDIT_KINDS and issuer_id not in ("GOI",) and not group_id:
        group_id = issuer_id
        flags.append("no_group")
    elif not group_id:
        group_id = issuer_id
    sector = _text(issuer.get("sector")) or _text((terms_row or {}).get("sector")).lower()
    if kind in GOI_KINDS:
        sector = sector or "sovereign"
    elif kind == "sdl":
        sector = sector or "local_government"
    rating, rating_source = None, ""
    if kind in GOI_KINDS or kind == "sdl":
        rating_source = "sovereign"
    else:
        rating, _ = rating_as_known(ratings, inst.isin, issuer_id, ctx.as_of)
        if rating is not None:
            rating_source = "ratings"
        elif inst.rating_bucket:
            rating, rating_source = inst.rating_bucket, "static"
            flags.append("static_rating")
        else:
            rating_source = "unrated"
    tier = _text(getattr(bond, "tier", "")).lower()
    if kind in GOI_KINDS or (kind == "sdl" and not settings.jtd_states):
        rank = ""  # no default risk modelled for GoI securities and SDLs (R1.7)
    elif tier in ("at1", "tier2"):
        rank = "subordinated"
    else:
        rank = seniority.get(inst.isin) or _text(issuer.get("seniority")) or "senior"
    maturity = inst.maturity
    bucket = tenor_bucket(maturity, d)
    # the yield DV01 (the engine's) and the convexity at the yield
    if isinstance(bond, GoiFloatingRateBond):
        idx = index_dv01(bond, s, 0.0, index_pct=y)
        spr = spread_dv01(bond, s, 0.0, index_pct=y)
        reset = next_reset(bond, s)
        dv01_yield = units * idx
        frb_index, frb_spread = units * idx, units * spr
        bucket = tenor_bucket(reset, d)
    else:
        dv01_yield = units * bond.dv01(y, s)
        frb_index = frb_spread = math.nan
        reset = None
    conv_yield = units * bond.convexity(y, s) * dirty * 1e-8
    engine_dv01 = _num(rec.get("dv01"), f"{where}: dv01", blank=math.nan)
    if math.isfinite(engine_dv01) and abs(engine_dv01 - dv01_yield) > 1e-6 * (abs(engine_dv01) + 1.0):
        flags.append("dv01_differs")  # the positions were valued on other terms or another day
    sens: list[Sensitivity] = []
    name = _reference_name(kind, ctx)
    curve_s, z, t, cf, disc = None, math.nan, None, None, None
    dv01_curve = cs01 = conv_curve = math.nan
    if isinstance(bond, GoiFloatingRateBond):
        t_reset = max((reset - s).days / _YEAR, 0.0)
        if "tbill182" in statics["table_factors"] and statics["frb_index"].get(inst.isin, "").startswith("tbill182"):
            # a T-bill FRB's index risk on the 182-day T-bill factor when its history is given (U)
            sens.append(Sensitivity("tbill182", "tbill182", "", math.nan, frb_index, "frb_index", "yield"))
        else:
            w = key_weights([t_reset], settings.key_tenors)[:, 0]
            sens.extend(_curve_sens("gsec", settings.key_tenors, frb_index * w, "frb_index", "zero"))
        sens.append(Sensitivity(f"frb_dm:{inst.isin}", "frb_dm", "", math.nan, frb_spread, "frb_spread", "spread"))
        dv01_curve = frb_index
    else:
        raw, flags_c = curves.get(name, (None, ())) if name else (None, ())
        if raw is not None and as_date(raw.valuation_date) <= s:
            try:
                curve_s = rt.seen_from(raw, s)
            except ValueError:
                curve_s = None
        if curve_s is not None:
            dates, cf = _bond_flows(bond, s)
            t = np.array([(x - s).days for x in dates], dtype=float) / _YEAR
            disc = np.asarray(curve_s.discount_t(t), dtype=float).reshape(-1)
            if not (np.all(np.isfinite(disc)) and np.all(disc > 0)):
                curve_s, t, cf, disc = None, None, None, None
                flags.append("beyond_curve")
            else:
                try:
                    z = _z_spread(dates, cf, s, dirty, curve_s)
                except (ValueError, RuntimeError):
                    curve_s, t, cf, disc = None, None, None, None
                    flags.append("no_z_spread")
            if curve_s is not None:
                flags.extend(flags_c)
                if not _in_range(curve_s, float(t[-1])):
                    flags.append("extrapolated")
        if curve_s is None:
            flags.append("no_curve")
            mac = _macaulay(bond, y, s)
            tenors = _curve_tenors(name or "gsec", None, settings)
            w = key_weights([mac], tenors)[:, 0]
            sens.extend(_curve_sens(name or "gsec", tenors, dv01_yield * w, "yield_proxy", "yield"))
            dv01_curve = dv01_yield
        else:
            pv = cf * disc * np.exp(-z * t)
            tenors = _curve_tenors(name, curve_s, settings)
            krd = _krd_analytic(units, t, pv, tenors)
            sens.extend(_curve_sens(name, tenors, krd, "analytic", "zero"))
            dv01_curve = float(krd.sum())
            h = settings.bump_bp / 2e4
            cs01 = units * (_value_on(curve_s, t, cf, z - h) - _value_on(curve_s, t, cf, z + h)) / settings.bump_bp
            conv_curve = units * float((pv * t * t).sum()) * 1e-8
            spread = _spread_factor(kind, rating, bucket, name, inst.isin, settings)
            if spread is not None:
                sens.append(Sensitivity(spread[0], spread[1], "", math.nan, cs01, "central", "spread"))
            if kind not in CREDIT_KINDS:
                cs01 = math.nan  # GoI securities and SDLs carry no credit CS01 (their spread factor is above)
        if settings.residual is not None and kind not in CREDIT_KINDS:
            sens.append(Sensitivity(f"resid:{inst.isin}", "resid", "", math.nan, dv01_yield, "yield", "yield"))
    lgd = jtd = math.nan
    if rank and (kind in CREDIT_KINDS or (kind == "sdl" and settings.jtd_states)):
        lgd = LGD[rank]
        jtd = _jtd(face, mv, lgd)
    return RiskPosition(
        book=_text(rec.get("book")), strategy=strategy, isin=inst.isin, kind=kind, state=state, face=face,
        settlement=s, yield_pct=y, dirty=dirty, value=value, instrument=inst, reference=name if curve_s is not None
        else None, curve=curve_s, z_spread=z, issuer_id=issuer_id, group_id=group_id, sector=sector,
        rating_bucket=rating, rating_source=rating_source, seniority=rank,
        liquidity=_text(rec.get("liquidity")) or None, mark_source=mark_source,
        mark_age_days=int(_num(rec.get("mark_age_days"), f"{where}: mark_age_days", blank=0)),
        flags=tuple(dict.fromkeys(flags)), maturity=maturity, tenor_bucket=bucket, times=t, amounts=cf, discount=disc,
        market_value=mv, dv01_yield=dv01_yield, dv01_curve=dv01_curve, cs01=cs01, frb_index_dv01=frb_index,
        frb_spread_dv01=frb_spread, next_reset=reset, convexity_yield=conv_yield, convexity_curve=conv_curve,
        lgd=lgd, jtd=jtd, engine_dv01=engine_dv01, funded=_num(rec.get("funded"), f"{where}: funded",
                                                                   blank=math.nan),
        macaulay=_macaulay(bond, y, s) if not isinstance(bond, GoiFloatingRateBond) else math.nan,
        sens=tuple(sens))


def _corporate_rows(ctx: RiskContext) -> dict[str, dict]:
    """The corporate terms by ISIN (``corporates.read_corporate_terms``: issuer, sector, tier, static rating)."""
    if ctx.market.corporate_terms is None:
        return {}
    from cr_strat.corporates import read_corporate_terms
    return {r["isin"].upper(): r for r in read_corporate_terms(ctx.market.corporate_terms).to_dict("records")}


def _frb_indices(ctx: RiskContext, isins: Iterable[str]) -> dict[str, str]:
    """The index of each GoI FRB of ``isins`` (``floating.load_frb_terms``: ``tbill182_way_avg3``, ...)."""
    wanted = set(isins)
    if not wanted:
        return {}
    from cr_strat.floating import load_frb_terms
    table = load_frb_terms() if ctx.market.frb_terms is None else load_frb_terms(ctx.market.frb_terms)
    return {str(r["isin"]).upper(): _text(r.get("index")) for r in table.to_dict("records")
            if str(r["isin"]).upper() in wanted}


def swap_krd(swap: OisSwap, curve, tenors: Sequence[float], *, fixings=None, calendar=None, bump_bp: float = 1.0
             ) -> dict[str, float]:
    """Zero-rate key-rate DV01s of an OIS (F4, rupees per bp, central differences of half a bump each way):
    ``NPV(C (+) -h w_k) - NPV(C (+) +h w_k)`` with ``(+)`` a ``returns.ShiftedCurve`` of the triangles, times from the
    curve's valuation date (the trade date)."""
    base = _plain(curve)
    h = bump_bp / 2e4
    out = {}
    for k, t in enumerate(tenors):
        def shape(x, k=k):
            return key_weights(np.asarray(x, dtype=float).reshape(-1), tenors)[k].reshape(np.shape(x))
        up = rt.ShiftedCurve(base, lambda x, sh=shape: h * sh(x))
        dn = rt.ShiftedCurve(base, lambda x, sh=shape: -h * sh(x))
        out[tenor_label(t)] = (ois_npv(dn, swap, fixings=fixings, calendar=calendar).npv
                               - ois_npv(up, swap, fixings=fixings, calendar=calendar).npv) / bump_bp
    return out


def swap_par_krd(swap: OisSwap, curve, *, fixings=None, calendar=None, bump_bp: float = 1.0,
                 differencing: str = "central", cache: dict | None = None) -> dict[str, float]:
    """Par-quote DV01s of an OIS by quote (``OisCurve.bumped``, the market's swap DV01 by tenor): central
    ``NPV(C.bumped(-h, [q])) - NPV(C.bumped(+h, [q]))``, or ``differencing="up"`` for the one-sided ``NPV(C) -
    NPV(C.bumped(+1, [q]))`` that ``ois_npv``'s ``dv01_par`` uses. Empty for a curve without quotes (rolled, rebased
    or held)."""
    if not isinstance(curve, OisCurve) or not curve.quotes or curve.overnight_fixing_pct is None:
        return {}
    _choice(differencing, ("central", "up"), "differencing")
    out = {}
    base = ois_npv(_plain(curve), swap, fixings=fixings, calendar=calendar).npv
    for label, months, _ in sorted(curve.quotes, key=lambda q: q[1]):
        if differencing == "central":
            h = bump_bp / 2
            dn, up = _bumped(curve, -h, label, cache), _bumped(curve, h, label, cache)
            out[label] = (ois_npv(dn, swap, fixings=fixings, calendar=calendar).npv
                          - ois_npv(up, swap, fixings=fixings, calendar=calendar).npv) / bump_bp
        else:
            up = _bumped(curve, bump_bp, label, cache)
            out[label] = (base - ois_npv(up, swap, fixings=fixings, calendar=calendar).npv) / bump_bp
    return out


def _bumped(curve: OisCurve, bp: float, label: str, cache: dict | None):
    """``curve`` rebuilt with quote ``label`` bumped ``bp`` (kept in ``cache``, one per book, by curve and bump)."""
    if cache is None:
        return _plain(curve.bumped(bp, tenors=[label]))
    key = (id(curve), bp, label)
    if key not in cache:
        cache[key] = (curve, _plain(curve.bumped(bp, tenors=[label])))  # the curve kept alive: its id stays unique
    return cache[key][1]


def zero_krd(bond, settlement, curve, z: float, tenors: Sequence[float]) -> dict[str, float]:
    """Analytic zero-rate key-rate DV01s per 100 face of a bond with fixed flows (F3) on ``curve`` seen from
    ``settlement`` at z-spread ``z`` (decimal): ``sum_i CF_i t_i w_k(t_i) D(t_i) e^{-z t_i} 1e-4``."""
    s = as_date(settlement)
    curve_s = rt.seen_from(curve, s) if as_date(curve.valuation_date) != s else curve
    dates, cf = _bond_flows(bond, s)
    t = np.array([(x - s).days for x in dates], dtype=float) / _YEAR
    pv = cf * np.asarray(curve_s.discount_t(t), dtype=float).reshape(-1) * np.exp(-z * t)
    krd = _krd_analytic(1.0, t, pv, tenors)  # units = 1: 100 face
    return {tenor_label(k): float(v) for k, v in zip(tenors, krd)}


def _swap(rec: Mapping, terms: Mapping[str, dict], ctx: RiskContext, d: date, strategy: str, curve, flags_c,
          fixings, cal, cache: dict) -> RiskSwap:
    isin = _text(rec.get("isin"))
    tid = isin.split(":", 1)[1] if ":" in isin else isin
    where = f"OIS row {isin} of book {rec.get('book')}"
    row = terms.get(tid)
    if row is None:
        raise ValueError(f"{where}: no swap terms for trade {tid} (give the OIS trades file, SWAP_TRADE_COLUMNS, or "
                         "a back-test's Snapshot.swaps())")
    months = int(_num(row.get("months"), f"{where}: months"))
    notional = abs(_num(rec.get("face"), f"{where}: face"))
    pay_fixed = _num(rec.get("face"), f"{where}: face") < 0
    effective = _date(rec.get("settlement_date"), f"{where}: settlement_date")
    rate = _num(rec.get("clean_mark"), f"{where}: clean_mark (the swap's fixed rate)")
    given_pay = row.get("pay_fixed")
    if not _blank(given_pay):
        text = str(given_pay).strip().lower()
        if (text in ("true", "1", "yes", "y", "t")) != pay_fixed:
            raise ValueError(f"{where}: the positions say {'pay' if pay_fixed else 'receive'} fixed, the swap terms "
                             f"the other side")
    swap = OisSwap(effective=effective, months=months, fixed_rate_pct=rate, notional=notional, pay_fixed=pay_fixed)
    flags = list(flags_c)
    if curve is None:
        raise ValueError(f"{where}: no OIS curve of {d} or before public by {ctx.as_of:%Y-%m-%d %H:%M} "
                         "(MarketData.ois_curves)")
    period_end = ois_schedule(effective, months, swap.conventions, cal)[-1].end
    val = ois_npv(_plain(curve), swap, fixings=fixings, calendar=cal)
    value = _num(rec.get("value"), f"{where}: value")
    if abs(val.npv - value) > 1e-6 * notional:
        flags.append("value_differs")
    tenors = _curve_tenors("ois", curve, ctx.risk)
    zk = swap_krd(swap, curve, tenors, fixings=fixings, calendar=cal, bump_bp=ctx.risk.bump_bp)
    sens = [Sensitivity(f"ois:{lab}", "ois", "ois", float(t), v, "central", "zero")
            for (lab, v), t in zip(zk.items(), tenors) if v != 0.0]
    par = swap_par_krd(swap, curve, fixings=fixings, calendar=cal, bump_bp=ctx.risk.bump_bp, cache=cache)
    if not par:
        flags.append("no_par_quotes")
    months_of = {label: m for label, m, _ in getattr(curve, "quotes", ())}
    sens.extend(Sensitivity(f"ois_par:{lab}", "ois_par", "ois", months_of.get(lab, math.nan) / 12, v, "par_quote",
                            "par") for lab, v in par.items() if v != 0.0)
    h = ctx.risk.bump_bp / 1e4
    base = _plain(curve)
    up = ois_npv(rt.ShiftedCurve(base, lambda x: np.full_like(np.asarray(x, dtype=float), h)), swap, fixings=fixings,
                 calendar=cal).npv
    dn = ois_npv(rt.ShiftedCurve(base, lambda x: np.full_like(np.asarray(x, dtype=float), -h)), swap,
                 fixings=fixings, calendar=cal).npv
    convexity = (up + dn - 2 * val.npv) / (ctx.risk.bump_bp ** 2)
    if period_end <= d:
        flags.append("ended")
    return RiskSwap(book=_text(rec.get("book")), strategy=strategy, trade_id=tid, swap=swap,
                    purpose=_text(row.get("purpose")).lower() or "hedge", value=val.npv, curve=curve,
                    fixings=fixings, calendar=cal, flags=tuple(dict.fromkeys(flags)),
                    engine_dv01=_num(rec.get("dv01"), f"{where}: dv01", blank=val.dv01),
                    dv01_curve=float(sum(zk.values())), convexity_curve=convexity, par_rate_pct=val.fair_rate_pct,
                    end=period_end, mark_source=_text(rec.get("mark_source")),
                    mark_age_days=int(_num(rec.get("mark_age_days"), f"{where}: mark_age_days", blank=0)),
                    sens=tuple(sens))


def _swap_terms(table) -> dict[str, dict]:
    if table is None:
        return {}
    frame = _frame(table, "the OIS trades")
    if "trade_id" not in frame:
        raise ValueError("the OIS trades need a trade_id column (returns.SWAP_TRADE_COLUMNS or SWAP_STATE_COLUMNS)")
    return {_text(r["trade_id"]): r for _, r in _lines(frame)}


def _fixings_as_of(ctx: RiskContext) -> dict[date, float]:
    from cr_strat.funding import fbil_published_at
    times = ctx.market.fixing_times
    out = {}
    for day, rate in sorted((as_date(k), v) for k, v in (ctx.market.fixings or {}).items()):
        if _blank(rate):
            continue
        public = times.get(day) if times is not None else fbil_published_at(day)
        if public is not None and public <= ctx.as_of:
            out[day] = float(rate)
    return out


def _nav_map(nav, d: date, books: Iterable[str], strategies: Mapping[str, str]) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    if nav is None:
        return out
    frame = _frame(nav, "the NAV table")
    _check_columns(frame, ("date", "book", "nav"), rt.NAV_COLUMNS, "the NAV table")
    day = frame[[_date(v, "the NAV table's date") == d for v in frame["date"]]] if len(frame) else frame
    by_book = {_text(r["book"]): _num(r["nav"], f"the NAV of {r['book']} on {d}") for r in day.to_dict("records")}
    for book, value in by_book.items():
        if book != "fund":
            out[("book", book)] = value
    book_values = {b: v for b, v in by_book.items() if b != "fund"}
    out[("fund", "fund")] = by_book.get("fund", _fsum(book_values.values()) if book_values else math.nan)
    for book in set(books) | set(book_values):
        strategy = strategies.get(book, book)
        if book in book_values:
            out[("strategy", strategy)] = out.get(("strategy", strategy), 0.0) + book_values[book]
    return out


def risk_book(positions, ctx: RiskContext, *, day=None) -> RiskBook:
    """The book of one day of a positions table (``returns.POSITION_COLUMNS``: the rows of ``day``, or the only date
    there) as risk sees it at ``ctx.as_of``: each bond as known then (``returns.instruments_as_known``), priced for its
    row's settlement at its row's yield, on its reference curve of the day public by then (else the latest earlier one,
    flagged ``stale_curve``; none: the yield proxy, flagged ``no_curve``), with every sensitivity of R1; each OIS row
    from its swap terms (``ctx.swaps``) on the day's OIS curve. Refuses a bond without terms, an OIS row without swap
    terms, duplicate rows and a day the table does not hold."""
    frame = read_positions(positions)
    dates = sorted({_date(v, "the positions' date") for v in frame["date"]}) if len(frame) else []
    if day is None:
        if len(dates) != 1:
            raise ValueError(f"the positions hold {len(dates)} dates; name the day (dates {dates[:3]}...)")
        d = dates[0]
    else:
        d = as_date(day)
    rows = [rec for _, rec in _lines(frame) if _date(rec["date"], "the positions' date") == d]
    if not rows:
        raise ValueError(f"no position on {d} in the positions table")
    if ctx.as_of.date() < d:
        raise ValueError(f"as_of {ctx.as_of:%Y-%m-%d %H:%M} is before the positions' day {d}")
    seen = set()
    for rec in rows:
        key = (_text(rec.get("book")), _text(rec.get("isin")).upper(), _text(rec.get("state")).lower(),
               _text(rec.get("settlement_date")))
        if key in seen:
            raise ValueError(f"the positions list {key[1]} of book {key[0]} ({key[2]}, {key[3]}) twice on {d}")
        seen.add(key)
    books = sorted({_text(r.get("book")) for r in rows})
    strategies = _strategy_map(ctx.strategy_books, books, d)
    bond_rows = [r for r in rows if _text(r.get("state")).lower() in ("settled", "pending")]
    swap_rows = [r for r in rows if _text(r.get("state")).lower() == "ois"]
    other = [r for r in rows if r not in bond_rows and r not in swap_rows]
    if other:
        raise ValueError(f"positions of state {_text(other[0].get('state'))!r} on {d}: risk knows settled, pending "
                         "and ois")
    isins = sorted({_text(r.get("isin")).upper() for r in bond_rows})
    insts = rt.instruments_as_known(ctx.market, ctx.settings, isins, ctx.as_of, terms=ctx.terms) if isins else {}
    return _assemble(d, ctx, bond_rows, swap_rows, insts, _swap_terms(ctx.swaps), strategies,
                     _nav_map(ctx.nav, d, books, strategies))


def book_from_snapshot(snapshot: rt.Snapshot, ctx: RiskContext) -> RiskBook:
    """The book a back-test's ``Snapshot`` holds at its decision (positions, ``swaps()``, ``instrument()``, the NAV by
    book), as of the snapshot's ``as_of`` (``ctx.as_of`` is replaced by it); the curves and fixings are the market
    data's cut at that time, as the snapshot's own accessors cut them."""
    ctx = replace(ctx, as_of=snapshot.as_of)
    d = snapshot.day
    frame = snapshot.positions
    rows = [rec for rec in frame.to_dict("records")] if len(frame) else []
    books = sorted({_text(r.get("book")) for r in rows} | set(snapshot.nav))
    strategies = _strategy_map(ctx.strategy_books, books, d)
    bond_rows = [r for r in rows if _text(r.get("state")).lower() in ("settled", "pending")]
    swap_rows = [r for r in rows if _text(r.get("state")).lower() == "ois"]
    insts = {isin: snapshot.instrument(isin) for isin in sorted({_text(r.get("isin")).upper() for r in bond_rows})}
    terms = {_text(r["trade_id"]): r for r in snapshot.swaps().to_dict("records")}
    nav = {("book", b): float(v) for b, v in snapshot.nav.items()}
    nav[("fund", "fund")] = _fsum(snapshot.nav.values()) if snapshot.nav else math.nan
    for b, v in snapshot.nav.items():
        key = ("strategy", strategies.get(b, b))
        nav[key] = nav.get(key, 0.0) + float(v)
    return _assemble(d, ctx, bond_rows, swap_rows, insts, terms, strategies, nav)


def _master_rows(ctx: RiskContext) -> dict[str, dict]:
    if ctx.market.master is None:
        return {}
    frame = _frame(ctx.market.master, "the security master")
    if "isin" not in frame:
        return {}
    return {_text(r.get("isin")).upper(): r for _, r in _lines(frame) if _text(r.get("isin"))}


def _assemble(d: date, ctx: RiskContext, bond_rows: list, swap_rows: list, insts: Mapping, swap_terms: Mapping,
              strategies: Mapping[str, str], nav: Mapping) -> RiskBook:
    settings = ctx.risk
    default = ctx.default_time
    curves: dict[str, Any] = {}
    used: dict[str, Any] = {}
    names = {_reference_name(inst.kind, ctx) for inst in insts.values()} - {None}
    for name in sorted(names):
        table = ctx.market.ois_curves if name == "ois" else (ctx.market.curves or {}).get(name)
        curve, _, flags = _curve_of_day(table, d, ctx.as_of, default)
        curves[name] = (curve, flags)
        if curve is not None:
            used[name] = curve
    table_factors = set()
    if ctx.factor_history is not None:
        frame = _frame(ctx.factor_history, "the factor history")
        if "factor" in frame:
            table_factors = {_text(f) for f in frame["factor"]}
    statics = {"table_factors": table_factors, "issuers": _issuer_rows(ctx.issuers, d),
               "ratings": _ratings_frame(ctx.market.ratings),
               "seniority": {} if ctx.seniority is None else dict(zip(*(read_seniority(ctx.seniority)[c]
                                                                        for c in ("isin", "seniority")))),
               "master": _master_rows(ctx), "corp_terms": _corporate_rows(ctx),
               "frb_index": _frb_indices(ctx, [i for i, inst in insts.items() if inst.kind == "frb"])}
    positions = []
    for rec in bond_rows:
        isin = _text(rec.get("isin")).upper()
        positions.append(_position(rec, insts[isin], ctx, d, strategies.get(_text(rec.get("book")),
                                                                          _text(rec.get("book"))),
                                   curves, statics))
    cal = ctx.calendar
    ois_curve, ois_day, ois_flags = _curve_of_day(ctx.market.ois_curves, d, ctx.as_of, default)
    if ois_curve is not None and ois_day is not None and ois_day < d:
        ois_curve = rt.rolled(ois_curve, d)  # a held curve rolled to the day at constant zero rates, as the engine
    if ois_curve is not None:
        used.setdefault("ois", ois_curve)
    fixings = _fixings_as_of(ctx) if swap_rows else {}
    cache: dict = {}
    swaps = [_swap(rec, swap_terms, ctx, d, strategies.get(_text(rec.get("book")), _text(rec.get("book"))),
                   ois_curve, ois_flags, fixings, cal, cache) for rec in swap_rows]
    positions.sort(key=lambda p: (p.book, p.isin, p.state, p.settlement))
    swaps.sort(key=lambda s: (s.book, s.isin))
    notes = []
    for name, (curve, flags) in sorted((k, v) for k, v in curves.items() if not k.startswith("_")):
        if curve is None:
            notes.append(f"no {name} curve of {d} or before public by {ctx.as_of:%Y-%m-%d %H:%M}: its positions are "
                         "risked on their own yields (no_curve)")
        elif flags:
            notes.append(f"the {name} curve used is flagged {', '.join(flags)}")
    return RiskBook(date=d, as_of=ctx.as_of, positions=tuple(positions), swaps=tuple(swaps), nav=dict(nav),
                    ois_curve=ois_curve, notes=tuple(notes), curves=used, settings=settings, calendar=cal,
                    strategies=dict(strategies), access=ctx.settings.access.name)


# ---------------------------------------------------------------- the sensitivity tables (R1.8)


def _iso(when) -> str:
    return "" if when is None else when.isoformat(timespec="minutes") if isinstance(when, datetime) else str(when)


def position_risk(book: RiskBook) -> pd.DataFrame:
    """One row per position and swap (``POSITION_RISK_COLUMNS``), sorted by book, ISIN, state and settlement."""
    rows = []
    for p in book.positions:
        rows.append({"date": book.date, "as_of": _iso(book.as_of), "book": p.book, "strategy": p.strategy,
                     "isin": p.isin, "kind": p.kind, "state": p.state, "face": p.face, "settlement_date": p.settlement,
                     "yield_pct": p.yield_pct, "dirty_price": p.dirty, "value": p.value,
                     "reference_curve": p.reference or "", "z_spread_bp": p.z_spread * 1e4,
                     "dv01_yield": p.dv01_yield, "dv01_curve": p.dv01_curve, "cs01": p.cs01,
                     "frb_index_dv01": p.frb_index_dv01, "frb_spread_dv01": p.frb_spread_dv01,
                     "next_reset": p.next_reset, "convexity_yield": p.convexity_yield,
                     "convexity_curve": p.convexity_curve, "maturity_date": p.maturity, "tenor_bucket": p.tenor_bucket,
                     "issuer_id": p.issuer_id, "group_id": p.group_id, "sector": p.sector,
                     "rating_bucket": p.rating_bucket or "", "rating_source": p.rating_source,
                     "seniority": p.seniority, "lgd": p.lgd, "jtd": p.jtd, "liquidity": p.liquidity or "",
                     "mark_source": p.mark_source, "mark_age_days": p.mark_age_days, "flags": _flags(p.flags)})
    for s in book.swaps:
        rows.append({"date": book.date, "as_of": _iso(book.as_of), "book": s.book, "strategy": s.strategy,
                     "isin": s.isin, "kind": "ois", "state": "ois",
                     "face": -s.swap.notional if s.swap.pay_fixed else s.swap.notional,
                     "settlement_date": s.swap.effective, "yield_pct": s.par_rate_pct, "dirty_price": math.nan,
                     "value": s.value, "reference_curve": "ois", "z_spread_bp": math.nan,
                     "dv01_yield": s.engine_dv01, "dv01_curve": s.dv01_curve, "cs01": math.nan,
                     "frb_index_dv01": math.nan, "frb_spread_dv01": math.nan, "next_reset": None,
                     "convexity_yield": math.nan, "convexity_curve": s.convexity_curve, "maturity_date": s.end,
                     "tenor_bucket": tenor_bucket(s.end, book.date), "issuer_id": "", "group_id": "",
                     "sector": "", "rating_bucket": "", "rating_source": "", "seniority": "", "lgd": math.nan,
                     "jtd": math.nan, "liquidity": "", "mark_source": s.mark_source,
                     "mark_age_days": s.mark_age_days, "flags": _flags(s.flags)})
    frame = pd.DataFrame(rows, columns=list(POSITION_RISK_COLUMNS))
    return frame.sort_values(["date", "book", "isin", "state", "settlement_date"], kind="mergesort").reset_index(
        drop=True)


def sensitivities(book: RiskBook) -> pd.DataFrame:
    """The long table of position by factor (``SENSITIVITY_COLUMNS``): every nonzero sensitivity, rupees per bp;
    ``method`` analytic, central, par_quote, yield_proxy, frb_index, frb_spread or yield; ``basis`` zero (key rates
    of the zero curve), par (OIS par quotes, reported, not a VaR factor), spread or yield."""
    rows = []
    for item in book.items:
        kind = "ois" if isinstance(item, RiskSwap) else item.kind
        for sn in item.sens:
            rows.append({"date": book.date, "as_of": _iso(book.as_of), "book": item.book, "strategy": item.strategy,
                         "isin": item.isin, "kind": kind, "factor": sn.factor, "factor_class": sn.factor_class,
                         "curve": sn.curve, "tenor": sn.tenor, "sensitivity": sn.value, "method": sn.method,
                         "basis": sn.basis, "flags": _flags(item.flags)})
    frame = pd.DataFrame(rows, columns=list(SENSITIVITY_COLUMNS))
    order = frame.assign(_t=frame["tenor"].fillna(-1.0))
    order = order.sort_values(["date", "book", "isin", "factor_class", "_t", "factor"], kind="mergesort")
    return frame.loc[order.index].reset_index(drop=True)


def _years(value: float, nav: float) -> float:
    return value / (nav * 1e-4) if math.isfinite(nav) and nav > 0 else math.nan


def _pct(value: float, nav: float) -> float:
    return value / nav * 100 if math.isfinite(nav) and nav > 0 else math.nan


def key_rate_table(book: RiskBook) -> pd.DataFrame:
    """Key-rate DV01s by scope, curve and tenor (``KEY_RATE_COLUMNS``): the zero-rate sensitivities of every position
    and swap summed (an FRB's index DV01 at its next reset among them; OIS par-quote DV01s as curve ``ois_par``), with
    ``krd_years`` = the KRD over NAV x 1e-4 (duration-equivalent years)."""
    rows = []
    for scope_type, scope in book.scopes():
        sums: dict[tuple[str, float], list[float]] = {}
        for item in book.items:
            if not book.in_scope(item, scope_type, scope):
                continue
            for sn in item.sens:
                curve = "ois_par" if sn.basis == "par" else sn.curve
                if curve and math.isfinite(sn.tenor):
                    sums.setdefault((curve, sn.tenor), []).append(sn.value)
        nav = book.nav_of(scope_type, scope)
        for (curve, tenor), values in sorted(sums.items(), key=lambda kv: (kv[0][0], kv[0][1])):
            krd = _fsum(values)
            rows.append({"date": book.date, "as_of": _iso(book.as_of), "scope_type": scope_type, "scope": scope,
                         "curve": curve, "tenor": tenor, "krd": krd, "krd_years": _years(krd, nav)})
    return pd.DataFrame(rows, columns=list(KEY_RATE_COLUMNS))


def _credit_items(book: RiskBook, scope_type: str, scope: str) -> list[RiskPosition]:
    return [p for p in book.positions if book.in_scope(p, scope_type, scope)
            and (p.kind in CREDIT_KINDS or (p.kind == "sdl" and book.settings.jtd_states))]


def credit_table(book: RiskBook) -> pd.DataFrame:
    """CS01, market value, share of NAV and JTD of the credit positions (corporate bonds, CP, CD) by scope and
    dimension (``CREDIT_COLUMNS``: issuer, group, sector, rating bucket, tenor bucket, rating x tenor)."""
    rows = []
    for scope_type, scope in book.scopes():
        items = _credit_items(book, scope_type, scope)
        nav = book.nav_of(scope_type, scope)
        for dimension in CREDIT_DIMENSIONS:
            groups: dict[str, list[RiskPosition]] = {}
            for p in items:
                key = {"issuer": p.issuer_id, "group": p.group_id, "sector": p.sector or "unknown",
                       "rating_bucket": p.rating_bucket or "unrated", "tenor_bucket": p.tenor_bucket,
                       "rating_tenor": f"{p.rating_bucket or 'unrated'}:{p.tenor_bucket}"}[dimension]
                groups.setdefault(key, []).append(p)
            for key, members in sorted(groups.items()):
                split = [p.cs01 for p in members if math.isfinite(p.cs01)]
                cs01 = _fsum(split) if split else math.nan  # NaN: no member's CS01 split out (no curve)
                mv = _fsum(p.market_value for p in members)
                jtd = _fsum(p.jtd for p in members if math.isfinite(p.jtd))
                rows.append({"date": book.date, "as_of": _iso(book.as_of), "scope_type": scope_type, "scope": scope,
                             "dimension": dimension, "key": key, "cs01": cs01, "cs01_years": _years(cs01, nav),
                             "market_value": mv, "pct_nav": _pct(mv, nav), "jtd": jtd, "jtd_pct_nav": _pct(jtd, nav),
                             "positions": len(members),
                             "flags": _flags(*(f for p in members for f in p.flags
                                               if f in ("static_rating", "no_group", "no_curve", "issuer_from_isin",
                                                        "no_issuer", "stale_mark")))})
    return pd.DataFrame(rows, columns=list(CREDIT_COLUMNS))


def jtd_table(book: RiskBook) -> pd.DataFrame:
    """Gross jump-to-default by issuer and scope (``JTD_COLUMNS``): the sum of the issuer's positions' JTD (no netting
    across issuers, nor between a long and a short of one issuer), with the face-weighted LGD."""
    rows = []
    for scope_type, scope in book.scopes():
        nav = book.nav_of(scope_type, scope)
        groups: dict[str, list[RiskPosition]] = {}
        for p in _credit_items(book, scope_type, scope):
            groups.setdefault(p.issuer_id, []).append(p)
        for issuer, members in sorted(groups.items()):
            face = _fsum(p.face for p in members)
            gross = _fsum(abs(p.face) for p in members)
            jtd = _fsum(p.jtd for p in members)
            rows.append({"date": book.date, "as_of": _iso(book.as_of), "scope_type": scope_type, "scope": scope,
                         "issuer_id": issuer, "group_id": members[0].group_id, "face": face,
                         "market_value": _fsum(p.market_value for p in members),
                         "lgd": _fsum(abs(p.face) * p.lgd for p in members) / gross if gross else math.nan,
                         "jtd": jtd, "jtd_pct_nav": _pct(jtd, nav)})
    return pd.DataFrame(rows, columns=list(JTD_COLUMNS))


def summary_table(book: RiskBook) -> pd.DataFrame:
    """Every position's risk summed by scope and dimension (``SUMMARY_COLUMNS``: the total, and by kind, tenor bucket,
    issuer, group, sector and rating bucket): face, market value, yield and curve DV01, CS01, FRB DV01s, convexities
    and JTD; ``dv01_years`` is the yield DV01 over NAV x 1e-4. Swaps count in the total, the kind ``ois`` and their
    tenor bucket (their market value is their NPV)."""
    rows = []
    for scope_type, scope in book.scopes():
        nav = book.nav_of(scope_type, scope)
        items = [i for i in book.items if book.in_scope(i, scope_type, scope)]
        for dimension in SUMMARY_DIMENSIONS:
            groups: dict[str, list] = {}
            for item in items:
                if isinstance(item, RiskSwap):
                    if dimension not in ("total", "kind", "tenor_bucket"):
                        continue
                    key = {"total": "total", "kind": "ois", "tenor_bucket": tenor_bucket(item.end, book.date)}[
                        dimension]
                else:
                    key = {"total": "total", "kind": item.kind, "tenor_bucket": item.tenor_bucket,
                           "issuer": item.issuer_id, "group": item.group_id, "sector": item.sector or "unknown",
                           "rating_bucket": item.rating_bucket or ("sovereign" if item.rating_source == "sovereign"
                                                                   else "unrated")}[dimension]
                groups.setdefault(key, []).append(item)
            for key, members in sorted(groups.items()):
                def total(attr, members=members):
                    return _fsum(getattr(m, attr) for m in members
                                 if hasattr(m, attr) and math.isfinite(getattr(m, attr)))
                face = _fsum((m.face if isinstance(m, RiskPosition) else
                              (-m.swap.notional if m.swap.pay_fixed else m.swap.notional)) for m in members)
                mv = _fsum((m.market_value if isinstance(m, RiskPosition) else m.value) for m in members)
                dv01 = _fsum((m.dv01_yield if isinstance(m, RiskPosition) else m.engine_dv01) for m in members)
                rows.append({"date": book.date, "as_of": _iso(book.as_of), "scope_type": scope_type, "scope": scope,
                             "dimension": dimension, "key": key, "positions": len(members), "face": face,
                             "market_value": mv, "pct_nav": _pct(mv, nav), "dv01_yield": dv01,
                             "dv01_years": _years(dv01, nav), "dv01_curve": total("dv01_curve"), "cs01": total("cs01"),
                             "frb_index_dv01": total("frb_index_dv01"), "frb_spread_dv01": total("frb_spread_dv01"),
                             "convexity_yield": total("convexity_yield"), "convexity_curve": total("convexity_curve"),
                             "jtd": total("jtd"),
                             "flags": _flags(*(f for m in members for f in m.flags
                                               if f in ("no_curve", "stale_curve", "stale_mark", "extrapolated",
                                                        "default_time", "dv01_differs", "value_differs")))})
    return pd.DataFrame(rows, columns=list(SUMMARY_COLUMNS))


# ---------------------------------------------------------------- factor histories (R2.1)


@dataclass(frozen=True, eq=False)
class FactorHistory:
    """Daily factor changes in bp, one column per factor, one row per observation date, built only from data public
    by ``as_of``: ``dates`` (ascending), ``previous`` (the date each row's first change is measured from), ``factors``
    (sorted), ``changes`` (``n x k``, NaN where a factor has no change that day), ``flags`` by (date, factor)
    (``scaled_gap``, ``extrapolated_node``, ``default_time``, ``coarse``), ``sources`` by factor, ``cells`` (the
    previous date and business-day gap of each change), ``curves`` (for ``scenario_shape="full_curve"``: the two fitted
    curves behind each curve change, with its gap scale) and ``notes`` (gaps that broke a series, rows dropped)."""

    dates: tuple[date, ...]
    previous: tuple[date, ...]
    factors: tuple[str, ...]
    changes: np.ndarray
    flags: Mapping[tuple[date, str], str] = field(default_factory=dict)
    sources: Mapping[str, str] = field(default_factory=dict)
    as_of: datetime | None = None
    cells: Mapping[tuple[date, str], tuple[date, int]] = field(default_factory=dict)
    curves: Mapping[tuple[str, date], tuple[Any, Any, float]] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        changes = np.asarray(self.changes, dtype=float).reshape(len(self.dates), len(self.factors))
        object.__setattr__(self, "changes", changes)
        if list(self.dates) != sorted(set(self.dates)):
            raise ValueError("a factor history's dates must be unique and ascending")

    def index(self, factor: str) -> int:
        return self.factors.index(factor)

    def frame(self) -> pd.DataFrame:
        """The history as long rows (``FACTOR_CHANGE_COLUMNS``), the table ``--write-history`` writes."""
        rows = []
        for i, day in enumerate(self.dates):
            for j, factor in enumerate(self.factors):
                value = self.changes[i, j]
                if not math.isfinite(value):
                    continue
                prev, gap = self.cells.get((day, factor), (self.previous[i], 1))
                rows.append({"date": day, "previous_date": prev, "factor": factor, "change_bp": value,
                             "gap_days": gap, "flag": self.flags.get((day, factor), "")})
        return pd.DataFrame(rows, columns=list(FACTOR_CHANGE_COLUMNS))


def _gap_days(cal: BusinessCalendar, prev: date, day: date) -> int:
    """Business days from ``prev`` (excluded) to ``day`` (included): 1 for consecutive business days."""
    return max(len(cal.lenient().business_days(prev + timedelta(days=1), day)), 1)


def _series_history(series: Mapping[str, list[tuple[date, float, str]]], *, cal: BusinessCalendar, gap_rule: str,
                    max_gap_days: int, as_of: datetime | None, sources: Mapping[str, str], what: str
                    ) -> FactorHistory:
    """Changes of level series (``{factor: [(date, level in bp, flag)]}``, ascending): consecutive observations one
    business day apart kept, a change over ``g`` days with ``1 < g <= max_gap_days`` scaled by ``1/sqrt(g)``
    (``scale``, flagged ``scaled_gap``), dropped (``drop``) or refused (``raise``); a longer gap breaks the series."""
    cells: dict[tuple[date, str], tuple[date, int]] = {}
    values: dict[tuple[date, str], float] = {}
    flags: dict[tuple[date, str], str] = {}
    broken = dropped = 0
    for factor, rows in series.items():
        for (d0, v0, f0), (d1, v1, f1) in zip(rows, rows[1:]):
            g = _gap_days(cal, d0, d1)
            scale = 1.0
            mark = []
            if g > max_gap_days:
                broken += 1
                continue
            if g > 1:
                if gap_rule == "raise":
                    raise ValueError(f"{what}: {factor} changes over {g} business days from {d0} to {d1} "
                                     "(gap_rule='raise')")
                if gap_rule == "drop":
                    dropped += 1
                    continue
                scale = 1 / math.sqrt(g)
                mark.append("scaled_gap")
            if not (math.isfinite(v0) and math.isfinite(v1)):
                continue
            values[(d1, factor)] = (v1 - v0) * scale
            cells[(d1, factor)] = (d0, g)
            flags[(d1, factor)] = _flags(mark, f0.split(";") if f0 else (), f1.split(";") if f1 else ())
    notes = []
    if broken:
        notes.append(f"{what}: {broken} change(s) across a gap of more than {max_gap_days} business days dropped "
                     "(the series breaks there)")
    if dropped:
        notes.append(f"{what}: {dropped} change(s) over 2-{max_gap_days} business days dropped (gap_rule='drop')")
    return _history_from_cells(values, flags, cells, sources, as_of, tuple(notes))


def _history_from_cells(values, flags, cells, sources, as_of, notes, curves=None) -> FactorHistory:
    dates = sorted({d for d, _ in values})
    factors = sorted({f for _, f in values})
    pos_d = {d: i for i, d in enumerate(dates)}
    pos_f = {f: j for j, f in enumerate(factors)}
    changes = np.full((len(dates), len(factors)), np.nan)
    for (d, f), v in values.items():
        changes[pos_d[d], pos_f[f]] = v
    previous = []
    for d in dates:
        prevs = [cells[(d, f)][0] for f in factors if (d, f) in cells]
        previous.append(min(prevs) if prevs else d)
    return FactorHistory(dates=tuple(dates), previous=tuple(previous), factors=tuple(factors), changes=changes,
                         flags={k: v for k, v in flags.items() if v}, sources=dict(sources), as_of=as_of,
                         cells=dict(cells), curves=dict(curves or {}), notes=tuple(notes))


def _nearest_fill(dz: np.ndarray, ok: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Each node outside the range takes the change of the nearest node inside it (ties: the shorter tenor)."""
    out = dz.copy()
    filled = np.zeros(dz.size, dtype=bool)
    inside = np.flatnonzero(ok)
    if not inside.size:
        return np.full(dz.size, np.nan), filled
    for k in np.flatnonzero(~ok):
        j = inside[np.argmin(np.abs(inside - k))]
        out[k] = dz[j]
        filled[k] = True
    return out, filled


def nss_key_rate_history(entries: Mapping, *, name: str = "gsec", tenors: Sequence[float] = KEY_TENORS,
                         as_of, default_time: time | None = DEFAULT_TIME, calendar: BusinessCalendar | None = None,
                         gap_rule: str = "scale", max_gap_days: int = MAX_GAP_DAYS, extrapolate: str = "nearest",
                         source: str = "") -> FactorHistory:
    """Key-rate zero-rate changes of one fitted curve (F8): ``{mark date: CurveEntry or curve}`` (``returns
    .read_nss_params``' entries of one curve name), cut at ``as_of`` by each entry's ``public_from`` (none: the default
    time on its mark date, an estimate flagged ``default_time``), in mark-date order; the change of date ``d`` is
    ``100 (z_d(T_k) - z_d'(T_k))`` bp against the previous date ``d'`` (continuous zero rates, each curve valued on its
    own settlement date, so constant-maturity rates). A key tenor outside either day's fitted range takes the change of
    the nearest tenor inside both (``extrapolate="nearest"``, flagged ``extrapolated_node``: NSS's ends follow one
    parameter and jump between fits) or has none (``"none"``, the OIS rule). Gaps by ``gap_rule`` (R2.1). Factors
    ``<name>:<tenor>``."""
    when = as_time(as_of, "as_of")
    _choice(gap_rule, GAP_RULES, "gap_rule")
    _choice(extrapolate, ("nearest", "none"), "extrapolate")
    cal = calendar if calendar is not None else mumbai_settlement_calendar()
    nodes = np.array([float(t) for t in tenors])
    labels = [f"{name}:{tenor_label(t)}" for t in nodes]
    public = curves_as_of(entries, when, default_time)
    levels = []
    for day, curve, estimated in public:
        z = np.asarray(curve.zero_rate(nodes, "continuous"), dtype=float).reshape(-1) * 100  # bp
        inside = getattr(curve, "in_data_range", None)
        if inside is not None and extrapolate == "nearest":
            ok = np.asarray(inside(nodes), dtype=bool).reshape(-1)
        else:
            ok = nodes <= float(getattr(curve, "t_max", math.inf)) + 1e-9
        ok &= np.isfinite(z)
        levels.append((day, z, ok, curve, estimated))
    values: dict[tuple[date, str], float] = {}
    flags: dict[tuple[date, str], str] = {}
    cells: dict[tuple[date, str], tuple[date, int]] = {}
    pairs: dict[tuple[str, date], tuple[Any, Any, float]] = {}
    broken = dropped = 0
    for (d0, z0, ok0, c0, e0), (d1, z1, ok1, c1, e1) in zip(levels, levels[1:]):
        g = _gap_days(cal, d0, d1)
        scale, mark = 1.0, []
        if g > max_gap_days:
            broken += 1
            continue
        if g > 1:
            if gap_rule == "raise":
                raise ValueError(f"the {name} curve history: the fits of {d0} and {d1} are {g} business days apart "
                                 "(gap_rule='raise')")
            if gap_rule == "drop":
                dropped += 1
                continue
            scale, mark = 1 / math.sqrt(g), ["scaled_gap"]
        if e0 or e1:
            mark.append("default_time")
        ok = ok0 & ok1
        dz = (z1 - z0) * scale
        if extrapolate == "nearest":
            dz, filled = _nearest_fill(dz, ok)
        else:
            dz, filled = np.where(ok, dz, np.nan), np.zeros(ok.size, dtype=bool)
        for k, label in enumerate(labels):
            if not math.isfinite(dz[k]):
                continue
            values[(d1, label)] = float(dz[k])
            cells[(d1, label)] = (d0, g)
            flags[(d1, label)] = _flags(mark, ["extrapolated_node"] if filled[k] else [])
        pairs[(name, d1)] = (c0, c1, scale)
    notes = []
    if broken:
        notes.append(f"the {name} curve history: {broken} change(s) across a gap of more than {max_gap_days} "
                     "business days dropped (the series breaks there)")
    if dropped:
        notes.append(f"the {name} curve history: {dropped} change(s) over 2-{max_gap_days} business days dropped")
    estimated = sum(1 for lv in levels if lv[4])
    if estimated:
        notes.append(f"the {name} curve history: {estimated} fit(s) without a public_from taken as public at the "
                     f"default time on their mark date (an estimate, flagged default_time)")
    sources = {label: source or f"{name} curve fits" for label in labels}
    return _history_from_cells(values, flags, cells, sources, when, tuple(notes), pairs)


def ois_key_rate_history(entries: Mapping, *, tenors: Sequence[float] = OIS_KEY_TENORS, as_of,
                         default_time: time | None = DEFAULT_TIME, calendar: BusinessCalendar | None = None,
                         gap_rule: str = "scale", max_gap_days: int = MAX_GAP_DAYS, source: str = "") -> FactorHistory:
    """Key-rate zero-rate changes of the OIS curves (``{trade date: OisCurve or CurveEntry}``), as
    ``nss_key_rate_history`` but with no extrapolation: a key tenor beyond a curve's last node has no change there
    (it is not covered). Factors ``ois:<tenor>``."""
    return nss_key_rate_history(entries, name="ois", tenors=tenors, as_of=as_of, default_time=default_time,
                                calendar=calendar, gap_rule=gap_rule, max_gap_days=max_gap_days, extrapolate="none",
                                source=source or "OIS curves")


def _known_factor(factor: str) -> None:
    head = factor.split(":", 1)[0]
    if factor == "tbill182" or (head in FACTOR_CLASSES and ":" in factor and factor.split(":", 1)[1]):
        return
    import difflib
    close = difflib.get_close_matches(head, FACTOR_CLASSES, n=1)
    hint = f" (did you mean {close[0]!r}?)" if close else ""
    raise ValueError(f"factor {factor!r} is not of a known class{hint}: classes {FACTOR_CLASSES} as "
                     "'<class>:<key>' (corp:AA:3-5y, cd:0-1y, sdl_spread:5-10y, frb_dm:<isin>, resid:<isin>), "
                     "or tbill182")


def table_factor_history(frame, *, as_of, calendar: BusinessCalendar | None = None, gap_rule: str = "scale",
                         max_gap_days: int = MAX_GAP_DAYS, default_time: time | None = DEFAULT_TIME
                         ) -> FactorHistory:
    """Changes of the factor levels a user keeps (``FACTOR_HISTORY_COLUMNS``: ``date``, ``factor``, ``value``,
    ``unit`` ``bp`` or ``pct``, ``public_from``, ``source``): rows public by ``as_of`` (a blank ``public_from``: the
    default time on its date, flagged ``default_time``), changes in bp between consecutive public rows of each factor,
    gaps by ``gap_rule``. A source naming ``coarse`` flags its changes. Refuses an unknown factor class (naming the
    closest), a factor in two units, two values of one factor and date, and blank cells, naming the line."""
    when = as_time(as_of, "as_of")
    table = _frame(frame, "the factor history")
    _check_columns(table, ("date", "factor", "value"), FACTOR_HISTORY_COLUMNS, "the factor history")
    series: dict[str, dict[date, tuple[float, str]]] = {}
    units: dict[str, str] = {}
    sources: dict[str, str] = {}
    for n, rec in _lines(table):
        where = f"factor history line {n}"
        factor = _text(rec.get("factor"))
        if not factor:
            raise ValueError(f"{where}: factor is blank")
        _known_factor(factor)
        day = _date(rec.get("date"), f"{where}: date")
        unit = _text(rec.get("unit")).lower() or "bp"
        if unit not in ("bp", "pct"):
            raise ValueError(f"{where}: unit {rec.get('unit')!r} must be bp or pct")
        if units.setdefault(factor, unit) != unit:
            raise ValueError(f"{where}: {factor} is given in {units[factor]} and {unit}")
        value = _num(rec.get("value"), f"{where}: value") * (100.0 if unit == "pct" else 1.0)
        public = public_time(rec.get("public_from"), f"{where}: public_from")
        flag = ""
        if public is None:
            if default_time is None:
                continue
            public, flag = datetime.combine(day, default_time), "default_time"
        if public > when or day > when.date():
            continue
        source = _text(rec.get("source"))
        if "coarse" in source.lower():
            flag = _flags(flag, "coarse")
        sources.setdefault(factor, source)
        rows = series.setdefault(factor, {})
        if day in rows and rows[day][0] != value:
            raise ValueError(f"{where}: two values of {factor} on {day}")
        rows[day] = (value, flag)
    ordered = {f: [(d, v, fl) for d, (v, fl) in sorted(rows.items())] for f, rows in sorted(series.items())}
    cal = calendar if calendar is not None else mumbai_settlement_calendar()
    return _series_history(ordered, cal=cal, gap_rule=gap_rule, max_gap_days=max_gap_days, as_of=when,
                           sources=sources, what="the factor history")


def residual_history_factors(frame, *, isins: Iterable[str] | None = None, as_of, residual: str = "in_fit",
                             calendar: BusinessCalendar | None = None, gap_rule: str = "scale",
                             max_gap_days: int = MAX_GAP_DAYS, default_time: time | None = DEFAULT_TIME
                             ) -> FactorHistory:
    """``resid:<isin>`` factors: the changes of each bond's residual to the fitted curve (``curvefit``'s residual
    panel, ``HISTORY_COLUMNS``: ``resid_yield_bp`` in the fit, ``loo_resid_yield_bp`` out of sample with
    ``residual="loo"``), rows of fits public by ``as_of`` (their ``public_from``, else the default time on the trade
    date, the business day before the fit's settlement ``date``, as ``Snapshot.residual_history`` cuts them)."""
    when = as_time(as_of, "as_of")
    _choice(residual, RESIDUAL_CHOICES, "residual")
    column = {"in_fit": "resid_yield_bp", "loo": "loo_resid_yield_bp"}[residual]
    table = _frame(frame, "the residual history")
    _check_columns(table, ("date", "isin", column), ("date", "isin", "resid_yield_bp", "loo_resid_yield_bp",
                                                     "public_from"), "the residual history")
    cal = calendar if calendar is not None else mumbai_settlement_calendar()
    wanted = None if isins is None else {str(i).strip().upper() for i in isins}
    series: dict[str, dict[date, tuple[float, str]]] = {}
    for n, rec in _lines(table):
        isin = _text(rec.get("isin")).upper()
        if wanted is not None and isin not in wanted or _blank(rec.get(column)):
            continue
        day = _date(rec.get("date"), f"residual history line {n}: date")
        public = public_time(rec.get("public_from"), f"residual history line {n}: public_from")
        flag = ""
        if public is None:
            if default_time is None:
                continue
            public = datetime.combine(cal.lenient().add_business_days(day, -1), default_time)
            flag = "default_time"
        if public > when:
            continue
        value = _num(rec.get(column), f"residual history line {n}: {column}")
        rows = series.setdefault(f"resid:{isin}", {})
        if day in rows and rows[day][0] != value:
            raise ValueError(f"residual history line {n}: two residuals of {isin} on {day}")
        rows[day] = (value, flag)
    ordered = {f: [(d, v, fl) for d, (v, fl) in sorted(rows.items())] for f, rows in sorted(series.items())}
    return _series_history(ordered, cal=cal, gap_rule=gap_rule, max_gap_days=max_gap_days, as_of=when,
                           sources={f: f"curvefit residuals ({residual})" for f in ordered},
                           what="the residual history")


def merge_histories(*histories: FactorHistory) -> FactorHistory:
    """One history on the union of the dates of ``histories`` (a factor in two of them is refused)."""
    values, flags, cells, sources, curves, notes = {}, {}, {}, {}, {}, []
    as_of = None
    seen: set[str] = set()
    for h in histories:
        if h is None:
            continue
        dup = seen & set(h.factors)
        if dup:
            raise ValueError(f"the factor {sorted(dup)[0]} is in two histories")
        seen |= set(h.factors)
        for i, d in enumerate(h.dates):
            for j, f in enumerate(h.factors):
                if math.isfinite(h.changes[i, j]):
                    values[(d, f)] = float(h.changes[i, j])
        flags.update(h.flags)
        cells.update(h.cells)
        sources.update(h.sources)
        curves.update(h.curves)
        notes.extend(h.notes)
        as_of = h.as_of if as_of is None or (h.as_of is not None and h.as_of > as_of) else as_of
    return _history_from_cells(values, flags, cells, sources, as_of, tuple(notes), curves)


def factor_history(ctx: RiskContext, book: RiskBook | None = None) -> FactorHistory:
    """The history of every factor a book can need, as public at ``ctx.as_of``: the G-sec curve's key rates from the NSS
    table (``ctx.nss_table``, else the market data's curves), any other curve the book is referenced to (``sdl``, a
    corporate curve), the OIS curves' key rates, the user's factor table and, with ``RiskSettings.residual``, the
    residual factors of the book's bonds."""
    st, cal = ctx.risk, ctx.calendar
    table = ctx.nss_table if ctx.nss_table is not None else (ctx.market.curves or {})
    names = {"gsec"} | ({n for n in book.curves if n != "ois"} if book is not None else set())
    if book is None:
        names |= set(table)
    parts = []
    for name in sorted(n for n in names if n in table):
        parts.append(nss_key_rate_history(table[name], name=name, tenors=st.key_tenors, as_of=ctx.as_of,
                                          default_time=ctx.default_time, calendar=cal, gap_rule=st.gap_rule,
                                          max_gap_days=st.max_gap_days))
    if ctx.market.ois_curves:
        parts.append(ois_key_rate_history(ctx.market.ois_curves, tenors=st.ois_key_tenors, as_of=ctx.as_of,
                                          default_time=ctx.default_time, calendar=cal, gap_rule=st.gap_rule,
                                          max_gap_days=st.max_gap_days))
    if ctx.factor_history is not None:
        parts.append(table_factor_history(ctx.factor_history, as_of=ctx.as_of, calendar=cal, gap_rule=st.gap_rule,
                                          max_gap_days=st.max_gap_days, default_time=ctx.default_time))
    if st.residual is not None and ctx.market.residual_history is not None:
        isins = None if book is None else [p.isin for p in book.positions]
        parts.append(residual_history_factors(ctx.market.residual_history, isins=isins, as_of=ctx.as_of,
                                              residual=st.residual, calendar=cal, gap_rule=st.gap_rule,
                                              max_gap_days=st.max_gap_days, default_time=ctx.default_time))
    return merge_histories(*parts) if parts else _history_from_cells({}, {}, {}, {}, ctx.as_of, ())


# ---------------------------------------------------------------- quantiles, ES, covariance (R2.2-R2.4)


def tail_size(n: int, alpha: float) -> Fraction:
    """``m = n (1 - a)`` computed exactly (``Fraction(str(a))``: in floats ``250 x (1 - 0.99)`` is 2.5000000000000022
    and ``200 x (1 - 0.99)`` is 2.0000000000000018)."""
    return Fraction(int(n)) * (1 - Fraction(str(float(alpha))))


def _losses(pnl) -> np.ndarray:
    arr = np.asarray(pnl, dtype=float).reshape(-1)
    if not arr.size or not np.all(np.isfinite(arr)):
        raise ValueError("scenario P&L must be a non-empty array of finite numbers")
    return np.sort(-arr)[::-1]  # L_(1) the largest loss


def hs_var(pnl, alpha: float, quantile_rule: str = "ceil") -> float:
    """The historical VaR at ``alpha`` (F10): with losses ``L = -PnL`` sorted largest first and ``m = n(1-a)`` exact,
    ``L_(ceil m)`` (``ceil``, default), ``L_(floor m + 1)`` (``floor_plus_one``) or numpy's linear quantile of the
    losses (``interpolate``)."""
    _choice(quantile_rule, QUANTILE_RULES, "quantile_rule")
    losses = _losses(pnl)
    if quantile_rule == "interpolate":
        return float(np.quantile(losses, float(alpha), method="linear"))
    m = tail_size(losses.size, alpha)
    rank = math.ceil(m) if quantile_rule == "ceil" else math.floor(m) + 1
    rank = min(max(rank, 1), losses.size)
    return float(losses[rank - 1])


def hs_es(pnl, alpha: float) -> float:
    """The historical ES at ``alpha`` (F10; Acerbi and Tasche 2002): the tail mean over exactly ``m = n(1-a)``
    observations, ``[sum_{i <= floor m} L_(i) + (m - floor m) L_(floor m + 1)] / m``."""
    losses = _losses(pnl)
    m = tail_size(losses.size, alpha)
    whole = math.floor(m)
    tail = math.fsum(losses[:whole])
    frac = m - whole
    if frac and whole < losses.size:
        tail += float(frac) * float(losses[whole])
    return tail / float(m)


def var_es(pnl, *, alpha_var: float = VAR_ALPHA, alpha_es: float = ES_ALPHA, quantile_rule: str = "ceil"
           ) -> tuple[float, float, float, float, Fraction]:
    """``(VaR at alpha_var, ES at alpha_es, ES at alpha_var, VaR at alpha_es, m at alpha_var)`` of scenario P&L."""
    return (hs_var(pnl, alpha_var, quantile_rule), hs_es(pnl, alpha_es), hs_es(pnl, alpha_var),
            hs_var(pnl, alpha_es, quantile_rule), tail_size(np.asarray(pnl).size, alpha_var))


def tail_weights(pnl, alpha: float) -> np.ndarray:
    """Acerbi-Tasche weights of each scenario in the ES at ``alpha``: 1 for the ``floor m`` worst, ``m - floor m`` for
    the next, 0 else (the worst first; ties to the earliest scenario), so ``ES = -sum a_s PnL_s / m``."""
    arr = np.asarray(pnl, dtype=float).reshape(-1)
    m = tail_size(arr.size, alpha)
    order = np.argsort(arr, kind="mergesort")
    w = np.zeros(arr.size)
    whole = math.floor(m)
    w[order[:whole]] = 1.0
    if m - whole and whole < arr.size:
        w[order[whole]] = float(m - whole)
    return w


def ewma_covariance(changes, *, lam: float = EWMA_LAMBDA, max_obs: int = EWMA_MAX_OBS) -> tuple[np.ndarray, int]:
    """The EWMA covariance (zero mean; RiskMetrics 1996 sec. 5.3) of ``n x k`` changes, oldest first, over the last
    ``min(n, max_obs)`` rows: ``sum_{i<n} (1-l) l^i r_{t-i} r_{t-i}' / (1 - l^n)``, the weights normalised to sum to
    one (U: the document's recursion omits the normalisation). Returns ``(matrix, rows used)``."""
    x = np.asarray(changes, dtype=float)
    if x.ndim != 2 or not x.shape[0]:
        raise ValueError("EWMA needs an n x k array of changes with n >= 1")
    if not 0 < lam < 1:
        raise ValueError(f"lambda must be between 0 and 1, not {lam}")
    x = x[-int(max_obs):]
    n = x.shape[0]
    w = (1 - lam) * lam ** np.arange(n - 1, -1, -1, dtype=float)  # oldest first: lam^(n-1) ... lam^0
    w = w / w.sum()
    return (x * w[:, None]).T @ x, n


def sample_covariance(changes) -> tuple[np.ndarray, int]:
    """The equal-weighted sample covariance (mean removed, ddof 1) of ``n x k`` changes."""
    x = np.asarray(changes, dtype=float)
    if x.ndim != 2 or x.shape[0] < 2:
        raise ValueError("a sample covariance needs at least two observations")
    return np.atleast_2d(np.cov(x, rowvar=False, ddof=1)), x.shape[0]


def normal_var_es(sigma: float, alpha_var: float, alpha_es: float, horizon_days: int = 1) -> tuple[float, float]:
    """Delta-normal VaR and ES (F11): ``z_a sigma sqrt(h)`` and ``sigma sqrt(h) phi(z_a) / (1 - a)``."""
    root = math.sqrt(horizon_days)
    z_var = _N.inv_cdf(alpha_var)
    z_es = _N.inv_cdf(alpha_es)
    return z_var * sigma * root, sigma * root * _N.pdf(z_es) / (1 - alpha_es)


# ---------------------------------------------------------------- the factors a scope's VaR uses


@dataclass(frozen=True, eq=False)
class _Plan:
    """A scope's VaR inputs: the history columns it uses (``factors``, ``cols``), each item's sensitivities on them
    (``S``, items x factors, rupees per bp, after proxies), the dates with every used factor (``rows``), and
    coverage."""

    items: tuple
    factors: tuple[str, ...]
    cols: np.ndarray
    S: np.ndarray
    rows: np.ndarray
    total_abs: float
    covered_abs: float
    uncovered: Mapping[str, float]
    flags: tuple[str, ...]
    item_factor_map: tuple  # per item: {used factor: [(own factor, scale)]}

    @property
    def coverage(self) -> float:
        return self.covered_abs / self.total_abs if self.total_abs > 0 else 1.0


def _var_factors(item) -> dict[str, float]:
    """The item's VaR sensitivities by factor (par-quote DV01s are reported, not VaR factors)."""
    out: dict[str, float] = {}
    for sn in item.sens:
        if sn.basis == "par":
            continue
        out[sn.factor] = out.get(sn.factor, 0.0) + sn.value
    return out


def _plan(book: RiskBook, history: FactorHistory, scope_type: str, scope: str, proxies=None,
          *, min_obs_hard: int, end: date | None = None) -> _Plan:
    items = tuple(i for i in book.items if book.in_scope(i, scope_type, scope))
    proxy = {}
    if proxies is not None:
        for rec in read_proxies(proxies).to_dict("records"):
            proxy[rec["factor"]] = (rec["proxy"], float(rec["scale"]))
    last = end if end is not None else book.date
    upto = np.array([d <= last for d in history.dates], dtype=bool)
    counts = {f: int(np.isfinite(history.changes[upto, j]).sum()) for j, f in enumerate(history.factors)}
    flags: list[str] = []
    uncovered: dict[str, float] = {}
    total = covered = 0.0
    maps = []
    used: set[str] = set()
    for item in items:
        mapping: dict[str, list[tuple[str, float]]] = {}
        for factor, value in _var_factors(item).items():
            total += abs(value)
            target, scale = factor, 1.0
            if counts.get(factor, 0) < min_obs_hard and factor in proxy:
                target, scale = proxy[factor]
                flags.append("proxied")
            if counts.get(target, 0) < min_obs_hard:
                uncovered[_factor_class(factor)] = uncovered.get(_factor_class(factor), 0.0) + abs(value)
                if counts.get(target, 0):
                    flags.append(f"short_factor:{target}")
                continue
            covered += abs(value)
            mapping.setdefault(target, []).append((factor, scale))
            used.add(target)
        maps.append(mapping)
    factors = tuple(sorted(used))
    cols = np.array([history.index(f) for f in factors], dtype=int)
    S = np.zeros((len(items), len(factors)))
    own = [_var_factors(i) for i in items]
    for a, mapping in enumerate(maps):
        for target, sources in mapping.items():
            S[a, factors.index(target)] = math.fsum(own[a][f] * sc for f, sc in sources)
    if len(cols):
        block = history.changes[:, cols]
        rows = np.flatnonzero(upto & np.all(np.isfinite(block), axis=1))
    else:
        rows = np.flatnonzero(upto)
    return _Plan(items=items, factors=factors, cols=cols, S=S, rows=rows, total_abs=total, covered_abs=covered,
                 uncovered=uncovered, flags=tuple(dict.fromkeys(flags)), item_factor_map=tuple(maps))


# ---------------------------------------------------------------- scenario P&L (R2.2)


def _horizon_changes(x: np.ndarray, horizon_days: int, rule: str) -> np.ndarray:
    """Scenario changes for a horizon: the daily changes (h = 1), overlapping sums of ``h`` consecutive rows
    (``n - h + 1`` scenarios, MAR33.4), or the daily changes for ``sqrt_time`` (scaled afterwards)."""
    if horizon_days == 1 or rule == "sqrt_time":
        return x
    if x.shape[0] < horizon_days:
        return np.zeros((0, x.shape[1]))
    c = np.cumsum(np.vstack([np.zeros((1, x.shape[1])), x]), axis=0)
    return c[horizon_days:] - c[:-horizon_days]


def _sens_pnl(plan: _Plan, dx: np.ndarray, delta_gamma: bool) -> np.ndarray:
    """F9, first order: ``PnL_{s,i} = - sum_f S_{i,f} dx_{s,f}`` (n x items), plus, with ``delta_gamma``, half each
    position's curve convexity times the square of its duration-weighted parallel move."""
    pnl = -(dx @ plan.S.T) if plan.S.size else np.zeros((dx.shape[0], len(plan.items)))
    if delta_gamma:
        for a, item in enumerate(plan.items):
            gamma = getattr(item, "convexity_curve", math.nan)
            row = plan.S[a]
            rate = np.array([_factor_class(f) not in SPREAD_CLASSES for f in plan.factors], dtype=bool)
            weight = row[rate].sum()
            if not math.isfinite(gamma) or not weight:
                continue
            p = dx[:, rate] @ row[rate] / weight
            pnl[:, a] += 0.5 * gamma * p * p
    return pnl


def _changes_by_factor(plan: _Plan, a: int, dx: np.ndarray) -> dict[str, np.ndarray]:
    """The scenario changes of item ``a``'s own factors (a proxied factor moves with its proxy times the scale)."""
    out: dict[str, np.ndarray] = {}
    for target, sources in plan.item_factor_map[a].items():
        col = dx[:, plan.factors.index(target)]
        for factor, scale in sources:
            out[factor] = col * scale
    return out


def _curve_shift(item_tenors: Sequence[float], name: str, moves: Mapping[str, np.ndarray], t: np.ndarray, n: int
                 ) -> np.ndarray:
    """``n x len(t)`` shifts (decimal) of continuous zero rates at times ``t`` from key-rate moves (bp) joined by the
    triangles of ``item_tenors``."""
    dx = np.zeros((n, len(item_tenors)))
    for k, tenor in enumerate(item_tenors):
        col = moves.get(f"{name}:{tenor_label(tenor)}")
        if col is not None:
            dx[:, k] = col
    return dx @ key_weights(t, item_tenors) * 1e-4


def _full_curve_shift(history: FactorHistory, name: str, dates: Sequence[date], t: np.ndarray, horizon_days: int,
                      rule: str) -> np.ndarray | None:
    """With ``scenario_shape="full_curve"``: the whole fitted change of curve ``name`` at times ``t`` for each
    scenario (summed over the horizon's days), or None when a date lacks its pair of fits."""
    rows = []
    for d in dates:
        pair = history.curves.get((name, d))
        if pair is None:
            return None
        c0, c1, scale = pair
        rows.append((np.asarray(c1.zero_rate(t, "continuous"), dtype=float)
                     - np.asarray(c0.zero_rate(t, "continuous"), dtype=float)) * scale / 100)
    x = np.vstack(rows) if rows else np.zeros((0, t.size))
    return _horizon_changes(x, horizon_days, rule)


def _full_pnl_item(item, moves: Mapping[str, np.ndarray], n: int, settings: RiskSettings,
                   full_shift: np.ndarray | None = None) -> np.ndarray:
    """Full revaluation of one position or swap under ``n`` scenarios of factor moves (F9): bonds on their curve
    shifted by the triangles of their key-rate moves (or the whole fitted change) at their z-spread plus their spread
    factor's move, a bond without a curve at its yield moved by its key rates at its Macaulay duration, an FRB with
    its projected coupons and discount rate moved by the index move at its next reset and its margin by ``frb_dm``,
    a swap through ``ois.ois_npv`` on the shifted OIS curve."""
    if isinstance(item, RiskSwap):
        tenors = tuple(sn.tenor for sn in item.sens if sn.basis == "zero" and sn.curve == "ois")
        tenors = tuple(sorted(set(tenors)))
        base = item.npv(item.curve)
        out = np.zeros(n)
        if not tenors:
            return out
        dx = np.zeros((n, len(tenors)))
        for k, tenor in enumerate(tenors):
            col = moves.get(f"ois:{tenor_label(tenor)}")
            if col is not None:
                dx[:, k] = col
        plain = _plain(item.curve)
        for s in range(n):
            if not np.any(dx[s]):
                continue
            row = dx[s]

            def shift(x, row=row):
                arr = np.asarray(x, dtype=float)
                return (row @ key_weights(arr.reshape(-1), tenors) * 1e-4).reshape(arr.shape)
            out[s] = item.npv(rt.ShiftedCurve(plain, shift)) - base
        return out
    bond, units = item.instrument.bond, item.units
    if isinstance(bond, GoiFloatingRateBond):
        t_reset = max((item.next_reset - item.settlement).days / _YEAR, 0.0)
        h = np.zeros(n)
        if "tbill182" in moves:
            h = moves["tbill182"]
        else:
            w = key_weights([t_reset], settings.key_tenors)[:, 0]
            for k, tenor in enumerate(settings.key_tenors):
                col = moves.get(f"gsec:{tenor_label(tenor)}")
                if col is not None and w[k]:
                    h = h + w[k] * col
        dm = moves.get(f"frb_dm:{item.isin}", np.zeros(n))
        base = _frb_dirty(bond, item.settlement, item.yield_pct, 0.0, 0.0)
        return np.array([units * (_frb_dirty(bond, item.settlement, item.yield_pct, float(h[s]), float(dm[s])) - base)
                         if (h[s] or dm[s]) else 0.0 for s in range(n)])
    if item.curve is None:  # the yield proxy
        name = next((sn.curve for sn in item.sens if sn.method == "yield_proxy"), "gsec")
        tenors = tuple(sorted({sn.tenor for sn in item.sens if sn.method == "yield_proxy"}))
        if not tenors:
            return np.zeros(n)
        all_tenors = _curve_tenors(name, None, settings)
        dy = _curve_shift(all_tenors, name, moves, np.array([item.macaulay]), n)[:, 0] * 100  # percent
        base = _dirty(bond, item.yield_pct, item.settlement)
        return np.array([units * (_dirty(bond, item.yield_pct + dy[s], item.settlement) - base) if dy[s] else 0.0
                         for s in range(n)])
    t, cf, disc, z = item.times, item.amounts, item.discount, item.z_spread
    tenors = _curve_tenors(item.reference, item.curve, settings)
    shift = full_shift if full_shift is not None else _curve_shift(tenors, item.reference, moves, t, n)
    dz = np.zeros(n)
    for sn in item.sens:
        if sn.basis in ("spread", "yield") and sn.factor in moves and sn.method != "yield_proxy":
            dz = dz + moves[sn.factor] * 1e-4
    base = float(cf @ (disc * np.exp(-z * t)))
    values = (np.exp(-(z + dz)[:, None] * t[None, :] - shift * t[None, :]) * disc[None, :]) @ cf
    return units * (values - base)


def _frb_dirty(bond: GoiFloatingRateBond, s: date, y: float, h_bp: float, dm_bp: float) -> float:
    """An FRB's dirty price with its projected coupons (periods after ``s``) and its discount rate moved by ``h_bp``
    and its discount margin by ``dm_bp`` (index form, the engine's index = yield convention), as
    ``floating.index_dv01`` bumps it."""
    h = h_bp / 100
    if h:
        rates = [(st, r + h if status == "projected" and st > s else r, status, when)
                 for st, r, status, when in bond.rates]
        bond = bond.with_rates(rates, bond.index_pct)
    return price_from_dm(bond, s, dm_bp, index_pct=y + h)[1]


def _full_pnl(book: RiskBook, plan: _Plan, dx: np.ndarray, history: FactorHistory, dates: Sequence[date],
              horizon_days: int, settings: RiskSettings) -> np.ndarray:
    n = dx.shape[0]
    out = np.zeros((n, len(plan.items)))
    rule = "overlapping"
    for a, item in enumerate(plan.items):
        moves = _changes_by_factor(plan, a, dx)
        full = None
        if settings.scenario_shape == "full_curve" and isinstance(item, RiskPosition) and item.curve is not None \
                and not isinstance(item.instrument.bond, GoiFloatingRateBond):
            full = _full_curve_shift(history, item.reference, dates, item.times, horizon_days, rule)
        out[:, a] = _full_pnl_item(item, moves, n, settings, full)
    return out


def scenario_pnl(book: RiskBook, history: FactorHistory, settings: RiskSettings | None = None, *, method: str,
                 horizon_days: int = 1, start: date | None = None, end: date | None = None,
                 scope: tuple[str, str] = ("fund", "fund"), proxies=None) -> tuple[tuple[date, ...], np.ndarray]:
    """``(scenario dates, n x items P&L)`` of the scope's positions and swaps (in ``RiskBook.items`` order, those in
    scope) under the history's changes on the dates from ``start`` to ``end`` with every used factor present:
    ``hs_sensitivity`` (F9 first order, plus convexity with ``delta_gamma``) or ``hs_full`` (full revaluation). The
    10-day horizon sums overlapping runs of 10 consecutive observations (``horizon_rule="overlapping"``); a scenario is
    dated by its last day."""
    st = settings if settings is not None else book.settings
    _choice(method, ("hs_sensitivity", "hs_full"), "method")
    plan = _plan(book, history, *scope, proxies, min_obs_hard=1, end=end)
    rows = plan.rows
    if start is not None:
        rows = rows[[history.dates[r] >= start for r in rows]] if len(rows) else rows
    return _scenarios(book, plan, history, rows, method, horizon_days, st)


def _scenarios(book: RiskBook, plan: _Plan, history: FactorHistory, rows: np.ndarray, method: str,
               horizon_days: int, st: RiskSettings) -> tuple[tuple[date, ...], np.ndarray]:
    x = history.changes[np.ix_(rows, plan.cols)] if len(plan.cols) else np.zeros((len(rows), 0))
    rule = st.horizon_rule
    dx = _horizon_changes(x, horizon_days, rule)
    day_list = [history.dates[r] for r in rows]
    if horizon_days > 1 and rule == "overlapping":
        dates = tuple(day_list[horizon_days - 1:])
    else:
        dates = tuple(day_list)
    if method == "hs_sensitivity":
        pnl = _sens_pnl(plan, dx, st.delta_gamma)
    else:
        pnl = _full_pnl(book, plan, dx, history, day_list, horizon_days if rule == "overlapping" else 1, st)
    if horizon_days > 1 and rule == "sqrt_time":
        pnl = pnl * math.sqrt(horizon_days)
    return dates, pnl


# ---------------------------------------------------------------- VaR and ES results (R2.2-R2.6)


@dataclass(frozen=True, eq=False)
class VarResult:
    """One VaR and ES of a scope: the method, horizon and window, the window's dates and size, both measures at their
    levels (and each at the other's level, ``var_at_alpha_es`` and ``es_at_alpha_var``), the coverage of the
    sensitivities by factors with history, the status (``ok``, ``short_history``, ``incomplete``, ``refused``), flags,
    and the scenarios behind a historical figure (``scenario_dates``, ``scenario_pnl`` n x items, ``items``)."""

    scope_type: str
    scope: str
    method: str
    horizon_days: int
    horizon_rule: str
    window: str
    window_start: date | None
    window_end: date | None
    n_obs: int
    alpha_var: float
    var: float
    alpha_es: float
    es: float
    coverage: float
    uncovered: float
    status: str
    flags: tuple[str, ...]
    var_at_alpha_es: float = math.nan
    es_at_alpha_var: float = math.nan
    scenario_dates: tuple[date, ...] = ()
    scenario_pnl: np.ndarray | None = None
    items: tuple = ()


def _status(n_obs: int, coverage: float, settings: RiskSettings, flags: list[str]) -> str:
    if n_obs < settings.min_obs:
        flags.append(f"short_history:{n_obs}<{settings.min_obs}")
    if coverage < 1 - 1e-12:
        return "incomplete"
    return "short_history" if n_obs < settings.min_obs else "ok"


def _window_rows(plan: _Plan, settings: RiskSettings, window: str = "current") -> np.ndarray:
    if window != "current":
        raise ValueError(f"window must be current here, not {window!r}")
    return plan.rows[-settings.window:]


def _coarse(history: FactorHistory, plan: _Plan) -> bool:
    return any("coarse" in history.sources.get(f, "").lower() for f in plan.factors) or any(
        "coarse" in v for (d, f), v in history.flags.items() if f in plan.factors)


def _plan_flags(plan: _Plan, history: FactorHistory) -> list[str]:
    flags = list(plan.flags)
    for cls, value in sorted(plan.uncovered.items()):
        flags.append(f"uncovered_{cls}={value:.2f}")
    if _coarse(history, plan):
        flags.append("coarse")
    return flags


def historical_var(book: RiskBook, history: FactorHistory, settings: RiskSettings | None = None, *, method: str,
                   horizon_days: int = 1, scope: tuple[str, str] = ("fund", "fund"), proxies=None,
                   rows: np.ndarray | None = None, window: str = "current") -> VarResult:
    """The historical VaR and ES of a scope (R2.2): ``hs_sensitivity`` or ``hs_full`` scenarios on the current window
    (the latest ``settings.window`` dates on which every used factor has a change), 1-day or ``horizon_days`` by
    ``horizon_rule``; quantiles and ES by ``settings.quantile_rule`` with ``m`` exact. Refused (``ValueError`` naming
    the count) below ``min_obs_hard`` observations; flagged ``short_history`` below ``min_obs``; ``incomplete`` when a
    factor of the scope has no history (coverage below one)."""
    st = settings if settings is not None else book.settings
    plan = _plan(book, history, *scope, proxies, min_obs_hard=st.min_obs_hard)
    use = _window_rows(plan, st) if rows is None else rows
    n_days = len(use)
    flags = _plan_flags(plan, history)
    if n_days < st.min_obs_hard:
        raise ValueError(f"{scope[0]} {scope[1]}: {n_days} observation(s) with every factor of the scope by "
                         f"{book.as_of:%Y-%m-%d}, below min_obs_hard {st.min_obs_hard}: refused")
    dates, pnl = _scenarios(book, plan, history, use, method, horizon_days, st)
    total = pnl.sum(axis=1) if pnl.size else np.zeros(len(dates))
    if not len(total):
        raise ValueError(f"{scope[0]} {scope[1]}: no {horizon_days}-day scenario in {n_days} observations: refused")
    if plan.total_abs > 0 and plan.covered_abs == 0:
        flags.append("no_factor_with_history")
    var, es, es_v, var_e, m = var_es(total, alpha_var=st.var_alpha, alpha_es=st.es_alpha,
                                     quantile_rule=st.quantile_rule)
    if horizon_days > 1:
        flags.append("overlapping" if st.horizon_rule == "overlapping" else "sqrt_time")
    status = _status(n_days, plan.coverage, st, flags)
    if plan.total_abs > 0 and plan.covered_abs == 0:
        var = es = es_v = var_e = math.nan
    days = [history.dates[r] for r in use]
    return VarResult(scope_type=scope[0], scope=scope[1], method=method, horizon_days=horizon_days,
                     horizon_rule="" if horizon_days == 1 else st.horizon_rule, window=window,
                     window_start=days[0] if days else None, window_end=days[-1] if days else None, n_obs=n_days,
                     alpha_var=st.var_alpha, var=var, alpha_es=st.es_alpha, es=es, coverage=plan.coverage,
                     uncovered=plan.total_abs - plan.covered_abs, status=status, flags=tuple(dict.fromkeys(flags)),
                     var_at_alpha_es=var_e, es_at_alpha_var=es_v, scenario_dates=dates, scenario_pnl=pnl,
                     items=plan.items)


def _covariance(plan: _Plan, history: FactorHistory, st: RiskSettings) -> tuple[np.ndarray, np.ndarray, int]:
    """(covariance of the plan's factors in bp^2 a day, the rows it used, their count): EWMA over the latest
    ``ewma_max_obs`` complete rows, or the sample covariance of the current window."""
    if st.cov_method == "ewma":
        rows = plan.rows[-st.ewma_max_obs:]
    else:
        rows = plan.rows[-st.window:]
    x = history.changes[np.ix_(rows, plan.cols)]
    if not len(plan.cols):
        return np.zeros((0, 0)), rows, len(rows)
    if st.cov_method == "ewma":
        cov, n = ewma_covariance(x, lam=st.ewma_lambda, max_obs=st.ewma_max_obs)
    else:
        cov, n = sample_covariance(x)
    return cov, rows, n


def parametric_var(book: RiskBook, history: FactorHistory, settings: RiskSettings | None = None, *,
                   horizon_days: int = 1, scope: tuple[str, str] = ("fund", "fund"), proxies=None) -> VarResult:
    """The delta-normal VaR and ES of a scope (R2.3, F11): ``sigma_p = sqrt(s' Sigma s)`` with ``s`` the scope's net
    factor sensitivities and ``Sigma`` the EWMA (or sample) covariance as of ``as_of``; ``VaR = z_a sigma_p sqrt(h)``,
    ``ES = sigma_p sqrt(h) phi(z_a) / (1 - a)`` (the square root of time for any horizon)."""
    st = settings if settings is not None else book.settings
    plan = _plan(book, history, *scope, proxies, min_obs_hard=st.min_obs_hard)
    cov, rows, n = _covariance(plan, history, st)
    flags = _plan_flags(plan, history)
    if n < st.min_obs_hard:
        raise ValueError(f"{scope[0]} {scope[1]}: {n} observation(s) for the covariance, below min_obs_hard "
                         f"{st.min_obs_hard}: refused")
    s = plan.S.sum(axis=0) if plan.S.size else np.zeros(0)
    sigma = math.sqrt(max(float(s @ cov @ s), 0.0)) if s.size else 0.0
    var, es = normal_var_es(sigma, st.var_alpha, st.es_alpha, horizon_days)
    var_e, es_v = normal_var_es(sigma, st.es_alpha, st.var_alpha, horizon_days)
    flags.append(f"{st.cov_method}:{n}")
    if horizon_days > 1:
        flags.append("sqrt_time")
    status = _status(n, plan.coverage, st, flags)
    if plan.total_abs > 0 and plan.covered_abs == 0:
        var = es = var_e = es_v = math.nan
        flags.append("no_factor_with_history")
    days = [history.dates[r] for r in rows]
    return VarResult(scope_type=scope[0], scope=scope[1], method="parametric", horizon_days=horizon_days,
                     horizon_rule="" if horizon_days == 1 else "sqrt_time", window="current",
                     window_start=days[0] if days else None, window_end=days[-1] if days else None, n_obs=n,
                     alpha_var=st.var_alpha, var=var, alpha_es=st.es_alpha, es=es, coverage=plan.coverage,
                     uncovered=plan.total_abs - plan.covered_abs, status=status, flags=tuple(dict.fromkeys(flags)),
                     var_at_alpha_es=var_e, es_at_alpha_var=es_v, items=plan.items)


def _reduced_factors(plan: _Plan, history: FactorHistory, days: int = 30) -> tuple[str, ...]:
    """The reduced factor set of the stressed search (MAR33.5, U rule): the plan's factors whose history starts no
    later than ``days`` calendar days after the earliest start among them."""
    starts = {}
    for f, col in zip(plan.factors, plan.cols):
        finite = np.flatnonzero(np.isfinite(history.changes[:, col]))
        if finite.size:
            starts[f] = history.dates[finite[0]]
    if not starts:
        return ()
    first = min(starts.values())
    return tuple(f for f in plan.factors if f in starts and starts[f] <= first + timedelta(days=days))


def _restricted(plan: _Plan, history: FactorHistory, factors: Sequence[str], end: date) -> _Plan:
    keep = [plan.factors.index(f) for f in factors]
    cols = plan.cols[keep] if keep else np.zeros(0, dtype=int)
    upto = np.array([d <= end for d in history.dates], dtype=bool)
    rows = np.flatnonzero(upto & np.all(np.isfinite(history.changes[:, cols]), axis=1)) if len(cols) else \
        np.flatnonzero(upto)
    maps = tuple({t: v for t, v in m.items() if t in factors} for m in plan.item_factor_map)
    covered = float(np.abs(plan.S[:, keep]).sum()) if keep else 0.0
    return replace(plan, factors=tuple(factors), cols=cols, S=plan.S[:, keep], rows=rows, item_factor_map=maps,
                   covered_abs=covered)


def stressed_window(book: RiskBook, history: FactorHistory, settings: RiskSettings | None = None, *,
                    horizon_days: int = 10, scope: tuple[str, str] = ("fund", "fund"), proxies=None
                    ) -> tuple[date | None, date | None, float]:
    """``(start, end, ES)`` of the ``window``-long run of consecutive observations (on the reduced factor set) in which
    the current book's ``horizon_days`` ES at ``es_alpha`` is largest (MAR33.7), searched over the whole history at a
    step of ``stressed_step`` observations, ties to the earliest start; by the sensitivity method (U: the search is
    fast; the stressed ES itself is then computed by each method asked)."""
    st = settings if settings is not None else book.settings
    plan = _plan(book, history, *scope, proxies, min_obs_hard=st.min_obs_hard)
    reduced = _restricted(plan, history, _reduced_factors(plan, history), book.date)
    rows = reduced.rows
    if len(rows) < st.window:
        return None, None, math.nan
    x = history.changes[np.ix_(rows, reduced.cols)] if len(reduced.cols) else np.zeros((len(rows), 0))
    daily = -(x @ reduced.S.sum(axis=0)) if reduced.S.size else np.zeros(len(rows))
    best = (-math.inf, None)
    for start in range(0, len(rows) - st.window + 1, st.stressed_step):
        block = daily[start:start + st.window]
        if horizon_days > 1 and st.horizon_rule == "overlapping":
            c = np.concatenate([[0.0], np.cumsum(block)])
            scen = c[horizon_days:] - c[:-horizon_days]
        else:
            scen = block * (math.sqrt(horizon_days) if horizon_days > 1 else 1.0)
        if not scen.size:
            continue
        es = hs_es(scen, st.es_alpha)
        if es > best[0]:
            best = (es, start)
    if best[1] is None:
        return None, None, math.nan
    a = best[1]
    return history.dates[rows[a]], history.dates[rows[a + st.window - 1]], best[0]


def stressed_es(book: RiskBook, history: FactorHistory, settings: RiskSettings | None = None, *, method: str,
                horizon_days: int = 10, scope: tuple[str, str] = ("fund", "fund"), proxies=None) -> VarResult:
    """The stressed ES (MAR33.6): ``ES_R,S x max(1, ES_F,C / ES_R,C)``, R the reduced factor set, F the full set, S the
    stressed window (``stressed_window``), C the current one; ``var`` is the stressed window's VaR on the reduced set,
    unscaled (flagged)."""
    st = settings if settings is not None else book.settings
    plan = _plan(book, history, *scope, proxies, min_obs_hard=st.min_obs_hard)
    reduced_names = _reduced_factors(plan, history)
    reduced = _restricted(plan, history, reduced_names, book.date)
    start, end, _ = stressed_window(book, history, st, horizon_days=horizon_days, scope=scope, proxies=proxies)
    if start is None:
        raise ValueError(f"{scope[0]} {scope[1]}: fewer than {st.window} observations of the reduced factor set: no "
                         "stressed window")
    rows_s = np.array([r for r in reduced.rows if start <= history.dates[r] <= end], dtype=int)
    d_s, p_s = _scenarios(book, reduced, history, rows_s, method, horizon_days, st)
    rows_c = _window_rows(plan, st)
    _, p_fc = _scenarios(book, plan, history, rows_c, method, horizon_days, st)
    rows_rc = _window_rows(reduced, st)
    _, p_rc = _scenarios(book, reduced, history, rows_rc, method, horizon_days, st)
    es_rs = hs_es(p_s.sum(axis=1), st.es_alpha)
    es_fc = hs_es(p_fc.sum(axis=1), st.es_alpha)
    es_rc = hs_es(p_rc.sum(axis=1), st.es_alpha)
    ratio = es_fc / es_rc if es_rc > 0 else 1.0
    factor = max(1.0, ratio)
    flags = _plan_flags(plan, history) + [f"ratio_fc_rc={ratio:.6f}", "var_reduced_set_unscaled",
                                          f"reduced_factors={len(reduced_names)}/{len(plan.factors)}"]
    var = hs_var(p_s.sum(axis=1), st.var_alpha, st.quantile_rule)
    status = _status(len(rows_s), plan.coverage, st, flags)
    return VarResult(scope_type=scope[0], scope=scope[1], method=method, horizon_days=horizon_days,
                     horizon_rule="" if horizon_days == 1 else st.horizon_rule, window="stressed", window_start=start,
                     window_end=end, n_obs=len(rows_s), alpha_var=st.var_alpha, var=var, alpha_es=st.es_alpha,
                     es=es_rs * factor, coverage=plan.coverage, uncovered=plan.total_abs - plan.covered_abs,
                     status=status, flags=tuple(dict.fromkeys(flags)),
                     var_at_alpha_es=hs_var(p_s.sum(axis=1), st.es_alpha, st.quantile_rule),
                     es_at_alpha_var=hs_es(p_s.sum(axis=1), st.var_alpha) * factor, scenario_dates=d_s,
                     scenario_pnl=p_s, items=reduced.items)


def _horizon_class(factor: str, rating_of: Mapping[str, str]) -> int:
    cls = _factor_class(factor)
    if cls == "corp":
        bucket = factor.split(":")[1] if factor.count(":") >= 2 else ""
        return LIQUIDITY_HORIZONS["corp"] if bucket in IG_BUCKETS else LIQUIDITY_HORIZONS["corp_hy"]
    return LIQUIDITY_HORIZONS.get(cls, 10)


def liquidity_horizon_es(book: RiskBook, history: FactorHistory, settings: RiskSettings | None = None, *,
                         scope: tuple[str, str] = ("fund", "fund"), proxies=None) -> VarResult:
    """MAR33.4's liquidity-horizon ES on the sensitivity method: ``sqrt(ES_T(P)^2 + sum_{j>=2} (ES_T(P, j)
    sqrt((LH_j - LH_{j-1}) / T))^2)``, ``T`` 10 days, ``ES_T(P, j)`` the 10-day ES with only the factors of horizon
    at least ``LH_j`` moved (``LIQUIDITY_HORIZONS``: rates 10, FRB margins 20, IG corporate spreads 40, high yield 60;
    the INR mapping UNVERIFIED)."""
    st = settings if settings is not None else book.settings
    plan = _plan(book, history, *scope, proxies, min_obs_hard=st.min_obs_hard)
    rows = _window_rows(plan, st)
    if len(rows) < st.min_obs_hard:
        raise ValueError(f"{scope[0]} {scope[1]}: {len(rows)} observation(s), below min_obs_hard: refused")
    x = history.changes[np.ix_(rows, plan.cols)]
    dx = _horizon_changes(x, 10, "overlapping")
    s = plan.S.sum(axis=0)
    lh = np.array([_horizon_class(f, {}) for f in plan.factors])
    total = 0.0
    prev = None
    for j, horizon in enumerate(LH_STEPS):
        mask = lh >= horizon
        pnl = -(dx[:, mask] @ s[mask]) if mask.any() else np.zeros(dx.shape[0])
        es = hs_es(pnl, st.es_alpha) if pnl.size else 0.0
        if j == 0:
            total += es * es
        else:
            total += (es * math.sqrt((horizon - prev) / 10)) ** 2
        prev = horizon
    flags = _plan_flags(plan, history) + ["liquidity_horizons"]
    status = _status(len(rows), plan.coverage, st, flags)
    days = [history.dates[r] for r in rows]
    return VarResult(scope_type=scope[0], scope=scope[1], method="hs_sensitivity", horizon_days=10,
                     horizon_rule="liquidity_horizons", window="current", window_start=days[0], window_end=days[-1],
                     n_obs=len(rows), alpha_var=st.var_alpha, var=math.nan, alpha_es=st.es_alpha,
                     es=math.sqrt(total), coverage=plan.coverage, uncovered=plan.total_abs - plan.covered_abs,
                     status=status, flags=tuple(dict.fromkeys(flags)), items=plan.items)


def _refused(scope, method, horizon, window, st, exc) -> VarResult:
    return VarResult(scope_type=scope[0], scope=scope[1], method=method, horizon_days=horizon,
                     horizon_rule="" if horizon == 1 else ("sqrt_time" if method == "parametric" else st.horizon_rule),
                     window=window, window_start=None, window_end=None, n_obs=0, alpha_var=st.var_alpha, var=math.nan,
                     alpha_es=st.es_alpha, es=math.nan, coverage=math.nan, uncovered=math.nan, status="refused",
                     flags=(str(exc),))


def var_results(book: RiskBook, history: FactorHistory, settings: RiskSettings | None = None, *, proxies=None,
                scopes: Sequence[tuple[str, str]] | None = None) -> list[VarResult]:
    """Every VaR the settings ask for: each scope (fund, books, strategies) x method x horizon x window (``current``;
    ``stressed`` for the historical methods when asked; the liquidity-horizon ES when asked). A figure that cannot be
    computed is a ``refused`` result saying why, never a silent zero."""
    st = settings if settings is not None else book.settings
    out = []
    for scope in (scopes or book.scopes()):
        for method in st.methods:
            for horizon in st.horizons:
                try:
                    if method == "parametric":
                        out.append(parametric_var(book, history, st, horizon_days=horizon, scope=scope,
                                                  proxies=proxies))
                    else:
                        out.append(historical_var(book, history, st, method=method, horizon_days=horizon,
                                                  scope=scope, proxies=proxies))
                except ValueError as exc:
                    out.append(_refused(scope, method, horizon, "current", st, exc))
                if "stressed" in st.windows and method != "parametric":
                    try:
                        out.append(stressed_es(book, history, st, method=method, horizon_days=horizon, scope=scope,
                                               proxies=proxies))
                    except ValueError as exc:
                        out.append(_refused(scope, method, horizon, "stressed", st, exc))
        if st.liquidity_horizons:
            try:
                out.append(liquidity_horizon_es(book, history, st, scope=scope, proxies=proxies))
            except ValueError as exc:
                out.append(_refused(scope, "hs_sensitivity", 10, "current", st, exc))
    return out


def var_table(book: RiskBook, history: FactorHistory, settings: RiskSettings | None = None, *, proxies=None,
              results: Sequence[VarResult] | None = None) -> pd.DataFrame:
    """``VAR_COLUMNS``: one row per scope, method, horizon and window at the settings' levels (VaR at ``var_alpha``,
    ES at ``es_alpha``) and a second row with the levels crossed (VaR at ``es_alpha``, ES at ``var_alpha``), so that
    each measure is reported at both levels; percentages of the scope's NAV."""
    st = settings if settings is not None else book.settings
    res = list(results) if results is not None else var_results(book, history, st, proxies=proxies)
    rows = []
    for r in res:
        nav = book.nav_of(r.scope_type, r.scope)
        base = {"date": book.date, "as_of": _iso(book.as_of), "scope_type": r.scope_type, "scope": r.scope,
                "method": r.method, "horizon_days": r.horizon_days, "horizon_rule": r.horizon_rule,
                "window": r.window, "window_start": r.window_start, "window_end": r.window_end, "n_obs": r.n_obs,
                "coverage": r.coverage, "uncovered_rs_per_bp": r.uncovered, "status": r.status,
                "flags": _flags(r.flags)}
        rows.append({**base, "alpha_var": r.alpha_var, "var": r.var, "var_pct_nav": _pct(r.var, nav),
                     "alpha_es": r.alpha_es, "es": r.es, "es_pct_nav": _pct(r.es, nav)})
        if r.alpha_var != r.alpha_es and r.horizon_rule != "liquidity_horizons" and r.status != "refused":
            rows.append({**base, "alpha_var": r.alpha_es, "var": r.var_at_alpha_es,
                         "var_pct_nav": _pct(r.var_at_alpha_es, nav), "alpha_es": r.alpha_var,
                         "es": r.es_at_alpha_var, "es_pct_nav": _pct(r.es_at_alpha_var, nav)})
    frame = pd.DataFrame(rows, columns=list(VAR_COLUMNS))
    order = {m: k for k, m in enumerate(METHODS)}
    frame = frame.assign(_s=frame["scope_type"].map({"fund": 0, "book": 1, "strategy": 2}),
                         _m=frame["method"].map(order), _w=frame["window"].map({"current": 0, "stressed": 1}))
    frame = frame.sort_values(["_s", "scope", "_m", "horizon_days", "horizon_rule", "_w", "alpha_var"],
                              kind="mergesort")
    return frame.drop(columns=["_s", "_m", "_w"]).reset_index(drop=True)


# ---------------------------------------------------------------- Euler components (P6.7, by position and factor)


def _item_name(item) -> str:
    return f"{item.book}|{item.isin}|{'ois' if isinstance(item, RiskSwap) else item.state}"


def var_components(book: RiskBook, history: FactorHistory, settings: RiskSettings | None = None, *, proxies=None,
                   results: Sequence[VarResult] | None = None) -> pd.DataFrame:
    """Euler contributions (``VAR_COMPONENT_COLUMNS``) of each position (``book|isin|state``), book, strategy and, for
    the sensitivity and parametric methods, factor class to each current-window VaR and ES: parametric ``x_i (Sigma
    x)_i z / sigma_p`` (summing to the VaR exactly); historical ES ``-sum_s a_s PnL_{s,i} / m`` with the
    Acerbi-Tasche tail weights (summing to the ES exactly; ties to the earliest scenario); historical VaR the mean of
    a band of 5 scenarios around the VaR's, rescaled to sum to the VaR (U)."""
    st = settings if settings is not None else book.settings
    res = list(results) if results is not None else var_results(book, history, st, proxies=proxies)
    rows = []
    for r in res:
        if r.window != "current" or r.status == "refused" or r.horizon_rule == "liquidity_horizons":
            continue
        base = {"date": book.date, "as_of": _iso(book.as_of), "scope_type": r.scope_type, "scope": r.scope,
                "method": r.method, "horizon_days": r.horizon_days}
        plan = _plan(book, history, r.scope_type, r.scope, proxies, min_obs_hard=st.min_obs_hard)
        if r.method == "parametric":
            cov, _, _ = _covariance(plan, history, st)
            s = plan.S.sum(axis=0)
            sigma = math.sqrt(max(float(s @ cov @ s), 0.0)) if s.size else 0.0
            if not sigma:
                continue
            root = math.sqrt(r.horizon_days)
            k_var = _N.inv_cdf(st.var_alpha) * root / sigma
            k_es = root * _N.pdf(_N.inv_cdf(st.es_alpha)) / (1 - st.es_alpha) / sigma
            marginal = cov @ s
            by_item = plan.S @ marginal
            parts = {"position": {_item_name(i): (v * k_var, v * k_es) for i, v in zip(plan.items, by_item)}}
            cls: dict[str, tuple[float, float]] = {}
            for j, f in enumerate(plan.factors):
                c = _factor_class(f)
                a, b = cls.get(c, (0.0, 0.0))
                cls[c] = (a + s[j] * marginal[j] * k_var, b + s[j] * marginal[j] * k_es)
            parts["factor_class"] = cls
        else:
            pnl = r.scenario_pnl
            total = pnl.sum(axis=1)
            m = float(tail_size(total.size, st.es_alpha))
            w = tail_weights(total, st.es_alpha)
            es_items = -(w @ pnl) / m
            order = np.argsort(total, kind="mergesort")
            rank = math.ceil(tail_size(total.size, st.var_alpha)) - 1
            band = order[max(rank - 2, 0):rank + 3]
            band_total = -total[band].mean()
            var_items = (-pnl[band].mean(axis=0) * r.var / band_total) if band_total else np.full(pnl.shape[1],
                                                                                                    math.nan)
            parts = {"position": {_item_name(i): (v, e) for i, v, e in zip(r.items, var_items, es_items)}}
            if r.method == "hs_sensitivity":
                use = _window_rows(plan, st)
                x = history.changes[np.ix_(use, plan.cols)]
                dx = _horizon_changes(x, r.horizon_days, st.horizon_rule)
                scale = math.sqrt(r.horizon_days) if r.horizon_days > 1 and st.horizon_rule == "sqrt_time" else 1.0
                s = plan.S.sum(axis=0)
                cls = {}
                for c in sorted({_factor_class(f) for f in plan.factors}):
                    mask = np.array([_factor_class(f) == c for f in plan.factors])
                    p_c = -(dx[:, mask] @ s[mask]) * scale
                    v = -p_c[band].mean() * r.var / band_total if band_total else math.nan
                    cls[c] = (v, -(w @ p_c) / m)
                parts["factor_class"] = cls
        books: dict[str, list] = {}
        strategies: dict[str, list] = {}
        for item, (name, value) in zip(plan.items if r.method == "parametric" else r.items,
                                       parts["position"].items()):
            books.setdefault(item.book, []).append(value)
            strategies.setdefault(item.strategy, []).append(value)
        parts["book"] = {k: (_fsum(v[0] for v in vals), _fsum(v[1] for v in vals)) for k, vals in books.items()}
        parts["strategy"] = {k: (_fsum(v[0] for v in vals), _fsum(v[1] for v in vals))
                             for k, vals in strategies.items()}
        for ctype in ("position", "book", "strategy", "factor_class"):
            for name, (v, e) in sorted(parts.get(ctype, {}).items()):
                rows.append({**base, "component_type": ctype, "component": name, "var_contribution": float(v),
                             "es_contribution": float(e)})
    return pd.DataFrame(rows, columns=list(VAR_COMPONENT_COLUMNS))


# ---------------------------------------------------------------- back-tests (R2.8)


def chi2_sf(x: float, dof: int) -> float:
    """The chi-squared survival probability for 1 or 2 degrees of freedom without scipy: ``erfc(sqrt(x/2))`` and
    ``exp(-x/2)``."""
    if dof == 1:
        return math.erfc(math.sqrt(max(x, 0.0) / 2))
    if dof == 2:
        return math.exp(-max(x, 0.0) / 2)
    raise ValueError(f"chi2_sf is built for 1 or 2 degrees of freedom, not {dof}")


def _xlogy(x: float, y: float) -> float:
    return 0.0 if x == 0 else x * math.log(y)


def kupiec(T: int, x: int, p: float) -> tuple[float, float]:
    """Kupiec's proportion-of-failures statistic (F13; Kupiec 1995, standard form) of ``x`` exceptions in ``T``
    observations at an exception probability ``p``: ``-2 ln((1-p)^(T-x) p^x) + 2 ln((1-x/T)^(T-x) (x/T)^x)`` with
    ``0 ln 0 = 0``, and its chi-squared(1) p-value."""
    if T <= 0 or not 0 <= x <= T:
        raise ValueError(f"Kupiec needs 0 <= x <= T and T > 0, not x={x}, T={T}")
    phat = x / T
    lr = -2 * (_xlogy(T - x, 1 - p) + _xlogy(x, p)) + 2 * (_xlogy(T - x, 1 - phat) + _xlogy(x, phat))
    lr = max(lr, 0.0)
    return lr, chi2_sf(lr, 1)


def christoffersen(indicators: Sequence[int]) -> dict:
    """Christoffersen's independence test (F13; 1998): transition counts ``n_ij`` over consecutive observations, the
    first-order Markov likelihood ratio ``LR_ind`` (chi-squared(1)) with ``0 ln 0 = 0``."""
    seq = [int(bool(v)) for v in indicators]
    n = {"n00": 0, "n01": 0, "n10": 0, "n11": 0}
    for a, b in zip(seq, seq[1:]):
        n[f"n{a}{b}"] += 1
    n00, n01, n10, n11 = n["n00"], n["n01"], n["n10"], n["n11"]
    pi0 = n01 / (n00 + n01) if n00 + n01 else 0.0
    pi1 = n11 / (n10 + n11) if n10 + n11 else 0.0
    total = n00 + n01 + n10 + n11
    pi = (n01 + n11) / total if total else 0.0
    l0 = _xlogy(n00 + n10, 1 - pi) + _xlogy(n01 + n11, pi)
    l1 = _xlogy(n00, 1 - pi0) + _xlogy(n01, pi0) + _xlogy(n10, 1 - pi1) + _xlogy(n11, pi1)
    lr = max(-2 * (l0 - l1), 0.0)
    return {**n, "pi0": pi0, "pi1": pi1, "pi": pi, "lr_ind": lr, "p_ind": chi2_sf(lr, 1)}


def binomial_cdf(x: int, T: int, p: float) -> float:
    """``P(X <= x)`` for ``X ~ Binomial(T, p)``, summed in logs (``math.lgamma``) so that long samples do not
    overflow."""
    if x < 0:
        return 0.0
    if x >= T:
        return 1.0
    terms = [math.exp(math.lgamma(T + 1) - math.lgamma(k + 1) - math.lgamma(T - k + 1) + k * math.log(p)
                      + (T - k) * math.log1p(-p)) for k in range(0, x + 1)]
    return min(math.fsum(terms), 1.0)


def traffic_light(x: int, T: int = 250, p: float = 0.01) -> tuple[str, float, float]:
    """The Basel zone of ``x`` exceptions in ``T`` observations (MAR32.9 Table 1 at 250; MAR99.17 for other sizes):
    green while ``P(X <= x) < 95%``, amber while below 99.99%, red else, and green for no exception at all (below six
    observations ``P(X <= 0)`` is already above 95%: no exception cannot count against the model); the MAR32
    multiplier at exactly 250 observations and 1% (NaN otherwise); and the cumulative probability."""
    c = binomial_cdf(x, T, p)
    zone = "green" if x == 0 or c < GREEN_LIMIT else ("amber" if c < AMBER_LIMIT else "red")
    multiplier = math.nan
    if T == 250 and abs(p - 0.01) < 1e-12:
        multiplier = MAR32_MULTIPLIERS.get(x, MAR32_RED_MULTIPLIER)
    return zone, multiplier, c


def hypothetical_pnl(attribution) -> pd.DataFrame:
    """``date, book, pnl_hypothetical`` from the engine's attribution (``ATTRIBUTION_COLUMNS``): the market lines of
    the positions held from the previous close (``HYPOTHETICAL_LINES`` and the curve-factor lines ``curve_*``,
    ``krd_*``, ``ois_curve_*``, ``ois_krd_*``); trades, costs, funding, the benchmark and memo lines are left out. A
    ``fund`` row sums the books each day."""
    frame = _frame(attribution, "the attribution")
    _check_columns(frame, ("date", "book", "line", "amount"), rt.ATTRIBUTION_COLUMNS, "the attribution")
    keep = []
    for rec in frame.to_dict("records"):
        line = _text(rec.get("line"))
        memo = str(rec.get("memo")).strip().lower() in ("true", "1")
        keep.append(not memo and (line in HYPOTHETICAL_LINES or line.startswith(HYPOTHETICAL_PREFIXES)))
    kept = frame[keep].copy()
    kept["date"] = [_date(v, "the attribution's date") for v in kept["date"]]
    kept["amount"] = [_num(v, "the attribution's amount") for v in kept["amount"]]
    rows = []
    for (day, book), group in kept.groupby(["date", "book"], sort=True):
        rows.append({"date": day, "book": book, "pnl_hypothetical": _fsum(sorted(group["amount"]))})
    out = pd.DataFrame(rows, columns=["date", "book", "pnl_hypothetical"])
    fund = [{"date": day, "book": "fund", "pnl_hypothetical": _fsum(sorted(g["pnl_hypothetical"]))}
            for day, g in out.groupby("date", sort=True)]
    if len(set(out["book"])) > 1:
        out = pd.concat([out, pd.DataFrame(fund, columns=out.columns)], ignore_index=True)
    elif len(out):
        out = pd.concat([out, out.assign(book="fund")], ignore_index=True)
    return out.sort_values(["date", "book"], kind="mergesort").reset_index(drop=True)


def append_var_history(table, var_rows: pd.DataFrame) -> pd.DataFrame:
    """The VaR history (``VAR_HISTORY_COLUMNS``; a frame, a CSV path that may not exist yet, or None) with the day's
    ``VAR_COLUMNS`` rows of the current window (both levels: VaR at ``var_alpha`` and at ``es_alpha``, which the
    back-test picks by its ``alpha``) replacing any rows of the same date, sorted by date, scope, method, horizon and
    level."""
    if table is None or (not isinstance(table, pd.DataFrame) and not Path(table).exists()):
        old = pd.DataFrame(columns=list(VAR_HISTORY_COLUMNS))
    else:
        old = _frame(table, "the VaR history")
        _check_columns(old, VAR_HISTORY_COLUMNS, VAR_HISTORY_COLUMNS, "the VaR history")
    new = var_rows[(var_rows["window"] == "current") & (var_rows["horizon_rule"] != "liquidity_horizons")]
    new = new.loc[:, list(VAR_HISTORY_COLUMNS)].copy()
    days = {str(d) for d in new["date"]}
    old = old[~old["date"].astype(str).isin(days)].loc[:, list(VAR_HISTORY_COLUMNS)]
    out = pd.concat([old.astype(str), new.astype(str)], ignore_index=True)
    return out.sort_values(["date", "scope_type", "scope", "method", "horizon_days", "alpha_var"],
                           kind="mergesort").reset_index(drop=True)


def backtest(var_history, nav, attribution=None, *, alpha: float = VAR_ALPHA, window: int = WINDOW,
             method: str | None = None, calendar: BusinessCalendar | None = None, end=None
             ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Back-test the one-day VaR at ``alpha`` (R2.8): the VaR of business day ``d`` against the P&L of the next
    business day ``d+1``, actual (the engine's ``pnl``, ``nav.csv``) and hypothetical (``hypothetical_pnl`` of the
    attribution), counted apart, the larger count used (MAR32.18); an exception when ``PnL < -VaR`` strictly; a day
    whose VaR or P&L is missing counts as an exception. Over the last ``window`` pairs ending at ``end`` (default the
    last VaR with a next business day): Kupiec, Christoffersen (on the sequence of the larger count) and the traffic
    light. Returns ``(BACKTEST_COLUMNS, BACKTEST_SUMMARY_COLUMNS)``; a strategy scope's P&L is the sum of its books'
    (the VaR history's ``scope`` of type ``strategy`` needs every book of it in the NAV table under that strategy
    name: give the books' sums yourself otherwise)."""
    cal = calendar if calendar is not None else mumbai_settlement_calendar().lenient()
    hist = _frame(var_history, "the VaR history")
    _check_columns(hist, ("date", "scope_type", "scope", "method", "horizon_days", "alpha_var", "var"),
                   VAR_HISTORY_COLUMNS, "the VaR history")
    hist = hist[[_num(h, "horizon_days") == 1 and abs(_num(a, "alpha_var") - alpha) < 1e-12
                 for h, a in zip(hist["horizon_days"], hist["alpha_var"])]]
    if method is not None:
        hist = hist[hist["method"] == method]
    navf = _frame(nav, "the NAV table")
    _check_columns(navf, ("date", "book", "pnl"), rt.NAV_COLUMNS, "the NAV table")
    actual = {(_date(r["date"], "the NAV date"), _text(r["book"])): _num(r["pnl"], "pnl", blank=math.nan)
              for r in navf.to_dict("records")}
    books = sorted({b for _, b in actual})
    if "fund" not in books:
        for day in sorted({d for d, _ in actual}):
            actual[(day, "fund")] = _fsum(actual[(day, b)] for b in books if (day, b) in actual)
    hypo = {}
    if attribution is not None:
        for rec in hypothetical_pnl(attribution).to_dict("records"):
            hypo[(rec["date"], rec["book"])] = rec["pnl_hypothetical"]
    rows = []
    last = as_date(end) if end is not None else None
    for rec in hist.to_dict("records"):
        d = _date(rec["date"], "the VaR history's date")
        nxt = cal.add_business_days(d, 1)
        if last is not None and nxt > last:
            continue
        scope_type, scope = _text(rec["scope_type"]), _text(rec["scope"])
        key = "fund" if scope_type == "fund" else scope
        var = _num(rec["var"], "var", blank=math.nan)
        pa = actual.get((nxt, key), math.nan)
        ph = hypo.get((nxt, key), math.nan) if attribution is not None else math.nan
        missing = not math.isfinite(var) or not math.isfinite(pa) or (attribution is not None
                                                                       and not math.isfinite(ph))
        ex_a = missing or pa < -var
        ex_h = (missing or ph < -var) if attribution is not None else False
        rows.append({"var_date": d, "pnl_date": nxt, "scope_type": scope_type, "scope": scope,
                     "method": _text(rec["method"]), "var": var, "pnl_actual": pa, "pnl_hypothetical": ph,
                     "exception_actual": bool(ex_a), "exception_hypothetical": bool(ex_h), "missing": bool(missing)})
    detail = pd.DataFrame(rows, columns=list(BACKTEST_COLUMNS))
    if len(detail):
        known = set(actual) | set(hypo)
        latest = max(d for d, _ in known) if known else None
        if latest is not None and last is None:
            detail = detail[detail["pnl_date"] <= latest]
        detail = detail.sort_values(["scope_type", "scope", "method", "var_date"], kind="mergesort")
        detail = detail.reset_index(drop=True)
    summary = []
    p = 1 - alpha
    for (scope_type, scope, method_), group in detail.groupby(["scope_type", "scope", "method"], sort=True):
        g = group.tail(window)
        T = len(g)
        xa, xh = int(g["exception_actual"].sum()), int(g["exception_hypothetical"].sum())
        use = g["exception_hypothetical"] if xh > xa else g["exception_actual"]
        x = max(xa, xh)
        lr, pv = kupiec(T, x, p)
        ch = christoffersen(use.astype(int).tolist())
        lr_cc = lr + ch["lr_ind"]
        zone, mult, cum = traffic_light(x, T, p)
        notes = []
        if T < window:
            notes.append(f"{T} of {window} observations")
        if attribution is None:
            notes.append("no attribution: hypothetical P&L not counted")
        if int(g["missing"].sum()):
            notes.append(f"{int(g['missing'].sum())} day(s) with a missing VaR or P&L counted as exceptions")
        summary.append({"end": g["var_date"].iloc[-1], "scope_type": scope_type, "scope": scope, "method": method_,
                        "alpha": alpha, "observations": T, "exceptions_actual": xa, "exceptions_hypothetical": xh,
                        "exceptions": x, "expected": T * p, "kupiec_lr": lr, "kupiec_p": pv, "n00": ch["n00"],
                        "n01": ch["n01"], "n10": ch["n10"], "n11": ch["n11"], "christoffersen_lr": ch["lr_ind"],
                        "christoffersen_p": ch["p_ind"], "lr_cc": lr_cc, "lr_cc_p": chi2_sf(lr_cc, 2),
                        "cumulative_probability": cum, "zone": zone, "multiplier": mult, "notes": "; ".join(notes)})
    return detail, pd.DataFrame(summary, columns=list(BACKTEST_SUMMARY_COLUMNS))


# ---------------------------------------------------------------- one call: every table of a day's risk


RESULT_FILES = ("position_risk", "sensitivities", "key_rates", "credit", "jtd", "risk_summary", "var",
                "var_components", "stress", "stress_positions", "stress_worst", "liquidity", "liquidity_summary",
                "funding_liquidity")
UNVERIFIED_RULES = (
    "key tenors 0.25-50 years (FRTB's 0.25-30 plus 40 and 50); OIS key tenors to the curve's last node",
    "zero-rate bumps of the fitted curves, analytic for fixed flows, central +-0.5 bp otherwise",
    "SDLs on the G-sec curve plus an sdl_spread factor (sdl_mode spread)",
    "CS01 on the z-spread to the reference curve; callable bonds valued to maturity (no option model)",
    "FRB index risk placed on the G-sec key rates at the next reset; margin risk on frb_dm:<isin>",
    "JTD with Basel LGDs, senior by default; GoI, SDLs and T-bills excluded; no netting",
    "VaR ceil(n(1-a))-th largest loss with m exact; ES the Acerbi-Tasche tail mean",
    "gaps of 2-5 business days scaled by 1/sqrt(g), longer gaps break a series; a tenor outside a fit's range "
    "takes the nearest in-range change; 60 observations the least for a VaR",
    "the window: the latest dates on which every factor of the scope with at least min_obs_hard observations has "
    "a change (fewer: left out, coverage below one)",
    "EWMA lambda 0.94 over up to 550 observations, the weights normalised; the sample covariance with ddof 1",
    "the stressed window searched by the sensitivity method on the factors whose history starts within 30 days of "
    "the earliest",
    "liquidity: 20% participation, ADV of 20 business days of the marks' volumes, else the user's table, else "
    "example proxies; stressed spreads 3 times; LVaR = VaR + stressed cost",
    "every scenario size marked U in stress_scenarios.csv (examples to be replaced by the fund's policy)",
)


@dataclass(frozen=True, eq=False)
class RiskResult:
    """A day's risk: the book, the factor history it used and every table (``RESULT_FILES``), with notes."""

    book: RiskBook
    history: FactorHistory
    position_risk: pd.DataFrame
    sensitivities: pd.DataFrame
    key_rates: pd.DataFrame
    credit: pd.DataFrame
    jtd: pd.DataFrame
    risk_summary: pd.DataFrame
    var: pd.DataFrame
    var_components: pd.DataFrame
    stress: pd.DataFrame
    stress_positions: pd.DataFrame
    stress_worst: pd.DataFrame
    liquidity: pd.DataFrame
    liquidity_summary: pd.DataFrame
    funding_liquidity: pd.DataFrame
    var_results: tuple[VarResult, ...] = ()
    notes: tuple[str, ...] = ()


def _preferred_var(results: Sequence[VarResult], settings: RiskSettings) -> dict[tuple[str, str], float]:
    """The one-day current VaR of each scope for the liquidity-adjusted VaR: full revaluation, else the sensitivity
    method, else the parametric one, whichever was computed."""
    out: dict[tuple[str, str], float] = {}
    for method in ("hs_full", "hs_sensitivity", "parametric"):
        for r in results:
            key = (r.scope_type, r.scope)
            if key in out or r.method != method or r.window != "current" or r.horizon_days != 1 or \
                    r.horizon_rule == "liquidity_horizons" or not math.isfinite(r.var):
                continue
            out[key] = r.var
    return out


def _notes(book: RiskBook, history: FactorHistory, results: Sequence[VarResult], ctx: RiskContext) -> list[str]:
    notes = [f"risk of {book.date} as of {book.as_of:%Y-%m-%d %H:%M} under the access preset {book.access!r} "
             "(the fund's legal form is a question for counsel)", *book.notes, *history.notes]
    flags: dict[str, int] = {}
    for item in book.items:
        for f in item.flags:
            flags[f] = flags.get(f, 0) + 1
    for f, n in sorted(flags.items()):
        notes.append(f"{n} position(s) flagged {f}")
    statuses: dict[str, int] = {}
    for r in results:
        statuses[r.status] = statuses.get(r.status, 0) + 1
    for s, n in sorted(statuses.items()):
        if s != "ok":
            notes.append(f"{n} VaR figure(s) {s}")
    if any("coarse" in r.flags for r in results):
        notes.append("some VaR figures stand on a history flagged coarse (labelled in their flags): plumbing, not a "
                     "measure of risk")
    if not history.dates:
        notes.append("no factor history public by as_of: no VaR could be computed (risk needs a daily history of "
                     "fitted curves, licensed)")
    if not (ctx.settings.access.repo or ctx.settings.access.treps):
        notes.append(f"funding stress and ALM: not_applicable under {ctx.settings.access.name} (it does not borrow)")
    notes.extend(f"UNVERIFIED: {rule}" for rule in UNVERIFIED_RULES)
    return notes


def run_risk(positions, ctx: RiskContext, *, day=None, scenarios=None, history: FactorHistory | None = None,
             participation: float | None = None, stress_multiplier: float | None = None) -> RiskResult:
    """Everything of R1-R4 for one day's positions in one call: the book (``risk_book``), the factor history as of
    ``ctx.as_of`` (``factor_history``, unless given), the sensitivity tables, every VaR and ES asked, their Euler
    components, the stress scenarios (``scenarios``: ``stress.ScenarioRow`` objects, a table, or None for the shipped
    ones available and effective by ``as_of``), liquidity and funding liquidity."""
    from cr_strat import stress as st
    book = risk_book(positions, ctx, day=day)
    hist = history if history is not None else factor_history(ctx, book)
    results = tuple(var_results(book, hist, proxies=ctx.proxies))
    if scenarios is None:
        rows = st.read_scenarios(as_of=ctx.as_of)
    elif isinstance(scenarios, (list, tuple)) and all(isinstance(r, st.ScenarioRow) for r in scenarios):
        day_ = ctx.as_of.date()
        rows = [r for r in scenarios if (r.available_from is None or r.available_from <= day_)
                and (r.effective_from is None or r.effective_from <= day_)]
    else:
        rows = st.read_scenarios(scenarios, as_of=ctx.as_of)
    part = st.PARTICIPATION if participation is None else participation
    mult = st.STRESS_SPREAD_MULTIPLIER if stress_multiplier is None else stress_multiplier
    sctx = st.StressContext(risk=ctx, participation=part, multiplier=mult)
    stress, stress_positions = st.run_stress(book, rows, sctx)
    liq, liq_summary = st.liquidity(book, ctx, participation=part, multiplier=mult,
                                    var_by_scope=_preferred_var(results, book.settings))
    funding_liq = st.funding_liquidity(ctx.funding, None, book, ctx, rows)
    return RiskResult(book=book, history=hist, position_risk=position_risk(book), sensitivities=sensitivities(book),
                      key_rates=key_rate_table(book), credit=credit_table(book), jtd=jtd_table(book),
                      risk_summary=summary_table(book), var=var_table(book, hist, results=results),
                      var_components=var_components(book, hist, results=results), stress=stress,
                      stress_positions=stress_positions, stress_worst=st.stress_worst(stress), liquidity=liq,
                      liquidity_summary=liq_summary, funding_liquidity=funding_liq, var_results=results,
                      notes=tuple(_notes(book, hist, results, ctx)))


def write_risk(result: RiskResult, out_dir, *, history: bool = False) -> list[Path]:
    """Write every table of ``result`` as a CSV (``RESULT_FILES``; with ``history`` also ``factor_changes.csv``,
    ``FACTOR_CHANGE_COLUMNS``) and its notes to ``notes.txt`` in ``out_dir``; the same result gives the same bytes.
    Returns the paths written."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in RESULT_FILES:
        path = out / f"{name}.csv"
        getattr(result, name).to_csv(path, index=False, date_format="%Y-%m-%d")
        paths.append(path)
    if history:
        path = out / "factor_changes.csv"
        result.history.frame().to_csv(path, index=False, date_format="%Y-%m-%d")
        paths.append(path)
    notes = out / "notes.txt"
    notes.write_text("".join(f"{n}\n" for n in result.notes), encoding="utf-8")
    paths.append(notes)
    return paths
