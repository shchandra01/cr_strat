import copy
import datetime as dt
import re

import pandas as pd
import pytest
import rbi_fixtures as fx

from cr_strat import rbi_releases as rr
from cr_strat.htmlparse import BlockedPageError, parse_count, parse_date, parse_html, parse_number

D = dt.date
T = pd.Timestamp


def _page(title, date_line, narrative, *tables, prid=63970, pr_no="2026-2027/1492", after=""):
    return fx.release_page(title, date_line, fx.body(narrative, *tables, pr_no=pr_no, after=after), prid=prid)


def _goi_page(columns=(fx.GOI_6_94,), prid=63970, **kw):
    return _page("Government Stock - Full Auction Results", "Nov 13, 2026", "",
                 fx.results_table(list(columns), **kw), prid=prid)


def _parse(*sources, **kw):
    return rr.parse_releases(list(sources), **kw)


def _only_reason(batch):
    assert len(batch.skipped) == 1, batch.skipped
    return batch.skipped[0].reason


# --- HTML extraction --------------------------------------------------------------------------------

def test_nested_tables_keep_only_direct_rows():
    page = parse_html('<table id="o"><tr><td>a</td><td>b <table><tr><td>c</td></tr><tr><td>d</td></tr></table>'
                      "</td></tr><tr><td colspan=2>e</td></tr></table>")
    outer, inner = page.tables
    assert outer.cell_texts() == [["a", "b c d"], ["e"]]
    assert inner.cell_texts() == [["c"], ["d"]]
    assert (outer.depth, inner.depth, inner.parent, outer.children) == (1, 2, 0, (1,))
    assert not outer.is_leaf and inner.is_leaf and page.leaf_tables() == [inner]
    assert outer.text == "a b c d e" and page.descendants(outer) == [inner]


def test_grid_expands_colspan_and_rowspan():
    table = parse_html("<table><tr><td rowspan=2>A</td><td colspan=2>B</td></tr><tr><td>c</td><td>d</td></tr>"
                       "<tr><td>e</td></tr></table>").tables[0]
    assert table.grid() == [["A", "B", "B"], ["A", "c", "d"], ["e", "", ""]]
    assert table.grid(fill=False) == [["A", "B", ""], ["", "c", "d"], ["e", "", ""]]
    assert [len(r) for r in table.rows] == [2, 2, 1]


def test_text_normalisation_br_nbsp_scripts_comments():
    page = parse_html("<html><head><title>T&nbsp;1</title><style>p{}</style></head><body>"
                      "<p>Amount&nbsp;in&#8377;<br>Crore​</p><script>var t='<table>';</script>"
                      "<!-- <a href='old.xlsx'>old</a> --><a href='new.xlsx'>new</a></body></html>")
    assert page.title == "T 1" and page.text == "Amount in₹ Crore new" and not page.tables
    assert page.links == (("new", "new.xlsx"),)


def test_unclosed_cells_and_stray_end_tags():
    page = parse_html("<table><tr><td>a<td>b<tr><td>c</span></td></font><td>d</table><p>x")
    assert page.tables[0].cell_texts() == [["a", "b"], ["c", "d"]]
    # a cell opened inside a nested table never closes the outer table's cell
    page = parse_html("<table><tr><td>a<table><tr><td>b<td>c</table>d<td>e</table>")
    assert page.tables[0].cell_texts() == [["a b c d", "e"]] and page.tables[1].cell_texts() == [["b", "c"]]


def test_tbody_thead_rows_are_direct_rows():
    page = parse_html("<table><thead><tr><th>h</th></tr></thead><tbody><tr><td>1</td></tr></tbody></table>")
    rows = page.tables[0].rows
    assert [c.text for r in rows for c in r] == ["h", "1"] and rows[0][0].header and not rows[1][0].header


@pytest.mark.parametrize("text, value", [
    ("73,016.600", 73016.6), ("1,01,908.561", 101908.561), ("18.3591%", 18.3591), ("₹6,000", 6000.0),
    ("Rs. 5,000", 5000.0), ("NIL", 0.0), ("Nil", 0.0), ("-", None), ("NA", None), ("", None), ("\xa0", None),
    ("#ERROR", None), (" 7.4578", 7.4578), ("6.9888\t", 6.9888), (12, 12.0), ("₹ 6000", 6000.0),
    ("7.25 %", 7.25), ("12,34,567", 1234567.0), ("12,891,490.175", 12891490.175), ("1,00,00,000", 1e7),
])
def test_parse_number(text, value):
    assert parse_number(text) == value


@pytest.mark.parametrize("text", ["(34 bids)", "98.51/7.3819", "abc", "1.2.3", "12 crore",
                                  # two numbers in one cell, or commas out of place: never one number
                                  "100 200", "1 234", "1,455 82,868.2", "1,2,3", "12,34", "1,0000", "123,45,678"])
def test_parse_number_refuses_text(text):
    with pytest.raises(ValueError, match="not a number"):
        parse_number(text)


def test_parse_count_and_date():
    assert parse_count("1,455") == 1455 and parse_count("-") is None
    with pytest.raises(ValueError, match="not a count"):
        parse_count("1.5")
    assert parse_date("November 20, 2026 (Friday)", ["%B %d, %Y"]) == D(2026, 11, 20)
    assert parse_date("8-Aug-2003", ["%d-%b-%Y"]) == D(2003, 8, 8)
    with pytest.raises(ValueError, match="not a date"):
        parse_date("2026-11-20", ["%B %d, %Y"])


