"""Load exchange-reported corporate bond trades and aggregate them by ISIN and day.

OTC trades in Indian corporate bonds are reported to the NSE and BSE reporting
platforms, which publish them daily as trade-level or ISIN-day summaries
(weighted average price and yield, number of trades, traded value). Column
names and value units differ by venue and over time, so ``load_trades`` maps
headers by pattern and reports what it mapped. Check that report against the
first real file from each venue.

Prices are per 100 of face value; yields are in percent as reported.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from cr_strat.isin import is_valid_isin
from cr_strat.textparse import (
    UNIT_MULTIPLIERS,
    cell_str,
    detect_multiplier,
    parse_date_column,
    read_rows,
    to_number,
)

TRADE_COLUMNS = (
    "trade_date", "isin", "price", "yield_pct", "amount_inr", "n_trades", "venue",
    "settlement_date", "source_file",
)
DAILY_COLUMNS = (
    "date", "isin", "yield_pct", "price", "volume_inr", "n_trades", "yield_min", "yield_max", "venues",
)
_PREFERRED = re.compile(r"weighted|average|\bwap\b|\bway\b", re.I)


def _classify_header(text: str) -> str | None:
    t = text.lower()
    if re.search(r"\bisin\b", t):
        return "isin"
    if "settle" in t:
        return "settlement_date"
    if re.search(r"maturity|coupon|issue\s*date|record|face\s*value", t):
        return None
    if re.search(r"no\.?\s*of\s*trades|number\s*of\s*trades|trade\s*count|^trades$", t):
        return "n_trades"
    if "date" in t:
        return "trade_date"
    if re.search(r"yield|\bytm\b|\bway\b", t):
        return "yield_pct"
    if re.search(r"price|\bwap\b", t):
        return "price"
    if re.search(r"amount|value|deal\s*size|turnover|volume", t):
        return "amount"
    return None


def _map_header(row: list) -> tuple[dict[str, int], dict[str, str]] | None:
    """Column index per field, preferring weighted-average columns when several match."""
    candidates: dict[str, list[tuple[int, str]]] = {}
    for j, cell in enumerate(row):
        text = cell_str(cell)
        field = _classify_header(text)
        if field:
            candidates.setdefault(field, []).append((j, text))
    if "isin" not in candidates or not ({"price", "yield_pct"} & candidates.keys()):
        return None
    colmap, names = {}, {}
    for field, cols in candidates.items():
        j, text = next(((j, t) for j, t in cols if _PREFERRED.search(t)), cols[0])
        colmap[field], names[field] = j, text
    return colmap, names


def load_trades(
    path: str | Path,
    venue: str,
    *,
    trade_date: pd.Timestamp | str | None = None,
    amount_unit: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return ``(trades, report)`` for one venue file (CSV or workbook).

    ``trade_date`` fills the date for files that carry it only in the file
    name; ``amount_unit`` (a key of ``UNIT_MULTIPLIERS``) overrides the unit
    read from the value header.
    """
    path = Path(path)
    frames, report = [], []
    for sheet, rows in read_rows(path).items():
        header = next(((i, m) for i, row in enumerate(rows) if (m := _map_header(row))), None)
        if header is None:
            report.append({"source_file": path.name, "sheet": sheet, "status": "skipped",
                           "note": "no header row with ISIN and price/yield columns"})
            continue
        hdr, (colmap, names) = header
        if hdr + 1 >= len(rows):
            report.append({"source_file": path.name, "sheet": sheet, "header_row": hdr + 1,
                           "status": "empty", "note": "header row but no data rows"})
            continue
        # CSV rows can be ragged; give every row the header's columns.
        body = pd.DataFrame([list(r) for r in rows[hdr + 1:]]).reindex(columns=range(len(rows[hdr])))
        col = lambda field: body[colmap[field]] if field in colmap else pd.Series(np.nan, index=body.index)
        mult = UNIT_MULTIPLIERS[amount_unit] if amount_unit else (
            detect_multiplier(names["amount"]) if "amount" in names else None)

        df = pd.DataFrame({
            "isin": col("isin").map(cell_str).str.upper(),
            "price": col("price").map(to_number),
            "yield_pct": col("yield_pct").map(to_number),
            "amount_inr": col("amount").map(to_number) * (mult if mult is not None else np.nan),
            "n_trades": col("n_trades").map(to_number),
        })
        if "trade_date" in colmap:
            df["trade_date"] = parse_date_column(col("trade_date"))
        else:
            df["trade_date"] = pd.Timestamp(trade_date) if trade_date is not None else pd.NaT
        df["settlement_date"] = parse_date_column(col("settlement_date")) if "settlement_date" in colmap else pd.NaT
        df["venue"] = venue.upper()
        df["source_file"] = path.name

        valid_isin = df["isin"].map(is_valid_isin)
        valid_value = (df["price"] > 0) | df["yield_pct"].between(-5, 100)
        valid_date = df["trade_date"].notna()
        keep = valid_isin & valid_value & valid_date
        notes = []
        if mult is None and "amount" in colmap:
            notes.append("traded value unit not found; pass --amount-unit")
        if not valid_date[valid_isin].any():
            notes.append("no trade dates; pass --trade-date")
        report.append({
            "source_file": path.name, "sheet": sheet, "header_row": hdr + 1, "status": "parsed",
            "columns": "; ".join(f"{k}={v}" for k, v in sorted(names.items())),
            "amount_multiplier": mult, "rows_read": int(valid_isin.sum()), "rows_kept": int(keep.sum()),
            "dropped_bad_value": int((valid_isin & ~valid_value).sum()),
            "dropped_no_date": int((valid_isin & valid_value & ~valid_date).sum()),
            "note": "; ".join(notes) or None,
        })
        frames.append(df[keep])

    trades = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    trades = trades.reindex(columns=list(TRADE_COLUMNS))
    return trades, pd.DataFrame(report)


