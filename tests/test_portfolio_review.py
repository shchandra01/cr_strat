"""Tests of the portfolio fixes of the risk layer's review: an OIS hedge sized on the engine's DV01, strategy returns
without a -100% day where a book's row is missing, and the drawdown scale's recovery level pinned. Synthetic data."""

from __future__ import annotations

import math
from datetime import date

import pandas as pd
import pytest
import limits_fixtures as lf
import risk_fixtures as fx

from cr_strat import limits as lm
from cr_strat import portfolio as pf
from cr_strat import returns as rt
from cr_strat.ois import MIBOR_OIS, OisSwap, effective_date, ois_npv, ois_par_rate


def _engine_dv01(curve, months, start, notional, cal) -> float:
    rate = ois_par_rate(curve, start, months, calendar=cal)
    return ois_npv(curve, OisSwap(effective=start, months=months, fixed_rate_pct=rate, notional=notional),
                   calendar=cal).dv01


@pytest.mark.parametrize("months", [3, 6, 12, 24, 36, 48])
def test_an_ois_hedge_is_dv01_neutral_on_the_engines_measure(months):
    cal = fx.CAL.lenient()
    curve = fx.b3_curve()
    start = effective_date(cal.add_business_days(date(2026, 11, 19), 1), MIBOR_OIS, cal)
    s = fx.CAL.settlement_date(date(2026, 11, 19), 1)
    dv01 = 1e9 / 100 * fx.GS2036.dv01(7.0, s)  # Rs 100 crore of GS2036's yield DV01, rupees per bp
    n = pf.hedge_ois_notional(dv01, curve, months, start, calendar=cal)
    assert abs(_engine_dv01(curve, months, start, n, cal)) / dv01 == pytest.approx(1.0, abs=1e-9)  # was 1.014 to 1.058
    annuity = pf.hedge_ois_notional(dv01, curve, months, start, calendar=cal, method="annuity")
    assert annuity > n  # the par-rate (annuity) rule is the larger notional on the engine's measure
    with pytest.raises(ValueError, match="hedge sizing method"):
        pf.hedge_ois_notional(dv01, curve, months, start, calendar=cal, method="par")


def test_a_12_month_hedge_sized_by_default_passes_the_engines_105_percent_test():
    market, trades = lf.ois_market()
    ctx = lf.context(market, "unregulated_company", trades=trades, project_calendar=True)
    state = lf.state([lf.gsec_row("GS2031", 1e9)], {"b": (1.5e9, 5e8)}, ctx, scenarios=lf.scenario_rows())
    dv01 = state.book.positions[0].dv01_yield
    curve = market.ois_curves[lf.END].curve
    cal = fx.CAL.lenient()
    start = effective_date(cal.add_business_days(lf.END, 1), MIBOR_OIS, cal)

    def refused_by(method):
        face = round(pf.hedge_ois_notional(dv01, curve, 12, start, calendar=cal, method=method), 0)
        _, rows = lm.PreTradeGate(None, ctx).check(state, [rt.Order(book="b", side="pay", face=face, months=12)])
        frame = lm.gate_frame(rows)
        return set(frame.loc[frame["decision"] == "refused", "limit_id"])

    assert "L05" in refused_by("annuity")  # 105.9% of the bond's DV01 on the engine's measure
    assert "L05" not in refused_by("engine")  # 100% (L06, the hedge-effect test at another tenor, may still refuse)


def _nav(rows):
    return pd.DataFrame([{"date": d, "book": b, "nav": n, "flow": f} for d, b, n, f in rows])


def test_a_missing_nav_row_gives_no_return_rather_than_minus_100_percent():
    days = [date(2026, 11, d) for d in (16, 17, 18, 19, 20)]
    rows = [(days[0], "b1", 101.0, 100.0), (days[1], "b1", 102.0, 0.0), (days[2], "b1", 103.0, 0.0),
            (days[3], "b1", 104.0, 0.0), (days[4], "b1", 105.0, 0.0),
            (days[0], "b2", 50.5, 50.0), (days[1], "b2", 51.0, 0.0),  # no row on the 18th
            (days[3], "b2", 52.0, 0.0), (days[4], "b2", 52.5, 0.0),
            (days[0], "b3", 20.2, 20.0), (days[1], "b3", 20.4, 0.0)]  # b3's history ends on the 17th
    ret = pf.strategy_returns(_nav(rows))
    assert ret["b1"].tolist() == pytest.approx([0.01, 1 / 101, 1 / 102, 1 / 103, 1 / 104])
    b2 = ret["b2"].tolist()
    assert b2[:2] == pytest.approx([0.01, 0.5 / 50.5])
    assert math.isnan(b2[2]) and math.isnan(b2[3])  # the gap and the day after it (no previous NAV): no return
    assert b2[4] == pytest.approx(0.5 / 52.0)
    b3 = ret["b3"].tolist()
    assert b3[:2] == pytest.approx([0.01, 0.2 / 20.2]) and math.isnan(b3[2])  # its end, not -100%
    assert not (ret < -0.5).any().any()


def test_a_strategy_with_a_book_missing_a_row_gives_no_return_that_day():
    days = [date(2026, 11, d) for d in (16, 17, 18)]
    rows = [(days[0], "b1", 101.0, 100.0), (days[1], "b1", 102.0, 0.0), (days[2], "b1", 103.0, 0.0),
            (days[0], "b2", 50.5, 50.0), (days[2], "b2", 51.5, 0.0)]
    books = pd.DataFrame([{"book": "b1", "strategy": "S1", "effective_from": "2026-11-01"},
                          {"book": "b2", "strategy": "S1", "effective_from": "2026-11-01"}])
    s1 = pf.strategy_returns(_nav(rows), books)["S1"].tolist()
    assert s1[0] == pytest.approx(1.5 / 150.0)
    assert math.isnan(s1[1]) and math.isnan(s1[2])  # was -33%: b2's missing NAV read as zero


def test_the_drawdown_scale_stays_down_until_the_drawdown_is_above_recover():
    # -3% (half), then -2% and -1.5% (still at or below recover: half), then -1.4% (above: back to 1)
    index = [1.00, 0.97, 0.98, 0.985, 0.986]
    assert pf.drawdowns(index).round(4).tolist() == [0.0, -0.03, -0.02, -0.015, -0.014]
    assert pf.drawdown_scale(index).tolist() == [1.0, 0.5, 0.5, 0.5, 1.0]
    assert pf.drawdown_scale(index, recover=-0.025).tolist() == [1.0, 0.5, 1.0, 1.0, 1.0]
