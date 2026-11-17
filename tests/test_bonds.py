import dataclasses
import math
from collections import namedtuple
from datetime import date

import numpy as np
import pandas as pd
import pytest

from cr_strat.bonds import (
    ANALYTICS_COLUMNS,
    YIELD_TOLERANCE_PCT,
    DiscountInstrument,
    FixedRateBond,
    ZeroCouponBond,
    price_bonds,
)
from cr_strat.calendars import CalendarCoverageError, mumbai_settlement_calendar
from cr_strat.conventions import CD, CMB, CP, GSEC, SDL, STRIPS, TBILL, days_30e_360

D = date
Pair = namedtuple("Pair", "security coupon maturity trade settle price printed_yield")


@pytest.fixture(scope="module")
def cal():
    return mumbai_settlement_calendar()


# RBI cut-off price and yield of G-sec reissue auctions, as compiled by FIMMDA. Maturities are
# the anniversary of each security's first settlement (inferred by the research pass; the
# 4-decimal yields pin them to the day for bonds away from par).
AUCTIONS = [
    # Source: https://www.fimmda.org/UploadPopupPageFiles/Gsec_Auction_data_2025_2026.xlsx
    Pair("6.90% GS 2065", 6.90, "2065-04-15", "2025-05-09", "2025-05-13", 99.18, "6.9605"),  # 12 May fits too
    Pair("6.33% GS 2035", 6.33, "2035-05-05", "2025-05-30", "2025-06-02", 100.97, "6.1969"),
    Pair("5.91% GS 2028", 5.91, "2028-06-30", "2025-07-25", "2025-07-28", 100.27, "5.8061"),  # coupon on the 30th
    Pair("6.01% GS 2030", 6.01, "2030-07-21", "2025-08-14", "2025-08-18", 98.96, "6.2572"),  # skips 15 Aug
    Pair("6.90% GS 2065", 6.90, "2065-04-15", "2025-08-29", "2025-09-01", 93.87, "7.3786"),
    Pair("6.28% GS 2032", 6.28, "2032-07-14", "2025-09-04", "2025-09-09", 98.52, "6.5497"),  # not 5 Sep, below
    Pair("7.24% GS 2055", 7.24, "2055-08-18", "2025-09-12", "2025-09-15", 100.04, "7.2360"),
    Pair("5.91% GS 2028", 5.91, "2028-06-30", "2025-09-19", "2025-09-22", 100.04, "5.8899"),
    Pair("6.90% GS 2065", 6.90, "2065-04-15", "2025-10-10", "2025-10-13", 96.53, "7.1650"),  # 2 days before coupon
    Pair("5.91% GS 2028", 5.91, "2028-06-30", "2025-10-31", "2025-11-03", 100.16, "5.8404"),
    Pair("5.91% GS 2028", 5.91, "2028-06-30", "2025-11-28", "2025-12-01", 100.30, "5.7808"),
    Pair("6.68% GS 2040", 6.68, "2040-07-07", "2025-12-12", "2025-12-15", 96.88, "7.0250"),
    Pair("5.91% GS 2028", 5.91, "2028-06-30", "2025-12-26", "2025-12-29", 100.18, "5.8316"),  # cum the 30 Dec coupon
    Pair("6.28% GS 2032", 6.28, "2032-07-14", "2025-12-26", "2025-12-29", 98.40, "6.5842"),
    Pair("6.01% GS 2030", 6.01, "2030-07-21", "2026-01-16", "2026-01-19", 98.21, "6.4743"),
    Pair("7.24% GS 2055", 7.24, "2055-08-18", "2026-01-23", "2026-01-27", 97.78, "7.4259"),  # skips Republic Day
    Pair("6.48% GS 2035", 6.48, "2035-10-06", "2026-01-30", "2026-02-02", 98.28, "6.7229"),
    Pair("6.03% GS 2029", 6.03, "2029-01-27", "2026-02-20", "2026-02-23", 100.16, "5.9676"),  # period across Feb
    Pair("6.48% GS 2035", 6.48, "2035-10-06", "2026-02-27", "2026-03-02", 98.36, "6.7134"),
    Pair("6.90% GS 2065", 6.90, "2065-04-15", "2026-03-06", "2026-03-09", 92.51, "7.4940"),
    # Source: https://www.fimmda.org/UploadPopupPageFiles/Gsec_Auction_data_2026_2027.xlsx
    Pair("6.68% GS 2040", 6.68, "2040-07-07", "2026-04-02", "2026-04-06", 92.63, "7.5303"),  # skips Good Friday
    Pair("6.36% GS 2031", 6.36, "2031-02-16", "2026-04-17", "2026-04-20", 98.98, "6.6075"),
    Pair("6.68% GS 2033", 6.68, "2033-01-27", "2026-04-24", "2026-04-27", 98.55, "6.9501"),
    Pair("6.68% GS 2040", 6.68, "2040-07-07", "2026-04-30", "2026-05-04", 93.59, "7.4168"),  # skips 1 May
    Pair("6.36% GS 2031", 6.36, "2031-02-16", "2026-05-15", "2026-05-18", 97.92, "6.8779"),
    # New issue at par on a coupon date: a smoke test (any day count returns the coupon), not a vector.
    Pair("6.94% GS 2036", 6.94, "2036-05-11", "2026-05-08", "2026-05-11", 100.00, "6.94"),
    # Source: RBI auction result of 13 Nov 2026 on www.rbi.org.in (not fetched; pair as given in the brief)
    Pair("6.94% GS 2036", 6.94, "2036-05-11", "2026-11-13", "2026-11-16", 97.43, "7.3203"),
]
IDS = [f"{p.security} {p.trade}" for p in AUCTIONS]


