"""The daily process for paper trading: after the evening's decision snapshot, replay the paper book, measure its
risk, check the limits, gate tomorrow's orders, back-test the VaR, reconcile with yesterday and write a report.

**The process** (D7.1 of the risk specification of 24 Nov 2026). Paper trading runs the code of a back-test once a
day with today's data as the last day, as of ``d`` at the decision time (20:00, the engine's default; nothing reads the
clock: the report's time is ``as_of``): (1) inputs public by then (marks, NSS fits with their ``public_from``, OIS
quotes, fixings, the RBI store's funding rates, ratings), each read by the engine and the risk layer only from its
time; (2) the paper book replayed by ``returns.run_book`` from inception to ``d`` (the paper trades file holds every
approved order of earlier days as a trade with a blank price, filled at that day's traded mid plus or minus the half
spread; OIS orders as swap trades with a blank fixed rate, filled at the day's par rate): positions, NAV, attribution,
funding, checks, performance, written by ``returns.write_result``; (3) the day's risk (``risk.run_risk``:
sensitivities, VaR and ES, stress, liquidity, funding liquidity); (4) the limits (``limits``: metrics, checks against
the limits in force, the breach log carried from the previous day's report; the drawdown de-risking scale of
``portfolio.drawdown_scale`` and the strategies' stop-loss multiply the DV01, KRD, CS01, VaR and ES levels when a
``derisk`` limit is in force); (5) the pre-trade check of the proposed orders for ``d + 1`` (``limits.PreTradeGate``):
approved and refused with reasons, the approved ones written as tomorrow's paper trades (``orders/approved_trades.csv``
and ``approved_swaps.csv``, which the operator appends to the paper book: never done by the process itself, so a
rerun is idempotent); (6) the day's VaR appended to the history and back-tested (``risk.backtest``); (7) the replay's
positions and NAV of the previous report day compared with the stored ones (a revised mark or curve changes the past:
each difference above ``1e-6`` relative is listed, never absorbed); (8) ``report.md`` and ``manifest.json`` (every
input's path, sha256 and rows, the settings, ``as_of``, the package version, the rows of each output). Steps (4) and
(5) read the ``engine/`` and ``risk/`` files just written (the CSVs round the last digits), so ``cr-strat limits`` and
``cr-strat pre-trade`` on a report's own directories reproduce its limit and gate files byte for byte.

**Files** (D7.2): ``<out_dir>/<YYYY-MM-DD>/`` with ``engine/`` (``returns.RESULT_FILES``), ``risk/``
(``risk.RESULT_FILES`` and ``notes.txt``), ``limits/`` (``metrics``, ``limit_checks``, ``breaches``, ``breach_log``),
``orders/`` (``proposed``, ``gate``, ``approved_trades``, ``approved_swaps``, ``open_orders``), ``backtest/``
(``var_history``, ``backtest``, ``backtest_summary``), ``reconciliation.csv``, ``report.md``, ``manifest.json``.

**Configuration** (``DailyConfig.read``, a TOML file read with the standard library's ``tomllib``): ``fund``,
``access`` (required: the access preset, a question for counsel), ``inception``, ``board_resolution_date``,
``allow_example_limits``, ``limits`` (``example`` or a table's path), ``project_calendar``, ``[inputs]`` (paths
relative to the file: ``marks``, ``nss_params``, ``flows``, ``trades``, ``swaps`` and the optional tables of
``cr-strat returns`` and ``cr-strat risk``, ``spread_matrix`` the peer matrix credit migrations read),
``[risk]`` (methods, horizons, window, observation thresholds, levels, ``stressed``, ``residual``, ``sdl_mode``,
``quantile_rule``, ``gap_rule``, ``max_gap_days``, ``participation``, ``stress_multiplier``: as ``cr-strat risk``'s
options), ``[gate]`` (``cash_buffer_pct``). Unknown keys are refused, naming the closest. The back-test pairs a
strategy's VaR with the P&L of its books (``strategy_books``) as the VaR's scopes are mapped.

**Limits.** Built and tested on synthetic books only; no licensed price has been through it. A library ``Strategy``
runs as ``returns.run_backtest(limits.GatedStrategy(strategy, gate), ...)`` (the same gate); the daily command takes an
orders file. The reconciliation compares the previous report's engine files only (risk tables are recomputed from the
replay). Not built: freezing a report as the official record, intraday checks, an approval workflow for refused orders
(each is listed with its reasons for a person to act on).
"""

from __future__ import annotations

import difflib
import hashlib
import json
import math
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import pandas as pd

from cr_strat import __version__
from cr_strat import limits as lm
from cr_strat import portfolio as pf
from cr_strat import returns as rt
from cr_strat import risk as rk
from cr_strat import stress as st
from cr_strat.calendars import mumbai_settlement_calendar
from cr_strat.ois import as_time

CONFIG_KEYS = ("fund", "access", "inception", "board_resolution_date", "allow_example_limits", "limits",
               "project_calendar", "decision_time", "inputs", "risk", "gate")
INPUT_KEYS = ("marks", "nss_params", "ois_quotes", "fixings", "store", "haircuts", "master", "liquidity",
              "residual_history", "corporate_terms", "corporate_schedule", "corporate_options",
              "corporate_amortisation", "corporate_steps", "ratings", "corporate_floating", "benchmark_fixings",
              "frb_terms", "frb_coupons", "tbills", "issuers", "stock", "strategy_books", "factor_history", "adv",
              "seniority", "proxies", "scenarios", "costs", "auctions", "flows", "trades", "swaps", "spread_matrix",
              "counterparties")
RISK_KEYS = ("methods", "horizons", "window", "min_obs", "min_obs_hard", "stressed", "var_alpha", "es_alpha",
             "cov_method", "ewma_lambda", "participation", "stress_multiplier", "residual", "sdl_mode",
             "quantile_rule", "gap_rule", "max_gap_days")
GATE_KEYS = ("cash_buffer_pct",)
RECONCILIATION_COLUMNS = ("date", "file", "key", "column", "stored", "replayed", "difference")
RECONCILE_RTOL = 1e-6
_POSITION_KEYS = ("book", "isin", "state", "settlement_date")
_POSITION_VALUES = ("face", "clean_mark", "yield_pct", "value", "dv01")
_NAV_KEYS = ("book",)
_NAV_VALUES = ("nav", "pnl", "cash", "bonds", "dv01")


def _closest(value: str, allowed: Sequence[str]) -> str:
    close = difflib.get_close_matches(value, list(allowed), n=1)
    return f" (did you mean {close[0]!r}?)" if close else ""


def _check_keys(table: Mapping, allowed: Sequence[str], where: str) -> None:
    for key in table:
        if key not in allowed:
            raise ValueError(f"{where}: unknown key {key!r}{_closest(key, allowed)} (keys: {', '.join(allowed)})")


