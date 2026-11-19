"""US Treasury yields, the New York Fed's rates, EIA's Brent price, the Fed's H.10 rupee rate, BIS and index events.

Build-order item 5 of ``docs/rates_data_sources.md``: the global series S5
(the long end against US yields, index inclusion) and S7 (the policy path:
US rates, SOFR, Brent, the rupee) need, each parsed into
``macro_base.Observation`` rows and dated by when it was public. Requests are
built here; fetching politely is ``macro_sources.fetch``. Sampled on 18 Nov
2026 unless said otherwise; samples stay outside the repository and the tests
use typed-in fixtures.

Sources (``Request.kind`` in brackets) and their terms as read (re-read them
before any bulk download):

* **US Treasury daily par yield curve**: one CSV per year,
  ``home.treasury.gov/.../daily-treasury-rates.csv/<YEAR>/all?type=daily_treasury_yield_curve
  &field_tdr_date_value=<YEAR>&page&_format=csv`` (``ust_par``). The Site
  Policies and Notices page has no reuse, copyright or automated-access
  clause; robots.txt allows all; US federal government data. Cite "U.S.
  Department of the Treasury, Daily Treasury Par Yield Curve Rates".
* **New York Fed reference rates**: ``markets.newyorkfed.org/api/rates/
  {secured,unsecured}/all/search.json?startDate=&endDate=`` (``nyfed_json``),
  one rate's last N as CSV (``nyfed_csv``). The terms grant a licence to
  access the content "manually or through an automated process" and to
  download, store and use it; a user of reference-rate data must carry the
  notice in ``NYFED_NOTICE``.
* **EIA Europe Brent spot (RBRTE)**: ``www.eia.gov/dnav/pet/hist/RBRTED.htm``
  (``eia_brent``). Public domain, to be cited with the publication date;
  material licensed from private companies is excluded, and the series' own
  copyright field was not read (``macro_sources`` leaves EIA off by default).
* **Federal Reserve H.10, the rupee**: ``www.federalreserve.gov/releases/h10/
  hist/dat00_in.htm`` (2000 on) and ``dat96_in.htm`` (the 1990s)
  (``h10_history``); the Data Download Program's CSV (``h10_ddp``), which the
  Board will remove in 2027. Public domain unless indicated; cite the Board.
* **BIS**: ``stats.bis.org/api/v2/data/dataflow/BIS/WS_CBPOL/1.0/D.IN+US``
  (``bis_cbpol``), ``/api/v1/data/WS_XRU/D.IN../all`` (``bis_xru``), the
  release calendar (``bis_release_calendar``). Use is unrestricted if the BIS
  is cited and, in a commercial product, its statistics add no charge; the
  API may limit an IP address and asks users to use its latest version:
  WS_CBPOL is asked on v2, WS_XRU still on v1, as its v2 layout has not been
  sampled (one request would settle it; not made).
* **Index events**: no feed. A table kept by hand holds the decisions on
  Indian government bonds, each with its source and ``verified`` (on the
  provider's own page) or ``unverified`` (news only); nothing is read unless
  a table is given (``read_index_events``). The facts found on 17 and 18 Nov
  2026 are in the example table ``data/index_events_example.csv`` of the
  repository, to be checked, copied and passed by the user: never a default.

**Quirks.**

* Treasury: newest row first, ``MM/DD/YYYY``, no newline after the last row;
  columns appeared over time (2 Mo from 16 Oct 2018, 4 Mo from 19 Oct 2022,
  1.5 Month from 18 Feb 2025; 30 Yr absent from 18 Feb 2002 to 9 Feb 2006),
  so columns are mapped by name, any subset is accepted and a blank cell is
  missing. Every value of the files read (2017 to 2026) has two decimals: a
  value without them is refused, which is how a file cut short in its last
  row shows. Only the par curve was sampled: real-yield, bill and long-term
  files are refused until one has been read. The probe of 18 Nov 2026 was
  refused by Treasury's Akamai front end ("Access Denied", HTTP 403) on an
  ``/all/all`` path, which ``macro_sources`` never requests; the 2026 file
  of 17 Nov, and the files of 2017 to 2026 on 18 Nov, were read. No ETag or
  Last-Modified: no conditional request.
* Answers cut short (a saved file of an interrupted download): an EIA or
  H.10 page must end with ``</html>``, a BIS answer with a newline and every
  row of a CSV must have the header's cells; otherwise it is refused.
* New York Fed: JSON numbers are read as their text (``3.90``, not 3.9).
  Layouts change over time: EFFR to 15 Dec 2008 has a point target and the
  intraday range and standard deviation, to 29 Feb 2016 a target range, then
  percentiles and volume; OBFR from 1 Mar 2016; SOFR, BGCR and TGCR from 2
  Apr 2018; the SOFR averages and index (``SOFRAI``) from 2 Mar 2020, dated
  on their publication day. ``NA`` (percentiles on a few footnoted days) is
  missing; ``revisionIndicator`` ``r`` marks a revised rate; ``footnoteId``
  has no text in the answers. US holidays have no row. No validators.
* EIA: one table row per week (the Monday's year, ``Nov- 9`` with a space
  before a one-digit day, weeks across a new year ``Dec-28 to Jan- 1``),
  five cells Monday to Friday, an empty cell for a day without a price,
  spacer rows; ``-``, ``--``, ``NA`` and ``W`` are the legend's missing
  markers. "Release Date" and "Next Release Date" are read into ``meta``.
  ``Last-Modified`` is the file's time, hours before the page went public
  (18 Nov 2026): never a publication time.
* H.10: ``D-MON-YY`` dates (``3-JAN-00``; the 1990s file ``1-Jan-90`` with
  padding), two-digit years read from 1970 to 2069; ``ND`` is missing; rates
  to four decimals, rupees per US dollar at noon in New York (certified by
  the New York Fed for customs purposes): not FBIL's Mumbai fixing.
* BIS: v1 CSV repeats every attribute on every row; v2 is SDMX-CSV 2.0
  (``STRUCTURE, STRUCTURE_ID, ACTION`` first); a missing value is ``NaN``
  with ``OBS_STATUS`` ``M`` (India's daily policy rate has a row for every
  calendar day, ``NaN`` on weekends and holidays). Only WS_CBPOL (v1 and v2)
  and WS_XRU (v1) were sampled; the v2 WS_XRU layout is refused. ETags change
  on every request and ``If-None-Match`` is ignored: no conditional request;
  ask for a short period (``startPeriod``, ``lastNObservations``).

**When a value was public** (``macro_base.PublicFrom``, India time without a
zone). A value first seen by a poll is dated by that sighting (the store's
job, which also dates by its sighting a value its own earlier read, after
the rule's time, did not hold); these rules date a history read later. Each
is set later than the publication practice recorded for its source, so it is
often late, and early only if a publisher was later than that practice (a
release delayed by more than the rule allows, a closure the rules do not
know):

* Treasury: 23:59:59 New York time on the curve date (usually out by 18:00
  according to a web search; Treasury's own FAQ was not read).
* New York Fed: SOFR, BGCR, TGCR at 10:00 New York time on the next date the
  same series has (published about 08:00 on the next business day; the next
  row is that day, so no US holiday list is needed), 17:00 for a revised
  row (revisions come about 14:30); EFFR and OBFR at 11:00 (published about
  09:00), 17:00 if revised, 23:59:59 for rows before 1 Mar 2016, revised or
  not (the practice then was not checked); the averages and index at 10:00
  on their own date, 17:00 if revised. The latest row of a series has no
  next date: ``time`` is ``None`` and the store uses its first sighting.
* EIA: 23:59:59 New York time on the Thursday after the first Wednesday
  after the price's date, or later: each day from that Monday to that
  Wednesday on which Washington may be closed (``us_closed``: the federal
  holidays, computed from the law's rules, and 24 December, and 26
  December on a Friday, which the government has given off in some years)
  moves the release a working day. Releases come on Wednesdays with prices
  through the day before (one release seen: 18 Nov 2026, 13:03 to 13:27);
  on Thursday 12 Nov 2026 after Veterans Day. In weeks of Christmas on a
  Wednesday the release came on the Friday, by memory (2019, 2024; not
  checked): the rule gives that Friday.
* H.10: 16:15 New York time on the working day after the release that
  carries the rate: weekly on Mondays at 16:15 with rates through the
  Friday before, the next working day after a Monday holiday, so Tuesday
  16:15 in most weeks and Wednesday after a Monday holiday.
* BIS policy rates: 23:59:59 local time (India for India, New York for the
  United States) on the date the rate is in force: known from the central
  bank's announcement, on or before that day, whatever BIS's lag. Before 19
  Dec 1985 BIS's US series is the effective federal funds rate, a statistic
  published the next business day: 23:59:59 New York time on the second
  weekday after its date (one weekday for a holiday); from then to 3 Feb
  1994 the FOMC did not announce its target (markets read changes from the
  Desk's operations; general knowledge, not checked): the same second
  weekday, an assumption. BIS's rupee figure is BIS's own (six decimals, not
  H.10's rate; BIS names no source): public when BIS publishes it, weekly on
  Thursdays with data to the Tuesday before (its 2026-27 calendar; on 18
  Nov 2026 its latest figure was for 10 Nov). It is dated 23:59:59 Basel
  time a week after the first Thursday at least two days after its date,
  two weeks over the year end (no release from 23 Dec 2026 to 7 Jan 2027):
  a rule from one calendar and one observation, so H.10 is the rupee for
  back-tests.
* Index events: 23:59:59 in the provider's zone (London for FTSE Russell,
  New York for J.P. Morgan and Bloomberg) on the announcement date, or the
  last day of its month when only the month is known.

Nothing here reads the clock.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from cr_strat.htmlparse import BlockedPageError, HtmlPage, check_not_blocked, clean_text, parse_html
from cr_strat.macro_base import (
    Observation,
    ObservationSet,
    ParseResult,
    PublicFrom,
    Request,
    SourceError,
    build_url,
    day_period,
    load_json,
    next_weekday,
    number_text,
    text_of,
)
from cr_strat.ustime import KOLKATA, LONDON, NEW_YORK, zone_to_ist

# --- US Treasury ----------------------------------------------------------------------------------------

UST = "ust"
UST_HOST = "home.treasury.gov"
UST_BASE = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv"
UST_CURVES = {"par": "daily_treasury_yield_curve", "real": "daily_treasury_real_yield_curve",
              "bills": "daily_treasury_bill_rates", "long_term": "daily_treasury_long_term_rate",
              "real_long_term": "daily_treasury_real_long_term"}
UST_PAR = "ust_par"
UST_KINDS = tuple(f"ust_{c}" for c in UST_CURVES)
UST_FIRST_YEAR = 1990
UST_PAR_COLUMNS = {"1 Mo": "ust_par_1m", "1.5 Month": "ust_par_1_5m", "2 Mo": "ust_par_2m", "3 Mo": "ust_par_3m",
                   "4 Mo": "ust_par_4m", "6 Mo": "ust_par_6m", "1 Yr": "ust_par_1y", "2 Yr": "ust_par_2y",
                   "3 Yr": "ust_par_3y", "5 Yr": "ust_par_5y", "7 Yr": "ust_par_7y", "10 Yr": "ust_par_10y",
                   "20 Yr": "ust_par_20y", "30 Yr": "ust_par_30y"}
UST_CITATION = "U.S. Department of the Treasury, Daily Treasury Par Yield Curve Rates"
UST_TERMS_URL = "https://home.treasury.gov/subfooter/site-policies-and-notices"
_TWO_DECIMALS = re.compile(r"-?\d+\.\d\d")


def ust_request(year: int, curve: str = "par") -> Request:
    """One year's file of a Treasury curve, at the per-year address Treasury's own page links."""
    if curve not in UST_CURVES:
        raise ValueError(f"curve must be one of {', '.join(UST_CURVES)}, not {curve!r}")
    if isinstance(year, bool) or not isinstance(year, int) or not UST_FIRST_YEAR <= year <= 2100:
        raise ValueError(f"year must be a whole number from {UST_FIRST_YEAR}, not {year!r}")
    url = f"{UST_BASE}/{year}/all?type={UST_CURVES[curve]}&field_tdr_date_value={year}&page&_format=csv"
    return Request(UST, f"ust_{curve}", url, "text/csv", (("year", str(year)), ("curve", curve)))


def _us_date(text: str, where: str) -> dt.date:
    try:
        return dt.datetime.strptime(text.strip(), "%m/%d/%Y").date()
    except ValueError:
        raise SourceError(f"{where}: date {text!r} is not MM/DD/YYYY") from None


def parse_ust_csv(body: bytes, name: str, *, curve: str = "par", year: int | None = None) -> ParseResult:
    """One year's Treasury par curve file (any subset of the known tenors; a blank cell is missing).

    ``year``, if given (the request's), must hold every row. Files of the
    other curves were not sampled and are refused.
    """
    if curve != "par":
        raise SourceError(f"{name}: the Treasury {curve} file's layout was not sampled; refused until one has been "
                          "read and a parser written")
    text = text_of(body, name)
    if text.lstrip()[:1] == "<":
        raise SourceError(f"{name}: a web page, not Treasury's CSV: {text.strip()[:80]!r}")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or not rows[0] or rows[0][0].strip() != "Date":
        raise SourceError(f"{name}: the header does not start with Date ({rows[0][:3] if rows else 'empty file'})")
    header = [h.strip() for h in rows[0]]
    unknown = [h for h in header[1:] if h not in UST_PAR_COLUMNS]
    if unknown:
        raise SourceError(f"{name}: column(s) {unknown} were not sampled in a par curve file; refused")
    if len(set(header)) != len(header):
        raise SourceError(f"{name}: a column appears twice in the header {header}")
    out = ObservationSet(name)
    notes: list[str] = []
    for line, row in enumerate(rows[1:], start=2):
        where = f"{name}, line {line}"
        if not any(cell.strip() for cell in row):
            continue
        if len(row) != len(header):
            raise SourceError(f"{where}: {len(row)} cells under a header of {len(header)}")
        day = _us_date(row[0], where)
        if year is not None and day.year != year:
            raise SourceError(f"{where}: {day} in the file of {year}")
        period = day_period(day)
        for column, cell in zip(header[1:], row[1:], strict=True):
            if not cell.strip():
                continue
            value = number_text(cell, f"{where}, {column}")
            if not _TWO_DECIMALS.fullmatch(value):
                raise SourceError(f"{where}, {column}: {value!r} does not have two decimals, as every value of the "
                                  "files read (2017 to 2026) has: the file was cut short (Treasury writes no newline "
                                  "after its last row), or its format changed; refused")
            out.add(Observation(UST, UST_PAR_COLUMNS[column], *period, value, "percent",
                                dataset="Daily Treasury Par Yield Curve Rates",
                                indicator=f"US Treasury par yield, {column}", area="United States", level="tenor",
                                code=column, label=column), where)
    notes += out.notes()
    return ParseResult(UST, name, UST_PAR, out.observations(), notes, {"columns": ",".join(header[1:])})


# --- New York Fed -------------------------------------------------------------------------------------

NYFED = "nyfed"
NYFED_HOST = "markets.newyorkfed.org"
NYFED_API = "https://markets.newyorkfed.org/api/rates"
NYFED_JSON, NYFED_CSV = "nyfed_json", "nyfed_csv"
NYFED_GROUPS = {"SOFR": "secured", "BGCR": "secured", "TGCR": "secured", "SOFRAI": "secured", "EFFR": "unsecured",
                "OBFR": "unsecured"}
NYFED_NOTICE = ("The {data} is subject to the Terms of Use posted at newyorkfed.org. The New York Fed is not "
                "responsible for publication of the {data} by {publisher}, does not sanction or endorse any particular "
                "republication, and has no liability for your use.")
NYFED_TERMS_URL = "https://www.newyorkfed.org/privacy/termsofuse"
_RATE_FIELDS = ("percentRate", "percentPercentile1", "percentPercentile25", "percentPercentile75",
                "percentPercentile99", "volumeInBillions")
_NYFED_LAYOUTS = {  # every sampled set of fields of a row, by rate type; footnoteId may be added to any
    "SOFR": [frozenset({"effectiveDate", "type", "revisionIndicator", *_RATE_FIELDS})],
    "SOFRAI": [frozenset({"effectiveDate", "type", "revisionIndicator", "average30day", "average90day",
                          "average180day", "index"})],
    "EFFR": [frozenset({"effectiveDate", "type", "revisionIndicator", *_RATE_FIELDS, "targetRateFrom",
                        "targetRateTo"}),
             frozenset({"effectiveDate", "type", "revisionIndicator", "percentRate", "intraDayLow", "intraDayHigh",
                        "stdDeviation", "targetRateFrom", "targetRateTo"}),
             frozenset({"effectiveDate", "type", "revisionIndicator", "percentRate", "intraDayLow", "intraDayHigh",
                        "stdDeviation", "targetRateFrom"})],
}
_NYFED_LAYOUTS["BGCR"] = _NYFED_LAYOUTS["TGCR"] = _NYFED_LAYOUTS["OBFR"] = _NYFED_LAYOUTS["SOFR"]
_NYFED_VALUES = {  # field -> (series suffix or full id, unit)
    "percentRate": ("", "percent"), "percentPercentile1": ("_p1", "percent"),
    "percentPercentile25": ("_p25", "percent"), "percentPercentile75": ("_p75", "percent"),
    "percentPercentile99": ("_p99", "percent"), "volumeInBillions": ("_volume_bn", "US$ billion"),
    "intraDayLow": ("_intraday_low", "percent"), "intraDayHigh": ("_intraday_high", "percent"),
    "stdDeviation": ("_std_dev", "percent"), "targetRateFrom": ("fomc_target_low", "percent"),
    "targetRateTo": ("fomc_target_high", "percent"), "average30day": ("sofr_avg_30d", "percent"),
    "average90day": ("sofr_avg_90d", "percent"), "average180day": ("sofr_avg_180d", "percent"),
    "index": ("sofr_index", "index, 2 Apr 2018 = 1"),
}
_NYFED_FULL_IDS = {"targetRateFrom", "targetRateTo", "average30day", "average90day", "average180day", "index"}
_NYFED_NAMES = {"SOFR": "Secured Overnight Financing Rate", "BGCR": "Broad General Collateral Rate",
                "TGCR": "Tri-Party General Collateral Rate", "EFFR": "Effective Federal Funds Rate",
                "OBFR": "Overnight Bank Funding Rate", "SOFRAI": "SOFR Averages and Index"}
NYFED_MISSING = frozenset({"NA"})


def nyfed_search_request(group: str, start: dt.date, end: dt.date) -> Request:
    """Every rate of one group (``secured``: SOFR, BGCR, TGCR, the SOFR averages and index; ``unsecured``: EFFR,
    OBFR) from ``start`` to ``end``, as JSON."""
    if group not in ("secured", "unsecured"):
        raise ValueError(f"group must be secured or unsecured, not {group!r}")
    if not isinstance(start, dt.date) or not isinstance(end, dt.date) or start > end:
        raise ValueError(f"start and end must be dates with start <= end, not {start!r}, {end!r}")
    url, query = build_url(f"{NYFED_API}/{group}/all/search.json",
                           [("startDate", start.isoformat()), ("endDate", end.isoformat())])
    return Request(NYFED, NYFED_JSON, url, "application/json", query)


def nyfed_latest_request() -> Request:
    """The latest value of every reference rate, as JSON."""
    return Request(NYFED, NYFED_JSON, f"{NYFED_API}/all/latest.json", "application/json")


def nyfed_last_request(rate: str = "sofr", n: int = 5, fmt: str = "json") -> Request:
    """The last ``n`` values of one rate (``sofr``, ``bgcr``, ``tgcr``, ``effr``, ``obfr``), as JSON or CSV."""
    group = NYFED_GROUPS.get(rate.upper()) if isinstance(rate, str) and rate.upper() != "SOFRAI" else None
    if group is None:
        raise ValueError(f"rate must be one of sofr, bgcr, tgcr, effr, obfr, not {rate!r}")
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 1000:
        raise ValueError(f"n must be a whole number from 1 to 1000, not {n!r}")
    if fmt not in ("json", "csv"):
        raise ValueError(f"fmt must be json or csv, not {fmt!r}")
    return Request(NYFED, NYFED_JSON if fmt == "json" else NYFED_CSV,
                   f"{NYFED_API}/{group}/{rate.lower()}/last/{n}.{fmt}",
                   "application/json" if fmt == "json" else "text/csv")


def _nyfed_value(rate: str, day: dt.date, field: str, value: object, where: str, revision: str, footnote: str,
                 out: ObservationSet) -> None:
    if value is None or (isinstance(value, str) and value.strip() in NYFED_MISSING) or value == "":
        return
    text = number_text(value, f"{where}, {field}")
    suffix, unit = _NYFED_VALUES[field]
    sid = suffix if field in _NYFED_FULL_IDS else f"{rate.lower()}{suffix}"
    name = _NYFED_NAMES[rate]
    out.add(Observation(NYFED, sid, *day_period(day), text, unit, dataset=f"New York Fed reference rates ({rate})",
                        indicator=f"{name}, {field}", area="United States", level=rate, code=field, label=name,
                        revision=revision, footnote=footnote), where)


def parse_nyfed_json(body: bytes, name: str) -> ParseResult:
    """``{"refRates": [...]}`` from the Markets Data API: every rate, percentile, volume, target and average."""
    doc = load_json(body, name)
    rows = doc.get("refRates") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        raise SourceError(f"{name}: no refRates list: not a New York Fed rates answer")
    out = ObservationSet(name)
    revised = 0
    for i, row in enumerate(rows, start=1):
        where = f"{name}, refRates row {i}"
        if not isinstance(row, dict):
            raise SourceError(f"{where}: not an object")
        rate = row.get("type")
        if rate not in _NYFED_LAYOUTS:
            raise SourceError(f"{where}: rate type {rate!r} was not sampled; refused")
        keys = frozenset(row) - {"footnoteId"}
        if keys not in _NYFED_LAYOUTS[rate]:
            raise SourceError(f"{where}: a {rate} row with fields {sorted(row)}, a layout not sampled; refused")
        try:
            day = dt.date.fromisoformat(str(row["effectiveDate"]))
        except ValueError:
            raise SourceError(f"{where}: effectiveDate {row['effectiveDate']!r} is not YYYY-MM-DD") from None
        revision = str(row.get("revisionIndicator") or "").strip()
        if revision not in ("", "r"):
            raise SourceError(f"{where}: revisionIndicator {revision!r} is not empty or r")
        revised += revision == "r"
        footnote = str(row.get("footnoteId") or "").strip()
        for field in sorted(keys - {"effectiveDate", "type", "revisionIndicator"}):
            _nyfed_value(rate, day, field, row[field], where, revision, footnote, out)
    notes = out.notes() + ([f"{revised} rows marked revised (r)"] if revised else [])
    return ParseResult(NYFED, name, NYFED_JSON, out.observations(), notes, {"rows": str(len(rows))})


NYFED_CSV_COLUMNS = ("Effective Date", "Rate Type", "Rate (%)", "1st Percentile (%)", "25th Percentile (%)",
                     "75th Percentile (%)", "99th Percentile (%)", "Volume ($Billions)", "Target Rate From (%)",
                     "Target Rate To (%)", "Intra Day - Low (%)", "Intra Day - High (%)", "Standard Deviation (%)",
                     "30-Day Average SOFR", "90-Day Average SOFR", "180-Day Average SOFR", "SOFR Index",
                     "Revision Indicator (Y/N)", "Footnote ID")
_NYFED_CSV_FIELDS = dict(zip(NYFED_CSV_COLUMNS[2:17], ("percentRate", "percentPercentile1", "percentPercentile25",
                                                       "percentPercentile75", "percentPercentile99", "volumeInBillions",
                                                       "targetRateFrom", "targetRateTo", "intraDayLow", "intraDayHigh",
                                                       "stdDeviation", "average30day", "average90day",
                                                       "average180day", "index"), strict=True))


def parse_nyfed_csv(body: bytes, name: str) -> ParseResult:
    """The API's CSV (one union header for every rate; blank cells for fields a rate lacks)."""
    text = text_of(body, name)
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or tuple(h.strip() for h in rows[0]) != NYFED_CSV_COLUMNS:
        raise SourceError(f"{name}: not the New York Fed's CSV header (a layout not sampled): "
                          f"{rows[0][:4] if rows else 'empty file'}")
    out = ObservationSet(name)
    for line, row in enumerate(rows[1:], start=2):
        where = f"{name}, line {line}"
        if not any(c.strip() for c in row):
            continue
        if len(row) != len(NYFED_CSV_COLUMNS):
            raise SourceError(f"{where}: {len(row)} cells under a header of {len(NYFED_CSV_COLUMNS)}")
        cells = dict(zip(NYFED_CSV_COLUMNS, (c.strip() for c in row), strict=True))
        rate = cells["Rate Type"]
        if rate not in _NYFED_LAYOUTS:
            raise SourceError(f"{where}: rate type {rate!r} was not sampled; refused")
        day = _us_date(cells["Effective Date"], where)
        flag = cells["Revision Indicator (Y/N)"]
        if flag not in ("", "Y", "N", "r"):
            raise SourceError(f"{where}: revision indicator {flag!r} is not blank, Y, N or r")
        revision = "r" if flag in ("Y", "r") else ""
        for column, field in _NYFED_CSV_FIELDS.items():
            _nyfed_value(rate, day, field, cells[column], where, revision, cells["Footnote ID"], out)
    return ParseResult(NYFED, name, NYFED_CSV, out.observations(), out.notes(), {"rows": str(len(rows) - 1)})


# --- EIA ----------------------------------------------------------------------------------------------

EIA = "eia"
EIA_HOST = "www.eia.gov"
EIA_BRENT = "eia_brent"
EIA_BRENT_URL = "https://www.eia.gov/dnav/pet/hist/RBRTED.htm"
EIA_CITATION = "Source: U.S. Energy Information Administration ({date}), Europe Brent Spot Price FOB (RBRTE)"
EIA_TERMS_URL = "https://www.eia.gov/about/copyrights_reuse.php"
EIA_MISSING = frozenset({"", "-", "--", "NA", "W"})  # the page's legend; an empty cell is a day without a price
_EIA_WEEK = re.compile(r"(\d{4}) ([A-Z][a-z]{2})- ?(\d{1,2}) to ([A-Z][a-z]{2})- ?(\d{1,2})")
_MONTH_ABBR = {m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
                                           "Nov", "Dec"), start=1)}


