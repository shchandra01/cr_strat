"""Tests of the return engine (spec A1-A5): positions, valuation, funding, OIS, attribution identities I1-I9 and the
worked examples R2-R9, R14 and R16, on synthetic data only (bonds priced off known NSS curves, synthetic OIS quotes,
synthetic money-market tables). Typed-in facts: the 13 Nov 2026 auction's 6.94% GS 2036 at 97.43 (RBI's result) and
the Mumbai holidays of the shipped calendar (10 and 24 Nov, 2 Oct 2026).
"""

import math
from dataclasses import replace
from datetime import date, datetime, time, timedelta

import pandas as pd
import pytest

from cr_strat import costs as co
from cr_strat import curvefit as cf
from cr_strat import funding as fu
from cr_strat import ois
from cr_strat import returns as rt
from cr_strat import sovereign as sv
from cr_strat.asset_swaps import hedge_notional
from cr_strat.bonds import DiscountInstrument, FixedRateBond
from cr_strat.calendars import mumbai_settlement_calendar
from cr_strat.conventions import CP

CAL = mumbai_settlement_calendar()
LEN = CAL.lenient()
U = 1e7  # rupees per 1.00 of price on Rs 100 crore of face
GS2036 = FixedRateBond(6.94, date(2036, 5, 11))
NSS = (7.6, -2.0, -1.0, 2.0, 1.5, 8.0)
B3 = {"1M": 5.62, "2M": 5.66, "3M": 5.70, "6M": 5.78, "9M": 5.84, "1Y": 5.88, "2Y": 5.98, "3Y": 6.06, "4Y": 6.12,
      "5Y": 6.18}
TERMS = {"isin": "GS2036", "kind": "gsec", "coupon_pct": 6.94, "maturity_date": "2036-05-11"}
HAIRCUTS = pd.DataFrame([
    {"effective_date": "2015-01-01", "kind": "gsec", "haircut_pct": 3.0, "source": "synthetic"},
    {"effective_date": "2015-01-01", "kind": "sdl", "haircut_pct": 10.0, "source": "synthetic"},
    {"effective_date": "2015-01-01", "kind": "tbill", "haircut_pct": 1.0, "source": "synthetic"}])


def zero_costs():
    """A cost table with every component at zero (user rows replacing the defaults)."""
    units = {"half_spread_bp": "bp_yield", "gst_rate": "pct", "treps_margin_pct": "pct",
             "stamp_duty": "pct_consideration", "treps_charge": "rs_per_crore_repayment"}
    rows = [{"rule_id": f"Z{k}", "component": c, "effective_from": "2015-01-01", "value": 0,
             "unit": units.get(c, "rs_per_crore_face"), "source": "test: no costs"}
            for k, c in enumerate(co.COMPONENTS) if c != "brokerage"]
    rows.append({"rule_id": "ZOIS", "component": "half_spread_bp", "kind": "ois", "effective_from": "2015-01-01",
                 "value": 0, "unit": "bp_rate", "source": "test: no costs"})
    return co.read_cost_table(overrides=pd.DataFrame(rows), replace_components=co.COMPONENTS)


NOCOST = zero_costs()


def mmd(start, end, rate=5.57, *, repo=None, overrides=None):
    """A synthetic money_market_daily frame (rbi_store's columns): rates on business days, public the next business
    day at 09:00."""
    rows, d = [], start
    overrides = overrides or {}
    while d <= end:
        business = CAL.is_business_day(d)
        nxt = LEN.add_business_days(d, 1)
        tri = overrides.get(d, rate) if business else None
        rp = (repo if repo is not None else tri) if business else None
        rows.append({"as_on_date": d, "business_day": business, "quarter_end_day": d.month in (3, 6, 9, 12) and
                     d.day >= 28, "fy_end_day": False, "triparty_rate_pct": tri,
                     "triparty_volume_cr": 5e5 if business else 0.0, "market_repo_rate_pct": rp,
                     "market_repo_volume_cr": 1e5 if business else 0.0, "call_rate_pct": tri,
                     "call_volume_cr": 1e4 if business else 0.0, "sdf_rate_pct": 5.25, "msf_rate_pct": 5.75,
                     "repo_rate_pct": 5.5, "public_from": datetime(nxt.year, nxt.month, nxt.day, 9)})
        d += timedelta(days=1)
    return pd.DataFrame(rows)


def funding(start=date(2026, 9, 1), end=date(2026, 12, 31), **kw):
    frame = mmd(start, end, **kw)
    return {"triparty": fu.funding_rates(frame, "triparty"), "market_repo": fu.funding_rates(frame, "market_repo")}


def settings(preset="nbfc_non_pd", **kw):
    return rt.EngineSettings(access=rt.ACCESS_PRESETS[preset], **kw)


def trade(tid="T1", side="buy", face=1e9, day="2026-11-13", book="b", **kw):
    return {"trade_id": tid, "book": book, **TERMS, "side": side, "face": face, "trade_date": day, **kw}


def flows(amount=2e9, day="2026-11-13", book="b"):
    return pd.DataFrame([{"date": day, "book": book, "amount": amount}])


def marks_at(bond, isin, yld, days, **extra):
    return pd.DataFrame([{"date": d, "isin": isin, "clean_price": bond.clean_price(yld, CAL.settlement_date(d, 1)),
                          **extra} for d in days])


def lines(result, book="b", memo=False):
    return result.lines(book, memo=memo)


def assert_identity(result):
    ident = result.checks[result.checks["check"] == "identity"]
    assert len(ident) and (ident["status"] == "ok").all(), ident[ident["status"] != "ok"]


# ---------------------------------------------------------------- carry, forwards, redemption (R2, R3, R3b, R16)


def test_hold_carry_over_a_weekend_and_a_day_r2_r3():
    y0 = GS2036.yield_from_price(97.43, date(2026, 11, 17))
    assert y0 == pytest.approx(7.3203088173, abs=1e-10)
    marks = pd.DataFrame([
        {"date": date(2026, 11, 12), "isin": "GS2036", "clean_price": GS2036.clean_price(y0, date(2026, 11, 13))},
        {"date": date(2026, 11, 13), "isin": "GS2036", "clean_price": GS2036.clean_price(y0, date(2026, 11, 16))},
        {"date": date(2026, 11, 16), "isin": "GS2036", "clean_price": 97.43},
        {"date": date(2026, 11, 17), "isin": "GS2036", "clean_price": GS2036.clean_price(y0, date(2026, 11, 18))}])
    res = rt.run_book([trade(day="2026-11-12", price=marks["clean_price"].iloc[0])], flows=flows(day="2026-11-12"),
                      market=rt.MarketData(marks=marks), settings=settings(costs=NOCOST), end=date(2026, 11, 17))
    by_day = lines(res)
    assert by_day.loc[date(2026, 11, 16), "accrual"] == pytest.approx(5_78_333.3333, abs=1e-4)  # 3 days of 30E/360
    assert by_day.loc[date(2026, 11, 16), "pull_to_par"] == pytest.approx(2_010.2267, abs=1e-4)
    assert by_day.loc[date(2026, 11, 17), "accrual"] == pytest.approx(1_92_777.7778, abs=1e-4)
    assert by_day.loc[date(2026, 11, 17), "pull_to_par"] == pytest.approx(2_049.1315, abs=1e-4)
    assert by_day.loc[date(2026, 11, 17)].sum() == pytest.approx(1_94_826.9093, abs=1e-4)
    for line in ("price_unattributed", "convexity", "unexplained"):
        assert abs(by_day.loc[date(2026, 11, 17)].get(line, 0.0)) < 1e-6
    assert_identity(res)
    nav = res.nav.set_index("date")
    assert nav.loc[date(2026, 11, 17), "pnl"] == pytest.approx(1_94_826.9093, abs=1e-4)


def test_forward_between_trade_and_settlement_r3b():
    marks = pd.DataFrame([{"date": date(2026, 11, 13), "isin": "GS2036", "clean_price": 97.43},
                          {"date": date(2026, 11, 16), "isin": "GS2036", "clean_price": 97.50}])
    res = rt.run_book([trade(price=97.43)], flows=flows(), market=rt.MarketData(marks=marks),
                      settings=settings(costs=NOCOST), end=date(2026, 11, 16))
    nav = res.nav.set_index("date")
    assert abs(nav.loc[date(2026, 11, 13), "forwards"]) < 1e-5  # clean on clean: worth nothing at the trade price
    assert nav.loc[date(2026, 11, 16), "pnl"] == pytest.approx(7_00_000.00, abs=1e-5)  # U x 0.07
    day = lines(res).loc[date(2026, 11, 16)]
    assert day.get("accrual", 0.0) == 0.0  # no accrual on the settlement day
    assert day.sum() == pytest.approx(7_00_000.00, abs=1e-5)
    pos = res.positions.set_index(["date", "state"])
    assert pos.loc[(date(2026, 11, 13), "pending"), "settlement_date"] == date(2026, 11, 16)
    assert pos.loc[(date(2026, 11, 16), "settled"), "face"] == 1e9
    assert_identity(res)


def test_redemption_paid_before_a_holiday_r16():
    bond = FixedRateBond(8.15, date(2026, 11, 24))  # 24 Nov 2026 is a Mumbai holiday: paid Mon 23 Nov
    days = [date(2026, 11, 18), date(2026, 11, 19), date(2026, 11, 20)]
    marks = marks_at(bond, "GS2026", 5.80, days)
    assert marks["clean_price"].iloc[2] == pytest.approx(100.0058739510, abs=1e-10)
    res = rt.run_book([{"trade_id": "R1", "book": "b", "isin": "GS2026", "kind": "gsec", "coupon_pct": 8.15,
                        "maturity_date": "2026-11-24", "side": "buy", "face": 1e9, "trade_date": "2026-11-18",
                        "price": marks["clean_price"].iloc[0]}], flows=flows(day="2026-11-18"),
                      market=rt.MarketData(marks=marks), settings=settings(costs=NOCOST), end=date(2026, 11, 25))
    day = lines(res).loc[date(2026, 11, 23)]
    assert day["accrual"] == pytest.approx(9_05_555.5556, abs=1e-4)  # the last coupon in full, paid a day early
    assert day["pull_to_par"] == pytest.approx(-58_739.5102, abs=1e-4)
    assert day.sum() == pytest.approx(8_46_816.0454, abs=1e-4)
    nav = res.nav.set_index("date")
    assert nav.loc[date(2026, 11, 23), "bonds"] == 0.0
    assert nav.loc[date(2026, 11, 23), "cash"] == pytest.approx(2e9 + nav["pnl"].sum(), abs=1e-3)  # all in cash
    assert nav.loc[date(2026, 11, 25), "pnl"] == 0.0
    assert set(res.positions["date"]) == set(days)
    assert_identity(res)
    with pytest.raises(ValueError, match="shut period before redemption"):
        rt.run_book([{"trade_id": "R2", "book": "b", "isin": "GS2026", "kind": "gsec", "coupon_pct": 8.15,
                      "maturity_date": "2026-11-24", "side": "buy", "face": 1e9, "trade_date": "2026-11-20",
                      "price": 100.0}], flows=flows(day="2026-11-20"), market=rt.MarketData(marks=marks),
                    settings=settings(costs=NOCOST), end=date(2026, 11, 23))


# ---------------------------------------------------------------- attribution on curves (R4, R5, R6, I2, I3)


def _nss(settle, params=NSS):
    return cf.NssCurve(valuation_date=settle, t_min=0.25, t_max=40.0, method="nss", params=params)


def _states():
    sp, sd = date(2026, 11, 17), date(2026, 11, 18)
    c_prev = _nss(sp)
    m0 = rt.model_yield(c_prev, GS2036, sp)
    y0 = m0 + 0.05  # the bond 5 bp cheap
    prev = rt.MarkState(date(2026, 11, 16), sp, GS2036.clean_price(y0, sp), y0, "traded", c_prev, m0)
    return prev, sd, y0


def test_roll_down_on_a_constant_curve_r4():
    prev, sd, y0 = _states()
    roll = rt.rolled(prev.curve, sd)
    assert roll.valuation_date == sd and roll.params == NSS
    m_roll = rt.model_yield(roll, GS2036, sd)
    assert prev.model_yield_pct == pytest.approx(7.7019748138, abs=1e-10)
    assert m_roll == pytest.approx(7.7018499136, abs=1e-10)
    y1 = y0 + (m_roll - prev.model_yield_pct)  # constant curve, constant residual
    today = rt.MarkState(date(2026, 11, 17), sd, GS2036.clean_price(y1, sd), y1, "traded", roll, m_roll)
    out = rt.attribute_bond(GS2036, 1e9, prev, today)
    assert out["accrual"] == pytest.approx(1_92_777.7778, abs=1e-4)
    assert out["pull_to_par"] == pytest.approx(7_376.5161, abs=1e-4)
    assert out["roll_down"] == pytest.approx(7_991.5938, abs=1e-4)
    assert out["convexity"] == pytest.approx(0.042931, abs=1e-6)
    for line in ("curve_parallel", "curve_slope", "curve_curvature", "curve_other", "rich_cheap"):
        assert abs(out[line]) < 1e-9
    assert abs(out["convexity"] + out["unexplained"]) < 0.05  # I2: below Rs 0.05 per Rs 100 crore a day
    v0 = U * (prev.clean + GS2036.accrued_interest(date(2026, 11, 16)))
    v1 = U * (today.clean + GS2036.accrued_interest(date(2026, 11, 17)))
    assert sum(out.values()) == pytest.approx(v1 - v0, abs=1e-6)
    assert sum(out.values()) == pytest.approx(2_08_145.9306, abs=1e-4)
    full_roll = U * (GS2036.clean_price(y1, sd) - GS2036.clean_price(y0, sd))
    assert out["accrual"] + out["pull_to_par"] + full_roll == pytest.approx(sum(out.values()), abs=1e-6)


@pytest.mark.parametrize("bp,unexplained", [(1, -0.0875), (10, -87.27), (25, -1_358.38), (50, -10_798.30),
                                            (100, -85_304.15)])
def test_unexplained_is_third_order_r5(bp, unexplained):
    _, sd, y0 = _states()
    prev = rt.MarkState(sd, sd, GS2036.clean_price(y0, sd), y0)
    today = rt.MarkState(sd, sd, GS2036.clean_price(y0 + bp / 100, sd), y0 + bp / 100)
    out = rt.attribute_bond(GS2036, 1e9, prev, today)
    assert out["unexplained"] == pytest.approx(unexplained, abs=0.01)
    first = -U * GS2036.dv01(y0, sd) * bp
    assert out["price_unattributed"] == pytest.approx(first, rel=1e-12)  # I3: no curve, the whole first-order move
    assert out["convexity"] == pytest.approx(
        U * 0.5 * GS2036.convexity(y0, sd) * GS2036.dirty_price(y0, sd) * (bp / 1e4) ** 2, rel=1e-12)


