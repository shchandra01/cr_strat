"""Parse Indian credit rating strings onto a common notch scale.

The SEBI-registered agencies (CRISIL, ICRA, CARE, India Ratings, Acuite,
Infomerics, Brickwork) share letter grades but decorate them differently:
``CRISIL AA+/Stable``, ``[ICRA]AA (Negative)``, ``CARE AA; Stable``,
``IND AA/RWN``, ``ACUITE AA- | Stable``, ``PP-MLD[ICRA]AA+``. ``parse_rating``
strips the decoration and returns the grade, its notch and the flags that
matter for mandate rules: outlook, watch, credit enhancement,
issuer-not-cooperating and withdrawal.

Quirks seen in the agencies' own disclosures:

- Brickwork puts a space before the modifier: ``BWR BB + /Stable``, ``BWR A1 +``.
- Infomerics writes watches as codes (``RWDI``, ``RWNI``, ``RWPI`` and the
  credit-watch ``CWDI``, ``CWNI``, ``CWPI``) and sometimes spells the grade out:
  ``IVR Triple B Plus``, ``IVR A One Plus``.
- CARE's ``(Under Credit Watch)`` names no direction. It gives
  ``watch="UNSPECIFIED"``, which counts as neither a negative nor a positive signal.
- Fund ratings (``IND A+mfs``, ``IND AA-idf-mf``, ``CARE AAA mfs``) use the same
  letters but rate a scheme's portfolio, not an issuer's debt. They parse with
  ``fund_scale=True`` and must not be used as issuer ratings.
- Fitch India's legacy short-term scale ``F1+(ind)`` ... ``F4(ind)`` maps to
  ``A1+`` ... ``A4``. ``F5(ind)`` has no equivalent and stays unparsed.
- ``(EXP)`` (expected) means provisional. A bare ``WD`` means withdrawn.
  ``(Short term rating withdrawn)`` withdraws only the short-term leg.
- Market-linked debentures are marked by ``PP-MLD``/``PPMLD``/``MLD`` or by an
  ``r``/``emr`` suffix on the grade (``CARE A+r``, ``IND PP-MLD AA-emr``).

One string often holds several ratings: a long-term/short-term pair
(``[ICRA]A+ (Stable)/[ICRA]A1+``), several facilities
(``ACUITE A-(CE) & ACUITE BBB-/ACUITE A3``) or several agencies
(``CRISIL AAA/Stable ();CARE AAA/Stable ()`` in the NSDL security master).
``parse_ratings`` splits the string where a new agency prefix starts after a
separator, and at ``&`` or ``;`` when a rating follows. Each segment is parsed on
its own, so an outlook, watch or flag never moves to another segment, with two
exceptions. Issuer-not-cooperating is a status of the issuer with an agency, so
it applies to every segment from that agency (CARE writes it once at the end:
``CARE BB+; Stable / CARE A4; ISSUER NOT COOPERATING*``). A watch or outlook
written after the short-term leg of a pair (``CARE A+ / CARE A1+ (Rating Watch
with Developing Implications)``) also applies to the long-term leg when that leg
has no outlook or watch of its own.

``parse_rating`` returns one segment: the worst long-term one among plain
segments (not credit-enhanced, market-linked or fund-scale), else the worst
long-term one among all segments, else the first short-term one. For a string
that lists several agencies that is the worst agency's rating; use
``parse_ratings`` to keep one row per agency.

Notches run from 1 (best) upwards, so a larger notch is a worse rating.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, replace

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
WATCH_LEVELS = ("NEGATIVE", "POSITIVE", "DEVELOPING", "UNSPECIFIED")

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

# Checked in order; a directional watch is removed before the undirected one is looked for.
_WATCH_RES = (
    ("NEGATIVE", re.compile(
        r"(?:CREDIT\s+|RATING\s+)?WATCH\s+(?:WITH\s+)?NEGATIVE(?:\s+IMPLICATIONS?)?|\b[RC]WNI?\b")),
    ("POSITIVE", re.compile(
        r"(?:CREDIT\s+|RATING\s+)?WATCH\s+(?:WITH\s+)?POSITIVE(?:\s+IMPLICATIONS?)?|\b[RC]WPI?\b")),
    ("DEVELOPING", re.compile(
        r"(?:CREDIT\s+|RATING\s+)?WATCH\s+(?:WITH\s+)?(?:DEVELOPING|EVOLVING)(?:\s+IMPLICATIONS?)?"
        r"|\bRWE\b|\b[RC]WDI?\b")),
    ("UNSPECIFIED", re.compile(r"(?<!OFF )\b(?:CREDIT|RATING)\s+WATCH\b")),
)
_OUTLOOK_RES = (
    ("STABLE", re.compile(r"\bSTABLE\b")),
    ("POSITIVE", re.compile(r"\bPOSITIVE\b")),
    ("NEGATIVE", re.compile(r"\bNEGATIVE\b")),
    ("DEVELOPING", re.compile(r"\bDEVELOPING\b")),
)
_LIQUIDITY_RES = tuple((level, re.compile(rf"\b{level}\b")) for level in LIQUIDITY_LEVELS)

_UNRATED_RE = re.compile(r"^\s*(?:N\.?\s*A\.?|NOT\s+RATED|UNRATED|NR|-+|NIL|NONE)\s*$")
# "Downgraded from AAA to AA+" keeps only the new rating; "(from AAA)" is dropped.
_FROM_TO_RE = re.compile(r"\bFROM\b.*?\bTO\b")
_FROM_RE = re.compile(r"\bFROM\b[^)]*\)?")
# Fitch India's "(EXP)" (expected) rating is a provisional rating.
_PROVISIONAL_RE = re.compile(r"\bPROVISIONAL\b|\(\s*EXP\s*\)")
_INC_RE = re.compile(r"ISSUER\s+NOT\s+CO\s*-?\s*OPERATING\*?|\bINC\b")
# "(Short term rating withdrawn)" withdraws one leg only.
_LEG_WITHDRAWN_RE = re.compile(
    r"\(?\s*\b(SHORT|LONG)[\s-]*TERM\s+RATINGS?\s+(?:(?:IS|WAS|HAS\s+BEEN)\s+)?WITHDRAWN\s*\)?")
_LEG_SCALE = {"SHORT": "ST", "LONG": "LT"}
_WITHDRAWN_RE = re.compile(r"\bWITHDRAWN\b|\bWITHDRAWAL\b|\bWD\b")
_CE_RE = re.compile(r"\(\s*(?:CE|SO)\s*\)")
_MLD_RE = re.compile(r"PP\s*-?\s*MLD|\bMLD\b")
_FD_PAREN_RE = re.compile(r"\(\s*FD\s*\)")
_HYB_RE = re.compile(r"\(\s*HYB\s*\)")
_SOVEREIGN_RE = re.compile(r"\bSOVEREIGN\b|\bSOV\b")

_GRADE_TOKEN = r"(?:AAA|AA|A|BBB|BB|B|C|D)[+-]?|A[1-4]\+?"
# Mutual fund ("mfs") and infrastructure debt fund ("idf-mf") scales, glued to the grade or not.
_FUND_RE = re.compile(rf"(?<![A-Z0-9])({_GRADE_TOKEN})\s*(?:IDF\s*-?\s*)?MFS?(?![A-Z0-9])")
# Brickwork's "BB +", "A1 +", "AA -". A spaced "-" is a modifier only when no word follows.
_SPACED_PLUS_RE = re.compile(r"(?<![A-Z0-9])([FM]?(?:AAA|AA|A|BBB|BB|B|C|D)|A[1-4])\s+\+")
_SPACED_MINUS_RE = re.compile(r"(?<![A-Z0-9])([FM]?(?:AAA|AA|A|BBB|BB|B|C|D))\s+-(?=\s*(?:$|[/(;,&|]))")
_WORD_LT_RE = re.compile(r"\b(SINGLE|DOUBLE|TRIPLE)\s+([ABC])(?:\s+(PLUS|MINUS))?\b")
_WORD_ST_RE = re.compile(r"\bA\s+(ONE|TWO|THREE|FOUR)(?:\s+(PLUS))?\b")
_WORD_COUNT = {"SINGLE": 1, "DOUBLE": 2, "TRIPLE": 3, "ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4}
_WORD_MODIFIER = {"PLUS": "+", "MINUS": "-", None: ""}
_FITCH_ST_RE = re.compile(r"(?<![A-Z0-9])F([1-4])(\+?)(?=\s*\(\s*IND\s*\))")

# Market-linked debentures carry an "r" (or "emr") suffix: "AAAr", "AA+emr", "AAA emr".
_LT_RE = re.compile(r"(?<![A-Z0-9])(AAA|AA|A|BBB|BB|B|C|D)([+-]?)(\s?EMR|R)?(?![A-Z0-9])")
_ST_RE = re.compile(r"(?<![A-Z0-9])A([1-4])(\+?)(?![A-Z0-9])")
# Fixed-deposit scales: CRISIL "FAA+", ICRA "MAA+".
_FD_RE = re.compile(r"(?<![A-Z0-9])[FM](AAA|AA|A|BBB|BB|B|C|D)([+-]?)(?![A-Z0-9])")

# Segment boundaries: a separator followed by a new rating. After "/", "|" or "," the
# new rating must start with an agency prefix (else "IND AA/Stable" would split); after
# "&" or ";" a bare grade also starts one ("ACUITE A-(CE) & BB+/ACUITE A4+").
_QUALIFIER = r"(?:PROVISIONAL|PROPOSED|PP\s*-?\s*MLD|(?:UN)?SUPPORTED(?:\s+RATINGS?)?\s*:?)"
_AGENCY_START = "|".join(f"(?:{p.pattern})" for _, p in _AGENCY_RES)
_GRADE_START = r"[FM]?(?:AAA|AA|A|BBB|BB|B|C|D)(?:\s*[+-])?(?![A-Z0-9])|A[1-4](?:\s*\+)?(?![A-Z0-9])"
_SPLIT_RE = re.compile(
    rf"\s*[/|,]\s*(?=(?:{_QUALIFIER}\s*)*(?:{_AGENCY_START}))"
    rf"|\s*[&;]\s*(?=(?:{_QUALIFIER}\s*)*(?:{_AGENCY_START}|{_GRADE_START}))",
    re.I,
)
_FROM_CLAUSE_RES = (re.compile(_FROM_TO_RE.pattern, re.I), re.compile(_FROM_RE.pattern, re.I))


@dataclass(frozen=True)
class ParsedRating:
    raw: str
    agency: str | None = None
    scale: str | None = None  # "LT" or "ST"
    grade: str | None = None
    notch: int | None = None
    outlook: str | None = None
    watch: str | None = None  # one of WATCH_LEVELS
    provisional: bool = False
    credit_enhanced: bool = False
    mld: bool = False
    fixed_deposit: bool = False
    hybrid: bool = False
    issuer_not_cooperating: bool = False
    withdrawn: bool = False
    sovereign: bool = False
    fund_scale: bool = False

    @property
    def is_default(self) -> bool:
        return self.grade == "D"

    @property
    def is_plain(self) -> bool:
        """Neither credit-enhanced, market-linked nor a fund rating."""
        return not (self.credit_enhanced or self.mld or self.fund_scale)


def _normalize(value: object) -> str:
    """Accents stripped and whitespace collapsed; case kept."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip()


