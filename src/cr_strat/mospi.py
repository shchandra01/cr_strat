"""MoSPI's eSankhyiki API (CPI, IIP, national accounts, WPI), its release calendar, and when each value was public.

Build-order item 5 of ``docs/rates_data_sources.md`` (S7, the policy path
against OIS, and the back-tests of every strategy). This module builds the
requests, parses the answers into ``macro_base.Observation`` rows and dates
every value; fetching politely is ``macro_sources.fetch``, keeping a history
the store's job.

**Source.** ``https://api.mospi.gov.in/api/...`` (no key), as sampled on 18
Nov 2026:

==================  ====================================================  ========  ==============================
Data                Endpoint                                              Page max  Layout (``ParseResult.layout``)
==================  ====================================================  ========  ==============================
CPI base 2024       ``/api/cpi/getCPIData`` (Current Jan 2025-, Back         100      ``cpi_2024``
                    2013-2024, general index only)
CPI bases 2012,     ``/api/cpi/getCPIIndex`` (groups and subgroups)          200      ``cpi_group``
2010                ``/api/cpi/getItemIndex`` (items, base 2012)             200      ``cpi_item``
IIP                 ``/api/iip/getIIPMonthly`` (bases 2022-23, 2011-12)      200      ``iip``
National accounts   ``/api/nas/getNASData`` (GDP: indicator 5; GVA: 1)       200      ``nas``, ``nas_gva``
WPI                 ``/api/wpi/getWpiRecords`` (bases 2022-23, 2011-12)      200      ``wpi_2022_23``, ``wpi_2011_12``
"RBI" tables        ``/api/rbi/getRbiRecords`` (indicator 33: rupee rates)    20      ``rbi_records``
Release calendar    ``https://www.mospi.gov.in/api/documents/                 -       (a pointer to a PDF)
                    get-latest-release-calender``
==================  ====================================================  ========  ==============================

**Terms.** No licence, terms of use or rate limit was found for the API (not
in its headers or bodies, nor in MoSPI's official client, ``llms.txt``,
``ard.json`` or ``ai-catalog.json``); the website's legacy copyright page
reportedly asks for permission by mail before reproduction. Whether a
commercial research history may be kept is the user's call once MoSPI
answers letter 12 of ``docs/data_enquiries.md``. ``llms.txt``
(``https://esankhyiki.mospi.gov.in/llms.txt``) asks AI systems that use the
data to keep the dataset name, indicator, value, unit, reference period,
source and publication or update information, and to attribute and link the
source: every ``Observation`` carries them (``macro_base``), the series
catalogue (``macro_sources``) the update information and ``CITATION``. It
also asks AI systems to prefer MoSPI's MCP server; this package does not use
it. MoSPI's official Python client defaults to a private test address and
switches certificate checks off; nothing here copies that
(``storekit.legacy_renegotiation_context`` keeps the checks, should the
server need legacy TLS renegotiation as the client says).

**Quirks the parsers handle.**

* Every value is a string (``"110.05"``); years are strings in base-2024 CPI
  rows and numbers elsewhere; months are English names. Values are kept as
  printed (``"61.20"``: the back series pads one decimal to two; WPI's
  ``"167"`` drops it).
* "No data" is HTTP 200 with ``{"msg": "No Data Found"}`` (an empty result
  with a note, not an error); ``msg`` is no guide otherwise. A page limit
  exceeded is HTTP 400 in two shapes (``{"success": false, "message": ...}``,
  ``{"error": ...}``): ``SourceError`` (a programming error, never a block).
* ``meta_data`` must add up: ``totalPages`` is ``ceil(totalRecords /
  recordPerPage)`` and the page holds what is left for it, else refused.
  ``combine_pages`` joins the pages of one query and checks they are all
  there.
* Base-2024 CPI: 668 nodes per area, sector and month (headline, 12
  divisions coded 01-11 and 13, 43 groups, 92 classes, 162 sub-classes, 358
  items); ``level`` does not restrict the answer; ``division_code=0`` gives
  the headline. The food index the press release calls CFPI is group 01.1
  "Food" (``..._g01_1``; alias ``..._food``). No weights and no status are
  published, so a provisional print is overwritten in place a month later.
  Item-level and sub-class codes were not sampled: they are read when their
  names and codes are present, by code depth.
* Bases 2012 and 2010 (``getCPIIndex``): names only, no codes; a group's
  total is the subgroup ``"<group>-Overall"``; the headline is group
  ``General``. Base 2012's subgroups take MoSPI's filter codes
  (``..._s1_1_01``); base 2010's, whose codes were not sampled, a name slug
  (``..._s_household_requisites``). The CFPI is group 7, "Consumer Food
  Price". Rural housing has no index (``null``: no row). Rows print ``status
  F``. Rows do not say the series (Current or Back): the caller does
  (default Current).
* IIP: a category's total has an empty ``sub_category``. On base 2022-23 the
  "growth rate" of April 2023 to March 2024 is the index minus 100 (no
  year-ago value exists on that base): dropped, with a note.
* National accounts: ``revision`` is ``null`` on every quarterly row; annual
  rows print the estimate type of the latest vintage only (kept as
  ``status``). The unit must be ``₹ Crore``. Indicators other than GDP (5)
  and GVA (1), and sub-industry or institutional-sector rows, were not
  sampled and are refused.
* WPI: field names differ by base (``major_group, sub_group, sub_sub_group``
  on 2022-23; ``majorgroup, subgroup, sub_subgroup`` on 2011-12); no
  inflation and no status is published; an item may have no index
  (``null``). Other bases' layouts were not sampled: refused.
* The "RBI" tables republish RBI's data, 15 months stale (to August 2025): a
  parser exists for indicator 33 (rupee rates) for checks only; the yen rate
  is printed with the unit "per unit of foreign currency" but is per 100 yen.

**Release calendar.** MoSPI's advance release calendar is a PDF this package
cannot read; its pointer (``get-latest-release-calender``, JSON) names the
current file, so a store can log when a new version appears. The dates are
shipped instead as ``src/cr_strat/data/macro_release_dates.csv`` (one row per
release with its ``source``: the 2025-26 calendar's planned dates and the
2026-27 calendar's actual and planned ones); a user-kept file of the same
columns overrides or extends it (``ReleaseCalendar.load``). Before April
2025, and for WPI (released by DPIIT's Office of the Economic Adviser, not
in MoSPI's calendar), the release rules below apply.

**When a value was public** (``ReleaseCalendar``; India time without a zone).
Release days follow MoSPI's working days, not Mumbai's settlement calendar
(CPI came out on 14 Sep 2026, a Mumbai holiday), and no list of them is
shipped: a rule date on a Saturday or Sunday moves as the rule says, a
weekday holiday is not known (``basis`` says ``release_rule``).

* CPI: the 12th of the next month (next working day if a holiday); final a
  month later, with the next month's first print. 16:00 from 12 Nov 2024,
  17:30 from July 2013, 11:30 before (news reports, not MoSPI's pages; the
  day rule before 2013 not checked).
* IIP: from April 2025 (March 2025 data) the 28th of the next month at 16:00,
  next working day if a holiday, quick estimate then final a month later;
  the release of 28 Apr 2025 also carried the finals of Dec 2024 to Feb 2025
  (MoSPI's note of 17 Apr 2025). Before: the 12th of the second month after
  the reference month, previous working day if a holiday, with a first
  revision a month later and the final with the quick estimate three months
  later; 16:00 from 12 Nov 2024, 17:30 from July 2013, 11:30 before.
* WPI: the 14th of the next month at 12:00 (next working day if a holiday),
  final with the release two months later (about ten weeks after the
  reference month).
* GDP: quarter estimates on the last working day of the second month after
  the quarter (August, November, February, May), first advance estimates on
  7 January; 16:00 from 29 Nov 2024, 17:30 before. A quarter's value is
  revised at later releases until its financial year's final estimates: 23
  months after the year ends from FY2023-24 (27 Feb 2026), 35 months (the
  third revised estimates, which MoSPI's note of 17 Apr 2025 says are no
  longer needed) for earlier years: the later date where the policy is not
  certain.
* A base's values are never public before the base was launched
  (``BASE_LAUNCHES``): CPI 2024 (Current and Back) 12 Feb 2026 16:00,
  national accounts 2022-23 27 Feb 2026 16:00, IIP 2022-23 1 Jun 2026
  16:00, WPI 2022-23 15 Jun 2026 12:00 (news reports). The older bases'
  launches are from memory, not checked, and moved to the end of the month
  believed: late rather than early, if that month is right.

``first_print``, ``final_print`` and the revision ladder in between give the
release of each stage of a value; ``backfill_public_from`` dates a value read
later (the API serves the latest vintage only, so the stage it is at when
read); ``live_public_from`` dates a value seen while polling (the release
time if it was seen within six hours after it on the release day, else the
first sighting). Every time is a ``macro_base.PublicFrom`` with its basis.

Nothing here reads the clock: ``seen_at`` and ``now`` are arguments.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from cr_strat.calendars import BusinessCalendar, mumbai_settlement_calendar
from cr_strat.macro_base import (
    Observation,
    ObservationSet,
    ParseResult,
    PublicFrom,
    Request,
    SourceError,
    add_months,
    build_url,
    fy_name,
    fy_period,
    fy_quarter_period,
    fy_start_year,
    last_weekday_of_month,
    load_json,
    month_number,
    month_period,
    number_text,
    period_bounds,
    previous_weekday,
    slug,
    text_of,
    year_of,
)
from cr_strat.ustime import IST, UTC, to_ist

API = "https://api.mospi.gov.in"
WEBSITE = "https://www.mospi.gov.in"
SOURCE = "mospi"
CALENDAR_SOURCE = "mospi_calendar"
PUBLISHER = "Ministry of Statistics and Programme Implementation (MoSPI), Government of India"
CITATION = ("Source: Ministry of Statistics and Programme Implementation (MoSPI), Government of India, "
            "eSankhyiki ({dataset}), {url}, retrieved {date}")
TERMS = ("No licence or terms of use found for the eSankhyiki API (18 Nov 2026); llms.txt asks to keep the dataset "
         "name, indicator, value, unit, reference period, source and update information. Ask MoSPI before keeping "
         "a history (docs/data_enquiries.md, letter 12).")
TERMS_URL = "https://esankhyiki.mospi.gov.in/llms.txt"
WPI_PUBLISHER = ("Office of the Economic Adviser, Department for Promotion of Industry and Internal Trade (DPIIT), "
                 "Government of India, via MoSPI's eSankhyiki")
WPI_CITATION = ("Source: Office of the Economic Adviser, DPIIT, Government of India, via MoSPI eSankhyiki "
                "({dataset}), {url}, retrieved {date}")
UPDATE_INFO = {  # what each family's release rule is (the module docstring; release_dates and the rules date values)
    "cpi": "monthly: the 12th of the next month (the next working day if a holiday), 16:00 India time since 12 Nov "
           "2024 (17:30 from July 2013, 11:30 before); provisional, then final with the next month's release; the API "
           "serves the latest value only",
    "iip": "monthly: from March 2025 data the 28th of the next month (next working day), 16:00 India time, a quick "
           "estimate, final a month later; before, the 12th of the second month after (previous working day), "
           "revised a month later and final two months after that; the API serves the latest value only",
    "gdp": "quarterly: the last working day of the second month after the quarter, 16:00 India time since 29 Nov "
           "2024 (17:30 before); first advance estimates on 7 January; revised at later releases until the year's "
           "final estimates; the API serves the latest value only",
    "wpi": "monthly: compiled by DPIIT's Office of the Economic Adviser, the 14th of the next month (next working "
           "day), 12:00 India time; provisional, final with the release two months later; the API serves the latest "
           "value only",
}

CPI_2024, CPI_GROUP, CPI_ITEM = "mospi_cpi_2024", "mospi_cpi_group", "mospi_cpi_item"
IIP, NAS, WPI, RBI = "mospi_iip", "mospi_nas", "mospi_wpi", "mospi_rbi"
CALENDAR_POINTER = "mospi_calendar_pointer"
KINDS = (CPI_2024, CPI_GROUP, CPI_ITEM, IIP, NAS, WPI, RBI, CALENDAR_POINTER)
ANY_KIND = "mospi"  # a saved answer whose layout cannot be told (no rows): parsed without a layout check
PAGE_LIMITS = {CPI_2024: (10, 100), CPI_GROUP: (1, 200), CPI_ITEM: (1, 200), IIP: (1, 200), NAS: (1, 200),
               WPI: (1, 200), RBI: (1, 20)}  # getCPIData refuses fewer than 10 and more than 100
_ENDPOINTS = {CPI_2024: "/api/cpi/getCPIData", CPI_GROUP: "/api/cpi/getCPIIndex", CPI_ITEM: "/api/cpi/getItemIndex",
              IIP: "/api/iip/getIIPMonthly", NAS: "/api/nas/getNASData", WPI: "/api/wpi/getWpiRecords",
              RBI: "/api/rbi/getRbiRecords"}
JSON_ACCEPT = "application/json"

UNIT_PERCENT_YOY = "percent, year on year"
UNIT_CRORE = "rupees crore"

LAYOUTS: dict[str, frozenset[str]] = {
    "cpi_2024": frozenset({"base_year", "series", "year", "month", "state", "sector", "division", "group", "class",
                           "sub_class", "item", "code", "index", "inflation", "imputation"}),
    "cpi_group": frozenset({"baseyear", "year", "month", "state", "sector", "group", "subgroup", "index", "inflation",
                            "status"}),
    "cpi_item": frozenset({"baseyear", "year", "month", "item", "index", "inflation", "status"}),
    "iip": frozenset({"base_year", "year", "month", "type", "category", "sub_category", "index", "growth_rate"}),
    "nas": frozenset({"base_year", "series", "year", "indicator", "frequency", "revision", "quarter",
                      "current_price", "constant_price", "unit"}),
    "wpi_2022_23": frozenset({"base_year", "year", "month", "major_group", "group", "sub_group", "sub_sub_group",
                              "item", "index_value"}),
    "wpi_2011_12": frozenset({"base_year", "year", "month", "majorgroup", "group", "subgroup", "sub_subgroup", "item",
                              "index_value"}),
    "rbi_records": frozenset({"indicator", "year", "month", "currency", "reference_rate", "value", "unit"}),
}
LAYOUTS["nas_gva"] = LAYOUTS["nas"] | {"industry", "subindustry", "institutional_sector"}
KIND_LAYOUTS = {CPI_2024: {"cpi_2024"}, CPI_GROUP: {"cpi_group"}, CPI_ITEM: {"cpi_item"}, IIP: {"iip"},
                 NAS: {"nas", "nas_gva"}, WPI: {"wpi_2022_23", "wpi_2011_12"}, RBI: {"rbi_records"}}
LAYOUT_KIND = {layout: kind for kind, layouts in KIND_LAYOUTS.items() for layout in layouts}


# --- Requests -------------------------------------------------------------------------------------------

def _check_choice(value, allowed: Sequence, what: str):
    if value not in allowed:
        raise ValueError(f"{what} must be one of {', '.join(map(repr, allowed))}, not {value!r}")
    return value


def _check_int(value, what: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{what} must be a whole number from {low} to {high}, not {value!r}")
    return value


def _years(year, what: str = "year") -> str | None:
    """``year`` as MoSPI takes it: one year or several joined by commas; ``None`` for all years."""
    if year is None:
        return None
    values = [year] if isinstance(year, (int, str)) else list(year)
    if not values:
        raise ValueError(f"{what} must name at least one year, or be None for all years")
    out = []
    for value in values:
        if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}", value):  # a financial year, for NAS
            fy_start_year(value, what)
            out.append(value)
        else:
            out.append(str(_check_int(value, what, 1950, 2100)))
    return ",".join(out)


def _month(month) -> int | None:
    return None if month is None else _check_int(month, "month", 1, 12)


def _page(kind: str, page: int, limit: int) -> tuple[int, int]:
    low, high = PAGE_LIMITS[kind]
    return _check_int(page, "page", 1, 10_000_000), _check_int(limit, f"limit for {kind}", low, high)


def _request(kind: str, params: list[tuple[str, object]], note: str = "") -> Request:
    url, query = build_url(API + _ENDPOINTS[kind], params)
    return Request(SOURCE, kind, url, JSON_ACCEPT, query, note)


def cpi_2024_request(*, series: str = "Current", year=None, month: int | None = None, state_code: int = 1,
                     sector_code: int | None = None, division_code: int | None = None, page: int = 1,
                     limit: int = 100) -> Request:
    """``getCPIData``: base-2024 CPI. ``state_code`` 1 is All India; ``sector_code`` 1 rural, 2 urban, 3 combined,
    ``None`` all three; ``division_code`` 0 the headline only. ``limit`` 10 to 100."""
    _check_choice(series, ("Current", "Back"), "series")
    page, limit = _page(CPI_2024, page, limit)
    params = [("base_year", 2024), ("series", series), ("year", _years(year)), ("month_code", _month(month)),
              ("state_code", _check_int(state_code, "state_code", 1, 99)),
              ("sector_code", None if sector_code is None else _check_int(sector_code, "sector_code", 1, 3)),
              ("division_code", None if division_code is None else _check_int(division_code, "division_code", 0, 13)),
              ("limit", limit), ("page", page), ("Format", "JSON")]
    return _request(CPI_2024, params)


def cpi_index_request(*, base_year: int = 2012, series: str = "Current", year=None, month: int | None = None,
                      state_code: int = 99, sector_code: int | None = None, group_code: str | None = None,
                      subgroup_code: str | None = None, page: int = 1, limit: int = 200) -> Request:
    """``getCPIIndex``: bases 2012 and 2010, groups and subgroups. ``state_code`` 99 is All India."""
    _check_choice(base_year, (2012, 2010), "base_year")
    _check_choice(series, ("Current", "Back"), "series")
    page, limit = _page(CPI_GROUP, page, limit)
    params = [("base_year", base_year), ("series", series), ("year", _years(year)), ("month_code", _month(month)),
              ("state_code", _check_int(state_code, "state_code", 1, 99)),
              ("sector_code", None if sector_code is None else _check_int(sector_code, "sector_code", 1, 3)),
              ("group_code", group_code), ("subgroup_code", subgroup_code), ("limit", limit), ("page", page),
              ("Format", "JSON")]
    return _request(CPI_GROUP, params, note=series)


def cpi_item_request(*, base_year: int = 2012, series: str = "Current", year=None, month: int | None = None,
                     item_code: str | None = None, page: int = 1, limit: int = 200) -> Request:
    """``getItemIndex``: base-2012 item indices (All India)."""
    _check_choice(base_year, (2012, 2010), "base_year")
    _check_choice(series, ("Current", "Back"), "series")
    page, limit = _page(CPI_ITEM, page, limit)
    params = [("base_year", base_year), ("series", series), ("year", _years(year)), ("month_code", _month(month)),
              ("item_code", item_code), ("limit", limit), ("page", page), ("Format", "JSON")]
    return _request(CPI_ITEM, params, note=series)


IIP_TYPES = ("All", "General", "Sectoral", "Use-based category")


def iip_request(*, base_year: str = "2022-23", type: str = "All", year=None, month: int | None = None,
                page: int = 1, limit: int = 200) -> Request:
    """``getIIPMonthly``: ``type`` All, General, Sectoral or Use-based category."""
    _check_choice(base_year, ("2022-23", "2011-12"), "base_year")
    _check_choice(type, IIP_TYPES, "type")
    page, limit = _page(IIP, page, limit)
    params = [("base_year", base_year), ("type", type), ("year", _years(year)), ("month_code", _month(month)),
              ("limit", limit), ("page", page), ("Format", "JSON")]
    return _request(IIP, params)


NAS_INDICATORS = {5: "Gross Domestic Product", 1: "Gross Value Added"}  # the two sampled


def nas_request(*, base_year: str = "2022-23", series: str = "Current", frequency: str = "Quarterly",
                indicator_code: int = 5, year=None, quarter: int | None = None, page: int = 1,
                limit: int = 200) -> Request:
    """``getNASData``: GDP (indicator 5) or GVA (1); ``year`` a financial year (``2025-26``) or several."""
    _check_choice(base_year, ("2022-23", "2011-12"), "base_year")
    _check_choice(series, ("Current", "Back"), "series")
    _check_choice(frequency, ("Quarterly", "Annually"), "frequency")
    _check_choice(indicator_code, tuple(NAS_INDICATORS), "indicator_code (only GDP and GVA were sampled)")
    if quarter is not None and frequency != "Quarterly":
        raise ValueError("quarter applies to quarterly data only")
    page, limit = _page(NAS, page, limit)
    params = [("base_year", base_year), ("series", series), ("frequency_code", frequency),
              ("indicator_code", indicator_code), ("year", _years(year)),
              ("quarterly_code", None if quarter is None else _check_int(quarter, "quarter", 1, 4)),
              ("limit", limit), ("page", page), ("Format", "JSON")]
    return _request(NAS, params)


def wpi_request(*, base_year: str = "2022-23", year=None, month: int | None = None,
                major_group_code: str | None = None, page: int = 1, limit: int = 200) -> Request:
    """``getWpiRecords``; ``major_group_code`` 1000000000 gives the headline only (the only filter sampled)."""
    _check_choice(base_year, ("2022-23", "2011-12"), "base_year")
    if major_group_code is not None and not re.fullmatch(r"\d{10}", str(major_group_code)):
        raise ValueError(f"major_group_code must be ten digits (1000000000 is the headline), not {major_group_code!r}")
    page, limit = _page(WPI, page, limit)
    params = [("base_year", base_year), ("year", _years(year)), ("month_code", _month(month)),
              ("major_group_code", major_group_code), ("limit", limit), ("page", page), ("Format", "JSON")]
    return _request(WPI, params)


def rbi_records_request(*, sub_indicator_code: int = 33, page: int = 1, limit: int = 20) -> Request:
    """``getRbiRecords`` (indicator 33: the rupee's monthly reference rates). Not stored by default (stale)."""
    page, limit = _page(RBI, page, limit)
    params = [("sub_indicator_code", _check_int(sub_indicator_code, "sub_indicator_code", 1, 1000)),
              ("limit", limit), ("page", page), ("Format", "JSON")]
    return _request(RBI, params)


def release_calendar_pointer_request() -> Request:
    """The pointer to the current advance release calendar (JSON naming the PDF)."""
    return Request(CALENDAR_SOURCE, CALENDAR_POINTER, f"{WEBSITE}/api/documents/get-latest-release-calender",
                   JSON_ACCEPT)


def page_request(request: Request, page: int) -> Request:
    """The same query as ``request`` for another page (``page`` replaced, every other parameter kept in order)."""
    if request.kind not in _ENDPOINTS:
        raise ValueError(f"{request.kind} has no pages")
    _check_int(page, "page", 1, 10_000_000)
    params = [(k, str(page) if k == "page" else v) for k, v in request.query]
    if not any(k == "page" for k, _ in request.query):
        raise ValueError(f"{request.url}: no page parameter to replace")
    return _request(request.kind, params, note=request.note)


def more_pages(request: Request, result: ParseResult) -> list[Request]:
    """The requests for pages 2 to N of a query whose page 1 ``result`` read (none for a later page)."""
    if result.meta.get("page") != "1":
        return []
    pages = int(result.meta.get("total_pages") or 0)
    return [page_request(request, n) for n in range(2, pages + 1)]


# --- The envelope -----------------------------------------------------------------------------------

def _meta(doc: dict, name: str, rows: int) -> dict[str, str]:
    meta = doc.get("meta_data")
    if not isinstance(meta, dict):
        raise SourceError(f"{name}: data without meta_data (page, totalRecords, totalPages, recordPerPage)")
    values = {}
    for key in ("page", "totalRecords", "totalPages", "recordPerPage"):
        text = str(meta.get(key, ""))
        if not text.isdigit():
            raise SourceError(f"{name}: meta_data {key} is {meta.get(key)!r}, not a whole number")
        values[key] = int(text)
    page, total, pages, per = values["page"], values["totalRecords"], values["totalPages"], values["recordPerPage"]
    if page < 1 or per < 1:
        raise SourceError(f"{name}: meta_data page {page} and recordPerPage {per} must be at least 1")
    expected = math.ceil(total / per)
    if pages != expected and not (total == 0 and pages in (0, 1)):
        raise SourceError(f"{name}: the page count does not add up: {total} records at {per} a page make {expected} "
                          f"pages, not {pages}")
    holds = max(0, min(per, total - (page - 1) * per))
    if rows != holds:
        raise SourceError(f"{name}: page {page} of {total} records at {per} a page should hold {holds} rows, but it "
                          f"holds {rows}")
    return {"page": str(page), "total_pages": str(pages), "total_records": str(total), "records_per_page": str(per),
            "rows": str(rows)}


def parse_mospi(body: bytes, name: str, *, kind: str | None = None, series: str | None = None) -> ParseResult:
    """Observations from one eSankhyiki answer (saved or fetched), refused as a whole if anything does not fit.

    ``kind`` (the request's, e.g. ``CPI_2024``) is checked against the layout
    the rows have; without it the layout is recognised from the rows' fields.
    ``series`` (Current or Back) labels base-2012 and 2010 CPI rows, which do
    not say it themselves (default Current). An error answer (a page limit
    exceeded) is a ``SourceError``; ``No Data Found`` an empty result.
    """
    if kind is not None and kind not in KIND_LAYOUTS:
        raise ValueError(f"kind must be one of {sorted(KIND_LAYOUTS)}, not {kind!r}")
    doc = load_json(body, name)
    if not isinstance(doc, dict):
        raise SourceError(f"{name}: not an eSankhyiki answer (a JSON object was expected)")
    if "error" in doc or doc.get("success") is False or doc.get("statusCode") is False:
        message = doc.get("error") or doc.get("message") or doc.get("msg") or "no message"
        raise SourceError(f"{name}: an error answer, not data: {message!r} (a page limit? getCPIData takes 10 to "
                          "100 rows a page, the older endpoints 200)")
    data = doc.get("data")
    if data is None or data == []:
        if "data" not in doc and str(doc.get("msg", "")).strip().lower() != "no data found":
            raise SourceError(f"{name}: no data and no 'No Data Found' message: {str(doc)[:120]!r}")
        return ParseResult(SOURCE, name, "", [], [f"no data: {doc.get('msg') or 'empty'}"], {"rows": "0"})
    if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
        raise SourceError(f"{name}: data is not a list of rows (a filter list? this parser reads data endpoints)")
    keys = frozenset(data[0])
    layout = next((k for k, v in LAYOUTS.items() if v == keys), None)
    if layout is None:
        raise SourceError(f"{name}: a layout no sample was seen of (fields {sorted(keys)}); refused rather than "
                          "guessed")
    for i, row in enumerate(data, start=1):
        if frozenset(row) != keys:
            raise SourceError(f"{name}, data row {i}: fields {sorted(row)} differ from the first row's {sorted(keys)}")
    if kind is not None and layout not in KIND_LAYOUTS[kind]:
        raise SourceError(f"{name}: a {kind} request answered with {layout} rows")
    meta = _meta(doc, name, len(data))
    out = ObservationSet(name)
    notes: list[str] = []
    _ROW_PARSERS[layout](data, name, out, notes, series or "Current")
    notes += out.notes()
    return ParseResult(SOURCE, name, layout, out.observations(), notes, meta)


def _field(row: dict, key: str, where: str, *, required: bool = True) -> str:
    value = row.get(key)
    if value is None:
        if required:
            raise SourceError(f"{where}: {key} is missing (null)")
        return ""
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    if required and not value:
        raise SourceError(f"{where}: {key} is empty")
    return value


def _value(row: dict, key: str, where: str) -> str | None:
    """A value field: its text, or ``None`` for MoSPI's documented missing value (``null``)."""
    value = row.get(key)
    if value is None:
        return None
    return number_text(value, f"{where}, {key}")


_SECTORS = {"rural": "rural", "urban": "urban", "combined": "combined"}


def _sector(value, where: str) -> str:
    sector = _SECTORS.get(str(value).strip().lower()) if isinstance(value, str) else None
    if sector is None:
        raise SourceError(f"{where}: sector {value!r} is not Rural, Urban or Combined")
    return sector


def _obs(series_id: str, period: tuple[str, str, str, str], value: str, unit: str, **extra) -> Observation:
    return Observation(SOURCE, series_id, period[0], period[1], period[2], period[3], value, unit, **extra)


# --- CPI ------------------------------------------------------------------------------------------------

_CODE = re.compile(r"\d{2}(?:\.\d+)*")
_CPI_2024_LEVELS = (("group", "g", 2), ("class", "c", 3), ("sub_class", "sc", None), ("item", "i", None))


def _cpi_2024_node(row: dict, where: str) -> tuple[str, str, str, str]:
    """``(node id, level, code, label)`` of a base-2024 CPI row."""
    division = _field(row, "division", where)
    names = [(level, prefix, depth, row.get(level)) for level, prefix, depth in _CPI_2024_LEVELS]
    present = [(level, prefix, depth, name) for level, prefix, depth, name in names if name not in (None, "")]
    code = row.get("code")
    if code in (None, ""):
        if division != "CPI (General)" or present:
            raise SourceError(f"{where}: a node without a code ({division!r}): not sampled, refused")
        return "general", "general", "", "CPI (General)"
    code = str(code).strip()
    if not _CODE.fullmatch(code):
        raise SourceError(f"{where}: code {code!r} is not like 01, 01.1 or 01.1.1")
    parts = code.count(".") + 1
    if not present:
        level, prefix, depth, label = "division", "d", 1, division
    else:
        level, prefix, depth, label = present[-1]
        if level == "class" and row.get("group") in (None, ""):
            raise SourceError(f"{where}: a class without its group")
    if depth is not None and parts != depth:
        raise SourceError(f"{where}: a {level} with the code {code!r}; a {level} code has {depth} part(s)")
    if depth is None and parts < 3:
        raise SourceError(f"{where}: a {level} with the short code {code!r} (sub-class and item codes were not "
                          "sampled, but cannot be shorter than a class's)")
    return f"{prefix}{code.replace('.', '_')}", level, code, str(label).strip()


def _cpi_2024(rows: list[dict], name: str, out: ObservationSet, notes: list[str], _series: str) -> None:
    for i, row in enumerate(rows, start=1):
        where = f"{name}, data row {i}"
        if str(row.get("base_year")).strip() != "2024":
            raise SourceError(f"{where}: base_year {row.get('base_year')!r} in a base-2024 layout")
        series = _field(row, "series", where)
        if series not in ("Current", "Back"):
            raise SourceError(f"{where}: series {series!r} is not Current or Back")
        period = month_period(year_of(row.get("year"), where), month_number(row.get("month"), where))
        area = _field(row, "state", where)
        sector = _sector(row.get("sector"), where)
        node, level, code, label = _cpi_2024_node(row, where)
        imputation = row.get("imputation")
        if imputation not in (None, "", "Y", "N"):
            raise SourceError(f"{where}: imputation {imputation!r} is not Y or N")
        sid = f"cpi_2024{'back' if series == 'Back' else ''}_{slug(area)}_{sector}_{node}"
        shown = f"{label} ({code})" if code else label
        dataset = f"eSankhyiki CPI, base 2024, {series} series"
        common = dict(dataset=dataset, base="2024", series_type=series, area=area, sector=sector, level=level,
                      code=code, label=label, imputation=imputation or "")
        index, inflation = _value(row, "index", where), _value(row, "inflation", where)
        if index is not None:
            out.add(_obs(sid, period, index, "index, 2024=100", indicator=f"Consumer Price Index, {area}, "
                         f"{sector.title()}, {shown}", **common), where)
        if inflation is not None:
            out.add(_obs(f"{sid}_yoy", period, inflation, UNIT_PERCENT_YOY, indicator=f"Consumer Price Index "
                         f"inflation, {area}, {sector.title()}, {shown}", **common), where)


CPI_GROUPS = {"general": "general", "food and beverages": "food_and_beverages",
              "pan, tobacco and intoxicants": "pan_tobacco", "pan; tobacco and intoxicants": "pan_tobacco",
              "clothing and footwear": "clothing_footwear",
              "clothing; bedding and footwear": "clothing_bedding_footwear",
              "housing": "housing", "fuel and light": "fuel_and_light", "miscellaneous": "miscellaneous",
              "consumer food price": "cfpi"}
# Base 2012's subgroup codes, from MoSPI's filter list (getCpiFilterByLevelAndBaseYear, 18 Nov 2026): name -> code
CPI_2012_SUBGROUPS = {
    "Cereals and Products": "1.1.01", "Meat and Fish": "1.1.02", "Egg": "1.1.03", "Milk and Products": "1.1.04",
    "Oils and Fats": "1.1.05", "Fruits": "1.1.06", "Vegetables": "1.1.07", "Pulses and Products": "1.1.08",
    "Sugar and Confectionery": "1.1.09", "Spices": "1.1.10", "Prepared Meals, Snacks, Sweets etc.": "1.1.12",
    "Non-alcoholic Beverages": "1.2.11", "Clothing": "3.1.01", "Footwear": "3.1.02",
    "Household Goods and Services": "6.1.01", "Health": "6.1.02", "Transport and Communication": "6.1.03",
    "Recreation and Amusement": "6.1.04", "Education": "6.1.05", "Personal Care and Effects": "6.1.06",
}


def _cpi_group(rows: list[dict], name: str, out: ObservationSet, notes: list[str], series: str) -> None:
    for i, row in enumerate(rows, start=1):
        where = f"{name}, data row {i}"
        base = _field(row, "baseyear", where)
        if base not in ("2012", "2010"):
            raise SourceError(f"{where}: baseyear {base!r} is not 2012 or 2010")
        period = month_period(year_of(row.get("year"), where), month_number(row.get("month"), where))
        area = _field(row, "state", where)
        sector = _sector(row.get("sector"), where)
        group, subgroup = _field(row, "group", where), _field(row, "subgroup", where)
        status = _field(row, "status", where, required=False)
        if subgroup.lower() == f"{group.lower()}-overall":
            node = CPI_GROUPS.get(group.lower()) or slug(group)
            level, code, label = ("general" if node == "general" else "group"), "", group
        else:
            code = CPI_2012_SUBGROUPS.get(subgroup, "") if base == "2012" else ""
            node = f"s{code.replace('.', '_')}" if code else f"s_{slug(subgroup)}"
            level, label = "subgroup", subgroup
        sid = f"cpi_{base}{'back' if series == 'Back' else ''}_{slug(area)}_{sector}_{node}"
        common = dict(dataset=f"eSankhyiki CPI, base {base}, {series} series", base=base, series_type=series,
                      area=area, sector=sector, level=level, code=code, label=label, status=status)
        index, inflation = _value(row, "index", where), _value(row, "inflation", where)
        if index is not None:
            out.add(_obs(sid, period, index, f"index, {base}=100", indicator=f"Consumer Price Index, {area}, "
                         f"{sector.title()}, {group} / {subgroup}", **common), where)
        if inflation is not None:
            out.add(_obs(f"{sid}_yoy", period, inflation, UNIT_PERCENT_YOY, indicator=f"Consumer Price Index "
                         f"inflation, {area}, {sector.title()}, {group} / {subgroup}", **common), where)


def _cpi_item(rows: list[dict], name: str, out: ObservationSet, notes: list[str], series: str) -> None:
    for i, row in enumerate(rows, start=1):
        where = f"{name}, data row {i}"
        base = _field(row, "baseyear", where)
        if base not in ("2012", "2010"):
            raise SourceError(f"{where}: baseyear {base!r} is not 2012 or 2010")
        period = month_period(year_of(row.get("year"), where), month_number(row.get("month"), where))
        item = _field(row, "item", where)
        sid = f"cpi_{base}{'back' if series == 'Back' else ''}_all_india_item_{slug(item)}"
        common = dict(dataset=f"eSankhyiki CPI items, base {base}, {series} series", base=base, series_type=series,
                      area="All India", level="item", label=item, status=_field(row, "status", where, required=False))
        index, inflation = _value(row, "index", where), _value(row, "inflation", where)
        if index is not None:
            out.add(_obs(sid, period, index, f"index, {base}=100", indicator=f"Consumer Price Index, All India, item "
                         f"{item}", **common), where)
        if inflation is not None:
            out.add(_obs(f"{sid}_yoy", period, inflation, UNIT_PERCENT_YOY, indicator=f"Consumer Price Index "
                         f"inflation, All India, item {item}", **common), where)


# --- IIP ------------------------------------------------------------------------------------------------

IIP_CATEGORIES = {"general": "general", "mining & quarrying": "mining", "mining": "mining",
                  "manufacturing": "manufacturing", "electricity & gas supply": "electricity_gas",
                  "electricity": "electricity", "water supply, sewerage & waste management": "water_sewerage",
                  "primary goods": "primary_goods", "capital goods": "capital_goods",
                  "intermediate goods": "intermediate_goods",
                  "infrastructure/ construction goods": "infrastructure_goods",
                  "infrastructure/construction goods": "infrastructure_goods", "consumer durables": "consumer_durables",
                  "consumer non-durables": "consumer_nondurables"}
_IIP_LEVELS = {"General": "general", "Sectoral": "sector", "Use-based category": "use_based"}


def _iip(rows: list[dict], name: str, out: ObservationSet, notes: list[str], _series: str) -> None:
    dropped = 0
    for i, row in enumerate(rows, start=1):
        where = f"{name}, data row {i}"
        base = _field(row, "base_year", where)
        fy_start_year(base, f"{where}, base_year")
        year, month = year_of(row.get("year"), where), month_number(row.get("month"), where)
        period = month_period(year, month)
        kind = _field(row, "type", where)
        if kind not in _IIP_LEVELS:
            raise SourceError(f"{where}: type {kind!r} is not General, Sectoral or Use-based category")
        category = _field(row, "category", where)
        sub = _field(row, "sub_category", where, required=False)
        if kind == "General" and (category != "General" or sub):
            raise SourceError(f"{where}: a General row for {category!r} / {sub!r}")
        node = IIP_CATEGORIES.get(category.lower()) or slug(category)
        level = _IIP_LEVELS[kind]
        if sub:
            node, level = f"{node}_{slug(re.sub(r'(?i)^manufacture of ', '', sub))}", f"sub_{level}"
        sid = f"iip_{base.replace('-', '_')}_{node}"
        label = f"{category} / {sub}" if sub else category
        common = dict(dataset=f"eSankhyiki IIP, base {base}", base=base, series_type="Current", area="All India",
                      level=level, label=label)
        index, growth = _value(row, "index", where), _value(row, "growth_rate", where)
        if index is not None:
            out.add(_obs(sid, period, index, f"index, {base}=100", indicator=f"Index of Industrial Production, "
                         f"{label}", **common), where)
        if growth is None:
            continue
        if base == "2022-23" and (2023, 4) <= (year, month) <= (2024, 3):  # no year-ago value on this base
            if index is not None and abs(float(growth) - round(float(index) - 100, 1)) < 1e-9:
                dropped += 1
                continue
            notes.append(f"{sid} {period[0]}: a growth rate on base 2022-23 before April 2024 that is not the index "
                         "minus 100; kept")
        out.add(_obs(f"{sid}_yoy", period, growth, UNIT_PERCENT_YOY, indicator=f"Index of Industrial Production "
                     f"growth, {label}", **common), where)
    if dropped:
        notes.append(f"{dropped} growth rates of April 2023 to March 2024 on base 2022-23 dropped: they are the index "
                     "minus 100 (the base has no year-ago value), not growth rates")


# --- National accounts --------------------------------------------------------------------------------

NAS_INDUSTRIES = {"agriculture, livestock, forestry and fishing": "agriculture", "mining and quarrying": "mining",
                  "manufacturing": "manufacturing",
                  "electricity, gas, water supply & other utility services": "utilities",
                  "construction": "construction",
                  "trade, hotels, transport, communication & services related to broadcasting": "trade_transport",
                  "financial, real estate & professional services": "financial_real_estate",
                  "public administration, defence & other services": "public_admin",
                  "total gross value added": "total"}
NAS_ESTIMATES = ("First Advance Estimates", "Second Advance Estimates", "Provisional Estimates",
                 "First Revised Estimates", "Second Revised Estimates", "Third Revised Estimates", "Final Estimates")


def _nas(rows: list[dict], name: str, out: ObservationSet, notes: list[str], _series: str) -> None:
    for i, row in enumerate(rows, start=1):
        where = f"{name}, data row {i}"
        base = _field(row, "base_year", where)
        fy_start_year(base, f"{where}, base_year")
        series = _field(row, "series", where)
        if series not in ("Current", "Back"):
            raise SourceError(f"{where}: series {series!r} is not Current or Back")
        indicator = _field(row, "indicator", where)
        if indicator not in NAS_INDICATORS.values():
            raise SourceError(f"{where}: indicator {indicator!r} was not sampled (only GDP and GVA); refused")
        unit = _field(row, "unit", where)
        if unit != "₹ Crore":
            raise SourceError(f"{where}: unit {unit!r}, not ₹ Crore: the unit changed")
        start = fy_start_year(_field(row, "year", where), f"{where}, year")
        frequency = _field(row, "frequency", where)
        quarter = row.get("quarter")
        revision = _field(row, "revision", where, required=False)
        if frequency == "Quarterly":
            m = re.fullmatch(r"Q([1-4])", str(quarter or ""))
            if m is None:
                raise SourceError(f"{where}: a quarterly row with quarter {quarter!r}")
            period, freq = fy_quarter_period(start, int(m.group(1))), "q"
            if revision:
                notes.append(f"{where}: a quarterly row with an estimate type ({revision!r}); kept as its status")
        elif frequency == "Annual":
            if quarter not in (None, ""):
                raise SourceError(f"{where}: an annual row with quarter {quarter!r}")
            if revision and revision not in NAS_ESTIMATES:
                raise SourceError(f"{where}: estimate type {revision!r} is not one of {', '.join(NAS_ESTIMATES)}")
            period, freq = fy_period(start), "a"
        else:
            raise SourceError(f"{where}: frequency {frequency!r} is not Quarterly or Annual")
        back = "back" if series == "Back" else ""
        if indicator == "Gross Domestic Product":
            if "industry" in row:
                raise SourceError(f"{where}: GDP rows with industries were not sampled")
            stem, label, level = f"gdp_{base.replace('-', '_')}{back}_{freq}", "Gross Domestic Product", "total"
        else:
            if row.get("subindustry") not in (None, "") or row.get("institutional_sector") not in (None, ""):
                raise SourceError(f"{where}: GVA by sub-industry or institutional sector was not sampled; refused")
            industry = _field(row, "industry", where)
            node = NAS_INDUSTRIES.get(industry.lower()) or slug(industry)
            stem, label, level = (f"gva_{base.replace('-', '_')}{back}_{freq}_{node}",
                                  f"Gross Value Added, {industry}", "total" if node == "total" else "industry")
        common = dict(dataset=f"eSankhyiki National Accounts, base {base}, {series} series", base=base,
                      series_type=series, area="All India", level=level, label=label, status=revision)
        for price, column in (("current", "current_price"), ("constant", "constant_price")):
            value = _value(row, column, where)
            if value is None:
                raise SourceError(f"{where}: {column} is missing (null); not a documented missing value here")
            out.add(_obs(f"{stem}_{price}_cr", period, value, UNIT_CRORE,
                         indicator=f"{label}, at {price} prices ({'quarterly' if freq == 'q' else 'annual'})",
                         **common), where)


# --- WPI ------------------------------------------------------------------------------------------------

WPI_MAJOR_GROUPS = {"wholesale price index": "all", "primary articles": "primary", "fuel & power": "fuel_power",
                    "manufactured products": "manufactured", "food index": "food_index"}
_WPI_FIELDS = {"wpi_2022_23": ("major_group", "group", "sub_group", "sub_sub_group", "item"),
               "wpi_2011_12": ("majorgroup", "group", "subgroup", "sub_subgroup", "item")}
_WPI_LEVELS = ("major_group", "group", "sub_group", "sub_sub_group", "item")


def _wpi(layout: str):
    def parse(rows: list[dict], name: str, out: ObservationSet, notes: list[str], _series: str) -> None:
        names = _WPI_FIELDS[layout]
        for i, row in enumerate(rows, start=1):
            where = f"{name}, data row {i}"
            base = _field(row, "base_year", where)
            if base.replace("-", "_") != layout[4:]:
                raise SourceError(f"{where}: base_year {base!r} in the {layout} layout (field names differ by base; "
                                  "other bases were not sampled)")
            period = month_period(year_of(row.get("year"), where), month_number(row.get("month"), where))
            path = [(level, _field(row, key, where, required=False))
                    for level, key in zip(_WPI_LEVELS, names, strict=True)]
            major = path[0][1]
            if not major:
                raise SourceError(f"{where}: no major group")
            parts = [(level, text) for level, text in path if text]
            node = WPI_MAJOR_GROUPS.get(major.lower()) or slug(major)
            if node == "all" and len(parts) > 1:
                raise SourceError(f"{where}: rows under the headline {major!r}")
            level = parts[-1][0] if len(parts) > 1 else ("all" if node == "all" else "major_group")
            sid = "__".join([f"wpi_{layout[4:]}_{node}", *(slug(t) for _, t in parts[1:])])
            value = _value(row, "index_value", where)
            if value is None:
                continue
            label = " / ".join(t for _, t in parts)
            out.add(_obs(sid, period, value, f"index, {base}=100", dataset=f"eSankhyiki WPI, base {base}",
                         indicator=f"Wholesale Price Index, {label}", base=base, series_type="Current",
                         area="All India", level=level, label=label), where)
    return parse


# --- The "RBI" tables -----------------------------------------------------------------------------------

_RBI_CURRENCIES = {"US Dollar": "usd", "SDR": "sdr", "Pound Sterling": "gbp", "Euro": "eur", "Japanese Yen": "jpy"}
_RBI_RATES = {"Average": "average", "End-month": "end_month"}
RBI_FX_INDICATOR = ("Exchange Rate of the Indian Rupee vis-à-vis the SDR, US Dollar, Pound Sterling, Euro and "
                    "Japanese Yen")


def _rbi(rows: list[dict], name: str, out: ObservationSet, notes: list[str], _series: str) -> None:
    for i, row in enumerate(rows, start=1):
        where = f"{name}, data row {i}"
        indicator = _field(row, "indicator", where)
        if not indicator.startswith(RBI_FX_INDICATOR):
            raise SourceError(f"{where}: RBI table {indicator[:60]!r} was not sampled (only indicator 33)")
        unit = _field(row, "unit", where)
        if unit != "₹ per unit of foreign currency":
            raise SourceError(f"{where}: unit {unit!r}: the unit changed")
        currency, rate = _field(row, "currency", where), _field(row, "reference_rate", where)
        if currency not in _RBI_CURRENCIES or rate not in _RBI_RATES:
            raise SourceError(f"{where}: currency {currency!r} with reference rate {rate!r} was not sampled")
        period = month_period(year_of(row.get("year"), where), month_number(row.get("month"), where))
        value = _value(row, "value", where)
        if value is None:
            continue
        sid = f"mospi_rbi_inr_per_{_RBI_CURRENCIES[currency]}_{_RBI_RATES[rate]}"
        note = "printed per unit, but the level is per 100 yen" if currency == "Japanese Yen" else ""
        out.add(_obs(sid, period, value, "rupees per 100 yen" if currency == "Japanese Yen" else
                     f"rupees per {currency}", dataset="eSankhyiki RBI tables, indicator 33",
                     indicator=f"Exchange rate of the rupee, {currency}, {rate} (RBI, via MoSPI)", area="India",
                     level=rate, label=currency, notes=note), where)


_ROW_PARSERS = {"cpi_2024": _cpi_2024, "cpi_group": _cpi_group, "cpi_item": _cpi_item, "iip": _iip, "nas": _nas,
                "nas_gva": _nas, "wpi_2022_23": _wpi("wpi_2022_23"), "wpi_2011_12": _wpi("wpi_2011_12"),
                "rbi_records": _rbi}


# --- Pages ------------------------------------------------------------------------------------------------

def combine_pages(results: Sequence[ParseResult], *, complete: bool = True) -> ParseResult:
    """The pages of one query as one result: refused if they disagree, repeat a page or a value, or miss a page.

    ``complete=False`` accepts a subset of the pages (the two-page CPI shortcut
    of ``cpi_2024_top_levels``) but still checks that the pages agree.
    """
    if not results:
        raise ValueError("no pages to combine")
    first = results[0]
    totals = {(r.meta.get("total_records"), r.meta.get("records_per_page"), r.meta.get("total_pages"))
              for r in results}
    if len(totals) != 1:
        raise SourceError(f"{first.name}: the pages disagree on the query's size {sorted(totals)} (did a release "
                          "come out between two pages? fetch them again)")
    layouts = {r.layout for r in results}
    if len(layouts) != 1:
        raise SourceError(f"{first.name}: pages of different layouts {sorted(layouts)}")
    pages = [int(r.meta.get("page", "0")) for r in results]
    if len(set(pages)) != len(pages):
        raise SourceError(f"{first.name}: page(s) {sorted({p for p in pages if pages.count(p) > 1})} given twice")
    total_pages = int(first.meta.get("total_pages") or 0)
    if complete and sorted(pages) != list(range(1, total_pages + 1)):
        missing = sorted(set(range(1, total_pages + 1)) - set(pages))
        raise SourceError(f"{first.name}: pages {missing} of {total_pages} are missing")
    out = ObservationSet(" + ".join(r.name for r in results))
    for result in sorted(results, key=lambda r: int(r.meta.get("page", "0"))):
        for obs in result.observations:
            out.add(obs, f"page {result.meta.get('page')}")
    rows = sum(int(r.meta.get("rows") or 0) for r in results)
    if complete and rows != int(first.meta.get("total_records") or 0):
        raise SourceError(f"{first.name}: the pages hold {rows} rows, not the {first.meta.get('total_records')} the "
                          "query has")
    meta = dict(first.meta, page=",".join(map(str, sorted(pages))), rows=str(rows))
    notes = [n for r in results for n in r.notes] + out.notes()
    return ParseResult(first.source, out.name, first.layout, out.observations(), notes, meta)


CPI_2024_DIVISIONS = 12
CPI_2024_GROUPS = 43


def cpi_2024_top_levels(result: ParseResult) -> list[str]:
    """What is missing for ``result`` (pages 1 and 2 of one month, all sectors) to hold every top-level node.

    Spec of the polling shortcut: two pages of 100 rows hold the headline, the
    12 divisions and the 43 groups for each sector (168 rows) when MoSPI keeps
    its order (newest month, then headline, divisions, groups). An empty list
    means they do; otherwise each line says what is missing, and all pages
    should be read.
    """
    problems = []
    sectors: dict[tuple[str, str, str], dict[str, set[str]]] = {}
    for obs in result.observations:
        if obs.series_id.endswith("_yoy"):
            continue
        key = (obs.period, obs.area, obs.sector)
        sectors.setdefault(key, {"general": set(), "division": set(), "group": set()})
        if obs.level in sectors[key]:
            sectors[key][obs.level].add(obs.code or "general")
    if not sectors:
        return ["no base-2024 CPI rows"]
    for (period, area, sector), levels in sorted(sectors.items()):
        counts = {k: len(v) for k, v in levels.items()}
        want = {"general": 1, "division": CPI_2024_DIVISIONS, "group": CPI_2024_GROUPS}
        for level, n in want.items():
            if counts[level] != n:
                problems.append(f"{period} {area} {sector}: {counts[level]} {level} rows, not {n}")
    return problems


# --- The release calendar's pointer ---------------------------------------------------------------------

@dataclass(frozen=True)
class CalendarPointer:
    """The current advance release calendar, as MoSPI's pointer names it (the PDF itself is not read here)."""

    document_id: str
    filename: str
    url: str
    size: int
    created_at: str


def parse_release_calendar_pointer(body: bytes, name: str) -> CalendarPointer:
    """The pointer's file name, absolute URL (spaces as ``%20``), size and creation time; refused if any is off."""
    doc = load_json(body, name)
    if not isinstance(doc, dict):
        raise SourceError(f"{name}: not the release calendar pointer (a JSON object was expected)")
    data = doc.get("data")
    if doc.get("status") != "success" or not isinstance(data, dict):
        raise SourceError(f"{name}: not the release calendar pointer (status {doc.get('status')!r})")
    for key in ("id", "filename", "url", "filemime", "size", "created_at"):
        if not str(data.get(key) or "").strip():
            raise SourceError(f"{name}: the pointer has no {key}")
    if data["filemime"] != "application/pdf" or not str(data["url"]).startswith("uploads/"):
        raise SourceError(f"{name}: the pointer names {data['url']!r} ({data['filemime']}), not a PDF under uploads/")
    if not str(data["size"]).isdigit():
        raise SourceError(f"{name}: size {data['size']!r} is not a number of bytes")
    url = f"{WEBSITE}/" + "/".join(part.replace(" ", "%20") for part in str(data["url"]).split("/"))
    return CalendarPointer(str(data["id"]), str(data["filename"]), url, int(data["size"]), str(data["created_at"]))


# --- Release dates -------------------------------------------------------------------------------------

DATA_DIR = Path(str(files("cr_strat") / "data"))
RELEASE_DATES_CSV = DATA_DIR / "macro_release_dates.csv"
RELEASE_DATE_COLUMNS = ("family", "reference_period", "content", "planned_date_first", "planned_date",
                        "release_date", "release_time_ist", "time_rule", "status", "source", "notes")
FAMILIES = ("cpi", "iip", "wpi", "gdp")
STAGES = {"cpi": ("first", "final"), "iip": ("quick", "first_revision", "final"), "wpi": ("provisional", "final"),
          "gdp": ("quarter", "fae", "sae", "pe", "fre", "sre", "tre", "final")}
RELEASE_STATUSES = ("actual", "planned")
_MONTHLY = ("cpi", "iip", "wpi")


@dataclass(frozen=True)
class ReleaseDate:
    """One row of a release-date table: a release, the periods and stages it carries, when it was or is due."""

    family: str
    reference_period: str
    content: tuple[tuple[str, str], ...]
    planned_date_first: dt.date | None
    planned_date: dt.date | None
    release_date: dt.date | None
    release_time: dt.time
    time_rule: str
    status: str
    source: str
    notes: str
    origin: str

    @property
    def release_id(self) -> str:
        return f"{self.family}_{self.reference_period}"


def _bounds(family: str, period: str) -> tuple[str, dt.date, dt.date]:
    """``period_bounds`` as ``family`` writes its periods: a national-accounts ``2011-12`` is a financial year, not
    December 2011 (``period_bounds`` alone reads it as a month)."""
    if family == "gdp":
        return period_bounds(period, "fy_quarter" if "Q" in str(period) else "fy")
    return period_bounds(period)


def _check_period(family: str, period: str, where: str, *, fy_ok: bool = False) -> str:
    try:
        kind, _, _ = _bounds(family, period)
    except (ValueError, SourceError) as exc:
        raise ValueError(f"{where}: {exc}") from None
    allowed = ("month",) if family in _MONTHLY else (("fy_quarter", "fy") if fy_ok else ("fy_quarter",))
    if kind not in allowed:
        raise ValueError(f"{where}: {period!r} is a {kind}; a {family} period must be a {' or '.join(allowed)}")
    return period


def _date(text: str, where: str, column: str) -> dt.date | None:
    text = text.strip()
    if not text:
        return None
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"{where}: {column} must be YYYY-MM-DD, not {text!r}") from None


