"""Tests of the risk layer's measurement core (risk.py) and the read-only accessors it added to returns.py: the risk
specification's vectors (T1-T17, its section 15) and identities (I1-I4, I6, I11, I12), every aggregation, look-ahead,
determinism and refusals. Synthetic data only: bonds priced off known NSS curves, the tests' B3 OIS quotes, factor
histories with known changes (risk_fixtures)."""

import math
import re
from datetime import date, datetime
from fractions import Fraction
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import risk_fixtures as fx

from cr_strat import returns as rt
from cr_strat import risk as rk
from cr_strat.bonds import FixedRateBond
from cr_strat.conventions import SDL

# ---------------------------------------------------------------- key rates, curve and yield DV01 (T1-T4, I1, I2)

T1_CENTRAL = {"0.25y": 1.1589e-05, "0.5y": 0.0001599381, "1y": 0.0005622418, "2y": 0.0012150937,
              "3y": 0.0027431934, "5y": 0.0047969521, "7y": 0.0150417332, "10y": 0.0418423757}


def test_triangles_sum_to_one_and_are_flat_beyond_the_ends():
    t = np.array([0.0, 0.1, 0.25, 0.6, 9.48, 14.0, 50.0, 80.0])
    w = rk.key_weights(t, rk.KEY_TENORS)
    assert np.allclose(w.sum(axis=0), 1.0, atol=1e-15)
    assert w[0, 0] == 1.0 and w[0, 1] == 1.0  # flat below 0.25 years
    assert w[-1, -1] == 1.0 and w[-1, -2] == 1.0  # flat beyond 50 years
    k10, k15 = rk.KEY_TENORS.index(10.0), rk.KEY_TENORS.index(15.0)
    assert w[k10, 5] == pytest.approx(0.2) and w[k15, 5] == pytest.approx(0.8)  # a 14-year flow: 20% / 80%


def test_t1_key_rates_of_gs2036_analytic_against_the_central_vector():
    curve = fx.nss(fx.S16)
    krd = rk.zero_krd(fx.GS2036, fx.S16, curve, 0.0, rk.KEY_TENORS)
    for label, want in T1_CENTRAL.items():
        assert krd[label] == pytest.approx(want, abs=1.1e-9)  # the spec: analytic and central agree to 1.04e-9
    assert all(abs(krd[k]) < 1e-15 for k in ("15y", "20y", "30y", "40y", "50y"))
    t, a = rt._flows(fx.GS2036, fx.S16)
    pv = a * curve.discount_t(t)
    assert sum(krd.values()) == pytest.approx(float((pv * t).sum()) * 1e-4, rel=1e-14)  # I1 exact
    assert sum(krd.values()) == pytest.approx(0.066373117944, abs=3e-9)  # the central parallel (third order)


@pytest.mark.parametrize("bond,analytic,yield_dv01", [(fx.GS2054, 0.104785188221, 0.102777090547),
                                                      (fx.GS2031, 0.039194892555, 0.037888867990)])
def test_t2_t3_key_rate_sums_and_the_compounding_basis(bond, analytic, yield_dv01):
    curve = fx.nss(fx.S16)
    krd = rk.zero_krd(bond, fx.S16, curve, 0.0, rk.KEY_TENORS)
    assert sum(krd.values()) == pytest.approx(analytic, abs=1e-11)
    y = fx.on_curve_yield(bond, curve, fx.S16)
    assert bond.dv01(y, fx.S16) == pytest.approx(yield_dv01, abs=1e-11)
    assert sum(krd.values()) > bond.dv01(y, fx.S16)  # the curve DV01 exceeds the yield DV01


def test_t1_through_the_book_matches_the_engine_dv01_and_the_basis():
    ctx = fx.gsec_context()
    book = rk.risk_book(fx.gs2036_positions(), ctx)
    p = book.positions[0]
    assert p.dirty == pytest.approx(95.0310895656, abs=1e-9)
    assert p.yield_pct == pytest.approx(7.7020997818, abs=1e-9)
    assert abs(p.z_spread) < 1e-12
    assert p.dv01_yield == pytest.approx(0.064282892630 * 1e7, rel=1e-11)
    assert p.dv01_yield == pytest.approx(p.engine_dv01, rel=1e-14)  # equal to the engine's where both exist
    assert p.dv01_curve / p.dv01_yield == pytest.approx(1.0325160432, abs=5e-8)  # analytic against central
    assert p.convexity_yield == pytest.approx(1e7 * 58.2446397648 * 95.0310895656 * 1e-8, rel=1e-9)
    sens = rk.sensitivities(book)
    assert set(sens["factor"]) == {f"gsec:{k}" for k in T1_CENTRAL}
    assert sens["sensitivity"].sum() == pytest.approx(p.dv01_curve, rel=1e-14)
    assert set(sens["method"]) == {"analytic"} and set(sens["basis"]) == {"zero"}
    assert p.flags == ()


def test_t4_cs01_on_the_z_spread_equals_the_curve_dv01_for_fixed_flows():
    curve = fx.nss(fx.S16)
    bond = FixedRateBond(8.95, date(2030, 12, 14))
    t, a = rt._flows(bond, fx.S16)
    dirty = float(a @ (curve.discount_t(t) * np.exp(-0.01 * t)))
    from cr_strat.asset_swaps import z_spread
    z = z_spread([f.date for f in bond.cash_flows(fx.S16)], a, fx.S16, dirty, curve)
    assert z * 1e4 == pytest.approx(100.0, abs=1e-9)
    cs01 = rk._value_on(curve, t, a, z - 0.5e-4) - rk._value_on(curve, t, a, z + 0.5e-4)
    assert cs01 == pytest.approx(0.036147101087, abs=1e-11)
    krd = sum(rk.zero_krd(bond, fx.S16, curve, z, rk.KEY_TENORS).values())
    assert cs01 == pytest.approx(krd, rel=1e-8)  # I2


# ---------------------------------------------------------------- OIS (T5), FRB (T6), JTD (T7)


def test_t5_swap_zero_and_par_key_rates():
    from cr_strat import ois
    curve = fx.b3_curve()
    sw = ois.OisSwap.spot(date(2026, 11, 19), "4Y", 6.134480, 1e9, pay_fixed=True, calendar=fx.LEN)
    v = ois.ois_npv(curve, sw, calendar=fx.LEN)
    assert v.npv == pytest.approx(-508_077.515121, abs=1e-5)
    zero = rk.swap_krd(sw, curve, (0.25, 0.5, 1, 2, 3, 5), calendar=fx.LEN)
    want = {"0.25y": 265.85191, "0.5y": -1_466.2777, "1y": -5_032.604, "2y": -10_857.186509,
            "3y": -1_78_735.944186, "5y": -1_65_171.4234}
    for k, w in want.items():
        assert zero[k] == pytest.approx(w, abs=1e-4)
    assert sum(zero.values()) == pytest.approx(-3_60_997.583885, abs=1e-5)
    assert sum(zero.values()) - v.dv01 == pytest.approx(-69.47, abs=0.01)  # one-sided against central
    up = rk.swap_par_krd(sw, curve, calendar=fx.LEN, differencing="up")
    assert up["4Y"] == pytest.approx(-3_50_893.646499, abs=1e-5)
    assert sum(up.values()) == pytest.approx(-3_50_963.844322, abs=1e-5)
    central = rk.swap_par_krd(sw, curve, calendar=fx.LEN)
    assert sum(central.values()) == pytest.approx(sum(up.values()), rel=2e-4)
    assert rk.swap_par_krd(sw, rt.rolled(curve, date(2026, 11, 20)), calendar=fx.LEN) == {}  # no quotes kept


