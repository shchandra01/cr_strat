"""The macro and global sources together: which hosts may be asked, a polite fetch, parsing, the series catalogue.

Build-order item 5 of ``docs/rates_data_sources.md``. ``mospi`` (MoSPI's
eSankhyiki API and release calendar) and ``global_sources`` (US Treasury, the
New York Fed, EIA, the Federal Reserve's H.10, BIS, index events) build the
requests and parse the answers; this module is what a store uses:

* ``SOURCES``: per source, its hosts, the validators its server honours, the
  pause between two requests to one host, a cap per India day, the most
  bytes it may send, whether it is polled by default, its terms and the
  citation it asks for.
* ``fetch(requests, now=..., hosts=..., transport=...)``: the requests, in
  order, politely (below); it returns what it did (``FetchReport``: every
  request's outcome and decoded body, and each host's updated
  ``HostState``). Keeping state between runs is the store's job: pass the
  states back in on the next run.
* ``parse(request, body)`` (or ``parse_kind(kind, body, name)``) reads a
  saved file or a fetched answer by the same code; ``detect_kind`` names the
  parser for a saved file from its content.
* ``catalogue()``: the series a store keeps by default (spec 3.5), each with
  its source, name, unit, frequency, base, first period, update
  information, terms and the citation or notice its publisher asks for;
  ``describe(observation)`` makes the same entry for any other series id a
  parser gives.
* ``public_from(observation, ...)``: from when a value read later was
  public, by the source's rules (``mospi.ReleaseCalendar`` for India,
  ``global_sources`` for the rest).

**Politeness.** Only the hosts of ``SOURCES`` are ever asked
(``check_request_url``), and never RBI's hosts, FRED or ALFRED (their terms
prohibit storing), FBIL, CCIL, AMFI, ICRA, India Ratings, CRISIL, NSDL, NSE,
BSE, bloomberg.com or MoSPI's MCP server (``REFUSED_DOMAINS``), nor
Treasury's ``/all/all`` path that was refused on 18 Nov 2026. Every request
names a generic client (``DEFAULT_USER_AGENT``; a name with an e-mail
address, a web address or this package's name is refused), asks for gzip,
follows no redirect (a 3xx is reported, not followed), and is conditional
where the server was seen to honour it (``If-None-Match`` for MoSPI's API,
``If-None-Match`` and ``If-Modified-Since`` for EIA, ``If-Modified-Since``
for H.10). At least ``SourceInfo.interval`` seconds (10, never under 5)
separate two requests to one host, counted from the previous request's start
as this run's clock knows it: the run's ``now`` plus the pauses it made
(``sleep``), never read from the wall clock, so a pause always waits the full
interval after an answer. At most ``max_requests`` are sent per run and
``SourceInfo.daily_cap`` per host per India day.

**The block rule** (as ``rbi_store``): HTTP 403, 418 or 429, a refusal page
whatever its status (a firewall's "Access Denied", Akamai's "Reference #", a
bot challenge: with HTTP 200, 404 or 503 alike), a page asking automated
clients for contact details (none is ever given), or a second web page in a
row where data were expected blocks that host: no request to it before 08:00
India time on the India day after the block (``storekit.resume_time``,
counted from the latest time the block can have happened: the run's clock
plus the timeouts of all its requests so far, ``sent_before`` counting those
of a store's earlier steps), and a
second block in a row stops it until a person resumes it (``resume=``) after
checking the site in a browser. A good answer resets the count. A 400 from
MoSPI (a page limit) or another 4xx is a programming error: that host gets
no more requests in the run, and the answer says so. A 5xx or a network
error is recorded; the next run tries again. A host's last request recorded
after ``now`` (or a cool-down longer than the rule allows) means the clock
moved back: no request, and ``clock_error`` says so. ``record`` (a callback)
gets each host's state just before its request is sent, so a store can
commit the attempt first, as ``rbi_store`` does.

**Defaults that are the user's call** (``SourceInfo.enabled_by_default``):
Treasury is off until someone has opened its per-year CSV in a browser from
the same network (it refused the probe on 18 Nov 2026); EIA is off until the
copyright field of series PET.RBRTE.D has been read (EIA's terms exclude
material licensed from private companies, and spot prices may be such);
MoSPI's "RBI" tables are not in the catalogue's default set (RBI's data,
fifteen months stale). Pass ``enabled=`` to change it.

Nothing here reads the clock: ``now`` is an argument, as in every module.
"""

from __future__ import annotations

import datetime as dt
import re
import time as _time
import urllib.parse
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from cr_strat import global_sources as gs
from cr_strat import mospi
from cr_strat.htmlparse import BlockedPageError, check_not_blocked, parse_html
from cr_strat.macro_base import Observation, ParseResult, PublicFrom, Request, SourceError, load_json, text_of
from cr_strat.storekit import (
    BLOCK_STATUSES,
    MAX_COOLDOWN,
    STOP_AFTER_BLOCKS,
    FeedResponse,
    Transport,
    check_timeout,
    check_user_agent,
    decode_body,
    printable,
    resume_time,
    urllib_transport,
)
from cr_strat.ustime import IST, UTC, utc_text

DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; data-client/1.0)"
MIN_INTERVAL = 5.0  # seconds: no host is asked twice within this, whatever a source's setting
DEFAULT_MAX_REQUESTS = 25
MAX_REQUESTS = 500
CLOCK_SLACK = dt.timedelta(minutes=5)
EXIT_OK, EXIT_ERROR, EXIT_BLOCKED, EXIT_ATTENTION = 0, 1, 3, 4
REFUSED_DOMAINS = frozenset({
    "rbi.org.in",  # www., rbidocs., website.
    "stlouisfed.org",  # FRED, ALFRED and their API: the terms prohibit storing
    "fbil.org.in", "ccilindia.com", "amfiindia.com", "icra.in", "icraanalytics.com", "indiaratings.co.in",
    "crisil.com", "nsdl.co.in", "nseindia.com", "bseindia.com", "bloomberg.com",
    "mcp.mospi.gov.in",  # MoSPI's MCP server: not used
})
_HTML_KINDS = frozenset({gs.EIA_BRENT, gs.H10_HISTORY})


@dataclass(frozen=True)
class SourceInfo:
    """A source: hosts, politeness settings, and what its publisher asks of a user."""

    key: str
    name: str
    publisher: str
    hosts: frozenset[str]
    kinds: tuple[str, ...]
    conditional: tuple[str, ...]  # validators the server was seen to honour: "etag", "last_modified"
    interval: float  # seconds between two requests to one host
    daily_cap: int  # requests to one host per India day
    enabled_by_default: bool
    max_wire_bytes: int
    max_body_bytes: int
    terms_url: str
    terms: str
    citation: str
    update_info: str
    notes: str = ""


