"""Synthetic RBI pages and RSS feeds for the tests.

The builders reproduce the structure of RBI's pages as seen in Nov 2026
(nesting, rowspans, colspans, labels), filled with a few typed-in facts
(security names, dates, amounts, prices) or made-up values. No RBI file or
text is copied here.
"""

from __future__ import annotations

from html import escape

# --- Page frames ------------------------------------------------------------------------------------


def release_page(title: str, date_line: str, body: str, prid: int | None = 63970, pdf: bool = True) -> str:
    """A BS_PressReleaseDisplay page: frame rows PDF size, 'Date : ...', title, content cell."""
    action = f'./BS_PressReleaseDisplay.aspx?prid={prid}' if prid is not None else "./BS_PressReleaseDisplay.aspx"
    link = "<a href='https://rbidocs.example/PR1.PDF'><img alt='PDF'></a>" if pdf else ""
    return (
        "<html><head><title>Press Releases | Test</title></head><body>"
        f'<form method="post" action="{action}" id="form1">'
        "<div class='menu'><a href='/home'>Home</a><!-- <a href='old.xlsx'>old</a> --></div>"
        '<table width="100%" role="presentation">'
        '<table class="tablebg" width="100%">'
        f'<tr><td class="tableheader">{link} (<span>333 kb</span>)</td></tr>'
        f'<tr><td align="right" class="tableheader"><b> Date : {date_line}</b></td></tr>'
        f'<tr><td align="center" class="tableheader"><b>{escape(title)}</b></td></tr>'
        f'<tr class="tablecontent1"><td>{body}</td></tr>'
        "</table></table></form>"
        "<script>var x = '<table><tr><td>not a table</td></tr></table>';</script>"
        "</body></html>"
    )


def body(narrative: str, *tables: str, pr_no: str = "2026-2027/1492", after: str = "") -> str:
    """Release body: the 'td' table holding the narrative, the data tables and the press-release number."""
    return (f'<table width="100%" class="td"><tr><td><p>{narrative}</p>{"".join(tables)}'
            f"<p>Communications</p><p>Press Release: {pr_no}</p>{after}</td></tr></table>")


def feed(items: list[dict], bom: bool = True) -> bytes:
    """RSS 2.0 bytes; each item has title, prid, pub (pubDate text) and html (the description)."""
    parts = []
    for it in items:
        parts.append(
            f"<item><title><![CDATA[{it['title']}]]></title>"
            f"<description><![CDATA[{it['html']}]]></description>"
            f"<link>https://www.rbi.org.in/scripts/BS_PressReleaseDisplay.aspx?prid={it['prid']}</link>"
            f"<pubDate>{it['pub']}</pubDate></item>")
    xml = ('<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel>'
           "<title>PRESS RELEASES FROM RBI</title><link>http://www.rbi.org.in</link>"
           "<description>Test feed</description><copyright>Test</copyright>"
           f"{''.join(parts)}</channel></rss>")
    return (b"\xef\xbb\xbf" if bom else b"") + xml.encode("utf-8")


def blocked_page(support_id: str = "123456789") -> str:
    return ("<html><head><title>Unauthorised Access </title></head><body><center><p>"
            "<font size=4>You are not authorized to view this page. <b><p>"
            f"<font color=Red><b> Support ID: {support_id}<br></b></font></center></body></html>")


# --- Auction results --------------------------------------------------------------------------------

GOI_6_94 = dict(  # 13 Nov 2026 auction, as in the spec's test vector
    name="6.94% GS 2036", notified="34,000", cr_n="349", cr_amt="73,016.600", price="97.43", ytm="7.3203",
    ca_n="146", ca_amt="33,947.718", pa_pct="18.3591%", pa_bids="(34 bids)", wap="97.46", way="7.3158",
    ncr_n="8", ncr_amt="52.282", nca_n="8", nca_amt="52.282", nca_pct="", uw="34,000", uw_acc="34,000",
    dev="NIL")
