"""Tests of ois: MIBOR-OIS conventions, schedules, compounding, the bootstrapped curve and swap valuation.

Synthetic quotes and fixings only, plus the handbook's worked examples typed in with their citation (FIMMDA Handbook
of Market Practices 2026, 6.10.2 and 6.12.3). Vectors B1-B6 are the analytics spec's of 19 Nov 2026.
"""

import json
import math
import random
from dataclasses import replace
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from cr_strat import ois
from cr_strat.calendars import CalendarCoverageError, mumbai_settlement_calendar
from cr_strat.cli import main
from cr_strat.curvefit import DiscountCurveLike
from cr_strat.fbil import load_fbil_money_market

CAL = mumbai_settlement_calendar()
LENIENT = CAL.lenient()
TRADE = date(2026, 11, 19)
EFFECTIVE = date(2026, 11, 20)
B3_QUOTES = {"1M": 5.62, "2M": 5.66, "3M": 5.70, "6M": 5.78, "9M": 5.84, "1Y": 5.88, "2Y": 5.98, "3Y": 6.06,
             "4Y": 6.12, "5Y": 6.18}
B3_NODES = {"1M": (date(2026, 12, 21), 0.995096851659), "2M": (date(2027, 1, 20), 0.990477495858),
            "3M": (date(2027, 2, 22), 0.985381734944), "6M": (date(2027, 5, 20), 0.971987053251),
            "9M": (date(2027, 8, 20), 0.958001110377), "1Y": (date(2027, 11, 22), 0.944033282125),
            "2Y": (date(2028, 11, 20), 0.888455028097), "3Y": (date(2029, 11, 20), 0.835541655439),
            "4Y": (date(2030, 11, 20), 0.785106654642), "5Y": (date(2031, 11, 20), 0.736735601037)}


@pytest.fixture(scope="module")
def b3():
    return ois.bootstrap_ois(B3_QUOTES, TRADE, 5.60, calendar=LENIENT, source="synthetic B3")


# ---------------------------------------------------------------- conventions and tenors


def test_conventions_record_the_handbook_rules_and_refuse_what_is_not_built():
    c = ois.MIBOR_OIS
    assert (c.index, c.day_count, c.effective_lag, c.roll, c.single_period_max_months, c.period_months) == (
        "FBIL-MIBOR", "ACT/365F", 1, "modified_following", 12, 6)
    assert (c.rate_decimals, c.amount_decimals, c.net_decimals, c.payment_lag, c.rate_cutoff) == (4, 2, 0, 0, 0)
    assert ois.SORR_OIS.index == "FBIL-SORR" and ois.SORR_OIS.period_months == 6
    with pytest.raises(ValueError, match="no calendar named 'new_york'"):
        ois.ois_schedule(EFFECTIVE, 12, ois.FCS_MIBOR_OIS, LENIENT)
    with pytest.raises(NotImplementedError, match="end-of-month rule"):
        replace(c, eom=True)
    with pytest.raises(NotImplementedError, match="fixing lag"):
        replace(c, fixing_lag=1)
    with pytest.raises(ValueError, match="roll must be one of"):
        replace(c, roll="nearest")
    with pytest.raises(ValueError, match="period_months must be one of"):
        replace(c, period_months=5)
    with pytest.raises(ValueError, match="rate cut-off"):
        ois.compound_overnight({}, EFFECTIVE, date(2026, 11, 27), conventions=replace(c, rate_cutoff=1))


@pytest.mark.parametrize("name, months", [("1M", 1), ("9m", 9), ("12M", 12), ("1Y", 12), ("5Y", 60),
                                          ("1 Month", 1), ("12 Months", 12), ("1 Year", 12), ("5 Years", 60),
                                          ("18M", 18)])
def test_tenor_months_reads_fbil_and_short_names(name, months):
    assert ois.tenor_months(name) == months


@pytest.mark.parametrize("name", ["7 Days", "1W", "0M", "two years", "", "5"])
def test_tenor_months_refuses_what_is_not_a_month_or_year(name):
    with pytest.raises(ValueError, match="tenor"):
        ois.tenor_months(name)