# --- Reading pages and feeds ------------------------------------------------------------------------

def test_read_release_page_frame():
    release = rr.read_release_page(_goi_page())
    assert (release.release_id, release.release_date, release.title) == (
        63970, D(2026, 11, 13), "Government Stock - Full Auction Results")
    assert release.press_release_no == "2026-2027/1492" and release.published is None
    assert release.pdf_url == "https://rbidocs.example/PR1.PDF"
    assert [t.is_leaf for t in release.body_tables] == [False, True] and len(release.data_tables) == 1
    assert release.link.endswith("prid=63970")


def test_release_id_from_file_name_or_refused(tmp_path):
    path = tmp_path / "goi_full_63971.html"
    path.write_text(_goi_page(prid=None), encoding="utf-8")
    assert rr.read_release_page(path).release_id == 63971
    with pytest.raises(rr.ReleaseParseError, match="no release id"):
        rr.read_release_page(_goi_page(prid=None))
    assert rr.read_release_page(_goi_page(prid=None), release_id=5).release_id == 5


def test_press_release_number_running_into_annex():
    html = _page("Auction of Government of India Dated Securities", "Nov 16, 2026", fx.GSEC_ANNOUNCEMENT_TEXT,
                 fx.gsec_announcement_table(), prid=63983, pr_no="2026-2027/1505", after="ANNEX")
    html = html.replace("2026-2027/1505</p>ANNEX", "2026-2027/1505ANNEX</p>")
    assert rr.read_release_page(html).press_release_no == "2026-2027/1505"


def test_blocked_and_frameless_pages():
    with pytest.raises(BlockedPageError, match="Support ID 123456789") as info:
        rr.read_release_page(fx.blocked_page())
    assert info.value.support_id == "123456789"
    with pytest.raises(rr.ReleaseParseError, match="no press-release frame"):
        rr.read_release_page("<html><body><table><tr><td>x</td></tr></table></body></html>")


def _switch_item(prid=63982, pub="Mon, 16 Nov 2026 14:55:00"):
    return dict(title="Result: Conversion/Switch Auction of Government of India Securities", prid=prid, pub=pub,
                html=fx.body("", fx.switch_table(fx.SWITCH_PAIRS), pr_no="2026-2027/1504"))


def test_read_rss_feed_with_bom():
    feed = rr.read_rss_feed(fx.feed([_switch_item(), _switch_item(63984, "Mon, 16 Nov 2026 13:55:00 +0000")]))
    assert feed.title == "PRESS RELEASES FROM RBI" and len(feed.items) == 2
    first, second = feed.items
    assert (first.index, first.release_id, first.published) == (1, 63982, dt.datetime(2026, 11, 16, 14, 55))
    assert second.published == dt.datetime(2026, 11, 16, 19, 25)  # a stated zone is converted to India time
    release = rr.feed_releases(feed)[0]
    assert release.release_date == D(2026, 11, 16) and release.press_release_no == "2026-2027/1504"
    assert release.source_file == "<bytes>#1" and len(release.data_tables) == 1


def test_feed_errors():
    with pytest.raises(ValueError, match="not an RSS 2.0 feed"):
        rr.read_rss_feed(b"<feed><entry/></feed>")
    with pytest.raises(ValueError, match="not well-formed"):
        rr.read_rss_feed(b"<rss><channel>")
    with pytest.raises(ValueError, match="entity"):
        rr.read_rss_feed(b'<!DOCTYPE r [<!ENTITY a "x">]><rss version="2.0"><channel/></rss>')


@pytest.mark.parametrize("title, kind", [
    ("Government Stock - Full Auction Results", "gsec_result"),
    ("Treasury Bills: Full Auction Result", "tbill_result"),
    ("State Government Securities - Full Auction Result", "sdl_result"),
    ("Result of Underwriting Auction conducted on November 13, 2026", "underwriting_result"),
    ("Result: Conversion/Switch Auction of Government of India Securities", "switch_result"),
    ("Auction of Government of India Dated Securities", "gsec_announcement"),
    ("Auction of 91-Day, 182-Day and 364-Day Treasury Bills", "tbill_announcement"),
    ("Auction of State Government Securities", "sdl_announcement"),
    ("Money Market Operations as on November 13, 2026", "money_market_operations"),
    ("RBI to conduct 2-day Variable Rate Reverse Repo (VRRR) auction under LAF on November 17, 2026",
     "laf_auction_announcement"),
    ("Result of the 3-day Variable Rate Reverse Repo (VRRR) auction held on November 13, 2026", "laf_auction_result"),
    ("Auction of Government of India Dated Security", "gsec_announcement"),  # one security on offer: the singular
    ("Detailed Result: OMO Purchase Auction held on Mar 13, 2026 and Settlement on Mar 16, 2026", "omo_result"),
    ("Issuance Calendar for Marketable Dated Securities for October 2026 - March 2027", "gsec_calendar"),
    ("RBI cancels Certificate of Registration of 49 NBFCs", "other"),
])
def test_classify_title(title, kind):
    assert rr.classify_title(title) == kind