def eia_brent_request() -> Request:
    """EIA's daily Brent history page (a weekly grid); conditional requests work (ETag, Last-Modified)."""
    return Request(EIA, EIA_BRENT, EIA_BRENT_URL, "text/html")


def _page(body: bytes, name: str) -> HtmlPage:
    """A history page, refused if it is a refusal page or does not end with ``</html>``: an answer cut short (a
    saved file of an interrupted download) would otherwise read as complete, its last value cut mid-way."""
    text = text_of(body, name)
    page = parse_html(text)
    try:
        check_not_blocked(page, name)
    except BlockedPageError as exc:
        raise SourceError(str(exc)) from None
    if "</html>" not in text[-2000:].lower():
        raise SourceError(f"{name}: the page does not end with </html>: it was cut short (an interrupted download?); "
                          "refused rather than read in part")
    return page


def _week(label: str, where: str) -> dt.date:
    m = _EIA_WEEK.fullmatch(clean_text(label))
    if m is None:
        raise SourceError(f"{where}: week label {label!r} is not like '2026 Nov- 9 to Nov-13'")
    year, mon1, day1, mon2, day2 = m.groups()
    if mon1 not in _MONTH_ABBR or mon2 not in _MONTH_ABBR:
        raise SourceError(f"{where}: week label {label!r} names an unknown month")
    try:
        monday = dt.date(int(year), _MONTH_ABBR[mon1], int(day1))
    except ValueError:
        raise SourceError(f"{where}: week label {label!r} is not a date") from None
    friday = monday + dt.timedelta(days=4)
    if monday.weekday() != 0 or (friday.month, friday.day) != (_MONTH_ABBR[mon2], int(day2)):
        raise SourceError(f"{where}: week label {label!r} is not a Monday to the Friday after it")
    return monday


