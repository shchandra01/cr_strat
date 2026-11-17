import datetime as dt

import pandas as pd
import pytest
import rbi_fixtures as fx

from cr_strat import sovereign as sv
from cr_strat.htmlparse import BlockedPageError

D = dt.date
T = pd.Timestamp


# --- Names ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw, kind, coupon, year, key", [
    # dated G-secs: RBI lists and results, FIMMDA files, the Handbook
    ("7.73%  GS 2034", "gsec", 7.73, 2034, "7.73% GS 2034"),
    ("8.17% GS 2044", "gsec", 8.17, 2044, "8.17% GS 2044"),
    ("7.06% GS 2028 ", "gsec", 7.06, 2028, "7.06% GS 2028"),
    ("7.35% GOVT.STOCK 2024", "gsec", 7.35, 2024, "7.35% GS 2024"),
    ("7.16% GOVT STOCK 2023", "gsec", 7.16, 2023, "7.16% GS 2023"),
    ("7.5% GOVT. STOCK 2034", "gsec", 7.50, 2034, "7.50% GS 2034"),
    ("9.23% Govt Stock 2043", "gsec", 9.23, 2043, "9.23% GS 2043"),
    ("7.83%  GOVT.STOCK2018", "gsec", 7.83, 2018, "7.83% GS 2018"),
    ("7.95% G.S 2032", "gsec", 7.95, 2032, "7.95% GS 2032"),
    ("8.13 GS 2045", "gsec", 8.13, 2045, "8.13% GS 2045"),
    ("07.26% GS 2029", "gsec", 7.26, 2029, "7.26% GS 2029"),
    ("6.68% GS 2040", "gsec", 6.68, 2040, "6.68% GS 2040"),
    ("6.01% GS 2028 (C Align)", "gsec", 6.01, 2028, "6.01% GS 2028 (C ALIGN)"),
    ("06.57% GS 2033 17AUG", "gsec", 6.57, 2033, "6.57% GS 2033"),
    # floating-rate and green bonds
    ("GOI FLOATING RATE BOND 2024", "frb", None, 2024, "FRB 2024"),
    ("GOI FRB 2034", "frb", None, 2034, "FRB 2034"),
    ("FRB 2031", "frb", None, 2031, "FRB 2031"),
    ("7.10% GOI SGrB 2028", "green", 7.10, 2028, "7.10% GOI SGRB 2028"),
    ("07.10% GOI SGrB 2028", "green", 7.10, 2028, "7.10% GOI SGRB 2028"),
    ("7.5% GOI SGrB 2056", "green", 7.50, 2056, "7.50% GOI SGRB 2056"),
    # SDLs: FIMMDA descriptions, RBI result headers, reissue cells
    ("07.43 GJ SGS 2027", "sdl", 7.43, 2027, "7.43% GJ SGS 2027"),
    ("07.74 AP SGS 2032 03 JAN", "sdl", 7.74, 2032, "7.74% AP SGS 2032"),
    ("07.83 RJ SDL 2032", "sdl", 7.83, 2032, "7.83% RJ SGS 2032"),
    ("06.83 TN SDL 2031 15 DEC", "sdl", 6.83, 2031, "6.83% TN SGS 2031"),
    ("ASSAM SGS 2046", "sdl", None, 2046, None),
    ("JAMMU AND KASHMIR SGS 2049", "sdl", None, 2049, None),
    ("Re-issue of 7.83% ASSAM SGS 2046 issued on 29-APR-2026", "sdl", 7.83, 2046, "7.83% AS SGS 2046"),
    ("Re-issue of 8.11% UTTARPRADESH SGS 2038 issued on 14-OCT-2026", "sdl", 8.11, 2038, "8.11% UP SGS 2038"),
    ("Re-issue of 7.68% Andhra Pradesh SGS 2051, issued on July 29, 2026", "sdl", 7.68, 2051, "7.68% AP SGS 2051"),
    ("7.58% Madhya Pradesh SGS 2038", "sdl", 7.58, 2038, "7.58% MP SGS 2038"),
    ("8.03% Tripura SGS 2034", "sdl", 8.03, 2034, "8.03% TR SGS 2034"),
    # T-bills: a tenor, no maturity date
    ("91-Day", "tbill", None, None, "91DTB"),
    ("182-Day", "tbill", None, None, "182DTB"),
    ("364-Day", "tbill", None, None, "364DTB"),
])
def test_name_formats(raw, kind, coupon, year, key):
    name = sv.parse_security_name(raw)
    assert (name.kind, name.coupon_pct, name.maturity_year, name.key, name.raw) == (kind, coupon, year, key, raw)