GOI_SECOND = dict(  # made up, to test a second security column
    name="7.71% GS 2066", notified="14,000", cr_n="120", cr_amt="30,500.250", price="99.10", ytm="7.7853",
    ca_n="60", ca_amt="13,950.000", pa_pct="50.0000%", pa_bids="(1 bid)", wap="99.15", way="7.7812",
    ncr_n="5", ncr_amt="50.000", nca_n="5", nca_amt="50.000", nca_pct="", uw="14,000", uw_acc="14,000",
    dev="NIL")
TBILL_COLUMNS = [  # 11 Nov 2026 T-bill auction, as in the spec's test vectors
    dict(name="91-Day", notified="8,000", cr_n="132", cr_amt="27,046.800", price="98.5581", ytm="5.8681",
         ca_n="28", ca_amt="7,600.000", pa_pct="48.1700%", pa_bids="(2 bids)", wap="98.5602", way="5.8594",
         ncr_n="9", ncr_amt="11,614.937", nca_n="9", nca_amt="11,600.000", nca_pct="96.4002"),
    dict(name="182-Day", notified="8,000", cr_n="91", cr_amt="20,021.500", price="96.8521", ytm="6.5183",
         ca_n="18", ca_amt="7,600.000", pa_pct="62.7941%", pa_bids="(1 bid)", wap="96.8579", way="6.5059",
         ncr_n="5", ncr_amt="1,019.514", nca_n="5", nca_amt="1,000.000", nca_pct="95.3484"),
    dict(name="364-Day", notified="7,000", cr_n="146", cr_amt="21,548.500", price="93.7900", ytm="6.6394",
         ca_n="48", ca_amt="6,650.000", pa_pct="60.0855%", pa_bids="(3 bids)", wap="93.8091", way="6.6176",
         ncr_n="6", ncr_amt="1,794.240", nca_n="6", nca_amt="1,700.000", nca_pct="78.7862"),
]


def _tds(columns: list[dict], key: str, fmt: str = "{}") -> str:
    return "".join(f'<td align="right">{fmt.format(c[key]) if c[key] != "" else ""}</td>' for c in columns)


def _blank(n: int) -> str:
    return "<td></td>" * n


def results_table(columns: list[dict], tbill: bool = False, unit: str = "(Amount in ₹ Crore)",
                  skip: tuple[str, ...] = ()) -> str:
    """The 'Auction Results' table of a GoI or T-bill result page, rowspans as RBI prints them."""
    n = len(columns)
    wa_label = "Weighted Average Price (₹)/Yield" if tbill else "Weighted Average Price (₹) / Yield"
    rows = [
        f'<tr><td colspan="{n + 2}" align="right">{unit}</td></tr>' if unit else "",
        '<tr align="center" class="head"><td colspan="2">Auction Results</td>'
        + "".join(f"<td>{c['name']}</td>" for c in columns) + "</tr>",
        f"<tr><td>I.</td><td>Notified Amount</td>{_tds(columns, 'notified')}</tr>",
        f'<tr><td rowspan="3">II.</td><td>Competitive Bids Received</td>{_blank(n)}</tr>',
        f"<tr><td>(i) Number</td>{_tds(columns, 'cr_n')}</tr>",
        f"<tr><td>(ii) Amount</td>{_tds(columns, 'cr_amt')}</tr>",
        "" if "cutoff" in skip else
        f'<tr><td rowspan="2">III.</td><td rowspan="2">Cut-off price (₹) / Yield</td>{_tds(columns, "price")}</tr>'
        f"<tr>{_tds(columns, 'ytm', '(YTM: {}%)')}</tr>",
        f'<tr><td rowspan="3">IV.</td><td>Competitive Bids Accepted</td>{_blank(n)}</tr>',
        f"<tr><td>(i) Number</td>{_tds(columns, 'ca_n')}</tr>",
        f"<tr><td>(ii) Amount</td>{_tds(columns, 'ca_amt')}</tr>",
        '<tr><td rowspan="2">V.</td><td rowspan="2">Partial Allotment Percentage of Competitive Bids</td>'
        f"{_tds(columns, 'pa_pct')}</tr><tr>{_tds(columns, 'pa_bids')}</tr>",
        f'<tr><td rowspan="2">VI.</td><td rowspan="2">{wa_label}</td>{_tds(columns, "wap")}</tr>'
        f"<tr>{_tds(columns, 'way', '(WAY: {}%)')}</tr>",
        f'<tr><td rowspan="3">VII.</td><td>Non-Competitive Bids Received</td>{_blank(n)}</tr>',
        f"<tr><td>(i) Number</td>{_tds(columns, 'ncr_n')}</tr>",
        f"<tr><td>(ii) Amount</td>{_tds(columns, 'ncr_amt')}</tr>",
        f'<tr><td rowspan="4">VIII.</td><td>Non-Competitive Bids Accepted</td>{_blank(n)}</tr>',
        f"<tr><td>(i) Number</td>{_tds(columns, 'nca_n')}</tr>",
        f"<tr><td>(ii) Amount</td>{_tds(columns, 'nca_amt')}</tr>",
        f"<tr><td>(iii) Partial Allotment Percentage</td>{_tds(columns, 'nca_pct')}</tr>",
    ]
    if not tbill:
        rows += [
            f"<tr><td>IX.</td><td>Underwriting Notified Amount</td>{_tds(columns, 'uw')}</tr>",
            "<tr><td>X.</td><td>Amount of Underwriting accepted from primary dealers</td>"
            f"{_tds(columns, 'uw_acc')}</tr>",
            f"<tr><td>XI.</td><td>Devolvement on Primary Dealers</td>{_tds(columns, 'dev')}</tr>",
        ]
    return f'<table width="80%" class="tablebg">{"".join(rows)}</table>'