def parse_eia_history(body: bytes, name: str) -> ParseResult:
    """EIA's Brent daily history page: one value per day, the unit checked, the release dates in ``meta``."""
    page = _page(body, name)
    if "brent spot price" not in page.title.lower() or "dollars per barrel" not in page.title.lower():
        raise SourceError(f"{name}: the page title {page.title!r} is not Europe Brent Spot Price FOB (Dollars per "
                          "Barrel): another page, or the unit changed")
    tables = [t for t in page.tables if t.rows and [c.text for c in t.rows[0]] == ["Week Of", "Mon", "Tue", "Wed",
                                                                                  "Thu", "Fri"]]
    if len(tables) != 1:
        raise SourceError(f"{name}: {len(tables)} tables with the header Week Of, Mon to Fri; one was expected")
    out = ObservationSet(name)
    previous = None
    markers = 0
    for r, row in enumerate(tables[0].rows[1:], start=2):
        where = f"{name}, table row {r}"
        cells = [c.text for c in row]
        if not any(cells):
            continue  # the spacer after every fifth week
        if len(cells) != 6:
            raise SourceError(f"{where}: {len(cells)} cells, not a week label and five days")
        monday = _week(cells[0], where)
        if previous is not None and monday <= previous:
            raise SourceError(f"{where}: the week of {monday} does not follow the week of {previous}")
        previous = monday
        for offset, cell in enumerate(cells[1:]):
            if cell in EIA_MISSING:
                markers += cell not in ("",)
                continue
            day = monday + dt.timedelta(days=offset)
            out.add(Observation(EIA, "brent_spot_usd_bbl", *day_period(day), number_text(cell, f"{where}, {day}"),
                                "US dollars per barrel", dataset="EIA Spot Prices (RBRTE, daily)",
                                indicator="Europe Brent Spot Price FOB", area="Europe", level="spot",
                                code="RBRTE", label="Europe Brent Spot Price FOB"), where)
    meta = {"rows": str(len(out.rows))}
    for key, label in (("release_date", "Release Date"), ("next_release_date", "Next Release Date")):
        m = re.search(rf"(?<!Next ){label}:\s*(\d{{1,2}}/\d{{1,2}}/\d{{4}})", page.text)
        if m:
            meta[key] = _us_date(m.group(1), f"{name}, {label}").isoformat()
    notes = out.notes() + ([f"{markers} cells held a legend marker (-, --, NA, W): missing"] if markers else [])
    return ParseResult(EIA, name, EIA_BRENT, out.observations(), notes, meta)