def _clean(value: object) -> str:
    return _normalize(value).upper()


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
    """Map a CRA liquidity descriptor ("Liquidity: Adequate") to ``LIQUIDITY_LEVELS``.

    The descriptor that appears first wins, so a narrative that mentions another
    level later ("Superior / Strong", "Adequate ... strong parentage") keeps the first.
    """
    text = _clean(value)
    best, first = None, None
    for level, pattern in _LIQUIDITY_RES:
        m = pattern.search(text)
        if m and (first is None or m.start() < first):
            best, first = level, m.start()
    return best


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


def _canonical_grades(text: str) -> str:
    """Rewrite spaced modifiers, spelled-out grades and legacy Fitch short-term grades as plain grades."""
    text = _SPACED_PLUS_RE.sub(r"\1+", text)
    text = _SPACED_MINUS_RE.sub(r"\1-", text)
    text = _WORD_LT_RE.sub(
        lambda m: m.group(2) * _WORD_COUNT[m.group(1)] + _WORD_MODIFIER[m.group(3)], text)
    text = _WORD_ST_RE.sub(
        lambda m: f"A{_WORD_COUNT[m.group(1)]}" + _WORD_MODIFIER[m.group(2)], text)
    return _FITCH_ST_RE.sub(r"A\1\2", text)


