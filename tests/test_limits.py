"""Tests of the limits layer (spec section 5): the limit table and its refusals, limits in force (dates, approval,
legal form), the metrics on a synthetic engine and risk run, checks and utilisation, the breach log, the legal-form
rules and the pre-trade gate (L5.6), the gated back-test (D7.3, identity I10), look-ahead (I11), determinism (I12) and
the legal form (I13). Synthetic data only (limits_fixtures, risk_fixtures)."""

import filecmp
import math
import re
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import limits_fixtures as lf
import pandas as pd
import pytest
import risk_fixtures as fx

from cr_strat import limits as lm
from cr_strat import portfolio as pf
from cr_strat import returns as rt
from cr_strat import risk as rk
from cr_strat.ois import MIBOR_OIS, OisSwap, effective_date, ois_npv

UNREG = rt.ACCESS_PRESETS["unregulated_company"]
NBFC = rt.ACCESS_PRESETS["nbfc_non_pd"]
AS_OF = fx.at(lf.END, 20)


# ---------------------------------------------------------------- the limit table (L5.2)


def test_the_limit_table_refuses_bad_rows_naming_the_line():
    good = lf.limit_frame([lf.approved()])
    assert len(lm.read_limits(good)) == 1
    cases = [
        ([lf.approved(), lf.approved()], "limit_id X1 is used twice"),
        ([lf.approved(metric="dv1")], "metric 'dv1' is not one the code knows (did you mean 'dv01'"),
        ([lf.approved(unit="pct")], "unit 'pct' does not apply to dv01"),
        ([lf.approved(selector="buckt=*")], "selector key 'buckt' does not apply to dv01"),
        ([lf.approved(selector="bucket=9-99y")], "bucket '9-99y' is not one of"),
        ([lf.approved(warning_level="2e9")], "warning level 2e+09 is above the hard level 1e+09 of a maximum"),
        ([lf.approved(metric="nof", unit="rs", direction="min", warning_level="1", hard_level="5")],
         "warning level 1 is below the hard level 5 of a minimum"),
        ([lf.approved(hard_level="")], "hard_level is blank"),
        ([lf.approved(legal_form="nbfc")], "legal_form 'nbfc' is not one of"),
        ([lf.approved(action="stop")], "action 'stop' is not one of"),
        ([lf.approved(policy_status="")], "policy_status '' must be one of"),
        ([lf.approved(effective_to="2026-01-01", effective_from="2026-02-01")], "is not after effective_from"),
        ([lf.approved(), lf.approved(limit_id="X2", effective_from="2026-06-01")],
         "limits X1 (line 2) and X2 (line 3) bind the same metric, scope and selector"),
        ([lf.approved(selector="abs=maybe")], "abs is yes or no"),
        ([lf.approved(scope_type="book", scope="")], "a book limit needs a scope"),
    ]
    for rows, message in cases:
        with pytest.raises(ValueError, match=re.escape(message)):
            lm.read_limits(lf.limit_frame(rows))
    bad = lf.limit_frame([lf.approved()]).assign(colour="red")
    with pytest.raises(ValueError, match="unknown columns"):
        lm.read_limits(bad)
    # versions of one limit with spans that meet are fine; a blank warning is the hard level; grades for floors
    ok = lm.read_limits(lf.limit_frame([lf.approved(effective_to="2026-06-01"),
                                        lf.approved(limit_id="X2", effective_from="2026-06-01", hard_level="2e9"),
                                        lf.approved(limit_id="X3", metric="rating_floor", unit="notch",
                                                    selector="of=purchases", warning_level="A", hard_level="A-")]))
    assert ok.loc[0, "warning_level"] == ok.loc[0, "hard_level"] == 1e9
    assert ok.loc[2, "hard_level"] == 7.0 and ok.loc[2, "warning_level"] == 6.0  # A- is notch 7, A notch 6


def test_shipped_tables_say_what_they_are():
    ex = lm.example_limits()
    assert list(ex["limit_id"]) == [f"E{k:02d}" for k in range(1, 31)]
    assert (ex["policy_status"] == "EXAMPLE").all() and ex["notes"].str.contains(lm.EXAMPLE_NOTE, regex=False).all()
    legal = lm.read_limits(lm.LEGAL_FORM_LIMITS_CSV)
    assert (legal["counsel"] == "yes").all() and (legal["source"] != "").all()
    assert (legal["origin"] == "legal_form").all()
    assert set(legal["policy_status"]) == {"APPROVED", "DRAFT"}
    assert set(lm.legal_form_limits(UNREG)["legal_form"]) == {"unregulated_company"}
    nb = lm.legal_form_limits(NBFC)
    assert set(nb["legal_form"]) == {"nbfc_non_pd"} and {"nof", "outside_liabilities", "crar", "alm_mismatch"} <= \
        set(nb["metric"])
    assert nb.set_index("limit_id").loc["L10", "hard_level"] == 7.0  # base layer leverage ratio
    rw = lm.read_risk_weights().set_index(["kind", "sector"])["risk_weight_pct"]
    assert rw[("gsec", "")] == 0 and rw[("sdl", "")] == 0 and rw[("corporate", "")] == 100 and \
        rw[("corporate", "psu_bank")] == 20 and rw[("cd", "")] == 0 and rw[("cd", "pfi")] == 100  # bank CDs: item (1)


