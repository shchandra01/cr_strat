import numpy as np
import pandas as pd
import pytest

from cr_strat.curves import BenchmarkCurve, convert_yield
from cr_strat.event_prices import event_price_paths
from cr_strat.ownership import load_securities
from cr_strat.price_panel import build_price_panel
from cr_strat.rating_actions import load_rating_actions
from cr_strat.rating_events import rating_timeline

T = pd.Timestamp("2025-06-02")


def test_price_panel_spreads_and_ratings(isin):
    a = isin("INE100A0701")
    securities = load_securities(pd.DataFrame([{"isin": a, "issuer_id": "A", "instrument_class": "BOND",
                                                "maturity_date": "2029-03-02", "sector": "NBFC"}]))
    daily = pd.DataFrame({"date": pd.to_datetime(["2026-02-27", "2026-03-02"]), "isin": [a, a],
                          "yield_pct": [7.9, 8.0], "price": [100.5, 100.2], "volume_inr": [5e8, 1e9],
                          "n_trades": [2, 3]})
    curve = BenchmarkCurve(pd.DataFrame({"date": ["2026-03-02", "2026-03-02"], "tenor_years": [1, 5],
                                         "yield_pct": [6.0, 6.4]}), compounding="semiannual")
    ratings = rating_timeline(load_rating_actions(pd.DataFrame([
        {"date": "2025-01-01", "issuer_id": "A", "rating": "CRISIL AA+/Stable"},
        {"date": "2026-03-02", "issuer_id": "A", "rating": "CRISIL AA/Negative"},
    ])))
    panel = build_price_panel(daily, securities, curve, ratings).set_index("date")

    before, on = panel.loc["2026-02-27"], panel.loc["2026-03-02"]
    assert np.isnan(before["spread_bps"])  # no curve on or before that day
    assert before["rating_bucket"] == "AA+" and on["rating_bucket"] == "AA"
    tenor = (pd.Timestamp("2029-03-02") - pd.Timestamp("2026-03-02")).days / 365.25
    bench = convert_yield(6.0 + 0.1 * (tenor - 1), "semiannual", "annual")
    assert on["spread_bps"] == pytest.approx((8.0 - bench) * 100)
    assert on["tenor_bucket"] == "3-5y" and on["sector"] == "NBFC"


def _rows(issuer, isin, bucket, spread, days):
    return [{"date": T + pd.Timedelta(days=d), "isin": isin, "issuer_id": issuer, "instrument_class": "BOND",
             "rating_bucket": bucket, "tenor_bucket": "1-3y", "spread_bps": spread, "volume_inr": 1e8}
            for d in days]


@pytest.fixture
def panel():
    rows = []
    for i in range(3):
        rows += _rows(f"P{i}", f"PEER{i}", "AA+", 100.0, range(-100, 271))
        rows += _rows(f"Q{i}", f"LOWER{i}", "AA", 160.0, range(-100, 271))
    path = {-62: 125.0, -5: 120.0, 2: 220.0, 10: 210.0, 60: 170.0, 120: 150.0, 250: 140.0}
    for d, s in path.items():
        rows += _rows("E", "E1", "AA+" if d < 0 else "AA", s, [d])
    rows += _rows("E", "E2", "AA", 300.0, [3])  # second bond, no trade before the event
    return pd.DataFrame(rows)


def test_event_paths(panel):
    events = pd.DataFrame([
        {"event_id": 7, "issuer_id": "E", "instrument_class": "BOND", "mandate": "mf_corporate_bond_fund",
         "threshold": "AA+", "direction": "down", "date": T},
        {"event_id": 8, "issuer_id": "NONE", "instrument_class": "BOND", "mandate": "mf_corporate_bond_fund",
         "threshold": "AA+", "direction": "down", "date": T},
    ])
    bonds, summary = event_price_paths(events, panel)
    e1 = bonds[bonds["isin"] == "E1"].iloc[0]
    assert e1["pre_spread_bps"] == 120 and e1["pre_peer_bps"] == 100 and e1["pre_bucket"] == "AA+"
    assert e1["pre_trade_days"] == 2  # t-62 and t-5 fall in the 90-day lookback
    assert (e1["first_post_days"], e1["first_post_abn_bps"], e1["first_post_gap_cur_bps"]) == (2, 100, 60)
    assert (e1["m60_days"], e1["m60_chg_bps"], e1["m60_abn_bps"]) == (-62, -5, -5)
    assert [e1[f"{k}_abn_bps"] for k in ("p10", "p60", "p120", "p250")] == [90, 50, 30, 20]
    assert [e1[f"{k}_gap_cur_bps"] for k in ("p10", "p60", "p120", "p250")] == [50, 10, -10, -20]

    e2 = bonds[bonds["isin"] == "E2"].iloc[0]
    assert np.isnan(e2["first_post_abn_bps"]) and e2["first_post_gap_cur_bps"] == 140

    s = summary.set_index("event_id")
    assert (s.loc[7, "n_bonds"], s.loc[7, "n_bonds_with_pre"]) == (2, 1)
    assert s.loc[7, "first_post_abn_bps"] == 100 and s.loc[7, "first_post_n"] == 1
    assert s.loc[8, "n_bonds"] == 0


def test_peer_drift_is_removed(panel):
    # If every AA+ peer widens 30 bps after the event, the bond's abnormal change shrinks by 30.
    shifted = panel.copy()
    after = (shifted["rating_bucket"] == "AA+") & (shifted["date"] >= T) & (shifted["issuer_id"] != "E")
    shifted.loc[after, "spread_bps"] += 30
    events = pd.DataFrame([{"event_id": 1, "issuer_id": "E", "instrument_class": "BOND", "mandate": "m",
                            "threshold": "AA+", "direction": "down", "date": T}])
    bonds, _ = event_price_paths(events, shifted)
    e1 = bonds[bonds["isin"] == "E1"].iloc[0]
    assert e1["p60_chg_bps"] == 50 and e1["p60_abn_bps"] == 20
