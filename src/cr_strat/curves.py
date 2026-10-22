"""Benchmark yield curves and peer spread benchmarks.

``BenchmarkCurve`` interpolates a risk-free curve (G-sec par yields or MIBOR
OIS) so each bond trade can be turned into a spread. Corporate bond yields
and G-sec yields are usually quoted on different compounding bases (Indian
corporate bonds annualised, G-secs semi-annual), which is worth 10-20 bps at
typical yields, so the curve is converted to the bond basis before
subtracting. Check the convention of each source. Money-market quotes (T-bill
yields, OIS up to 1Y) are simple Act/365 rates; ``money_market_to_annual``
turns them into annual-compounding equivalents.

A curve frame may carry a ``basis`` column (the FBIL loaders write one); if it
does, it must agree with ``compounding``, so a curve normalised to annual
compounding cannot be read as semi-annual by mistake.

The curve is held flat beyond its shortest and longest tenors. Set
``max_extrapolation_years`` to stop the long end at the longest tenor plus that
many years: a 5Y OIS curve should not silently price a 10Y bond.

``SpreadBenchmark`` is the matched-peer benchmark for event studies: the
median spread of other issuers' bonds in the same peer group (instrument
class, rating bucket, tenor bucket by default) over a short trailing window.
Each issuer counts once per day, and the issuer being studied is excluded.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pandas as pd

COMPOUNDING = ("annual", "semiannual")
TENOR_EDGES = (0.0, 1.0, 3.0, 5.0, 10.0, np.inf)
TENOR_LABELS = ("0-1y", "1-3y", "3-5y", "5-10y", "10y+")


def convert_yield(yield_pct, from_basis: str, to_basis: str):
    """Convert yields in percent between annual and semi-annual compounding."""
    if from_basis not in COMPOUNDING or to_basis not in COMPOUNDING:
        raise ValueError(f"compounding must be one of {COMPOUNDING}")
    if from_basis == to_basis:
        return yield_pct
    y = np.asarray(yield_pct, dtype=float) / 100
    if from_basis == "semiannual":
        return ((1 + y / 2) ** 2 - 1) * 100
    return 2 * (np.sqrt(1 + y) - 1) * 100


def money_market_to_annual(rate_pct, days):
    """Annual-compounding equivalent (percent) of a simple Act/365 rate for a term of ``days``."""
    r = np.asarray(rate_pct, dtype=float) / 100
    d = np.asarray(days, dtype=float)
    return ((1 + r * d / 365) ** (365 / d) - 1) * 100


def _check_basis(basis: pd.Series, compounding: str) -> None:
    found = {str(v).strip().lower() for v in basis.dropna()} - {""}
    if found and found != {compounding}:
        raise ValueError(
            f"curve rows are on basis {sorted(found)} but compounding={compounding!r}; "
            "pass the curve's basis as compounding"
        )


def tenor_bucket(tenor_years: pd.Series) -> pd.Series:
    return pd.cut(tenor_years, TENOR_EDGES, labels=TENOR_LABELS, right=True).astype(object)


class BenchmarkCurve:
    """Daily curve from rows of (date, tenor_years, yield_pct); linear in tenor, flat beyond the ends.

    ``max_staleness_days``: how long a curve date stays usable. ``max_extrapolation_years``:
    ``None`` holds the longest tenor's yield flat forever; a number gives NaN for tenors
    more than that many years past the longest tenor of the curve date used.
    """

    def __init__(
        self,
        curve: pd.DataFrame,
        *,
        compounding: str = "semiannual",
        max_staleness_days: int = 7,
        max_extrapolation_years: float | None = None,
    ):
        if compounding not in COMPOUNDING:
            raise ValueError(f"compounding must be one of {COMPOUNDING}")
        if max_extrapolation_years is not None and not max_extrapolation_years >= 0:
            raise ValueError("max_extrapolation_years must be None or >= 0")
        df = curve.copy()
        df.columns = [c.strip().lower() for c in df.columns]
        missing = [c for c in ("date", "tenor_years", "yield_pct") if c not in df]
        if missing:
            raise ValueError(f"benchmark curve missing columns: {missing}")
        if "basis" in df:
            _check_basis(df["basis"], compounding)
        df["date"] = pd.to_datetime(df["date"], format="ISO8601", errors="coerce")
        df["tenor_years"] = pd.to_numeric(df["tenor_years"], errors="coerce")
        df["yield_pct"] = pd.to_numeric(df["yield_pct"], errors="coerce")
        df = df.dropna(subset=["date", "tenor_years", "yield_pct"]).sort_values(["date", "tenor_years"])
        self.compounding = compounding
        self.max_staleness = pd.Timedelta(days=max_staleness_days)
        self.max_extrapolation_years = max_extrapolation_years
        self._dates = np.array(sorted(df["date"].unique()), dtype="datetime64[ns]")
        self._points = {
            np.datetime64(d, "ns"): (g["tenor_years"].to_numpy(), g["yield_pct"].to_numpy())
            for d, g in df.groupby("date")
        }

    @classmethod
    def from_csv(cls, path: str | Path, **kwargs) -> BenchmarkCurve:
        return cls(pd.read_csv(path), **kwargs)

    def yields(self, dates: pd.Series, tenors: pd.Series, *, to_basis: str = "annual") -> np.ndarray:
        """Benchmark yield (percent, ``to_basis`` compounding) for each (date, tenor).

        NaN when the curve is stale or the tenor lies beyond ``max_extrapolation_years``.
        """
        dates = pd.to_datetime(pd.Series(dates)).to_numpy(dtype="datetime64[ns]")
        tenors = np.asarray(tenors, dtype=float)
        out = np.full(len(dates), np.nan)
        if not len(self._dates):
            return out
        idx = np.searchsorted(self._dates, dates, side="right") - 1
        for i in np.unique(idx[idx >= 0]):
            curve_date = self._dates[i]
            rows = np.flatnonzero(idx == i)
            rows = rows[(dates[rows] - curve_date) <= self.max_staleness.to_timedelta64()]
            if not len(rows):
                continue
            x, y = self._points[curve_date]
            values = np.interp(tenors[rows], x, y)
            if self.max_extrapolation_years is not None:
                values[tenors[rows] > x[-1] + self.max_extrapolation_years] = np.nan
            out[rows] = values
        return convert_yield(out, self.compounding, to_basis)


class SpreadBenchmark:
    """Median peer spread for a group key on a date, over a trailing window, excluding one issuer."""

    def __init__(
        self,
        panel: pd.DataFrame,
        *,
        match_on: Sequence[str] = ("instrument_class", "rating_bucket", "tenor_bucket"),
        window_days: int = 5,
        min_issuers: int = 3,
    ):
        self.match_on = tuple(match_on)
        self.window = np.timedelta64(window_days - 1, "D")
        self.min_issuers = min_issuers
        df = panel.dropna(subset=["spread_bps", "issuer_id", *self.match_on])
        obs = (df.groupby([*self.match_on, "date", "issuer_id"], observed=True)["spread_bps"]
               .median().reset_index().sort_values("date"))
        self._groups = {}
        for key, g in obs.groupby(list(self.match_on), sort=False):
            key = key if isinstance(key, tuple) else (key,)
            self._groups[key] = (
                g["date"].to_numpy(dtype="datetime64[ns]"),
                g["spread_bps"].to_numpy(dtype=float),
                g["issuer_id"].astype(str).to_numpy(),
            )

    def level(self, key: Sequence, date, exclude_issuer: str | None = None) -> tuple[float, int]:
        """``(median spread, number of issuers)``; the median is NaN below ``min_issuers``."""
        group = self._groups.get(tuple(key))
        if group is None or pd.isna(date):
            return np.nan, 0
        dates, spreads, issuers = group
        end = np.datetime64(pd.Timestamp(date), "ns")
        lo = np.searchsorted(dates, end - self.window, side="left")
        hi = np.searchsorted(dates, end, side="right")
        s, iss = spreads[lo:hi], issuers[lo:hi]
        if exclude_issuer is not None:
            keep = iss != str(exclude_issuer)
            s, iss = s[keep], iss[keep]
        n = len(np.unique(iss))
        return (float(np.median(s)) if n >= self.min_issuers else np.nan), n

    def table(self, freq: str = "W-FRI") -> pd.DataFrame:
        """Benchmark levels for every group on a regular date grid, for inspecting peer curves."""
        rows = []
        for key, (dates, _, _) in self._groups.items():
            grid = pd.date_range(pd.Timestamp(dates[0]), pd.Timestamp(dates[-1]), freq=freq)
            for d in grid:
                level, n = self.level(key, d)
                rows.append({**dict(zip(self.match_on, key)), "date": d, "spread_bps": level, "n_issuers": n})
        return pd.DataFrame(rows, columns=[*self.match_on, "date", "spread_bps", "n_issuers"])
