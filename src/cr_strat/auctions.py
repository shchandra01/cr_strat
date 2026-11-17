"""Sovereign auctions in one table: FIMMDA's compiled files, RBI's Handbook Table 221 and RBI's releases.

Nothing here downloads. Inputs are files saved by hand (``data/README.md``):

* **FIMMDA's yearly G-sec auction workbooks** (``Gsec_Auction_data_YYYY_YYYY.xlsx``),
  read by ``load_fimmda_gsec``. Every sheet of every file is read; each file
  repeats the older years, so rows are merged on (auction date, security).
  Identical copies collapse into one row that lists every copy in
  ``source_files``; copies that disagree are kept once and listed in
  ``conflicts``, never dropped silently. Sheet names are not trusted: the
  financial year comes from each auction date, a sheet's year is the year most
  of its rows fall in, and a row outside it is flagged ``outside_sheet_year``
  (the 2023-24 sheets carry one 2025-26 row). Amounts are ₹ crore of face
  value ("Total Rs. in crores"; the column headers say "Rs."). From FY2025-26
  "Amount Accepted" is the competitive amount (it matches the Handbook's
  competitive column on every auction both hold); before, it is the total
  accepted (``FIMMDA_COMPETITIVE_FROM``: summed, it equals RBI's outstanding
  stock exactly for securities issued and reissued within FY2022-24). It
  leaves out any devolvement on primary dealers: an auction that accepted
  less than 95% of the notified amount is flagged ``shortfall``. Rows with a
  non-integer notified amount (15 between 2017 and 2020, e.g. 14,529.44 of
  8.30% GS 2042 on 22 Mar 2018) look like conversions or switches priced at
  market, not auctions: they are flagged ``non_round_notified``, and on such
  a day a security maturing within a year (8.24% GS 2018, 15,091 at 100.13)
  is flagged ``probable_switch_source``; neither counts as issuance. A day
  whose lines price only for settlement on or before the auction date (the
  four lines of 29 Sep 2016, 100 to 425 crore each) was a special operation
  settled on the day, not an auction: each of its G-sec lines is flagged
  ``auction_day_pricing`` and does not count as issuance either.
* **FIMMDA's SDL auction workbook** (``SDL_Auction_Data_<date>.xlsx``), read by
  ``load_fimmda_sdl``: monthly sheets of ISIN, description, maturity date,
  weighted average yield, NEW/REISSUE and the amount accepted. There are no
  prices. Data sheets are found by content (an ISIN in column B), with or
  without a header row. A sheet that repeats an older one with the auction
  dates moved is dropped: when more than half its NEW rows carry an ISIN
  whose financial year of first issue (characters 5-8) is not the financial
  year of the auction, the sheet is a copy (the 11 Feb 2025 file's "February
  2025" and "March 2025" are its 2024 sheets with the year changed); the
  report says whether another sheet holds the same rows. The coupon comes
  from the description (the Coupon and Cutoff columns hold yields on some
  rows); the cut-off yield of a NEW row is its coupon and a REISSUE row has
  none. Rows whose ISIN digits, State Code and description disagree on the
  state are flagged ``state_conflict``.
* **RBI's Handbook of Statistics, Table 221** ("Details of Central Government
  Market Borrowings", saved HTML page), read by ``load_handbook_auctions``: one
  financial year or so of auctions per edition, with the date of issue
  (settlement), competitive and non-competitive amounts (whole crore) and the
  cut-off price and yield as ``price/yield`` (``100/coupon`` for a new issue,
  ``-/-`` when every bid was rejected).
* **RBI release tables** from ``rbi_releases`` (``parse_releases(...).tables``,
  or the CSVs ``cr-strat rbi-releases`` writes, read by
  ``read_release_tables``): GoI, T-bill and SDL results, with settlement and
  repayment dates from the matching announcements. A GoI result finds its
  announcement by name key, else by the printed name (``New GS 2036``), else,
  for a new issue, by maturity year; a key two lines of one auction day share
  with different dates joins nothing and is reported (``announcement_join``).

**The table.** ``build_auctions`` merges the sources into one row per
security per auction (``AUCTION_COLUMNS``), keyed on the auction date and the
name key (``sovereign.parse_security_name``). For each field the most
precise source wins: RBI results, then FIMMDA, then the Handbook (whose
amounts are rounded to the crore); the printed price and yield come from the
source with the most decimals. Disagreements beyond the coarser source's
rounding go to ``conflicts``. ``accepted_cr`` is the amount issued:
competitive plus non-competitive (plus any devolvement on primary dealers)
where a source gives both, else the competitive amount alone, as
``accepted_basis`` says. A total from a more precise source wins over parts
from a coarser one: FIMMDA's exact total before FY2025-26, not the sum of
the Handbook's whole-crore parts. A coupon-less new issue (``New GS 2055``)
sold at par is keyed by its cut-off yield, so FIMMDA's and RBI's lines for
it merge.

**ISINs.** Names are joined to the security master by kind, coupon, maturity
year and state (the name key) with ``sovereign.match_isin``, narrowed by the
auction date: an ISIN joins only if it was live then, even when it is the only
one with the key. A key with two live ISINs (6.57% GS 2033) is settled by the
yield when only one candidate's maturity reproduces it
(``matched_by_yield``); otherwise it stays ``ambiguous``. FIMMDA's SDL ISINs
are added to the master (``printed``). Unmatched names, mostly securities
matured before the master's lists, keep their name key.

**Settlement.** Taken from the release (an RBI announcement), the Handbook's
date of issue, the master's first issue date for a new issue, or else T+1 on
the Mumbai settlement calendar (``settlement_source``). Where the calendar
does not cover the year, the next weekday is used and marked so.

**Repricing.** On every fixed-coupon G-sec, green bond and SDL row with a
price, a printed yield and a maturity date, the yield from the cut-off price
at the settlement date (``bonds.FixedRateBond``, 30E/360) must equal the
printed yield to its printed decimals (a half unit, plus ``TIE_TOLERANCE`` of
a unit for the source's own rounding of ties). When it does not, the first
``SETTLEMENT_SEARCH_WEEKDAYS`` weekdays after the auction are tried, and the
first that reproduces the yield becomes the settlement date
(``settlement_source = 'yield'``, ``repricing = 'settlement_inferred'``). If
that date is a listed holiday, the yield was fixed before the holiday was
declared or moved (22 Jan 2024, 7 Feb 2022, 5-8 Sep 2025) and the security
settled the next business day (``yield_rolled``). A settlement printed by
RBI is never replaced. Rows nothing reproduces are
``not_reproduced`` with the likely cause in ``repricing_note``: a non-round
operation, an odd first coupon period (settling before the first coupon of a
security issued off its coupon schedule: the engine's broken-period rule does
not match RBI's), a yield that fits only on or before the auction date, other
maturity dates that fit (a wrong maturity or ISIN join), or nothing that
fits (a typo in the source). A new issue at par (price 100, yield equal to
the coupon) is ``par_issue``: it reproduces under any settlement and day
count, so it proves nothing. ``settlement_candidates`` lists every weekday
in the window that reproduces the yield; one-day shifts often still
reproduce (the yield moves by a few hundredths of a basis point a day), so a
date is only pinned when it is the sole candidate.

**Maturity inference.** A security missing from the master (matured before
the lists were saved) has no maturity date. ``build_auctions`` tries the
anniversary of its first issue (the settlement of a new issue in the table,
or the issue date an SDL reissue cell prints): accepted as
``issue_anniversary`` if it reproduces every priced reissue of the key. Else
every day of the maturity year is tried; a single fit is
``yield_search``, several or none are refused and reported. Lines of special
operations (``SPECIAL_OPERATION_FLAGS``) are left out of both: under 30E/360
a price fixed for settlement on the auction day fits a maturity one day late
exactly as T+1 settlement fits the true one, so a line whose settlement basis
is not T+1 cannot date a maturity (the conversion legs of 8.24% GS 2018 put
it on 23 Apr 2018; RBI's notifications give 22 Apr). A floating-rate
bond has no fixed coupon to search with and is refused too. The table
``AuctionBuild.maturities`` lists each attempt; the ledger redeems a key left
undated by the end of the year in its name (``issuance``).

**Holiday evidence.** ``settlement_evidence`` lines up, per auction, the
calendar's T+1, the next weekday and the dates the yield allows, plus the
dates the Handbook, the master's first issue dates and FIMMDA's SDL new
issues (whose maturity falls on the anniversary of the issue) imply. A listed
holiday the yields confirm, a weekday the yields show was not a settlement
day, or a listed holiday on which an auction settled is reported there.

Units: amounts in ₹ crore of face value, prices per ₹100 face value, yields
in percent (semi-annual for dated securities, simple for T-bills), dates as
pandas Timestamps. FIMMDA states no terms; RBI grants no reuse licence and
its disclaimer prohibits caching without permission: ask both in writing
before keeping a history (``docs/rates_data_sources.md``).
"""

from __future__ import annotations

import datetime as dt
import math
import re
import warnings
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
import pandas as pd

from cr_strat.bonds import FixedRateBond
from cr_strat.calendars import BusinessCalendar, CalendarCoverageError, mumbai_settlement_calendar
from cr_strat.conventions import GSEC as GSEC_CONVENTIONS
from cr_strat.conventions import SDL as SDL_CONVENTIONS
from cr_strat.htmlparse import check_not_blocked, clean_text, parse_html, parse_number, read_text, squash
from cr_strat.isin import is_valid_isin
from cr_strat.sovereign import (
    FRB,
    GREEN,
    GSEC,
    MASTER_COLUMNS,
    SDL,
    TBILL,
    build_master,
    financial_year,
    isin_fy,
    match_isin,
    normalise_name,
    parse_security_name,
    state_for_isin,
    with_par_coupon,
)

FIXED_COUPON_KINDS = frozenset({GSEC, GREEN, SDL})
# Lines that are not auctions settling at T+1: they do not count as issuance and do not date maturities.
SPECIAL_OPERATION_FLAGS = ("non_round_notified", "probable_switch_source", "auction_day_pricing")
# FIMMDA's "Amount Accepted" is the competitive amount from FY2025-26 (it equals the Handbook's competitive column
# on every auction both hold); before, it is the total accepted (it sums to RBI's outstanding stock exactly for
# securities issued and reissued entirely within FY2022-24).
FIMMDA_COMPETITIVE_FROM = dt.date(2025, 4, 1)
SETTLEMENT_SEARCH_WEEKDAYS = 4
TIE_TOLERANCE = 0.005  # of a unit of the last printed decimal, beyond the half unit
NEW, REISSUE = "new", "reissue"
SOURCE_RANK = {"rbi_result": 0, "fimmda_gsec": 1, "fimmda_sdl": 1, "handbook": 2}
SETTLEMENT_RANK = {"announcement": 0, "handbook": 1, "first_issue_date": 2, "calendar_t1": 3, "weekday_t1": 4}

FIMMDA_GSEC_COLUMNS = (
    "auction_date", "fy", "security", "kind", "name_key", "coupon_pct", "maturity_year", "name_maturity_date",
    "name_new_issue", "notified_cr", "accepted_cr", "cutoff_price", "cutoff_price_text", "cutoff_yield",
    "cutoff_yield_text", "status", "flags", "source_file", "sheet", "source_row", "source_files",
)
FIMMDA_SDL_COLUMNS = (
    "auction_date", "fy", "isin", "description", "name_key", "state_code", "coupon_pct", "maturity_year",
    "maturity_date", "issue_type", "way", "cutoff_yield", "cutoff_yield_text", "accepted_cr", "flags",
    "source_file", "sheet", "source_row",
)
HANDBOOK_COLUMNS = (
    "auction_date", "issue_date", "fy", "security", "kind", "name_key", "coupon_pct", "maturity_year",
    "notified_cr", "maturity_years", "comp_received_count", "comp_received_cr", "nc_received_count",
    "nc_received_cr", "comp_accepted_count", "comp_accepted_cr", "nc_accepted_count", "nc_accepted_cr",
    "devolvement_cr", "cutoff_price", "cutoff_price_text", "cutoff_yield", "cutoff_yield_text", "status",
    "source_file", "source_row",
)
LINE_COLUMNS = (
    "source", "auction_date", "kind", "security", "name_key", "coupon_pct", "maturity_year", "state_code",
    "tenor_days", "isin", "maturity_date", "maturity_source", "first_issue_date", "notified_cr", "comp_accepted_cr",
    "nc_accepted_cr", "devolvement_cr", "total_accepted_cr", "cutoff_price", "cutoff_price_text", "cutoff_yield",
    "cutoff_yield_text", "issue_type", "issue_type_source", "settlement_date", "settlement_source", "status",
    "flags", "source_file", "source_row",
)
AUCTION_COLUMNS = (
    "auction_id", "auction_date", "fy", "kind", "security", "name_key", "coupon_pct", "maturity_year",
    "state_code", "tenor_days", "isin", "isin_match", "isin_candidates", "maturity_date", "maturity_source",
    "first_issue_date", "settlement_date", "settlement_source", "settlement_candidates", "notified_cr",
    "comp_accepted_cr", "nc_accepted_cr", "devolvement_cr", "accepted_cr", "accepted_basis", "cutoff_price",
    "cutoff_price_text", "cutoff_yield", "cutoff_yield_text", "issue_type", "issue_type_source", "status",
    "counts_as_issue", "repricing", "repriced_yield", "repricing_note", "flags", "sources", "source_file",
    "source_row",
)
SWITCH_COLUMNS = (
    "auction_id", "auction_date", "settlement_date", "settlement_source", "source_security", "source_key",
    "source_isin", "source_match", "destination_security", "destination_key", "destination_isin",
    "destination_match", "notified_cr", "source_accepted_cr", "destination_issued_cr", "dest_cutoff_price",
    "dest_cutoff_yield", "repricing", "repricing_note", "source_file", "source_row",
)
CONFLICT_COLUMNS = ("auction_date", "security", "field", "kept", "kept_source", "other", "other_source")
REPORT_COLUMNS = ("check", "severity", "auction_date", "security", "detail", "source_file", "source_row")
MATURITY_COLUMNS = ("key", "kind", "maturity_year", "maturity_date", "maturity_source", "status", "fits",
                    "checked_auctions", "detail")
