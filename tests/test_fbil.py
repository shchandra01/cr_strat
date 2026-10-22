import datetime as dt
import json

import numpy as np
import openpyxl
import pandas as pd
import pytest

from cr_strat.curves import BenchmarkCurve, convert_yield
from cr_strat.fbil import (
    GSEC_COLUMNS,
    MM_COLUMNS,
    combine_curves,
    excel_engine,
    load_fbil_gsec_curve,
    load_fbil_money_market,
    tenor_years,
)

TENORS = [0.25, 0.5, 0.75, 1.0, 2.0, 5.0]
SEMI = [5.40, 5.90, 6.10, 6.15, 6.40, 6.80]
PAR_HEADER = ["Tenor (Year)", "YTM% p.a.(Semi-Annual)", "YTM % p.a.(Annualized)"]


def _save(path, sheets: dict[str, list[list]]):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    wb.save(path)
    return path


def _block(tenors=TENORS, semi=SEMI, scale=1.0):
    annual = np.round(convert_yield(np.array(semi), "semiannual", "annual"), 2)
    return [[t, s * scale, a * scale] for t, s, a in zip(tenors, semi, annual)]


def _par_sheet(date_cell="06-Oct-2026", header=PAR_HEADER, block=None):
    return [
        ["Financial Benchmarks India Private Ltd "],
        [],
        ["FBIL GSec Base/Par Yield", None, date_cell],
        [],
        header,
        *(block if block is not None else _block()),
    ]


def test_par_2026_layout_title_date_wins_over_file_name(tmp_path):
    # Recent files are OOXML saved under .xls, sheet 'Par Yield', header on row 5.
    path = _save(tmp_path / "gsec_20261005.xls", {
        "G-Sec": [["ISIN", "Security", "Price"]],
        "Par Yield": _par_sheet(),
        "Special": [["Special securities"]],
    })
    df = load_fbil_gsec_curve(path)
    assert list(df.columns) == list(GSEC_COLUMNS)
    assert (df["date"] == pd.Timestamp("2026-10-06")).all()
    assert df["tenor_years"].tolist() == TENORS
    assert df["yield_pct"].tolist() == pytest.approx(SEMI)
    assert set(df["basis"]) == {"semiannual"} and set(df["source_file"]) == {"gsec_20261005.xls"}
    assert df["yield_annualized_pct"].tolist() == pytest.approx(convert_yield(SEMI, "semiannual", "annual"), abs=0.006)


def test_par_2018_layout_decimals_and_sdl_block(tmp_path):
    sdl = [[t, s / 100 + 0.006, s / 100 + 0.0065] for t, s in zip(TENORS, SEMI)]
    rows = [
        [],
        [],
        ["FBIL Base/Par Yield as on March 28, 2018                 ((Month/Quarter/Year-End)"],
        [],
        [],
        ["Maturity", "YTM                      (Semi-Annual)", "YTM (Annualized)"],
        *_block(scale=0.01),
        [],
        ["State Government Yield Curve as on March 28, 2018 ((Month/Quarter/Year-End)"],
        ["Maturity", "YTM (Semi - Annualized)", "YTM (Annualized)"],
        *sdl,
    ]
    path = _save(tmp_path / "gsec_20180331.xls", {"G-Sec": [[]], "Par-Yield": rows, "SDL": [[]], "ZCYC": [[]]})
    df = load_fbil_gsec_curve(path)
    assert (df["date"] == pd.Timestamp("2018-03-28")).all()  # listed as 03-31, valued as of 03-28
    assert df["tenor_years"].tolist() == TENORS  # SDL block not appended
    assert df["yield_pct"].tolist() == pytest.approx(SEMI)
    assert df["yield_annualized_pct"].iloc[0] > 5


@pytest.mark.parametrize("cell, expected", [
    ("15 Jul, 2019", "2019-07-15"),
    ("30 Jun, 2022 (Month-End)", "2022-06-30"),
    ("FBIL - GOI Prices as on June 30, 2022 (Month-End)", "2022-06-30"),
    (dt.datetime(2026, 10, 6), "2026-10-06"),
])
def test_title_date_formats(tmp_path, cell, expected):
    path = _save(tmp_path / "gsec_20000101.xls", {"Par-Yield": _par_sheet(date_cell=cell)})
    assert load_fbil_gsec_curve(path)["date"].iat[0] == pd.Timestamp(expected)