def _content(family: str, text: str, where: str) -> tuple[tuple[str, str], ...]:
    out = []
    for part in (p.strip() for p in text.split(";")):
        m = re.fullmatch(r"([a-z_]+) (\S+)", part)
        if m is None or m.group(1) not in STAGES[family]:
            raise ValueError(f"{where}: content {part!r} is not '<stage> <period>' with a stage of "
                             f"{', '.join(STAGES[family])}")
        stage, period = m.groups()
        fy_ok = family == "gdp" and stage != "quarter"
        out.append((stage, _check_period(family, period, where, fy_ok=fy_ok)))
    if len(set(out)) != len(out):
        raise ValueError(f"{where}: content repeats a stage and period")
    return tuple(out)


def read_release_dates(source: str | Path | bytes | None = None, *, name: str | None = None) -> list[ReleaseDate]:
    """A release-date table (default: the one shipped with the package), every row checked.

    Refused, naming the file and the line: a missing column, an unknown
    family or status, a period that does not suit its family, content that is
    not ``<stage> <period>`` pairs led by the release's own first print, a
    date that is not ISO or comes before the end of the period, a release
    date on a planned row or none on an actual one, a time that is not HH:MM,
    an empty source, a release listed twice.
    """
    if source is None:
        source = RELEASE_DATES_CSV
    if isinstance(source, bytes):
        text, name = text_of(source, name or "<bytes>"), name or "<bytes>"
    else:
        path = Path(source)
        text, name = text_of(path.read_bytes(), str(path)), name or str(path)
    reader = csv.DictReader(io.StringIO(text))
    missing = [c for c in RELEASE_DATE_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"{name}: a release-date table needs the columns {list(RELEASE_DATE_COLUMNS)}; missing "
                         f"{missing}")
    rows, seen = [], set()
    for line, rec in enumerate(reader, start=2):
        where = f"{name}, line {line}"
        family = (rec["family"] or "").strip()
        if family not in FAMILIES:
            raise ValueError(f"{where}: family {family!r} is not one of {', '.join(FAMILIES)}")
        ref = _check_period(family, (rec["reference_period"] or "").strip(), where, fy_ok=True)
        content = _content(family, rec["content"] or "", where)
        lead = {"cpi": "first", "iip": "quick", "wpi": "provisional"}.get(family)
        if family == "gdp":
            lead = "quarter" if _bounds(family, ref)[0] == "fy_quarter" else "fae"
        if content[0] != (lead, ref):
            raise ValueError(f"{where}: content must begin with '{lead} {ref}', the release's own first print")
        first = _date(rec["planned_date_first"] or "", where, "planned_date_first")
        planned = _date(rec["planned_date"] or "", where, "planned_date")
        released = _date(rec["release_date"] or "", where, "release_date")
        status = (rec["status"] or "").strip()
        if status not in RELEASE_STATUSES:
            raise ValueError(f"{where}: status {status!r} is not actual or planned")
        if status == "actual" and released is None:
            raise ValueError(f"{where}: an actual release without a release_date")
        if status == "planned" and released is not None:
            raise ValueError(f"{where}: a planned release with a release_date (mark it actual)")
        if planned is None and released is None:
            raise ValueError(f"{where}: neither a planned_date nor a release_date")
        kind, start, end = _bounds(family, ref)
        floor = start if family == "gdp" and kind == "fy" else end
        for column, day in (("planned_date_first", first), ("planned_date", planned), ("release_date", released)):
            if day is not None and day <= floor:
                raise ValueError(f"{where}: {column} {day} is not after {floor}, so it cannot release {ref}")
        m = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", (rec["release_time_ist"] or "").strip())
        if m is None:
            raise ValueError(f"{where}: release_time_ist must be HH:MM (India time), not {rec['release_time_ist']!r}")
        source_text = (rec["source"] or "").strip()
        if not source_text:
            raise ValueError(f"{where}: source is empty: every row says where its date comes from")
        if (family, ref) in seen:
            raise ValueError(f"{where}: {family} {ref} is listed twice")
        seen.add((family, ref))
        rows.append(ReleaseDate(family, ref, content, first, planned, released,
                                dt.time(int(m.group(1)), int(m.group(2))), (rec["time_rule"] or "").strip(), status,
                                source_text, (rec["notes"] or "").strip(), f"{Path(name).name}, line {line}"))
    return rows


