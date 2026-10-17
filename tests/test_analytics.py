import numpy as np
import pandas as pd
import pytest

from cr_strat import analytics


def _nav(values, start="2025-01-01"):
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)), dtype=float)


def test_drawdowns_and_table():
    nav = _nav([100, 110, 99, 105, 110, 121, 100, 101])
    dd = analytics.drawdown(nav)
    assert dd.min() == pytest.approx(100 / 121 - 1)
    table = analytics.drawdowns(nav, top=2)
    assert table["depth"].tolist() == pytest.approx([100 / 121 - 1, 99 / 110 - 1])
    assert pd.isna(table.loc[0, "recovery"]) and table.loc[1, "recovery"] == nav.index[4]


def test_performance_stats_annualise_by_sample():
    nav = _nav(100 * np.cumprod(np.r_[1, np.tile([1.001, 0.9995], 260)]))
    stats = analytics.performance_stats(nav, cash_rate_pct=pd.Series(0.0, index=nav.index))
    r = nav.pct_change().dropna()
    ppy = len(r) / ((nav.index[-1] - nav.index[0]).days / 365.25)
    assert stats["sharpe"] == pytest.approx(r.mean() / r.std() * np.sqrt(ppy))
    assert stats["max_drawdown"] == pytest.approx(-0.0005)
    assert stats["hit_rate_daily"] == pytest.approx(0.5)
    bench = analytics.performance_stats(nav, benchmark=nav)
    assert bench["beta"] == pytest.approx(1.0) and bench["tracking_error"] == pytest.approx(0.0)


def test_monthly_returns():
    nav = pd.Series([100, 102, 102, 99.96], index=pd.to_datetime(["2025-01-01", "2025-01-31", "2025-02-14",
                                                                    "2025-02-28"]))
    table = analytics.monthly_returns(nav).set_index("calendar_year")
    assert table.loc[2025, "Jan"] == pytest.approx(0.02) and table.loc[2025, "Feb"] == pytest.approx(-0.02)
    assert table.loc[2025, "year"] == pytest.approx(-0.0004)


def test_episodes_sum_daily_attribution():
    rows = []
    for k, d in enumerate(pd.bdate_range("2025-01-01", periods=4)):
        rows.append({"date": d, "isin": "A", "issuer_id": "I", "sector": None, "rating_bucket": "AA",
                     "face": 0.0 if k == 3 else 1e7, "value": 0.0 if k == 3 else 1e7, "spread_bps": 100 + k,
                     "entry_date": pd.Timestamp("2025-01-01"), "pnl_income": 10.0, "pnl_roll": 1.0,
                     "pnl_rates": -5.0, "pnl_spread": 2.0, "pnl_execution": -3.0 if k in (0, 3) else 0.0,
                     "pnl_funding": -8.0, "closed": "sold" if k == 3 else None})
    ep = analytics.episodes(pd.DataFrame(rows)).iloc[0]
    assert ep["pnl"] == pytest.approx(4 * 8.0 - 6.0)
    assert ep["excess_pnl"] == pytest.approx(ep["pnl"] + 4 * 5.0 - 4 * 8.0)
    assert ep["status"] == "sold" and ep["holding_days"] == 5 and ep["exit_spread_bps"] == 103


def test_rising_nav_has_no_drawdowns_and_inputs_are_checked():
    nav = _nav([100, 100.1, 100.2, 100.3])
    table = analytics.drawdowns(nav)
    assert table.empty and list(table.columns)[-2:] == ["days_to_trough", "days_underwater"]
    frame = pd.DataFrame({"date": nav.index, "nav": nav.to_numpy()})
    assert analytics.performance_stats(frame)["max_drawdown"] == 0.0  # BacktestResult.nav-style frame works
    with pytest.raises(TypeError, match="indexed by date"):
        analytics.performance_stats(pd.Series([100.0, 101.0, 102.0]))
    with pytest.raises(ValueError, match="no dates"):
        analytics.performance_stats(nav, cash_rate_pct=pd.Series([6.0] * 4))
