"""Backtest configuration files, output writing and parameter sweeps.

A backtest config is a TOML file with four tables (see ``config/backtest_*.toml``):

    [backtest]          BacktestConfig fields: dates, capital, limits, cash rate
    [execution]         ExecutionModel fields; [execution.half_spread_bps] by rating bucket
    [strategy]          name = one of strategies.STRATEGIES, plus [strategy.params]
    [benchmark]         optional second strategy run alongside for relative statistics

Data paths stay on the command line so one config can run on several datasets.

``sweep`` reruns one config over a grid of parameter overrides on the same
market and reports each run's statistics for the whole period and, with a
``split_date``, separately before and after it. Choose parameters on the
first period and judge them on the second. The deflated Sharpe ratio in the
output corrects the best run's Sharpe for the number of combinations tried.
"""

from __future__ import annotations

import dataclasses
import itertools
import tomllib
from pathlib import Path

import numpy as np
import pandas as pd

from cr_strat import analytics
from cr_strat.backtest import BacktestConfig, BacktestResult, run_backtest
from cr_strat.curves import BenchmarkCurve
from cr_strat.execution import ExecutionModel
from cr_strat.market import MarketData
from cr_strat.ownership import load_securities
from cr_strat.price_panel import read_price_panel
from cr_strat.rating_actions import load_rating_actions
from cr_strat.rating_events import rating_timeline
from cr_strat.stats import deflated_sharpe, sharpe_moments
from cr_strat.strategies import Strategy, build_strategy

_SECTIONS = ("backtest", "execution", "strategy", "benchmark")


def load_config(source: str | Path | dict) -> dict:
    raw = dict(source) if isinstance(source, dict) else tomllib.loads(Path(source).read_text())
    unknown = sorted(set(raw) - set(_SECTIONS))
    if unknown:
        raise ValueError(f"unknown config sections {unknown}; expected {list(_SECTIONS)}")
    if "strategy" not in raw or "name" not in raw["strategy"]:
        raise ValueError("config needs [strategy] with a name")
    return raw


def _parse_value(text: str):
    try:
        return tomllib.loads(f"v = {text}")["v"]
    except tomllib.TOMLDecodeError:
        return text


def set_param(raw: dict, key: str, value) -> dict:
    """Copy of ``raw`` with a dotted key set: ``strategy.hold_days`` sets a strategy parameter,
    ``backtest.start`` or ``execution.lag_days`` an engine one."""
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in raw.items()}
    section, _, name = key.partition(".")
    if section not in _SECTIONS or not name:
        raise ValueError(f"parameter keys look like strategy.hold_days or execution.lag_days, got {key!r}")
    value = _parse_value(value) if isinstance(value, str) else value
    table = out.setdefault(section, {})
    if section in ("strategy", "benchmark") and name != "name":
        table["params"] = dict(table.get("params", {}), **{name: value})
    else:
        table[name] = value
    return out


def _dataclass(cls, table: dict, section: str):
    fields = {f.name for f in dataclasses.fields(cls)}
    unknown = sorted(set(table) - fields)
    if unknown:
        raise ValueError(f"[{section}] unknown keys {unknown}; valid: {sorted(fields)}")
    return cls(**table)


def build(raw: dict) -> tuple[BacktestConfig, ExecutionModel, Strategy, Strategy | None]:
    config = _dataclass(BacktestConfig, raw.get("backtest", {}), "backtest")
    ex = dict(raw.get("execution", {}))
    if "half_spread_bps" in ex:
        ex["half_spread_bps"] = {**ExecutionModel().half_spread_bps, **ex["half_spread_bps"]}
    execution = _dataclass(ExecutionModel, ex, "execution")
    strategy = build_strategy(raw["strategy"]["name"], raw["strategy"].get("params"))
    bench = raw.get("benchmark")
    benchmark = build_strategy(bench["name"], bench.get("params")) if bench else None
    return config, execution, strategy, benchmark


def load_market(
    panel, securities, benchmark_curve, rating_actions=None, *, benchmark_compounding: str = "semiannual",
    bond_compounding: str = "annual", match_on=("instrument_class", "rating_bucket", "tenor_bucket"),
    window_days: int = 5, min_issuers: int = 3, peer_adjust: bool = True,
) -> MarketData:
    """MarketData from the pipeline's files (price_panel.csv, securities.csv, curve CSV, rating actions)."""
    panels = [read_price_panel(p) for p in ([panel] if isinstance(panel, (str, Path)) else panel)]
    ratings = rating_timeline(load_rating_actions(rating_actions)) if rating_actions else None
    return MarketData(
        pd.concat(panels, ignore_index=True), load_securities(securities),
        BenchmarkCurve.from_csv(benchmark_curve, compounding=benchmark_compounding), ratings=ratings,
        bond_compounding=bond_compounding, match_on=match_on, window_days=window_days,
        min_issuers=min_issuers, peer_adjust=peer_adjust,
    )


def read_cash_rates(path) -> pd.Series:
    """Cash rate CSV with ``date`` and ``rate_pct`` (e.g. TREPS weighted average, MIBOR)."""
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    if not {"date", "rate_pct"} <= set(df.columns):
        raise ValueError("cash rate file needs columns date, rate_pct")
    return pd.Series(pd.to_numeric(df["rate_pct"], errors="coerce").to_numpy(),
                     index=pd.to_datetime(df["date"], format="ISO8601")).dropna()


def _write(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, date_format="%Y-%m-%d")
    print(f"wrote {len(df):>7} rows  {path}")


