"""Tests of the S4 event ledger (``event_ledger``).

Synthetic auction tables, ledgers and RBI-store tables built in code, plus a few typed-in facts with their source:
the auction of 6.94% GS 2036 on 13 Nov 2026 and its underwriting (RBI press releases 2026-2027/1492 and /1489), the
T-bills of 11 Nov 2026 (/1478), the switch of 16 Nov 2026 (/1504), each read from copies saved by an earlier job, and
the Mumbai holidays shipped with the package (10 and 24 Nov 2026). No publisher's file or text is copied here.
"""

from __future__ import annotations

import datetime as dt
import random

import numpy as np
import pandas as pd
import pytest

from cr_strat import event_ledger as el
from cr_strat.auctions import AUCTION_COLUMNS, SWITCH_COLUMNS
from cr_strat.bonds import DiscountInstrument, FixedRateBond
from cr_strat.calendars import mumbai_settlement_calendar
from cr_strat.cli import main
from cr_strat.conventions import TBILL
from cr_strat.global_sources import INDEX_EVENT_COLUMNS
from cr_strat.issuance import build_ledger, coverage, coverage_path, outstanding_on, supply_summary
from cr_strat.policy_path import load_mpc_calendar
from cr_strat.rbi_releases import COLUMNS
from cr_strat.rbi_store import DERIVED_COLUMNS, GAP_COLUMNS, RELEASE_INDEX_COLUMNS

T = pd.Timestamp
CAL = mumbai_settlement_calendar()
G1, G2, S1 = "IN0020990011", "IN0020990029", "IN1520990035"  # made-up securities
GS2036 = "IN0020260025"  # 6.94% GS 2036, matures 11 May 2036 (RBI's list of 16 Nov 2026)


# --- Builders -----------------------------------------------------------------------------------------------------

def _auction(day, security, key, amount, *, settle=None, isin=None, issue_type="reissue", maturity=None,
             kind="gsec", notified=None, flags="", counts=True, tenor=None, basis="total", coupon=None, cutoff=None,
             price=None, nc=None, devolvement=None, state=None, sources="fimmda_gsec", source_row="r", status=None,
             first_issue=None, year=None):
    row = dict.fromkeys(AUCTION_COLUMNS)
    settle = settle if settle is not None else CAL.settlement_date(day, 1)
    row.update(auction_id=f"{day} {key}", auction_date=T(day), fy="", kind=kind, security=security, name_key=key,
               coupon_pct=coupon, isin=isin, settlement_date=T(settle), settlement_source="calendar_t1",
               accepted_cr=amount, accepted_basis=basis, notified_cr=notified if notified is not None else amount,
               issue_type=issue_type, maturity_date=T(maturity) if maturity else pd.NaT,
               maturity_source="master" if maturity else "", counts_as_issue=counts, flags=flags, cutoff_yield=cutoff,
               cutoff_price=price, nc_accepted_cr=nc, devolvement_cr=devolvement, state_code=state,
               first_issue_date=T(first_issue) if first_issue else pd.NaT, sources=sources, source_file="f.xlsx",
               source_row=source_row, status=status or ("accepted" if amount else "rejected"), tenor_days=tenor,
               maturity_year=year)
    return row


def _table(rows) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=list(AUCTION_COLUMNS))
    for col in ("auction_date", "settlement_date", "maturity_date", "first_issue_date"):
        df[col] = pd.to_datetime(df[col])
    for col in ("coupon_pct", "notified_cr", "accepted_cr", "cutoff_yield", "cutoff_price", "nc_accepted_cr",
                "devolvement_cr", "comp_accepted_cr"):
        df[col] = pd.to_numeric(df[col]).astype(float)
    df["tenor_days"] = pd.to_numeric(df["tenor_days"]).astype("Int64")
    df["maturity_year"] = pd.to_numeric(df["maturity_year"]).astype("Int64")
    return df


def _base_auctions() -> pd.DataFrame:
    """Made-up auctions of April 2025: two G-secs, an SDL, a T-bill; a shortfall, a rejected line, a reissue
    without a yield."""
    return _table([
        _auction("2025-04-04", "7.00% GS 2035", "7.00% GS 2035", 20000.0, isin=G1, issue_type="new",
                 maturity="2035-04-07", coupon=7.0, cutoff=7.0, price=100.0),
        _auction("2025-04-11", "7.00% GS 2035", "7.00% GS 2035", 15000.0, isin=G1, maturity="2035-04-07", coupon=7.0,
                 cutoff=6.95, price=100.36),
        _auction("2025-04-04", "7.20% GS 2055", "7.20% GS 2055", 10000.0, isin=G2, maturity="2055-06-15", coupon=7.2,
                 cutoff=7.25, price=99.4, first_issue="2024-06-15"),
        _auction("2025-04-11", "7.20% GS 2055", "7.20% GS 2055", 4000.0, isin=G2, maturity="2055-06-15", coupon=7.2,
                 cutoff=7.31, price=98.7, notified=10000.0, flags="shortfall"),
        _auction("2025-04-25", "7.20% GS 2055", "7.20% GS 2055", 0.0, isin=G2, maturity="2055-06-15", coupon=7.2,
                 counts=False),
        _auction("2025-04-08", "7.40% KA SGS 2045", "7.40% KA SGS 2045", 2000.0, isin=S1, kind="sdl",
                 issue_type="new", maturity="2045-04-09", coupon=7.4, cutoff=7.4, state="KA", sources="fimmda_sdl",
                 basis="as_printed"),
        _auction("2025-04-15", "7.40% KA SGS 2045", "7.40% KA SGS 2045", 1000.0, isin=S1, kind="sdl",
                 maturity="2045-04-09", coupon=7.4, state="KA", sources="fimmda_sdl", basis="as_printed"),
        _auction("2025-04-09", "91-Day", "91DTB", 9000.0, kind="tbill", tenor=91, issue_type="new",
                 maturity="2025-07-10", cutoff=6.0, price=98.5262),
    ])


def _switches() -> pd.DataFrame:
    row = dict.fromkeys(SWITCH_COLUMNS)
    row.update(auction_id="2025-05-19 switch 7.20% GS 2055 to 7.00% GS 2035", auction_date=T("2025-05-19"),
               settlement_date=T("2025-05-20"), settlement_source="calendar_t1", source_security="7.20% GS 2055",
               source_key="7.20% GS 2055", source_isin=G2, destination_security="7.00% GS 2035",
               destination_key="7.00% GS 2035", destination_isin=G1, notified_cr=3000.0, source_accepted_cr=2500.0,
               destination_issued_cr=2400.0, dest_cutoff_price=100.2, dest_cutoff_yield=6.97, source_file="pr.html",
               source_row="release 70001 line 1")
    return pd.DataFrame([row], columns=list(SWITCH_COLUMNS))


def _base_ledger(auctions=None):
    buybacks = pd.DataFrame({"date": [T("2025-06-02")], "amount_cr": [500.0], "isin": [G2],
                             "security": ["7.20% GS 2055"]})
    return build_ledger(_base_auctions() if auctions is None else auctions, _switches(), buybacks=buybacks,
                        calendar=CAL, operations_from="2025-04-01")


def _store(path, tables: dict[str, list[dict]], releases=(), gaps=()) -> dict:
    """A directory in the RBI store's layout from synthetic rows, read back as the store reads it."""
    (path / "tables").mkdir(parents=True, exist_ok=True)
    (path / "derived").mkdir(exist_ok=True)
    for name, rows in tables.items():
        cols = COLUMNS[name] if name in COLUMNS else DERIVED_COLUMNS[name]
        sub = "tables" if name in COLUMNS else "derived"
        pd.DataFrame([{c: r.get(c, "") for c in cols} for r in rows], columns=list(cols)).to_csv(
            path / sub / f"{name}.csv", index=False)
    pd.DataFrame([{c: r.get(c, "") for c in RELEASE_INDEX_COLUMNS} for r in releases],
                 columns=list(RELEASE_INDEX_COLUMNS)).to_csv(path / "releases.csv", index=False)
    pd.DataFrame([{c: r.get(c, "") for c in GAP_COLUMNS} for r in gaps], columns=list(GAP_COLUMNS)).to_csv(
        path / "gaps.csv", index=False)
    return el.read_store_tables(path)


# Typed-in facts: RBI press release 2026-2027/1492 (13 Nov 2026), /1489 (underwriting) and /1478 (T-bills, 11 Nov).
GSEC_RESULT = {"release_id": 63970, "release_date": "2026-11-13", "auction_date": "2026-11-13", "line": 1,
               "security": "6.94% GS 2036", "name_key": "6.94% GS 2036", "notified_cr": 34000,
               "comp_received_count": 349, "comp_received_cr": 73016.6, "cutoff_price": 97.43, "cutoff_yield": 7.3203,
               "comp_accepted_count": 146, "comp_accepted_cr": 33947.718, "wap": 97.46, "way": 7.3158,
               "nc_received_count": 8, "nc_received_cr": 52.282, "nc_accepted_count": 8, "nc_accepted_cr": 52.282,
               "total_accepted_cr": 34000, "status": "accepted", "devolvement_cr": 0}
UNDERWRITING = {"release_id": 63967, "release_date": "2026-11-13", "auction_date": "2026-11-13", "line": 1,
                "security": "6.94% GS 2036", "name_key": "6.94% GS 2036", "notified_cr": 34000, "muc_cr": 17010,
                "acu_accepted_cr": 16990, "total_underwritten_cr": 34000, "acu_commission_paise": 0.54}
TBILLS = ((91, "2027-02-11", 8000, 27046.8, 98.5581, 5.8681, 98.5602, 5.8594, 11614.937, 11600, 19200),
          (182, "2027-05-13", 8000, 20021.5, 96.8521, 6.5183, 96.8579, 6.5059, 1019.514, 1000, 8600),
          (364, "2027-11-11", 7000, 21548.5, 93.79, 6.6394, 93.8091, 6.6176, 1794.24, 1700, 8350))


def _nov_2026(tmp_path):
    """The 13 Nov 2026 G-sec auction and the 11 Nov T-bills as the auction table, the ledger and the store hold
    them (typed-in facts above)."""
    rows = [_auction("2026-11-13", "6.94% GS 2036", "6.94% GS 2036", 34000.0, isin=GS2036, maturity="2036-05-11",
                     coupon=6.94, cutoff=7.3203, price=97.43, nc=52.282, devolvement=0.0, sources="rbi_result",
                     source_row="goi_full_63970.html:release 63970 line 1")]
    tbill_rows = []
    for n, (tenor, maturity, notified, bids, cp, cy, wap, way, ncr, nca, total) in enumerate(TBILLS, start=1):
        rows.append(_auction("2026-11-11", f"{tenor}-Day", f"{tenor}DTB", float(total), kind="tbill", tenor=tenor,
                             issue_type="new", maturity=maturity, cutoff=cy, price=cp, nc=float(nca),
                             notified=float(notified), sources="rbi_result",
                             source_row=f"tbill_full_63956.html:release 63956 line {n}"))
        tbill_rows.append({"release_id": 63956, "release_date": "2026-11-11", "auction_date": "2026-11-11",
                           "line": n, "security": f"{tenor}-Day", "tenor_days": tenor, "name_key": f"{tenor}DTB",
                           "notified_cr": notified, "comp_received_cr": bids, "cutoff_price": cp, "cutoff_yield": cy,
                           "wap": wap, "way": way, "nc_received_cr": ncr, "nc_accepted_cr": nca,
                           "total_accepted_cr": total, "status": "accepted"})
    auctions = _table(rows)
    ledger = build_ledger(auctions, calendar=CAL)
    tables = _store(tmp_path / "store", {"gsec_result": [GSEC_RESULT], "underwriting_result": [UNDERWRITING],
                                         "tbill_result": tbill_rows},
                    releases=[{"release_id": 63970, "kind": "gsec_result", "status": "parsed",
                               "release_date": "2026-11-13"},
                              {"release_id": 63956, "kind": "tbill_result", "status": "parsed",
                               "release_date": "2026-11-11"}])
    return auctions, ledger, tables


# --- DV01 ---------------------------------------------------------------------------------------------------------

