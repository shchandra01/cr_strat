import datetime as dt

import pandas as pd
import pytest

import auction_fixtures as fx
from cr_strat.auctions import (
    AUCTION_COLUMNS,
    FIMMDA_GSEC_COLUMNS,
    FIMMDA_SDL_COLUMNS,
    HANDBOOK_COLUMNS,
    AuctionLoadError,
    build_auctions,
    confirmed_holidays,
    holiday_discrepancies,
    load_fimmda_gsec,
    load_fimmda_sdl,
    load_handbook_auctions,
    normalise_switches,
    read_auctions,
    reprice,
    reproduces,
    settlement_evidence,
    text_decimals,
    weekdays_after,
)
from cr_strat.calendars import BusinessCalendar, load_holidays, mumbai_settlement_calendar, DEFAULT_HOLIDAYS_CSV
from cr_strat.sovereign import build_master
from test_bonds import AUCTIONS

D = dt.datetime
T = pd.Timestamp

# Typed-in facts from RBI's lists (ISIN, name, first issue, maturity), for the securities the tests auction.
LISTED = [
    ("IN0020230077", "7.18% GS 2037", "2023-07-24", "2037-07-24"),
    ("IN0020220037", "7.38% GS 2027", "2022-06-20", "2027-06-20"),
    ("IN0020180454", "7.26% GS 2029", "2019-01-14", "2029-01-14"),
    ("IN0020150051", "7.73% GS 2034", "2015-10-12", "2034-12-19"),
    ("IN0020240050", "7.04% GS 2029", "2024-06-03", "2029-06-03"),
    ("IN0020220102", "7.41% GS 2036", "2022-12-19", "2036-12-19"),
    ("IN0020250067", "6.01% GS 2030", "2025-07-21", "2030-07-21"),
    ("IN0020250059", "6.28% GS 2032", "2025-07-14", "2032-07-14"),
    ("IN0020250042", "6.68% GS 2040", "2025-07-07", "2040-07-07"),
    ("IN0020260066", "6.57% GS 2033", "2026-08-17", "2033-08-17"),
    ("IN0020160100", "6.57% GS 2033", "2016-12-05", "2033-12-05"),
    ("IN0020260025", "6.94% GS 2036", "2026-05-11", "2036-05-11"),
]


@pytest.fixture(scope="module")
def cal():
    return mumbai_settlement_calendar()


@pytest.fixture(scope="module")
def master():
    frame = pd.DataFrame(LISTED, columns=["isin", "name", "issue_date", "maturity_date"]).assign(source="rbi_list")
    return build_master(extra=[frame])


# --- FIMMDA G-sec workbooks ----------------------------------------------------------------------------

def _sheets_2015_2026():
    return {
        "2015-2016": [(D(2016, 2, 5), "7.73%  GS 2034", 1000, 1000, "95.61", "8.1893")],  # text cells, FY2015-16
        "2018-2019": [(D(2019, 1, 18), "7.00% GS 2021", 2000, 3000, "-", "#ERROR")],
        "2023-2024": [(D(2025, 4, 4), "6.64% GS 2027", "₹6,000", "₹5997.390", None, None),  # stray FY2025-26 row
                      (D(2023, 9, 28), "7.25% GS 2063", 12000, 12000, 97.35, "\xa07.4578"),
                      (D(2023, 9, 28), "7.18% GS 2037", 10000, 10000, 101.72, 7.0006)],
        "2025-2026": [(D(2025, 4, 4), "6.64% GS 2027", 6000, 5997.39, 100.94, 6.2478),
                      (D(2025, 6, 13), "6.98% GOI SGrB 2054", 5000, "NIL", "NA", "NA"),
                      (D(2025, 8, 14), "New GS 2055", 13000, 12996.514, 100, 7.24),
                      (D(2025, 9, 19), "6.82% GS 2033", 10000, 9995.5, 98.2, 7.06)],
    }


def test_fimmda_gsec_fields_flags_and_printed_decimals(tmp_path):
    path = fx.gsec_workbook(tmp_path / "Gsec_Auction_data_2025_2026.xlsx", _sheets_2015_2026())
    load = load_fimmda_gsec([path])
    rows = load.rows.set_index(["auction_date", "security"])
    assert list(load.rows.columns) == list(FIMMDA_GSEC_COLUMNS) and len(load.rows) == 8  # the stray row merged away
    r = rows.loc[(T("2016-02-05"), "7.73%  GS 2034")]
    assert (r["fy"], r["name_key"], r["notified_cr"], r["cutoff_price_text"], r["cutoff_yield_text"]) == (
        "2015-16", "7.73% GS 2034", 1000.0, "95.61", "8.1893")
    bad = rows.loc[(T("2019-01-18"), "7.00% GS 2021")]
    assert bad["cutoff_price"] is None or pd.isna(bad["cutoff_price"])
    assert set(bad["flags"].split(";")) == {"greenshoe", "bad_price_yield"} and bad["status"] == "accepted"
    nbsp = rows.loc[(T("2023-09-28"), "7.25% GS 2063")]
    assert nbsp["cutoff_yield"] == 7.4578 and nbsp["cutoff_yield_text"] == "7.4578"
    assert nbsp["cutoff_price_text"] == "97.35"
    rejected = rows.loc[(T("2025-06-13"), "6.98% GOI SGrB 2054")]
    assert rejected["status"] == "rejected" and rejected["accepted_cr"] == 0 and rejected["kind"] == "green"
    new = rows.loc[(T("2025-08-14"), "New GS 2055")]
    assert new["name_key"] == "7.24% GS 2055" and new["name_new_issue"] and new["cutoff_yield_text"] == "7.2400"
    # numeric cells print with their format's decimals: 98.2 is '98.20', 7.06 is '7.0600'
    fmt = rows.loc[(T("2025-09-19"), "6.82% GS 2033")]
    assert (fmt["cutoff_price_text"], fmt["cutoff_yield_text"]) == ("98.20", "7.0600")
    stray = load.report[load.report["detail"].str.contains("stray row dropped")]
    assert len(stray) == 1 and "2023-2024!2" in stray["detail"].iloc[0]
    kept = rows.loc[(T("2025-04-04"), "6.64% GS 2027")]
    assert kept["sheet"] == "2025-2026" and kept["cutoff_price"] == 100.94 and "outside_sheet_year" not in kept["flags"]


