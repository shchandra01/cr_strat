"""The long format every macro and global source is parsed into, the refusal, and the period conventions.

``mospi`` (CPI, IIP, national accounts, WPI from MoSPI's eSankhyiki API) and
``global_sources`` (US Treasury, New York Fed, EIA, the Federal Reserve's
H.10, BIS) parse responses into ``Observation`` rows; ``macro_sources`` puts
them together (catalogue, fetch). This module holds what all of them share.

**One row per value.** An ``Observation`` is one published value: the series
it belongs to (``series_id``), its reference period, the value **as the
publisher's text** (``"110.05"``, ``"3.90"``: numbers are checked, never
re-formatted, so a store can tell a revision from a rounding), its unit, and
the source's own fields that matter: the base year, the Current or Back
series, area, sector, level and code of a CPI node, the estimate type of an
annual national-accounts row, the status a publisher prints (``F``), the New
York Fed's revision indicator and footnote, BIS's observation status. A
missing value (``ND``, ``NaN`` with status ``M``, a blank cell, ``null``) is
no row. MoSPI's ``llms.txt`` asks AI systems that use eSankhyiki data to keep
the dataset name, indicator, value, unit, reference period, source and
publication or update information: ``dataset``, ``indicator``, ``value``,
``unit`` and ``period`` are on every row, the source in ``source`` (and the
request's URL in the store), the update information in the series catalogue.

**Periods** (``period_type``):

==============  =============  ==========================================================================
``day``         ``2026-11-17``  the observation date (trade date, curve date, rate-in-force date)
``month``       ``2026-10``     a calendar month
``fy_quarter``  ``2026-27Q1``   a quarter of India's financial year (April to March): Q1 is April to
                               June 2026, Q2 July to September, Q3 October to December 2026, Q4 January
                               to March 2027
``fy``          ``2025-26``     India's financial year, 1 April 2025 to 31 March 2026
==============  =============  ==========================================================================

``period_start`` and ``period_end`` are ISO dates on every row.

**Refusals.** ``SourceError`` refuses a response as a whole, never half
reads it: a missing column or field, a value that should be a number and is
not (other than each source's documented missing-value markers), a period
repeated with another value, a page count that does not add up, a unit that
changed, a layout no sample was seen of. The message names the file (or the
name the caller gave the bytes) and the row. Softer anomalies (a period
repeated with the same value, a quirk the publisher documents) become notes
of the ``ParseResult``.
"""

from __future__ import annotations

import calendar as _calendar
import datetime as dt
import json
import re
import urllib.parse
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field, fields

PERIOD_TYPES = ("day", "month", "fy_quarter", "fy")
MONTHS = {name.lower(): number for number, name in enumerate(_calendar.month_name) if name}
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)")  # a point needs a digit after it: "93." is a cut-off text
_FY = re.compile(r"(\d{4})-(\d{2})")
_FY_QUARTER = re.compile(r"(\d{4})-(\d{2})Q([1-4])")
_MONTH = re.compile(r"(\d{4})-(\d{2})")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
MIN_YEAR, MAX_YEAR = 1940, 2100


class SourceError(ValueError):
    """A response refused as a whole: it cannot be read without guessing. The message names the file and the row."""


@dataclass(frozen=True)
class Request:
    """One GET a source builds: the URL, which parser reads the answer (``kind``), and the ``Accept`` header.

    ``query`` keeps the URL's parameters in the order sent (the same request
    always gives the same URL, which matters for conditional requests:
    validators belong to a URL), so a follow-up such as the next page can be
    built from it.
    """

    source: str
    kind: str
    url: str
    accept: str = "*/*"
    query: tuple[tuple[str, str], ...] = ()
    note: str = ""

    @property
    def host(self) -> str:
        return (urllib.parse.urlsplit(self.url).hostname or "").lower()


