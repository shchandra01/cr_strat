"""Sovereign security master: security names, state codes and RBI's security lists.

**Names.** ``parse_security_name(text)`` reads the names that RBI, FIMMDA and
the Handbook of Statistics print for G-secs, SDLs, T-bills, floating-rate and
green bonds into a frozen ``SecurityName``: ``kind``, ``coupon_pct``,
``maturity_year``, ``maturity_date`` (when the name has a day and month),
``state_code`` for SDLs, the ``raw`` text and ``key``, a normalised name for
joining sources (``7.73% GS 2034``, ``7.10% GOI SGRB 2028``, ``FRB 2031``,
``7.74% AP SGS 2032``, ``91DTB``). A key is not an identity: ``6.57% GS 2033``
is two ISINs. ``match_isin`` settles such cases with the maturity date or the
auction date. Text is normalised first (NBSP to space, whitespace collapsed,
upper case). Formats read, all from saved RBI and FIMMDA files:

=====================================  ===========  =================================
Format                                 Kind         Example
=====================================  ===========  =================================
``C.CC% GS YYYY``                      ``gsec``     ``7.73%  GS 2034``, ``8.13 GS 2045``
``GOVT.STOCK`` / ``GOVT STOCK`` /      ``gsec``     ``7.83% GOVT.STOCK2018``,
``Govt Stock`` / ``G.S``                            ``7.95% G.S 2032``
``NI`` / ``NEW`` before ``GS``         ``gsec``     ``7.72% NI GS 2025`` (``new_issue``)
``New GS YYYY`` (no coupon)            ``gsec``     ``New GS 2055``: coupon from the auction
``(C Align)`` / ``DDMON`` suffix       ``gsec``     ``6.01% GS 2028 (C Align)``, ``06.57% GS 2033 17AUG``
``GOI FRB`` / ``FRB`` /                ``frb``      ``GOI FLOATING RATE BOND 2024``
``GOI FLOATING RATE BOND``
``C.CC% [NEW] GOI SGrB YYYY``          ``green``    ``7.5% GOI SGrB 2056``
``CC.CC XX SGS|SDL YYYY [DD MON]``     ``sdl``      ``07.74 AP SGS 2032 03 JAN`` (FIMMDA)
``STATE SGS YYYY[*#]``                 ``sdl``      ``BIHAR SGS 2044*`` (RBI result; no coupon)
``C.CC% State SGS YYYY``               ``sdl``      ``7.58% Madhya Pradesh SGS 2038`` (RBI reissue cells)
``Re-issue of C.CC% STATE SGS YYYY``   ``sdl``      ``... issued on 29-APR-2026`` (``reissue``)
``91-Day`` / ``182-Day`` / ``364-Day`` ``tbill``    tenor only: no maturity date in the name
=====================================  ===========  =================================

Anything else is ``kind = 'unknown'`` with no key (``strict=True`` raises
``UnknownSecurityName``): no inflation-indexed, cash management bill or STRIPS
name was seen in any sample, so ``iib``, ``cmb`` and ``strips`` exist as kinds
but no rule produces them yet. Coupons are rounded to 2 decimals (``07.26`` is
7.26). A name without a coupon (``New GS 2055``, ``BIHAR SGS 2044``) has no
key until ``with_coupon`` supplies the coupon (the cut-off yield of a new
issue, or the coupon in an SDL reissue cell). SDL tenor cells (``18 -Years``,
``08``, ``Re-issue of ...``) are read by ``parse_sdl_tenor``.

**State codes.** ``STATES`` holds the 29 codes of the State List sheet in
FIMMDA's SDL auction workbook (SDL_Auction_Data_11Feb2025New.xlsx, sheet
``State List``) with the two ISIN digits after ``IN`` that each state's SDLs
carry in that workbook's rows (observed, not stated by FIMMDA). FIMMDA lists
neither Tripura nor Delhi, yet Tripura issues SDLs: ``TR`` and ``DL`` are this
package's own codes, marked so in ``source``. ``state_code`` maps a printed
state name (any case and spacing: ``UTTARPRADESH``, ``TAMILNADU``,
``CHHATTISGARH`` and FIMMDA's ``Chattisgarh``; old names ``ORISSA``,
``PONDICHERRY``, ``UTTARANCHAL``; ``KERALAM``, as RBI's state calendar of
1 Oct 2026 names Kerala) to its code. RBI's SDL results name one state two
ways in one release: the column heading spells it out (``ANDHRA PRADESH SGS
2051``) and the reissue cell below shortens it (``Re-issue of 7.68% ANDHRA
SGS 2051``, seen on 17 Nov 2026). ``ANDHRA`` is the only short form seen and
the only one in the table: a name that is not listed has no code, and is
never matched by resemblance. Compare states by code, not by spelling.

**RBI lists** (saved pages; nothing here downloads):

* ``load_goi_outstanding``: "List of Government of India Securities
  outstanding" (``bs_viewcontent.aspx?Id=1956``). One row per ISIN with
  ``as_of`` (the date in the list's title), ``isin``, ``name``,
  ``issue_date`` (first issue), ``maturity_date``, ``outstanding_cr`` (face
  value in ₹ crore, stripped amounts included, special securities excluded)
  and ``maturity_fy`` (the group heading). The page must have a Total row,
  the rows must sum to it and every row must sit under the financial year
  of its maturity, or the page is refused. The page is re-dated daily and lists live securities only:
  keep each day's copy; it cannot backfill history.
* ``load_far_list``: the Fully Accessible Route "Specified securities" list
  (``Id=3876``), outstanding and matured, undated.
* ``read_sdl_outstanding_index``: the SDL list page (``Id=4433``) holds only
  the as-of date and links to an XLS and a PDF on rbidocs; it returns those
  and never a table.
* ``check_not_blocked`` (from ``htmlparse``) refuses a saved firewall page such
  as RBI's "Unauthorised Access ... Support ID" reply (the saved STRIPS list).

**Master.** ``build_master(goi_lists, far, extra)`` gives one row per ISIN
(``MASTER_COLUMNS``): the parsed name fields, dates, the FAR flag, the latest
outstanding stock and the snapshots it appears in, and its sources. ISINs are
checked with ``isin.is_valid_isin`` and an invalid one is refused. Conflicting
dates between sources, a name whose year differs from the maturity date, and
an ISIN whose issuer digits disagree with the name are listed in
``conflicts``; the GoI list wins over the FAR list, which wins over ``extra``.
Characters 5 to 8 of these ISINs are the financial year of first issue
(``isin_fy``); ``isin_fy_matches`` flags the exceptions (IN0020020247).

``match_isin(master, name, on=..., maturity=...)`` finds the ISIN behind a
printed name (``attach_isin`` does a whole table): the candidates sharing its
key, narrowed by the maturity date (or the name's own day and month) and by
the date of the auction or trade (first issued no more than
``SETTLEMENT_SLACK_DAYS`` after it, since a new issue settles after its
auction, and not matured). One candidate is a match; ``none`` and
``ambiguous`` are reported, never guessed.

RBI's disclaimer grants no reuse licence and prohibits caching without
permission: ask RBI in writing before keeping a history of these lists
(``docs/rates_data_sources.md``).
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, replace
from pathlib import Path

import pandas as pd

from cr_strat.htmlparse import (
    HtmlPage,
    Table,
    check_not_blocked,
    clean_text,
    parse_date,
    parse_html,
    parse_number,
    read_text,
    squash,
)
from cr_strat.isin import is_valid_isin

GSEC, FRB, IIB, GREEN, SDL, TBILL, CMB, STRIPS, UNKNOWN = (
    "gsec", "frb", "iib", "green", "sdl", "tbill", "cmb", "strips", "unknown")
KINDS = (GSEC, FRB, IIB, GREEN, SDL, TBILL, CMB, STRIPS, UNKNOWN)
CENTRAL_KINDS = frozenset({GSEC, FRB, IIB, GREEN, TBILL, CMB, STRIPS})
GOI_SOURCE = "rbi_goi_outstanding"
FAR_SOURCE = "rbi_far"

GOI_LIST_COLUMNS = (
    "as_of", "sl_no", "isin", "name", "issue_date", "maturity_date", "outstanding_cr", "maturity_fy", "source_file")
FAR_COLUMNS = ("sl_no", "isin", "name", "issue_date", "maturity_date", "source_file")
MASTER_COLUMNS = (
    "isin", "name", "name_key", "kind", "coupon_pct", "maturity_year", "maturity_date", "issue_date",
    "state_code", "far", "far_name", "in_latest_list", "outstanding_cr", "outstanding_as_of",
    "first_listed", "last_listed", "matured", "isin_fy", "isin_fy_matches", "sources", "conflicts",
)

_MONTHS = {m: i for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), start=1)}


class UnknownSecurityName(ValueError):
    """A security name matches no known format (``parse_security_name(..., strict=True)``)."""


# --- States -----------------------------------------------------------------------------------------

FIMMDA_STATE_LIST = ("FIMMDA SDL auction workbook SDL_Auction_Data_11Feb2025New.xlsx, sheet 'State List'; "
                     "ISIN digits observed in its data rows")
LOCAL_CODE = "cr_strat's own code: not in FIMMDA's State List"


@dataclass(frozen=True)
class State:
    """A state or union territory that issues SDLs: ``code``, ``name``, ISIN digits and the code's source."""

    code: str
    name: str
    isin_prefix: str | None
    source: str