def test_limits_in_force_by_dates_approval_and_legal_form_i11():
    rows = lf.limit_frame([
        lf.approved(limit_id="A", effective_from="2026-11-20"),  # from tomorrow
        lf.approved(limit_id="B", metric="cs01", approval_date="2026-11-20"),  # approved after as_of
        lf.approved(limit_id="C", metric="krd", effective_to="2026-11-19"),  # ended yesterday (excluded)
        lf.approved(limit_id="D", metric="exposure", unit="pct_nav", legal_form="nbfc_non_pd"),
        lf.approved(limit_id="E", metric="jtd", unit="pct_nav", approval_date="2026-11-19")])
    live = lm.limits_in_force(rows, as_of=AS_OF, legal_form="unregulated_company")
    assert list(live["limit_id"]) == ["E"]
    assert list(lm.limits_in_force(rows, as_of=datetime(2026, 11, 20, 9), legal_form=NBFC)["limit_id"]) == \
        ["A", "B", "D", "E"]


# ---------------------------------------------------------------- metrics (L5.3)


def _metrics(preset="unregulated_company", limits=None, **kw):
    market, trades, res, ctx, rr = lf.engine_and_risk(preset)
    table = limits if limits is not None else lm.limit_table(lm.example_limits(), lm.legal_form_limits(
        ctx.settings.access))
    return lm.limit_metrics(rr, res, ctx, limits=table, board_resolution_date="2026-04-02", **kw), table, rr, res, ctx


def _value(metrics, metric, selector="", key="", scope=("fund", "fund"), unit=None):
    m = metrics[(metrics["metric"] == metric) & (metrics["selector"] == selector) & (metrics["key"] == key)
                & (metrics["scope_type"] == scope[0]) & (metrics["scope"] == scope[1])]
    if unit:
        m = m[m["unit"] == unit]
    assert len(m) == 1, (metric, selector, key, m)
    return m.iloc[0]


def test_metrics_read_the_risk_run_and_the_engine():
    m, _, rr, res, ctx = _metrics()
    nav = rr.book.nav_of("fund", "fund")
    summ = rr.risk_summary[(rr.risk_summary["scope_type"] == "fund") & (rr.risk_summary["dimension"] == "total")]
    dv01 = float(summ["dv01_yield"].iloc[0])
    row = _value(m, "dv01", "abs=yes")
    assert row["value"] == pytest.approx(abs(dv01) / (nav * 1e-4), rel=1e-12) and row["inputs_complete"]
    credit = rr.credit[(rr.credit["scope_type"] == "fund") & (rr.credit["dimension"] == "issuer")].set_index("key")
    assert _value(m, "exposure", "issuer=*;rating_bucket=AA+|AA|AA-", "ISS1")["value"] == \
        pytest.approx(credit.loc["ISS1", "pct_nav"], rel=1e-12)
    assert _value(m, "jtd", "issuer=*", "ISS1")["value"] == pytest.approx(credit.loc["ISS1", "jtd_pct_nav"], rel=1e-12)
    floor = _value(m, "rating_floor", "of=purchases")
    assert floor["value"] == 3.0 and "bucket AA" in floor["detail"]  # the terms' static AA: notch 3
    tenor = _value(m, "max_tenor", "kind=gsec|sdl")
    assert tenor["value"] == pytest.approx((date(2054, 8, 5) - lf.END).days / 365) and "GS2054" in tenor["detail"]
    stress = rr.stress[(rr.stress["scope_type"] == "fund") & (rr.stress["component"] == "total")
                       & (rr.stress["family"] == "rates_irrbb")]
    worst = stress.sort_values(["pnl", "scenario_id"]).iloc[0]
    row = _value(m, "stress_loss", "family=rates_irrbb")
    assert row["value"] == pytest.approx(-worst["pnl_pct_nav"], rel=1e-10) and worst["scenario_id"] in row["detail"]
    var = rr.var[(rr.var["scope_type"] == "fund") & (rr.var["method"] == "hs_full") & (rr.var["horizon_days"] == 1)
                 & (rr.var["window"] == "current") & (rr.var["alpha_var"] == 0.99)]
    row = _value(m, "var", "alpha=0.99;horizon=1")
    assert row["value"] == pytest.approx(float(var["var_pct_nav"].iloc[0]), rel=1e-12)
    assert not row["inputs_complete"] and "coverage" in row["detail"]  # the NCD's spread has no history
    assert not _value(m, "isin_share", "isin=*", "GS2036")["inputs_complete"]  # no stock table
    assert _value(m, "borrowing")["value"] == 0.0
    assert _value(m, "total_assets")["value"] == pytest.approx(nav)  # no borrowing, no purchase to settle
    assert _value(m, "board_resolution")["value"] == 1.0


