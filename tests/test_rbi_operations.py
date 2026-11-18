import copy
import datetime as dt
import os
import random
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
import rbi_fixtures as fx

from cr_strat import rbi_operations as ro
from cr_strat import rbi_releases as rr
from cr_strat.calendars import BusinessCalendar
from cr_strat.cli import main

D = dt.date
T = pd.Timestamp
FRI, SAT, SUN, MON = "Fri, 13/11/2026", "Sat, 14/11/2026", "Sun, 15/11/2026", "Mon, 16/11/2026"


def _page(title, date_line, narrative, *tables, prid, pr_no="2026-2027/1492", after=""):
    return fx.release_page(title, date_line, fx.body(narrative, *tables, pr_no=pr_no, after=after), prid=prid)


def _parse(*sources, **kw):
    return rr.parse_releases(list(sources), **kw)


def _reason(batch):
    assert len(batch.skipped) == 1, batch.skipped
    assert all(t.empty for t in batch.tables.values())  # refused: never half-read
    return batch.skipped[0].reason


def _notes(batch):
    assert len(batch.releases) == 1 and batch.releases.iloc[0]["status"] == "parsed", batch.releases.to_dict("records")
    return batch.releases.iloc[0]["notes"]


def _check(row, expected):
    for key, value in expected.items():
        if value is None:
            assert pd.isna(row[key]), key
        elif isinstance(value, float):
            assert row[key] == pytest.approx(value), key
        else:
            assert row[key] == value, key


def _without(df, *columns):
    return df.drop(columns=list(columns)).reset_index(drop=True)


# --- Registry and titles ----------------------------------------------------------------------------

PARSED_KINDS = {"gsec_announcement", "tbill_announcement", "sdl_announcement", "gsec_result", "tbill_result",
                "sdl_result", "underwriting_result", "switch_result", ro.MMO, ro.LAF_ANNOUNCEMENT, ro.LAF_RESULT,
                ro.OMO_ANNOUNCEMENT, ro.OMO_RESULT, ro.GSEC_CALENDAR, ro.SDL_CALENDAR}
UNPARSED_KINDS = {"gsec_cutoff", "tbill_cutoff", "sdl_cutoff", "underwriting_announcement", "switch_announcement",
                  "buyback_result", "buyback_announcement", "omo_cutoff", "tbill_calendar", "mpc_resolution",
                  "fx_swap_result"}
TITLES = [  # every operations title seen in RBI's listing, feed and saved pages of Nov 2026, and those reported
    ("Money Market Operations as on November 04, 2026", ro.MMO),
    ("Result of the 3-day Variable Rate Reverse Repo (VRRR) auction held on November 13, 2026", ro.LAF_RESULT),
    ("Result of the Overnight Variable Rate Reverse Repo (VRRR) auction held on November 12, 2026", ro.LAF_RESULT),
    ("Result of the 4-day Variable Rate Repo (VRR) auction held on November 09, 2026", ro.LAF_RESULT),
    ("RBI to conduct 2-day Variable Rate Reverse Repo (VRRR) auction under LAF on November 17, 2026",
     ro.LAF_ANNOUNCEMENT),
    ("RBI to conduct Overnight Variable Rate Reverse Repo (VRRR) auction under LAF on November 18, 2026",
     ro.LAF_ANNOUNCEMENT),
    ("RBI to conduct 4-day Variable Rate Repo (VRR) auction under LAF on November 09, 2026", ro.LAF_ANNOUNCEMENT),
    ("Detailed Result: OMO Purchase Auction held on Mar 13, 2026 and Settlement on Mar 16, 2026", ro.OMO_RESULT),
    ("Detailed Result: OMO Sale Auction held on Sep 28, 2026 and Settlement on Sep 29, 2026", ro.OMO_RESULT),
    ("RBI announces OMO Purchase of Government of India Securities", ro.OMO_ANNOUNCEMENT),
    ("RBI announces OMO Sale of Government of India Securities", ro.OMO_ANNOUNCEMENT),
    ("Open Market Operation (OMO) - Purchase of Government of India Securities held on Mar 13, 2026: Cut-Offs",
     "omo_cutoff"),
    ("Issuance Calendar for Marketable Dated Securities for October 2026 - March 2027", ro.GSEC_CALENDAR),
    (fx.SDL_CALENDAR_TITLE, ro.SDL_CALENDAR),
    ("Calendar for Auction of Government of India Treasury Bills", "tbill_calendar"),
    ("Revised Calendar for Auction of Government of India Treasury Bills", "tbill_calendar"),
    ("Resolution of the Monetary Policy Committee (MPC) October 5 to 7, 2026", "mpc_resolution"),
    ("Result of the USD/INR Buy/Sell Swap Auction, February 4, 2026", "fx_swap_result"),
    ("Auction of Government of India Dated Securities", "gsec_announcement"),
    ("Auction of Government of India Dated Security", "gsec_announcement"),  # one security: the singular
    ("Government Stock - Auction Results: Cut-off", "gsec_cutoff"),
    ("Government Stock - Full Auction Results", "gsec_result"),
    ("91-Day, 182-Day and 364-Day T-Bill Auction Result: Cut-off", "tbill_cutoff"),
    ("Treasury Bills: Full Auction Result", "tbill_result"),
    ("Auction of 91-Day, 182-Day and 364-Day Treasury Bills", "tbill_announcement"),
    ("Result of Yield/Price Based Auction of State Government Securities", "sdl_cutoff"),
    ("State Government Securities - Full Auction Result", "sdl_result"),
    ("Auction of State Government Securities", "sdl_announcement"),
    ("Underwriting Auction for sale of Government Security for ₹34,000 crore on November 13, 2026",
     "underwriting_announcement"),
    ("Underwriting Auction for sale of Government Securities for ₹36,000 crore on November 06, 2026",
     "underwriting_announcement"),
    ("Result of Underwriting Auction conducted on November 13, 2026", "underwriting_result"),
    ("Conversion/Switch of Government of India Securities", "switch_announcement"),
    ("Result: Conversion/Switch Auction of Government of India Securities", "switch_result"),
    ("Result of Buyback Auction of Government of India Dated Securities", "buyback_result"),
    ("Buyback of Government of India Dated Securities", "buyback_announcement"),
    # not operations, or forms no pattern is meant to take
    ("Minutes of the Monetary Policy Committee Meeting, October 5 to 7, 2026", "other"),
    ("Foreign Exchange Turnover Data: October 26 - October 30, 2026", "other"),
    ("Reserve Money and Money Supply for the fortnight ended on October 31, 2026", "other"),
    ("Results of OMO Purchase and Sale auction held on March 10, 2021 and settlement on March 12, 2021", "other"),
    ("RBI cancels Certificate of Registration of 49 NBFCs", "other"),
]


@pytest.mark.parametrize("title, kind", TITLES)
def test_titles_are_classified_by_one_pattern_at_most(title, kind):
    assert rr.classify_title(title) == kind
    taken = [k for k, t in rr.RELEASE_TYPES.items() if t.title.search(rr.clean_text(title).lower())]
    assert taken == ([] if kind == "other" else [kind])


def test_registry_holds_every_kind_with_a_parser_or_a_reason():
    assert set(rr.RELEASE_TYPES) == PARSED_KINDS | UNPARSED_KINDS and not PARSED_KINDS & UNPARSED_KINDS
    assert {k for k, t in rr.RELEASE_TYPES.items() if t.parser is not None} == PARSED_KINDS == set(rr.COLUMNS)
    assert all(rr.RELEASE_TYPES[k].no_parser for k in UNPARSED_KINDS)  # each says why it has no parser
    assert "vrrr_result" not in rr.RELEASE_TYPES and "vrrr_announcement" not in rr.RELEASE_TYPES
    for kind in ro.OPERATION_COLUMNS:
        columns = rr.COLUMNS[kind]
        assert columns[:3] == rr.COMMON_HEAD and columns[-2:] == rr.COMMON_TAIL and "line" in columns
        assert len(set(columns)) == len(columns)


def test_column_types_follow_one_rule():
    types = {c: rr.column_type(c) for kind in ro.OPERATION_COLUMNS for c in rr.COLUMNS[kind]}
    assert {c for c, t in types.items() if t == "date"} == {
        "release_date", "as_on_date", "auction_date", "maturity_date", "ref_date", "reversal_date", "settlement_date",
        "programme_date", "period_start", "period_end", "week_start", "week_end", "quarter_start", "quarter_end"}
    assert {c for c, t in types.items() if t == "int"} == {
        "release_id", "line", "seq", "tenor_days", "tenor_years", "week_no", "allocation_no",
        "offers_received_count", "offers_accepted_count"}
    assert {c for c, t in types.items() if t == "text"} == {
        "block", "item", "label", "marks", "direction", "window_start", "window_end", "operation", "isin",
        "security", "name_key", "auction_method", "programme_pr_no", "security_type", "annex_urls",
        "press_release_no", "source_file"}
    assert types["published"] == "time" and types["cutoff_yield"] == types["wap"] == types["rate_pct"] == "float"


