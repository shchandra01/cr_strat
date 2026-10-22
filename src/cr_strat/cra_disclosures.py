"""Load the SEBI-mandated CRA disclosure spreadsheets as rating actions.

Every SEBI-registered CRA publishes two files each half-year that together give
a bulk rating history. The annexure numbers differ by circular and agency.

* **Movement of each credit rating**: every upgrade and downgrade in the
  half-year, with the rating before and after, the press-release date, the
  notch difference and the trigger. Reaffirmations, outlook-only changes and
  withdrawals are left out.
* **History of credit rating of all outstanding securities**: one wide row per
  outstanding instrument with the initial rating and its date, each revision
  and its date, and the current rating. Matured and withdrawn instruments drop
  out, so snapshots from several half-years should be combined
  (``combine_rating_actions``).

``load_cra_disclosure`` reads either kind, detected from the headers, into the
``rating_actions.csv`` format that ``load_rating_actions`` reads. It handles
these layout quirks:

* Several sub-tables are stacked in one sheet (securities and other
  instruments, upgrades and downgrades), each with its own header row. The
  direction comes from the header text ("... for Rating Downgrade"), else the
  title rows above it, the sheet name or the file name. The segment comes
  from the titles, the sheet name or the file name.
* Header cells have stray spaces, embedded newlines and misspellings
  ("Intial"). Acuité uses two-row headers ("First Revision" above
  "Rating | Date"). Columns are therefore matched by pattern, and revision
  slots are paired by position, not by ordinal.
* ICRA's structured-finance section adds a "Sub Rating" column ("(SO)") after
  each rating. It is appended to the rating and shifts later columns.
* Continuation rows with a blank issuer (Infomerics: one issuer, several
  instruments, merged cells) take the issuer, sector, type, date and trigger
  from the row above. In history files, a blank date beside a rating takes the
  date from the row above.
* India Ratings' archive CSVs are cp1252, put long-term and short-term ratings
  in separate columns (one action row per leg), and pad every row to about
  16,000 empty columns. Files can exceed 100 MB, so they are streamed and the
  padding is dropped line by line.
* Grade-only ratings (ICRA history, India Ratings archive history: "AA",
  "D INC") get the agency prefix so that ``parse_rating`` recognises them.
* Placeholder cells ("-", "NULL", "Not Reviewed") are skipped. Acuité marks
  issuers whose rating has since been withdrawn with "#"; the mark is moved
  from the name to ``note``.
* Tables without a press-release date (CARE's IG-to-BIG list) and defaults
  lists are not read. A file with no movement or history table raises
  ``ValueError``.

Issuer identity: the files carry names only. India Ratings' history also has
its own Issuer ID, kept as ``agency_issuer_id``. ``issuer_id`` is looked up by
normalised name in an issuer map (CSV with ``name, issuer_id``). Otherwise it
is ``NAME:`` plus the normalised name, and ``unmapped_issuers`` lists those.

Beyond the canonical columns the output has ``prior_rating``, ``trigger``,
``notch_difference``, ``sector``, ``listing_status``,
``outstanding_size_inr_mn``, ``segment`` (securities / other),
``agency_issuer_id``, ``note``, ``source_kind`` (movement, history_initial,
history_revision, history_current), ``source_file``, ``source_sheet`` and
``source_row`` (1-based row in the sheet or CSV).

History rows get an ``action`` by comparing each rating with the previous one
in the same row: Assigned, Upgraded, Downgraded, Withdrawn, Reaffirmed (same
grade, outlook and watch) or Revised (anything else). Brickwork lists yearly
reaffirmations as revisions; they come out as Reaffirmed. In grade-only files
an outlook change also looks like a reaffirmation. The comparison closes up
Brickwork's spaced modifiers ("BWR A +"); the ``rating`` column keeps the
string as published.

The current rating becomes a ``history_current`` row only when it adds
something: a rating that differs from the last dated one, or (Brickwork) its
own date. Without its own date it is dated at the snapshot date: ``as_of`` if
given, else "as on 31 March 2026" or "on 31/03/2026" in the title, else the
month end of a bare "March 26". When none of these is available the date is
left blank, and ``load_rating_actions`` reports a bad date.
"""

