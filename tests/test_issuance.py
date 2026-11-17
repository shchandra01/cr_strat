import pandas as pd
import pytest

from cr_strat.auctions import AUCTION_COLUMNS, SWITCH_COLUMNS
from cr_strat.calendars import mumbai_settlement_calendar
from cr_strat.issuance import (
    COVERAGE_COLUMNS,
    LEDGER_COLUMNS,
    OUTSTANDING_COLUMNS,
    auction_coverage,
    build_ledger,
    coverage,
    coverage_path,
    outstanding_by_bucket,
    outstanding_on,
    read_ledger,
    reconcile,
    supply_summary,
    tenor_bucket,
)

T = pd.Timestamp
A_ISIN, B_ISIN = "IN0020190040", "IN0020200153"  # typed-in ISINs; the amounts and dates below are made up


@pytest.fixture(scope="module")
def cal():
    return mumbai_settlement_calendar()


def _auction(day, settle, security, key, amount, *, isin=None, issue_type="reissue", maturity=None, kind="gsec",
             notified=None, flags="", counts=True, tenor=None, basis="total"):
    row = dict.fromkeys(AUCTION_COLUMNS)
    row.update(auction_id=f"{day} {key}", auction_date=T(day), fy="", kind=kind, security=security, name_key=key,
               isin=isin, settlement_date=T(settle), settlement_source="calendar_t1", accepted_cr=amount,
               accepted_basis=basis, notified_cr=notified if notified is not None else amount,
               issue_type=issue_type, maturity_date=T(maturity) if maturity else pd.NaT,
               maturity_source="master" if maturity else "", counts_as_issue=counts, flags=flags,
               sources="fimmda_gsec", source_file="f.xlsx", source_row="r", status="accepted" if amount else "rejected",
               tenor_days=tenor)
    return row


def _table(rows):
    df = pd.DataFrame(rows, columns=list(AUCTION_COLUMNS))
    for col in ("auction_date", "settlement_date", "maturity_date"):
        df[col] = pd.to_datetime(df[col])
    return df


def _switch(day, settle, src, src_isin, dst, dst_isin, out_cr, in_cr):
    row = dict.fromkeys(SWITCH_COLUMNS)
    row.update(auction_id=f"{day} switch", auction_date=T(day), settlement_date=T(settle),
               settlement_source="calendar_t1", source_security=src, source_key=src, source_isin=src_isin,
               destination_security=dst, destination_key=dst, destination_isin=dst_isin,
               source_accepted_cr=out_cr, destination_issued_cr=in_cr, source_file="pr.html", source_row="1")
    return pd.DataFrame([row], columns=list(SWITCH_COLUMNS))


def _base_auctions():
    return _table([
        _auction("2019-04-26", "2019-04-30", "7.69% GS 2043", "7.69% GS 2043", 3000.0, isin=A_ISIN,
                 issue_type="new", maturity="2043-06-17"),
        _auction("2019-05-10", "2019-05-13", "7.69% GS 2043", "7.69% GS 2043", 2000.0, isin=A_ISIN,
                 maturity="2043-06-17"),
        _auction("2019-06-07", "2019-06-10", "7.69% GS 2043", "7.69% GS 2043", 0.0, isin=A_ISIN,
                 maturity="2043-06-17", counts=False),  # rejected: no event
        _auction("2016-01-08", "2016-01-11", "7.59% GS 2026", "7.59% GS 2026", 4000.0, issue_type="new",
                 maturity="2026-01-11"),  # matured; known only by its name key
        _auction("2016-03-04", "2016-03-07", "7.59% GS 2026", "7.59% GS 2026", 1000.0, maturity="2026-01-11"),
        _auction("2018-03-22", "2018-03-23", "8.30% GS 2042", "8.30% GS 2042", 14529.44, flags="non_round_notified",
                 counts=False, maturity="2042-12-31"),
    ])


