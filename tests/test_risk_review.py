"""Regression tests for the review of the risk layer (risk.py and stress.py): each test reproduces a reviewer's failing
case with synthetic data built here (NSS fits with known moves, typed-in bond terms, made-up issuers and ratings) and
checks the corrected behaviour. Nothing here is a real price or rating."""

import math
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest
import risk_fixtures as fx

from cr_strat import returns as rt
from cr_strat import risk as rk
from cr_strat import stress as st
from cr_strat.rating_scale import LT_NOTCH

NCD_TERMS = [{"isin": "INE000A07011", "issuer_id": "ISS1", "kind": "corporate", "coupon_pct": "9.0", "frequency": "1",
              "issue_date": "2023-12-15", "maturity_date": "2030-12-15", "rating_bucket": "AA",
              "sector": "financials"}]
CP = "INE000A14ZZ9"  # a CP of the NCD's company: the same seven-character ISIN prefix


def _ncd():
    settings = rt.EngineSettings(access=rt.ACCESS_PRESETS["nbfc_non_pd"])
    market = rt.MarketData(corporate_terms=pd.DataFrame(NCD_TERMS))
    return rt.instruments_as_known(market, settings, ["INE000A07011"], datetime(2026, 11, 13, 20))[
        "INE000A07011"].bond


def _cp_row(bond, face: float, day: date, settle: date, y: float, *, book: str = "b") -> dict:
    px = bond.price(y, settle, rounded=False)
    return {"date": day, "book": book, "isin": CP, "kind": "cp", "state": "settled", "face": face,
            "settlement_date": settle, "clean_mark": px, "mark_source": "valuation", "mark_age_days": 0,
            "yield_pct": y, "accrued": 0.0, "value": face / 100 * px, "dv01": face / 100 * bond.dv01(y, settle),
            "bucket": "", "liquidity": "", "haircut_pct": "", "funded": "", "stale_warning": False,
            "extrapolated": False, "calendar_projected": False, "notes": ""}


# ---------------------------------------------------------------- the VaR window and a factor whose history stopped


def _stale_book(spread_days, *, proxies=None):
    end = fx.D13
    ctx0, days, _ = fx.parallel_history_context(n=400, end=end)
    market = rt.MarketData(curves=ctx0.market.curves, master=fx.MASTER, corporate_terms=pd.DataFrame(NCD_TERMS))
    settle = fx.CAL.settlement_date(end, 1)
    curve = ctx0.market.curves["gsec"][end].curve
    ncd = _ncd()
    pos = pd.DataFrame([fx.bond_row(fx.GS2036, "GS2036", "gsec", 1e9, end, settle,
                                    fx.on_curve_yield(fx.GS2036, curve, settle)),
                        fx.bond_row(ncd, "INE000A07011", "corporate", 2e8, end, settle,
                                    fx.on_curve_yield(ncd, curve, settle, 0.01), liquidity="")])
    rows = [{"date": d, "factor": "corp:AA:3-5y", "value": 100 + 2.0 * math.sin(k), "unit": "bp",
             "public_from": f"{d}T19:00", "source": "synthetic"} for k, d in enumerate(spread_days(days))]
    ctx = rk.RiskContext(market=market, settings=ctx0.settings, risk=rk.RiskSettings(), as_of=ctx0.as_of,
                         factor_history=pd.DataFrame(rows), nss_table=ctx0.market.curves, proxies=proxies)
    book = rk.risk_book(pos, ctx, day=end)
    return book, rk.factor_history(ctx, book), ctx


def test_a_factor_whose_history_stopped_is_left_out_and_the_window_ends_on_the_book_day():
    # the spread table's 200 days end 100 business days before the book's day: the old rule cut every factor's
    # window back to them (window 2025-..., status ok, coverage 1)
    book, hist, _ = _stale_book(lambda days: days[-300:-100])
    for method in ("hs_sensitivity", "hs_full"):
        r = rk.historical_var(book, hist, method=method)
        assert r.window_end == fx.D13 and r.n_obs == 250
        assert r.status == "incomplete" and r.coverage < 1
        assert "stale_factor:corp:AA:3-5y" in r.flags
    p = rk.parametric_var(book, hist)
    assert p.window_end == fx.D13 and p.status == "incomplete" and "stale_factor:corp:AA:3-5y" in p.flags
    gsec_only = rk.historical_var(book, hist, method="hs_sensitivity", scope=("book", "b"))
    assert gsec_only.window_end == fx.D13
    # a proxy with a current history covers it again, on the window that ends on the book's day
    proxied, hist2, ctx = _stale_book(lambda days: days[-300:-100], proxies=pd.DataFrame(
        [{"factor": "corp:AA:3-5y", "proxy": "gsec:5y", "scale": 1.0}]))
    r = rk.historical_var(proxied, hist2, method="hs_sensitivity", proxies=ctx.proxies)
    assert r.window_end == fx.D13 and r.coverage == 1.0 and "proxied" in r.flags and r.status == "ok"


