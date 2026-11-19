"""Tests of funding: rates from a synthetic money_market_daily table, haircut tables, carry (B9, B11 of the spec)."""

from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from cr_strat import funding as fu
from cr_strat import ois, rbi_store
from cr_strat.bonds import DiscountInstrument, FixedRateBond
from cr_strat.calendars import mumbai_settlement_calendar
from cr_strat.cli import main

CAL = mumbai_settlement_calendar()
GS2031 = FixedRateBond(6.36, date(2031, 2, 16))


def mmd(start: date, end: date, rate=5.57, call=5.60, *, saturdays=(), overrides=None, quarter_end=()):
    """A synthetic money_market_daily frame in rbi_store's columns: a row per calendar day, rates on dealing days,
    published the next business day at 9:00 (weekend rows at 12:00)."""
    rows, d = [], start
    overrides = overrides or {}
    while d <= end:
        business = CAL.is_business_day(d)
        dealing = business or d in saturdays
        nxt = CAL.lenient().add_business_days(d, 1)
        tri = overrides.get(d, rate) if dealing else None
        rows.append({"as_on_date": d, "business_day": business, "quarter_end_day": d in quarter_end,
                     "fy_end_day": False, "triparty_rate_pct": tri, "triparty_volume_cr": 5e5 if dealing else 0.0,
                     "market_repo_rate_pct": tri, "market_repo_volume_cr": 1e5 if dealing else 0.0,
                     "call_rate_pct": call if dealing else None, "call_volume_cr": 1e4 if dealing else 0.0,
                     "sdf_rate_pct": 5.25, "msf_rate_pct": 5.75, "repo_rate_pct": 5.50,
                     "public_from": datetime(nxt.year, nxt.month, nxt.day, 9 if business else 12)})
        d += timedelta(days=1)
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def november():
    return fu.funding_rates(mmd(date(2026, 11, 13), date(2026, 12, 31)), "triparty")


def test_carry_of_an_asset_swap_b9(november):
    s, e = date(2026, 11, 20), date(2026, 12, 20)
    fixings = {d: 5.60 for d in CAL.business_days(s, e - timedelta(days=1))}
    swap = ois.OisSwap(effective=s, months=48, fixed_rate_pct=6.134480, notional=1e9)
    c = fu.carry(GS2031, 1e9, 99.50, s, e, funding=november, haircut_pct=4.0, swap=swap, fixings=fixings,
                 pull_to_par=False)
    assert (c.days, c.days_30e) == (30, 30)
    assert c.income == pytest.approx(53_00_000.00, abs=0.01)
    assert c.cash_borrowed == pytest.approx(97_11_42_400.00, abs=0.01)
    assert c.repo == pytest.approx(44_45_969.73, abs=0.01)
    assert c.equity_cost == pytest.approx(1_85_248.74, abs=0.01)
    assert c.fixed == pytest.approx(50_42_038.36, abs=0.01)
    assert c.floating == pytest.approx(46_12_662.99, abs=0.01)  # compounded; simple would be 46,02,739.73
    assert c.income - c.repo + c.swap_carry == pytest.approx(4_24_654.90, abs=0.01)
    assert c.net == pytest.approx(2_39_406.16, abs=0.01)
    assert (c.income - c.repo + c.swap_carry) / 1e9 * 365 / 30 * 1e4 == pytest.approx(51.67, abs=0.005)
    assert c.net_bp_annual == pytest.approx(c.net / 1e9 * 365 / 30 * 1e4)


def test_funding_table_rules_b11():
    frame = mmd(date(2026, 11, 13), date(2026, 11, 23), saturdays=(date(2026, 11, 21),),
                overrides={date(2026, 11, 16): 5.10})
    table = fu.funding_rates(frame, "triparty")
    assert list(table.columns) == list(fu.FUNDING_COLUMNS)
    rows = table.set_index("date")
    assert not rows.loc[date(2026, 11, 14), "dealing_day"] and pd.isna(rows.loc[date(2026, 11, 14), "rate_pct"])
    assert rows.loc[date(2026, 11, 21), "dealing_day"]  # a Saturday with a rate and a volume
    assert rows.loc[date(2026, 11, 16), "below_sdf"] and rows.loc[date(2026, 11, 16), "rate_pct"] == 5.10  # kept
    assert "below the SDF" in rows.loc[date(2026, 11, 16), "notes"]
    assert rows.loc[date(2026, 11, 13), "public_from"] == pd.Timestamp("2026-11-16 09:00")
    c = fu.carry(GS2031, 1e9, 99.5, date(2026, 11, 13), date(2026, 11, 23), funding=table, haircut_pct=0.0,
                 pull_to_par=False)
    by_day = c.daily.set_index("date")["rate_from"]
    assert [by_day[date(2026, 11, d)] for d in (13, 14, 15)] == [date(2026, 11, 13)] * 3  # Friday covers 3 days
    assert by_day[date(2026, 11, 22)] == date(2026, 11, 21)  # the dealing Saturday covers its Sunday
    rates = [5.57] * 3 + [5.10] + [5.57] * 6
    assert c.repo == pytest.approx(c.cash_borrowed * sum(rates) / 36500, rel=1e-14)
    assert fu.fixings_from(table, calendar=CAL).keys().isdisjoint({date(2026, 11, 21)})  # MIBOR never on Saturday


