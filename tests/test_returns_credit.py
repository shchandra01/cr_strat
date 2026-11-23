"""Tests of the return engine with corporate bonds and GoI FRBs (synthetic data only).

Corporate bonds are priced by ``corporates`` (SEBI actual/actual, record dates 15 days before each due date), GoI FRBs
by ``floating`` on the shipped terms of FRB 2031 (spread 1.00%, coupons on 7 Jun and 7 Dec) with synthetic weekly
182-day T-bill results; every mark, yield and fixing is synthetic. Each run is checked for the identity: the lines
sum to the NAV change exactly.
"""

from dataclasses import replace
from datetime import date, datetime, time, timedelta

import pandas as pd
import pytest
from test_returns import CAL, LEN, NOCOST, assert_identity, funding, mmd, settings

from cr_strat import costs as co
from cr_strat import funding as fu
from cr_strat import returns as rt
from cr_strat.corporates import CorporateBond

NCD_ISIN = "INE000C09011"
NCD = CorporateBond(9.0, date(2030, 12, 15), date(2023, 12, 15))  # record date of the 15 Dec 2026 coupon: 30 Nov
NCD_TERMS = {"isin": NCD_ISIN, "kind": "corporate", "coupon_pct": "9.0", "frequency": "1", "issue_date": "2023-12-15",
             "maturity_date": "2030-12-15", "rating_bucket": "AAA"}
FRB_ISIN = "IN0020180041"  # GOI FRB 2031 of the shipped terms
DAYS = CAL.business_days(date(2026, 11, 20), date(2026, 12, 18))


def ncd_marks(bond=NCD, isin=NCD_ISIN, days=DAYS, yld=8.5, calendar=CAL):
    return pd.DataFrame([{"date": d, "isin": isin, "clean_price": bond.clean_price(yld, calendar.settlement_date(d, 1))}
                         for d in days])


def trade(tid, book, side, day, isin=NCD_ISIN, face=1e8, **kw):
    return {"trade_id": tid, "book": book, "isin": isin, "side": side, "face": face, "trade_date": day, **kw}


def capital(books, amount=2e8, day="2026-11-20"):
    return pd.DataFrame([{"date": day, "book": b, "amount": amount} for b in books])


def run(trades, books, *, marks=None, terms=(NCD_TERMS,), end=DAYS[-1], st=None, amount=2e8, **market):
    data = rt.MarketData(marks=ncd_marks() if marks is None else marks,
                         corporate_terms=pd.DataFrame(list(terms)) if terms else None, **market)
    return rt.run_book(trades, flows=capital(books, amount), market=data, settings=st or settings(costs=NOCOST),
                       end=end)


def test_a_corporate_bond_through_its_record_date():
    """The 15 Dec 2026 coupon goes to the holder at the close of its record date, 30 Nov: a buyer settling on it is
    cum, one settling on 2 Dec is not; a seller settling on 3 Dec still receives it, on 15 Dec."""
    assert NCD.record_date(date(2026, 12, 15)) == date(2026, 11, 30)
    trades = [trade("H1", "hold", "buy", "2026-11-20"), trade("L1", "late", "buy", "2026-12-01"),
              trade("S1", "sold", "buy", "2026-11-20"), trade("S2", "sold", "sell", "2026-12-02"),
              trade("R1", "on_rd", "buy", "2026-11-27")]
    res = run(trades, ("hold", "late", "sold", "on_rd"))
    assert_identity(res)
    nav = res.nav.set_index(["book", "date"])
    coupon = 1e8 / 100 * 9.0  # an annual coupon is the full rate: Rs 90 lakh
    for book, entitled in (("hold", True), ("late", False), ("sold", True), ("on_rd", True)):
        assert nav.loc[(book, date(2026, 11, 30)), "receivables"] == 0.0
        assert nav.loc[(book, date(2026, 12, 1)), "receivables"] == (coupon if entitled else 0.0)
        assert nav.loc[(book, date(2026, 12, 15)), "receivables"] == 0.0  # paid on its due date, a Tuesday
    # accrual: actual days held at 9% over 365 (the coupon year 15 Dec 2025 - 15 Dec 2026 has 365 days)
    held = {"hold": (date(2026, 11, 23), date(2026, 12, 18)), "late": (date(2026, 12, 2), date(2026, 12, 18)),
            "sold": (date(2026, 11, 23), date(2026, 12, 3)), "on_rd": (date(2026, 11, 30), date(2026, 12, 18))}
    for book, (start, stop) in held.items():
        assert res.lines(book)["accrual"].sum() == pytest.approx(1e8 * 0.09 * (stop - start).days / 365, rel=1e-12)
    # the purchase settling ex-interest pays the clean price plus a negative accrued interest
    fill = res.fills.set_index("trade_id").loc["L1"]
    ai = NCD.accrued_interest(date(2026, 12, 2))
    assert ai == pytest.approx(-9.0 * 13 / 365, abs=1e-12)
    cash = nav.loc[("late", date(2026, 12, 2)), "cash"]
    assert cash == pytest.approx(2e8 - 1e8 / 100 * (fill["fill"] + ai), abs=1e-6)
    pos = res.positions[res.positions["book"] == "hold"].set_index("date")
    assert "is ex-interest" in pos.loc[date(2026, 11, 30), "notes"]  # its T+1 mark settles on 1 Dec
    assert pos.loc[date(2026, 11, 30), "accrued"] > 0 > pos.loc[date(2026, 12, 1), "accrued"]