def build_url(base: str, params: Iterable[tuple[str, object]]) -> tuple[str, tuple[tuple[str, str], ...]]:
    """``base`` with ``params`` (in order; ``None`` left out; sequences joined by commas) and the pairs sent.

    Spaces are encoded as ``%20`` and commas kept, as MoSPI's API takes
    ``year=2025,2026``.
    """
    pairs: list[tuple[str, str]] = []
    for key, value in params:
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            value = ",".join(str(v) for v in value)
        pairs.append((key, str(value)))
    if not pairs:
        return base, ()
    query = "&".join(f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe=',')}" for k, v in pairs)
    return f"{base}?{query}", tuple(pairs)


@dataclass(frozen=True)
class Observation:
    """One published value in the long format every source shares (see the module docstring)."""

    source: str
    series_id: str
    period: str
    period_type: str
    period_start: str
    period_end: str
    value: str
    unit: str
    dataset: str = ""
    indicator: str = ""
    base: str = ""
    series_type: str = ""
    area: str = ""
    sector: str = ""
    level: str = ""
    code: str = ""
    label: str = ""
    status: str = ""
    revision: str = ""
    footnote: str = ""
    imputation: str = ""
    notes: str = ""

    def as_dict(self) -> dict[str, str]:
        return asdict(self)

    @property
    def key(self) -> tuple[str, str]:
        return (self.series_id, self.period)


OBSERVATION_COLUMNS = tuple(f.name for f in fields(Observation))


@dataclass
class ParseResult:
    """What one response held: its observations (in the response's order), notes, and facts about the response.

    ``meta`` holds what a response says about itself: MoSPI's page,
    ``total_pages``, ``total_records`` and ``records_per_page`` (and
    ``rows``, the data rows read); EIA's ``release_date`` and
    ``next_release_date``; H.10's ``release_date``. All text.
    """

    source: str
    name: str
    layout: str
    observations: list[Observation] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    meta: dict[str, str] = field(default_factory=dict)

    def by_key(self) -> dict[tuple[str, str], Observation]:
        return {o.key: o for o in self.observations}

    def series_ids(self) -> list[str]:
        return sorted({o.series_id for o in self.observations})

    def values(self, series_id: str) -> dict[str, str]:
        """``{period: value}`` of one series."""
        return {o.period: o.value for o in self.observations if o.series_id == series_id}


class ObservationSet:
    """Observations of one response, keyed by ``(series_id, period)``: a repeat with another value is refused."""

    def __init__(self, name: str):
        self.name = name
        self.rows: dict[tuple[str, str], Observation] = {}
        self.repeats = 0

    def add(self, obs: Observation, where: str) -> None:
        held = self.rows.get(obs.key)
        if held is None:
            self.rows[obs.key] = obs
            return
        if held.value != obs.value:
            raise SourceError(f"{self.name}, {where}: {obs.series_id} for {obs.period} is {obs.value}, but an earlier "
                              f"row of the same response gave {held.value}; refused rather than pick one")
        self.repeats += 1

    def observations(self) -> list[Observation]:
        return list(self.rows.values())

    def notes(self) -> list[str]:
        if not self.repeats:
            return []
        return [f"{self.repeats} values repeated with the same value in the response; each kept once"]


# --- Numbers and text -----------------------------------------------------------------------------

def number_text(value: object, where: str) -> str:
    """A value that must be a number, as the publisher's text (``"110.05"``); ``SourceError`` otherwise.

    A plain decimal: optional sign, digits, optional fraction (a point with a
    digit after it: ``93.`` is refused, as the end of an answer cut short
    looks like that); no thousands separator, no exponent, no spaces inside.
    JSON numbers read with ``parse_float=str`` arrive as their text already.
    """
    if isinstance(value, bool) or value is None:
        raise SourceError(f"{where}: {value!r} is not a number")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):  # a JSON float read without parse_float=str: its text is lost; refuse
        raise SourceError(f"{where}: {value!r} was read as a float, losing the publisher's text (read JSON with "
                          "parse_float=str)")
    text = str(value).strip()
    if not _NUMBER.fullmatch(text):
        raise SourceError(f"{where}: {value!r} is not a number")
    return text


