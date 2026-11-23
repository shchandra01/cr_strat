"""Tests of asset_swaps: G-sec and bill asset-swap measures against OIS and fitted curves (synthetic data only).

B7 and B8 are the analytics spec's vectors (19 Nov 2026), computed there by a scratch reference implementation.
"""

import math
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from cr_strat import asset_swaps as aw
from cr_strat import curvefit as cf
from cr_strat import ois
from cr_strat.bonds import DiscountInstrument, FixedRateBond, ZeroCouponBond
from cr_strat.calendars import mumbai_settlement_calendar
from cr_strat.cli import main
from cr_strat.conventions import SDL, convert_yield
from cr_strat.corporates import CorporateBond
from cr_strat.floating import GoiFloatingRateBond, discount_margin_bp

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


# ---------------------------------------------------------------- corporate bonds and FRBs (synthetic data only)

NCD = CorporateBond(7.4, date(2029, 6, 15), date(2024, 6, 15), isin="INE000A07011")


def test_a_corporate_bond_on_the_curve_has_zero_par_par_and_z_spread(curve):
    m = aw.asset_swap(NCD, S, on_curve_clean(NCD, curve), curve)
    assert abs(m.par_par_bp) < 1e-9 and abs(m.proceeds_bp) < 1e-9 and abs(m.z_spread_bp) < 1e-9
    assert m.accrued == pytest.approx(NCD.accrued_interest(S), rel=1e-15)
    assert m.ytm_pct == pytest.approx(NCD.yield_from_price(m.clean_price, S), abs=1e-12)  # annual, actual/365
    cheap = aw.asset_swap(NCD, S, 99.0, curve)
    assert cheap.proceeds_bp == pytest.approx(cheap.par_par_bp * 100 / cheap.dirty_price, rel=1e-14)
    assert 150 < cheap.z_spread_bp < cheap.proceeds_bp < cheap.par_par_bp < cheap.yield_yield_bp


def test_the_matched_rate_is_restated_to_the_bonds_annual_basis(curve):
    """A semi-annual NCD's yield is annual: its matched OIS rate on the semi-annual schedule is restated before the
    yield-yield spread (without it a bond priced on the curve would show 9 bp)."""
    semi = CorporateBond(7.4, date(2029, 6, 15), date(2024, 6, 15), frequency=2)
    clean = on_curve_clean(semi, curve)
    on_basis = aw.asset_swap(semi, S, clean, curve)
    raw = aw.asset_swap(semi, S, clean, curve, k_match_basis="schedule")
    assert on_basis.k_match_pct == pytest.approx(float(convert_yield(raw.k_match_pct, "semiannual", "annual")),
                                                 rel=1e-14)
    assert abs(on_basis.yield_yield_bp) < 0.1 and raw.yield_yield_bp > 9.0
    zero = CorporateBond(0.0, date(2029, 11, 20), date(2025, 11, 20), frequency=0, redemption=125.0)
    z = aw.asset_swap(zero, S, on_curve_clean(zero, curve), curve)
    assert abs(z.yield_yield_bp) < 1e-9 and abs(z.par_par_bp) < 1e-9  # one period: the curve's annual zero rate
    gsec = aw.asset_swap(GS2031, S, 99.50, curve, k_match_basis="bond")
    assert gsec.k_match_pct == aw.asset_swap(GS2031, S, 99.50, curve).k_match_pct  # a G-sec's is on its basis
    with pytest.raises(ValueError, match="k_match_basis"):
        aw.asset_swap(NCD, S, 99.0, curve, k_match_basis="annual")


def test_an_amortising_bond_swaps_an_amortising_notional(curve):
    bond = CorporateBond(8.0, date(2030, 4, 1), date(2025, 4, 1),
                         amortisation=((date(2028, 4, 1), 30.0), (date(2029, 4, 1), 30.0)))
    m = aw.asset_swap(bond, S, on_curve_clean(bond, curve), curve)
    assert abs(m.par_par_bp) < 1e-9 and abs(m.z_spread_bp) < 1e-9
    flows = sorted({f.date for f in bond.cash_flows(S)})
    d = [float(curve.discount(x) / curve.discount(S)) for x in flows]
    tau = [(e - b).days / 365 for b, e in zip([S, *flows[:-1]], flows)]
    assert flows == [date(2027, 4, 1), date(2028, 4, 1), date(2029, 4, 1), date(2030, 4, 1)]
    faces = [100.0, 100.0, 70.0, 40.0]  # each period's face: 30 redeemed at the end of the 2028 and 2029 periods
    assert m.annuity == pytest.approx(sum(t * x * f / 100 for t, x, f in zip(tau, d, faces)), rel=1e-14)