# --- Federal Reserve H.10 -----------------------------------------------------------------------------

H10 = "h10"
H10_HOST = "www.federalreserve.gov"
H10_HISTORY, H10_DDP = "h10_history", "h10_ddp"
H10_FILES = {"dat00_in": "2000 to the latest release", "dat96_in": "1990 to 1999"}
H10_DDP_SERIES = "60f32914ab61dfab590e0e470153e3ae"  # the "H.10 Statistical Release - Daily rates" package
H10_DDP_ID = "H10/H10/RXI_N.B.IN"
H10_CITATION = "Board of Governors of the Federal Reserve System, H.10 Foreign Exchange Rates (Indian rupee)"
H10_TERMS_URL = "https://www.federalreserve.gov/disclaimer.htm"
_H10_DATE = re.compile(r"(\d{1,2})-([A-Za-z]{3})-(\d{2})")


def h10_history_request(file: str = "dat00_in") -> Request:
    """An H.10 history page for India (``dat00_in``: 2000 on; ``dat96_in``: the 1990s)."""
    if file not in H10_FILES:
        raise ValueError(f"file must be one of {', '.join(H10_FILES)}, not {file!r}")
    return Request(H10, H10_HISTORY, f"https://www.federalreserve.gov/releases/h10/hist/{file}.htm", "text/html")


def h10_ddp_request(*, last_obs: int | None = None, start: dt.date | None = None,
                    end: dt.date | None = None) -> Request:
    """The Data Download Program's daily-rates package as CSV (to be removed by the Board on 31 Mar 2027)."""
    if last_obs is not None and (isinstance(last_obs, bool) or not isinstance(last_obs, int) or last_obs < 1):
        raise ValueError(f"last_obs must be a positive whole number, not {last_obs!r}")
    if start is not None and end is not None and start > end:
        raise ValueError(f"start {start} is after end {end}")
    query = (("rel", "H10"), ("series", H10_DDP_SERIES), ("lastobs", "" if last_obs is None else str(last_obs)),
             ("from", "" if start is None else start.strftime("%m/%d/%Y")),
             ("to", "" if end is None else end.strftime("%m/%d/%Y")), ("filetype", "csv"), ("label", "include"),
             ("layout", "seriescolumn"), ("type", "package"))
    url = "https://www.federalreserve.gov/datadownload/Output.aspx?" + "&".join(f"{k}={v}" for k, v in query)
    return Request(H10, H10_DDP, url, "text/csv", query)


def _h10_date(text: str, where: str) -> dt.date:
    m = _H10_DATE.fullmatch(clean_text(text))
    if m is None or m.group(2).title() not in _MONTH_ABBR:
        raise SourceError(f"{where}: date {text!r} is not like 3-JAN-00")
    yy = int(m.group(3))
    try:
        return dt.date(1900 + yy if yy >= 70 else 2000 + yy, _MONTH_ABBR[m.group(2).title()], int(m.group(1)))
    except ValueError:
        raise SourceError(f"{where}: {text!r} is not a date") from None


def parse_h10_history(body: bytes, name: str) -> ParseResult:
    """An H.10 history page of India's rupee rate (both date styles; ``ND`` missing); the release date in ``meta``."""
    page = _page(body, name)
    text = page.text.lower()
    if "indian rupees per u.s. dollar" not in text and "rupees/us$" not in text:
        raise SourceError(f"{name}: no 'Indian rupees per U.S. dollar' (or 'RUPEES/US$') on the page: another "
                          "currency, or the unit changed")
    tables = [t for t in page.tables if t.text[:9].lower() == "date rate"]
    if len(tables) != 1:
        raise SourceError(f"{name}: {len(tables)} tables headed Date, Rate; one was expected")
    out = ObservationSet(name)
    previous = None
    missing = 0
    for r, row in enumerate(tables[0].rows, start=1):
        cells = [c.text for c in row]
        if cells in (["Date", "Rate"], []):
            continue
        where = f"{name}, table row {r}"
        if len(cells) != 2:
            raise SourceError(f"{where}: {len(cells)} cells, not a date and a rate")
        day = _h10_date(cells[0], where)
        if previous is not None and day <= previous:
            raise SourceError(f"{where}: {day} does not follow {previous}")
        previous = day
        if cells[1] == "ND":
            missing += 1
            continue
        out.add(Observation(H10, "usdinr_h10_noon", *day_period(day), number_text(cells[1], f"{where}, rate"),
                            "rupees per US dollar", dataset="Federal Reserve H.10, historical rates for India",
                            indicator="Indian rupees per U.S. dollar, noon buying rate in New York",
                            area="India", level="spot", code="RXI_N.B.IN", label="Indian rupee"), where)
    meta = {"rows": str(len(out.rows) + missing)}
    m = re.search(r"Release Date:\s*(?:[A-Za-z]+,\s*)?([A-Za-z]+ \d{1,2}, \d{4})", page.text)
    if m:
        try:
            meta["release_date"] = dt.datetime.strptime(m.group(1), "%B %d, %Y").date().isoformat()
        except ValueError:
            pass
    notes = out.notes() + ([f"{missing} days marked ND (no data): missing"] if missing else [])
    return ParseResult(H10, name, H10_HISTORY, out.observations(), notes, meta)


