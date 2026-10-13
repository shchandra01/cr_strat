import numpy as np
import pandas as pd
import pytest

from cr_strat.flows import event_flows, flow_columns, parse_windows, window_label
from cr_strat.mandates import HolderRule, Mandate

CBF = Mandate("mf_corporate_bond_fund", "worst", ("AA+",), holders=(HolderRule("MF", "corporate bond"),))
INS = Mandate("insurer_approved_investment", "worst", ("AA",), holders=(HolderRule("INSURER"),))
CBF_CAT, CRF_CAT, MED_CAT = ("Debt Scheme - Corporate Bond Fund", "Debt Scheme - Credit Risk Fund",
                             "Debt Scheme - Medium Duration Fund")
EVENT_DATE = pd.Timestamp("2026-06-01")
OLD_DATE = pd.Timestamp("2019-06-10")


def _trade(date, day, holder, category, isin, direction, deal, face=None, value=None):
    when = date + pd.Timedelta(days=day)
    return {"trade_date": when, "settlement_date": when, "isin": isin, "direction": direction, "deal_type": deal,
            "holder_type": "MF", "holder_id": f"MF|amc|{holder}", "holder_name": holder, "category": category,
            "face_traded_inr": face, "value_inr": value if value is not None else face}


@pytest.fixture
def setup(isin):
    i1, i2, i3, i4, i5, old = (isin("INE111A07AA"), isin("INE111A07AB"), isin("INE111A14AC"),
                               isin("INE999Z07AA"), isin("INE111A07AD"), isin("INE555Y08AA"))
    securities = pd.DataFrame([
        {"isin": i1, "issuer_id": "X1", "instrument_class": "BOND", "outstanding_face_inr": 1e9,
         "issue_date": "2020-01-01", "maturity_date": "2030-01-01"},
        {"isin": i2, "issuer_id": "X1", "instrument_class": "BOND", "outstanding_face_inr": 1e9,
         "maturity_date": "2026-05-15"},
        {"isin": i3, "issuer_id": "X1", "instrument_class": "ST", "outstanding_face_inr": 1e9},
        {"isin": i4, "issuer_id": "Z", "instrument_class": "BOND", "outstanding_face_inr": 1e9},
        {"isin": i5, "issuer_id": "X1", "instrument_class": "BOND", "outstanding_face_inr": 1e9,
         "issue_date": "2026-07-01"},
        {"isin": old, "issuer_id": "Y1", "instrument_class": "BOND", "outstanding_face_inr": 5e8},
    ])
    d = EVENT_DATE
    trades = [
        _trade(d, -10, "A", CBF_CAT, i1, "BUY", "MARKET", 20e6),
        _trade(d, 2, "A", CBF_CAT, i1, "SELL", "MARKET", 100e6),
        _trade(d, 5, "A", CBF_CAT, i1, "SELL", "INTER_SCHEME", 50e6),
        _trade(d, 5, "B", CRF_CAT, i1, "BUY", "INTER_SCHEME", 50e6),
        _trade(d, 3, "D", MED_CAT, i1, "BUY", "OFF_MARKET", 70e6),
        _trade(d, 40, "C", CBF_CAT, i1, "SELL", "MARKET", None, 29e6),
        _trade(d, 6, "E", None, i1, "SELL", "MARKET", 10e6),
        # Not counted: repo, a matured ISIN, another class, another issuer, an ISIN issued after the event.
        _trade(d, 4, "A", CBF_CAT, i1, "SELL", "REPO", 500e6),
        _trade(d, 4, "A", CBF_CAT, i2, "SELL", "MARKET", 500e6),
        _trade(d, 4, "A", CBF_CAT, i3, "SELL", "MARKET", 500e6),
        _trade(d, 4, "A", CBF_CAT, i4, "SELL", "MARKET", 500e6),
        _trade(d, 35, "A", CBF_CAT, i5, "BUY", "PRIMARY", 500e6),
        # A TREPS row only tells us that day's file was loaded.
        _trade(d, 20, "A", CBF_CAT, None, "BUY", "TREPS", 1e6),
        # Before the schema change: no direction.
        _trade(OLD_DATE, -5, "A", CBF_CAT, old, None, "MARKET", None, 40e6),
        _trade(OLD_DATE, -3, "F", None, old, None, "INTER_SCHEME", None, 15e6),
        _trade(OLD_DATE, 2, "A", CBF_CAT, None, None, "TREPS", None, 1e6),
    ]
    events = pd.DataFrame([
        {"event_id": 0, "issuer_id": "X1", "instrument_class": "BOND", "mandate": CBF.name, "threshold": "AA+",
         "direction": "down", "date": "2026-06-01"},
        {"event_id": 1, "issuer_id": "NOPE", "instrument_class": "BOND", "mandate": CBF.name, "threshold": "AA+",
         "direction": "down", "date": "2026-06-01"},
        {"event_id": 2, "issuer_id": "X1", "instrument_class": "BOND", "mandate": INS.name, "threshold": "AA",
         "direction": "down", "date": "2026-06-01"},
        {"event_id": 3, "issuer_id": "Y1", "instrument_class": "BOND", "mandate": CBF.name, "threshold": "AA+",
         "direction": "down", "date": "2019-06-10"},
        {"event_id": 4, "issuer_id": "X1", "instrument_class": "BOND", "mandate": "nope", "threshold": "AA+",
         "direction": "down", "date": "2026-06-01"},
        {"event_id": 5, "issuer_id": "Z", "instrument_class": "BOND", "mandate": CBF.name, "threshold": "AA+",
         "direction": "down", "date": "2027-03-01"},
    ])
    return events, pd.DataFrame(trades), securities