def test_a_factor_a_few_days_behind_is_kept_and_a_window_ending_early_is_incomplete():
    # last change 3 business days before the day: within max_gap_days (5), kept; the window ends on its last change
    book, hist, _ = _stale_book(lambda days: days[-300:-3])
    r = rk.historical_var(book, hist, method="hs_sensitivity")
    assert "stale_factor:corp:AA:3-5y" not in r.flags and r.coverage == 1.0
    assert r.window_end == fx.CAL.add_business_days(fx.D13, -3) and r.status == "ok"
    # a tighter max_gap_days (2) leaves it out
    tight = rk.historical_var(book, hist, rk.RiskSettings(max_gap_days=2), method="hs_sensitivity")
    assert "stale_factor:corp:AA:3-5y" in tight.flags and tight.window_end == fx.D13


# ---------------------------------------------------------------- ratings: the ISIN's, the issuer's, short-term grades


def test_rating_takes_the_worse_of_the_isin_and_the_issuer_and_reads_a_short_term_grade():
    as_of = datetime(2026, 11, 20, 20)
    cp_rows = pd.DataFrame([
        {"agency": "CRISIL", "rating": "CRISIL A1+", "public_from": "2026-10-01T10:00", "isin": CP, "issuer_id": ""},
        {"agency": "CRISIL", "rating": "CRISIL AAA/Stable", "public_from": "2026-10-01T10:00", "isin": "",
         "issuer_id": "ISS1"}])
    assert rk.rating_detail(cp_rows, CP, "ISS1", as_of) == ("AAA", 1, "issuer")  # was (None, None): 'unrated'
    assert rk.rating_detail(cp_rows.iloc[:1], CP, "ISS1", as_of) == ("A+", LT_NOTCH["A+"], "short_term:A1+")
    ncd_rows = pd.DataFrame([
        {"agency": "ICRA", "rating": "[ICRA]AA+ (Stable)", "public_from": "2025-01-10T10:00", "isin": "INE222B07YY2",
         "issuer_id": ""},
        {"agency": "ICRA", "rating": "[ICRA]A (Negative)", "public_from": "2026-11-02T10:00", "isin": "",
         "issuer_id": "ISSNCD"}])
    assert rk.rating_as_known(ncd_rows, "INE222B07YY2", "ISSNCD", as_of) == ("A", LT_NOTCH["A"])  # was AA+
    before = datetime(2026, 11, 1, 20)  # the downgrade was not public yet
    assert rk.rating_as_known(ncd_rows, "INE222B07YY2", "ISSNCD", before) == ("AA+", LT_NOTCH["AA+"])


def _cp_book(*, issuers=None, ratings=None, curve_day=True):
    d, s = date(2026, 11, 20), date(2026, 11, 23)
    as_of = datetime(2026, 11, 20, 20)
    trades = pd.DataFrame([{"trade_id": "C1", "book": "b", "isin": CP, "kind": "cp", "side": "buy", "face": 2.5e8,
                            "trade_date": "2026-11-19", "maturity_date": "2027-02-19", "issue_date": "2026-11-19"}])
    market = rt.MarketData(corporate_terms=pd.DataFrame(NCD_TERMS), ratings=ratings,
                           ois_curves={d: rt.CurveEntry(fx.b3_curve(d), fx.at(d))} if curve_day else {})
    settings = rt.EngineSettings(access=rt.ACCESS_PRESETS["nbfc_non_pd"], project_calendar=True)
    cp = rt.instruments_as_known(market, settings, [CP], as_of, terms=trades)[CP].bond
    ncd = rt.instruments_as_known(market, settings, ["INE000A07011"], as_of)["INE000A07011"].bond
    rows = [fx.bond_row(ncd, "INE000A07011", "corporate", 7e8, d, s, ncd.yield_from_dirty_price(101.0, s),
                        source="valuation"), _cp_row(cp, 6e8, d, s, 6.4)]
    ctx = rk.RiskContext(market=market, settings=settings, risk=rk.RiskSettings(), as_of=as_of, terms=trades,
                         issuers=issuers, nav=pd.DataFrame([{"date": d, "book": "b", "nav": 1e10}]))
    return rk.risk_book(pd.DataFrame(rows), ctx), ctx