# --- GoI and T-bill results -------------------------------------------------------------------------

GOI_VECTOR = dict(  # spec test vector, 6.94% GS 2036
    auction_date=T("2026-11-13"), security="6.94% GS 2036", name_key="6.94% GS 2036", notified_cr=34000.0,
    comp_received_count=349, comp_received_cr=73016.6, cutoff_price=97.43, cutoff_yield=7.3203,
    comp_accepted_count=146, comp_accepted_cr=33947.718, partial_allotment_pct=18.3591, partial_allotment_count=34,
    wap=97.46, way=7.3158, nc_received_count=8, nc_received_cr=52.282, nc_accepted_count=8, nc_accepted_cr=52.282,
    uw_notified_cr=34000.0, uw_accepted_from_pds_cr=34000.0, devolvement_cr=0.0, status="accepted",
    release_id=63970, release_date=T("2026-11-13"), press_release_no="2026-2027/1492", line=1)


def _check(row, expected):
    for key, value in expected.items():
        if isinstance(value, float):
            assert row[key] == pytest.approx(value), key
        else:
            assert row[key] == value, key


def test_gsec_result_spec_vector():
    batch = _parse(_goi_page())
    df = batch.tables["gsec_result"]
    assert list(df.columns) == list(rr.COLUMNS["gsec_result"]) and len(df) == 1 and not batch.skipped
    row = df.iloc[0]
    _check(row, GOI_VECTOR)
    assert pd.isna(row["nc_partial_pct"]) and pd.isna(row["greenshoe_cr"]) and pd.isna(row["published"])
    assert row["total_accepted_cr"] == pytest.approx(34000.0) and row["source_file"] == "<text 1>"
    assert batch.releases.iloc[0]["status"] == "parsed"


def test_gsec_result_two_securities_and_feed_copy():
    page = _goi_page([fx.GOI_6_94, fx.GOI_SECOND])
    df = _parse(page).tables["gsec_result"]
    assert df["security"].tolist() == ["6.94% GS 2036", "7.71% GS 2066"] and df["line"].tolist() == [1, 2]
    _check(df.iloc[0], {k: v for k, v in GOI_VECTOR.items() if k not in ("line",)})
    assert df.iloc[1]["partial_allotment_count"] == 1 and df.iloc[1]["comp_accepted_cr"] == pytest.approx(13950)
    item = dict(title="Government Stock - Full Auction Results", prid=63970, pub="Fri, 13 Nov 2026 18:10:00",
                html=fx.body("", fx.results_table([fx.GOI_6_94, fx.GOI_SECOND])))
    from_feed = _parse(fx.feed([item])).tables["gsec_result"]
    same = [c for c in df.columns if c not in ("published", "source_file")]
    pd.testing.assert_frame_equal(df[same], from_feed[same])
    assert from_feed["published"].iloc[0] == T("2026-11-13 18:10")


def test_tbill_result_spec_vectors():
    page = _page("Treasury Bills: Full Auction Result", "Nov 11, 2026", "",
                 fx.results_table(fx.TBILL_COLUMNS, tbill=True), prid=63956)
    batch = _parse(page)
    df = batch.tables["tbill_result"]
    assert df["tenor_days"].tolist() == [91, 182, 364] and df["name_key"].tolist() == ["91DTB", "182DTB", "364DTB"]
    _check(df.iloc[0], dict(
        auction_date=T("2026-11-11"), notified_cr=8000.0, comp_received_count=132, comp_received_cr=27046.8,
        cutoff_price=98.5581, cutoff_yield=5.8681, comp_accepted_count=28, comp_accepted_cr=7600.0,
        partial_allotment_pct=48.17, partial_allotment_count=2, wap=98.5602, way=5.8594, nc_received_count=9,
        nc_received_cr=11614.937, nc_accepted_count=9, nc_accepted_cr=11600.0, nc_partial_pct=96.4002))
    _check(df.iloc[1], dict(partial_allotment_count=1, cutoff_price=96.8521, cutoff_yield=6.5183,
                            nc_partial_pct=95.3484, nc_accepted_cr=1000.0))
    _check(df.iloc[2], dict(notified_cr=7000.0, cutoff_price=93.79, cutoff_yield=6.6394, wap=93.8091,
                            way=6.6176, comp_accepted_cr=6650.0, nc_partial_pct=78.7862))
    # non-competitive bids sit outside the notified amount: 11,600 + 7,600 against 8,000
    assert df.iloc[0]["total_accepted_cr"] == pytest.approx(19200.0)
    assert batch.releases.iloc[0]["notes"] == ""  # yields follow from the prices on the tenor


def test_price_and_yield_in_one_cell():
    html = _goi_page().replace('<td align="right">97.43</td>', '<td align="right">97.43<br>(YTM: 7.3203%)</td>')
    html = html.replace('<td align="right">(YTM: 7.3203%)</td>', '<td align="right"></td>')
    row = _parse(html).tables["gsec_result"].iloc[0]
    assert (row["cutoff_price"], row["cutoff_yield"]) == (97.43, 7.3203)


def test_gsec_and_tbill_columns_are_not_confused():
    tbill_as_gsec = _page("Government Stock - Full Auction Results", "Nov 11, 2026", "",
                          fx.results_table(fx.TBILL_COLUMNS, tbill=True), prid=63956)
    assert "is not a G-sec name" in _only_reason(_parse(tbill_as_gsec))


