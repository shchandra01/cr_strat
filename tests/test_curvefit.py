"""Tests of curvefit: synthetic bonds priced off known curves, typed-in published vectors, the CLI."""

import json
import math
import time
from dataclasses import replace
from datetime import date, timedelta
from itertools import pairwise

import numpy as np
import pandas as pd
import pytest

from cr_strat import curvefit as cf
from cr_strat.bonds import DiscountInstrument, FixedRateBond
from cr_strat.calendars import CalendarCoverageError, mumbai_settlement_calendar
from cr_strat.cli import main
from cr_strat.curves import BenchmarkCurve
from cr_strat.sovereign import MASTER_COLUMNS

S = date(2026, 11, 16)
UPWARD = (7.6, -2.0, -1.0, 2.0, 1.5, 8.0)
INVERTED = (6.0, 1.5, 0.5, -1.0, 1.0, 6.0)
HUMPED = (7.0, -1.0, 4.0, -2.0, 2.0, 10.0)
FAST = cf.FitSettings(leave_one_out=False)


def price_off(params, coupon, maturity, s=S):
    """Clean price of a G-sec whose scheduled flows are discounted on the NSS curve (Act/365F times)."""
    bond = FixedRateBond(coupon, maturity)
    flows = bond.cash_flows(s)
    t = np.array([(f.date - s).days / 365 for f in flows])
    a = np.array([f.amount for f in flows])
    return float(a @ np.exp(-cf.nss_zero(t, params) * t / 100)) - bond.accrued_interest(s)