def test_rows_from_an_rbi_store_read_typed(tmp_path):
    frame = mmd(date(2026, 11, 13), date(2026, 11, 17))
    (tmp_path / "derived").mkdir()
    text = frame.assign(public_from=frame["public_from"].map(lambda t: t.strftime("%Y-%m-%dT%H:%M:%S")))
    text.to_csv(tmp_path / "derived" / "money_market_daily.csv", index=False)
    typed = rbi_store.read_table(tmp_path, "money_market_daily")
    table = fu.funding_rates(typed, "triparty")
    assert table["dealing_day"].tolist() == [True, False, False, True, True]
    assert table["rate_pct"].iloc[0] == 5.57 and table["business_day"].tolist() == [True, False, False, True, True]
    assert table.equals(fu.funding_rates(frame, "triparty"))


def test_as_of_keeps_only_rates_public_by_then_and_nothing_after_a_gap():
    frame = mmd(date(2026, 11, 13), date(2026, 11, 20))
    table = fu.funding_rates(frame, "triparty", as_of=datetime(2026, 11, 17, 10, 0))
    assert list(table["date"]) == [date(2026, 11, d) for d in (13, 14, 15, 16)]  # 17 Nov's row is public 18 Nov
    early = frame.copy()
    early.loc[early["as_on_date"] == date(2026, 11, 14), "public_from"] = None  # time unknown: never used
    assert list(fu.funding_rates(early, "triparty", as_of=datetime(2026, 11, 30))["date"]) == [date(2026, 11, 13)]
    with pytest.raises(ValueError, match="ends 2026-11-16 .* before the horizon ends 2026-11-20"):
        fu.carry(GS2031, 1e9, 99.5, date(2026, 11, 13), date(2026, 11, 20), funding=table, haircut_pct=0.0)
    c = fu.carry(GS2031, 1e9, 99.5, date(2026, 11, 13), date(2026, 11, 20), funding=table, haircut_pct=0.0,
                 projection="last")
    assert c.projected_days == 3 and "held at the 2026-11-16 rate" in c.notes[0]
    assert c.daily["projected"].tolist() == [False] * 4 + [True] * 3


def test_carry_over_a_quarter_end_uses_its_rate():
    spike = date(2026, 9, 30)
    frame = mmd(date(2026, 9, 25), date(2026, 10, 9), rate=5.55, overrides={spike: 6.50}, quarter_end=(spike,))
    table = fu.funding_rates(frame, "triparty")
    s, e = date(2026, 9, 29), date(2026, 10, 5)
    c = fu.carry(GS2031, 1e9, 99.5, s, e, funding=table, haircut_pct=3.0, pull_to_par=False)
    # 29 Sep, the quarter-end 30 Sep at 6.50, then 1 Oct's rate over the holiday of 2 Oct and the weekend
    assert c.daily["rate_pct"].tolist() == [5.55, 6.50, 5.55, 5.55, 5.55, 5.55]
    assert c.repo == pytest.approx(c.cash_borrowed * (5.55 * 5 + 6.50) / 36500, rel=1e-14)
    assert c.quarter_end_days == (spike,) and any("quarter- or year-end" in n for n in c.notes)
    flat = fu.carry(GS2031, 1e9, 99.5, s, e, funding=fu.funding_rates(mmd(date(2026, 9, 25), date(2026, 10, 9),
                                                                       rate=5.55), "triparty"),
                    haircut_pct=3.0, pull_to_par=False)
    assert c.repo - flat.repo == pytest.approx(c.cash_borrowed * 0.95 / 36500, rel=1e-12)


def test_a_projection_over_a_quarter_end_is_flagged():
    table = fu.funding_rates(mmd(date(2026, 12, 21), date(2026, 12, 24)), "triparty")
    c = fu.carry(GS2031, 1e9, 99.5, date(2026, 12, 21), date(2027, 1, 4), funding=table, haircut_pct=0.0,
                 projection="last", calendar=CAL.lenient())
    assert date(2026, 12, 31) in c.quarter_end_days


def test_a_missing_row_inside_the_table_is_refused():
    frame = mmd(date(2026, 11, 13), date(2026, 11, 20))
    gap = frame[frame["as_on_date"] != date(2026, 11, 17)]
    with pytest.raises(ValueError, match="no row for 2026-11-17"):
        fu.carry(GS2031, 1e9, 99.5, date(2026, 11, 16), date(2026, 11, 19), funding=fu.funding_rates(gap),
                 haircut_pct=0.0)


def test_income_pull_to_par_compounding_and_equity(november):
    s, e = date(2026, 11, 20), date(2026, 12, 21)
    simple = fu.carry(GS2031, 1e9, 99.50, s, e, funding=november, haircut_pct=4.0)
    y = GS2031.yield_from_price(99.50, s)
    assert simple.pull_to_par == pytest.approx(1e7 * (GS2031.clean_price(y, e) - 99.50), rel=1e-12)
    compound = fu.carry(GS2031, 1e9, 99.50, s, e, funding=november, haircut_pct=4.0, repo_compounding="compound")
    assert compound.repo > simple.repo and compound.repo - simple.repo < 0.01 * simple.repo
    fixed = fu.carry(GS2031, 1e9, 99.50, s, e, funding=november, haircut_pct=4.0, equity_rate_pct=7.0)
    assert fixed.equity_cost == pytest.approx(fixed.equity * 7.0 * 31 / 36500, rel=1e-14)
    # a coupon inside the horizon: income is the coupon plus the change in accrued interest
    bond = FixedRateBond(7.0, date(2030, 12, 5))
    c = fu.carry(bond, 1e9, 100.0, s, date(2026, 12, 18), funding=november, haircut_pct=0.0, pull_to_par=False)
    assert c.income == pytest.approx(1e9 * 0.07 * 28 / 360, rel=1e-12)  # 30E/360 days 20 Nov to 18 Dec


