"""Corporate bonds (NCDs): SEBI actual/actual, holidays, record dates, workouts, steps, amortisation, term tables.

Worked examples are typed-in numbers from public documents (their wording is not reproduced):
- SEBI master circular for non-convertible securities (15 Oct 2025), cash-flow illustration: 8.95% on Rs 10 lakh,
  allotted 14 Dec 2020, maturing 14 Dec 2025.
- SEBI circular CIR/IMD/DF/18/2013, Annex A: 8.95% on Rs 10 lakh, 13 Nov 2013 to 13 Nov 2018.
- SEBI circular CIR/IMD/DF-1/122/2016, examples (a) and (b):
  https://www.sebi.gov.in/sebi_data/attachdocs/aug-2022/1659352777649.pdf
- Kosamattam Finance, illustrative cash flows of its Nov 2025 public issue (Series II and VII):
  https://www.kosamattam.com/media/fxxjxeot/illustrative-cash-flow.pdf
- Power Finance Corporation, Tranche I of Jan 2026, and Capri Global Capital, Oct 2025: printed effective yields,
  cumulative amounts and issue prices (term sheets at https://media.thefixedincome.com).
Everything else is synthetic, with answers computed independently (corporates/scripts/ref.py in the build notes).
"""

import dataclasses
import math
import random
from datetime import date, datetime

import pandas as pd
import pytest

from cr_strat import conventions
from cr_strat.bonds import FixedRateBond
from cr_strat.calendars import BusinessCalendar, mumbai_settlement_calendar
from cr_strat.conventions import (
    CONVENTIONS,
    CORPORATE_CONVENTIONS,
    GOI_FRB,
    GSEC,
    NCD,
    NCD_2013,
    NCD_PRE2013,
    convert_yield,
    coupon_period_days,
    day_count_days,
    ncd_conventions,
    year_fraction,
)
from cr_strat.corporates import (
    PRICED_COLUMNS,
    CallPut,
    CorporateBond,
    StepRule,
    apply_rating_steps,
    corporate_from_terms,
    perpetual_bond,
    price_corporates,
    public_time,
    read_corporate_options,
    read_corporate_schedule,
    read_corporate_terms,
    year_fraction_sebi,
)

D = date
LAKH10 = 1_000_000


@pytest.fixture(scope="module")
def cal():
    return mumbai_settlement_calendar()


@pytest.fixture(scope="module")
def weekends():
    return BusinessCalendar("weekends", frozenset(), frozenset(range(2010, 2040)))


def sebi_2025(**kw):
    """SEBI's NCS master circular illustration: 8.95% annual on Rs 10 lakh, 14 Dec 2020 - 14 Dec 2025."""
    return CorporateBond(8.95, D(2025, 12, 14), D(2020, 12, 14), face_value_rs=LAKH10, **kw)


# ---------------------------------------------------------------- conventions


def test_conventions_entries():
    assert set(CONVENTIONS) == {"gsec", "sdl", "strips", "tbill", "cmb", "cp", "cd"}  # bonds.py's instruments only
    assert CORPORATE_CONVENTIONS == {"ncd": NCD, "ncd_2013": NCD_2013, "ncd_pre2013": NCD_PRE2013, "frb": GOI_FRB}
    assert (NCD.day_count, NCD.compounding, NCD.time_basis, NCD.settlement_lag) == ("ACT/ACT SEBI", "annual",
                                                                                     "act/365", 1)
    assert (NCD.ex_coupon_days, NCD.coupon_roll, NCD.redemption_roll, NCD.price_decimals) == (15, "following",
                                                                                              "preceding", 4)
    assert (NCD.leap_year_rule, NCD.postponed_coupon, NCD.redemption_interest, NCD.flow_dates) == (
        "anniversary", "scheduled", "scheduled", "by_date")
    assert NCD.due_dates_from == D(2025, 8, 18) and NCD.mm_final_days == 365
    assert (NCD_2013.postponed_coupon, NCD_2013.redemption_interest) == ("to_payment", "to_payment")
    assert (NCD_PRE2013.day_count, NCD_PRE2013.leap_year_rule) == ("ACT/365F", "fixed365")
    assert dataclasses.replace(GOI_FRB, name=GSEC.name) == GSEC
    assert ncd_conventions("2017-01-01") is NCD and ncd_conventions("2016-12-31") is NCD_2013
    assert ncd_conventions("2013-12-01") is NCD_2013 and ncd_conventions("2013-11-30") is NCD_PRE2013
    assert isinstance(NCD, conventions.BondConventions)


def test_corporate_conventions_validate_their_fields():
    for field, bad in [("leap_year_rule", "isda"), ("postponed_coupon", "late"), ("redemption_interest", "x"),
                       ("flow_dates", "settle"), ("record_roll", "nearest"), ("anniversary_anchor", "coupon")]:
        with pytest.raises(ValueError, match=field):
            dataclasses.replace(NCD, **{field: bad})
    with pytest.raises(ValueError, match="time_basis"):
        dataclasses.replace(NCD, time_basis="periods")
    with pytest.raises(ValueError, match="disagree"):
        dataclasses.replace(NCD, day_count="ACT/365F")
    with pytest.raises(ValueError, match="mm_final_days"):
        dataclasses.replace(NCD, mm_final_days=-1)
    with pytest.raises(ValueError, match="amount_decimals"):
        dataclasses.replace(NCD, amount_decimals=-2)
    with pytest.raises(ValueError, match="payment_calendar_name"):
        dataclasses.replace(NCD, payment_calendar_name=" ")
    assert dataclasses.replace(NCD, due_dates_from="2025-08-18").due_dates_from == D(2025, 8, 18)


def test_act_act_sebi_is_refused_where_its_year_is_undefined():
    assert day_count_days("ACT/ACT SEBI", D(2024, 1, 1), D(2024, 7, 1)) == 182
    for call in (lambda: year_fraction("ACT/ACT SEBI", D(2024, 1, 1), D(2024, 7, 1)),
                 lambda: coupon_period_days("ACT/ACT SEBI", D(2024, 1, 1), D(2024, 7, 1), 2)):
        with pytest.raises(ValueError, match="corporates.year_fraction_sebi"):
            call()
    with pytest.raises(NotImplementedError, match="ex_coupon_days"):  # T24: NCD has a 15-day record date
        FixedRateBond(8.0, D(2030, 1, 1), NCD)
    bond = FixedRateBond(8.0, D(2030, 1, 1), dataclasses.replace(NCD, ex_coupon_days=0))
    with pytest.raises(ValueError, match="corporates.year_fraction_sebi"):
        bond.dirty_price(8.0, D(2026, 6, 1))
    with pytest.raises(TypeError, match="CorporateConventions"):
        CorporateBond(8.0, D(2030, 1, 1), D(2025, 1, 1), GSEC)


def test_monthly_and_quarterly_bases_convert():
    assert convert_yield(8.90, "monthly", "annual") == pytest.approx(((1 + 0.089 / 12) ** 12 - 1) * 100)
    assert convert_yield(8.0, "quarterly", "annual") == pytest.approx((1.02 ** 4 - 1) * 100)
    assert convert_yield(convert_yield(7.5, "annual", "monthly"), "monthly", "annual") == pytest.approx(7.5)


# ---------------------------------------------------------------- the SEBI year


def test_sebi_2016_example_b_both_readings():
    # Issue 1 Jan 2016, coupons 1 Jul and 1 Jan: 366 days for both half-years of 2016 (T4).
    for rule in ("anniversary", "calendar"):
        assert year_fraction_sebi(D(2016, 1, 1), D(2016, 7, 1), rule=rule, anchor=D(2016, 1, 1)) == \
            pytest.approx(182 / 366, abs=1e-15)
        assert year_fraction_sebi(D(2016, 7, 1), D(2017, 1, 1), rule=rule, anchor=D(2016, 1, 1)) == \
            pytest.approx(184 / 366, abs=1e-15)


