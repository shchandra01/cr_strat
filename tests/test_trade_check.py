"""Tests of the trade-yield check (trades.check_trade_yields) and the price panel's recomputed yields.

Synthetic data only. T19 is the corporates spec's vector (19 Nov 2026): T17's 8.95% annual NCD (allotted 14 Dec 2023,
maturing 14 Dec 2030, a Saturday, so redeemed on Friday 13 Dec) at a clean price of 102.2591928939 for settlement on
16 Nov 2026 yields 8.2612262724% on its due dates, 8.2662660611% on its payment dates, 10.8309501599% with the price
read as dirty and 8.0973101915% compounded semi-annually.
"""

import math

import pandas as pd
import pytest

from cr_strat import trades as tr
from cr_strat.curves import BenchmarkCurve
from cr_strat.ownership import load_securities
from cr_strat.price_panel import PANEL_COLUMNS, RECOMPUTED_COLUMNS, build_price_panel
from cr_strat.rating_actions import load_rating_actions
from cr_strat.rating_events import rating_timeline

CLEAN = 102.2591928939


def terms(t17: str, call: str = "INE000CALL01") -> pd.DataFrame:
    return pd.DataFrame([
        {"isin": t17, "kind": "corporate", "issuer_id": "A", "coupon_pct": "8.95", "frequency": "1",
         "issue_date": "2023-12-14", "maturity_date": "2030-12-14"},
        {"isin": call, "kind": "corporate", "issuer_id": "B", "coupon_pct": "8.0", "frequency": "1",
         "issue_date": "2024-01-15", "maturity_date": "2034-01-15"},
    ])


def trade(isin, price, yld, settle="2026-11-16", day="2026-11-13", amount=5e7):
    return {"trade_date": pd.Timestamp(day), "isin": isin, "price": price, "yield_pct": yld, "amount_inr": amount,
            "n_trades": 1, "venue": "NSE", "settlement_date": pd.Timestamp(settle) if settle else pd.NaT,
            "source_file": "synthetic.csv"}


def test_spec_vector_t19_readings(isin):
    t17 = isin("INE000T1701")
    frame = pd.DataFrame([trade(t17, CLEAN, 8.2612262724), trade(t17, CLEAN, 8.2662660611),
                          trade(t17, CLEAN, 10.8309501599), trade(t17, CLEAN, 8.0973101915)])
    out = tr.check_trade_yields(frame, terms(t17))
    assert list(out.columns) == [*frame.columns, *tr.CHECK_COLUMNS]
    pd.testing.assert_frame_equal(out[list(frame.columns)], frame)  # the trades' own columns are untouched
    # the third print's yield matches only with its price read as dirty: its recomputed yield reads it so (the build
    # kept the clean reading, 8.2612262724, and called the print ok)
    assert out["recomputed_yield_pct"].tolist() == pytest.approx([8.2612262724] * 2 + [10.8309501599]
                                                                 + [8.2612262724], abs=1e-9)
    assert out["standard_diff_bp"].tolist() == pytest.approx([0.0, 0.503979, 0.0, -16.3916], abs=1e-4)
    assert out["nearest_reading"].tolist()[:3] == ["clean/due dates/maturity", "clean/payment dates/maturity",
                                                   "dirty/due dates/maturity"]
    assert out["yield_check"].tolist() == ["ok", "ok", "dirty_price", "mismatch"]  # a semi-annual yield is no reading
    assert out["yield_diff_bp"].iloc[3] == pytest.approx(-16.3916, abs=1e-4)
    loose = tr.check_trade_yields(frame, terms(t17), tolerance_bp=20.0)
    assert loose["yield_check"].iloc[3] == "ok"


def test_statuses_and_settlement(isin):
    t17 = isin("INE000T1701")
    frame = pd.DataFrame([
        trade(t17, CLEAN, 8.2612262724, settle=None),  # T+1 from Friday 13 Nov: Monday 16 Nov
        trade(isin("INE000NO001"), 100.0, 8.0),
        trade(t17, math.nan, 8.0),
        trade(t17, CLEAN, math.nan),
        trade(t17, 100.0, 9.0, settle="2030-12-02", day="2030-12-01"),  # after the record date of 29 Nov 2030
    ])
    out = tr.check_trade_yields(frame, terms(t17))
    assert out["yield_check"].tolist() == ["ok", "no_terms", "no_price", "no_yield", "error"]
    assert out["settlement_used"].iloc[0] == pd.Timestamp("2026-11-16").date()
    assert out["check_notes"].iloc[0] == "settlement taken as T+1"
    assert out["recomputed_yield_pct"].iloc[3] == pytest.approx(8.2612262724, abs=1e-9)  # no yield, still computed
    assert "redemption record date" in out["check_notes"].iloc[4]
    with pytest.raises(ValueError, match="workout must be one of"):
        tr.check_trade_yields(frame, terms(t17), workout="sebi")