def _decimals(text: str) -> int:
    return len(text.split(".")[1])


@pytest.mark.parametrize("pair", AUCTIONS, ids=IDS)
def test_auction_yield_from_cut_off_price(pair):
    bond = FixedRateBond(pair.coupon, pair.maturity)
    got = bond.yield_from_price(pair.price, pair.settle)
    assert f"{got:.{_decimals(pair.printed_yield)}f}" == pair.printed_yield
    # And back: the price at the printed yield rounds to the printed 2-decimal cut-off price.
    assert f"{bond.clean_price(float(pair.printed_yield), pair.settle):.2f}" == f"{pair.price:.2f}"


@pytest.mark.parametrize("pair", AUCTIONS, ids=IDS)
def test_auction_settles_t_plus_1(cal, pair):
    settle = cal.settlement_date(pair.trade, GSEC.settlement_lag)
    if pair.trade == "2025-09-04":
        # The calendar gives 5 Sep 2025 (Id-E-Milad moved to 8 Sep in Mumbai), but that date
        # does not reproduce the published yield: see test_published_numbers_reject.
        assert settle == D(2025, 9, 5)
    else:
        assert settle == D.fromisoformat(pair.settle)


def test_no_pair_separates_30e_360_from_us_or_the_fraction_rules():
    variants = [dataclasses.replace(GSEC, day_count="30/360 US"), dataclasses.replace(GSEC, fraction_rule="days_to_next")]
    for pair in AUCTIONS:
        for conv in variants:
            got = FixedRateBond(pair.coupon, pair.maturity, conv).yield_from_price(pair.price, pair.settle)
            assert f"{got:.{_decimals(pair.printed_yield)}f}" == pair.printed_yield


@pytest.mark.parametrize("label, coupon, maturity, settle, price, printed", [
    ("settled on Independence Day", 6.01, "2030-07-21", "2025-08-15", 98.96, "6.2572"),
    ("settled on Good Friday", 6.68, "2040-07-07", "2026-04-03", 92.63, "7.5303"),
    ("settled on Maharashtra Din", 6.68, "2040-07-07", "2026-05-01", 93.59, "7.4168"),
    ("plain T+1 on 5 Sep 2025", 6.28, "2032-07-14", "2025-09-05", 98.52, "6.5497"),
    ("maturity 26 Jan 2033", 6.68, "2033-01-26", "2026-04-27", 98.55, "6.9501"),
    ("maturity 15 Feb 2031", 6.36, "2031-02-15", "2026-05-18", 97.92, "6.8779"),
    ("maturity 17 Feb 2031", 6.36, "2031-02-17", "2026-05-18", 97.92, "6.8779"),
])
def test_published_numbers_reject(label, coupon, maturity, settle, price, printed):
    got = FixedRateBond(coupon, maturity).yield_from_price(price, settle)
    assert f"{got:.4f}" != printed


def test_published_numbers_reject_actual_day_counts():
    actact = dataclasses.replace(GSEC, day_count="ACT/ACT ICMA")
    act365 = dataclasses.replace(GSEC, day_count="ACT/365F")
    # Spec B candidates C and F, worked independently: 6.877493 and 7.324616.
    assert FixedRateBond(6.36, "2031-02-16", actact).yield_from_price(97.92, "2026-05-18") == pytest.approx(6.877493, abs=1e-6)
    assert FixedRateBond(6.94, "2036-05-11", act365).yield_from_price(97.43, "2026-11-16") == pytest.approx(7.324616, abs=1e-6)
    for coupon, maturity, settle, price, printed in [(6.36, "2031-02-16", "2026-05-18", 97.92, "6.8779"),
                                                      (5.91, "2028-06-30", "2025-07-28", 100.27, "5.8061"),
                                                      (6.33, "2035-05-05", "2025-06-02", 100.97, "6.1969")]:
        assert f"{FixedRateBond(coupon, maturity, actact).yield_from_price(price, settle):.4f}" != printed


def test_4_sep_2025_auction_settled_after_the_moved_holiday():
    bond = FixedRateBond(6.28, "2032-07-14")
    for settle in ("2025-09-08", "2025-09-09"):  # both fit; 5 Sep does not
        assert f"{bond.yield_from_price(98.52, settle):.4f}" == "6.5497"


# Source: https://www.fimmda.org/uploadpopuppagefiles/SDL_Auction_Data_11Feb2025New.xlsx, sheets
# "February" (GJ: auction 06-02-2024, maturity 07-02-2026) and "March" (KL: 2024-03-19, 2064-03-20),
# both NEW issues with cut-off = coupon. The file's "February 2025" and "March 2025" sheets repeat these
# rows with only the auction date moved to 2025 (the ISINs' "2023" is FY2023-24); the 2024 dates are used.
# SMOKE TESTS ONLY: at par on the issue date, a price of 100 returns the coupon on any day count, so
# these say nothing about SDL conventions. No priced SDL reissue was available. Issue = auction + 1 day,
# a Wednesday; there is no 2024 holiday list to check T+1 against.
@pytest.mark.parametrize("isin, coupon, issue, maturity, printed", [
    ("IN2020230263", 7.42, "2024-03-20", "2064-03-20", "7.42"),  # 07.42 KL SGS 2064, auction 19 Mar 2024
    ("IN1520230252", 7.25, "2024-02-07", "2026-02-07", "7.25"),  # 07.25 GJ SGS 2026, auction 6 Feb 2024
])
def test_sdl_new_issues_smoke(isin, coupon, issue, maturity, printed):
    bond = FixedRateBond(coupon, maturity, SDL, issue_date=issue, name=isin)
    assert f"{bond.yield_from_price(100.0, issue):.2f}" == printed
    assert bond.accrued_interest(issue) == 0
    assert len(bond.coupon_dates()) == 2 * (D.fromisoformat(maturity).year - D.fromisoformat(issue).year)