SDL_ASSAM = dict(name="ASSAM SGS 2046", notified="1000", tenor="Re-issue of 7.83% ASSAM SGS 2046 issued on 29-APR-2026",
                 cr_n="69", cr_amt="2295", cy="8.0814", cp="97.55", ca_n="29", ca_amt="989.940", pa_pct="69.7457",
                 pa_n="2", ncr_n="3", ncr_amt="10.060", ncp="97.67", nca_n="3", nca_amt="10.060", ncpa="",
                 way="8.0688", total="1000")
SDL_BIHAR = dict(name="BIHAR SGS 2044*", notified="2000", tenor="18 -Years", cr_n="64", cr_amt="2970", cy="8.10",
                 cp="100", ca_n="30", ca_amt="1385", pa_pct="-", pa_n="-", ncr_n="8", ncr_amt="148.882",
                 ncp="100.30", nca_n="8", nca_amt="148.882", ncpa="", way="8.0682", total="1533.882")
SDL_CHHATTISGARH = dict(
    name="CHHATTISGARH SGS 2036#", notified="900",
    tenor="Re-issue of 7.68% CHHATTISGARH SGS 2036 issued on 29-APR-2026", cr_n="73", cr_amt="3980", cy="-",
    cp="-", ca_n="-", ca_amt="-", pa_pct="-", pa_n="-", ncr_n="4", ncr_amt="11.028", ncp="-", nca_n="-",
    nca_amt="-", ncpa="", way="-", total="0")
SDL_UP = dict(name="UTTAR PRADESH SGS 2038", notified="1500",
              tenor="Re-issue of 8.11% UTTARPRADESH SGS 2038 issued on 14-OCT-2026", cr_n="109", cr_amt="7623.200",
              cy="7.9889", cp="100.91", ca_n="19", ca_amt="1413.705", pa_pct="9.2945", pa_n="1", ncr_n="11",
              ncr_amt="86.295", ncp="101.22", nca_n="11", nca_amt="86.295", ncpa="", way="7.9485", total="1500")
SDL_COLUMNS = [SDL_ASSAM, SDL_BIHAR, SDL_CHHATTISGARH, SDL_UP]
SDL_FOOTNOTE = ("*Bihar has accepted a partial amount in the 18-year security. "
                "#Chhattisgarh has not accepted any amount in the Re-issue of 7.68% Chhattisgarh SGS 2036.")
