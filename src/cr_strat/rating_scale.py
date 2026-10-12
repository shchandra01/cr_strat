"""Parse Indian credit rating strings onto a common notch scale.

The SEBI-registered agencies (CRISIL, ICRA, CARE, India Ratings, Acuite,
Infomerics, Brickwork) share letter grades but decorate them differently:
``CRISIL AA+/Stable``, ``[ICRA]AA (Negative)``, ``CARE AA; Stable``,
``IND AA/RWN``, ``ACUITE AA- | Stable``, ``PP-MLD[ICRA]AA+``. ``parse_rating``
strips the decoration and returns the grade, its notch and the flags that
matter for mandate rules: outlook, watch, credit enhancement,
issuer-not-cooperating and withdrawal.

Notches run from 1 (best) upwards, so a larger notch is a worse rating.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass

LT_GRADES = (
    "AAA", "AA+", "AA", "AA-", "A+", "A", "A-",
    "BBB+", "BBB", "BBB-", "BB+", "BB", "BB-",
    "B+", "B", "B-", "C+", "C", "C-", "D",
)
ST_GRADES = ("A1+", "A1", "A2+", "A2", "A3+", "A3", "A4+", "A4", "D")
LT_NOTCH = {g: i for i, g in enumerate(LT_GRADES, start=1)}
ST_NOTCH = {g: i for i, g in enumerate(ST_GRADES, start=1)}
LT_INVESTMENT_GRADE_FLOOR = LT_NOTCH["BBB-"]
ST_INVESTMENT_GRADE_FLOOR = ST_NOTCH["A3"]

AGENCIES = ("CRISIL", "ICRA", "CARE", "IND", "ACUITE", "INFOMERICS", "BRICKWORK")
LIQUIDITY_LEVELS = ("SUPERIOR", "STRONG", "ADEQUATE", "STRETCHED", "POOR")

# Agency names as they appear in an agency column ("India Ratings & Research",
# "CareEdge", "Acuité") and as prefixes inside rating strings ("IND AA").
_AGENCY_RES = (
    ("CRISIL", re.compile(r"\bCRISIL\b")),
    ("ICRA", re.compile(r"\[?\s*\bICRA\b\s*\]?")),
    ("CARE", re.compile(r"\bCARE(?:\s*EDGE)?\b")),
    ("IND", re.compile(r"\bIND\b|\bINDIA\s+RATINGS?\b|\bIND-?RA\b|\bFITCH\b")),
    ("ACUITE", re.compile(r"\bACUITE\b|\bSMERA\b")),
    ("INFOMERICS", re.compile(r"\bINFOMERICS\b|\bIVR\b")),
    ("BRICKWORK", re.compile(r"\bBRICKWORK\b|\bBWR\b")),
)

_WATCH_RES = (
    ("NEGATIVE", re.compile(
        r"(?:CREDIT\s+|RATING\s+)?WATCH\s+(?:WITH\s+)?NEGATIVE(?:\s+IMPLICATIONS?)?|\bRWN\b|\bCWN\b")),
    ("POSITIVE", re.compile(
        r"(?:CREDIT\s+|RATING\s+)?WATCH\s+(?:WITH\s+)?POSITIVE(?:\s+IMPLICATIONS?)?|\bRWP\b|\bCWP\b")),
    ("DEVELOPING", re.compile(
        r"(?:CREDIT\s+|RATING\s+)?WATCH\s+(?:WITH\s+)?(?:DEVELOPING|EVOLVING)(?:\s+IMPLICATIONS?)?"
        r"|\bRWE\b|\bRWD\b|\bCWD\b")),
)
_OUTLOOK_RES = (
    ("STABLE", re.compile(r"\bSTABLE\b")),
    ("POSITIVE", re.compile(r"\bPOSITIVE\b")),
    ("NEGATIVE", re.compile(r"\bNEGATIVE\b")),
    ("DEVELOPING", re.compile(r"\bDEVELOPING\b")),
)

_UNRATED_RE = re.compile(r"^\s*(?:N\.?\s*A\.?|NOT\s+RATED|UNRATED|NR|-+|NIL|NONE)\s*$")
# "Downgraded from AAA to AA+" keeps only the new rating; "(from AAA)" is dropped.
_FROM_TO_RE = re.compile(r"\bFROM\b.*?\bTO\b")
_FROM_RE = re.compile(r"\bFROM\b[^)]*\)?")
_PROVISIONAL_RE = re.compile(r"\bPROVISIONAL\b")
_INC_RE = re.compile(r"ISSUER\s+NOT\s+CO\s*-?\s*OPERATING\*?|\bINC\b")
_WITHDRAWN_RE = re.compile(r"\bWITHDRAWN\b|\bWITHDRAWAL\b")
_CE_RE = re.compile(r"\(\s*(?:CE|SO)\s*\)")
_MLD_RE = re.compile(r"PP\s*-?\s*MLD")
_FD_PAREN_RE = re.compile(r"\(\s*FD\s*\)")
_HYB_RE = re.compile(r"\(\s*HYB\s*\)")
_SOVEREIGN_RE = re.compile(r"\bSOVEREIGN\b|\bSOV\b")

_LT_RE = re.compile(r"(?<![A-Z0-9])(AAA|AA|A|BBB|BB|B|C|D)([+-]?)(?![A-Z0-9])")
# Market-linked debentures carry an "r" (or "emr") suffix: "AAAr", "AA+emr".
_LT_MLD_RE = re.compile(r"(?<![A-Z0-9])(AAA|AA|A|BBB|BB|B|C|D)([+-]?)(?:EMR|R)?(?![A-Z0-9])")
_ST_RE = re.compile(r"(?<![A-Z0-9])A([1-4])(\+?)(?![A-Z0-9])")
# Fixed-deposit scales: CRISIL "FAA+", ICRA "MAA+".
_FD_RE = re.compile(r"(?<![A-Z0-9])[FM](AAA|AA|A|BBB|BB|B|C|D)([+-]?)(?![A-Z0-9])")


@dataclass(frozen=True)
class ParsedRating:
    raw: str
    agency: str | None = None
    scale: str | None = None  # "LT" or "ST"
    grade: str | None = None
    notch: int | None = None
    outlook: str | None = None
    watch: str | None = None
    provisional: bool = False
    credit_enhanced: bool = False
    mld: bool = False
    fixed_deposit: bool = False
    hybrid: bool = False
    issuer_not_cooperating: bool = False
    withdrawn: bool = False
    sovereign: bool = False

    @property
    def is_default(self) -> bool:
        return self.grade == "D"


def _clean(value: object) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip().upper()


def _take(pattern: re.Pattern[str], text: str) -> tuple[bool, str]:
    new, n = pattern.subn(" ", text)
    return n > 0, new


def normalize_agency(value: object) -> str | None:
    """Map an agency name or rating-string prefix to a code in ``AGENCIES``."""
    text = _clean(value)
    if not text:
        return None
    for code, pattern in _AGENCY_RES:
        if pattern.search(text):
            return code
    return None


def normalize_liquidity(value: object) -> str | None:
    """Map a CRA liquidity descriptor ("Liquidity: Adequate") to ``LIQUIDITY_LEVELS``."""
    text = _clean(value)
    for level in LIQUIDITY_LEVELS:
        if re.search(rf"\b{level}\b", text):
            return level
    return None


def parse_outlook(value: object) -> tuple[str | None, str | None]:
    """Return ``(outlook, watch)`` from text such as "Negative" or "Rating Watch with Negative Implications"."""
    outlook, watch, _ = _outlook_and_watch(_clean(value))
    return outlook, watch


def _outlook_and_watch(text: str) -> tuple[str | None, str | None, str]:
    watch = None
    for level, pattern in _WATCH_RES:
        if pattern.search(text):
            watch = watch or level
            text = pattern.sub(" ", text)
    outlook, first = None, None
    for level, pattern in _OUTLOOK_RES:
        m = pattern.search(text)
        if m and (first is None or m.start() < first):
            outlook, first = level, m.start()
    for _, pattern in _OUTLOOK_RES:
        text = pattern.sub(" ", text)
    return outlook, watch, text


def parse_rating(raw: object) -> ParsedRating:
    """Parse one rating string. Never raises; an unrecognised grade leaves ``grade`` as None."""
    text = _clean(raw)
    raw_str = "" if not text else str(raw)
    if not text or _UNRATED_RE.match(text):
        return ParsedRating(raw=raw_str)

    agency = normalize_agency(text)
    text = _FROM_TO_RE.sub(" ", text)
    text = _FROM_RE.sub(" ", text)
    provisional, text = _take(_PROVISIONAL_RE, text)
    inc, text = _take(_INC_RE, text)
    withdrawn, text = _take(_WITHDRAWN_RE, text)
    credit_enhanced, text = _take(_CE_RE, text)
    mld, text = _take(_MLD_RE, text)
    fd_paren, text = _take(_FD_PAREN_RE, text)
    hybrid, text = _take(_HYB_RE, text)
    sovereign, text = _take(_SOVEREIGN_RE, text)
    outlook, watch, text = _outlook_and_watch(text)
    for _, pattern in _AGENCY_RES:
        text = pattern.sub(" ", text)

    scale = grade = None
    fixed_deposit = fd_paren
    m = (_LT_MLD_RE if mld else _LT_RE).search(text)
    if m:
        scale, grade = "LT", m.group(1) + m.group(2)
    else:
        m = _ST_RE.search(text)
        if m:
            scale, grade = "ST", f"A{m.group(1)}{m.group(2)}"
        else:
            m = _FD_RE.search(text)
            if m:
                scale, grade, fixed_deposit = "LT", m.group(1) + m.group(2), True

    notch = None
    if scale == "LT":
        notch = LT_NOTCH.get(grade)
    elif scale == "ST":
        notch = ST_NOTCH.get(grade)
    if notch is None:
        scale = grade = None

    return ParsedRating(
        raw=raw_str,
        agency=agency,
        scale=scale,
        grade=grade,
        notch=notch,
        outlook=outlook,
        watch=watch,
        provisional=provisional,
        credit_enhanced=credit_enhanced,
        mld=mld,
        fixed_deposit=fixed_deposit,
        hybrid=hybrid,
        issuer_not_cooperating=inc,
        withdrawn=withdrawn,
        sovereign=sovereign,
    )


def lt_grade(notch: int | None) -> str | None:
    """Long-term grade for a notch, or None."""
    if notch is None:
        return None
    return LT_GRADES[notch - 1]


def rating_bucket(notch: int | None) -> str | None:
    """Peer bucket for spread comparisons: each grade down to A-, then BBB, SUB_IG and D."""
    if notch is None or notch != notch:  # None or NaN
        return None
    notch = int(notch)
    if notch <= LT_NOTCH["A-"]:
        return LT_GRADES[notch - 1]
    if notch <= LT_INVESTMENT_GRADE_FLOOR:
        return "BBB"
    return "D" if notch == LT_NOTCH["D"] else "SUB_IG"