def test_factor_split_by_revaluation_r6():
    prev, sd, y0 = _states()
    roll = rt.rolled(prev.curve, sd)
    shapes = rt.factor_shapes()
    mixed = rt.ShiftedCurve(roll, lambda t: (10 * shapes["parallel"](t) + 5 * shapes["slope"](t)
                                             - 3 * shapes["curvature"](t)) / 1e4)
    sizes = rt.factor_moves(mixed, roll)
    assert sizes == pytest.approx({"parallel": 10.0, "slope": 5.0, "curvature": -3.0}, abs=1e-9)
    m = rt.model_yield(mixed, GS2036, sd)
    y1 = y0 + m - prev.model_yield_pct
    today = rt.MarkState(date(2026, 11, 17), sd, GS2036.clean_price(y1, sd), y1, "traded", mixed, m)
    out = rt.attribute_bond(GS2036, 1e9, prev, today)
    dv = -U * GS2036.dv01(y0, sd)
    assert out["curve_parallel"] / dv == pytest.approx(10.327737, abs=1e-6)
    assert out["curve_slope"] / dv == pytest.approx(1.358454, abs=1e-6)
    assert out["curve_curvature"] / dv == pytest.approx(1.072394, abs=1e-6)
    assert out["curve_parallel"] == pytest.approx(-66_08_084.93, abs=0.01)
    assert out["curve_slope"] == pytest.approx(-8_69_191.60, abs=0.01)
    assert out["curve_curvature"] == pytest.approx(-6_86_159.26, abs=0.01)
    assert out["curve_other"] / dv == pytest.approx(-7.28e-4, abs=1e-6)
    assert abs(out["rich_cheap"]) < 1e-6
    key = rt.factor_moves(mixed, roll, basis="key_rates")
    assert list(key) == [f"{t:g}y" for t in rt.DEFAULT_NODES]


def _curve_market(days, isin_bonds, shift=None, resid=None, params=NSS):
    """Curves and marks: each day's NSS curve for its T+1 settlement (``shift(k)`` changes the parameters on day k),
    each bond marked at its model yield plus a residual (``resid(k, j)`` percent)."""
    curves, marks = {}, []
    for k, d in enumerate(days):
        s = CAL.settlement_date(d, 1)
        c = _nss(s, shift(k) if shift else params)
        curves[d] = c
        for j, (isin, bond) in enumerate(isin_bonds.items()):
            y = rt.model_yield(c, bond, s) + (resid(k, j) if resid else 0.05)
            marks.append({"date": d, "isin": isin, "clean_price": bond.clean_price(y, s)})
    return curves, pd.DataFrame(marks)


def test_constant_curve_through_the_engine_i2():
    days = CAL.business_days(date(2026, 11, 13), date(2026, 11, 27))
    curves, marks = _curve_market(days, {"GS2036": GS2036})
    res = rt.run_book([trade(price=marks["clean_price"].iloc[0])], flows=flows(),
                      market=rt.MarketData(marks=marks, curves={"gsec": curves}), settings=settings(costs=NOCOST),
                      end=days[-1])
    by_day = lines(res)
    held = by_day[by_day.index > date(2026, 11, 16)]
    for line in ("curve_parallel", "curve_slope", "curve_curvature", "curve_other", "rich_cheap"):
        assert held.get(line, pd.Series(0.0)).abs().max() < 1e-4  # solver noise only (Rs 100 crore)
    assert (held["roll_down"] > 0).all()  # an upward curve: rolling down it gains
    steps = pd.Series({d: (CAL.settlement_date(d, 1) - CAL.settlement_date(p, 1)).days
                       for p, d in zip(days, days[1:])})
    second = (held.get("convexity", 0) + held.get("unexplained", 0)).abs()
    assert (second / steps.reindex(second.index) ** 2).max() < 0.05  # Rs 0.05 per Rs 100 crore a day, squared
    assert_identity(res)
    assert set(res.positions["mark_source"]) == {"traded"}


def test_parallel_shift_and_twist_through_the_engine():
    days = CAL.business_days(date(2026, 11, 13), date(2026, 11, 20))
    other = FixedRateBond(7.26, date(2029, 1, 14))

    def shift(k):
        b0, b1, b2, b3, t1, t2 = NSS
        return (b0 + 0.10 * (k >= 2), b1 - 0.20 * (k >= 4), b2, b3, t1, t2)

    curves, marks = _curve_market(days, {"GS2036": GS2036, "GS2029": other}, shift=shift)
    trades = [trade(price=marks["clean_price"].iloc[0]),
              {**trade("T2", face=5e8), "isin": "GS2029", "coupon_pct": 7.26, "maturity_date": "2029-01-14",
               "price": marks["clean_price"].iloc[1]}]
    res = rt.run_book(trades, flows=flows(5e9), market=rt.MarketData(marks=marks, curves={"gsec": curves}),
                      settings=settings(costs=NOCOST), end=days[-1])
    att = res.attribution.set_index(["date", "instrument", "line"])["amount"]
    k2 = days[2]  # beta0 up 10 bp: a parallel move of continuous zero rates
    assert att.loc[(k2, "GS2036", "curve_parallel")] < 0 and att.loc[(k2, "GS2029", "curve_parallel")] < 0
    assert abs(att.get((k2, "GS2036", "curve_slope"), 0.0)) < 1e-6 * abs(att.loc[(k2, "GS2036", "curve_parallel")])
    k4 = days[4]  # beta1 down 20 bp: short rates fall, a steepening
    assert att.loc[(k4, "GS2029", "curve_slope")] != 0
    assert abs(att.get((k4, "GS2036", "rich_cheap"), 0.0)) < 1e-4  # the residual did not change
    assert_identity(res)


# ---------------------------------------------------------------- funding (R8, I5, caps, quarter-end, shortfall)


def _held_book(day="2026-11-13", end=date(2026, 12, 16), capital=1e8, fund=None, policy=None, preset="nbfc_non_pd",
               cost_table=NOCOST, **trade_kw):
    s_first = CAL.settlement_date(as_day(day), 1)
    y = GS2036.yield_from_price(97.43, s_first)
    days = CAL.business_days(as_day(day), end)
    market = rt.MarketData(marks=marks_at(GS2036, "GS2036", y, days), funding=fund or funding(), haircuts=HAIRCUTS)
    st = settings(preset, costs=cost_table, funding=policy or rt.FundingPolicy())
    res = rt.run_book([trade(day=day, price=97.43, liquidity="liquid", **trade_kw)], flows=flows(capital, day),
                      market=market, settings=st, end=end)
    return res, y


def as_day(text):
    return date.fromisoformat(text) if isinstance(text, str) else text


def test_fixed_sizing_reproduces_funding_carry_r8_i5():
    tri = funding()["triparty"]
    res, y = _held_book(policy=rt.FundingPolicy(sizing="fixed", margin_pct=0.0))
    f = res.funding.set_index("date")
    assert f.loc[date(2026, 11, 16), "borrowed"] == pytest.approx(94_60_05_972.22, abs=0.01)  # R8
    assert f.loc[date(2026, 11, 16), "haircut_equity"] == pytest.approx(2_92_57_916.67, abs=0.01)
    by_day = lines(res, memo=True)
    assert by_day.loc[date(2026, 11, 17), "funding_repo"] == pytest.approx(-1_44_363.10, abs=0.005)
    assert by_day.loc[date(2026, 11, 23), "funding_repo"] == pytest.approx(-4_33_089.31, abs=0.005)  # Fri to Mon
    assert by_day.loc[date(2026, 11, 17), "memo_haircut_cost"] == pytest.approx(-4_464.84, abs=0.005)
    held = by_day[by_day.index > date(2026, 11, 16)]
    carry = fu.carry(GS2036, 1e9, 97.43, date(2026, 11, 16), date(2026, 12, 16), funding=tri, haircut_pct=3.0)
    assert held["accrual"].sum() == pytest.approx(carry.income, rel=1e-9)  # I5
    assert -held["funding_repo"].sum() == pytest.approx(carry.repo, rel=1e-9)
    assert -held["memo_haircut_cost"].sum() == pytest.approx(carry.equity_cost, rel=1e-9)
    restated = U * (GS2036.clean_price(y, date(2026, 12, 17)) - GS2036.clean_price(y, date(2026, 11, 17)))
    assert held["pull_to_par"].sum() == pytest.approx(restated, rel=1e-9)
    assert_identity(res)


def test_cash_need_sizing_lots_margin_and_charges():
    res, _ = _held_book(cost_table=co.read_cost_table())
    f = res.funding.set_index("date")
    row = f.loc[date(2026, 11, 17)]
    assert row["borrowed"] % 5e5 == 0  # TREPS's lot of Rs 5 lakh
    growth = 1 + 5.57 / 36500
    assert row["margin"] == pytest.approx(0.005 * row["borrowed"] * growth)  # 0.50% of the second leg
    assert 0 <= row["idle_cash"] < 5e5
    by_day = lines(res, memo=True)
    charge = min(10 * row["borrowed"] * growth / 1e7, 1500.0)
    assert by_day.loc[date(2026, 11, 17), "repo_charges"] == pytest.approx(-charge * 1.18)
    assert by_day.loc[date(2026, 11, 18), "memo_margin_cost"] == pytest.approx(-row["margin"] * 5.57 / 36500)
    fixed, _ = _held_book(cost_table=co.read_cost_table(), policy=rt.FundingPolicy(sizing="fixed", margin_pct=0.0))
    assert lines(res)["funding_repo"].sum() < 0 and lines(fixed)["funding_repo"].sum() < 0
    assert_identity(res)
    assert "PF01" in " ".join(res.notes) and "UNVERIFIED" in " ".join(res.notes)


def test_quarter_end_spike_and_a_holiday_weekend():
    fund = funding(overrides={date(2026, 9, 30): 6.52, date(2026, 10, 1): 5.90})
    res, _ = _held_book(day="2026-09-25", end=date(2026, 10, 6), fund=fund,
                        policy=rt.FundingPolicy(sizing="fixed", margin_pct=0.0))
    b = res.funding.set_index("date").loc[date(2026, 9, 30), "borrowed"]
    by_day = lines(res)
    assert by_day.loc[date(2026, 10, 1), "funding_repo"] == pytest.approx(-b * 6.52 / 36500)
    # 1 Oct (Thu) to 5 Oct (Mon): 2 Oct is a holiday, so four nights at 1 Oct's rate
    assert by_day.loc[date(2026, 10, 5), "funding_repo"] == pytest.approx(-b * 4 * 5.90 / 36500)
    assert_identity(res)


def test_treps_caps_send_an_sdl_book_to_market_repo():
    sdl = FixedRateBond(7.61, date(2030, 11, 18))
    days = CAL.business_days(date(2026, 11, 13), date(2026, 11, 20))
    marks = marks_at(sdl, "SDL2030", 7.40, days)
    fund = funding(repo=5.60)
    res = rt.run_book([{"trade_id": "S1", "book": "b", "isin": "SDL2030", "kind": "sdl", "coupon_pct": 7.61,
                        "maturity_date": "2030-11-18", "side": "buy", "face": 1e9, "trade_date": "2026-11-13",
                        "price": marks["clean_price"].iloc[0]}], flows=flows(1.5e8),
                      market=rt.MarketData(marks=marks, funding=fund, haircuts=HAIRCUTS),
                      settings=settings(costs=NOCOST), end=days[-1])
    f = res.funding
    assert set(f["segment"]) == {"market_repo"}  # CCIL lends nothing against an SDL-only book
    assert "RMD/SS/26/13" in f["notes"].iloc[0]
    by_day = lines(res)
    assert "funding_repo" not in by_day or by_day["funding_repo"].abs().sum() == 0
    b = f.set_index("date").loc[date(2026, 11, 17), "borrowed"]
    assert by_day.loc[date(2026, 11, 18), "funding_excess"] == pytest.approx(-b * 5.60 / 36500)
    assert_identity(res)


def test_shortfall_is_refused_or_overdrawn():
    with pytest.raises(ValueError, match="funding_shortfall: book b on 2026-11-16"):
        _held_book(capital=1e7)
    res, _ = _held_book(capital=1e7, end=date(2026, 11, 18),
                        policy=rt.FundingPolicy(on_shortfall="overdraft", overdraft_rate_pct=9.0))
    by_day = lines(res)
    over = res.funding[res.funding["segment"] == "overdraft"].set_index("date")["borrowed"]
    expected = -over.loc[date(2026, 11, 16)] * 9 / 36500
    assert by_day.loc[date(2026, 11, 17), "funding_overdraft"] == pytest.approx(expected)
    with pytest.raises(ValueError, match="allows no repo"):
        _held_book(preset="unregulated_company")
    with pytest.raises(ValueError, match="overdraft_rate_pct"):
        rt.FundingPolicy(on_shortfall="overdraft")


# ---------------------------------------------------------------- OIS (R9, R9b, settlement)


@pytest.fixture(scope="module")
def curve19():
    return ois.bootstrap_ois(B3, date(2026, 11, 19), 5.60, calendar=LEN, source="synthetic B3")


def _swap_run(curves, end, fixings=None, **swap_kw):
    swap = {"trade_id": "S1", "book": "b", "trade_date": "2026-11-19", "effective": "2026-11-20", "months": 48,
            "fixed_rate_pct": 6.134480, "notional": 1e9, "pay_fixed": True, "mid_rate_pct": 6.134480,
            "purpose": "view", **swap_kw}
    return rt.run_book(swaps=[swap], flows=flows(1e7, "2026-11-19"),
                       market=rt.MarketData(ois_curves=curves, fixings=fixings or {date(2026, 11, 20): 5.60}),
                       settings=settings(project_calendar=True), end=end)


def test_ois_carry_and_roll_down_on_a_constant_curve_r9(curve19):
    curves = {d: rt.rolled(curve19, d) for d in (date(2026, 11, 19), date(2026, 11, 20), date(2026, 11, 23))}
    res = _swap_run(curves, date(2026, 11, 23))
    by_day = lines(res)
    assert by_day.loc[date(2026, 11, 19), "trading"] == pytest.approx(-5_08_077.5151, abs=1e-4)  # NPV at the mid
    assert by_day.loc[date(2026, 11, 19), "stamp_duty"] == pytest.approx(-1000.0)
    assert by_day.loc[date(2026, 11, 23), "ois_carry"] == pytest.approx(-43_929.8630, abs=1e-4)
    assert by_day.loc[date(2026, 11, 23), "ois_roll_down"] == pytest.approx(-4_470.6316, abs=1e-4)
    assert by_day.loc[date(2026, 11, 23)].sum() == pytest.approx(-48_400.4946, abs=1e-4)  # I6
    nav = res.nav.set_index("date")
    assert nav.loc[date(2026, 11, 23), "ois"] == pytest.approx(-5_72_577.5742, abs=1e-4)
    pos = res.positions.set_index("date").loc[date(2026, 11, 23)]
    assert pos["isin"] == "OIS:S1" and pos["dv01"] < 0  # a payer loses as rates fall
    assert_identity(res)


def test_ois_curve_factors_r9b(curve19):
    shapes = rt.factor_shapes()
    moved = rt.ShiftedCurve(rt.rolled(curve19, date(2026, 11, 23)),
                            lambda t: (10 * shapes["parallel"](t) + 5 * shapes["slope"](t)) / 1e4)
    curves = {date(2026, 11, 19): curve19, date(2026, 11, 20): rt.rolled(curve19, date(2026, 11, 20)),
              date(2026, 11, 23): moved}
    res = _swap_run(curves, date(2026, 11, 23))
    day = lines(res).loc[date(2026, 11, 23)]
    assert day["ois_curve_parallel"] == pytest.approx(35_97_301.50, abs=0.01)
    assert day["ois_curve_slope"] == pytest.approx(-1_41_859.14, abs=0.01)
    assert day["ois_curve_other"] == pytest.approx(441.48, abs=0.01)
    assert day.get("ois_curve_curvature", 0.0) == pytest.approx(0.0, abs=1e-6)
    assert_identity(res)