def _swap_book(**kw):
    from cr_strat import ois
    curve = fx.b3_curve()
    sw = ois.OisSwap.spot(date(2026, 11, 19), "4Y", 6.134480, 1e9, pay_fixed=True, calendar=fx.LEN)
    v = ois.ois_npv(curve, sw, calendar=fx.LEN)
    pos = pd.DataFrame([{"date": date(2026, 11, 19), "book": "s", "isin": "OIS:S1", "kind": "ois", "state": "ois",
                         "face": -1e9, "settlement_date": sw.effective, "clean_mark": 6.134480,
                         "mark_source": "curve", "mark_age_days": 0, "yield_pct": v.fair_rate_pct, "value": v.npv,
                         "dv01": v.dv01}])
    market = kw.pop("market", None) or rt.MarketData(ois_curves={date(2026, 11, 19): rt.CurveEntry(curve, fx.at(
        date(2026, 11, 19)))})
    ctx = rk.RiskContext(market=market, settings=rt.EngineSettings(access=rt.ACCESS_PRESETS["nbfc_non_pd"],
                                                                   project_calendar=True),
                         risk=kw.pop("risk", rk.RiskSettings()), as_of=fx.at(date(2026, 11, 19), 20),
                         swaps=pd.DataFrame([{"trade_id": "S1", "months": 48, "pay_fixed": True}]), **kw)
    return rk.risk_book(pos, ctx), ctx, v


def test_t5_a_swap_row_through_the_book():
    book, _, v = _swap_book()
    s = book.swaps[0]
    assert s.value == pytest.approx(v.npv, abs=1e-6) and "value_differs" not in s.flags
    assert s.dv01_curve == pytest.approx(-3_60_997.583885, abs=1e-5)
    assert s.engine_dv01 == pytest.approx(v.dv01, abs=1e-9)
    pr = rk.position_risk(book).iloc[0]
    assert pr["kind"] == "ois" and pr["face"] == -1e9 and pr["dv01_curve"] == pytest.approx(s.dv01_curve)
    keys = rk.key_rate_table(book)
    fund = keys[keys["scope_type"] == "fund"]
    assert set(fund["curve"]) == {"ois", "ois_par"}
    assert fund[fund["curve"] == "ois"]["krd"].sum() == pytest.approx(s.dv01_curve, abs=1e-6)
    assert set(fund[fund["curve"] == "ois"]["tenor"]) <= {0.25, 0.5, 1.0, 2.0, 3.0, 5.0}  # to the curve's 5 years


def test_a_swap_without_its_terms_or_its_curve_is_refused():
    book, ctx, v = _swap_book()
    pos = pd.DataFrame([{"date": date(2026, 11, 19), "book": "s", "isin": "OIS:S9", "kind": "ois", "state": "ois",
                         "face": -1e9, "settlement_date": "2026-11-20", "clean_mark": 6.13, "yield_pct": 6.1,
                         "value": 0.0, "dv01": 0.0}])
    with pytest.raises(ValueError, match="no swap terms for trade S9"):
        rk.risk_book(pos, ctx)
    from dataclasses import replace
    empty = replace(ctx, market=rt.MarketData())
    with pytest.raises(ValueError, match="no OIS curve"):
        rk.risk_book(rk.position_risk(book).assign(isin="OIS:S1", clean_mark=6.134480, dv01=v.dv01), empty)


def test_t6_frb_index_and_spread_dv01_at_its_next_reset():
    st = rt.EngineSettings(access=rt.ACCESS_PRESETS["nbfc_non_pd"], frb_allow_fewer=True, frb_allow_stale=True)
    as_of = datetime(2026, 11, 20, 20)
    bond = rt.instruments_as_known(rt.MarketData(), st, [fx.FRB2031], as_of)[fx.FRB2031].bond
    s = date(2026, 11, 23)
    y = bond.yield_from_price(100.25, s)
    assert y == pytest.approx(6.4602032847, abs=1e-9)
    from cr_strat import floating as fl
    row = fx.bond_row(bond, fx.FRB2031, "frb", 1e8, date(2026, 11, 20), s, y,
                      dv01=1e6 * fl.index_dv01(bond, s, 0.0, index_pct=y))
    ctx = rk.RiskContext(market=rt.MarketData(), settings=st, risk=rk.RiskSettings(), as_of=as_of)
    p = rk.risk_book(pd.DataFrame([row]), ctx).positions[0]
    assert p.frb_index_dv01 / 1e6 == pytest.approx(0.0004527838, abs=1e-10)
    assert p.frb_spread_dv01 / 1e6 == pytest.approx(0.0425031888, abs=1e-10)
    assert p.frb_index_dv01 == pytest.approx(452.7838, abs=1e-4) and p.next_reset == date(2026, 12, 7)
    assert p.dv01_yield == p.frb_index_dv01 and p.tenor_bucket == "0-1y"
    by = {sn.factor: sn for sn in p.sens}
    assert by["gsec:0.25y"].value == pytest.approx(p.frb_index_dv01) and by["gsec:0.25y"].method == "frb_index"
    assert by[f"frb_dm:{fx.FRB2031}"].value == pytest.approx(p.frb_spread_dv01)
    assert p.frb_spread_dv01 / p.frb_index_dv01 == pytest.approx(93.87, abs=0.01)  # an FRB is a spread position


def _corporate_book(rows, terms, *, ratings=None, issuers=None, seniority=None, preset="nbfc_non_pd"):
    market = rt.MarketData(corporate_terms=pd.DataFrame(terms), ratings=ratings)
    st = rt.EngineSettings(access=rt.ACCESS_PRESETS[preset])
    ctx = rk.RiskContext(market=market, settings=st, risk=rk.RiskSettings(), as_of=datetime(2026, 11, 20, 20),
                         issuers=issuers, seniority=seniority)
    return rk.risk_book(pd.DataFrame(rows), ctx), ctx


NCD_TERMS = [{"isin": "INE000A07011", "issuer_id": "ISS1", "kind": "corporate", "coupon_pct": "9.0",
              "frequency": "1", "issue_date": "2023-12-15", "maturity_date": "2030-12-15", "rating_bucket": "AA",
              "sector": "financials"},
             {"isin": "INE000B08022", "issuer_id": "ISS2", "kind": "corporate", "coupon_pct": "8.0",
              "frequency": "1", "issue_date": "2022-01-10", "maturity_date": "2032-01-10", "tier": "tier2",
              "rating_bucket": "AA+"}]


def _ncd_rows(values=((1e8, 1.02e8), (5e7, 4.8e7)), book="c"):
    st = rt.EngineSettings(access=rt.ACCESS_PRESETS["nbfc_non_pd"])
    insts = rt.instruments_as_known(rt.MarketData(corporate_terms=pd.DataFrame(NCD_TERMS)), st,
                                    [t["isin"] for t in NCD_TERMS], datetime(2026, 11, 20, 20))
    s = date(2026, 11, 23)
    rows = []
    for t, (face, mv) in zip(NCD_TERMS, values):
        bond = insts[t["isin"]].bond
        y = bond.yield_from_dirty_price(abs(mv / face) * 100, s)
        row = fx.bond_row(bond, t["isin"], "corporate", face, date(2026, 11, 20), s, y, book=book,
                          source="valuation")
        row["value"] = mv
        rows.append(row)
    return rows


def test_t7_jump_to_default_with_basel_lgds():
    book, _ = _corporate_book(_ncd_rows(), NCD_TERMS)
    by = {p.isin: p for p in book.positions}
    assert by["INE000A07011"].jtd == pytest.approx(7.70e7, abs=1e-3) and by["INE000A07011"].lgd == 0.75
    assert by["INE000B08022"].jtd == pytest.approx(4.80e7, abs=1e-3) and by["INE000B08022"].lgd == 1.0
    assert by["INE000B08022"].seniority == "subordinated"
    jtd = rk.jtd_table(book)
    fund = jtd[jtd["scope_type"] == "fund"].set_index("issuer_id")
    assert fund.loc["ISS1", "jtd"] == pytest.approx(7.70e7) and fund.loc["ISS2", "jtd"] == pytest.approx(4.80e7)


