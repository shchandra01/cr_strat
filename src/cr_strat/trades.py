"""Load exchange-reported corporate bond trades, check their yields against their prices, aggregate by ISIN and day.

OTC trades in Indian corporate bonds are reported to the NSE and BSE reporting
platforms, which publish them daily as trade-level or ISIN-day summaries
(weighted average price and yield, number of trades, traded value). Column
names and value units differ by venue and over time, so ``load_trades`` maps
headers by pattern and reports what it mapped. Check that report against the
first real file from each venue.

Prices are per 100 of face value; yields are in percent as reported.

**Checking reported yields** (``check_trade_yields``). A reported yield is the reporting dealer's: its type (to
maturity, call or put) is the dealer's choice (SEBI's NCS master circular, Ch. XVI Table 1), and a print can carry a
dirty price or a yield on another basis. Each trade's yield is recomputed from its price and the bond's terms through
``corporates`` (SEBI actual/actual coupons, record dates, annual compounding on actual days over 365, the money-market
rule in the last year), first on the standard reading (a clean price, the bond's own flow dates: due dates from 18 Aug
2025, payment dates before; to maturity, or to worst with ``workout="worst"``), then on every other reading: the price
read as dirty, due or payment dates, to each call and put still ahead, compounded instead of the money-market rule.
The reading nearest the reported yield is kept: ``ok`` within ``tolerance_bp`` (1 bp, UNVERIFIED default),
``mismatch`` beyond it, ``no_terms`` without the bond's terms, ``no_price`` and ``no_yield`` without either, ``error``
when the bond cannot be priced for that settlement (inside the redemption record window, say). On one synthetic bond
the misreadings are this large: payment dates for due dates +0.504 bp, the price read as dirty +256.97 bp (the
corporates spec's T19). A trade without a settlement date is taken to settle T+1 on the bond's payment calendar
(noted). ``isin_daily_checked`` turns checked trades into ISIN-day rows whose yield is the value-weighted recomputed
yield (the mutual-fund rule's VWAY, SEBI's MF master circular 10.2.3), with the reported one beside it. Existing
outputs are unchanged: the check appends columns (``CHECK_COLUMNS``) and ``isin_daily`` is untouched.
"""

from __future__ import annotations

import math
import re
from dataclasses import replace
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from cr_strat.calendars import mumbai_settlement_calendar
from cr_strat.conventions import as_date
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
CHECK_COLUMNS = (
    "settlement_used", "recomputed_yield_pct", "workout_date", "workout_kind", "standard_diff_bp", "nearest_reading",
    "nearest_yield_pct", "yield_diff_bp", "yield_check", "check_notes",
)
CHECK_STATUSES = ("ok", "mismatch", "no_terms", "no_price", "no_yield", "error")
CHECKED_DAILY_COLUMNS = ("reported_yield_pct", "recomputed_yield_pct", "yield_check", "workout_date")
WORKOUT_RULES = ("maturity", "worst")
TOLERANCE_BP = 1.0  # UNVERIFIED default: a reported yield within 1 bp of a reading counts as that reading
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


# ---------------------------------------------------------------- reported yields checked against prices


def _blank(value) -> bool:
    if value is None or (isinstance(value, str) and not value.strip()):
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _readings(bond, s: date, price: float, workout: str):
    """(standard yield, workout date, workout kind, [(reading, yield)]) of one trade's price; standard first."""
    from cr_strat.corporates import CorporateBond

    if not isinstance(bond, CorporateBond):
        raise TypeError("the trade check reads corporates.CorporateBond terms")
    target, kind, when = bond, "maturity", bond.maturity
    if workout == "worst":
        w = bond.yield_to_worst(s, price)
        if w.date != bond.maturity:
            target, kind, when = bond.to_workout(w.date, w.price), w.kind, w.date
    standard = target.yield_from_price(price, s)
    own = "due" if bond.uses_due_dates(s) else "payment"
    ends = [("maturity", bond)] + [(f"{k} {d}", bond.to_workout(d, p)) for k, d, p in bond.workouts(s)
                                   if k != "maturity"]
    out = []
    for end_name, end_bond in ends:
        for dates in (own, "payment" if own == "due" else "due"):
            dated = end_bond if dates == own else replace(end_bond, flow_dates=dates)
            for rule in ("mm", "compound"):
                ruled = dated if rule == "mm" else replace(dated, conventions=replace(dated.conventions,
                                                                                      final_period="compound"))
                for basis in ("clean", "dirty"):
                    try:
                        y = (ruled.yield_from_price(price, s) if basis == "clean"
                             else ruled.yield_from_dirty_price(price, s))
                    except (ValueError, RuntimeError):
                        continue
                    name = "/".join(x for x in (basis, f"{dates} dates", end_name,
                                                "compounded" if rule == "compound" else "") if x)
                    out.append((name, y))
    seen, unique = set(), []
    for name, y in out:  # the same yield under several names (no money-market flow, say): keep the first, simplest
        key = round(y, 12)
        if key not in seen:
            seen.add(key)
            unique.append((name, y))
    return standard, when, kind, unique


