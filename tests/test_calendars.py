import dataclasses
import tomllib
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import cr_strat
from cr_strat.calendars import (
    DEFAULT_HOLIDAYS_CSV,
    BusinessCalendar,
    CalendarCoverageError,
    calendar_for,
    calendar_from_holidays,
    coverage_path,
    load_calendar,
    load_coverage,
    load_holidays,
    mumbai_settlement_calendar,
    settlement_date,
)
from cr_strat.conventions import GSEC, TBILL

D = date


@pytest.fixture(scope="module")
def cal():
    return mumbai_settlement_calendar()


def test_shipped_list(cal):
    holidays = load_holidays(DEFAULT_HOLIDAYS_CSV)
    assert cal.years == set(range(2015, 2027)) and len(cal.holidays) == len(holidays) == 239
    assert holidays["source"].str.startswith("https://").all()  # one source URL per row
    gazette_2026 = [d for d in holidays["date"] if d.year == 2026 and d != D(2026, 1, 15)]  # 15 Jan: ad hoc
    # NSE Clearing's 2026 debt-segment settlement holidays (circular 0399/2025) number 19 weekdays
    # and 4 weekend days, the same as the Maharashtra gazette list (count via a search summary).
    assert sum(d.weekday() < 5 for d in gazette_2026) == 19
    assert sum(d.weekday() >= 5 for d in gazette_2026) == 4
    unconfirmed = holidays.loc[~holidays["confirmed"], "date"].tolist()
    assert unconfirmed == [D(2015, 7, 1), D(2017, 2, 21)]  # no RBI, CCIL or auction evidence either way


def test_holidays_and_weekends(cal):
    assert not cal.is_business_day(D(2026, 4, 1))  # bank closing day, a holiday under handbook 4.10.3
    assert not cal.is_business_day(D(2026, 1, 15))  # ad hoc election holiday, kept by default
    assert not cal.is_business_day(D(2025, 9, 8)) and cal.is_business_day(D(2025, 9, 5))  # Id-E-Milad moved
    assert not cal.is_business_day(D(2026, 11, 14))  # Saturday
    assert cal.is_business_day(D(2026, 11, 16))
    assert cal.is_holiday(D(2026, 11, 8)) and not cal.is_business_day(D(2026, 11, 8))  # Diwali on a Sunday
    assert not cal.is_holiday(D(2026, 11, 7))  # a plain Saturday is not a listed holiday


@pytest.mark.parametrize("trade, settle", [
    (D(2026, 11, 13), D(2026, 11, 16)),  # 6.94% GS 2036 auction, Friday to Monday
    (D(2026, 4, 2), D(2026, 4, 6)),  # Good Friday 3 Apr 2026
    (D(2026, 4, 30), D(2026, 5, 4)),  # Maharashtra Din 1 May 2026
    (D(2025, 8, 14), D(2025, 8, 18)),  # Independence Day 15 Aug 2025
    (D(2026, 1, 23), D(2026, 1, 27)),  # Republic Day 26 Jan 2026
    (D(2025, 4, 11), D(2025, 4, 15)),  # Ambedkar Jayanti 14 Apr 2025
    (D(2026, 5, 27), D(2026, 5, 29)),  # T-bill auction over Bakri Id, 28 May 2026
    (D(2025, 5, 9), D(2025, 5, 13)),  # Buddha Pournima 12 May 2025
    (D(2026, 4, 1), D(2026, 4, 2)),  # traded on a settlement holiday (exchanges open)
])
def test_t_plus_1(cal, trade, settle):
    assert cal.settlement_date(trade, 1) == settle


def test_adjust_rules(cal):
    sat = D(2026, 1, 31)
    assert cal.adjust(sat, "following") == D(2026, 2, 2)
    assert cal.adjust(sat, "modified_following") == D(2026, 1, 30)  # stays in January
    assert cal.adjust(D(2026, 1, 26), "preceding") == D(2026, 1, 23)  # Republic Day, Monday
    assert cal.adjust(D(2026, 11, 1), "modified_preceding") == D(2026, 11, 2)
    assert cal.adjust(D(2026, 11, 24), "following") == D(2026, 11, 25)
    assert cal.adjust(sat, "unadjusted") == sat
    assert cal.adjust(D(2026, 11, 16), "preceding") == D(2026, 11, 16)
    with pytest.raises(ValueError, match="rule"):
        cal.adjust(sat, "nearest")