def test_fimmda_sheet_names_are_not_trusted(tmp_path):
    # FIMMDA's 2026-27 file holds its FY2026-27 auctions in a sheet named '2025-2026'.
    path = fx.gsec_workbook(tmp_path / "g.xlsx", {"2025-2026": [(D(2026, 4, 2), "6.68% GS 2040", 17000, 16986.006,
                                                                 92.63, 7.5303)]})
    load = load_fimmda_gsec([path])
    assert load.rows["fy"].tolist() == ["2026-27"]
    assert load.report["detail"].str.contains("holds FY2026-27 auctions").any()


def test_overlapping_files_merge_and_conflicts_are_reported(tmp_path):
    row = (D(2016, 2, 5), "7.73%  GS 2034", 1000, 1000, "95.61", "8.1893")
    a = fx.gsec_workbook(tmp_path / "Gsec_2023_24.xlsx", {"2015-2016": [row]})
    b = fx.gsec_workbook(tmp_path / "Gsec_2024_25.xlsx", {"2015-2016": [row]})
    c = fx.gsec_workbook(tmp_path / "Gsec_2025_26.xlsx", {"2015-2016": [(*row[:3], 900, *row[4:])]})
    same = load_fimmda_gsec([a, b])
    assert len(same.rows) == 1 and same.rows["source_files"].iloc[0].count("xlsx") == 2 and not len(same.conflicts)
    differ = load_fimmda_gsec([a, b, c])
    assert len(differ.rows) == 1 and differ.rows["accepted_cr"].iloc[0] == 900  # the later file wins
    assert "conflicting_duplicate" in differ.rows["flags"].iloc[0]
    assert differ.conflicts[["field", "kept", "other"]].values.tolist() == [["accepted_cr", 900.0, 1000.0]] * 2
    assert (differ.report["check"] == "fimmda_conflict").sum() == 2


def test_fimmda_non_round_rows_are_flagged_and_bad_files_refused(tmp_path):
    path = fx.gsec_workbook(tmp_path / "g.xlsx", {"2017-2018": [
        (D(2018, 3, 22), "8.24% GOVT.STOCK 2018", 15091, 15091, "100.13", "6.4502"),
        (D(2018, 3, 22), "8.30% GOVT STOCK 2042", 14529.44, 14529.44, "104.00", "7.9270")]})
    rows = load_fimmda_gsec([path]).rows.set_index("security")
    assert rows.loc["8.30% GOVT STOCK 2042", "flags"] == "non_round_notified"
    assert rows.loc["8.24% GOVT.STOCK 2018", "flags"] == "" and rows.loc["8.24% GOVT.STOCK 2018", "name_key"] == \
        "8.24% GS 2018"
    (tmp_path / "page.xlsx").write_text("<html>blocked</html>")
    with pytest.raises(AuctionLoadError, match="not an .xlsx workbook"):
        load_fimmda_gsec([tmp_path / "page.xlsx"])


# --- FIMMDA SDL workbook -------------------------------------------------------------------------------

JAN = [  # typed-in facts from the FIMMDA SDL file's 'January' and 'October' sheets (2024)
    ("02-01-2024", "IN1020230679", "07.74 AP SGS 2032 03 JAN", "03-01-2032", 7.74, 7.7151, "NEW", 7.74, "AP",
     "#REF!", 1000, 7740),
    (D(2024, 1, 2), "IN1520230161", "07.43 GJ SGS 2027", D(2027, 1, 3), 7.43, 7.4203, "NEW ", 7.43, "GJ", "#REF!",
     1000, 7430),
]
OCT = [
    ("01-10-2024", "IN1920240034", "07.00 KA SGS 2030", "03-10-2030", 7.0, "6.9888\t", "NEW", 7.0, "KA",
     "Karnataka", 2000, 14000),
    ("01-10-2024", "IN2920220018", "07.70 RJ SDL 2032", D(2032, 5, 18), 7.1006, 7.0821, "REISSUE", 7.1006, "RJ",
     "Rajasthan", 500, 3550.3),
]
MARCH = [("21-03-2024", "IN2220230345", "07.46 UP SGS 2039", "22-03-2039", 7.46, 7.4407, "NEW", 7.46, "UP",
          "#REF!", 3000, 22380)]


def _repeat(rows, years=1):
    """A sheet that repeats ``rows`` with only the auction year moved, as FIMMDA's 2025 copies do."""
    out = []
    for r in rows:
        day = r[0] if isinstance(r[0], D) else D.strptime(r[0], "%d-%m-%Y")
        out.append((day.replace(year=day.year + years), *r[1:]))
    return out


def test_fimmda_sdl_rows_and_cell_quirks(tmp_path):
    path = fx.sdl_workbook(tmp_path / "SDL.xlsx", {"January": JAN, "October": OCT, "March": MARCH},
                           extra={"January Website": [["Row Labels", "Sum of Amount"], ["AP", 1000]]})
    load = load_fimmda_sdl([path])
    rows = load.rows.set_index("isin")
    assert list(load.rows.columns) == list(FIMMDA_SDL_COLUMNS) and len(rows) == 5
    ap = rows.loc["IN1020230679"]
    assert (ap["auction_date"], ap["maturity_date"], ap["coupon_pct"], ap["state_code"], ap["issue_type"]) == (
        T("2024-01-02"), T("2032-01-03"), 7.74, "AP", "new")
    assert ap["cutoff_yield"] == 7.74 and ap["way"] == 7.7151 and ap["name_key"] == "7.74% AP SGS 2032"
    assert rows.loc["IN1520230161", "issue_type"] == "new"  # 'NEW ' with a trailing space
    assert rows.loc["IN1920240034", "way"] == 6.9888  # text with a trailing tab
    rj = rows.loc["IN2920220018"]
    assert rj["coupon_pct"] == 7.70 and pd.isna(rj["cutoff_yield"]) and "coupon_cell_differs" in rj["flags"]
    assert rj["maturity_date"] == T("2032-05-18") and rj["issue_type"] == "reissue"
    up = rows.loc["IN2220230345"]
    assert "state_conflict" in up["flags"]  # ISIN digits 22 are Maharashtra's; the cells say UP
    assert load.report["detail"].str.contains("ISIN digits say MH").sum() == 1


