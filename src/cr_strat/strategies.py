"""Trading strategies: each turns a point-in-time view of the market into target weights.

A strategy is a dataclass with a ``rebalance`` frequency (``"D"``, ``"W-FRI"``,
``"M"``, ``"Q"``) and a ``target_weights(ctx)`` method returning a Series of
weights of NAV by ISIN; whatever is not allocated stays in cash. The engine
caps weights per bond and issuer, ignores negative weights (Indian corporate
bonds cannot practically be shorted) and turns the rest into orders that go
live after the execution lag.

``ctx`` (a ``Context``) holds only what was known at the close of
``ctx.date``: market snapshots from ``MarketData.snapshot``, rating events
published on or before that date, the pre-event ownership measures where the
holdings disclosure behind them had been published, and current holdings.

Built-in strategies:

* ``event_reversal``: buy bonds of issuers whose rating has just crossed a
  mandate threshold downward and hold for a fixed period: the forced-selling
  reversal trade this repo was built to study.
* ``relative_value``: hold the bonds trading widest of their matched peers.
* ``carry``: hold the widest-spread bonds above a rating floor.
* ``universe_index``: equal- or size-weighted basket of the eligible
  universe, as a benchmark.

To add a strategy, write a ``@dataclass`` subclass of ``Strategy`` and pass an
instance to ``run_backtest``. To use it from a config file, name it there as
``"my_file.py:MyClass"`` (or ``"package.module:MyClass"``), or decorate it with
``register_strategy`` so its ``name`` works like a built-in one.
"""

from __future__ import annotations

import dataclasses
import importlib
import importlib.util
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import numpy as np
import pandas as pd

from cr_strat.market import MarketData
from cr_strat.rating_scale import LT_NOTCH

_BOOL_COLUMNS = ("first_in_window", "prior_signal", "below_ig", "default", "split", "issuer_not_cooperating")


def prepare_events(events: pd.DataFrame | None, ownership: pd.DataFrame | None = None,
                   disclosure_lag_days: int = 10) -> pd.DataFrame:
    """Rating events with typed columns and, where available, pre-event ownership.

    ``ownership`` is ``event_ownership.csv``. Only measures fixed before the
    event are kept (shares at the baseline snapshot); the post-event holdings
    changes are outcomes and must not reach a strategy. ``ownership_known``
    is the baseline snapshot date plus ``disclosure_lag_days``: AMCs publish
    portfolios days after the date they describe.
    """
    if events is None:
        return pd.DataFrame(columns=["event_id", "issuer_id", "instrument_class", "mandate", "direction", "date"])
    ev = events.copy()
    ev["date"] = pd.to_datetime(ev["date"], format="ISO8601", errors="coerce")
    ev["issuer_id"] = ev["issuer_id"].astype(str).str.strip()
    for col in _BOOL_COLUMNS:
        if col in ev:
            ev[col] = ev[col].map(lambda v: str(v).strip().lower() in ("true", "1", "yes"))
    for col in ("prev_notch", "new_notch", "event_id"):
        if col in ev:
            ev[col] = pd.to_numeric(ev[col], errors="coerce")
    if ownership is not None and len(ownership):
        own = ownership.copy()
        own["event_id"] = pd.to_numeric(own["event_id"], errors="coerce")
        keep = [c for c in ("constrained_share", "other_mf_share", "n_constrained_holders", "baseline_to")
                if c in own]
        own = own[["event_id", *keep]]
        for col in ("constrained_share", "other_mf_share", "n_constrained_holders"):
            if col in own:
                own[col] = pd.to_numeric(own[col], errors="coerce")
        own["ownership_known"] = (pd.to_datetime(own.get("baseline_to"), format="ISO8601", errors="coerce")
                                  + pd.Timedelta(days=disclosure_lag_days))
        ev = ev.merge(own.drop(columns=["baseline_to"], errors="ignore"), on="event_id", how="left")
    return ev.dropna(subset=["date"]).sort_values("date", kind="stable").reset_index(drop=True)


