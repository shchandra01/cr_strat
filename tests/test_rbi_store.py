"""The daily store of RBI releases and its feed poller: synthetic pages and feeds only, and no network.

The transport of every poll is a fake one; the clock is an argument.
"""

import dataclasses
import datetime as dt
import gzip
import hashlib
import io
import json
import re
import subprocess
import sys
import urllib.error

import pandas as pd
import pytest
import rbi_fixtures as fx

from cr_strat import rbi_releases as rr
from cr_strat import rbi_store as st
from cr_strat.auctions import read_release_tables
from cr_strat.calendars import BusinessCalendar
from cr_strat.sovereign import MASTER_COLUMNS

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 11, 16, 14, 0, tzinfo=UTC)  # 19:30 in India
HOUR = dt.timedelta(hours=1)
MINUTE = dt.timedelta(minutes=1)


# --- Builders ----------------------------------------------------------------------------------------

def _page(title, date_line, narrative, *tables, prid, pr_no="2026-2027/1492", after=""):
    return fx.release_page(title, date_line, fx.body(narrative, *tables, pr_no=pr_no, after=after),
                           prid=prid).encode("utf-8")


LAF_ANNOUNCEMENT = dict(title=fx.LAF_ANNOUNCEMENT_TITLE, prid=63984, pub="Mon, 16 Nov 2026 19:25:00",
                        html=fx.body(fx.LAF_ANNOUNCEMENT_TEXT, fx.laf_announcement_table(), pr_no="2026-2027/1506"))
OTHER = dict(title="Some unrelated release", prid=63979, pub="Mon, 16 Nov 2026 11:00:00", html=fx.body("Text."))


def _result_17(values=("2-day", "50,000", "7,627", "7,627", "5.49", "5.49", "NA")):
    """The result of the auction ``LAF_ANNOUNCEMENT`` announces (2 days from 17 Nov 2026), as a feed item."""
    title = "Result of the 2-day Variable Rate Reverse Repo (VRRR) auction held on November 17, 2026"
    return dict(title=title, prid=63986, pub="Tue, 17 Nov 2026 10:35:00",
                html=fx.body("", fx.laf_result_table(values), pr_no="2026-2027/1508"))


def _result_13(accepted="40,000"):
    """The result of the 3-day auction of Friday 13 Nov 2026 that ``mmo_friday()`` shows (40,000 at 5.49), a page."""
    values = ("3-day", "75,000", accepted, accepted, "5.49", "5.49", "NA")
    return _page(fx.LAF_RESULT_TITLE, "Nov 13, 2026", "", fx.laf_result_table(values), prid=63968,
                 pr_no="2026-2027/1490")


def _feed16(*extra):
    """The feed of Monday 16 Nov: the weekend's money markets, an auction announcement and an unrelated release."""
    return fx.feed([*extra, LAF_ANNOUNCEMENT, fx.mmo_item(fx.mmo_sunday()), fx.mmo_item(fx.mmo_saturday()), OTHER,
                    fx.mmo_item(fx.mmo_friday())])


def _feed17():
    return fx.feed([_result_17(), fx.mmo_item(fx.mmo_monday()), LAF_ANNOUNCEMENT, fx.mmo_item(fx.mmo_sunday())])


def _omo_announcement():
    return _page(fx.OMO_ANNOUNCEMENT_TITLE, "Mar 11, 2026", fx.OMO_ANNOUNCEMENT_TEXT, fx.omo_announcement_table(),
                 prid=62367, pr_no="2025-2026/2251", after=fx.OMO_ANNOUNCEMENT_AFTER)


def _omo_result():
    return _page(fx.OMO_RESULT_TITLE, "Mar 13, 2026", "", fx.omo_result_tables(), prid=62379, pr_no="2025-2026/2263")


def _files(root):
    """Every file of a store and its bytes."""
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _csv(path):
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def _index(store):
    return _csv(store / "releases.csv").set_index("release_id")


def _master(tmp_path, rows):
    """A security master CSV with ``MASTER_COLUMNS``: rows of (isin, name, maturity date)."""
    records = [{c: "" for c in MASTER_COLUMNS} | dict(isin=i, name=n, name_key=n, kind="gsec", maturity_date=m)
               for i, n, m in rows]
    path = tmp_path / "master.csv"
    pd.DataFrame(records, columns=list(MASTER_COLUMNS)).to_csv(path, index=False)
    return path