def test_tenor_labels():
    assert [ois.tenor_label(m) for m in (1, 9, 12, 18, 24, 60)] == ["1M", "9M", "1Y", "18M", "2Y", "5Y"]


def test_fixing_regime_marks_mibors_history():
    assert ois.fixing_regime(date(2015, 7, 21))["regime"] == "polled"
    old = ois.fixing_regime(date(2020, 3, 2))
    assert (old["window"], old["published_by"]) == ("09:00-10:00", "10:45")
    new = ois.fixing_regime(date(2025, 8, 4))
    assert (new["window"], new["published_by"]) == ("09:00-12:00", "12:45")
    assert date(2017, 8, 16) in ois.MIBOR_BREAKS


# ---------------------------------------------------------------- schedules


def test_effective_date_is_t_plus_one_business_day_never_a_saturday():
    assert ois.effective_date(TRADE, calendar=CAL) == EFFECTIVE
    assert ois.effective_date(date(2026, 11, 20), calendar=CAL) == date(2026, 11, 23)  # Friday to Monday
    assert ois.effective_date(date(2026, 11, 23), calendar=CAL) == date(2026, 11, 25)  # 24 Nov: Guru Nanak Jayanti


def test_schedule_single_period_up_to_a_year_and_semiannual_beyond():
    one = ois.ois_schedule(EFFECTIVE, "1Y", calendar=LENIENT)
    assert len(one) == 1 and one[0].end == date(2027, 11, 22) and one[0].unadjusted_end == date(2027, 11, 20)
    assert one[0].days == 367 and one[0].year_fraction == pytest.approx(367 / 365, abs=0)
    two = ois.ois_schedule(EFFECTIVE, "2Y", calendar=LENIENT)
    assert [p.end for p in two] == [date(2027, 5, 20), date(2027, 11, 22), date(2028, 5, 22), date(2028, 11, 20)]
    assert [p.start for p in two] == [EFFECTIVE, date(2027, 5, 20), date(2027, 11, 22), date(2028, 5, 22)]
    assert all(p.payment == p.end for p in two)


def test_schedule_rolls_on_the_effective_day_clipped_to_month_end_modified_following():
    periods = ois.ois_schedule(date(2026, 8, 31), "2Y", calendar=LENIENT)
    # 28 Feb 2027 is a Sunday: modified following stays in February
    assert [p.end for p in periods] == [date(2027, 2, 26), date(2027, 8, 31), date(2028, 2, 29), date(2028, 8, 31)]


def test_schedule_refusals():
    with pytest.raises(CalendarCoverageError, match="2027"):  # B6: the strict calendar ends with 2026
        ois.ois_schedule(EFFECTIVE, 12, calendar=CAL)
    with pytest.raises(ValueError, match="not a whole number of 6-month periods"):
        ois.ois_schedule(EFFECTIVE, "15M", calendar=LENIENT)
    with pytest.raises(ValueError, match="not a business day"):
        ois.ois_schedule(date(2026, 11, 21), 3, calendar=LENIENT)


# ---------------------------------------------------------------- compounding


HANDBOOK_FIXINGS = {date(2015, 12, 15): 6.99, date(2015, 12, 16): 6.85, date(2015, 12, 17): 7.10,
                    date(2015, 12, 18): 7.03, date(2015, 12, 21): 6.93}