@dataclass
class Context:
    """What a strategy may see at the close of ``date``."""

    date: pd.Timestamp
    market: MarketData
    nav: float
    holdings: pd.DataFrame  # index isin: face, value, weight, issuer_id, entry_date
    events: pd.DataFrame
    _cache: dict = field(default_factory=dict, repr=False)

    def snapshot(self, max_age_days: int = 5, liquidity_days: int = 60) -> pd.DataFrame:
        key = (max_age_days, liquidity_days)
        if key not in self._cache:
            self._cache[key] = self.market.snapshot(self.date, max_age_days=max_age_days,
                                                    liquidity_days=liquidity_days)
        return self._cache[key]

    def recent_events(self, days: int, direction: str | None = None) -> pd.DataFrame:
        ev = self.events[self.events["date"] > self.date - pd.Timedelta(days=days)]
        return ev if direction is None else ev[ev["direction"] == direction]


@dataclass
class Strategy:
    """Base class. Subclasses are dataclasses whose fields are the parameters.

    ``name`` (a ClassVar) labels outputs and selects the strategy in config
    files; it defaults to the class name in snake case.
    """

    rebalance: str = "W-FRI"
    name: ClassVar[str] = "strategy"
    needs_events: ClassVar[bool] = False

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        if "name" not in cls.__dict__:
            cls.name = re.sub(r"(?<!^)(?=[A-Z])", "_", cls.__name__).lower()

    def target_weights(self, ctx: Context) -> pd.Series:
        raise NotImplementedError

    def params(self) -> dict:
        return {f.name: getattr(self, f.name) for f in dataclasses.fields(self)}


def _notch(grade: str) -> int:
    if grade not in LT_NOTCH:
        raise ValueError(f"unknown rating grade {grade!r}")
    return LT_NOTCH[grade]


def eligible(snap: pd.DataFrame, *, min_rating: str, instrument_classes, min_tenor_years: float,
             max_tenor_years: float, min_trade_days: int = 1) -> pd.DataFrame:
    """Snapshot rows a strategy may buy: priceable, rated at or above ``min_rating``, in the tenor range."""
    notch = pd.to_numeric(snap["rating_notch"], errors="coerce")
    keep = (snap["priceable"].astype(bool)
            & (notch <= _notch(min_rating))
            & snap["instrument_class"].isin(list(instrument_classes))
            & snap["tenor_years"].between(min_tenor_years, max_tenor_years)
            & (snap["trade_days"].fillna(0) >= min_trade_days)
            & snap["yield_pct"].notna())
    return snap[keep]


def _equal(isins, weight: float) -> pd.Series:
    isins = list(dict.fromkeys(isins))
    return pd.Series(weight, index=pd.Index(isins, name="isin"), dtype=float)


