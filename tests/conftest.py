import pytest

from cr_strat.isin import is_valid_isin


def make_isin(payload: str) -> str:
    """Append the check digit to an 11-character ISIN payload."""
    for d in "0123456789":
        if is_valid_isin(payload + d):
            return payload + d
    raise ValueError(payload)


@pytest.fixture
def isin():
    return make_isin


@pytest.fixture
def market_factory():
    """Build a MarketData from a few securities and (date, isin, yield_pct, volume_inr) trades on a flat curve."""
    import numpy as np
    import pandas as pd

    from cr_strat.curves import BenchmarkCurve, tenor_bucket
    from cr_strat.market import MarketData
    from cr_strat.ownership import load_securities
    from cr_strat.rating_scale import LT_NOTCH

    def make(securities, trades, *, curve_yield=6.0, start="2025-01-01", end="2026-12-31", **kwargs):
        days = pd.bdate_range(start, end)
        curve = BenchmarkCurve(pd.DataFrame({"date": np.repeat(days, 2), "tenor_years": np.tile([0.25, 10.0], len(days)),
                                             "yield_pct": curve_yield}), compounding="annual")
        sec = load_securities(pd.DataFrame(securities))
        rows = pd.DataFrame(trades, columns=["date", "isin", "yield_pct", "volume_inr"])
        rows["date"] = pd.to_datetime(rows["date"])
        bucket = sec.set_index("isin")["rating_bucket"] if "rating_bucket" in sec else pd.Series("AA", index=sec["isin"])
        rows = rows.merge(sec[["isin", "issuer_id", "instrument_class", "maturity_date"]], on="isin")
        rows["tenor_years"] = (rows["maturity_date"] - rows["date"]).dt.days / 365.25
        rows["tenor_bucket"] = tenor_bucket(rows["tenor_years"]).to_numpy()
        rows["rating_bucket"] = rows["isin"].map(bucket)
        rows["rating_notch"] = rows["rating_bucket"].map(LT_NOTCH)
        rows["spread_bps"] = (rows["yield_pct"] - curve.yields(rows["date"], rows["tenor_years"])) * 100
        return MarketData(rows, sec, curve, **kwargs)

    return make
