"""Tests of portfolio construction (spec section 6): equal risk contribution (T27, identity I8), drawdown de-risking
(T28), sizing (T29), key-rate hedges by NNLS (T30), Euler contributions (T31, T32, identity I9), the hedge-effect
numbers of T26, covariance, allocation under both legal forms, rebalancing. Synthetic numbers only."""

import math
from datetime import date, datetime
from statistics import NormalDist

import numpy as np
import pandas as pd
import pytest

from cr_strat import portfolio as pf
from cr_strat import returns as rt
from cr_strat import risk as rk

VOL = np.array([0.10, 0.20, 0.30, 0.40])
RHO = np.array([[1, .8, 0, 0], [.8, 1, 0, 0], [0, 0, 1, -.5], [0, 0, -.5, 1]])
COV = pd.DataFrame(np.outer(VOL, VOL) * RHO, index=list("ABCD"), columns=list("ABCD"))
T27 = [0.3836125063, 0.1918062531, 0.2426178518, 0.1819633888]
UNREG = rt.ACCESS_PRESETS["unregulated_company"]
NBFC = rt.ACCESS_PRESETS["nbfc_non_pd"]


def test_t27_erc_by_coordinate_descent_and_newton_agree():
    w1, n1 = pf.erc_weights(COV, method="ccd", tol=1e-14)
    w2, n2 = pf.erc_weights(COV, method="newton", tol=1e-14)
    assert w1.tolist() == pytest.approx(T27, abs=1e-10)
    assert w2.tolist() == pytest.approx(T27, abs=1e-10)
    assert float((w1 - w2).abs().max()) < 1e-12
    assert n1 == 17 and n2 <= 6  # the spec's 17 sweeps; Newton in at most 6 steps
    rc, sigma = pf.risk_contributions(w1, COV)
    assert sigma == pytest.approx(0.102934036928, abs=1e-12)
    assert rc.tolist() == pytest.approx([0.025733509232] * 4, abs=1e-12)  # each a quarter (I8)
    assert float(rc.sum()) == pytest.approx(sigma, abs=1e-15)


def test_i8_constant_correlation_erc_is_inverse_volatility():
    rho = np.full((4, 4), 0.3) + 0.7 * np.eye(4)
    cov = pd.DataFrame(np.outer(VOL, VOL) * rho, index=list("ABCD"), columns=list("ABCD"))
    w, _ = pf.erc_weights(cov)
    inv = pf.inverse_vol_weights(pd.Series(VOL, index=list("ABCD")))
    assert w.tolist() == pytest.approx([0.48, 0.24, 0.16, 0.12], abs=1e-10)
    assert inv.tolist() == pytest.approx([0.48, 0.24, 0.16, 0.12], abs=1e-15)


def test_risk_budgets_give_contributions_in_their_shares():
    budgets = {"A": 0.4, "B": 0.3, "C": 0.2, "D": 0.1}
    for method in ("ccd", "newton"):
        w, _ = pf.erc_weights(COV, budgets, method=method)
        rc, sigma = pf.risk_contributions(w, COV)
        assert (rc / sigma).tolist() == pytest.approx([0.4, 0.3, 0.2, 0.1], abs=1e-10)
    with pytest.raises(ValueError, match="positive"):
        pf.erc_weights(COV, {"A": 0.5, "B": 0.5, "C": 0.0, "D": 0.0})
    with pytest.raises(ValueError, match="no risk budget"):
        pf.erc_weights(COV, {"A": 1.0})


def test_t28_drawdown_scale_with_hysteresis():
    index = [1.00, 1.01, 0.99, 0.975, 0.97, 0.955, 0.96, 0.98, 0.997, 1.00, 1.02]
    dd = pf.drawdowns(index)
    assert dd.round(4).tolist() == [0.0, 0.0, -0.0198, -0.0347, -0.0396, -0.0545, -0.0495, -0.0297, -0.0129,
                                    -0.0099, 0.0]
    assert pf.drawdown_scale(index).tolist() == [1, 1, 1, 0.5, 0.5, 0.25, 0.25, 0.25, 1, 1, 1]
    with pytest.raises(ValueError, match="recover"):
        pf.drawdown_scale(index, recover=-0.04)  # below the first trigger: no hysteresis