# --- When a value was public -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Release:
    """One release of a family, dated: from the table (actual or planned) or by the family's rule."""

    release_id: str
    family: str
    reference_period: str
    day: dt.date
    time: dt.time
    basis: str
    rule: str
    source: str = ""
    notes: str = ""

    @property
    def public_from(self) -> dt.datetime:
        """India time, without a zone."""
        return dt.datetime.combine(self.day, self.time)

    @property
    def public_from_utc(self) -> dt.datetime:
        return self.public_from.replace(tzinfo=IST).astimezone(UTC)


@dataclass(frozen=True)
class BaseLaunch:
    """When a base's values were first public: India time, the release that launched it, and how this is known."""

    time: dt.datetime
    release_id: str
    note: str


BASE_LAUNCHES: dict[tuple[str, str], BaseLaunch] = {
    ("cpi", "2024"): BaseLaunch(dt.datetime(2026, 2, 12, 16, 0), "cpi_2026-01", "base 2024 launched with the release "
                                "of 12 Feb 2026 at 16:00 (news reports)"),
    ("cpi", "2024back"): BaseLaunch(dt.datetime(2026, 2, 12, 16, 0), "cpi_2026-01", "the base-2024 back series came "
                                    "with the base's launch on 12 Feb 2026 (news reports)"),
    ("cpi", "2012"): BaseLaunch(dt.datetime(2015, 2, 28, 23, 59, 59), "cpi_2015-01", "base 2012 believed launched on "
                                "12 Feb 2015 (not checked): the end of that month is used"),
    ("gdp", "2022-23"): BaseLaunch(dt.datetime(2026, 2, 27, 16, 0), "gdp_2025-26Q3", "base 2022-23 launched with the "
                                   "release of 27 Feb 2026 (news reports)"),
    ("gdp", "2011-12"): BaseLaunch(dt.datetime(2015, 1, 31, 23, 59, 59), "", "base 2011-12 believed launched on 30 Jan "
                                   "2015 (not checked): the end of that month is used"),
    ("iip", "2022-23"): BaseLaunch(dt.datetime(2026, 6, 1, 16, 0), "iip_2026-04", "base 2022-23 launched with the "
                                   "release of 1 Jun 2026 (news reports)"),
    ("iip", "2011-12"): BaseLaunch(dt.datetime(2017, 5, 31, 23, 59, 59), "iip_2017-03", "base 2011-12 believed "
                                   "launched on 12 May 2017 (not checked): the end of that month is used"),
    ("wpi", "2022-23"): BaseLaunch(dt.datetime(2026, 6, 15, 12, 0), "wpi_2026-05", "base 2022-23 launched with the "
                                   "release of 15 Jun 2026 (news reports)"),
    ("wpi", "2011-12"): BaseLaunch(dt.datetime(2017, 5, 31, 23, 59, 59), "wpi_2017-04", "base 2011-12 believed "
                                   "launched in May 2017 (not checked): the end of that month is used"),
}
LIVE_WINDOW = dt.timedelta(hours=6)  # a value first seen this soon after its release time is dated by the release
_IIP_NEW_RULE = (2025, 3)  # March 2025 data: the first release on the 28th
_IIP_TRANSITION = ((2024, 12), (2025, 2))  # finals of these months came with the release of 28 Apr 2025