def test_add_business_days(cal):
    assert cal.add_business_days(D(2026, 11, 6), 5) == D(2026, 11, 16)  # skips Diwali on 10 Nov
    assert cal.add_business_days(D(2026, 11, 16), -1) == D(2026, 11, 13)
    assert cal.add_business_days(D(2026, 11, 10), 0) == D(2026, 11, 11)  # lag 0 on a holiday rolls on
    assert cal.settlement_date(D(2026, 11, 16), 0) == D(2026, 11, 16)
    assert cal.settlement_date(D(2025, 9, 4), 1) == D(2025, 9, 5)
    assert cal.business_days(D(2026, 11, 6), D(2026, 11, 13)) == [
        D(2026, 11, 6), D(2026, 11, 9), D(2026, 11, 11), D(2026, 11, 12), D(2026, 11, 13)]
    with pytest.raises(ValueError, match="lag"):
        cal.settlement_date(D(2026, 11, 16), -1)


def test_years_outside_the_list_raise(cal):
    with pytest.raises(CalendarCoverageError, match="2027"):
        cal.is_business_day(D(2027, 1, 4))
    with pytest.raises(CalendarCoverageError, match="lenient"):
        cal.add_business_days(D(2026, 12, 31), 1)  # 1 Jan 2027 is a Friday nobody has listed
    with pytest.raises(CalendarCoverageError):
        cal.add_business_days(D(2015, 1, 1), -1)  # 31 Dec 2014
    assert cal.settlement_date(D(2014, 12, 31), 1) == D(2015, 1, 1)  # only the dates examined must be covered
    assert isinstance(CalendarCoverageError("x"), ValueError)
    assert not cal.is_business_day(D(2027, 1, 2))  # weekends need no list
    assert not cal.covers(D(2027, 1, 4)) and cal.covers("2026-06-01")


def test_lenient_calendar(cal):
    loose = cal.lenient()
    assert not loose.strict and cal.strict
    assert loose.add_business_days(D(2026, 12, 31), 1) == D(2027, 1, 1)
    assert loose.adjust(D(2027, 1, 2)) == D(2027, 1, 4)
    assert loose.is_business_day(D(2026, 11, 24)) is False  # listed holidays still count
    assert load_calendar(strict=False).add_business_days(D(2026, 12, 31), 1) == D(2027, 1, 1)


def test_unconfirmed_holidays_can_be_dropped():
    cal = mumbai_settlement_calendar(include_unconfirmed=False)
    assert cal.is_business_day(D(2017, 2, 21)) and not mumbai_settlement_calendar().is_business_day(D(2017, 2, 21))
    assert not cal.is_business_day(D(2026, 1, 15))  # confirmed by CCIL notification CCIL/Legal/26/01
    assert cal.years == set(range(2015, 2027))
    with pytest.raises(CalendarCoverageError):
        mumbai_settlement_calendar(years=[2026]).is_business_day(D(2025, 6, 2))


def test_holiday_csv_validation(tmp_path):
    def write(text):
        path = tmp_path / "h.csv"
        path.write_text(text)
        return path

    good = write("Date,Name,Source\n2030-01-01,New Year,https://example.org/a\n")
    frame = load_holidays(good)
    assert frame.loc[0, "date"] == D(2030, 1, 1) and bool(frame.loc[0, "confirmed"])
    with pytest.raises(FileNotFoundError, match="no coverage file"):
        load_calendar(good)  # one row no longer makes 2030 covered
    assert load_calendar(good, years=[2030]).years == {2030}  # the caller declares it
    with pytest.raises(ValueError, match="missing columns"):
        load_holidays(write("date,name\n2030-01-01,x\n"))
    with pytest.raises(ValueError, match="row 2: date"):
        load_holidays(write("date,name,source\n01/01/2030,x,https://a\n"))
    with pytest.raises(ValueError, match="source is empty"):
        load_holidays(write("date,name,source\n2030-01-01,x,\n"))
    with pytest.raises(ValueError, match="listed twice"):
        load_holidays(write("date,name,source\n2030-01-01,x,https://a\n2030-01-01,y,https://b\n"))
    with pytest.raises(ValueError, match="confirmed"):
        load_holidays(write("date,name,source,confirmed\n2030-01-01,x,https://a,maybe\n"))
    with pytest.raises(FileNotFoundError):
        load_calendar(tmp_path / "missing.csv")


