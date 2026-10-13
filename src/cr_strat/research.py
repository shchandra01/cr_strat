"""Signal research: cross-sectional signals, forward returns, information coefficients, quantile spreads.

Before a signal becomes a strategy, check that it predicts returns at all, at
which horizon, and how steadily. ``signal_panel`` samples the market on a
regular grid (weekly by default) and records, for every bond that traded in
the ``max_age_days`` before each date, the built-in signals:

* ``spread_bps``: spread to the benchmark curve (carry);
* ``rv_bps``: spread minus the matched-peer median, issuer excluded (relative value);
* ``spread_chg_{n}d``: change in the bond's spread since its last trade at
  least ``n`` days earlier (momentum if positive coefficients, reversal if negative);
* ``trade_days``: days traded in the liquidity window.

Any other column added to the panel can be tested the same way.

``forward_returns`` measures each bond from an entry point to ``h`` calendar
days after the sample date, using ``MarketData.marks``: total return (price
change plus coupons and redemptions), excess return over a duration-matched
risk-free bond (``xr``), and the change in spread. By default the entry is the
bond's first reported trade after the sample date (within
``entry_window_days``), which is when the backtester could buy it. Entering at
the sample-date mark instead (``entry="mark"``) reuses the trade that produced
the signal, so bid-ask bounce in that trade shows up as spurious reversal in
``rv_bps`` and spread-change signals. When the bond did not trade near the
horizon the end mark is a matrix mark carried from an older trade;
``end_age_{h}d`` says how old, and ``max_end_age_days`` drops observations
whose end mark is older than that, since a carried mark understates the move.

``information_coefficients`` gives the Spearman rank correlation between a
signal and a forward return on each date; ``ic_summary`` averages them, with
Newey-West t-statistics when the horizon overlaps the sampling interval.
``quantile_returns`` sorts each date into quantiles and reports mean forward
returns per quantile and the top-minus-bottom spread. ``neutralize`` ranks a
signal within groups (e.g. rating bucket) first, so the test is about
differences between comparable bonds rather than between rating grades.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
import pandas as pd

from cr_strat.backtest import rebalance_dates
from cr_strat.market import MarketData
from cr_strat.stats import newey_west_tstat

DEFAULT_SIGNALS = ("spread_bps", "rv_bps", "spread_chg_20d", "spread_chg_60d", "trade_days")


def signal_panel(
    market: MarketData,
    *,
    start=None,
    end=None,
    freq: str = "W-FRI",
    max_age_days: int = 5,
    liquidity_days: int = 60,
    momentum_days: Sequence[int] = (20, 60),
) -> pd.DataFrame:
    """One row per (sample date, bond traded in the ``max_age_days`` before it) with signals."""
    cal = market.curve.dates
    cal = cal[(cal >= (pd.Timestamp(start) if start else market.panel["date"].min()))
              & (cal <= (pd.Timestamp(end) if end else cal.max()))]
    frames = []
    for d in sorted(rebalance_dates(cal, freq)):
        snap = market.snapshot(d, max_age_days=max_age_days, liquidity_days=liquidity_days)
        if len(snap):
            frames.append(snap.assign(date=d))
    if not frames:
        return pd.DataFrame()
    panel = pd.concat(frames, ignore_index=True)
    for n in momentum_days:
        back = pd.DataFrame({"isin": panel["isin"], "date": panel["obs_date"] - pd.Timedelta(days=n)})
        prior = market.last_trade(back)
        too_old = (back["date"] - prior["obs_date"]).dt.days > n
        panel[f"spread_chg_{n}d"] = (panel["spread_bps"] - prior["spread_bps"]).where(~too_old)
    first = ["date", "isin", "issuer_id"]
    return panel[first + [c for c in panel.columns if c not in first]]


def forward_returns(
    market: MarketData,
    panel: pd.DataFrame,
    horizons: Sequence[int] = (20, 60, 120),
    *,
    entry: str = "next_trade",
    entry_window_days: int = 10,
    max_end_age_days: int | None = 30,
) -> pd.DataFrame:
    """Add ``entry_date``, ``tr_{h}d``, ``xr_{h}d``, ``dspread_{h}d`` and ``end_age_{h}d`` per horizon."""
    if entry not in ("next_trade", "mark"):
        raise ValueError("entry must be 'next_trade' or 'mark'")
    out = panel.copy()
    cal = market.curve.dates
    if entry == "mark":
        out["entry_date"] = out["date"]
    else:
        left = out[["isin", "date"]].assign(_row=np.arange(len(out))).sort_values("date", kind="stable")
        trades = market.panel[["date", "isin"]].rename(columns={"date": "entry_date"})
        nxt = pd.merge_asof(left, trades.sort_values("entry_date", kind="stable"), left_on="date",
                            right_on="entry_date", by="isin", direction="forward", allow_exact_matches=False,
                            tolerance=pd.Timedelta(days=entry_window_days))
        out["entry_date"] = nxt.sort_values("_row")["entry_date"].to_numpy()
    has_entry = out["entry_date"].notna().to_numpy()
    start = market.marks(out["isin"], out["entry_date"].fillna(out["date"]))
    days_ok = has_entry & (start["dirty"].to_numpy() > 0)
    for h in horizons:
        target = out["date"] + pd.Timedelta(days=h)
        idx = np.searchsorted(cal, target.to_numpy(dtype="datetime64[ns]"), side="right") - 1
        end_date = pd.Series(cal[np.clip(idx, 0, len(cal) - 1)], index=out.index)
        valid = (idx >= 0) & (target <= cal.max()).to_numpy() & days_ok & (end_date > out["entry_date"]).to_numpy()
        res = {k: np.full(len(out), np.nan) for k in ("tr", "xr", "dspread", "end_age")}
        if valid.any():
            v = np.flatnonzero(valid)
            isins, d0, d1 = out["isin"].to_numpy()[v], out["entry_date"].to_numpy()[v], end_date.to_numpy()[v]
            end = market.marks(isins, d1)
            s = start.iloc[v]
            cpn, prin = market.cashflows.cash_flows(isins, d0, d1)
            dirty0 = s["dirty"].to_numpy()
            tr = (end["dirty"].to_numpy() + cpn + prin) / dirty0 - 1
            years = (d1 - d0) / np.timedelta64(1, "D") / 365
            b0, b1 = s["benchmark_yield_pct"].to_numpy(), end["benchmark_yield_pct"].to_numpy()
            matched = (1 + b0 / 100) ** years - 1 - s["mod_duration"].to_numpy() * (b1 - b0) / 100
            res["tr"][v] = tr
            res["xr"][v] = tr - matched
            res["dspread"][v] = end["spread_bps"].to_numpy() - s["spread_bps"].to_numpy()
            res["end_age"][v] = end["age_days"].to_numpy()
        stale = res["end_age"] > max_end_age_days if max_end_age_days is not None else np.zeros(len(out), bool)
        for k in ("tr", "xr", "dspread"):
            out[f"{k}_{h}d"] = np.where(stale, np.nan, res[k])
        out[f"end_age_{h}d"] = res["end_age"]
    return out


def neutralize(panel: pd.DataFrame, signal: str, by: str | Sequence[str]) -> pd.Series:
    """Percentile rank of ``signal`` within each date and ``by`` group, centred on zero."""
    keys = ["date", *([by] if isinstance(by, str) else by)]
    return panel.groupby(keys, dropna=False)[signal].rank(pct=True) - 0.5


def information_coefficients(panel: pd.DataFrame, signal: str, ret: str, *, min_obs: int = 10) -> pd.DataFrame:
    """Per-date Spearman correlation of ``signal`` with ``ret``."""
    df = panel[["date", signal, ret]].dropna()
    rows = []
    for d, g in df.groupby("date"):
        if len(g) >= min_obs and g[signal].nunique() > 1 and g[ret].nunique() > 1:
            r = g[[signal, ret]].rank()
            rows.append({"date": d, "ic": r[signal].corr(r[ret]), "n": len(g)})
    return pd.DataFrame(rows, columns=["date", "ic", "n"])


def ic_summary(ic: pd.DataFrame, horizon_days: int, sample_days: int = 7) -> dict[str, float]:
    """Mean IC, its standard deviation and IR, a Newey-West t-stat with lags covering the overlap, and hit rate."""
    if ic.empty:
        return {"n_dates": 0}
    lags = max(int(math.ceil(horizon_days / sample_days)) - 1, 0)
    mean, t, _ = newey_west_tstat(ic["ic"], lags=lags)
    sd = ic["ic"].std(ddof=1)
    return {"n_dates": len(ic), "avg_n": ic["n"].mean(), "mean_ic": mean, "ic_sd": sd,
            "ic_ir": mean / sd if sd > 0 else np.nan, "t_stat_nw": t, "hit_rate": (ic["ic"] > 0).mean()}


def quantile_returns(panel: pd.DataFrame, signal: str, ret: str, *, n: int = 5, min_obs: int = 10,
                     horizon_days: int = 0, sample_days: int = 7) -> tuple[pd.DataFrame, dict[str, float]]:
    """``(per_quantile, long_short)``: mean ``ret`` by signal quantile (1 = lowest) and top minus bottom."""
    df = panel[["date", signal, ret]].dropna()
    df = df[df.groupby("date")[signal].transform("size") >= min_obs]
    if df.empty:
        return pd.DataFrame(columns=["quantile", "mean", "n_dates", "avg_n"]), {}
    df = df.assign(quantile=df.groupby("date")[signal].transform(
        lambda s: pd.qcut(s.rank(method="first"), n, labels=False) + 1))
    by_date = df.groupby(["date", "quantile"])[ret].agg(["mean", "size"]).reset_index()
    table = by_date.groupby("quantile").agg(mean=("mean", "mean"), n_dates=("mean", "size"),
                                            avg_n=("size", "mean")).reset_index()
    wide = by_date.pivot(index="date", columns="quantile", values="mean")
    spread = (wide[n] - wide[1]).dropna() if n in wide and 1 in wide else pd.Series(dtype=float)
    lags = max(int(math.ceil(horizon_days / sample_days)) - 1, 0)
    mean, t, _ = newey_west_tstat(spread, lags=lags)
    return table, {"long_short_mean": mean, "long_short_t_nw": t, "n_dates": len(spread),
                   "monotonic": bool(table["mean"].is_monotonic_increasing or table["mean"].is_monotonic_decreasing)}


def evaluate_signals(
    panel: pd.DataFrame,
    signals: Sequence[str] = DEFAULT_SIGNALS,
    horizons: Sequence[int] = (20, 60, 120),
    *,
    ret: str = "xr",
    n_quantiles: int = 5,
    neutralize_by: str | None = None,
    sample_days: int = 7,
    min_obs: int = 10,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """``(ic_summary, quantile_table, ic_by_date)`` for every signal and horizon present in ``panel``."""
    summary, quantiles, by_date = [], [], []
    for sig in signals:
        if sig not in panel:
            continue
        col = sig
        df = panel
        if neutralize_by:
            col = f"{sig}_neutral"
            df = panel.assign(**{col: neutralize(panel, sig, neutralize_by)})
        for h in horizons:
            target = f"{ret}_{h}d"
            if target not in df:
                continue
            ic = information_coefficients(df, col, target, min_obs=min_obs)
            by_date.append(ic.assign(signal=sig, horizon_days=h))
            q, ls = quantile_returns(df, col, target, n=n_quantiles, min_obs=min_obs, horizon_days=h,
                                     sample_days=sample_days)
            quantiles.append(q.assign(signal=sig, horizon_days=h))
            summary.append({"signal": sig, "horizon_days": h, "return": target,
                            **ic_summary(ic, h, sample_days), **ls})
    cat = lambda frames: pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return pd.DataFrame(summary), cat(quantiles), cat(by_date)