def gsec_frame(params=UPWARD, s=S, seed=20261119):
    """Spec vector A2: 27 maturities 15 Nov 2027 to 15 Nov 2066, plus 20 Apr 2061 and 18 May 2066."""
    rng = np.random.default_rng(seed)
    maturities = [date(2027 + k // 2, 5 if k % 2 else 11, 15) for k in range(0, 80, 3)]
    maturities += [date(2061, 4, 20), date(2066, 5, 18)]
    rows = []
    for k, m in enumerate(maturities):
        coupon = round(float(rng.uniform(5.5, 8.0)), 2)
        rows.append({"isin": f"IN00{k:08d}", "kind": "gsec", "coupon_pct": coupon, "maturity_date": m,
                     "clean_price": price_off(params, coupon, m, s), "traded": True})
    return pd.DataFrame(rows)


def bill_rows(params=UPWARD, s=S, days=(35, 91, 182, 273, 364)):
    rows = []
    for d in days:
        t = d / 365
        rows.append({"isin": f"TB{d:03d}", "kind": "tbill", "maturity_date": s + timedelta(days=d),
                     "clean_price": 100 * math.exp(-float(cf.nss_zero(t, params)) * t / 100), "traded": True})
    return pd.DataFrame(rows)


def with_yield_shift(frame, isin, bp, s=S):
    """A copy of ``frame`` with one bond's price moved to its yield plus ``bp``."""
    out = frame.copy()
    k = int(np.flatnonzero(out["isin"].to_numpy() == isin)[0])
    bond = FixedRateBond(out.loc[k, "coupon_pct"], out.loc[k, "maturity_date"])
    y = bond.yield_from_price(out.loc[k, "clean_price"], s)
    out.loc[k, "clean_price"] = bond.clean_price(y + bp / 100, s)
    return out


@pytest.fixture(scope="module")
def a2():
    return cf.fit_curve(gsec_frame(), S)


# ---------------------------------------------------------------- formulas


def test_nss_formula_reproduces_ecb_svensson_spot_rates():
    # ECB euro area AAA curve, 3 June 2024: data-api.ecb.europa.eu, series YC.B.U2.EUR.4F.G_N_A.SV_C_YM.*
    # (BETA0..BETA3, TAU1, TAU2 and SR_1Y, SR_5Y, SR_10Y, SR_30Y), percent, continuous compounding.
    params = (0.7279744395, 3.0505457849, -3.238264559, 7.1305997808, 3.4525375206, 10.9280257377)
    spot = cf.nss_zero([1, 5, 10, 30], params)
    assert spot == pytest.approx([3.2959554473, 2.5993371395, 2.6614829951, 2.6795022684], abs=1e-8)


def test_nss_limits_and_forward():
    p = UPWARD
    assert cf.nss_loadings(0.0, 1.5, 8.0).tolist() == [1.0, 1.0, 0.0, 0.0]
    assert float(cf.nss_zero(0.0, p)) == pytest.approx(p[0] + p[1])
    assert float(cf.nss_forward(0.0, p)) == pytest.approx(p[0] + p[1])
    assert float(cf.nss_zero(1e5, p)) == pytest.approx(p[0], abs=1e-3)
    # the forward is the derivative of z t
    t, h = 7.3, 1e-6
    def zt(x):
        return float(cf.nss_zero(x, p)) * x

    assert (zt(t + h) - zt(t - h)) / (2 * h) == pytest.approx(float(cf.nss_forward(t, p)), abs=1e-7)
    with pytest.raises(ValueError, match="six parameters"):
        cf.nss_zero(1.0, p[:5])
    with pytest.raises(ValueError, match="above zero"):
        cf.nss_zero(1.0, (*p[:4], 0.0, 8.0))
    with pytest.raises(ValueError, match="zero or more years"):
        cf.nss_zero(-1.0, p)


def test_bspline_basis_sums_to_one_and_integrates_exactly():
    knots = np.r_[np.zeros(4), 40 * (np.arange(1, 13) / 13) ** 2, np.full(4, 40.0)]
    t = np.array([0.0, 0.003, 0.24, 1.0, 3.3, 10.0, 23.0, 39.99, 40.0])
    assert cf.bspline_basis(t, knots).sum(axis=1) == pytest.approx(np.ones(t.size), abs=1e-14)
    integral = cf.bspline_integral_basis(t, knots)
    x, w = np.polynomial.legendre.leggauss(10)  # exact for the cubic pieces between knots
    edges = np.unique(knots)
    for row, upper in enumerate(t):
        want = np.zeros(knots.size - 4)
        for a, b in pairwise(edges):
            hi = min(upper, b)
            if hi > a:
                want += ((hi - a) / 2 * w) @ cf.bspline_basis((a + hi) / 2 + (hi - a) / 2 * x, knots)
        assert integral[row] == pytest.approx(want, abs=1e-13)


@pytest.mark.parametrize("method", ["nss", "pspline"])
def test_discount_zero_forward_and_par_identities(a2, method):
    # Spec vector A6, on both curve families fitted to the A2 bonds.
    curve = a2.curve if method == "nss" else cf.fit_curve(gsec_frame(), S, replace(FAST, method="pspline")).curve
    t = np.array([0.3, 1.0, 2.7, 7.0, 15.5, 33.0, 39.5, 45.0])
    z = curve.zero_rate(t)
    assert curve.discount_t(t) == pytest.approx(np.exp(-z * t / 100), rel=1e-14)
    for upper in t:  # z(t) t is the integral of the forward curve (Simpson)
        x = np.linspace(0.0, upper, 4001)
        f = curve.forward_rate(x)
        integral = (x[1] - x[0]) / 3 * (f[0] + f[-1] + 4 * f[1:-1:2].sum() + 2 * f[2:-1:2].sum())
        assert integral / upper == pytest.approx(float(curve.zero_rate(upper)), abs=1e-8)  # 1e-6 bp
    h = 1e-5
    slope = -(np.log(curve.discount_t(t + h)) - np.log(curve.discount_t(t - h))) / (2 * h) * 100
    assert slope == pytest.approx(curve.forward_rate(t), abs=1e-6)  # 1e-4 bp
    for tenor in (0.5, 1.0, 5.0, 10.0, 30.0):
        y = float(curve.par_yield(tenor))
        k = np.arange(1, int(2 * tenor) + 1) / 2
        par_bond = (y / 2) * curve.discount_t(k).sum() + 100 * float(curve.discount_t(tenor))
        assert par_bond == pytest.approx(100, abs=1e-10)
    # zero-rate bases are the same discount factor
    df = curve.discount_t(t)
    assert (1 + curve.zero_rate(t, "semiannual") / 200) ** (-2 * t) == pytest.approx(df, rel=1e-13)
    assert (1 + curve.zero_rate(t, "annual") / 100) ** (-t) == pytest.approx(df, rel=1e-13)
    with pytest.raises(ValueError, match="basis must be one of"):
        curve.zero_rate(t, "quarterly")


def test_short_par_yield_is_the_simple_rate_and_dates_discount_on_act365():
    flat = cf.NssCurve(params=(6.0, 0, 0, 0, 1.0, 2.0), valuation_date=S, t_min=0.5, t_max=10, method="flat")
    # a 3-month par bond pays its share of the coupon once: the simple rate on the year fraction
    assert float(flat.par_yield(0.25)) == pytest.approx((math.exp(0.06 * 0.25) - 1) / 0.25 * 100, abs=1e-12)
    assert float(flat.par_yield(1.0)) == pytest.approx(2 * (math.exp(0.03) - 1) * 100, abs=1e-12)
    assert float(flat.discount(date(2027, 11, 16))) == pytest.approx(math.exp(-0.06), rel=1e-15)
    assert flat.discount(["2026-11-16", "2027-05-17"]).tolist() == pytest.approx([1.0, math.exp(-0.06 * 182 / 365)])
    with pytest.raises(ValueError, match="before the curve's valuation date"):
        flat.discount("2026-11-13")
    with pytest.raises(ValueError, match="par yields need maturities above zero"):
        flat.par_yield(0.0)


def test_bond_par_yield_and_pricing_through_bonds(a2):
    curve = a2.curve
    bond = FixedRateBond(7.0, date(2036, 11, 16))  # settlement on a coupon date
    par = curve.bond_par_yield(bond)
    dirty, clean = curve.bond_price(replace(bond, coupon_pct=par))
    assert clean == pytest.approx(100.0, abs=1e-10) and dirty == pytest.approx(100.0, abs=1e-10)
    # between coupon dates the yield of the par-coupon bond comes from bonds' formula
    odd = FixedRateBond(7.0, date(2036, 5, 11))
    y = curve.bond_par_yield(odd)
    coupon = (100 - curve.bond_price(replace(odd, coupon_pct=0.0))[0]) / (
        curve.bond_price(replace(odd, coupon_pct=1.0))[0] - curve.bond_price(replace(odd, coupon_pct=0.0))[0]
        - replace(odd, coupon_pct=1.0).accrued_interest(S))
    assert y == pytest.approx(replace(odd, coupon_pct=coupon).yield_from_price(100.0, S), abs=1e-12)
    bill = DiscountInstrument(date(2027, 5, 13))
    assert curve.bond_par_yield(bill) == pytest.approx(curve.bond_yield(bill), abs=1e-14)
    with pytest.raises(ValueError, match="the curve is for settlement on 2026-11-16"):
        curve.bond_price(bond, "2026-11-17")


# ---------------------------------------------------------------- fitting


def test_exact_recovery_of_the_spec_curve(a2):
    # Spec vector A2: parameters to 1e-6, residuals below 1e-6 bp, zero rates 0.5-40y within 1e-6 bp.
    d = a2.diagnostics
    assert np.array(a2.curve.params) == pytest.approx(UPWARD, abs=1e-6)
    assert d["max_abs_bp"] < 1e-6 and d["n_fit"] == 29 and d["converged"]
    assert 1 <= d["n_at_best"] <= d["n_starts"] == 22
    grid = np.arange(0.5, 40.01, 0.5)
    assert np.abs(a2.curve.zero_rate(grid) - cf.nss_zero(grid, UPWARD)).max() * 100 < 1e-6
    assert (a2.residuals["resid_yield_bp"].abs() < 1e-6).all()
    assert d["leave_one_out"]["max_abs_bp"] < 1e-6 and d["warnings"] == []
    assert list(a2.residuals.columns) == [*cf.RESIDUAL_COLUMNS, *cf.RESIDUAL_EXTRA_COLUMNS]


@pytest.mark.parametrize("params", [INVERTED, HUMPED], ids=["inverted", "humped"])
def test_exact_recovery_of_inverted_and_humped_curves(params):
    frame = pd.concat([gsec_frame(params), bill_rows(params)], ignore_index=True)
    res = cf.fit_curve(frame, S, FAST)
    assert np.array(res.curve.params) == pytest.approx(params, abs=1e-6)
    assert res.diagnostics["max_abs_bp"] < 1e-6 and res.diagnostics["n_fit"] == 34


@pytest.mark.parametrize("objective", ["price", "price_over_duration"])
def test_price_objectives_recover_the_curve(objective):
    frame = pd.concat([gsec_frame(), bill_rows()], ignore_index=True)
    res = cf.fit_curve(frame, S, replace(FAST, objective=objective))
    assert np.array(res.curve.params) == pytest.approx(UPWARD, abs=1e-6)
    assert res.diagnostics["objective_kind"] == objective


def test_spline_cross_check_recovers_the_nss_curve():
    # Spec vector A3: 12 interior knots, zero rates within 0.1 bp of the true NSS for lam in {0.01, 1, 100}.
    frame = gsec_frame()
    grid = np.array([1, 2, 5, 10, 20, 30, 39.0])
    for lam in (0.01, 1.0, 100.0):
        res = cf.fit_curve(frame, S, replace(FAST, method="pspline", lam=lam))
        assert np.abs(res.curve.zero_rate(grid) - cf.nss_zero(grid, UPWARD)).max() * 100 < 0.1
        assert res.diagnostics["lam"] == lam and len(res.curve.knots) == 12 + 8


def test_noise_robustness():
    rng = np.random.default_rng(7)
    frame = gsec_frame()
    for k in range(len(frame)):
        frame = with_yield_shift(frame, frame.loc[k, "isin"], rng.normal(0, 2.0))
    grid = np.array([1, 2, 5, 10, 20, 30, 39.0])
    nss = cf.fit_curve(frame, S)
    spline = cf.fit_curve(frame, S, cf.FitSettings(method="pspline"))
    for res in (nss, spline):
        d = res.diagnostics
        assert 1.0 < d["rmse_bp"] < 2.5  # 2 bp of noise, less what the curve absorbs
        assert np.abs(res.curve.zero_rate(grid) - cf.nss_zero(grid, UPWARD)).max() * 100 < 6
        assert d["leave_one_out"]["rmse_bp"] > d["rmse_bp"]  # out of sample is worse than in sample
    lams = [lam for lam, _ in spline.diagnostics["lam_scores"]]
    assert lams[0] < spline.diagnostics["lam"] < lams[-1]  # the criterion found an interior penalty


def test_spline_short_end_stays_sane_without_short_bonds():
    # No bond under 4 years and 3 bp of noise: the leave-one-out criterion keeps the unpenalised short end in check
    # (GCV, an option, picked lam = 100 here and the spline's z(0+) went to 20%).
    rng = np.random.default_rng(1)
    frame = gsec_frame().iloc[2:].reset_index(drop=True)
    for k in range(len(frame)):
        frame = with_yield_shift(frame, frame.loc[k, "isin"], rng.normal(0, 3.0))
    spline = cf.fit_curve(frame, S, cf.FitSettings(method="pspline"))
    nss = cf.fit_curve(frame, S, FAST)
    assert spline.diagnostics["lam_criterion"] == "cv" and spline.diagnostics["lam"] > 100
    assert abs(float(spline.curve.zero_rate(0.0)) - (UPWARD[0] + UPWARD[1])) < 1.0
    assert spline.diagnostics["leave_one_out"]["rmse_bp"] < 5.0
    comparison = cf.compare_curves(nss.curve, spline.curve)
    assert comparison.loc[comparison["in_joint_range"], "diff_bp"].abs().max() < 3.0
    gcv = cf.fit_curve(frame, S, cf.FitSettings(method="pspline", lam_criterion="gcv", leave_one_out=False))
    assert gcv.diagnostics["lam_criterion"] == "gcv" and len(gcv.diagnostics["lam_scores"]) == 7


def test_outliers_squared_huber_and_rejection():
    frame = with_yield_shift(gsec_frame(), "IN0000000010", 30.0)
    plain = cf.fit_curve(frame, S, FAST)
    assert any(w.startswith("suspect_residuals") and "IN0000000010" in w for w in plain.diagnostics["warnings"])
    others = plain.residuals[plain.residuals["isin"] != "IN0000000010"]
    assert others["resid_yield_bp"].abs().max() > 1  # one bad print moves its neighbours' residuals

    huber = cf.fit_curve(frame, S, replace(FAST, loss="huber"))
    row = huber.residuals.set_index("isin").loc["IN0000000010"]
    assert row["robust_weight"] < 0.5 and row["in_fit"]
    assert huber.residuals.loc[huber.residuals["isin"] != "IN0000000010", "resid_yield_bp"].abs().max() < 1
    assert huber.diagnostics["huber_downweighted"] == ["IN0000000010"]

    rejected = cf.fit_curve(frame, S, replace(FAST, outlier_bp=10.0))
    row = rejected.residuals.set_index("isin").loc["IN0000000010"]
    assert row["excluded_reason"] == "outlier" and not row["in_fit"] and row["weight"] == 0
    assert row["resid_yield_bp"] == pytest.approx(30.0, abs=1e-6)  # measured out of sample
    assert rejected.residuals.loc[rejected.residuals["in_fit"], "resid_yield_bp"].abs().max() < 1e-6
    out = rejected.diagnostics["outliers"]
    assert [o["isin"] for o in out] == ["IN0000000010"] and out[0]["round"] == 1
    assert rejected.diagnostics["n_fit"] == 28 and rejected.diagnostics["n_outliers"] == 1


def test_outlier_rule_stops_when_clean_and_never_goes_below_the_minimum():
    frame = with_yield_shift(gsec_frame().iloc[::3].reset_index(drop=True), "IN0000000009", 40.0)
    assert len(frame) == 10
    res = cf.fit_curve(frame, S, replace(FAST, outlier_bp=1.0, outlier_rounds=5))
    assert res.diagnostics["n_fit"] == 9 and res.diagnostics["warnings"] == []  # the rest fit exactly
    rng = np.random.default_rng(3)
    for k in range(len(frame)):  # every bond off the curve by a few bp: the rule would take them all
        frame = with_yield_shift(frame, frame.loc[k, "isin"], rng.choice([-1, 1]) * rng.uniform(2, 4))
    res = cf.fit_curve(frame, S, replace(FAST, outlier_bp=0.1, outlier_mads=0.1, outlier_rounds=5))
    assert res.diagnostics["n_fit"] == cf.MIN_BONDS["nss"] and res.diagnostics["n_outliers"] == 2
    assert any(w.startswith("outlier_rule_stopped") for w in res.diagnostics["warnings"])


def test_single_bond_needs_the_flat_method():
    frame = gsec_frame().iloc[[6]]
    with pytest.raises(ValueError, match=r"1 bond\(s\) in the fit \(IN0000000006\); method 'nss' needs at least 8"):
        cf.fit_curve(frame, S)
    with pytest.raises(ValueError, match="method 'pspline' needs at least 6"):
        cf.fit_curve(frame, S, cf.FitSettings(method="pspline"))
    res = cf.fit_curve(frame, S, cf.FitSettings(method="flat"))
    assert abs(res.residuals["resid_yield_bp"].iloc[0]) < 1e-8
    assert res.curve.method == "flat" and set(res.diagnostics["params"]) == {"zero_cc_pct"}
    assert any(w.startswith("leave_one_out_skipped") for w in res.diagnostics["warnings"])
    # one bond measured against an existing curve
    table = cf.residual_table(res.curve, frame)
    assert len(table) == 1 and not table["in_fit"].iloc[0]


def test_too_few_bonds_or_maturities_are_refused():
    frame = gsec_frame()
    with pytest.raises(ValueError, match=r"7 bond\(s\) in the fit .*needs at least 8"):
        cf.fit_curve(frame.iloc[:7], S)
    same = pd.concat([frame.iloc[:3]] * 3, ignore_index=True)
    same["isin"] = [f"X{k}" for k in range(len(same))]
    same["coupon_pct"] = np.linspace(6, 8, len(same))
    with pytest.raises(ValueError, match="3 distinct maturity date"):
        cf.fit_curve(same, S)
    # rows filtered out of the fit do not count
    frame = frame.assign(traded=[k >= 25 for k in range(len(frame))])
    with pytest.raises(ValueError, match=r"4 bond\(s\) in the fit"):
        cf.fit_curve(frame, S)


@pytest.mark.parametrize("change, message", [
    (lambda f: pd.concat([f, f.iloc[[0]]]), "duplicate ISINs in the bond frame: \\['IN0000000000'\\]"),
    (lambda f: f.assign(kind=["bond"] + ["gsec"] * (len(f) - 1)), "ISIN IN0000000000: kind 'bond' is not one of"),
    (lambda f: f.assign(maturity_date=[None] + list(f["maturity_date"][1:])),
     "ISIN IN0000000000: maturity_date is missing"),
    (lambda f: f.assign(maturity_date=["15-11-2027"] + list(f["maturity_date"][1:])),
     "maturity_date '15-11-2027' is not a date"),
    (lambda f: f.assign(coupon_pct=[None] + list(f["coupon_pct"][1:])), "coupon_pct is missing for a gsec"),
    (lambda f: f.assign(clean_price=[None] + list(f["clean_price"][1:])),
     "ISIN IN0000000000 is in the fit but has no usable market price \\(no price or yield\\)"),
    (lambda f: f.assign(clean_price=[-5.0] + list(f["clean_price"][1:])), "clean price must be a positive number"),
    (lambda f: f.assign(clean_price=["abc"] + list(f["clean_price"][1:])), "clean_price 'abc' is not a number"),
    (lambda f: f.assign(weight=[-1.0] + [1.0] * (len(f) - 1)), "weight must be zero or more"),
    (lambda f: f.assign(settlement_date="2026-11-17"), "settlement_date 2026-11-17 differs from the fit's settlement"),
    (lambda f: f.drop(columns=["clean_price"]), "needs a clean_price or a yield_pct column"),
    (lambda f: f.drop(columns=["kind"]), "missing columns \\['kind'\\]"),
    (lambda f: f.iloc[0:0], "the bond frame has no rows"),
    (lambda f: f.assign(traded=["maybe"] * len(f)), "traded 'maybe' is not true or false"),
])
def test_degenerate_inputs_are_refused_with_the_reason(change, message):
    with pytest.raises((ValueError, TypeError), match=message):
        cf.fit_curve(change(gsec_frame()), S, FAST)


def test_universe_rules_and_reasons():
    extra = pd.DataFrame([
        {"isin": "SDL1", "kind": "sdl", "coupon_pct": 7.5, "maturity_date": date(2036, 11, 16), "clean_price": 99.0},
        {"isin": "FRB1", "kind": "frb", "coupon_pct": 6.0, "maturity_date": date(2033, 11, 16), "clean_price": 100.0},
        {"isin": "WI1", "kind": "gsec", "coupon_pct": 7.0, "maturity_date": date(2041, 11, 16), "clean_price": 99.0,
         "flags": "when_issued"},
        {"isin": "OLD1", "kind": "gsec", "coupon_pct": 7.0, "maturity_date": date(2026, 11, 1), "clean_price": 99.0},
        {"isin": "SHORT1", "kind": "gsec", "coupon_pct": 7.0, "maturity_date": date(2027, 1, 16), "clean_price": 100.0},
        {"isin": "TB5", "kind": "tbill", "maturity_date": date(2026, 11, 21), "yield_pct": 5.5},
        {"isin": "TB30", "kind": "tbill", "maturity_date": date(2026, 12, 16), "yield_pct": 5.5, "volume_cr": 2.0},
        {"isin": "BM1", "kind": "gsec", "coupon_pct": 6.9, "maturity_date": date(2036, 5, 16), "clean_price": 98.0,
         "flags": "benchmark;far"},
        {"isin": "NT1", "kind": "gsec", "coupon_pct": 7.2, "maturity_date": date(2031, 11, 16), "traded": "no"},
        {"isin": "W0", "kind": "gsec", "coupon_pct": 7.2, "maturity_date": date(2032, 11, 16), "clean_price": 99.0,
         "weight": 0},
    ])
    frame = pd.concat([gsec_frame(), extra], ignore_index=True)
    reasons = cf.select_universe(frame, cf.FitSettings(drop_benchmarks=True), S).set_index("isin")["excluded_reason"]
    want = {"SDL1": "kind:sdl", "FRB1": "no_pricer", "WI1": "flag:when_issued", "OLD1": "matured", "SHORT1": "short",
            "TB5": "short", "TB30": "volume", "BM1": "benchmark", "NT1": "not_traded", "W0": "zero_weight"}
    assert {k: reasons[k] for k in want} == want
    assert (reasons[[i for i in reasons.index if i.startswith("IN")]] == "").all()
    res = cf.fit_curve(frame, S, replace(FAST, drop_benchmarks=True))
    table = res.residuals.set_index("isin")
    assert table.loc["SDL1", "resid_yield_bp"] == pytest.approx(
        100 * (FixedRateBond(7.5, date(2036, 11, 16), cf.SDL).yield_from_price(99.0, S)
               - res.curve.bond_yield(FixedRateBond(7.5, date(2036, 11, 16), cf.SDL))), abs=1e-9)
    assert math.isnan(table.loc["FRB1", "resid_yield_bp"]) and table.loc["FRB1", "note"] == "no pricer for kind frb"
    assert math.isnan(table.loc["NT1", "market_yield_pct"]) and math.isfinite(table.loc["NT1", "model_yield_pct"])
    assert table.loc["OLD1", "note"].startswith("matured on")
    assert table.loc["BM1", "flags"] == "benchmark;far" and math.isfinite(table.loc["BM1", "resid_yield_bp"])
    # bills are measured in days, bonds in years; a volume filter skips rows without a volume
    reasons = cf.select_universe(frame, cf.FitSettings(min_days_bills=3, min_volume_cr=None), S)
    assert reasons.set_index("isin").loc[["TB5", "TB30"], "in_fit"].tolist() == [True, True]


def test_determinism_and_row_order(a2):
    again = cf.fit_curve(gsec_frame(), S)
    shuffled = cf.fit_curve(gsec_frame().sample(frac=1, random_state=11).reset_index(drop=True), S)
    text = a2.residuals.to_csv(index=False)
    assert again.residuals.to_csv(index=False) == text == shuffled.residuals.to_csv(index=False)
    assert json.dumps(a2.diagnostics) == json.dumps(again.diagnostics) == json.dumps(shuffled.diagnostics)
    assert a2.curve.curve_id == shuffled.curve.curve_id and len(a2.curve.curve_id) == 16
    assert cf.curve_points(a2.curve).to_csv(index=False) == cf.curve_points(shuffled.curve).to_csv(index=False)
    assert cf.fit_curve(gsec_frame(), S, replace(FAST, objective="price")).curve.curve_id != a2.curve.curve_id


def test_residuals_ignore_volume_scale_and_out_of_fit_rows():
    # Spec vector A7.
    frame = with_yield_shift(gsec_frame(), "IN0000000004", 3.0)
    frame["volume_cr"] = np.linspace(10.0, 900.0, len(frame))
    liquid = replace(FAST, weights="liquidity")
    base = cf.fit_curve(frame, S, liquid)
    scaled = cf.fit_curve(frame.assign(volume_cr=frame["volume_cr"] * 7), S, liquid)
    # scaled volumes change the weights in the last bit only: the same optimum to the solver's tolerance
    assert np.abs(base.residuals["resid_yield_bp"] - scaled.residuals["resid_yield_bp"]).max() < 1e-6
    extra = pd.DataFrame([
        {"isin": "SDL9", "kind": "sdl", "coupon_pct": 7.5, "maturity_date": date(2036, 11, 16), "clean_price": 99.0},
        {"isin": "WI9", "kind": "gsec", "coupon_pct": 7.0, "maturity_date": date(2041, 11, 16), "clean_price": 90.0,
         "flags": "when_issued"},
        {"isin": "FRB9", "kind": "frb", "coupon_pct": 6.0, "maturity_date": date(2033, 11, 16), "clean_price": 100.0}])
    more = cf.fit_curve(pd.concat([frame, extra], ignore_index=True), S, liquid)
    fit = more.residuals[more.residuals["in_fit"]].reset_index(drop=True)
    assert fit["resid_yield_bp"].tolist() == base.residuals["resid_yield_bp"].tolist()
    assert more.curve.curve_id == base.curve.curve_id


def test_self_pricing_and_a_cheap_bond(a2):
    # Spec vector A5.
    curve = a2.curve
    bond = FixedRateBond(7.1, date(2036, 5, 11))
    model_yield = curve.bond_yield(bond)
    rows = pd.DataFrame([
        {"isin": "SELF", "kind": "gsec", "coupon_pct": 7.1, "maturity_date": "2036-05-11",
         "clean_price": curve.bond_price(bond)[1]},
        {"isin": "CHEAP", "kind": "gsec", "coupon_pct": 7.1, "maturity_date": "2036-05-11",
         "clean_price": bond.clean_price(model_yield + 0.05, S)}])
    table = cf.residual_table(curve, rows).set_index("isin")
    assert abs(table.loc["SELF", "resid_yield_bp"]) < 1e-8 and abs(table.loc["SELF", "resid_price"]) < 1e-8
    assert table.loc["CHEAP", "resid_yield_bp"] == pytest.approx(5.0, abs=1e-6)
    assert table.loc["CHEAP", "resid_price"] < 0  # cheap: market price below model


def test_tbill_is_one_flow_on_its_own_conventions():
    # Spec vector A9: the 182-day bill of the 11 Nov 2026 auction (cut-off 6.5183%), on a flat 6.5% continuous curve.
    s = date(2026, 11, 12)
    flat = cf.NssCurve(params=(6.5, 0, 0, 0, 1.0, 2.0), valuation_date=s, t_min=0.1, t_max=1, method="flat")
    bill = pd.DataFrame([{"isin": "TB182", "kind": "tbill", "maturity_date": "2027-05-13", "yield_pct": 6.5183}])
    row = cf.residual_table(flat, bill).iloc[0]
    assert row["model_dirty"] == pytest.approx(100 * math.exp(-0.065 * 182 / 365), abs=1e-9)
    assert row["model_dirty"] == pytest.approx(96.810865, abs=1e-6)
    assert row["model_yield_pct"] == pytest.approx(6.606483, abs=1e-6)
    assert row["resid_yield_bp"] == pytest.approx(-8.82, abs=5e-3)  # market yield below model: rich
    assert row["market_dirty"] == pytest.approx(100 / (1 + 0.065183 * 182 / 365), abs=1e-12)  # unrounded
    assert row["quote"] == "yield" and row["dv01"] > 0


def test_anchor_pins_the_short_end_and_warnings_name_what_is_extrapolated():
    long_only = gsec_frame().iloc[2:].reset_index(drop=True)  # shortest bond 15 Nov 2030, 4 years out
    plain = cf.fit_curve(long_only, S, FAST)
    assert any(w.startswith("short_end_extrapolated") for w in plain.diagnostics["warnings"])
    pinned = cf.fit_curve(long_only, S, replace(FAST, anchor_pct=5.0, anchor_weight=100.0))
    assert float(pinned.curve.zero_rate(0.0)) == pytest.approx(5.0, abs=0.01)
    assert pinned.diagnostics["anchor"]["model_pct"] == pytest.approx(5.0, abs=0.01)
    assert not any(w.startswith("short_end_extrapolated") for w in pinned.diagnostics["warnings"])
    # data from a curve starting below zero leave beta0 + beta1 on its bound, and say so
    negative = cf.fit_curve(gsec_frame((7.0, -8.0, 0.0, 0.0, 1.0, 5.0)), S, FAST)
    assert "params_at_bound: beta0+beta1 at its lower bound 0" in negative.diagnostics["warnings"]
    assert float(negative.curve.zero_rate(0.0)) == pytest.approx(0.0, abs=1e-12)


def test_warm_start_and_quote_mismatch():
    frame = gsec_frame()
    res = cf.fit_curve(frame, S, FAST, warm_start=UPWARD)
    assert res.diagnostics["n_starts"] == 23
    bad = frame.assign(yield_pct=50.0)  # a yield column that disagrees with every price
    res = cf.fit_curve(bad, S, FAST)
    assert res.residuals["quote"].eq("price").all()
    assert any(w.startswith("quote_mismatch") for w in res.diagnostics["warnings"])
    assert res.residuals["note"].str.startswith("quote_mismatch").all()


def test_leave_one_out_finds_the_influential_bond():
    frame = with_yield_shift(gsec_frame(), "IN0000000012", 15.0)
    res = cf.fit_curve(frame, S)
    table = res.residuals.set_index("isin")
    assert abs(table.loc["IN0000000012", "loo_resid_yield_bp"]) > abs(table.loc["IN0000000012", "resid_yield_bp"])
    assert res.diagnostics["leave_one_out"]["max_shift_isin"] == "IN0000000012"
    assert res.diagnostics["leave_one_out"]["rmse_bp"] > res.diagnostics["rmse_bp"]


def test_leave_one_out_shift_is_measured_where_the_refit_has_data():
    # The shortest bond 20 bp off an exact curve: the refit without it recovers the true curve, so its shift is
    # the gap between the true and the fitted par curves over the remaining bonds' range (from the second bond).
    frame = with_yield_shift(gsec_frame(), "IN0000000000", 20.0)
    res = cf.fit_curve(frame, S)
    years = res.residuals["years_to_maturity"].to_numpy(dtype=float)
    lo, hi = years[1], years[-1]
    grid = np.unique(np.r_[lo, np.arange(math.ceil(lo * 4) / 4, hi, 0.25), hi])
    truth = cf.NssCurve(params=UPWARD, valuation_date=S, t_min=lo, t_max=hi, method="nss")
    want = np.max(np.abs(truth.par_yield(grid) - res.curve.par_yield(grid))) * 100
    row = res.residuals.set_index("isin").loc["IN0000000000"]
    assert row["loo_shift_bp"] == pytest.approx(want, abs=1e-6) and want > 0.1
    assert row["loo_resid_yield_bp"] == pytest.approx(20.0, abs=1e-6)  # out of sample: the full 20 bp


def test_flow_dates_on_payment_dates_need_a_covering_calendar():
    frame = gsec_frame().iloc[:10]
    cal = mumbai_settlement_calendar()
    with pytest.raises(CalendarCoverageError, match="2027"):
        cf.fit_curve(frame, S, replace(FAST, flow_dates="payment"), calendar=cal)
    with pytest.raises(ValueError, match="needs a calendar"):
        cf.fit_curve(frame, S, replace(FAST, flow_dates="payment"))
    res = cf.fit_curve(frame, S, replace(FAST, flow_dates="payment"), calendar=cal.lenient())
    assert res.diagnostics["max_abs_bp"] < 0.5  # holiday-adjusted dates move flows by a few days only


@pytest.mark.parametrize("kwargs, message", [
    ({"method": "cubic"}, "method must be one of"),
    ({"objective": "yields"}, "objective must be one of"),
    ({"include_kinds": ("frb",)}, "'frb' cannot be fitted"),
    ({"include_kinds": ()}, "include_kinds is empty"),
    ({"tau1_grid": (6.0,), "tau2_grid": (2.0,)}, "no pair of tau1_grid and tau2_grid"),
    ({"tau_ratio": 0.5}, "tau_ratio must be at least 1"),
    ({"lambdas": ()}, "lambdas is empty"),
    ({"huber_bp": 0}, "huber_bp must be above zero"),
    ({"outlier_rounds": 1.5}, "outlier_rounds must be an integer"),
    ({"anchor_pct": float("nan")}, "anchor_pct must be a finite rate"),
])
def test_settings_are_checked(kwargs, message):
    with pytest.raises((ValueError, TypeError), match=message):
        cf.FitSettings(**kwargs)


# ---------------------------------------------------------------- rich/cheap


def test_zscores_against_the_previous_window():
    # Spec vector A8: residuals 0, 1, ..., 59 then 100 for one ISIN.
    days = pd.bdate_range("2026-01-01", periods=61).date
    panel = pd.DataFrame({"date": days, "isin": "X", "resid_yield_bp": [*range(60), 100]})
    z = cf.residual_zscores(panel)
    assert z["z_ts"].iloc[-1] == pytest.approx(70.5 / np.std(np.arange(60), ddof=1), abs=1e-12)
    assert z["z_ts"].iloc[-1] == pytest.approx(4.0368, abs=1e-4)
    assert z["z_ts"].iloc[:20].isna().all() and z["z_ts"].iloc[20:].notna().all()
    assert z["n_prior"].iloc[-1] == 60 and z["n_prior"].iloc[0] == 0
    # a missing residual is no observation and gets no score
    gap = panel.copy()
    gap.loc[30, "resid_yield_bp"] = np.nan
    z = cf.residual_zscores(gap)
    assert math.isnan(z["z_ts"].iloc[30]) and z["n_prior"].iloc[31] == 30
    with pytest.raises(ValueError, match="more than one row for ISIN X"):
        cf.residual_zscores(pd.concat([panel, panel.iloc[[0]]]))
    with pytest.raises(ValueError, match="min_obs 30 is above window 20"):
        cf.residual_zscores(panel, window=20, min_obs=30)


def test_cross_sectional_robust_zscores_by_bucket():
    panel = pd.DataFrame({"date": S, "isin": [f"B{k}" for k in range(7)],
                          "resid_yield_bp": [1.0, 2.0, 3.0, 10.0, -1.0, 0.0, 1.0],
                          "years_to_maturity": [2.0, 2.5, 2.9, 2.2, 12.0, 13.0, 14.0]})
    z = cf.residual_zscores(panel).set_index("isin")
    short = np.array([1.0, 2.0, 3.0, 10.0])
    mad = 1.4826 * np.median(np.abs(short - np.median(short)))
    assert z.loc["B3", "z_cs"] == pytest.approx((10.0 - np.median(short)) / mad)
    assert z.loc["B3", "bucket"] == "1-3y" and z.loc["B4", "bucket"] == "10-20y"
    assert z.loc["B5", "z_cs"] == pytest.approx(0.0)


def test_rich_cheap_table_scores_today_against_the_history_only(a2):
    today = a2.residuals.assign(resid_yield_bp=np.linspace(-3, 3, len(a2.residuals)))
    days = pd.bdate_range("2026-08-01", periods=40).date
    history = pd.DataFrame([{"date": d, "isin": i, "resid_yield_bp": 0.5 * ((k + j) % 3 - 1)}
                            for k, d in enumerate(days) for j, i in enumerate(today["isin"])])
    table = cf.rich_cheap_table(today, history)
    assert list(table["isin"]) == list(today["isin"]) and (table["n_prior"] == 40).all()
    first = table.iloc[0]
    past = history[history["isin"] == first["isin"]]["resid_yield_bp"]
    assert first["z_ts"] == pytest.approx((first["resid_yield_bp"] - past.mean()) / past.std(ddof=1))
    with pytest.raises(ValueError, match="on or after 2026-11-16 .*would look ahead"):
        cf.rich_cheap_table(today, pd.concat([history, pd.DataFrame([{"date": S, "isin": "X", "resid_yield_bp": 1}])]))
    assert cf.rich_cheap_table(today)["z_ts"].isna().all()


def test_bond_butterflies_are_dv01_neutral_and_add_up(a2):
    table = with_yield_shift(gsec_frame(), "IN0000000007", 4.0)
    res = cf.fit_curve(table, S, FAST)
    flies = cf.bond_butterflies(res.residuals, [("IN0000000003", "IN0000000007", "IN0000000012"),
                                               ("IN0000000003", "IN0000000007", "MISSING")])
    assert len(flies) == 1
    fly = flies.iloc[0]
    assert fly["resid_fly_bp"] == pytest.approx(fly["yield_fly_bp"] - fly["model_fly_bp"], abs=1e-9)
    assert fly["resid_fly_bp"] > 3  # the body is the cheap bond
    r = res.residuals.set_index("isin")
    dv01 = r["dv01"]
    assert fly["short_face"] * dv01["IN0000000003"] == pytest.approx(fly["body_face"] * dv01["IN0000000007"] / 2)
    assert fly["long_face"] * dv01["IN0000000012"] == pytest.approx(fly["body_face"] * dv01["IN0000000007"] / 2)
    with pytest.raises(ValueError, match="not short < body < long"):
        cf.bond_butterflies(res.residuals, [("IN0000000012", "IN0000000007", "IN0000000003")])
    with pytest.raises(ValueError, match="three different ISINs"):
        cf.bond_butterflies(res.residuals, [("IN0000000003", "IN0000000003", "IN0000000012")])


# ---------------------------------------------------------------- curve outputs


def test_curve_points_benchmark_frame_and_spreads(a2):
    points = cf.curve_points(a2.curve)
    assert list(points.columns) == list(cf.CURVE_POINT_COLUMNS) and len(points) == 200
    assert points["tenor_years"].iloc[[0, -1]].tolist() == [0.25, 50.0]
    inside = points.set_index("tenor_years")["in_data_range"]
    assert a2.curve.t_max == pytest.approx(14609 / 365)  # 15 Nov 2066, 40 years less a day
    assert not inside[0.5] and inside[1.0] and inside[40.0] and not inside[40.25]
    frame = cf.benchmark_frame([a2.curve])
    bench = BenchmarkCurve(frame, compounding="semiannual")
    tenors = [2.0, 10.0, 30.0]
    got = bench.yields([pd.Timestamp(S)] * 3, tenors, to_basis="semiannual")
    assert got == pytest.approx(a2.curve.par_yield(tenors), abs=1e-12)
    with pytest.raises(ValueError, match="two curves for 2026-11-16"):
        cf.benchmark_frame([a2.curve, a2.curve])
    spreads = cf.curve_spreads(a2.curve).set_index(["short_years", "long_years"])
    assert spreads.loc[(2.0, 10.0), "spread_bp"] == pytest.approx(
        100 * float(a2.curve.par_yield(10.0) - a2.curve.par_yield(2.0)))
    assert not spreads.loc[(10.0, 50.0), "in_data_range"]
    flies = cf.curve_butterflies(a2.curve)
    assert flies["fly_bp"].iloc[0] == pytest.approx(
        100 * float(2 * a2.curve.par_yield(5.0) - a2.curve.par_yield(2.0) - a2.curve.par_yield(10.0)))


def test_compare_curves_flags_model_risk_inside_the_joint_range_only(a2):
    spline = cf.fit_curve(gsec_frame(), S, replace(FAST, method="pspline")).curve
    comparison = cf.compare_curves(a2.curve, spline)
    assert not comparison["model_risk"].any()
    assert comparison.loc[comparison["in_joint_range"], "diff_bp"].abs().max() < 0.2
    shifted = cf.NssCurve(params=(a2.curve.params[0] + 0.05, *a2.curve.params[1:]), valuation_date=S,
                          t_min=a2.curve.t_min, t_max=20.0, method="nss")
    comparison = cf.compare_curves(a2.curve, shifted, threshold_bp=3.0)
    assert comparison.loc[comparison["in_joint_range"], "model_risk"].all()
    assert not comparison.loc[~comparison["in_joint_range"], "model_risk"].any()
    flagged = cf.model_risk_flags(a2.residuals, comparison).set_index("isin")
    assert flagged["model_risk"].sum() == (a2.residuals["years_to_maturity"] <= 20.0).sum()
    longest = a2.residuals["isin"].iloc[-1]  # 15 Nov 2066: beyond the shifted curve's data
    assert math.isnan(flagged.loc[longest, "crosscheck_diff_bp"]) and not flagged.loc[longest, "model_risk"]
    assert flagged["crosscheck_diff_bp"].dropna().between(5.0, 5.4).all()  # 5 bp continuous: about 5.2 semi-annual


# ---------------------------------------------------------------- price files and the CLI


def _master(frame):
    rows = []
    for r in frame.itertuples():
        rows.append({c: None for c in MASTER_COLUMNS} | {
            "isin": r.isin, "name": f"{r.coupon_pct:.2f}% GS {r.maturity_date.year}", "kind": "gsec",
            "coupon_pct": r.coupon_pct, "maturity_date": r.maturity_date})
    return pd.DataFrame(rows)


def test_prepare_prices_fills_from_the_master_and_refuses_conflicts(isin):
    frame = gsec_frame()
    frame["isin"] = [isin(f"IN00202{k:04d}") for k in range(len(frame))]
    master = _master(frame)
    prices = frame[["isin", "clean_price"]].rename(columns={"clean_price": "price"}).astype(str)
    prepared = cf.prepare_prices(prices, master)
    assert prepared["kind"].eq("gsec").all() and prepared["coupon_pct"].tolist() == frame["coupon_pct"].tolist()
    res = cf.fit_curve(prepared, S, FAST)
    assert res.diagnostics["max_abs_bp"] < 1e-6
    clash = prices.assign(coupon_pct="9.99")
    with pytest.raises(ValueError, match="coupon_pct is '9.99' in the price file but .* in the security master"):
        cf.prepare_prices(clash, master)
    # a row neither the file nor the master describes stays out of the fit with a reason, the rest is fitted
    assert cf.prepare_prices(prices)["kind"].eq("unknown").all()
    stray = isin("IN999999999")
    partial = cf.prepare_prices(pd.concat([prices, pd.DataFrame([{"isin": stray, "price": "100"}])]), master)
    fitted = cf.fit_curve(partial, S, FAST)
    row = fitted.residuals.set_index("isin").loc[stray]
    assert row["excluded_reason"] == "no_kind" and math.isnan(row["resid_yield_bp"]) and not row["in_fit"]
    assert fitted.diagnostics["n_fit"] == len(prices)
    assert f"no_kind: out of the fit, the master does not say what they are: {stray}" in fitted.diagnostics["warnings"]
    with pytest.raises(ValueError, match="both 'price' and 'clean_price'"):
        cf.prepare_prices(prices.assign(clean_price="1"), master)


def test_cli_curve_fit_end_to_end(tmp_path, capsys, isin):
    frame = gsec_frame()
    frame["isin"] = [isin(f"IN00202{k:04d}") for k in range(len(frame))]
    _master(frame).to_csv(tmp_path / "master.csv", index=False)
    bills = bill_rows().assign(isin=[isin(f"IN00226{k:04d}") for k in range(5)])
    bills["yield_pct"] = [DiscountInstrument(m).yield_from_price(p, S)
                          for m, p in zip(bills["maturity_date"], bills["clean_price"])]
    prices = pd.concat([frame[["isin", "clean_price"]].assign(volume_cr=100, flags=""),
                        bills.drop(columns=["clean_price"]).assign(volume_cr=500)], ignore_index=True)
    prices.loc[4, "flags"] = "benchmark"
    prices.to_csv(tmp_path / "prices.csv", index=False)
    history = pd.DataFrame([{"date": d, "isin": i, "resid_yield_bp": 0.1 * (k % 3)}
                            for k, d in enumerate(pd.bdate_range("2026-09-01", periods=25).date)
                            for i in frame["isin"]])
    history.to_csv(tmp_path / "history.csv", index=False)
    fly = ",".join(frame["isin"].iloc[[3, 7, 12]])
    args = ["curve-fit", "--prices", str(tmp_path / "prices.csv"), "--master", str(tmp_path / "master.csv"),
            "--settlement", "2026-11-16", "--history", str(tmp_path / "history.csv"), "--fly", fly]
    assert main([*args, "--out-dir", str(tmp_path / "out")]) == 0
    printed = capsys.readouterr().out
    assert "nss: 34 of 34 rows in the fit" in printed and "cross-check pspline: 0 of" in printed
    out = tmp_path / "out"
    residuals = pd.read_csv(out / "residuals.csv")
    assert len(residuals) == 34 and residuals["resid_yield_bp"].abs().max() < 1e-6
    assert residuals["model_risk"].eq(False).all()
    curve = pd.read_csv(out / "curve.csv")
    assert list(curve.columns) == list(cf.CURVE_POINT_COLUMNS) and len(curve) == 200
    assert pd.read_csv(out / "benchmark_par.csv")["basis"].eq("semiannual").all()
    assert len(pd.read_csv(out / "butterflies.csv")) == 1
    rich = pd.read_csv(out / "rich_cheap.csv")
    assert rich.loc[rich["kind"] == "gsec", "n_prior"].eq(25).all()
    diagnostics = json.loads((out / "diagnostics.json").read_text())
    assert diagnostics["primary"]["n_fit"] == 34 and diagnostics["check"]["method"] == "pspline"
    for name in ("crosscheck.csv", "curve_check.csv", "curve_spreads.csv"):
        assert (out / name).exists()
    # the same inputs give the same bytes
    assert main([*args, "--out-dir", str(tmp_path / "again")]) == 0
    for path in out.iterdir():
        assert (tmp_path / "again" / path.name).read_bytes() == path.read_bytes()


def test_cli_curve_fit_judges_model_risk_again_without_a_suspect_bond(tmp_path, capsys):
    frame = pd.concat([gsec_frame(), bill_rows()], ignore_index=True)
    longest = frame.loc[frame["maturity_date"].idxmax(), "isin"]
    with_yield_shift(frame, longest, 20.0).to_csv(tmp_path / "prices.csv", index=False)
    assert main(["curve-fit", "--prices", str(tmp_path / "prices.csv"), "--settlement", "2026-11-16",
                 "--out-dir", str(tmp_path / "out")]) == 0
    printed = capsys.readouterr().out
    assert f"suspect_residuals: above 10 bp and 4 robust sd: {longest}" in printed
    assert f"note: model risk judged again without the suspect bond(s) {longest}: 3 flag(s) cleared" in printed
    residuals = pd.read_csv(tmp_path / "out" / "residuals.csv", keep_default_na=False)
    assert residuals["model_risk"].astype(str).eq("False").all()
    assert (residuals["model_risk_note"].str.startswith("cleared")).sum() == 3


def test_cli_curve_fit_reports_bad_input(tmp_path, capsys):
    gsec_frame().iloc[:5].to_csv(tmp_path / "few.csv", index=False)
    assert main(["curve-fit", "--prices", str(tmp_path / "few.csv"), "--settlement", "2026-11-16",
                 "--out-dir", str(tmp_path / "out")]) == 2
    assert "5 bond(s) in the fit" in capsys.readouterr().err
    assert main(["curve-fit", "--prices", str(tmp_path / "few.csv"), "--settlement", "16-11-2026",
                 "--out-dir", str(tmp_path / "out")]) == 2
    assert "--settlement" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


# ---------------------------------------------------------------- review fixes


def test_an_anchor_that_agrees_with_the_bonds_does_not_spoil_the_spline():
    # a sparse curve without bills, anchored at its own z(0+): the CV score is taken over the bonds, not the anchor
    mats = [date(2030, 5, 15), date(2031, 11, 15), date(2032, 5, 15), date(2033, 11, 15), date(2035, 5, 15),
            date(2036, 11, 15), date(2038, 5, 15), date(2040, 11, 15), date(2043, 5, 15), date(2046, 11, 15),
            date(2051, 5, 15), date(2056, 11, 15)]
    rows = [{"isin": f"IN0{k:09d}", "kind": "gsec", "coupon_pct": 7.0, "maturity_date": m,
             "clean_price": price_off(UPWARD, 7.0, m), "traded": True} for k, m in enumerate(mats)]
    frame = pd.DataFrame(rows)
    z0 = UPWARD[0] + UPWARD[1]
    spl = cf.fit_curve(frame, S, cf.FitSettings(method="pspline", anchor_pct=z0, leave_one_out=False))
    grid = np.array([4.0, 5, 10, 20, 30])
    assert spl.diagnostics["rmse_bp"] < 0.01 and spl.diagnostics["lam"] < 1e8
    assert np.max(np.abs(spl.curve.zero_rate(grid) - cf.nss_zero(grid, UPWARD))) * 100 < 0.5
    nss = cf.fit_curve(frame, S, cf.FitSettings(anchor_pct=z0, leave_one_out=False))
    assert not cf.compare_curves(nss.curve, spl.curve)["model_risk"].any()


def test_z_scores_count_prior_observations_in_one_pass():
    days = pd.bdate_range("2025-01-01", periods=200).date
    isins = [f"IN{k:010d}" for k in range(130)]
    rng = np.random.default_rng(5)
    r = rng.normal(0, 2, len(days) * len(isins))
    r[rng.uniform(size=r.size) < 0.3] = np.nan
    panel = pd.DataFrame({"date": np.repeat(days, len(isins)), "isin": np.tile(isins, len(days)), "resid_yield_bp": r})
    started = time.perf_counter()
    z = cf.residual_zscores(panel)
    assert time.perf_counter() - started < 3.0  # 26,000 rows, 30% without a residual: the old per-row scan took 11 s
    one = z[z["isin"] == isins[7]].reset_index(drop=True)
    finite = np.isfinite(one["resid_yield_bp"].to_numpy())
    expected = np.minimum(np.cumsum(finite) - finite, cf.ZSCORE_WINDOW)
    assert one["n_prior"].tolist() == expected.tolist()  # rows with and without a residual alike


def test_benchmark_points_under_half_a_year_are_semiannual_rates():
    flat = cf.NssCurve(params=(6.5, 0.0, 0.0, 0.0, 1.0, 2.0), valuation_date=S, t_min=0.1, t_max=30, method="nss")
    frame = cf.benchmark_frame([flat], tenors=(0.1, 0.25, 0.5, 1.0, 2.0))
    assert frame["yield_pct"].to_numpy() == pytest.approx([200 * math.expm1(0.065 / 2)] * 5, abs=1e-10)
    bench = BenchmarkCurve(frame, compounding="semiannual")
    annual = bench.yields(pd.Series([pd.Timestamp(S)] * 3), pd.Series([0.1, 0.25, 0.5]), to_basis="annual")
    assert annual == pytest.approx([math.expm1(0.065) * 100] * 3, abs=1e-9)


def test_settlement_must_be_a_business_day():
    frame = gsec_frame()
    with pytest.raises(ValueError, match="settlement 2026-11-15 is not a business day \\(a weekend"):
        cf.fit_curve(frame, date(2026, 11, 15), FAST)
    with pytest.raises(ValueError, match="settlement 2026-11-24 is not a business day \\(a holiday"):
        cf.fit_curve(frame, date(2026, 11, 24), FAST)
    assert cf.check_settlement(date(2027, 1, 26)) == date(2027, 1, 26)  # 2027: no holiday list, weekdays pass


def test_a_cheap_end_bond_is_cheap_out_of_sample_and_its_leverage_says_why():
    base = pd.concat([gsec_frame(), bill_rows()], ignore_index=True)
    longest = base.loc[base["maturity_date"].idxmax(), "isin"]
    shifted = with_yield_shift(base, longest, 10.0)
    nss = cf.fit_curve(shifted, S)
    row = nss.residuals.set_index("isin").loc[longest]
    assert 5 < row["resid_yield_bp"] < 8 and row["loo_resid_yield_bp"] == pytest.approx(10.0, abs=0.5)
    # in a linear fit the in-fit residual is (1 - leverage) times the out-of-sample one; nearly so for a small error
    small = cf.fit_curve(with_yield_shift(base, longest, 2.0), S).residuals.set_index("isin").loc[longest]
    assert small["resid_yield_bp"] == pytest.approx((1 - small["leverage"]) * small["loo_resid_yield_bp"], abs=0.01)
    inside = nss.residuals[nss.residuals["in_fit"]]
    assert inside["leverage"].between(0, 1).all() and inside["leverage"].sum() == pytest.approx(6, abs=1e-6)
    spl = cf.fit_curve(shifted, S, cf.FitSettings(method="pspline"))
    comparison = cf.compare_curves(nss.curve, spl.curve)
    full = cf.model_risk_flags(nss.residuals, comparison).set_index("isin")
    assert full.loc[longest, "model_risk"]  # the spline bends to the cheap bond: a gap at its maturity
    loo = cf.model_risk_flags(nss.residuals, comparison, check_residuals=spl.residuals).set_index("isin")
    assert abs(loo.loc[longest, "crosscheck_loo_diff_bp"]) < 1 and not loo.loc[longest, "model_risk"]
    scored = cf.rich_cheap_table(nss.residuals, column="loo_resid_yield_bp")
    assert scored.set_index("isin").loc[longest, "loo_resid_yield_bp"] > 9.5
    with pytest.raises(ValueError, match="leave_one_out"):
        cf.model_risk_flags(nss.residuals.drop(columns="loo_par_pct"), comparison, check_residuals=spl.residuals)


def _bumped(frame, size_bp, centre=11.0, width=2.0):
    """``frame``'s G-secs priced on the UPWARD curve plus a bump of ``size_bp`` in zero rates around ``centre``."""
    out = frame.copy()
    for k in out.index[out["kind"] == "gsec"]:
        bond = FixedRateBond(out.loc[k, "coupon_pct"], out.loc[k, "maturity_date"])
        flows = bond.cash_flows(S)
        t = np.array([(f.date - S).days / 365 for f in flows])
        z = cf.nss_zero(t, UPWARD) + size_bp / 100 * np.exp(-((t - centre) / width) ** 2)
        out.loc[k, "clean_price"] = float(np.array([f.amount for f in flows]) @ np.exp(-z * t / 100)) - \
            bond.accrued_interest(S)
    return out


def test_a_large_mispricing_elsewhere_is_not_model_risk_but_a_shape_nss_cannot_follow_is():
    base = pd.concat([gsec_frame(), bill_rows()], ignore_index=True)
    bonds = base[base["kind"] == "gsec"].sort_values("maturity_date").reset_index(drop=True)
    primary, check = cf.FitSettings(), cf.FitSettings(method="pspline")
    for k in (len(bonds) - 1, 15):  # the longest bond, then a mid-curve one, 20 bp cheap on an exact NSS curve
        isin = bonds.loc[k, "isin"]
        frame = with_yield_shift(base, isin, 20.0)
        nss, spl = cf.fit_curve(frame, S, primary), cf.fit_curve(frame, S, check)
        comparison = cf.compare_curves(nss.curve, spl.curve)
        first = cf.model_risk_flags(nss.residuals, comparison, check_residuals=spl.residuals)
        # it sways both refits, differently: gaps open at its neighbours (2066) or at the extrapolated 35-day bill
        assert first["model_risk"].any() and not first.set_index("isin").loc[isin, "model_risk"]
        assert cf.suspect_bonds(nss.residuals) == [isin]
        judged = cf.model_risk_flags(nss.residuals, comparison, check_residuals=spl.residuals,
                                     refit=cf.refitter(frame, S, primary, check))
        assert not judged["model_risk"].any()
        cleared = judged[judged["model_risk_note"].str.startswith("cleared")]
        assert set(cleared["isin"]) == set(first.loc[first["model_risk"], "isin"])
        assert cleared["crosscheck_suspect_diff_bp"].abs().max() < 0.1 and "flag(s) cleared" in judged.attrs["notes"][0]

        def broken(isins):
            raise ValueError("too few bonds")

        kept = cf.model_risk_flags(nss.residuals, comparison, check_residuals=spl.residuals, refit=broken)
        assert kept["model_risk"].equals(first["model_risk"])  # a flag whose refit fails stands
        assert kept.loc[kept["model_risk"], "model_risk_note"].str.startswith("kept: the curves could not").all()
    # a 10 bp bump in zero rates at 11 years, which NSS cannot follow: no bond is suspect, so the flags stand
    bumped = _bumped(base, 10.0)
    nss, spl = cf.fit_curve(bumped, S, primary), cf.fit_curve(bumped, S, check)
    judged = cf.model_risk_flags(nss.residuals, cf.compare_curves(nss.curve, spl.curve), check_residuals=spl.residuals,
                                 refit=cf.refitter(bumped, S, primary, check))
    flagged = judged[judged["model_risk"]]
    assert len(flagged) >= 2 and flagged["years_to_maturity"].between(6, 14).all() and judged.attrs["notes"] == []


def test_bond_pairs_are_dv01_neutral():
    res = cf.fit_curve(with_yield_shift(gsec_frame(), "IN0000000020", 4.0), S, FAST).residuals
    ten, forty = "IN0000000006", "IN0000000020"
    pair = cf.bond_pairs(res, [(ten, forty)]).iloc[0]
    by = res.set_index("isin")
    assert pair["resid_spread_bp"] == pytest.approx(by.loc[forty, "resid_yield_bp"] - by.loc[ten, "resid_yield_bp"])
    assert pair["yield_spread_bp"] - pair["model_spread_bp"] == pytest.approx(pair["resid_spread_bp"], abs=1e-9)
    assert pair["short_face"] * by.loc[ten, "dv01"] == pytest.approx(100 * by.loc[forty, "dv01"])
    with pytest.raises(ValueError, match="two different ISINs"):
        cf.bond_pairs(res, [(ten, ten)])
    with pytest.raises(ValueError, match="not shorter < longer"):
        cf.bond_pairs(res, [(forty, ten)])


def test_a_frame_without_trade_information_is_named_in_the_warnings():
    bare = gsec_frame().drop(columns="traded")
    warnings = cf.fit_curve(bare, S, FAST).diagnostics["warnings"]
    assert any(w.startswith("no_trade_data:") for w in warnings)
    assert not any(w.startswith("no_trade_data:") for w in cf.fit_curve(gsec_frame(), S, FAST).diagnostics["warnings"])


def test_history_is_appended_and_checked_for_its_method():
    first = cf.fit_curve(gsec_frame(), S, FAST).residuals
    history = cf.append_history(None, first)
    assert list(history.columns) == list(cf.HISTORY_COLUMNS) and len(history) == 29
    again = cf.append_history(history, first)  # a refit of the same day replaces its rows
    assert again.equals(history)
    nxt = date(2026, 11, 17)
    later = cf.fit_curve(gsec_frame(s=nxt), nxt, FAST).residuals
    both = cf.append_history(history, later)
    assert len(both) == 58 and sorted(set(both["date"])) == [S, nxt]
    scored = cf.rich_cheap_table(later, history, min_obs=1)
    assert scored["n_prior"].eq(1).all() and scored.attrs["notes"] == []
    spline_day = cf.fit_curve(gsec_frame(s=nxt), nxt, cf.FitSettings(method="pspline", leave_one_out=False))
    with pytest.raises(ValueError, match="history's method is \\['nss'\\]"):
        cf.rich_cheap_table(spline_day.residuals, history)
    with pytest.raises(ValueError, match="settings_id"):
        cf.append_history(history, cf.fit_curve(gsec_frame(s=nxt), nxt, cf.FitSettings(leave_one_out=False,
                                                                                       min_years=0.5)).residuals)


def test_a_history_anchored_at_each_days_own_rate_is_one_history():
    # the anchor is the day's overnight rate, data like the prices: days anchored at 5.61% and 5.65% append and score
    nxt = date(2026, 11, 17)
    first = cf.fit_curve(gsec_frame(), S, replace(FAST, anchor_pct=5.61)).residuals
    second = cf.fit_curve(gsec_frame(s=nxt), nxt, replace(FAST, anchor_pct=5.65)).residuals
    assert first["settings_id"].iat[0] == second["settings_id"].iat[0]
    history = cf.append_history(None, first)
    both = cf.append_history(history, second)
    assert sorted(set(both["date"])) == [S, nxt] and len(both) == 58
    scored = cf.rich_cheap_table(second, history, min_obs=1)
    assert scored["n_prior"].eq(1).all()
    # whether there is an anchor, and its weight, are the method: those still part a history
    plain = cf.settings_id(FAST)
    assert cf.settings_id(replace(FAST, anchor_pct=5.61)) != plain
    assert cf.settings_id(replace(FAST, anchor_pct=5.61, anchor_weight=2.0)) != cf.settings_id(
        replace(FAST, anchor_pct=5.61))
    with pytest.raises(ValueError, match="settings_id"):
        cf.append_history(history, cf.fit_curve(gsec_frame(s=nxt), nxt, FAST).residuals)
    assert first["curve_id"].iat[0] != cf.fit_curve(gsec_frame(), S, replace(FAST, anchor_pct=5.65)).residuals[
        "curve_id"].iat[0]  # the fit itself still knows its anchor


def test_rich_cheap_table_on_a_history_longer_than_the_window_equals_the_full_panel():
    # 150 days of residuals for three ISINs: the last day scored against the 149 before it must give the same scores
    # as residual_zscores on the whole panel (only each ISIN's last `window` residuals may enter, no fewer)
    days = pd.bdate_range("2026-04-01", periods=150).date
    rng = np.random.default_rng(11)
    isins = ["IN0000000001", "IN0000000002", "IN0000000003"]
    panel = pd.DataFrame({"date": np.repeat(days, 3), "isin": np.tile(isins, len(days)),
                          "resid_yield_bp": rng.normal(0.0, 2.0, 3 * len(days)) + np.repeat(np.linspace(0, 6, 150), 3),
                          "years_to_maturity": np.tile([2.0, 9.0, 30.0], len(days))})
    full = cf.residual_zscores(panel)
    expected = full[full["date"] == days[-1]].set_index("isin")
    today = panel[panel["date"] == days[-1]].reset_index(drop=True)
    scored = cf.rich_cheap_table(today, panel[panel["date"] < days[-1]]).set_index("isin")
    assert (scored["n_prior"] == cf.ZSCORE_WINDOW).all()
    for col in ("prior_mean", "prior_sd", "z_ts"):
        assert scored.loc[isins, col].to_numpy() == pytest.approx(expected.loc[isins, col].to_numpy(), abs=1e-12)


def test_cli_curve_fit_options_and_missing_files(tmp_path, capsys, isin):
    frame = gsec_frame()
    frame["isin"] = [isin(f"IN00202{k:04d}") for k in range(len(frame))]
    _master(frame).to_csv(tmp_path / "master.csv", index=False)
    frame[["isin", "clean_price"]].assign(volume_cr=100).to_csv(tmp_path / "prices.csv", index=False)
    args = ["curve-fit", "--prices", str(tmp_path / "prices.csv"), "--master", str(tmp_path / "master.csv"),
            "--settlement", "2026-11-16", "--check", "none"]
    pair = ",".join(frame["isin"].iloc[[6, 20]])
    hist = tmp_path / "hist.csv"
    assert main([*args, "--append-history", str(hist), "--pair", pair, "--out-dir", str(tmp_path / "o")]) == 0
    assert len(pd.read_csv(hist)) == 29 and len(pd.read_csv(tmp_path / "o" / "pairs.csv")) == 1
    assert main([*args, "--residual", "loo", "--history", str(hist), "--out-dir", str(tmp_path / "p")]) == 2
    assert "on or after 2026-11-16" in capsys.readouterr().err  # the history holds the day itself
    for missing in ("--prices", "--master"):
        bad = [*args]
        bad[bad.index(missing) + 1] = str(tmp_path / "absent.csv")
        assert main([*bad, "--out-dir", str(tmp_path / "q")]) == 2
        assert "absent.csv" in capsys.readouterr().err
    assert main([*args, "--history", str(tmp_path / "absent.csv"), "--out-dir", str(tmp_path / "r")]) == 2


def test_cli_curve_fit_keeps_a_daily_history_anchored_at_each_days_rate(tmp_path, capsys):
    # analytics.md's routine: --append-history once a day, that file as the next day's --history, each day anchored
    # at its own call rate
    hist = tmp_path / "hist.csv"
    for day, anchor in ((S, "5.61"), (date(2026, 11, 17), "5.65")):
        prices = tmp_path / f"prices_{day:%Y%m%d}.csv"
        gsec_frame(s=day).to_csv(prices, index=False)
        args = ["curve-fit", "--prices", str(prices), "--settlement", day.isoformat(), "--anchor", anchor,
                "--check", "none", "--no-leave-one-out", "--append-history", str(hist),
                "--out-dir", str(tmp_path / f"o{day:%d}")]
        if hist.exists():
            args += ["--history", str(hist), "--min-obs", "1"]
        assert main(args) == 0, capsys.readouterr().err
    kept = pd.read_csv(hist)
    assert sorted(set(kept["date"])) == ["2026-11-16", "2026-11-17"] and kept["settings_id"].nunique() == 1
    rich = pd.read_csv(tmp_path / "o17" / "rich_cheap.csv")
    assert len(rich) == 29 and (rich["n_prior"] == 1).all()


# ---------------------------------------------------------------- what a residual still holds


def _ledger(rows):
    from cr_strat.issuance import LEDGER_COLUMNS

    return pd.DataFrame([{c: None for c in LEDGER_COLUMNS} | r for r in rows])


def test_residual_drivers_join_the_master_ledger_and_stock_as_of_the_day(isin):
    a, b, c = (isin(f"IN00202{k:04d}") for k in (1, 2, 3))
    day = date(2026, 11, 16)
    residuals = pd.DataFrame([
        {"date": day, "isin": a, "in_fit": True, "resid_yield_bp": 1.0, "coupon_pct": 7.1, "flags": "benchmark"},
        {"date": day, "isin": b, "in_fit": True, "resid_yield_bp": -2.0, "coupon_pct": None, "flags": ""},
        {"date": day, "isin": c, "in_fit": True, "resid_yield_bp": 0.5, "coupon_pct": 6.5, "flags": None}])
    master = pd.DataFrame([{k: None for k in MASTER_COLUMNS} | row for row in (
        {"isin": a, "coupon_pct": 7.1, "issue_date": pd.Timestamp("2016-11-16"), "far": True},
        {"isin": b, "coupon_pct": 6.9, "issue_date": None, "far": False})])
    ledger = _ledger([
        {"key": b, "isin": b, "event": "issue", "date": pd.Timestamp("2024-05-10"), "amount_cr": 12000.0},
        {"key": b, "isin": b, "event": "reissue", "date": pd.Timestamp("2025-01-10"), "amount_cr": 8000.0},
        {"key": b, "isin": b, "event": "reissue", "date": pd.Timestamp("2026-11-06"), "amount_cr": 6000.0},
        {"key": b, "isin": b, "event": "reissue", "date": pd.Timestamp("2026-11-20"), "amount_cr": 6000.0},  # later
        {"key": a, "isin": a, "event": "switch_in", "date": pd.Timestamp("2026-10-01"), "amount_cr": 500.0}])
    stock = pd.DataFrame([
        {"key": b, "date": pd.Timestamp(day), "outstanding_cr": 26000.0, "issuance_complete": True,
         "operations_complete": True},
        {"key": a, "date": pd.Timestamp("2026-11-13"), "outstanding_cr": 99999.0, "issuance_complete": True,
         "operations_complete": True}])  # another day's stock: not used
    out = cf.residual_drivers(residuals, master, ledger=ledger, stock=stock).set_index("isin")
    assert set(cf.DRIVER_COLUMNS) <= set(out.columns)
    assert bool(out.loc[a, "far"]) and out.loc[a, "benchmark"] and not out.loc[b, "benchmark"]
    assert out.loc[a, "age_years"] == pytest.approx((day - date(2016, 11, 16)).days / 365.25)
    assert math.isnan(out.loc[a, "reissues"]) and math.isnan(out.loc[a, "outstanding_cr"])  # a switch is no issue
    # the master fills a missing coupon, the ledger's first issue a missing issue date; the 20 Nov reissue is unseen
    assert out.loc[b, "coupon_pct"] == 6.9 and out.loc[b, "issue_date"] == date(2024, 5, 10)
    assert out.loc[b, "reissues"] == 2 and out.loc[b, "last_issue_date"] == date(2026, 11, 6)
    assert out.loc[b, "days_since_issue"] == 10 and not bool(out.loc[b, "far"])
    assert out.loc[b, "outstanding_cr"] == 26000 and out.loc[b, "log_stock"] == pytest.approx(math.log(26000))
    assert bool(out.loc[b, "stock_complete"])
    assert out.loc[c, "coupon_pct"] == 6.5 and pd.isna(out.loc[c, "far"]) and math.isnan(out.loc[c, "age_years"])
    with pytest.raises(ValueError, match="stock table is missing columns \\['outstanding_cr'\\]"):
        cf.residual_drivers(residuals, stock=stock.drop(columns=["outstanding_cr"]))


def test_control_residuals_take_out_what_the_drivers_explain():
    rng = np.random.default_rng(11)
    n, day, other = 30, date(2026, 11, 16), date(2026, 11, 17)
    frame = pd.DataFrame({"date": [day] * n, "isin": [f"X{k:02d}" for k in range(n)], "in_fit": True,
                          "coupon_pct": rng.uniform(5.5, 8.0, n), "age_years": rng.uniform(0.0, 20.0, n),
                          "log_stock": np.log(rng.uniform(5e3, 1.5e5, n)), "far": rng.integers(0, 2, n) == 1})
    design = np.column_stack([np.ones(n), frame[["coupon_pct", "age_years", "log_stock"]], frame["far"]]).astype(float)
    richness = rng.normal(0.0, 0.5, n)
    richness -= design @ np.linalg.lstsq(design, richness, rcond=None)[0]  # what no driver explains
    true = np.array([1.5, 0.8, -0.1, -0.6, 2.0])
    frame["resid_yield_bp"] = design @ true + richness
    cheap = frame.iloc[[0]].assign(isin="CHEAP", in_fit=False)  # out of the fit: controlled, not in the regression
    cheap["resid_yield_bp"] += 10.0 - richness[0]
    few = frame.iloc[:6].assign(date=other)  # 6 rows cannot carry 5 coefficients and 5 degrees of freedom
    out = cf.control_residuals(pd.concat([frame, cheap, few], ignore_index=True))
    coefs = out.attrs["coefficients"]
    assert set(coefs["date"]) == {day} and coefs["n"].eq(n).all()
    assert np.allclose(coefs.set_index("driver").loc[["intercept", *cf.CONTROL_DRIVERS], "coef"], true, atol=1e-9)
    assert np.allclose(out["resid_ctrl_bp"].iloc[:n], richness, atol=1e-9)
    assert out["resid_ctrl_bp"].iloc[n] == pytest.approx(10.0, abs=1e-9)
    assert out["resid_ctrl_bp"].iloc[n + 1:].isna().all()
    assert any(note.startswith("2026-11-17: 6 in-fit row(s)") for note in out.attrs["notes"])
    same = cf.control_residuals(frame.assign(far=True))  # a driver that does not vary is dropped, not singular
    dropped = same.attrs["coefficients"].set_index("driver").loc["far"]
    assert math.isnan(dropped["coef"]) and "dropped" in dropped["note"]
    assert np.isfinite(same["resid_ctrl_bp"]).all()
    with pytest.raises(ValueError, match="missing columns \\['log_stock'\\]"):
        cf.control_residuals(frame.drop(columns=["log_stock"]))
    with pytest.raises(ValueError, match="min_dof"):
        cf.control_residuals(frame, min_dof=0)


def test_cli_curve_fit_writes_drivers_and_controls(tmp_path, capsys, isin):
    frame = gsec_frame()
    frame["isin"] = [isin(f"IN00202{k:04d}") for k in range(len(frame))]
    master = _master(frame)
    master["issue_date"] = [date(2016 + k % 9, 1 + k % 12, 10) for k in range(len(frame))]
    master["far"] = [k % 3 == 0 for k in range(len(frame))]
    master.to_csv(tmp_path / "master.csv", index=False)
    frame[["isin", "clean_price"]].assign(volume_cr=100).to_csv(tmp_path / "prices.csv", index=False)
    two = frame["isin"].iloc[[4, 9]].tolist()
    ledger = _ledger([{"key": i, "isin": i, "kind": "gsec", "event": e, "date": d, "date_source": "settlement",
                       "amount_cr": amount, "maturity_date": frame.loc[frame["isin"] == i, "maturity_date"].iloc[0]}
                      for i in two for e, d, amount in (("issue", "2025-06-13", 10000.0),
                                                        ("reissue", "2026-11-06", 4000.0))])
    ledger.to_csv(tmp_path / "ledger.csv", index=False)
    args = ["curve-fit", "--prices", str(tmp_path / "prices.csv"), "--master", str(tmp_path / "master.csv"),
            "--settlement", "2026-11-16", "--check", "none", "--ledger", str(tmp_path / "ledger.csv"),
            "--control", "coupon_pct,age_years,far", "--out-dir", str(tmp_path / "o")]
    assert main(args) == 0
    drivers = pd.read_csv(tmp_path / "o" / "drivers.csv").set_index("isin")
    assert len(drivers) == 29 and drivers.loc[two, "reissues"].eq(1).all()
    assert drivers.loc[two, "days_since_issue"].eq(10).all() and drivers.loc[two, "outstanding_cr"].eq(14000).all()
    assert drivers["resid_ctrl_bp"].abs().max() < 1e-6  # the curve prices every bond: nothing to explain
    assert set(pd.read_csv(tmp_path / "o" / "controls.csv")["driver"]) == {"intercept", "coupon_pct", "age_years",
                                                                          "far"}
    capsys.readouterr()
    assert main([*args[:-4], "--control", "log_stock", "--out-dir", str(tmp_path / "p")]) == 0  # stock of two bonds
    assert "2 in-fit row(s) with every driver known, fewer than the 7" in capsys.readouterr().out
    assert pd.read_csv(tmp_path / "p" / "controls.csv").empty
    assert main([a for a in args if a not in ("--ledger", str(tmp_path / "ledger.csv"), "--master",
                                              str(tmp_path / "master.csv"))]) == 2
    assert "--control needs the drivers" in capsys.readouterr().err


def test_cli_curve_fit_writes_the_nss_parameters_cr_strat_returns_reads(tmp_path, capsys):
    from cr_strat import returns as rt
    pd.concat([gsec_frame(), bill_rows()], ignore_index=True).to_csv(tmp_path / "prices.csv", index=False)
    table = tmp_path / "nss_params.csv"
    args = ["curve-fit", "--prices", str(tmp_path / "prices.csv"), "--settlement", "2026-11-16", "--check", "none",
            "--no-leave-one-out", "--nss-params", str(table), "--out-dir", str(tmp_path / "out")]
    assert main(args) == 0
    assert "the business day before --settlement" in capsys.readouterr().out
    assert main([*args, "--trade-date", "2026-11-13", "--public-from", "2026-11-13T18:30:00"]) == 0  # a refit
    written = pd.read_csv(table, dtype=str, keep_default_na=False)
    assert len(written) == 1 and written["date"].iloc[0] == "2026-11-13"
    entry = rt.read_nss_params(table)["gsec"][date(2026, 11, 13)]
    fitted = json.loads((tmp_path / "out" / "diagnostics.json").read_text())["primary"]
    assert entry.curve.valuation_date == date(2026, 11, 16) and entry.public_from.hour == 18
    assert fitted["method"] == "nss" and len(entry.curve.params) == 6
    assert main([*args[:-2], "--method", "pspline", "--out-dir", str(tmp_path / "x")]) == 2
    assert "--nss-params writes NSS parameters" in capsys.readouterr().err