def frb_tbills(late: date | None = None, at: time = time(18, 30)) -> pd.DataFrame:
    """Weekly 182-day results from 6 May 2026, WAY 5.60% rising 2 bp a week; the 2 Dec result public late if asked."""
    rows, d, k = [], date(2026, 5, 6), 0
    while d <= date(2026, 12, 16):
        public = datetime.combine(late if (late and d == date(2026, 12, 2)) else d, at)
        rows.append({"auction_date": d.isoformat(), "tenor_days": 182, "way_pct": round(5.60 + 0.02 * k, 4),
                     "public_from": public.isoformat()})
        d += timedelta(days=7)
        k += 1
    return pd.DataFrame(rows)


def frb_run(tbills, st=None):
    marks = pd.DataFrame([{"date": d, "isin": FRB_ISIN, "clean_price": 100.25} for d in DAYS])
    return run([trade("F1", "frb", "buy", "2026-11-20", isin=FRB_ISIN, kind="frb")], ("frb",), marks=marks,
               terms=(), tbills=tbills, st=st)


def test_an_frb_through_its_reset_with_coupons_only_as_public():
    res = frb_run(frb_tbills())
    assert_identity(res)
    pos = res.positions.set_index("date")
    # 7 Jun - 7 Dec: 20 May, 27 May and 3 Jun results, mean 5.66, plus 1.00; from 7 Dec: 18 Nov, 25 Nov and 2 Dec
    assert pos.loc[date(2026, 12, 4), "notes"] == "coupon 6.6600% computed"
    assert pos.loc[date(2026, 12, 8), "notes"] == "coupon 7.1800% computed"
    assert pos.loc[date(2026, 12, 4), "accrued"] == pytest.approx(6.66 * 177 / 360, rel=1e-12)  # 30E/360 to 4 Dec
    assert pos.loc[date(2026, 12, 8), "accrued"] == pytest.approx(7.18 / 360, rel=1e-12)
    cash = res.nav.set_index("date")["cash"]
    assert cash.loc[date(2026, 12, 7)] - cash.loc[date(2026, 12, 4)] == pytest.approx(1e6 * 6.66 / 2, rel=1e-12)
    assert "coupon_reset" not in res.lines("frb")  # every coupon was known before it began to accrue
    # published late, on 9 Dec: the 7 Dec coupon is projected flat (11, 18 and 25 Nov: 7.16%) until then
    late = frb_run(frb_tbills(late=date(2026, 12, 9)))
    assert_identity(late)
    pos = late.positions.set_index("date")
    assert pos.loc[date(2026, 12, 8), "notes"] == "coupon 7.1600% projected"
    assert pos.loc[date(2026, 12, 9), "notes"] == "coupon 7.1800% computed"
    reset = late.lines("frb")["coupon_reset"]
    # the accrued interest of 8 Dec (one day) restated from 7.16% to 7.18% on Rs 10 crore
    assert reset.loc[date(2026, 12, 9)] == pytest.approx(1e6 * 0.02 / 360, rel=1e-9)
    assert (reset.drop(date(2026, 12, 9)) == 0).all()
    assert any("current coupon was projected" in n for n in late.notes)
    # a result public at 15:30 is not used by a valuation at 15:00 that day: the 2 Dec result published at 15:30 on
    # 7 Dec fixes the coupon from 7 Dec only from the next day's 15:00 valuation; a 20:00 valuation uses it on 7 Dec
    early = settings(costs=NOCOST, valuation_time=time(15, 0), decision_time=time(15, 0))
    timed = frb_run(frb_tbills(late=date(2026, 12, 7), at=time(15, 30)), st=early)
    assert_identity(timed)
    notes = timed.positions.set_index("date")["notes"]
    assert notes.loc[date(2026, 12, 4)] == "coupon 6.6600% computed"
    assert notes.loc[date(2026, 12, 7)] == "coupon 7.1600% projected"
    assert notes.loc[date(2026, 12, 8)] == "coupon 7.1800% computed"
    evening = frb_run(frb_tbills(late=date(2026, 12, 7), at=time(15, 30)))
    assert evening.positions.set_index("date").loc[date(2026, 12, 7), "notes"] == "coupon 7.1800% computed"