# --- SDL results ------------------------------------------------------------------------------------

def _sdl_page(columns=fx.SDL_COLUMNS, total=None, prid=63950, held="November 09, 2026", **kw):
    narrative = f"The result of the auction of State Government Securities held on {held} is as under:"
    return _page("State Government Securities - Full Auction Result", "Nov 09, 2026", narrative,
                 *fx.sdl_tables(list(columns), total=total, **kw), prid=prid, pr_no="2026-2027/1472")


def test_sdl_result_spec_vectors():
    batch = _parse(_sdl_page(per_table=2))
    df = batch.tables["sdl_result"].set_index("security")
    assert not batch.skipped and len(df) == 4  # 'Total' is a check, not a row
    _check(df.loc["ASSAM SGS 2046"], dict(
        auction_date=T("2026-11-09"), state_code="AS", maturity_year=2046, issue_type="reissue", coupon_pct=7.83,
        first_issue_date=T("2026-04-29"), name_key="7.83% AS SGS 2046", notified_cr=1000.0, comp_received_count=69,
        comp_received_cr=2295.0, cutoff_yield=8.0814, cutoff_price=97.55, comp_accepted_count=29,
        comp_accepted_cr=989.94, partial_allotment_pct=69.7457, partial_allotment_count=2, nc_received_count=3,
        nc_received_cr=10.06, nc_price=97.67, nc_accepted_count=3, nc_accepted_cr=10.06, way=8.0688,
        total_accepted_cr=1000.0, status="accepted"))
    bihar = df.loc["BIHAR SGS 2044*"]
    _check(bihar, dict(footnote="*", state_code="BR", issue_type="new", tenor_years=18, coupon_pct=8.10,
                       name_key="8.10% BR SGS 2044", cutoff_price=100.0, comp_accepted_cr=1385.0, nc_price=100.30,
                       total_accepted_cr=1533.882))
    assert pd.isna(bihar["partial_allotment_pct"]) and pd.isna(bihar["partial_allotment_count"])
    assert bihar["footnote_text"].startswith("Bihar has accepted a partial amount")
    chh = df.loc["CHHATTISGARH SGS 2036#"]
    _check(chh, dict(footnote="#", state_code="CG", coupon_pct=7.68, notified_cr=900.0, comp_received_count=73,
                     comp_received_cr=3980.0, nc_received_count=4, nc_received_cr=11.028, total_accepted_cr=0.0,
                     status="rejected"))
    for col in ("cutoff_yield", "cutoff_price", "comp_accepted_cr", "nc_price", "nc_accepted_cr", "way"):
        assert pd.isna(chh[col]), col
    up = df.loc["UTTAR PRADESH SGS 2038"]  # the reissue cell spells UTTARPRADESH
    _check(up, dict(state_code="UP", coupon_pct=8.11, first_issue_date=T("2026-10-14"), cutoff_yield=7.9889,
                    cutoff_price=100.91, partial_allotment_pct=9.2945, way=7.9485, total_accepted_cr=1500.0))


def test_sdl_total_column_must_add_up():
    total = fx.sdl_total(fx.SDL_COLUMNS)
    total["ca_amt"] = "9999.000"
    assert "comp_accepted_cr sums to" in _only_reason(_parse(_sdl_page(total=total)))


def test_sdl_row_missing_from_one_table_is_a_failed_sum_not_an_error():
    page = _sdl_page(per_table=2)
    row = "<tr><td>(i) No.</td><td>3</td><td>8</td></tr>"  # the first table's non-competitive bids: received, accepted
    assert page.count(row) == 2
    reason = _only_reason(_parse(page.replace(row, "", 1)))  # without the row of the bids received
    assert reason == ("failed check: <text 1>: nc_received_count sums to 15 over the securities but the Total "
                      "column says 26")


def test_sdl_parts_must_make_the_total():
    columns = copy.deepcopy(fx.SDL_COLUMNS)
    columns[0]["total"] = "1100"
    assert "is not the total allotment" in _only_reason(_parse(_sdl_page(columns, total=False)))


def test_sdl_reissue_cell_must_match_its_column():
    columns = copy.deepcopy(fx.SDL_COLUMNS)
    columns[0]["tenor"] = "Re-issue of 7.83% BIHAR SGS 2046 issued on 29-APR-2026"
    assert "tenor cell names another security" in _only_reason(_parse(_sdl_page(columns)))


def _sdl_column(name, tenor):
    return dict(fx.SDL_ASSAM, name=name, tenor=tenor)


