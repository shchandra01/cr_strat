"""Tests of costs: the dated cost table, its precedence, fills, CCIL charges, GST and stamp duty (spec A3, R7, R8, R17).

Typed-in facts with their source: CCIL's settlement charges from 1 Oct 2019 (Rs 95 per crore of face for dated
securities, minimum 20, maximum 4,750 a trade; TREPS Rs 10 per crore of repayment value, maximum 1,500) as an earlier
probe of CCIL's page read them; the TREPS initial margin of 0.50% (CCIL RMD/SS/25/22); stamp duty of 0.0001% on CP
and OIS (Finance Act 2019 schedule, through secondary sources). The 13 Nov 2026 auction's 6.94% GS 2036 at 97.43.
"""

from datetime import date

import pandas as pd
import pytest

from cr_strat import costs as co
from cr_strat.bonds import FixedRateBond

GS2036 = FixedRateBond(6.94, date(2036, 5, 11))
S = date(2026, 11, 16)


@pytest.fixture(scope="module")
def table():
    return co.CostTable(co.read_cost_table())


def _dv01():
    return GS2036.dv01(GS2036.yield_from_price(97.43, S), S)


def test_defaults_load_with_source_and_status(table):
    frame = table.frame
    assert set(frame["component"]) <= set(co.COMPONENTS)
    assert frame["source"].str.len().min() > 10
    assert set(frame["status"]) <= {"V", "P", "U"}
    assert frame["rule_id"].is_unique
    unverified = co.unverified_rules(table)
    assert {"HS05", "HS09", "CU01", "PF01", "SD03"} <= set(unverified["rule_id"])
    assert "CS01" not in set(unverified["rule_id"])


def test_round_trip_r7(table):
    dv01 = _dv01()
    ai = GS2036.accrued_interest(S)
    buy = co.bond_trade_costs(table, kind="gsec", side="buy", face=1e9, mid=97.43, dv01_per100=dv01,
                              trade_date=date(2026, 11, 13), liquidity="liquid", accrued=ai)
    sell = co.bond_trade_costs(table, kind="gsec", side="sell", face=1e9, mid=97.43, dv01_per100=dv01,
                               trade_date=date(2026, 11, 13), liquidity="liquid", accrued=ai)
    assert buy.fill - 97.43 == pytest.approx(0.041522, abs=5e-7)
    assert buy.spread_cost + sell.spread_cost == pytest.approx(8_30_441.41, abs=0.01)
    assert buy.ccil == sell.ccil == 4750.0  # Rs 95 x 100 crore = 9,500, capped at 4,750 a trade
    assert buy.gst + sell.gst == pytest.approx(1710.0)
    total = buy.spread_cost + sell.spread_cost + buy.total + sell.total
    assert total == pytest.approx(8_41_651.41, abs=0.01)
    assert total / 1e9 * 1e4 == pytest.approx(8.4165, abs=5e-5)
    assert buy.rules == ("HS01:P", "CS01:V", "PF01:U", "CU01:U", "GS01:P", "SD01:P")
    assert buy.unverified == ("PF01", "CU01")
    assert buy.stamp_duty == 0.0  # no stamp duty on government securities


def test_stamp_duty_r17(table):
    cp = co.bond_trade_costs(table, kind="cp", side="sell", face=25e7, mid=100.0, dv01_per100=0.0,
                             trade_date=date(2026, 11, 13), fill=100.0)
    assert cp.stamp_duty == pytest.approx(250.0)  # 0.0001% of Rs 25 crore, paid by the seller (transferor)
    buyer = co.bond_trade_costs(table, kind="cp", side="buy", face=25e7, mid=100.0, dv01_per100=0.0,
                                trade_date=date(2026, 11, 13), fill=100.0)
    assert buyer.stamp_duty == 0.0
    swap = co.ois_trade_costs(table, side="pay", notional=1e9, pv01=3_50_882.26, tenor_years=4,
                              trade_date=date(2026, 11, 19), mid_rate_pct=6.12)
    assert swap.stamp_duty == pytest.approx(1000.0)
    assert swap.gst == 0.0  # no GST on stamp duty
    assert swap.fill == pytest.approx(6.125)  # 0.5 bp half spread up to 5 years (UNVERIFIED)
    assert swap.spread_cost == pytest.approx(0.5 * 3_50_882.26)
    long = co.ois_trade_costs(table, side="receive", notional=1e9, pv01=1.0, tenor_years=7,
                              trade_date=date(2026, 11, 19), mid_rate_pct=6.5)
    assert long.fill == pytest.approx(6.485)  # 1.5 bp beyond 5 years, a receiver gives up
    gsec = co.bond_trade_costs(table, kind="gsec", side="sell", face=25e7, mid=100.0, dv01_per100=0.05,
                               trade_date=date(2026, 11, 13), fill=100.0)
    assert gsec.stamp_duty == 0.0


