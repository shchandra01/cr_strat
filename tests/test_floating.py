"""Floating-rate bonds: T-bill WAYs, GoI FRB coupons as known at a time, the pricer, discount margin, corporate FRBs.

Typed-in facts: the RBI store's 182-day T-bill result of 11 Nov 2026 (WAP 96.8579 and WAY 6.5059, cut-off 96.8521 and
6.5183), the 27 Oct 2022 182-day WAP 96.7777 and cut-off 96.7581, and the three 182-day cut-off yields before
30 Oct 2022 (6.7647, 6.7400, 6.7195) with RBI's 7.69% FRB 2034 coupon for 30 Oct 2022 - 29 Apr 2023 (RBI release of
28 Oct 2022, read in a copy). Everything else is synthetic, with answers computed independently.
"""

import math
from dataclasses import replace
from datetime import date, datetime, time, timedelta

import pandas as pd
import pytest

from cr_strat.bonds import FixedRateBond
from cr_strat.conventions import GOI_FRB, NCD, days_30e_360
from cr_strat.corporates import CorporateBond
from cr_strat.curvefit import NssCurve
from cr_strat.floating import (
    FRB_RATE_COLUMNS,
    TBILL_COLUMNS,
    FloatingTerms,
    GoiFloatingRateBond,
    corporate_frb_rates,
    discount_margin_bp,
    floating_terms_from_row,
    forward_tbill_yield,
    frb_periods,
    goi_frb_bond,
    goi_frb_fixing,
    goi_frb_rates,
    index_dv01,
    load_frb_coupons,
    load_frb_terms,
    price_from_dm,
    rate_steps,
    read_fixings,
    spread_duration,
    spread_dv01,
    tbill_way_table,
    way_from_wap,
)

D = date
FRB2033 = {"isin": "IN0020200120", "name": "GOI FRB 2033", "issue_date": "2020-06-22", "maturity_date": "2033-09-22",
           "first_coupon_date": "2020-09-22", "index": "tbill182_way_avg3", "spread_pct": "1.22", "decimals": "2",
           "reset_months": "6"}


def tbills(rows):
    """Synthetic 182-day results: (auction date, WAY, public_from)."""
    return tbill_way_table(pd.DataFrame({"auction_date": [r[0] for r in rows], "tenor_days": [182] * len(rows),
                                         "way": [r[1] for r in rows], "public_from": [r[2] for r in rows]}))


def weekly(start: date, ways: list[float], at: str = "19:00") -> pd.DataFrame:
    rows = [(start + timedelta(days=7 * i), w, f"{start + timedelta(days=7 * i)}T{at}") for i, w in enumerate(ways)]
    return tbills(rows)


class FlatContinuous:
    """A flat continuously compounded discount curve on actual/365 from its valuation date."""

    def __init__(self, valuation_date: date, rate_pct: float):
        self.valuation_date, self.rate, self.t_max = valuation_date, rate_pct / 100, 50.0

    def discount(self, when):
        return math.exp(-self.rate * (when - self.valuation_date).days / 365)


class HalfYearCurve:
    """A flat discount curve measured in the G-sec's own 30E/360 half-years (for exact par identities)."""

    def __init__(self, valuation_date: date, rate_pct: float):
        self.valuation_date, self.rate, self.t_max = valuation_date, rate_pct / 100, 50.0

    def discount(self, when):
        return (1 + self.rate / 2) ** (-days_30e_360(self.valuation_date, when) / 180)


# ---------------------------------------------------------------- T-bill yields