def _find_grade(text: str, mld: bool) -> tuple[str | None, str | None, bool, bool]:
    """``(scale, grade, mld, fixed_deposit)`` from text stripped of everything but the grade."""
    for m in _LT_RE.finditer(text):
        suffix = m.group(3)
        # A bare "CR" or "BR" (crore, branch) is not a market-linked "C" or "B".
        if suffix and not mld and len(m.group(1)) == 1 and not m.group(2):
            continue
        return "LT", m.group(1) + m.group(2), mld or bool(suffix), False
    m = _ST_RE.search(text)
    if m:
        return "ST", f"A{m.group(1)}{m.group(2)}", mld, False
    m = _FD_RE.search(text)
    if m:
        return "LT", m.group(1) + m.group(2), mld, True
    return None, None, mld, False


def _parse_segment(segment: str) -> ParsedRating:
    """Parse text that holds at most one rating."""
    text = segment.upper()
    if not text or _UNRATED_RE.match(text):
        return ParsedRating(raw=segment)

    agency = normalize_agency(text)
    text = _FROM_TO_RE.sub(" ", text)
    text = _FROM_RE.sub(" ", text)
    text = _canonical_grades(text)
    provisional, text = _take(_PROVISIONAL_RE, text)
    inc, text = _take(_INC_RE, text)
    leg = _LEG_WITHDRAWN_RE.search(text)
    text = _LEG_WITHDRAWN_RE.sub(" ", text)
    withdrawn, text = _take(_WITHDRAWN_RE, text)
    credit_enhanced, text = _take(_CE_RE, text)
    mld, text = _take(_MLD_RE, text)
    text, n_fund = _FUND_RE.subn(r"\1 ", text)
    fd_paren, text = _take(_FD_PAREN_RE, text)
    hybrid, text = _take(_HYB_RE, text)
    sovereign, text = _take(_SOVEREIGN_RE, text)
    outlook, watch, text = _outlook_and_watch(text)
    for _, pattern in _AGENCY_RES:
        text = pattern.sub(" ", text)

    scale, grade, mld, fd_scale = _find_grade(text, mld)
    notch = None
    if scale == "LT":
        notch = LT_NOTCH.get(grade)
    elif scale == "ST":
        notch = ST_NOTCH.get(grade)
    if notch is None:
        scale = grade = None
    if leg is not None and (scale is None or _LEG_SCALE[leg.group(1)] == scale):
        withdrawn = True

    return ParsedRating(
        raw=segment,
        agency=agency,
        scale=scale,
        grade=grade,
        notch=notch,
        outlook=outlook,
        watch=watch,
        provisional=provisional,
        credit_enhanced=credit_enhanced,
        mld=mld,
        fixed_deposit=fd_paren or fd_scale,
        hybrid=hybrid,
        issuer_not_cooperating=inc,
        withdrawn=withdrawn,
        sovereign=sovereign,
        fund_scale=n_fund > 0,
    )


