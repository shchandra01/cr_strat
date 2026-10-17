from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import pytest

from cr_strat.backtest import BacktestConfig, rebalance_dates, run_backtest
from cr_strat.execution import ExecutionModel
from cr_strat.strategies import Strategy

COMPONENTS = ["pnl_income", "pnl_roll", "pnl_rates", "pnl_spread", "pnl_execution", "pnl_cash"]
FREE = dict(half_spread_bps={}, default_half_spread_bps=0.0, impact_bps=0.0, stamp_duty_pct=0.0)


@dataclass
class Hold(Strategy):
    weights: dict = field(default_factory=lambda: {"B": 1.0})
    rebalance: str = "D"

    def target_weights(self, ctx):
        return pd.Series(self.weights)


def _bond(**kw):
    return {"isin": "B", "issuer_id": "I", "instrument_class": "BOND", "maturity_date": "2026-06-30",
            "issue_date": "2024-06-30", "coupon_pct": 8.0, "face_value_inr": 1e5, **kw}


def _trades(days, isin="B", y=8.0, volume=1e12):
    return [(d, isin, y, volume) for d in days]


def _config(**kw):
    return BacktestConfig(max_bond_weight=1.0, max_issuer_weight=1.0, **kw)


def test_buy_and_hold_reconciles_and_earns_the_yield(market_factory):
    days = pd.bdate_range("2025-01-01", "2026-12-31")
    md = market_factory([_bond()], _trades(days[::5][days[::5] < "2026-06-30"]))
    res = run_backtest(md, Hold(), config=_config(cash_rate_pct=0.0), execution=ExecutionModel(**FREE))
    nav = res.nav
    assert (nav["pnl"] - nav[COMPONENTS].sum(axis=1)).abs().max() < 1e-4
    hedged = nav["pnl"] - nav["pnl_rates"] + nav["pnl_funding"] + nav["pnl_hedge_cash"]
    assert (nav["pnl_hedged"] - hedged).abs().max() < 1e-4
    fill = res.fills.iloc[0]
    bought, matured = fill["date"], pd.Timestamp("2026-06-30")
    by_date = nav.set_index("date")
    # With no interest on cash, the position returns exactly its two remaining coupons and par.
    proceeds = by_date.loc[matured, "nav"] - by_date.loc[bought, "cash"]
    assert proceeds == pytest.approx(fill["face"] * (8.0 + 8.0 + 100.0) / 100)
    # and the price paid is those flows discounted at the traded 8% yield, so it earned 8% a year
    # on the purchase value up to the first coupon.
    first_coupon = pd.Timestamp("2025-06-30")
    before = by_date.loc[first_coupon - pd.offsets.BDay(1), "invested"]
    assert before == pytest.approx(fill["value_inr"] * 1.08 ** ((first_coupon - pd.offsets.BDay(1) - bought).days / 365),
                                   rel=1e-9)
    last = res.positions[res.positions["isin"] == "B"].iloc[-1]
    assert last["closed"] == "matured" and nav["n_positions"].iloc[-1] == 0
    assert nav["cash"].iloc[-1] == pytest.approx(nav["nav"].iloc[-1])
    # Coupons are income; the redemption shows as pull to par in roll.
    assert nav["pnl_income"].sum() > 0 and res.orders["status"].iloc[0] == "filled"


def test_orders_go_live_after_the_lag_and_fill_only_on_trade_days(market_factory):
    days = pd.bdate_range("2025-01-01", "2025-03-31")
    trade_days = [days[0], days[4], days[9]]
    md = market_factory([_bond()], _trades(trade_days), end="2025-03-31")
    res = run_backtest(md, Hold(), config=_config(), execution=ExecutionModel(lag_days=1, **FREE))
    assert res.fills["date"].tolist() == [days[4]]  # not days[0], the signal's own trade day
    res = run_backtest(md, Hold(), config=_config(),
                       execution=ExecutionModel(lag_days=1, trade_days_only=False, **FREE))
    assert res.fills["date"].iloc[0] == days[1] and not res.fills["on_trade_day"].iloc[0]