def test_a_cp_joins_its_issuers_ncds_and_takes_the_issuers_rating():
    book, _ = _cp_book()
    by_isin = {p.isin: p for p in book.positions}
    assert by_isin[CP].issuer_id == "ISS1" and "issuer_from_prefix" in by_isin[CP].flags  # was INE000A: two issuers
    jtd = rk.jtd_table(book)
    fund = jtd[jtd["scope_type"] == "fund"]
    assert list(fund["issuer_id"]) == ["ISS1"]
    assert float(fund["market_value"].iloc[0]) == pytest.approx(by_isin[CP].market_value
                                                                 + by_isin["INE000A07011"].market_value)
    # the issuer table's isin_prefix names the issuer explicitly (no flag), and its rating then reaches the CP
    ratings = pd.DataFrame([
        {"agency": "CRISIL", "rating": "CRISIL A1+", "public_from": "2026-10-01T10:00", "isin": CP, "issuer_id": ""},
        {"agency": "CRISIL", "rating": "CRISIL AAA/Stable", "public_from": "2026-10-01T10:00", "isin": "",
         "issuer_id": "ISSCP"}])
    issuers = pd.DataFrame([{"issuer_id": "ISSCP", "group_id": "G1", "sector": "financials",
                             "effective_from": "2020-01-01", "isin_prefix": "INE000A14"}])
    book2, ctx2 = _cp_book(issuers=issuers, ratings=ratings)
    cp = {p.isin: p for p in book2.positions}[CP]
    assert cp.issuer_id == "ISSCP" and "issuer_from_prefix" not in cp.flags
    assert cp.rating_bucket == "AAA" and cp.rating_source == "ratings"
    assert [sn.factor for sn in cp.sens if sn.basis == "spread"] == ["corp:AAA:0-1y"]  # was corp:unrated:0-1y
    ladder = [r for r in st.read_scenarios() if r.scenario_id == "CS_RATING_LADDER"]
    row = [r for r in st.apply_scenario(book2, ladder, st.StressContext(risk=ctx2)) if r["isin"] == CP][0]
    assert row["pnl"] == pytest.approx(-50.0 * cp.cs01, rel=2e-3)  # the AAA rung (50 bp), not the unrated 500 bp
    with pytest.raises(ValueError, match="ISIN prefix INE000A"):
        rk.read_issuers(pd.DataFrame([{"issuer_id": "A", "isin_prefix": "INE000A"},
                                      {"issuer_id": "B", "isin_prefix": "INE000A"}]))


# ---------------------------------------------------------------- CS01 without a curve, callable bonds


def test_a_credit_position_without_its_curve_keeps_a_cs01_and_a_spread_factor():
    d, s = date(2026, 11, 13), fx.S16
    market = rt.MarketData(corporate_terms=pd.DataFrame(NCD_TERMS), master=fx.MASTER)
    ctx = rk.RiskContext(market=market, settings=rt.EngineSettings(access=rt.ACCESS_PRESETS["nbfc_non_pd"]),
                         risk=rk.RiskSettings(), as_of=fx.at(d, 20))
    ncd = _ncd()
    book = rk.risk_book(pd.DataFrame([fx.bond_row(ncd, "INE000A07011", "corporate", 2e8, d, s, 9.1,
                                                  liquidity="")]), ctx)
    p = book.positions[0]
    assert "no_curve" in p.flags and "cs01_from_yield" in p.flags
    assert p.cs01 == pytest.approx(p.dv01_yield)  # was NaN: the CS01 limits read 0
    spread = [sn for sn in p.sens if sn.basis == "spread"]
    assert [sn.factor for sn in spread] == ["corp:AA:3-5y"] and spread[0].value == pytest.approx(p.dv01_yield)
    credit = rk.credit_table(book)
    assert float(credit[(credit["scope_type"] == "fund") & (credit["dimension"] == "rating_bucket")]["cs01"]
                 .iloc[0]) == pytest.approx(p.dv01_yield)


