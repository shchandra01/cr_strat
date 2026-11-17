import dataclasses
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from cr_strat import conventions, curves
from cr_strat.conventions import (
    CD,
    CMB,
    CONVENTIONS,
    CP,
    GSEC,
    SDL,
    STRIPS,
    TBILL,
    BondConventions,
    as_date,
    as_int,
    convert_yield,
    coupon_period_days,
    day_count_days,
    days_30_360_us,
    days_30e_360,
    days_actual,
    money_market_to_annual,
    round_half_up,
    round_to_tick,
    truncate,
    year_fraction,
)

D = date


# FIMMDA Handbook 2026, 6.6.1, broken-period table:
# https://www.fimmda.org/UploadPopupPageFiles/Fimmda_Handbook_2026_Final_Ver.pdf
# The handbook gives no years; 2025 stands in for a non-leap year and 2024 for a leap year.
# Row (b) prints "Total 14 Days", but its own lines (Sep 13 + Oct 29) add to 42: a typo, tested as 42.
@pytest.mark.parametrize("row, start, end, days", [
    ("a", D(2025, 7, 28), D(2025, 8, 14), 16),
    ("b", D(2025, 9, 18), D(2025, 10, 31), 42),
    ("c", D(2025, 2, 23), D(2025, 3, 5), 12),
    ("d", D(2025, 1, 6), D(2025, 2, 28), 52),
    ("e", D(2025, 2, 28), D(2025, 3, 17), 19),
    ("f1", D(2024, 2, 28), D(2024, 2, 28), 0),
    ("f2", D(2024, 2, 28), D(2024, 2, 29), 1),
    ("f3", D(2024, 2, 28), D(2024, 3, 1), 3),
    ("f3 non-leap", D(2025, 2, 28), D(2025, 3, 1), 3),
    ("g", D(2023, 12, 22), D(2024, 2, 29), 67),
    ("h", D(2024, 2, 29), D(2024, 4, 19), 50),
    ("i", D(2025, 8, 31), D(2025, 10, 22), 52),
    ("j", D(2025, 8, 31), D(2025, 8, 31), 0),
    ("k", D(2025, 8, 30), D(2025, 10, 22), 52),
])
def test_fimmda_broken_period_table(row, start, end, days):
    assert days_30e_360(start, end) == days
    assert day_count_days("30E/360", start, end) == days


def test_30_360_us_is_not_fimmdas_rule():
    # 30/360 US (SIA, Excel basis 0, which RBI's primer reportedly uses) differs from the
    # handbook on the 31st and at the end of February.
    assert days_30_360_us(D(2025, 9, 18), D(2025, 10, 31)) == 43  # handbook row (b): 42
    assert days_30_360_us(D(2025, 2, 28), D(2025, 3, 17)) == 17  # row (e): 19
    assert days_30_360_us(D(2024, 2, 29), D(2024, 4, 19)) == 49  # row (h): 50
    assert days_30_360_us(D(2025, 7, 28), D(2025, 8, 14)) == 16  # same as 30E/360 away from month ends
    assert days_30_360_us(D(2025, 2, 28), D(2026, 2, 28)) == 360  # both end-February


def test_30e_360_month_ends_and_leap_years():
    assert days_30e_360(D(2025, 1, 31), D(2025, 3, 1)) == 31  # 31 Jan counts as 30 Jan
    assert days_30e_360(D(2025, 1, 30), D(2025, 2, 28)) == 28  # 28 Feb is not moved to 30 Feb
    assert days_30e_360(D(2024, 1, 30), D(2024, 2, 29)) == 29
    assert days_30e_360(D(2025, 8, 30), D(2026, 2, 28)) == 178  # a "half year" across February
    assert days_30e_360(D(2026, 2, 28), D(2026, 8, 31)) == 182
    assert days_30e_360(D(2025, 3, 31), D(2025, 9, 30)) == 180
    assert days_30e_360(D(2026, 3, 1), D(2025, 3, 1)) == -360
    assert days_actual(D(2024, 2, 28), D(2024, 3, 1)) == 2
    assert days_actual(D(2025, 2, 28), D(2025, 3, 1)) == 1