def test_name_details():
    p = sv.parse_security_name
    assert p("06.57% GS 2033 17AUG").maturity_date == D(2033, 8, 17)
    assert p("07.74 AP SGS 2032 03 JAN").maturity_date == D(2032, 1, 3) and p("07.74 AP SGS 2032").maturity_date is None
    assert p("6.01% GS 2028 (C Align)").flags == ("(C ALIGN)",)
    assert p("7.72% NI GS 2025").new_issue and p("5.79% NEW GS 2030").new_issue
    assert p("7.37% NEW GOI SGrB 2054").new_issue and p("7.37% NEW GOI SGrB 2054").kind == "green"
    assert not p("7.73% GS 2034").new_issue
    bihar = p("BIHAR SGS 2044*")
    assert (bihar.state_code, bihar.flags, bihar.state_name) == ("BR", ("*",), "BIHAR")
    assert p("CHHATTISGARH SGS 2036#").state_code == "CG" and p("TAMILNADU SGS 2041").state_code == "TN"
    reissue = p("Re-issue of 7.83% ASSAM SGS 2046 issued on 29-APR-2026")
    assert reissue.reissue and reissue.first_issue_date == D(2026, 4, 29) and reissue.state_code == "AS"
    assert p("91-Day").tenor_days == 91 and p("91-Day").maturity_date is None
    assert p("7.73%\xa0GS 2034").key == "7.73% GS 2034"  # NBSP


def test_names_without_coupon_take_one():
    new = sv.parse_security_name("New GS 2055")
    assert (new.kind, new.coupon_pct, new.maturity_year, new.new_issue, new.key) == ("gsec", None, 2055, True, None)
    assert new.with_coupon(7.24).key == "7.24% GS 2055" and new.with_coupon(None).key is None
    header = sv.parse_security_name("BIHAR SGS 2044*").with_coupon(8.1)
    assert header.key == "8.10% BR SGS 2044" and header.flags == ("*",)
    assert sv.parse_security_name("7.73% GS 2034").with_coupon(9.0).coupon_pct == 7.73


@pytest.mark.parametrize("raw", [
    "IIB 2023", "1.44% INFLATION INDEX GS 2023", "CMB 63 DAYS", "GS CG 15JUN2027", "", None, "08", "18 -Years",
    "06.57% GS 2033 31FEB", "6.57% XS 2033", "Total",
])
def test_unknown_names_are_not_guessed(raw):
    name = sv.parse_security_name(raw)
    assert name.kind == "unknown" and name.key is None and name.coupon_pct is None
    with pytest.raises(sv.UnknownSecurityName):
        sv.parse_security_name(raw, strict=True)


def test_unknown_states():
    ladakh = sv.parse_security_name("LADAKH SGS 2040")
    assert ladakh.kind == "sdl" and ladakh.state_code is None and "unknown_state" in ladakh.flags
    assert sv.parse_security_name("07.74 XX SGS 2032").key is None
    with pytest.raises(sv.UnknownSecurityName, match="unknown state"):
        sv.parse_security_name("LADAKH SGS 2040", strict=True)