def test_ledger_events_amounts_and_maturities(cal):
    sw = _switch("2019-11-25", "2019-11-26", "7.69% GS 2043", A_ISIN, "5.77% GS 2030", B_ISIN, 500.0, 480.0)
    led = build_ledger(_base_auctions(), sw, calendar=cal)
    assert list(led.columns) == list(LEDGER_COLUMNS)
    a = led[led["key"] == A_ISIN].set_index("event")
    assert a.loc["issue", "amount_cr"] == 3000 and a.loc["issue", "date"] == T("2019-04-30")
    assert a.loc["reissue", "amount_cr"] == 2000 and a.loc["switch_out", "amount_cr"] == -500
    assert a.loc["maturity", "amount_cr"] == -4500 and a.loc["maturity", "amount_basis"] == "forward_sum"
    b = led[led["key"] == B_ISIN]  # known only from the switch: no maturity date, so redeemed by the end of 2030
    assert b["event"].tolist() == ["switch_in", "maturity"] and b["amount_cr"].sum() == 0
    assert b.iloc[-1]["date"] == T("2030-12-31") and b.iloc[-1]["date_source"] == "name_year"
    assert pd.isna(b.iloc[-1]["maturity_date"]) and "placeholder" in b.iloc[-1]["note"]
    old = led[led["key"] == "7.59% GS 2026"]
    assert old["event"].tolist() == ["issue", "reissue", "maturity"] and old["amount_cr"].sum() == 0
    # 11 Jan 2026 is a Sunday: redeemed the previous business day
    assert old.iloc[-1]["date"] == T("2026-01-09") and old.iloc[-1]["date_source"] == "redemption_preceding"
    assert "8.30% GS 2042" not in set(led["key"])  # not counted as issuance, so no event


def test_shortfall_reading(cal):
    rows = _table([_auction("2020-09-25", "2020-09-28", "5.77% GS 2030", "5.77% GS 2030", 136.103, isin=B_ISIN,
                            notified=18000.0, flags="shortfall", maturity="2030-08-03")])
    devolved = build_ledger(rows, calendar=cal)
    first = devolved.iloc[0]
    assert first["amount_cr"] == 18000 and first["amount_basis"] == "notified_assumed_devolved"
    assert "taken as devolved" in first["note"]
    taken = build_ledger(rows, calendar=cal, shortfall="accepted").iloc[0]
    assert taken["amount_cr"] == 136.103 and taken["amount_basis"] == "total"
    with pytest.raises(ValueError, match="shortfall"):
        build_ledger(rows, shortfall="maybe")


def test_tbill_keys_and_a_redemption_on_a_holiday(cal):
    rows = _table([_auction("2026-08-25", "2026-08-27", "91-Day", "91DTB", 9000.0, kind="tbill", tenor=89,
                            maturity="2026-11-24", issue_type="new")])
    led = build_ledger(rows, calendar=cal)
    assert led["key"].unique().tolist() == ["89DTB 2026-11-24"]
    mat = led[led["event"] == "maturity"].iloc[0]
    assert mat["date"] == T("2026-11-23") and mat["amount_cr"] == -9000  # 24 Nov 2026: Guru Nanak Jayanti
    no_date = _table([_auction("2026-08-25", "2026-08-27", "91-Day", "91DTB", 9000.0, kind="tbill", tenor=91)])
    assert not len(build_ledger(no_date, calendar=cal))  # a T-bill without a maturity date has no key


def test_outstanding_forward(cal):
    led = build_ledger(_base_auctions(), calendar=cal)
    early = outstanding_on(led, "2019-05-01").set_index("key")
    assert list(early.reset_index().columns) == list(OUTSTANDING_COLUMNS)
    assert early.loc[A_ISIN, "outstanding_cr"] == 3000 and early.loc[A_ISIN, "method"] == "forward"
    assert early.loc["7.59% GS 2026", "outstanding_cr"] == 5000
    later = outstanding_on(led, "2026-06-30").set_index("key")
    assert A_ISIN in later.index and "7.59% GS 2026" not in later.index  # matured: zero, left out
    zero = outstanding_on(led, "2026-06-30", include_zero=True).set_index("key")
    assert zero.loc["7.59% GS 2026", "method"] == "matured" and zero.loc["7.59% GS 2026", "outstanding_cr"] == 0
    before = outstanding_on(led, "2015-12-31")
    assert not len(before)
    assert early.loc[A_ISIN, "tenor_bucket"] == "15-30y"


