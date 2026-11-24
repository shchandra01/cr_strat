"""Regression tests for the review of the limits layer (limits.py, the gate and the pre-trade command): each test
reproduces a reviewer's failing case on the synthetic book of limits_fixtures and checks the corrected behaviour.
Nothing here is a real price, rating or limit."""

import contextlib
import io
import math
import random
from dataclasses import replace
from datetime import date, datetime

import limits_fixtures as lf
import pandas as pd
import pytest
import risk_fixtures as fx

from cr_strat import cli
from cr_strat import limits as lm
from cr_strat import returns as rt
from cr_strat import risk as rk
from cr_strat.ois import MIBOR_OIS, OisSwap, effective_date, ois_npv

# ---------------------------------------------------------------- the gate's cache


def test_the_gate_never_reads_a_freed_views_values():
    market, trades, res, ctx, rr = lf.engine_and_risk("nbfc_non_pd")
    state = lm.state_from_result(res, lf.END, ctx, risk=rr)
    limits = lf.limit_frame([lf.approved(limit_id="X1", metric="dv01", hard_level="1.55e6", warning_level="1.5e6")])
    rng = random.Random(7)
    sequences = []
    for _ in range(12):
        sequences.append([rt.Order(book="b", side=rng.choice(["buy", "buy", "sell"]),
                                   face=rng.choice([2e7, 5e7, 1e8]), isin="GS2036") for _ in range(16)])
    orders = sequences[10]
    _, rows = lm.PreTradeGate(limits, ctx, legal_form_rows=False).check(state, orders)
    first = {}
    for r in rows:
        first.setdefault(r["seq"], r)
    decisions = [first[s]["decision"] for s in sorted(first)]
    # each decision as a fresh gate takes it, the earlier approved orders queued (counted as filled)
    assert decisions[8:] == ["approved_with_warning", "refused", "refused", "approved", "approved",
                             "approved_with_warning", "approved", "approved_with_warning"]
    assert first[11]["post_value"] == pytest.approx(1597538.31, abs=0.01)  # order 11 as order 10: refused
    # the cache lives on each view: a view built where a freed one lived reads its own values
    view = lm._view(state, [], {})
    ident = id(view)
    first_values = lm._cached_values(view, {}, "dv01", "fund", "fund", "", "rs_per_bp")
    del view
    risked = lm.order_risk(state, rt.Order(book="b", side="buy", face=1e9, isin="GS2036"), seq=1)
    for _ in range(50):
        other = lm._view(state, [risked], {})
        if id(other) == ident:
            break
    values = lm._cached_values(other, {}, "dv01", "fund", "fund", "", "rs_per_bp")
    fresh = {k: (v, ok, d) for k, v, ok, d in lm.metric_values(other, "dv01", "fund", "fund", "", "rs_per_bp")}
    assert values == fresh and values != first_values


# ---------------------------------------------------------------- the breach log through a day not measured


def _checks(day, status, value):
    return pd.DataFrame([{"date": day, "as_of": "", "limit_id": "X1", "name": "x", "metric": "var",
                          "scope_type": "fund", "scope": "fund", "selector": "", "key": "", "value": value,
                          "unit": "rs", "direction": "max", "warning_level": 1e6, "hard_level": 1e6,
                          "utilisation": value / 1e6, "status": status, "policy_status": "APPROVED",
                          "legal_form": "any", "counsel": "no", "detail": ""}])