def test_stock_and_ratings_as_known_i11():
    market, trades, res, ctx, rr = lf.engine_and_risk()
    from dataclasses import replace
    stock = pd.DataFrame([{"isin": "GS2036", "date": "2026-10-01", "outstanding_cr": 1000.0},
                          {"isin": "GS2036", "date": "2026-11-20", "outstanding_cr": 10.0}])  # dated after the day
    ratings = pd.DataFrame([{"agency": "CRISIL", "rating": "CRISIL AA-", "public_from": "2026-11-01T10:00",
                             "issuer_id": "ISS1"},
                            {"agency": "ICRA", "rating": "[ICRA]BBB", "public_from": "2026-11-19T20:30",
                             "issuer_id": "ISS1"}])  # half an hour after as_of
    ctx2 = replace(ctx, market=replace(market, stock=stock, ratings=ratings))
    table = lm.limit_table(lm.example_limits())
    m = lm.limit_metrics(rr, res, ctx2, limits=table)
    share = _value(m, "isin_share", "isin=*", "GS2036")
    assert share["inputs_complete"] and share["value"] == pytest.approx(1e9 / 1000e7 * 100)
    floor = _value(m, "rating_floor", "of=purchases")
    assert floor["value"] == 4.0 and "ratings" in floor["detail"]  # AA- (notch 4); the BBB came after as_of


# ---------------------------------------------------------------- checks (L5.4)


def test_utilisation_and_status_rules():
    assert lm.utilisation(6.0, 8.0, "max") == 0.75
    assert lm.utilisation(60.0, 50.0, "min") == pytest.approx(50 / 60)  # a minimum with a positive level
    assert lm.utilisation(-4.0, -8.0, "min") == 0.5 and lm.utilisation(-10.0, -8.0, "min") == 1.25  # a drawdown
    assert lm.utilisation(0.0, -8.0, "min") == 0.0 and str(lm.utilisation(0.0, -8.0, "min")) == "0.0"
    assert lm.utilisation(0.0, 0.0, "max") == 0.0 and lm.utilisation(5.0, 0.0, "max") == math.inf
    assert lm.past(8.0, 8.0, "max") is False and lm.past(8.000001, 8.0, "max") is True  # exact on floats
    assert lm.past(49.9, 50.0, "min") is True and lm.past(math.inf, 60.0, "max") is True
    m, table, rr, res, ctx = _metrics()
    checks = lm.check_limits(m, table, as_of=ctx.as_of, legal_form=UNREG)
    by = checks.set_index(["limit_id", "scope_type", "scope", "key"])
    assert by.loc[("E26", "fund", "fund", ""), "status"] == "not_applicable"  # an nbfc_non_pd row
    assert by.loc[("L01", "fund", "fund", ""), "status"] == "ok"  # no public funds: borrowing at zero holds
    assert by.loc[("E17", "fund", "fund", ""), "status"] == "incomplete"
    assert (checks[checks["policy_status"] == "EXAMPLE"]["detail"].str.startswith("EXAMPLE")).all()
    e27 = by.loc[("E27", "fund", "fund", "")]
    # the fund's cash counts as saleable at day 0 (43% of NAV here) and the bonds are netted by ISIN: within the 50%
    assert e27["status"] == "ok" and e27["value"] > 50 and e27["utilisation"] < 1
    tight = lm.limit_table(lf.limit_frame([lf.approved(metric="liquid_share", unit="pct_nav", direction="min",
                                                       selector="days=1", hard_level="99", warning_level="99.5")]))
    mt = lm.limit_metrics(rr, res, ctx, limits=tight)
    x1 = lm.check_limits(mt, tight, as_of=ctx.as_of, legal_form=UNREG).iloc[0]
    assert x1["status"] == "breach" and x1["value"] < 99 and x1["utilisation"] > 1  # a minimum missed
    nb, table_n, *_ = _metrics("nbfc_non_pd")
    checks_n = lm.check_limits(nb, table_n, as_of=ctx.as_of, legal_form=NBFC).set_index(
        ["limit_id", "scope_type", "scope", "key"])
    assert checks_n.loc[("L11", "fund", "fund", ""), "status"] == "not_applicable"  # CRAR: below Rs 1,000 crore
    assert "below Rs 1000 crore" in checks_n.loc[("L11", "fund", "fund", ""), "detail"]
    assert checks_n.loc[("L10", "fund", "fund", ""), "status"] == "ok"  # base layer leverage ratio in force
    policy = lm.limit_table(lf.limit_frame([lf.approved(metric="borrowing", unit="rs", hard_level="0")]))
    mb = lm.limit_metrics(rr, res, ctx, limits=policy)
    assert lm.check_limits(mb, policy, as_of=ctx.as_of, legal_form=UNREG)["status"].iloc[0] == "not_applicable"
    assert lm.check_limits(mb, policy, as_of=ctx.as_of, legal_form=NBFC)["status"].iloc[0] == "ok"


def _row(checks, limit_id, key=""):
    hit = checks[(checks["limit_id"] == limit_id) & (checks["key"] == key)]
    assert len(hit) == 1, (limit_id, key, hit)
    return hit.iloc[0]


