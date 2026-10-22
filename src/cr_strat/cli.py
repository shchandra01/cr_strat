"""Command-line entry points for building the research panels (``cr-strat --help``)."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from cr_strat.amfi_debt_trades import build_mf_trades, mapping_report, to_price_trades
from cr_strat.cra_disclosures import disclosure_summary, load_cra_disclosures, unmapped_issuers
from cr_strat.curves import BenchmarkCurve, SpreadBenchmark
from cr_strat.event_prices import DEFAULT_ANCHORS, event_price_paths
from cr_strat.fbil import GSEC_KINDS, MM_KINDS, combine_curves, load_fbil_gsec_curve, load_fbil_money_market
from cr_strat.flows import DEFAULT_WINDOWS, event_flows, parse_windows
from cr_strat.mandates import load_mandates
from cr_strat.mf_portfolio import parse_mf_portfolio
from cr_strat.ownership import event_ownership, load_events, load_securities
from cr_strat.price_panel import build_price_panel, read_price_panel
from cr_strat.rating_actions import load_rating_actions
from cr_strat.rating_events import build_rating_events, rating_timeline
from cr_strat.rating_scale import parse_rating, parse_ratings
from cr_strat.schemes import attach_categories, parse_amfi_navall
from cr_strat.textparse import UNIT_MULTIPLIERS
from cr_strat.trades import clean_trades, isin_daily, load_trades, read_trades


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
        args.events, args.holdings, args.securities, load_mandates(args.mandates),
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
    _write(pd.concat(reports, ignore_index=True), out.with_name(out.stem + "_report.csv"))
    return 0


def _price_panel(args: argparse.Namespace) -> int:
    out = Path(args.out_dir)
    trades = clean_trades(
        read_trades(args.trades), min_amount_inr=args.min_amount_inr,
        dedupe_across_venues=args.dedupe_across_venues,
    )
    curve = BenchmarkCurve.from_csv(
        args.benchmark, compounding=args.benchmark_compounding,
        max_extrapolation_years=args.benchmark_max_extrapolation_years,
    )
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


def _cra_disclosures(args: argparse.Namespace) -> int:
    out = Path(args.out)
    actions = load_cra_disclosures(
        args.files, args.agency, kind=args.kind, issuer_map=args.issuer_map, as_of=args.as_of
    )
    _write(actions, out)
    _write(disclosure_summary(actions), out.with_name(out.stem + "_summary.csv"))
    _write(unmapped_issuers(actions), out.with_name(out.stem + "_unmapped_issuers.csv"))
    return 0


def _amfi_trades(args: argparse.Namespace) -> int:
    out = Path(args.out)
    trades, matches = build_mf_trades(
        args.files, navall=args.navall, scheme_overrides=args.scheme_overrides,
        keep_no_isin=args.keep_no_isin,
    )
    _write(trades, out)
    _write(mapping_report(trades), out.with_name(out.stem + "_mappings.csv"))
    if matches is not None:
        _write(matches, out.with_name(out.stem + "_scheme_matches.csv"))
    if args.price_trades_out:
        _write(to_price_trades(trades, include_inter_scheme=args.include_inter_scheme), Path(args.price_trades_out))
    return 0


def _flows(args: argparse.Namespace) -> int:
    windows = parse_windows(args.windows) if args.windows else DEFAULT_WINDOWS
    result = event_flows(args.events, args.mf_trades, args.securities, load_mandates(args.mandates), windows=windows)
    _write(result, Path(args.out))
    return 0


def _fbil_curve(args: argparse.Namespace) -> int:
    _write(combine_curves(load_fbil_gsec_curve(f, args.kind) for f in args.files), Path(args.out))
    return 0


def _fbil_mm(args: argparse.Namespace) -> int:
    _write(combine_curves(load_fbil_money_market(f, args.kind) for f in args.files), Path(args.out))
    return 0


def _parse_ratings(args: argparse.Namespace) -> int:
    df = pd.read_csv(args.input, dtype=str)
    keep = [c for c in (args.keep or "").split(",") if c]
    rows = []
    for _, row in df.iterrows():
        raw = row[args.column]
        chosen = {k: v for k, v in asdict(parse_rating(raw)).items() if k != "raw"}
        for i, seg in enumerate(parse_ratings(raw)):
            fields = asdict(seg)
            rec = {c: row.get(c) for c in keep}
            rec.update(segment_index=i, segment_raw=fields.pop("raw"), **fields, is_plain=seg.is_plain)
            rec["selected"] = fields == chosen
            rows.append(rec)
    _write(pd.DataFrame(rows), Path(args.out))
    return 0


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
    p.add_argument("--benchmark-max-extrapolation-years", type=float,
                   help="leave spreads empty beyond the curve's longest tenor plus this (default: flat)")
    _add_peer_args(p)
    p.set_defaults(func=_price_panel)

    p = sub.add_parser("event-prices", help="spread paths around rating events against matched peers")
    p.add_argument("--events", required=True, help="rating_events.csv")
    p.add_argument("--panel", required=True, help="price_panel.csv")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--anchors", default=",".join(str(a) for a in DEFAULT_ANCHORS),
                   help="days relative to the event")
    _add_peer_args(p)
    p.set_defaults(func=_event_prices)

    p = sub.add_parser("cra-disclosures", help="rating actions from CRAs' SEBI movement/history spreadsheets")
    p.add_argument("files", nargs="+")
    p.add_argument("--agency", required=True, help="e.g. ICRA, CARE, 'India Ratings', Acuite, Infomerics, Brickwork")
    p.add_argument("--kind", choices=["movement", "history"], help="default: detect both")
    p.add_argument("--issuer-map", help="CSV of name, issuer_id")
    p.add_argument("--as-of", help="snapshot date (YYYY-MM-DD) for current-rating rows when the file has none")
    p.add_argument("--out", required=True)
    p.set_defaults(func=_cra_disclosures)

    p = sub.add_parser("amfi-trades", help="MF debt and money-market trades from AMFI's daily disclosure")
    p.add_argument("files", nargs="+", help="AMFI xlsx exports or saved JSON pages")
    p.add_argument("--out", required=True)
    p.add_argument("--navall", help="AMFI NAVAll.txt, to attach scheme categories")
    p.add_argument("--scheme-overrides", help="CSV of amc, scheme_name, category")
    p.add_argument("--keep-no-isin", action="store_true", help="keep TREPS/repo rows that have no ISIN")
    p.add_argument("--price-trades-out", help="also write market trades in the load-trades format")
    p.add_argument("--include-inter-scheme", action="store_true", help="keep inter-scheme transfers as prices")
    p.set_defaults(func=_amfi_trades)

    p = sub.add_parser("flows", help="MF selling and buying of event bonds, by constrained and other funds")
    p.add_argument("--events", required=True)
    p.add_argument("--mf-trades", required=True, nargs="+", help="CSVs written by amfi-trades")
    p.add_argument("--securities", required=True)
    p.add_argument("--mandates", default="config/mandates.toml")
    p.add_argument("--windows", help="day windows around the event, e.g. -30:-1,0:10,0:30")
    p.add_argument("--out", required=True)
    p.set_defaults(func=_flows)

    p = sub.add_parser("fbil-curve", help="G-sec par or zero curve from FBIL daily workbooks")
    p.add_argument("files", nargs="+")
    p.add_argument("--kind", choices=tuple(GSEC_KINDS), default="par")
    p.add_argument("--out", required=True)
    p.set_defaults(func=_fbil_curve)

    p = sub.add_parser("fbil-mm", help="MIBOR-OIS or T-bill curve from FBIL data, on an annual basis")
    p.add_argument("files", nargs="+")
    p.add_argument("--kind", choices=MM_KINDS, required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=_fbil_mm)

    p = sub.add_parser("parse-ratings", help="split and parse rating strings in a CSV column, for auditing a source")
    p.add_argument("input")
    p.add_argument("--column", default="rating")
    p.add_argument("--keep", help="comma-separated columns to carry through")
    p.add_argument("--out", required=True)
    p.set_defaults(func=_parse_ratings)

    args = parser.parse_args(argv)
    return args.func(args)