def test_calendar_constructor():
    cal = BusinessCalendar("x", {D(2030, 1, 1)}, years=[2030])
    assert cal.years == {2030} and not cal.is_business_day("2030-01-01")
    assert "1 holidays" in repr(cal) and hash(cal) == hash(BusinessCalendar("x", [D(2030, 1, 1)], years={2030}))
    with pytest.raises(ValueError, match="pass years="):
        BusinessCalendar("x", {D(2030, 1, 1)})  # coverage is never inferred from the holidays
    assert BusinessCalendar("empty").years == frozenset()
    with pytest.raises(ValueError, match="outside the covered years"):
        BusinessCalendar("x", {D(2030, 1, 1)}, years=[2031])
    friday_weekend = BusinessCalendar("gulf", years=[2030], weekend={4, 5})
    assert friday_weekend.is_business_day(D(2030, 1, 6)) and not friday_weekend.is_business_day(D(2030, 1, 4))


def test_shipped_coverage():
    coverage = load_coverage(coverage_path(DEFAULT_HOLIDAYS_CSV)).set_index("year")
    assert coverage.index.tolist() == list(range(2015, 2027)) and coverage["complete"].all()
    assert coverage.loc[[2025, 2026], "source"].str.startswith("https://www.fimmda.org/").all()  # the gazette lists
    # 2015-2024: the clearing houses' settlement-holiday lists (NSE, NSE Clearing, CCIL), which track the NI Act list
    assert coverage.loc[2015:2023, "source"].str.startswith("https://nsearchives.nseindia.com/").all()
    assert coverage.loc[2024, "source"].startswith("https://www.ccilindia.com/")


def test_coverage_is_declared_not_inferred(tmp_path):
    # One 2027 row (Republic Day is a fixed date, tempting to add early) must not cover 2027.
    shipped = load_holidays(DEFAULT_HOLIDAYS_CSV)
    shipped = shipped[[d.year in (2025, 2026) for d in shipped["date"]]]
    extra = pd.concat([shipped, pd.DataFrame([{"date": D(2027, 1, 26), "name": "Republic Day",
                                               "source": "https://example.org/2027", "confirmed": True}])],
                      ignore_index=True)
    with pytest.raises(TypeError):
        calendar_from_holidays(extra, name="m")  # years is required
    cal = calendar_from_holidays(extra, name="m", years=[2025, 2026])
    assert not cal.covers("2027-03-22")
    with pytest.raises(CalendarCoverageError, match="2027"):
        cal.is_business_day("2027-03-22")

    # The same in files: a year with rows must have a line in <name>_years.csv.
    csv = tmp_path / "h.csv"
    extra.to_csv(csv, index=False)
    years = coverage_path(csv)
    assert years == tmp_path / "h_years.csv"
    with pytest.raises(FileNotFoundError, match="no coverage file"):
        load_calendar(csv)
    declared = "year,complete,source\n2025,yes,https://a\n2026,yes,https://b\n"
    years.write_text(declared)
    with pytest.raises(ValueError, match=r"rows for \[2027\]"):
        load_calendar(csv)
    years.write_text(declared + "2027,no,https://example.org/2027\n")  # partial list, staged
    cal = load_calendar(csv)
    assert cal.years == {2025, 2026} and D(2027, 1, 26) not in cal.holidays
    with pytest.raises(CalendarCoverageError, match="2027"):
        cal.is_business_day("2027-03-22")
    with pytest.raises(ValueError, match=r"not marked complete"):
        load_calendar(csv, years=[2026, 2027])
    assert load_calendar(csv, years=[2026]).years == {2026}
    years.write_text(declared + "2027,yes,https://example.org/2027\n")
    cal = load_calendar(csv)
    assert cal.covers("2027-03-22") and not cal.is_business_day("2027-01-26")
    with pytest.raises(FileNotFoundError, match="coverage CSV not found"):
        load_calendar(csv, coverage=tmp_path / "missing.csv")