def test_amortisation_pays_cash_and_reduces_the_outstanding_face():
    isin = "INE000AMRT01"
    terms = {"isin": isin, "kind": "corporate", "coupon_pct": "9.0", "frequency": "1", "issue_date": "2024-12-15",
             "maturity_date": "2028-12-15"}
    amort = pd.DataFrame([{"isin": isin, "date": "2026-12-15", "principal_per100": "50"}])
    bond = CorporateBond(9.0, date(2028, 12, 15), date(2024, 12, 15), amortisation=((date(2026, 12, 15), 50.0),))
    res = run([trade("A1", "b", "buy", "2026-11-20", isin=isin)], ("b",), marks=ncd_marks(bond, isin), terms=(terms,),
              corporate_amortisation=amort)
    assert_identity(res)
    nav = res.nav.set_index("date")
    assert nav.loc[date(2026, 12, 1), "receivables"] == pytest.approx(1e6 * (9.0 + 50.0))
    assert nav.loc[date(2026, 12, 15), "cash"] - nav.loc[date(2026, 12, 14), "cash"] == pytest.approx(5.9e7)
    pos = res.positions.set_index("date")
    assert pos.loc[date(2026, 12, 16), "face"] == 1e8  # in original face, as corporates prices it
    assert "outstanding 50 per 100 original face" in pos.loc[date(2026, 12, 16), "notes"]
    assert pos.loc[date(2026, 12, 16), "value"] == pytest.approx(
        1e6 * (bond.clean_price(8.5, date(2026, 12, 17)) + bond.accrued_interest(date(2026, 12, 16))), rel=1e-12)
    # on its record date, 30 Nov, the T+1 mark settles ex the principal while the holder at the close is owed it: the
    # mark is restated to settle that day, cum, so the NAV does not fall by the principal for a day
    rd = date(2026, 11, 30)
    assert pos.loc[rd, "settlement_date"] == rd
    assert pos.loc[rd, "value"] == pytest.approx(1e6 * (bond.clean_price(8.5, rd) + bond.accrued_interest(rd)),
                                                 rel=1e-12)
    # the next day the principal is booked at face against the clean price's fall at the same yield: the difference
    # is the discount of 50 from 15 Dec to 30 Nov
    pull = res.lines("b").loc[date(2026, 12, 1), "pull_to_par"]
    assert pull == pytest.approx(1e6 * (50.0 + bond.clean_price(8.5, date(2026, 12, 2)) - bond.clean_price(8.5, rd)),
                                 rel=1e-9)
    assert 1e5 < pull < 2.5e5
    # no marks from 27 Nov to 2 Dec: the price held from 26 Nov is cum the principal, so across the record date its
    # yield is held instead (holding the price would count the principal twice, in the bond and the receivable)
    gap = (date(2026, 11, 27), date(2026, 11, 30), date(2026, 12, 1), date(2026, 12, 2))
    marks = ncd_marks(bond, isin)
    stale = run([trade("A1", "b", "buy", "2026-11-20", isin=isin)], ("b",), marks=marks[~marks["date"].isin(gap)],
                terms=(terms,), corporate_amortisation=amort)
    assert_identity(stale)
    spos, snav = stale.positions.set_index("date"), stale.nav.set_index("date")["nav"]
    assert spos.loc[date(2026, 12, 1), "mark_source"] == "stale"
    held = bond.yield_from_price(bond.clean_price(8.5, date(2026, 11, 27)), rd)  # the held price at 30 Nov
    assert spos.loc[date(2026, 12, 1), "clean_mark"] == pytest.approx(bond.clean_price(held, date(2026, 12, 2)),
                                                                      rel=1e-12)
    assert abs(snav.loc[date(2026, 12, 1)] - nav.loc[date(2026, 12, 1), "nav"]) < 1e4  # not Rs 5 crore higher