EVIDENCE_COLUMNS = (
    "auction_date", "security", "isin", "method", "calendar_t1", "next_weekday", "implied_dates", "pinned",
    "verdict", "detail", "source_file", "source_row",
)


class AuctionLoadError(ValueError):
    """A file is not laid out as its loader expects."""


# --- Small helpers ----------------------------------------------------------------------------------

def _fy_label(day: dt.date) -> str:
    fy = financial_year(day)
    return f"{fy}-{(fy + 1) % 100:02d}"


def _fy_of_label(label: str) -> int | None:
    """First year of a sheet name such as ``2015-2016`` or ``2015-16``; ``None`` if it is not one."""
    m = re.fullmatch(r"\s*(\d{4})\s*-\s*(\d{2}|\d{4})\s*", str(label))
    if not m:
        return None
    first, second = int(m.group(1)), int(m.group(2)[-2:])
    return first if (first + 1) % 100 == second else None


def _text(value) -> str:
    """A cell as clean text: NBSP, tabs and runs of spaces collapsed; ``''`` for blank."""
    if value is None:
        return ""
    return clean_text(str(value).replace("\t", " "))


def _format_decimals(number_format: str | None) -> int | None:
    """Decimals an Excel number format shows (``0.0000`` is 4, accounting ``#,##0.00`` is 2); ``None`` for General."""
    if not number_format or number_format.lower() in ("general", "@"):
        return None
    section = number_format.split(";")[0]
    section = re.sub(r'"[^"]*"', "", section)
    section = re.sub(r"\\.|_.|\*.", "", section)
    m = re.search(r"\.([0#?]+)", section)
    return len(m.group(1)) if m else 0


def _printed(value, number_format: str | None = None, decimals: int | None = None) -> str:
    """Text of a cell as the workbook shows it: text as written, numbers with the format's decimals."""
    if value is None:
        return ""
    if isinstance(value, str):
        return _text(value)
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        places = decimals if decimals is not None else _format_decimals(number_format)
        if places is None:
            return f"{float(value):.10g}"
        return f"{float(value):.{places}f}"
    return _text(value)


def text_decimals(text: str | None) -> int | None:
    """Decimal places of a printed number (``'7.4578'`` is 4, ``'100'`` is 0); ``None`` if it is not a number."""
    if text is None:
        return None
    t = _text(text).rstrip("%")
    m = re.fullmatch(r"[+-]?\d+(?:\.(\d*))?", t)
    if not m:
        return None
    return len(m.group(1) or "")


def _as_printed(value) -> str:
    """A name cell as printed: trimmed (NBSP and tabs too), inner spacing kept (``'7.73%  GS 2034'``)."""
    return "" if value is None else str(value).strip(" \t\r\n\xa0")


def _number(value, what: str) -> float | None:
    """A number from a cell (text with ₹, commas, NBSP or tabs allowed); ``None`` for blank, NA, '-', #ERROR."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{what}: not a number: {value!r}")
    if isinstance(value, (int, float)):
        return None if isinstance(value, float) and math.isnan(value) else float(value)
    text = _text(value)
    if text.upper() in ("#ERROR", "#N/A", "#VALUE!", "#REF!", "#DIV/0!"):
        return None
    try:
        return parse_number(text)
    except ValueError:
        raise ValueError(f"{what}: not a number: {value!r}") from None


def _amount(value: float | None) -> float | None:
    """An amount in crore with float noise removed (FIMMDA amounts have at most 3 decimals)."""
    return None if value is None else round(float(value) + 0.0, 3)


def _as_day(value) -> dt.date | None:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, pd.Timestamp):
        return value.date()
    return pd.Timestamp(value).date()


def _ts(value) -> pd.Timestamp:
    day = _as_day(value)
    return pd.NaT if day is None else pd.Timestamp(day)


def _join(items: Iterable[str]) -> str:
    seen = []
    for item in items:
        for part in str(item).split(";"):
            part = part.strip()
            if part and part not in seen:
                seen.append(part)
    return ";".join(seen)


def _report(check: str, severity: str, detail: str, auction_date=None, security: str = "", source_file: str = "",
            source_row: str = "") -> dict:
    return {"check": check, "severity": severity, "auction_date": _ts(auction_date), "security": security or "",
            "detail": detail, "source_file": source_file or "", "source_row": source_row or ""}


def _load_workbook(path: Path):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # unsupported extensions (conditional formatting)
            return openpyxl.load_workbook(path, data_only=True)
    except Exception as exc:  # zip or XML errors from a file that is not a workbook
        raise AuctionLoadError(f"{path.name}: not an .xlsx workbook ({exc})") from None


def _named(name, price: float | None, price_yield: float | None):
    """Parsed name, with the coupon of a coupon-less new issue at par ('New GS 2055') taken from its cut-off yield."""
    return with_par_coupon(parse_security_name(name), price, price_yield)


# --- FIMMDA G-sec workbooks -------------------------------------------------------------------------

@dataclass
class FimmdaLoad:
    """Rows from FIMMDA workbooks, the merge conflicts and a report of what each sheet held."""

    rows: pd.DataFrame
    conflicts: pd.DataFrame
    report: pd.DataFrame


_GSEC_HEADER = {"auction_date": "auctiondate", "security": "security", "notified": "notifiedamount",
                "accepted": "amountaccepted", "price": "cutoffprice", "yield": "cutoffyield"}


def _gsec_header(ws) -> tuple[int, dict[str, int]] | None:
    for r, row in enumerate(ws.iter_rows(min_row=1, max_row=10), start=1):
        labels = [squash(_text(c.value)) for c in row]
        cols = {}
        for key, prefix in _GSEC_HEADER.items():
            hits = [j for j, label in enumerate(labels) if label.startswith(prefix)]
            if hits:
                cols[key] = hits[0]
        if len(cols) == len(_GSEC_HEADER):
            return r, cols
    return None


def _fimmda_gsec_sheet(path: Path, ws, report: list[dict]) -> list[dict]:
    found = _gsec_header(ws)
    if found is None:
        report.append(_report("fimmda_sheet", "info", f"sheet {ws.title!r}: no auction header; skipped",
                              source_file=path.name))
        return []
    header, cols = found
    rows, unit_seen = [], False
    for r, row in enumerate(ws.iter_rows(min_row=header + 1), start=header + 1):
        cells = {k: row[j] if j < len(row) else None for k, j in cols.items()}
        first = cells["auction_date"].value if cells["auction_date"] is not None else None
        label = " ".join(_text(c.value) for c in row[:3] if c.value is not None)
        if not isinstance(first, (dt.datetime, dt.date)):
            if "total" in label.lower():
                unit_seen = unit_seen or "crore" in label.lower()
            elif any(c.value not in (None, "") for c in row):
                report.append(_report("fimmda_row", "warning", f"row with no auction date: {label[:60]!r}",
                                      source_file=path.name, source_row=f"{ws.title}!{r}"))
            continue
        where = f"{ws.title}!{r}"
        day = _as_day(first)
        raw_name = _as_printed(cells["security"].value)
        flags: list[str] = []
        try:
            notified = _amount(_number(cells["notified"].value, "notified amount"))
            accepted = _amount(_number(cells["accepted"].value, "amount accepted"))
        except ValueError as exc:
            report.append(_report("fimmda_row", "error", str(exc), day, raw_name, path.name, where))
            continue
        price_cell, yield_cell = cells["price"], cells["yield"]
        price_text = _printed(price_cell.value, price_cell.number_format)
        yield_text = _printed(yield_cell.value, yield_cell.number_format)
        values = {}
        for what, txt in (("price", price_text), ("yield", yield_text)):
            try:
                values[what] = _number(txt, f"cut-off {what}")
            except ValueError:
                values[what] = None
                flags.append("bad_price_yield")
                report.append(_report("fimmda_row", "warning", f"cut-off {what} {txt!r} is not a number",
                                      day, raw_name, path.name, where))
        price, yield_ = values["price"], values["yield"]
        if accepted and (txt_is_marker(price_text) or txt_is_marker(yield_text)) and "bad_price_yield" not in flags:
            flags.append("bad_price_yield")
        price_text = price_text if price is not None else ""
        yield_text = yield_text if yield_ is not None else ""
        parsed = _named(raw_name, price, yield_)
        if parsed.kind == "unknown":
            report.append(_report("fimmda_name", "warning", f"unrecognised security name {raw_name!r}",
                                  day, raw_name, path.name, where))
        status = "rejected" if not accepted else "accepted"
        if notified is not None and accepted is not None and accepted > notified + 1e-6:
            flags.append("greenshoe")
        if notified is not None and abs(notified - round(notified)) > 1e-6:
            flags.append("non_round_notified")
        if notified is not None and notified > 200_000:
            raise AuctionLoadError(f"{path.name} {where}: notified amount {notified} is not in crore")
        rows.append({
            "auction_date": pd.Timestamp(day), "fy": _fy_label(day), "security": raw_name, "kind": parsed.kind,
            "name_key": parsed.key, "coupon_pct": parsed.coupon_pct, "maturity_year": parsed.maturity_year,
            "name_maturity_date": _ts(parsed.maturity_date), "name_new_issue": bool(parsed.new_issue),
            "notified_cr": notified, "accepted_cr": accepted, "cutoff_price": price, "cutoff_price_text": price_text,
            "cutoff_yield": yield_, "cutoff_yield_text": yield_text, "status": status, "flags": flags,
            "source_file": path.name, "sheet": ws.title, "source_row": where, "source_files": path.name,
        })
    if rows and not unit_seen:
        report.append(_report("fimmda_sheet", "info", f"sheet {ws.title!r}: no 'Total Rs. in crores' row; "
                              "amounts taken as crore", source_file=path.name))
    _mark_sheet_year(rows, path, ws.title, report)
    return rows


def txt_is_marker(text: str) -> bool:
    """True for the null markers FIMMDA prints instead of a price or yield (``NA``, ``-``, ``#ERROR``)."""
    return _text(text).upper() in ("NA", "N.A.", "-", "#ERROR", "NIL")


def _mark_sheet_year(rows: list[dict], path: Path, sheet: str, report: list[dict]) -> None:
    """Flag rows outside the financial year most of the sheet's rows fall in; note a misleading sheet name."""
    if not rows:
        return
    years = Counter(r["fy"] for r in rows)
    named = _fy_of_label(sheet)
    top = max(years.values())
    tied = sorted(fy for fy, n in years.items() if n == top)
    named_label = f"{named}-{(named + 1) % 100:02d}" if named is not None else None
    modal = named_label if named_label in tied else tied[0]  # the sheet's name only breaks a tie
    if named is not None and f"{named}-{(named + 1) % 100:02d}" != modal:
        report.append(_report("fimmda_sheet", "info", f"sheet {sheet!r} holds FY{modal} auctions "
                              f"({years[modal]} rows); the year comes from each auction date", source_file=path.name))
    for r in rows:
        if r["fy"] != modal:
            r["flags"].append("outside_sheet_year")
            report.append(_report("fimmda_row", "warning", f"auction dated {r['auction_date']:%Y-%m-%d} (FY{r['fy']}) "
                                  f"in a sheet of FY{modal} auctions", r["auction_date"], r["security"],
                                  path.name, r["source_row"]))


def _merge_key(name_key, security: str) -> str:
    return name_key if isinstance(name_key, str) and name_key else normalise_name(security)


def _close(a, b, tol: float) -> bool:
    if a is None or b is None or (isinstance(a, float) and math.isnan(a)) or (isinstance(b, float) and math.isnan(b)):
        return True  # a missing value never conflicts
    return abs(float(a) - float(b)) <= tol


def load_fimmda_gsec(paths: Iterable[str | Path]) -> FimmdaLoad:
    """FIMMDA's yearly G-sec auction workbooks, every sheet, merged on (auction date, security).

    Returns ``FimmdaLoad(rows, conflicts, report)``: ``rows`` has
    ``FIMMDA_GSEC_COLUMNS`` (``flags`` a ``;``-separated list: ``greenshoe``,
    ``non_round_notified``, ``bad_price_yield``, ``outside_sheet_year``,
    ``conflicting_duplicate``); ``conflicts`` lists every field on which two
    copies of a row disagree (``CONFLICT_COLUMNS``). Of two copies the one in
    a sheet of its own financial year wins, then the later file in ``paths``.
    """
    all_rows: list[dict] = []
    report: list[dict] = []
    for order, p in enumerate(paths):
        path = Path(p)
        wb = _load_workbook(path)
        n_before = len(all_rows)
        for ws in wb.worksheets:
            for row in _fimmda_gsec_sheet(path, ws, report):
                row["_order"] = order
                all_rows.append(row)
        report.append(_report("fimmda_file", "info", f"{len(all_rows) - n_before} auction rows in "
                              f"{len(wb.worksheets)} sheets", source_file=path.name))
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in all_rows:
        groups[(row["auction_date"], _merge_key(row["name_key"], row["security"]))].append(row)
    merged, conflicts = [], []
    for (day, _), copies in groups.items():
        copies.sort(key=lambda r: ("outside_sheet_year" not in r["flags"], r["_order"]))
        keep = dict(copies[-1])
        keep["flags"] = list(keep["flags"])
        for other in copies[:-1]:
            for f, tol in (("notified_cr", 1e-6), ("accepted_cr", 1e-6), ("cutoff_price", 1e-9),
                           ("cutoff_yield", 1e-9)):
                if not _close(keep[f], other[f], tol):
                    conflicts.append({"auction_date": day, "security": keep["security"], "field": f,
                                      "kept": keep[f], "kept_source": f"{keep['source_file']} {keep['source_row']}",
                                      "other": other[f],
                                      "other_source": f"{other['source_file']} {other['source_row']}"})
                    if "conflicting_duplicate" not in keep["flags"]:
                        keep["flags"].append("conflicting_duplicate")
            if "outside_sheet_year" in other["flags"] and "outside_sheet_year" not in keep["flags"]:
                report.append(_report("fimmda_row", "info", f"stray row dropped: {other['source_file']} "
                                      f"{other['source_row']} repeats {keep['source_file']} {keep['source_row']}",
                                      day, keep["security"], other["source_file"], other["source_row"]))
        keep["source_files"] = _join(f"{c['source_file']}:{c['source_row']}" for c in copies)
        merged.append(keep)
    for row in merged:
        row.pop("_order", None)
        row["flags"] = ";".join(dict.fromkeys(row["flags"]))
    out = pd.DataFrame(merged, columns=list(FIMMDA_GSEC_COLUMNS))
    if len(out):
        out["maturity_year"] = out["maturity_year"].astype("Int64")
        out = out.sort_values(["auction_date", "security"], kind="stable").reset_index(drop=True)
    for r in conflicts:
        report.append(_report("fimmda_conflict", "warning", f"{r['field']}: {r['kept']} ({r['kept_source']}) "
                              f"against {r['other']} ({r['other_source']})", r["auction_date"], r["security"]))
    return FimmdaLoad(out, pd.DataFrame(conflicts, columns=list(CONFLICT_COLUMNS)),
                      pd.DataFrame(report, columns=list(REPORT_COLUMNS)))


# --- FIMMDA SDL workbook ----------------------------------------------------------------------------

_SDL_FIELDS = ("auction_date", "isin", "description", "maturity_date", "coupon", "way", "indicator", "cutoff",
               "state_code", "state", "accepted")
_SDL_HEADER = {"isin": "isin", "description": "description", "maturity_date": "maturitydate",
               "indicator": "indicator", "state_code": "statecode"}


def _sdl_date(value, what: str) -> dt.date:
    """A FIMMDA SDL date: an Excel date, or text ``DD-MM-YYYY`` (day first)."""
    if isinstance(value, (dt.datetime, dt.date)):
        return _as_day(value)
    text = _text(value)
    m = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})", text)
    if m:
        return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    raise ValueError(f"{what}: not a DD-MM-YYYY date: {value!r}")