_MB = 1_000_000
SOURCES: dict[str, SourceInfo] = {s.key: s for s in (
    SourceInfo(mospi.SOURCE, "MoSPI eSankhyiki API", mospi.PUBLISHER, frozenset({"api.mospi.gov.in"}),
               tuple(k for k in mospi.KINDS if k != mospi.CALENDAR_POINTER), ("etag",), 10.0, 60, True, 5 * _MB,
               5 * _MB, mospi.TERMS_URL, mospi.TERMS, mospi.CITATION,
               "CPI: 12th of the next month, 16:00 India time; IIP: 28th, 16:00; GDP: end of the second month after "
               "the quarter, 16:00; WPI: 14th, 12:00 (each series' own update_info gives its family's rule)",
               "weak ETag answered with HTTP 304 (seen once); no compression; no rate limit published"),
    SourceInfo(mospi.CALENDAR_SOURCE, "MoSPI advance release calendar (pointer)", mospi.PUBLISHER,
               frozenset({"www.mospi.gov.in"}), (mospi.CALENDAR_POINTER,), (), 10.0, 4, True, _MB, _MB,
               "https://www.mospi.gov.in", "No terms read (the site's terms page is rendered by script)", "",
               "re-issued about monthly", "the PDF itself is not read by this package"),
    SourceInfo(gs.UST, "US Treasury daily par yield curve", "U.S. Department of the Treasury",
               frozenset({gs.UST_HOST}), gs.UST_KINDS, (), 10.0, 10, False, 5 * _MB, 5 * _MB, gs.UST_TERMS_URL,
               "No reuse, copyright or automated-access clause on the Site Policies and Notices page; robots.txt "
               "allows all; US federal government data", gs.UST_CITATION,
               "daily, usually by 18:00 New York time (Treasury)",
               "off by default: Treasury refused the probe of 18 Nov 2026 (Akamai 403); no validators"),
    SourceInfo(gs.NYFED, "New York Fed reference rates", "Federal Reserve Bank of New York",
               frozenset({gs.NYFED_HOST}), (gs.NYFED_JSON, gs.NYFED_CSV), (), 10.0, 10, True, 5 * _MB, 20 * _MB,
               gs.NYFED_TERMS_URL, "Licence to access the content manually or by an automated process, and to "
               "download, store and use it; reference-rate users must carry the New York Fed's notice",
               gs.NYFED_NOTICE, "SOFR, BGCR, TGCR about 08:00 New York time on the next business day; EFFR, OBFR "
               "about 09:00; revisions about 14:30", "no validators; ask a short date range"),
    SourceInfo(gs.EIA, "EIA Europe Brent spot price", "U.S. Energy Information Administration",
               frozenset({gs.EIA_HOST}), (gs.EIA_BRENT,), ("etag", "last_modified"), 10.0, 10, False, 5 * _MB,
               5 * _MB, gs.EIA_TERMS_URL, "Public domain, cite EIA with the publication date; material licensed from "
               "private companies excluded (the series' copyright field was not read)", gs.EIA_CITATION,
               "weekly, normally Wednesday (13:03 to 13:27 New York time on 18 Nov 2026), through the day before",
               "off by default until the copyright field of PET.RBRTE.D is read; Last-Modified is not a "
               "publication time"),
    SourceInfo(gs.H10, "Federal Reserve H.10 (rupee)", "Board of Governors of the Federal Reserve System",
               frozenset({gs.H10_HOST}), (gs.H10_HISTORY, gs.H10_DDP), ("last_modified",), 10.0, 10, True,
               5 * _MB, 5 * _MB, gs.H10_TERMS_URL, "Public domain unless indicated; cite the Board", gs.H10_CITATION,
               "weekly, Monday 16:15 New York time, through the Friday before",
               "If-Modified-Since answered with HTTP 304; the Data Download Program goes in 2027"),
    SourceInfo(gs.BIS, "BIS statistics (SDMX API)", "Bank for International Settlements", frozenset({gs.BIS_HOST}),
               (gs.BIS_CBPOL, gs.BIS_XRU, gs.BIS_REL_CAL), (), 10.0, 10, True, 10 * _MB, 40 * _MB, gs.BIS_TERMS_URL,
               "Use unrestricted if the BIS is cited and its statistics add no charge to a commercial product; the "
               "API may limit an IP address; use the latest API version", gs.BIS_CITATION,
               "about weekly, on Thursdays (BIS release calendar); India's policy rate about a month behind",
               "ETags change on every request: no conditional request"),
)}


def source_of(request: Request) -> SourceInfo:
    try:
        return SOURCES[request.source]
    except KeyError:
        raise ValueError(f"no source {request.source!r}; known: {', '.join(SOURCES)}") from None


def _refused_domain(host: str) -> str | None:
    return next((d for d in REFUSED_DOMAINS if host == d or host.endswith("." + d)), None)


def check_request_url(url: str) -> tuple[str, SourceInfo]:
    """The host of ``url`` and its source, if a macro source may request it; ``ValueError`` otherwise."""
    if not isinstance(url, str) or not url or not printable(url) or " " in url:
        raise ValueError(f"a request URL must be visible ASCII without spaces, not {url!r}")
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme != "https" or not host or parts.username or parts.password or parts.fragment:
        raise ValueError(f"{url!r}: only https URLs without credentials or a fragment are requested")
    try:
        port = parts.port
    except ValueError:
        port = -1
    if port not in (None, 443):
        raise ValueError(f"{url!r}: a port other than 443")
    refused = _refused_domain(host)
    if refused is not None:
        raise ValueError(f"{host} is never requested by the macro sources ({refused}: terms, access or a block)")
    matches = [s for s in SOURCES.values() if host in s.hosts]
    if not matches:
        known = ", ".join(sorted(h for s in SOURCES.values() for h in s.hosts))
        raise ValueError(f"{host} is not a host of any macro source ({known})")
    if host == gs.UST_HOST and ("/all/all" in parts.path or "field_tdr_date_value=all" in parts.query):
        raise ValueError("Treasury's all-years path was refused (HTTP 403) on 18 Nov 2026: ask one year at a time")
    return host, matches[0]


_IDENTIFYING = re.compile(r"@|https?:|www\.|cr[-_ ]?strat", re.IGNORECASE)


def check_generic_user_agent(user_agent: str) -> str:
    """A client name that identifies no one: ``storekit.check_user_agent``, and no e-mail address, web address or
    this package's name (a source asking for contact details gets none)."""
    check_user_agent(user_agent)
    if _IDENTIFYING.search(user_agent):
        raise ValueError(f"user_agent {user_agent!r} names someone (an e-mail or web address, or this package): use a "
                         "generic client name")
    return user_agent


# --- Parsing ------------------------------------------------------------------------------------------