def test_year_fraction_rules_and_refusals():
    a, b, anchor = D(2023, 12, 14), D(2024, 6, 14), D(2020, 12, 14)
    assert year_fraction_sebi(a, b, anchor=anchor) == pytest.approx(183 / 366)  # in the leap coupon year
    assert year_fraction_sebi(a, b, rule="calendar") == pytest.approx(18 / 365 + 165 / 366)
    assert year_fraction_sebi(a, b, rule="fixed365") == pytest.approx(183 / 365)
    assert year_fraction_sebi(a, a, anchor=anchor) == 0.0
    # A 29 Feb anchor falls on 28 Feb in common years. SEBI 2016 para 3(b) counts 366 days for the year holding 29
    # Feb: 29 Feb 2024 - 28 Feb 2025 (365 days) holds one, 28 Feb 2027 - 29 Feb 2028 (366 days) holds none.
    # (UNVERIFIED reading; the build counted each year's own length, 1.0 for both.)
    assert year_fraction_sebi(D(2027, 2, 28), D(2028, 2, 29), anchor=D(2024, 2, 29)) == pytest.approx(366 / 365)
    assert year_fraction_sebi(D(2024, 2, 29), D(2025, 2, 28), anchor=D(2024, 2, 29)) == pytest.approx(365 / 366)
    # the half-years of a semi-annual bond allotted on 29 Feb 2024 (the math review's case)
    leap = CorporateBond(10.0, D(2029, 2, 28), D(2024, 2, 29), frequency=2)
    amounts = {p.due: round(p.amount, 6) for p in leap.periods()}
    assert amounts[D(2024, 8, 29)] == 4.972678  # 182 / 366
    assert amounts[D(2027, 8, 29)] == 4.986301  # 182 / 365
    for anchor_ in (D(2023, 3, 1), D(2023, 2, 28), D(2024, 1, 15)):  # other anchors: unchanged, a year's length
        assert year_fraction_sebi(anchor_, anchor_.replace(year=anchor_.year + 1), anchor=anchor_) == 1.0
    # Additive over a split point, every rule.
    for rule in ("anniversary", "calendar", "fixed365"):
        whole = year_fraction_sebi(D(2023, 3, 5), D(2026, 9, 9), rule=rule, anchor=anchor)
        parts = (year_fraction_sebi(D(2023, 3, 5), D(2024, 8, 1), rule=rule, anchor=anchor)
                 + year_fraction_sebi(D(2024, 8, 1), D(2026, 9, 9), rule=rule, anchor=anchor))
        assert whole == pytest.approx(parts, abs=1e-14)
    with pytest.raises(ValueError, match="before start"):
        year_fraction_sebi(b, a, anchor=anchor)
    with pytest.raises(ValueError, match="anchor"):
        year_fraction_sebi(a, b)
    with pytest.raises(ValueError, match="rule"):
        year_fraction_sebi(a, b, rule="isda", anchor=anchor)


# ---------------------------------------------------------------- SEBI's illustrations


def test_sebi_2025_illustration_anniversary_rule(cal):
    # T1: 89,500 every year; the 14 Dec 2024 coupon (second Saturday) paid Mon 16 Dec at the full amount; the
    # maturity on Sun 14 Dec 2025 paid on Fri 12 Dec with the full last coupon; Rs 14,47,500 in all.
    sched = sebi_2025(calendar=cal).cash_schedule()
    coupons = sched[sched["kind"] == "coupon"]
    assert coupons["amount_rs"].round(2).tolist() == [89_500.0] * 5
    assert coupons["payment_date"].tolist() == [D(2021, 12, 14), D(2022, 12, 14), D(2023, 12, 14), D(2024, 12, 16),
                                               D(2025, 12, 12)]
    assert coupons["days"].tolist() == [365, 365, 365, 366, 365]
    assert sched["amount_rs"].sum() == pytest.approx(1_447_500.0, abs=1e-6)
    assert sched.iloc[-1][["kind", "payment_date", "amount_rs"]].tolist() == ["redemption", D(2025, 12, 12), LAKH10]


def test_sebi_2025_illustration_rejects_the_calendar_split(cal):
    coupons = sebi_2025(calendar=cal, leap_year_rule="calendar").cash_schedule().query("kind == 'coupon'")
    assert coupons["amount_rs"].round(2).tolist() == [89_487.94, 89_500.0, 89_500.0, 89_512.06, 89_487.94]


def test_redemption_interest_to_payment(cal):
    # Per-issue option (PFC, Capri): interest only to the early payment date, 363/365 of a coupon.
    coupons = sebi_2025(calendar=cal, redemption_interest="to_payment").cash_schedule().query("kind == 'coupon'")
    assert coupons["amount_rs"].round(2).tolist()[-1] == 89_009.59
    assert coupons["accrual_end"].tolist()[-1] == D(2025, 12, 12)


def test_sebi_2013_illustration_legacy_rule(weekends, cal):
    # T2: a coupon due Sun 13 Nov 2016 paid Mon 14 Nov accrues 367 days, the next 364; rupee-rounded total.
    bond = CorporateBond(8.95, D(2018, 11, 13), D(2013, 11, 13), NCD_2013, calendar=weekends, face_value_rs=LAKH10)
    sched = bond.cash_schedule(decimals=0)
    coupons = sched[sched["kind"] == "coupon"]
    assert coupons["days"].tolist() == [365, 365, 367, 364, 365]
    assert coupons["amount_rs"].round(2).tolist() == [89_500.0, 89_500.0, 89_745.21, 89_254.79, 89_500.0]
    assert coupons["amount_rs_rounded"].tolist() == [89_500, 89_500, 89_745, 89_255, 89_500]
    assert sched["amount_rs_rounded"].sum() == 1_447_500
    # On the real calendar 14 Nov 2016 was a holiday: paid Tue 15 Nov, 368 then 363 days.
    real = CorporateBond(8.95, D(2018, 11, 13), D(2013, 11, 13), NCD_2013, calendar=cal.lenient(),
                         face_value_rs=LAKH10).cash_schedule().query("kind == 'coupon'")
    assert real["payment_date"].tolist()[2] == D(2016, 11, 15)
    assert real["amount_rs"].round(2).tolist()[2:4] == [89_990.41, 89_009.59]


def test_settlement_calendar_days(cal):
    # T3
    assert cal.is_business_day(D(2024, 12, 16)) and cal.is_business_day(D(2025, 12, 12))
    for holiday in (D(2016, 11, 14), D(2026, 10, 2), D(2026, 11, 24)):
        assert not cal.is_business_day(holiday)


def test_sebi_2016_example_a_forward_schedule(cal):
    # T5: issue 1 Jul 2016, maturity 30 Jun 2018, coupons 1 Jan and 1 Jul at 10%.
    bond = CorporateBond(10.0, D(2018, 6, 30), D(2016, 7, 1), frequency=2, calendar=cal)
    periods = bond.periods()
    assert [p.due for p in periods] == [D(2017, 1, 1), D(2017, 7, 1), D(2018, 1, 1), D(2018, 6, 30)]
    assert [p.payment for p in periods] == [D(2017, 1, 2), D(2017, 7, 3), D(2018, 1, 1), D(2018, 6, 29)]
    assert [round(p.amount, 6) for p in periods] == [5.041096, 4.958904, 5.041096, 4.931507]
    assert periods[0].end == D(2017, 1, 1)  # interest only to the due date, though paid on 2 Jan
    backward = CorporateBond(10.0, D(2018, 6, 30), D(2016, 7, 1), frequency=2, schedule="backward")
    assert backward.due_schedule == (D(2016, 12, 30), D(2017, 6, 30), D(2017, 12, 30), D(2018, 6, 30))


