import numpy as np
import pandas as pd
import pytest

from cr_strat.mandates import HolderRule, Mandate
from cr_strat.ownership import event_ownership

CBF = Mandate("mf_corporate_bond_fund", "worst", ("AA+",), holders=(HolderRule("MF", "corporate bond"),))


@pytest.fixture
def setup(isin):
    i1, i2, i3 = isin("INE111A0701"), isin("INE111A0702"), isin("INE999Z0701")
    securities = pd.DataFrame([
        {"isin": i1, "issuer_id": "X1", "instrument_class": "BOND", "face_value_inr": 1e5,
         "outstanding_face_inr": 1e9, "maturity_date": "2030-01-01"},
        {"isin": i2, "issuer_id": "X1", "instrument_class": "BOND", "face_value_inr": 1e5,
         "outstanding_face_inr": 1e9, "maturity_date": "2020-07-15"},
        {"isin": i3, "issuer_id": "Z", "instrument_class": "BOND", "face_value_inr": 1e5,
         "outstanding_face_inr": 1e9, "maturity_date": "2030-01-01"},
    ])
    cbf, crf = "Debt Scheme - Corporate Bond Fund", "Debt Scheme - Credit Risk Fund"
    rows = [
        ("2020-05-31", "MF", "A", cbf, i1, 300), ("2020-05-31", "MF", "A", cbf, i2, 50),
        ("2020-05-31", "MF", "A", cbf, i3, 10),
        ("2020-06-30", "MF", "A", cbf, i1, 100), ("2020-06-30", "MF", "A", cbf, i3, 10),
        ("2020-07-31", "MF", "A", cbf, i3, 10),
        ("2020-05-15", "MF", "B", crf, i1, 200), ("2020-06-30", "MF", "B", crf, i1, 400),
        ("2020-07-31", "MF", "B", crf, i1, 400),
        ("2020-03-31", "INSURER", "I", None, i1, 1000),  # too stale for a 2020-06-01 event
    ]
    holdings = pd.DataFrame(rows, columns=["as_of_date", "holder_type", "holder_id", "category", "isin", "quantity"])
    events = pd.DataFrame([
        {"event_id": 0, "issuer_id": "X1", "instrument_class": "BOND", "mandate": CBF.name,
         "threshold": "AA+", "direction": "down", "date": "2020-06-01"},
        {"event_id": 1, "issuer_id": "NOPE", "instrument_class": "BOND", "mandate": CBF.name,
         "threshold": "AA+", "direction": "down", "date": "2020-06-01"},
    ])
    return events, holdings, securities


def test_constrained_ownership_and_first_stage(setup):
    events, holdings, securities = setup
    out = event_ownership(events, holdings, securities, [CBF]).set_index("event_id")
    ev = out.loc[0]
    assert ev["n_isins"] == 2
    assert ev["outstanding_face_inr"] == pytest.approx(2e9)
    assert ev["constrained_held_inr"] == pytest.approx(350 * 1e5)
    assert ev["constrained_share"] == pytest.approx(0.0175)
    assert ev["n_constrained_holders"] == 1
    assert ev["other_mf_share"] == pytest.approx(0.01)
    assert ev["value_basis"] == "face"
    assert ev["baseline_from"] == pd.Timestamp("2020-05-15")

    # The maturing ISIN is left out, so only the 300 units of i1 are compared.
    assert ev["constrained_chg_30d"] == pytest.approx(-2 / 3)
    assert ev["constrained_cov_30d"] == pytest.approx(1.0)
    assert ev["other_mf_chg_30d"] == pytest.approx(1.0)
    assert ev["constrained_chg_60d"] == pytest.approx(-1.0)
    assert np.isnan(ev["constrained_chg_90d"]) and ev["constrained_cov_90d"] == 0.0

    assert out.loc[1, "note"] == "no live ISINs in security master"


def test_market_value_fallback(setup):
    events, holdings, securities = setup
    securities = securities.drop(columns=["face_value_inr"])
    holdings = holdings.assign(market_value_inr=holdings["quantity"] * 1.01e5)
    ev = event_ownership(events, holdings, securities, [CBF]).set_index("event_id").loc[0]
    assert ev["value_basis"] == "market"
    assert ev["constrained_held_inr"] == pytest.approx(350 * 1.01e5)
