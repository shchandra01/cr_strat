"""Statistics for strategy research: regressions with robust errors, autocorrelation-robust
t-statistics, and Sharpe ratio inference that allows for non-normal returns and multiple trials.

* ``ols``: least squares with HC1 or issuer/date-clustered standard errors, for
  cross-sectional event regressions (e.g. spread reversal on constrained ownership).
* ``newey_west_tstat``: t-statistic of a mean with Newey-West errors, for
  return series and for averages of overlapping-horizon signals.
* ``probabilistic_sharpe``: Bailey & Lopez de Prado (2012), the probability the
  true Sharpe ratio exceeds a benchmark given sample length, skew and kurtosis.
* ``deflated_sharpe``: Bailey & Lopez de Prado (2014), the same probability
  against the Sharpe ratio expected from the best of ``n_trials`` unskilled
  strategies; use it after a parameter sweep.
* ``block_bootstrap``: stationary-block bootstrap confidence interval for any
  statistic of a return series.

Regression p-values use Student's t with n - k degrees of freedom, or G - 1
with G clusters, so a handful of issuers does not pass for a large sample.
Sharpe-ratio probabilities use the normal approximation of the cited papers.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from statistics import NormalDist

import numpy as np
import pandas as pd

_N = NormalDist()
EULER_GAMMA = 0.5772156649015329


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (modified Lentz)."""
    tiny = 1e-300
    c, d = 1.0, 1.0 - (a + b) * x / (a + 1.0)
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        for aa in (m * (b - m) * x / ((a - 1 + m2) * (a + m2)),
                   -(a + m) * (a + b + m) * x / ((a + m2) * (a + 1 + m2))):
            d = 1.0 + aa * d
            d = 1.0 / (d if abs(d) > tiny else tiny)
            c = 1.0 + aa / c
            c = c if abs(c) > tiny else tiny
            h *= d * c
        if abs(d * c - 1.0) < 3e-14:
            break
    return h


def _betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x))
    if x < (a + 1) / (a + b + 2):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1 - x) / b


def _p_value(t: float, df: float | None = None) -> float:
    """Two-sided p-value of a t-statistic; normal when ``df`` is None."""
    if not np.isfinite(t):
        return np.nan
    if df is None or df > 1e6:
        return 2 * (1 - _N.cdf(abs(t)))
    if df < 1:
        return np.nan
    return _betainc(df / 2, 0.5, df / (df + t * t))


