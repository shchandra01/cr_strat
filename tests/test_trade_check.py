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


# ---------------------------------------------------------------- fixes of the review


def _panel_inputs(isin_code: str, maturity: str = "2034-01-15"):
    securities = load_securities(pd.DataFrame([{"isin": isin_code, "issuer_id": "B", "instrument_class": "BOND",
                                                "maturity_date": maturity, "sector": "NBFC"}]))
    ratings = rating_timeline(load_rating_actions(pd.DataFrame([
        {"date": "2025-01-01", "issuer_id": "B", "rating": "CRISIL AA/Stable"}])))
    return securities, ratings


def test_a_recomputed_panel_measures_the_spread_at_the_workout(isin):
    """The completeness review's case: a print to the 2029 call (6.3469%) was set against the curve at its 2034
    maturity, -77.56 bp in the 5-10y bucket, where the spread to the call is +25.61 bp."""
    t17, call = isin("INE000T1701"), isin("INE000CAL01")
    options = pd.DataFrame([{"isin": call, "kind": "call", "date": "2029-01-15"},
                            {"isin": call, "kind": "call", "date": "2030-01-15"}])
    frame = pd.DataFrame([trade(call, 103.0, 6.34690720, settle="2027-01-15", day="2027-01-14")])
    checked = tr.check_trade_yields(frame, terms(t17, call), options=options, workout="worst")
    daily = tr.isin_daily_checked(tr.clean_trades(tr.recomputed_trades(checked)))
    securities, ratings = _panel_inputs(call)
    curve = BenchmarkCurve(pd.DataFrame({"date": ["2027-01-14"] * 2, "tenor_years": [2, 7], "yield_pct": [6.0, 7.0]}),
                           compounding="semiannual")
    row = build_price_panel(daily, securities, curve, ratings, yield_source="recomputed").iloc[0]
    assert (round(row["tenor_years"], 4), row["tenor_bucket"]) == (2.0041, "1-3y")
    assert (round(row["benchmark_yield_pct"], 6), round(row["spread_bps"], 4)) == (6.090846, 25.6061)
    reported = build_price_panel(tr.isin_daily(tr.clean_trades(frame)), securities, curve, ratings).iloc[0]
    assert round(reported["tenor_years"], 4) == 7.0034  # the reported panel stays at maturity


def test_a_dirty_print_enters_the_panel_on_its_dirty_reading(isin):
    """A print at the dirty price 110.522618 reported at 8.2612% matched the dirty reading but went into the panel at
    the clean reading's 5.957%, its day checked 'ok'."""
    t17 = isin("INE000T1701")
    dirty = CLEAN + 8.2634246575  # T17's accrued interest on 16 Nov 2026
    frame = pd.DataFrame([trade(t17, dirty, 8.2612262724)] * 3)
    checked = tr.check_trade_yields(frame, terms(t17))
    assert checked["yield_check"].tolist() == ["dirty_price"] * 3
    assert checked["recomputed_yield_pct"].tolist() == pytest.approx([8.2612262724] * 3, abs=1e-8)
    assert "read as dirty" in checked["check_notes"].iloc[0]
    daily = tr.isin_daily_checked(tr.clean_trades(tr.recomputed_trades(checked))).iloc[0]
    assert daily["yield_pct"] == pytest.approx(8.2612262724, abs=1e-8) and daily["yield_check"] == "dirty_price:3"


def test_prints_of_a_partly_redeemed_bond_are_per_100_outstanding(isin):
    """Spec T14 (30 of 100 redeemed): 99.9031 per 100 of the 70 outstanding is a 9% yield; the build read every print
    per 100 original face (-21.11%, a mismatch). A print typed per 100 original is recognised as the other basis."""
    bond = isin("INE000AM001")
    table = pd.DataFrame([{"isin": bond, "kind": "corporate", "coupon_pct": "9", "frequency": "1",
                           "issue_date": "2026-04-01", "maturity_date": "2031-04-01"}])
    amort = pd.DataFrame([{"isin": bond, "date": "2029-04-01", "principal_per100": "30"},
                          {"isin": bond, "date": "2030-04-01", "principal_per100": "30"}])
    frame = pd.DataFrame([trade(bond, 99.9031, 9.0, settle="2029-10-01", day="2029-09-28"),
                          trade(bond, 69.9321, 9.0, settle="2029-10-01", day="2029-09-28")])
    out = tr.check_trade_yields(frame, table, amortisation=amort)
    assert out["yield_check"].tolist() == ["ok", "face_basis"]
    assert out["recomputed_yield_pct"].tolist() == pytest.approx([9.0, 9.0], abs=1e-4)
    assert out["nearest_reading"].iloc[1].startswith("per 100 original face/clean")
    original = tr.check_trade_yields(frame, table, amortisation=amort, price_basis="original")
    assert original["yield_check"].tolist() == ["face_basis", "ok"]