def test_accrued_interest():
    bond = FixedRateBond(6.94, "2036-05-11")
    assert bond.accrued_interest("2026-11-16") == pytest.approx(6.94 * 5 / 360)  # A = 5 from 11 Nov 2026
    assert bond.accrued_interest("2027-05-10") == pytest.approx(6.94 * 179 / 360)
    # Coupon on the 30th, across February: 30 Aug 2025 to 27 Feb 2026 is 177 days under 30E/360.
    feb = FixedRateBond(6.0, "2030-08-30")
    assert feb.previous_coupon_date("2026-02-27") == D(2025, 8, 30)
    assert feb.next_coupon_date("2026-02-27") == D(2026, 2, 28)
    assert feb.accrued_interest("2026-02-27") == pytest.approx(6.0 * 177 / 360)


def test_settlement_on_a_coupon_date():
    bond = FixedRateBond(6.94, "2036-05-11")
    s = "2026-11-11"
    assert bond.accrued_interest(s) == 0
    assert bond.previous_coupon_date(s) == D(2026, 11, 11) and bond.next_coupon_date(s) == D(2027, 5, 11)
    flows = bond.cash_flows(s)
    assert flows[0].date == D(2027, 5, 11) and flows[0].periods == pytest.approx(1.0)
    assert sum(f.kind == "coupon" for f in flows) == 19
    assert bond.clean_price(6.94, s) == pytest.approx(100.0, abs=1e-9) == bond.dirty_price(6.94, s)


def test_cum_coupon_the_day_before_a_coupon():
    bond = FixedRateBond(5.91, "2028-06-30")
    flows = bond.cash_flows("2025-12-29")
    assert flows[0].date == D(2025, 12, 30) and flows[0].amount == pytest.approx(2.955)
    assert bond.accrued_interest("2025-12-29") == pytest.approx(5.91 * 179 / 360)
    assert not bond.in_shut_period("2025-12-29")


def test_last_period_simple_interest():
    bond = FixedRateBond(5.91, "2028-06-30")
    s, y = "2028-06-29", 6.0  # the day before maturity: A = 179, w = 1/180
    w = (180 - days_30e_360(D(2027, 12, 30), D(2028, 6, 29))) / 180
    assert w == pytest.approx(1 / 180)
    dirty = (100 + 2.955) / (1 + w * y / 200)
    assert bond.dirty_price(y, s) == pytest.approx(dirty)
    assert bond.clean_price(y, s) == pytest.approx(dirty - 5.91 * 179 / 360)
    assert bond.yield_from_price(bond.clean_price(y, s), s) == pytest.approx(y, abs=1e-8)
    assert bond.in_shut_period(s) and not bond.in_shut_period("2028-06-28")  # unverified one-day shut period
    compound = FixedRateBond(5.91, "2028-06-30", dataclasses.replace(GSEC, final_period="compound"))
    assert compound.dirty_price(y, s) == pytest.approx(102.955 / 1.03 ** w)
    mid = "2028-03-30"
    assert bond.dirty_price(y, mid) != pytest.approx(compound.dirty_price(y, mid), abs=1e-6)
    # On the last coupon date before maturity the two rules agree (w = 1).
    assert bond.dirty_price(y, "2027-12-30") == pytest.approx(compound.dirty_price(y, "2027-12-30"))


# FIMMDA's 30E/360 leaves February alone, so a half-year from 28 or 29 Feb to the 29th-31st of
# Aug is 181-182 days while E is 180, and it counts 0 days from the 30th to the 31st. Excel's E - A
# then reaches 0 or below before the payment. The engine runs the last period on the days to
# redemption (Excel YIELD's DSR) and never lets the days to a payment fall below one.
@pytest.mark.parametrize("maturity, settle, days_left", [
    ("2035-08-31", "2035-08-28", 2),  # 28 Feb-31 Aug 2035 is 182 days: E - A = 0, DSR = 2
    ("2035-08-31", "2035-08-29", 1),  # E - A = -1 (was priced above the 103.5 still to come)
    ("2035-08-31", "2035-08-30", 1),  # E - A = -2; 30E/360 counts 0 to the 31st: floored at a day
    ("2035-08-30", "2035-08-29", 1),  # 182 days
    ("2035-08-29", "2035-08-28", 1),  # 28 Feb to 29 Aug in a non-leap year: 181 days
    ("2036-08-31", "2036-08-29", 1),  # leap year: 29 Feb to 31 Aug, 181 days
    ("2030-10-31", "2030-10-30", 1),  # a regular 180-day period; the 30th to the 31st counts 0
])
def test_last_period_never_runs_out_of_days(maturity, settle, days_left):
    bond = FixedRateBond(7.0, maturity)
    (flow,) = bond.cash_flows(settle)[-1:]
    assert flow.kind == "redemption" and flow.periods == pytest.approx(days_left / 180)
    dirty = bond.dirty_price(7.0, settle)
    assert dirty == pytest.approx(103.5 / (1 + days_left / 180 * 0.035))
    assert bond.dirty_price(6.0, settle) > dirty > bond.dirty_price(8.0, settle)
    assert bond.dv01(7.0, settle) > 0 and bond.modified_duration(7.0, settle) > 0
    for clean in (95.0, 100.0, 101.0):
        assert bond.clean_price(bond.yield_from_price(clean, settle), settle) == pytest.approx(clean, abs=1e-9)
    # The same dates under the other fraction rule and under 30/360 US.
    for conv in (dataclasses.replace(GSEC, fraction_rule="days_to_next"), dataclasses.replace(GSEC, day_count="30/360 US")):
        other = FixedRateBond(7.0, maturity, conv)
        assert other.cash_flows(settle)[-1].periods > 0 and other.dv01(7.0, settle) > 0