def test_sdl_tenor_cells():
    assert sv.parse_sdl_tenor("18 -Years") == sv.SdlTenor("18 -Years", True, 18)
    assert sv.parse_sdl_tenor("08").tenor_years == 8 and sv.parse_sdl_tenor("10 -Years").new_issue
    cell = sv.parse_sdl_tenor("Re-issue of 7.64% CHHATTISGARH SGS 2042 issued on 08-JUL-2026")
    assert not cell.new_issue and cell.reissue_of.key == "7.64% CG SGS 2042"
    assert cell.reissue_of.first_issue_date == D(2026, 7, 8)
    with pytest.raises(ValueError, match="not an SDL tenor"):
        sv.parse_sdl_tenor("7.83% ASSAM SGS 2046")


def test_add_name_fields():
    df = sv.add_name_fields(pd.DataFrame({"security": ["6.94% GS 2036", "07.74 AP SGS 2032 03 JAN", "junk"]}))
    assert df["kind"].tolist() == ["gsec", "sdl", "unknown"]
    assert df["name_key"].tolist()[:2] == ["6.94% GS 2036", "7.74% AP SGS 2032"] and pd.isna(df["name_key"].iloc[2])
    assert df["name_maturity_date"].iloc[1] == T("2032-01-03") and pd.isna(df["maturity_year"].iloc[2])


# --- States -----------------------------------------------------------------------------------------

def test_state_table():
    fimmda = [s for s in sv.STATES if s.source == sv.FIMMDA_STATE_LIST]
    local = [s for s in sv.STATES if s.source == sv.LOCAL_CODE]
    assert len(fimmda) == 29 and [s.code for s in local] == ["TR", "DL"]
    assert all(s.isin_prefix for s in fimmda) and len({s.isin_prefix for s in fimmda}) == 29
    assert len({s.code for s in sv.STATES}) == 31


@pytest.mark.parametrize("name, code", [
    ("Andhra Pradesh", "AP"), ("UTTARPRADESH", "UP"), ("UTTAR PRADESH", "UP"), ("TAMILNADU", "TN"),
    ("CHHATTISGARH", "CG"), ("Chattisgarh", "CG"), ("JAMMU AND KASHMIR", "JK"), ("Jammu & Kashmir", "JK"),
    ("ODISHA", "OD"), ("Orissa", "OD"), ("Puducherry", "PY"), ("Tripura", "TR"), ("Delhi", "DL"), ("GJ", "GJ"),
    ("Ladakh", None), ("", None),
])
def test_state_code(name, code):
    assert sv.state_code(name) == code


def test_isin_digits():
    assert sv.state_for_isin("IN1520230161") == "GJ" and sv.state_for_isin("IN0020140060") == "GOI"
    assert sv.state_for_isin("IN3320230359") == "UP" and sv.state_for_isin("US0020140060") is None
    assert sv.isin_fy("IN0020140060") == 2014 and sv.isin_fy("INE001A07AB1") is None
    assert sv.financial_year(D(2026, 3, 31)) == 2025 and sv.financial_year(D(2026, 4, 1)) == 2026


# --- RBI lists --------------------------------------------------------------------------------------

def test_goi_outstanding_list():
    df = sv.load_goi_outstanding(fx.goi_list_page())
    assert list(df.columns) == list(sv.GOI_LIST_COLUMNS) and len(df) == len(fx.GOI_LIST_ROWS)
    assert df.attrs["as_of"] == D(2026, 11, 16) and df["as_of"].eq(T("2026-11-16")).all()
    assert df.attrs["xls_url"] == "https://rbidocs.example/OS_GS.XLS"
    assert df.attrs["pdf_url"] == "https://rbidocs.example/OS_GS.PDF"  # not the page-wide 'Vision' PDF
    rows = df.set_index("sl_no")
    first = rows.loc[1]
    assert (first["isin"], first["name"], first["issue_date"], first["maturity_date"], first["maturity_fy"]) == (
        "IN0020140060", "8.15% GS 2026", T("2014-11-24"), T("2026-11-24"), "2026-27")
    assert first["outstanding_cr"] == pytest.approx(27205.741)
    assert rows.loc[3, "outstanding_cr"] == pytest.approx(101908.561)  # Indian digit grouping
    assert rows.loc[11, "issue_date"] == T("2003-08-08")  # '8-Aug-2003': day not zero-padded
    assert rows.loc[43, "name"] == "FRB 2031" and rows.loc[43, "maturity_date"] == T("2031-12-07")
    assert rows.loc[57, "isin"] == "IN0020260066" and rows.loc[60, "maturity_date"] == T("2033-12-05")
    assert df.attrs["total_cr"] == pytest.approx(df["outstanding_cr"].sum())


