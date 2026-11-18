"""RBI press releases on government securities: read saved pages and RSS feeds, parse the auction tables.

Nothing here downloads. Inputs are pages saved from
``https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=<id>`` and
copies of the press-release RSS feed (``pressreleases_rss.xml``, the 10
latest releases with their full tables). RBI grants no reuse licence and its
disclaimer prohibits caching without permission: ask RBI in writing before
keeping a history of these releases (``docs/rates_data_sources.md``).

**Reading.** ``read_release_page`` finds the page frame (a table whose direct
rows are a PDF size, ``Date : Nov 13, 2026``, the title and the content cell)
and takes the release id from the page's form action (``prid=63970``), or
else from a single 4-7 digit number in the file name. ``read_rss_feed`` reads
``rss/channel/item`` (``title``, ``link``, ``pubDate``, ``description``; no
guid): the release id comes from ``prid=`` in the link, the date from
``pubDate`` (no zone is given; India time is assumed), the body from the
description's HTML. Both give a ``Release``; one code path parses either.
The press-release number (``2026-2027/1492``) is read from the signature
(digits only: the GoI announcement's runs into ``ANNEX``). Data tables are
the body's innermost tables with more than one cell, picked by their labels,
never by depth or class (a page nests them four tables deep, a feed item
two). The HTML table extractor lives in ``htmlparse`` and is re-exported here
(``parse_html``, ``Table``, ``parse_number``...).

**Types.** ``classify_title`` maps a title to a kind through the registry
``RELEASE_TYPES``; ``classify`` falls back to the tables' labels when the
title is not recognised, and a parser refuses a release whose tables do not
carry its labels. Kinds with a parser (titles as observed in Nov 2026):

========================  ==============================================================
Kind                      Title
========================  ==============================================================
``gsec_result``           Government Stock - Full Auction Results
``tbill_result``          Treasury Bills: Full Auction Result
``sdl_result``            State Government Securities - Full Auction Result
``underwriting_result``   Result of Underwriting Auction conducted on November 13, 2026
``switch_result``         Result: Conversion/Switch Auction of Government of India Securities
``gsec_announcement``     Auction of Government of India Dated Securities
``tbill_announcement``    Auction of 91-Day, 182-Day and 364-Day Treasury Bills
``sdl_announcement``      Auction of State Government Securities
========================  ==============================================================

Money market operations, VRRR announcements and results, OMO results and the
issuance calendars are registered without a parser: they are reported as
skipped with that reason. A parser for any of them is one more
``register(ReleaseType(...))`` call. Other titles are skipped as not
recognised.

**Tables.** ``parse_releases(sources)`` takes saved pages and feeds (paths or
text), reads each release once (copies of a release id are tried in a fixed
order, saved pages before feed items and then by file name, whatever the
order of ``sources``; the first that parses wins, keeping a feed copy's
``published``, and later copies are listed as duplicates) and returns a
``ReleaseBatch``: one
DataFrame per kind (``COLUMNS[kind]``), the releases it skipped with a reason,
and an index of every release read. Every row carries ``release_id``,
``release_date``, ``published`` (feed items only), ``line`` (position in the
release, so ``(release_id, line)`` is a row's key), the security name as
printed, ``press_release_no`` and ``source_file`` (a file name, ``#n`` for the
n-th feed item). Units: amounts in ₹ crore of face value (``_cr``), prices per
₹100 face value, yields in percent, counts of bids (``_count``), dates as
pandas Timestamps.

* GoI and T-bill results read the "Auction Results" table: one column per
  security, sections I to XI by label. Each spanning label is expanded, so a
  section's continuation rows (``(YTM: 7.3203%)``, ``(34 bids)``) stay with
  it and the repeated ``(i) Number`` / ``(ii) Amount`` sub-rows are kept per
  section. ``total_accepted_cr`` is competitive plus non-competitive. T-bill
  non-competitive bids are outside the notified amount; GoI ones inside it.
  The page prints no ISIN, settlement or maturity date: join ``name_key`` to
  the security master (``sovereign.match_isin``) and the announcement. A new
  issue named without a coupon (``New GS 2036``) and sold at par is keyed by
  its cut-off yield (``6.85% GS 2036``); at any other price it has no key.
* SDL results read every table with a ``Notified Amount`` row; the auction
  date comes from "held on <date>". Footnote marks (``BIHAR SGS 2044*``) are
  kept in ``footnote`` with their text. A reissue's coupon and first issue
  date come from its tenor cell; a new issue's coupon is its cut-off yield.
  The ``Total`` column is a check, not a row.
* Underwriting results give the minimum underwriting commitment, the
  additional competitive underwriting accepted and the commission cut-off in
  paise per ₹100 face value.
* Switch results give one row per pair: the source security's amount bought
  back (``source_accepted_cr``) and the destination's amount issued
  (``destination_issued_cr``); they differ because the exchange is at market
  value. ``NA`` (nothing accepted) is a blank price and yield.
* Announcements give the auction and settlement dates (rowspans expanded),
  the GoI repayment date and greenshoe (``additional subscription up to
  ₹2,000 crore against each security``), T-bill maturities (settlement date
  plus the tenor, before any holiday shift of the payment), and each SDL
  line's new-issue tenor or reissued security with the payment date.

**Checks.** A release is refused, never half-read, if a value cell is not a
number, a required row is missing, or a check fails: yields outside
``SanityLimits.yield_range`` (0-20%), prices outside ``price_range`` (50-150),
percentages outside 0-100, more accepted than received, competitive plus
non-competitive not equal to an SDL's total, column sums not equal to the
SDL ``Total`` column or an announcement's Total row, a switch accepting more
than offered or notified, MUC plus ACU not equal to the amount underwritten,
or more accepted than the notified amount plus the greenshoe (GoI: total
accepted; T-bills: competitive accepted, since non-competitive bids are
outside the notified amount; SDLs: total allotment). The greenshoe is the
announced one when the GoI announcement for that auction is in the batch (or
passed as ``announcements``); otherwise accepted amounts may reach
``1 + unknown_greenshoe_ratio`` times the notified amount (2x by default:
the largest ratio in FIMMDA's 2015-2027 G-sec auction files is 2.0, ₹2,000
crore on ₹1,000 crore notified). Softer anomalies (a T-bill yield that does
not follow from its price on the tenor, a new SDL issue not priced at 100,
an unrecognised row label) are kept as notes in the release index.
"""

from __future__ import annotations

import datetime as dt
import email.utils
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from functools import cached_property
from pathlib import Path

import pandas as pd

from cr_strat.htmlparse import (  # noqa: F401  (re-exported: the HTML table extractor)
    BlockedPageError,
    Cell,
    Element,
    HtmlPage,
    Table,
    check_not_blocked,
    clean_text,
    parse_count,
    parse_date,
    parse_html,
    parse_number,
    read_text,
    squash,
)
from cr_strat.sovereign import (
    FRB,
    GREEN,
    GSEC,
    SDL,
    TBILL,
    normalise_name,
    parse_sdl_tenor,
    parse_security_name,
    state_code,
    with_par_coupon,
)

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
COMMON_HEAD = ("release_id", "release_date", "published")
COMMON_TAIL = ("press_release_no", "source_file")

_RESULT_BIDS = (
    "comp_received_count", "comp_received_cr", "cutoff_price", "cutoff_yield", "comp_accepted_count",
    "comp_accepted_cr", "partial_allotment_pct", "partial_allotment_count", "wap", "way",
    "nc_received_count", "nc_received_cr", "nc_accepted_count", "nc_accepted_cr", "nc_partial_pct",
    "total_accepted_cr", "status",
)
COLUMNS: dict[str, tuple[str, ...]] = {
    "gsec_result": (*COMMON_HEAD, "auction_date", "line", "security", "name_key", "notified_cr", "greenshoe_cr",
                    *_RESULT_BIDS, "uw_notified_cr", "uw_accepted_from_pds_cr", "devolvement_cr", *COMMON_TAIL),
    "tbill_result": (*COMMON_HEAD, "auction_date", "line", "security", "tenor_days", "name_key", "notified_cr",
                     *_RESULT_BIDS, *COMMON_TAIL),
    "sdl_result": (
        *COMMON_HEAD, "auction_date", "line", "security", "footnote", "footnote_text", "state_code",
        "maturity_year", "issue_type", "tenor_years", "coupon_pct", "first_issue_date", "name_key",
        "notified_cr", "comp_received_count", "comp_received_cr", "cutoff_yield", "cutoff_price",
        "comp_accepted_count", "comp_accepted_cr", "partial_allotment_pct", "partial_allotment_count",
        "nc_received_count", "nc_received_cr", "nc_price", "nc_accepted_count", "nc_accepted_cr",
        "nc_partial_pct", "way", "total_accepted_cr", "status", *COMMON_TAIL),
    "underwriting_result": (*COMMON_HEAD, "auction_date", "line", "security", "name_key", "notified_cr",
                            "muc_cr", "acu_accepted_cr", "total_underwritten_cr", "acu_commission_paise",
                            *COMMON_TAIL),
    "switch_result": (*COMMON_HEAD, "auction_date", "line", "source_security", "source_key",
                      "destination_security", "destination_key", "notified_cr", "offers_received_count",
                      "source_offered_cr", "offers_accepted_count", "source_accepted_cr",
                      "destination_issued_cr", "dest_cutoff_price", "dest_cutoff_yield", *COMMON_TAIL),
    "gsec_announcement": (*COMMON_HEAD, "auction_date", "settlement_date", "line", "security", "name_key",
                          "repayment_date", "notified_cr", "greenshoe_cr", "notification", "auction_method",
                          *COMMON_TAIL),
    "tbill_announcement": (*COMMON_HEAD, "auction_date", "settlement_date", "line", "security", "tenor_days",
                           "maturity_date", "notified_cr", "auction_method", *COMMON_TAIL),
    "sdl_announcement": (*COMMON_HEAD, "auction_date", "settlement_date", "line", "state", "state_code",
                         "issue_type", "tenor_years", "security", "coupon_pct", "maturity_year",
                         "first_issue_date", "name_key", "notified_cr", "auction_type", *COMMON_TAIL),
}
DATE_COLUMNS = ("release_date", "auction_date", "settlement_date", "repayment_date", "maturity_date",
                "first_issue_date", "as_on_date", "ref_date", "reversal_date", "period_start", "period_end",
                "week_start", "week_end", "quarter_start", "quarter_end", "programme_date")