def test_line_dv01_reproduces_r1_and_names_what_it_cannot_price():
    s = dt.date(2026, 11, 16)
    bond = FixedRateBond(6.94, dt.date(2036, 5, 11))
    y = bond.yield_from_price(97.43, s)
    assert y == pytest.approx(7.3202785589, abs=1e-9)
    dv, status = el.line_dv01("gsec", 6.94, "2036-05-11", y, s)
    assert status == "ok" and dv == pytest.approx(0.0664353131, abs=1e-10)
    assert 34000 * dv / 100 == pytest.approx(22.58800644, abs=1e-7)  # the auction's DV01, crore per bp (R1)
    bill, status = el.line_dv01("tbill", None, "2027-02-11", 5.8681, "2026-11-12")
    assert status == "ok" and bill == DiscountInstrument(dt.date(2027, 2, 11), TBILL).dv01(5.8681, "2026-11-12")
    par, status = el.line_dv01("gsec", None, "2035-04-07", 7.0, "2025-04-07")
    assert status == "par_coupon" and par == FixedRateBond(7.0, dt.date(2035, 4, 7)).dv01(7.0, "2025-04-07")
    assert el.line_dv01("frb", None, "2030-01-01", 7.0, "2025-04-07")[1] == "no_pricer"
    assert el.line_dv01("gsec", 7.0, None, 7.0, "2025-04-07")[1] == "no_maturity"
    assert el.line_dv01("gsec", 7.0, "2035-04-07", None, "2025-04-07")[1] == "no_yield"
    assert el.line_dv01("gsec", 7.0, "2025-04-07", 7.0, "2025-04-07")[1] == "matured"


# --- The ledger's identities --------------------------------------------------------------------------------------

def test_auction_rows_book_the_ledger_amounts_and_e1_e2_hold():
    auctions, ledger = _base_auctions(), _base_ledger()
    events = el.collect_events(auctions=auctions, ledger=ledger, switches=_switches())
    assert list(events.columns) == list(el.EVENT_COLUMNS) and events["event_id"].is_unique
    rows = events.set_index("auction_id")
    short = rows.loc["2025-04-11 7.20% GS 2055"]
    assert short["booked_cr"] == 10000 and short["accepted_cr"] == 4000 and short["amount_basis"] == \
        "notified_assumed_devolved"
    assert short["devolvement_inferred_cr"] == 6000 and "shortfall" in short["result_flags"]  # the result's flag
    rejected = rows.loc["2025-04-25 7.20% GS 2055"]
    assert rejected["booked_cr"] == 0 and "not_counted" in rejected["result_flags"] and \
        "rejected" in rejected["result_flags"]
    bill = rows.loc["2025-04-09 91DTB"]
    assert bill["key"] == "91DTB 2025-07-10" and bill["settlement_date"] == T("2025-04-11")  # 10 Apr 2025 a holiday
    # E1: by settlement month, kind and bucket, against issuance.supply_summary, gross to net
    check = el.check_supply(events, ledger)
    assert len(check) and (check["status"] == "ok").all() and check["difference_cr"].abs().max() == 0
    assert set(check["column"]) == {"gross_cr", "switch_in_cr", "switch_out_cr", "buyback_cr", "redemption_cr",
                                    "net_cr"}
    assert check.loc[check["column"] == "gross_cr", "events_cr"].sum() == supply_summary(ledger)[
        "gross_issuance_cr"].sum()
    # E2: one auction row per ledger issuance row, and back
    join = el.check_ledger_join(events, ledger)
    assert (join["status"] == "ok").all() and len(join) == 7
    copy = events[events["auction_id"] == "2025-04-04 7.00% GS 2035"].assign(event_id="gsec_auction:copy")
    doubled = el.check_ledger_join(pd.concat([events, copy], ignore_index=True), ledger).set_index("auction_id")
    assert doubled.loc["2025-04-04 7.00% GS 2035", "status"] == "duplicated"
    assert (doubled.drop(index="2025-04-04 7.00% GS 2035")["status"] == "ok").all()
    # a ledger from another build is refused, naming the stray auctions
    other = ledger.copy()
    other.loc[other.index[other["event"] == "issue"][0], "auction_id"] = "nowhere"
    with pytest.raises(ValueError, match="not from the same build"):
        el.auction_events(auctions, other)
    twice = ledger.copy()
    issues = twice.index[twice["event"] == "issue"]
    twice.loc[issues[1], "auction_id"] = twice.loc[issues[0], "auction_id"]
    with pytest.raises(ValueError, match="more than once"):
        el.auction_events(auctions, twice)


def test_dv01_arithmetic_of_a_week_e3():
    events = el.collect_events(auctions=_base_auctions(), ledger=_base_ledger(), switches=_switches())
    week = el.supply_table(events)
    lines = events[events["kind"].isin(el.AUCTION_EVENT_KINDS) & (events["event_date"] <= T("2025-04-04"))]
    for rec in lines.to_dict("records"):  # each row: booked x bonds' DV01 at the cut-off for its settlement / 100
        bond = FixedRateBond(rec["coupon_pct"], rec["maturity_date"].date())
        assert rec["dv01_cr_per_bp"] == pytest.approx(
            rec["booked_cr"] * bond.dv01(rec["cutoff_yield"], rec["settlement_date"].date()) / 100, rel=1e-12)
    first = week[week["period"] == "2025-03-31"]
    assert set(first["tenor_bucket"]) == {"5-10y", "30y+"}  # the new 10-year bond: 10.0 years at 365.25 a year
    assert first["gross_dv01"].sum() == pytest.approx(lines["dv01_cr_per_bp"].sum(), rel=1e-12)
    assert first["gross_cr"].sum() == 30000 and first["net_cr"].sum() == 30000
    # the SDL reissue without a yield is priced at its coupon, flagged; FRB-free weeks miss no DV01
    sdl = events[events["auction_id"] == "2025-04-15 7.40% KA SGS 2045"].iloc[0]
    assert sdl["dv01_basis"] == "coupon" and "dv01_at_coupon" in sdl["result_flags"]
    assert (week["dv01_missing_cr"] == 0).all()
    switched = week[week["period"] == "2025-05-19"].set_index("kind").loc["gsec"]
    assert switched["switch_in_cr"].sum() == 2400 and switched["switch_out_cr"].sum() == -2500
    assert (week.loc[week["period"] == "2025-05-19", "switch_out_dv01"] <= 0).all()


def test_stock_on_dates_agrees_with_outstanding_on():
    auctions = _base_auctions()
    ledger = build_ledger(auctions, _switches(), calendar=CAL)
    snapshot = pd.DataFrame({"as_of": [T("2025-05-30"), T("2025-05-30")], "isin": [G2, "IN0020990037"],
                             "outstanding_cr": [16000.0, 5000.0], "name": ["7.20% GS 2055", "6.50% GS 2030"],
                             "issue_date": [T("2024-06-15"), T("2025-05-02")],
                             "maturity_date": [T("2055-06-15"), T("2030-05-02")]})
    dates = ["2025-03-01", "2025-04-04", "2025-04-07", "2025-04-30", "2025-05-01", "2025-05-30", "2025-06-30",
             "2025-07-10", "2025-07-11", "2030-05-02", "2056-01-01"]
    for snap in (None, snapshot):
        for zero in (False, True):
            fast = el.stock_on_dates(ledger, dates, snap, include_zero=zero)
            for d in dates:
                slow = outstanding_on(ledger, d, snap, include_zero=zero).set_index("key")
                mine = fast[fast["date"] == T(d)].set_index("key")
                assert sorted(slow.index) == sorted(mine.index), d
                for col in ("outstanding_cr", "method", "issuance_complete", "operations_complete", "tenor_bucket",
                            "kind", "anchor_date"):
                    assert (slow.loc[mine.index, col].astype(str) == mine[col].astype(str)).all(), (d, col)
    assert set(el.stock_on_dates(ledger, dates, snapshot, include_zero=True)["method"]) >= {
        "forward", "backward", "forward_from_snapshot", "before_issue", "matured"}


def test_supply_shares_need_a_complete_history():
    # a ledger whose every security is first issued in it, with switches and buybacks declared covered
    auctions = _table([
        _auction("2025-04-04", "7.00% GS 2035", "7.00% GS 2035", 20000.0, isin=G1, issue_type="new",
                 maturity="2035-04-07", coupon=7.0, cutoff=7.0, price=100.0),
        _auction("2025-04-11", "7.00% GS 2035", "7.00% GS 2035", 15000.0, isin=G1, maturity="2035-04-07",
                 coupon=7.0, cutoff=6.95, price=100.36),
        _auction("2025-04-25", "7.00% GS 2035", "7.00% GS 2035", 5000.0, isin=G1, maturity="2035-04-07",
                 coupon=7.0, cutoff=6.9, price=100.7)])
    ledger = build_ledger(auctions, calendar=CAL, operations_from="2025-04-01")
    events = el.collect_events(auctions=auctions, ledger=ledger)
    week = el.supply_table(events, ledger=ledger).set_index("period")
    # the stock at the end of the day before the week: the 4 Apr auction settled on 7 Apr, so nothing before
    assert pd.isna(week.loc["2025-03-31", "stock_cr"]) and pd.isna(week.loc["2025-04-07", "stock_cr"])
    third = week.loc["2025-04-21"]  # the 11 Apr reissue settled on 15 Apr (14 Apr a holiday)
    assert third["stock_cr"] == 35000 and bool(third["stock_complete"]) and third["stock_complete_cr"] == 35000
    assert third["gross_share"] == pytest.approx(5000 / 35000)
    stock_dv01 = 35000 * FixedRateBond(7.0, dt.date(2035, 4, 7)).dv01(6.95, dt.date(2025, 4, 21)) / 100
    assert third["stock_dv01"] == pytest.approx(stock_dv01, rel=1e-12)  # at its latest cut-off known, 6.95
    assert third["dv01_share"] == pytest.approx(third["gross_dv01"] / stock_dv01)
    assert third["auctions_covered"] == "yes" and week.loc["2025-03-31", "auctions_covered"] == "partly"
    coupon = el.supply_table(events, ledger=ledger, settings=el.EventSettings(stock_dv01_basis="coupon"))
    at_coupon = coupon.set_index("period").loc["2025-04-21", "stock_dv01"]
    assert at_coupon == pytest.approx(35000 * FixedRateBond(7.0, dt.date(2035, 4, 7)).dv01(
        7.0, dt.date(2025, 4, 21)) / 100, rel=1e-12)
    # without declared switch coverage the stock is incomplete and the shares blank
    open_ledger = build_ledger(auctions, calendar=CAL)
    blank = el.supply_table(el.collect_events(auctions=auctions, ledger=open_ledger), ledger=open_ledger)
    blank = blank.set_index("period").loc["2025-04-21"]
    assert not bool(blank["stock_complete"]) and pd.isna(blank["gross_share"]) and blank["stock_cr"] == 35000
    assert blank["stock_complete_cr"] == 0


# --- Results, announcements, metrics (typed-in Nov 2026 facts) ------------------------------------------------------

def test_metrics_of_the_13_and_11_nov_2026_auctions_r11(tmp_path):
    auctions, ledger, tables = _nov_2026(tmp_path)
    events = el.collect_events(auctions=auctions, ledger=ledger, tables=tables)
    m = el.auction_metrics(events).set_index("key")
    g = m.loc[GS2036]
    assert round(g["bid_cover"], 4) == 2.1475 and round(g["bid_cover_all"], 4) == 2.1491
    assert g["tail_bp"] == pytest.approx(0.45, abs=1e-9) and g["tail_price"] == pytest.approx(0.03, abs=1e-9)
    assert g["nc_share"] == pytest.approx(52.282 / 34000) and g["devolvement_share"] == 0
    assert g["commission_paise"] == 0.54
    for tenor, maturity, cover, tail in ((91, "2027-02-11", 3.3808, 0.87), (182, "2027-05-13", 2.5027, 1.24),
                                         (364, "2027-11-11", 3.0784, 2.18)):
        b = m.loc[f"{tenor}DTB {maturity}"]
        assert round(b["bid_cover"], 4) == cover and b["tail_bp"] == pytest.approx(tail, abs=1e-9)
    assert round(m.loc["91DTB 2027-02-11", "bid_cover_all"], 4) == 4.8327  # 11,600 crore outside the notified
    uw = events[events["kind"] == "underwriting"].iloc[0]
    assert uw["key"] == GS2036 and uw["commission_paise"] == 0.54 and uw["accepted_cr"] == 34000
    gsec = events[events["key"] == GS2036].set_index("kind").loc["gsec_auction"]
    assert gsec["dv01_cr_per_bp"] == pytest.approx(34000 * FixedRateBond(6.94, dt.date(2036, 5, 11)).dv01(
        7.3203, dt.date(2026, 11, 16)) / 100, rel=1e-12)