@pytest.mark.parametrize("first", ["cr_strat.rbi_operations", "cr_strat.rbi_releases", "cr_strat.cli"])
def test_importing_either_module_registers_the_operation_kinds(first):
    code = (f"import {first}; from cr_strat import rbi_releases as rr; "
            "print(len(rr.RELEASE_TYPES), rr.RELEASE_TYPES['money_market_operations'].parser.__module__)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                         env={**os.environ, "PYTHONPATH": str(Path(rr.__file__).parents[1])})
    assert out.stdout.split() == ["26", "cr_strat.rbi_operations"]


def test_the_calendar_is_loaded_only_when_a_parser_asks_for_it(monkeypatch):
    loads = []
    monkeypatch.setattr(rr, "mumbai_settlement_calendar", lambda: loads.append(1) or BusinessCalendar(
        "stub", frozenset(), frozenset({2026})))
    auction = fx.release_page("Government Stock - Full Auction Results", "Nov 13, 2026",
                              fx.body("", fx.results_table([fx.GOI_6_94])), prid=63970)
    assert len(_parse(auction).tables["gsec_result"]) == 1 and not loads  # an auction result asks no calendar
    days = [fx.mmo_friday(), fx.mmo_saturday(), fx.mmo_sunday()]
    batch = _parse(auction, *(fx.mmo_page(d) for d in days))
    assert len(batch.tables[ro.MMO]) == 77 and loads == [1]  # once for the three releases that ask


def test_kinds_without_a_parser_are_listed_with_their_reason_and_time():
    items = [dict(title="Calendar for Auction of Government of India Treasury Bills", prid=63669,
                  pub="Fri, 25 Sep 2026 17:30:00", html=fx.body("A table that no parser has seen.")),
             dict(title="Result of Yield/Price Based Auction of State Government Securities", prid=63987,
                  pub="Tue, 17 Nov 2026 15:05:00", html=fx.body("Cut-offs."))]
    batch = _parse(fx.feed(items))
    index = batch.releases.set_index("release_id")
    assert index.loc[63669, "kind"] == "tbill_calendar" and index.loc[63669, "status"] == "skipped"
    assert index.loc[63669, "reason"].startswith("no parser for tbill_calendar: no page or feed item of this kind")
    assert index.loc[63669, "published"] == T("2026-09-25 17:30") and index.loc[63987, "kind"] == "sdl_cutoff"
    assert "full result follows the same day" in index.loc[63987, "reason"]


# --- Money Market Operations: every printed line ----------------------------------------------------

FRIDAY_ROWS = [  # item, seq, amount, rate, low, high, effect, marks
    ("overnight.total", 1, 734151.0, 5.61, 1.0, 7.3, None, ""),
    ("overnight.call_money", 1, 12340.5, 5.6, 4.25, 5.9, None, ""),
    ("overnight.triparty_repo", 1, 510220.25, 5.64, 5.4, 5.85, None, ""),
    ("overnight.market_repo", 1, 205110.0, 5.52, 1.0, 5.95, None, ""),
    ("overnight.corporate_bond_repo", 1, 6480.25, 5.72, 5.6, 7.3, None, ""),
    ("term.notice_money", 1, 120.0, 5.4, 4.9, 5.7, None, "**"),
    ("term.term_money", 1, 1050.5, None, 5.75, 6.25, None, "@@"),  # a volume and a range but no rate
    ("term.triparty_repo", 1, 0.0, None, None, None, None, ""),  # printed 0.00 stays 0; the dashes are blanks
    ("term.market_repo", 1, 0.0, None, None, None, None, ""),
    ("term.corporate_bond_repo", 1, 0.0, None, None, None, None, ""),
    ("laf.today.reverse_repo", 1, 40000.0, 5.49, None, None, -40000.0, ""),
    ("laf.today.msf", 1, 12500.0, 5.75, None, None, 12500.0, "#"),
    ("laf.today.msf", 2, 0.0, 5.75, None, None, 0.0, "#"),  # an amount of 0.00 is a row
    ("laf.today.msf", 3, 1000.0, 5.75, None, None, 1000.0, "#"),
    ("laf.today.sdf", 1, 150000.0, 5.25, None, None, -150000.0, "Δ#"),
    ("laf.today.sdf", 2, 900.0, 5.25, None, None, -900.0, "Δ#"),
    ("laf.today.sdf", 3, 2500.0, 5.25, None, None, -2500.0, "Δ#"),
    ("net.today", 1, -179900.0, None, None, None, None, "*"),
    ("slf.availed", 1, 9800.5, None, None, None, 9800.5, "$"),
    ("net.outstanding", 1, 9800.5, None, None, None, None, "*"),
    ("net.total", 1, -170099.5, None, None, None, None, "*"),
    ("reserves.cash_balances", 1, 840000.25, None, None, None, None, "^^"),
    ("reserves.crr_requirement", 1, 810000.0, None, None, None, None, "^"),
    ("government.surplus_cash_balance", 1, 0.0, None, None, None, None, "¥"),
    ("liquidity.net_durable", 1, 780000.0, None, None, None, None, ""),
]


def _mmo(day=None, **kw):
    return _parse(fx.mmo_page(fx.mmo_friday() if day is None else day, **kw))


def _mmo_table(day=None, **kw):
    batch = _mmo(day, **kw)
    assert not batch.skipped, batch.skipped
    return batch.tables[ro.MMO]


def _item(df, item, seq=1):
    rows = df[(df["item"] == item) & (df["seq"] == seq)]
    assert len(rows) == 1, (item, seq, len(rows))
    return rows.iloc[0]


def test_mmo_friday_gives_one_row_per_printed_line():
    batch = _mmo()
    df = batch.tables[ro.MMO]
    assert list(df.columns) == list(rr.COLUMNS[ro.MMO]) and df["line"].tolist() == list(range(1, 26))
    assert _notes(batch) == ""
    assert df["as_on_date"].eq(T("2026-11-13")).all() and df["release_date"].eq(T("2026-11-16")).all()
    assert df["published"].isna().all() and df["press_release_no"].eq("2026-2027/1499").all()
    assert df["release_id"].eq(63977).all() and df["source_file"].eq("<text 1>").all()
    assert df["block"].value_counts().to_dict() == {"rbi_operations": 11, "money_market": 10, "reserve_position": 4}
    assert list(zip(df["item"], df["seq"], df["marks"].fillna(""), strict=True)) == [(x[0], x[1], x[7])
                                                                                     for x in FRIDAY_ROWS]
    assert df["marks"].isna().sum() == 10  # a line without a footnote mark has a blank, as in the CSV
    for row, (item, _, amount, rate, low, high, effect, _) in zip(df.to_dict("records"), FRIDAY_ROWS, strict=True):
        _check(row, dict(amount_cr=amount, rate_pct=rate, rate_low_pct=low, rate_high_pct=high,
                         liquidity_effect_cr=effect))
    ops = df[df["item"].str.startswith("laf.")]
    assert ops["auction_date"].eq(T("2026-11-13")).all() and ops["tenor_days"].tolist() == [3, 1, 2, 3, 1, 2, 3]
    assert ops["maturity_date"].dt.day.tolist() == [16, 14, 15, 16, 14, 15, 16]
    rest = df[~df["item"].str.startswith("laf.")]
    assert rest[["auction_date", "tenor_days", "maturity_date"]].isna().all().all()
    reserves = df[df["block"] == "reserve_position"]
    assert reserves["ref_date"].dt.strftime("%Y-%m-%d").tolist() == ["2026-11-13", "2026-11-15", "2026-11-13",
                                                                     "2026-10-15"]
    assert df[df["block"] != "reserve_position"]["ref_date"].isna().all()
    assert _item(df, "laf.today.sdf", 2)["label"] == "4. SDFΔ#"  # a continuation row keeps the label that spans it
    assert _item(df, "net.total")["label"].startswith("F. Net liquidity injected (outstanding including today's")
    assert _item(df, "reserves.cash_balances")["label"] == "(i) Cash balances with RBI as on^^"
    # nothing for the lines with five blank cells: no operation is not a zero
    assert not df["item"].isin(["laf.today.repo"]).any() and not df["item"].str.startswith("laf.outstanding").any()
    # the effects of a release add up to its line F
    assert df["liquidity_effect_cr"].sum() == pytest.approx(_item(df, "net.total")["amount_cr"])
    # the corridor of the day
    assert (_item(df, "laf.today.sdf")["rate_pct"], _item(df, "laf.today.msf")["rate_pct"]) == (5.25, 5.75)


@pytest.mark.parametrize("day", [fx.mmo_friday, fx.mmo_saturday, fx.mmo_sunday, fx.mmo_monday])
def test_mmo_page_and_feed_item_give_the_same_rows(day):
    page, feed = _parse(fx.mmo_page(day())), _parse(fx.feed([fx.mmo_item(day())]))
    a, b = page.tables[ro.MMO], feed.tables[ro.MMO]
    pd.testing.assert_frame_equal(_without(a, "published", "source_file"), _without(b, "published", "source_file"))
    assert a["published"].isna().all() and b["published"].eq(T(day()["pub"])).all()
    assert b["source_file"].eq("<bytes 1>#1").all() and _notes(page) == _notes(feed) == ""
    # both copies in one batch: the page gives the rows, the feed copy the time
    both = _parse(fx.feed([fx.mmo_item(day())]), fx.mmo_page(day()))
    assert both.releases["status"].tolist() == ["duplicate", "parsed"]
    pd.testing.assert_frame_equal(_without(both.tables[ro.MMO], "source_file"), _without(b, "source_file"))


def test_mmo_weekend_layouts():
    sat, sun = _mmo_table(fx.mmo_saturday()), _mmo_table(fx.mmo_sunday())
    assert (len(sat), len(sun)) == (27, 25)
    for df in (sat, sun):
        markets = df[df["block"] == "money_market"]
        assert len(markets) == 10 and markets["amount_cr"].eq(0.0).all()  # printed 0.00: zero, and the rows stay
        assert markets[["rate_pct", "rate_low_pct", "rate_high_pct"]].isna().all().all()  # dashes: blank, not 0
        assert not df["item"].eq("laf.today.reverse_repo").any()  # five blank cells: no row
    assert sat["as_on_date"].eq(T("2026-11-14")).all() and sat["release_date"].eq(T("2026-11-16")).all()
    today = sat[sat["item"].str.startswith("laf.today.")]
    assert [(r.item, r.seq, r.tenor_days, r.maturity_date.day, r.amount_cr) for r in today.itertuples()] == [
        ("laf.today.msf", 1, 1, 15, 20000.0), ("laf.today.msf", 2, 2, 16, 0.0),
        ("laf.today.sdf", 1, 1, 15, 155000.0), ("laf.today.sdf", 2, 2, 16, 10.0)]
    assert today["auction_date"].eq(T("2026-11-14")).all()
    outstanding = sat[sat["item"].str.startswith("laf.outstanding.")]
    assert [(r.item, r.seq, r.auction_date.day, r.tenor_days, r.maturity_date.day, r.amount_cr, r.rate_pct,
             r.liquidity_effect_cr) for r in outstanding.itertuples()] == [
        ("laf.outstanding.reverse_repo", 1, 13, 3, 16, 40000.0, 5.49, -40000.0),
        ("laf.outstanding.msf", 1, 13, 2, 15, 0.0, 5.75, 0.0),
        ("laf.outstanding.msf", 2, 13, 3, 16, 1000.0, 5.75, 1000.0),
        ("laf.outstanding.sdf", 1, 13, 2, 15, 900.0, 5.25, -900.0),
        ("laf.outstanding.sdf", 2, 13, 3, 16, 2500.0, 5.25, -2500.0)]
    assert [_item(sat, i)["amount_cr"] for i in ("net.today", "slf.availed", "net.outstanding", "net.total")] == [
        -135010.0, 9800.5, -32599.5, -167609.5]
    assert [_item(sun, i)["amount_cr"] for i in ("net.today", "net.outstanding", "net.total")] == [
        -135000.0, -31709.5, -166709.5]
    # Sunday's outstanding rows come most recent auction first; seq keeps the printed order
    msf = sun[sun["item"] == "laf.outstanding.msf"]
    assert [(r.seq, r.auction_date.day, r.tenor_days) for r in msf.itertuples()] == [(1, 14, 2), (2, 13, 3)]
    # each reserve line has its own date: only the cash balances are as on the weekend day
    assert sat[sat["block"] == "reserve_position"]["ref_date"].dt.strftime("%m-%d").tolist() == [
        "11-14", "11-15", "11-13", "10-15"]


def test_mmo_monday_and_the_outstanding_rows_across_the_four_days():
    days = [fx.mmo_friday(), fx.mmo_saturday(), fx.mmo_sunday(), fx.mmo_monday()]
    batch = _parse(*(fx.mmo_page(d) for d in days))
    df = batch.tables[ro.MMO]
    assert not batch.skipped and batch.releases["notes"].eq("").all()
    assert df.groupby("as_on_date").size().tolist() == [25, 27, 25, 20]
    monday = df[df["release_id"] == 63985]
    _check(_item(monday, "term.market_repo"), dict(amount_cr=7.25, rate_pct=5.98, rate_low_pct=5.98,
                                                   rate_high_pct=5.98))
    _check(_item(monday, "reserves.crr_requirement"), dict(ref_date=T("2026-11-30"), amount_cr=825000.0))
    assert not monday["item"].str.startswith("laf.outstanding").any()
    # the outstanding rows of a day are the earlier days' today rows that have not matured
    today = df[df["item"].str.startswith("laf.today.")]
    for release_id, day in ((63977, "2026-11-13"), (63980, "2026-11-14"), (63981, "2026-11-15"), (63985, "2026-11-16")):
        still = today[(today["auction_date"] < T(day)) & (today["maturity_date"] > T(day))]
        expected = sorted((r.item.replace("today", "outstanding"), r.auction_date, r.tenor_days, r.amount_cr)
                          for r in still.itertuples())
        rows = df[(df["release_id"] == release_id) & df["item"].str.startswith("laf.outstanding.")]
        assert sorted((r.item, r.auction_date, r.tenor_days, r.amount_cr) for r in rows.itertuples()) == expected
    assert [len(df[(df["release_id"] == i) & df["item"].str.startswith("laf.outstanding.")])
            for i in (63977, 63980, 63981, 63985)] == [0, 5, 5, 0]


def test_mmo_a_repo_operation_injects():
    day = fx.mmo_friday()
    day["today"]["repo"] = [(FRI, "4", "Tue, 17/11/2026", "50,000.00", "5.51")]
    day.update(net_today="-1,29,900.00", net_total="-1,20,099.50")
    df = _mmo_table(day)
    _check(_item(df, "laf.today.repo"), dict(auction_date=T("2026-11-13"), tenor_days=4, maturity_date=T("2026-11-17"),
                                             amount_cr=50000.0, rate_pct=5.51, liquidity_effect_cr=50000.0,
                                             label="(a) Repo Operation"))
    assert len(df) == 26


def test_mmo_several_auctions_under_one_line_are_continuation_rows():
    day = fx.mmo_saturday()  # a second reverse repo still running, printed under the label's rowspan
    day["outstanding"]["reverse_repo"].append(("Thu, 12/11/2026", "7", "Thu, 19/11/2026", "10,000.00", "5.48"))
    day.update(net_outstanding="-42,599.50", net_total="-1,77,609.50")
    rows = _mmo_table(day)
    rows = rows[rows["item"] == "laf.outstanding.reverse_repo"]
    assert rows["seq"].tolist() == [1, 2] and rows["amount_cr"].tolist() == [40000.0, 10000.0]
    assert rows["liquidity_effect_cr"].tolist() == [-40000.0, -10000.0] and rows["tenor_days"].tolist() == [3, 7]


@pytest.mark.parametrize("change", [
    lambda html: html.replace("<sup>", "").replace("</sup>", "").replace("<b>**</b>", "**"),  # marks as plain text
    lambda html: html.replace("<br>", " "),  # header cells in one line
    lambda html: html.replace("&nbsp;" * 5, ""),  # sub-lines not indented
    lambda html: html.replace("Rate in Per cent", "Rate in Percent"),
    lambda html: html.replace("Today's", "Today’s").replace("today's", "today’s"),  # a typographic apostrophe
    lambda html: html.replace("A. Overnight Segment (I+II+III+IV)", "A. OVERNIGHT SEGMENT (I + II + III + IV)"),
    lambda html: html.replace("3. MSF", "3.&nbsp; MSF ").replace("(b) Reverse Repo Operation",
                                                               "(b)&nbsp;Reverse  Repo Operation"),
    lambda html: html.replace("<th", "<td").replace("</th>", "</td>"),  # label cells as plain cells
    lambda html: html.replace("<td>November ", "<td>Nov ").replace("<td>October ", "<td>Oct. "),  # short months
    lambda html: html.replace("Fri, 13/11/2026", "Friday, 13/11/2026").replace("Mon, 16/11/2026", "Mon 16/11/2026"),
    lambda html: html.replace("<td align=\"right\">1.00-7.30</td>", "<td align=\"right\">1.00 – 7.30</td>"),
])
def test_mmo_label_variants_read_the_same(change):
    plain = _mmo_table()
    batch = _parse(change(fx.mmo_page(fx.mmo_friday())))
    assert _notes(batch) == ""
    values = [c for c in plain.columns if c != "label"]
    pd.testing.assert_frame_equal(batch.tables[ro.MMO][values], plain[values])


def test_mmo_lines_e_and_f_span_two_columns():
    # the label of E and F covers the Auction Date column: filling spans would put it there
    release = rr.read_release_page(fx.mmo_page(fx.mmo_friday()))
    table = next(t for t in release.data_tables if t.rows[0][0].text.startswith("RBI Operations"))
    assert table.rows[-1][0].colspan == 2 and len(table.rows[-1]) == 5
    assert table.grid()[-1][1].startswith("F. Net liquidity") and table.grid(fill=False)[-1][1] == ""
    row = _item(_mmo_table(), "net.total")
    assert pd.isna(row["auction_date"]) and pd.isna(row["tenor_days"]) and row["amount_cr"] == -170099.5


# --- Money Market Operations: identities ------------------------------------------------------------

def _friday(change):
    day = fx.mmo_friday()
    change(day)
    return day


def _set(path, value):
    """A change to one entry of a day's description: ``("markets", 1, 2)`` is the rate of the second line."""
    def change(day):
        target = day
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
    return change


def _operation(section, facility, index, **fields):
    """A change to cells of one operation: ``_operation("today", "msf", 0, amount="12,600.00")``."""
    names = ("auction", "tenor", "maturity", "amount", "rate")

    def change(day):
        row = list(day[section][facility][index])
        for name, value in fields.items():
            row[names.index(name)] = value
        day[section][facility][index] = tuple(row)
    return change


@pytest.mark.parametrize("change, message", [
    # M1: the overnight volume is the sum of its four parts
    (_set(("markets", 1, 1), "12,340.60"), "overnight.total volume 734151.00 is not the sum of its four parts "
                                           "(734151.10"),
    # M2: line 5 is repo + MSF - reverse repo - SDF over today's operations
    (_operation("today", "msf", 0, amount="12,600.00"), "net.today -179900.00 is not repo + MSF - reverse repo - SDF "
                                                        "over today's operations (-179800.00)"),
    (_set(("net_today",), "1,79,900.00"), "net.today 179900.00 is not repo + MSF - reverse repo - SDF"),  # the sign
    # M3: line E is the outstanding operations plus SLF
    (_set(("slf",), "9,900.50"), "net.outstanding 9800.50 is not the outstanding operations plus SLF (9900.50)"),
    # M4: line F is line 5 plus line E
    (_set(("net_total",), "-1,70,000.00"), "net.total -170000.00 is not net.today -179900.00 plus net.outstanding "
                                           "9800.50"),
    # M5: maturity is the auction date plus the tenor, and the printed weekdays fit
    (_operation("today", "msf", 0, tenor="2"), "maturity 2026-11-14 is not the auction date 2026-11-13 plus the "
                                               "tenor of 2 days"),
    (_operation("today", "sdf", 2, maturity="Tue, 17/11/2026"), "maturity 2026-11-17 is not the auction date"),
    (_operation("today", "msf", 0, maturity="Sun, 14/11/2026"), "maturity date 'Sun, 14/11/2026' is a Saturday"),
    (_operation("today", "reverse_repo", 0, auction="Thu, 13/11/2026"), "auction date 'Thu, 13/11/2026' is a Friday"),
    # M6: a row of today's block is dated the as-on date
    (_operation("today", "reverse_repo", 0, auction="Thu, 12/11/2026", tenor="4"),
     "an operation of today's block is dated 2026-11-12, not the 2026-11-13 of the title"),
    # M7: limits
    (_set(("markets", 1, 2), "25.60"), "[overnight.call_money]: rate_pct 25.6 is outside the rate range 0-20"),
    (_set(("markets", 1, 3), "5.90-4.25"), "[overnight.call_money]: the range 5.9-4.25 runs downwards"),
    (_set(("markets", 6, 1), "-120.00"), "[term.notice_money]: amount_cr is negative (-120)"),
    (_set(("reserves", 0, 2), "-8,40,000.25"), "[reserves.cash_balances]: amount_cr is negative"),
    (_operation("today", "sdf", 0, rate="52.5"), "[laf.today.sdf]: rate_pct 52.5 is outside the rate range"),
])
def test_mmo_a_failed_identity_refuses_the_release(change, message):
    reason = _reason(_mmo(_friday(change)))
    assert reason.startswith("failed check: <text 1>") and message in reason


def test_mmo_identities_hold_within_rounding_and_net_durable_liquidity_may_be_negative():
    day = _friday(_set(("markets", 1, 1), "12,340.54"))  # 0.04 off: within the 0.05 allowed for two-decimal sums
    assert len(_mmo_table(day)) == 25
    day = _friday(_set(("reserves", 3, 2), "-7,80,000.00"))  # a deficit
    assert _item(_mmo_table(day), "liquidity.net_durable")["amount_cr"] == -780000.0
    limits = rr.SanityLimits(mmo_sum_tolerance_cr=0.2)
    assert not _parse(fx.mmo_page(_friday(_set(("markets", 1, 1), "12,340.60"))), limits=limits).skipped


# --- Money Market Operations: structure -------------------------------------------------------------

def _tables(day=None):
    day = fx.mmo_friday() if day is None else day
    return [fx.mmo_markets_table(day), fx.mmo_operations_table(day), fx.mmo_reserves_table(day)]


def _mmo_with(tables, day=None):
    return _parse(fx.mmo_page(fx.mmo_friday() if day is None else day, tables=tuple(tables)))


def _fixed_rate_filled(day):
    day["today"]["fixed"] = [(FRI, "1", SAT, "100.00", "5.50")]


def _fixed_rate_filled_on_a_second_row(day):
    day["outstanding"]["fixed"] = [("&nbsp;",) * 5, (FRI, "3", MON, "100.00", "5.50")]


def _variable_rate_heading_filled(day):
    day["today"]["variable"] = [(FRI, "1", SAT, "100.00", "5.50")]


@pytest.mark.parametrize("change, message", [
    (_fixed_rate_filled, "RBI Operations row 4 ('1. Fixed Rate'): a filled '1. Fixed Rate' line was in no sample "
                         "and its sign in the net lines is not known; the release is not read"),
    (_fixed_rate_filled_on_a_second_row, "(under '1. Fixed Rate'): a filled '1. Fixed Rate' line was in no sample"),
    (_variable_rate_heading_filled, "('2. Variable Rate&'): a heading with values"),
    (_operation("today", "msf", 1, rate="&nbsp;"), "an operation without its rate (a facility row prints all five "
                                                   "cells or none)"),
    (_operation("today", "msf", 1, auction="-", maturity=""), "an operation without its auction date, maturity date"),
    (_operation("today", "msf", 1, amount="abc"), "not a number: 'abc'"),
    (_operation("today", "msf", 1, tenor="1.5"), "not a count: '1.5'"),
    (_operation("today", "msf", 1, auction="13/11/2026"), "auction date '13/11/2026' is not of the form "
                                                         "'Fri, 13/11/2026'"),
    (_operation("today", "msf", 1, auction="Fri, 31/11/2026"), "auction date 'Fri, 31/11/2026' is not a date"),
    (_set(("markets", 6, 3), "4.90 to 5.70"), "Money Markets row 8 ('I. Notice Money**'): range '4.90 to 5.70' is "
                                              "not 'low-high'"),
    (_set(("markets", 6, 2), "5,40"), "not a number: '5,40'"),
    (_set(("markets", 5, 1), "1.00"), "('B. Term Segment'): the heading of the term segment carries values"),
    (_set(("reserves", 1, 1), "15/11/2026"), "Reserve Position row 4"),
    (_set(("net_today",), "&nbsp;"), "RBI Operations row 14 (\"5. Net liquidity injected from today's operations "
                                     "[injection (+)/absorption (-)]*\"): no amount"),
    (lambda day: day["markets"].pop(4), "the line overnight.corporate_bond_repo is missing"),
    (lambda day: day["markets"].insert(2, list(day["markets"][1])), "the line overnight.call_money is printed more "
                                                                    "than once"),
    (lambda day: day["reserves"].pop(3), "the line liquidity.net_durable is missing"),
    (lambda day: day["markets"].pop(0), "the line overnight.total is missing"),
])
def test_mmo_structure_refusals(change, message):
    reason = _reason(_mmo(_friday(change)))
    assert reason.startswith("cannot parse: <text 1>") and message in reason


def test_mmo_missing_tables_units_and_title():
    markets, operations, reserves = _tables()
    assert "no table with the header 'Reserve Position'" in _reason(_mmo_with([markets, operations]))
    assert "no table with the header 'Money Markets'" in _reason(_mmo_with([operations, reserves]))
    assert "2 tables with the header 'RBI Operations'" in _reason(_mmo_with([markets, operations, operations,
                                                                            reserves]))
    assert "units unknown" in _reason(_mmo(units="(Amount in ₹ lakh)"))
    assert "units unknown" in _reason(_mmo(units="(Amount in ₹ crore)"))
    day = fx.mmo_friday()
    page = fx.release_page("Money Market Operations as on 13.11.2026", day["date_line"], fx.mmo_body(day), prid=1)
    assert "title 'Money Market Operations as on 13.11.2026' is not of the form" in _reason(_parse(page))
    page = fx.release_page("Money Market Operations as on November 31, 2026", day["date_line"], fx.mmo_body(day),
                           prid=1)
    assert "title: not a date" in _reason(_parse(page))


def test_mmo_columns_are_found_by_their_labels_and_an_unknown_one_refuses():
    markets, operations, reserves = _tables()
    renamed = markets.replace("Volume<br> (One Leg)", "Volume<br> (Both Legs)")
    assert "the 'Money Markets' table has 0 columns headed 'volumeoneleg'" in _reason(_mmo_with([renamed, operations,
                                                                                               reserves]))
    extra = markets.replace("</th></tr>", "</th><th>Remarks</th></tr>", 1)
    assert ("the 'Money Markets' table has columns that no sample had ('Remarks'); their values would be lost"
            in _reason(_mmo_with([extra, operations, reserves])))
    stray = reserves.replace("<td>0.00</td>", "<td>0.00</td><td>see note</td>")
    assert "cells beyond the table's 3 columns" in _reason(_mmo_with([markets, operations, stray]))
    # the columns in another order are the same table
    swapped = fx.mmo_friday()
    swapped["reserves"] = [[label, amount, date] for label, date, amount in swapped["reserves"]]
    moved = fx.mmo_reserves_table(swapped).replace("<th>Date</th><th>Amount</th>", "<th>Amount</th><th>Date</th>")
    batch = _mmo_with([markets, operations, moved])
    pd.testing.assert_frame_equal(batch.tables[ro.MMO], _mmo_table())


def test_mmo_a_continuation_row_needs_the_label_that_spans_it():
    markets, operations, reserves = _tables()
    # without the rowspan the two rows of five cells shift one column to the left
    unspanned = operations.replace('<th rowspan="3" valign="top">3. MSF', '<th valign="top">3. MSF')
    assert ("RBI Operations row 9 ('Fri, 13/11/2026'): a value where the row's label should be (a row of five cells "
            "that no label spans)") in _reason(_mmo_with([markets, unspanned, reserves]))
    # values under a label that is no facility line
    net = "5.&nbsp;Net liquidity injected"
    spanned = operations.replace(f"<th> {net}", f'<th rowspan="2"> {net}').replace(
        "<tr><th colspan=\"6\"> II.", f"<tr><td>{FRI}</td><td>1</td><td>{SAT}</td><td>5.00</td><td>5.75</td></tr>"
                                     "<tr><th colspan=\"6\"> II.")
    assert "values under a label that is not a facility line" in _reason(_mmo_with([markets, spanned, reserves]))
    # values in a row with an empty label of its own
    unlabelled = operations.replace("<tr><th colspan=\"6\"> II.", f"<tr><th></th><td>{FRI}</td><td>1</td><td>{SAT}"
                                    "</td><td>5.00</td><td>5.75</td></tr><tr><th colspan=\"6\"> II.")
    assert "RBI Operations row 15 (''): values without a label" in _reason(_mmo_with([markets, unlabelled, reserves]))
    blank_row = operations.replace("<tr><th colspan=\"6\"> II.", "<tr><th></th>" + "<td>&nbsp;</td>" * 5
                                   + "</tr><tr><th colspan=\"6\"> II.")
    assert len(_mmo_with([markets, blank_row, reserves]).tables[ro.MMO]) == 25  # an empty row is nothing


def test_mmo_rows_without_a_label_and_empty_rows():
    markets, operations, reserves = _tables()
    values = "<td>1.00</td><td>5.50</td><td>5.40-5.60</td>"
    unlabelled = markets.replace("</tbody>", f"<tr><th></th>{values}</tr></tbody>")
    assert "Money Markets row 13 (''): values without a label" in _reason(_mmo_with([unlabelled, operations, reserves]))
    shifted = markets.replace("</tbody>", f"<tr><th>12,340.50</th>{values}</tr></tbody>")
    assert "Money Markets row 13 ('12,340.50'): a value where the row's label should be" in _reason(
        _mmo_with([shifted, operations, reserves]))
    dated = reserves.replace("</tbody>", "<tr><th></th><td>November 13, 2026</td><td>1.00</td></tr></tbody>")
    assert "Reserve Position row 7 (''): values without a label" in _reason(_mmo_with([markets, operations, dated]))
    heading = "G. Cash Reserves Position of Scheduled Commercial Banks"
    filled = reserves.replace(f'<tr><th colspan="4">{heading}</th></tr>',
                              f"<tr><th>{heading}</th><td>November 13, 2026</td><td>5.00</td></tr>")
    assert f"Reserve Position row 2 ('{heading}'): a heading with values" in _reason(
        _mmo_with([markets, operations, filled]))
    # rows that print nothing are nothing: blank cells, dashes, an empty <tr>
    blank = "<tr><th>&nbsp;</th><td>&nbsp;</td><td>-</td><td></td></tr>"
    batch = _mmo_with([markets.replace("</tbody>", blank + "</tbody>"),
                       operations.replace("</tbody>", "<tr></tr></tbody>"),
                       reserves.replace("</tbody>", "<tr><th></th><td>&nbsp;</td><td></td></tr></tbody>")])
    assert _notes(batch) == ""
    pd.testing.assert_frame_equal(batch.tables[ro.MMO], _mmo_table())
    # a blank row under a label that spans it is no operation
    day = fx.mmo_friday()
    day["today"]["msf"][2] = ("&nbsp;",) * 5
    day.update(net_today="-1,80,900.00", net_total="-1,71,099.50")
    df = _mmo_table(day)
    assert df[df["item"] == "laf.today.msf"]["seq"].tolist() == [1, 2] and len(df) == 24


def test_mmo_feed_item_without_a_publication_time():
    batch = _parse(fx.feed([dict(fx.mmo_item(fx.mmo_friday()), pub="")]))
    df = batch.tables[ro.MMO]
    assert len(df) == 25 and df["release_date"].isna().all() and df["published"].isna().all()
    assert df["as_on_date"].eq(T("2026-11-13")).all() and _notes(batch) == ""  # nothing to check the day against


def test_mmo_headings_of_the_two_blocks_are_required_once():
    markets, operations, reserves = _tables()
    no_heading = operations.replace('<tr><th colspan="6"> II. Outstanding Operations</th></tr>', "")
    assert "the RBI Operations table has no heading II. Outstanding Operations" in _reason(
        _mmo_with([markets, no_heading, reserves]))
    twice = operations.replace(" II. Outstanding Operations", " I. Today's Operations")
    assert "the heading occurs twice" in _reason(_mmo_with([markets, twice, reserves]))
    early = operations.replace('<tr><th colspan="6"> I. Today\'s Operations</th></tr>', "")
    assert "a facility line before the heading" in _reason(_mmo_with([markets, early, reserves]))
    filled = operations.replace(f'<tr><th colspan="6">{fx.MMO_OUTSTANDING_HEADING}</th></tr>',
                                f"<tr><th>{fx.MMO_OUTSTANDING_HEADING}</th><td>1</td>" + "<td></td>" * 4 + "</tr>")
    assert "('II. Outstanding Operations'): a heading with values" in _reason(_mmo_with([markets, filled, reserves]))
    dated = operations.replace("<td>-1,79,900.00</td><td>&nbsp;</td>", "<td>-1,79,900.00</td><td>5.75</td>")
    assert "a line that carries an amount only has a rate" in _reason(_mmo_with([markets, dated, reserves]))


# --- Money Market Operations: notes -----------------------------------------------------------------

def test_mmo_an_unknown_row_is_noted_with_its_cells():
    day = fx.mmo_friday()
    day["markets"].append([fx.NBSP5 + "VI. Some New Line", "5.00", "5.50", "5.40-5.60"])
    batch = _mmo(day)
    assert len(batch.tables[ro.MMO]) == 25
    assert _notes(batch) == ("Money Markets row 13 'VI. Some New Line' not recognised; ignored (cells: 5.00, 5.50, "
                             "5.40-5.60)")
    day = fx.mmo_friday()
    day["reserves"].append(["J. Another balance as on", "November 13, 2026", "12.00"])
    assert "Reserve Position row 7 'J. Another balance as on' not recognised; ignored (cells: November 13, 2026, " \
           "12.00)" in _notes(_mmo(day))
    # a name of the other segment is not known where it stands
    day = fx.mmo_friday()
    day["markets"].insert(5, [fx.NBSP5 + "V. Notice Money", "0.00", "-", "-"])
    assert "Money Markets row 7 'V. Notice Money' not recognised" in _notes(_mmo(day))


LONG_TERM = ('<tr><th valign="top">&nbsp;(c) Long Term Repo Operation</th><td>Fri, 13/11/2026</td><td>14</td>'
             "<td>Fri, 27/11/2026</td><td>5,000.00</td><td>5.55</td></tr>")


def test_mmo_a_layout_with_other_sub_lines_fails_the_net_lines_once_they_are_filled():
    markets, operations, reserves = _tables()
    position = '<tr><th rowspan="3" valign="top">3. MSF'
    with_line = operations.replace(position, LONG_TERM + position, 1)
    batch = _mmo_with([markets, with_line, reserves])  # line 5 does not count the unknown line: the release stands
    assert _notes(batch) == ("RBI Operations row 8 '(c) Long Term Repo Operation' not recognised; ignored (cells: "
                             "Fri, 13/11/2026, 14, Fri, 27/11/2026, 5,000.00, 5.55)")
    counted = with_line.replace("-1,79,900.00", "-1,74,900.00").replace("-1,70,099.50", "-1,65,099.50")
    reason = _reason(_mmo_with([markets, counted, reserves]))
    assert "net.today -174900.00 is not repo + MSF - reverse repo - SDF over today's operations (-179900.00)" in reason
    assert reason.endswith("rows of the table that were not recognised: ['(c) Long Term Repo Operation'] (a layout "
                           "not sampled?)")


def test_mmo_an_unknown_table_is_noted():
    extra = '<table><tr><th>Forex</th><th>Amount</th></tr><tr><th>Sales</th><td>12.00</td></tr></table>'
    batch = _mmo_with([*_tables(), extra])
    assert _notes(batch) == "table 5 of the release (2 rows, beginning 'Forex') not recognised; ignored"
    assert len(batch.tables[ro.MMO]) == 25


def test_mmo_changed_footnote_marks_are_noted():
    page = fx.mmo_page(fx.mmo_friday())
    changed = page.replace("3. MSF<sup>#</sup>", "3. MSF<sup>##</sup>").replace("as on^^", "as on").replace(
        "Money Markets<sup>@</sup>", "Money Markets<sup>@@</sup>").replace("I. Call Money", "I. Call Money<sup>$</sup>")
    batch = _parse(changed)
    assert _notes(batch).split("; ") == [
        "marks of the Money Markets heading changed: '@@' (was '@')",
        "marks of overnight.call_money changed: '$' (was '')",
        "marks of laf.today.msf changed: '##' (was '#')",
        "marks of laf.outstanding.msf changed: '##' (was '#')",
        "marks of reserves.cash_balances changed: '' (was '^^')"]
    df = batch.tables[ro.MMO]
    assert _item(df, "laf.today.msf")["marks"] == "##" and pd.isna(_item(df, "reserves.cash_balances")["marks"])
    pd.testing.assert_frame_equal(_without(df, "label", "marks"), _without(_mmo_table(), "label", "marks"))


def _volume_without_range(day):
    day["markets"][7][3] = "-"


@pytest.mark.parametrize("change, note", [
    (_set(("markets", 1, 2), "5.95"), "overnight.call_money: rate 5.95 is outside its range 4.25-5.9"),
    (_set(("markets", 8, 2), "5.50"), "term.triparty_repo: a volume of 0 with a rate or a range"),
    (_volume_without_range, "term.term_money: a volume of 1050.5 without a range"),
    (_set(("markets", 0, 2), "5.70"), "overnight.total: rate 5.7 against 5.6065, the volume-weighted rate of its "
                                      "parts"),
    (_set(("markets", 0, 3), "1.00-7.40"), "overnight.total: range 1-7.4 against 1-7.3 over its parts"),
    (_operation("today", "msf", 1, rate="5.80"), "laf.today.msf: rows at different rates ([5.75, 5.8])"),
    (_set(("reserves", 0, 1), "November 12, 2026"), "reserves.cash_balances is dated 2026-11-12, not the as-on date "
                                                     "2026-11-13"),
    (_set(("markets", 10, 1), "-"), "term.corporate_bond_repo: no amount printed"),
    (_set(("reserves", 2, 1), "&nbsp;"), "government.surplus_cash_balance: no date printed"),
    (_set(("slf",), "&nbsp;"), None),  # no SLF: a blank, and line E is then the outstanding operations alone
    (_set(("date_line",), "Nov 17, 2026"), "published on 2026-11-17, not on 2026-11-16, the next business day after "
                                           "the as-on date 2026-11-13"),
])
def test_mmo_soft_anomalies_are_notes(change, note):
    day = _friday(change)
    if note is None:
        day.update(net_outstanding="0.00", net_total="-1,79,900.00")
    batch = _mmo(day)
    assert len(batch.tables[ro.MMO]) == 25
    assert _notes(batch) == (note or "")
    if note is None:
        row = _item(batch.tables[ro.MMO], "slf.availed")
        assert pd.isna(row["amount_cr"]) and pd.isna(row["liquidity_effect_cr"])


def test_mmo_an_outstanding_row_that_has_matured_is_noted():
    day = fx.mmo_saturday()
    day["outstanding"]["msf"][0] = (FRI, "1", SAT, "0.00", "5.75")
    assert _notes(_mmo(day)) == ("laf.outstanding.msf: an outstanding operation of 2026-11-13 maturing 2026-11-14 is "
                                 "not strictly around the as-on date 2026-11-14")


def _year_end(published="Jan 01, 2027"):
    """Thursday 31 Dec 2026, built from the Monday fixture: its next business day is in 2027."""
    day = fx.mmo_monday()
    thu, fri = "Thu, 31/12/2026", "Fri, 01/01/2027"
    day.update(as_on="December 31, 2026", date_line=published, prid=64400,
               today={**day["today"], "msf": [(thu, "1", fri, "6,000.00", "5.75")],
                      "sdf": [(thu, "1", fri, "1,45,000.00", "5.25")]})
    day["reserves"][0][1] = "December 31, 2026"
    return day


def test_mmo_publication_day_uses_the_calendar_and_never_fails_for_a_year_it_lacks():
    # Monday 9 Nov and the holiday Tuesday 10 Nov 2026 were both due on Wednesday 11 Nov
    day = fx.mmo_monday()
    mon, tue = "Mon, 09/11/2026", "Tue, 10/11/2026"
    day.update(as_on="November 09, 2026", date_line="Nov 11, 2026",
               today={**day["today"], "msf": [(mon, "1", tue, "6,000.00", "5.75")],
                      "sdf": [(mon, "1", tue, "1,45,000.00", "5.25")]})
    day["reserves"][0][1] = "November 09, 2026"
    assert _notes(_mmo(day)) == ""
    day["date_line"] = "Nov 10, 2026"
    assert _notes(_mmo(day)) == ("published on 2026-11-10, not on 2026-11-11, the next business day after the as-on "
                                 "date 2026-11-09")
    # the shipped holiday list ends in 2026: the check is skipped and said to be, the rows are kept
    batch = _mmo(_year_end())
    assert len(batch.tables[ro.MMO]) == 20 and batch.tables[ro.MMO]["maturity_date"].max() == T("2027-01-01")
    assert _notes(batch) == ("the business day after the as-on date 2026-12-31 is in a year the settlement calendar "
                             "does not cover; the publication day 2027-01-01 was not checked against it")
    # with a calendar that covers 2027 the check runs
    calendar = BusinessCalendar("test", frozenset({D(2027, 1, 1)}), frozenset({2026, 2027}))
    late = _parse(fx.mmo_page(_year_end()), calendar=calendar)
    assert _notes(late) == ("published on 2027-01-01, not on 2027-01-04, the next business day after the as-on date "
                            "2026-12-31")
    assert _notes(_parse(fx.mmo_page(_year_end("Jan 04, 2027")), calendar=calendar)) == ""
    # called without a context, a parser loads the shipped calendar itself
    release = rr.read_release_page(fx.mmo_page(_year_end()))
    assert len(ro.parse_money_market_operations(release)) == 20


# --- Variable rate auctions: results ----------------------------------------------------------------

REPO_RESULT_TITLE = "Result of the 4-day Variable Rate Repo (VRR) auction held on November 09, 2026"
REPO_RESULT_VALUES = ("4-day", "50,000", "62,000", "50,003", "5.51", "5.53", "80.65")  # made up


def _laf_result(values=fx.LAF_RESULT_VALUES, labels=fx.LAF_REVERSE_REPO_LABELS, title=fx.LAF_RESULT_TITLE,
                date_line="Nov 13, 2026", prid=63968):
    return _page(title, date_line, "", fx.laf_result_table(values, labels), prid=prid, pr_no="2026-2027/1490")


def _result_with(**changes):
    names = ("tenor", "notified", "received", "accepted", "cutoff", "wa", "partial")
    values = list(fx.LAF_RESULT_VALUES)
    for name, value in changes.items():
        values[names.index(name)] = value
    return _laf_result(tuple(values))


def test_laf_result_of_a_reverse_repo():
    batch = _parse(_laf_result())
    df = batch.tables[ro.LAF_RESULT]
    assert list(df.columns) == list(rr.COLUMNS[ro.LAF_RESULT]) and len(df) == 1 and _notes(batch) == ""
    _check(df.iloc[0], dict(
        release_id=63968, release_date=T("2026-11-13"), published=None, auction_date=T("2026-11-13"), line=1,
        direction="reverse_repo", tenor_days=3, notified_cr=75000.0, received_cr=38592.0, accepted_cr=38592.0,
        cutoff_rate_pct=5.49, wa_rate_pct=5.49, partial_pct=None, liquidity_effect_cr=-38592.0,
        press_release_no="2026-2027/1490"))


def test_laf_result_feed_item_and_tenor_forms():
    title = "Result of the 2-day Variable Rate Reverse Repo (VRRR) auction held on November 17, 2026"
    values = ("2-day", "50,000", "7,627", "7,627", "5.49", "5.49", "NA")
    item = dict(title=title, prid=63986, pub="Tue, 17 Nov 2026 10:35:00",
                html=fx.body("", fx.laf_result_table(values), pr_no="2026-2027/1508"))
    feed = _parse(fx.feed([item])).tables[ro.LAF_RESULT]
    page = _parse(_page(title, "Nov 17, 2026", "", fx.laf_result_table(values), prid=63986,
                        pr_no="2026-2027/1508")).tables[ro.LAF_RESULT]
    pd.testing.assert_frame_equal(_without(feed, "published", "source_file"),
                                  _without(page, "published", "source_file"))
    _check(feed.iloc[0], dict(published=T("2026-11-17 10:35"), auction_date=T("2026-11-17"), tenor_days=2,
                              notified_cr=50000.0, received_cr=7627.0, accepted_cr=7627.0, liquidity_effect_cr=-7627.0,
                              press_release_no="2026-2027/1508"))
    overnight = "Result of the Overnight Variable Rate Reverse Repo (VRRR) auction held on November 12, 2026"
    for cell in ("1-day", "Overnight", "1 day", "1-Day"):
        row = _parse(_laf_result(("%s" % cell, *fx.LAF_RESULT_VALUES[1:]), title=overnight,
                                 date_line="Nov 12, 2026")).tables[ro.LAF_RESULT].iloc[0]
        assert (row["tenor_days"], row["auction_date"]) == (1, T("2026-11-12"))
    fourteen = "Result of the 14-day Variable Rate Reverse Repo (VRRR) auction held on November 13, 2026"
    assert _parse(_laf_result(("14 days", *fx.LAF_RESULT_VALUES[1:]), title=fourteen)).tables[ro.LAF_RESULT].iloc[0][
        "tenor_days"] == 14


def test_laf_result_of_a_repo_is_read_only_in_the_wording_recorded_for_it():
    batch = _parse(_laf_result(REPO_RESULT_VALUES, fx.LAF_REPO_LABELS, REPO_RESULT_TITLE, "Nov 09, 2026", 63945))
    _check(batch.tables[ro.LAF_RESULT].iloc[0], dict(
        direction="repo", tenor_days=4, auction_date=T("2026-11-09"), notified_cr=50000.0, received_cr=62000.0,
        accepted_cr=50003.0, cutoff_rate_pct=5.51, wa_rate_pct=5.53, partial_pct=80.65, liquidity_effect_cr=50003.0))
    assert _notes(batch) == ("variable rate repo (injection) result: this layout was known only from descriptions "
                             "when its parser was written, not from a sample; compare the rows with the release")
    # a repo title over the words of a reverse repo, and the other way round
    wrong = _laf_result(REPO_RESULT_VALUES, fx.LAF_REVERSE_REPO_LABELS, REPO_RESULT_TITLE, "Nov 09, 2026", 63945)
    assert "the title says repo but the table uses the words of a reverse repo" in _reason(_parse(wrong))
    wrong = _laf_result(labels=fx.LAF_REPO_LABELS)
    assert "the title says reverse repo but the table uses the words of a repo" in _reason(_parse(wrong))
    mixed = (*fx.LAF_REPO_LABELS[:3], fx.LAF_REVERSE_REPO_LABELS[3], *fx.LAF_REPO_LABELS[4:])
    assert "uses the words of a repo and a reverse repo" in _reason(_parse(_laf_result(
        REPO_RESULT_VALUES, mixed, REPO_RESULT_TITLE, "Nov 09, 2026", 63945)))
    # in a repo the weighted average rate is not below the cut-off
    low = _laf_result(("4-day", "50,000", "62,000", "50,003", "5.51", "5.50", "80.65"), fx.LAF_REPO_LABELS,
                      REPO_RESULT_TITLE, "Nov 09, 2026", 63945)
    assert "weighted average rate 5.5 is below the cut-off 5.51 of a repo" in _reason(_parse(low))


@pytest.mark.parametrize("changes, message", [
    (dict(accepted="40,000"), "failed check: <text 1>: accepted_cr 40000 is above received_cr 38592"),  # L1
    (dict(received="80,000", accepted="75,100", partial="50.00"),
     "failed check: <text 1>: accepted_cr 75100 is more than 0.10% above notified_cr 75000"),  # L2
    (dict(wa="5.50"), "failed check: <text 1>: weighted average rate 5.5 is above the cut-off 5.49 of a reverse "
                      "repo"),  # L3
    (dict(cutoff="25.49", wa="25.49"), "failed check: <text 1>: cutoff_rate_pct 25.49 is outside the rate range"),
    (dict(partial="120"), "failed check: <text 1>: partial_pct 120 is outside the percentage range 0-100"),
    (dict(cutoff="NA"), "failed check: <text 1>: an amount accepted but no cut-off or weighted average rate"),
    (dict(tenor="2-day"), "failed check: <text 1>: the table says 2 days, the title 3"),  # L5
    (dict(notified="-75,000"), "failed check: <text 1>: notified_cr is negative (-75000)"),
    (dict(notified="0"), "failed check: <text 1>: no notified amount"),
    (dict(received="abc"), "cannot parse: <text 1>: 'Total amount of offers received (in ₹ crore)': not a number"),
    (dict(accepted="NA"), "cannot parse: <text 1>: 'Amount accepted (in ₹ crore)': no amount"),
    (dict(tenor="three days"), "cannot parse: <text 1>: 'Tenor': tenor 'three days' is not 'Overnight' or 'N-day'"),
    (dict(tenor="0-day"), "cannot parse: <text 1>: 'Tenor': tenor '0-day' is not"),
])
def test_laf_result_checks(changes, message):
    assert _reason(_parse(_result_with(**changes))).startswith(message)


def test_laf_result_rounding_blank_cells_and_notes():
    # pro-rata rounding lets the accepted amount pass the notified amount a little
    row = _parse(_result_with(received="90,000", accepted="75,026", partial="83.37")).tables[ro.LAF_RESULT].iloc[0]
    assert (row["accepted_cr"], row["partial_pct"], row["liquidity_effect_cr"]) == (75026.0, 83.37, -75026.0)
    for blank in ("NA", "N.A.", "-", ""):
        assert pd.isna(_parse(_result_with(partial=blank)).tables[ro.LAF_RESULT].iloc[0]["partial_pct"])
    # nothing accepted: no rates, and an effect of zero, not minus zero
    batch = _parse(_result_with(accepted="0", cutoff="NA", wa="NA"))
    row = batch.tables[ro.LAF_RESULT].iloc[0]
    assert pd.isna(row["cutoff_rate_pct"]) and str(row["liquidity_effect_cr"]) == "0.0" and _notes(batch) == ""
    assert _notes(_parse(_result_with(received="50,000"))) == ("accepted less than received but no partial "
                                                               "percentage printed")


def test_laf_result_table_layout():
    table = fx.laf_result_table()
    page = _laf_result().replace(table, table.replace("</table>", "<tr><td>Settlement</td><td>T+0</td></tr></table>"))
    batch = _parse(page)
    assert len(batch.tables[ro.LAF_RESULT]) == 1 and _notes(batch) == ("row 8 'Settlement' not recognised; ignored "
                                                                       "(value: 'T+0')")
    no_unit = (fx.LAF_REVERSE_REPO_LABELS[0], "Notified Amount", *fx.LAF_REVERSE_REPO_LABELS[2:])
    assert "'Notified Amount' does not say crore; unit unknown" in _reason(_parse(_laf_result(labels=no_unit)))
    renamed = (*fx.LAF_REVERSE_REPO_LABELS[:4], "Marginal Rate (%)", *fx.LAF_REVERSE_REPO_LABELS[5:])
    batch = _parse(_laf_result(labels=renamed))
    assert "no row for cutoff_rate_pct" in _reason(batch) and "'Marginal Rate (%)' not recognised" in \
        batch.releases.iloc[0]["notes"]
    twice = (*fx.LAF_REVERSE_REPO_LABELS[:5], "Cut off Rate (%)", fx.LAF_REVERSE_REPO_LABELS[6])
    assert "two rows for cutoff_rate_pct" in _reason(_parse(_laf_result(labels=twice)))
    three = _laf_result().replace("<td align='center'>75,000</td>", "<td>:</td><td>75,000</td>")
    assert "row 2 has 3 cells, not a label and a value" in _reason(_parse(three))
    unlabelled = _laf_result().replace(table, table.replace("</table>", "<tr><td></td><td>12</td></tr></table>"))
    assert "row 8 has values but no label (['12'])" in _reason(_parse(unlabelled))
    assert "no table with a 'Tenor' row of a label and a value" in _reason(_parse(_laf_result(
        labels=("Period", *fx.LAF_REVERSE_REPO_LABELS[1:]))))
    two = _page(fx.LAF_RESULT_TITLE, "Nov 13, 2026", "", table, table, prid=63968)
    assert "2 tables with a 'Tenor' row" in _reason(_parse(two))
    other = _page(fx.LAF_RESULT_TITLE, "Nov 13, 2026", "", table,
                  "<table><tr><td>Bank</td><td>Amount</td></tr></table>", prid=63968)
    assert _notes(_parse(other)) == "table 2 of the release (1 rows, beginning 'Bank') not recognised; ignored"
    untitled = _laf_result(title="Result of the 3-day Variable Rate Reverse Repo (VRRR) auction")
    assert "is not of the form 'Result of the 3-day Variable Rate Reverse Repo (VRRR) auction held on" in _reason(
        _parse(untitled))


# --- Variable rate auctions: announcements ----------------------------------------------------------

def _laf_announcement(rows=(fx.LAF_ANNOUNCEMENT_ROW,), title=fx.LAF_ANNOUNCEMENT_TITLE, text=fx.LAF_ANNOUNCEMENT_TEXT,
                      date_line="Nov 16, 2026", prid=63984, **kw):
    return _page(title, date_line, text, fx.laf_announcement_table(rows, **kw), prid=prid, pr_no="2026-2027/1506")


def _announcement_row(**changes):
    names = ("number", "notified", "tenor", "window", "reversal")
    row = list(fx.LAF_ANNOUNCEMENT_ROW)
    for name, value in changes.items():
        row[names.index(name)] = value
    return tuple(row)


OVERNIGHT_TITLE = "RBI to conduct Overnight Variable Rate Reverse Repo (VRRR) auction under LAF on {}"
OVERNIGHT_TEXT = "The Reserve Bank announces a Variable Rate Reverse Repo (VRRR) auction on {}, on the terms below."


def _overnight(auction, weekday, reversal, **kw):
    """An announcement of an overnight auction on ``auction`` (``November 18, 2026``) reversing on ``reversal``."""
    return _laf_announcement((_announcement_row(tenor="1", window="9:30 AM to 10:00 AM", reversal=reversal),),
                             OVERNIGHT_TITLE.format(auction), OVERNIGHT_TEXT.format(f"{weekday}, {auction}"), **kw)


def test_laf_announcement_page_and_feed():
    item = dict(title=fx.LAF_ANNOUNCEMENT_TITLE, prid=63984, pub="Mon, 16 Nov 2026 19:25:00",
                html=fx.body(fx.LAF_ANNOUNCEMENT_TEXT, fx.laf_announcement_table(), pr_no="2026-2027/1506"))
    feed, page = _parse(fx.feed([item])), _parse(_laf_announcement())
    df = feed.tables[ro.LAF_ANNOUNCEMENT]
    assert list(df.columns) == list(rr.COLUMNS[ro.LAF_ANNOUNCEMENT]) and _notes(feed) == _notes(page) == ""
    _check(df.iloc[0], dict(
        release_id=63984, release_date=T("2026-11-16"), published=T("2026-11-16 19:25"), auction_date=T("2026-11-17"),
        line=1, direction="reverse_repo", tenor_days=2, notified_cr=50000.0, window_start="09:30", window_end="10:00",
        reversal_date=T("2026-11-19"), press_release_no="2026-2027/1506"))
    pd.testing.assert_frame_equal(_without(df, "published", "source_file"),
                                  _without(page.tables[ro.LAF_ANNOUNCEMENT], "published", "source_file"))
    # an overnight auction: the title says Overnight, the table 1; the hour without its leading zero
    batch = _parse(_overnight("November 18, 2026", "Wednesday", "November 19, 2026 (Thursday)", prid=63991))
    _check(batch.tables[ro.LAF_ANNOUNCEMENT].iloc[0], dict(auction_date=T("2026-11-18"), tenor_days=1,
                                                           window_start="09:30", window_end="10:00",
                                                           reversal_date=T("2026-11-19")))
    assert _notes(batch) == ""


@pytest.mark.parametrize("window, start, end", [
    ("02:30 PM to 03:00 PM", "14:30", "15:00"), ("11:45 am to 12:15 pm", "11:45", "12:15"),
    ("12:05 AM to 12:35 AM", "00:05", "00:35"), ("9:30AM to 10:00AM", "09:30", "10:00"),
])
def test_laf_announcement_window_in_24_hour_time(window, start, end):
    row = _parse(_laf_announcement((_announcement_row(window=window),))).tables[ro.LAF_ANNOUNCEMENT].iloc[0]
    assert (row["window_start"], row["window_end"]) == (start, end)


@pytest.mark.parametrize("row, message", [
    (dict(reversal="November 20, 2026 (Friday)"), "failed check: <text 1>: row 2: reversal on 2026-11-20 is not the "
                                                  "auction date 2026-11-17 plus the tenor of 2 days"),
    (dict(tenor="3", reversal="November 20, 2026 (Friday)"), "failed check: <text 1>: the table says 3 days, the "
                                                             "title 2"),
    (dict(window="10:00 AM to 09:30 AM"), "failed check: <text 1>: row 2: the window 10:00 to 09:30 does not run "
                                          "forwards"),
    (dict(notified="0"), "failed check: <text 1>: row 2: notified amount 0 is not above 0"),
    (dict(window="09:30 to 10:00"), "cannot parse: <text 1>: row 2: window '09:30 to 10:00' is not '09:30 AM to "
                                    "10:00 AM'"),
    (dict(window="13:30 PM to 14:00 PM"), "cannot parse: <text 1>: row 2: 13:30 PM is not a time"),
    (dict(notified="fifty thousand"), "cannot parse: <text 1>: row 2: not a number"),
    (dict(tenor="-"), "cannot parse: <text 1>: row 2: no tenor"),
    (dict(reversal="19/11/2026"), "cannot parse: <text 1>: row 2: not a date"),
    (dict(reversal=""), "cannot parse: <text 1>: row 2: no date of reversal"),
])
def test_laf_announcement_row_checks(row, message):
    assert _reason(_parse(_laf_announcement((_announcement_row(**row),)))).startswith(message)


def test_laf_announcement_title_text_and_table_must_agree():
    other_day = fx.LAF_ANNOUNCEMENT_TEXT.replace("Tuesday, November 17", "Wednesday, November 18")
    assert "the title says the auction is on 2026-11-17, the text 2026-11-18" in _reason(
        _parse(_laf_announcement(text=other_day)))
    assert "no 'auction on <date>' sentence to check the title's date against" in _reason(
        _parse(_laf_announcement(text="An auction will be held.")))
    repo_text = fx.LAF_ANNOUNCEMENT_TEXT.replace("Reverse Repo (VRRR)", "Repo (VRR)")
    assert "the title announces a reverse repo, the text a repo" in _reason(_parse(_laf_announcement(text=repo_text)))
    header = ("Sl. No.", "Notified Amount", *fx.LAF_ANNOUNCEMENT_HEADER[2:])
    assert "the amount is not stated in crore or the tenor not in days" in _reason(
        _parse(_laf_announcement(header=header)))
    header = (*fx.LAF_ANNOUNCEMENT_HEADER[:4], "Reversal")
    assert "no table with the columns of the auction table (notifiedamount, tenor, windowtiming, dateofreversal)" \
        in _reason(_parse(_laf_announcement(header=header)))
    assert "no rows under the header of the auction table" in _reason(_parse(_laf_announcement(())))
    untitled = _laf_announcement(title="RBI to conduct Variable Rate Reverse Repo (VRRR) auction under LAF")
    assert "is not of the form 'RBI to conduct 2-day Variable Rate Reverse Repo" in _reason(_parse(untitled))


def test_laf_announcement_of_a_repo_and_of_two_auctions():
    title = "RBI to conduct 4-day Variable Rate Repo (VRR) auction under LAF on November 09, 2026"
    text = "The Reserve Bank announces a Variable Rate Repo (VRR) auction on Monday, November 09, 2026, as below."
    row = _announcement_row(tenor="4", reversal="November 13, 2026 (Friday)")
    batch = _parse(_laf_announcement((row,), title, text, "Nov 06, 2026", 63943))
    _check(batch.tables[ro.LAF_ANNOUNCEMENT].iloc[0], dict(direction="repo", auction_date=T("2026-11-09"),
                                                           tenor_days=4, reversal_date=T("2026-11-13")))
    assert _notes(batch).startswith("variable rate repo (injection) announcement: this layout was known only from")
    rows = (fx.LAF_ANNOUNCEMENT_ROW, ("2", "25,000", "7", "11:00 AM to 11:30 AM", "November 24, 2026 (Tuesday)"))
    batch = _parse(_laf_announcement(rows))  # the second tenor differs from the title's: not checked for two rows
    df = batch.tables[ro.LAF_ANNOUNCEMENT]
    assert df["line"].tolist() == [1, 2] and df["tenor_days"].tolist() == [2, 7]
    assert df["notified_cr"].tolist() == [50000.0, 25000.0] and df["window_start"].tolist() == ["09:30", "11:00"]
    assert "2 auctions in one release (not seen in any sample)" in _notes(batch)
    assert "row 3: the reversal date 2026-11-24 (Tuesday) is not a business day" in _notes(batch)  # Guru Nanak Jayanti


def test_laf_announcement_reversal_day_notes():
    wrong_weekday = _announcement_row(reversal="November 19, 2026 (Friday)")
    assert _notes(_parse(_laf_announcement((wrong_weekday,)))) == ("row 2: the reversal date 2026-11-19 is a "
                                                                    "Thursday, not a Friday")
    weekend = _overnight("November 13, 2026", "Friday", "November 14, 2026 (Saturday)")
    assert _notes(_parse(weekend)) == ("row 2: the reversal date 2026-11-14 (Saturday) is not a business day of the "
                                       "settlement calendar")
    holiday = _overnight("November 09, 2026", "Monday", "November 10, 2026 (Tuesday)")
    assert _notes(_parse(holiday)) == ("row 2: the reversal date 2026-11-10 (Tuesday) is not a business day of the "
                                       "settlement calendar")
    # a reversal in a year the holiday list does not reach: not checked, said so, and the row is kept
    new_year = _overnight("December 31, 2026", "Thursday", "January 01, 2027 (Friday)")
    batch = _parse(new_year)
    assert batch.tables[ro.LAF_ANNOUNCEMENT].iloc[0]["reversal_date"] == T("2027-01-01")
    assert _notes(batch) == ("row 2: the reversal date 2027-01-01 is in a year the settlement calendar does not "
                             "cover; not checked against its holidays")
    calendar = BusinessCalendar("test", frozenset({D(2027, 1, 1)}), frozenset({2026, 2027}))
    assert "2027-01-01 (Friday) is not a business day" in _notes(_parse(new_year, calendar=calendar))
    open_day = BusinessCalendar("test", frozenset(), frozenset({2026, 2027}))
    assert _notes(_parse(new_year, calendar=open_day)) == ""


def test_laf_announcement_wording_variants_read_the_same():
    plain = _parse(_laf_announcement()).tables[ro.LAF_ANNOUNCEMENT]
    variants = [
        _laf_announcement(text="A Variable Rate Reverse Repo (VRRR) auction on November 17, 2026 is announced."),
        _laf_announcement((_announcement_row(window="9:30 a.m. to 10:00 a.m.", reversal="Nov 19, 2026"),)),
        _laf_announcement(header=("Sr. No.", "Notified Amount (in ₹ Crore)", "Tenor (days)", "Window  Timing",
                                  "Date of Reversal")),
        _laf_announcement(header=fx.LAF_ANNOUNCEMENT_HEADER[1:], rows=(fx.LAF_ANNOUNCEMENT_ROW[1:],)),  # no Sl. No.
    ]
    for page in variants:
        batch = _parse(page)
        assert _notes(batch) == ""
        pd.testing.assert_frame_equal(batch.tables[ro.LAF_ANNOUNCEMENT], plain)


def test_laf_announcement_unknown_column_and_remark_row_are_noted():
    header = (*fx.LAF_ANNOUNCEMENT_HEADER, "Remarks")
    batch = _parse(_laf_announcement(((*fx.LAF_ANNOUNCEMENT_ROW, "none"),), header=header))
    assert len(batch.tables[ro.LAF_ANNOUNCEMENT]) == 1
    assert _notes(batch) == "column 'Remarks' of the auction table not recognised; ignored (1 cells)"
    remark = '<tr><td colspan="5">A remark under the table.</td></tr>'
    batch = _parse(_laf_announcement(after=remark))
    assert len(batch.tables[ro.LAF_ANNOUNCEMENT]) == 1
    assert _notes(batch) == ("row 3 of the auction table spans it and is not a data row; ignored ('A remark under "
                             "the table.')")


# --- Open market operations: results ----------------------------------------------------------------

OMO_SALE_TITLE = "Detailed Result: OMO Sale Auction held on Sep 28, 2026 and Settlement on Sep 29, 2026"
OMO_SALE_SECURITIES = [  # RBI sells to the best bids: weighted average yield <= cut-off yield, price >= cut-off
    dict(name="6.45% GS 2029", values=("120", "20,000", "30", "9,000", "6.1350", "100.97", "6.1200", "101.02",
                                       "75.50")),
    dict(name="7.41% GS 2036", values=("90", "15,500", "40", "11,000", "6.7500", "104.95", "6.7300", "105.10", "NA")),
    dict(name="7.06% GS 2046", values=("35", "6,500", "1", "5,000", "7.3200", "97.20", "7.3200", "97.20", "NA")),
]
OMO_FIELDS = ("offers_received_count", "offered_cr", "offers_accepted_count", "accepted_cr", "cutoff_yield",
              "cutoff_price", "way", "wap", "partial_allotment_pct")


def _omo_result(securities=None, summary=fx.OMO_SUMMARY, title=fx.OMO_RESULT_TITLE, date_line="Mar 13, 2026",
                operation="Purchase", summary_labels=fx.OMO_SUMMARY_LABELS, detail_labels=fx.OMO_DETAIL_LABELS):
    tables = fx.omo_result_tables(fx.omo_summary_table(summary, summary_labels),
                                  fx.omo_detail_table(securities, detail_labels), operation)
    return _page(title, date_line, "", tables, prid=62379, pr_no="2025-2026/2263")


def _securities(index, **changes):
    securities = copy.deepcopy(fx.OMO_SECURITIES)
    values = list(securities[index]["values"])
    for name, value in changes.items():
        values[OMO_FIELDS.index(name)] = value
    securities[index]["values"] = tuple(values)
    return securities


def _sale(**kw):
    kw.setdefault("securities", OMO_SALE_SECURITIES)
    kw.setdefault("summary_labels", fx.OMO_SALE_SUMMARY_LABELS)
    kw.setdefault("detail_labels", fx.OMO_SALE_DETAIL_LABELS)
    return _omo_result(title=OMO_SALE_TITLE, date_line="Sep 28, 2026", operation="Sale", **kw)


def test_omo_result_of_a_purchase():
    batch = _parse(_omo_result())
    df = batch.tables[ro.OMO_RESULT]
    assert list(df.columns) == list(rr.COLUMNS[ro.OMO_RESULT]) and len(df) == 3 and _notes(batch) == ""
    assert df["line"].tolist() == [1, 2, 3] and df["operation"].eq("purchase").all()
    assert df["security"].tolist() == df["name_key"].tolist() == ["6.45% GS 2029", "7.41% GS 2036", "7.06% GS 2046"]
    assert df["auction_date"].eq(T("2026-03-13")).all() and df["settlement_date"].eq(T("2026-03-16")).all()
    assert df["release_date"].eq(T("2026-03-13")).all() and df["press_release_no"].eq("2025-2026/2263").all()
    _check(df.iloc[0], dict(offers_received_count=120, offered_cr=20000.0, offers_accepted_count=30,
                            accepted_cr=9000.0, cutoff_yield=6.12, cutoff_price=101.02, way=6.135, wap=100.97,
                            partial_allotment_pct=75.5, liquidity_effect_cr=9000.0))
    _check(df.iloc[2], dict(offers_received_count=35, offered_cr=6500.0, offers_accepted_count=1, accepted_cr=5000.0,
                            cutoff_yield=7.32, cutoff_price=97.2, way=7.32, wap=97.2, partial_allotment_pct=None,
                            liquidity_effect_cr=5000.0))
    # the summary is repeated on every row, and the columns add up to it
    assert df["aggregate_notified_cr"].eq(25000.0).all() and df["total_offered_cr"].eq(42000.0).all()
    assert df["total_accepted_cr"].eq(25000.0).all()
    assert (df["offered_cr"].sum(), df["accepted_cr"].sum()) == (42000.0, 25000.0)
    item = dict(title=fx.OMO_RESULT_TITLE, prid=62379, pub="Fri, 13 Mar 2026 14:10:00",
                html=fx.body("", fx.omo_result_tables(), pr_no="2025-2026/2263"))
    feed = _parse(fx.feed([item])).tables[ro.OMO_RESULT]
    pd.testing.assert_frame_equal(_without(feed, "published", "source_file"), _without(df, "published", "source_file"))


@pytest.mark.parametrize("index, changes, message", [
    (0, dict(offered_cr="20,001"), "failed check: <text 1>: offered_cr sums to 42001 over the securities but the "
                                   "summary says 42000"),  # O1
    (0, dict(accepted_cr="9,001"), "failed check: <text 1>: accepted_cr sums to 25001 over the securities but the "
                                   "summary says 25000"),  # O2
    (2, dict(accepted_cr="7,000"), "failed check: <text 1> [7.06% GS 2046]: accepted_cr 7000 is above offered_cr "
                                   "6500"),  # O4
    (2, dict(offers_accepted_count="40"), "failed check: <text 1> [7.06% GS 2046]: offers_accepted_count 40 is above "
                                          "offers_received_count 35"),
    (1, dict(cutoff_yield="26.7300", way="26.7500"), "failed check: <text 1> [7.41% GS 2036]: cutoff_yield 26.73 is "
                                                     "outside the yield range 0-20"),
    (1, dict(cutoff_price="15.10", wap="14.95"), "failed check: <text 1> [7.41% GS 2036]: cutoff_price 15.1 is "
                                                 "outside the price range 50-150"),
    (0, dict(partial_allotment_pct="175.50"), "failed check: <text 1> [6.45% GS 2029]: partial_allotment_pct 175.5 "
                                              "is outside the percentage range 0-100"),
    (0, dict(way="6.1100"), "failed check: <text 1> [6.45% GS 2029]: weighted average yield 6.11 is below the "
                            "cut-off yield 6.12 of a purchase"),  # O5
    (0, dict(wap="101.05"), "failed check: <text 1> [6.45% GS 2029]: weighted average price 101.05 is above the "
                            "cut-off price 101.02 of a purchase"),
    (1, dict(cutoff_price="NA"), "failed check: <text 1> [7.41% GS 2036]: an amount accepted but no cut-off yield "
                                 "or price"),  # O6
    (1, dict(offered_cr="-15,500"), "failed check: <text 1> [7.41% GS 2036]: offered_cr is negative"),
    (1, dict(offers_received_count="9.5"), "cannot parse: <text 1> [7.41% GS 2036] 'No. of offers received': not a "
                                           "count"),
    (1, dict(accepted_cr="NA"), "cannot parse: <text 1> [7.41% GS 2036]: no accepted_cr"),
    (1, dict(cutoff_yield="6.73%%"), "cannot parse: <text 1> [7.41% GS 2036] 'Cut off yield (%)': not a number"),
])
def test_omo_result_checks(index, changes, message):
    assert _reason(_parse(_omo_result(_securities(index, **changes)))).startswith(message)


def test_omo_result_total_accepted_against_the_notified_amount():
    more = _securities(1, accepted_cr="11,100")  # RBI may buy marginally more than it notified
    batch = _parse(_omo_result(more, ("₹25,000 crore", "₹42,000 crore", "₹25,100 crore")))
    assert batch.tables[ro.OMO_RESULT]["total_accepted_cr"].eq(25100.0).all()
    assert _notes(batch) == "total accepted 25100 is above the aggregate amount notified 25000"
    much = _securities(1, accepted_cr="11,300")
    assert "total accepted 25300 is more than 1% above the aggregate amount notified 25000" in _reason(
        _parse(_omo_result(much, ("₹25,000 crore", "₹42,000 crore", "₹25,300 crore"))))
    # nothing accepted in one security: dashes for its cut-offs
    none = _securities(2, offers_accepted_count="0", accepted_cr="0", cutoff_yield="-", cutoff_price="-", way="-",
                       wap="-")
    batch = _parse(_omo_result(none, ("₹25,000 crore", "₹42,000 crore", "₹20,000 crore")))
    row = batch.tables[ro.OMO_RESULT].iloc[2]
    assert row["accepted_cr"] == 0.0 and pd.isna(row["cutoff_yield"]) and str(row["liquidity_effect_cr"]) == "0.0"


def test_omo_result_title_text_and_summary():
    late = "Detailed Result: OMO Purchase Auction held on Mar 13, 2026 and Settlement on Mar 12, 2026"
    assert "settlement 2026-03-12 before the auction 2026-03-13" in _reason(_parse(_omo_result(title=late)))
    assert "the title says OMO purchase, the text OMO sale" in _reason(_parse(_omo_result(operation="Sale")))
    short = "Detailed Result: OMO Purchase Auction held on Mar 13, 2026"
    assert "is not of the form 'Detailed Result: OMO Purchase Auction held on" in _reason(_parse(_omo_result(
        title=short)))
    for cell in ("50,000", "₹25,000", "25,000 crore", "₹ crore"):
        assert "is not an amount of the form '₹50,000 crore'" in _reason(_parse(_omo_result(
            summary=(cell, "₹42,000 crore", "₹25,000 crore"))))
    row = _parse(_omo_result(summary=("₹ 25,000.00 Crore", "₹42,000 crore", "₹25,000 crore"))).tables[
        ro.OMO_RESULT].iloc[0]
    assert row["aggregate_notified_cr"] == 25000.0
    labels = (fx.OMO_SUMMARY_LABELS[0], "Amount offered", fx.OMO_SUMMARY_LABELS[2])
    batch = _parse(_omo_result(summary_labels=labels))
    assert "summary: no row for total_offered_cr" in _reason(batch)
    assert "row 2 'Amount offered' not recognised; ignored (value: '₹42,000 crore')" in batch.releases.iloc[0]["notes"]
    only_detail = _page(fx.OMO_RESULT_TITLE, "Mar 13, 2026", "OMO Purchase", fx.omo_detail_table(), prid=62379)
    assert "no table with an 'Aggregate Amount ... notified' row" in _reason(_parse(only_detail))
    only_summary = _page(fx.OMO_RESULT_TITLE, "Mar 13, 2026", "OMO Purchase", fx.omo_summary_table(), prid=62379)
    assert "no table with a first row headed 'Security'" in _reason(_parse(only_summary))
    untold = _page(fx.OMO_RESULT_TITLE, "Mar 13, 2026", "", fx.omo_summary_table(), fx.omo_detail_table(), prid=62379)
    assert _notes(_parse(untold)) == "the text names no OMO purchase or sale to check the title's purchase against"


def test_omo_result_detail_table_layout():
    detail = fx.omo_detail_table()
    extra = detail.replace("</table>", "<tr><td>Devolvement (₹ in crore)</td><td>1</td><td>2</td><td>3</td></tr>"
                                       "</table>")
    batch = _parse(_page(fx.OMO_RESULT_TITLE, "Mar 13, 2026", "", fx.omo_result_tables(detail=extra), prid=62379))
    assert len(batch.tables[ro.OMO_RESULT]) == 3
    assert _notes(batch) == ("row 11 'Devolvement (₹ in crore)' not recognised; ignored (value: ['1', '2', '3'])")
    empty = detail.replace("</table>", "<tr><td></td><td></td><td>-</td><td>&nbsp;</td></tr></table>")
    batch = _parse(_page(fx.OMO_RESULT_TITLE, "Mar 13, 2026", "", fx.omo_result_tables(detail=empty), prid=62379))
    assert len(batch.tables[ro.OMO_RESULT]) == 3 and _notes(batch) == ""  # a row that prints nothing
    unlabelled = detail.replace("</table>", "<tr><td></td><td>1</td><td>2</td><td>3</td></tr></table>")
    assert "details: row 11 has values but no label (['1', '2', '3'])" in _reason(_parse(_page(
        fx.OMO_RESULT_TITLE, "Mar 13, 2026", "", fx.omo_result_tables(detail=unlabelled), prid=62379)))
    # a row must hold one cell for each security: with one more or one fewer the values would shift
    wide = detail.replace("<td>NA</td></tr>", "<td>NA</td><td>9</td></tr>")
    assert "details row 10 ('Partial allotment % of competitive offers at cut off price') is not a label and one " \
           "cell for each of the 3 securities (['75.50', 'NA', 'NA', '9'])" in _reason(_parse(_page(
               fx.OMO_RESULT_TITLE, "Mar 13, 2026", "", fx.omo_result_tables(detail=wide), prid=62379)))
    short = detail.replace("<td>6.1350</td>", "")  # the first weighted average yield is missing: two cells left
    assert "details row 8 ('Weighted average yield (%)') is not a label and one cell for each of the 3 securities " \
           "(['6.7500', '7.3200'])" in _reason(_parse(_page(fx.OMO_RESULT_TITLE, "Mar 13, 2026", "",
                                                           fx.omo_result_tables(detail=short), prid=62379)))
    merged = detail.replace("<td>120</td><td>90</td>", '<td colspan="2">210</td>')
    assert "details row 2 ('No. of offers received') is not a label and one cell for each of the 3 securities" \
        in _reason(_parse(_page(fx.OMO_RESULT_TITLE, "Mar 13, 2026", "", fx.omo_result_tables(detail=merged),
                                prid=62379)))
    remark = detail.replace("</table>", '<tr><td colspan="4">A remark.</td></tr></table>')
    batch = _parse(_page(fx.OMO_RESULT_TITLE, "Mar 13, 2026", "", fx.omo_result_tables(detail=remark), prid=62379))
    assert len(batch.tables[ro.OMO_RESULT]) == 3 and "row 11 'A remark.' not recognised; ignored" in _notes(batch)
    missing = tuple(label for label in fx.OMO_DETAIL_LABELS if not label.startswith("Weighted average price"))
    securities = [dict(s, values=s["values"][:7] + s["values"][8:]) for s in fx.OMO_SECURITIES]
    assert "details: no row for wap" in _reason(_parse(_omo_result(securities, detail_labels=missing)))
    no_unit = ("No. of offers received", "Total amount (face value) offered", *fx.OMO_DETAIL_LABELS[2:])
    assert "'Total amount (face value) offered' does not say crore" in _reason(_parse(_omo_result(
        detail_labels=no_unit)))
    unnamed = [dict(fx.OMO_SECURITIES[0]), dict(fx.OMO_SECURITIES[1], name=""), dict(fx.OMO_SECURITIES[2])]
    assert "empty security name in the header" in _reason(_parse(_omo_result(unnamed)))
    strange = [dict(fx.OMO_SECURITIES[0], name="Bond A"), *fx.OMO_SECURITIES[1:]]
    batch = _parse(_omo_result(strange))
    assert pd.isna(batch.tables[ro.OMO_RESULT].iloc[0]["name_key"])
    assert _notes(batch) == "<text 1> [Bond A]: not a security name this package knows; no name key"


def test_omo_result_of_a_sale_is_read_only_with_bids_for_offers():
    batch = _parse(_sale())
    df = batch.tables[ro.OMO_RESULT]
    assert df["operation"].eq("sale").all() and df["liquidity_effect_cr"].tolist() == [-9000.0, -11000.0, -5000.0]
    assert df["auction_date"].eq(T("2026-09-28")).all() and df["settlement_date"].eq(T("2026-09-29")).all()
    _check(df.iloc[0], dict(offers_received_count=120, offered_cr=20000.0, offers_accepted_count=30,
                            accepted_cr=9000.0, cutoff_yield=6.135, cutoff_price=100.97, way=6.12, wap=101.02))
    assert _notes(batch) == ("OMO sale result: this layout was known only from descriptions when its parser was "
                             "written, not from a sample; compare the rows with the release")
    # the order of cut-off and weighted average is the reverse of a purchase
    reason = _reason(_parse(_sale(securities=fx.OMO_SECURITIES)))
    assert "weighted average yield 6.135 is above the cut-off yield 6.12 of a sale" in reason
    # a sale title over the labels of a purchase is not read
    assert "the title says sale but the tables use the words of a purchase" in _reason(_parse(_sale(
        summary_labels=fx.OMO_SUMMARY_LABELS, detail_labels=fx.OMO_DETAIL_LABELS)))
    assert "the title says sale but the tables use the words of a purchase and a sale" in _reason(_parse(_sale(
        summary_labels=fx.OMO_SUMMARY_LABELS)))
    assert "the title says purchase but the tables use the words of a sale" in _reason(_parse(_omo_result(
        detail_labels=fx.OMO_SALE_DETAIL_LABELS, summary_labels=fx.OMO_SALE_SUMMARY_LABELS)))


# --- Open market operations: announcements ----------------------------------------------------------

def _omo_announcement(rows=None, text=fx.OMO_ANNOUNCEMENT_TEXT, after=fx.OMO_ANNOUNCEMENT_AFTER,
                      title=fx.OMO_ANNOUNCEMENT_TITLE, **kw):
    return _page(title, "Mar 11, 2026", text, fx.omo_announcement_table(rows, **kw), prid=62367,
                 pr_no="2025-2026/2251", after=after)


def test_omo_announcement():
    batch = _parse(_omo_announcement())
    df = batch.tables[ro.OMO_ANNOUNCEMENT]
    assert list(df.columns) == list(rr.COLUMNS[ro.OMO_ANNOUNCEMENT]) and len(df) == 3 and _notes(batch) == ""
    assert df["isin"].tolist() == ["IN0020190362", "IN0020220102", "IN0020160068"]  # a trailing space is dropped
    assert df["security"].tolist() == df["name_key"].tolist() == ["6.45% GS 2029", "7.41% GS 2036", "7.06% GS 2046"]
    assert df["maturity_date"].dt.strftime("%Y-%m-%d").tolist() == ["2029-10-07", "2036-12-19", "2046-10-10"]
    assert df["aggregate_notified_cr"].eq(25000.0).all()  # one cell spanning every row
    _check(df.iloc[1], dict(release_id=62367, release_date=T("2026-03-11"), line=2, operation="purchase",
                            auction_date=T("2026-03-13"), settlement_date=T("2026-03-16"), auction_method="multiple",
                            programme_pr_no="2025-26/2228", programme_date=T("2026-03-06"),
                            press_release_no="2025-2026/2251"))
    # the result of the same auction names the same securities in the same order
    result = _parse(_omo_result()).tables[ro.OMO_RESULT]
    assert result["security"].tolist() == df["security"].tolist()
    assert result["auction_date"].iloc[0] == df["auction_date"].iloc[0]
    item = dict(title=fx.OMO_ANNOUNCEMENT_TITLE, prid=62367, pub="Wed, 11 Mar 2026 17:35:00",
                html=fx.body(fx.OMO_ANNOUNCEMENT_TEXT, fx.omo_announcement_table(), pr_no="2025-2026/2251",
                             after=fx.OMO_ANNOUNCEMENT_AFTER))
    feed = _parse(fx.feed([item])).tables[ro.OMO_ANNOUNCEMENT]
    pd.testing.assert_frame_equal(_without(feed, "published", "source_file"), _without(df, "published", "source_file"))
    assert feed["published"].eq(T("2026-03-11 17:35")).all()


def _announced(index, **changes):
    names = ("number", "isin", "security", "maturity")
    rows = [list(r) for r in fx.OMO_ANNOUNCED]
    for name, value in changes.items():
        rows[index][names.index(name)] = value
    return rows


@pytest.mark.parametrize("kw, message", [
    (dict(amount="₹30,000 crore"), "failed check: <text 1>: row 2 [6.45% GS 2029]: the table's amount 30000 is not "
                                   "the 25000 of the text"),
    (dict(amount="30,000"), "cannot parse: <text 1>: row 2 [6.45% GS 2029]: '30,000' is not an amount of the form"),
    (dict(rows=_announced(1, isin="IN00202201")), "cannot parse: <text 1>: row 3 [7.41% GS 2036]: 'IN00202201' is "
                                                  "not an ISIN"),
    (dict(rows=_announced(1, isin="US0020220102")), "cannot parse: <text 1>: row 3 [7.41% GS 2036]: 'US0020220102' "
                                                    "is not an ISIN"),
    (dict(rows=_announced(0, maturity="7-Oct-2025")), "failed check: <text 1>: row 2 [6.45% GS 2029]: maturity "
                                                      "2025-10-07 is not after the auction 2026-03-13"),
    (dict(rows=_announced(0, maturity="2029-10-07")), "cannot parse: <text 1>: row 2 [6.45% GS 2029]: not a date"),
    (dict(rows=_announced(0, maturity="")), "cannot parse: <text 1>: row 2 [6.45% GS 2029]: no date of maturity"),
    (dict(rows=_announced(2, security="")), "cannot parse: <text 1>: row 4 []: no security name"),
    (dict(rows=[]), "cannot parse: <text 1>: no securities under the header of the table"),
    (dict(text=fx.OMO_ANNOUNCEMENT_TEXT + " A second tranche for an aggregate amount of ₹25,000 crore on March 20, "
                                          "2026 follows."),
     "cannot parse: <text 1>: 2 sentences 'aggregate amount of ₹... crore on <date>' (one expected"),
    (dict(text="An OMO purchase auction will be held."), "cannot parse: <text 1>: 0 sentences 'aggregate amount"),
    (dict(text=fx.OMO_ANNOUNCEMENT_TEXT.replace("OMO purchase", "OMO sale")),
     "cannot parse: <text 1>: the title says OMO purchase, the text OMO sale"),
    (dict(after=fx.OMO_ANNOUNCEMENT_AFTER.replace("March 16, 2026", "March 12, 2026")),
     "failed check: <text 1>: settlement 2026-03-12 before the auction 2026-03-13"),
])
def test_omo_announcement_checks(kw, message):
    assert _reason(_parse(_omo_announcement(**kw))).startswith(message)


def test_omo_announcement_notes_and_dates():
    batch = _parse(_omo_announcement(after=""))
    assert batch.tables[ro.OMO_ANNOUNCEMENT]["settlement_date"].isna().all()
    assert _notes(batch) == "no settlement date in the text ('SGL account by ... on <date>'); left blank"
    batch = _parse(_omo_announcement(_announced(0, isin="IN0020190363")))
    assert _notes(batch) == "<text 1>: row 2 [6.45% GS 2029]: the check digit of IN0020190363 is wrong"
    batch = _parse(_omo_announcement(_announced(0, maturity="7-Oct-2030")))
    assert _notes(batch) == "<text 1>: row 2 [6.45% GS 2029]: the name says 2029, the date of maturity 2030-10-07"
    batch = _parse(_omo_announcement(_announced(0, security="Bond A")))
    assert _notes(batch) == "<text 1>: row 2 [Bond A]: not a security name this package knows; no name key"
    for cell in ("07-Oct-2029", "7-October-2029", "October 7, 2029"):
        df = _parse(_omo_announcement(_announced(0, maturity=cell))).tables[ro.OMO_ANNOUNCEMENT]
        assert df["maturity_date"].iloc[0] == T("2029-10-07")
    plain = fx.OMO_ANNOUNCEMENT_TEXT.replace("Further to Press Release 2025-26/2228 dated March 06, 2026, the", "The")
    row = _parse(_omo_announcement(text=plain.replace("multiple price", "uniform price"))).tables[
        ro.OMO_ANNOUNCEMENT].iloc[0]
    assert pd.isna(row["programme_pr_no"]) and pd.isna(row["programme_date"]) and row["auction_method"] == "uniform"
    remark = '<tr><td colspan="5">The list may change.</td></tr>'
    batch = _parse(_page(fx.OMO_ANNOUNCEMENT_TITLE, "Mar 11, 2026", fx.OMO_ANNOUNCEMENT_TEXT,
                         fx.omo_announcement_table(after=remark), prid=62367, after=fx.OMO_ANNOUNCEMENT_AFTER))
    assert len(batch.tables[ro.OMO_ANNOUNCEMENT]) == 3
    assert _notes(batch) == ("row 5 of the securities table spans it and is not a data row; ignored ('The list may "
                             "change.')")


def test_omo_announcement_of_a_sale():
    text = ("The Reserve Bank will hold an OMO sale auction of Government of India securities for an aggregate "
            "amount of ₹25,000 crore on September 28, 2026, using the multiple price method.")
    batch = _parse(_omo_announcement(text=text, after="", title="RBI announces OMO Sale of Government of India "
                                                                "Securities"))
    df = batch.tables[ro.OMO_ANNOUNCEMENT]
    assert df["operation"].eq("sale").all() and df["auction_date"].eq(T("2026-09-28")).all()
    assert _notes(batch) == (
        "no settlement date in the text ('SGL account by ... on <date>'); left blank; "
        "OMO sale announcement: this layout was known only from descriptions when its parser was written, not from "
        "a sample; compare the rows with the release")


# --- Issuance calendars -----------------------------------------------------------------------------

def _gsec_calendar(text=fx.GSEC_CALENDAR_TEXT, title=fx.GSEC_CALENDAR_TITLE, **kw):
    return _page(title, "Sep 25, 2026", text, fx.gsec_calendar_table(**kw), prid=63668, pr_no="2026-2027/1190")


def _weeks(index, week=None, amount=None, lines=None):
    weeks = [list(w) for w in fx.GSEC_CALENDAR_WEEKS]
    for position, value in enumerate((week, amount, lines)):
        if value is not None:
            weeks[index][position] = value
    return weeks


def test_gsec_calendar_gives_one_row_per_allocation_line():
    batch = _parse(_gsec_calendar())
    df = batch.tables[ro.GSEC_CALENDAR]
    assert list(df.columns) == list(rr.COLUMNS[ro.GSEC_CALENDAR]) and len(df) == 10 and _notes(batch) == ""
    assert df["line"].tolist() == list(range(1, 11)) and df["week_no"].tolist() == [1, 1, 1, 1, 2, 2, 3, 4, 4, 5]
    assert df["allocation_no"].tolist() == [1, 2, 3, 4, 1, 2, 1, 1, 2, 1]
    assert df["period_start"].eq(T("2026-10-01")).all() and df["period_end"].eq(T("2027-03-31")).all()
    assert df["release_date"].eq(T("2026-09-25")).all() and df["total_cr"].eq(174000.0).all()
    # the three forms a week is printed in: one month, two months, two years
    weeks = df.drop_duplicates("week_no").set_index("week_no")
    assert weeks["week_start"].dt.strftime("%Y-%m-%d").tolist() == ["2026-09-28", "2026-10-05", "2026-10-12",
                                                                    "2026-12-28", "2027-01-04"]
    assert weeks["week_end"].dt.strftime("%Y-%m-%d").tolist() == ["2026-10-02", "2026-10-09", "2026-10-16",
                                                                  "2027-01-01", "2027-01-08"]
    assert weeks["week_amount_cr"].tolist() == [33000.0, 36000.0, 34000.0, 36000.0, 35000.0]
    first = df[df["week_no"] == 1]  # the week's cells span its four lines
    assert list(zip(first["tenor_years"], first["security_type"], first["amount_cr"], strict=True)) == [
        (3, "GS", 9000.0), (7, "GS", 12000.0), (30, "GS", 9000.0), (30, "SGrB", 3000.0)]
    assert df.groupby("week_no")["amount_cr"].sum().tolist() == weeks["week_amount_cr"].tolist()  # G1
    assert weeks["week_amount_cr"].sum() == 174000.0  # G2
    item = dict(title=fx.GSEC_CALENDAR_TITLE, prid=63668, pub="Fri, 25 Sep 2026 17:30:00",
                html=fx.body(fx.GSEC_CALENDAR_TEXT, fx.gsec_calendar_table(), pr_no="2026-2027/1190"))
    feed = _parse(fx.feed([item])).tables[ro.GSEC_CALENDAR]
    pd.testing.assert_frame_equal(_without(feed, "published", "source_file"), _without(df, "published", "source_file"))


@pytest.mark.parametrize("kw, message", [
    (dict(weeks=_weeks(1, amount="37,000")), "failed check: <text 1>: week 2 ('October 05-09, 2026', row 8): its "
                                             "allocation lines add up to 36000, not the week's 37000"),  # G1
    (dict(total="1,75,000"), "failed check: <text 1>: the weeks add up to 174000 but the Total row says 175000"),
    (dict(weeks=_weeks(1, week="October 06-09, 2026")), "failed check: <text 1>: week 2 ('October 06-09, 2026', row "
                                                        "8): 2026-10-06 to 2026-10-09 is not a Monday to the Friday"),
    (dict(weeks=_weeks(1, week="October 05-16, 2026")), "failed check: <text 1>: week 2 ('October 05-16, 2026', row "
                                                        "8): 2026-10-05 to 2026-10-16 is not a Monday"),
    (dict(weeks=_weeks(2, week="October 05-09, 2026")), "failed check: <text 1>: week 3 ('October 05-09, 2026', row "
                                                        "10): starts on 2026-10-05, not after the week before it "
                                                        "ended (2026-10-09)"),  # G4
    (dict(weeks=_weeks(2, lines=["(i) 10 Year FRB for ₹34,000 crore"])),
     "cannot parse: <text 1>: row 10: '(i) 10 Year FRB for ₹34,000 crore' is not an allocation line of the form"),
    (dict(weeks=_weeks(2, lines=["10 Year for ₹34,000 crore"])), "cannot parse: <text 1>: row 10: '10 Year for "
                                                                 "₹34,000 crore' is not an allocation line"),  # G5
    (dict(weeks=_weeks(2, week="Oct 12 to 16, 2026")), "cannot parse: <text 1>: row 10: auction week 'Oct 12 to 16, "
                                                       "2026' is not of the form"),
    (dict(weeks=_weeks(2, week="October 12-32, 2026")), "cannot parse: <text 1>: row 10: auction week 'October "
                                                        "12-32, 2026' has a day that does not exist"),
    (dict(weeks=_weeks(2, amount="thirty-four")), "cannot parse: <text 1>: row 10: not a number"),
    (dict(header=("Sl. No.", "Auction Week", "Amount", "Security-wise Allocation")),
     "cannot parse: <text 1>: the amount column is not in crore ('Amount')"),  # G6
    (dict(header=("Sl. No.", "Week", "Amount (in ₹ crore)", "Security-wise Allocation")),
     "cannot parse: <text 1>: no table with the columns of the calendar table (slno, auctionweek, amount, "
     "securitywiseallocation)"),
    (dict(weeks=[]), "cannot parse: <text 1>: no allocation lines under the header of the calendar"),
])
def test_gsec_calendar_checks(kw, message):
    assert _reason(_parse(_gsec_calendar(**kw))).startswith(message)


def test_gsec_calendar_label_variants_read_the_same():
    plain = _parse(_gsec_calendar()).tables[ro.GSEC_CALENDAR]
    weeks = [(week.replace("-", " – "), amount, [line.replace("Year for ₹", "Years for ₹ ") for line in lines])
             for week, amount, lines in fx.GSEC_CALENDAR_WEEKS]  # an en dash with spaces, a plural, a space
    for kw in (dict(weeks=weeks), dict(header=("Sr. No.", "Auction  Week", "Amount (₹ Crore)",
                                               "Security - wise Allocation")),
               dict(period="(Oct 01, 2026 to Mar 31, 2027)")):
        batch = _parse(_gsec_calendar(**kw))
        assert _notes(batch) == ""
        pd.testing.assert_frame_equal(batch.tables[ro.GSEC_CALENDAR], plain)


def test_gsec_calendar_week_numbers_and_rows_after_the_total():
    table = fx.gsec_calendar_table()
    gap = table.replace("<td>5</td><td>January 04-08, 2027</td>", "<td>6</td><td>January 04-08, 2027</td>")
    assert "the weeks are numbered [1, 2, 3, 4, 6], not 1 to 5 in order" in _reason(_parse(_page(
        fx.GSEC_CALENDAR_TITLE, "Sep 25, 2026", fx.GSEC_CALENDAR_TEXT, gap, prid=63668)))  # G3
    twice = table.replace("<td>5</td><td>January 04-08, 2027</td>", "<td>3</td><td>January 04-08, 2027</td>")
    assert "week 3 is printed twice, as 'October 12-16, 2026' and 'January 04-08, 2027'" in _reason(_parse(_page(
        fx.GSEC_CALENDAR_TITLE, "Sep 25, 2026", fx.GSEC_CALENDAR_TEXT, twice, prid=63668)))
    late = fx.gsec_calendar_table(after="<tr><td>6</td><td>January 11-15, 2027</td><td>1,000</td><td>(i) 5 Year for "
                                        "₹1,000 crore</td></tr>")
    assert "row 15: a row after the Total row" in _reason(_parse(_page(
        fx.GSEC_CALENDAR_TITLE, "Sep 25, 2026", fx.GSEC_CALENDAR_TEXT, late, prid=63668)))
    total = "<tr><td></td><td>Total</td><td>1,74,000</td><td></td></tr>"
    assert "row 15: a second Total row" in _reason(_parse(_page(
        fx.GSEC_CALENDAR_TITLE, "Sep 25, 2026", fx.GSEC_CALENDAR_TEXT, fx.gsec_calendar_table(after=total),
        prid=63668)))
    unnumbered = table.replace("<td>3</td><td>October 12-16, 2026</td>", "<td></td><td>October 12-16, 2026</td>")
    assert "row 10: no week number" in _reason(_parse(_page(fx.GSEC_CALENDAR_TITLE, "Sep 25, 2026",
                                                           fx.GSEC_CALENDAR_TEXT, unnumbered, prid=63668)))


def test_gsec_calendar_notes():
    assert _notes(_parse(_gsec_calendar(total=None))) == "no Total row to check the weeks against"
    assert pd.isna(_parse(_gsec_calendar(total=None)).tables[ro.GSEC_CALENDAR]["total_cr"]).all()
    batch = _parse(_gsec_calendar(period=""))  # no period above the header: the text has it
    assert _notes(batch) == "the table states no period; taken from the text"
    assert batch.tables[ro.GSEC_CALENDAR]["period_end"].eq(T("2027-03-31")).all()
    batch = _parse(_gsec_calendar(period="", text="The calendar is as under:"))
    assert _notes(batch) == ("no period '(<date> to <date>)' in the table or the text; taken as the months of the "
                             "title")
    assert batch.tables[ro.GSEC_CALENDAR]["period_start"].eq(T("2026-10-01")).all()
    assert "no period '(<date> to <date>)' in the table or the text and no months in the title" in _reason(_parse(
        _gsec_calendar(period="", text="As under:", title="Issuance Calendar for Marketable Dated Securities")))
    batch = _parse(_gsec_calendar(period="(October 01, 2026 to February 28, 2027)"))
    assert _notes(batch) == "the period 2026-10-01 to 2027-02-28 differs from the months in the title"
    assert "the period 2027-03-31 to 2026-10-01 does not run forwards" in _reason(_parse(_gsec_calendar(
        period="(March 31, 2027 to October 01, 2026)")))
    weeks = [*fx.GSEC_CALENDAR_WEEKS, ("April 05-09, 2027", "1,000", ["(i) 5 Year for ₹1,000 crore"])]
    batch = _parse(_gsec_calendar(weeks=weeks, total="1,75,000"))
    assert _notes(batch) == "week 6 (2027-04-05 to 2027-04-09) lies outside the period 2026-10-01 to 2027-03-31"
    numbered = _weeks(1, lines=["(i) 15 Year for ₹23,000 crore", "(iii) 50 Year for ₹13,000 crore"])
    assert _notes(_parse(_gsec_calendar(weeks=numbered))) == "week 2: allocation lines numbered [1, 3]"
    remark = '<tr><td colspan="4">The calendar may change.</td></tr>'
    batch = _parse(_gsec_calendar(after=remark))
    assert len(batch.tables[ro.GSEC_CALENDAR]) == 10
    assert _notes(batch) == ("row 15 of the calendar table spans it and is not a data row; ignored ('The calendar "
                             "may change.')")
    header = (*fx.GSEC_CALENDAR_HEADER, "Remarks")
    batch = _parse(_gsec_calendar(header=header))
    assert _notes(batch) == "column 'Remarks' of the calendar table not recognised; ignored (0 cells)"
    for label in ("Sr. No.", "S. No."):
        assert len(_parse(_gsec_calendar(header=(label, *fx.GSEC_CALENDAR_HEADER[1:]))).tables[ro.GSEC_CALENDAR]) == 10


def _sdl_calendar(title=fx.SDL_CALENDAR_TITLE, date_line="Oct 01, 2026", tables=(), **kw):
    return _page(title, date_line, fx.sdl_calendar_text(**kw), *tables, prid=63715, pr_no="2026-2027/1237")


def test_sdl_calendar_gives_the_quarter_and_its_total():
    batch = _parse(_sdl_calendar())
    df = batch.tables[ro.SDL_CALENDAR]
    assert list(df.columns) == list(rr.COLUMNS[ro.SDL_CALENDAR]) and len(df) == 1 and _notes(batch) == ""
    _check(df.iloc[0], dict(release_id=63715, release_date=T("2026-10-01"), line=1, quarter_start=T("2026-10-01"),
                            quarter_end=T("2026-12-31"), total_cr=360820.0, press_release_no="2026-2027/1237",
                            annex_urls="https://rbidocs.example/rdocs/content/pdfs/PR1237_A1.pdf "
                                       "https://rbidocs.example/rdocs/content/pdfs/PR1237_A2.pdf"))
    assert not rr.read_release_page(_sdl_calendar()).data_tables  # the page carries no table at all
    stem = "Indicative Calendar of Market borrowings by State Governments/ Union Territories for the Quarter "
    for quarter, start, end in (("January – March 2027", "2027-01-01", "2027-03-31"),
                                ("April - June 2026", "2026-04-01", "2026-06-30"),
                                ("July — September, 2026", "2026-07-01", "2026-09-30")):
        row = _parse(_sdl_calendar(stem + quarter, quarter=quarter)).tables[ro.SDL_CALENDAR].iloc[0]
        assert (row["quarter_start"], row["quarter_end"]) == (T(start), T(end))
    item = dict(title=fx.SDL_CALENDAR_TITLE, prid=63715, pub="Thu, 01 Oct 2026 18:20:00",
                html=fx.body(fx.sdl_calendar_text(), pr_no="2026-2027/1237"))
    feed = _parse(fx.feed([item])).tables[ro.SDL_CALENDAR]
    pd.testing.assert_frame_equal(_without(feed, "published", "source_file"), _without(df, "published", "source_file"))


def test_sdl_calendar_checks_and_notes():
    stem = "Indicative Calendar of Market borrowings by State Governments/ Union Territories for the Quarter "
    assert "failed check: <text 1>: October to November is not a quarter of the financial year" in _reason(_parse(
        _sdl_calendar(stem + "October – November 2026")))
    assert "is not of the form" in _reason(_parse(_sdl_calendar(stem.replace(" for the Quarter ", ""))))
    assert "0 sentences '... expected to be ₹... crore' (one expected)" in _reason(_parse(_sdl_calendar(
        total="about ₹3,60,820 crore".replace("₹", "Rs. "))))
    twice = _sdl_calendar().replace("Each auction", "For the year it is expected to be ₹9,00,000 crore. Each auction")
    assert "2 sentences '... expected to be ₹... crore' (one expected)" in _reason(_parse(twice))
    assert "the quarter's total 0 is not above 0" in _reason(_parse(_sdl_calendar(total="₹0 crore")))
    batch = _parse(_sdl_calendar(annexes=()))
    assert pd.isna(batch.tables[ro.SDL_CALENDAR].iloc[0]["annex_urls"])
    assert _notes(batch) == "no links named Annex in the text; annex_urls left blank"
    table = "<table><tr><td>State</td><td>Amount</td></tr><tr><td>Goa</td><td>100</td></tr></table>"
    batch = _parse(_sdl_calendar(tables=(table,)))
    assert len(batch.tables[ro.SDL_CALENDAR]) == 1
    assert _notes(batch) == "table 1 of the release (2 rows, beginning 'State') not recognised; ignored"


# --- Damaged pages ----------------------------------------------------------------------------------

DAMAGE_TEXTS = ("", "-", "NA", "0", "0.00", "abc", "1,2,3", "-5", "99999999", "5.5-4.5", "Fri, 13/11/2026", "Total",
                "November 31, 2026", "&nbsp;", "₹1 crore", "(i)", "1. Fixed Rate", "3. MSF", "7-Oct-2029",
                "IN0020190362", "(x) 3 Year for ₹9,000 crore", "9:30 AM to 9:00 AM")


def _damage(page: str, rng: random.Random) -> str:
    """One random change to a page: a cell's text, a cell or a row dropped, doubled or moved, a span, a cut."""
    cells = list(re.finditer(r"<(td|th)\b([^>]*)>(.*?)</\1>", page, flags=re.S))
    rows = list(re.finditer(r"<tr\b[^>]*>.*?</tr>", page, flags=re.S))
    if not cells or len(rows) < 2:
        return page
    how, cell, row = rng.randrange(8), rng.choice(cells), rng.choice(rows)
    if how == 0:
        return page[:cell.start(3)] + rng.choice(DAMAGE_TEXTS) + page[cell.end(3):]
    if how == 1:
        return page[:cell.start()] + page[cell.end():]
    if how == 2:
        return page[:row.start()] + page[row.end():]
    if how == 3:
        return page[:row.end()] + row.group(0) + page[row.end():]
    if how == 4:
        span = f' {rng.choice(("colspan", "rowspan"))}="{rng.randrange(9)}"'
        return page[:cell.start(2)] + span + page[cell.start(2):]
    if how == 5:
        first, second = sorted(rng.sample(rows, 2), key=lambda m: m.start())
        return (page[:first.start()] + second.group(0) + page[first.end():second.start()] + first.group(0)
                + page[second.end():])
    if how == 6:
        return page[:cell.start()] + f"<td>{rng.choice(DAMAGE_TEXTS)}</td>" + page[cell.start():]
    return page[:cell.end()]


def test_a_damaged_release_is_refused_or_read_but_never_an_error():
    pages = [fx.mmo_page(fx.mmo_friday()), fx.mmo_page(fx.mmo_saturday()), fx.mmo_page(fx.mmo_monday()),
             _laf_result(), _laf_announcement(), _omo_result(), _sale(), _omo_announcement(), _gsec_calendar(),
             _sdl_calendar()]
    rng = random.Random(20261118)
    outcomes = {"parsed": 0, "skipped": 0}
    for _ in range(100):
        page = rng.choice(pages)
        for _ in range(rng.randrange(1, 4)):
            page = _damage(page, rng)
        batch = rr.parse_releases([page])  # whatever the damage: rows, or a reason, never an exception
        assert len(batch.releases) == 1
        status = batch.releases.iloc[0]["status"]
        outcomes[status] += 1
        if status == "skipped":
            assert batch.releases.iloc[0]["reason"] and all(t.empty for t in batch.tables.values())
        else:
            assert sum(len(t) for t in batch.tables.values()) == batch.releases.iloc[0]["rows"] > 0
    assert outcomes["skipped"] > 50 and outcomes["parsed"] > 5  # most damage is refused; harmless damage is read


# --- The command ------------------------------------------------------------------------------------

def _operation_files(tmp_path):
    src = tmp_path / "rbi"
    src.mkdir()
    pages = {
        "mmo_63977.html": fx.mmo_page(fx.mmo_friday()),
        "vrrr_63968.html": _laf_result(),
        "omo_62379.html": _omo_result(),
        "omo_announcement_62367.html": _omo_announcement(),
        "cal_63668.html": _gsec_calendar(),
        "sdl_cal_63715.html": _sdl_calendar(),
    }
    for name, html in pages.items():
        (src / name).write_text(html, encoding="utf-8")
    items = [dict(title=fx.LAF_ANNOUNCEMENT_TITLE, prid=63984, pub="Mon, 16 Nov 2026 19:25:00",
                  html=fx.body(fx.LAF_ANNOUNCEMENT_TEXT, fx.laf_announcement_table(), pr_no="2026-2027/1506")),
             fx.mmo_item(fx.mmo_sunday()), fx.mmo_item(fx.mmo_saturday()), fx.mmo_item(fx.mmo_friday()),
             dict(title="Calendar for Auction of Government of India Treasury Bills", prid=63669,
                  pub="Fri, 25 Sep 2026 17:30:00", html=fx.body("Not sampled."))]
    (src / "pr_feed_20261116.xml").write_bytes(fx.feed(items))
    return [str(p) for p in sorted(src.iterdir())]


def test_rbi_releases_command_writes_the_operation_tables_the_same_in_any_order(tmp_path, capsys):
    files = _operation_files(tmp_path)
    a, b = tmp_path / "a", tmp_path / "b"
    assert main(["rbi-releases", *files, "--out-dir", str(a)]) == 0
    printed = capsys.readouterr().out
    assert "no parser for tbill_calendar: no page or feed item of this kind has been sampled" in printed
    first = {p.name: p.read_bytes() for p in a.iterdir()}
    assert set(first) == {f"{kind}.csv" for kind in ro.OPERATION_COLUMNS} | {"releases.csv"}
    assert main(["rbi-releases", *reversed(files), "--out-dir", str(b)]) == 0
    assert {p.name: p.read_bytes() for p in b.iterdir()} == first
    # some files again, among them the saved page of a release without its feed copy: nothing changes, and the
    # publication time the feed gave is kept
    assert [Path(f).name for f in files[:3]] == ["cal_63668.html", "mmo_63977.html", "omo_62379.html"]
    assert main(["rbi-releases", *files[:3], "--out-dir", str(a)]) == 0
    assert {p.name: p.read_bytes() for p in a.iterdir()} == first
    batch = rr.parse_releases(files)
    for kind in ro.OPERATION_COLUMNS:  # the CSV gives back the table, typed by the one rule
        csv = pd.read_csv(a / f"{kind}.csv", dtype=str, keep_default_na=False).replace({"": None})
        assert list(csv.columns) == list(rr.COLUMNS[kind]) and not csv.duplicated(["release_id", "line"]).any()
        for column in csv.columns:
            how, dtype = rr.column_type(column), batch.tables[kind][column].dtype
            if how in ("date", "time"):
                csv[column] = pd.to_datetime(csv[column]).astype(dtype)
            elif how in ("int", "float"):
                csv[column] = pd.to_numeric(csv[column]).astype(dtype)
            else:
                csv[column] = csv[column].astype(dtype)
        pd.testing.assert_frame_equal(csv, batch.tables[kind])
    mmo = pd.read_csv(a / "money_market_operations.csv")
    assert mmo.groupby("as_on_date").size().to_dict() == {"2026-11-13": 25, "2026-11-14": 27, "2026-11-15": 25}
    friday = mmo[mmo["as_on_date"] == "2026-11-13"]
    assert friday["source_file"].eq("mmo_63977.html").all()  # the saved page, with the time of the feed copy
    assert friday["published"].eq("2026-11-16T09:00:00").all() and friday["release_date"].eq("2026-11-16").all()
    assert mmo.loc[mmo["item"] == "term.term_money", "rate_pct"].isna().all()  # a dash is an empty cell
    assert (mmo.loc[mmo["item"] == "term.triparty_repo", "amount_cr"] == 0).all()  # a printed 0.00 is 0
    announcement = pd.read_csv(a / "laf_auction_announcement.csv", dtype=str)
    assert announcement.loc[0, "window_start"] == "09:30" and announcement.loc[0, "reversal_date"] == "2026-11-19"
    index = pd.read_csv(a / "releases.csv")
    assert index["status"].value_counts().to_dict() == {"parsed": 9, "duplicate": 1, "skipped": 1}
    assert index["notes"].isna().all()