def test_stop_loss_halves_until_back_above_half_the_trigger():
    pnl = pd.Series([0.0, -10.0, -15.0, 5.0, 12.0, 0.0])
    scale = pf.stop_loss_scale(pnl, 1000.0, days=20, trigger=-0.02)
    # cumulative -10, -25 (2.5% down: halved), -20 (still below -1%), -8 (above -1%: back to 1), +4
    assert scale.tolist() == [1.0, 1.0, 0.5, 0.5, 1.0, 1.0]


def test_t29_sizing_and_lots():
    face = pf.size_to_dv01(5e5, 0.064282892630)
    assert face == pytest.approx(77_78_11_917.83, abs=0.01)
    assert pf.round_to_lot(face) == 75e7  # down to Rs 5 crore lots
    assert pf.round_to_lot(-face) == -75e7 and pf.round_to_lot(4e7) == 0.0
    assert pf.size_to_cs01(5e5, 0.036147101087) == pytest.approx(5e5 / 0.036147101087 * 100)
    assert pf.size_to_vol(0.01, 0.004) == 2.0 and pf.size_to_vol(0.01, 0.02) == 0.5  # capped at 2
    assert pf.hedge_gsec_face(6_42_828.9263, 0.064282892630) == pytest.approx(-1e9, rel=1e-12)
    with pytest.raises(ValueError):
        pf.size_to_dv01(5e5, 0.0)


def test_t30_key_rate_hedge_least_squares_and_nnls():
    k = pd.DataFrame({"2y": [0, -190, -5, 0, 0], "5y": [0, 0, -10, -460, -5], "10y": [0, 0, 0, -20, -760]},
                     index=[1, 2, 3, 5, 10], dtype=float)
    book = pd.Series([5_000.0, -60_000, 30_000, 1_20_000, 3_80_000], index=[1, 2, 3, 5, 10])
    free, _ = pf.key_rate_hedge(book, k, nonneg=False)
    assert free.tolist() == pytest.approx([-311.751666, 240.578339, 498.395297], abs=1e-6)
    pos, resid = pf.key_rate_hedge(book, k, nonneg=True)
    assert pos.tolist() == pytest.approx([0.0, 240.504615, 498.396955], abs=1e-6)
    assert resid.tolist() == pytest.approx([5_000, -60_000, 27_594.9539, -600.0619, 15.7911], abs=1e-4)
    # NNLS against a brute-force check on the active set: no feasible move lowers the error
    x = pf.nnls(k.to_numpy(), -book.to_numpy())
    grad = k.to_numpy().T @ (-book.to_numpy() - k.to_numpy() @ x)
    assert all(g <= 1e-6 for g, xi in zip(grad, x) if xi == 0)
    assert all(abs(g) < 1e-3 for g, xi in zip(grad, x) if xi > 0)


def test_t31_parametric_euler_components_sum_to_the_var():
    x = np.array([30e7, 50e7, 20e7])
    sd = np.array([0.0010, 0.0020, 0.0030])
    corr = np.array([[1, 0.5, 0.2], [0.5, 1, -0.3], [0.2, -0.3, 1]])
    comp = pf.component_var(x, np.outer(sd, sd) * corr, 0.99)
    assert comp.tolist() == pytest.approx([5_31_018.9919, 18_66_262.3991, 4_15_580.0806], abs=1e-4)
    assert float(comp.sum()) == pytest.approx(28_12_861.4716, abs=1e-4)


def test_t32_historical_es_components_with_ties_to_the_earliest():
    a = [3, -5, 1, -2, 4, -7, 0, 2, -1, 6, -3, 5, -4, 1, -6, 2, 0, -2, 3, -1]
    b = [-1, 2, -3, 1, -2, 1, 4, -5, 2, -1, 0, 3, -2, -4, 1, 2, -3, 5, -1, 0]
    pnl = pd.DataFrame({"A": a, "B": b}, dtype=float)
    comp = pf.component_es(pnl, 0.875)
    assert comp.tolist() == pytest.approx([5.6, 0.2], abs=1e-12)
    assert float(comp.sum()) == pytest.approx(rk.hs_es(pnl.sum(axis=1), 0.875), abs=1e-12)  # I9: exactly the ES
    hs = pf.component_var_hs(pnl, 0.875)
    assert float(hs.sum()) == pytest.approx(rk.hs_var(pnl.sum(axis=1), 0.875), abs=1e-12)