def write_result(result: BacktestResult, out_dir: str | Path, benchmark: BacktestResult | None = None,
                 *, report: bool = True, title: str | None = None, synthetic: bool = False) -> pd.DataFrame:
    """Write every table for one backtest (and its tearsheet); returns the summary table."""
    out = Path(out_dir)
    eps = analytics.episodes(result.positions)
    summary = analytics.summary(result, benchmark)
    nav = result.nav.set_index("date")
    _write(result.nav, out / "nav.csv")
    _write(result.positions, out / "positions.csv")
    _write(result.fills, out / "fills.csv")
    _write(result.orders, out / "orders.csv")
    _write(eps, out / "episodes.csv")
    _write(summary, out / "summary.csv")
    _write(analytics.attribution(result.nav), out / "attribution.csv")
    _write(analytics.monthly_returns(nav["nav"]), out / "monthly_returns.csv")
    _write(analytics.monthly_returns(nav["nav_hedged"]), out / "monthly_returns_hedged.csv")
    _write(analytics.drawdowns(nav["nav"], top=10), out / "drawdowns.csv")
    for by in ("rating_bucket", "sector"):
        _write(analytics.exposures(result.positions, by), out / f"exposure_{by}.csv")
    _write(analytics.concentration(result.positions), out / "concentration.csv")
    if benchmark is not None:
        _write(benchmark.nav, out / "benchmark_nav.csv")
    params = [{"section": "strategy", "key": "name", "value": result.strategy}]
    params += [{"section": "strategy", "key": k, "value": v} for k, v in result.params.items()]
    params += [{"section": "backtest", "key": k, "value": v} for k, v in dataclasses.asdict(result.config).items()]
    _write(pd.DataFrame(params), out / "params.csv")
    if report:
        from cr_strat.report import tearsheet

        path = out / "tearsheet.html"
        path.write_text(tearsheet(result, benchmark, episodes=eps, summary=summary, title=title,
                                  synthetic=synthetic), encoding="utf-8")
        print(f"wrote  report  {path}")
    for note in result.notes:
        print(f"note: {note}")
    return summary


def _period_stats(nav: pd.DataFrame, start=None, end=None) -> dict[str, float]:
    nv = nav.set_index("date")
    if start is not None:
        nv = nv[nv.index >= start]
    if end is not None:
        nv = nv[nv.index <= end]
    if len(nv) < 3:
        return {}
    rate = nv["cash_rate_pct"]
    total = analytics.performance_stats(nv["nav"], cash_rate_pct=rate)
    hedged = analytics.performance_stats(nv["nav_hedged"], cash_rate_pct=rate)
    return {"cagr": total.get("cagr"), "sharpe": total.get("sharpe"), "max_drawdown": total.get("max_drawdown"),
            "hedged_excess_return": hedged.get("ann_excess_return"), "hedged_sharpe": hedged.get("sharpe"),
            "hedged_max_drawdown": hedged.get("max_drawdown")}


def sweep(
    market: MarketData,
    raw: dict,
    grid: dict[str, list],
    *,
    events=None,
    ownership=None,
    cash_rates=None,
    split_date=None,
    progress: bool = True,
) -> pd.DataFrame:
    """Run every combination in ``grid`` (dotted key -> values); one row of statistics per run."""
    keys = list(grid)
    rows, excess = [], []
    combos = list(itertools.product(*(grid[k] for k in keys)))
    split = pd.Timestamp(split_date) if split_date else None
    for n, values in enumerate(combos, start=1):
        cfg = raw
        for k, v in zip(keys, values):
            cfg = set_param(cfg, k, v)
        config, execution, strategy, _ = build(cfg)
        res = run_backtest(market, strategy, config=config, execution=execution, events=events,
                           ownership=ownership, cash_rates=cash_rates)
        nav = res.nav.set_index("date")
        r = nav["nav_hedged"].pct_change().dropna()
        rf = (nav["cash_rate_pct"].shift(1) / 100 * nav.index.to_series().diff().dt.days / 365).reindex(r.index)
        excess.append(r - rf.fillna(0.0))
        row = dict(zip(keys, values))
        row.update({f"all_{k}": v for k, v in _period_stats(res.nav).items()})
        if split is not None:
            row.update({f"is_{k}": v for k, v in _period_stats(res.nav, end=split).items()})
            row.update({f"oos_{k}": v for k, v in _period_stats(res.nav, start=split + pd.Timedelta(days=1)).items()})
        trading = analytics.trading_stats(res)
        row.update({k: trading.get(k) for k in ("avg_positions", "turnover_annual", "cost_bps_annual", "n_fills")})
        rows.append(row)
        if progress:
            print(f"run {n}/{len(combos)}: {dict(zip(keys, values))} hedged sharpe "
                  f"{row.get('all_hedged_sharpe', np.nan):.2f}")
    out = pd.DataFrame(rows)
    sharpes = [sharpe_moments(e)[0] for e in excess]
    out["hedged_sharpe_per_period"] = sharpes
    out["deflated_sharpe"] = [deflated_sharpe(e, sharpes) for e in excess]
    return out


def parse_grid(items: list[str]) -> dict[str, list]:
    """``["strategy.hold_days=60,120", ...]`` -> ``{"strategy.hold_days": [60, 120]}``."""
    grid = {}
    for item in items:
        key, _, values = item.partition("=")
        if not values:
            raise ValueError(f"grid entries look like strategy.hold_days=60,120, got {item!r}")
        grid[key.strip()] = [_parse_value(v.strip()) for v in values.split(",")]
    return grid