def test_result_times_observed_saved_page_end_of_day_and_fixed(tmp_path):
    rows = [
        _auction("2026-11-17", "7.66% MP SGS 2048", "7.66% MP SGS 2048", 1000.0, kind="sdl", maturity="2048-11-18",
                 coupon=7.66, cutoff=7.6795, price=99.78, sources="rbi_result", state="MP",
                 source_row="sdl_full_63989.html:release 63989 line 7"),
        _auction("2026-11-13", "6.94% GS 2036", "6.94% GS 2036", 34000.0, isin=GS2036, maturity="2036-05-11",
                 coupon=6.94, cutoff=7.3203, price=97.43, sources="rbi_result",
                 source_row="goi_full_63970.html:release 63970 line 1"),
        _auction("2018-06-08", "7.17% GS 2028", "7.17% GS 2028", 3000.0, maturity="2028-01-08", coupon=7.17,
                 cutoff=7.9, price=95.1),
        _auction("2021-03-10", "91-Day", "91DTB", 5000.0, kind="tbill", tenor=91, issue_type="new",
                 maturity="2021-06-10", cutoff=3.2, price=99.2085),
        _auction("2018-06-12", "8.10% MH SGS 2028", "8.10% MH SGS 2028", 1000.0, kind="sdl", maturity="2028-06-13",
                 coupon=8.1, cutoff=8.1, state="MH", sources="fimmda_sdl", issue_type="new")]
    auctions = _table(rows)
    ledger = build_ledger(auctions, calendar=CAL)
    releases = [{"release_id": 63989, "kind": "sdl_result", "release_date": "2026-11-17",
                 "published": "2026-11-17T16:40:00"},
                {"release_id": 63970, "kind": "gsec_result", "release_date": "2026-11-13"}]
    tables = _store(tmp_path / "s", {}, releases=releases)
    got = el.auction_events(auctions, ledger, tables=tables, releases=tables["releases"]).set_index("auction_id")
    assert got.loc["2026-11-17 7.66% MP SGS 2048", "result_public_from"] == T("2026-11-17 16:40")
    assert got.loc["2026-11-17 7.66% MP SGS 2048", "result_basis"] == "feed"
    assert not got.loc["2026-11-17 7.66% MP SGS 2048", "result_estimated"]
    assert got.loc["2026-11-13 6.94% GS 2036", "result_public_from"] == T("2026-11-13 23:59:59")
    assert got.loc["2026-11-13 6.94% GS 2036", "result_basis"] == "release_date"
    assert got.loc["2018-06-08 7.17% GS 2028", "result_basis"] == "end_of_day"
    assert got.loc["2018-06-08 7.17% GS 2028", "result_public_from"] == T("2018-06-08 23:59:59")
    assert bool(got.loc["2018-06-08 7.17% GS 2028", "result_estimated"])
    fixed = el.auction_events(auctions, ledger, tables=tables, releases=tables["releases"],
                              settings=el.EventSettings(result_rule="rbi_fixed_time")).set_index("auction_id")
    assert fixed.loc["2018-06-08 7.17% GS 2028", "result_public_from"] == T("2018-06-08 14:30")
    assert fixed.loc["2021-03-10 91DTB", "result_public_from"] == T("2021-03-10 15:30")
    assert fixed.loc["2018-06-12 8.10% MH SGS 2028", "result_basis"] == "end_of_day"  # no SDL time known
    assert fixed.loc["2026-11-17 7.66% MP SGS 2048", "result_basis"] == "feed"  # a print beats the rule
    # E4: no result before its announcement
    for frame in (got, fixed):
        both = frame.dropna(subset=["announcement_public_from", "result_public_from"])
        assert (both["result_public_from"] >= both["announcement_public_from"]).all()


def test_announcement_rules_across_holidays():
    conservative = el.EventSettings()
    typical = el.EventSettings(announcement_rule="typical")
    # T-bills on Wed 11 Nov 2026: the business day before is Mon 9 Nov (10 Nov a holiday)
    assert el.estimated_announcement("tbill", "2026-11-11", CAL, conservative)[:2] == (T("2026-11-09 18:00"),
                                                                                       "estimated")
    assert el.estimated_announcement("gsec", "2026-11-13", CAL, typical)[:2] == (T("2026-11-09 18:00"), "typical")
    assert el.estimated_announcement("gsec", "2026-11-20", CAL, typical)[0] == T("2026-11-16 18:00")
    assert el.estimated_announcement("sdl", "2026-11-17", CAL, typical)[0] == T("2026-11-13 18:00")
    assert el.estimated_announcement("tbill", "2026-11-18", CAL, typical)[0] == T("2026-11-13 18:00")
    # the Monday of the week is a holiday (26 Jan 2026): the next business day; the Friday before is (3 Apr 2026)
    assert el.estimated_announcement("gsec", "2026-01-30", CAL, typical)[0] == T("2026-01-27 18:00")
    assert el.estimated_announcement("tbill", "2026-04-08", CAL, typical)[0] == T("2026-04-06 18:00")
    # beyond the holiday list: weekdays only, flagged projected
    when, basis, projected = el.estimated_announcement("gsec", "2027-01-08", CAL, conservative)
    assert when == T("2027-01-07 18:00") and projected


def test_observed_announcements_and_auctions_known_only_from_releases(tmp_path):
    auctions, ledger, _ = _nov_2026(tmp_path)
    tables = _store(tmp_path / "s2", {
        "gsec_announcement": [{"release_id": 63983, "release_date": "2026-11-16", "published": "2026-11-16T17:45:00",
                               "auction_date": "2026-11-20", "settlement_date": "2026-11-23", "line": 1,
                               "security": "6.53% GS 2031", "name_key": "6.53% GS 2031",
                               "repayment_date": "2031-09-07", "notified_cr": 19000, "greenshoe_cr": 2000}],
        "tbill_announcement": [{"release_id": 63975, "release_date": "2026-11-13", "published": "2026-11-13T17:45:00",
                                "auction_date": "2026-11-18", "settlement_date": "2026-11-19", "line": 1,
                                "security": "91-Day", "tenor_days": 91, "maturity_date": "2027-02-18",
                                "notified_cr": 8000}],
        "sdl_result": [{"release_id": 63989, "release_date": "2026-11-17", "published": "2026-11-17T16:40:00",
                        "auction_date": "2026-11-17", "line": 1, "security": "7.66% MP SGS 2048", "state_code": "MP",
                        "maturity_year": 2048, "issue_type": "reissue", "coupon_pct": 7.66,
                        "first_issue_date": "2025-11-18", "name_key": "7.66% MP SGS 2048", "notified_cr": 1000,
                        "comp_received_cr": 3610, "cutoff_yield": 7.6795, "cutoff_price": 99.78, "way": 7.66,
                        "total_accepted_cr": 1000, "status": "accepted"}],
        "sdl_announcement": [{"release_id": 63976, "release_date": "2026-11-13", "published": "2026-11-13T17:50:00",
                              "auction_date": "2026-11-17", "settlement_date": "2026-11-18", "line": 1,
                              "state_code": "MP", "issue_type": "reissue", "security": "7.66% MP SGS 2048",
                              "coupon_pct": 7.66, "maturity_year": 2048, "name_key": "7.66% MP SGS 2048",
                              "notified_cr": 1000}]})
    events = el.auction_events(auctions, ledger, tables=tables).set_index("event_id")
    ann = events.loc["gsec_auction:2026-11-20:6.53% GS 2031"]
    assert "announced_only" in ann["flags"] and ann["announcement_public_from"] == T("2026-11-16 17:45")
    assert ann["announcement_basis"] == "feed" and pd.isna(ann["result_public_from"]) and ann["notified_cr"] == 19000
    assert "greenshoe option" not in ann["notes"]  # the option shows on a matched auction row only
    bill = events.loc["tbill_auction:2026-11-18:91DTB 2027-02-18"]
    assert "announced_only" in bill["flags"] and bill["settlement_date"] == T("2026-11-19")
    sdl = events.loc["sdl_auction:2026-11-17:7.66% MP SGS 2048"]
    assert set(sdl["result_flags"].split(";")) == {"not_in_ledger", "result_only"} and pd.isna(sdl["booked_cr"])
    assert sdl["flags"] == ""
    assert sdl["announcement_public_from"] == T("2026-11-13 17:50") and sdl["result_public_from"] == T(
        "2026-11-17 16:40")
    assert sdl["maturity_date"] == T("2048-11-18")  # the first issue's anniversary, noted
    assert sdl["tail_bp"] == pytest.approx(1.95, abs=1e-9) and sdl["bids_cr"] == 3610


def test_events_are_never_visible_before_their_announcement(tmp_path):
    auctions, ledger, tables = _nov_2026(tmp_path)
    events = el.collect_events(auctions=auctions, ledger=ledger, tables=tables)
    gsec_id = f"gsec_auction:2026-11-13:{GS2036}"
    before = el.events_as_of(events, "2026-11-12T17:59:59")  # the estimated announcement is 18:00 on 12 Nov
    assert gsec_id not in set(before["event_id"])
    during = el.events_as_of(events, "2026-11-13T20:00:00").set_index("event_id")
    assert during.loc[gsec_id, "notified_cr"] == 34000  # announced: the notified amount is known
    assert pd.isna(during.loc[gsec_id, "cutoff_yield"]) and pd.isna(during.loc[gsec_id, "booked_cr"])
    after = el.events_as_of(events, dt.date(2026, 11, 13)).set_index("event_id")  # a date: the end of that day
    assert after.loc[gsec_id, "cutoff_yield"] == 7.3203 and after.loc[gsec_id, "bids_cr"] == 73016.6
    later = el.events_as_of(events, "2026-11-16T09:00:00")
    for c in el.RESULT_FIELDS:  # whatever is visible carries no result before its time
        hidden = later[later["result_public_from"].isna() | (later["result_public_from"] > T("2026-11-16 09:00"))]
        assert hidden[c].isna().all() or (hidden[c] == "").all(), c
    with pytest.raises(ValueError, match="needs a time"):
        el.events_as_of(events, "not a time")


# --- Switches, operations, calendars, MPC, macro, index events ---------------------------------------------------