def test_t26_numbers_behind_the_hedge_effect_test():
    z = NormalDist().inv_cdf(0.99)
    sd = np.array([6.0, 5.0, 4.5])
    corr = np.array([[1, .8, .6], [.8, 1, .85], [.6, .85, 1]])
    cov = np.outer(sd, sd) * corr
    def var(s):
        return z * math.sqrt(np.asarray(s) @ cov @ np.asarray(s))
    assert var([0, 0, 6e5]) == pytest.approx(62_81_140, abs=1.0)
    assert var([-6e5, 0, 6e5]) == pytest.approx(68_16_636, abs=1.0)  # the 2-year payer: more risk
    assert var([0, -3e4, 3e4]) == pytest.approx(1_84_648, abs=1.0)  # the 10-year payer
    assert float(pf.component_var(np.array([-6e5, 0, 6e5]), cov, 0.99).sum()) == pytest.approx(68_16_636, abs=1.0)


def _returns(n=120, seed=3):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2026-01-01", periods=n).date
    x = rng.multivariate_normal([0, 0, 0], np.outer([1e-3, 2e-3, 3e-3], [1e-3, 2e-3, 3e-3])
                                * np.array([[1, .3, 0], [.3, 1, -.2], [0, -.2, 1]]), size=n)
    return pd.DataFrame(x, index=days, columns=["S1_asw", "S3_rv", "S7_policy"])


def test_covariance_matches_the_risk_layers_ewma_and_drops_days_pairwise():
    r = _returns()
    cov, obs = pf.covariance(r)
    ref, n = rk.ewma_covariance(r.to_numpy())
    assert np.allclose(cov.to_numpy(), ref, rtol=1e-12, atol=0) and (obs.to_numpy() == n).all()
    gappy = r.copy()
    gappy.iloc[:40, 2] = np.nan  # S7 had no capital at first
    cov2, obs2 = pf.covariance(gappy, method="sample")
    assert obs2.loc["S7_policy", "S7_policy"] == 80 and obs2.loc["S1_asw", "S3_rv"] == 120
    pair = gappy[["S1_asw", "S7_policy"]].dropna()
    assert cov2.loc["S1_asw", "S7_policy"] == pytest.approx(np.cov(pair.to_numpy(), rowvar=False, ddof=1)[0, 1])
    with pytest.raises(ValueError, match="joint observation"):
        pf.covariance(r.iloc[:10])


def test_make_positive_definite_shrinks_to_the_diagonal():
    bad = pd.DataFrame([[1.0, 0.9, -0.9], [0.9, 1.0, 0.9], [-0.9, 0.9, 1.0]], index=list("xyz"), columns=list("xyz"))
    fixed, delta = pf.make_positive_definite(bad)
    assert delta > 0 and delta == pytest.approx(round(delta / 0.05) * 0.05)
    assert np.linalg.eigvalsh(fixed.to_numpy()).min() > 0
    smaller = (1 - (delta - 0.05)) * bad.to_numpy() + (delta - 0.05) * np.diag(np.diag(bad.to_numpy()))
    assert np.linalg.eigvalsh(smaller).min() <= 1e-12  # the smallest step that works
    same, zero = pf.make_positive_definite(COV)
    assert zero == 0.0 and np.array_equal(same.to_numpy(), COV.to_numpy())


def test_es_parity_reaches_its_budgets_or_refuses():
    r = _returns(250)
    w, n = pf.es_parity_weights(r[["S1_asw", "S3_rv"]])
    comp = pf.component_es(r[["S1_asw", "S3_rv"]].to_numpy() * w.to_numpy(), 0.975)
    assert (comp / comp.sum()).tolist() == pytest.approx([0.5, 0.5], abs=1e-6) and n < 500
    hedge = pd.DataFrame({"a": r["S1_asw"], "b": -r["S1_asw"] * 0.5 + 1e-5 * r["S3_rv"]})
    with pytest.raises(ValueError, match="ES parity"):
        pf.es_parity_weights(hedge)


