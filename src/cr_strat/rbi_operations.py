"""RBI liquidity operations, money-market rates, open market operations and issuance calendars.

Parsers for the releases the daily store of RBI operations is built from
(``docs/rates_data_sources.md``, build-order item 4). Nothing here downloads
and nothing reads the clock: the inputs are the ``Release`` objects that
``rbi_releases`` makes from saved pages and copies of the press-release RSS
feed, one code path for both. Importing ``cr_strat.rbi_releases`` registers
these kinds, so ``rbi_releases.parse_releases`` and ``cr-strat rbi-releases``
parse them with no further import. The house rules of ``rbi_releases`` hold:
a release is refused (``ReleaseParseError``, ``SanityCheckError``), never
half-read; softer anomalies become notes in the release index; one table per
kind (``COLUMNS[kind]``) with rows keyed ``(release_id, line)``.

Kinds with a parser here (titles as observed in Nov 2026; the layouts were
taken from the samples named):

============================  ==============================================================  =================
Kind                          Title                                                           Sampled
============================  ==============================================================  =================
``money_market_operations``   Money Market Operations as on November 13, 2026                 Fri, Sat, Sun, Mon
``laf_auction_announcement``  RBI to conduct 2-day Variable Rate Reverse Repo (VRRR) auction  reverse repo only
                              under LAF on November 17, 2026
``laf_auction_result``        Result of the 3-day Variable Rate Reverse Repo (VRRR) auction   reverse repo only
                              held on November 13, 2026
``omo_announcement``          RBI announces OMO Purchase of Government of India Securities    purchase only
``omo_result``                Detailed Result: OMO Purchase Auction held on Mar 13, 2026 and  purchase only
                              Settlement on Mar 16, 2026
``gsec_calendar``             Issuance Calendar for Marketable Dated Securities for October   one half-year
                              2026 - March 2027
``sdl_calendar``              Indicative Calendar of Market borrowings by State Governments/  one quarter
                              Union Territories for the Quarter October – December 2026
============================  ==============================================================  =================

Registered without a parser, with the reason as the skip reason
(``ReleaseType.no_parser``): the OMO cut-off release, the Treasury bill
calendar, MPC resolutions and USD/INR swap results. None was sampled, and a
layout that was not seen is not guessed.

**Units and signs.** Amounts are ₹ crore (``_cr``): cash in the money-market
and liquidity tables, face value for OMOs and calendars. Rates and yields are
percent, prices per ₹100 face value, dates pandas Timestamps, window times
``HH:MM`` in India time. ``liquidity_effect_cr`` follows RBI's own sign
("injection (+)/absorption (-)"): plus the amount for a repo, MSF, SLF or an
OMO purchase, minus it for a reverse repo, SDF or an OMO sale. Printed amounts
are never re-signed.

**Labels.** Tables and rows are found by their labels, never by position,
depth, class or id. A row's key is its label without ONE leading enumerator
(``A.``, ``II.``, ``5.``, ``(a)``, ``(ii)``), squashed to lower-case letters
and digits (``label_key``); the footnote marks at the end of the label
(``label_marks``: ``Δ#`` of ``4. SDFΔ#``) are kept, because a changed mark is
the only machine-visible sign that RBI changed a definition. A row with a
label no parser knows is reported as a note with its cells, never dropped
silently; the identities then decide whether the release stands.

**Money Market Operations** (daily, one release per calendar day, published on
the next Mumbai business day). One long table, one row per printed data
line, so that no printed number is lost; ``item`` names the line:

* ``money_market`` block: ``overnight.total``, ``overnight.call_money``,
  ``overnight.triparty_repo``, ``overnight.market_repo``,
  ``overnight.corporate_bond_repo``, ``term.notice_money``,
  ``term.term_money``, ``term.triparty_repo``, ``term.market_repo``,
  ``term.corporate_bond_repo``. ``amount_cr`` is the volume (one leg),
  ``rate_pct`` the weighted average rate, ``rate_low_pct`` and
  ``rate_high_pct`` the range (``1.00-7.40``). The same three names occur in
  both segments: the heading above a line decides, not its numeral. A dash
  ("no transaction") is a blank, never zero; a printed ``0.00`` stays 0. Term
  money prints a volume and a range but no rate. On a weekend or holiday every
  volume is ``0.00`` and every rate a dash: keep the rows, never fill rates
  forward from them.
* ``rbi_operations`` block: ``laf.today.repo``, ``laf.today.reverse_repo``,
  ``laf.today.msf``, ``laf.today.sdf`` and the same four under
  ``laf.outstanding``, one row per operation with ``auction_date``,
  ``tenor_days``, ``maturity_date``, ``amount_cr`` and ``rate_pct`` (the
  facility's current rate, or an auction's cut-off rate); ``seq`` counts the
  rows of an item from 1. A facility line with five blank cells is "no
  operation" and gives no row; an amount of ``0.00`` is a row. Further tenors
  of a facility are rows of five cells under the label's ``rowspan``: they
  are attached to the label that spans them, and values in a row with no label
  are refused. ``net.today`` (line 5), ``slf.availed`` (D),
  ``net.outstanding`` (E) and ``net.total`` (F) carry an amount only; the net
  lines are printed signed and have no ``liquidity_effect_cr`` (they total
  the rows above: never add them to those rows). Lines E and F span two
  columns with their label, which is why the table is read with
  ``grid(fill=False)``. The corridor is ``laf.today.sdf`` and
  ``laf.today.msf``'s ``rate_pct``.
* ``reserve_position`` block: ``reserves.cash_balances``,
  ``reserves.crr_requirement``, ``government.surplus_cash_balance``,
  ``liquidity.net_durable``, each with its own ``ref_date`` (only the cash
  balances are as on the release's day).

``as_on_date`` comes from the title and nowhere else; ``release_date`` and
``published`` are the publication. Refused: a title without the date; no unit
line (``Amount in ₹ crore, Rate in Per cent``); a missing table, column or
value line; a filled ``1. Fixed Rate`` line (never seen filled: its sign in
the net lines is unknown); values on a heading; a facility row with some of
its five cells; the overnight volume not the sum of its four parts; line 5
not repo + MSF - reverse repo - SDF over today's rows; line E not the
outstanding rows plus SLF; line F not line 5 plus line E (all within
``SanityLimits.mmo_sum_tolerance_cr``); a maturity that is not the auction
date plus the tenor, or a printed weekday that does not fit its date
(``strptime`` would not notice); a row of today's block not dated the as-on
date; a rate outside ``yield_range``, a range running downwards, a negative
amount other than a net line or net durable liquidity. Notes: an unknown row;
changed footnote marks; a rate outside its range; a volume of 0 with a rate;
the overnight rate or range against its parts; MSF or SDF rows of one block
at different rates; an outstanding row not strictly between its auction and
maturity dates; cash balances dated another day. Releases before 30 Sep 2025
split the variable-rate block into main, fine-tuning and long-term
operations: those rows are unknown here, so such a release fails the net
identities as soon as one of them is filled.

**Variable rate repo and reverse repo auctions** (``direction``: ``repo``
injects, ``reverse_repo`` absorbs). The result is a seven-row label and value
table found by its ``Tenor`` row; tenor, direction and auction date come from
the title and must agree with the table (``Overnight`` is one day). A reverse
repo speaks of offers received, an amount accepted and a partial acceptance
percentage; a repo, as far as descriptions of such releases go, of bids
received, an amount allotted and a partial allotment percentage. Either
wording is accepted when it fits the title; a table that mixes them or
contradicts the title is refused, and every repo release carries a note
until one has been compared with its page. Checks: accepted not above
received, nor above the notified amount by more than
``SanityLimits.laf_excess_ratio`` (pro-rata rounding lets it pass a little);
the weighted average rate not above the cut-off in a reverse repo, not below
it in a repo. The announcement gives one row per auction: notified amount,
tenor, window (``09:30 AM to 10:00 AM``, the hour with or without a leading
zero) and the date of reversal, which must be the auction date of the title
and the text plus the tenor.

**Open market operations.** The detailed result has a three-row summary
(``₹50,000 crore``) and a table with one column per security; operation,
auction and settlement dates come from the title. Checks: the columns add up
to the summary; accepted not above offered; the total accepted not above the
notified aggregate by more than ``SanityLimits.omo_excess_ratio``; in a
purchase the weighted average yield is not below the cut-off yield and the
weighted average price not above the cut-off price (RBI buys the cheapest
offers), the reverse in a sale. The result prints no ISIN: the announcement
lists the same securities in the same order with ISIN and maturity date, the
aggregate amount in one cell spanning all rows, and in its text the auction
date, the settlement date, the price method and the earlier release that
announced the programme. Sale releases are known from descriptions only and
carry a note.

**Calendars.** The GoI calendar is one row per allocation line (``(i) 3 Year
for ₹9,000 crore``, ``SGrB`` for a green bond) under its auction week, which
is printed in three forms (``October 05-09, 2026``, ``September 28-October
02, 2026``, ``December 28, 2026-January 01, 2027``). Checks: every week runs
Monday to Friday, weeks are numbered from 1 without a gap and do not overlap,
the lines of a week add up to its amount and the weeks to the Total row.
Weeks are not checked against ``cr_strat.calendars`` (its holiday list ends
in 2026). The state calendar's page carries one number, the quarter's total;
the per-state amounts are in two PDF annexes whose addresses are kept in
``annex_urls`` for a person to open (never to be fetched by a program: see
the access rules in ``docs/rates_data_sources.md``).
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
from collections.abc import Iterable, Sequence
from dataclasses import replace

from cr_strat.htmlparse import NULL_MARKERS, Table, clean_text, parse_count, parse_date, parse_number, squash
from cr_strat.isin import is_valid_isin
from cr_strat.rbi_releases import (
    COLUMNS,
    COMMON_HEAD,
    COMMON_TAIL,
    ParseContext,
    Release,
    ReleaseParseError,
    ReleaseType,
    SanityCheckError,
    register,
)
from cr_strat.sovereign import UNKNOWN, parse_security_name

MMO = "money_market_operations"
LAF_RESULT = "laf_auction_result"
LAF_ANNOUNCEMENT = "laf_auction_announcement"
OMO_RESULT = "omo_result"
OMO_ANNOUNCEMENT = "omo_announcement"
GSEC_CALENDAR = "gsec_calendar"
SDL_CALENDAR = "sdl_calendar"

OPERATION_COLUMNS: dict[str, tuple[str, ...]] = {
    MMO: (*COMMON_HEAD, "as_on_date", "line", "block", "item", "seq", "label", "marks", "auction_date",
          "tenor_days", "maturity_date", "ref_date", "amount_cr", "rate_pct", "rate_low_pct", "rate_high_pct",
          "liquidity_effect_cr", *COMMON_TAIL),
    LAF_ANNOUNCEMENT: (*COMMON_HEAD, "auction_date", "line", "direction", "tenor_days", "notified_cr",
                       "window_start", "window_end", "reversal_date", *COMMON_TAIL),
    LAF_RESULT: (*COMMON_HEAD, "auction_date", "line", "direction", "tenor_days", "notified_cr", "received_cr",
                 "accepted_cr", "cutoff_rate_pct", "wa_rate_pct", "partial_pct", "liquidity_effect_cr",
                 *COMMON_TAIL),
    OMO_ANNOUNCEMENT: (*COMMON_HEAD, "auction_date", "settlement_date", "line", "operation", "isin", "security",
                       "name_key", "maturity_date", "aggregate_notified_cr", "auction_method", "programme_pr_no",
                       "programme_date", *COMMON_TAIL),
    OMO_RESULT: (*COMMON_HEAD, "auction_date", "settlement_date", "line", "operation", "security", "name_key",
                 "offers_received_count", "offered_cr", "offers_accepted_count", "accepted_cr", "cutoff_yield",
                 "cutoff_price", "way", "wap", "partial_allotment_pct", "aggregate_notified_cr",
                 "total_offered_cr", "total_accepted_cr", "liquidity_effect_cr", *COMMON_TAIL),
    GSEC_CALENDAR: (*COMMON_HEAD, "period_start", "period_end", "line", "week_no", "week_start", "week_end",
                    "week_amount_cr", "allocation_no", "tenor_years", "security_type", "amount_cr", "total_cr",
                    *COMMON_TAIL),
    SDL_CALENDAR: (*COMMON_HEAD, "quarter_start", "quarter_end", "line", "total_cr", "annex_urls", *COMMON_TAIL),
}
COLUMNS.update(OPERATION_COLUMNS)

# Money Market Operations: every item that can give a row, with its block and the footnote marks RBI printed on
# the line in Nov 2026. The two fixed-rate lines have no item: a release that fills one is refused.
MMO_ITEMS: dict[str, tuple[str, str]] = {
    "overnight.total": ("money_market", ""),
    "overnight.call_money": ("money_market", ""),
    "overnight.triparty_repo": ("money_market", ""),
    "overnight.market_repo": ("money_market", ""),
    "overnight.corporate_bond_repo": ("money_market", ""),
    "term.notice_money": ("money_market", "**"),
    "term.term_money": ("money_market", "@@"),
    "term.triparty_repo": ("money_market", ""),
    "term.market_repo": ("money_market", ""),
    "term.corporate_bond_repo": ("money_market", ""),
    "laf.today.repo": ("rbi_operations", ""),
    "laf.today.reverse_repo": ("rbi_operations", ""),
    "laf.today.msf": ("rbi_operations", "#"),
    "laf.today.sdf": ("rbi_operations", "Δ#"),
    "net.today": ("rbi_operations", "*"),
    "laf.outstanding.repo": ("rbi_operations", ""),
    "laf.outstanding.reverse_repo": ("rbi_operations", ""),
    "laf.outstanding.msf": ("rbi_operations", "#"),
    "laf.outstanding.sdf": ("rbi_operations", "Δ#"),
    "slf.availed": ("rbi_operations", "$"),
    "net.outstanding": ("rbi_operations", "*"),
    "net.total": ("rbi_operations", "*"),
    "reserves.cash_balances": ("reserve_position", "^^"),
    "reserves.crr_requirement": ("reserve_position", "^"),
    "government.surplus_cash_balance": ("reserve_position", "¥"),
    "liquidity.net_durable": ("reserve_position", ""),
}
# The lines every release must print exactly once (the facility lines print one row per operation, or none).
MMO_REQUIRED = tuple(item for item in MMO_ITEMS if not item.startswith("laf."))
_OVERNIGHT_PARTS = ("overnight.call_money", "overnight.triparty_repo", "overnight.market_repo",
                    "overnight.corporate_bond_repo")
_SEGMENT_NAMES = {"callmoney": "call_money", "tripartyrepo": "triparty_repo", "marketrepo": "market_repo",
                  "repoincorporatebond": "corporate_bond_repo", "noticemoney": "notice_money",
                  "termmoney": "term_money"}
_FACILITIES = {"repooperation": "repo", "reverserepooperation": "reverse_repo", "msf": "msf", "sdf": "sdf"}
_EFFECT_SIGN = {"repo": 1.0, "msf": 1.0, "reverse_repo": -1.0, "sdf": -1.0}
_AMOUNT_LINES = (  # (start of the label key, item) of the lines of the RBI operations table that carry an amount only
    ("netliquidityinjectedfromtodaysoperations", "net.today"),
    ("standingliquidityfacility", "slf.availed"),
    ("netliquidityinjectedfromoutstandingoperations", "net.outstanding"),
    ("netliquidityinjectedoutstandingincludingtodaysoperations", "net.total"),
)
_RESERVE_LINES = (
    ("cashbalanceswithrbi", "reserves.cash_balances"),
    ("averagedailycashreserverequirement", "reserves.crr_requirement"),
    ("governmentofindiasurpluscashbalance", "government.surplus_cash_balance"),
    ("netdurableliquidity", "liquidity.net_durable"),
)
_HEADING_MARKS = {"moneymarkets": "@", "rbioperations": "@", "reserveposition": "@", "variablerate": "&"}
OVERNIGHT_RATE_TOLERANCE_PCT = 0.02  # note when the overnight rate is further than this from its parts' average

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december")
_LONG_FORMATS = ("%B %d, %Y", "%b %d, %Y")
_LONG_DATE = r"([A-Z][a-z]{2,8}\.? \d{1,2}, \d{4})"
_ENUMERATOR = re.compile(r"^\s*(?:\([ivxlc]+\)|\([a-z]\)|[ivxlc]+\.|[a-z]\.|\d+\.)\s*", re.I)
_MARKS = re.compile(r"[@*#&$^¥Δ]+$")
_CRORE_AMOUNT = re.compile(r"^₹\s*([\d,]+(?:\.\d+)?)\s*crore\b", re.I)
_UNSAMPLED_NOTE = ("{what}: this layout was known only from descriptions when its parser was written, not from "
                   "a sample; compare the rows with the release")


# --- Labels and cells -------------------------------------------------------------------------------

def label_key(label: str) -> str:
    """Key of a row label: one leading enumerator (``A.``, ``II.``, ``5.``, ``(a)``, ``(ii)``) off, then squashed.

    ``4. SDFΔ#`` gives ``sdf`` and ``I. Notice Money**`` gives ``noticemoney``:
    squashing drops footnote marks, brackets, signs and spacing.
    """
    return squash(_ENUMERATOR.sub("", clean_text(label), count=1))


def label_marks(label: str) -> str:
    """The footnote marks that end a label (``Δ#`` of ``4. SDFΔ#``, ``^^``, ``*``); empty if there are none."""
    m = _MARKS.search(clean_text(label))
    return m.group(0) if m else ""


def _blank(text: str) -> bool:
    """True for an empty cell or a null marker (a dash, ``NA``): "not applicable / no transaction"."""
    return clean_text(text).lower() in NULL_MARKERS


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


def _date(text: str, formats: Sequence[str], what: str) -> dt.date | None:
    try:
        return parse_date(clean_text(text).replace(".", ""), formats)
    except ValueError as exc:
        raise ReleaseParseError(f"{what}: {exc}") from None


def _need(value, what: str, name: str):
    if value is None:
        raise ReleaseParseError(f"{what}: no {name}")
    return value


def _signed(sign: float, amount: float | None) -> float | None:
    """``amount`` with the sign of its liquidity effect; never a negative zero."""
    return None if amount is None else sign * amount + 0.0


def _is_value(text: str) -> bool:
    """True if a cell reads as an operation date or a number: a value sitting where a label should be."""
    if _OP_DATE.match(clean_text(text)):
        return True
    try:
        return parse_number(text) is not None
    except ValueError:
        return False


def _check_range(rec: dict, fields: Iterable[str], bounds: tuple[float, float], what: str, label: str) -> None:
    lo, hi = bounds
    for f in fields:
        v = rec.get(f)
        if v is not None and not lo <= v <= hi:
            raise SanityCheckError(f"{label}: {f} {v:g} is outside the {what} range {lo:g}-{hi:g}")


def _cover(table: Table) -> list[list[tuple[int, ...] | None]]:
    """For each position of ``table.grid()`` the (row, cell) in ``table.rows`` that covers it; None where padded.

    Found by running the table's own span expansion on a copy whose cells hold
    their coordinates, so it cannot disagree with ``grid``. It tells a row
    whose label is spanned from the row above from one whose own label is empty.
    """
    marked = replace(table, rows=tuple(tuple(replace(cell, text=f"{r} {i}") for i, cell in enumerate(row))
                                       for r, row in enumerate(table.rows)))
    return [[tuple(int(n) for n in text.split()) if text else None for text in row] for row in marked.grid()]


def _title_match(pattern: re.Pattern, release: Release, form: str) -> re.Match:
    m = pattern.search(clean_text(release.title))
    if not m:
        raise ReleaseParseError(f"{release.source_file}: title {release.title!r} is not of the form {form!r}")
    return m


def _one_table(release: Release, found: list, what: str):
    if len(found) != 1:
        raise ReleaseParseError(f"{release.source_file}: {'no' if not found else len(found)} "
                                f"table{'' if len(found) < 2 else 's'} with {what}")
    return found[0]


def _headed_table(release: Release, first: str, wanted: dict[str, str], name: str) -> tuple[Table, int, dict]:
    """The one table whose header row starts with the label ``first``: table, header row, column of each field.

    ``wanted`` maps a field to the exact key of its header cell.
    """
    found = []
    for table in release.data_tables:
        for h, row in enumerate(table.rows):
            if len(row) > 1 and squash(row[0].text) == first:
                found.append((table, h))
                break
    table, h = _one_table(release, found, f"the header '{name}'")
    keys = [squash(cell) for cell in table.grid(fill=False)[h]]
    cols = {}
    for field, key in wanted.items():
        hits = [j for j, k in enumerate(keys) if k == key]
        if len(hits) != 1:
            raise ReleaseParseError(f"{release.source_file}: the '{name}' table has {len(hits)} columns headed "
                                    f"{key!r} (header: {table.cell_texts()[h]})")
        cols[field] = hits[0]
    return table, h, cols


def _column_table(release: Release, wanted: dict[str, str], optional: Iterable[str], ctx: ParseContext,
                  name: str) -> tuple[Table, int, dict[str, int]]:
    """The one table with a header row holding a cell for every field: table, header row, columns.

    ``wanted`` maps a field to the start of its header cell's key. Header cells
    that are neither wanted nor ``optional`` are noted.
    """
    found = []
    for table in release.data_tables:
        for h, cells in enumerate(table.grid(fill=False)):
            keys = [squash(cell) for cell in cells]
            cols = {field: [j for j, k in enumerate(keys) if k.startswith(prefix)] for field, prefix in wanted.items()}
            if all(len(hits) == 1 for hits in cols.values()):
                found.append((table, h, {field: hits[0] for field, hits in cols.items()}))
                break
    table, h, cols = _one_table(release, found, f"the columns of {name} ({', '.join(wanted.values())})")
    known = tuple(optional)
    for j, cell in enumerate(table.grid(fill=False)[h]):
        key = squash(cell)
        if key and j not in cols.values() and not key.startswith(known):
            ctx.notes.append(f"column {cell!r} of the {name} table not recognised; ignored")
    return table, h, cols


def _labelled_values(pairs: Iterable[tuple[int, str, object]], rows: Sequence[tuple[str, str, str | None]],
                     where: str, ctx: ParseContext) -> tuple[dict[str, tuple[str, object]], set[str]]:
    """Rows of a label and value table sorted into fields: ``{field: (label, value)}`` and the wordings used.

    ``pairs`` yields ``(row number, label, value)``; ``rows`` lists ``(start of
    the label key, field, wording)``, where ``wording`` names the variant of
    the release a label belongs to (None when all variants share it). A field
    printed twice is refused; a label that is not known is noted.
    """
    found: dict[str, tuple[str, object]] = {}
    wordings: set[str] = set()
    for number, label, value in pairs:
        key = label_key(label)
        if not key:
            continue
        hit = next(((field, wording) for prefix, field, wording in rows if key.startswith(prefix)), None)
        if hit is None:
            ctx.notes.append(f"row {number} {label!r} not recognised; ignored (value: {value!r})")
            continue
        field, wording = hit
        if field in found:
            raise ReleaseParseError(f"{where}: two rows for {field} ({found[field][0]!r} and {label!r})")
        found[field] = (label, value)
        if wording:
            wordings.add(wording)
    return found, wordings


def _check_wording(wordings: set[str], expected: str, where: str, what: str) -> None:
    if wordings - {expected}:
        raise ReleaseParseError(f"{where}: the title says {expected.replace('_', ' ')} but the {what} uses the "
                                f"words of a {' and a '.join(sorted(w.replace('_', ' ') for w in wordings))}")


def _missing_rows(found: dict, fields: Iterable[str], where: str) -> None:
    missing = [f for f in fields if f not in found]
    if missing:
        raise ReleaseParseError(f"{where}: no row for {', '.join(missing)}")


def _unit_is_crore(labels: Iterable[str], where: str) -> None:
    for label in labels:
        if "crore" not in label.lower():
            raise ReleaseParseError(f"{where}: {label!r} does not say crore; unit unknown")


def _mentions(pattern: re.Pattern, text: str) -> set[str]:
    return {re.sub(r"\s+", "_", m.lower()) for m in pattern.findall(text)}


# --- Money Market Operations ------------------------------------------------------------------------

_MMO_TITLE = re.compile(r"^money market operations as on ([a-z]+ \d{1,2}, \d{4})$", re.I)
_OP_DATE = re.compile(r"^([A-Za-z]{3,9})\.?,?\s*(\d{1,2})/(\d{1,2})/(\d{4})$")
_RANGE = re.compile(r"^(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)$")
_OP_FIELDS = ("auction_date", "tenor_days", "maturity_date", "amount_cr", "rate_pct")
_OP_NAMES = ("auction date", "tenor", "maturity date", "amount", "rate")


def _mmo_row(block: str, item: str, label: str, **fields) -> dict:
    return {"block": block, "item": item, "label": clean_text(label), "marks": label_marks(label), **fields}


def _note_marks(what: str, label: str, expected: str, ctx: ParseContext) -> None:
    marks = label_marks(label)
    if marks != expected:
        ctx.notes.append(f"marks of {what} changed: {marks!r} (was {expected!r})")


def _unknown_row(name: str, number: int, label: str, cells: Iterable[str], ctx: ParseContext,
                 unknown: dict[str, list[str]]) -> None:
    printed = [clean_text(c) for c in cells if not _blank(c)]
    ctx.notes.append(f"{name} row {number} {label!r} not recognised; ignored"
                     + (f" (cells: {', '.join(printed)})" if printed else ""))
    unknown.setdefault(name, []).append(clean_text(label))


def _no_stray_cells(row: list[str], width: int, where: str) -> None:
    if any(clean_text(cell) for cell in row[width:]):
        raise ReleaseParseError(f"{where}: cells beyond the table's {width} columns ({row[width:]})")


def _mmo_money_markets(release: Release, ctx: ParseContext, unknown: dict[str, list[str]]) -> list[dict]:
    name = "Money Markets"
    table, h, cols = _headed_table(release, "moneymarkets", {"amount_cr": "volumeoneleg",
                                                             "rate_pct": "weightedaveragerate", "range": "range"},
                                   name)
    grid = table.grid(fill=False)
    _note_marks("the Money Markets heading", grid[h][0], _HEADING_MARKS["moneymarkets"], ctx)
    width = len(table.rows[h])
    segment, out = None, []
    for r in range(h + 1, len(grid)):
        row = grid[r]
        label = row[0]
        where = f"{release.source_file}: {name} row {r + 1} ({label!r})"
        _no_stray_cells(row, width, where)
        cells = {f: row[c] for f, c in cols.items()}
        filled = any(not _blank(v) for v in cells.values())
        key = label_key(label)
        if not key:
            if filled:
                raise ReleaseParseError(f"{where}: values without a label")
            continue
        if _is_value(label):
            raise ReleaseParseError(f"{where}: a value where the row's label should be")
        if key.startswith("overnightsegment"):
            segment, item = "overnight", "overnight.total"
        elif key == "termsegment":
            segment = "term"
            if filled:
                raise ReleaseParseError(f"{where}: the heading of the term segment carries values")
            continue
        elif segment and f"{segment}.{_SEGMENT_NAMES.get(key)}" in MMO_ITEMS:
            item = f"{segment}.{_SEGMENT_NAMES[key]}"
        else:
            _unknown_row(name, r + 1, label, cells.values(), ctx, unknown)
            continue
        _note_marks(item, label, MMO_ITEMS[item][1], ctx)
        low = high = None
        if not _blank(cells["range"]):
            m = _RANGE.match(clean_text(cells["range"]))
            if not m:
                raise ReleaseParseError(f"{where}: range {cells['range']!r} is not 'low-high'")
            low, high = float(m.group(1)), float(m.group(2))
        out.append(_mmo_row("money_market", item, label, amount_cr=_num(cells["amount_cr"], where),
                            rate_pct=_num(cells["rate_pct"], where), rate_low_pct=low, rate_high_pct=high))
    return out


def _op_date(text: str, where: str, name: str) -> dt.date:
    m = _OP_DATE.match(clean_text(text))
    if not m:
        raise ReleaseParseError(f"{where}: {name} {text!r} is not of the form 'Fri, 13/11/2026'")
    try:
        day = dt.date(int(m.group(4)), int(m.group(3)), int(m.group(2)))
    except ValueError:
        raise ReleaseParseError(f"{where}: {name} {text!r} is not a date") from None
    if _WEEKDAYS[day.weekday()][:3] != m.group(1).lower()[:3]:
        raise SanityCheckError(f"{where}: {name} {text!r} is a {_WEEKDAYS[day.weekday()].title()}")
    return day


def _operation(item: str, label: str, values: list[str], as_on: dt.date, where: str, ctx: ParseContext) -> dict:
    """One row of a facility line: auction date, tenor, maturity date, amount and rate, all five printed."""
    missing = [name for name, v in zip(_OP_NAMES, values, strict=True) if _blank(v)]
    if missing:
        raise ReleaseParseError(f"{where}: an operation without its {', '.join(missing)} (a facility row "
                                "prints all five cells or none)")
    auction = _op_date(values[0], where, "auction date")
    tenor = _need(_cnt(values[1], where), where, "tenor")
    maturity = _op_date(values[2], where, "maturity date")
    amount = _need(_num(values[3], where), where, "amount")
    rate = _need(_num(values[4], where), where, "rate")
    if tenor < 1 or (maturity - auction).days != tenor:
        raise SanityCheckError(f"{where}: maturity {maturity} is not the auction date {auction} plus the tenor "
                               f"of {tenor} days")
    section, facility = item.split(".")[1:]
    if section == "today":
        if auction != as_on:
            raise SanityCheckError(f"{where}: an operation of today's block is dated {auction}, not the "
                                   f"{as_on} of the title")
    elif not auction < as_on < maturity:
        ctx.notes.append(f"{item}: an outstanding operation of {auction} maturing {maturity} is not strictly "
                         f"around the as-on date {as_on}")
    return _mmo_row("rbi_operations", item, label, auction_date=auction, tenor_days=tenor, maturity_date=maturity,
                    amount_cr=amount, rate_pct=rate, liquidity_effect_cr=_signed(_EFFECT_SIGN[facility], amount))


def _mmo_operations(release: Release, as_on: dt.date, ctx: ParseContext,
                    unknown: dict[str, list[str]]) -> list[dict]:
    name = "RBI Operations"
    table, h, cols = _headed_table(release, "rbioperations", {
        "auction_date": "auctiondate", "tenor_days": "tenordays", "maturity_date": "maturitydate",
        "amount_cr": "amount", "rate_pct": "currentratecutoffrate"}, name)
    grid, cover = table.grid(fill=False), _cover(table)
    _note_marks("the RBI Operations heading", grid[h][0], _HEADING_MARKS["rbioperations"], ctx)
    width = len(table.rows[h])
    section: str | None = None
    sections: list[str] = []
    spanning: tuple[tuple[int, ...] | None, str, str | None] = (None, "", None)  # label cell, label, facility item
    out = []
    for r in range(h + 1, len(grid)):
        if not table.rows[r]:
            continue
        row = grid[r]
        values = [row[cols[f]] for f in _OP_FIELDS]
        filled = any(not _blank(v) for v in values)
        origin = cover[r][0]
        if origin is not None and origin[0] < r:  # the label of a row above spans down to this one
            label, item = spanning[1], spanning[2] if origin == spanning[0] else None
            where = f"{release.source_file}: {name} row {r + 1} (under {label!r})"
            _no_stray_cells(row, width, where)
            if not filled:
                continue
            if label_key(label) == "fixedrate":
                raise ReleaseParseError(f"{where}: {_FIXED_RATE_REFUSAL}")
            if item is None:
                raise ReleaseParseError(f"{where}: values under a label that is not a facility line")
            out.append(_operation(item, label, values, as_on, where, ctx))
            continue
        label = row[0]
        where = f"{release.source_file}: {name} row {r + 1} ({label!r})"
        _no_stray_cells(row, width, where)
        key = label_key(label)
        spanning = (origin, label, None)
        if not key:
            if filled:
                raise ReleaseParseError(f"{where}: values without a label")
            continue
        if _is_value(label):
            raise ReleaseParseError(f"{where}: a value where the row's label should be (a row of five cells "
                                    "that no label spans)")
        heading = (key.startswith("liquidityadjustmentfacility") or key in ("todaysoperations",
                                                                           "outstandingoperations", "variablerate"))
        if heading:
            if filled:
                raise ReleaseParseError(f"{where}: a heading with values")
            if key in ("todaysoperations", "outstandingoperations"):
                section = "today" if key == "todaysoperations" else "outstanding"
                if section in sections:
                    raise ReleaseParseError(f"{where}: the heading occurs twice")
                sections.append(section)
            _note_marks(f"the heading {clean_text(_ENUMERATOR.sub('', label, count=1)).rstrip('@*#&$^¥Δ')!r}",
                        label, _HEADING_MARKS.get(key, ""), ctx)
        elif key == "fixedrate":
            if filled:
                raise ReleaseParseError(f"{where}: {_FIXED_RATE_REFUSAL}")
        elif key in _FACILITIES:
            if section is None:
                raise ReleaseParseError(f"{where}: a facility line before the heading of today's or the "
                                        "outstanding operations")
            item = f"laf.{section}.{_FACILITIES[key]}"
            spanning = (origin, label, item)
            _note_marks(item, label, MMO_ITEMS[item][1], ctx)
            if filled:
                out.append(_operation(item, label, values, as_on, where, ctx))
        else:
            item = next((code for prefix, code in _AMOUNT_LINES if key.startswith(prefix)), None)
            if item is None:
                _unknown_row(name, r + 1, label, values, ctx, unknown)
                continue
            _note_marks(item, label, MMO_ITEMS[item][1], ctx)
            others = [n for n, v in zip(_OP_NAMES, values, strict=True) if n != "amount" and not _blank(v)]
            if others:
                raise ReleaseParseError(f"{where}: a line that carries an amount only has a {', '.join(others)}")
            amount = _num(values[3], where)
            if item != "slf.availed":
                _need(amount, where, "amount")
            out.append(_mmo_row("rbi_operations", item, label, amount_cr=amount,
                                liquidity_effect_cr=_signed(1.0, amount) if item == "slf.availed" else None))
    absent = [s for s in ("today", "outstanding") if s not in sections]
    if absent:
        raise ReleaseParseError(f"{release.source_file}: the {name} table has no heading for "
                                f"{' or '.join(absent)}'s operations".replace("outstanding's", "the outstanding"))
    return out


_FIXED_RATE_REFUSAL = ("a filled '1. Fixed Rate' line was in no sample and its sign in the net lines is not "
                       "known; the release is not read")


def _mmo_reserves(release: Release, ctx: ParseContext, unknown: dict[str, list[str]]) -> list[dict]:
    name = "Reserve Position"
    table, h, cols = _headed_table(release, "reserveposition", {"ref_date": "date", "amount_cr": "amount"}, name)
    grid = table.grid(fill=False)
    _note_marks("the Reserve Position heading", grid[h][0], _HEADING_MARKS["reserveposition"], ctx)
    width = len(table.rows[h])
    out = []
    for r in range(h + 1, len(grid)):
        row = grid[r]
        label = row[0]
        where = f"{release.source_file}: {name} row {r + 1} ({label!r})"
        _no_stray_cells(row, width, where)
        cells = {f: row[c] for f, c in cols.items()}
        filled = any(not _blank(v) for v in cells.values())
        key = label_key(label)
        if not key:
            if filled:
                raise ReleaseParseError(f"{where}: values without a label")
            continue
        if key.startswith("cashreservesposition"):
            if filled:
                raise ReleaseParseError(f"{where}: a heading with values")
            continue
        item = next((code for prefix, code in _RESERVE_LINES if key.startswith(prefix)), None)
        if item is None:
            _unknown_row(name, r + 1, label, cells.values(), ctx, unknown)
            continue
        _note_marks(item, label, MMO_ITEMS[item][1], ctx)
        out.append(_mmo_row("reserve_position", item, label, ref_date=_date(cells["ref_date"], _LONG_FORMATS, where),
                            amount_cr=_num(cells["amount_cr"], where)))
    return out


def _fmt(value: float | None) -> str:
    return "blank" if value is None else f"{value:.2f}"


def _mmo_identities(by: dict[str, list[dict]], release: Release, ctx: ParseContext,
                    unknown: dict[str, list[str]]) -> None:
    """The sums the release itself prints: overnight total, and the three net liquidity lines."""
    src, tol = release.source_file, ctx.limits.mmo_sum_tolerance_cr

    def amount(item: str) -> float | None:
        return by[item][0]["amount_cr"]

    def effects(section: str) -> float:
        return sum(row["liquidity_effect_cr"] for item, rows in by.items() if item.startswith(f"laf.{section}.")
                   for row in rows)

    def fail(message: str, table: str) -> None:
        extra = unknown.get(table)
        hint = f"; rows of the table that were not recognised: {extra} (a layout not sampled?)" if extra else ""
        raise SanityCheckError(f"{src}: {message}{hint}")

    total, parts = amount("overnight.total"), [amount(item) for item in _OVERNIGHT_PARTS]
    printed = [p for p in parts if p is not None]
    if (total is None and printed) or (total is not None and abs(total - sum(printed)) > tol):
        fail(f"overnight.total volume {_fmt(total)} is not the sum of its four parts ({sum(printed):.2f}: call "
             "money, triparty repo, market repo, repo in corporate bond)", "Money Markets")
    today, outstanding, both = amount("net.today"), amount("net.outstanding"), amount("net.total")
    if abs(today - effects("today")) > tol:
        fail(f"net.today {today:.2f} is not repo + MSF - reverse repo - SDF over today's operations "
             f"({effects('today'):.2f})", "RBI Operations")
    expected = effects("outstanding") + (amount("slf.availed") or 0.0)
    if abs(outstanding - expected) > tol:
        fail(f"net.outstanding {outstanding:.2f} is not the outstanding operations plus SLF ({expected:.2f})",
             "RBI Operations")
    if abs(both - (today + outstanding)) > tol:
        fail(f"net.total {both:.2f} is not net.today {today:.2f} plus net.outstanding {outstanding:.2f}",
             "RBI Operations")


def _mmo_limits(rows: list[dict], release: Release, ctx: ParseContext) -> None:
    for row in rows:
        where = f"{release.source_file} [{row['item']}]"
        _check_range(row, ("rate_pct", "rate_low_pct", "rate_high_pct"), ctx.limits.yield_range, "rate", where)
        low, high, amount = row.get("rate_low_pct"), row.get("rate_high_pct"), row.get("amount_cr")
        if low is not None and high is not None and low > high:
            raise SanityCheckError(f"{where}: the range {low:g}-{high:g} runs downwards")
        if amount is not None and amount < 0 and not (row["item"].startswith("net.")
                                                      or row["item"] == "liquidity.net_durable"):
            raise SanityCheckError(f"{where}: amount_cr is negative ({amount:g})")


def _mmo_notes(by: dict[str, list[dict]], as_on: dt.date, ctx: ParseContext) -> None:
    """The soft checks: anomalies that leave the rows in place."""
    for item, rows in by.items():
        block = MMO_ITEMS[item][0]
        for row in rows:
            amount, rate, low, high = (row.get(f) for f in ("amount_cr", "rate_pct", "rate_low_pct",
                                                            "rate_high_pct"))
            if amount is None and item != "slf.availed":
                ctx.notes.append(f"{item}: no amount printed")
            if block != "money_market":
                continue
            if rate is not None and low is not None and not low <= rate <= high:
                ctx.notes.append(f"{item}: rate {rate:g} is outside its range {low:g}-{high:g}")
            if amount == 0 and (rate is not None or low is not None):
                ctx.notes.append(f"{item}: a volume of 0 with a rate or a range")
            if amount and low is None:
                ctx.notes.append(f"{item}: a volume of {amount:g} without a range")
        if item.endswith((".msf", ".sdf")) and len({row["rate_pct"] for row in rows}) > 1:
            ctx.notes.append(f"{item}: rows at different rates ({sorted({row['rate_pct'] for row in rows})})")
    total = by["overnight.total"][0]
    parts = [by[item][0] for item in _OVERNIGHT_PARTS]
    traded = [p for p in parts if p["amount_cr"]]
    if total["amount_cr"] and total["rate_pct"] is not None and all(p["rate_pct"] is not None for p in traded):
        average = sum(p["amount_cr"] * p["rate_pct"] for p in traded) / sum(p["amount_cr"] for p in traded)
        if abs(average - total["rate_pct"]) > OVERNIGHT_RATE_TOLERANCE_PCT:
            ctx.notes.append(f"overnight.total: rate {total['rate_pct']:g} against {average:.4f}, the "
                             "volume-weighted rate of its parts")
    lows = [p["rate_low_pct"] for p in traded if p["rate_low_pct"] is not None]
    highs = [p["rate_high_pct"] for p in traded if p["rate_high_pct"] is not None]
    if total["rate_low_pct"] is not None and lows and (total["rate_low_pct"], total["rate_high_pct"]) != (
            min(lows), max(highs)):
        ctx.notes.append(f"overnight.total: range {total['rate_low_pct']:g}-{total['rate_high_pct']:g} against "
                         f"{min(lows):g}-{max(highs):g} over its parts")
    cash = by["reserves.cash_balances"][0]["ref_date"]
    if cash is not None and cash != as_on:
        ctx.notes.append(f"reserves.cash_balances is dated {cash}, not the as-on date {as_on}")


def parse_money_market_operations(release: Release, ctx: ParseContext | None = None) -> list[dict]:
    """Rows of a Money Market Operations release, one per printed data line (``COLUMNS[MMO]`` fields)."""
    ctx = ctx or ParseContext()
    m = _title_match(_MMO_TITLE, release, "Money Market Operations as on November 13, 2026")
    as_on = _need(_date(m.group(1), _LONG_FORMATS, f"{release.source_file}: title"), release.source_file,
                  "as-on date in the title")
    units = squash(release.text)
    if "amountincrore" not in units or "rateinpercent" not in units:
        raise ReleaseParseError(f"{release.source_file}: no '(Amount in ₹ crore, Rate in Per cent)' line; units "
                                "unknown")
    unknown: dict[str, list[str]] = {}
    rows = (_mmo_money_markets(release, ctx, unknown) + _mmo_operations(release, as_on, ctx, unknown)
            + _mmo_reserves(release, ctx, unknown))
    by: dict[str, list[dict]] = {}
    for row in rows:
        by.setdefault(row["item"], []).append(row)
        row.update(as_on_date=as_on, seq=len(by[row["item"]]))
    for item in MMO_REQUIRED:
        if len(by.get(item, ())) != 1:
            raise ReleaseParseError(f"{release.source_file}: the line {item} "
                                    f"{'is missing' if item not in by else 'is printed more than once'}")
    _mmo_limits(rows, release, ctx)
    _mmo_identities(by, release, ctx, unknown)
    _mmo_notes(by, as_on, ctx)
    return rows


# --- Variable rate repo and reverse repo auctions ---------------------------------------------------

_TENOR_WORDS = r"(overnight|\d+\s*-?\s*days?)"
_LAF_RESULT_TITLE = re.compile(r"^result of the " + _TENOR_WORDS + r" variable rate (reverse repo|repo)\b.*"
                               r"\bauction held on ([a-z]+ \d{1,2}, \d{4})$", re.I)
_LAF_ANNOUNCEMENT_TITLE = re.compile(r"\bto conduct " + _TENOR_WORDS + r" variable rate (reverse repo|repo)\b.*"
                                     r"\bauction\b.*\bon ([a-z]+ \d{1,2}, \d{4})$", re.I)
_LAF_DIRECTION = re.compile(r"variable rate (reverse repo|repo)\b", re.I)
_LAF_SENTENCE = re.compile(r"\bauction on (?:[A-Z][a-z]+day, )?" + _LONG_DATE)
_TENOR = re.compile(r"^(\d+)\s*-?\s*days?$", re.I)
_WINDOW = re.compile(r"^(\d{1,2}):(\d{2}) ?([AP])\.?M\.? to (\d{1,2}):(\d{2}) ?([AP])\.?M\.?$", re.I)
_LAF_RESULT_ROWS = (
    ("tenor", "tenor", None),
    ("notifiedamount", "notified_cr", None),
    ("totalamountofoffersreceived", "received_cr", "reverse_repo"),
    ("totalamountofbidsreceived", "received_cr", "repo"),
    ("amountaccepted", "accepted_cr", "reverse_repo"),
    ("amountallotted", "accepted_cr", "repo"),
    ("cutoffrate", "cutoff_rate_pct", None),
    ("weightedaveragerate", "wa_rate_pct", None),
    ("partialacceptancepercentage", "partial_pct", "reverse_repo"),
    ("partialallotmentpercentage", "partial_pct", "repo"),
)
_LAF_RESULT_FIELDS = ("tenor", "notified_cr", "received_cr", "accepted_cr", "cutoff_rate_pct", "wa_rate_pct",
                      "partial_pct")


def _tenor_days(text: str, where: str) -> int:
    """Days of a printed tenor: ``Overnight`` is 1, ``3-day``, ``14 days``."""
    tenor = clean_text(text).lower()
    m = _TENOR.match(tenor)
    if tenor != "overnight" and not (m and int(m.group(1)) >= 1):
        raise ReleaseParseError(f"{where}: tenor {text!r} is not 'Overnight' or 'N-day'")
    return 1 if tenor == "overnight" else int(m.group(1))


def _laf_title(pattern: re.Pattern, release: Release, form: str) -> tuple[int, str, dt.date]:
    """Tenor in days, direction and auction date from the title of a variable rate auction release."""
    m = _title_match(pattern, release, form)
    where = f"{release.source_file}: title"
    return (_tenor_days(m.group(1), where), m.group(2).lower().replace(" ", "_"),
            _need(_date(m.group(3), _LONG_FORMATS, where), where, "auction date"))


def parse_laf_auction_result(release: Release, ctx: ParseContext | None = None) -> list[dict]:
    """The row of a variable rate repo or reverse repo auction result (``COLUMNS[LAF_RESULT]`` fields)."""
    ctx = ctx or ParseContext()
    src = release.source_file
    tenor, direction, auction_date = _laf_title(
        _LAF_RESULT_TITLE, release, "Result of the 3-day Variable Rate Reverse Repo (VRRR) auction held on "
                                    "November 13, 2026")
    table = _one_table(release, [t for t in release.data_tables
                                 if any(len(row) == 2 and label_key(row[0].text) == "tenor" for row in t.rows)],
                       "a 'Tenor' row of a label and a value")
    pairs = []
    for r, row in enumerate(table.rows, start=1):
        if len(row) > 2:
            raise ReleaseParseError(f"{src}: row {r} has {len(row)} cells, not a label and a value "
                                    f"({[c.text for c in row]})")
        if row and any(c.text for c in row):
            pairs.append((r, row[0].text, row[-1].text if len(row) == 2 else ""))
    found, wordings = _labelled_values(pairs, _LAF_RESULT_ROWS, src, ctx)
    _missing_rows(found, _LAF_RESULT_FIELDS, src)
    _check_wording(wordings, direction, src, "table")
    _unit_is_crore((found[f][0] for f in ("notified_cr", "received_cr", "accepted_cr")), src)
    rec: dict = {"auction_date": auction_date, "direction": direction,
                 "tenor_days": _tenor_days(found["tenor"][1], f"{src}: {found['tenor'][0]!r}")}
    for f in _LAF_RESULT_FIELDS[1:]:
        rec[f] = _num(found[f][1], f"{src}: {found[f][0]!r}")
    for f in ("notified_cr", "received_cr", "accepted_cr"):
        _need(rec[f], f"{src}: {found[f][0]!r}", "amount")
    if rec["tenor_days"] != tenor:
        raise SanityCheckError(f"{src}: the table says {rec['tenor_days']} days, the title {tenor}")
    for f in ("notified_cr", "received_cr", "accepted_cr"):
        if rec[f] < 0:
            raise SanityCheckError(f"{src}: {f} is negative ({rec[f]:g})")
    if not rec["notified_cr"]:
        raise SanityCheckError(f"{src}: no notified amount")
    tol = ctx.limits.amount_tolerance_cr
    if rec["accepted_cr"] > rec["received_cr"] + tol:
        raise SanityCheckError(f"{src}: accepted_cr {rec['accepted_cr']:g} is above received_cr "
                               f"{rec['received_cr']:g}")
    if rec["accepted_cr"] > rec["notified_cr"] * (1 + ctx.limits.laf_excess_ratio) + tol:
        raise SanityCheckError(f"{src}: accepted_cr {rec['accepted_cr']:g} is more than "
                               f"{ctx.limits.laf_excess_ratio:.2%} above notified_cr {rec['notified_cr']:g}")
    _check_range(rec, ("cutoff_rate_pct", "wa_rate_pct"), ctx.limits.yield_range, "rate", src)
    _check_range(rec, ("partial_pct",), (0.0, 100.0), "percentage", src)
    cutoff, average = rec["cutoff_rate_pct"], rec["wa_rate_pct"]
    if rec["accepted_cr"] > 0 and (cutoff is None or average is None):
        raise SanityCheckError(f"{src}: an amount accepted but no cut-off or weighted average rate")
    if cutoff is not None and average is not None:
        sign = -1 if direction == "reverse_repo" else 1
        if sign * (average - cutoff) < -1e-9:
            raise SanityCheckError(f"{src}: weighted average rate {average:g} is "
                                   f"{'above' if sign < 0 else 'below'} the cut-off {cutoff:g} of a "
                                   f"{direction.replace('_', ' ')}")
    rec["liquidity_effect_cr"] = _signed(-1.0 if direction == "reverse_repo" else 1.0, rec["accepted_cr"])
    if rec["partial_pct"] is None and rec["accepted_cr"] < rec["received_cr"] - tol:
        ctx.notes.append("accepted less than received but no partial percentage printed")
    if direction == "repo":
        ctx.notes.append(_UNSAMPLED_NOTE.format(what="variable rate repo (injection) result"))
    return [rec]


def _window_time(hour: str, minute: str, half: str, where: str) -> str:
    h, m = int(hour), int(minute)
    if not (1 <= h <= 12 and m < 60):
        raise ReleaseParseError(f"{where}: {hour}:{minute} {half}M is not a time")
    return f"{h % 12 + (12 if half.upper() == 'P' else 0):02d}:{m:02d}"


def parse_laf_auction_announcement(release: Release, ctx: ParseContext | None = None) -> list[dict]:
    """Rows of a variable rate repo or reverse repo auction announcement (``COLUMNS[LAF_ANNOUNCEMENT]`` fields)."""
    ctx = ctx or ParseContext()
    src = release.source_file
    tenor, direction, auction_date = _laf_title(
        _LAF_ANNOUNCEMENT_TITLE, release, "RBI to conduct 2-day Variable Rate Reverse Repo (VRRR) auction under "
                                          "LAF on November 17, 2026")
    stated = {_date(text, _LONG_FORMATS, f"{src}: text") for text in _LAF_SENTENCE.findall(release.narrative)}
    if not stated:
        raise ReleaseParseError(f"{src}: no 'auction on <date>' sentence to check the title's date against")
    if stated != {auction_date}:
        raise SanityCheckError(f"{src}: the title says the auction is on {auction_date}, the text "
                               f"{' and '.join(str(d) for d in sorted(stated))}")
    spoken = _mentions(_LAF_DIRECTION, release.narrative)
    if spoken and direction not in spoken:
        raise ReleaseParseError(f"{src}: the title announces a {direction.replace('_', ' ')}, the text a "
                                f"{' and a '.join(sorted(s.replace('_', ' ') for s in spoken))}")
    table, h, cols = _column_table(release, {"notified_cr": "notifiedamount", "tenor_days": "tenor",
                                             "window": "windowtiming", "reversal_date": "dateofreversal"},
                                   ("slno", "srno", "sno"), ctx, "auction")
    grid = table.grid()
    header = grid[h]
    if "crore" not in header[cols["notified_cr"]].lower() or "day" not in header[cols["tenor_days"]].lower():
        raise ReleaseParseError(f"{src}: the amount is not stated in crore or the tenor not in days "
                                f"({header[cols['notified_cr']]!r}, {header[cols['tenor_days']]!r})")
    out = []
    for r in range(h + 1, len(grid)):
        cells = {f: grid[r][c] for f, c in cols.items()}
        if not any(clean_text(v) for v in grid[r]):
            continue
        where = f"{src}: row {r + 1}"
        window = _WINDOW.match(clean_text(cells["window"]))
        if not window:
            raise ReleaseParseError(f"{where}: window {cells['window']!r} is not '09:30 AM to 10:00 AM'")
        rec = {"auction_date": auction_date, "direction": direction,
               "tenor_days": _need(_cnt(cells["tenor_days"], where), where, "tenor"),
               "notified_cr": _need(_num(cells["notified_cr"], where), where, "notified amount"),
               "window_start": _window_time(*window.groups()[:3], where),
               "window_end": _window_time(*window.groups()[3:], where),
               "reversal_date": _need(_date(cells["reversal_date"], _LONG_FORMATS, where), where,
                                      "date of reversal")}
        if rec["notified_cr"] <= 0:
            raise SanityCheckError(f"{where}: notified amount {rec['notified_cr']:g} is not above 0")
        if rec["window_start"] >= rec["window_end"]:
            raise SanityCheckError(f"{where}: the window {rec['window_start']} to {rec['window_end']} does not "
                                   "run forwards")
        if (rec["reversal_date"] - auction_date).days != rec["tenor_days"]:
            raise SanityCheckError(f"{where}: reversal on {rec['reversal_date']} is not the auction date "
                                   f"{auction_date} plus the tenor of {rec['tenor_days']} days")
        weekday = re.search(r"\(([A-Za-z]+day)\)", cells["reversal_date"])
        if weekday and weekday.group(1).lower() != _WEEKDAYS[rec["reversal_date"].weekday()]:
            ctx.notes.append(f"row {r + 1}: the reversal date {rec['reversal_date']} is a "
                             f"{_WEEKDAYS[rec['reversal_date'].weekday()].title()}, not a {weekday.group(1)}")
        if rec["reversal_date"].weekday() >= 5:
            ctx.notes.append(f"row {r + 1}: the reversal date {rec['reversal_date']} falls on a weekend")
        out.append(rec)
    if not out:
        raise ReleaseParseError(f"{src}: no rows under the header of the auction table")
    if len(out) == 1 and out[0]["tenor_days"] != tenor:
        raise SanityCheckError(f"{src}: the table says {out[0]['tenor_days']} days, the title {tenor}")
    if len(out) > 1:
        ctx.notes.append(f"{len(out)} auctions in one release (not seen in any sample): each row is given the "
                         "auction date of the title")
    if direction == "repo":
        ctx.notes.append(_UNSAMPLED_NOTE.format(what="variable rate repo (injection) announcement"))
    return out


# --- Open market operations -------------------------------------------------------------------------

_OMO_RESULT_TITLE = re.compile(r"^detailed result\s*:\s*omo (purchase|sale) auction held on "
                               r"([a-z]{3,9}\.? \d{1,2}, \d{4}) and settlement on ([a-z]{3,9}\.? \d{1,2}, \d{4})$",
                               re.I)
_OMO_ANNOUNCEMENT_TITLE = re.compile(r"^rbi announces omo (purchase|sale) of government of india securities$", re.I)
_OMO_OPERATION = re.compile(r"\bomo (purchase|sale)\b", re.I)
_OMO_AMOUNT_SENTENCE = re.compile(r"aggregate amount of ₹\s*([\d,]+(?:\.\d+)?)\s*crore on " + _LONG_DATE)
_OMO_SETTLEMENT = re.compile(r"SGL account by [^.]*? on " + _LONG_DATE)
_OMO_METHOD = re.compile(r"\b(multiple|uniform) price method", re.I)
_OMO_PROGRAMME = re.compile(r"Press Release (?:No\.? ?)?(\d{4}-\d{2,4}/\d+) dated " + _LONG_DATE)
_ISIN = re.compile(r"^IN[0-9A-Z]{10}$")
_OMO_SUMMARY_ROWS = (
    ("aggregateamount", "aggregate_notified_cr", None),
    ("totalamountoffered", "total_offered_cr", "purchase"),
    ("totalamountbid", "total_offered_cr", "sale"),
    ("totalamountaccepted", "total_accepted_cr", None),
)
_OMO_DETAIL_ROWS = (
    ("noofoffersreceived", "offers_received_count", "purchase"),
    ("noofbidsreceived", "offers_received_count", "sale"),
    ("totalamountfacevalueoffered", "offered_cr", "purchase"),
    ("totalamountfacevaluebid", "offered_cr", "sale"),
    ("noofoffersaccepted", "offers_accepted_count", "purchase"),
    ("noofbidsaccepted", "offers_accepted_count", "sale"),
    ("totalofferamountfacevalueaccepted", "accepted_cr", "purchase"),
    ("totalbidamountfacevalueaccepted", "accepted_cr", "sale"),
    ("cutoffyield", "cutoff_yield", None),
    ("cutoffprice", "cutoff_price", None),
    ("weightedaverageyield", "way", None),
    ("weightedaverageprice", "wap", None),
    ("partialallotment", "partial_allotment_pct", None),
)
_OMO_SUMMARY_FIELDS = ("aggregate_notified_cr", "total_offered_cr", "total_accepted_cr")
_OMO_DETAIL_FIELDS = ("offers_received_count", "offered_cr", "offers_accepted_count", "accepted_cr",
                      "cutoff_yield", "cutoff_price", "way", "wap", "partial_allotment_pct")


def _crore(text: str, where: str) -> float:
    """The number of a cell printed as ``₹50,000 crore``, which may be followed by a remark."""
    m = _CRORE_AMOUNT.match(clean_text(text))
    if not m:
        raise ReleaseParseError(f"{where}: {text!r} is not an amount of the form '₹50,000 crore'")
    return _need(_num(m.group(1), where), where, "amount")


def _operation_in_text(release: Release, operation: str, ctx: ParseContext) -> None:
    spoken = _mentions(_OMO_OPERATION, release.narrative)
    if not spoken:
        ctx.notes.append(f"the text names no OMO purchase or sale to check the title's {operation} against")
    elif spoken != {operation}:
        raise ReleaseParseError(f"{release.source_file}: the title says OMO {operation}, the text OMO "
                                f"{' and '.join(sorted(spoken))}")


def _security_key(name: str, where: str, ctx: ParseContext) -> str | None:
    parsed = parse_security_name(name)
    if parsed.kind == UNKNOWN:
        ctx.notes.append(f"{where}: not a security name this package knows; no name key")
    return parsed.key


def parse_omo_result(release: Release, ctx: ParseContext | None = None) -> list[dict]:
    """Rows of the detailed result of an OMO purchase or sale auction, one per security (``COLUMNS[OMO_RESULT]``)."""
    ctx = ctx or ParseContext()
    src = release.source_file
    m = _title_match(_OMO_RESULT_TITLE, release, "Detailed Result: OMO Purchase Auction held on Mar 13, 2026 and "
                                                 "Settlement on Mar 16, 2026")
    operation = m.group(1).lower()
    auction_date = _need(_date(m.group(2), _LONG_FORMATS, f"{src}: title"), src, "auction date in the title")
    settlement = _need(_date(m.group(3), _LONG_FORMATS, f"{src}: title"), src, "settlement date in the title")
    if settlement < auction_date:
        raise SanityCheckError(f"{src}: settlement {settlement} before the auction {auction_date}")
    _operation_in_text(release, operation, ctx)
    summary_table = _one_table(release, [t for t in release.data_tables if any(
        len(row) > 1 and label_key(row[0].text).startswith("aggregateamount") for row in t.rows)],
        "an 'Aggregate Amount ... notified' row")
    summary, wordings = _labelled_values(
        ((r, row[0].text, row[-1].text) for r, row in enumerate(summary_table.rows, start=1) if len(row) > 1),
        _OMO_SUMMARY_ROWS, f"{src}: summary", ctx)
    _missing_rows(summary, _OMO_SUMMARY_FIELDS, f"{src}: summary")
    totals = {f: _crore(summary[f][1], f"{src}: {summary[f][0]!r}") for f in _OMO_SUMMARY_FIELDS}
    detail = _one_table(release, [t for t in release.data_tables
                                  if t.rows and len(t.rows[0]) > 1 and label_key(t.rows[0][0].text) == "security"],
                        "a first row headed 'Security'")
    grid = detail.grid()
    names = grid[0][1:]
    while names and not names[-1]:
        names.pop()
    if not names or any(not n for n in names):
        raise ReleaseParseError(f"{src}: empty security name in the header {grid[0][1:]}")
    found, more = _labelled_values(((r + 1, row[0], row[1:1 + len(names)]) for r, row in enumerate(grid) if r),
                                   _OMO_DETAIL_ROWS, f"{src}: details", ctx)
    _missing_rows(found, _OMO_DETAIL_FIELDS, f"{src}: details")
    _check_wording(wordings | more, operation, src, "tables")
    _unit_is_crore((found[f][0] for f in ("offered_cr", "accepted_cr")), src)
    sign = 1.0 if operation == "purchase" else -1.0
    tol = ctx.limits.amount_tolerance_cr
    out = []
    for j, name in enumerate(names):
        where = f"{src} [{name}]"
        rec: dict = {"auction_date": auction_date, "settlement_date": settlement, "operation": operation,
                     "security": name, "name_key": _security_key(name, where, ctx)}
        for f in _OMO_DETAIL_FIELDS:
            rec[f] = (_cnt if f.endswith("_count") else _num)(found[f][1][j], f"{where} {found[f][0]!r}")
        for f in ("offers_received_count", "offered_cr", "offers_accepted_count", "accepted_cr"):
            _need(rec[f], where, f)
            if rec[f] < 0:
                raise SanityCheckError(f"{where}: {f} is negative ({rec[f]:g})")
        if rec["offers_accepted_count"] > rec["offers_received_count"]:
            raise SanityCheckError(f"{where}: offers_accepted_count {rec['offers_accepted_count']} is above "
                                   f"offers_received_count {rec['offers_received_count']}")
        if rec["accepted_cr"] > rec["offered_cr"] + tol:
            raise SanityCheckError(f"{where}: accepted_cr {rec['accepted_cr']:g} is above offered_cr "
                                   f"{rec['offered_cr']:g}")
        _check_range(rec, ("cutoff_yield", "way"), ctx.limits.yield_range, "yield", where)
        _check_range(rec, ("cutoff_price", "wap"), ctx.limits.price_range, "price", where)
        _check_range(rec, ("partial_allotment_pct",), (0.0, 100.0), "percentage", where)
        if rec["accepted_cr"] > 0 and (rec["cutoff_yield"] is None or rec["cutoff_price"] is None):
            raise SanityCheckError(f"{where}: an amount accepted but no cut-off yield or price")
        if None not in (rec["way"], rec["cutoff_yield"]) and sign * (rec["way"] - rec["cutoff_yield"]) < -1e-9:
            raise SanityCheckError(f"{where}: weighted average yield {rec['way']:g} is "
                                   f"{'below' if sign > 0 else 'above'} the cut-off yield {rec['cutoff_yield']:g} "
                                   f"of a {operation}")
        if None not in (rec["wap"], rec["cutoff_price"]) and sign * (rec["cutoff_price"] - rec["wap"]) < -1e-9:
            raise SanityCheckError(f"{where}: weighted average price {rec['wap']:g} is "
                                   f"{'above' if sign > 0 else 'below'} the cut-off price {rec['cutoff_price']:g} "
                                   f"of a {operation}")
        rec.update(totals, liquidity_effect_cr=_signed(sign, rec["accepted_cr"]))
        out.append(rec)
    for f, total in (("offered_cr", "total_offered_cr"), ("accepted_cr", "total_accepted_cr")):
        summed = sum(rec[f] for rec in out)
        if abs(summed - totals[total]) > tol * len(out):
            raise SanityCheckError(f"{src}: {f} sums to {summed:g} over the securities but the summary says "
                                   f"{totals[total]:g}")
    accepted, notified = totals["total_accepted_cr"], totals["aggregate_notified_cr"]
    if accepted > notified * (1 + ctx.limits.omo_excess_ratio) + tol:
        raise SanityCheckError(f"{src}: total accepted {accepted:g} is more than "
                               f"{ctx.limits.omo_excess_ratio:.0%} above the aggregate amount notified "
                               f"{notified:g}")
    if accepted > notified + tol:
        ctx.notes.append(f"total accepted {accepted:g} is above the aggregate amount notified {notified:g}")
    if operation == "sale":
        ctx.notes.append(_UNSAMPLED_NOTE.format(what="OMO sale result"))
    return out


def parse_omo_announcement(release: Release, ctx: ParseContext | None = None) -> list[dict]:
    """Rows of an OMO purchase or sale announcement, one per security (``COLUMNS[OMO_ANNOUNCEMENT]`` fields)."""
    ctx = ctx or ParseContext()
    src = release.source_file
    operation = _title_match(_OMO_ANNOUNCEMENT_TITLE, release,
                             "RBI announces OMO Purchase of Government of India Securities").group(1).lower()
    _operation_in_text(release, operation, ctx)
    text = release.narrative
    sentences = _OMO_AMOUNT_SENTENCE.findall(text)
    if len(sentences) != 1:
        raise ReleaseParseError(f"{src}: {len(sentences)} sentences 'aggregate amount of ₹... crore on <date>' "
                                "(one expected: a release announcing several auctions was not sampled)")
    stated = _need(_num(sentences[0][0], f"{src}: text"), src, "aggregate amount in the text")
    auction_date = _need(_date(sentences[0][1], _LONG_FORMATS, f"{src}: text"), src, "auction date in the text")
    m = _OMO_SETTLEMENT.search(text)
    settlement = _date(m.group(1), _LONG_FORMATS, f"{src}: text") if m else None
    if settlement is None:
        ctx.notes.append("no settlement date in the text ('SGL account by ... on <date>'); left blank")
    elif settlement < auction_date:
        raise SanityCheckError(f"{src}: settlement {settlement} before the auction {auction_date}")
    m = _OMO_METHOD.search(text)
    method = m.group(1).lower() if m else None
    m = _OMO_PROGRAMME.search(text)
    programme_no = m.group(1) if m else None
    programme_date = _date(m.group(2), _LONG_FORMATS, f"{src}: text") if m else None
    table, h, cols = _column_table(release, {"isin": "isin", "security": "security",
                                             "maturity_date": "dateofmaturity",
                                             "aggregate_notified_cr": "aggregateamount"},
                                   ("sno", "slno", "srno"), ctx, "securities")
    grid = table.grid()
    out = []
    for r in range(h + 1, len(grid)):
        cells = {f: grid[r][c] for f, c in cols.items()}
        if not any(clean_text(v) for v in grid[r]):
            continue
        where = f"{src}: row {r + 1} [{cells['security']}]"
        isin = clean_text(cells["isin"]).upper()
        if not _ISIN.match(isin):
            raise ReleaseParseError(f"{where}: {cells['isin']!r} is not an ISIN (IN and ten letters or digits)")
        if not is_valid_isin(isin):
            ctx.notes.append(f"{where}: the check digit of {isin} is wrong")
        if not cells["security"]:
            raise ReleaseParseError(f"{where}: no security name")
        parsed = parse_security_name(cells["security"])
        maturity = _need(_date(cells["maturity_date"], ("%d-%b-%Y", "%d-%B-%Y", *_LONG_FORMATS), where), where,
                         "date of maturity")
        amount = _crore(cells["aggregate_notified_cr"], where)
        if abs(amount - stated) > ctx.limits.amount_tolerance_cr:
            raise SanityCheckError(f"{where}: the table's amount {amount:g} is not the {stated:g} of the text "
                                   "(amounts notified per security were not sampled)")
        if maturity <= auction_date:
            raise SanityCheckError(f"{where}: maturity {maturity} is not after the auction {auction_date}")
        if parsed.kind == UNKNOWN:
            ctx.notes.append(f"{where}: not a security name this package knows; no name key")
        elif parsed.maturity_year is not None and parsed.maturity_year != maturity.year:
            ctx.notes.append(f"{where}: the name says {parsed.maturity_year}, the date of maturity {maturity}")
        out.append({"auction_date": auction_date, "settlement_date": settlement, "operation": operation,
                    "isin": isin, "security": cells["security"], "name_key": parsed.key, "maturity_date": maturity,
                    "aggregate_notified_cr": amount, "auction_method": method, "programme_pr_no": programme_no,
                    "programme_date": programme_date})
    if not out:
        raise ReleaseParseError(f"{src}: no securities under the header of the table")
    if operation == "sale":
        ctx.notes.append(_UNSAMPLED_NOTE.format(what="OMO sale announcement"))
    return out


# --- Issuance calendars -----------------------------------------------------------------------------

_PERIOD = re.compile(r"\(" + _LONG_DATE + r" to " + _LONG_DATE + r"\)")
_TITLE_PERIOD = re.compile(r"\bfor ([a-z]+) (\d{4})\s*[-–]\s*([a-z]+) (\d{4})$", re.I)
_WEEK = re.compile(r"^([A-Z][a-z]+) (\d{1,2})(?:, (\d{4}))?\s*[-–]\s*(?:([A-Z][a-z]+) )?(\d{1,2}), (\d{4})$")
_ALLOCATION = re.compile(r"^\(([ivx]+)\)\s*(\d+)\s*Years?\s*(SGrB)?\s*for\s*₹\s*([\d,]+(?:\.\d+)?)\s*crore$", re.I)
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10}
_QUARTER = re.compile(r"\bquarter ([a-z]+)\s*[-–]\s*([a-z]+),? (\d{4})$", re.I)
_QUARTERS = {("april", "june"), ("july", "september"), ("october", "december"), ("january", "march")}
_SDL_TOTAL = re.compile(r"expected to be ₹\s*([\d,]+(?:\.\d+)?)\s*crore", re.I)