INTEGER_COLUMNS = ("release_id", "line", "tenor_days", "tenor_years", "maturity_year", "seq", "week_no",
                   "allocation_no")
FLOAT_COLUMNS = ("cutoff_price", "cutoff_yield", "wap", "way", "nc_price", "dest_cutoff_price",
                 "dest_cutoff_yield", "acu_commission_paise")
INDEX_COLUMNS = ("release_id", "release_date", "published", "title", "kind", "classified_by", "status", "rows",
                 "reason", "notes", "press_release_no", "source_file")
SKIPPED_COLUMNS = ("release_id", "release_date", "title", "kind", "source_file", "reason")


class ReleaseParseError(ValueError):
    """A release's tables are not laid out as its type expects."""


class SanityCheckError(ValueError):
    """A parsed value is out of range or inconsistent, so the release is refused."""


# --- Releases ---------------------------------------------------------------------------------------

@dataclass(frozen=True, eq=False)
class Release:
    """One press release from a saved page or a feed item. ``body`` is the element holding its content."""

    release_id: int | None
    release_date: dt.date | None
    title: str
    press_release_no: str | None
    published: dt.datetime | None
    link: str | None
    source_file: str
    page: HtmlPage = field(repr=False)
    body: Element = field(repr=False)
    body_tables: tuple[Table, ...] = field(repr=False, default=())
    pdf_url: str | None = None
    from_feed: bool = False

    @property
    def data_tables(self) -> list[Table]:
        """The innermost tables of the body with more than one cell (not a one-cell wrapper), in order."""
        return [t for t in self.body_tables if t.is_leaf and sum(len(r) for r in t.rows) > 1]

    @cached_property
    def text(self) -> str:
        """All text of the body, tables included."""
        return self.body.text()

    @cached_property
    def narrative(self) -> str:
        """Body text without the data tables."""
        return self.body.text(exclude=[t.element for t in self.data_tables])


_DATE_LINE = re.compile(r"^Date\s*:\s*([A-Z][a-z]{2,8}\.? \d{1,2},? \d{4})$")
_PR_NO = re.compile(r"Press Release\s*:\s*(\d{4}-\d{4}/\d+)")
_PRID = re.compile(r"prid=(\d+)", re.I)
_RELEASE_DATE_FORMATS = ("%b %d, %Y", "%B %d, %Y", "%b %d %Y")


def _row_elements(table: Element) -> list[Element]:
    rows = []
    for child in table.children:
        if isinstance(child, Element) and child.tag == "tr":
            rows.append(child)
        elif isinstance(child, Element) and child.tag in ("thead", "tbody", "tfoot"):
            rows.extend(c for c in child.children if isinstance(c, Element) and c.tag == "tr")
    return rows


def _inside(table: Table, container: Element) -> bool:
    return any(a is container for a in table.element.ancestors())


def _press_release_no(text: str) -> str | None:
    m = _PR_NO.search(text)
    return m.group(1) if m else None


def _id_from_name(name: str) -> int | None:
    found = re.findall(r"(?<!\d)(\d{4,7})(?!\d)", Path(name).stem)
    return int(found[0]) if len(found) == 1 else None


def _release_frame(page: HtmlPage) -> tuple[Table, int] | None:
    """The table holding the 'Date : ...' line, and that line's row index."""
    for table in page.tables:
        for i, row in enumerate(table.rows[:-1]):
            if len(row) == 1 and _DATE_LINE.match(row[0].text):
                return table, i
    return None


def read_release_page(source: str | Path | bytes, *, release_id: int | None = None,
                      name: str | None = None) -> Release:
    """A release from a saved ``BS_PressReleaseDisplay.aspx`` page (path, text or bytes).

    ``name`` labels text or bytes in ``source_file``. Raises
    ``BlockedPageError`` for a firewall page and ``ReleaseParseError`` when the
    page has no release frame or no release id.
    """
    text, read_name = read_text(source)
    name = name or read_name
    page = parse_html(text)
    check_not_blocked(page, name)
    found = _release_frame(page)
    if found is None:
        raise ReleaseParseError(f"{name}: no press-release frame (a 'Date : Mon DD, YYYY' row)")
    frame, i = found
    release_date = parse_date(_DATE_LINE.match(frame.rows[i][0].text).group(1), _RELEASE_DATE_FORMATS)
    title = frame.rows[i + 1][0].text if frame.rows[i + 1] else ""
    rows = _row_elements(frame.element)
    cells = [c for c in rows[i + 2].children if isinstance(c, Element) and c.tag in ("td", "th")] \
        if i + 2 < len(rows) else []
    body = cells[0] if cells else Element("#empty", {}, None)
    pdf = None
    if i > 0:
        pdf = next((url for cell in frame.rows[i - 1] for _, url in cell.links if url.lower().endswith(".pdf")),
                   None)
    if release_id is None:
        for form in page.root.iter("form"):
            m = _PRID.search(form.attrs.get("action", ""))
            if m:
                release_id = int(m.group(1))
                break
    if release_id is None:
        release_id = _id_from_name(name)
    if release_id is None:
        raise ReleaseParseError(f"{name}: no release id (prid) in the page or the file name")
    body_tables = tuple(t for t in page.tables if _inside(t, body)) if cells else ()
    return Release(int(release_id), release_date, title, _press_release_no(body.text()), None,
                   f"https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid={release_id}", name, page,
                   body, body_tables, pdf)


@dataclass(frozen=True)
class FeedItem:
    """One ``<item>`` of the RSS feed: position (from 1), title, link, release id, publication time, body."""

    index: int
    title: str
    link: str
    release_id: int | None
    published: dt.datetime | None
    description_html: str


@dataclass(frozen=True)
class Feed:
    title: str
    link: str
    items: tuple[FeedItem, ...]
    source_file: str


def _published(text: str | None) -> dt.datetime | None:
    if not text or not text.strip():
        return None
    try:
        stamp = email.utils.parsedate_to_datetime(text.strip())
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone(IST).replace(tzinfo=None)
    return stamp


def is_feed(text: str | bytes) -> bool:
    """True if ``text`` looks like an RSS document rather than an HTML page."""
    head = text[:500].decode("utf-8", "replace") if isinstance(text, bytes) else text[:500]
    return bool(re.search(r"<rss[\s>]", head))


def read_rss_feed(source: str | Path | bytes, *, name: str | None = None) -> Feed:
    """RBI's press-release RSS feed (path, text or bytes; a byte-order mark is fine)."""
    if isinstance(source, bytes):
        raw, read_name = source, "<bytes>"
    elif isinstance(source, str) and "<" in source:
        raw, read_name = source.encode("utf-8"), "<text>"
    else:
        path = Path(source)
        raw, read_name = path.read_bytes(), path.name
    name = name or read_name
    if b"<!ENTITY" in raw:
        raise ValueError(f"{name}: XML entity declarations are not accepted")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError(f"{name}: not well-formed XML ({exc})") from None
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise ValueError(f"{name}: not an RSS 2.0 feed (root <{root.tag}>)")
    items = []
    for i, item in enumerate(channel.findall("item"), start=1):
        link = clean_text(item.findtext("link") or "")
        m = _PRID.search(link)
        items.append(FeedItem(i, clean_text(item.findtext("title") or ""), link, int(m.group(1)) if m else None,
                              _published(item.findtext("pubDate")), item.findtext("description") or ""))
    return Feed(clean_text(channel.findtext("title") or ""), clean_text(channel.findtext("link") or ""),
                tuple(items), name)


def feed_releases(feed: Feed) -> list[Release]:
    """Each feed item as a ``Release`` (body: the description's HTML)."""
    out = []
    for item in feed.items:
        page = parse_html(item.description_html)
        out.append(Release(item.release_id, item.published.date() if item.published else None, item.title,
                           _press_release_no(page.root.text()), item.published, item.link,
                           f"{feed.source_file}#{item.index}", page, page.root, page.tables, from_feed=True))
    return out