def test_feasible_strategies_by_legal_form():
    names = ["S1_asw", "S3_rv", "S7_policy", "macro"]
    unreg = pf.feasible_strategies(UNREG, names)
    assert unreg["S7_policy"][0] is False and "hedging only" in unreg["S7_policy"][1]
    assert unreg["S3_rv"][0] and "long-only" in unreg["S3_rv"][1]
    assert unreg["S1_asw"][0] and "unfunded" in unreg["S1_asw"][1]
    assert "no legal-form rule" in unreg["macro"][1]
    nbfc = pf.feasible_strategies(NBFC, names)
    assert all(ok for ok, _ in nbfc.values()) and "7 times" in nbfc["S1_asw"][1]
    assert pf.strategy_code("s10-carry") == "S10" and pf.strategy_code("S1") == "S1" and pf.strategy_code("x") is None


def test_allocation_never_weights_a_forbidden_strategy_and_says_why():
    r = _returns(150)
    erc_u = pf.allocate("erc_vol", as_of=datetime(2026, 12, 31, 20), access=UNREG, returns=r)
    by = erc_u.set_index("strategy")
    assert by.loc["S7_policy", "weight"] == 0.0 and not by.loc["S7_policy", "allowed"]
    assert by["weight"].sum() == pytest.approx(1.0, abs=1e-12)
    shares = by.loc[["S1_asw", "S3_rv"], "risk_share"]
    assert shares.tolist() == pytest.approx([0.5, 0.5], abs=1e-9)  # ERC over the two allowed
    erc_n = pf.allocate("erc_vol", as_of=datetime(2026, 12, 31, 20), access=NBFC, returns=r).set_index("strategy")
    assert erc_n["risk_share"].tolist() == pytest.approx([1 / 3] * 3, abs=1e-9)
    fixed = pd.DataFrame([{"strategy": "S1_asw", "weight": 0.5}, {"strategy": "S3_rv", "weight": 0.3},
                          {"strategy": "S7_policy", "weight": 0.2}])
    fu = pf.allocate("fixed", as_of=date(2026, 12, 31), access=UNREG, returns=r, fixed=fixed).set_index("strategy")
    assert fu["weight"].tolist() == pytest.approx([0.625, 0.375, 0.0])
    assert "set to 0" in fu.loc["S7_policy", "notes"] and "scaled by" in fu.loc["S1_asw", "notes"]
    inv = pf.allocate("inverse_vol", as_of=date(2026, 12, 31), access=NBFC, returns=r).set_index("strategy")
    vols = np.sqrt(np.diag(pf.covariance(r)[0].to_numpy()))
    assert inv["weight"].tolist() == pytest.approx(((1 / vols) / (1 / vols).sum()).tolist(), abs=1e-12)
    es = pf.allocate("erc_es", as_of=date(2026, 12, 31), access=NBFC, returns=_returns(250))
    assert list(es.columns) == list(pf.ALLOCATION_COLUMNS)
    es = es.set_index("strategy")
    assert es["risk_share"].tolist() == pytest.approx([1 / 3] * 3, abs=1e-6) and es["weight"].sum() == \
        pytest.approx(1.0)
    with pytest.raises(ValueError, match="did not converge in 500 iterations \\(last error"):
        pf.es_parity_weights(_returns(500, 3))  # no weights give the budgets (piecewise-linear ES): refused


def test_bounds_clip_and_resolve_and_budgets_scale():
    r = _returns(150)
    bounds = pd.DataFrame([{"strategy": "S1_asw", "max_weight": 0.3}])
    out = pf.allocate("erc_vol", as_of=date(2026, 12, 31), access=NBFC, returns=r, bounds=bounds)
    w = out.set_index("strategy")["weight"]
    assert w["S1_asw"] == pytest.approx(0.3) and w.sum() == pytest.approx(1.0)
    with pytest.raises(ValueError, match="bounds"):
        pf.allocate("erc_vol", as_of=date(2026, 12, 31), access=NBFC, returns=r,
                    bounds=pd.DataFrame([{"strategy": s, "max_weight": 0.2} for s in r.columns]))
    budgets = pd.DataFrame([{"strategy": "S1_asw", "metric": "dv01", "budget": 5e5, "effective_from": "2026-01-01"},
                            {"strategy": "S3_rv", "metric": "dv01", "budget": 5e5, "effective_from": "2027-01-01"}])
    scales = pf.budget_scales({"S1_asw": -1e6, "S3_rv": 1e5}, budgets, as_of=date(2026, 12, 31))
    assert scales.to_dict() == {"S1_asw": 0.5}  # S3's budget is not in force yet
    up = pf.budget_scales({"S1_asw": 2.5e5}, budgets, as_of=date(2026, 12, 31), allow_scale_up=True)
    assert up["S1_asw"] == 2.0
    only = pf.allocate("budget", as_of=date(2026, 12, 31), access=UNREG, budgets=budgets,
                       metrics={"S1_asw": 1e6}).set_index("strategy")
    assert only.loc["S1_asw", "scale"] == 0.5 and math.isnan(only.loc["S1_asw", "weight"])