def _segment_spans(text: str) -> list[tuple[int, int]]:
    masked = text
    for pattern in _FROM_CLAUSE_RES:  # "(Downgraded from CRISIL AA/CRISIL A1+)" holds no new rating
        masked = pattern.sub(lambda m: " " * len(m.group()), masked)
    spans, start = [], 0
    for m in _SPLIT_RE.finditer(masked):
        spans.append((start, m.start()))
        start = m.end()
    spans.append((start, len(text)))
    return [(a, b) for a, b in spans if text[a:b].strip()]


def _merge_gradeless(text: str, spans: list[tuple[int, int]]) -> list[tuple[int, int, ParsedRating]]:
    """Fold a piece with no grade ("IND tA", a stray note) into the previous piece of the same agency."""
    out: list[tuple[int, int, ParsedRating]] = []
    for a, b in spans:
        p = _parse_segment(text[a:b].strip())
        if out and p.grade is None and p.agency in (None, out[-1][2].agency):
            a = out[-1][0]
            out[-1] = (a, b, _parse_segment(text[a:b].strip()))
        else:
            out.append((a, b, p))
    return out


def _share_issuer_status(parts: list[ParsedRating]) -> list[ParsedRating]:
    # A segment without a prefix ("BB+" in "ACUITE A-(CE) & BB+/ACUITE A4+") takes the agency before it.
    agency = next((p.agency for p in parts if p.agency), None)
    for i, p in enumerate(parts):
        agency = p.agency or agency
        if p.agency is None and agency is not None:
            parts[i] = replace(p, agency=agency)
    inc_agencies = {p.agency for p in parts if p.issuer_not_cooperating}
    parts = [replace(p, issuer_not_cooperating=True) if p.agency in inc_agencies else p for p in parts]
    for i in range(len(parts) - 1):
        lt, st = parts[i], parts[i + 1]
        if (lt.scale == "LT" and st.scale == "ST" and lt.agency == st.agency
                and lt.outlook is None and lt.watch is None and (st.outlook or st.watch)):
            parts[i] = replace(lt, outlook=st.outlook, watch=st.watch)
    return parts