def test_jtd_of_a_short_is_negative_and_never_netted():
    short = rk._jtd(-1e8, -1.02e8, 0.75)
    assert short == pytest.approx(min(0.75 * -1e8 + (-1.02e8 + 1e8), 0.0))
    assert rk._jtd(1e8, 0.2e8, 0.25) == 0.0  # a long whose loss is already taken: floored at zero


def test_seniority_rating_and_group_come_from_the_users_tables_as_known():
    issuers = pd.DataFrame([{"issuer_id": "ISS1", "group_id": "GRP", "sector": "financials",
                             "effective_from": "2020-01-01"},
                            {"issuer_id": "ISS2", "group_id": "GRP", "sector": "financials",
                             "seniority": "covered", "effective_from": "2020-01-01"}])
    ratings = pd.DataFrame([{"agency": "CRISIL", "rating": "CRISIL AA-", "public_from": "2026-11-01T10:00",
                             "issuer_id": "ISS1"},
                            {"agency": "ICRA", "rating": "[ICRA]A+", "public_from": "2026-11-21T10:00",
                             "issuer_id": "ISS1"}])  # public after as_of: not used
    book, _ = _corporate_book(_ncd_rows(), NCD_TERMS, ratings=ratings, issuers=issuers)
    by = {p.isin: p for p in book.positions}
    assert by["INE000A07011"].rating_bucket == "AA-" and by["INE000A07011"].rating_source == "ratings"
    assert by["INE000B08022"].rating_bucket == "AA+" and "static_rating" in by["INE000B08022"].flags
    assert by["INE000A07011"].group_id == "GRP" and "no_group" not in by["INE000A07011"].flags
    assert by["INE000B08022"].seniority == "subordinated"  # the terms' tier beats the issuer's default
    credit = rk.credit_table(book)
    grp = credit[(credit["scope_type"] == "fund") & (credit["dimension"] == "group")]
    assert list(grp["key"]) == ["GRP"] and grp["jtd"].iloc[0] == pytest.approx(7.70e7 + 4.80e7)
    with pytest.raises(ValueError, match="two rows in force"):
        rk.read_issuers(pd.DataFrame([{"issuer_id": "A", "effective_from": "2020-01-01"},
                                      {"issuer_id": "A", "effective_from": "2021-01-01",
                                       "effective_to": "2022-01-01"}]))


def test_a_corporate_bond_on_the_gsec_curve_has_a_cs01_on_its_rating_tenor_factor():
    s = date(2026, 11, 23)
    curve = fx.nss(s)
    rows = _ncd_rows()
    market = rt.MarketData(corporate_terms=pd.DataFrame(NCD_TERMS),
                           curves={"gsec": {date(2026, 11, 20): rt.CurveEntry(curve, fx.at(date(2026, 11, 20)))}})
    ctx = rk.RiskContext(market=market, settings=rt.EngineSettings(access=rt.ACCESS_PRESETS["nbfc_non_pd"]),
                         risk=rk.RiskSettings(), as_of=datetime(2026, 11, 20, 20))
    book = rk.risk_book(pd.DataFrame(rows), ctx)
    p = {x.isin: x for x in book.positions}["INE000A07011"]
    assert p.reference == "gsec" and p.z_spread * 1e4 > 50
    assert p.cs01 == pytest.approx(p.dv01_curve, rel=1e-8)  # I2 on a corporate bond's own flows
    spread = [sn for sn in p.sens if sn.basis == "spread"]
    assert [sn.factor for sn in spread] == ["corp:AA:3-5y"] and spread[0].value == pytest.approx(p.cs01)
    credit = rk.credit_table(book)
    fund = credit[(credit["scope_type"] == "fund") & (credit["dimension"] == "rating_tenor")]
    assert set(fund["key"]) == {"AA:3-5y", "AA+:5-10y"}


# ---------------------------------------------------------------- the engine's positions and the tables


def _engine_run():
    days = fx.CAL.business_days(date(2026, 11, 13), date(2026, 11, 20))
    curves, marks = {}, []
    other = FixedRateBond(7.26, date(2029, 1, 14))
    for k, d in enumerate(days):
        s = fx.CAL.settlement_date(d, 1)
        c = fx.nss(s, (fx.NSS[0] + 0.01 * k, *fx.NSS[1:]), t_max=40.0)
        curves[d] = c
        for isin, bond in (("GS2036", fx.GS2036), ("GS2029", other)):
            y = rt.model_yield(c, bond, s) + 0.03
            marks.append({"date": d, "isin": isin, "clean_price": bond.clean_price(y, s)})
    marks = pd.DataFrame(marks)
    trades = [{"trade_id": "T1", "book": "a", "isin": "GS2036", "kind": "gsec", "coupon_pct": 6.94,
               "maturity_date": "2036-05-11", "side": "buy", "face": 1e9, "trade_date": "2026-11-13",
               "price": marks["clean_price"].iloc[0]},
              {"trade_id": "T2", "book": "b", "isin": "GS2029", "kind": "gsec", "coupon_pct": 7.26,
               "maturity_date": "2029-01-14", "side": "buy", "face": 5e8, "trade_date": "2026-11-20"}]
    flows = pd.DataFrame([{"date": "2026-11-13", "book": "a", "amount": 2e9},
                          {"date": "2026-11-13", "book": "b", "amount": 1e9}])
    market = rt.MarketData(marks=marks, curves={"gsec": curves})
    res = rt.run_book(trades, flows=flows, market=market,
                      settings=rt.EngineSettings(access=rt.ACCESS_PRESETS["unregulated_company"]), end=days[-1])
    return res, market, days, pd.DataFrame(trades)


def test_the_engines_positions_settled_and_pending_keep_its_dv01():
    res, market, days, trades = _engine_run()
    pos = res.positions[res.positions["date"] == days[-1]]
    assert set(pos["state"]) == {"settled", "pending"}
    ctx = rk.RiskContext(market=market, settings=rt.EngineSettings(access=rt.ACCESS_PRESETS["unregulated_company"]),
                         risk=rk.RiskSettings(), as_of=fx.at(days[-1], 20), nav=res.nav, terms=trades)
    book = rk.risk_book(res.positions, ctx, day=days[-1])
    for p in book.positions:
        assert p.dv01_yield == pytest.approx(p.engine_dv01, rel=1e-12), p.isin
        assert "dv01_differs" not in p.flags and p.reference == "gsec"
    pending = [p for p in book.positions if p.state == "pending"][0]
    assert pending.settlement == fx.CAL.settlement_date(days[-1], 1)
    assert pending.market_value == pytest.approx(pending.units * pending.dirty)
    assert book.nav_of("fund", "fund") == pytest.approx(float(res.nav[(res.nav["date"] == days[-1])
                                                                      & (res.nav["book"] == "fund")]["nav"].iloc[0]))
    summary = rk.summary_table(book)
    total = summary[(summary["dimension"] == "total") & (summary["scope_type"] == "fund")].iloc[0]
    assert total["dv01_yield"] == pytest.approx(float(pos["dv01"].sum()), rel=1e-12)
    assert total["dv01_years"] == pytest.approx(total["dv01_yield"] / (book.nav_of("fund", "fund") * 1e-4))
    by_book = summary[(summary["dimension"] == "total") & (summary["scope_type"] == "book")]
    assert by_book["dv01_yield"].sum() == pytest.approx(total["dv01_yield"], rel=1e-12)
    kinds = summary[(summary["dimension"] == "kind") & (summary["scope_type"] == "fund")]
    assert list(kinds["key"]) == ["gsec"]
    buckets = summary[(summary["dimension"] == "tenor_bucket") & (summary["scope_type"] == "fund")]
    assert set(buckets["key"]) == {"1-3y", "5-10y"}
    keys = rk.key_rate_table(book)
    fund_keys = keys[keys["scope_type"] == "fund"]
    assert fund_keys["krd"].sum() == pytest.approx(total["dv01_curve"], rel=1e-12)
    assert fund_keys["krd_years"].notna().all()


