"""Synthetic answers of the macro and global sources, built in code: each source's structure, made-up values.

Field names, headers and page layouts follow the answers sampled on 18 Nov
2026 (``src/cr_strat/mospi.py`` and ``global_sources.py`` describe them); no
publisher's file or text is copied, and every number is invented.
"""

from __future__ import annotations

import csv
import io
import json
import math

# --- MoSPI eSankhyiki -------------------------------------------------------------------------------


def mospi_answer(rows: list[dict], *, page: int = 1, per: int = 100, total: int | None = None,
                 pages: int | None = None, msg: str = "Data fetched successfully") -> bytes:
    """An eSankhyiki data answer: ``data`` and ``meta_data`` that add up unless told otherwise."""
    total = len(rows) if total is None else total
    pages = math.ceil(total / per) if pages is None else pages
    doc = {"data": rows, "meta_data": {"page": page, "totalRecords": total, "totalPages": pages,
                                       "recordPerPage": per}, "msg": msg, "statusCode": True}
    return json.dumps(doc, ensure_ascii=False).encode("utf-8")


NO_DATA = b'{"data":[],"msg":"No Data Found","statusCode":true}'
LIMIT_ERROR = b'{"error":"Limit parameter too large. Maximum allowed is 100."}'
LIMIT_ERROR_OLD = b'{"success":false,"message":"Limit cannot be greater than 200"}'


def cpi24(year: int, month: str, sector: str, *, division: str = "CPI (General)", group=None, klass=None,
          sub_class=None, item=None, code=None, index="100.00", inflation=None, series="Current",
          state="All India", imputation=None) -> dict:
    return {"base_year": "2024", "series": series, "year": str(year), "month": month, "state": state,
            "sector": sector, "division": division, "group": group, "class": klass, "sub_class": sub_class,
            "item": item, "code": code, "index": index, "inflation": inflation, "imputation": imputation}


def cpi24_month(year: int, month: str, sector: str = "Combined", *, base: float = 100.0) -> list[dict]:
    """A headline, two divisions, a group and a class for one sector: a page's worth of nodes, made up."""
    return [
        cpi24(year, month, sector, index=f"{base:.2f}", inflation="4.20"),
        cpi24(year, month, sector, division="Food and beverages", code="01", index=f"{base + 2:.2f}",
              inflation="5.10"),
        cpi24(year, month, sector, division="Housing, water, electricity, gas and other fuels", code="04",
              index=f"{base + 1:.2f}", inflation="2.30"),
        cpi24(year, month, sector, division="Food and beverages", group="Food", code="01.1",
              index=f"{base + 3:.2f}", inflation="6.40"),
        cpi24(year, month, sector, division="Food and beverages", group="Food", klass="Cereals and products",
              code="01.1.1", index=f"{base + 4:.2f}", inflation="3.30"),
    ]


def cpi_group(year: int, month: str, sector: str, group: str, subgroup: str, *, index="150.0", inflation="3.25",
              base="2012", status="F", state="All India") -> dict:
    return {"baseyear": base, "year": year, "month": month, "state": state, "sector": sector, "group": group,
            "subgroup": subgroup, "index": index, "inflation": inflation, "status": status}


def cpi_item(year: int, month: str, item: str, *, index="140.0", inflation="1.10", base="2012") -> dict:
    return {"baseyear": base, "year": year, "month": month, "item": item, "index": index, "inflation": inflation,
            "status": "F"}


def iip(year: int, month: str, *, kind="General", category="General", sub="", index="120.0", growth="5.0",
        base="2022-23") -> dict:
    return {"base_year": base, "year": year, "month": month, "type": kind, "category": category,
            "sub_category": sub, "index": index, "growth_rate": growth}


def nas(year: str, quarter: str | None, *, current="1000000", constant="900000", indicator="Gross Domestic Product",
        frequency="Quarterly", revision=None, base="2022-23", series="Current", unit="₹ Crore",
        industry: str | None = None) -> dict:
    row = {"base_year": base, "series": series, "year": year, "indicator": indicator, "frequency": frequency,
           "revision": revision, "quarter": quarter, "current_price": current, "constant_price": constant,
           "unit": unit}
    if industry is not None:
        row.update(industry=industry, subindustry=None, institutional_sector=None)
    return row


def wpi(year: int, month: str, *, major="Wholesale Price Index", group=None, sub=None, subsub=None, item=None,
        index="110.0", base="2022-23") -> dict:
    if base == "2022-23":
        return {"base_year": base, "year": year, "month": month, "major_group": major, "group": group,
                "sub_group": sub, "sub_sub_group": subsub, "item": item, "index_value": index}
    return {"base_year": base, "year": year, "month": month, "majorgroup": major, "group": group, "subgroup": sub,
            "sub_subgroup": subsub, "item": item, "index_value": index}