def test_derisk_scales_multiply_market_risk_levels():
    m, table, rr, res, ctx = _metrics()
    plain = lm.check_limits(m, table, as_of=ctx.as_of, legal_form=UNREG)
    scaled = lm.check_limits(m, table, as_of=ctx.as_of, legal_form=UNREG, scales={("fund", "fund"): 0.25})
    assert _row(scaled, "E01")["hard_level"] == 0.25 * _row(plain, "E01")["hard_level"]
    assert _row(scaled, "E01")["status"] == "breach" and "scaled" in _row(scaled, "E01")["detail"]
    assert _row(scaled, "E09", "ISS1")["hard_level"] == _row(plain, "E09", "ISS1")["hard_level"]  # not scaled


# ---------------------------------------------------------------- the breach log (L5.5)


def _check(limit_id, value, hard, status, *, key="", scope=("fund", "fund"), direction="max"):
    return {"limit_id": limit_id, "scope_type": scope[0], "scope": scope[1], "key": key, "value": value,
            "hard_level": hard, "status": status, "direction": direction}


def test_breach_log_new_continuing_escalated_cured_and_active():
    table = lm.limit_table(lf.limit_frame([lf.approved(cure_business_days="2"),
                                           lf.approved(limit_id="L9", metric="short_face", unit="rs",
                                                       hard_level="0", cure_business_days="0")]))
    fills = pd.DataFrame([{"date": date(2026, 11, 16), "book": "b", "status": "filled"}])
    d = [date(2026, 11, 16), date(2026, 11, 17), date(2026, 11, 18), date(2026, 11, 19), date(2026, 11, 20)]
    log = lm.update_breach_log(None, pd.DataFrame([_check("X1", 12.0, 10.0, "breach")]), as_of=d[0], limits=table,
                               fills=fills)
    r = log.iloc[0]
    assert r["status"] == "new" and r["origin"] == "active" and r["cure_due"] == date(2026, 11, 18)
    assert "bypassed" in r["notes"]
    log = lm.update_breach_log(log, pd.DataFrame([_check("X1", 15.0, 10.0, "breach")]), as_of=d[1], limits=table)
    r = log.iloc[0]
    assert r["status"] == "continuing" and r["business_days"] == 2 and r["worst_value"] == 15.0
    again = lm.update_breach_log(log, pd.DataFrame([_check("X1", 15.0, 10.0, "breach")]), as_of=d[1], limits=table)
    assert again.equals(log)  # the same day run again
    log = lm.update_breach_log(log, pd.DataFrame([_check("X1", 11.0, 10.0, "breach")]), as_of=d[2], limits=table)
    assert log.iloc[0]["status"] == "continuing"  # on its cure date
    log = lm.update_breach_log(log, pd.DataFrame([_check("X1", 11.0, 10.0, "breach"),
                                                   _check("L9", 5e7, 0.0, "breach")]), as_of=d[3], limits=table)
    by = log.set_index("limit_id")
    assert by.loc["X1", "status"] == "escalated" and by.loc["X1", "worst_value"] == 15.0
    assert by.loc["L9", "status"] == "escalated" and by.loc["L9", "origin"] == "passive"  # a legal-form incident
    log = lm.update_breach_log(log, pd.DataFrame([_check("X1", 9.0, 10.0, "ok")]), as_of=d[4], limits=table)
    by = log.set_index("limit_id")
    assert by.loc["X1", "status"] == "cured" and by.loc["X1", "closed_date"] == d[4]
    assert list(lm.breaches_of_day(log, d[4])["limit_id"]) == ["X1", "L9"] or \
        set(lm.breaches_of_day(log, d[4])["limit_id"]) == {"X1", "L9"}
    later = lm.update_breach_log(log, pd.DataFrame([_check("X1", 12.0, 10.0, "breach")]), as_of=date(2026, 11, 23),
                                 limits=table)
    assert sorted(later[later["limit_id"] == "X1"]["status"]) == ["cured", "new"]  # a new episode


# ---------------------------------------------------------------- the pre-trade gate (L5.6)


def _gate(limits, ctx, **kw):
    return lm.PreTradeGate(lm.limit_table(lf.limit_frame(limits)) if isinstance(limits, list) else limits, ctx, **kw)


def _decisions(rows):
    frame = lm.gate_frame(rows)
    return [frame[frame["seq"] == s]["decision"].iloc[0] for s in sorted(set(frame["seq"]))], frame