STATES: tuple[State, ...] = tuple(State(c, n, p, FIMMDA_STATE_LIST) for c, n, p in (
    ("AP", "Andhra Pradesh", "10"), ("AR", "Arunachal Pradesh", "11"), ("AS", "Assam", "12"),
    ("BR", "Bihar", "13"), ("CG", "Chattisgarh", "35"), ("GA", "Goa", "14"), ("GJ", "Gujarat", "15"),
    ("HR", "Haryana", "16"), ("HP", "Himachal Pradesh", "17"), ("JH", "Jharkhand", "37"),
    ("KA", "Karnataka", "19"), ("KL", "Kerala", "20"), ("MP", "Madhya Pradesh", "21"),
    ("MH", "Maharashtra", "22"), ("MN", "Manipur", "23"), ("ML", "Meghalaya", "24"), ("MZ", "Mizoram", "25"),
    ("NL", "Nagaland", "26"), ("OD", "Odisha", "27"), ("PN", "Punjab", "28"), ("RJ", "Rajasthan", "29"),
    ("SK", "Sikkim", "30"), ("TN", "Tamil Nadu", "31"), ("TS", "Telangana", "45"),
    ("UK", "Uttarakhand", "36"), ("UP", "Uttar Pradesh", "33"), ("WB", "West Bengal", "34"),
    ("PY", "Puducherry", "38"), ("JK", "Jammu and Kashmir", "49"),
)) + (State("TR", "Tripura", None, LOCAL_CODE), State("DL", "Delhi", None, LOCAL_CODE))
STATE_BY_CODE = {s.code: s for s in STATES}
# Other spellings: RBI's CHHATTISGARH (FIMMDA's list has Chattisgarh), three old names, the short ANDHRA of the
# reissue cells in RBI's SDL result of 17 Nov 2026, and KERALAM, as RBI's state calendar of 1 Oct 2026 names Kerala.
_STATE_ALIASES = {"CHHATTISGARH": "CG", "ORISSA": "OD", "PONDICHERRY": "PY", "UTTARANCHAL": "UK", "ANDHRA": "AP",
                  "KERALAM": "KL"}