def test_accrued_interest_passes_the_coupon_in_a_182_day_period():
    # Not capped: whether CCIL or NDS-OM caps the days at 180 is an open question (docs/conventions.md).
    bond = FixedRateBond(7.0, "2035-08-31")
    assert bond.accrued_interest("2035-08-29") == pytest.approx(3.5 * 181 / 180)
    assert bond.accrued_interest("2035-08-30") == pytest.approx(3.5 * 182 / 180)
    # So a clean price of 100 on 29 Aug pays 103.5194 for 103.5 two days later, a negative yield:
    # Excel's one-period YIELD with DSR = 1, y = 2 * (180 / 1) * (103.5 / dirty - 1).
    y = bond.yield_from_price(100.0, "2035-08-29")
    assert y == pytest.approx(360 * (103.5 / (100 + 3.5 * 181 / 180) - 1) * 100, abs=1e-9)
    assert y == pytest.approx(-6.762015, abs=1e-6)


def test_coupon_due_the_next_day_is_discounted_for_a_day():
    # 31 Aug 2045 bond on 30 Aug 2035: E - A = 180 - 182 = -2 and 30E/360 counts 0 days to the coupon.
    for rule in ("excel", "days_to_next"):
        bond = FixedRateBond(7.0, "2045-08-31", dataclasses.replace(GSEC, fraction_rule=rule))
        flows = bond.cash_flows("2035-08-30")
        assert flows[0].date == D(2035, 8, 31) and flows[0].periods == pytest.approx(1 / 180)
        assert flows[1].periods == pytest.approx(1 + 1 / 180)
        assert bond.yield_from_price(100.0, "2035-08-30") == pytest.approx(6.991749, abs=1e-6)


@pytest.mark.parametrize("maturity", ["2030-08-28", "2030-08-29", "2030-08-30", "2030-08-31", "2032-08-31",
                                      "2031-03-31", "2031-02-28", "2032-02-29"])
def test_flow_times_stay_positive_and_never_grow(maturity):
    # Every settlement day for 400 days before maturity, for a coupon bond and a STRIP.
    for bond in (FixedRateBond(7.0, maturity), ZeroCouponBond(maturity)):
        previous = None
        for s in pd.date_range(end=pd.Timestamp(maturity) - pd.Timedelta(days=1), periods=400):
            times = [f.periods for f in bond.cash_flows(s)]
            assert min(times) > 0
            if previous is not None and len(previous) == len(times):
                assert all(t <= p + 1e-12 for t, p in zip(times, previous))
            previous = times
            assert bond.dv01(7.0, s) > 0


def test_flows_at_or_before_settlement_are_refused():
    # Schedules never produce one; the guard is for Bond subclasses still to come.
    from cr_strat.bonds import _Flows
    for times in ((0.0, 1.0), (-1 / 180,), (math.nan,)):
        with pytest.raises(ValueError, match="every flow must be after it"):
            _Flows(times, (103.5,) * len(times), 2, False, 0.0)


def test_price_yield_round_trip():
    assert YIELD_TOLERANCE_PCT == 1e-10  # in percent
    bond = FixedRateBond(7.18, "2037-07-24")
    for settle in pd.date_range("2026-01-02", "2027-12-31", freq="17D"):
        for y in (-0.5, 0.25, 3.0, 6.5, 7.18, 9.75, 15.0):
            clean = bond.clean_price(y, settle)
            assert abs(bond.yield_from_price(clean, settle) - y) < YIELD_TOLERANCE_PCT
    short = FixedRateBond(8.0, "2027-03-15")
    for settle in ("2026-09-16", "2026-11-17", "2027-03-12"):
        for y in (0.5, 6.0, 30.0):
            assert abs(short.yield_from_price(short.clean_price(y, settle), settle) - y) < YIELD_TOLERANCE_PCT
    for price in (1.0, 150.0, 400.0):  # far from par: the bracket widens until it holds the yield
        y = bond.yield_from_price(price, "2026-11-16")
        assert bond.clean_price(y, "2026-11-16") == pytest.approx(price, abs=1e-9)


def _bump(fn, y, h=0.005):
    return fn(y - h), fn(y), fn(y + h)


