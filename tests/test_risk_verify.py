"""Regression tests for the verification of the risk layer's review fixes: each test reproduces a verifier's failing
case with synthetic data built here (NSS fits with known moves, typed-in bond terms, made-up issuers, ratings and
NAV rows) and checks the corrected behaviour. Nothing here is a real price, rating or limit."""

import math
from dataclasses import replace
from datetime import date, datetime

import limits_fixtures as lf
import numpy as np
import pandas as pd
import pytest
import risk_fixtures as fx

from cr_strat import limits as lm
from cr_strat import returns as rt
from cr_strat import risk as rk
from cr_strat.ois import MIBOR_OIS, OisSwap, effective_date, ois_npv

NCD_TERMS = [{"isin": "INE000A07011", "issuer_id": "ISS1", "kind": "corporate", "coupon_pct": "9.0", "frequency": "1",
              "issue_date": "2023-12-15", "maturity_date": "2030-12-15", "rating_bucket": "AA",
              "sector": "financials"}]
SPREAD = "corp:AA:3-5y"


# ---------------------------------------------------------------- the VaR window and a factor with a hole in it


def _hole_book(*, spread_keep=None, curve_keep=None, n: int = 300):
    """Rs 100 cr GS 2036 and Rs 1 cr of an AA NCD on ``n`` days of NSS fits whose last 100 days move five times as
    much, a spread table for the NCD every day; ``spread_keep`` / ``curve_keep``: boolean masks of the days kept."""
    end = fx.D13
    days = fx.history_days(end, n)
    rng = np.random.default_rng(3)
    moves = rng.normal(0, 4, n)
    moves[0] = 0
    moves[-100:] *= 5
    entries = fx.nss_entries(days, level_moves_bp=moves)
    if curve_keep is not None:
        entries = {d: e for d, e, k in zip(days, entries.values(), curve_keep) if k or d == end}
    level = 150 + np.cumsum(rng.normal(0, 1.0, n))
    table = pd.DataFrame({"date": days, "factor": SPREAD, "value": level, "unit": "bp", "public_from": "",
                          "source": "synthetic"})
    if spread_keep is not None:
        table = table[np.asarray(spread_keep)].reset_index(drop=True)
    settings = rt.EngineSettings(access=rt.ACCESS_PRESETS["nbfc_non_pd"], project_calendar=True)
    market = rt.MarketData(corporate_terms=pd.DataFrame(NCD_TERMS), curves={"gsec": entries}, master=fx.MASTER)
    as_of = fx.at(end, 20)
    ncd = rt.instruments_as_known(market, settings, ["INE000A07011"], as_of)["INE000A07011"].bond
    curve = entries[end].curve
    settle = curve.valuation_date
    rows = [fx.bond_row(fx.GS2036, "GS2036", "gsec", 1e9, end, settle, fx.on_curve_yield(fx.GS2036, curve, settle)),
            fx.bond_row(ncd, "INE000A07011", "corporate", 1e7, end, settle, ncd.yield_from_dirty_price(101.0, settle),
                        source="valuation")]
    ctx = rk.RiskContext(market=market, settings=settings, risk=rk.RiskSettings(), as_of=as_of,
                         factor_history=table)
    book = rk.risk_book(pd.DataFrame(rows), ctx)
    return book, rk.factor_history(ctx, book), ctx, days


def _wide(hist):
    return pd.DataFrame(hist.changes, index=pd.Index(hist.dates), columns=list(hist.factors))


def test_a_spread_factor_with_a_hole_inside_the_window_is_left_out_and_the_window_stays_current():
    full, hist_full, _, days = _hole_book()
    keep = np.ones(len(days), dtype=bool)
    keep[-100:-8] = False  # 92 business days missing, ending 8 business days before the book's day
    book, hist, ctx, _ = _hole_book(spread_keep=keep)
    ref = rk.historical_var(full, hist_full, method="hs_sensitivity")
    for method in ("hs_sensitivity", "hs_full"):
        r = rk.historical_var(book, hist, method=method)
        # the old rule: the window the dates with every factor, pushed back to 2025 past the hole (7 of the 100
        # volatile days in it), status ok and coverage 1
        assert (r.window_start, r.window_end, r.n_obs) == (ref.window_start, fx.D13, 250)
        assert r.status == "incomplete" and r.coverage < 1 and f"holey_factor:{SPREAD}" in r.flags
        assert sum(d in set(r.scenario_dates) for d in days[-100:]) == 100
    p = rk.parametric_var(book, hist)
    p_full = rk.parametric_var(full, hist_full)
    assert p.n_obs == p_full.n_obs == 299 and p.status == "incomplete" and f"holey_factor:{SPREAD}" in p.flags
    # the gate's parametric VaR leaves out the same factor and gives the same figure
    var, _, coverage, n, detail = lm.parametric_from_frames(rk.sensitivities(book), _wide(hist), ctx.risk,
                                                            day=fx.D13)
    assert var == pytest.approx(p.var, rel=1e-12) and n == 299 and coverage == pytest.approx(p.coverage, rel=1e-12)
    assert coverage < 1 and f"{SPREAD} (93 dates missing)" in detail
    # a proxy with a full history covers it again on the current window
    proxies = pd.DataFrame([{"factor": SPREAD, "proxy": "gsec:5y", "scale": 1.0}])
    q = rk.historical_var(book, hist, method="hs_sensitivity", proxies=proxies)
    assert q.window_start == ref.window_start and q.coverage == 1.0 and "proxied" in q.flags and q.status == "ok"