def _is_isin(value) -> bool:
    return isinstance(value, str) and is_valid_isin(value.strip().upper())


def _sdl_sheet_rows(path: Path, ws, report: list[dict]) -> list[dict] | None:
    """Rows of one SDL data sheet (``None`` if the sheet holds no ISIN rows)."""
    values = [tuple(c.value for c in row) for row in ws.iter_rows()]
    data_rows = [i for i, v in enumerate(values) if len(v) > 1 and _is_isin(v[1])]
    if not data_rows:
        return None
    header = None
    for i, v in enumerate(values[:data_rows[0] + 1]):
        if len(v) > 1 and squash(_text(v[1])) == "isin":
            header = i
    if header is not None:
        labels = [squash(_text(x)) for x in values[header]]
        expected = {1: "isin", 2: "description", 3: "maturitydate", 6: "indicator", 8: "statecode"}
        wrong = [f"{openpyxl.utils.get_column_letter(j + 1)}={values[header][j]!r}" for j, want in expected.items()
                 if j >= len(labels) or not labels[j].startswith(want)]
        if wrong:
            raise AuctionLoadError(f"{path.name} sheet {ws.title!r}: SDL columns are not where expected ({wrong})")
    out = []
    for i in data_rows:
        v = list(values[i]) + [None] * 12
        where = f"{ws.title}!{i + 1}"
        try:
            auction = _sdl_date(v[0], "auction date")
            maturity = _sdl_date(v[3], "maturity date")
        except ValueError as exc:
            report.append(_report("fimmda_sdl_row", "error", str(exc), None, _text(v[2]), path.name, where))
            continue
        isin = v[1].strip().upper()
        desc = _text(v[2])
        parsed = parse_security_name(desc)
        flags = []
        if parsed.kind != SDL:
            report.append(_report("fimmda_sdl_row", "error", f"description {desc!r} is not an SDL name",
                                  auction, desc, path.name, where))
            continue
        indicator = _text(v[6]).upper()
        if indicator not in ("NEW", "REISSUE"):
            report.append(_report("fimmda_sdl_row", "error", f"indicator {v[6]!r} is neither NEW nor REISSUE",
                                  auction, desc, path.name, where))
            continue
        try:
            way = _number(v[5], "WAY")
            coupon_cell = _number(v[4], "coupon")
            cutoff_cell = _number(v[7], "cutoff")
            accepted = _amount(_number(v[10], "accepted amount"))
        except ValueError as exc:
            report.append(_report("fimmda_sdl_row", "error", str(exc), auction, desc, path.name, where))
            continue
        cell_state = _text(v[8]).upper() or None
        isin_state = state_for_isin(isin)
        states = {s for s in (cell_state, isin_state, parsed.state_code) if s}
        if len(states) > 1:
            flags.append("state_conflict")
            report.append(_report("fimmda_sdl_state", "warning", f"{isin}: ISIN digits say {isin_state}, State Code "
                                  f"{cell_state}, description {parsed.state_code}", auction, desc, path.name, where))
        if parsed.maturity_year != maturity.year or (parsed.maturity_date and parsed.maturity_date != maturity):
            flags.append("maturity_conflict")
        if coupon_cell is not None and abs(coupon_cell - parsed.coupon_pct) > 0.005:
            flags.append("coupon_cell_differs")
        if indicator == "NEW" and cutoff_cell is not None and abs(cutoff_cell - parsed.coupon_pct) > 0.005:
            flags.append("cutoff_cell_differs")
        new = indicator == "NEW"
        out.append({
            "auction_date": pd.Timestamp(auction), "fy": _fy_label(auction), "isin": isin, "description": desc,
            "name_key": parsed.key, "state_code": parsed.state_code or cell_state, "coupon_pct": parsed.coupon_pct,
            "maturity_year": parsed.maturity_year, "maturity_date": pd.Timestamp(maturity),
            "issue_type": NEW if new else REISSUE, "way": way,
            "cutoff_yield": parsed.coupon_pct if new else None,
            "cutoff_yield_text": f"{parsed.coupon_pct:.2f}" if new else "",
            "accepted_cr": accepted, "flags": ";".join(flags), "source_file": path.name, "sheet": ws.title,
            "source_row": where,
        })
    return out


def _repeated_sheet(rows: list[dict]) -> tuple[int, int]:
    """(NEW rows, NEW rows whose ISIN's year of first issue is not the auction's financial year)."""
    new = [r for r in rows if r["issue_type"] == NEW]
    bad = [r for r in new if isin_fy(r["isin"]) != financial_year(r["auction_date"].date())]
    return len(new), len(bad)


def _signature(rows: list[dict]) -> Counter:
    return Counter((r["isin"], r["maturity_date"], r["issue_type"], r["accepted_cr"]) for r in rows)


def load_fimmda_sdl(paths: Iterable[str | Path]) -> FimmdaLoad:
    """FIMMDA's SDL auction workbooks: every data sheet, repeated sheets dropped (see the module docstring).

    Returns ``FimmdaLoad(rows, conflicts, report)`` with ``FIMMDA_SDL_COLUMNS``.
    Rows repeated across files (same auction date and ISIN) are kept once;
    copies that disagree on the amount are listed in ``conflicts``.
    """
    report: list[dict] = []
    kept_rows: list[dict] = []
    for p in paths:
        path = Path(p)
        wb = _load_workbook(path)
        sheets: dict[str, list[dict]] = {}
        notes: dict[str, list[dict]] = {}
        for ws in wb.worksheets:
            notes[ws.title] = []
            rows = _sdl_sheet_rows(path, ws, notes[ws.title])
            if rows is None:
                continue
            sheets[ws.title] = rows
        signatures = {name: _signature(rows) for name, rows in sheets.items()}
        for name, rows in sheets.items():
            n_new, n_bad = _repeated_sheet(rows)
            if not (n_new and n_bad * 2 > n_new):
                report.extend(notes[name])  # row notes of a dropped copy would repeat the original's
            if n_new and n_bad * 2 > n_new:
                twins = [o for o, sig in signatures.items() if o != name and sig == signatures[name]]
                confirm = (f"; its rows equal sheet {twins[0]!r} but for the auction dates" if twins
                           else "; no other sheet holds the same rows")
                report.append(_report("fimmda_sdl_sheet", "warning", f"sheet {name!r} dropped as a repeat: {n_bad} of "
                                      f"{n_new} NEW ISINs were first issued in another financial year{confirm}",
                                      source_file=path.name))
                continue
            report.append(_report("fimmda_sdl_sheet", "info", f"sheet {name!r}: {len(rows)} rows "
                                  f"({n_new} NEW, {n_bad} with an ISIN of another year)", source_file=path.name))
            kept_rows.extend(rows)
    seen: dict[tuple, dict] = {}
    conflicts = []
    for row in kept_rows:
        key = (row["auction_date"], row["isin"])
        if key in seen:
            first = seen[key]
            if not _close(first["accepted_cr"], row["accepted_cr"], 1e-6):
                conflicts.append({"auction_date": row["auction_date"], "security": row["description"],
                                  "field": "accepted_cr", "kept": first["accepted_cr"],
                                  "kept_source": f"{first['source_file']} {first['source_row']}",
                                  "other": row["accepted_cr"],
                                  "other_source": f"{row['source_file']} {row['source_row']}"})
            continue
        seen[key] = row
    rows = list(seen.values())
    new_count = Counter(r["isin"] for r in rows if r["issue_type"] == NEW)
    for isin, n in new_count.items():
        if n > 1:
            report.append(_report("fimmda_sdl_new_twice", "warning", f"{isin} is NEW on {n} auction dates"))
    out = pd.DataFrame(rows, columns=list(FIMMDA_SDL_COLUMNS))
    if len(out):
        out["maturity_year"] = out["maturity_year"].astype("Int64")
        out = out.sort_values(["auction_date", "isin"], kind="stable").reset_index(drop=True)
    return FimmdaLoad(out, pd.DataFrame(conflicts, columns=list(CONFLICT_COLUMNS)),
                      pd.DataFrame(report, columns=list(REPORT_COLUMNS)))


def sdl_listing(rows: pd.DataFrame) -> pd.DataFrame:
    """ISIN, name and maturity of every SDL in FIMMDA rows, for ``sovereign.build_master(extra=...)``."""
    if rows is None or not len(rows):
        return pd.DataFrame(columns=["isin", "name", "maturity_date", "source"])
    first = rows.sort_values("auction_date").drop_duplicates("isin")
    return pd.DataFrame({"isin": first["isin"], "name": first["description"],
                         "maturity_date": first["maturity_date"], "source": "fimmda_sdl"}).reset_index(drop=True)


# --- RBI Handbook Table 221 -------------------------------------------------------------------------

_HBS_DATE = re.compile(r"\d{2}-[A-Z][a-z]{2}-\d{4}")
_HBS_HEADER = ("dateofauction", "dateofissue", "notifiedamount", "maturity", "bidsreceived", "bidsaccepted",
               "devolvement", "indicativeytm", "nomenclature")


def load_handbook_auctions(sources: Iterable[str | Path | bytes]) -> pd.DataFrame:
    """RBI Handbook of Statistics Table 221 pages (saved HTML) as ``HANDBOOK_COLUMNS``.

    Data rows are the 15-cell rows whose first cell is a ``DD-Mon-YYYY`` date
    under a header naming "Date of Auction" and "Date of Issue" in a table
    whose units row says crore. Amounts are whole crore. Raises
    ``AuctionLoadError`` if a page has no such table.
    """
    rows = []
    for source in sources:
        text, name = read_text(source)
        page = parse_html(text)
        check_not_blocked(page, name)
        found = 0
        for table in page.leaf_tables():
            texts = [squash(c.text) for row in table.rows[:8] for c in row]
            if not any(t.startswith("dateofauction") for t in texts):
                continue
            if not any("crore" in t for t in texts):
                raise AuctionLoadError(f"{name}: Table 221 block {table.index} does not state crore")
            head = next(r for r in table.rows if squash(r[0].text).startswith("dateofauction"))
            labels = [squash(c.text) for c in head]
            if len(labels) != len(_HBS_HEADER) or not all(a.startswith(b) for a, b in zip(labels, _HBS_HEADER)):
                raise AuctionLoadError(f"{name}: Table 221 block {table.index} has columns {labels}, "
                                       f"not {list(_HBS_HEADER)}")
            fy = None
            for r, row in enumerate(table.rows):
                cells = [c.text for c in row]
                if re.fullmatch(r"\d{4}-\d{2}", cells[0]) and not any(cells[1:]):
                    fy = cells[0]
                    continue
                if len(cells) != 15 or not _HBS_DATE.fullmatch(cells[0]):
                    continue
                where = f"table {table.index} row {r + 1}"
                try:
                    auction = dt.datetime.strptime(cells[0], "%d-%b-%Y").date()
                    issue = dt.datetime.strptime(cells[1], "%d-%b-%Y").date()
                    nums = [_number(c, "amount") for c in cells[2:13]]
                except ValueError as exc:
                    raise AuctionLoadError(f"{name} {where}: {exc}") from None
                price_text, _, yield_text = cells[13].partition("/")
                price_text, yield_text = price_text.strip(), yield_text.strip()
                price = None if txt_is_marker(price_text) else _number(price_text, "price")
                yield_ = None if txt_is_marker(yield_text) else _number(yield_text, "yield")
                parsed = _named(cells[14], price, yield_)
                comp_acc, nc_acc = nums[7], nums[9]
                rows.append({
                    "auction_date": pd.Timestamp(auction), "issue_date": pd.Timestamp(issue),
                    "fy": fy or _fy_label(auction), "security": cells[14], "kind": parsed.kind, "name_key": parsed.key,
                    "coupon_pct": parsed.coupon_pct, "maturity_year": parsed.maturity_year, "notified_cr": nums[0],
                    "maturity_years": nums[1], "comp_received_count": nums[2], "comp_received_cr": nums[3],
                    "nc_received_count": nums[4], "nc_received_cr": nums[5], "comp_accepted_count": nums[6],
                    "comp_accepted_cr": comp_acc, "nc_accepted_count": nums[8], "nc_accepted_cr": nc_acc,
                    "devolvement_cr": nums[10], "cutoff_price": price,
                    "cutoff_price_text": price_text if price is not None else "",
                    "cutoff_yield": yield_, "cutoff_yield_text": yield_text if yield_ is not None else "",
                    "status": "accepted" if (comp_acc or 0) + (nc_acc or 0) > 0 else "rejected",
                    "source_file": name, "source_row": where,
                })
                found += 1
        if not found:
            raise AuctionLoadError(f"{name}: no Table 221 auction rows (a 'Date of Auction' table of 15 columns)")
    out = pd.DataFrame(rows, columns=list(HANDBOOK_COLUMNS))
    if len(out):
        out = out.drop_duplicates(subset=["auction_date", "security"], keep="first")
        for c in ("comp_received_count", "nc_received_count", "comp_accepted_count", "nc_accepted_count"):
            out[c] = out[c].astype("Int64")
        out["maturity_year"] = out["maturity_year"].astype("Int64")
        out = out.sort_values(["auction_date", "security"], kind="stable").reset_index(drop=True)
    return out