def test_way_from_wap_reproduces_rbi():
    # T20: the 11 Nov 2026 result and 27 Oct 2022.
    assert round(way_from_wap(96.8579), 6) == 6.505886 and round(way_from_wap(96.8579), 4) == 6.5059
    assert round(way_from_wap(96.8521), 6) == 6.518285 and round(way_from_wap(96.8521), 4) == 6.5183
    assert round(way_from_wap(96.7777), 6) == 6.677473
    assert round(way_from_wap(96.7581), 6) == 6.719451
    # The rule uses WAYs: cut-off yields would have set FRB 2034 at 7.72 from 30 Oct 2022, not RBI's 7.69.
    cut = tbills([("2022-10-12", 6.7647, "2022-10-12"), ("2022-10-19", 6.7400, "2022-10-19"),
                  ("2022-10-27", 6.7195, "2022-10-27")])
    assert goi_frb_fixing("2022-10-30", 0.98, cut).coupon_pct == pytest.approx(7.72)
    with pytest.raises(ValueError, match="positive"):
        way_from_wap(0)


def test_tbill_table_from_store_rows():
    store = pd.DataFrame({"auction_date": ["2026-11-11", "2026-11-11", "2026-11-11"], "tenor_days": [91, 182, 364],
                          "wap": [98.5602, 96.8579, 93.8091], "way": [5.8594, 6.5059, 6.6176],
                          "cutoff_price": [98.5581, 96.8521, 93.79], "cutoff_yield": [5.8681, 6.5183, 6.6394],
                          "published": ["", "", ""], "release_date": ["2026-11-11"] * 3})
    table = tbill_way_table(store)
    assert list(table.columns) == list(TBILL_COLUMNS) and len(table) == 1
    row = table.iloc[0]
    assert (row["way_pct"], row["cutoff_pct"], row["way_source"]) == (6.5059, 6.5183, "printed")
    assert (row["public_from"], row["public_from_source"]) == (datetime(2026, 11, 11, 23, 59, 59), "release_date")
    fed = tbill_way_table(store.assign(published=["2026-11-11T19:30:00"] * 3, way=[None] * 3))
    assert fed["public_from"].iloc[0] == datetime(2026, 11, 11, 19, 30)
    assert fed["way_source"].iloc[0] == "from_wap" and round(fed["way_pct"].iloc[0], 6) == 6.505886
    with pytest.raises(ValueError, match="disagree"):
        tbill_way_table(store.assign(way=[5.8594, 6.5159, 6.6176]))


def test_tbill_table_needs_public_times():
    raw = pd.DataFrame({"auction_date": ["2026-11-11"], "tenor_days": [182], "cutoff_price": [96.8521]})
    with pytest.raises(ValueError, match="no public_from"):
        tbill_way_table(raw)
    ruled = tbill_way_table(raw, default_time=time(18, 0))
    assert ruled["public_from"].iloc[0] == datetime(2026, 11, 11, 18, 0)
    assert ruled["public_from_source"].iloc[0] == "rule"
    twice = pd.DataFrame({"auction_date": ["2026-11-11", "2026-11-11"], "tenor_days": [182, 182], "way": [6.5, 6.5],
                          "public_from": ["2026-11-12T10:00", "2026-11-11T19:00"]})
    assert tbill_way_table(twice)["public_from"].tolist() == [datetime(2026, 11, 11, 19, 0)]
    with pytest.raises(ValueError, match="listed twice"):
        tbill_way_table(twice.assign(way=[6.5, 6.6]))


# ---------------------------------------------------------------- GoI FRB coupons


