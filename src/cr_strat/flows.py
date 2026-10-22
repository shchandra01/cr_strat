"""Trade-level first stage: what MFs sold and bought of an issuer's paper around each rating event.

``ownership`` infers selling from month-end or fortnightly portfolio snapshots.
AMFI's daily debt-transaction disclosure (``amfi_debt_trades``) shows it
directly. For each event and each window of calendar days around the event
date, this sums what the schemes the mandate binds sold and bought of the
issuer's paper and, separately, what all other MF schemes did. Net selling is
expressed as a share of the issuer's outstanding face in that instrument class.

ISINs are chosen exactly as in ``ownership``: the security master's ISINs for
the event's issuer and instrument class, issued by and still live on the event
date. A trade is dated by its trade date (settlement date when that is blank)
and sized at face (quantity x face value per unit) where the disclosure gives
it, otherwise at traded value; ``value_basis`` says which. Only transfers of
the security count (``FLOW_DEAL_TYPES``), not repo, TREPS, maturities or swaps.

Before about 22-Sep-2020 the disclosure has no buy/sell flag. A window holding
any trade without one has ``direction_available_<w>`` False and blank sold,
bought and net figures; its gross traded value (``<group>_traded_inr_<w>``) is
still given. Inter-scheme
transfers count as sales and purchases, since they move paper out of the
selling scheme; ``<group>_interscheme_share_<w>`` says how much of the selling
they were. ``days_with_data_<w>`` counts the days in the window with any trade
in the input, so that "no selling" can be told from "no files downloaded";
windows without data are left blank and flagged ``direction_available_<w>``
False. Files come by settlement date, so trades dated near the end of the data
show up only once the next settlement days' files are loaded.
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

from cr_strat.amfi_debt_trades import read_mf_trades
from cr_strat.mandates import Mandate
from cr_strat.ownership import load_events, load_securities

FLOW_DEAL_TYPES = ("MARKET", "OFF_MARKET", "INTER_SCHEME", "PRIMARY", "BUYBACK")
DEFAULT_WINDOWS = ((-30, -1), (0, 10), (0, 30), (0, 60), (0, 90))
GROUPS = ("constrained", "other_mf")
GROUP_METRICS = (
    "traded_inr", "sold_inr", "bought_inr", "sold_share", "net_sold_share", "n_sellers", "interscheme_share",
)
_EVENT_COLUMNS = (
    "event_id", "issuer_id", "instrument_class", "mandate", "threshold", "direction", "date",
    "n_isins", "outstanding_face_inr", "outstanding_coverage", "n_isins_traded", "value_basis",
    "uncategorised_share", "note",
)


def window_label(window: tuple[int, int]) -> str:
    """Column suffix for a window: (-30, -1) -> "m30_m1", (0, 10) -> "0_10"."""
    return "_".join(f"m{-d}" if d < 0 else str(d) for d in window)


def parse_windows(text: str) -> tuple[tuple[int, int], ...]:
    """Parse "-30:-1,0:10,0:30" into ((-30, -1), (0, 10), (0, 30))."""
    windows = []
    for part in text.split(","):
        start, end = (int(x) for x in part.split(":"))
        windows.append((start, end))
    return _check_windows(windows)


def _check_windows(windows: Iterable[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    windows = tuple((int(a), int(b)) for a, b in windows)
    bad = [w for w in windows if w[0] > w[1]]
    if bad or not windows:
        raise ValueError(f"windows must be (start, end) with start <= end, got {bad or 'none'}")
    return windows


def flow_columns(windows: Iterable[tuple[int, int]] = DEFAULT_WINDOWS) -> list[str]:
    """Output columns of ``event_flows`` for these windows, in order."""
    cols = list(_EVENT_COLUMNS)
    for window in _check_windows(windows):
        label = window_label(window)
        cols += [f"n_trades_{label}", f"days_with_data_{label}", f"direction_available_{label}"]
        cols += [f"{g}_{m}_{label}" for g in GROUPS for m in GROUP_METRICS]
    return cols


def _prepare(mf_trades, deal_types: Iterable[str]) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    """Transfer trades with a day and a size, and every day that has any trade in the input."""
    trades = read_mf_trades(mf_trades)
    trades = trades.assign(day=trades["trade_date"].fillna(trades["settlement_date"]))
    data_days = pd.DatetimeIndex(trades["day"].dropna().unique()).sort_values()
    keep = trades["isin"].notna() & trades["deal_type"].isin(tuple(deal_types)) & trades["day"].notna()
    flows = trades.loc[keep]
    face = flows["face_traded_inr"]
    flows = flows.assign(
        amount=face.fillna(flows["value_inr"]),
        basis=np.where(face.notna(), "face", "value"),
    )
    return flows[flows["amount"].notna()], data_days


def _group_flows(rows: pd.DataFrame, outstanding: float, directional: bool) -> dict[str, float]:
    share = (lambda v: v / outstanding if pd.notna(outstanding) and outstanding > 0 else np.nan)
    out = dict.fromkeys(GROUP_METRICS, np.nan)
    out["traded_inr"] = rows["amount"].sum()
    if not directional:
        return out
    sells = rows[rows["direction"] == "SELL"]
    sold = sells["amount"].sum()
    bought = rows.loc[rows["direction"] == "BUY", "amount"].sum()
    out.update({
        "sold_inr": sold,
        "bought_inr": bought,
        "sold_share": share(sold),
        "net_sold_share": share(sold - bought),
        "n_sellers": sells["holder_id"].nunique(),
        "interscheme_share": (sells.loc[sells["deal_type"] == "INTER_SCHEME", "amount"].sum() / sold
                              if sold > 0 else np.nan),
    })
    return out


def event_flows(
    events,
    mf_trades,
    securities,
    mandates: list[Mandate],
    *,
    windows: Iterable[tuple[int, int]] = DEFAULT_WINDOWS,
    deal_types: Iterable[str] = FLOW_DEAL_TYPES,
) -> pd.DataFrame:
    """One row per event: MF selling and buying of the event's ISINs in each window around the event.

    ``windows`` are (start, end) calendar days relative to the event date,
    both ends included. ``mf_trades`` is ``load_amfi_debt_trades`` output (a
    frame or CSV paths) with ``category`` attached, so that the mandate can
    tell constrained schemes from the rest.
    """
    windows = _check_windows(windows)
    events = load_events(events)
    sec = load_securities(securities)
    flows, data_days = _prepare(mf_trades, deal_types)
    by_isin = dict(tuple(flows.groupby("isin", sort=False)))
    by_name = {m.name: m for m in mandates}
    sec_by_key = {k: g for k, g in sec.groupby(["issuer_id", "instrument_class"])}
    span = (min(a for a, _ in windows), max(b for _, b in windows))

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
        binds_mf = any(rule.holder_type.upper() == "MF" for rule in mandate.holders)
        if not binds_mf:
            row["note"] = "mandate binds no MF schemes"

        parts = [by_isin[i] for i in live["isin"] if i in by_isin]
        rows = pd.concat(parts) if parts else flows.iloc[0:0]
        flags = [mandate.applies_to(t, c) for t, c in zip(rows["holder_type"], rows["category"])]
        rows = rows.assign(constrained=pd.Series(flags, index=rows.index, dtype=bool))
        around = rows[rows["day"].between(ev.date + pd.Timedelta(days=span[0]),
                                          ev.date + pd.Timedelta(days=span[1]))]
        total = around["amount"].abs().sum()
        row["n_isins_traded"] = around["isin"].nunique()
        row["value_basis"] = "+".join(sorted(around["basis"].unique())) or None
        row["uncategorised_share"] = (around.loc[around["category"].isna(), "amount"].abs().sum() / total
                                      if total > 0 else np.nan)

        for window in windows:
            label = window_label(window)
            start, end = (ev.date + pd.Timedelta(days=d) for d in window)
            sel = rows[rows["day"].between(start, end)]
            days = int(((data_days >= start) & (data_days <= end)).sum())
            directional = days > 0 and bool(sel["direction"].notna().all())
            row[f"n_trades_{label}"] = len(sel)
            row[f"days_with_data_{label}"] = days
            row[f"direction_available_{label}"] = directional
            if days == 0:
                continue
            groups = {"constrained": sel[sel["constrained"]],
                      "other_mf": sel[~sel["constrained"] & (sel["holder_type"] == "MF")]}
            for group, group_rows in groups.items():
                if group == "constrained" and not binds_mf:
                    continue
                for metric, value in _group_flows(group_rows, outstanding, directional).items():
                    row[f"{group}_{metric}_{label}"] = value
        out.append(row)
    return pd.DataFrame(out).reindex(columns=flow_columns(windows))