def test_completeness_flags(cal):
    rows = _base_auctions()
    rows.loc[0, "issue_type"] = "reissue"  # as if 7.69% GS 2043 was first issued before the files
    led = build_ledger(rows, calendar=cal, operations_from="2015-01-01")
    out = outstanding_on(led, "2019-05-31").set_index("key")
    a, b = out.loc[A_ISIN], out.loc["7.59% GS 2026"]
    assert not a["issuance_complete"] and "before the first auction file" in a["reasons"]
    assert a["operations_complete"]  # switches and buybacks declared covered from 2015
    # 7.59% GS 2026: issued 2016 with its new issue in the ledger, but no auction file covers 2016-03 to 2019-04
    assert not b["issuance_complete"] and "not all in the inputs" in b["reasons"]
    cov = coverage(led)
    assert list(cov.columns) == list(COVERAGE_COLUMNS)
    assert cov[cov["source"] == "auctions:gsec"][["start", "end"]].values.tolist() == [
        [T("2016-01-08"), T("2016-03-04")], [T("2018-03-22"), T("2018-03-22")], [T("2019-04-26"), T("2019-06-07")]]
    assert cov[cov["source"] == "operations"]["start"].tolist() == [T("2015-01-01")]
    complete = outstanding_on(led, "2016-02-01").set_index("key").loc["7.59% GS 2026"]
    assert complete["issuance_complete"] and complete["operations_complete"] and complete["reasons"] == ""
    no_ops = outstanding_on(build_ledger(_base_auctions(), calendar=cal), "2016-02-01").set_index("key")
    assert not no_ops.loc["7.59% GS 2026", "operations_complete"]
    assert "switches and buybacks not covered" in no_ops.loc["7.59% GS 2026", "reasons"]


def _snapshot(as_of, rows):
    return pd.DataFrame([{"as_of": T(as_of), "isin": i, "name": n, "issue_date": T(iss), "maturity_date": T(m),
                          "outstanding_cr": v} for i, n, iss, m, v in rows])


def test_outstanding_backward_and_forward_from_a_snapshot(cal):
    led = build_ledger(_base_auctions(), calendar=cal)
    snap = _snapshot("2019-12-31", [(A_ISIN, "7.69% GS 2043", "2019-04-30", "2043-06-17", 38920.288),
                                    ("IN0020170042", "6.68% GS 2031", "2017-09-04", "2031-09-17", 70000.0)])
    back = outstanding_on(led, "2019-05-01", snap).set_index("key")
    a = back.loc[A_ISIN]
    assert a["method"] == "backward" and a["anchor_date"] == T("2019-12-31")
    assert a["outstanding_cr"] == pytest.approx(38920.288 - 2000)  # the 13 May reissue is after the date
    other = back.loc["IN0020170042"]  # no events: the snapshot as it is
    assert other["outstanding_cr"] == 70000 and other["method"] == "backward"
    assert outstanding_on(led, "2017-06-30", snap, include_zero=True).set_index("key").loc[
        "IN0020170042", "method"] == "before_issue"
    sw = _switch("2020-02-10", "2020-02-11", "7.69% GS 2043", A_ISIN, "x", None, 1000.0, 0.0)
    later = outstanding_on(build_ledger(_base_auctions(), sw, calendar=cal), "2020-03-31", snap).set_index("key")
    assert later.loc[A_ISIN, "outstanding_cr"] == pytest.approx(38920.288 - 1000)
    assert later.loc[A_ISIN, "method"] == "forward_from_snapshot"
    # the snapshot nearest the date is used
    two = pd.concat([snap, _snapshot("2019-05-15", [(A_ISIN, "7.69% GS 2043", "2019-04-30", "2043-06-17", 5000.0)])])
    near = outstanding_on(led, "2019-05-20", two).set_index("key").loc[A_ISIN]
    assert near["anchor_date"] == T("2019-05-15") and near["outstanding_cr"] == 5000


def test_maturity_amount_from_a_snapshot(cal):
    rows = _table([_auction("2016-03-04", "2016-03-07", "7.59% GS 2026", "7.59% GS 2026", 1000.0,
                            isin="IN0020150069", maturity="2026-01-11")])
    snap = _snapshot("2025-11-14", [("IN0020150069", "7.59% GS 2026", "2016-01-11", "2026-01-11", 12345.0)])
    mat = build_ledger(rows, snapshots=snap, calendar=cal).set_index("event").loc["maturity"]
    assert mat["amount_cr"] == -12345 and mat["amount_basis"] == "snapshot"