def test_fimmda_sdl_repeated_sheets_are_dropped(tmp_path):
    path = fx.sdl_workbook(tmp_path / "SDL.xlsx", {"January": JAN, "March": MARCH, " January2025": [],
                                                   "February 2025": _repeat(JAN), "March 2025": _repeat(MARCH)},
                           headerless=("March 2025",))
    load = load_fimmda_sdl([path])
    assert sorted(load.rows["isin"]) == ["IN1020230679", "IN1520230161", "IN2220230345"]
    assert load.rows["auction_date"].dt.year.unique().tolist() == [2024]
    dropped = load.report[load.report["check"] == "fimmda_sdl_sheet"]
    notes = dropped[dropped["severity"] == "warning"]["detail"].tolist()
    assert len(notes) == 2 and all("dropped as a repeat" in n for n in notes)
    assert "equal sheet 'January'" in notes[0] and "equal sheet 'March'" in notes[1]
    # the copy's state conflict is not reported twice
    assert load.report["detail"].str.contains("ISIN digits say MH").sum() == 1


def test_fimmda_sdl_headerless_sheet_is_read_when_genuine(tmp_path):
    path = fx.sdl_workbook(tmp_path / "SDL.xlsx", {"October": OCT}, headerless=("October",))
    assert len(load_fimmda_sdl([path]).rows) == 2


# --- Handbook Table 221 --------------------------------------------------------------------------------

HBS_ROWS = [  # typed-in facts of three FY2025-26 auctions
    ("01-Aug-2025", "04-Aug-2025", "16000", "14.93", "270", "38491", "7", "15", "154", "15985", "7", "15", "0",
     "99.82/6.6985", "6.68% GS 2040"),
    ("31-Oct-2025", "03-Nov-2025", "11000", "6.70", "143", "26584", "5", "10", "0", "0", "0", "0", "0", "-/-",
     "6.28% GS 2032"),
    ("16-Jan-2026", "19-Jan-2026", "13000", "50.00", "134", "37390", "3", "7", "16", "12993", "3", "7", "0",
     "100/7.43", "7.43% GS 2076"),
]


def test_handbook_table_221(tmp_path):
    (tmp_path / "hbs.html").write_text(fx.handbook_page(HBS_ROWS), encoding="utf-8")
    hb = load_handbook_auctions([tmp_path / "hbs.html"]).set_index("security")
    assert list(hb.reset_index()[list(HANDBOOK_COLUMNS)].columns) == list(HANDBOOK_COLUMNS)
    a = hb.loc["6.68% GS 2040"]
    assert (a["auction_date"], a["issue_date"], a["notified_cr"], a["comp_accepted_cr"], a["nc_accepted_cr"]) == (
        T("2025-08-01"), T("2025-08-04"), 16000, 15985, 15)
    assert (a["cutoff_price_text"], a["cutoff_yield_text"], a["fy"]) == ("99.82", "6.6985", "2025-26")
    rejected = hb.loc["6.28% GS 2032"]
    assert rejected["status"] == "rejected" and pd.isna(rejected["cutoff_price"])
    assert rejected["cutoff_yield_text"] == ""
    assert hb.loc["7.43% GS 2076", "cutoff_yield"] == 7.43
    (tmp_path / "other.html").write_text("<html><table><tr><td>x</td><td>y</td></tr></table></html>")
    with pytest.raises(AuctionLoadError, match="no Table 221 auction rows"):
        load_handbook_auctions([tmp_path / "other.html"])
    issue, notified = '<td rowspan="3">Date of Issue</td>', '<td rowspan="3">Notified Amount</td>'
    moved = fx.handbook_page(HBS_ROWS).replace(issue + notified, notified + issue)  # columns swapped
    (tmp_path / "moved.html").write_text(moved, encoding="utf-8")
    with pytest.raises(AuctionLoadError, match="has columns"):
        load_handbook_auctions([tmp_path / "moved.html"])


# --- Repricing -----------------------------------------------------------------------------------------

def test_reproduces_with_ties_and_decimals():
    assert reproduces(7.32034, "7.3203") and not reproduces(7.32051, "7.3203")
    assert reproduces(7.8296500, "7.8296")  # a tie in the source's own arithmetic
    assert reproduces(7.244, "7.24") and not reproduces(7.246, "7.24")
    assert not reproduces(None, "7.24") and not reproduces(7.24, "") and not reproduces(7.24, "NA")
    assert text_decimals("7.4578") == 4 and text_decimals("100") == 0 and text_decimals("x") is None
    assert weekdays_after(dt.date(2026, 11, 13), 3) == [dt.date(2026, 11, 16), dt.date(2026, 11, 17),
                                                        dt.date(2026, 11, 18)]


@pytest.mark.parametrize("pair", [p for p in AUCTIONS if p.trade != "2025-09-04" and p.price != 100.0],
                         ids=lambda p: f"{p.security} {p.trade}")