def test_accrued_interest_over_a_leap_year():
    # T6: last coupon 14 Dec 2023, the coupon year to 14 Dec 2024 holds 29 Feb 2024.
    s = D(2024, 6, 14)
    assert sebi_2025().accrued_interest(s) == pytest.approx(4.4750000000, abs=1e-10)
    assert sebi_2025(leap_year_rule="calendar").accrued_interest(s) == pytest.approx(4.4762059286, abs=1e-10)
    assert sebi_2025(leap_year_rule="fixed365").accrued_interest(s) == pytest.approx(4.4872602740, abs=1e-10)
    bond = sebi_2025()
    for day, value in ((D(2024, 1, 15), 0.7825136612), (D(2024, 2, 29), 1.8829234973), (D(2024, 3, 1), 1.9073770492)):
        assert bond.accrued_interest(day) == pytest.approx(value, abs=1e-10)
    # 13 Dec 2024 is after the coupon's record date (29 Nov): ex-interest, -8.95 x 1/366.
    assert bond.accrued_interest(D(2024, 12, 13)) == pytest.approx(-8.95 / 366, abs=1e-12)
    assert sebi_2025(record_days=0).accrued_interest(D(2024, 12, 13)) == pytest.approx(8.9255464481, abs=1e-10)


# Kosamattam Finance, Nov 2025 issue: monthly interest per Rs 1,000, rounded to the paisa (T7).
KOSAMATTAM = {
    "II": (9.50, D(2028, 11, 9), [
        5.47, 8.07, 8.07, 7.29, 8.07, 7.81, 8.07, 7.81, 8.07, 8.07, 7.81, 8.07, 7.81, 8.07, 8.07, 7.29, 8.07, 7.81,
        8.07, 7.81, 8.07, 8.07, 7.81, 8.07, 7.81, 8.07, 8.05, 7.53, 8.05, 7.79, 8.05, 7.79, 8.05, 8.05, 7.79, 8.05,
        2.08]),
    "VII": (10.00, D(2030, 11, 9), [
        5.75, 8.49, 8.49, 7.67, 8.49, 8.22, 8.49, 8.22, 8.49, 8.49, 8.22, 8.49, 8.22, 8.49, 8.49, 7.67, 8.49, 8.22,
        8.49, 8.22, 8.49, 8.49, 8.22, 8.49, 8.22, 8.49, 8.47, 7.92, 8.47, 8.20, 8.47, 8.20, 8.47, 8.47, 8.20, 8.47,
        8.20, 8.47, 8.49, 7.67, 8.49, 8.22, 8.49, 8.22, 8.49, 8.49, 8.22, 8.49, 8.22, 8.49, 8.49, 7.67, 8.49, 8.22,
        8.49, 8.22, 8.49, 8.49, 8.22, 8.49, 2.19]),
}


@pytest.mark.parametrize("series", ["II", "VII"])
def test_kosamattam_monthly_table(series):
    rate, maturity, printed = KOSAMATTAM[series]

    def coupons(rule):
        bond = CorporateBond(rate, maturity, D(2025, 11, 10), frequency=12, schedule="month_start",
                             leap_year_rule=rule, face_value_rs=1000)
        sched = bond.cash_schedule(decimals=2)
        return sched.loc[sched["kind"] == "coupon", "amount_rs_rounded"].tolist()

    assert coupons("calendar") == printed  # every coupon to the paisa
    misses = [i + 1 for i, (got, want) in enumerate(zip(coupons("anniversary"), printed)) if abs(got - want) > 0.004]
    assert misses == ([25, 26] if series == "II" else [25, 26, 37, 38])
    first = CorporateBond(rate, maturity, D(2025, 11, 10), frequency=12, schedule="month_start").periods()[0]
    assert (first.start, first.due) == (D(2025, 11, 10), D(2025, 12, 1))  # 21 days, then calendar months


# ---------------------------------------------------------------- issuers' printed yields


def _years(d: date, months: int) -> date:
    from cr_strat.bonds import _shift_months
    return _shift_months(d, months)


@pytest.mark.parametrize("allotment", [D(2026, 2, 3), D(2026, 2, 6)])
def test_pfc_effective_yields(allotment):
    # T8: annual par series on due-date XIRR, within 1 bp of the printed 6.85/6.99/7.04/7.00/7.19/7.29.
    expected = [(5, 6.85, 6.8461, 6.85), (10, 7.00, 6.9959, 6.99), (15, 7.05, 7.0450, 7.04), (5, 7.00, 6.9961, 7.00),
                (10, 7.20, 7.1958, 7.19), (15, 7.30, 7.2948, 7.29)]
    for years, coupon, computed, printed in expected:
        y = CorporateBond(coupon, _years(allotment, 12 * years), allotment).yield_from_price(100.0, allotment)
        assert round(y, 4) == computed and abs(y - printed) < 0.01
    # Cumulative series: Rs 1,000 to 2,780.50 / 2,839.56 / 2,879.58 after 15 years (5,479 days).
    maturity = _years(allotment, 180)
    assert (maturity - allotment).days == 5479
    unit = CorporateBond(0.0, maturity, allotment, frequency=0)  # redeems 100 per 100 face
    for rate, printed, computed in ((7.05, 2780.50, 2780.5090), (7.20, 2839.56, 2839.5707),
                                    (7.30, 2879.58, 2879.5934)):
        amount = 1000 * 100 / unit.dirty_price(rate, allotment)  # what Rs 1,000 grows to at the yield
        assert round(amount, 4) == computed and abs(amount - printed) <= 0.02
        bond = CorporateBond(0.0, maturity, allotment, frequency=0, redemption=printed / 10)
        assert bond.yield_from_price(100.0, allotment) == pytest.approx(rate, abs=1e-4)
        assert bond.accrued_interest(allotment) == 0.0
    # Whole-year compounding (1,000 x 1.0705^15 = 2,778.43) misses the printed amount by Rs 2.
    assert abs(1000 * 1.0705 ** 15 - 2780.50) > 2.0
    # Zero coupon, 10 years 1 month (3,681 days): issue prices for Rs 1,00,000 face.
    zero = CorporateBond(0.0, _years(allotment, 121), allotment, frequency=0)
    for price, computed in ((51_502, 6.8009), (51_263, 6.8502), (50_780, 6.9505)):
        assert round(zero.yield_from_price(price / 1000, allotment), 4) == computed


def test_capri_effective_yields():
    # T9 (allotment 24 Oct 2025 assumed); the monthly series pay on the 1st, the first period clubbed.
    allotment = D(2025, 10, 24)
    cases = [(18, 12, 8.55, 8.8929), (18, 1, 8.90, 8.9623), (36, 12, 8.90, 9.2639), (36, 1, 9.25, 9.2419),
             (60, 1, 9.45, 9.4446), (120, 1, 9.70, 9.6943)]
    for months, frequency, coupon, computed in cases:
        kw = dict(frequency=12, schedule="month_start", first_period="clubbed") if frequency == 12 else {}
        bond = CorporateBond(coupon, _years(allotment, months), allotment, **kw)
        assert round(bond.yield_from_price(100.0, allotment), 4) == computed
    # The printed 9.27% of the 36-month monthly series is the nominal rate compounded monthly, not the XIRR.
    assert round(convert_yield(8.90, "monthly", "annual"), 2) == 9.27
    clubbed = CorporateBond(8.55, _years(allotment, 18), allotment, frequency=12, schedule="month_start",
                            first_period="clubbed").periods()[0]
    assert (clubbed.start, clubbed.due) == (D(2025, 10, 24), D(2025, 12, 1))


# ---------------------------------------------------------------- price, yield and risk