def test_sdl_reissue_cell_may_spell_the_state_another_way():
    # RBI's result of 17 Nov 2026: a shorter state name in the reissue cell than in the column heading
    columns = [_sdl_column("ANDHRA PRADESH SGS 2051", "Re-issue of 7.68% ANDHRA SGS 2051 issued on 29-JUL-2026"),
               _sdl_column("MADHYA PRADESH SGS 2030", "Re-issue of 7.77% MADHYAPRADESH SGS 2030 issued on 21-OCT-2026"),
               _sdl_column("WEST BENGAL SGS 2031", "Re-issue of 7.07% WESTBENGAL SGS 2031 issued on 08-JUL-2026")]
    batch = _parse(_sdl_page(columns, footnote=""))
    df = batch.tables["sdl_result"]
    assert not batch.skipped and batch.releases.iloc[0]["notes"] == ""
    assert df["state_code"].tolist() == ["AP", "MP", "WB"] and df["issue_type"].eq("reissue").all()
    assert df["name_key"].tolist() == ["7.68% AP SGS 2051", "7.77% MP SGS 2030", "7.07% WB SGS 2031"]
    assert df["coupon_pct"].tolist() == [7.68, 7.77, 7.07] and df["maturity_year"].tolist() == [2051, 2030, 2031]
    assert df["first_issue_date"].dt.strftime("%Y-%m-%d").tolist() == ["2026-07-29", "2026-10-21", "2026-07-08"]


@pytest.mark.parametrize("name, tenor, message", [
    # another state, though its name begins alike
    ("ANDHRA PRADESH SGS 2051", "Re-issue of 7.68% ARUNACHAL PRADESH SGS 2051 issued on 29-JUL-2026",
     "[ANDHRA PRADESH SGS 2051]: tenor cell names another security: its state is AR, the column's AP"),
    ("MADHYA PRADESH SGS 2030", "Re-issue of 7.77% MAHARASHTRA SGS 2030 issued on 21-OCT-2026",
     "[MADHYA PRADESH SGS 2030]: tenor cell names another security: its state is MH, the column's MP"),
    # the same state, another maturity year
    ("ANDHRA PRADESH SGS 2051", "Re-issue of 7.68% ANDHRA SGS 2052 issued on 29-JUL-2026",
     "[ANDHRA PRADESH SGS 2051]: tenor cell names another security: it matures in 2052, the column's in 2051"),
    ("ANDHRA PRADESH SGS 2051", "Re-issue of 7.68% ANDHRA PRADESH SGS 2015 issued on 29-JUL-2026",
     "tenor cell names another security: it matures in 2015, the column's in 2051"),
    # a short form the state table does not list is not matched by resemblance
    ("ANDHRA PRADESH SGS 2051", "Re-issue of 7.68% ANDHRA PR SGS 2051 issued on 29-JUL-2026",
     "cannot tell whether the tenor cell names the column's security: state 'ANDHRA PR' has no code"),
    ("UTTAR PRADESH SGS 2038", "Re-issue of 8.11% UTTAR SGS 2038 issued on 14-OCT-2026",
     "cannot tell whether the tenor cell names the column's security: state 'UTTAR' has no code"),
    ("LADAKH SGS 2040", "Re-issue of 7.50% ASSAM SGS 2040 issued on 29-JUL-2026",
     "cannot tell whether the tenor cell names the column's security: state 'LADAKH' has no code"),
    # two states without a code are one state only when spelt alike
    ("LADAKH SGS 2040", "Re-issue of 7.50% LAKSHADWEEP SGS 2040 issued on 29-JUL-2026",
     "tenor cell names another security: its state is 'LAKSHADWEEP', and neither state has a code"),
])
def test_sdl_reissue_cell_naming_another_security_is_refused(name, tenor, message):
    reason = _only_reason(_parse(_sdl_page([_sdl_column(name, tenor), fx.SDL_BIHAR])))
    assert reason.startswith("cannot parse: <text 1> [") and message in reason and repr(tenor) in reason


def test_sdl_state_without_a_code_is_kept_when_both_cells_spell_it_alike():
    column = _sdl_column("LADAKH SGS 2040", "Re-issue of 7.50% Ladakh SGS 2040 issued on 29-JUL-2026")
    batch = _parse(_sdl_page([column]))
    row = batch.tables["sdl_result"].iloc[0]
    assert pd.isna(row["state_code"]) and pd.isna(row["name_key"]) and row["coupon_pct"] == 7.5
    assert "state 'LADAKH' has no code" in batch.releases.iloc[0]["notes"]


def test_sdl_without_total_or_date_notes_it():
    batch = _parse(_sdl_page(total=False, held="the date"))
    notes = batch.releases.iloc[0]["notes"]
    assert "no Total column" in notes and "auction date taken as the release date" in notes
    assert batch.tables["sdl_result"]["auction_date"].iloc[0] == T("2026-11-09")


# --- Underwriting and switch ------------------------------------------------------------------------

def test_underwriting_result_spec_vector():
    html = _page("Result of Underwriting Auction conducted on November 13, 2026", "Nov 13, 2026",
                 "In the underwriting auction conducted on November 13, 2026, the cut-off rates are:",
                 fx.uw_table([("6.94% GS 2036", "34,000", "17,010", "16,990", "34,000", "0.54")]), prid=63967)
    df = _parse(html).tables["underwriting_result"]
    assert len(df) == 1  # the note row is no security
    _check(df.iloc[0], dict(auction_date=T("2026-11-13"), security="6.94% GS 2036", notified_cr=34000.0,
                            muc_cr=17010.0, acu_accepted_cr=16990.0, total_underwritten_cr=34000.0,
                            acu_commission_paise=0.54))
    bad = html.replace("<td>16,990</td>", "<td>16,000</td>")
    assert "MUC plus ACU is not the total" in _only_reason(_parse(bad))