def test_zcyc_sheet_of_strips_workbook(tmp_path):
    header = ["Tenor (Year)", "Zero Coupon%  (Semi-annual)", "Zero Coupon %          (Annualized)"]
    path = _save(tmp_path / "strips_20261006.xls", {
        "STRIPS": [["ISIN", "Coupon STRIPS", "Maturity", "Price", "Yield% (Semi-Annual)"]],
        "ZCYC": _par_sheet(header=header),
    })
    df = load_fbil_gsec_curve(path, kind="zcyc")
    assert len(df) == len(TENORS) and df["yield_pct"].iloc[-1] == pytest.approx(SEMI[-1])
    with pytest.raises(ValueError, match="kind='zcyc'"):
        load_fbil_gsec_curve(path, kind="par")
    with pytest.raises(ValueError, match="kind must be"):
        load_fbil_gsec_curve(path, kind="sdl")


def test_block_ends_at_non_increasing_tenor(tmp_path):
    block = _block() + [[0.25, 9.0, 9.2], [0.5, 9.1, 9.3]]
    path = _save(tmp_path / "gsec_20261006.xls", {"Par Yield": _par_sheet(block=block)})
    assert load_fbil_gsec_curve(path)["tenor_years"].tolist() == TENORS


def test_missing_annualized_column(tmp_path):
    block = [[t, s] for t, s in zip(TENORS, SEMI)]
    path = _save(tmp_path / "g.xls", {"Par Yield": _par_sheet(header=PAR_HEADER[:2], block=block)})
    df = load_fbil_gsec_curve(path)
    assert df["yield_annualized_pct"].isna().all() and df["yield_pct"].notna().all()


def test_date_falls_back_to_file_name(tmp_path):
    path = _save(tmp_path / "fbil_par_2024-01-15.xls", {"Par Yield": _par_sheet(date_cell=None)})
    assert load_fbil_gsec_curve(path)["date"].iat[0] == pd.Timestamp("2024-01-15")
    undated = _save(tmp_path / "par.xls", {"Par Yield": _par_sheet(date_cell=None)})
    with pytest.raises(ValueError, match="no valuation date"):
        load_fbil_gsec_curve(undated)


def test_excel_engine_sniffing(tmp_path):
    assert excel_engine(b"PK\x03\x04\x14\x00") == "openpyxl"
    assert excel_engine(bytes.fromhex("d0cf11e0a1b11ae1") + b"\x00" * 8) == "xlrd"
    with pytest.raises(ValueError, match="not an Excel workbook"):
        excel_engine(b"<!DOCTYPE html>")
    error_page = tmp_path / "gsec_20261011.xls"
    error_page.write_text('{"status": 500, "error": "Server Error"}')
    with pytest.raises(ValueError, match="gsec_20261011.xls: not an Excel workbook"):
        load_fbil_gsec_curve(error_page)


def test_gsec_curve_feeds_benchmark_curve(tmp_path):
    path = _save(tmp_path / "g.xls", {"Par Yield": _par_sheet()})
    df = load_fbil_gsec_curve(path)
    curve = BenchmarkCurve(df)  # semi-annual, matching the basis column
    got = curve.yields(pd.Series(pd.to_datetime(["2026-10-07"])), [5.0])
    assert got[0] == pytest.approx(df["yield_annualized_pct"].iloc[-1], abs=0.006)
    with pytest.raises(ValueError, match="basis"):
        BenchmarkCurve(df, compounding="annual")


def test_combine_curves_keeps_later_file():
    a = pd.DataFrame({"date": pd.to_datetime(["2026-10-05", "2026-10-06"]), "tenor_years": [1.0, 1.0],
                      "yield_pct": [6.0, 6.1]})
    b = pd.DataFrame({"date": pd.to_datetime(["2026-10-06"]), "tenor_years": [1.0], "yield_pct": [6.2]})
    out = combine_curves([b.iloc[:0], a, b])
    assert out["yield_pct"].tolist() == [6.0, 6.2]
    assert combine_curves([]).empty


@pytest.mark.parametrize("name, years", [
    ("7 Days", 7 / 365), ("14 Days", 14 / 365), ("1 Month", 1 / 12), ("2 Months", 2 / 12),
    ("12 Months", 1.0), ("1M", 1 / 12), ("9M", 0.75), ("1Y", 1.0), ("5Y", 5.0), (" 3 months ", 0.25),
])
def test_tenor_years(name, years):
    assert tenor_years(name) == pytest.approx(years)


