"""The macro and global sources: request builders, parsers, release timing and the polite fetch.

Synthetic answers only (``macro_fixtures``), a fake transport and a fake
sleep: no network, and the clock is an argument.
"""

import datetime as dt
import gzip
import io
import json
import ssl
from pathlib import Path

import macro_fixtures as fx
import pytest

from cr_strat import global_sources as gs
from cr_strat import macro_sources as ms
from cr_strat import mospi, storekit
from cr_strat import ustime as ut
from cr_strat.macro_base import Observation, SourceError, period_bounds
from cr_strat.storekit import FeedResponse

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 18, 10, 0, tzinfo=UTC)  # 15:30 in India
D = dt.date
T = dt.time


def ist(*args):
    return dt.datetime(*args)


# --- New York, London and India time --------------------------------------------------------------

@pytest.mark.parametrize("instant, hours", [
    (dt.datetime(2026, 3, 7, 12, tzinfo=UTC), -5), (dt.datetime(2026, 3, 9, 12, tzinfo=UTC), -4),
    (dt.datetime(2026, 10, 31, 12, tzinfo=UTC), -4), (dt.datetime(2026, 11, 2, 12, tzinfo=UTC), -5),
    (dt.datetime(2007, 3, 10, 12, tzinfo=UTC), -5), (dt.datetime(2007, 3, 12, 12, tzinfo=UTC), -4),
    (dt.datetime(2007, 11, 3, 12, tzinfo=UTC), -4), (dt.datetime(2007, 11, 5, 12, tzinfo=UTC), -5),
    (dt.datetime(2006, 4, 1, 12, tzinfo=UTC), -5), (dt.datetime(2006, 4, 3, 12, tzinfo=UTC), -4),
    (dt.datetime(2006, 10, 28, 12, tzinfo=UTC), -4), (dt.datetime(2006, 10, 30, 12, tzinfo=UTC), -5),
    (dt.datetime(2006, 3, 15, 12, tzinfo=UTC), -5),  # the 2007 rule's March start did not apply yet
    (dt.datetime(1990, 3, 31, 12, tzinfo=UTC), -5), (dt.datetime(1990, 4, 2, 12, tzinfo=UTC), -4),
    (dt.datetime(1990, 10, 27, 12, tzinfo=UTC), -4), (dt.datetime(1990, 10, 29, 12, tzinfo=UTC), -5),
    (dt.datetime(1987, 4, 4, 12, tzinfo=UTC), -5), (dt.datetime(1987, 4, 6, 12, tzinfo=UTC), -4),
    (dt.datetime(1986, 7, 1, 12, tzinfo=UTC), -5),  # before 1987: standard time assumed
])
def test_new_york_offsets_follow_the_us_rules_of_each_era(instant, hours):
    assert ut.utc_offset(ut.NEW_YORK, instant) == dt.timedelta(hours=hours)


def test_new_york_change_days_refuse_skipped_times_and_read_repeated_ones_as_standard_time():
    assert ut.ny_to_utc(D(2026, 3, 8), T(1, 59)) == dt.datetime(2026, 3, 8, 6, 59, tzinfo=UTC)
    assert ut.ny_to_utc(D(2026, 3, 8), T(3, 0)) == dt.datetime(2026, 3, 8, 7, 0, tzinfo=UTC)
    with pytest.raises(ut.NonexistentTimeError):
        ut.ny_to_utc(D(2026, 3, 8), T(2, 30))
    with pytest.raises(ut.NonexistentTimeError):
        ut.ny_to_utc(D(2006, 4, 2), T(2, 0))
    assert ut.ny_to_utc(D(2026, 11, 1), T(1, 30)) == dt.datetime(2026, 11, 1, 6, 30, tzinfo=UTC)  # the later one
    assert ut.ny_to_utc(D(2026, 11, 1), T(0, 59)) == dt.datetime(2026, 11, 1, 4, 59, tzinfo=UTC)
    assert ut.ny_to_utc(D(2006, 10, 29), T(1, 30)) == dt.datetime(2006, 10, 29, 6, 30, tzinfo=UTC)
    assert ut.ny_offset(dt.datetime(2026, 11, 1, 1, 30)) == dt.timedelta(hours=-5)
    assert ut.ny_to_utc(D(1986, 7, 1), T(12, 0)) == dt.datetime(1986, 7, 1, 17, 0, tzinfo=UTC)


def test_london_and_india_times():
    assert ut.to_utc(ut.LONDON, D(2026, 3, 29), T(0, 59)) == dt.datetime(2026, 3, 29, 0, 59, tzinfo=UTC)
    with pytest.raises(ut.NonexistentTimeError):
        ut.to_utc(ut.LONDON, D(2026, 3, 29), T(1, 30))
    assert ut.to_utc(ut.LONDON, D(2026, 3, 29), T(2, 0)) == dt.datetime(2026, 3, 29, 1, 0, tzinfo=UTC)
    assert ut.to_utc(ut.LONDON, D(2026, 10, 25), T(1, 30)) == dt.datetime(2026, 10, 25, 1, 30, tzinfo=UTC)
    assert ut.to_utc(ut.LONDON, D(1995, 7, 1), T(12, 0)) == dt.datetime(1995, 7, 1, 12, 0, tzinfo=UTC)
    assert ut.ny_to_ist(D(2026, 11, 17), T(10, 0)) == ist(2026, 11, 17, 20, 30)
    assert ut.ny_to_ist(D(2026, 7, 1), T(23, 59, 59)) == ist(2026, 7, 2, 9, 29, 59)
    assert ut.zone_to_ist(ut.KOLKATA, D(2026, 7, 1), T(16, 0)) == ist(2026, 7, 1, 16, 0)
    assert ut.ist_text(dt.datetime(2026, 11, 18, 18, 30, tzinfo=UTC)) == "2026-11-19T00:00:00"
    assert ut.to_local(ut.NEW_YORK, dt.datetime(2026, 11, 1, 6, 30, tzinfo=UTC)) == dt.datetime(2026, 11, 1, 1, 30)
    assert ut.nth_sunday(2026, 3, 2) == D(2026, 3, 8) and ut.nth_sunday(2026, 10, -1) == D(2026, 10, 25)
    with pytest.raises(ValueError):
        ut.to_utc("Europe/Paris", D(2026, 1, 1), T(0))
    with pytest.raises(ValueError):
        ut.localize(ut.NEW_YORK, dt.datetime(2026, 1, 1, tzinfo=UTC))


# --- MoSPI: requests ----------------------------------------------------------------------------------

def test_mospi_request_builders_give_exact_urls_in_a_fixed_parameter_order():
    r = mospi.cpi_2024_request(year=2026, month=10, division_code=0)
    assert r.url == ("https://api.mospi.gov.in/api/cpi/getCPIData?base_year=2024&series=Current&year=2026&"
                     "month_code=10&state_code=1&division_code=0&limit=100&page=1&Format=JSON")
    assert (r.source, r.kind, r.accept, r.host) == ("mospi", mospi.CPI_2024, "application/json", "api.mospi.gov.in")
    assert mospi.cpi_2024_request(series="Back", year=[2025, 2026]).url.count("year=2025,2026") == 1
    assert mospi.cpi_index_request(year=2025, month=12, sector_code=3).url == (
        "https://api.mospi.gov.in/api/cpi/getCPIIndex?base_year=2012&series=Current&year=2025&month_code=12&"
        "state_code=99&sector_code=3&limit=200&page=1&Format=JSON")
    assert "type=Use-based%20category" in mospi.iip_request(type="Use-based category").url
    assert mospi.nas_request(year=["2026-27", "2025-26"], indicator_code=1).url == (
        "https://api.mospi.gov.in/api/nas/getNASData?base_year=2022-23&series=Current&frequency_code=Quarterly&"
        "indicator_code=1&year=2026-27,2025-26&limit=200&page=1&Format=JSON")
    assert mospi.wpi_request(base_year="2011-12", major_group_code="1000000000", limit=10).url == (
        "https://api.mospi.gov.in/api/wpi/getWpiRecords?base_year=2011-12&major_group_code=1000000000&limit=10&"
        "page=1&Format=JSON")
    assert mospi.release_calendar_pointer_request().url == (
        "https://www.mospi.gov.in/api/documents/get-latest-release-calender")
    page3 = mospi.page_request(r, 3)
    assert page3.url == r.url.replace("page=1", "page=3") and page3.kind == r.kind


@pytest.mark.parametrize("call", [
    lambda: mospi.cpi_2024_request(limit=200), lambda: mospi.cpi_2024_request(limit=5),
    lambda: mospi.cpi_index_request(limit=201), lambda: mospi.cpi_2024_request(series="Old"),
    lambda: mospi.cpi_2024_request(month=13), lambda: mospi.iip_request(type="Sector"),
    lambda: mospi.nas_request(indicator_code=10), lambda: mospi.nas_request(frequency="Annually", quarter=1),
    lambda: mospi.wpi_request(major_group_code="10"), lambda: mospi.cpi_2024_request(page=0),
    lambda: mospi.cpi_index_request(base_year=2024), lambda: mospi.nas_request(year="2025-27"),
])
def test_mospi_request_builders_refuse_what_the_api_would_refuse_or_was_not_sampled(call):
    with pytest.raises(ValueError):
        call()


def test_more_pages_asks_the_rest_of_a_query_after_its_first_page():
    request = mospi.cpi_2024_request(year=2026, month=10)
    rows = fx.cpi24_month(2026, "October")
    first = mospi.parse_mospi(fx.mospi_answer(rows, per=5, total=12), "p1", kind=mospi.CPI_2024)
    assert [r.url[-20:] for r in mospi.more_pages(request, first)] == ["0&page=2&Format=JSON", "0&page=3&Format=JSON"]
    later = mospi.parse_mospi(fx.mospi_answer(rows, page=2, per=5, total=12), "p2", kind=mospi.CPI_2024)
    assert mospi.more_pages(request, later) == []


# --- MoSPI: CPI ---------------------------------------------------------------------------------------

def test_base_2024_cpi_rows_become_index_and_inflation_series_by_node_code():
    rows = fx.cpi24_month(2026, "October") + [fx.cpi24(2026, "October", "Rural", index="101.10", inflation=None)]
    result = mospi.parse_mospi(fx.mospi_answer(rows), "cpi.json", kind=mospi.CPI_2024)
    values = {k: o.value for k, o in result.by_key().items()}
    assert values[("cpi_2024_all_india_combined_general", "2026-10")] == "100.00"
    assert values[("cpi_2024_all_india_combined_general_yoy", "2026-10")] == "4.20"
    assert values[("cpi_2024_all_india_combined_d01", "2026-10")] == "102.00"
    assert values[("cpi_2024_all_india_combined_g01_1_yoy", "2026-10")] == "6.40"
    assert values[("cpi_2024_all_india_combined_c01_1_1", "2026-10")] == "104.00"
    assert ("cpi_2024_all_india_rural_general_yoy", "2026-10") not in values  # null inflation: no row
    obs = result.by_key()[("cpi_2024_all_india_combined_g01_1", "2026-10")]
    assert (obs.level, obs.code, obs.label, obs.base, obs.series_type, obs.unit) == (
        "group", "01.1", "Food", "2024", "Current", "index, 2024=100")
    assert (obs.period_type, obs.period_start, obs.period_end) == ("month", "2026-10-01", "2026-10-31")
    assert obs.dataset == "eSankhyiki CPI, base 2024, Current series" and "Combined" in obs.indicator
    assert result.layout == "cpi_2024" and result.meta["total_pages"] == "1"


def test_the_base_2024_back_series_and_a_state_get_their_own_ids():
    rows = [fx.cpi24(2013, "January", "Urban", series="Back", index="55.10"),
            fx.cpi24(2026, "October", "Urban", state="Andhra Pradesh", index="108.00", inflation="5.00")]
    result = mospi.parse_mospi(fx.mospi_answer(rows), "x")
    assert sorted(result.series_ids()) == ["cpi_2024_andhra_pradesh_urban_general",
                                           "cpi_2024_andhra_pradesh_urban_general_yoy",
                                           "cpi_2024back_all_india_urban_general"]


@pytest.mark.parametrize("change, message", [
    (dict(index="1,10.0"), "is not a number"),
    (dict(index="NA"), "is not a number"),
    (dict(sector="Total"), "is not Rural, Urban or Combined"),
    (dict(month="Oct"), "is not an English month name"),
    (dict(year="26"), "is not a four-digit year"),
    (dict(series="Draft"), "is not Current or Back"),
    (dict(code="1.1"), "is not like 01"),
    (dict(division="Food and beverages", group="Food", code="01"), "a group code has 2 part"),
    (dict(division="Food and beverages", code=None), "a node without a code"),
    (dict(division="Food and beverages", code="01", imputation="maybe"), "imputation"),
])
def test_a_base_2024_row_that_does_not_fit_refuses_the_whole_answer(change, message):
    rows = fx.cpi24_month(2026, "October")
    rows[1] = rows[1] | change
    with pytest.raises(SourceError, match=message):
        mospi.parse_mospi(fx.mospi_answer(rows), "cpi.json")


