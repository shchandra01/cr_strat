from pathlib import Path

import openpyxl
import pandas as pd
import pytest

from cr_strat.cli import main

CONFIG = Path(__file__).resolve().parents[1] / "config" / "mandates.toml"


def test_end_to_end(tmp_path, isin):
    bond = isin("INE111A0701")
    pd.DataFrame([
        {"date": "2026-01-05", "issuer_id": "X1", "agency": "CRISIL", "instrument": "NCD", "rating": "CRISIL AA+/Stable"},
        {"date": "2026-01-06", "issuer_id": "X1", "agency": "ICRA", "instrument": "NCD", "rating": "[ICRA]AA+ (Stable)"},
        {"date": "2026-09-20", "issuer_id": "X1", "agency": "CRISIL", "instrument": "NCD", "rating": "CRISIL AA/Negative"},
    ]).to_csv(tmp_path / "actions.csv", index=False)
    pd.DataFrame([{"isin": bond, "issuer_id": "X1", "instrument_class": "BOND", "face_value_inr": 1e5,
                   "outstanding_face_inr": 5e9}]).to_csv(tmp_path / "securities.csv", index=False)
    (tmp_path / "NAVAll.txt").write_text(
        "Open Ended Schemes(Debt Scheme - Corporate Bond Fund)\n\nABC Mutual Fund\n\n"
        "1;INF000A01011;-;ABC Corporate Bond Fund - Direct Plan - Growth;30.1;10-Oct-2026\n"
    )
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Scheme Name: ABC Corporate Bond Fund"])
    ws.append(["Portfolio as on 15-Sep-2026"])
    ws.append(["Name of the Instrument", "ISIN", "Rating", "Quantity", "Market Value (Rs. in Lakhs)", "% to Net Assets"])
    ws.append(["X1 NCD", bond, "CRISIL AA+", 1000, 1000.0, 5.0])
    wb.save(tmp_path / "abc.xlsx")

    out = tmp_path / "out"
    assert main(["rating-events", "--actions", str(tmp_path / "actions.csv"), "--mandates", str(CONFIG),
                 "--out-dir", str(out)]) == 0
    assert main(["parse-mf", str(tmp_path / "abc.xlsx"), "--amc", "ABC Mutual Fund", "--out", str(out / "holdings.csv"),
                 "--navall", str(tmp_path / "NAVAll.txt")]) == 0
    assert main(["ownership", "--events", str(out / "rating_events.csv"), "--holdings", str(out / "holdings.csv"),
                 "--securities", str(tmp_path / "securities.csv"), "--mandates", str(CONFIG),
                 "--out", str(out / "event_ownership.csv")]) == 0

    result = pd.read_csv(out / "event_ownership.csv")
    cbf = result[result["mandate"] == "mf_corporate_bond_fund"].iloc[0]
    assert cbf["constrained_share"] == 1000 * 1e5 / 5e9


def test_price_pipeline(tmp_path, isin):
    t = pd.Timestamp("2025-06-02")
    issuers = {"E": isin("INE500E0701"), "P1": isin("INE501P0701"), "P2": isin("INE502P0701"), "P3": isin("INE503P0701")}
    actions = [{"date": "2025-01-01", "issuer_id": k, "rating": "CRISIL AA+/Stable", "instrument": "NCD"} for k in issuers]
    actions.append({"date": "2025-06-02", "issuer_id": "E", "rating": "CRISIL AA/Negative", "instrument": "NCD"})
    pd.DataFrame(actions).to_csv(tmp_path / "actions.csv", index=False)
    pd.DataFrame([{"isin": v, "issuer_id": k, "instrument_class": "BOND", "maturity_date": "2027-06-30"}
                  for k, v in issuers.items()]).to_csv(tmp_path / "securities.csv", index=False)
    days = pd.date_range("2025-03-01", "2025-09-30", freq="D")
    pd.DataFrame([{"date": d.strftime("%Y-%m-%d"), "tenor_years": tenor, "yield_pct": y}
                  for d in days for tenor, y in ((1, 6.0), (5, 6.4))]).to_csv(tmp_path / "gsec.csv", index=False)

    lines = ["Trade Date,ISIN,Description,Deal Size (Rs. Lakhs),Price,Yield,Settlement Date"]
    for offset in (-20, -5, 2, 10, 60):
        d = t + pd.Timedelta(days=offset)
        for k, v in issuers.items():
            y = 8.0 if k != "E" else (8.2 if offset < 0 else 9.2)
            lines.append(f"{d:%d-%b-%Y},{v},{k} NCD,1000,100,{y},{d:%d-%b-%Y}")
    (tmp_path / "nse.csv").write_text("\n".join(lines))

    out = tmp_path / "out"
    assert main(["rating-events", "--actions", str(tmp_path / "actions.csv"), "--mandates", str(CONFIG),
                 "--out-dir", str(out)]) == 0
    assert main(["load-trades", str(tmp_path / "nse.csv"), "--venue", "NSE", "--out", str(out / "trades.csv")]) == 0
    assert main(["price-panel", "--trades", str(out / "trades.csv"), "--securities", str(tmp_path / "securities.csv"),
                 "--benchmark", str(tmp_path / "gsec.csv"), "--rating-actions", str(tmp_path / "actions.csv"),
                 "--out-dir", str(out)]) == 0
    assert main(["event-prices", "--events", str(out / "rating_events.csv"), "--panel", str(out / "price_panel.csv"),
                 "--out-dir", str(out)]) == 0

    events = pd.read_csv(out / "event_prices.csv")
    cbf = events[events["mandate"] == "mf_corporate_bond_fund"].iloc[0]
    assert cbf["issuer_id"] == "E" and cbf["n_bonds"] == 1
    assert cbf["first_post_abn_bps"] == pytest.approx(100, abs=1)
    assert pd.isna(cbf["p60_gap_cur_bps"])  # no AA-rated peers, so no current-rating benchmark
    assert (out / "peer_spreads_weekly.csv").exists()


