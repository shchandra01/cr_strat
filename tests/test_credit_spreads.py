"""Tests of credit_spreads: G-spread, matched par spread and z-spreads of corporate bonds, and peer matrices.

Synthetic data only. T17-T18 are the corporates spec's vectors (19 Nov 2026), computed there by a scratch reference
implementation independent of the repository's pricer: an 8.95% annual NCD (allotted 14 Dec 2023, maturing 14 Dec
2030) priced for 16 Nov 2026 on the NSS curve (7.6, -2, -1, 2, 1.5, 8) plus 100 bp of continuous zero rate.
"""

import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from cr_strat import credit_spreads as cs
from cr_strat.corporates import CallPut, CorporateBond
from cr_strat.curvefit import NssCurve
from cr_strat.curves import SpreadBenchmark

S = date(2026, 11, 16)
PARAMS = (7.6, -2.0, -1.0, 2.0, 1.5, 8.0)
CURVE = NssCurve(valuation_date=S, t_min=0.25, t_max=40.0, method="nss", params=PARAMS, curve_id="nss-test")
T17 = CorporateBond(8.95, date(2030, 12, 14), date(2023, 12, 14), isin="INE000T17011")
T17_CLEAN = 102.2591928939


def on_curve(bond, s, curve, z=0.0):
    """Clean price of ``bond`` for ``s`` on ``curve`` plus ``z`` (decimal) of continuous zero rate, on due dates."""
    flows = bond.cash_flows(s)
    t = np.array([(f.date - s).days / 365 for f in flows])
    df = np.asarray(curve.discount([f.date for f in flows]), dtype=float) / float(curve.discount(s))
    return float((np.array([f.amount for f in flows]) * df * np.exp(-z * t)).sum()) - bond.accrued_interest(s)


def test_spec_vectors_t17_t18():
    m = cs.spread_measures(T17, S, T17_CLEAN, CURVE)
    assert m["accrued"] == pytest.approx(8.2634246575, abs=1e-10)
    assert m["dirty_price"] == pytest.approx(110.5226175514, abs=1e-9)
    assert m["yield_pct"] == pytest.approx(8.2612262724, abs=1e-9)
    assert m["z_spread_bp"] == pytest.approx(100.0, abs=1e-6)
    assert m["z_spread_sa_bp"] == pytest.approx(103.788758, abs=1e-6)
    assert m["curve_yield_pct"] == pytest.approx(7.1926347827, abs=1e-9)  # 7.0677519873% semi-annual, restated
    assert m["g_spread_bp"] == pytest.approx(106.859149, abs=1e-6)
    assert m["par_coupon_pct"] == pytest.approx(7.2065560908, abs=1e-9)
    assert m["par_yield_pct"] == pytest.approx(7.1961783336, abs=1e-9)
    assert m["par_spread_bp"] == pytest.approx(106.504794, abs=1e-6)
    assert m["workout_kind"] == "maturity" and m["years_to_workout"] == pytest.approx(4.079452, abs=1e-6)
    assert m["curve_id"] == "nss-test" and not m["extrapolated"]
    assert cs.g_spread_bp(T17, S, T17_CLEAN, CURVE) == m["g_spread_bp"]
    assert cs.par_spread_bp(T17, S, T17_CLEAN, CURVE) == m["par_spread_bp"]
    assert cs.z_spread_bp(T17, S, T17_CLEAN, CURVE) == pytest.approx(m["z_spread_bp"], abs=1e-12)


def test_z_spread_recovers_the_shift_and_a_par_twin_has_no_par_spread():
    for z_bp in (-40.0, 0.0, 25.0, 250.0):
        clean = on_curve(T17, S, CURVE, z_bp / 1e4)
        assert cs.z_spread_bp(T17, S, clean, CURVE) == pytest.approx(z_bp, abs=1e-8)
    coupon = cs.par_coupon_pct(T17, S, CURVE)
    twin = CorporateBond(coupon, T17.maturity, T17.issue_date)
    assert on_curve(twin, S, CURVE) == pytest.approx(100.0, abs=1e-10)  # worth par clean on the curve
    assert cs.par_spread_bp(twin, S, 100.0, CURVE) == pytest.approx(0.0, abs=1e-8)


