"""Tests of stress.py: the shipped scenario table and its refusals, BCBS's IRRBB shapes (T18) and their full
revaluation (T19, T22), the yield mode (T8), key-rate scenarios (T21), spread, FRB, funding (T23) and liquidity (T24,
T25) components with their interaction, the legal-form rules and look-ahead in the choice of scenarios. Synthetic data
only (risk_fixtures)."""

import math
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest
import risk_fixtures as fx

from cr_strat import returns as rt
from cr_strat import risk as rk
from cr_strat import stress as st
from cr_strat.bonds import DiscountInstrument
from cr_strat.conventions import CP

T18 = np.array([0.0028, 0.25, 0.5, 1, 2, 3.5, 5, 10, 15, 25, 30, 50])


def _scenario(rows):
    return st.read_scenarios(pd.DataFrame([{"family": "rates", "status": "U", "source": "test", **r}
                                           for r in rows]))


def _gs_book(preset="nbfc_non_pd", **kw):
    ctx = fx.gsec_context(preset=preset, **kw)
    return rk.risk_book(fx.gs2036_positions(), ctx), ctx


# ---------------------------------------------------------------- the table


def test_the_shipped_table_reads_and_every_row_has_a_source_and_a_status():
    rows = st.read_scenarios()
    ids = st.scenarios_by_id(rows)
    for sid in ("IRRBB_INR_PARALLEL_UP", "IRRBB_INR_PARALLEL_DOWN", "IRRBB_INR_STEEPENER", "IRRBB_INR_FLATTENER",
                "IRRBB_INR_SHORT_UP", "IRRBB_INR_SHORT_DOWN", "YIELD_UP_100", "CS_RATING_LADDER", "FRTB_CSR",
                "DOWNGRADE_1_NOTCH", "FRB_INDEX_IRRBB", "REPO_UP_200_10D", "BIDASK_X3", "H2013_JUL_TIGHTENING",
                "H2013_TAPER", "H2018_SELLOFF_ILFS", "H2020_COVID_CREDIT", "H2022_HIKES"):
        assert sid in ids, sid
    assert all(r.source and r.status in st.STATUSES for r in rows)
    assert {r.status for r in ids["IRRBB_INR_PARALLEL_UP"]} == {"V"}
    raw = pd.read_csv(st.DEFAULT_SCENARIOS_CSV, dtype=str, keep_default_na=False)
    assert list(raw.columns) == list(st.SCENARIO_COLUMNS)


def test_a_scenario_not_yet_available_is_left_out_of_an_earlier_run():
    early = st.scenarios_by_id(st.read_scenarios(as_of="2019-12-31"))
    assert "H2018_SELLOFF_ILFS" in early and "H2020_COVID_CREDIT" not in early and "H2022_HIKES" not in early
    assert "IRRBB_INR_PARALLEL_UP" in early
    assert "IRRBB_INR_PARALLEL_UP" not in st.scenarios_by_id(st.read_scenarios(as_of="2015-06-30"))
    adopted = pd.DataFrame([{"scenario_id": "MINE", "family": "rates", "component": "all_rate_curves",
                             "shape": "parallel", "size_bp": 25, "effective_from": "2026-12-01", "status": "U",
                             "source": "the fund's policy"}])
    assert "MINE" not in st.scenarios_by_id(st.read_scenarios(adopted, as_of="2026-11-30"))
    assert "MINE" in st.scenarios_by_id(st.read_scenarios(adopted, as_of="2026-12-01"))


def test_a_user_table_replaces_a_shipped_scenario_by_id_and_adds_its_own():
    user = pd.DataFrame([{"scenario_id": "RATES_PAR_UP_100", "family": "rates", "component": "all_rate_curves",
                          "shape": "parallel", "size_bp": 150, "status": "U", "source": "mine"},
                         {"scenario_id": "MY_TWIST", "family": "rates", "component": "gsec_curve",
                          "shape": "key_rate", "size_bp": 30, "tenor_years": 10, "status": "U", "source": "mine"}])
    ids = st.scenarios_by_id(st.read_scenarios(extend=user))
    assert [r.size_bp for r in ids["RATES_PAR_UP_100"]] == [150.0] and "MY_TWIST" in ids
    assert "IRRBB_INR_SHORT_UP" in ids