def test_treps_charge_and_margin_r8(table):
    cash = 94_60_05_972.22
    repayment = cash * (1 + 5.57 / 36500)
    deal = co.repo_deal_costs(table, segment="treps", face=1e9, cash=cash, repayment=repayment,
                              deal_date=date(2026, 11, 16))
    assert deal.ccil == pytest.approx(946.15, abs=0.005)
    assert deal.gst == pytest.approx(0.18 * deal.ccil)
    assert deal.stamp_duty == 0.0
    margin, rule = co.treps_margin(table, repayment=repayment, on=date(2026, 11, 16))
    assert margin == pytest.approx(47_30_751.68, abs=0.01)
    assert (rule.rule_id, rule.status) == ("TM01", "V")
    assert co.treps_margin(table, repayment=repayment, on=date(2025, 1, 2))[1].status == "U"
    repo = co.repo_deal_costs(table, segment="market_repo", face=1e9, cash=9e8, repayment=9.001e8,
                              deal_date=date(2026, 11, 16))
    assert repo.ccil == 2 * 1000.0  # Rs 10 x 100 crore a leg, both legs on the deal date
    capped = co.repo_deal_costs(table, segment="market_repo", face=1e10, cash=9e9, repayment=9.001e9,
                                deal_date=date(2026, 11, 16))
    assert capped.ccil == 2 * 1500.0  # Rs 10,000 a leg, capped at Rs 1,500


def test_auction_allotments_pay_no_spread_or_ccil_charge(table):
    out = co.bond_trade_costs(table, kind="gsec", side="buy", face=1e9, mid=97.43, dv01_per100=_dv01(),
                              trade_date=date(2026, 11, 13), venue="auction", liquidity="liquid")
    assert out.fill == 97.43 and out.spread_cost == 0.0 and out.ccil == 0.0
    assert "CS05:U" in out.rules and "HS11:U" in out.rules
    early = co.bond_trade_costs(table, kind="gsec", side="buy", face=1e9, mid=97.43, dv01_per100=_dv01(),
                                trade_date=date(2018, 3, 1), venue="auction_nc", liquidity="liquid")
    assert early.ccil == 0.0 and "CS15:U" in early.rules


def test_dated_rows_and_held_back_schedules(table):
    old = co.bond_trade_costs(table, kind="gsec", side="buy", face=1e9, mid=97.43, dv01_per100=_dv01(),
                              trade_date=date(2018, 3, 1), liquidity="liquid")
    assert "CS11:U" in old.rules and "GS01:P" in old.rules
    st = co.bond_trade_costs(table, kind="gsec", side="buy", face=1e9, mid=97.43, dv01_per100=_dv01(),
                             trade_date=date(2016, 3, 1), liquidity="liquid")
    assert st.gst == pytest.approx(0.145 * st.fees)  # service tax with the Swachh Bharat cess (UNVERIFIED rows)
    with pytest.raises(ValueError, match="no cost rule gives the half_spread_bp"):
        co.cost_rule(table, "half_spread_bp", kind="gsec", on=date(2014, 12, 31), liquidity="liquid")