def slug(text: str) -> str:
    """Lower-case letters and digits joined by single underscores: ``"Fuel & Power"`` -> ``fuel_power``."""
    out = re.sub(r"[^a-z0-9]+", "_", str(text).lower().replace("&", " ").replace("'", ""))
    return out.strip("_")


def load_json(body: bytes, name: str) -> object:
    """JSON from bytes, numbers kept as their text (``parse_float=str``, ``parse_int=str``)."""
    text = _text(body, name)
    stripped = text.lstrip()
    if stripped[:1] == "<":
        raise SourceError(f"{name}: a web page (HTML or XML), not JSON: {stripped[:80]!r}")
    try:
        return json.loads(text, parse_float=str, parse_int=str)
    except ValueError as exc:
        raise SourceError(f"{name}: not readable JSON ({exc})") from None


def _text(body: bytes, name: str) -> str:
    if isinstance(body, str):
        return body
    try:
        return bytes(body).decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SourceError(f"{name}: not UTF-8 text ({exc})") from None


def text_of(body: bytes, name: str) -> str:
    """The body as text (UTF-8, a byte-order mark dropped); ``SourceError`` if it is not UTF-8."""
    return _text(body, name)


# --- Periods ------------------------------------------------------------------------------------------

def _year(value: object, where: str) -> int:
    text = str(value).strip() if value is not None else ""
    if not re.fullmatch(r"\d{4}", text) or not MIN_YEAR <= int(text) <= MAX_YEAR:
        raise SourceError(f"{where}: year {value!r} is not a four-digit year")
    return int(text)


def year_of(value: object, where: str) -> int:
    """A four-digit year from a string or number; ``SourceError`` otherwise."""
    return _year(value, where)


def month_number(value: object, where: str) -> int:
    """An English month name (``"October"``) as 1 to 12; ``SourceError`` otherwise."""
    number = MONTHS.get(str(value).strip().lower()) if isinstance(value, str) else None
    if number is None:
        raise SourceError(f"{where}: month {value!r} is not an English month name")
    return number


def day_period(day: dt.date) -> tuple[str, str, str, str]:
    """``(period, period_type, start, end)`` of a day."""
    text = day.isoformat()
    return text, "day", text, text


def month_period(year: int, month: int) -> tuple[str, str, str, str]:
    """``(period, period_type, start, end)`` of a calendar month."""
    last = _calendar.monthrange(year, month)[1]
    return f"{year}-{month:02d}", "month", dt.date(year, month, 1).isoformat(), dt.date(year, month, last).isoformat()


def fy_start_year(text: str, where: str) -> int:
    """The first calendar year of an Indian financial year written ``2025-26``; ``SourceError`` otherwise."""
    m = _FY.fullmatch(str(text).strip()) if isinstance(text, str) else None
    if m is None or (int(m.group(1)) + 1) % 100 != int(m.group(2)) or not MIN_YEAR <= int(m.group(1)) <= MAX_YEAR:
        raise SourceError(f"{where}: {text!r} is not a financial year like 2025-26")
    return int(m.group(1))


def fy_name(start_year: int) -> str:
    """``2025`` -> ``2025-26``."""
    return f"{start_year}-{(start_year + 1) % 100:02d}"


def fy_period(start_year: int) -> tuple[str, str, str, str]:
    """``(period, period_type, start, end)`` of the financial year from 1 April ``start_year``."""
    return (fy_name(start_year), "fy", dt.date(start_year, 4, 1).isoformat(),
            dt.date(start_year + 1, 3, 31).isoformat())


def fy_quarter_months(start_year: int, quarter: int) -> tuple[dt.date, dt.date]:
    """First and last day of quarter ``quarter`` (1 to 4) of the financial year from April ``start_year``."""
    first_month = 4 + 3 * (quarter - 1)
    year = start_year + (first_month - 1) // 12
    month = (first_month - 1) % 12 + 1
    last_month = month + 2
    return dt.date(year, month, 1), dt.date(year, last_month, _calendar.monthrange(year, last_month)[1])