def test_a_bill_carries_by_its_pull_to_par_alone(november):
    bill = DiscountInstrument(date(2027, 2, 18))
    c = fu.carry(bill, 1e9, 98.65, date(2026, 11, 20), date(2026, 12, 18), funding=november, haircut_pct=1.0)
    y = bill.yield_from_price(98.65, date(2026, 11, 20))
    assert c.income == 0 and c.pull_to_par == pytest.approx(1e7 * (bill.price(y, date(2026, 12, 18), rounded=False)
                                                                   - 98.65), rel=1e-12)


def test_carry_refusals(november):
    with pytest.raises(ValueError, match="not a business day"):
        fu.carry(GS2031, 1e9, 99.5, date(2026, 11, 21), date(2026, 11, 30), funding=november, haircut_pct=4.0)
    with pytest.raises(ValueError, match="on or after the bond's maturity"):
        fu.carry(FixedRateBond(7.0, date(2026, 12, 1)), 1e9, 100, date(2026, 11, 20), date(2026, 12, 1),
                 funding=november, haircut_pct=4.0)
    with pytest.raises(ValueError, match="needs fixings"):
        fu.carry(GS2031, 1e9, 99.5, date(2026, 11, 20), date(2026, 11, 30), funding=november, haircut_pct=4.0,
                 swap=ois.OisSwap(effective=date(2026, 11, 20), months=12, fixed_rate_pct=6.0, notional=1e9))
    fixings = {d: 5.6 for d in CAL.business_days(date(2026, 11, 20), date(2026, 11, 29))}
    with pytest.raises(ValueError, match="after the carry starts"):
        fu.carry(GS2031, 1e9, 99.5, date(2026, 11, 20), date(2026, 11, 30), funding=november, haircut_pct=4.0,
                 swap=ois.OisSwap(effective=date(2026, 11, 23), months=12, fixed_rate_pct=6.0, notional=1e9),
                 fixings=fixings)
    with pytest.raises(ValueError, match="before the horizon ends"):
        fu.carry(GS2031, 1e9, 99.5, date(2026, 11, 20), date(2026, 12, 30), funding=november, haircut_pct=4.0,
                 swap=ois.OisSwap(effective=date(2026, 11, 20), months=1, fixed_rate_pct=6.0, notional=1e9),
                 fixings={d: 5.6 for d in CAL.business_days(date(2026, 11, 20), date(2026, 12, 29))})
    with pytest.raises(ValueError, match="haircut_pct"):
        fu.carry(GS2031, 1e9, 99.5, date(2026, 11, 20), date(2026, 11, 30), funding=november, haircut_pct=100)
    with pytest.raises(ValueError, match="no dealing day on or before"):
        fu.carry(GS2031, 1e9, 99.5, date(2026, 11, 12), date(2026, 11, 30), funding=november, haircut_pct=4.0)
    with pytest.raises(ValueError, match="source must be one of"):
        fu.funding_rates(mmd(date(2026, 11, 13), date(2026, 11, 14)), "cblo")
    with pytest.raises(ValueError, match="appears twice"):
        frame = mmd(date(2026, 11, 13), date(2026, 11, 14))
        fu.funding_rates(pd.concat([frame, frame]))


HAIRCUTS = pd.DataFrame([
    {"effective_date": "2026-08-24", "isin": "", "kind": "tbill", "haircut_pct": 1, "source": "synthetic"},
    {"effective_date": "2026-08-24", "isin": "", "kind": "sdl", "haircut_pct": 10, "source": "synthetic"},
    {"effective_date": "2026-02-01", "isin": "", "kind": "sdl", "haircut_pct": 15, "source": "synthetic"},
    {"effective_date": "2026-08-24", "isin": "", "kind": "gsec", "min_years": 0, "max_years": 10,
     "liquidity": "liquid", "haircut_pct": 3, "source": "synthetic"},
    {"effective_date": "2026-08-24", "isin": "", "kind": "gsec", "min_years": 10, "max_years": 20,
     "liquidity": "liquid", "haircut_pct": 4, "source": "synthetic"},
    {"effective_date": "2026-08-24", "isin": "", "kind": "gsec", "haircut_pct": 6, "source": "synthetic"},
    {"effective_date": "2026-08-24", "isin": "IN0020990012", "kind": "gsec", "haircut_pct": 28, "source": "synthetic"},
])


def test_haircuts_latest_notification_then_most_specific_row():
    on = date(2026, 11, 19)
    assert fu.haircut(HAIRCUTS, kind="sdl", maturity=date(2036, 1, 1), on=on) == 10
    assert fu.haircut(HAIRCUTS, kind="sdl", maturity=date(2036, 1, 1), on=date(2026, 3, 1)) == 15
    assert fu.haircut(HAIRCUTS, kind="gsec", maturity=date(2031, 2, 16), on=on, liquidity="liquid") == 3
    assert fu.haircut(HAIRCUTS, kind="gsec", maturity=date(2040, 7, 7), on=on, liquidity="liquid") == 4
    assert fu.haircut(HAIRCUTS, kind="gsec", maturity=date(2031, 2, 16), on=on, liquidity="illiquid") == 6
    assert fu.haircut(HAIRCUTS, kind="gsec", maturity=date(2031, 2, 16), on=on, isin="IN0020990012") == 28
    assert fu.haircut(HAIRCUTS, kind="tbill", maturity=date(2027, 2, 18), on=on) == 1