_STATE_BY_NAME = {re.sub(r"[^A-Z]", "", s.name.upper()): s.code for s in STATES} | _STATE_ALIASES
_STATE_BY_PREFIX = {s.isin_prefix: s.code for s in STATES if s.isin_prefix}


def state_code(name: str) -> str | None:
    """Code of a state printed as ``name`` (case, spaces, ``&`` and dots ignored); ``None`` if unknown."""
    text = clean_text(name).upper().replace("&", "AND")
    if text in STATE_BY_CODE:
        return text
    return _STATE_BY_NAME.get(re.sub(r"[^A-Z]", "", text))


def state_for_isin(isin: str) -> str | None:
    """Issuer of an Indian sovereign ISIN from digits 3-4: ``'GOI'`` for ``00``, else a state code or ``None``."""
    isin = str(isin).strip().upper()
    if not re.fullmatch(r"IN\d{2}.{8}", isin):
        return None
    prefix = isin[2:4]
    return "GOI" if prefix == "00" else _STATE_BY_PREFIX.get(prefix)


def isin_fy(isin: str) -> int | None:
    """Financial year (its first calendar year) of first issue in characters 5-8 of a sovereign ISIN."""
    isin = str(isin).strip().upper()
    return int(isin[4:8]) if re.fullmatch(r"IN\d{6}.{4}", isin) else None


def financial_year(day: dt.date) -> int:
    """First calendar year of the April-March financial year holding ``day``."""
    return day.year if day.month >= 4 else day.year - 1


# --- Names ------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SecurityName:
    """A parsed security name. ``key`` is ``None`` when the name lacks what a key needs (a coupon)."""

    raw: str
    kind: str = UNKNOWN
    coupon_pct: float | None = None
    maturity_year: int | None = None
    maturity_date: dt.date | None = None
    state_code: str | None = None
    key: str | None = None
    new_issue: bool = False
    reissue: bool = False
    first_issue_date: dt.date | None = None
    tenor_days: int | None = None
    flags: tuple[str, ...] = ()
    state_name: str | None = None

    def with_coupon(self, coupon_pct: float | None) -> SecurityName:
        """This name with ``coupon_pct`` filled in (and its key built); unchanged if it has a coupon."""
        if self.coupon_pct is not None or coupon_pct is None or pd.isna(coupon_pct):
            return self
        return replace(self, coupon_pct=round(float(coupon_pct), 2)).rekeyed()

    def rekeyed(self) -> SecurityName:
        return replace(self, key=_key(self))


def _key(n: SecurityName) -> str | None:
    if n.kind == TBILL:
        return f"{n.tenor_days}DTB" if n.tenor_days else None
    if n.kind == FRB:
        return f"FRB {n.maturity_year}"
    if n.coupon_pct is None or n.maturity_year is None:
        return None
    if n.kind == GSEC:
        suffix = " (C ALIGN)" if "(C ALIGN)" in n.flags else ""
        return f"{n.coupon_pct:.2f}% GS {n.maturity_year}{suffix}"
    if n.kind == GREEN:
        return f"{n.coupon_pct:.2f}% GOI SGRB {n.maturity_year}"
    if n.kind == SDL and n.state_code:
        return f"{n.coupon_pct:.2f}% {n.state_code} SGS {n.maturity_year}"
    return None


_COUPON = r"(\d{1,2}(?:\.\d{1,2})?)"
_RULES = (
    ("tbill", re.compile(r"(91|182|364) ?- ?DAYS?")),
    ("frb", re.compile(r"(?:GOI )?(?:FRB|FLOATING RATE BONDS?) ?(\d{4})")),
    ("green", re.compile(_COUPON + r"% (?:(NEW|NI) )?GOI SGRB (\d{4})")),
    ("gsec", re.compile(_COUPON + r"%? ?(?:(NEW|NI) )?(?:GS|G\.S\.?|GOVT\.? ?STOCK) ?(\d{4})"
                        r"(?: (\(C ALIGN\)|\d{2} ?[A-Z]{3}))?")),
    ("gsec_new", re.compile(r"NEW GS (\d{4})")),
    ("sdl_fimmda", re.compile(r"(\d{1,2}\.\d{2}) ([A-Z]{2}) (?:SGS|SDL) (\d{4})(?: (\d{2}) ([A-Z]{3}))?")),
    ("sdl_reissue", re.compile(r"RE-?ISSUE OF " + _COUPON + r"% ([A-Z][A-Z .&]*?) (?:SGS|SDL) (\d{4}),? "
                               r"ISSUED ON (.+)")),
    ("sdl_named", re.compile(_COUPON + r"% ([A-Z][A-Z .&]*?) (?:SGS|SDL) (\d{4})")),
    ("sdl_rbi", re.compile(r"([A-Z][A-Z .&]*?) (?:SGS|SDL) (\d{4}) ?([*#]+)?")),
)
_ISSUE_DATE_FORMATS = ("%d-%b-%Y", "%B %d, %Y", "%b %d, %Y", "%d %B %Y")