def test_cpi_answers_with_missing_fields_unknown_layouts_or_mismatched_kinds_are_refused():
    rows = fx.cpi24_month(2026, "October")
    broken = [dict(r) for r in rows]
    del broken[2]["imputation"]
    with pytest.raises(SourceError, match=r"data row 3: fields .* differ"):
        mospi.parse_mospi(fx.mospi_answer(broken), "cpi.json")
    with pytest.raises(SourceError, match="a layout no sample was seen of"):
        mospi.parse_mospi(fx.mospi_answer([{"a": 1}]), "cpi.json")
    with pytest.raises(SourceError, match="a mospi_iip request answered with cpi_2024 rows"):
        mospi.parse_mospi(fx.mospi_answer(rows), "cpi.json", kind=mospi.IIP)
    with pytest.raises(SourceError, match="a web page"):
        mospi.parse_mospi(fx.MAINTENANCE_PAGE, "cpi.json")


def test_a_value_repeated_with_another_value_is_refused_and_with_the_same_one_noted():
    rows = fx.cpi24_month(2026, "October")
    same = mospi.parse_mospi(fx.mospi_answer(rows + [rows[0]]), "cpi.json")
    assert any("repeated with the same value" in n for n in same.notes)
    with pytest.raises(SourceError, match="refused rather than pick one"):
        mospi.parse_mospi(fx.mospi_answer(rows + [rows[0] | {"index": "99.99"}]), "cpi.json")


@pytest.mark.parametrize("kwargs, message", [
    (dict(per=100, total=5, pages=2), "the page count does not add up: 5 records at 100 a page make 1 pages, not 2"),
    (dict(per=2, total=5), "should hold 2 rows, but it holds 5"),
    (dict(page=3, per=2, total=5), "should hold 1 rows"),
])
def test_page_counts_that_do_not_add_up_are_refused(kwargs, message):
    with pytest.raises(SourceError, match=message):
        mospi.parse_mospi(fx.mospi_answer(fx.cpi24_month(2026, "October"), **kwargs), "cpi.json")


def test_error_answers_are_refused_and_no_data_is_an_empty_result():
    for body, text in ((fx.LIMIT_ERROR, "Maximum allowed is 100"), (fx.LIMIT_ERROR_OLD, "cannot be greater than 200")):
        with pytest.raises(SourceError, match=text):
            mospi.parse_mospi(body, "err.json")
    empty = mospi.parse_mospi(fx.NO_DATA, "none.json", kind=mospi.WPI)
    assert empty.observations == [] and empty.notes == ["no data: No Data Found"]
    with pytest.raises(SourceError, match="data without meta_data"):
        mospi.parse_mospi(json.dumps({"data": fx.cpi24_month(2026, "May")}).encode(), "nometa.json")
    with pytest.raises(SourceError, match="not a list of rows"):
        mospi.parse_mospi(b'{"data": {"year": [{"year": 2026}]}, "msg": "ok", "statusCode": true}', "filters.json")


def test_base_2012_and_2010_cpi_groups_subgroups_and_items():
    rows = [fx.cpi_group(2025, "December", "Combined", "General", "General-Overall", index="198.0", inflation="1.33"),
            fx.cpi_group(2025, "December", "Combined", "Consumer Food Price", "Consumer Food Price-Overall"),
            fx.cpi_group(2025, "December", "Rural", "Housing", "Housing-Overall", index=None, inflation=None),
            fx.cpi_group(2025, "December", "Combined", "Food and Beverages", "Cereals and Products", index="197.4"),
            fx.cpi_group(2025, "December", "Combined", "Food and Beverages", "A new subgroup", index="111.1")]
    result = mospi.parse_mospi(fx.mospi_answer(rows, per=200), "c12.json", kind=mospi.CPI_GROUP)
    values = result.by_key()
    assert values[("cpi_2012_all_india_combined_general", "2025-12")].value == "198.0"
    assert values[("cpi_2012_all_india_combined_general_yoy", "2025-12")].status == "F"
    assert ("cpi_2012_all_india_combined_cfpi", "2025-12") in values
    assert not any(k[0].startswith("cpi_2012_all_india_rural_housing") for k in values)  # null index: no row
    assert values[("cpi_2012_all_india_combined_s1_1_01", "2025-12")].code == "1.1.01"
    assert ("cpi_2012_all_india_combined_s_a_new_subgroup", "2025-12") in values
    old = [fx.cpi_group(2014, "December", "Rural", "Miscellaneous", "Household Requisites", base="2010"),
           fx.cpi_group(2014, "December", "Rural", "Clothing; Bedding and Footwear",
                        "Clothing; Bedding and Footwear-Overall", base="2010")]
    ids = mospi.parse_mospi(fx.mospi_answer(old, per=10), "c10.json").series_ids()
    assert "cpi_2010_all_india_rural_s_household_requisites" in ids
    assert "cpi_2010_all_india_rural_clothing_bedding_footwear" in ids
    back = mospi.parse_mospi(fx.mospi_answer(rows[:1], per=200), "c12b.json", series="Back")
    assert back.series_ids() == ["cpi_2012back_all_india_combined_general",
                                 "cpi_2012back_all_india_combined_general_yoy"]
    items = mospi.parse_mospi(fx.mospi_answer([fx.cpi_item(2025, "December", "Air Conditioner, Air Cooler"),
                                               fx.cpi_item(2025, "December", "Mango", index=None, inflation=None)],
                                              per=200), "items.json", kind=mospi.CPI_ITEM)
    assert items.series_ids() == ["cpi_2012_all_india_item_air_conditioner_air_cooler",
                                  "cpi_2012_all_india_item_air_conditioner_air_cooler_yoy"]


# --- MoSPI: IIP, national accounts, WPI, the "RBI" tables ---------------------------------------------

def test_iip_categories_sub_categories_and_the_growth_of_a_year_without_a_year_ago_value():
    rows = [fx.iip(2026, "September", index="124.3", growth="6.7"),
            fx.iip(2026, "September", kind="Sectoral", category="Mining & Quarrying", index="90.4", growth="-6.2"),
            fx.iip(2026, "September", kind="Sectoral", category="Manufacturing", sub="Manufacture of Food Products",
                   index="105.1", growth="7.8"),
            fx.iip(2026, "September", kind="Use-based category", category="Capital Goods", index="153.4",
                   growth="10.4"),
            fx.iip(2024, "March", index="119.0", growth="19.0"),
            fx.iip(2023, "April", index="100.4", growth="0.4"),
            fx.iip(2023, "May", index="101.5", growth="3.0")]
    result = mospi.parse_mospi(fx.mospi_answer(rows, per=200), "iip.json", kind=mospi.IIP)
    values = {k: o.value for k, o in result.by_key().items()}
    assert values[("iip_2022_23_general_yoy", "2026-09")] == "6.7"
    assert values[("iip_2022_23_mining", "2026-09")] == "90.4"
    assert values[("iip_2022_23_manufacturing_food_products_yoy", "2026-09")] == "7.8"
    assert values[("iip_2022_23_capital_goods", "2026-09")] == "153.4"
    assert ("iip_2022_23_general_yoy", "2024-03") not in values and ("iip_2022_23_general_yoy", "2023-04") not in values
    assert values[("iip_2022_23_general", "2024-03")] == "119.0"
    assert values[("iip_2022_23_general_yoy", "2023-05")] == "3.0"  # not the index minus 100: kept, noted
    assert any("2 growth rates" in n for n in result.notes) and any("2023-05" in n for n in result.notes)
    old = mospi.parse_mospi(fx.mospi_answer([fx.iip(2024, "March", index="160.0", growth="5.5", base="2011-12")],
                                            per=200), "iip11.json")
    assert {k: o.value for k, o in old.by_key().items()} == {("iip_2011_12_general", "2024-03"): "160.0",
                                                             ("iip_2011_12_general_yoy", "2024-03"): "5.5"}
    with pytest.raises(SourceError, match="a General row"):
        mospi.parse_mospi(fx.mospi_answer([fx.iip(2026, "May", category="Mining")], per=200), "bad.json")


def test_national_accounts_quarters_years_industries_and_units():
    rows = [fx.nas("2026-27", "Q1", current="8826871", constant="8136153"),
            fx.nas("2025-26", None, frequency="Annual", revision="Provisional Estimates", current="34536796",
                   constant="32470100")]
    result = mospi.parse_mospi(fx.mospi_answer(rows, per=200), "nas.json", kind=mospi.NAS)
    values = result.by_key()
    q1 = values[("gdp_2022_23_q_current_cr", "2026-27Q1")]
    assert (q1.value, q1.period_type, q1.period_start, q1.period_end, q1.unit) == (
        "8826871", "fy_quarter", "2026-04-01", "2026-06-30", "rupees crore")
    year = values[("gdp_2022_23_a_constant_cr", "2025-26")]
    assert (year.value, year.status, year.period_start, year.period_end) == ("32470100", "Provisional Estimates",
                                                                             "2025-04-01", "2026-03-31")
    gva = [fx.nas("2026-27", "Q1", indicator="Gross Value Added", industry="Manufacturing", current="1034961"),
           fx.nas("2026-27", "Q1", indicator="Gross Value Added", industry="Total Gross Value Added")]
    ids = mospi.parse_mospi(fx.mospi_answer(gva, per=200), "gva.json").series_ids()
    assert ids == ["gva_2022_23_q_manufacturing_constant_cr", "gva_2022_23_q_manufacturing_current_cr",
                   "gva_2022_23_q_total_constant_cr", "gva_2022_23_q_total_current_cr"]
    assert period_bounds("2026-27Q4")[1:] == (D(2027, 1, 1), D(2027, 3, 31))


@pytest.mark.parametrize("change, message", [
    (dict(unit="₹ Lakh"), "the unit changed"),
    (dict(indicator="Private Final Consumption Expenditure"), "was not sampled"),
    (dict(quarter="Q5"), "a quarterly row with quarter"),
    (dict(year="2026-28"), "is not a financial year"),
    (dict(frequency="Monthly"), "is not Quarterly or Annual"),
    (dict(current_price=None), "current_price is missing"),
    (dict(frequency="Annual", quarter=None, revision="Fourth Estimates"), "estimate type"),
])
def test_national_accounts_rows_that_do_not_fit_are_refused(change, message):
    with pytest.raises(SourceError, match=message):
        mospi.parse_mospi(fx.mospi_answer([fx.nas("2026-27", "Q1") | change], per=200), "nas.json")


def test_gva_by_sub_industry_was_not_sampled_and_is_refused():
    row = fx.nas("2026-27", "Q1", indicator="Gross Value Added", industry="Manufacturing") | {"subindustry": "x"}
    with pytest.raises(SourceError, match="sub-industry"):
        mospi.parse_mospi(fx.mospi_answer([row], per=200), "gva.json")


def test_wpi_both_bases_their_field_names_and_missing_items():
    rows = [fx.wpi(2026, "September", index="112.5"),
            fx.wpi(2026, "September", major="Primary Articles", index="120.3"),
            fx.wpi(2026, "September", major="Primary Articles", group="Food Articles",
                   sub="Food Grains (Cereals+Pulses)", subsub="Cereals", item="Paddy", index="125.2"),
            fx.wpi(2026, "September", major="Primary Articles", group="Food Articles", sub="Fruits & Vegetables",
                   subsub="Fruits", item="Mango", index=None),
            fx.wpi(2026, "September", major="Fuel & Power", group="Mineral Oils", item="Petrol", index="123.6")]
    result = mospi.parse_mospi(fx.mospi_answer(rows, per=200), "wpi.json", kind=mospi.WPI)
    assert {k[0]: o.value for k, o in result.by_key().items()} == {
        "wpi_2022_23_all": "112.5", "wpi_2022_23_primary": "120.3",
        "wpi_2022_23_primary__food_articles__food_grains_cereals_pulses__cereals__paddy": "125.2",
        "wpi_2022_23_fuel_power__mineral_oils__petrol": "123.6"}
    assert result.by_key()[("wpi_2022_23_fuel_power__mineral_oils__petrol", "2026-09")].level == "item"
    old = mospi.parse_mospi(fx.mospi_answer([fx.wpi(2026, "April", major="Wholesale price index", index="167",
                                                    base="2011-12")], per=10), "wpi11.json")
    assert old.layout == "wpi_2011_12" and old.values("wpi_2011_12_all") == {"2026-04": "167"}
    with pytest.raises(SourceError, match="field names differ by base"):
        mospi.parse_mospi(fx.mospi_answer([fx.wpi(2026, "April") | {"base_year": "2004-05"}], per=10), "w.json")


def test_the_rbi_tables_read_for_checks_with_the_yen_per_100():
    rows = [fx.rbi_fx("2025", "August", "US Dollar", "Average", "87.52"),
            fx.rbi_fx("2025", "August", "Japanese Yen", "End-month", "59.76")]
    result = mospi.parse_mospi(fx.mospi_answer(rows, per=20), "rbi.json", kind=mospi.RBI)
    usd, jpy = (result.by_key()[(s, "2025-08")] for s in ("mospi_rbi_inr_per_usd_average",
                                                          "mospi_rbi_inr_per_jpy_end_month"))
    assert (usd.value, usd.unit) == ("87.52", "rupees per US Dollar")
    assert jpy.unit == "rupees per 100 yen" and "100 yen" in jpy.notes
    assert not ms.stored_by_default("mospi_rbi_inr_per_usd_average")


# --- MoSPI: pages, the calendar pointer ---------------------------------------------------------------

def _page(page, rows, per=2, total=5):
    return mospi.parse_mospi(fx.mospi_answer(rows, page=page, per=per, total=total), f"p{page}", kind=mospi.CPI_2024)