def test_published_pairs_reproduce_at_calendar_t1(cal, pair):
    settle = cal.settlement_date(dt.date.fromisoformat(pair.trade), 1)
    result = reprice("gsec", pair.coupon, pair.maturity, pair.price, pair.printed_yield, pair.trade,
                     settlement=settle, settlement_source="calendar_t1", calendar=cal)
    assert result.status == "reproduced" and result.settlement == dt.date.fromisoformat(pair.settle)
    assert dt.date.fromisoformat(pair.settle) in result.candidates


def test_settlement_inferred_when_a_holiday_moved(cal):
    # 4 Sep 2025: the yield was fixed for 8 Sep (Id-E-Milad was then on 5 Sep); the holiday moved to 8 Sep,
    # so the auction settled on 9 Sep. T+1 on the calendar (5 Sep) does not reproduce.
    result = reprice("gsec", 6.28, "2032-07-14", 98.52, "6.5497", "2025-09-04", settlement=dt.date(2025, 9, 5),
                     settlement_source="calendar_t1", calendar=cal)
    assert result.status == "settlement_inferred" and result.source == "yield_rolled"
    assert result.settlement == dt.date(2025, 9, 9) and result.candidates == (dt.date(2025, 9, 8), dt.date(2025, 9, 9))
    # without a calendar the first date that fits is taken as it is
    plain = reprice("gsec", 6.28, "2032-07-14", 98.52, "6.5497", "2025-09-04", settlement=dt.date(2025, 9, 5),
                    settlement_source="calendar_t1")
    assert (plain.status, plain.settlement, plain.source) == ("settlement_inferred", dt.date(2025, 9, 8), "yield")


def test_printed_settlement_is_never_replaced():
    result = reprice("gsec", 6.28, "2032-07-14", 98.52, "6.5497", "2025-09-04", settlement=dt.date(2025, 9, 5),
                     settlement_source="handbook")
    assert result.status == "not_reproduced" and "handbook settlement date disagrees" in result.note


@pytest.mark.parametrize("maturity, price, printed, cause", [
    ("2031-02-15", 97.92, "6.8779", "2031-02-16"),  # a maturity a day out: the right one is found
    ("2031-02-16", 79.92, "6.8779", "nothing fits"),  # a mistyped price
])
def test_not_reproduced_names_a_cause(maturity, price, printed, cause):
    result = reprice("gsec", 6.36, maturity, price, printed, "2026-05-15", settlement=dt.date(2026, 5, 18),
                     settlement_source="calendar_t1")
    assert result.status == "not_reproduced" and cause in result.note


def test_par_issues_and_odd_first_periods_prove_nothing():
    par = reprice("gsec", 6.94, "2036-05-11", 100.0, "6.94", "2026-05-08", settlement=dt.date(2026, 5, 11))
    assert par.status == "par_issue"
    # 7.16% GS 2050, issued 20 Apr 2020 off its 20 Mar/20 Sep schedule, reissued in its first coupon period
    odd = reprice("gsec", 7.16, "2050-09-20", 109.82, "6.4185", "2020-07-24", settlement=dt.date(2020, 7, 27),
                  settlement_source="calendar_t1", issue_date="2020-04-20")
    assert odd.status == "not_reproduced" and "odd first coupon period" in odd.note
    assert odd.settlement == dt.date(2020, 7, 27)  # no date is inferred there


# --- Build ---------------------------------------------------------------------------------------------

def _gsec_rows():
    """Made-up auctions (yields from the engine) plus typed-in published ones."""
    m1 = fx.priced_yield(7.42, "2019-09-16", 101.40, "2017-02-13")
    m2 = fx.priced_yield(7.42, "2019-09-16", 102.10, "2017-06-12")
    m3 = fx.priced_yield(7.42, "2019-09-16", 100.85, "2018-01-15")
    n1 = fx.priced_yield(7.11, "2021-06-13", 101.20, "2016-08-16")
    s1 = fx.priced_yield(6.57, "2033-08-17", 99.50, "2026-09-14")
    return {
        "2016-2017": [(D(2016, 6, 10), "7.11% GS 2021", 3000, 3000, "100.00", "7.1100"),  # new issue, matured
                      (D(2016, 8, 12), "7.11% GS 2021", 2000, 2000, "101.20", n1),
                      (D(2017, 2, 10), "7.42% GS 2019", 2000, 2000, "101.40", m1),  # never issued in the files
                      (D(2017, 6, 9), "7.42% GS 2019", 2000, 2000, "102.10", m2)],
        "2017-2018": [(D(2018, 1, 12), "7.42% GS 2019", 2000, 2000, "100.85", m3),
                      (D(2018, 3, 22), "8.24% GOVT.STOCK 2018", 15091, 15091, "100.13", "6.4502"),
                      (D(2018, 3, 22), "8.30% GOVT STOCK 2042", 14529.44, 14529.44, "104.00", "7.9270")],
        "2020-2021": [(D(2020, 9, 25), "5.77% GS 2030", 18000, 136.103, "98.23", "6.0095")],
        "2023-2024": [(D(2024, 1, 19), "7.18% GS 2037", 14000, 14000, 99.01, 7.2964)],  # 22 Jan 2024 declared that day
        "2025-2026": [(D(2025, 8, 14), "6.01% GS 2030", 11000, 10988.5, 98.96, 6.2572),
                      (D(2025, 8, 1), "6.68% GS 2040", 16000, 15984.753, 99.82, 6.6985),
                      (D(2025, 9, 4), "6.28% GS 2032", 11000, 10990.0, 98.52, 6.5497)],
        "2026-2027": [(D(2026, 9, 11), "6.57% GS 2033", 12000, 12000, 99.50, float(s1))],
    }