# --- RBI release tables -----------------------------------------------------------------------------

_RELEASE_KINDS = ("gsec_result", "tbill_result", "sdl_result", "switch_result", "gsec_announcement",
                  "tbill_announcement", "sdl_announcement")
_DATE_COLS = ("release_date", "auction_date", "settlement_date", "repayment_date", "maturity_date",
              "first_issue_date")


def read_release_tables(directory: str | Path) -> dict[str, pd.DataFrame]:
    """The CSVs ``cr-strat rbi-releases`` wrote in ``directory``, typed as ``parse_releases`` gives them."""
    from cr_strat.rbi_releases import COLUMNS

    out = {}
    for kind in _RELEASE_KINDS:
        path = Path(directory) / f"{kind}.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path, dtype=str, keep_default_na=False).replace({"": None})
        for col in df.columns:
            if col in _DATE_COLS or col == "published":
                df[col] = pd.to_datetime(df[col])
            elif col.endswith(("_cr", "_pct", "_count")) or col in (
                    "cutoff_price", "cutoff_yield", "wap", "way", "nc_price", "dest_cutoff_price",
                    "dest_cutoff_yield", "acu_commission_paise", "release_id", "line", "tenor_days",
                    "tenor_years", "maturity_year"):
                df[col] = pd.to_numeric(df[col])
        missing = [c for c in COLUMNS[kind] if c not in df]
        if missing:
            raise AuctionLoadError(f"{path}: not a {kind} table (missing {missing})")
        out[kind] = df
    return out


def _val(row, col):
    value = row.get(col) if hasattr(row, "get") else None
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    return value


_AMBIGUOUS = object()  # an announcement key two rows of one auction day share with different dates


def _announcement_index(frame: pd.DataFrame | None, keys, dates: tuple[str, ...], what: str,
                        report: list[dict]) -> dict:
    """Announcement rows by every key ``keys(row)`` gives; a key two rows share with different dates is ambiguous.

    Rows that agree on ``dates`` (the same line announced twice) are one row.
    An ambiguous key joins nothing and is reported.
    """
    out: dict = {}
    if frame is None or not len(frame):
        return out
    for _, a in frame.iterrows():
        for key in keys(a):
            if key is None or key[0] is None or key[-1] is None:
                continue
            seen = out.get(key)
            if seen is None:
                out[key] = a
            elif seen is not _AMBIGUOUS and not all(_same_day(_val(seen, d), _val(a, d)) for d in dates):
                out[key] = _AMBIGUOUS
                report.append(_report("announcement_join", "warning",
                                      f"{what} lines {seen['security']!r} and {a['security']!r} of the "
                                      f"{key[0]:%Y-%m-%d} auction share the key {key[1:]} with different dates: "
                                      "neither joins a result", key[0], str(a["security"])))
    return out


def _same_day(x, y) -> bool:
    x, y = _ts(x), _ts(y)
    return (x is pd.NaT and y is pd.NaT) or x == y


def _lookup(index: dict, *keys):
    for key in keys:
        if key is None or key[-1] is None or key not in index:
            continue
        hit = index[key]
        return None if hit is _AMBIGUOUS else hit
    return None


def _release_lines(tables: dict[str, pd.DataFrame], report: list[dict] | None = None) -> list[dict]:
    """Auction lines from RBI result tables, with settlement and maturity from matching announcements.

    A GoI result joins its announcement by name key, else by the printed name
    (``New GS 2036``: no coupon until the auction), else, for a new issue, by
    its maturity year when one new issue of that year was announced that day.
    """
    tables = tables or {}
    report = [] if report is None else report
    ann_gsec = _announcement_index(
        tables.get("gsec_announcement"),
        lambda a: [(_as_day(a["auction_date"]), _val(a, "name_key")),
                   (_as_day(a["auction_date"]), normalise_name(a["security"])),
                   (_as_day(a["auction_date"]), "new issue", parse_security_name(a["security"]).maturity_year)
                   if parse_security_name(a["security"]).new_issue else None],
        ("settlement_date", "repayment_date"), "GoI announcement", report)
    ann_tbill = _announcement_index(
        tables.get("tbill_announcement"), lambda a: [(_as_day(a["auction_date"]), int(a["tenor_days"]))],
        ("settlement_date", "maturity_date"), "T-bill announcement", report)
    ann_sdl = _announcement_index(
        tables.get("sdl_announcement"),
        lambda a: [(_as_day(a["auction_date"]), _val(a, "state_code"), NEW, _val(a, "tenor_years"))
                   if _val(a, "issue_type") == NEW else (_as_day(a["auction_date"]), _val(a, "name_key"))],
        ("settlement_date",), "SDL announcement", report)
    lines = []

    def where(r):
        return f"release {int(r['release_id'])} line {int(r['line'])}" if _val(r, "release_id") is not None else ""

    for _, r in (tables.get("gsec_result") if tables.get("gsec_result") is not None else pd.DataFrame()).iterrows():
        day = _as_day(r["auction_date"])
        price, yld = _val(r, "cutoff_price"), _val(r, "cutoff_yield")
        parsed = with_par_coupon(parse_security_name(r["security"]), price, yld)  # 'New GS 2036' at par
        a = _lookup(ann_gsec, (day, parsed.key or _val(r, "name_key")), (day, normalise_name(r["security"])),
                    (day, "new issue", parsed.maturity_year) if parsed.new_issue else None)
        lines.append(_line(
            "rbi_result", day, parsed.kind, r["security"], parsed.key or _val(r, "name_key"), parsed.coupon_pct,
            parsed.maturity_year,
            None, None, notified=_val(r, "notified_cr"), comp=_val(r, "comp_accepted_cr"),
            nc=_val(r, "nc_accepted_cr"), devolvement=_val(r, "devolvement_cr"), total=_val(r, "total_accepted_cr"),
            price=price, price_text=f"{price:.2f}" if price is not None else "",
            yld=yld, yield_text=f"{yld:.4f}" if yld is not None else "",
            settlement=_val(a, "settlement_date") if a is not None else None,
            settlement_source="announcement" if a is not None and _val(a, "settlement_date") is not None else "",
            maturity=_val(a, "repayment_date") if a is not None else None, maturity_source="announcement",
            status=_val(r, "status") or "accepted", source_file=_val(r, "source_file") or "", source_row=where(r)))
    for _, r in (tables.get("tbill_result") if tables.get("tbill_result") is not None else pd.DataFrame()).iterrows():
        day = _as_day(r["auction_date"])
        tenor = int(r["tenor_days"])
        a = _lookup(ann_tbill, (day, tenor))
        price, yld = _val(r, "cutoff_price"), _val(r, "cutoff_yield")
        lines.append(_line(
            "rbi_result", day, TBILL, r["security"], f"{tenor}DTB", None, None, None, tenor,
            notified=_val(r, "notified_cr"), comp=_val(r, "comp_accepted_cr"), nc=_val(r, "nc_accepted_cr"),
            total=_val(r, "total_accepted_cr"), price=price, price_text=f"{price:.4f}" if price is not None else "",
            yld=yld, yield_text=f"{yld:.4f}" if yld is not None else "",
            settlement=_val(a, "settlement_date") if a is not None else None,
            settlement_source="announcement" if a is not None else "",
            maturity=_val(a, "maturity_date") if a is not None else None, maturity_source="announcement",
            status=_val(r, "status") or "accepted", source_file=_val(r, "source_file") or "", source_row=where(r)))
    for _, r in (tables.get("sdl_result") if tables.get("sdl_result") is not None else pd.DataFrame()).iterrows():
        day = _as_day(r["auction_date"])
        issue_type = _val(r, "issue_type")
        key = _val(r, "name_key")
        if issue_type == NEW:
            a = _lookup(ann_sdl, (day, _val(r, "state_code"), NEW, _val(r, "tenor_years")))
        else:
            a = _lookup(ann_sdl, (day, key))
        price, yld = _val(r, "cutoff_price"), _val(r, "cutoff_yield")
        par = issue_type == NEW
        security = re.sub(r"[*#]+$", "", str(r["security"])).strip()
        lines.append(_line(
            "rbi_result", day, SDL, security, key, _val(r, "coupon_pct"), _val(r, "maturity_year"),
            _val(r, "state_code"), None, notified=_val(r, "notified_cr"), comp=_val(r, "comp_accepted_cr"),
            nc=_val(r, "nc_accepted_cr"), total=_val(r, "total_accepted_cr"),
            price=price, price_text=(f"{price:.2f}" if price is not None else ""),
            yld=yld, yield_text=(f"{yld:.2f}" if par else f"{yld:.4f}") if yld is not None else "",
            issue_type=issue_type, issue_type_source="printed" if issue_type else "",
            first_issue=_val(r, "first_issue_date"),
            settlement=_val(a, "settlement_date") if a is not None else None,
            settlement_source="announcement" if a is not None and _val(a, "settlement_date") is not None else "",
            flags=["footnote"] if _val(r, "footnote") else [],
            status=_val(r, "status") or "accepted", source_file=_val(r, "source_file") or "", source_row=where(r)))
    return lines


def _line(source, day, kind, security, key, coupon, year, state, tenor, *, notified=None, comp=None, nc=None,
          devolvement=None, total=None, price=None, price_text="", yld=None, yield_text="", isin=None,
          maturity=None, maturity_source="", first_issue=None, issue_type=None, issue_type_source="", settlement=None,
          settlement_source="", status="accepted", flags=None, source_file="", source_row="") -> dict:
    return {
        "source": source, "auction_date": pd.Timestamp(day), "kind": kind, "security": security, "name_key": key,
        "coupon_pct": None if coupon is None else float(coupon),
        "maturity_year": None if year is None else int(year), "state_code": state,
        "tenor_days": None if tenor is None else int(tenor), "isin": isin, "maturity_date": _ts(maturity),
        "maturity_source": (maturity_source or "printed") if _ts(maturity) is not pd.NaT else "",
        "first_issue_date": _ts(first_issue), "notified_cr": notified, "comp_accepted_cr": comp,
        "nc_accepted_cr": nc, "devolvement_cr": devolvement, "total_accepted_cr": total, "cutoff_price": price,
        "cutoff_price_text": price_text or "", "cutoff_yield": yld, "cutoff_yield_text": yield_text or "",
        "issue_type": issue_type, "issue_type_source": issue_type_source, "settlement_date": _ts(settlement),
        "settlement_source": settlement_source, "status": status, "flags": list(flags or []),
        "source_file": source_file, "source_row": source_row,
    }


def _fimmda_gsec_lines(rows: pd.DataFrame) -> list[dict]:
    out = []
    for _, r in rows.iterrows():
        issue_type, how = (NEW, "name") if r["name_new_issue"] else (None, "")
        competitive = _as_day(r["auction_date"]) >= FIMMDA_COMPETITIVE_FROM
        amount = _val(r, "accepted_cr")
        out.append(_line(
            "fimmda_gsec", r["auction_date"], r["kind"], r["security"], _val(r, "name_key"), _val(r, "coupon_pct"),
            _val(r, "maturity_year"), None, None, notified=_val(r, "notified_cr"),
            comp=amount if competitive else None, total=None if competitive else amount,
            price=_val(r, "cutoff_price"), price_text=r["cutoff_price_text"], yld=_val(r, "cutoff_yield"),
            yield_text=r["cutoff_yield_text"], maturity=_val(r, "name_maturity_date"), maturity_source="name",
            issue_type=issue_type,
            issue_type_source=how, status=r["status"], flags=[f for f in str(r["flags"] or "").split(";") if f],
            source_file=r["source_file"], source_row=r["source_row"]))
    return out


def _fimmda_sdl_lines(rows: pd.DataFrame) -> list[dict]:
    out = []
    for _, r in rows.iterrows():
        out.append(_line(
            "fimmda_sdl", r["auction_date"], SDL, r["description"], _val(r, "name_key"), _val(r, "coupon_pct"),
            _val(r, "maturity_year"), _val(r, "state_code"), None, comp=_val(r, "accepted_cr"),
            yld=_val(r, "cutoff_yield"), yield_text=r["cutoff_yield_text"], isin=r["isin"],
            maturity=r["maturity_date"], maturity_source="fimmda_sdl", issue_type=r["issue_type"],
            issue_type_source="printed",
            status="accepted" if (_val(r, "accepted_cr") or 0) > 0 else "rejected",
            flags=[f for f in str(r["flags"] or "").split(";") if f], source_file=r["source_file"],
            source_row=r["source_row"]))
    return out