@pytest.mark.parametrize("field,value,message", [
    ("component", "corp_spreads", "did you mean 'corp_spread'"),
    ("shape", "parralel", "did you mean 'parallel'"),
    ("family", "ratez", "did you mean 'rates'"),
    ("selector", "ratng_bucket=AA", "did you mean 'rating_bucket'"),
    ("status", "maybe", "status"),
    ("size_bp", "", "needs size_bp"),
])
def test_bad_rows_are_refused_naming_the_closest_known_value(field, value, message):
    row = {"scenario_id": "X", "family": "rates", "component": "all_rate_curves", "shape": "parallel",
           "size_bp": 10, "status": "U", "source": "test", field: value}
    if field == "selector":
        row.update(component="corp_spread", shape="add")
    with pytest.raises(ValueError, match=message):
        st.read_scenarios(pd.DataFrame([row]))


# ---------------------------------------------------------------- rate shapes and revaluation (T18, T19, T8, T21, T22)


def test_t18_irrbb_inr_shapes_and_the_standards_example():
    want = {"short": [499.6501, 469.7065, 441.2485, 389.4004, 303.2653, 208.431, 143.2524, 41.0425, 11.7589, 0.9652,
                      0.2765, 0.0019],
            "steepener": [-324.5836, -288.9508, -255.0857, -193.3865, -90.8857, 21.9671, 99.5296, 221.1594, 256.0069,
                          268.8514, 269.6709, 269.9978],
            "flattener": [399.5941, 364.8596, 331.8482, 271.7045, 171.7878, 61.78, -13.8272, -132.3907, -166.3597,
                          -178.8803, -179.6792, -179.9978]}
    for shape, values in want.items():
        assert st.irrbb_shift(shape, T18) == pytest.approx(values, abs=1e-4)
    assert st.irrbb_shift("parallel", T18) == pytest.approx([400.0] * 12)
    assert st.irrbb_shift("short", T18, sign=-1.0) == pytest.approx([-v for v in want["short"]], abs=1e-4)
    example = {"parallel": 0.0, "short": 100.0, "long": 100.0}
    assert float(st.irrbb_shift("steepener", 3.5, sizes=example)) == pytest.approx(25.3864, abs=1e-4)
    assert float(st.irrbb_shift("flattener", 3.5, sizes=example)) == pytest.approx(-1.6393, abs=1e-4)


@pytest.mark.parametrize("sid,pnl", [("IRRBB_INR_PARALLEL_UP", -22_56_44_876.70),
                                     ("IRRBB_INR_PARALLEL_DOWN", 31_60_65_411.65),
                                     ("IRRBB_INR_SHORT_UP", -4_73_66_398.24), ("IRRBB_INR_SHORT_DOWN", 5_00_07_400.68),
                                     ("IRRBB_INR_STEEPENER", -11_02_95_424.70),
                                     ("IRRBB_INR_FLATTENER", 6_71_80_774.62)])
def test_t19_irrbb_scenarios_on_gs2036(sid, pnl):
    book, ctx = _gs_book()
    rows = st.scenarios_by_id(st.read_scenarios())[sid]
    out = st.apply_scenario(book, rows, st.StressContext(risk=ctx))[0]
    assert out["pnl"] == pytest.approx(pnl, abs=0.01)
    assert out["pnl_rates"] == pytest.approx(pnl, abs=0.01) and abs(out["interaction"]) < 1e-6
    assert out["method"] == "full"