@pytest.mark.parametrize("kwargs, message", [
    (dict(total="1,00,000.000"), "rows sum to"),
    (dict(unit="Outstanding Stock (₹ Lakh)#"), "not in crore"),
    (dict(as_of="16 November"), "no 'as on <date>'"),
    (dict(rows=[*fx.GOI_LIST_ROWS[:2], ("2027-28",) + fx.GOI_LIST_ROWS[1][1:]]), "ISINs listed twice"),
    (dict(rows=[("2027-28",) + fx.GOI_LIST_ROWS[0][1:]]), "matures 2026-11-24 but sits under 2027-28"),
    (dict(rows=[("2026-27", "1", "IN0020140060", "8.15% GS 2026", "24/11/2014", "24-Nov-2026", "1.000")]),
     "not a date"),
])
def test_goi_outstanding_refuses_bad_pages(kwargs, message):
    with pytest.raises(ValueError, match=message):
        sv.load_goi_outstanding(fx.goi_list_page(**kwargs))


def test_list_pages_refuse_blocked_and_foreign_pages():
    with pytest.raises(BlockedPageError, match="Support ID 4063894414161732744"):
        sv.load_goi_outstanding(fx.blocked_page("4063894414161732744"))
    with pytest.raises(ValueError, match="no RBI list frame"):
        sv.load_far_list("<html><body><table><tr><td>ISIN</td></tr></table></body></html>")
    with pytest.raises(ValueError, match="no 'as on <date>'"):
        sv.load_goi_outstanding(fx.far_page())
    with pytest.raises(ValueError, match="no table with the columns"):
        sv.load_goi_outstanding(fx.sdl_index_page())


def test_far_list():
    df = sv.load_far_list(fx.far_page())
    assert list(df.columns) == list(sv.FAR_COLUMNS) and len(df) == len(fx.FAR_ROWS)
    rows = df.set_index("sl_no")
    assert (rows.loc[2, "isin"], rows.loc[2, "name"], rows.loc[2, "issue_date"], rows.loc[2, "maturity_date"]) == (
        "IN0020180488", "07.32% GS 2024", T("2019-01-28"), T("2024-01-28"))
    assert rows.loc[3, "maturity_date"] == T("2049-06-15") and rows.loc[24, "name"] == "07.10% GOI SGrB 2028"
    assert rows.loc[55, "name"] == "06.57% GS 2033 17AUG" and rows.loc[56, "issue_date"] == T("2026-09-07")


def test_sdl_outstanding_index_has_links_not_data():
    page = sv.read_sdl_outstanding_index(fx.sdl_index_page())
    assert page.as_of == D(2026, 11, 11)
    assert page.xls_url == "https://rbidocs.example/OUTSTANDINGSGS.XLS"
    assert page.pdf_url == "https://rbidocs.example/OUTSTANDINGSGS.PDF"
    with pytest.raises(ValueError, match="not the SDL outstanding list"):
        sv.read_sdl_outstanding_index(fx.goi_list_page())


# --- Master -----------------------------------------------------------------------------------------

def _master(**kw):
    return sv.build_master(sv.load_goi_outstanding(fx.goi_list_page()), sv.load_far_list(fx.far_page()), **kw)