def _handbook_lines(rows: pd.DataFrame) -> list[dict]:
    out = []
    for _, r in rows.iterrows():
        comp, nc, dev = _val(r, "comp_accepted_cr"), _val(r, "nc_accepted_cr"), _val(r, "devolvement_cr")
        total = None if comp is None and nc is None else (comp or 0) + (nc or 0)
        price, yld = _val(r, "cutoff_price"), _val(r, "cutoff_yield")
        coupon = _val(r, "coupon_pct")
        par = price == 100 and coupon is not None and yld is not None and abs(yld - coupon) < 1e-9
        out.append(_line(
            "handbook", r["auction_date"], r["kind"], r["security"], _val(r, "name_key"), coupon,
            _val(r, "maturity_year"), None, None, notified=_val(r, "notified_cr"), comp=comp, nc=nc,
            devolvement=dev, total=total, price=price, price_text=r["cutoff_price_text"], yld=yld,
            yield_text=r["cutoff_yield_text"], issue_type=NEW if par else None,
            issue_type_source="handbook_par" if par else "", settlement=r["issue_date"], settlement_source="handbook",
            status=r["status"], source_file=r["source_file"], source_row=r["source_row"]))
    return out


# --- Repricing --------------------------------------------------------------------------------------

AUTHORITATIVE_SETTLEMENT = frozenset({"announcement", "handbook"})


def reproduces(computed: float | None, printed_text: str | None, *, tie_tolerance: float = TIE_TOLERANCE) -> bool:
    """True if ``computed`` rounds to ``printed_text`` (a half unit of its last decimal, plus ``tie_tolerance``)."""
    if computed is None or not printed_text:
        return False
    places = text_decimals(printed_text)
    if places is None:
        return False
    return abs(computed - float(_text(printed_text).rstrip("%"))) <= (0.5 + tie_tolerance) * 10.0 ** -places


def weekdays_after(day: dt.date, n: int) -> list[dt.date]:
    """The first ``n`` Monday-to-Friday dates after ``day`` (holidays included)."""
    out, d = [], day
    while len(out) < n:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return out


def _bond(kind: str, coupon: float, maturity: dt.date, issue: dt.date | None, settle: dt.date) -> FixedRateBond:
    use_issue = issue if issue is not None and issue <= settle and issue < maturity else None
    conventions = SDL_CONVENTIONS if kind == SDL else GSEC_CONVENTIONS
    return FixedRateBond(float(coupon), maturity, conventions, issue_date=use_issue)


def yield_at(kind: str, coupon: float, maturity, price: float, settle, issue=None) -> float | None:
    """Yield (percent) from a clean price at ``settle``, or ``None`` when the bond cannot be priced then.

    ``issue`` (the first issue date) is used when it is on or before
    ``settle``: a first coupon period off the schedule then accrues from it.
    """
    try:
        return _bond(kind, coupon, _as_day(maturity), _as_day(issue), _as_day(settle)).yield_from_price(
            float(price), _as_day(settle))
    except (ValueError, RuntimeError):
        return None


def odd_first_period(coupon: float, maturity, issue, settle) -> bool:
    """True if ``settle`` falls before the first coupon of a bond issued off its coupon schedule."""
    maturity, issue, settle = _as_day(maturity), _as_day(issue), _as_day(settle)
    if issue is None or maturity is None or settle is None or issue >= maturity:
        return False
    try:
        regular = FixedRateBond(float(coupon), maturity)
        return regular.previous_coupon_date(issue) != issue and settle < regular.next_coupon_date(issue)
    except ValueError:
        return False


def is_par_issue(price: float | None, yield_text: str | None, coupon: float | None) -> bool:
    """A new issue at par: price 100 and the printed yield equal to the coupon."""
    if _missing(price) or _missing(coupon) or not yield_text or abs(float(price) - 100.0) > 1e-9:
        return False
    return reproduces(float(coupon), yield_text)


@dataclass(frozen=True)
class Repricing:
    """Result of ``reprice``: ``status``, the settlement used, its source, the dates that fit and a note."""

    status: str  # reproduced | settlement_inferred | not_reproduced | par_issue
    settlement: dt.date | None
    source: str
    candidates: tuple[dt.date, ...] = ()
    computed: float | None = None
    note: str = ""


def _dates(days: Iterable[dt.date]) -> str:
    return ", ".join(f"{d:%Y-%m-%d}" for d in days)


def reprice(kind: str, coupon: float, maturity, price: float, yield_text: str, auction_date, *,
            settlement=None, settlement_source: str = "", issue_date=None, calendar: BusinessCalendar | None = None,
            weekdays: int = SETTLEMENT_SEARCH_WEEKDAYS, flags: Iterable[str] = (), diagnose: bool = True,
            authoritative: Iterable[str] = AUTHORITATIVE_SETTLEMENT) -> Repricing:
    """Check a cut-off price against its printed yield, inferring the settlement date if needed.

    ``settlement`` (with its ``settlement_source``) is tried first. If it
    fails, the first ``weekdays`` weekdays after ``auction_date`` are tried
    and the first that reproduces the yield is returned as
    ``settlement_inferred`` (source ``yield``). If that date is a holiday on
    ``calendar``, the yield was fixed for a day that became a holiday after
    the auction (22 Jan 2024, 7 Feb 2022) or the holiday moved (5 to 8 Sep
    2025): the security settled the next business day (source
    ``yield_rolled``). No date is inferred for a settlement printed by a
    source in ``authoritative``, for a row with one of the
    ``SPECIAL_OPERATION_FLAGS`` (priced on another basis), or in an odd first
    coupon period (the engine's broken-period rule does not match RBI's, so a
    fit there is chance). ``diagnose`` looks for the likely cause when
    nothing fits.
    """
    auction, maturity, settle, issue = (_as_day(x) for x in (auction_date, maturity, settlement, issue_date))
    window = weekdays_after(auction, weekdays)
    fits = tuple(d for d in window if reproduces(yield_at(kind, coupon, maturity, price, d, issue), yield_text))
    computed = yield_at(kind, coupon, maturity, price, settle, issue) if settle is not None else None
    if is_par_issue(price, yield_text, coupon):
        return Repricing("par_issue", settle, settlement_source, fits, computed,
                         "new issue at par: reproduces under any settlement date")
    if settle is not None and reproduces(computed, yield_text):
        return Repricing("reproduced", settle, settlement_source, fits, computed)
    flags = set(flags)
    blocked = (bool(flags & set(SPECIAL_OPERATION_FLAGS)) or settlement_source in set(authoritative)
               or (settle is not None and odd_first_period(coupon, maturity, issue, settle)))
    if fits and not blocked:
        inferred, source = fits[0], "yield"
        note = f"T+1 {settle:%Y-%m-%d} gives {computed:.6f}; " if settle is not None and computed is not None else ""
        note += f"yield fits {_dates(fits)}"
        rolled = _roll_holiday(calendar, inferred)
        if rolled != inferred:
            note += (f"; {inferred:%Y-%m-%d} is a listed holiday (declared or moved after the auction?): "
                     f"settled the next business day, {rolled:%Y-%m-%d}")
            inferred, source = rolled, "yield_rolled"
        return Repricing("settlement_inferred", inferred, source, fits,
                         yield_at(kind, coupon, maturity, price, fits[0], issue), note)
    note = _diagnosis(kind, coupon, maturity, price, yield_text, auction, settle, issue, fits, flags,
                      settlement_source) if diagnose else ""
    return Repricing("not_reproduced", settle, settlement_source, fits, computed, note)


def _roll_holiday(calendar: BusinessCalendar | None, day: dt.date) -> dt.date:
    """``day``, or the next business day if ``calendar`` lists ``day`` as a holiday."""
    if calendar is None:
        return day
    try:
        return day if calendar.is_business_day(day) else calendar.adjust(day, "following")
    except CalendarCoverageError:
        return day


def _diagnosis(kind, coupon, maturity, price, yield_text, auction, settle, issue, fits, flags, settlement_source):
    target = settle or weekdays_after(auction, 1)[0]
    computed = yield_at(kind, coupon, maturity, price, target, issue)
    shown = f"{computed:.6f}" if computed is not None else "no yield"
    causes = []
    if "non_round_notified" in flags:
        causes.append("special operation (non-round amount): probably a conversion or switch, priced otherwise")
    elif "probable_switch_source" in flags:
        causes.append("special operation (converted out on a conversion day): priced otherwise")
    elif "auction_day_pricing" in flags:
        early = [d for d in (auction, auction - dt.timedelta(days=1))
                 if reproduces(yield_at(kind, coupon, maturity, price, d, issue), yield_text)]
        causes.append("special operation: the day's lines price for settlement on or before the auction date"
                      + (f" (this one fits {_dates(early)})" if early else ""))
    if odd_first_period(coupon, maturity, issue, target):
        causes.append("odd first coupon period: the engine's broken-period rule does not match RBI's")
    if fits and settlement_source in AUTHORITATIVE_SETTLEMENT:
        causes.append(f"the {settlement_source} settlement date disagrees: the yield fits {_dates(fits)}")
    if causes:
        return "; ".join(causes) + f" (yield at {target:%Y-%m-%d}: {shown})"
    early = [d for d in (auction, auction - dt.timedelta(days=1))
             if reproduces(yield_at(kind, coupon, maturity, price, d, None), yield_text)]
    if early:
        return (f"fits only on or before the auction date ({_dates(early)}); "
                f"yield at {target:%Y-%m-%d}: {shown}")
    others = _maturity_fits(kind, coupon, maturity.year, [(price, yield_text, target, None, ())], limit=6)
    if others:
        return (f"maturity: {_dates(others)} fit instead of {maturity:%Y-%m-%d} (wrong maturity or ISIN join?); "
                f"yield at {target:%Y-%m-%d}: {shown}")
    return f"nothing fits: price or yield mistyped in the source? yield at {target:%Y-%m-%d}: {shown}"


def _fits(kind: str, coupon: float, maturity: dt.date, row: tuple) -> bool:
    """Whether a (price, yield_text, settlement, issue, other dates) row reproduces with ``maturity``.

    The row reproduces at its settlement date or, failing that, at one of its
    other dates (the weekdays after the auction, where the settlement is in
    doubt).
    """
    price, text, settle, issue, others = row
    return any(reproduces(yield_at(kind, coupon, maturity, price, d, issue), text) for d in (settle, *others))


def _maturity_fits(kind: str, coupon: float, year: int, rows: list[tuple], limit: int | None = None) -> list[dt.date]:
    """Days of ``year`` on which every (price, yield_text, settlement, issue, other dates) row reproduces."""
    out = []
    order = sorted(rows, key=lambda r: abs(float(r[0]) - 100.0), reverse=True)  # most telling first
    day = dt.date(year, 1, 1)
    while day.year == year:
        if all(_fits(kind, coupon, day, r) for r in order):
            out.append(day)
            if limit is not None and len(out) >= limit:
                break
        day += dt.timedelta(days=1)
    return out


# --- Build -------------------------------------------------------------------------------------------

@dataclass
class AuctionBuild:
    """Output of ``build_auctions``: the auction table and what was checked on the way."""

    auctions: pd.DataFrame
    lines: pd.DataFrame
    conflicts: pd.DataFrame
    report: pd.DataFrame
    maturities: pd.DataFrame
    master: pd.DataFrame
    calendar: BusinessCalendar | None = field(default=None, repr=False)


def _missing(value) -> bool:
    if value is None or (isinstance(value, str) and value == ""):
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _calendar_t1(calendar: BusinessCalendar | None, day: dt.date) -> tuple[dt.date, str]:
    if calendar is not None:
        try:
            return calendar.settlement_date(day, 1), "calendar_t1"
        except CalendarCoverageError:
            pass
    return weekdays_after(day, 1)[0], "weekday_t1"


def _extend_master(master: pd.DataFrame | None, listing: pd.DataFrame | None) -> pd.DataFrame:
    """``master`` plus rows for the ISINs in ``listing`` it lacks (built by ``sovereign.build_master``)."""
    base = master.reset_index(drop=True) if master is not None else pd.DataFrame(columns=list(MASTER_COLUMNS))
    if listing is None or not len(listing):
        return base
    new = listing[~listing["isin"].isin(set(base["isin"]))]
    if not len(new):
        return base
    extra = build_master(extra=[new])
    return pd.concat([f for f in (base, extra) if len(f)], ignore_index=True)


_PRICE_TOL = {0: 0.5, 1: 0.05, 2: 0.005, 3: 0.0005, 4: 0.00005}
_AMOUNT_FIELDS = ("comp_accepted_cr", "nc_accepted_cr", "devolvement_cr", "total_accepted_cr")