def parse_ratings(raw: object) -> list[ParsedRating]:
    """Parse every rating in a string, one ``ParsedRating`` per segment, in order. Never raises.

    Blank input gives an empty list. A single-segment string keeps ``raw`` as given;
    otherwise each segment's ``raw`` is its own text.
    """
    text = _normalize(raw)
    if not text:
        return []
    pieces = _merge_gradeless(text, _segment_spans(text))
    parts = _share_issuer_status([p for _, _, p in pieces])
    if len(parts) == 1:
        return [replace(parts[0], raw=str(raw))]
    return parts


def _rank(indexed: tuple[int, ParsedRating]) -> tuple[int, int, bool, bool, int]:
    i, p = indexed
    special = p.credit_enhanced + p.mld + p.fund_scale
    return p.notch, -special, not p.provisional, bool(p.outlook or p.watch), -i


def parse_rating(raw: object) -> ParsedRating:
    """Parse a rating string to the one rating that matters for mandates. Never raises.

    The worst long-term segment wins, among plain segments if there are any, else
    among all segments. Ties go to the segment with fewer flags among
    credit-enhanced, market-linked and fund-scale, then to a final (not provisional)
    rating, then to one that states an outlook or watch, then to the first. With no
    long-term segment the first short-term one is returned. An unrecognised grade
    leaves ``grade`` as None.
    """
    parts = parse_ratings(raw)
    raw_str = str(raw) if parts else ""
    if not parts:
        return ParsedRating(raw=raw_str)
    lt = [(i, p) for i, p in enumerate(parts) if p.scale == "LT"]
    if lt:
        pool = [(i, p) for i, p in lt if p.is_plain] or lt
        best = max(pool, key=_rank)[1]
    else:
        best = next((p for p in parts if p.scale == "ST"), parts[0])
    return replace(best, raw=raw_str)


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