def parse_kind(kind: str, body: bytes, name: str, *, query: Sequence[tuple[str, str]] = (),
               note: str = "") -> ParseResult:
    """Parse ``body`` (a saved file or a fetched answer) with the parser of ``kind``.

    ``query`` and ``note`` are the request's, when known: the Treasury year,
    the CPI series (Current or Back) of a base-2012 request.
    """
    params = dict(query)
    if kind == mospi.ANY_KIND or (kind in mospi.KINDS and kind != mospi.CALENDAR_POINTER):
        series = note if note in ("Current", "Back") else params.get("series")
        return mospi.parse_mospi(body, name, kind=None if kind == mospi.ANY_KIND else kind, series=series)
    if kind == gs.UST_PAR or kind in gs.UST_KINDS:
        year = params.get("year")
        return gs.parse_ust_csv(body, name, curve=kind[4:], year=int(year) if year else None)
    if kind == gs.NYFED_JSON:
        return gs.parse_nyfed_json(body, name)
    if kind == gs.NYFED_CSV:
        return gs.parse_nyfed_csv(body, name)
    if kind == gs.EIA_BRENT:
        return gs.parse_eia_history(body, name)
    if kind == gs.H10_HISTORY:
        return gs.parse_h10_history(body, name)
    if kind == gs.H10_DDP:
        return gs.parse_h10_ddp_csv(body, name)
    if kind in (gs.BIS_CBPOL, gs.BIS_XRU):
        return gs.parse_bis_csv(body, name, dataflow="WS_CBPOL" if kind == gs.BIS_CBPOL else "WS_XRU")
    raise ValueError(f"no parser of observations for kind {kind!r} (the calendar pointer and BIS's release "
                     "calendar have their own functions)")


def parse(request: Request, body: bytes, name: str | None = None) -> ParseResult:
    """Parse a fetched answer with the parser its request names."""
    return parse_kind(request.kind, body, name or request.url, query=request.query, note=request.note)


def detect_kind(body: bytes, name: str = "<bytes>") -> str:
    """The parser kind of a saved response, recognised from its content; ``SourceError`` if none fits.

    eSankhyiki answers are told apart by the exact fields of their rows;
    one without rows (``No Data Found``, an error answer) is ``mospi.ANY_KIND``,
    which ``parse_kind`` reads without a layout check. A refusal page is
    refused here, naming it.
    """
    why = refusal(body)
    if why:
        raise SourceError(f"{name}: {why}")
    text = text_of(body[:400_000], name).lstrip()
    head = text[:600]
    if head.startswith(("{", "[")):
        if '"refRates"' in head:
            return gs.NYFED_JSON
        try:
            doc = load_json(body, name)
        except SourceError:
            raise SourceError(f"{name}: JSON of no known layout (not readable)") from None
        rows = doc.get("data") if isinstance(doc, dict) else None
        if isinstance(rows, dict) and str(rows.get("url", "")).startswith("uploads/documents/releaseCalender/"):
            return mospi.CALENDAR_POINTER
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            kind = next((mospi.LAYOUT_KIND[layout] for layout, keys in mospi.LAYOUTS.items()
                         if frozenset(rows[0]) == keys), None)
            if kind is not None:
                return kind
        if isinstance(doc, dict) and ("statusCode" in doc or "meta_data" in doc or "success" in doc
                                      or "error" in doc) and not (isinstance(rows, list) and rows):
            return mospi.ANY_KIND
        raise SourceError(f"{name}: JSON of no known layout")
    first = head.splitlines()[0] if head else ""
    if first.startswith('Date,"1 Mo"') or (first.startswith("Date,") and " Yr" in first):
        return gs.UST_PAR
    if first.startswith("Effective Date,Rate Type"):
        return gs.NYFED_CSV
    if first.startswith(('"Series Description"', "Series Description")):
        return gs.H10_DDP
    if first.startswith("FREQ,CATEGORY,RELEASE_TYPE"):
        return gs.BIS_REL_CAL
    if first.startswith(("STRUCTURE,STRUCTURE_ID,ACTION", "FREQ,REF_AREA")):
        if "WS_XRU" in head or ",CURRENCY,COLLECTION," in first:
            return gs.BIS_XRU
        return gs.BIS_CBPOL
    low = text.lower()
    if "brent spot price" in low:
        return gs.EIA_BRENT
    if "historical rates for the indian rupee" in low or "india -- spot exchange rate" in low:
        return gs.H10_HISTORY
    raise SourceError(f"{name}: not a response of any macro source this package reads ({first[:60]!r})")


# --- The series catalogue -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Series:
    """One series: what it is, where it comes from, how often and when it is published, and what to cite."""

    series_id: str
    source: str
    publisher: str
    name: str
    unit: str
    frequency: str
    base: str = ""
    first_period: str = ""
    dataset: str = ""
    period_format: str = ""
    update_info: str = ""
    citation: str = ""
    terms: str = ""
    terms_url: str = ""
    aliases: str = ""
    stored_by_default: bool = True
    notes: str = ""


_FREQUENCY = {"day": ("daily", "YYYY-MM-DD"), "month": ("monthly", "YYYY-MM"),
              "fy_quarter": ("quarterly (financial year)", "YYYY-YYQn"), "fy": ("annual (financial year)", "YYYY-YY")}
CPI_2024_DIVISIONS = {"01": "Food and beverages", "02": "Paan, tobacco and intoxicants", "03": "Clothing and footwear",
                      "04": "Housing, water, electricity, gas and other fuels",
                      "05": "Furnishings, household equipment and routine household maintenance", "06": "Health",
                      "07": "Transport", "08": "Information and communication", "09": "Recreation, sport and culture",
                      "10": "Education services", "11": "Restaurants and accommodation services",
                      "13": "Personal care, social protection and miscellaneous goods and services"}
CPI_2024_ALIASES = {"food": ("g01_1", "Food (the CFPI of MoSPI's release)"), "food_and_beverages": ("d01", ""),
                    "housing_utilities": ("d04", ""), "fuel": ("g04_5", "Electricity, gas and other fuels"),
                    "housing_rent": ("g04_1", "Actual rental payments made for housing")}
SECTORS = ("combined", "rural", "urban")


def mospi_family(series_id: str) -> str | None:
    """The MoSPI release family of a series id (``cpi``, ``iip``, ``gdp``, ``wpi``), or ``None``."""
    head = series_id.split("_")[0]
    return {"cpi": "cpi", "iip": "iip", "gdp": "gdp", "gva": "gdp", "wpi": "wpi"}.get(head)


def _publisher_of(series_id: str, source: str) -> tuple[str, str, str]:
    """``(publisher, citation, update_info)`` of a series: WPI is compiled by DPIIT's Office of the Economic Adviser
    and served by MoSPI's API; each MoSPI family has its own release rule."""
    info = SOURCES[source]
    if source != mospi.SOURCE:
        return info.publisher, info.citation, info.update_info
    family = mospi_family(series_id)
    update = mospi.UPDATE_INFO.get(family or "", info.update_info)
    if family == "wpi":
        return mospi.WPI_PUBLISHER, mospi.WPI_CITATION, update
    return info.publisher, info.citation, update


def _entry(series_id: str, source: str, name: str, unit: str, period_type: str, *, base: str = "",
           first: str = "", dataset: str = "", aliases: str = "", stored: bool = True, notes: str = "") -> Series:
    info = SOURCES[source]
    frequency, fmt = _FREQUENCY[period_type]
    publisher, citation, update = _publisher_of(series_id, source)
    return Series(series_id, source, publisher, name, unit, frequency, base, first, dataset or info.name, fmt,
                  update, citation, info.terms, info.terms_url, aliases, stored, notes)