@pytest.mark.parametrize("bond, settlement", [
    (CorporateBond(8.95, D(2030, 12, 14), D(2023, 12, 14)), D(2026, 11, 16)),
    (CorporateBond(9.5, D(2028, 11, 9), D(2025, 11, 10), frequency=12, schedule="month_start"), D(2026, 3, 17)),
    (CorporateBond(10.0, D(2018, 6, 30), D(2016, 7, 1), frequency=2), D(2017, 2, 10)),
    (CorporateBond(0.0, D(2036, 3, 3), D(2026, 2, 3), frequency=0), D(2027, 5, 5)),
    (CorporateBond(7.0, D(2031, 4, 1), D(2026, 4, 1), frequency=4,
                   amortisation=((D(2029, 4, 1), 25), (D(2030, 4, 1), 25))), D(2029, 8, 20)),
])
def test_round_trip_and_risk(bond, settlement):
    # T10: a bond priced at a yield returns that yield; risk numbers are derivatives of the price.
    for y in (-0.5, 4.0, 8.3, 15.0):
        clean = bond.clean_price(y, settlement)
        assert bond.yield_from_price(clean, settlement) == pytest.approx(y, abs=1e-9)
    y, h = 8.0, 1e-4
    up, down, mid = (bond.dirty_price(v, settlement) for v in (y + h, y - h, y))
    assert bond.dv01(y, settlement) == pytest.approx((down - up) / (2 * h) / 100, rel=1e-6)  # per bp
    assert bond.modified_duration(y, settlement) == pytest.approx((down - up) / (2 * h / 100) / mid, rel=1e-6)
    h2 = 1e-2  # a wider step for the second difference, to keep rounding error small
    up2, down2 = bond.dirty_price(y + h2, settlement), bond.dirty_price(y - h2, settlement)
    assert bond.convexity(y, settlement) == pytest.approx((up2 + down2 - 2 * mid) / (h2 / 100) ** 2 / mid, rel=1e-5)
    a = bond.analytics(settlement, yield_pct=y)
    assert a.dv01 > 0 and a.macaulay_duration > 0
    assert a.clean_price == pytest.approx(a.dirty_price - a.accrued_interest)


def test_money_market_rule_in_the_last_year():
    # T16: one flow left, 183 days away: simple interest on actual/365 (FIMMDA handbook 6.9.1(c)).
    bond = CorporateBond(8.95, D(2030, 12, 14), D(2023, 12, 14))
    s = D(2030, 6, 14)
    assert bond.accrued_interest(s) == pytest.approx(4.4627397260, abs=1e-10)
    assert bond.yield_from_price(100.0, s) == pytest.approx(8.5676481619, abs=1e-9)
    assert bond.yield_from_price(100.5, s) == pytest.approx(7.5767193779, abs=1e-9)
    compound = dataclasses.replace(bond, conventions=dataclasses.replace(NCD, final_period="compound"))
    assert compound.yield_from_price(100.0, s) == pytest.approx(8.7506427110, abs=1e-9)
    assert compound.yield_from_price(100.5, s) == pytest.approx(7.7198330627, abs=1e-9)
    # A year or more away the yield is compounded again.
    assert bond.yield_from_price(100.0, D(2029, 12, 14)) == pytest.approx(8.95, abs=1e-9)
    # At the boundary: 364 days away is simple, 366 days away (over 29 Feb 2028) compounded.
    zero = CorporateBond(0.0, D(2028, 3, 1), D(2026, 3, 1), frequency=0)
    assert zero.yield_from_price(92.0, D(2027, 3, 3)) == pytest.approx((100 / 92 - 1) * 365 / 364 * 100, abs=1e-9)
    assert zero.yield_from_price(92.0, D(2027, 3, 1)) == pytest.approx(((100 / 92) ** (365 / 366) - 1) * 100,
                                                                        abs=1e-9)


def test_due_dates_payment_dates_and_bases(cal):
    # T17/T19 on the 8.95% 2023-2030 bond at a clean price of 102.2591928939 for 16 Nov 2026.
    bond = CorporateBond(8.95, D(2030, 12, 14), D(2023, 12, 14), calendar=cal.lenient())
    s, clean = D(2026, 11, 16), 102.2591928939
    assert bond.accrued_interest(s) == pytest.approx(8.2634246575, abs=1e-10)
    assert bond.yield_from_price(clean, s) == pytest.approx(8.2612262724, abs=1e-9)  # due dates from 18 Aug 2025
    on_payment = dataclasses.replace(bond, flow_dates="payment")  # redemption Fri 13 Dec 2030 for Sat 14 Dec
    assert on_payment.yield_from_price(clean, s) == pytest.approx(8.2662660611, abs=1e-9)
    assert bond.with_compounding("semiannual").yield_from_price(clean, s) == pytest.approx(8.0973101915, abs=1e-9)
    # Before 18 Aug 2025 "by_date" discounts on payment dates.
    early = D(2025, 6, 2)
    assert bond.yield_from_price(100.0, early) == pytest.approx(
        on_payment.yield_from_price(100.0, early), abs=1e-12)
    assert bond.yield_from_price(100.0, early) != pytest.approx(
        dataclasses.replace(bond, flow_dates="due").yield_from_price(100.0, early), abs=1e-6)


def test_compounding_bases_convert_exactly():
    bond = CorporateBond(9.0, D(2032, 3, 1), D(2024, 3, 1), frequency=2)
    s = D(2026, 5, 7)
    annual = bond.yield_from_price(98.5, s)
    for basis in ("semiannual", "quarterly", "monthly"):
        y = bond.with_compounding(basis).yield_from_price(98.5, s)
        assert convert_yield(y, basis, "annual") == pytest.approx(annual, abs=1e-9)


# ---------------------------------------------------------------- holidays, record dates, ex-interest


def test_coupon_and_redemption_on_holidays(cal):
    bond = sebi_2025(calendar=cal)
    flows = bond.cash_flows(D(2024, 11, 25))
    assert [(f.date, f.payment_date, f.kind) for f in flows] == [
        (D(2024, 12, 14), D(2024, 12, 16), "coupon"), (D(2025, 12, 14), D(2025, 12, 12), "coupon"),
        (D(2025, 12, 14), D(2025, 12, 12), "redemption")]
    # The coupon's amount ignores the move; the next period starts on the due date (SEBI 2016).
    assert flows[0].amount == pytest.approx(8.95) and flows[1].accrual_start == D(2024, 12, 14)
    # Without a calendar nothing rolls.
    assert [f.payment_date for f in sebi_2025().cash_flows(D(2024, 11, 25))] == [D(2024, 12, 14), D(2025, 12, 14),
                                                                                D(2025, 12, 14)]


def test_record_dates_and_rolls(cal):
    bond = CorporateBond(9.0, D(2030, 12, 15), D(2023, 12, 15), calendar=cal)
    assert bond.record_date(D(2026, 12, 15)) == D(2026, 11, 30)
    # 15 days before 16 Aug 2025 is Fri 1 Aug; before 13 Oct 2024 it is Sat 28 Sep: preceding gives Fri 27 Sep,
    # following Mon 30 Sep.
    b = CorporateBond(8.0, D(2028, 10, 13), D(2023, 10, 13), calendar=cal)
    assert b.record_date(D(2024, 10, 13)) == D(2024, 9, 27)
    assert dataclasses.replace(b, record_roll="following").record_date(D(2024, 10, 13)) == D(2024, 9, 30)
    assert dataclasses.replace(b, record_days=7).record_date(D(2024, 10, 13)) == D(2024, 10, 4)
    assert dataclasses.replace(b, record_days=0).record_date(D(2024, 10, 13)) == D(2024, 10, 13)