from __future__ import annotations

import csv
import datetime as dt
import functools
import re
import unicodedata
import warnings
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
import pandas as pd

from cr_strat.rating_actions import load_rating_actions
from cr_strat.rating_scale import ParsedRating, normalize_agency, parse_rating
from cr_strat.textparse import cell_str, parse_date_text, to_number

ACTION_COLUMNS = (
    "date", "issuer_id", "issuer_name", "agency", "instrument", "rating", "action",
    "prior_rating", "trigger", "notch_difference", "sector", "listing_status",
    "outstanding_size_inr_mn", "segment", "agency_issuer_id", "note",
    "source_kind", "source_file", "source_sheet", "source_row",
)
KINDS = ("movement", "history")
# In the order combine_rating_actions prefers them when rows coincide.
SOURCE_KINDS = ("movement", "history_revision", "history_initial", "history_current")

# How each agency's ratings start, for grade-only cells.
RATING_PREFIX = {
    "CRISIL": "CRISIL ", "ICRA": "[ICRA]", "CARE": "CARE ", "IND": "IND ",
    "ACUITE": "ACUITE ", "INFOMERICS": "IVR ", "BRICKWORK": "BWR ",
}

_SNO = re.compile(r"^(?:s|sr|sl|si)\.?\s*no\b")
_COMMON_RULES = (
    ("sno", _SNO),
    ("issuer_code", re.compile(r"issuer\s*id")),
    ("listing", re.compile(r"listing|listed")),
)
_TAIL_RULES = (
    ("sector", re.compile(r"sector")),
    ("industry", re.compile(r"industry")),
    ("instrument", re.compile(r"securit|instrument|\btype\b")),
    ("issuer", re.compile(r"issuer|\bname\b")),
)
_MOVEMENT_RULES = _COMMON_RULES + (
    ("trigger", re.compile(r"trigger")),
    ("notch", re.compile(r"notch")),
    ("prior", re.compile(r"\bprior\b")),
    ("post", re.compile(r"\bpost\b")),
    ("date", re.compile(r"\bdate\b|press\s*release")),
    ("size", re.compile(r"size|million|amount")),
) + _TAIL_RULES
_HISTORY_RULES = _COMMON_RULES + (
    ("size", re.compile(r"size|million|amount")),
    ("sub", re.compile(r"sub[\s-]*rating")),
    ("date", re.compile(r"date")),
    ("current", re.compile(r"current|outstanding")),
    ("rating", re.compile(r"rating|revision")),
) + _TAIL_RULES
_RATING_WORD = re.compile(r"\brating", re.I)  # not "cooperating" in rating strings
_NAME_WORD = re.compile(r"\bissuer\b|\bname\b", re.I)
_SHORT_TERM = re.compile(r"short[\s-]*term")
_INITIAL = re.compile(r"\bini?tial\b")
_REVISION = re.compile(r"revision")

_PLACEHOLDER = re.compile(
    r"^(?:-+|—|n\.?\s*a\.?|#?n/a|<na>|nil|null|nan|none|not\s+(?:reviewed|applicable))$", re.I)
_SPACED_MODIFIER = re.compile(r"(?<![A-Za-z0-9])(AAA|AA|A|BBB|BB|B|C|A[1-4])\s+([+-])(?![A-Za-z0-9])")
_UP = re.compile(r"upgrad", re.I)
_DOWN = re.compile(r"downgrad", re.I)
_OTHER_SEGMENT = re.compile(
    r"other\s+than\s+securit|other\s+financial|other\s+instrument|\bfios\b|non[\s-]*securit", re.I)