def _us_series() -> list[Series]:
    out = []
    history = {"ust_par_1m": "from 31 Jul 2001 (not verified)", "ust_par_1_5m": "from 18 Feb 2025",
               "ust_par_2m": "from 16 Oct 2018", "ust_par_4m": "from 19 Oct 2022",
               "ust_par_20y": "from 1 Oct 1993 (discontinued at the end of 1986)",
               "ust_par_30y": "absent from 18 Feb 2002 to 9 Feb 2006"}
    for column, sid in gs.UST_PAR_COLUMNS.items():
        out.append(_entry(sid, gs.UST, f"US Treasury par yield, {column}", "percent", "day",
                          notes="yearly files from 1990 (those of 2017 to 2026 were read; earlier ones not)"
                                + (f"; {history[sid]}" if sid in history else "")))
    names = {"sofr": "SOFR", "bgcr": "BGCR", "tgcr": "TGCR", "effr": "EFFR", "obfr": "OBFR"}
    firsts = {"sofr": "2018-04-02", "bgcr": "2018-04-02", "tgcr": "2018-04-02", "effr": "2000-07-03",
              "obfr": "2016-03-01"}
    for rate, label in names.items():
        long = gs._NYFED_NAMES[label]
        out.append(_entry(rate, gs.NYFED, long, "percent", "day", first=firsts[rate],
                          notes="EFFR's layout changed on 16 Dec 2008 and 1 Mar 2016 (derived/global_daily.csv "
                          "notes the break)" if rate == "effr" else ""))
        for suffix, what in (("_p1", "1st percentile"), ("_p25", "25th percentile"), ("_p75", "75th percentile"),
                             ("_p99", "99th percentile")):
            out.append(_entry(rate + suffix, gs.NYFED, f"{long}, {what}", "percent", "day",
                              first="2016-03-01" if rate == "effr" else firsts[rate]))
        out.append(_entry(f"{rate}_volume_bn", gs.NYFED, f"{long}, volume", "US$ billion", "day",
                          first="2016-03-01" if rate == "effr" else firsts[rate]))
    for suffix, what in (("_intraday_low", "intraday low"), ("_intraday_high", "intraday high"),
                         ("_std_dev", "standard deviation")):
        out.append(_entry(f"effr{suffix}", gs.NYFED, f"Effective Federal Funds Rate, {what}", "percent", "day",
                          first="2000-07-03", notes="to 29 Feb 2016"))
    out.append(_entry("fomc_target_low", gs.NYFED, "FOMC target: lower end of the range (the point target before "
                      "16 Dec 2008)", "percent", "day", first="2000-07-03", notes="from the EFFR rows"))
    out.append(_entry("fomc_target_high", gs.NYFED, "FOMC target: upper end of the range", "percent", "day",
                      first="2008-12-16", notes="from the EFFR rows; no upper end while the target was a point"))
    for sid, what in (("sofr_avg_30d", "30-day average SOFR"), ("sofr_avg_90d", "90-day average SOFR"),
                      ("sofr_avg_180d", "180-day average SOFR")):
        out.append(_entry(sid, gs.NYFED, what, "percent", "day", first="2020-03-02",
                          notes="dated on its publication day"))
    out.append(_entry("sofr_index", gs.NYFED, "SOFR Index", "index, 2 Apr 2018 = 1", "day", first="2020-03-02",
                      notes="dated on its publication day"))
    out.append(_entry("brent_spot_usd_bbl", gs.EIA, "Europe Brent Spot Price FOB", "US dollars per barrel", "day",
                      first="1987-05-20", stored=True, notes="the EIA poller is off by default (copyright field "
                      "not read)"))
    out.append(_entry("usdinr_h10_noon", gs.H10, "Indian rupees per US dollar, noon buying rate in New York",
                      "rupees per US dollar", "day", first="1990-01-02",
                      notes="earlier years in a file not identified (default1989.htm not read); not FBIL's fixing"))
    out.append(_entry("policy_rate_in_bis", gs.BIS, "India policy rate (BIS): repo rate from 3 Apr 2001, bank rate "
                      "before", "percent per annum", "day", first="1946-01-01",
                      notes="about a month behind; some 2019 change dates a day after RBI's decision; for "
                      "checking the hand-kept policy rates (macro-store-status --policy-rates), not a source"))
    out.append(_entry("policy_rate_us_bis", gs.BIS, "US policy rate (BIS): mid-point of the FOMC target range from "
                      "19 Dec 1985, effective rate before", "percent per annum", "day", first="1954-07-01"))
    out.append(_entry("usdinr_bis_daily", gs.BIS, "Indian rupees per US dollar, daily average (BIS)",
                      "rupees per US dollar", "day", first="1973-01-02", notes="six decimals; not the H.10 rate; "
                      "BIS names no source; dated by BIS's weekly release with a week's margin: prefer "
                      "usdinr_h10_noon in back-tests"))
    return out


