"""Load rating actions into one normalised table.

Input is a CSV (or DataFrame) with one row per rating action, assembled from
CRA press releases, exchange disclosures or a vendor. Required columns:

    date        first public date of the action, ISO format (YYYY-MM-DD)
    issuer_id   stable issuer key shared with the security master (e.g. CIN)
    rating      rating string as published, e.g. "CRISIL AA+/Stable"

Optional columns: issuer_name, agency, instrument, instrument_class, outlook,
action, liquidity, isin, source_url. A missing agency is read from the rating
string; a missing instrument_class is inferred from the instrument text.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from cr_strat.rating_scale import (
    ST_NOTCH,
    normalize_agency,
    normalize_liquidity,
    parse_outlook,
    parse_rating,
)

INSTRUMENT_CLASSES = ("BOND", "BANK_LOAN", "ST", "SUB", "PERP", "STRUCTURED", "MLD", "FD", "OTHER")

# Checked in order; the first match wins.
_CLASS_RULES = (
    ("ST", re.compile(r"commercial paper|\bcps?\b|certificate of deposit|\bcds?\b|short[\s-]*term", re.I)),
    ("SUB", re.compile(r"subordinat|tier[\s-]*(?:ii|2)\b|lower tier|upper tier", re.I)),
    ("PERP", re.compile(r"perpetual|tier[\s-]*(?:i|1)\b|\bat[\s-]*1\b|additional tier", re.I)),
    ("MLD", re.compile(r"market[\s-]*linked|\bmlds?\b", re.I)),
    ("STRUCTURED", re.compile(r"pass[\s-]*through|\bptcs?\b|securiti[sz]ation|credit[\s-]*enhance", re.I)),
    ("FD", re.compile(r"fixed deposit|\bfds?\b|deposit program", re.I)),
    ("BANK_LOAN", re.compile(r"bank (?:loan|facilit)|term loan|cash credit|fund[\s-]*based|working capital|\bloans?\b", re.I)),
    ("BOND", re.compile(r"\bncds?\b|non[\s-]*convertible|debenture|\bbonds?\b|debt program", re.I)),
)

_REQUIRED = ("date", "issuer_id", "rating")


def classify_instrument(instrument: object) -> str:
    """Instrument class from free text; blank text is assumed to mean bonds."""
    if instrument is None or (isinstance(instrument, float) and pd.isna(instrument)):
        return "BOND"
    text = str(instrument).strip()
    if not text:
        return "BOND"
    for cls, pattern in _CLASS_RULES:
        if pattern.search(text):
            return cls
    return "OTHER"


def _str_or_none(value: object) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text or None


def load_rating_actions(source: str | Path | pd.DataFrame) -> pd.DataFrame:
    """Parse every action. Rows that cannot be used have ``parse_ok`` False and a ``failure`` reason."""
    df = source.copy() if isinstance(source, pd.DataFrame) else pd.read_csv(source, dtype=str)
    df.columns = [str(c).strip().lower() for c in df.columns]
    missing = [c for c in _REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"rating actions missing required columns: {missing}")

    out = pd.DataFrame(index=df.index)
    out["action_id"] = range(len(df))
    out["date"] = pd.to_datetime(df["date"], format="ISO8601", errors="coerce")
    out["issuer_id"] = df["issuer_id"].map(_str_or_none)
    out["issuer_name"] = df["issuer_name"].map(_str_or_none) if "issuer_name" in df else None
    out["instrument"] = df["instrument"].map(_str_or_none) if "instrument" in df else None
    out["isin"] = df["isin"].map(_str_or_none) if "isin" in df else None
    out["source_url"] = df["source_url"].map(_str_or_none) if "source_url" in df else None

    parsed = [parse_rating(r) for r in df["rating"]]
    fields = pd.DataFrame([asdict(p) for p in parsed], index=df.index)
    out["rating_raw"] = df["rating"]
    for col in ("scale", "grade", "provisional", "credit_enhanced", "mld", "fixed_deposit",
                "hybrid", "issuer_not_cooperating", "sovereign"):
        out[col] = fields[col]
    notch = fields["notch"].astype("Int64")

    agency = fields["agency"]
    if "agency" in df:
        given = df["agency"].map(normalize_agency)
        agency = given.where(given.notna(), agency)
    out["agency"] = agency

    outlook, watch = fields["outlook"], fields["watch"]
    if "outlook" in df:
        from_col = df["outlook"].map(parse_outlook)
        col_outlook = from_col.map(lambda t: t[0])
        col_watch = from_col.map(lambda t: t[1])
        outlook = col_outlook.where(col_outlook.notna(), outlook)
        watch = col_watch.where(col_watch.notna(), watch)
    out["outlook"], out["watch"] = outlook, watch
    out["liquidity"] = df["liquidity"].map(normalize_liquidity) if "liquidity" in df else None

    withdrawn = fields["withdrawn"].astype(bool)
    if "action" in df:
        withdrawn = withdrawn | df["action"].fillna("").str.contains("withdraw", case=False)
    out["withdrawn"] = withdrawn

    if "instrument_class" in df:
        given_cls = df["instrument_class"].map(_str_or_none).str.upper()
        inferred = out["instrument"].map(classify_instrument)
        cls = given_cls.where(given_cls.isin(INSTRUMENT_CLASSES), inferred)
    else:
        cls = out["instrument"].map(classify_instrument)
    # The rating itself overrides the instrument text where it is more specific.
    cls = cls.where(~out["credit_enhanced"].astype(bool), "STRUCTURED")
    cls = cls.where(~out["mld"].astype(bool), "MLD")
    cls = cls.where(~(out["fixed_deposit"].astype(bool) & cls.isin(["BOND", "OTHER"])), "FD")
    cls = cls.where(~(out["hybrid"].astype(bool) & cls.isin(["BOND", "OTHER"])), "SUB")
    cls = cls.where(out["scale"] != "ST", "ST")
    out["instrument_class"] = cls

    # A bare "D" on a short-term instrument is the short-term default grade.
    st_default = (out["instrument_class"] == "ST") & (out["grade"] == "D")
    out.loc[st_default, "scale"] = "ST"
    notch = notch.mask(st_default, ST_NOTCH["D"])
    out["notch"] = notch
    out["is_default"] = out["grade"] == "D"

    failure = pd.Series(None, index=df.index, dtype=object)
    failure = failure.mask(out["date"].isna(), "bad date")
    failure = failure.mask(failure.isna() & out["issuer_id"].isna(), "missing issuer_id")
    failure = failure.mask(failure.isna() & out["agency"].isna(), "unknown agency")
    failure = failure.mask(failure.isna() & ~out["withdrawn"] & out["notch"].isna(), "unparsed rating")
    out["failure"] = failure
    out["parse_ok"] = failure.isna()
    return out.reset_index(drop=True)