def test_fixing_formula():
    # T23: synthetic WAYs 5.51, 5.53, 5.55 -> base 5.53 -> FRB 2033's 6.75.
    t = weekly(D(2026, 3, 4), [5.51, 5.53, 5.55])
    f = goi_frb_fixing("2026-03-22", 1.22, t)
    assert (f.auctions, f.base_pct, f.coupon_pct, f.status) == ((D(2026, 3, 4), D(2026, 3, 11), D(2026, 3, 18)),
                                                                 5.53, 6.75, "computed")
    assert f.public_from == datetime(2026, 3, 18, 19, 0)
    # Half up to two decimals: 5.515, 5.525, 5.535 average 5.525 -> 5.53; unrounded with decimals=None.
    t2 = weekly(D(2026, 3, 4), [5.515, 5.525, 5.535])
    assert goi_frb_fixing("2026-03-22", 1.22, t2).base_pct == 5.53
    assert goi_frb_fixing("2026-03-22", 1.22, t2, decimals=None).base_pct == pytest.approx(5.525)
    # Only the last three before the fixing day; one held on the fixing day counts only if asked.
    t3 = weekly(D(2026, 2, 25), [9.0, 5.51, 5.53, 5.55, 7.0])  # the fifth is on 25 Mar
    f3 = goi_frb_fixing("2026-03-25", 1.22, t3)
    assert f3.auctions == (D(2026, 3, 4), D(2026, 3, 11), D(2026, 3, 18)) and f3.base_pct == 5.53
    with_day = goi_frb_fixing("2026-03-25", 1.22, t3, include_fixing_day=True)
    assert with_day.auctions == (D(2026, 3, 11), D(2026, 3, 18), D(2026, 3, 25)) and with_day.base_pct == 6.03


def test_fixing_uses_only_public_results():
    t = weekly(D(2026, 3, 4), [5.51, 5.53, 5.55], at="15:30")
    with pytest.raises(ValueError, match="2 182-day results public by 2026-03-18 15:00"):
        goi_frb_fixing("2026-03-22", 1.22, t, as_of="2026-03-18T15:00")
    early = goi_frb_fixing("2026-03-22", 1.22, t, as_of="2026-03-18T15:00", allow_fewer=True)
    assert early.status == "projected" and early.auctions == (D(2026, 3, 4), D(2026, 3, 11)) and early.base_pct == 5.52
    assert "only 2 of 3" in early.notes
    later = goi_frb_fixing("2026-03-22", 1.22, t, as_of="2026-03-18T15:30")
    assert later.status == "computed" and later.coupon_pct == 6.75
    # Three results, but the last more than a week before the fixing day: another auction may still come.
    assert goi_frb_fixing("2026-04-01", 1.22, t).status == "projected"
    gappy = tbills([("2026-02-04", 5.5, "2026-02-04"), ("2026-03-11", 5.5, "2026-03-11"),
                    ("2026-03-18", 5.5, "2026-03-18")])
    assert "is the table complete" in goi_frb_fixing("2026-03-22", 1.22, gappy).notes


def test_rates_as_known_at_a_time():
    t = weekly(D(2026, 3, 4), [5.51, 5.53, 5.55])
    announced = pd.DataFrame({"isin": ["IN0020200120"], "period_start": ["2026-03-22"], "period_end": ["2026-09-22"],
                              "rate_pct": ["6.75"], "announced": ["2026-03-20T17:00:00"], "source": ["synthetic"],
                              "status": ["V"]})

    def period(as_of, **kw):
        r = goi_frb_rates(FRB2033, t, announced, as_of=as_of, **kw)
        assert list(r.columns) == list(FRB_RATE_COLUMNS)
        return r.set_index("period_start").loc[D(2026, 3, 22)]

    p = period("2026-03-19T10:00")  # the T-bill results fix it; the release is not out yet
    assert (p["status"], p["rate_pct"], p["public_from"]) == ("computed", 6.75, datetime(2026, 3, 18, 19, 0))
    p = period("2026-03-21T10:00")
    assert (p["status"], p["public_from"], p["public_from_source"]) == ("announced", datetime(2026, 3, 20, 17, 0),
                                                                       "release")
    p = period("2026-03-12T10:00", allow_fewer=True)  # two results: a flagged projection
    assert p["status"] == "projected" and p["rate_pct"] == pytest.approx(5.52 + 1.22) and pd.isna(p["public_from"])
    with_bad = announced.assign(rate_pct=["6.80"])
    noted = goi_frb_rates(FRB2033, t, with_bad, as_of="2026-03-21T10:00").set_index("period_start").loc[
        D(2026, 3, 22)]
    assert noted["rate_pct"] == 6.80 and "give 6.75, not the announced 6.80" in noted["notes"]


