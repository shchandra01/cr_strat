"""Command-line entry points for building the research panels (``cr-strat --help``)."""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pandas as pd

from cr_strat.amfi_debt_trades import build_mf_trades, mapping_report, to_price_trades
from cr_strat.auctions import (
    REPORT_COLUMNS,
    build_auctions,
    holiday_discrepancies,
    load_fimmda_gsec,
    load_fimmda_sdl,
    load_handbook_auctions,
    normalise_switches,
    read_release_tables,
    settlement_evidence,
)
from cr_strat.bonds import FixedRateBond, ZeroCouponBond
from cr_strat.calendars import mumbai_settlement_calendar
from cr_strat.conventions import CONVENTIONS, FINAL_PERIOD_RULES, as_date
from cr_strat.cra_disclosures import disclosure_summary, load_cra_disclosures, unmapped_issuers
from cr_strat.curves import BenchmarkCurve, SpreadBenchmark
from cr_strat.event_prices import DEFAULT_ANCHORS, event_price_paths
from cr_strat.fbil import GSEC_KINDS, MM_KINDS, combine_curves, load_fbil_gsec_curve, load_fbil_money_market
from cr_strat.flows import DEFAULT_WINDOWS, event_flows, parse_windows
from cr_strat.issuance import (
    build_ledger,
    coverage,
    coverage_path,
    outstanding_by_bucket,
    outstanding_on,
    read_ledger,
    read_snapshots,
    reconcile,
    supply_summary,
)
from cr_strat.mandates import load_mandates
from cr_strat.mf_portfolio import parse_mf_portfolio
from cr_strat.ownership import event_ownership, load_events, load_securities
from cr_strat.price_panel import build_price_panel, read_price_panel
from cr_strat.rating_actions import load_rating_actions
from cr_strat.rating_events import build_rating_events, rating_timeline
from cr_strat.rating_scale import parse_rating, parse_ratings
from cr_strat.rbi_releases import COLUMNS as RELEASE_COLUMNS
from cr_strat.rbi_releases import INDEX_COLUMNS, parse_releases
from cr_strat.schemes import attach_categories, parse_amfi_navall
from cr_strat.sovereign import GOI_LIST_COLUMNS, build_master, load_far_list, load_goi_outstanding, read_master
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


def _as_csv_text(df: pd.DataFrame) -> pd.DataFrame:
    """``df`` as the strings a CSV holds: dates ISO, ``published`` with its time, blanks empty."""
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            fmt = "%Y-%m-%dT%H:%M:%S" if col == "published" else "%Y-%m-%d"
            out[col] = out[col].dt.strftime(fmt)
    out = out.astype(object).where(out.notna(), "")
    return out.map(lambda v: v if isinstance(v, str) else (f"{v:.15g}" if isinstance(v, float) else str(v)))


def _upsert_csv(path: Path, new: pd.DataFrame, replace_on: list[str], order: list[str],
                sort: list[str]) -> pd.DataFrame:
    """Rows of the CSV at ``path`` whose ``replace_on`` values appear in ``new`` swapped for ``new``'s rows.

    Running twice on the same input leaves the file unchanged. Columns follow
    ``order`` (extra columns of an older file are kept at the end); rows are
    sorted by ``sort`` (numerically where the values are numbers).
    """
    fresh = _as_csv_text(new)
    if path.exists():
        old = pd.read_csv(path, dtype=str, keep_default_na=False)
        keys = set(map(tuple, fresh[replace_on].to_numpy().tolist())) if len(fresh) else set()
        kept = old[[tuple(r) not in keys for r in old[replace_on].to_numpy().tolist()]] if len(old) else old
        merged = pd.concat([kept, fresh], ignore_index=True).fillna("")
    else:
        merged = fresh
    cols = [c for c in order if c in merged] + [c for c in merged if c not in order]
    merged = merged.loc[:, cols]
    if len(merged):
        sort_keys = {c: pd.to_numeric(merged[c], errors="coerce").fillna(-1) if c in ("release_id", "line")
                     else merged[c] for c in sort}
        merged = merged.iloc[pd.DataFrame(sort_keys).sort_values(sort, kind="stable").index].reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(path, index=False)
    print(f"wrote {len(merged):>7} rows  {path}")
    return merged