def test_handbook_one_week_ois_b1():
    # FIMMDA Handbook 2026, 6.10.2 worked example: Rs 25 crore, fixings of 15-21 Dec 2015, Friday 18 Dec for 3 days
    c = ois.compound_overnight(HANDBOOK_FIXINGS, date(2015, 12, 15), date(2015, 12, 22), calendar=CAL)
    assert (c.days, c.business_days) == (7, 5)
    assert c.rate_pct == pytest.approx(6.9977269799, abs=1e-10) and c.rate_rounded_pct == 6.9977
    plain = ois.settlement_amounts(25_00_00_000, 6.80, c, conventions=replace(ois.MIBOR_OIS, rate_decimals=None))
    assert plain["floating_amount"] == 335507.46 and plain["fixed_amount"] == 326027.40
    assert plain["net_settlement"] == 9480 and plain["payer"] == "fixed receiver"  # the handbook's Rs 9,480
    text = ois.settlement_amounts(25_00_00_000, 6.80, c)  # the handbook's text: rate rounded to 0.0001% first
    assert text["floating_amount"] == 335506.16 and text["net_settlement"] == 9479


def test_handbook_fra_act365_arithmetic_b2():
    # FIMMDA Handbook 2026, 6.12.3: Rs 10 crore, 91 days, 9.00% against 8.50%, amounts to the rupee, settled on the
    # start date discounted at 8.50% for 91 days. One fixing held 91 days is simple Act/365 interest.
    start = date(2024, 1, 1)
    c = ois.compound_overnight({start: 8.50}, start, start + timedelta(days=91), calendar=CAL, missing="holiday")
    assert c.factor == pytest.approx(1 + 0.085 * 91 / 365, rel=1e-15)
    rupees = replace(ois.MIBOR_OIS, rate_decimals=None, amount_decimals=0)
    s = ois.settlement_amounts(10_00_00_000, 9.00, c, conventions=rupees)
    assert (s["fixed_amount"], s["floating_amount"], s["net_settlement"]) == (2243836, 2119178, 124658)
    assert s["net_settlement"] / c.factor == pytest.approx(122071.10, abs=0.005)


def test_weekends_and_holidays_accrue_at_the_previous_fixing():
    fix = {date(2026, 11, 20): 6.0, date(2026, 11, 23): 7.0, date(2026, 11, 25): 8.0}  # 24 Nov a holiday
    c = ois.compound_overnight(fix, date(2026, 11, 20), date(2026, 11, 26), calendar=CAL)
    expected = (1 + 6.0 * 3 / 36500) * (1 + 7.0 * 2 / 36500) * (1 + 8.0 / 36500)
    assert c.factor == pytest.approx(expected, rel=1e-15) and c.business_days == 3


def test_missing_fixings_raise_or_follow_the_handbook_rules():
    fix = {date(2026, 11, 16): 5.0, date(2026, 11, 18): 6.0}  # 17 Nov missing
    with pytest.raises(ValueError, match="no fixing for 1 business day.*2026-11-17"):
        ois.compound_overnight(fix, date(2026, 11, 16), date(2026, 11, 19), calendar=CAL)
    holiday = ois.compound_overnight(fix, date(2026, 11, 16), date(2026, 11, 19), calendar=CAL, missing="holiday")
    assert holiday.factor == pytest.approx((1 + 5.0 * 2 / 36500) * (1 + 6.0 / 36500), rel=1e-15)
    previous = ois.compound_overnight(fix, date(2026, 11, 16), date(2026, 11, 19), calendar=CAL, missing="previous")
    assert previous.factor == pytest.approx((1 + 5.0 / 36500) ** 2 * (1 + 6.0 / 36500), rel=1e-15)
    assert holiday.missing == previous.missing == (date(2026, 11, 17),)
    with pytest.raises(ValueError, match="first business day"):
        ois.compound_overnight({date(2026, 11, 17): 5.0}, date(2026, 11, 16), date(2026, 11, 18), calendar=CAL,
                               missing="holiday")


def test_saturday_fixings_are_ignored_and_listed():
    fix = {date(2026, 11, 20): 6.0, date(2026, 11, 21): 9.9, date(2026, 11, 23): 6.0}
    c = ois.compound_overnight(fix, date(2026, 11, 20), date(2026, 11, 24), calendar=CAL)
    assert c.ignored == (date(2026, 11, 21),)
    assert c.factor == pytest.approx((1 + 6.0 * 3 / 36500) * (1 + 6.0 / 36500), rel=1e-15)