_DDP_LABELS = ("Series Description", "Unit:", "Multiplier:", "Currency:", "Unique Identifier:", "Time Period")


def parse_h10_ddp_csv(body: bytes, name: str) -> ParseResult:
    """The Data Download Program package CSV: India's column (``H10/H10/RXI_N.B.IN``), its unit checked."""
    rows = list(csv.reader(io.StringIO(text_of(body, name))))
    if len(rows) < 6 or tuple(r[0].strip() for r in rows[:6]) != _DDP_LABELS:
        raise SourceError(f"{name}: not a Data Download Program CSV (six header lines {list(_DDP_LABELS)})")
    ids = [c.strip() for c in rows[4]]
    if ids.count(H10_DDP_ID) != 1:
        raise SourceError(f"{name}: no single column {H10_DDP_ID}")
    j = ids.index(H10_DDP_ID)
    for line, want in ((1, "Currency"), (2, "1"), (3, "INR")):
        if rows[line][j].strip() != want:
            raise SourceError(f"{name}, line {line + 1}: {_DDP_LABELS[line]} {rows[line][j]!r} for {H10_DDP_ID}, not "
                              f"{want!r}: the unit changed")
    out = ObservationSet(name)
    for line, row in enumerate(rows[6:], start=7):
        where = f"{name}, line {line}"
        if not any(c.strip() for c in row):
            continue
        try:
            day = dt.date.fromisoformat(row[0].strip())
        except ValueError:
            raise SourceError(f"{where}: date {row[0]!r} is not YYYY-MM-DD") from None
        if len(row) != len(ids):  # a row cut short (the file ends without a newline) would end in a cut value
            raise SourceError(f"{where}: {len(row)} cells under a header of {len(ids)}: the file was cut short, or "
                              "its layout changed; refused")
        cell = row[j].strip()
        if cell == "ND":
            continue
        out.add(Observation(H10, "usdinr_h10_noon", *day_period(day), number_text(cell, f"{where}, rate"),
                            "rupees per US dollar", dataset="Federal Reserve H.10, Data Download Program",
                            indicator="Indian rupees per U.S. dollar, noon buying rate in New York", area="India",
                            level="spot", code="RXI_N.B.IN", label="Indian rupee"), where)
    return ParseResult(H10, name, H10_DDP, out.observations(), out.notes(), {"rows": str(len(rows) - 6)})


# --- BIS ------------------------------------------------------------------------------------------------

BIS = "bis"
BIS_HOST = "stats.bis.org"
BIS_CBPOL, BIS_XRU, BIS_REL_CAL = "bis_cbpol", "bis_xru", "bis_release_calendar"
BIS_V2_CSV = "application/vnd.sdmx.data+csv;version=2.0.0"
BIS_CITATION = "Bank for International Settlements, {dataset}, data.bis.org"
BIS_TERMS_URL = "https://data.bis.org/help/legal"
_BIS_KEYS = {"WS_CBPOL": re.compile(r"D\.[A-Z]{2}(?:\+[A-Z]{2})*"), "WS_XRU": re.compile(r"D\.IN\.(?:INR)?\.(?:A)?")}
_BIS_KIND = {"WS_CBPOL": BIS_CBPOL, "WS_XRU": BIS_XRU}


def bis_request(dataflow: str = "WS_CBPOL", key: str = "D.IN+US", *, last_n: int | None = None,
                start_period: dt.date | None = None, version: str | None = None) -> Request:
    """BIS data as CSV: v2 (SDMX-CSV 2.0, the API the terms ask users to prefer) for WS_CBPOL, v1 for WS_XRU (its
    v2 layout was not sampled). ``last_n`` (v2) or ``start_period`` keep the answer small."""
    if dataflow not in _BIS_KEYS:
        raise ValueError(f"dataflow must be WS_CBPOL or WS_XRU (the two sampled), not {dataflow!r}")
    if not _BIS_KEYS[dataflow].fullmatch(key):
        raise ValueError(f"key {key!r} does not fit {dataflow} (daily: D.IN+US for policy rates, D.IN.. or "
                         "D.IN.INR.A for the rupee)")
    version = version or ("v2" if dataflow == "WS_CBPOL" else "v1")
    if version not in ("v1", "v2") or (dataflow == "WS_XRU" and version == "v2"):
        raise ValueError(f"version {version!r} is not available for {dataflow} here (WS_XRU: v1 only)")
    if last_n is not None and (isinstance(last_n, bool) or not isinstance(last_n, int) or not 1 <= last_n <= 10000):
        raise ValueError(f"last_n must be a whole number from 1 to 10000, not {last_n!r}")
    if last_n is not None and version == "v1":
        raise ValueError("last_n is a v2 parameter (lastNObservations); use start_period with v1")
    start = None if start_period is None else start_period.isoformat()
    if version == "v2":
        url, query = build_url(f"https://stats.bis.org/api/v2/data/dataflow/BIS/{dataflow}/1.0/{key}",
                               [("lastNObservations", last_n), ("startPeriod", start)])
        return Request(BIS, _BIS_KIND[dataflow], url, BIS_V2_CSV, query)
    url, query = build_url(f"https://stats.bis.org/api/v1/data/{dataflow}/{key}/all",
                           [("format", "csv"), ("startPeriod", start)])
    return Request(BIS, _BIS_KIND[dataflow], url, "text/csv", query)


def bis_release_calendar_request(start_period: str) -> Request:
    """BIS's release calendar (``BIS_REL_CAL``) from a month (``2026-10``), as v1 CSV."""
    if not re.fullmatch(r"\d{4}-\d{2}(?:-\d{2})?", str(start_period)):
        raise ValueError(f"start_period must be YYYY-MM or YYYY-MM-DD, not {start_period!r}")
    url, query = build_url("https://stats.bis.org/api/v1/data/BIS_REL_CAL/all/all",
                           [("format", "csv"), ("startPeriod", start_period)])
    return Request(BIS, BIS_REL_CAL, url, "text/csv", query)


_BIS_REQUIRED = {"WS_CBPOL": ("FREQ", "REF_AREA", "UNIT_MEASURE", "UNIT_MULT", "TIME_PERIOD", "OBS_VALUE",
                              "OBS_STATUS"),
                 "WS_XRU": ("FREQ", "REF_AREA", "CURRENCY", "COLLECTION", "UNIT_MULT", "TIME_PERIOD", "OBS_VALUE",
                            "OBS_STATUS")}