def test_a_few_missing_days_are_kept_and_more_than_max_gap_days_leave_the_factor_out():
    _, _, _, days = _hole_book()
    for missing, kept in ((4, True), (6, False)):
        keep = np.ones(len(days), dtype=bool)
        keep[[len(days) - 20 * (k + 1) for k in range(missing)]] = False  # single days, each a scaled 2-day change
        book, hist, _, _ = _hole_book(spread_keep=keep)
        r = rk.historical_var(book, hist, method="hs_sensitivity")
        if kept:
            assert r.coverage == 1.0 and r.status == "ok" and not any("holey" in f for f in r.flags)
        else:
            assert r.coverage < 1 and r.status == "incomplete" and f"holey_factor:{SPREAD}" in r.flags
    weekly = np.zeros(400, dtype=bool)
    weekly[::-5] = True  # a weekly spread table (79 changes) beside daily curves: four days in five missing
    book, hist, _, _ = _hole_book(spread_keep=weekly, n=400)
    r = rk.historical_var(book, hist, method="hs_sensitivity")
    assert r.window_end == fx.D13 and r.n_obs == 250 and f"holey_factor:{SPREAD}" in r.flags


def test_a_hole_in_the_curve_history_itself_makes_the_window_incomplete_and_the_gate_refuses():
    _, _, _, days = _hole_book()
    keep = np.ones(len(days), dtype=bool)
    keep[-100:-8] = False  # the fits themselves missing; the spread table complete
    book, hist, ctx, _ = _hole_book(curve_keep=keep)
    r = rk.historical_var(book, hist, method="hs_sensitivity")
    assert r.status == "incomplete" and "holey_window:93" in r.flags and r.coverage == 1.0
    p = rk.parametric_var(book, hist)
    assert p.status == "incomplete" and "holey_window:93" in p.flags
    with pytest.raises(ValueError, match="leave out 93 business days of a factor's changes"):
        lm.parametric_from_frames(rk.sensitivities(book), _wide(hist), ctx.risk, day=fx.D13)
    # a coarse history whose every change spans its gap (weekly fits and a weekly table) has no hole
    weekly = np.zeros(len(days), dtype=bool)
    weekly[::-5] = True
    book, hist, _, _ = _hole_book(curve_keep=weekly, spread_keep=weekly)
    r = rk.historical_var(book, hist, rk.RiskSettings(min_obs_hard=40), method="hs_sensitivity")
    assert r.status == "short_history" and not any("holey" in f for f in r.flags)


# ---------------------------------------------------------------- L06: the KRDs netted into the OIS curve's last node


def _short_history_gate(days_kept: int = 40):
    market, trades = lf.ois_market()
    days, _, entries = lf.fits()
    keep = set(days[-days_kept:])  # fewer changes than the VaR's 60: the key-rate test decides alone
    short = replace(market, curves={"gsec": {d: e for d, e in entries.items() if d in keep}},
                    ois_curves={d: e for d, e in market.ois_curves.items() if d in keep})
    settings = rt.EngineSettings(access=rt.ACCESS_PRESETS["unregulated_company"], project_calendar=True)
    ctx = rk.RiskContext(market=short, settings=settings, risk=rk.RiskSettings(min_obs_hard=60),
                         as_of=fx.at(lf.END, 20), terms=trades, nss_table={"gsec": short.curves["gsec"]})
    state = lf.state([lf.gsec_row("GS2036", 1e9)], {"b": (1.5e9, 5e8)}, ctx, scenarios=lf.scenario_rows())
    return short, ctx, state


def _sized_payer(state, market, months: int) -> rt.Order:
    """A payer swap sized to the book's first bond's yield DV01 at the OIS curve of the day."""
    curve = market.ois_curves[lf.END].curve
    cal = fx.CAL.lenient()
    start = effective_date(cal.add_business_days(lf.END, 1), MIBOR_OIS, cal)
    unit = ois_npv(curve, OisSwap(effective=start, months=months, fixed_rate_pct=6.0, notional=1e7, pay_fixed=True),
                   calendar=cal).dv01
    return rt.Order(book="b", side="pay", face=round(state.book.positions[0].dv01_yield / abs(unit) * 1e7, 0),
                    months=months)