def test_strategies_map_books_and_sum_their_nav():
    res, market, days, trades = _engine_run()
    sb = pd.DataFrame([{"book": "a", "strategy": "S1"}, {"book": "b", "strategy": "S1"}])
    ctx = rk.RiskContext(market=market, settings=rt.EngineSettings(access=rt.ACCESS_PRESETS["unregulated_company"]),
                         risk=rk.RiskSettings(), as_of=fx.at(days[-1], 20), nav=res.nav, strategy_books=sb,
                         terms=trades)
    book = rk.risk_book(res.positions, ctx, day=days[-1])
    assert {p.strategy for p in book.positions} == {"S1"}
    assert ("strategy", "S1") in book.scopes()
    assert book.nav_of("strategy", "S1") == pytest.approx(book.nav_of("book", "a") + book.nav_of("book", "b"))


def test_refusals_name_what_was_wrong():
    ctx = fx.gsec_context()
    pos = fx.gs2036_positions()
    with pytest.raises(ValueError, match="twice"):
        rk.risk_book(pd.concat([pos, pos]), ctx)
    with pytest.raises(ValueError, match="no position on 2026-11-16"):
        rk.risk_book(pos, ctx, day="2026-11-16")
    with pytest.raises(ValueError, match="lacks"):
        rk.risk_book(pos.drop(columns=["yield_pct"]), ctx)
    unknown = pos.assign(isin="NOTERMS")
    with pytest.raises(ValueError, match="NOTERMS"):
        rk.risk_book(unknown, ctx)
    with pytest.raises(ValueError, match="before the positions' day"):
        rk.risk_book(pos, rk.RiskContext(market=ctx.market, settings=ctx.settings, risk=ctx.risk,
                                         as_of=datetime(2026, 11, 12, 20)))
    with pytest.raises(ValueError, match="key_tenors"):
        rk.RiskSettings(key_tenors=(1.0, 0.5))
    with pytest.raises(ValueError, match="var_alpha"):
        rk.RiskSettings(var_alpha=1.2)
    with pytest.raises(ValueError, match="min_obs_hard"):
        rk.RiskSettings(window=30)
    with pytest.raises(ValueError, match="quantile_rule"):
        rk.RiskSettings(quantile_rule="median")
    with pytest.raises(TypeError, match="EngineSettings"):
        rk.RiskContext(market=ctx.market, settings=None, risk=ctx.risk, as_of=ctx.as_of)


def test_a_curve_not_yet_public_is_not_used_and_an_earlier_one_is_flagged():
    day = fx.D13
    late = {day: rt.CurveEntry(fx.nss(fx.S16), fx.at(day, 21))}  # public at 21:00, after the 20:00 as_of
    ctx = fx.gsec_context(entries=late)
    p = rk.risk_book(fx.gs2036_positions(), ctx).positions[0]
    assert p.curve is None and "no_curve" in p.flags
    assert {sn.method for sn in p.sens} == {"yield_proxy"}
    assert sum(sn.value for sn in p.sens) == pytest.approx(p.dv01_yield, rel=1e-12)
    earlier = {date(2026, 11, 12): rt.CurveEntry(fx.nss(date(2026, 11, 13)), fx.at(date(2026, 11, 12))), **late}
    p2 = rk.risk_book(fx.gs2036_positions(), fx.gsec_context(entries=earlier)).positions[0]
    assert p2.curve is not None and "stale_curve" in p2.flags
    no_time = {day: fx.nss(fx.S16)}
    p3 = rk.risk_book(fx.gs2036_positions(), fx.gsec_context(entries=no_time)).positions[0]
    assert "default_time" in p3.flags  # public at the marks' default 18:00, an estimate


def test_the_same_inputs_in_any_row_order_give_the_same_bytes(tmp_path):
    res, market, days, trades = _engine_run()
    ctx = rk.RiskContext(market=market, settings=rt.EngineSettings(access=rt.ACCESS_PRESETS["unregulated_company"]),
                         risk=rk.RiskSettings(methods=("hs_sensitivity", "parametric")), as_of=fx.at(days[-1], 20),
                         nav=res.nav, terms=trades)
    shuffled = res.positions.sample(frac=1.0, random_state=3).reset_index(drop=True)
    outs = []
    for k, frame in enumerate((res.positions, shuffled, res.positions)):
        result = rk.run_risk(frame, ctx, day=days[-1])
        paths = rk.write_risk(result, tmp_path / f"r{k}", history=True)
        outs.append({p.name: p.read_bytes() for p in paths})
    assert outs[0] == outs[1] == outs[2]


def test_no_clock_is_read_by_the_risk_modules():
    src = Path(rk.__file__).parent
    for name in ("risk.py", "stress.py"):
        text = (src / name).read_text(encoding="utf-8")
        assert not re.search(r"datetime\.now|date\.today|time\.time\(|utcnow|perf_counter", text), name


# ---------------------------------------------------------------- factor histories (T10, gaps, look-ahead)


def test_t10_nss_parameter_changes_become_key_rate_changes():
    c0 = fx.nss(fx.S16)
    entries = {fx.D13: c0, date(2026, 11, 16): fx.nss(date(2026, 11, 17), (7.6, -2.2, -1.0, 2.0, 1.5, 8.0)),
               date(2026, 11, 17): fx.nss(date(2026, 11, 18), (7.7, -2.2, -1.0, 2.0, 1.5, 8.0))}
    h = rk.nss_key_rate_history(entries, as_of=datetime(2026, 11, 18, 20))
    col = {f: h.changes[:, j] for j, f in enumerate(h.factors)}
    tau = rk.nss_key_rate_history({fx.D13: c0, date(2026, 11, 16): fx.nss(date(2026, 11, 17),
                                                                         (7.6, -2.0, -1.0, 2.0, 1.6, 8.0))},
                                  as_of=datetime(2026, 11, 18, 20))
    tcol = {f: tau.changes[:, j] for j, f in enumerate(tau.factors)}
    beta1 = [-18.422193, -17.008121, -14.597486, -11.046043, -8.646647, -5.785956, -4.245413, -2.996182, -1.999909,
             -1.499998, -1.0, -0.75, -0.6]
    tau1 = [-0.517716, -1.018744, -1.927787, -3.257439, -3.961529, -4.16642, -3.708584, -2.898825, -1.994706,
            -1.499736, -0.999999, -0.75, -0.6]
    for k, t in enumerate(rk.KEY_TENORS):
        f = f"gsec:{rk.tenor_label(t)}"
        assert col[f][0] == pytest.approx(beta1[k], abs=1e-6)  # -20 (1 - e^-x) / x, x = T / 1.5
        assert col[f][1] == pytest.approx(10.0, abs=1e-9)  # beta0 +0.10: every key rate +10 bp
        assert tcol[f][0] == pytest.approx(tau1[k], abs=1e-6)
    assert all("default_time" in h.flags.get((d, "gsec:10y"), "") for d in h.dates)  # no public_from: estimated


def test_a_fit_public_after_as_of_is_not_in_the_history():
    days = fx.history_days(fx.D13, 5)
    entries = fx.nss_entries(days, level_moves_bp=[0, 1, 2, 3, 4])
    late = dict(entries)
    late[days[-1]] = rt.CurveEntry(entries[days[-1]].curve, fx.at(days[-1], 21))
    h0 = rk.nss_key_rate_history(entries, as_of=fx.at(days[-1], 20))
    h1 = rk.nss_key_rate_history(late, as_of=fx.at(days[-1], 20))
    assert h0.dates[-1] == days[-1] and days[-1] not in h1.dates
    assert h1.changes.shape[0] == h0.changes.shape[0] - 1
    assert np.allclose(h1.changes, h0.changes[:-1])