def test_a_callable_bond_is_flagged_and_in_the_money_when_priced_above_its_call():
    terms = [{**NCD_TERMS[0], "isin": "INE000C07033", "issuer_id": "ISSC", "coupon_pct": "10.0",
              "issue_date": "2023-03-01", "maturity_date": "2036-03-01", "rating_bucket": "AA+"}]
    options = pd.DataFrame([{"isin": "INE000C07033", "kind": "call", "date": "2027-03-01", "price_per100": 100.0}])
    market = rt.MarketData(corporate_terms=pd.DataFrame(terms), corporate_options=options,
                           curves={"gsec": {fx.D13: rt.CurveEntry(fx.nss(fx.S16), fx.at(fx.D13))}}, master=fx.MASTER)
    settings = rt.EngineSettings(access=rt.ACCESS_PRESETS["nbfc_non_pd"])
    ctx = rk.RiskContext(market=market, settings=settings, risk=rk.RiskSettings(), as_of=fx.at(fx.D13, 20))
    bond = rt.instruments_as_known(market, settings, ["INE000C07033"], ctx.as_of)["INE000C07033"].bond
    y = fx.on_curve_yield(bond, fx.nss(fx.S16), fx.S16, 0.02)  # 200 bp over the curve: priced well above par
    p = rk.risk_book(pd.DataFrame([fx.bond_row(bond, "INE000C07033", "corporate", 1e8, fx.D13, fx.S16, y)]),
                     ctx).positions[0]
    assert "callable_to_maturity" in p.flags and "callable_in_the_money" in p.flags  # were absent
    cheap = rk.risk_book(pd.DataFrame([fx.bond_row(bond, "INE000C07033", "corporate", 1e8, fx.D13, fx.S16,
                                                   y + 3.0)]), ctx).positions[0]
    assert "callable_to_maturity" in cheap.flags and "callable_in_the_money" not in cheap.flags


# ---------------------------------------------------------------- components, the stressed window, the strategy NAV


def test_two_pending_rows_of_one_isin_are_two_components_and_every_book_keeps_its_own():
    ctx, _, _ = fx.parallel_history_context(300, preset="nbfc_non_pd")
    d = fx.D13
    curve = ctx.market.curves["gsec"][d].curve
    s = curve.valuation_date
    y = fx.on_curve_yield(fx.GS2036, curve, s)

    def at(settle):
        return fx.GS2036.yield_from_dirty_price(fx.GS2036.dirty_price(y, s), settle)
    rows = [fx.bond_row(fx.GS2036, "GS2036", "gsec", 1e9, d, s, y),
            fx.bond_row(fx.GS2036, "GS2036", "gsec", 5e8, d, date(2026, 11, 18), at(date(2026, 11, 18)),
                        state="pending"),
            fx.bond_row(fx.GS2036, "GS2036", "gsec", 2e8, d, date(2026, 11, 20), at(date(2026, 11, 20)),
                        state="pending"),
            fx.bond_row(fx.GS2054, "GS2054", "gsec", 3e8, d, s, fx.on_curve_yield(fx.GS2054, curve, s), book="x")]
    book = rk.risk_book(pd.DataFrame(rows), ctx)
    hist = rk.factor_history(ctx, book)
    res = rk.var_results(book, hist, scopes=[("fund", "fund")])
    comp = rk.var_components(book, hist, results=res)
    for r in res:
        c = comp[(comp["method"] == r.method) & (comp["horizon_days"] == r.horizon_days)]
        pos, books = c[c["component_type"] == "position"], c[c["component_type"] == "book"]
        assert len(pos) == 4  # was 3: the two pending rows collided
        assert pos["var_contribution"].sum() == pytest.approx(r.var, rel=1e-9)
        assert pos["es_contribution"].sum() == pytest.approx(r.es, rel=1e-9)
        assert set(books["component"]) == {"b", "x"} and books["var_contribution"].sum() == pytest.approx(r.var,
                                                                                                         rel=1e-9)
    names = set(comp[comp["component_type"] == "position"]["component"])
    assert {"b|GS2036|pending|2026-11-18", "b|GS2036|pending|2026-11-20", "x|GS2054|settled|2026-11-16"} <= names