class FakeTransport:
    """Answers polls from a list (an exception in it is raised), and records every request it was sent."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, url, headers, timeout):
        self.requests.append((url, dict(headers), timeout))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _ok(body, etag='W/"d07757b7977dd1:0"', modified="Mon, 16 Nov 2026 13:55:50 GMT", **headers):
    return st.FeedResponse(200, {"etag": etag, "last-modified": modified, "content-type": "text/xml", **headers}, body)


def _state(store):
    return json.loads((store / "state.json").read_text(encoding="utf-8"))["poller"]


# --- Ingest ------------------------------------------------------------------------------------------

def test_ingest_writes_the_store_and_the_same_copies_again_change_nothing(tmp_path):
    store = tmp_path / "store"
    feed = _feed16()
    report = st.ingest(store, [("pr_feed_20261116.xml", feed)], now=T0)
    assert report.new == [63977, 63979, 63980, 63981, 63984] and report.copies == 5
    assert not report.revised and not report.failed and not report.unreadable
    raw = f"pr_feed_20261116T140000Z_{hashlib.sha256(feed).hexdigest()[:12]}.xml"
    assert report.raw_saved == [raw] and (store / "raw" / raw).read_bytes() == feed
    files = _files(store)
    assert set(files) == {"releases.csv", "revisions.csv", "gaps.csv", f"raw/{raw}",
                          "tables/money_market_operations.csv", "tables/laf_auction_announcement.csv",
                          *(f"derived/{name}.csv" for name in st.DERIVED_COLUMNS)}
    assert set(report.written) == set(files)
    index = _index(store)
    assert list(index.reset_index().columns) == list(st.RELEASE_INDEX_COLUMNS)
    assert index["status"].to_dict() == {"63977": "parsed", "63979": "not_recognised", "63980": "parsed",
                                         "63981": "parsed", "63984": "parsed"}
    assert index.loc["63977", "rows"] == "25" and index.loc["63977", "published"] == "2026-11-16T09:00:00"
    assert index.loc["63979", "reason"] == "title not recognised: 'Some unrelated release'"
    assert index["first_seen"].eq("2026-11-16T19:30:00").all() and index["last_changed"].eq("").all()
    mmo = _csv(store / "tables" / "money_market_operations.csv")
    assert list(mmo.columns) == list(rr.COLUMNS["money_market_operations"]) and len(mmo) == 25 + 27 + 25
    assert not (store / "store.lock").exists() and not (store / "journal.json").exists()

    again = st.ingest(store, [("pr_feed_20261116.xml", feed), ("copy.xml", feed)], now=T0 + 3 * HOUR)
    assert again.written == [] and again.new == [] and again.raw_saved == [] and _files(store) == files
    assert again.summary() == ("5 releases in 10 copies from 2 inputs, 0 new, 0 revised, 0 repaired, 0 failed; "
                               "no file changed")


def test_the_order_of_the_sources_does_not_matter(tmp_path):
    sources = [("pr_feed_20261116.xml", _feed16()), ("pr_feed_20261117.xml", _feed17()),
               ("mmo_63977.html", fx.mmo_page(fx.mmo_friday()).encode()), ("vrrr_63968.html", _result_13())]
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    st.ingest(a, sources, now=T0)
    st.ingest(b, list(reversed(sources)), now=T0)
    assert _files(a) == _files(b)
    assert _index(a).loc["63977", "source_file"] == "mmo_63977.html"  # the saved page, with the feed's time
    assert _index(a).loc["63977", "published"] == "2026-11-16T09:00:00"
    for source in (sources[1], sources[3], sources[0], sources[2]):  # one at a time, in yet another order
        st.ingest(c, [source], now=T0)
    derived = {k: v for k, v in _files(a).items() if k.startswith(("derived/", "raw/")) or k == "gaps.csv"}
    assert {k: v for k, v in _files(c).items() if k in derived} == derived
    for kind in ("money_market_operations", "laf_auction_result", "laf_auction_announcement"):
        left, right = _csv(a / "tables" / f"{kind}.csv"), _csv(c / "tables" / f"{kind}.csv")
        pd.testing.assert_frame_equal(left.drop(columns="source_file"), right.drop(columns="source_file"))


def test_a_corrected_release_replaces_its_rows_and_the_change_is_logged(tmp_path):
    store = tmp_path / "s"
    st.ingest(store, [("pr_feed_20261116.xml", _feed16())], now=T0)
    friday = fx.mmo_friday()
    friday["markets"][1][2] = "5.62"  # the call money rate, 5.60 at first
    corrected = ("pr_feed_20261116T1500.xml", fx.feed([fx.mmo_item(friday)]))
    report = st.ingest(store, [corrected], now=T0 + HOUR)
    assert report.revised == [63977] and report.new == [] and report.published == []
    log = _csv(store / "revisions.csv")
    assert list(log.columns) == list(st.REVISION_COLUMNS) and log["change"].tolist() == ["rows"]
    entry = log.iloc[0]
    assert (entry["seen"], entry["release_id"], entry["kind"]) == ("2026-11-16T20:30:00", "63977",
                                                                   "money_market_operations")
    assert entry["detail"] == "line 2 rate_pct 5.6 -> 5.62" and entry["source_file"] == "pr_feed_20261116T1500.xml#1"
    index = _index(store)
    assert entry["new_hash"] == index.loc["63977", "rows_hash"] != entry["old_hash"]
    assert index.loc["63977", "last_changed"] == "2026-11-16T20:30:00"
    mmo = _csv(store / "tables" / "money_market_operations.csv")
    assert mmo.loc[(mmo["release_id"] == "63977") & (mmo["item"] == "overnight.call_money"), "rate_pct"].tolist() \
        == ["5.62"]
    daily = _csv(store / "derived" / "money_market_daily.csv").set_index("as_on_date")
    assert daily.loc["2026-11-13", "call_rate_pct"] == "5.62" and daily.loc["2026-11-13", "revised"] == entry["seen"]
    assert daily.loc["2026-11-14", "revised"] == ""

    files = _files(store)  # neither copy changes anything now: the old one is a version the store held before
    for source in ([("pr_feed_20261116.xml", _feed16())], [corrected]):
        assert st.ingest(store, source, now=T0 + 2 * HOUR).written == []
    assert _files(store) == files


def test_several_versions_in_one_ingest_apply_feed_copies_by_time_and_saved_pages_last(tmp_path):
    friday = fx.mmo_friday()
    friday["markets"][1][2] = "5.62"
    late = fx.mmo_friday()
    late["markets"][1][2] = "5.64"
    sources = [("pr_feed_b.xml", fx.feed([dict(fx.mmo_item(late), pub="Mon, 16 Nov 2026 09:30:00")])),
               ("pr_feed_a.xml", fx.feed([fx.mmo_item(fx.mmo_friday())])),
               ("mmo_63977.html", fx.mmo_page(friday).encode())]
    for order in (sources, list(reversed(sources))):
        store = tmp_path / f"s{len(list(tmp_path.iterdir()))}"
        report = st.ingest(store, order, now=T0)
        assert report.new == [63977] and report.revised == [63977]
        log = _csv(store / "revisions.csv")
        assert log["detail"].tolist() == ["line 2 rate_pct 5.6 -> 5.64", "line 2 rate_pct 5.64 -> 5.62"]
        index = _index(store)
        assert index.loc["63977", "source_file"] == "mmo_63977.html"
        assert index.loc["63977", "published"] == "2026-11-16T09:00:00"  # the earliest feed time seen
    assert _files(tmp_path / "s0") == _files(tmp_path / "s1")


def test_a_feed_copy_after_the_saved_page_adds_the_publication_time(tmp_path):
    store = tmp_path / "s"
    st.ingest(store, [("mmo_63977.html", fx.mmo_page(fx.mmo_friday()).encode())], now=T0)
    daily = _csv(store / "derived" / "money_market_daily.csv")
    assert daily[["public_from", "public_from_source"]].values.tolist() == [["2026-11-16T23:59:59", "release_date"]]
    report = st.ingest(store, [("pr_feed_20261116.xml", _feed16())], now=T0 + HOUR)
    assert report.published == [63977] and report.revised == [] and 63977 not in report.new
    log = _csv(store / "revisions.csv")
    assert log[["release_id", "change", "detail"]].values.tolist() == [
        ["63977", "published", "published 2026-11-16T09:00:00 (was blank)"]]
    mmo = _csv(store / "tables" / "money_market_operations.csv")
    friday = mmo[mmo["release_id"] == "63977"]
    assert friday["published"].eq("2026-11-16T09:00:00").all() and friday["source_file"].eq("mmo_63977.html").all()
    daily = _csv(store / "derived" / "money_market_daily.csv").set_index("as_on_date")
    assert daily.loc["2026-11-13", ["public_from", "public_from_source", "revised"]].tolist() == [
        "2026-11-16T09:00:00", "feed", ""]


def _broken_friday():
    day = fx.mmo_friday()
    day["net_total"] = "-1,70,000.00"  # line F is no longer line 5 plus line E
    return day


def test_a_failed_release_stays_failed_until_a_copy_that_parses_repairs_it(tmp_path):
    store = tmp_path / "s"
    broken = ("pr_feed_a.xml", fx.feed([fx.mmo_item(_broken_friday())]))
    report = st.ingest(store, [broken], now=T0)
    assert [(r, k) for r, k, _ in report.failed] == [(63977, "money_market_operations")]
    assert report.failed[0][2].startswith("failed check: ") and report.new == [63977]
    index = _index(store)
    assert (index.loc["63977", "status"], index.loc["63977", "rows"]) == ("failed", "0")
    assert index.loc["63977", "published"] == "2026-11-16T09:00:00"
    assert not (store / "tables" / "money_market_operations.csv").exists()
    assert [f["release_id"] for f in st.status(store).failed] == ["63977"]
    files = _files(store)
    assert st.ingest(store, [broken], now=T0 + HOUR).written == [] and _files(store) == files

    report = st.ingest(store, [("mmo_63977.html", fx.mmo_page(fx.mmo_friday()).encode())], now=T0 + 2 * HOUR)
    assert report.repaired == [63977] and report.failed == [] and report.new == []
    index = _index(store)
    assert (index.loc["63977", "status"], index.loc["63977", "rows"]) == ("parsed", "25")
    assert index.loc["63977", "published"] == "2026-11-16T09:00:00"  # the feed saw it, though its copy failed
    assert index.loc["63977", "first_seen"] == "2026-11-16T19:30:00"
    log = _csv(store / "revisions.csv")
    assert log["change"].tolist() == ["repaired"] and log.loc[0, "detail"].startswith("was failed: failed check: ")
    assert log.loc[0, "old_hash"] == "" and log.loc[0, "new_hash"] == index.loc["63977", "rows_hash"]


def test_a_copy_that_fails_after_the_rows_were_stored_is_logged_once_and_the_rows_are_kept(tmp_path):
    store = tmp_path / "s"
    st.ingest(store, [("pr_feed_20261116.xml", _feed16())], now=T0)
    bad = ("pr_feed_b.xml", fx.feed([fx.mmo_item(_broken_friday())]))
    report = st.ingest(store, [bad], now=T0 + HOUR)
    assert report.refused == [63977] and report.revised == [] and report.failed == []
    log = _csv(store / "revisions.csv")
    assert log["change"].tolist() == ["refused"] and log.loc[0, "detail"].startswith("failed: failed check: ")
    assert _index(store).loc["63977", "status"] == "parsed"
    assert (_csv(store / "tables" / "money_market_operations.csv")["release_id"] == "63977").sum() == 25
    files = _files(store)
    assert st.ingest(store, [bad], now=T0 + 2 * HOUR).written == [] and _files(store) == files


def test_a_parser_error_fails_that_release_and_not_the_ingest(tmp_path, monkeypatch):
    def broken(release, ctx=None):
        raise KeyError("a bug")

    rt = rr.RELEASE_TYPES["laf_auction_announcement"]
    monkeypatch.setitem(rr.RELEASE_TYPES, "laf_auction_announcement", dataclasses.replace(rt, parser=broken))
    report = st.ingest(tmp_path / "s", [("f.xml", _feed16())], now=T0)
    assert report.failed == [(63984, "laf_auction_announcement", "parser error: KeyError: 'a bug'")]
    assert _index(tmp_path / "s").loc["63977", "status"] == "parsed"


def test_a_parser_giving_a_fraction_for_a_whole_number_fails_that_release(tmp_path, monkeypatch):
    rt = rr.RELEASE_TYPES["laf_auction_announcement"]

    def fractional(release, ctx=None):
        rows = rt.parser(release, ctx)
        rows[0]["tenor_days"] = 2.5
        return rows

    monkeypatch.setitem(rr.RELEASE_TYPES, "laf_auction_announcement", dataclasses.replace(rt, parser=fractional))
    report = st.ingest(tmp_path / "s", [("f.xml", _feed16())], now=T0)
    assert report.failed == [(63984, "laf_auction_announcement", "parser error: ValueError: 2.5 is not a whole number")]


def test_two_inputs_of_one_name_holding_different_copies_give_one_store_in_either_order(tmp_path):
    friday = fx.mmo_friday()
    friday["markets"][1][2] = "5.62"
    a, b = ("pr_feed.xml", fx.feed([fx.mmo_item(fx.mmo_friday())])), ("pr_feed.xml", fx.feed([fx.mmo_item(friday)]))
    st.ingest(tmp_path / "x", [a, b], now=T0)
    st.ingest(tmp_path / "y", [b, a], now=T0)
    assert _files(tmp_path / "x") == _files(tmp_path / "y") and len(_csv(tmp_path / "x" / "revisions.csv")) == 1


def test_unreadable_inputs_are_reported_and_add_nothing(tmp_path):
    store = tmp_path / "s"
    report = st.ingest(store, [("strips_3973.html", fx.blocked_page().encode()), ("bad.xml", b"<rss><channel>"),
                               ("pr_feed.xml", _feed16())], now=T0)
    assert [name for name, _ in report.unreadable] == ["strips_3973.html", "bad.xml"]
    assert "Support ID" in report.unreadable[0][1] and "not well-formed" in report.unreadable[1][1]
    assert len(_index(store)) == 5 and len(report.raw_saved) == 1  # the broken feed is not kept


def test_no_raw_copy_when_switched_off_and_none_twice_for_the_same_bytes(tmp_path):
    feed = _feed16()
    off = st.ingest(tmp_path / "off", [("f.xml", feed)], now=T0, keep_raw=False)
    assert off.raw_saved == [] and not (tmp_path / "off" / "raw").exists()
    store = tmp_path / "on"
    first = st.ingest(store, [("f.xml", feed), ("g.xml", feed)], now=T0)
    later = st.ingest(store, [("h.xml", feed)], now=T0 + HOUR)
    assert len(first.raw_saved) == 1 and later.raw_saved == [] and len(list((store / "raw").iterdir())) == 1


def test_now_must_carry_a_time_zone(tmp_path):
    with pytest.raises(ValueError, match="timezone-aware"):
        st.ingest(tmp_path / "s", [], now=dt.datetime(2026, 11, 16, 14, 0))
    with pytest.raises(ValueError, match="timezone-aware"):
        st.poll(tmp_path / "s", now=dt.datetime(2026, 11, 16, 14, 0), transport=FakeTransport())


def test_the_store_refuses_files_that_are_not_its_own(tmp_path):
    store = tmp_path / "s"
    store.mkdir()
    (store / "releases.csv").write_text("release_id,kind\n1,x\n", encoding="utf-8")
    with pytest.raises(st.StoreError, match="missing columns"):
        st.ingest(store, [], now=T0)
    other = tmp_path / "t"
    st.ingest(other, [("f.xml", _feed16())], now=T0)
    table = other / "tables" / "money_market_operations.csv"
    table.write_text(table.read_text(encoding="utf-8").replace("\n63977,", "\n63978,", 1), encoding="utf-8")
    with pytest.raises(st.StoreError, match="money_market_operations.csv, line 2: rows of release '63978'"):
        st.ingest(other, [], now=T0)
    assert not (other / "store.lock").exists()


def test_tables_are_read_by_the_auction_reader_and_typed_by_read_table(tmp_path):
    store = tmp_path / "s"
    goi = _page("Government Stock - Full Auction Results", "Nov 13, 2026", "", fx.results_table([fx.GOI_6_94]),
                prid=63970)
    st.ingest(store, [("goi_full_63970.html", goi), ("f.xml", _feed16())], now=T0)
    tables = read_release_tables(store / "tables")
    assert set(tables) == {"gsec_result"} and tables["gsec_result"]["cutoff_yield"].tolist() == [7.3203]
    daily = st.read_table(store, "money_market_daily")
    assert str(daily["as_on_date"].dtype).startswith("datetime64") and daily["business_day"].dtype == "boolean"
    assert daily["release_id"].dtype == "Int64" and daily["sdf_cr"].dtype == float
    assert daily["public_from"].iloc[0] == pd.Timestamp("2026-11-16 09:00") and pd.isna(daily["revised"]).all()
    index = st.read_table(store, "releases")
    assert index["release_id"].dtype == "Int64" and index["rows"].dtype == "Int64"
    mmo = st.read_table(store, "money_market_operations")
    assert mmo["seq"].dtype == "Int64" and str(mmo["published"].dtype).startswith("datetime64")
    with pytest.raises(ValueError, match="no store table"):
        st.read_table(store, "nonsense")


# --- Possible gaps -----------------------------------------------------------------------------------

def test_possible_gaps_are_ids_and_money_market_days_never_seen(tmp_path):
    store = tmp_path / "s"
    first = fx.feed([OTHER, dict(OTHER, prid=63978, title="Another unrelated release"), fx.mmo_item(fx.mmo_friday())])
    later = fx.feed([LAF_ANNOUNCEMENT, fx.mmo_item(fx.mmo_sunday())])
    st.ingest(store, [("omo_announcement_62367.html", _omo_announcement())], now=T0)
    fake = FakeTransport(_ok(first, etag='W/"1"'), _ok(later, etag='W/"2"'))
    st.poll(store, now=T0 - 3 * HOUR, transport=fake)  # the span the poller received begins at 63977 ...
    st.poll(store, now=T0, transport=fake)  # ... and ends at 63984; 63980, 63982 and 63983 came and went in between
    assert _csv(store / "gaps.csv").to_dict("records") == [
        {"gap": "release_id", "first": "62368", "last": "63976", "count": "1609", "in_feed_span": "False"},
        {"gap": "release_id", "first": "63980", "last": "63980", "count": "1", "in_feed_span": "True"},
        {"gap": "release_id", "first": "63982", "last": "63983", "count": "2", "in_feed_span": "True"},
        {"gap": "mmo_day", "first": "2026-11-14", "last": "2026-11-14", "count": "1", "in_feed_span": "True"}]
    lines = st.status(store).lines()
    assert ("possible gaps, release ids never seen: 3 ranges, 1612 in all; 2 ranges (3) inside the span polls "
            "received: 63980, 63982 to 63983") in lines
    assert ("possible gaps, days without Money Market Operations: 1 ranges, 1 in all; 1 ranges (1) inside the "
            "span polls received: 2026-11-14") in lines
    st.ingest(store, [("g.xml", fx.feed([fx.mmo_item(fx.mmo_saturday())]))], now=T0 + HOUR)  # the Saturday: 63980
    gaps = _csv(store / "gaps.csv")
    assert gaps[["first", "last"]].values.tolist() == [["62368", "63976"], ["63982", "63983"]]


# --- Derived tables ----------------------------------------------------------------------------------

def test_money_market_daily_has_one_row_per_day_with_rates_facilities_and_reserves(tmp_path):
    store = tmp_path / "s"
    st.ingest(store, [("a.xml", _feed16()), ("b.xml", _feed17())], now=T0)
    daily = _csv(store / "derived" / "money_market_daily.csv")
    assert list(daily.columns) == list(st.DERIVED_COLUMNS["money_market_daily"])
    assert daily["as_on_date"].tolist() == ["2026-11-13", "2026-11-14", "2026-11-15", "2026-11-16"]
    assert daily["business_day"].tolist() == ["True", "False", "False", "True"]
    friday = daily.iloc[0].to_dict()
    expected = dict(
        overnight_volume_cr="734151", overnight_rate_pct="5.61", overnight_low_pct="1", overnight_high_pct="7.3",
        call_volume_cr="12340.5", call_rate_pct="5.6", call_low_pct="4.25", call_high_pct="5.9",
        triparty_volume_cr="510220.25", market_repo_rate_pct="5.52", corp_bond_repo_high_pct="7.3",
        notice_volume_cr="120", term_money_volume_cr="1050.5", term_money_rate_pct="", term_money_low_pct="5.75",
        term_triparty_volume_cr="0", term_triparty_rate_pct="",
        sdf_cr="153400", sdf_rate_pct="5.25", msf_cr="13500", msf_rate_pct="5.75", vrr_cr="", vrr_rate_pct="",
        vrrr_cr="40000", vrrr_rate_pct="5.49", net_today_cr="-179900", slf_cr="9800.5", net_outstanding_cr="9800.5",
        net_total_cr="-170099.5", cash_balances_cr="840000.25", cash_balances_date="2026-11-13",
        crr_requirement_cr="810000", crr_requirement_date="2026-11-15", gov_surplus_cash_cr="0",
        gov_surplus_cash_date="2026-11-13", net_durable_liquidity_cr="780000",
        net_durable_liquidity_date="2026-10-15", release_id="63977", release_date="2026-11-16",
        public_from="2026-11-16T09:00:00", public_from_source="feed", revised="", notes="")
    assert {k: friday[k] for k in expected} == expected
    saturday, monday = daily.iloc[1], daily.iloc[3]
    assert (saturday["overnight_volume_cr"], saturday["overnight_rate_pct"]) == ("0", "")  # no trading: no rate
    assert (saturday["msf_cr"], saturday["sdf_cr"], saturday["net_outstanding_cr"]) == ("20000", "155010", "-32599.5")
    assert (monday["release_id"], monday["public_from"]) == ("63985", "2026-11-17T09:00:00")


def test_money_market_daily_weighs_differing_rates_and_leaves_uncovered_years_blank(tmp_path):
    friday = fx.mmo_friday()
    fri, mon = "Fri, 13/11/2026", "Mon, 16/11/2026"
    friday["today"]["msf"][2] = (fri, "3", mon, "1,000.00", "6.00")
    covers_2025 = BusinessCalendar("test", frozenset(), years=frozenset({2025}))
    st.ingest(tmp_path / "s", [("f.xml", fx.feed([fx.mmo_item(friday)]))], now=T0, calendar=covers_2025)
    row = _csv(tmp_path / "s" / "derived" / "money_market_daily.csv").iloc[0]
    assert (row["msf_cr"], row["msf_rate_pct"]) == ("13500", "5.7685")  # (12,500 x 5.75 + 1,000 x 6.00) / 13,500
    assert row["notes"] == "msf rates differ: 5.75, 6 (amount-weighted mean used)"
    assert row["business_day"] == ""  # 2026 is not a year this calendar covers


def test_liquidity_operations_join_announcement_result_and_money_market_line(tmp_path):
    store = tmp_path / "s"
    st.ingest(store, [("a.xml", _feed16()), ("vrrr_63968.html", _result_13()), ("b.xml", _feed17())], now=T0)
    ops = _csv(store / "derived" / "liquidity_operations.csv")
    assert list(ops.columns) == list(st.DERIVED_COLUMNS["liquidity_operations"]) and len(ops) == 2
    friday, tuesday = ops.iloc[0].to_dict(), ops.iloc[1].to_dict()
    assert {k: friday[k] for k in ("operation", "direction", "auction_date", "tenor_days", "reversal_date",
                                   "announcement_id", "notified_cr", "result_id", "result_public_from",
                                   "accepted_cr", "cutoff_rate_pct", "liquidity_effect_cr", "mmo_release_id",
                                   "mmo_public_from", "mmo_amount_cr", "mmo_rate_pct", "mmo_check", "public_from",
                                   "public_from_source", "notes")} == dict(
        operation="variable_rate_reverse_repo", direction="reverse_repo", auction_date="2026-11-13", tenor_days="3",
        reversal_date="2026-11-16", announcement_id="", notified_cr="75000", result_id="63968",
        result_public_from="2026-11-13T23:59:59", accepted_cr="40000", cutoff_rate_pct="5.49",
        liquidity_effect_cr="-40000", mmo_release_id="63977", mmo_public_from="2026-11-16T09:00:00",
        mmo_amount_cr="40000", mmo_rate_pct="5.49", mmo_check="agrees", public_from="2026-11-16T09:00:00",
        public_from_source="feed", notes="")
    assert friday["first_public_from"] == "2026-11-13T23:59:59"  # the result, a saved page: its release date
    assert {k: tuesday[k] for k in ("auction_date", "tenor_days", "reversal_date", "announcement_id",
                                    "announcement_public_from", "notified_cr", "window_start", "window_end",
                                    "result_id", "result_public_from", "received_cr", "accepted_cr", "mmo_check",
                                    "public_from", "public_from_source")} == dict(
        auction_date="2026-11-17", tenor_days="2", reversal_date="2026-11-19", announcement_id="63984",
        announcement_public_from="2026-11-16T19:25:00", notified_cr="50000", window_start="09:30",
        window_end="10:00", result_id="63986", result_public_from="2026-11-17T10:35:00", received_cr="7627",
        accepted_cr="7627", mmo_check="", public_from="2026-11-17T10:35:00", public_from_source="feed")
    assert tuesday["first_public_from"] == "2026-11-16T19:25:00"  # announced the evening before


def test_liquidity_operations_say_how_a_result_and_money_market_operations_differ(tmp_path):
    store = tmp_path / "s"
    monday_result = _page("Result of the Overnight Variable Rate Reverse Repo (VRRR) auction held on November 16, 2026",
                          "Nov 16, 2026", "", fx.laf_result_table(("1-day", "50,000", "20,000", "20,000", "5.48",
                                                                   "5.48", "NA")), prid=63983)
    st.ingest(store, [("a.xml", _feed16()), ("b.xml", _feed17()), ("vrrr_63968.html", _result_13("38,592")),
                      ("vrrr_63983.html", monday_result)], now=T0)
    ops = _csv(store / "derived" / "liquidity_operations.csv").set_index("auction_date")
    assert ops.loc["2026-11-13", "mmo_check"] == "differs"
    assert ops.loc["2026-11-13", "notes"] == "Money Market Operations differ: amount 40000 against 38592 accepted"
    assert ops.loc["2026-11-16", "mmo_check"] == "missing"  # Monday's release shows no reverse repo of the day
    assert ops.loc["2026-11-16", "notes"] == ("Money Market Operations as on 2026-11-16 (release 63985) shows no such "
                                              "operation; reversal date: auction date plus tenor (no announcement)")
    only = tmp_path / "only"  # without the result, the Friday auction is known from Money Market Operations alone
    st.ingest(only, [("a.xml", _feed16())], now=T0)
    ops = _csv(only / "derived" / "liquidity_operations.csv").set_index("auction_date")
    assert ops.loc["2026-11-13", ["result_id", "mmo_release_id", "reversal_date", "mmo_check", "notes"]].tolist() \
        == ["", "63977", "2026-11-16", "", "known only from Money Market Operations (release 63977)"]


def test_omo_operations_join_announcement_and_result_with_isins(tmp_path):
    store = tmp_path / "s"
    st.ingest(store, [("omo_62367.html", _omo_announcement()), ("omo_62379.html", _omo_result())], now=T0)
    omo = _csv(store / "derived" / "omo_operations.csv")
    assert list(omo.columns) == list(st.DERIVED_COLUMNS["omo_operations"])
    assert omo["security"].tolist() == ["6.45% GS 2029", "7.41% GS 2036", "7.06% GS 2046"]
    assert omo["isin"].tolist() == ["IN0020190362", "IN0020220102", "IN0020160068"]
    assert omo["isin_source"].eq("announcement").all() and omo["isin_match"].eq("").all()
    assert omo["accepted_cr"].tolist() == ["9000", "11000", "5000"] and omo["total_accepted_cr"].eq("25000").all()
    first = omo.iloc[0]
    assert (first["operation"], first["auction_date"], first["settlement_date"], first["maturity_date"],
            first["aggregate_notified_cr"], first["cutoff_yield"], first["cutoff_price"], first["auction_method"],
            first["programme_pr_no"], first["liquidity_effect_cr"]) == (
        "purchase", "2026-03-13", "2026-03-16", "2029-10-07", "25000", "6.12", "101.02", "multiple", "2025-26/2228",
        "9000")
    assert (first["announcement_id"], first["result_id"], first["public_from"], first["public_from_source"],
            first["first_public_from"]) == ("62367", "62379", "2026-03-13T23:59:59", "release_date",
                                            "2026-03-11T23:59:59")


def test_omo_operations_take_isins_from_the_stored_master(tmp_path):
    master = _master(tmp_path, [("IN0020190362", "6.45% GS 2029", "2029-10-07"),
                                ("IN0020220102", "7.41% GS 2036", "2036-12-19"),
                                ("IN0020990010", "7.06% GS 2046", "2046-10-10")])
    alone = tmp_path / "alone"
    st.ingest(alone, [("omo_62379.html", _omo_result())], now=T0)  # a result without announcement or master
    omo = _csv(alone / "derived" / "omo_operations.csv")
    assert omo["isin"].eq("").all() and omo["notes"].eq(
        "no ISIN: the announcement was not seen and no security master is stored").all()
    store = tmp_path / "s"
    st.ingest(store, [("omo_62379.html", _omo_result())], now=T0, master=master)  # a result without announcement
    assert (store / "sovereign_master.csv").read_bytes() == master.read_bytes()
    omo = _csv(store / "derived" / "omo_operations.csv")
    assert omo["isin"].tolist() == ["IN0020190362", "IN0020220102", "IN0020990010"]
    assert omo["isin_source"].eq("master").all() and omo["isin_match"].eq("matched").all()
    st.ingest(store, [("omo_62367.html", _omo_announcement())], now=T0 + HOUR)  # the master stays in use
    omo = _csv(store / "derived" / "omo_operations.csv")
    assert omo["isin"].tolist() == ["IN0020190362", "IN0020220102", "IN0020160068"]  # the announcement's
    assert omo["notes"].tolist() == ["", "", "the security master gives IN0020990010 for 7.06% GS 2046"]
    (tmp_path / "bad.csv").write_text("isin,name\nX,Y\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not a security master"):
        st.ingest(store, [], now=T0, master=tmp_path / "bad.csv")


def test_supply_calendar_lists_planned_and_announced_auctions_with_the_time_they_became_public(tmp_path):
    store = tmp_path / "s"
    sdl = fx.body(fx.SDL_ANNOUNCEMENT_TEXT, fx.sdl_announcement_table(), pr_no="2026-2027/1498")
    feed = fx.feed([dict(title="Auction of State Government Securities", prid=63976, pub="Fri, 13 Nov 2026 17:50:00",
                         html=sdl)])
    sources = [
        ("cal_63668.html", _page(fx.GSEC_CALENDAR_TITLE, "Sep 25, 2026", fx.GSEC_CALENDAR_TEXT,
                                 fx.gsec_calendar_table(), prid=63668, pr_no="2026-2027/1190")),
        ("sdl_cal_63715.html", _page(fx.SDL_CALENDAR_TITLE, "Oct 01, 2026", fx.sdl_calendar_text(), prid=63715,
                                     pr_no="2026-2027/1237")),
        ("goi_63983.html", _page("Auction of Government of India Dated Securities", "Nov 16, 2026",
                                 fx.GSEC_ANNOUNCEMENT_TEXT, fx.gsec_announcement_table(), prid=63983)),
        ("tbill_63975.html", _page("Auction of 91-Day, 182-Day and 364-Day Treasury Bills", "Nov 13, 2026",
                                   "The auction will be price based using multiple price method.",
                                   fx.tbill_announcement_table(), prid=63975)),
        ("f.xml", feed)]
    st.ingest(store, sources, now=T0)
    supply = _csv(store / "derived" / "supply_calendar.csv")
    assert list(supply.columns) == list(st.DERIVED_COLUMNS["supply_calendar"])
    assert supply["source"].value_counts().to_dict() == {"gsec_calendar": 10, "sdl_announcement": 6,
                                                         "tbill_announcement": 3, "gsec_announcement": 2,
                                                         "sdl_calendar": 1}
    calendar = supply[supply["source"] == "gsec_calendar"]
    assert calendar[["kind", "period_start", "period_end", "tenor_years", "tenor_bucket", "amount_cr"]].values.tolist()[
        :4] == [["gsec", "2026-09-28", "2026-10-02", "3", "1-3y", "9000"],
                ["gsec", "2026-09-28", "2026-10-02", "7", "5-10y", "12000"],
                ["gsec", "2026-09-28", "2026-10-02", "30", "15-30y", "9000"],
                ["green", "2026-09-28", "2026-10-02", "30", "15-30y", "3000"]]
    assert calendar["public_from"].eq("2026-09-25T23:59:59").all() and calendar["tenor_bucket"].tolist()[4:7] == [
        "10-15y", "30y+", "5-10y"]  # 15, 50 and 10 years
    quarter = supply[supply["source"] == "sdl_calendar"].iloc[0]
    assert (quarter["period_start"], quarter["period_end"], quarter["amount_cr"]) == ("2026-10-01", "2026-12-31",
                                                                                       "360820")
    goi = supply[supply["source"] == "gsec_announcement"]
    columns = ["security", "auction_date", "maturity_date", "amount_cr", "greenshoe_cr", "tenor_bucket"]
    assert goi[columns].values.tolist() == [["6.53% GS 2031", "2026-11-20", "2031-09-07", "19000", "2000", "3-5y"],
                                            ["7.71% GS 2066", "2026-11-20", "2066-05-18", "14000", "2000", "30y+"]]
    tbills = supply[supply["source"] == "tbill_announcement"]
    assert tbills["tenor_bucket"].eq("0-1y").all() and tbills["amount_cr"].tolist() == ["8000", "8000", "7000"]
    sdls = supply[supply["source"] == "sdl_announcement"]
    assert sdls["tenor_bucket"].tolist() == ["5-10y", "3-5y", "10-15y", "15-30y", "5-10y", "15-30y"]
    assert sdls["notes"].tolist()[0] == ("new issue: tenor bucket of its 8-year tenor, as for a calendar line; matures "
                                         "2034-11-18 if on the anniversary of the settlement (maturity not printed)")
    assert sdls["notes"].tolist()[1] == ("tenor bucket from the first issue's anniversary, 2030-10-21 (maturity not "
                                         "printed)")  # a reissue
    assert sdls["public_from"].eq("2026-11-13T17:50:00").all() and sdls["public_from_source"].eq("feed").all()


# --- Atomic writes and the lock ----------------------------------------------------------------------

class _Crash(Exception):
    pass


def _crash_on(monkeypatch, name, call):
    """Make ``rbi_store.os.<name>`` raise on its ``call``-th use, as a crash would stop the process there."""
    real = getattr(st.os, name)
    calls = []

    def fake(*args, **kwargs):
        calls.append(args)
        if len(calls) == call:
            raise _Crash(f"{name} call {call}")
        return real(*args, **kwargs)

    monkeypatch.setattr(st.os, name, fake)


def test_a_commit_interrupted_after_its_journal_is_completed_by_the_next_command(tmp_path, monkeypatch):
    clean, crashed = tmp_path / "clean", tmp_path / "crashed"
    st.ingest(clean, [("f.xml", _feed16())], now=T0)
    _crash_on(monkeypatch, "replace", 4)  # the journal, two files, then the crash
    with pytest.raises(_Crash):
        st.ingest(crashed, [("f.xml", _feed16())], now=T0)
    monkeypatch.undo()
    assert (crashed / "journal.json").exists() and not (crashed / "store.lock").exists()
    assert any(p.name.endswith(".tmp-rbistore") for p in crashed.rglob("*"))
    report = st.status(crashed)
    assert report.interrupted and any("interrupted commit" in line for line in report.lines())
    st.ingest(crashed, [], now=T0 + HOUR)  # any command finishes the renames first
    assert _files(crashed) == _files(clean)


def test_a_crash_at_any_rename_of_a_commit_ends_in_the_same_store(tmp_path, monkeypatch):
    clean = tmp_path / "clean"
    st.ingest(clean, [("f.xml", _feed16())], now=T0)
    renames = len(_files(clean)) + 1  # every file, and the journal first
    for call in range(1, renames + 1):
        crashed = tmp_path / f"crash{call}"
        _crash_on(monkeypatch, "replace", call)
        with pytest.raises(_Crash):
            st.ingest(crashed, [("f.xml", _feed16())], now=T0)
        monkeypatch.undo()
        assert (crashed / "journal.json").exists() == (call > 1)  # the journal's own rename is the commit point
        st.ingest(crashed, [], now=T0 + HOUR)  # completes the commit, or finds the store as it was
        st.ingest(crashed, [("f.xml", _feed16())], now=T0)  # then the same ingest again
        assert _files(crashed) == _files(clean), call


def test_a_commit_interrupted_before_its_journal_leaves_the_store_as_it_was(tmp_path, monkeypatch):
    store = tmp_path / "s"
    st.ingest(store, [("f.xml", _feed16())], now=T0)
    before = _files(store)
    _crash_on(monkeypatch, "fsync", 3)  # while the temporary files are written
    with pytest.raises(_Crash):
        st.ingest(store, [("g.xml", _feed17())], now=T0 + HOUR)
    monkeypatch.undo()
    stray = [p for p in store.rglob("*") if p.name.endswith(".tmp-rbistore")]
    assert stray and not (store / "journal.json").exists()
    assert {k: v for k, v in _files(store).items() if not k.endswith(".tmp-rbistore")} == before
    report = st.ingest(store, [("g.xml", _feed17())], now=T0 + HOUR)
    assert report.new == [63985, 63986] and not any(p.name.endswith(".tmp-rbistore") for p in store.rglob("*"))


def _lock(store, **info):
    store.mkdir(exist_ok=True)
    (store / "store.lock").write_text(json.dumps({"command": "poll", "pid": 4242, "host": "another-machine",
                                                  "since": "2026-11-16T13:30:00Z", **info}), encoding="utf-8")


def test_a_held_lock_refuses_other_commands_and_is_never_broken(tmp_path):
    store = tmp_path / "s"
    _lock(store)
    fake = FakeTransport(_ok(_feed16()))
    with pytest.raises(st.StoreLockedError) as held:
        st.poll(store, now=T0, transport=fake)
    assert str(held.value) == (f"store {store} is locked by poll (pid 4242 on another-machine) since "
                               f"2026-11-16T13:30:00Z. Wait for that command to end; if none is running, delete "
                               f"{store / 'store.lock'}.")
    assert fake.requests == []
    with pytest.raises(st.StoreLockedError, match=r"has a stale lock .*: it is 3\.5 hours old\. If no rbi-store "
                                                  r"command is running, delete .*store\.lock and run again"):
        st.ingest(store, [("f.xml", _feed16())], now=T0 + 3 * HOUR)
    assert (store / "store.lock").exists() and not (store / "releases.csv").exists()
    other = tmp_path / "t"  # status reads without the lock, and says who holds it
    st.ingest(other, [("f.xml", _feed16())], now=T0)
    _lock(other)
    assert st.status(other, now=T0).lock.startswith(f"store {other} is locked by poll (pid 4242 on another-machine)")
    assert any(line.startswith("locked: ") for line in st.status(other, now=T0).lines())


def test_a_lock_left_by_a_process_that_ended_is_reported_as_stale(tmp_path):
    import socket

    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    store = tmp_path / "s"
    _lock(store, pid=child.pid, host=socket.gethostname(), since="2026-11-16T13:59:00Z")
    with pytest.raises(st.StoreLockedError, match=f"process {child.pid} is not running"):
        st.ingest(store, [], now=T0)


# --- The poller --------------------------------------------------------------------------------------

def test_poll_ingests_a_new_feed_then_waits_then_asks_conditionally(tmp_path):
    store = tmp_path / "s"
    feed = _feed16()
    fake = FakeTransport(_ok(feed), st.FeedResponse(304, {}, b""))
    first = st.poll(store, now=T0, transport=fake)
    assert (first.outcome, first.exit_code, first.http_status, first.requested) == ("new", 0, 200, True)
    assert first.message == ("new: 5 new releases (63977, 63979-63981, 63984), 0 revised, 0 repaired, 0 failed; "
                             "5 items in the feed")
    assert first.report.new == [63977, 63979, 63980, 63981, 63984] and "state.json" in first.report.written
    url, headers, timeout = fake.requests[0]
    assert url == st.FEED_URL and timeout == 60.0 and "If-None-Match" not in headers
    assert headers["User-Agent"] == st.DEFAULT_USER_AGENT and headers["Accept-Encoding"] == "gzip"
    raw = list((store / "raw").iterdir())
    assert [p.name for p in raw] == [f"pr_feed_20261116T140000Z_{hashlib.sha256(feed).hexdigest()[:12]}.xml"]
    assert _index(store).loc["63977", "source_file"] == f"{raw[0].name}#5"
    state = _state(store)
    assert (state["last_attempt"], state["last_outcome"], state["last_http_status"], state["etag"],
            state["last_modified"], state["last_new"]) == ("2026-11-16T14:00:00Z", "new", 200, 'W/"d07757b7977dd1:0"',
                                                           "Mon, 16 Nov 2026 13:55:50 GMT", "2026-11-16T14:00:00Z")
    assert [key for key, _ in state["items"]] == ["63984", "63981", "63980", "63979", "63977"]

    files = _files(store)
    soon = st.poll(store, now=T0 + 10 * MINUTE, transport=fake)
    assert (soon.outcome, soon.exit_code, soon.requested) == ("too_soon", 0, False) and len(fake.requests) == 1
    assert soon.message == ("too soon: the last request was at 2026-11-16T14:00:00Z (10 min ago); the minimum "
                            "interval is 25 min")
    assert _files(store) == files  # nothing is written when no request is made

    later = st.poll(store, now=T0 + 30 * MINUTE, transport=fake)
    assert (later.outcome, later.exit_code, later.http_status) == ("not_modified", 0, 304)
    headers = fake.requests[1][1]
    assert headers["If-None-Match"] == 'W/"d07757b7977dd1:0"'
    assert headers["If-Modified-Since"] == "Mon, 16 Nov 2026 13:55:50 GMT"
    assert _state(store)["last_success"] == "2026-11-16T14:30:00Z"


def test_poll_does_not_ingest_a_feed_whose_items_are_unchanged(tmp_path):
    store = tmp_path / "s"
    feed = _feed16()
    rewritten = feed.replace(b"<description>Test feed</description>", b"<description>Rewritten</description>")
    fake = FakeTransport(_ok(feed), _ok(feed, etag='W/"e:0"'), _ok(rewritten))
    st.poll(store, now=T0, transport=fake)
    tables = {k: v for k, v in _files(store).items() if k not in ("state.json", "polls.csv")}
    for n in (1, 2):
        result = st.poll(store, now=T0 + n * HOUR, transport=fake)
        assert (result.outcome, result.exit_code, result.report) == ("unchanged", 0, None)
        assert result.message == "unchanged: the feed holds the same 5 items as at 2026-11-16T14:00:00Z"
    assert {k: v for k, v in _files(store).items() if k not in ("state.json", "polls.csv")} == tables
    assert len(fake.requests) == 3 and _csv(store / "polls.csv")["outcome"].tolist() == ["new", "unchanged",
                                                                                        "unchanged"]
    assert _state(store)["etag"] == 'W/"d07757b7977dd1:0"'  # the last answer's validators


def test_poll_after_a_block_waits_for_the_next_india_day_and_stops_after_a_second_block(tmp_path):
    store = tmp_path / "s"
    blocked = st.FeedResponse(418, {}, fx.blocked_page().encode())
    fake = FakeTransport(blocked, blocked, blocked, _ok(_feed16()))
    first = st.poll(store, now=T0, transport=fake)  # 19:30 in India on 16 Nov
    assert (first.outcome, first.exit_code, first.http_status) == ("blocked", 3, 418)
    assert first.message == ("blocked: HTTP 418; no request before 2026-11-17T02:30:00Z (08:00 India time the next "
                             "day); a block of the next request stops the poller")
    assert (_state(store)["cooldown_until"], _state(store)["blocks"]) == ("2026-11-17T02:30:00Z", 1)
    morning = dt.datetime(2026, 11, 17, 2, 30, tzinfo=UTC)
    waiting = st.poll(store, now=morning - MINUTE, transport=fake)
    assert (waiting.outcome, waiting.exit_code, waiting.requested) == ("cooling_down", 3, False)
    second = st.poll(store, now=morning, transport=fake)
    assert second.outcome == "blocked" and second.message == (
        "blocked: HTTP 418; 2 blocks in a row, so the poller has stopped: no request until it is resumed (check the "
        "site in a browser first, then run rbi-store-poll --resume)")
    state = _state(store)
    assert (state["stopped"], state["cooldown_until"], state["blocks"]) == ("2026-11-17T02:30:00Z", None, 2)
    for days in (1, 6):  # however long it waits: resuming is the operator's decision
        later = st.poll(store, now=morning + days * 24 * HOUR, transport=fake)
        assert (later.outcome, later.exit_code, later.requested) == ("stopped", 3, False)
    assert later.message.startswith("stopped after 2 blocks in a row (the last: HTTP 418 at 2026-11-17T02:30:00Z): no "
                                    "request until the poller is resumed")
    assert len(fake.requests) == 2
    again = st.poll(store, now=morning + 7 * 24 * HOUR, transport=fake, resume=True)
    assert again.outcome == "blocked" and "3 blocks in a row" in again.message  # blocked again: stopped at once
    assert _state(store)["stopped"] == "2026-11-24T02:30:00Z" and len(fake.requests) == 3
    too_soon = st.poll(store, now=morning + 7 * 24 * HOUR + 10 * MINUTE, transport=fake, resume=True)
    assert too_soon.outcome == "too_soon" and len(fake.requests) == 3  # resuming keeps the minimum interval
    done = st.poll(store, now=morning + 7 * 24 * HOUR + HOUR, transport=fake, resume=True)
    assert done.outcome == "new" and len(fake.requests) == 4
    state = _state(store)
    assert (state["blocks"], state["cooldown_until"], state["stopped"]) == (0, None, None)
    assert _csv(store / "polls.csv")["http_status"].tolist() == ["418", "418", "418", "200"]


def test_poll_blocked_in_the_morning_asks_nothing_more_that_india_day(tmp_path):
    store = tmp_path / "s"
    fake = FakeTransport(_ok(_feed16()), st.FeedResponse(418, {}, b""), _ok(_feed16()))
    morning = dt.datetime(2026, 11, 17, 2, 30, tzinfo=UTC)  # 08:00 in India
    assert st.poll(store, now=morning, transport=fake).outcome == "new"
    assert st.poll(store, now=morning + 30 * MINUTE, transport=fake).outcome == "blocked"
    for minutes in range(60, 16 * 60, 30):  # every half hour until 23:30 India time: nothing is sent
        assert st.poll(store, now=morning + minutes * MINUTE, transport=fake).outcome == "cooling_down"
    assert len(fake.requests) == 2
    assert st.poll(store, now=morning + 24 * HOUR, transport=fake).outcome == "unchanged"  # 08:00 the next day


def test_poll_records_any_number_of_blocks_without_overflow(tmp_path):
    store = tmp_path / "s"
    blocked = st.FeedResponse(418, {}, b"")
    fake = FakeTransport(*[blocked] * 40)
    now = T0
    for n in range(1, 41):  # the operator resumes after every block: each block is recorded, none overflows
        result = st.poll(store, now=now, transport=fake, resume=True)
        assert result.outcome == "blocked" and _state(store)["blocks"] == n
        now += 24 * HOUR
    assert _state(store)["stopped"] and len(fake.requests) == 40
    assert st.poll(store, now=now + 30 * MINUTE, transport=fake).outcome == "stopped" and len(fake.requests) == 40


def test_poll_treats_403_and_a_refusal_page_as_blocks(tmp_path):
    fake = FakeTransport(st.FeedResponse(403, {}, b""), _ok(fx.blocked_page().encode()))
    first = st.poll(tmp_path / "a", now=T0, transport=fake)
    assert (first.outcome, first.http_status) == ("blocked", 403)
    page = st.poll(tmp_path / "b", now=T0, transport=fake)
    assert page.outcome == "blocked" and "a refusal page in place of the feed" in page.message
    assert "Support ID 123456789" in page.message and not (tmp_path / "b" / "releases.csv").exists()


@pytest.mark.parametrize("answer, outcome, text", [
    (st.FeedResponse(503, {}, b"busy"), "server_error", "server error: HTTP 503; the next poll tries again"),
    (st.FeedResponse(None, error="URLError: timed out"), "network_error",
     "network error: URLError: timed out; the next poll tries again"),
    (st.FeedResponse(301, {"location": "https://website.rbi.org.in/feed"}, b""), "http_error",
     "HTTP 301 to https://website.rbi.org.in/feed, not followed; the next poll tries again"),
    (_ok(b"<html><body>Down for maintenance</body></html>"), "bad_feed",
     "not a readable feed: the answer is a web page, not an RSS feed ('Down for maintenance')"),
    (_ok(fx.feed([])), "bad_feed", "not a readable feed: the feed has no items"),
    (_ok(b"\x1f\x8b broken", **{"content-encoding": "gzip"}), "bad_feed", "not a readable feed: the gzip body"),
    (_ok(b"..", **{"content-encoding": "br"}), "bad_feed", "content-encoding 'br' cannot be decoded"),
])
def test_poll_records_an_error_and_does_not_retry_in_the_same_run(tmp_path, answer, outcome, text):
    store = tmp_path / "s"
    fake = FakeTransport(answer, _ok(_feed16()))
    result = st.poll(store, now=T0, transport=fake)
    assert (result.outcome, result.exit_code) == (outcome, 1) and text in result.message and len(fake.requests) == 1
    state = _state(store)
    assert (state["last_outcome"], state["errors"], state.get("cooldown_until")) == (outcome, 1, None)
    assert not (store / "releases.csv").exists()
    assert st.poll(store, now=T0 + 10 * MINUTE, transport=fake).outcome == "too_soon"
    assert st.poll(store, now=T0 + 30 * MINUTE, transport=fake).outcome == "new" and _state(store)["errors"] == 0


def test_poll_records_the_attempt_before_the_request_is_sent(tmp_path):
    store = tmp_path / "s"
    with pytest.raises(_Crash):
        st.poll(store, now=T0, transport=FakeTransport(_Crash("the process died during the request")))
    state = _state(store)
    assert (state["last_attempt"], state["last_outcome"]) == ("2026-11-16T14:00:00Z", "requesting")
    fake = FakeTransport(_ok(_feed16()))
    assert st.poll(store, now=T0 + 5 * MINUTE, transport=fake).outcome == "too_soon" and fake.requests == []


def test_poll_reports_a_clock_that_moved_back_instead_of_waiting_quietly(tmp_path):
    store = tmp_path / "s"
    fake = FakeTransport(st.FeedResponse(418, {}, b""), _ok(_feed16()))
    st.poll(store, now=T0 + 400 * 24 * HOUR, transport=fake)  # a clock a year and more ahead, then corrected
    result = st.poll(store, now=T0, transport=fake)
    assert (result.outcome, result.exit_code, result.requested) == ("clock_error", 1, False)
    assert result.message.startswith("clock error: the last request is recorded at 2027-12-21T14:00:00Z, after now "
                                     "(2026-11-16T14:00:00Z); no request.")
    state = json.loads((store / "state.json").read_text(encoding="utf-8"))
    del state["poller"]["last_attempt"]
    (store / "state.json").write_text(json.dumps(state), encoding="utf-8")
    result = st.poll(store, now=T0, transport=fake)
    assert result.outcome == "clock_error" and "the cool-down runs until 2027-12-22T02:30:00Z" in result.message
    assert len(fake.requests) == 1


def test_poll_reads_a_gzip_answer_and_names_ids_the_feed_skipped(tmp_path):
    store = tmp_path / "s"
    late = fx.feed([dict(OTHER, prid=63990), dict(OTHER, prid=63989)])
    fake = FakeTransport(_ok(gzip.compress(_feed16()), **{"content-encoding": "gzip"}), _ok(late))
    assert st.poll(store, now=T0, transport=fake).report.new == [63977, 63979, 63980, 63981, 63984]
    result = st.poll(store, now=T0 + HOUR, transport=fake)
    assert result.missed == list(range(63985, 63989)) and result.report.new == [63989, 63990]
    assert result.message == ("new: 2 new releases (63989-63990), 0 revised, 0 repaired, 0 failed; 2 items in the "
                              "feed; possible gap: ids 63985-63988 were never seen (more than 2 releases since the "
                              "last poll?)")
    gaps = _csv(store / "gaps.csv")
    assert gaps[["first", "last", "in_feed_span"]].values.tolist()[-1] == ["63985", "63988", "True"]


def test_poll_sends_a_configurable_generic_client_name_and_validators_only_to_their_url(tmp_path):
    store = tmp_path / "s"
    fake = FakeTransport(_ok(_feed16()), _ok(_feed16()))
    st.poll(store, now=T0, transport=fake, user_agent="feed-check/2.0")
    st.poll(store, now=T0 + HOUR, transport=fake, url="https://www.rbi.org.in/pressreleases_rss.xml?x=1")
    first, second = fake.requests[0][1], fake.requests[1][1]
    assert first["User-Agent"] == "feed-check/2.0" and "If-None-Match" not in second
    for name in ("cr_strat", "cr-strat"):
        assert name not in st.DEFAULT_USER_AGENT.lower()
    assert set(first) == {"User-Agent", "Accept", "Accept-Encoding"}
    with pytest.raises(ValueError, match="one non-empty line"):
        st.poll(store, now=T0 + 2 * HOUR, transport=fake, user_agent="a\r\nX-Header: 1")
    with pytest.raises(ValueError, match="positive number of seconds"):
        st.poll(store, now=T0 + 2 * HOUR, transport=fake, timeout=0)
    assert len(fake.requests) == 2


@pytest.mark.parametrize("argument, value, message", [
    ("timeout", 1e10, "at most 600"),  # the socket layer overflows above about 9.2e9 seconds
    ("timeout", 601, "at most 600"),
    ("min_interval", dt.timedelta(days=1, seconds=1), "from 0 to 24 hours"),
    ("user_agent", "", "one non-empty line"),
    ("user_agent", "feed\treader", "visible ASCII"),
    ("user_agent", "feed reader ₹", "visible ASCII"),  # http.client could not encode it
    ("user_agent", "x" * 201, "at most 200 long"),
    ("url", "https://www.rbi.org.in/press releases_rss.xml", "without spaces"),
    ("url", "https://www.rbi.org.in:99999/pressreleases_rss.xml", "port must be a number from 1 to 65535"),
    ("url", "https://www.rbi.org.in:abc/pressreleases_rss.xml", "port must be a number from 1 to 65535"),
])
def test_poll_refuses_an_argument_the_request_would_fail_on_before_recording_anything(tmp_path, argument, value,
                                                                                        message):
    store = tmp_path / "s"
    fake = FakeTransport(_ok(_feed16()))
    with pytest.raises(ValueError, match=message):
        st.poll(store, now=T0, transport=fake, **{argument: value})
    assert fake.requests == [] and not store.exists()  # no attempt recorded, so the next poll is not "too soon"


@pytest.mark.parametrize("url, message", [
    ("https://rbidocs.rbi.org.in/rdocs/x.pdf", "bot challenge"),
    ("https://website.rbi.org.in/feed.xml", "bot challenge"),
    ("https://RBIDOCS.rbi.org.in./x.pdf", "bot challenge"),
    ("https://www.rbi.org.in/robots.txt", "robots.txt"),
    ("ftp://www.rbi.org.in/pressreleases_rss.xml", "http or https"),
    ("file:///tmp/feed.xml", "http or https"),
])
def test_poll_refuses_hosts_rbi_guards_and_other_schemes(tmp_path, url, message):
    fake = FakeTransport(_ok(_feed16()))
    with pytest.raises(ValueError, match=message):
        st.poll(tmp_path / "s", now=T0, url=url, transport=fake)
    assert fake.requests == []


def test_poll_makes_no_request_when_the_store_cannot_take_the_answer(tmp_path):
    store = tmp_path / "s"
    store.mkdir()
    (store / "releases.csv").write_text("not,a,store\n", encoding="utf-8")
    fake = FakeTransport(_ok(_feed16()))
    with pytest.raises(st.StoreError):
        st.poll(store, now=T0, transport=fake)
    assert fake.requests == [] and not (store / "state.json").exists()


# --- The standard-library transport ------------------------------------------------------------------

class _Response(io.BytesIO):
    status = 200
    headers = {"ETag": 'W/"x:0"', "Content-Type": "text/xml"}


class _Opener:
    def __init__(self, outcome):
        self.outcome, self.requests = outcome, []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def test_urllib_transport_maps_answers_errors_and_redirects():
    ok = _Opener(_Response(b"<rss/>"))
    answer = st.urllib_transport(st.FEED_URL, {"User-Agent": "x"}, 5.0, opener=ok)
    assert (answer.status, answer.headers, answer.body) == (200, {"etag": 'W/"x:0"', "content-type": "text/xml"},
                                                            b"<rss/>")
    request, timeout = ok.requests[0]
    assert (request.get_method(), request.full_url, request.get_header("User-agent"), timeout) == (
        "GET", st.FEED_URL, "x", 5.0)
    teapot = urllib.error.HTTPError(st.FEED_URL, 418, "I'm a teapot", {"Content-Type": "text/html"},
                                    io.BytesIO(b"Unauthorised Access"))
    answer = st.urllib_transport(st.FEED_URL, {}, 5.0, opener=_Opener(teapot))
    assert (answer.status, answer.body, answer.headers) == (418, b"Unauthorised Access", {"content-type": "text/html"})
    answer = st.urllib_transport(st.FEED_URL, {}, 5.0, opener=_Opener(urllib.error.URLError("timed out")))
    assert (answer.status, answer.error) == (None, "URLError: timed out")
    answer = st.urllib_transport(st.FEED_URL, {}, 5.0, opener=_Opener(ConnectionResetError("reset")))
    assert (answer.status, answer.error) == (None, "ConnectionResetError: reset")
    assert st._NoRedirect().redirect_request(None, None, 301, "Moved", {}, "https://elsewhere/") is None


# --- Status ------------------------------------------------------------------------------------------

def test_status_reports_rows_dates_failures_and_the_poller(tmp_path):
    store = tmp_path / "s"
    fake = FakeTransport(_ok(_feed16(fx.mmo_item(_broken_friday()) | dict(prid=63985, title="Money Market "
                                                                                "Operations as on November 16, 2026"))))
    st.poll(store, now=T0, transport=fake)
    report = st.status(store)
    assert report.releases == {"parsed": 4, "failed": 1, "no_parser": 0, "not_recognised": 1, "listed": 0}
    assert (report.first_id, report.last_id, report.revisions) == (63977, 63985, 0)
    kinds = {k["kind"]: k for k in report.kinds}
    assert (kinds["money_market_operations"]["rows"], kinds["money_market_operations"]["first"],
            kinds["money_market_operations"]["last"]) == (77, "2026-11-13", "2026-11-15")
    assert kinds["money_market_operations"]["statuses"] == "1 failed, 3 parsed"
    lines = report.lines()
    assert lines[0] == f"store {store}"
    assert ("releases: 6 (4 parsed, 1 failed, 1 not_recognised); ids 63977 to 63985; last change "
            "2026-11-16T19:30:00") in lines
    assert "  money_market_operations: 4 releases (1 failed, 3 parsed), 77 rows, 2026-11-13 to 2026-11-15" in lines
    assert any(line.startswith("failed: 63985 money_market_operations (Money Market Operations as on November 16, "
                               "2026): failed check: ") for line in lines)
    assert any(line.startswith("poller: last request 2026-11-16T14:00:00Z: new (HTTP 200)") for line in lines)
    with pytest.raises(st.StoreError, match="no store here"):
        st.status(tmp_path / "nothing")


# --- Times a back-test may rely on -------------------------------------------------------------------

def test_a_joined_row_is_public_only_once_every_part_of_it_is(tmp_path):
    store = tmp_path / "s"
    st.ingest(store, [("f.xml", fx.feed([_result_17(), LAF_ANNOUNCEMENT]))], now=T0 + 24 * HOUR)
    ops = st.read_table(store, "liquidity_operations")
    row = ops.iloc[0]
    assert (row["first_public_from"], row["public_from"], row["accepted_cr"]) == (
        pd.Timestamp("2026-11-16 19:25"), pd.Timestamp("2026-11-17 10:35"), 7627.0)
    at_nine = pd.Timestamp("2026-11-17 09:00")  # announced the evening before, result at 10:35
    assert ops[ops["public_from"] <= at_nine].empty  # the rule data/README gives a back-test hides the result
    partly = ops[ops["first_public_from"] <= at_nine].iloc[0]
    assert partly["announcement_public_from"] <= at_nine < partly["result_public_from"]


def test_a_result_checked_against_a_day_without_its_line_is_public_with_that_day_s_release(tmp_path):
    store = tmp_path / "s"
    monday_result = _page("Result of the Overnight Variable Rate Reverse Repo (VRRR) auction held on November 16, 2026",
                          "Nov 16, 2026", "", fx.laf_result_table(("1-day", "50,000", "20,000", "20,000", "5.48",
                                                                   "5.48", "NA")), prid=63983)
    st.ingest(store, [("b.xml", _feed17()), ("vrrr_63983.html", monday_result)], now=T0 + 24 * HOUR)
    ops = _csv(store / "derived" / "liquidity_operations.csv").set_index("auction_date")
    assert ops.loc["2026-11-16", ["mmo_check", "mmo_release_id", "mmo_public_from", "first_public_from",
                                  "public_from"]].tolist() == [
        "missing", "63985", "2026-11-17T09:00:00", "2026-11-16T23:59:59", "2026-11-17T09:00:00"]


def _goi_announcement(rows, auction="November 25, 2026 (Wednesday)", settlement="November 27, 2026 (Friday)"):
    """A GoI auction announcement page of ``rows`` (security, date of repayment, notified amount)."""
    head = ("Sr No", "Security", "Date of Repayment", "Notified Amount (₹ Crore)", "GoI specific Notification",
            "Auction Date", "Settlement Date")
    cells = "".join(f"<tr><td>{n}</td><td>{name}</td><td>{repayment}</td><td>{amount:,}</td>"
                    + (f'<td rowspan="{len(rows)}">F.No.4(1)-B(W&amp;M)/2026 dated November 23, 2026</td>'
                       f'<td rowspan="{len(rows)}">{auction}</td><td rowspan="{len(rows)}">{settlement}</td>'
                       if n == 1 else "") + "</tr>" for n, (name, repayment, amount) in enumerate(rows, start=1))
    total = sum(amount for _, _, amount in rows)
    table = ('<table class="tablebg"><tr>' + "".join(f"<th>{h}</th>" for h in head) + "</tr>" + cells
             + f"<tr><td></td><td>Total</td><td></td><td>{total:,}</td><td></td><td></td><td></td></tr></table>")
    text = ("Government of India has announced the sale (issue/re-issue) of dated securities. GoI will have the option "
            "to retain additional subscription up to ₹2,000 crore against each security. The auction will be "
            "conducted using multiple price method. Payment by successful bidders will have to be made on "
            f"{settlement}.")
    return _page("Auction of Government of India Dated Securities", "Nov 23, 2026", text, table, prid=64010,
                 pr_no="2026-2027/1600")


def test_a_new_issue_and_the_calendar_line_it_is_auctioned_under_share_a_tenor_bucket(tmp_path):
    # Settling on 27 Nov 2026, new 3-, 10- and 15-year issues span one, three and four leap days: on residual maturity
    # (365.25-day years, as supply_monthly.csv) each lands one bucket above its calendar line; a 5-year one does not.
    lines = [f"(i) {n} Year for ₹{a:,} crore" for n, a in ((3, 6000), (5, 7000), (10, 8000), (15, 9000))]
    calendar = _page(fx.GSEC_CALENDAR_TITLE, "Sep 25, 2026", fx.GSEC_CALENDAR_TEXT, fx.gsec_calendar_table(
        weeks=[("November 23-27, 2026", "30,000", lines)], total="30,000"), prid=63668, pr_no="2026-2027/1190")
    rows = [("New GS 2029", "Nov 27, 2029", 6000), ("New GS 2031", "Nov 27, 2031", 7000),
            ("New GS 2036", "Nov 27, 2036", 8000), ("New GS 2041", "Nov 27, 2041", 9000),
            ("6.33% GS 2035", "May 05, 2035", 2000)]  # a reissue: residual maturity, 8.4 years
    st.ingest(tmp_path / "s", [("cal.html", calendar), ("goi.html", _goi_announcement(rows))], now=T0)
    supply = _csv(tmp_path / "s" / "derived" / "supply_calendar.csv")
    planned = supply[supply["source"] == "gsec_calendar"]
    announced = supply[supply["source"] == "gsec_announcement"]
    assert planned[["tenor_years", "tenor_bucket"]].values.tolist() == [["3", "1-3y"], ["5", "3-5y"], ["10", "5-10y"],
                                                                         ["15", "10-15y"]]
    assert announced[["security", "tenor_years", "tenor_bucket"]].values.tolist() == [
        ["New GS 2029", "3", "1-3y"], ["New GS 2031", "5", "3-5y"], ["New GS 2036", "10", "5-10y"],
        ["New GS 2041", "15", "10-15y"], ["6.33% GS 2035", "", "5-10y"]]
    assert announced["notes"].tolist() == [
        "new issue: tenor bucket of its 3-year tenor, as for a calendar line (on residual maturity, as "
        "supply_monthly.csv counts it: 3-5y)",
        "new issue: tenor bucket of its 5-year tenor, as for a calendar line",
        "new issue: tenor bucket of its 10-year tenor, as for a calendar line (on residual maturity, as "
        "supply_monthly.csv counts it: 10-15y)",
        "new issue: tenor bucket of its 15-year tenor, as for a calendar line (on residual maturity, as "
        "supply_monthly.csv counts it: 15-30y)", ""]


def test_a_calendar_line_of_n_years_goes_in_the_bucket_of_the_security_auctioned_under_it():
    from cr_strat.issuance import tenor_bucket

    assert [st.nominal_tenor_bucket(n) for n in (0.5, 1, 2, 3, 5, 7, 10, 14, 15, 30, 40, 50)] == [
        "0-1y", "0-1y", "1-3y", "1-3y", "3-5y", "5-10y", "5-10y", "10-15y", "10-15y", "15-30y", "30y+", "30y+"]
    # reissues auctioned under a 10-year and a 5-year line, on residual maturity as supply_monthly.csv has them
    assert tenor_bucket("2036-05-11", "2026-11-16") == st.nominal_tenor_bucket(10) == "5-10y"
    assert tenor_bucket("2031-09-07", "2026-11-23") == st.nominal_tenor_bucket(5) == "3-5y"
    assert st.nominal_tenor_bucket(None) == "unknown"


def test_read_table_gives_blank_cells_as_missing_values_in_text_columns_too(tmp_path):
    store = tmp_path / "s"
    st.ingest(store, [("f.xml", _feed16())], now=T0)
    marks = st.read_table(store, "money_market_operations")["marks"]
    parsed = rr.parse_releases([_feed16()]).tables["money_market_operations"]
    assert not (marks == "").any() and marks.isna().sum() == parsed["marks"].isna().sum() == 30
    ops = st.read_table(store, "liquidity_operations").set_index("auction_date")
    assert pd.isna(ops.loc["2026-11-13", "window_start"]) and ops.loc["2026-11-17", "window_start"] == "09:30"


# --- The poller: blocks, damaged state, odd answers --------------------------------------------------

def test_poll_records_a_block_whatever_the_count_before_it(tmp_path):
    store = tmp_path / "s"
    st.poll(store, now=T0, transport=FakeTransport(_ok(_feed16())))
    data = json.loads((store / "state.json").read_text(encoding="utf-8"))
    data["poller"]["blocks"] = 32  # weeks of blocks, each resumed by hand
    (store / "state.json").write_text(json.dumps(data), encoding="utf-8")
    fake = FakeTransport(st.FeedResponse(418, {}, b""))
    result = st.poll(store, now=T0 + HOUR, transport=fake)
    assert result.outcome == "blocked" and _state(store)["blocks"] == 33 and _state(store)["stopped"]
    assert st.poll(store, now=T0 + 2 * HOUR, transport=fake).outcome == "stopped" and len(fake.requests) == 1


@pytest.mark.parametrize("key, value, message", [
    ("blocks", "two", "poller value 'blocks' must be a whole number of at least 0, or null, not 'two'"),
    ("errors", -1, "poller value 'errors' must be a whole number of at least 0, or null, not -1"),
    ("last_attempt", 1763301600, "poller value 'last_attempt': 1763301600 is not a time like"),
    ("cooldown_until", "tomorrow", "poller value 'cooldown_until': 'tomorrow' is not a time like"),
    ("items", [["63977"]], "poller value 'items' must be a list of [item, hash] pairs, or null"),
    ("etag", 5, "poller value 'etag' must be text or null"),
])
def test_poll_refuses_a_damaged_state_before_any_request(tmp_path, key, value, message):
    store = tmp_path / "s"
    st.poll(store, now=T0, transport=FakeTransport(_ok(_feed16())))
    data = json.loads((store / "state.json").read_text(encoding="utf-8"))
    data["poller"][key] = value
    (store / "state.json").write_text(json.dumps(data), encoding="utf-8")
    fake = FakeTransport(st.FeedResponse(418, {}, b""))
    with pytest.raises(st.StoreError, match=re.escape(message)):
        st.poll(store, now=T0 + HOUR, transport=fake)
    assert fake.requests == []


def test_poll_makes_no_request_when_the_stored_master_does_not_read(tmp_path):
    store = tmp_path / "s"
    st.ingest(store, [("f.xml", fx.feed([fx.mmo_item(fx.mmo_friday())]))], now=T0)
    (store / "sovereign_master.csv").write_text("isin,name\nIN0020190362,6.45% GS 2029\n", encoding="utf-8")
    fake = FakeTransport(_ok(_feed16()))
    with pytest.raises(st.StoreError, match="sovereign_master.csv: the store's security master cannot be read"):
        st.poll(store, now=T0 + HOUR, transport=fake)
    assert fake.requests == [] and not (store / "state.json").exists()


def test_a_failing_derived_table_keeps_the_rows_read_and_the_previous_derived_tables(tmp_path, monkeypatch):
    store = tmp_path / "s"
    st.ingest(store, [("a.xml", fx.feed([fx.mmo_item(fx.mmo_friday())]))], now=T0)
    before = (store / "derived" / "money_market_daily.csv").read_bytes()

    def broken(*args, **kwargs):
        raise KeyError("a bug")

    monkeypatch.setattr(st, "derive", broken)
    result = st.poll(store, now=T0 + HOUR, transport=FakeTransport(_ok(_feed16())))
    assert (result.outcome, result.exit_code) == ("new", 4)
    assert result.attention == ["derived tables not rebuilt: KeyError: 'a bug' (the previous ones are kept)"]
    assert (store / "derived" / "money_market_daily.csv").read_bytes() == before
    assert len(list((store / "raw").iterdir())) == 2 and _index(store).loc["63980", "status"] == "parsed"
    assert json.loads((store / "state.json").read_text(encoding="utf-8"))["derived"] == {
        "error": "KeyError: 'a bug'", "since": "2026-11-16T15:00:00Z"}
    assert "derived tables: not rebuilt since 2026-11-16T15:00:00Z: KeyError: 'a bug' (the previous ones are kept; " \
           "every ingest tries again)" in st.status(store).lines()
    monkeypatch.undo()
    report = st.ingest(store, [], now=T0 + 2 * HOUR)  # the next ingest rebuilds them and clears the record
    assert report.derive_error == "" and "derived" not in json.loads((store / "state.json").read_text("utf-8"))
    assert len(st.read_table(store, "money_market_daily")) == 3


def test_poll_treats_a_bot_challenge_and_a_second_web_page_in_a_row_as_blocks(tmp_path):
    challenge = (b"<html><head><title></title><script>document.cookie='t=1';location.reload();</script></head>"
                 b"<body><noscript>Please enable JavaScript to view this site.</noscript></body></html>")
    first = st.poll(tmp_path / "a", now=T0, transport=FakeTransport(_ok(challenge)))
    assert first.outcome == "blocked" and "a script-only page in place of content (a bot challenge)" in first.message
    fake = FakeTransport(*[_ok(b"<html><body><p>Down for maintenance</p></body></html>")] * 2)
    one = st.poll(tmp_path / "b", now=T0, transport=fake)
    assert (one.outcome, one.exit_code) == ("bad_feed", 1)
    two = st.poll(tmp_path / "b", now=T0 + 30 * MINUTE, transport=fake)
    assert two.outcome == "blocked" and "a web page in place of the feed, 2 answers in a row ('Down for " \
                                       "maintenance')" in two.message
    assert st.poll(tmp_path / "b", now=T0 + HOUR, transport=fake).outcome == "cooling_down" and len(fake.requests) == 2


def test_poll_of_a_feed_without_release_ids_is_an_error_and_keeps_the_raw_copy(tmp_path):
    store = tmp_path / "s"
    feed = _feed16().replace(b"BS_PressReleaseDisplay.aspx?prid=", b"BS_PressReleaseDisplay.aspx?Id=")
    fake = FakeTransport(_ok(feed), _ok(feed))
    result = st.poll(store, now=T0, transport=fake)
    assert (result.outcome, result.exit_code) == ("bad_feed", 1)
    assert result.message == ("not a readable feed: none of its 5 items has a release id (prid=) in its link (have "
                              "RBI's links changed?); nothing stored; its raw copy is kept")
    assert len(result.attention) == 5 and result.attention[0].startswith("feed item #1 ('RBI to conduct 2-day")
    assert [p.name[:8] for p in (store / "raw").iterdir()] == ["pr_feed_"] and not (store / "tables").exists()
    assert sum(line.startswith("poller: last feed's item #") for line in st.status(store).lines()) == 5
    assert st.poll(store, now=T0 + HOUR, transport=fake).outcome == "bad_feed"  # never 'unchanged': nothing stored
    one = _feed16().replace(b"BS_PressReleaseDisplay.aspx?prid=63979", b"BS_PressReleaseDisplay.aspx?Id=63979")
    result = st.poll(tmp_path / "t", now=T0, transport=FakeTransport(_ok(one)))
    assert (result.outcome, result.exit_code) == ("new", 4) and result.message.endswith("; 1 without a release id")
    assert result.report.new == [63977, 63980, 63981, 63984]


def test_poll_sees_a_change_to_an_item_whose_id_another_item_shares(tmp_path):
    store = tmp_path / "s"
    friday = fx.mmo_item(fx.mmo_friday())
    changed = fx.mmo_friday()
    changed["markets"][1][2] = "5.64"
    fake = FakeTransport(_ok(fx.feed([friday, fx.mmo_item(fx.mmo_saturday()), friday])),
                         _ok(fx.feed([fx.mmo_item(changed), fx.mmo_item(fx.mmo_saturday()), friday])))
    st.poll(store, now=T0, transport=fake)
    result = st.poll(store, now=T0 + HOUR, transport=fake)
    assert result.outcome == "new" and result.report.revised == [63977]


def test_a_poll_that_stores_a_failed_release_exits_4_and_says_why(tmp_path):
    store = tmp_path / "s"
    result = st.poll(store, now=T0, transport=FakeTransport(_ok(fx.feed([fx.mmo_item(_broken_friday())]))))
    assert (result.outcome, result.exit_code) == ("new", 4)
    assert result.attention == [f"release 63977 (money_market_operations) did not parse: "
                                f"{_index(store).loc['63977', 'reason']}"]


# --- Gaps --------------------------------------------------------------------------------------------

def test_gaps_count_from_the_first_poll_and_a_poll_names_the_ids_missed_since_the_previous_one(tmp_path):
    def item(prid, pub):
        return dict(OTHER, prid=prid, pub=pub, title=f"Unrelated release {prid}")

    store = tmp_path / "s"
    goi = _page("Government Stock - Full Auction Results", "Nov 13, 2026", "", fx.results_table([fx.GOI_6_94]),
                prid=63970)
    st.ingest(store, [("goi_63970.html", goi), ("pr_feed_20261102.xml", fx.feed([item(63800, "Mon, 02 Nov 2026 "
                                                                                       "11:00:00")]))],
              now=T0 - HOUR)  # history saved by hand: a page and an old feed
    first = fx.feed([item(i, "Mon, 16 Nov 2026 11:00:00") for i in range(63984, 63974, -1)])
    later = fx.feed([item(i, "Wed, 18 Nov 2026 11:00:00") for i in range(63999, 63989, -1)]
                    + [item(63950, "Wed, 18 Nov 2026 10:00:00")])  # an old id published late
    fake = FakeTransport(_ok(first), _ok(later))
    one = st.poll(store, now=T0, transport=fake)
    assert one.missed == [] and "possible gap" not in one.message and one.exit_code == 0  # no poll before it
    two = st.poll(store, now=T0 + 48 * HOUR, transport=fake)
    assert two.missed == [63985, 63986, 63987, 63988, 63989] and two.exit_code == 4
    assert two.message.endswith("possible gap: ids 63985-63989 were never seen (more than 11 releases since the last "
                                "poll?)")
    assert two.attention[0].startswith("ids 63985-63989 were never seen")
    gaps = _csv(store / "gaps.csv")
    assert gaps[["first", "last", "in_feed_span"]].values.tolist() == [
        ["63801", "63949", "False"], ["63951", "63969", "False"], ["63971", "63974", "False"],
        ["63985", "63989", "True"]]
    assert (_state(store)["first_id"], _state(store)["last_id"]) == (63975, 63999)


def test_ids_the_first_feed_lacks_below_its_top_are_history_not_what_polling_missed(tmp_path):
    def item(prid, pub="Mon, 16 Nov 2026 11:00:00"):
        return dict(OTHER, prid=prid, pub=pub, title=f"Unrelated release {prid}")

    def spans(store):
        return _csv(store / "gaps.csv")[["first", "last", "in_feed_span"]].values.tolist()

    store = tmp_path / "s"  # 63970 was published after 63971-63975 (or they never were): the feed lacks them
    first = fx.feed([item(i) for i in range(63984, 63975, -1)] + [item(63970, "Mon, 16 Nov 2026 10:00:00")])
    later = fx.feed([item(i, "Wed, 18 Nov 2026 11:00:00") for i in range(63999, 63989, -1)])
    fake = FakeTransport(_ok(first, etag='W/"1"'), _ok(later, etag='W/"2"'))
    st.poll(store, now=T0, transport=fake)
    assert spans(store) == [["63971", "63975", "False"]] and _state(store)["first_id"] == 63976
    st.poll(store, now=T0 + 48 * HOUR, transport=fake)  # more than 10 releases between the polls
    assert spans(store) == [["63971", "63975", "False"], ["63985", "63989", "True"]]
    hole = tmp_path / "t"  # a hole inside the first feed: 63980 came before the oldest item, later, or never
    st.poll(hole, now=T0, transport=FakeTransport(_ok(fx.feed([item(i) for i in range(63985, 63974, -1)
                                                               if i != 63980]))))
    assert spans(hole) == [["63980", "63980", "False"]] and _state(hole)["first_id"] == 63981


def test_status_names_as_inside_the_span_polls_received_only_the_ranges_that_are(tmp_path):
    def gap_line(store):
        return next(line for line in st.status(store).lines() if line.startswith("possible gaps, release ids"))

    store = tmp_path / "s"
    goi = _page("Government Stock - Full Auction Results", "Nov 13, 2026", "", fx.results_table([fx.GOI_6_94]),
                prid=63950)
    st.ingest(store, [("goi_63950.html", goi), ("pr_feed_a.xml", _feed16())], now=T0 - HOUR)
    assert gap_line(store) == ("possible gaps, release ids never seen: 3 ranges, 29 in all; no poll has received ids "
                               "yet: 63951 to 63976, 63978, 63982 to 63983")
    feed = fx.feed([dict(OTHER, prid=i, title=f"Unrelated release {i}") for i in range(63994, 63984, -1)])
    st.poll(store, now=T0, transport=FakeTransport(_ok(feed)))
    assert gap_line(store) == ("possible gaps, release ids never seen: 3 ranges, 29 in all; none inside the span polls "
                               "received; outside it: 63951 to 63976, 63978, 63982 to 63983")
    st.poll(store, now=T0 + HOUR, transport=FakeTransport(st.FeedResponse(301, {"location": "https://x.org/f"}, b"")))
    st.poll(store, now=T0 + 2 * HOUR, transport=FakeTransport(st.FeedResponse(200, {}, b"<html><p>x</p></html>")))
    assert ("poller: 2 requests in a row without a usable answer since the last good one (2026-11-16T14:00:00Z): see "
            "the daily routine in data/README.md") in st.status(store).lines()


def test_a_saved_listing_records_ids_titles_and_dates_and_closes_gaps_that_do_not_matter(tmp_path):
    store = tmp_path / "s"
    st.poll(store, now=T0, transport=FakeTransport(_ok(_feed16())))  # 63977, 63979-63981, 63984
    assert _csv(store / "gaps.csv")[["first", "last"]].values.tolist() == [["63978", "63978"], ["63982", "63983"]]
    report = st.ingest(store, [("pr_list.html", fx.listing_page().encode())], now=T0 + HOUR)
    assert report.listed == [63975, 63976, 63982, 63983] and report.unreadable == [] and report.new == []
    index = _index(store)
    assert index.loc["63982", ["status", "kind", "release_date", "title", "rows"]].tolist() == [
        "listed", "other", "2026-11-16", "Some unrelated release", "0"]
    assert index.loc["63983", ["status", "kind"]].tolist() == ["listed", "gsec_announcement"]
    assert _csv(store / "gaps.csv").to_dict("records") == [  # below the unbroken run at the top of the first poll's
        {"gap": "release_id", "first": "63978", "last": "63978", "count": "1", "in_feed_span": "False"},  # feed
        {"gap": "listed", "first": "63976", "last": "63976", "count": "1", "in_feed_span": "False"},  # (63984):
        {"gap": "listed", "first": "63983", "last": "63983", "count": "1", "in_feed_span": "False"}]  # history
    assert st.status(store).releases["listed"] == 4
    page = _page("Auction of Government of India Dated Securities", "Nov 16, 2026", fx.GSEC_ANNOUNCEMENT_TEXT,
                 fx.gsec_announcement_table(), prid=63983)
    report = st.ingest(store, [("goi_63983.html", page)], now=T0 + 2 * HOUR)  # the copy takes the listed entry's place
    assert report.new == [63983]
    assert _index(store).loc["63983", ["status", "first_seen"]].tolist() == ["parsed", "2026-11-16T20:30:00"]
    assert "63983" not in _csv(store / "gaps.csv")["first"].tolist()
    files = _files(store)
    assert st.ingest(store, [("pr_list.html", fx.listing_page().encode())], now=T0 + 3 * HOUR).written == []
    assert _files(store) == files


# --- Copies read again -------------------------------------------------------------------------------

def test_bare_bytes_are_named_by_their_content_so_their_order_does_not_matter(tmp_path):
    friday = fx.mmo_friday()
    friday["markets"][1][2] = "5.62"
    a, b = fx.mmo_page(fx.mmo_friday()).encode(), fx.mmo_page(friday).encode()
    st.ingest(tmp_path / "x", [a, b], now=T0)
    st.ingest(tmp_path / "y", [b, a], now=T0)
    assert _files(tmp_path / "x") == _files(tmp_path / "y")
    last = max(f"<bytes {hashlib.sha256(page).hexdigest()[:12]}>" for page in (a, b))  # pages apply by name
    assert _index(tmp_path / "x").loc["63977", "source_file"] == last


def test_copies_the_store_has_read_before_change_nothing_whatever_their_name(tmp_path):
    store = tmp_path / "s"
    broken = fx.feed([fx.mmo_item(_broken_friday())])
    st.ingest(store, [("pr_feed_a.xml", broken)], now=T0)
    assert st.ingest(store, [("pr_feed_b.xml", broken)], now=T0 + HOUR).written == []  # the same copy, renamed
    assert _index(store).loc["63977", "source_file"] == "pr_feed_a.xml#1"
    report = st.ingest(store, [("mmo_63977.html", fx.mmo_page(fx.mmo_friday()).encode())], now=T0 + 2 * HOUR)
    assert report.repaired == [63977]
    assert st.ingest(store, [("pr_feed_a.xml", broken)], now=T0 + 3 * HOUR).written == []  # as a rebuild from raw/
    assert _csv(store / "revisions.csv")["change"].tolist() == ["repaired"]
    assert len(_index(store).loc["63977", "copies"].split()) == 2


def test_pages_are_kept_in_raw_and_reading_raw_again_after_a_parser_fix_repairs_a_release(tmp_path):
    store = tmp_path / "s"
    friday = fx.mmo_friday()
    friday["net_total"] = "-1,70,099.40"  # 0.10 off line 5 plus line E: refused at the default tolerance
    page = fx.mmo_page(friday).encode()
    report = st.ingest(store, [("mmo_63977.html", page)], now=T0)
    assert report.failed and report.raw_saved == [f"page_20261116T140000Z_{hashlib.sha256(page).hexdigest()[:12]}"
                                                  ".html"]
    kept = sorted((store / "raw").iterdir())
    assert st.ingest(store, kept, now=T0 + HOUR).written == []  # the same parser: the same refusal, nothing changes
    looser = dataclasses.replace(rr.DEFAULT_LIMITS, mmo_sum_tolerance_cr=0.2)  # stands in for a parser fixed since
    fixed = st.ingest(store, kept, now=T0 + 2 * HOUR, limits=looser)
    assert fixed.repaired == [63977] and _index(store).loc["63977", "status"] == "parsed"
    assert fixed.raw_saved == []  # bytes already kept are not saved again


_SAID = ["kind", "status", "reason", "notes", "classified_by"]


def test_reading_raw_again_after_an_update_brings_entries_without_rows_to_what_a_fresh_store_holds(tmp_path,
                                                                                                    monkeypatch):
    store, fresh = tmp_path / "s", tmp_path / "fresh"
    broken = fx.mmo_friday()
    broken["net_total"] = "-1,70,000.00"
    st.ingest(store, [("pr_feed_a.xml", fx.feed([fx.mmo_item(broken), OTHER]))], now=T0)
    assert _index(store).loc["63979", ["kind", "status"]].tolist() == ["other", "not_recognised"]
    real_classify, real_parse = st.classify, st.parse_copy

    def reworded(*args, **kwargs):  # the next version words its refusals differently ...
        result = real_parse(*args, **kwargs)
        return dataclasses.replace(result, reason="refused: " + result.reason) if result.status == "failed" else result

    monkeypatch.setattr(st, "classify", lambda r: ("mpc_resolution", "title") if r.title == OTHER["title"]
                        else real_classify(r))  # ... and recognises this title (a kind without a parser)
    monkeypatch.setattr(st, "parse_copy", reworded)
    kept = sorted((store / "raw").iterdir())
    report = st.ingest(store, kept, now=T0 + HOUR)
    assert report.updated == [63977, 63979] and report.summary().endswith("2 entries updated without new rows; 1 file "
                                                                          "written")
    st.ingest(fresh, kept, now=T0 + HOUR)
    for rid in ("63977", "63979"):
        assert _index(store).loc[rid, _SAID].tolist() == _index(fresh).loc[rid, _SAID].tolist()
    assert _index(store).loc["63979", ["kind", "status"]].tolist() == ["mpc_resolution", "no_parser"]
    assert _index(store).loc["63977", "reason"].startswith("refused: failed check: ")
    assert _index(store).loc["63977", "first_seen"] == "2026-11-16T19:30:00"  # the history stays
    files = _files(store)
    assert st.ingest(store, kept, now=T0 + 2 * HOUR).written == [] and _files(store) == files


def test_reading_raw_again_after_an_update_refreshes_the_notes_of_parsed_releases_and_not_their_rows(tmp_path,
                                                                                                     monkeypatch):
    store = tmp_path / "s"
    st.ingest(store, [("pr_feed_a.xml", _feed16())], now=T0)
    tables = {p.name: p.read_bytes() for p in (store / "tables").iterdir()}
    real_parse = st.parse_copy

    def noted(*args, **kwargs):  # the next version notes something about every release it parses
        result = real_parse(*args, **kwargs)
        return dataclasses.replace(result, notes=[*result.notes, "a note"]) if result.status == "parsed" else result

    monkeypatch.setattr(st, "parse_copy", noted)
    report = st.ingest(store, sorted((store / "raw").iterdir()), now=T0 + HOUR)
    assert report.updated == [63977, 63980, 63981, 63984] and report.revised == []
    assert _index(store).loc["63977", "notes"] == "a note"
    assert {p.name: p.read_bytes() for p in (store / "tables").iterdir()} == tables
    assert _csv(store / "revisions.csv").empty
    daily = _csv(store / "derived" / "money_market_daily.csv")
    assert daily["release_notes"].str.endswith(": a note").all()


def test_some_of_the_copies_read_before_change_nothing_and_all_of_them_give_what_a_fresh_store_holds(tmp_path):
    store, fresh = tmp_path / "s", tmp_path / "fresh"
    broken = fx.mmo_friday()
    broken["net_total"] = "-1,70,000.00"  # a page refused for line F ...
    other = fx.mmo_friday()
    other["net_today"] = "1.00"  # ... and a feed copy refused for line 5
    page, feed = fx.mmo_page(broken).encode(), fx.feed([fx.mmo_item(other)])
    st.ingest(store, [("mmo_63977.html", page)], now=T0)
    st.ingest(store, [("pr_feed_b.xml", feed)], now=T0 + HOUR)  # the copy never read before says why
    assert "net.today" in _index(store).loc["63977", "reason"]
    files = _files(store)
    for n, source in enumerate([("mmo_63977.html", page), ("pr_feed_b.xml", feed), ("again.html", page)]):
        assert st.ingest(store, [source], now=T0 + (2 + n) * HOUR).written == []
    assert _files(store) == files
    kept = sorted((store / "raw").iterdir())
    assert st.ingest(store, kept, now=T0 + 5 * HOUR).updated == [63977]  # every copy: the highest-ranked one, the page
    st.ingest(fresh, kept, now=T0 + 5 * HOUR)
    assert _index(store).loc["63977", _SAID].tolist() == _index(fresh).loc["63977", _SAID].tolist()
    assert "net.total" in _index(store).loc["63977", "reason"]
    for n, sources in enumerate([kept, [("pr_feed_b.xml", feed)], [("mmo_63977.html", page), ("pr_feed_b.xml", feed)]]):
        assert st.ingest(store, sources, now=T0 + (6 + n) * HOUR).written == []


# --- Derived tables: notes, checks, flags, switches, policy rates -----------------------------------

def test_derived_rows_carry_their_releases_notes_and_flag_changed_footnote_marks(tmp_path):
    store = tmp_path / "s"
    monday = fx.mmo_monday()
    monday["reserves"][0][0] = monday["reserves"][0][0].replace("on^^", "on^^#")  # a new mark: a new definition
    st.ingest(store, [("f.xml", fx.feed([fx.mmo_item(fx.mmo_friday()), fx.mmo_item(monday)]))], now=T0 + 24 * HOUR)
    daily = _csv(store / "derived" / "money_market_daily.csv").set_index("as_on_date")
    assert daily["marks_changed"].to_dict() == {"2026-11-13": "", "2026-11-16": "reserves.cash_balances"}
    assert daily.loc["2026-11-16", "release_notes"] == ("63985: marks of reserves.cash_balances changed: '^^#' (was "
                                                       "'^^')")
    title = "Result of the 4-day Variable Rate Repo (VRR) auction held on November 09, 2026"
    values = ("4-day", "50,000", "62,000", "50,003", "5.51", "5.53", "80.65")  # made up
    repo = fx.release_page(title, "Nov 09, 2026", fx.body("", fx.laf_result_table(values, fx.LAF_REPO_LABELS),
                                                          pr_no="2026-2027/1467"), prid=63945)
    st.ingest(store, [("vrr_63945.html", repo.encode())], now=T0 + 25 * HOUR)
    ops = _csv(store / "derived" / "liquidity_operations.csv").set_index("result_id")
    assert ops.loc["63945", "release_notes"].startswith("63945: variable rate repo (injection) result: this layout "
                                                        "was known only from descriptions")


def test_money_market_daily_checks_outstanding_operations_against_the_days_they_were_taken(tmp_path):
    store = tmp_path / "s"
    st.ingest(store, [("a.xml", _feed16())], now=T0)
    daily = _csv(store / "derived" / "money_market_daily.csv").set_index("as_on_date")
    # the longest operation runs 3 days: Sunday is the first day with both earlier days stored, and it agrees
    assert daily["outstanding_check"].to_dict() == {"2026-11-13": "", "2026-11-14": "", "2026-11-15": "agrees"}
    saturday = fx.mmo_saturday()
    saturday["outstanding"]["reverse_repo"] = [("Fri, 13/11/2026", "3", "Mon, 16/11/2026", "39,000.00", "5.49")]
    saturday["net_outstanding"], saturday["net_total"] = "-31,599.50", "-1,66,609.50"  # the release still adds up
    other = tmp_path / "t"
    st.ingest(other, [("f.xml", fx.feed([fx.mmo_item(fx.mmo_friday()), fx.mmo_item(saturday)]))], now=T0)
    daily = _csv(other / "derived" / "money_market_daily.csv").set_index("as_on_date")
    assert daily.loc["2026-11-14", ["outstanding_check", "notes"]].tolist() == [
        "differs", "laf.outstanding.reverse_repo of 2026-11-13 for 3 days: 39000 at 5.49 outstanding, 40000 at 5.49 "
                   "taken on 2026-11-13 (release 63977)"]


def test_money_market_daily_flags_the_last_business_day_of_a_quarter_and_of_march():
    calendar = st.mumbai_settlement_calendar()
    # 31 Mar 2026 (Mahavir Janmakalyanak) and 31 Mar 2025 (Ramzan-Id) are holidays in the shipped list
    assert {d: st._period_end_days(calendar, d) for d in (
        "2026-03-30", "2026-03-31", "2026-06-30", "2026-12-31", "2025-03-28", "2026-11-16", "2027-03-31")} == {
        "2026-03-30": ("True", "True"), "2026-03-31": ("False", "False"), "2026-06-30": ("True", "False"),
        "2026-12-31": ("True", "False"), "2025-03-28": ("True", "True"), "2026-11-16": ("False", "False"),
        "2027-03-31": ("", "")}
    assert {"quarter_end_day", "fy_end_day"} <= set(st.DERIVED_COLUMNS["money_market_daily"])


def test_policy_rates_entered_by_hand_give_the_repo_rate_and_are_checked_against_the_corridor(tmp_path):
    table = tmp_path / "policy.csv"
    table.write_text("effective_date,repo_pct,sdf_pct,msf_pct,source\n2026-08-06,5.25,5.00,5.50,MPC August 2026\n"
                     "2026-10-07,5.50,5.25,5.75,MPC October 2026\n", encoding="utf-8")
    store = tmp_path / "s"
    st.ingest(store, [("a.xml", _feed16()), ("vrrr_63968.html", _result_13())], now=T0, policy_rates=table)
    assert (store / "policy_rates.csv").read_bytes() == table.read_bytes()
    daily = _csv(store / "derived" / "money_market_daily.csv")
    assert daily[["repo_rate_pct", "policy_check"]].values.tolist() == [["5.5", "agrees"]] * 3
    assert _csv(store / "derived" / "liquidity_operations.csv")["notes"].tolist() == ["", ""]  # 5.49 <= 5.50
    wrong = tmp_path / "wrong.csv"
    wrong.write_text("effective_date,repo_pct,sdf_pct,msf_pct,source\n2026-10-07,5.40,5.00,5.75,typed in\n",
                     encoding="utf-8")
    st.ingest(store, [], now=T0 + HOUR, policy_rates=wrong)  # the store keeps the last table given
    daily = _csv(store / "derived" / "money_market_daily.csv")
    assert daily["policy_check"].eq("differs").all() and daily["notes"].iloc[0] == (
        "the corridor differs from the policy rates effective 2026-10-07 (typed in): SDF 5.25 against 5")
    assert _csv(store / "derived" / "liquidity_operations.csv")["notes"].tolist() == [
        "cut-off 5.49 above the repo rate 5.4 in force (policy rates of 2026-10-07)", ""]  # 17 Nov: no result yet
    bad = tmp_path / "bad.csv"
    bad.write_text("effective_date,repo_pct,sdf_pct,msf_pct,source\n2026-10-07,5.50,5.75,5.25,x\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"bad\.csv, line 2: the rates must satisfy SDF <= repo <= MSF"):
        st.ingest(store, [], now=T0 + 2 * HOUR, policy_rates=bad)
    assert (store / "policy_rates.csv").read_bytes() == wrong.read_bytes()


def test_switch_operations_list_both_legs_with_isins_and_maturities_from_the_master(tmp_path):
    switch = fx.body("", fx.switch_table(fx.SWITCH_PAIRS), pr_no="2026-2027/1504")
    feed = fx.feed([dict(title="Result: Conversion/Switch Auction of Government of India Securities", prid=63982,
                         pub="Mon, 16 Nov 2026 14:55:00", html=switch)])
    bare = tmp_path / "bare"
    st.ingest(bare, [("f.xml", feed)], now=T0)
    switches = _csv(bare / "derived" / "switch_operations.csv")
    assert list(switches.columns) == list(st.DERIVED_COLUMNS["switch_operations"]) and len(switches) == 3
    assert switches["notes"].eq("no ISINs: no security master is stored").all()
    master = _master(tmp_path, [("IN0020170042", "7.38% GS 2027", "2027-06-20"),
                                ("IN0020170026", "7.18% GS 2037", "2037-07-24")])
    store = tmp_path / "s"
    st.ingest(store, [("f.xml", feed)], now=T0, master=master)
    first = _csv(store / "derived" / "switch_operations.csv").iloc[0]
    assert (first["source_security"], first["source_isin"], first["source_maturity_date"],
            first["destination_security"], first["destination_isin"], first["destination_maturity_date"],
            first["source_accepted_cr"], first["destination_issued_cr"], first["public_from"]) == (
        "7.38% GS 2027", "IN0020170042", "2027-06-20", "7.18% GS 2037", "IN0020170026", "2037-07-24", "3898.732",
        "3960.932", "2026-11-16T14:55:00")