def test_ex_interest_window():
    # T13: 9% annual 2023-2030; record date 30 Nov 2026 for the 15 Dec 2026 coupon; yield 8.5%.
    bond = CorporateBond(9.0, D(2030, 12, 15), D(2023, 12, 15))
    cases = [(D(2026, 11, 27), False, 8.556164, 110.172806), (D(2026, 11, 30), False, 8.630137, 110.246704),
             (D(2026, 12, 1), True, -0.345205, 101.299466), (D(2026, 12, 4), True, -0.271233, 101.367412),
             (D(2026, 12, 14), True, -0.024658, 101.594228)]
    for s, ex, accrued, dirty in cases:
        assert bond.is_ex_interest(s) is ex
        assert round(bond.accrued_interest(s), 6) == accrued
        assert round(bond.dirty_price(8.5, s), 6) == dirty
    # Identity: the ex dirty price is the cum dirty price less the coupon's present value.
    s = D(2026, 12, 1)
    cum = CorporateBond(9.0, D(2030, 12, 15), D(2023, 12, 15), record_days=0)
    pv_coupon = 9.0 / 1.085 ** ((D(2026, 12, 15) - s).days / 365)
    assert round(cum.dirty_price(8.5, s), 6) == 110.271348 and round(pv_coupon, 6) == 8.971882
    assert bond.dirty_price(8.5, s) == pytest.approx(cum.dirty_price(8.5, s) - pv_coupon, abs=1e-12)
    assert [f.date for f in bond.cash_flows(s)][0] == D(2027, 12, 15)
    assert bond.in_shut_period(s) is False


def test_record_date_never_before_allotment():
    # A first period shorter than the record offset: allotted 24 Oct, first coupon 1 Nov (8 days).
    bond = CorporateBond(8.55, D(2027, 4, 24), D(2025, 10, 24), frequency=12, schedule="month_start")
    assert bond.record_date(D(2025, 11, 1)) == D(2025, 10, 24)
    assert not bond.is_ex_interest(D(2025, 10, 24))  # the allottee is the first holder on record
    assert bond.is_ex_interest(D(2025, 10, 27))  # a buyer after allotment buys without that coupon
    assert bond.accrued_interest(D(2025, 10, 24)) == 0.0
    assert bond.yield_from_price(100.0, D(2025, 10, 24)) == pytest.approx(
        CorporateBond(8.55, D(2027, 4, 24), D(2025, 10, 24), frequency=12, schedule="month_start",
                      record_days=0).yield_from_price(100.0, D(2025, 10, 24)), abs=1e-12)


def test_redemption_record_window_is_refused():
    bond = CorporateBond(9.0, D(2030, 12, 15), D(2023, 12, 15))
    assert bond.dirty_price(8.5, D(2030, 11, 30)) > 0  # the record date itself is cum
    assert bond.in_shut_period(D(2030, 12, 1)) and not bond.in_shut_period(D(2030, 11, 30))
    with pytest.raises(ValueError, match="redemption record date 2030-11-30"):
        bond.dirty_price(8.5, D(2030, 12, 1))
    with pytest.raises(ValueError, match="on or after maturity"):
        bond.accrued_interest(D(2030, 12, 15))
    with pytest.raises(ValueError, match="before the issue date"):
        bond.accrued_interest(D(2023, 12, 14))


# ---------------------------------------------------------------- calls, puts and the SEBI rule


def _callable(**kw):
    options = kw.pop("options", (CallPut("call", D(2029, 1, 15)), CallPut("call", D(2030, 1, 15)),
                                 CallPut("call", D(2031, 1, 15)), CallPut("put", D(2028, 1, 15))))
    return CorporateBond(8.0, D(2034, 1, 15), D(2024, 1, 15), options=options, **kw)


def test_values_and_yields_to_each_workout():
    # T11, settlement 15 Jan 2027 (a coupon date: no accrued interest).
    bond, s = _callable(), D(2027, 1, 15)
    values = {(k, d): round(bond.to_workout(d, p).clean_price(7.0, s), 6) for k, d, p in bond.workouts(s)}
    assert values == {("put", D(2028, 1, 15)): 100.934579, ("call", D(2029, 1, 15)): 101.790534,
                      ("call", D(2030, 1, 15)): 102.606681, ("call", D(2031, 1, 15)): 103.369434,
                      ("maturity", D(2034, 1, 15)): 105.357690}
    for dirty, expected, worst in ((103.0, [4.85436893, 6.34690720, 6.85331477, 7.10685971, 7.42884612], 6.34690720),
                                   (97.0, [11.34020619, 9.70874574, 9.18042404, 8.91787145, 8.58085584], 8.58085584)):
        rows = bond.yields_to_workouts(s, dirty)
        assert [round(w.yield_pct, 8) for w in rows] == expected
        ytw = bond.yield_to_worst(s, dirty)
        assert round(ytw.yield_pct, 8) == worst
        assert all(ytw.yield_pct <= w.yield_pct + 1e-12 for w in rows if w.kind != "put")  # identity
    assert bond.yield_to_worst(s, 103.0, include_puts=True).kind == "put"
    assert bond.price_to_worst(s, 7.0)[1:] == (D(2029, 1, 15), "call")
    assert bond.price_to_best_put(s, 9.0)[1:] == (D(2028, 1, 15), "put")
    assert bond.price_to_worst(s, 9.0)[1:] == (D(2034, 1, 15), "maturity")


def test_workouts_drop_options_past_their_record_date():
    bond = _callable()
    assert [w[1] for w in bond.workouts(D(2027, 12, 31))][0] == D(2028, 1, 15)
    assert [w[1] for w in bond.workouts(D(2028, 1, 1))][0] == D(2029, 1, 15)  # the put's record date has passed
    declined = _callable(options=(CallPut("call", D(2029, 1, 15), exercised=False,
                                          exercised_public_from="2028-12-01"),))
    # a decision not to call is used only from when it was public (the build dropped the call at any date)
    assert [w[0] for w in declined.workouts(D(2027, 1, 15))] == ["call", "maturity"]  # no as_of: no decision used
    assert [w[0] for w in declined.workouts(D(2027, 1, 15), as_of="2027-01-14T00:00")] == ["call", "maturity"]
    assert [w[0] for w in declined.workouts(D(2028, 12, 5), as_of="2028-12-02T00:00")] == ["maturity"]


def test_sebi_valuation_rule():
    # T12.
    s = D(2027, 1, 15)
    v = _callable().sebi_valuation(s, 7.0)
    assert (v.kind, v.date, v.call_trigger, v.put_trigger, round(v.clean_price, 6)) == (
        "call", D(2029, 1, 15), D(2029, 1, 15), None, 101.790534)
    v = _callable().sebi_valuation(s, 9.0)
    assert (v.kind, v.date, v.call_trigger, v.put_trigger, round(v.clean_price, 6)) == (
        "put", D(2028, 1, 15), None, D(2028, 1, 15), 99.082569)
    assert v.put_favours_holder  # 9% is more than 30 bp above the 8% coupon
    two = _callable(options=(CallPut("call", D(2029, 1, 15), 102), CallPut("call", D(2031, 1, 15), 101),
                             CallPut("put", D(2030, 1, 15))))
    for y, kind, when, value in ((7.5, "call", D(2031, 1, 15), 102.404646), (8.0, "put", D(2030, 1, 15), 99.980479),
                                 (8.25, "put", D(2030, 1, 15), 99.338649), (8.5, "put", D(2030, 1, 15), 98.702574),
                                 (9.0, "put", D(2030, 1, 15), 97.447428)):
        v = two.sebi_valuation(s, y)
        assert (v.kind, v.date, round(v.clean_price, 6)) == (kind, when, value)
    both = _callable(options=(CallPut("put", D(2028, 1, 15), 106), CallPut("call", D(2031, 1, 15))))
    v = both.sebi_valuation(s, 7.0)
    assert (v.call_trigger, v.put_trigger, v.date, round(v.clean_price, 6)) == (D(2031, 1, 15), D(2028, 1, 15),
                                                                                D(2028, 1, 15), 106.542056)
    same = _callable(options=(CallPut("put", D(2029, 1, 15)), CallPut("call", D(2029, 1, 15))))
    for y, value in ((7.0, 101.790534), (9.0, 98.219429)):
        v = same.sebi_valuation(s, y)
        assert (v.kind, v.date, round(v.clean_price, 6)) == ("deemed_maturity", D(2029, 1, 15), value)
    assert _callable(options=()).sebi_valuation(s, 7.0).kind == "maturity"