@pytest.fixture(scope="module")
def build(tmp_path_factory, master, cal):
    tmp = tmp_path_factory.mktemp("build")
    gsec = load_fimmda_gsec([fx.gsec_workbook(tmp / "g.xlsx", _gsec_rows())])
    sdl = load_fimmda_sdl([fx.sdl_workbook(tmp / "s.xlsx", {"January": JAN, "October": OCT})])
    (tmp / "hbs.html").write_text(fx.handbook_page(HBS_ROWS[:1] + [
        ("14-Aug-2025", "18-Aug-2025", "11000", "4.93", "200", "30000", "4", "9", "120", "10988", "4", "12", "0",
         "98.96/6.2572", "6.01% GS 2030")]), encoding="utf-8")
    hbs = load_handbook_auctions([tmp / "hbs.html"])
    return build_auctions(fimmda_gsec=gsec, fimmda_sdl=sdl, handbook=hbs, master=master, calendar=cal)


def _row(build, day, key):
    a = build.auctions
    hit = a[(a["auction_date"] == T(day)) & (a["name_key"] == key)]
    assert len(hit) == 1, (day, key)
    return hit.iloc[0]


def test_build_table_shape_and_isin_joins(build):
    a = build.auctions
    assert list(a.columns) == list(AUCTION_COLUMNS)
    assert a["auction_id"].is_unique and (a["fy"].str.match(r"\d{4}-\d{2}")).all()
    r = _row(build, "2024-01-19", "7.18% GS 2037")
    assert (r["isin"], r["isin_match"], r["maturity_source"]) == ("IN0020230077", "matched", "master")
    # two live ISINs share the key 6.57% GS 2033: the yield picks the one maturing on 17 Aug 2033
    s = _row(build, "2026-09-11", "6.57% GS 2033")
    assert (s["isin"], s["isin_match"]) == ("IN0020260066", "matched_by_yield")
    assert set(s["isin_candidates"].split(";")) == {"IN0020260066", "IN0020160100"}
    sdl = a[a["isin"] == "IN1020230679"].iloc[0]
    assert (sdl["isin_match"], sdl["maturity_source"], sdl["accepted_basis"]) == ("printed", "fimmda_sdl", "as_printed")
    assert (sdl["repricing"], sdl["repricing_note"]) == ("not_checked", "no cut-off price or yield")
    assert "IN1020230679" in set(build.master["isin"])  # FIMMDA's SDL ISINs join the master


def test_build_settlements_repricing_and_holiday_rolls(build):
    r = _row(build, "2024-01-19", "7.18% GS 2037")
    assert (r["repricing"], r["settlement_source"], r["settlement_date"]) == (
        "settlement_inferred", "yield_rolled", T("2024-01-23"))
    assert r["settlement_candidates"] == "2024-01-22" and "listed holiday" in r["repricing_note"]
    ok = _row(build, "2025-08-14", "6.01% GS 2030")
    assert (ok["repricing"], ok["settlement_source"], ok["settlement_date"]) == (
        "reproduced", "handbook", T("2025-08-18"))
    assert ok["sources"] == "fimmda_gsec;handbook"
    # FIMMDA's competitive amount plus the Handbook's non-competitive one
    assert (ok["comp_accepted_cr"], ok["nc_accepted_cr"], ok["accepted_cr"], ok["accepted_basis"]) == (
        10988.5, 12.0, 11000.5, "total")
    moved = _row(build, "2025-09-04", "6.28% GS 2032")
    assert (moved["settlement_date"], moved["settlement_source"]) == (T("2025-09-09"), "yield_rolled")


def test_build_maturity_inference(build):
    mats = build.maturities.set_index("key")
    assert mats.loc["7.11% GS 2021", "maturity_source"] == "issue_anniversary"
    assert mats.loc["7.11% GS 2021", "maturity_date"] == T("2021-06-13")  # first issue settled 13 Jun 2016
    assert mats.loc["7.42% GS 2019", "maturity_source"] == "yield_search"
    assert mats.loc["7.42% GS 2019", "maturity_date"] == T("2019-09-16") and mats.loc["7.42% GS 2019", "fits"] == 1
    r = _row(build, "2017-06-09", "7.42% GS 2019")
    assert (r["maturity_date"], r["repricing"], r["issue_type"]) == (T("2019-09-16"), "reproduced", "reissue")
    new = _row(build, "2016-06-10", "7.11% GS 2021")
    assert (new["issue_type"], new["repricing"]) == ("new", "par_issue")


def test_build_flags_special_operations_and_shortfalls(build):
    conv = _row(build, "2018-03-22", "8.30% GS 2042")
    leg = _row(build, "2018-03-22", "8.24% GS 2018")
    assert "non_round_notified" in conv["flags"] and not conv["counts_as_issue"]
    assert "probable_switch_source" in leg["flags"] and not leg["counts_as_issue"]
    short = _row(build, "2020-09-25", "5.77% GS 2030")
    assert "shortfall" in short["flags"] and short["counts_as_issue"] and short["accepted_cr"] == 136.103
    checks = set(build.report["check"])
    assert {"special_operation", "settlement_inferred", "maturity_inference"} <= checks


def test_conversion_legs_do_not_date_a_maturity(build):
    # 8.24% GS 2018's only line is the security converted out on 22 Mar 2018. Its price was fixed for another
    # settlement basis: searched at T+1 it puts the maturity on 23 Apr 2018, a day after the true one.
    mats = build.maturities.set_index("key")
    assert mats.loc["8.24% GS 2018", "status"] == "refused"
    assert "special-operation" in mats.loc["8.24% GS 2018", "detail"]
    leg = _row(build, "2018-03-22", "8.24% GS 2018")
    assert pd.isna(leg["maturity_date"]) and leg["repricing"] == "not_checked"