def _time_of_day(family: str, day: dt.date) -> tuple[dt.time, str]:
    if family == "wpi":
        return dt.time(12, 0), "12:00 (the Office of the Economic Adviser's embargo; news reports)"
    if family == "gdp":
        if day >= dt.date(2024, 11, 29):
            return dt.time(16, 0), "16:00 since 29 Nov 2024 (news reports)"
        return dt.time(17, 30), "17:30 before 29 Nov 2024 (news reports; before July 2013 not checked)"
    if day >= dt.date(2024, 11, 12):
        return dt.time(16, 0), "16:00 since 12 Nov 2024 (news reports)"
    if day >= dt.date(2013, 7, 1):
        return dt.time(17, 30), "17:30 from July 2013 to October 2024 (news reports)"
    return dt.time(11, 30), "11:00 to 11:30 before July 2013 (news reports; the later used)"


def _ym(period: str) -> tuple[int, int]:
    year, month = period.split("-")
    return int(year), int(month)


def _month_text(year: int, month: int) -> str:
    return f"{year}-{month:02d}"


_HOLIDAY_PROXY: BusinessCalendar | None = None
NEXT_WORKING_DAY = ("weekends and the Mumbai settlement holidays skipped (a proxy for the central government's "
                    "holidays that can only make the date later; weekends only outside the list's years, 2015 to "
                    "2026)")