def test_reconcile(cal):
    led = build_ledger(_base_auctions(), calendar=cal)
    snap = _snapshot("2020-03-31", [(A_ISIN, "7.69% GS 2043", "2019-04-30", "2043-06-17", 5000.0),
                                    ("IN0020170042", "6.68% GS 2031", "2017-09-04", "2031-09-17", 70000.0)])
    rec = reconcile(led, snap).set_index("key")
    a = rec.loc[A_ISIN]
    assert (a["rbi_cr"], a["ledger_cr"], a["difference_cr"], a["first_event"]) == (5000, 5000, 0, "issue")
    missing = rec.loc["IN0020170042"]
    assert missing["ledger_cr"] == 0 and missing["difference_cr"] == -70000 and missing["reasons"] == \
        "no event in the ledger"
    only = rec.loc["7.59% GS 2026"]  # the ledger holds it, RBI's list does not
    assert pd.isna(only["rbi_cr"]) and only["ledger_cr"] == 5000 and "RBI's list does not" in only["reasons"]
    with pytest.raises(ValueError, match="snapshot"):
        reconcile(led, snap.iloc[0:0])


def test_supply_summary_and_buckets(cal):
    sw = _switch("2019-05-20", "2019-05-21", "7.69% GS 2043", A_ISIN, "5.77% GS 2030", B_ISIN, 500.0, 480.0)
    led = build_ledger(_base_auctions(), sw, calendar=cal)
    s = supply_summary(led, freq="M").set_index(["period", "tenor_bucket"])
    may = s.loc[("2019-05", "15-30y")]
    assert (may["gross_issuance_cr"], may["switch_out_cr"], may["net_cr"]) == (2000, -500, 1500)
    assert s.loc[("2019-05", "unknown"), "switch_in_cr"] == 480  # no maturity date known for the destination
    assert s.loc[("2026-01", "0-1y"), "redemption_cr"] == -5000
    q = supply_summary(led, freq="Q", by_bucket=False)
    assert q.loc[q["period"] == "2019Q2", "gross_issuance_cr"].sum() == 5000
    assert tenor_bucket("2036-05-11", "2026-11-16") == "5-10y" and tenor_bucket(None, "2026-11-16") == "unknown"
    by = outstanding_by_bucket(outstanding_on(led, "2019-06-30"))
    assert by.set_index(["kind", "tenor_bucket"]).loc[("gsec", "15-30y"), "outstanding_cr"] == 4500


def test_coverage_splits_at_long_gaps():
    rows = _table([_auction(d, d, "x", "x", 1.0) for d in ("2015-04-10", "2015-06-05", "2016-02-05", "2016-04-08",
                                                           "2017-01-06")])
    cov = auction_coverage(rows)
    assert cov[["start", "end"]].values.tolist() == [[T("2015-04-10"), T("2015-06-05")],
                                                     [T("2016-02-05"), T("2016-04-08")],
                                                     [T("2017-01-06"), T("2017-01-06")]]
    assert "245 days later" in cov["detail"].iloc[0]


def test_ledger_csv_round_trip(cal, tmp_path):
    led = build_ledger(_base_auctions(), operations_from="2016-01-01", calendar=cal)
    path = tmp_path / "ledger.csv"
    led.to_csv(path, index=False, date_format="%Y-%m-%d")
    coverage(led).to_csv(coverage_path(path), index=False, date_format="%Y-%m-%d")
    back = read_ledger(path)
    assert list(back.columns) == list(LEDGER_COLUMNS) and len(back) == len(led)
    assert back["amount_cr"].sum() == pytest.approx(led["amount_cr"].sum())
    pd.testing.assert_frame_equal(outstanding_on(back, "2019-06-30"), outstanding_on(led, "2019-06-30"),
                                  check_dtype=False)
    assert len(coverage(back)) == len(coverage(led))
    pd.DataFrame({"x": [1]}).to_csv(tmp_path / "bad.csv", index=False)
    with pytest.raises(ValueError, match="not a ledger"):
        read_ledger(tmp_path / "bad.csv")