def test_synthetic_market_through_backtest_and_research(tmp_path):
    raw, out = tmp_path / "raw", tmp_path / "out"
    assert main(["synth", "--out-dir", str(raw), "--issuers", "40", "--start", "2021-01-01", "--end", "2022-06-30",
                 "--seed", "3"]) == 0
    assert main(["rating-events", "--actions", str(raw / "rating_actions.csv"), "--mandates", str(CONFIG),
                 "--out-dir", str(out)]) == 0
    assert main(["load-trades", *map(str, sorted((raw / "trades" / "nse").glob("*.csv"))), "--venue", "NSE",
                 "--out", str(out / "trades.csv")]) == 0
    assert main(["price-panel", "--trades", str(out / "trades.csv"), "--securities", str(raw / "securities.csv"),
                 "--benchmark", str(raw / "gsec_par_curve.csv"), "--rating-actions", str(raw / "rating_actions.csv"),
                 "--out-dir", str(out)]) == 0
    market = ["--panel", str(out / "price_panel.csv"), "--securities", str(raw / "securities.csv"),
              "--benchmark", str(raw / "gsec_par_curve.csv"), "--rating-actions", str(raw / "rating_actions.csv")]
    config = str(CONFIG.parent / "backtest_carry.toml")
    assert main(["backtest", "--config", config, *market, "--events", str(out / "rating_events.csv"),
                 "--set", "strategy.n_bonds=5", "--set", "benchmark.n_bonds=10", "--out-dir", str(out / "bt"),
                 "--synthetic"]) == 0
    nav = pd.read_csv(out / "bt" / "nav.csv")
    parts = ["pnl_income", "pnl_roll", "pnl_rates", "pnl_spread", "pnl_execution", "pnl_cash"]
    assert (nav["pnl"] - nav[parts].sum(axis=1)).abs().max() < 1e-3
    # Sells wait for a trade in the bond, so the book can hold more names than the target while exiting.
    assert 5 <= nav["n_positions"].max() <= 10 and len(pd.read_csv(out / "bt" / "fills.csv")) > 5
    html = (out / "bt" / "tearsheet.html").read_text()
    assert "Synthetic data" in html and "Growth of 100" in html
    summary = pd.read_csv(out / "bt" / "summary.csv").set_index("metric")
    assert {"sharpe", "information_ratio", "turnover_annual", "max_drawdown"} <= set(summary.index)

    assert main(["signals", *market, "--out-dir", str(out / "sig"), "--horizons", "20,60",
                 "--signals", "rv_bps,spread_bps"]) == 0
    sig = pd.read_csv(out / "sig" / "signal_summary.csv")
    assert set(sig["signal"]) == {"rv_bps", "spread_bps"}
    assert sig.loc[sig["signal"] == "spread_bps", "n_dates"].min() > 20  # few rv_bps dates: thin peer groups

    assert main(["sweep", "--config", config, *market, "--grid", "strategy.n_bonds=3,6",
                 "--split-date", "2021-12-31", "--out", str(out / "sweep.csv")]) == 0
    assert len(pd.read_csv(out / "sweep.csv")) == 2

    assert main(["regress", "--data", str(out / "sig" / "signal_panel.csv"), "--y", "xr_60d",
                 "--x", "rv_bps,spread_bps", "--cluster", "date", "--out", str(out / "reg.csv")]) == 0
    assert pd.read_csv(out / "reg.csv")["term"].tolist() == ["const", "rv_bps", "spread_bps"]
