"""Tests of asset_swaps: G-sec and bill asset-swap measures against OIS and fitted curves (synthetic data only).

B7 and B8 are the analytics spec's vectors (19 Nov 2026), computed there by a scratch reference implementation.
"""

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from cr_strat import asset_swaps as aw
from cr_strat import curvefit as cf
from cr_strat import ois
from cr_strat.bonds import DiscountInstrument, FixedRateBond, ZeroCouponBond
from cr_strat.calendars import mumbai_settlement_calendar
from cr_strat.cli import main
from cr_strat.conventions import SDL

LENIENT = mumbai_settlement_calendar().lenient()
TRADE, S = date(2026, 11, 19), date(2026, 11, 20)
QUOTES = {"1M": 5.62, "2M": 5.66, "3M": 5.70, "6M": 5.78, "9M": 5.84, "1Y": 5.88, "2Y": 5.98, "3Y": 6.06, "4Y": 6.12,
          "5Y": 6.18}
GS2031 = FixedRateBond(6.36, date(2031, 2, 16))


@pytest.fixture(scope="module")
def curve():
    return ois.bootstrap_ois(QUOTES, TRADE, 5.60, calendar=LENIENT)


class Flat:
    """A flat continuously compounded curve from ``S`` (any DiscountCurveLike will do)."""

    def __init__(self, rate: float):
        self.rate, self.valuation_date, self.t_max = rate, S, 50.0

    def discount(self, when):
        scalar = isinstance(when, date)
        dates = [when] if scalar else list(when)
        out = np.array([math.exp(-self.rate * (d - S).days / 365) for d in dates])
        return out.reshape(()) if scalar else out


def on_curve_clean(bond, curve):
    flows = bond.cash_flows(S)
    pv = sum(f.amount * float(curve.discount(f.date) / curve.discount(S)) for f in flows)
    return pv - bond.accrued_interest(S)


def test_spec_vector_b7(curve):
    m = aw.asset_swap(GS2031, S, 99.50, curve)
    assert m.accrued == pytest.approx(1.660667, abs=1e-6) and m.dirty_price == pytest.approx(101.160667, abs=1e-6)
    assert m.ytm_pct == pytest.approx(6.493356, abs=1e-6) and m.modified_duration == pytest.approx(3.603648, abs=1e-6)
    assert m.pv_on_curve == pytest.approx(102.444859, abs=1e-6) and m.annuity == pytest.approx(3.69582591, abs=1e-8)
    assert m.k_match_pct == pytest.approx(6.134480, abs=1e-6)
    for name, value in (("yield_yield_bp", 35.8876), ("par_par_bp", 34.7471), ("proceeds_bp", 34.3484),
                        ("z_spread_bp", 33.8574), ("z_spread_sa_bp", 34.9258)):
        assert getattr(m, name) == pytest.approx(value, abs=1e-4), name
    assert not m.extrapolated and m.calendar_projected


def test_identities_b8(curve):
    m = aw.asset_swap(GS2031, S, 99.50, curve)
    assert m.proceeds_bp == m.par_par_bp * 100 / m.dirty_price
    at_model = aw.asset_swap(GS2031, S, on_curve_clean(GS2031, curve), curve)
    assert abs(at_model.par_par_bp) < 1e-9 and abs(at_model.z_spread_bp) < 1e-9 and abs(at_model.z_spread_sa_bp) < 1e-9
    # flat 6% continuous curve, bond priced at a continuous 6.42% on the same times: z-spread 42 bp
    flows = GS2031.cash_flows(S)
    dirty = sum(f.amount * math.exp(-0.0642 * (f.date - S).days / 365) for f in flows)
    z = aw.z_spread([f.date for f in flows], [f.amount for f in flows], S, dirty, Flat(0.06))
    assert z * 1e4 == pytest.approx(42.0, abs=1e-9)