def test_haircut_refusals():
    with pytest.raises(ValueError, match="no haircut for a cd"):
        fu.haircut(HAIRCUTS, kind="cd", maturity=date(2027, 2, 18), on=date(2026, 11, 19))
    with pytest.raises(ValueError, match="no haircut"):
        fu.haircut(HAIRCUTS, kind="sdl", maturity=date(2036, 1, 1), on=date(2026, 1, 1))  # before any notification
    clash = pd.concat([HAIRCUTS, pd.DataFrame([{"effective_date": "2026-08-24", "kind": "sdl", "max_years": 50,
                                                "haircut_pct": 12, "source": "synthetic"},
                                               {"effective_date": "2026-08-24", "kind": "sdl", "min_years": 0,
                                                "haircut_pct": 11, "source": "synthetic"}])])
    with pytest.raises(ValueError, match="several haircuts"):
        fu.haircut(clash, kind="sdl", maturity=date(2036, 1, 1), on=date(2026, 11, 19))
    for bad, message in (({"haircut_pct": 100}, "haircut_pct"), ({"source": ""}, "source is empty"),
                         ({"liquidity": "very"}, "liquidity"), ({"kind": "", "isin": ""}, "isin or a kind"),
                         ({"min_years": 5, "max_years": 3}, "min_years")):
        row = {"effective_date": "2026-08-24", "kind": "sdl", "haircut_pct": 10, "source": "x", **bad}
        with pytest.raises(ValueError, match=message):
            fu.read_haircuts(pd.DataFrame([row]))


def test_carry_table_and_determinism(november):
    bonds = pd.DataFrame([
        {"isin": "B", "kind": "gsec", "coupon_pct": 6.36, "maturity_date": "2031-02-16", "clean_price": "99.50",
         "liquidity": "liquid"},
        {"isin": "A", "kind": "sdl", "coupon_pct": 7.2, "maturity_date": "2029-11-20", "clean_price": "100.10"},
        {"isin": "C", "kind": "cd", "coupon_pct": "", "maturity_date": "2027-02-18", "clean_price": "98.6"},
    ])
    # the SDL is capped by CCIL's TREPS caps (30% of the liquid G-sec's haircut-adjusted value): the rest of its
    # cash is funded at market repo
    repo = fu.funding_rates(mmd(date(2026, 11, 13), date(2026, 12, 31), rate=5.80), "market_repo")
    table = fu.carry_table(bonds, date(2026, 11, 20), date(2026, 12, 18), funding=november, haircuts=HAIRCUTS,
                           excess_funding=repo)
    assert list(table.columns) == list(fu.CARRY_COLUMNS) and list(table["isin"]) == ["C", "A", "B"]
    rows = table.set_index("isin")
    assert rows.loc["A", "haircut_pct"] == 10 and rows.loc["B", "haircut_pct"] == 3
    assert "no haircut for C (a cd maturing 2027-02-18) on 2026-11-20" in rows.loc["C", "error"]
    one = fu.carry(GS2031, 1e9, 99.50, date(2026, 11, 20), date(2026, 12, 18), funding=november, haircut_pct=3)
    assert rows.loc["B", "net"] == pytest.approx(one.net, rel=1e-15) and rows.loc["B", "funding_share"] == 1
    assert rows.loc["A", "funding_share"] == pytest.approx(0.3 * one.cash_borrowed / rows.loc["A", "cash_borrowed"],
                                                           rel=1e-12)
    again = fu.carry_table(bonds.iloc[::-1], date(2026, 11, 20), date(2026, 12, 18), funding=november,
                           haircuts=HAIRCUTS, excess_funding=repo)
    assert again.equals(table)


def test_cli_repo_carry(tmp_path, capsys):
    store = tmp_path / "store"
    (store / "derived").mkdir(parents=True)
    frame = mmd(date(2026, 11, 13), date(2026, 11, 30))
    frame.assign(public_from=frame["public_from"].map(lambda t: t.strftime("%Y-%m-%dT%H:%M:%S"))).to_csv(
        store / "derived" / "money_market_daily.csv", index=False)
    pd.DataFrame([{"isin": "B", "kind": "gsec", "coupon_pct": 6.36, "maturity_date": "2031-02-16",
                   "clean_price": 99.5, "haircut_pct": 4}]).to_csv(tmp_path / "bonds.csv", index=False)
    args = ["repo-carry", "--store", str(store), "--bonds", str(tmp_path / "bonds.csv"), "--start", "2026-11-20",
            "--end", "2026-11-30"]
    assert main([*args, "--out", str(tmp_path / "c.csv")]) == 0
    assert "1 of 1 bonds carried" in capsys.readouterr().out
    out = pd.read_csv(tmp_path / "c.csv")
    direct = fu.carry(GS2031, 1e9, 99.5, date(2026, 11, 20), date(2026, 11, 30),
                      funding=fu.funding_rates(frame), haircut_pct=4)
    assert out["net"].iloc[0] == pytest.approx(direct.net, rel=1e-12)
    assert main([*args, "--as-of", "2026-11-23T10:00", "--out", str(tmp_path / "late.csv")]) == 1
    assert "before the horizon ends" in capsys.readouterr().out
    assert main([*args, "--as-of", "2026-11-23T10:00", "--project", "--out", str(tmp_path / "proj.csv")]) == 0
    # Friday 20 Nov is public at 9:00 on 23 Nov, its weekend rows only at 12:00: 21 to 29 Nov are projected
    assert pd.read_csv(tmp_path / "proj.csv")["projected_days"].iloc[0] == 9
    assert main([*args, "--as-of", "23-11-2026", "--out", str(tmp_path / "bad.csv")]) == 2


# ---------------------------------------------------------------- the swap's floating leg period by period


LCAL = CAL.lenient()


