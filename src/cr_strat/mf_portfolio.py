"""Parse AMC portfolio disclosure workbooks into a holdings table.

Every AMC publishes monthly (and, for debt schemes, fortnightly) portfolio
workbooks, but layouts differ: one sheet per scheme or several schemes stacked
in one sheet, scheme name and date in free-text rows above the table, market
value in rupees, lakhs or crores. The parser looks for header rows that carry
an ISIN column plus a quantity or market value column, reads the scheme name
and as-of date from the rows above each header, and keeps rows whose ISIN
passes the check digit. The per-block report says what was skipped and why.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from cr_strat.isin import is_valid_isin
from cr_strat.schemes import normalize_amc, normalize_scheme_name
from cr_strat.textparse import (
    UNIT_MULTIPLIERS,
    cell_str,
    detect_multiplier,
    parse_date_text,
    to_number,
)

HOLDINGS_COLUMNS = (
    "as_of_date", "holder_type", "holder_id", "holder_name", "amc", "category", "isin",
    "instrument_name", "rating_disclosed", "quantity", "market_value_inr", "market_value_raw",
    "pct_nav", "source_file", "sheet",
)
REPORT_COLUMNS = (
    "source_file", "sheet", "header_row", "scheme_name", "as_of_date", "mv_multiplier",
    "rows", "status", "note",
)
_PREAMBLE_ROWS = 15


def _classify_header(text: str) -> str | None:
    t = text.lower()
    if re.search(r"\bisin\b", t):
        return "isin"
    if re.search(r"yield|ytm|ytc|coupon|maturity", t):
        return None
    if re.search(r"%|percent", t) and re.search(r"net\s*asset|nav|aum|total", t):
        return "pct_nav"
    if re.search(r"market\s*/?\s*fair|market\s*value|fair\s*value|value\s*\(", t):
        return "market_value"
    if re.search(r"quantity|\bqty\b|no\.?\s*of\s*(?:units|shares|bonds|securities)", t):
        return "quantity"
    if "rating" in t:
        return "rating"
    if re.search(r"name|instrument|security|issuer|company", t):
        return "name"
    return None


def _map_header(row: list) -> dict[str, int] | None:
    colmap: dict[str, int] = {}
    for j, cell in enumerate(row):
        field = _classify_header(cell_str(cell))
        if field and field not in colmap:
            colmap[field] = j
    if "isin" in colmap and ("quantity" in colmap or "market_value" in colmap):
        return colmap
    return None


def _row_texts(rows: list[list]) -> list[str]:
    """Joined text of each preamble row, skipping rows that hold a valid ISIN."""
    texts = []
    for row in rows:
        cells = [cell_str(c) for c in row]
        if any(is_valid_isin(c.upper()) for c in cells if c):
            continue
        joined = " | ".join(c for c in cells if c)
        if joined:
            texts.append(joined)
    return texts


def _find_as_of(texts: list[str]) -> pd.Timestamp | None:
    for text in texts:
        m = re.search(r"as\s+(?:on|at)\s*[:\-]?\s*\|?\s*(.+)", text, re.I)
        if m:
            found = parse_date_text(m.group(1))
            if found is not None:
                return found
    return None


def _clean_scheme(text: str) -> str:
    text = text.split(" | ")[0]
    text = re.split(r"\(|\s-\s*an open|\s-\s*a close", text, maxsplit=1, flags=re.I)[0]
    return re.sub(r"\s+", " ", text).strip(" :-")


def _find_scheme_name(texts: list[str]) -> str | None:
    for text in texts:
        m = re.search(r"scheme\s*name\s*[:\-–]?\s*\|?\s*(.+)", text, re.I)
        if m:
            return _clean_scheme(m.group(1))
    for text in texts:
        m = re.search(
            r"portfolio\s+(?:statement\s+)?(?:of|for)\s+(?:the\s+)?(.+?)(?:\s+as\s+(?:on|at)\b|\s+for\s+the\s+(?:period|month)|$)",
            text, re.I,
        )
        if m:
            return _clean_scheme(m.group(1))
    for text in texts:
        first = text.split(" | ")[0]
        if (re.search(r"\bfund\b|\bplan\b|\bfmp\b|\betf\b", first, re.I)
                and not re.fullmatch(r"[\w\s&.\-]*mutual fund\.?", first.strip(), re.I)
                and not re.search(r"portfolio|total|net assets|development fund", first, re.I)):
            return _clean_scheme(first)
    return None


def parse_mf_portfolio(
    path: str | Path,
    amc: str,
    *,
    as_of: pd.Timestamp | str | None = None,
    mv_unit: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return ``(holdings, report)`` for one AMC workbook.

    ``as_of`` and ``mv_unit`` (one of ``UNIT_MULTIPLIERS``) override what the
    parser reads from the file, for layouts where it cannot find them.
    """
    path = Path(path)
    forced_date = pd.Timestamp(as_of).normalize() if as_of is not None else None
    forced_mult = UNIT_MULTIPLIERS[mv_unit] if mv_unit else None
    sheets = pd.read_excel(path, sheet_name=None, header=None, dtype=object)
    records, report = [], []
    amc_key = normalize_amc(amc)

    for sheet, frame in sheets.items():
        rows = frame.values.tolist()
        headers = [(i, cm) for i, row in enumerate(rows) if (cm := _map_header(row))]
        if not headers:
            report.append({"source_file": path.name, "sheet": sheet, "status": "skipped",
                           "note": "no header row with ISIN and quantity/value columns", "rows": 0})
            continue
        prev_end = 0
        for b, (hdr, colmap) in enumerate(headers):
            end = headers[b + 1][0] if b + 1 < len(headers) else len(rows)
            texts = _row_texts(rows[max(prev_end, hdr - _PREAMBLE_ROWS):hdr])
            scheme = _find_scheme_name(texts) or str(sheet)
            as_of_date = forced_date or _find_as_of(texts) or _find_as_of(_row_texts(rows[:hdr]))
            mult = forced_mult
            if mult is None and "market_value" in colmap:
                mult = detect_multiplier(cell_str(rows[hdr][colmap["market_value"]]))
                if mult is None:
                    mult = detect_multiplier(" ".join(texts))
            notes = []
            if as_of_date is None:
                notes.append("as-of date not found; pass --as-of")
            if mult is None and "market_value" in colmap:
                notes.append("market value unit not found; pass --mv-unit")

            count, last_data = 0, hdr
            for i in range(hdr + 1, end):
                row = rows[i]
                isin = cell_str(row[colmap["isin"]]).upper()
                if not is_valid_isin(isin):
                    continue
                last_data = i
                raw_mv = to_number(row[colmap["market_value"]]) if "market_value" in colmap else float("nan")
                records.append({
                    "as_of_date": as_of_date,
                    "holder_type": "MF",
                    "holder_id": f"MF|{amc_key}|{normalize_scheme_name(scheme)}",
                    "holder_name": scheme,
                    "amc": amc,
                    "category": None,
                    "isin": isin,
                    "instrument_name": cell_str(row[colmap["name"]]) if "name" in colmap else None,
                    "rating_disclosed": cell_str(row[colmap["rating"]]) if "rating" in colmap else None,
                    "quantity": to_number(row[colmap["quantity"]]) if "quantity" in colmap else float("nan"),
                    "market_value_inr": raw_mv * mult if mult is not None else float("nan"),
                    "market_value_raw": raw_mv,
                    "pct_nav": to_number(row[colmap["pct_nav"]]) if "pct_nav" in colmap else float("nan"),
                    "source_file": path.name,
                    "sheet": sheet,
                })
                count += 1
            report.append({
                "source_file": path.name, "sheet": sheet, "header_row": hdr + 1,
                "scheme_name": scheme, "as_of_date": as_of_date, "mv_multiplier": mult,
                "rows": count, "status": "parsed" if count else "empty", "note": "; ".join(notes) or None,
            })
            # Rows after this block's last holding belong to the next block's preamble.
            prev_end = last_data + 1

    holdings = pd.DataFrame(records, columns=list(HOLDINGS_COLUMNS))
    holdings["as_of_date"] = pd.to_datetime(holdings["as_of_date"])
    return holdings, pd.DataFrame(report, columns=list(REPORT_COLUMNS))