@pytest.mark.parametrize("bond, settle", [
    (FixedRateBond(6.94, "2036-05-11"), "2026-11-16"),
    (FixedRateBond(6.90, "2065-04-15"), "2026-03-09"),
    (FixedRateBond(5.91, "2028-06-30"), "2028-02-15"),  # last period: simple interest
    (ZeroCouponBond("2036-05-11"), "2026-11-16"),
    (FixedRateBond(6.94, "2036-05-11", dataclasses.replace(GSEC, compounding="annual", time_basis="act/365")),
     "2026-11-16"),
])
def test_risk_measures_match_finite_differences(bond, settle):
    y, h = 7.1, 0.001  # a tenth of a basis point either side, in percent
    down, mid, up = _bump(lambda r: bond.dirty_price(r, settle), y, h)
    assert bond.dv01(y, settle) == pytest.approx((down - up) / (2 * h) * 0.01, rel=1e-7)
    assert bond.modified_duration(y, settle) == pytest.approx((down - up) / (2 * h / 100) / mid, rel=1e-7)
    assert bond.convexity(y, settle) == pytest.approx((up - 2 * mid + down) / (h / 100) ** 2 / mid, rel=1e-5)
    a = bond.analytics(settle, yield_pct=y)
    assert a.dv01 == pytest.approx(a.modified_duration * a.dirty_price * 1e-4)
    assert a.clean_price == pytest.approx(a.dirty_price - a.accrued_interest)


def test_macaulay_and_modified_duration():
    bond = FixedRateBond(6.94, "2036-05-11")
    a = bond.analytics("2026-11-16", clean_price=97.43)
    assert a.modified_duration == pytest.approx(a.macaulay_duration / (1 + a.yield_pct / 200))
    zero = ZeroCouponBond("2036-05-11")
    assert zero.macaulay_duration(7.0, "2026-11-16") == pytest.approx((18 + 175 / 180) / 2)
    last = FixedRateBond(5.91, "2028-06-30")  # simple interest in the last period
    w = (180 - 45) / 180  # 30 Dec 2027 to 15 Feb 2028 is 45 days
    assert last.macaulay_duration(6.0, "2028-02-15") == pytest.approx(w / 2)
    assert last.modified_duration(6.0, "2028-02-15") == pytest.approx((w / 2) / (1 + w * 0.03))


def test_coupon_schedule():
    june = FixedRateBond(5.91, "2028-06-30", issue_date="2025-06-30")
    assert june.coupon_dates()[:3] == [D(2025, 12, 30), D(2026, 6, 30), D(2026, 12, 30)]
    assert len(june.coupon_dates()) == 6 and june.coupon_dates()[-1] == D(2028, 6, 30)
    august = FixedRateBond(6.0, "2030-08-31")
    assert august.coupon_dates(after="2027-08-31")[:2] == [D(2028, 2, 29), D(2028, 8, 31)]
    march = FixedRateBond(6.0, "2031-03-31")
    assert march.coupon_dates(after="2030-01-01") == [D(2030, 3, 31), D(2030, 9, 30), D(2031, 3, 31)]
    quarterly = FixedRateBond(8.0, "2027-01-15", dataclasses.replace(GSEC, frequency=4, compounding="annual",
                                                                     time_basis="act/365"))
    assert quarterly.coupon_dates(after="2026-06-01") == [D(2026, 7, 15), D(2026, 10, 15), D(2027, 1, 15)]
    with pytest.raises(ValueError, match="after="):
        march.coupon_dates()


def test_new_issue_on_its_issue_date():
    bond = FixedRateBond(6.94, "2036-05-11", issue_date="2026-05-11")
    assert bond.previous_coupon_date("2026-05-11") == D(2026, 5, 11)
    assert len(bond.cash_flows("2026-05-11")) == 21  # 20 coupons and the redemption
    assert bond.clean_price(6.94, "2026-05-11") == pytest.approx(100.0, abs=1e-9)


def test_payment_dates_follow_the_holiday_rules(cal):
    # Coupon on a holiday: next working day. Guru Nanak Jayanti 5 Nov 2025; 16 Aug 2026 is a Sunday.
    flows = FixedRateBond(6.33, "2035-05-05", calendar=cal).cash_flows("2025-06-02", until="2026-12-31")
    assert [(f.date, f.payment_date) for f in flows] == [(D(2025, 11, 5), D(2025, 11, 6)), (D(2026, 5, 5), D(2026, 5, 5)),
                                                         (D(2026, 11, 5), D(2026, 11, 5))]
    flow = FixedRateBond(6.36, "2031-02-16", calendar=cal).cash_flows("2026-05-18", until="2026-12-31")[0]
    assert (flow.date, flow.payment_date) == (D(2026, 8, 16), D(2026, 8, 17))
    # Redemption (and the last coupon) on a holiday: the previous working day.
    flows = FixedRateBond(7.0, "2026-11-24", calendar=cal).cash_flows("2026-06-01")
    assert [(f.kind, f.payment_date) for f in flows] == [("coupon", D(2026, 11, 23)), ("redemption", D(2026, 11, 23))]
    bill = DiscountInstrument("2026-08-15", CP, calendar=cal)  # Saturday and Independence Day
    assert bill.payment_date() == D(2026, 8, 14)
    # Amounts and prices do not move; the calendar only dates the cash.
    plain, dated = FixedRateBond(6.36, "2031-02-16"), FixedRateBond(6.36, "2031-02-16", calendar=cal)
    assert plain.clean_price(6.8, "2026-05-18") == dated.clean_price(6.8, "2026-05-18")
    # A strict calendar refuses payment dates in years it has no list for.
    with pytest.raises(CalendarCoverageError):
        FixedRateBond(6.94, "2036-05-11", calendar=cal).cash_flows("2026-11-16")
    flows = FixedRateBond(6.94, "2036-05-11", calendar=cal.lenient()).cash_flows("2026-11-16")
    assert flows[-1].date == D(2036, 5, 11) and flows[-1].payment_date == D(2036, 5, 9)  # a Sunday