def normalise_name(text: object) -> str:
    """Upper case, NBSP as space, whitespace collapsed: the text every name rule matches."""
    return clean_text("" if text is None else str(text)).upper()


def _day_month(year: int, day: str, month: str) -> dt.date | None:
    if month not in _MONTHS:
        return None
    try:
        return dt.date(year, _MONTHS[month], int(day))
    except ValueError:
        return None


def parse_security_name(text: object, *, strict: bool = False) -> SecurityName:
    """Parse a printed security name (see the module docstring for the formats).

    Returns ``kind = 'unknown'`` for anything else, or raises
    ``UnknownSecurityName`` with ``strict=True``. An SDL whose state is not in
    ``STATES`` keeps ``kind = 'sdl'`` with ``state_code`` and ``key`` ``None``
    and the flag ``unknown_state`` (``strict=True`` raises).
    """
    raw = "" if text is None else str(text)
    u = normalise_name(raw)
    for rule, pattern in _RULES:
        m = pattern.fullmatch(u)
        if m:
            name = _from_match(raw, rule, m)
            if name is not None:
                if strict and "unknown_state" in name.flags:
                    raise UnknownSecurityName(f"unknown state in SDL name {raw!r}")
                return name
    if strict:
        raise UnknownSecurityName(f"unrecognised security name {raw!r}")
    return SecurityName(raw)


def _from_match(raw: str, rule: str, m: re.Match) -> SecurityName | None:
    g = m.groups()
    if rule == "tbill":
        return SecurityName(raw, TBILL, tenor_days=int(g[0])).rekeyed()
    if rule == "frb":
        return SecurityName(raw, FRB, maturity_year=int(g[0])).rekeyed()
    if rule == "green":
        return SecurityName(raw, GREEN, round(float(g[0]), 2), int(g[2]), new_issue=g[1] is not None).rekeyed()
    if rule == "gsec":
        year, suffix, flags, maturity = int(g[2]), g[3], (), None
        if suffix == "(C ALIGN)":
            flags = (suffix,)
        elif suffix:
            maturity = _day_month(year, suffix[:2], suffix[-3:])
            if maturity is None:
                return None
        return SecurityName(raw, GSEC, round(float(g[0]), 2), year, maturity, new_issue=g[1] is not None,
                            flags=flags).rekeyed()
    if rule == "gsec_new":
        return SecurityName(raw, GSEC, None, int(g[0]), new_issue=True)
    if rule == "sdl_fimmda":
        code, year = g[1], int(g[2])
        maturity = None
        if g[3]:
            maturity = _day_month(year, g[3], g[4])
            if maturity is None:
                return None
        known = code in STATE_BY_CODE
        return SecurityName(raw, SDL, round(float(g[0]), 2), year, maturity, code if known else None,
                            flags=() if known else ("unknown_state",), state_name=code).rekeyed()
    if rule == "sdl_reissue":
        try:
            first = parse_date(g[3], _ISSUE_DATE_FORMATS)
        except ValueError:
            return None
        code = state_code(g[1])
        return SecurityName(raw, SDL, round(float(g[0]), 2), int(g[2]), None, code, reissue=True,
                            first_issue_date=first, flags=() if code else ("unknown_state",),
                            state_name=g[1].strip()).rekeyed()
    if rule == "sdl_named":
        code = state_code(g[1])
        return SecurityName(raw, SDL, round(float(g[0]), 2), int(g[2]), None, code,
                            flags=() if code else ("unknown_state",), state_name=g[1].strip()).rekeyed()
    if rule == "sdl_rbi":
        code = state_code(g[0])
        flags = tuple(g[2]) if g[2] else ()
        if code is None:
            flags += ("unknown_state",)
        return SecurityName(raw, SDL, None, int(g[1]), None, code, flags=flags, state_name=g[0].strip())
    return None  # pragma: no cover


def name_key(text: object) -> str | None:
    """``parse_security_name(text).key``."""
    return parse_security_name(text).key


def with_par_coupon(name: SecurityName, price, yield_pct) -> SecurityName:
    """A coupon-less new GoI issue (``New GS 2036``) keyed by its cut-off yield, if it was sold at par.

    A new issue's coupon is its cut-off yield, so a price of 100 and a yield
    give the key (``6.85% GS 2036``). At any other price, or without both,
    the name is returned unchanged (no key).
    """
    if name.key is not None or name.kind != GSEC or not name.new_issue:
        return name
    try:
        if price is None or yield_pct is None or pd.isna(price) or pd.isna(yield_pct):
            return name
        if abs(float(price) - 100.0) > 1e-9:
            return name
    except (TypeError, ValueError):
        return name
    return name.with_coupon(float(yield_pct))


@dataclass(frozen=True)
class SdlTenor:
    """An SDL tenor cell: a new issue's tenor in years, or the security a reissue adds to."""

    raw: str
    new_issue: bool
    tenor_years: int | None = None
    reissue_of: SecurityName | None = None