def _merge(lines: list[dict]) -> tuple[list[dict], list[dict]]:
    """One record per (auction date, kind, key) from the source lines, and the conflicts between them."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for line in lines:
        groups[(line["auction_date"], line["kind"], _merge_key(line["name_key"], line["security"]))].append(line)
    merged, conflicts = [], []
    for (day, _kind, _key), group in groups.items():
        group.sort(key=lambda r: SOURCE_RANK.get(r["source"], 9))
        head = group[0]
        rec = {k: head[k] for k in LINE_COLUMNS}
        rec["flags"] = list(head["flags"])
        rec["sources"] = _join(g["source"] for g in group)
        rec["source_file"] = _join(g["source_file"] for g in group)
        rec["source_row"] = _join(f"{g['source_file']}:{g['source_row']}" if g["source_row"] else g["source_file"]
                                  for g in group)
        head_rank = SOURCE_RANK.get(head["source"], 9)
        rec["_amount_rank"] = {f: head_rank for f in _AMOUNT_FIELDS if not _missing(rec[f])}
        for f in ("notified_cr", "comp_accepted_cr", "nc_accepted_cr", "devolvement_cr", "total_accepted_cr",
                  "isin", "state_code", "tenor_days", "first_issue_date", "coupon_pct", "maturity_year"):
            if _missing(rec[f]):
                for g in group[1:]:
                    if not _missing(g[f]):
                        rec[f] = g[f]
                        if f in _AMOUNT_FIELDS:
                            rec["_amount_rank"][f] = SOURCE_RANK.get(g["source"], 9)
                        break
        if _missing(rec["maturity_date"]):
            for g in group[1:]:
                if not _missing(g["maturity_date"]):
                    rec["maturity_date"], rec["maturity_source"] = g["maturity_date"], g["maturity_source"]
                    break
        for g in group:  # a printed issue type wins over a rule of thumb
            if g["issue_type"] and (not rec["issue_type"] or g["issue_type_source"] == "printed"):
                rec["issue_type"], rec["issue_type_source"] = g["issue_type"], g["issue_type_source"]
                if g["issue_type_source"] == "printed":
                    break
        priced = [g for g in group if g["cutoff_yield_text"]]
        if priced:  # the printed price and yield with the most decimals
            best = max(priced, key=lambda g: (text_decimals(g["cutoff_yield_text"]) or 0,
                                              -SOURCE_RANK.get(g["source"], 9)))
            for f in ("cutoff_price", "cutoff_price_text", "cutoff_yield", "cutoff_yield_text"):
                rec[f] = best[f]
        dated = [g for g in group if g["settlement_source"]]
        if dated:
            best = min(dated, key=lambda g: SETTLEMENT_RANK.get(g["settlement_source"], 9))
            rec["settlement_date"], rec["settlement_source"] = best["settlement_date"], best["settlement_source"]
        for g in group[1:]:
            rec["flags"] += [f for f in g["flags"] if f not in rec["flags"]]
            conflicts.extend(_line_conflicts(day, rec, head, g))
        if any(g["status"] == "accepted" for g in group):
            rec["status"] = "accepted"
        merged.append(rec)
    return merged, conflicts


def _line_conflicts(day, rec, head, other) -> list[dict]:
    out = []
    amount_tol = 1.0 if "handbook" in (head["source"], other["source"]) else 0.0015

    def add(f, kept, value):
        out.append({"auction_date": day, "security": rec["security"], "field": f, "kept": kept,
                    "kept_source": f"{head['source']} {head['source_file']} {head['source_row']}".strip(),
                    "other": value,
                    "other_source": f"{other['source']} {other['source_file']} {other['source_row']}".strip()})

    for f in ("notified_cr", "comp_accepted_cr", "nc_accepted_cr", "total_accepted_cr"):
        if not _close(head[f], other[f], amount_tol):
            add(f, head[f], other[f])
    for f, text in (("cutoff_price", "cutoff_price_text"), ("cutoff_yield", "cutoff_yield_text")):
        if head[text] and other[text]:
            places = min(text_decimals(head[text]) or 0, text_decimals(other[text]) or 0)
            if not _close(head[f], other[f], _PRICE_TOL.get(places, 0.5) + 1e-9):
                add(f, head[text], other[text])
    return out


def build_auctions(*, fimmda_gsec: FimmdaLoad | pd.DataFrame | None = None,
                   fimmda_sdl: FimmdaLoad | pd.DataFrame | None = None, handbook: pd.DataFrame | None = None,
                   releases: dict[str, pd.DataFrame] | None = None, master: pd.DataFrame | None = None,
                   calendar: BusinessCalendar | None = None, infer_maturities: bool = True,
                   weekdays: int = SETTLEMENT_SEARCH_WEEKDAYS) -> AuctionBuild:
    """One row per security per auction from every source given (see the module docstring).

    ``fimmda_gsec`` and ``fimmda_sdl``: what ``load_fimmda_gsec`` and
    ``load_fimmda_sdl`` return (their reports and conflicts are carried
    over), or just their ``rows``. ``handbook``: ``load_handbook_auctions``.
    ``releases``: ``rbi_releases.parse_releases(...).tables`` or
    ``read_release_tables``. ``master``: ``sovereign.build_master`` (or
    ``sovereign.read_master`` of the CSV ``sovereign-master`` writes);
    FIMMDA's SDL ISINs are added to it. ``calendar`` defaults to the shipped
    Mumbai settlement calendar.
    """
    calendar = mumbai_settlement_calendar() if calendar is None else calendar
    report: list[dict] = []
    conflicts: list[dict] = []
    lines: list[dict] = []
    gsec_rows = fimmda_gsec.rows if isinstance(fimmda_gsec, FimmdaLoad) else fimmda_gsec
    sdl_rows = fimmda_sdl.rows if isinstance(fimmda_sdl, FimmdaLoad) else fimmda_sdl
    for load in (fimmda_gsec, fimmda_sdl):
        if isinstance(load, FimmdaLoad):
            report.extend(load.report.to_dict("records"))
            conflicts.extend(load.conflicts.to_dict("records"))
    if gsec_rows is not None and len(gsec_rows):
        lines.extend(_fimmda_gsec_lines(gsec_rows))
    if sdl_rows is not None and len(sdl_rows):
        lines.extend(_fimmda_sdl_lines(sdl_rows))
    if handbook is not None and len(handbook):
        lines.extend(_handbook_lines(handbook))
    lines.extend(_release_lines(releases or {}, report))
    master = _extend_master(master, sdl_listing(sdl_rows) if sdl_rows is not None else None)
    by_isin = master.set_index("isin") if len(master) else None
    merged, found = _merge(lines)
    conflicts.extend(found)
    for c in found:
        report.append(_report("source_conflict", "warning", f"{c['field']}: {c['kept']} ({c['kept_source']}) "
                              f"against {c['other']} ({c['other_source']})", c["auction_date"], c["security"]))
    records = [_join_master(rec, master, by_isin, report) for rec in merged]
    records.sort(key=lambda r: (r["auction_date"], r["kind"], str(r["security"])))
    for rec in records:
        _settle(rec, calendar)
    for rec in records:
        if rec["isin_match"] == "ambiguous":
            _resolve_ambiguous(rec, by_isin, weekdays)
    _issue_types(records)
    _first_issues(records)
    for rec in records:
        if rec["kind"] == TBILL and _missing(rec["maturity_date"]) and not _missing(rec["tenor_days"]):
            rec["maturity_date"] = pd.Timestamp(rec["settlement_date"]) + pd.Timedelta(days=int(rec["tenor_days"]))
            rec["maturity_source"] = "tenor"
    _flag_operations(records)  # before inference: special-operation lines do not date maturities
    _flag_auction_day_pricing(records, weekdays, report)
    maturities = _infer_maturities(records, report, weekdays) if infer_maturities else []
    _late_issue_types(records)
    for rec in records:
        _reprice_record(rec, calendar, weekdays, report)
    for rec in records:
        _finish(rec, report)
    _join_notes(records, report)
    out = pd.DataFrame(records, columns=list(AUCTION_COLUMNS))
    for col in ("maturity_year", "tenor_days"):
        out[col] = out[col].astype("Int64")
    for col in ("auction_date", "maturity_date", "first_issue_date", "settlement_date"):
        out[col] = pd.to_datetime(out[col])
    for col in ("coupon_pct", "notified_cr", "comp_accepted_cr", "nc_accepted_cr", "devolvement_cr", "accepted_cr",
                "cutoff_price", "cutoff_yield", "repriced_yield"):
        out[col] = pd.to_numeric(out[col]).astype(float)
    line_frame = pd.DataFrame([{**ln, "flags": ";".join(ln["flags"])} for ln in lines], columns=list(LINE_COLUMNS))
    return AuctionBuild(
        auctions=out.reset_index(drop=True), lines=line_frame,
        conflicts=pd.DataFrame(conflicts, columns=list(CONFLICT_COLUMNS)),
        report=pd.DataFrame(report, columns=list(REPORT_COLUMNS)),
        maturities=pd.DataFrame(maturities, columns=list(MATURITY_COLUMNS)), master=master, calendar=calendar)


def _join_master(rec: dict, master: pd.DataFrame, by_isin, report: list[dict]) -> dict:
    rec = dict(rec)
    rec["isin_candidates"] = ""
    if rec["kind"] == TBILL:
        rec["isin"], rec["isin_match"] = None, "no_isin"
        return rec
    if not _missing(rec["isin"]):
        rec["isin_match"] = "printed"
        if by_isin is not None and rec["isin"] in by_isin.index:
            m = by_isin.loc[rec["isin"]]
            if _missing(rec["first_issue_date"]) and not _missing(m["issue_date"]):
                rec["first_issue_date"] = m["issue_date"]
        return rec
    rec["isin"] = None
    if not rec["name_key"]:
        rec["isin_match"] = "unparsed"
        return rec
    if by_isin is None:
        rec["isin_match"] = "none"
        return rec
    maturity = None if _missing(rec["maturity_date"]) else rec["maturity_date"]
    match = match_isin(master, rec["name_key"], on=rec["auction_date"], maturity=maturity)
    rec["isin_match"], rec["isin_candidates"] = match.status, ";".join(match.candidates)
    if match.isin is None:
        undated = match_isin(master, rec["name_key"], maturity=maturity) if match.status == "none" else match
        if undated.isin is not None:  # one ISIN has the key, but it was not live on the auction date
            m = by_isin.loc[undated.isin]
            when = f"first issued {m['issue_date']:%Y-%m-%d}" if not _missing(m["issue_date"]) else "issue unknown"
            when += f", maturing {m['maturity_date']:%Y-%m-%d}" if not _missing(m["maturity_date"]) else ""
            report.append(_report("isin_join", "info", f"{rec['name_key']}: the only ISIN with this key, "
                                  f"{undated.isin} ({when}), was not live on the auction date; not joined",
                                  rec["auction_date"], rec["security"], rec["source_file"], rec["source_row"]))
        return rec
    _use_master(rec, match.isin, by_isin.loc[match.isin])
    return rec


def _use_master(rec: dict, isin: str, m) -> None:
    rec["isin"] = isin
    if not _missing(m["maturity_date"]):
        rec["maturity_date"], rec["maturity_source"] = m["maturity_date"], "master"
    if not _missing(m["issue_date"]):
        rec["first_issue_date"] = m["issue_date"]


def _settle(rec: dict, calendar: BusinessCalendar | None) -> None:
    """Settlement date: the source's, a new issue's first issue date, else T+1 on the calendar."""
    if rec["settlement_source"] and rec["settlement_source"] not in ("calendar_t1", "weekday_t1"):
        return
    day = _as_day(rec["auction_date"])
    first = _as_day(rec["first_issue_date"])
    if first is not None and day < first <= day + dt.timedelta(days=7):
        rec["settlement_date"], rec["settlement_source"] = pd.Timestamp(first), "first_issue_date"
        return
    t1, how = _calendar_t1(calendar, day)
    rec["settlement_date"], rec["settlement_source"] = pd.Timestamp(t1), how


def _priced(rec: dict) -> bool:
    return (rec["kind"] in FIXED_COUPON_KINDS and not _missing(rec["coupon_pct"]) and not _missing(rec["cutoff_price"])
            and bool(rec["cutoff_yield_text"]))


def _resolve_ambiguous(rec: dict, by_isin, weekdays: int) -> None:
    """Pick the one candidate ISIN whose maturity reproduces the printed yield, if exactly one does."""
    if not _priced(rec) or by_isin is None:
        return
    day = _as_day(rec["auction_date"])
    dates = [_as_day(rec["settlement_date"]), *weekdays_after(day, weekdays)]
    ok = []
    for isin in [c for c in rec["isin_candidates"].split(";") if c]:
        m = by_isin.loc[isin]
        if _missing(m["maturity_date"]):
            continue
        issue = None if _missing(m["issue_date"]) else m["issue_date"]
        if any(reproduces(yield_at(rec["kind"], rec["coupon_pct"], m["maturity_date"], rec["cutoff_price"], d, issue),
                          rec["cutoff_yield_text"]) for d in dates):
            ok.append(isin)
    if len(ok) == 1:
        _use_master(rec, ok[0], by_isin.loc[ok[0]])
        rec["isin_match"] = "matched_by_yield"
        rec["maturity_source"] = "master"


def _issue_types(records: list[dict]) -> None:
    """New issue or reissue, and how it was decided (``issue_type_source``), in auction-date order."""
    seen: set[str] = set()
    for rec in records:
        key = rec["isin"] or rec["name_key"] or normalise_name(rec["security"])
        accepted = rec["status"] == "accepted"
        if rec["kind"] == TBILL:
            rec["issue_type"], rec["issue_type_source"] = NEW, "tbill"
            continue
        if rec["issue_type_source"] == "printed":
            if accepted:
                seen.add(key)
            continue
        day = _as_day(rec["auction_date"])
        first = _as_day(rec["first_issue_date"])
        if first is not None and rec["isin"]:
            if day < first <= day + dt.timedelta(days=7):
                rec["issue_type"], rec["issue_type_source"] = NEW, "first_issue_date"
            elif first <= day:
                rec["issue_type"], rec["issue_type_source"] = REISSUE, "first_issue_date"
        elif key in seen:
            rec["issue_type"], rec["issue_type_source"] = REISSUE, "earlier_auction"
        elif rec["issue_type"] == NEW:
            pass  # from the name ('NI', 'NEW', 'New GS') or a Handbook '100/coupon'
        elif not _missing(rec["cutoff_price"]) and abs(float(rec["cutoff_price"]) - 100.0) > 1e-9:
            rec["issue_type"], rec["issue_type_source"] = REISSUE, "price_not_par"
        elif is_par_issue(rec["cutoff_price"], rec["cutoff_yield_text"], rec["coupon_pct"]):
            rec["issue_type"], rec["issue_type_source"] = NEW, "par_at_coupon"
        if accepted:
            seen.add(key)


def _first_issues(records: list[dict]) -> None:
    """A first issue date from the settlement of a new issue in the table, for keys no source dates."""
    first: dict[str, pd.Timestamp] = {}
    for rec in records:
        key = rec["isin"] or rec["name_key"]
        if rec["issue_type"] == NEW and rec["status"] == "accepted" and key and key not in first \
                and rec["kind"] in FIXED_COUPON_KINDS:
            first[key] = rec["settlement_date"]
    for rec in records:
        key = rec["isin"] or rec["name_key"]
        if key in first and _missing(rec["first_issue_date"]) and rec["settlement_date"] >= first[key]:
            rec["first_issue_date"] = first[key]


def _infer_maturities(records: list[dict], report: list[dict], weekdays: int) -> list[dict]:
    """Maturity dates for keys with none, from the anniversary of the first issue or a search of the year.

    The anniversary is accepted if every priced reissue reproduces with it,
    at its settlement date or another weekday of the window (the settlement
    of some auctions moved after their yields were fixed). The search of the
    year uses the settlement dates as they are, and only if no day fits that
    way lets them move within the window; it leaves out rows settling within
    184 days of the first issue (an odd first coupon period is priced
    otherwise) and accepts a day only if it is the one day that fits.
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for rec in records:
        if (rec["kind"] in FIXED_COUPON_KINDS and _missing(rec["maturity_date"]) and rec["name_key"]
                and not _missing(rec["maturity_year"]) and rec["isin_match"] != "ambiguous"):
            groups[rec["isin"] or rec["name_key"]].append(rec)
    out = []
    for key, recs in sorted(groups.items()):
        year, kind, coupon = int(recs[0]["maturity_year"]), recs[0]["kind"], float(recs[0]["coupon_pct"])
        first_issue = next((_as_day(r["first_issue_date"]) for r in recs if not _missing(r["first_issue_date"])), None)
        special = [r for r in recs if _priced(r) and any(f in r["flags"] for f in SPECIAL_OPERATION_FLAGS)]
        priced = [r for r in recs if _priced(r) and not any(f in r["flags"] for f in SPECIAL_OPERATION_FLAGS)
                  and not is_par_issue(r["cutoff_price"], r["cutoff_yield_text"], coupon)]
        window = {id(r): tuple(weekdays_after(_as_day(r["auction_date"]), weekdays)) for r in priced}
        flexible = [(r["cutoff_price"], r["cutoff_yield_text"], _as_day(r["settlement_date"]), first_issue,
                     window[id(r)]) for r in priced]
        found, source, detail, fits = None, "", "", 0
        anniversary = None
        if first_issue is not None:
            try:
                anniversary = first_issue.replace(year=year)
            except ValueError:  # 29 February
                anniversary = dt.date(year, 2, 28)
        if anniversary is not None and all(_fits(kind, coupon, anniversary, r) for r in flexible):
            found, source = anniversary, "issue_anniversary"
            detail = f"anniversary of the first issue on {first_issue:%Y-%m-%d}"
            detail += (f"; reproduces all {len(flexible)} priced reissues" if flexible
                       else "; no priced reissue to check it")
        else:
            late = [r for r in priced if first_issue is None
                    or _as_day(r["settlement_date"]) >= first_issue + dt.timedelta(days=184)]
            strict = [(r["cutoff_price"], r["cutoff_yield_text"], _as_day(r["settlement_date"]), None, ())
                      for r in late]
            days, how = (_maturity_fits(kind, coupon, year, strict), "") if strict else ([], "")
            if strict and not days:
                loose = [(p, y, d, None, window[id(r)]) for (p, y, d, _, _), r in zip(strict, late)]
                days, how = _maturity_fits(kind, coupon, year, loose), " (settlement allowed to move in the window)"
            fits = len(days)
            if not strict:
                detail = "no priced reissue to search with: refused"
                if special:
                    detail = (f"no priced reissue to search with ({len(special)} special-operation line(s) left "
                              "out: priced on another settlement basis): refused")
            elif fits == 1:
                found, source = days[0], "yield_search"
                detail = f"the only day of {year} that reproduces all {len(strict)} priced auctions{how}"
            elif fits:
                detail = f"{fits} days of {year} fit ({days[0]:%Y-%m-%d} to {days[-1]:%Y-%m-%d}){how}: refused"
            else:
                detail = f"no day of {year} reproduces all {len(strict)} priced auctions{how}: refused"
            if anniversary is not None:
                detail = f"the anniversary {anniversary:%Y-%m-%d} does not reproduce; " + detail
        out.append({"key": key, "kind": kind, "maturity_year": year, "maturity_date": _ts(found),
                    "maturity_source": source, "status": "inferred" if found else "refused", "fits": fits,
                    "checked_auctions": len(flexible), "detail": detail})
        report.append(_report("maturity_inference", "info" if found else "warning", f"{key}: {detail}",
                              security=key))
        if found is not None:
            for r in recs:
                r["maturity_date"], r["maturity_source"] = pd.Timestamp(found), source
    floaters: dict[str, list[dict]] = defaultdict(list)
    for rec in records:
        if rec["kind"] == FRB and _missing(rec["maturity_date"]) and rec["name_key"] and rec["status"] == "accepted":
            floaters[rec["isin"] or rec["name_key"]].append(rec)
    for key, recs in sorted(floaters.items()):
        year = recs[0]["maturity_year"]
        detail = (f"floating rate: no fixed coupon, so no yield to search {year} with: refused "
                  "(the ledger redeems it by the end of that year)")
        out.append({"key": key, "kind": FRB, "maturity_year": year, "maturity_date": pd.NaT, "maturity_source": "",
                    "status": "refused", "fits": 0, "checked_auctions": 0, "detail": detail})
        report.append(_report("maturity_inference", "warning", f"{key}: {detail}", security=key))
    return out