def test_irregular_first_coupons():
    short = FixedRateBond(6.94, "2036-05-11", issue_date="2026-02-01")
    first = short.cash_flows("2026-02-01")[0]
    assert first.date == D(2026, 5, 11) and first.accrual_start == D(2026, 2, 1)
    assert first.amount == pytest.approx(3.47 * 100 / 180)  # 1 Feb to 11 May is 100 days of 30E/360
    assert first.periods == pytest.approx(100 / 180)
    assert short.accrued_interest("2026-03-01") == pytest.approx(6.94 * 30 / 360)
    assert short.previous_coupon_date("2026-03-01") == D(2026, 2, 1)

    long_ = FixedRateBond(6.94, "2036-05-11", issue_date="2025-09-01", first_coupon_date="2026-05-11")
    assert long_.coupon_dates()[0] == D(2026, 5, 11)
    early = long_.cash_flows("2025-10-01")[0]  # before the skipped quasi-coupon date of 11 Nov 2025
    assert early.amount == pytest.approx(3.47 * 250 / 180)
    assert early.periods == pytest.approx(1 + 40 / 180)
    assert long_.next_coupon_date("2025-10-01") == D(2026, 5, 11)
    assert long_.accrued_interest("2025-12-01") == pytest.approx(6.94 * 90 / 360)
    for s in ("2025-09-01", "2025-10-01", "2026-01-20", "2026-05-11"):
        assert long_.yield_from_price(long_.clean_price(7.1, s), s) == pytest.approx(7.1, abs=1e-9)

    icma = FixedRateBond(6.94, "2036-05-11", dataclasses.replace(GSEC, day_count="ACT/ACT ICMA"),
                         issue_date="2025-09-01", first_coupon_date="2026-05-11")
    # Split over the quasi periods 11 May-11 Nov 2025 (184 days) and 11 Nov 2025-11 May 2026 (181).
    assert icma.cash_flows("2025-09-01")[0].amount == pytest.approx(3.47 * (71 / 184 + 1))


def test_zero_coupon_strips():
    strip = ZeroCouponBond("2036-05-11")
    assert strip.conventions is STRIPS and strip.accrued_interest("2026-11-16") == 0
    assert strip.dirty_price(7.0, "2026-11-16") == pytest.approx(100 / 1.035 ** (18 + 175 / 180))
    assert [f.kind for f in strip.cash_flows("2026-11-16")] == ["redemption"] and strip.coupon_dates(after="2026-01-01") == []
    assert strip.yield_from_price(52.0653, "2026-11-16") == pytest.approx(7.0, abs=1e-4)
    last = ZeroCouponBond("2027-05-11")  # compounding to the end, not simple interest
    assert last.dirty_price(7.0, "2026-11-16") == pytest.approx(100 / 1.035 ** (175 / 180))
    actual = ZeroCouponBond("2036-05-11", dataclasses.replace(STRIPS, time_basis="act/365"))
    days = (D(2036, 5, 11) - D(2026, 11, 16)).days
    assert actual.dirty_price(7.0, "2026-11-16") == pytest.approx(100 / 1.035 ** (days / 182.5))


@pytest.mark.parametrize("settle, days_left", [("2035-08-28", 2), ("2035-08-29", 1), ("2035-08-30", 1)])
def test_strip_in_its_last_days(settle, days_left):
    # 28 Feb-31 Aug 2035 is 182 days of 30E/360: these were priced at or above 100 at a positive yield.
    strip = ZeroCouponBond("2035-08-31")
    assert strip.dirty_price(7.0, settle) == pytest.approx(100 / 1.035 ** (days_left / 180))
    assert strip.dirty_price(7.0, settle) < 100 and strip.dv01(7.0, settle) > 0
    for price in (99.99, 99.0, 50.0):
        assert strip.clean_price(strip.yield_from_price(price, settle), settle) == pytest.approx(price, rel=1e-12)
    # A price far above face a day out has a yield too close to -200% to hold in a double: refused.
    with pytest.raises(ValueError, match="too close to -200%"):
        strip.yield_from_price(150.0, settle)


def test_tbill():
    # A 91-day bill issued on a Thursday matures on the Thursday 13 weeks later.
    bill = DiscountInstrument("2027-02-11", TBILL, issue_date="2026-11-12")
    assert bill.days_to_maturity("2026-11-12") == 91
    exact = 100 / (1 + 5.5 * 91 / 36500)
    assert bill.price(5.5, "2026-11-12", rounded=False) == pytest.approx(exact)
    assert bill.price(5.5, "2026-11-12") == 98.6473  # rounded off to 4 decimals (handbook 6.7.1)
    assert bill.yield_from_price(98.6473, "2026-11-12") == pytest.approx((100 - 98.6473) * 36500 / (98.6473 * 91))
    assert bill.yield_from_price(exact, "2026-11-12") == pytest.approx(5.5)
    for days in (1, 14, 91, 182, 364):
        p = bill.price(6.25, days=days, rounded=False)
        assert bill.yield_from_price(p, days=days) == pytest.approx(6.25)
    y = 5.5
    down, up = (bill.price(y + d, "2026-11-12", rounded=False) for d in (-0.005, 0.005))
    assert bill.dv01(y, "2026-11-12") == pytest.approx(down - up, rel=1e-6)
    assert bill.modified_duration(y, "2026-11-12") == pytest.approx((91 / 365) / (1 + 0.055 * 91 / 365))
    assert bill.convexity(y, "2026-11-12") > 0
    cmb = DiscountInstrument("2026-12-17", CMB)
    assert cmb.price(5.4, "2026-11-17") == pytest.approx(round(100 / (1 + 5.4 * 30 / 36500), 4))