def test_compounding_refusals():
    with pytest.raises(ValueError, match="not a business day"):
        ois.compound_overnight({}, date(2026, 11, 21), date(2026, 11, 24), calendar=CAL)
    with pytest.raises(ValueError, match="must be after start"):
        ois.compound_overnight({}, EFFECTIVE, EFFECTIVE, calendar=CAL)
    with pytest.raises(ValueError, match="two different fixings"):
        ois.compound_overnight({"2026-11-20": 6.0, date(2026, 11, 20): 6.1}, EFFECTIVE, date(2026, 11, 23),
                               calendar=CAL)
    with pytest.raises(TypeError, match="mapping of dates"):
        ois.compound_overnight([6.0], EFFECTIVE, date(2026, 11, 23), calendar=CAL)


# ---------------------------------------------------------------- the curve


def test_bootstrap_reproduces_the_spec_curve_b3(b3):
    nodes = b3.nodes().set_index("label")
    for label, (end, df) in B3_NODES.items():
        assert nodes.loc[label, "end_date"] == end
        assert nodes.loc[label, "discount"] == pytest.approx(df, abs=1e-10)
    assert float(b3.zero_rate(b3.year_fraction(date(2031, 11, 20)))) == pytest.approx(6.103835, abs=1e-6)
    assert nodes["error_bp"].abs().max() < 1e-8  # every quote reprices (bp)
    assert b3.calendar_projected and b3.effective == EFFECTIVE and b3.t_max == pytest.approx(1827 / 365, abs=0)
    assert b3.discount(EFFECTIVE) == pytest.approx(1 / (1 + 5.60 / 36500), rel=1e-15)


def test_par_swaps_reprice_to_zero_on_their_own_curve(b3):
    for _label, months, rate in b3.quotes:
        assert ois.ois_par_rate(b3, EFFECTIVE, months) == pytest.approx(rate, abs=1e-10)
        for pay in (True, False):
            v = ois.ois_npv(b3, ois.OisSwap.spot(TRADE, months, rate, 1.0, pay_fixed=pay, calendar=LENIENT))
            assert abs(v.npv) < 1e-12 and v.fair_rate_pct == pytest.approx(rate, abs=1e-10)


def test_discount_zero_forward_consistency(b3):
    t = np.linspace(0.0, b3.t_max, 400)
    z = b3.zero_rate(t)
    assert np.allclose(b3.discount_t(t[1:]), np.exp(-z[1:] * t[1:] / 100), rtol=0, atol=1e-15)
    for basis, m in (("semiannual", 2), ("annual", 1)):
        zb = b3.zero_rate(t[1:], basis)
        assert np.allclose((1 + zb / 100 / m) ** (-m * t[1:]), b3.discount_t(t[1:]), rtol=1e-13)
    simple = b3.zero_rate(t[1:], "simple")
    assert np.allclose(1 / (1 + simple / 100 * t[1:]), b3.discount_t(t[1:]), rtol=1e-13)
    # instantaneous forward = -d ln P / dt inside each segment (flat forwards between nodes)
    mids = (np.array(b3.times[:-1]) + np.array(b3.times[1:])) / 2
    h = 1e-6
    numeric = -(np.log(b3.discount_t(mids + h)) - np.log(b3.discount_t(mids - h))) / (2 * h) * 100
    assert np.allclose(b3.forward_rate(mids), numeric, atol=1e-6)
    # a simple forward between two dates compounds back to the discount ratio
    a, b = date(2027, 1, 4), date(2027, 7, 1)
    f = b3.forward(a, b)
    assert 1 + f / 100 * (b - a).days / 365 == pytest.approx(float(b3.discount(a) / b3.discount(b)), rel=1e-14)
    assert isinstance(b3, DiscountCurveLike)


def test_compounding_the_curves_overnight_forwards_gives_its_discount_ratio_b4(b3):
    s, e = EFFECTIVE, date(2027, 11, 22)
    c = ois.compound_overnight(b3.overnight_forwards(s, e), s, e, calendar=LENIENT)
    assert abs(c.factor - float(b3.discount(s) / b3.discount(e))) < 1e-13 and c.business_days == 259