def parse_sdl_tenor(text: object) -> SdlTenor:
    """Read ``18 -Years`` or ``08`` (new issue) or ``Re-issue of 7.83% ASSAM SGS 2046 issued on ...``.

    Raises ``ValueError`` for anything else.
    """
    raw = "" if text is None else str(text)
    u = normalise_name(raw)
    m = re.fullmatch(r"(\d{1,2}) ?- ?YEARS?|(\d{1,2})", u)
    if m:
        return SdlTenor(raw, True, int(m.group(1) or m.group(2)))
    name = parse_security_name(raw)
    if name.kind == SDL and name.reissue:
        return SdlTenor(raw, False, None, name)
    raise ValueError(f"not an SDL tenor or reissue cell: {raw!r}")


def add_name_fields(frame: pd.DataFrame, column: str = "security", prefix: str = "") -> pd.DataFrame:
    """Copy of ``frame`` with the parsed fields of ``column``'s names appended.

    Adds ``kind``, ``coupon_pct``, ``maturity_year``, ``name_maturity_date``,
    ``state_code`` and ``name_key``, each with ``prefix``.
    """
    parsed = [parse_security_name(v) for v in frame[column]]
    out = frame.copy()
    out[prefix + "kind"] = [p.kind for p in parsed]
    out[prefix + "coupon_pct"] = [p.coupon_pct for p in parsed]
    out[prefix + "maturity_year"] = pd.array([p.maturity_year for p in parsed], dtype="Int64")
    out[prefix + "name_maturity_date"] = pd.to_datetime([p.maturity_date for p in parsed])
    out[prefix + "state_code"] = [p.state_code for p in parsed]
    out[prefix + "name_key"] = [p.key for p in parsed]
    return out


# --- RBI list pages ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class ListPage:
    """The frame of an RBI list page: title, as-of date and the page's own XLS/PDF attachments."""

    title: str
    as_of: dt.date | None
    attachments: tuple[tuple[str, str], ...]
    source: str

    def attachment(self, extension: str) -> str | None:
        """URL of the first attachment whose path ends in ``extension`` (``.xls``, ``.pdf``)."""
        for _, url in self.attachments:
            if url.lower().split("?")[0].endswith(extension.lower()):
                return url
        return None

    @property
    def xls_url(self) -> str | None:
        return self.attachment(".xls") or self.attachment(".xlsx")

    @property
    def pdf_url(self) -> str | None:
        return self.attachment(".pdf")


_SIZE_ROW = re.compile(r"^\(\s*\d+(?:\.\d+)?\s*kb\s*\)", re.I)
_AS_ON = re.compile(r"\bas on ([A-Z][a-z]+\.? \d{1,2},? \d{4})")
_ATTACHMENT_EXT = (".xls", ".xlsx", ".pdf", ".csv", ".zip")


def _frame(page: HtmlPage, name: str) -> tuple[Table, int]:
    """The list page's frame table and the index of its title row (the row after the file-size row)."""
    for table in page.tables:
        for i, row in enumerate(table.rows[:-1]):
            if len(row) == 1 and _SIZE_ROW.match(row[0].text):
                return table, i + 1
    raise ValueError(f"{name}: no RBI list frame (a '(NN kb)' attachment row followed by a title row)")


def read_list_page(source: str | Path | bytes) -> tuple[ListPage, HtmlPage]:
    """The frame of a saved RBI list page and the parsed page; refuses firewall pages."""
    text, name = read_text(source)
    page = parse_html(text)
    check_not_blocked(page, name)
    table, title_row = _frame(page, name)
    title = table.rows[title_row][0].text
    m = _AS_ON.search(title)
    as_of = parse_date(m.group(1).replace(".", ""), ("%B %d, %Y", "%b %d, %Y", "%B %d %Y")) if m else None
    links = tuple((label, url) for cell in table.rows[title_row - 1] for label, url in cell.links
                  if url.lower().split("?")[0].endswith(_ATTACHMENT_EXT))
    return ListPage(title, as_of, links, name), page


def _find_header(page: HtmlPage, wanted: dict[str, str], name: str) -> tuple[Table, int, dict[str, int]]:
    """The leaf table and row whose cells start with each label in ``wanted`` (squashed prefixes)."""
    for table in page.leaf_tables():
        for i, row in enumerate(table.rows):
            labels = [squash(c.text) for c in row]
            cols: dict[str, int] = {}
            for field_name, prefix in wanted.items():
                hits = [j for j, label in enumerate(labels) if label.startswith(prefix)]
                if hits:
                    cols[field_name] = hits[0]
            if len(cols) == len(wanted):
                return table, i, cols
    raise ValueError(f"{name}: no table with the columns {list(wanted.values())}")


def _fy_label(fy: int) -> str:
    return f"{fy}-{(fy + 1) % 100:02d}"


_GOI_HEADER = {"sl_no": "slno", "isin": "isin", "name": "nomenclature", "issue_date": "dateofissue",
               "maturity_date": "dateofmaturity", "outstanding": "outstandingstock"}