def test_workout_between_coupon_dates_pays_interest_to_it():
    bond = CorporateBond(8.0, D(2034, 1, 15), D(2024, 1, 15), options=(CallPut("call", D(2029, 7, 15), 101),))
    w = bond.to_workout(D(2029, 7, 15))
    last = w.periods()[-1]
    assert (last.start, last.due, w.redemption) == (D(2029, 1, 15), D(2029, 7, 15), 101.0)
    assert last.amount == pytest.approx(8.0 * 181 / 365)
    assert w.anchor == D(2024, 1, 15) and w.options == ()


def test_perpetual_and_at1():
    bond = perpetual_bond("2026-07-15", 7.75, [D(2031, 7, 15), (D(2032, 7, 15), 100)])
    assert (bond.maturity, bond.perpetual, bond.tier) == (D(2126, 7, 15), True, "at1")
    v = bond.sebi_valuation("2026-11-20", 8.5)
    assert (v.kind, v.date) == ("first_call", D(2031, 7, 15))
    assert v.clean_price == pytest.approx(bond.to_workout(D(2031, 7, 15)).clean_price(8.5, "2026-11-20"))
    tier2 = perpetual_bond("2026-07-15", 7.75, [D(2031, 7, 15)], tier="tier2")
    assert tier2.sebi_valuation("2026-11-20", 7.0).kind == "call"  # the call rule, not the first call by default
    assert tier2.sebi_valuation("2026-11-20", 9.0).kind == "maturity"


def test_exercise_counts_from_its_notice():
    bond = _callable(options=(CallPut("call", D(2029, 1, 15), 100, exercised=True,
                                      exercised_public_from="2028-12-10"),))
    assert bond.effective_bond("2028-12-10T12:00").maturity == D(2034, 1, 15)  # a bare date counts from 23:59:59
    assert bond.effective_bond("2028-12-11T09:00").maturity == D(2029, 1, 15)
    with pytest.raises(ValueError, match="exercised_public_from"):
        CallPut("call", D(2029, 1, 15), exercised=True)
    partial = _callable(options=(CallPut("call", D(2029, 1, 15), 100, portion=0.5, exercised=True,
                                         exercised_public_from="2028-12-10T10:00"),))
    with pytest.raises(NotImplementedError, match="partial exercise"):
        partial.effective_bond("2028-12-11")


# ---------------------------------------------------------------- steps and amortisation


def test_rating_step_up():
    # T15: 8.50% from 1 Jun 2026, +25 bp a notch below AA; AA- public on 10 Mar 2027 at 18:00.
    bond = CorporateBond(8.50, D(2031, 6, 1), D(2026, 6, 1))
    ratings = pd.DataFrame({"agency": ["CRISIL", "CRISIL"], "rating": ["CRISIL AA/Stable", "CRISIL AA-/Stable"],
                            "public_from": ["2026-05-20T10:00:00", "2027-03-10T18:00:00"]})
    event = apply_rating_steps(bond, ratings, StepRule("AA", 25, effective="from_event"), as_of="2027-06-30T20:00")
    assert event.rate_steps[0][:2] == (D(2027, 3, 11), 8.75)
    assert event.periods()[0].amount == pytest.approx(8.5561643836, abs=1e-10)
    assert event.periods()[0].amount == pytest.approx(8.50 * 283 / 365 + 8.75 * 82 / 365)
    nxt = apply_rating_steps(bond, ratings, StepRule("AA", 25), as_of="2027-06-30T20:00")
    assert [round(p.amount, 10) for p in nxt.periods()[:2]] == [8.5, 8.75]
    # Accrual inside a stepped period is piecewise.
    assert event.accrued_interest(D(2027, 4, 1)) == pytest.approx(8.50 * 283 / 365 + 8.75 * 21 / 365)
    # Not before the action was public.
    assert apply_rating_steps(bond, ratings, StepRule("AA", 25), as_of="2027-03-10T17:59").rate_steps == ()
    upgrade = pd.concat([ratings, pd.DataFrame({"agency": ["CRISIL"], "rating": ["CRISIL AA/Stable"],
                                                "public_from": ["2028-01-05"]})])
    back = apply_rating_steps(bond, upgrade, StepRule("AA", 25), as_of="2029-01-01")
    assert [s[:2] for s in back.rate_steps] == [(D(2027, 6, 1), 8.75), (D(2028, 6, 1), 8.5)]
    kept = apply_rating_steps(bond, upgrade, StepRule("AA", 25, step_down=False), as_of="2029-01-01")
    assert [s[:2] for s in kept.rate_steps] == [(D(2027, 6, 1), 8.75)]
    capped = apply_rating_steps(bond, pd.DataFrame({"agency": ["ICRA"], "rating": ["[ICRA]A (Negative)"],
                                                   "public_from": ["2027-03-10T18:00"]}),
                                StepRule("AA", 25, max_step_bp=50), as_of="2028-01-01")
    assert capped.rate_steps[0][1] == pytest.approx(9.0)  # three notches, capped at 50 bp
    with pytest.raises(ValueError, match="public_from is missing"):
        apply_rating_steps(bond, ratings.assign(public_from=["2026-05-20", ""]), StepRule("AA", 25),
                           as_of="2028-01-01")


def test_combined_ratings():
    bond = CorporateBond(8.0, D(2031, 6, 1), D(2026, 6, 1))
    ratings = pd.DataFrame({"agency": ["CRISIL", "ICRA", "CARE"],
                            "rating": ["CRISIL AA-", "[ICRA]AA", "CARE A+"],
                            "public_from": ["2026-07-01", "2026-07-02", "2026-07-03"]})
    # Below AA+: AA- is two notches, AA one, A+ three.
    for combine, step in (("worst", 0.75), ("best", 0.25), ("second_best", 0.50)):
        b = apply_rating_steps(bond, ratings, StepRule("AA+", 25, combine=combine), as_of="2026-12-31")
        assert b.rate_steps[-1][1] == pytest.approx(8.0 + step)


def test_amortising_bond():
    # T14: 9% annual 2026-2031, principal 30/30/40 on 1 Apr 2029, 2030 and 2031.
    bond = CorporateBond(9.0, D(2031, 4, 1), D(2026, 4, 1),
                         amortisation=((D(2029, 4, 1), 30), (D(2030, 4, 1), 30), (D(2031, 4, 1), 40)))
    assert [round(p.amount + p.principal, 6) for p in bond.periods()] == [9.0, 9.0, 39.0, 36.3, 43.6]
    assert [p.kind for p in bond.periods()] == ["coupon", "coupon", "principal", "principal", "redemption"]
    s = D(2029, 10, 1)
    assert round(bond.accrued_interest(s), 6) == 3.158630
    assert round(bond.dirty_price(9.0, s), 6) == 73.090774
    clean = bond.clean_price(9.0, s)
    assert round(clean, 6) == 69.932143 and round(clean / (bond.outstanding_face(s) / 100), 6) == 99.903062
    assert bond.dirty_price(9.0, D(2026, 4, 1)) == pytest.approx(99.9783417058, abs=1e-10)  # 2028 has 366 days
    # After a principal's record date the bond trades ex-principal: that payment goes to the seller.
    assert bond.outstanding_face(D(2029, 3, 20)) == 70.0 and bond.outstanding_face(D(2029, 3, 10)) == 100.0
    assert bond.cash_flows(D(2029, 3, 20))[0].date == D(2030, 4, 1)
    same = CorporateBond(9.0, D(2031, 4, 1), D(2026, 4, 1), amortisation=((D(2029, 4, 1), 30), (D(2030, 4, 1), 30)))
    assert same.periods() == bond.periods()