SWITCH_VECTORS = [
    dict(source_security="7.38% GS 2027", destination_security="7.18% GS 2037", notified_cr=5000.0,
         offers_received_count=6, source_offered_cr=4408.732, offers_accepted_count=4, source_accepted_cr=3898.732,
         destination_issued_cr=3960.932, dest_cutoff_price=98.51, dest_cutoff_yield=7.3819),
    dict(source_security="7.26% GS 2029", destination_security="7.73% GS 2034", notified_cr=4000.0,
         offers_received_count=1, source_offered_cr=1.0, offers_accepted_count=0, source_accepted_cr=0.0,
         destination_issued_cr=0.0),
    dict(source_security="7.04% GS 2029", destination_security="7.41% GS 2036", notified_cr=5000.0,
         offers_received_count=7, source_offered_cr=3658.204, offers_accepted_count=7, source_accepted_cr=3658.204,
         destination_issued_cr=3657.111, dest_cutoff_price=100.28, dest_cutoff_yield=7.3688),
]


def test_switch_result_page_and_feed_are_one_code_path():
    page = _page("Result: Conversion/Switch Auction of Government of India Securities", "Nov 16, 2026", "",
                 fx.switch_table(fx.SWITCH_PAIRS), prid=63982, pr_no="2026-2027/1504")
    batch = _parse(page, fx.feed([_switch_item()]))
    df = batch.tables["switch_result"]
    assert len(df) == 3 and df["auction_date"].eq(T("2026-11-16")).all()
    for (_, row), expected in zip(df.iterrows(), SWITCH_VECTORS):
        _check(row, expected)
    assert pd.isna(df.iloc[1]["dest_cutoff_price"]) and pd.isna(df.iloc[1]["dest_cutoff_yield"])  # 'NA'
    assert [s.reason for s in batch.skipped] == ["duplicate of release 63982 read from <text 1>"]
    assert batch.releases["status"].tolist() == ["parsed", "duplicate"]
    feed_only = _parse(fx.feed([_switch_item()])).tables["switch_result"]
    same = [c for c in df.columns if c not in ("published", "source_file")]
    pd.testing.assert_frame_equal(df[same], feed_only[same])


@pytest.mark.parametrize("pair, field, value, message", [
    (0, "accepted", "4,500.000", "source_accepted_cr 4500 is above source_offered_cr"),
    (1, "issued", "5.000", "destination issued 5"),
    (0, "cutoff", "98.51", "is not 'price/yield' or NA"),
    (2, "cutoff", "10.28/7.3688", "dest_cutoff_price 10.28 is outside the price range"),
])
def test_switch_checks(pair, field, value, message):
    pairs = copy.deepcopy(fx.SWITCH_PAIRS)
    pairs[pair][field] = value
    page = _page("Result: Conversion/Switch Auction of Government of India Securities", "Nov 16, 2026", "",
                 fx.switch_table(pairs), prid=63982)
    assert message in _only_reason(_parse(page))


# --- Announcements ----------------------------------------------------------------------------------

def _gsec_announcement(total="33,000"):
    return _page("Auction of Government of India Dated Securities", "Nov 16, 2026", fx.GSEC_ANNOUNCEMENT_TEXT,
                 fx.gsec_announcement_table(total), prid=63983, pr_no="2026-2027/1505")


def test_gsec_announcement_rowspans_and_greenshoe():
    df = _parse(_gsec_announcement()).tables["gsec_announcement"]
    assert df["security"].tolist() == ["6.53% GS 2031", "7.71% GS 2066"]  # Total row skipped
    _check(df.iloc[0], dict(repayment_date=T("2031-09-07"), notified_cr=19000.0, greenshoe_cr=2000.0,
                            auction_date=T("2026-11-20"), settlement_date=T("2026-11-23"),
                            notification="F.No.4(1)-B(W&M)/2026 dated November 16, 2026", auction_method="multiple"))
    _check(df.iloc[1], dict(repayment_date=T("2066-05-18"), notified_cr=14000.0, greenshoe_cr=2000.0,
                            auction_date=T("2026-11-20"), settlement_date=T("2026-11-23"),
                            notification="F.No.4(1)-B(W&M)/2026 dated November 16, 2026"))
    assert "Total row says 34000" in _only_reason(_parse(_gsec_announcement(total="34,000")))


def test_tbill_announcement_maturities():
    html = _page("Auction of 91-Day, 182-Day and 364-Day Treasury Bills", "Nov 13, 2026",
                 "The auction will be price based using multiple price method.", fx.tbill_announcement_table(),
                 prid=63975, pr_no="2026-2027/1497")
    df = _parse(html).tables["tbill_announcement"]
    assert df["tenor_days"].tolist() == [91, 182, 364] and df["notified_cr"].tolist() == [8000.0, 8000.0, 7000.0]
    assert df["auction_date"].eq(T("2026-11-18")).all() and df["settlement_date"].eq(T("2026-11-19")).all()
    assert df["maturity_date"].dt.strftime("%Y-%m-%d").tolist() == ["2027-02-18", "2027-05-20", "2027-11-18"]