def test_pages_combine_into_one_answer_and_must_all_be_there():
    rows = fx.cpi24_month(2026, "October")
    pages = [_page(1, rows[:2]), _page(2, rows[2:4]), _page(3, rows[4:])]
    whole = mospi.combine_pages(pages[::-1])
    assert len(whole.observations) == 10 and whole.meta["page"] == "1,2,3" and whole.meta["rows"] == "5"
    with pytest.raises(SourceError, match=r"pages \[3\] of 3 are missing"):
        mospi.combine_pages(pages[:2])
    assert len(mospi.combine_pages(pages[:2], complete=False).observations) == 8
    with pytest.raises(SourceError, match="given twice"):
        mospi.combine_pages([pages[0], pages[0], pages[1], pages[2]])
    with pytest.raises(SourceError, match="the pages disagree"):
        mospi.combine_pages([pages[0], _page(2, rows[2:4], total=6), pages[2]])
    clash = _page(2, [rows[0] | {"index": "1.00"}, rows[3]])
    with pytest.raises(SourceError, match="refused rather than pick one"):
        mospi.combine_pages([pages[0], clash, pages[2]])


def test_the_two_page_cpi_shortcut_checks_every_top_level_node_is_there():
    divisions = list(ms.CPI_2024_DIVISIONS.items())
    rows = []
    for sector in ("Rural", "Urban", "Combined"):
        rows.append(fx.cpi24(2026, "October", sector))
        rows += [fx.cpi24(2026, "October", sector, division=name, code=code) for code, name in divisions]
        rows += [fx.cpi24(2026, "October", sector, division="Food and beverages", group=f"g{n}", code=f"01.{n}")
                 for n in range(1, 44)]
    result = mospi.parse_mospi(fx.mospi_answer(rows, per=200), "two.json")
    assert mospi.cpi_2024_top_levels(result) == []
    short = mospi.parse_mospi(fx.mospi_answer(rows[:-1], per=200), "short.json")
    assert mospi.cpi_2024_top_levels(short) == ["2026-10 All India combined: 42 group rows, not 43"]


def test_the_release_calendar_pointer_names_the_current_pdf():
    pointer = mospi.parse_release_calendar_pointer(fx.CALENDAR_POINTER, "pointer.json")
    assert pointer.url == ("https://www.mospi.gov.in/uploads/documents/releaseCalender/"
                           "1700000000000-ARC%202027-28%20test.pdf")
    assert (pointer.document_id, pointer.size, pointer.created_at) == ("9", 1234, "2027-04-01T00:00:00.000Z")
    doc = json.loads(fx.CALENDAR_POINTER)
    doc["data"]["filemime"] = "text/html"
    with pytest.raises(SourceError, match="not a PDF"):
        mospi.parse_release_calendar_pointer(json.dumps(doc).encode(), "pointer.json")
    with pytest.raises(SourceError, match="not the release calendar pointer"):
        mospi.parse_release_calendar_pointer(b'{"status": "error"}', "pointer.json")


# --- MoSPI: the release-date table and when each value was public ---------------------------------------

def test_the_shipped_release_date_table_reads_and_names_a_source_on_every_row():
    rows = mospi.read_release_dates()
    assert len(rows) == 58 and all(r.source.startswith("https://www.mospi.gov.in/") for r in rows)
    assert {r.family for r in rows} == {"cpi", "iip", "gdp"}
    by_id = {r.release_id: r for r in rows}
    assert by_id["cpi_2026-08"].release_date == D(2026, 9, 14) and by_id["cpi_2026-08"].status == "actual"
    assert by_id["iip_2025-03"].content == (("quick", "2025-03"), ("final", "2024-12"), ("final", "2025-01"),
                                            ("final", "2025-02"))
    assert by_id["gdp_2025-26Q3"].content[-1] == ("final", "2023-24")


HEADER = ("family,reference_period,content,planned_date_first,planned_date,release_date,release_time_ist,time_rule,"
          "status,source,notes\n")


@pytest.mark.parametrize("row, message", [
    ("gni,2026-10,first 2026-10,2026-11-12,2026-11-12,,16:00,,planned,s,", "family 'gni'"),
    ("cpi,2026-27Q1,first 2026-10,2026-11-12,2026-11-12,,16:00,,planned,s,", "must be a month"),
    ("cpi,2026-10,final 2026-09,2026-11-12,2026-11-12,,16:00,,planned,s,", "must begin with 'first 2026-10'"),
    ("cpi,2026-10,first 2026-10; quick 2026-09,2026-11-12,2026-11-12,,16:00,,planned,s,", "is not '<stage> <period>'"),
    ("cpi,2026-10,first 2026-10,2026-11-12,2026-11-12,2026-11-12,16:00,,planned,s,", "a planned release with"),
    ("cpi,2026-10,first 2026-10,2026-11-12,2026-11-12,,16:00,,actual,s,", "an actual release without"),
    ("cpi,2026-10,first 2026-10,2026-11-12,2026-11-12,,4 pm,,planned,s,", "must be HH:MM"),
    ("cpi,2026-10,first 2026-10,2026-11-12,2026-11-12,,16:00,,planned,,", "source is empty"),
    ("cpi,2026-10,first 2026-10,2026-10-30,2026-10-30,,16:00,,planned,s,", "cannot release 2026-10"),
    ("cpi,2026-10,first 2026-10,12/11/2026,2026-11-12,,16:00,,planned,s,", "must be YYYY-MM-DD"),
    ("cpi,2026-10,first 2026-10,2026-11-12,2026-11-12,,16:00,,maybe,s,", "status 'maybe'"),
    ("gdp,2026-27,quarter 2026-27Q1,2026-08-31,2026-08-31,,16:00,,planned,s,", "must begin with 'fae 2026-27'"),
])
def test_a_release_date_row_that_does_not_fit_is_refused_naming_the_file_and_line(row, message):
    with pytest.raises(ValueError, match=message) as info:
        mospi.read_release_dates((HEADER + row + "\n").encode(), name="mine.csv")
    assert "mine.csv, line 2" in str(info.value)


def test_a_release_listed_twice_or_a_table_without_its_columns_is_refused():
    row = "cpi,2026-10,first 2026-10,2026-11-12,2026-11-12,,16:00,,planned,s,\n"
    with pytest.raises(ValueError, match="line 3: cpi 2026-10 is listed twice"):
        mospi.read_release_dates((HEADER + row + row).encode(), name="mine.csv")
    with pytest.raises(ValueError, match="missing"):
        mospi.read_release_dates(b"family,reference_period\ncpi,2026-10\n", name="mine.csv")


def test_the_users_table_overrides_a_shipped_row_and_extends_the_table():
    user = (HEADER + "cpi,2026-10,first 2026-10; final 2026-09,2026-11-12,2026-11-12,2026-11-12,16:00,PIB stamp,"
            "actual,https://pib.example/1,\ncpi,2027-03,first 2027-03,2027-04-12,2027-04-12,,16:00,,planned,s,\n")
    calendar = mospi.ReleaseCalendar.load(user.encode())
    assert calendar.release("cpi", "2026-10").basis == "release_table"
    assert calendar.release("cpi", "2026-10").source == "https://pib.example/1"
    assert calendar.release("cpi", "2027-03").day == D(2027, 4, 12)


CAL = mospi.ReleaseCalendar.load()


@pytest.mark.parametrize("family, period, day, time, basis", [
    ("cpi", "2026-09", D(2026, 10, 12), T(16), "release_table"),  # actual, from the 2026-27 calendar
    ("cpi", "2026-08", D(2026, 9, 14), T(16), "release_table"),  # the 12th was a Saturday
    ("cpi", "2026-11", D(2026, 12, 14), T(16), "release_table_planned"),  # planned for Saturday 12 Dec
    ("cpi", "2024-10", D(2024, 11, 12), T(16), "release_rule"),  # the first release at 16:00
    ("cpi", "2024-09", D(2024, 10, 14), T(17, 30), "release_rule"),  # 12 Oct 2024 was a Saturday
    ("cpi", "2013-06", D(2013, 7, 12), T(17, 30), "release_rule"),
    ("cpi", "2013-05", D(2013, 6, 12), T(11, 30), "release_rule"),
    ("iip", "2026-08", D(2026, 9, 28), T(16), "release_table"),
    ("iip", "2026-05", D(2026, 6, 29), T(16), "release_table"),  # the 28th was a Sunday
    ("iip", "2025-05", D(2025, 6, 30), T(16), "release_table_planned"),  # planned for Saturday 28 Jun 2025
    ("iip", "2027-06", D(2027, 7, 28), T(16), "release_rule"),
    ("iip", "2024-08", D(2024, 10, 11), T(17, 30), "release_rule"),  # old rule: 12 Oct a Saturday, the Friday before
    ("iip", "2024-09", D(2024, 11, 12), T(16), "release_rule"),
    ("wpi", "2026-09", D(2026, 10, 14), T(12), "release_rule"),
    ("wpi", "2026-10", D(2026, 11, 16), T(12), "release_rule"),  # 14 Nov 2026 a Saturday
    ("gdp", "2026-27Q1", D(2026, 8, 31), T(16), "release_table"),
    ("gdp", "2024-25Q1", D(2024, 8, 30), T(17, 30), "release_rule"),  # the last weekday of August
    ("gdp", "2024-25Q2", D(2024, 11, 29), T(16), "release_rule"),
    ("gdp", "2023-24Q4", D(2024, 5, 31), T(17, 30), "release_rule"),
    ("gdp", "2024-25", D(2025, 1, 7), T(16), "release_rule"),  # first advance estimates
])
def test_each_release_is_dated_by_the_table_or_by_its_rule(family, period, day, time, basis):
    release = CAL.release(family, period)
    assert (release.day, release.time, release.basis) == (day, time, basis)


def test_the_stages_of_a_value_follow_each_familys_revision_policy():
    def stages(*args, **kwargs):
        return [(s, r.release_id) for s, r in CAL.stages(*args, **kwargs)]
    assert stages("cpi", "2026-09") == [("first", "cpi_2026-09"), ("final", "cpi_2026-10")]
    assert stages("iip", "2026-08") == [("quick", "iip_2026-08"), ("final", "iip_2026-09")]
    assert stages("iip", "2024-06") == [("quick", "iip_2024-06"), ("first_revision", "iip_2024-07"),
                                        ("final", "iip_2024-09")]
    assert stages("iip", "2025-01") == [("quick", "iip_2025-01"), ("first_revision", "iip_2025-02"),
                                        ("final", "iip_2025-03")]
    assert stages("iip", "2025-02") == [("quick", "iip_2025-02"), ("final", "iip_2025-03")]
    assert stages("wpi", "2026-09") == [("provisional", "wpi_2026-09"), ("final", "wpi_2026-11")]
    gdp = stages("gdp", "2025-26Q3")
    assert gdp[0] == ("quarter", "gdp_2025-26Q3") and gdp[-1] == ("final", "gdp_2027-28Q3")
    assert ("revised", "gdp_2025-26Q4") in gdp and ("revised", "gdp_2026-27Q3") in gdp
    assert CAL.gdp_final("2023-24").release_id == "gdp_2025-26Q3"  # from the table's content
    assert CAL.gdp_final("2020-21").release_id == "gdp_2023-24Q3"  # the third revised estimates, by rule
    assert stages("gdp", "2025-26", estimate="Provisional Estimates") == [("pe", "gdp_2025-26Q4")]
    assert stages("gdp", "2024-25", estimate="First Revised Estimates") == [("fre", "gdp_2025-26Q3")]


def test_a_value_read_later_is_dated_by_the_last_stage_released_before_it_was_read():
    seen = dt.datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
    p = CAL.backfill_public_from("cpi", "2024", "2026-09", seen)
    assert (p.text, p.basis, p.release_id) == ("2026-11-12T16:00:00", "release_table_planned", "cpi_2026-10")
    p = CAL.backfill_public_from("cpi", "2024", "2026-10", seen)
    assert (p.text, p.release_id) == ("2026-11-12T16:00:00", "cpi_2026-10")  # its final is still due
    p = CAL.backfill_public_from("cpi", "2024", "2025-01", seen)
    assert (p.text, p.basis, p.release_id) == ("2026-02-12T16:00:00", "release_rule", "cpi_2026-01")  # the launch
    p = CAL.backfill_public_from("iip", "2022-23", "2023-04", seen)
    assert p.text == "2026-06-01T16:00:00" and "launch" in p.rule
    p = CAL.backfill_public_from("gdp", "2022-23", "2025-26", seen, estimate="Provisional Estimates")
    assert (p.text, p.basis) == ("2026-06-05T16:00:00", "release_table")
    p = CAL.backfill_public_from("gdp", "2022-23", "2026-27Q1", seen)
    assert p.text == "2026-08-31T16:00:00"
    p = CAL.backfill_public_from("cpi", "2024", "2027-01", seen)  # read before any release that could set it
    assert (p.text, p.basis) == ("2026-11-18T17:30:00", "first_seen")
    p = CAL.backfill_public_from("cpi", "2024", "2025-01", dt.datetime(2026, 2, 1, tzinfo=UTC))  # before the launch
    assert p.basis == "first_seen" and "launch" in p.rule
    with pytest.raises(ValueError):
        CAL.backfill_public_from("cpi", "2024", "2026-09", dt.datetime(2026, 11, 18))


