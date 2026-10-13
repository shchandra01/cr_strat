"""End-to-end demo on a synthetic market.

Generates a synthetic Indian corporate bond market (``synthetic``), then runs
every step a real study would, through the same commands:

1. ``rating-events``, ``ownership``, ``load-trades`` (NSE and BSE),
   ``price-panel`` and ``event-prices``;
2. a backtest of each ``config/backtest_*.toml`` strategy, with the
   ``universe_index`` benchmark run once and shared;
3. ``signals`` research on the price panel;
4. the forced-selling regressions: abnormal widening at the first post-event
   trade, and its reversal by t+120, on the pre-event constrained share.

Because the effects are planted, the outputs can be checked against
``raw/truth_events.csv``: the regressions should find widening that rises with
the constrained share and then reverts. Nothing here says anything about the
real market.
"""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd

from cr_strat.cli import main as cli
from cr_strat.runner import build, load_config, load_market, write_result

_REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


def config_dir() -> Path:
    """The repo's config folder (editable install), else ./config."""
    return _REPO_CONFIG if (_REPO_CONFIG / "mandates.toml").exists() else Path("config")


def _step(label: str, args: list[str], *, optional: bool = False) -> None:
    print(f"\n== {label}")
    try:
        if cli(args) != 0:
            raise RuntimeError(f"{label} failed")
    except ValueError as exc:
        if not optional:
            raise
        print(f"skipped: {exc}")


def run_demo(out: Path, *, seed: int = 7, n_issuers: int = 200, start: str = "2019-01-01",
             end: str = "2024-12-31") -> int:
    from cr_strat.backtest import run_backtest

    t0 = time.time()
    raw, work = out / "raw", out / "out"
    cfg = config_dir()
    _step("synthetic market", ["synth", "--out-dir", str(raw), "--seed", str(seed), "--issuers", str(n_issuers),
                               "--start", start, "--end", end])
    _step("rating events", ["rating-events", "--actions", str(raw / "rating_actions.csv"),
                            "--mandates", str(cfg / "mandates.toml"), "--out-dir", str(work)])
    _step("ownership", ["ownership", "--events", str(work / "rating_events.csv"),
                        "--holdings", str(raw / "holdings_mf.csv"), "--securities", str(raw / "securities.csv"),
                        "--mandates", str(cfg / "mandates.toml"), "--out", str(work / "event_ownership.csv")])
    for venue in ("nse", "bse"):
        files = sorted(str(p) for p in (raw / "trades" / venue).glob("*.csv"))
        _step(f"load trades ({venue.upper()})", ["load-trades", *files, "--venue", venue.upper(),
                                                 "--out", str(work / f"trades_{venue}.csv")])
    _step("price panel", ["price-panel", "--trades", str(work / "trades_nse.csv"), str(work / "trades_bse.csv"),
                          "--securities", str(raw / "securities.csv"), "--benchmark", str(raw / "gsec_par_curve.csv"),
                          "--rating-actions", str(raw / "rating_actions.csv"), "--out-dir", str(work)])
    _step("event price paths", ["event-prices", "--events", str(work / "rating_events.csv"),
                                "--panel", str(work / "price_panel.csv"), "--out-dir", str(work)])

    print("\n== backtests")
    market = load_market(work / "price_panel.csv", raw / "securities.csv", raw / "gsec_par_curve.csv",
                         raw / "rating_actions.csv")
    events = pd.read_csv(work / "rating_events.csv", dtype=str)
    ownership = pd.read_csv(work / "event_ownership.csv", dtype=str)
    bench_cache: dict = {}
    rows = []
    for path in sorted(cfg.glob("backtest_*.toml")):
        config, execution, strategy, bench_strategy = build(load_config(path))
        print(f"-- {strategy.name} ({path.name})")
        result = run_backtest(market, strategy, config=config, execution=execution, events=events,
                              ownership=ownership)
        bench = None
        if bench_strategy is not None:
            key = (repr(bench_strategy), repr(config), repr(execution))
            if key not in bench_cache:
                bench_cache[key] = run_backtest(market, bench_strategy, config=config, execution=execution,
                                                events=events, ownership=ownership)
            bench = bench_cache[key]
        summary = write_result(result, work / "backtests" / strategy.name, bench,
                               title=f"{strategy.name.replace('_', ' ').capitalize()} (synthetic data)",
                               synthetic=True).set_index("metric")
        rows.append({"strategy": strategy.name, **{f"{k}": summary.at[k, "total"] for k in
                     ("cagr", "sharpe", "max_drawdown", "avg_positions", "turnover_annual", "cost_bps_annual")},
                     "hedged_excess_return": summary.at["ann_excess_return", "hedged"],
                     "hedged_sharpe": summary.at["sharpe", "hedged"]})
    if bench_cache:
        bench = next(iter(bench_cache.values()))
        summary = write_result(bench, work / "backtests" / bench.strategy, title="Universe index (synthetic data)",
                               synthetic=True).set_index("metric")
        rows.append({"strategy": bench.strategy, **{f"{k}": summary.at[k, "total"] for k in
                     ("cagr", "sharpe", "max_drawdown", "avg_positions", "turnover_annual", "cost_bps_annual")},
                     "hedged_excess_return": summary.at["ann_excess_return", "hedged"],
                     "hedged_sharpe": summary.at["sharpe", "hedged"]})
    comparison = pd.DataFrame(rows)
    comparison.to_csv(work / "backtests" / "comparison.csv", index=False)

    market_args = ["--panel", str(work / "price_panel.csv"), "--securities", str(raw / "securities.csv"),
                   "--benchmark", str(raw / "gsec_par_curve.csv"), "--rating-actions", str(raw / "rating_actions.csv")]
    _step("signal research", ["signals", *market_args, "--out-dir", str(work / "signals"),
                              "--neutralize-by", "rating_bucket"])
    common = ["--data", str(work / "event_prices.csv"), "--merge", str(work / "event_ownership.csv"),
              "--where", "mandate == 'mf_corporate_bond_fund' and direction == 'down'", "--x", "constrained_share"]
    _step("regression: widening at first post-event trade", [
        "regress", *common, "--y", "first_post_abn_bps", "--out", str(work / "regressions" / "widening.csv")],
        optional=True)
    _step("regression: reversal by t+120", [
        "regress", *common, "--assign", "reversal_bps = first_post_abn_bps - p120_abn_bps", "--y", "reversal_bps",
        "--out", str(work / "regressions" / "reversal.csv")], optional=True)

    print("\n== strategy comparison (synthetic data)")
    print(comparison.to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    print(f"\ndone in {time.time() - t0:.0f}s. Tearsheets: {work / 'backtests'}/*/tearsheet.html")
    print("note: every input was simulated; results check the machinery, not the market")
    return 0