def _late_issue_types(records: list[dict]) -> None:
    """Issue types the price could not decide, from the maturity: a new issue matures on its anniversary."""
    seen: set[str] = set()
    for rec in records:
        key = rec["isin"] or rec["name_key"] or normalise_name(rec["security"])
        if not rec["issue_type"] and rec["kind"] in FIXED_COUPON_KINDS and not _missing(rec["maturity_date"]):
            settle, maturity = _as_day(rec["settlement_date"]), _as_day(rec["maturity_date"])
            if key in seen:
                rec["issue_type"], rec["issue_type_source"] = REISSUE, "earlier_auction"
            elif (settle.month, settle.day) == (maturity.month, maturity.day):
                rec["issue_type"], rec["issue_type_source"] = NEW, "maturity_anniversary"
        if rec["status"] == "accepted":
            seen.add(key)


def _flag_operations(records: list[dict]) -> None:
    """Flag the other legs of FIMMDA's conversion days and auctions that fell well short of the notified amount.

    On a date with a ``non_round_notified`` row (a conversion or switch
    priced at market, listed by FIMMDA as auction lines), a G-sec maturing
    within about a year of the auction is the security converted out, not an
    issue: ``probable_switch_source``. An accepted amount below 95% of the
    notified one (non-competitive bids are at most 5%) is ``shortfall``: bids
    were rejected, and the rest may have devolved on primary dealers, which
    FIMMDA does not show.
    """
    special = {rec["auction_date"] for rec in records if "non_round_notified" in rec["flags"]}
    for rec in records:
        if (rec["auction_date"] in special and rec["kind"] in (GSEC, GREEN) and "non_round_notified" not in rec["flags"]
                and not _missing(rec["maturity_year"])
                and int(rec["maturity_year"]) - rec["auction_date"].year <= 1):
            rec["flags"].append("probable_switch_source")
        taken = next((float(rec[c]) for c in ("total_accepted_cr", "comp_accepted_cr") if not _missing(rec[c])), None)
        if (rec["kind"] in (GSEC, GREEN, FRB) and not _missing(rec["notified_cr"]) and taken is not None
                and 0 < taken < 0.95 * float(rec["notified_cr"]) and _missing(rec["devolvement_cr"])):
            rec["flags"].append("shortfall")


def _flag_auction_day_pricing(records: list[dict], weekdays: int, report: list[dict]) -> None:
    """Flag the G-sec lines of a day whose dated lines price only for settlement on or before the auction date.

    An auction's cut-off prices reproduce its printed yields at T+1 or a
    later weekday of the window. On 29 Sep 2016 FIMMDA lists four small
    lines (100 to 425 crore) that fit only settlement on the auction day or
    the day before: a special operation settled on the day, not an auction.
    A day is flagged when a line with a known maturity fits only that early
    and no such line of the day reproduces in the window; every G-sec, green
    and floating-rate line of the day then gets ``auction_day_pricing``. Lines
    with no maturity, at par, in an odd first coupon period or already
    flagged as special operations are not evidence either way. Runs before
    maturity inference, so only the master's (or a source's) maturities count.
    """
    early: dict[dt.date, list[str]] = defaultdict(list)
    normal: set[dt.date] = set()
    for rec in records:
        if (rec["kind"] not in (GSEC, GREEN) or not _priced(rec) or _missing(rec["maturity_date"])
                or any(f in rec["flags"] for f in SPECIAL_OPERATION_FLAGS)
                or is_par_issue(rec["cutoff_price"], rec["cutoff_yield_text"], rec["coupon_pct"])):
            continue
        auction, maturity = _as_day(rec["auction_date"]), _as_day(rec["maturity_date"])
        issue, settle = _as_day(rec["first_issue_date"]), _as_day(rec["settlement_date"])
        window = weekdays_after(auction, weekdays)
        if odd_first_period(rec["coupon_pct"], maturity, issue, settle or window[0]):
            continue

        def fits(day, rec=rec, maturity=maturity, issue=issue):
            return reproduces(yield_at(rec["kind"], rec["coupon_pct"], maturity, rec["cutoff_price"], day, issue),
                              rec["cutoff_yield_text"])

        if any(fits(d) for d in ([settle] if settle is not None else []) + list(window)):
            normal.add(auction)
        elif any(fits(d) for d in (auction, auction - dt.timedelta(days=1))):
            early[auction].append(str(rec["security"]))
    for day in sorted(set(early) - normal):
        flagged = [rec for rec in records if _as_day(rec["auction_date"]) == day and rec["kind"] in (GSEC, GREEN, FRB)]
        for rec in flagged:
            rec["flags"].append("auction_day_pricing")
        report.append(_report(
            "special_operation", "warning",
            f"{'; '.join(early[day])} price only for settlement on or before the auction date and no line of the "
            f"day prices at T+1: a special operation settled on the day; its {len(flagged)} G-sec line(s) are "
            "flagged auction_day_pricing, not counted as issuance and not used to infer maturities", day))


def _reprice_record(rec: dict, calendar: BusinessCalendar | None, weekdays: int, report: list[dict]) -> None:
    rec["settlement_candidates"], rec["repriced_yield"], rec["repricing_note"] = "", None, ""
    if not _priced(rec):
        if rec["kind"] in FIXED_COUPON_KINDS:
            why = "no price (all bids rejected)" if rec["status"] == "rejected" else "no cut-off price or yield"
        else:
            why = {TBILL: "T-bill: the yield follows from the tenor, not the dates",
                   FRB: "floating rate"}.get(rec["kind"], "not a fixed-coupon security")
        rec["repricing"], rec["repricing_note"] = "not_checked", why
        return
    if _missing(rec["maturity_date"]):
        why = "no maturity date" + (" (ISIN ambiguous)" if rec["isin_match"] == "ambiguous" else "")
        rec["repricing"], rec["repricing_note"] = "not_checked", why
        return
    result = reprice(rec["kind"], rec["coupon_pct"], rec["maturity_date"], rec["cutoff_price"],
                     rec["cutoff_yield_text"], rec["auction_date"], settlement=rec["settlement_date"],
                     settlement_source=rec["settlement_source"], issue_date=rec["first_issue_date"],
                     calendar=calendar, weekdays=weekdays, flags=rec["flags"])
    rec["repricing"], rec["repriced_yield"], rec["repricing_note"] = result.status, result.computed, result.note
    rec["settlement_candidates"] = ";".join(f"{d:%Y-%m-%d}" for d in result.candidates)
    if result.status == "settlement_inferred":
        rec["settlement_date"], rec["settlement_source"] = pd.Timestamp(result.settlement), result.source
        report.append(_report("settlement_inferred", "info", result.note, rec["auction_date"], rec["security"],
                              rec["source_file"], rec["source_row"]))
    elif result.status == "not_reproduced":
        report.append(_report("repricing", "warning", result.note, rec["auction_date"], rec["security"],
                              rec["source_file"], rec["source_row"]))


def _finish(rec: dict, report: list[dict]) -> None:
    day = _as_day(rec["auction_date"])
    rec["fy"] = _fy_label(day)
    rec["auction_id"] = f"{day:%Y-%m-%d} {_merge_key(rec['name_key'], rec['security'])}"
    comp, nc, dev, total = (rec["comp_accepted_cr"], rec["nc_accepted_cr"], rec["devolvement_cr"],
                            rec["total_accepted_cr"])
    rank = rec.pop("_amount_rank", {})
    split = not _missing(comp) and not _missing(nc)
    if split and not _missing(total) and rank.get("total_accepted_cr", 9) < max(
            rank.get("comp_accepted_cr", 9), rank.get("nc_accepted_cr", 9)):
        accepted, basis = float(total), "total"  # a more precise source's total (FIMMDA's, not the Handbook's sum)
    elif split:
        accepted, basis = float(comp) + float(nc), "total"
    elif not _missing(total):
        accepted, basis = float(total), "total"
    elif not _missing(comp):
        accepted, basis = float(comp), "competitive_only"
    else:
        accepted, basis = None, ""
    if accepted is not None and not _missing(dev) and float(dev) > 0:
        accepted += float(dev)
        rec["flags"].append("devolvement")
    if rec["kind"] == SDL and rec["sources"] == "fimmda_sdl":
        basis = "as_printed"  # FIMMDA's SDL amount: whether it includes non-competitive bids is not stated
    rec["accepted_cr"] = None if accepted is None else round(accepted, 3)
    rec["accepted_basis"] = basis
    if not rec["accepted_cr"]:
        rec["status"] = "rejected"
    if (not _missing(rec["notified_cr"]) and rec["accepted_cr"] and rec["kind"] != TBILL
            and rec["accepted_cr"] > float(rec["notified_cr"]) + 0.001 and "greenshoe" not in rec["flags"]):
        rec["flags"].append("greenshoe")
    special = [f for f in SPECIAL_OPERATION_FLAGS if f in rec["flags"]]
    rec["counts_as_issue"] = bool(rec["status"] == "accepted" and rec["accepted_cr"] and not special)
    if special and rec["status"] == "accepted":
        if special[0] == "non_round_notified":
            why = f"notified {rec['notified_cr']:g} crore is not a round amount: probably a conversion or switch leg"
        elif special[0] == "probable_switch_source":
            why = ("a security maturing within a year, on a day of non-round (conversion) lines: probably the "
                   "security converted out")
        else:
            why = ("a line of a day whose lines price for settlement on or before the auction date: a special "
                   "operation settled on the day, not an auction")
        report.append(_report("special_operation", "warning",
                              f"{why}; not counted as issuance until RBI confirms it", rec["auction_date"],
                              rec["security"], rec["source_file"], rec["source_row"]))
    rec["flags"] = ";".join(dict.fromkeys(rec["flags"]))