def test_unwind_rule_inside_a_period_b5(b3):
    end = date(2027, 11, 22)
    tau = (end - EFFECTIVE).days / 365
    at_inception = (1 + 0.0588 * tau) * float(b3.discount(end) / b3.discount(EFFECTIVE)) - 1.0
    assert abs(at_inception) < 1e-15
    mid = date(2026, 12, 21)
    rolled = b3.rebase(mid)
    realised = b3.overnight_forwards(EFFECTIVE, mid)
    swap = ois.OisSwap(effective=EFFECTIVE, months=12, fixed_rate_pct=5.88, notional=1.0, pay_fixed=False)
    v = ois.ois_npv(rolled, swap, fixings=realised, calendar=LENIENT)
    a_real = ois.compound_overnight(realised, EFFECTIVE, mid, calendar=LENIENT).factor
    assert v.npv == pytest.approx((1 + 0.0588 * tau) * float(rolled.discount(end)) - a_real, abs=1e-12)
    assert v.accrued_float == pytest.approx(a_real - 1, abs=1e-15)
    assert v.accrued_fixed == pytest.approx(0.0588 * 31 / 365, abs=1e-15)
    with pytest.raises(ValueError, match="no fixing"):  # a running period needs its fixings
        ois.ois_npv(rolled, swap, calendar=LENIENT)


def test_rebase_keeps_the_forward_rates(b3):
    d = date(2027, 3, 1)
    r = b3.rebase(d)
    for x in (date(2027, 3, 2), date(2028, 6, 30), date(2031, 11, 20)):
        assert float(r.discount(x)) == pytest.approx(float(b3.discount(x) / b3.discount(d)), rel=1e-13)
    assert r.forward(date(2027, 5, 3), date(2027, 9, 1)) == pytest.approx(b3.forward(date(2027, 5, 3),
                                                                                     date(2027, 9, 1)), abs=1e-12)


def test_beyond_the_last_node_the_curve_refuses_unless_allowed(b3):
    late = date(2033, 11, 21)
    assert math.isnan(float(b3.discount(late)))
    with pytest.raises(ValueError, match="beyond the curve"):
        ois.ois_par_rate(b3, EFFECTIVE, "7Y")
    longer = ois.bootstrap_ois(B3_QUOTES, TRADE, 5.60, calendar=LENIENT, max_extrapolation_years=2.5)
    assert bool(longer.extrapolated(longer.year_fraction(late))) and math.isfinite(float(longer.discount(late)))
    slope = (longer.log_dfs[-1] - longer.log_dfs[-2]) / (longer.times[-1] - longer.times[-2])
    t = longer.year_fraction(late)
    assert math.log(float(longer.discount(late))) == pytest.approx(longer.log_dfs[-1] + slope * (t - longer.t_max),
                                                                   abs=1e-15)
    with pytest.raises(ValueError, match="before the curve's valuation date"):
        b3.discount(date(2026, 11, 18))


def test_longer_tenors_the_user_supplies_extend_the_curve():
    curve = ois.bootstrap_ois({**B3_QUOTES, "7Y": 6.30, "10Y": 6.45}, TRADE, 5.60, calendar=LENIENT)
    assert curve.node_dates[-1] == date(2036, 11, 20)
    for months, rate in ((84, 6.30), (120, 6.45)):
        assert ois.ois_par_rate(curve, EFFECTIVE, months) == pytest.approx(rate, abs=1e-10)


