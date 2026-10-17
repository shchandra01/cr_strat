"""Constrained ownership at each rating event, and whether those holders sold.

For every event this measures, from the last holdings snapshot before the
event date:

* how much of the issuer's outstanding paper in that instrument class sat with
  holders the mandate binds (``constrained_share``): the pressure variable;
* how those holdings changed 30/60/90 days later (``constrained_chg_*``): the
  first stage. If constrained holders did not sell, there is no forced-selling
  trade. Unconstrained MF holders are measured the same way as a control.

Holdings are valued at face (quantity x face value per unit from the security
master) where possible and at market value otherwise; ``value_basis`` says
which. Only ISINs still outstanding through the horizon are compared, so
maturities do not look like selling.
"""

from __future__ import annotations

import warnings
from collections.abc import Iterable
from pathlib import Path

import numpy as np
import pandas as pd

from cr_strat.mandates import Mandate

_HOLDER = ["holder_type", "holder_id"]


def _read(source: str | Path | pd.DataFrame | Iterable) -> pd.DataFrame:
    if isinstance(source, pd.DataFrame):
        return source.copy()
    if isinstance(source, (str, Path)):
        return pd.read_csv(source, dtype=str)
    return pd.concat([_read(s) for s in source], ignore_index=True)


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df[col], errors="coerce") if col in df else pd.Series(np.nan, index=df.index)


def load_securities(source) -> pd.DataFrame:
    """Security master: isin, issuer_id, instrument_class, face_value_inr,
    outstanding_face_inr, issue_date, maturity_date (dates ISO, optional)."""
    df = _read(source)
    df.columns = [c.strip().lower() for c in df.columns]
    missing = [c for c in ("isin", "issuer_id", "instrument_class") if c not in df]
    if missing:
        raise ValueError(f"security master missing columns: {missing}")
    df["isin"] = df["isin"].str.strip().str.upper()
    df["issuer_id"] = df["issuer_id"].astype(str).str.strip()
    df["instrument_class"] = df["instrument_class"].str.strip().str.upper()
    df["face_value_inr"] = _num(df, "face_value_inr")
    df["outstanding_face_inr"] = _num(df, "outstanding_face_inr")
    for col in ("issue_date", "maturity_date"):
        if col not in df:
            df[col] = pd.NaT
            continue
        given = df[col].notna() & (df[col].astype(str).str.strip() != "")
        df[col] = pd.to_datetime(df[col], format="ISO8601", errors="coerce")
        bad = int((given & df[col].isna()).sum())
        if bad:
            warnings.warn(f"security master: {bad} {col} values are not ISO dates (YYYY-MM-DD) and were ignored",
                          stacklevel=2)
    return df.drop_duplicates("isin", keep="last").reset_index(drop=True)


def load_holdings(source) -> pd.DataFrame:
    """Holdings: as_of_date, holder_type, holder_id, isin, plus optional category,
    quantity, market_value_inr. ``parse_mf_portfolio`` writes this format."""
    df = _read(source)
    df.columns = [c.strip().lower() for c in df.columns]
    missing = [c for c in ("as_of_date", "holder_type", "holder_id", "isin") if c not in df]
    if missing:
        raise ValueError(f"holdings missing columns: {missing}")
    df["as_of_date"] = pd.to_datetime(df["as_of_date"], format="ISO8601", errors="coerce")
    df["holder_type"] = df["holder_type"].str.strip().str.upper()
    df["isin"] = df["isin"].str.strip().str.upper()
    if "category" not in df:
        df["category"] = None
    df["quantity"] = _num(df, "quantity")
    df["market_value_inr"] = _num(df, "market_value_inr")
    return df[df["as_of_date"].notna()].reset_index(drop=True)


def load_events(source) -> pd.DataFrame:
    df = _read(source)
    df["date"] = pd.to_datetime(df["date"], format="ISO8601", errors="coerce")
    df["issuer_id"] = df["issuer_id"].astype(str).str.strip()
    return df


def _held_by_holder(rows: pd.DataFrame) -> pd.Series:
    return rows.groupby(_HOLDER)["held"].sum()


def _follow_up(
    base: pd.DataFrame,
    snapshots: pd.DataFrame,
    rows: pd.DataFrame,
    isins: set[str],
    event_date: pd.Timestamp,
    target: pd.Timestamp,
    tolerance: pd.Timedelta,
) -> tuple[float, float]:
    """Change in holdings of ``isins`` for the holders in ``base``; returns (change, coverage)."""
    before = _held_by_holder(rows[rows["isin"].isin(isins)].merge(base[_HOLDER + ["as_of_date"]]))
    before = before[before > 0]
    if before.empty:
        return np.nan, np.nan
    snap = snapshots[(snapshots["as_of_date"] > event_date)
                     & ((snapshots["as_of_date"] - target).abs() <= tolerance)]
    snap = snap.assign(dist=(snap["as_of_date"] - target).abs())
    snap = snap.sort_values("dist").drop_duplicates(_HOLDER)
    snap = snap.set_index(_HOLDER).reindex(before.index).dropna(subset=["as_of_date"]).reset_index()
    if snap.empty:
        return np.nan, 0.0
    after_rows = rows[rows["isin"].isin(isins)].merge(snap[_HOLDER + ["as_of_date"]])
    after = _held_by_holder(after_rows).reindex(pd.MultiIndex.from_frame(snap[_HOLDER])).fillna(0.0)
    covered = before.reindex(after.index)
    return after.sum() / covered.sum() - 1.0, covered.sum() / before.sum()