def test_ois_settlement_rounding_and_maturity(curve19):
    days = CAL.business_days(date(2026, 11, 19), date(2026, 12, 22))
    curves = {d: rt.rolled(curve19, d) for d in days}
    fixings = {d: 5.60 + 0.01 * (k % 3) for k, d in enumerate(days)}
    res = _swap_run(curves, date(2026, 12, 22), fixings=fixings, months=1, fixed_rate_pct=5.6512, mid_rate_pct="")
    by_day = lines(res)
    paid = date(2026, 12, 21)  # 20 Dec 2026 is a Sunday: Modified Following to Monday
    comp = ois.compound_overnight(fixings, date(2026, 11, 20), paid, calendar=CAL)
    amounts = ois.settlement_amounts(1e9, 5.6512, comp)
    unrounded = 1e9 * (comp.factor - 1) - 1e9 * 5.6512 * comp.days / 36500
    x = amounts["net_settlement"] * (1 if amounts["payer"] == "fixed receiver" else -1)
    assert by_day.loc[paid, "ois_settlement_rounding"] == pytest.approx(x - unrounded, abs=1e-6)
    assert abs(x - unrounded) < 0.00005 / 100 * 1e9 * comp.days / 365 + 1  # the rate rounded to 0.0001%, the net
    nav = res.nav.set_index("date")
    assert nav.loc[paid, "ois"] == 0.0 and nav.loc[date(2026, 12, 22), "pnl"] == 0.0
    assert nav.loc[paid, "cash"] == pytest.approx(nav.loc[date(2026, 12, 18), "cash"] + x)
    assert_identity(res)
    assert "no mid given" in res.fills["notes"].iloc[0]


# ---------------------------------------------------------------- trades and costs (I4, R7, T+0, T+2, switches)


def test_same_day_round_trip_loses_exactly_its_costs_i4_r7():
    marks = pd.DataFrame([{"date": date(2026, 11, 13), "isin": "GS2036", "clean_price": 97.43, "vwap": 97.43,
                           "source": "traded"},
                          {"date": date(2026, 11, 16), "isin": "GS2036", "clean_price": 97.43}])
    trades = [trade("B1", liquidity="liquid"), trade("S1", side="sell", liquidity="liquid")]
    res = rt.run_book(trades, flows=flows(), market=rt.MarketData(marks=marks),
                      settings=settings(costs=co.read_cost_table()), end=date(2026, 11, 16))
    fills = res.fills.set_index("trade_id")
    assert fills.loc["B1", "fill"] - 97.43 == pytest.approx(0.041522, abs=5e-7)
    assert fills.loc["S1", "fill"] - 97.43 == pytest.approx(-0.041522, abs=5e-7)
    assert fills["fees"].sum() == 9500.0 and fills["gst"].sum() == pytest.approx(1710.0)
    total = res.nav["pnl"].sum()
    assert total == pytest.approx(-8_41_651.41, abs=0.01)
    by_line = lines(res).sum()
    assert by_line["spread_cost"] == pytest.approx(-8_30_441.41, abs=0.01)
    assert by_line["fees"] + by_line["gst"] == pytest.approx(-11_210.0)
    for line in ("trading", "pull_to_par", "price_unattributed", "convexity", "unexplained", "accrual"):
        assert abs(by_line.get(line, 0.0)) < 1e-6
    assert "HS01:P" in fills.loc["B1", "rules"]
    assert_identity(res)


def test_t0_cp_trade_and_a_t2_trade_restated_to_the_mark():
    cp = DiscountInstrument(date(2027, 2, 15), CP)
    days = CAL.business_days(date(2026, 11, 13), date(2026, 11, 18))
    cp_marks = pd.DataFrame([{"date": d, "isin": "CP2027", "clean_price": cp.price(7.20, CAL.settlement_date(d, 1),
                                                                                    rounded=False)} for d in days])
    y = GS2036.yield_from_price(97.43, date(2026, 11, 16))
    gs_marks = marks_at(GS2036, "GS2036", y, days)
    trades = [{"trade_id": "C1", "book": "b", "isin": "CP2027", "kind": "cp", "maturity_date": "2027-02-15",
               "side": "buy", "face": 5e8, "trade_date": "2026-11-13", "settlement_date": "2026-11-13",
               "yield_pct": 7.25},
              trade("F1", price=97.40, settlement_date="2026-11-17")]  # an FPI counterparty: T+2
    res = rt.run_book(trades, flows=flows(2e9), market=rt.MarketData(marks=pd.concat([cp_marks, gs_marks])),
                      settings=settings(costs=co.read_cost_table()), end=days[-1])
    pos = res.positions.set_index(["date", "isin"])
    assert pos.loc[(date(2026, 11, 13), "CP2027"), "state"] == "settled"  # T+0: held at once
    assert pos.loc[(date(2026, 11, 16), "GS2036"), "state"] == "pending"
    assert pos.loc[(date(2026, 11, 17), "GS2036"), "state"] == "settled"
    att = res.attribution.set_index(["date", "instrument", "line"])["amount"]
    assert att.loc[(date(2026, 11, 13), "CP2027", "pull_to_par")] != 0  # the T+0 fill restated to T+1
    assert (date(2026, 11, 13), "CP2027", "stamp_duty") not in att.index  # the buyer pays no CP stamp duty
    fill = res.fills.set_index("trade_id").loc["C1", "fill"]
    assert fill == pytest.approx(cp.price(7.25, date(2026, 11, 13), rounded=False))
    value = 5e8 / 100 * fill
    assert att.loc[(date(2026, 11, 13), "CP2027", "fees")] == pytest.approx(-2.5 * value / 1e7)  # per crore of value
    assert_identity(res)
    with pytest.raises(ValueError, match="T\\+0 trade in a gsec is refused"):
        rt.run_book([trade(price=97.43, settlement_date="2026-11-13")], flows=flows(),
                    market=rt.MarketData(marks=gs_marks), settings=settings(), end=days[-1])
    ok = rt.run_book([trade(price=97.43, settlement_date="2026-11-13")], flows=flows(),
                     market=rt.MarketData(marks=gs_marks), settings=settings(allow_t0=True), end=days[1])
    assert_identity(ok)


def test_switch_legs_pay_no_spread_or_ccil_charge():
    other = FixedRateBond(7.26, date(2029, 1, 14))
    days = CAL.business_days(date(2026, 11, 13), date(2026, 11, 18))
    y = GS2036.yield_from_price(97.43, date(2026, 11, 16))
    marks = pd.concat([marks_at(GS2036, "GS2036", y, days), marks_at(other, "GS2029", 6.60, days)])
    trades = [trade("T1", price=97.43), {**trade("W1", side="sell", day="2026-11-16", venue="switch",
                                                 price=97.45)},
              {**trade("W2", day="2026-11-16", venue="switch", price=101.20), "isin": "GS2029",
               "coupon_pct": 7.26, "maturity_date": "2029-01-14"}]
    res = rt.run_book(trades, flows=flows(3e9), market=rt.MarketData(marks=marks),
                      settings=settings(costs=co.read_cost_table()), end=days[-1])
    fills = res.fills.set_index("trade_id")
    assert fills.loc["W1", "fees"] == 0.0 and fills.loc["W2", "fees"] == 0.0
    assert "CS05:U" in fills.loc["W1", "rules"]
    held = res.positions[res.positions["date"] == days[-1]].set_index("isin")
    assert "GS2036" not in held.index and held.loc["GS2029", "face"] == 1e9
    assert_identity(res)


# ---------------------------------------------------------------- coupons, shorts (I7)


def test_coupon_on_a_saturday_is_income_on_monday():
    bond = FixedRateBond(7.10, date(2030, 6, 12))  # coupons 12 Jun and 12 Dec; 12 Dec 2026 is a Saturday
    days = CAL.business_days(date(2026, 12, 1), date(2026, 12, 16))
    marks = marks_at(bond, "GS2030", 6.80, days)
    res = rt.run_book([{"trade_id": "C1", "book": "b", "isin": "GS2030", "kind": "gsec", "coupon_pct": 7.10,
                        "maturity_date": "2030-06-12", "side": "buy", "face": 1e9, "trade_date": "2026-12-01",
                        "price": marks["clean_price"].iloc[0]}], flows=flows(2e9, "2026-12-01"),
                      market=rt.MarketData(marks=marks), settings=settings(costs=NOCOST), end=days[-1])
    by_day = lines(res)
    expected = U * (bond.accrued_interest(date(2026, 12, 14)) - bond.accrued_interest(date(2026, 12, 11)) + 3.55)
    assert by_day.loc[date(2026, 12, 14), "accrual"] == pytest.approx(expected)
    nav = res.nav.set_index("date")
    assert nav.loc[date(2026, 12, 14), "receivables"] == 0.0  # paid the same Monday
    assert nav.loc[date(2026, 12, 14), "cash"] - nav.loc[date(2026, 12, 11), "cash"] == pytest.approx(3.55 * U)
    assert by_day["accrual"].sum() == pytest.approx(U * (bond.accrued_interest(days[-1]) - bond.accrued_interest(
        date(2026, 12, 2)) + 3.55))
    assert_identity(res)


def _short_market(end=date(2026, 12, 16)):
    bond = FixedRateBond(7.10, date(2030, 6, 12))
    days = CAL.business_days(date(2026, 12, 1), end)
    return bond, days, marks_at(bond, "GS2030", 6.80, days)


def _short_trade(tid="S1", side="sell", day="2026-12-01", face=1e9, book="s", price=None, **kw):
    return {"trade_id": tid, "book": book, "isin": "GS2030", "kind": "gsec", "coupon_pct": 7.10,
            "maturity_date": "2030-06-12", "side": side, "face": face, "trade_date": day, "price": price,
            "special_repo_pct": 5.0, "liquidity": "liquid", **kw}


def test_short_through_a_coupon_under_bank_or_pd():
    bond, days, marks = _short_market()
    price = marks["clean_price"].iloc[0]
    res = rt.run_book([_short_trade(price=price)], flows=flows(1e8, "2026-12-01", "s"),
                      market=rt.MarketData(marks=marks, haircuts=HAIRCUTS),
                      settings=settings("bank_or_pd", costs=NOCOST), end=days[-1])
    by_day = lines(res, "s")
    assert by_day.loc[date(2026, 12, 14), "accrual"] < 0  # the coupon is paid away
    lent = res.funding.set_index("date").loc[date(2026, 12, 2), "lent_short"]
    dirty = price + bond.accrued_interest(date(2026, 12, 2))
    assert lent == pytest.approx(0.97 * dirty * U)  # (1 - h) of the sale consideration
    att = res.attribution.set_index(["date", "instrument", "line"])["amount"]
    assert att.loc[(date(2026, 12, 3), "GS2030", "cash_interest")] == pytest.approx(lent * 5.0 / 36500)
    assert_identity(res)
    with pytest.raises(ValueError, match="allows no short sales"):
        rt.run_book([_short_trade(price=price)], flows=flows(1e8, "2026-12-01", "s"),
                    market=rt.MarketData(marks=marks, haircuts=HAIRCUTS), settings=settings(costs=NOCOST),
                    end=days[-1])
    with pytest.raises(ValueError, match="exceeds the limit of Rs 500.00 crore"):
        rt.run_book([_short_trade(price=price, face=6e9)], flows=flows(1e9, "2026-12-01", "s"),
                    market=rt.MarketData(marks=marks, haircuts=HAIRCUTS),
                    settings=settings("bank_or_pd", costs=NOCOST), end=days[-1])
    with pytest.raises(ValueError, match="needs special_repo_pct"):
        rt.run_book([{**_short_trade(price=price), "special_repo_pct": ""}], flows=flows(1e8, "2026-12-01", "s"),
                    market=rt.MarketData(marks=marks, haircuts=HAIRCUTS),
                    settings=settings("bank_or_pd", costs=NOCOST), end=days[-1])
    bill = DiscountInstrument(date(2027, 2, 18))
    with pytest.raises(ValueError, match="short sales are allowed only in"):
        rt.run_book([{"trade_id": "B1", "book": "s", "isin": "TB2027", "kind": "tbill", "maturity_date": "2027-02-18",
                      "side": "sell", "face": 1e8, "trade_date": "2026-12-01", "price": bill.price(6.0, date(
                          2026, 12, 2)), "special_repo_pct": 5.0}], flows=flows(1e8, "2026-12-01", "s"),
                    market=rt.MarketData(marks=marks, haircuts=HAIRCUTS),
                    settings=settings("bank_or_pd", costs=NOCOST), end=days[-1])


def test_a_long_and_a_short_net_to_zero_on_every_market_line_i7():
    _, days, marks = _short_market()
    price = marks["clean_price"].iloc[0]
    trades = [_short_trade(price=price), _short_trade("L1", side="buy", book="l", price=price)]
    res = rt.run_book(trades, flows=pd.concat([flows(5e9, "2026-12-01", "s"), flows(5e9, "2026-12-01", "l")]),
                      market=rt.MarketData(marks=marks, haircuts=HAIRCUTS),
                      settings=settings("bank_or_pd", costs=NOCOST), end=days[-1])
    long, short = lines(res, "l"), lines(res, "s")
    for line in ("accrual", "pull_to_par", "price_unattributed", "convexity", "unexplained", "trading"):
        if line in long or line in short:
            assert (long.get(line, 0.0) + short.get(line, 0.0)).abs().max() < 1e-6, line
    fund = res.nav[res.nav["book"] == "fund"]
    assert len(fund) == len(days)
    by_book = res.nav[res.nav["book"] != "fund"].groupby("date")["nav"].sum()
    assert (fund.set_index("date")["nav"] - by_book).abs().max() < 1e-6


# ---------------------------------------------------------------- marks: stale, model, not yet public (I9)


def _gap_market(with_curves: bool):
    days = CAL.business_days(date(2026, 11, 13), date(2026, 11, 27))
    curves, marks = _curve_market(days, {"GS2036": GS2036})
    keep = marks[[d in (date(2026, 11, 13), date(2026, 11, 16), date(2026, 11, 27)) for d in marks["date"]]]
    return days, rt.MarketData(marks=keep, curves={"gsec": curves} if with_curves else {}), marks


def test_stale_marks_are_held_and_flagged():
    days, market, full = _gap_market(False)
    res = rt.run_book([trade(price=full["clean_price"].iloc[0])], flows=flows(), market=market,
                      settings=settings(costs=NOCOST), end=days[-1])
    pos = res.positions.set_index("date")
    held = full.set_index("date").loc[date(2026, 11, 16), "clean_price"]
    gap = [d for d in days if date(2026, 11, 16) < d < date(2026, 11, 27)]
    assert list(pos.loc[gap, "mark_source"]) == ["stale"] * len(gap)
    assert (pos.loc[gap, "clean_mark"] == held).all()  # the clean price held
    assert list(pos.loc[gap, "mark_age_days"]) == list(range(1, len(gap) + 1))
    assert list(pos.loc[gap, "stale_warning"]) == [k > 5 for k in range(1, len(gap) + 1)]
    warned = res.checks[res.checks["check"] == "stale_warning"]
    assert list(warned["date"]) == gap[5:]
    assert pos.loc[date(2026, 11, 27), "mark_source"] == "traded"
    assert_identity(res)


def test_model_marks_use_the_curve_and_the_last_residual():
    days, market, full = _gap_market(True)
    res = rt.run_book([trade(price=full["clean_price"].iloc[0])], flows=flows(), market=market,
                      settings=settings(costs=NOCOST), end=days[-1])
    pos = res.positions.set_index("date")
    d = date(2026, 11, 19)
    assert pos.loc[d, "mark_source"] == "model"
    curve = market.curves["gsec"][d]
    assert pos.loc[d, "yield_pct"] == pytest.approx(rt.model_yield(curve, GS2036, curve.valuation_date) + 0.05,
                                                   abs=1e-9)
    assert pos.loc[d, "clean_mark"] == pytest.approx(full.set_index("date").loc[d, "clean_price"], abs=1e-9)
    by_day = lines(res)
    assert by_day.loc[d].get("rich_cheap", 0.0) == pytest.approx(0.0, abs=1e-4)
    history = pd.DataFrame([{"date": date(2026, 11, 17), "isin": "GS2036", "resid_yield_bp": 7.0}])
    market2 = replace(market, residual_history=history, marks=market.marks[market.marks["date"] != date(2026, 11, 16)])
    res2 = rt.run_book([trade(price=full["clean_price"].iloc[0])], flows=flows(), market=market2,
                       settings=settings(costs=NOCOST), end=date(2026, 11, 17))
    first = res2.positions.set_index("date").loc[date(2026, 11, 16)]
    assert first["mark_source"] == "model"  # the history's 13 Nov residual (dated 17 Nov) is not yet usable: ours is
    assert_identity(res)