def _month(name: str) -> int | None:
    return _MONTHS.index(name.lower()) + 1 if name.lower() in _MONTHS else None


def _week(text: str, where: str) -> tuple[dt.date, dt.date]:
    """First and last day of an auction week printed in one of the calendar's three forms."""
    m = _WEEK.match(clean_text(text))
    months = (_month(m.group(1)), _month(m.group(4) or m.group(1))) if m else (None, None)
    if not m or None in months:
        raise ReleaseParseError(f"{where}: auction week {text!r} is not of the form 'October 05-09, 2026', "
                                "'September 28-October 02, 2026' or 'December 28, 2026-January 01, 2027'")
    try:
        return (dt.date(int(m.group(3) or m.group(6)), months[0], int(m.group(2))),
                dt.date(int(m.group(6)), months[1], int(m.group(5))))
    except ValueError:
        raise ReleaseParseError(f"{where}: auction week {text!r} has a day that does not exist") from None


def _calendar_period(release: Release, table: Table, h: int, ctx: ParseContext) -> tuple[dt.date, dt.date]:
    """The period a GoI calendar covers: from the rows above the table's header, else the text, else the title."""
    src = release.source_file
    title = _TITLE_PERIOD.search(clean_text(release.title))
    months = (_month(title.group(1)), _month(title.group(3))) if title else (None, None)
    named = None
    if title and None not in months:
        last = int(title.group(4)), months[1]
        named = (dt.date(int(title.group(2)), months[0], 1), dt.date(*last, calendar.monthrange(*last)[1]))
    for source, text in (("table", " ".join(c.text for row in table.rows[:h] for c in row)),
                         ("text", release.narrative)):
        m = _PERIOD.search(text)
        if m:
            start = _need(_date(m.group(1), _LONG_FORMATS, f"{src}: period"), src, "start of the period")
            end = _need(_date(m.group(2), _LONG_FORMATS, f"{src}: period"), src, "end of the period")
            if source == "text":
                ctx.notes.append("the table states no period; taken from the text")
            if named and (start.year, start.month, end.year, end.month) != (
                    named[0].year, named[0].month, named[1].year, named[1].month):
                ctx.notes.append(f"the period {start} to {end} differs from the months in the title")
            if end <= start:
                raise SanityCheckError(f"{src}: the period {start} to {end} does not run forwards")
            return start, end
    if named is None:
        raise ReleaseParseError(f"{src}: no period '(<date> to <date>)' in the table or the text and no months in "
                                "the title")
    ctx.notes.append("no period '(<date> to <date>)' in the table or the text; taken as the months of the title")
    return named


