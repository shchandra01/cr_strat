"""Command-line entry points for building the research panels (``cr-strat --help``)."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pandas as pd

from cr_strat import event_ledger, macro_store, rbi_store
from cr_strat import returns as return_engine
from cr_strat.amfi_debt_trades import build_mf_trades, mapping_report, to_price_trades
from cr_strat.asset_swaps import asset_swap_table, fill_from_master
from cr_strat.auctions import (
    REPORT_COLUMNS,
    build_auctions,
    holiday_discrepancies,
    load_fimmda_gsec,
    load_fimmda_sdl,
    load_handbook_auctions,
    normalise_switches,
    read_auctions,
    read_release_tables,
    read_switches,
    settlement_evidence,
)
from cr_strat.bonds import FixedRateBond, ZeroCouponBond
from cr_strat.calendars import mumbai_settlement_calendar
from cr_strat.conventions import (
    CONVENTIONS,
    CORPORATE_CONVENTIONS,
    FINAL_PERIOD_RULES,
    FLOW_DATE_RULES,
    LEAP_YEAR_RULES,
    PERIOD_END_RULES,
    as_date,
    ncd_conventions,
)
from cr_strat.corporates import (
    BOND_FREQUENCIES,
    FIRST_PERIODS,
    SCHEDULES,
    TIERS,
    CallPut,
    CorporateBond,
    corporate_from_terms,
    default_calendar,
    perpetual_bond,
    price_corporates,
    read_amortisation,
    read_corporate_options,
    read_corporate_schedule,
    read_corporate_terms,
)
from cr_strat.costs import read_cost_table
from cr_strat.cra_disclosures import disclosure_summary, load_cra_disclosures, unmapped_issuers
from cr_strat.curvefit import (
    CONTROL_DRIVERS,
    LOSSES,
    METHODS,
    MODEL_RISK_BP,
    OBJECTIVES,
    RESIDUAL_CHOICES,
    WEIGHT_SCHEMES,
    ZSCORE_MIN_OBS,
    ZSCORE_WINDOW,
    FitSettings,
    append_history,
    benchmark_frame,
    bond_butterflies,
    bond_pairs,
    compare_curves,
    control_residuals,
    curve_points,
    curve_spreads,
    fit_curve,
    model_risk_flags,
    prepare_prices,
    refitter,
    residual_drivers,
    rich_cheap_table,
)
from cr_strat.curves import BenchmarkCurve, SpreadBenchmark
from cr_strat.event_prices import DEFAULT_ANCHORS, event_price_paths
from cr_strat.fbil import GSEC_KINDS, MM_KINDS, combine_curves, load_fbil_gsec_curve, load_fbil_money_market
from cr_strat.floating import (
    FRB_COUPONS_CSV,
    FRB_TERMS_CSV,
    discount_margin_bp,
    goi_frb_bond,
    goi_frb_rates,
    index_dv01,
    load_frb_terms,
    price_from_dm,
    spread_duration,
    spread_dv01,
    tbill_way_table,
)
from cr_strat.flows import DEFAULT_WINDOWS, event_flows, parse_windows
from cr_strat.funding import (
    FUNDING_SOURCES,
    REPO_COMPOUNDING,
    carry_table,
    fbil_published_at,
    funding_rates,
    read_haircuts,
)
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
from cr_strat.ois import (
    MIBOR_OIS,
    SORR_OIS,
    add_months,
    as_time,
    bootstrap_ois,
    curve_table,
    fixing_map,
    ois_quotes,
    tenor_months,
)
from cr_strat.ownership import event_ownership, load_events, load_securities
from cr_strat.policy_path import (
    DEFAULT_LAM,
    DEFAULT_MPC_CSV,
    EFFECTIVE_RULES,
    implied_policy_path,
    load_mpc_calendar,
    mibor_repo_spread,
    policy_rate_on,
    read_policy_table,
)
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


def _clock() -> dt.datetime:
    """The wall clock, read once per command: library code takes the time as an argument."""
    return dt.datetime.now(dt.timezone.utc)


def _store_failed(command: str, exc: Exception) -> int:
    """One line on stderr, the cron-friendly way, and the store's exit code for errors."""
    message = " ".join(str(exc).split()) or type(exc).__name__
    if not isinstance(exc, (rbi_store.StoreError, ValueError, OSError)):
        message = f"{type(exc).__name__}: {message}"
    print(f"{command}: error: {message}", file=sys.stderr)
    return rbi_store.EXIT_ERROR


def _rbi_store_poll(args: argparse.Namespace) -> int:
    try:
        result = rbi_store.poll(args.store, now=_clock(), url=args.url,
                                min_interval=dt.timedelta(minutes=args.min_interval), user_agent=args.user_agent,
                                timeout=args.timeout, keep_raw=not args.no_raw, resume=args.resume)
    except Exception as exc:  # cron wants one line and an exit code, not a traceback
        return _store_failed("rbi-store-poll", exc)
    stream = sys.stdout if result.exit_code == rbi_store.EXIT_OK else sys.stderr
    # every exit 1 says "error:", as _store_failed does: the daily grep of the log (data/README.md) relies on it
    tag = "error: " if result.exit_code == rbi_store.EXIT_ERROR else ""
    print(f"rbi-store-poll: {tag}{result.message}", file=stream)
    for line in result.attention:  # on stderr, so that a cron job that mails its errors mails these too
        print(f"rbi-store-poll: needs a look: {line}", file=stream)
    return result.exit_code


def _rbi_store_ingest(args: argparse.Namespace) -> int:
    try:
        report = rbi_store.ingest(args.store, args.files, now=_clock(), keep_raw=not args.no_raw, master=args.master,
                                  policy_rates=args.policy_rates)
    except Exception as exc:
        return _store_failed("rbi-store-ingest", exc)
    print(f"rbi-store-ingest: {report.summary()}")
    for name, reason in report.unreadable:
        print(f"unreadable {name}: {reason}")
    for release_id, kind, reason in report.failed:
        print(f"failed {release_id} ({kind}): {reason}")
    if report.derive_error:
        print(f"rbi-store-ingest: error: derived tables not rebuilt: {report.derive_error} (the previous ones are "
              "kept; the parsed rows and raw copies were saved)", file=sys.stderr)
        return rbi_store.EXIT_ERROR
    return rbi_store.EXIT_OK


def _rbi_store_status(args: argparse.Namespace) -> int:
    try:
        report = rbi_store.status(args.store, now=_clock())
    except Exception as exc:
        return _store_failed("rbi-store-status", exc)
    for line in report.lines():
        print(line)
    return rbi_store.EXIT_OK


def _macro_failed(command: str, exc: Exception) -> int:
    message = " ".join(str(exc).split()) or type(exc).__name__
    if not isinstance(exc, (macro_store.StoreError, ValueError, OSError)):
        message = f"{type(exc).__name__}: {message}"
    print(f"{command}: error: {message}", file=sys.stderr)
    return macro_store.EXIT_ERROR


def _macro_settings(args: argparse.Namespace) -> macro_store.Settings:
    return macro_store.Settings(cpi_levels=args.cpi_levels, states=args.states, keep_raw=not args.no_raw,
                                mospi_raw=args.mospi_raw, backfill=not getattr(args, "no_backfill", False),
                                mospi_history=getattr(args, "mospi_history", "derived"))


def _macro_store_update(args: argparse.Namespace) -> int:
    now = _clock()
    stamp = now.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # on every line: the log is appended to
    try:
        report = macro_store.update(args.store, now=now, sources=args.sources, settings=_macro_settings(args),
                                    max_requests=args.max_requests, max_per_host=args.max_per_host,
                                    min_interval=dt.timedelta(minutes=args.min_interval), user_agent=args.user_agent,
                                    timeout=args.timeout, resume=args.resume, retry_failed=args.retry_failed,
                                    release_dates=args.release_dates, index_events=args.index_events,
                                    legacy_tls=args.legacy_tls)
    except Exception as exc:  # cron wants one line and an exit code, not a traceback
        return _macro_failed(f"macro-store-update: {stamp}", exc)
    for problem, line in report.lines():  # problems on stderr, so that a cron job that mails its errors mails these
        print(f"macro-store-update: {stamp} {line}", file=sys.stderr if problem else sys.stdout)
    return report.exit_code


def _macro_store_ingest(args: argparse.Namespace) -> int:
    try:
        report = macro_store.ingest(args.store, args.files, now=_clock(), settings=_macro_settings(args),
                                    release_dates=args.release_dates, index_events=args.index_events,
                                    cpi_series=args.cpi_series)
    except Exception as exc:
        return _macro_failed("macro-store-ingest", exc)
    print(f"macro-store-ingest: {report.summary()}")
    for name, reason in report.refused:
        print(f"refused {name}: {reason}")
    for name, reason in report.unreadable:
        print(f"not recognised {name}: {reason}")
    for name, reason in report.other:
        print(f"not data {name}: {reason}")
    for conflict in report.conflicts:
        print(f"two inputs disagreed: {conflict}")
    if report.derive_error:
        print(f"macro-store-ingest: error: derived tables not rebuilt: {report.derive_error} (the previous ones are "
              "kept; the values read were saved)", file=sys.stderr)
        return macro_store.EXIT_ERROR
    return macro_store.EXIT_OK


def _macro_store_status(args: argparse.Namespace) -> int:
    try:
        report = macro_store.status(args.store, now=_clock())
        lines = report.lines(args.series)
        if args.policy_rates:
            lines += macro_store.policy_rate_check(args.store, args.policy_rates)
    except Exception as exc:
        return _macro_failed("macro-store-status", exc)
    for line in lines:
        print(line)
    return macro_store.EXIT_OK