def test_a_mark_not_yet_public_is_not_used():
    y = GS2036.yield_from_price(97.43, date(2026, 11, 16))
    days = CAL.business_days(date(2026, 11, 13), date(2026, 11, 17))
    marks = marks_at(GS2036, "GS2036", y, days)
    marks["public_from"] = ""
    late = marks["date"] == date(2026, 11, 16)
    marks.loc[late, "public_from"] = "2026-11-17T09:00"
    marks.loc[late, "clean_price"] = 99.0
    res = rt.run_book([trade(price=97.43)], flows=flows(), market=rt.MarketData(marks=marks),
                      settings=settings(costs=NOCOST), end=days[-1])
    pos = res.positions.set_index("date")
    assert pos.loc[date(2026, 11, 16), "mark_source"] == "stale"
    assert pos.loc[date(2026, 11, 16), "clean_mark"] == pytest.approx(97.43)
    assert pos.loc[date(2026, 11, 17), "mark_source"] == "traded"
    never = rt.run_book([trade(price=97.43)], flows=flows(), market=rt.MarketData(marks=marks),
                        settings=settings(costs=NOCOST, marks=rt.MarkPolicy(default_time=None)), end=days[-1])
    assert set(never.positions["mark_source"]) <= {"fill", "stale"}  # rows without a time are never public


class _BuyOnce:
    """Buys Rs 100 crore of GS2036 on the first evening and records what each decision saw."""

    def __init__(self, isin="GS2036"):
        self.seen, self.isin = [], isin

    def decide(self, snapshot):
        marks = snapshot.marks(self.isin)
        self.seen.append((snapshot.day, snapshot.as_of, len(marks), float(marks["price"].iloc[-1])
                          if len(marks) else math.nan))
        if len(self.seen) == 1:
            return [rt.Order(book="main", isin=self.isin, side="buy", face=1e9)]
        return []


def test_backtest_sees_only_what_was_public_i9():
    days = CAL.business_days(date(2026, 11, 13), date(2026, 11, 24))
    y = GS2036.yield_from_price(97.43, date(2026, 11, 16))
    marks = marks_at(GS2036, "GS2036", y, days)
    marks["vwap"] = marks["clean_price"]
    marks["public_from"] = [f"{d.isoformat()}T18:00" for d in marks["date"]]
    poisoned = pd.concat([marks, pd.DataFrame([{"date": date(2026, 11, 20), "isin": "GS2036", "clean_price": 50.0,
                                                "source": "valuation", "public_from": "2026-11-23T09:00"}])])
    master = pd.DataFrame([TERMS])
    runs = []
    for frame in (marks, poisoned):
        strategy = _BuyOnce()
        res = rt.run_backtest(strategy, rt.MarketData(marks=frame, master=master, funding=funding(),
                                                      haircuts=HAIRCUTS),
                              settings(costs=co.read_cost_table()), start=days[0], end=days[-1], capital=2e9)
        runs.append((strategy.seen, res))
    clean, dirty = runs
    upto = [s for s in clean[0] if s[0] <= date(2026, 11, 20)]
    assert upto == [s for s in dirty[0] if s[0] <= date(2026, 11, 20)]  # nothing before its time changes
    assert clean[0][0][1] == datetime(2026, 11, 13, 20, 0)
    fill = clean[1].fills.iloc[0]
    assert fill["date"] == date(2026, 11, 16) and fill["status"] == "filled"  # the next business day
    vwap = marks.set_index("date").loc[date(2026, 11, 16), "vwap"]
    s = date(2026, 11, 17)
    # no CCIL class given: the illiquid half spread of 4.5 bp (HS04)
    assert fill["fill"] == pytest.approx(vwap + 4.5 * GS2036.dv01(GS2036.yield_from_price(vwap, s), s))
    assert "HS04:P" in fill["rules"]
    assert_identity(clean[1])


def test_orders_wait_for_a_traded_price_then_cancel():
    days = CAL.business_days(date(2026, 11, 13), date(2026, 11, 24))
    marks = marks_at(GS2036, "GS2036", 7.30, days, source="valuation")
    strategy = _BuyOnce()
    res = rt.run_backtest(strategy, rt.MarketData(marks=marks, master=pd.DataFrame([TERMS])),
                          settings(max_wait_days=2), start=days[0], end=days[-1], capital=2e9)
    row = res.fills.iloc[0]
    assert row["status"] == "cancelled" and row["date"] == date(2026, 11, 18)  # tried 16, 17 and 18 Nov
    assert "no traded price within 2 business day(s)" in row["notes"]
    allowed = rt.run_backtest(_BuyOnce(), rt.MarketData(marks=marks, master=pd.DataFrame([TERMS]), funding=funding(),
                                                        haircuts=HAIRCUTS),
                              settings(marks=rt.MarkPolicy(fill_sources=("traded", "valuation"))),
                              start=days[0], end=days[-1], capital=2e9)
    assert allowed.fills.iloc[0]["status"] == "filled"


# ---------------------------------------------------------------- determinism (I8), refusals, performance (R14)


def _det_inputs():
    days = CAL.business_days(date(2026, 11, 13), date(2026, 11, 25))
    other = FixedRateBond(7.26, date(2029, 1, 14))

    def shift(k):
        return (NSS[0] + 0.02 * k, *NSS[1:])

    curves, marks = _curve_market(days, {"GS2036": GS2036, "GS2029": other}, shift=shift,
                                  resid=lambda k, j: 0.03 * (j + 1) + 0.002 * k)
    trades = pd.DataFrame([trade(price=marks["clean_price"].iloc[0], liquidity="liquid"),
                           {**trade("T2", face=5e8, day="2026-11-16"), "isin": "GS2029", "coupon_pct": 7.26,
                            "maturity_date": "2029-01-14"},
                           trade("T3", side="sell", face=3e8, day="2026-11-20")])
    marks["vwap"] = marks["clean_price"]
    return days, curves, marks, trades


def _write(res, path):
    rt.write_result(res, path)
    return {p.name: p.read_bytes() for p in sorted(path.iterdir())}


def test_same_inputs_give_the_same_bytes_in_any_row_order_i8(tmp_path):
    days, curves, marks, trades = _det_inputs()
    market = rt.MarketData(marks=marks, curves={"gsec": curves}, funding=funding(), haircuts=HAIRCUTS)
    st = settings(costs=co.read_cost_table())
    first = _write(rt.run_book(trades, flows=flows(3e8), market=market, settings=st, end=days[-1]), tmp_path / "a")
    again = _write(rt.run_book(trades, flows=flows(3e8), market=market, settings=st, end=days[-1]), tmp_path / "b")
    shuffled = replace(market, marks=marks.sample(frac=1.0, random_state=7))
    third = _write(rt.run_book(trades.sample(frac=1.0, random_state=3), flows=flows(3e8), market=shuffled,
                               settings=st, end=days[-1]), tmp_path / "c")
    assert first == again == third
    assert set(first) == {f"{n}.csv" for n in rt.RESULT_FILES} | {"notes.txt"}


def test_refusals_name_what_was_wrong():
    days = CAL.business_days(date(2026, 11, 13), date(2026, 11, 17))
    marks = marks_at(GS2036, "GS2036", 7.3, days)
    market = rt.MarketData(marks=marks)
    st = settings(costs=NOCOST)

    def run(trades, **kw):
        return rt.run_book(trades, flows=kw.pop("flows", flows()), market=kw.pop("market", market),
                           settings=kw.pop("settings", st), end=kw.pop("end", days[-1]), **kw)

    with pytest.raises(TypeError, match="access must be an AccessPolicy"):
        rt.EngineSettings(access="nbfc_non_pd")
    with pytest.raises(ValueError, match="trade_id T1 is used twice"):
        run([trade(price=97.0), trade(price=97.0)])
    with pytest.raises(ValueError, match="trade date 2026-11-10 is not a Mumbai business day"):
        run([trade(price=97.0, day="2026-11-10")], flows=flows(day="2026-11-09"), start=date(2026, 11, 9))
    with pytest.raises(ValueError, match="settlement 2026-11-14 is not a Mumbai business day"):
        run([trade(price=97.0, settlement_date="2026-11-14")])
    with pytest.raises(ValueError, match="before GS2036's issue date"):
        run([trade(price=97.0, issue_date="2026-12-01")])
    with pytest.raises(ValueError, match="no pricer in bonds.py"):
        run([{**trade(price=97.0), "isin": "FRB2034", "kind": "frb"}])
    with pytest.raises(ValueError, match="kind 'bond' is not one of"):
        run([{**trade(price=97.0), "kind": "bond"}])
    with pytest.raises(ValueError, match="no price given and no traded price of GS2029 on 2026-11-13"):
        run([{**trade(), "isin": "GS2029", "coupon_pct": 7.26, "maturity_date": "2029-01-14", "price": ""}])
    with pytest.raises(ValueError, match="give a price or a yield_pct, not both"):
        run([trade(price=97.0, yield_pct=7.3)])
    with pytest.raises(ValueError, match="'s coupon_pct is 7.0 in the trades but"):
        run([trade(price=97.0, coupon_pct=7.0)], market=replace(market, master=pd.DataFrame([TERMS])))
    with pytest.raises(ValueError, match="flows line 2: 2026-11-14 is not a Mumbai business day"):
        run([trade(price=97.0)], flows=flows(day="2026-11-14"))
    with pytest.raises(ValueError, match="fall before the run's start"):
        run([trade(price=97.0)], start=date(2026, 11, 16))
    with pytest.raises(ValueError, match="needs a brokerage rule"):
        run([trade(price=97.0, broker="XYZ")])
    with pytest.raises(ValueError, match="side must be buy or sell"):
        run([trade(price=97.0, side="hold")])
    with pytest.raises(ValueError, match="no haircut table"):
        run([trade(price=97.0)], flows=flows(1e8), market=replace(market, funding=funding()))
    with pytest.raises(ValueError, match="no triparty funding rates"):
        run([trade(price=97.0, liquidity="liquid")], flows=flows(1e8), market=replace(market, haircuts=HAIRCUTS))
    with pytest.raises(ValueError, match="no market_repo funding rates"):  # unclassified: illiquid, no TREPS
        run([trade(price=97.0)], flows=flows(1e8), market=replace(market, haircuts=HAIRCUTS))
    with pytest.raises(ValueError, match="no Mumbai business day from"):
        rt.run_book(market=market, settings=st, start=date(2026, 11, 14), end=date(2026, 11, 15))
    with pytest.raises(ValueError, match="nothing to run"):
        rt.run_book(market=market, settings=st, end=date(2026, 11, 15))


def test_ois_access_rules(curve19):
    curves = {d: rt.rolled(curve19, d) for d in (date(2026, 11, 19), date(2026, 11, 20))}
    swap = {"trade_id": "S1", "book": "b", "trade_date": "2026-11-19", "months": 48, "fixed_rate_pct": 6.12,
            "notional": 1e9, "pay_fixed": True, "purpose": "view"}
    market = rt.MarketData(ois_curves=curves, fixings={date(2026, 11, 20): 5.60})
    with pytest.raises(ValueError, match="allows OIS for hedging only"):
        rt.run_book(swaps=[swap], flows=flows(1e7, "2026-11-19"), market=market,
                    settings=settings("unregulated_company", project_calendar=True), end=date(2026, 11, 20))
    with pytest.raises(ValueError, match="does not hedge its bonds"):
        rt.run_book(swaps=[{**swap, "purpose": "hedge"}], flows=flows(1e7, "2026-11-19"), market=market,
                    settings=settings("unregulated_company", project_calendar=True), end=date(2026, 11, 20))
    with pytest.raises(ValueError, match="no OIS curve on 2026-11-19"):
        rt.run_book(swaps=[swap], flows=flows(1e7, "2026-11-19"), market=rt.MarketData(),
                    settings=settings(project_calendar=True), end=date(2026, 11, 20))


def test_performance_twr_drawdown_r14():
    nav = pd.DataFrame({"date": CAL.business_days(date(2026, 11, 16), date(2026, 11, 20)), "book": "x",
                        "nav": [100.0, 101.0, 111.5, 109.0, 112.0], "flow": [0.0, 0.0, 10.0, 0.0, 0.0]})
    perf = rt.performance(nav).iloc[0]
    assert perf["twr"] == pytest.approx(0.019099099, abs=1e-9)
    assert perf["max_drawdown"] == pytest.approx(-0.022421525, abs=1e-9)
    assert perf["business_days"] == 4
    assert perf["drawdown_trough"] == date(2026, 11, 19) and perf["drawdown_recovery"] == date(2026, 11, 20)
    dd = rt.drawdowns(nav)
    assert dd["index"].iloc[1:].round(10).tolist() == [1.01, 1.0145495495, 0.9918018018, 1.0190990991]
    rets = dd["index"].pct_change().iloc[1:]
    assert rets.round(9).tolist() == [0.01, 0.004504505, -0.022421525, 0.027522936]
    assert perf["sharpe"] == pytest.approx(rets.mean() / rets.std(ddof=1) * math.sqrt(perf["obs_per_year"]))


def test_calendar_projection_is_refused_or_flagged():
    from cr_strat.calendars import CalendarCoverageError
    days = CAL.business_days(date(2026, 12, 28), date(2026, 12, 31)) + LEN.business_days(date(2027, 1, 1),
                                                                                         date(2027, 1, 6))
    marks = pd.DataFrame([{"date": d, "isin": "GS2036", "clean_price": GS2036.clean_price(
        7.3, LEN.settlement_date(d, 1))} for d in days])
    market = rt.MarketData(marks=marks)
    trades = [trade(price=marks["clean_price"].iloc[0], day="2026-12-28")]
    with pytest.raises(CalendarCoverageError):
        rt.run_book(trades, flows=flows(day="2026-12-28"), market=market, settings=settings(costs=NOCOST),
                    end=date(2027, 1, 6))
    res = rt.run_book(trades, flows=flows(day="2026-12-28"), market=market,
                      settings=settings(costs=NOCOST, project_calendar=True), end=date(2027, 1, 6))
    nav = res.nav.set_index("date")
    # 31 Dec 2026 is a covered day, but its T+1 mark settlement and funding night fall on projected 2027 days
    assert not nav.loc[date(2026, 12, 30), "calendar_projected"]
    assert nav.loc[date(2026, 12, 31), "calendar_projected"] and nav.loc[date(2027, 1, 4), "calendar_projected"]
    assert any("calendar_projected" in n for n in res.notes)
    assert_identity(res)


def test_key_rates_and_the_excess_basis():
    days = CAL.business_days(date(2026, 11, 13), date(2026, 11, 20))

    def shift(k):
        return (NSS[0] + 0.05 * (k >= 3), *NSS[1:])

    curves, marks = _curve_market(days, {"GS2036": GS2036}, shift=shift)
    market = rt.MarketData(marks=marks, curves={"gsec": curves}, funding=funding(), haircuts=HAIRCUTS)
    key = rt.run_book([trade(price=marks["clean_price"].iloc[0])], flows=flows(), market=market,
                      settings=settings(costs=NOCOST, attribution=rt.AttributionSettings(factor_basis="key_rates")),
                      end=days[-1])
    day = lines(key).loc[days[3]]
    krd = [c for c in day.index if c.startswith("krd_")]
    assert krd and all(day[c] < 0 for c in krd if abs(day[c]) > 1e-6)  # rates up at every node: losses
    assert_identity(key)
    excess = rt.run_book([trade(price=marks["clean_price"].iloc[0])], flows=flows(), market=market,
                         settings=settings(costs=NOCOST,
                                           attribution=rt.AttributionSettings(basis="excess_of_cash")),
                         end=days[-1])
    by_day = lines(excess)
    nav = excess.nav.set_index("date")
    d, p = days[2], days[1]
    assert by_day.loc[d, "cash_benchmark"] == pytest.approx(-nav.loc[p, "nav"] * 5.57 / 36500)
    assert nav.loc[d, "rf_return"] == pytest.approx(5.57 / 36500)
    assert_identity(excess)