def test_gaps_are_scaled_dropped_refused_or_break_the_series():
    days = fx.history_days(fx.D13, 12)
    kept = [days[0], days[1], days[4], days[11]]  # a 3-day gap, then a 7-day gap
    entries = {d: fx.nss(fx.CAL.settlement_date(d, 1), (7.6 + 0.01 * k, *fx.NSS[1:])) for k, d in enumerate(kept)}
    h = rk.nss_key_rate_history(entries, as_of=fx.at(fx.D13, 20))
    j = h.factors.index("gsec:10y")
    assert h.dates == (days[1], days[4])  # the 7-day gap broke the series
    assert h.changes[0, j] == pytest.approx(1.0, abs=1e-9)
    assert h.changes[1, j] == pytest.approx(1.0 / math.sqrt(3), abs=1e-9)
    assert "scaled_gap" in h.flags[(days[4], "gsec:10y")] and h.cells[(days[4], "gsec:10y")] == (days[1], 3)
    assert any("gap of more than 5" in n for n in h.notes)
    dropped = rk.nss_key_rate_history(entries, as_of=fx.at(fx.D13, 20), gap_rule="drop")
    assert dropped.dates == (days[1],)
    with pytest.raises(ValueError, match="3 business days apart"):
        rk.nss_key_rate_history(entries, as_of=fx.at(fx.D13, 20), gap_rule="raise")


def test_a_tenor_outside_the_fit_takes_the_nearest_in_range_change():
    days = fx.history_days(fx.D13, 2)
    entries = {d: fx.nss(fx.CAL.settlement_date(d, 1), (7.6, -2.0 - 0.1 * k, *fx.NSS[2:]), t_max=30.0)
               for k, d in enumerate(days)}
    h = rk.nss_key_rate_history(entries, as_of=fx.at(fx.D13, 20))
    col = dict(zip(h.factors, h.changes[0]))
    assert col["gsec:40y"] == col["gsec:30y"] == col["gsec:50y"]
    assert "extrapolated_node" in h.flags[(days[1], "gsec:50y")]
    assert "extrapolated_node" not in h.flags.get((days[1], "gsec:30y"), "")


def test_the_factor_table_is_cut_at_public_from_and_refuses_bad_rows():
    table = pd.DataFrame([{"date": "2026-11-12", "factor": "corp:AA:3-5y", "value": 1.50, "unit": "pct",
                           "public_from": "2026-11-12T18:00"},
                          {"date": "2026-11-13", "factor": "corp:AA:3-5y", "value": 1.52, "unit": "pct",
                           "public_from": "2026-11-13T19:00"},
                          {"date": "2026-11-16", "factor": "corp:AA:3-5y", "value": 1.60, "unit": "pct",
                           "public_from": "2026-11-17T09:00"}])  # public the next morning
    h = rk.table_factor_history(table, as_of=datetime(2026, 11, 16, 20))
    assert h.dates == (date(2026, 11, 13),) and h.changes[0, 0] == pytest.approx(2.0)
    later = rk.table_factor_history(table, as_of=datetime(2026, 11, 17, 10))
    assert later.changes[-1, 0] == pytest.approx(8.0)
    with pytest.raises(ValueError, match="did you mean 'corp'"):
        rk.table_factor_history(table.assign(factor="crop:AA:3-5y"), as_of=datetime(2026, 11, 17, 10))
    with pytest.raises(ValueError, match="bp and pct|pct and bp"):
        rk.table_factor_history(table.assign(unit=["pct", "bp", "pct"]), as_of=datetime(2026, 11, 17, 10))
    with pytest.raises(ValueError, match="two values"):
        rk.table_factor_history(pd.concat([table, table.assign(value=9.0)]).iloc[:4],
                                as_of=datetime(2026, 11, 17, 10))


# ---------------------------------------------------------------- quantiles, ES, covariance (T9, T9b, T12, T13)


def test_t9_one_position_sensitivity_var_and_es():
    pnl = -663_731.17027 * fx.grid()
    assert rk.hs_var(pnl, 0.99) == pytest.approx(81_30_706.835808, abs=1e-5)
    assert rk.hs_es(pnl, 0.99) == pytest.approx(82_10_354.576240, abs=1e-5)
    assert rk.hs_var(pnl, 0.975) == pytest.approx(78_65_214.367700, abs=1e-5)
    assert rk.hs_es(pnl, 0.975) == pytest.approx(80_88_228.040910, abs=1e-5)
    var, es, es_v, var_e, m = rk.var_es(pnl)
    assert (var, es_v, var_e, m) == (rk.hs_var(pnl, 0.99), rk.hs_es(pnl, 0.99), rk.hs_var(pnl, 0.975),
                                     rk.tail_size(250, 0.99))
    assert rk.hs_var(pnl, 0.99, "floor_plus_one") == rk.hs_var(pnl, 0.99)  # the same at 250 (m = 5/2)


def test_t9b_m_is_exact():
    assert rk.tail_size(250, 0.99) == Fraction(5, 2)
    assert rk.tail_size(200, 0.99) == 2  # 200 x (1 - 0.99) is 2.0000000000000018 in floats
    pnl = -np.arange(200, dtype=float)  # losses 0 ... 199
    assert rk.hs_var(pnl, 0.99) == 198.0  # the 2nd largest loss (m = 2 exactly), not the 3rd
    assert rk.hs_var(pnl, 0.99, "floor_plus_one") == 197.0  # L_(floor m + 1) when m is whole
    assert rk.hs_es(pnl, 0.99) == pytest.approx((199.0 + 198.0) / 2)


def test_t13_es_is_never_below_the_var_of_its_own_level_but_can_be_below_another():
    pnl = -np.array([10.0] * 3 + [0.0] * 247)
    assert rk.hs_es(pnl, 0.975) == pytest.approx(4.8) and rk.hs_var(pnl, 0.99) == 10.0
    rng = np.random.default_rng(5)
    for _ in range(20):
        x = rng.standard_t(3, size=137)
        for a in (0.95, 0.975, 0.99):
            assert rk.hs_es(x, a) >= rk.hs_var(x, a) - 1e-12  # I4


def test_t12_normal_constants_and_the_ewma_example():
    var, es = rk.normal_var_es(1.0, 0.99, 0.975)
    assert var == pytest.approx(2.326348, abs=1e-6) and es == pytest.approx(2.337803, abs=1e-6)
    assert es / var == pytest.approx(1.004924, abs=1e-6)
    cov, n = rk.ewma_covariance(np.array([[2.0, 1.0], [-3.0, -1.0], [1.0, 2.0]]))
    assert n == 3
    assert cov == pytest.approx(np.array([[4.602068281626292, 2.3329083439580676],
                                          [2.3329083439580676, 2.0624734381640457]]), abs=1e-12)
    many = np.ones((600, 1))
    assert rk.ewma_covariance(many)[1] == 550  # at most 550 observations
    with pytest.raises(ValueError):
        rk.ewma_covariance(np.zeros((0, 2)))


# ---------------------------------------------------------------- VaR through the book (I3, I6, T11, coverage)