def test_refusals():
    base = dict(coupon_pct=9.0, maturity=D(2031, 4, 1), issue_date=D(2026, 4, 1))
    cases = [
        (dict(schedule="explicit", due_dates=(D(2027, 4, 1), D(2027, 3, 1), D(2031, 4, 1))), "must increase"),
        (dict(schedule="explicit", due_dates=(D(2027, 4, 1), D(2030, 4, 1))), "not the maturity"),
        (dict(schedule="explicit", due_dates=(D(2026, 3, 1), D(2031, 4, 1))), "not after issue"),
        (dict(amortisation=((D(2029, 4, 1), 30), (D(2031, 4, 1), 60))), "sums to 90"),
        (dict(amortisation=((D(2029, 4, 1), 70), (D(2030, 4, 1), 40))), "redeems 110"),
        (dict(amortisation=((D(2029, 5, 1), 30),)), "not a due date"),
        (dict(frequency=0), "frequency 0"),
        (dict(frequency=3), "frequency must be one of"),
        (dict(schedule="month_start"), "frequency must be 12"),
        (dict(schedule="explicit", due_dates=(D(2031, 4, 1),), first_period="clubbed"), "clubbed"),
        (dict(frequency=2, schedule="backward", first_coupon_date=D(2026, 8, 1)), "not on the schedule"),
        (dict(options=(CallPut("call", D(2031, 4, 1)),)), "not between issue"),
        (dict(options=(CallPut("call", D(2029, 4, 1)), CallPut("call", D(2029, 4, 1), 101))), "listed twice"),
        (dict(payment_dates=(D(2027, 4, 2),)), "explicit schedule"),
        (dict(rate_steps=((D(2026, 1, 1), 9.5),)), "before issue"),
        (dict(rate_steps=((D(2027, 1, 1), 9.5), (D(2027, 1, 1), 9.6))), "two rate steps"),
        (dict(redemption=0.0), "redemption"),
        (dict(leap_year_rule="isda"), "leap_year_rule"),
        (dict(record_roll="nearest"), "record_roll"),
    ]
    for kw, match in cases:
        with pytest.raises(ValueError, match=match):
            CorporateBond(**(base | kw))
    with pytest.raises(ValueError, match="not before maturity"):
        CorporateBond(9.0, D(2026, 4, 1), D(2026, 4, 1))
    with pytest.raises(ValueError, match="kind"):
        CallPut("conversion", D(2029, 1, 1))
    with pytest.raises(ValueError, match="threshold"):
        StepRule("ZZ", 25)


def test_explicit_schedule_with_payment_and_record_dates():
    dues = (D(2027, 4, 1), D(2028, 4, 1), D(2029, 4, 1))
    pays = (D(2027, 4, 1), D(2028, 4, 3), D(2029, 3, 30))
    records = (D(2027, 3, 17), D(2028, 3, 17), D(2029, 3, 16))
    bond = CorporateBond(9.0, D(2029, 4, 1), D(2026, 4, 1), schedule="explicit", due_dates=dues, payment_dates=pays,
                         record_dates=records)
    assert [p.payment for p in bond.periods()] == list(pays)
    assert bond.record_date(D(2028, 4, 1)) == D(2028, 3, 17)
    assert bond.is_ex_interest(D(2028, 3, 18)) and not bond.is_ex_interest(D(2028, 3, 17))
    with pytest.raises(ValueError, match="2 payment_dates for 3"):
        dataclasses.replace(bond, payment_dates=pays[:2])


# ---------------------------------------------------------------- term tables and batch pricing


TERMS = pd.DataFrame([
    {"isin": "INE000X01011", "kind": "corporate", "coupon_pct": "8.0", "frequency": "1", "issue_date": "2024-01-15",
     "maturity_date": "2034-01-15", "name": "callable"},
    {"isin": "INE000X01029", "kind": "corporate", "coupon_pct": "9.0", "frequency": "1", "issue_date": "2026-04-01",
     "maturity_date": "2031-04-01"},
    {"isin": "INE000X01037", "kind": "perpetual", "coupon_pct": "7.75", "frequency": "1", "issue_date": "2026-07-15",
     "maturity_date": "", "tier": "at1"},
    {"isin": "INE000X01045", "kind": "corporate", "coupon_pct": "8.95", "frequency": "1", "issue_date": "2013-11-13",
     "maturity_date": "2018-11-13", "conventions": "ncd_2013"},
])
OPTIONS = pd.DataFrame([{"isin": "INE000X01011", "kind": "call", "date": "2029-01-15"},
                        {"isin": "INE000X01011", "kind": "put", "date": "2028-01-15", "price_per100": "100"},
                        {"isin": "INE000X01037", "kind": "call", "date": "2031-07-15"}])
AMORT = pd.DataFrame([{"isin": "INE000X01029", "date": "2029-04-01", "principal_per100": "30"},
                      {"isin": "INE000X01029", "date": "2030-04-01", "principal_per100": "30"}])


def test_term_tables_refuse_conflicting_repeats():
    doubled = pd.concat([TERMS, TERMS.iloc[[1]]])
    assert len(read_corporate_terms(doubled)) == 4  # an exact repeat is kept once
    conflict = pd.concat([TERMS, TERMS.iloc[[1]].assign(coupon_pct="9.5")])
    with pytest.raises(ValueError, match=r"given twice with different values .*coupon_pct"):
        read_corporate_terms(conflict)
    with pytest.raises(ValueError, match="kind must be one of"):
        read_corporate_terms(TERMS.assign(kind="loan"))
    with pytest.raises(ValueError, match="needs columns"):
        read_corporate_terms(TERMS.drop(columns="issue_date"))
    assert len(read_corporate_options(pd.concat([OPTIONS, OPTIONS]))) == 3


def test_corporate_from_terms():
    rows = {r["isin"]: r for r in read_corporate_terms(TERMS).to_dict("records")}
    bond = corporate_from_terms(rows["INE000X01011"], options=OPTIONS)
    assert [o.kind for o in bond.options] == ["put", "call"] and bond.conventions is NCD
    assert bond.calendar is not None and not bond.calendar.strict  # the payment calendar, lenient beyond its years
    perp = corporate_from_terms(rows["INE000X01037"], options=OPTIONS)
    assert perp.maturity == D(2126, 7, 15) and perp.perpetual and perp.tier == "at1"
    legacy = corporate_from_terms(rows["INE000X01045"], calendar=None)
    assert legacy.conventions is NCD_2013
    schedule = pd.DataFrame([
        {"isin": "INE000X01029", "due_date": "2027-04-01", "kind": "coupon", "amount_per100": "9.0"},
        {"isin": "INE000X01029", "due_date": "2028-04-01", "kind": "coupon"},
        {"isin": "INE000X01029", "due_date": "2029-04-01", "kind": "principal", "amount_per100": "30"},
        {"isin": "INE000X01029", "due_date": "2029-04-01", "kind": "coupon", "amount_per100": "9.0"},
        {"isin": "INE000X01029", "due_date": "2030-04-01", "kind": "principal", "amount_per100": "30"},
        {"isin": "INE000X01029", "due_date": "2031-04-01", "kind": "redemption", "amount_per100": "40"},
    ])
    explicit = corporate_from_terms(rows["INE000X01029"], schedule=read_corporate_schedule(schedule), calendar=None)
    assert explicit.schedule == "explicit" and explicit.amortisation == ((D(2029, 4, 1), 30.0), (D(2030, 4, 1), 30.0))
    wrong = schedule.copy()
    wrong.loc[1, "amount_per100"] = "9.02"
    with pytest.raises(ValueError, match="the rules give 9.00000000"):  # an annual coupon is the full rate
        corporate_from_terms(rows["INE000X01029"], schedule=wrong, calendar=None)
    with pytest.raises(ValueError, match="INE000X01029: .*maturity_date is required"):
        corporate_from_terms(rows["INE000X01029"] | {"maturity_date": ""})