def test_the_preset_rules_refuse_first_i13():
    market, trades = lf.market_and_trades()[:2]
    ctx = lf.context(market, "unregulated_company", trades=trades)
    st = lf.state([lf.gsec_row("GS2036", 1e9)], {"b": (1.5e9, 5e8)}, ctx, scenarios=lf.scenario_rows())
    gate = lm.PreTradeGate(None, ctx)
    orders = [rt.Order(book="b", side="sell", face=2e9, isin="GS2036"),
              rt.Order(book="b", side="pay", face=1e9, months=24, purpose="view"),
              rt.Order(book="b", side="buy", face=6e8, isin="GS2054"),
              rt.Order(book="b", side="buy", face=1e8, isin="GS2054")]
    approved, rows = gate.check(st, orders)
    decisions, frame = _decisions(rows)
    assert decisions == ["refused", "refused", "refused", "approved"]
    assert approved == [orders[3]] and approved[0] is orders[3]  # the same object
    why = frame.set_index("seq")["reason"]
    assert "allows no short sales (Short Sale Directions 2018 para 3)" in why[1]
    assert "hedging only (Rupee IRD Directions 2025 para 5.4(i))" in why[2]
    assert "would need Rs" in why[3] and "cannot borrow" in why[3]
    assert list(frame.set_index("seq").loc[[1, 2, 3], "limit_id"]) == ["preset:short_sale", "preset:ois_purpose",
                                                                        "preset:no_borrowing"]
    ctx_n = lf.context(market, "nbfc_non_pd", trades=trades)
    st_n = lf.state([lf.gsec_row("GS2036", 1e9)], {"b": (1.5e9, 5e8)}, ctx_n, scenarios=lf.scenario_rows())
    _, rows_n = lm.PreTradeGate(None, ctx_n).check(st_n, [orders[2]])
    assert _decisions(rows_n)[0] == ["approved"]  # an NBFC may borrow in repo for it


def test_refuse_only_when_worse_than_before_and_keys_not_touched_never_block():
    market, trades = lf.market_and_trades()[:2]
    ctx = lf.context(market, "nbfc_non_pd", trades=trades)
    st = lf.state([lf.gsec_row("GS2036", 1e9), lf.gsec_row("GS2031", 2e8, book="c")],
                  {"b": (1.5e9, 5e8), "c": (5e8, 3e8)}, ctx, scenarios=lf.scenario_rows())
    dv01 = float(rk.summary_table(st.book).query("scope_type == 'fund' and dimension == 'total'")["dv01_yield"].iloc[0])
    gate = _gate([lf.approved(hard_level=str(dv01 - 1e4), warning_level=str(dv01 - 2e4)),  # in breach already
                  lf.approved(limit_id="X2", metric="dv01", scope_type="book", scope="c", hard_level="1e12",
                              warning_level="1")], ctx)
    # the sale of Rs 1 crore (DV01 about Rs 6,400 per bp) leaves the fund past the hard level, Rs 10,000 per bp
    # below its DV01: a Rs 5 crore sale (about Rs 32,000) would cure the breach, and the third order would then only
    # warn (the gate's cache once read a freed view's values and refused it all the same)
    orders = [rt.Order(book="b", side="buy", face=5e7, isin="GS2036"),   # deeper: refused
              rt.Order(book="b", side="sell", face=1e7, isin="GS2036"),  # smaller, still in breach: allowed
              rt.Order(book="c", side="buy", face=5e7, isin="GS2031")]   # deeper again; and warns on X2
    approved, rows = gate.check(st, orders)
    decisions, frame = _decisions(rows)
    # the sale is still past both levels but smaller: neither test fails (both need worse than before)
    assert decisions == ["refused", "approved", "refused"]
    first = frame[frame["seq"] == 1].iloc[0]
    assert first["limit_id"] == "X1" and first["post_value"] > first["pre_value"] > first["hard_level"]
    assert re.search(r"post-trade Rs [\d,.]+ per bp above the hard Rs [\d,.]+ per bp \(pre-trade Rs", first["reason"])
    second = frame[frame["seq"] == 2]
    assert len(second) == 1 and second["limit_id"].iloc[0] == ""  # one row, blank limit columns
    third = frame[frame["seq"] == 3].set_index("limit_id")
    assert third.loc["X1", "decision"] == "refused" and "warning" in third.loc["X2", "reason"]
    # the sale was approved: the third order is checked against the book after it
    assert third.loc["X1", "pre_value"] < first["pre_value"] < third.loc["X1", "post_value"]
    # a book limit of book c never blocks an order of book b
    gate2 = _gate([lf.approved(scope_type="book", scope="c", hard_level="0")], ctx)
    assert _decisions(gate2.check(st, [orders[0]])[1])[0] == ["approved"]


def test_untouched_issuer_keys_and_the_purchase_rating_floor():
    market, trades = lf.market_and_trades()[:2]
    ratings = pd.DataFrame([{"agency": "CARE", "rating": "CARE BBB+", "public_from": "2026-11-02T10:00",
                             "issuer_id": "ISS1"}])
    from dataclasses import replace
    ctx = lf.context(replace(market, ratings=ratings), "nbfc_non_pd", trades=trades)
    ncd = lf.ncd_bond()
    s = fx.CAL.settlement_date(lf.END, 1)
    y = fx.on_curve_yield(ncd, lf.fits()[2][lf.END].curve, s, 0.01)
    ncd_row = fx.bond_row(ncd, lf.NCD, "corporate", 3e8, lf.END, s, y, book="c")
    st = lf.state([lf.gsec_row("GS2036", 1e9), ncd_row], {"b": (1.5e9, 5e8), "c": (5e8, 3e8)}, ctx,
                  scenarios=lf.scenario_rows())
    gate = _gate([lf.approved(metric="exposure", unit="pct_nav", selector="issuer=*", hard_level="5"),
                  lf.approved(limit_id="F1", metric="rating_floor", unit="notch", selector="of=purchases",
                              warning_level="A", hard_level="A-")], ctx)
    orders = [rt.Order(book="b", side="buy", face=1e8, isin="GS2036"),  # does not touch ISS1's key
              rt.Order(book="c", side="buy", face=1e7, isin=lf.NCD),    # deeper on ISS1, and rated BBB+
              rt.Order(book="c", side="sell", face=1e7, isin=lf.NCD)]   # a sale: no floor, smaller exposure
    _, rows = gate.check(st, orders)
    decisions, frame = _decisions(rows)
    assert decisions == ["approved", "refused", "approved"]
    two = frame[frame["seq"] == 2].set_index("limit_id")
    assert "rated notch 8 (BBB+), past the hard floor notch 7 (A-)" in two.loc["F1", "reason"]
    assert "[ISS1]" in two.loc["X1", "reason"]