def test_master_joins_lists():
    m = _master().set_index("isin")
    assert list(m.reset_index().columns) == list(sv.MASTER_COLUMNS)
    goi = {r[2] for r in fx.GOI_LIST_ROWS}
    far = {r[1] for r in fx.FAR_ROWS}
    assert set(m.index) == goi | far
    row = m.loc["IN0020260025"]  # in both lists
    assert (row["name"], row["name_key"], row["far"], row["far_name"]) == (
        "6.94% GS 2036", "6.94% GS 2036", True, "06.94% GS 2036")
    assert row["in_latest_list"] and row["outstanding_cr"] == pytest.approx(272000.0) and not row["matured"]
    assert row["sources"] == "rbi_far;rbi_goi_outstanding" and row["conflicts"] == ""
    matured = m.loc["IN0020180488"]  # FAR only, matured
    assert matured["matured"] and not matured["in_latest_list"] and pd.isna(matured["outstanding_cr"])
    assert matured["name_key"] == "7.32% GS 2024" and matured["sources"] == "rbi_far"
    assert m.loc["IN0020180041", "kind"] == "frb" and m.loc["IN0020220136", "kind"] == "green"
    assert not m.loc["IN0020020247", "isin_fy_matches"] and m.loc["IN0020020247", "isin_fy"] == 2002
    assert m.loc["IN0020140060", "isin_fy_matches"]
    assert m.loc["IN0020260066", "far_name"] == "06.57% GS 2033 17AUG" and m.loc["IN0020260066", "conflicts"] == ""


def test_master_snapshots_and_conflicts():
    old = sv.load_goi_outstanding(fx.goi_list_page(as_of="November 13, 2026", rows=[
        ("2026-27", "1", "IN0020140060", "8.15% GS 2026", "24-Nov-2014", "24-Nov-2026", "27,000.000"),
        ("2026-27", "2", "IN0020060078", "8.24% GS 2027", "15-Feb-2007", "15-Feb-2027", "58,052.341")]))
    new = sv.load_goi_outstanding(fx.goi_list_page())
    far = sv.load_far_list(fx.far_page()).copy()
    far.loc[far["isin"] == "IN0020260025", "maturity_date"] = T("2036-05-12")
    m = sv.build_master([old, new], far).set_index("isin")
    first = m.loc["IN0020140060"]
    assert first["outstanding_cr"] == pytest.approx(27205.741) and first["outstanding_as_of"] == T("2026-11-16")
    assert (first["first_listed"], first["last_listed"]) == (T("2026-11-13"), T("2026-11-16"))
    gone = m.loc["IN0020060078"]  # only in the older snapshot
    assert not gone["in_latest_list"] and gone["outstanding_as_of"] == T("2026-11-13")
    assert m.loc["IN0020260025", "maturity_date"] == T("2036-05-11")  # the GoI list wins
    assert "maturity_date: rbi_goi_outstanding 2036-05-11, rbi_far 2036-05-12" in m.loc["IN0020260025", "conflicts"]


def test_master_extra_listings_and_checks(isin):
    sdl = pd.DataFrame({"isin": ["IN1520230161", "IN3320230359"], "name": ["07.43 GJ SGS 2027", "07.45 MH SGS 2038"],
                        "maturity_date": ["2027-03-07", "2038-03-22"], "source": "fimmda_sdl"})
    m = sv.build_master(extra=[sdl]).set_index("isin")
    assert m.loc["IN1520230161", "state_code"] == "GJ" and m.loc["IN1520230161", "conflicts"] == ""
    assert "digits 33 are UP, name says MH" in m.loc["IN3320230359", "conflicts"]
    bad_year = pd.DataFrame({"isin": ["IN0020140060"], "name": ["8.15% GS 2027"], "maturity_date": ["2026-11-24"]})
    assert "name says 2027" in sv.build_master(extra=[bad_year]).iloc[0]["conflicts"]
    with pytest.raises(ValueError, match="invalid ISINs"):
        sv.build_master(extra=[pd.DataFrame({"isin": ["IN0020140061"], "name": ["8.15% GS 2026"]})])
    assert sv.build_master().empty and list(sv.build_master().columns) == list(sv.MASTER_COLUMNS)