def _keep_published(path: Path, table: pd.DataFrame) -> pd.DataFrame:
    """``table`` with each blank ``published`` filled from the CSV at ``path``, by release.

    Only a feed copy has a publication time. A release read again from its
    saved page alone must not lose the time an earlier run took from the feed.
    """
    if not len(table) or not table["published"].isna().any() or not path.exists():
        return table
    old = pd.read_csv(path, dtype=str, keep_default_na=False)
    if "published" not in old or "release_id" not in old:
        return table
    stored = old[old["published"] != ""].drop_duplicates("release_id").set_index("release_id")["published"]
    known = pd.to_datetime(table["release_id"].astype(str).map(stored))
    return table.assign(published=table["published"].fillna(known))


def _rbi_releases(args: argparse.Namespace) -> int:
    out = Path(args.out_dir)
    known = out / "gsec_announcement.csv"
    announcements = pd.read_csv(known, dtype=str, keep_default_na=False) if known.exists() else None
    if announcements is not None:
        announcements = announcements.replace({"": None})
    batch = parse_releases(args.files, announcements=announcements)
    for kind, table in batch.tables.items():
        path = out / f"{kind}.csv"
        if len(table) or path.exists():
            _upsert_csv(path, _keep_published(path, table), ["release_id"], list(RELEASE_COLUMNS[kind]),
                        ["release_date", "release_id", "line"])
    _upsert_csv(out / "releases.csv", batch.releases, ["release_id", "source_file"], list(INDEX_COLUMNS),
                ["release_date", "release_id", "source_file"])
    for s in batch.skipped:
        print(f"skipped {s.release_id or '-'} ({s.kind}, {s.source_file}): {s.reason}")
    return 0


def _sovereign_master(args: argparse.Namespace) -> int:
    lists = [load_goi_outstanding(f) for f in args.goi_list]
    if args.snapshots:
        path = Path(args.snapshots)
        fresh = pd.concat(lists, ignore_index=True) if lists else pd.DataFrame(columns=list(GOI_LIST_COLUMNS))
        stacked = _upsert_csv(path, fresh, ["as_of"], list(GOI_LIST_COLUMNS), ["as_of", "sl_no"])
        stacked = stacked.replace({"": None})
        for col in ("as_of", "issue_date", "maturity_date"):
            stacked[col] = pd.to_datetime(stacked[col])
        stacked["outstanding_cr"] = pd.to_numeric(stacked["outstanding_cr"])
        lists = [stacked]
    far = load_far_list(args.far) if args.far else None
    extra = []
    for f in args.extra or ():
        frame = pd.read_csv(f, dtype=str, keep_default_na=False).replace({"": None})
        frame.index = frame.index + 2  # a row's label is its line in the file (line 1 is the header)
        frame.attrs["source_file"] = Path(f).name
        extra.append(frame)
    try:
        master = build_master(lists, far, extra, as_of=args.as_of)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    _write(master, Path(args.out))
    return 0