def test_an_exercised_call_redeems_once_its_notice_is_public():
    isin = "INE000CALL01"
    terms = {"isin": isin, "kind": "corporate", "coupon_pct": "8.0", "frequency": "1", "issue_date": "2024-01-15",
             "maturity_date": "2034-01-15"}
    options = pd.DataFrame([{"isin": isin, "kind": "call", "date": "2027-01-15", "exercised": "yes",
                             "exercised_public_from": "2026-12-01T17:00"}])
    base = CorporateBond(8.0, date(2034, 1, 15), date(2024, 1, 15))
    called = base.to_workout(date(2027, 1, 15))
    days = LEN.business_days(date(2026, 11, 20), date(2027, 1, 20))
    rows = []
    for d in days:
        s = LEN.settlement_date(d, 1)
        bond, y = (base, 7.5) if d < date(2026, 12, 1) else (called, 7.0)
        if s <= called.last_cum_settlement():
            rows.append({"date": d, "isin": isin, "clean_price": bond.clean_price(y, s)})
    st = settings(costs=NOCOST, project_calendar=True)
    res = run([trade("C1", "b", "buy", "2026-11-20", isin=isin)], ("b",), marks=pd.DataFrame(rows), terms=(terms,),
              end=days[-1], st=st, corporate_options=options)
    assert_identity(res)
    pos = res.positions.set_index("date")
    assert pos.loc[date(2026, 11, 30), "dv01"] > 40 * pos.loc[date(2026, 12, 1), "dv01"]  # to 2034, then to the call
    assert "redeemed on 2027-01-15 at 100" in pos.loc[date(2026, 12, 1), "notes"]
    assert pos.index.max() == date(2026, 12, 31)  # the call's record date: the position closes after its close
    nav = res.nav.set_index("date")
    assert nav.loc[date(2027, 1, 1), "receivables"] == pytest.approx(1e6 * 108.0)  # coupon and call price
    assert nav.loc[date(2027, 1, 15), "receivables"] == 0.0 and nav.loc[date(2027, 1, 1), "bonds"] == 0.0
    # before its notice the exercise is not seen: the same run with a notice one day later keeps 1 Dec to maturity
    later = run([trade("C1", "b", "buy", "2026-11-20", isin=isin)], ("b",), marks=pd.DataFrame(rows), terms=(terms,),
                end=date(2026, 12, 4), st=st,
                corporate_options=options.assign(exercised_public_from="2026-12-02T17:00"))
    assert_identity(later)
    lpos = later.positions.set_index("date")
    assert lpos.loc[date(2026, 12, 1), "notes"] == "" and "redeemed on" in lpos.loc[date(2026, 12, 2), "notes"]