def test_a_day_priced_for_the_auction_date_is_a_special_operation(tmp_path, master, cal):
    from cr_strat.auctions import yield_at
    day, t0 = D(2023, 3, 16), "2023-03-16"  # a Thursday; its T+1 is Friday 17 Mar
    lines = [("7.38% GS 2027", 7.38, "2027-06-20", 96.00), ("7.26% GS 2029", 7.26, "2029-01-14", 94.00),
             ("7.16% GS 2023", 7.16, "2023-05-20", 100.20)]  # the last is not in the master
    priced = [(name, c, mat, px, fx.priced_yield(c, mat, px, t0)) for name, c, mat, px in lines]
    for name, c, mat, px, y in priced[:2]:  # the yields fit the auction day and no weekday after it
        assert not any(reproduces(yield_at("gsec", c, mat, px, d), y) for d in weekdays_after(day.date(), 4)), name
    normal = fx.priced_yield(7.41, "2036-12-19", 99.00, "2023-03-13")
    sheets = {"2022-2023": [(day, name, 300, 300, f"{px:.2f}", y) for name, _, _, px, y in priced]
              + [(D(2023, 3, 10), "7.41% GS 2036", 13000, 13000, "99.00", normal)]}
    b = build_auctions(fimmda_gsec=load_fimmda_gsec([fx.gsec_workbook(tmp_path / "g.xlsx", sheets)]),
                       master=master, calendar=cal)
    for name, *_ in priced:
        r = _row(b, "2023-03-16", name)
        assert "auction_day_pricing" in r["flags"] and not r["counts_as_issue"], name
    listed = _row(b, "2023-03-16", "7.38% GS 2027")
    assert listed["repricing"] == "not_reproduced" and "fits 2023-03-16" in listed["repricing_note"]
    # the line with no maturity cannot date one: searched at T+1 it would give 21 May 2023
    unlisted = _row(b, "2023-03-16", "7.16% GS 2023")
    assert pd.isna(unlisted["maturity_date"]) and unlisted["repricing"] == "not_checked"
    assert b.maturities.set_index("key").loc["7.16% GS 2023", "status"] == "refused"
    ok = _row(b, "2023-03-10", "7.41% GS 2036")
    assert ok["flags"] == "" and ok["counts_as_issue"] and ok["repricing"] == "reproduced"
    warn = b.report[(b.report["check"] == "special_operation") & (b.report["auction_date"] == T(t0))]
    assert len(warn) == 1 + 3  # the day, then each line
    assert "7.38% GS 2027" in warn.iloc[0]["detail"] and "7.26% GS 2029" in warn.iloc[0]["detail"]


def test_build_from_rbi_release_tables(master, cal):
    import rbi_fixtures as rf
    from cr_strat.rbi_releases import parse_releases

    pages = [
        rf.release_page("Government Stock - Full Auction Results", "Nov 13, 2026",
                        rf.body("", rf.results_table([rf.GOI_6_94])), prid=63970),
        rf.release_page("Treasury Bills: Full Auction Result", "Nov 18, 2026",
                        rf.body("", rf.results_table(rf.TBILL_COLUMNS, tbill=True)), prid=64010),
        rf.release_page("Auction of 91-Day, 182-Day and 364-Day Treasury Bills", "Nov 13, 2026",
                        rf.body("Treasury bills auction.", rf.tbill_announcement_table()), prid=63975),
    ]
    tables = parse_releases(pages).tables
    b = build_auctions(releases=tables, master=master, calendar=cal)
    goi = b.auctions[b.auctions["kind"] == "gsec"].iloc[0]
    assert (goi["isin"], goi["accepted_cr"], goi["accepted_basis"], goi["settlement_date"]) == (
        "IN0020260025", 34000.0, "total", T("2026-11-16"))
    assert (goi["repricing"], goi["cutoff_yield_text"]) == ("reproduced", "7.3203")
    bills = b.auctions[b.auctions["kind"] == "tbill"].set_index("tenor_days")
    # the 18 Nov results take the settlement and maturity dates from that auction's announcement
    assert bills.loc[91, "settlement_date"] == T("2026-11-19") and bills.loc[91, "maturity_date"] == T("2027-02-18")
    assert bills.loc[91, "accepted_cr"] == 7600 + 11600 and bills.loc[91, "settlement_source"] == "announcement"
    assert bills.loc[364, "issue_type"] == "new" and bills.loc[364, "repricing"] == "not_checked"


def test_switches_get_isins_and_a_checked_settlement(cal):
    import rbi_fixtures as rf
    from cr_strat.rbi_releases import parse_releases

    master = build_master(extra=[pd.DataFrame(LISTED, columns=["isin", "name", "issue_date", "maturity_date"])])
    page = rf.release_page("Result: Conversion/Switch Auction of Government of India Securities", "Nov 16, 2026",
                           rf.body("", rf.switch_table(rf.SWITCH_PAIRS), pr_no="2026-2027/1504"), prid=63982)
    sw = normalise_switches(parse_releases([page]).tables, master, cal).set_index("source_security")
    first = sw.loc["7.38% GS 2027"]
    assert (first["source_isin"], first["destination_isin"]) == ("IN0020220037", "IN0020230077")
    assert (first["settlement_date"], first["repricing"]) == (T("2026-11-17"), "reproduced")  # T+1 confirmed
    assert sw.loc["7.26% GS 2029", "repricing"] == "not_checked"  # nothing accepted ('NA')


def test_settlement_evidence_verdicts(build, cal):
    ev = settlement_evidence(build, cal)
    hit = ev[(ev["auction_date"] == T("2025-08-14")) & (ev["method"] == "yield")]
    assert hit["verdict"].tolist() == ["holiday_confirmed"]  # 15 Aug 2025 ruled out, 18 Aug fits
    assert T("2025-08-15") in set(confirmed_holidays(ev)["holiday"])
    # A calendar that missed Independence Day 2025: the yields show 15 Aug was not a settlement day.
    shipped = load_holidays(DEFAULT_HOLIDAYS_CSV)
    days = [d for d in shipped["date"] if d.year == 2025 and d != dt.date(2025, 8, 15)]
    missing = BusinessCalendar("test", days, years=[2025])
    gaps = holiday_discrepancies(settlement_evidence(build.auctions[build.auctions["fy"] == "2025-26"], missing))
    row = gaps[gaps["auction_date"] == T("2025-08-14")].iloc[0]
    assert row["verdict"] == "missed_holiday" and row["calendar_t1"] == T("2025-08-15")
    # The 19 Jan 2024 yields point to 22 Jan, a holiday declared that day: settled_on_holiday, reported.
    jan = holiday_discrepancies(ev)
    assert jan.loc[jan["auction_date"] == T("2024-01-19"), "verdict"].tolist() == ["settled_on_holiday"]