def _flat_funding(start, end, rate=5.57):
    rows, d = [], start
    while d <= end:
        b = LCAL.is_business_day(d)
        rows.append({"as_on_date": d, "triparty_rate_pct": rate if b else None, "triparty_volume_cr": 5e5 if b else 0.0,
                     "sdf_rate_pct": 5.25, "msf_rate_pct": 5.75,
                     "public_from": datetime(d.year, d.month, d.day, 9) + timedelta(days=1)})
        d += timedelta(days=1)
    return fu.funding_rates(pd.DataFrame(rows), "triparty")


def test_a_running_swap_accrues_floating_interest_from_each_period_start():
    funding = _flat_funding(date(2026, 11, 1), date(2027, 12, 31))
    swap = ois.OisSwap(effective=date(2026, 11, 20), months=48, fixed_rate_pct=6.134480, notional=1e9)
    fix = {x: 5.60 for x in LCAL.business_days(date(2026, 11, 20), date(2027, 12, 31))}

    def a(x, y):
        return ois.compound_overnight(fix, x, y, calendar=LCAL).factor

    # one month inside the second period (20 May 2027 is its start, 20 Nov 2027 its end): the notional has
    # compounded since 20 Nov 2026's period ended... no: since the period began on 20 May? 20 Apr is in the first one
    month = fu.carry(GS2031, 1e9, 99.5, date(2027, 4, 20), date(2027, 5, 20), funding=funding, haircut_pct=4.0,
                     swap=swap, fixings=fix, pull_to_par=False, calendar=LCAL)
    first = ois.ois_schedule(swap.effective, swap.months, calendar=LCAL)[0]
    assert first.end == date(2027, 5, 20)
    expected = 1e9 * (a(first.start, date(2027, 5, 20)) - a(first.start, date(2027, 4, 20)))
    assert month.floating == pytest.approx(expected, abs=1e-6)
    assert month.floating == pytest.approx(47_20_804.80, abs=0.01)  # the review's independent figure
    # a year across the 20 May 2027 payment: each period's interest compounds from its own start, never across
    year = fu.carry(GS2031, 1e9, 99.5, date(2026, 11, 20), date(2027, 11, 22), funding=funding, haircut_pct=4.0,
                    swap=swap, fixings=fix, pull_to_par=False, calendar=LCAL)
    second_end = date(2027, 11, 22)
    expected = 1e9 * (a(first.start, first.end) - 1) + 1e9 * (a(first.end, second_end) - 1)
    assert year.floating == pytest.approx(expected, abs=1e-6)
    assert year.floating == pytest.approx(5_70_98_719.77, abs=0.01)
    assert year.fixed == pytest.approx(1e9 * 6.134480 * 367 / 36500, rel=1e-14)
    assert year.calendar_projected  # 2027 is not in the holiday list


def test_a_running_swap_needs_the_fixings_of_its_period_before_the_carry_starts():
    funding = _flat_funding(date(2026, 11, 1), date(2027, 1, 31))
    swap = ois.OisSwap(effective=date(2026, 11, 20), months=12, fixed_rate_pct=5.8, notional=1e9)
    fix = {x: 5.60 for x in CAL.business_days(date(2026, 12, 1), date(2026, 12, 30))}  # none before 1 Dec
    with pytest.raises(ValueError, match="no fixing for 6 business day"):  # 20 to 30 Nov but the 24 Nov holiday
        fu.carry(GS2031, 1e9, 99.5, date(2026, 12, 1), date(2026, 12, 15), funding=funding, haircut_pct=4.0,
                 swap=swap, fixings=fix)


# ---------------------------------------------------------------- as of a time: fixings and revised rows


def test_as_of_uses_only_fixings_published_by_then():
    table = fu.funding_rates(mmd(date(2026, 11, 13), date(2026, 12, 4)), "triparty")
    s, e = date(2026, 11, 20), date(2026, 12, 4)
    swap = ois.OisSwap(effective=s, months=48, fixed_rate_pct=6.134480, notional=1e9)
    days = CAL.business_days(s, e - timedelta(days=1))
    results = []
    for late in (5.60, 9.00):
        fix = {d: (late if d >= date(2026, 11, 25) else 5.60) for d in days}
        results.append(fu.carry(GS2031, 1e9, 99.5, s, e, funding=table, haircut_pct=4.0, swap=swap, fixings=fix,
                                as_of=datetime(2026, 11, 25, 10, 0), projection="last"))
    # 25 Nov's MIBOR is published by 12:45 that day: at 10:00 the last fixing known is 23 Nov's (24 Nov is a
    # holiday), held from 25 Nov
    assert results[0].floating == results[1].floating and results[0].net == results[1].net
    assert results[0].projected_fixings == 7 and any("held at the 2026-11-23 fixing" in n for n in results[0].notes)
    # the held days carry exactly the last fixing known: 5.60% compounded by hand over the horizon's business days
    growth = 1.0
    for d, nxt in zip(days, [*days[1:], e]):
        growth *= 1 + 5.60 * (nxt - d).days / 36500
    assert results[0].floating == pytest.approx(1e9 * (growth - 1), abs=1e-6)
    assert results[0].projected_days > 0  # the funding table is cut at as_of too
    with pytest.raises(ValueError, match="pass projection='last'"):
        fu.carry(GS2031, 1e9, 99.5, s, e, funding=table, haircut_pct=4.0, swap=swap,
                 fixings={d: 5.60 for d in days}, as_of=datetime(2026, 11, 25, 10, 0))
    # fixings that stop before the horizon ends are refused without a projection
    with pytest.raises(ValueError, match="no fixing given for 7 business day"):
        fu.carry(GS2031, 1e9, 99.5, s, e, funding=table, haircut_pct=4.0, swap=swap,
                 fixings={d: 5.60 for d in days if d < date(2026, 11, 25)})
    # a projection from a curve's forwards instead of the last fixing, and publication times given explicitly
    forwards = {d: 6.0 for d in days}
    curve = fu.carry(GS2031, 1e9, 99.5, s, e, funding=table, haircut_pct=4.0, swap=swap,
                     fixings={d: 5.60 for d in days}, projected_fixings=forwards, as_of=datetime(2026, 11, 25, 13, 0),
                     projection="last")
    assert curve.projected_fixings == 6  # 25 Nov is public at 13:00
    late_times = {d: datetime(d.year, d.month, d.day, 9) + timedelta(days=1) for d in days}  # WACR's next morning
    wacr = fu.carry(GS2031, 1e9, 99.5, s, e, funding=table, haircut_pct=4.0, swap=swap,
                    fixings={d: 5.60 for d in days}, projected_fixings=forwards, as_of=datetime(2026, 11, 25, 13, 0),
                    fixing_times=late_times, projection="last")
    assert wacr.projected_fixings == 7  # 23 Nov's rate public on 24 Nov at 9:00, 25 Nov's only on 26 Nov