def test_coverage_csv_validation(tmp_path):
    csv = tmp_path / "h.csv"
    csv.write_text("date,name,source\n2030-01-01,New Year,https://a\n")
    years = coverage_path(csv)
    for text, message in [
        ("year,source\n2030,https://a\n", "missing columns"),
        ("year,complete,source\n30,yes,https://a\n", "four-digit year"),
        ("year,complete,source\n2030,maybe,https://a\n", "complete must be yes or no"),
        ("year,complete,source\n2030,,https://a\n", "complete must be yes or no"),
        ("year,complete,source\n2030,yes,\n", "source is empty"),
        ("year,complete,source\n2030,yes,https://a\n2030,no,https://b\n", "listed twice"),
        ("year,complete,source\n2030,yes,https://a\n2031,yes,https://b\n", r"marks \[2031\] complete"),
    ]:
        years.write_text(text)
        with pytest.raises(ValueError, match=message):
            load_calendar(csv)


def test_calendar_from_hand_built_frames():
    shipped = load_holidays(DEFAULT_HOLIDAYS_CSV)
    # Without a confirmed column every row counts as confirmed.
    no_flag = calendar_from_holidays(shipped.drop(columns=["confirmed"]), name="x", years=[2025, 2026],
                                     include_unconfirmed=False)
    assert not no_flag.is_business_day(D(2026, 1, 15))
    # Read straight from the CSV, confirmed is yes/no text.
    raw = pd.read_csv(DEFAULT_HOLIDAYS_CSV)
    text = calendar_from_holidays(raw, name="x", years=[2017, 2025, 2026], include_unconfirmed=False)
    assert text.is_business_day(D(2017, 2, 21)) and not text.is_business_day(D(2026, 1, 26))
    assert text.holidays == mumbai_settlement_calendar(include_unconfirmed=False, years=[2017, 2025, 2026]).holidays
    with pytest.raises(ValueError, match="'date' column"):
        calendar_from_holidays(shipped.rename(columns={"date": "day"}), name="x", years=[2026])
    with pytest.raises(ValueError, match="confirmed must be yes or no"):
        calendar_from_holidays(shipped.assign(confirmed="maybe"), name="x", years=[2026])
    with pytest.raises(ValueError, match="confirmed must be a boolean"):
        calendar_from_holidays(shipped.assign(confirmed=2), name="x", years=[2026])


def test_lags_must_be_whole_numbers(cal):
    for bad in (-0.5, 1.7, 1.5):  # -0.5 gave T+0 and 1.7 gave T+1
        with pytest.raises(ValueError, match="lag must be a whole number"):
            cal.settlement_date(D(2026, 11, 16), bad)
    with pytest.raises(ValueError, match="n must be a whole number"):
        cal.add_business_days(D(2026, 11, 16), 1.5)
    for bad in (True, "1", None):
        with pytest.raises(TypeError, match="lag"):
            cal.settlement_date(D(2026, 11, 16), bad)
    assert cal.settlement_date(D(2026, 11, 13), 1.0) == cal.settlement_date(D(2026, 11, 13), np.int64(1)) \
        == D(2026, 11, 16)


def test_settlement_date_from_conventions(cal):
    assert settlement_date(D(2026, 11, 13), GSEC) == D(2026, 11, 16)  # T+1 on the calendar GSEC names
    t2 = dataclasses.replace(GSEC, settlement_lag=2)  # e.g. a trade with an FPI
    assert settlement_date(D(2026, 11, 13), t2, calendar=cal) == D(2026, 11, 17)
    assert settlement_date(D(2026, 11, 13), TBILL, calendar=cal, lag=0) == D(2026, 11, 13)
    assert calendar_for("mumbai_settlement").holidays == cal.holidays
    with pytest.raises(ValueError, match="no calendar named 'nse'"):
        settlement_date(D(2026, 11, 13), dataclasses.replace(GSEC, calendar_name="nse"))
    with pytest.raises(TypeError, match="conventions"):
        settlement_date(D(2026, 11, 13), "gsec")
    with pytest.raises(TypeError, match="BusinessCalendar"):
        settlement_date(D(2026, 11, 13), GSEC, calendar="mumbai")


