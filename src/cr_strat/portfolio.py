"""Portfolio construction across strategy books: covariance of strategy returns, capital allocation (fixed weights,
inverse volatility, equal risk contribution on volatility or on ES, DV01 / CS01 / VaR budgets), rebalancing and
drawdown de-risking rules, position sizing, DV01-neutral hedges, and Euler risk contributions.

**Strategy returns** (P6.1 of the risk specification of 24 Nov 2026). One book per strategy is the recommended set-up;
``strategy_books`` (``risk.STRATEGY_BOOK_COLUMNS``) maps books to strategies when not. A strategy's daily return is
its books' NAV change net of flows over the start-of-day capital, ``(NAV - NAV_prev - flow) / (NAV_prev + flow)``, the
engine's own definition (``returns``, A4), from the engine's ``nav.csv``; a day without capital has no return (NaN).
**Covariance**: EWMA (zero mean, decay 0.94, at most 550 observations, the weights normalised to sum to one:
RiskMetrics Technical Document 1996, sec. 5.3, as ``risk.ewma_covariance``) or the equal-weighted sample of the last
``window`` days (mean removed, ddof 1); a pair uses only the days on which both strategies had capital (pairwise,
UNVERIFIED), so the matrix may not be positive definite: ``make_positive_definite`` shrinks it towards its diagonal,
``(1 - delta) S + delta diag(S)``, with the smallest ``delta`` in steps of 0.05 that makes every eigenvalue positive
(UNVERIFIED; Ledoit and Wolf's optimal shrinkage is not built).

**Allocation** (P6.2), over the strategies the legal form allows (``feasible_strategies``, P6.8: under
``unregulated_company`` S7's OIS views get no weight, S3 and S5 run long-only against paid OIS, S1 and S2 unfunded;
under ``nbfc_non_pd`` all ten): ``fixed`` weights (``FIXED_WEIGHT_COLUMNS``, the latest row of each strategy in force);
``inverse_vol`` ``w_i ~ 1/sigma_i``; ``erc_vol``, equal (or budgeted) risk contributions to volatility, ``w_i (Sigma
w)_i = b_i w' Sigma w`` (Maillard, Roncalli and Teiletche, J. Portfolio Management 36(4), 2010), solved by cyclical
coordinate descent ``y_i <- (-a_i + sqrt(a_i^2 + 4 Sigma_ii b_i)) / (2 Sigma_ii)``, ``a_i = sum_{j != i} Sigma_ij
y_j`` (Griveau-Billion, Richard and Roncalli, 2013), with Newton's method on ``F(y) = 0.5 y' Sigma y - sum b_i ln y_i``
(Spinu, 2013) as the check, both to ``w = y / sum y``; ``erc_es``, budgeted shares of the historical ES (Euler
contributions with the Acerbi-Tasche tail weights) by the damped fixed point ``w_i <- w_i (b_i / s_i)^p``, ``p``
starting at 1/2 and halved (down to 1/1024) whenever the error rises (not proven to converge, and exact parity need not
exist for a historical ES: refused after ``max_iter`` with the last error, UNVERIFIED); ``budget``: no weights, each
strategy's scale ``min(1, budget / |metric|)`` from ``BUDGET_COLUMNS`` (DV01, CS01, VaR or ES, the sizing a strategy
reads, P6.5). Bounds (``BOUNDS_COLUMNS``) other than 0 and 1 are applied by clipping and re-solving on the strategies
left free (UNVERIFIED).

**Rebalancing and de-risking** (P6.3, P6.4). ``rebalance_due``: on the first business day of a month, or when a weight
is more than 5 percentage points or 20% from its target, or both (UNVERIFIED rules), never while a hard breach in the
fund's or the strategy's scope is open (``limits.BREACH_COLUMNS``); ``rebalance_cost`` scales each strategy's exit cost
(``stress.liquidity``) by the share of it traded. ``drawdown_scale``: the drawdown ``I / max I - 1`` mapped to a risk
scale with hysteresis (UNVERIFIED example levels: half at -3%, a quarter at -5%, back to one only above -1.5%; the
scale only falls while the drawdown deepens); ``stop_loss_scale``: a strategy's 20-day P&L at or below -2% of the
fund's NAV halves its budgets until it is back above half the trigger (UNVERIFIED).

**Sizing and hedges** (P6.5, P6.6). ``size_to_dv01`` ``face = target / (DV01 per 100 / 100)`` (and CS01);
``size_to_vol`` ``target / current`` capped at 2 (UNVERIFIED); ``round_to_lot`` down to the market lot (Rs 5 crore on
NDS-OM, UNVERIFIED, a parameter). ``hedge_ois_notional`` by default the notional whose DV01 as ``ois.ois_npv``
measures it (the continuously compounded zero rates up 1 bp: the engine's, the gate's and the risk tables' measure)
equals the DV01 given, ``|DV01| / DV01 per rupee of a par swap``; ``method="annuity"`` ``N = DV01 / (A x 1e-4)`` with
the swap's annuity re-based to its start (``asset_swaps.hedge_notional``'s rule for one bond, which offsets a 1 bp move
of the swap's par rate and is 1.4% to 6% larger on the engine's measure for 3- to 48-month swaps on the tests' curve);
``hedge_gsec_face`` ``-DV01 / (DV01_h / 100)``; ``key_rate_hedge`` minimises ``|| KRD_book + K h ||^2`` by least
squares or, with every hedge in its allowed direction, by non-negative least squares (``nnls``: the active-set method
of Lawson and Hanson, Solving Least Squares Problems, 1974, written in numpy). Every hedge or rebalance still passes
the pre-trade gate (``limits``).

**Euler contributions** (P6.7; Tasche 1999 and 2008, Garman, Risk 1997): parametric ``x_i (Sigma x)_i z / sigma_p``
summing to the VaR; historical ES ``-sum_s a_s PnL_{s,i} / m`` with the Acerbi-Tasche weights (``risk.tail_weights``,
ties to the earliest scenario) summing to the ES exactly; historical VaR the mean of a band of 5 scenarios centred on
the VaR's, rescaled to sum to the VaR (UNVERIFIED: one scenario's split is too noisy).

**Limits.** Numpy only, deterministic (no random starts). Tested on the specification's vectors and synthetic books
only; no real strategy history exists yet, so every covariance here is of back-tests or paper trading. The rules marked
UNVERIFIED (``UNVERIFIED_RULES``) are choices for the fund, each a named parameter. Library code never reads the clock.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

from cr_strat import returns as rt
from cr_strat import risk as rk
from cr_strat.ois import MIBOR_OIS, OisConventions, OisSwap, as_time, ois_npv, ois_par_rate, ois_schedule

# ---------------------------------------------------------------- conventions (each UNVERIFIED unless said)

EWMA_LAMBDA = rk.EWMA_LAMBDA  # RiskMetrics 1996, Table 5.9 (verified)
EWMA_MAX_OBS = rk.EWMA_MAX_OBS  # RiskMetrics: 550 daily observations (verified)
WINDOW = 250  # the sample covariance's window
MIN_OBS = 20  # UNVERIFIED: fewer joint observations of a pair and its covariance is refused
SHRINK_STEP = 0.05  # UNVERIFIED: the shrinkage towards the diagonal, in these steps
COV_METHODS = ("ewma", "sample")
METHODS = ("fixed", "inverse_vol", "erc_vol", "erc_es", "budget")
ERC_ALGORITHMS = ("ccd", "newton")
BUDGET_METRICS = ("dv01", "cs01", "var", "es")
DRAWDOWN_LEVELS = ((-0.03, 0.5), (-0.05, 0.25))  # UNVERIFIED example levels
DRAWDOWN_RECOVER = -0.015  # UNVERIFIED: back to a scale of one only above this drawdown
STOP_LOSS_DAYS = 20  # UNVERIFIED
STOP_LOSS_TRIGGER = -0.02  # UNVERIFIED: a 20-day loss of 2% of the fund's NAV
STOP_LOSS_SCALE = 0.5  # UNVERIFIED: halves the strategy's budgets
MAX_VOL_SCALE = 2.0  # UNVERIFIED
GSEC_LOT = 5e7  # UNVERIFIED: Rs 5 crore, the NDS-OM market lot for G-secs
REBALANCE_RULES = ("calendar", "threshold", "both")
REBALANCE_BAND_ABS = 0.05  # UNVERIFIED: 5 percentage points
REBALANCE_BAND_REL = 0.20  # UNVERIFIED: 20% of the target weight
HS_VAR_BAND = 5  # UNVERIFIED: scenarios around the VaR's in its Euler split
# the strategies of docs/rates_data_sources.md; a strategy is matched by its name starting with the code (S7, s7_...)
STRATEGY_CODES = {
    "S1": "sovereign asset swaps (SDLs or off-the-run G-secs against paid OIS)",
    "S2": "front-end AAA bonds, CDs and CP against OIS",
    "S3": "rich/cheap to a fitted G-sec curve",
    "S4": "supply and RBI-operation cycle",
    "S5": "long end (10-year against 30- to 50-year G-secs)",
    "S6": "SDL relative value",
    "S7": "policy path against OIS",
    "S8": "high-grade credit spreads and new issues",
    "S9": "forced selling after rating actions",
    "S10": "selective AA and A carry",
}

ALLOCATION_COLUMNS = ("date", "as_of", "strategy", "method", "allowed", "weight", "vol", "risk_contribution",
                      "risk_share", "budget_metric", "budget", "scale", "notes")
CORRELATION_COLUMNS = ("date", "as_of", "strategy", "other", "correlation", "covariance", "observations")
FIXED_WEIGHT_COLUMNS = ("strategy", "weight", "effective_from", "approved_by")
BUDGET_COLUMNS = ("strategy", "metric", "budget", "unit", "effective_from")
BOUNDS_COLUMNS = ("strategy", "min_weight", "max_weight")
UNVERIFIED_RULES = (
    "covariance pairwise on the days both strategies had capital; shrunk to the diagonal in steps of 0.05 until "
    "positive definite; 20 joint observations at the least",
    "ERC by cyclical coordinate descent, checked by Newton's method; bounds by clipping and re-solving",
    "ES parity by the damped fixed point w <- w (b/s)^p, p = 0.5 halved (to 1/1024 at least) whenever the error "
    "rises, refused after 500 iterations",
    "budget scales min(1, budget/|metric|), no scaling up unless asked",
    "rebalancing on the first business day of a month or a band of 5 percentage points or 20%, never while a hard "
    "breach in scope is open",
    "drawdown scale 0.5 at -3%, 0.25 at -5%, back to 1 above -1.5%; a strategy's 20-day loss of 2% of fund NAV "
    "halves its budgets until back above -1%",
    "volatility target scale capped at 2; faces rounded down to Rs 5 crore lots",
    "historical VaR components from a band of 5 scenarios around the VaR's",
)

_N = NormalDist()


# ---------------------------------------------------------------- small helpers


def _num(value, where: str) -> float:
    return rk._num(value, where)


def _as_series(values, name: str) -> pd.Series:
    if isinstance(values, pd.Series):
        out = values.astype(float)
    elif isinstance(values, Mapping):
        out = pd.Series({str(k): float(v) for k, v in values.items()}, dtype=float)
    else:
        raise TypeError(f"{name} must be a pandas Series or a mapping of strategy to value")
    return out


def _as_matrix(cov) -> tuple[np.ndarray, list[str]]:
    """``(matrix, labels)`` of a covariance given as a DataFrame (labels from its index) or a square array."""
    if isinstance(cov, pd.DataFrame):
        if list(cov.index) != list(cov.columns):
            raise ValueError("a covariance frame needs the same labels in its index and columns, in the same order")
        labels = [str(c) for c in cov.columns]
        m = cov.to_numpy(dtype=float)
    else:
        m = np.asarray(cov, dtype=float)
        labels = [str(i) for i in range(m.shape[0])] if m.ndim == 2 else []
    if m.ndim != 2 or m.shape[0] != m.shape[1] or not m.shape[0]:
        raise ValueError(f"a covariance must be a non-empty square matrix, not of shape {m.shape}")
    if not np.all(np.isfinite(m)):
        raise ValueError("the covariance has missing or infinite entries (too few joint observations of a pair?)")
    if not np.allclose(m, m.T, rtol=1e-12, atol=0.0):
        raise ValueError("the covariance is not symmetric")
    if np.any(np.diag(m) <= 0):
        bad = [labels[i] for i in np.flatnonzero(np.diag(m) <= 0)]
        raise ValueError(f"the covariance gives {bad} no variance: a strategy needs a positive volatility")
    return m, labels


def _budget_vector(budgets, labels: Sequence[str]) -> np.ndarray:
    """Risk budgets in ``labels``' order: equal by default; given ones must be positive and are scaled to sum to one."""
    if budgets is None:
        return np.full(len(labels), 1.0 / len(labels))
    series = _as_series(budgets, "budgets")
    missing = [s for s in labels if s not in series.index]
    if missing:
        raise ValueError(f"no risk budget for {missing}")
    b = np.array([float(series[s]) for s in labels])
    if not (np.all(np.isfinite(b)) and np.all(b > 0)):
        raise ValueError(f"risk budgets must be positive numbers, not {dict(zip(labels, b))}")
    return b / b.sum()