@dataclass
class EventReversal(Strategy):
    """Buy bonds of issuers that just crossed a mandate threshold downward; hold for ``hold_days``.

    The hypothesis: holders bound by the mandate must sell whatever the price,
    the selling pushes spreads past what the new rating warrants, and they
    revert once it ends. Entry is ``entry_delay_days`` after the public event
    date, exit ``hold_days`` after it (calendar days). Filters:

    * ``mandates``, ``first_in_window_only``, ``exclude_default``,
      ``min_new_grade``: which crossings count;
    * ``prior_signal``: ``"with"`` or ``"without"`` a matching outlook or watch
      beforehand (anticipated versus surprise events), or ``"any"``;
    * ``min_constrained_share``: pre-event share held by constrained holders,
      used only once the holdings disclosure is public;
    * ``min_gap_bps``: the bond must trade at least this wide of peers at its
      new rating on a trade after the event, i.e. still be dislocated at entry;
    * ``exit_on_new_event``: exit early on any later downgrade event for the issuer.

    Each qualifying issuer gets ``position_weight`` of NAV, split over its
    ``bonds_per_issuer`` most traded bonds. Bonds already held are kept until exit.
    """

    name: ClassVar[str] = "event_reversal"
    needs_events: ClassVar[bool] = True
    rebalance: str = "D"
    mandates: tuple[str, ...] = ("mf_corporate_bond_fund",)
    direction: str = "down"
    entry_delay_days: int = 0
    hold_days: int = 120
    first_in_window_only: bool = True
    exclude_default: bool = True
    min_new_grade: str = "BBB-"
    prior_signal: str = "any"
    min_constrained_share: float = 0.0
    min_gap_bps: float | None = None
    max_signal_age_days: int = 10
    liquidity_days: int = 90
    bonds_per_issuer: int = 1
    min_tenor_years: float = 0.5
    position_weight: float = 0.05
    exit_on_new_event: bool = True

    def __post_init__(self) -> None:
        if self.prior_signal not in ("any", "with", "without"):
            raise ValueError("prior_signal must be 'any', 'with' or 'without'")
        _notch(self.min_new_grade)

    def active_events(self, ctx: Context) -> pd.DataFrame:
        ev = ctx.events
        if ev.empty:
            return ev
        sel = ev[ev["mandate"].isin(list(self.mandates)) & (ev["direction"] == self.direction)]
        if self.first_in_window_only and "first_in_window" in sel:
            sel = sel[sel["first_in_window"]]
        if self.exclude_default and "default" in sel:
            sel = sel[~sel["default"]]
        if "new_notch" in sel:
            sel = sel[sel["new_notch"].fillna(LT_NOTCH["D"]) <= _notch(self.min_new_grade)]
        if self.prior_signal != "any" and "prior_signal" in sel:
            sel = sel[sel["prior_signal"] == (self.prior_signal == "with")]
        age = (ctx.date - sel["date"]).dt.days
        sel = sel[(age >= self.entry_delay_days) & (age < self.hold_days)]
        if self.min_constrained_share > 0:
            if "constrained_share" not in sel:
                return sel.iloc[0:0]
            known = sel["ownership_known"] <= ctx.date
            sel = sel[known & (sel["constrained_share"] >= self.min_constrained_share)]
        if self.exit_on_new_event and len(sel):
            last_down = ev.loc[ev["direction"] == "down"].groupby("issuer_id")["date"].max()
            sel = sel[~(sel["issuer_id"].map(last_down) > sel["date"])]
        # One position per issuer: the earliest live event keeps it until that event's exit.
        return sel.sort_values("date", kind="stable").drop_duplicates("issuer_id")

    def target_weights(self, ctx: Context) -> pd.Series:
        sel = self.active_events(ctx)
        if sel.empty:
            return pd.Series(dtype=float)
        held = ctx.holdings
        held_issuers = set(held["issuer_id"]) if len(held) else set()
        new = [i for i in sel["issuer_id"] if i not in held_issuers]
        snap = ctx.market.snapshot(ctx.date, max_age_days=self.max_signal_age_days,
                                   liquidity_days=self.liquidity_days, issuers=new) if new else None
        weights = []
        for e in sel.itertuples(index=False):
            mine = held.index[held["issuer_id"] == e.issuer_id] if len(held) else []
            if len(mine):
                weights.append(_equal(mine, self.position_weight / len(mine)))
                continue
            if snap is None or snap.empty:
                continue
            cand = snap[(snap["issuer_id"] == e.issuer_id) & (snap["instrument_class"] == e.instrument_class)
                        & snap["priceable"].astype(bool) & (snap["tenor_years"] >= self.min_tenor_years)]
            if self.min_gap_bps is not None:
                cand = cand[(cand["obs_date"] >= e.date) & (cand["rv_bps"] >= self.min_gap_bps)]
            if cand.empty:
                continue
            cand = cand.sort_values(["trade_days", "volume_inr"], ascending=False).head(self.bonds_per_issuer)
            weights.append(_equal(cand["isin"], self.position_weight / len(cand)))
        return pd.concat(weights) if weights else pd.Series(dtype=float)