def _sources_arg(text: str) -> tuple[str, ...]:
    try:
        return macro_store.resolve_sources([t for t in text.split(",") if t.strip()])
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _count_arg(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {text!r}") from None
    if not 1 <= value <= macro_store.ms.MAX_REQUESTS:
        raise argparse.ArgumentTypeError(f"from 1 to {macro_store.ms.MAX_REQUESTS}, not {text}")
    return value


def _interval_arg(text: str) -> float:
    """``--min-interval`` of macro-store-update: minutes from 0 to a week."""
    most = macro_store.MAX_MIN_INTERVAL.total_seconds() / 60
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number of minutes: {text!r}") from None
    if not math.isfinite(value) or not 0 <= value <= most:
        raise argparse.ArgumentTypeError(f"from 0 to {most:g} minutes (a week), and finite, not {text}")
    return value


def _generic_agent_arg(text: str) -> str:
    try:
        return macro_store.ms.check_generic_user_agent(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _pattern_arg(text: str) -> str:
    try:
        re.compile(text)
    except re.error as exc:
        raise argparse.ArgumentTypeError(f"not a regular expression: {exc}") from None
    return text


def _existing_file_arg(text: str) -> str:
    if not Path(text).is_file():
        raise argparse.ArgumentTypeError(f"no such file: {text}")
    return text


def _minutes_arg(text: str) -> float:
    """``--min-interval``: every value the poll would refuse, or overflow on, is a usage error here (exit 2)."""
    most = rbi_store.MAX_MIN_INTERVAL.total_seconds() / 60
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number of minutes: {text!r}") from None
    if not math.isfinite(value) or not 1 <= value <= most:
        raise argparse.ArgumentTypeError(f"at least 1 minute and at most {most:g} (a day), and finite, not {text}")
    return value


def _seconds_arg(text: str) -> float:
    """``--timeout``: positive, finite and at most ``rbi_store.MAX_TIMEOUT``; checked before anything is recorded."""
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number of seconds: {text!r}") from None
    if not math.isfinite(value) or not 0 < value <= rbi_store.MAX_TIMEOUT:
        raise argparse.ArgumentTypeError(f"a positive, finite number of seconds, at most {rbi_store.MAX_TIMEOUT:g}, "
                                         f"not {text}")
    return value


def _user_agent_arg(text: str) -> str:
    try:
        return rbi_store.check_user_agent(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _feed_url_arg(text: str) -> str:
    try:
        return rbi_store.check_url(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


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


def _clock_arg(value: str, flag: str) -> dt.time:
    """``HH:MM`` as a time of day, with errors naming the flag."""
    try:
        return dt.datetime.strptime(value.strip(), "%H:%M").time()
    except (ValueError, AttributeError):
        raise ValueError(f"{flag}: {value!r} is not a time of day like 20:00") from None


def _release_index_csv(directory: str) -> pd.DataFrame | None:
    """The release index ``cr-strat rbi-releases`` writes beside its tables (``releases.csv``), typed; or None."""
    path = Path(directory) / "releases.csv"
    if not path.exists():
        return None
    frame = pd.read_csv(path, dtype=str, keep_default_na=False).replace({"": None})
    frame["release_id"] = pd.to_numeric(frame["release_id"]).astype("Int64")
    for col in ("release_date", "published"):
        if col in frame:
            frame[col] = pd.to_datetime(frame[col])
    return frame


def _event_ledger(args: argparse.Namespace) -> int:
    """The S4 event ledger: events with their public times, supply by period, checks, metrics, windows."""
    try:
        for flag in ("auctions", "ledger", "store", "releases", "master", "switches", "snapshot", "mpc",
                     "policy_rates", "macro_releases", "index_events", "macro_store", "panel", "residuals",
                     "calendar_lines"):
            value = getattr(args, flag)
            if value and not Path(value).exists():
                raise FileNotFoundError(f"--{flag.replace('_', '-')}: {value} not found")
        for page in args.handbook:
            if not Path(page).exists():
                raise FileNotFoundError(f"--handbook: {page} not found")
        if bool(args.auctions) != bool(args.ledger):
            raise ValueError("--auctions and --ledger go together: the ledger built from that auction table")
        settings = event_ledger.EventSettings(
            announcement_rule=args.announcement_rule, result_rule=args.result_rule,
            decision_time=_clock_arg(args.decision_time, "--decision-time"), period=args.period,
            bid_cover=args.bid_cover, stock_dv01_basis=args.stock_dv01_basis)
        start, end = _date_arg(args.start, "--start"), _date_arg(args.end, "--end")
        macro_from, macro_to = _date_arg(args.macro_from, "--macro-from"), _date_arg(args.macro_to, "--macro-to")
        calendar = mumbai_settlement_calendar()
        auctions = read_auctions(args.auctions) if args.auctions else None
        ledger = read_ledger(args.ledger) if args.ledger else None
        sources = [event_ledger.read_store_tables(args.store)] if args.store else []
        if args.releases:  # joined to the store's tables, the store's copy of a release both hold kept
            extra = read_release_tables(args.releases)
            index = _release_index_csv(args.releases)
            if index is not None:
                extra["releases"] = index
            sources.append(extra)
        tables = event_ledger.merge_tables(*sources)
        handbook = load_handbook_auctions(args.handbook) if args.handbook else None
        master = read_master(args.master) if args.master else None
        switches = read_switches(args.switches) if args.switches else None
        snapshot = read_snapshots(args.snapshot) if args.snapshot else None
        meetings = None if args.no_mpc else load_mpc_calendar(args.mpc or DEFAULT_MPC_CSV)
        if meetings is not None and args.mpc:  # the file's own decision times, which load_mpc_calendar leaves out
            meetings = event_ledger.attach_decision_times(meetings, args.mpc)
        policy = _read_csv_text(args.policy_rates) if args.policy_rates else None
        index_events = None
        if args.index_events:
            index_events = args.index_events
        elif args.macro_store:
            index_events = macro_store.read_table(args.macro_store, "index_events")
        macro_range = (macro_from, macro_to) if macro_from or macro_to else None
        calendar_lines = event_ledger.read_calendar_lines(args.calendar_lines) if args.calendar_lines else None
        every = event_ledger.collect_events(
            auctions=auctions, ledger=ledger, tables=tables, handbook=handbook, master=master, switches=switches,
            meetings=meetings, policy=policy, macro_releases=args.macro_releases, macro_range=macro_range,
            macro=args.macro or macro_range is not None, index_events=index_events, calendar_lines=calendar_lines,
            calendar=calendar, settings=settings)
        # --start and --end choose the events written; the checks, the yields behind the stock's DV01, the trailing
        # DV01 of forward supply and the overlaps read every event of the inputs, so the range's edges see as much.
        in_range = pd.Series(True, index=every.index)
        if start is not None:
            in_range &= every["event_date"] >= pd.Timestamp(start)
        if end is not None:
            in_range &= every["event_date"] <= pd.Timestamp(end)
        events = every[in_range].reset_index(drop=True)
        if not len(events):
            print("no events in the inputs given" if not len(every) else
                  f"no events dated {args.start or 'any time'} to {args.end or 'any time'} ({len(every)} outside)")
            return 1
        supply = event_ledger.supply_table(every, ledger=ledger, snapshot=snapshot, settings=settings, start=start,
                                           end=end)
        metrics = event_ledger.auction_metrics(
            events, panel=_read_csv_text(args.panel) if args.panel and "yield_pct" in args.panel_columns.split(",")
            else None, residuals=_read_csv_text(args.residuals) if args.residuals else None, calendar=calendar,
            settings=settings)
        if args.forward_dates:
            dates = [_date_arg(d, "--forward-dates") for d in args.forward_dates.split(",") if d.strip()]
        else:  # each Friday from the first calendar's release to the last planned week, within --start and --end
            lines = every[every["kind"].isin(event_ledger.CALENDAR_KINDS)]
            dates = list(pd.date_range(lines["announcement_public_from"].min().normalize(),
                                       lines["period_end"].max(), freq="W-FRI").date) if len(lines) else []
            dates = [d for d in dates if (start is None or d >= start) and (end is None or d <= end)]
        forward = event_ledger.forward_supply(every, dates, horizon_weeks=args.horizon_weeks,
                                              coverage_table=coverage(ledger) if ledger is not None else None,
                                              settings=settings)
        windows = event_ledger.event_windows(every, calendar=calendar, pre=args.pre, post=args.post,
                                             anchor=args.anchor, project_calendar=args.project_calendar,
                                             settings=settings)
        overlap = event_ledger.overlaps(windows)
        exclude = tuple(k for k in args.exclude_kinds.split(",") if k)
        unknown = sorted(set(exclude) - set(event_ledger.EVENT_KINDS))
        if unknown:
            raise ValueError(f"--exclude-kinds: {unknown} are not event kinds ({', '.join(event_ledger.EVENT_KINDS)})")
        radius = min(3, args.pre, args.post) if args.radius is None else args.radius
        isolated = event_ledger.isolate(windows, overlap, exclude_kinds=exclude, radius=radius)
        ids = set(events["event_id"])
        skipped = {k: sum(1 for e in v if e in ids) for k, v in windows.attrs["skipped_ids"].items()}
        windows, overlap, isolated = (f[f["event_id"].isin(ids)].reset_index(drop=True)
                                      for f in (windows, overlap, isolated))
        paths = None
        if args.panel:
            paths = event_ledger.attach_panel(
                windows, _read_csv_text(args.panel).replace({"": None}), columns=args.panel_columns.split(","),
                key=args.panel_key or None, window_key=args.window_key, tolerance=args.tolerance,
                panel_dates=args.panel_dates, calendar=calendar, project_calendar=args.project_calendar)
        gaps = event_ledger.input_gaps(events, ledger=ledger, tables=tables, meetings=meetings,
                                       index_events_given=index_events is not None)
    except (ValueError, TypeError, OSError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    out = Path(args.out_dir)
    times = (*event_ledger.TIME_FIELDS, "as_of", "planned_public_from", "announced_public_from",
             "realised_public_from", "anchor_public_from")
    files = {"events.csv": events, f"supply_{'weekly' if args.period.endswith('week') else 'monthly'}.csv": supply,
             "auction_metrics.csv": metrics, "forward_supply.csv": forward, "windows.csv": windows,
             "overlaps.csv": overlap, "isolated.csv": isolated, "gaps.csv": gaps}
    if ledger is not None:  # identities of the whole inputs, whatever --start and --end
        files["supply_monthly_check.csv"] = event_ledger.check_supply(every, ledger)
        files["ledger_join_check.csv"] = event_ledger.check_ledger_join(every, ledger)
    if paths is not None:
        files["event_paths.csv"] = paths
    for name, frame in files.items():
        path = (event_ledger.write_events(frame, out / name) if name == "events.csv"
                else event_ledger.write_table(frame, out / name, times=times))
        print(f"wrote {len(frame):>7} rows  {path}")
    counts = events["kind"].value_counts()
    print("events: " + ", ".join(f"{k} {counts[k]}" for k in event_ledger.EVENT_KINDS if k in counts))
    if ledger is not None:
        e1, e2 = files["supply_monthly_check.csv"], files["ledger_join_check.csv"]
        print(f"supply check (E1, all inputs): {int((e1['status'] != 'ok').sum())} of {len(e1)} cells differ; "
              f"ledger join (E2): {int((e2['status'] != 'ok').sum())} of {len(e2)} auction ids not one to one")
    print(f"windows: {windows['event_id'].nunique() if len(windows) else 0} events; skipped "
          f"{skipped.get('no_anchor', 0)} without the anchor's time, {skipped.get('beyond_calendar', 0)} beyond the "
          "calendar's years (--project-calendar to project them)")
    if paths is not None:
        matched = paths["panel_match"].value_counts()
        print("panel: " + ", ".join(f"{k} {matched[k]}" for k in ("exact", "nearest", "missing", "no_key")
                                    if k in matched))
    notes = out / "notes.txt"
    notes.write_text("".join(f"{r.source}: {r.detail}\n" for r in gaps.itertuples()), encoding="utf-8")
    print(f"wrote {len(gaps):>7} lines {notes} (what the inputs lack)")
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


def _option_arg(text: str, kind: str) -> CallPut:
    """``--call``/``--put`` as ``DATE`` or ``DATE:PRICE`` (price per 100 of the outstanding face, default 100)."""
    when, _, price = text.partition(":")
    try:
        return CallPut(kind, _date_arg(when, f"--{kind}"), float(price) if price else 100.0)
    except ValueError as exc:
        raise ValueError(f"--{kind} {text}: {exc}") from None


def _corporate_bond(args: argparse.Namespace) -> CorporateBond:
    """The bond ``corp-price`` prices: a terms-table row (``--terms --isin``) or the terms given as flags."""
    calendar = None if args.calendar == "none" else "default"
    extra = tuple(_option_arg(t, "call") for t in args.call) + tuple(_option_arg(t, "put") for t in args.put)
    if args.terms:
        if not args.isin:
            raise ValueError("--terms needs --isin")
        terms = read_corporate_terms(args.terms)
        rows = terms[terms["isin"] == args.isin]
        if rows.empty:
            raise ValueError(f"--isin {args.isin} is not in {args.terms}")
        bond = corporate_from_terms(
            rows.iloc[0].to_dict(), calendar=calendar,
            schedule=read_corporate_schedule(args.schedule_file) if args.schedule_file else None,
            options=read_corporate_options(args.options) if args.options else None,
            amortisation=read_amortisation(args.amortisation) if args.amortisation else None)
        return replace(bond, options=bond.options + extra) if extra else bond
    missing = [flag for flag, value in (("--coupon", args.coupon), ("--issue-date", args.issue_date))
               if value is None]
    if args.maturity is None and not args.perpetual:
        missing.append("--maturity")
    if missing:
        raise ValueError(f"give --terms and --isin, or the terms: {', '.join(missing)} missing")
    issue = _date_arg(args.issue_date, "--issue-date")
    conventions = CORPORATE_CONVENTIONS[args.conventions] if args.conventions else ncd_conventions(issue)
    kwargs = {name: getattr(args, name) for name in ("record_days", "record_roll", "leap_year_rule",
                                                     "postponed_coupon", "redemption_interest", "flow_dates")
              if getattr(args, name) is not None}
    if args.tier is not None:
        kwargs["tier"] = args.tier
    kwargs.update(conventions=conventions, frequency=args.frequency, redemption=args.redemption,
                  first_period=args.first_period, isin=args.isin or "",
                  calendar=None if calendar is None else default_calendar(conventions))
    if args.perpetual:
        bond = perpetual_bond(issue, args.coupon, [o for o in extra if o.kind == "call"], **kwargs)
        puts = tuple(o for o in extra if o.kind == "put")
        return replace(bond, options=bond.options + puts) if puts else bond
    return CorporateBond(args.coupon, _date_arg(args.maturity, "--maturity"), issue, schedule=args.schedule,
                         first_coupon_date=_date_arg(args.first_coupon_date, "--first-coupon-date"), options=extra,
                         **kwargs)


def _corp_price(args: argparse.Namespace) -> int:
    """Price one corporate bond (NCD) from its terms, or every row of a prices file."""
    try:
        if args.prices:
            if not (args.terms and args.out):
                raise ValueError("--prices needs --terms and --out")
            frame = _read_csv_text(args.prices)
            given = "yield_pct" if args.valuation == "sebi" or ("yield_pct" in frame and "clean_price" not in frame) \
                else "clean_price"
            out = price_corporates(frame, read_corporate_terms(args.terms),
                                   schedule=read_corporate_schedule(args.schedule_file) if args.schedule_file
                                   else None,
                                   options=read_corporate_options(args.options) if args.options else None,
                                   amortisation=read_amortisation(args.amortisation) if args.amortisation else None,
                                   given=given, valuation=args.valuation,
                                   calendar=None if args.calendar == "none" else "default")
            _write(out, Path(args.out))
            return 0
        if (args.yield_pct is None) == (args.clean_price is None):
            raise ValueError("give exactly one of --yield and --price (or --prices with --out)")
        if not args.settlement:
            raise ValueError("--settlement is required")
        s = _date_arg(args.settlement, "--settlement")
        bond = _corporate_bond(args)
        target, label, note = bond, "maturity", ""
        if args.valuation == "worst":
            if args.clean_price is not None:
                w = bond.yield_to_worst(s, args.clean_price)
                target, label = bond.to_workout(w.date, w.price), f"{w.kind} {w.date}"
            else:
                _, when, kind = bond.price_to_worst(s, args.yield_pct)
                price = {(k, d): p for k, d, p in bond.workouts(s)}[(kind, when)]
                target, label = bond.to_workout(when, price), f"{kind} {when}"
        elif args.valuation == "sebi":
            if args.yield_pct is None:
                raise ValueError("--valuation sebi works from --yield")
            v = bond.sebi_valuation(s, args.yield_pct)
            price = {(k, d): p for k, d, p in bond.workouts(s)}
            key = {"first_call": "call", "deemed_maturity": "put"}.get(v.kind, v.kind)
            target = bond.to_workout(v.date, price.get((key, v.date)) if v.kind != "maturity" else None)
            label = f"{v.kind} {v.date}"
            note = (f"call trigger {v.call_trigger or 'none'}, put trigger {v.put_trigger or 'none'}"
                    + (f"; {v.notes}" if v.notes else "") + ("; a put favours the holder (yield > coupon + 30 bp)"
                                                             if v.put_favours_holder else ""))
        a = target.analytics(s, yield_pct=args.yield_pct, clean_price=args.clean_price)
        c = bond.conventions
        record = bond.record_date(bond.next_coupon_date(s))
    except (ValueError, TypeError, NotImplementedError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    kind = "zero-coupon or cumulative" if not bond.frequency else f"{bond.frequency} coupon(s) a year"
    rows = [
        ("bond", (f"{bond.isin + ' ' if bond.isin else ''}{bond.coupon_pct:g}% maturing {bond.maturity}"
                  f"{' (deemed, perpetual)' if bond.perpetual else ''}, allotted {bond.issue_date}, {kind}")),
        ("conventions", (f"{c.name}: {c.day_count} ({bond._yf_rule}), {c.compounding} on actual/365, flows on "
                         f"{'due' if bond._use_due(s) else 'payment'} dates, record date {bond._record_days} days")),
        ("valued to", label + (f" ({note})" if note else "")),
        ("settlement", a.settlement),
        ("previous coupon", a.previous_coupon_date),
        ("next coupon", a.next_coupon_date),
        ("record date", f"{record}{' (passed: ex-interest)' if bond.is_ex_interest(s) else ''}"),
        ("clean price", f"{a.clean_price:.6f}"),
        ("accrued interest", f"{a.accrued_interest:.6f}"),
        ("dirty price", f"{a.dirty_price:.6f}"),
        ("yield (%)", f"{a.yield_pct:.6f}"),
        ("macaulay duration", f"{a.macaulay_duration:.4f}"),
        ("modified duration", f"{a.modified_duration:.4f}"),
        ("dv01 (per 100)", f"{a.dv01:.6f}"),
        ("convexity", f"{a.convexity:.4f}"),
    ]
    for label_, value in rows:
        print(f"{label_:<19}{value}")
    if bond.options:
        heading = "yield (%)" if args.clean_price is not None else "clean"
        print(f"\n{'workout':<10}{'date':<12}{'price':>9}{heading:>14}")
        for kind_, when, price in bond.workouts(s):
            w = bond.to_workout(when, price)
            value = (w.yield_from_price(args.clean_price, s) if args.clean_price is not None
                     else w.clean_price(args.yield_pct, s))
            print(f"{kind_:<10}{when.isoformat():<12}{price:>9.4f}{value:>14.6f}")
    if args.cash_flows:
        print(f"\n{'due':<12}{'paid':<12}{'kind':<12}{'amount':>12}{'periods':>12}")
        for f in target.cash_flows(s):
            print(f"{f.date.isoformat():<12}{f.payment_date.isoformat():<12}{f.kind:<12}{f.amount:>12.6f}"
                  f"{f.periods:>12.6f}")
    return 0


def _frb_price(args: argparse.Namespace) -> int:
    """Price a GoI FRB: its coupons as known at --as-of, the discount margin and the spread risks."""
    try:
        terms = load_frb_terms(args.terms or FRB_TERMS_CSV)
        rows = terms[terms["isin"] == args.isin]
        if rows.empty:
            raise ValueError(f"--isin {args.isin} is not in {args.terms or FRB_TERMS_CSV}; known: "
                             f"{', '.join(terms['isin'])}")
        row = rows.iloc[0].to_dict()
        default_time = _clock_arg(args.tbill_time, "--tbill-time") if args.tbill_time else None
        tbills = None
        if args.tbills:
            tbills = tbill_way_table(_read_csv_text(args.tbills), default_time=default_time)
        elif args.store:
            tbills = tbill_way_table(rbi_store.read_table(args.store, "tbill_result"), default_time=default_time)
        as_of = _time_arg(args.as_of, "--as-of") if args.as_of else None
        announced = None if args.no_coupons else (args.coupons or FRB_COUPONS_CSV)
        rates = goi_frb_rates(row, tbills, announced, as_of=as_of, allow_fewer=args.allow_fewer)
        s = _date_arg(args.settlement, "--settlement")
        bond = goi_frb_bond(row, rates)
        index = args.index if args.index is not None else bond.index_pct
        if index is None:
            raise ValueError("no flat index to measure the discount margin from: pass --index")
        if (args.clean_price is None) == (args.dm is None):
            raise ValueError("give exactly one of --price and --dm")
        if args.dm is not None:
            clean, dirty = price_from_dm(bond, s, args.dm, index_pct=index)
            dm = args.dm
        else:
            clean = args.clean_price
            dm = discount_margin_bp(bond, s, clean, index_pct=index)
            dirty = clean + bond.accrued_interest(s)
        y = bond.yield_from_price(clean, s)
        current = rates[(rates["period_start"] <= s) & (rates["period_end"] > s)].iloc[0]
        ahead = rates[rates["period_start"] > s].head(1)
    except (ValueError, TypeError, NotImplementedError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    def describe(r) -> str:
        when = "" if pd.isna(r["public_from"]) else f", public from {pd.Timestamp(r['public_from']):%Y-%m-%d %H:%M}"
        return f"{r['rate_pct']:.4f}% {r['status']} ({r['period_start']} to {r['period_end']}{when})"

    lines = [
        ("bond", f"{row['isin']} {row['name']}, spread {row['spread_pct'] or 'none'}, index {row['index']}"),
        ("as of", "everything in the inputs" if as_of is None else as_of),
        ("settlement", s),
        ("current coupon", describe(current)),
        ("next coupon", describe(ahead.iloc[0]) if not ahead.empty else "none (last period)"),
        ("index (flat)", f"{index:.4f}%"),
        ("clean price", f"{clean:.6f}"),
        ("accrued interest", f"{bond.accrued_interest(s):.6f}"),
        ("dirty price", f"{dirty:.6f}"),
        ("yield (%)", f"{y:.6f} (semi-annual, the projected coupons)"),
        ("discount margin", f"{dm:.4f} bp over the index"),
        ("spread dv01", f"{spread_dv01(bond, s, dm, index_pct=index):.6f}"),
        ("spread duration", f"{spread_duration(bond, s, dm, index_pct=index):.4f}"),
        ("index dv01", f"{index_dv01(bond, s, dm, index_pct=index):.6f}"),
    ]
    for label, value in lines:
        print(f"{label:<19}{value}")
    notes = [n for n in (current["notes"], *(ahead["notes"].tolist())) if n]
    for n in notes:
        print(f"note: {n}")
    if args.rates:
        print(f"\n{'period start':<14}{'rate (%)':>10}  {'status':<10}{'public from':<18}notes")
        for r in rates[rates["period_end"] > s].to_dict("records"):
            when = "" if pd.isna(r["public_from"]) else f"{pd.Timestamp(r['public_from']):%Y-%m-%d %H:%M}"
            print(f"{r['period_start'].isoformat():<14}{r['rate_pct']:>10.4f}  {r['status']:<10}{when:<18}"
                  f"{r['notes']}")
    if args.out:
        _write(rates, Path(args.out))
    return 0


def _curve_fit(args: argparse.Namespace) -> int:
    """Fit a curve to one settlement date's prices, cross-check it and write the curve and every residual."""
    try:
        settlement = _date_arg(args.settlement, "--settlement")
        if args.control and not (args.master or args.ledger):
            raise ValueError("--control needs the drivers: give --master and/or --ledger")
        prices = pd.read_csv(args.prices, dtype=str, keep_default_na=False)
        master = read_master(args.master) if args.master else None
        frame = prepare_prices(prices, master)
        settings = FitSettings(
            method=args.method, objective=args.objective, loss=args.loss, outlier_bp=args.outlier_bp,
            weights=args.weights, include_kinds=tuple(k for k in args.kinds.split(",") if k.strip()),
            min_volume_cr=args.min_volume_cr, anchor_pct=args.anchor, drop_benchmarks=args.drop_benchmarks,
            leave_one_out=not args.no_leave_one_out)
        result = fit_curve(frame, settlement, settings)
        residuals, check, comparison = result.residuals, None, None
        if args.check not in ("none", args.method):
            # the check is refitted without each bond too, so model risk at a bond is judged on curves it cannot sway;
            # on a day with suspect bonds a flag is judged again on curves fitted without them as well
            check_settings = replace(settings, method=args.check)
            check = fit_curve(frame, settlement, check_settings)
            comparison = compare_curves(result.curve, check.curve, threshold_bp=args.model_risk_bp)
            loo = settings.leave_one_out
            residuals = model_risk_flags(residuals, comparison, check_residuals=check.residuals if loo else None,
                                         refit=refitter(frame, settlement, settings, check_settings) if loo else None)
        column = RESIDUAL_CHOICES[args.residual]
        if args.residual == "loo" and not settings.leave_one_out:
            raise ValueError("--residual loo needs the leave-one-out refits: drop --no-leave-one-out")
        rich = None
        if args.history:
            history = pd.read_csv(args.history, dtype=str, keep_default_na=False).replace({"": None})
            rich = rich_cheap_table(residuals, history, window=args.window, min_obs=args.min_obs, column=column)
        appended = None
        if args.append_history:
            past = Path(args.append_history)
            old = pd.read_csv(past, dtype=str, keep_default_na=False).replace({"": None}) if past.exists() else None
            appended = append_history(old, residuals)
        flies = None
        if args.fly:
            flies = bond_butterflies(residuals, [tuple(text.split(",")) for text in args.fly])
        pairs = None
        if args.pair:
            pairs = bond_pairs(residuals, [tuple(text.split(",")) for text in args.pair])
        nss_table = None
        if args.nss_params:
            if args.method != "nss":
                raise ValueError("--nss-params writes NSS parameters: fit with --method nss (a spline has none)")
            mark_day = _date_arg(args.trade_date, "--trade-date") if args.trade_date else \
                mumbai_settlement_calendar().add_business_days(settlement, -1)
            row = return_engine.nss_params_row(result.curve, mark_day, name=args.curve_name,
                                               public_from=args.public_from)
            nss_table = return_engine.append_nss_params(args.nss_params, row)
        drivers = controlled = None
        if args.master or args.ledger:
            ledger = read_ledger(args.ledger) if args.ledger else None
            stock = outstanding_on(ledger, settlement) if ledger is not None else None
            drivers = residual_drivers(residuals, master, ledger=ledger, stock=stock)
            if args.control:
                names = [c.strip() for c in args.control.split(",") if c.strip()]
                drivers = control_residuals(drivers, names, column=column)
                controlled = drivers.attrs["coefficients"]
    except (ValueError, TypeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    out = Path(args.out_dir)
    _write(curve_points(result.curve), out / "curve.csv")
    _write(residuals, out / "residuals.csv")
    _write(benchmark_frame([result.curve]), out / "benchmark_par.csv")
    _write(curve_spreads(result.curve), out / "curve_spreads.csv")
    if check is not None:
        _write(curve_points(check.curve), out / "curve_check.csv")
        _write(comparison, out / "crosscheck.csv")
    if rich is not None:
        _write(rich, out / "rich_cheap.csv")
    if flies is not None:
        _write(flies, out / "butterflies.csv")
    if pairs is not None:
        _write(pairs, out / "pairs.csv")
    if drivers is not None:
        _write(drivers, out / "drivers.csv")
    if controlled is not None:
        _write(controlled, out / "controls.csv")
    if appended is not None:
        _write(appended, Path(args.append_history))
    if nss_table is not None:
        _write(nss_table, Path(args.nss_params))
        if not args.trade_date:
            print("note: the NSS row's date is the business day before --settlement (T+1); give --trade-date if not")
    diagnostics = {"primary": result.diagnostics, "check": check.diagnostics if check is not None else None}
    path = out / "diagnostics.json"
    path.write_text(json.dumps(diagnostics, indent=2, allow_nan=False) + "\n")
    print(f"wrote diagnostics  {path}")
    d = result.diagnostics
    print(f"{d['method']}: {d['n_fit']} of {d['n_input']} rows in the fit, RMSE {d['rmse_bp']:.2f} bp, largest "
          f"{d['max_abs_bp']:.2f} bp, curve {d['curve_id']}")
    if comparison is not None:
        flagged = comparison[comparison["model_risk"]]
        print(f"cross-check {args.check}: {len(flagged)} of {int(comparison['in_joint_range'].sum())} tenors in the "
              f"joint data range differ by more than {args.model_risk_bp:g} bp in par yield")
    for warning in d["warnings"]:
        print(f"warning: {warning}")
    for note in (residuals.attrs.get("notes", []) if check is not None else []):
        print(f"note: {note}")
    for note in (rich.attrs.get("notes", []) if rich is not None else []):
        print(f"note: {note}")
    for note in (drivers.attrs.get("notes", []) if drivers is not None else []):
        print(f"note: {note}")
    return 0


OIS_CONVENTIONS = {"mibor": MIBOR_OIS, "sorr": SORR_OIS}


def _ois_calendar(args: argparse.Namespace):
    calendar = mumbai_settlement_calendar()
    return calendar.lenient() if args.project_calendar else calendar


def _read_csv_text(path: str) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def _bootstrap(args: argparse.Namespace, trade):
    return bootstrap_ois(_read_csv_text(args.quotes), trade, args.overnight,
                         conventions=OIS_CONVENTIONS[args.conventions], calendar=_ois_calendar(args),
                         max_extrapolation_years=args.max_extrapolation_years, source=Path(args.quotes).name)


def _calendar_warning(projected: bool) -> None:
    if projected:
        print("warning: dates past 2026 are projected on weekends alone (no holiday list yet): calendar_projected")


def _ois_curve(args: argparse.Namespace) -> int:
    """Bootstrap an OIS curve from one day's par quotes; write its nodes and a monthly table."""
    try:
        trade = _date_arg(args.trade_date, "--trade-date")
        curve = _bootstrap(args, trade)
        nodes, table = curve.nodes(), curve_table(curve)
    except (ValueError, TypeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    out = Path(args.out_dir)
    _write(nodes, out / "nodes.csv")
    _write(table, out / "curve.csv")
    print(f"{curve.conventions.name} curve of {trade}: effective {curve.effective}, {len(curve.quotes)} quotes to "
          f"{curve.node_dates[-1]}, largest repricing error {nodes['error_bp'].abs().max():.1e} bp")
    _calendar_warning(curve.calendar_projected)
    return 0


def _asset_swaps(args: argparse.Namespace) -> int:
    """Asset-swap measures of each bond in a file against the OIS curve of one day's quotes."""
    try:
        trade = _date_arg(args.trade_date, "--trade-date")
        settlement = _date_arg(args.settlement, "--settlement")
        curve = _bootstrap(args, trade)
        bonds = _read_csv_text(args.bonds)
        if args.master:
            bonds = fill_from_master(bonds, read_master(args.master))
        table = asset_swap_table(bonds, curve, settlement)
    except (ValueError, TypeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    _write(table, Path(args.out))
    failed = table[table["error"] != ""]
    print(f"{len(table) - len(failed)} of {len(table)} bonds measured against the {curve.conventions.name} curve of "
          f"{trade} (to {curve.node_dates[-1]})")
    for rec in failed.to_dict("records"):
        print(f"refused {rec['isin']}: {rec['error']}")
    _calendar_warning(curve.calendar_projected)
    return 0 if len(failed) < len(table) else 1


def _horizon(quotes: pd.DataFrame, trade) -> dt.date:
    """The last date a set of policy-path quotes can reach, with room for the spot lag and the roll."""
    columns = [str(c).strip().lower() for c in quotes.columns]
    if "end_date" in columns:
        return max(as_date(v) for v in quotes[quotes.columns[columns.index("end_date")]])
    months = int(ois_quotes(quotes)["months"].max())
    return add_months(trade, months) + dt.timedelta(days=10)


def _policy_path(args: argparse.Namespace) -> int:
    """The repo-rate path after each MPC meeting implied by one day's OIS quotes."""
    try:
        trade = _date_arg(args.trade_date, "--trade-date")
        calendar = _ois_calendar(args)
        quotes = _read_csv_text(args.quotes)
        meetings = load_mpc_calendar(args.meetings or DEFAULT_MPC_CSV, until=_horizon(quotes, trade),
                                     project=args.project, calendar=calendar, as_of=trade)
        policy = _read_csv_text(args.policy_rates) if args.policy_rates else None
        notes = []
        current = args.policy_rate
        if current is None:
            if policy is None:
                raise ValueError("give --policy-rate, or --policy-rates for the repo rate in force")
            current = policy_rate_on(policy, trade)  # known on the trade date: never a later row of the table
            if math.isnan(current):
                raise ValueError(f"--policy-rates has no repo rate in force on {trade}")
            listed = set(meetings.loc[~meetings["projected"].astype(bool), "decision_date"])
            later = [(d, r) for d, r in read_policy_table(policy) if trade < d <= calendar.add_business_days(trade, 1)]
            if trade in listed and later:
                # a decision day and a change dated the next business day: most likely that day's decision dated a day
                # late (BIS dates some so), and then the rate known on the trade date is the later row's; never guess
                raise ValueError(f"{trade} is an MPC decision day and --policy-rates dates a repo-rate change to "
                                 f"{later[0][1]:g}% on {later[0][0]}, the next business day: if that is the decision "
                                 f"announced on {trade} dated a day late (as BIS dates some), the rate known on "
                                 f"{trade} is {later[0][1]:g}%, not the table's {current:g}%. Give --policy-rate with "
                                 "the repo rate known on the trade date")
            if trade in listed:
                notes.append(f"{trade} is an MPC decision day: the starting rate {current:g}% from --policy-rates "
                             "must include that day's decision")
            if later:
                notes.append(f"--policy-rates dates a repo-rate change on {later[0][0]}, after the trade date: not "
                             "used (if it is a decision announced on the trade date and dated a day late, as BIS "
                             "dates some, give --policy-rate)")
        spread, estimate = args.spread_bp, None
        if spread is None:
            if not (args.store and policy is not None):
                raise ValueError("give --spread-bp, or --store with --policy-rates to estimate it from the call rate")
            as_of = _time_arg(args.as_of, "--as-of") if args.as_of else None
            rates = funding_rates(rbi_store.read_table(args.store, "money_market_daily"), "call")
            estimate = mibor_repo_spread(rates, policy, trade, as_of=as_of, calendar=calendar)
            spread = estimate.spread_bp
        result = implied_policy_path(quotes, trade, current_policy_pct=current, meetings=meetings, spread_bp=spread,
                                     lam=args.lam, calendar=calendar, effective_rule=args.effective_rule)
    except (ValueError, TypeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    out = Path(args.out_dir)
    _write(result.path, out / "path.csv")
    _write(result.quotes, out / "quote_fit.csv")
    diagnostics = dict(result.diagnostics)
    if estimate is not None:
        diagnostics["spread_estimate"] = {"spread_bp": estimate.spread_bp, "days": estimate.n_obs,
                                          "first": estimate.first.isoformat(), "last": estimate.last.isoformat(),
                                          "regime_start": estimate.regime_start.isoformat(),
                                          "as_of": estimate.as_of.isoformat(), "proxy": "weighted average call rate",
                                          "fallback": estimate.fallback, "note": estimate.note}
    diagnostics["notes"] = notes
    path = out / "diagnostics.json"
    path.write_text(json.dumps(diagnostics, indent=2, allow_nan=False) + "\n")
    print(f"wrote diagnostics  {path}")
    for note in notes:
        print(f"note: {note}")
    if estimate is not None:
        print(f"MIBOR-repo spread {spread:+.2f} bp: median of {estimate.n_obs} day(s) of the call rate (a stand-in for "
              f"MIBOR) from {estimate.first} to {estimate.last}")
        if estimate.fallback:
            print(f"warning: {estimate.note}")
    for rec in result.path.to_dict("records"):
        if rec["in_horizon"]:
            mark = "" if rec["identified"] else " (not identified by the quotes alone)"
            label = " projected" if rec["projected"] else ""
            print(f"{rec['decision_date']}{label}: {rec['level_pct']:.4f}% ({rec['change_bp']:+.2f} bp){mark}")
    d = result.diagnostics
    print(f"largest quote error {d['max_abs_error_bp']:.2e} bp, {d['levels']} levels, rank {d['rank']}")
    _calendar_warning(result.diagnostics["calendar_projected"])
    return 0


def _time_arg(value: str, flag: str) -> dt.datetime:
    """``value`` as a time (India time, no zone, ISO 8601), with errors naming the flag."""
    try:
        return as_time(value, flag)
    except (ValueError, TypeError) as exc:
        raise ValueError(str(exc)) from None


def _read_fixings(path: str) -> tuple[pd.Series, dict | None]:
    """A fixings file (``date``, ``rate_pct``, optional ``public_from``) as a Series indexed by date, so that two
    different rates for one date are refused, and the publication times when the column is there: a blank cell is
    FBIL's publication time of that day (``funding.fbil_published_at``), as for a file without the column."""
    table = _read_csv_text(path)
    table.columns = [str(c).strip().lower() for c in table.columns]
    missing = [c for c in ("date", "rate_pct") if c not in table]
    if missing:
        raise ValueError(f"the fixings file {path} lacks {missing} (columns: date, rate_pct, optional public_from)")
    kept = table[table["rate_pct"].astype(str).str.strip() != ""]
    dates = [_date_arg(d, f"the fixings file's date {d!r}") for d in kept["date"]]
    try:
        rates = [float(r) for r in kept["rate_pct"]]
    except ValueError as exc:
        raise ValueError(f"the fixings file {path}: {exc}") from None
    series = pd.Series(rates, index=dates, dtype=float)
    fixing_map(series)  # refuses two different rates for one date
    times = None
    if "public_from" in kept:
        times = {d: (_time_arg(t, f"the fixings file's public_from {t!r}") if str(t).strip() else fbil_published_at(d))
                 for d, t in zip(dates, kept["public_from"])}
    return series, times


def _repo_carry(args: argparse.Namespace) -> int:
    """Carry of each bond in a file funded in repo at the RBI store's rates over a horizon."""
    try:
        start, end = _date_arg(args.start, "--start"), _date_arg(args.end, "--end")
        as_of = _time_arg(args.as_of, "--as-of") if args.as_of else None
        policy = _read_csv_text(args.policy_rates) if args.policy_rates else None
        daily = rbi_store.read_table(args.store, "money_market_daily")
        funding = funding_rates(daily, args.source, as_of=as_of, policy=policy)
        excess, excess_missing = None, ""
        if args.source == "triparty" and not args.no_treps_caps:
            try:
                excess = funding_rates(daily, args.excess_source, as_of=as_of, policy=policy)
            except ValueError as exc:  # needed only where CCIL's caps bind: those rows are refused, saying why
                excess_missing = str(exc)
        bonds = _read_csv_text(args.bonds)
        if args.master:
            bonds = fill_from_master(bonds, read_master(args.master))
        haircuts = read_haircuts(args.haircuts) if args.haircuts else None
        fixings = times = None
        if args.pay_fixed is not None:
            if not (args.fixings and args.swap_tenor):
                raise ValueError("--pay-fixed needs --swap-tenor and --fixings (date, rate_pct: overnight MIBOR)")
            fixings, times = _read_fixings(args.fixings)
        months = tenor_months(args.swap_tenor) if args.swap_tenor else None
        calendar = mumbai_settlement_calendar()
        result = carry_table(bonds, start, end, funding=funding, haircuts=haircuts, face=args.face,
                             swap_rate_pct=args.pay_fixed, swap_months=months, fixings=fixings,
                             excess_funding=excess, treps_caps=not args.no_treps_caps,
                             repo_compounding=args.repo_compounding, projection="last" if args.project else None,
                             pull_to_par=not args.no_pull_to_par,
                             calendar=calendar.lenient() if args.project_calendar else calendar, as_of=as_of,
                             fixing_times=times, spread_bp=args.funding_spread_bp)
    except (ValueError, TypeError, OSError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    _write(result, Path(args.out))
    failed = result[result["error"] != ""]
    print(f"{len(result) - len(failed)} of {len(result)} bonds carried from {start} to {end} on {args.source} rates"
          + (f" public by {as_of}" if as_of else "") + (" (fixings too)" if as_of and fixings is not None else ""))
    for rec in failed.to_dict("records"):
        print(f"refused {rec['isin']}: {rec['error']}")
    if excess_missing and failed["error"].str.contains("excess_funding").any():
        print(f"no {args.excess_source} rates for the rest: {excess_missing}")
    carried = result[result["error"] == ""]
    capped = carried[carried["funding_share"] < 1]
    if len(capped):
        print(f"{len(capped)} position(s) partly funded at {args.excess_source} rates under CCIL's TREPS caps")
    if len(carried) and carried["projected_fixings"].max() > 0:
        print(f"{int(carried['projected_fixings'].max())} business day(s) of MIBOR projected (not public by --as-of "
              "or past the fixings given)")
    _calendar_warning(bool(carried["calendar_projected"].any()) if len(carried) else False)
    return 0 if len(failed) < len(result) else 1


def _ois_curves_from_quotes(path: str, calendar) -> dict:
    """One OIS curve per date of a long quotes file (``date``, ``tenor``, ``rate_pct``, ``overnight_pct``, optional
    ``public_from``; all-blank rows dropped), each bootstrapped on its date from that day's overnight fixing."""
    table = _read_csv_text(path)
    table.columns = [str(c).strip().lower() for c in table.columns]
    missing = [c for c in ("date", "tenor", "rate_pct", "overnight_pct") if c not in table]
    if missing:
        raise ValueError(f"the OIS quotes file {path} lacks {missing} (columns: date, tenor, rate_pct, overnight_pct, "
                         "optional public_from)")
    table = table[~table.apply(lambda row: all(not str(v).strip() for v in row), axis=1)] if len(table) else table
    out = {}
    for text, group in table.groupby("date", sort=True):
        day = _date_arg(text, f"the OIS quotes file's date {text!r}")
        nights = {str(v).strip() for v in group["overnight_pct"]}
        if len(nights) != 1:
            raise ValueError(f"the OIS quotes file gives {day} several overnight fixings: {sorted(nights)}")
        try:
            overnight = float(nights.pop())
        except ValueError:
            raise ValueError(f"the OIS quotes file's overnight_pct for {day} is not a number") from None
        curve = bootstrap_ois(group[["tenor", "rate_pct"]], day, overnight, calendar=calendar,
                              source=f"{Path(path).name} {day}")
        public = None
        if "public_from" in group and str(group["public_from"].iloc[0]).strip():
            public = _time_arg(str(group["public_from"].iloc[0]), f"the OIS quotes file's public_from for {day}")
        out[day] = return_engine.CurveEntry(curve, public)
    return out


def _returns(args: argparse.Namespace) -> int:
    """Daily NAV, positions, P&L attribution and performance of a book of trades (the return engine)."""
    try:
        for flag in ("trades", "swaps", "flows", "marks", "nss_params", "ois_quotes", "fixings", "haircuts", "costs",
                     "master", "liquidity", "residual_history"):
            value = getattr(args, flag)
            if value and not Path(value).exists():
                raise FileNotFoundError(f"--{flag.replace('_', '-')}: {value} not found")
        start, end = _date_arg(args.start, "--start"), _date_arg(args.end, "--end")
        calendar = mumbai_settlement_calendar()
        calendar = calendar.lenient() if args.project_calendar else calendar
        funding_tables = {}
        if args.store:
            daily = rbi_store.read_table(args.store, "money_market_daily")
            for source in sorted({args.funding_source, args.excess_source, "triparty"}):
                try:
                    funding_tables[source] = funding_rates(daily, source)
                except ValueError as exc:
                    print(f"note: no {source} rates from the store ({exc})")
        fixings, times = ({}, None)
        if args.fixings:
            series, times = _read_fixings(args.fixings)
            fixings = {d: float(v) for d, v in series.items()}
        master = read_master(args.master) if args.master else None
        market = return_engine.MarketData(
            marks=_read_csv_text(args.marks),
            curves=return_engine.read_nss_params(args.nss_params, calendar=calendar) if args.nss_params else {},
            ois_curves=_ois_curves_from_quotes(args.ois_quotes, calendar) if args.ois_quotes else {},
            fixings=fixings, fixing_times=times, funding=funding_tables,
            haircuts=read_haircuts(args.haircuts) if args.haircuts else None,
            liquidity=_read_csv_text(args.liquidity) if args.liquidity else None, master=master,
            residual_history=_read_csv_text(args.residual_history) if args.residual_history else None)
        policy = return_engine.FundingPolicy(
            segment=args.funding_source, excess_segment=args.excess_source, spread_bp=args.funding_spread_bp,
            sizing=args.sizing, margin_pct=args.margin_pct, idle_cash_rate_pct=args.idle_rate_pct,
            on_shortfall="overdraft" if args.overdraft_rate_pct is not None else "raise",
            overdraft_rate_pct=args.overdraft_rate_pct, projection="last" if args.project_funding else None,
            rate_basis=args.rate_basis)
        reference = dict(return_engine.DEFAULT_REFERENCE)
        for item in args.reference or []:
            kind, sep, curve_name = item.partition("=")
            if not sep or not kind.strip() or not curve_name.strip():
                raise ValueError(f"--reference {item!r}: give KIND=CURVE, e.g. sdl=sdl")
            reference[kind.strip().lower()] = curve_name.strip()
        unused = sorted(set(market.curves) - set(reference.values()))
        settings = return_engine.EngineSettings(
            access=return_engine.access_policy(args.access), project_calendar=args.project_calendar,
            allow_t0=args.allow_t0, funding=policy,
            attribution=return_engine.AttributionSettings(factor_basis=args.factor_basis, basis=args.basis,
                                                          reference=tuple(sorted(reference.items()))),
            costs=read_cost_table(overrides=args.costs) if args.costs else None)
        result = return_engine.run_book(args.trades or None, args.swaps or None, args.flows or None, market=market,
                                        settings=settings, start=start, end=end)
        written = return_engine.write_result(result, Path(args.out_dir))
    except (ValueError, TypeError, OSError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for path in written:
        print(f"wrote {path}")
    for name in unused:
        print(f"note: the curve {name!r} in --nss-params is the reference of no kind (pass --reference KIND={name})")
    breaches = result.checks[(result.checks["check"] == "identity") & (result.checks["status"] != "ok")]
    flagged = result.checks[result.checks["status"] == "flag"]
    for rec in result.performance.to_dict("records"):
        print(f"book {rec['book']}: {rec['business_days']} day(s), TWR {rec['twr']:.6%}, P&L Rs {rec['pnl']:,.2f}, "
              f"costs Rs {rec['costs']:,.2f}")
    print(f"identity breaches: {len(breaches)}; flags: {len(flagged)} "
          f"({', '.join(sorted(set(flagged['check']))) or 'none'})")
    for note in result.notes:
        print(f"note: {note}")
    _calendar_warning(bool(result.nav["calendar_projected"].any()) if len(result.nav) else False)
    return 0 if len(result.positions) else 1  # 1: nothing could be valued


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

    p = sub.add_parser("corp-price", help="price, yield, accrued interest, workouts and risk of a corporate bond "
                                          "(NCD) from its terms, or of every row of a prices file")
    p.add_argument("--terms", help="corporate_terms.csv (with --isin, or with --prices)")
    p.add_argument("--isin", help="the bond's ISIN in --terms (or a label for terms given as flags)")
    p.add_argument("--options", help="corporate_options.csv: calls and puts")
    p.add_argument("--amortisation", help="corporate_amortisation.csv: principal by date")
    p.add_argument("--schedule-file", help="corporate_schedule.csv: due, payment and record dates and amounts")
    p.add_argument("--coupon", type=float, help="coupon, percent a year (0 with --frequency 0)")
    p.add_argument("--issue-date", help="deemed date of allotment, YYYY-MM-DD")
    p.add_argument("--maturity", help="maturity date, YYYY-MM-DD (none with --perpetual)")
    p.add_argument("--frequency", type=int, choices=BOND_FREQUENCIES, default=1,
                   help="coupons a year; 0 for zero-coupon and cumulative bonds")
    p.add_argument("--schedule", choices=[s for s in SCHEDULES if s != "explicit"], default="forward",
                   help="how due dates are counted (default forward from the allotment)")
    p.add_argument("--first-coupon-date", help="first coupon date of a forward or backward schedule")
    p.add_argument("--first-period", choices=FIRST_PERIODS, default="separate",
                   help="clubbed: the first short period paid with the next")
    p.add_argument("--redemption", type=float, default=100.0, help="redemption per 100 face (cumulative: accreted)")
    p.add_argument("--conventions", choices=[k for k in CORPORATE_CONVENTIONS if k != "frb"],
                   help="default by the issue date: ncd (2017 on), ncd_2013 (Dec 2013-2016), ncd_pre2013")
    p.add_argument("--call", action="append", default=[], metavar="DATE[:PRICE]", help="a call (repeatable)")
    p.add_argument("--put", action="append", default=[], metavar="DATE[:PRICE]", help="a put (repeatable)")
    p.add_argument("--perpetual", action="store_true", help="a perpetual: deemed to mature 100 years after issue")
    p.add_argument("--tier", choices=[t for t in TIERS if t], help="at1 (valued to the first call) or tier2")
    p.add_argument("--record-days", type=int, help="record date, calendar days before each due date (default 15)")
    p.add_argument("--record-roll", choices=["preceding", "following"], help="record date on a holiday")
    p.add_argument("--leap-year-rule", choices=LEAP_YEAR_RULES, help="which 366-day year (default anniversary)")
    p.add_argument("--postponed-coupon", choices=PERIOD_END_RULES, help="interest on a coupon paid late")
    p.add_argument("--redemption-interest", choices=PERIOD_END_RULES, help="interest on a redemption paid early")
    p.add_argument("--flow-dates", choices=FLOW_DATE_RULES, help="discount on due or payment dates")
    p.add_argument("--calendar", choices=["settlement", "none"], default="settlement",
                   help="roll payment and record dates on the Mumbai settlement calendar (lenient after its years) "
                        "or not at all")
    p.add_argument("--settlement", help="settlement date, YYYY-MM-DD")
    given = p.add_mutually_exclusive_group()
    given.add_argument("--yield", dest="yield_pct", type=float, help="yield, percent (annual, actual/365)")
    given.add_argument("--price", dest="clean_price", type=float, help="clean price per 100 face")
    p.add_argument("--valuation", choices=["ytm", "worst", "sebi"], default="ytm",
                   help="to maturity, to worst over calls, or SEBI's trigger rule for calls and puts (from --yield)")
    p.add_argument("--prices", help="CSV of isin, settlement_date and clean_price or yield_pct: price every row")
    p.add_argument("--out", help="with --prices: the analytics CSV to write")
    p.add_argument("--cash-flows", action="store_true", help="also list the flows a buyer receives")
    p.set_defaults(func=_corp_price)

    p = sub.add_parser("frb-price", help="a GoI floating-rate bond's coupons as known at a time, price, yield, "
                                         "discount margin and spread risk")
    p.add_argument("--isin", required=True, help="the FRB's ISIN in --terms")
    p.add_argument("--terms", help="GoI FRB terms CSV (default: the shipped goi_frb_terms.csv)")
    p.add_argument("--coupons", help="announced coupons CSV (default: the shipped goi_frb_coupons.csv)")
    p.add_argument("--no-coupons", action="store_true", help="use no announced coupon, only T-bill results")
    source = p.add_mutually_exclusive_group()
    source.add_argument("--tbills", help="CSV of 182-day T-bill results: auction_date, tenor_days, way or wap, "
                                         "public_from (or published, release_date)")
    source.add_argument("--store", help="an RBI store directory: its T-bill results (rbi-store-ingest)")
    p.add_argument("--tbill-time", help="HH:MM on the auction day for results without a public time (a rule)")
    p.add_argument("--as-of", help="use only what was public by this time (India, YYYY-MM-DDTHH:MM)")
    p.add_argument("--allow-fewer", action="store_true", help="project from fewer than three T-bill results")
    p.add_argument("--settlement", required=True, help="settlement date, YYYY-MM-DD")
    given = p.add_mutually_exclusive_group(required=True)
    given.add_argument("--price", dest="clean_price", type=float, help="clean price per 100 face")
    given.add_argument("--dm", type=float, help="discount margin over the index, basis points")
    p.add_argument("--index", type=float, help="the index the discount margin is measured over (default: the base "
                                               "of the flat projection)")
    p.add_argument("--rates", action="store_true", help="also list every coupon period's rate from settlement on")
    p.add_argument("--out", help="write every period's rate, status and public time to this CSV")
    p.set_defaults(func=_frb_price)

    p = sub.add_parser("curve-fit", help="fit a zero curve to one settlement date's G-sec and T-bill prices or "
                                         "yields; write the curve on a grid and every bond's rich/cheap residual")
    p.add_argument("--prices", required=True,
                   help="CSV, one row per ISIN: isin and clean_price or yield_pct; optional kind, coupon_pct, "
                        "maturity_date, issue_date, first_coupon_date, weight, flags, traded, trade_count, volume_cr")
    p.add_argument("--master", help="security master CSV (sovereign-master) giving kind, coupon and dates by ISIN")
    p.add_argument("--settlement", required=True, help="settlement date of the prices, YYYY-MM-DD")
    p.add_argument("--method", choices=METHODS, default="nss")
    p.add_argument("--check", choices=(*METHODS, "none"), default="pspline",
                   help="cross-check curve (default pspline); residuals where it differs are flagged model_risk")
    p.add_argument("--model-risk-bp", type=float, default=MODEL_RISK_BP,
                   help="par-yield gap between the curves that flags model risk (UNVERIFIED default 3)")
    p.add_argument("--objective", choices=OBJECTIVES, default="yield")
    p.add_argument("--loss", choices=LOSSES, default="squared")
    p.add_argument("--outlier-bp", type=float,
                   help="take the worst bond out of the fit while its residual exceeds this and 4 robust sd")
    p.add_argument("--weights", choices=WEIGHT_SCHEMES, default="equal")
    p.add_argument("--kinds", default="gsec,tbill,cmb", help="kinds in the fit, comma-separated (sdl for an SDL curve)")
    p.add_argument("--min-volume-cr", type=float, default=5.0,
                   help="smallest daily volume (Rs crore) in the fit; rows without a volume are kept")
    p.add_argument("--anchor", type=float, help="overnight rate (percent) anchoring the start of the curve")
    p.add_argument("--drop-benchmarks", action="store_true", help="keep rows flagged benchmark out of the fit")
    p.add_argument("--no-leave-one-out", action="store_true", help="skip the leave-one-out refits")
    p.add_argument("--history", help="earlier residuals (date, isin, resid_yield_bp, optional years_to_maturity) "
                                     "for z-scores; written to rich_cheap.csv")
    p.add_argument("--residual", choices=tuple(RESIDUAL_CHOICES), default="in_fit",
                   help="residual scored against --history: in_fit (resid_yield_bp) or loo (each bond against the "
                        "curve refitted without it, which end bonds cannot pull towards themselves)")
    p.add_argument("--append-history", metavar="CSV",
                   help="add the day's residuals to this history file (created if absent; the day's old rows replaced)")
    p.add_argument("--window", type=int, default=ZSCORE_WINDOW, help="z-score window, observations")
    p.add_argument("--min-obs", type=int, default=ZSCORE_MIN_OBS, help="fewest observations for a z-score")
    p.add_argument("--fly", action="append", metavar="SHORT,BODY,LONG",
                   help="ISINs of a butterfly, short to long (repeatable); written to butterflies.csv")
    p.add_argument("--pair", action="append", metavar="SHORTER,LONGER",
                   help="ISINs of a DV01-neutral pair (repeatable), e.g. the 10-year against a 40-year; pairs.csv")
    p.add_argument("--ledger", help="issuance ledger CSV (cr-strat auctions): reissues and outstanding stock as of the "
                                    "settlement date, joined to each residual in drivers.csv")
    p.add_argument("--nss-params", metavar="CSV",
                   help="add (or replace) this day's NSS parameters in this table, the layout cr-strat returns "
                        "--nss-params reads (date, settlement_date, beta0..tau2, t_min, t_max, curve, public_from)")
    p.add_argument("--trade-date", help="the prices' trade date for --nss-params (default: the business day before "
                                        "--settlement)")
    p.add_argument("--curve-name", default="gsec", help="the curve's name in --nss-params (gsec; sdl for an SDL curve)")
    p.add_argument("--public-from", help="when the fit was public, for --nss-params (blank: the marks' default time)")
    p.add_argument("--control", metavar="DRIVERS",
                   help="regress the day's --residual on these drivers of drivers.csv, comma-separated, e.g. "
                        f"{','.join(CONTROL_DRIVERS)} (UNVERIFIED form): resid_ctrl_bp and controls.csv")
    p.add_argument("--out-dir", required=True)
    p.set_defaults(func=_curve_fit)

    def _ois_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--quotes", required=True,
                       help="CSV of one day's par quotes: tenor and rate_pct, or fbil-mm's columns (rate_quoted_pct)")
        p.add_argument("--trade-date", required=True, help="the quotes' trade date, YYYY-MM-DD")
        p.add_argument("--overnight", type=float, required=True, help="the trade date's overnight fixing, percent")
        p.add_argument("--conventions", choices=tuple(OIS_CONVENTIONS), default="mibor")
        p.add_argument("--max-extrapolation-years", type=float, default=0.0,
                       help="years past the last quote the curve may run on its last forward rate (default none)")
        p.add_argument("--project-calendar", action="store_true",
                       help="treat weekdays of years without a holiday list (2027 on) as business days")

    p = sub.add_parser("ois-curve", help="bootstrap a MIBOR-OIS discount curve from one day's par quotes")
    _ois_args(p)
    p.add_argument("--out-dir", required=True)
    p.set_defaults(func=_ois_curve)

    p = sub.add_parser("asset-swaps", help="yield-yield, par-par, proceeds and z-spreads of bonds against an OIS curve")
    _ois_args(p)
    p.add_argument("--bonds", required=True,
                   help="CSV: isin, clean_price or yield_pct; kind, coupon_pct, maturity_date (or --master); optional "
                        "issue_date, settlement_date")
    p.add_argument("--master", help="security master CSV (sovereign-master) filling kind, coupon and dates by ISIN")
    p.add_argument("--settlement", help="settlement date of the prices (default: the swaps' effective date, T+1)")
    p.add_argument("--out", required=True)
    p.set_defaults(func=_asset_swaps)

    p = sub.add_parser("policy-path", help="the repo-rate level after each MPC meeting implied by one day's OIS quotes")
    p.add_argument("--quotes", required=True, help="CSV: tenor and rate_pct, or end_date and rate_pct")
    p.add_argument("--trade-date", required=True, help="the quotes' trade date, YYYY-MM-DD")
    p.add_argument("--policy-rate", type=float,
                   help="the repo rate known on the trade date (in force that day, after any decision announced that "
                        "day), percent")
    p.add_argument("--policy-rates", help="policy-rate table (effective_date, repo_pct): the rate in force on the "
                                          "trade date, and the regime for the spread estimate; on a decision day a "
                                          "change it dates the next business day is refused (give --policy-rate)")
    p.add_argument("--spread-bp", type=float, help="overnight MIBOR minus the repo rate, bp")
    p.add_argument("--store", help="RBI store: estimate the spread from its call rate (a stand-in for MIBOR)")
    p.add_argument("--as-of", help="use only store rates public by this time (India time); default: the trade date's "
                                   "start")
    p.add_argument("--meetings", help=f"MPC meeting file in the layout of {DEFAULT_MPC_CSV.name} (default: shipped)")
    p.add_argument("--project", action="store_true", help="project meetings past the last complete year (UNVERIFIED)")
    p.add_argument("--lam", type=float, default=DEFAULT_LAM, help="penalty on changes between meetings")
    p.add_argument("--effective-rule", choices=EFFECTIVE_RULES, default="next_business_day")
    p.add_argument("--project-calendar", action="store_true",
                   help="treat weekdays of years without a holiday list (2027 on) as business days")
    p.add_argument("--out-dir", required=True)
    p.set_defaults(func=_policy_path)

    p = sub.add_parser("repo-carry", help="carry of bonds funded in repo at an RBI store's daily rates over a horizon")
    p.add_argument("--store", required=True, help="RBI store whose derived/money_market_daily.csv gives the rates")
    p.add_argument("--bonds", required=True,
                   help="CSV: isin, kind, coupon_pct, maturity_date, clean_price; optional face (rupees), liquidity "
                        "(CCIL's class), haircut_pct, side (long or short) and a short's special_repo_pct")
    p.add_argument("--master", help="security master CSV filling kind, coupon and dates by ISIN")
    p.add_argument("--haircuts", help="haircut table kept by you (effective_date, isin or kind, buckets, haircut_pct, "
                                      "source)")
    p.add_argument("--start", required=True, help="the purchase's settlement date, YYYY-MM-DD")
    p.add_argument("--end", required=True, help="end of the horizon, YYYY-MM-DD (exclusive)")
    p.add_argument("--source", choices=FUNDING_SOURCES, default="triparty")
    p.add_argument("--excess-source", choices=FUNDING_SOURCES, default="market_repo",
                   help="rates for the cash TREPS does not lend against SDLs and illiquid G-secs under CCIL's caps "
                        "(RMD/SS/26/13; default market_repo)")
    p.add_argument("--no-treps-caps", action="store_true", help="fund every position in full at --source's rates")
    p.add_argument("--funding-spread-bp", type=float, default=0.0,
                   help="spread over every repo rate, bp, e.g. what a constituent pays its bank (UNVERIFIED default 0)")
    p.add_argument("--as-of", help="use only rates and fixings public by this time (India time), as a signal would")
    p.add_argument("--project", action="store_true",
                   help="hold the last known rate (and MIBOR fixing) past the end of what is known")
    p.add_argument("--project-calendar", action="store_true",
                   help="treat weekdays of years without a holiday list (2027 on) as business days")
    p.add_argument("--policy-rates", help="policy-rate table for the repo rate column")
    p.add_argument("--face", type=float, default=1e9,
                   help="face in rupees where the file gives none (default 100 crore)")
    p.add_argument("--pay-fixed", type=float, help="add a pay-fixed OIS at this rate (percent) on each bond's face")
    p.add_argument("--swap-tenor", help="the OIS tenor, e.g. 5Y")
    p.add_argument("--fixings", help="CSV of overnight MIBOR fixings (date, rate_pct, optional public_from: a blank "
                                     "cell is FBIL's publication time that day) for the OIS's floating leg; with "
                                     "--as-of only those public by then are used")
    p.add_argument("--repo-compounding", choices=REPO_COMPOUNDING, default="simple")
    p.add_argument("--no-pull-to-par", action="store_true", help="leave the pull to par out of the carry")
    p.add_argument("--out", required=True)
    p.set_defaults(func=_repo_carry)

    p = sub.add_parser("returns", help="daily NAV, positions, P&L attribution and performance of a book of bond and "
                                       "OIS trades, funded in repo at an RBI store's rates (docs/analytics.md)")
    p.add_argument("--trades", help="bond trades CSV (trade_id, book, isin, side, face, trade_date; price or "
                                    "yield_pct, kind, coupon_pct, maturity_date, settlement_date, venue, mid_price, "
                                    "liquidity, broker, special_repo_pct ...: returns.TRADE_COLUMNS)")
    p.add_argument("--swaps", help="OIS trades CSV (trade_id, book, trade_date, months, notional, pay_fixed, "
                                   "fixed_rate_pct, mid_rate_pct, effective, purpose)")
    p.add_argument("--flows", help="capital flows CSV (date, book, amount in rupees; start of the day)")
    p.add_argument("--marks", required=True,
                   help="marks CSV (date, isin, clean_price or yield_pct for the standard settlement, source traded "
                        "or valuation, vwap, last_price, settlement_date, public_from): licensed prices, never shipped")
    p.add_argument("--nss-params", help="NSS curves CSV (date, beta0..beta3, tau1, tau2, t_min, t_max, "
                                        "settlement_date, curve, public_from)")
    p.add_argument("--ois-quotes", help="OIS quotes CSV (date, tenor, rate_pct, overnight_pct, public_from), "
                                        "bootstrapped per date")
    p.add_argument("--fixings", help="overnight MIBOR CSV (date, rate_pct, optional public_from)")
    p.add_argument("--store", help="RBI store whose derived/money_market_daily.csv gives the funding rates")
    p.add_argument("--haircuts", help="haircut table kept by you (funding.read_haircuts)")
    p.add_argument("--costs", help="your cost table, overriding the defaults (costs.COST_COLUMNS)")
    p.add_argument("--master", help="security master CSV filling kind, coupon and dates by ISIN")
    p.add_argument("--liquidity", help="CCIL liquidity classes kept by you (effective_date, isin, liquidity)")
    p.add_argument("--residual-history", help="curvefit residual history (for model marks)")
    p.add_argument("--access", required=True, choices=sorted(return_engine.ACCESS_PRESETS),
                   help="the fund's access preset: decides repo, shorts and OIS (no default)")
    p.add_argument("--start", help="first day, YYYY-MM-DD (default: the first trade or flow)")
    p.add_argument("--end", required=True, help="last day, YYYY-MM-DD")
    p.add_argument("--funding-source", choices=FUNDING_SOURCES, default="triparty")
    p.add_argument("--excess-source", choices=FUNDING_SOURCES, default="market_repo")
    p.add_argument("--funding-spread-bp", type=float, default=0.0)
    p.add_argument("--sizing", choices=return_engine.SIZINGS, default="cash_need")
    p.add_argument("--margin-pct", type=float, help="TREPS initial margin, percent (default: the cost table's)")
    p.add_argument("--idle-rate-pct", type=float, default=0.0, help="interest on idle cash (UNVERIFIED default 0)")
    p.add_argument("--overdraft-rate-pct", type=float, help="fund a shortfall at this rate instead of refusing it")
    p.add_argument("--project-funding", action="store_true", help="hold the last funding rate past the table's end")
    p.add_argument("--project-calendar", action="store_true",
                   help="treat weekdays of years without a holiday list (2027 on) as business days")
    p.add_argument("--allow-t0", action="store_true", help="accept T+0 G-sec, SDL and T-bill trades")
    p.add_argument("--factor-basis", choices=return_engine.FACTOR_BASES, default="legendre3")
    p.add_argument("--basis", choices=return_engine.PNL_BASES, default="actual")
    p.add_argument("--reference", action="append", metavar="KIND=CURVE",
                   help="attribute a kind against a curve of --nss-params by its name (repeatable; e.g. sdl=sdl for "
                        "S6; default every G-sec, bill, STRIPS and SDL against gsec, CP and CD against ois)")
    p.add_argument("--rate-basis", choices=return_engine.RATE_BASES, default="deal_day",
                   help="repo interest: the deal day's rate for every night (default) or each calendar day's latest "
                        "dealing rate, as repo-carry")
    p.add_argument("--out-dir", required=True)
    p.set_defaults(func=_returns)

    p = sub.add_parser("rbi-releases", help="auctions, liquidity operations, money-market rates and calendars from "
                                            "saved RBI pages and RSS feeds")
    p.add_argument("files", nargs="+", help="saved BS_PressReleaseDisplay pages and pressreleases_rss.xml copies")
    p.add_argument("--out-dir", required=True,
                   help="one CSV per release kind plus releases.csv; re-running replaces a release's rows")
    p.set_defaults(func=_rbi_releases)

    p = sub.add_parser("rbi-store-poll", help="fetch RBI's press-release feed once, if due, into a store (for cron); "
                                              "exit 0 ok, 4 ok but needs a look, 3 blocked, cooling down or "
                                              "stopped, 1 other errors, 2 bad arguments")
    p.add_argument("--store", required=True, help="store directory, created on first use")
    p.add_argument("--url", type=_feed_url_arg, default=rbi_store.FEED_URL,
                   help="feed address (default: RBI's press-release feed)")
    p.add_argument("--min-interval", type=_minutes_arg, default=25,
                   help="minutes: no request if the last one, answered or not, was more recent (default 25; set it a "
                        "little below the cron period; at most 1440)")
    p.add_argument("--user-agent", type=_user_agent_arg, default=rbi_store.DEFAULT_USER_AGENT,
                   help="client name sent with the request, one line of visible ASCII: keep it generic, nothing that "
                        "names you or the firm")
    p.add_argument("--timeout", type=_seconds_arg, default=60.0,
                   help=f"seconds to wait for the answer (default 60, at most {rbi_store.MAX_TIMEOUT:g})")
    p.add_argument("--no-raw", action="store_true", help="keep no raw copy of a new feed")
    p.add_argument("--resume", action="store_true",
                   help="end a stop or a cool-down after a block: the operator's decision, once the site has been "
                        "checked in a browser (never in cron)")
    p.set_defaults(func=_rbi_store_poll)

    p = sub.add_parser("rbi-store-ingest", help="add saved RBI pages, feed copies and press-release listings to a "
                                                "store; rebuild its gaps and derived tables")
    p.add_argument("files", nargs="*", help="saved BS_PressReleaseDisplay pages (a release, or the listing) and "
                                            "pressreleases_rss.xml copies")
    p.add_argument("--store", required=True, help="store directory, created on first use")
    p.add_argument("--master", help="security master CSV (sovereign-master), kept in the store for the OMO and switch "
                                    "ISINs")
    p.add_argument("--policy-rates", help="policy rates entered by hand (CSV: effective_date, repo_pct, sdf_pct, "
                                          "msf_pct, source), kept in the store and checked against each day's corridor")
    p.add_argument("--no-raw", action="store_true", help="keep no raw copy of the feeds and pages read")
    p.set_defaults(func=_rbi_store_ingest)

    p = sub.add_parser("rbi-store-status", help="what a store holds: rows and dates per kind, possible gaps, failed "
                                                "releases, the poller's state")
    p.add_argument("--store", required=True, help="store directory")
    p.set_defaults(func=_rbi_store_status)

    def _macro_scope(p: argparse.ArgumentParser) -> None:
        p.add_argument("--cpi-levels", choices=macro_store.CPI_LEVELS, default="groups",
                       help="base-2024 CPI kept for All India: down to groups (default, while MoSPI's terms are not "
                            "settled), or every node down to items")
        p.add_argument("--states", action="store_true", help="also keep (and fetch) each state's general CPI index")
        p.add_argument("--no-raw", action="store_true", help="keep no raw copy of the answers")
        p.add_argument("--mospi-raw", action="store_true",
                       help="keep raw copies of MoSPI's answers too (off by default: its terms are not known)")
        p.add_argument("--release-dates", type=_existing_file_arg,
                       help="MoSPI release dates kept by hand (the columns of src/cr_strat/data/macro_release_dates."
                            "csv): kept in the store, its rows override the shipped ones")
        p.add_argument("--index-events", type=_existing_file_arg,
                       help="index inclusion events kept by hand (an example with sources: "
                            "data/index_events_example.csv), checked and kept in the store")

    p = sub.add_parser("macro-store-update", help="fetch what is new from the macro and global sources into a store "
                                                  "(for cron); exit 0 ok, 4 ok but needs a look, 3 blocked, cooling "
                                                  "down or stopped, 1 other errors, 2 bad arguments")
    p.add_argument("--store", required=True, help="store directory, created on first use")
    p.add_argument("--sources", type=_sources_arg, default=macro_store.DEFAULT_SOURCES,
                   help="comma-separated: mospi, ust, nyfed, eia, h10, bis, or all; default "
                        f"{','.join(macro_store.DEFAULT_SOURCES)} (MoSPI only when named: its licence is not settled; "
                        "Treasury and EIA are off by default too)")
    p.add_argument("--max-requests", type=_count_arg, default=macro_store.DEFAULT_MAX_REQUESTS,
                   help=f"requests in this run, at most (default {macro_store.DEFAULT_MAX_REQUESTS})")
    p.add_argument("--max-per-host", type=_count_arg, default=macro_store.DEFAULT_MAX_PER_HOST,
                   help=f"requests to one host in this run, at most (default {macro_store.DEFAULT_MAX_PER_HOST}); each "
                        "host's own daily cap applies too")
    p.add_argument("--min-interval", type=_interval_arg, default=macro_store.DEFAULT_MIN_INTERVAL.total_seconds() / 60,
                   help="minutes: a source whose last update that asked something began more recently is skipped "
                        "(default 25)")
    p.add_argument("--user-agent", type=_generic_agent_arg, default=macro_store.ms.DEFAULT_USER_AGENT,
                   help="client name sent with each request: generic, nothing that names you or the firm")
    p.add_argument("--timeout", type=_seconds_arg, default=60.0,
                   help=f"seconds to wait for each answer (default 60, at most {rbi_store.MAX_TIMEOUT:g})")
    p.add_argument("--resume", action="store_true",
                   help="end a stop or a cool-down of the named sources' hosts after a block: the operator's decision, "
                        "once the site has been checked in a browser (never in cron)")
    p.add_argument("--retry-failed", action="store_true",
                   help="queue again the requests an earlier run gave up on (a refused answer, an HTTP 4xx)")
    p.add_argument("--no-backfill", action="store_true", help="fetch no history, only the recent window")
    p.add_argument("--mospi-history", choices=macro_store.MOSPI_HISTORY, default="derived",
                   help="MoSPI history: what the derived tables use, from 2013 (default), or everything kept")
    p.add_argument("--legacy-tls", action="store_true",
                   help="allow legacy TLS renegotiation for api.mospi.gov.in (certificates still checked)")
    _macro_scope(p)
    p.set_defaults(func=_macro_store_update)

    p = sub.add_parser("macro-store-ingest", help="add saved answers of the macro and global sources to a store; "
                                                  "rebuild its tables")
    p.add_argument("files", nargs="*", help="saved answers (MoSPI JSON, Treasury and New York Fed CSV or JSON, EIA "
                                            "and H.10 pages, BIS CSV), recognised by their content; copies of the "
                                            "store's own answers (its raw/) are dated by its request log")
    p.add_argument("--store", required=True, help="store directory, created on first use")
    p.add_argument("--cpi-series", choices=("Current", "Back"),
                   help="the series of saved base-2012 or 2010 CPI answers, whose rows do not say it (the store's own "
                        "raw copies are read as their request asked); without it such an answer is refused")
    _macro_scope(p)
    p.set_defaults(func=_macro_store_ingest)

    p = sub.add_parser("macro-store-status", help="what a macro store holds: per source its series, history, last "
                                                  "request, blocks and failures; the derived tables' series")
    p.add_argument("--store", required=True, help="store directory")
    p.add_argument("--series", type=_pattern_arg, help="list the series whose id matches this regular expression")
    p.add_argument("--policy-rates", type=_existing_file_arg,
                   help="a policy-rate table kept by hand (rbi-store-ingest --policy-rates columns): compare BIS's "
                        "repo rate changes with its own (a row that moves only the corridor is not compared)")
    p.set_defaults(func=_macro_store_status)

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

    p = sub.add_parser("event-ledger", help="the S4 event ledger: auctions, switches, OMOs, liquidity operations, "
                                            "calendars, MPC and macro releases with their public times; DV01 "
                                            "supply by week and bucket; event windows (docs/analytics.md)")
    p.add_argument("--auctions", help="auctions.csv written by the auctions command")
    p.add_argument("--ledger", help="ledger.csv written by the same auctions run")
    p.add_argument("--store", help="RBI store directory (release tables, derived tables, releases, gaps)")
    p.add_argument("--releases", help="directory of CSVs written by rbi-releases (result and announcement tables)")
    p.add_argument("--handbook", nargs="*", default=[], help="saved RBI Handbook Table 221 pages (bids, devolvement)")
    p.add_argument("--master", help="security master CSV (ISINs of auctions known only from an announcement)")
    p.add_argument("--switches", help="switches.csv written by the auctions command (destination cut-offs)")
    p.add_argument("--snapshot", help="RBI GoI-outstanding snapshot(s) for the stock (CSV or saved page)")
    p.add_argument("--calendar-lines", help="issuance calendar lines kept by you, the T-bill calendar's above all "
                                            "(event_ledger.CALENDAR_LINE_COLUMNS), added to the store's")
    p.add_argument("--mpc", help="MPC meetings file (policy_path's layout, plus an optional decision_time column, "
                                 "HH:MM; default: the shipped FY2026-27 file)")
    p.add_argument("--no-mpc", action="store_true", help="leave MPC meetings out")
    p.add_argument("--policy-rates", help="policy-rate table (effective_date, repo_pct, ...) for the MPC rows")
    p.add_argument("--macro", action="store_true", help="add MoSPI's releases from the shipped release table")
    p.add_argument("--macro-releases", help="your release-date table (mospi's layout), over the shipped one")
    p.add_argument("--macro-from", help="with --macro-to: every MoSPI release in the span, by rule beyond the table")
    p.add_argument("--macro-to", help="last day for --macro-from")
    p.add_argument("--index-events", help="index-event table kept by you (global_sources' layout)")
    p.add_argument("--macro-store", help="macro store whose derived index_events table to use")
    p.add_argument("--panel", help="a later (date, isin) price panel for the event paths: marks, price_panel rows, "
                                   "or curvefit's residual history (licensed prices: never shipped)")
    p.add_argument("--panel-columns", default="yield_pct", help="comma-separated panel columns to attach")
    p.add_argument("--panel-key", default="isin", help="the panel's key column ('' for one row per date)")
    p.add_argument("--window-key", default="isin", choices=["isin", "key"], help="the window column it matches")
    p.add_argument("--panel-dates", choices=event_ledger.PANEL_DATES, default="trade",
                   help="settlement: the panel is dated by settlement (curvefit's residual history)")
    p.add_argument("--tolerance", type=int, default=0, help="business days a nearest panel row may stand in")
    p.add_argument("--residuals", help="curvefit residual history for the auctions' pre and recovery moves")
    p.add_argument("--period", choices=event_ledger.PERIODS, default="auction_week")
    p.add_argument("--anchor", choices=event_ledger.ANCHORS, default="result")
    p.add_argument("--pre", type=int, default=5, help="business days before day 0")
    p.add_argument("--post", type=int, default=10, help="business days after day 0")
    p.add_argument("--radius", type=int, help="business days within which another event's day 0 breaks an event's "
                                              "isolation (default 3, or less to fit --pre and --post)")
    p.add_argument("--exclude-kinds", default="vrr,vrrr,macro_release,redemption",
                   help="kinds that do not break an event's isolation (comma-separated)")
    p.add_argument("--announcement-rule", choices=event_ledger.ANNOUNCEMENT_RULES, default="conservative")
    p.add_argument("--result-rule", choices=event_ledger.RESULT_RULES, default="observed_or_end_of_day")
    p.add_argument("--decision-time", default="20:00", help="HH:MM, India time (UNVERIFIED default 20:00)")
    p.add_argument("--bid-cover", choices=event_ledger.BID_COVER_BASES, default="competitive")
    p.add_argument("--stock-dv01-basis", choices=event_ledger.STOCK_DV01_BASES, default="last_cutoff")
    p.add_argument("--forward-dates", help="comma-separated dates for forward_supply (default: each Friday of "
                                           "the calendars' span)")
    p.add_argument("--horizon-weeks", type=int, default=4)
    p.add_argument("--start", help="first event date written, YYYY-MM-DD (the checks, the stock's yields, forward "
                                   "supply's trailing DV01 and the overlaps still read every event)")
    p.add_argument("--end", help="last event date written, YYYY-MM-DD")
    p.add_argument("--project-calendar", action="store_true",
                   help="windows past 2026: weekdays as business days (no holiday list yet), flagged")
    p.add_argument("--out-dir", required=True)
    p.set_defaults(func=_event_ledger)

    p = sub.add_parser("parse-ratings", help="split and parse rating strings in a CSV column, for auditing a source")
    p.add_argument("input")
    p.add_argument("--column", default="rating")
    p.add_argument("--keep", help="comma-separated columns to carry through")
    p.add_argument("--out", required=True)
    p.set_defaults(func=_parse_ratings)

    args = parser.parse_args(argv)
    return args.func(args)