def _when(as_of) -> datetime:
    return as_time(as_of, "as_of")


def _iso(when: datetime) -> str:
    return when.isoformat(timespec="minutes")


# ---------------------------------------------------------------- strategy returns and covariance (P6.1)


def strategy_returns(nav, strategy_books=None, *, as_of=None) -> pd.DataFrame:
    """Daily returns by strategy (a date-indexed frame, one column per strategy, sorted) from the engine's NAV rows
    (``returns.NAV_COLUMNS``: ``date``, ``book``, ``nav``, ``flow``; the ``fund`` rows are left out): each strategy's
    books (``strategy_books``, as mapped on each date; default each book its own strategy) summed, ``(NAV - NAV_prev -
    flow) / (NAV_prev + flow)``; NaN on a day without capital. A book with no row on a date after one with a row (a gap
    in its rows, or the end of its history) gives its strategy no return that day (NaN), never -100%, and the day
    after a gap none either (no previous NAV); a book's first row counts from its flow. With ``as_of``, only dates on
    or before its day."""
    frame = rk._frame(nav, "the NAV table")
    rk._check_columns(frame, ("date", "book", "nav"), ("date", "book", "nav", "flow"), "the NAV table")
    rows = []
    for _, rec in rk._lines(frame):
        book = rk._text(rec.get("book"))
        if book == "fund":
            continue
        day = rk._date(rec.get("date"), "the NAV table's date")
        rows.append((day, book, rk._num(rec.get("nav"), f"the NAV of {book} on {day}"),
                     rk._num(rec.get("flow"), f"the flow of {book} on {day}", blank=0.0)))
    if as_of is not None:
        last = _when(as_of).date()
        rows = [r for r in rows if r[0] <= last]
    if not rows:
        return pd.DataFrame()
    table = pd.DataFrame(rows, columns=["date", "book", "nav", "flow"])
    if table.duplicated(["date", "book"]).any():
        dup = table[table.duplicated(["date", "book"], keep=False)].iloc[0]
        raise ValueError(f"the NAV table gives book {dup['book']} two rows on {dup['date']}")
    navs = table.pivot(index="date", columns="book", values="nav").sort_index().sort_index(axis=1)
    flows = table.pivot(index="date", columns="book", values="flow").reindex_like(navs).fillna(0.0)
    present = navs.notna()
    before = present.shift(1, fill_value=False)
    navs = navs.fillna(0.0)
    prev = navs.shift(1).fillna(0.0)
    mapping = rk.read_strategy_books(strategy_books) if strategy_books is not None else None

    def strategy_of(book: str, day: date) -> str:
        if mapping is None:
            return book
        live = mapping[(mapping["book"] == book) & (mapping["effective_from"] <= day)]
        return str(live["strategy"].iloc[-1]) if len(live) else book

    out: dict[str, dict[date, float]] = {}
    for day in navs.index:
        groups: dict[str, list[str]] = {}
        gaps: set[str] = set()
        for book in navs.columns:
            here, was = bool(present.loc[day, book]), bool(before.loc[day, book])
            if here:
                groups.setdefault(strategy_of(book, day), []).append(book)
            elif was:  # no row today after one yesterday: a gap or the book's end, not a -100% day
                gaps.add(strategy_of(book, day))
        for strategy in sorted(gaps):
            out.setdefault(strategy, {})[day] = math.nan
        for strategy, books in groups.items():
            if strategy in gaps:
                continue
            n = math.fsum(navs.loc[day, b] for b in books)
            p = math.fsum(prev.loc[day, b] for b in books if before.loc[day, b])
            f = math.fsum(flows.loc[day, b] for b in books)
            base = p + f
            after_gap = any(not before.loc[day, b] and _had_row(present, day, b) for b in books)
            out.setdefault(strategy, {})[day] = (n - p - f) / base if base > 0 and not after_gap else math.nan
    result = pd.DataFrame(out).sort_index().sort_index(axis=1)
    result.index.name = "date"
    return result