def test_spreads_do_not_depend_on_the_notional(curve):
    """Per-100 measures equal the position-level ones for any face (here x1 and x7)."""
    m = aw.asset_swap(GS2031, S, 99.50, curve)
    for face in (1e7, 7e7):
        pv = face / 100 * m.pv_on_curve
        paid = face / 100 * m.dirty_price
        annuity = face * m.annuity
        assert (pv - paid) / annuity * 1e4 == pytest.approx(m.par_par_bp, rel=1e-12)
        assert (pv - paid) / (paid * m.annuity) * 1e4 == pytest.approx(m.proceeds_bp, rel=1e-12)
    h1 = aw.hedge_notional(GS2031, S, 99.50, curve, "4Y", face=1e7)
    h7 = aw.hedge_notional(GS2031, S, 99.50, curve, "4Y", face=7e7)
    assert h7 == pytest.approx(7 * h1, rel=1e-12)


def test_a_bond_priced_off_the_ois_curve_has_zero_z_spread_and_a_small_yield_spread(curve):
    for maturity in (date(2027, 2, 20), date(2027, 11, 15), date(2029, 6, 30), date(2031, 8, 31)):
        for coupon in (5.0, 7.5):
            bond = FixedRateBond(coupon, maturity)
            m = aw.asset_swap(bond, S, on_curve_clean(bond, curve), curve)
            assert abs(m.z_spread_bp) < 1e-8 and abs(m.par_par_bp) < 1e-8
            # yield against swap rate is not zero on the curve: 30E/360 against Act/365F and the coupon effect
            assert abs(m.yield_yield_bp) < 5.0


def test_bills_priced_on_the_curve_have_zero_yield_yield_and_it_equals_proceeds(curve):
    for days in (35, 91, 182, 364):
        bill = DiscountInstrument(S + timedelta(days=days))
        price = 100 * float(curve.discount(bill.maturity) / curve.discount(S))
        m = aw.asset_swap(bill, S, price, curve)
        assert abs(m.yield_yield_bp) < 1e-9 and abs(m.par_par_bp) < 1e-9
        off = aw.asset_swap(bill, S, price - 0.05, curve)
        assert off.yield_yield_bp == pytest.approx(off.proceeds_bp, abs=1e-9)  # both Act/365 simple, one flow
        assert off.yield_yield_bp > 0 and off.accrued == 0


def test_k_match_is_the_ois_par_rate_on_the_bonds_schedule(curve):
    m = aw.asset_swap(GS2031, S, 99.50, curve)
    dates = sorted({f.date for f in GS2031.cash_flows(S)})
    d = [float(curve.discount(x) / curve.discount(S)) for x in dates]
    starts = [S, *dates[:-1]]
    annuity = sum((e - b).days / 365 * v for b, e, v in zip(starts, dates, d))
    assert m.annuity == pytest.approx(annuity, rel=1e-15)
    assert m.k_match_pct == pytest.approx((1 - d[-1]) / annuity * 100, rel=1e-15)
    # one coupon date left: K_match is the single-period simple OIS rate
    short = FixedRateBond(7.0, date(2027, 2, 20))
    ms = aw.asset_swap(short, S, 100.2, curve)
    tau = (date(2027, 2, 20) - S).days / 365
    dt = float(curve.discount(date(2027, 2, 20)) / curve.discount(S))
    assert ms.k_match_pct == pytest.approx((1 / dt - 1) / tau * 100, rel=1e-13)


def test_against_a_fitted_curve_the_z_spread_is_a_spread_to_the_sovereign_curve():
    params = (7.6, -2.0, -1.0, 2.0, 1.5, 8.0)
    nss = cf.NssCurve(valuation_date=S, t_min=0.5, t_max=30.0, method="nss", params=params)
    bond = FixedRateBond(7.1, date(2036, 5, 11))
    flows = bond.cash_flows(S)
    t = np.array([(f.date - S).days / 365 for f in flows])
    a = np.array([f.amount for f in flows])
    dirty = float(a @ np.exp(-(cf.nss_zero(t, params) / 100 + 0.0010) * t))
    m = aw.asset_swap(bond, S, dirty - bond.accrued_interest(S), nss)
    assert m.z_spread_bp == pytest.approx(10.0, abs=1e-8) and not m.extrapolated