RBI_FX = ("Exchange Rate of the Indian Rupee vis-à-vis the SDR, US Dollar, Pound Sterling, Euro and Japanese Yen "
          "(Monthly Average and end-Month Rates)")


def rbi_fx(year: str, month: str, currency: str, rate: str, value: str) -> dict:
    return {"indicator": RBI_FX, "year": year, "month": month, "currency": currency, "reference_rate": rate,
            "value": value, "unit": "₹ per unit of foreign currency"}


CALENDAR_POINTER = json.dumps({"status": "success", "code": 200, "message": "fetched", "data": {
    "id": "9", "filename": "1700000000000-ARC 2027-28 test.pdf",
    "url": "uploads/documents/releaseCalender/1700000000000-ARC 2027-28 test.pdf", "filemime": "application/pdf",
    "size": "1234", "created_at": "2027-04-01T00:00:00.000Z"}}).encode("utf-8")


# --- US Treasury ------------------------------------------------------------------------------------

UST_HEADER_2026 = ["1 Mo", "1.5 Month", "2 Mo", "3 Mo", "4 Mo", "6 Mo", "1 Yr", "2 Yr", "3 Yr", "5 Yr", "7 Yr",
                   "10 Yr", "20 Yr", "30 Yr"]
UST_HEADER_2004 = ["1 Mo", "3 Mo", "6 Mo", "1 Yr", "2 Yr", "3 Yr", "5 Yr", "7 Yr", "10 Yr", "20 Yr"]


def ust_csv(rows: list[tuple[str, list[str]]], header: list[str] | None = None) -> bytes:
    """Treasury's yearly CSV: Date and quoted tenor names; newest row first as given; no newline at the end."""
    header = UST_HEADER_2026 if header is None else header
    lines = ["Date," + ",".join(f'"{h}"' for h in header)]
    lines += [f"{day}," + ",".join(values) for day, values in rows]
    return "\n".join(lines).encode("utf-8")


# --- New York Fed -----------------------------------------------------------------------------------


def nyfed_json(rows: list[dict]) -> bytes:
    """``{"refRates": [...]}`` with numbers written as their text (``3.90``), as the API writes them."""
    def value(v):
        if isinstance(v, str) and v and (v[0].isdigit() or v[0] == "-") and v.replace(".", "", 1).lstrip("-").isdigit():
            return v
        return json.dumps(v)
    items = ["{ " + " ,".join(f'"{k}": {value(v)}' for k, v in row.items()) + " }" for row in rows]
    return ('{ "refRates": [ ' + ", ".join(items) + " ] }").encode("utf-8")


def sofr_row(day: str, rate="3.90", *, kind="SOFR", p=("3.85", "3.88", "3.95", "3.99"), volume="2500",
             revision="", footnote=None) -> dict:
    row = {"effectiveDate": day, "type": kind, "percentRate": rate, "percentPercentile1": p[0],
           "percentPercentile25": p[1], "percentPercentile75": p[2], "percentPercentile99": p[3],
           "volumeInBillions": volume, "revisionIndicator": revision}
    if footnote is not None:
        row["footnoteId"] = footnote
    return row


def effr_row(day: str, rate="3.88", *, low="3.75", high="4.00", revision="") -> dict:
    return {"effectiveDate": day, "type": "EFFR", "percentRate": rate, "percentPercentile1": "3.85",
            "percentPercentile25": "3.87", "percentPercentile75": "3.89", "percentPercentile99": "3.90",
            "targetRateFrom": low, "targetRateTo": high, "volumeInBillions": "90", "revisionIndicator": revision}


def effr_old_row(day: str, rate="5.20", *, target="5.25", high=None) -> dict:
    row = {"effectiveDate": day, "type": "EFFR", "percentRate": rate, "intraDayLow": "4.00", "intraDayHigh": "5.50",
           "stdDeviation": "0.10", "targetRateFrom": target, "revisionIndicator": ""}
    if high is not None:
        row["targetRateTo"] = high
    return row


def sofrai_row(day: str) -> dict:
    return {"effectiveDate": day, "type": "SOFRAI", "average30day": "3.91000", "average90day": "3.80000",
            "average180day": "3.70000", "index": "1.20000000", "revisionIndicator": ""}


NYFED_CSV_HEADER = ("Effective Date,Rate Type,Rate (%),1st Percentile (%),25th Percentile (%),75th Percentile (%),"
                    "99th Percentile (%),Volume ($Billions),Target Rate From (%),Target Rate To (%),"
                    "Intra Day - Low (%),Intra Day - High (%),Standard Deviation (%),30-Day Average SOFR,"
                    "90-Day Average SOFR,180-Day Average SOFR,SOFR Index,Revision Indicator (Y/N),Footnote ID")