def _mospi_series() -> list[Series]:
    out = []
    first = {"2024": "2025-01", "2024back": "2013-01", "2012": "2011-01", "2010": ""}  # base 2010's start not checked
    for sector in SECTORS:
        for base, label in (("2024", "base 2024"), ("2024back", "base 2024, back series"), ("2012", "base 2012"),
                            ("2010", "base 2010")):
            stem = f"cpi_{base}_all_india_{sector}"
            unit = f"index, {base[:4]}=100"
            note = "the first month was not checked; to December 2014" if base == "2010" else ""
            out.append(_entry(f"{stem}_general", mospi.SOURCE, f"CPI, All India, {sector}, general index ({label})",
                              unit, "month", base=base[:4], first=first[base], notes=note))
            out.append(_entry(f"{stem}_general_yoy", mospi.SOURCE, f"CPI inflation, All India, {sector}, general "
                              f"({label})", mospi.UNIT_PERCENT_YOY, "month", base=base[:4], first=first[base],
                              notes=note))
        stem = f"cpi_2024_all_india_{sector}"
        for code, name in CPI_2024_DIVISIONS.items():
            aliases = ",".join(f"{stem}_{a}" for a, (node, _) in CPI_2024_ALIASES.items() if node == f"d{code}")
            for suffix, unit, what in (("", "index, 2024=100", "index"), ("_yoy", mospi.UNIT_PERCENT_YOY,
                                                                          "inflation")):
                out.append(_entry(f"{stem}_d{code}{suffix}", mospi.SOURCE, f"CPI {what}, All India, {sector}, "
                                  f"division {code} {name} (base 2024)", unit, "month", base="2024", first="2025-01",
                                  aliases=",".join(a + suffix for a in aliases.split(",") if a)))
        for alias, (node, name) in CPI_2024_ALIASES.items():
            if not node.startswith("g"):
                continue
            for suffix, unit, what in (("", "index, 2024=100", "index"), ("_yoy", mospi.UNIT_PERCENT_YOY,
                                                                          "inflation")):
                out.append(_entry(f"{stem}_{node}{suffix}", mospi.SOURCE, f"CPI {what}, All India, {sector}, group "
                                  f"{node[1:].replace('_', '.')} {name} (base 2024)", unit, "month", base="2024",
                                  first="2025-01", aliases=f"{stem}_{alias}{suffix}"))
        for node in ("food_and_beverages", "pan_tobacco", "clothing_footwear", "housing", "fuel_and_light",
                     "miscellaneous", "cfpi"):
            for suffix, unit in (("", "index, 2012=100"), ("_yoy", mospi.UNIT_PERCENT_YOY)):
                out.append(_entry(f"cpi_2012_all_india_{sector}_{node}{suffix}", mospi.SOURCE,
                                  f"CPI {'inflation' if suffix else 'index'}, All India, {sector}, "
                                  f"{node.replace('_', ' ')} (base 2012)", unit, "month", base="2012",
                                  first="2011-01", notes="no rural housing index" if node == "housing" else ""))
    iip = {"general": "General index", "mining": "Mining & Quarrying", "manufacturing": "Manufacturing",
           "electricity_gas": "Electricity & Gas Supply", "water_sewerage": "Water Supply, Sewerage & Waste Management",
           "primary_goods": "Primary Goods", "capital_goods": "Capital Goods",
           "intermediate_goods": "Intermediate Goods", "infrastructure_goods": "Infrastructure/Construction Goods",
           "consumer_durables": "Consumer Durables", "consumer_nondurables": "Consumer Non-durables"}
    for node, name in iip.items():
        for suffix, unit in (("", "index, 2022-23=100"), ("_yoy", mospi.UNIT_PERCENT_YOY)):
            out.append(_entry(f"iip_2022_23_{node}{suffix}", mospi.SOURCE, f"IIP {'growth' if suffix else 'index'}, "
                              f"{name} (base 2022-23)", unit, "month", base="2022-23", first="2023-04",
                              notes="growth from April 2024 only (FY2023-24 has no year-ago value on this base)"
                              if suffix else ""))
    for suffix, unit in (("", "index, 2011-12=100"), ("_yoy", mospi.UNIT_PERCENT_YOY)):
        out.append(_entry(f"iip_2011_12_general{suffix}", mospi.SOURCE, f"IIP {'growth' if suffix else 'index'}, "
                          "General index (base 2011-12)", unit, "month", base="2011-12", first="2012-04",
                          notes="to March 2026; the first month inferred from 5,544 rows (168 months) by an earlier "
                          "probe; sector names on this base were not sampled"))
    for base, first_q in (("2022-23", "2022-23Q1"), ("2011-12", "2011-12Q1")):
        b = base.replace("-", "_")
        for freq, period_type, first_p in (("q", "fy_quarter", first_q), ("a", "fy", base)):
            for price in ("current", "constant"):
                out.append(_entry(f"gdp_{b}_{freq}_{price}_cr", mospi.SOURCE, f"GDP at {price} prices "
                                  f"({'quarterly' if freq == 'q' else 'annual'}, base {base})", mospi.UNIT_CRORE,
                                  period_type, base=base, first=first_p, notes="never splice levels across bases"))
        for node in ("total", *(v for v in mospi.NAS_INDUSTRIES.values() if v != "total")):
            for price in ("current", "constant"):
                out.append(_entry(f"gva_{b}_q_{node}_{price}_cr", mospi.SOURCE, f"GVA at {price} prices, "
                                  f"{node.replace('_', ' ')} (quarterly, base {base})", mospi.UNIT_CRORE, "fy_quarter",
                                  base=base, notes=f"the first quarter was not checked (GDP's on this base: "
                                  f"{first_q})"))
    for node, name in (("all", "all commodities"), ("primary", "primary articles"), ("fuel_power", "fuel & power"),
                       ("manufactured", "manufactured products"), ("food_index", "food index")):
        out.append(_entry(f"wpi_2022_23_{node}", mospi.SOURCE, f"WPI, {name} (base 2022-23)", "index, 2022-23=100",
                          "month", base="2022-23", first="2023-04", notes="no inflation published: compute it on "
                          "one base"))
    out.append(_entry("wpi_2011_12_all", mospi.SOURCE, "WPI, all commodities (base 2011-12)", "index, 2011-12=100",
                      "month", base="2011-12", first="2012-04", notes="to April 2026"))
    return out


def catalogue() -> list[Series]:
    """The series a store keeps by default (spec 3.5), sorted by id."""
    return sorted(_us_series() + _mospi_series(), key=lambda s: s.series_id)


_CATALOGUE: dict[str, Series] | None = None


def series_info(series_id: str) -> Series | None:
    """The catalogue entry of ``series_id``, or ``None`` if it is not one of the default set."""
    global _CATALOGUE
    if _CATALOGUE is None:
        _CATALOGUE = {s.series_id: s for s in catalogue()}
    return _CATALOGUE.get(series_id)


_DEFAULT_MOSPI = re.compile(
    r"cpi_2024_all_india_(?:rural|urban|combined)_(?:general|d\d{2}|g\d{2}_\d+)(?:_yoy)?"
    r"|cpi_2024back_all_india_(?:rural|urban|combined)_general(?:_yoy)?"
    r"|cpi_(?:2012|2010)_all_india_(?:rural|urban|combined)_(?:general|food_and_beverages|pan_tobacco|"
    r"clothing_footwear|clothing_bedding_footwear|housing|fuel_and_light|miscellaneous|cfpi|s[\d_]+|s_[a-z0-9_]+)"
    r"(?:_yoy)?"
    r"|iip_(?:2022_23|2011_12)_(?:general|mining|manufacturing|electricity_gas|electricity|water_sewerage|"
    r"primary_goods|capital_goods|intermediate_goods|infrastructure_goods|consumer_durables|consumer_nondurables)"
    r"(?:_yoy)?"
    r"|gdp_(?:2022_23|2011_12)_[qa]_(?:current|constant)_cr"
    r"|gva_(?:2022_23|2011_12)_[qa]_[a-z_]+_(?:current|constant)_cr"
    r"|wpi_(?:2022_23|2011_12)_(?:all|primary|fuel_power|manufactured|food_index)")


def stored_by_default(series_id: str) -> bool:
    """Whether a store keeps the series under the conservative default (decision 4.1 of the spec).

    MoSPI: All India only, and only CPI down to base 2024's groups (base 2012's
    subgroups), IIP's general index, sectors and use-based groups, GDP and
    GVA by industry, WPI's headline and major groups; no state or item level,
    and not the "RBI" tables. Every US and BIS series of the catalogue.
    """
    if series_id.startswith("mospi_rbi_"):
        return False
    if series_id.startswith(("cpi_", "iip_", "gdp_", "gva_", "wpi_")):
        return _DEFAULT_MOSPI.fullmatch(series_id) is not None
    return series_info(series_id) is not None


def describe(obs: Observation) -> Series:
    """A catalogue entry for the series of ``obs``: the catalogue's if it has one, else made from the row."""
    known = series_info(obs.series_id)
    if known is not None:
        return known
    if obs.source not in SOURCES:
        raise ValueError(f"no source {obs.source!r}")
    info = SOURCES[obs.source]
    frequency, fmt = _FREQUENCY[obs.period_type]
    publisher, citation, update = _publisher_of(obs.series_id, obs.source)
    return Series(obs.series_id, obs.source, publisher, obs.indicator or obs.label or obs.series_id, obs.unit,
                  frequency, obs.base, "", obs.dataset or info.name, fmt, update, citation, info.terms,
                  info.terms_url, "", stored_by_default(obs.series_id), obs.notes)