def test_match_isin():
    m = _master()
    assert sv.match_isin(m, "6.94% GS 2036", on="2026-11-13") == sv.IsinMatch(
        "IN0020260025", "matched", ("IN0020260025",))
    both = sv.match_isin(m, "6.57% GS 2033", on="2026-11-20")
    assert both.status == "ambiguous" and set(both.candidates) == {"IN0020260066", "IN0020160100"}
    assert sv.match_isin(m, "6.57% GS 2033", maturity="2033-12-05").isin == "IN0020160100"
    assert sv.match_isin(m, "06.57% GS 2033 17AUG").isin == "IN0020260066"  # the name's day and month
    assert sv.match_isin(m, "6.57% GS 2033", on="2016-12-02").isin == "IN0020160100"  # before the 2026 issue
    assert sv.match_isin(m, "9.99% GS 2099").status == "none" and sv.match_isin(m, "junk").status == "unparsed"
    df = sv.attach_isin(pd.DataFrame({"security": ["6.94% GS 2036", "6.57% GS 2033"],
                                      "auction_date": [T("2026-11-13"), T("2026-11-20")]}), m)
    assert df["isin"].iloc[0] == "IN0020260025" and pd.isna(df["isin"].iloc[1])
    assert df["isin_match"].tolist() == ["matched", "ambiguous"]


def test_goi_outstanding_needs_its_total_row():
    page = fx.goi_list_page()
    total_row = page[page.index("<tr><td><b>Total</b>"):]
    no_total = page.replace(total_row[:total_row.index("</tr>") + 5], "")
    with pytest.raises(ValueError, match="no Total row to check the 7 security rows"):
        sv.load_goi_outstanding(no_total)
    loose = sv.load_goi_outstanding(no_total, require_total=False)
    assert len(loose) == 7 and loose.attrs["total_cr"] is None


def test_match_isin_checks_the_date_for_a_single_candidate():
    m = pd.DataFrame([dict(isin="IN0020220151", name="7.26% GS 2033", name_key="7.26% GS 2033",
                           maturity_date=T("2033-02-06"), issue_date=T("2023-02-06"))])
    for on in ("2019-01-04", "2034-01-04", "2033-02-06"):  # before the issue, after and on the maturity
        assert sv.match_isin(m, "7.26% GS 2033", on=on) == sv.IsinMatch(None, "none", ())
    assert sv.match_isin(m, "7.26% GS 2033", on="2023-02-03").isin == "IN0020220151"  # settles within 7 days
    assert sv.match_isin(m, "7.26% GS 2033").isin == "IN0020220151"  # no date: the key alone
    out = sv.attach_isin(pd.DataFrame({"security": ["7.26% GS 2033"] * 2,
                                       "auction_date": [T("2019-01-04"), T("2024-01-05")]}), m)
    assert out["isin_match"].tolist() == ["none", "matched"]


def test_master_names_extra_rows_without_an_isin():
    extra = pd.DataFrame({"isin": ["IN2020230123", None], "name": ["7.74% KL SGS 2033", "8.00% KL SGS 2034"]})
    with pytest.raises(ValueError, match=r"extra listing 1: no ISIN in 1 row\(s\): row 1 \('8.00% KL SGS 2034'\)"):
        sv.build_master(extra=[extra])
    extra.attrs["source_file"] = "kerala.csv"
    with pytest.raises(ValueError, match="extra listing kerala.csv"):
        sv.build_master(extra=[extra])
    with pytest.raises(ValueError, match=r"IN0020140061 \(extra: '8.15% GS 2026'\)"):
        sv.build_master(extra=[pd.DataFrame({"isin": ["IN0020140061"], "name": ["8.15% GS 2026"]})])