def test_unknown_release_time_is_estimated_at_the_fixing_day():
    announced = pd.DataFrame({"isin": ["IN0020200120"], "period_start": ["2025-09-22"], "period_end": ["2026-03-22"],
                              "rate_pct": ["6.82"], "announced": [""], "source": ["typed"], "status": ["P"]})
    r = goi_frb_rates(FRB2033, None, announced, as_of="2025-09-21T23:00").set_index("period_start")
    assert r.loc[D(2025, 9, 22), "status"] == "unknown"  # not yet public, and nothing to project from
    r = goi_frb_rates(FRB2033, None, announced, as_of="2025-09-22T00:00").set_index("period_start")
    row = r.loc[D(2025, 9, 22)]
    assert (row["status"], row["public_from"], row["public_from_source"]) == ("announced", datetime(2025, 9, 22),
                                                                             "estimated")
    nxt = r.loc[D(2026, 3, 22)]  # flat at the base the announced coupon implies
    assert nxt["status"] == "projected" and nxt["rate_pct"] == pytest.approx(6.82) and nxt["base_pct"] == \
        pytest.approx(5.60)


def test_forward_projection():
    curve = NssCurve(valuation_date=D(2026, 11, 20), t_min=0.25, t_max=40.0, method="nss",
                     params=(7.6, -2.0, -1.0, 2.0, 1.5, 8.0))
    t = weekly(D(2026, 10, 28), [6.40, 6.45, 6.50])
    r = goi_frb_rates(FRB2033, t, None, as_of="2026-11-20T20:00", projection="forward", curve=curve,
                      allow_fewer=True).set_index("period_start")
    fwd = r.loc[D(2027, 3, 22)]
    assert fwd["status"] == "projected" and fwd["base_pct"] == pytest.approx(forward_tbill_yield(curve, D(2027, 3, 22)))
    df0, df1 = float(curve.discount(D(2027, 3, 22))), float(curve.discount(D(2027, 9, 20)))
    assert fwd["base_pct"] == pytest.approx((df0 / df1 - 1) * 365 / 182 * 100)
    with pytest.raises(ValueError, match="curve="):
        goi_frb_rates(FRB2033, t, None, projection="forward")


def test_five_yearly_reset_uses_announced_coupons():
    terms = load_frb_terms().set_index("isin").loc["IN0020042050"].to_dict() | {"isin": "IN0020042050"}
    r = goi_frb_rates(terms, None, as_of="2026-11-19T20:00").set_index("period_start")
    assert r.loc[D(2026, 7, 25), "rate_pct"] == 6.66 and r.loc[D(2026, 7, 25), "status"] == "announced"
    assert r.loc[D(2026, 7, 25), "fixing_day"] == D(2025, 1, 25)
    later = r.loc[D(2030, 1, 25)]
    assert later["status"] == "projected" and later["rate_pct"] == 6.66


def test_shipped_frb_files():
    terms = load_frb_terms()
    assert terms["isin"].tolist() == ["IN0020210160", "IN0020180041", "IN0020200120", "IN0020210137", "IN0020042050"]
    spreads = dict(zip(terms["isin"], terms["spread_pct"]))
    assert spreads == {"IN0020210160": "0.64", "IN0020180041": "1.00", "IN0020200120": "1.22", "IN0020210137": "0.98",
                       "IN0020042050": ""}
    assert set(terms["status"]) <= {"V", "P"} and terms["source"].str.len().min() > 20
    coupons = load_frb_coupons()
    assert set(coupons["status"]) <= {"V", "P"} and (coupons["source"] != "").all()
    frb2034 = coupons[(coupons["isin"] == "IN0020210137") & (coupons["period_start"] == "2022-10-30")].iloc[0]
    assert (frb2034["rate_pct"], frb2034["announced"], frb2034["status"]) == ("7.69", "2022-10-28", "V")
    for rec in terms.to_dict("records"):  # every shipped bond builds a schedule
        assert frb_periods(rec)[-1][1].isoformat() == rec["maturity_date"]