def test_t8_the_yield_mode_reproduces_the_price_at_the_yield_plus_one_percent():
    book, ctx = _gs_book()
    p = book.positions[0]
    out = st.apply_scenario(book, st.scenarios_by_id(st.read_scenarios())["YIELD_UP_100"],
                            st.StressContext(risk=ctx))[0]
    assert out["method"] == "yield"
    assert out["pnl"] == pytest.approx(1e7 * (fx.GS2036.dirty_price(p.yield_pct + 1.0, p.settlement) - p.dirty),
                                       rel=1e-12)
    assert out["pnl"] == pytest.approx(1e7 * (88.8709673644 - 95.0310895656), abs=1e-3)
    curve = st.apply_scenario(book, st.scenarios_by_id(st.read_scenarios())["RATES_PAR_UP_100"],
                              st.StressContext(risk=ctx))[0]
    assert curve["pnl"] == pytest.approx(1e7 * (88.6651451976 - 95.0310895656), abs=1e-3)  # the curve mode


def test_t21_key_rate_points_joined_by_the_triangles():
    book, ctx = _gs_book()
    rows = _scenario([{"scenario_id": "T21", "component": "all_rate_curves", "shape": "key_rate", "size_bp": b,
                       "tenor_years": t} for t, b in ((0.25, 250), (5, 80), (10, 74), (15, 46), (30, 34))])
    assert float(st._rate_shift_bp(rows, np.array([9.48]))[0]) == pytest.approx(74.6240, abs=1e-4)
    assert float(st._rate_shift_bp(rows, np.array([60.0]))[0]) == pytest.approx(34.0)  # flat beyond the last
    assert st.apply_scenario(book, rows, st.StressContext(risk=ctx))[0]["pnl"] == pytest.approx(-5_29_08_651.12,
                                                                                               abs=0.01)


def test_t22_a_payer_swap_under_the_irrbb_shocks():
    from test_risk import _swap_book
    book, ctx, _ = _swap_book()
    ids = st.scenarios_by_id(st.read_scenarios())
    sctx = st.StressContext(risk=ctx)
    assert st.apply_scenario(book, ids["IRRBB_INR_PARALLEL_UP"], sctx)[0]["pnl"] == pytest.approx(
        13_38_42_034.38, abs=0.01)
    assert st.apply_scenario(book, ids["IRRBB_INR_SHORT_UP"], sctx)[0]["pnl"] == pytest.approx(
        6_72_65_995.01, abs=0.01)
    assert st.apply_scenario(book, ids["GSEC_OIS_BASIS_25"], sctx)[0]["pnl"] == 0.0  # the G-sec curve only


def test_a_down_shock_is_floored_at_zero():
    low = fx.nss(fx.S16, (2.0, -1.0, 0.0, 0.0, 1.5, 8.0))  # zero rates 1% to 2%
    entries = {fx.D13: rt.CurveEntry(low, fx.at(fx.D13))}
    ctx = fx.gsec_context(entries=entries)
    book = rk.risk_book(fx.gs2036_positions(curve=low), ctx)
    p = book.positions[0]
    rows = st.scenarios_by_id(st.read_scenarios())["IRRBB_INR_PARALLEL_DOWN"]
    out = st.apply_scenario(book, rows, st.StressContext(risk=ctx))[0]
    floored = p.units * float(p.amounts @ np.ones_like(p.amounts)) - p.units * p.dirty  # every rate at 0%: par sum
    assert out["pnl"] == pytest.approx(floored, rel=1e-9)
    shift = st.scenario_shift(rows, "gsec", p.curve)(p.times)
    z = -np.log(p.discount) / p.times
    assert np.all(z + shift >= -1e-12)


# ---------------------------------------------------------------- spreads, FRBs, interaction