def test_a_yield_to_call_is_recognised_and_worst_is_recomputed(isin):
    """T11's bond: calls at par on 15 Jan 2029 and 2030; dirty 103 on a coupon date gives YTC 6.34690720%."""
    t17, call = isin("INE000T1701"), isin("INE000CAL01")
    options = pd.DataFrame([{"isin": call, "kind": "call", "date": "2029-01-15"},
                            {"isin": call, "kind": "call", "date": "2030-01-15"}])
    frame = pd.DataFrame([trade(call, 103.0, 6.34690720, settle="2027-01-15", day="2027-01-14"),
                          trade(call, 103.0, 7.42884612, settle="2027-01-15", day="2027-01-14")])
    out = tr.check_trade_yields(frame, terms(t17, call), options=options)
    assert out["yield_check"].tolist() == ["ok", "ok"]
    assert out["nearest_reading"].tolist() == ["clean/due dates/call 2029-01-15", "clean/due dates/maturity"]
    assert out["recomputed_yield_pct"].tolist() == pytest.approx([7.42884612] * 2, abs=1e-8)  # to maturity
    worst = tr.check_trade_yields(frame, terms(t17, call), options=options, workout="worst")
    assert worst["recomputed_yield_pct"].tolist() == pytest.approx([6.34690720] * 2, abs=1e-8)
    assert worst["workout_kind"].tolist() == ["call", "call"]
    assert str(worst["workout_date"].iloc[0]) == "2029-01-15"


def test_the_money_market_reading(isin):
    t17 = isin("INE000T1701")
    # T16: settlement 14 Jun 2030, clean 100: 8.5676481619% on the money-market rule, 8.7506427110% compounded
    frame = pd.DataFrame([trade(t17, 100.0, 8.5676481619, settle="2030-06-14", day="2030-06-13"),
                          trade(t17, 100.0, 8.7506427110, settle="2030-06-14", day="2030-06-13")])
    out = tr.check_trade_yields(frame, terms(t17))
    assert out["yield_check"].tolist() == ["ok", "ok"]
    assert out["nearest_reading"].iloc[1] == "clean/due dates/maturity/compounded"
    assert out["recomputed_yield_pct"].iloc[1] == pytest.approx(8.5676481619, abs=1e-9)


def test_checked_daily_rows_and_the_recomputed_panel(isin):
    t17 = isin("INE000T1701")
    frame = pd.DataFrame([trade(t17, CLEAN, 8.2612262724, amount=1e8), trade(t17, CLEAN, 8.2662660611, amount=3e8),
                          trade(t17, CLEAN, 8.40, amount=1e8)])
    checked = tr.check_trade_yields(frame, terms(t17))
    rec = tr.clean_trades(tr.recomputed_trades(checked))
    daily = tr.isin_daily_checked(rec)
    assert list(daily.columns) == [*tr.DAILY_COLUMNS, *tr.CHECKED_DAILY_COLUMNS]
    row = daily.iloc[0]
    assert row["yield_pct"] == pytest.approx(8.2612262724, abs=1e-9)  # every trade's price is the same
    assert row["reported_yield_pct"] == pytest.approx((8.2612262724 + 3 * 8.2662660611 + 8.40) / 5, abs=1e-9)
    assert row["yield_check"] == "mismatch:1;ok:2" and str(row["workout_date"]) == "2030-12-14"
    securities = load_securities(pd.DataFrame([{"isin": t17, "issuer_id": "A", "instrument_class": "BOND",
                                                "maturity_date": "2030-12-14", "sector": "NBFC"}]))
    curve = BenchmarkCurve(pd.DataFrame({"date": ["2026-11-13", "2026-11-13"], "tenor_years": [1, 5],
                                         "yield_pct": [6.0, 6.4]}), compounding="semiannual")
    ratings = rating_timeline(load_rating_actions(pd.DataFrame([
        {"date": "2025-01-01", "issuer_id": "A", "rating": "CRISIL AA+/Stable"}])))
    plain = build_price_panel(tr.isin_daily(tr.clean_trades(frame)), securities, curve, ratings)
    again = build_price_panel(tr.isin_daily(tr.clean_trades(frame)), securities, curve, ratings,
                              yield_source="reported")
    assert list(plain.columns) == list(PANEL_COLUMNS)
    pd.testing.assert_frame_equal(plain, again)  # the default panel is the same frame
    panel = build_price_panel(daily, securities, curve, ratings, yield_source="recomputed")
    assert list(panel.columns) == [*PANEL_COLUMNS, *RECOMPUTED_COLUMNS]
    assert panel["yield_pct"].iloc[0] == pytest.approx(8.2612262724, abs=1e-9)
    assert panel["reported_yield_pct"].iloc[0] == pytest.approx(row["reported_yield_pct"], abs=1e-12)
    bench = panel["benchmark_yield_pct"].iloc[0]
    assert panel["spread_bps"].iloc[0] == pytest.approx((8.2612262724 - bench) * 100, abs=1e-6)
    assert plain["spread_bps"].iloc[0] == pytest.approx((plain["yield_pct"].iloc[0] - bench) * 100, abs=1e-9)
    with pytest.raises(ValueError, match="isin_daily_checked"):
        build_price_panel(tr.isin_daily(tr.clean_trades(frame)), securities, curve, ratings,
                          yield_source="recomputed")
