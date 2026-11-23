"""Load exchange-reported corporate bond trades, check their yields against their prices, aggregate by ISIN and day.

OTC trades in Indian corporate bonds are reported to the NSE and BSE reporting
platforms, which publish them daily as trade-level or ISIN-day summaries
(weighted average price and yield, number of trades, traded value). Column
names and value units differ by venue and over time, so ``load_trades`` maps
headers by pattern and reports what it mapped. Check that report against the
first real file from each venue.

Prices are per 100 of face value; yields are in percent as reported. A file
in SEBI's reporting format (NCS master circular Ch. XVI Table 1) also carries
each deal's type (direct, brokered or an inter-scheme transfer, IST) and the
reported yield's type (YTM, YTC, YTP): both are kept (``deal_type``,
``yield_type``) when the file has them, and ``isin_daily`` leaves inter-scheme
transfers out, as the mutual-fund valuation rule leaves them out of the traded
yields it uses (SEBI's MF master circular, 10.2.3).

**Checking reported yields** (``check_trade_yields``). A reported yield is the reporting dealer's: its type (to
maturity, call or put) is the dealer's choice (SEBI's NCS master circular, Ch. XVI Table 1), and a print can carry a
dirty price or a yield on another basis. Each trade's yield is recomputed from its price and the bond's terms through
``corporates`` (SEBI actual/actual coupons, record dates, annual compounding on actual days over 365, the money-market
rule in the last year), the terms as known at 00:00 on the trade date by default (an exercise, or a decision not to
exercise, public by then; ``as_of`` or a trade's ``as_of`` cell otherwise), first on the standard reading (a clean
price per 100 of the face outstanding, ``price_basis``, UNVERIFIED default: exchanges trade a partly redeemed bond
on its reduced face; the bond's own flow dates: due dates from 18 Aug 2025, payment dates before; to maturity, or to
worst with ``workout="worst"``), then on every other reading: the price read as dirty or on the other face basis,
due or payment dates, to each call and put still ahead, compounded instead of the money-market rule. A declared
``yield_type`` limits the readings to its workout. The reading nearest the reported yield is kept: ``ok`` within
``tolerance_bp`` (1 bp, UNVERIFIED default) on the standard price reading; ``dirty_price`` or ``face_basis`` when it
matches only with the price read as dirty or on the other face basis, the recomputed yield then read so (a dirty
print would otherwise enter the panel hundreds of bp off, called ok); ``mismatch`` beyond the tolerance;
``no_terms`` without the bond's terms, ``no_price`` and ``no_yield`` without either, ``error`` when the bond cannot be
priced for that settlement (inside the redemption record window, say). On one synthetic bond the misreadings are
this large: payment dates for due dates +0.504 bp, the price read as dirty +256.97 bp (the corporates spec's T19). A
trade without a settlement date is taken to settle T+1 on the bond's payment calendar (noted). ``isin_daily_checked``
turns checked trades into ISIN-day rows whose yield is the value-weighted recomputed yield (a value-weighted yield as
the mutual-fund rule's VWAY is, SEBI's MF master circular 10.2.3, inter-scheme transfers left out; the rule's own
filters on trade size and its waterfall are not built), with the reported one beside it and the day's statuses
counted. ``recomputed_trades`` leaves out trades of bonds without terms, or keeps them on their reported yields
(``without_terms="reported"``). Existing outputs are unchanged: the check appends columns (``CHECK_COLUMNS``) and
``isin_daily`` is untouched for files without a deal type.
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
OPTIONAL_TRADE_COLUMNS = ("deal_type", "yield_type")  # kept when a file has them (SEBI's format: Ch. XVI Table 1)
EXCLUDED_DEAL_TYPES = ("IST",)  # inter-scheme transfers: left out of the daily yields (MF master circular 10.2.3)
DAILY_COLUMNS = (
    "date", "isin", "yield_pct", "price", "volume_inr", "n_trades", "yield_min", "yield_max", "venues",
)
CHECK_COLUMNS = (
    "settlement_used", "recomputed_yield_pct", "workout_date", "workout_kind", "standard_diff_bp", "nearest_reading",
    "nearest_yield_pct", "yield_diff_bp", "yield_check", "known_as_of", "calendar_projected", "check_notes",
)
CHECK_STATUSES = ("ok", "dirty_price", "face_basis", "mismatch", "no_terms", "no_price", "no_yield", "error")
PRICE_BASIS = "outstanding"  # UNVERIFIED: a print of a partly redeemed bond is per 100 of the face still outstanding
YIELD_TYPES = {"YTM": "maturity", "YTC": "call", "YTP": "put"}  # SEBI's reporting format (Ch. XVI Table 1)
CHECKED_DAILY_COLUMNS = ("reported_yield_pct", "recomputed_yield_pct", "yield_check", "workout_date")
WORKOUT_RULES = ("maturity", "worst")
TOLERANCE_BP = 1.0  # UNVERIFIED default: a reported yield within 1 bp of a reading counts as that reading
_PREFERRED = re.compile(r"weighted|average|\bwap\b|\bway\b", re.I)


def _classify_header(text: str) -> str | None:
    t = text.lower()
    if re.search(r"\bisin\b", t):
        return "isin"
    if re.search(r"\btype\b", t):  # SEBI's reporting format (NCS master circular Ch. XVI Table 1): deal and yield types
        if "yield" in t:
            return "yield_type"
        if "deal" in t:
            return "deal_type"
        return None
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
        for field in OPTIONAL_TRADE_COLUMNS:
            if field in colmap:
                df[field] = col(field).map(cell_str).str.strip()
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
    trades = trades.reindex(columns=list(TRADE_COLUMNS) + [c for c in OPTIONAL_TRADE_COLUMNS if c in trades])
    return trades, pd.DataFrame(report)


def read_trades(source) -> pd.DataFrame:
    """Read canonical trade CSVs written by ``load_trades`` (one path or several)."""
    if isinstance(source, pd.DataFrame):
        df = source.copy()
    elif isinstance(source, (str, Path)):
        df = pd.read_csv(source, dtype={"isin": str, "venue": str, "source_file": str, "deal_type": str,
                                        "yield_type": str})
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


def is_excluded_deal(value) -> bool:
    """True for a deal type the daily yields leave out (``EXCLUDED_DEAL_TYPES``: an inter-scheme transfer, written
    ``IST`` or in words)."""
    text = cell_str(value).strip().upper()
    words = re.sub(r"[^A-Z]+", " ", text).split()
    return text in EXCLUDED_DEAL_TYPES or ("INTER" in " ".join(words) and "SCHEME" in " ".join(words))


def isin_daily(trades: pd.DataFrame) -> pd.DataFrame:
    """One row per ISIN and trade date: value-weighted yield and price over non-outlier trades.

    A trade whose ``deal_type`` is an inter-scheme transfer (``is_excluded_deal``) is left out, as the
    mutual-fund rule leaves it out of the traded yields it values with (SEBI's MF master circular, 10.2.3); files
    without the column are unaffected."""
    df = trades[~trades.get("outlier", pd.Series(False, index=trades.index)).astype(bool)].copy()
    if "deal_type" in df:
        df = df[~df["deal_type"].map(is_excluded_deal)]
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


def declared_workout(value) -> str | None:
    """The workout a reported yield type names (``YTM``, ``YTC``, ``YTP`` or in words): ``maturity``, ``call``,
    ``put``; ``None`` when blank or not recognised."""
    text = re.sub(r"[^A-Z]+", " ", cell_str(value).upper()).strip()
    if not text:
        return None
    if text in YIELD_TYPES:
        return YIELD_TYPES[text]
    for word, kind in (("CALL", "call"), ("PUT", "put"), ("MATURITY", "maturity")):
        if word in text.split():
            return kind
    return None


def _readings(bond, s: date, price: float, workout: str, price_basis: str = PRICE_BASIS):
    """(standard yield, workout date, workout kind, [(reading, yield, basis, end)]) of one trade's price.

    ``basis``: how the price was read: ``(clean or dirty, face basis)``; the standard reading is a clean price on
    ``price_basis``. A partly redeemed bond is read on both face bases. ``end``: ``maturity``, ``call`` or ``put``."""
    from cr_strat.corporates import CorporateBond, price_factor

    if not isinstance(bond, CorporateBond):
        raise TypeError("the trade check reads corporates.CorporateBond terms")
    f_std = price_factor(bond, s, price_basis)
    bases = [(price_basis, f_std)]
    other = "original" if price_basis == "outstanding" else "outstanding"
    f_other = price_factor(bond, s, other)
    if abs(f_other - f_std) > 1e-12:
        bases.append((other, f_other))
    target, kind, when = bond, "maturity", bond.maturity
    if workout == "worst":
        w = bond.yield_to_worst(s, price * f_std)
        if w.date != bond.maturity:
            target, kind, when = bond.to_workout(w.date, w.price), w.kind, w.date
    standard = target.yield_from_price(price * f_std, s)
    own = "due" if bond.uses_due_dates(s) else "payment"
    ends = [("maturity", "maturity", bond)] + [(f"{k} {d}", k, bond.to_workout(d, p)) for k, d, p in bond.workouts(s)
                                               if k != "maturity"]
    out = []
    for basis, f in bases:
        for end_name, end_kind, end_bond in ends:
            for dates in (own, "payment" if own == "due" else "due"):
                dated = end_bond if dates == own else replace(end_bond, flow_dates=dates)
                for rule in ("mm", "compound"):
                    ruled = dated if rule == "mm" else replace(dated, conventions=replace(dated.conventions,
                                                                                          final_period="compound"))
                    for quote in ("clean", "dirty"):
                        try:
                            y = (ruled.yield_from_price(price * f, s) if quote == "clean"
                                 else ruled.yield_from_dirty_price(price * f, s))
                        except (ValueError, RuntimeError):
                            continue
                        name = "/".join(x for x in (f"per 100 {basis} face" if basis != price_basis else "", quote,
                                                    f"{dates} dates", end_name,
                                                    "compounded" if rule == "compound" else "") if x)
                        out.append((name, y, (quote, basis), end_kind))
    seen, unique = set(), []
    for item in out:  # the same yield under several names (no money-market flow, say): keep the first, simplest
        key = round(item[1], 12)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return standard, when, kind, unique


def _reread(bond, s: date, price: float, workout: str, quote: str, f: float) -> float:
    """The standard yield (own dates, to maturity or to worst) of a price read as ``quote`` on face factor ``f``."""
    target = bond
    clean = price * f if quote == "clean" else price * f - bond.accrued_interest(s)
    if workout == "worst":
        w = bond.yield_to_worst(s, clean)
        if w.date != bond.maturity:
            target = bond.to_workout(w.date, w.price)
    return target.yield_from_price(clean, s) if quote == "clean" else target.yield_from_dirty_price(price * f, s)


def check_trade_yields(trades: pd.DataFrame, terms, *, schedule=None, options=None, amortisation=None,
                       tolerance_bp: float = TOLERANCE_BP, workout: str = "maturity", calendar="default",
                       rates: dict | None = None, as_of=None, price_basis: str = PRICE_BASIS) -> pd.DataFrame:
    """``trades`` (``TRADE_COLUMNS``: ``isin``, ``price``, ``yield_pct``, ``trade_date``, ``settlement_date``) with
    ``CHECK_COLUMNS`` appended: each trade's yield recomputed from its price and the bond's terms (module docstring).

    ``terms``: the corporate terms table (``corporates.read_corporate_terms``' layout) with its optional schedule,
    options and amortisation tables; ``rates``: rate steps by ISIN for ``frb_corp`` rows. Each bond is taken as
    known at ``as_of`` (``CorporateBond.as_known``: exercises, and decisions not to exercise, public by then): one
    time for every trade, else a trade's ``as_of`` cell, else 00:00 on its trade date (UNVERIFIED default: a
    print's time is not known, so a notice made public that day counts from the next). ``price_basis``: prices per
    100 of the face ``outstanding`` at settlement (default, UNVERIFIED: exchanges trade a partly redeemed bond on
    its reduced face) or of the ``original`` face; the other basis is a reading too. ``recomputed_yield_pct`` is
    the standard reading (clean price, the bond's own flow dates, to maturity or to worst); ``nearest_reading``
    and ``yield_diff_bp`` (reported less nearest) say which reading the reported yield matches, ``yield_check``
    how well: ``ok`` on the standard price reading; ``dirty_price`` or ``face_basis`` when it matches only with
    the price read as dirty or on the other face basis, the recomputed yield then taken from the price read so;
    ``mismatch`` beyond ``tolerance_bp``. A trade's ``yield_type`` (YTM, YTC, YTP: SEBI's reporting format)
    limits the readings to that workout when the bond has one. The trades keep their order and columns.
    """
    from cr_strat.corporates import (
        PRICE_BASES,
        _as_of_for,
        corporate_from_terms,
        normalise_isin,
        price_factor,
        public_time,
        read_amortisation,
        read_corporate_options,
        read_corporate_schedule,
        read_corporate_terms,
    )

    if workout not in WORKOUT_RULES:
        raise ValueError(f"workout must be one of {WORKOUT_RULES}, not {workout!r}")
    if price_basis not in PRICE_BASES:
        raise ValueError(f"price_basis must be one of {PRICE_BASES}, not {price_basis!r}")
    tol = float(tolerance_bp)
    if not (math.isfinite(tol) and tol >= 0):
        raise ValueError(f"tolerance_bp must be a number >= 0, not {tolerance_bp!r}")
    missing = [c for c in ("isin", "price", "yield_pct", "trade_date") if c not in trades]
    if missing:
        raise ValueError(f"the trades lack {missing}")
    when_all = None if as_of is None else public_time(as_of, "as_of")
    table = read_corporate_terms(terms)
    sched = None if schedule is None else read_corporate_schedule(schedule)
    opts = None if options is None else read_corporate_options(options)
    amort = None if amortisation is None else read_amortisation(amortisation)
    by_isin = {r["isin"]: r for r in table.to_dict("records")}
    rates = None if rates is None else {normalise_isin(k): v for k, v in rates.items()}
    bonds: dict[str, object] = {}
    known: dict[tuple, object] = {}
    fallback = mumbai_settlement_calendar().lenient()
    rows = []
    for rec in trades.to_dict("records"):
        out = dict.fromkeys(CHECK_COLUMNS, math.nan) | {"settlement_used": None, "workout_date": None,
                                                        "workout_kind": "", "nearest_reading": "",
                                                        "known_as_of": None, "calendar_projected": None,
                                                        "check_notes": ""}
        isin = "" if _blank(rec.get("isin")) else normalise_isin(rec["isin"])
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
            base = bonds[isin]
            if isinstance(base, Exception):
                raise base
            if _blank(rec.get("settlement_date")):
                cal = base.calendar or fallback
                s = cal.settlement_date(as_date(rec["trade_date"]), base.conventions.settlement_lag)
                notes.append(f"settlement taken as T+{base.conventions.settlement_lag}")
            else:
                s = as_date(rec["settlement_date"])
            out["settlement_used"] = s
            when = _as_of_for(base, rec, s, when_all)
            out["known_as_of"] = when
            if (isin, when) not in known:
                known[(isin, when)] = base.as_known(when)
            bond = known[(isin, when)]
            out["calendar_projected"] = bond.calendar_projected(s)
            exercised = ""
            if bond.maturity != base.maturity:
                done = [o.kind for o in base.options if o.date == bond.maturity and o.exercised]
                exercised = f"exercised_{done[0]}" if done else "exercised"
                notes.append(f"redeemed on {bond.maturity} by an exercise public by {when:%Y-%m-%d %H:%M}")
            if _blank(rec.get("price")):
                out["yield_check"] = "no_price"
                rows.append(out | {"check_notes": "; ".join(notes)})
                continue
            price = float(rec["price"])
            standard, end_date, kind, readings = _readings(bond, s, price, workout, price_basis)
            out.update(recomputed_yield_pct=standard, workout_date=end_date,
                       workout_kind=exercised if exercised and kind == "maturity" else kind)
            if _blank(rec.get("yield_pct")):
                out["yield_check"] = "no_yield"
            else:
                reported = float(rec["yield_pct"])
                declared = declared_workout(rec.get("yield_type"))
                pool = readings
                if declared is not None:
                    pool = [r for r in readings if r[3] == declared]
                    if not pool:
                        notes.append(f"yield type {cell_str(rec.get('yield_type'))} names a {declared} the bond "
                                     "does not have ahead: every reading compared")
                        pool = readings
                name, y, (quote, basis), _ = min(pool, key=lambda r: abs(r[1] - reported))
                within = abs(reported - y) * 100 <= tol + 1e-9
                status = "mismatch"
                if within:
                    status = "ok" if (quote, basis) == ("clean", price_basis) else (
                        "dirty_price" if quote == "dirty" else "face_basis")
                if status in ("dirty_price", "face_basis"):  # the price was quoted so: recompute it read that way
                    standard = _reread(bond, s, price, workout, quote, price_factor(bond, s, basis))
                    out["recomputed_yield_pct"] = standard
                    notes.append(f"the reported yield matches the price read as {quote} per 100 {basis} face: the "
                                 "recomputed yield reads it so")
                out.update(standard_diff_bp=(reported - standard) * 100, nearest_reading=name, nearest_yield_pct=y,
                           yield_diff_bp=(reported - y) * 100, yield_check=status)
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


RECOMPUTED_FALLBACKS = ("drop", "reported")


def recomputed_trades(checked: pd.DataFrame, *, without_terms: str = "drop") -> pd.DataFrame:
    """Checked trades with ``yield_pct`` the recomputed yield and the reported one kept as ``reported_yield_pct``.
    Feed it to ``clean_trades`` (outliers on the recomputed yields) and then ``isin_daily_checked``.

    Trades without a recomputed yield (no price, an error) are left out. Trades of bonds without terms
    (``no_terms``) are left out too (``without_terms="drop"``, the default: a recomputed panel then covers only
    the bonds whose terms are typed in, and peer groups shrink to them), or kept on their reported yield
    (``"reported"``: ``recomputed_yield_pct`` stays blank and the check ``no_terms`` says which)."""
    if "recomputed_yield_pct" not in checked:
        raise ValueError("check the trades first (check_trade_yields)")
    if without_terms not in RECOMPUTED_FALLBACKS:
        raise ValueError(f"without_terms must be one of {RECOMPUTED_FALLBACKS}, not {without_terms!r}")
    unchecked = (checked["yield_check"] == "no_terms") & checked["yield_pct"].notna()
    use = checked["recomputed_yield_pct"].notna() | (unchecked if without_terms == "reported" else False)
    keep = checked[use].copy()
    keep["reported_yield_pct"] = keep["yield_pct"]
    keep["yield_pct"] = keep["recomputed_yield_pct"].where(keep["recomputed_yield_pct"].notna(), keep["yield_pct"])
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
    if "deal_type" in keys:
        keys = keys[~keys["deal_type"].map(is_excluded_deal)]
    extra = keys.groupby(["date", "isin"])[["yield_check", "workout_date"]].apply(summary).reset_index()
    out = daily.merge(rep, on=["date", "isin"], how="left").merge(extra, on=["date", "isin"], how="left")
    # a day of trades kept on their reported yields (no terms) has no recomputed yield
    unchecked = out["yield_check"].fillna("").str.fullmatch(r"no_terms:\d+")
    out["recomputed_yield_pct"] = out["yield_pct"].where(~unchecked)
    return out[[*DAILY_COLUMNS, *CHECKED_DAILY_COLUMNS]]