def test_precedence_specific_user_and_conflicts(table):
    assert co.cost_rule(table, "half_spread_bp", kind="gsec", on=date(2026, 1, 5), liquidity="liquid").value == 0.625
    assert co.cost_rule(table, "half_spread_bp", kind="gsec", on=date(2026, 1, 5)).value == 4.5  # no class
    user = pd.DataFrame([
        {"rule_id": "U1", "component": "half_spread_bp", "kind": "gsec", "liquidity": "liquid",
         "effective_from": "2015-01-01", "value": 0.4, "unit": "bp_yield", "source": "our desk's fills"},
        {"rule_id": "U2", "component": "half_spread_bp", "isin": "IN0020250052", "effective_from": "2015-01-01",
         "value": 0.3, "unit": "bp_yield", "source": "our desk, the 10-year"},
        {"rule_id": "U3", "component": "brokerage", "effective_from": "2015-01-01", "value": 50,
         "unit": "rs_per_crore_face", "gst": "yes", "source": "broker letter"}])
    both = co.read_cost_table(overrides=user)
    rule = co.cost_rule(both, "half_spread_bp", kind="gsec", on=date(2026, 1, 5), liquidity="liquid")
    assert (rule.rule_id, rule.origin, rule.status) == ("U1", "user", "user")  # equal date and score: the user's
    assert co.cost_rule(both, "half_spread_bp", kind="gsec", on=date(2026, 1, 5), liquidity="liquid",
                        isin="IN0020250052").rule_id == "U2"  # an ISIN beats a kind
    out = co.bond_trade_costs(both, kind="gsec", side="buy", face=1e9, mid=97.43, dv01_per100=_dv01(),
                              trade_date=date(2026, 11, 13), liquidity="liquid", broker="ABC")
    assert out.brokerage == pytest.approx(5000.0)
    assert out.gst == pytest.approx(0.18 * (4750.0 + 5000.0))
    clash = pd.DataFrame([
        {"rule_id": "X1", "component": "custody", "effective_from": "2015-01-01", "value": 1,
         "unit": "rs_per_crore_face", "source": "a"},
        {"rule_id": "X2", "component": "custody", "effective_from": "2015-01-01", "value": 2,
         "unit": "rs_per_crore_face", "source": "b"}])
    with pytest.raises(ValueError, match="several equally specific rules that differ"):
        co.cost_rule(co.read_cost_table(overrides=clash), "custody", kind="gsec", on=date(2026, 1, 5))
    later = pd.DataFrame([{"rule_id": "L1", "component": "ccil_settlement", "kind": "gsec", "effective_from":
                           "2015-01-01", "value": 50, "unit": "rs_per_crore_face", "source": "old letter"}])
    table2 = co.read_cost_table(overrides=later)
    assert co.cost_rule(table2, "ccil_settlement", kind="gsec", on=date(2026, 1, 5)).rule_id == "CS01"
    assert co.cost_rule(co.read_cost_table(overrides=later, replace_components=("ccil_settlement",)),
                        "ccil_settlement", kind="gsec", on=date(2026, 1, 5)).rule_id == "L1"


def test_refusals_name_the_input(table):
    with pytest.raises(ValueError, match="broker 'XYZ' needs a brokerage rule"):
        co.bond_trade_costs(table, kind="gsec", side="buy", face=1e9, mid=97.43, dv01_per100=_dv01(),
                            trade_date=date(2026, 11, 13), broker="XYZ")
    with pytest.raises(ValueError, match="face must be a positive number"):
        co.bond_trade_costs(table, kind="gsec", side="buy", face=-1, mid=97.43, dv01_per100=_dv01(),
                            trade_date=date(2026, 11, 13))
    with pytest.raises(ValueError, match="side must be buy or sell"):
        co.fill_price(97.43, "hold", 1.0, 0.06)
    bad_unit = pd.DataFrame([{"component": "custody", "effective_from": "2015-01-01", "value": 1, "unit": "rs",
                              "source": "x"}])
    with pytest.raises(ValueError, match="user cost table line 2: unit 'rs' is not one of"):
        co.read_cost_table(overrides=bad_unit)
    no_source = pd.DataFrame([{"component": "custody", "effective_from": "2015-01-01", "value": 1,
                               "unit": "rs_per_crore_face", "source": ""}])
    with pytest.raises(ValueError, match="source is empty"):
        co.read_cost_table(overrides=no_source)
    bad_component = pd.DataFrame([{"component": "tips", "effective_from": "2015-01-01", "value": 1,
                                   "unit": "pct", "source": "x"}])
    with pytest.raises(ValueError, match="component 'tips' is not one of"):
        co.read_cost_table(overrides=bad_component)
    with pytest.raises(FileNotFoundError):
        co.read_cost_table("/nonexistent/costs.csv")


def test_charge_bounds_and_fill_price():
    rule = co.CostRule("C", "ccil_settlement", 95.0, "rs_per_crore_face", 20.0, 4750.0, "trade", "both", True, "s",
                       "V", "default")
    assert co.charge(rule, face=1e6) == 20.0  # Rs 9.50 raised to the minimum
    assert co.charge(rule, face=1e8) == pytest.approx(950.0)
    assert co.charge(rule, face=1e10) == 4750.0
    assert co.fill_price(100.0, "sell", 2.0, 0.05) == pytest.approx(99.9)
    assert co.years_between(date(2026, 11, 16), date(2036, 5, 11)) == pytest.approx(3464 / 365.25)