def test_a_value_seen_by_polling_is_dated_by_its_release_only_within_six_hours_of_it():
    def live(hour, minute, day=12):
        return CAL.live_public_from("cpi", "2026-09", "first", dt.datetime(2026, 10, day, hour, minute, tzinfo=UTC))
    assert (live(10, 40).text, live(10, 40).basis) == ("2026-10-12T16:00:00", "release_table")  # 16:10 India time
    assert (live(16, 29).text, live(16, 29).basis) == ("2026-10-12T16:00:00", "release_table")  # 21:59
    assert (live(16, 31).text, live(16, 31).basis) == ("2026-10-12T22:01:00", "first_seen")
    assert (live(10, 0).text, live(10, 0).basis) == ("2026-10-12T15:30:00", "first_seen")  # before 16:00
    assert live(4, 0, day=13).basis == "first_seen"
    with pytest.raises(ValueError, match="no stage"):
        CAL.live_public_from("cpi", "2026-09", "quick", NOW)


def test_timing_keys_name_family_base_and_estimate():
    obs = mospi.parse_mospi(fx.mospi_answer([fx.cpi24(2013, "May", "Rural", series="Back")]), "b").observations[0]
    assert mospi.timing_key(obs) == ("cpi", "2024back", "2013-05", None)
    year = mospi.parse_mospi(fx.mospi_answer([fx.nas("2024-25", None, frequency="Annual",
                                                     revision="First Revised Estimates")], per=200), "n")
    assert mospi.timing_key(year.observations[0]) == ("gdp", "2022-23", "2024-25", "First Revised Estimates")
    rbi = mospi.parse_mospi(fx.mospi_answer([fx.rbi_fx("2025", "July", "Euro", "Average", "100.64")], per=20), "r")
    assert mospi.timing_key(rbi.observations[0]) is None
    p = ms.public_from(rbi.observations[0], seen_at=NOW)
    assert (p.basis, p.text) == ("first_seen", "2026-11-18T15:30:00")


def test_financial_years_written_like_months_are_read_as_financial_years():
    # 2011-12 is also December 2011: national accounts periods must not be read as months
    assert period_bounds("2011-12") == ("month", D(2011, 12, 1), D(2011, 12, 31))
    assert period_bounds("2011-12", "fy") == ("fy", D(2011, 4, 1), D(2012, 3, 31))
    with pytest.raises(ValueError, match="not a fy_quarter period"):
        period_bounds("2011-12", "fy_quarter")
    first = [(s, r.release_id) for s, r in CAL.stages("gdp", "2011-12Q1")][:2]
    assert first == [("quarter", "gdp_2011-12Q1"), ("revised", "gdp_2011-12Q2")]
    assert CAL.stages("gdp", "2010-11", estimate="Final Estimates")[0][0] == "final"
    year = mospi.parse_mospi(fx.mospi_answer([fx.nas("2011-12", None, frequency="Annual", base="2011-12",
                                                     revision="Final Estimates")], per=200), "n").observations[0]
    p = ms.public_from(year, seen_at=NOW)
    assert (year.period_type, p.basis) == ("fy", "release_rule") and p.time < dt.datetime(2016, 1, 1)
    table = (",".join(mospi.RELEASE_DATE_COLUMNS) + "\ngdp,2011-12,fae 2011-12,2012-02-07,2012-02-07,,17:30,,planned,"
             "a typed-in test row,\n").encode()
    assert mospi.read_release_dates(table, name="t.csv")[0].reference_period == "2011-12"


# --- US Treasury ----------------------------------------------------------------------------------------

def test_treasury_requests_use_the_per_year_path_and_its_parser_reads_any_subset_of_tenors():
    assert gs.ust_request(2026).url == (
        "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/2026/all"
        "?type=daily_treasury_yield_curve&field_tdr_date_value=2026&page&_format=csv")
    body = fx.ust_csv([("11/16/2027", ["4.10", "4.19", "4.27", "4.34", "4.33", "4.42", "4.62", "4.83", "4.87", "4.93",
                                       "5.01", "5.12", "5.51", "5.46"])])
    result = gs.parse_ust_csv(body, "ust.csv", year=2027)
    assert result.values("ust_par_1_5m") == {"2027-11-16": "4.19"} and len(result.observations) == 14
    old = fx.ust_csv([("01/02/2004", ["0.90", "0.93", "1.01", "1.27", "1.94", "2.43", "3.27", "3.79", "4.38", ""])],
                     header=fx.UST_HEADER_2004)
    result = gs.parse_ust_csv(old, "ust2004.csv")
    assert result.values("ust_par_10y") == {"2004-01-02": "4.38"} and result.values("ust_par_20y") == {}
    assert "ust_par_30y" not in result.series_ids()


@pytest.mark.parametrize("body, kwargs, message", [
    (fx.ust_csv([("11/16/2027", ["4.10"])], header=["1 Mo"]), dict(year=2026), "in the file of 2026"),
    (fx.ust_csv([("11/16/2027", ["4.10"])], header=["13 Wk"]), {}, "were not sampled"),
    (fx.ust_csv([("11/16/2027", ["N/A"])], header=["1 Mo"]), {}, "is not a number"),
    (fx.ust_csv([("2027-11-16", ["4.10"])], header=["1 Mo"]), {}, "is not MM/DD/YYYY"),
    (fx.ust_csv([("11/16/2027", ["4.10", "4.20"])], header=["1 Mo"]), {}, "3 cells under a header of 2"),
    (fx.ust_csv([("11/16/2027", ["4.10"]), ("11/16/2027", ["4.20"])], header=["1 Mo"]), {}, "pick one"),
    (fx.ust_csv([("11/16/2027", ["4.10"])], header=["1 Mo"]), dict(curve="real"), "was not sampled"),
    (fx.AKAMAI_PAGE, {}, "a web page"),
])
def test_treasury_files_that_do_not_fit_are_refused(body, kwargs, message):
    with pytest.raises(SourceError, match=message):
        gs.parse_ust_csv(body, "ust.csv", **kwargs)


# --- New York Fed ---------------------------------------------------------------------------------------

def test_new_york_fed_json_keeps_the_published_text_of_every_rate_percentile_volume_and_target():
    body = fx.nyfed_json([fx.sofr_row("2027-11-17"), fx.effr_row("2027-11-17"), fx.sofrai_row("2027-11-18"),
                          fx.sofr_row("2027-11-16", kind="TGCR", p=("NA", "NA", "NA", "NA"), footnote="2"),
                          fx.sofr_row("2027-11-16", kind="OBFR", rate="3.87", revision="r"),
                          fx.effr_old_row("2007-06-01"), fx.effr_old_row("2009-06-01", target="0.00", high="0.25")])
    assert b'"percentRate": 3.90' in body  # numbers as the API writes them
    result = gs.parse_nyfed_json(body, "rates.json")
    values = {k: o.value for k, o in result.by_key().items()}
    assert values[("sofr", "2027-11-17")] == "3.90" and values[("sofr_volume_bn", "2027-11-17")] == "2500"
    assert values[("effr_p99", "2027-11-17")] == "3.90"
    assert (values[("fomc_target_low", "2027-11-17")], values[("fomc_target_high", "2027-11-17")]) == ("3.75", "4.00")
    assert values[("sofr_index", "2027-11-18")] == "1.20000000" and values[("sofr_avg_180d", "2027-11-18")] == "3.70000"
    assert ("tgcr_p1", "2027-11-16") not in values and values[("tgcr", "2027-11-16")] == "3.90"  # NA: missing
    assert result.by_key()[("tgcr", "2027-11-16")].footnote == "2"
    assert result.by_key()[("obfr", "2027-11-16")].revision == "r"
    assert values[("fomc_target_low", "2007-06-01")] == "5.25" and ("fomc_target_high", "2007-06-01") not in values
    assert values[("effr_intraday_high", "2007-06-01")] == "5.50" and values[("effr_std_dev", "2007-06-01")] == "0.10"
    assert values[("fomc_target_high", "2009-06-01")] == "0.25"
    assert result.notes == ["1 rows marked revised (r)"]


@pytest.mark.parametrize("row, message", [
    (fx.sofr_row("2027-11-17", kind="AMERIBOR"), "rate type 'AMERIBOR' was not sampled"),
    (fx.sofr_row("2027-11-17") | {"newField": "1"}, "a layout not sampled"),
    ({k: v for k, v in fx.sofr_row("2027-11-17").items() if k != "volumeInBillions"}, "a layout not sampled"),
    (fx.sofr_row("2027-11-17", rate="three"), "is not a number"),
    (fx.sofr_row("2027-11-17", revision="x"), "revisionIndicator 'x'"),
    (fx.sofr_row("11/17/2027"), "is not YYYY-MM-DD"),
])
def test_new_york_fed_rows_that_do_not_fit_are_refused(row, message):
    with pytest.raises(SourceError, match=message):
        gs.parse_nyfed_json(fx.nyfed_json([row]), "rates.json")
    with pytest.raises(SourceError, match="no refRates"):
        gs.parse_nyfed_json(b'{"rates": []}', "rates.json")


def test_new_york_fed_csv_and_request_urls():
    body = fx.nyfed_csv(["11/17/2027,SOFR,3.92,3.87,3.90,3.97,3.99,3173,,,,,,,,,,,",
                         "11/16/2027,EFFR,3.88,3.85,3.88,3.89,3.90,95,3.75,4.00,,,,,,,,Y,"])
    result = gs.parse_nyfed_csv(body, "sofr.csv")
    assert result.values("sofr") == {"2027-11-17": "3.92"}
    assert result.values("fomc_target_high") == {"2027-11-16": "4.00"}
    assert result.by_key()[("effr", "2027-11-16")].revision == "r"
    with pytest.raises(SourceError, match="not the New York Fed's CSV header"):
        gs.parse_nyfed_csv(b"Date,Rate\n11/17/2027,3.92\n", "sofr.csv")
    assert gs.nyfed_search_request("secured", D(2027, 11, 8), D(2027, 11, 18)).url == (
        "https://markets.newyorkfed.org/api/rates/secured/all/search.json?startDate=2027-11-08&endDate=2027-11-18")
    assert gs.nyfed_last_request("sofr", 5, "csv").url == "https://markets.newyorkfed.org/api/rates/secured/sofr/last/5.csv"
    assert gs.nyfed_latest_request().url == "https://markets.newyorkfed.org/api/rates/all/latest.json"
    with pytest.raises(ValueError):
        gs.nyfed_search_request("secured", D(2027, 11, 18), D(2027, 11, 8))


# --- EIA ------------------------------------------------------------------------------------------------

WEEKS = [("2027 Dec-27 to Dec-31", ["70.10", "70.20", "", "70.40", "70.50"]),
         ("2027 Dec-27 to Dec-31".replace("2027 Dec-27 to Dec-31", "2028 Jan- 3 to Jan- 7"),
          ["71.10", "NA", "W", "-", "71.50"])]


def test_eia_weekly_grid_reads_one_value_a_day_and_its_release_dates():
    weeks = [("2027 Dec-27 to Dec-31", ["70.10", "70.20", "", "70.40", "70.50"]),
             ("2028 Jan- 3 to Jan- 7", ["71.10", "NA", "W", "-", "71.50"]),
             ("2028 Jan-10 to Jan-14", ["72.00", "", "", "", ""]),
             ("2029 Dec-31 to Jan- 4", ["73.00", "", "", "", ""])]
    result = gs.parse_eia_history(fx.eia_page(weeks), "brent.htm")
    values = result.values("brent_spot_usd_bbl")
    assert values == {"2027-12-27": "70.10", "2027-12-28": "70.20", "2027-12-30": "70.40", "2027-12-31": "70.50",
                      "2028-01-03": "71.10", "2028-01-07": "71.50", "2028-01-10": "72.00", "2029-12-31": "73.00"}
    assert result.meta["release_date"] == "2027-11-18" and result.meta["next_release_date"] == "2027-11-24"
    assert result.notes == ["3 cells held a legend marker (-, --, NA, W): missing"]
    obs = result.observations[0]
    assert (obs.unit, obs.code, obs.source) == ("US dollars per barrel", "RBRTE", "eia")


@pytest.mark.parametrize("weeks, kwargs, message", [
    ([("2027 Dec-28 to Jan- 1", ["1.00"] * 5)], {}, "not a Monday to the Friday after it"),
    ([("2027 Dec-27 to Dec-30", ["1.00"] * 5)], {}, "not a Monday to the Friday after it"),
    ([("Week 52", ["1.00"] * 5)], {}, "is not like"),
    ([("2027 Dec-27 to Dec-31", ["1.00", "1,00", "", "", ""])], {}, "is not a number"),
    ([("2028 Jan- 3 to Jan- 7", ["1"] * 5), ("2027 Dec-27 to Dec-31", ["1"] * 5)], {}, "does not follow"),
    ([("2027 Dec-27 to Dec-31", ["1.00"] * 5)], dict(title="Europe Brent Spot Price FOB (Dollars per Tonne)"),
     "the unit changed"),
    ([("2027 Dec-27 to Dec-31", ["1.00"] * 5)], dict(header=("Week", "Mon", "Tue", "Wed", "Thu", "Fri")),
     "0 tables with the header"),
])
def test_eia_pages_that_do_not_fit_are_refused(weeks, kwargs, message):
    with pytest.raises(SourceError, match=message):
        gs.parse_eia_history(fx.eia_page(weeks, **kwargs), "brent.htm")


