"""Command-line entry points: research panels, backtests, signal research and the demo (``cr-strat --help``)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

from cr_strat.curves import BenchmarkCurve, SpreadBenchmark
from cr_strat.event_prices import DEFAULT_ANCHORS, event_price_paths
from cr_strat.mandates import load_mandates
from cr_strat.mf_portfolio import parse_mf_portfolio
from cr_strat.ownership import event_ownership, load_events, load_securities
from cr_strat.price_panel import build_price_panel, read_price_panel
from cr_strat.rating_actions import load_rating_actions
from cr_strat.rating_events import build_rating_events, rating_timeline
from cr_strat.schemes import attach_categories, parse_amfi_navall
from cr_strat.textparse import UNIT_MULTIPLIERS
from cr_strat.trades import clean_trades, isin_daily, load_trades, read_trades


_REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"
# Side files the commands write next to their outputs; a glob like trades_*.csv also matches them.
_SIDE_FILE_SUFFIXES = ("_report.csv", "_scheme_matches.csv")


def config_dir() -> Path:
    """./config when run from the repo root, else the repo's config folder (editable install)."""
    return Path("config") if (Path("config") / "mandates.toml").exists() else _REPO_CONFIG


def _config_path(value: str) -> str:
    """A config file as given, else from the repo's config folder: a file name or a bare strategy name."""
    path = Path(value)
    if path.exists():
        return str(path)
    for candidate in (config_dir() / path.name, config_dir() / f"backtest_{path.stem}.toml"):
        if candidate.exists():
            return str(candidate)
    raise FileNotFoundError(f"config not found: {value} (also looked in {config_dir()})")


def _inputs(paths: list[str], what: str) -> list[str]:
    """Drop the report files a shell glob picks up alongside the real inputs, saying so."""
    keep = [p for p in paths if not p.endswith(_SIDE_FILE_SUFFIXES)]
    skipped = [p for p in paths if p.endswith(_SIDE_FILE_SUFFIXES)]
    if skipped:
        print(f"note: ignoring {len(skipped)} report file(s) among the {what}: {', '.join(skipped)}")
    if not keep:
        raise ValueError(f"no {what} files left after dropping report files")
    return keep


def _write(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, date_format="%Y-%m-%d")
    print(f"wrote {len(df):>7} rows  {path}")


def _rating_events(args: argparse.Namespace) -> int:
    out = Path(args.out_dir)
    actions = load_rating_actions(args.actions)
    mandates = load_mandates(args.mandates)
    timeline, events = build_rating_events(
        actions, mandates, dedup_days=args.dedup_days, signal_lookback_days=args.signal_lookback_days
    )
    _write(actions, out / "rating_actions_normalized.csv")
    _write(actions[~actions["parse_ok"]], out / "rating_parse_failures.csv")
    _write(timeline, out / "effective_ratings.csv")
    _write(events, out / "rating_events.csv")
    unverified = sorted({m.name for m in mandates if m.verify})
    if unverified:
        print(f"note: rules not yet verified against primary sources: {', '.join(unverified)}")
    return 0


def _parse_mf(args: argparse.Namespace) -> int:
    frames, reports = [], []
    for f in args.files:
        holdings, report = parse_mf_portfolio(f, args.amc, as_of=args.as_of, mv_unit=args.mv_unit)
        frames.append(holdings)
        reports.append(report)
    holdings = pd.concat(frames, ignore_index=True)
    out = Path(args.out)
    if args.navall:
        overrides = pd.read_csv(args.scheme_overrides) if args.scheme_overrides else None
        holdings, matches = attach_categories(holdings, parse_amfi_navall(args.navall), overrides)
        _write(matches, out.with_name(out.stem + "_scheme_matches.csv"))
        unmatched = (matches["match"] == "none").sum()
        if unmatched:
            print(f"note: {unmatched} schemes have no category; add them to --scheme-overrides")
    _write(holdings, out)
    _write(pd.concat(reports, ignore_index=True), out.with_name(out.stem + "_report.csv"))
    return 0


def _ownership(args: argparse.Namespace) -> int:
    horizons = tuple(int(h) for h in args.horizons.split(","))
    result = event_ownership(
        args.events, _inputs(args.holdings, "holdings files"), args.securities, load_mandates(args.mandates),
        max_staleness_days=args.max_staleness_days, horizons=horizons,
        tolerance_days=args.tolerance_days,
    )
    _write(result, Path(args.out))
    return 0


