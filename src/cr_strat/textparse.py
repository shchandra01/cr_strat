"""Helpers for reading the loosely formatted spreadsheets Indian market sources publish."""

from __future__ import annotations

import csv
import datetime as dt
import re
import warnings
from pathlib import Path

import pandas as pd

UNIT_MULTIPLIERS = {"rupee": 1.0, "thousand": 1e3, "lakh": 1e5, "crore": 1e7}

_DATE_PATTERNS = (
    re.compile(r"\d{4}-\d{2}-\d{2}"),
    re.compile(r"\d{1,2}[-/. ]+[A-Za-z]{3,9}\.?[-/. ,]+\d{2,4}"),
    re.compile(r"[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}"),
    re.compile(r"\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}"),
)
_COLUMN_DATE_FORMATS = (
    "%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y", "%d/%m/%Y", "%d %b %Y", "%d-%b-%y", "%d.%m.%Y", "%d%m%Y",
    "%Y-%m-%d %H:%M:%S",
)


def cell_str(value: object) -> str:
    """Cell as trimmed text; dates become ISO strings, blanks become ""."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, (dt.datetime, dt.date, pd.Timestamp)):
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    return re.sub(r"\s+", " ", str(value)).strip()


def to_number(value: object) -> float:
    """Parse "1,234.5", "(12)", "₹ 10" and similar; anything else is NaN."""
    if value is None or isinstance(value, bool):
        return float("nan")
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r"[,\s₹]", "", str(value))
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    try:
        number = float(text)
    except ValueError:
        return float("nan")
    return -number if negative else number


def detect_multiplier(text: str) -> float | None:
    """Rupee multiplier named in a header such as "Value (Rs. in Lakhs)"."""
    t = text.lower()
    if re.search(r"lakh|\blacs?\b|\blac\.", t):
        return 1e5
    if re.search(r"crore|\bcrs?\b", t):
        return 1e7
    if re.search(r"thousand|'000", t):
        return 1e3
    if re.search(r"\b(?:rs|inr|rupees)\b|₹", t):
        return 1.0
    return None


def parse_date_text(text: str) -> pd.Timestamp | None:
    """First date found in free text ("Portfolio as on 15th September, 2026"); day-first."""
    text = re.sub(r"(\d)(?:st|nd|rd|th)\b", r"\1", text, flags=re.I)
    for pattern in _DATE_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            if pattern is _DATE_PATTERNS[0]:
                value = pd.to_datetime(m.group(0), format="ISO8601", errors="coerce")
            else:
                value = pd.to_datetime(m.group(0), dayfirst=True, errors="coerce")
        if not pd.isna(value):
            return pd.Timestamp(value).normalize()
    return None


def parse_date_column(values: pd.Series) -> pd.Series:
    """Parse a date column using whichever common format fits most rows, then day-first for the rest."""
    text = values.map(cell_str)
    best, best_ok = None, -1
    for fmt in _COLUMN_DATE_FORMATS:
        parsed = pd.to_datetime(text, format=fmt, errors="coerce")
        ok = int(parsed.notna().sum())
        if ok > best_ok:
            best, best_ok = parsed, ok
    missing = best.isna() & (text != "")
    if missing.any():
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            rest = text[missing].map(lambda t: pd.to_datetime(t, dayfirst=True, errors="coerce"))
        best = best.where(~missing, pd.to_datetime(rest, errors="coerce"))
    return best.dt.normalize()


def read_rows(path: str | Path) -> dict[str, list[list]]:
    """All rows of a CSV or workbook as ``{sheet: rows}``, without assuming a header position."""
    path = Path(path)
    if path.suffix.lower() in (".csv", ".txt"):
        with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
            return {path.stem: [row for row in csv.reader(fh)]}
    sheets = pd.read_excel(path, sheet_name=None, header=None, dtype=object)
    return {str(name): frame.values.tolist() for name, frame in sheets.items()}