def _validation(build, evidence: pd.DataFrame, reconciliation: pd.DataFrame | None) -> pd.DataFrame:
    """The build's report plus the holiday discrepancies and the reconciliation's large gaps, as one table."""
    rows = list(build.report.to_dict("records"))
    for _, d in holiday_discrepancies(evidence).iterrows():
        rows.append({"check": "holiday_evidence", "severity": "warning", "auction_date": d["auction_date"],
                     "security": d["securities"],
                     "detail": f"{d['verdict']}: calendar T+1 {d['calendar_t1']:%Y-%m-%d}, evidence "
                               f"({d['methods']}) points to {d['implied_dates']}",
                     "source_file": "", "source_row": ""})
    if reconciliation is not None:
        for _, r in reconciliation.iterrows():
            if pd.isna(r["difference_cr"]) or abs(r["difference_cr"]) > 1:
                if pd.notna(r["rbi_cr"]):
                    detail = f"ledger {r['ledger_cr']:.3f} against RBI {r['rbi_cr']:.3f} crore"
                else:
                    detail = f"ledger {r['ledger_cr']:.3f}, not in RBI's list"
                rows.append({"check": "reconciliation", "severity": "info", "auction_date": r["as_of"],
                             "security": f"{r['security']} {r['key']}",
                             "detail": detail + (f": {r['reasons']}" if r["reasons"] else ""),
                             "source_file": "", "source_row": ""})
    order = {"error": 0, "warning": 1, "info": 2}
    out = pd.DataFrame(rows, columns=list(REPORT_COLUMNS))
    out["_o"] = out["severity"].map(order).fillna(3)
    out = out.sort_values(["_o", "check", "auction_date", "security"], kind="stable").drop(columns="_o")
    return out.reset_index(drop=True)


def _auctions(args: argparse.Namespace) -> int:
    out = Path(args.out_dir)
    calendar = mumbai_settlement_calendar()
    gsec = load_fimmda_gsec(args.fimmda_gsec) if args.fimmda_gsec else None
    sdl = load_fimmda_sdl(args.fimmda_sdl) if args.fimmda_sdl else None
    handbook = load_handbook_auctions(args.handbook) if args.handbook else None
    releases = read_release_tables(args.releases) if args.releases else {}
    master = read_master(args.master) if args.master else None
    snapshots = read_snapshots(args.snapshots) if args.snapshots else None
    operations_from = _date_arg(args.operations_from, "--operations-from")
    build = build_auctions(fimmda_gsec=gsec, fimmda_sdl=sdl, handbook=handbook, releases=releases, master=master,
                           calendar=calendar, infer_maturities=not args.no_infer_maturities)
    switches = normalise_switches(releases, build.master, calendar)
    ledger = build_ledger(build.auctions, switches, snapshots=snapshots, master=build.master, calendar=calendar,
                          shortfall=args.shortfall, competitive_only=args.competitive_only,
                          operations_from=operations_from)
    evidence = settlement_evidence(build, calendar, fimmda_sdl=sdl)
    reconciliation = reconcile(ledger, snapshots) if snapshots is not None and len(snapshots) else None
    _write(build.auctions, out / "auctions.csv")
    _write(switches, out / "switches.csv")
    _write(ledger, out / "ledger.csv")
    _write(coverage(ledger), coverage_path(out / "ledger.csv"))
    _write(supply_summary(ledger, freq="M"), out / "supply_monthly.csv")
    _write(evidence, out / "settlement_evidence.csv")
    if reconciliation is not None:
        _write(reconciliation, out / "reconciliation.csv")
    report = _validation(build, evidence, reconciliation)
    _write(report, out / "validation.csv")
    a = build.auctions
    priced = a[a["repricing"] != "not_checked"]
    print(f"{len(a)} auction rows: ISIN {(a['isin'].notna()).sum()}, repricing "
          + ", ".join(f"{k} {v}" for k, v in priced["repricing"].value_counts().items()))
    counts = report["severity"].value_counts()
    print(f"validation: {counts.get('error', 0)} errors, {counts.get('warning', 0)} warnings, "
          f"{counts.get('info', 0)} notes")
    return 0


def _outstanding(args: argparse.Namespace) -> int:
    ledger = read_ledger(args.ledger)
    snapshot = read_snapshots(args.snapshot) if args.snapshot else None
    try:
        day = _date_arg(args.date, "--date")
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    result = outstanding_on(ledger, day, snapshot, include_zero=args.include_zero)
    out = Path(args.out)
    _write(result, out)
    if args.by_bucket:
        _write(outstanding_by_bucket(result), out.with_name(out.stem + "_by_bucket.csv"))
    complete = result["issuance_complete"] & result["operations_complete"]
    print(f"{day}: {result['outstanding_cr'].sum():,.3f} crore in {len(result)} securities; "
          f"{result.loc[complete, 'outstanding_cr'].sum():,.3f} crore with a complete history")
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