def test_the_fbil_loaders_frame_bootstraps_from_the_quoted_rate_not_the_converted_yield(b3):
    names = {"1M": "1 Month", "2M": "2 Months", "3M": "3 Months", "6M": "6 Months", "9M": "9 Months",
             "1Y": "1 Year", "2Y": "2 Years", "3Y": "3 Years", "4Y": "4 Years", "5Y": "5 Years"}
    rows = [{"processRunDate": "2026-11-19", "tenorName": names[k], "rate": v} for k, v in B3_QUOTES.items()]
    frame = load_fbil_money_market(json.dumps(rows), "miborois")
    curve = ois.bootstrap_ois(frame, TRADE, 5.60, calendar=LENIENT)
    assert curve.log_dfs == b3.log_dfs
    with pytest.raises(ValueError, match="never from a yield"):
        ois.bootstrap_ois(frame.drop(columns=["rate_quoted_pct"]), TRADE, 5.60, calendar=LENIENT)
    wrong = frame.assign(quote_convention="semiannual")
    with pytest.raises(ValueError, match="marked 'semiannual'"):
        ois.bootstrap_ois(wrong, TRADE, 5.60, calendar=LENIENT)


def test_quote_refusals():
    frame = pd.DataFrame({"date": ["2026-11-19", "2026-11-18"], "tenor": ["1M", "2M"], "rate_pct": [5.6, 5.7]})
    with pytest.raises(ValueError, match="more than one date"):
        ois.bootstrap_ois(frame, TRADE, 5.60, calendar=LENIENT)
    with pytest.raises(ValueError, match="not the trade date"):
        ois.bootstrap_ois(frame.iloc[:1], date(2026, 11, 20), 5.60, calendar=LENIENT)
    with pytest.raises(ValueError, match="quoted twice"):
        ois.ois_quotes(pd.DataFrame({"tenor": ["12M", "1Y"], "rate_pct": [5.8, 5.9]}))
    with pytest.raises(ValueError, match="is missing"):
        ois.ois_quotes(pd.DataFrame({"tenor": ["1M"], "rate_pct": [float("nan")]}))
    blank = pd.DataFrame({"tenor": ["1M", "2Y"], "rate_pct": [5.6, 6.0],
                          "quote_convention": [float("nan"), "semiannual"]})
    assert ois.ois_quotes(blank)["quote_kind"].tolist() == ["single_period", "semiannual"]  # a blank cell is no claim
    with pytest.raises(ValueError, match="not a business day"):
        ois.bootstrap_ois(B3_QUOTES, date(2026, 11, 21), 5.60, calendar=LENIENT)
    with pytest.raises(ValueError, match="tenor"):
        ois.bootstrap_ois({"7 Days": 5.6}, TRADE, 5.60, calendar=LENIENT)
    with pytest.raises(CalendarCoverageError, match="2027"):
        ois.bootstrap_ois(B3_QUOTES, TRADE, 5.60)  # the strict default calendar stops at 2026


def test_bootstrap_is_deterministic_and_ignores_quote_order(b3):
    items = list(B3_QUOTES.items())
    random.Random(7).shuffle(items)
    again = ois.bootstrap_ois(dict(items), TRADE, 5.60, calendar=LENIENT, source="synthetic B3")
    assert again.log_dfs == b3.log_dfs and again.times == b3.times and again.labels == b3.labels
    assert again.nodes().equals(b3.nodes())


# ---------------------------------------------------------------- valuation


def test_receiver_and_payer_values_and_risk(b3):
    receive = ois.OisSwap.spot(TRADE, "5Y", 6.00, 1e9, pay_fixed=False, calendar=LENIENT)
    pay = replace(receive, pay_fixed=True)
    vr, vp = ois.ois_npv(b3, receive), ois.ois_npv(b3, pay)
    assert vr.npv == pytest.approx(-vp.npv, abs=1e-6) and vr.npv < 0  # receiving 6.00% when par is 6.18%
    assert vr.npv == pytest.approx((6.00 - 6.18) * 1e9 * vr.annuity / 100, rel=1e-9)
    assert vr.pv01 == pytest.approx(1e9 * vr.annuity * 1e-4) and vr.dv01 > 0 > vp.dv01
    assert vr.dv01_par == pytest.approx(vr.pv01, rel=0.01)  # a near-par receiver loses about its annuity per bp
    assert 0 < vr.dv01_par < vr.dv01  # a zero-rate shift also moves the overnight start