def test_a_breach_is_not_cured_by_a_day_its_metric_is_incomplete():
    limits = lm.read_limits(lf.limit_frame([lf.approved(limit_id="X1", metric="var", unit="rs", hard_level="1e6",
                                                        cure_business_days="5")]))
    days = [date(2026, 11, d) for d in (2, 3, 4, 5, 6, 9, 11, 12)]
    status = ["breach", "breach", "breach", "incomplete", "breach", "breach", "breach", "breach"]
    log = None
    for d, s in zip(days, status):
        log = lm.update_breach_log(log, _checks(d, s, 2e6 if s == "breach" else math.nan),
                                   as_of=datetime(d.year, d.month, d.day, 20), limits=limits)
        if d == date(2026, 11, 5):
            mid = log.iloc[-1]
            assert mid["status"] == "continuing" and pd.isna(mid["closed_date"])  # was 'cured' on 5 Nov
            assert "not measured on 2026-11-05 (incomplete)" in mid["notes"]
    assert len(log) == 1  # one episode, not a fresh one from 6 Nov
    last = log.iloc[0]
    assert last["first_date"] == date(2026, 11, 2) and last["cure_due"] == date(2026, 11, 9)
    assert last["status"] == "escalated"  # past its cure date of 9 Nov (was only 'continuing', day 4)
    # a day within the level cures it as before
    log = lm.update_breach_log(log, _checks(date(2026, 11, 13), "ok", 5e5), as_of=datetime(2026, 11, 13, 20),
                               limits=limits)
    assert log.iloc[0]["status"] == "cured" and log.iloc[0]["closed_date"] == date(2026, 11, 13)


# ---------------------------------------------------------------- selector values


def test_selector_values_are_checked_and_put_in_the_risk_tables_form():
    market, trades, res, ctx, rr = lf.engine_and_risk("nbfc_non_pd")
    for selector, hint in (("curve=gsc;tenor=10y", "did you mean 'gsec'"), ("curve=gsec;tenor=12y", "12y"),
                           ("curve=gsec;tenor=ten", "ten")):
        with pytest.raises(ValueError, match=f"line 2: .*{hint}"):
            lm.read_limits(lf.limit_frame([lf.approved(metric="krd", selector=selector)]))
    with pytest.raises(ValueError, match="rating_bucket 'BBB\\+' is not one of"):
        lm.read_limits(lf.limit_frame([lf.approved(metric="cs01", selector="rating_bucket=BBB+")]))
    with pytest.raises(ValueError, match="kind 'bond' is not one of"):
        lm.read_limits(lf.limit_frame([lf.approved(metric="exposure", unit="pct_nav", selector="kind=bond")]))
    rows = [lf.approved(limit_id="K2", metric="krd", selector="curve=gsec;tenor=10", hard_level="1e5"),
            lf.approved(limit_id="C2", metric="cs01", selector="rating_bucket=aa", hard_level="1e4"),
            lf.approved(limit_id="E2", metric="exposure", unit="pct_nav", selector="sector=Financials",
                        hard_level="5"),
            lf.approved(limit_id="E3", metric="exposure", unit="pct_nav", selector="kind=Corporate", hard_level="5")]
    table = lm.read_limits(lf.limit_frame(rows))
    assert list(table["selector"]) == ["curve=gsec;tenor=10y", "rating_bucket=AA", "sector=financials",
                                       "kind=corporate"]
    checks = lm.check_limits(lm.metrics_frame(lm.metric_inputs(rr, res, ctx, day=lf.END), table), table,
                             as_of=ctx.as_of, legal_form=ctx.settings.access).set_index("limit_id")
    assert checks.loc["K2", "value"] == pytest.approx(480335.62, abs=0.01)  # each read 0.00 and 'ok' before
    assert checks.loc["C2", "value"] == pytest.approx(73542.09, abs=0.01)
    assert checks.loc["E2", "value"] == pytest.approx(7.28, abs=0.005) and checks.loc["E3", "value"] > 0
    assert set(checks["status"]) == {"breach"}


# ---------------------------------------------------------------- CS01 not known, utilisation edges, CRAR