@dataclass
class _Ranked(Strategy):
    """Shared machinery for strategies that hold the top ``n_bonds`` of the eligible universe by a score.

    A new position needs a trade in the last ``max_signal_age_days``. A held
    bond stays while it is still eligible and its latest trade in the
    ``liquidity_days`` window ranks it within the top ``hold_buffer`` x
    ``n_bonds``; a held bond with no trade in that window stays while its
    tenor, rating and downgrade filters still pass. Without this buffer a bond
    would be sold just for not having traded lately, which in this market is
    most bonds most of the time.
    """

    rebalance: str = "M"
    min_rating: str = "A"
    instrument_classes: tuple[str, ...] = ("BOND",)
    min_tenor_years: float = 0.75
    max_tenor_years: float = 10.0
    max_signal_age_days: int = 5
    liquidity_days: int = 60
    min_trade_days: int = 3
    n_bonds: int = 20
    one_bond_per_issuer: bool = True
    exclude_downgraded_days: int = 90
    hold_buffer: float = 2.0

    def score(self, uni: pd.DataFrame) -> pd.Series:
        """Higher is better; NaN means the bond cannot be ranked."""
        raise NotImplementedError

    def entry_filter(self, uni: pd.DataFrame) -> pd.DataFrame:
        return uni

    def _downgraded(self, ctx: Context) -> set:
        if not self.exclude_downgraded_days or ctx.events.empty:
            return set()
        return set(ctx.recent_events(self.exclude_downgraded_days, "down")["issuer_id"])

    def universe(self, ctx: Context, max_age_days: int, min_trade_days: int | None = None) -> pd.DataFrame:
        snap = eligible(ctx.snapshot(max_age_days, self.liquidity_days), min_rating=self.min_rating,
                        instrument_classes=self.instrument_classes, min_tenor_years=self.min_tenor_years,
                        max_tenor_years=self.max_tenor_years,
                        min_trade_days=self.min_trade_days if min_trade_days is None else min_trade_days)
        return snap[~snap["issuer_id"].isin(self._downgraded(ctx))]

    def _ranked(self, uni: pd.DataFrame) -> pd.DataFrame:
        return uni.assign(_score=self.score(uni)).dropna(subset=["_score"]).sort_values(
            "_score", ascending=False, kind="stable")

    def holds(self, ctx: Context, wide: pd.DataFrame) -> dict[str, str]:
        """Held bonds to keep, as isin -> issuer."""
        held = ctx.holdings
        if held.empty:
            return {}
        ranked = self._ranked(wide)
        rank = dict(zip(ranked["isin"], range(len(ranked))))
        seen = set(ctx.snapshot(self.liquidity_days, self.liquidity_days)["isin"])
        unseen = self._still_eligible(ctx, [i for i in held.index if i not in seen])
        limit = self.hold_buffer * self.n_bonds
        return {i: iss for i, iss in held["issuer_id"].items() if rank.get(i, np.inf) < limit or i in unseen}

    def _still_eligible(self, ctx: Context, isins: list[str]) -> set[str]:
        if not isins:
            return set()
        sec = ctx.market.securities.reindex(isins)
        tenor = (pd.to_datetime(sec["maturity_date"]) - ctx.date).dt.days / 365.25
        ok = (tenor >= self.min_tenor_years).to_numpy() & ~sec["issuer_id"].isin(self._downgraded(ctx)).to_numpy()
        rated = ctx.market.ratings_asof(ctx.date)
        if len(rated):
            notch = sec[["issuer_id", "instrument_class"]].astype(str).merge(
                rated, on=["issuer_id", "instrument_class"], how="left")["rating_notch"].to_numpy(dtype=float)
            ok &= ~(notch > _notch(self.min_rating))
        return set(np.asarray(isins)[ok])

    def pick(self, ranked: pd.DataFrame, keep: dict[str, str]) -> list[str]:
        """Bonds in ``keep`` first, then the top of ``ranked``, up to ``n_bonds``."""
        keep = dict(list(keep.items())[: self.n_bonds])
        chosen, issuers = list(keep), set(keep.values())
        for isin, issuer in zip(ranked["isin"], ranked["issuer_id"]):
            if len(chosen) >= self.n_bonds:
                break
            if isin in keep or (self.one_bond_per_issuer and issuer in issuers):
                continue
            chosen.append(isin)
            issuers.add(issuer)
        return chosen

    def weights(self, ctx: Context, chosen: list[str]) -> pd.Series:
        return _equal(chosen, 1.0 / self.n_bonds)

    def target_weights(self, ctx: Context) -> pd.Series:
        # Liquidity screens apply to new entries only; a held bond is judged on its latest trade.
        keep = self.holds(ctx, self.universe(ctx, self.liquidity_days, min_trade_days=1))
        fresh = self.entry_filter(self.universe(ctx, self.max_signal_age_days))
        return self.weights(ctx, self.pick(self._ranked(fresh), keep))