def test_eia_refusal_pages_are_refused_as_such():
    with pytest.raises(SourceError, match="refused the request"):
        gs.parse_eia_history(fx.AKAMAI_PAGE, "brent.htm")
    with pytest.raises(SourceError, match="bot challenge"):
        gs.parse_eia_history(fx.CHALLENGE_PAGE, "brent.htm")


# --- Federal Reserve H.10 -------------------------------------------------------------------------------

def test_h10_history_pages_of_both_styles_and_the_data_download_csv():
    page = fx.h10_page([("3-JAN-00", "43.5500"), ("11-NOV-26", "ND"), ("12-NOV-26", "94.0400")])
    result = gs.parse_h10_history(page, "dat00_in.htm")
    assert result.values("usdinr_h10_noon") == {"2000-01-03": "43.5500", "2026-11-12": "94.0400"}
    assert result.meta == {"rows": "3", "release_date": "2027-11-15"}
    assert result.notes == ["1 days marked ND (no data): missing"]
    old = gs.parse_h10_history(fx.h10_page_1990s([("1-Jan-90", "ND"), ("2-Jan-90", "16.9600"),
                                                  ("31-Dec-99", "43.5100")]), "dat96_in.htm")
    assert old.values("usdinr_h10_noon") == {"1990-01-02": "16.9600", "1999-12-31": "43.5100"}
    assert old.meta["release_date"] == "1999-12-31"
    ddp = gs.parse_h10_ddp_csv(fx.ddp_csv([("2026-11-10", "1.1500", "93.9300"), ("2026-11-11", "ND", "ND")]), "ddp.csv")
    assert ddp.values("usdinr_h10_noon") == {"2026-11-10": "93.9300"}


@pytest.mark.parametrize("body, parser, message", [
    (fx.h10_page([("3-JAN-00", "43.55")], unit="(Rates in euros per U.S. dollar)"), gs.parse_h10_history,
     "the unit changed"),
    (fx.h10_page([("4-JAN-00", "43.55"), ("3-JAN-00", "43.60")]), gs.parse_h10_history, "does not follow"),
    (fx.h10_page([("2000-01-03", "43.55")]), gs.parse_h10_history, "is not like 3-JAN-00"),
    (fx.h10_page([("31-FEB-00", "43.55")]), gs.parse_h10_history, "is not a date"),
    (fx.h10_page([("3-JAN-00", "43,55")]), gs.parse_h10_history, "is not a number"),
    (fx.ddp_csv([("2026-11-10", "1.15", "93.93")], currency="USD"), gs.parse_h10_ddp_csv, "the unit changed"),
    (fx.ddp_csv([("2026-11-10", "1.15", "93.93")], multiplier="100"), gs.parse_h10_ddp_csv, "the unit changed"),
    (b"Date,Rate\n2026-11-10,93.93\n", gs.parse_h10_ddp_csv, "not a Data Download Program CSV"),
])
def test_h10_answers_that_do_not_fit_are_refused(body, parser, message):
    with pytest.raises(SourceError, match=message):
        parser(body, "h10")


def test_h10_request_urls():
    assert gs.h10_history_request("dat96_in").url == "https://www.federalreserve.gov/releases/h10/hist/dat96_in.htm"
    assert gs.h10_ddp_request(last_obs=15).url == (
        "https://www.federalreserve.gov/datadownload/Output.aspx?rel=H10&series=60f32914ab61dfab590e0e470153e3ae&"
        "lastobs=15&from=&to=&filetype=csv&label=include&layout=seriescolumn&type=package")
    assert "from=11/01/2026&to=11/18/2026" in gs.h10_ddp_request(start=D(2026, 11, 1), end=D(2026, 11, 18)).url
    with pytest.raises(ValueError):
        gs.h10_history_request("dat89_in")


# --- BIS ------------------------------------------------------------------------------------------------

def test_bis_policy_rates_in_both_csv_versions_and_the_rupee_rate():
    v1 = gs.parse_bis_csv(fx.cbpol_v1([("IN", "2026-10-07", "5.5", "A"), ("IN", "2026-10-10", "NaN", "M"),
                                       ("US", "2026-09-17", "3.875", "A")]), "cbpol.csv")
    assert v1.values("policy_rate_in_bis") == {"2026-10-07": "5.5"} and v1.values("policy_rate_us_bis") == {
        "2026-09-17": "3.875"}
    assert v1.layout == "v1_WS_CBPOL" and v1.notes == ["1 days with no value (NaN, OBS_STATUS M): missing"]
    assert v1.by_key()[("policy_rate_in_bis", "2026-10-07")].status == "A"
    v2 = gs.parse_bis_csv(fx.cbpol_v2([("IN", "2026-10-15", "5.5", "A"), ("US", "2026-11-10", "3.875", "A")]),
                          "v2.csv", dataflow="WS_CBPOL")
    assert v2.layout == "v2_WS_CBPOL" and len(v2.observations) == 2
    xru = gs.parse_bis_csv(fx.xru_v1([("2026-11-10", "93.928571", "A"), ("2026-11-14", "NaN", "M")]), "xru.csv")
    assert xru.values("usdinr_bis_daily") == {"2026-11-10": "93.928571"}
    calendar = gs.parse_bis_release_calendar(fx.BIS_REL_CAL, "cal.csv")
    assert calendar[0] == {"category": "CBPOL", "release_type": "DM2", "period": "2027-10",
                           "release_date": "2027-11-11"}


@pytest.mark.parametrize("body, message", [
    (fx.cbpol_v1([("IN", "2026-10-07", "NaN", "A")]), "with OBS_STATUS 'A', not M"),
    (fx.cbpol_v1([("IN", "2026-10-07", "5.5", "M")]), "marked missing"),
    (fx.cbpol_v1([("IN", "2026-10-07", "5.5", "A")], unit="355"), "the unit changed"),
    (fx.cbpol_v1([("IN", "2026-10", "5.5", "A")], freq="M"), "only daily"),
    (fx.cbpol_v1([("IN", "2026-10-07", "5.5", "A"), ("IN", "2026-10-07", "5.4", "A")]), "pick one"),
    (fx.cbpol_v2([("IN", "2026-10-15", "5.5", "A")], flow="BIS:WS_XRU(1.0)"), "WS_XRU in SDMX-CSV 2.0"),
    (fx.cbpol_v2([("IN", "2026-10-15", "5.5", "A")], action="D"), "action I"),
    (fx.xru_v1([("2026-11-10", "93.9", "A")], collection="E"), "was sampled"),
    (b'<?xml version="1.0" ?><message:Error/>', "not BIS CSV"),
])
def test_bis_answers_that_do_not_fit_are_refused(body, message):
    with pytest.raises(SourceError, match=message):
        gs.parse_bis_csv(body, "bis.csv")


def test_bis_request_urls_prefer_v2_for_policy_rates_and_v1_for_the_rupee():
    r = gs.bis_request("WS_CBPOL", "D.IN+US", last_n=10)
    assert r.url == "https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CBPOL/1.0/D.IN+US?lastNObservations=10"
    assert r.accept == "application/vnd.sdmx.data+csv;version=2.0.0"
    assert gs.bis_request("WS_XRU", "D.IN..", start_period=D(2026, 11, 1)).url == (
        "https://stats.bis.org/api/v1/data/WS_XRU/D.IN../all?format=csv&startPeriod=2026-11-01")
    assert gs.bis_release_calendar_request("2026-10").url == (
        "https://stats.bis.org/api/v1/data/BIS_REL_CAL/all/all?format=csv&startPeriod=2026-10")
    for call in (lambda: gs.bis_request("WS_LONG_CPI"), lambda: gs.bis_request("WS_XRU", "D.IN..", version="v2"),
                 lambda: gs.bis_request("WS_CBPOL", "M.IN"), lambda: gs.bis_request("WS_CBPOL", "D.IN", version="v1",
                                                                                     last_n=5)):
        with pytest.raises(ValueError):
            call()


# --- When US and BIS values were public -----------------------------------------------------------------

def test_us_publication_rules_in_india_time_across_daylight_saving():
    assert gs.ust_public_from(D(2026, 3, 6)).text == "2026-03-07T10:29:59"  # EST
    assert gs.ust_public_from(D(2026, 3, 9)).text == "2026-03-10T09:29:59"  # EDT
    assert gs.nyfed_public_from("SOFR", D(2026, 11, 13), D(2026, 11, 16)).text == "2026-11-16T20:30:00"
    assert gs.nyfed_public_from("SOFR", D(2026, 11, 13), D(2026, 11, 16), revised=True).text == "2026-11-17T03:30:00"
    assert gs.nyfed_public_from("EFFR", D(2026, 11, 13), D(2026, 11, 16)).text == "2026-11-16T21:30:00"
    assert gs.nyfed_public_from("EFFR", D(2015, 6, 1), D(2015, 6, 2)).text == "2015-06-03T09:29:59"
    assert gs.nyfed_public_from("SOFRAI", D(2026, 11, 18), None).text == "2026-11-18T20:30:00"
    latest = gs.nyfed_public_from("SOFR", D(2026, 11, 17), None)
    assert (latest.time, latest.basis) == (None, "first_seen")
    assert gs.eia_public_from(D(2026, 11, 17)).text == "2026-11-20T10:29:59"  # Tuesday: Wednesday's release
    assert gs.eia_public_from(D(2026, 11, 13)).text == "2026-11-20T10:29:59"
    assert gs.eia_public_from(D(2026, 11, 18)).text == "2026-11-27T10:29:59"  # a Wednesday waits a week
    assert gs.h10_public_from(D(2026, 11, 13)).text == "2026-11-18T02:45:00"  # Friday: Monday's release
    assert gs.h10_public_from(D(2026, 11, 9)).text == "2026-11-18T02:45:00"  # a Monday waits a week
    assert gs.h10_public_from(D(2026, 7, 10)).text == "2026-07-15T01:45:00"  # EDT
    assert gs.bis_policy_public_from("IN", D(2026, 10, 7)).text == "2026-10-07T23:59:59"
    assert gs.bis_policy_public_from("US", D(2026, 9, 17)).text == "2026-09-18T09:29:59"
    # before 19 Dec 1985 the US series is the effective rate, published the next business day: the second weekday
    assert gs.bis_policy_public_from("US", D(1985, 12, 13)).text == "1985-12-18T10:29:59"  # Friday: Tuesday
    assert gs.bis_policy_public_from("US", D(1985, 12, 18)).text == "1985-12-21T10:29:59"
    # from 19 Dec 1985 the target's mid-point, not announced before 4 Feb 1994: the same second weekday, an assumption
    assert gs.bis_policy_public_from("US", D(1985, 12, 19)).text == "1985-12-24T10:29:59"  # Thursday: Monday
    assert "not announce" in gs.bis_policy_public_from("US", D(1990, 7, 13)).rule
    assert gs.bis_policy_public_from("US", D(1994, 2, 4)).text == "1994-02-05T10:29:59"  # announced: its own day
    assert gs.bis_policy_public_from("IN", D(1990, 1, 5)).text == "1990-01-05T23:59:59"  # the bank rate, announced
    # BIS's own rupee figure: a week after the first Thursday release at least two days after its date, 23:59:59
    # Basel time taken as UTC+1 (04:29:59 India time the next day)
    assert gs.bis_fx_public_from(D(2026, 11, 10)).text == "2026-11-20T04:29:59"


def test_new_york_fed_rows_are_dated_by_the_next_date_of_the_same_rate():
    body = fx.nyfed_json([fx.sofr_row("2026-11-17"), fx.sofr_row("2026-11-13"), fx.effr_row("2026-11-13"),
                          fx.sofr_row("2026-11-16")])
    dates = gs.nyfed_public_from_all(gs.parse_nyfed_json(body, "x").observations)
    assert dates[("sofr", "2026-11-13")].text == "2026-11-16T20:30:00"
    assert dates[("sofr_p25", "2026-11-16")].text == "2026-11-17T20:30:00"
    assert dates[("sofr", "2026-11-17")].time is None
    assert dates[("fomc_target_low", "2026-11-13")].time is None  # the only EFFR row: no next date


EXAMPLE_EVENTS = Path(__file__).resolve().parents[1] / "data" / "index_events_example.csv"


def test_index_events_are_dated_in_the_providers_zone_and_checked():
    events = gs.load_index_events(EXAMPLE_EVENTS)  # the example table of the repository: facts typed in, with sources
    assert len(events) == 13 and {e.status for e in events} == {"verified", "unverified"}
    # every row names a page it was read on or reported in; one only a report's title (no URL was found)
    assert [e.event_id for e in events if not e.source_url.startswith("https://")] == [
        "idx_bbg_globalagg_2026_11_review"]
    by_id = {e.event_id: e for e in events}
    assert gs.index_event_public_from(by_id["idx_ftse_emgbi_2024_announce"]).text == "2024-10-09T04:29:59"  # BST
    assert gs.index_event_public_from(by_id["idx_jpm_gbiem_2023_announce"]).text == "2023-09-22T09:29:59"  # EDT
    month = gs.index_event_public_from(by_id["idx_ftse_emgbi_2025_start"])
    assert month.text == "2025-10-01T04:29:59" and "last day" in month.rule
    assert all(e.source_url for e in events)


EVENTS = ("event_id,provider,index_name,event_type,announced_date,announced_time,announced_tz,effective_start,"
          "effective_end,phase_in,target_weight_pct,bonds,status,source_url,notes\n")