_SDL_ROWS = [  # (label, key or None for a section row)
    ("Notified Amount", "notified"), ("Tenor", "tenor"), ("Competitive Bids Received", None), ("(i) No.", "cr_n"),
    ("(ii) Amount", "cr_amt"), ("Cut-off Yield (%)", "cy"), ("Cut off Price (₹)", "cp"),
    ("Competitive Bids Accepted", None), ("(i) No.", "ca_n"), ("(ii) Amount", "ca_amt"),
    ("Partial Allotment Percentage of Competitive Bids", None), ("(i) Percentage", "pa_pct"), ("(ii) No.", "pa_n"),
    ("Non - Competitive Bids Received", None), ("(i) No.", "ncr_n"), ("(ii) Amount", "ncr_amt"),
    ("Non-Competitive Price (₹)", "ncp"), ("Non-Competitive Bids Accepted", None), ("(i) No.", "nca_n"),
    ("(ii) Amount", "nca_amt"), ("Partial Allotment Percentage of Non-Competitive Bids", None),
    ("Percentage", "ncpa"), ("Weighted Average Yield (%)", "way"), ("Total Allotment Amount", "total"),
]
_SUMMED = ("notified", "cr_n", "cr_amt", "ca_n", "ca_amt", "ncr_n", "ncr_amt", "nca_n", "nca_amt", "total")


def sdl_total(columns: list[dict]) -> dict:
    """The 'Total' column RBI adds: sums of the amount and count rows, '-' or blank elsewhere."""
    total = {k: "" for k in columns[0]}
    total["name"] = "Total"
    for k in _SUMMED:
        values = [float(c[k]) for c in columns if c[k] not in ("-", "")]
        s = sum(values)
        total[k] = f"{s:.3f}" if k.endswith("amt") or k == "total" else f"{s:.0f}"
    for k in ("cy", "cp", "pa_pct", "pa_n", "ncp", "way", "ncpa"):
        total[k] = "-"
    return total


def sdl_tables(columns: list[dict], per_table: int = 4, total: dict | None = None, footnote: str = SDL_FOOTNOTE,
               unit: str = "(Amount in ₹ Crore)") -> list[str]:
    """SDL result tables, ``per_table`` security columns each; the last adds Total and the footnote."""
    cols = list(columns)
    if total is not False:
        cols.append(total or sdl_total(columns))
    chunks = [cols[i:i + per_table] for i in range(0, len(cols), per_table)]
    if len(chunks) > 1 and len(chunks[-1]) == 1 and total is not False:
        chunks[-2:] = [chunks[-2][:-1], chunks[-2][-1:] + chunks[-1]]
    tables = []
    for i, chunk in enumerate(chunks):
        rows = []
        if i == 0 and unit:
            rows.append(f'<tr><td colspan="{len(chunk) + 1}" align="right">{unit}</td></tr>')
        rows.append("<tr><td></td>" + "".join(f"<td><b>{c['name']}</b></td>" for c in chunk) + "</tr>")
        for label, key in _SDL_ROWS:
            cells = "".join(f"<td>{c[key]}</td>" if key else "<td></td>" for c in chunk)
            rows.append(f"<tr><td>{label}</td>{cells}</tr>")
        if i == len(chunks) - 1 and footnote:
            rows.append(f'<tr><td colspan="3">{footnote}</td></tr>')
        tables.append(f'<table class="tablebg">{"".join(rows)}</table>')
    return tables