@dataclass
class RelativeValue(_Ranked):
    """Hold bonds trading widest of matched peers (``rv_bps``: spread minus peer median, issuer excluded).

    New entries need ``rv_bps`` between ``min_rv_bps`` and ``max_rv_bps``;
    extreme residuals are more often unrecognised bad news than mispricing.
    A held bond stays while its latest ``rv_bps`` is at least ``hold_rv_bps``.
    """

    name: ClassVar[str] = "relative_value"
    min_peer_issuers: int = 3
    min_rv_bps: float = 10.0
    max_rv_bps: float | None = 300.0
    hold_rv_bps: float = 0.0

    def score(self, uni: pd.DataFrame) -> pd.Series:
        return uni["rv_bps"].where(uni["peer_issuers"] >= self.min_peer_issuers)

    def entry_filter(self, uni: pd.DataFrame) -> pd.DataFrame:
        uni = uni[uni["rv_bps"] >= self.min_rv_bps]
        return uni if self.max_rv_bps is None else uni[uni["rv_bps"] <= self.max_rv_bps]

    def holds(self, ctx: Context, wide: pd.DataFrame) -> dict[str, str]:
        held = ctx.holdings
        if held.empty:
            return {}
        rv = wide.set_index("isin")["rv_bps"]
        seen = set(ctx.snapshot(self.liquidity_days, self.liquidity_days)["isin"])
        unseen = self._still_eligible(ctx, [i for i in held.index if i not in seen])
        return {i: iss for i, iss in held["issuer_id"].items()
                if i in unseen or (i in rv.index and not rv[i] < self.hold_rv_bps)}


@dataclass
class Carry(_Ranked):
    """Hold the widest-spread bonds above a rating floor (``signal``: ``spread_bps`` or ``yield_pct``)."""

    name: ClassVar[str] = "carry"
    min_rating: str = "AA-"
    signal: str = "spread_bps"

    def score(self, uni: pd.DataFrame) -> pd.Series:
        return uni[self.signal]


@dataclass
class UniverseIndex(_Ranked):
    """Basket of the ``n_bonds`` most traded eligible bonds, equal or outstanding-weighted, fully invested."""

    name: ClassVar[str] = "universe_index"
    min_rating: str = "A-"
    n_bonds: int = 100
    one_bond_per_issuer: bool = False
    exclude_downgraded_days: int = 0
    weighting: str = "equal"

    def score(self, uni: pd.DataFrame) -> pd.Series:
        return uni["trade_days"] + uni["volume_inr"].rank(pct=True).fillna(0)

    def weights(self, ctx: Context, chosen: list[str]) -> pd.Series:
        if not chosen:
            return pd.Series(dtype=float)
        if self.weighting == "outstanding" and "outstanding_face_inr" in ctx.market.securities:
            size = pd.to_numeric(ctx.market.securities["outstanding_face_inr"], errors="coerce").reindex(chosen)
            size = size.fillna(size.median() if size.notna().any() else 1.0)
            return size / size.sum()
        return _equal(chosen, 1.0 / len(chosen))


STRATEGIES: dict[str, type[Strategy]] = {
    cls.name: cls for cls in (EventReversal, RelativeValue, Carry, UniverseIndex)
}


def register_strategy(cls: type[Strategy]) -> type[Strategy]:
    """Class decorator that makes a strategy available by ``cls.name`` in config files."""
    STRATEGIES[cls.name] = cls
    return cls


def _import_strategy(ref: str) -> type[Strategy]:
    """``"path/to/file.py:Class"`` or ``"package.module:Class"``."""
    target, _, attr = ref.rpartition(":")
    if target.endswith(".py"):
        path = Path(target).expanduser().resolve()
        if not path.exists():
            raise ValueError(f"strategy file not found: {path}")
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[path.stem] = module
        spec.loader.exec_module(module)
    else:
        module = importlib.import_module(target)
    cls = getattr(module, attr, None)
    if not (isinstance(cls, type) and issubclass(cls, Strategy)):
        raise ValueError(f"{ref!r} does not name a Strategy subclass")
    return cls


def build_strategy(name: str, params: dict | None = None) -> Strategy:
    """Instantiate a strategy from config, converting lists to tuples.

    ``name`` is a registered name (``STRATEGIES``) or a reference to your own
    class: ``"my_strategies.py:WideSpreads"`` (a file) or
    ``"mypackage.strategies:WideSpreads"`` (an importable module). A relative
    file path is resolved against the current directory.
    """
    if ":" in name:
        cls = _import_strategy(name)
    elif name in STRATEGIES:
        cls = STRATEGIES[name]
    else:
        raise ValueError(f"unknown strategy {name!r}; choose from {sorted(STRATEGIES)} "
                         "or give your own class as 'file.py:ClassName'")
    fields = {f.name for f in dataclasses.fields(cls)}
    params = dict(params or {})
    unknown = sorted(set(params) - fields)
    if unknown:
        raise ValueError(f"{name}: unknown parameters {unknown}; valid: {sorted(fields)}")
    return cls(**{k: tuple(v) if isinstance(v, list) else v for k, v in params.items()})