def test_switch_legs_of_16_nov_2026_r15(tmp_path):
    # typed-in facts of RBI press release 2026-2027/1504 (destination maturities from RBI's list)
    pairs = (("7.38% GS 2027", "IN0020220037", "2027-06-20", "7.18% GS 2037", "IN0020230077", "2037-07-24",
              3898.732, 3960.932, 98.51, 7.3819),
             ("8.26% GS 2027", "IN0020070036", "2027-08-02", "7.40% GS 2062", "IN0020220094", "2062-09-19",
              2000.0, 2122.423, 95.08, 7.8093),
             ("7.37% GS 2028", "IN0020230101", "2028-10-23", "6.83% GS 2039", "IN0020080050", "2039-01-19",
              4000.0, 4232.782, 95.54, 7.3901),
             ("7.10% GS 2029", "IN0020220011", "2029-04-18", "6.64% GS 2035", "IN0020210020", "2035-06-16",
              250.0, 261.578, 95.89, 7.2915),
             ("7.04% GS 2029", "IN0020240050", "2029-06-03", "7.41% GS 2036", "IN0020220102", "2036-12-19",
              3658.204, 3657.111, 100.28, 7.3688))
    rows, ops, master = [], [], []
    for n, (src, src_isin, src_mat, dst, dst_isin, dst_mat, out_cr, in_cr, price, y) in enumerate(pairs, start=1):
        row = dict.fromkeys(SWITCH_COLUMNS)
        row.update(auction_id=f"2026-11-16 switch {src} to {dst}", auction_date=T("2026-11-16"),
                   settlement_date=T("2026-11-17"), settlement_source="calendar_t1", source_security=src,
                   source_key=src, source_isin=src_isin, destination_security=dst, destination_key=dst,
                   destination_isin=dst_isin, source_accepted_cr=out_cr, destination_issued_cr=in_cr,
                   dest_cutoff_price=price, dest_cutoff_yield=y, source_file="switch.html",
                   source_row=f"release 63982 line {n}")
        rows.append(row)
        ops.append({"auction_date": "2026-11-16", "line": n, "source_security": src, "destination_security": dst,
                    "source_accepted_cr": out_cr, "destination_issued_cr": in_cr, "dest_cutoff_price": price,
                    "dest_cutoff_yield": y, "release_id": 63982, "release_date": "2026-11-16",
                    "public_from": "2026-11-16T14:55:00", "public_from_source": "feed", "notified_cr": 5000})
        master += [{"isin": dst_isin, "maturity_date": T(dst_mat)}, {"isin": src_isin, "maturity_date": T(src_mat)}]
    switches = pd.DataFrame(rows, columns=list(SWITCH_COLUMNS))
    master = pd.DataFrame(master)
    ledger = build_ledger(_table([]), switches, master=master, calendar=CAL)
    tables = _store(tmp_path / "s", {"switch_operations": ops})
    events = el.switch_events(ledger, switches=switches, tables=tables)
    legs_in, legs_out = events[events["kind"] == "switch_in"], events[events["kind"] == "switch_out"]
    assert round(legs_in["dv01_cr_per_bp"].sum(), 4) == 11.2998
    assert sorted(round(v, 4) for v in legs_in["dv01_cr_per_bp"]) == [0.1605, 2.435, 2.5767, 2.8659, 3.2616]
    assert legs_in["booked_cr"].sum() == pytest.approx(14234.826) and legs_out["booked_cr"].sum() == pytest.approx(
        -13806.936)
    assert (events["result_public_from"] == T("2026-11-16 14:55")).all() and (events["result_basis"] == "feed").all()
    assert (events["announcement_basis"] == "estimated").all()
    assert (legs_out["dv01_basis"] == "coupon").all() and (legs_out["dv01_cr_per_bp"] < 0).all()
    # with a yield known before the switch, the source leg is priced there
    known = el.empty_events()
    known = el.build_event_ledger(el._events_frame([el._event(
        event_id="gsec_auction:2026-10-30:IN0020220037", kind="gsec_auction", event_date=T("2026-10-30"),
        key="IN0020220037", cutoff_yield=6.4, result_public_from=T("2026-10-30 23:59:59"), result_basis="end_of_day")]))
    priced = el.switch_events(ledger, switches=switches, tables=tables, yields=known).set_index("key")
    leg = priced[priced["kind"] == "switch_out"].loc["IN0020220037"]
    assert leg["dv01_basis"] == "last_known_yield" and "6.4%" in leg["result_notes"]
    assert leg["dv01_cr_per_bp"] == pytest.approx(-3898.732 * FixedRateBond(7.38, dt.date(2027, 6, 20)).dv01(
        6.4, dt.date(2026, 11, 17)) / 100, rel=1e-12)


def test_open_market_and_liquidity_operations(tmp_path):
    tables = _store(tmp_path / "s", {
        "omo_operations": [{"operation": "purchase", "auction_date": "2026-03-13", "settlement_date": "2026-03-16",
                            "line": 1, "security": "6.45% GS 2029", "name_key": "6.45% GS 2029",
                            "isin": "IN0020190362", "maturity_date": "2029-10-07", "aggregate_notified_cr": 50000,
                            "total_offered_cr": 107869, "total_accepted_cr": 50000, "offered_cr": 27844,
                            "accepted_cr": 13006, "cutoff_yield": 6.1187, "cutoff_price": 101.04, "way": 6.1408,
                            "wap": 100.97, "liquidity_effect_cr": 13006, "announcement_id": 62367,
                            "announcement_public_from": "2026-03-11T23:59:59", "result_id": 62379,
                            "result_public_from": "2026-03-13T23:59:59"}],
        "liquidity_operations": [
            {"operation": "variable_rate_reverse_repo", "direction": "reverse_repo", "auction_date": "2026-11-17",
             "tenor_days": 2, "reversal_date": "2026-11-19", "announcement_id": 63984,
             "announcement_public_from": "2026-11-16T19:25:00", "notified_cr": 50000, "result_id": 63986,
             "result_public_from": "2026-11-17T10:35:00", "received_cr": 7627, "accepted_cr": 7627,
             "cutoff_rate_pct": 5.49, "wa_rate_pct": 5.49, "liquidity_effect_cr": -7627},
            {"operation": "variable_rate_reverse_repo", "direction": "reverse_repo", "auction_date": "2026-11-13",
             "tenor_days": 3, "result_id": 63968, "result_public_from": "2026-11-13T23:59:59", "accepted_cr": 38592,
             "cutoff_rate_pct": 5.49, "liquidity_effect_cr": -38592, "mmo_check": "agrees"}]})
    ops = el.operation_events(tables).set_index("event_id")
    omo = ops.loc["omo_purchase:2026-03-13:IN0020190362"]
    assert omo["accepted_cr"] == 13006 and pd.isna(omo["booked_cr"]) and omo["liquidity_effect_cr"] == 13006
    assert omo["dv01_cr_per_bp"] == pytest.approx(13006 * FixedRateBond(6.45, dt.date(2029, 10, 7)).dv01(
        6.1187, dt.date(2026, 3, 16)) / 100, rel=1e-12)
    # RBI buys the cheapest offers first, so the tail is the weighted average less the cut-off: 6.1408 - 6.1187
    assert omo["announcement_basis"] == "release_date" and omo["tail_bp"] == pytest.approx(2.21, abs=1e-9)
    vrrr = ops.loc["vrrr:2026-11-17:2d"]
    assert vrrr["announcement_public_from"] == T("2026-11-16 19:25") and vrrr["announcement_basis"] == "feed"
    assert vrrr["rate_pct"] == 5.49 and vrrr["liquidity_effect_cr"] == -7627 and vrrr["reversal_date"] == T(
        "2026-11-19")
    early = ops.loc["vrrr:2026-11-13:3d"]
    assert pd.isna(early["announcement_public_from"]) and early["result_basis"] == "release_date"
    assert "mmo_agrees" in early["result_flags"]
    week = el.supply_table(el.build_event_ledger(el.operation_events(tables)))
    assert week["omo_purchase_cr"].sum() == 13006 and week["market_net_cr"].sum() == -13006


def test_calendars_mpc_macro_and_index_events(tmp_path):
    tables = _store(tmp_path / "s", {"supply_calendar": [
        {"source": "gsec_calendar", "kind": "gsec", "period_start": "2026-09-28", "period_end": "2026-10-02",
         "tenor_years": 7, "tenor_bucket": "5-10y", "amount_cr": 12000, "week_no": 1, "allocation_no": 2,
         "release_id": 63668, "line": 2, "public_from": "2026-09-25T23:59:59", "public_from_source": "release_date"},
        {"source": "sdl_calendar", "kind": "sdl", "period_start": "2026-10-01", "period_end": "2026-12-31",
         "tenor_bucket": "all", "amount_cr": 360820, "release_id": 63715, "line": 1,
         "public_from": "2026-10-01T23:59:59", "public_from_source": "release_date"},
        {"source": "gsec_announcement", "kind": "gsec", "period_start": "2026-11-20", "amount_cr": 19000,
         "release_id": 63983, "line": 1, "public_from": "2026-11-16T17:45:00", "public_from_source": "feed"}]})
    cal = el.calendar_events(tables["supply_calendar"]).set_index("kind")
    assert len(cal) == 2 and cal.loc["gsec_calendar", "event_date"] == T("2026-09-25")
    assert cal.loc["gsec_calendar", "period_start"] == T("2026-09-28") and cal.loc["gsec_calendar", "notified_cr"] == \
        12000
    assert cal.loc["sdl_calendar", "tenor_bucket"] == "all" and cal.loc["sdl_calendar", "notified_cr"] == 360820
    meetings = load_mpc_calendar()
    policy = pd.DataFrame({"effective_date": ["2026-06-05", "2026-12-04"], "repo_pct": [5.25, 5.0],
                           "sdf_pct": ["", ""], "msf_pct": ["", ""], "source": ["test", "test"]})
    mpc = el.policy_events(meetings, policy).set_index("event_id")
    dec = mpc.loc["mpc_decision:2026-12-04:mpc"]
    assert dec["result_public_from"] == T("2026-12-04 10:00") and dec["announcement_public_from"] == T(
        "2026-03-23 23:59:59")
    assert dec["rate_pct"] == 5.0 and "5.25 -> 5" in dec["result_notes"] and "5.25" not in dec["notes"]
    assert mpc.loc["mpc_minutes:2026-12-18:mpc", "result_public_from"] == T("2026-12-18 23:59:59")
    assert "unconfirmed" in mpc.loc["mpc_decision:2027-02-05:mpc", "flags"]
    macro = el.macro_events().set_index("event_id")
    cpi = macro.loc["macro_release:2025-05-13:cpi_2025-04"]
    assert cpi["result_public_from"] == T("2025-05-13 16:00") and cpi["result_basis"] == "release_table_planned"
    ruled = el.macro_events(families=("cpi",), start="2016-02-01", end="2016-02-29").set_index("key")
    assert ruled.loc["cpi_2016-01", "result_public_from"] == T("2016-02-12 17:30")
    assert ruled.loc["cpi_2016-01", "result_basis"] == "release_rule" and bool(
        ruled.loc["cpi_2016-01", "result_estimated"])
    rows = [dict.fromkeys(INDEX_EVENT_COLUMNS, "") | {
        "event_id": "idx_test_announce", "provider": "Provider", "index_name": "Test Index",
        "event_type": "announcement", "announced_date": "2024-10-08", "announced_tz": "Europe/London",
        "status": "verified", "source_url": "https://example.org/x"},
        dict.fromkeys(INDEX_EVENT_COLUMNS, "") | {
        "event_id": "idx_test_month", "provider": "Provider", "index_name": "Test Index",
        "event_type": "inclusion_start", "announced_date": "2025-09", "announced_tz": "Asia/Kolkata",
        "status": "unverified", "source_url": "https://example.org/y"}]
    raw = pd.DataFrame(rows, columns=list(INDEX_EVENT_COLUMNS)).to_csv(index=False).encode()
    idx = el.index_event_rows(raw).set_index("key")
    assert idx.loc["idx_test_announce", "announcement_public_from"] == T("2024-10-09 04:29:59")  # London BST
    assert idx.loc["idx_test_month", "event_date"] == T("2025-09-30") and "unverified" in idx.loc[
        "idx_test_month", "flags"]


def test_redemptions_buybacks_and_their_times():
    events = el.collect_events(auctions=_base_auctions(), ledger=_base_ledger(), switches=_switches())
    red = events[events["kind"] == "redemption"].set_index("key")
    bill = red.loc["91DTB 2025-07-10"]
    assert bill["booked_cr"] == -9000 and bill["event_date"] == T("2025-07-10")
    assert bill["announcement_public_from"] == T("2025-04-09 23:59:59")  # its auction: the date was known
    assert bill["result_basis"] == "last_change" and bill["dv01_basis"] == "redemption"
    assert "scheduled" in red.loc[G1, "flags"]  # matures after the inputs' last date
    buy = events[events["kind"] == "buyback"].iloc[0]
    assert buy["booked_cr"] == -500 and buy["result_public_from"] == T("2025-06-02 23:59:59")
    assert buy["dv01_basis"] == "last_known_yield" and buy["dv01_cr_per_bp"] < 0  # at 7.31%, its 11 Apr cut-off
    assert buy["dv01_cr_per_bp"] == pytest.approx(-500 * FixedRateBond(7.2, dt.date(2055, 6, 15)).dv01(
        7.31, dt.date(2025, 6, 2)) / 100, rel=1e-12)


# --- Forward supply -----------------------------------------------------------------------------------------------

def _calendar_line(release, public, week, tenor, bucket, amount, line):
    return {"source": "gsec_calendar", "kind": "gsec", "period_start": week,
            "period_end": str((T(week) + pd.Timedelta(days=4)).date()), "tenor_years": tenor, "tenor_bucket": bucket,
            "amount_cr": amount, "week_no": 1, "allocation_no": line, "release_id": release, "line": line,
            "public_from": public, "public_from_source": "release_date"}