def test_a_curve_valued_earlier_is_seen_from_settlement():
    early = NssCurve(valuation_date=date(2026, 11, 13), t_min=0.25, t_max=40.0, method="nss", params=PARAMS)
    view = cs.seen_from(early, S)
    clean = on_curve(T17, S, view, 0.0123)
    assert cs.z_spread_bp(T17, S, clean, early) == pytest.approx(123.0, abs=1e-8)
    assert cs.seen_from(CURVE, S) is CURVE
    with pytest.raises(ValueError, match="after the settlement"):
        cs.seen_from(CURVE, date(2026, 11, 13))


def test_the_money_market_rule_meets_the_curves_simple_rate():
    """One flow left within a year: the bond's yield is simple, set against the curve's simple rate to that date."""
    s = date(2030, 6, 14)
    curve = NssCurve(valuation_date=s, t_min=0.25, t_max=40.0, method="nss", params=PARAMS)
    clean = on_curve(T17, s, curve)
    y, basis = cs.curve_yield_pct(T17, s, curve)
    assert basis == "simple"
    df = float(curve.discount(T17.maturity))
    assert y == pytest.approx((1 / df - 1) / ((T17.maturity - s).days / 365) * 100, rel=1e-14)
    assert cs.g_spread_bp(T17, s, clean, curve) == pytest.approx(0.0, abs=1e-9)  # on the curve: no spread
    assert cs.g_spread_bp(T17, s, clean - 0.5, curve) > 0


def test_zero_coupon_and_amortising_bonds():
    zero = CorporateBond(0.0, date(2031, 2, 3), date(2026, 2, 3), frequency=0, redemption=140.0)
    clean = on_curve(zero, S, CURVE, 0.008)
    assert cs.z_spread_bp(zero, S, clean, CURVE) == pytest.approx(80.0, abs=1e-8)
    assert math.isnan(cs.par_coupon_pct(zero, S, CURVE))
    assert cs.par_spread_bp(zero, S, on_curve(zero, S, CURVE), CURVE) == pytest.approx(0.0, abs=1e-8)
    amort = CorporateBond(9.0, date(2031, 4, 1), date(2026, 4, 1),
                          amortisation=((date(2029, 4, 1), 30.0), (date(2030, 4, 1), 30.0)))
    later = date(2029, 10, 1)
    curve = NssCurve(valuation_date=later, t_min=0.25, t_max=40.0, method="nss", params=PARAMS)
    coupon = cs.par_coupon_pct(amort, later, curve)
    twin = CorporateBond(coupon, amort.maturity, amort.issue_date, amortisation=amort.amortisation)
    assert on_curve(twin, later, curve) == pytest.approx(70.0, abs=1e-10)  # par is the outstanding 70%
    assert cs.par_spread_bp(twin, later, 70.0, curve) == pytest.approx(0.0, abs=1e-8)
    assert cs.z_spread_bp(amort, later, on_curve(amort, later, curve, 0.015), curve) == pytest.approx(150.0, abs=1e-8)


def test_workout_to_worst_measures_to_the_call():
    """T11's bond: 8% annual, allotted 15 Jan 2024, maturing 15 Jan 2034, calls at par 2029-2031, a put in 2028."""
    s = date(2027, 1, 15)
    curve = NssCurve(valuation_date=s, t_min=0.25, t_max=40.0, method="nss", params=PARAMS)
    bond = CorporateBond(8.0, date(2034, 1, 15), date(2024, 1, 15),
                         options=(CallPut("call", date(2029, 1, 15)), CallPut("call", date(2030, 1, 15)),
                                  CallPut("call", date(2031, 1, 15)), CallPut("put", date(2028, 1, 15))))
    clean = 103.0  # dirty 103 on a coupon date: T11's YTW 6.34690720% to 15 Jan 2029
    m = cs.spread_measures(bond, s, clean, curve, workout="worst")
    assert m["workout_date"] == date(2029, 1, 15) and m["workout_kind"] == "call"
    assert m["yield_pct"] == pytest.approx(6.34690720, abs=1e-8)
    to_call = bond.to_workout(date(2029, 1, 15))
    assert m["g_spread_bp"] == pytest.approx((6.34690720 - cs.curve_yield_pct(to_call, s, curve)[0]) * 100, abs=1e-6)
    assert m["z_spread_bp"] == pytest.approx(cs.z_spread_bp(to_call, s, clean, curve), abs=1e-9)
    to_maturity = cs.spread_measures(bond, s, clean, curve)
    assert to_maturity["yield_pct"] == pytest.approx(7.42884612, abs=1e-8) and to_maturity["g_spread_bp"] > m[
        "g_spread_bp"]
    with pytest.raises(ValueError, match="needs a yield"):
        cs.workout_bond(bond, s, clean, "sebi")