def test_a_rating_step_accrues_from_when_it_was_public():
    isin = "INE000STEP01"
    terms = {"isin": isin, "kind": "corporate", "coupon_pct": "8.5", "frequency": "1", "issue_date": "2026-06-01",
             "maturity_date": "2029-06-01", "issuer_id": "ISS1"}
    steps = pd.DataFrame([{"isin": isin, "threshold": "AA", "step_bp_per_notch": "25", "effective": "from_event"}])
    ratings = pd.DataFrame([
        {"issuer_id": "ISS1", "agency": "CRISIL", "rating": "CRISIL AA/Stable", "public_from": "2026-05-20"},
        {"issuer_id": "ISS1", "agency": "CRISIL", "rating": "CRISIL AA-/Negative", "public_from": "2026-12-02T18:00"}])
    marks = pd.DataFrame([{"date": d, "isin": isin, "clean_price": 100.0} for d in DAYS])
    res = run([trade("P1", "b", "buy", "2026-11-20", isin=isin)], ("b",), marks=marks, terms=(terms,),
              corporate_steps=steps, ratings=ratings)
    assert_identity(res)
    accrual = res.lines("b")["accrual"]
    assert accrual.loc[date(2026, 12, 3)] == pytest.approx(1e8 * 0.085 / 365, rel=1e-12)  # 2-3 Dec at 8.50%
    assert accrual.loc[date(2026, 12, 4)] == pytest.approx(1e8 * 0.0875 / 365, rel=1e-12)  # from 3 Dec at 8.75%
    notes = res.positions.set_index("date")["notes"]
    assert notes.loc[date(2026, 12, 1)] == "" and notes.loc[date(2026, 12, 2)] == "coupon 8.75% from its steps"
    with pytest.raises(ValueError, match="MarketData.ratings is not given"):
        run([trade("P1", "b", "buy", "2026-11-20", isin=isin)], ("b",), marks=marks, terms=(terms,),
            corporate_steps=steps)


def test_a_corporate_frb_uses_each_fixing_from_its_public_time():
    isin = "INE000FRN001"
    terms = {"isin": isin, "kind": "frb_corp", "coupon_pct": "8.0", "frequency": "4", "issue_date": "2026-09-15",
             "maturity_date": "2028-09-15"}
    floating = pd.DataFrame([{"isin": isin, "benchmark": "TB91", "spread_pct": "2.0", "reset_months": "3"}])
    fixings = pd.DataFrame([
        {"benchmark": "TB91", "date": "2026-09-15", "value_pct": "5.50", "public_from": "2026-09-15T19:00"},
        {"benchmark": "TB91", "date": "2026-12-15", "value_pct": "5.80", "public_from": "2026-12-16T21:00"}])
    marks = pd.DataFrame([{"date": d, "isin": isin, "clean_price": 100.10} for d in DAYS])
    res = run([trade("Q1", "b", "buy", "2026-11-20", isin=isin)], ("b",), marks=marks, terms=(terms,),
              corporate_floating=floating, benchmark_fixings=fixings)
    assert_identity(res)
    nav = res.nav.set_index("date")
    q3 = 1e6 * 7.50 * 91 / 365  # 15 Sep - 15 Dec at 5.50 + 2.00 (SEBI actual/actual, 2026 not a leap year)
    assert nav.loc[date(2026, 12, 1), "receivables"] == pytest.approx(q3, rel=1e-12)  # record date 30 Nov
    # the 15 Dec fixing is public at 21:00 on 16 Dec, after that day's 20:00 valuation: 16 Dec accrues at the flat
    # projection (5.50 + 2.00), and 17 Dec restates that day at 7.80% before accruing at it
    accrual = res.lines("b")["accrual"]
    assert accrual.loc[date(2026, 12, 16)] == pytest.approx(1e8 * 0.075 / 365, rel=1e-12)
    reset = res.lines("b")["coupon_reset"]
    assert reset.loc[date(2026, 12, 17)] == pytest.approx(1e8 * 0.003 / 365, rel=1e-9)
    assert (reset.drop(date(2026, 12, 17)) == 0).all()
    assert accrual.loc[date(2026, 12, 17)] == pytest.approx(1e8 * 0.078 / 365, rel=1e-12)


def corporate_funding() -> dict:
    """``funding()`` with a corporate bond repo segment 15 bp over market repo (5.72%: the store's 13 Nov spread)."""
    frame = mmd(date(2026, 9, 1), date(2026, 12, 31))
    frame["corp_bond_repo_rate_pct"] = frame["market_repo_rate_pct"].map(lambda r: None if r is None else r + 0.15)
    frame["corp_bond_repo_volume_cr"] = frame["market_repo_volume_cr"] / 50
    return funding() | {"corp_bond_repo": fu.funding_rates(frame, "corp_bond_repo")}