@pytest.mark.parametrize("row, message", [
    ("e1,FTSE,EMGBI,announcement,2024-10-08,,Europe/London,,,,,,rumoured,src,", "status 'rumoured'"),
    ("e1,FTSE,EMGBI,announcement,2024-10-08,,Europe/Paris,,,,,,verified,src,", "announced_tz"),
    ("e1,FTSE,EMGBI,rumour,2024-10-08,,Europe/London,,,,,,verified,src,", "event_type 'rumour'"),
    ("e1,FTSE,EMGBI,announcement,2024-10-08,,Europe/London,2025-09,2025-08,,,,verified,src,", "before"),
    ("e1,FTSE,EMGBI,announcement,08/10/2024,,Europe/London,,,,,,verified,src,", "YYYY-MM-DD or YYYY-MM"),
    ("e1,FTSE,EMGBI,announcement,2024-10,21:00,Europe/London,,,,,,verified,src,", "needs a full announced_date"),
    ("e1,FTSE,EMGBI,announcement,2024-10-08,9pm,Europe/London,,,,,,verified,src,", "is not HH:MM"),
    ("e1,FTSE,EMGBI,announcement,2024-10-08,,Europe/London,,,,ten,,verified,src,", "is not a number"),
    ("e1,FTSE,EMGBI,announcement,2024-10-08,,Europe/London,,,,,,verified,,", "source_url is empty"),
    ("E 1,FTSE,EMGBI,announcement,2024-10-08,,Europe/London,,,,,,verified,src,", "lower-case"),
])
def test_an_index_event_that_does_not_fit_is_refused(row, message):
    with pytest.raises(ValueError, match=message):
        gs.read_index_events((EVENTS + row + "\n").encode(), name="events.csv")


def test_only_the_table_given_is_read_and_nothing_is_a_default():
    row = ("idx_ftse_2026_09_review,FTSE Russell,review,review_no_change,2026-10-06,21:30,Europe/London,,,,,,verified,"
           "https://example.invalid/review,typed by hand\n")
    events = {e.event_id: e for e in gs.load_index_events((EVENTS + row).encode())}
    assert len(events) == 1 and events["idx_ftse_2026_09_review"].announced_time == T(21, 30)
    with pytest.raises(ValueError, match="none is shipped as a default"):
        gs.read_index_events(None)
    assert gs.index_event_public_from(events["idx_ftse_2026_09_review"]).text == "2026-10-07T02:00:00"
    with pytest.raises(ValueError, match="used twice"):
        gs.read_index_events((EVENTS + row + row).encode(), name="events.csv")


# --- The umbrella: hosts, kinds, catalogue ---------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "https://www.rbi.org.in/x", "https://rbidocs.rbi.org.in/x", "https://website.rbi.org.in/x",
    "https://fred.stlouisfed.org/series/DGS10", "https://api.stlouisfed.org/fred/series", "https://alfred.stlouisfed.org/",
    "https://www.fbil.org.in/", "https://www.ccilindia.com/", "https://www.amfiindia.com/", "https://www.icra.in/",
    "https://www.indiaratings.co.in/", "https://www.crisil.com/", "https://www.fpi.nsdl.co.in/", "https://www.nseindia.com/",
    "https://www.bseindia.com/", "https://www.bloomberg.com/news", "https://mcp.mospi.gov.in/",
])
def test_refused_hosts_are_never_requested(url):
    with pytest.raises(ValueError, match="never requested"):
        ms.check_request_url(url)


@pytest.mark.parametrize("url, message", [
    ("https://example.com/data", "not a host of any macro source"),
    ("http://api.mospi.gov.in/api/cpi/getCPIData", "only https"),
    ("https://user:pw@api.mospi.gov.in/api", "only https"),
    ("https://api.mospi.gov.in:8443/api", "a port other than 443"),
    ("https://api.mospi.gov.in/api/x y", "without spaces"),
    ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/all/all?"
     "type=daily_treasury_yield_curve&field_tdr_date_value=all&page&_format=csv", "one year at a time"),
])
def test_request_urls_must_be_https_to_a_known_host(url, message):
    with pytest.raises(ValueError, match=message):
        ms.check_request_url(url)
    assert ms.check_request_url(gs.ust_request(2026).url)[0] == "home.treasury.gov"


@pytest.mark.parametrize("agent", ["research bot (me@example.com)", "client https://example.com", "cr-strat/0.1",
                                   "CR_STRAT", "www.example.com fetcher", "tab\tname", ""])
def test_client_names_that_identify_someone_are_refused(agent):
    with pytest.raises(ValueError):
        ms.check_generic_user_agent(agent)
    assert ms.check_generic_user_agent(ms.DEFAULT_USER_AGENT) == ms.DEFAULT_USER_AGENT


def test_saved_answers_are_recognised_from_their_content():
    cases = {
        mospi.CPI_2024: fx.mospi_answer(fx.cpi24_month(2026, "May")),
        mospi.CPI_GROUP: fx.mospi_answer([fx.cpi_group(2025, "May", "Rural", "General", "General-Overall")]),
        mospi.CPI_ITEM: fx.mospi_answer([fx.cpi_item(2025, "May", "Egg")]),
        mospi.IIP: fx.mospi_answer([fx.iip(2026, "May")]), mospi.NAS: fx.mospi_answer([fx.nas("2026-27", "Q1")]),
        mospi.WPI: fx.mospi_answer([fx.wpi(2026, "May", base="2011-12", major="Wholesale price index")]),
        mospi.RBI: fx.mospi_answer([fx.rbi_fx("2025", "May", "Euro", "Average", "90.10")]),
        mospi.ANY_KIND: fx.NO_DATA, mospi.CALENDAR_POINTER: fx.CALENDAR_POINTER,
        gs.UST_PAR: fx.ust_csv([("11/16/2027", ["4.10"])], header=["1 Mo"]),
        gs.NYFED_JSON: fx.nyfed_json([fx.sofr_row("2027-11-17")]), gs.NYFED_CSV: fx.nyfed_csv([]),
        gs.EIA_BRENT: fx.eia_page(WEEKS[:1]), gs.H10_HISTORY: fx.h10_page([("3-JAN-00", "43.55")]),
        gs.H10_DDP: fx.ddp_csv([]), gs.BIS_CBPOL: fx.cbpol_v2([]), gs.BIS_XRU: fx.xru_v1([]),
        gs.BIS_REL_CAL: fx.BIS_REL_CAL,
    }
    for kind, body in cases.items():
        assert ms.detect_kind(body, kind) == kind, kind
    assert ms.detect_kind(fx.LIMIT_ERROR) == mospi.ANY_KIND
    with pytest.raises(SourceError, match="Access Denied"):
        ms.detect_kind(fx.AKAMAI_PAGE)
    with pytest.raises(SourceError, match="no known layout"):
        ms.detect_kind(b'{"something": 1}')


def test_parse_reads_a_fetched_answer_with_its_requests_parser_and_parameters():
    request = gs.ust_request(2027)
    with pytest.raises(SourceError, match="in the file of 2027"):
        ms.parse(request, fx.ust_csv([("11/16/2026", ["4.10"])], header=["1 Mo"]))
    request = mospi.cpi_index_request(series="Back")
    result = ms.parse(request, fx.mospi_answer([fx.cpi_group(2025, "May", "Rural", "General", "General-Overall")]))
    assert result.series_ids()[0].startswith("cpi_2012back_")


def test_the_catalogue_has_one_entry_per_id_each_with_terms_and_what_to_cite():
    entries = ms.catalogue()
    ids = [e.series_id for e in entries]
    assert len(ids) == len(set(ids)) and len(ids) > 200
    assert all(e.citation and e.terms and e.terms_url and e.unit and e.frequency for e in entries)
    sofr = ms.series_info("sofr")
    assert sofr.first_period == "2018-04-02" and "Terms of Use posted at newyorkfed.org" in sofr.citation
    food = ms.series_info("cpi_2024_all_india_combined_g01_1")
    assert food.aliases == "cpi_2024_all_india_combined_food" and "CFPI" in food.name
    assert ms.series_info("cpi_2024_all_india_combined_d04").aliases == "cpi_2024_all_india_combined_housing_utilities"
    assert ms.stored_by_default("cpi_2024_all_india_rural_g04_5_yoy")
    assert not ms.stored_by_default("cpi_2024_all_india_rural_c01_1_1")  # classes and below: not by default
    assert not ms.stored_by_default("cpi_2024_andhra_pradesh_urban_general")  # states: not by default
    assert not ms.stored_by_default("cpi_2012_all_india_item_egg")
    assert ms.stored_by_default("iip_2022_23_manufacturing") and not ms.stored_by_default(
        "iip_2022_23_manufacturing_food_products")
    assert ms.stored_by_default("wpi_2022_23_food_index") and not ms.stored_by_default("wpi_2022_23_primary__food")
    obs = gs.parse_eia_history(fx.eia_page(WEEKS[:1]), "x").observations[0]
    assert ms.describe(obs) == ms.series_info("brent_spot_usd_bbl")
    item = mospi.parse_mospi(fx.mospi_answer([fx.cpi_item(2025, "May", "Egg")]), "i").observations[0]
    entry = ms.describe(item)
    assert (entry.series_id, entry.frequency, entry.stored_by_default) == ("cpi_2012_all_india_item_egg", "monthly",
                                                                           False)
    assert "{dataset}" in entry.citation and entry.publisher.startswith("Ministry of Statistics")


def test_public_from_dispatches_to_each_sources_rules():
    brent = gs.parse_eia_history(fx.eia_page(WEEKS[:1]), "x").observations[0]
    later = dt.datetime(2028, 1, 15, tzinfo=UTC)
    assert ms.public_from(brent, seen_at=later).text == gs.eia_public_from(D(2027, 12, 27)).text
    cpi = mospi.parse_mospi(fx.mospi_answer([fx.cpi24(2026, "September", "Rural")]), "c").observations[0]
    assert ms.public_from(cpi, seen_at=NOW).text == "2026-11-12T16:00:00"
    bis = gs.parse_bis_csv(fx.cbpol_v1([("US", "2026-09-17", "3.875", "A")]), "b").observations[0]
    assert ms.public_from(bis, seen_at=NOW).text == "2026-09-18T09:29:59"


# --- The polite fetch -------------------------------------------------------------------------------------

class FakeTransport:
    """Answers from a list (or one answer for every URL); records every request and the order of events."""

    def __init__(self, *answers, events=None):
        self.answers = list(answers)
        self.requests = []
        self.events = events if events is not None else []

    def __call__(self, url, headers, timeout):
        self.requests.append((url, dict(headers), timeout))
        self.events.append(("send", url))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return answer(url) if callable(answer) else answer


class FakeSleep:
    def __init__(self):
        self.waits = []

    def __call__(self, seconds):
        self.waits.append(round(seconds, 6))


def ok(body, **headers):
    return FeedResponse(200, {"content-type": "application/json", **headers}, body)


MOSPI_BODY = fx.mospi_answer(fx.cpi24_month(2026, "October"))
CPI = mospi.cpi_2024_request(year=2026, month=10)
NYFED = gs.nyfed_search_request("secured", D(2026, 11, 8), D(2026, 11, 18))


def run(requests, *answers, **kwargs):
    transport = kwargs.pop("transport", None) or FakeTransport(*answers)
    sleep = kwargs.pop("sleep", None) or FakeSleep()
    report = ms.fetch(requests, now=kwargs.pop("now", NOW), transport=transport, sleep=sleep, **kwargs)
    return report, transport, sleep


def test_fetch_sends_a_generic_client_name_asks_for_gzip_and_decodes_it():
    body = fx.nyfed_json([fx.sofr_row("2026-11-17")])
    report, transport, _ = run([NYFED], ok(gzip.compress(body), **{"content-encoding": "gzip"}))
    (_url, headers, timeout), = transport.requests
    assert headers == {"User-Agent": "Mozilla/5.0 (compatible; data-client/1.0)", "Accept-Encoding": "gzip",
                       "Accept": "application/json"} and timeout == 60.0
    exchange, = report.answers()
    assert exchange.body == body and exchange.time == NOW and exchange.message.startswith("HTTP 200")
    assert ms.parse(exchange.request, exchange.body).values("sofr") == {"2026-11-17": "3.90"}
    state = report.hosts["markets.newyorkfed.org"]
    assert (state.last_request, state.last_success, state.day, state.day_requests) == (NOW, NOW, "2026-11-18", 1)
    assert report.exit_code == ms.EXIT_OK and report.log_rows()[0]["outcome"] == "ok"


def test_conditional_headers_go_only_to_servers_seen_to_honour_them():
    eia, h10 = gs.eia_brent_request(), gs.h10_history_request()
    validators = {r.url: ('W/"abc"', "Mon, 16 Nov 2026 21:15:09 GMT") for r in (CPI, NYFED, eia, h10)}
    report, transport, _ = run([CPI, NYFED, eia, h10], FeedResponse(304, {}, b""), validators=validators,
                               enabled={"mospi", "nyfed", "eia", "h10"})
    sent = {url: headers for url, headers, _ in transport.requests}
    assert sent[CPI.url]["If-None-Match"] == 'W/"abc"' and "If-Modified-Since" not in sent[CPI.url]
    assert "If-None-Match" not in sent[NYFED.url] and "If-Modified-Since" not in sent[NYFED.url]
    assert sent[eia.url]["If-None-Match"] == 'W/"abc"' and sent[eia.url]["If-Modified-Since"].startswith("Mon")
    assert "If-None-Match" not in sent[h10.url] and sent[h10.url]["If-Modified-Since"].startswith("Mon")
    assert {e.outcome for e in report.exchanges} == {"not_modified"}