def check_trade_yields(trades: pd.DataFrame, terms, *, schedule=None, options=None, amortisation=None,
                       tolerance_bp: float = TOLERANCE_BP, workout: str = "maturity", calendar="default",
                       rates: dict | None = None) -> pd.DataFrame:
    """``trades`` (``TRADE_COLUMNS``: ``isin``, ``price``, ``yield_pct``, ``trade_date``, ``settlement_date``) with
    ``CHECK_COLUMNS`` appended: each trade's yield recomputed from its price and the bond's terms (module docstring).

    ``terms``: the corporate terms table (``corporates.read_corporate_terms``' layout) with its optional schedule,
    options and amortisation tables; ``rates``: rate steps by ISIN for ``frb_corp`` rows. ``recomputed_yield_pct``
    is the standard reading (clean price, the bond's own flow dates, to maturity or to worst); ``nearest_reading``
    and ``yield_diff_bp`` (reported less nearest) say which reading the reported yield matches, ``yield_check`` how
    well. The trades keep their order and columns.
    """
    from cr_strat.corporates import (
        corporate_from_terms,
        read_amortisation,
        read_corporate_options,
        read_corporate_schedule,
        read_corporate_terms,
    )

    if workout not in WORKOUT_RULES:
        raise ValueError(f"workout must be one of {WORKOUT_RULES}, not {workout!r}")
    tol = float(tolerance_bp)
    if not (math.isfinite(tol) and tol >= 0):
        raise ValueError(f"tolerance_bp must be a number >= 0, not {tolerance_bp!r}")
    missing = [c for c in ("isin", "price", "yield_pct", "trade_date") if c not in trades]
    if missing:
        raise ValueError(f"the trades lack {missing}")
    table = read_corporate_terms(terms)
    sched = None if schedule is None else read_corporate_schedule(schedule)
    opts = None if options is None else read_corporate_options(options)
    amort = None if amortisation is None else read_amortisation(amortisation)
    by_isin = {r["isin"]: r for r in table.to_dict("records")}
    bonds: dict[str, object] = {}
    fallback = mumbai_settlement_calendar().lenient()
    rows = []
    for rec in trades.to_dict("records"):
        out = dict.fromkeys(CHECK_COLUMNS, math.nan) | {"settlement_used": None, "workout_date": None,
                                                        "workout_kind": "", "nearest_reading": "",
                                                        "check_notes": ""}
        isin = "" if _blank(rec.get("isin")) else str(rec["isin"]).strip().upper()
        notes = []
        try:
            if isin not in by_isin:
                out["yield_check"] = "no_terms"
                rows.append(out)
                continue
            if isin not in bonds:
                try:
                    bond = corporate_from_terms(by_isin[isin], schedule=sched, options=opts, amortisation=amort,
                                                calendar=calendar)
                    if by_isin[isin]["kind"] == "frb_corp":
                        if not rates or isin not in rates:
                            raise ValueError(f"{isin}: a frb_corp bond needs its rate steps (rates=)")
                        bond = bond.with_rates(rates[isin])
                    bonds[isin] = bond
                except (ValueError, TypeError, NotImplementedError) as exc:
                    bonds[isin] = exc
            bond = bonds[isin]
            if isinstance(bond, Exception):
                raise bond
            if _blank(rec.get("settlement_date")):
                cal = bond.calendar or fallback
                s = cal.settlement_date(as_date(rec["trade_date"]), bond.conventions.settlement_lag)
                notes.append(f"settlement taken as T+{bond.conventions.settlement_lag}")
            else:
                s = as_date(rec["settlement_date"])
            out["settlement_used"] = s
            if _blank(rec.get("price")):
                out["yield_check"] = "no_price"
                rows.append(out | {"check_notes": "; ".join(notes)})
                continue
            price = float(rec["price"])
            standard, when, kind, readings = _readings(bond, s, price, workout)
            out.update(recomputed_yield_pct=standard, workout_date=when, workout_kind=kind)
            if _blank(rec.get("yield_pct")):
                out["yield_check"] = "no_yield"
            else:
                reported = float(rec["yield_pct"])
                name, y = min(readings, key=lambda r: abs(r[1] - reported))
                out.update(standard_diff_bp=(reported - standard) * 100, nearest_reading=name, nearest_yield_pct=y,
                           yield_diff_bp=(reported - y) * 100,
                           yield_check="ok" if abs(reported - y) * 100 <= tol + 1e-9 else "mismatch")
        except (ValueError, TypeError, NotImplementedError, RuntimeError) as exc:
            out.update(yield_check="error")
            notes.append(str(exc))
        out["check_notes"] = "; ".join(notes)
        rows.append(out)
    checked = pd.DataFrame(rows, columns=list(CHECK_COLUMNS), index=trades.index)
    result = trades.copy()
    for column in CHECK_COLUMNS:
        result[column] = checked[column]
    return result