def test_holiday_list_ships_inside_the_package():
    # Located through the package, not the source checkout, so a wheel install finds it.
    package = Path(cr_strat.__file__).resolve().parent
    assert DEFAULT_HOLIDAYS_CSV.resolve().parent == package / "data"
    assert coverage_path(DEFAULT_HOLIDAYS_CSV).exists()
    pyproject = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    assert "data/*.csv" in pyproject["tool"]["setuptools"]["package-data"]["cr_strat"]


# 2015-2024: weekday holidays per year from the clearing houses' settlement-holiday lists (NSCCL, NSE Clearing,
# NSE currency segment, CCIL), with the ad hoc and moved days of each year.
@pytest.mark.parametrize("year, weekdays", [(2015, 21), (2016, 20), (2017, 18), (2018, 20), (2019, 19), (2020, 17),
                                            (2021, 20), (2022, 17), (2023, 18), (2024, 22)])
def test_added_years_weekday_holidays(year, weekdays):
    holidays = load_holidays(DEFAULT_HOLIDAYS_CSV)
    days = [d for d in holidays["date"] if d.year == year]
    assert len(days) == weekdays and all(d.weekday() < 5 for d in days)  # weekend holidays are not listed
    assert holidays.loc[[d.year == year for d in holidays["date"]], "source"].str.startswith("https://").all()


@pytest.mark.parametrize("trade, settle", [
    (D(2015, 4, 30), D(2015, 5, 5)),  # Maharashtra Day 1 May and Buddha Pournima 4 May 2015 (yields rule out 1 May)
    (D(2016, 4, 7), D(2016, 4, 11)),  # Gudi Padwa 8 Apr 2016
    (D(2017, 8, 24), D(2017, 8, 28)),  # Ganesh Chaturthi 25 Aug 2017
    (D(2018, 11, 8), D(2018, 11, 9)),  # 9 Nov 2018 (Bhai Duj) was dropped from the revised 2018 list
    (D(2018, 11, 22), D(2018, 11, 26)),  # Gurunanak Jayanti 23 Nov 2018
    (D(2019, 4, 26), D(2019, 4, 30)),  # Lok Sabha polling 29 Apr 2019: RBI dates 7.69% GS 2043 from 30 Apr
    (D(2019, 10, 18), D(2019, 10, 22)),  # Assembly polling 21 Oct 2019
    (D(2020, 4, 9), D(2020, 4, 13)),  # Good Friday 10 Apr 2020
    (D(2021, 11, 18), D(2021, 11, 22)),  # Gurunanak Jayanti 19 Nov 2021
    (D(2022, 2, 4), D(2022, 2, 8)),  # 7 Feb 2022, declared on 6 Feb (CCIL rolled settlement to 8 Feb)
    (D(2023, 6, 27), D(2023, 6, 28)),  # Bakri Id moved from 28 to 29 Jun 2023: 28 Jun a business day
    (D(2023, 6, 28), D(2023, 6, 30)),
    # Id-E-Milad 2023: Maharashtra cancelled the 28 Sep holiday and declared 29 Sep, but RBI kept its markets open
    # and settling on 29 Sep (press release 2023-2024/1005); the 28 Sep auction's yields pin settlement to 29 Sep
    (D(2023, 9, 27), D(2023, 9, 28)),
    (D(2023, 9, 28), D(2023, 9, 29)),
    (D(2024, 1, 19), D(2024, 1, 23)),  # 22 Jan 2024, declared on 19 Jan
    (D(2024, 9, 13), D(2024, 9, 16)),  # Id-E-Milad moved from 16 to 18 Sep 2024
    (D(2024, 9, 17), D(2024, 9, 19)),
    (D(2024, 11, 19), D(2024, 11, 21)),  # Assembly polling 20 Nov 2024: SDL new issues dated 21 Nov
])
def test_t_plus_1_in_the_added_years(cal, trade, settle):
    assert cal.settlement_date(trade, 1) == settle


def test_unconfirmed_days_of_the_added_years():
    # Kept as holidays by default; nothing confirms or refutes their effect on RBI settlement.
    strict, loose = mumbai_settlement_calendar(), mumbai_settlement_calendar(include_unconfirmed=False)
    for day in (D(2015, 7, 1), D(2017, 2, 21)):
        assert not strict.is_business_day(day) and loose.is_business_day(day)
    assert strict.settlement_date(D(2015, 6, 30), 1) == D(2015, 7, 2)