def test_constrained_selling_by_window(setup):
    events, trades, securities = setup
    out = event_flows(events, trades, securities, [CBF, INS]).set_index("event_id")
    assert list(out.reset_index().columns) == flow_columns()
    ev = out.loc[0]
    assert ev["n_isins"] == 1 and ev["n_isins_traded"] == 1
    assert ev["outstanding_face_inr"] == pytest.approx(1e9)
    assert ev["value_basis"] == "face+value"
    assert ev["uncategorised_share"] == pytest.approx(10 / 329)

    assert ev["constrained_bought_inr_m30_m1"] == pytest.approx(20e6)
    assert ev["constrained_net_sold_share_m30_m1"] == pytest.approx(-0.02)
    assert np.isnan(ev["constrained_interscheme_share_m30_m1"])
    assert ev["days_with_data_m30_m1"] == 1

    assert ev["n_trades_0_10"] == 5
    assert ev["days_with_data_0_10"] == 5 and ev["days_with_data_0_30"] == 6
    assert bool(ev["direction_available_0_10"])
    assert ev["constrained_sold_inr_0_10"] == pytest.approx(150e6)
    assert ev["constrained_sold_share_0_10"] == pytest.approx(0.15)
    assert ev["constrained_net_sold_share_0_10"] == pytest.approx(0.15)
    assert ev["constrained_n_sellers_0_10"] == 1
    assert ev["constrained_interscheme_share_0_10"] == pytest.approx(1 / 3)
    assert ev["other_mf_bought_inr_0_10"] == pytest.approx(120e6)
    assert ev["other_mf_sold_inr_0_10"] == pytest.approx(10e6)
    assert ev["other_mf_net_sold_share_0_10"] == pytest.approx(-0.11)
    assert ev["other_mf_traded_inr_0_10"] == pytest.approx(130e6)

    # Scheme C sells on day 40 and reports no face value, so its traded value counts.
    assert ev["constrained_sold_inr_0_30"] == pytest.approx(150e6)
    assert ev["constrained_sold_inr_0_60"] == pytest.approx(179e6)
    assert ev["constrained_n_sellers_0_60"] == 2

    assert out.loc[1, "note"] == "no live ISINs in security master"
    assert out.loc[4, "note"] == "unknown mandate"


def test_mandate_without_mf_holders(setup):
    events, trades, securities = setup
    ev = event_flows(events, trades, securities, [CBF, INS]).set_index("event_id").loc[2]
    assert ev["note"] == "mandate binds no MF schemes"
    assert np.isnan(ev["constrained_sold_inr_0_10"])
    assert ev["other_mf_sold_inr_0_10"] == pytest.approx(160e6)


def test_windows_before_schema_change_and_without_data(setup):
    events, trades, securities = setup
    out = event_flows(events, trades, securities, [CBF]).set_index("event_id")
    old = out.loc[3]
    assert not old["direction_available_m30_m1"]
    assert old["constrained_traded_inr_m30_m1"] == pytest.approx(40e6)
    assert old["other_mf_traded_inr_m30_m1"] == pytest.approx(15e6)
    assert np.isnan(old["constrained_sold_inr_m30_m1"]) and np.isnan(old["other_mf_net_sold_share_m30_m1"])
    assert old["value_basis"] == "value"
    # No trades in the event ISINs after the event, but the day's file was there.
    assert old["direction_available_0_10"] and old["days_with_data_0_10"] == 1
    assert old["constrained_sold_inr_0_10"] == 0

    none = out.loc[5]
    assert none["days_with_data_0_90"] == 0 and none["n_trades_0_90"] == 0
    assert not none["direction_available_0_90"]
    assert np.isnan(none["constrained_sold_inr_0_90"]) and np.isnan(none["other_mf_traded_inr_0_90"])


def test_csv_input_and_custom_windows(setup, tmp_path):
    events, trades, securities = setup
    trades.to_csv(tmp_path / "mf_trades.csv", index=False, date_format="%Y-%m-%d")
    events.to_csv(tmp_path / "events.csv", index=False)
    securities.to_csv(tmp_path / "securities.csv", index=False)
    windows = parse_windows("-5:-1,0:3")
    out = event_flows(tmp_path / "events.csv", [tmp_path / "mf_trades.csv"], tmp_path / "securities.csv", [CBF],
                      windows=windows).set_index("event_id")
    assert "constrained_sold_inr_0_3" in out.columns and "constrained_sold_inr_0_10" not in out.columns
    # Event ids come back as text from CSV, as in ownership.
    assert out.loc["0", "constrained_sold_inr_0_3"] == pytest.approx(100e6)
    assert out.loc["0", "other_mf_bought_inr_0_3"] == pytest.approx(70e6)
    assert out.loc["3", "constrained_traded_inr_m5_m1"] == pytest.approx(40e6)


def test_window_helpers():
    assert parse_windows("-30:-1, 0:10") == ((-30, -1), (0, 10))
    assert window_label((-30, -1)) == "m30_m1" and window_label((0, 90)) == "0_90"
    with pytest.raises(ValueError):
        parse_windows("10:0")
    assert "other_mf_interscheme_share_0_90" in flow_columns()