def test_participation_cap_spreads_a_buy_over_trade_days(market_factory):
    days = pd.bdate_range("2025-01-01", "2025-06-30")
    md = market_factory([_bond()], _trades(days, volume=1e8), end="2025-06-30")
    res = run_backtest(md, Hold(), config=_config(), execution=ExecutionModel(max_participation=0.25, **FREE))
    assert (res.fills["value_inr"] <= 0.25 * 1e8 + 1e5).all()
    assert len(res.fills) > 30
    assert res.fills["participation"].max() <= 0.25 + 1e-3


def test_fills_pay_half_spread_impact_and_stamp_duty(market_factory):
    days = pd.bdate_range("2025-01-01", "2025-02-28")
    md = market_factory([_bond()], _trades(days, volume=4e9), end="2025-02-28")
    ex = ExecutionModel(half_spread_bps={"AA": 10.0}, impact_bps=8.0, stamp_duty_pct=0.0001)
    res = run_backtest(md, Hold(), config=_config(), execution=ex)
    f = res.fills.iloc[0]
    assert f["cost_bps"] == pytest.approx(10.0 + 8.0 * f["participation"])
    assert f["fill_yield_pct"] == pytest.approx(f["mark_yield_pct"] - f["cost_bps"] / 100)
    mark = md.marks(["B"], f["date"]).iloc[0]
    expected = (f["fill_dirty"] - mark["dirty"]) * f["face"] / 100 + f["value_inr"] * 1e-6
    assert f["cost_inr"] == pytest.approx(expected)
    day = res.nav.set_index("date").loc[f["date"]]
    assert day["pnl_execution"] == pytest.approx(-f["cost_inr"])
    assert res.nav["cash"].min() >= -1e-6  # never borrows when max_gross is 1


def test_strategy_only_sees_the_past(market_factory):
    days = pd.bdate_range("2025-01-01", "2025-06-30")
    md = market_factory([_bond()], _trades(days[::3]), end="2025-06-30")
    events = pd.DataFrame({"event_id": [0, 1], "issuer_id": ["I", "I"], "instrument_class": "BOND",
                           "mandate": "m", "direction": "down", "date": ["2025-02-03", "2025-05-05"]})
    ownership = pd.DataFrame({"event_id": [0, 1], "constrained_share": [0.3, 0.4],
                              "baseline_to": ["2025-01-31", "2025-04-30"], "constrained_chg_30d": [-1.0, -1.0]})
    seen = []

    @dataclass
    class Spy(Strategy):
        rebalance: str = "D"

        def target_weights(self, ctx):
            snap = ctx.snapshot(30, 30)
            assert (snap["obs_date"] <= ctx.date).all()
            assert (ctx.events["date"] <= ctx.date).all()
            assert "constrained_chg_30d" not in ctx.events  # outcomes never reach a strategy
            seen.append((ctx.date, ctx.events["constrained_share"].tolist()))
            return pd.Series(dtype=float)

    run_backtest(md, Spy(), config=_config(disclosure_lag_days=10), events=events, ownership=ownership)
    by_date = dict(seen)
    assert np.isnan(by_date[pd.Timestamp("2025-02-07")][0])  # 31 Jan portfolio is published on 10 Feb
    assert by_date[pd.Timestamp("2025-02-10")] == [pytest.approx(0.3)]
    assert np.isnan(by_date[pd.Timestamp("2025-05-09")][1])
    assert by_date[pd.Timestamp("2025-05-12")][1] == pytest.approx(0.4)