def test_sdl_new_issue_anniversaries_as_evidence(tmp_path, cal, isin):
    rows = [("17-09-2024", isin("IN352024011"), "07.00 CG SGS 2029", "19-09-2029", 7.0, 6.98, "NEW", 7.0, "CG",
             "x", 1000, 7000)]
    sdl = load_fimmda_sdl([fx.sdl_workbook(tmp_path / "s.xlsx", {"September": rows})])
    b = build_auctions(fimmda_sdl=sdl, calendar=cal)
    ev = settlement_evidence(b, cal, fimmda_sdl=sdl)
    # 18 Sep 2024 (Id-E-Milad, moved from 16 Sep) is ruled out: the new issue is dated 19 Sep
    assert ev[["method", "implied_dates", "verdict"]].values.tolist() == [
        ["sdl_anniversary", "2024-09-19", "holiday_confirmed"]]


def test_auction_csv_round_trip(build, tmp_path):
    path = tmp_path / "auctions.csv"
    build.auctions.to_csv(path, index=False, date_format="%Y-%m-%d")
    back = read_auctions(path)
    assert list(back.columns) == list(AUCTION_COLUMNS) and len(back) == len(build.auctions)
    assert back["counts_as_issue"].tolist() == build.auctions["counts_as_issue"].tolist()
    assert back.loc[back["name_key"] == "6.82% GS 2033", "cutoff_yield_text"].tolist() in ([], ["7.0600"])
    assert (back["cutoff_yield_text"] == build.auctions["cutoff_yield_text"].fillna("")).all()
    with pytest.raises(AuctionLoadError, match="not an auction table"):
        pd.DataFrame({"x": [1]}).to_csv(tmp_path / "bad.csv", index=False)
        read_auctions(tmp_path / "bad.csv")


@pytest.mark.parametrize("security, coupon, first_issue, maturity, price, printed", [
    # RBI's SDL result of 9 Nov 2026 (typed-in facts): reissues settle on 11 Nov, after the Diwali holiday
    ("7.83% ASSAM SGS 2046", 7.83, "2026-04-29", "2046-04-29", 97.55, "8.0814"),
    ("8.11% UTTAR PRADESH SGS 2038", 8.11, "2026-10-14", "2038-10-14", 100.91, "7.9889"),
    ("7.92% KERALA SGS 2032", 7.92, "2026-10-14", "2032-10-14", 100.50, "7.8109"),
])
def test_sdl_reissues_reproduce_on_the_issue_anniversary(cal, security, coupon, first_issue, maturity, price, printed):
    settle = cal.settlement_date(dt.date(2026, 11, 9), 1)
    assert settle == dt.date(2026, 11, 11)
    result = reprice("sdl", coupon, maturity, price, printed, "2026-11-09", settlement=settle,
                     settlement_source="calendar_t1", issue_date=first_issue, calendar=cal)
    assert result.status == "reproduced", security


def test_switch_csv_round_trip(tmp_path, cal, master):
    import rbi_fixtures as rf
    from cr_strat.auctions import SWITCH_COLUMNS, read_switches
    from cr_strat.rbi_releases import parse_releases

    page = rf.release_page("Result: Conversion/Switch Auction of Government of India Securities", "Nov 16, 2026",
                           rf.body("", rf.switch_table(rf.SWITCH_PAIRS), pr_no="2026-2027/1504"), prid=63982)
    sw = normalise_switches(parse_releases([page]).tables, master, cal)
    sw.to_csv(tmp_path / "switches.csv", index=False, date_format="%Y-%m-%d")
    back = read_switches(tmp_path / "switches.csv")
    assert list(back.columns) == list(SWITCH_COLUMNS) and back["destination_issued_cr"].tolist() == \
        sw["destination_issued_cr"].tolist()
    assert back["settlement_date"].tolist() == [T("2026-11-17")] * 3


def test_a_new_issue_result_merges_with_fimmda_coupon_row(tmp_path, master, cal):
    import rbi_fixtures as rf
    from cr_strat.issuance import build_ledger
    from cr_strat.rbi_releases import parse_releases

    new = dict(rf.GOI_6_94, name="New GS 2036", price="100.00", ytm="6.8500")  # RBI names a new issue by year
    tables = parse_releases([rf.release_page("Government Stock - Full Auction Results", "Nov 13, 2026",
                                             rf.body("", rf.results_table([new])), prid=63970)]).tables
    gsec = load_fimmda_gsec([fx.gsec_workbook(tmp_path / "g.xlsx", {"2026-2027": [
        (D(2026, 11, 13), "6.85% GS 2036", 34000, 33947.718, 100, 6.85)]})])
    b = build_auctions(fimmda_gsec=gsec, releases=tables, calendar=cal)
    assert len(b.auctions) == 1
    row = b.auctions.iloc[0]
    assert (row["name_key"], row["sources"], row["accepted_cr"]) == ("6.85% GS 2036", "rbi_result;fimmda_gsec", 34000)
    led = build_ledger(b.auctions, calendar=cal)
    assert led.loc[led["event"].isin(["issue", "reissue"]), "amount_cr"].sum() == 34000


def _announcements(rows):
    return pd.DataFrame([dict(release_id=1, auction_date=T("2026-12-04"), settlement_date=T("2026-12-07"),
                              security=s, name_key=None, repayment_date=T(r), notified_cr=n, greenshoe_cr=2000.0)
                         for s, r, n in rows])


