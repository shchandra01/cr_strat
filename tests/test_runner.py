from pathlib import Path

import pandas as pd
import pytest

from cr_strat.runner import build, load_config, parse_grid, set_param, sweep

CONFIG = Path(__file__).resolve().parents[1] / "config"


@pytest.mark.parametrize("name", ["event_reversal", "relative_value", "carry"])
def test_shipped_configs_build(name):
    config, execution, strategy, benchmark = build(load_config(CONFIG / f"backtest_{name}.toml"))
    assert strategy.name == name and benchmark.name == "universe_index"
    assert execution.trade_days_only and config.max_gross == 1.0


def test_overrides_and_validation():
    raw = load_config(CONFIG / "backtest_event_reversal.toml")
    raw = set_param(raw, "strategy.hold_days", "60")
    raw = set_param(raw, "execution.lag_days", "2")
    raw = set_param(raw, "backtest.start", "2020-01-01")
    config, execution, strategy, _ = build(raw)
    assert strategy.hold_days == 60 and execution.lag_days == 2
    assert pd.Timestamp(config.start) == pd.Timestamp("2020-01-01")
    assert parse_grid(["strategy.hold_days=60,120", "strategy.prior_signal=with,any"]) == {
        "strategy.hold_days": [60, 120], "strategy.prior_signal": ["with", "any"]}
    with pytest.raises(ValueError, match="unknown keys"):
        build(set_param(raw, "backtest.capital", 1))
    with pytest.raises(ValueError):
        load_config({"strategy": {"name": "carry"}, "extra": {}})
    with pytest.raises(ValueError):
        build(set_param(raw, "execution.lag_days", 0))  # a signal cannot trade on its own data


def test_sweep_reports_each_combination(market_factory):
    days = pd.bdate_range("2025-01-01", "2025-06-30")
    sec = [{"isin": f"B{i}", "issuer_id": f"I{i}", "instrument_class": "BOND", "maturity_date": "2028-06-30",
            "coupon_pct": 8.0, "face_value_inr": 1e5} for i in range(5)]
    trades = [(d, f"B{i}", 7.0 + 0.3 * i + 0.01 * (k % 7), 1e9) for k, d in enumerate(days) for i in range(5)]
    md = market_factory(sec, trades, end="2025-06-30")
    raw = {"strategy": {"name": "carry", "params": {"min_rating": "AA", "exclude_downgraded_days": 0}}}
    out = sweep(md, raw, {"strategy.n_bonds": [1, 3]}, split_date="2025-03-31", progress=False)
    assert out["strategy.n_bonds"].tolist() == [1, 3]
    assert {"all_sharpe", "is_hedged_sharpe", "oos_cagr", "deflated_sharpe", "turnover_annual"} <= set(out.columns)
    assert out["avg_positions"].iloc[1] > out["avg_positions"].iloc[0]


def test_nested_overrides_and_input_errors(tmp_path):
    raw = set_param(load_config(CONFIG / "backtest_carry.toml"), "execution.half_spread_bps.AAA", "4")
    _, execution, _, _ = build(raw)
    assert execution.half_spread_bps["AAA"] == 4 and execution.half_spread_bps["AA"] == 7.5
    from cr_strat.backtest import read_cash_rates
    from cr_strat.stats import ols
    from cr_strat.trades import read_trades
    with pytest.raises(ValueError, match="ISO"):
        read_cash_rates(pd.DataFrame({"date": ["01/02/2021"], "rate_pct": [5.0]}))
    assert read_cash_rates(pd.DataFrame({"date": ["2021-02-01"], "rate_pct": [5.0]})).iloc[0] == 5.0
    pd.DataFrame({"ISIN": ["X"], "Yield": [7.0]}).to_csv(tmp_path / "raw_venue.csv", index=False)
    with pytest.raises(ValueError, match="not a load-trades output"):
        read_trades(tmp_path / "raw_venue.csv")
    with pytest.raises(ValueError, match="not numeric"):
        ols(pd.DataFrame({"y": [1.0, 2, 3, 4], "x": ["a", "b", "c", "d"]}), "y", ["x"])