def test_stock_history_stacks_dates(cal):
    from cr_strat.issuance import stock_history

    led = build_ledger(_base_auctions(), calendar=cal)
    hist = stock_history(led, ["2019-05-01", "2019-06-30"])
    a = hist[hist["key"] == A_ISIN].set_index("date")["outstanding_cr"]
    assert a.to_dict() == {T("2019-05-01"): 3000, T("2019-06-30"): 5000}


def test_a_key_with_only_a_maturity_year_is_redeemed_by_its_end(cal):
    # 'FRB 2024' as FIMMDA names it: a floating-rate bond (no yield to date its maturity) missing from RBI's lists
    rows = _table([_auction("2016-11-04", "2016-11-07", "GOI FLOATING RATE BOND 2024", "FRB 2024", 3000.0, kind="frb"),
                   _auction("2016-11-18", "2016-11-21", "GOI FLOATING RATE BOND 2024", "FRB 2024", 2000.0, kind="frb")])
    rows["maturity_year"] = 2024
    led = build_ledger(rows, calendar=cal)
    mat = led[led["event"] == "maturity"].iloc[0]
    assert (mat["date"], mat["date_source"], mat["amount_cr"]) == (T("2024-12-31"), "name_year", -5000)
    assert pd.isna(mat["maturity_date"]) and "redeemed by the end of 2024" in mat["note"]
    for day in ("2025-01-01", "2026-11-16", "2037-01-01"):
        assert "FRB 2024" not in set(outstanding_on(led, day)["key"])
    gone = outstanding_on(led, "2026-11-16", include_zero=True).set_index("key").loc["FRB 2024"]
    assert gone["method"] == "matured_date_unknown" and gone["outstanding_cr"] == 0
    assert "maturity date unknown" in gone["reasons"]
    during = outstanding_on(led, "2024-06-30").set_index("key").loc["FRB 2024"]
    assert during["outstanding_cr"] == 5000 and "unknown day of 2024" in during["reasons"]
    assert "unknown day" not in outstanding_on(led, "2023-06-30").set_index("key").loc["FRB 2024", "reasons"]
    snap = _snapshot("2026-11-16", [(A_ISIN, "7.69% GS 2043", "2019-04-30", "2043-06-17", 5000.0)])
    assert "FRB 2024" not in set(reconcile(led, snap)["key"])  # matured: not a stock RBI's list lacks
    # RBI's list, where it holds the ISIN, dates what the ledger could not (a made-up ISIN and dates)
    rows_isin = rows.assign(isin="IN0020160068", maturity_year=2024)
    dated = _snapshot("2024-01-31", [("IN0020160068", "FRB 2024", "2016-11-07", "2024-11-07", 5000.0)])
    led2 = build_ledger(rows_isin, calendar=cal)
    assert outstanding_on(led2, "2024-11-30", dated).empty
    assert outstanding_on(led2, "2024-11-06", dated)["outstanding_cr"].tolist() == [5000]


def test_competitive_only_rows_book_the_notified_amount(cal):
    def row(day, accepted, notified, flags="", kind="gsec"):
        return _auction(day, day, "6.68% GS 2040", "6.68% GS 2040", accepted, isin="IN0020250042",
                        maturity="2040-07-07", notified=notified, flags=flags, basis="competitive_only", kind=kind)

    rows = _table([row("2025-07-07", 15990.554, 16000.0), row("2025-08-04", 9000.0, 16000.0, flags="shortfall"),
                   row("2025-09-01", 17990.0, 16000.0, flags="greenshoe")])
    led = build_ledger(rows, calendar=cal).set_index("date")
    full = led.loc[T("2025-07-07")]
    assert full["amount_cr"] == 16000 and full["amount_basis"] == "notified_assumed_full"
    assert "competitive 15990.6 of 16000 notified" in full["note"]
    assert led.loc[T("2025-08-04"), "amount_basis"] == "notified_assumed_devolved"  # the shortfall rule
    green = led.loc[T("2025-09-01")]
    assert green["amount_cr"] == 17990 and green["amount_basis"] == "competitive_only" and "greenshoe" in green["note"]
    as_printed = build_ledger(rows, calendar=cal, competitive_only="accepted").set_index("date")
    assert as_printed.loc[T("2025-07-07"), "amount_cr"] == 15990.554
    assert as_printed.loc[T("2025-07-07"), "amount_basis"] == "competitive_only"
    both = build_ledger(rows, calendar=cal, shortfall="accepted").set_index("date")
    assert both.loc[T("2025-08-04"), "amount_cr"] == 9000  # a shortfall is never read as full allotment
    sdl = _table([row("2025-07-08", 990.0, 1000.0, kind="sdl")])
    assert build_ledger(sdl, calendar=cal).iloc[0]["amount_cr"] == 990  # GoI auctions only
    with pytest.raises(ValueError, match="competitive_only"):
        build_ledger(rows, competitive_only="maybe")