def test_an_ex_interest_settlement_leaves_the_coupon_out(curve):
    s = date(2026, 12, 3)  # the 15 Dec coupon's record date is 30 Nov: a settlement on 3 Dec is ex-interest
    bond = CorporateBond(9.0, date(2030, 12, 15), date(2023, 12, 15))
    assert bond.is_ex_interest(s) and bond.accrued_interest(s) < 0
    flows = bond.cash_flows(s)
    assert flows[0].date == date(2027, 12, 15)  # the 2026 coupon goes to the seller
    pv = sum(f.amount * float(curve.discount(f.date) / curve.discount(s)) for f in flows)
    m = aw.asset_swap(bond, s, pv - bond.accrued_interest(s), curve)
    assert abs(m.par_par_bp) < 1e-9 and m.dirty_price == pytest.approx(pv, rel=1e-14)


def corp_terms():
    return pd.DataFrame([
        {"isin": "INE000A07011", "kind": "corporate", "coupon_pct": "7.4", "frequency": "1",
         "issue_date": "2024-06-15", "maturity_date": "2029-06-15"},
        {"isin": "INE000F07011", "kind": "frb_corp", "coupon_pct": "7.4", "frequency": "4",
         "issue_date": "2025-06-15", "maturity_date": "2028-06-15"},
    ])


def test_asset_swap_table_with_corporate_terms(curve):
    frame = pd.DataFrame([
        {"isin": "INE000A07011", "kind": "", "maturity_date": "", "clean_price": on_curve_clean(NCD, curve)},
        {"isin": "INE000F07011", "kind": "", "maturity_date": "", "clean_price": 100.0},
        {"isin": "INE000B07011", "kind": "corporate", "coupon_pct": 8.1, "maturity_date": "2028-03-10",
         "issue_date": "2025-03-10", "frequency": 1, "yield_pct": 7.9},
        {"isin": GS2031.name or "GS2031", "kind": "gsec", "coupon_pct": 6.36, "maturity_date": "2031-02-16",
         "clean_price": 99.50},
    ])
    table = aw.asset_swap_table(frame, curve, terms=corp_terms())
    rows = table.set_index("isin")
    assert rows.loc["INE000A07011", "kind"] == "corporate" and rows.loc["INE000A07011", "error"] == ""
    assert rows.loc["INE000A07011", "maturity_date"] == date(2029, 6, 15)
    assert abs(rows.loc["INE000A07011", "par_par_bp"]) < 1e-9
    assert "discount margin" in rows.loc["INE000F07011", "error"]
    row = rows.loc["INE000B07011"]  # a corporate row read as a terms row: priced from its yield
    bond = CorporateBond(8.1, date(2028, 3, 10), date(2025, 3, 10))
    assert row["error"] == "" and row["ytm_pct"] == pytest.approx(7.9, abs=1e-9)
    assert row["clean_price"] == pytest.approx(bond.clean_price(7.9, S), rel=1e-14)
    assert rows.loc["GS2031", "par_par_bp"] == pytest.approx(34.7471, abs=1e-4)  # G-secs unchanged
    clash = frame.iloc[[0]].assign(maturity_date="2029-06-16")
    assert "differs from the terms" in aw.asset_swap_table(clash, curve, terms=corp_terms())["error"].iloc[0]


def flat_curve(rate: float):
    class FlatCC(Flat):
        def discount_t(self, t):
            return np.exp(-self.rate * np.asarray(t, dtype=float))
    return FlatCC(rate)


def frb2033(rate: float = 7.70, projected: float = 7.73) -> GoiFloatingRateBond:
    """FRB 2033 with synthetic rates: the current period at ``rate``, later ones projected (T22's set-up)."""
    starts = [date(2026, 9, 22)] + [date(y, m, 22) for y in range(2027, 2034) for m in (3, 9)
                                    if date(y, m, 22) < date(2033, 9, 22)]
    rows = [(starts[0], rate, "announced")] + [(d, projected, "projected") for d in starts[1:]]
    return GoiFloatingRateBond(date(2033, 9, 22), date(2020, 6, 22), 1.22, tuple(rows),
                               first_coupon_date=date(2020, 9, 22), index_pct=6.51)


def test_frbs_are_measured_by_their_discount_margin():
    bond = frb2033()
    curve = flat_curve(0.065)
    with pytest.raises(ValueError, match="discount margin"):
        aw.asset_swap(bond, S, 100.0, curve)
    m = aw.frb_measures(bond, S, 100.0, curve)
    assert m.accrued == pytest.approx(1.2405555556, abs=1e-10)  # T22: 7.70% on the unit accrued 0.1611111111
    assert m.frb_yield_pct == pytest.approx(7.7250833578, abs=1e-9)  # T22's yield at clean 100
    assert m.dm_index_bp == pytest.approx(121.508336, abs=1e-6)  # over the 6.51% index
    assert m.dm_curve_bp == pytest.approx(discount_margin_bp(bond, S, 100.0, curve=curve, form="curve"), abs=1e-8)
    assert m.coupon_pct == 7.70 and m.coupon_status == "announced"
    assert m.spread_dv01 > 0 and abs(m.index_dv01) < m.spread_dv01 / 10  # a floater's rate risk is small
    no_index = aw.frb_measures(GoiFloatingRateBond(bond.maturity, bond.issue_date, 1.22, bond.rates,
                                                   first_coupon_date=bond.first_coupon_date), S, 100.0, curve)
    assert math.isnan(no_index.dm_index_bp) and no_index.spread_dv01 > 0