PREVIOUS_WORKING_DAY = ("weekday holidays not known: on a holiday the release came the working day before, earlier "
                        "than this date")


def next_working_day(day: dt.date) -> dt.date:
    """``day``, or the next day that is neither a Saturday, a Sunday nor a Mumbai settlement holiday.

    MoSPI's working days are the central government's in New Delhi, for which no list is shipped. The Mumbai
    settlement holidays (``calendars``, 2015 to 2026) stand in for them where a rule or a planned date says "the next
    working day": skipping a Mumbai-only holiday makes a date later than the release (14 Sep 2026, Ganesh
    Chaturthi in Mumbai, was a release day), never earlier, so a value dated by it is late rather than early. Outside
    the list's years only weekends are skipped. Never used for a "previous working day" rule, where it would make
    dates earlier.
    """
    global _HOLIDAY_PROXY
    if _HOLIDAY_PROXY is None:
        _HOLIDAY_PROXY = mumbai_settlement_calendar(strict=False)
    while not _HOLIDAY_PROXY.is_business_day(day):
        day += dt.timedelta(days=1)
    return day


def rule_release(family: str, reference_period: str) -> Release:
    """The release of ``reference_period`` dated by the family's rule (no table), with the rule's text."""
    if family not in FAMILIES:
        raise ValueError(f"family must be one of {', '.join(FAMILIES)}, not {family!r}")
    holidays = NEXT_WORKING_DAY
    if family in _MONTHLY:
        year, month = _ym(_check_period(family, reference_period, "rule_release"))
        if family == "cpi":
            y, m = add_months(year, month, 1)
            day, rule = next_working_day(dt.date(y, m, 12)), "the 12th of the next month (next working day)"
        elif family == "wpi":
            y, m = add_months(year, month, 1)
            day, rule = next_working_day(dt.date(y, m, 14)), "the 14th of the next month (next working day)"
        elif (year, month) >= _IIP_NEW_RULE:
            y, m = add_months(year, month, 1)
            day, rule = next_working_day(dt.date(y, m, 28)), "the 28th of the next month (next working day)"
        else:
            y, m = add_months(year, month, 2)
            day, rule = (previous_weekday(dt.date(y, m, 12)),
                         "the 12th of the second month after (previous working day), the rule to April 2025")
            holidays = PREVIOUS_WORKING_DAY
    else:
        kind, _, _ = _bounds(family, _check_period(family, reference_period, "rule_release", fy_ok=True))
        fy_start = fy_start_year(reference_period[:7], "rule_release")
        if kind == "fy":
            day, rule = next_working_day(dt.date(fy_start + 1, 1, 7)), "first advance estimates on 7 January"
        else:
            quarter = int(reference_period[-1])
            y, m = {1: (fy_start, 8), 2: (fy_start, 11), 3: (fy_start + 1, 2), 4: (fy_start + 1, 5)}[quarter]
            day, rule = last_weekday_of_month(y, m), "the last working day of the second month after the quarter"
            holidays = PREVIOUS_WORKING_DAY
    time, time_rule = _time_of_day(family, day)
    return Release(f"{family}_{reference_period}", family, reference_period, day, time, "release_rule",
                   f"{rule}, {time_rule}; {holidays}")