# ---------------------------------------------------------------- the FRB pricer


def _frb2033(current: float, projected: float, from_: date, index: float) -> GoiFloatingRateBond:
    starts = [s for s, _ in frb_periods(FRB2033) if s >= from_]
    rates = tuple((s, current if s == starts[0] else projected, "computed" if s == starts[0] else "projected")
                  for s in starts)
    return GoiFloatingRateBond(D(2033, 9, 22), D(2020, 6, 22), 1.22, rates, first_coupon_date=D(2020, 9, 22),
                               index_pct=index)


def test_frb_by_composition():
    # T22: FRB 2033 for 20 Nov 2026; synthetic rates 7.70% now and 6.51 + 1.22 = 7.73% later.
    bond, s = _frb2033(7.70, 7.73, D(2026, 9, 22), 6.51), D(2026, 11, 20)
    unit = FixedRateBond(1.0, D(2033, 9, 22), GOI_FRB, issue_date=D(2020, 6, 22), first_coupon_date=D(2020, 9, 22))
    assert unit.accrued_interest(s) == pytest.approx(0.1611111111, abs=1e-10)
    assert bond.accrued_interest(s) == pytest.approx(1.2405555556, abs=1e-10)
    flows = bond.cash_flows(s)
    assert len(flows) == 15 and flows[0].date == D(2027, 3, 22) and flows[0].periods == pytest.approx(0.6777777778)
    assert flows[0].amount == pytest.approx(7.70 / 2) and flows[1].amount == pytest.approx(7.73 / 2)
    for clean, y, dm in ((100.0, 7.7250833578, 121.508336), (101.0, 7.5353845263, 102.538453)):
        assert bond.yield_from_price(clean, s) == pytest.approx(y, abs=1e-9)
        assert discount_margin_bp(bond, s, clean) == pytest.approx(dm, abs=1e-6)
    assert bond.coupon_rate(s) == 7.70 and bond.rate_status(s) == "computed"
    assert bond.previous_coupon_date(s) == D(2026, 9, 22) and bond.next_coupon_date(s) == D(2027, 3, 22)


def test_constant_rates_price_as_the_fixed_rate_bond():
    starts = [s for s, _ in frb_periods(FRB2033)]
    frb = GoiFloatingRateBond(D(2033, 9, 22), D(2020, 6, 22), 1.22, tuple((st, 7.1) for st in starts),
                              first_coupon_date=D(2020, 9, 22))
    fixed = FixedRateBond(7.1, D(2033, 9, 22), GOI_FRB, issue_date=D(2020, 6, 22), first_coupon_date=D(2020, 9, 22))
    for s in (D(2020, 7, 1), D(2026, 11, 20), D(2033, 6, 1)):
        for y in (5.0, 7.1, 9.3):
            assert frb.dirty_price(y, s) == pytest.approx(fixed.dirty_price(y, s), abs=1e-12)
            assert frb.dv01(y, s) == pytest.approx(fixed.dv01(y, s), abs=1e-14)
        assert frb.accrued_interest(s) == pytest.approx(fixed.accrued_interest(s), abs=1e-14)


