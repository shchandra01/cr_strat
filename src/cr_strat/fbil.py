"""Loaders for FBIL benchmark curves: G-sec par and zero curves, MIBOR-OIS and the T-bill curve.

Financial Benchmarks India (FBIL) administers the rupee rate benchmarks used
here as risk-free curves:

* the daily G-sec valuation workbook, whose par-yield sheet is the G-sec par
  curve (0.25y steps out to 38, 40 or 50 years, flat past the longest bond);
* the STRIPS workbook, whose ``ZCYC`` sheet is the G-sec zero curve in the
  same layout (zero rates, so not a like-for-like benchmark for bond YTMs);
* MIBOR-OIS (1M to 5Y) and the T-bill curve (7 days to 12 months), delivered
  as JSON rows of ``processRunDate``, ``tenorName`` and ``rate``.

Terms of use. FBIL's website data are published with a 7-day lag and are for
delayed, non-commercial viewing only. FBIL's terms forbid compiling, downloading
or storing them into an internal database, historical archive or systematic
feed, and count back-testing as commercial use, without a paid subscription
(there is a cheaper tier for recognised research and educational institutions).
Redistribution needs FBIL's written consent. These loaders are meant for files
delivered under an FBIL subscription; nothing here downloads from FBIL.

Workbook quirks handled by ``load_fbil_gsec_curve``:

* ``.xls`` files may be OOXML or BIFF whatever the extension, so the engine is
  picked from the first bytes (``PK`` is openpyxl, the OLE signature is xlrd).
  An error page saved under a workbook name is rejected.
* The par sheet is ``Par Yield`` (2023 on) or ``Par-Yield``; the zero curve is
  ``ZCYC`` (``Zero Coupon% (Semi-annual)``).
* The header is ``Tenor (Year) | YTM% p.a.(Semi-Annual) | YTM % p.a.(Annualized)``
  on row 5 from 2019, and ``Maturity | YTM (Semi-Annual) | YTM (Annualized)`` on
  row 6 in 2018.
* 2018 par yields are decimals (0.0600), later ones percent. A column whose
  values are all below 1 is taken as decimals; INR yields never sit below 1%.
* The 2018 par sheet has a State Government curve below the G-sec block.
  Reading stops at the first non-numeric or non-increasing tenor.
* The valuation date sits in a title cell (``06-Oct-2026``, ``15 Jul, 2019``,
  ``30 Jun, 2022 (Month-End)``, ``FBIL Base/Par Yield as on March 28, 2018
  ((Month/Quarter/Year-End)``) and wins over the file name: the file FBIL lists
  under 2018-03-31 is valued as of 28 March. A date in the file name is only
  the fallback.

Money-market conventions handled by ``load_fbil_money_market``: OIS up to 1Y and
all T-bill curve rates are simple Act/365 money-market rates, converted with
``(1 + r*d/365)^(365/d) - 1`` for a tenor of ``d`` days (months count as
365/12 days each); OIS 2Y to 5Y are published semi-annual. Output is on annual
compounding (``basis = 'annual'``), with the quoted rate kept alongside.
"""

from __future__ import annotations

import io
import json
import re
from collections.abc import Iterable
from pathlib import Path

import numpy as np
import pandas as pd

from cr_strat.curves import convert_yield, money_market_to_annual
from cr_strat.textparse import cell_str, parse_date_text, to_number

GSEC_COLUMNS = ("date", "tenor_years", "yield_pct", "basis", "yield_annualized_pct", "source_file")
MM_COLUMNS = (
    "date", "tenor_name", "tenor_years", "yield_pct", "basis", "rate_quoted_pct", "quote_convention",
    "source_file",
)
GSEC_KINDS = {"par": "paryield", "zcyc": "zcyc"}
MM_KINDS = ("miborois", "tbill")
_OLE_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
_HEADER_SCAN_ROWS = 15
_UNIT_YEARS = {"d": 1 / 365, "m": 1 / 12, "y": 1.0}


def excel_engine(head: bytes) -> str:
    """pandas engine for a workbook from its first bytes, whatever its extension says."""
    if head.startswith(b"PK\x03\x04"):
        return "openpyxl"
    if head.startswith(_OLE_MAGIC):
        return "xlrd"
    raise ValueError(f"not an Excel workbook (starts with {head[:16]!r}); an error page saved as .xls?")