def test_beyond_the_ois_curve_refused_unless_extrapolation_allowed(curve):
    ten = FixedRateBond(6.94, date(2036, 5, 11))
    with pytest.raises(ValueError, match="beyond the curve"):
        aw.asset_swap(ten, S, 97.43, curve)
    longer = ois.bootstrap_ois(QUOTES, TRADE, 5.60, calendar=LENIENT, max_extrapolation_years=5)
    m = aw.asset_swap(ten, S, 97.43, longer)
    assert m.extrapolated and math.isfinite(m.z_spread_bp)


def test_refusals(curve):
    with pytest.raises(ValueError, match="STRIPS are not measured"):
        aw.asset_swap(ZeroCouponBond(date(2030, 5, 15)), S, 80.0, curve)
    with pytest.raises(TypeError, match="FixedRateBond"):
        aw.asset_swap("6.36% GS 2031", S, 99.5, curve)
    with pytest.raises(ValueError, match="clean price must be a positive"):
        aw.asset_swap(GS2031, S, -1, curve)
    with pytest.raises(ValueError, match="before the curve's valuation date"):
        aw.asset_swap(GS2031, date(2026, 11, 18), 99.5, curve)
    with pytest.raises(ValueError, match="float_schedule"):
        aw.asset_swap(GS2031, S, 99.5, curve, float_schedule="standard")


def test_hedge_notional_matches_the_bonds_dv01(curve):
    face = 1e9
    n = aw.hedge_notional(GS2031, S, 99.50, curve, "4Y", face=face)
    periods = ois.ois_schedule(S, 48, calendar=LENIENT)
    annuity = sum(p.year_fraction * float(curve.discount(p.end) / curve.discount(S)) for p in periods)
    dv01 = GS2031.analytics(S, clean_price=99.50).dv01
    assert n * annuity * 1e-4 == pytest.approx(face * dv01 / 100, rel=1e-12)


def test_asset_swap_table(curve, isin):
    frame = pd.DataFrame([
        {"isin": isin("IN002025014"), "kind": "gsec", "coupon_pct": 6.36, "maturity_date": "2031-02-16",
         "clean_price": 99.50},
        {"isin": isin("IN002026002"), "kind": "gsec", "coupon_pct": 6.94, "maturity_date": "2036-05-11",
         "clean_price": 97.43},
        {"isin": "TB2027A", "kind": "tbill", "coupon_pct": None, "maturity_date": "2027-02-18", "yield_pct": 5.55},
        {"isin": "SDL2029", "kind": "sdl", "coupon_pct": 7.1, "maturity_date": "2029-11-20", "clean_price": 100.1},
        {"isin": "FRB2033", "kind": "frb", "coupon_pct": 6.5, "maturity_date": "2033-10-22", "clean_price": 100.0},
    ])
    table = aw.asset_swap_table(frame, curve)
    assert list(table.columns) == list(aw.ASW_COLUMNS)
    assert list(table["isin"]) == ["TB2027A", "SDL2029", frame["isin"][0], "FRB2033", frame["isin"][1]]
    errors = dict(zip(table["isin"], table["error"]))
    assert "beyond the curve" in errors[frame["isin"][1]] and "has no pricer" in errors["FRB2033"]
    ok = table[table["error"] == ""].set_index("isin")
    assert (ok["settlement_date"] == S).all()  # the curve's effective date
    assert ok.loc[frame["isin"][0], "par_par_bp"] == pytest.approx(34.7471, abs=1e-4)
    bill = DiscountInstrument(date(2027, 2, 18))
    assert ok.loc["TB2027A", "clean_price"] == pytest.approx(bill.price(5.55, S, rounded=False), rel=1e-15)
    sdl = aw.asset_swap(FixedRateBond(7.1, date(2029, 11, 20), SDL), S, 100.1, curve)
    assert ok.loc["SDL2029", "z_spread_bp"] == pytest.approx(sdl.z_spread_bp, abs=1e-12)
    shuffled = aw.asset_swap_table(frame.sample(frac=1, random_state=3), curve)
    assert shuffled.equals(table)