def _load_trades(args: argparse.Namespace) -> int:
    frames, reports = [], []
    for f in args.files:
        trades, report = load_trades(f, args.venue, trade_date=args.trade_date, amount_unit=args.amount_unit)
        frames.append(trades)
        reports.append(report)
    out = Path(args.out)
    _write(pd.concat(frames, ignore_index=True), out)
    report = pd.concat(reports, ignore_index=True)
    _write(report, out.with_name(out.stem + "_report.csv"))
    for _, r in report[report["note"].notna() | (report["status"] != "parsed")].iterrows():
        print(f"note: {r['source_file']} [{r['sheet']}]: {r['status']}; {r['note']}")
    return 0


def _price_panel(args: argparse.Namespace) -> int:
    out = Path(args.out_dir)
    raw = read_trades(_inputs(args.trades, "trade files"))
    no_yield = int(raw["yield_pct"].isna().sum())
    if no_yield:
        print(f"note: {no_yield} of {len(raw)} trades have no yield and are dropped; spreads need reported yields")
    trades = clean_trades(raw, min_amount_inr=args.min_amount_inr, dedupe_across_venues=args.dedupe_across_venues)
    curve = BenchmarkCurve.from_csv(args.benchmark, compounding=args.benchmark_compounding)
    ratings = rating_timeline(load_rating_actions(args.rating_actions))
    panel = build_price_panel(
        isin_daily(trades), load_securities(args.securities), curve, ratings,
        bond_compounding=args.bond_compounding,
    )
    print(f"{int(trades['outlier'].sum())} of {len(trades)} trades flagged as yield outliers")
    _write(panel, out / "price_panel.csv")
    peers = SpreadBenchmark(panel, match_on=args.match_on.split(","), window_days=args.window_days,
                            min_issuers=args.min_issuers)
    _write(peers.table(), out / "peer_spreads_weekly.csv")
    return 0


def _event_prices(args: argparse.Namespace) -> int:
    out = Path(args.out_dir)
    bonds, events = event_price_paths(
        load_events(args.events), read_price_panel(args.panel),
        anchors=tuple(int(a) for a in args.anchors.split(",")),
        match_on=args.match_on.split(","), window_days=args.window_days, min_issuers=args.min_issuers,
    )
    _write(bonds, out / "event_bond_prices.csv")
    _write(events, out / "event_prices.csv")
    return 0


def _market(args: argparse.Namespace):
    from cr_strat.runner import load_market

    return load_market(
        args.panel, args.securities, args.benchmark, args.rating_actions,
        benchmark_compounding=args.benchmark_compounding, bond_compounding=args.bond_compounding,
        match_on=args.match_on.split(","), window_days=args.window_days, min_issuers=args.min_issuers,
        peer_adjust=not args.no_peer_adjust,
    )


def _events(args: argparse.Namespace):
    events = pd.read_csv(args.events, dtype=str) if args.events else None
    ownership = pd.read_csv(args.ownership, dtype=str) if args.ownership else None
    return events, ownership


def _backtest(args: argparse.Namespace) -> int:
    from cr_strat.backtest import run_backtest
    from cr_strat.backtest import read_cash_rates
    from cr_strat.runner import build, load_config, set_param, write_result

    raw = load_config(args.config)
    for item in args.set or []:
        key, _, value = item.partition("=")
        raw = set_param(raw, key.strip(), value.strip())
    config, execution, strategy, bench_strategy = build(raw)
    market = _market(args)
    events, ownership = _events(args)
    cash = read_cash_rates(args.cash_rates) if args.cash_rates else None
    result = run_backtest(market, strategy, config=config, execution=execution, events=events,
                          ownership=ownership, cash_rates=cash)
    bench = None
    if bench_strategy is not None:
        bench = run_backtest(market, bench_strategy, config=config, execution=execution, events=events,
                             ownership=ownership, cash_rates=cash)
    summary = write_result(result, Path(args.out_dir), bench, report=not args.no_report, title=args.title,
                           synthetic=args.synthetic)
    _print_headline(summary)
    return 0


def _print_headline(summary: pd.DataFrame) -> None:
    s = summary.set_index("metric")
    pick = [("cagr", "CAGR", True), ("sharpe", "Sharpe", False), ("max_drawdown", "max drawdown", True),
            ("ann_excess_return", "excess return over cash", True)]
    for key, label, pct in pick:
        if key in s.index:
            fmt = (lambda v: f"{v:7.2%}") if pct else (lambda v: f"{v:7.2f}")
            print(f"{label:>26}: total {fmt(s.at[key, 'total'])}   rates-hedged {fmt(s.at[key, 'hedged'])}")