def test_price_corporates_batch_and_determinism():
    prices = pd.DataFrame([
        {"isin": "INE000X01011", "settlement_date": "2027-01-15", "yield_pct": 7.0},
        {"isin": "INE000X01011", "settlement_date": "2027-01-15", "yield_pct": 9.0},
        {"isin": "INE000X01029", "settlement_date": "2029-10-01", "yield_pct": 9.0},
        {"isin": "INE000X01037", "settlement_date": "2026-11-20", "yield_pct": 8.5},
        {"isin": "INE999X99999", "settlement_date": "2026-11-20", "yield_pct": 8.5},
    ])
    sebi = price_corporates(prices, TERMS, options=OPTIONS, amortisation=AMORT, given="yield_pct", valuation="sebi")
    assert sebi["clean_price"].round(6).tolist()[:3] == [101.790534, 99.082569, 69.932143]
    assert sebi["workout_kind"].tolist()[:4] == ["call", "put", "maturity", "first_call"]
    assert sebi["error"].tolist()[4] == "INE999X99999: no terms"
    assert set(PRICED_COLUMNS) <= set(sebi.columns)
    ytm = price_corporates(prices, TERMS, options=OPTIONS, amortisation=AMORT, given="yield_pct")
    assert ytm["clean_price"].round(6).tolist()[:2] == [105.357690, 94.931291]
    worst = price_corporates(pd.DataFrame([{"isin": "INE000X01011", "settlement_date": "2027-01-15",
                                            "clean_price": 103.0}]), TERMS, options=OPTIONS, valuation="worst")
    assert round(worst["yield_pct"].iloc[0], 8) == 6.34690720 and worst["workout_date"].iloc[0] == D(2029, 1, 15)
    shuffled = TERMS.sample(frac=1.0, random_state=random.Random(7).randint(0, 999))
    again = price_corporates(prices, shuffled, options=OPTIONS.iloc[::-1], amortisation=AMORT, given="yield_pct",
                             valuation="sebi")
    assert again.to_csv(index=False) == sebi.to_csv(index=False)
    from cr_strat.corporates import _price_row
    with pytest.raises(ValueError, match="SEBI valuation works from a yield"):
        _price_row(_callable(), _callable(), D(2027, 1, 15), "clean_price", 100.0, "sebi", "original")
    from_price = price_corporates(pd.DataFrame([{"isin": "INE000X01011", "settlement_date": "2027-01-15",
                                                 "clean_price": 100.0}]), TERMS, options=OPTIONS, valuation="sebi")
    assert "SEBI valuation works from a yield" in from_price["error"].iloc[0]


def test_same_inputs_same_bytes():
    first = sebi_2025(leap_year_rule="calendar").cash_schedule().to_csv(index=False)
    assert first == sebi_2025(leap_year_rule="calendar").cash_schedule().to_csv(index=False)
    assert repr(_callable().sebi_valuation(D(2027, 1, 15), 7.5)) == repr(_callable().sebi_valuation(D(2027, 1, 15),
                                                                                                   7.5))


def test_public_time_rules():
    assert public_time("2027-03-10") == datetime(2027, 3, 10, 23, 59, 59)
    assert public_time(D(2027, 3, 10)) == datetime(2027, 3, 10, 23, 59, 59)
    assert public_time("2027-03-10T18:00") == datetime(2027, 3, 10, 18, 0)
    assert public_time("2027-03-10T12:30:00+00:00") == datetime(2027, 3, 10, 18, 0)  # India time
    assert public_time("") is None and public_time(None) is None and public_time(math.nan) is None
    with pytest.raises(ValueError, match="ISO"):
        public_time("10/03/2027 18:00")


# ---------------------------------------------------------------- command line


def _cli_fields(text: str) -> dict:
    import re
    rows = [re.split(r"\s{2,}", line.strip(), maxsplit=1) for line in text.splitlines() if line.strip()]
    return {r[0]: r[1] for r in rows if len(r) == 2}


def test_corp_price_command(capsys, tmp_path):
    from cr_strat.cli import main
    args = ["corp-price", "--coupon", "8.95", "--issue-date", "2023-12-14", "--maturity", "2030-12-14",
            "--settlement", "2026-11-16"]
    assert main([*args, "--price", "102.2591928939", "--cash-flows"]) == 0
    text = capsys.readouterr().out
    out = _cli_fields(text)
    assert float(out["yield (%)"]) == pytest.approx(8.261226, abs=1e-6)
    assert float(out["accrued interest"]) == pytest.approx(8.263425, abs=1e-6)
    assert out["record date"] == "2026-11-27"  # 29 Nov is a Sunday: the preceding business day
    assert "2030-12-14  2030-12-13  redemption" in text  # a Saturday maturity paid the day before
    assert main(["corp-price", "--coupon", "8.0", "--issue-date", "2024-01-15", "--maturity", "2034-01-15",
                 "--settlement", "2027-01-15", "--yield", "7", "--call", "2029-01-15", "--call", "2030-01-15:101",
                 "--put", "2028-01-15", "--valuation", "sebi"]) == 0
    out = _cli_fields(capsys.readouterr().out)
    assert out["valued to"].startswith("call 2029-01-15") and float(out["clean price"]) == pytest.approx(101.790534)
    terms, options, prices = tmp_path / "terms.csv", tmp_path / "options.csv", tmp_path / "prices.csv"
    TERMS.to_csv(terms, index=False)
    OPTIONS.to_csv(options, index=False)
    pd.DataFrame([{"isin": "INE000X01011", "settlement_date": "2027-01-15", "yield_pct": 9.0}]).to_csv(prices,
                                                                                                      index=False)
    assert main(["corp-price", "--terms", str(terms), "--isin", "INE000X01011", "--options", str(options),
                 "--settlement", "2027-01-15", "--price", "103", "--valuation", "worst"]) == 0
    out = _cli_fields(capsys.readouterr().out)
    assert float(out["yield (%)"]) == pytest.approx(6.346907, abs=1e-6) and out["valued to"] == "call 2029-01-15"
    result = tmp_path / "out.csv"
    assert main(["corp-price", "--terms", str(terms), "--options", str(options), "--prices", str(prices),
                 "--valuation", "sebi", "--out", str(result)]) == 0
    capsys.readouterr()
    assert round(pd.read_csv(result)["clean_price"].iloc[0], 6) == 99.082569
    for bad, message in ((["--price", "100"], "--settlement is required"),
                         (["--settlement", "2026-11-16"], "exactly one of --yield and --price"),
                         (["--settlement", "2030-12-05", "--price", "100"], "redemption record date")):
        assert main(["corp-price", "--coupon", "8.95", "--issue-date", "2023-12-14", "--maturity", "2030-12-14",
                     *bad]) == 2
        assert message in capsys.readouterr().err
    assert main(["corp-price", "--terms", str(terms), "--isin", "INE404X0000", "--settlement", "2027-01-15",
                 "--price", "100"]) == 2
    assert "not in" in capsys.readouterr().err