def load_goi_outstanding(source: str | Path | bytes, *, require_total: bool = True) -> pd.DataFrame:
    """RBI's list of GoI securities outstanding (saved page) as ``GOI_LIST_COLUMNS``.

    ``attrs`` holds ``as_of``, ``total_cr`` (the Total row), ``title``, ``xls_url``
    and ``pdf_url``. Raises ``ValueError`` if the page is not that list, an
    amount is not in crore, a row cannot be read, an ISIN appears twice, a
    row sits under the wrong maturity year, or the rows do not sum to the
    Total. A page with no Total row (a truncated or partial save) is refused
    too, since nothing shows that every security is in it; ``require_total=
    False`` loads it anyway (``total_cr`` is then ``None``).
    """
    frame, page = read_list_page(source)
    name = frame.source
    if frame.as_of is None:
        raise ValueError(f"{name}: no 'as on <date>' in the list title {frame.title!r}")
    table, header, cols = _find_header(page, _GOI_HEADER, name)
    unit = table.rows[header][cols["outstanding"]].text
    if "crore" not in unit.lower():
        raise ValueError(f"{name}: outstanding stock is not in crore ({unit!r})")
    width = max(cols.values()) + 1
    records, total, group = [], None, None
    for r, row in enumerate(table.rows[header + 1:], start=header + 2):
        cells = [c.text for c in row]
        if len(cells) < width:
            continue  # footnote rows span the table
        sl, isin = cells[cols["sl_no"]], cells[cols["isin"]].upper()
        if sl.lower() == "total":
            total = parse_number(cells[cols["outstanding"]])
            continue
        if re.fullmatch(r"\d+", sl) and isin:
            try:
                rec = {
                    "as_of": frame.as_of, "sl_no": int(sl), "isin": isin, "name": cells[cols["name"]],
                    "issue_date": parse_date(cells[cols["issue_date"]], ("%d-%b-%Y",)),
                    "maturity_date": parse_date(cells[cols["maturity_date"]], ("%d-%b-%Y",)),
                    "outstanding_cr": parse_number(cells[cols["outstanding"]]),
                    "maturity_fy": group, "source_file": name,
                }
            except ValueError as exc:
                raise ValueError(f"{name} row {r}: {exc}") from None
            if rec["maturity_date"] is None or rec["outstanding_cr"] is None:
                raise ValueError(f"{name} row {r}: no maturity date or outstanding amount for {isin}")
            if group is not None and _fy_label(financial_year(rec["maturity_date"])) != group:
                raise ValueError(f"{name} row {r}: {isin} matures {rec['maturity_date']} but sits under {group}")
            records.append(rec)
            continue
        others = [c for j, c in enumerate(cells) if j != cols["name"]]
        if re.fullmatch(r"\d{4}-\d{2}", cells[cols["name"]]) and not any(others):
            group = cells[cols["name"]]
    df = pd.DataFrame(records, columns=list(GOI_LIST_COLUMNS))
    if df.empty:
        raise ValueError(f"{name}: no security rows under the header")
    dupes = sorted(df.loc[df["isin"].duplicated(), "isin"])
    if dupes:
        raise ValueError(f"{name}: ISINs listed twice: {dupes}")
    if total is None and require_total:
        raise ValueError(f"{name}: no Total row to check the {len(df)} security rows against (a truncated or "
                         "partial save?); pass require_total=False to load it anyway")
    if total is not None and abs(df["outstanding_cr"].sum() - total) > 0.001:
        raise ValueError(f"{name}: rows sum to {df['outstanding_cr'].sum():.3f} crore but the Total row "
                         f"says {total:.3f}")
    for col in ("as_of", "issue_date", "maturity_date"):
        df[col] = pd.to_datetime(df[col])
    df.attrs.update(as_of=frame.as_of, total_cr=total, title=frame.title, xls_url=frame.xls_url,
                    pdf_url=frame.pdf_url)
    return df


_FAR_HEADER = {"sl_no": "sno", "isin": "isin", "name": "securitydescription", "issue_date": "dateofissue",
               "maturity_date": "dateofmaturity"}


def load_far_list(source: str | Path | bytes) -> pd.DataFrame:
    """RBI's list of FAR "Specified securities" (saved page) as ``FAR_COLUMNS``. The page has no date."""
    frame, page = read_list_page(source)
    name = frame.source
    table, header, cols = _find_header(page, _FAR_HEADER, name)
    width = max(cols.values()) + 1
    records = []
    for r, row in enumerate(table.rows[header + 1:], start=header + 2):
        cells = [c.text for c in row]
        if len(cells) < width or not re.fullmatch(r"\d+", cells[cols["sl_no"]]):
            continue
        try:
            records.append({
                "sl_no": int(cells[cols["sl_no"]]), "isin": cells[cols["isin"]].upper(),
                "name": cells[cols["name"]],
                "issue_date": parse_date(cells[cols["issue_date"]], ("%d %B %Y",)),
                "maturity_date": parse_date(cells[cols["maturity_date"]], ("%d %B %Y",)),
                "source_file": name,
            })
        except ValueError as exc:
            raise ValueError(f"{name} row {r}: {exc}") from None
    df = pd.DataFrame(records, columns=list(FAR_COLUMNS))
    if df.empty:
        raise ValueError(f"{name}: no security rows under the header")
    dupes = sorted(df.loc[df["isin"].duplicated(), "isin"])
    if dupes:
        raise ValueError(f"{name}: ISINs listed twice: {dupes}")
    for col in ("issue_date", "maturity_date"):
        df[col] = pd.to_datetime(df[col])
    df.attrs.update(title=frame.title, pdf_url=frame.pdf_url)
    return df


def read_sdl_outstanding_index(source: str | Path | bytes) -> ListPage:
    """As-of date and XLS/PDF links of RBI's SDL outstanding page; the data are only in those files."""
    frame, _ = read_list_page(source)
    if "state government securities" not in frame.title.lower():
        raise ValueError(f"{frame.source}: not the SDL outstanding list ({frame.title!r})")
    return frame


# --- Master -----------------------------------------------------------------------------------------

def _frames(value) -> list[pd.DataFrame]:
    if value is None:
        return []
    if isinstance(value, pd.DataFrame):
        return [value]
    return [f for f in value if f is not None]