def fy_quarter_period(start_year: int, quarter: int) -> tuple[str, str, str, str]:
    """``(period, period_type, start, end)`` of a financial-year quarter: ``2026-27Q1`` is April to June 2026."""
    start, end = fy_quarter_months(start_year, quarter)
    return f"{fy_name(start_year)}Q{quarter}", "fy_quarter", start.isoformat(), end.isoformat()


def period_bounds(period: str, period_type: str | None = None) -> tuple[str, dt.date, dt.date]:
    """``(period_type, start, end)`` of a period written in any of the four formats; ``ValueError`` otherwise.

    Financial years 2000-01 to 2011-12 are written like months (``2011-12``
    is also December 2011): without ``period_type`` such a text reads as a
    month. Pass ``period_type`` (``fy`` for an annual national-accounts
    period, say) to read the text as that type only.
    """
    text = str(period).strip()
    if period_type is not None and period_type not in PERIOD_TYPES:
        raise ValueError(f"period_type must be one of {', '.join(PERIOD_TYPES)}, not {period_type!r}")
    want = (lambda kind: period_type in (None, kind))
    if want("day") and _DAY.fullmatch(text):
        day = dt.date.fromisoformat(text)
        return "day", day, day
    m = _FY_QUARTER.fullmatch(text)
    if want("fy_quarter") and m:
        start_year = fy_start_year(f"{m.group(1)}-{m.group(2)}", f"period {text!r}")
        start, end = fy_quarter_months(start_year, int(m.group(3)))
        return "fy_quarter", start, end
    m = _MONTH.fullmatch(text)
    if want("month") and m and 1 <= int(m.group(2)) <= 12:
        year, month = int(m.group(1)), int(m.group(2))
        return "month", dt.date(year, month, 1), dt.date(year, month, _calendar.monthrange(year, month)[1])
    if want("fy") and _FY.fullmatch(text):
        start_year = fy_start_year(text, f"period {text!r}")
        return "fy", dt.date(start_year, 4, 1), dt.date(start_year + 1, 3, 31)
    if period_type is not None:
        raise ValueError(f"period {period!r} is not a {period_type} period")
    raise ValueError(f"period {period!r} is not a day (2026-11-17), a month (2026-10), a financial-year quarter "
                     "(2026-27Q1) or a financial year (2025-26)")


def add_months(year: int, month: int, n: int) -> tuple[int, int]:
    """``(year, month)`` moved by ``n`` months."""
    index = year * 12 + (month - 1) + n
    return index // 12, index % 12 + 1


def next_weekday(day: dt.date) -> dt.date:
    """``day`` if it is Monday to Friday, else the Monday after."""
    while day.weekday() >= 5:
        day += dt.timedelta(days=1)
    return day


def previous_weekday(day: dt.date) -> dt.date:
    """``day`` if it is Monday to Friday, else the Friday before."""
    while day.weekday() >= 5:
        day -= dt.timedelta(days=1)
    return day


def last_weekday_of_month(year: int, month: int) -> dt.date:
    return previous_weekday(dt.date(year, month, _calendar.monthrange(year, month)[1]))


# --- Times from which values were public ------------------------------------------------------------

BASES = ("release_table", "release_table_planned", "release_rule", "publication_rule", "first_seen")


@dataclass(frozen=True)
class PublicFrom:
    """From when a value was public: India time without a zone, how that time was found, and the release.

    ``basis``: ``release_table`` (an actual release date in the shipped or
    the user's release-date table), ``release_table_planned`` (a planned date
    there), ``release_rule`` (a publisher's release rule), ``publication_rule``
    (a conservative rule for a US or BIS publication), ``first_seen`` (when a
    store first read the value). ``time`` is ``None`` when no rule applies
    (the latest row of a New York Fed series: its next business day is not
    known yet); the store then uses its first sighting.
    """

    time: dt.datetime | None
    basis: str
    rule: str = ""
    release_id: str = ""
    notes: str = ""

    @property
    def text(self) -> str:
        """``time`` as the CSVs write it (``2026-11-12T16:00:00``), or ``""``."""
        return "" if self.time is None else self.time.strftime("%Y-%m-%dT%H:%M:%S")