def test_tenor_years_unknown():
    assert np.isnan(tenor_years("O/N")) and np.isnan(tenor_years(""))


def _rows(tenor_rates, date="2026-10-06", time="17:45:00", comments=" "):
    return [{"processRunDate": f"{date} 00:00:00", "displayTime": f"{date} {time}", "tenorName": name,
             "rate": rate, "comments": comments} for name, rate in tenor_rates]


def test_miborois_conventions_to_annual():
    df = load_fbil_money_market(_rows([("1M", 5.54), ("6M", 5.90), ("1Y", 6.25), ("5Y", 6.70)]), "miborois")
    assert list(df.columns) == list(MM_COLUMNS)
    assert df["tenor_years"].tolist() == pytest.approx([1 / 12, 0.5, 1.0, 5.0])
    assert df["quote_convention"].tolist() == ["money_market"] * 3 + ["semiannual"]
    one_month = ((1 + 0.0554 * (365 / 12) / 365) ** 12 - 1) * 100
    six_month = ((1 + 0.059 / 2) ** 2 - 1) * 100
    five_year = ((1 + 0.067 / 2) ** 2 - 1) * 100
    assert df["yield_pct"].tolist() == pytest.approx([one_month, six_month, 6.25, five_year])
    assert df["rate_quoted_pct"].tolist() == [5.54, 5.90, 6.25, 6.70]
    assert set(df["basis"]) == {"annual"} and (df["date"] == pd.Timestamp("2026-10-06")).all()


def test_tbill_inputs_path_text_and_list(tmp_path):
    rows = _rows([("7 Days", 5.22), ("3 Months", 5.50), ("12 Months", 6.13)], time="17:30:00")
    path = tmp_path / "tbill.json"
    path.write_text(json.dumps(rows))
    from_path = load_fbil_money_market(path, "tbill")
    assert set(from_path["source_file"]) == {"tbill.json"}
    pd.testing.assert_frame_equal(from_path.drop(columns="source_file"),
                                  load_fbil_money_market(json.dumps(rows), "tbill").drop(columns="source_file"))
    df = load_fbil_money_market(rows, "tbill")
    week = ((1 + 0.0522 * 7 / 365) ** (365 / 7) - 1) * 100
    assert df["yield_pct"].tolist() == pytest.approx([week, ((1 + 0.055 / 4) ** 4 - 1) * 100, 6.13], abs=1e-3)
    assert set(df["quote_convention"]) == {"money_market"}


def test_money_market_duplicates_and_blanks():
    rows = _rows([("1Y", 6.20), ("2Y", 6.40)], date="2026-10-05") + _rows([("1Y", 6.25), ("1Y", 6.26), ("2Y", None)])
    df = load_fbil_money_market(rows, "miborois")
    assert df[["date", "tenor_name"]].astype(str).values.tolist() == [
        ["2026-10-05", "1Y"], ["2026-10-05", "2Y"], ["2026-10-06", "1Y"]]
    assert df["rate_quoted_pct"].iloc[-1] == 6.26
    assert load_fbil_money_market([], "tbill").empty


def test_money_market_errors(tmp_path):
    with pytest.raises(ValueError, match="unrecognised FBIL tenor"):
        load_fbil_money_market(_rows([("O/N", 5.2)]), "miborois")
    with pytest.raises(ValueError, match="beyond 1 year"):
        load_fbil_money_market(_rows([("1Y", 6.2), ("2Y", 6.4)]), "tbill")
    with pytest.raises(ValueError, match="kind must be"):
        load_fbil_money_market([], "mibor")
    with pytest.raises(ValueError, match="list of FBIL rate rows"):
        load_fbil_money_market('{"status": 500}', "tbill")
    with pytest.raises(ValueError, match="lack"):
        load_fbil_money_market([{"processRunDate": "2026-10-06", "rate": 5.0}], "tbill")


def test_ois_curve_with_extrapolation_limit():
    df = load_fbil_money_market(_rows([("1Y", 6.25), ("3Y", 6.55), ("5Y", 6.70)]), "miborois")
    with pytest.raises(ValueError, match="basis"):
        BenchmarkCurve(df)
    curve = BenchmarkCurve(df, compounding="annual", max_extrapolation_years=1)
    got = curve.yields(pd.Series(pd.to_datetime(["2026-10-06"] * 3)), [5.0, 6.0, 10.0])
    assert got[0] == pytest.approx(got[1]) and np.isnan(got[2])