# --- From when a value read later was public ----------------------------------------------------------

def public_from(obs: Observation, *, seen_at: dt.datetime, calendar: mospi.ReleaseCalendar | None = None,
                next_day: dt.date | None = None) -> PublicFrom:
    """From when the value of ``obs``, read at ``seen_at`` (not as it was published), was public.

    MoSPI rows: ``mospi.ReleaseCalendar.backfill_public_from`` (``calendar``,
    default the shipped table). US and BIS rows: the publication rules of
    ``global_sources`` (New York Fed rows need ``next_day``, the next date of
    the same series; ``global_sources.nyfed_public_from_all`` does a whole
    answer). A value is never dated after ``seen_at``, when it was evidently
    public: a rule time later than that (or none: the latest New York Fed
    row) gives ``seen_at`` itself (``first_seen``). A MoSPI "RBI" row has no
    rule: its ``seen_at`` is returned.
    """
    if seen_at.tzinfo is None or seen_at.utcoffset() is None:
        raise ValueError(f"seen_at must be a timezone-aware datetime, not {seen_at!r}")
    seen = seen_at.astimezone(IST).replace(tzinfo=None)
    if obs.source == mospi.SOURCE:
        key = mospi.timing_key(obs)
        if key is None:
            return PublicFrom(seen, "first_seen", "no release rule (RBI's data republished by MoSPI)")
        family, base, period, estimate = key
        return (calendar or mospi.ReleaseCalendar.load()).backfill_public_from(family, base, period, seen_at,
                                                                              estimate=estimate)
    rule = gs.public_from_rule(obs, next_day=next_day)
    if rule.time is None or rule.time > seen:
        return PublicFrom(seen, "first_seen", f"read before the rule's time ({rule.text or 'none'}): {rule.rule}",
                          rule.release_id)
    return rule


# --- The polite fetch ---------------------------------------------------------------------------------

_HOST_TIMES = ("last_request", "last_success", "cooldown_until", "stopped", "resumed", "last_block")
_HOST_COUNTS = ("blocks", "web_pages", "errors", "day_requests")
_HOST_TEXTS = ("last_outcome", "last_block_reason", "day", "contact_demand")


