"""Load rating actions into one normalised table.

Input is a CSV (or DataFrame) with one row per rating action, assembled from
CRA press releases, exchange disclosures or a vendor. Required columns:

    date        first public date of the action, ISO format (YYYY-MM-DD)
    issuer_id   stable issuer key shared with the security master (e.g. CIN)
    rating      rating string as published, e.g. "CRISIL AA+/Stable"

Optional columns: issuer_name, agency, instrument, instrument_class, outlook,
action, liquidity, isin, source_url. A missing agency is read from the rating
string; a missing instrument_class is inferred from the instrument text.

The rating string overrides the class where it is more specific: credit-enhanced
ratings are STRUCTURED, market-linked ones MLD, short-term ones ST, and fund
ratings (``fund_scale``, e.g. "CARE AAA mfs") OTHER, so they never reach a bond
mandate. A string holding several ratings is reduced to one by
``rating_scale.parse_rating``.
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

# Within one label, checked in order; the first match wins. Convertibles come first
# ("non-convertible" is rewritten to "ncd" before matching), and BOND comes before
# BANK_LOAN so that an issuer name with "LOANS" in an NSDL description
# ("XYZ GOLD LOANS LIMITED SR II 9.5 NCD ...") stays a bond. The NSDL abbreviations are BD/BDS
# (bond), LOA (letter of allotment, a bond not yet in certificate form), CCD and OCD
# (compulsorily and optionally convertible debentures).
_CLASS_RULES = (
    ("OTHER", re.compile(r"\bconvertible|\b[co]cds?\b", re.I)),
    ("ST", re.compile(r"commercial paper|\bcps?\b|certificate of deposit|\bcds?\b|short[\s-]*term", re.I)),
    ("SUB", re.compile(r"sub[\s-]*ordinat|tier[\s-]*(?:ii|2)\b|lower tier|upper tier", re.I)),
    ("PERP", re.compile(r"perpetual|tier[\s-]*(?:i|1)\b|\bat[\s-]*1\b|additional tier", re.I)),
    ("MLD", re.compile(r"market[\s-]*linked|\bmlds?\b|\bpp[\s-]*mlds?\b", re.I)),
    ("STRUCTURED", re.compile(
        r"pass[\s-]*through|\bptcs?\b(?!\s+india)|securiti[sz](?:ation|ed)|credit[\s-]*enhance|\bsdis?\b(?![/-]?\d)"
        r"|\(sns?\)|\bsn\s+series\b|\bseries\s+\w+\s+sn\b|(?:first|second)[\s-]*loss|\b[fs]lcfs?\b", re.I)),
    ("FD", re.compile(r"fixed deposit|\bfds?\b|deposit program", re.I)),
    ("BOND", re.compile(
        r"\bncds?\b|debenture|\bbonds?\b|\bbds?\b|\bloa\b|debt program|market borrowing", re.I)),
    ("BANK_LOAN", re.compile(
        r"bank (?:loan|facilit)|term loan|cash credit|fund[\s-]*based|working capital|\bloans?\b|\bblr\b", re.I)),
)
# A label naming several instruments ("BLR/Bond/CP", "NCD & CP") takes the class
# that matters most for bond mandates. A "/" joining a digit or a single letter is
# part of a series code ("SR AS/MLD/A/06"), not a list.
_CLASS_PRIORITY = ("BOND", "SUB", "PERP", "MLD", "STRUCTURED", "FD", "BANK_LOAN", "ST", "OTHER")
_LABEL_SPLIT_RE = re.compile(r"[/,&]|\band\b", re.I)
_CODE_TOKEN_RE = re.compile(r"\S*/\S*")
_CODE_PIECE_RE = re.compile(r"(?:^|/)(?:[^/]*\d[^/]*|[A-Za-z])(?:/|$)")
_NON_CONVERTIBLE_RE = re.compile(r"non[\s-]*convertible", re.I)

_REQUIRED = ("date", "issuer_id", "rating")


def _classify_part(text: str) -> str:
    for cls, pattern in _CLASS_RULES:
        if pattern.search(text):
            return cls
    return "OTHER"


def classify_instrument(instrument: object) -> str:
    """Instrument class from free text; blank text is assumed to mean bonds.

    CARE's bare "Long Term", "Short Term" and "LT/ST" labels cover bank facilities
    and unallocated limits as well as debt, so "Long Term" and "LT/ST" stay OTHER
    unless the text names a bank facility or loan; treating them as bonds would feed
    bank-loan ratings into bond mandates.
    """
    if instrument is None or (isinstance(instrument, float) and pd.isna(instrument)):
        return "BOND"
    text = _NON_CONVERTIBLE_RE.sub(" ncd ", str(instrument).strip())
    if not text:
        return "BOND"
    text = _CODE_TOKEN_RE.sub(
        lambda m: m.group().replace("/", "-") if _CODE_PIECE_RE.search(m.group()) else m.group(), text)
    classes = {_classify_part(part) for part in _LABEL_SPLIT_RE.split(text) if part.strip()}
    return min(classes, key=_CLASS_PRIORITY.index, default="OTHER")


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
                "hybrid", "issuer_not_cooperating", "sovereign", "fund_scale"):
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
    # A fund rating ("IND AA-mfs") rates a scheme, never an issuer's debt.
    cls = cls.where(~out["fund_scale"].astype(bool), "OTHER")
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