def test_forward_starting_and_matured_swaps(b3):
    start = date(2027, 11, 22)
    k = ois.ois_par_rate(b3, start, "2Y")
    v = ois.ois_npv(b3, ois.OisSwap(effective=start, months=24, fixed_rate_pct=k, notional=1.0))
    assert abs(v.npv) < 1e-12 and v.realised is None and v.periods_left == 4
    later = b3.rebase(date(2027, 12, 1))
    with pytest.raises(ValueError, match="ended on"):
        ois.ois_npv(later, ois.OisSwap(effective=EFFECTIVE, months=12, fixed_rate_pct=5.88, notional=1.0))


def test_bumped_curve_moves_every_quote_by_the_bump(b3):
    up = b3.bumped(1.0)
    for _label, months, rate in b3.quotes:
        assert ois.ois_par_rate(up, EFFECTIVE, months) == pytest.approx(rate + 0.01, abs=1e-10)
    only = b3.bumped(1.0, ["5Y"])
    assert ois.ois_par_rate(only, EFFECTIVE, 48) == pytest.approx(6.12, abs=1e-10)
    with pytest.raises(ValueError, match="no quote for"):
        b3.bumped(1.0, ["7Y"])
    with pytest.raises(ValueError, match="not bootstrapped"):
        b3.rebase(date(2027, 1, 4)).bumped(1.0)


def test_curve_table_lays_the_curve_on_a_monthly_grid(b3):
    table = ois.curve_table(b3)
    assert list(table.columns) == list(ois.CURVE_COLUMNS) and len(table) == 60
    one = table.set_index("tenor").loc["1Y"]
    assert one["par_pct"] == pytest.approx(5.88, abs=1e-10) and not one["extrapolated"]
    assert math.isnan(table.set_index("tenor").loc["13M", "par_pct"])  # not a whole number of periods


def test_curve_validation():
    with pytest.raises(ValueError, match="first node"):
        ois.OisCurve(valuation_date=TRADE, times=(0.1, 0.2), log_dfs=(0.0, -0.01))
    with pytest.raises(ValueError, match="must increase"):
        ois.OisCurve(valuation_date=TRADE, times=(0.0, 0.2, 0.2), log_dfs=(0.0, -0.01, -0.02))
    with pytest.raises(ValueError, match="max_extrapolation_years"):
        ois.OisCurve(valuation_date=TRADE, times=(0.0, 0.2), log_dfs=(0.0, -0.01), max_extrapolation_years=-1)


# ---------------------------------------------------------------- command line


def _quotes_csv(path):
    pd.DataFrame({"tenor": list(B3_QUOTES), "rate_pct": list(B3_QUOTES.values())}).to_csv(path, index=False)


def test_cli_ois_curve(tmp_path, capsys):
    _quotes_csv(tmp_path / "q.csv")
    args = ["ois-curve", "--quotes", str(tmp_path / "q.csv"), "--trade-date", "2026-11-19", "--overnight", "5.60"]
    assert main([*args, "--out-dir", str(tmp_path / "strict")]) == 2
    assert "2027" in capsys.readouterr().err  # the strict calendar refuses, and nothing is written
    assert not (tmp_path / "strict").exists()
    assert main([*args, "--project-calendar", "--out-dir", str(tmp_path / "out")]) == 0
    printed = capsys.readouterr().out
    assert "10 quotes to 2031-11-20" in printed and "calendar_projected" in printed
    nodes = pd.read_csv(tmp_path / "out" / "nodes.csv")
    assert nodes.loc[nodes["label"] == "5Y", "discount"].iloc[0] == pytest.approx(0.736735601037, abs=1e-10)
    assert main([*args, "--project-calendar", "--out-dir", str(tmp_path / "again")]) == 0
    for name in ("nodes.csv", "curve.csv"):
        assert (tmp_path / "again" / name).read_bytes() == (tmp_path / "out" / name).read_bytes()