def uw_table(rows: list[tuple[str, ...]], note: str = "The auction will be held on November 13, 2026.") -> str:
    header = ("Nomenclature of the Security", "Notified Amount (₹ crore)",
              "Minimum Underwriting Commitment (MUC) Amount (₹ crore)",
              "Additional Competitive Underwriting Amount Accepted (₹ crore)", "Total Amount underwritten (₹ crore)",
              "ACU Commission Cut-off rate (paise per ₹100)")
    out = ["<tr>" + "".join(f"<td><b>{h}</b></td>" for h in header) + "</tr>"]
    out += ["<tr>" + "".join(f"<td>{v}</td>" for v in r) + "</tr>" for r in rows]
    if note:
        out.append(f'<tr><td colspan="6">{note}</td></tr>')
    return f'<table class="tablebg">{"".join(out)}</table>'


SWITCH_PAIRS = [  # 16 Nov 2026 switch, pairs 1, 4 and 6 of the spec's test vectors
    dict(src="7.38% GS 2027", dst="7.18% GS 2037", notified="5,000", rec_n="6", offered="4,408.732", acc_n="4",
         accepted="3,898.732", issued="3,960.932", cutoff="98.51/7.3819"),
    dict(src="7.26% GS 2029", dst="7.73% GS 2034", notified="4,000", rec_n="1", offered="1.000", acc_n="0",
         accepted="0", issued="0", cutoff="NA"),
    dict(src="7.04% GS 2029", dst="7.41% GS 2036", notified="5,000", rec_n="7", offered="3,658.204", acc_n="7",
         accepted="3,658.204", issued="3,657.111", cutoff="100.28/7.3688"),
]


def switch_table(pairs: list[dict]) -> str:
    def tds(key):
        return "".join(f"<td>{p[key]}</td>" for p in pairs)
    rows = [
        f"<tr><td>A.</td><td>Source Security</td>{tds('src')}</tr>",
        f'<tr><td rowspan="2">B.</td><td>Notified Amount (in ₹ crore)</td>{tds("notified")}</tr>',
        f"<tr><td>Destination Security</td>{tds('dst')}</tr>",
        f'<tr><td rowspan="6">C.</td><td>i. No. of offers received</td>{tds("rec_n")}</tr>',
        f"<tr><td>ii. Total amount of Source Security offered (Face value, in ₹ crore)</td>{tds('offered')}</tr>",
        f"<tr><td>iii. No. of offers accepted</td>{tds('acc_n')}</tr>",
        f"<tr><td>iv. Total amount of source security accepted (Face value, in ₹ crore)</td>{tds('accepted')}</tr>",
        f"<tr><td>v. Total amount of destination security issued (Face value, in ₹ crore)</td>{tds('issued')}</tr>",
        f"<tr><td>vi. Cut-off price (₹) / yield (%) for destination security</td>{tds('cutoff')}</tr>",
    ]
    return f'<table class="tablebg">{"".join(rows)}</table>'


# --- Announcements ----------------------------------------------------------------------------------

def gsec_announcement_table(total: str = "33,000") -> str:
    """Two securities sharing the notification, auction and settlement cells through rowspan."""
    head = ("Sr No", "Security", "Date of Repayment", "Notified Amount (₹ Crore)", "GoI specific Notification",
            "Auction Date", "Settlement Date")
    return ('<table class="tablebg"><tr>' + "".join(f"<th>{h}</th>" for h in head) + "</tr>"
            "<tr><td>1</td><td>6.53% GS 2031</td><td>Sep 07, 2031</td><td>19,000</td>"
            '<td rowspan="2">F.No.4(1)-B(W&amp;M)/2026 dated November 16, 2026</td>'
            '<td rowspan="2">November 20, 2026 (Friday)</td><td rowspan="2">November 23, 2026 (Monday)</td></tr>'
            "<tr><td>2</td><td>7.71% GS 2066</td><td>May 18, 2066</td><td>14,000</td></tr>"
            f"<tr><td></td><td>Total</td><td></td><td>{total}</td><td></td><td></td><td></td></tr></table>")


GSEC_ANNOUNCEMENT_TEXT = ("Government of India has announced the sale (re-issue) of two dated securities. "
                          "GoI will have the option to retain additional subscription up to ₹2,000 crore "
                          "against each security. The auction will be conducted using multiple price method. "
                          "Payment by successful bidders will have to be made on November 23, 2026 (Monday).")


