import numpy as np
import pandas as pd
import pytest

D0 = pd.Timestamp("2025-03-03")


def _sec(isin, issuer, **kw):
    return {"isin": isin, "issuer_id": issuer, "instrument_class": "BOND", "maturity_date": "2028-06-30",
            "coupon_pct": 8.0, "face_value_inr": 1e5, **kw}


@pytest.fixture
def market(market_factory):
    sec = [_sec("E1", "E"), *(_sec(f"P{i}", f"Q{i}") for i in range(3))]
    trades = [(D0, "E1", 7.5, 1e8)]
    for day in range(0, 15):
        d = D0 + pd.Timedelta(days=day)
        if d.weekday() < 5:
            trades += [(d, f"P{i}", 7.0 if day < 3 else 7.2, 1e8) for i in range(3)]
    return market_factory, sec, trades


def test_trade_day_mark_is_the_traded_yield(market):
    factory, sec, trades = market
    m = factory(sec, trades).marks(["E1"], D0).iloc[0]
    assert (m["yield_pct"], m["source"], m["age_days"]) == (7.5, "trade", 0)
    assert m["spread_bps"] == pytest.approx(150)
    assert m["dirty"] > m["clean"] > 0


def test_stale_mark_moves_with_its_peers(market):
    factory, sec, trades = market
    later = D0 + pd.Timedelta(days=11)
    m = factory(sec, trades).marks(["E1"], later).iloc[0]
    # Peers widened 20 bps since E1's trade; E1 keeps its 50 bps residual over them.
    assert (m["source"], m["age_days"]) == ("matrix", 11)
    assert m["spread_bps"] == pytest.approx(170)
    assert m["yield_pct"] == pytest.approx(6.0 + 1.70)
    flat = factory(sec, trades, peer_adjust=False).marks(["E1"], later).iloc[0]
    assert flat["source"] == "carried" and flat["spread_bps"] == pytest.approx(150)


def test_marks_never_look_ahead(market):
    factory, sec, trades = market
    md = factory(sec, trades + [(D0 + pd.Timedelta(days=30), "E1", 9.0, 1e8)])
    before, on = md.marks(["E1", "E1"], [D0 - pd.Timedelta(days=1), D0 + pd.Timedelta(days=11)]).itertuples()
    assert np.isnan(before.dirty) and pd.isna(before.source)
    assert on.spread_bps == pytest.approx(170)  # the later 9% trade is not visible yet


def test_snapshot_relative_value_excludes_issuer(market):
    factory, sec, trades = market
    md = factory(sec, trades)
    snap = md.snapshot(D0, max_age_days=5).set_index("isin")
    assert snap.loc["E1", "peer_bps"] == pytest.approx(100) and snap.loc["E1", "rv_bps"] == pytest.approx(50)
    assert snap.loc["E1", "peer_issuers"] == 3
    later = md.snapshot(D0 + pd.Timedelta(days=11), max_age_days=5)
    assert "E1" not in set(later["isin"])  # its only trade is older than max_age_days
    assert md.snapshot(D0 + pd.Timedelta(days=11), max_age_days=5, issuers=["Q0"])["isin"].tolist() == ["P0"]