def test_discount_margin_identities():
    # T21: FRB 2033 on the reset date 22 Mar 2027, 13 half-years left, base 5.53, spread 1.22, flat 6.75%.
    bond, s = _frb2033(6.75, 6.75, D(2027, 3, 22), 5.53), D(2027, 3, 22)
    assert price_from_dm(bond, s, 122.0)[1] == pytest.approx(100.0, abs=1e-11)
    assert price_from_dm(bond, s, 0.0)[1] == pytest.approx(106.5859092624, abs=1e-10)
    assert discount_margin_bp(bond, s, 99.0) == pytest.approx(141.377955, abs=1e-6)
    flat = HalfYearCurve(s, 6.75)
    assert price_from_dm(bond, s, 0.0, curve=flat, form="curve")[1] == pytest.approx(100.0, abs=1e-10)
    assert discount_margin_bp(bond, s, 100.0, curve=flat, form="curve") == pytest.approx(0.0, abs=1e-7)
    # Round trips and derivatives, both forms, off a reset date.
    bond, s = _frb2033(7.70, 7.73, D(2026, 9, 22), 6.51), D(2026, 11, 20)
    curve = NssCurve(valuation_date=D(2026, 11, 16), t_min=0.25, t_max=40.0, method="nss",
                     params=(7.6, -2.0, -1.0, 2.0, 1.5, 8.0))
    for form, kw in (("index", {}), ("curve", {"curve": curve})):
        for dm in (-40.0, 35.0, 180.0):
            clean, dirty = price_from_dm(bond, s, dm, form=form, **kw)
            assert dirty - clean == pytest.approx(bond.accrued_interest(s))
            assert discount_margin_bp(bond, s, clean, form=form, **kw) == pytest.approx(dm, abs=1e-7)
            numeric = (price_from_dm(bond, s, dm - 0.5, form=form, **kw)[1]
                       - price_from_dm(bond, s, dm + 0.5, form=form, **kw)[1])
            assert spread_dv01(bond, s, dm, form=form, **kw) == pytest.approx(numeric, rel=1e-6)
    assert spread_duration(bond, s, 120.0) == pytest.approx(
        spread_dv01(bond, s, 120.0) * 1e4 / price_from_dm(bond, s, 120.0)[1])
    with pytest.raises(ValueError, match="valuation date"):
        price_from_dm(bond, s, 0.0, curve=replace(curve, valuation_date=D(2026, 12, 1)), form="curve")
    # Discount factors are taken relative to settlement: a flat curve valued four days earlier prices the same.
    early, same = FlatContinuous(D(2026, 11, 16), 7.2), FlatContinuous(s, 7.2)
    assert price_from_dm(bond, s, 50.0, curve=early, form="curve")[1] == pytest.approx(
        price_from_dm(bond, s, 50.0, curve=same, form="curve")[1], abs=1e-12)
    expected = sum(f.amount * math.exp(-(0.072 + 0.005) * (f.date - s).days / 365) for f in bond.cash_flows(s))
    assert price_from_dm(bond, s, 50.0, curve=early, form="curve")[1] == pytest.approx(expected, abs=1e-10)


def test_index_dv01_is_near_zero_on_a_reset_date():
    on_reset = _frb2033(6.75, 6.75, D(2027, 3, 22), 5.53)
    s = D(2027, 3, 22)
    # On the reset date at DM = spread the projected coupons and the discount rate move together: only the fixed
    # current coupon is exposed, so the index DV01 is that of a six-month fixed coupon: 0.5 / (1 + 6.75%/2) x 1 bp.
    assert index_dv01(on_reset, s, 122.0) == pytest.approx(0.5 / (1 + 0.0675 / 2) * 0.01, rel=1e-6)
    assert index_dv01(on_reset, s, 122.0) < spread_dv01(on_reset, s, 122.0) / 10
    later = _frb2033(7.70, 7.73, D(2026, 9, 22), 6.51)
    assert 0 < index_dv01(later, D(2026, 11, 20), 121.5) < spread_dv01(later, D(2026, 11, 20), 121.5)