def test_a_newer_generic_row_does_not_cancel_a_venue_exception():
    user = pd.DataFrame([{"rule_id": "U1", "component": "ccil_settlement", "kind": "gsec;green;special;sdl",
                          "effective_from": "2025-01-01", "value": 100, "unit": "rs_per_crore_face", "minimum_rs": 20,
                          "maximum_rs": 5000, "gst": "yes", "source": "a newer schedule of our own"}])
    both = co.read_cost_table(overrides=user)
    for venue in ("auction", "auction_nc", "omo", "switch"):
        out = co.bond_trade_costs(both, kind="gsec", side="buy", face=1e9, mid=98.0, dv01_per100=_dv01(),
                                  trade_date=date(2026, 11, 13), venue=venue, fill=98.0)
        assert out.ccil == 0.0 and out.gst == 0.0 and "CS05:U" in out.rules  # the exception stands
    ndsom = co.bond_trade_costs(both, kind="gsec", side="buy", face=1e9, mid=98.0, dv01_per100=_dv01(),
                                trade_date=date(2026, 11, 13), venue="ndsom", fill=98.0)
    assert ndsom.ccil == 5000.0 and "U1:user" in ndsom.rules  # the user's schedule replaces CCIL's 2019 one
    venue_row = pd.DataFrame([{"rule_id": "U2", "component": "ccil_settlement", "venue": "auction",
                               "effective_from": "2025-01-01", "value": 10, "unit": "rs_per_crore_face",
                               "source": "our clearing member charges allotments"}])
    out = co.bond_trade_costs(co.read_cost_table(overrides=venue_row), kind="gsec", side="buy", face=1e9, mid=98.0,
                              dv01_per100=_dv01(), trade_date=date(2026, 11, 13), venue="auction", fill=98.0)
    assert out.ccil == 1000.0 and "U2:user" in out.rules  # a row naming the venue overrides the exception


def test_a_row_listing_several_liquidity_classes_applies_to_each():
    user = pd.DataFrame([{"rule_id": "U1", "component": "half_spread_bp", "kind": "gsec",
                          "liquidity": "semi_liquid;illiquid", "effective_from": "2026-01-01", "value": 9,
                          "unit": "bp_yield", "source": "our desk"}])
    both = co.read_cost_table(overrides=user)
    for cls in ("semi_liquid", "illiquid"):
        assert co.cost_rule(both, "half_spread_bp", kind="gsec", on=date(2026, 11, 13), liquidity=cls).rule_id == "U1"
    assert co.cost_rule(both, "half_spread_bp", kind="gsec", on=date(2026, 11, 13), liquidity="liquid").rule_id == \
        "HS01"


def test_stamp_duty_on_cp_falls_on_the_seller_and_on_cd_on_every_trade(table):
    duty = {}
    for kind in ("cp", "cd"):
        for side in ("buy", "sell"):
            out = co.bond_trade_costs(table, kind=kind, side=side, face=5e8, mid=98.5, dv01_per100=0.02,
                                      trade_date=date(2026, 11, 13), venue="otc", fill=98.5)
            duty[(kind, side)] = (out.stamp_duty, [r for r in out.rules if r.startswith("SD")])
    assert duty[("cp", "buy")] == (0.0, ["SD02:P"]) and duty[("cp", "sell")][0] == pytest.approx(492.5)
    assert duty[("cd", "buy")] == duty[("cd", "sell")] == (pytest.approx(492.5), ["SD05:U"])  # payer UNVERIFIED


# ---------------------------------------------------------------- corporate bonds and FRBs