def test_stock_is_zero_from_the_redemption_day(cal):
    rows = _table([_auction("2026-01-05", "2026-01-06", "7.00% GS 2030", "7.00% GS 2030", 1000.0, issue_type="new",
                            maturity="2030-06-16")])  # 16 Jun 2030 is a Sunday: redeemed on Friday 14 Jun
    led = build_ledger(rows, calendar=cal)
    assert led[led["event"] == "maturity"]["date"].tolist() == [T("2030-06-14")]
    assert outstanding_on(led, "2030-06-13")["outstanding_cr"].tolist() == [1000]
    for day in ("2030-06-14", "2030-06-15"):
        assert outstanding_on(led, day).empty
    sat = outstanding_on(led, "2030-06-15", include_zero=True).iloc[0]
    assert sat["method"] == "matured" and sat["anchor_date"] == T("2030-06-14")
    no_events = led[led["event"] != "maturity"]  # a ledger without its maturity rows: the contractual date
    assert outstanding_on(no_events, "2030-06-15")["outstanding_cr"].tolist() == [1000]


def test_rows_without_a_settlement_date(cal):
    rows = _table([_auction("2026-01-05", "2026-01-06", "7.00% GS 2030", "7.00% GS 2030", 1000.0, issue_type="new",
                            maturity="2030-06-16"),
                   _auction("2026-02-05", None, "7.00% GS 2030", "7.00% GS 2030", 500.0, maturity="2030-06-16")])
    led = build_ledger(rows, calendar=cal)
    late = led[led["event"] == "reissue"].iloc[0]
    assert (late["date"], late["date_source"]) == (T("2026-02-06"), "assumed_t1") and "T+1" in late["note"]
    assert outstanding_on(led, "2026-03-01")["outstanding_cr"].tolist() == [1500]
    assert led[led["event"] == "maturity"]["amount_cr"].tolist() == [-1500]
    undated = rows.copy()
    undated.loc[1, "auction_date"] = pd.NaT
    with pytest.raises(ValueError, match="neither a settlement nor an auction date"):
        build_ledger(undated, calendar=cal)
    with pytest.raises(ValueError, match="no date"):
        build_ledger(rows.iloc[:1], buybacks=pd.DataFrame({"date": [None], "amount_cr": [100.0],
                                                            "isin": ["IN0020190040"]}), calendar=cal)


def test_maturity_notes_do_not_depend_on_the_run_date(cal, monkeypatch):
    rows = _table([_auction("2026-10-02", "2026-10-05", "8.15% GS 2026", "8.15% GS 2026", 100.0,
                            isin="IN0020140060", maturity="2026-11-24"),
                   _auction("2026-10-02", "2026-10-05", "6.79% GS 2027", "6.79% GS 2027", 100.0,
                            isin="IN0020170026", maturity="2027-05-15")])
    snap = _snapshot("2026-11-16", [("IN0020140060", "8.15% GS 2026", "2014-11-24", "2026-11-24", 27205.741)])
    notes = []
    for today in ("2026-11-17", "2026-11-25", "2027-06-01"):
        monkeypatch.setattr(pd.Timestamp, "today", classmethod(lambda cls, *a, _d=today, **k: T(_d)))
        led = build_ledger(rows, snapshots=snap, calendar=cal)
        notes.append(led.loc[led["event"] == "maturity", "note"].tolist())
    assert notes[0] == notes[1] == notes[2]
    # both redemptions come after the inputs' last date (the RBI list of 16 Nov 2026): scheduled
    assert all(n.endswith("scheduled (later issuance not known)") for n in notes[0])
    past = build_ledger(rows, snapshots=snap.assign(as_of=T("2027-06-30")), calendar=cal)
    assert not past.loc[past["event"] == "maturity", "note"].str.contains("scheduled").any()