def _sweep(args: argparse.Namespace) -> int:
    from cr_strat.backtest import read_cash_rates
    from cr_strat.runner import load_config, parse_grid, sweep

    market = _market(args)
    events, ownership = _events(args)
    cash = read_cash_rates(args.cash_rates) if args.cash_rates else None
    result = sweep(market, load_config(args.config), parse_grid(args.grid), events=events, ownership=ownership,
                   cash_rates=cash, split_date=args.split_date)
    _write(result, Path(args.out))
    print(f"note: {len(result)} combinations tried; read deflated_sharpe, not the best raw Sharpe")
    return 0


def _signals(args: argparse.Namespace) -> int:
    from cr_strat import research

    market = _market(args)
    out = Path(args.out_dir)
    horizons = tuple(int(h) for h in args.horizons.split(","))
    panel = research.signal_panel(market, start=args.start, end=args.end, freq=args.freq,
                                  max_age_days=args.max_age_days)
    panel = research.forward_returns(market, panel, horizons, entry=args.entry,
                                     max_end_age_days=args.max_end_age_days)
    signals = args.signals.split(",") if args.signals else research.DEFAULT_SIGNALS
    summary, quantiles, by_date = research.evaluate_signals(
        panel, signals, horizons, ret=args.ret, n_quantiles=args.quantiles, neutralize_by=args.neutralize_by)
    _write(panel, out / "signal_panel.csv")
    _write(summary, out / "signal_summary.csv")
    _write(quantiles, out / "signal_quantiles.csv")
    _write(by_date, out / "signal_ic_by_date.csv")
    if len(summary):
        cols = ["signal", "horizon_days", "avg_n", "mean_ic", "t_stat_nw", "long_short_mean", "long_short_t_nw"]
        print(summary[cols].to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    return 0


def _regress(args: argparse.Namespace) -> int:
    from cr_strat.stats import ols

    df = pd.read_csv(args.data)
    for extra in args.merge or []:
        df = df.merge(pd.read_csv(extra), on=args.on.split(","), how="left", suffixes=("", "_merged"))
    for item in args.assign or []:
        name, _, expr = item.partition("=")
        df[name.strip()] = df.eval(expr)
    if args.where:
        df = df.query(args.where)
    result = ols(df, args.y, args.x.split(","), cluster=args.cluster)
    a = result.attrs
    print(f"OLS {a['y']} on {args.x}: n={a['n']}, R2={a['r2']:.3f}, errors={a['cov']}"
          + (f" ({a['n_clusters']} clusters)" if a["n_clusters"] else ""))
    print(result.to_string(index=False, float_format=lambda v: f"{v:.4g}"))
    if args.out:
        _write(result.assign(n=a["n"], r2=a["r2"], cov=a["cov"], n_clusters=a["n_clusters"]), Path(args.out))
    return 0


def _synth(args: argparse.Namespace) -> int:
    from cr_strat.synthetic import SyntheticConfig, generate_market, write_market

    cfg = SyntheticConfig(start=args.start, end=args.end, n_issuers=args.issuers, seed=args.seed,
                          overshoot_bps_per_share=args.overshoot_bps_per_share)
    market = generate_market(cfg)
    paths = write_market(market, Path(args.out_dir))
    for name, path in paths.items():
        print(f"wrote {len(market.get(name, [])) if name in market else '':>7}  {path}")
    print(f"note: synthetic data; {len(market['truth'])} planted forced-selling events in truth_events.csv")
    return 0


def _demo(args: argparse.Namespace) -> int:
    from cr_strat.demo import run_demo

    return run_demo(Path(args.out_dir), seed=args.seed, n_issuers=args.issuers, start=args.start, end=args.end)


def _add_market_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--panel", required=True, nargs="+", help="price_panel.csv from price-panel")
    p.add_argument("--securities", required=True, help="security master with coupon_pct and maturity_date")
    p.add_argument("--benchmark", required=True, help="curve CSV: date, tenor_years, yield_pct")
    p.add_argument("--rating-actions", help="rating actions CSV, for ratings as of each date")
    p.add_argument("--bond-compounding", choices=["annual", "semiannual"], default="annual")
    p.add_argument("--benchmark-compounding", choices=["annual", "semiannual"], default="semiannual")
    p.add_argument("--no-peer-adjust", action="store_true",
                   help="carry stale marks flat instead of moving them with their peer group")
    _add_peer_args(p)


def _add_peer_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--match-on", default="instrument_class,rating_bucket,tenor_bucket",
                   help="peer-group columns; add 'sector' if the security master has it")
    p.add_argument("--window-days", type=int, default=5, help="trailing days pooled for peer spreads")
    p.add_argument("--min-issuers", type=int, default=3, help="fewest peer issuers for a benchmark")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cr-strat", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("rating-events", help="normalise rating actions and detect threshold crossings")
    p.add_argument("--actions", required=True, help="rating actions CSV (see data/README.md)")
    p.add_argument("--mandates", default="config/mandates.toml")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--dedup-days", type=int, default=365)
    p.add_argument("--signal-lookback-days", type=int, default=90)
    p.set_defaults(func=_rating_events)

    p = sub.add_parser("parse-mf", help="parse one AMC's portfolio disclosure workbooks")
    p.add_argument("files", nargs="+")
    p.add_argument("--amc", required=True, help="AMC name as AMFI writes it, e.g. 'HDFC Mutual Fund'")
    p.add_argument("--out", required=True, help="holdings CSV to write")
    p.add_argument("--as-of", help="portfolio date (YYYY-MM-DD) when the files do not state it")
    p.add_argument("--mv-unit", choices=sorted(UNIT_MULTIPLIERS), help="market value unit if not stated")
    p.add_argument("--navall", help="AMFI NAVAll.txt, used to attach SEBI scheme categories")
    p.add_argument("--scheme-overrides", help="CSV of amc, scheme_name, category for unmatched schemes")
    p.set_defaults(func=_parse_mf)

    p = sub.add_parser("ownership", help="constrained ownership and holder selling around events")
    p.add_argument("--events", required=True)
    p.add_argument("--holdings", required=True, nargs="+")
    p.add_argument("--securities", required=True)
    p.add_argument("--mandates", default="config/mandates.toml")
    p.add_argument("--out", required=True)
    p.add_argument("--max-staleness-days", type=int, default=45)
    p.add_argument("--horizons", default="30,60,90", help="comma-separated days after the event")
    p.add_argument("--tolerance-days", type=int, default=15)
    p.set_defaults(func=_ownership)

    p = sub.add_parser("load-trades", help="read NSE/BSE reported-trade files into one trades CSV")
    p.add_argument("files", nargs="+")
    p.add_argument("--venue", required=True, help="e.g. NSE or BSE")
    p.add_argument("--out", required=True)
    p.add_argument("--trade-date", help="trade date (YYYY-MM-DD) for files without a date column")
    p.add_argument("--amount-unit", choices=sorted(UNIT_MULTIPLIERS), help="traded value unit if not stated")
    p.set_defaults(func=_load_trades)

    p = sub.add_parser("price-panel", help="ISIN-day yields and spreads with rating and tenor peer groups")
    p.add_argument("--trades", required=True, nargs="+", help="CSVs written by load-trades")
    p.add_argument("--securities", required=True)
    p.add_argument("--benchmark", required=True, help="curve CSV: date, tenor_years, yield_pct")
    p.add_argument("--rating-actions", required=True, help="rating actions CSV, for as-of ratings")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--bond-compounding", choices=["annual", "semiannual"], default="annual")
    p.add_argument("--benchmark-compounding", choices=["annual", "semiannual"], default="semiannual")
    p.add_argument("--min-amount-inr", type=float, default=0.0, help="drop trades smaller than this")
    p.add_argument("--dedupe-across-venues", action="store_true")
    _add_peer_args(p)
    p.set_defaults(func=_price_panel)

    p = sub.add_parser("event-prices", help="spread paths around rating events against matched peers")
    p.add_argument("--events", required=True, help="rating_events.csv")
    p.add_argument("--panel", required=True, help="price_panel.csv")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--anchors", default=",".join(str(a) for a in DEFAULT_ANCHORS),
                   help="days relative to the event; write --anchors=-60,10,60 when the first is negative")
    _add_peer_args(p)
    p.set_defaults(func=_event_prices)

    p = sub.add_parser("backtest", help="run a strategy backtest from a TOML config")
    p.add_argument("--config", required=True,
                   help="e.g. config/backtest_event_reversal.toml, or just event_reversal for a shipped config")
    _add_market_args(p)
    p.add_argument("--events", help="rating_events.csv, for event strategies")
    p.add_argument("--ownership", help="event_ownership.csv, for ownership filters")
    p.add_argument("--cash-rates", help="CSV of date, rate_pct (TREPS or MIBOR) for interest on cash")
    p.add_argument("--set", action="append", metavar="KEY=VALUE",
                   help="override a config value, e.g. strategy.hold_days=60 (repeatable)")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--title", help="tearsheet title")
    p.add_argument("--no-report", action="store_true", help="skip the HTML tearsheet")
    p.add_argument("--synthetic", action="store_true", help="label the tearsheet as built on synthetic data")
    p.set_defaults(func=_backtest)

    p = sub.add_parser("sweep", help="rerun a backtest over a parameter grid, with in/out-of-sample statistics")
    p.add_argument("--config", required=True, help="as for backtest")
    _add_market_args(p)
    p.add_argument("--events")
    p.add_argument("--ownership")
    p.add_argument("--cash-rates")
    p.add_argument("--grid", required=True, action="append", metavar="KEY=V1,V2",
                   help="e.g. strategy.hold_days=60,120,250 (repeatable)")
    p.add_argument("--split-date", help="report statistics separately up to and after this date")
    p.add_argument("--out", required=True)
    p.set_defaults(func=_sweep)

    p = sub.add_parser("signals", help="information coefficients and quantile returns for bond signals")
    _add_market_args(p)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--signals", help="comma-separated signal columns; default spread_bps, rv_bps, "
                                     "spread_chg_20d, spread_chg_60d, trade_days")
    p.add_argument("--horizons", default="20,60,120", help="forward return horizons, calendar days")
    p.add_argument("--freq", default="W-FRI", help="sampling frequency")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--max-age-days", type=int, default=5, help="bonds must have traded this recently")
    p.add_argument("--entry", choices=["next_trade", "mark"], default="next_trade",
                   help="start returns at the next reported trade (default) or the sample-date mark")
    p.add_argument("--max-end-age-days", type=int, default=30, help="drop returns ending on staler marks")
    p.add_argument("--ret", choices=["xr", "tr", "dspread"], default="xr",
                   help="excess over duration-matched risk-free (xr), total (tr) or spread change")
    p.add_argument("--quantiles", type=int, default=5)
    p.add_argument("--neutralize-by", help="rank signals within this column first, e.g. rating_bucket")
    p.set_defaults(func=_signals)

    p = sub.add_parser("regress", help="OLS with robust or clustered errors on any research CSV")
    p.add_argument("--data", required=True)
    p.add_argument("--merge", action="append", help="CSV to left-join on --on (repeatable)")
    p.add_argument("--on", default="event_id")
    p.add_argument("--assign", action="append", metavar="NAME=EXPR",
                   help="derived column, e.g. reversal=first_post_abn_bps-p120_abn_bps (repeatable)")
    p.add_argument("--where", help="row filter, e.g. \"direction == 'down'\"")
    p.add_argument("--y", required=True)
    p.add_argument("--x", required=True, help="comma-separated regressors")
    p.add_argument("--cluster", help="cluster errors on this column, e.g. issuer_id")
    p.add_argument("--out")
    p.set_defaults(func=_regress)

    p = sub.add_parser("synth", help="write a synthetic market (all raw input files) with planted effects")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--issuers", type=int, default=200)
    p.add_argument("--start", default="2019-01-01")
    p.add_argument("--end", default="2024-12-31")
    p.add_argument("--overshoot-bps-per-share", type=float, default=300.0)
    p.set_defaults(func=_synth)

    p = sub.add_parser("demo", help="generate a synthetic market and run the whole pipeline on it")
    p.add_argument("--out-dir", default="data/demo")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--issuers", type=int, default=200)
    p.add_argument("--start", default="2019-01-01")
    p.add_argument("--end", default="2024-12-31")
    p.set_defaults(func=_demo)

    args = parser.parse_args(argv)
    try:
        if getattr(args, "mandates", None) == "config/mandates.toml":
            args.mandates = str(config_dir() / "mandates.toml")
        if getattr(args, "config", None):
            args.config = _config_path(args.config)
        return args.func(args)
    except (ValueError, TypeError, FileNotFoundError, KeyError) as exc:
        if os.environ.get("CR_STRAT_DEBUG"):
            raise
        print(f"error: {exc}", file=sys.stderr)
        print("(set CR_STRAT_DEBUG=1 for the full traceback)", file=sys.stderr)
        return 2