def _results(rows):
    return pd.DataFrame([dict(release_id=2, line=i, auction_date=T("2026-12-04"), security=s, name_key=None,
                              notified_cr=n, comp_accepted_cr=0.95 * n, nc_accepted_cr=0.05 * n, total_accepted_cr=n,
                              cutoff_price=100.0, cutoff_yield=y, status="accepted", source_file="r.html",
                              devolvement_cr=None) for i, (s, n, y) in enumerate(rows, start=1)])


def test_new_issues_announced_the_same_day_keep_their_own_maturities(cal):
    ann = _announcements([("New GS 2036", "2036-12-07", 20000.0), ("New GS 2066", "2066-12-07", 10000.0)])
    res = _results([("New GS 2036", 20000.0, 6.85), ("New GS 2066", 10000.0, 7.40)])
    a = build_auctions(releases={"gsec_announcement": ann, "gsec_result": res}, calendar=cal).auctions
    got = a.set_index("name_key")[["maturity_date", "maturity_source", "settlement_date"]]
    assert got.loc["6.85% GS 2036"].tolist() == [T("2036-12-07"), "announcement", T("2026-12-07")]
    assert got.loc["7.40% GS 2066"].tolist() == [T("2066-12-07"), "announcement", T("2026-12-07")]
    # a result printed with its coupon still finds the 'New GS 2036' announcement, by its maturity year
    coupon = _results([("6.85% GS 2036", 20000.0, 6.85)])
    b = build_auctions(releases={"gsec_announcement": ann, "gsec_result": coupon}, calendar=cal).auctions
    assert b.iloc[0]["maturity_date"] == T("2036-12-07")
    # two lines one key cannot tell apart: neither joins, and the report says why
    clash = _announcements([("New GS 2036", "2036-12-07", 20000.0), ("NEW GS 2036", "2036-06-07", 5000.0)])
    c = build_auctions(releases={"gsec_announcement": clash, "gsec_result": res.iloc[:1]}, calendar=cal)
    joined = c.auctions.iloc[0]
    assert joined["maturity_source"] != "announcement" and joined["settlement_source"] != "announcement"
    assert c.report.loc[c.report["check"] == "announcement_join", "detail"].str.contains("neither joins").all()
    # the same line read twice (two copies of an announcement) is one line, blank dates included
    twice = pd.concat([ann, ann], ignore_index=True)
    sdl = pd.DataFrame([dict(auction_date=T("2026-12-01"), settlement_date=pd.NaT, state_code="KA", issue_type="new",
                             tenor_years=10, name_key=None, security=None)] * 2)
    d = build_auctions(releases={"gsec_announcement": twice, "gsec_result": res, "sdl_announcement": sdl},
                       calendar=cal)
    assert d.auctions["maturity_source"].eq("announcement").all()
    assert "announcement_join" not in set(d.report["check"])


def test_a_more_precise_total_is_not_replaced_by_rounded_parts(tmp_path, cal):
    gsec = load_fimmda_gsec([fx.gsec_workbook(tmp_path / "g.xlsx", {"2023-2024": [
        (D(2023, 8, 4), "7.18% GS 2033", 11000, 10978.452, 100.25, 7.1450)]})])  # made-up amounts
    hbs = load_handbook_auctions([fx.handbook_page([
        ("04-Aug-2023", "07-Aug-2023", "11000", "10", "300", "30000", "20", "530", "150", "10450", "18", "528", "0",
         "100.25/7.1450", "7.18% GS 2033")], fy="2023-24")])
    row = build_auctions(fimmda_gsec=gsec, handbook=hbs, calendar=cal).auctions.iloc[0]
    assert row["sources"] == "fimmda_gsec;handbook"
    assert (row["accepted_cr"], row["accepted_basis"], row["comp_accepted_cr"], row["nc_accepted_cr"]) == (
        10978.452, "total", 10450.0, 528.0)  # FIMMDA's exact total; the Handbook's split kept
    off = load_handbook_auctions([fx.handbook_page([
        ("04-Aug-2023", "07-Aug-2023", "11000", "10", "300", "30000", "20", "530", "150", "10400", "18", "528", "0",
         "100.25/7.1450", "7.18% GS 2033")], fy="2023-24")])
    clash = build_auctions(fimmda_gsec=gsec, handbook=off, calendar=cal)
    assert clash.conflicts["field"].tolist() == ["total_accepted_cr"]  # 10,928 against 10,978.452


def test_name_keys_join_only_isins_live_on_the_auction_date(tmp_path, cal):
    one = build_master(extra=[pd.DataFrame({"isin": ["IN0020220151"], "name": ["7.26% GS 2033"],
                                            "issue_date": ["2023-02-06"], "maturity_date": ["2033-02-06"]})])
    gsec = load_fimmda_gsec([fx.gsec_workbook(tmp_path / "g.xlsx", {
        "2018-2019": [(D(2019, 1, 4), "7.26% GS 2033", 2000, 2000, "100.00", "7.2600")],
        "2023-2024": [(D(2023, 4, 6), "7.26% GS 2033", 2000, 2000, "100.50", "7.1890"),
                      (D(2016, 11, 4), "GOI FLOATING RATE BOND 2024", 3000, 3000, "96.40", "6.5100")]})])
    b = build_auctions(fimmda_gsec=gsec, master=one, calendar=cal)
    a = b.auctions.set_index("auction_date")
    assert a.loc[T("2019-01-04"), "isin_match"] == "none" and pd.isna(a.loc[T("2019-01-04"), "isin"])
    assert a.loc[T("2023-04-06"), "isin"] == "IN0020220151"
    assert b.report["detail"].str.contains("IN0020220151 .first issued 2023-02-06.*not live on the auction date",
                                           regex=True).any()
    # a floating-rate bond missing from the master: no yield to date it, and the report says so
    frb = b.maturities.set_index("key").loc["FRB 2024"]
    assert frb["status"] == "refused" and "floating rate" in frb["detail"]