def nyfed_csv(rows: list[str]) -> bytes:
    return ("\n".join([NYFED_CSV_HEADER, *rows]) + "\n").encode("utf-8")


# --- EIA ----------------------------------------------------------------------------------------------


def eia_page(weeks: list[tuple[str, list[str]]], *, title="Europe Brent Spot Price FOB  (Dollars per Barrel)",
             release="11/18/2027", next_release="11/24/2027",
             header=("Week Of", "Mon", "Tue", "Wed", "Thu", "Fri")) -> bytes:
    """EIA's history page: a week label and five cells per row, a spacer after every second week, the legend."""
    rows = []
    for n, (label, cells) in enumerate(weeks, start=1):
        rows.append("<tr> <td class='B6'>&nbsp;&nbsp;" + label + "</td> "
                    + " ".join(f"<td class='B3'>{c}</td>" for c in cells) + " </tr>")
        if n % 2 == 0:
            rows.append("<tr> <td height='3'></td> <td></td> <td></td> <td></td> <td></td> <td></td> </tr>")
    head = "<tr bgcolor='#993333'> " + " ".join(f"<th class='G'>{h}&nbsp;</th>" for h in header) + " </tr>"
    page = (f"<html><head><title>{title}</title></head><body><table width='675'><tr><td>menu</td></tr></table>"
            f"<table SUMMARY='{title}' width='600'> {head} {' '.join(rows)} </table>"
            "<table><tr><td class='F2'><span class='FNlabel'>-</span> = a legend line (made up)</td></tr></table>"
            f"<table><tr><td class='F2'>Release Date: {release}</td></tr>"
            f"<tr><td class='F2'>Next Release Date: {next_release}</td></tr></table></body></html>")
    return page.encode("utf-8")


# --- Federal Reserve H.10 -----------------------------------------------------------------------------


def h10_page(rows: list[tuple[str, str]], *, release="Monday, November 15, 2027",
             unit="(Rates in Indian rupees per U.S. dollar)") -> bytes:
    """The 2000-present history page: a pubtables table whose thead holds bare th cells, a th date per row."""
    body = "".join(f'<tr>\n<th id="r1" headers="a1">{d} </th>\n<td headers="a2 a1 r1">      {v}      </td>\n</tr>\n'
                   for d, v in rows)
    page = ("<!DOCTYPE html><html><head><title>Federal Reserve Board - H.10 - test</title></head><body>"
            f'<div class="dates">Release Date: {release}</div>'
            '<h5 class="tablehead">Historical Rates for the Indian Rupee</h5>'
            f'<span class="tableunit">{unit}</span>'
            '<table class="pubtables" title="Historical Rates for the Indian Rupee">\n<thead>\n'
            f"<th scope=col>Date</th>\n<th scope=col>Rate</th>\n</thead>\n{body}</table></body></html>")
    return page.encode("utf-8")


def h10_page_1990s(rows: list[tuple[str, str]]) -> bytes:
    """The 1990s page: a header row, dates split by ABBR, cells padded with non-breaking spaces."""
    body = ""
    for d, v in rows:
        day, mon, yy = d.split("-")
        value = '<ABBR title="No Data">&nbsp;ND&nbsp;</ABBR>' if v == "ND" else f"{v}&nbsp;"
        body += (f'<tr><th axis="Date">&nbsp;{day}-<ABBR title="month">{mon}-</ABBR>{yy}&nbsp;</th>'
                 f'<td>&nbsp;&nbsp;&nbsp;{value}</td></tr>\n')
    page = ("<HTML><BODY><FONT><I>Release Date: December 31, 1999</I></FONT><PRE>&nbsp;<BR>"
            "INDIA -- SPOT EXCHANGE RATE, RUPEES/US$<BR></pre>"
            '<table border="1" summary="INDIA -- SPOT EXCHANGE RATE, RUPEES/US$"><tr><th>Date</th><th>Rate</th></tr>'
            f"{body}</table></PRE></BODY></HTML>")
    return page.encode("utf-8")