def terms_frame():
    return pd.DataFrame([
        {"isin": "INE000T17011", "kind": "corporate", "coupon_pct": "8.95", "frequency": "1",
         "issue_date": "2023-12-14", "maturity_date": "2030-12-14"},
        {"isin": "INE000CALL01", "kind": "corporate", "coupon_pct": "8.0", "frequency": "1",
         "issue_date": "2024-01-15", "maturity_date": "2034-01-15"},
        {"isin": "INE000FRB001", "kind": "frb_corp", "coupon_pct": "8.0", "frequency": "4",
         "issue_date": "2025-01-15", "maturity_date": "2028-01-15"},
    ])


def test_spread_table_measures_rows_and_keeps_errors():
    options = pd.DataFrame([{"isin": "INE000CALL01", "kind": "call", "date": "2029-01-15"}])
    frame = pd.DataFrame([
        {"isin": "INE000T17011", "settlement_date": "2026-11-16", "clean_price": T17_CLEAN},
        {"isin": "INE000CALL01", "settlement_date": "2026-11-16", "clean_price": 104.0},
        {"isin": "INE000NONE01", "settlement_date": "2026-11-16", "clean_price": 100.0},
        {"isin": "INE000FRB001", "settlement_date": "2026-11-16", "clean_price": 100.0},
    ])
    table = cs.spread_table(frame, terms_frame(), CURVE, options=options, workout="worst", calendar=None)
    assert list(table.columns) == list(cs.SPREAD_COLUMNS) and list(table["isin"]) == list(frame["isin"])
    rows = table.set_index("isin")
    assert rows.loc["INE000T17011", "g_spread_bp"] == pytest.approx(106.859149, abs=1e-6)
    assert rows.loc["INE000T17011", "error"] == ""
    assert rows.loc["INE000CALL01", "workout_kind"] in ("call", "maturity") and rows.loc["INE000CALL01", "error"] == ""
    assert "no terms" in rows.loc["INE000NONE01", "error"] and math.isnan(rows.loc["INE000NONE01", "g_spread_bp"])
    assert "discount margin" in rows.loc["INE000FRB001", "error"]
    shuffled = cs.spread_table(frame.iloc[::-1], terms_frame(), CURVE, options=options, workout="worst",
                               calendar=None)
    pd.testing.assert_frame_equal(shuffled.loc[table.index], table)  # row order does not change a row's numbers


def test_spread_table_from_yields_and_the_sebi_rule():
    options = pd.DataFrame([{"isin": "INE000CALL01", "kind": "call", "date": "2029-01-15"},
                            {"isin": "INE000CALL01", "kind": "put", "date": "2028-01-15"}])
    s = "2027-01-15"
    curve = NssCurve(valuation_date=date(2027, 1, 15), t_min=0.25, t_max=40.0, method="nss", params=PARAMS)
    frame = pd.DataFrame([{"isin": "INE000CALL01", "settlement_date": s, "yield_pct": 7.0},
                          {"isin": "INE000CALL01", "settlement_date": s, "yield_pct": 9.0}])
    table = cs.spread_table(frame, terms_frame(), curve, options=options, workout="sebi", calendar=None)
    # T12: at 7% the call trigger (15 Jan 2029) values the bond at 101.790534; at 9% the put trigger at 99.082569
    assert list(table["workout_date"]) == [date(2029, 1, 15), date(2028, 1, 15)]
    assert table["clean_price"].tolist() == pytest.approx([101.790534, 99.082569], abs=1e-6)
    assert table["yield_pct"].tolist() == pytest.approx([7.0, 9.0], abs=1e-9)
    with pytest.raises(ValueError, match="workout must be one of"):
        cs.spread_table(frame, terms_frame(), curve, workout="best")


