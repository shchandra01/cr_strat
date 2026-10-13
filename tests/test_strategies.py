import pandas as pd
import pytest

from cr_strat.strategies import Carry, Context, EventReversal, build_strategy, prepare_events

T = pd.Timestamp("2025-06-02")


def _ctx(events, date=T, market=None, holdings=None):
    holdings = holdings if holdings is not None else pd.DataFrame(
        columns=["face", "value", "issuer_id", "entry_date", "weight"], index=pd.Index([], name="isin"))
    return Context(date=date, market=market, nav=1e9, holdings=holdings, events=events)


def _event(eid, issuer, days_ago, **kw):
    row = {"event_id": eid, "issuer_id": issuer, "instrument_class": "BOND", "mandate": "mf_corporate_bond_fund",
           "threshold": "AA+", "direction": "down", "date": (T - pd.Timedelta(days=days_ago)).strftime("%Y-%m-%d"),
           "new_notch": "3", "first_in_window": "True", "default": "False", "prior_signal": "False"}
    return row | kw


def test_event_selection_window_and_filters():
    events = prepare_events(pd.DataFrame([
        _event(1, "A", 10),
        _event(2, "B", 200),                                   # past the holding period
        _event(3, "C", 5, new_notch="12"),                     # lands below BBB-
        _event(4, "D", 5, prior_signal="True"),
        _event(5, "E", 30), _event(6, "E", 3, mandate="insurer_approved_investment"),  # later downgrade
        _event(7, "F", 5, first_in_window="False"),
    ]))
    picked = lambda **kw: set(EventReversal(**kw).active_events(_ctx(events))["issuer_id"])
    assert picked() == {"A", "D"}
    assert picked(prior_signal="without") == {"A"}
    assert picked(prior_signal="with") == {"D"}
    assert picked(exit_on_new_event=False) == {"A", "D", "E"}
    assert picked(entry_delay_days=8) == {"A"}


def test_constrained_share_only_once_disclosed():
    events = prepare_events(pd.DataFrame([_event(1, "A", 10), _event(2, "B", 10)]),
                            pd.DataFrame({"event_id": [1, 2], "constrained_share": [0.3, 0.05],
                                          "baseline_to": ["2025-04-30", "2025-05-31"]}), disclosure_lag_days=10)
    strategy = EventReversal(min_constrained_share=0.1)
    assert set(strategy.active_events(_ctx(events))["issuer_id"]) == {"A"}
    assert events.set_index("event_id").loc[2, "ownership_known"] == pd.Timestamp("2025-06-10")


def test_build_strategy_validates_parameters():
    s = build_strategy("event_reversal", {"mandates": ["a", "b"], "hold_days": 60})
    assert s.mandates == ("a", "b") and s.hold_days == 60 and s.params()["hold_days"] == 60
    with pytest.raises(ValueError, match="unknown parameters"):
        build_strategy("carry", {"hold_dayz": 1})
    with pytest.raises(ValueError, match="unknown strategy"):
        build_strategy("nope")
    with pytest.raises(ValueError):
        EventReversal(prior_signal="maybe")


def test_ranked_strategy_keeps_holdings_within_buffer(market_factory):
    d0 = pd.Timestamp("2025-03-03")
    sec = [{"isin": f"B{i}", "issuer_id": f"I{i}", "instrument_class": "BOND", "maturity_date": "2028-06-30",
            "coupon_pct": 8.0} for i in range(4)]
    trades = [(d0, f"B{i}", 7.0 + i * 0.5, 1e8) for i in range(4)]
    trades += [(d0 - pd.Timedelta(days=d), f"B{i}", 7.0 + i * 0.5, 1e8) for i in range(4) for d in (7, 14)]
    md = market_factory(sec, trades)
    holdings = pd.DataFrame({"face": [1e7, 1e7], "value": [1e7, 1e7], "issuer_id": ["I2", "I0"],
                             "entry_date": [d0, d0], "weight": [0.01, 0.01]}, index=pd.Index(["B2", "B0"], name="isin"))
    ctx = _ctx(prepare_events(None), d0, md, holdings)
    w = Carry(min_rating="AA", n_bonds=1, hold_buffer=2.0, exclude_downgraded_days=0).target_weights(ctx)
    assert list(w.index) == ["B2"]  # ranked 2nd of 4: kept; B0 ranked 4th: sold; no room for B3
    w = Carry(min_rating="AA", n_bonds=2, hold_buffer=1.0, exclude_downgraded_days=0).target_weights(ctx)
    assert set(w.index) == {"B3", "B2"} and w.sum() == pytest.approx(1.0)