def _join_notes(records: list[dict], report: list[dict]) -> None:
    """One report line per name key that found no ISIN (or several), with its auctions' date range."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for rec in records:
        if rec["isin_match"] in ("none", "ambiguous") and rec["status"] == "accepted":
            groups[(rec["isin_match"], rec["name_key"] or rec["security"])].append(rec)
    for (status, key), recs in sorted(groups.items()):
        first, last = recs[0]["auction_date"], recs[-1]["auction_date"]
        why = (f"candidates {recs[0]['isin_candidates']}" if status == "ambiguous"
               else "not in the master (matured before its lists were saved?)")
        report.append(_report("isin_join", "warning" if status == "ambiguous" else "info",
                              f"{status}: {key}, {len(recs)} auctions {first:%Y-%m-%d} to {last:%Y-%m-%d}; {why}",
                              first, recs[0]["security"], recs[0]["source_file"], recs[0]["source_row"]))


# --- Switches ---------------------------------------------------------------------------------------

def normalise_switches(tables: dict[str, pd.DataFrame] | pd.DataFrame | None, master: pd.DataFrame | None = None,
                       calendar: BusinessCalendar | None = None,
                       weekdays: int = SETTLEMENT_SEARCH_WEEKDAYS) -> pd.DataFrame:
    """RBI switch results with ISINs for both legs and a settlement date (``SWITCH_COLUMNS``).

    No release prints a switch's settlement date: it is T+1 on the calendar,
    checked against the destination's cut-off price and yield where its
    maturity is known (and inferred from the yield when T+1 fails).
    """
    frame = tables.get("switch_result") if isinstance(tables, dict) else tables
    if frame is None or not len(frame):
        return pd.DataFrame(columns=list(SWITCH_COLUMNS))
    calendar = mumbai_settlement_calendar() if calendar is None else calendar
    master = master if master is not None else pd.DataFrame(columns=list(MASTER_COLUMNS))
    by_isin = master.set_index("isin") if len(master) else None
    rows = []
    for _, r in frame.iterrows():
        day = _as_day(r["auction_date"])
        settle, how = _calendar_t1(calendar, day)
        legs = {}
        for leg in ("source", "destination"):
            parsed = parse_security_name(r[f"{leg}_security"])
            legs[leg] = (parsed, match_isin(master, r[f"{leg}_security"], on=day) if by_isin is not None else None)
        (src, src_m), (dst, dst_m) = legs["source"], legs["destination"]
        price, yld = _val(r, "dest_cutoff_price"), _val(r, "dest_cutoff_yield")
        status, note = "not_checked", "no destination price (nothing accepted)"
        if price is not None and yld is not None and dst_m is not None and dst_m.isin and dst.coupon_pct is not None:
            m = by_isin.loc[dst_m.isin]
            result = reprice(dst.kind, dst.coupon_pct, m["maturity_date"], price, f"{yld:.4f}", day,
                             settlement=settle, settlement_source=how, calendar=calendar,
                             issue_date=None if _missing(m["issue_date"]) else m["issue_date"], weekdays=weekdays)
            status, note = result.status, result.note
            if result.status == "settlement_inferred":
                settle, how = result.settlement, result.source
        elif price is not None:
            note = "destination ISIN not matched"
        rows.append({
            "auction_id": (f"{day:%Y-%m-%d} switch {src.key or r['source_security']} to "
                           f"{dst.key or r['destination_security']}"),
            "auction_date": pd.Timestamp(day), "settlement_date": pd.Timestamp(settle), "settlement_source": how,
            "source_security": r["source_security"], "source_key": src.key,
            "source_isin": src_m.isin if src_m else None, "source_match": src_m.status if src_m else "none",
            "destination_security": r["destination_security"], "destination_key": dst.key,
            "destination_isin": dst_m.isin if dst_m else None,
            "destination_match": dst_m.status if dst_m else "none",
            "notified_cr": _val(r, "notified_cr"), "source_accepted_cr": _val(r, "source_accepted_cr"),
            "destination_issued_cr": _val(r, "destination_issued_cr"), "dest_cutoff_price": price,
            "dest_cutoff_yield": yld, "repricing": status, "repricing_note": note,
            "source_file": _val(r, "source_file") or "",
            "source_row": (f"release {int(r['release_id'])} line {int(r['line'])}"
                           if _val(r, "release_id") is not None else ""),
        })
    out = pd.DataFrame(rows, columns=list(SWITCH_COLUMNS))
    for col in ("auction_date", "settlement_date"):
        out[col] = pd.to_datetime(out[col])
    return out


# --- Holiday evidence -------------------------------------------------------------------------------

def settlement_evidence(build: AuctionBuild | pd.DataFrame, calendar: BusinessCalendar | None = None, *,
                        fimmda_sdl: FimmdaLoad | pd.DataFrame | None = None,
                        weekdays: int = SETTLEMENT_SEARCH_WEEKDAYS) -> pd.DataFrame:
    """What each auction says about the settlement calendar (``EVIDENCE_COLUMNS``).

    Methods: ``yield`` (the weekdays the printed yield allows, on rows whose
    maturity comes from RBI's lists or a name, not from inference, and not in
    an odd first coupon period), ``handbook``, ``announcement`` and
    ``first_issue_date`` (dates RBI prints), and ``sdl_anniversary`` (a
    FIMMDA SDL new issue whose maturity day and month fall in the window: its
    issue date). Verdicts: ``consistent``; ``holiday_confirmed`` (the next
    weekday is a listed holiday and the evidence rules it out);
    ``missed_holiday`` (the evidence rules out the calendar's T+1 and points
    later: a holiday not on the list, or a settlement fixed before a holiday
    was declared); ``settled_on_holiday`` (it points to a listed holiday);
    ``inconsistent`` (dates on both sides, not T+1). Years the calendar does
    not cover are skipped.
    """
    auctions = build.auctions if isinstance(build, AuctionBuild) else build
    if calendar is None:
        own = build.calendar if isinstance(build, AuctionBuild) else None
        calendar = own if own is not None else mumbai_settlement_calendar()
    rows = []
    for _, a in auctions.iterrows():
        day = _as_day(a["auction_date"])
        try:
            t1 = calendar.settlement_date(day, 1)
        except CalendarCoverageError:
            continue
        window = weekdays_after(day, weekdays)
        base = {"auction_date": a["auction_date"], "security": a["security"], "isin": a["isin"],
                "calendar_t1": pd.Timestamp(t1), "next_weekday": pd.Timestamp(window[0]),
                "source_file": a["source_file"], "source_row": a["source_row"]}
        if (a["repricing"] in ("reproduced", "settlement_inferred")
                and a["maturity_source"] in ("master", "name", "fimmda_sdl", "announcement")
                and not odd_first_period(a["coupon_pct"], a["maturity_date"], a["first_issue_date"], t1)):
            implied = [dt.date.fromisoformat(d) for d in str(a["settlement_candidates"]).split(";") if d]
            rows.append({**base, "method": "yield", "implied_dates": ";".join(f"{d:%Y-%m-%d}" for d in implied),
                         "pinned": len(implied) == 1, "verdict": _verdict(t1, window[0], implied), "detail": ""})
        if a["settlement_source"] in ("handbook", "first_issue_date", "announcement"):
            printed = _as_day(a["settlement_date"])
            rows.append({**base, "method": a["settlement_source"], "implied_dates": f"{printed:%Y-%m-%d}",
                         "pinned": True, "verdict": _verdict(t1, window[0], [printed]), "detail": ""})
    sdl_rows = fimmda_sdl.rows if isinstance(fimmda_sdl, FimmdaLoad) else fimmda_sdl
    if sdl_rows is not None and len(sdl_rows):
        for _, r in sdl_rows[sdl_rows["issue_type"] == NEW].iterrows():
            day = _as_day(r["auction_date"])
            try:
                t1 = calendar.settlement_date(day, 1)
            except CalendarCoverageError:
                continue
            window = weekdays_after(day, weekdays)
            mat = _as_day(r["maturity_date"])
            implied = [d for d in window if (d.month, d.day) == (mat.month, mat.day)]
            if not implied:
                continue
            rows.append({"auction_date": r["auction_date"], "security": r["description"], "isin": r["isin"],
                         "method": "sdl_anniversary", "calendar_t1": pd.Timestamp(t1),
                         "next_weekday": pd.Timestamp(window[0]), "implied_dates": f"{implied[0]:%Y-%m-%d}",
                         "pinned": True, "verdict": _verdict(t1, window[0], implied),
                         "detail": f"matures {mat:%Y-%m-%d}, the anniversary of {implied[0]:%Y-%m-%d}",
                         "source_file": r["source_file"], "source_row": r["source_row"]})
    out = pd.DataFrame(rows, columns=list(EVIDENCE_COLUMNS))
    for col in ("auction_date", "calendar_t1", "next_weekday"):
        out[col] = pd.to_datetime(out[col])
    return out.sort_values(["auction_date", "method", "security"], kind="stable").reset_index(drop=True)


def _verdict(t1: dt.date, next_weekday: dt.date, implied: list[dt.date]) -> str:
    if not implied:
        return "uninformative"
    if t1 in implied:
        return "holiday_confirmed" if next_weekday != t1 and next_weekday not in implied else "consistent"
    before, after = [d for d in implied if d < t1], [d for d in implied if d > t1]
    if before and not after:
        return "settled_on_holiday"
    if after and not before:
        return "missed_holiday"
    return "inconsistent"


def holiday_discrepancies(evidence: pd.DataFrame) -> pd.DataFrame:
    """Evidence that questions the calendar: one row per auction date, verdict and calendar T+1."""
    cols = ["auction_date", "verdict", "calendar_t1", "implied_dates", "methods", "securities"]
    bad = evidence[evidence["verdict"].isin(["missed_holiday", "settled_on_holiday", "inconsistent"])]
    if not len(bad):
        return pd.DataFrame(columns=cols)
    out = (bad.groupby(["auction_date", "verdict", "calendar_t1"], as_index=False)
           .agg(implied_dates=("implied_dates", _join), methods=("method", _join), securities=("security", _join)))
    return out.loc[:, cols].sort_values("auction_date").reset_index(drop=True)


def confirmed_holidays(evidence: pd.DataFrame) -> pd.DataFrame:
    """Listed holidays that an auction's evidence rules out as its settlement day (``holiday_confirmed``)."""
    cols = ["holiday", "auction_date", "methods", "securities"]
    ok = evidence[evidence["verdict"] == "holiday_confirmed"]
    if not len(ok):
        return pd.DataFrame(columns=cols)
    out = (ok.groupby(["next_weekday", "auction_date"], as_index=False)
           .agg(methods=("method", _join), securities=("security", _join))
           .rename(columns={"next_weekday": "holiday"}))
    return out.loc[:, cols].sort_values("holiday").reset_index(drop=True)


# --- Reading back ------------------------------------------------------------------------------------

_BOOL = {"true": True, "false": False, "1": True, "0": False, "yes": True, "no": False}


def _bools(series: pd.Series) -> pd.Series:
    return series.map(lambda v: _BOOL.get(str(v).strip().lower(), False) if v is not None else False).astype(bool)


def read_auctions(path: str | Path) -> pd.DataFrame:
    """An auction table CSV written by ``cr-strat auctions``, typed as ``build_auctions`` gives it.

    Printed price and yield texts stay text, so their decimals survive.
    """
    df = pd.read_csv(path, dtype=str, keep_default_na=False).replace({"": None})
    missing = [c for c in AUCTION_COLUMNS if c not in df]
    if missing:
        raise AuctionLoadError(f"{path}: not an auction table (missing {missing})")
    for col in ("auction_date", "maturity_date", "first_issue_date", "settlement_date"):
        df[col] = pd.to_datetime(df[col])
    for col in ("coupon_pct", "notified_cr", "comp_accepted_cr", "nc_accepted_cr", "devolvement_cr", "accepted_cr",
                "cutoff_price", "cutoff_yield", "repriced_yield"):
        df[col] = pd.to_numeric(df[col]).astype(float)
    for col in ("maturity_year", "tenor_days"):
        df[col] = pd.to_numeric(df[col]).astype("Int64")
    df["counts_as_issue"] = _bools(df["counts_as_issue"])
    for col in ("cutoff_price_text", "cutoff_yield_text", "flags", "sources", "settlement_candidates",
                "isin_candidates", "repricing_note", "issue_type_source", "settlement_source", "maturity_source"):
        df[col] = df[col].fillna("")
    return df


def read_switches(path: str | Path) -> pd.DataFrame:
    """A switch table CSV written by ``cr-strat auctions`` (``SWITCH_COLUMNS``), typed."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False).replace({"": None})
    missing = [c for c in SWITCH_COLUMNS if c not in df]
    if missing:
        raise AuctionLoadError(f"{path}: not a switch table (missing {missing})")
    for col in ("auction_date", "settlement_date"):
        df[col] = pd.to_datetime(df[col])
    for col in ("notified_cr", "source_accepted_cr", "destination_issued_cr", "dest_cutoff_price",
                "dest_cutoff_yield"):
        df[col] = pd.to_numeric(df[col]).astype(float)
    return df