def test_forward_supply_respects_public_times_and_revisions_e5(tmp_path):
    auctions = _table([
        _auction("2025-04-04", "7.00% GS 2035", "7.00% GS 2035", 20000.0, isin=G1, issue_type="new",
                 maturity="2035-04-07", coupon=7.0, cutoff=7.0, price=100.0),
        _auction("2025-04-11", "7.00% GS 2035", "7.00% GS 2035", 15000.0, isin=G1, maturity="2035-04-07",
                 coupon=7.0, cutoff=6.95, price=100.36),
        # a new 15-year issue: 15.0007 years at 365.25 a year (four leap days), so 15-30y on residual maturity,
        # 10-15y as the calendar's 15-year line holds it
        _auction("2025-04-04", "7.10% GS 2040", "7.10% GS 2040", 8000.0, isin=G2, issue_type="new",
                 maturity="2040-04-07", coupon=7.1, cutoff=7.1, price=100.0)])
    ledger = build_ledger(auctions, calendar=CAL)
    tables = _store(tmp_path / "s", {"supply_calendar": [
        _calendar_line(80001, "2025-03-28T23:59:59", "2025-03-31", 15, "10-15y", 9000, 3),
        _calendar_line(80001, "2025-03-28T23:59:59", "2025-03-31", 10, "5-10y", 18000, 1),
        _calendar_line(80001, "2025-03-28T23:59:59", "2025-04-07", 10, "5-10y", 18000, 2),
        _calendar_line(80002, "2025-04-04T23:59:59", "2025-04-07", 10, "5-10y", 15000, 1)]})
    events = el.collect_events(auctions=auctions, ledger=ledger, tables=tables)
    early = el.forward_supply(events, ["2025-03-28"], horizon_weeks=2)  # 20:00: the calendar comes at 23:59:59
    assert early["planned_cr"].isna().all()  # E5: not before its public_from (no calendar known: blank)
    assert early["notes"].str.contains("no weekly calendar").all()
    fwd = el.forward_supply(events, ["2025-03-31", "2025-04-07"], horizon_weeks=2,
                            coverage_table=coverage(ledger)).set_index(["as_of", "period_start", "kind",
                                                                        "tenor_bucket"])
    first = fwd.loc[(T("2025-03-31 20:00"), T("2025-03-31"), "gsec", "5-10y")]
    assert first["planned_cr"] == 18000 and first["planned_release"] == "release 80001"
    assert first["realised_cr"] == 20000 and first["calendar_gap_cr"] == 2000
    assert first["announced_cr"] == 0 and first["realised_to_date_cr"] == 0  # announced Thu 3 Apr, after as_of
    fifteen = fwd.loc[(T("2025-03-31 20:00"), T("2025-03-31"), "gsec", "10-15y")]
    assert fifteen["planned_cr"] == 9000 and fifteen["realised_cr"] == 8000 and fifteen["calendar_gap_cr"] == -1000
    supply = el.supply_table(events).set_index(["period", "tenor_bucket"])
    assert supply.loc[("2025-03-31", "15-30y"), "gross_cr"] == 8000  # the supply table keeps residual maturity
    revised = fwd.loc[(T("2025-04-07 20:00"), T("2025-04-07"), "gsec", "5-10y")]
    assert revised["planned_cr"] == 15000 and revised["planned_release"] == "release 80002"
    assert revised["announced_cr"] == 0  # the 11 Apr auction is announced at 18:00 on 10 Apr
    later = el.forward_supply(events, ["2025-04-10T19:00:00"], horizon_weeks=1).iloc[0]
    assert later["announced_cr"] == 15000 and later["realised_cr"] == 15000
    rate = 20000 * FixedRateBond(7.0, dt.date(2035, 4, 7)).dv01(7.0, dt.date(2025, 4, 7)) / 100 / 20000
    assert revised["dv01_per_cr"] == pytest.approx(rate) and revised["planned_dv01"] == pytest.approx(15000 * rate)
    assert first["realised_covered"] == "partly"
    with pytest.raises(ValueError, match="horizon_weeks"):
        el.forward_supply(events, ["2025-04-07"], horizon_weeks=0)


# --- Windows, overlaps, isolation, panels -------------------------------------------------------------------------

def test_windows_over_the_november_2026_holidays_r13(tmp_path):
    auctions, ledger, tables = _nov_2026(tmp_path)
    events = el.collect_events(auctions=auctions, ledger=ledger, tables=tables)
    gsec = events[events["key"] == GS2036]
    gsec = gsec[gsec["kind"] == "gsec_auction"]
    w = el.event_windows(gsec, pre=5, post=6)
    assert list(w.columns) == list(el.WINDOW_COLUMNS)
    days = dict(zip(w["offset"], w["date"].dt.day))
    assert days == {-5: 6, -4: 9, -3: 11, -2: 12, -1: 13, 0: 16, 1: 17, 2: 18, 3: 19, 4: 20, 5: 23, 6: 25}
    assert (w["day0"] == T("2026-11-16")).all() and (w["anchor_public_from"] == T("2026-11-13 23:59:59")).all()
    on_day = el.event_windows(gsec, pre=3, post=3, anchor="event_date")
    assert dict(zip(on_day["offset"], on_day["date"].dt.day)) == {-3: 9, -2: 11, -1: 12, 0: 13, 1: 16, 2: 17, 3: 18}
    early = el.event_windows(gsec, settings=el.EventSettings(decision_time=dt.time(23, 59, 59)), pre=0, post=0)
    assert early["date"].iloc[0] == T("2026-11-13")  # a midnight decision sees the end-of-day result
    # E4: day 0 is a business day at or after the anchor's time
    every = el.event_windows(events, pre=1, post=1)
    zero = every[every["offset"] == 0]
    assert all(CAL.is_business_day(d.date()) for d in zero["date"])
    assert (zero["date"] + pd.Timedelta(hours=20) >= zero["anchor_public_from"]).all()
    # windows reaching 2027 need the projection, flagged
    redemption = events[events["kind"] == "redemption"]
    assert not len(el.event_windows(redemption, pre=1, post=1))
    projected = el.event_windows(redemption, pre=1, post=1, project_calendar=True)
    assert projected["calendar_projected"].any()
    for bad in ({"anchor": "settle"}, {"pre": -1}, {"post": 1.5}):
        with pytest.raises(ValueError):
            el.event_windows(gsec, **bad)


def test_overlaps_siblings_and_isolation():
    def ev(eid, kind, day, public):
        return el._event(event_id=eid, kind=kind, event_date=T(day), key=eid, result_public_from=T(public),
                         result_basis="end_of_day")
    events = el.build_event_ledger(el._events_frame([
        ev("a1", "sdl_auction", "2025-04-08", "2025-04-08 23:59:59"),
        ev("a2", "sdl_auction", "2025-04-08", "2025-04-08 23:59:59"),
        ev("b", "gsec_auction", "2025-04-11", "2025-04-11 23:59:59"),
        ev("v", "vrrr", "2025-04-08", "2025-04-08 10:35:00"),
        ev("c", "gsec_auction", "2025-06-06", "2025-06-06 23:59:59"),
        ev("c_frb", "frb_auction", "2025-06-06", "2025-06-06 23:59:59")]))
    w = el.event_windows(events, pre=3, post=3)
    ov = el.overlaps(w).set_index(["event_id", "other_event_id"])
    # a1's day 0 is Wed 9 Apr 2025 (result 8 Apr at 23:59:59); b's is Tue 15 Apr (result Fri 11 Apr at the end of the
    # day; 14 Apr a holiday): with 10 Apr a holiday too, 2 business days on
    assert ov.loc[("a1", "b"), "other_offset"] == 2 and ov.loc[("b", "a1"), "other_offset"] == -2
    assert bool(ov.loc[("a1", "a2"), "sibling"]) and not bool(ov.loc[("a1", "b"), "sibling"])
    assert not bool(ov.loc[("a1", "v"), "sibling"])  # another event on the auction's day is no sibling
    assert ov.loc[("a1", "v"), "other_offset"] == -1  # the VRRR result came at 10:35: day 0 on 8 Apr
    assert ov.loc[("b", "v"), "other_offset"] == -3 and ("c", "a1") not in ov.index
    iso = el.isolate(w, el.overlaps(w), radius=1, exclude_kinds=("vrrr",)).set_index("event_id")
    assert bool(iso.loc["a1", "isolated"]) and bool(iso.loc["c", "isolated"])  # its sibling and the VRRR set aside
    assert bool(ov.loc[("c", "c_frb"), "sibling"]) and bool(iso.loc["c_frb", "isolated"])  # one GoI auction
    assert not bool(el.isolate(w, el.overlaps(w), radius=1).set_index("event_id").loc["a1", "isolated"])
    near = el.isolate(w, el.overlaps(w), radius=2).set_index("event_id")
    assert not bool(near.loc["a1", "isolated"]) and near.loc["a1", "nearest_event_id"] == "v"
    assert near.loc["a1", "others_within"] == 2 and near.loc["b", "nearest_offset"] == -2
    assert near.loc["b", "nearest_event_id"] == "a1"
    with pytest.raises(ValueError, match="beyond the windows"):
        el.isolate(w, el.overlaps(w), radius=4)
    assert (w.loc[w["event_id"] == "a1"].set_index("offset").loc[0, "others_day0"]) == 1  # a2 shares day 0


def test_attach_panel_exact_nearest_missing_and_settlement_dates():
    events = el.build_event_ledger(el._events_frame([el._event(
        event_id="gsec_auction:2026-11-13:X", kind="gsec_auction", event_date=T("2026-11-13"), isin="X", key="X",
        result_public_from=T("2026-11-13 23:59:59"), result_basis="end_of_day")]))
    w = el.event_windows(events, pre=3, post=3)  # 11, 12, 13 | 16 | 17, 18, 19 Nov
    panel = pd.DataFrame({"date": ["2026-11-11", "2026-11-12", "2026-11-16", "2026-11-18"], "isin": "X",
                          "yield_pct": [7.30, 7.31, 7.33, 7.32]})
    exact = el.attach_panel(w, panel, columns=["yield_pct"]).set_index("offset")
    assert exact.loc[-3, "panel_match"] == "exact" and exact.loc[-1, "panel_match"] == "missing"
    assert pd.isna(exact.loc[-1, "yield_pct"]) and pd.isna(exact.loc[0, "yield_pct_chg"])  # day -1 is missing
    near = el.attach_panel(w, panel, columns=["yield_pct"], tolerance=1).set_index("offset")
    assert near.loc[-1, "panel_match"] == "nearest" and near.loc[-1, "panel_date"] == T("2026-11-12")
    assert near.loc[-1, "panel_gap_days"] == -1
    assert near.loc[1, "panel_date"] == T("2026-11-16")  # 17 Nov: the nearest on day 0's side, 16 and 18 tie early
    assert near.loc[0, "yield_pct_chg"] == pytest.approx(0.02)  # against day -1 (12 Nov's value stands in)
    assert near.loc[-1, "yield_pct"] == 7.31 and near.loc[-2, "panel_gap_days"] == 0
    # a settlement-dated panel (curvefit's residual history) moves to the trade date before
    resid = pd.DataFrame({"date": ["2026-11-17"], "isin": "X", "resid_yield_bp": [1.5]})
    moved = el.attach_panel(w, resid, columns=["resid_yield_bp"], panel_dates="settlement").set_index("offset")
    assert moved.loc[0, "resid_yield_bp"] == 1.5 and moved.loc[0, "panel_date"] == T("2026-11-16")
    curve = pd.DataFrame({"date": ["2026-11-13", "2026-11-16"], "par_10y": [7.10, 7.14]})
    flat = el.attach_panel(w, curve, columns=["par_10y"], key=None).set_index("offset")
    assert flat.loc[0, "par_10y_chg"] == pytest.approx(0.04)
    across = el.attach_panel(w, panel[panel["date"] != "2026-11-12"], columns=["yield_pct"], tolerance=1)
    assert across.set_index("offset").loc[-1, "panel_match"] == "missing"  # 16 Nov is on day 0's other side
    blank = el.attach_panel(w.assign(isin=""), panel, columns=["yield_pct"])
    assert (blank["panel_match"] == "no_key").all()
    with pytest.raises(ValueError, match="two rows"):
        el.attach_panel(w, pd.concat([panel, panel]), columns=["yield_pct"])
    with pytest.raises(ValueError, match="at least one panel column"):
        el.attach_panel(w, panel, columns=[])


