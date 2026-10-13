"""Performance, risk, trading, attribution and exposure analytics for backtest results.

Return statistics annualise with the number of observations per year in the
sample, so they work for any calendar. Excess returns are over the cash rate
the backtest paid on cash. The hedged series (``nav_hedged``) is the strategy
with an idealised duration-matched rates hedge; see ``backtest``.

Holding episodes run from a position's first fill to the day it is closed.
Their P&L is the sum of the daily attribution, so execution costs on both legs
and coupons received are included. ``excess_pnl`` removes the rates component
and charges the benchmark yield on the position's value: the return over a
duration-matched risk-free bond.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from cr_strat.backtest import COMPONENTS, BacktestResult
from cr_strat.stats import newey_west_tstat, probabilistic_sharpe, sharpe_moments


def _series(nav: pd.DataFrame, col: str) -> pd.Series:
    return nav.set_index("date")[col].astype(float)


def returns(nav: pd.Series) -> pd.Series:
    return nav.pct_change().dropna()


def _years(index: pd.DatetimeIndex) -> float:
    return max((index[-1] - index[0]).days / 365.25, 1e-9)


def drawdown(nav: pd.Series) -> pd.Series:
    return nav / nav.cummax() - 1


def performance_stats(nav: pd.Series, *, cash_rate_pct: pd.Series | None = None,
                      benchmark: pd.Series | None = None) -> dict[str, float]:
    """Return, risk and significance statistics for a NAV series indexed by date."""
    nav = nav.dropna()
    r = returns(nav)
    if len(r) < 2:
        return {"n_obs": len(r)}
    years = _years(nav.index)
    ppy = len(r) / years
    rf = pd.Series(0.0, index=r.index)
    if cash_rate_pct is not None:
        rate = cash_rate_pct.reindex(nav.index).ffill().shift(1).reindex(r.index)
        days = nav.index.to_series().diff().dt.days.reindex(r.index)
        rf = (rate / 100 * days / 365).fillna(0.0)
    ex = r - rf
    dd = drawdown(nav)
    under = dd < 0
    spells = (under != under.shift()).cumsum()[under]
    longest = max((g.index[-1] - g.index[0]).days + 1 for _, g in spells.groupby(spells)) if under.any() else 0
    monthly = nav.resample("ME").last()
    monthly = pd.concat([nav.iloc[:1], monthly]).pct_change().dropna()
    sr, n, skew, kurt = sharpe_moments(ex)
    downside = math.sqrt((np.minimum(ex, 0.0) ** 2).mean()) * math.sqrt(ppy)
    q05 = r.quantile(0.05)
    total = nav.iloc[-1] / nav.iloc[0] - 1
    cagr = (1 + total) ** (1 / years) - 1
    vol = r.std(ddof=1) * math.sqrt(ppy)
    _, t_nw, _ = newey_west_tstat(ex)
    out = {
        "start": nav.index[0], "end": nav.index[-1], "years": years, "n_obs": len(r),
        "total_return": total, "cagr": cagr, "ann_vol": vol,
        "ann_excess_return": ex.mean() * ppy, "sharpe": sr * math.sqrt(ppy) if np.isfinite(sr) else np.nan,
        "sortino": ex.mean() * ppy / downside if downside > 0 else np.nan,
        "max_drawdown": dd.min(), "max_drawdown_date": dd.idxmin(), "longest_drawdown_days": longest,
        "calmar": cagr / abs(dd.min()) if dd.min() < 0 else np.nan,
        "skew": skew, "excess_kurtosis": kurt - 3, "var_95_daily": -q05, "cvar_95_daily": -r[r <= q05].mean(),
        "hit_rate_daily": (r > 0).mean(), "hit_rate_monthly": (monthly > 0).mean() if len(monthly) else np.nan,
        "best_month": monthly.max(), "worst_month": monthly.min(),
        "excess_t_stat_nw": t_nw, "prob_sharpe_gt_0": probabilistic_sharpe(sr, n, skew, kurt),
    }
    if benchmark is not None:
        b = returns(benchmark.dropna()).reindex(r.index)
        both = pd.concat([r, b], axis=1, keys=["s", "b"]).dropna()
        if len(both) > 2:
            active = both["s"] - both["b"]
            te = active.std(ddof=1) * math.sqrt(ppy)
            beta = both["s"].cov(both["b"]) / both["b"].var()
            out.update(tracking_error=te, information_ratio=active.mean() * ppy / te if te > 0 else np.nan,
                       beta=beta, alpha_ann=(both["s"].mean() - beta * both["b"].mean()) * ppy,
                       correlation=both["s"].corr(both["b"]))
    return out


def episodes(positions: pd.DataFrame) -> pd.DataFrame:
    """One row per holding episode (ISIN from first fill to close) with its P&L by component."""
    if positions.empty:
        return pd.DataFrame()
    pos = positions.sort_values(["isin", "entry_date", "date"], kind="stable")
    pnl_cols = [f"pnl_{c}" for c in (*COMPONENTS, "funding")]
    g = pos.groupby(["isin", "entry_date"], sort=False)
    last = g.tail(1).set_index(["isin", "entry_date"])
    first = g.head(1).set_index(["isin", "entry_date"])
    out = pd.DataFrame({
        "issuer_id": first["issuer_id"], "sector": first["sector"], "entry_rating": first["rating_bucket"],
        "exit_date": last["date"].where(last["closed"].notna()), "last_date": last["date"],
        "status": last["closed"].fillna("open"), "max_face": g["face"].max(), "avg_value": g["value"].mean(),
        "entry_spread_bps": first["spread_bps"], "exit_spread_bps": last["spread_bps"],
    })
    sums = g[pnl_cols].sum()
    out = out.join(sums)
    out["pnl"] = sums[[f"pnl_{c}" for c in COMPONENTS]].sum(axis=1)
    out["excess_pnl"] = out["pnl"] - out["pnl_rates"] + out["pnl_funding"]
    out = out.reset_index()
    out["holding_days"] = (out["last_date"] - out["entry_date"]).dt.days
    peak = g["value"].max().to_numpy()
    out["return_on_peak_value"] = out["pnl"].to_numpy() / np.where(peak > 0, peak, np.nan)
    out["excess_return_on_peak_value"] = out["excess_pnl"].to_numpy() / np.where(peak > 0, peak, np.nan)
    return out.sort_values("entry_date", kind="stable").reset_index(drop=True)


def trading_stats(result: BacktestResult, eps: pd.DataFrame | None = None) -> dict[str, float]:
    nav = result.nav.set_index("date")
    years = _years(nav.index)
    avg_nav = nav["nav"].mean()
    eps = episodes(result.positions) if eps is None else eps
    orders = result.orders
    closed = orders[orders["status"] != "open_at_end"]
    out = {
        "avg_positions": nav["n_positions"].mean(), "avg_gross": nav["gross"].mean(),
        "turnover_annual": nav["traded_inr"].sum() / 2 / avg_nav / years,
        "cost_inr": nav["cost_inr"].sum(), "cost_bps_annual": nav["cost_inr"].sum() / avg_nav / years * 1e4,
        "n_fills": len(result.fills), "n_orders": len(orders),
        "order_fill_rate": (closed["status"] == "filled").mean() if len(closed) else np.nan,
        "orders_expired": int((orders["status"] == "expired").sum()),
        "avg_fill_cost_bps": result.fills["cost_bps"].mean() if len(result.fills) else np.nan,
        "avg_mod_duration": nav["mod_duration"].mean(), "avg_spread_bps": nav["spread_bps"].mean(),
        "avg_stale_share": nav["stale_share"].mean(), "avg_mark_age_days": nav["avg_mark_age_days"].mean(),
        "n_episodes": len(eps),
    }
    if len(eps):
        done = eps[eps["status"] != "open"]
        out.update(
            avg_holding_days=eps["holding_days"].mean(),
            episode_win_rate=(done["pnl"] > 0).mean() if len(done) else np.nan,
            episode_excess_win_rate=(done["excess_pnl"] > 0).mean() if len(done) else np.nan,
            avg_episode_return=done["return_on_peak_value"].mean() if len(done) else np.nan,
            avg_episode_excess_return=done["excess_return_on_peak_value"].mean() if len(done) else np.nan,
            profit_factor=(done.loc[done["pnl"] > 0, "pnl"].sum() / -done.loc[done["pnl"] < 0, "pnl"].sum()
                           if (done["pnl"] < 0).any() else np.nan),
        )
    return out


def summary(result: BacktestResult, benchmark: BacktestResult | None = None) -> pd.DataFrame:
    """All headline statistics in one long table: metric, total, hedged."""
    rate = _series(result.nav, "cash_rate_pct")
    bench = _series(benchmark.nav, "nav") if benchmark is not None else None
    bench_h = _series(benchmark.nav, "nav_hedged") if benchmark is not None else None
    total = performance_stats(_series(result.nav, "nav"), cash_rate_pct=rate, benchmark=bench)
    hedged = performance_stats(_series(result.nav, "nav_hedged"), cash_rate_pct=rate, benchmark=bench_h)
    rows = [{"metric": k, "total": v, "hedged": hedged.get(k)} for k, v in total.items()]
    rows += [{"metric": k, "total": v, "hedged": None} for k, v in trading_stats(result).items()]
    return pd.DataFrame(rows)


def attribution(nav: pd.DataFrame) -> pd.DataFrame:
    """P&L by component: rupees, share of the total, and annualised bps of average NAV."""
    nv = nav.set_index("date")
    years = _years(nv.index)
    cols = [*(f"pnl_{c}" for c in COMPONENTS), "pnl_cash"]
    total = nv[cols].sum()
    out = pd.DataFrame({"component": [c[4:] for c in cols], "pnl_inr": total.to_numpy()})
    out.loc[len(out)] = ["total", total.sum()]
    out.loc[len(out)] = ["hedge_rates_removed", -total["pnl_rates"]]
    out.loc[len(out)] = ["hedge_matched_carry", nv["pnl_funding"].sum()]
    out.loc[len(out)] = ["hedge_cash_on_bonds", nv["pnl_hedge_cash"].sum()]
    out.loc[len(out)] = ["hedged_total", nv["pnl_hedged"].sum()]
    out["share_of_total"] = out["pnl_inr"] / total.sum() if total.sum() else np.nan
    out["bps_per_year"] = out["pnl_inr"] / nv["nav"].mean() / years * 1e4
    return out


def cumulative_attribution(nav: pd.DataFrame) -> pd.DataFrame:
    cols = [*(f"pnl_{c}" for c in COMPONENTS), "pnl_cash"]
    return nav.set_index("date")[cols].cumsum().reset_index()


def monthly_returns(nav: pd.Series) -> pd.DataFrame:
    """Year x month table of returns, with the calendar-year return in ``year``."""
    nav = nav.dropna()
    me = nav.resample("ME").last()
    r = pd.concat([nav.iloc[:1], me]).pct_change().dropna()
    r = r[~r.index.duplicated(keep="last")]
    table = pd.DataFrame({"year": r.index.year, "month": r.index.month, "r": r.to_numpy()})
    out = table.pivot(index="year", columns="month", values="r").reindex(columns=range(1, 13))
    out.columns = [pd.Timestamp(2000, m, 1).strftime("%b") for m in out.columns]
    ye = nav.resample("YE").last()
    out["year"] = pd.concat([nav.iloc[:1], ye]).pct_change().dropna().groupby(lambda d: d.year).last()
    return out.reset_index(names="calendar_year")


def drawdowns(nav: pd.Series, top: int = 5) -> pd.DataFrame:
    """The ``top`` deepest drawdowns: peak, trough, recovery dates, depth and length."""
    nav = nav.dropna()
    dd = drawdown(nav)
    rows, in_dd, peak = [], False, nav.index[0]
    for date, value in dd.items():
        if value < 0 and not in_dd:
            in_dd, start = True, peak
        if value == 0:
            if in_dd:
                seg = dd[start:date]
                rows.append({"peak": start, "trough": seg.idxmin(), "recovery": date, "depth": seg.min()})
                in_dd = False
            peak = date
    if in_dd:
        seg = dd[start:]
        rows.append({"peak": start, "trough": seg.idxmin(), "recovery": pd.NaT, "depth": seg.min()})
    out = pd.DataFrame(rows, columns=["peak", "trough", "recovery", "depth"])
    end = out["recovery"].fillna(nav.index[-1])
    out["days_to_trough"] = (out["trough"] - out["peak"]).dt.days
    out["days_underwater"] = (end - out["peak"]).dt.days
    return out.sort_values("depth").head(top).reset_index(drop=True)


def exposures(positions: pd.DataFrame, by: str, freq: str = "ME") -> pd.DataFrame:
    """Average weight of NAV by ``by`` (rating_bucket, sector, tenor bucket...) per period."""
    live = positions[positions["face"] > 0]
    if live.empty:
        return pd.DataFrame()
    daily = live.groupby(["date", live[by].fillna("unknown")])["weight"].sum().unstack(fill_value=0.0)
    return daily.resample(freq).mean().dropna(how="all").reset_index()


def concentration(positions: pd.DataFrame) -> pd.DataFrame:
    """Daily issuer concentration: issuers held, top issuer weight, Herfindahl of issuer weights."""
    live = positions[positions["face"] > 0]
    if live.empty:
        return pd.DataFrame(columns=["date", "n_issuers", "top_issuer_weight", "issuer_hhi"])
    w = live.groupby(["date", "issuer_id"])["weight"].sum()
    g = w.groupby(level=0)
    return pd.DataFrame({"n_issuers": g.size(), "top_issuer_weight": g.max(),
                         "issuer_hhi": g.apply(lambda s: float(((s / s.sum()) ** 2).sum()))}).reset_index()


def rolling_stats(nav: pd.Series, window: int = 126) -> pd.DataFrame:
    """Rolling annualised return, volatility and Sharpe (no cash adjustment) over ``window`` observations."""
    r = returns(nav.dropna())
    ppy = len(r) / _years(nav.index)
    mean = r.rolling(window).mean() * ppy
    vol = r.rolling(window).std() * math.sqrt(ppy)
    return pd.DataFrame({"ann_return": mean, "ann_vol": vol, "sharpe": mean / vol}).reset_index()
