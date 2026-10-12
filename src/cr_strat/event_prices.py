"""Spread paths around rating events, against matched peers.

For each event and each traded bond of the issuer in the event's instrument
class, spreads are read at anchor dates relative to the event date t:

* ``pre``: the last trade in the ``pre_lookback_days`` before t, the reference level;
* ``first_post``: the first trade in the ``first_post_days`` from t, the immediate reaction;
* one anchor per offset in ``anchors`` (default t-60, t+10, t+60, t+120, t+250):
  the trade nearest the target day within a tolerance of
  ``max(min_tolerance_days, tolerance_frac * |offset|)`` days, on the same side
  of t as the target.

No price is interpolated. If a bond did not trade near an anchor the anchor is
empty, and ``*_days`` records how far the trade used actually was from t.

For each anchor:

* ``chg``: raw spread change in bps. For later anchors it is anchor minus pre;
  for earlier anchors it is pre minus anchor, so a positive value is always
  widening into the later date.
* ``abn``: the same change minus the change in the matched peer benchmark. Peers
  are fixed at the bond's **pre-event** rating bucket, so ``abn`` is the move
  relative to bonds that were rated like this one before the event.
* ``gap_cur``: spread minus peers at the bond's rating bucket **on the anchor
  date**. After a downgrade this shows whether the bond trades wide or tight of
  its new rating.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from cr_strat.curves import SpreadBenchmark

DEFAULT_ANCHORS = (-60, 10, 60, 120, 250)
_EVENT_FIELDS = (
    "event_id", "issuer_id", "issuer_name", "instrument_class", "mandate", "threshold", "direction",
    "date", "prev_grade", "new_grade", "cause", "prior_signal", "first_in_window", "below_ig",
)


def anchor_label(offset: int) -> str:
    return f"m{-offset}" if offset < 0 else f"p{offset}"


def _key(row, match_on: Sequence[str], bucket) -> tuple:
    return tuple(bucket if c == "rating_bucket" else row[c] for c in match_on)


def _nearest(rows: pd.DataFrame, target: pd.Timestamp, tolerance: pd.Timedelta, before: pd.Timestamp | None,
             after: pd.Timestamp | None):
    window = rows[(rows["date"] - target).abs() <= tolerance]
    if before is not None:
        window = window[window["date"] < before]
    if after is not None:
        window = window[window["date"] >= after]
    if window.empty:
        return None
    dist = (window["date"] - target).abs()
    return window.loc[dist.sort_values(kind="stable").index[0]]


def event_price_paths(
    events: pd.DataFrame,
    panel: pd.DataFrame,
    *,
    anchors: Sequence[int] = DEFAULT_ANCHORS,
    pre_lookback_days: int = 30,
    first_post_days: int = 20,
    min_tolerance_days: int = 5,
    tolerance_frac: float = 0.2,
    match_on: Sequence[str] = ("instrument_class", "rating_bucket", "tenor_bucket"),
    window_days: int = 5,
    min_issuers: int = 3,
    liquidity_days: int = 90,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return ``(bond_paths, event_paths)``: one row per event x bond, and the mean across bonds per event."""
    bench = SpreadBenchmark(panel, match_on=match_on, window_days=window_days, min_issuers=min_issuers)
    traded = panel[panel["spread_bps"].notna()].sort_values("date")
    by_key = {k: g for k, g in traded.groupby(["issuer_id", "instrument_class"])}
    one_day = pd.Timedelta(days=1)
    labels = ["first_post"] + [anchor_label(k) for k in anchors]

    bond_rows = []
    for ev in events.itertuples(index=False):
        t = pd.Timestamp(ev.date)
        bonds = by_key.get((str(ev.issuer_id), ev.instrument_class))
        if bonds is None or pd.isna(t):
            continue
        for isin, b in bonds.groupby("isin"):
            out = {"event_id": ev.event_id, "isin": isin}
            before = b[(b["date"] >= t - pd.Timedelta(days=pre_lookback_days)) & (b["date"] < t)]
            pre = before.iloc[-1] if len(before) else None
            recent = b[(b["date"] >= t - pd.Timedelta(days=liquidity_days)) & (b["date"] < t)]
            out["pre_trade_days"] = len(recent)
            out["pre_volume_inr"] = recent["volume_inr"].sum(min_count=1)
            pre_bucket = pre["rating_bucket"] if pre is not None else None
            if pre is not None:
                pre_peer, n = bench.level(_key(pre, match_on, pre_bucket), pre["date"], ev.issuer_id)
                out.update(pre_date=pre["date"], pre_spread_bps=pre["spread_bps"], pre_bucket=pre_bucket,
                           pre_tenor_bucket=pre["tenor_bucket"], pre_peer_bps=pre_peer, pre_peer_issuers=n)
            else:
                pre_peer = np.nan

            picks = {"first_post": None}
            after_t = b[(b["date"] >= t) & (b["date"] <= t + pd.Timedelta(days=first_post_days))]
            if len(after_t):
                picks["first_post"] = after_t.iloc[0]
            for k in anchors:
                tol = pd.Timedelta(days=max(min_tolerance_days, round(tolerance_frac * abs(k))))
                target = t + pd.Timedelta(days=k)
                picks[anchor_label(k)] = _nearest(b, target, tol, before=t if k < 0 else None,
                                                  after=t if k >= 0 else None)

            for label, r in picks.items():
                if r is None:
                    continue
                s = r["spread_bps"]
                same_bucket_peer, _ = bench.level(_key(r, match_on, pre_bucket), r["date"], ev.issuer_id)
                cur_peer, n_cur = bench.level(_key(r, match_on, r["rating_bucket"]), r["date"], ev.issuer_id)
                sign = -1.0 if r["date"] < t else 1.0
                out[f"{label}_date"] = r["date"]
                out[f"{label}_days"] = (r["date"] - t) / one_day
                out[f"{label}_spread_bps"] = s
                if pre is not None:
                    out[f"{label}_chg_bps"] = sign * (s - pre["spread_bps"])
                    out[f"{label}_abn_bps"] = sign * ((s - pre["spread_bps"]) - (same_bucket_peer - pre_peer))
                out[f"{label}_gap_cur_bps"] = s - cur_peer
                out[f"{label}_peer_issuers"] = n_cur
            bond_rows.append(out)

    bonds_df = pd.DataFrame(bond_rows)
    event_info = events[[c for c in _EVENT_FIELDS if c in events.columns]]
    if bonds_df.empty:
        return bonds_df, event_info.assign(n_bonds=0)
    columns = ["pre_date", "pre_spread_bps", "pre_bucket", "pre_tenor_bucket", "pre_peer_bps", "pre_peer_issuers"]
    for label in labels:
        columns += [f"{label}_{suffix}" for suffix in
                    ("date", "days", "spread_bps", "chg_bps", "abn_bps", "gap_cur_bps", "peer_issuers")]
    bonds_df = bonds_df.reindex(columns=["event_id", "isin", "pre_trade_days", "pre_volume_inr", *columns])

    g = bonds_df.groupby("event_id")
    summary = pd.DataFrame({
        "n_bonds": g["isin"].count(),
        "n_bonds_with_pre": g["pre_spread_bps"].count(),
        "pre_trade_days": g["pre_trade_days"].sum(),
        "pre_volume_inr": g["pre_volume_inr"].sum(min_count=1),
    })
    for label in labels:
        summary[f"{label}_n"] = g[f"{label}_abn_bps"].count()
        summary[f"{label}_days"] = g[f"{label}_days"].median()
        for metric in ("chg_bps", "abn_bps", "gap_cur_bps"):
            summary[f"{label}_{metric}"] = g[f"{label}_{metric}"].mean()
    event_df = event_info.merge(summary.reset_index(), on="event_id", how="left")
    event_df["n_bonds"] = event_df["n_bonds"].fillna(0).astype(int)
    bonds_df = event_info[["event_id", "issuer_id", "instrument_class", "mandate", "threshold",
                           "direction", "date"]].merge(bonds_df, on="event_id")
    return bonds_df, event_df