def test_loaders_for_curves_and_prices(tmp_path):
    path = tmp_path / "nss.csv"
    pd.DataFrame([{"date": "2026-11-16", "beta0": 7.6, "beta1": -2, "beta2": -1, "beta3": 2, "tau1": 1.5, "tau2": 8,
                   "t_min": 0.25, "t_max": 40, "public_from": "2026-11-16T19:00"}]).to_csv(path, index=False)
    curves = rt.read_nss_params(path)
    entry = curves["gsec"][date(2026, 11, 16)]
    assert entry.curve.valuation_date == date(2026, 11, 17) and entry.public_from == datetime(2026, 11, 16, 19)
    with pytest.raises(ValueError, match="lacks"):
        rt.read_nss_params(pd.DataFrame([{"date": "2026-11-16", "beta0": 7.6}]))
    prices = pd.DataFrame([{"isin": "GS2036", "clean_price": 97.43, "traded": "yes", "volume_cr": 500},
                           {"isin": "GS2029", "yield_pct": 6.6, "traded": "no"}])
    marks = rt.marks_from_prices(prices, "2026-11-16", public_from="2026-11-16T18:30")
    assert list(marks["source"]) == ["traded", "valuation"]
    assert list(marks.columns) == list(rt.MARK_COLUMNS)
    assert marks["public_from"].iloc[0] == datetime(2026, 11, 16, 18, 30)


# ---------------------------------------------------------------- the command line


def _cli_files(tmp_path):
    days, curves, marks, trades = _det_inputs()
    store = tmp_path / "store"
    (store / "derived").mkdir(parents=True)
    frame = mmd(date(2026, 11, 1), date(2026, 11, 30))
    frame.assign(public_from=frame["public_from"].map(lambda t: t.strftime("%Y-%m-%dT%H:%M:%S"))).to_csv(
        store / "derived" / "money_market_daily.csv", index=False)
    trades.to_csv(tmp_path / "trades.csv", index=False)
    marks.to_csv(tmp_path / "marks.csv", index=False)
    flows(3e8).to_csv(tmp_path / "flows.csv", index=False)
    HAIRCUTS.to_csv(tmp_path / "haircuts.csv", index=False)
    pd.DataFrame([{"date": d, "settlement_date": c.valuation_date, **dict(zip(cf.NSS_PARAMS, c.params)),
                   "t_min": c.t_min, "t_max": c.t_max} for d, c in curves.items()]).to_csv(tmp_path / "nss.csv",
                                                                                         index=False)
    args = ["returns", "--trades", str(tmp_path / "trades.csv"), "--flows", str(tmp_path / "flows.csv"),
            "--marks", str(tmp_path / "marks.csv"), "--nss-params", str(tmp_path / "nss.csv"),
            "--store", str(store), "--haircuts", str(tmp_path / "haircuts.csv"), "--access", "nbfc_non_pd",
            "--end", days[-1].isoformat()]
    return days, curves, marks, trades, args