def test_a_row_revised_after_as_of_is_not_yet_public():
    frame = pd.DataFrame([
        {"as_on_date": date(2026, 11, 13), "triparty_rate_pct": 5.65, "triparty_volume_cr": 5e5,
         "public_from": "2026-11-16T09:00:00", "revised": ""},
        {"as_on_date": date(2026, 11, 16), "triparty_rate_pct": 6.40, "triparty_volume_cr": 5e5,
         "public_from": "2026-11-17T09:00:00", "revised": "2026-11-20T15:00:00"},
    ])
    before = fu.funding_rates(frame, "triparty", as_of=datetime(2026, 11, 18, 10, 0))
    assert list(before["date"]) == [date(2026, 11, 13)]  # 16 Nov's 6.40% is its correction of 20 Nov
    after = fu.funding_rates(frame, "triparty", as_of=datetime(2026, 11, 20, 16, 0))
    assert list(after["rate_pct"]) == [5.65, 6.40] and after["revised"].iloc[1] == pd.Timestamp("2026-11-20 15:00")
    full = fu.funding_rates(frame, "triparty")  # realised funding uses the latest values
    assert len(full) == 2
    # carry(as_of=) cuts an uncut table the same way
    c = fu.carry(GS2031, 1e9, 99.5, date(2026, 11, 13), date(2026, 11, 17), funding=full, haircut_pct=0.0,
                 as_of=datetime(2026, 11, 18, 10, 0), projection="last")
    assert c.daily["rate_pct"].tolist() == [5.65] * 4 and c.projected_days == 3  # the frame has no weekend rows


# ---------------------------------------------------------------- CCIL's TREPS caps


def test_treps_shares_follow_the_concentration_rule():
    on = date(2026, 11, 20)
    shares, notes = fu.treps_shares([("gsec", "liquid", 150.0), ("gsec", "semi_liquid", 50.0),
                                     ("gsec", "illiquid", 40.0), ("sdl", None, 90.0), ("tbill", None, 10.0)], on)
    # base 200: illiquid G-secs lent in full (40 <= 100), SDLs 60 of 90 (30% of the base, within 50% - 40)
    assert shares == pytest.approx([1.0, 1.0, 1.0, 60 / 90, 1.0]) and "RMD/SS/26/13" in notes[0]
    shares, _ = fu.treps_shares([("gsec", "liquid", 200.0), ("gsec", None, 150.0), ("sdl", None, 90.0)], on)
    assert shares == pytest.approx([1.0, 100 / 150, 0.0])  # the illiquid ones are served first: none left for SDLs
    shares, _ = fu.treps_shares([("sdl", None, 90.0)], on)
    assert shares == [0.0]  # an SDL-only book has no TREPS borrowing limit
    # before 2 Jun 2026 the earlier rule as CCIL's FAQ summarises it (UNVERIFIED): illiquid G-secs and SDLs together
    # 20% of the base, illiquid first, no separate SDL cap
    before = date(2026, 5, 29)
    shares, notes = fu.treps_shares([("gsec", "liquid", 150.0), ("gsec", "semi_liquid", 50.0),
                                     ("gsec", "illiquid", 40.0), ("sdl", None, 90.0)], before)
    assert shares == pytest.approx([1.0, 1.0, 1.0, 0.0]) and "RMD/SS/21/21" in notes[0] and "UNVERIFIED" in notes[0]
    shares, _ = fu.treps_shares([("gsec", "liquid", 200.0), ("sdl", None, 90.0)], before)
    assert shares == pytest.approx([1.0, 40 / 90])  # 20% of 200, all of it to the SDLs
    shares, _ = fu.treps_shares([("sdl", None, 90.0)], before)
    assert shares == [0.0]  # nothing on a stand-alone basis: an SDL-only book had no TREPS limit before June either
    # before the earlier notice no rule is known: nothing is lent against SDLs and illiquid G-secs (conservative)
    shares, notes = fu.treps_shares([("gsec", "liquid", 200.0), ("sdl", None, 90.0)], date(2021, 3, 1))
    assert shares == [1.0, 0.0] and "no rule known before 2021-04-16" in notes[0]
    with pytest.raises(ValueError, match="not TREPS collateral"):
        fu.treps_shares([("cd", None, 10.0)], on)