def test_fill_from_master(isin):
    master = pd.DataFrame({"isin": [isin("IN002025014")], "kind": ["gsec"], "coupon_pct": [6.36],
                           "maturity_date": [date(2031, 2, 16)], "issue_date": [None]})
    bonds = pd.DataFrame({"isin": [isin("IN002025014")], "clean_price": ["99.50"]})
    filled = aw.fill_from_master(bonds, master)
    assert filled.loc[0, "coupon_pct"] == 6.36 and filled.loc[0, "maturity_date"] == date(2031, 2, 16)
    with pytest.raises(ValueError, match="coupon_pct is '6.5' in the bonds but 6.36 in the security master"):
        aw.fill_from_master(bonds.assign(coupon_pct="6.5"), master)


def test_cli_asset_swaps(tmp_path, capsys):
    pd.DataFrame({"tenor": list(QUOTES), "rate_pct": list(QUOTES.values())}).to_csv(tmp_path / "q.csv", index=False)
    pd.DataFrame([{"isin": "A", "kind": "gsec", "coupon_pct": 6.36, "maturity_date": "2031-02-16",
                   "clean_price": 99.5},
                  {"isin": "B", "kind": "gsec", "coupon_pct": 6.94, "maturity_date": "2036-05-11",
                   "clean_price": 97.43}]).to_csv(tmp_path / "b.csv", index=False)
    args = ["asset-swaps", "--quotes", str(tmp_path / "q.csv"), "--trade-date", "2026-11-19", "--overnight", "5.60",
            "--project-calendar", "--bonds", str(tmp_path / "b.csv")]
    assert main([*args, "--out", str(tmp_path / "asw.csv")]) == 0
    printed = capsys.readouterr().out
    assert "1 of 2 bonds measured" in printed and "refused B" in printed
    out = pd.read_csv(tmp_path / "asw.csv")
    assert out.loc[out["isin"] == "A", "z_spread_bp"].iloc[0] == pytest.approx(33.8574, abs=1e-4)
    assert main([*args, "--max-extrapolation-years", "5", "--out", str(tmp_path / "asw5.csv")]) == 0
    assert pd.read_csv(tmp_path / "asw5.csv")["extrapolated"].tolist() == [False, True]
    pd.read_csv(tmp_path / "b.csv").iloc[1:].to_csv(tmp_path / "only_long.csv", index=False)
    assert main([*args[:-1], str(tmp_path / "only_long.csv"), "--out", str(tmp_path / "none.csv")]) == 1


def test_settlement_on_a_weekend_or_holiday_and_duplicate_isins_are_refused(curve):
    with pytest.raises(ValueError, match="settlement 2026-11-21 is not a business day \\(a weekend"):
        aw.asset_swap(GS2031, date(2026, 11, 21), 99.5, curve)
    with pytest.raises(ValueError, match="settlement 2026-11-24 is not a business day \\(a holiday"):
        aw.asset_swap(GS2031, date(2026, 11, 24), 99.5, curve)
    frame = pd.DataFrame([{"isin": "A", "kind": "gsec", "coupon_pct": 6.36, "maturity_date": "2031-02-16",
                           "clean_price": price} for price in (99.50, 99.70)])
    with pytest.raises(ValueError, match="ISIN A more than once"):
        aw.asset_swap_table(frame, curve)
    saturday = aw.asset_swap_table(frame.iloc[:1].assign(settlement_date="2026-11-21"), curve)
    assert "not a business day" in saturday["error"].iloc[0]


def test_cli_missing_files_exit_2(tmp_path, capsys):
    pd.DataFrame({"tenor": list(QUOTES), "rate_pct": list(QUOTES.values())}).to_csv(tmp_path / "q.csv", index=False)
    common = ["--trade-date", "2026-11-19", "--overnight", "5.60", "--project-calendar"]
    assert main(["ois-curve", "--quotes", str(tmp_path / "absent.csv"), *common, "--out-dir", str(tmp_path / "o")]) == 2
    assert "absent.csv" in capsys.readouterr().err
    assert main(["asset-swaps", "--quotes", str(tmp_path / "q.csv"), *common, "--bonds", str(tmp_path / "nob.csv"),
                 "--out", str(tmp_path / "a.csv")]) == 2
    assert "nob.csv" in capsys.readouterr().err