def test_30e_360_is_additive():
    rng = np.random.default_rng(7)
    base = D(2024, 1, 1)
    for _ in range(300):
        a, b, c = sorted(base + timedelta(days=int(x)) for x in rng.integers(0, 2000, 3))
        assert days_30e_360(a, b) + days_30e_360(b, c) == days_30e_360(a, c)


def test_year_fractions():
    assert year_fraction("ACT/365F", "2024-01-01", "2025-01-01") == pytest.approx(366 / 365)
    assert year_fraction("30E/360", "2026-05-11", "2026-11-16") == pytest.approx(185 / 360)
    got = year_fraction("ACT/ACT ICMA", D(2026, 5, 11), D(2026, 8, 11),
                        period_start=D(2026, 5, 11), period_end=D(2026, 11, 11), frequency=2)
    assert got == pytest.approx(92 / (2 * 184))
    with pytest.raises(ValueError, match="ICMA"):
        year_fraction("ACT/ACT ICMA", D(2026, 5, 11), D(2026, 8, 11))
    with pytest.raises(ValueError, match="day_count"):
        day_count_days("ACT/360", D(2026, 1, 1), D(2026, 2, 1))
    assert coupon_period_days("30E/360", D(2026, 5, 11), D(2026, 11, 11), 2) == 180
    assert coupon_period_days("ACT/365F", D(2026, 5, 11), D(2026, 11, 11), 2) == 182.5
    assert coupon_period_days("ACT/ACT ICMA", D(2026, 5, 11), D(2026, 11, 11), 2) == 184


def test_yield_conversions_moved_and_reexported():
    assert curves.convert_yield is conventions.convert_yield
    assert curves.money_market_to_annual is conventions.money_market_to_annual
    assert curves.COMPOUNDING is conventions.COMPOUNDING
    assert convert_yield(7.3203, "semiannual", "annual") == pytest.approx(100 * ((1 + 0.073203 / 2) ** 2 - 1))
    assert money_market_to_annual(5.5, 91) == pytest.approx(100 * ((1 + 0.055 * 91 / 365) ** (365 / 91) - 1))


def test_rounding_helpers():
    assert round_half_up(98.64735, 4) == 98.6474  # "rounded off": half away from zero
    assert round_half_up(2.675, 2) == 2.68  # the decimal shown, not the binary value 2.67499..
    assert round_half_up(98.64734999, 4) == 98.6473
    assert truncate(98.64739, 4) == 98.6473
    assert np.isnan(round_half_up(float("nan"), 4))
    # Handbook 6.6.1 note: "Rs. 100.4350 or Rs. 101.2125 but not Rs. 102.3745 or Rs. 103.5018"
    assert round_to_tick(100.4350) == 100.4350 and round_to_tick(101.2125) == 101.2125
    assert round_to_tick(102.3745) == 102.375 and round_to_tick(103.5018) == 103.5025
    assert round_to_tick(100.4337) == 100.4325


def test_as_date():
    assert as_date("2026-11-16") == D(2026, 11, 16)
    assert as_date(pd.Timestamp("2026-11-16 10:30")) == D(2026, 11, 16)
    assert as_date(np.datetime64("2026-11-16")) == D(2026, 11, 16)
    for bad in (None, pd.NaT, float("nan")):
        with pytest.raises(ValueError):
            as_date(bad)
    with pytest.raises(TypeError):
        as_date(20261116)


def test_instrument_conventions():
    assert (GSEC.day_count, GSEC.frequency, GSEC.compounding, GSEC.settlement_lag) == ("30E/360", 2, "semiannual", 1)
    assert (GSEC.coupon_roll, GSEC.redemption_roll, GSEC.quote) == ("following", "preceding", "clean_price")
    assert dataclasses.replace(SDL, name=GSEC.name) == GSEC  # handbook 6.6 covers State Government securities
    assert STRIPS.final_period == "compound" and GSEC.final_period == "simple"
    for bill in (TBILL, CMB, CP, CD):
        assert (bill.year_days, bill.discount, bill.price_decimals, bill.maturity_roll) == (365, "rear", 4, "preceding")
    assert set(CONVENTIONS) == {"gsec", "sdl", "strips", "tbill", "cmb", "cp", "cd"}
    with pytest.raises(dataclasses.FrozenInstanceError):
        GSEC.day_count = "30/360 US"