def test_frb_refusals():
    starts = [s for s, _ in frb_periods(FRB2033)]
    with pytest.raises(ValueError, match="not the start of a coupon period"):
        GoiFloatingRateBond(D(2033, 9, 22), D(2020, 6, 22), 1.22, ((D(2026, 10, 1), 7.0),))
    with pytest.raises(ValueError, match="two rates"):
        GoiFloatingRateBond(D(2033, 9, 22), D(2020, 6, 22), 1.22, ((starts[5], 7.0), (starts[5], 7.1)))
    bond = GoiFloatingRateBond(D(2033, 9, 22), D(2020, 6, 22), 1.22, ((D(2026, 9, 22), 7.0),),
                               first_coupon_date=D(2020, 9, 22))
    with pytest.raises(ValueError, match="no rate for the coupon period from 2027-03-22"):
        bond.dirty_price(7.0, D(2026, 11, 20))
    with pytest.raises(ValueError, match="index_pct"):
        discount_margin_bp(bond.with_rates([(st, 7.0) for st in starts]), D(2026, 11, 20), 100.0)
    with pytest.raises(TypeError, match="G-sec"):
        GoiFloatingRateBond(D(2033, 9, 22), D(2020, 6, 22), 1.22, (), conventions=NCD)


def test_goi_frb_bond_from_rates():
    t = weekly(D(2026, 10, 28), [6.40, 6.45, 6.50])
    rates = goi_frb_rates(FRB2033, t, None, as_of="2026-11-20T20:00")
    bond = goi_frb_bond(FRB2033, rates)
    assert bond.index_pct == pytest.approx(6.45)  # flat at the latest computable base
    current = rates.set_index("period_start").loc[D(2026, 9, 22)]
    assert current["status"] == "projected" and "not in the data" in current["notes"]
    assert discount_margin_bp(bond, D(2026, 11, 20), 100.0) == pytest.approx(
        (bond.yield_from_price(100.0, D(2026, 11, 20)) - 6.45) * 100)
    again = goi_frb_rates(FRB2033, t, None, as_of="2026-11-20T20:00")
    assert again.to_csv(index=False) == rates.to_csv(index=False)


# ---------------------------------------------------------------- corporate FRBs


FIXINGS = pd.DataFrame({"benchmark": ["tb91"] * 4, "date": ["2025-09-10", "2025-12-10", "2026-03-11", "2026-06-10"],
                        "value_pct": ["5.40", "5.30", "5.20", "5.50"],
                        "public_from": ["2025-09-10T19:00", "2025-12-10T19:00", "2026-03-11T19:00",
                                        "2026-06-10T19:00"]})


def test_corporate_frb_rates_and_pricing():
    bond = CorporateBond(0.0, D(2028, 9, 15), D(2025, 9, 15), frequency=4)
    terms = FloatingTerms("tb91", 2.30, 3, fixing_lag_days=1)
    r = corporate_frb_rates(bond, terms, FIXINGS, as_of="2026-04-01T20:00")
    assert r["status"].tolist()[:4] == ["fixed", "fixed", "fixed", "projected"]
    assert r["rate_pct"].tolist()[:4] == [7.7, 7.6, 7.5, 7.5]
    later = corporate_frb_rates(bond, terms, FIXINGS, as_of="2026-06-15T20:00")
    assert later.loc[3, ["status", "rate_pct"]].tolist() == ["fixed", 7.8]
    early = corporate_frb_rates(bond, terms, FIXINGS, as_of="2026-06-10T18:00")  # the June fixing is not out
    assert early.loc[3, ["status", "rate_pct"]].tolist() == ["projected", 7.5]
    priced = bond.with_rates(rate_steps(r))
    s = D(2026, 4, 1)
    assert priced.rate_on(s) == 7.5
    period = next(p for p in priced.periods() if p.start <= s < p.due)
    assert priced.accrued_interest(s) == pytest.approx(7.5 * (s - period.start).days / 365)
    assert priced.clean_price(8.0, s) == pytest.approx(99.378566, abs=1e-6)