def _dates(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="raise").dt.normalize()


def _check_isins_present(df: pd.DataFrame, what: str) -> None:
    """Refuse a listing with rows that have a name but no ISIN, naming them (an ISIN is the master's key)."""
    if "isin" not in df:
        return
    blank = df["isin"].isna() | df["isin"].astype(str).str.strip().isin(["", "nan", "None", "NaN"])
    if blank.any():
        rows = df[blank]
        names = rows["name"].tolist() if "name" in df else [None] * len(rows)
        listed = ", ".join(f"row {i} ({n!r})" for i, n in zip(rows.index[:10], names[:10]))
        raise ValueError(f"{what}: no ISIN in {int(blank.sum())} row(s): {listed}; fill or remove them")


def _listing(df: pd.DataFrame, source: str | None) -> pd.DataFrame:
    missing = [c for c in ("isin", "name") if c not in df]
    if missing:
        raise ValueError(f"listing lacks columns {missing}")
    out = pd.DataFrame({"isin": df["isin"].astype(str).str.strip().str.upper(), "name": df["name"].astype(str)})
    for col in ("issue_date", "maturity_date"):
        out[col] = _dates(df[col]) if col in df else pd.NaT
    if source is not None:
        out["source"] = source
    elif "source" in df:
        out["source"] = df["source"].astype(str)
    else:
        out["source"] = "extra"
    for col in ("as_of", "outstanding_cr"):
        if col in df:
            out[col] = _dates(df[col]) if col == "as_of" else pd.to_numeric(df[col])
    return out


def build_master(goi_lists=None, far: pd.DataFrame | None = None, extra=None, *,
                 as_of=None) -> pd.DataFrame:
    """One row per ISIN from GoI-outstanding snapshots, the FAR list and other listings (``MASTER_COLUMNS``).

    ``goi_lists``: frames from ``load_goi_outstanding`` (one per date, or
    stacked); the latest snapshot holding an ISIN gives its outstanding stock.
    ``far``: a frame from ``load_far_list``. ``extra``: frames with at least
    ``isin`` and ``name`` (optional ``issue_date``, ``maturity_date``,
    ``source``), e.g. SDLs from another file. ``matured`` compares the
    maturity date with ``as_of`` (default: the latest snapshot's date).
    Raises ``ValueError`` on an invalid ISIN.
    """
    goi = [_listing(f, GOI_SOURCE) for f in _frames(goi_lists)]
    goi = [g for g in goi if len(g)]
    if goi and any("as_of" not in g for g in goi):
        raise ValueError("GoI list frames need the as_of column load_goi_outstanding gives")
    far_rows = _listing(far, FAR_SOURCE) if far is not None and len(far) else None
    extras = []
    for n, frame in enumerate(_frames(extra), start=1):
        _check_isins_present(frame, f"extra listing {frame.attrs.get('source_file') or n}")
        extras.append(_listing(frame, None))
    parts = [*goi, *([far_rows] if far_rows is not None else []), *[e for e in extras if len(e)]]
    if not parts:
        return pd.DataFrame(columns=list(MASTER_COLUMNS))
    pool = pd.concat(parts, ignore_index=True)
    bad = pool[[not is_valid_isin(i) for i in pool["isin"]]]
    if len(bad):
        where = "; ".join(f"{r.isin} ({r.source}: {r.name!r})" for r in bad.drop_duplicates("isin").head(10)
                          .itertuples())
        raise ValueError(f"invalid ISINs (check digit or format): {where}")
    snapshots = pd.concat(goi, ignore_index=True) if goi else None
    latest_date = snapshots["as_of"].max() if snapshots is not None else None
    ref = pd.Timestamp(as_of) if as_of is not None else latest_date
    rank = {GOI_SOURCE: 0, FAR_SOURCE: 1}
    pool["_rank"] = pool["source"].map(rank).fillna(2)
    pool["_asof"] = pool["as_of"] if "as_of" in pool else pd.NaT
    pool = pool.sort_values(["isin", "_rank", "_asof"], ascending=[True, True, False], kind="stable")
    rows = []
    for isin, grp in pool.groupby("isin", sort=True):
        best = grp.iloc[0]
        conflicts = []
        dates = {}
        for col in ("issue_date", "maturity_date"):
            values = grp.dropna(subset=[col]).drop_duplicates(subset=["source", col])
            distinct = values[col].unique()
            if len(distinct) > 1:
                conflicts.append(f"{col}: " + ", ".join(
                    f"{s} {d:%Y-%m-%d}" for s, d in zip(values["source"], values[col])))
            dates[col] = values[col].iloc[0] if len(values) else pd.NaT
        parsed = parse_security_name(best["name"])
        maturity = dates["maturity_date"]
        if parsed.maturity_year and pd.notna(maturity) and parsed.maturity_year != maturity.year:
            conflicts.append(f"name says {parsed.maturity_year}, maturity date {maturity:%Y-%m-%d}")
        far_grp = grp[grp["source"] == FAR_SOURCE]
        for _, row in grp.iterrows():
            n = parse_security_name(row["name"])
            if n.maturity_date and pd.notna(maturity) and pd.Timestamp(n.maturity_date) != maturity:
                conflicts.append(f"{row['source']} name {row['name']!r} gives maturity {n.maturity_date}")
        issuer = state_for_isin(isin)
        if parsed.kind in CENTRAL_KINDS and issuer not in (None, "GOI"):
            conflicts.append(f"ISIN issuer digits {isin[2:4]} are a state's, name is {parsed.kind}")
        if parsed.kind == SDL and parsed.state_code and issuer and issuer != parsed.state_code:
            conflicts.append(f"ISIN issuer digits {isin[2:4]} are {issuer}, name says {parsed.state_code}")
        listed = grp[grp["source"] == GOI_SOURCE]
        latest = listed.iloc[0] if len(listed) else None
        issue = dates["issue_date"]
        fy = isin_fy(isin)
        rows.append({
            "isin": isin, "name": best["name"], "name_key": parsed.key, "kind": parsed.kind,
            "coupon_pct": parsed.coupon_pct, "maturity_year": parsed.maturity_year,
            "maturity_date": maturity, "issue_date": issue, "state_code": parsed.state_code,
            "far": len(far_grp) > 0, "far_name": far_grp["name"].iloc[0] if len(far_grp) else None,
            "in_latest_list": bool(latest is not None and latest["as_of"] == latest_date),
            "outstanding_cr": latest["outstanding_cr"] if latest is not None else None,
            "outstanding_as_of": latest["as_of"] if latest is not None else pd.NaT,
            "first_listed": listed["as_of"].min() if len(listed) else pd.NaT,
            "last_listed": listed["as_of"].max() if len(listed) else pd.NaT,
            "matured": (bool(maturity <= ref) if ref is not None and pd.notna(maturity) else None),
            "isin_fy": fy,
            "isin_fy_matches": (fy == financial_year(issue.date()) if fy is not None and pd.notna(issue)
                                else None),
            "sources": ";".join(sorted(set(grp["source"]))),
            "conflicts": "; ".join(conflicts),
        })
    out = pd.DataFrame(rows, columns=list(MASTER_COLUMNS))
    out["maturity_year"] = out["maturity_year"].astype("Int64")
    out["isin_fy"] = out["isin_fy"].astype("Int64")
    return out.sort_values(["maturity_date", "isin"], kind="stable").reset_index(drop=True)