def test_the_trade_check_uses_exercises_only_once_public(isin):
    """A call known (from 2028) not to be exercised still makes a 2026 print's yield to it a reading; an exercise
    public before the trade day values the bond to its call."""
    t17, call = isin("INE000T1701"), isin("INE000CAL01")
    declined = pd.DataFrame([{"isin": call, "kind": "call", "date": "2029-01-15", "exercised": "no",
                              "exercised_public_from": "2028-12-01"}])
    frame = pd.DataFrame([trade(call, 103.0, 6.4450279, settle="2026-11-16", day="2026-11-13")])
    out = tr.check_trade_yields(frame, terms(t17, call), options=declined)
    assert (out["yield_check"].iloc[0], out["nearest_reading"].iloc[0]) == ("ok", "clean/due dates/call 2029-01-15")
    assert out["known_as_of"].iloc[0] == pd.Timestamp("2026-11-13 00:00")
    exercised = pd.DataFrame([{"isin": call, "kind": "call", "date": "2027-01-15", "exercised": "yes",
                               "exercised_public_from": "2026-11-02T17:00"}])
    out = tr.check_trade_yields(frame.assign(price=100.086466, yield_pct=7.0), terms(t17, call), options=exercised)
    assert out["recomputed_yield_pct"].iloc[0] == pytest.approx(7.0, abs=1e-5)
    assert (str(out["workout_date"].iloc[0]), out["workout_kind"].iloc[0]) == ("2027-01-15", "exercised_call")


def test_yield_and_deal_types_are_read(tmp_path, isin):
    """A 'Yield Type' column before the yield was read as the yield (NaN yields); the deal and yield types of SEBI's
    format were dropped, so inter-scheme transfers entered the daily yields."""
    call = isin("INE000CAL01")
    path = tmp_path / "trades.csv"
    pd.DataFrame({"ISIN": [call] * 3, "Trade Date": ["14-Jan-2027"] * 3, "Deal Type": ["Direct", "IST", "Brokered"],
                  "Traded Price (Rs)": [103.0, 101.0, 103.0], "Yield Type": ["YTC", "YTM", "YTM"],
                  "Traded Yield (%)": [6.3469, 7.6, 7.4288], "Trade Value (Rs lakh)": [500, 500, 500]}
                 ).to_csv(path, index=False)
    trades, report = tr.load_trades(path, "NSE")
    assert "yield_pct=Traded Yield (%)" in report["columns"].iloc[0] and "yield_type=Yield Type" in \
        report["columns"].iloc[0]
    assert trades["yield_pct"].tolist() == [6.3469, 7.6, 7.4288] and trades["deal_type"].tolist()[1] == "IST"
    daily = tr.isin_daily(trades)
    assert daily["n_trades"].iloc[0] == 2  # the inter-scheme transfer left out
    assert daily["yield_pct"].iloc[0] == pytest.approx((6.3469 + 7.4288) / 2)
    options = pd.DataFrame([{"isin": call, "kind": "call", "date": "2029-01-15"}])
    checked = tr.check_trade_yields(trades.assign(settlement_date=pd.Timestamp("2027-01-15")),
                                    terms(isin("INE000T1701"), call), options=options, tolerance_bp=5)
    assert checked["nearest_reading"].iloc[0] == "clean/due dates/call 2029-01-15"  # YTC: the call readings only
    assert checked["nearest_reading"].iloc[2] == "clean/due dates/maturity"
    assert tr.declared_workout("Yield to Call") == "call" and tr.declared_workout("") is None
    assert tr.is_excluded_deal("Inter-Scheme Transfer") and not tr.is_excluded_deal("Direct")
    plain = tr.load_trades(path, "NSE")[0].drop(columns=["deal_type", "yield_type"])
    assert list(plain.columns) == list(tr.TRADE_COLUMNS)  # files without the columns: the canonical table


def test_a_recomputed_panel_can_keep_bonds_without_terms(isin):
    t17, other = isin("INE000T1701"), isin("INE000OTH01")
    frame = pd.DataFrame([trade(t17, CLEAN, 8.2612262724), trade(other, 100.0, 8.0)])
    checked = tr.check_trade_yields(frame, terms(t17))
    assert len(tr.recomputed_trades(checked)) == 1  # left out by default
    kept = tr.recomputed_trades(checked, without_terms="reported")
    assert kept["yield_pct"].tolist() == pytest.approx([8.2612262724, 8.0])
    daily = tr.isin_daily_checked(tr.clean_trades(kept)).set_index("isin")
    assert daily.loc[other, "yield_check"] == "no_terms:1" and pd.isna(daily.loc[other, "recomputed_yield_pct"])
    assert daily.loc[other, "yield_pct"] == 8.0
    with pytest.raises(ValueError, match="without_terms"):
        tr.recomputed_trades(checked, without_terms="guess")
