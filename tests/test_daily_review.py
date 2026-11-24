"""Tests of the daily-process fixes of the risk layer's review: the configuration takes the risk options and peer
matrix ``cr-strat risk`` takes, the back-test pairs a strategy with its books' P&L, the report shows the fund's open
breaches, scope types, settlement cover and what the VaR leaves out, the funding table carries the share of NAV in
cash, T-bills and CP, and ``var-backtest`` takes the strategy map and the time-effects choice. Synthetic data only."""

from __future__ import annotations

import contextlib
import io
import math
from dataclasses import replace
from datetime import date

import pandas as pd
import pytest
import limits_fixtures as lf
import risk_fixtures as fx

from cr_strat import cli
from cr_strat import daily as dl
from cr_strat import returns as rt
from cr_strat import risk as rk
from cr_strat import stress as st
from cr_strat.bonds import DiscountInstrument
from cr_strat.conventions import CP

MATRIX = pd.DataFrame([{"rating_bucket": b, "tenor_years": t, "spread_bp": s}
                       for b, pts in (("AA", ((1.0, 100.0), (10.0, 140.0))), ("AA-", ((1.0, 180.0), (10.0, 220.0))))
                       for t, s in pts])
MAP = pd.DataFrame([{"book": "b", "strategy": "S1", "effective_from": "2026-11-13"},
                    {"book": "c", "strategy": "S8", "effective_from": "2026-11-13"}])


def _configure(root, inputs: str = "", risk: str = "") -> dl.DailyConfig:
    path = root / "config.toml"
    text = path.read_text(encoding="utf-8")
    text = text.replace("[inputs]\n", "[inputs]\n" + inputs)
    text = text.replace("min_obs_hard = 60\n", "min_obs_hard = 60\n" + risk)
    path.write_text(text, encoding="utf-8")
    return dl.DailyConfig.read(path)


@pytest.fixture(scope="module")
def mapped(tmp_path_factory):
    """Two days of the paper fund with books mapped to strategies S1 and S8, a peer matrix, the risk options and an
    issuer limit the NCD breaches on both days."""
    root = lf.paper_fund(tmp_path_factory.mktemp("paper_review"), limits="limits.csv")
    lf.tight_limits(root)
    MAP.to_csv(root / "strategy_books.csv", index=False)
    MATRIX.to_csv(root / "matrix.csv", index=False)
    cfg = _configure(root, 'strategy_books = "strategy_books.csv"\nspread_matrix = "matrix.csv"\n',
                     'quantile_rule = "ceil"\ngap_rule = "scale"\nmax_gap_days = 5\nsdl_mode = "spread"\n')
    r1 = dl.run_daily(cfg, lf.DAY1, orders=root / "orders.csv", out_dir=root)
    lf.append_approved(root, lf.DAY1)
    r2 = dl.run_daily(cfg, lf.DAY2, orders=None, out_dir=root)
    return root, cfg, r1, r2


def test_the_daily_configuration_takes_the_risk_options_of_cr_strat_risk(tmp_path):
    root = lf.paper_fund(tmp_path / "a")
    cfg = _configure(root, risk='residual = "in_fit"\nquantile_rule = "floor_plus_one"\ngap_rule = "drop"\n'
                                'max_gap_days = 3\nsdl_mode = "spread"\n')
    settings = dl._risk_settings(cfg)
    assert (settings.residual, settings.quantile_rule, settings.gap_rule, settings.max_gap_days) == \
        ("in_fit", "floor_plus_one", "drop", 3)
    bad = lf.paper_fund(tmp_path / "b")
    with pytest.raises(ValueError, match="residual"):
        dl._risk_settings(_configure(bad, risk='residual = "everything"\n'))


def test_the_daily_migration_stress_reads_the_peer_matrix(mapped):
    _, _, _, r2 = mapped
    rows = r2.risk.stress_positions
    got = rows[(rows["scenario_id"] == "DOWNGRADE_1_NOTCH") & (rows["isin"] == lf.NCD)]["pnl_spread"].iloc[0]
    _, _, _, ctx, rr = lf.engine_and_risk("unregulated_company")
    ncd = next(p for p in rr.book.positions if p.isin == lf.NCD)
    book = replace(rr.book, positions=(ncd,), swaps=())
    down = st.scenarios_by_id(st.read_scenarios())["DOWNGRADE_1_NOTCH"]
    with_matrix = st.apply_scenario(book, down, st.StressContext(risk=replace(ctx, spread_matrix=MATRIX)))[0]
    ladder = st.apply_scenario(book, down, st.StressContext(risk=ctx))[0]
    assert got == pytest.approx(with_matrix["pnl_spread"], rel=1e-9)  # AA to AA-: 80 bp from the matrix
    assert got < ladder["pnl_spread"] * 1.4  # the ladder's step is 50 bp