def test_a_credit_position_without_a_cs01_makes_the_cs01_limit_incomplete():
    market, trades = lf.market_and_trades()[:2]
    ctx = lf.context(market, "nbfc_non_pd", trades=trades)
    ncd = lf.ncd_bond()
    s = fx.CAL.settlement_date(lf.END, 1)
    row = fx.bond_row(ncd, lf.NCD, "corporate", 2e8, lf.END, s, 9.1, book="c")
    state = lf.state([row], {"c": (5e8, 3e8)}, ctx)
    view = lm._view(state, [], {})
    positions = view.positions.copy()
    positions.loc[positions["isin"] == lf.NCD, "cs01"] = math.nan  # a risk run whose CS01 could not be split out
    blind = replace(view, positions=positions, _rows=None, _values={})
    (key, value, ok, detail), = lm.metric_values(blind, "cs01", "fund", "fund", "", "rs_per_bp")
    assert not ok and "CS01 not known for INE000A07011" in detail  # was 0.0 with inputs complete
    (_, value, ok, _), = lm.metric_values(view, "cs01", "fund", "fund", "", "rs_per_bp")
    assert ok and value > 0  # the risk layer itself gives a CS01 now (the yield proxy without a curve)


def test_utilisation_signs_at_the_edges():
    assert lm.utilisation(-5.0, 10.0, "min") == math.inf  # a minimum of 10 missed by a value below zero (was -2.0)
    assert lm.utilisation(0.0, 10.0, "min") == math.inf
    assert lm.utilisation(-3.0, -2.0, "max") == pytest.approx(2 / 3)  # a maximum below zero that holds (was 1.5)
    assert lm.utilisation(-1.0, -2.0, "max") == 2.0 and lm.utilisation(0.5, -2.0, "max") == math.inf
    assert lm.utilisation(5.0, -8.0, "min") == 0.0  # a drawdown limit while the index is at a peak


def test_bank_cds_carry_no_risk_weight_and_a_pfi_cd_full_weight():
    weights = lm.read_risk_weights().set_index(["kind", "sector"])["risk_weight_pct"]
    assert weights[("cd", "")] == 0 and weights[("cd", "pfi")] == 100  # item (1) and item (2)(c) of the table
    market, trades = lf.market_and_trades()[:2]
    cd = "INE000CD0011"
    master = pd.DataFrame([*lf.MASTER.to_dict("records"),
                           {"isin": cd, "kind": "cd", "maturity_date": "2027-05-14", "issue_date": "2026-05-14"}])
    market = replace(market, master=master)
    ctx = lf.context(market, "nbfc_non_pd", trades=trades)
    s = fx.CAL.settlement_date(lf.END, 1)
    bond = rt.instruments_as_known(market, ctx.settings, [cd], fx.at(lf.END, 20))[cd].bond
    price = bond.price(7.0, s)
    cd_row = {"date": lf.END, "book": "b", "isin": cd, "kind": "cd", "state": "settled", "face": 6e9,
              "settlement_date": s, "clean_mark": price, "mark_source": "traded", "mark_age_days": 0,
              "yield_pct": 7.0, "accrued": 0.0, "value": 6e9 / 100 * price, "dv01": 6e9 / 100 * bond.dv01(7.0, s),
              "bucket": "", "liquidity": "", "haircut_pct": "", "funded": "", "stale_warning": False,
              "extrapolated": False, "calendar_projected": False, "notes": ""}
    ncd = lf.ncd_bond()
    ncd_row = fx.bond_row(ncd, lf.NCD, "corporate", 4e9, lf.END, s,
                          fx.on_curve_yield(ncd, lf.fits()[2][lf.END].curve, s, 0.01), book="b")
    state = lf.state([cd_row, ncd_row], {"b": (1.5e9, 0.0)}, ctx)
    view = lm._view(state, [], {})
    ncd_mv = [p.market_value for p in state.book.positions if p.isin == lf.NCD][0]
    (_, crar, ok, detail), = lm.metric_values(view, "crar", "fund", "fund", (), "pct")
    assert crar == pytest.approx(1.5e9 / ncd_mv * 100, rel=1e-12)  # 33.85%, was 14.65% with the CD at 100%
    assert "CDs taken as banks'" in detail and cd in detail
    issuers = pd.DataFrame([{"issuer_id": "PFI1", "sector": "pfi", "isin_prefix": "INE000CD"}])
    state_p = lf.state([cd_row, ncd_row], {"b": (1.5e9, 0.0)}, replace(ctx, issuers=issuers))
    (_, crar_p, _, _), = lm.metric_values(lm._view(state_p, [], {}), "crar", "fund", "fund", (), "pct")
    assert crar_p == pytest.approx(1.5e9 / (ncd_mv + cd_row["value"]) * 100, rel=1e-9)  # a PFI's CD: 100%