def test_cli_returns_end_to_end(tmp_path, capsys):
    from cr_strat.cli import main
    days, curves, marks, trades, args = _cli_files(tmp_path)
    assert main([*args, "--out-dir", str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    assert "identity breaches: 0" in out and "book b:" in out
    written = {p.name for p in (tmp_path / "out").iterdir()}
    assert {"positions.csv", "nav.csv", "attribution.csv", "fills.csv", "funding.csv", "checks.csv",
            "performance.csv"} <= written
    nav = pd.read_csv(tmp_path / "out" / "nav.csv")
    market = rt.MarketData(marks=marks, curves={"gsec": curves}, funding=funding(date(2026, 11, 1),
                                                                                  date(2026, 11, 30)),
                           haircuts=HAIRCUTS)
    direct = rt.run_book(trades, flows=flows(3e8), market=market, settings=settings(), end=days[-1])
    assert nav["nav"].tolist() == pytest.approx(direct.nav["nav"].tolist(), rel=1e-12)
    att = pd.read_csv(tmp_path / "out" / "attribution.csv")
    assert {"curve_parallel", "roll_down", "spread_cost", "funding_repo"} <= set(att["line"])
    assert main([*args, "--out-dir", str(tmp_path / "again")]) == 0
    for name in written:
        assert (tmp_path / "out" / name).read_bytes() == (tmp_path / "again" / name).read_bytes(), name
    assert main([*args[:2], str(tmp_path / "missing.csv"), *args[3:], "--out-dir", str(tmp_path / "x")]) == 2
    assert "not found" in capsys.readouterr().err
    assert main([*args, "--start", "13-11-2026", "--out-dir", str(tmp_path / "y")]) == 2
    only_cash = ["returns", "--flows", str(tmp_path / "flows.csv"), "--marks", str(tmp_path / "marks.csv"),
                 "--access", "nbfc_non_pd", "--end", "2026-11-16", "--out-dir", str(tmp_path / "z")]
    assert main(only_cash) == 1  # nothing to value
    with pytest.raises(SystemExit):
        main(["returns", "--marks", str(tmp_path / "marks.csv"), "--end", "2026-11-16", "--out-dir", "o"])


# ---------------------------------------------------------------- partial closes and an S1 asset swap


def test_partial_close_keeps_the_rest_settled():
    y = GS2036.yield_from_price(97.43, date(2026, 11, 16))
    days = CAL.business_days(date(2026, 11, 13), date(2026, 11, 20))
    marks = marks_at(GS2036, "GS2036", y, days)
    trades = [trade(price=97.43), trade("T2", side="sell", face=4e8, day="2026-11-17", price=97.50)]
    res = rt.run_book(trades, flows=flows(), market=rt.MarketData(marks=marks), settings=settings(costs=NOCOST),
                      end=days[-1])
    pos = res.positions.set_index(["date", "state"])
    assert pos.loc[(date(2026, 11, 17), "settled"), "face"] == 1e9
    assert pos.loc[(date(2026, 11, 17), "pending"), "face"] == -4e8
    assert pos.loc[(date(2026, 11, 18), "settled"), "face"] == pytest.approx(6e8)
    mark17 = marks.set_index("date").loc[date(2026, 11, 17), "clean_price"]
    att = res.attribution.set_index(["date", "instrument", "line"])["amount"]
    assert att.loc[(date(2026, 11, 17), "GS2036", "trading")] == pytest.approx(-4e6 * (mark17 - 97.50))
    nav = res.nav.set_index("date")
    cash_in = 4e6 * (97.50 + GS2036.accrued_interest(date(2026, 11, 18)))
    assert nav.loc[date(2026, 11, 18), "cash"] - nav.loc[date(2026, 11, 17), "cash"] == pytest.approx(cash_in)
    assert_identity(res)


def test_an_asset_swap_hedged_with_ois(curve19):
    from cr_strat.asset_swaps import hedge_notional
    days = [date(2026, 11, 19), date(2026, 11, 20), date(2026, 11, 23)]
    ois_curves = {d: rt.rolled(curve19, d) for d in days}
    gcurves, marks = _curve_market(days, {"GS2036": GS2036})
    s = date(2026, 11, 20)
    clean = marks.set_index("date").loc[days[0], "clean_price"]
    notional = hedge_notional(GS2036, s, clean, ois_curves[days[0]], 48, face=1e9, calendar=LEN)
    swap = {"trade_id": "H1", "book": "b", "trade_date": "2026-11-19", "months": 48, "notional": notional,
            "pay_fixed": True, "purpose": "hedge"}
    market = rt.MarketData(marks=marks, curves={"gsec": gcurves}, ois_curves=ois_curves,
                           fixings={date(2026, 11, 20): 5.60}, funding=funding(), haircuts=HAIRCUTS)
    listed = replace(rt.ACCESS_PRESETS["unregulated_company"], name="listed_company", repo=True, treps=True)
    st = rt.EngineSettings(access=listed, project_calendar=True, costs=NOCOST)
    res = rt.run_book([trade(price=clean, day="2026-11-19", liquidity="liquid")], [swap], flows(2e8, "2026-11-19"),
                      market=market, settings=st, end=days[-1])
    nav = res.nav.set_index("date")
    bonds_dv01 = res.positions[(res.positions["kind"] == "gsec")].set_index("date")["dv01"]
    assert abs(nav.loc[days[-1], "dv01"]) < 0.05 * bonds_dv01.loc[days[-1]]  # the swap offsets the bond's DV01
    by_day = lines(res)
    assert {"accrual", "roll_down", "funding_repo", "ois_carry", "ois_roll_down"} <= set(by_day.columns)
    assert by_day.loc[days[-1], "ois_carry"] < 0  # paying 6.1% fixed against a 5.6% fixing
    assert_identity(res)
    with pytest.raises(ValueError, match="does not hedge"):  # twice the notional over-hedges the bond
        rt.run_book([trade(price=clean, day="2026-11-19", liquidity="liquid")], [{**swap, "notional": 2 * notional}],
                    flows(2e8, "2026-11-19"), market=market, settings=st, end=days[-1])


# ---------------------------------------------------------------- the review fix pass (findings of 19 Nov 2026)


def test_a_decision_before_the_valuation_time_is_refused_and_an_early_view_sees_no_later_mark():
    with pytest.raises(ValueError, match="decision_time 18:00 is before valuation_time 20:00"):
        settings(decision_time=time(18, 0))
    days = CAL.business_days(date(2026, 11, 16), date(2026, 11, 20))
    marks = pd.DataFrame([{"date": d, "isin": "GS2036", "clean_price": GS2036.clean_price(
        7.30 + 0.05 * k, CAL.settlement_date(d, 1)), "public_from": datetime.combine(d, time(19, 0))}
        for k, d in enumerate(days)])
    seen = []

    class Spy:
        def decide(self, snap):
            pos = snap.positions
            public = snap.marks("GS2036")
            if not pos.empty:
                seen.append((float(pos["clean_mark"].iloc[0]), float(public.sort_values("date")["price"].iloc[-1])))
            return [rt.Order(book="main", side="buy", face=1e8, isin="GS2036")] if snap.day == days[0] else []

    st = settings(costs=NOCOST, decision_time=time(18, 0), valuation_time=time(18, 0))
    rt.run_backtest(Spy(), rt.MarketData(marks=marks, master=pd.DataFrame([TERMS])), st, start=days[0],
                    end=days[-1], capital=5e8)
    # 17 Nov: the position is the fund's own fill of that day; from 18 Nov the 19:00 marks value it a day late
    assert len(seen) == 4 and all(abs(mark - public) < 1e-9 for mark, public in seen[1:])


def _ois_mm_curves(days, months):
    out = {}
    for k, d in enumerate(days):
        dates = [d] + [ois.add_months(d, m) for m in months]
        t = [(x - d).days / 365.0 for x in dates]
        out[d] = ois.OisCurve(valuation_date=d, times=tuple(t), log_dfs=tuple(-(5.9 + 0.02 * k) / 100 * x for x in t),
                              node_dates=tuple(dates))
    return out


def test_money_market_ois_curves_attribute_a_cp_and_a_swap():
    days = CAL.business_days(date(2026, 11, 13), date(2026, 11, 18))
    cp = DiscountInstrument(date(2027, 2, 12), CP)
    marks = pd.DataFrame([{"date": d, "isin": "CP1", "clean_price": cp.price(6.5, CAL.settlement_date(d, 1),
                                                                             rounded=False)} for d in days])
    trade_cp = [{"trade_id": "C", "book": "b", "isin": "CP1", "kind": "cp", "maturity_date": "2027-02-12",
                 "side": "buy", "face": 5e8, "trade_date": "2026-11-13", "price": marks["clean_price"].iloc[0],
                 "venue": "otc"}]
    for months, short in (((1, 3, 6, 9, 12), True), ((1, 3, 6, 9, 12, 24), False)):
        res = rt.run_book(trade_cp, flows=flows(6e8), market=rt.MarketData(
            marks=marks, ois_curves=_ois_mm_curves(days, months)), settings=settings(costs=NOCOST), end=days[-1])
        assert_identity(res)
        day = lines(res).loc[date(2026, 11, 16)]
        assert abs(day.get("curve_parallel", 0.0)) > 100  # the 2 bp rise of the curve is attributed
        for line in ("curve_slope", "curve_curvature"):
            assert day.get(line, 0.0) == 0.0  # a parallel move: no slope or curvature, fitted or not
        # to 1 year the nodes from 0.5 years support fewer shapes than three: the leading ones are fitted, noted
        assert any(n.startswith("curve factors: on 3 day(s)") for n in res.notes) == short
    swap = [{"trade_id": "S", "book": "b", "trade_date": "2026-11-13", "months": 6, "notional": 1e9,
             "pay_fixed": "true", "purpose": "view"}]
    res = rt.run_book(None, swap, flows(1e7), market=rt.MarketData(
        ois_curves=_ois_mm_curves(days, (1, 3, 6, 9, 12)), fixings={d: 5.6 for d in days}),
        settings=settings(costs=NOCOST, project_calendar=True), end=days[-1])
    assert_identity(res)
    assert "ois_curve_parallel" in lines(res).columns
    assert any(n.startswith("curve factors: on ") for n in res.notes)  # the swap's 1M-12M curve, noted
    report = {}
    sizes = rt.factor_moves(_ois_mm_curves(days, (1,))[days[1]], _ois_mm_curves(days, (1,))[days[0]],
                            report=report)
    assert report == {"usable_nodes": 0, "fitted": 0} and set(sizes.values()) == {0.0}


def _saturday_book(**policy):
    days = CAL.business_days(date(2026, 9, 28), date(2026, 10, 6))
    frame = mmd(date(2026, 9, 1), date(2026, 10, 31), overrides={date(2026, 10, 1): 5.90})
    frame.loc[frame["as_on_date"] == date(2026, 10, 3), ["triparty_rate_pct", "market_repo_rate_pct"]] = 6.00
    frame.loc[frame["as_on_date"] == date(2026, 10, 3), ["triparty_volume_cr", "market_repo_volume_cr"]] = 1e4
    fund = {"triparty": fu.funding_rates(frame, "triparty"), "market_repo": fu.funding_rates(frame, "market_repo")}
    marks = marks_at(GS2036, "GS2036", 7.3, days)
    res = rt.run_book([trade(day="2026-09-28", price=float(marks["clean_price"].iloc[0]), liquidity="liquid")],
                      flows=flows(1e8, "2026-09-28"), market=rt.MarketData(marks=marks, funding=fund,
                                                                           haircuts=HAIRCUTS),
                      settings=settings(costs=NOCOST, funding=rt.FundingPolicy(**policy)), end=date(2026, 10, 6))
    borrowed = float(res.funding.set_index("date").loc[date(2026, 10, 1), "borrowed"])
    return -float(lines(res).loc[date(2026, 10, 5), "funding_repo"]), borrowed


def test_an_overnight_deal_bears_its_deal_day_rate_over_a_dealing_saturday():
    deal, borrowed = _saturday_book()  # Thu 1 Oct to Mon 5 Oct 2026 (2 Oct a holiday), Saturday 3 Oct dealt at 6%
    assert deal == pytest.approx(borrowed * 4 * 5.90 / 36500, rel=1e-12)
    calendar, borrowed2 = _saturday_book(rate_basis="calendar_day")
    assert calendar == pytest.approx(borrowed2 * (5.90 + 5.90 + 6.00 + 6.00) / 36500, rel=1e-12)
    with pytest.raises(ValueError, match="rate_basis must be one of"):
        rt.FundingPolicy(rate_basis="weekly")


def _three_bond_book(end=date(2026, 11, 27), funding_end=date(2026, 11, 20), **policy):
    days = CAL.business_days(date(2026, 11, 13), end)
    secs = {"A": (6.94, date(2036, 5, 11), 7.30), "B": (7.10, date(2035, 6, 12), 7.20),
            "C": (7.30, date(2042, 6, 19), 7.40)}
    rows = []
    for k, d in enumerate(days):
        for isin, (c, m, y) in secs.items():
            rows.append({"date": d, "isin": isin, "clean_price": FixedRateBond(c, m).clean_price(
                y + 0.03 * k * (1 if isin == "A" else -1), CAL.settlement_date(d, 1))})
    marks = pd.DataFrame(rows)
    first = marks[marks["date"] == days[0]].set_index("isin")["clean_price"]
    trades = [{"trade_id": f"T{i}", "book": "b", "isin": i, "kind": "gsec", "coupon_pct": c,
               "maturity_date": m.isoformat(), "side": "buy", "face": 1e9, "trade_date": days[0].isoformat(),
               "price": first[i], "liquidity": "liquid"} for i, (c, m, _) in secs.items()]
    return rt.run_book(trades, flows=flows(2e8), market=rt.MarketData(
        marks=marks, haircuts=HAIRCUTS, funding=funding(end=funding_end)),
        settings=settings(costs=NOCOST, funding=rt.FundingPolicy(**policy)), end=end)


def test_positions_funded_sums_to_the_night_borrowing_and_the_risk_free_rate_is_held_only_when_asked():
    res = _three_bond_book(projection="last")
    pos = res.positions[res.positions["state"] == "settled"]
    borrowed = res.funding.groupby("date")["borrowed"].sum()
    for d, group in pos.groupby("date"):
        assert group["funded"].sum() == pytest.approx(borrowed.loc[d], rel=1e-12)
    assert any("night(s) of the risk-free rate (triparty) held at the last rate" in n for n in res.notes)
    with pytest.raises(ValueError, match="the risk-free rate .triparty. for the night of 2026-11-23"):
        rt.run_book(None, None, flows(1e8), market=rt.MarketData(funding=funding(end=date(2026, 11, 20))),
                    settings=settings(costs=NOCOST), start=date(2026, 11, 13), end=date(2026, 11, 27))


def test_the_projection_notes_count_calendar_nights_once_whatever_the_books():
    """A funding table ending Fri 20 Nov 2026 and a run to Fri 27 Nov (24 Nov a holiday): the deals of 23, 25 and 26 Nov
    are held at the last rate, four calendar nights (23 to 26 Nov), for the funding and the risk-free rate alike, and
    two books borrowing over the same nights do not double them. Each calendar day priced alone, the weekend after
    the table's last day is held too."""
    def notes(res):
        return "\n".join(res.notes)

    held = notes(_three_bond_book(projection="last"))
    assert "4 night(s) of funding held at the last rate of a funding table, from 2026-11-23" in held
    assert "4 night(s) of the risk-free rate (triparty) held at the last rate of its table, from 2026-11-23" in held
    held = notes(_three_bond_book(projection="last", rate_basis="calendar_day"))
    assert "6 night(s) of funding held at the last rate of a funding table, from 2026-11-21" in held
    assert "6 night(s) of the risk-free rate (triparty) held at the last rate of its table, from 2026-11-21" in held
    days = CAL.business_days(date(2026, 11, 13), date(2026, 11, 27))
    two = rt.run_book([trade(price=97.4, liquidity="liquid"), trade("T2", price=97.4, liquidity="liquid", book="c")],
                      flows=pd.concat([flows(2e8), flows(2e8, book="c")]), market=rt.MarketData(
                          marks=marks_at(GS2036, "GS2036", 7.3, days), haircuts=HAIRCUTS,
                          funding=funding(end=date(2026, 11, 20))),
                      settings=settings(costs=NOCOST, funding=rt.FundingPolicy(projection="last")),
                      end=date(2026, 11, 27))
    assert set(two.funding["book"]) == {"b", "c"} and (two.funding["borrowed"] > 0).all()
    held = notes(two)
    assert "4 night(s) of funding held at the last rate of a funding table, from 2026-11-23" in held
    assert "4 night(s) of the risk-free rate (triparty)" in held


def test_a_drawdown_from_the_starting_capital_starts_before_the_first_return():
    frame = pd.DataFrame({"date": CAL.business_days(date(2026, 11, 16), date(2026, 11, 20)), "book": "b",
                          "nav": [98.0, 97.0, 99.0, 101.0, 100.0], "flow": [100.0, 0, 0, 0, 0]})
    frame["return"] = [-0.02, -1 / 98, 2 / 97, 2 / 99, -1 / 101]
    perf = rt.performance(frame).iloc[0]
    assert perf["max_drawdown"] == pytest.approx(-0.03, abs=1e-12)
    assert perf["drawdown_peak"] == date(2026, 11, 16) and perf["drawdown_trough"] == date(2026, 11, 17)
    assert perf["drawdown_days"] == 2 and perf["drawdown_recovery"] == date(2026, 11, 19)
    assert "starting capital at the start of 2026-11-16" in perf["notes"]


def test_a_trade_settling_on_a_redemption_rolled_back_to_its_payment_day_is_refused():
    terms = {"isin": "GS2026X", "kind": "gsec", "coupon_pct": 7.0, "maturity_date": "2026-12-06",  # a Sunday
             "issue_date": "2016-12-06"}
    days = CAL.business_days(date(2026, 11, 25), date(2026, 12, 10))
    marks = pd.DataFrame([{"date": d, "isin": "GS2026X", "yield_pct": 5.6} for d in days])
    for side in ("buy", "sell"):
        with pytest.raises(ValueError, match="on or after GS2026X's redemption payment on 2026-12-04"):
            rt.run_book([{"trade_id": "T1", "book": "b", **terms, "side": "buy", "face": 1e8,
                          "trade_date": "2026-11-25", "price": 100.0},
                         {"trade_id": "T2", "book": "b", **terms, "side": side, "face": 1e8,
                          "trade_date": "2026-12-03", "price": 100.0}],
                        flows=flows(5e8, "2026-11-25"), market=rt.MarketData(marks=marks, haircuts=HAIRCUTS),
                        settings=settings(costs=NOCOST), end=date(2026, 12, 10))
    with pytest.raises(ValueError, match="on or after CPX's redemption payment on 2026-12-04"):
        rt.run_book([{"trade_id": "C1", "book": "b", "isin": "CPX", "kind": "cp", "maturity_date": "2026-12-06",
                      "side": "buy", "face": 1e8, "trade_date": "2026-12-04", "price": 99.99,
                      "settlement_date": "2026-12-04", "venue": "otc"}], flows=flows(5e8, "2026-12-04"),
                    market=rt.MarketData(marks=pd.DataFrame([{"date": date(2026, 12, 3), "isin": "CPX",
                                                              "clean_price": 99.9}])),
                    settings=settings(costs=NOCOST), end=date(2026, 12, 8))
    held = rt.run_book([{"trade_id": "T1", "book": "b", **terms, "side": "buy", "face": 1e8,
                         "trade_date": "2026-11-25", "price": 100.0}], flows=flows(5e8, "2026-11-25"),
                       market=rt.MarketData(marks=marks), settings=settings(costs=NOCOST), end=date(2026, 12, 10))
    assert held.nav.set_index("date").loc[date(2026, 12, 4), "bonds"] == 0.0  # redeemed on Friday 4 Dec


def test_market_repo_charges_are_on_the_collateral_face():
    days = CAL.business_days(date(2026, 11, 13), date(2026, 11, 18))
    marks = marks_at(GS2036, "GS2036", 7.30, days)
    table = co.read_cost_table()
    for st in (rt.EngineSettings(access=rt.ACCESS_PRESETS["nbfc_non_pd"],
                                 funding=rt.FundingPolicy(segment="market_repo")),
               rt.EngineSettings(access=rt.AccessPolicy(name="listed", repo=True, treps=False, short_sale=False,
                                                        ois="hedge_only"))):
        res = rt.run_book([trade(price=97.4)], flows=flows(1e8), market=rt.MarketData(
            marks=marks, haircuts=HAIRCUTS, funding=funding()), settings=st, end=date(2026, 11, 18))
        row = res.funding.iloc[0]
        assert row["segment"] == "market_repo"
        expected = co.repo_deal_costs(table, segment="market_repo", face=1e9 * row["borrowed"] / row["limit"],
                                      cash=row["borrowed"], repayment=row["borrowed"], deal_date=row["date"]).total
        assert row["charges"] == pytest.approx(expected, rel=1e-12) and row["charges"] > 2000


def test_a_when_issued_purchase_is_a_forward_to_the_issue_date():
    terms = {"isin": "GSNEW36", "kind": "gsec", "coupon_pct": 6.80, "maturity_date": "2036-11-23",
             "issue_date": "2026-11-23"}
    days = CAL.business_days(date(2026, 11, 16), date(2026, 11, 25))
    marks = pd.DataFrame([{"date": d, "isin": "GSNEW36", "yield_pct": 6.80 + 0.01 * k} for k, d in enumerate(days)])
    wi = {"trade_id": "W1", "book": "b", **terms, "side": "buy", "face": 1e8, "trade_date": "2026-11-16",
          "price": 100.0, "venue": "when_issued", "notified_cr": 10000}
    res = rt.run_book([dict(wi, settlement_date="2026-11-23")], flows=flows(2e8, "2026-11-16"),
                      market=rt.MarketData(marks=marks, haircuts=HAIRCUTS, funding=funding()),
                      settings=settings(costs=NOCOST), end=date(2026, 11, 25))
    assert_identity(res)
    pend = res.positions[res.positions["state"] == "pending"].set_index("date")
    bond = FixedRateBond(6.80, date(2036, 11, 23), issue_date=date(2026, 11, 23))
    assert pend.loc[date(2026, 11, 17), "value"] == pytest.approx(
        1e6 * (bond.clean_price(6.81, date(2026, 11, 23)) - 100.0), abs=1e-6)  # restated to the issue date
    same = rt.run_book([wi], flows=flows(2e8, "2026-11-16"), market=rt.MarketData(marks=marks, haircuts=HAIRCUTS,
                       funding=funding()), settings=settings(costs=NOCOST), end=date(2026, 11, 25))
    assert same.fills["settlement_date"].iloc[0] == date(2026, 11, 23)  # defaults to the new issue's issue date


def test_a_run_may_end_on_the_last_day_of_the_holiday_list():
    days = CAL.business_days(date(2026, 12, 21), date(2026, 12, 31))
    marks = pd.concat([marks_at(GS2036, "GS2036", 7.30, days[:-1]),
                       pd.DataFrame([{"date": date(2026, 12, 31), "isin": "GS2036", "clean_price": 97.5,
                                      "settlement_date": "2027-01-01"}])])
    for trades in (None, [trade(face=1e8, day="2026-12-21", price=97.4)]):
        res = rt.run_book(trades, flows=flows(2e8, "2026-12-21"), market=rt.MarketData(
            marks=marks, haircuts=HAIRCUTS, funding=funding()), settings=settings(costs=NOCOST),
            end=date(2026, 12, 31))
        nav = res.nav.set_index("date")
        assert nav.loc[date(2026, 12, 31), "calendar_projected"] and not nav.loc[date(2026, 12, 30),
                                                                                 "calendar_projected"]
        assert any("inside the holiday list's years (2026-12-31)" in n for n in res.notes)
        assert_identity(res)


def test_trades_without_a_time_and_flows_of_one_day_do_not_depend_on_ids_or_row_order(tmp_path):
    marks = marks_at(GS2036, "GS2036", 7.30, CAL.business_days(date(2026, 11, 13), date(2026, 11, 18)))
    market = rt.MarketData(marks=marks, haircuts=HAIRCUTS, funding=funding())
    res = rt.run_book([trade("T9", "buy", 1e8, price=97.4), trade("T10", "sell", 1e8, price=97.5)],
                      flows=flows(2e8), market=market, settings=settings(costs=NOCOST), end=date(2026, 11, 17))
    assert list(res.fills["trade_id"]) == ["T9", "T10"]  # natural order of ids, buys first: no short sale
    # a sale without a time comes after the day's timed purchases too, a purchase without one before timed sales;
    # timed trades keep their times' order, so a sale timed before the purchase is still a short sale
    for buy, sell in (({"trade_time": "10:00"}, {}), ({}, {"trade_time": "09:00"})):
        res = rt.run_book([trade("B1", "buy", 1e8, price=97.4, **buy), trade("S1", "sell", 1e8, price=97.5, **sell)],
                          flows=flows(2e8), market=market, settings=settings(costs=NOCOST), end=date(2026, 11, 17))
        assert list(res.fills["trade_id"]) == ["B1", "S1"]
    mixed = [trade("S0", "sell", 1e8, price=97.5), trade("B1", "buy", 2e8, price=97.4, trade_time="11:00"),
             trade("S2", "sell", 1e8, price=97.6, trade_time="10:00"), trade("B3", "buy", 1e8, price=97.3)]
    res = rt.run_book(mixed, flows=flows(5e8), market=market, settings=settings(costs=NOCOST), end=date(2026, 11, 17))
    assert list(res.fills["trade_id"]) == ["B3", "S2", "B1", "S0"]  # untimed buys, by time, untimed sales
    with pytest.raises(ValueError, match="trade S1: selling 100,000,000 of GS2036 would leave the book short"):
        rt.run_book([trade("B1", "buy", 1e8, price=97.4, trade_time="15:00"),
                     trade("S1", "sell", 1e8, price=97.5, trade_time="10:00")],
                    flows=flows(2e8), market=market, settings=settings(costs=NOCOST), end=date(2026, 11, 17))
    amounts = [1234567.89, 0.1, 0.2, 7654321.01, 5e6]
    navs = set()
    for order in (amounts, amounts[::-1], amounts[2:] + amounts[:2]):
        fl = pd.DataFrame([{"date": "2026-11-13", "book": "b", "amount": a} for a in order])
        navs.add(repr(float(rt.run_book(None, flows=fl, market=rt.MarketData(marks=marks),
                                        settings=settings(costs=NOCOST), end=date(2026, 11, 13)).nav["nav"].iloc[0])))
    assert len(navs) == 1


def test_reference_rows_given_twice_with_different_values_are_refused():
    marks = marks_at(GS2036, "GS2036", 7.30, CAL.business_days(date(2026, 11, 13), date(2026, 11, 18)))
    bare = [{"trade_id": "A1", "book": "b", "isin": "GS2036", "side": "buy", "face": 1e8,
             "trade_date": "2026-11-13", "price": 97.4}]
    master = pd.DataFrame([TERMS, dict(TERMS, coupon_pct=6.90)])
    with pytest.raises(ValueError, match="lists GS2036 twice with different coupon_pct .lines 2 and 3."):
        rt.run_book(bare, flows=flows(), market=rt.MarketData(marks=marks, master=master),
                    settings=settings(costs=NOCOST), end=date(2026, 11, 18))
    same = rt.run_book(bare, flows=flows(), market=rt.MarketData(marks=marks, master=pd.DataFrame([TERMS, TERMS])),
                       settings=settings(costs=NOCOST), end=date(2026, 11, 18))  # an exact repeat is kept once
    assert_identity(same)
    liq = pd.DataFrame([{"effective_date": "2026-01-01", "isin": "GS2036", "liquidity": "liquid"},
                        {"effective_date": "2026-01-01", "isin": "GS2036", "liquidity": "illiquid"}])
    with pytest.raises(ValueError, match="gives GS2036 two classes from 2026-01-01"):
        rt.run_book([trade(price=97.4)], flows=flows(), market=rt.MarketData(marks=marks, liquidity=liq),
                    settings=settings(costs=NOCOST), end=date(2026, 11, 18))
    auc = pd.DataFrame([{"isin": "GS2036", "auction_date": "2026-11-13", "settlement_date": "2026-11-16",
                         "cutoff_price": p, "wap": 97.46} for p in (97.43, 97.40)])
    with pytest.raises(ValueError, match="auctioned on 2026-11-13 twice with different values"):
        rt.run_book([trade(price=97.4)], flows=flows(), market=rt.MarketData(marks=marks, auctions=auc),
                    settings=settings(costs=NOCOST), end=date(2026, 11, 18))


def test_the_funds_own_fill_beats_an_older_stale_mark_on_the_trade_day():
    old = pd.DataFrame([{"date": date(2026, 10, 1), "isin": "GS2036", "clean_price": 95.0}])
    res = rt.run_book([trade(price=97.4)], flows=flows(), market=rt.MarketData(marks=old),
                      settings=settings(costs=NOCOST), end=date(2026, 11, 16))
    pos = res.positions.set_index("date")
    assert pos.loc[date(2026, 11, 13), "mark_source"] == "fill" and pos.loc[date(2026, 11, 13), "clean_mark"] == 97.4
    assert res.nav.set_index("date").loc[date(2026, 11, 13), "pnl"] == pytest.approx(0.0, abs=1e-4)
    assert pos.loc[date(2026, 11, 16), "mark_source"] == "stale"  # held at the fill afterwards, aged from it
    assert pos.loc[date(2026, 11, 16), "clean_mark"] == 97.4


def test_the_funding_table_carries_a_night_charges_once():
    days = CAL.business_days(date(2026, 11, 13), date(2026, 11, 18))
    st = rt.EngineSettings(access=rt.AccessPolicy(name="listed", repo=True, treps=False, short_sale=False,
                                                  ois="hedge_only"),
                           funding=rt.FundingPolicy(on_shortfall="overdraft", overdraft_rate_pct=9.0))
    res = rt.run_book([trade(price=97.4)], flows=flows(1e7), market=rt.MarketData(
        marks=marks_at(GS2036, "GS2036", 7.30, days), haircuts=HAIRCUTS, funding=funding()), settings=st,
        end=date(2026, 11, 18))
    assert set(res.funding["segment"]) == {"market_repo", "overdraft"}
    table = res.funding.groupby("date")["charges"].sum()
    booked = -lines(res)["repo_charges"]
    assert (table - booked.reindex(table.index)).abs().max() < 1e-9


def test_a_coupon_on_a_business_day_is_counted_once():
    bond = FixedRateBond(6.92, date(2039, 11, 18))  # pays 18 May and 18 Nov: Wed 18 Nov 2026
    days = CAL.business_days(date(2026, 11, 13), date(2026, 11, 20))
    marks = marks_at(bond, "GS2039", 7.10, days)
    res = rt.run_book([{"trade_id": "C1", "book": "b", "isin": "GS2039", "kind": "gsec", "coupon_pct": 6.92,
                        "maturity_date": "2039-11-18", "side": "buy", "face": 1e9, "trade_date": "2026-11-13",
                        "price": float(marks["clean_price"].iloc[0])}], flows=flows(2e9),
                      market=rt.MarketData(marks=marks), settings=settings(costs=NOCOST), end=date(2026, 11, 20))
    by_day = lines(res)
    day = 1e7 * 3.46 / 180  # one day of 30E/360 accrual on Rs 100 crore
    for d in (date(2026, 11, 17), date(2026, 11, 18), date(2026, 11, 19), date(2026, 11, 20)):
        assert by_day.loc[d, "accrual"] == pytest.approx(day, abs=1e-6)
    cash = res.nav.set_index("date")["cash"]
    assert cash.loc[date(2026, 11, 18)] - cash.loc[date(2026, 11, 17)] == pytest.approx(1e7 * 3.46, abs=1e-6)
    assert cash.loc[date(2026, 11, 19)] == pytest.approx(cash.loc[date(2026, 11, 18)], abs=1e-6)
    assert_identity(res)


def test_blank_csv_rows_are_skipped_and_an_unwritable_output_exits_2(tmp_path, capsys):
    from cr_strat.cli import main
    path = tmp_path / "trades.csv"
    pd.DataFrame([trade(price=97.4), {k: "" for k in trade()}]).to_csv(path, index=False)
    assert len(rt.read_trades(path)) == 1 and rt.read_trades(path).attrs["lines"] == [2]
    marks = tmp_path / "marks.csv"
    marks_at(GS2036, "GS2036", 7.3, [date(2026, 11, 13), date(2026, 11, 16)]).to_csv(marks, index=False)
    flowfile = tmp_path / "flows.csv"
    flows().to_csv(flowfile, index=False)
    blocker = tmp_path / "a_file"
    blocker.write_text("x")
    code = main(["returns", "--trades", str(path), "--flows", str(flowfile), "--marks", str(marks),
                 "--access", "nbfc_non_pd", "--end", "2026-11-16", "--out-dir", str(blocker / "out")])
    assert code == 2 and "error:" in capsys.readouterr().err


def test_every_input_table_drops_an_all_blank_row(tmp_path):
    """A spreadsheet's trailing ,,, row in any input of cr-strat returns (the liquidity classes and the haircuts as
    well as the trades) changes nothing; the library's own tables (stock, auctions, residual history) skip it too."""
    from cr_strat.cli import main
    days = CAL.business_days(date(2026, 11, 13), date(2026, 11, 18))
    curves, marks = _curve_market(days, {"GS2036": GS2036})
    tables = {
        "trades": pd.DataFrame([trade(price=marks["clean_price"].iloc[0], liquidity="")]),
        "flows": flows(2e9), "marks": marks, "haircuts": HAIRCUTS,
        "liquidity": pd.DataFrame([{"effective_date": "2026-11-01", "isin": "GS2036", "liquidity": "liquid"}]),
        "master": pd.DataFrame([{**dict.fromkeys(sv.MASTER_COLUMNS, ""), **TERMS, "name": "6.94% GS 2036",
                                 "issue_date": "2026-05-11", "matured": "False"}]),
        "nss": pd.DataFrame([rt.nss_params_row(c, d) for d, c in curves.items()]),
        "history": pd.DataFrame([{"date": "2026-11-16", "isin": "GS2036", "resid_yield_bp": 1.0}]),
        "fixings": pd.DataFrame([{"date": "2026-11-13", "rate_pct": 5.60}]),
        "ois": pd.DataFrame([{"date": "2026-11-13", "tenor": t, "rate_pct": r, "overnight_pct": 5.60}
                             for t, r in B3.items()]),
        "costs": pd.DataFrame([{"rule_id": "U1", "component": "custody", "effective_from": "2026-01-01", "value": 0,
                                "unit": "rs_per_crore_face", "source": "test"}])}
    flags = {"nss": "nss-params", "history": "residual-history", "ois": "ois-quotes"}
    outputs = {}
    for label in ("clean", "blank"):
        args = ["returns", "--access", "nbfc_non_pd", "--end", "2026-11-18", "--project-calendar",  # the OIS curve
                "--out-dir", str(tmp_path / label)]
        for name, frame in tables.items():
            path = tmp_path / f"{label}_{name}.csv"
            frame.to_csv(path, index=False)
            if label == "blank":
                with open(path, "a") as f:
                    f.write("," * (len(frame.columns) - 1) + "\n")
            args += [f"--{flags.get(name, name)}", str(path)]
        assert main(args) == 0, label
        outputs[label] = {f.name: f.read_bytes() for f in sorted((tmp_path / label).iterdir())}
    assert outputs["clean"] == outputs["blank"] and len(outputs["clean"]) == 9
    blank = {"isin": None, "date": None, "auction_date": None, "settlement_date": None, "cutoff_price": None,
             "outstanding_cr": None, "resid_yield_bp": None}
    seen = {}

    class Look:
        def decide(self, snap):
            seen["history"] = snap.residual_history()
            return []

    rt.run_backtest(Look(), rt.MarketData(
        marks=marks, stock=pd.DataFrame([{"isin": "GS2036", "date": "2026-11-13", "outstanding_cr": 272000}, blank]),
        auctions=pd.DataFrame([{"isin": "GS2036", "auction_date": "2026-11-13", "settlement_date": "2026-11-16",
                                "cutoff_price": 97.43}, blank]),
        residual_history=pd.DataFrame([{"date": "2026-11-16", "isin": "GS2036", "resid_yield_bp": 1.0}, blank])),
        settings(costs=NOCOST), start=days[0], end=days[-1], capital=1e8)
    assert list(seen["history"]["date"]) == ["2026-11-16"]


def test_a_when_issued_short_is_covered_by_the_auction_day_unless_it_may_be_carried():
    y = GS2036.yield_from_price(97.43, date(2026, 11, 17))
    days = CAL.business_days(date(2026, 11, 16), date(2026, 11, 25))
    market = rt.MarketData(marks=marks_at(GS2036, "GS2036", y, days), funding=funding(), haircuts=HAIRCUTS)
    sale = trade("WI1", "sell", 5e9, "2026-11-16", price=97.40, venue="when_issued", settlement_date="2026-11-23",
                 notified_cr=34000, liquidity="liquid")
    cover = trade("AU1", "buy", 5e9, "2026-11-20", price=97.35, venue="auction", settlement_date="2026-11-23",
                  liquidity="liquid")
    for preset in ("nbfc_non_pd", "bank_or_pd"):
        res = rt.run_book([sale, cover], flows=flows(1e8, "2026-11-16"), market=market,
                          settings=settings(preset, costs=NOCOST), end=date(2026, 11, 25))
        assert_identity(res)
        assert res.nav["pnl"].sum() == pytest.approx(5e7 * (97.40 - 97.35), abs=1e-4)  # sold at 97.40, bought 97.35
    with pytest.raises(ValueError, match="when-issued short of Rs 500.00 crore in GS2036 is still open at the "
                                         "close of its auction day 2026-11-20"):
        rt.run_book([sale], flows=flows(1e8, "2026-11-16"), market=market, settings=settings(costs=NOCOST),
                    end=date(2026, 11, 25))
    with pytest.raises(ValueError, match="exceeds 3,400.00 crore"):  # 10% of the notified amount
        rt.run_book([dict(sale, face=3.5e10)], flows=flows(1e8, "2026-11-16"), market=market,
                    settings=settings(costs=NOCOST), end=date(2026, 11, 25))


def test_a_when_issued_short_carried_past_the_auction_meets_the_short_sale_rules():
    """bank_or_pd may carry a when-issued short past the auction day; uncovered, it settles on the issue date (Mon 23
    Nov) as a short sale, so at the auction day's close (Fri 20 Nov) it must meet the Short Sale Directions as an
    ordinary short: Rs 500 crore at most in a liquid security without a stock, and a special repo rate."""
    y = GS2036.yield_from_price(97.43, date(2026, 11, 17))
    days = CAL.business_days(date(2026, 11, 16), date(2026, 12, 4))
    market = rt.MarketData(marks=marks_at(GS2036, "GS2036", y, days), funding=funding(), haircuts=HAIRCUTS)

    def run(face, preset="bank_or_pd", **kw):
        sale = trade("WI1", "sell", face, "2026-11-16", price=97.40, venue="when_issued", settlement_date="2026-11-23",
                     notified_cr=34000, liquidity="liquid", **kw)
        access = preset if isinstance(preset, rt.AccessPolicy) else rt.ACCESS_PRESETS[preset]
        return rt.run_book([sale], flows=flows(2e9, "2026-11-16"), market=market,
                           settings=rt.EngineSettings(access=access, costs=NOCOST), end=date(2026, 12, 4))

    with pytest.raises(ValueError, match="carried past the close of its auction day 2026-11-20 is a short sale from "
                                         "the issue date 2026-11-23: a short of Rs 5,000.00 crore in GS2036 exceeds "
                                         "the limit of Rs 500.00 crore"):
        run(5e10, special_repo_pct=5.0)  # within the 25% when-issued limit (Rs 8,500 crore), not the short limit
    with pytest.raises(ValueError, match="auction day 2026-11-20 .* needs special_repo_pct"):
        run(1e9)  # refused at the close, not left to fail on the issue date
    carrier = replace(rt.ACCESS_PRESETS["bank_or_pd"], name="carrier_without_shorts", short_sale=False)
    with pytest.raises(ValueError, match="'carrier_without_shorts' allows no short sales"):
        run(1e9, carrier, special_repo_pct=5.0)
    res = run(4e9, special_repo_pct=5.0)  # Rs 400 crore: within the limit, carried and settled as a short
    held = res.positions[res.positions["date"] == date(2026, 12, 4)]
    assert list(held["face"]) == [-4e9] and list(held["state"]) == ["settled"]
    assert res.funding.set_index("date").loc[date(2026, 11, 23), "lent_short"] > 0  # borrowed in a reverse repo
    assert_identity(res)


def test_backtest_auction_orders_respect_the_announcement_and_the_auction_size():
    days = CAL.business_days(date(2026, 11, 9), date(2026, 11, 20))
    marks = marks_at(GS2036, "GS2036", GS2036.yield_from_price(97.43, date(2026, 11, 16)), days)
    auctions = pd.DataFrame([{"isin": "GS2036", "auction_date": "2026-11-13", "settlement_date": "2026-11-16",
                              "cutoff_price": 97.43, "wap": 97.46, "notified_cr": 34000, "accepted_cr": 34000,
                              "announcement_public_from": "2026-11-09T17:45:00"}])

    class Bid:
        def __init__(self, face, venue, day):
            self.face, self.venue, self.day = face, venue, day

        def decide(self, snap):
            if snap.day != self.day:
                return []
            if self.day == date(2026, 11, 12) and self.face == 2e7:  # announced, the result not yet out
                assert len(snap.auctions()) == 1 and pd.isna(snap.auctions()["cutoff_price"].iloc[0])
            return [rt.Order(book="main", isin="GS2036", side="buy", face=self.face, venue=self.venue,
                             auction_date=date(2026, 11, 13))]

    def run(face, venue, day, table=auctions):  # a reissue: the bond was issued on 11 May 2026, so its ISIN is public
        return rt.run_backtest(Bid(face, venue, day), rt.MarketData(
            marks=marks, master=pd.DataFrame([dict(TERMS, issue_date="2026-05-11")]), funding=funding(),
            haircuts=HAIRCUTS, auctions=table),
            settings(costs=NOCOST, funding=rt.FundingPolicy(on_shortfall="overdraft", overdraft_rate_pct=9.0)),
            start=min(day, days[0]), end=days[-1], capital=2e11).fills.iloc[0]

    big = run(5e11, "auction", date(2026, 11, 12))
    assert big["status"] == "filled" and big["face"] == 3.4e10 and big["fill"] == 97.43  # 10% of Rs 34,000 crore
    assert "at most 10% of the amount accepted" in big["notes"]
    small_nc = run(2e7, "auction_nc", date(2026, 11, 12))
    assert small_nc["status"] == "filled" and small_nc["fill"] == 97.46
    for face, venue, day, table, why in (
            (1e10, "auction_nc", date(2026, 11, 12), auctions, "exceeds Rs 2 crore a bid"),
            (1e9, "auction", date(2026, 11, 6), auctions, "before the auction was announced"),
            (1e9, "auction", date(2026, 11, 12), auctions.drop(columns="announcement_public_from"),
             "no announcement_public_from")):
        row = run(face, venue, day, table)
        assert row["status"] == "refused" and why in row["notes"]


def test_a_hedge_left_without_its_bonds_is_a_breach_every_day(curve19):
    days = CAL.business_days(date(2026, 11, 19), date(2026, 11, 27))
    ois_curves = {d: rt.rolled(curve19, d) for d in days}
    gcurves, marks = _curve_market(days, {"GS2036": GS2036})
    clean = marks.set_index("date").loc[days[0], "clean_price"]
    notional = hedge_notional(GS2036, date(2026, 11, 20), clean, ois_curves[days[0]], 48, face=1e9, calendar=LEN)
    swap = {"trade_id": "H1", "book": "b", "trade_date": "2026-11-19", "months": 48, "notional": notional,
            "pay_fixed": True, "purpose": "hedge"}
    market = rt.MarketData(marks=marks, curves={"gsec": gcurves}, ois_curves=ois_curves,
                           fixings={d: 5.60 for d in days}, funding=funding(), haircuts=HAIRCUTS)
    listed = replace(rt.ACCESS_PRESETS["unregulated_company"], name="listed_company", repo=True, treps=True)
    sell = marks.set_index("date").loc[date(2026, 11, 20), "clean_price"]
    res = rt.run_book([trade(price=clean, day="2026-11-19", liquidity="liquid"),
                       trade("T2", side="sell", price=sell, day="2026-11-20", liquidity="liquid")],
                      [swap], flows(2e8, "2026-11-19"), market=market,
                      settings=rt.EngineSettings(access=listed, project_calendar=True, costs=NOCOST), end=days[-1])
    breach = res.checks[res.checks["check"] == "hedge_only"]
    assert list(breach["date"]) == days[1:] and (breach["status"] == "breach").all()
    assert any("did not hedge its bonds on 5 business day(s)" in n for n in res.notes)


def test_the_snapshot_cuts_every_table_at_its_time(tmp_path):
    from cr_strat import event_ledger as el
    from cr_strat import rbi_store
    days = CAL.business_days(date(2026, 11, 16), date(2026, 11, 18))
    rows = [dict.fromkeys(el.EVENT_COLUMNS), dict.fromkeys(el.EVENT_COLUMNS)]
    rows[0].update(event_id="gsec_auction:2026-11-20:new GS 2066", kind="gsec_auction", subkind="new",
                   event_date="2026-11-20", key="7.71% GS 2066", security="7.71% GS 2066", coupon_pct=7.71,
                   notified_cr=10000, cutoff_yield=7.71, announcement_public_from="2026-11-16T17:45:00",
                   result_public_from="2026-11-20T23:59:59", isin="IN0020260099", result_flags="shortfall")
    rows[1].update(event_id="gsec_auction:2026-11-27:IN0020260033", kind="gsec_auction", subkind="reissue",
                   event_date="2026-11-27", key="IN0020260033", announcement_public_from="2026-11-23T17:45:00")
    events = el._events_frame(rows)
    history = pd.DataFrame([{"date": "2026-11-17", "isin": "GS2036", "resid_yield_bp": 1.0},
                            {"date": "2026-11-18", "isin": "GS2036", "resid_yield_bp": 2.0}])
    store = tmp_path / "store"
    (store / "derived").mkdir(parents=True)
    cols = rbi_store.DERIVED_COLUMNS["money_market_daily"]
    mm = [{"as_on_date": "2026-11-13", "triparty_rate_pct": 5.65, "public_from": "2026-11-16T09:00:00"},
          {"as_on_date": "2026-11-14", "triparty_rate_pct": 5.60, "public_from": "2026-11-16T09:00:00",
           "revised": "2026-11-17T09:00:00"},
          {"as_on_date": "2026-11-16", "triparty_rate_pct": 5.57, "public_from": "2026-11-17T09:00:00"}]
    pd.DataFrame([{c: r.get(c, "") for c in cols} for r in mm], columns=list(cols)).to_csv(
        store / "derived" / "money_market_daily.csv", index=False)
    seen = {}

    class Look:
        def decide(self, snap):
            if snap.day == days[0]:
                seen.update(events=snap.events(), history=snap.residual_history(), master=snap.master(),
                            mpc=snap.mpc_calendar(), mm=snap.store_table("money_market_daily"))
                with pytest.raises(ValueError, match="no macro store in the market data"):
                    snap.macro("cpi")
            return []

    master = pd.DataFrame([dict(TERMS, issue_date="2026-05-11")])  # issued before the run: in the snapshot's master
    rt.run_backtest(Look(), rt.MarketData(marks=marks_at(GS2036, "GS2036", 7.3, days), events=events,
                                          residual_history=history, master=master, store=store),
                    settings(costs=NOCOST), start=days[0], end=days[-1], capital=1e8)
    assert list(seen["events"]["event_id"]) == ["gsec_auction:2026-11-20:new GS 2066"]  # the other not announced
    row = seen["events"].iloc[0]
    assert pd.isna(row["coupon_pct"]) and pd.isna(row["cutoff_yield"]) and row["result_flags"] == ""
    assert row["key"] == row["security"] == "new GS 2066" and row["isin"] == ""
    assert list(seen["history"]["date"]) == ["2026-11-17"]  # the fit for 17 Nov settlement, public 16 Nov 18:00
    assert list(seen["master"]["isin"]) == ["GS2036"] and len(seen["mpc"]) > 0
    assert [str(d)[:10] for d in seen["mm"]["as_on_date"]] == ["2026-11-13"]  # 14 Nov revised later, 16 Nov later


def test_the_snapshot_master_and_auctions_hide_a_new_issue_until_its_result():
    """A new 7.71% GS 2066 auctioned on Fri 20 Nov 2026 (result public at the end of that day), issued Mon 23 Nov: a
    strategy deciding before the result sees no row of it in the master and no ISIN on its auction line, only the
    line's name; the master's state columns (today's stock, lists, FAR, class) are blank, matured is as of the day."""
    days = CAL.business_days(date(2026, 11, 16), date(2026, 11, 24))
    built = {"outstanding_as_of": "2026-11-30", "in_latest_list": True, "far": True, "far_name": "x",
             "last_listed": "2026-11-30", "sources": "rbi_far;rbi_goi_outstanding", "liquidity": "liquid"}
    master = pd.DataFrame([
        dict(TERMS, name="6.94% GS 2036", issue_date="2026-05-11", outstanding_cr=272000, matured=False, **built),
        {"isin": "IN0020260099", "name": "7.71% GS 2066", "kind": "gsec", "coupon_pct": 7.71,
         "maturity_date": "2066-11-23", "issue_date": "2026-11-23", "outstanding_cr": 10000, "matured": False,
         **built},
        {"isin": "GS2026", "name": "5.63% GS 2026", "kind": "gsec", "coupon_pct": 5.63, "maturity_date": "2026-11-17",
         "issue_date": "2021-11-17", "outstanding_cr": None, "matured": True, **built},
        # no issue date: a bill priced on 16 Nov existed then; a bond with no mark, auction or date stays out
        {"isin": "TB2027", "kind": "tbill", "maturity_date": "2027-02-18", "matured": False, **built},
        {"isin": "GS2030Z", "kind": "gsec", "coupon_pct": 7.0, "maturity_date": "2030-01-01", "matured": False,
         **built}])
    auctions = pd.DataFrame([
        {"isin": "IN0020260099", "auction_date": "2026-11-20", "settlement_date": "2026-11-23", "cutoff_price": 100.0,
         "notified_cr": 10000, "accepted_cr": 10000, "announcement_public_from": "2026-11-16T17:45:00",
         "result_public_from": "2026-11-20T23:59:59", "line": "new GS 2066"},
        {"isin": "GS2036", "auction_date": "2026-11-20", "settlement_date": "2026-11-23", "cutoff_price": 97.43,
         "notified_cr": 20000, "accepted_cr": 20000, "announcement_public_from": "2026-11-16T17:45:00",
         "result_public_from": "2026-11-20T23:59:59", "line": "GS2036"}])
    seen = {}

    class Look:
        def __init__(self, name=None):
            self.name = name

        def decide(self, snap):
            seen[snap.day] = (snap.master(), snap.auctions())
            if snap.day == days[0] and self.name:
                return [rt.Order(book="main", isin=self.name, side="buy", face=1e9, venue="auction",
                                 auction_date=date(2026, 11, 20))]
            return []

    bill = pd.DataFrame([{"date": date(2026, 11, 16), "isin": "TB2027", "clean_price": 98.5},  # and a when-issued
                         {"date": date(2026, 11, 16), "isin": "IN0020260099", "yield_pct": 7.70}])  # price: no terms
    market = rt.MarketData(marks=pd.concat([marks_at(GS2036, "GS2036", 7.3, days), bill], ignore_index=True),
                           master=master, auctions=auctions, funding=funding(), haircuts=HAIRCUTS)
    rt.run_backtest(Look(), market, settings(costs=NOCOST), start=days[0], end=days[-1], capital=2e10)
    before, lines_before = seen[date(2026, 11, 16)]
    assert list(before["isin"]) == ["GS2036", "GS2026", "TB2027"]  # the new issue has no row before its result
    for column in ("outstanding_cr", "outstanding_as_of", "in_latest_list", "far", "far_name", "last_listed",
                   "sources", "liquidity"):
        assert before[column].isna().all(), column  # the master's state when it was built, not point in time
    assert list(before["matured"]) == [False, False, False] and before["coupon_pct"].iloc[0] == 6.94
    assert list(seen[date(2026, 11, 17)][0]["matured"]) == [False, True, False]  # GS2026 redeemed on 17 Nov
    assert list(lines_before["line"]) == ["GS2036", "new GS 2066"]
    assert list(lines_before["isin"]) == ["GS2036", ""] and lines_before["cutoff_price"].isna().all()
    on_the_day = seen[date(2026, 11, 20)]  # 20:00 on the auction day: the result is public only at 23:59:59
    assert "IN0020260099" not in set(on_the_day[0]["isin"]) and "IN0020260099" not in set(on_the_day[1]["isin"])
    issued = seen[date(2026, 11, 23)]
    row = issued[0].set_index("isin").loc["IN0020260099"]
    assert row["coupon_pct"] == 7.71 and pd.isna(row["outstanding_cr"])
    assert "IN0020260099" in set(issued[1]["isin"]) and issued[1]["cutoff_price"].notna().all()
    # a bid naming the new issue's ISIN before its result is refused; naming its line it fills at the cut-off
    refused = rt.run_backtest(Look("IN0020260099"), market, settings(costs=NOCOST), start=days[0], end=days[-1],
                              capital=2e10).fills
    assert list(refused["status"]) == ["refused"] and "not known before its result" in refused["notes"].iloc[0]
    filled = rt.run_backtest(Look("new GS 2066"), market, settings(costs=NOCOST), start=days[0], end=days[-1],
                             capital=2e10).fills
    assert list(filled["status"]) == ["filled"] and filled["instrument"].iloc[0] == "IN0020260099"
    assert filled["fill"].iloc[0] == 100.0 and filled["date"].iloc[0] == date(2026, 11, 20)


def test_curve_fit_writes_the_nss_parameters_the_engine_reads(tmp_path):
    curve = cf.NssCurve(valuation_date=date(2026, 11, 17), t_min=0.3, t_max=39.5, method="nss", params=NSS,
                        curve_id="x")
    row = rt.nss_params_row(curve, date(2026, 11, 16), public_from="2026-11-16T18:30:00")
    table = rt.append_nss_params(None, row)
    table = rt.append_nss_params(table, dict(row, beta0=repr(7.7)))  # a refit of the day replaces the row
    assert len(table) == 1 and table["beta0"].iloc[0] == "7.7"
    path = tmp_path / "nss.csv"
    table.to_csv(path, index=False)
    entry = rt.read_nss_params(path)["gsec"][date(2026, 11, 16)]
    assert entry.curve.valuation_date == date(2026, 11, 17) and entry.curve.params[0] == 7.7
    assert entry.public_from == datetime(2026, 11, 16, 18, 30)
    with pytest.raises(TypeError, match="needs an NssCurve"):
        rt.nss_params_row(object(), date(2026, 11, 16))


def test_a_cp_against_a_constant_ois_curve_books_no_curve_lines_over_a_weekend(curve19):
    curve = ois.bootstrap_ois(B3, date(2026, 11, 13), 5.60, calendar=LEN, source="synthetic B3")
    days = CAL.business_days(date(2026, 11, 13), date(2026, 11, 20))
    cp = DiscountInstrument(date(2027, 2, 15), CP)
    marks = pd.DataFrame([{"date": d, "isin": "CP2027", "clean_price": cp.price(
        7.20 + 0.02 * k, CAL.settlement_date(d, 1), rounded=False)} for k, d in enumerate(days)])
    res = rt.run_book([{"trade_id": "C1", "book": "b", "isin": "CP2027", "kind": "cp", "maturity_date": "2027-02-15",
                        "side": "buy", "face": 5e8, "trade_date": "2026-11-13", "yield_pct": 7.20}], flows=flows(1e9),
                      market=rt.MarketData(marks=marks, ois_curves={d: rt.rolled(curve, d) for d in days}),
                      settings=settings(costs=NOCOST, project_calendar=True), end=days[-1])
    by_day = lines(res).reindex(columns=[*rt.AttributionSettings().curve_lines, "rich_cheap"], fill_value=0.0)
    assert by_day[list(rt.AttributionSettings().curve_lines)].abs().max().max() < 1e-4  # the Monday included
    assert by_day["rich_cheap"].abs().max() > 1000  # the CP's 2 bp a day over OIS is its residual
    assert_identity(res)


def test_cli_returns_attributes_sdls_against_an_sdl_curve(tmp_path, capsys):
    from cr_strat.cli import main
    from cr_strat.conventions import SDL
    sdl = FixedRateBond(7.61, date(2030, 11, 18), SDL)
    days = CAL.business_days(date(2026, 11, 13), date(2026, 11, 19))
    curves, marks = _curve_market(days, {"SDL2030": sdl}, shift=lambda k: (NSS[0] + 0.05 * (k >= 2), *NSS[1:]))
    nss = pd.DataFrame([dict(rt.nss_params_row(c, d), curve="sdl") for d, c in curves.items()])
    for name, frame in (("nss.csv", nss), ("marks.csv", marks), ("flows.csv", flows(2e9))):
        frame.to_csv(tmp_path / name, index=False)
    pd.DataFrame([{"trade_id": "S1", "book": "b", "isin": "SDL2030", "kind": "sdl", "coupon_pct": 7.61,
                   "maturity_date": "2030-11-18", "side": "buy", "face": 1e9, "trade_date": "2026-11-13",
                   "price": marks["clean_price"].iloc[0]}]).to_csv(tmp_path / "trades.csv", index=False)
    args = ["returns", "--trades", str(tmp_path / "trades.csv"), "--flows", str(tmp_path / "flows.csv"), "--marks",
            str(tmp_path / "marks.csv"), "--nss-params", str(tmp_path / "nss.csv"), "--access", "nbfc_non_pd",
            "--end", "2026-11-19"]
    assert main([*args, "--out-dir", str(tmp_path / "plain")]) == 0
    assert "the curve 'sdl' in --nss-params is the reference of no kind" in capsys.readouterr().out
    plain = pd.read_csv(tmp_path / "plain" / "attribution.csv")
    assert "curve_parallel" not in set(plain["line"])  # SDLs against gsec, which the file lacks
    assert main([*args, "--reference", "sdl=sdl", "--out-dir", str(tmp_path / "sdl")]) == 0
    att = pd.read_csv(tmp_path / "sdl" / "attribution.csv")
    assert att[att["line"] == "curve_parallel"]["amount"].abs().sum() > 1000  # the 5 bp shift on 17 Nov
