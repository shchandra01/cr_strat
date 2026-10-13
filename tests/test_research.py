import numpy as np
import pandas as pd
import pytest

from cr_strat import research


def test_ic_and_quantiles_on_a_planted_signal():
    rng = np.random.default_rng(0)
    rows = []
    for d in pd.date_range("2025-01-03", periods=30, freq="W-FRI"):
        s = rng.normal(size=50)
        rows += [{"date": d, "isin": f"B{i}", "signal": s[i], "xr_60d": 0.01 * s[i] + rng.normal(0, 0.002),
                  "bucket": "AA" if i % 2 else "A"} for i in range(50)]
    panel = pd.DataFrame(rows)
    ic = research.information_coefficients(panel, "signal", "xr_60d")
    assert len(ic) == 30 and ic["ic"].mean() > 0.9
    summary = research.ic_summary(ic, horizon_days=60, sample_days=7)
    assert summary["t_stat_nw"] > 10 and summary["hit_rate"] == 1.0
    table, ls = research.quantile_returns(panel, "signal", "xr_60d", n=5, horizon_days=60)
    assert table["mean"].is_monotonic_increasing and ls["long_short_mean"] > 0 and ls["monotonic"]
    neutral = research.neutralize(panel, "signal", "bucket")
    assert neutral.between(-0.5, 0.5).all()
    summary, quantiles, by_date = research.evaluate_signals(panel, ["signal"], [60], neutralize_by="bucket")
    assert summary.loc[0, "mean_ic"] > 0.8 and len(quantiles) == 5


def test_forward_returns_earn_the_yield_and_spread(market_factory):
    days = pd.bdate_range("2025-01-01", "2025-12-31")
    sec = [{"isin": "B", "issuer_id": "I", "instrument_class": "BOND", "maturity_date": "2029-06-30",
            "coupon_pct": 8.0}]
    md = market_factory(sec, [(d, "B", 8.0, 1e8) for d in days[::2]], end="2025-12-31")
    panel = research.signal_panel(md, start="2025-01-01", end="2025-08-31")
    assert {"rv_bps", "spread_chg_20d", "spread_chg_60d"} <= set(panel.columns)
    out = research.forward_returns(md, panel, (60,))
    row = out.dropna(subset=["xr_60d"]).iloc[0]
    years = (row["date"] + pd.Timedelta(days=60) - row["entry_date"]).days / 365
    assert row["entry_date"] > row["date"]
    # Constant 8% yield and 6% flat curve: total return ~8% a year, excess ~2% a year.
    assert row["tr_60d"] == pytest.approx(1.08 ** years - 1, rel=0.02)
    assert row["xr_60d"] == pytest.approx(1.08 ** years - 1.06 ** years, rel=0.02)
    assert row["dspread_60d"] == pytest.approx(0.0, abs=1e-6)
    mark = research.forward_returns(md, panel, (60,), entry="mark")
    assert (mark["entry_date"] == mark["date"]).all()