def read_trades(source) -> pd.DataFrame:
    """Read canonical trade CSVs written by ``load_trades`` (one path or several)."""
    if isinstance(source, pd.DataFrame):
        df = source.copy()
    elif isinstance(source, (str, Path)):
        df = pd.read_csv(source, dtype={"isin": str, "venue": str, "source_file": str})
    else:
        return pd.concat([read_trades(s) for s in source], ignore_index=True)
    for col in ("trade_date", "settlement_date"):
        df[col] = pd.to_datetime(df[col], format="ISO8601", errors="coerce")
    for col in ("price", "yield_pct", "amount_inr", "n_trades"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def clean_trades(
    trades: pd.DataFrame,
    *,
    min_amount_inr: float = 0.0,
    outlier_bps: float = 25.0,
    outlier_mads: float = 3.0,
    dedupe_across_venues: bool = False,
) -> pd.DataFrame:
    """Flag yield outliers within each ISIN-day and apply size and duplicate filters.

    A trade is an outlier when its yield is further from the ISIN-day median
    than both ``outlier_bps`` and ``outlier_mads`` median absolute deviations.
    ``dedupe_across_venues`` drops a trade that appears with the same date,
    ISIN, price, yield and amount on a second venue; whether that happens in
    the published data should be checked before turning it on.
    """
    df = trades[trades["yield_pct"].notna()].copy()
    if min_amount_inr > 0:
        df = df[df["amount_inr"].isna() | (df["amount_inr"] >= min_amount_inr)]
    if dedupe_across_venues:
        key = ["trade_date", "isin", "price", "yield_pct", "amount_inr"]
        first_venue = df.groupby(key, dropna=False)["venue"].transform("first")
        df = df[(df["venue"] == first_venue) | ~df.duplicated(key, keep=False)]
    grp = df.groupby(["trade_date", "isin"])["yield_pct"]
    median = grp.transform("median")
    mad = (df["yield_pct"] - median).abs().groupby([df["trade_date"], df["isin"]]).transform("median")
    deviation_bps = (df["yield_pct"] - median).abs() * 100
    df["outlier"] = (deviation_bps > outlier_bps) & (deviation_bps > outlier_mads * mad * 100)
    return df.reset_index(drop=True)


def isin_daily(trades: pd.DataFrame) -> pd.DataFrame:
    """One row per ISIN and trade date: value-weighted yield and price over non-outlier trades."""
    df = trades[~trades.get("outlier", pd.Series(False, index=trades.index)).astype(bool)].copy()
    weight = df["amount_inr"].where(df["amount_inr"] > 0)
    # Equal weights within an ISIN-day when any trade lacks a size.
    has_all = weight.notna().groupby([df["trade_date"], df["isin"]]).transform("all")
    df["w"] = weight.where(has_all, 1.0)
    df["wy"] = df["w"] * df["yield_pct"]
    df["wp"] = df["w"] * df["price"]
    df["wp_n"] = df["w"].where(df["price"].notna(), 0.0)
    df["n"] = df["n_trades"].fillna(1.0)
    g = df.groupby(["trade_date", "isin"])
    out = pd.DataFrame({
        "yield_pct": g["wy"].sum() / g["w"].sum(),
        "price": g["wp"].sum() / g["wp_n"].sum().replace(0.0, np.nan),
        "volume_inr": g["amount_inr"].sum(min_count=1),
        "n_trades": g["n"].sum(),
        "yield_min": g["yield_pct"].min(),
        "yield_max": g["yield_pct"].max(),
        "venues": g["venue"].agg(lambda v: ";".join(sorted(set(v)))),
    }).reset_index().rename(columns={"trade_date": "date"})
    return out[list(DAILY_COLUMNS)]