def parse_gsec_calendar(release: Release, ctx: ParseContext | None = None) -> list[dict]:
    """Rows of a GoI dated-securities issuance calendar, one per allocation line (``COLUMNS[GSEC_CALENDAR]``)."""
    ctx = ctx or ParseContext()
    src = release.source_file
    table, h, cols = _column_table(release, {"week_no": "slno", "week": "auctionweek", "week_amount_cr": "amount",
                                             "allocation": "securitywiseallocation"}, (), ctx, "calendar")
    grid = table.grid()
    if "crore" not in grid[h][cols["week_amount_cr"]].lower():
        raise ReleaseParseError(f"{src}: the amount column is not in crore ({grid[h][cols['week_amount_cr']]!r})")
    period_start, period_end = _calendar_period(release, table, h, ctx)
    tol = ctx.limits.amount_tolerance_cr
    out, weeks, total = [], {}, None
    for r in range(h + 1, len(grid)):
        cells = {f: grid[r][c] for f, c in cols.items()}
        where = f"{src}: row {r + 1}"
        if not any(clean_text(v) for v in grid[r]):
            continue
        if any(squash(v) == "total" for v in grid[r]):
            total = _need(_num(cells["week_amount_cr"], f"{where} (Total)"), f"{where} (Total)", "amount")
            continue
        m = _ALLOCATION.match(clean_text(cells["allocation"]))
        if not m or m.group(1).lower() not in _ROMAN:
            raise ReleaseParseError(f"{where}: {cells['allocation']!r} is not an allocation line of the form "
                                    "'(i) 3 Year for ₹9,000 crore' (week "
                                    f"{cells['week_no']!r}, {cells['week']!r})")
        number = _need(_cnt(cells["week_no"], where), where, "week number")
        amount = _need(_num(cells["week_amount_cr"], where), where, "amount of the week")
        start, end = _week(cells["week"], where)
        week = weeks.setdefault(number, {"text": cells["week"], "amount": amount, "start": start, "end": end,
                                         "lines": [], "row": r + 1})
        if (week["text"], week["amount"]) != (cells["week"], amount):
            raise ReleaseParseError(f"{where}: week {number} is printed twice, as {week['text']!r} and "
                                    f"{cells['week']!r}")
        rec = {"period_start": period_start, "period_end": period_end, "week_no": number, "week_start": start,
               "week_end": end, "week_amount_cr": amount, "allocation_no": _ROMAN[m.group(1).lower()],
               "tenor_years": int(m.group(2)), "security_type": "SGrB" if m.group(3) else "GS",
               "amount_cr": _need(_num(m.group(4), where), where, "amount")}
        week["lines"].append(rec)
        out.append(rec)
    if not out:
        raise ReleaseParseError(f"{src}: no allocation lines under the header of the calendar")
    if list(weeks) != list(range(1, len(weeks) + 1)):
        raise SanityCheckError(f"{src}: the weeks are numbered {list(weeks)}, not 1 to {len(weeks)} in order")
    previous = None
    for number, week in weeks.items():
        where = f"{src}: week {number} ({week['text']!r}, row {week['row']})"
        start, end = week["start"], week["end"]
        if start.weekday() != 0 or end.weekday() != 4 or (end - start).days != 4:
            raise SanityCheckError(f"{where}: {start} to {end} is not a Monday to the Friday after it")
        if previous is not None and start <= previous:
            raise SanityCheckError(f"{where}: starts on {start}, not after the week before it ended ({previous})")
        previous = end
        summed = sum(line["amount_cr"] for line in week["lines"])
        if abs(summed - week["amount"]) > tol:
            raise SanityCheckError(f"{where}: its allocation lines add up to {summed:g}, not the week's "
                                   f"{week['amount']:g}")
        if [line["allocation_no"] for line in week["lines"]] != list(range(1, len(week["lines"]) + 1)):
            ctx.notes.append(f"week {number}: allocation lines numbered "
                             f"{[line['allocation_no'] for line in week['lines']]}")
        if start < period_start - dt.timedelta(days=6) or end > period_end + dt.timedelta(days=6):
            ctx.notes.append(f"week {number} ({start} to {end}) lies outside the period {period_start} to "
                             f"{period_end}")
    if total is None:
        ctx.notes.append("no Total row to check the weeks against")
    else:
        summed = sum(week["amount"] for week in weeks.values())
        if abs(summed - total) > tol:
            raise SanityCheckError(f"{src}: the weeks add up to {summed:g} but the Total row says {total:g}")
    for rec in out:
        rec["total_cr"] = total
    return out