def tbill_announcement_table(total: str = "23,000") -> str:
    head = ("Sr. No.", "Treasury Bill", "Notified Amount (₹ crore)", "Auction Date", "Settlement Date")
    return ('<table class="tablebg"><tr>' + "".join(f"<th>{h}</th>" for h in head) + "</tr>"
            '<tr><td>1</td><td>91-Day</td><td>8,000</td><td rowspan="3">November 18, 2026 (Wednesday)</td>'
            '<td rowspan="3">November 19, 2026 (Thursday)</td></tr>'
            "<tr><td>2</td><td>182-Day</td><td>8,000</td></tr><tr><td>3</td><td>364-Day</td><td>7,000</td></tr>"
            f"<tr><td></td><td>Total</td><td>{total}</td><td></td><td></td></tr></table>")


def sdl_announcement_table(total: str = "6200") -> str:
    head = ("Sr. No.", "State", "Amount to be raised (₹ Crore)", "Tenor (Year)", "Type of Auction")
    return ('<table class="tablebg"><tr>' + "".join(f"<th>{h}</th>" for h in head) + "</tr>"
            "<tr><td>1.</td><td>Karnataka</td><td>2000</td><td>08</td><td>Yield</td></tr>"
            '<tr><td rowspan="3">2.</td><td rowspan="3">Madhya Pradesh</td><td>1000</td>'
            "<td>Re-issue of 7.77% Madhya Pradesh SGS 2030, issued on October 21, 2026</td><td>Price</td></tr>"
            "<tr><td>1000</td><td>Re-issue of 7.58% Madhya Pradesh SGS 2038, issued on July 22, 2026</td>"
            "<td>Price</td></tr>"
            "<tr><td>1000</td><td>Re-issue of 7.66% Madhya Pradesh SGS 2048, issued on August 19, 2026</td>"
            "<td>Price</td></tr>"
            '<tr><td rowspan="2">3.</td><td rowspan="2">Tripura</td><td>200</td>'
            "<td>Re-issue of 8.03% Tripura SGS 2034, issued on October 21, 2026</td><td>Price</td></tr>"
            "<tr><td>1000</td><td>20</td><td>Yield</td></tr>"
            f"<tr><td></td><td>Total</td><td>{total}</td><td></td><td></td></tr></table>")


SDL_ANNOUNCEMENT_TEXT = ("The following State Governments have offered to sell stock by auction. "
                         "The auction will be conducted on the e-Kuber system on November 17, 2026 (Tuesday). "
                         "The results will be announced on November 17, 2026 (Tuesday) and payment by successful "
                         "bidders will be made during banking hours on November 18, 2026 (Wednesday).")

# --- RBI list pages ---------------------------------------------------------------------------------

GOI_LIST_ROWS = [  # (group or None, sl, isin, name, issue, maturity, outstanding) as the list prints them
    ("2026-27", "1", "IN0020140060", "8.15% GS 2026", "24-Nov-2014", "24-Nov-2026", "27,205.741"),
    ("2027-28", "3", "IN0020170026", "6.79% GS 2027", "15-May-2017", "15-May-2027", "1,01,908.561"),
    ("2027-28", "11", "IN0020020247", "6.01% GS 2028 (C Align)", "8-Aug-2003", "25-Mar-2028", "15,000.000"),
    ("2031-32", "43", "IN0020180041", "FRB 2031", "7-May-2018", "7-Dec-2031", "1,39,915.719"),
    ("2033-34", "57", "IN0020260066", "6.57% GS 2033", "17-Aug-2026", "17-Aug-2033", "46,000.000"),
    ("2033-34", "60", "IN0020160100", "6.57% GS 2033", "5-Dec-2016", "5-Dec-2033", "1,56,240.132"),
    ("2036-37", "77", "IN0020260025", "6.94% GS 2036", "11-May-2026", "11-May-2036", "2,72,000.000"),
]