def test_conventions_validate_choices():
    with pytest.raises(ValueError, match="day_count"):
        dataclasses.replace(GSEC, day_count="ACT/360")
    with pytest.raises(ValueError, match="final_period"):
        dataclasses.replace(GSEC, final_period="money_market")
    with pytest.raises(ValueError, match="coupon_roll"):
        dataclasses.replace(GSEC, coupon_roll="nearest")
    with pytest.raises(ValueError, match="time_basis 'periods'"):
        dataclasses.replace(GSEC, compounding="annual")  # periods need compounding at the coupon frequency
    annual = dataclasses.replace(GSEC, compounding="annual", time_basis="act/365")
    assert annual.compounding_frequency == 1
    with pytest.raises(ValueError, match="discount"):
        dataclasses.replace(TBILL, discount="middle")
    with pytest.raises(TypeError):
        BondConventions(name="incomplete")


def test_as_date_takes_iso_date_times_only():
    # pandas writes datetime cells as "2026-11-16 00:00:00"; the date part is taken.
    assert as_date("2026-11-16 00:00:00") == as_date("2026-11-16T10:30:00+05:30") == D(2026, 11, 16)
    # Not pd.Timestamp parsing: it reads FIMMDA's "06-02-2024" (6 Feb) as 2 June.
    for bad in ("06-02-2024", "16/11/2026", "2036-13-01", ""):
        with pytest.raises(ValueError):
            as_date(bad)


def test_as_int():
    assert as_int(2, "n") == as_int(2.0, "n") == as_int(np.int64(2), "n") == 2
    assert isinstance(as_int(np.float64(3.0), "n"), int)
    for bad in (1.5, -0.5, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="n must be a whole number"):
            as_int(bad, "n")
    for bad in (True, np.True_, "2", None):
        with pytest.raises(TypeError, match="n must be an integer"):
            as_int(bad, "n")
    with pytest.raises(ValueError, match="n must be >= 0, not -1"):
        as_int(-1, "n", minimum=0)


def test_conventions_need_whole_numbers():
    two = dataclasses.replace(GSEC, frequency=2.0)  # passed the choice check, then failed in pricing
    assert two == GSEC and isinstance(two.frequency, int)
    for name, bad in [("settlement_lag", 1.5), ("frequency", 2.5), ("ex_coupon_days", -1),
                      ("price_decimals", -3), ("shut_days_before_redemption", 0.5)]:
        with pytest.raises(ValueError, match=name):
            dataclasses.replace(GSEC, **{name: bad})
    with pytest.raises(TypeError, match="settlement_lag"):
        dataclasses.replace(GSEC, settlement_lag=True)
    for bad in (0, -0.0025, float("nan")):
        with pytest.raises(ValueError, match="price_tick"):
            dataclasses.replace(GSEC, price_tick=bad)
    for name, bad in [("price_decimals", -3), ("year_days", 0), ("settlement_lag", 0.5)]:
        with pytest.raises(ValueError, match=name):  # price_decimals=-3 priced every bill at 0.0
            dataclasses.replace(TBILL, **{name: bad})
    assert dataclasses.replace(TBILL, price_decimals=None).price_decimals is None


def test_convert_yield_works_for_any_compounding_basis(monkeypatch):
    # A basis added later (quarterly corporate coupons) converts on its own frequency, not as annual.
    monkeypatch.setattr(conventions, "COMPOUNDING", (*conventions.COMPOUNDING, "quarterly"))
    monkeypatch.setitem(conventions.COMPOUNDING_FREQUENCY, "quarterly", 4)
    assert convert_yield(8.0, "quarterly", "annual") == pytest.approx((1.02 ** 4 - 1) * 100)  # 8.243216
    assert convert_yield(8.0, "quarterly", "semiannual") == pytest.approx((1.02 ** 2 - 1) * 200)
    assert convert_yield(convert_yield(7.5, "annual", "quarterly"), "quarterly", "annual") == pytest.approx(7.5)
    # The two bases in use give what they gave before.
    assert convert_yield(8.0, "semiannual", "annual") == pytest.approx(8.16, abs=1e-12)
    assert convert_yield(8.0, "annual", "semiannual") == pytest.approx(2 * (np.sqrt(1.08) - 1) * 100, abs=1e-12)