# --- Gaps, determinism, refusals, CLI -----------------------------------------------------------------------------

def test_input_gaps_name_what_is_missing(tmp_path):
    auctions = _table([
        _auction("2025-04-04", "7.00% GS 2035", "7.00% GS 2035", 20000.0, isin=G1, issue_type="new",
                 maturity="2035-04-07", coupon=7.0, cutoff=7.0, price=100.0),
        _auction("2025-09-05", "7.00% GS 2035", "7.00% GS 2035", 5000.0, isin=G1, maturity="2035-04-07",
                 coupon=7.0, cutoff=6.9, price=100.6)])
    ledger = build_ledger(auctions, calendar=CAL)
    _, _, nov = _nov_2026(tmp_path)
    tables = _store(tmp_path / "s", {"sdl_result": [{
        "release_id": 63989, "release_date": "2026-11-17", "auction_date": "2026-11-17", "line": 1,
        "security": "7.66% MP SGS 2048", "state_code": "MP", "name_key": "7.66% MP SGS 2048", "notified_cr": 1000,
        "total_accepted_cr": 1000, "cutoff_yield": 7.6795, "status": "accepted"}]},
        gaps=[{"gap": "release_id", "first": 63951, "last": 63955, "count": 5, "in_feed_span": "False"}])
    events = el.collect_events(auctions=auctions, ledger=ledger, tables=tables, meetings=load_mpc_calendar())
    gaps = el.input_gaps(events, ledger=ledger, tables=tables, meetings=load_mpc_calendar(), index_events_given=False)
    assert list(gaps.columns) == list(el.GAP_COLUMNS)
    hole = gaps[(gaps["source"] == "auctions:gsec") & gaps["detail"].str.startswith("no auction")]
    assert len(hole) == 1 and hole.iloc[0]["start"] == T("2025-04-05") and hole.iloc[0]["end"] == T("2025-09-04")
    sources = set(gaps["source"])
    assert {"operations", "rbi_store:release_id", "events:result_only", "mpc", "index_events",
            "times:result"} <= sources
    assert "63951 to 63955" in gaps.loc[gaps["source"] == "rbi_store:release_id", "detail"].iloc[0]


def _write_bytes(events, tmp_path, name):
    path = el.write_events(events, tmp_path / f"{name}.csv")
    return path.read_bytes()


def test_same_inputs_same_bytes_in_any_row_order_e6(tmp_path):
    auctions, ledger = _base_auctions(), _base_ledger()
    tables = _store(tmp_path / "s", {"supply_calendar": [
        _calendar_line(80001, "2025-03-28T23:59:59", "2025-03-31", 10, "5-10y", 18000, 1),
        _calendar_line(80001, "2025-03-28T23:59:59", "2025-04-07", 30, "30y+", 9000, 2)]})
    first = el.collect_events(auctions=auctions, ledger=ledger, tables=tables, switches=_switches())
    again = el.collect_events(auctions=auctions, ledger=ledger, tables=tables, switches=_switches())
    rng = random.Random(20261119)
    order = list(range(len(auctions)))
    rng.shuffle(order)
    shuffled = {k: v.sample(frac=1.0, random_state=7) if isinstance(v, pd.DataFrame) else v for k, v in tables.items()}
    mixed = el.collect_events(auctions=auctions.iloc[order].reset_index(drop=True),
                              ledger=ledger.sample(frac=1.0, random_state=3).reset_index(drop=True),
                              tables=shuffled, switches=_switches())
    one, two, three = (_write_bytes(e, tmp_path, n) for e, n in ((first, "a"), (again, "b"), (mixed, "c")))
    assert one == two == three
    for fn in (lambda e: el.supply_table(e, ledger=ledger), lambda e: el.event_windows(e, pre=2, post=2),
               lambda e: el.forward_supply(e, ["2025-03-31"], horizon_weeks=2), el.auction_metrics):
        a = el.write_table(fn(first), tmp_path / "x.csv", times=("as_of", "anchor_public_from")).read_bytes()
        b = el.write_table(fn(mixed), tmp_path / "y.csv", times=("as_of", "anchor_public_from")).read_bytes()
        assert a == b
    back = el.read_events(tmp_path / "a.csv")
    assert _write_bytes(back, tmp_path, "d") == one
    pd.testing.assert_frame_equal(back, first, check_dtype=False)


def test_refusals_name_the_input():
    auctions, ledger = _base_auctions(), _base_ledger()
    odd = auctions.copy()
    odd.loc[0, "kind"] = "iib"
    with pytest.raises(ValueError, match="kind 'iib'"):
        el.auction_events(odd, ledger)
    with pytest.raises(ValueError, match="lacks the columns"):
        el.auction_events(auctions.drop(columns=["cutoff_yield"]), ledger)
    with pytest.raises(ValueError, match="announcement_rule"):
        el.EventSettings(announcement_rule="soon")
    with pytest.raises(ValueError, match="period"):
        el.EventSettings(period="fortnight")
    with pytest.raises(TypeError, match="decision_time"):
        el.EventSettings(decision_time="20:00")
    with pytest.raises(ValueError, match="dv01_weeks"):
        el.EventSettings(dv01_weeks=0)
    with pytest.raises(ValueError, match="together"):
        el.collect_events(auctions=auctions)
    events = el.collect_events(auctions=auctions, ledger=ledger)
    with pytest.raises(ValueError, match="given twice"):
        el.build_event_ledger(events, events)
    with pytest.raises(ValueError, match="period must be"):
        el.supply_table(events, period="weekly")
    with pytest.raises(ValueError, match="not a date"):
        el.forward_supply(events, ["soon"])
    with pytest.raises(ValueError, match="both start and end"):
        el.macro_events(start="2016-01-01")
    with pytest.raises(ValueError, match="families"):
        el.macro_events(families=("gst",))
    with pytest.raises(ValueError, match="every date"):
        el.stock_on_dates(ledger, ["2025-01-01", None])
    with pytest.raises(ValueError, match="panel_dates"):
        el.attach_panel(el.event_windows(events, pre=1, post=1), pd.DataFrame({"date": [], "isin": [], "y": []}),
                        columns=["y"], panel_dates="value")
    bad = pd.DataFrame({"operation": ["swap"]})
    with pytest.raises(ValueError, match="omo_operations lacks"):
        el.operation_events({"omo_operations": bad})


def _write_inputs(tmp_path):
    auctions, ledger = _base_auctions(), _base_ledger()
    auctions.to_csv(tmp_path / "auctions.csv", index=False, date_format="%Y-%m-%d")
    ledger.to_csv(tmp_path / "ledger.csv", index=False, date_format="%Y-%m-%d")
    coverage(ledger).to_csv(coverage_path(tmp_path / "ledger.csv"), index=False, date_format="%Y-%m-%d")
    _switches().to_csv(tmp_path / "switches.csv", index=False, date_format="%Y-%m-%d")
    _store(tmp_path / "store", {"supply_calendar": [
        _calendar_line(80001, "2025-03-28T23:59:59", "2025-03-31", 10, "5-10y", 18000, 1)]},
        releases=[{"release_id": 80001, "kind": "gsec_calendar", "release_date": "2025-03-28"}])
    pd.DataFrame({"date": ["2025-04-03", "2025-04-04", "2025-04-08"], "isin": G1,
                  "yield_pct": [7.01, 7.0, 6.98]}).to_csv(tmp_path / "panel.csv", index=False)


def _cli(tmp_path, out, *extra):
    return main(["event-ledger", "--auctions", str(tmp_path / "auctions.csv"), "--ledger", str(tmp_path / "ledger.csv"),
                 "--store", str(tmp_path / "store"), "--switches", str(tmp_path / "switches.csv"), "--out-dir",
                 str(out), *extra])


def test_cli_end_to_end(tmp_path, capsys):
    _write_inputs(tmp_path)
    assert _cli(tmp_path, tmp_path / "out1", "--panel", str(tmp_path / "panel.csv")) == 0
    printed = capsys.readouterr().out
    assert "supply check (E1, all inputs): 0 of" in printed and "ledger join (E2): 0 of 7" in printed
    names = {p.name for p in (tmp_path / "out1").iterdir()}
    assert {"events.csv", "supply_weekly.csv", "supply_monthly_check.csv", "ledger_join_check.csv",
            "auction_metrics.csv", "forward_supply.csv", "windows.csv", "overlaps.csv", "isolated.csv", "gaps.csv",
            "event_paths.csv", "notes.txt"} <= names
    events = el.read_events(tmp_path / "out1" / "events.csv")
    assert {"gsec_calendar", "mpc_decision", "redemption", "switch_in", "buyback"} <= set(events["kind"])
    assert _cli(tmp_path, tmp_path / "out2", "--panel", str(tmp_path / "panel.csv")) == 0
    for name in names:
        assert (tmp_path / "out1" / name).read_bytes() == (tmp_path / "out2" / name).read_bytes(), name
    paths = pd.read_csv(tmp_path / "out1" / "event_paths.csv")
    assert (paths["panel_match"] == "exact").sum() >= 1
    # --start writes the later events only, but the identities and the neighbours still read every input
    capsys.readouterr()
    assert _cli(tmp_path, tmp_path / "out7", "--start", "2025-04-11", "--no-mpc", "--forward-dates", "2025-04-11") == 0
    printed = capsys.readouterr().out
    assert "supply check (E1, all inputs): 0 of" in printed and "ledger join (E2): 0 of 7" in printed
    assert "windows: 8 events; skipped 0 without the anchor's time, 3 beyond" in printed
    later = el.read_events(tmp_path / "out7" / "events.csv")
    assert later["event_date"].min() == pd.Timestamp("2025-04-11") and len(later) == 11
    iso = pd.read_csv(tmp_path / "out7" / "isolated.csv").set_index("event_id")
    tbill = "tbill_auction:2025-04-09:91DTB 2025-07-10"  # dated before --start, its day 0 one business day before
    assert iso.loc[f"gsec_auction:2025-04-11:{G1}", "nearest_event_id"] == tbill
    assert iso.loc[f"gsec_auction:2025-04-11:{G1}", "nearest_offset"] == -1
    assert tbill not in set(later["event_id"]) and tbill not in set(iso.index)
    weekly = pd.read_csv(tmp_path / "out7" / "supply_weekly.csv")
    assert weekly["period"].min() == "2025-04-07"  # the week --start cuts counts only its events from 11 April
    bill = weekly[weekly["kind"] == "tbill"].iloc[0]  # priced at its 9 April cut-off, an event before --start
    assert bill["stock_cr"] == 9000 and bill["stock_dv01"] > 0 and bill["stock_no_dv01_cr"] == 0
    fwd = pd.read_csv(tmp_path / "out7" / "forward_supply.csv")
    ten = fwd[(fwd["kind"] == "gsec") & (fwd["tenor_bucket"] == "5-10y")].iloc[0]
    assert ten["realised_cr"] == 15000 and ten["dv01_per_cr"] > 0  # the trailing DV01 of 4 April, before --start
    assert _cli(tmp_path, tmp_path / "out3", "--start", "2099-01-01", "--no-mpc") == 1  # nothing in the span
    assert _cli(tmp_path, tmp_path / "out4", "--snapshot", str(tmp_path / "missing.csv")) == 2
    assert _cli(tmp_path, tmp_path / "out5", "--decision-time", "8pm") == 2
    assert _cli(tmp_path, tmp_path / "out6", "--exclude-kinds", "lunch") == 2
    err = capsys.readouterr().err
    assert "missing.csv not found" in err and "--decision-time" in err and "lunch" in err
    assert main(["event-ledger", "--auctions", str(tmp_path / "auctions.csv"), "--out-dir", str(tmp_path / "o")]) == 2