class ReleaseCalendar:
    """Release dates from a table (shipped, and a user's), and the rules for everything else (module docstring)."""

    def __init__(self, rows: Iterable[ReleaseDate] = ()):
        self.rows: dict[tuple[str, str], ReleaseDate] = {}
        for row in rows:
            self.rows[(row.family, row.reference_period)] = row  # a later row (the user's) replaces an earlier one
        self.carriers: dict[tuple[str, str, str], str] = {}
        for row in self.rows.values():
            for stage, period in row.content:
                self.carriers.setdefault((row.family, stage, period), row.reference_period)

    @classmethod
    def load(cls, user: str | Path | bytes | None = None, *, shipped: bool = True) -> ReleaseCalendar:
        """The shipped table, overridden or extended row by row by the user's (same columns), if given."""
        rows = read_release_dates() if shipped else []
        if user is not None:
            rows += read_release_dates(user)
        return cls(rows)

    def release(self, family: str, reference_period: str) -> Release:
        """A release, from the table if it lists it (an actual date, else the planned one), else by the rule.

        A planned date on a Saturday, a Sunday or a holiday moves to the next
        working day (2026-27 calendar: "next working day"; the 2025-26 one
        says previous working day for IIP, contradicted by MoSPI's note: the
        later is used), holidays by ``next_working_day``'s proxy. An actual
        date is taken as it is.
        """
        row = self.rows.get((family, reference_period))
        if row is None:
            return rule_release(family, reference_period)
        if row.release_date is not None:
            day, basis, how = row.release_date, "release_table", "actual date"
        else:
            day = next_working_day(row.planned_date)
            basis = "release_table_planned"
            how = "planned date" + (f" {row.planned_date}, a weekend or holiday, moved to the next working day "
                                    f"({NEXT_WORKING_DAY})" if day != row.planned_date else "")
        rule = f"{how} ({row.origin}); {row.release_time:%H:%M} India time: {row.time_rule or 'as listed'}"
        return Release(row.release_id, family, reference_period, day, row.release_time, basis, rule, row.source,
                       row.notes)

    def _carrier(self, family: str, stage: str, period: str, default: str) -> Release:
        return self.release(family, self.carriers.get((family, stage, period), default))

    def _gdp_releases(self, first_year: int, last_year: int) -> list[Release]:
        refs = []
        for year in range(first_year, last_year + 1):
            fy = fy_name(year)
            refs += [f"{fy}Q1", f"{fy}Q2", fy, f"{fy}Q3", f"{fy}Q4"]
        return sorted((self.release("gdp", r) for r in refs), key=lambda r: (r.public_from, r.release_id))

    def gdp_final(self, fy: str) -> Release:
        """The release of a financial year's final national accounts estimates (table, else the rule)."""
        start = fy_start_year(fy, "gdp_final")
        default = f"{fy_name(start + 2)}Q3" if start >= 2023 else f"{fy_name(start + 3)}Q3"
        return self._carrier("gdp", "final", fy, default)

    def stages(self, family: str, period: str, *, base: str | None = None,
               estimate: str | None = None) -> list[tuple[str, Release]]:
        """Every release that could have set the value of ``period``, in time order, each with its stage.

        CPI: first print, final. IIP: quick, (first revision before April
        2025), final. WPI: provisional, final. A GDP quarter: its first
        estimate, every later national-accounts release up to its year's final
        estimates (``revised``), the final. A GDP year: the release of the
        printed estimate type (``estimate``), or the whole ladder of advance,
        provisional, revised and final estimates when it is not given.
        """
        if family not in FAMILIES:
            raise ValueError(f"family must be one of {', '.join(FAMILIES)}, not {family!r}")
        if family in _MONTHLY:
            year, month = _ym(_check_period(family, period, "stages"))
            nxt = _month_text(*add_months(year, month, 1))
            if family == "cpi":
                return [("first", self._carrier("cpi", "first", period, period)),
                        ("final", self._carrier("cpi", "final", period, nxt))]
            if family == "wpi":
                return [("provisional", self._carrier("wpi", "provisional", period, period)),
                        ("final", self._carrier("wpi", "final", period, _month_text(*add_months(year, month, 2))))]
            out = [("quick", self._carrier("iip", "quick", period, period))]
            if (year, month) >= _IIP_NEW_RULE:
                return out + [("final", self._carrier("iip", "final", period, nxt))]
            if (year, month) < _IIP_TRANSITION[0]:
                out.append(("first_revision", self._carrier("iip", "first_revision", period, nxt)))
                return out + [("final", self._carrier("iip", "final", period,
                                                      _month_text(*add_months(year, month, 3))))]
            if (year, month) < _IIP_TRANSITION[1]:
                out.append(("first_revision", self._carrier("iip", "first_revision", period, nxt)))
            return out + [("final", self._carrier("iip", "final", period, _month_text(*_IIP_NEW_RULE)))]
        kind, start, _ = _bounds("gdp", _check_period("gdp", period, "stages", fy_ok=True))
        if kind == "fy_quarter":
            fy = fy_name(start.year if start.month >= 4 else start.year - 1)
            first = self._carrier("gdp", "quarter", period, period)
            final = self.gdp_final(fy)
            fy_start = fy_start_year(fy, "stages")
            later = [r for r in self._gdp_releases(fy_start, fy_start + 3)
                     if first.public_from < r.public_from < final.public_from]
            return [("quarter", first), *(("revised", r) for r in later), ("final", final)]
        fy, s = period, start.year
        ladder = [("fae", self._carrier("gdp", "fae", fy, fy)),
                  ("sae", self._carrier("gdp", "sae", fy, f"{fy}Q3")),
                  ("pe", self._carrier("gdp", "pe", fy, f"{fy}Q4")),
                  ("fre", self._carrier("gdp", "fre", fy, f"{fy_name(s + 1)}Q3"))]
        if s < 2023:
            ladder += [("sre", self._carrier("gdp", "sre", fy, f"{fy_name(s + 2)}Q3"))]
        ladder.append(("final", self.gdp_final(fy)))
        stage = _ESTIMATE_STAGES.get(estimate or "")
        if stage is None:
            return sorted(ladder, key=lambda x: x[1].public_from)
        if stage == "tre":
            return [("tre", self._carrier("gdp", "tre", fy, f"{fy_name(s + 3)}Q3"))]
        return [x for x in ladder if x[0] == stage]

    def first_print(self, family: str, period: str, *, base: str | None = None) -> Release:
        return self.stages(family, period, base=base)[0][1]

    def final_print(self, family: str, period: str, *, base: str | None = None) -> Release:
        return self.stages(family, period, base=base)[-1][1]

    def backfill_public_from(self, family: str, base: str, period: str, seen_at: dt.datetime, *,
                             estimate: str | None = None) -> PublicFrom:
        """From when the value read at ``seen_at`` (the latest vintage the API serves) was public.

        The last stage released at or before ``seen_at`` (``stages``), never
        before the base's launch (``BASE_LAUNCHES``). A value read before every
        release that could have set it (the table or the rule is wrong) is
        dated by ``seen_at`` itself (``first_seen``).
        """
        if seen_at.tzinfo is None or seen_at.utcoffset() is None:
            raise ValueError(f"seen_at must be a timezone-aware datetime, not {seen_at!r}")
        ladder = self.stages(family, period, base=base, estimate=estimate)
        known = [(stage, r) for stage, r in ladder if r.public_from_utc <= seen_at]
        seen = to_ist(seen_at)
        if not known:
            return PublicFrom(seen, "first_seen", "read before every release the calendar or the rule gives for it",
                              ladder[0][1].release_id)
        stage, release = known[-1]
        time, basis, rule = release.public_from, release.basis, f"{stage}: {release.rule}"
        launch = BASE_LAUNCHES.get((family, base))
        if launch is not None and launch.time > time:
            if launch.time > seen:
                return PublicFrom(seen, "first_seen", f"read before the base's launch as recorded ({launch.note})",
                                  release.release_id)
            return PublicFrom(launch.time, "release_rule", f"{stage}, but not before the base's launch: {launch.note}",
                              launch.release_id or release.release_id)
        return PublicFrom(time, basis, rule, release.release_id)

    def live_public_from(self, family: str, period: str, stage: str, first_seen: dt.datetime, *,
                         base: str | None = None, estimate: str | None = None) -> PublicFrom:
        """From when a value first seen by polling at ``first_seen`` was public.

        The release time of its stage if ``first_seen`` falls on the release
        day, at or after that time and within ``LIVE_WINDOW``; otherwise
        ``first_seen`` (``basis`` ``first_seen``): a value read a day late, or
        before the time the calendar gives, is dated by its sighting.
        """
        if first_seen.tzinfo is None or first_seen.utcoffset() is None:
            raise ValueError(f"first_seen must be a timezone-aware datetime, not {first_seen!r}")
        ladder = self.stages(family, period, base=base, estimate=estimate)
        candidates = [r for s, r in ladder if s == stage]  # a GDP quarter is revised at several releases
        if not candidates:
            names = ", ".join(dict.fromkeys(s for s, _ in ladder))
            raise ValueError(f"{family} {period} has no stage {stage!r}; its stages: {names}")
        seen = to_ist(first_seen)
        for release in candidates:
            if seen.date() == release.day and release.public_from <= seen <= release.public_from + LIVE_WINDOW:
                return PublicFrom(release.public_from, release.basis, f"{stage}: {release.rule}",
                                  release.release_id)
        earlier = [r for r in candidates if r.public_from <= seen]
        release = earlier[-1] if earlier else candidates[0]
        return PublicFrom(seen, "first_seen", f"{stage}: seen outside {LIVE_WINDOW.total_seconds() / 3600:g} h after "
                          f"the release time {release.public_from:%Y-%m-%dT%H:%M}", release.release_id)


_ESTIMATE_STAGES = {"First Advance Estimates": "fae", "Second Advance Estimates": "sae", "Provisional Estimates": "pe",
                    "First Revised Estimates": "fre", "Second Revised Estimates": "sre",
                    "Third Revised Estimates": "tre", "Final Estimates": "final"}


def timing_key(obs: Observation) -> tuple[str, str, str, str | None] | None:
    """``(family, base, period, estimate)`` by which ``ReleaseCalendar`` dates an observation; ``None`` if none.

    The "RBI" tables are RBI's data and are not dated by MoSPI's calendar.
    """
    sid = obs.series_id
    if sid.startswith("cpi_"):
        return "cpi", obs.base + ("back" if obs.series_type == "Back" else ""), obs.period, None
    if sid.startswith(("gdp_", "gva_")):
        return "gdp", obs.base, obs.period, (obs.status or None) if obs.period_type == "fy" else None
    for family in ("iip", "wpi"):
        if sid.startswith(f"{family}_"):
            return family, obs.base, obs.period, None
    return None