def _had_row(present: pd.DataFrame, day, book: str) -> bool:
    """Whether ``book`` has a row on a date before ``day`` (a row after a gap has no previous NAV to count from)."""
    column = present[book]
    return bool(column[column.index < day].any())


def _pair_rows(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return np.flatnonzero(np.isfinite(x) & np.isfinite(y))


def covariance(returns, *, method: str = "ewma", lam: float = EWMA_LAMBDA, window: int = WINDOW,
               max_obs: int = EWMA_MAX_OBS, min_obs: int = MIN_OBS) -> tuple[pd.DataFrame, pd.DataFrame]:
    """``(covariance, observations)`` of the strategies' daily returns (a date-indexed frame, oldest first), both as
    frames by strategy: EWMA (zero mean, the latest ``max_obs`` joint days of each pair, weights ``(1 - lam) lam^k``
    normalised to sum to one) or the sample (the latest ``window`` joint days, pairwise means removed, ddof 1). A pair
    with fewer than ``min_obs`` joint days is refused, naming it."""
    rk._choice(method, COV_METHODS, "the covariance method")
    if not 0 < lam < 1:
        raise ValueError(f"lambda must be between 0 and 1, not {lam}")
    frame = returns if isinstance(returns, pd.DataFrame) else pd.DataFrame(returns)
    frame = frame.sort_index()
    labels = [str(c) for c in frame.columns]
    if not labels:
        raise ValueError("no strategy returns to estimate a covariance from")
    x = frame.to_numpy(dtype=float)
    k = len(labels)
    cov = np.full((k, k), math.nan)
    obs = np.zeros((k, k), dtype=int)
    for i in range(k):
        for j in range(i, k):
            rows = _pair_rows(x[:, i], x[:, j])
            rows = rows[-(max_obs if method == "ewma" else window):]
            n = len(rows)
            obs[i, j] = obs[j, i] = n
            if n < max(min_obs, 2):
                raise ValueError(f"the covariance of {labels[i]} and {labels[j]} has {n} joint observation(s), below "
                                 f"the {max(min_obs, 2)} it needs")
            a, b = x[rows, i], x[rows, j]
            if method == "ewma":
                w = (1 - lam) * lam ** np.arange(n - 1, -1, -1, dtype=float)
                w = w / w.sum()
                value = float(np.sum(w * a * b))
            else:
                value = float(np.sum((a - a.mean()) * (b - b.mean())) / (n - 1))
            cov[i, j] = cov[j, i] = value
    return (pd.DataFrame(cov, index=labels, columns=labels), pd.DataFrame(obs, index=labels, columns=labels))


def make_positive_definite(cov, *, step: float = SHRINK_STEP) -> tuple[pd.DataFrame | np.ndarray, float]:
    """``((1 - delta) S + delta diag(S), delta)`` with the smallest ``delta`` in steps of ``step`` (0 first) that makes
    every eigenvalue positive (above ``1e-12`` of the mean variance); a matrix that is not even positive on its
    diagonal is refused. The input's type (frame or array) is kept."""
    m, labels = _as_matrix(cov)
    if not 0 < step <= 1:
        raise ValueError(f"the shrinkage step must be above 0 and at most 1, not {step}")
    floor = 1e-12 * float(np.mean(np.diag(m)))
    steps = int(round(1 / step))
    for k in range(steps + 1):
        delta = min(k * step, 1.0)
        shrunk = (1 - delta) * m + delta * np.diag(np.diag(m))
        if float(np.min(np.linalg.eigvalsh(shrunk))) > floor:
            out = pd.DataFrame(shrunk, index=labels, columns=labels) if isinstance(cov, pd.DataFrame) else shrunk
            return out, float(delta)
    raise ValueError("the covariance cannot be made positive definite by shrinking it to its diagonal")


def correlation(cov) -> pd.DataFrame | np.ndarray:
    """The correlation matrix of a covariance (frame in, frame out)."""
    m, labels = _as_matrix(cov)
    vol = np.sqrt(np.diag(m))
    corr = m / np.outer(vol, vol)
    np.fill_diagonal(corr, 1.0)
    return pd.DataFrame(corr, index=labels, columns=labels) if isinstance(cov, pd.DataFrame) else corr


def correlation_table(cov: pd.DataFrame, observations: pd.DataFrame | None = None, *, as_of) -> pd.DataFrame:
    """The covariance and correlation of every pair (``CORRELATION_COLUMNS``), with its joint observations."""
    when = _when(as_of)
    m, labels = _as_matrix(cov)
    corr = correlation(m)
    rows = []
    for i, a in enumerate(labels):
        for j, b in enumerate(labels):
            rows.append({"date": when.date(), "as_of": _iso(when), "strategy": a, "other": b,
                         "correlation": float(corr[i, j]), "covariance": float(m[i, j]),
                         "observations": int(observations.loc[a, b]) if observations is not None else math.nan})
    return pd.DataFrame(rows, columns=list(CORRELATION_COLUMNS))


# ---------------------------------------------------------------- allocation (P6.2)


def inverse_vol_weights(vols) -> pd.Series:
    """``w_i = (1/sigma_i) / sum_j (1/sigma_j)`` (F17); every volatility must be positive."""
    s = _as_series(vols, "vols")
    if not (np.all(np.isfinite(s.to_numpy())) and np.all(s.to_numpy() > 0)):
        raise ValueError(f"inverse-volatility weights need positive volatilities, not {s.to_dict()}")
    inv = 1.0 / s
    return inv / inv.sum()


def _erc_ccd(m: np.ndarray, b: np.ndarray, tol: float, max_iter: int) -> tuple[np.ndarray, int]:
    y = 1.0 / np.sqrt(np.diag(m))
    for k in range(1, max_iter + 1):
        old = y.copy()
        for i in range(len(y)):
            a = float(m[i] @ y - m[i, i] * y[i])
            y[i] = (-a + math.sqrt(a * a + 4 * m[i, i] * b[i])) / (2 * m[i, i])
        if float(np.max(np.abs(y - old))) < tol:
            return y / y.sum(), k
    raise ValueError(f"equal risk contribution by coordinate descent did not converge in {max_iter} sweeps (last "
                     f"change {float(np.max(np.abs(y - old))):.3e})")


def _erc_newton(m: np.ndarray, b: np.ndarray, tol: float, max_iter: int) -> tuple[np.ndarray, int]:
    n = len(b)
    y = 1.0 / np.sqrt(np.diag(m)) / math.sqrt(n)
    for k in range(1, max_iter + 1):
        g = m @ y - b / y
        if float(np.max(np.abs(g))) < tol:
            return y / y.sum(), k
        h = m + np.diag(b / y ** 2)
        step = np.linalg.solve(h, g)
        t = 1.0
        while np.any(y - t * step <= 0):
            t *= 0.5
        y = y - t * step
    g = m @ y - b / y
    if float(np.max(np.abs(g))) < tol:
        return y / y.sum(), max_iter
    raise ValueError(f"equal risk contribution by Newton's method did not converge in {max_iter} steps (gradient "
                     f"{float(np.max(np.abs(g))):.3e})")


def erc_weights(cov, budgets=None, *, method: str = "ccd", tol: float = 1e-12, max_iter: int = 100_000
                ) -> tuple[pd.Series, int]:
    """Weights whose risk contributions to volatility ``w_i (Sigma w)_i / sigma_p`` are equal, or shares ``b_i`` of it
    (``budgets``: positive, scaled to sum to one), and the iterations taken: ``method="ccd"`` (cyclical coordinate
    descent, sweeps until no weight moves by ``tol``) or ``"newton"`` (Newton's method on Spinu's convex objective,
    steps halved to stay positive, until the gradient is below ``tol``). Not converged: refused."""
    rk._choice(method, ERC_ALGORITHMS, "the ERC method")
    m, labels = _as_matrix(cov)
    b = _budget_vector(budgets, labels)
    w, iterations = (_erc_ccd if method == "ccd" else _erc_newton)(m, b, tol, max_iter)
    return pd.Series(w, index=labels, dtype=float), iterations


def risk_contributions(weights, cov) -> tuple[pd.Series, float]:
    """``(RC_i = w_i (Sigma w)_i / sigma_p, sigma_p)`` (F17): the contributions sum to ``sigma_p``."""
    m, labels = _as_matrix(cov)
    w = _as_series(weights, "weights").reindex(labels)
    if w.isna().any():
        raise ValueError(f"no weight for {list(w[w.isna()].index)}")
    x = w.to_numpy(dtype=float)
    sigma = math.sqrt(max(float(x @ m @ x), 0.0))
    rc = x * (m @ x) / sigma if sigma > 0 else np.zeros_like(x)
    return pd.Series(rc, index=labels, dtype=float), sigma


def es_parity_weights(pnl, budgets=None, *, alpha: float = rk.ES_ALPHA, tol: float = 1e-6, max_iter: int = 500
                      ) -> tuple[pd.Series, int]:
    """Weights whose Euler shares of the historical ES at ``alpha`` (``component_es``) equal the budgets (equal by
    default), by ``w_i <- w_i (b_i / s_i)^{1/2}`` then normalised, from inverse-volatility weights, until every share
    is within ``tol`` of its budget; refused after ``max_iter`` iterations with the last error (historical ES is
    piecewise linear in the weights, so the iteration is not proven to converge), or when a share is not positive
    (a strategy that hedges the others has no ES parity)."""
    frame = pnl if isinstance(pnl, pd.DataFrame) else pd.DataFrame(pnl)
    labels = [str(c) for c in frame.columns]
    x = frame.to_numpy(dtype=float)
    if x.ndim != 2 or not x.shape[0] or not np.all(np.isfinite(x)):
        raise ValueError("ES parity needs a complete scenarios x strategies table of P&L (no missing values)")
    b = _budget_vector(budgets, labels)
    sd = x.std(axis=0, ddof=1) if x.shape[0] > 1 else np.ones(x.shape[1])
    if np.any(sd <= 0):
        raise ValueError(f"ES parity needs every strategy to move: {[labels[i] for i in np.flatnonzero(sd <= 0)]}")
    w = (1 / sd) / (1 / sd).sum()
    err, last, power = math.inf, math.inf, 0.5
    for k in range(1, max_iter + 1):
        comp = component_es(x * w, alpha).to_numpy()
        es = float(comp.sum())
        if not es > 0:
            raise ValueError(f"ES parity: the portfolio's ES is not positive ({es:.6g}) at iteration {k}")
        share = comp / es
        if np.any(share <= 0):
            bad = [labels[i] for i in np.flatnonzero(share <= 0)]
            raise ValueError(f"ES parity: {bad} contribute nothing or less to the ES (a hedge has no ES parity)")
        err = float(np.max(np.abs(share - b)))
        if err < tol:
            return pd.Series(w, index=labels, dtype=float), k
        if err >= last:
            power = max(power / 2, 1 / 1024)  # the error rose: damp harder (UNVERIFIED safeguard)
        last = err
        w = w * (b / share) ** power
        w = w / w.sum()
    raise ValueError(f"ES parity did not converge in {max_iter} iterations (last error {err:.3e} in the shares)")


def _rows_in_force(table, columns: Sequence[str], required: Sequence[str], what: str, as_of: datetime
                   ) -> pd.DataFrame:
    frame = rk._frame(table, what)
    rk._check_columns(frame, required, columns, what)
    rows = []
    for n, rec in rk._lines(frame):
        start = rk._opt_date(rec.get("effective_from"), f"{what} line {n}: effective_from") or date.min
        if start > as_of.date():
            continue
        rows.append({**rec, "effective_from": start, "line": n})
    columns = list(dict.fromkeys([*frame.columns, "effective_from", "line"]))
    return pd.DataFrame(rows, columns=columns)


def read_fixed_weights(table, *, as_of) -> pd.Series:
    """The fixed weights in force at ``as_of`` (``FIXED_WEIGHT_COLUMNS``: each strategy's latest row with
    ``effective_from`` on or before the day; blank: always). Weights must be 0 to 1 and sum to one (within 1e-9)."""
    when = _when(as_of)
    rows = _rows_in_force(table, FIXED_WEIGHT_COLUMNS, ("strategy", "weight"), "the fixed weights", when)
    out: dict[str, tuple[date, float, int]] = {}
    for rec in rows.to_dict("records"):
        where = f"the fixed weights line {rec['line']}"
        strategy = rk._text(rec.get("strategy"))
        if not strategy:
            raise ValueError(f"{where}: strategy is blank")
        weight = _num(rec.get("weight"), f"{where}: weight")
        if not 0 <= weight <= 1:
            raise ValueError(f"{where}: weight {weight} is not between 0 and 1")
        prev = out.get(strategy)
        if prev is not None and prev[0] == rec["effective_from"] and prev[1] != weight:
            raise ValueError(f"{where}: {strategy} has two weights from {rec['effective_from']} (line {prev[2]})")
        if prev is None or rec["effective_from"] >= prev[0]:
            out[strategy] = (rec["effective_from"], weight, rec["line"])
    if not out:
        raise ValueError(f"no fixed weight in force on {when.date()}")
    series = pd.Series({k: v[1] for k, v in sorted(out.items())}, dtype=float)
    if abs(float(series.sum()) - 1.0) > 1e-9:
        raise ValueError(f"the fixed weights in force on {when.date()} sum to {series.sum():.10g}, not 1")
    return series


def read_budgets(table, *, as_of) -> pd.DataFrame:
    """The budgets in force at ``as_of`` (``BUDGET_COLUMNS``): each (strategy, metric)'s latest row; metrics ``dv01``,
    ``cs01`` (rupees per bp), ``var`` and ``es`` (rupees); a budget must be positive."""
    when = _when(as_of)
    rows = _rows_in_force(table, BUDGET_COLUMNS, ("strategy", "metric", "budget"), "the budgets", when)
    out: dict[tuple[str, str], dict] = {}
    for rec in rows.to_dict("records"):
        where = f"the budgets line {rec['line']}"
        strategy, metric = rk._text(rec.get("strategy")), rk._text(rec.get("metric")).lower()
        if not strategy:
            raise ValueError(f"{where}: strategy is blank")
        rk._choice(metric, BUDGET_METRICS, f"{where}: metric")
        budget = _num(rec.get("budget"), f"{where}: budget")
        if not budget > 0:
            raise ValueError(f"{where}: a budget must be positive, not {budget}")
        key = (strategy, metric)
        if key not in out or rec["effective_from"] >= out[key]["effective_from"]:
            out[key] = {"strategy": strategy, "metric": metric, "budget": budget,
                        "unit": rk._text(rec.get("unit")) or ("rs_per_bp" if metric in ("dv01", "cs01") else "rs"),
                        "effective_from": rec["effective_from"]}
    return pd.DataFrame(sorted(out.values(), key=lambda r: (r["strategy"], r["metric"])), columns=list(BUDGET_COLUMNS))


def read_bounds(table) -> pd.DataFrame:
    """Weight bounds by strategy (``BOUNDS_COLUMNS``; blank: 0 and 1); ``min_weight <= max_weight`` within [0, 1]."""
    frame = rk._frame(table, "the bounds")
    rk._check_columns(frame, ("strategy",), BOUNDS_COLUMNS, "the bounds")
    rows, seen = [], set()
    for n, rec in rk._lines(frame):
        where = f"the bounds line {n}"
        strategy = rk._text(rec.get("strategy"))
        if not strategy or strategy in seen:
            raise ValueError(f"{where}: give each strategy once ({strategy!r})")
        seen.add(strategy)
        lo = rk._num(rec.get("min_weight"), f"{where}: min_weight", blank=0.0)
        hi = rk._num(rec.get("max_weight"), f"{where}: max_weight", blank=1.0)
        if not 0 <= lo <= hi <= 1:
            raise ValueError(f"{where}: bounds must satisfy 0 <= min_weight <= max_weight <= 1, not {lo}, {hi}")
        rows.append({"strategy": strategy, "min_weight": lo, "max_weight": hi})
    return pd.DataFrame(rows, columns=list(BOUNDS_COLUMNS))


def budget_scales(metrics, budgets, *, allow_scale_up: bool = False, metric: str | None = None,
                  as_of=None) -> pd.Series:
    """Each strategy's scale ``min(1, budget / |metric|)`` (``budget / |metric|`` with ``allow_scale_up``; 1 when its
    metric is zero) for the strategies with a budget (``BUDGET_COLUMNS`` rows in force at ``as_of``, or a frame
    already read), on ``metric`` (default: the only metric the budgets name). ``metrics``: strategy to its current
    DV01, CS01, VaR or ES."""
    values = _as_series(metrics, "metrics")
    table = budgets if isinstance(budgets, pd.DataFrame) and "line" not in budgets and as_of is None else \
        read_budgets(budgets, as_of=as_of if as_of is not None else datetime.max)
    if metric is None:
        names = sorted(set(table["metric"]))
        if len(names) != 1:
            raise ValueError(f"the budgets name {names}: say which metric to scale on")
        metric = names[0]
    out = {}
    for rec in table[table["metric"] == metric].to_dict("records"):
        strategy = rec["strategy"]
        if strategy not in values.index:
            raise ValueError(f"no current {metric} for {strategy}, which has a {metric} budget")
        current = abs(float(values[strategy]))
        if current == 0:
            out[strategy] = 1.0
            continue
        scale = float(rec["budget"]) / current
        out[strategy] = scale if allow_scale_up else min(1.0, scale)
    return pd.Series(out, dtype=float).sort_index()


def strategy_code(strategy: str) -> str | None:
    """The S1-S10 code a strategy's name starts with (``S7``, ``s7_policy``, ``S10-carry``), or None."""
    match = re.match(r"^\s*[sS](10|[1-9])(?![0-9])", str(strategy))
    return f"S{match.group(1)}" if match else None


def feasible_strategies(access, strategies: Sequence[str]) -> dict[str, tuple[bool, str]]:
    """``{strategy: (allowed, reason)}`` under an access preset (P6.8; the legal form is a question for counsel):
    under ``unregulated_company`` (or any preset with OIS for hedging only) S7's OIS views get no weight; S3 and S5
    only as long-only versions against paid OIS (the strategy must provide them); S1 and S2 unfunded (no repo: their
    DV01 budgets scale with equity); leverage at most 1. Under ``nbfc_non_pd`` every strategy, leverage within the
    base layer's 7 times outside liabilities to owned fund, CRAR 15% (corporate bonds at 100%) from Rs 1,000 crore of
    assets. A name that is not S1-S10 is allowed with a note that no legal-form rule is known for it."""
    access = access if isinstance(access, rt.AccessPolicy) else rt.access_policy(str(access))  # a name too
    name, ois = access.name, access.ois
    borrows, shorts = bool(access.repo or access.treps), bool(access.short_sale)
    out = {}
    for strategy in strategies:
        code = strategy_code(strategy)
        if code is None:
            out[strategy] = (True, f"not one of S1-S10: no legal-form rule known for it under {name} (check it)")
            continue
        if code == "S7" and ois != "any":
            allowed = "hedging only (Rupee IRD Directions 2025 para 5.4(i))" if ois == "hedge_only" else "nothing"
            out[strategy] = (False, f"{code} trades OIS views; {name} allows OIS for {allowed}: weight 0")
            continue
        notes = []
        if code in ("S3", "S5") and not shorts:
            notes.append(f"no short legs under {name}: only its long-only version against paid OIS")
        if code in ("S1", "S2") and not borrows:
            notes.append(f"unfunded under {name} (no repo or TREPS): its DV01 budget scales with equity")
        if not borrows:
            notes.append("leverage at most 1")
        elif name == "nbfc_non_pd":
            notes.append("leverage within the base layer's 7 times outside liabilities to owned fund; CRAR 15% from "
                         "Rs 1,000 crore of assets")
        out[strategy] = (True, "; ".join(notes) or f"allowed under {name}")
    return out


def _bounded(solve, labels: Sequence[str], bounds: pd.DataFrame | None) -> tuple[pd.Series, list[str]]:
    """Weights by ``solve(labels) -> Series`` within the bounds: a weight outside is clipped to its bound and held,
    the method re-solved on the strategies left free for the weight left (U)."""
    lo = {s: 0.0 for s in labels}
    hi = {s: 1.0 for s in labels}
    if bounds is not None:
        for rec in read_bounds(bounds).to_dict("records"):
            if rec["strategy"] in lo:
                lo[rec["strategy"]], hi[rec["strategy"]] = rec["min_weight"], rec["max_weight"]
    if sum(lo.values()) > 1 + 1e-12 or sum(hi.values()) < 1 - 1e-12:
        raise ValueError(f"the bounds cannot hold weights summing to one (minimums {sum(lo.values()):.6g}, maximums "
                         f"{sum(hi.values()):.6g})")
    held: dict[str, float] = {}
    notes = []
    for _ in range(len(labels) + 1):
        free = [s for s in labels if s not in held]
        left = 1.0 - math.fsum(held.values())
        raw = solve(free) if free else pd.Series(dtype=float)
        w = {s: float(raw[s]) * left for s in free}
        out = [s for s in free if w[s] < lo[s] - 1e-12 or w[s] > hi[s] + 1e-12]
        if not out:
            return pd.Series({**held, **w}, dtype=float).reindex(labels), notes
        for s in out:
            held[s] = lo[s] if w[s] < lo[s] else hi[s]
            notes.append(f"{s} held at its bound {held[s]:g}")
    raise ValueError("the bounds could not be met by clipping and re-solving")


def allocate(method: str, *, as_of, access, returns: pd.DataFrame | None = None, cov=None, fixed=None, budgets=None,
             bounds=None, metrics=None, risk_budgets=None, budget_metric: str | None = None, algorithm: str = "ccd",
             cov_method: str = "ewma", allow_scale_up: bool = False, alpha_es: float = rk.ES_ALPHA,
             strategies: Sequence[str] | None = None) -> pd.DataFrame:
    """Capital weights across strategies (``ALLOCATION_COLUMNS``) by ``method``: ``fixed`` (``fixed``,
    ``FIXED_WEIGHT_COLUMNS``), ``inverse_vol``, ``erc_vol`` (``algorithm`` ``ccd`` or ``newton``; ``risk_budgets`` by
    strategy, default equal), ``erc_es`` (on ``returns`` as the scenarios), or ``budget`` (no weights: each strategy's
    scale from ``budgets`` and its current ``metrics``). The covariance is ``cov`` or estimated from ``returns``
    (``cov_method``) and shrunk to positive definite; strategies the preset forbids (``feasible_strategies``) get
    weight 0 and say why, a fixed weight given to one is set to 0 and the others scaled to sum to one (noted).
    ``budgets`` with ``metrics`` add each strategy's scale to any method's rows."""
    rk._choice(method, METHODS, "the allocation method")
    when = _when(as_of)
    if returns is not None and not isinstance(returns, pd.DataFrame):
        raise TypeError("returns must be a date-indexed frame of strategy returns (strategy_returns)")
    rets = None if returns is None else returns[[d <= when.date() for d in returns.index]]
    cov_frame, obs, shrink = None, None, 0.0
    if cov is not None:
        cov_frame = cov if isinstance(cov, pd.DataFrame) else pd.DataFrame(cov)
    elif rets is not None and method in ("inverse_vol", "erc_vol", "fixed", "budget") and len(rets.columns):
        cov_frame, obs = covariance(rets, method=cov_method)
    if cov_frame is not None:
        cov_frame, shrink = make_positive_definite(cov_frame)
    names = list(strategies) if strategies is not None else []
    for source in (cov_frame.columns if cov_frame is not None else [], rets.columns if rets is not None else []):
        names.extend(str(s) for s in source if str(s) not in names)
    fixed_w = read_fixed_weights(fixed, as_of=when) if method == "fixed" else None
    if fixed_w is not None:
        names.extend(s for s in fixed_w.index if s not in names)
    budget_table = read_budgets(budgets, as_of=when) if budgets is not None else None
    if budget_table is not None and method == "budget":
        names.extend(s for s in budget_table["strategy"] if s not in names)
    names = sorted(set(names))
    if not names:
        raise ValueError("no strategy to allocate to: give returns, a covariance, fixed weights or budgets")
    feasible = feasible_strategies(access, names)
    allowed = [s for s in names if feasible[s][0]]
    notes = {s: [feasible[s][1]] for s in names}
    weights = pd.Series(0.0, index=names)
    if method == "fixed":
        given = fixed_w.reindex(names).fillna(0.0)
        lost = math.fsum(float(given[s]) for s in names if s not in allowed)
        for s in names:
            if s not in allowed and given[s]:
                notes[s].append(f"its fixed weight {given[s]:g} set to 0")
        kept = given[[s for s in names if s in allowed]]
        if not kept.sum() > 0:
            raise ValueError("every fixed weight is on a strategy the preset forbids")
        if lost:
            for s in kept.index:
                notes[s].append(f"scaled by 1/{1 - lost:.6g} for the weight of forbidden strategies")
        weights.loc[kept.index] = kept / kept.sum()
    elif method in ("inverse_vol", "erc_vol"):
        if cov_frame is None:
            raise ValueError(f"{method} needs strategy returns or a covariance")
        missing = [s for s in allowed if s not in cov_frame.index]
        if missing:
            raise ValueError(f"no covariance for {missing}")

        def solve(free):
            sub = cov_frame.loc[free, free]
            if method == "inverse_vol":
                return inverse_vol_weights(pd.Series(np.sqrt(np.diag(sub.to_numpy())), index=free))
            w, iterations = erc_weights(sub, None if risk_budgets is None else
                                        _as_series(risk_budgets, "risk_budgets").reindex(free), method=algorithm)
            for s in free:
                notes[s].append(f"{algorithm} {iterations} iterations")
            return w
        if allowed:
            w, extra = _bounded(solve, allowed, bounds)
            weights.loc[allowed] = w
            for s in allowed:
                notes[s].extend(extra)
    elif method == "erc_es":
        if rets is None:
            raise ValueError("erc_es needs the strategies' returns as scenarios")
        sub = rets[allowed].dropna()
        if not len(sub):
            raise ValueError("erc_es needs days on which every allowed strategy has a return")

        def solve_es(free):
            w, iterations = es_parity_weights(sub[free], None if risk_budgets is None else
                                              _as_series(risk_budgets, "risk_budgets").reindex(free), alpha=alpha_es)
            for s in free:
                notes[s].append(f"ES parity {iterations} iterations on {len(sub)} days")
            return w
        if allowed:
            w, extra = _bounded(solve_es, allowed, bounds)
            weights.loc[allowed] = w
            for s in allowed:
                notes[s].extend(extra)
    else:  # budget: scales only
        weights[:] = math.nan
    vols = pd.Series(math.nan, index=names)
    rc = pd.Series(math.nan, index=names)
    share = pd.Series(math.nan, index=names)
    if method == "erc_es" and rets is not None and allowed:
        sub = rets[allowed].dropna()
        vols.loc[allowed] = sub.std(ddof=1)
        comp = component_es(sub.to_numpy() * weights.loc[allowed].to_numpy(), alpha_es)
        comp.index = allowed
        rc.loc[allowed] = comp
        total = float(comp.sum())
        if total:
            share.loc[allowed] = comp / total
    elif cov_frame is not None:
        live = [s for s in names if s in cov_frame.index]
        vols.loc[live] = np.sqrt(np.diag(cov_frame.loc[live, live].to_numpy()))
        if method != "budget" and live:
            w = weights.reindex(live).fillna(0.0)
            contrib, sigma = risk_contributions(w, cov_frame.loc[live, live])
            rc.loc[live] = contrib
            if sigma > 0:
                share.loc[live] = contrib / sigma
    if shrink:
        for s in names:
            notes[s].append(f"covariance shrunk to its diagonal by {shrink:g}")
    if obs is not None:
        for s in names:
            if s in obs.index:
                notes[s].append(f"{int(obs.loc[s, s])} observations")
    scales = pd.Series(math.nan, index=names)
    budget_of = pd.Series(math.nan, index=names)
    metric_name = ""
    if budget_table is not None and len(budget_table):
        names_m = sorted(set(budget_table["metric"]))
        metric_name = budget_metric or (names_m[0] if len(names_m) == 1 else "")
        if not metric_name:
            raise ValueError(f"the budgets name {names_m}: say which one to scale on (budget_metric)")
        if metrics is None:
            raise ValueError("budgets need each strategy's current metric (metrics) to give a scale")
        sc = budget_scales(metrics, budget_table, allow_scale_up=allow_scale_up, metric=metric_name)
        for s, v in sc.items():
            if s in scales.index:
                scales[s] = v
        for rec in budget_table[budget_table["metric"] == metric_name].to_dict("records"):
            if rec["strategy"] in budget_of.index:
                budget_of[rec["strategy"]] = rec["budget"]
    for s in names:
        if s not in allowed:
            scales[s] = 0.0 if method == "budget" or math.isfinite(scales[s]) else scales[s]
    rows = []
    for s in names:
        rows.append({"date": when.date(), "as_of": _iso(when), "strategy": s, "method": method,
                     "allowed": s in allowed, "weight": float(weights[s]), "vol": float(vols[s]),
                     "risk_contribution": float(rc[s]), "risk_share": float(share[s]),
                     "budget_metric": metric_name if math.isfinite(budget_of[s]) else "",
                     "budget": float(budget_of[s]), "scale": float(scales[s]),
                     "notes": "; ".join(dict.fromkeys(n for n in notes[s] if n))})
    return pd.DataFrame(rows, columns=list(ALLOCATION_COLUMNS))


# ---------------------------------------------------------------- rebalancing (P6.3)


def _first_business_day(day: date, calendar) -> date:
    cal = calendar.lenient() if hasattr(calendar, "lenient") else calendar
    return cal.adjust(date(day.year, day.month, 1), "following")


def rebalance_due(current, target, *, last, as_of, rule: str = "both", calendar=None,
                  band_abs: float = REBALANCE_BAND_ABS, band_rel: float = REBALANCE_BAND_REL,
                  breaches: pd.DataFrame | None = None) -> tuple[bool, list[str]]:
    """Whether to rebalance from the ``current`` weights to the ``target`` ones at ``as_of`` (P6.3), and why:
    ``calendar`` on the first business day of the month (when the last rebalance, ``last``, was before it),
    ``threshold`` when a weight is more than ``band_abs`` (absolute) or ``band_rel`` of its target from it, ``both``
    either; never while a hard breach is open (``breaches``: ``limits.BREACH_COLUMNS`` rows of status new, continuing
    or escalated) in the fund's scope or a strategy's that would trade (the cure comes first)."""
    from cr_strat.calendars import mumbai_settlement_calendar
    rk._choice(rule, REBALANCE_RULES, "the rebalancing rule")
    when = _when(as_of)
    cal = calendar if calendar is not None else mumbai_settlement_calendar().lenient()
    cur, tgt = _as_series(current, "current"), _as_series(target, "target")
    names = sorted(set(cur.index) | set(tgt.index))
    cur, tgt = cur.reindex(names).fillna(0.0), tgt.reindex(names).fillna(0.0)
    reasons, due = [], False
    if rule in ("calendar", "both"):
        first = _first_business_day(when.date(), cal)
        last_day = rk._opt_date(last, "the last rebalance") if last is not None else None
        if when.date() == first and (last_day is None or last_day < first):
            due = True
            reasons.append(f"calendar: {first} is the month's first business day")
    if rule in ("threshold", "both"):
        for s in names:
            gap = abs(float(cur[s]) - float(tgt[s]))
            rel = gap / float(tgt[s]) if tgt[s] > 0 else (math.inf if gap > 0 else 0.0)
            if gap > band_abs or rel > band_rel:
                due = True
                reasons.append(f"threshold: {s} at {cur[s]:.4f} against {tgt[s]:.4f}")
    if due and breaches is not None and len(breaches):
        live = breaches[breaches["status"].isin(("new", "continuing", "escalated"))]
        moving = {s for s in names if abs(float(cur[s]) - float(tgt[s])) > 0}
        blocked = [f"{r['limit_id']} ({r['scope_type']} {r['scope']})" for r in live.to_dict("records")
                   if r["scope_type"] == "fund" or (r["scope_type"] == "strategy" and r["scope"] in moving)]
        if blocked:
            return False, [*reasons, f"not now: hard breach open in scope: {', '.join(sorted(blocked))} (the cure "
                                     "comes first)"]
    return due, reasons


def rebalance_cost(current, target, exit_cost) -> pd.Series:
    """The expected cost of a rebalance by strategy: each strategy's cost of exiting its whole book
    (``exit_cost``, e.g. ``stress.liquidity``'s ``liquidation_cost`` summed by strategy) times the share of it traded,
    ``|w* - w| / w``; NaN for a strategy with no weight now (its book's cost is not known). ``total`` is the sum."""
    cur, tgt, cost = _as_series(current, "current"), _as_series(target, "target"), _as_series(exit_cost, "exit_cost")
    names = sorted(set(cur.index) | set(tgt.index))
    out = {}
    for s in names:
        w, w_star = float(cur.get(s, 0.0)), float(tgt.get(s, 0.0))
        if w > 0 and s in cost.index:
            out[s] = abs(w_star - w) / w * float(cost[s])
        else:
            out[s] = 0.0 if w_star == w else math.nan
    series = pd.Series(out, dtype=float)
    series["total"] = math.fsum(v for v in series if math.isfinite(v))
    return series


# ---------------------------------------------------------------- de-risking (P6.4)


def drawdown_scale(index, *, levels: Sequence[tuple[float, float]] = DRAWDOWN_LEVELS,
                   recover: float = DRAWDOWN_RECOVER) -> pd.Series:
    """The risk scale of each day of a NAV index (F20): ``dd = I / max I - 1``; at or below each level's drawdown its
    scale (example levels: 0.5 at -3%, 0.25 at -5%), never rising while the drawdown stays at or below ``recover``,
    and back to 1 only when the drawdown is above ``recover`` (-1.5%). T28: 1.00, 1.01, 0.99, 0.975, 0.97, 0.955,
    0.96, 0.98, 0.997, 1.00, 1.02 give 1, 1, 1, 0.5, 0.5, 0.25, 0.25, 0.25, 1, 1, 1."""
    marks = sorted((float(d), float(s)) for d, s in levels)
    for d, s in marks:
        if not (d < 0 and 0 <= s <= 1):
            raise ValueError(f"a drawdown level is a negative drawdown and a scale from 0 to 1, not ({d}, {s})")
    if not recover < 0 or (marks and recover <= marks[-1][0]):
        raise ValueError(f"recover ({recover}) must be negative and above every trigger level "
                         f"({[d for d, _ in marks]})")
    series = index if isinstance(index, pd.Series) else pd.Series(index, dtype=float)
    values = series.to_numpy(dtype=float)
    if not np.all(np.isfinite(values)) or np.any(values <= 0):
        raise ValueError("a NAV index must be positive finite numbers")
    peak = np.maximum.accumulate(values)
    dd = values / peak - 1
    scale, out = 1.0, []
    for x in dd:
        if x > recover:
            scale = 1.0
        else:
            level = min((s for d, s in marks if x <= d), default=1.0)
            scale = min(scale, level)
        out.append(scale)
    return pd.Series(out, index=series.index, dtype=float)


def drawdowns(index) -> pd.Series:
    """``I / max I - 1`` of a NAV index (F20)."""
    series = index if isinstance(index, pd.Series) else pd.Series(index, dtype=float)
    values = series.to_numpy(dtype=float)
    return pd.Series(values / np.maximum.accumulate(values) - 1, index=series.index, dtype=float)


def stop_loss_scale(pnl, base, *, days: int = STOP_LOSS_DAYS, trigger: float = STOP_LOSS_TRIGGER,
                    scale: float = STOP_LOSS_SCALE) -> pd.Series:
    """A strategy's budget scale each day from its daily P&L (``pnl``, rupees) over the last ``days`` days divided by
    ``base`` (the fund's NAV at the window's start, a Series on the same dates or a number): ``scale`` from the day the
    ratio is at or below ``trigger`` until it is back above half of it (UNVERIFIED rule)."""
    p = pnl if isinstance(pnl, pd.Series) else pd.Series(pnl, dtype=float)
    if not trigger < 0:
        raise ValueError(f"the stop-loss trigger must be negative, not {trigger}")
    rolled = p.rolling(days, min_periods=1).sum()
    if isinstance(base, pd.Series):
        start = base.shift(days - 1).fillna(base.iloc[0]) if len(base) else base
        ratio = rolled / start.reindex(p.index)
    else:
        ratio = rolled / float(base)
    out, current = [], 1.0
    for x in ratio.to_numpy(dtype=float):
        if math.isfinite(x) and x <= trigger:
            current = scale
        elif math.isfinite(x) and x > trigger / 2:
            current = 1.0
        out.append(current)
    return pd.Series(out, index=p.index, dtype=float)


# ---------------------------------------------------------------- sizing (P6.5)


def size_to_dv01(target: float, dv01_per100: float) -> float:
    """Face (rupees) whose DV01 is ``target`` rupees per bp (F19): ``target / (DV01 per 100 / 100)``. T29: Rs 5 lakh
    per bp of 6.94% GS 2036 (0.064282892630 per 100) is Rs 77,78,11,917.83."""
    d = _num(dv01_per100, "dv01_per100")
    if d == 0:
        raise ValueError("a DV01 of zero cannot be sized to")
    return _num(target, "target") / (d / 100)


def size_to_cs01(target: float, cs01_per100: float) -> float:
    """Face (rupees) whose CS01 is ``target`` rupees per bp: ``target / (CS01 per 100 / 100)``."""
    d = _num(cs01_per100, "cs01_per100")
    if d == 0:
        raise ValueError("a CS01 of zero cannot be sized to")
    return _num(target, "target") / (d / 100)


def size_to_vol(target: float, current: float, *, max_scale: float = MAX_VOL_SCALE) -> float:
    """The scale ``target / current`` of a book whose volatility (or parametric VaR over ``z``) is ``current``, capped
    at ``max_scale`` (F19; UNVERIFIED cap 2)."""
    t, c = _num(target, "target"), _num(current, "current")
    if not (t > 0 and c > 0):
        raise ValueError(f"volatility targeting needs positive target and current volatilities, not {t}, {c}")
    return min(t / c, float(max_scale))


def round_to_lot(face: float, lot: float = GSEC_LOT) -> float:
    """``face`` rounded towards zero to a whole number of ``lot`` (the market lot: Rs 5 crore on NDS-OM, U)."""
    f, size = _num(face, "face"), _num(lot, "lot")
    if not size > 0:
        raise ValueError(f"the lot must be positive, not {size}")
    units = math.floor(abs(f) / size + 1e-9)
    return math.copysign(units * size, f) if units else 0.0


# ---------------------------------------------------------------- hedges (P6.6)


def ois_annuity(curve, months: int, start, *, conventions: OisConventions | None = None, calendar=None) -> float:
    """``A = sum tau_k D(E_k) / D(start)``: the annuity of a swap of ``months`` from ``start`` on ``curve``, re-based to
    its start (as ``asset_swaps.hedge_notional``)."""
    s = rk._date(start, "the swap's start")
    conv = conventions if conventions is not None else getattr(curve, "conventions", MIBOR_OIS)
    cal = calendar if calendar is not None else getattr(curve, "calendar", None)
    periods = ois_schedule(s, int(months), conv, cal)
    values = np.asarray(curve.discount([s, *[p.end for p in periods]]), dtype=float).reshape(-1)
    if not (np.all(np.isfinite(values)) and np.all(values > 0)):
        raise ValueError(f"the curve does not reach the end of a {months}-month swap from {s}")
    return float(sum(p.year_fraction * v for p, v in zip(periods, values[1:] / values[0])))


HEDGE_METHODS = ("engine", "annuity")


def hedge_ois_notional(dv01: float, curve, months: int, start, *, conventions: OisConventions | None = None,
                       calendar=None, method: str = "engine") -> float:
    """The OIS notional (rupees) whose DV01 offsets ``dv01`` rupees per bp (F18); pay fixed against a long book's
    positive DV01. ``method="engine"`` (the default): ``N = |DV01| / |DV01_1|``, ``DV01_1`` the DV01 per rupee of
    notional of a par swap of ``months`` from ``start`` as ``ois.ois_npv`` measures it (the zero rates up 1 bp), the
    measure the engine's hedge test (L05), the gate and the risk tables use: the hedge's DV01 on it is ``dv01``.
    ``method="annuity"``: ``N = DV01 / (A x 1e-4)``, ``A`` the swap's annuity from ``start`` (the rule of
    ``asset_swaps.hedge_notional``: a 1 bp move of the swap's par rate), larger on the engine's measure. Give the
    book's DV01 on the measure to be neutralised (a bond's yield DV01 is not its curve DV01)."""
    rk._choice(method, HEDGE_METHODS, "the hedge sizing method")
    target = abs(_num(dv01, "dv01"))
    if method == "annuity":
        a = ois_annuity(curve, months, start, conventions=conventions, calendar=calendar)
        return target / (a * 1e-4)
    s = rk._date(start, "the swap's start")
    conv = conventions if conventions is not None else getattr(curve, "conventions", MIBOR_OIS)
    cal = calendar if calendar is not None else getattr(curve, "calendar", None)
    rate = ois_par_rate(curve, s, int(months), conv, cal)
    unit = ois_npv(curve, OisSwap(effective=s, months=int(months), fixed_rate_pct=rate, notional=1e7, pay_fixed=True,
                                  conventions=conv), calendar=cal).dv01
    if not (math.isfinite(unit) and unit != 0):
        raise ValueError(f"a {months}-month swap from {s} has no DV01 on the curve to size a hedge with")
    return target / abs(unit) * 1e7


def hedge_gsec_face(dv01: float, dv01_h_per100: float) -> float:
    """The face of a G-sec hedge (F18): ``-DV01 / (DV01_h / 100)``, a sale against a long book (where shorts are not
    allowed, a reduction of an existing long: the gate refuses a short)."""
    h = _num(dv01_h_per100, "dv01_h_per100")
    if h == 0:
        raise ValueError("a hedge with no DV01 cannot offset a book")
    return -_num(dv01, "dv01") / (h / 100)


def nnls(A, b, *, tol: float = 1e-12, max_iter: int | None = None) -> np.ndarray:
    """Non-negative least squares ``min ||A x - b||^2, x >= 0`` by the active-set method of Lawson and Hanson (1974):
    move the variable with the largest positive gradient into the passive set, solve the unconstrained problem on it,
    step back towards the feasible region when a passive variable would go non-positive, until no gradient of an
    active variable is positive (above ``tol``)."""
    a = np.asarray(A, dtype=float)
    y = np.asarray(b, dtype=float).reshape(-1)
    if a.ndim != 2 or a.shape[0] != y.size:
        raise ValueError(f"nnls needs an m x n matrix and m targets, not {a.shape} and {y.size}")
    n = a.shape[1]
    limit = max_iter if max_iter is not None else 3 * n + 10
    passive = np.zeros(n, dtype=bool)
    x = np.zeros(n)
    w = a.T @ (y - a @ x)
    for _ in range(limit):
        if passive.all() or float(np.max(np.where(~passive, w, -np.inf))) <= tol:
            return x
        j = int(np.argmax(np.where(~passive, w, -np.inf)))
        passive[j] = True
        for _ in range(limit):
            z = np.zeros(n)
            z[passive] = np.linalg.lstsq(a[:, passive], y, rcond=None)[0]
            if np.all(z[passive] > tol):
                x = z
                break
            mask = passive & (z <= tol)
            alpha = float(np.min(x[mask] / (x[mask] - z[mask])))
            x = x + alpha * (z - x)
            passive = passive & (x > tol)
            x[~passive] = 0.0
        else:
            raise ValueError("nnls: the inner loop did not settle")
        w = a.T @ (y - a @ x)
    raise ValueError(f"nnls did not converge in {limit} iterations")


def key_rate_hedge(book_krd, hedge_krd: pd.DataFrame, *, nonneg: bool = True) -> tuple[pd.Series, pd.Series]:
    """``(notionals by hedge, residual KRDs by tenor)``: ``h`` minimising ``|| KRD_book + K h ||^2`` (F18), ``K`` the
    hedges' KRDs per unit (``hedge_krd``: tenors x hedges); with ``nonneg`` every hedge in its allowed direction (h >=
    0: payer swaps against long bonds under ``hedge_only``) by ``nnls``, else unconstrained least squares. T30."""
    k = hedge_krd if isinstance(hedge_krd, pd.DataFrame) else pd.DataFrame(hedge_krd)
    book = _as_series(book_krd, "book_krd") if not isinstance(book_krd, pd.Series) else book_krd.astype(float)
    tenors = list(dict.fromkeys([*book.index, *k.index]))
    kb = book.reindex(tenors).fillna(0.0).to_numpy()
    km = k.reindex(tenors).fillna(0.0).to_numpy(dtype=float)
    h = nnls(km, -kb) if nonneg else np.linalg.lstsq(km, -kb, rcond=None)[0]
    return (pd.Series(h, index=list(k.columns), dtype=float), pd.Series(kb + km @ h, index=tenors, dtype=float))


# ---------------------------------------------------------------- Euler contributions (P6.7)


def component_var(x, cov, alpha: float = rk.VAR_ALPHA) -> pd.Series:
    """Parametric Euler contributions ``x_i (Sigma x)_i z / sigma_p`` summing to ``VaR = z sigma_p`` (F17; T31)."""
    m, labels = _as_matrix(cov)
    if isinstance(x, pd.Series):
        vec = x.reindex(labels).to_numpy(dtype=float) if isinstance(cov, pd.DataFrame) else x.to_numpy(dtype=float)
    elif isinstance(x, Mapping):
        vec = np.array([float(x[s]) for s in labels])
    else:
        vec = np.asarray(x, dtype=float).reshape(-1)
    if vec.size != len(labels) or not np.all(np.isfinite(vec)):
        raise ValueError("component VaR needs one finite exposure per covariance row")
    sigma = math.sqrt(max(float(vec @ m @ vec), 0.0))
    z = _N.inv_cdf(alpha)
    comp = vec * (m @ vec) / sigma * z if sigma > 0 else np.zeros_like(vec)
    return pd.Series(comp, index=labels, dtype=float)


def _pnl_matrix(pnl) -> tuple[np.ndarray, list]:
    if isinstance(pnl, pd.DataFrame):
        return pnl.to_numpy(dtype=float), list(pnl.columns)
    arr = np.asarray(pnl, dtype=float)
    if arr.ndim != 2:
        raise ValueError("scenario P&L must be a scenarios x components array")
    return arr, list(range(arr.shape[1]))


def component_es(pnl, alpha: float = rk.ES_ALPHA) -> pd.Series:
    """Historical ES contributions ``-sum_s a_s PnL_{s,i} / m`` (Acerbi-Tasche weights of the total's worst scenarios,
    ties to the earliest): they sum to the ES exactly (T32)."""
    x, labels = _pnl_matrix(pnl)
    if not x.shape[0] or not np.all(np.isfinite(x)):
        raise ValueError("component ES needs a non-empty table of finite scenario P&L")
    total = x.sum(axis=1)
    a = rk.tail_weights(total, alpha)
    m = float(rk.tail_size(total.size, alpha))
    return pd.Series(-(a @ x) / m, index=labels, dtype=float)


def component_var_hs(pnl, alpha: float = rk.VAR_ALPHA, *, band: int = HS_VAR_BAND,
                     quantile_rule: str = "ceil") -> pd.Series:
    """Historical VaR contributions (U): the mean loss of each component over a band of ``band`` scenarios centred on
    the VaR scenario (the ``ceil(m)``-th worst total, ties to the earliest), rescaled to sum to the VaR."""
    x, labels = _pnl_matrix(pnl)
    if not x.shape[0] or not np.all(np.isfinite(x)):
        raise ValueError("component VaR needs a non-empty table of finite scenario P&L")
    total = x.sum(axis=1)
    var = rk.hs_var(total, alpha, quantile_rule)
    order = np.argsort(total, kind="mergesort")
    m = rk.tail_size(total.size, alpha)
    rank = min(max(math.ceil(m), 1), total.size) - 1
    half = band // 2
    lo = max(0, min(rank - half, total.size - band))
    pick = order[lo:lo + band]
    comp = -x[pick].mean(axis=0)
    s = float(comp.sum())
    comp = comp * (var / s) if s else comp
    return pd.Series(comp, index=labels, dtype=float)


def write_allocation(frame: pd.DataFrame, path) -> Path:
    """Write an allocation (``ALLOCATION_COLUMNS``) to ``path`` (the same frame gives the same bytes)."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out, index=False, date_format="%Y-%m-%d")
    return out