def panel_frame():
    rows = []
    for k, issuer in enumerate(("I1", "I2", "I3", "I4")):
        for day in (date(2026, 11, 12), date(2026, 11, 13), date(2026, 11, 16)):
            rows.append({"date": pd.Timestamp(day), "isin": f"INE{k}", "issuer_id": issuer, "instrument_class": "bond",
                         "rating_bucket": "AA", "tenor_bucket": "3-5y", "tenor_years": 3.5 + k * 0.2,
                         "spread_bps": 150.0 + 10 * k})
            rows.append({"date": pd.Timestamp(day), "isin": f"INF{k}", "issuer_id": issuer, "instrument_class": "bond",
                         "rating_bucket": "AA", "tenor_bucket": "5-10y", "tenor_years": 7.0,
                         "spread_bps": 180.0 + 10 * k})
    return pd.DataFrame(rows)


def test_peer_matrix_and_matrix_yield():
    matrix = cs.build_peer_matrix(panel_frame(), date=date(2026, 11, 16), window_days=5, min_issuers=3)
    rows = matrix.set_index("tenor_bucket")
    assert rows.loc["3-5y", "spread_bp"] == pytest.approx(165.0) and rows.loc["3-5y", "n_issuers"] == 4
    assert rows.loc["3-5y", "tenor_years"] == pytest.approx(3.8)
    assert rows.loc["5-10y", "spread_bp"] == pytest.approx(195.0)
    kept = cs.read_spread_matrix(matrix)
    assert cs.matrix_spread_bp(kept, tenor_years=5.4, rating_bucket="AA") == pytest.approx(165.0 + 30.0 * 1.6 / 3.2)
    assert cs.matrix_spread_bp(kept, tenor_years=1.0, rating_bucket="AA") == pytest.approx(165.0)  # flat at the ends
    assert cs.matrix_spread_bp(kept, tenor_years=20.0, rating_bucket="AA") == pytest.approx(195.0)
    assert math.isnan(cs.matrix_spread_bp(kept, tenor_years=4.0, rating_bucket="A"))
    assert cs.matrix_yield(kept, tenor_years=7.0, base_yield_pct=7.10, rating_bucket="AA") == pytest.approx(9.05)
    thin = cs.build_peer_matrix(panel_frame(), date=date(2026, 11, 16), min_issuers=5)
    assert thin["spread_bp"].isna().all() and (thin["n_issuers"] == 4).all()
    bad = pd.DataFrame([{"rating_bucket": "AA", "tenor_years": 3.0, "spread_bp": 1.0},
                        {"rating_bucket": "AA", "tenor_years": 3.0, "spread_bp": 2.0}])
    with pytest.raises(ValueError, match="two spreads"):
        cs.read_spread_matrix(bad)


def test_add_peer_spreads_leaves_out_the_issuer():
    bench = SpreadBenchmark(panel_frame(), window_days=5, min_issuers=3)
    table = pd.DataFrame([{"isin": "INE0", "issuer_id": "I1", "instrument_class": "bond", "rating_bucket": "AA",
                           "tenor_bucket": "3-5y", "settlement_date": date(2026, 11, 16), "g_spread_bp": 140.0},
                          {"isin": "NEW1", "issuer_id": "I9", "instrument_class": "bond", "rating_bucket": "AA",
                           "tenor_bucket": "3-5y", "settlement_date": date(2026, 11, 16), "g_spread_bp": 200.0}])
    out = cs.add_peer_spreads(table, bench).set_index("isin")
    assert out.loc["INE0", "peer_spread_bp"] == pytest.approx(170.0) and out.loc["INE0", "peer_issuers"] == 3
    assert out.loc["INE0", "relative_spread_bp"] == pytest.approx(-30.0)
    assert out.loc["NEW1", "peer_spread_bp"] == pytest.approx(165.0) and out.loc["NEW1", "peer_issuers"] == 4
    with pytest.raises(ValueError, match="lacks"):
        cs.add_peer_spreads(table.drop(columns=["rating_bucket"]), bench)


def test_refusals():
    from cr_strat.floating import GoiFloatingRateBond
    frb = GoiFloatingRateBond(date(2033, 9, 22), date(2020, 6, 22), 1.22, ((date(2026, 9, 22), 7.7),),
                              first_coupon_date=date(2020, 9, 22))
    with pytest.raises(TypeError, match="discount margin"):
        cs.g_spread_bp(frb, S, 100.0, CURVE)
    with pytest.raises(ValueError, match="positive"):
        cs.spread_measures(T17, S, -1.0, CURVE)
    with pytest.raises(ValueError, match="flow_dates"):
        cs.z_spread_bp(T17, S, T17_CLEAN, CURVE, flow_dates="scheduled")