def test_corporate_bonds_are_not_treps_collateral():
    books = ("b",)
    with pytest.raises(ValueError, match="not TREPS collateral here"):
        run([trade("T1", "b", "buy", "2026-11-20")], books, amount=2e7, funding=funding())
    cuts = pd.DataFrame([{"effective_date": "2015-01-01", "kind": "corporate", "haircut_pct": 10.0,
                          "source": "synthetic"}])
    # lent against in corporate bond repo, at its rate (the build funded it at market repo, the G-sec segment's)
    with pytest.raises(ValueError, match="no corp_bond_repo funding rates"):
        run([trade("T1", "b", "buy", "2026-11-20")], books, amount=2e7, funding=funding(), haircuts=cuts)
    res = run([trade("T1", "b", "buy", "2026-11-20")], books, amount=2e7, funding=corporate_funding(), haircuts=cuts)
    assert_identity(res)
    assert set(res.funding["segment"]) == {"corp_bond_repo"}  # TREPS lends nothing against it
    assert res.funding["rate_pct"].round(10).eq(5.72).all()
    assert res.positions["haircut_pct"].dropna().eq(10.0).all()
    night = res.funding.iloc[0]
    nights = (night["repay_date"] - night["date"]).days
    assert res.lines("b").loc[night["repay_date"], "funding_excess"] == pytest.approx(
        -night["borrowed"] * 5.72 * nights / 36500, rel=1e-12)
    as_gsec = run([trade("T1", "b", "buy", "2026-11-20")], books, amount=2e7, funding=funding(), haircuts=cuts,
                  st=settings(costs=NOCOST, funding=rt.FundingPolicy(corporate_segment="market_repo")))
    assert set(as_gsec.funding["segment"]) == {"market_repo"}  # the G-sec segment's rate, if the user says so


def test_corporate_trade_costs_and_t0_settlement():
    st = settings(costs=co.read_cost_table())
    res = run([trade("B1", "b", "buy", "2026-11-20", settlement_date="2026-11-20")], ("b",), st=st)
    fill = res.fills.iloc[0]
    assert fill["settlement_date"] == date(2026, 11, 20)  # T+0 is allowed for corporate bonds (handbook 3.5.1)
    assert fill["rules"].split(";") == ["HS20:U", "CS07:U", "PF04:P", "CU01:U", "GS01:P", "SD06:P"]
    consideration = 1e6 * (fill["fill"] + NCD.accrued_interest(date(2026, 11, 20)))
    assert fill["stamp_duty"] == pytest.approx(consideration * 1e-6)  # 0.0001%, on the buyer
    assert fill["fees"] == pytest.approx(consideration / 1e7 * 5)  # Rs 5 a crore of value (Rs 25 to Rs 500)
    assert fill["gst"] == pytest.approx(0.18 * fill["fees"])
    rated = run([trade("B1", "b", "buy", "2026-11-20", rating_bucket="AA-")], ("b",), st=st)
    assert rated.fills.iloc[0]["rules"].startswith("HS22:U")  # the trade's bucket over the terms'
    with pytest.raises(ValueError, match="after INE000C09011's redemption record date 2030-11-29"):
        run([trade("B1", "b", "buy", "2030-12-02", settlement_date="2030-12-03")], ("b",),
            st=settings(costs=NOCOST, project_calendar=True), end=date(2030, 12, 4))


def test_refusals_and_terms_checks():
    with pytest.raises(ValueError, match="no pricer in bonds.py: its terms come from MarketData.frb_terms"):
        run([trade("F1", "b", "buy", "2026-11-20", isin="FRB2099", kind="frb")], ("b",), terms=())
    with pytest.raises(ValueError, match="coupon_pct is 8.95 in the trades but '9.0' in its corporate terms"):
        run([trade("H1", "b", "buy", "2026-11-20", coupon_pct=8.95)], ("b",))
    with pytest.raises(ValueError, match="is in the corporate terms, but its kind is given as 'gsec'"):
        run([trade("H1", "b", "buy", "2026-11-20", kind="gsec")], ("b",))
    marks = pd.DataFrame([{"date": d, "isin": FRB_ISIN, "clean_price": 100.25} for d in DAYS])
    with pytest.raises(ValueError, match="is not known at .* and cannot be projected"):
        run([trade("F1", "b", "buy", "2026-11-20", isin=FRB_ISIN)], ("b",), marks=marks, terms=(),
            frb_coupons=None)