def event_ownership(
    events,
    holdings,
    securities,
    mandates: list[Mandate],
    *,
    max_staleness_days: int = 45,
    horizons: tuple[int, ...] = (30, 60, 90),
    tolerance_days: int = 15,
) -> pd.DataFrame:
    """One row per event with ownership at the event and holdings changes after it."""
    events = load_events(events)
    sec = load_securities(securities)
    hold = load_holdings(holdings)
    by_name = {m.name: m for m in mandates}

    hold = hold.merge(sec[["isin", "face_value_inr"]], on="isin", how="left")
    face = hold["quantity"] * hold["face_value_inr"]
    hold["value_basis"] = np.where(face.notna(), "face", "market")
    hold["held"] = face.fillna(hold["market_value_inr"])
    snapshots = hold[_HOLDER + ["category", "as_of_date"]].drop_duplicates(_HOLDER + ["as_of_date"])
    staleness = pd.Timedelta(days=max_staleness_days)
    tolerance = pd.Timedelta(days=tolerance_days)
    sec_by_key = {k: g for k, g in sec.groupby(["issuer_id", "instrument_class"])}

    out = []
    for ev in events.itertuples(index=False):
        row = {"event_id": ev.event_id, "issuer_id": ev.issuer_id,
               "instrument_class": ev.instrument_class, "mandate": ev.mandate,
               "threshold": ev.threshold, "direction": ev.direction, "date": ev.date}
        mandate = by_name.get(ev.mandate)
        issued = sec_by_key.get((ev.issuer_id, ev.instrument_class), sec.iloc[0:0])
        live = issued[(issued["issue_date"].isna() | (issued["issue_date"] <= ev.date))
                      & (issued["maturity_date"].isna() | (issued["maturity_date"] > ev.date))]
        row["n_isins"] = len(live)
        if mandate is None or live.empty or pd.isna(ev.date):
            row["note"] = "unknown mandate" if mandate is None else "no live ISINs in security master"
            out.append(row)
            continue
        outstanding = live["outstanding_face_inr"].sum(min_count=1)
        row["outstanding_face_inr"] = outstanding
        row["outstanding_coverage"] = live["outstanding_face_inr"].notna().mean()

        base = snapshots[(snapshots["as_of_date"] < ev.date)
                         & (snapshots["as_of_date"] >= ev.date - staleness)]
        base = base.sort_values("as_of_date").drop_duplicates(_HOLDER, keep="last")
        flags = [mandate.applies_to(t, c) for t, c in zip(base["holder_type"], base["category"])]
        # Explicit bool dtype: an empty float column would turn the masks below into column lookups.
        base = base.assign(constrained=pd.Series(flags, index=base.index, dtype=bool))
        rows = hold[hold["isin"].isin(set(live["isin"]))]
        at_base = rows.merge(base[_HOLDER + ["as_of_date", "constrained"]])
        held = at_base.groupby(_HOLDER + ["constrained"])["held"].sum().reset_index()
        is_constrained = held["constrained"].astype(bool)
        constrained = held.loc[is_constrained]
        other_mf = held.loc[~is_constrained & (held["holder_type"] == "MF")]

        share = (lambda v: v / outstanding if outstanding and outstanding > 0 else np.nan)
        row.update({
            "baseline_from": base["as_of_date"].min(),
            "baseline_to": base["as_of_date"].max(),
            "value_basis": "+".join(sorted(at_base["value_basis"].unique())) or None,
            "constrained_held_inr": constrained["held"].sum(),
            "constrained_share": share(constrained["held"].sum()),
            "n_constrained_holders": int((constrained["held"] > 0).sum()),
            "top_constrained_holder_share": share(constrained["held"].max()) if len(constrained) else np.nan,
            "other_mf_held_inr": other_mf["held"].sum(),
            "other_mf_share": share(other_mf["held"].sum()),
            "all_held_share": share(held["held"].sum()),
        })

        for h in horizons:
            target = ev.date + pd.Timedelta(days=h)
            surviving = set(live.loc[live["maturity_date"].isna()
                                     | (live["maturity_date"] > target + tolerance), "isin"])
            for label, group in (("constrained", base.loc[base["constrained"]]),
                                 ("other_mf", base.loc[~base["constrained"] & (base["holder_type"] == "MF")])):
                chg, cov = _follow_up(group, snapshots, rows, surviving, ev.date, target, tolerance)
                row[f"{label}_chg_{h}d"] = chg
                row[f"{label}_cov_{h}d"] = cov
        out.append(row)
    return pd.DataFrame(out)