def test_the_store_and_a_releases_directory_are_joined_not_replaced(tmp_path, capsys):
    auctions, ledger, tables = _nov_2026(tmp_path)
    auctions.to_csv(tmp_path / "auctions.csv", index=False, date_format="%Y-%m-%d")
    ledger.to_csv(tmp_path / "ledger.csv", index=False, date_format="%Y-%m-%d")
    coverage(ledger).to_csv(coverage_path(tmp_path / "ledger.csv"), index=False, date_format="%Y-%m-%d")
    rel = tmp_path / "rel"
    rel.mkdir()
    later = dict(GSEC_RESULT, release_id=63999, release_date="2026-11-20", auction_date="2026-11-20")  # made up
    pd.DataFrame([{c: later.get(c, "") for c in COLUMNS["gsec_result"]}]).to_csv(rel / "gsec_result.csv", index=False)
    tables["tbill_result"].to_csv(rel / "tbill_result.csv", index=False, date_format="%Y-%m-%d")  # the store's too
    pd.DataFrame([{"release_id": 63999, "kind": "gsec_result", "release_date": "2026-11-20"}],
                 columns=list(RELEASE_INDEX_COLUMNS)).to_csv(rel / "releases.csv", index=False)
    assert main(["event-ledger", "--auctions", str(tmp_path / "auctions.csv"), "--ledger", str(tmp_path / "ledger.csv"),
                 "--store", str(tmp_path / "store"), "--releases", str(rel), "--no-mpc", "--out-dir",
                 str(tmp_path / "out")]) == 0
    events = el.read_events(tmp_path / "out" / "events.csv")
    gsec = events[events["kind"] == "gsec_auction"].set_index("event_date")
    assert gsec.loc[T("2026-11-13"), "bids_cr"] == pytest.approx(73016.6)  # the store's result is still joined
    assert "result_only" in gsec.loc[T("2026-11-20"), "result_flags"]  # and the directory's later one is added
    bills = events[events["kind"] == "tbill_auction"]
    assert len(bills) == 3 and not bills["flags"].str.contains("result_only").any()  # release 63956 counted once
    # the library's join: the first source's copy of a release wins, a table without release ids keeps each row once
    first = {"gsec_result": pd.DataFrame({"release_id": [1, 1], "line": [1, 2], "v": ["a", "b"]}),
             "gaps": pd.DataFrame({"start_id": [5], "end_id": [6]})}
    second = {"gsec_result": pd.DataFrame({"release_id": [1, 2], "line": [1, 1], "v": ["changed", "c"]}),
              "gaps": pd.DataFrame({"start_id": [5, 8], "end_id": [6, 9]}), "tbill_result": pd.DataFrame()}
    merged = el.merge_tables(first, second)
    assert merged["gsec_result"]["v"].tolist() == ["a", "b", "c"]
    assert merged["gaps"]["start_id"].tolist() == [5, 8] and len(merged["tbill_result"]) == 0
    assert el.merge_tables() == {} and el.merge_tables(second, first)["gsec_result"]["v"].tolist() == ["changed", "c"]


def test_supply_table_reproduces_supply_summary_by_month():
    events = el.collect_events(auctions=_base_auctions(), ledger=_base_ledger(), switches=_switches())
    mine = el.supply_table(events, period="settlement_month").set_index(["period", "kind", "tenor_bucket"])
    theirs = supply_summary(_base_ledger()).set_index(["period", "kind", "tenor_bucket"])
    assert sorted(mine.index) == sorted(theirs.index)
    assert np.allclose(mine.loc[theirs.index, "gross_cr"], theirs["gross_issuance_cr"])
    assert np.allclose(mine.loc[theirs.index, "net_cr"], theirs["net_cr"])
    by_auction = el.supply_table(events, period="auction_month")
    assert set(by_auction["period"]) >= {"2025-04", "2025-05", "2025-06", "2025-07"}
    unbucketed = el.supply_table(events, by_bucket=False)
    assert set(unbucketed["tenor_bucket"]) == {"all"}


# --- The review fix pass (findings of 19 Nov 2026) ----------------------------------------------------------------


def test_tbill_greenshoe_counts_only_competitive_acceptance_above_the_notified_amount(tmp_path):
    auctions, ledger, tables = _nov_2026(tmp_path)
    comp = {91: 7600, 182: 7600, 364: 6650}  # RBI's result of 11 Nov 2026 (press release 2026-2027/1478)
    bills = tables["tbill_result"].copy()
    bills["comp_accepted_cr"] = [comp[int(t)] for t in bills["tenor_days"]]
    events = el.auction_events(auctions, ledger, tables=dict(tables, tbill_result=bills)).set_index("key")
    for tenor, maturity in ((91, "2027-02-11"), (182, "2027-05-13"), (364, "2027-11-11")):
        assert events.loc[f"{tenor}DTB {maturity}", "greenshoe_cr"] == 0.0  # non-competitive bids sit outside
    bills.loc[bills["tenor_days"] == 91, "comp_accepted_cr"] = 8500
    events = el.auction_events(auctions, ledger, tables=dict(tables, tbill_result=bills)).set_index("key")
    assert events.loc["91DTB 2027-02-11", "greenshoe_cr"] == 500.0
    no_comp = el.auction_events(auctions, ledger, tables=tables).set_index("key")  # the result prints none
    assert pd.isna(no_comp.loc["91DTB 2027-02-11", "greenshoe_cr"])


def test_mpc_decisions_are_public_at_their_era_time_or_the_meetings_own():
    meetings = pd.DataFrame([
        {"decision_date": d, "kind": "scheduled", "announced": a, "confirmed": True, "projected": False,
         "source": "typed in", "decision_time": t}
        for d, a, t in (("2016-10-04", "2016-09-21", ""), ("2019-10-04", "2019-09-01", ""),
                        ("2021-02-05", "2021-01-01", ""), ("2026-12-04", "2026-03-23", ""),
                        ("2018-06-06", "2018-05-01", "14:15"))])
    policy = pd.DataFrame([{"effective_date": "2016-04-05", "repo_pct": 6.50, "source": "typed in"},
                           {"effective_date": "2016-10-04", "repo_pct": 6.25, "source": "typed in"}])
    ev = el.policy_events(meetings, policy).set_index("event_id")
    times = {d: ev.loc[f"mpc_decision:{d}:mpc", "result_public_from"] for d in
             ("2016-10-04", "2019-10-04", "2021-02-05", "2026-12-04", "2018-06-06")}
    assert times == {"2016-10-04": T("2016-10-04 14:30"), "2019-10-04": T("2019-10-04 11:45"),
                     "2021-02-05": T("2021-02-05 14:30"), "2026-12-04": T("2026-12-04 10:00"),
                     "2018-06-06": T("2018-06-06 14:15")}
    assert ev.loc["mpc_decision:2018-06-06:mpc", "result_basis"] == "meeting_time"
    noon = el.events_as_of(ev.reset_index(), "2016-10-04T12:00:00").set_index("event_id")
    first = noon.loc["mpc_decision:2016-10-04:mpc"]
    assert pd.isna(first["rate_pct"]) and first["result_notes"] == "" and "6.25" not in first["notes"]
    later = el.events_as_of(ev.reset_index(), "2016-10-04T14:30:00").set_index("event_id")
    assert later.loc["mpc_decision:2016-10-04:mpc", "rate_pct"] == 6.25
    fixed = el.policy_events(meetings, settings=el.EventSettings(mpc_decision_time=dt.time(9, 0)))
    assert fixed[fixed["kind"] == "mpc_decision"]["result_public_from"].dt.hour.tolist() == [9, 14, 9, 9, 9]
    with pytest.raises(ValueError, match="decision_time 'noon' is not HH:MM"):
        el.policy_events(meetings.assign(decision_time="noon"))


def test_tails_are_zero_or_positive_when_an_operation_ran_by_its_rules(tmp_path):
    laf = []
    for direction, cutoff, wa in (("repo", 5.30, 5.35), ("reverse_repo", 5.49, 5.45)):
        laf.append({"direction": direction, "auction_date": "2026-11-17", "tenor_days": 7, "reversal_date":
                    "2026-11-24", "announcement_id": 64001, "announcement_public_from": "2026-11-16T19:05:00",
                    "notified_cr": 50000, "result_id": 64002, "result_public_from": "2026-11-17T10:35:00",
                    "received_cr": 60000, "accepted_cr": 50000, "cutoff_rate_pct": cutoff, "wa_rate_pct": wa,
                    "liquidity_effect_cr": 50000 if direction == "repo" else -50000, "mmo_check": ""})
    ops = el.operation_events(_store(tmp_path, {"liquidity_operations": laf})).set_index("kind")
    assert ops.loc["vrr", "tail_bp"] == pytest.approx(5.0, abs=1e-9)  # RBI lends: highest rates first
    assert ops.loc["vrrr", "tail_bp"] == pytest.approx(4.0, abs=1e-9)  # RBI borrows: lowest rates first


def test_a_duplicated_auction_line_is_flagged_and_kept_out_of_supply():
    auctions = _base_auctions()
    copy = auctions.iloc[[0]].copy()
    copy["auction_id"] = copy["auction_id"] + " (copy)"
    doubled = pd.concat([auctions, copy], ignore_index=True)
    ledger = build_ledger(doubled, _switches(), calendar=CAL, operations_from="2025-04-01")
    events = el.collect_events(auctions=doubled, ledger=ledger, calendar=CAL)
    lines = events[events["event_id"].str.startswith("gsec_auction:2025-04-04:new GS 2035")]
    assert len(lines) == 2 and lines["flags"].str.contains("duplicate_line").all()
    week = el.supply_table(events, ledger=ledger)
    assert week[(week["period"] == "2025-03-31") & (week["kind"] == "gsec")]["gross_cr"].sum() == 30000
    both = el.supply_table(events, ledger=ledger, keep_duplicates=True)
    assert both[(both["period"] == "2025-03-31") & (both["kind"] == "gsec")]["gross_cr"].sum() == 50000
    assert (el.check_supply(events, ledger)["status"] != "ok").any()  # E1 now shows the ledger's double count
    gaps = el.input_gaps(events, ledger=ledger)
    assert gaps["source"].eq("events:duplicate_line").any()


def test_a_duplicated_auction_line_is_kept_out_of_the_redemption_and_the_stock_too():
    """The issuance ledger built from a table with a repeated line counts the copy in the security's redemption and
    stock as well: the redemption is flagged, and the supply table takes the copy out of both, as it does of the
    gross supply, unless told to keep it."""
    auctions = _base_auctions()
    copy = auctions.iloc[[0]].copy()
    copy["auction_id"] = copy["auction_id"] + " (copy)"
    doubled = pd.concat([auctions, copy], ignore_index=True)
    clean_ledger = build_ledger(auctions, _switches(), calendar=CAL, operations_from="2025-04-01")
    ledger = build_ledger(doubled, _switches(), calendar=CAL, operations_from="2025-04-01")
    clean = el.collect_events(auctions=auctions, ledger=clean_ledger, calendar=CAL)
    events = el.collect_events(auctions=doubled, ledger=ledger, calendar=CAL)
    red = events[events["kind"] == "redemption"].set_index("key").loc[G1]
    assert red["booked_cr"] == -57400.0  # the issuance ledger's amount, copy included
    assert "duplicate_line" in red["flags"] and "1 numbered copy(ies)" in red["notes"] and "20,000.00" in red["notes"]

    def cell(table, period, column, bucket):
        row = table[(table["period"] == period) & (table["kind"] == "gsec") & (table["tenor_bucket"] == bucket)]
        assert len(row) == 1, (period, bucket)
        return row[column].iloc[0]

    mine = el.supply_table(events, ledger=ledger, period="auction_month")
    base = el.supply_table(clean, ledger=clean_ledger, period="auction_month")
    kept = el.supply_table(events, ledger=ledger, period="auction_month", keep_duplicates=True)
    for table, redemption, stock_april, stock_2035 in ((mine, -37400.0, 35000.0, 37400.0),
                                                      (base, -37400.0, 35000.0, 37400.0),
                                                      (kept, -57400.0, 55000.0, 57400.0)):
        assert cell(table, "2035-04", "redemption_cr", "0-1y") == cell(table, "2035-04", "net_cr", "0-1y") == redemption
        assert cell(table, "2025-05", "stock_cr", "5-10y") == stock_april  # 30 Apr 2025: 20,000 + 15,000 (+ copy)
        assert cell(table, "2035-04", "stock_cr", "0-1y") == stock_2035  # 31 Mar 2035, the 2,400 switched in too
    check = el.check_supply(events, ledger)
    late = check[(check["period"] == "2035-04") & (check["status"] != "ok")]
    assert set(late["column"]) == {"redemption_cr", "net_cr"}  # E1 shows the ledger's double count there too
    gaps = el.input_gaps(events, ledger=ledger)
    assert gaps["detail"].str.contains("1 redemption rows: redemptions of securities with a duplicated").any()
    from cr_strat import returns as rt  # a back-test's auction table keeps the copy as one line, named coupon-free
    table = rt.auctions_from_events(events)
    assert sorted(set(table.loc[table["isin"] == G1, "line"])) == [G1, "new GS 2035"]
    res = rt.run_book(None, None, pd.DataFrame([{"date": "2025-04-07", "book": "b", "amount": 1.0}]),
                      market=rt.MarketData(auctions=table), start="2025-04-07", end="2025-04-07",
                      settings=rt.EngineSettings(access=rt.ACCESS_PRESETS["nbfc_non_pd"]))
    assert list(res.nav["nav"]) == [1.0]