def test_the_stressed_window_ties_go_to_the_earliest_start():
    d, s = date(2026, 11, 20), date(2026, 11, 23)
    n = 700
    days = fx.history_days(d, n)
    rng = np.random.default_rng(11)
    moves = rng.normal(0.0, 3.0, n)
    moves[0] = 0.0
    moves[200:450] *= 3.0  # a volatile block: every window holding its worst 10-day runs has the same ES
    entries = fx.nss_entries(days, level_moves_bp=moves)
    spread_days = days[-320:]
    level = 150 + np.cumsum(rng.normal(0.0, 2.0, len(spread_days)))
    factors = pd.DataFrame({"date": spread_days, "factor": "corp:AA:3-5y", "value": level, "unit": "bp",
                            "public_from": "", "source": "synthetic"})
    settings = rt.EngineSettings(access=rt.ACCESS_PRESETS["nbfc_non_pd"], project_calendar=True)
    market = rt.MarketData(corporate_terms=pd.DataFrame(NCD_TERMS), curves={"gsec": entries}, master=fx.MASTER)
    as_of = datetime(2026, 11, 20, 20)
    ncd = rt.instruments_as_known(market, settings, ["INE000A07011"], as_of)["INE000A07011"].bond
    curve = entries[d].curve
    rows = [fx.bond_row(fx.GS2036, "GS2036", "gsec", 1e9, d, s, fx.on_curve_yield(fx.GS2036, curve, s)),
            fx.bond_row(ncd, "INE000A07011", "corporate", 3e8, d, s, ncd.yield_from_dirty_price(101.0, s),
                        source="valuation")]
    ctx = rk.RiskContext(market=market, settings=settings, risk=rk.RiskSettings(), as_of=as_of,
                         factor_history=factors)
    book = rk.risk_book(pd.DataFrame(rows), ctx)
    hist = rk.factor_history(ctx, book)
    start, _, es = rk.stressed_window(book, hist, horizon_days=10)
    # the reduced set is the G-sec key rates (the spread table starts later); each window's ES from its own runs
    cols = [j for j, f in enumerate(hist.factors) if f.startswith("gsec:")]
    sens = np.zeros(len(hist.factors))
    for item in book.items:
        for sn in item.sens:
            if sn.basis != "par" and sn.factor in hist.factors:
                sens[hist.factors.index(sn.factor)] += sn.value
    x = hist.changes[:, cols]
    rows_ok = np.flatnonzero(np.all(np.isfinite(x), axis=1))
    values = []
    for a in range(len(rows_ok) - 250 + 1):
        block = x[rows_ok[a:a + 250]]
        runs = np.array([block[i:i + 10].sum(axis=0) for i in range(241)])
        values.append(rk.hs_es(-(runs @ sens[cols]), 0.975))
    values = np.array(values)
    tied = np.flatnonzero(np.abs(values - values.max()) <= 1e-9 * values.max())
    assert len(tied) > 100  # the review's case: 156 windows tie
    assert start == hist.dates[rows_ok[tied[0]]] == date(2024, 5, 17)  # the earliest (was 2024-12-16)
    assert es == pytest.approx(values.max(), rel=1e-9)


def test_a_strategy_nav_is_the_same_whatever_the_order_of_its_books():
    nav = pd.DataFrame([{"date": date(2026, 11, 13), "book": b, "nav": v}
                        for b, v in (("b1", 1e16), ("b2", 1.0), ("b3", 1.0), ("b4", -1e16))])
    strategies = {"b1": "S", "b2": "S", "b3": "S", "b4": "S"}
    got = {rk._nav_map(nav.sample(frac=1.0, random_state=k), date(2026, 11, 13), list(perm), strategies)[
        ("strategy", "S")] for k, perm in enumerate((("b1", "b2", "b3", "b4"), ("b4", "b3", "b2", "b1"),
                                                     ("b2", "b4", "b1", "b3")))}
    assert got == {2.0}  # math.fsum of the four, in sorted order


# ---------------------------------------------------------------- the VaR history and the back-test


def _var_rows(day, scope, var, scope_type="book"):
    return pd.DataFrame([{"date": day, "as_of": "", "scope_type": scope_type, "scope": scope, "method": "parametric",
                          "horizon_days": 1, "horizon_rule": "", "window": "current", "alpha_var": a, "var": var,
                          "alpha_es": 0.975, "es": var, "n_obs": 250, "coverage": 1.0, "status": "ok"}
                         for a in (0.99, 0.975)])


def test_the_var_history_replaces_only_the_same_scope():
    day = date(2026, 11, 13)
    first = rk.append_var_history(None, _var_rows(day, "S1", 2e6))
    both = rk.append_var_history(first, _var_rows(day, "S7", 1e6))
    assert sorted(set(both["scope"])) == ["S1", "S7"] and len(both) == 4  # S1's rows were dropped before
    again = rk.append_var_history(both, _var_rows(day, "S1", 3e6))
    assert len(again) == 4 and set(again[again["scope"] == "S1"]["var"].astype(float)) == {3e6}