# ---------------------------------------------------------------- the hedging-only test (L06)


def _payer(state, market, months):
    dv01 = state.book.positions[0].dv01_yield
    curve = market.ois_curves[lf.END].curve
    cal = fx.CAL.lenient()
    start = effective_date(cal.add_business_days(lf.END, 1), MIBOR_OIS, cal)
    unit = ois_npv(curve, OisSwap(effective=start, months=months, fixed_rate_pct=6.0, notional=1e7,
                                  pay_fixed=True), calendar=cal).dv01
    return rt.Order(book="b", side="pay", face=round(dv01 / abs(unit) * 1e7, 0), months=months)


def test_a_long_bond_hedged_at_the_ois_curves_end_passes_the_hedging_only_test():
    market, trades = lf.ois_market()
    ctx = lf.context(market, "unregulated_company", trades=trades, project_calendar=True)
    state = lf.state([lf.gsec_row("GS2036", 1e9)], {"b": (2e9, 1e9)}, ctx, scenarios=lf.scenario_rows())
    approved, rows = lm.PreTradeGate(None, ctx).check(state, [_payer(state, market, 54)])
    frame = lm.gate_frame(rows)
    assert list(frame["decision"]) == ["approved"] and len(approved) == 1  # was refused by L06 at 1.691393
    risked = lm.order_risk(state, _payer(state, market, 54), seq=1)
    (_, ratio, ok, detail), = lm.metric_values(lm._view(state, [risked], {}), "ois_hedge_effect", "book", "b", "",
                                               "ratio")
    assert ok and ratio == pytest.approx(0.502672, abs=1e-6) and "beyond 5 years netted" in detail


def test_the_hedging_only_test_binds_before_the_var_has_history():
    market, trades = lf.ois_market()
    days, _, entries = lf.fits()
    keep = set(days[-40:])  # 40 days of curves: fewer than the 60 changes a VaR needs
    short = replace(market, curves={"gsec": {d: e for d, e in entries.items() if d in keep}},
                    ois_curves={d: e for d, e in market.ois_curves.items() if d in keep})
    settings = rt.EngineSettings(access=rt.ACCESS_PRESETS["unregulated_company"], project_calendar=True)
    ctx = rk.RiskContext(market=short, settings=settings, risk=rk.RiskSettings(min_obs_hard=60),
                         as_of=fx.at(lf.END, 20), terms=trades, nss_table={"gsec": short.curves["gsec"]})
    state = lf.state([lf.gsec_row("GS2031", 1e9)], {"b": (1.5e9, 5e8)}, ctx, scenarios=lf.scenario_rows())
    _, rows = lm.PreTradeGate(None, ctx).check(state, [_payer(state, short, 12)])
    frame = lm.gate_frame(rows).set_index("limit_id")
    assert frame["decision"].iloc[0] == "refused"  # was approved_with_warning: L06 'incomplete'
    assert frame.loc["L06", "post_value"] == pytest.approx(1.938899, abs=1e-6)
    assert "the key-rate test decides" in lm.metric_values(
        lm._view(state, [lm.order_risk(state, _payer(state, short, 12), seq=1)], {}), "ois_hedge_effect", "book",
        "b", "", "ratio")[0][3]