def test_two_requests_to_one_host_are_spaced_by_its_interval_counted_on_the_runs_clock():
    second = mospi.cpi_2024_request(year=2026, month=9)
    report, transport, sleep = run([CPI, NYFED, second], ok(MOSPI_BODY))
    assert sleep.waits == [10.0] and len(transport.requests) == 3
    assert [e.time for e in report.exchanges] == [NOW, NOW, NOW + dt.timedelta(seconds=10)]
    previous = {"api.mospi.gov.in": ms.HostState("api.mospi.gov.in", last_request=NOW - dt.timedelta(seconds=3))}
    report, _, sleep = run([CPI], ok(MOSPI_BODY), hosts=previous)
    assert sleep.waits == [7.0] and report.exchanges[0].time == NOW + dt.timedelta(seconds=7)
    assert previous["api.mospi.gov.in"].day_requests == 0  # the caller's states are not changed


def test_the_cap_per_run_and_the_cap_per_india_day():
    requests = [CPI, NYFED, gs.h10_history_request()]
    report, transport, _ = run(requests, ok(MOSPI_BODY), max_requests=2)
    assert [e.outcome for e in report.exchanges] == ["ok", "ok", "run_cap"] and len(transport.requests) == 2
    full = {"api.mospi.gov.in": ms.HostState("api.mospi.gov.in", day="2026-11-18", day_requests=60)}
    report, transport, _ = run([CPI], ok(MOSPI_BODY), hosts=full)
    assert report.exchanges[0].outcome == "daily_cap" and transport.requests == []
    report, transport, _ = run([CPI], ok(MOSPI_BODY), hosts=full, now=dt.datetime(2026, 11, 18, 19, 0, tzinfo=UTC))
    assert report.exchanges[0].outcome == "ok" and report.hosts["api.mospi.gov.in"].day == "2026-11-19"


def test_a_block_cools_the_host_until_eight_the_next_india_day_and_a_second_stops_it():
    report, _, _ = run([NYFED], FeedResponse(403, {}, b"Forbidden"))
    state = report.hosts["markets.newyorkfed.org"]
    assert report.exchanges[0].outcome == "blocked" and report.exit_code == ms.EXIT_BLOCKED
    assert state.cooldown_until == dt.datetime(2026, 11, 19, 2, 30, tzinfo=UTC) and state.blocks == 1
    later = NOW + dt.timedelta(hours=10)
    report, transport, _ = run([NYFED], ok(b"{}"), hosts=report.hosts, now=later)
    assert report.exchanges[0].outcome == "cooling_down" and transport.requests == []
    report, _, _ = run([NYFED], FeedResponse(429, {}, b""), hosts=report.hosts,
                       now=dt.datetime(2026, 11, 19, 2, 30, tzinfo=UTC))
    state = report.hosts["markets.newyorkfed.org"]
    assert (state.blocks, state.stopped, state.cooldown_until) == (2, dt.datetime(2026, 11, 19, 2, 30, tzinfo=UTC),
                                                                    None)
    report, transport, _ = run([NYFED], ok(b"{}"), hosts=report.hosts, now=later + dt.timedelta(days=3))
    assert report.exchanges[0].outcome == "stopped" and transport.requests == []
    resumed, transport, _ = run([NYFED], FeedResponse(418, {}, b""), hosts=report.hosts,
                                now=later + dt.timedelta(days=3), resume=["markets.newyorkfed.org"])
    assert resumed.exchanges[0].outcome == "blocked" and resumed.hosts["markets.newyorkfed.org"].blocks == 3
    assert resumed.hosts["markets.newyorkfed.org"].stopped is not None  # stopped again at once


def test_a_block_near_midnight_counts_from_the_latest_time_the_answer_can_have_come():
    report, _, _ = run([NYFED], FeedResponse(403, {}, b""), now=dt.datetime(2026, 11, 18, 18, 29, 30, tzinfo=UTC))
    assert report.hosts["markets.newyorkfed.org"].cooldown_until == dt.datetime(2026, 11, 20, 2, 30, tzinfo=UTC)


@pytest.mark.parametrize("body, request_", [
    (fx.AKAMAI_PAGE, NYFED), (fx.AKAMAI_UNTITLED, NYFED), (fx.CHALLENGE_PAGE, gs.eia_brent_request()),
    (fx.CONTACT_PAGE, NYFED), (fx.CONTACT_TEXT, gs.h10_ddp_request(last_obs=5)),
])
def test_refusal_pages_and_demands_for_contact_details_are_blocks(body, request_):
    report, _, _ = run([request_], FeedResponse(200, {"content-type": "text/html"}, body), enabled={request_.source})
    exchange = report.exchanges[0]
    assert exchange.outcome == "blocked" and report.hosts[request_.host].blocks == 1
    if b"contact" in body.lower() or b"email" in body.lower():
        assert report.hosts[request_.host].contact_demand and "contact details" in report.attention[0]


def test_a_web_page_where_data_were_expected_is_an_error_once_and_a_block_twice():
    report, _, _ = run([NYFED], ok(fx.MAINTENANCE_PAGE))
    assert report.exchanges[0].outcome == "bad_answer" and report.hosts["markets.newyorkfed.org"].web_pages == 1
    again, _, _ = run([NYFED], ok(fx.MAINTENANCE_PAGE), hosts=report.hosts, now=NOW + dt.timedelta(hours=1))
    assert again.exchanges[0].outcome == "blocked"
    page = fx.eia_page(WEEKS[:1])  # a page is what EIA and H.10 send
    report, _, _ = run([gs.eia_brent_request()], ok(page), enabled={"eia"})
    assert report.exchanges[0].outcome == "ok"


def test_errors_stop_the_host_for_the_run_and_other_hosts_go_on():
    second = mospi.cpi_2024_request(year=2026, month=9)
    report, transport, _ = run([CPI, second, NYFED], FeedResponse(400, {}, fx.LIMIT_ERROR), ok(b"{}"))
    assert [e.outcome for e in report.exchanges] == ["client_error", "halted", "ok"]
    assert len(transport.requests) == 2 and "programming error" in report.exchanges[0].message
    assert report.attention and report.exit_code == ms.EXIT_ERROR
    for answer, outcome in ((FeedResponse(503, {}, b""), "server_error"),
                            (FeedResponse(None, error="URLError: timed out"), "network_error"),
                            (FeedResponse(301, {"location": "https://elsewhere.example/"}, b""), "http_error"),
                            (ok(b"\x1f\x8bnot gzip", **{"content-encoding": "gzip"}), "bad_answer"),
                            (ok(b"{}", **{"content-encoding": "br"}), "bad_answer")):
        report, transport, _ = run([NYFED, gs.nyfed_latest_request()], answer)
        assert [e.outcome for e in report.exchanges] == [outcome, "halted"], outcome
        assert len(transport.requests) == 1  # a redirect is not followed
        assert report.hosts["markets.newyorkfed.org"].errors == 1
    report, _, _ = run([NYFED], FeedResponse(301, {"location": "https://elsewhere.example/"}, b""))
    assert "not followed" in report.exchanges[0].message


def test_disabled_sources_duplicates_and_a_clock_that_moved_back_send_nothing():
    report, transport, _ = run([gs.ust_request(2026), gs.eia_brent_request(), NYFED, NYFED], ok(b"{}"))
    assert [e.outcome for e in report.exchanges] == ["disabled", "disabled", "ok", "duplicate"]
    assert len(transport.requests) == 1
    report, transport, _ = run([gs.ust_request(2026)], ok(fx.ust_csv([("11/17/2026", ["4.10"])], header=["1 Mo"])),
                               enabled={"ust"})
    assert report.exchanges[0].outcome == "ok"
    host = "markets.newyorkfed.org"
    future = {host: ms.HostState(host, last_request=NOW + dt.timedelta(hours=1))}
    report, transport, _ = run([NYFED], ok(b"{}"), hosts=future)
    assert report.exchanges[0].outcome == "clock_error" and transport.requests == []
    assert report.exit_code == ms.EXIT_ERROR


def test_bad_arguments_and_bad_requests_are_refused_before_anything_is_sent():
    transport = FakeTransport(ok(b"{}"))
    bad = ms.Request("nyfed", gs.NYFED_JSON, "https://www.rbi.org.in/x")
    for kwargs in (dict(requests=[bad]), dict(requests=[NYFED], user_agent="me@example.com"),
                   dict(requests=[NYFED], timeout=0), dict(requests=[NYFED], max_requests=0),
                   dict(requests=[NYFED], enabled={"fred"}), dict(requests=[NYFED], now=dt.datetime(2026, 11, 18)),
                   dict(requests=[ms.Request("mospi", gs.NYFED_JSON, NYFED.url)])):
        args = dict(now=NOW, transport=transport, sleep=FakeSleep()) | kwargs
        with pytest.raises(ValueError):
            ms.fetch(args.pop("requests"), **args)
    assert transport.requests == []


def test_the_attempt_is_recorded_before_the_request_is_sent():
    events = []
    transport = FakeTransport(ok(b"{}"), events=events)

    def record(host, state):
        events.append(("record", host, state.last_outcome, state.last_request))
    ms.fetch([NYFED], now=NOW, transport=transport, sleep=FakeSleep(), record=record)
    assert events == [("record", "markets.newyorkfed.org", "requesting", NOW), ("send", NYFED.url)]


def test_follow_pages_fetches_the_rest_of_a_mospi_query_politely():
    rows = fx.cpi24_month(2026, "October")

    def answer(url):
        page = int(url.split("page=")[1].split("&")[0])
        return ok(fx.mospi_answer(rows[:2] if page < 3 else rows[:1], page=page, per=2, total=5))
    request = mospi.cpi_2024_request(year=2026, month=10, limit=10)
    report, transport, sleep = run([request], answer, follow=ms.follow_pages)
    assert [u.split("page=")[1][:1] for u, _, _ in transport.requests] == ["1", "2", "3"]
    assert sleep.waits == [10.0, 10.0]
    pages = [ms.parse(e.request, e.body) for e in report.answers()]
    assert [p.meta["page"] for p in pages] == ["1", "2", "3"]


def test_host_states_round_trip_through_json_and_refuse_damaged_values():
    report, _, _ = run([NYFED], FeedResponse(403, {}, b""))
    state = report.hosts["markets.newyorkfed.org"]
    text = json.dumps(state.to_dict(), sort_keys=True)
    assert ms.HostState.from_dict(json.loads(text)) == state
    assert json.loads(text)["cooldown_until"] == "2026-11-19T02:30:00Z"
    for change, message in (({"blocks": -1}, "whole number"), ({"last_request": "yesterday"}, "is not a time"),
                            ({"surprise": 1}, "unknown keys"), ({"last_status": 999}, "HTTP status"),
                            ({"day": 5}, "must be text"), ({"host": ""}, "no host")):
        with pytest.raises(ValueError, match=message):
            ms.HostState.from_dict(state.to_dict() | change)


def test_the_request_log_has_one_row_per_request_sent():
    report, _, _ = run([NYFED, gs.ust_request(2026)], ok(b"{}", etag='"x"', **{"last-modified": "Mon"}))
    rows = report.log_rows()
    assert list(rows[0]) == list(ms.REQUEST_LOG_COLUMNS) and len(rows) == 1
    first = rows[0]
    assert (first["time"], first["host"], first["etag"], first["bytes"]) == ("2026-11-18T10:00:00Z",
                                                                             "markets.newyorkfed.org", '"x"', "2")
    attention = ms.FetchReport([], {}, ["look"])
    assert attention.exit_code == ms.EXIT_ATTENTION


# --- storekit ---------------------------------------------------------------------------------------------

class _Response(io.BytesIO):
    status = 200
    headers = {"Content-Type": "text/csv"}


class _Opener:
    def __init__(self, response):
        self.response = response

    def open(self, request, timeout):
        return self.response


def test_the_shared_transport_reads_at_most_the_cap_and_keeps_certificate_checks():
    answer = storekit.urllib_transport("https://stats.bis.org/x", {}, 5.0, opener=_Opener(_Response(b"0123456789")),
                                       max_bytes=4)
    assert answer.body == b"01234"  # one byte over the cap, so that a cut-off answer can be told
    context = storekit.legacy_renegotiation_context()
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert context.options & getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
    assert storekit.decode_body(gzip.compress(b"abc"), {"content-encoding": "gzip"}, 100) == b"abc"
    with pytest.raises(ValueError, match="larger than 100 bytes once decompressed"):
        storekit.decode_body(gzip.compress(b"a" * 1000), {"content-encoding": "gzip"}, 100)
    with pytest.raises(ValueError, match="larger than 10 bytes$"):
        storekit.decode_body(gzip.compress(b"abc"), {"content-encoding": "gzip"}, 10)
    assert storekit.resume_time(dt.datetime(2026, 11, 18, 18, 31, tzinfo=UTC)) == dt.datetime(
        2026, 11, 20, 8, 0, tzinfo=ut.IST)


def test_observations_are_plain_text_records():
    obs = Observation("eia", "brent_spot_usd_bbl", "2026-11-17", "day", "2026-11-17", "2026-11-17", "92.94",
                      "US dollars per barrel")
    assert obs.as_dict()["value"] == "92.94" and obs.key == ("brent_spot_usd_bbl", "2026-11-17")