def test_example_limits_need_permission_and_drafts_only_warn():
    market, trades = lf.market_and_trades()[:2]
    ctx = lf.context(market, "unregulated_company", trades=trades)
    with pytest.raises(ValueError, match="refuses to run on EXAMPLE limits"):
        lm.PreTradeGate(lm.example_limits(), ctx)
    assert len(lm.PreTradeGate(lm.example_limits(), ctx, allow_example_limits=True).limits) > 30
    st = lf.state([lf.gsec_row("GS2036", 1e9)], {"b": (1.5e9, 5e8)}, ctx, scenarios=lf.scenario_rows())
    gate = _gate([lf.approved(policy_status="DRAFT", hard_level="1")], ctx)
    _, rows = gate.check(st, [rt.Order(book="b", side="buy", face=1e7, isin="GS2036")])
    decisions, frame = _decisions(rows)
    assert decisions == ["approved_with_warning"] and "DRAFT (not enforced)" in frame["reason"].iloc[0]
    halt = _gate([lf.approved(action="halt", hard_level="1")], ctx)
    _, rows = halt.check(st, [rt.Order(book="b", side="sell", face=1e7, isin="GS2036")])
    decisions, frame = _decisions(rows)
    assert decisions == ["refused"] and "halts every new order" in frame["reason"].iloc[0]  # even a reducing sale


def test_a_fund_row_may_not_reuse_a_legal_form_id_and_warnings_on_refusals_say_so():
    market, trades = lf.market_and_trades()[:2]
    ctx = lf.context(market, "unregulated_company", trades=trades)
    with pytest.raises(ValueError, match="limit_id L05 is in the fund's limit table and in the legal-form table"):
        lm.PreTradeGate(lf.limit_frame([lf.approved(limit_id="L05")]), ctx)
    st = lf.state([lf.gsec_row("GS2036", 1e9)], {"b": (1.5e9, 5e8)}, ctx)
    mine = lf.limit_frame([lf.approved(hard_level="1e5", warning_level="1"),
                           lf.approved(limit_id="X2", unit="years", hard_level="1e6", warning_level="0.001")])
    order = [rt.Order(book="b", side="buy", face=1e8, isin="GS2036")]
    alone = lm.gate_frame(lm.PreTradeGate(mine, ctx).check(st, order)[1])
    joined = lm.limit_table(mine, lm.legal_form_limits(UNREG))  # the legal-form rows given twice are taken once
    assert alone.to_csv(index=False) == lm.gate_frame(lm.PreTradeGate(joined, ctx).check(st, order)[1]).to_csv(
        index=False)
    assert list(alone["decision"].unique()) == ["refused"] and list(alone["limit_id"]) == ["X1", "X2"]
    assert not alone["reason"].iloc[0].startswith(lm.WARNING_ONLY)  # X1's hard level refuses the order
    assert alone["reason"].iloc[1].startswith(lm.WARNING_ONLY)  # X2 only warns


def test_no_public_price_is_refused_and_queued_orders_count_as_filled():
    market, trades = lf.market_and_trades()[:2]
    marks = market.marks[market.marks["isin"] != "GS2054"]
    from dataclasses import replace
    ctx = lf.context(replace(market, marks=marks), "unregulated_company", trades=trades)
    queued = pd.DataFrame([{"order_id": "Q1", "book": "b", "isin": "GS2036", "side": "buy", "face": 4e8}])
    st = lf.state([lf.gsec_row("GS2036", 1e9)], {"b": (1.5e9, 5e8)}, ctx, orders=queued,
                  scenarios=lf.scenario_rows())
    assert len(st.queued) == 1
    _, rows = lm.PreTradeGate(None, ctx).check(st, [rt.Order(book="b", side="buy", face=1e8, isin="GS2054"),
                                                    rt.Order(book="b", side="buy", face=1.5e8, isin="GS2036")])
    decisions, frame = _decisions(rows)
    assert decisions == ["refused", "refused"]
    assert "no public price for GS2054 by 2026-11-19 20:00" in frame["reason"].iloc[0]
    assert "would need Rs" in frame["reason"].iloc[1]  # Rs 40 crore queued + Rs 15 crore > Rs 50 crore of cash
    st2 = lf.state([lf.gsec_row("GS2036", 1e9)], {"b": (1.5e9, 5e8)}, ctx, scenarios=lf.scenario_rows())
    _, rows2 = lm.PreTradeGate(None, ctx).check(st2, [rt.Order(book="b", side="buy", face=1.5e8, isin="GS2036")])
    assert _decisions(rows2)[0] == ["approved"]  # alone it fits