def _corp_book(rating="AA", sector="financials", tier="", preset="nbfc_non_pd", extra=None):
    terms = [{"isin": fx.NCD_ISIN, "issuer_id": "ISS1", "kind": "corporate", "coupon_pct": "9.0", "frequency": "1",
              "issue_date": "2023-12-15", "maturity_date": "2030-12-15", "rating_bucket": rating, "sector": sector,
              "tier": tier}]
    s = date(2026, 11, 23)
    curve = fx.nss(s)
    market = rt.MarketData(corporate_terms=pd.DataFrame(terms),
                           curves={"gsec": {date(2026, 11, 20): rt.CurveEntry(curve, fx.at(date(2026, 11, 20)))}})
    settings = rt.EngineSettings(access=rt.ACCESS_PRESETS[preset])
    bond = rt.instruments_as_known(market, settings, [fx.NCD_ISIN], datetime(2026, 11, 20, 20))[fx.NCD_ISIN].bond
    y = fx.on_curve_yield(bond, curve, s, z=0.015)
    row = fx.bond_row(bond, fx.NCD_ISIN, "corporate", 1e8, date(2026, 11, 20), s, y, book="c", **(extra or {}))
    ctx = rk.RiskContext(market=market, settings=settings, risk=rk.RiskSettings(), as_of=datetime(2026, 11, 20, 20))
    return rk.risk_book(pd.DataFrame([row]), ctx), ctx


def _z_pnl(p, dz_bp):
    return p.units * (float(p.amounts @ (p.discount * np.exp(-(p.z_spread + dz_bp * 1e-4) * p.times)))
                      - float(p.amounts @ (p.discount * np.exp(-p.z_spread * p.times))))


def test_the_credit_ladder_financials_and_frtb_shocks_by_rating_and_sector():
    book, ctx = _corp_book("AA", "financials")
    p = book.positions[0]
    sctx = st.StressContext(risk=ctx)
    ids = st.scenarios_by_id(st.read_scenarios())
    ladder = st.apply_scenario(book, ids["CS_RATING_LADDER"], sctx)[0]
    assert ladder["pnl_spread"] == pytest.approx(_z_pnl(p, 100.0), rel=1e-12) and ladder["pnl_rates"] == 0.0
    assert st.apply_scenario(book, ids["CS_FINANCIALS_X2"], sctx)[0]["pnl"] == pytest.approx(_z_pnl(p, 200.0))
    assert st.apply_scenario(book, ids["FRTB_CSR"], sctx)[0]["pnl"] == pytest.approx(_z_pnl(p, 500.0))
    other, ctx2 = _corp_book("A", "")  # a sector the issuer table does not name: FRTB's 'other', 1,200 bp
    q = other.positions[0]
    assert st.apply_scenario(other, ids["FRTB_CSR"], st.StressContext(risk=ctx2))[0]["pnl"] == pytest.approx(
        _z_pnl(q, 1200.0))
    assert st.apply_scenario(other, ids["CS_RATING_LADDER"], st.StressContext(risk=ctx2))[0]["pnl"] == \
        pytest.approx(_z_pnl(q, 200.0))


def test_a_downgrade_through_the_ladder_or_a_peer_matrix():
    book, ctx = _corp_book("AA-", "industrials")
    p = book.positions[0]
    rows = st.scenarios_by_id(st.read_scenarios())["DOWNGRADE_1_NOTCH"]
    out = st.apply_scenario(book, rows, st.StressContext(risk=ctx))[0]
    assert out["pnl"] == pytest.approx(_z_pnl(p, 200.0 - 150.0))  # AA- to A+ on the ladder
    matrix = pd.DataFrame([{"rating_bucket": b, "tenor_years": t, "spread_bp": s}
                           for b, s in (("AA-", 180.0), ("A+", 260.0)) for t in (1.0, 10.0)])
    from dataclasses import replace
    ctx_m = replace(ctx, spread_matrix=matrix)
    out_m = st.apply_scenario(book, rows, st.StressContext(risk=ctx_m))[0]
    assert out_m["pnl"] == pytest.approx(_z_pnl(p, 80.0))
    forced = st.scenarios_by_id(st.read_scenarios())["DOWNGRADE_2_NOTCH_FORCED"]
    two = st.apply_scenario(book, forced, st.StressContext(risk=ctx))[0]
    assert two["pnl"] == pytest.approx(_z_pnl(p, (200.0 - 150.0) * 1.5))  # AA- to A: the ladder's step, x 1.5