def test_limits_and_rebalance_dates(market_factory):
    days = pd.bdate_range("2025-01-01", "2025-03-31")
    sec = [_bond(), _bond(isin="C", issuer_id="I"), _bond(isin="D", issuer_id="J")]
    trades = _trades(days) + _trades(days, "C") + _trades(days, "D")
    md = market_factory(sec, trades, end="2025-03-31")
    res = run_backtest(md, Hold(weights={"B": 0.5, "C": 0.5, "D": 0.9}),
                       config=BacktestConfig(max_bond_weight=0.4, max_issuer_weight=0.5), execution=ExecutionModel(**FREE))
    w = res.positions[res.positions["date"] == res.positions["date"].max()].set_index("isin")["weight"]
    assert w.groupby(md.securities["issuer_id"].reindex(w.index)).sum().max() <= 0.5 + 0.01
    assert w["D"] <= 0.4 + 0.01
    weekly = sorted(rebalance_dates(days, "W-FRI"))
    assert all(d.weekday() == 4 for d in weekly[:-1]) and weekly[-1] == days[-1]  # last, partial week
    assert sorted(rebalance_dates(days, "M")) == [pd.Timestamp("2025-01-31"), pd.Timestamp("2025-02-28"),
                                                  pd.Timestamp("2025-03-31")]


def test_a_run_that_never_trades_explains_itself(market_factory):
    days = pd.bdate_range("2025-01-01", "2025-03-31")
    md = market_factory([_bond(), _bond(isin="N", coupon_pct=None)], _trades(days), end="2025-03-31")
    res = run_backtest(md, Hold(weights={"N": 0.5, "X": 0.5}), config=_config())
    assert res.fills.empty
    notes = " ".join(res.notes)
    assert "targets for 1 bond(s) not in the security master were ignored: X" in notes
    assert "targets for 1 bond(s) without coupon or maturity terms were ignored: N" in notes
    assert "never traded" in notes
    from cr_strat.strategies import EventReversal
    res = run_backtest(md, EventReversal(), config=_config())
    assert any("rating events" in n for n in res.notes)


def test_inputs_as_paths_dicts_and_type_errors(market_factory, tmp_path):
    days = pd.bdate_range("2025-01-01", "2025-02-28")
    md = market_factory([_bond()], _trades(days), end="2025-02-28")
    pd.DataFrame({"date": ["2025-01-01"], "rate_pct": [5.0]}).to_csv(tmp_path / "treps.csv", index=False)
    pd.DataFrame(columns=["event_id", "issuer_id", "instrument_class", "mandate", "direction", "date"]).to_csv(
        tmp_path / "events.csv", index=False)

    @dataclass
    class Dict(Strategy):
        rebalance: str = "ME"  # pandas' month-end alias is accepted

        def target_weights(self, ctx):
            return {"B": 0.5}

    res = run_backtest(md, Dict(), config=_config(), events=tmp_path / "events.csv", cash_rates=tmp_path / "treps.csv")
    assert len(res.fills) and (res.nav["cash_rate_pct"] == 5.0).all() and Dict.name == "dict"

    @dataclass
    class Bad(Strategy):
        def target_weights(self, ctx):
            return ["B"]

    with pytest.raises(TypeError, match="pandas Series"):
        run_backtest(md, Bad(), config=_config())

    class NotADataclass(Strategy):
        rebalance = "M"

    with pytest.raises(TypeError, match="@dataclass"):
        run_backtest(md, NotADataclass(), config=_config())
    with pytest.raises(ValueError, match="rebalance must be"):
        run_backtest(md, Hold(rebalance="fortnightly"), config=_config())


def test_a_flat_book_earns_exactly_cash_in_both_series(market_factory):
    days = pd.bdate_range("2025-01-01", "2025-12-31")
    md = market_factory([_bond(maturity_date="2025-06-30")], _trades(days[days < "2025-06-27"]), end="2025-12-31")
    res = run_backtest(md, Hold(), config=_config(cash_rate_pct=6.0), execution=ExecutionModel(**FREE))
    nav = res.nav.set_index("date")
    after = nav[nav.index > "2025-07-15"]  # the bond has matured; both books are all cash
    assert (after["n_positions"] == 0).all()
    growth = after["nav"].pct_change().dropna(), after["nav_hedged"].pct_change().dropna()
    assert (growth[0] - growth[1]).abs().max() < 1e-15  # the hedged book earns cash on its own cash
    from cr_strat import analytics
    flat = analytics.performance_stats(after["nav_hedged"], cash_rate_pct=after["cash_rate_pct"])
    assert np.isnan(flat["sharpe"]) and np.isnan(flat["excess_t_stat_nw"])