def test_i3_and_the_three_methods_on_a_parallel_history():
    ctx, days, mv = fx.parallel_history_context(300)
    book = rk.risk_book(fx.gs2036_positions(), ctx)
    hist = rk.factor_history(ctx, book)
    assert len(hist.dates) == 299 and hist.factors[0].startswith("gsec:")
    p = book.positions[0]
    sens = rk.historical_var(book, hist, method="hs_sensitivity")
    window = np.array(mv[-250:])
    assert sens.n_obs == 250 and sens.status == "ok" and sens.coverage == 1.0
    assert sens.var == pytest.approx(p.dv01_curve * np.sort(window)[::-1][2], rel=1e-9)  # I3: the 3rd largest rise
    full = rk.historical_var(book, hist, method="hs_full")
    assert full.var < sens.var  # convexity: a long bond loses less than first order on a rise
    assert full.var == pytest.approx(sens.var, rel=0.01)
    ten = rk.historical_var(book, hist, method="hs_sensitivity", horizon_days=10)
    assert len(ten.scenario_dates) == 241 and ten.horizon_rule == "overlapping"  # n - 9 scenarios
    root = rk.historical_var(book, hist, rk.RiskSettings(horizon_rule="sqrt_time"), method="hs_sensitivity",
                             horizon_days=10)
    assert root.var == pytest.approx(sens.var * math.sqrt(10), rel=1e-12)
    par = rk.parametric_var(book, hist)
    cov, n = rk.ewma_covariance(hist.changes)  # every key rate moves alike: one factor in effect
    assert n == 299 and par.n_obs == 299
    assert par.var == pytest.approx(2.3263478740408408 * p.dv01_curve * math.sqrt(cov[0, 0]), rel=1e-9)
    assert par.es == pytest.approx(2.337802792201416 * p.dv01_curve * math.sqrt(cov[0, 0]), rel=1e-9)


def test_t11_full_revaluation_against_first_order_on_a_50_bp_move():
    ctx = fx.gsec_context()
    book = rk.risk_book(fx.gs2036_positions(), ctx)
    factors = tuple(f"gsec:{rk.tenor_label(t)}" for t in rk.KEY_TENORS)
    for move, full_want, first_want in ((50.0, -3_24_98_086.25, -3_31_86_557.93),
                                        (-50.0, 3_38_95_838.12, 3_31_86_557.93)):
        hist = rk.FactorHistory(dates=(fx.D13,), previous=(date(2026, 11, 12),), factors=factors,
                                changes=np.full((1, 13), move))
        _, full = rk.scenario_pnl(book, hist, method="hs_full")
        _, first = rk.scenario_pnl(book, hist, method="hs_sensitivity")
        assert full[0, 0] == pytest.approx(full_want, abs=0.01)
        assert first[0, 0] == pytest.approx(first_want, abs=0.01)
    beta1 = rk.nss_key_rate_history({date(2026, 11, 12): fx.nss(fx.D13), fx.D13: fx.nss(
        fx.S16, (7.6, -2.2, -1.0, 2.0, 1.5, 8.0))}, as_of=fx.at(fx.D13, 20))
    _, full = rk.scenario_pnl(book, beta1, method="hs_full")
    _, first = rk.scenario_pnl(book, beta1, method="hs_sensitivity")
    assert full[0, 0] == pytest.approx(26_56_473.03, abs=0.01) and first[0, 0] == pytest.approx(26_52_631.63, abs=0.01)
    dg = rk.scenario_pnl(book, rk.FactorHistory(dates=(fx.D13,), previous=(date(2026, 11, 12),), factors=factors,
                                                changes=np.full((1, 13), 50.0)),
                         rk.RiskSettings(delta_gamma=True), method="hs_sensitivity")[1][0, 0]
    assert abs(dg - (-3_24_98_086.25)) < abs(-3_31_86_557.93 - (-3_24_98_086.25))  # delta-gamma closes the gap


def test_short_histories_are_flagged_and_too_short_ones_refused():
    ctx, _, _ = fx.parallel_history_context(100)
    book = rk.risk_book(fx.gs2036_positions(), ctx)
    hist = rk.factor_history(ctx, book)
    r = rk.historical_var(book, hist, method="hs_sensitivity")
    assert r.status == "short_history" and r.n_obs == 99 and any(f.startswith("short_history") for f in r.flags)
    ctx, _, _ = fx.parallel_history_context(40)
    book = rk.risk_book(fx.gs2036_positions(), ctx)
    with pytest.raises(ValueError, match="39 observation"):
        rk.historical_var(book, rk.factor_history(ctx, book), method="hs_sensitivity")
    rows = rk.var_table(book, rk.factor_history(ctx, book))
    assert set(rows["status"]) == {"refused"} and rows["var"].isna().all()


def test_a_factor_without_history_lowers_the_coverage_and_a_proxy_restores_it():
    days = fx.history_days(date(2026, 11, 20), 120)
    s = date(2026, 11, 23)
    entries = fx.nss_entries(days, level_moves_bp=np.r_[0.0, fx.grid()[:119]])
    rows = _ncd_rows(values=((1e8, 1.02e8),))
    market = rt.MarketData(corporate_terms=pd.DataFrame(NCD_TERMS[:1]), curves={"gsec": entries})
    base = dict(market=market, settings=rt.EngineSettings(access=rt.ACCESS_PRESETS["nbfc_non_pd"]),
                risk=rk.RiskSettings(min_obs=60), as_of=datetime(2026, 11, 20, 20))
    ctx = rk.RiskContext(**base)
    book = rk.risk_book(pd.DataFrame(rows), ctx)
    assert book.positions[0].settlement == s
    hist = rk.factor_history(ctx, book)
    r = rk.historical_var(book, hist, method="hs_sensitivity")
    p = book.positions[0]
    assert r.status == "incomplete" and r.coverage == pytest.approx(p.dv01_curve / (p.dv01_curve + p.cs01))
    assert r.uncovered == pytest.approx(p.cs01) and any(f.startswith("uncovered_corp") for f in r.flags)
    spreads = pd.DataFrame([{"date": d, "factor": "corp:AA:5-10y", "value": 100 + (k % 7), "unit": "bp",
                             "public_from": fx.at(d, 19).isoformat()} for k, d in enumerate(days)])
    proxies = pd.DataFrame([{"factor": "corp:AA:3-5y", "proxy": "corp:AA:5-10y", "scale": 1.0}])
    ctx2 = rk.RiskContext(**base, factor_history=spreads, proxies=proxies)
    hist2 = rk.factor_history(ctx2, book)
    r2 = rk.historical_var(book, hist2, method="hs_sensitivity", proxies=proxies)
    assert r2.coverage == 1.0 and "proxied" in r2.flags and r2.status == "ok"
    assert r2.var > r.var


def test_the_stressed_window_finds_the_volatile_block_and_the_ratio_is_floored():
    n = 700
    mv = np.zeros(n)
    g = fx.grid()
    for k in range(1, n):
        mv[k] = g[k % 250] * (3.0 if 300 <= k < 550 else 0.3)
    ctx, days, _ = fx.parallel_history_context(n, moves=mv,
                                               risk=rk.RiskSettings(windows=("current", "stressed")))
    book = rk.risk_book(fx.gs2036_positions(), ctx)
    hist = rk.factor_history(ctx, book)
    start, end, es = rk.stressed_window(book, hist)
    a, b = days.index(start), days.index(end)
    assert b - a == 249 and a <= 300 and b >= 300  # the window overlaps the block's start
    assert any(300 <= k < 550 for k in range(a, b + 1))
    current = rk.historical_var(book, hist, method="hs_sensitivity", horizon_days=10)
    stressed = rk.stressed_es(book, hist, method="hs_sensitivity", horizon_days=10)
    assert stressed.window == "stressed" and stressed.es >= current.es
    assert "ratio_fc_rc=1.000000" in stressed.flags  # one factor set: the MAR33.6 ratio is 1
    assert stressed.es == pytest.approx(es, rel=1e-12)