def test_an_18_month_payer_against_a_10_year_bond_is_refused_by_the_hedging_only_test():
    market, ctx, state = _short_history_gate()
    assert lm.ois_key_tenors_of_day(ctx, lf.END) == (0.25, 0.5, 1.0, 2.0, 3.0, 5.0)  # the B3 curve ends at 5 years
    order = _sized_payer(state, market, 18)
    _, rows = lm.PreTradeGate(None, ctx).check(state, [order])
    frame = lm.gate_frame(rows).set_index("limit_id")
    # the old rule netted the bond's 3- to 10-year KRDs into the swap's own last row (2 years): 0.995558, approved
    assert frame["decision"].iloc[0] == "refused"
    assert frame.loc["L06", "post_value"] == pytest.approx(1.923818, abs=1e-6)
    view = lm._view(state, [lm.order_risk(state, order, seq=1)], {})
    (_, ratio, ok, detail), = lm.metric_values(view, "ois_hedge_effect", "book", "b", "", "ratio")
    assert ok and "KRDs beyond 5 years netted into it: the OIS curve's last key tenor" in detail
    assert "Rs 1,272,928.09 with the swaps, Rs 661,667.77 without" in detail
    # a swap at the curve's end still hedges the long bond (54 months: approved, as before)
    approved, rows = lm.PreTradeGate(None, ctx).check(state, [_sized_payer(state, market, 54)])
    assert [r["decision"] for r in rows][:1] == ["approved"] and len(approved) == 1
    # without the day's OIS curve the netting node is the settings' last OIS key tenor (10 years), never the swaps'
    (_, ratio_none, ok, detail), = lm.metric_values(replace(view, ois_key_tenors=(), _values={}),
                                                    "ois_hedge_effect", "book", "b", "", "ratio")
    assert ok and ratio_none >= ratio and "no OIS curve of the day given" in detail


# ---------------------------------------------------------------- issuer and group selectors


def test_issuer_and_group_selectors_ignore_case_and_an_unknown_id_is_incomplete():
    market, trades, res, ctx, rr = lf.engine_and_risk("nbfc_non_pd")  # the NCD's issuer is ISS1
    rows = [lf.approved(limit_id="E1", metric="exposure", unit="pct_nav", selector="issuer=iss1", hard_level="5"),
            lf.approved(limit_id="J1", metric="jtd", unit="pct_nav", selector="issuer=iss1", hard_level="3"),
            lf.approved(limit_id="C1", metric="cs01", unit="rs_per_bp", selector="issuer=iss1", hard_level="1e4"),
            lf.approved(limit_id="G1", metric="exposure", unit="pct_nav", selector="group=Iss1", hard_level="5"),
            lf.approved(limit_id="E9", metric="exposure", unit="pct_nav", selector="issuer=ISS9", hard_level="5"),
            lf.approved(limit_id="G9", metric="jtd", unit="pct_nav", selector="group=GRPX", hard_level="3"),
            lf.approved(limit_id="V1", metric="exposure", unit="pct_nav", selector="issuer=goi;kind=gsec",
                        hard_level="500")]
    table = lm.read_limits(lf.limit_frame(rows))
    metrics = lm.metrics_frame(lm.metric_inputs(rr, res, ctx, day=lf.END), table)
    checks = lm.check_limits(metrics, table, as_of=ctx.as_of, legal_form=ctx.settings.access).set_index("limit_id")
    # the old rule compared the ids exactly: iss1 read 0, ok, 'none held'
    assert checks.loc["E1", "value"] == pytest.approx(7.2843, abs=1e-4) and checks.loc["E1", "status"] == "breach"
    assert checks.loc["J1", "value"] == pytest.approx(5.6406, abs=1e-4) and checks.loc["J1", "status"] == "breach"
    assert checks.loc["C1", "value"] == pytest.approx(73542.09, abs=0.01) and checks.loc["C1", "status"] == "breach"
    assert checks.loc["G1", "value"] == checks.loc["E1", "value"]
    for lid, what in (("E9", "issuer 'ISS9'"), ("G9", "group 'GRPX'")):
        assert checks.loc[lid, "status"] == "incomplete" and math.isnan(checks.loc[lid, "value"])
        assert f"selector matched nothing: {what}" in checks.loc[lid, "detail"]
    assert checks.loc["V1", "status"] == "ok" and checks.loc["V1", "value"] > 0  # GoI is a known id
    # the gate blocks a purchase on the lower-case limit
    state = lm.state_from_result(res, lf.END, ctx, risk=rr)
    limits = lf.limit_frame([lf.approved(limit_id="E1", metric="exposure", unit="pct_nav", selector="issuer=iss1",
                                         hard_level="8")])
    _, out = lm.PreTradeGate(limits, ctx, legal_form_rows=False).check(state, [rt.Order(book="b", side="buy",
                                                                                         face=5e8, isin=lf.NCD)])
    frame = lm.gate_frame(out)
    assert frame["decision"].iloc[0] == "refused" and frame["post_value"].iloc[0] == pytest.approx(25.510856,
                                                                                                    abs=1e-6)