def _letters(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def _pick_sheet(names: list, kind: str, path: Path) -> str:
    for name in names:
        if _letters(str(name)) == GSEC_KINDS[kind]:
            return name
    hint = ""
    if kind == "zcyc":
        hint = " (the G-sec zero curve is in FBIL's STRIPS workbook)"
    elif any(_letters(str(n)) == GSEC_KINDS["zcyc"] for n in names):
        hint = " (a STRIPS workbook? use kind='zcyc')"
    raise ValueError(f"{path.name}: no {kind!r} sheet among {list(names)}{hint}")


def _find_header(rows: list[list]) -> tuple[int, dict[str, int]] | None:
    """Row index and column positions of the tenor / semi-annual / annualised header."""
    for i, row in enumerate(rows[:_HEADER_SCAN_ROWS]):
        cols: dict[str, int] = {}
        for j, cell in enumerate(row):
            text = _letters(cell_str(cell))
            if re.match(r"tenor|maturity", text):
                cols.setdefault("tenor", j)
            elif "semi" in text:
                cols.setdefault("semi", j)
            elif "annual" in text:
                cols.setdefault("annual", j)
        if "tenor" in cols and "semi" in cols:
            return i, cols
    return None


def _title_date(rows: list[list]) -> pd.Timestamp | None:
    for row in rows:
        for cell in row:
            found = parse_date_text(cell_str(cell))
            if found is not None:
                return found
    return None


def _file_name_date(path: Path) -> pd.Timestamp | None:
    m = re.search(r"(?<!\d)((?:19|20)\d{2})[-_]?(\d{2})[-_]?(\d{2})(?!\d)", path.stem)
    if not m:
        return None
    found = pd.to_datetime("-".join(m.groups()), format="%Y-%m-%d", errors="coerce")
    return None if pd.isna(found) else pd.Timestamp(found)


def _cell(row: list, j: int | None) -> float:
    return to_number(row[j]) if j is not None and j < len(row) else float("nan")


def _read_block(rows: list[list], cols: dict[str, int]) -> pd.DataFrame:
    """Rows of the first curve block: stops at the first non-numeric or non-increasing tenor."""
    out, last = [], -np.inf
    for row in rows:
        tenor = _cell(row, cols["tenor"])
        if not np.isfinite(tenor) or tenor <= last:
            break
        last = tenor
        out.append((tenor, _cell(row, cols["semi"]), _cell(row, cols.get("annual"))))
    return pd.DataFrame(out, columns=["tenor_years", "yield_pct", "yield_annualized_pct"], dtype=float)


def _to_percent(values: pd.Series) -> pd.Series:
    return values * 100 if values.notna().any() and values.abs().max() < 1 else values


def load_fbil_gsec_curve(path: str | Path, kind: str = "par") -> pd.DataFrame:
    """One FBIL G-sec workbook (par curve, or ``kind='zcyc'`` for the STRIPS zero curve) in long format.

    Columns ``GSEC_COLUMNS``: ``yield_pct`` is FBIL's semi-annual yield in percent
    (``basis = 'semiannual'``); ``yield_annualized_pct`` is FBIL's own annualised
    column, NaN if the sheet has none.
    """
    if kind not in GSEC_KINDS:
        raise ValueError(f"kind must be one of {tuple(GSEC_KINDS)}")
    path = Path(path)
    data = path.read_bytes()
    try:
        engine = excel_engine(data[:16])
    except ValueError as exc:
        raise ValueError(f"{path.name}: {exc}") from None
    with pd.ExcelFile(io.BytesIO(data), engine=engine) as book:
        sheet = _pick_sheet(book.sheet_names, kind, path)
        rows = book.parse(sheet, header=None, dtype=object).values.tolist()
    found = _find_header(rows)
    if found is None:
        raise ValueError(f"{path.name} [{sheet}]: no tenor header with a semi-annual yield column")
    header, cols = found
    date = _title_date(rows[:header])
    if date is None:
        date = _file_name_date(path)
    if date is None:
        raise ValueError(f"{path.name} [{sheet}]: no valuation date in the title cells or the file name")
    block = _read_block(rows[header + 1:], cols)
    block["yield_pct"] = _to_percent(block["yield_pct"])
    block["yield_annualized_pct"] = _to_percent(block["yield_annualized_pct"])
    block = block.dropna(subset=["yield_pct"])
    block.insert(0, "date", date)
    block["basis"] = "semiannual"
    block["source_file"] = path.name
    return block.loc[:, list(GSEC_COLUMNS)].reset_index(drop=True)


def tenor_years(name: str) -> float:
    """Years in an FBIL tenor name: '7 Days', '1 Month', '12 Months', '1M', '1Y', '5Y'; NaN otherwise."""
    m = re.fullmatch(r"(\d+)\s*(d|days?|m|months?|y|yrs?|years?)", cell_str(name).lower())
    return int(m.group(1)) * _UNIT_YEARS[m.group(2)[0]] if m else float("nan")


def _read_records(path_or_json: str | Path | list) -> tuple[list, str]:
    if isinstance(path_or_json, list):
        return path_or_json, ""
    if isinstance(path_or_json, str) and path_or_json.lstrip().startswith(("[", "{")):
        records, source = json.loads(path_or_json), ""
    else:
        path = Path(path_or_json)
        records, source = json.loads(path.read_text(encoding="utf-8-sig")), path.name
    if not isinstance(records, list):
        raise ValueError(f"{source or 'JSON'}: expected a list of FBIL rate rows")
    return records, source


def load_fbil_money_market(path_or_json: str | Path | list, kind: str) -> pd.DataFrame:
    """FBIL MIBOR-OIS (``kind='miborois'``) or T-bill curve (``'tbill'``) JSON rows, on annual compounding.

    ``path_or_json`` is a JSON file, JSON text or the parsed list of rows. A date and
    tenor listed twice keeps the last row. Columns ``MM_COLUMNS``.
    """
    if kind not in MM_KINDS:
        raise ValueError(f"kind must be one of {MM_KINDS}")
    records, source = _read_records(path_or_json)
    raw = pd.DataFrame.from_records(records)
    if raw.empty:
        return pd.DataFrame(columns=list(MM_COLUMNS))
    name_col = "tenorName" if "tenorName" in raw else "tenor"
    missing = [c for c in ("processRunDate", name_col, "rate") if c not in raw]
    if missing:
        raise ValueError(f"{source or 'JSON'}: rows lack {missing}")
    df = pd.DataFrame({
        "date": pd.to_datetime(raw["processRunDate"].map(cell_str), format="ISO8601", errors="coerce").dt.normalize(),
        "tenor_name": raw[name_col].map(cell_str),
        "rate_quoted_pct": raw["rate"].map(to_number),
    })
    df["tenor_years"] = df["tenor_name"].map(tenor_years)
    unknown = sorted(df.loc[df["tenor_years"].isna(), "tenor_name"].unique())
    if unknown:
        raise ValueError(f"{source or 'JSON'}: unrecognised FBIL tenor names {unknown}")
    df = (df.dropna(subset=["date", "rate_quoted_pct"])
          .drop_duplicates(["date", "tenor_years"], keep="last")
          .sort_values(["date", "tenor_years"]))
    money_market = df["tenor_years"] <= 1 + 1e-9
    if kind == "tbill" and not money_market.all():
        raise ValueError(f"{source or 'JSON'}: T-bill curve tenors beyond 1 year; is this the OIS curve?")
    rate, years = df["rate_quoted_pct"].to_numpy(), df["tenor_years"].to_numpy()
    df["yield_pct"] = np.where(
        money_market, money_market_to_annual(rate, years * 365), convert_yield(rate, "semiannual", "annual")
    )
    df["quote_convention"] = np.where(money_market, "money_market", "semiannual")
    df["basis"] = "annual"
    df["source_file"] = source
    return df.loc[:, list(MM_COLUMNS)].reset_index(drop=True)


def combine_curves(frames: Iterable[pd.DataFrame]) -> pd.DataFrame:
    """Stack curves loaded from several files; a (date, tenor) seen twice keeps the later file's row."""
    frames = list(frames)
    if not frames:
        return pd.DataFrame(columns=["date", "tenor_years", "yield_pct"])
    df = pd.concat([f for f in frames if len(f)] or frames[:1], ignore_index=True)
    return (df.drop_duplicates(["date", "tenor_years"], keep="last")
            .sort_values(["date", "tenor_years"]).reset_index(drop=True))