def test_an_frb_under_index_and_margin_shocks_matches_its_dv01s_to_first_order():
    st_ = rt.EngineSettings(access=rt.ACCESS_PRESETS["nbfc_non_pd"], frb_allow_fewer=True, frb_allow_stale=True)
    as_of = datetime(2026, 11, 20, 20)
    bond = rt.instruments_as_known(rt.MarketData(), st_, [fx.FRB2031], as_of)[fx.FRB2031].bond
    s = date(2026, 11, 23)
    y = bond.yield_from_price(100.25, s)
    ctx = rk.RiskContext(market=rt.MarketData(), settings=st_, risk=rk.RiskSettings(), as_of=as_of)
    book = rk.risk_book(pd.DataFrame([fx.bond_row(bond, fx.FRB2031, "frb", 1e8, date(2026, 11, 20), s, y)]), ctx)
    p = book.positions[0]
    rows = _scenario([{"scenario_id": "I1", "family": "frb", "component": "frb_index", "shape": "add",
                       "size_bp": 1.0},
                      {"scenario_id": "D1", "family": "frb", "component": "frb_dm", "shape": "add", "size_bp": 1.0}])
    ids = st.scenarios_by_id(rows)
    sctx = st.StressContext(risk=ctx)
    assert st.apply_scenario(book, ids["I1"], sctx)[0]["pnl_frb"] == pytest.approx(-p.frb_index_dv01, rel=1e-3)
    assert st.apply_scenario(book, ids["D1"], sctx)[0]["pnl_frb"] == pytest.approx(-p.frb_spread_dv01, rel=1e-3)
    irrbb = st.apply_scenario(book, st.scenarios_by_id(st.read_scenarios())["IRRBB_INR_SHORT_UP"], sctx)[0]
    t_reset = (p.next_reset - p.settlement).days / 365
    shock = float(st.irrbb_shift("short", t_reset))
    exact = p.units * (rk._frb_dirty(bond, s, y, shock, 0.0) - rk._frb_dirty(bond, s, y, 0.0, 0.0))
    assert irrbb["pnl_rates"] == pytest.approx(exact, rel=1e-12)  # the curve's shift at its next reset
    assert irrbb["pnl_rates"] == pytest.approx(-p.frb_index_dv01 * shock, rel=3e-2)  # first order, less convexity


def test_total_components_and_interaction_add_up():
    book, ctx = _corp_book("AA", "financials")
    rows = st.scenarios_by_id(st.read_scenarios())["H2018_SELLOFF_ILFS"]
    out = st.apply_scenario(book, rows, st.StressContext(risk=ctx))[0]
    parts = out["pnl_rates"] + out["pnl_spread"] + out["pnl_frb"] + out["pnl_funding"] + out["pnl_liquidity"]
    assert out["pnl"] == pytest.approx(parts + out["interaction"], abs=1e-9)
    assert out["interaction"] != 0.0  # rates and spreads compound in a full revaluation
    assert out["pnl_spread"] == pytest.approx(_z_pnl(book.positions[0], 150.0))  # AA financial corporate +150 bp
    stress, positions = st.run_stress(book, rows, st.StressContext(risk=ctx))
    fund = stress[stress["scope_type"] == "fund"].set_index("component")["pnl"]
    assert fund["total"] == pytest.approx(out["pnl"])
    assert set(fund.index) == {"total", "rates", "spread", "funding", "interaction"}


# ---------------------------------------------------------------- funding (T23) and the legal forms


def _funding_frame(day, borrowed, segment="triparty", book="b"):
    return pd.DataFrame([{"date": day, "book": book, "segment": segment, "borrowed": borrowed, "rate_pct": 5.65,
                          "limit": borrowed * 1.1, "haircut_equity": 1e6, "margin": 2e5, "idle_cash": 0.0}])