def test_euler_components_sum_to_the_measures():
    res, market, days, trades = _engine_run()
    days_h = fx.history_days(days[-1], 200)
    entries = fx.nss_entries(days_h, level_moves_bp=np.r_[0.0, fx.grid()[:199]])
    entries.update({d: rt.CurveEntry(c, fx.at(d)) for d, c in market.curves["gsec"].items()})
    m2 = rt.MarketData(marks=market.marks, curves={"gsec": entries})
    ctx = rk.RiskContext(market=m2, settings=rt.EngineSettings(access=rt.ACCESS_PRESETS["unregulated_company"]),
                         risk=rk.RiskSettings(min_obs=60, horizons=(1,)), as_of=fx.at(days[-1], 20), nav=res.nav,
                         terms=trades)
    book = rk.risk_book(res.positions, ctx, day=days[-1])
    hist = rk.factor_history(ctx, book)
    results = rk.var_results(book, hist, scopes=[("fund", "fund")])
    comps = rk.var_components(book, hist, results=results)
    for r in results:
        mine = comps[(comps["method"] == r.method) & (comps["component_type"] == "position")]
        assert mine["es_contribution"].sum() == pytest.approx(r.es, rel=1e-9), r.method
        if r.method == "parametric":
            assert mine["var_contribution"].sum() == pytest.approx(r.var, rel=1e-9)
        else:
            assert mine["var_contribution"].sum() == pytest.approx(r.var, rel=1e-9)
        books = comps[(comps["method"] == r.method) & (comps["component_type"] == "book")]
        assert books["es_contribution"].sum() == pytest.approx(r.es, rel=1e-9)


def test_t32_tail_weights_split_the_es_exactly():
    a = np.array([3, -5, 1, -2, 4, -7, 0, 2, -1, 6, -3, 5, -4, 1, -6, 2, 0, -2, 3, -1], float)
    b = np.array([-1, 2, -3, 1, -2, 1, 4, -5, 2, -1, 0, 3, -2, -4, 1, 2, -3, 5, -1, 0], float)
    total = a + b
    w = rk.tail_weights(total, 0.875)
    assert list(np.flatnonzero(w)) == [5, 12, 14] and w[14] == 0.5
    m = float(rk.tail_size(20, 0.875))
    assert -(w @ total) / m == pytest.approx(5.8) == pytest.approx(rk.hs_es(total, 0.875))
    assert -(w @ a) / m == pytest.approx(5.6) and -(w @ b) / m == pytest.approx(0.2)


# ---------------------------------------------------------------- back-tests (T15-T17)


@pytest.mark.parametrize("x,lr", [(0, 5.025168), (1, 1.176491), (2, 0.108435), (3, 0.094940), (4, 0.769138),
                                  (5, 1.956810), (6, 3.555355), (7, 5.496990), (10, 12.955491)])
def test_t15_kupiec(x, lr):
    stat, p = rk.kupiec(250, x, 0.01)
    assert stat == pytest.approx(lr, abs=1e-6)
    if x == 6:
        assert p == pytest.approx(0.059354, abs=1e-6)


def test_t16_christoffersen():
    seq = [0] * 250
    for d in (10, 11, 50, 120, 121, 200):
        seq[d - 1] = 1
    ch = rk.christoffersen(seq)
    assert (ch["n00"], ch["n01"], ch["n10"], ch["n11"]) == (239, 4, 4, 2)
    assert ch["lr_ind"] == pytest.approx(8.136469, abs=1e-6) and ch["p_ind"] == pytest.approx(0.004338, abs=1e-6)
    cc = rk.kupiec(250, 6, 0.01)[0] + ch["lr_ind"]
    assert cc == pytest.approx(11.691823, abs=1e-6) and rk.chi2_sf(cc, 2) == pytest.approx(0.002892, abs=1e-6)


def test_t17_traffic_light_zones_and_multipliers():
    zones = [rk.traffic_light(x, 250, 0.01) for x in range(12)]
    assert [z[0] for z in zones] == ["green"] * 5 + ["amber"] * 5 + ["red"] * 2
    assert [z[1] for z in zones[:11]] == [1.5] * 5 + [1.70, 1.76, 1.83, 1.88, 1.92, 2.0]
    assert zones[4][2] == pytest.approx(0.89218763, abs=1e-8) and zones[5][2] == pytest.approx(0.95881682, abs=1e-8)
    assert zones[9][2] == pytest.approx(0.99974981, abs=1e-8) and zones[10][2] == pytest.approx(0.99994610, abs=1e-8)
    assert [rk.traffic_light(x, 500, 0.01)[0] for x in (8, 9, 14, 15)] == ["green", "amber", "amber", "red"]
    assert math.isnan(rk.traffic_light(3, 500, 0.01)[1])
    assert rk.traffic_light(0, 4, 0.01)[0] == "green" and rk.traffic_light(0, 4, 0.01)[2] > 0.95  # no exception
    assert rk.traffic_light(1, 4, 0.01)[0] == "amber"
    assert 1 - rk.binomial_cdf(4, 250, 0.01) == pytest.approx(0.107812, abs=1e-6)  # MAR99: five or more, 10.8%
    with pytest.raises(ValueError):
        rk.chi2_sf(1.0, 3)


def test_backtest_pairs_each_var_with_the_next_days_pnl_and_counts_strictly():
    days = fx.CAL.business_days(date(2026, 11, 2), date(2026, 11, 13))
    hist = pd.DataFrame([{"date": d, "as_of": "", "scope_type": "fund", "scope": "fund", "method": "hs_sensitivity",
                          "horizon_days": 1, "alpha_var": 0.99, "var": 100.0, "alpha_es": 0.975, "es": 110.0,
                          "n_obs": 250, "coverage": 1.0, "status": "ok"} for d in days[:-1]])
    pnl = {days[1]: -100.0, days[2]: -100.01, days[3]: 5.0, days[4]: -150.0}
    nav = pd.DataFrame([{"date": d, "book": "b", "pnl": pnl.get(d, 1.0)} for d in days if d != days[6]])
    att = pd.DataFrame([{"date": d, "book": "b", "instrument": "X", "line": line, "amount": amt, "memo": False}
                        for d in days for line, amt in (("accrual", 1.0), ("curve_parallel", pnl.get(d, 0.0)),
                                                        ("spread_cost", -500.0), ("funding_repo", -50.0))])
    detail, summary = rk.backtest(hist, nav, att)
    d = detail.set_index("pnl_date")
    assert not d.loc[days[1], "exception_actual"]  # -100 is not below -100: strict
    assert d.loc[days[2], "exception_actual"] and d.loc[days[4], "exception_actual"]
    assert d.loc[days[6], "missing"] and d.loc[days[6], "exception_actual"]  # a missing P&L counts
    assert d.loc[days[2], "pnl_hypothetical"] == pytest.approx(-99.01)  # trades and funding left out
    s = summary.iloc[0]
    assert s["observations"] == len(days) - 1 and s["exceptions_actual"] == 3
    assert s["exceptions"] == max(s["exceptions_actual"], s["exceptions_hypothetical"])
    assert "of 250 observations" in s["notes"]


def test_hypothetical_pnl_keeps_only_market_lines_and_var_history_replaces_a_day():
    att = pd.DataFrame([{"date": "2026-11-13", "book": "b", "instrument": "X", "line": line, "amount": 1.0,
                         "memo": memo} for line, memo in (("accrual", False), ("krd_10y", False),
                                                          ("ois_krd_2y", False), ("trading", False),
                                                          ("fees", False), ("memo_haircut_cost", True),
                                                          ("cash_benchmark", False), ("unexplained", False))])
    h = rk.hypothetical_pnl(att)
    assert h[h["book"] == "b"]["pnl_hypothetical"].iloc[0] == 4.0
    assert set(h["book"]) == {"b", "fund"}
    var = pd.DataFrame([{"date": date(2026, 11, 13), "as_of": "x", "scope_type": "fund", "scope": "fund",
                         "method": "parametric", "horizon_days": 1, "horizon_rule": "", "window": "current",
                         "alpha_var": 0.99, "var": 1.0, "alpha_es": 0.975, "es": 2.0, "n_obs": 250,
                         "coverage": 1.0, "status": "ok"}])
    once = rk.append_var_history(None, var)
    twice = rk.append_var_history(once, var.assign(var=5.0))
    assert len(twice) == 1 and float(twice["var"].iloc[0]) == 5.0