def parse_bis_csv(body: bytes, name: str, *, dataflow: str | None = None) -> ParseResult:
    """BIS policy rates (WS_CBPOL, v1 or v2 CSV) or the daily rupee rate (WS_XRU, v1 CSV), refused if anything is off.

    ``NaN`` with ``OBS_STATUS`` ``M`` is missing; any other ``NaN``, or a
    number marked ``M``, is refused. Daily data only; the unit is checked.
    """
    text = text_of(body, name)
    if text.lstrip()[:1] == "<":
        raise SourceError(f"{name}: XML or a web page, not BIS CSV: {text.strip()[:80]!r}")
    if text and not text.endswith("\n"):
        raise SourceError(f"{name}: no newline at the end, as every BIS answer read has: the answer was cut short (an "
                          "interrupted download?); refused rather than read in part")
    reader = csv.DictReader(io.StringIO(text))
    columns = reader.fieldnames or []
    v2 = columns[:3] == ["STRUCTURE", "STRUCTURE_ID", "ACTION"]
    rows = list(reader)
    for line, row in enumerate(rows, start=2):
        if None in row or any(value is None for value in row.values()):
            raise SourceError(f"{name}, line {line}: the row's cells do not match the header's {len(columns)} (a row "
                              "cut short, or a layout not sampled); refused")
    if v2:
        flows = {r.get("STRUCTURE_ID") for r in rows}
        found = {f.split(":")[-1].split("(")[0] for f in flows if f}
        if len(found) > 1:
            raise SourceError(f"{name}: rows of several dataflows {sorted(found)}")
        flow = found.pop() if found else (dataflow or "")
        if flow == "WS_XRU":
            raise SourceError(f"{name}: WS_XRU in SDMX-CSV 2.0 was not sampled; refused (ask v1)")
    else:
        flow = "WS_XRU" if "CURRENCY" in columns else ("WS_CBPOL" if "UNIT_MEASURE" in columns else "")
    if flow not in _BIS_REQUIRED or (dataflow is not None and flow != dataflow):
        raise SourceError(f"{name}: dataflow {flow or 'unknown'!r} (columns {columns[:6]}) is not "
                          f"{dataflow or 'WS_CBPOL or WS_XRU'}")
    missing_columns = [c for c in _BIS_REQUIRED[flow] if c not in columns]
    if missing_columns:
        raise SourceError(f"{name}: columns {missing_columns} are missing")
    out = ObservationSet(name)
    missing = 0
    for line, row in enumerate(rows, start=2):
        where = f"{name}, line {line}"
        if v2 and (row["STRUCTURE"] != "dataflow" or row["ACTION"] != "I"):
            raise SourceError(f"{where}: STRUCTURE {row['STRUCTURE']!r} / ACTION {row['ACTION']!r}; only dataflow "
                              "rows with action I were sampled")
        if row["FREQ"] != "D":
            raise SourceError(f"{where}: frequency {row['FREQ']!r}; only daily (D) was sampled")
        if row["UNIT_MULT"] != "0":
            raise SourceError(f"{where}: UNIT_MULT {row['UNIT_MULT']!r}, not 0: the unit changed")
        area = row["REF_AREA"]
        if not re.fullmatch(r"[A-Z]{2}", area or ""):
            raise SourceError(f"{where}: REF_AREA {area!r} is not a two-letter code")
        if flow == "WS_CBPOL":
            if row["UNIT_MEASURE"] != "368":
                raise SourceError(f"{where}: UNIT_MEASURE {row['UNIT_MEASURE']!r}, not 368 (per cent per year): "
                                  "the unit changed")
            sid, unit, dataset = f"policy_rate_{area.lower()}_bis", "percent per annum", "WS_CBPOL"
            indicator = f"Central bank policy rate, {area} (BIS)"
        else:
            if (area, row["CURRENCY"], row["COLLECTION"]) != ("IN", "INR", "A"):
                raise SourceError(f"{where}: {area} {row['CURRENCY']} collection {row['COLLECTION']}; only India's "
                                  "rupee, average of observations (IN, INR, A), was sampled")
            sid, unit, dataset = "usdinr_bis_daily", "rupees per US dollar", "WS_XRU"
            indicator = "Indian rupees per US dollar, daily, average of observations (BIS)"
        try:
            day = dt.date.fromisoformat(row["TIME_PERIOD"])
        except (TypeError, ValueError):
            raise SourceError(f"{where}: TIME_PERIOD {row['TIME_PERIOD']!r} is not a daily date") from None
        value, status = (row["OBS_VALUE"] or "").strip(), (row["OBS_STATUS"] or "").strip()
        if value == "NaN" or value == "":
            if status != "M":
                raise SourceError(f"{where}: no value ({value!r}) with OBS_STATUS {status!r}, not M")
            missing += 1
            continue
        if status == "M":
            raise SourceError(f"{where}: a value {value!r} marked missing (OBS_STATUS M)")
        out.add(Observation(BIS, sid, *day_period(day), number_text(value, f"{where}, OBS_VALUE"), unit,
                            dataset=f"BIS {dataset}", indicator=indicator, area=area, level="daily",
                            code=f"{dataset} {row['FREQ']}.{area}", label=(row.get("TITLE") or "").strip(),
                            status=status, notes=(row.get("COMPILATION") or "").strip()), where)
    notes = out.notes() + ([f"{missing} days with no value (NaN, OBS_STATUS M): missing"] if missing else [])
    layout = f"{'v2' if v2 else 'v1'}_{flow}"
    return ParseResult(BIS, name, layout, out.observations(), notes, {"rows": str(len(rows))})


def parse_bis_release_calendar(body: bytes, name: str) -> list[dict[str, str]]:
    """BIS's release calendar: ``category``, ``release_type``, ``period`` and ``release_date`` (ISO) per row."""
    reader = csv.DictReader(io.StringIO(text_of(body, name)))
    need = ("FREQ", "CATEGORY", "RELEASE_TYPE", "TIME_PERIOD", "OBS_VALUE")
    if any(c not in (reader.fieldnames or []) for c in need):
        raise SourceError(f"{name}: not BIS's release calendar (columns {need} needed)")
    out = []
    for line, row in enumerate(reader, start=2):
        value = (row["OBS_VALUE"] or "").strip()
        if not re.fullmatch(r"\d{8}", value):
            raise SourceError(f"{name}, line {line}: release date {value!r} is not YYYYMMDD")
        try:
            day = dt.date(int(value[:4]), int(value[4:6]), int(value[6:]))
        except ValueError:
            raise SourceError(f"{name}, line {line}: {value!r} is not a date") from None
        out.append({"category": row["CATEGORY"], "release_type": row["RELEASE_TYPE"], "period": row["TIME_PERIOD"],
                    "release_date": day.isoformat()})
    return out


# --- When a value was public ----------------------------------------------------------------------------

_END_OF_DAY = dt.time(23, 59, 59)


def _ny(day: dt.date, time: dt.time, rule: str) -> PublicFrom:
    return PublicFrom(zone_to_ist(NEW_YORK, day, time), "publication_rule", rule)