def test_t23_funding_stress_under_a_preset_that_borrows():
    book, ctx = _gs_book("nbfc_non_pd")
    from dataclasses import replace
    ctx = replace(ctx, funding=_funding_frame(fx.D13, 158.55e7),
                  market=replace(ctx.market, haircuts=pd.DataFrame([{"effective_date": "2015-01-01", "kind": "gsec",
                                                                     "haircut_pct": 3.0, "source": "synthetic"}])))
    rows = st.read_scenarios()
    out = st.funding_liquidity(None, None, book, ctx, rows)
    by = out.set_index("scenario_id")
    assert by.loc["REPO_UP_200_10D", "extra_interest"] == pytest.approx(8_68_767.12, abs=0.01)
    p = book.positions[0]
    assert by.loc["HAIRCUT_UP_2PP", "extra_equity"] == pytest.approx(0.02 * p.market_value, rel=1e-12)
    assert 0.02 * 210e7 * 0.97 == pytest.approx(4_07_40_000)  # the spec's T23: Rs 210 crore face at 97
    assert by.loc["HAIRCUT_X2", "extra_equity"] == pytest.approx(0.03 * p.market_value, rel=1e-12)
    assert by.loc["NO_MARKET_REPO", "shortfall"] == 0.0  # nothing in market repo
    base = by.loc[""]
    assert bool(base["applicable"]) and base["headroom"] == pytest.approx(15.855e7)
    assert 0 <= base["alm_1_7d"] <= 1


def test_funding_is_not_applicable_under_an_unregistered_company():
    book, ctx = _gs_book("unregulated_company")
    out = st.funding_liquidity(None, None, book, ctx, st.read_scenarios())
    assert len(out) == 1 and not bool(out["applicable"].iloc[0])
    assert "not_applicable: unregulated_company does not borrow" in out["notes"].iloc[0]
    assert math.isnan(out["alm_1_7d"].iloc[0])
    stress, _ = st.run_stress(book, st.read_scenarios(), st.StressContext(risk=ctx))
    repo = stress[(stress["scenario_id"] == "REPO_UP_200_10D") & (stress["scope_type"] == "fund")]
    assert repo["pnl"].isna().all() and repo["flags"].str.contains("not_applicable").all()
    hist = stress[(stress["scenario_id"] == "H2013_JUL_TIGHTENING") & (stress["scope_type"] == "fund")]
    comp = hist.set_index("component")
    assert math.isfinite(comp.loc["total", "pnl"]) and math.isnan(comp.loc["funding", "pnl"])


def test_repo_rate_stress_charges_each_long_its_funded_share():
    book, ctx = _gs_book("nbfc_non_pd")
    pos = fx.gs2036_positions(funded=158.55e7)
    book = rk.risk_book(pos, ctx)
    out = st.apply_scenario(book, st.scenarios_by_id(st.read_scenarios())["REPO_UP_200_10D"],
                            st.StressContext(risk=ctx))[0]
    assert out["pnl_funding"] == pytest.approx(-8_68_767.12, abs=0.01) and out["pnl"] == out["pnl_funding"]


# ---------------------------------------------------------------- liquidity (T24, T25)


def test_t24_days_to_liquidate_from_the_users_volumes():
    book, ctx = _gs_book()
    from dataclasses import replace
    for face, adv, days in ((50e7, 10.0, 25.0), (500e7, 2000.0, 1.25), (25e7, 0.5, 250.0)):
        b = rk.risk_book(fx.gs2036_positions(face=face), ctx)
        table = pd.DataFrame([{"date": "2026-11-01", "isin": "GS2036", "adv_cr": adv, "source": "test"}])
        detail, _ = st.liquidity(b, replace(ctx, adv=table))
        assert detail["days_to_liquidate"].iloc[0] == pytest.approx(days)
        assert detail["adv_source"].iloc[0] == "table"
    late = pd.DataFrame([{"date": "2026-11-01", "isin": "GS2036", "adv_cr": 1.0, "source": "x",
                          "public_from": "2026-11-14T09:00"}])  # public after as_of: the proxy instead
    detail, _ = st.liquidity(book, replace(ctx, adv=late))
    assert detail["adv_source"].iloc[0] == "proxy_class" and detail["adv"].iloc[0] == 1000e7