def weekly_tbills(public_shift_days: int = 0) -> pd.DataFrame:
    rows, d, k = [], date(2026, 5, 20), 0
    while d <= date(2026, 11, 18):
        public = d + timedelta(days=public_shift_days if d == date(2026, 11, 18) else 0)
        rows.append({"auction_date": d.isoformat(), "tenor_days": 182, "way_pct": round(5.50 + 0.01 * k, 4),
                     "public_from": f"{public.isoformat()}T18:30"})
        d += timedelta(days=7)
        k += 1
    return pd.DataFrame(rows)


def test_frb_asset_swap_table_uses_only_coupons_known_at_as_of(curve):
    frame = pd.DataFrame([{"isin": "IN0020210160", "clean_price": 100.40}, {"isin": "GS2036", "clean_price": 97.4}])
    as_of = datetime(2026, 11, 19, 20, 0)
    table = aw.frb_asset_swap_table(frame, curve, S, tbills=weekly_tbills(), as_of=as_of)
    assert list(table.columns) == list(aw.FRB_ASW_COLUMNS)
    rows = table.set_index("isin")
    frb = rows.loc["IN0020210160"]  # FRB 2028, spread 0.64 (shipped terms)
    assert frb["error"] == "" and frb["name"] == "GOI FRB 2028"
    assert frb["coupon_status"] == "computed" and frb["coupon_pct"] == pytest.approx(5.68 + 0.64)  # Sep auctions
    assert frb["next_status"] == "projected" and frb["index_pct"] == pytest.approx(5.75)  # 4, 11, 18 Nov results
    assert frb["next_coupon_pct"] == pytest.approx(5.75 + 0.64)
    assert "not in the GoI FRB terms" in rows.loc["GS2036", "error"]
    late = aw.frb_asset_swap_table(frame.iloc[[0]], curve, S, tbills=weekly_tbills(public_shift_days=1), as_of=as_of)
    assert late["index_pct"].iloc[0] == pytest.approx(5.75)  # 18 Nov's result public on 19 Nov at 18:30: used
    later = aw.frb_asset_swap_table(frame.iloc[[0]], curve, S, tbills=weekly_tbills(public_shift_days=2),
                                    as_of=as_of)
    assert later["index_pct"].iloc[0] == pytest.approx(5.74)  # public on 20 Nov: 28 Oct, 4 and 11 Nov only


def test_a_corporate_row_that_contradicts_its_terms_is_refused(curve):
    """A row whose coupon and kind contradict the terms was measured as the terms' bond, without a word."""
    frame = pd.DataFrame([{"isin": "INE000A07011", "kind": "gsec", "coupon_pct": 6.0, "clean_price": 99.0}])
    assert "coupon_pct 6.0 differs from the terms' 7.4" in aw.asset_swap_table(frame, curve,
                                                                               terms=corp_terms())["error"].iloc[0]
    wrong_kind = frame.assign(coupon_pct=7.4)
    assert "kind 'gsec' differs from the terms" in aw.asset_swap_table(wrong_kind, curve,
                                                                       terms=corp_terms())["error"].iloc[0]
    ok = aw.asset_swap_table(frame.assign(coupon_pct=7.4, kind="corporate", isin="ine000a07011"), curve,
                             terms=corp_terms())
    assert ok["error"].iloc[0] == "" and ok["kind"].iloc[0] == "corporate"
    empty = pd.DataFrame(columns=["isin", "clean_price"])  # an FRB-only bonds file, its FRBs measured apart
    assert aw.asset_swap_table(empty, curve).empty


def test_the_asset_swap_of_a_called_bond_runs_to_its_call(curve):
    options = pd.DataFrame([{"isin": "INE000A07011", "kind": "call", "date": "2027-06-15", "exercised": "yes",
                             "exercised_public_from": "2026-11-02T17:00"}])
    frame = pd.DataFrame([{"isin": "INE000A07011", "clean_price": 100.0}])
    called = aw.asset_swap_table(frame, curve, terms=corp_terms(), options=options).iloc[0]
    assert called["maturity_date"] == date(2027, 6, 15) and called["error"] == ""
    before = aw.asset_swap_table(frame, curve, terms=corp_terms(), options=options, as_of="2026-11-01T00:00").iloc[0]
    assert before["maturity_date"] == date(2029, 6, 15)
    assert called["years_to_maturity"] < 1 < before["years_to_maturity"]