def test_sdl_announcement_rows_under_rowspans():
    html = _page("Auction of State Government Securities", "Nov 13, 2026", fx.SDL_ANNOUNCEMENT_TEXT,
                 fx.sdl_announcement_table(), prid=63976, pr_no="2026-2027/1498")
    df = _parse(html).tables["sdl_announcement"]
    assert df["state"].tolist() == ["Karnataka"] + ["Madhya Pradesh"] * 3 + ["Tripura"] * 2
    assert df["auction_date"].eq(T("2026-11-17")).all() and df["settlement_date"].eq(T("2026-11-18")).all()
    _check(df.iloc[0], dict(state_code="KA", notified_cr=2000.0, issue_type="new", tenor_years=8, auction_type="Yield"))
    _check(df.iloc[2], dict(state_code="MP", notified_cr=1000.0, issue_type="reissue",
                            security="7.58% Madhya Pradesh SGS 2038", coupon_pct=7.58, maturity_year=2038,
                            first_issue_date=T("2026-07-22"), name_key="7.58% MP SGS 2038", auction_type="Price"))
    _check(df.iloc[4], dict(state_code="TR", notified_cr=200.0, coupon_pct=8.03, maturity_year=2034,
                            first_issue_date=T("2026-10-21")))
    _check(df.iloc[5], dict(state_code="TR", issue_type="new", tenor_years=20, notified_cr=1000.0))


# --- Checks and skips -------------------------------------------------------------------------------

def _goi_with(**changes):
    return _goi_page([dict(fx.GOI_6_94, **changes)])


@pytest.mark.parametrize("changes, message", [
    (dict(ytm="27.3203"), "cutoff_yield 27.3203 is outside the yield range"),
    (dict(price="30.43"), "cutoff_price 30.43 is outside the price range"),
    (dict(pa_pct="118.3591%"), "partial_allotment_pct 118.359 is outside the percentage range"),
    (dict(ca_amt="83,947.718"), "comp_accepted_cr 83947.7 is above comp_received_cr"),
    (dict(ca_n="400"), "comp_accepted_count 400 is above comp_received_count"),
    (dict(notified="15,000"), "more than 2 times notified 15000"),
])
def test_sanity_checks_refuse_the_release(changes, message):
    batch = _parse(_goi_with(**changes))
    assert batch.tables["gsec_result"].empty
    reason = _only_reason(batch)
    assert reason.startswith("failed check:") and message in reason


def test_bad_cells_and_missing_rows_are_parse_errors():
    assert "not a number: 'abc'" in _only_reason(_parse(_goi_with(cr_amt="abc")))
    assert "no 'cutoff' row" in _only_reason(_parse(_goi_page(skip=("cutoff",))))
    assert "unit unknown" in _only_reason(_parse(_goi_page(unit="")))
    broken = _goi_page().replace('<td colspan="2">Auction Results</td>', '<td colspan="2">Results</td>')
    assert "no table with an 'Auction Results' header" in _only_reason(_parse(broken))


def test_announced_greenshoe_bounds_the_result():
    result = _goi_with(notified="32,000", uw="32,000", uw_acc="32,000")  # 34,000 accepted on 32,000 notified
    announcement = pd.DataFrame({"auction_date": ["2026-11-13"], "name_key": ["6.94% GS 2036"],
                                 "greenshoe_cr": [2000.0]})
    ok = _parse(result, announcements=announcement)
    assert ok.tables["gsec_result"]["greenshoe_cr"].tolist() == [2000.0] and not ok.skipped
    tight = announcement.assign(greenshoe_cr=1000.0)
    assert "plus the announced greenshoe 1000" in _only_reason(_parse(result, announcements=tight))
    # an announcement in the same batch is used, whatever the input order
    same_day = _gsec_announcement().replace("November 20, 2026 (Friday)", "November 13, 2026 (Friday)")
    same_day = same_day.replace("6.53% GS 2031", "6.94% GS 2036").replace("Sep 07, 2031", "May 11, 2036")
    batch = _parse(result, same_day)
    assert batch.tables["gsec_result"]["greenshoe_cr"].tolist() == [2000.0]


def test_skip_reasons(tmp_path):
    blocked = tmp_path / "strips_3973.html"
    blocked.write_text(fx.blocked_page(), encoding="utf-8")
    items = [dict(title="RBI cancels Certificate of Registration of 49 NBFCs", prid=63979,
                  pub="Mon, 16 Nov 2026 11:05:00", html=fx.body("Text only.")),
             dict(title="Conversion/Switch of Government of India Securities", prid=63958,
                  pub="Wed, 11 Nov 2026 17:40:00", html=fx.body("Tables.")),
             dict(title="Money Market Operations as on November 15, 2026", prid=63981,
                  pub="Mon, 16 Nov 2026 12:05:00", html=fx.body("Tables."))]
    batch = _parse(blocked, fx.feed(items))
    reasons = {s.release_id: (s.kind, s.reason) for s in batch.skipped}
    assert reasons[3973][0] == "unreadable" and "Support ID 123456789" in reasons[3973][1]
    assert reasons[63979] == ("other", "title not recognised: 'RBI cancels Certificate of Registration of 49 NBFCs'")
    # a kind registered without a parser says why; a kind with a parser refuses a body that is not its layout
    assert reasons[63958] == ("switch_announcement", "no parser for switch_announcement: no page or feed item of "
                                                     "this kind has been sampled")
    assert reasons[63981] == ("money_market_operations", "cannot parse: <bytes 2>#3: no '(Amount in ₹ crore, Rate in "
                                                         "Per cent)' line; units unknown")
    assert all(t.empty for t in batch.tables.values()) and set(batch.tables) == set(rr.COLUMNS)
    assert list(batch.skipped_frame().columns) == list(rr.SKIPPED_COLUMNS) and len(batch.releases) == 4