def recomputed_trades(checked: pd.DataFrame) -> pd.DataFrame:
    """Checked trades with ``yield_pct`` the recomputed yield and the reported one kept as ``reported_yield_pct``;
    trades without a recomputed yield (no terms, no price, an error) are left out. Feed it to ``clean_trades``
    (outliers on the recomputed yields) and then ``isin_daily_checked``."""
    if "recomputed_yield_pct" not in checked:
        raise ValueError("check the trades first (check_trade_yields)")
    keep = checked[checked["recomputed_yield_pct"].notna()].copy()
    keep["reported_yield_pct"] = keep["yield_pct"]
    keep["yield_pct"] = keep["recomputed_yield_pct"]
    return keep.reset_index(drop=True)


def isin_daily_checked(trades: pd.DataFrame) -> pd.DataFrame:
    """``isin_daily`` of recomputed trades (``recomputed_trades``, outliers flagged by ``clean_trades``) with
    ``CHECKED_DAILY_COLUMNS`` appended: the value-weighted reported yield, the recomputed one (the day's
    ``yield_pct``), the day's check (``ok`` when every trade's is, else the count of each status, e.g.
    ``mismatch:1;ok:3``) and the commonest workout date."""
    need = ("reported_yield_pct", "yield_check", "workout_date")
    missing = [c for c in need if c not in trades]
    if missing:
        raise ValueError(f"the trades lack {missing}: pass recomputed_trades(check_trade_yields(...))")
    daily = isin_daily(trades)
    live = trades[~trades.get("outlier", pd.Series(False, index=trades.index)).astype(bool)]
    reported = live[live["reported_yield_pct"].notna()]
    rep = isin_daily(reported.assign(yield_pct=reported["reported_yield_pct"]))[["date", "isin", "yield_pct"]]
    rep = rep.rename(columns={"yield_pct": "reported_yield_pct"})

    def summary(group: pd.DataFrame) -> pd.Series:
        counts = group["yield_check"].value_counts()
        check = "ok" if set(counts.index) == {"ok"} else ";".join(f"{k}:{counts[k]}" for k in sorted(counts.index))
        dates = group["workout_date"].dropna().map(as_date)
        mode = sorted(dates.value_counts().items(), key=lambda kv: (-kv[1], kv[0]))
        return pd.Series({"yield_check": check, "workout_date": mode[0][0] if mode else None})

    keys = live.rename(columns={"trade_date": "date"})
    extra = keys.groupby(["date", "isin"])[["yield_check", "workout_date"]].apply(summary).reset_index()
    out = daily.merge(rep, on=["date", "isin"], how="left").merge(extra, on=["date", "isin"], how="left")
    out["recomputed_yield_pct"] = out["yield_pct"]
    return out[[*DAILY_COLUMNS, *CHECKED_DAILY_COLUMNS]]