def test_cp_and_cd():
    cp = DiscountInstrument("2027-02-15", CP)
    assert cp.price(7.25, "2026-11-17") == 98.2437
    assert DiscountInstrument("2027-02-15", dataclasses.replace(CP, price_rounding="truncate")).price(
        7.25, "2026-11-17") == 98.2437
    cd = DiscountInstrument("2027-11-17", CD)
    assert cd.price(7.0, "2026-11-17") == round(100 / (1 + 7.0 * 365 / 36500), 4)
    assert cd.yield_from_price(cd.price(7.0, "2026-11-17", rounded=False), "2026-11-17") == pytest.approx(7.0)
    assert DiscountInstrument("2026-12-01", dataclasses.replace(CP, price_rounding="truncate")).price(
        6.9, days=9) == math.floor(100 / (1 + 6.9 * 9 / 36500) * 1e4) / 1e4


def test_front_ended_discount_handbook_example():
    # FIMMDA Handbook 2026, 6.3.2 (bill rediscounting): Rs 10 crore for 45 days at 10.25% front-ended,
    # amount payable Rs. 9,87,36,301. https://www.fimmda.org/UploadPopupPageFiles/Fimmda_Handbook_2026_Final_Ver.pdf
    brds = DiscountInstrument("2030-01-01", dataclasses.replace(TBILL, name="Bill rediscounting", discount="front",
                                                                price_decimals=None))
    price = brds.price(10.25, days=45)
    assert f"{price:.6f}" == "98.736301"
    assert round(10_00_00_000 * price / 100) == 9_87_36_301
    rear = DiscountInstrument("2030-01-01", TBILL)
    assert rear.yield_from_price(98.736301, days=45) == pytest.approx(10.381190, abs=1e-6)  # rear-ended equivalent
    assert brds.yield_from_price(98.736301, days=45) == pytest.approx(10.25, abs=1e-5)


def test_errors():
    bond = FixedRateBond(6.94, "2036-05-11", issue_date="2026-05-11")
    with pytest.raises(ValueError, match="on or after maturity"):
        bond.clean_price(7.0, "2036-05-11")
    with pytest.raises(ValueError, match="before issue date"):
        bond.accrued_interest("2026-05-08")
    with pytest.raises(ValueError, match="not on the coupon schedule"):
        FixedRateBond(6.94, "2036-05-11", issue_date="2026-01-01", first_coupon_date="2026-06-11")
    with pytest.raises(ValueError, match="needs issue_date"):
        FixedRateBond(6.94, "2036-05-11", first_coupon_date="2026-11-11")
    with pytest.raises(ValueError, match="coupon_pct"):
        FixedRateBond(-1.0, "2036-05-11")
    with pytest.raises(NotImplementedError, match="record dates"):
        FixedRateBond(6.94, "2036-05-11", dataclasses.replace(GSEC, ex_coupon_days=15))
    with pytest.raises(TypeError):
        FixedRateBond(6.94, "2036-05-11", TBILL)
    with pytest.raises(TypeError):
        DiscountInstrument("2027-02-11", GSEC)
    with pytest.raises(ValueError, match="exactly one"):
        bond.analytics("2026-11-16")
    with pytest.raises(ValueError, match="positive"):
        bond.yield_from_price(-5.0, "2026-11-16")
    bill = DiscountInstrument("2027-02-11", issue_date="2026-11-12")
    with pytest.raises(ValueError, match="on or after maturity"):
        bill.price(5.5, "2027-02-11")
    with pytest.raises(ValueError, match="not both"):
        bill.price(5.5, "2026-11-12", days=91)
    with pytest.raises(ValueError, match="before issue date"):
        bill.days_to_maturity("2026-11-11")


@pytest.mark.parametrize("price", [0.0, -1.0, math.nan, math.inf])
def test_clean_price_must_be_positive(price):
    # On 10 May 2027 accrued is 3.45, so a clean price of 0 or -1 still gives a positive dirty price.
    bond = FixedRateBond(6.94, "2036-05-11")
    with pytest.raises(ValueError, match="clean price must be a positive number"):
        bond.yield_from_price(price, "2027-05-10")
    with pytest.raises(ValueError, match="clean price must be a positive number"):
        bond.analytics("2027-05-10", clean_price=price)
    with pytest.raises(ValueError, match="dirty price must be a positive number"):
        bond.yield_from_dirty_price(price, "2027-05-10")


@pytest.mark.parametrize("y", [math.nan, math.inf, -math.inf, -250.0, -200.0])
def test_yields_must_be_finite_and_above_the_floor(y):
    bond = FixedRateBond(6.94, "2036-05-11")
    for method in (bond.dirty_price, bond.clean_price, bond.macaulay_duration, bond.modified_duration,
                   bond.dv01, bond.convexity):
        with pytest.raises(ValueError, match="yield must be"):
            method(y, "2027-05-10")
    with pytest.raises(ValueError, match="yield must be"):
        bond.analytics("2027-05-10", yield_pct=y)


def test_yield_floor_in_the_simple_last_period():
    # Simple interest is defined down to -2 / w: w = 135/180 on 15 Feb 2028, so -266.7%.
    last = FixedRateBond(5.91, "2028-06-30")
    assert last.dirty_price(-250.0, "2028-02-15") > 0
    with pytest.raises(ValueError, match=r"above -266\.667%"):
        last.dirty_price(-270.0, "2028-02-15")