@dataclass(frozen=True)
class DailyConfig:
    """The paper fund's configuration (see the module docstring); ``inputs`` are resolved paths."""

    fund: str
    access: str
    inception: date
    inputs: Mapping[str, Path] = field(default_factory=dict)
    board_resolution_date: date | None = None
    allow_example_limits: bool = False
    limits: str | Path | None = None
    project_calendar: bool = False
    decision_time: time | None = None
    risk: Mapping[str, Any] = field(default_factory=dict)
    gate: Mapping[str, Any] = field(default_factory=dict)
    path: Path | None = None
    sha256: str = ""

    @classmethod
    def read(cls, path) -> DailyConfig:
        """Read a TOML configuration; its inputs are relative to its directory."""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"the daily configuration not found: {p}")
        raw = p.read_bytes()
        try:
            data = tomllib.loads(raw.decode("utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"the daily configuration {p} is not valid TOML: {exc}") from None
        return cls.from_mapping(data, base=p.parent, path=p, sha256=hashlib.sha256(raw).hexdigest())

    @classmethod
    def from_mapping(cls, data: Mapping, *, base=".", path=None, sha256: str = "") -> DailyConfig:
        """A configuration from a mapping (the parsed TOML), checked: unknown keys, a missing preset or inception,
        an input that is not there are refused, naming the key."""
        where = f"the daily configuration {path}" if path else "the daily configuration"
        _check_keys(data, CONFIG_KEYS, where)
        root = Path(base)
        access = str(data.get("access", "")).strip()
        if not access:
            raise ValueError(f"{where}: access is required (the access preset: a question for counsel; no default)")
        rt.access_policy(access)
        if "inception" not in data:
            raise ValueError(f"{where}: inception (the paper book's first day) is required")
        inception = rk._date(data["inception"], f"{where}: inception")
        inputs = data.get("inputs", {}) or {}
        if not isinstance(inputs, Mapping):
            raise ValueError(f"{where}: [inputs] must be a table of paths")
        _check_keys(inputs, INPUT_KEYS, f"{where} [inputs]")
        resolved = {}
        for key, value in sorted(inputs.items()):
            if value in (None, ""):
                continue
            target = Path(value) if Path(value).is_absolute() else root / value
            if not target.exists() and key not in ("trades", "swaps"):
                raise FileNotFoundError(f"{where} [inputs] {key}: {target} not found")
            resolved[key] = target
        risk = data.get("risk", {}) or {}
        _check_keys(risk, RISK_KEYS, f"{where} [risk]")
        gate = data.get("gate", {}) or {}
        _check_keys(gate, GATE_KEYS, f"{where} [gate]")
        limits = data.get("limits")
        if limits not in (None, "", "example"):
            limits = Path(limits) if Path(limits).is_absolute() else root / limits
            if not Path(limits).exists():
                raise FileNotFoundError(f"{where}: limits {limits} not found")
        clock = data.get("decision_time")
        when = None
        if clock not in (None, ""):
            try:
                hh, mm = str(clock).split(":")
                when = time(int(hh), int(mm))
            except ValueError:
                raise ValueError(f"{where}: decision_time {clock!r} is not HH:MM") from None
        allow = data.get("allow_example_limits", False)
        project = data.get("project_calendar", False)
        for name, value in (("allow_example_limits", allow), ("project_calendar", project)):
            if not isinstance(value, bool):
                raise ValueError(f"{where}: {name} must be true or false, not {value!r}")
        board = data.get("board_resolution_date")
        return cls(fund=str(data.get("fund", "paper")), access=access, inception=inception, inputs=resolved,
                   board_resolution_date=rk._date(board, f"{where}: board_resolution_date") if board else None,
                   allow_example_limits=allow, limits=limits or None, project_calendar=project, decision_time=when,
                   risk=dict(risk), gate=dict(gate), path=Path(path) if path else None, sha256=sha256)


@dataclass(eq=False)
class DailyResult:
    """One day of the paper process: every frame it wrote, the report and the manifest."""

    day: date
    as_of: datetime
    out: Path
    engine: rt.BookResult
    risk: rk.RiskResult | None
    metrics: pd.DataFrame
    checks: pd.DataFrame
    breach_log: pd.DataFrame
    breaches: pd.DataFrame
    proposed: pd.DataFrame
    gate: pd.DataFrame
    approved: list
    approved_trades: pd.DataFrame
    approved_swaps: pd.DataFrame
    var_history: pd.DataFrame
    backtest: pd.DataFrame
    backtest_summary: pd.DataFrame
    reconciliation: pd.DataFrame
    scales: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    report: str = ""
    manifest: dict = field(default_factory=dict)
    paths: list = field(default_factory=list)
    config: DailyConfig | None = None
    limits: pd.DataFrame | None = None
    counterparty: pd.DataFrame | None = None


# ---------------------------------------------------------------- reading the inputs


def _csv(path) -> pd.DataFrame | None:
    if path is None:
        return None
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def read_fixings(path) -> tuple[dict[date, float], dict[date, datetime] | None]:
    """A fixings file (``date``, ``rate_pct``, optional ``public_from``: a blank cell is FBIL's publication time) as
    ``({date: rate}, {date: public time} or None)``; two rates for one date are refused."""
    from cr_strat.funding import fbil_published_at
    table = _csv(path)
    table.columns = [str(c).strip().lower() for c in table.columns]
    missing = [c for c in ("date", "rate_pct") if c not in table]
    if missing:
        raise ValueError(f"the fixings file {path} lacks {missing} (columns: date, rate_pct, optional public_from)")
    rates, times = {}, ({} if "public_from" in table else None)
    for n, rec in enumerate(table.to_dict("records"), start=2):
        if not str(rec["rate_pct"]).strip():
            continue
        day = rk._date(rec["date"], f"the fixings file line {n}: date")
        rate = rk._num(rec["rate_pct"], f"the fixings file line {n}: rate_pct")
        if day in rates and rates[day] != rate:
            raise ValueError(f"the fixings file gives {day} two rates ({rates[day]} and {rate}, line {n})")
        rates[day] = rate
        if times is not None:
            text = str(rec.get("public_from", "")).strip()
            times[day] = as_time(text, f"the fixings file line {n}: public_from") if text else fbil_published_at(day)
    return rates, times


def ois_curves_from_quotes(path, calendar) -> dict:
    """One OIS curve per date of a long quotes file (``date``, ``tenor``, ``rate_pct``, ``overnight_pct``, optional
    ``public_from``), bootstrapped on its date (``ois.bootstrap_ois``), as ``cr-strat returns`` reads it."""
    from cr_strat.ois import bootstrap_ois
    table = _csv(path)
    table.columns = [str(c).strip().lower() for c in table.columns]
    missing = [c for c in ("date", "tenor", "rate_pct", "overnight_pct") if c not in table]
    if missing:
        raise ValueError(f"the OIS quotes file {path} lacks {missing}")
    table = table[~table.apply(lambda row: all(not str(v).strip() for v in row), axis=1)] if len(table) else table
    out = {}
    for text, group in table.groupby("date", sort=True):
        day = rk._date(text, f"the OIS quotes file's date {text!r}")
        nights = {str(v).strip() for v in group["overnight_pct"]}
        if len(nights) != 1:
            raise ValueError(f"the OIS quotes file gives {day} several overnight fixings: {sorted(nights)}")
        curve = bootstrap_ois(group[["tenor", "rate_pct"]], day, float(nights.pop()), calendar=calendar,
                              source=f"{Path(path).name} {day}")
        public = None
        if "public_from" in group and str(group["public_from"].iloc[0]).strip():
            public = as_time(str(group["public_from"].iloc[0]), f"the OIS quotes' public_from for {day}")
        out[day] = rt.CurveEntry(curve, public)
    return out


def _market(config: DailyConfig, calendar) -> rt.MarketData:
    from cr_strat import rbi_store
    from cr_strat.floating import FRB_COUPONS_CSV, tbill_way_table
    from cr_strat.funding import funding_rates, read_haircuts
    from cr_strat.sovereign import read_master
    inp = config.inputs
    fixings, times = read_fixings(inp["fixings"]) if "fixings" in inp else ({}, None)
    funding = {}
    if "store" in inp:
        daily = rbi_store.read_table(inp["store"], "money_market_daily")
        for source in ("corp_bond_repo", "market_repo", "triparty"):
            try:
                funding[source] = funding_rates(daily, source)
            except ValueError:
                continue
    credit = {k: _csv(inp[k]) for k in ("corporate_terms", "corporate_schedule", "corporate_options",
                                         "corporate_amortisation", "corporate_steps", "ratings",
                                         "corporate_floating", "benchmark_fixings", "frb_terms") if k in inp}
    return rt.MarketData(
        marks=_csv(inp.get("marks")),
        curves=rt.read_nss_params(inp["nss_params"], calendar=calendar) if "nss_params" in inp else {},
        ois_curves=ois_curves_from_quotes(inp["ois_quotes"], calendar) if "ois_quotes" in inp else {},
        fixings=fixings, fixing_times=times, funding=funding,
        haircuts=read_haircuts(inp["haircuts"]) if "haircuts" in inp else None,
        liquidity=_csv(inp.get("liquidity")), master=read_master(inp["master"]) if "master" in inp else None,
        residual_history=_csv(inp.get("residual_history")), stock=_csv(inp.get("stock")),
        auctions=_csv(inp.get("auctions")), frb_coupons=inp.get("frb_coupons", FRB_COUPONS_CSV),
        tbills=tbill_way_table(_csv(inp["tbills"])) if "tbills" in inp else None, **credit)


def _settings(config: DailyConfig) -> rt.EngineSettings:
    from cr_strat.costs import read_cost_table
    kw = {}
    if config.decision_time is not None:
        kw = {"decision_time": config.decision_time, "valuation_time": config.decision_time}
    return rt.EngineSettings(access=rt.access_policy(config.access), project_calendar=config.project_calendar,
                             costs=read_cost_table(overrides=config.inputs["costs"]) if "costs" in config.inputs
                             else None, **kw)


def _risk_settings(config: DailyConfig) -> rk.RiskSettings:
    r = config.risk
    defaults = rk.RiskSettings()
    return rk.RiskSettings(
        methods=tuple(r.get("methods", defaults.methods)), horizons=tuple(int(h) for h in r.get("horizons",
                                                                                              defaults.horizons)),
        window=int(r.get("window", defaults.window)), min_obs=int(r.get("min_obs", defaults.min_obs)),
        min_obs_hard=int(r.get("min_obs_hard", defaults.min_obs_hard)),
        var_alpha=float(r.get("var_alpha", defaults.var_alpha)), es_alpha=float(r.get("es_alpha", defaults.es_alpha)),
        cov_method=str(r.get("cov_method", defaults.cov_method)),
        ewma_lambda=float(r.get("ewma_lambda", defaults.ewma_lambda)),
        windows=("current", "stressed") if r.get("stressed", False) else ("current",),
        residual=str(r["residual"]) if r.get("residual") not in (None, "") else defaults.residual,
        sdl_mode=str(r.get("sdl_mode", defaults.sdl_mode)),
        quantile_rule=str(r.get("quantile_rule", defaults.quantile_rule)),
        gap_rule=str(r.get("gap_rule", defaults.gap_rule)),
        max_gap_days=int(r.get("max_gap_days", defaults.max_gap_days)))


def _previous_dir(root: Path, day: date) -> Path | None:
    best = None
    if not root.exists():
        return None
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            d = date.fromisoformat(child.name)
        except ValueError:
            continue
        if d < day and (best is None or d > best[0]):
            best = (d, child)
    return best[1] if best else None


def _book_file(config: DailyConfig, root: Path, key: str) -> pd.DataFrame | None:
    path = config.inputs.get(key, root / f"{key}.csv")
    if not Path(path).exists():
        return None
    frame = _csv(path)
    return frame if len(frame) else None


# ---------------------------------------------------------------- de-risking, approved orders, reconciliation


def derisk_scales(limits: pd.DataFrame, nav: pd.DataFrame | None, *, as_of, legal_form: str,
                  strategies: Mapping[str, str]) -> dict[tuple[str, str], float]:
    """The de-risking scales of the day (P6.4) when a ``derisk`` limit is in force: the fund's drawdown scale
    (``portfolio.drawdown_scale`` on its NAV index: 0.5 at -3%, 0.25 at -5%, back to 1 above -1.5%, UNVERIFIED) for
    every scope, times a strategy's stop-loss scale (``portfolio.stop_loss_scale``: its 20-day P&L at or below -2% of
    the fund's NAV halves it) for the strategy and its books. Empty without a ``derisk`` limit or a NAV history."""
    live = lm.limits_in_force(limits, as_of=as_of, legal_form=legal_form)
    live = live[live["action"] == "derisk"]
    if not len(live) or nav is None or not len(nav):
        return {}
    day = as_time(as_of, "as_of").date()
    frame = nav.copy()
    frame["_d"] = [rk._date(v, "the NAV date") for v in frame["date"]]
    frame = frame[frame["_d"] <= day]
    books = sorted(b for b in set(frame["book"].astype(str)) if b != "fund")
    rows = frame[frame["book"].astype(str).isin(books)]
    by_day = rows.groupby("_d", sort=True)
    navs = by_day["nav"].apply(lambda s: math.fsum(float(v) for v in s))
    flows = by_day["flow"].apply(lambda s: math.fsum(float(v) for v in s))
    prev, index, values = 0.0, 1.0, []
    for n, f in zip(navs, flows):
        base = prev + f
        ret = (n - prev - f) / base if base > 0 else math.nan
        if math.isfinite(ret):
            index *= 1 + ret
        values.append(index)
        prev = n
    fund_scale = float(pf.drawdown_scale(pd.Series(values, index=navs.index)).iloc[-1]) if values else 1.0
    out: dict[tuple[str, str], float] = {("fund", "fund"): fund_scale}
    base_nav = navs.shift(1).fillna(navs - by_day["pnl"].apply(lambda s: math.fsum(float(v) for v in s)))
    for strategy in sorted({strategies.get(b, b) for b in books}):
        mine = rows[rows["book"].astype(str).map(lambda b: strategies.get(b, b)) == strategy]
        pnl = mine.groupby("_d", sort=True)["pnl"].apply(lambda s: math.fsum(float(v) for v in s))
        pnl = pnl.reindex(navs.index).fillna(0.0)
        stop = float(pf.stop_loss_scale(pnl, base_nav).iloc[-1]) if len(pnl) else 1.0
        out[("strategy", strategy)] = fund_scale * stop
        for b in books:
            if strategies.get(b, b) == strategy:
                out[("book", b)] = fund_scale * stop
    return out


def approved_trades(gate_rows: pd.DataFrame, orders: pd.DataFrame, day, *, calendar=None,
                    kinds: Mapping[str, str] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Tomorrow's paper trades from the gate's approved orders: bond orders as ``TRADE_COLUMNS`` rows of the next
    business day with a blank price (the engine fills them at that day's traded mid plus or minus the half spread;
    an auction bid on its auction day, its price to be filled in from the result), OIS orders as
    ``SWAP_TRADE_COLUMNS`` rows with a blank fixed rate (filled at the day's par rate); trade ids
    ``P<YYYYMMDD>-<seq>``, unless the order set its own id."""
    cal = (calendar if calendar is not None else mumbai_settlement_calendar()).lenient()
    d = rk.as_date(day)
    nxt = cal.add_business_days(d, 1)
    decided = gate_rows.drop_duplicates("seq") if len(gate_rows) else gate_rows
    keep = {int(r["seq"]) for r in decided.to_dict("records") if r["decision"] in ("approved",
                                                                                     "approved_with_warning")}
    trades, swaps = [], []
    for seq, rec in enumerate(orders.to_dict("records"), start=1):
        if seq not in keep:
            continue
        tid = rec.get("order_id") or f"P{d:%Y%m%d}-{seq:03d}"
        note = f"approved by the pre-trade gate on {d} (order {seq})"
        if rec["isin"]:
            auction = rec.get("venue") in ("auction", "auction_nc") and rec.get("auction_date") is not None
            trades.append({"trade_id": tid, "book": rec["book"], "isin": rec["isin"],
                           "kind": (kinds or {}).get(rec["isin"], ""), "side": rec["side"], "face": rec["face"],
                           "price": "", "yield_pct": "", "trade_date": rec["auction_date"] if auction else nxt,
                           "trade_time": "", "settlement_date": "", "venue": rec.get("venue") or "",
                           "mid_price": "", "liquidity": "", "broker": "", "special_repo_pct": "",
                           "notified_cr": "", "coupon_pct": "", "maturity_date": "", "issue_date": "",
                           "first_coupon_date": "", "rating_bucket": "",
                           "note": note + ("; fill in the auction's cut-off price" if auction else "")})
        else:
            swaps.append({"trade_id": tid, "book": rec["book"], "trade_date": nxt, "effective": "",
                          "months": int(rec["months"]), "fixed_rate_pct": "", "notional": rec["face"],
                          "pay_fixed": rec["side"] == "pay", "mid_rate_pct": "", "purpose": rec.get("purpose") or
                          "hedge", "note": note})
    return (pd.DataFrame(trades, columns=list(rt.TRADE_COLUMNS)),
            pd.DataFrame(swaps, columns=list(rt.SWAP_TRADE_COLUMNS)))


def _as_text(frame: pd.DataFrame) -> pd.DataFrame:
    import io
    buffer = io.StringIO()
    frame.to_csv(buffer, index=False, date_format="%Y-%m-%d")
    buffer.seek(0)
    return pd.read_csv(buffer, dtype=str, keep_default_na=False)


def _compare(stored: pd.DataFrame, replayed: pd.DataFrame, keys: Sequence[str], values: Sequence[str], name: str,
             day: date) -> list[dict]:
    rows = []
    def keyed(frame):
        return {tuple(str(r.get(k, "")) for k in keys): r for r in frame.to_dict("records")}
    a, b = keyed(stored), keyed(replayed)
    for key in sorted(set(a) | set(b)):
        label = "|".join(key)
        if key not in a or key not in b:
            rows.append({"date": day, "file": name, "key": label, "column": "row",
                         "stored": "present" if key in a else "absent", "replayed": "present" if key in b else
                         "absent", "difference": math.nan})
            continue
        for col in values:
            x, y = a[key].get(col, ""), b[key].get(col, "")
            try:
                fx, fy = float(x), float(y)
            except (TypeError, ValueError):
                if str(x) != str(y):
                    rows.append({"date": day, "file": name, "key": label, "column": col, "stored": x, "replayed": y,
                                 "difference": math.nan})
                continue
            if not (math.isfinite(fx) and math.isfinite(fy)):
                if not (math.isnan(fx) and math.isnan(fy)) and fx != fy:
                    rows.append({"date": day, "file": name, "key": label, "column": col, "stored": x, "replayed": y,
                                 "difference": math.nan})
                continue
            if abs(fx - fy) > RECONCILE_RTOL * max(abs(fx), abs(fy), 1.0):
                rows.append({"date": day, "file": name, "key": label, "column": col, "stored": x, "replayed": y,
                             "difference": fy - fx})
    return rows


def reconcile(previous_dir, result: rt.BookResult) -> pd.DataFrame:
    """The replay's positions and NAV of the previous report day against the stored ones
    (``RECONCILIATION_COLUMNS``): a row missing on either side, and every value differing by more than ``1e-6``
    relative (a revised mark or curve restates the past: listed, never absorbed). Empty without a previous report."""
    if previous_dir is None:
        return pd.DataFrame(columns=list(RECONCILIATION_COLUMNS))
    prev = Path(previous_dir)
    day = date.fromisoformat(prev.name)
    rows: list[dict] = []
    for name, frame, keys, values in (("positions", result.positions, _POSITION_KEYS, _POSITION_VALUES),
                                      ("nav", result.nav, _NAV_KEYS, _NAV_VALUES)):
        path = prev / "engine" / f"{name}.csv"
        if not path.exists():
            rows.append({"date": day, "file": name, "key": "", "column": "file", "stored": "absent",
                         "replayed": "present", "difference": math.nan})
            continue
        stored = pd.read_csv(path, dtype=str, keep_default_na=False)
        stored = stored[stored["date"] == day.isoformat()]
        replay = _as_text(frame)
        replay = replay[replay["date"] == day.isoformat()]
        rows.extend(_compare(stored, replay, keys, values, name, day))
    return pd.DataFrame(rows, columns=list(RECONCILIATION_COLUMNS))


# ---------------------------------------------------------------- the report and the manifest


def _f(value, digits: int = 2) -> str:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return str(value)
    if math.isnan(x):
        return "n/a"
    if math.isinf(x):
        return "inf" if x > 0 else "-inf"
    if round(x, digits) == 0:
        x = 0.0  # never -0.00
    return f"{x:,.{digits}f}"


def _table(rows: Sequence[Mapping], columns: Sequence[tuple[str, str]]) -> list[str]:
    if not rows:
        return ["(none)", ""]
    out = ["| " + " | ".join(h for h, _ in columns) + " |", "|" + "---|" * len(columns)]
    for r in rows:
        out.append("| " + " | ".join(str(r.get(k, "")).replace("|", "/") for _, k in columns) + " |")
    out.append("")
    return out


def report_markdown(result: DailyResult) -> str:
    """The day's report (D7.2) as plain, deterministic Markdown."""
    cfg = result.config
    lines: list[str] = []
    table = result.limits if result.limits is not None else pd.DataFrame(columns=["policy_status"])
    live = lm.limits_in_force(table, as_of=result.as_of, legal_form=cfg.access) if len(table) else table
    examples = bool(len(live)) and bool((live["policy_status"] == "EXAMPLE").any())
    title = f"# Daily risk report: {cfg.fund}, {result.day}"
    lines.append(f"{title} (EXAMPLE limits)" if examples else title)
    lines.append("")
    if examples:
        lines.append(f"**{lm.EXAMPLE_NOTE}.**")
        lines.append("")
    lines.append(f"As of {result.as_of:%Y-%m-%d %H:%M} (India), access preset `{cfg.access}` (the fund's legal form is "
                 "a question for counsel). Synthetic or paper data unless the inputs say otherwise; every UNVERIFIED "
                 "rule used is listed at the end.")
    lines.append("")
    if len(live):
        counts = live["policy_status"].value_counts()
        lines.append("Limits in force: " + ", ".join(f"{int(counts[s])} {s}" for s in lm.POLICY_STATUSES if s in counts)
                     + " (EXAMPLE rows are not the fund's policy; DRAFT rows warn and are never enforced)")
        lines.append("")
    log = result.breach_log
    hot = log[log["status"].isin(("escalated", "new"))] if len(log) else log
    hot = hot[hot["last_date"] == result.day] if len(hot) else hot
    lines.append("## Escalated and new breaches")
    lines.append("")
    order = {"escalated": 0, "new": 1}
    rows = sorted(hot.to_dict("records"), key=lambda r: (order[r["status"]], r["limit_id"], r["scope"], r["key"]))
    breach_columns = [("limit", "limit_id"), ("scope", "scope"), ("key", "key"), ("status", "status"),
                      ("origin", "origin"), ("first day", "first_date"), ("worst", "worst"), ("hard", "hard"),
                      ("cure due", "cure_due"), ("action", "action")]
    lines += _table([{**r, "worst": _f(r["worst_value"], 4), "hard": _f(r["hard_level"], 4)} for r in rows],
                    breach_columns)
    # the hard breaches still open from an earlier day, so the top of the report shows the fund's whole state
    still = log[(log["status"] == "continuing") & (log["last_date"] == result.day)] if len(log) else log
    still_rows = sorted(still.to_dict("records"), key=lambda r: (r["limit_id"], r["scope"], r["key"]))
    lines.append("Still open from an earlier day (continuing):")
    lines.append("")
    lines += _table([{**r, "worst": _f(r["worst_value"], 4), "hard": _f(r["hard_level"], 4)} for r in still_rows],
                    breach_columns)
    nav = result.engine.nav
    fund = nav[nav["book"] == "fund"] if (nav["book"] == "fund").any() else nav
    fund = fund[fund["date"] <= result.day] if len(fund) else fund
    lines.append("## NAV and P&L")
    lines.append("")
    if len(fund):
        today = fund[fund["date"] == result.day]
        def pnl_since(start: date) -> float:
            mine = fund[(fund["date"] >= start) & (fund["date"] <= result.day)]
            return math.fsum(float(v) for v in mine["pnl"])
        if len(today):
            t = today.iloc[-1]
            lines.append(f"- NAV Rs {_f(t['nav'])}; the day's P&L Rs {_f(t['pnl'])}")
        lines.append(f"- Month to date Rs {_f(pnl_since(result.day.replace(day=1)))}; financial year to date Rs "
                     f"{_f(pnl_since(date(result.day.year if result.day.month >= 4 else result.day.year - 1, 4, 1)))}")
        attr = result.engine.attribution
        attr = attr[(attr["date"] == result.day) & ~attr["memo"].astype(bool)] if len(attr) else attr
        if len(attr):
            sums = attr.groupby("line", sort=True)["amount"].apply(lambda s: math.fsum(float(v) for v in s))
            lines.append("- The day's attribution: " + "; ".join(f"{k} Rs {_f(v)}" for k, v in sums.items()))
    else:
        lines.append("(no NAV on the day)")
    lines.append("")
    rr = result.risk
    lines.append("## Rates: DV01 and key rates")
    lines.append("")
    if rr is not None:
        summ = rr.risk_summary
        tot = summ[(summ["scope_type"] == "fund") & (summ["dimension"] == "total")]
        if len(tot):
            r0 = tot.iloc[0]
            lines.append(f"- Yield DV01 Rs {_f(r0['dv01_yield'])} per bp ({_f(r0['dv01_years'], 4)} years of NAV); "
                         f"curve DV01 Rs {_f(r0['dv01_curve'])}")
        kr = rr.key_rates[rr.key_rates["scope_type"] == "fund"]
        lines += [""] + _table([{"curve": r["curve"], "tenor": rk.tenor_label(r["tenor"]), "krd": _f(r["krd"]),
                                 "years": _f(r["krd_years"], 4)} for r in kr.to_dict("records")],
                               [("curve", "curve"), ("tenor", "tenor"), ("KRD Rs per bp", "krd"),
                                ("years of NAV", "years")])
    else:
        lines += ["(no position on the day)", ""]
    lines.append("## Credit")
    lines.append("")
    if rr is not None and len(rr.credit):
        cr = rr.credit[rr.credit["scope_type"] == "fund"]
        by_rating = cr[cr["dimension"] == "rating_bucket"]
        lines += _table([{"key": r["key"], "cs01": _f(r["cs01"]), "mv": _f(r["market_value"]),
                          "pct": _f(r["pct_nav"], 4), "jtd": _f(r["jtd"])} for r in by_rating.to_dict("records")],
                        [("rating", "key"), ("CS01 Rs per bp", "cs01"), ("market value", "mv"), ("% NAV", "pct"),
                         ("JTD", "jtd")])
        for dim in ("issuer", "group", "sector"):
            top = cr[cr["dimension"] == dim].sort_values(["market_value", "key"], ascending=[False, True],
                                                         kind="mergesort").head(5)
            lines.append(f"Five largest by {dim}:")
            lines.append("")
            lines += _table([{"key": r["key"], "mv": _f(r["market_value"]), "pct": _f(r["pct_nav"], 4),
                              "jtd": _f(r["jtd"])} for r in top.to_dict("records")],
                            [(dim, "key"), ("market value", "mv"), ("% NAV", "pct"), ("JTD", "jtd")])
    else:
        lines += ["(no credit position)", ""]
    exposure = result.counterparty
    fund_cpty = exposure[exposure["scope_type"] == "fund"] if exposure is not None and len(exposure) else None
    if fund_cpty is not None and len(fund_cpty):
        lines.append("OIS counterparty exposure (current exposure method: gross positive MTM plus the add-on; CCPs "
                     "zero):")
        lines.append("")
        lines += _table([{**r, "ce": _f(r["current_exposure"]), "pfe": _f(r["pfe"]), "eq": _f(r["credit_equivalent"]),
                          "pc": _f(r["pct_nav"], 4)} for r in fund_cpty.to_dict("records")],
                        [("counterparty", "counterparty"), ("type", "counterparty_type"), ("swaps", "swaps"),
                         ("current exposure", "ce"), ("add-on", "pfe"), ("credit equivalent", "eq"), ("% NAV", "pc"),
                         ("flags", "flags")])
    lines.append("## VaR and ES")
    lines.append("")
    if rr is not None:
        var = rr.var[(rr.var["scope_type"] == "fund") & (rr.var["alpha_var"] == rr.book.settings.var_alpha)]
        lines += _table([{**r, "v": _f(r["var"]), "e": _f(r["es"]), "c": _f(r["coverage"], 4)}
                         for r in var.to_dict("records")],
                        [("method", "method"), ("days", "horizon_days"), ("window", "window"), ("obs", "n_obs"),
                         (f"VaR {rr.book.settings.var_alpha:g}", "v"), (f"ES {rr.book.settings.es_alpha:g}", "e"),
                         ("coverage", "c"), ("status", "status")])
        lines.append("The VaR leaves out: " + "; ".join(rk.VAR_LEAVES_OUT) + ".")
        lines.append("")
    else:
        lines += ["(no VaR: no position)", ""]
    lines.append("## Stress (fund, worst first)")
    lines.append("")
    if rr is not None:
        s = rr.stress[(rr.stress["scope_type"] == "fund") & (rr.stress["component"] == "total")]
        s = s.assign(_p=s["pnl"].fillna(math.inf)).sort_values(["_p", "scenario_id"], kind="mergesort")
        lines += _table([{**r, "p": _f(r["pnl"]), "pc": _f(r["pnl_pct_nav"], 4),
                          "f": str(r["flags"])[:80]} for r in s.to_dict("records")],
                        [("scenario", "scenario_id"), ("family", "family"), ("P&L", "p"), ("% NAV", "pc"),
                         ("status", "status"), ("flags", "f")])
    else:
        lines += ["(none)", ""]
    lines.append("## Liquidity and funding")
    lines.append("")
    if rr is not None:
        ls = rr.liquidity_summary[rr.liquidity_summary["scope_type"] == "fund"]
        lines += _table([{"s1": _f(r["share_1d"] * 100, 2), "s5": _f(r["share_5d"] * 100, 2),
                          "s20": _f(r["share_20d"] * 100, 2), "max": _f(r["max_days"]),
                          "cost": _f(r["liquidation_cost"]), "stressed": _f(r["stressed_cost"])}
                         for r in ls.to_dict("records")],
                        [("% saleable 1d", "s1"), ("5d", "s5"), ("20d", "s20"), ("longest days", "max"),
                         ("exit cost", "cost"), ("stressed", "stressed")])
        fl = rr.funding_liquidity
        if len(fl) and not fl["applicable"].astype(bool).any():
            lines.append(f"Funding liquidity: not applicable (`{cfg.access}` does not borrow: no repo or TREPS, a "
                         "question for counsel). Settlement cover (cash over the purchases still to settle) and the "
                         "share of NAV in cash, T-bills, CMBs and CP, by book:")
            lines.append("")
            lines += _table([{"book": r["book"], "p": _f(r["pending_purchases"]), "c": _f(r["settlement_cover"], 4),
                              "l": _f(r["liquid_asset_share"] * 100, 2)} for r in fl.to_dict("records")],
                            [("book", "book"), ("purchases to settle", "p"), ("settlement cover", "c"),
                             ("% NAV in cash, T-bills, CP", "l")])
        elif len(fl):
            base = fl[fl["scenario_id"].astype(str) == ""]
            lines += _table([{**r, "b": _f(r["borrowed"]), "a1": _f(r["alm_1_7d"], 4),
                              "l": _f(r["liquid_asset_share"] * 100, 2)} for r in base.to_dict("records")],
                            [("book", "book"), ("borrowed", "b"), ("ALM 1-7d", "a1"),
                             ("% NAV in cash, T-bills, CP", "l")])
        lines.append("")
    lines.append("## Limits")
    lines.append("")
    checks = result.checks
    lines += _table([{**r, "v": _f(r["value"], 4), "h": _f(r["hard_level"], 4), "u": _f(r["utilisation"], 4),
                      "scope_k": f"{r['scope']}" + (f" [{r['key']}]" if str(r['key']) else "")}
                     for r in checks.to_dict("records")],
                    [("limit", "limit_id"), ("metric", "metric"), ("scope", "scope_k"), ("value", "v"),
                     ("unit", "unit"), ("hard", "h"), ("utilisation", "u"), ("status", "status"),
                     ("policy", "policy_status")])
    if result.scales:
        lines.append("De-risking scales: " + "; ".join(f"{t} {s} {v:g}" for (t, s), v in sorted(result.scales.items())))
        lines.append("")
    lines.append("## Tomorrow's orders")
    lines.append("")
    g = result.gate
    lines += _table([{**r, "f": _f(r["face"], 0), "why": str(r["reason"])} for r in g.to_dict("records")],
                    [("seq", "seq"), ("book", "book"), ("isin", "isin"), ("side", "side"), ("face", "f"),
                     ("decision", "decision"), ("limit", "limit_id"), ("reason", "why")])
    lines.append("## Back-test")
    lines.append("")
    bts = result.backtest_summary
    lines += _table([{**r, "k": _f(r["kupiec_p"], 4), "c": _f(r["christoffersen_p"], 4)}
                     for r in bts.to_dict("records")],
                    [("type", "scope_type"), ("scope", "scope"), ("method", "method"),
                     ("observations", "observations"), ("exceptions", "exceptions"), ("Kupiec p", "k"),
                     ("Christoffersen p", "c"), ("zone", "zone")])
    lines.append("## Reconciliation with the previous report")
    lines.append("")
    rec = result.reconciliation
    lines += _table(rec.to_dict("records"), [("file", "file"), ("key", "key"), ("column", "column"),
                                             ("stored", "stored"), ("replayed", "replayed")]) if len(rec) else \
        ["(no difference)" if result.notes.count("no previous report") == 0 else "(no previous report)", ""]
    lines.append("## Data quality")
    lines.append("")
    quality = []
    if rr is not None:
        flags: dict[str, int] = {}
        for text in rr.position_risk["flags"].astype(str):
            for f in [x for x in text.split(";") if x]:
                flags[f] = flags.get(f, 0) + 1
        quality += [f"- {n} position(s) flagged {f}" for f, n in sorted(flags.items())]
        sources = rr.position_risk["mark_source"].astype(str).value_counts()
        quality += [f"- {int(n)} position(s) on {s} marks" for s, n in sorted(sources.items()) if s in ("model",
                                                                                                         "stale")]
        statuses = rr.var["status"].value_counts()
        quality += [f"- {int(n)} VaR row(s) {s}" for s, n in sorted(statuses.items()) if s != "ok"]
        quality += [f"- {n}" for n in rr.history.notes]
    quality += [f"- {n}" for n in result.notes]
    lines += quality or ["(nothing flagged)"]
    lines.append("")
    lines.append("## UNVERIFIED rules used")
    lines.append("")
    derisk = [r for r in pf.UNVERIFIED_RULES if r.startswith("drawdown scale")]  # the only portfolio rule used
    lines += [f"- {r}" for r in (*rk.UNVERIFIED_RULES, *lm.UNVERIFIED_RULES, *derisk)]
    lines.append("")
    return "\n".join(lines)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _rows(path: Path) -> int | None:
    if path.is_dir() or path.suffix.lower() != ".csv":
        return None
    with open(path, "rb") as handle:
        return max(sum(1 for _ in handle) - 1, 0)


def _rel(path: Path, base: Path | None) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve())) if base is not None else str(path)
    except ValueError:
        return str(path)


def manifest(config: DailyConfig, inputs: Mapping[str, Path], outputs: Sequence[Path], as_of: datetime, *,
             out: Path, settings: Mapping | None = None) -> dict:
    """The run's manifest: the configuration's path and sha256, every input (path relative to the configuration,
    sha256, rows), the settings, ``as_of``, the package version and each output's rows (paths relative to ``out``)."""
    base = config.path.parent if config.path is not None else None
    ins = {}
    for key, path in sorted(inputs.items()):
        p = Path(path)
        if p.is_dir():
            ins[key] = {"path": _rel(p, base), "sha256": "", "rows": None, "note": "a directory (the store)"}
        elif p.exists():
            ins[key] = {"path": _rel(p, base), "sha256": _sha256(p), "rows": _rows(p)}
    outs = {_rel(Path(p), out): {"rows": _rows(Path(p))} for p in sorted(outputs, key=str)}
    return {"fund": config.fund, "as_of": as_of.isoformat(timespec="minutes"), "access": config.access,
            "config": {"path": _rel(config.path, base) if config.path else "", "sha256": config.sha256},
            "inputs": ins, "settings": dict(settings or {}), "package": {"name": "cr-strat", "version": __version__},
            "outputs": outs}


# ---------------------------------------------------------------- the daily run


def _write(frame: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, date_format="%Y-%m-%d")
    return path


def run_daily(config: DailyConfig, day, *, orders=None, out_dir) -> DailyResult:
    """One day of the paper process (D7.1) for ``config`` on business day ``day``, with ``orders``
    (``limits.ORDER_COLUMNS``: a frame or a CSV path; None: no order proposed) checked for the next day, writing
    ``<out_dir>/<day>/`` (D7.2). Raises ``ValueError`` on bad input (naming the file, key or row); the gate's refusal
    to run on EXAMPLE limits without ``allow_example_limits`` is raised before anything is written."""
    root = Path(out_dir)
    d = rk.as_date(day)
    settings = _settings(config)
    cal = (settings.calendar if settings.calendar is not None else mumbai_settlement_calendar())
    cal = cal.lenient() if config.project_calendar else cal
    if not cal.is_business_day(d):
        raise ValueError(f"{d} is not a Mumbai business day: the paper process runs on business days")
    if d < config.inception:
        raise ValueError(f"{d} is before the paper book's inception {config.inception}")
    as_of = datetime.combine(d, settings.decision_time)
    market = _market(config, cal)
    trades, swaps, flows = (_book_file(config, root, k) for k in ("trades", "swaps", "flows"))
    user = lm.example_limits() if config.limits in (None, "example") else lm.read_limits(config.limits)
    table = lm.monitoring_limits(user)  # every preset's legal-form rows: the other form's read not_applicable
    proposed = lm.read_orders(orders) if orders is not None else pd.DataFrame(columns=list(lm.ORDER_COLUMNS))
    risk_settings = _risk_settings(config)
    ctx = rk.RiskContext(
        market=market, settings=settings, risk=risk_settings, as_of=as_of, swaps=swaps, terms=trades,
        issuers=_csv(config.inputs.get("issuers")), strategy_books=_csv(config.inputs.get("strategy_books")),
        factor_history=_csv(config.inputs.get("factor_history")), proxies=_csv(config.inputs.get("proxies")),
        adv=_csv(config.inputs.get("adv")), seniority=_csv(config.inputs.get("seniority")),
        nss_table=market.curves or None, spread_matrix=_csv(config.inputs.get("spread_matrix")),
        counterparties=_csv(config.inputs.get("counterparties")))
    gate = lm.PreTradeGate(user, ctx, allow_example_limits=config.allow_example_limits,
                           cash_buffer_pct=float(config.gate.get("cash_buffer_pct", lm.CASH_BUFFER_PCT))) \
        if len(proposed) else None
    if trades is None and flows is None:
        raise ValueError("the paper book has no trades and no capital flows: give [inputs] flows (the capital)")
    engine = rt.run_book(trades, swaps, flows, market=market, settings=settings, start=config.inception, end=d)
    out = root / d.isoformat()
    paths = list(rt.write_result(engine, out / "engine"))
    ctx = lm.with_cost_table(replace(ctx, nav=engine.nav, funding=engine.funding))
    notes: list[str] = []
    day_positions = engine.positions[engine.positions["date"] == d] if len(engine.positions) else engine.positions
    rr = None
    scenarios = st.read_scenarios(as_of=as_of, extend=config.inputs.get("scenarios"))
    if len(day_positions):
        rr = rk.run_risk(day_positions, ctx, day=d, scenarios=scenarios,
                         participation=config.risk.get("participation"),
                         stress_multiplier=config.risk.get("stress_multiplier"))
        paths += rk.write_risk(rr, out / "risk")
    else:
        notes.append(f"no position on {d}: no risk tables")
    strategies = rk._strategy_map(ctx.strategy_books, sorted(set(engine.nav["book"].astype(str)) - {"fund"}), d)
    scales = derisk_scales(table, engine.nav, as_of=as_of, legal_form=config.access, strategies=strategies)
    # the limits read the files just written, so cr-strat limits and pre-trade on this report's engine/ and risk/
    # directories reproduce its limit and gate files byte for byte (the CSVs round the last digits)
    metrics = lm.limit_metrics(out / "risk" if rr is not None else {}, out / "engine", ctx, limits=table, day=d,
                               board_resolution_date=config.board_resolution_date)
    checks = lm.check_limits(metrics, table, as_of=as_of, legal_form=settings.access, scales=scales)
    prev = _previous_dir(root, d)
    if prev is None:
        notes.append("no previous report")
    log = lm.update_breach_log(prev / "limits" / "breach_log.csv" if prev else None, checks, as_of=as_of,
                               calendar=cal, fills=engine.fills, limits=table, strategies=strategies)
    breaches = lm.breaches_of_day(log, d)
    paths += lm.write_limits(metrics, checks, breaches, out / "limits", breach_log=log)
    # the OIS counterparty exposure (current exposure method) of the fund and each book, for the report (no new file:
    # the day's set of files is fixed; cr-strat limits gives the metric to any limit row that asks for it)
    fund_nav = engine.nav[(engine.nav["book"] == "fund") & (engine.nav["date"] == d)]["nav"]
    books = sorted(set(engine.nav["book"].astype(str)) - {"fund"})
    exposure = lm.counterparty_exposure(
        rr.position_risk.to_dict("records") if rr is not None else [], d, ctx.counterparties,
        nav={("fund", "fund"): float(fund_nav.iloc[0])} if len(fund_nav) else None,
        scopes=(("fund", "fund"), *(("book", b) for b in books)), strategies=strategies)
    if gate is not None:
        state = lm.state_from_result(out / "engine", d, replace(ctx, nav=None, funding=None), scenarios=scenarios,
                                     board_resolution_date=config.board_resolution_date)
        approved, rows = gate.check(state, lm.orders_from_frame(proposed), scales=scales)
        notes.extend(state.notes)
    else:
        approved, rows = [], []
    gate_frame = lm.gate_frame(rows)
    kinds = {}
    if rr is not None:
        kinds = {p.isin: p.kind for p in rr.book.positions}
    for rec in proposed.to_dict("records"):
        if rec["isin"] and rec["isin"] not in kinds:
            try:
                kinds[rec["isin"]] = rt.instruments_as_known(market, settings, [rec["isin"]], as_of,
                                                             terms=trades)[rec["isin"]].kind
            except (ValueError, KeyError, TypeError):
                kinds[rec["isin"]] = ""
    trades_next, swaps_next = approved_trades(gate_frame, proposed, d, calendar=cal, kinds=kinds)
    kept = {int(r["seq"]) for r in rows if r["decision"] != "refused"}
    queued = proposed.iloc[[i - 1 for i in range(1, len(proposed) + 1) if i in kept]].reset_index(drop=True) \
        if len(proposed) else proposed
    for name, frame in (("proposed", proposed), ("gate", gate_frame), ("approved_trades", trades_next),
                        ("approved_swaps", swaps_next), ("open_orders", queued)):
        paths.append(_write(frame, out / "orders" / f"{name}.csv"))
    history_prev = prev / "backtest" / "var_history.csv" if prev and (prev / "backtest" / "var_history.csv").exists() \
        else None
    if rr is not None:
        history = rk.append_var_history(history_prev, rr.var)
    elif history_prev is not None:
        history = pd.read_csv(history_prev, dtype=str, keep_default_na=False)
    else:
        history = pd.DataFrame(columns=list(rk.VAR_HISTORY_COLUMNS))
    paths.append(_write(history, out / "backtest" / "var_history.csv"))
    if len(history):
        # a strategy's P&L is its books' (strategy_books, as mapped on the VaR's day), as the VaR's scopes are
        bt, bts = rk.backtest(history, engine.nav, engine.attribution, calendar=cal, strategy_books=ctx.strategy_books)
    else:
        bt = pd.DataFrame(columns=list(rk.BACKTEST_COLUMNS))
        bts = pd.DataFrame(columns=list(rk.BACKTEST_SUMMARY_COLUMNS))
    paths.append(_write(bt, out / "backtest" / "backtest.csv"))
    paths.append(_write(bts, out / "backtest" / "backtest_summary.csv"))
    reconciliation = reconcile(prev, engine)
    paths.append(_write(reconciliation, out / "reconciliation.csv"))
    result = DailyResult(day=d, as_of=as_of, out=out, engine=engine, risk=rr, metrics=metrics, checks=checks,
                         breach_log=log, breaches=breaches, proposed=proposed, gate=gate_frame, approved=approved,
                         approved_trades=trades_next, approved_swaps=swaps_next, var_history=history,
                         backtest=bt, backtest_summary=bts, reconciliation=reconciliation, scales=scales,
                         notes=notes, config=config, limits=table, counterparty=exposure)
    result.report = report_markdown(result)
    report_path = out / "report.md"
    report_path.write_text(result.report, encoding="utf-8")
    paths.append(report_path)
    used = dict(config.inputs)
    if orders is not None and not isinstance(orders, pd.DataFrame):
        used["orders"] = Path(orders)
    if config.limits not in (None, "example"):
        used["limits"] = Path(config.limits)  # the fund's limit table, hashed like every other input
    result.manifest = manifest(config, used, paths, as_of, out=out,
                               settings={"risk": {k: (list(v) if isinstance(v, tuple) else v) for k, v in
                                                  sorted(config.risk.items())},
                                         "gate": dict(sorted(config.gate.items())),
                                         "allow_example_limits": config.allow_example_limits,
                                         "limits": "example" if config.limits in (None, "example") else
                                         _rel(Path(config.limits), config.path.parent if config.path else None),
                                         "board_resolution_date": config.board_resolution_date.isoformat()
                                         if config.board_resolution_date else ""})
    manifest_path = out / "manifest.json"
    manifest_path.write_text(json.dumps(result.manifest, indent=2, sort_keys=True, default=str) + "\n",
                             encoding="utf-8")
    result.paths = [*paths, manifest_path]
    return result


def summary_lines(result: DailyResult) -> list[str]:
    """A short text summary of the day for the terminal."""
    lines = []
    live = lm.limits_in_force(result.limits, as_of=result.as_of, legal_form=result.config.access) \
        if result.limits is not None else pd.DataFrame(columns=["policy_status"])
    if len(live) and (live["policy_status"] == "EXAMPLE").any():
        lines.append(lm.EXAMPLE_NOTE)
    nav = result.engine.nav
    fund = nav[(nav["book"] == "fund") | (nav["book"].nunique() == 1)]
    today = fund[fund["date"] == result.day]
    if len(today):
        lines.append(f"{result.day}: NAV Rs {today['nav'].iloc[-1]:,.2f}, P&L Rs {today['pnl'].iloc[-1]:,.2f}")
    if result.risk is not None:
        v = result.risk.var
        row = v[(v["scope_type"] == "fund") & (v["horizon_days"] == 1) & (v["window"] == "current")
                & (v["alpha_var"] == result.risk.book.settings.var_alpha)]
        for rec in row.to_dict("records"):
            lines.append(f"fund {rec['method']} 1d VaR {rec['alpha_var']:.1%} Rs {rec['var']:,.2f} ({rec['status']})")
    counts = result.checks["status"].value_counts()
    lines.append("limits: " + ", ".join(f"{int(counts.get(s, 0))} {s}" for s in lm.CHECK_STATUSES))
    open_ = result.breaches[result.breaches["status"].isin(lm.OPEN_BREACHES)] if len(result.breaches) else \
        result.breaches
    for rec in open_.to_dict("records"):
        lines.append(f"breach {rec['limit_id']} {rec['scope_type']} {rec['scope']} {rec['key']}: {rec['status']} "
                     f"({rec['origin']}), cure due {rec['cure_due']}")
    decided = result.gate.drop_duplicates("seq") if len(result.gate) else result.gate
    if len(decided):
        dec = decided["decision"].value_counts()
        lines.append("orders: " + ", ".join(f"{int(dec.get(s, 0))} {s}" for s in lm.DECISIONS))
        refused = result.gate[result.gate["decision"] == "refused"]
        for rec in refused.to_dict("records"):
            if not str(rec["reason"]).startswith(lm.WARNING_ONLY):
                lines.append(f"refused order {rec['seq']} ({rec['book']} {rec['side']} {rec['isin']}): "
                             f"{rec['reason']}")
        warned = int(refused["reason"].astype(str).str.startswith(lm.WARNING_ONLY).sum())
        if warned:
            lines.append(f"(and {warned} warning(s) on refused orders: orders/gate.csv)")
    if len(result.reconciliation):
        lines.append(f"reconciliation: {len(result.reconciliation)} difference(s) with the previous report")
    return lines