# --- Registry ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SanityLimits:
    """Bounds a parsed release must respect (see the module docstring)."""

    yield_range: tuple[float, float] = (0.0, 20.0)
    price_range: tuple[float, float] = (50.0, 150.0)
    unknown_greenshoe_ratio: float = 1.0
    amount_tolerance_cr: float = 0.002
    laf_excess_ratio: float = 0.001  # accepted over notified in a variable rate auction (pro-rata rounding)
    omo_excess_ratio: float = 0.01  # total accepted over the aggregate amount notified for an OMO
    mmo_sum_tolerance_cr: float = 0.05  # sums of the two-decimal amounts of Money Market Operations


DEFAULT_LIMITS = SanityLimits()


@dataclass
class ParseContext:
    """What a parser may use besides the release: the limits, announced greenshoes, and a note list."""

    limits: SanityLimits = DEFAULT_LIMITS
    greenshoe: dict[tuple[dt.date, str], float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


Parser = Callable[[Release, ParseContext], list[dict]]


@dataclass(frozen=True)
class ReleaseType:
    """A kind of release: a title pattern, an optional parser and the columns its rows have.

    ``detect(release)`` says whether a release's tables look like this kind;
    ``classify`` uses it when no title pattern matches. ``order`` sets the
    parsing order (announcements first, so results can use their greenshoe).
    ``no_parser`` says why a kind is registered without a parser; it becomes
    the reason such a release is skipped with.
    """

    kind: str
    title: re.Pattern
    parser: Parser | None = None
    detect: Callable[[Release], bool] | None = None
    description: str = ""
    order: int = 1
    no_parser: str = ""


RELEASE_TYPES: dict[str, ReleaseType] = {}


def register(release_type: ReleaseType) -> ReleaseType:
    """Add (or replace) a release type. Title patterns are tried in registration order."""
    if release_type.parser is not None and release_type.kind not in COLUMNS:
        raise ValueError(f"declare COLUMNS[{release_type.kind!r}] before registering its parser")
    RELEASE_TYPES[release_type.kind] = release_type
    return release_type


def classify_title(title: str) -> str:
    """Kind of a release from its title (``'other'`` when no registered pattern matches)."""
    text = clean_text(title).lower()
    for kind, release_type in RELEASE_TYPES.items():
        if release_type.title.search(text):
            return kind
    return "other"


def classify(release: Release) -> tuple[str, str]:
    """``(kind, how)``: by title, else by table labels when exactly one parser's tables match."""
    kind = classify_title(release.title)
    if kind != "other":
        return kind, "title"
    hits = [k for k, t in RELEASE_TYPES.items() if t.detect is not None and _safe_detect(t, release)]
    if len(hits) == 1:
        return hits[0], "tables"
    return "other", ""


def _safe_detect(release_type: ReleaseType, release: Release) -> bool:
    try:
        return bool(release_type.detect(release))
    except Exception:  # a detector must never stop a batch
        return False


# --- Cell helpers -----------------------------------------------------------------------------------

def _num(text: str, what: str) -> float | None:
    try:
        return parse_number(text)
    except ValueError as exc:
        raise ReleaseParseError(f"{what}: {exc}") from None


def _cnt(text: str, what: str) -> int | None:
    try:
        return parse_count(text)
    except ValueError as exc:
        raise ReleaseParseError(f"{what}: {exc}") from None


def _date(text: str, formats: tuple[str, ...], what: str) -> dt.date | None:
    try:
        return parse_date(text, formats)
    except ValueError as exc:
        raise ReleaseParseError(f"{what}: {exc}") from None


_LONG_DATE = r"([A-Z][a-z]{2,8}\.? \d{1,2},? \d{4})"
_LONG_FORMATS = ("%B %d, %Y", "%b %d, %Y", "%B %d %Y")
_YTM = re.compile(r"\(\s*YTM\s*:?\s*([\d.]+)\s*%?\s*\)", re.I)
_WAY = re.compile(r"\(\s*WAY\s*:?\s*([\d.]+)\s*%?\s*\)", re.I)
_BIDS = re.compile(r"\(\s*(\d+)\s*bids?\s*\)", re.I)
_SUB = re.compile(r"(?:iii|ii|iv|vi|v|i|[a-f])?(number|no|amount|partialallotmentpercentage|percentage)")
_SUB_FIELD = {"number": "count", "no": "count", "amount": "amount", "percentage": "pct",
              "partialallotmentpercentage": "pct"}


def _sub_label(key: str) -> str | None:
    m = _SUB.fullmatch(key)
    return _SUB_FIELD[m.group(1)] if m else None


def _find_date(pattern: str, text: str) -> dt.date | None:
    m = re.search(pattern, text, re.I | re.S)
    if not m:
        return None
    try:
        return parse_date(m.group(1).replace(".", ""), _LONG_FORMATS)
    except ValueError:
        return None


def _amount_unit_is_crore(table: Table, release: Release) -> None:
    if "crore" not in table.text.lower():
        raise ReleaseParseError(f"{release.source_file}: no '(Amount in ₹ crore)' in the table; unit unknown")


# --- Checks -----------------------------------------------------------------------------------------

def _check_range(rec: dict, fields: Iterable[str], bounds: tuple[float, float], what: str, label: str) -> None:
    lo, hi = bounds
    for f in fields:
        v = rec.get(f)
        if v is not None and not lo <= v <= hi:
            raise SanityCheckError(f"{label}: {f} {v:g} is outside the {what} range {lo:g}-{hi:g}")


def _check_not_above(rec: dict, small: str, big: str, label: str, tol: float = 1e-9) -> None:
    a, b = rec.get(small), rec.get(big)
    if a is not None and b is not None and a > b + tol:
        raise SanityCheckError(f"{label}: {small} {a:g} is above {big} {b:g}")


def _check_nonnegative(rec: dict, label: str) -> None:
    for f, v in rec.items():
        if (f.endswith("_cr") or f.endswith("_count")) and isinstance(v, (int, float)) and v < 0:
            raise SanityCheckError(f"{label}: {f} is negative ({v:g})")


def _check_bids(rec: dict, ctx: ParseContext, label: str) -> None:
    lim = ctx.limits
    _check_range(rec, ("cutoff_price", "wap", "nc_price"), lim.price_range, "price", label)
    _check_range(rec, ("cutoff_yield", "way"), lim.yield_range, "yield", label)
    _check_range(rec, ("partial_allotment_pct", "nc_partial_pct"), (0.0, 100.0), "percentage", label)
    _check_nonnegative(rec, label)
    tol = lim.amount_tolerance_cr
    _check_not_above(rec, "comp_accepted_count", "comp_received_count", label)
    _check_not_above(rec, "comp_accepted_cr", "comp_received_cr", label, tol)
    _check_not_above(rec, "nc_accepted_count", "nc_received_count", label)
    _check_not_above(rec, "nc_accepted_cr", "nc_received_cr", label, tol)
    accepted = (rec.get("comp_accepted_cr") or 0) > 0
    if accepted and rec.get("cutoff_price") is None and rec.get("cutoff_yield") is None:
        raise SanityCheckError(f"{label}: bids accepted but no cut-off price or yield")


def _check_greenshoe(amount: float | None, notified: float | None, greenshoe: float | None,
                     ctx: ParseContext, label: str, what: str) -> None:
    if amount is None or notified is None:
        return
    tol = ctx.limits.amount_tolerance_cr
    if greenshoe is not None and not pd.isna(greenshoe):
        if amount > notified + greenshoe + tol:
            raise SanityCheckError(f"{label}: {what} {amount:g} is above notified {notified:g} plus the "
                                   f"announced greenshoe {greenshoe:g}")
    elif amount > notified * (1 + ctx.limits.unknown_greenshoe_ratio) + tol:
        raise SanityCheckError(
            f"{label}: {what} {amount:g} is more than {1 + ctx.limits.unknown_greenshoe_ratio:g} times notified "
            f"{notified:g} and no announcement gives the greenshoe (pass it as announcements=)")


# --- GoI and T-bill results -------------------------------------------------------------------------

_RESULT_SECTIONS = {
    "notifiedamount": "notified",
    "competitivebidsreceived": "comp_received",
    "cutoffpriceyield": "cutoff",
    "competitivebidsaccepted": "comp_accepted",
    "partialallotmentpercentageofcompetitivebids": "partial",
    "weightedaveragepriceyield": "wa",
    "noncompetitivebidsreceived": "nc_received",
    "noncompetitivebidsaccepted": "nc_accepted",
    "underwritingnotifiedamount": "uw_notified",
    "amountofunderwritingacceptedfromprimarydealers": "uw_accepted",
    "devolvementonprimarydealers": "devolvement",
}


def _results_table(release: Release) -> tuple[Table, int]:
    for table in release.data_tables:
        for i, row in enumerate(table.rows):
            if row and squash(row[0].text) == "auctionresults" and len(row) > 1:
                return table, i
    raise ReleaseParseError(f"{release.source_file}: no table with an 'Auction Results' header row")


def _detect_results(kinds: frozenset[str]) -> Callable[[Release], bool]:
    def detect(release: Release) -> bool:
        table, h = _results_table(release)
        names = table.grid()[h][table.rows[h][0].colspan:]
        labels = {squash(r[min(1, len(r) - 1)].text) for r in table.rows if r}
        return ("notifiedamount" in labels and bool(names)
                and all(parse_security_name(n).kind in kinds for n in names))
    return detect


def _sections(table: Table, h: int, label_cols: int, n: int, release: Release,
              ctx: ParseContext) -> dict[tuple[str, str | None], list[list[str]]]:
    """Value rows of an 'Auction Results' table keyed by (section, sub-field)."""
    grid = table.grid()
    store: dict[tuple[str, str | None], list[list[str]]] = {}
    section = numeral = None
    for r in range(h + 1, len(grid)):
        raw, row = table.rows[r], grid[r]
        if not raw or (len(raw) == 1 and raw[0].colspan >= len(row)):
            continue  # a note spanning the table
        num_cell = row[0] if label_cols > 1 else ""
        label = row[label_cols - 1]
        values = row[label_cols:label_cols + n]
        key = squash(label)
        if key in _RESULT_SECTIONS and not (_RESULT_SECTIONS[key] == section and num_cell == numeral):
            section, numeral = _RESULT_SECTIONS[key], num_cell
            store[(section, None)] = [values]
        elif section is not None and key in _RESULT_SECTIONS and _RESULT_SECTIONS[key] == section:
            store[(section, None)].append(values)  # continuation row of a spanning label
        elif section is not None and _sub_label(key):
            store.setdefault((section, _sub_label(key)), []).append(values)
        else:
            ctx.notes.append(f"row {r + 1} {label!r} not recognised; ignored")
    return store


def _value_rows(store, section: str, sub: str | None, release: Release, required: bool = True):
    rows = store.get((section, sub))
    if rows is None and required:
        what = f"{section} {sub}" if sub else section
        raise ReleaseParseError(f"{release.source_file}: no '{what}' row in the results table")
    return rows or []


def _split_tagged(rows: list[list[str]], j: int, tag: re.Pattern, what: str) -> tuple[float | None, str | None]:
    """The plain number and the ``tag``-ged value of column ``j`` over a section's rows.

    RBI prints them in two rows (``97.43`` then ``(YTM: 7.3203%)``); one cell
    holding both is read the same way.
    """
    plain = tagged = None
    for row in rows:
        cell = row[j]
        m = tag.search(cell)
        if m:
            tagged = m.group(1)
            cell = cell[:m.start()] + cell[m.end():]
        if clean_text(cell):
            plain = _num(cell, what)
    return plain, tagged


def _split_price_yield(rows: list[list[str]], j: int, tag: re.Pattern, what: str) -> tuple[float | None,
                                                                                         float | None]:
    price, yld = _split_tagged(rows, j, tag, what)
    return price, (float(yld) if yld is not None else None)


def _parse_numbered_results(release: Release, ctx: ParseContext, tbill: bool) -> list[dict]:
    table, h = _results_table(release)
    _amount_unit_is_crore(table, release)
    label_cols = table.rows[h][0].colspan
    names = table.grid()[h][label_cols:]
    if not names or any(not n for n in names):
        raise ReleaseParseError(f"{release.source_file}: empty security name in the header {names}")
    expected = {TBILL} if tbill else {GSEC, GREEN, FRB}
    for name in names:
        kind = parse_security_name(name).kind
        if kind not in expected:
            raise ReleaseParseError(f"{release.source_file}: column {name!r} is not a "
                                    f"{'T-bill tenor' if tbill else 'G-sec name'}")
    store = _sections(table, h, label_cols, len(names), release, ctx)
    auction_date = release.release_date
    out = []
    for j, name in enumerate(names):
        label = f"{release.source_file} [{name}]"
        parsed = parse_security_name(name)

        def one(section, sub=None, required=True, count=False, j=j, label=label):
            rows = _value_rows(store, section, sub, release, required)
            if not rows:
                return None
            cell = rows[0][j]
            return _cnt(cell, label) if count else _num(cell, label)

        rec = {"auction_date": auction_date, "security": name, "name_key": parsed.key,
               "notified_cr": one("notified")}
        if tbill:
            rec["tenor_days"] = parsed.tenor_days
        rec["comp_received_count"] = one("comp_received", "count", count=True)
        rec["comp_received_cr"] = one("comp_received", "amount")
        rec["cutoff_price"], rec["cutoff_yield"] = _split_price_yield(
            _value_rows(store, "cutoff", None, release), j, _YTM, label)
        if not tbill:  # 'New GS 2036' sold at par: its coupon is the cut-off yield
            parsed = with_par_coupon(parsed, rec["cutoff_price"], rec["cutoff_yield"])
            rec["name_key"] = parsed.key
        rec["comp_accepted_count"] = one("comp_accepted", "count", count=True)
        rec["comp_accepted_cr"] = one("comp_accepted", "amount")
        pct, count = _split_tagged(_value_rows(store, "partial", None, release, required=False), j, _BIDS, label)
        rec["partial_allotment_pct"], rec["partial_allotment_count"] = pct, int(count) if count else None
        rec["wap"], rec["way"] = _split_price_yield(_value_rows(store, "wa", None, release, required=False), j,
                                                    _WAY, label)
        rec["nc_received_count"] = one("nc_received", "count", count=True)
        rec["nc_received_cr"] = one("nc_received", "amount")
        rec["nc_accepted_count"] = one("nc_accepted", "count", count=True)
        rec["nc_accepted_cr"] = one("nc_accepted", "amount")
        rec["nc_partial_pct"] = one("nc_accepted", "pct", required=False)
        rec["total_accepted_cr"] = (rec["comp_accepted_cr"] or 0.0) + (rec["nc_accepted_cr"] or 0.0)
        rec["status"] = "accepted" if rec["total_accepted_cr"] > 0 else "rejected"
        if not tbill:
            rec["uw_notified_cr"] = one("uw_notified", required=False)
            rec["uw_accepted_from_pds_cr"] = one("uw_accepted", required=False)
            rec["devolvement_cr"] = one("devolvement", required=False)
            rec["greenshoe_cr"] = next((ctx.greenshoe[k] for k in ((auction_date, parsed.key),
                                                                     (auction_date, normalise_name(name)))
                                        if k[1] and k in ctx.greenshoe), None)
        _check_bids(rec, ctx, label)
        if tbill:
            _check_greenshoe(rec["comp_accepted_cr"], rec["notified_cr"], None, ctx, label, "competitive accepted")
            _note_tbill_yield(rec, ctx, label)
        else:
            _check_greenshoe(rec["total_accepted_cr"], rec["notified_cr"], rec["greenshoe_cr"], ctx, label,
                             "total accepted")
            _check_not_above(rec, "devolvement_cr", "uw_notified_cr", label, ctx.limits.amount_tolerance_cr)
        out.append(rec)
    return out


def _note_tbill_yield(rec: dict, ctx: ParseContext, label: str) -> None:
    price, yld, days = rec.get("cutoff_price"), rec.get("cutoff_yield"), rec.get("tenor_days")
    if price and yld and days:
        implied = (100 / price - 1) * 365 / days * 100
        if abs(implied - yld) > 0.001:
            ctx.notes.append(f"{label}: cut-off yield {yld:g} differs from the {implied:.4f} implied by price "
                             f"{price:g} over {days} days")


def parse_gsec_result(release: Release, ctx: ParseContext | None = None) -> list[dict]:
    """Rows of a GoI dated-security full auction result (``COLUMNS['gsec_result']`` fields)."""
    return _parse_numbered_results(release, ctx or ParseContext(), tbill=False)


def parse_tbill_result(release: Release, ctx: ParseContext | None = None) -> list[dict]:
    """Rows of a T-bill full auction result (``COLUMNS['tbill_result']`` fields)."""
    return _parse_numbered_results(release, ctx or ParseContext(), tbill=True)


# --- SDL results ------------------------------------------------------------------------------------

_SDL_TOP = {
    "notifiedamount": "notified_cr", "tenor": "tenor", "cutoffyield": "cutoff_yield",
    "cutoffprice": "cutoff_price", "noncompetitiveprice": "nc_price", "weightedaverageyield": "way",
    "totalallotmentamount": "total_accepted_cr",
}
_SDL_SECTIONS = {
    "competitivebidsreceived": "comp_received", "competitivebidsaccepted": "comp_accepted",
    "partialallotmentpercentageofcompetitivebids": "partial",
    "noncompetitivebidsreceived": "nc_received", "noncompetitivebidsaccepted": "nc_accepted",
    "partialallotmentpercentageofnoncompetitivebids": "nc_partial",
}
_SDL_SUBS = {
    ("comp_received", "count"): "comp_received_count", ("comp_received", "amount"): "comp_received_cr",
    ("comp_accepted", "count"): "comp_accepted_count", ("comp_accepted", "amount"): "comp_accepted_cr",
    ("partial", "pct"): "partial_allotment_pct", ("partial", "count"): "partial_allotment_count",
    ("nc_received", "count"): "nc_received_count", ("nc_received", "amount"): "nc_received_cr",
    ("nc_accepted", "count"): "nc_accepted_count", ("nc_accepted", "amount"): "nc_accepted_cr",
    ("nc_partial", "pct"): "nc_partial_pct",
}
_SDL_REQUIRED = ("notified_cr", "tenor", "comp_received_count", "comp_received_cr", "cutoff_yield",
                 "comp_accepted_cr", "nc_received_cr", "nc_accepted_cr", "total_accepted_cr")
_SDL_TOTALLED = ("notified_cr", "comp_received_count", "comp_received_cr", "comp_accepted_count",
                 "comp_accepted_cr", "nc_received_count", "nc_received_cr", "nc_accepted_count", "nc_accepted_cr",
                 "total_accepted_cr")


def _sdl_tables(release: Release) -> list[tuple[Table, int, int]]:
    """``(table, header row, notified row)`` of each table with a 'Notified Amount' row."""
    found = []
    for table in release.data_tables:
        rows = table.rows
        for i, row in enumerate(rows):
            if row and squash(row[0].text) == "notifiedamount":
                header = next((k for k in range(i - 1, -1, -1)
                               if len(rows[k]) > 1 and not rows[k][0].text and all(c.text for c in rows[k][1:])),
                              None)
                if header is not None:
                    found.append((table, header, i))
                break
    return found


def _footnotes(release: Release) -> dict[str, str]:
    """Footnote text by mark, from rows of one cell spanning an SDL table (``*Bihar has accepted ...``)."""
    notes: dict[str, str] = {}
    for table, _, _ in _sdl_tables(release):
        for row in table.rows:
            if len(row) == 1 and row[0].colspan > 1 and row[0].text[:1] in "*#":
                for part in re.split(r"(?:^|(?<=\.)\s*)(?=[*#])", row[0].text):
                    m = re.match(r"([*#]+)\s*(.+)", part.strip())
                    if m:
                        notes[m.group(1)] = m.group(2).strip()
    return notes


def _detect_sdl(release: Release) -> bool:
    tables = _sdl_tables(release)
    return bool(tables) and all(
        parse_security_name(c.text).kind == SDL or c.text.lower() == "total"
        for table, header, _ in tables for c in table.rows[header][1:])


def parse_sdl_result(release: Release, ctx: ParseContext | None = None) -> list[dict]:
    """Rows of a state government securities full auction result (``COLUMNS['sdl_result']`` fields)."""
    ctx = ctx or ParseContext()
    tables = _sdl_tables(release)
    if not tables:
        raise ReleaseParseError(f"{release.source_file}: no SDL table with a 'Notified Amount' row")
    if "crore" not in release.text.lower():
        raise ReleaseParseError(f"{release.source_file}: no '(Amount in ₹ Crore)' line; unit unknown")
    auction_date = _find_date(r"held on " + _LONG_DATE, release.narrative)
    if auction_date is None:
        auction_date = release.release_date
        ctx.notes.append("no 'held on <date>' in the text; auction date taken as the release date")
    notes = _footnotes(release)
    columns: list[tuple[str, dict]] = []
    for table, header, _ in tables:
        grid = table.grid()
        names = grid[header][1:]
        recs = [{} for _ in names]
        section = None
        for r in range(header + 1, len(grid)):
            raw = table.rows[r]
            if not raw or (len(raw) == 1 and raw[0].colspan >= len(grid[r])):
                continue
            key = squash(grid[r][0])
            values = grid[r][1:1 + len(names)]
            if key in _SDL_SECTIONS:
                section = _SDL_SECTIONS[key]
                continue
            if key in _SDL_TOP:
                section, field_name = None, _SDL_TOP[key]
            elif section and _sub_label(key) and (section, _sub_label(key)) in _SDL_SUBS:
                field_name = _SDL_SUBS[(section, _sub_label(key))]
            else:
                ctx.notes.append(f"table {table.index} row {r + 1} {grid[r][0]!r} not recognised; ignored")
                continue
            for rec, value in zip(recs, values):
                rec[field_name] = value
        columns.extend(zip(names, recs))
    out, total = [], None
    for name, cells in columns:
        label = f"{release.source_file} [{name}]"
        if squash(name) == "total":
            total = cells
            continue
        missing = [f for f in _SDL_REQUIRED if f not in cells]
        if missing:
            raise ReleaseParseError(f"{label}: no {missing} rows")
        rec = _sdl_record(name, cells, label, auction_date, notes, ctx)
        out.append(rec)
    if not out:
        raise ReleaseParseError(f"{release.source_file}: no SDL columns")
    if total is None:
        ctx.notes.append("no Total column to check the sums against")
    else:
        for f in _SDL_TOTALLED:
            if f not in total:
                continue
            stated = _cnt(total[f], "Total") if f.endswith("_count") else _num(total[f], "Total")
            if stated is None:
                continue
            summed = sum(r[f] or 0 for r in out)
            if abs(summed - stated) > ctx.limits.amount_tolerance_cr * max(1, len(out)):
                raise SanityCheckError(f"{release.source_file}: {f} sums to {summed:g} over the securities but "
                                       f"the Total column says {stated:g}")
    return out


def _sdl_record(name: str, cells: dict, label: str, auction_date: dt.date | None, notes: dict[str, str],
                ctx: ParseContext) -> dict:
    parsed = parse_security_name(name)
    if parsed.kind != SDL:
        raise ReleaseParseError(f"{label}: column header is not an SDL name")
    if parsed.state_code is None:
        ctx.notes.append(f"{label}: state {parsed.state_name!r} has no code")
    try:
        tenor = parse_sdl_tenor(cells["tenor"])
    except ValueError as exc:
        raise ReleaseParseError(f"{label}: {exc}") from None
    rec: dict = {"auction_date": auction_date, "security": name}
    for f, value in cells.items():
        if f != "tenor":
            rec[f] = _cnt(value, label) if f.endswith("_count") else _num(value, label)
    marks = "".join(m for m in parsed.flags if m in "*#")
    rec.update(footnote=marks or None, footnote_text=" ".join(notes[m] for m in marks if m in notes) or None,
               state_code=parsed.state_code, maturity_year=parsed.maturity_year)
    if tenor.new_issue:
        rec.update(issue_type="new", tenor_years=tenor.tenor_years, coupon_pct=rec.get("cutoff_yield"),
                   first_issue_date=None)
    else:
        of = tenor.reissue_of
        if (of.state_code, of.maturity_year) != (parsed.state_code, parsed.maturity_year):
            raise ReleaseParseError(f"{label}: tenor cell names another security ({cells['tenor']!r})")
        if auction_date and of.first_issue_date and of.first_issue_date > auction_date:
            raise SanityCheckError(f"{label}: reissue of a security first issued after the auction")
        rec.update(issue_type="reissue", tenor_years=None, coupon_pct=of.coupon_pct,
                   first_issue_date=of.first_issue_date)
    rec["name_key"] = parsed.with_coupon(rec["coupon_pct"]).key
    total = rec.get("total_accepted_cr")
    rec["status"] = "accepted" if total else "rejected"
    _check_bids(rec, ctx, label)
    _check_range(rec, ("coupon_pct",), ctx.limits.yield_range, "yield", label)
    parts = (rec.get("comp_accepted_cr") or 0) + (rec.get("nc_accepted_cr") or 0)
    if total is not None and abs(parts - total) > ctx.limits.amount_tolerance_cr:
        raise SanityCheckError(f"{label}: competitive {rec.get('comp_accepted_cr')} plus non-competitive "
                               f"{rec.get('nc_accepted_cr')} is not the total allotment {total:g}")
    _check_greenshoe(total, rec.get("notified_cr"), None, ctx, label, "total allotment")
    if rec["issue_type"] == "new" and rec.get("cutoff_price") not in (None, 100.0):
        ctx.notes.append(f"{label}: new issue with cut-off price {rec['cutoff_price']:g}, not 100")
    return rec


# --- Underwriting results ---------------------------------------------------------------------------

_UW_HEADER = {
    "security": "nomenclature", "notified_cr": "notifiedamount", "muc_cr": "minimumunderwritingcommitment",
    "acu_accepted_cr": "additionalcompetitiveunderwriting", "total_underwritten_cr": "totalamountunderwritten",
    "acu_commission_paise": "acucommission",
}


def _header_columns(release: Release, wanted: dict[str, str]) -> tuple[Table, int, dict[str, int]]:
    """Leaf table, header row and column of each field, the header cells matching ``wanted`` prefixes."""
    for table in release.data_tables:
        for i, row in enumerate(table.rows):
            labels = [squash(c.text) for c in row]
            cols = {}
            for f, prefix in wanted.items():
                hits = [j for j, lab in enumerate(labels) if lab.startswith(prefix)]
                if hits:
                    cols[f] = hits[0]
            if len(cols) == len(wanted):
                return table, i, cols
    raise ReleaseParseError(f"{release.source_file}: no table with the columns {sorted(wanted.values())}")


def _detect_header(wanted: dict[str, str]) -> Callable[[Release], bool]:
    def detect(release: Release) -> bool:
        _header_columns(release, wanted)
        return True
    return detect


def parse_underwriting_result(release: Release, ctx: ParseContext | None = None) -> list[dict]:
    """Rows of an underwriting auction result (``COLUMNS['underwriting_result']`` fields)."""
    ctx = ctx or ParseContext()
    table, h, cols = _header_columns(release, _UW_HEADER)
    header = [c.text.lower() for c in table.rows[h]]
    if "crore" not in header[cols["notified_cr"]] or "paise" not in header[cols["acu_commission_paise"]]:
        raise ReleaseParseError(f"{release.source_file}: underwriting units are not crore and paise per ₹100")
    auction_date = (_find_date(r"conducted on " + _LONG_DATE, release.title)
                    or _find_date(r"conducted on " + _LONG_DATE, release.narrative)
                    or _find_date(r"will be held on " + _LONG_DATE, release.text))
    if auction_date is None:
        auction_date = release.release_date
        ctx.notes.append("no auction date in the title or text; taken as the release date")
    out = []
    grid = table.grid()
    for r in range(h + 1, len(grid)):
        raw = table.rows[r]
        if len(raw) < len(cols):
            continue  # the note row ('Auction for the sale of security will be held on ...')
        cells = grid[r]
        name = cells[cols["security"]]
        label = f"{release.source_file} [{name}]"
        parsed = parse_security_name(name)
        if parsed.kind not in (GSEC, GREEN, FRB):
            raise ReleaseParseError(f"{label}: not a G-sec name")
        rec = {"auction_date": auction_date, "security": name, "name_key": parsed.key}
        for f in ("notified_cr", "muc_cr", "acu_accepted_cr", "total_underwritten_cr", "acu_commission_paise"):
            rec[f] = _num(cells[cols[f]], label)
        _check_nonnegative(rec, label)
        _check_range(rec, ("acu_commission_paise",), (0.0, 100.0), "commission", label)
        tol = ctx.limits.amount_tolerance_cr
        if None not in (rec["muc_cr"], rec["acu_accepted_cr"], rec["total_underwritten_cr"]) and abs(
                rec["muc_cr"] + rec["acu_accepted_cr"] - rec["total_underwritten_cr"]) > tol:
            raise SanityCheckError(f"{label}: MUC plus ACU is not the total underwritten")
        _check_not_above(rec, "total_underwritten_cr", "notified_cr", label, tol)
        out.append(rec)
    if not out:
        raise ReleaseParseError(f"{release.source_file}: no security rows in the underwriting table")
    return out


# --- Switch results ---------------------------------------------------------------------------------

_SWITCH_RULES = (
    ("source_security", lambda k: k == "sourcesecurity"),
    ("destination_security", lambda k: k == "destinationsecurity"),
    ("notified_cr", lambda k: "notifiedamount" in k),
    ("offers_received_count", lambda k: "noofoffersreceived" in k),
    ("source_offered_cr", lambda k: "sourcesecurityoffered" in k),
    ("offers_accepted_count", lambda k: "noofoffersaccepted" in k),
    ("source_accepted_cr", lambda k: "sourcesecurityaccepted" in k),
    ("destination_issued_cr", lambda k: "destinationsecurityissued" in k),
    ("dest_cutoff", lambda k: "cutoffprice" in k),
)


def _switch_table(release: Release) -> tuple[Table, int, int]:
    """Table, row and label column of the 'Source Security' row."""
    for table in release.data_tables:
        grid = table.grid()
        for i, row in enumerate(grid):
            for j, cell in enumerate(row[:3]):
                if squash(cell) == "sourcesecurity":
                    return table, i, j
    raise ReleaseParseError(f"{release.source_file}: no table with a 'Source Security' row")


def _detect_switch(release: Release) -> bool:
    table, i, j = _switch_table(release)
    return any(squash(r[j]) == "destinationsecurity" for r in table.grid())


def parse_switch_result(release: Release, ctx: ParseContext | None = None) -> list[dict]:
    """Rows of a conversion/switch auction result, one per pair (``COLUMNS['switch_result']`` fields)."""
    ctx = ctx or ParseContext()
    table, i, j = _switch_table(release)
    grid = table.grid()
    sources = grid[i][j + 1:]
    while sources and not sources[-1]:
        sources.pop()
    n = len(sources)
    if not n or any(not s for s in sources):
        raise ReleaseParseError(f"{release.source_file}: empty source security in {sources}")
    if "crore" not in table.text.lower():
        raise ReleaseParseError(f"{release.source_file}: amounts not stated in crore")
    found: dict[str, list[str]] = {}
    for r, row in enumerate(grid):
        key = squash(row[j])
        for f, rule in _SWITCH_RULES:
            if rule(key):
                found.setdefault(f, row[j + 1:j + 1 + n])
                break
        else:
            if r != i and key:
                ctx.notes.append(f"row {r + 1} {row[j]!r} not recognised; ignored")
    missing = [f for f, _ in _SWITCH_RULES if f not in found]
    if missing:
        raise ReleaseParseError(f"{release.source_file}: no {missing} rows in the switch table")
    out = []
    for k in range(n):
        src, dst = found["source_security"][k], found["destination_security"][k]
        label = f"{release.source_file} [{src} -> {dst}]"
        ps, pd_ = parse_security_name(src), parse_security_name(dst)
        if ps.kind not in (GSEC, GREEN, FRB) or pd_.kind not in (GSEC, GREEN, FRB):
            raise ReleaseParseError(f"{label}: not G-sec names")
        rec = {"auction_date": release.release_date, "source_security": src, "source_key": ps.key,
               "destination_security": dst, "destination_key": pd_.key}
        for f in ("notified_cr", "source_offered_cr", "source_accepted_cr", "destination_issued_cr"):
            rec[f] = _num(found[f][k], label)
        for f in ("offers_received_count", "offers_accepted_count"):
            rec[f] = _cnt(found[f][k], label)
        cell = clean_text(found["dest_cutoff"][k])
        if "/" in cell:
            parts = [p.strip() for p in cell.split("/")]
            if len(parts) != 2:
                raise ReleaseParseError(f"{label}: cut-off price/yield {cell!r} is not 'price/yield'")
            rec["dest_cutoff_price"], rec["dest_cutoff_yield"] = _num(parts[0], label), _num(parts[1], label)
        elif _num(cell, label) is None:  # 'NA': nothing accepted
            rec["dest_cutoff_price"] = rec["dest_cutoff_yield"] = None
        else:
            raise ReleaseParseError(f"{label}: cut-off price/yield {cell!r} is not 'price/yield' or NA")
        _check_nonnegative(rec, label)
        _check_range(rec, ("dest_cutoff_price",), ctx.limits.price_range, "price", label)
        _check_range(rec, ("dest_cutoff_yield",), ctx.limits.yield_range, "yield", label)
        tol = ctx.limits.amount_tolerance_cr
        _check_not_above(rec, "offers_accepted_count", "offers_received_count", label)
        _check_not_above(rec, "source_accepted_cr", "source_offered_cr", label, tol)
        _check_not_above(rec, "source_accepted_cr", "notified_cr", label, tol)
        accepted = (rec["source_accepted_cr"] or 0) > 0
        if accepted != ((rec["destination_issued_cr"] or 0) > 0):
            raise SanityCheckError(f"{label}: source accepted {rec['source_accepted_cr']} but destination "
                                   f"issued {rec['destination_issued_cr']}")
        if accepted and rec["dest_cutoff_price"] is None:
            raise SanityCheckError(f"{label}: switch accepted but no cut-off price")
        out.append(rec)
    return out


# --- Announcements ----------------------------------------------------------------------------------

_GSEC_ANN_HEADER = {"security": "security", "repayment_date": "dateofrepayment", "notified_cr": "notifiedamount",
                    "auction_date": "auctiondate", "settlement_date": "settlementdate"}
_TBILL_ANN_HEADER = {"security": "treasurybill", "notified_cr": "notifiedamount", "auction_date": "auctiondate",
                     "settlement_date": "settlementdate"}
_SDL_ANN_HEADER = {"state": "state", "notified_cr": "amounttoberaised", "tenor": "tenor",
                   "auction_type": "typeofauction"}
_GREENSHOE = re.compile(r"additional subscription (?:of )?up to ₹\s*([\d,]+(?:\.\d+)?)\s*crore", re.I)
_METHOD = re.compile(r"\b(multiple|uniform) price (?:method|based)", re.I)


def _announcement_rows(release: Release, wanted: dict[str, str]):
    """The announcement table, its header row, its rows (rowspans expanded) as dicts, and the Total."""
    table, h, cols = _header_columns(release, wanted)
    if "crore" not in table.rows[h][cols["notified_cr"]].text.lower():
        raise ReleaseParseError(f"{release.source_file}: the amount column is not in crore "
                                f"({table.rows[h][cols['notified_cr']].text!r})")
    grid = table.grid()
    rows, total = [], None
    for r in range(h + 1, len(grid)):
        cells = {f: grid[r][c] for f, c in cols.items()}
        if not any(cells.values()):
            continue
        if any(squash(v) == "total" for v in grid[r]):
            total = _num(cells["notified_cr"], f"{release.source_file} Total row")
            continue
        rows.append((r, cells))
    if not rows:
        raise ReleaseParseError(f"{release.source_file}: no rows under the announcement header")
    return table, h, grid, rows, total


def _check_total(release: Release, records: list[dict], total: float | None, ctx: ParseContext) -> None:
    if total is None:
        return
    summed = sum(r["notified_cr"] or 0 for r in records)
    if abs(summed - total) > ctx.limits.amount_tolerance_cr:
        raise SanityCheckError(f"{release.source_file}: notified amounts sum to {summed:g} but the Total row "
                               f"says {total:g}")


def _method(release: Release) -> str | None:
    m = _METHOD.search(release.narrative)
    return m.group(1).lower() if m else None


def _payment_date(release: Release) -> dt.date | None:
    return _find_date(r"payment by successful bidders will (?:have to )?be made\b[^.]*?\bon "
                      r"(?:(?:mon|tues|wednes|thurs|fri|satur|sun)day, )?" + _LONG_DATE, release.narrative)


def parse_gsec_announcement(release: Release, ctx: ParseContext | None = None) -> list[dict]:
    """Rows of a GoI dated-securities auction announcement (``COLUMNS['gsec_announcement']`` fields)."""
    ctx = ctx or ParseContext()
    table, h, grid, rows, total = _announcement_rows(release, _GSEC_ANN_HEADER)
    notification_col = next((j for j, c in enumerate(table.rows[h]) if "notification" in c.text.lower()), None)
    m = _GREENSHOE.search(release.narrative)
    greenshoe = _num(m.group(1), "greenshoe") if m else None
    method = _method(release)
    out = []
    for r, cells in rows:
        label = f"{release.source_file} [{cells['security']}]"
        parsed = parse_security_name(cells["security"])
        if parsed.kind not in (GSEC, GREEN, FRB):
            ctx.notes.append(f"{label}: not a known G-sec name")
        rec = {
            "security": cells["security"], "name_key": parsed.key,
            "repayment_date": _date(cells["repayment_date"], ("%b %d, %Y", "%B %d, %Y", "%d-%b-%Y"), label),
            "notified_cr": _num(cells["notified_cr"], label), "greenshoe_cr": greenshoe,
            "notification": grid[r][notification_col] if notification_col is not None else None,
            "auction_date": _date(cells["auction_date"], _LONG_FORMATS, label),
            "settlement_date": _date(cells["settlement_date"], _LONG_FORMATS, label),
            "auction_method": method,
        }
        _check_dates(rec, label)
        out.append(rec)
    _check_total(release, out, total, ctx)
    paid = _payment_date(release)
    if paid and any(r["settlement_date"] != paid for r in out):
        ctx.notes.append(f"payment date in the text ({paid}) differs from the table's settlement date")
    return out


def _check_dates(rec: dict, label: str) -> None:
    a, s = rec.get("auction_date"), rec.get("settlement_date")
    if a is None or s is None:
        raise ReleaseParseError(f"{label}: no auction or settlement date")
    if s < a:
        raise SanityCheckError(f"{label}: settlement {s} before auction {a}")
    end = rec.get("repayment_date") or rec.get("maturity_date")
    if end is not None and end <= s:
        raise SanityCheckError(f"{label}: repayment {end} not after settlement {s}")
    if not rec.get("notified_cr"):
        raise SanityCheckError(f"{label}: no notified amount")


def parse_tbill_announcement(release: Release, ctx: ParseContext | None = None) -> list[dict]:
    """Rows of a T-bill auction announcement (``COLUMNS['tbill_announcement']`` fields)."""
    ctx = ctx or ParseContext()
    _, _, _, rows, total = _announcement_rows(release, _TBILL_ANN_HEADER)
    method = _method(release)
    out = []
    for _, cells in rows:
        label = f"{release.source_file} [{cells['security']}]"
        parsed = parse_security_name(cells["security"])
        if parsed.kind != TBILL:
            raise ReleaseParseError(f"{label}: not a T-bill tenor")
        settlement = _date(cells["settlement_date"], _LONG_FORMATS, label)
        rec = {
            "security": cells["security"], "tenor_days": parsed.tenor_days,
            "notified_cr": _num(cells["notified_cr"], label),
            "auction_date": _date(cells["auction_date"], _LONG_FORMATS, label), "settlement_date": settlement,
            "maturity_date": settlement + dt.timedelta(days=parsed.tenor_days) if settlement else None,
            "auction_method": method,
        }
        _check_dates(rec, label)
        out.append(rec)
    _check_total(release, out, total, ctx)
    return out


def parse_sdl_announcement(release: Release, ctx: ParseContext | None = None) -> list[dict]:
    """Rows of a state government securities auction announcement (``COLUMNS['sdl_announcement']`` fields)."""
    ctx = ctx or ParseContext()
    _, _, _, rows, total = _announcement_rows(release, _SDL_ANN_HEADER)
    text = release.narrative
    auction_date = _find_date(r"auction will be conducted on .{0,160}?\bon " + _LONG_DATE, text)
    if auction_date is None:
        raise ReleaseParseError(f"{release.source_file}: no auction date ('auction will be conducted ... on')")
    settlement = _payment_date(release)
    out = []
    for _, cells in rows:
        state = cells["state"]
        label = f"{release.source_file} [{state} {cells['tenor']}]"
        try:
            tenor = parse_sdl_tenor(cells["tenor"])
        except ValueError as exc:
            raise ReleaseParseError(f"{label}: {exc}") from None
        code = state_code(state)
        if code is None:
            ctx.notes.append(f"{label}: state {state!r} has no code")
        rec = {"auction_date": auction_date, "settlement_date": settlement, "state": state, "state_code": code,
               "notified_cr": _num(cells["notified_cr"], label), "auction_type": cells["auction_type"] or None}
        if tenor.new_issue:
            rec.update(issue_type="new", tenor_years=tenor.tenor_years, security=None, coupon_pct=None,
                       maturity_year=None, first_issue_date=None, name_key=None)
        else:
            of = tenor.reissue_of
            if code and of.state_code != code:
                raise ReleaseParseError(f"{label}: reissue cell names another state's security")
            name = re.sub(r"(?i)^re-?issue of\s+", "", re.split(r",?\s+issued on", cells["tenor"], flags=re.I)[0])
            rec.update(issue_type="reissue", tenor_years=None, security=name, coupon_pct=of.coupon_pct,
                       maturity_year=of.maturity_year, first_issue_date=of.first_issue_date, name_key=of.key)
            if of.first_issue_date and of.first_issue_date > auction_date:
                raise SanityCheckError(f"{label}: reissue of a security first issued after the auction")
        _check_range(rec, ("coupon_pct",), ctx.limits.yield_range, "yield", label)
        if not rec["notified_cr"]:
            raise SanityCheckError(f"{label}: no amount to be raised")
        out.append(rec)
    _check_total(release, out, total, ctx)
    if settlement is None:
        ctx.notes.append("no payment date in the text; settlement_date left blank")
    elif settlement < auction_date:
        raise SanityCheckError(f"{release.source_file}: payment {settlement} before the auction {auction_date}")
    return out


# --- Registration -----------------------------------------------------------------------------------

def _t(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.I)


_NOT_SAMPLED = "no page or feed item of this kind has been sampled"
_FULL_RESULT_FOLLOWS = "the full result follows the same day and has a parser; this entry records the time"

for _rt in (
    ReleaseType("gsec_announcement", _t(r"^auction of government of india dated securit(?:y|ies)$"),
                parse_gsec_announcement, _detect_header(_GSEC_ANN_HEADER), "GoI dated securities auction", 0),
    ReleaseType("tbill_announcement", _t(r"^auction of .*\btreasury bills?$"), parse_tbill_announcement,
                _detect_header(_TBILL_ANN_HEADER), "T-bill auction", 0),
    ReleaseType("sdl_announcement", _t(r"^auction of state government securities$"), parse_sdl_announcement,
                _detect_header(_SDL_ANN_HEADER), "SDL auction", 0),
    ReleaseType("gsec_result", _t(r"^government stock\s*[-:–]\s*full auction results?$"), parse_gsec_result,
                _detect_results(frozenset({GSEC, GREEN, FRB})), "GoI dated securities full auction result"),
    ReleaseType("tbill_result", _t(r"^treasury bills?\s*[-:–]\s*full auction results?$"), parse_tbill_result,
                _detect_results(frozenset({TBILL})), "T-bill full auction result"),
    ReleaseType("sdl_result", _t(r"^state government securities\s*[-:–]\s*full auction results?$"),
                parse_sdl_result, _detect_sdl, "SDL full auction result"),
    ReleaseType("underwriting_result", _t(r"^result of underwriting auction\b"), parse_underwriting_result,
                _detect_header(_UW_HEADER), "Underwriting auction result"),
    ReleaseType("switch_result", _t(r"^result\s*:?\s*(?:conversion\s*/\s*)?switch auction\b"),
                parse_switch_result, _detect_switch, "Conversion/switch auction result"),
    # Recognised, not parsed: the release index then records each event's kind and, for feed copies, its time.
    ReleaseType("gsec_cutoff", _t(r"^government stock\s*[-:–]\s*auction results?\s*:\s*cut-?off$"),
                description="GoI dated securities quick result (cut-offs)",
                no_parser=f"not sampled; {_FULL_RESULT_FOLLOWS}"),
    ReleaseType("tbill_cutoff", _t(r"\bt-bill auction results?\s*:\s*cut-?off$"),
                description="T-bill quick result (cut-offs)", no_parser=f"not sampled; {_FULL_RESULT_FOLLOWS}"),
    ReleaseType("sdl_cutoff", _t(r"^result of yield\s*/\s*price based auction of state government securities$"),
                description="SDL quick result (cut-offs)", no_parser=_FULL_RESULT_FOLLOWS),
    ReleaseType("underwriting_announcement", _t(r"^underwriting auction for sale of government securit(?:y|ies)\b"),
                description="Underwriting auction announcement",
                no_parser=f"{_NOT_SAMPLED}; the title carries the amount and the auction date, and the result "
                          "has a parser"),
    ReleaseType("switch_announcement", _t(r"^conversion\s*/\s*switch of government of india securities$"),
                description="Conversion/switch auction announcement", no_parser=_NOT_SAMPLED),
    ReleaseType("buyback_result", _t(r"^result of buyback (?:auction )?of government of india (?:dated )?securities$"),
                description="Buyback auction result", no_parser=_NOT_SAMPLED),
    ReleaseType("buyback_announcement", _t(r"^buyback of government of india (?:dated )?securities$"),
                description="Buyback auction announcement", no_parser=_NOT_SAMPLED),
):
    register(_rt)


# --- Entry point ------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SkippedRelease:
    """A release that produced no rows, and why."""

    release_id: int | None
    release_date: dt.date | None
    title: str
    kind: str
    source_file: str
    reason: str


@dataclass
class ReleaseBatch:
    """Output of ``parse_releases``: rows per kind, the releases skipped, and an index of all releases read."""

    tables: dict[str, pd.DataFrame]
    skipped: list[SkippedRelease]
    releases: pd.DataFrame

    def skipped_frame(self) -> pd.DataFrame:
        return pd.DataFrame([asdict(s) for s in self.skipped], columns=list(SKIPPED_COLUMNS))


def column_type(name: str) -> str:
    """How a table column is typed: ``'date'``, ``'time'`` (``published``), ``'int'``, ``'float'`` or ``'text'``.

    ``parse_releases`` types every table by this rule, so a reader of the CSVs
    that ``cr-strat rbi-releases`` writes can give them back the same types.
    """
    if name in DATE_COLUMNS:
        return "date"
    if name == "published":
        return "time"
    if name.endswith("_count") or name in INTEGER_COLUMNS:
        return "int"
    if name.endswith(("_cr", "_pct")) or name in FLOAT_COLUMNS:
        return "float"
    return "text"


def _no_parser_reason(kind: str) -> str:
    why = RELEASE_TYPES[kind].no_parser
    return f"no parser for {kind}: {why}" if why else f"no parser for {kind} yet"


def _frame(kind: str, records: list[dict]) -> pd.DataFrame:
    cols = list(COLUMNS[kind])
    df = pd.DataFrame(records, columns=cols)
    for c in cols:
        how = column_type(c)
        if how in ("date", "time"):
            df[c] = pd.to_datetime(df[c])
        elif how == "int":
            df[c] = df[c].astype("Int64")
        elif how == "float":
            df[c] = pd.to_numeric(df[c]).astype(float)
    if len(df):
        df = df.sort_values(["release_date", "release_id", "line"], kind="stable").reset_index(drop=True)
    return df


def _greenshoe_lookup(frame: pd.DataFrame | None) -> dict[tuple[dt.date, str], float]:
    """Greenshoe by (auction date, name key), and by (auction date, printed name) for 'New GS' lines."""
    out: dict[tuple[dt.date, str], float] = {}
    if frame is None or not len(frame):
        return out
    for _, row in frame.iterrows():
        key, gs, day, name = row.get("name_key"), row.get("greenshoe_cr"), row.get("auction_date"), row.get("security")
        if gs is None or pd.isna(gs) or day is None or pd.isna(day):
            continue
        for k in (key, normalise_name(name) if isinstance(name, str) else None):
            if isinstance(k, str) and k:
                out[(pd.Timestamp(day).date(), k)] = float(gs)
    return out


def _raw(source: str | Path | bytes, position: int) -> tuple[bytes, str]:
    if isinstance(source, bytes):
        return source, f"<bytes {position}>"
    if isinstance(source, str) and "<" in source:
        return source.encode("utf-8"), f"<text {position}>"
    path = Path(source)
    return path.read_bytes(), path.name


def read_releases(sources: Iterable[str | Path | bytes]) -> tuple[list[Release], list[SkippedRelease]]:
    """Every release in saved pages and feeds, and the inputs that hold none (blocked or unreadable pages).

    A source is a path, or the text or bytes of a page or feed; feeds are
    told from pages by their ``<rss>`` element. A missing file raises.
    """
    releases, skipped = [], []
    for position, source in enumerate(sources, start=1):
        raw, name = _raw(source, position)
        try:
            if is_feed(raw):
                releases.extend(feed_releases(read_rss_feed(raw, name=name)))
            else:
                releases.append(read_release_page(raw, name=name))
        except (BlockedPageError, ReleaseParseError, ValueError) as exc:
            skipped.append(SkippedRelease(_id_from_name(name), None, "", "unreadable", name, str(exc)))
    return releases, skipped


def parse_releases(sources: Iterable[str | Path | bytes], *, limits: SanityLimits = DEFAULT_LIMITS,
                   announcements: pd.DataFrame | None = None) -> ReleaseBatch:
    """Parse saved release pages and RSS feeds (paths or text) into one table per kind.

    Copies of one release id (the same release saved as a page and seen in a
    feed) are tried in a fixed order, whatever the order of ``sources``:
    saved pages before feed items, then by file name. The first copy that
    parses gives the rows (with the feed's ``published`` time if a feed copy
    has one); a copy that fails is skipped with its reason and the next one
    tried; copies after the parsed one are listed as duplicates.
    ``announcements``: GoI announcement rows from earlier runs
    (``COLUMNS['gsec_announcement']``), whose greenshoes bound the results;
    announcements in the batch are used too.
    """
    releases, skipped = read_releases(sources)
    index: list[dict] = [{"release_id": s.release_id, "release_date": s.release_date, "published": None,
                          "title": s.title, "kind": s.kind, "classified_by": "", "status": "skipped", "rows": 0,
                          "reason": s.reason, "notes": "", "press_release_no": None, "source_file": s.source_file}
                         for s in skipped]

    def skip(release: Release, kind: str, entry: dict, reason: str, status: str = "skipped") -> None:
        entry.update(reason=reason, status=status)
        skipped.append(SkippedRelease(release.release_id, release.release_date, release.title, kind,
                                      release.source_file, reason))

    groups: dict[int, list[tuple]] = {}
    for position, release in enumerate(releases):
        kind, how = classify(release)
        entry = {"release_id": release.release_id, "release_date": release.release_date,
                 "published": release.published, "title": release.title, "kind": kind, "classified_by": how,
                 "status": "skipped", "rows": 0, "reason": "", "notes": "",
                 "press_release_no": release.press_release_no, "source_file": release.source_file}
        index.append(entry)
        if release.release_id is None:
            skip(release, kind, entry, "no release id (prid) in the link")
            continue
        groups.setdefault(release.release_id, []).append((release.from_feed, release.source_file, position,
                                                          release, kind, how, entry))
    order = []  # announcements first (their greenshoes bound the results), then by release id
    for release_id, copies in groups.items():
        copies.sort(key=lambda c: c[:3])
        rank = min((RELEASE_TYPES[c[4]].order for c in copies
                    if c[4] in RELEASE_TYPES and RELEASE_TYPES[c[4]].parser is not None), default=99)
        order.append((rank, release_id, copies))
    greenshoe = _greenshoe_lookup(announcements)
    records: dict[str, list[dict]] = {k: [] for k, t in RELEASE_TYPES.items() if t.parser is not None}
    for _, release_id, copies in sorted(order, key=lambda x: x[:2]):
        parsed_from = None
        unparsable: dict[str, str] = {}  # kind -> the copy already skipped for it (no parser, not recognised)
        published = next((c[3].published for c in copies if c[3].published is not None), None)
        for *_, release, kind, how, entry in copies:
            if parsed_from is not None or kind in unparsable:
                skip(release, kind, entry, f"duplicate of release {release_id} read from "
                                           f"{parsed_from or unparsable[kind]}", "duplicate")
                continue
            if kind == "other" or RELEASE_TYPES[kind].parser is None:
                unparsable[kind] = release.source_file
                skip(release, kind, entry, f"title not recognised: {release.title!r}" if kind == "other"
                     else _no_parser_reason(kind))
                continue
            ctx = ParseContext(limits, greenshoe)
            if how == "tables":
                ctx.notes.append(f"title {release.title!r} not recognised; classified as {kind} from its tables")
            try:
                rows = RELEASE_TYPES[kind].parser(release, ctx)
            except (ReleaseParseError, SanityCheckError) as exc:
                word = "failed check" if isinstance(exc, SanityCheckError) else "cannot parse"
                entry["notes"] = "; ".join(ctx.notes)
                skip(release, kind, entry, f"{word}: {exc}")
                continue
            for line, row in enumerate(rows, start=1):
                row.update(release_id=release.release_id, release_date=release.release_date,
                           published=published, line=line, press_release_no=release.press_release_no,
                           source_file=release.source_file)
            records[kind].extend(rows)
            entry.update(status="parsed", rows=len(rows), notes="; ".join(ctx.notes))
            if kind == "gsec_announcement":
                greenshoe.update(_greenshoe_lookup(_frame(kind, rows)))
            parsed_from = release.source_file
    tables = {kind: _frame(kind, recs) for kind, recs in records.items()}
    idx = pd.DataFrame(index, columns=list(INDEX_COLUMNS))
    idx["release_id"] = idx["release_id"].astype("Int64")
    idx["release_date"] = pd.to_datetime(idx["release_date"])
    idx["published"] = pd.to_datetime(idx["published"])
    return ReleaseBatch(tables, skipped, idx)


# Money market operations, liquidity and open market operations and the issuance calendars are parsed in
# rbi_operations, which registers its kinds here. Importing it last, when everything it needs from this module
# exists, makes ``import cr_strat.rbi_releases`` enough to have them, whichever of the two is imported first.
from cr_strat import rbi_operations as _rbi_operations  # noqa: E402, F401