def test_us_holidays_shift_releases_within_the_conservative_rules():
    # SOFR of Tuesday 10 Nov 2026 is published on the next business day, Thursday 12 Nov (Veterans Day between)
    body = fx.nyfed_json([fx.sofr_row("2026-11-10"), fx.sofr_row("2026-11-12")])
    dates = gs.nyfed_public_from_all(gs.parse_nyfed_json(body, "x").observations)
    assert dates[("sofr", "2026-11-10")].text == "2026-11-12T20:30:00"
    # EIA released on Thursday 12 Nov 2026 (Wednesday a holiday): Tuesday's price dated Thursday 23:59:59 New York
    assert gs.eia_public_from(D(2026, 11, 10)).text == "2026-11-13T10:29:59"
    # H.10 after Martin Luther King Day (Monday 19 Jan 2026) comes on Tuesday 16:15: dated a working day later, as
    # a Monday release is dated on the Tuesday
    assert gs.h10_public_from(D(2026, 1, 16)).text == "2026-01-22T02:45:00"


@pytest.mark.parametrize("rows, message", [
    ([fx.cpi_group(2025, "May", "Rural", "General", "General-Overall", base="2024")], "baseyear '2024'"),
    ([fx.rbi_fx("2025", "May", "Euro", "Average", "90.10") | {"indicator": "Foreign Exchange Reserves"}],
     "was not sampled"),
    ([fx.rbi_fx("2025", "May", "Euro", "Average", "90.10") | {"unit": "₹ crore"}], "the unit changed"),
    ([fx.rbi_fx("2025", "May", "Yuan", "Average", "12.00")], "was not sampled"),
    ([fx.iip(2026, "May", kind="Regional")], "is not General, Sectoral or Use-based category"),
    ([fx.iip(2026, "May", base="2022")], "is not a financial year"),
])
def test_other_mospi_rows_that_do_not_fit_are_refused(rows, message):
    with pytest.raises(SourceError, match=message):
        mospi.parse_mospi(fx.mospi_answer(rows, per=200), "m.json")


def test_mospi_answers_marked_as_failures_are_refused():
    body = json.dumps({"data": [], "msg": "Something failed", "statusCode": False}).encode()
    with pytest.raises(SourceError, match="an error answer"):
        mospi.parse_mospi(body, "fail.json")
    with pytest.raises(SourceError, match="no data and no 'No Data Found'"):
        mospi.parse_mospi(b'{"msg": "Data fetched successfully", "statusCode": true}', "odd.json")


def test_a_revision_seen_by_polling_is_dated_by_the_revising_release_of_that_day():
    # a 2025-26 Q2 value revised at the release of 31 Aug 2026 (actual, 16:00), seen at 16:20 India time that day
    seen = dt.datetime(2026, 8, 31, 10, 50, tzinfo=UTC)
    p = CAL.live_public_from("gdp", "2025-26Q2", "revised", seen, base="2022-23")
    assert (p.text, p.basis, p.release_id) == ("2026-08-31T16:00:00", "release_table", "gdp_2026-27Q1")
    later = CAL.live_public_from("gdp", "2025-26Q2", "revised", seen + dt.timedelta(days=2), base="2022-23")
    assert (later.basis, later.release_id) == ("first_seen", "gdp_2026-27Q1")


def test_a_value_is_never_dated_after_it_was_read():
    brent = gs.parse_eia_history(fx.eia_page([("2026 Nov-16 to Nov-20", ["92.90", "92.94", "", "", ""])]),
                                 "x").by_key()[("brent_spot_usd_bbl", "2026-11-17")]
    read = dt.datetime(2026, 11, 18, 18, 30, tzinfo=UTC)  # the evening of the Wednesday release
    p = ms.public_from(brent, seen_at=read)
    assert (p.text, p.basis) == ("2026-11-19T00:00:00", "first_seen") and "2026-11-20T10:29:59" in p.rule
    assert ms.public_from(brent, seen_at=read + dt.timedelta(days=3)).text == "2026-11-20T10:29:59"
    sofr = gs.parse_nyfed_json(fx.nyfed_json([fx.sofr_row("2026-11-17")]), "s").by_key()[("sofr", "2026-11-17")]
    latest = ms.public_from(sofr, seen_at=read)  # no next date: the sighting
    assert (latest.text, latest.basis) == ("2026-11-19T00:00:00", "first_seen")
    with pytest.raises(ValueError):
        ms.public_from(sofr, seen_at=dt.datetime(2026, 11, 18))


# --- Rules that must never date a value early: holidays, revisions, BIS's own figures --------------------------------

def test_us_federal_holidays_are_computed_from_the_laws_rules():
    assert sorted(gs.us_federal_holidays(2026)) == [  # 4 July 2026 is a Saturday: observed on Friday 3 July
        D(2026, 1, 1), D(2026, 1, 19), D(2026, 2, 16), D(2026, 5, 25), D(2026, 6, 19), D(2026, 7, 3),
        D(2026, 9, 7), D(2026, 10, 12), D(2026, 11, 11), D(2026, 11, 26), D(2026, 12, 25)]
    assert D(2025, 1, 20) in gs.us_federal_holidays(2025)  # Martin Luther King Day and Inauguration Day
    assert D(2021, 12, 31) in gs.us_federal_holidays(2021)  # New Year's Day 2022, a Saturday, observed on the Friday
    assert D(2020, 6, 19) not in gs.us_federal_holidays(2020) and D(2021, 6, 18) in gs.us_federal_holidays(2021)
    assert D(1983, 1, 17) not in gs.us_federal_holidays(1983)  # no Martin Luther King Day before 1986
    assert D(1985, 1, 21) in gs.us_federal_holidays(1985)  # Inauguration Day on a Sunday: the Monday
    assert gs.us_closed(D(2024, 12, 24)) and gs.us_closed(D(2025, 12, 26)) and not gs.us_closed(D(2024, 12, 26))


def test_eia_and_h10_releases_move_past_us_holidays_and_year_end_closures():
    # Christmas on a Wednesday, 24 Dec possibly off: the release of that week comes on the Friday (2019 and 2024, by
    # memory): the prices it carries are dated 23:59:59 New York time on Friday 27 Dec, not Thursday
    assert gs.eia_release_day(D(2024, 12, 18)) == D(2024, 12, 27)
    assert gs.eia_public_from(D(2024, 12, 18)).text == "2024-12-28T10:29:59"
    assert gs.eia_public_from(D(2019, 12, 24)).text == "2019-12-28T10:29:59"
    assert gs.eia_public_from(D(2024, 12, 10)).text == "2024-12-13T10:29:59"  # an ordinary week: Thursday
    assert gs.eia_release_day(D(2026, 9, 4)) == D(2026, 9, 10)  # Labor Day: Thursday
    # H.10: Christmas Eve (possibly off) and Christmas on Monday and Tuesday 2018: released Wednesday 26 Dec, dated
    # 16:15 New York time on the working day after it
    assert gs.h10_public_from(D(2018, 12, 21)).text == "2018-12-28T02:45:00"


def test_revised_new_york_fed_rows_are_never_dated_before_their_unrevised_rule():
    revised = gs.nyfed_public_from("EFFR", D(2005, 6, 9), D(2005, 6, 10), revised=True)
    plain = gs.nyfed_public_from("EFFR", D(2005, 6, 9), D(2005, 6, 10))
    assert revised.text == plain.text == "2005-06-11T09:29:59"  # 23:59:59 on the next date, revised or not
    assert gs.nyfed_public_from("SOFRAI", D(2026, 11, 18), None, revised=True).text == "2026-11-19T03:30:00"  # 17:00
    assert gs.nyfed_public_from("SOFRAI", D(2026, 11, 18), None).text == "2026-11-18T20:30:00"  # 10:00, unrevised
    assert gs.nyfed_public_from("EFFR", D(2017, 5, 31), D(2017, 6, 1), revised=True).text == "2017-06-02T02:30:00"


def test_bis_rupee_figures_wait_for_bis_s_weekly_release_with_a_week_of_margin():
    # releases on Thursdays with data to the Tuesday before (12 Nov 2026 carried 10 Nov): one more week of margin
    assert gs.bis_fx_release_day(D(2026, 11, 10)) == D(2026, 11, 19)
    assert gs.bis_fx_release_day(D(2026, 11, 11)) == D(2026, 11, 26)  # a Wednesday's figure missed the 12th
    assert gs.bis_fx_release_day(D(2026, 12, 22)) == D(2027, 1, 7)  # no release from 23 Dec to 7 Jan: two weeks
    assert gs.bis_fx_public_from(D(2026, 7, 7)).text == "2026-07-17T04:29:59"  # UTC+1 all year: an hour late in summer
    xru = gs.parse_bis_csv(fx.xru_v1([("2026-11-10", "93.928571", "A")]), "x").observations[0]
    cbpol = gs.parse_bis_csv(fx.cbpol_v1([("IN", "2026-10-07", "5.5", "A")]), "c").observations[0]
    assert gs.public_when_published(xru) and not gs.public_when_published(cbpol)  # the policy rate: announced


def test_mospi_next_working_day_rules_skip_holidays_but_previous_working_day_rules_do_not():
    cal = mospi.ReleaseCalendar.load()
    assert mospi.next_working_day(D(2022, 8, 14)) == D(2022, 8, 17)  # Sunday, Independence Day, Parsi New Year
    assert cal.release("wpi", "2022-07").day == D(2022, 8, 17)  # the final of May 2022 came with it
    assert cal.release("cpi", "2019-10").day == D(2019, 11, 13)  # 12 Nov 2019, Guru Nanak Jayanti
    planned = cal.release("cpi", "2025-03")  # planned for 14 Apr 2025, Ambedkar Jayanti
    assert (planned.day, planned.basis) == (D(2025, 4, 15), "release_table_planned") and "holiday" in planned.rule
    assert cal.release("cpi", "2026-08").day == D(2026, 9, 14)  # an actual date is kept, a Mumbai holiday or not
    assert cal.release("iip", "2019-06").day == D(2019, 8, 12)  # previous working day: a holiday leaves it later
    assert mospi.next_working_day(D(2027, 3, 12)) == D(2027, 3, 12)  # beyond the list's years: weekends only


@pytest.mark.parametrize("body, parser, message", [
    (fx.h10_page([("12-NOV-26", "94.0400"), ("13-NOV-26", "94.2400")])[:-30], gs.parse_h10_history, "cut short"),
    (fx.eia_page([("2026 Nov- 9 to Nov-13", ["91.03", "90.16", "92.14", "", "95.17"])])[:-40],
     gs.parse_eia_history, "cut short"),
    (fx.ust_csv([("11/16/2027", ["4.10", "4.1"])], header=["1 Mo", "2 Mo"]), gs.parse_ust_csv, "two decimals"),
    (fx.xru_v1([("2026-11-10", "93.928571", "A")])[:-1], gs.parse_bis_csv, "no newline at the end"),
    (fx.xru_v1([("2026-11-10", "93.928571", "A")]).replace(b",A,,F\n", b",A\n"), gs.parse_bis_csv,
     "cells do not match"),
    (fx.xru_v1([("2026-11-10", "93.", "A")]), gs.parse_bis_csv, "is not a number"),
    (fx.ddp_csv([("2026-11-10", "1.1500", "93.9300")])[:-12] + b'"\n', gs.parse_h10_ddp_csv, "cells under a header"),
])
def test_answers_cut_short_are_refused_rather_than_read_in_part(body, parser, message):
    with pytest.raises(SourceError, match=message):
        parser(body, "cut")


@pytest.mark.parametrize("status", [200, 401, 404, 503])
def test_a_refusal_page_is_a_block_whatever_its_status(status):
    for body in (fx.CHALLENGE_PAGE, fx.AKAMAI_PAGE):
        report, _, _ = run([NYFED], FeedResponse(status, {"content-type": "text/html"}, body))
        assert report.exchanges[0].outcome == "blocked" and report.exit_code == ms.EXIT_BLOCKED, status
        assert report.hosts["markets.newyorkfed.org"].cooldown_until == dt.datetime(2026, 11, 19, 2, 30, tzinfo=UTC)
    plain, _, _ = run([NYFED], FeedResponse(503, {}, b""))
    assert plain.exchanges[0].outcome == "server_error"  # an error page that refuses nothing is not a block


def test_the_latest_time_an_answer_can_have_come_counts_every_request_of_the_run():
    report, _, _ = run([NYFED], ok(fx.nyfed_json([fx.sofr_row("2026-11-17")])), sent_before=4)
    assert report.exchanges[0].latest == NOW + dt.timedelta(seconds=60 * 5)  # four earlier requests and this one
    # a run that began at 23:55 India time and blocked after 24 requests of 60 s: the block may have come after
    # midnight, so no request before 08:00 India time the day after that
    late = dt.datetime(2026, 11, 18, 18, 28, 50, tzinfo=UTC)
    report, _, _ = run([NYFED], FeedResponse(429, {}, b""), now=late, sent_before=23)
    assert report.hosts["markets.newyorkfed.org"].cooldown_until == dt.datetime(2026, 11, 20, 2, 30, tzinfo=UTC)
    with pytest.raises(ValueError, match="sent_before"):
        run([NYFED], ok(b"{}"), sent_before=-1)
