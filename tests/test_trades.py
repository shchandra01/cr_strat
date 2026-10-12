import openpyxl
import pandas as pd
import pytest

from cr_strat.trades import clean_trades, isin_daily, load_trades, read_trades


@pytest.fixture
def trade_csv(tmp_path, isin):
    a, b = isin("INE100A0701"), isin("INE200B0801")
    lines = [
        "Corporate Bonds - Reported Trades",
        "Report date: 02-Mar-2026",
        "",
        "Trade Date,ISIN,Description,Maturity Date,Deal Size (Rs. Lakhs),Price,Yield,Settlement Date",
        f"02-Mar-2026,{a},8.5% A FIN 2029,15-Jun-2029,500,101.25,8.0100,03-Mar-2026",
        f"02-Mar-2026,{a},8.5% A FIN 2029,15-Jun-2029,\"1,500\",101.20,8.0300,03-Mar-2026",
        f"02-Mar-2026,{b},7.9% B HFC 2027,10-Jan-2027,2500,99.90,8.4500,03-Mar-2026",
        "02-Mar-2026,INE000000000,BAD ISIN,,100,100,8,03-Mar-2026",
        f"02-Mar-2026,{b},7.9% B HFC 2027,10-Jan-2027,100,0,,03-Mar-2026",
    ]
    path = tmp_path / "nse_trades.csv"
    path.write_text("\n".join(lines))
    return path, a, b


def test_load_trade_level_csv(trade_csv):
    path, a, b = trade_csv
    trades, report = load_trades(path, "nse")
    assert len(trades) == 3
    assert set(trades["venue"]) == {"NSE"}
    assert (trades["trade_date"] == pd.Timestamp("2026-03-02")).all()
    assert trades["amount_inr"].tolist() == pytest.approx([500e5, 1500e5, 2500e5])
    assert trades.loc[trades["isin"] == b, "yield_pct"].item() == pytest.approx(8.45)
    row = report.iloc[0]
    assert row["rows_read"] == 4 and row["rows_kept"] == 3 and row["dropped_bad_value"] == 1
    assert "amount=Deal Size (Rs. Lakhs)" in row["columns"]


def test_load_daily_summary_workbook(tmp_path, isin):
    a = isin("INE100A0701")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["ISIN", "Security", "No. of Trades", "Total Traded Value (Rs. Cr)", "Last Traded Price",
               "Weighted Average Price", "Last Traded Yield", "Weighted Average Yield"])
    ws.append([a, "A FIN", 3, 45.0, 101.0, 101.1, 8.05, 8.02])
    path = tmp_path / "bse_20260302.xlsx"
    wb.save(path)
    trades, report = load_trades(path, "BSE", trade_date="2026-03-02")
    row = trades.iloc[0]
    assert (row["price"], row["yield_pct"], row["n_trades"]) == (101.1, 8.02, 3)
    assert row["amount_inr"] == pytest.approx(45e7)
    assert row["trade_date"] == pd.Timestamp("2026-03-02")
    trades, report = load_trades(path, "BSE")
    assert trades.empty and "pass --trade-date" in report.iloc[0]["note"]


def test_clean_and_aggregate(trade_csv, tmp_path):
    path, a, b = trade_csv
    trades, _ = load_trades(path, "NSE")
    extra = trades.iloc[[0]].assign(yield_pct=9.5, amount_inr=1e7)  # far off the ISIN-day median
    trades = pd.concat([trades, extra], ignore_index=True)
    trades.to_csv(tmp_path / "t.csv", index=False, date_format="%Y-%m-%d")
    cleaned = clean_trades(read_trades(tmp_path / "t.csv"))
    assert cleaned["outlier"].sum() == 1
    daily = isin_daily(cleaned).set_index("isin")
    assert daily.loc[a, "yield_pct"] == pytest.approx((8.01 * 500 + 8.03 * 1500) / 2000)
    assert daily.loc[a, "volume_inr"] == pytest.approx(2000e5)
    assert daily.loc[a, "n_trades"] == 2
    assert len(clean_trades(trades, min_amount_inr=1e8)) == 2


def test_cross_venue_dedupe(trade_csv):
    path, *_ = trade_csv
    nse, _ = load_trades(path, "NSE")
    bse, _ = load_trades(path, "BSE")
    both = pd.concat([nse, bse], ignore_index=True)
    assert len(clean_trades(both)) == 6
    assert len(clean_trades(both, dedupe_across_venues=True)) == 3


def test_header_without_rows_and_ragged_rows(tmp_path, isin):
    a = isin("INE100A0701")
    empty = tmp_path / "empty.csv"
    empty.write_text("Trade Date,ISIN,Price,Yield,Value (Rs. Cr)\n")
    trades, report = load_trades(empty, "NSE")
    assert trades.empty and report.iloc[0]["status"] == "empty"
    ragged = tmp_path / "ragged.csv"
    ragged.write_text(f"Trade Date,ISIN,Price,Yield,Value (Rs. Cr)\n02-03-2026,{a},101,8.1\n")
    trades, _ = load_trades(ragged, "NSE")
    assert trades["yield_pct"].item() == 8.1 and pd.isna(trades["amount_inr"].item())