def test_t25_exit_cost_at_the_cost_tables_half_spread_and_stressed():
    book, ctx = _gs_book()
    detail, summary = st.liquidity(book, ctx)
    row = detail.iloc[0]
    assert row["half_spread_bp"] == 0.625 and row["liquidation_cost"] == pytest.approx(4_01_768.08, abs=0.01)
    assert row["stressed_cost"] == pytest.approx(12_05_304.24, abs=0.01)
    illiquid = rk.risk_book(fx.gs2036_positions(liquidity="illiquid"), ctx)
    d2, _ = st.liquidity(illiquid, ctx)
    assert d2["half_spread_bp"].iloc[0] == 4.5 and d2["liquidation_cost"].iloc[0] == pytest.approx(28_92_730.17,
                                                                                                    abs=0.01)
    assert d2["stressed_cost"].iloc[0] == pytest.approx(86_78_190.51, abs=0.01) and bool(d2["illiquid"].iloc[0])
    assert summary["lvar"].isna().all()  # no VaR given: no liquidity-adjusted VaR
    _, s2 = st.liquidity(book, ctx, var_by_scope={("fund", "fund"): 1e6})
    assert s2[s2["scope_type"] == "fund"]["lvar"].iloc[0] == pytest.approx(1e6 + 12_05_304.24, abs=0.01)


def test_liquidity_shares_from_the_marks_volumes_and_the_proxies():
    day = fx.D13
    days = fx.CAL.business_days(date(2026, 10, 14), day)[-20:]
    marks = pd.DataFrame([{"date": d, "isin": "GS2036", "clean_price": 97.0, "source": "traded", "volume_cr": 200.0,
                           "public_from": fx.at(d, 18).isoformat()} for d in days[::2]])  # traded every other day
    from dataclasses import replace
    _, ctx = _gs_book()
    ctx = replace(ctx, market=replace(ctx.market, marks=marks),
                  nav=pd.DataFrame([{"date": day, "book": "b", "nav": 5e8}]))
    book = rk.risk_book(fx.gs2036_positions(), ctx)  # the NAV of the book as of its date
    detail, summary = st.liquidity(book, ctx)
    assert detail["adv_source"].iloc[0] == "marks" and detail["adv"].iloc[0] == pytest.approx(100e7)  # 10 of 20
    assert detail["days_to_liquidate"].iloc[0] == pytest.approx(5.0)
    p = book.positions[0]
    fund = summary[summary["scope_type"] == "fund"].iloc[0]
    assert fund["share_1d"] == pytest.approx(0.2 * abs(p.market_value) / 5e8)
    assert fund["share_5d"] == pytest.approx(abs(p.market_value) / 5e8)
    assert fund["max_days"] == pytest.approx(5.0) and fund["illiquid_share"] == 0.0
    cp = DiscountInstrument(date(2027, 2, 15), CP)
    s = fx.CAL.settlement_date(day, 1)
    price = cp.price(7.0, s)
    row = {**fx.bond_row(fx.GS2036, "CP27", "cp", 25e7, day, s, 7.0, liquidity=""), "clean_mark": price,
           "accrued": 0.0, "value": 25e7 / 100 * price, "dv01": 25e7 / 100 * cp.dv01(7.0, s)}
    master = pd.DataFrame([{"isin": "CP27", "kind": "cp", "maturity_date": "2027-02-15"}])
    cbook = rk.risk_book(pd.DataFrame([row]), replace(ctx, market=rt.MarketData(master=master)))
    d3, _ = st.liquidity(cbook, ctx)
    assert d3["adv"].iloc[0] == 25e7 and "proxy_adv" in d3["flags"].iloc[0] and d3["days_to_liquidate"].iloc[0] == 5.0


def test_stress_worst_picks_the_lowest_total_of_each_family():
    book, ctx = _gs_book()
    stress, _ = st.run_stress(book, st.read_scenarios(), st.StressContext(risk=ctx))
    worst = st.stress_worst(stress)
    fund = worst[worst["scope_type"] == "fund"].set_index("family")
    assert fund.loc["rates_irrbb", "scenario_id"] == "IRRBB_INR_PARALLEL_UP"
    assert fund.loc["rates_irrbb", "pnl"] == pytest.approx(-22_56_44_876.70, abs=0.01)
    assert list(worst.columns) == list(st.STRESS_WORST_COLUMNS)