def test_corporate_rows_by_rating_bucket(table):
    picks = {rb: co.cost_rule(table, "half_spread_bp", kind="corporate", on=S, rating_bucket=rb)
             for rb in ("AAA", "AA+", "AA", "AA-", "A", "BBB", "SUB_IG", None)}
    assert {rb: (r.rule_id, r.value) for rb, r in picks.items()} == {
        "AAA": ("HS20", 4.0), "AA+": ("HS21", 7.5), "AA": ("HS21", 7.5), "AA-": ("HS22", 10.0), "A": ("HS23", 20.0),
        "BBB": ("HS23", 20.0), "SUB_IG": ("HS23", 20.0), None: ("HS24", 20.0)}
    assert all(r.status == "U" for r in picks.values())
    buy = co.bond_trade_costs(table, kind="corporate", side="buy", face=25e7, mid=101.0, dv01_per100=0.04,
                              trade_date=S, venue="otc", accrued=1.0, rating_bucket="AAA")
    assert buy.fill == pytest.approx(101.16)  # 4 bp a side on a DV01 of 0.04
    assert buy.ccil == 0.0 and buy.platform == pytest.approx(5 * 25.54)  # Rs 5 a crore of the Rs 25.54 crore paid
    assert buy.gst == pytest.approx(0.18 * 5 * 25.54) and buy.stamp_duty == pytest.approx(255.40)  # 0.0001%, buyer
    assert buy.rules == ("HS20:U", "CS07:U", "PF04:P", "CU01:U", "GS01:P", "SD06:P")
    sell = co.bond_trade_costs(table, kind="corporate", side="sell", face=25e7, mid=101.0, dv01_per100=0.04,
                               trade_date=S, venue="otc", accrued=1.0, rating_bucket="AAA")
    assert sell.stamp_duty == 0.0 and sell.fill == pytest.approx(100.84)
    small = co.bond_trade_costs(table, kind="corporate", side="buy", face=1e6, mid=100.0, dv01_per100=0.04,
                                trade_date=S, venue="otc", fill=100.0)
    assert small.platform == 25.0  # the exchange fee's minimum
    old = co.bond_trade_costs(table, kind="corporate", side="buy", face=1e9, mid=100.0, dv01_per100=0.04,
                              trade_date=date(2019, 5, 2), venue="otc", fill=100.0)
    assert "SD16:U" in old.rules and old.stamp_duty == pytest.approx(1000.0)  # held back before Jul 2020


def test_frb_rows_are_those_of_a_dated_gsec(table):
    frb = co.bond_trade_costs(table, kind="frb", side="buy", face=1e9, mid=100.0, dv01_per100=0.02, trade_date=S,
                              fill=100.0)
    gsec = co.bond_trade_costs(table, kind="gsec", side="buy", face=1e9, mid=100.0, dv01_per100=0.02, trade_date=S,
                               fill=100.0, liquidity="liquid")
    assert frb.ccil == gsec.ccil == 4750.0 and frb.stamp_duty == 0.0 and frb.platform == 0.0
    assert frb.rules == ("CS06:P", "PF05:U", "CU01:U", "GS01:P", "SD07:P")
    assert co.cost_rule(table, "half_spread_bp", kind="frb", on=S).value == 4.5


def test_rating_buckets_rank_below_the_kind_and_a_table_without_them_reads():
    user = pd.DataFrame([
        {"rule_id": "U1", "component": "half_spread_bp", "kind": "corporate", "min_years": "0", "max_years": "3",
         "effective_from": "2015-01-01", "value": "1.0", "unit": "bp_yield", "source": "my desk"},
        {"rule_id": "U2", "component": "half_spread_bp", "kind": "corporate", "min_years": "3", "max_years": "50",
         "effective_from": "2026-01-01", "value": "2.0", "unit": "bp_yield", "source": "my desk"},
    ])
    mine = co.CostTable(co.read_cost_table(overrides=user))  # a user table without a rating_bucket column
    # same date (2015): the default's rating bucket outranks the user's maturity bucket
    assert co.cost_rule(mine, "half_spread_bp", kind="corporate", on=S, rating_bucket="AA", years=2.0).rule_id == \
        "HS21"
    assert co.cost_rule(mine, "half_spread_bp", kind="corporate", on=S, years=2.0).rule_id == "U1"
    # a later row wins whatever its specificity
    assert co.cost_rule(mine, "half_spread_bp", kind="corporate", on=S, rating_bucket="AA", years=5.0).rule_id == \
        "U2"
    bad = user.assign(rating_bucket=["AAA", "AA plus"])
    with pytest.raises(ValueError, match="rating_bucket 'AA PLUS' is not one of"):
        co.read_cost_table(overrides=bad)
    both = user.iloc[[0]].assign(rating_bucket="aa;AAA", rule_id="U3", effective_from="2026-06-01")
    frame = co.read_cost_table(overrides=both)
    assert frame.loc[frame["rule_id"] == "U3", "rating_bucket"].iloc[0] == "AAA;AA"  # upper case, notch order