def test_discount_instrument_inputs():
    with pytest.raises(TypeError, match="BusinessCalendar"):
        DiscountInstrument("2027-02-11", calendar="mumbai")
    bill = DiscountInstrument("2027-02-11")
    assert bill.price(5.5, days=91.0) == bill.price(5.5, days=np.int64(91)) == bill.price(5.5, "2026-11-12")
    with pytest.raises(ValueError, match="days must be a whole number"):
        bill.price(5.5, days=91.9)
    with pytest.raises(TypeError, match="days"):
        bill.price(5.5, days=True)
    with pytest.raises(ValueError, match="days must be >= 1"):
        bill.price(5.5, days=0)
    for y in (math.nan, math.inf):
        for method in (bill.price, bill.modified_duration, bill.dv01, bill.convexity):
            with pytest.raises(ValueError, match="finite"):
                method(y, "2026-11-12")
    with pytest.raises(ValueError, match="zero or negative"):
        bill.price(-500.0, "2026-11-12")  # 1 + y d / 365 below zero
    front = DiscountInstrument("2027-02-11", dataclasses.replace(TBILL, discount="front"))
    with pytest.raises(ValueError, match="no positive price"):
        front.price(500.0, "2026-11-12")  # was priced at -24.6575
    for price in (0.0, -1.0, math.nan, math.inf):
        with pytest.raises(ValueError, match="price must be a positive number"):
            bill.yield_from_price(price, "2026-11-12")


def test_coupon_dates_refuse_settlements_outside_the_bond():
    bond = FixedRateBond(6.94, "2036-05-11", issue_date="2026-05-11")
    for method in (bond.previous_coupon_date, bond.next_coupon_date):
        with pytest.raises(ValueError, match="before issue date"):
            method("2026-01-01")  # gave 11 May and 11 Nov 2026
        with pytest.raises(ValueError, match="on or after maturity"):
            method("2037-01-01")
    assert bond.next_coupon_date("2036-05-10") == D(2036, 5, 11)
    assert bond.previous_coupon_date("2026-05-11") == D(2026, 5, 11)
    with pytest.raises(ValueError, match="on or after maturity"):
        bond.in_shut_period("2036-05-11")


def test_price_bonds_frame():
    frame = pd.DataFrame({
        "coupon_pct": [6.94, 5.91, 6.94],
        "maturity_date": ["2036-05-11", "2028-06-30", "2036-05-11"],
        "settlement_date": [pd.Timestamp("2026-11-16"), pd.Timestamp("2025-12-29"), pd.Timestamp("2036-06-01")],
        "clean_price": [97.43, 100.18, 99.0],
    })
    out = price_bonds(frame)
    assert out["clean_price"].tolist() == frame["clean_price"].tolist()
    assert np.round(out["yield_pct"].iloc[:2], 4).tolist() == [7.3203, 5.8316]
    assert out["error"].iloc[:2].tolist() == ["", ""] and "maturity" in out["error"].iloc[2]
    assert np.isnan(out["dv01"].iloc[2])
    back = price_bonds(out.iloc[:2].drop(columns=["clean_price"]), given="yield_pct")
    assert back["clean_price"].tolist() == pytest.approx([97.43, 100.18])
    with pytest.raises(ValueError, match="missing columns"):
        price_bonds(frame.drop(columns=["clean_price"]))


def test_price_bonds_frame_inputs():
    frame = pd.DataFrame({
        "coupon_pct": [6.94] * 5,
        "maturity_date": ["2036-05-11"] * 5,
        "settlement_date": ["2026-11-16 00:00:00", pd.Timestamp("2026-11-16"), "2026-11-16", "2026-11-16",
                            "16-11-2026"],  # ISO date-times pass; day-first dates are refused
        "issue_date": ["", None, np.nan, pd.NaT, ""],  # blank cells mean no issue date
        "price": [97.43, 97.43, -1.0, 97.43, 97.43],  # a trades-style column name
    })
    out = price_bonds(frame, column="price")
    assert np.round(out["yield_pct"].iloc[[0, 1, 3]], 4).tolist() == [7.3203] * 3
    assert out["error"].iloc[2] == "clean price must be a positive number, not -1.0"
    assert "Invalid isoformat string" in out["error"].iloc[4]
    assert all(out[c].dtype == np.float64 for c in ANALYTICS_COLUMNS if c != "clean_price")
    with pytest.raises(ValueError, match=r"missing columns: \['clean_price'\]"):
        price_bonds(frame)
    with pytest.raises(ValueError, match="would be overwritten"):
        price_bonds(frame.rename(columns={"price": "dv01"}), column="dv01")

    by_yield = price_bonds(frame.drop(columns="price").assign(yield_pct=[7.0, -250.0, np.nan, math.inf, 7.0]),
                           given="yield_pct")
    assert by_yield["error"].iloc[1] == "yield must be above -200% for these cash flows, not -250.0%"
    assert by_yield["error"].iloc[2] == "yield_pct is missing"
    assert by_yield["error"].iloc[3] == "yield must be a finite number, not inf"
    # One bad row no longer turns a column complex: every result column stays float.
    assert all(by_yield[c].dtype == np.float64 for c in ANALYTICS_COLUMNS if c != "yield_pct")
    assert by_yield["macaulay_duration"].iloc[0] > 0 and np.isnan(by_yield["macaulay_duration"].iloc[1])
