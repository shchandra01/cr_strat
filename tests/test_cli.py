from pathlib import Path

import openpyxl
import pandas as pd

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