def goi_list_page(rows=GOI_LIST_ROWS, as_of: str = "November 16, 2026", total: str | None = None,
                  unit: str = "Outstanding Stock (₹ Crore)#") -> str:
    """'List of Government of India Securities outstanding' with group, blank, Total and footnote rows."""
    if total is None:
        s = sum(float(r[6].replace(",", "")) for r in rows)
        total = f"{s:,.3f}"
    head = ("Sl. No.", "ISIN", "Nomenclature", "Date of issue", "Date of maturity", unit)
    trs = ["<tr>" + "".join(f"<th>{h}</th>" for h in head) + "</tr>"]
    group = None
    for g, *cells in rows:
        if g != group:
            if group is not None:
                trs.append("<tr>" + "<td>&nbsp;</td>" * 6 + "</tr>")
            trs.append(f"<tr><td></td><td></td><td><b>{g}</b></td><td></td><td></td><td></td></tr>")
            group = g
        trs.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    trs.append("<tr>" + "<td>&nbsp;</td>" * 6 + "</tr>")
    trs.append(f"<tr><td><b>Total</b></td><td></td><td></td><td></td><td></td><td><b>{total}</b></td></tr>")
    trs.append('<tr><td colspan="6">*Excludes special securities, # includes stripped securities</td></tr>')
    data = f'<table class="tablebg">{"".join(trs)}</table>'
    return _list_frame(f"List of Government of India Securities outstanding as on {as_of}*", data,
                       links=("https://rbidocs.example/OS_GS.XLS", "https://rbidocs.example/OS_GS.PDF"))


def _list_frame(title: str, content: str, links: tuple[str, ...] = ()) -> str:
    anchors = " or ".join(f"<a href='{u}'><img alt='file'></a> (77 kb)" for u in links) or "(152 kb)"
    return ("<div><a href='https://rbidocs.example/Vision.pdf'>Vision (652 kb)</a></div>"
            f'<table class="tablebg"><tr><td class="tableheader">{anchors}</td></tr>'
            f'<tr><td class="tableheader"><b>{title}</b></td></tr><tr><td>{content}</td></tr></table>')


FAR_ROWS = [  # (sl, isin, description, issue, maturity) as the FAR list prints them
    ("1", "IN0020180454", "07.26% GS 2029", "14 January 2019", "14 January 2029"),
    ("2", "IN0020180488", "07.32% GS 2024", "28 January 2019", "28 January 2024"),
    ("3", "IN0020190032", "07.72% GS 2049", "15 April 2019", "15 June 2049"),
    ("24", "IN0020220136", "07.10% GOI SGrB 2028", "27 January 2023", "27 January 2028"),
    ("52", "IN0020260025", "06.94% GS 2036", "11 May 2026", "11 May 2036"),
    ("55", "IN0020260066", "06.57% GS 2033 17AUG", "17 August 2026", "17 August 2033"),
    ("56", "IN0020260074", "06.53% GS 2031", "7 September 2026", "7 September 2031"),
]


def far_page(rows=FAR_ROWS) -> str:
    trs = ['<tr><td colspan="5">List of all \'Specified securities\' included under the FAR</td></tr>',
           "<tr>" + "".join(f"<th>{h}</th>" for h in ("S No.", "ISIN", "Security Description", "Date of issue",
                                                      "Date of maturity")) + "</tr>"]
    trs += ["<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows]
    content = (f'<table class="td"><tr><td>Investors may invest in specified securities. '
               f'<table class="tablebg">{"".join(trs)}</table></td></tr></table>')
    return _list_frame("Fully Accessible Route for non-resident investment in Government securities: "
                       "Specified Securities", content, links=("https://rbidocs.example/FAR.PDF",))


def sdl_index_page(as_of: str = "November 11, 2026") -> str:
    return _list_frame(f"List of State Government Securities outstanding as on {as_of}*", "",
                       links=("https://rbidocs.example/OUTSTANDINGSGS.XLS",
                              "https://rbidocs.example/OUTSTANDINGSGS.PDF"))