_SECURITIES = re.compile(r"securit", re.I)
_GENERIC_TITLE = re.compile(r"outstanding\s+securities", re.I)
_AS_OF = re.compile(r"(?:\bas\s+(?:on|of|at)\b|\bon\b)\s*:?\s*(.+)", re.I)
_MONTH_YEAR = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?[\s,'-]+(\d{4}|\d{2})\b(?!\s*[-/.]\d)", re.I)
_DATE_FORMATS = (
    "%d-%b-%y", "%d-%b-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y",
    "%d.%m.%y", "%d-%m-%y", "%d %b %Y", "%d %B %Y", "%d-%B-%Y", "%B %d, %Y", "%b %d, %Y",
    "%Y-%m-%d %H:%M:%S",
)
_EXCEL_EPOCH = pd.Timestamp("1899-12-30")

_FORMERLY_RE = re.compile(r"\(\s*(?:formerly|erstwhile|earlier|previously|old\s+name|now)\b[^)]*\)?")
_FORMERLY_TAIL_RE = re.compile(r"\b(?:formerly|erstwhile)\b.*$")
_NAME_DROP = {"limited", "ltd", "private", "pvt", "the"}


# --- cells -------------------------------------------------------------------

def _get(row: list, j: int | None) -> object:
    return row[j] if j is not None and j < len(row) else None


def _filled(value: object) -> bool:
    """Cheap non-blank test, to skip ``cell_str`` on the many empty cells of wide sheets."""
    if value is None:
        return False
    if isinstance(value, float):
        return value == value
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _text(value: object) -> str:
    """Cell as text; whole floats lose their ".0"."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return cell_str(value)


def _is_blank(text: str) -> bool:
    return not text or bool(_PLACEHOLDER.match(text))


def _to_date(value: object) -> tuple[pd.Timestamp | None, str | None]:
    """``(date, unparsed_text)``: Excel datetimes and serials, dd-Mon-yy and other day-first strings."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None, None
    if isinstance(value, (dt.datetime, dt.date, pd.Timestamp)):
        return pd.Timestamp(value).normalize(), None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 20000 < value < 80000:
        return _EXCEL_EPOCH + pd.Timedelta(days=int(value)), None
    text = _text(value)
    if _is_blank(text):
        return None, None
    for fmt in _DATE_FORMATS:
        try:
            return pd.Timestamp(dt.datetime.strptime(text, fmt)), None
        except ValueError:
            continue
    parsed = parse_date_text(text)
    return (parsed, None) if parsed is not None else (None, text)


def _rating(value: object, agency: str, sub: object = None) -> str | None:
    """Rating cell as text with the agency prefix added when missing; None for blanks."""
    if not _filled(value):
        return None
    text = cell_str(value)
    if _is_blank(text):
        return None
    sub_text = cell_str(sub) if _filled(sub) else ""
    if not _is_blank(sub_text):
        text = f"{text} {sub_text}"
    return _prefixed(text, agency)


@functools.lru_cache(maxsize=65536)
def _prefixed(text: str, agency: str) -> str:
    return text if normalize_agency(text) is not None else RATING_PREFIX[agency] + text


def _squash(text: str | None) -> str:
    return re.sub(r"\s+", "", text or "").upper()


def _same_rating(a: str | None, b: str | None) -> bool:
    return _squash(a) == _squash(b)


@functools.lru_cache(maxsize=65536)
def _parsed(text: str) -> ParsedRating:
    """Parsed rating for comparisons; Brickwork's spaced modifiers ("BWR A +") are closed up first."""
    return parse_rating(_SPACED_MODIFIER.sub(r"\1\2", text))


def _compare(prev: str | None, cur: str) -> str:
    """Action implied by moving from rating ``prev`` to ``cur``."""
    p_cur = _parsed(cur)
    if p_cur.withdrawn:
        return "Withdrawn"
    if prev is None:
        return "Assigned"
    p_prev = _parsed(prev)
    if (p_cur.grade == "D") != (p_prev.grade == "D") and p_cur.notch and p_prev.notch:
        # A bare "D" parses as long-term even after a short-term grade; default is worst on both scales.
        return "Downgraded" if p_cur.grade == "D" else "Upgraded"
    if p_cur.notch is None or p_prev.notch is None or p_cur.scale != p_prev.scale:
        return "Revised"
    if p_cur.notch != p_prev.notch:
        return "Upgraded" if p_cur.notch < p_prev.notch else "Downgraded"
    flags = ("outlook", "watch", "issuer_not_cooperating", "credit_enhanced", "provisional")
    same = all(getattr(p_cur, f) == getattr(p_prev, f) for f in flags)
    return "Reaffirmed" if same else "Revised"


def _size_multiplier(header: str) -> float:
    """Factor to INR million from a size header's unit."""
    h = header.lower()
    if re.search(r"crore|\bcrs?\b", h):
        return 10.0
    if re.search(r"lakh|\blacs?\b", h):
        return 0.1
    if re.search(r"billion|\bbn\b", h):
        return 1000.0
    return 1.0


# --- issuer names --------------------------------------------------------------

def normalize_issuer_name(name: object) -> str:
    """Matching key for an issuer name: lower case, no punctuation, legal-form words and "(formerly ...)" dropped."""
    return _normalize_name(cell_str(name))


@functools.lru_cache(maxsize=65536)
def _normalize_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = re.sub(r"^m\s*/\s*s\.?\s+", "", text.replace("#", " ").strip())
    text = _FORMERLY_RE.sub(" ", text)
    text = _FORMERLY_TAIL_RE.sub(" ", text)
    text = re.sub(r"['’`]", "", text.replace("&", " and "))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(t for t in text.split() if t not in _NAME_DROP)


def read_issuer_map(source: str | Path | pd.DataFrame | Mapping[str, str] | None) -> dict[str, str]:
    """``{normalised name: issuer_id}`` from a CSV/DataFrame with columns name, issuer_id (or a dict)."""
    if source is None:
        return {}
    if isinstance(source, Mapping):
        pairs = list(source.items())
    else:
        df = source if isinstance(source, pd.DataFrame) else pd.read_csv(source, dtype=str)
        cols = {str(c).strip().lower(): c for c in df.columns}
        if "name" not in cols or "issuer_id" not in cols:
            raise ValueError("issuer map needs columns name, issuer_id")
        pairs = list(zip(df[cols["name"]], df[cols["issuer_id"]]))
    out: dict[str, str] = {}
    conflicts = set()
    for name, issuer_id in pairs:
        key, value = normalize_issuer_name(name), _text(issuer_id)
        if not key or not value:
            continue
        if out.setdefault(key, value) != value:
            conflicts.add(key)
    if conflicts:
        raise ValueError(f"issuer map gives several issuer_ids for: {sorted(conflicts)}")
    return out


def _issuer_id(name: str, issuer_map: Mapping[str, str]) -> str | None:
    key = normalize_issuer_name(name)
    if not key:
        return None
    return issuer_map.get(key) or "NAME:" + key.upper()


# --- reading -------------------------------------------------------------------

def _iter_csv_rows(path: Path) -> Iterator[list[str]]:
    """CSV rows without trailing empty cells; each line is decoded as UTF-8, else cp1252."""
    def lines() -> Iterator[str]:
        with open(path, "rb") as fh:
            for raw in fh:
                raw = raw.rstrip(b"\r\n").rstrip(b", \t")
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    text = raw.decode("cp1252", errors="replace")
                yield text.lstrip("\ufeff") + "\n"

    for row in csv.reader(lines()):
        while row and not row[-1].strip():
            row.pop()
        yield row


def _read_sheets(path: Path) -> dict[str, list[list]]:
    """Rows of every sheet as lists of raw cell values (datetimes stay datetimes)."""
    suffix = path.suffix.lower()
    if suffix in (".csv", ".txt"):
        return {path.stem: list(_iter_csv_rows(path))}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # openpyxl: "Workbook contains no default style", header/footer
        if suffix in (".xlsx", ".xlsm"):
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            try:
                return {ws.title: [list(r) for r in ws.iter_rows(values_only=True)] for ws in wb.worksheets}
            finally:
                wb.close()
        sheets = pd.read_excel(path, sheet_name=None, header=None, dtype=object)
    return {str(name): frame.values.tolist() for name, frame in sheets.items()}


# --- layout --------------------------------------------------------------------

@dataclass
class _Section:
    kind: str
    header_row: int
    data_start: int
    cols: dict[str, int]
    labels: dict[str, str]
    direction: str | None = None
    segment: str | None = None
    # history only: (rating, date, sub) column per slot; slot 0 is the initial rating
    slots: list[list[int | None]] = field(default_factory=list)
    current: list[int | None] = field(default_factory=lambda: [None, None, None])


def _classify(label: str, rules: tuple) -> str | None:
    text = label.lower()
    for name, pattern in rules:
        if pattern.search(text):
            return name
    return None


def _direction(text: str) -> str | None:
    up, down = bool(_UP.search(text)), bool(_DOWN.search(text))
    if up == down:
        return None
    return "Upgraded" if up else "Downgraded"


def _segment(text: str) -> str | None:
    text = _GENERIC_TITLE.sub(" ", text)
    if _OTHER_SEGMENT.search(text):
        return "other"
    return "securities" if _SECURITIES.search(text) else None


def _plain(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", " ", name)


def _row_labels(row: list) -> list[str]:
    return [cell_str(v) for v in row]


def _merge_subheader(top: list[str], sub_row: list) -> list[str] | None:
    """Acuité-style second header row ("Rating", "Date" under each revision): merged labels, or None."""
    sub = _row_labels(sub_row)
    filled = [s for s in sub if s]
    if not filled or any(not isinstance(v, str) for v in sub_row if cell_str(v)):
        return None
    if sum(s.lower() in ("rating", "date") for s in filled) < 2:
        return None
    merged, carry = [], ""
    for j in range(max(len(top), len(sub))):
        t = top[j] if j < len(top) else ""
        s = sub[j] if j < len(sub) else ""
        carry = t or (carry if s else "")
        merged.append(f"{carry} {s}".strip())
    return merged


def _header(rows: list[list], i: int, kinds: tuple[str, ...]) -> _Section | None:
    """The section whose header is row ``i``, if it is one."""
    if any(isinstance(v, (dt.date, dt.datetime)) for v in rows[i]):
        return None
    texts = [v for v in rows[i] if isinstance(v, str)]
    if not any(_RATING_WORD.search(t) for t in texts) or not any(_NAME_WORD.search(t) for t in texts):
        return None
    labels = _row_labels(rows[i])
    lowered = [s.lower() for s in labels]
    if "movement" in kinds:
        fields = [_classify(s, _MOVEMENT_RULES) if s else None for s in labels]
        if {"prior", "post", "issuer", "date"} <= set(fields):
            cols, names = {}, {}
            for j, (f, s) in enumerate(zip(fields, lowered)):
                if f in ("prior", "post") and _SHORT_TERM.search(s):
                    f = f + "_st"
                if f and f not in cols:
                    cols[f], names[f] = j, labels[j]
            return _Section("movement", i, i + 1, cols, names, direction=_direction(" ".join(labels)))
    if "history" in kinds and any(_INITIAL.search(s) for s in lowered) and any(_REVISION.search(s) for s in lowered):
        start = i + 1
        if i + 1 < len(rows) and (merged := _merge_subheader(labels, rows[i + 1])) is not None:
            labels, start = merged, i + 2
        fields = [_classify(s, _HISTORY_RULES) if s else None for s in labels]
        if "issuer" not in fields or "rating" not in fields:
            return None
        section = _Section("history", i, start, {}, {})
        target: list[int | None] | None = None
        for j, f in enumerate(fields):
            is_current = f == "current" or (f == "date" and "current" in labels[j].lower())
            if f == "rating":
                target = [j, None, None]
                section.slots.append(target)
            elif is_current and f == "date":
                section.current[1] = j
            elif is_current:
                section.current[0] = j
                target = section.current
            elif f == "date" and target is not None and target[1] is None:
                target[1] = j
            elif f == "sub" and target is not None:
                target[2] = j
            elif f and f not in section.cols:
                section.cols[f], section.labels[f] = j, labels[j]
        return section
    return None


def _title_text(row: list) -> str | None:
    """Text of a title row: one or two non-empty cells, all text."""
    cells = []
    for v in row:
        if _filled(v):
            if len(cells) == 2 or not isinstance(v, str):
                return None
            cells.append(v)
    if cells:
        return " ".join(cell_str(v) for v in cells)
    return None


def _as_of(texts: list[str]) -> pd.Timestamp | None:
    """Snapshot date from title text: "as on 31 March 2026", "on 31/03/2026", else month-end of "March 26"."""
    for text in texts:
        m = _AS_OF.search(text)
        if m and (date := parse_date_text(m.group(1))) is not None:
            return date
    for text in texts:
        matches = list(_MONTH_YEAR.finditer(text))
        if matches:
            month, year = matches[-1].group(1), matches[-1].group(2)
            year = int(year) + (2000 if len(year) == 2 else 0)
            start = pd.to_datetime(f"1 {month[:3]} {year}", format="%d %b %Y", errors="coerce")
            if not pd.isna(start):
                return start + pd.offsets.MonthEnd(0)
    return None


# --- rows ----------------------------------------------------------------------

def _base(section: _Section, row: list, agency: str, where: tuple[str, str, int]) -> dict:
    cols = section.cols
    size_col = cols.get("size")
    size = to_number(_get(row, size_col))
    if size_col is not None and not pd.isna(size):
        size *= _size_multiplier(section.labels["size"])
    return {
        "issuer_name": cell_str(_get(row, cols.get("issuer"))),
        "agency_issuer_id": _text(_get(row, cols.get("issuer_code"))) or None,
        "agency": agency,
        "instrument": cell_str(_get(row, cols.get("instrument"))) or None,
        "sector": cell_str(_get(row, cols.get("sector"))) or None,
        "listing_status": cell_str(_get(row, cols.get("listing"))) or None,
        "outstanding_size_inr_mn": size,
        "segment": section.segment,
        "source_file": where[0],
        "source_sheet": where[1],
        "source_row": where[2],
    }


def _fill_from(rec: dict, prev: dict | None, keys: tuple[str, ...]) -> None:
    """Continuation row: blank fields take the previous row's values."""
    if prev is None:
        return
    for k in keys:
        if not cell_str(rec.get(k)):
            rec[k] = prev.get(k)


def _movement_rows(section: _Section, rows: list[list], end: int, agency: str,
                   where: tuple[str, str]) -> Iterator[dict]:
    cols = section.cols
    prev: dict | None = None
    legs = [(cols.get("prior"), cols.get("post"), None), (cols.get("prior_st"), cols.get("post_st"), "short-term leg")]
    for r in range(section.data_start, end):
        row = rows[r]
        posts = [_rating(_get(row, post), agency) for _, post, _ in legs]
        if not any(posts):
            continue
        rec = _base(section, row, agency, (*where, r + 1))
        rec["date_raw"] = _get(row, cols.get("date"))
        rec["trigger"] = cell_str(_get(row, cols.get("trigger"))) or None
        rec["notch_difference"] = _text(_get(row, cols.get("notch"))) or None
        if not rec["issuer_name"]:
            _fill_from(rec, prev, ("issuer_name", "agency_issuer_id", "sector", "instrument", "listing_status",
                                   "date_raw", "trigger", "notch_difference"))
        prev = rec
        for (prior_col, _, leg_note), post in zip(legs, posts):
            if post is None:
                continue
            prior = _rating(_get(row, prior_col), agency)
            yield {**rec, "rating": post, "prior_rating": prior, "source_kind": "movement",
                   "action": section.direction or _compare(prior, post), "note": leg_note}


def _history_rows(section: _Section, rows: list[list], end: int, agency: str,
                  where: tuple[str, str], as_of: pd.Timestamp | None) -> Iterator[dict]:
    prev: dict | None = None
    prev_dates: list[object] = [None] * len(section.slots)
    cur_rating, cur_date, cur_sub = section.current
    for r in range(section.data_start, end):
        row = rows[r]
        ratings = [_rating(_get(row, s[0]), agency, _get(row, s[2])) for s in section.slots]
        current = _rating(_get(row, cur_rating), agency, _get(row, cur_sub))
        if not any(ratings) and current is None:
            continue
        rec = _base(section, row, agency, (*where, r + 1))
        dates = [_get(row, s[1]) for s in section.slots]
        if not rec["issuer_name"] and prev is not None:
            _fill_from(rec, prev, ("issuer_name", "agency_issuer_id", "sector", "instrument", "listing_status"))
            dates = [d if (not ratings[k] or cell_str(d)) else prev_dates[k] for k, d in enumerate(dates)]
        prev, prev_dates = rec, dates

        last, last_date = None, None
        for k, rating in enumerate(ratings):
            if rating is None:
                continue
            kind = "history_initial" if k == 0 else "history_revision"
            yield {**rec, "rating": rating, "prior_rating": last, "date_raw": dates[k],
                   "source_kind": kind, "action": _compare(last, rating)}
            last, last_date = rating, _to_date(dates[k])[0]
        if current is None:
            continue
        own_date = _get(row, cur_date)
        unchanged = last is not None and (_same_rating(current, last) or _compare(last, current) == "Reaffirmed")
        if cell_str(own_date):
            if unchanged and _to_date(own_date)[0] == last_date:
                continue
            date_raw, note = own_date, None
        elif unchanged:
            continue
        else:
            date_raw = as_of
            note = ("current rating differs from the last dated rating" if last is not None
                    else "only the current rating is given")
        yield {**rec, "rating": current, "prior_rating": last, "date_raw": date_raw,
               "source_kind": "history_current", "action": _compare(last, current), "note": note}


def _finish(records: list[dict], issuer_map: Mapping[str, str]) -> pd.DataFrame:
    for rec in records:
        date, unparsed = _to_date(rec.pop("date_raw", None))
        rec["date"] = date
        notes = [n for n in (rec.get("note"), f"unparsed date: {unparsed}" if unparsed else None) if n]
        name = rec["issuer_name"] or ""
        if "#" in name:
            name = re.sub(r"\s*#\s*", " ", name).strip()
            notes.append("rating withdrawn (#)")
        rec["issuer_name"] = name or None
        rec["issuer_id"] = _issuer_id(name, issuer_map)
        rec["note"] = "; ".join(notes) or None
    df = pd.DataFrame(records, columns=list(ACTION_COLUMNS))
    df["date"] = pd.to_datetime(df["date"])
    df["outstanding_size_inr_mn"] = pd.to_numeric(df["outstanding_size_inr_mn"])
    df["source_row"] = df["source_row"].astype("Int64")
    return df


def load_cra_disclosure(
    path: str | Path,
    agency: str,
    kind: str | None = None,
    issuer_map: str | Path | pd.DataFrame | Mapping[str, str] | None = None,
    *,
    as_of: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Rating-action rows (``ACTION_COLUMNS``) from one movement or history disclosure file.

    ``agency`` is any name ``normalize_agency`` knows ("ICRA", "India Ratings",
    "Acuite", ...). ``kind`` restricts the tables read to "movement" or
    "history"; by default both are detected. ``issuer_map`` is a CSV path,
    DataFrame or dict mapping issuer names to issuer_ids. ``as_of`` dates
    history rows that come only from the current-rating column, overriding
    the date read from the file's title.
    """
    path = Path(path)
    code = normalize_agency(agency)
    if code is None:
        raise ValueError(f"unknown agency: {agency!r}")
    if kind is not None and kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, not {kind!r}")
    kinds = (kind,) if kind else KINDS
    names = read_issuer_map(issuer_map)
    given_as_of = pd.Timestamp(as_of).normalize() if as_of is not None else None

    sheets = _read_sheets(path)
    records: list[dict] = []
    found = 0
    for sheet, rows in sheets.items():
        direction = _direction(_plain(sheet)) or _direction(_plain(path.stem))
        segment = _segment(_plain(sheet)) or _segment(_plain(path.stem))
        sections: list[_Section] = []
        titles: list[str] = []  # text above the first table, where the snapshot date is
        for i, row in enumerate(rows):
            section = _header(rows, i, kinds)
            if section is not None:
                section.direction = section.direction or direction
                section.segment = segment
                if not sections:
                    titles.extend(s for s in _row_labels(row) if s)
                sections.append(section)
                continue
            text = _title_text(row)
            if text:
                if not sections:
                    titles.append(text)
                direction = _direction(text) or direction
                segment = _segment(text) or segment
        sheet_as_of = given_as_of or _as_of(titles)
        found += len(sections)
        for n, section in enumerate(sections):
            end = sections[n + 1].header_row if n + 1 < len(sections) else len(rows)
            where = (path.name, sheet)
            if section.kind == "movement":
                records.extend(_movement_rows(section, rows, end, code, where))
            else:
                records.extend(_history_rows(section, rows, end, code, where, sheet_as_of))
    if not found:
        wanted = kind or "movement or history"
        raise ValueError(f"{path.name}: no {wanted} table found (no header row with the expected columns)")
    return _finish(records, names)


def load_cra_disclosures(
    paths: Iterable[str | Path],
    agency: str,
    kind: str | None = None,
    issuer_map: str | Path | pd.DataFrame | Mapping[str, str] | None = None,
    *,
    as_of: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Load several files of one agency and drop actions repeated across them."""
    names = read_issuer_map(issuer_map)
    frames = [load_cra_disclosure(p, agency, kind, names, as_of=as_of) for p in paths]
    return combine_rating_actions(frames)


def _key_text(value: object) -> str:
    return re.sub(r"\s+", " ", cell_str(value)).casefold()


def combine_rating_actions(frames: Iterable[pd.DataFrame]) -> pd.DataFrame:
    """Concatenate rating-action frames, dropping repeats of the same agency, issuer, instrument, date and rating.

    Overlapping half-year snapshots repeat most rows. When a movement row and a
    history row coincide, the movement row (which has the prior rating and
    trigger) is kept.
    """
    frames = [f for f in frames if f is not None and len(f)]
    if not frames:
        return pd.DataFrame(columns=list(ACTION_COLUMNS))
    df = pd.concat(frames, ignore_index=True)
    key = pd.DataFrame({
        "agency": df["agency"].map(_key_text),
        "issuer_id": df["issuer_id"].map(_key_text),
        "instrument": df["instrument"].map(_key_text) if "instrument" in df else "",
        "date": pd.to_datetime(df["date"], format="ISO8601", errors="coerce"),
        "rating": df["rating"].map(_key_text),
    })
    rank = {k: i for i, k in enumerate(SOURCE_KINDS)}
    if "source_kind" in df:
        priority = df["source_kind"].map(rank).fillna(len(rank))
    else:
        priority = pd.Series(0, index=df.index)
    order = priority.sort_values(kind="stable").index
    duplicated = key.loc[order].duplicated(keep="first")
    keep = duplicated.index[~duplicated.to_numpy()].sort_values()
    return df.loc[keep].reset_index(drop=True)


def unmapped_issuers(actions: pd.DataFrame) -> pd.DataFrame:
    """Issuers keyed by name only (``NAME:``), with the spellings seen, to build an issuer map from."""
    mask = actions["issuer_id"].fillna("").astype(str).str.startswith("NAME:")
    sub = actions[mask]
    columns = ["issuer_id", "issuer_name", "spellings", "agencies", "rows", "first_date", "last_date"]
    if sub.empty:
        return pd.DataFrame(columns=columns)
    out = sub.groupby("issuer_id", sort=True).agg(
        issuer_name=("issuer_name", "first"),
        spellings=("issuer_name", lambda s: " | ".join(sorted(set(s.dropna())))),
        agencies=("agency", lambda s: ",".join(sorted(set(s.dropna())))),
        rows=("issuer_id", "size"),
        first_date=("date", "min"),
        last_date=("date", "max"),
    )
    return out.reset_index()[columns]


def disclosure_summary(actions: pd.DataFrame) -> pd.DataFrame:
    """Per source file and kind: rows, date range, issuers, unmapped issuers and the parse_ok share.

    ``parse_ok`` is what ``load_rating_actions`` reports for the rows.
    """
    columns = ["source_file", "source_kind", "rows", "date_min", "date_max", "issuers",
               "unmapped_issuers", "parse_ok", "parse_ok_share"]
    if actions.empty:
        return pd.DataFrame(columns=columns)
    df = actions.reset_index(drop=True)
    parsed = load_rating_actions(df[["date", "issuer_id", "issuer_name", "agency", "instrument", "rating", "action"]])
    unmapped = df["issuer_id"].fillna("").astype(str).str.startswith("NAME:")
    df = df.assign(parse_ok=parsed["parse_ok"].to_numpy(), unmapped_id=df["issuer_id"].where(unmapped))
    out = df.groupby(["source_file", "source_kind"], sort=True).agg(
        rows=("rating", "size"),
        date_min=("date", "min"),
        date_max=("date", "max"),
        issuers=("issuer_id", "nunique"),
        unmapped_issuers=("unmapped_id", "nunique"),
        parse_ok=("parse_ok", "sum"),
    ).reset_index()
    out["parse_ok_share"] = out["parse_ok"] / out["rows"]
    return out[columns]