def test_an_sdl_only_book_on_treps_is_funded_at_market_repo(november):
    sdl = pd.DataFrame([{"isin": "HR2030", "kind": "sdl", "coupon_pct": 7.61, "maturity_date": "2030-11-11",
                         "clean_price": 99.999224, "haircut_pct": 10}])
    s, e = date(2026, 11, 20), date(2026, 12, 18)
    refused = fu.carry_table(sdl, s, e, funding=november, haircuts=None)
    assert "lend 0.0% of its cash: give excess_funding" in refused["error"].iloc[0]
    repo = fu.funding_rates(mmd(date(2026, 11, 13), date(2026, 12, 31), rate=5.80), "market_repo")
    table = fu.carry_table(sdl, s, e, funding=november, haircuts=None, excess_funding=repo)
    row = table.iloc[0]
    assert row["error"] == "" and row["funding_share"] == 0.0 and row["repo"] == row["repo_other"]
    bond = FixedRateBond(7.61, date(2030, 11, 11), conventions=__import__("cr_strat").conventions.SDL)
    alone = fu.carry(bond, 1e9, 99.999224, s, e, funding=repo, haircut_pct=10)
    assert row["repo"] == pytest.approx(alone.repo, rel=1e-12) and "RMD/SS/26/13" in row["notes"]
    uncapped = fu.carry_table(sdl, s, e, funding=november, haircuts=None, treps_caps=False)
    assert uncapped["funding_share"].iloc[0] == 1.0 and uncapped["repo"].iloc[0] < row["repo"]
    cd = pd.DataFrame([{"isin": "CD1", "kind": "cd", "maturity_date": "2027-02-18", "clean_price": 98.6,
                        "haircut_pct": 1.5}])
    assert "not TREPS collateral" in fu.carry_table(cd, s, e, funding=november, haircuts=None)["error"].iloc[0]
    with pytest.raises(ValueError, match="ISIN HR2030 more than once"):
        fu.carry_table(pd.concat([sdl, sdl]), s, e, funding=november, haircuts=None, excess_funding=repo)


# ---------------------------------------------------------------- shorts and a funding spread


def test_a_short_pays_the_coupon_and_earns_the_special_repo_rate(november):
    s, e = date(2026, 11, 20), date(2026, 12, 18)
    flat = fu.funding_rates(mmd(date(2026, 11, 13), date(2026, 12, 31), rate=5.57), "triparty")
    long = fu.carry(GS2031, 1e9, 99.50, s, e, funding=flat, haircut_pct=3.0)
    short = fu.carry(GS2031, 1e9, 99.50, s, e, funding=flat, haircut_pct=3.0, side="short",
                     reverse_repo_rate_pct=5.57)
    assert short.side == "short" and short.income == -long.income and short.pull_to_par == -long.pull_to_par
    assert short.cash_borrowed == -long.cash_borrowed and short.repo == pytest.approx(-long.repo, rel=1e-12)
    assert long.net + short.net == pytest.approx(0.0, abs=1e-6)  # a long and a short at the same rates cancel
    special = fu.carry(GS2031, 1e9, 99.50, s, e, funding=flat, haircut_pct=3.0, side="short",
                       reverse_repo_rate_pct=4.0)
    assert special.net - short.net == pytest.approx(-short.cash_borrowed * (4.0 - 5.57) * 28 / 36500, rel=1e-9)
    with pytest.raises(ValueError, match="special repo rate"):
        fu.carry(GS2031, 1e9, 99.50, s, e, funding=flat, haircut_pct=3.0, side="short")
    bonds = pd.DataFrame([{"isin": "S", "kind": "gsec", "coupon_pct": 6.36, "maturity_date": "2031-02-16",
                           "clean_price": "99.50", "haircut_pct": 3, "side": "short", "special_repo_pct": 4.0}])
    fix = {d: 5.60 for d in CAL.business_days(s, e - timedelta(days=1))}
    row = fu.carry_table(bonds, s, e, funding=november, haircuts=None, swap_rate_pct=6.0, swap_months=12,
                         fixings=fix).iloc[0]
    assert row["side"] == "short" and row["error"] == "" and row["fixed"] > 0  # receives fixed against the short


def test_a_funding_spread_adds_to_every_repo_rate(november):
    s, e = date(2026, 11, 20), date(2026, 12, 18)
    base = fu.carry(GS2031, 1e9, 99.50, s, e, funding=november, haircut_pct=3.0)
    wide = fu.carry(GS2031, 1e9, 99.50, s, e, funding=november, haircut_pct=3.0, spread_bp=15)
    assert wide.repo - base.repo == pytest.approx(base.cash_borrowed * 0.15 * 28 / 36500, rel=1e-12)
    assert wide.equity_cost == base.equity_cost and any("+15 bp" in n for n in wide.notes)
    # a short earns the special repo rate given: the spread changes nothing, and the notes do not say it applied
    short = fu.carry(GS2031, 1e9, 99.50, s, e, funding=november, haircut_pct=3.0, side="short",
                     reverse_repo_rate_pct=5.0)
    short_wide = fu.carry(GS2031, 1e9, 99.50, s, e, funding=november, haircut_pct=3.0, side="short",
                          reverse_repo_rate_pct=5.0, spread_bp=15)
    assert short_wide.net == short.net and short_wide.repo == short.repo
    assert not any("(funding spread)" in n for n in short_wide.notes)
    assert any("funding spread of +15 bp not applied" in n for n in short_wide.notes)


# ---------------------------------------------------------------- the command line