def _date_arg(value: str | None, flag: str):
    """``value`` as a date, with errors naming the command-line flag it came from."""
    if value is None:
        return None
    try:
        return as_date(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{flag}: {exc}") from None


def _bond_price(args: argparse.Namespace) -> int:
    conv = CONVENTIONS[args.conventions]
    if args.final_period:
        conv = replace(conv, final_period=args.final_period)
    try:
        maturity = _date_arg(args.maturity, "--maturity")
        settlement = _date_arg(args.settlement, "--settlement")
        issue_date = _date_arg(args.issue_date, "--issue-date")
        first_coupon_date = _date_arg(args.first_coupon_date, "--first-coupon-date")
        if args.conventions == "strips":
            if args.coupon != 0:
                raise ValueError(f"STRIPS have no coupon; pass --coupon 0, not {args.coupon:g}")
            if first_coupon_date is not None:
                raise ValueError("--first-coupon-date does not apply to a STRIP")
            bond = ZeroCouponBond(maturity, conv, issue_date=issue_date)
        else:
            bond = FixedRateBond(args.coupon, maturity, conv, issue_date=issue_date,
                                 first_coupon_date=first_coupon_date)
        a = bond.analytics(settlement, yield_pct=args.yield_pct, clean_price=args.clean_price)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    rows = [
        ("bond", (f"{args.coupon:g}% maturing {bond.maturity}, {conv.name} conventions "
                  f"({conv.day_count}, {conv.compounding}, last period {conv.final_period})")),
        ("settlement", a.settlement),
        ("previous coupon", a.previous_coupon_date),
        ("next coupon", a.next_coupon_date),
        ("clean price", f"{a.clean_price:.6f}"),
        ("accrued interest", f"{a.accrued_interest:.6f}"),
        ("dirty price", f"{a.dirty_price:.6f}"),
        ("yield (%)", f"{a.yield_pct:.6f}"),
        ("macaulay duration", f"{a.macaulay_duration:.4f}"),
        ("modified duration", f"{a.modified_duration:.4f}"),
        ("dv01 (per 100)", f"{a.dv01:.6f}"),
        ("convexity", f"{a.convexity:.4f}"),
    ]
    for label, value in rows:
        print(f"{label:<19}{value}")
    if args.cash_flows:
        print(f"\n{'date':<12}{'kind':<12}{'amount':>12}{'periods':>12}")
        for f in bond.cash_flows(a.settlement):
            print(f"{f.date.isoformat():<12}{f.kind:<12}{f.amount:>12.6f}{f.periods:>12.6f}")
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

    p = sub.add_parser("bond-price", help="price, yield, accrued interest and risk of a G-sec, SDL or STRIP")
    p.add_argument("--coupon", type=float, required=True,
                   help="coupon, percent a year (0 with --conventions strips)")
    p.add_argument("--maturity", required=True, help="maturity date, YYYY-MM-DD")
    p.add_argument("--settlement", required=True, help="settlement date, YYYY-MM-DD")
    given = p.add_mutually_exclusive_group(required=True)
    given.add_argument("--yield", dest="yield_pct", type=float, help="yield, percent (semi-annual for G-secs)")
    given.add_argument("--price", dest="clean_price", type=float, help="clean price per 100 face")
    p.add_argument("--conventions", choices=["gsec", "sdl", "strips"], default="gsec")
    p.add_argument("--final-period", choices=FINAL_PERIOD_RULES,
                   help="interest in the last coupon period (default: the conventions')")
    p.add_argument("--issue-date", help="issue date, for a bond still in its first coupon period")
    p.add_argument("--first-coupon-date", help="first coupon date, for an irregular first period")
    p.add_argument("--cash-flows", action="store_true", help="also list the remaining cash flows")
    p.set_defaults(func=_bond_price)

    p = sub.add_parser("rbi-releases", help="auctions, liquidity operations, money-market rates and calendars from "
                                            "saved RBI pages and RSS feeds")
    p.add_argument("files", nargs="+", help="saved BS_PressReleaseDisplay pages and pressreleases_rss.xml copies")
    p.add_argument("--out-dir", required=True,
                   help="one CSV per release kind plus releases.csv; re-running replaces a release's rows")
    p.set_defaults(func=_rbi_releases)

    p = sub.add_parser("sovereign-master", help="one row per G-sec ISIN from saved RBI security lists")
    p.add_argument("--goi-list", nargs="*", default=[], help="saved 'GoI securities outstanding' pages (Id=1956)")
    p.add_argument("--far", help="saved FAR specified-securities page (Id=3876)")
    p.add_argument("--snapshots", help="CSV of dated GoI list snapshots: the pages' rows are added (one per date)")
    p.add_argument("--extra", nargs="*", help="CSVs with isin, name [, issue_date, maturity_date, source]")
    p.add_argument("--as-of", help="date to judge maturity against (default: the latest list's date)")
    p.add_argument("--out", required=True)
    p.set_defaults(func=_sovereign_master)

    p = sub.add_parser("auctions", help="auction table, issuance ledger and validation report (G-secs, SDLs, T-bills)")
    p.add_argument("--fimmda-gsec", nargs="*", default=[], help="FIMMDA yearly G-sec auction workbooks (.xlsx)")
    p.add_argument("--fimmda-sdl", nargs="*", default=[], help="FIMMDA SDL auction workbooks (.xlsx)")
    p.add_argument("--handbook", nargs="*", default=[], help="saved RBI Handbook Table 221 pages (.html)")
    p.add_argument("--releases", help="directory of CSVs written by rbi-releases")
    p.add_argument("--master", help="security master CSV written by sovereign-master")
    p.add_argument("--snapshots", help="GoI-outstanding snapshots (CSV from sovereign-master, or a saved list page)")
    p.add_argument("--shortfall", choices=["devolved", "accepted"], default="devolved",
                   help="what an auction that accepted far less than notified adds to the ledger")
    p.add_argument("--competitive-only", choices=["notified", "accepted"], default="notified",
                   help="what a GoI auction known only by its competitive amount adds (notified: 95-100%% of it "
                        "is taken as fully allotted)")
    p.add_argument("--operations-from", help="date from which every switch and buyback release is in --releases")
    p.add_argument("--no-infer-maturities", action="store_true",
                   help="do not infer maturity dates of securities missing from the master")
    p.add_argument("--out-dir", required=True)
    p.set_defaults(func=_auctions)

    p = sub.add_parser("outstanding", help="outstanding stock of every security on a date, from the ledger")
    p.add_argument("--ledger", required=True, help="ledger.csv written by the auctions command")
    p.add_argument("--date", required=True, help="date, YYYY-MM-DD (stock at the end of that day)")
    p.add_argument("--snapshot", help="RBI GoI-outstanding snapshot(s) to reconstruct from (CSV or saved page)")
    p.add_argument("--include-zero", action="store_true", help="also list securities with no stock that day")
    p.add_argument("--by-bucket", action="store_true", help="also write totals by kind and tenor bucket")
    p.add_argument("--out", required=True)
    p.set_defaults(func=_outstanding)

    p = sub.add_parser("parse-ratings", help="split and parse rating strings in a CSV column, for auditing a source")
    p.add_argument("input")
    p.add_argument("--column", default="rating")
    p.add_argument("--keep", help="comma-separated columns to carry through")
    p.add_argument("--out", required=True)
    p.set_defaults(func=_parse_ratings)

    args = parser.parse_args(argv)
    return args.func(args)