def parse_sdl_calendar(release: Release, ctx: ParseContext | None = None) -> list[dict]:
    """The one row a state borrowing calendar's page gives: the quarter and its total (``COLUMNS[SDL_CALENDAR]``)."""
    ctx = ctx or ParseContext()
    src = release.source_file
    m = _title_match(_QUARTER, release, "Indicative Calendar of Market borrowings by State Governments/ Union "
                                        "Territories for the Quarter October – December 2026")
    first, last, year = m.group(1).lower(), m.group(2).lower(), int(m.group(3))
    if (first, last) not in _QUARTERS:
        raise SanityCheckError(f"{src}: {m.group(1)} to {m.group(2)} is not a quarter of the financial year")
    totals = _SDL_TOTAL.findall(release.text)
    if len(totals) != 1:
        raise ReleaseParseError(f"{src}: {len(totals)} sentences '... expected to be ₹... crore' (one expected)")
    annexes = [clean_text(a.attrs.get("href", "")).replace(" ", "%20") for a in release.body.iter("a")
               if re.match(r"annex", clean_text(a.text()), re.I) and clean_text(a.attrs.get("href", ""))]
    if not annexes:
        ctx.notes.append("no links named Annex in the text; annex_urls left blank")
    if release.data_tables:
        ctx.notes.append(f"{len(release.data_tables)} tables in the release were not read (the sampled page had "
                         "none)")
    total = _need(_num(totals[0], f"{src}: total"), src, "total")
    if total <= 0:
        raise SanityCheckError(f"{src}: the quarter's total {total:g} is not above 0")
    return [{"quarter_start": dt.date(year, _month(first), 1),
             "quarter_end": dt.date(year, _month(last), calendar.monthrange(year, _month(last))[1]),
             "total_cr": total, "annex_urls": " ".join(annexes) or None}]