def test_a_row_repeated_in_an_input_table_is_refused_by_its_table(tmp_path):
    auctions = _base_auctions()
    with pytest.raises(ValueError, match="the auction table repeats a row .auction_id 2025-04-04 7.00% GS 2035"):
        el.auction_events(pd.concat([auctions, auctions.iloc[[0]]], ignore_index=True), _base_ledger())
    clash = pd.concat([auctions, auctions.iloc[[0]].assign(accepted_cr=1.0)], ignore_index=True)
    with pytest.raises(ValueError, match="gives the auction id '2025-04-04 7.00% GS 2035' to two different rows"):
        el.auction_events(clash, _base_ledger())
    omo = {"operation": "purchase", "auction_date": "2026-03-13", "settlement_date": "2026-03-16", "line": 1,
           "security": "6.45% GS 2029", "name_key": "6.45% GS 2029", "isin": "IN0020190362",
           "maturity_date": "2029-10-07", "accepted_cr": 13006, "offered_cr": 27844, "cutoff_yield": 6.1187,
           "cutoff_price": 101.04, "way": 6.1408, "wap": 100.97, "liquidity_effect_cr": 13006,
           "announcement_id": 62367, "announcement_public_from": "2026-03-11T23:59:59", "result_id": 62379,
           "result_public_from": "2026-03-13T23:59:59"}
    with pytest.raises(ValueError, match="omo_operations repeats a row .auction_date 2026-03-13, operation purchase"):
        el.operation_events(_store(tmp_path, {"omo_operations": [omo, omo]}))


def test_decision_day_is_day_zero_of_a_window():
    assert el.decision_day("2026-11-13T23:59:59", CAL) == (dt.date(2026, 11, 16), False)  # after 20:00: Monday
    assert el.decision_day("2026-11-13T19:00:00", CAL) == (dt.date(2026, 11, 13), False)
    assert el.decision_day("2026-11-13T20:00:00", CAL) == (dt.date(2026, 11, 13), False)  # at the decision time
    assert el.decision_day("2026-11-09T20:30:00", CAL) == (dt.date(2026, 11, 11), False)  # 10 Nov a holiday
    assert el.decision_day("2026-11-14T09:00:00", CAL) == (dt.date(2026, 11, 16), False)  # a Saturday
    assert el.decision_day("2026-12-31T21:00:00", CAL) == (dt.date(2027, 1, 1), True)  # past the list: projected
    events = el._events_frame([dict(dict.fromkeys(el.EVENT_COLUMNS), event_id=f"vrrr:2026-11-13:{k}d", kind="vrrr",
                                    event_date="2026-11-13", key=f"{k}d", result_public_from=t)
                               for k, t in ((1, "2026-11-13T23:59:59"), (2, "2026-11-13T19:00:00"))])
    windows = el.event_windows(events, pre=0, post=0).set_index("event_id")
    for eid, t in (("vrrr:2026-11-13:1d", "2026-11-13T23:59:59"), ("vrrr:2026-11-13:2d", "2026-11-13T19:00:00")):
        assert windows.loc[eid, "day0"].date() == el.decision_day(t, CAL)[0]


def test_events_as_of_shows_nothing_a_result_decides_before_it_is_public():
    events = el.collect_events(auctions=_base_auctions(), ledger=_base_ledger(), switches=_switches())
    eid = "gsec_auction:2025-04-04:new GS 2035"  # a new issue: its coupon is its cut-off yield
    full = events.set_index("event_id").loc[eid]
    assert full["coupon_pct"] == 7.0 and full["key"] == G1 and full["auction_id"] == "2025-04-04 7.00% GS 2035"
    before = el.events_as_of(events, full["announcement_public_from"]).set_index("event_id").loc[eid]
    assert pd.isna(before["coupon_pct"]) and before["key"] == before["security"] == "new GS 2035"
    assert before["isin"] == before["auction_id"] == before["source_ref"] == ""
    sdl = el.events_as_of(events, "2025-04-07T18:30:00").set_index("event_id").loc[
        "sdl_auction:2025-04-08:new KA SGS 2045"]
    assert pd.isna(sdl["coupon_pct"]) and "7.40" not in "".join(str(v) for v in sdl.to_numpy())
    short = events[events["result_flags"].str.contains("shortfall")].iloc[0]
    seen = el.events_as_of(events, short["announcement_public_from"]).set_index("event_id").loc[short["event_id"]]
    assert seen["result_flags"] == "" and seen["result_notes"] == "" and "shortfall" not in seen["flags"]
    after = el.events_as_of(events, short["result_public_from"]).set_index("event_id").loc[short["event_id"]]
    assert "shortfall" in after["result_flags"] and "devolved" in after["result_notes"]


def test_forward_supply_leaves_planned_blank_where_no_weekly_calendar_plans_the_kind(tmp_path):
    auctions = _table([
        _auction("2025-04-11", "7.00% GS 2035", "7.00% GS 2035", 15000.0, isin=G1, maturity="2035-04-07",
                 coupon=7.0, cutoff=6.95, price=100.36),
        _auction("2025-04-09", "91-Day", "91DTB", 9000.0, kind="tbill", tenor=91, issue_type="new",
                 maturity="2025-07-10", cutoff=6.0, price=98.5262)])
    ledger = build_ledger(auctions, calendar=CAL)
    tables = _store(tmp_path, {"supply_calendar": [_calendar_line(80001, "2025-03-28T23:59:59", "2025-04-07", 10,
                                                                  "5-10y", 15000, 1)]})
    events = el.collect_events(auctions=auctions, ledger=ledger, tables=tables)
    fwd = el.forward_supply(events, ["2025-04-07"], horizon_weeks=1).set_index(["kind", "tenor_bucket"])
    assert fwd.loc[("gsec", "5-10y"), "planned_cr"] == 15000 and fwd.loc[("gsec", "5-10y"), "calendar_gap_cr"] == 0
    bill = fwd.loc[("tbill", "0-1y")]
    assert pd.isna(bill["planned_cr"]) and pd.isna(bill["calendar_gap_cr"]) and bill["realised_cr"] == 9000
    assert "no weekly calendar of tbill auctions" in bill["notes"]


def _bill_lines(rows) -> pd.DataFrame:
    cols = ("source", "period_start", "period_end", "tenor_days", "amount_cr", "release_id", "line", "public_from")
    return pd.DataFrame([dict(zip(cols, r)) for r in rows], columns=list(cols))


def test_tbill_calendar_lines_of_the_users_own_plan_the_tbill_weeks(tmp_path):
    auctions = _table([_auction("2025-04-09", "91-Day", "91DTB", 9000.0, kind="tbill", tenor=91, issue_type="new",
                                maturity="2025-07-10", cutoff=6.0, price=98.5262)])
    ledger = build_ledger(auctions, calendar=CAL)
    lines = el.read_calendar_lines(_bill_lines([
        ("tbill_calendar", "2025-04-07", "2025-04-11", "91", "9000", "1", "1", "2025-03-27"),
        ("tbill_calendar", "2025-04-07", "2025-04-11", "182", "5000", "1", "2", "2025-03-27"),
        # a revised calendar for the week, public on 4 April at 17:00: it replaces the first
        ("tbill_calendar", "2025-04-07", "2025-04-11", "91", "8000", "2", "1", "2025-04-04T17:00:00")]))
    assert list(lines["tenor_bucket"]) == ["0-1y"] * 3 and set(lines["kind"]) == {"tbill"}
    assert lines["public_from"].iloc[0] == pd.Timestamp("2025-03-27 23:59:59")  # a date alone: the end of the day
    events = el.collect_events(auctions=auctions, ledger=ledger, calendar_lines=lines)
    cal = events[events["kind"] == "tbill_calendar"].set_index("key")
    assert set(cal.index) == {"2025-04-07/91d/tbill/1", "2025-04-07/182d/tbill/2"}
    assert set(cal["announcement_basis"]) == {"user_table"}
    fwd = el.forward_supply(events, ["2025-04-03", "2025-04-07"], horizon_weeks=2)
    bill = fwd[(fwd["kind"] == "tbill") & (fwd["period_start"] == pd.Timestamp("2025-04-07"))].set_index("as_of")
    first, revised = bill.loc[pd.Timestamp("2025-04-03 20:00")], bill.loc[pd.Timestamp("2025-04-07 20:00")]
    assert first["planned_cr"] == 14000 and first["calendar_gap_cr"] == 9000 - 14000
    assert revised["planned_cr"] == 8000 and revised["calendar_gap_cr"] == 1000
    assert "no weekly calendar" not in revised["notes"]
    # the command line reads the same table
    _write_inputs(tmp_path)
    _bill_lines([("tbill_calendar", "2025-04-07", "2025-04-11", "91", "9500", "7", "1", "2025-04-01T17:00:00")]
                ).to_csv(tmp_path / "bills.csv", index=False)
    assert _cli(tmp_path, tmp_path / "out", "--no-mpc", "--calendar-lines", str(tmp_path / "bills.csv"),
                "--forward-dates", "2025-04-07") == 0
    out = pd.read_csv(tmp_path / "out" / "forward_supply.csv")
    row = out[(out["kind"] == "tbill") & (out["period_start"] == "2025-04-07")].iloc[0]
    assert row["planned_cr"] == 9500 and row["realised_cr"] == 9000 and row["calendar_gap_cr"] == -500
    # refusals name the line
    for bad, words in ((("tbill_calendar", "2025-04-07", "2025-04-11", "91", "9000", "1", "1", "soon"), "public_from"),
                       (("bill_calendar", "2025-04-07", "2025-04-11", "91", "9000", "1", "1", "2025-04-01"), "source"),
                       (("tbill_calendar", "2025-04-07", "2025-04-11", "", "9000", "1", "1", "2025-04-01"), "tenor"),
                       (("tbill_calendar", "2025-04-07", "2025-04-11", "91", "9000", "x", "1", "2025-04-01"),
                        "release_id")):
        with pytest.raises(ValueError, match=f"line 2.*{words}"):
            el.read_calendar_lines(_bill_lines([bad]))


def test_the_command_line_keeps_a_meetings_files_own_decision_time(tmp_path, capsys):
    from cr_strat.policy_path import DEFAULT_MPC_CSV, coverage_path as mpc_years
    _write_inputs(tmp_path)
    meetings = pd.read_csv(DEFAULT_MPC_CSV, dtype=str, keep_default_na=False)
    meetings["decision_time"] = ["10:15" if d == "2026-12-04" else "" for d in meetings["decision_date"]]
    meetings.to_csv(tmp_path / "mpc.csv", index=False)
    (tmp_path / "mpc_years.csv").write_bytes(mpc_years(DEFAULT_MPC_CSV).read_bytes())
    assert _cli(tmp_path, tmp_path / "out", "--mpc", str(tmp_path / "mpc.csv")) == 0
    events = el.read_events(tmp_path / "out" / "events.csv").set_index("event_id")
    dec = events.loc["mpc_decision:2026-12-04:mpc"]
    assert dec["result_public_from"] == pd.Timestamp("2026-12-04 10:15") and dec["result_basis"] == "meeting_time"
    other = events.loc["mpc_decision:2026-10-07:mpc"]  # no time in the file: its era's (10:00 since 2024)
    assert other["result_public_from"] == pd.Timestamp("2026-10-07 10:00") and other["result_basis"] == "decision_time"
    meetings.loc[meetings["decision_date"] == "2026-12-04", "decision_time"] = "noon"
    meetings.to_csv(tmp_path / "mpc.csv", index=False)
    capsys.readouterr()
    assert _cli(tmp_path, tmp_path / "bad", "--mpc", str(tmp_path / "mpc.csv")) == 2
    assert "decision_time 'noon' is not HH:MM" in capsys.readouterr().err