def test_unknown_title_classified_from_tables():
    html = _page("Government Stock: Auction Result", "Nov 13, 2026", "", fx.results_table([fx.GOI_6_94]))
    batch = _parse(html)
    assert len(batch.tables["gsec_result"]) == 1
    entry = batch.releases.iloc[0]
    assert entry["classified_by"] == "tables" and "classified as gsec_result from its tables" in entry["notes"]
    other = _page("Some other release", "Nov 13, 2026", "No tables here.")
    assert _only_reason(_parse(other)).startswith("title not recognised")


def test_registry_takes_new_parsers(monkeypatch):
    monkeypatch.setitem(rr.COLUMNS, "test_kind", (*rr.COMMON_HEAD, "line", "words", *rr.COMMON_TAIL))
    monkeypatch.setattr(rr, "RELEASE_TYPES", dict(rr.RELEASE_TYPES))

    def parse_words(release, ctx):
        return [{"words": release.narrative}]

    rr.register(rr.ReleaseType("test_kind", re.compile(r"^test release$", re.I), parse_words))
    batch = _parse(_page("Test release", "Nov 13, 2026", "three short words"))
    assert batch.tables["test_kind"]["words"].tolist() == ["three short words Communications Press Release: "
                                                           "2026-2027/1492"]
    with pytest.raises(ValueError, match="declare COLUMNS"):
        rr.register(rr.ReleaseType("undeclared", re.compile("x"), parse_words))


def test_new_issue_named_without_a_coupon_is_keyed_at_par():
    par = _parse(_goi_with(name="New GS 2036", price="100.00", ytm="6.8500")).tables["gsec_result"].iloc[0]
    assert (par["security"], par["name_key"]) == ("New GS 2036", "6.85% GS 2036")
    off_par = _parse(_goi_with(name="New GS 2036")).tables["gsec_result"].iloc[0]  # 97.43: the yield is no coupon
    assert pd.isna(off_par["name_key"])


def _named_files(tmp_path, *pairs):
    paths = []
    for name, content in pairs:
        path = tmp_path / name
        path.write_bytes(content) if isinstance(content, bytes) else path.write_text(content, encoding="utf-8")
        paths.append(path)
    return paths


def test_a_copy_that_fails_does_not_hide_one_that_parses(tmp_path):
    bad_html = fx.body("", fx.results_table([fx.GOI_6_94], unit="(Amount)"))  # no crore: unit unknown
    good_item = dict(title="Government Stock - Full Auction Results", prid=63970, pub="Fri, 13 Nov 2026 19:00:00",
                     html=fx.body("", fx.results_table([fx.GOI_6_94])))
    bad_page = _page("Government Stock - Full Auction Results", "Nov 13, 2026", "",
                     fx.results_table([fx.GOI_6_94], unit="(Amount)"))
    page, feed = _named_files(tmp_path, ("goi_63970.html", bad_page), ("pr_feed.xml", fx.feed([good_item])))
    batch = _parse(page, feed)
    assert len(batch.tables["gsec_result"]) == 1 and batch.tables["gsec_result"].iloc[0]["source_file"] == \
        "pr_feed.xml#1"
    status = batch.releases.set_index("source_file")["status"].to_dict()
    assert status == {"goi_63970.html": "skipped", "pr_feed.xml#1": "parsed"}
    assert "unit unknown" in batch.releases.set_index("source_file").loc["goi_63970.html", "reason"]
    # a bad feed copy and a good page: the page parses, the feed copy is a duplicate
    bad_feed = dict(good_item, html=bad_html)
    page, feed = _named_files(tmp_path, ("goi_63970.html", _goi_page()), ("pr_feed.xml", fx.feed([bad_feed])))
    batch = _parse(feed, page)
    assert len(batch.tables["gsec_result"]) == 1
    assert batch.releases.set_index("source_file")["status"].to_dict() == {
        "pr_feed.xml#1": "duplicate", "goi_63970.html": "parsed"}


def test_copies_are_chosen_the_same_whatever_the_input_order(tmp_path):
    switch = fx.body("", fx.switch_table(fx.SWITCH_PAIRS), pr_no="2026-2027/1504")
    page, feed = _named_files(
        tmp_path, ("switch_63982.html", fx.release_page(
            "Result: Conversion/Switch Auction of Government of India Securities", "Nov 16, 2026", switch,
            prid=63982)),
        ("pr_feed.xml", fx.feed([_switch_item()])))
    one, two = _parse(page, feed), _parse(feed, page)
    pd.testing.assert_frame_equal(one.tables["switch_result"], two.tables["switch_result"])
    df = one.tables["switch_result"]
    assert df["source_file"].eq("switch_63982.html").all()  # a saved page before a feed copy
    assert df["published"].eq(T("2026-11-16 14:55")).all()  # the time from the feed copy
    key = ["source_file", "status", "reason"]
    pd.testing.assert_frame_equal(one.releases[key].sort_values("source_file").reset_index(drop=True),
                                  two.releases[key].sort_values("source_file").reset_index(drop=True))