def test_cli_repo_carry_as_of_fixings_calendar_and_caps(tmp_path, capsys):
    store = tmp_path / "store"
    (store / "derived").mkdir(parents=True)
    frame = mmd(date(2026, 11, 13), date(2026, 12, 4))
    frame.assign(public_from=frame["public_from"].map(lambda t: t.strftime("%Y-%m-%dT%H:%M:%S"))).to_csv(
        store / "derived" / "money_market_daily.csv", index=False)
    pd.DataFrame([{"isin": "B", "kind": "gsec", "coupon_pct": 6.36, "maturity_date": "2031-02-16",
                   "clean_price": 99.5, "haircut_pct": 4, "liquidity": "liquid"}]).to_csv(tmp_path / "b.csv",
                                                                                         index=False)
    days = CAL.business_days(date(2026, 11, 20), date(2026, 12, 3))
    for late in ("5.60", "9.00"):
        pd.DataFrame({"date": days, "rate_pct": [late if d >= date(2026, 11, 26) else "5.60" for d in days]}).to_csv(
            tmp_path / f"f{late}.csv", index=False)
    args = ["repo-carry", "--store", str(store), "--bonds", str(tmp_path / "b.csv"), "--start", "2026-11-20", "--end",
            "2026-12-04", "--as-of", "2026-11-25T10:00", "--project", "--pay-fixed", "6.13448", "--swap-tenor", "4Y"]
    nets = []
    for late in ("5.60", "9.00"):
        assert main([*args, "--fixings", str(tmp_path / f"f{late}.csv"), "--out", str(tmp_path / f"c{late}.csv")]) == 0
        nets.append(pd.read_csv(tmp_path / f"c{late}.csv")["net"].iloc[0])
    assert nets[0] == nets[1]  # fixings published after --as-of are not used
    assert "of MIBOR projected" in capsys.readouterr().out
    # a public_from column: a time given is used as given, a blank cell is FBIL's time of that day (12:45)
    rates = ["9.00" if d == date(2026, 11, 25) else "5.60" for d in days]
    pd.DataFrame({"date": days, "rate_pct": rates}).to_csv(tmp_path / "plain.csv", index=False)
    pd.DataFrame({"date": days, "rate_pct": rates,
                  "public_from": ["2026-11-26T09:00:00" if d == date(2026, 11, 25) else "" for d in days]}).to_csv(
        tmp_path / "timed.csv", index=False)
    at = [*args]
    at[at.index("--as-of") + 1] = "2026-11-25T14:00"
    rows = {}
    for name in ("plain", "timed"):
        assert main([*at, "--fixings", str(tmp_path / f"{name}.csv"), "--out", str(tmp_path / f"{name}_c.csv")]) == 0
        rows[name] = pd.read_csv(tmp_path / f"{name}_c.csv").iloc[0]

    def growth(from_25_nov):
        g = 1.0
        for d, nxt in zip(days, [*days[1:], date(2026, 12, 4)]):
            g *= 1 + (from_25_nov if d >= date(2026, 11, 25) else 5.60) * (nxt - d).days / 36500
        return g

    # at 14:00 on 25 Nov FBIL's fixing of that day (9.00%) is public and held from 26 Nov; given as public only on
    # 26 Nov it is not, so 23 Nov's 5.60% is held from 25 Nov; the blank cells of 20 and 23 Nov count from 12:45
    assert rows["plain"]["floating"] == pytest.approx(1e9 * (growth(9.00) - 1), abs=1e-4)
    assert rows["timed"]["floating"] == pytest.approx(1e9 * (growth(5.60) - 1), abs=1e-4)
    assert (rows["plain"]["projected_fixings"], rows["timed"]["projected_fixings"]) == (6, 7)
    capsys.readouterr()
    pd.DataFrame({"date": ["2026-11-20", "2026-11-20"], "rate_pct": [5.6, 5.9]}).to_csv(tmp_path / "dup.csv",
                                                                                         index=False)
    assert main([*args, "--fixings", str(tmp_path / "dup.csv"), "--out", str(tmp_path / "x.csv")]) == 2
    assert "two different fixings for 2026-11-20" in capsys.readouterr().err
    pd.DataFrame({"day": ["2026-11-20"], "rate_pct": [5.6]}).to_csv(tmp_path / "bad.csv", index=False)
    assert main([*args, "--fixings", str(tmp_path / "bad.csv"), "--out", str(tmp_path / "x.csv")]) == 2
    assert "lacks ['date']" in capsys.readouterr().err
    # a carry into 2027 needs the projected calendar, and says so
    plain = ["repo-carry", "--store", str(store), "--bonds", str(tmp_path / "b.csv"), "--project"]
    assert main([*plain, "--start", "2026-12-04", "--end", "2027-01-08", "--pay-fixed", "5.8", "--swap-tenor", "1Y",
                 "--fixings", str(tmp_path / "f5.60.csv"), "--out", str(tmp_path / "y.csv")]) == 1
    capsys.readouterr()
    assert main([*plain, "--start", "2026-12-04", "--end", "2027-01-08", "--project-calendar",
                 "--out", str(tmp_path / "z.csv")]) == 0
    assert "calendar_projected" in capsys.readouterr().out
    assert bool(pd.read_csv(tmp_path / "z.csv")["calendar_projected"].iloc[0])
    # an SDL on TREPS is funded at market repo beyond CCIL's caps
    pd.DataFrame([{"isin": "HR", "kind": "sdl", "coupon_pct": 7.61, "maturity_date": "2030-11-11",
                   "clean_price": 99.9992, "haircut_pct": 10}]).to_csv(tmp_path / "sdl.csv", index=False)
    assert main(["repo-carry", "--store", str(store), "--bonds", str(tmp_path / "sdl.csv"), "--start", "2026-11-20",
                 "--end", "2026-11-30", "--out", str(tmp_path / "s.csv")]) == 0
    assert "partly funded at market_repo rates" in capsys.readouterr().out
    assert pd.read_csv(tmp_path / "s.csv")["funding_share"].iloc[0] == 0