def test_rebalancing_rules_and_the_open_breach_block():
    cur, tgt = {"a": 0.5, "b": 0.5}, {"a": 0.56, "b": 0.44}
    due, why = pf.rebalance_due(cur, tgt, last=date(2026, 11, 2), as_of=date(2026, 11, 20), rule="threshold")
    assert due and "threshold" in why[0]  # 6 points past the 5-point band
    due, _ = pf.rebalance_due({"a": 0.5, "b": 0.5}, {"a": 0.52, "b": 0.48}, last=date(2026, 11, 2),
                              as_of=date(2026, 11, 20), rule="threshold")
    assert not due  # 2 points and 4% of the target: within both bands
    due, why = pf.rebalance_due({"a": 0.5, "b": 0.5}, {"a": 0.5, "b": 0.5}, last=date(2026, 11, 2),
                                as_of=date(2026, 12, 1), rule="calendar")
    assert due and "2026-12-01" in why[0]
    breach = pd.DataFrame([{"limit_id": "E09", "scope_type": "fund", "scope": "fund", "status": "continuing"}])
    due, why = pf.rebalance_due(cur, tgt, last=None, as_of=date(2026, 11, 20), rule="both", breaches=breach)
    assert not due and "hard breach open" in why[-1]
    cost = pf.rebalance_cost(cur, tgt, {"a": 1000.0, "b": 2000.0})
    assert cost["a"] == pytest.approx(0.06 / 0.5 * 1000) and cost["total"] == pytest.approx(120 + 240)


def test_ois_hedge_notional_matches_asset_swaps_for_one_bond():
    from cr_strat.asset_swaps import hedge_notional
    from cr_strat.bonds import FixedRateBond
    from cr_strat.calendars import mumbai_settlement_calendar
    from cr_strat.ois import bootstrap_ois
    cal = mumbai_settlement_calendar().lenient()
    curve = bootstrap_ois({"1M": 5.62, "3M": 5.70, "6M": 5.78, "1Y": 5.88, "2Y": 5.98, "3Y": 6.06, "5Y": 6.18},
                          date(2026, 11, 19), 5.60, calendar=cal)
    bond = FixedRateBond(6.36, date(2031, 6, 23))
    s = date(2026, 11, 20)
    clean = 99.0
    face = 1e9
    dv01 = face / 100 * bond.analytics(s, clean_price=clean).dv01
    assert pf.hedge_ois_notional(dv01, curve, 48, s, calendar=cal) == pytest.approx(
        hedge_notional(bond, s, clean, curve, 48, face=face, calendar=cal), rel=1e-12)


def test_strategy_returns_from_the_engines_nav_rows():
    nav = pd.DataFrame([
        {"date": "2026-11-13", "book": "b1", "nav": 101.0, "flow": 100.0},
        {"date": "2026-11-13", "book": "b2", "nav": 50.0, "flow": 50.0},
        {"date": "2026-11-16", "book": "b1", "nav": 103.02, "flow": 0.0},
        {"date": "2026-11-16", "book": "b2", "nav": 49.0, "flow": 0.0},
        {"date": "2026-11-16", "book": "fund", "nav": 152.02, "flow": 0.0}])
    r = pf.strategy_returns(nav)
    assert r.loc[date(2026, 11, 13), "b1"] == pytest.approx(0.01) and r.loc[date(2026, 11, 16), "b1"] == \
        pytest.approx(0.02)
    books = pd.DataFrame([{"book": "b1", "strategy": "S1"}, {"book": "b2", "strategy": "S1"}])
    s = pf.strategy_returns(nav, books)
    assert list(s.columns) == ["S1"] and s.loc[date(2026, 11, 16), "S1"] == pytest.approx((152.02 - 151) / 151)