def test_floating_terms_rules():
    bond = CorporateBond(0.0, D(2028, 9, 15), D(2025, 9, 15), frequency=4)
    capped = FloatingTerms("tb91", 2.30, 6, floor_pct=7.65, cap_pct=7.75, decimals=2, lookback=2)
    r = corporate_frb_rates(bond, capped, FIXINGS, as_of="2026-07-01")
    assert r["period_start"].tolist()[:3] == [D(2025, 9, 15), D(2026, 3, 15), D(2026, 9, 15)]  # every second period
    # The first reset needs two fixings and the table has one: its period has ended, so it stays unknown.
    assert r["status"].tolist()[:3] == ["unknown", "fixed", "projected"]
    assert r["rate_pct"].tolist()[1:3] == [7.65, 7.65]  # (5.30 + 5.20)/2 + 2.30 = 7.55, floored; then 5.35 + 2.30
    assert rate_steps(r)[0][:2] == (D(2026, 3, 15), 7.65)
    assert FloatingTerms("x", 2.0, 3, decimals=1).coupon(5.65) == 7.7  # rounded half up
    assert FloatingTerms("x", 2.0, 3, cap_pct=7.0).coupon(5.5) == 7.0
    with pytest.raises(ValueError, match="multiple"):
        corporate_frb_rates(bond, FloatingTerms("tb91", 2.3, 4), FIXINGS)
    with pytest.raises(ValueError, match="floor"):
        FloatingTerms("tb91", 2.3, 3, floor_pct=8.0, cap_pct=7.0)
    with pytest.raises(ValueError, match="public_from"):
        read_fixings(FIXINGS.drop(columns="public_from"))
    with pytest.raises(ValueError, match="no rate for the resets"):
        rate_steps(corporate_frb_rates(bond, FloatingTerms("other", 2.3, 3), FIXINGS))
    row = {"isin": "INE1", "benchmark": "tb91", "spread_pct": "2.3", "reset_months": "3", "fixing_lag_days": "",
           "lookback": "", "floor_pct": "", "cap_pct": "8", "decimals": "2"}
    assert floating_terms_from_row(row) == FloatingTerms("tb91", 2.3, 3, 0, 1, None, 8.0, 2)


def test_frb_price_command(capsys, tmp_path):
    from cr_strat.cli import main
    results = tmp_path / "tbills.csv"
    pd.DataFrame({"auction_date": ["2026-10-28", "2026-11-04", "2026-11-11"], "tenor_days": [182] * 3,
                  "wap": [96.9000, 96.8800, 96.8579], "published": ["", "", ""],
                  "release_date": ["2026-10-28", "2026-11-04", "2026-11-11"]}).to_csv(results, index=False)
    args = ["frb-price", "--isin", "IN0020200120", "--tbills", str(results), "--as-of", "2026-11-19T20:00",
            "--settlement", "2026-11-20"]
    assert main([*args, "--dm", "122", "--rates"]) == 0
    text = capsys.readouterr().out
    lines = {line[:19].strip(): line[19:].strip() for line in text.splitlines() if line[:19].strip()}
    base = round((way_from_wap(96.9000) + way_from_wap(96.8800) + way_from_wap(96.8579)) / 3, 2)
    assert lines["index (flat)"] == f"{base:.4f}%"
    assert lines["discount margin"].startswith("122.0000 bp")
    assert "the coupon fixed on 2026-09-22 is not in the data" in text
    assert main([*args, "--price", "100"]) == 0
    out = capsys.readouterr().out
    assert "spread duration" in out and "index dv01" in out
    assert main(["frb-price", "--isin", "IN0000000000", "--settlement", "2026-11-20", "--price", "100"]) == 2
    assert "not in" in capsys.readouterr().err
    # Too few results without --allow-fewer and no announced coupon to fall back on: no index.
    assert main([*args, "--no-coupons", "--price", "100", "--as-of", "2026-11-05T10:00"]) == 2
    assert "--index" in capsys.readouterr().err