# US federal holidays (5 U.S.C. 6103), computed from the law's rules, and the days the federal government has closed
# by executive order in some years: the EIA and H.10 rules move past them. The Fed Board and EIA are in Washington.
_FIXED_HOLIDAYS = ((1, 1, 1900), (6, 19, 2021), (7, 4, 1900), (11, 11, 1978), (12, 25, 1900))  # month, day, from year
_MONDAY_HOLIDAYS = ((1, 3, 1986), (2, 3, 1971), (5, -1, 1971), (9, 1, 1900), (10, 2, 1971))  # month, n-th Monday, from


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    """The ``n``-th ``weekday`` (0 Monday) of a month; ``n = -1`` the last one."""
    if n == -1:
        last = dt.date(year + month // 12, month % 12 + 1, 1) - dt.timedelta(days=1)
        return last - dt.timedelta(days=(last.weekday() - weekday) % 7)
    first = dt.date(year, month, 1)
    return first + dt.timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _observed(day: dt.date) -> dt.date:
    """A fixed-date holiday as observed: the Friday before a Saturday, the Monday after a Sunday."""
    return day - dt.timedelta(days=1) if day.weekday() == 5 else day + dt.timedelta(days=1) if day.weekday() == 6 \
        else day


def us_federal_holidays(year: int) -> frozenset[dt.date]:
    """The US federal holidays observed in ``year`` (New Year's Day of the next year included when it is observed on
    31 December), and Inauguration Day (1 in 4 years, a holiday in Washington): computed from the rules, not a list.

    New Year's Day, Martin Luther King's birthday (third Monday of January, from 1986), Washington's birthday (third
    Monday of February), Memorial Day (last Monday of May), Juneteenth (19 June, from 2021), Independence Day, Labor
    Day (first Monday of September), Columbus Day (second Monday of October), Veterans Day (11 November from 1978;
    the fourth Monday of October 1971 to 1977), Thanksgiving (fourth Thursday of November) and Christmas.
    """
    days = set()
    for month, day, first in _FIXED_HOLIDAYS:
        for y in (year, year + 1):
            if y >= first:
                observed = _observed(dt.date(y, month, day))
                if observed.year == year:
                    days.add(observed)
    for month, n, first in _MONDAY_HOLIDAYS:
        if year >= first:
            days.add(_nth_weekday(year, month, 0, n))
    if 1971 <= year < 1978:
        days.add(_nth_weekday(year, 10, 0, 4))
    days.add(_nth_weekday(year, 11, 3, 4))
    if year % 4 == 1:
        inauguration = dt.date(year, 1, 20)
        days.add(inauguration + dt.timedelta(days=1) if inauguration.weekday() == 6 else inauguration)
    return frozenset(d for d in days if d.weekday() < 5)


def us_closed(day: dt.date) -> bool:
    """Whether Washington's federal offices may be closed on ``day``: a weekend, a federal holiday, or a day the
    government has given off by executive order in some years (24 December on a weekday, 26 December on a Friday):
    counted as closed every year, so that a rule built on it is late rather than early."""
    if day.weekday() >= 5 or day in us_federal_holidays(day.year):
        return True
    return (day.month, day.day) == (12, 24) or ((day.month, day.day) == (12, 26) and day.weekday() == 4)


def _next_open(day: dt.date) -> dt.date:
    """The first day after ``day`` that is not ``us_closed``."""
    day += dt.timedelta(days=1)
    while us_closed(day):
        day += dt.timedelta(days=1)
    return day


def ust_public_from(day: dt.date) -> PublicFrom:
    """A Treasury curve read later: 23:59:59 New York time on its date."""
    return _ny(day, _END_OF_DAY, "Treasury curve: 23:59:59 New York time on the curve date (usually out by 18:00)")


NYFED_NEW_LAYOUT = dt.date(2016, 3, 1)  # EFFR's percentiles and volume from here; OBFR from here


def nyfed_public_from(rate: str, day: dt.date, next_day: dt.date | None, revised: bool = False) -> PublicFrom:
    """A New York Fed rate read later; ``next_day`` is the next date the same series has (``None`` for the latest).

    A revised row (``r``) is dated after the revision window (about 14:30): 17:00 New York time, on the next business
    day for the rates and on their own date for the averages and index; EFFR and OBFR before 1 Mar 2016, whose
    practice was not checked, at 23:59:59 on the next business day whether revised or not (the later of the two).
    """
    rate = rate.upper()
    if rate == "SOFRAI":
        if revised:
            return _ny(day, dt.time(17, 0), "SOFR averages and index marked revised: 17:00 New York time on their own "
                       "date (revisions come about 14:30)")
        return _ny(day, dt.time(10, 0), "SOFR averages and index: 10:00 New York time on their own date")
    if rate not in NYFED_GROUPS:
        raise ValueError(f"rate {rate!r} is not one of {', '.join(NYFED_GROUPS)}")
    if next_day is None:
        return PublicFrom(None, "first_seen", f"{rate}: the latest row; its next business day is not known yet")
    if next_day <= day:
        raise ValueError(f"next_day {next_day} must come after {day}")
    if rate in ("EFFR", "OBFR") and day < NYFED_NEW_LAYOUT:
        return _ny(next_day, _END_OF_DAY, f"{rate} before 1 Mar 2016: 23:59:59 on the next business day, revised or "
                   "not (the practice then was not checked)")
    if revised:
        return _ny(next_day, dt.time(17, 0), f"{rate} marked revised: 17:00 New York time on the next business day")
    if rate in ("EFFR", "OBFR"):
        return _ny(next_day, dt.time(11, 0), f"{rate}: 11:00 New York time on the next business day (published "
                   "about 09:00)")
    return _ny(next_day, dt.time(10, 0), f"{rate}: 10:00 New York time on the next business day (published about "
               "08:00)")


def nyfed_rate_of(series_id: str) -> str:
    """The New York Fed rate type a series comes from (``sofr_p25`` -> SOFR; ``fomc_target_low`` -> EFFR)."""
    if series_id.startswith(("fomc_target", "effr")):
        return "EFFR"
    if series_id.startswith(("sofr_avg", "sofr_index")):
        return "SOFRAI"
    head = series_id.split("_")[0].upper()
    if head not in NYFED_GROUPS:
        raise ValueError(f"{series_id!r} is not a New York Fed series")
    return head


def nyfed_public_from_all(observations: Iterable[Observation]) -> dict[tuple[str, str], PublicFrom]:
    """``nyfed_public_from`` for every New York Fed observation, the next date taken from the same rate's rows."""
    observations = [o for o in observations if o.source == NYFED]
    days: dict[str, set[dt.date]] = {}
    for obs in observations:
        days.setdefault(nyfed_rate_of(obs.series_id), set()).add(dt.date.fromisoformat(obs.period))
    following: dict[str, dict[dt.date, dt.date | None]] = {}
    for rate, values in days.items():
        ordered = sorted(values)
        following[rate] = dict(zip(ordered, [*ordered[1:], None], strict=True))
    out = {}
    for obs in observations:
        rate = nyfed_rate_of(obs.series_id)
        day = dt.date.fromisoformat(obs.period)
        out[obs.key] = nyfed_public_from(rate, day, following[rate][day], obs.revision == "r")
    return out


def eia_release_day(day: dt.date) -> dt.date:
    """The day EIA's weekly release carries the price of ``day`` by the conservative rule: the first Wednesday after
    it, one working day later for each day from that Monday to that Wednesday on which Washington may be closed
    (``us_closed``): a Monday holiday moves it to Thursday, Christmas on a Wednesday with 24 December off to Friday."""
    wednesday = day + dt.timedelta(days=(2 - day.weekday()) % 7 or 7)
    release = wednesday
    for _ in range(sum(us_closed(wednesday - dt.timedelta(days=k)) for k in (2, 1, 0))):
        release = _next_open(release)
    return release


def eia_public_from(day: dt.date) -> PublicFrom:
    """A Brent price read later: 23:59:59 New York time on the day of the release that carries it
    (``eia_release_day``), and never before the Thursday after that first Wednesday."""
    release = eia_release_day(day)
    thursday = day + dt.timedelta(days=(2 - day.weekday()) % 7 or 7) + dt.timedelta(days=1)
    return _ny(max(release, thursday), _END_OF_DAY, "EIA Brent: 23:59:59 New York time on the Thursday after the "
               "first Wednesday after the price's date (weekly releases), later when US holidays or closures from "
               "that Monday to that Wednesday move the release past Thursday")


def h10_public_from(day: dt.date) -> PublicFrom:
    """An H.10 rate read later: 16:15 New York time on the working day after the release that carries it: the first
    Monday after its date, or the next working day if Washington may be closed that Monday (``us_closed``)."""
    monday = day + dt.timedelta(days=(0 - day.weekday()) % 7 or 7)
    release = monday if not us_closed(monday) else _next_open(monday)
    return _ny(_next_open(release), dt.time(16, 15), "H.10: 16:15 New York time on the working day after the release "
               "(weekly on Mondays, 16:15; the next working day after a Monday holiday) that carries the rate")


US_TARGET_FROM = dt.date(1985, 12, 19)  # BIS's US series: the target's mid-point from here, the effective rate before
US_TARGET_ANNOUNCED = dt.date(1994, 2, 4)  # the FOMC's first announcement of a target change


def _second_weekday_after(day: dt.date) -> dt.date:
    later = day
    for _ in range(2):
        later = next_weekday(later + dt.timedelta(days=1))
    return later


def bis_policy_public_from(area: str, day: dt.date) -> PublicFrom:
    """A BIS policy rate: 23:59:59 local time on the date it is in force (India and the United States).

    BIS's US values before 19 Dec 1985 are the effective federal funds rate, published the next business day: 23:59:59
    New York time on the second weekday after the date (a margin of one weekday for a holiday). From then to 3 Feb
    1994 they are the mid-point of a target the FOMC did not announce (markets read changes from the Desk's
    operations; from general knowledge, not checked): the same second weekday after the date, an assumption.
    """
    zone = {"IN": KOLKATA, "US": NEW_YORK}.get(area.upper())
    if zone is None:
        raise ValueError(f"no local time zone known for {area!r} (India and the United States only)")
    if area.upper() == "US" and day < US_TARGET_FROM:
        return PublicFrom(zone_to_ist(NEW_YORK, _second_weekday_after(day), _END_OF_DAY), "publication_rule",
                          "BIS policy rate, US, before 19 Dec 1985 (the effective federal funds rate, published the "
                          "next business day): 23:59:59 New York time on the second weekday after its date")
    if area.upper() == "US" and day < US_TARGET_ANNOUNCED:
        return PublicFrom(zone_to_ist(NEW_YORK, _second_weekday_after(day), _END_OF_DAY), "publication_rule",
                          "BIS policy rate, US, 19 Dec 1985 to 3 Feb 1994 (a target the FOMC did not announce; markets "
                          "read changes from the Desk's operations): 23:59:59 New York time on the second weekday "
                          "after the date in force, an assumption not checked")
    return PublicFrom(zone_to_ist(zone, day, _END_OF_DAY), "publication_rule",
                      f"BIS policy rate, {area.upper()}: 23:59:59 local time on the date it is in force (announced by "
                      "the central bank on or before that day)")


_CET = dt.timedelta(hours=1)  # Basel in winter; summer time (UTC+2) is not modelled: an hour later then


def bis_fx_release_day(day: dt.date) -> dt.date:
    """The day by which BIS's weekly release has carried its rupee figure of ``day``, by the conservative rule: the
    first Thursday at least two days after it (BIS released XRU on Thursdays in its 2026-27 calendar, with the data
    to the Tuesday before: on Wednesday 18 Nov 2026 the latest figure was for 10 Nov), one week later for a skipped
    release, two weeks for dates from 15 December to 7 January (no release between 23 Dec 2026 and 7 Jan 2027)."""
    first = day + dt.timedelta(days=2)
    thursday = first + dt.timedelta(days=(3 - first.weekday()) % 7)
    weeks = 2 if (day.month, day.day) >= (12, 15) or (day.month, day.day) <= (1, 7) else 1
    return thursday + dt.timedelta(weeks=weeks)


def bis_fx_public_from(day: dt.date) -> PublicFrom:
    """BIS's daily rupee figure: 23:59:59 Basel time (taken as UTC+1) on ``bis_fx_release_day``. BIS's figure (six
    decimals) is its own, not H.10's or FBIL's rate, and BIS names no source: it is public when BIS publishes it."""
    release = bis_fx_release_day(day)
    india = dt.datetime.combine(release, _END_OF_DAY) - _CET + dt.timedelta(hours=5, minutes=30)
    return PublicFrom(india, "publication_rule", "BIS rupee figure: 23:59:59 Basel time on the Thursday a week after "
                      "the first weekly release (Thursdays, data to the Tuesday before) that can carry it; two weeks "
                      "over the year end (a rule from one calendar, 2026-27, and one observation)")


def public_when_published(obs: Observation) -> bool:
    """Whether a value became public only when its publisher published it, so that the store's own earlier read
    without it is evidence that it was not public yet. Not so for BIS's policy rates, public from the central bank's
    announcement whatever BIS's lag."""
    return not (obs.source == BIS and obs.series_id != "usdinr_bis_daily")


def public_from_rule(obs: Observation, *, next_day: dt.date | None = None) -> PublicFrom:
    """The rule-based ``PublicFrom`` of a US or BIS observation read later (see the module docstring).

    New York Fed rows need the next date of their series (``next_day``; use
    ``nyfed_public_from_all`` for a whole response).
    """
    day = dt.date.fromisoformat(obs.period)
    if obs.source == UST:
        return ust_public_from(day)
    if obs.source == NYFED:
        return nyfed_public_from(nyfed_rate_of(obs.series_id), day, next_day, obs.revision == "r")
    if obs.source == EIA:
        return eia_public_from(day)
    if obs.source == H10:
        return h10_public_from(day)
    if obs.source == BIS:
        return bis_fx_public_from(day) if obs.series_id == "usdinr_bis_daily" else bis_policy_public_from(obs.area,
                                                                                                          day)
    raise ValueError(f"no publication rule for source {obs.source!r}")


# --- Index events -------------------------------------------------------------------------------------

INDEX_EVENT_COLUMNS = ("event_id", "provider", "index_name", "event_type", "announced_date", "announced_time",
                       "announced_tz", "effective_start", "effective_end", "phase_in", "target_weight_pct", "bonds",
                       "status", "source_url", "notes")
EVENT_TYPES = ("watch_list", "consultation", "announcement", "inclusion_start", "tranche", "inclusion_complete",
               "deferral", "review_no_change", "scheduled_review")
EVENT_STATUSES = ("verified", "unverified")
EVENT_ZONES = (NEW_YORK, LONDON, KOLKATA)


@dataclass(frozen=True)
class IndexEvent:
    """One index decision or step, hand-kept with its source and whether a provider's own page confirmed it."""

    event_id: str
    provider: str
    index_name: str
    event_type: str
    announced: str  # YYYY-MM-DD, or YYYY-MM when only the month is known
    announced_time: dt.time | None
    announced_tz: str
    effective_start: str
    effective_end: str
    phase_in: str
    target_weight_pct: str
    bonds: str
    status: str
    source_url: str
    notes: str
    origin: str

    @property
    def announced_day(self) -> dt.date:
        """The announcement date; the last day of its month when only the month is known."""
        return _last_day(self.announced)


def _last_day(text: str) -> dt.date:
    if re.fullmatch(r"\d{4}-\d{2}", text):
        year, month = int(text[:4]), int(text[5:])
        nxt = dt.date(year + month // 12, month % 12 + 1, 1)
        return nxt - dt.timedelta(days=1)
    return dt.date.fromisoformat(text)


def _first_day(text: str) -> dt.date:
    return dt.date(int(text[:4]), int(text[5:7]), 1) if len(text) == 7 else dt.date.fromisoformat(text)


def _event_date(text: str, where: str, column: str, *, required: bool) -> str:
    text = text.strip()
    if not text:
        if required:
            raise ValueError(f"{where}: {column} is empty")
        return ""
    try:
        if re.fullmatch(r"\d{4}-\d{2}", text):
            _last_day(text)
        else:
            dt.date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"{where}: {column} must be YYYY-MM-DD or YYYY-MM, not {text!r}") from None
    return text


def read_index_events(source: str | Path | bytes, *, name: str | None = None) -> list[IndexEvent]:
    """An index-event table kept by hand (a path or its bytes), every row checked. Nothing is read by default.

    Refused, naming the file and the line: a missing column, an unknown event
    type, status or time zone, a date that is not ``YYYY-MM-DD`` or
    ``YYYY-MM``, a time that is not ``HH:MM``, an end before its start, a
    weight that is not a number, an empty provider, index or source, an id
    that is not lower-case letters, digits and underscores, or is used twice.
    """
    if source is None:
        raise ValueError("no index-event table given: none is shipped as a default (an example with its sources is "
                         "data/index_events_example.csv in the repository)")
    if isinstance(source, bytes):
        text, name = text_of(source, name or "<bytes>"), name or "<bytes>"
    else:
        path = Path(source)
        text, name = text_of(path.read_bytes(), str(path)), name or str(path)
    reader = csv.DictReader(io.StringIO(text))
    missing = [c for c in INDEX_EVENT_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"{name}: an index-event table needs the columns {list(INDEX_EVENT_COLUMNS)}; missing "
                         f"{missing}")
    out, seen = [], set()
    for line, rec in enumerate(reader, start=2):
        where = f"{name}, line {line}"
        rec = {k: (v or "").strip() for k, v in rec.items() if k is not None}
        if not re.fullmatch(r"[a-z0-9_]+", rec["event_id"]):
            raise ValueError(f"{where}: event_id {rec['event_id']!r} must be lower-case letters, digits and _")
        if rec["event_id"] in seen:
            raise ValueError(f"{where}: event_id {rec['event_id']!r} is used twice")
        seen.add(rec["event_id"])
        for column in ("provider", "index_name", "source_url"):
            if not rec[column]:
                raise ValueError(f"{where}: {column} is empty")
        if rec["event_type"] not in EVENT_TYPES:
            raise ValueError(f"{where}: event_type {rec['event_type']!r} is not one of {', '.join(EVENT_TYPES)}")
        if rec["status"] not in EVENT_STATUSES:
            raise ValueError(f"{where}: status {rec['status']!r} is not verified or unverified")
        if rec["announced_tz"] not in EVENT_ZONES:
            raise ValueError(f"{where}: announced_tz {rec['announced_tz']!r} is not one of {', '.join(EVENT_ZONES)}")
        announced = _event_date(rec["announced_date"], where, "announced_date", required=True)
        time = None
        if rec["announced_time"]:
            m = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", rec["announced_time"])
            if m is None:
                raise ValueError(f"{where}: announced_time {rec['announced_time']!r} is not HH:MM")
            if len(announced) != 10:
                raise ValueError(f"{where}: an announced_time needs a full announced_date")
            time = dt.time(int(m.group(1)), int(m.group(2)))
        start = _event_date(rec["effective_start"], where, "effective_start", required=False)
        end = _event_date(rec["effective_end"], where, "effective_end", required=False)
        if start and end and _last_day(end) < _first_day(start):
            raise ValueError(f"{where}: effective_end {end} is before effective_start {start}")
        if rec["target_weight_pct"]:
            number_text(rec["target_weight_pct"], f"{where}, target_weight_pct")
        out.append(IndexEvent(rec["event_id"], rec["provider"], rec["index_name"], rec["event_type"], announced, time,
                              rec["announced_tz"], start, end, rec["phase_in"], rec["target_weight_pct"],
                              rec["bonds"], rec["status"], rec["source_url"], rec["notes"],
                              f"{Path(name).name}, line {line}"))
    return out


def load_index_events(source: str | Path | bytes, *, name: str | None = None) -> list[IndexEvent]:
    """The events of a table kept by hand (``read_index_events``), by announcement date and id."""
    return sorted(read_index_events(source, name=name), key=lambda e: (e.announced_day, e.event_id))


def index_event_public_from(event: IndexEvent) -> PublicFrom:
    """23:59:59 (or the announced time) in the provider's zone on the announcement date, in India time."""
    day = event.announced_day
    time = event.announced_time or _END_OF_DAY
    how = "the announced time" if event.announced_time else "23:59:59"
    month = " (only the month is known: its last day)" if len(event.announced) == 7 else ""
    return PublicFrom(zone_to_ist(event.announced_tz, day, time), "publication_rule",
                      f"{how} {event.announced_tz} on the announcement date{month}", event.event_id,
                      "" if event.status == "verified" else "unverified: from news reports")
