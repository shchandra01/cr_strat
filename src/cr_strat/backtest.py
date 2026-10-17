"""Daily backtest engine for corporate bond strategies.

On each business day (the benchmark curve's dates) the engine:

1. accrues interest on cash at a supplied overnight rate series (TREPS or
   MIBOR), a fixed rate, or by default the curve's shortest tenor (negative
   cash pays ``borrow_spread_bps`` more);
2. collects coupons and redemptions and revalues every holding at its mark
   (``MarketData.marks``), splitting each position's P&L into
   - ``income``: coupon accrual and coupons received,
   - ``roll``: the price change from time passing at an unchanged yield
     (pull to par, and the redemption itself at maturity),
   - ``rates``: modified duration times the change in the benchmark yield at
     the bond's tenor (this includes rolling down the benchmark curve),
   - ``spread``: the rest of the price change from the yield move, i.e. the
     spread change plus convexity;
3. works live orders, sells first and then buys within available cash, filling
   them as the ``ExecutionModel`` allows. The gap between fill price and mark,
   plus stamp duty on buys, is ``execution`` P&L, always a cost;
4. on rebalance dates, asks the strategy for target weights from the market as
   of that close, applies position limits, and turns the targets into orders
   that go live ``lag_days`` later.

The day's NAV change equals the sum of the components plus cash interest; the
tests check this to the rupee.

Each position also carries ``funding``: minus the benchmark yield at its tenor
on its value, the carry of a duration-matched risk-free bond. ``total -
rates + funding`` is then the position's credit excess return: spread carry,
spread change and costs. ``nav_hedged`` swaps every bond's return for that
excess plus the cash rate on the bond's value (``hedge_cash``), as a costless
total-return swap against a duration-matched risk-free bond would. Its excess
over cash is the strategy's pure credit return. The overlay has no cost, basis
or margin: it isolates what the strategy earns from credit, not what a hedged
account would have netted.

The run ends with positions still open, marked to model.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from cr_strat.execution import ExecutionModel
from cr_strat.market import MarketData
from cr_strat.strategies import Context, Strategy, prepare_events

COMPONENTS = ("income", "roll", "rates", "spread", "execution")
NAV_COLUMNS = (
    "date", "nav", "nav_hedged", "cash", "invested", "gross", "n_positions", "pnl", "pnl_hedged",
    *(f"pnl_{c}" for c in COMPONENTS), "pnl_cash", "pnl_funding", "pnl_hedge_cash", "cash_rate_pct", "traded_inr",
    "cost_inr", "mod_duration", "dv01_inr", "spread_bps", "yield_pct", "stale_share", "avg_mark_age_days",
    "rebalance",
)
POSITION_COLUMNS = (
    "date", "isin", "issuer_id", "sector", "rating_bucket", "face", "dirty", "value", "weight", "yield_pct",
    "spread_bps", "benchmark_yield_pct", "mod_duration", "dv01_inr", "tenor_years", "mark_age_days",
    "mark_source", "entry_date", *(f"pnl_{c}" for c in COMPONENTS), "pnl_funding", "closed",
)
FILL_COLUMNS = (
    "date", "order_id", "isin", "issuer_id", "side", "face", "mark_yield_pct", "fill_yield_pct", "cost_bps",
    "fill_clean", "fill_dirty", "value_inr", "cost_inr", "participation", "on_trade_day", "rating_bucket",
)
ORDER_COLUMNS = (
    "order_id", "created", "live_from", "isin", "issuer_id", "start_face", "target_face", "filled_face",
    "n_fills", "status", "closed",
)


@dataclass
class BacktestConfig:
    start: str | None = None
    end: str | None = None
    initial_capital_inr: float = 1e9
    cash_rate_pct: float | None = None
    borrow_spread_bps: float = 50.0
    max_gross: float = 1.0
    max_bond_weight: float = 0.10
    max_issuer_weight: float = 0.10
    drift_tolerance: float = 0.25
    min_trade_inr: float = 1e6
    lot_face_inr: float = 1e5
    stale_days: int = 30
    disclosure_lag_days: int = 10


@dataclass
class BacktestResult:
    strategy: str
    params: dict
    config: BacktestConfig
    execution: ExecutionModel
    nav: pd.DataFrame
    positions: pd.DataFrame
    fills: pd.DataFrame
    orders: pd.DataFrame
    notes: list[str] = field(default_factory=list)


@dataclass
class _Order:
    order_id: int
    created: pd.Timestamp
    live_idx: int
    expiry_idx: int
    isin: str
    issuer_id: str
    start_face: float
    target_face: float
    filled_face: float = 0.0
    n_fills: int = 0


_FREQ_ALIASES = {"DAILY": "D", "WEEKLY": "W-FRI", "W": "W-FRI", "MONTHLY": "M", "ME": "M", "BM": "M", "BME": "M",
                 "QUARTERLY": "Q", "QE": "Q", "BQ": "Q", "BQE": "Q"}


def rebalance_dates(dates: pd.DatetimeIndex, freq: str) -> set[pd.Timestamp]:
    """Last calendar date in each period of ``freq``: "D", "W-FRI" (any W-xxx), "M" or "Q"."""
    freq = _FREQ_ALIASES.get(str(freq).upper(), freq)
    if freq.upper() == "D":
        return set(dates)
    try:
        periods = dates.to_period(freq)
    except ValueError:
        raise ValueError(f"rebalance must be D, W-FRI (or another W-xxx), M or Q; got {freq!r}") from None
    return set(pd.Series(dates, index=periods).groupby(level=0).max())


def read_cash_rates(source) -> pd.Series:
    """Cash rate CSV with ``date`` and ``rate_pct`` (e.g. TREPS weighted average, MIBOR), as a Series."""
    if isinstance(source, pd.Series):
        return source
    df = pd.read_csv(source)
    df.columns = [c.strip().lower() for c in df.columns]
    if not {"date", "rate_pct"} <= set(df.columns):
        raise ValueError("cash rate file needs columns date, rate_pct")
    return pd.Series(pd.to_numeric(df["rate_pct"], errors="coerce").to_numpy(),
                     index=pd.to_datetime(df["date"], format="ISO8601")).dropna()


def _frame(source) -> pd.DataFrame | None:
    """Events or ownership as given: a DataFrame, a CSV path, or None."""
    if source is None or isinstance(source, pd.DataFrame):
        return source
    return pd.read_csv(Path(source), dtype=str)


class _Engine:
    def __init__(self, market: MarketData, strategy: Strategy, config: BacktestConfig,
                 execution: ExecutionModel, events: pd.DataFrame, cash_rates: pd.Series | None = None):
        self.market, self.strategy, self.cfg, self.ex, self.events = market, strategy, config, execution, events
        cal = market.curve.dates
        first = market.panel["date"].min()
        start = max(pd.Timestamp(config.start), first) if config.start else first
        end = pd.Timestamp(config.end) if config.end else cal.max()
        self.dates = cal[(cal >= start) & (cal <= end)]
        if len(self.dates) < 2:
            raise ValueError(f"fewer than two curve dates between {start.date()} and {end.date()}")
        self.rebalance = rebalance_dates(self.dates, strategy.rebalance)
        self.cash_rates = self._cash_rates(cash_rates)
        self.cash = float(config.initial_capital_inr)
        self.book: dict[str, dict] = {}
        self.orders: dict[str, _Order] = {}
        self.order_log: list[dict] = []
        self.fills: list[dict] = []
        self.nav_rows: list[dict] = []
        self.pos_rows: list[dict] = []
        self.notes: list[str] = []
        self.dropped: dict[str, set] = {}
        self.next_order_id = 0
        self.sectors = market.securities["sector"].to_dict() if "sector" in market.securities else {}
        self._event_dates = events["date"].to_numpy(dtype="datetime64[ns]")

    def _cash_rates(self, series: pd.Series | None) -> np.ndarray:
        if series is not None:
            s = pd.Series(series.to_numpy(dtype=float), index=pd.to_datetime(series.index)).sort_index()
            return s.reindex(s.index.union(self.dates)).ffill().reindex(self.dates).bfill().to_numpy()
        if self.cfg.cash_rate_pct is not None:
            return np.full(len(self.dates), float(self.cfg.cash_rate_pct))
        curve = self.market.curve
        rates = curve.yields(pd.Series(self.dates), np.full(len(self.dates), curve.shortest_tenor),
                             to_basis="annual")
        return pd.Series(rates).ffill().bfill().fillna(0.0).to_numpy()

    # -- daily steps ---------------------------------------------------------------------------
    def _revalue(self, t: pd.Timestamp, prev: pd.Timestamp, cash_rate: float, day: dict) -> tuple[dict, dict]:
        pnl = dict.fromkeys([*COMPONENTS, "funding", "hedge_cash"], 0.0)
        closed = {}
        isins = list(self.book)
        if not isins:
            return pnl, closed
        dt = (t - prev).days
        mk = self.market.marks(isins, t)
        old = {k: np.array([self.book[i][k] for i in isins], dtype=float)
               for k in ("face", "yield_pct", "benchmark_yield_pct", "dirty", "accrued", "mod_duration")}
        y_t = mk["yield_pct"].to_numpy(dtype=float)
        y_t = np.where(np.isfinite(y_t), y_t, old["yield_pct"])
        b_t = mk["benchmark_yield_pct"].to_numpy(dtype=float)
        at_old_yield = self.market.price(isins, t, old["yield_pct"])
        now = self.market.price(isins, t, y_t)
        cpn, prin = self.market.cashflows.cash_flows(isins, prev, t)
        scale = old["face"] / 100
        income = (now["accrued"] - old["accrued"] + cpn)
        roll = at_old_yield["dirty"] + cpn + prin - old["dirty"] - income
        db = b_t - old["benchmark_yield_pct"]
        rates = np.where(np.isfinite(db), -old["mod_duration"] * old["dirty"] * db / 100, 0.0)
        spread = now["dirty"] - at_old_yield["dirty"] - rates
        matched_rate = np.where(np.isfinite(old["benchmark_yield_pct"]), old["benchmark_yield_pct"], cash_rate)
        # The benchmark yield is on the bond's annual basis, so a day's carry compounds it.
        funding = -old["dirty"] * ((1 + matched_rate / 100) ** (dt / 365) - 1)
        pnl["hedge_cash"] = float((scale * old["dirty"]).sum() * cash_rate / 100 * dt / 365)
        self.cash += float((scale * (cpn + prin)).sum())
        spread_t, age_t = mk["spread_bps"].to_numpy(dtype=float), mk["age_days"].to_numpy(dtype=float)
        source_t, tenor_t = mk["source"].to_numpy(dtype=object), mk["tenor_years"].to_numpy(dtype=float)
        bucket_t = mk["rating_bucket"].to_numpy(dtype=object)
        for j, isin in enumerate(isins):
            comps = {"income": income[j], "roll": roll[j], "rates": rates[j], "spread": spread[j],
                     "execution": 0.0, "funding": funding[j]}
            comps = {k: v * scale[j] for k, v in comps.items()}
            for k, v in comps.items():
                pnl[k] += v
            day[isin] = comps
            pos = self.book[isin]
            pos.update(yield_pct=y_t[j], dirty=now["dirty"][j], accrued=now["accrued"][j],
                       mod_duration=now["mod_duration"][j], dv01=now["dv01"][j])
            if np.isfinite(b_t[j]):
                pos["benchmark_yield_pct"] = b_t[j]
            pos.update(spread_bps=spread_t[j], age_days=age_t[j], source=source_t[j], tenor_years=tenor_t[j])
            if isinstance(bucket_t[j], str):
                pos["rating_bucket"] = bucket_t[j]
            if now["dirty"][j] == 0 and tenor_t[j] <= 0:
                closed[isin] = self.book.pop(isin) | {"closed": "matured"}
                if isin in self.orders:
                    self._close_order(isin, t, "matured")
        return pnl, closed

    def _execute(self, i: int, t: pd.Timestamp, nav_before: float, day: dict, closed: dict) -> float:
        live = [o for o in self.orders.values() if o.live_idx <= i]
        if not live:
            return 0.0
        trades = self.market.trades_on(t)
        need = [o.isin for o in live if o.isin not in self.book]
        fresh = self.market.marks(need, t).set_index("isin") if need else None
        floor = -max(self.cfg.max_gross - 1.0, 0.0) * nav_before
        lots = self.market.lot_face([o.isin for o in live], self.cfg.lot_face_inr)
        cost_total = 0.0
        # Sells first, so their proceeds can fund the buys.
        for o in sorted(live, key=lambda o: o.target_face - self.book.get(o.isin, {}).get("face", 0.0)):
            held = self.book.get(o.isin)
            mark = held if held is not None else fresh.loc[o.isin].to_dict()
            dirty = mark.get("dirty", np.nan)
            if not np.isfinite(dirty) or dirty <= 0:
                continue
            current = held["face"] if held else 0.0
            delta = o.target_face - current
            lot = float(lots.get(o.isin, self.cfg.lot_face_inr))
            if abs(delta) < lot or abs(delta) * dirty / 100 < self.cfg.min_trade_inr:
                self._close_order(o.isin, t, "filled" if o.n_fills else "no_trade")
                continue
            on_trade = o.isin in trades.index
            if self.ex.trade_days_only and not on_trade:
                continue
            side = 1.0 if delta > 0 else -1.0
            volume = float(trades.at[o.isin, "volume_inr"]) if on_trade else np.nan
            size = min(abs(delta), self.ex.max_fill_value(volume) / (dirty / 100))
            participation = size * dirty / 100 / volume if np.isfinite(volume) and volume > 0 else np.nan
            cost_bps = self.ex.cost_bps(mark.get("rating_bucket"), participation, on_trade)
            fill_yield = mark["yield_pct"] - side * cost_bps / 100
            fill = self.market.price([o.isin], t, [fill_yield])
            fill_dirty = float(fill["dirty"][0])
            if side > 0:
                per_face = fill_dirty / 100 * (1 + self.ex.stamp_duty_pct / 100)
                size = min(size, max(self.cash - floor, 0.0) / per_face)
            size = math.floor(size / lot + 1e-9) * lot
            if size <= 0 or (size * dirty / 100 < self.cfg.min_trade_inr and size < abs(delta)):
                continue
            value = size * fill_dirty / 100
            stamp = value * self.ex.stamp_duty_pct / 100 if side > 0 else 0.0
            exec_pnl = side * (dirty - fill_dirty) * size / 100 - stamp
            self.cash -= side * value + stamp
            cost_total -= exec_pnl
            if held is None:
                held = self.book[o.isin] = {
                    "face": 0.0, "issuer_id": o.issuer_id, "entry_date": t,
                    **{k: mark.get(k) for k in ("yield_pct", "benchmark_yield_pct", "dirty", "accrued",
                                                "mod_duration", "dv01", "spread_bps", "rating_bucket",
                                                "tenor_years", "source")},
                    "age_days": mark.get("age_days"),
                }
            held["face"] += side * size
            comps = day.setdefault(o.isin, dict.fromkeys([*COMPONENTS, "funding"], 0.0))
            comps["execution"] += exec_pnl
            o.filled_face += side * size
            o.n_fills += 1
            self.fills.append({
                "date": t, "order_id": o.order_id, "isin": o.isin, "issuer_id": o.issuer_id,
                "side": "buy" if side > 0 else "sell", "face": size, "mark_yield_pct": mark["yield_pct"],
                "fill_yield_pct": fill_yield, "cost_bps": cost_bps, "fill_clean": float(fill["clean"][0]),
                "fill_dirty": fill_dirty, "value_inr": value, "cost_inr": -exec_pnl,
                "participation": participation, "on_trade_day": on_trade, "rating_bucket": mark.get("rating_bucket"),
            })
            if held["face"] <= 1e-6:
                closed[o.isin] = self.book.pop(o.isin) | {"closed": "sold"}
            if abs(o.target_face - (self.book.get(o.isin, {}).get("face", 0.0))) < lot:
                self._close_order(o.isin, t, "filled")
        for o in list(self.orders.values()):
            if o.live_idx <= i and i >= o.expiry_idx:
                self._close_order(o.isin, t, "expired")
        return cost_total

    def _close_order(self, isin: str, t: pd.Timestamp, status: str) -> None:
        o = self.orders.pop(isin)
        self.order_log.append({
            "order_id": o.order_id, "created": o.created,
            "live_from": self.dates[o.live_idx] if o.live_idx < len(self.dates) else pd.NaT,
            "isin": o.isin, "issuer_id": o.issuer_id, "start_face": o.start_face, "target_face": o.target_face,
            "filled_face": o.filled_face, "n_fills": o.n_fills, "status": status, "closed": t,
        })

    def _holdings(self, nav: float) -> pd.DataFrame:
        book = self.book.values()
        value = np.array([p["face"] * p["dirty"] / 100 for p in book], dtype=float)
        return pd.DataFrame({
            "face": np.array([p["face"] for p in book], dtype=float), "value": value,
            "issuer_id": np.array([p["issuer_id"] for p in book], dtype=object),
            "entry_date": pd.DatetimeIndex([p["entry_date"] for p in book]),
            "weight": value / nav if nav else np.nan,
        }, index=pd.Index(list(self.book), name="isin", dtype=object))

    def _limit(self, weights) -> pd.Series:
        if weights is None:
            weights = pd.Series(dtype=float)
        elif isinstance(weights, dict):
            weights = pd.Series(weights, dtype=float)
        elif not isinstance(weights, pd.Series):
            raise TypeError(f"{self.strategy.name}.target_weights must return a pandas Series of weights "
                            f"indexed by ISIN, got {type(weights).__name__}")
        w = pd.to_numeric(weights, errors="coerce")
        if not w.index.is_unique:
            w = w.groupby(level=0).sum()
        w = w[(w > 0).to_numpy()]
        known = np.array([i in self.market.securities.index for i in w.index], dtype=bool)
        priced = np.array([i in self.market.cashflows for i in w.index], dtype=bool)
        for reason, mask in (("not in the security master", ~known),
                             ("without coupon or maturity terms", known & ~priced)):
            if mask.any():
                self.dropped.setdefault(reason, set()).update(w.index[mask])
        w = w[priced]
        if w.empty:
            return w
        w = w.clip(upper=self.cfg.max_bond_weight)
        issuers = [self.market.issuer(i) for i in w.index]
        totals: dict = {}
        for iss, v in zip(issuers, w.to_numpy()):
            totals[iss] = totals.get(iss, 0.0) + v
        w = w * np.minimum(1.0, self.cfg.max_issuer_weight / np.array([totals[i] for i in issuers]))
        if w.sum() > self.cfg.max_gross:
            w = w * self.cfg.max_gross / w.sum()
        return w

    def _rebalance(self, i: int, t: pd.Timestamp, nav: float) -> None:
        ev = self.events.iloc[: int(np.searchsorted(self._event_dates, np.datetime64(t, "ns"), side="right"))]
        if "ownership_known" in ev:
            ev = ev.copy()
            unknown = ~(ev["ownership_known"] <= t)
            ev.loc[unknown, ["constrained_share", "other_mf_share", "n_constrained_holders"]] = np.nan
        ctx = Context(date=t, market=self.market, nav=nav, holdings=self._holdings(nav), events=ev)
        w = self._limit(self.strategy.target_weights(ctx))
        new = [i for i in w.index if i not in self.book]
        dirty_now = {i: p["dirty"] for i, p in self.book.items()}
        if new:
            dirty_now.update(zip(new, self.market.marks(new, t)["dirty"].to_numpy()))
        lots = self.market.lot_face(list(dict.fromkeys([*w.index, *self.book])), self.cfg.lot_face_inr)
        targets = {}
        for isin, weight in w.items():
            dirty = dirty_now[isin]
            if not np.isfinite(dirty) or dirty <= 0:
                continue
            lot = float(lots[isin])
            targets[isin] = math.floor(weight * nav / (dirty / 100) / lot) * lot
        for isin in self.book:
            targets.setdefault(isin, 0.0)
        live_idx = i + self.ex.lag_days
        for isin, face in targets.items():
            lot = float(lots[isin])
            current = self.book.get(isin, {}).get("face", 0.0)
            existing = self.orders.get(isin)
            if existing is not None:
                if abs(face - current) >= lot and np.sign(face - current) == np.sign(existing.target_face - current):
                    # Resize the working order; it keeps its place in the queue.
                    existing.target_face = face
                    existing.expiry_idx = max(existing.expiry_idx, live_idx + self.ex.order_ttl_days)
                    continue
                done = abs(face - current) < lot
                self._close_order(isin, t, ("filled" if existing.n_fills else "cancelled") if done else "replaced")
            if abs(face - current) < lot:
                continue
            if current > 0 and face > 0 and abs(face - current) <= self.cfg.drift_tolerance * face:
                continue
            self.orders[isin] = _Order(
                order_id=self.next_order_id, created=t, live_idx=live_idx,
                expiry_idx=live_idx + self.ex.order_ttl_days, isin=isin,
                issuer_id=str(self.market.issuer(isin)), start_face=current, target_face=face,
            )
            self.next_order_id += 1
        for isin in [k for k in self.orders if k not in targets]:
            self._close_order(isin, t, "cancelled")

    def _record(self, t: pd.Timestamp, nav: float, day: dict, closed: dict) -> None:
        for isin, pos in list(self.book.items()) + list(closed.items()):
            comps = day.get(isin, {})
            value = pos["face"] * pos["dirty"] / 100 if isin in self.book else 0.0
            self.pos_rows.append({
                "date": t, "isin": isin, "issuer_id": pos["issuer_id"], "sector": self.sectors.get(isin),
                "rating_bucket": pos.get("rating_bucket"), "face": pos["face"] if isin in self.book else 0.0,
                "dirty": pos["dirty"], "value": value, "weight": value / nav if nav else np.nan,
                "yield_pct": pos["yield_pct"], "spread_bps": pos.get("spread_bps"),
                "benchmark_yield_pct": pos.get("benchmark_yield_pct"), "mod_duration": pos["mod_duration"],
                "dv01_inr": pos["face"] / 100 * pos["dv01"] if isin in self.book else 0.0,
                "tenor_years": pos.get("tenor_years"), "mark_age_days": pos.get("age_days"),
                "mark_source": pos.get("source"), "entry_date": pos["entry_date"],
                **{f"pnl_{k}": comps.get(k, 0.0) for k in (*COMPONENTS, "funding")},
                "closed": pos.get("closed"),
            })

    def run(self) -> None:
        if not len(self.market.cashflows.index):
            raise ValueError("no security has coupon_pct and maturity_date, so nothing can be priced or traded; "
                             "add them to the security master (see data/README.md)")
        nav_prev = hedged = self.cash
        prev = None
        for i, t in enumerate(self.dates):
            day: dict[str, dict] = {}
            pnl = dict.fromkeys([*COMPONENTS, "funding", "hedge_cash"], 0.0)
            closed: dict[str, dict] = {}
            cash_pnl = 0.0
            if prev is not None:
                rate = self.cash_rates[i - 1] + (self.cfg.borrow_spread_bps / 100 if self.cash < 0 else 0.0)
                cash_pnl = self.cash * rate / 100 * (t - prev).days / 365
                self.cash += cash_pnl
                pnl, closed = self._revalue(t, prev, self.cash_rates[i - 1], day)
            n_fills = len(self.fills)
            cost = self._execute(i, t, nav_prev, day, closed)
            pnl["execution"] -= cost
            invested = sum(p["face"] * p["dirty"] / 100 for p in self.book.values())
            nav = self.cash + invested
            day_pnl = nav - nav_prev if prev is not None else 0.0
            hedged_pnl = day_pnl - pnl["rates"] + pnl["funding"] + pnl["hedge_cash"]
            hedged += hedged_pnl
            values = np.array([p["face"] * p["dirty"] / 100 for p in self.book.values()])
            stats = {"mod_duration": np.nan, "spread_bps": np.nan, "yield_pct": np.nan, "stale_share": np.nan,
                     "avg_mark_age_days": np.nan}
            if values.sum() > 0:
                w = values / values.sum()
                col = lambda k: np.array([float(p.get(k) if p.get(k) is not None else np.nan)
                                          for p in self.book.values()])
                ages = col("age_days")
                stats = {"mod_duration": float(np.nansum(w * col("mod_duration"))),
                         "spread_bps": float(np.nansum(w * col("spread_bps"))),
                         "yield_pct": float(np.nansum(w * col("yield_pct"))),
                         "stale_share": float(w[np.nan_to_num(ages) > self.cfg.stale_days].sum()),
                         "avg_mark_age_days": float(np.nansum(w * ages))}
            rebalancing = t in self.rebalance
            self.nav_rows.append({
                "date": t, "nav": nav, "nav_hedged": hedged, "cash": self.cash, "invested": invested,
                "gross": invested / nav if nav else np.nan, "n_positions": len(self.book),
                "pnl": day_pnl, "pnl_hedged": hedged_pnl, **{f"pnl_{k}": pnl[k] for k in COMPONENTS},
                "pnl_cash": cash_pnl, "pnl_funding": pnl["funding"], "pnl_hedge_cash": pnl["hedge_cash"],
                "cash_rate_pct": self.cash_rates[i],
                "traded_inr": sum(f["value_inr"] for f in self.fills[n_fills:]), "cost_inr": cost,
                "dv01_inr": sum(p["face"] / 100 * p["dv01"] for p in self.book.values()), **stats,
                "rebalance": rebalancing,
            })
            self._record(t, nav, day, closed)
            if rebalancing:
                self._rebalance(i, t, nav)
            nav_prev, prev = nav, t
        for isin in list(self.orders):
            self._close_order(isin, self.dates[-1], "open_at_end")


def run_backtest(
    market: MarketData,
    strategy: Strategy,
    *,
    config: BacktestConfig | None = None,
    execution: ExecutionModel | None = None,
    events: pd.DataFrame | str | Path | None = None,
    ownership: pd.DataFrame | str | Path | None = None,
    cash_rates: pd.Series | str | Path | None = None,
) -> BacktestResult:
    """Run ``strategy`` over the market and return daily NAV, positions, fills and orders.

    ``events`` (``rating_events.csv``) and ``ownership`` (``event_ownership.csv``)
    may be DataFrames or paths. ``cash_rates`` (percent by date, e.g. the TREPS
    weighted average or MIBOR; a Series or a CSV of ``date, rate_pct``)
    overrides ``config.cash_rate_pct`` and the curve's shortest tenor as the
    rate earned on cash. Anything that probably explains an empty or partial
    backtest is listed in ``result.notes``.
    """
    if not dataclasses.is_dataclass(strategy) or "__dataclass_fields__" not in type(strategy).__dict__:
        raise TypeError(f"{type(strategy).__name__} must be decorated with @dataclass, like the built-in "
                        "strategies, or its parameters are ignored")
    config = config or BacktestConfig()
    execution = execution or ExecutionModel()
    events, ownership = _frame(events), _frame(ownership)
    cash_rates = read_cash_rates(cash_rates) if cash_rates is not None else None
    engine = _Engine(market, strategy, config, execution,
                     prepare_events(events, ownership, disclosure_lag_days=config.disclosure_lag_days),
                     cash_rates)
    engine.run()
    notes = engine.notes
    if getattr(strategy, "needs_events", False) and (events is None or not len(events)):
        notes.append(f"{strategy.name} trades on rating events but none were given (--events rating_events.csv)")
    if getattr(strategy, "min_constrained_share", 0) and ownership is None:
        notes.append("min_constrained_share is set but no ownership was given (--ownership event_ownership.csv), "
                     "so no event qualifies")
    for reason, isins in engine.dropped.items():
        shown = ", ".join(sorted(map(str, isins))[:5]) + (", ..." if len(isins) > 5 else "")
        notes.append(f"targets for {len(isins)} bond(s) {reason} were ignored: {shown}")
    if len(market.unpriced):
        notes.append(f"{len(market.unpriced)} of {len(market.securities)} securities lack coupon or maturity "
                     "terms and cannot be traded")
    if not engine.fills:
        notes.append("the strategy never traded: check its filters, the events and ownership inputs, and that "
                     "target bonds have coupon terms and reported trades after the signal")
    return BacktestResult(
        strategy=strategy.name, params=strategy.params(), config=config, execution=execution,
        nav=pd.DataFrame(engine.nav_rows, columns=list(NAV_COLUMNS)),
        positions=pd.DataFrame(engine.pos_rows, columns=list(POSITION_COLUMNS)),
        fills=pd.DataFrame(engine.fills, columns=list(FILL_COLUMNS)),
        orders=pd.DataFrame(engine.order_log, columns=list(ORDER_COLUMNS)).sort_values("order_id",
                                                                                       kind="stable"),
        notes=engine.notes,
    )