# --- Registration -----------------------------------------------------------------------------------

def _t(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.I)


_NOT_SAMPLED = "no page or feed item of this kind has been sampled"

OPERATION_TYPES: tuple[ReleaseType, ...] = (
    ReleaseType(MMO, _t(r"^money market operations as on\b"), parse_money_market_operations,
                description="Daily money market operations"),
    ReleaseType(LAF_ANNOUNCEMENT, _t(r"\bto conduct .*\bvariable rate (?:reverse )?repo\b"),
                parse_laf_auction_announcement, description="Variable rate repo or reverse repo auction"),
    ReleaseType(LAF_RESULT, _t(r"^result of the .*\bvariable rate (?:reverse )?repo\b"), parse_laf_auction_result,
                description="Variable rate repo or reverse repo auction result"),
    ReleaseType(OMO_ANNOUNCEMENT, _t(r"^rbi announces omo (?:purchase|sale) of government of india securities$"),
                parse_omo_announcement, description="Open market operation announcement"),
    ReleaseType(OMO_RESULT, _t(r"^detailed result\s*:\s*omo (?:purchase|sale) auction held on\b"), parse_omo_result,
                description="Open market operation detailed result"),
    ReleaseType(GSEC_CALENDAR, _t(r"^issuance calendar for marketable dated securities\b"), parse_gsec_calendar,
                description="Half-yearly GoI dated securities issuance calendar"),
    ReleaseType(SDL_CALENDAR, _t(r"^indicative calendar of market borrowings? by state governments?\b"),
                parse_sdl_calendar, description="Quarterly indicative calendar of state borrowings"),
    # Recognised, not parsed: the release index then records each event's kind and, for feed copies, its time.
    ReleaseType("omo_cutoff", _t(r"^open market operations? \(omo\)\s*[-–]\s*(?:purchase|sale) of government of "
                                 r"india securities held on .*:\s*cut-?offs?$"),
                description="Open market operation quick result (cut-offs)",
                no_parser=f"{_NOT_SAMPLED}; the detailed result follows the same day and has a parser"),
    ReleaseType("tbill_calendar", _t(r"^(?:revised )?calendar for auction of government of india treasury bills$"),
                description="Quarterly Treasury bill auction calendar",
                no_parser=f"{_NOT_SAMPLED} (RBI's site refused the one request made for it)"),
    ReleaseType("mpc_resolution", _t(r"\bresolution of the monetary policy committee\b"),
                description="Monetary Policy Committee resolution",
                no_parser="prose, six a year, not sampled: keep the policy rates by hand and check them against "
                          "the SDF and MSF rates of money_market_operations"),
    ReleaseType("fx_swap_result", _t(r"^result of the usd/inr (?:buy/sell|sell/buy) swap auction\b"),
                description="USD/INR swap auction result", no_parser=_NOT_SAMPLED),
)

for _rt in OPERATION_TYPES:
    register(_rt)