def ddp_csv(rows: list[tuple[str, str, str]], *, unit="Currency", multiplier="1", currency="INR") -> bytes:
    """The Data Download Program package: six header lines, then one row per day (another currency beside)."""
    out = io.StringIO()
    w = csv.writer(out, quoting=csv.QUOTE_ALL, lineterminator="\n")
    w.writerow(["Series Description", "Euro-Area Euro", "Indian Rupee"])
    w.writerow(["Unit:", "Currency", unit])
    w.writerow(["Multiplier:", "1", multiplier])
    w.writerow(["Currency:", "EUR", currency])
    w.writerow(["Unique Identifier: ", "H10/H10/RXI$US_N.B.EU", "H10/H10/RXI_N.B.IN"])
    w.writerow(["Time Period", "RXI$US_N.B.EU", "RXI_N.B.IN"])
    for row in rows:
        w.writerow(row)
    return out.getvalue().encode("utf-8")


# --- BIS ------------------------------------------------------------------------------------------------

CBPOL_V1_HEADER = ("FREQ,REF_AREA,UNIT_MEASURE,UNIT_MULT,TIME_FORMAT,COMPILATION,DECIMALS,SOURCE_REF,SUPP_INFO_BREAKS,"
                   "TITLE,TIME_PERIOD,OBS_VALUE,OBS_STATUS,OBS_CONF,OBS_PRE_BREAK")
XRU_V1_HEADER = ("FREQ,REF_AREA,CURRENCY,COLLECTION,UNIT_MULT,DECIMALS,AVAILABILITY,TITLE,TIME_PERIOD,OBS_VALUE,"
                 "OBS_STATUS,OBS_PRE_BREAK,OBS_CONF")
CBPOL_V2_HEADER = ("STRUCTURE,STRUCTURE_ID,ACTION,FREQ,REF_AREA,TIME_PERIOD,OBS_VALUE,UNIT_MEASURE,UNIT_MULT,"
                   "TIME_FORMAT,COMPILATION,DECIMALS,SOURCE_REF,SUPP_INFO_BREAKS,TITLE,OBS_STATUS,OBS_CONF,OBS_PRE_BREAK")


def cbpol_v1(rows: list[tuple[str, str, str, str]], *, unit="368", freq="D") -> bytes:
    """``(area, date, value, status)`` rows in BIS's v1 CSV, every attribute repeated."""
    lines = [CBPOL_V1_HEADER]
    for area, day, value, status in rows:
        lines.append(f"{freq},{area},{unit},0,,a compilation note,4,a central bank,,policy rate - {area},{day},"
                     f"{value},{status},F,")
    return ("\n".join(lines) + "\n").encode("utf-8")


def cbpol_v2(rows: list[tuple[str, str, str, str]], *, flow="BIS:WS_CBPOL(1.0)", action="I") -> bytes:
    lines = [CBPOL_V2_HEADER]
    for area, day, value, status in rows:
        lines.append(f"dataflow,{flow},{action},D,{area},{day},{value},368,0,,a note,4,a bank,,policy rate,{status},F,")
    return ("\n".join(lines) + "\n").encode("utf-8")


def xru_v1(rows: list[tuple[str, str, str]], *, currency="INR", collection="A") -> bytes:
    lines = [XRU_V1_HEADER]
    for day, value, status in rows:
        lines.append(f"D,IN,{currency},{collection},0,6,A, rupee per dollar,{day},{value},{status},,F")
    return ("\n".join(lines) + "\n").encode("utf-8")


BIS_REL_CAL = (b"FREQ,CATEGORY,RELEASE_TYPE,TIME_PERIOD,OBS_VALUE,OBS_STATUS,OBS_PRE_BREAK,OBS_CONF,COMMENTARY\n"
               b"M,CBPOL,DM2,2027-10,20271111,A,,F,\nM,XRU,DM1,2027-10,20271104,A,,F,\n")


# --- Refusals -----------------------------------------------------------------------------------------

AKAMAI_PAGE = (b"<HTML><HEAD>\n<TITLE>Access Denied</TITLE>\n</HEAD><BODY>\n<H1>Access Denied</H1>\n You don't have "
               b"permission to access this resource on this server.<P>\nReference&#32;&#35;18&#46;00000000&#46;1\n"
               b"</BODY>\n</HTML>")
AKAMAI_UNTITLED = (b"<HTML><BODY>You don't have permission to access this page. Reference #18.abcdef.12345</BODY>"
                   b"</HTML>")
CHALLENGE_PAGE = (b"<html><head><title>Just a moment...</title><script>var x=1;</script></head>"
                  b"<body><noscript>Please enable JavaScript to continue</noscript></body></html>")
CONTACT_PAGE = (b"<html><head><title>Notice</title></head><body>Automated tools must declare your traffic by putting "
                b"contact information in the user agent.</body></html>")
CONTACT_TEXT = b"Your request looks automated. Please include your email address in the User-Agent header."
MAINTENANCE_PAGE = b"<html><head><title>Maintenance</title></head><body><p>Back soon</p></body></html>"