def test_the_daily_backtest_pairs_a_strategy_with_its_books_pnl(mapped):
    _, _, _, r2 = mapped
    bt = r2.backtest
    nav = r2.engine.nav
    for strategy, book in (("S1", "b"), ("S8", "c")):
        mine = bt[(bt["scope_type"] == "strategy") & (bt["scope"] == strategy)]
        assert len(mine) and not mine["missing"].astype(bool).any()  # was: every strategy day missing
        pnl = float(nav[(nav["book"] == book) & (nav["date"] == lf.DAY2)]["pnl"].iloc[0])
        assert mine["pnl_actual"].astype(float).tolist() == pytest.approx([pnl] * len(mine))
    summary = r2.backtest_summary
    strategies = summary[summary["scope_type"] == "strategy"]
    assert len(strategies) and not strategies["notes"].astype(str).str.contains("counted as exceptions").any()


def test_the_report_shows_open_breaches_scope_types_cover_and_what_the_var_leaves_out(mapped):
    _, _, _, r2 = mapped
    lines = r2.report.split("\n")
    top = lines.index("## Escalated and new breaches")
    nxt = lines.index("## NAV and P&L")
    still = lines.index("Still open from an earlier day (continuing):")
    assert top < still < nxt
    assert any(line.startswith("| X01 ") and "| continuing |" in line for line in lines[still:nxt])
    assert "| type | scope | method |" in r2.report  # book b and strategy S1 no longer look like duplicates
    assert "Funding liquidity: not applicable" in r2.report  # the unregulated preset does not borrow
    funding = lines.index("| book | purchases to settle | settlement cover | % NAV in cash, T-bills, CP |")
    assert lines[funding + 2].startswith("| b |") and lines[funding + 3].startswith("| c |")
    assert any(line.startswith("The VaR leaves out: carry and funding") for line in lines)


def test_funding_liquidity_gives_the_share_of_nav_in_cash_tbills_and_cp():
    day = fx.D13
    s = fx.CAL.settlement_date(day, 1)
    cp = DiscountInstrument(date(2027, 2, 15), CP)
    price = cp.price(7.0, s)
    cp_row = {**fx.bond_row(fx.GS2036, "CP27", "cp", 25e7, day, s, 7.0, liquidity=""), "clean_mark": price,
              "accrued": 0.0, "value": 25e7 / 100 * price, "dv01": 25e7 / 100 * cp.dv01(7.0, s)}
    gsec = fx.gs2036_positions(day, face=5e8).iloc[0].to_dict()
    master = pd.concat([fx.MASTER, pd.DataFrame([{"isin": "CP27", "kind": "cp", "maturity_date": "2027-02-15"}])])
    cash = 1e8
    nav_value = cash + cp_row["value"] + gsec["value"]
    nav = pd.DataFrame([{"date": day, "book": "b", "nav": nav_value, "cash": cash, "pnl": 0.0, "flow": 0.0}])
    curves = {"gsec": {day: rt.CurveEntry(fx.nss(s), fx.at(day))}}
    ctx = fx.gsec_context(day, market=rt.MarketData(curves=curves, master=master), nav=nav)
    book = rk.risk_book(pd.DataFrame([cp_row, gsec]), ctx, day=day)
    row = st.funding_liquidity(None, None, book, ctx).iloc[0]
    assert row["liquid_assets"] == pytest.approx(cash + cp_row["value"])  # the G-sec is not a liquid asset here
    assert row["liquid_asset_share"] == pytest.approx((cash + cp_row["value"]) / nav_value)
    assert not row["applicable"] and math.isnan(row["settlement_cover"])  # nothing to settle


def _main(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


def test_var_backtest_takes_the_strategy_map_and_the_time_effects(mapped, tmp_path):
    root, _, _, _ = mapped
    day = root / lf.DAY2.isoformat()
    args = ["var-backtest", "--var-history", day / "backtest" / "var_history.csv", "--nav", day / "engine" / "nav.csv",
            "--attribution", day / "engine" / "attribution.csv", "--strategy-books", root / "strategy_books.csv",
            "--project-calendar"]
    code, out, err = _main([*args, "--out-dir", tmp_path / "x"])
    assert code == 0, err
    summary = pd.read_csv(tmp_path / "x" / "backtest_summary.csv", keep_default_na=False)
    strategies = summary[summary["scope_type"] == "strategy"]
    assert set(strategies["scope"]) == {"S1", "S8"}
    assert not strategies["notes"].astype(str).str.contains("counted as exceptions").any()
    code, _, err = _main([*args, "--time-effects", "include", "--out-dir", tmp_path / "y"])
    assert code == 0, err
    hypo_x = pd.read_csv(tmp_path / "x" / "backtest.csv")["pnl_hypothetical"]
    hypo_y = pd.read_csv(tmp_path / "y" / "backtest.csv")["pnl_hypothetical"]
    assert not hypo_x.equals(hypo_y)  # the time lines (accrual, roll-down) are in the second only
    with pytest.raises(SystemExit) as stop:  # argparse refuses another choice, naming the two
        _main([*args, "--time-effects", "sometimes", "--out-dir", tmp_path / "z"])
    assert stop.value.code == 2