def test_t26_an_ois_hedge_at_the_wrong_tenor_is_refused_under_hedge_only():
    market, trades = lf.ois_market()
    strict = lf.context(market, "unregulated_company", trades=trades)
    st0 = lf.state([lf.gsec_row("GS2031", 1e9)], {"b": (1.5e9, 5e8)}, strict, scenarios=lf.scenario_rows())
    _, rows0 = lm.PreTradeGate(None, strict).check(st0, [rt.Order(book="b", side="pay", face=1e9, months=12)])
    assert "project_calendar" in rows0[0]["reason"]  # a swap past the holiday list's years: refused, saying why
    ctx = lf.context(market, "unregulated_company", trades=trades, project_calendar=True)
    bond_row = lf.gsec_row("GS2031", 1e9)
    st = lf.state([bond_row], {"b": (1.5e9, 5e8)}, ctx, scenarios=lf.scenario_rows())
    dv01 = st.book.positions[0].dv01_yield
    curve = market.ois_curves[lf.END].curve
    cal = fx.CAL.lenient()
    start = effective_date(cal.add_business_days(lf.END, 1), MIBOR_OIS, cal)

    def sized(months):  # the notional whose engine DV01 (zero-rate, one-sided) offsets the bond's exactly
        unit = ois_npv(curve, OisSwap(effective=start, months=months, fixed_rate_pct=6.0, notional=1e7,
                                      pay_fixed=True), calendar=cal).dv01
        return rt.Order(book="b", side="pay", face=round(dv01 / abs(unit) * 1e7, 0), months=months)
    good, bad = sized(48), sized(12)
    annuity = pf.hedge_ois_notional(dv01, curve, 12, start, calendar=cal, method="annuity")
    assert annuity > bad.face  # annuity sizing is larger
    approved, rows = lm.PreTradeGate(None, ctx).check(st, [bad, good])
    decisions, frame = _decisions(rows)
    assert decisions == ["refused", "approved"] and approved == [good]
    why = frame[frame["seq"] == 1].set_index("limit_id")
    assert "L06" in why.index and why.loc["L06", "post_value"] > 1.0  # the hedge-effect test (U, counsel)
    assert "L05" not in why.index  # the engine's DV01 test alone would have passed it
    # under nbfc_non_pd the same swap is allowed (OIS for any purpose): the DRAFT row only warns
    ctx_n = lf.context(market, "nbfc_non_pd", trades=trades, project_calendar=True)
    st_n = lf.state([bond_row], {"b": (1.5e9, 5e8)}, ctx_n, scenarios=lf.scenario_rows())
    decisions_n, frame_n = _decisions(lm.PreTradeGate(None, ctx_n).check(st_n, [bad])[1])
    assert decisions_n == ["approved_with_warning"] and "DRAFT (not enforced)" in frame_n["reason"].iloc[0]


def test_the_gate_is_deterministic_and_reads_any_row_order():
    market, trades = lf.market_and_trades()[:2]
    ctx = lf.context(market, "nbfc_non_pd", trades=trades)
    orders = [rt.Order(book="b", side="buy", face=1e8, isin="GS2036"), rt.Order(book="c", side="buy", face=5e7,
                                                                                 isin="GS2054")]
    table = lm.example_limits()
    frames = []
    for t in (table, table.sample(frac=1.0, random_state=4)):
        st = lf.state([lf.gsec_row("GS2036", 1e9)], {"b": (1.5e9, 5e8), "c": (5e8, 5e8)}, ctx)
        frames.append(lm.gate_frame(lm.PreTradeGate(t, ctx, allow_example_limits=True).check(st, orders)[1]))
    assert frames[0].to_csv(index=False) == frames[1].to_csv(index=False)


# ---------------------------------------------------------------- the gated back-test (D7.3, I10)


class _Buyer:
    def __init__(self, plan):
        self.plan = plan

    def decide(self, snapshot):
        return [rt.Order(book="main", side=s, face=f, isin=i) for d, s, f, i in self.plan if d == snapshot.day]


