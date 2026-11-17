"""Synthetic FIMMDA auction workbooks and an RBI Handbook Table 221 page for the tests.

The builders reproduce the layouts seen in Nov 2026 (FIMMDA's yearly G-sec
sheets and monthly SDL sheets, the Handbook table's header rows) filled with a
few typed-in facts (security names, dates, amounts, prices) or made-up values.
No FIMMDA or RBI file or text is copied here. Yields of made-up auctions are
computed with the pricing engine, so they reproduce by construction.
"""

from __future__ import annotations

import datetime as dt

import openpyxl

from cr_strat.bonds import FixedRateBond

D = dt.datetime
GSEC_HEADER = ("Auction Date", "Security", "Notified Amount (Rs.)", "Amount Accepted (Rs.)", "Cut Off Price (Rs.)",
               "Cut Off Yield (%)")
SDL_HEADER = ("Auciton Date", "ISIN", "Description", "Maturity Date", "Coupon", "WAY", "Indicator", "Cutoff",
              "State Code", "State", "Accpected Amount (Rupees Crore)", "SumProduct")


def priced_yield(coupon: float, maturity: str, price: float, settle: str, issue: str | None = None) -> str:
    """The 4-decimal yield the engine gives a clean price: a made-up auction that reproduces by construction."""
    bond = FixedRateBond(coupon, maturity, issue_date=issue)
    return f"{bond.yield_from_price(price, settle):.4f}"


def gsec_workbook(path, sheets: dict[str, list[tuple]], total: bool = True):
    """A FIMMDA yearly G-sec workbook: per sheet the header, rows (date, name, notified, accepted, price, yield).

    Numeric prices and yields get FIMMDA's number formats from FY2021-22
    (``0.00`` and ``0.0000``); text cells are kept as written.
    """
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        ws.append(list(GSEC_HEADER))
        for r in rows:
            ws.append(list(r))
            row = ws.max_row
            ws.cell(row, 1).number_format = "dd-mmm-yyyy"
            for col in (3, 4):
                ws.cell(row, col).number_format = '_ * #,##0.00_ ;_ * \\-#,##0.00_ ;_ * "-"??_ ;_ @_ '
            if isinstance(r[4], (int, float)):
                ws.cell(row, 5).number_format = "0.00"
            if isinstance(r[5], (int, float)):
                ws.cell(row, 6).number_format = "0.0000"
        if total:
            nums = [r for r in rows if isinstance(r[2], (int, float))]
            ws.append([None, "Total Rs. In crores", sum(r[2] for r in nums), None, None, None])
    wb.save(path)
    return path


def sdl_workbook(path, sheets: dict[str, list[tuple]], headerless: tuple[str, ...] = (),
                 extra: dict[str, list[list]] | None = None):
    """A FIMMDA SDL workbook: a State List, the data sheets (A-L as FIMMDA lays them out) and other sheets."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    states = wb.create_sheet("State List")
    for code, name in (("AP", "Andhra Pradesh"), ("KA", "Karnataka"), ("RJ", "Rajasthan"), ("UP", "Uttar Pradesh")):
        states.append([code, name])
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        if name not in headerless:
            ws.append(list(SDL_HEADER))
        for r in rows:
            ws.append(list(r))
    for name, rows in (extra or {}).items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    wb.save(path)
    return path


HBS_HEAD = (
    '<tr><td colspan="15">(Amount in ₹ Crore; Rate in per cent)</td></tr>'
    '<tr><td rowspan="3">Date of Auction</td><td rowspan="3">Date of Issue</td><td rowspan="3">Notified Amount</td>'
    '<td rowspan="3">Maturity (Period/ Residual Period) (Years)</td><td colspan="4">Bids Received</td>'
    '<td colspan="4">Bids Accepted</td><td rowspan="3">Devolvement on Primary Dealers</td>'
    '<td rowspan="3">Indicative YTM at Cut-off Price/ Reissue Price/ Coupon Rate</td>'
    '<td rowspan="3">Nomenclature of Securities</td></tr>'
    '<tr><td colspan="2">Competitive</td><td colspan="2">Non-competitive</td><td colspan="2">Competitive</td>'
    '<td colspan="2">Non-competitive</td></tr>'
    "<tr>" + "<td>Number</td><td>Face Value</td>" * 4 + "</tr>"
    "<tr>" + "".join(f"<td>{i}</td>" for i in range(1, 16)) + "</tr>"
)


def handbook_page(rows: list[tuple[str, ...]], fy: str = "2025-26") -> str:
    """A saved Handbook of Statistics Table 221 page: frame, units row, header rows, an FY row, the data rows."""
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    table = (f'<table class="tablebg" width="95%">{HBS_HEAD}'
             f"<tr><td>{fy}</td>" + "<td></td>" * 14 + f"</tr>{body}"
             '<tr><td colspan="15">(Continued)</td></tr></table>')
    return ("<html><head><title>Publications | Test</title></head><body>"
            '<table width="100%"><tr><td><table class="tablebg" width="100%">'
            "<tr><td>(20 kb)</td></tr><tr><td>Date : Jul 31, 2026</td></tr>"
            "<tr><td>Table 221 : Details of Central Government Market Borrowings</td></tr>"
            f"<tr><td>{table}</td></tr></table></td></tr></table></body></html>")