@dataclass
class HostState:
    """What the fetch remembers of a host between runs (the store keeps it, e.g. in ``state.json``). Times UTC."""

    host: str
    last_request: dt.datetime | None = None
    last_success: dt.datetime | None = None
    last_outcome: str = ""
    last_status: int | None = None
    cooldown_until: dt.datetime | None = None
    stopped: dt.datetime | None = None
    resumed: dt.datetime | None = None
    blocks: int = 0  # blocks in a row
    last_block: dt.datetime | None = None
    last_block_reason: str = ""
    web_pages: int = 0  # web pages in a row where data were expected
    errors: int = 0  # requests in a row without a usable answer
    day: str = ""  # the India day ``day_requests`` counts
    day_requests: int = 0
    contact_demand: str = ""

    def to_dict(self) -> dict:
        """JSON-ready: times as ``2026-11-18T03:09:51Z`` or null."""
        out = {}
        for key, value in self.__dict__.items():
            out[key] = utc_text(value) if isinstance(value, dt.datetime) else value
        return out

    @classmethod
    def from_dict(cls, data: Mapping, where: str = "host state") -> HostState:
        """A state written by ``to_dict``, every value checked (a hand edit included); ``ValueError`` names the key."""
        if not isinstance(data, Mapping) or not isinstance(data.get("host"), str) or not data["host"]:
            raise ValueError(f"{where}: not a host state (no host)")
        unknown = sorted(set(data) - set(cls.__dataclass_fields__))
        if unknown:
            raise ValueError(f"{where}: unknown keys {unknown}")
        values = {"host": data["host"]}
        for key in _HOST_TIMES:
            text = data.get(key)
            if text in (None, ""):
                values[key] = None
                continue
            try:
                values[key] = dt.datetime.strptime(str(text), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
            except ValueError:
                raise ValueError(f"{where}: {key} {text!r} is not a time like 2026-11-18T03:09:51Z") from None
        for key in _HOST_COUNTS:
            value = data.get(key, 0)
            if type(value) is not int or value < 0:
                raise ValueError(f"{where}: {key} must be a whole number of at least 0, not {value!r}")
            values[key] = value
        status = data.get("last_status")
        if status is not None and (type(status) is not int or not 100 <= status <= 599):
            raise ValueError(f"{where}: last_status must be an HTTP status or null, not {status!r}")
        values["last_status"] = status
        for key in _HOST_TEXTS:
            value = data.get(key, "")
            if not isinstance(value, str):
                raise ValueError(f"{where}: {key} must be text, not {value!r}")
            values[key] = value
        return cls(**values)


@dataclass(frozen=True)
class Exchange:
    """One request of a fetch and what came of it. ``time`` is when it was sent (the run's clock, UTC: no earlier
    than the real time), ``None`` if it was not sent; ``latest`` the latest the answer can have come (that clock plus
    the timeouts of every request of the run so far: no earlier than the real time of the answer); ``body`` the
    decoded answer of an ``ok`` one."""

    request: Request
    outcome: str
    message: str
    time: dt.datetime | None = None
    http_status: int | None = None
    wire_bytes: int = 0
    body: bytes | None = None
    etag: str = ""
    last_modified: str = ""
    content_type: str = ""
    latest: dt.datetime | None = None

    @property
    def sent(self) -> bool:
        return self.time is not None


SENT_OUTCOMES = ("ok", "not_modified", "blocked", "server_error", "network_error", "http_error", "client_error",
                 "bad_answer")
SKIPPED_OUTCOMES = ("disabled", "duplicate", "halted", "stopped", "cooling_down", "daily_cap", "run_cap",
                    "clock_error")
REQUEST_LOG_COLUMNS = ("time", "source", "host", "url", "http_status", "outcome", "bytes", "etag", "last_modified",
                       "message")


@dataclass
class FetchReport:
    """What a fetch did: every request in order with its outcome, each host's state, and what needs a look."""

    exchanges: list[Exchange]
    hosts: dict[str, HostState]
    attention: list[str] = field(default_factory=list)

    def sent(self) -> list[Exchange]:
        return [e for e in self.exchanges if e.sent]

    def answers(self) -> list[Exchange]:
        """The exchanges with a body to parse."""
        return [e for e in self.exchanges if e.outcome == "ok"]

    def log_rows(self) -> list[dict[str, str]]:
        """One row per request sent, for a store's request log (``REQUEST_LOG_COLUMNS``; times UTC)."""
        return [{"time": utc_text(e.time), "source": e.request.source, "host": e.request.host, "url": e.request.url,
                 "http_status": "" if e.http_status is None else str(e.http_status), "outcome": e.outcome,
                 "bytes": str(e.wire_bytes), "etag": e.etag, "last_modified": e.last_modified, "message": e.message}
                for e in self.sent()]

    @property
    def exit_code(self) -> int:
        """3 if a host is blocked, cooling down or stopped; 1 for an error or a clock error; 4 if something needs a
        look; else 0 (as ``rbi-store-poll``)."""
        outcomes = {e.outcome for e in self.exchanges}
        if outcomes & {"blocked", "cooling_down", "stopped"}:
            return EXIT_BLOCKED
        if outcomes & {"server_error", "network_error", "http_error", "client_error", "bad_answer", "clock_error"}:
            return EXIT_ERROR
        return EXIT_ATTENTION if self.attention else EXIT_OK


_AKAMAI = re.compile(r"reference\s*#\s*[\d.a-f]+|errors\.edgesuite\.net|you don't have permission to access",
                     re.IGNORECASE)
_CONTACT = re.compile(r"(?:contact (?:information|details)|e-?mail address|your e-?mail)[^.]{0,120}"
                      r"\b(?:user[- ]?agent|automated|crawlers?|robots?|bots?)\b"
                      r"|\b(?:user[- ]?agent|automated|crawlers?|robots?|bots?)\b[^.]{0,120}"
                      r"(?:contact (?:information|details)|e-?mail address|your e-?mail)"
                      r"|declare your (?:traffic|user[- ]?agent)", re.IGNORECASE)
_SHORT_TEXT = 5000  # a plain-text refusal is short; data are not


def refusal(body: bytes) -> str:
    """Why ``body`` is a refusal rather than content, or ``""``.

    A refusal is a firewall's page ("Access Denied", "Unauthorised Access ...
    Support ID", Akamai's "Reference #"), a bot challenge (a script-only page),
    or a page or short text that asks automated clients for contact details.
    JSON and CSV are never read as refusals, nor a page with a table.
    """
    text = body[:200_000].decode("utf-8", errors="replace").lstrip()
    if not text or text[:1] in "{[":
        return ""
    if text[:1] != "<":
        if len(text) <= _SHORT_TEXT and _CONTACT.search(text):
            return "a text asking automated clients for contact details (none given)"
        return ""
    page = parse_html(text)
    try:
        check_not_blocked(page, "the answer")
    except BlockedPageError as exc:
        return str(exc)
    if page.tables:
        return ""
    if _CONTACT.search(f"{page.title} {page.text}"):
        return "a page asking automated clients for contact details (none given)"
    if _AKAMAI.search(page.text):
        return f"an Akamai refusal page ({page.title or page.text[:60]!r})"
    return ""


def _looks_like_page(body: bytes, kind: str) -> bool:
    """A web page where JSON or CSV was expected (an XML error answer included)."""
    return kind not in _HTML_KINDS and body.lstrip()[:1] == b"<"


def follow_pages(request: Request, exchange: Exchange) -> list[Request]:
    """For ``fetch(follow=...)``: the further pages of a MoSPI query whose page 1 just came back."""
    if request.kind not in mospi.KINDS or request.kind == mospi.CALENDAR_POINTER or exchange.body is None:
        return []
    try:
        result = parse(request, exchange.body)
    except SourceError:
        return []
    return mospi.more_pages(request, result)


def fetch(requests: Iterable[Request], *, now: dt.datetime, hosts: Mapping[str, HostState] | None = None,
          transport: Transport | None = None, user_agent: str = DEFAULT_USER_AGENT, timeout: float = 60.0,
          max_requests: int = DEFAULT_MAX_REQUESTS, enabled: Iterable[str] | None = None,
          validators: Mapping[str, tuple[str, str]] | None = None,
          follow: Callable[[Request, Exchange], list[Request]] | None = None,
          sleep: Callable[[float], None] | None = None, resume: Iterable[str] = (),
          record: Callable[[str, HostState], None] | None = None, ssl_context=None,
          sent_before: int = 0) -> FetchReport:
    """Send ``requests`` in order, politely (module docstring), and return what was done.

    ``now`` (timezone-aware) is when the run starts, or the run's clock when
    a run fetches in several steps; ``sent_before`` the requests that run sent
    in earlier steps, so that the latest time an answer can have come (and a
    block's cool-down) counts the timeouts of all of them; ``hosts`` the
    states of the previous run (copied, not changed); ``transport`` sends one
    GET (default ``storekit.urllib_transport`` with the source's byte cap and
    ``ssl_context``); ``validators`` maps a URL to the ``(etag,
    last_modified)`` its last answer sent; ``follow`` may add requests after
    each good answer (``follow_pages`` for MoSPI's pages); ``sleep`` waits
    between two requests to a host (default ``time.sleep``); ``resume`` names
    hosts a person has checked and resumes (a stop or cool-down ended);
    ``record`` gets each host's state just before its request is sent.
    Arguments and every request URL are checked before anything is sent.
    """
    if not isinstance(now, dt.datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError(f"now must be a timezone-aware datetime, not {now!r}")
    check_generic_user_agent(user_agent)
    check_timeout(timeout)
    if isinstance(max_requests, bool) or not isinstance(max_requests, int) or not 1 <= max_requests <= MAX_REQUESTS:
        raise ValueError(f"max_requests must be a whole number from 1 to {MAX_REQUESTS}, not {max_requests!r}")
    if isinstance(sent_before, bool) or not isinstance(sent_before, int) or sent_before < 0:
        raise ValueError(f"sent_before must be a whole number of at least 0, not {sent_before!r}")
    enabled_set = set(enabled) if enabled is not None else {k for k, s in SOURCES.items() if s.enabled_by_default}
    unknown = enabled_set - set(SOURCES)
    if unknown:
        raise ValueError(f"enabled names unknown sources {sorted(unknown)}; known: {', '.join(SOURCES)}")
    queue = list(requests)
    for request in queue:
        _check_request(request)
    states = {h: replace(s) for h, s in (hosts or {}).items()}
    for host in resume:
        state = states.setdefault(host, HostState(host))
        state.stopped = state.cooldown_until = None
        state.resumed = now.astimezone(UTC)
    sleep = sleep if sleep is not None else _time.sleep
    clock = now.astimezone(UTC)
    exchanges: list[Exchange] = []
    attention: list[str] = []
    seen: set[str] = set()
    halted: dict[str, str] = {}
    sent = 0
    position = 0
    while position < len(queue):
        request = queue[position]
        position += 1
        info = source_of(request)
        host = request.host
        state = states.setdefault(host, HostState(host))
        skip = _skipper(exchanges, request)
        if request.source not in enabled_set:
            skip("disabled", f"source {request.source} is not enabled ({info.notes or 'off by default'})")
            continue
        if request.url in seen:
            skip("duplicate", "the same URL was already requested in this run")
            continue
        if host in halted:
            skip("halted", f"no more requests to {host} in this run: {halted[host]}")
            continue
        if state.stopped is not None:
            skip("stopped", f"{host} stopped after {state.blocks} blocks in a row (the last: {state.last_block_reason} "
                 f"at {utc_text(state.last_block) if state.last_block else 'unknown'}): no request until a person "
                 "checks the site in a browser and resumes it")
            continue
        skew = _clock_error(state, clock)
        if skew:
            skip("clock_error", skew)
            halted[host] = "clock error"
            continue
        if state.cooldown_until is not None and clock < state.cooldown_until:
            skip("cooling_down", f"{host} is cooling down after {state.last_block_reason or 'a block'}: no request "
                 f"before {utc_text(state.cooldown_until)}")
            continue
        day = clock.astimezone(IST).date().isoformat()
        if state.day != day:
            state.day, state.day_requests = day, 0
        if state.day_requests >= info.daily_cap:
            skip("daily_cap", f"{host} has had its {info.daily_cap} requests of the India day {day}")
            continue
        if sent >= max_requests:
            skip("run_cap", f"the run's cap of {max_requests} requests is reached")
            continue
        interval = max(MIN_INTERVAL, info.interval)
        if state.last_request is not None:
            wait = interval - (clock - state.last_request).total_seconds()
            if wait > 0:
                sleep(wait)
                clock += dt.timedelta(seconds=wait)
        headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip", "Accept": request.accept}
        etag, modified = (validators or {}).get(request.url, ("", ""))
        if etag and "etag" in info.conditional:
            headers["If-None-Match"] = etag
        if modified and "last_modified" in info.conditional:
            headers["If-Modified-Since"] = modified
        state.last_request, state.last_outcome, state.last_status = clock, "requesting", None
        state.day_requests += 1
        seen.add(request.url)
        sent += 1
        if record is not None:
            record(host, replace(state))
        if transport is None:
            response = urllib_transport(request.url, headers, timeout, ssl_context=ssl_context,
                                        max_bytes=info.max_wire_bytes)
        else:
            response = transport(request.url, headers, timeout)
        latest = clock + dt.timedelta(seconds=timeout * (sent_before + sent))  # the latest the answer can have come
        exchange = replace(_answer(request, info, state, response, clock, latest, attention), latest=latest)
        exchanges.append(exchange)
        state.last_outcome, state.last_status = exchange.outcome, exchange.http_status
        if exchange.outcome not in ("ok", "not_modified"):
            halted[host] = f"{exchange.outcome} ({exchange.message})"
        elif exchange.outcome == "ok" and follow is not None:
            for extra in follow(request, exchange):
                _check_request(extra)
                queue.append(extra)
    return FetchReport(exchanges, states, attention)


def _skipper(exchanges: list[Exchange], request: Request) -> Callable[[str, str], None]:
    def skip(outcome: str, message: str) -> None:
        exchanges.append(Exchange(request, outcome, message))
    return skip


def _check_request(request: Request) -> None:
    if not isinstance(request, Request):
        raise ValueError(f"not a Request: {request!r}")
    host, info = check_request_url(request.url)
    if request.source != info.key or request.kind not in info.kinds:
        raise ValueError(f"{request.url}: source {request.source!r} kind {request.kind!r} does not belong to {host}")


def _clock_error(state: HostState, clock: dt.datetime) -> str:
    if state.last_request is not None and state.last_request - clock > CLOCK_SLACK:
        return (f"clock error: the last request to {state.host} is recorded at {utc_text(state.last_request)}, after "
                f"now ({utc_text(clock)}); no request. Check the clock, then clear last_request of that host")
    if state.cooldown_until is not None and state.cooldown_until - clock > MAX_COOLDOWN + CLOCK_SLACK:
        return (f"clock error: the cool-down of {state.host} runs until {utc_text(state.cooldown_until)}, longer "
                f"than the block rule allows after now ({utc_text(clock)}); no request")
    return ""


def _block(state: HostState, when: dt.datetime, latest: dt.datetime, why: str) -> str:
    state.blocks += 1
    state.last_block, state.last_block_reason, state.web_pages = when, why, 0
    if state.blocks >= STOP_AFTER_BLOCKS:
        state.stopped, state.cooldown_until = when, None
        return (f"blocked: {why}; {state.blocks} blocks in a row, so {state.host} is stopped until a person checks "
                "the site in a browser and resumes it")
    state.cooldown_until = resume_time(latest).astimezone(UTC)
    return (f"blocked: {why}; no request to {state.host} before {utc_text(state.cooldown_until)} (08:00 India time "
            "the next day); a block of the next request stops it")


def _answer(request: Request, info: SourceInfo, state: HostState, response: FeedResponse, when: dt.datetime,
            latest: dt.datetime, attention: list[str]) -> Exchange:
    status = response.status
    common = dict(time=when, http_status=status, wire_bytes=len(response.body),
                  etag=response.headers.get("etag", ""), last_modified=response.headers.get("last-modified", ""),
                  content_type=response.headers.get("content-type", ""))

    def failed(outcome: str, message: str) -> Exchange:
        state.errors += 1
        return Exchange(request, outcome, message, **common)

    if status is None:
        return failed("network_error", f"network error: {response.error or 'no answer'}; the next run tries again")
    unreadable = ""
    if len(response.body) > info.max_wire_bytes:
        body, unreadable = response.body, f"the answer is larger than {info.max_wire_bytes} bytes (cut off)"
    else:
        try:
            body = decode_body(response.body, response.headers, info.max_body_bytes)
        except ValueError as exc:
            body, unreadable = response.body, str(exc)
    why = refusal(body) if status != 304 else ""
    if status in BLOCK_STATUSES or why:  # a refusal page is a block whatever its status (a 503 challenge included)
        reason = f"HTTP {status}" + (f": {why}" if why else "")
        if "contact details" in why:
            state.contact_demand = f"{utc_text(when)} {request.url}"
            attention.append(f"{state.host} asks automated clients for contact details; none was given (read the "
                             "site's terms before going on)")
        return Exchange(request, "blocked", _block(state, when, latest, reason), **common)
    if 500 <= status <= 599:
        return failed("server_error", f"server error: HTTP {status}; the next run tries again")
    if status == 304:
        state.last_success, state.blocks, state.errors, state.web_pages = when, 0, 0, 0
        state.cooldown_until = None
        return Exchange(request, "not_modified", "not modified (HTTP 304)", **common)
    if 300 <= status <= 399:
        where = response.headers.get("location", "")
        return failed("http_error", f"HTTP {status}" + (f" to {where}, not followed" if where else "") +
                      "; check the address in a browser")
    if status != 200:
        attention.append(f"{request.url}: HTTP {status}, a request this package should not have made (a page limit? "
                         "a moved address?)")
        return failed("client_error", f"HTTP {status}: the request is wrong (a programming error, not a block); no "
                      f"more requests to {state.host} in this run")
    if unreadable:
        return failed("bad_answer", f"not readable: {unreadable}")
    if _looks_like_page(body, request.kind):
        state.web_pages += 1
        if state.web_pages >= 2:
            return Exchange(request, "blocked", _block(state, when, latest, "a web page in place of data, twice in a "
                            "row"), **common)
        page = parse_html(body.decode("utf-8", errors="replace"))
        state.errors += 1
        return Exchange(request, "bad_answer", f"a web page where {request.kind} data were expected "
                        f"({page.title or page.text[:60]!r}); another one in a row counts as a block", **common)
    state.last_success, state.blocks, state.errors, state.web_pages = when, 0, 0, 0
    state.cooldown_until = None
    return Exchange(request, "ok", f"HTTP 200, {len(body)} bytes", body=body, **common)