def test_gated_backtests_write_the_same_bytes_and_log_refusals(tmp_path):
    market, trades, *_ = lf.market_and_trades()
    st = rt.EngineSettings(access=UNREG)
    ctx = lf.context(market, "unregulated_company", trades=trades)
    plan = [(date(2026, 11, 13), "buy", 1e9, "GS2036"), (date(2026, 11, 16), "buy", 5e8, "GS2054"),
            (date(2026, 11, 17), "buy", 4e9, "GS2036"), (date(2026, 11, 17), "buy", 1e8, "GS2054")]

    def run(strategy, name):
        res = rt.run_backtest(strategy, market, st, start=date(2026, 11, 13), end=lf.END, capital=2e9)
        rt.write_result(res, tmp_path / name)
        return res

    run(_Buyer(plan[:2]), "ungated")
    gate = lm.PreTradeGate(None, ctx)
    passing = lm.GatedStrategy(_Buyer(plan[:2]), gate)
    run(passing, "pass_all")
    recorded = lm.GatedStrategy(_Buyer(plan[:2]), gate, recorder=lm.risk_recorder(ctx))
    run(recorded, "recorder")
    names = sorted(p.name for p in (tmp_path / "ungated").iterdir())
    for other in ("pass_all", "recorder"):
        assert all(filecmp.cmp(tmp_path / "ungated" / n, tmp_path / other / n, shallow=False) for n in names), other
    assert set(passing.log_frame()["decision"]) == {"approved"} and len(recorded.records) > 0
    refusing = lm.GatedStrategy(_Buyer(plan), gate)
    res = run(refusing, "refusing")
    log = refusing.log_frame()
    refused = log[log["decision"] == "refused"].iloc[0]
    assert refused["decision_date"] == date(2026, 11, 17) and refused["seq"] == 1 and refused["face"] == 4e9
    assert refused["limit_id"] == "preset:no_borrowing"
    assert (res.fills["face"] != 4e9).all() and list(res.fills["status"]) == ["filled"] * 3
    cuts = pd.DataFrame([{"effective_date": "2015-01-01", "kind": "gsec", "haircut_pct": 3.0, "source": "synthetic"}])
    with pytest.raises(ValueError, match="funding_shortfall.*allows no repo"):  # ungated, the engine stops there
        rt.run_backtest(_Buyer(plan), replace(market, haircuts=cuts), st, start=date(2026, 11, 13), end=lf.END,
                        capital=2e9)


def test_a_gated_nbfc_backtest_and_the_cli_state_count_each_books_cash(tmp_path):
    """The ALM ratios credit each book's cash on day 1: a state without it read every purchase as unfunded (100%)."""
    market, trades, *_ = lf.market_and_trades()
    ctx = lf.context(market, "nbfc_non_pd", trades=trades)
    plan = [(date(2026, 11, 13), "buy", 1e9, "GS2036"), (date(2026, 11, 16), "buy", 5e8, "GS2054")]
    gated = lm.GatedStrategy(_Buyer(plan), lm.PreTradeGate(None, ctx))
    rt.run_backtest(gated, market, rt.EngineSettings(access=NBFC), start=date(2026, 11, 13), end=lf.END, capital=2e9)
    log = gated.log_frame()
    assert len(log) == 2 and set(log["decision"]) == {"approved"}  # Rs 200 crore of assets: L15-L17 apply
    _, _, res, rctx, _ = lf.engine_and_risk("nbfc_non_pd")
    rt.write_result(res, tmp_path / "engine")
    bare = lf.context(market, "nbfc_non_pd", trades=trades)  # no nav or funding: the CLI's context
    order = [rt.Order(book="c", side="buy", face=3e7, isin=lf.NCD)]
    from_dir = lm.gate_frame(lm.PreTradeGate(None, bare).check(lm.state_from_result(tmp_path / "engine", lf.END,
                                                                                   bare), order)[1])
    from_run = lm.gate_frame(lm.PreTradeGate(None, rctx).check(lm.state_from_result(res, lf.END, rctx), order)[1])
    assert from_dir.to_csv(index=False) == from_run.to_csv(index=False)
    assert not from_dir["limit_id"].isin(["L15", "L16", "L17"]).any()


# ---------------------------------------------------------------- determinism and the clock (I12)


def test_limit_outputs_are_the_same_bytes_in_any_row_order(tmp_path):
    m, table, rr, res, ctx = _metrics()
    shuffled = table.sample(frac=1.0, random_state=7).reset_index(drop=True)
    m2 = lm.limit_metrics(rr, res, ctx, limits=shuffled, board_resolution_date="2026-04-02")
    c1 = lm.check_limits(m, table, as_of=ctx.as_of, legal_form=UNREG)
    c2 = lm.check_limits(m2, shuffled, as_of=ctx.as_of, legal_form=UNREG)
    log = lm.update_breach_log(None, c1, as_of=ctx.as_of, limits=table)
    lm.write_limits(m, c1, log, tmp_path / "a", breach_log=log)
    lm.write_limits(m2, c2, lm.update_breach_log(None, c2, as_of=ctx.as_of, limits=shuffled), tmp_path / "b",
                    breach_log=lm.update_breach_log(None, c2, as_of=ctx.as_of, limits=shuffled))
    for name in ("metrics", "limit_checks", "breaches", "breach_log"):
        assert (tmp_path / "a" / f"{name}.csv").read_bytes() == (tmp_path / "b" / f"{name}.csv").read_bytes(), name


def test_no_clock_is_read_by_the_new_modules():
    src = Path(lm.__file__).parent
    for name in ("limits.py", "portfolio.py", "daily.py"):
        text = (src / name).read_text()
        for pattern in ("datetime.now", "date.today", "time.time(", "datetime.utcnow"):
            assert pattern not in text, (name, pattern)
