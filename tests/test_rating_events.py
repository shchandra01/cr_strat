from pathlib import Path

import pandas as pd
import pytest

from cr_strat.mandates import HolderRule, Mandate, load_mandates
from cr_strat.rating_actions import classify_instrument, load_rating_actions
from cr_strat.rating_events import build_rating_events

CONFIG = Path(__file__).resolve().parents[1] / "config" / "mandates.toml"


@pytest.fixture
def mandates():
    return load_mandates(CONFIG)


def _actions(rows):
    return load_rating_actions(pd.DataFrame(rows, columns=["date", "issuer_id", "issuer_name", "agency", "instrument", "rating", "action"]))


@pytest.fixture
def actions():
    return _actions([
        ("2020-01-10", "X1", "X Finance", "CRISIL", "NCD", "CRISIL AA+/Stable", "Assigned"),
        ("2020-01-15", "X1", "X Finance", "ICRA", "Non-convertible debentures", "[ICRA]AA+ (Stable)", "Assigned"),
        ("2020-05-01", "X1", "X Finance", "CRISIL", "NCD", "CRISIL AA+/Negative", "Outlook revised"),
        ("2020-06-01", "X1", "X Finance", "CRISIL", "NCD", "CRISIL AA/Negative", "Downgraded"),
        ("2020-06-01", "X1", "X Finance", "CRISIL", "Commercial paper", "CRISIL A1+", "Reaffirmed"),
        ("2020-08-01", "X1", "X Finance", "ICRA", "NCD", "[ICRA]AA- (Negative)", "Downgraded"),
        ("2021-03-01", "X1", "X Finance", "ICRA", "NCD", "", "Withdrawn"),
        ("2020-02-01", "X2", "X2 Bank", "CARE", "Tier II bonds", "CARE AA; Stable", "Assigned"),
        ("2020-02-01", "X2", "X2 Bank", "CRISIL", "NCD", "XYZ", "Assigned"),
    ])


def _find(events, **kw):
    mask = pd.Series(True, index=events.index)
    for k, v in kw.items():
        mask &= events[k] == v
    return events[mask]


def test_config_loads(mandates):
    names = {m.name for m in mandates}
    assert {"mf_corporate_bond_fund", "insurer_approved_investment", "nps_two_agency_aa"} <= names
    assert all(m.verify for m in mandates)


def test_effective_rating_rules():
    worst = Mandate("w", "worst", ("AA",))
    second = Mandate("k", "kth_best", ("AA",), k=2)
    one = Mandate("a", "agency", ("AA",), agency="ICRA")
    ratings = {"CRISIL": 2, "ICRA": 4, "CARE": 3}
    assert worst.effective_notch(ratings) == 4
    assert second.effective_notch(ratings) == 3
    assert second.effective_notch({"CRISIL": 2}) is None
    assert one.effective_notch(ratings) == 4
    with pytest.raises(ValueError):
        Mandate("bad", "worst", ("A1+",))


def test_holder_rule():
    rule = HolderRule("MF", "corporate bond")
    assert rule.matches("mf", "Debt Scheme - Corporate Bond Fund")
    assert not rule.matches("MF", "Debt Scheme - Credit Risk Fund")
    assert not rule.matches("INSURER", "Corporate Bond")
    assert HolderRule("NPS").matches("NPS", None)


def test_load_classifies_and_flags_failures(actions):
    assert classify_instrument("Basel III Tier II bonds") == "SUB"
    assert classify_instrument("Long-term bank facilities") == "BANK_LOAN"
    assert classify_instrument("Short-term bank facilities") == "ST"
    assert classify_instrument(None) == "BOND"
    cp = actions[actions["instrument"] == "Commercial paper"].iloc[0]
    assert cp["instrument_class"] == "ST" and cp["scale"] == "ST"
    assert actions.loc[actions["rating_raw"] == "XYZ", "failure"].item() == "unparsed rating"
    withdrawn = actions[actions["date"] == pd.Timestamp("2021-03-01")].iloc[0]
    assert withdrawn["withdrawn"] and withdrawn["parse_ok"]


def test_threshold_crossings(actions, mandates):
    timeline, events = build_rating_events(actions, mandates)
    x1 = events[events["issuer_id"] == "X1"]

    cbf = _find(x1, mandate="mf_corporate_bond_fund")
    assert len(cbf) == 1
    ev = cbf.iloc[0]
    assert (ev["direction"], ev["date"], ev["prev_grade"], ev["new_grade"]) == ("down", pd.Timestamp("2020-06-01"), "AA+", "AA")
    assert ev["trigger_agencies"] == "CRISIL" and ev["cause"] == "rating_action"
    assert ev["prior_signal"] and ev["split"] and not ev["below_ig"]
    assert ev["agency_ratings"] == "CRISIL:AA;ICRA:AA+"

    nps = _find(x1, mandate="nps_two_agency_aa")
    assert list(nps["direction"]) == ["up", "down"]
    assert nps.iloc[0]["cause"] == "new_rating"  # second agency makes it NPS-eligible
    assert nps.iloc[1]["date"] == pd.Timestamp("2020-08-01")

    ins = _find(x1, mandate="insurer_approved_investment")
    assert list(zip(ins["direction"], ins["cause"])) == [("down", "rating_action"), ("up", "withdrawal")]

    # Short-term and subordinated ratings do not feed BOND mandates.
    assert events[events["issuer_id"] == "X2"].empty
    assert set(timeline["instrument_class"]) == {"BOND"}
    assert events["event_id"].is_unique


def test_repeat_crossings_are_flagged():
    actions = _actions([
        ("2022-01-01", "Y", "Y Ltd", "CRISIL", "NCD", "CRISIL AA+/Stable", None),
        ("2022-02-01", "Y", "Y Ltd", "CRISIL", "NCD", "CRISIL AA/Stable", None),
        ("2022-05-01", "Y", "Y Ltd", "CRISIL", "NCD", "CRISIL AA+/Stable", None),
        ("2022-09-01", "Y", "Y Ltd", "CRISIL", "NCD", "CRISIL AA/Stable", None),
    ])
    m = Mandate("cbf", "worst", ("AA+",), holders=(HolderRule("MF"),))
    _, events = build_rating_events(actions, [m], dedup_days=365)
    downs = events[events["direction"] == "down"]
    assert list(downs["first_in_window"]) == [True, False]
    assert not events["prior_signal"].any()