def test_a_gsec_book_is_unchanged_by_the_corporate_and_frb_inputs():
    from test_returns import GS2036, TERMS, marks_at
    gdays = CAL.business_days(date(2026, 11, 13), date(2026, 12, 4))
    gmarks = marks_at(GS2036, "GS2036", 7.3, gdays)
    trades = [{"trade_id": "G1", "book": "g", **TERMS, "side": "buy", "face": 1e9, "trade_date": "2026-11-13"}]
    flows = pd.DataFrame([{"date": "2026-11-13", "book": "g", "amount": 2e9}])
    st = settings(costs=co.read_cost_table())
    plain = rt.run_book(trades, flows=flows, market=rt.MarketData(marks=gmarks), settings=st, end=gdays[-1])
    full = rt.run_book(trades, flows=flows, market=rt.MarketData(
        marks=pd.concat([gmarks, ncd_marks()]), corporate_terms=pd.DataFrame([NCD_TERMS]), tbills=frb_tbills()),
        settings=st, end=gdays[-1])
    for name in rt.RESULT_FILES:
        a, b = getattr(plain, name), getattr(full, name)
        assert a.to_csv(index=False) == b.to_csv(index=False), name
    assert plain.notes == full.notes
    assert replace(st).attribution.reference_map["corporate"] == "gsec"


# ---------------------------------------------------------------- fixes of the review


def test_an_frbs_engine_dv01_is_its_rate_risk():
    """The engine put FRB 2031's yield DV01 (its spread DV01, Rs 41,746 a bp on Rs 10 crore) into positions.dv01, the
    5-10y bucket and the hedge check; its rate risk is the index DV01, near Rs 350, at its next reset."""
    from cr_strat.floating import goi_frb_bond, goi_frb_rates, index_dv01, load_frb_terms

    res = frb_run(frb_tbills())
    assert_identity(res)
    pos = res.positions.set_index("date").loc[date(2026, 11, 26)]
    terms = load_frb_terms().set_index("isin").loc[FRB_ISIN].to_dict() | {"isin": FRB_ISIN}
    bond = goi_frb_bond(terms, goi_frb_rates(terms, frb_tbills(), as_of="2026-11-26T20:00"))
    s = pos["settlement_date"]
    y = bond.yield_from_price(100.25, s)
    assert pos["dv01"] == pytest.approx(1e6 * index_dv01(bond, s, 0.0, index_pct=y), rel=1e-9)
    assert 100 < pos["dv01"] < 0.02 * 1e6 * bond.dv01(y, s)  # the yield DV01 is 100 times larger
    assert pos["bucket"] == "0-1y"  # the next reset, 7 Dec 2026
    nav = res.nav.set_index("date").loc[date(2026, 11, 26)]
    assert nav["dv01"] == pytest.approx(pos["dv01"]) and nav["dv01_0-1y"] == pytest.approx(pos["dv01"])


def _frb2031_run(coupons, end=date(2026, 12, 11), st=None):
    days = CAL.business_days(date(2026, 11, 20), end)
    marks = pd.DataFrame([{"date": d, "isin": FRB_ISIN, "clean_price": 100.0} for d in days])
    return run([trade("F1", "frb", "buy", "2026-11-20", isin=FRB_ISIN, kind="frb")], ("frb",), marks=marks,
               terms=(), frb_coupons=coupons, end=end, st=st)


def test_an_frb_coupon_projected_to_its_end_is_paid():
    """The shipped coupons lack FRB 2031's coupon from 7 Jun 2026: it is projected flat at 6.52% (the Dec 2025
    coupon) and, when its period ends on 7 Dec, paid at that rate (the build raised 'no rate for the coupon period
    from 2026-06-07'). The next coupon, typed in as announced on 4 Dec, takes over."""
    from cr_strat.floating import load_frb_coupons

    typed = pd.concat([load_frb_coupons(), pd.DataFrame([{
        "isin": FRB_ISIN, "period_start": "2026-12-07", "period_end": "2027-06-07", "rate_pct": "6.80",
        "announced": "2026-12-04T17:00", "source": "synthetic", "status": "P"}])], ignore_index=True)
    res = _frb2031_run(typed)
    assert_identity(res)
    cash = res.nav.set_index("date")["cash"]
    assert cash.loc[date(2026, 12, 7)] - cash.loc[date(2026, 12, 4)] == pytest.approx(1e6 * 6.52 / 2, rel=1e-12)
    notes = res.positions.set_index("date")["notes"]
    assert notes.loc[date(2026, 12, 4)] == "coupon 6.5200% projected"
    assert notes.loc[date(2026, 12, 8)] == "coupon 6.8000% announced"
    # without the typed coupon the new period's only base is a year old: refused, naming why
    with pytest.raises(ValueError, match="period from 2026-12-07 is not known at 2026-12-07 20:00.*too old"):
        _frb2031_run(load_frb_coupons())
    stale = _frb2031_run(load_frb_coupons(), st=settings(costs=NOCOST, frb_allow_stale=True))
    assert_identity(stale)
    assert stale.positions.set_index("date")["notes"].loc[date(2026, 12, 8)] == "coupon 6.5200% projected"