def test_the_gates_var_leaves_out_a_factor_whose_history_stopped():
    days = fx.history_days(lf.END, 120)
    wide = pd.DataFrame({"gsec:10y": [1.0, -1.0] * 60, "corp:AA:3-5y": [0.5, -0.5] * 40 + [math.nan] * 40},
                        index=days)
    sens = pd.DataFrame([{"factor": "gsec:10y", "sensitivity": 1e5, "basis": "zero"},
                         {"factor": "corp:AA:3-5y", "sensitivity": 1e4, "basis": "spread"}])
    settings = rk.RiskSettings(min_obs_hard=60)
    var, _, coverage, n, detail = lm.parametric_from_frames(sens, wide, settings, day=lf.END)
    assert n == 120 and coverage == pytest.approx(1e5 / 1.1e5) and "stale factor(s) left out: corp:AA:3-5y" in detail
    var_old, _, coverage_old, n_old, _ = lm.parametric_from_frames(sens, wide, settings)  # without the day
    assert n_old == 80 and coverage_old == 1.0  # the old rule: the window cut to the stopped series' dates
    with pytest.raises(ValueError, match="stale: corp:AA:3-5y"):  # its only factor stopped 40 days ago
        lm.parametric_from_frames(sens.iloc[1:], wide, rk.RiskSettings(min_obs_hard=40), day=lf.END)
    # two current factors whose common dates end ten days before the book's day: refused, never an old window
    apart = pd.DataFrame({"gsec:10y": [1.0, -1.0] * 55 + [math.nan] * 9 + [1.0],
                          "corp:AA:3-5y": [0.5, -0.5] * 55 + [math.nan] * 8 + [0.5, math.nan]}, index=days)
    with pytest.raises(ValueError, match="with every factor end on"):
        lm.parametric_from_frames(sens, apart, settings, day=lf.END)


# ---------------------------------------------------------------- S9's own rating floor; the pre-trade day


def test_a_strategy_held_to_its_own_rating_floor_may_buy_below_the_funds():
    market, trades = lf.market_and_trades()[:2]
    ratings = pd.DataFrame([{"agency": "CARE", "rating": "CARE BBB+", "public_from": "2026-11-02T10:00",
                             "issuer_id": "ISS1"}])
    books = pd.DataFrame([{"book": "c", "strategy": "S9"}, {"book": "b", "strategy": "S8"}])
    ctx = lf.context(replace(market, ratings=ratings), "nbfc_non_pd", trades=trades, strategy_books=books)
    state = lf.state([lf.gsec_row("GS2036", 1e9)], {"b": (1.5e9, 5e8), "c": (5e8, 3e8)}, ctx)
    limits = lf.limit_frame([
        lf.approved(limit_id="F1", metric="rating_floor", unit="notch", selector="of=purchases;exclude_strategy=S9",
                    hard_level="A-"),
        lf.approved(limit_id="F9", metric="rating_floor", unit="notch", selector="of=purchases",
                    scope_type="strategy", scope="S9", hard_level="BBB-")])
    orders = [rt.Order(book="c", side="buy", face=1e7, isin=lf.NCD), rt.Order(book="b", side="buy", face=1e7,
                                                                               isin=lf.NCD)]
    _, rows = lm.PreTradeGate(limits, ctx).check(state, orders)
    frame = lm.gate_frame(rows)
    first = frame[frame["seq"] == 1]
    assert first["decision"].iloc[0] == "approved"  # S9 under its own BBB- floor (F1 refused it before)
    second = frame[frame["seq"] == 2].set_index("limit_id")
    assert second.loc["F1", "decision"] == "refused" and "past the hard floor notch 7 (A-)" in second.loc["F1",
                                                                                                          "reason"]


def test_pre_trade_refuses_a_day_the_engine_holds_no_book(tmp_path):
    market, trades, res, ctx, rr = lf.engine_and_risk("nbfc_non_pd")
    engine = tmp_path / "engine"
    rt.write_result(res, engine)
    with pytest.raises(ValueError, match="nothing to decide against: no NAV on 2026-11-24"):
        lm.state_from_result(engine, date(2026, 11, 24), ctx)
    lf.ORDERS.to_csv(tmp_path / "orders.csv", index=False)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(["pre-trade", "--orders", str(tmp_path / "orders.csv"), "--engine-dir", str(engine),
                         "--date", "2026-11-24", "--limits", "example", "--allow-example-limits", "--access",
                         "nbfc_non_pd", "--out-dir", str(tmp_path / "gate")])
    assert code == 1 and "nothing to decide against: no NAV on 2026-11-24" in out.getvalue()  # was exit 0
    assert not (tmp_path / "gate" / "gate.csv").exists()