def ols(
    data: pd.DataFrame,
    y: str,
    x: Sequence[str],
    *,
    cluster: str | None = None,
    add_constant: bool = True,
) -> pd.DataFrame:
    """OLS of ``y`` on ``x``; rows with any missing value are dropped.

    Errors are HC1 (heteroskedasticity-robust) or, with ``cluster``, clustered
    on that column with the usual small-sample factor. Booleans become 0/1.
    The result has one row per term (coef, se, t, p) and ``attrs`` with n,
    r2, adj_r2 and n_clusters.
    """
    cols = [y, *x] + ([cluster] if cluster else [])
    df = data[cols].copy()
    for c in [y, *x]:
        df[c] = pd.to_numeric(df[c].map(lambda v: float(v) if isinstance(v, (bool, np.bool_)) else v),
                              errors="coerce")
    complete = df.dropna()
    if len(complete) <= len(x) + int(add_constant):
        missing = ", ".join(f"{c} {int(df[c].isna().sum())}" for c in cols if df[c].isna().any())
        raise ValueError(f"{len(complete)} of {len(df)} rows have every value, too few for {len(x) + add_constant} "
                         f"terms; missing values by column: {missing or 'none'}")
    df = complete
    terms = (["const"] if add_constant else []) + list(x)
    X = df[list(x)].to_numpy(dtype=float)
    if add_constant:
        X = np.column_stack([np.ones(len(df)), X])
    Y = df[y].to_numpy(dtype=float)
    n, k = X.shape
    xtx_inv = np.linalg.pinv(X.T @ X)
    beta = xtx_inv @ X.T @ Y
    resid = Y - X @ beta
    if cluster:
        groups = df[cluster].to_numpy()
        scores = pd.DataFrame(X * resid[:, None]).groupby(groups).sum().to_numpy()
        g = len(scores)
        meat = scores.T @ scores
        factor = g / (g - 1) * (n - 1) / (n - k) if g > 1 else np.nan
    else:
        g = None
        meat = (X * resid[:, None] ** 2).T @ X
        factor = n / (n - k)
    cov = factor * xtx_inv @ meat @ xtx_inv
    se = np.sqrt(np.diag(cov))
    t = beta / se
    dof = (g - 1) if cluster else (n - k)
    out = pd.DataFrame({"term": terms, "coef": beta, "se": se, "t": t, "p": [_p_value(v, dof) for v in t]})
    ss_res = float(resid @ resid)
    ss_tot = float(((Y - Y.mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else np.nan
    out.attrs.update(n=n, r2=r2, adj_r2=1 - (1 - r2) * (n - 1) / (n - k) if add_constant else np.nan,
                     n_clusters=g, y=y, cov="cluster" if cluster else "HC1")
    return out


def newey_west_tstat(x, lags: int | None = None) -> tuple[float, float, int]:
    """``(mean, t-stat, lags)`` with Newey-West errors; default lags = floor(4 (n/100)^(2/9))."""
    v = pd.Series(x, dtype=float).dropna().to_numpy()
    n = len(v)
    if n < 3:
        return (float(v.mean()) if n else np.nan), np.nan, 0
    if lags is None:
        lags = int(math.floor(4 * (n / 100) ** (2 / 9)))
    lags = max(0, min(lags, n - 1))
    e = v - v.mean()
    var = e @ e / n
    for lag in range(1, lags + 1):
        var += 2 * (1 - lag / (lags + 1)) * (e[lag:] @ e[:-lag]) / n
    se = math.sqrt(var / n) if var > 0 else np.nan
    return float(v.mean()), float(v.mean() / se) if se else np.nan, lags


def sharpe_moments(returns) -> tuple[float, int, float, float]:
    """Per-period Sharpe ratio, observations, skewness and (non-excess) kurtosis."""
    r = pd.Series(returns, dtype=float).dropna()
    sd = r.std(ddof=1)
    sr = float(r.mean() / sd) if sd > 0 else np.nan
    return sr, len(r), float(r.skew()), float(r.kurt()) + 3.0


def probabilistic_sharpe(sr: float, n: int, skew: float, kurtosis: float, sr_benchmark: float = 0.0) -> float:
    """P(true per-period Sharpe > ``sr_benchmark``); ``kurtosis`` is raw (3 for normal)."""
    if not np.isfinite(sr) or n < 3:
        return np.nan
    denom = 1 - skew * sr + (kurtosis - 1) / 4 * sr**2
    if denom <= 0:
        return np.nan
    return _N.cdf((sr - sr_benchmark) * math.sqrt(n - 1) / math.sqrt(denom))


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """Sharpe ratio the best of ``n_trials`` skill-less strategies is expected to show."""
    if n_trials < 2 or not np.isfinite(sr_variance) or sr_variance <= 0:
        return 0.0
    return math.sqrt(sr_variance) * ((1 - EULER_GAMMA) * _N.inv_cdf(1 - 1 / n_trials)
                                     + EULER_GAMMA * _N.inv_cdf(1 - 1 / (n_trials * math.e)))


def deflated_sharpe(returns, trial_sharpes: Sequence[float]) -> float:
    """Probabilistic Sharpe of ``returns`` against the expected maximum over the trials' per-period Sharpes."""
    sr, n, skew, kurt = sharpe_moments(returns)
    trials = np.asarray([s for s in trial_sharpes if np.isfinite(s)])
    bench = expected_max_sharpe(len(trials), float(trials.var(ddof=1)) if len(trials) > 1 else np.nan)
    return probabilistic_sharpe(sr, n, skew, kurt, bench)


def block_bootstrap(
    returns,
    stat: Callable[[np.ndarray], float],
    *,
    block: int = 20,
    n_boot: int = 1000,
    seed: int = 0,
    ci: float = 0.95,
) -> tuple[float, float, float]:
    """``(estimate, lower, upper)`` from a stationary bootstrap with mean block length ``block``."""
    r = pd.Series(returns, dtype=float).dropna().to_numpy()
    n = len(r)
    if n < 2 * block:
        return stat(r) if n else np.nan, np.nan, np.nan
    rng = np.random.default_rng(seed)
    draws = np.empty(n_boot)
    p = 1.0 / block
    for b in range(n_boot):
        idx = np.empty(n, dtype=int)
        idx[0] = rng.integers(n)
        jumps = rng.random(n) < p
        starts = rng.integers(n, size=n)
        for i in range(1, n):
            idx[i] = starts[i] if jumps[i] else (idx[i - 1] + 1) % n
        draws[b] = stat(r[idx])
    lo, hi = np.nanquantile(draws, [(1 - ci) / 2, 1 - (1 - ci) / 2])
    return float(stat(r)), float(lo), float(hi)
