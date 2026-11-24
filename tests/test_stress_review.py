"""Tests of the stress and liquidity fixes of the risk layer's review: liquidity netted by ISIN with the scope's cash
at day 0, the ADV averaged over the days the marks cover, FRTB's buckets for a fund's sector names and for the
sovereign and local-government buckets, an FRB's own yield moving its index, the rating ladder's missing steps
flagged, and the H2022 short-end proxy marked unverified. Synthetic data only."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime

import pandas as pd
import pytest
import limits_fixtures as lf
import risk_fixtures as fx

from cr_strat import limits as lm
from cr_strat import returns as rt
from cr_strat import risk as rk
from cr_strat import stress as st

ADV_TABLE = pd.DataFrame([{"date": "2026-11-01", "isin": "GS2054", "kind": "", "liquidity": "", "adv_cr": 10.0,
                           "public_from": "2026-11-01T18:00", "source": "test"}])


def _gs2054(face, *, book="b", state="settled", settle=fx.S16):
    curve = fx.nss(fx.S16)
    y = fx.on_curve_yield(fx.GS2054, curve, fx.S16)
    return fx.bond_row(fx.GS2054, "GS2054", "gsec", face, fx.D13, settle, y, book=book, state=state)


def _liquidity(rows, navs: dict):
    """``(detail, fund summary row)`` of ``rows`` with ``{book: (nav, cash)}`` and an ADV of Rs 10 crore a day."""
    nav = pd.DataFrame([{"date": fx.D13, "book": b, "nav": n, "cash": c, "pnl": 0.0, "flow": 0.0}
                        for b, (n, c) in navs.items()])
    ctx = fx.gsec_context(fx.D13, adv=ADV_TABLE, nav=nav)
    book = rk.risk_book(pd.DataFrame(rows), ctx, day=fx.D13)
    detail, summary = st.liquidity(book, ctx)
    return detail, summary[summary.scope_type == "fund"].iloc[0]


def test_one_isin_in_two_books_is_one_position_to_liquidate():
    # Rs 200 crore of GS2054 at Rs 10 crore a day and 20% participation: 100 days, whether in one book or two
    _, whole = _liquidity([_gs2054(2e9)], {"b": (5e9, 0.0)})
    detail, split = _liquidity([_gs2054(1e9, book="b"), _gs2054(1e9, book="c")], {"b": (2.5e9, 0.0),
                                                                                    "c": (2.5e9, 0.0)})
    assert whole["max_days"] == pytest.approx(100.0)
    assert split["max_days"] == pytest.approx(100.0)  # was 50: each row had the whole ADV to itself
    assert split["share_5d"] == pytest.approx(whole["share_5d"])
    assert list(detail["days_to_liquidate"]) == pytest.approx([50.0, 50.0])  # each book's own net position


def test_a_pending_sale_shortens_the_days_of_the_holding_it_sells():
    settle = fx.CAL.settlement_date(fx.D13, 1)
    rows = [_gs2054(2e9), _gs2054(-1e9, state="pending", settle=settle)]
    detail, fund = _liquidity(rows, {"b": (5e9, 0.0)})
    assert fund["max_days"] == pytest.approx(50.0)  # Rs 100 crore left to sell, was 100 days for the Rs 200 crore
    assert list(detail["days_to_liquidate"]) == pytest.approx([50.0, 50.0])
    sale = detail[detail["face"] < 0].iloc[0]["value"]  # the pending sale's value, negative
    held = detail["value"].sum()  # the net holding
    # 5 days of 50: a tenth of the net holding, plus the sale's proceeds as cash on a trade-date view
    assert fund["share_5d"] == pytest.approx((held * 0.1 - sale) / 5e9)


def test_the_scopes_cash_is_saleable_at_day_zero():
    row = _gs2054(2e9)
    value = row["value"]
    _, fund = _liquidity([row], {"b": (value + 1e9, 1e9)})
    assert fund["share_1d"] == pytest.approx((1e9 + value / 100) / (value + 1e9))  # was value / 100 / NAV
    assert fund["share_20d"] == pytest.approx((1e9 + value / 5) / (value + 1e9))


def test_an_all_cash_book_is_all_liquid_for_the_liquid_share_limit():
    market, trades = lf.market_and_trades()[:2]
    ctx = lm.with_cost_table(lf.context(market, "unregulated_company", trades=trades))
    state = lf.state([], {"b": (3e9, 3e9)}, ctx)
    sel = lm.parse_selector("days=5", "liquid_share")
    values = lm.metric_values(lm._view(state, [], {}), "liquid_share", "fund", "fund", sel, "pct_nav")
    assert values[0][1] == pytest.approx(100.0)  # was 0: cash was out of the numerator, in the NAV


def test_the_gate_adds_up_orders_of_one_isin_for_days_to_liquidate():
    market, trades = lf.market_and_trades()[:2]
    ctx = lm.with_cost_table(lf.context(market, "unregulated_company", trades=trades))
    state = lf.state([lf.gsec_row("GS2036", 1e9)], {"b": (3e9, 2e9)}, ctx)
    sel = lm.parse_selector("stat=max", "days_to_liquidate")

    def days(orders):
        items = [lm.order_risk(state, o, seq=k + 1) for k, o in enumerate(orders)]
        return lm.metric_values(lm._view(state, items, {}), "days_to_liquidate", "fund", "fund", sel, "days")[0][1]

    four = days([rt.Order(book="b", side="buy", face=5e7, isin=lf.NCD) for _ in range(4)])
    one = days([rt.Order(book="b", side="buy", face=2e8, isin=lf.NCD)])
    assert four == pytest.approx(one)  # Rs 20 crore in four orders is Rs 20 crore to sell
    assert four > days([rt.Order(book="b", side="buy", face=5e7, isin=lf.NCD)])


def _marks(n_days: int, volume_cr: float, end=fx.D13):
    curve = fx.nss(fx.S16)
    y = fx.on_curve_yield(fx.GS2054, curve, fx.S16)
    return pd.DataFrame([{"date": d, "isin": "GS2054", "clean_price": fx.GS2054.clean_price(y, fx.S16),
                          "source": "traded", "volume_cr": volume_cr, "public_from": fx.at(d, 18).isoformat()}
                         for d in fx.history_days(end, n_days)])


def _adv(marks):
    curves = {"gsec": {fx.D13: rt.CurveEntry(fx.nss(fx.S16), fx.at(fx.D13))}}
    ctx = fx.gsec_context(fx.D13, market=rt.MarketData(curves=curves, master=fx.MASTER, marks=marks))
    book = rk.risk_book(pd.DataFrame([_gs2054(1e9)]), ctx, day=fx.D13)
    return st.adv_of(book, ctx)[book.positions[0].key]


def test_adv_from_marks_is_averaged_over_the_days_the_marks_cover():
    adv, source, flags = _adv(_marks(10, 5.0))
    assert source == "marks" and adv == pytest.approx(5e7)  # Rs 50 crore over 10 days, was over 20 (Rs 2.5 crore)
    assert flags == ("short_adv_window:10d",)
    adv, source, flags = _adv(_marks(25, 5.0))
    assert source == "marks" and adv == pytest.approx(5e7) and flags == ()  # the full window: no flag


def test_marks_covering_too_few_days_give_way_to_a_proxy():
    adv, source, flags = _adv(_marks(3, 5.0))
    assert source == "proxy_class" and adv == pytest.approx(st.ADV_PROXIES["gsec_liquid"])
    assert "short_adv_window:3d" in flags and "proxy_adv" in flags


def _ncd_book(sector: str, frtb_sector: str = ""):
    """The paper fund's book on END with the NCD's issuer in the issuer table under ``sector`` / ``frtb_sector``."""
    _, _, res, ctx, _ = lf.engine_and_risk("unregulated_company")
    issuers = pd.DataFrame([{"issuer_id": "ISS1", "name": "Issuer one", "group_id": "G1", "sector": sector,
                             **({"frtb_sector": frtb_sector} if frtb_sector else {})}])
    ctx = replace(ctx, issuers=issuers)
    book = rk.risk_book(res.positions[res.positions["date"] == lf.END], ctx, day=lf.END)
    ncd = next(p for p in book.positions if p.isin == lf.NCD)
    return replace(book, positions=(ncd,), swaps=()), ncd, ctx


def _spread_pnl(book, rows, ctx) -> float:
    return st.apply_scenario(book, rows, st.StressContext(risk=ctx))[0]["pnl_spread"]


def _shock(size_bp: float):
    return st.read_scenarios(pd.DataFrame([{"scenario_id": "X", "family": "credit", "component": "corp_spread",
                                            "shape": "add", "size_bp": size_bp, "status": "U", "source": "test"}]))


def test_a_public_sector_bank_is_a_financial_for_frtb_and_the_financials_stress():
    book, ncd, ctx = _ncd_book("psu_bank")
    assert ncd.sector == "psu_bank" and st._attributes(ncd)["frtb_sector"] == "financials"  # was 'other'
    scen = st.scenarios_by_id(st.read_scenarios())
    assert _spread_pnl(book, scen["FRTB_CSR"], ctx) == pytest.approx(_spread_pnl(book, _shock(500.0), ctx))
    # the ladder's 100 bp for AA and the financials' own 100 bp on top
    assert _spread_pnl(book, scen["CS_FINANCIALS_X2"], ctx) == pytest.approx(_spread_pnl(book, _shock(200.0), ctx))
    # an issuer table's frtb_sector wins over the fund's sector name
    book, ncd, ctx = _ncd_book("psu_bank", "technology_telecoms")
    assert _spread_pnl(book, scen["FRTB_CSR"], ctx) == pytest.approx(_spread_pnl(book, _shock(200.0), ctx))


@pytest.mark.parametrize("bucket, ig_bp, hy_bp", [("sovereign", 50.0, 200.0), ("local_government", 100.0, 400.0),
                                                   ("financials", 500.0, 1200.0), ("covered", 250.0, 250.0),
                                                   ("other", 1200.0, 1200.0)])
def test_frtb_csr_shocks_every_bucket_by_mar21_53(bucket, ig_bp, hy_bp):
    book, ncd, ctx = _ncd_book(bucket)
    frtb = st.scenarios_by_id(st.read_scenarios())["FRTB_CSR"]
    for rating, size in (("A", ig_bp), ("SUB_IG", hy_bp)):
        item = replace(ncd, rating_bucket=rating)
        b = replace(book, positions=(item,))
        assert _spread_pnl(b, frtb, ctx) == pytest.approx(_spread_pnl(b, _shock(size), ctx)) != 0.0


def test_every_frtb_bucket_name_gets_a_shock():
    frtb = st.scenarios_by_id(st.read_scenarios())["FRTB_CSR"]
    for bucket in (*st.FRTB_SECTORS, "other"):
        book, _, ctx = _ncd_book(bucket)
        assert _spread_pnl(book, frtb, ctx) < 0.0, bucket


def test_a_downgrade_the_ladder_gives_no_step_is_flagged():
    book, ncd, ctx = _ncd_book("financials")
    down = st.scenarios_by_id(st.read_scenarios())["DOWNGRADE_1_NOTCH"]
    sctx = st.StressContext(risk=ctx)
    for rating, no_step in (("AA+", True), ("A+", True), ("A", True), ("AA", False), ("A-", False)):
        b = replace(book, positions=(replace(ncd, rating_bucket=rating),))
        row = st.apply_scenario(b, down, sctx)[0]
        assert ("ladder_no_step" in row["flags"].split(";")) is no_step, rating
        assert (row["pnl_spread"] == 0.0) is no_step, rating
    b = replace(book, positions=(replace(ncd, rating_bucket="AA+"),))
    table = st.run_stress(b, down, sctx)[0]
    flagged = table[(table["scope_type"] == "fund") & (table["component"] == "total")]["flags"].iloc[0]
    assert "ladder_no_step" in flagged


def _frb_book():
    st_ = rt.EngineSettings(access=rt.ACCESS_PRESETS["nbfc_non_pd"], frb_allow_fewer=True, frb_allow_stale=True)
    as_of = datetime(2026, 11, 20, 20)
    bond = rt.instruments_as_known(rt.MarketData(), st_, [fx.FRB2031], as_of)[fx.FRB2031].bond
    s = date(2026, 11, 23)
    y = bond.yield_from_price(100.25, s)
    ctx = rk.RiskContext(market=rt.MarketData(), settings=st_, risk=rk.RiskSettings(), as_of=as_of)
    return rk.risk_book(pd.DataFrame([fx.bond_row(bond, fx.FRB2031, "frb", 1e8, date(2026, 11, 20), s, y)]), ctx), ctx


def test_an_frbs_own_yield_up_moves_its_index_not_its_margin():
    book, ctx = _frb_book()
    sctx = st.StressContext(risk=ctx)
    rows = st.read_scenarios(pd.DataFrame([
        {"scenario_id": "I", "family": "frb", "component": "frb_index", "shape": "add", "size_bp": 100.0},
        {"scenario_id": "D", "family": "frb", "component": "frb_dm", "shape": "add", "size_bp": 100.0}]
        ).assign(status="U", source="test"))
    ids = st.scenarios_by_id(rows)
    index = st.apply_scenario(book, ids["I"], sctx)[0]["pnl"]
    margin = st.apply_scenario(book, ids["D"], sctx)[0]["pnl"]
    up = st.apply_scenario(book, st.scenarios_by_id(st.read_scenarios())["YIELD_UP_100"], sctx)[0]["pnl"]
    assert up == pytest.approx(index, rel=1e-9)  # was the margin's +100 bp
    assert abs(margin) > 5 * abs(up)


def test_the_h2022_short_end_proxy_row_is_unverified():
    rows = [r for r in st.read_scenarios() if r.scenario_id == "H2022_HIKES"]
    short = [r for r in rows if r.component == "all_rate_curves" and r.tenor_years == 0.25]
    assert len(short) == 1 and short[0].status == "U"
    assert any(r.component == "repo_rate" and r.status == "V" for r in rows)  # the repo move itself is verified


def test_an_auction_tail_example_is_shipped_for_s4():
    by = st.scenarios_by_id(st.read_scenarios())
    tail = by["AUCTION_TAIL_10Y"]
    assert {(r.tenor_years, r.size_bp) for r in tail} == {(7.0, 0.0), (10.0, 10.0), (15.0, 0.0)}
    assert all(r.status == "U" and r.component == "gsec_curve" for r in tail)


def test_without_a_nav_table_the_cash_is_the_books_nav_less_its_holdings():
    # a back-test's snapshot: the book's NAV is known, the NAV table's cash column is not
    ctx = fx.gsec_context(fx.D13, adv=ADV_TABLE)
    book = rk.risk_book(pd.DataFrame([_gs2054(2e9)]), ctx, day=fx.D13)
    value = book.positions[0].market_value
    nav = value + 1e9
    book = replace(book, nav={("fund", "fund"): nav, ("book", "b"): nav, ("strategy", "b"): nav})
    fund = st.liquidity(book, ctx)[1].iloc[0]
    assert fund["share_1d"] == pytest.approx((1e9 + value / 100) / nav)  # Rs 100 crore of cash at day 0
