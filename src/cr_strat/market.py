"""Point-in-time market view for strategies and backtests: snapshots and marks.

The price panel only has rows on days a bond traded. A backtest needs a value
for every bond it holds on every day, and a strategy must see the market as it
stood on the signal date and no later. ``MarketData`` provides both:

* ``snapshot(date)``: one row per ISIN that traded in the ``max_age_days`` up
  to and including ``date``: its last observed yield and spread, the median
  spread of matched peers on that day with the issuer excluded, the gap between
  the two (``rv_bps``), trading activity over ``liquidity_days``, and its
  rating as of ``date``.
* ``marks(isins, dates)``: a model mark for any bond on any date. The spread is
  the last observed spread moved by the change in its peer group's median
  since that trade (matrix pricing: the bond keeps its residual against
  peers). The peer group is fixed at the last trade's rating and tenor bucket,
  so a downgrade reaches a bond's mark only when it trades. The yield is the
  benchmark yield at the bond's remaining tenor plus that spread; price and
  risk come from ``bond_math``. On a day the bond traded the mark is the traded
  yield. Where there is no spread (tenor below the panel's minimum, stale
  curve) the last traded yield is carried.

Nothing looks past the requested date: last trades come from a backward as-of
join and peer levels from trailing windows.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from cr_strat.bond_math import CashflowTable, bonds_from_securities
from cr_strat.curves import BenchmarkCurve, SpreadBenchmark, tenor_bucket
from cr_strat.rating_scale import rating_bucket

DEFAULT_MATCH_ON = ("instrument_class", "rating_bucket", "tenor_bucket")
MARK_COLUMNS = (
    "isin", "date", "issuer_id", "obs_date", "age_days", "source", "rating_bucket", "tenor_years",
    "benchmark_yield_pct", "spread_bps", "yield_pct", "dirty", "accrued", "clean", "mod_duration",
    "convexity", "dv01",
)
SNAPSHOT_COLUMNS = (
    "isin", "issuer_id", "instrument_class", "sector", "obs_date", "age_days", "yield_pct", "spread_bps",
    "obs_rating_bucket", "obs_tenor_bucket", "peer_bps", "peer_issuers", "rv_bps", "trade_days",
    "volume_inr", "tenor_years", "tenor_bucket", "rating_notch", "rating_bucket", "priceable",
)


class MarketData:
    """Price panel, security terms and benchmark curve, queried as of a date."""

    def __init__(
        self,
        panel: pd.DataFrame,
        securities: pd.DataFrame,
        curve: BenchmarkCurve,
        *,
        ratings: pd.DataFrame | None = None,
        bond_compounding: str = "annual",
        match_on: Sequence[str] = DEFAULT_MATCH_ON,
        window_days: int = 5,
        min_issuers: int = 3,
        peer_adjust: bool = True,
    ):
        self.curve = curve
        self.bond_compounding = bond_compounding
        self.match_on = tuple(match_on)
        self.peer_adjust = peer_adjust
        self.securities = securities.drop_duplicates("isin", keep="last").set_index("isin")
        self._maturity = dict(zip(self.securities.index, pd.to_datetime(self.securities["maturity_date"])
                                  .to_numpy(dtype="datetime64[ns]")))
        self._issuer = self.securities["issuer_id"].to_dict()
        df = panel[panel["isin"].isin(self.securities.index) & panel["yield_pct"].notna()].copy()
        df["date"] = df["date"].astype("datetime64[ns]")
        df = df.drop_duplicates(["date", "isin"], keep="last")  # overlapping panels passed together
        for col in self.match_on:
            if col not in df:
                df[col] = self.securities[col].reindex(df["isin"]).to_numpy() if col in self.securities else None
        self.panel = df.sort_values(["date", "isin"], kind="stable").reset_index(drop=True)
        self._dates = self.panel["date"].to_numpy(dtype="datetime64[ns]")
        first = self.panel["date"].min() if len(self.panel) else None
        bonds, self.unpriced = bonds_from_securities(
            self.securities.reset_index(), after=None if first is None else first - pd.Timedelta(days=1))
        self.cashflows = CashflowTable(bonds)
        self.bench = SpreadBenchmark(self.panel, match_on=self.match_on, window_days=window_days,
                                     min_issuers=min_issuers)
        self._peer_cache: dict[tuple, float] = {}
        self._obs_by_isin: dict[str, tuple[np.ndarray, np.ndarray]] | None = None
        self._obs_arrays: dict[str, np.ndarray] = {}
        obs_cols = ["date", "isin", "issuer_id", "yield_pct", "spread_bps", *self.match_on]
        self._obs = (self.panel[list(dict.fromkeys(obs_cols))].rename(columns={"date": "obs_date"})
                     .sort_values("obs_date", kind="stable"))
        self.ratings = None
        if ratings is not None and len(ratings):
            rt = ratings.dropna(subset=["date"]).copy()
            rt["date"] = pd.to_datetime(rt["date"]).astype("datetime64[ns]")
            rt["issuer_id"] = rt["issuer_id"].astype(str)
            self.ratings = rt.sort_values("date", kind="stable")[["issuer_id", "instrument_class", "date", "notch"]]

    # -- raw observations ---------------------------------------------------------------------
    def _slice(self, after: pd.Timestamp, upto: pd.Timestamp) -> pd.DataFrame:
        """Panel rows with ``after < date <= upto``."""
        lo = np.searchsorted(self._dates, np.datetime64(after, "ns"), side="right")
        hi = np.searchsorted(self._dates, np.datetime64(upto, "ns"), side="right")
        return self.panel.iloc[lo:hi]

    def trades_on(self, date) -> pd.DataFrame:
        """ISIN-day trade rows on ``date``, indexed by ISIN."""
        t = pd.Timestamp(date)
        return self._slice(t - pd.Timedelta(days=1), t).set_index("isin")

    def issuer(self, isin: str) -> str | None:
        return self.securities["issuer_id"].get(isin)

    def lot_face(self, isins, default: float) -> pd.Series:
        """Face value per unit for each ISIN (the smallest tradable amount), ``default`` where unknown."""
        fv = self.securities["face_value_inr"] if "face_value_inr" in self.securities else pd.Series(dtype=float)
        return pd.to_numeric(fv.reindex(list(isins)), errors="coerce").fillna(default)

    def ratings_asof(self, date) -> pd.DataFrame:
        """Worst-of-agencies notch per (issuer_id, instrument_class) as of ``date``."""
        if self.ratings is None:
            return pd.DataFrame(columns=["issuer_id", "instrument_class", "rating_notch"])
        rt = self.ratings[self.ratings["date"] <= pd.Timestamp(date)]
        last = rt.groupby(["issuer_id", "instrument_class"], sort=False).tail(1)
        return last.drop(columns="date").rename(columns={"notch": "rating_notch"})

    def _peer(self, key: tuple, date) -> float:
        cache_key = (key, date)
        if cache_key not in self._peer_cache:
            self._peer_cache[cache_key] = self.bench.level(key, date)[0]
        return self._peer_cache[cache_key]

    # -- strategy view ------------------------------------------------------------------------
    def snapshot(self, date, *, max_age_days: int = 5, liquidity_days: int = 60, issuers=None) -> pd.DataFrame:
        """Bonds observed in the ``max_age_days`` to ``date`` with spreads, peers, liquidity and rating.

        ``issuers`` restricts the result to those issuer ids, which is much
        faster when a strategy only needs a few names.
        """
        t = pd.Timestamp(date)
        look = max(max_age_days, liquidity_days)
        window = self._slice(t - pd.Timedelta(days=look), t)
        if issuers is not None:
            window = window[window["issuer_id"].isin([str(i) for i in issuers])]
        recent = window[window["date"] > t - pd.Timedelta(days=max_age_days)]
        if recent.empty:
            return pd.DataFrame(columns=list(SNAPSHOT_COLUMNS))
        last = recent.groupby("isin", sort=False).tail(1).copy()
        g = window.groupby("isin")
        liq = pd.DataFrame({"trade_days": g["date"].nunique(), "volume_inr": g["volume_inr"].sum(min_count=1)})
        keys = last[list(self.match_on)].to_numpy(dtype=object)
        peers = [self.bench.level(tuple(k), d, iss) if np.isfinite(s) else (np.nan, 0)
                 for k, d, iss, s in zip(keys, last["date"], last["issuer_id"], last["spread_bps"].to_numpy(float))]
        last["peer_bps"] = [p[0] for p in peers]
        last["peer_issuers"] = [p[1] for p in peers]
        last["rv_bps"] = last["spread_bps"] - last["peer_bps"]
        # Liquidity is the window's total, not the last day's volume carried on the panel row.
        snap = last.drop(columns=["volume_inr", "trade_days"], errors="ignore").rename(
            columns={"date": "obs_date", "rating_bucket": "obs_rating_bucket", "tenor_bucket": "obs_tenor_bucket"})
        snap = snap.merge(liq, left_on="isin", right_index=True, how="left")
        snap["age_days"] = (t - snap["obs_date"]).dt.days
        maturity = pd.to_datetime(self.securities["maturity_date"].reindex(snap["isin"]).to_numpy())
        snap["tenor_years"] = (maturity - t).days / 365.25
        snap["tenor_bucket"] = tenor_bucket(snap["tenor_years"]).to_numpy()
        snap["issuer_id"] = snap["issuer_id"].astype(str)
        rated = self.ratings_asof(t)
        if len(rated):
            snap = snap.drop(columns=["rating_notch"], errors="ignore").merge(
                rated, on=["issuer_id", "instrument_class"], how="left")
        elif "rating_notch" not in snap:
            snap["rating_notch"] = np.nan
        # Without a rating timeline for the issuer, fall back to the rating at the last trade.
        snap["rating_bucket"] = [rating_bucket(n) for n in snap["rating_notch"]]
        snap["rating_bucket"] = snap["rating_bucket"].where(snap["rating_bucket"].notna(),
                                                            snap["obs_rating_bucket"])
        snap["priceable"] = snap["isin"].map(lambda i: i in self.cashflows)
        if "sector" not in snap:
            snap["sector"] = None
        return snap.reindex(columns=list(SNAPSHOT_COLUMNS)).reset_index(drop=True)

    # -- valuation ----------------------------------------------------------------------------
    def last_trade(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Join each (isin, date) row to the ISIN's last observation on or before that date."""
        isins = frame["isin"].to_numpy(dtype=object)
        when = frame["date"].to_numpy(dtype="datetime64[ns]")
        return pd.DataFrame({"isin": isins, "date": when, **self._last_obs(isins, when)})

    def _last_obs(self, isins: np.ndarray, when: np.ndarray) -> dict[str, np.ndarray]:
        if len(isins) > 5000:
            left = pd.DataFrame({"isin": isins, "date": when, "_row": np.arange(len(isins))})
            m = pd.merge_asof(left.sort_values("date", kind="stable"), self._obs, left_on="date",
                              right_on="obs_date", by="isin", direction="backward").sort_values("_row")
            return {c: m[c].to_numpy() for c in self._obs.columns if c != "isin"}
        # Small batches (a day's holdings): per-ISIN binary search beats a full as-of join.
        if self._obs_by_isin is None:
            dates = self._obs["obs_date"].to_numpy(dtype="datetime64[ns]")
            self._obs_by_isin = {k: (dates[v], v) for k, v in self._obs.groupby("isin").indices.items()}
            self._obs_arrays = {c: self._obs[c].to_numpy() for c in self._obs.columns if c != "isin"}
        pos = np.full(len(isins), -1)
        for j, isin in enumerate(isins):
            hit = self._obs_by_isin.get(isin)
            if hit is not None:
                k = np.searchsorted(hit[0], when[j], side="right") - 1
                if k >= 0:
                    pos[j] = hit[1][k]
        found = pos >= 0
        take = np.where(found, pos, 0)
        out = {}
        for col, arr in self._obs_arrays.items():
            values = arr[take]
            if values.dtype.kind == "M":
                values = np.where(found, values, np.datetime64("NaT", "ns"))
            elif values.dtype.kind == "f":
                values = np.where(found, values, np.nan)
            else:
                values = np.where(found, values.astype(object), None)
            out[col] = values
        return out

    def price(self, isins, dates, yields_pct) -> dict[str, np.ndarray]:
        return self.cashflows.analytics(list(isins), dates, yields_pct, self.bond_compounding)

    def marks(self, isins, dates) -> pd.DataFrame:
        """Model mark for each (isin, date); see the module docstring. Unmarkable rows have NaN prices."""
        isins = list(isins)
        if not isins:
            return pd.DataFrame(columns=list(MARK_COLUMNS))
        dates = pd.to_datetime(dates)
        if isinstance(dates, pd.Timestamp):
            dates = [dates] * len(isins)
        isins = np.asarray(isins, dtype=object)
        date = pd.DatetimeIndex(dates).to_numpy(dtype="datetime64[ns]")
        m = self._last_obs(isins, date)
        obs_date = m["obs_date"].astype("datetime64[ns]")
        age = (date - obs_date) / np.timedelta64(1, "D")
        spread_obs = m["spread_bps"].astype(float)
        yield_obs = m["yield_pct"].astype(float)
        drift = np.zeros(len(isins))
        adjusted = np.zeros(len(isins), dtype=bool)
        if self.peer_adjust:
            stale = np.flatnonzero((age > 0) & np.isfinite(spread_obs))
            keys = list(zip(*(m[c] for c in self.match_on)))
            for i in stale:
                d = self._peer(keys[i], date[i]) - self._peer(keys[i], obs_date[i])
                if np.isfinite(d):
                    drift[i], adjusted[i] = d, True
        maturity = np.array([self._maturity.get(i, np.datetime64("NaT", "ns")) for i in isins], dtype="datetime64[ns]")
        tenor = (maturity - date) / np.timedelta64(1, "D") / 365.25
        bench = self.curve.yields(date, tenor, to_basis=self.bond_compounding)
        spread = spread_obs + drift
        model_yield = bench + spread / 100
        fresh = age == 0
        yld = np.where(fresh | ~np.isfinite(model_yield), yield_obs, model_yield)
        out = {
            "isin": isins,
            "date": date,
            "issuer_id": np.array([self._issuer.get(i) for i in isins], dtype=object),
            "obs_date": obs_date,
            "age_days": age,
            "source": np.select([np.isnan(age), fresh, adjusted], [None, "trade", "matrix"], "carried"),
            "rating_bucket": m["rating_bucket"] if "rating_bucket" in m else None,
            "tenor_years": tenor,
            "benchmark_yield_pct": bench,
            "spread_bps": np.where(fresh, spread_obs, spread),
            "yield_pct": yld,
        }
        ok = np.array([i in self.cashflows for i in out["isin"]]) & np.isfinite(yld)
        prices = {c: np.full(len(isins), np.nan)
                  for c in ("dirty", "accrued", "clean", "mod_duration", "convexity", "dv01")}
        if ok.any():
            for col, values in self.price(out["isin"][ok], date[ok], yld[ok]).items():
                prices[col][ok] = values
        return pd.DataFrame(out | prices, columns=list(MARK_COLUMNS))
