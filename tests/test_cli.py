import re
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


def _fields(text: str) -> dict:
    rows = [re.split(r"\s{2,}", line.strip(), maxsplit=1) for line in text.splitlines() if line.strip()]
    return {r[0]: r[1] for r in rows if len(r) == 2}


def test_bond_price(capsys):
    # 6.94% GS 2036, RBI auction of 13 Nov 2026: cut-off price 97.43, yield 7.3203%, settled 16 Nov.
    args = ["bond-price", "--coupon", "6.94", "--maturity", "2036-05-11", "--settlement", "2026-11-16"]
    assert main([*args, "--price", "97.43"]) == 0
    out = _fields(capsys.readouterr().out)
    assert float(out["yield (%)"]) == pytest.approx(7.320279, abs=1e-6)
    assert float(out["accrued interest"]) == pytest.approx(6.94 * 5 / 360, abs=1e-6)
    assert out["previous coupon"] == "2026-11-11" and out["next coupon"] == "2027-05-11"
    for key in ("clean price", "dirty price", "modified duration", "dv01 (per 100)", "convexity"):
        assert float(out[key]) > 0

    assert main([*args, "--yield", "7.3203", "--cash-flows"]) == 0
    text = capsys.readouterr().out
    assert f"{float(_fields(text)['clean price']):.4f}" == "97.4299"
    assert "2036-05-11  redemption" in text

    assert main(["bond-price", "--coupon", "6.94", "--maturity", "2036-05-11", "--settlement", "2036-05-11",
                 "--yield", "7"]) == 2
    assert "on or after maturity" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main([*args])  # neither a yield nor a price


@pytest.mark.parametrize("extra, message", [
    (["--coupon", "7", "--maturity", "2036-05-11", "--yield", "7", "--conventions", "strips"],
     "STRIPS have no coupon; pass --coupon 0"),
    (["--coupon", "0", "--maturity", "2036-05-11", "--yield", "7", "--conventions", "strips",
      "--issue-date", "2025-09-01", "--first-coupon-date", "2026-05-11"], "does not apply to a STRIP"),
    (["--coupon", "6.94", "--maturity", "2036-13-01", "--yield", "7"], "--maturity: month must be in 1..12"),
    (["--coupon", "6.94", "--maturity", "2036-05-11", "--yield", "7", "--issue-date", "2026-02-30"],
     "--issue-date: day is out of range for month"),
    (["--coupon", "6.94", "--maturity", "2036-05-11", "--price", "0"], "clean price must be a positive number"),
    (["--coupon", "6.94", "--maturity", "2036-05-11", "--price", "inf"], "clean price must be a positive number"),
    (["--coupon", "6.94", "--maturity", "2036-05-11", "--yield", "nan"], "yield must be a finite number"),
    (["--coupon", "6.94", "--maturity", "2036-05-11", "--yield", "-250"], "yield must be above -200%"),
])
def test_bond_price_rejects_bad_input(capsys, extra, message):
    assert main(["bond-price", "--settlement", "2026-11-16", *extra]) == 2
    assert message in capsys.readouterr().err


def test_bond_price_settlement_flag_is_named(capsys):
    assert main(["bond-price", "--coupon", "6.94", "--maturity", "2036-05-11", "--settlement", "16-11-2026",
                 "--yield", "7"]) == 2
    assert "--settlement: Invalid isoformat string" in capsys.readouterr().err


def test_bond_price_options(capsys):
    # A STRIP is priced as a zero-coupon bond.
    assert main(["bond-price", "--coupon", "0", "--maturity", "2036-05-11", "--settlement", "2026-11-16",
                 "--yield", "7", "--conventions", "strips"]) == 0
    out = _fields(capsys.readouterr().out)
    assert float(out["dirty price"]) == pytest.approx(100 / 1.035 ** (18 + 175 / 180), abs=1e-6)
    assert "STRIPS conventions" in out["bond"] and float(out["accrued interest"]) == 0
    # A long first coupon, from the issue date to the first coupon date.
    assert main(["bond-price", "--coupon", "6.94", "--maturity", "2036-05-11", "--settlement", "2025-10-01",
                 "--yield", "7.1", "--issue-date", "2025-09-01", "--first-coupon-date", "2026-05-11"]) == 0
    out = _fields(capsys.readouterr().out)
    assert out["previous coupon"] == "2025-09-01" and out["next coupon"] == "2026-05-11"
    assert float(out["accrued interest"]) == pytest.approx(6.94 * 30 / 360, abs=1e-6)
    assert main(["bond-price", "--coupon", "5.91", "--maturity", "2028-06-30", "--settlement", "2028-02-15",
                 "--yield", "6", "--final-period", "compound"]) == 0
    assert "last period compound" in capsys.readouterr().out
    # 31 Aug maturity two days out: was exit 2 ("no yield prices these cash flows").
    assert main(["bond-price", "--coupon", "7", "--maturity", "2035-08-31", "--settlement", "2035-08-29",
                 "--price", "100"]) == 0
    out = _fields(capsys.readouterr().out)
    assert float(out["yield (%)"]) == pytest.approx(-6.762015, abs=1e-6)
    assert float(out["modified duration"]) > 0 and float(out["dv01 (per 100)"]) > 0