# ---------------------------------------------------------------- the returns.py accessors (spec section 13)


class _Peek:
    """Buys once and reads every new accessor each evening."""

    def __init__(self, peek: bool):
        self.peek, self.n, self.seen = peek, 0, []

    def decide(self, snap):
        self.n += 1
        if self.peek:
            snap.open_orders()
            snap.swaps()
            snap.curve_history("gsec")
            snap.instrument("GS2036")
            self.seen.append(sorted(snap._engine.instruments))
        return [rt.Order(book="main", isin="GS2036", side="buy", face=1e8)] if self.n == 1 else []


def test_a_strategy_reading_the_accessors_leaves_the_engine_outputs_byte_identical(tmp_path):
    days = fx.CAL.business_days(date(2026, 11, 13), date(2026, 11, 20))
    marks = pd.DataFrame([{"date": d, "isin": "GS2036", "clean_price": 97.43, "vwap": 97.43} for d in days])
    curves = {"gsec": {d: rt.CurveEntry(fx.nss(fx.CAL.settlement_date(d, 1)), fx.at(d)) for d in days}}
    outs = []
    for peek in (False, True):
        strategy = _Peek(peek)
        res = rt.run_backtest(strategy, rt.MarketData(marks=marks, master=fx.MASTER, curves=curves),
                              rt.EngineSettings(access=rt.ACCESS_PRESETS["unregulated_company"]), start=days[0],
                              end=days[-1], capital=2e8)
        paths = rt.write_result(res, tmp_path / str(peek))
        outs.append({p.name: p.read_bytes() for p in paths})
    assert outs[0] == outs[1]
    assert strategy.seen[0] == []  # GS2036 built for the snapshot, not registered before its order filled


def test_snapshot_views_are_cut_at_the_decision():
    days = fx.CAL.business_days(date(2026, 11, 13), date(2026, 11, 18))
    marks = pd.DataFrame([{"date": d, "isin": "GS2036", "clean_price": 97.43, "source": "valuation"}
                          for d in days])  # valuation marks only: the order waits for a traded price
    curves = {"gsec": {d: rt.CurveEntry(fx.nss(fx.CAL.settlement_date(d, 1)), fx.at(d, 21 if k == 2 else 19))
                       for k, d in enumerate(days)}}
    seen = []

    class _Look:
        def decide(self, snap):
            seen.append((snap.day, snap.open_orders(), sorted(snap.curve_history())))
            return [rt.Order(book="main", isin="GS2036", side="buy", face=1e8, order_id="X1")] if not seen[:-1] \
                else []
    rt.run_backtest(_Look(), rt.MarketData(marks=marks, master=fx.MASTER, curves=curves),
                    rt.EngineSettings(access=rt.ACCESS_PRESETS["unregulated_company"]), start=days[0], end=days[-1],
                    capital=2e8)
    first, second = seen[0], seen[1]
    assert first[1].empty and list(second[1]["order_id"]) == ["X1"]  # queued, waiting for a traded price
    assert list(second[1].columns) == list(rt.OPEN_ORDER_COLUMNS)
    third = seen[2]
    assert days[2] not in third[2] and days[1] in third[2]  # the 21:00 fit is not public at 20:00


def test_snapshot_instrument_of_an_frb_without_coupons_raises_without_stopping_the_run():
    days = fx.CAL.business_days(date(2026, 11, 13), date(2026, 11, 17))
    errors = []

    class _Ask:
        def decide(self, snap):
            try:
                snap.instrument(fx.FRB2031)
            except ValueError as exc:
                errors.append(str(exc))
            return []
    res = rt.run_backtest(_Ask(), rt.MarketData(frb_coupons=None),
                          rt.EngineSettings(access=rt.ACCESS_PRESETS["unregulated_company"]), start=days[0],
                          end=days[-1], capital=1e8)
    assert len(errors) == len(days) and errors[0].startswith(fx.FRB2031)
    assert len(res.nav) == len(days)


def test_instruments_as_known_and_book_from_snapshot_agree_with_the_file_route():
    with pytest.raises(ValueError, match="two values of coupon_pct"):
        rt.instruments_as_known(rt.MarketData(), rt.EngineSettings(access=rt.ACCESS_PRESETS["nbfc_non_pd"]),
                                ["X"], datetime(2026, 11, 13, 20),
                                terms=pd.DataFrame([{"isin": "X", "kind": "gsec", "coupon_pct": 7.0,
                                                     "maturity_date": "2030-01-01"},
                                                    {"isin": "X", "kind": "gsec", "coupon_pct": 7.1}]))
    days = fx.CAL.business_days(date(2026, 11, 13), date(2026, 11, 18))
    marks = pd.DataFrame([{"date": d, "isin": "GS2036", "clean_price": 97.43, "vwap": 97.43} for d in days])
    curves = {"gsec": {d: rt.CurveEntry(fx.nss(fx.CAL.settlement_date(d, 1)), fx.at(d)) for d in days}}
    market = rt.MarketData(marks=marks, master=fx.MASTER, curves=curves)
    st = rt.EngineSettings(access=rt.ACCESS_PRESETS["unregulated_company"])
    ctx = rk.RiskContext(market=market, settings=st, risk=rk.RiskSettings(), as_of=fx.at(days[0], 20))
    books = {}

    class _Risk(_Peek):
        def decide(self, snap):
            if len(snap.positions):
                books[snap.day] = rk.book_from_snapshot(snap, ctx)
            return super().decide(snap)
    res = rt.run_backtest(_Risk(False), market, st, start=days[0], end=days[-1], capital=2e8)
    day = max(books)
    from_file = rk.risk_book(res.positions, replace_as_of(ctx, fx.at(day, 20)), day=day)
    a, b = books[day].positions[0], from_file.positions[0]
    assert a.dv01_curve == pytest.approx(b.dv01_curve, rel=1e-14) and a.dv01_yield == b.dv01_yield


def replace_as_of(ctx, when):
    from dataclasses import replace
    return replace(ctx, as_of=when)


def test_an_sdl_on_the_gsec_curve_has_an_sdl_spread_factor_and_none_on_its_own_curve():
    sdl = FixedRateBond(7.61, date(2030, 11, 18), SDL)
    curve = fx.nss(fx.S16)
    y = fx.on_curve_yield(sdl, curve, fx.S16, z=0.006)
    row = fx.bond_row(sdl, "SDL2030", "sdl", 2e8, fx.D13, fx.S16, y)
    master = pd.DataFrame([{"isin": "SDL2030", "kind": "sdl", "coupon_pct": 7.61, "maturity_date": "2030-11-18",
                            "state_code": "MH"}])
    entries = {fx.D13: rt.CurveEntry(curve, fx.at(fx.D13))}
    market = rt.MarketData(curves={"gsec": entries, "sdl": entries}, master=master)
    ctx = fx.gsec_context(market=market)
    p = rk.risk_book(pd.DataFrame([row]), ctx).positions[0]
    assert p.issuer_id == "STATE:MH" and p.sector == "local_government" and p.z_spread * 1e4 == pytest.approx(60.0)
    spread = [sn for sn in p.sens if sn.basis == "spread"]
    assert [sn.factor for sn in spread] == ["sdl_spread:3-5y"] and math.isnan(p.cs01)
    own = rk.RiskContext(market=market, settings=ctx.settings, risk=rk.RiskSettings(sdl_mode="own_curve"),
                         as_of=ctx.as_of)
    q = rk.risk_book(pd.DataFrame([row]), own).positions[0]
    assert q.reference == "sdl" and not [sn for sn in q.sens if sn.basis == "spread"]
    assert {sn.factor_class for sn in q.sens} == {"sdl"}