def read_master(path: str | Path) -> pd.DataFrame:
    """A master CSV written by ``cr-strat sovereign-master`` (``MASTER_COLUMNS``), typed like ``build_master``'s."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False).replace({"": None})
    missing = [c for c in MASTER_COLUMNS if c not in df]
    if missing:
        raise ValueError(f"{path}: not a security master (missing {missing})")
    for col in ("maturity_date", "issue_date", "outstanding_as_of", "first_listed", "last_listed"):
        df[col] = pd.to_datetime(df[col])
    for col in ("coupon_pct", "outstanding_cr"):
        df[col] = pd.to_numeric(df[col]).astype(float)
    for col in ("maturity_year", "isin_fy"):
        df[col] = pd.to_numeric(df[col]).astype("Int64")
    for col in ("far", "in_latest_list", "matured", "isin_fy_matches"):
        df[col] = df[col].map(lambda v: None if v is None else str(v).strip().lower() in ("true", "1", "yes"))
    return df


@dataclass(frozen=True)
class IsinMatch:
    """Result of ``match_isin``: the ISIN (or ``None``), how it was found, and the candidates."""

    isin: str | None
    status: str  # 'matched', 'none', 'ambiguous' or 'unparsed'
    candidates: tuple[str, ...] = ()


SETTLEMENT_SLACK_DAYS = 7


def match_isin(master: pd.DataFrame, name: str, *, on=None, maturity=None) -> IsinMatch:
    """ISIN in ``master`` for a printed ``name``, using the maturity date or the date of a trade or auction.

    Candidates share the name's key. ``maturity`` (or a day-month suffix in
    the name) keeps those maturing that day; ``on`` keeps those already
    issued (first issue within ``SETTLEMENT_SLACK_DAYS`` after ``on``, since a
    new issue settles after its auction) and not yet matured, even when only
    one candidate shares the key. One candidate left is a match; none or
    several are reported, never guessed.
    """
    parsed = parse_security_name(name)
    if parsed.key is None:
        return IsinMatch(None, "unparsed")
    cand = master[master["name_key"] == parsed.key]
    maturity = maturity if maturity is not None else parsed.maturity_date
    if maturity is not None and len(cand):
        cand = cand[cand["maturity_date"] == pd.Timestamp(maturity)]
    if on is not None and len(cand):
        day = pd.Timestamp(on)
        live = cand["maturity_date"].isna() | (cand["maturity_date"] > day)
        issued = cand["issue_date"].isna() | (cand["issue_date"] <= day + pd.Timedelta(days=SETTLEMENT_SLACK_DAYS))
        cand = cand[live & issued]
    isins = tuple(cand["isin"])
    if len(isins) == 1:
        return IsinMatch(isins[0], "matched", isins)
    return IsinMatch(None, "ambiguous" if isins else "none", isins)


def attach_isin(frame: pd.DataFrame, master: pd.DataFrame, *, name: str = "security",
                date: str | None = "auction_date", maturity: str | None = None) -> pd.DataFrame:
    """Copy of ``frame`` with ``isin`` and ``isin_match`` (``match_isin``'s status) for each row."""
    out = frame.copy()
    results = []
    for _, row in frame.iterrows():
        on = row[date] if date and date in frame and pd.notna(row[date]) else None
        mat = row[maturity] if maturity and maturity in frame and pd.notna(row[maturity]) else None
        results.append(match_isin(master, row[name], on=on, maturity=mat))
    out["isin"] = [r.isin for r in results]
    out["isin_match"] = [r.status for r in results]
    return out