def _called(notice: str, record_date: str = ""):
    isin = "INECCC000001"
    terms = {"isin": isin, "kind": "corporate", "coupon_pct": "8.0", "frequency": "1", "issue_date": "2024-06-15",
             "maturity_date": "2031-06-15"}
    options = pd.DataFrame([{"isin": isin, "kind": "call", "date": "2026-12-10", "price_per100": "101",
                             "exercised": "yes", "exercised_public_from": notice, "record_date": record_date}])
    bond = CorporateBond(8.0, date(2031, 6, 15), date(2024, 6, 15))
    days = CAL.business_days(date(2026, 11, 20), date(2026, 12, 14))
    marks = pd.DataFrame([{"date": d, "isin": isin, "clean_price": bond.clean_price(8.2, CAL.settlement_date(d, 1))}
                          for d in days if d <= date(2026, 11, 24)])
    return run([trade("C1", "b", "buy", "2026-11-20", isin=isin)], ("b",), marks=marks, terms=(terms,),
               end=days[-1], corporate_options=options)


def test_an_exercise_notice_after_its_record_date_is_refused_up_front():
    """The 15-day rule puts the 10 Dec 2026 call's record date on 25 Nov: a notice public on 30 Nov stopped the
    engine on that day with a message about the previous close's mark. It is refused before the run, naming the
    remedy; with the record date the notice gives, the redemption is entitled at that date's close."""
    with pytest.raises(ValueError, match="after the redemption's record date 2026-11-25 .*record_date in "
                                         "corporate_options"):
        _called("2026-11-30T17:00")
    res = _called("2026-11-30T17:00", record_date="2026-12-01")
    assert_identity(res)
    nav = res.nav.set_index("date")
    coupon = 8.0 * 178 / 365  # 15 Jun to 10 Dec 2026: interest to the call date
    assert nav.loc[date(2026, 12, 2), "receivables"] == pytest.approx(1e6 * (101.0 + coupon), rel=1e-9)
    assert nav.loc[date(2026, 12, 10), "receivables"] == 0.0
    assert res.positions["date"].max() == date(2026, 12, 1)


def test_the_half_spread_follows_the_rating_public_before_the_trade():
    """The cost bucket came from the terms' static rating_bucket, so a downgrade typed in later priced earlier
    trades: now the rating actions public before the trade day decide it."""
    st = settings(costs=co.read_cost_table())
    ratings = pd.DataFrame([
        {"isin": NCD_ISIN, "agency": "CRISIL", "rating": "CRISIL AAA/Stable", "public_from": "2025-01-10"},
        {"isin": NCD_ISIN, "agency": "CRISIL", "rating": "CRISIL A/Negative", "public_from": "2026-11-25T18:00"}])
    trades = [trade("B1", "b", "buy", "2026-11-20"), trade("S1", "b", "sell", "2026-11-26", face=5e7)]
    res = run(trades, ("b",), st=st, terms=(NCD_TERMS | {"rating_bucket": "A"},), ratings=ratings)
    rules = res.fills.set_index("trade_id")["rules"].map(lambda r: r.split(";")[0])
    assert rules.to_dict() == {"B1": "HS20:U", "S1": "HS23:U"}  # AAA before the downgrade, A after it
    static = run(trades, ("b",), st=st, terms=(NCD_TERMS | {"rating_bucket": "A"},))
    assert static.fills.set_index("trade_id")["rules"].map(lambda r: r.split(";")[0]).tolist() == ["HS23:U"] * 2
    assert any("static rating_bucket" in n for n in static.notes)