def _strategy_history(days, books=("b1",), strategy="S8", var=1e9):
    rows = []
    for d in days[:-1]:
        for b in books:
            rows += _var_rows(d, b, var).to_dict("records")
        rows += _var_rows(d, strategy, var, "strategy").to_dict("records")
        rows += _var_rows(d, "fund", var, "fund").to_dict("records")
    return pd.DataFrame(rows)


def test_a_strategys_back_test_sums_its_books_pnl():
    days = fx.CAL.business_days(date(2026, 11, 2), date(2026, 11, 13))
    hist = _strategy_history(days, books=("b", "c"))
    nav = pd.DataFrame([{"date": d, "book": b, "pnl": 1.0} for d in days for b in ("b", "c")])
    books = pd.DataFrame([{"book": "b", "strategy": "S8"}, {"book": "c", "strategy": "S8"}])
    detail, summary = rk.backtest(hist, nav, strategy_books=books)
    s8 = summary[(summary["scope_type"] == "strategy")].iloc[0]
    assert s8["exceptions"] == 0 and s8["zone"] == "green"  # was every day 'missing', all exceptions, red
    assert (detail[detail["scope_type"] == "strategy"]["pnl_actual"] == 2.0).all()
    # without the map the strategy has no P&L at all: not back-tested, never counted as exceptions
    _, plain = rk.backtest(hist, nav)
    s8 = plain[plain["scope_type"] == "strategy"].iloc[0]
    assert s8["observations"] == 0 and s8["exceptions"] == 0 and s8["zone"] == ""
    assert "not back-tested" in s8["notes"]


def test_a_book_holding_only_cash_has_a_hypothetical_pnl_of_zero():
    days = fx.CAL.business_days(date(2026, 11, 2), date(2026, 11, 13))
    hist = pd.DataFrame([r for d in days[:-1] for r in _var_rows(d, "c", 0.0).to_dict("records")])
    nav = pd.DataFrame([{"date": d, "book": b, "pnl": 0.0 if b == "c" else 5.0} for d in days for b in ("b", "c")])
    att = pd.DataFrame([{"date": d, "book": "b", "instrument": "X", "line": "curve_parallel", "amount": 5.0,
                         "memo": False} for d in days])
    detail, summary = rk.backtest(hist, nav, att)
    c = summary[summary["scope"] == "c"].iloc[0]
    assert c["exceptions_actual"] == 0 and c["exceptions_hypothetical"] == 0 and c["zone"] == "green"  # was 4 red
    assert (detail["pnl_hypothetical"] == 0.0).all() and not detail["missing"].any()
    # a day the attribution does not cover stays missing, an exception of the hypothetical series only
    short = att[att["date"] != days[3]]
    d2 = rk.backtest(hist, nav, short)[0].set_index("pnl_date")
    assert d2.loc[days[3], "missing"] and d2.loc[days[3], "exception_hypothetical"]
    assert not d2.loc[days[3], "exception_actual"]


def test_the_desk_rule_is_reported_at_both_levels():
    days = fx.CAL.business_days(date(2026, 1, 1), date(2026, 11, 13))
    hist = _strategy_history(days, books=("b",), var=10.0)
    nav = pd.DataFrame([{"date": d, "book": "b", "pnl": -11.0 if k % 15 == 0 else 1.0} for k, d in enumerate(days)])
    _, summary = rk.backtest(hist, nav)
    b = summary[summary["scope"] == "b"].iloc[0]
    assert "desk rule (MAR32.19, reported, not enforced)" in b["notes"] and "past 12 and 30" in b["notes"]


# ---------------------------------------------------------------- what the VaR leaves out, notes and horizons


def test_notes_say_what_the_var_leaves_out_and_every_unverified_rule():
    ctx, _, _ = fx.parallel_history_context(n=300)
    result = rk.run_risk(fx.gs2036_positions(), ctx)
    notes = "\n".join(result.notes)
    for text in ("leaves out carry and funding", "leaves out default", "leaves out exit costs",
                 "option value of callable bonds", "bond-specific basis", "floored at 0%", "liquidity horizons",
                 "investment grade", "no exception below six observations is green", "time effects"):
        assert text in notes, text
    flags = result.var["flags"].astype(str)
    assert flags.str.contains("no_residual_factor").all()  # a G-sec book without resid: factors
    assert rk.LIQUIDITY_HORIZONS["sdl_spread"] == 20 and rk.LIQUIDITY_HORIZONS["resid"] == 20  # MAR33.12: sovereign IG
