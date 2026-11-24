"""End-to-end tests of ``cr-strat risk`` and ``cr-strat var-backtest`` on synthetic files: the return engine's own
outputs (``cr-strat returns`` on two G-secs) and a history of NSS fits with public times. Synthetic data only
(risk_fixtures); every number here is made up."""

from datetime import date

import numpy as np
import pandas as pd
import pytest
import risk_fixtures as fx

from cr_strat import returns as rt
from cr_strat import risk as rk
from cr_strat import stress as st
from cr_strat.cli import main

END = date(2026, 11, 19)
N_FITS = 90  # business days of NSS fits to END: 89 changes
TERMS = {"GS2036": (6.94, "2036-05-11"), "GS2054": (7.09, "2054-08-05")}
BONDS = {"GS2036": fx.GS2036, "GS2054": fx.GS2054}


def _inputs(root):
    days = fx.history_days(END, N_FITS)
    g = fx.grid()
    entries = fx.nss_entries(days, level_moves_bp=[0.0] + [g[(7 * i) % 250] for i in range(N_FITS - 1)])
    pd.DataFrame([rt.nss_params_row(e.curve, d, public_from=e.public_from) for d, e in entries.items()]).to_csv(
        root / "nss.csv", index=False)
    run_days = [d for d in days if d >= date(2026, 11, 13)]
    marks = []
    for d in run_days:
        c = entries[d].curve
        for isin, bond in BONDS.items():
            y = fx.on_curve_yield(bond, c, c.valuation_date) + 0.03
            marks.append({"date": d, "isin": isin, "clean_price": bond.clean_price(y, c.valuation_date),
                          "volume_cr": 500.0 if isin == "GS2036" else 40.0,
                          "public_from": fx.at(d, 18).isoformat()})
    marks = pd.DataFrame(marks)
    marks.to_csv(root / "marks.csv", index=False)
    price = {(r["date"], r["isin"]): r["clean_price"] for r in marks.to_dict("records")}
    trades = pd.DataFrame([
        {"trade_id": "T1", "book": "b", "isin": "GS2036", "kind": "gsec", "coupon_pct": TERMS["GS2036"][0],
         "maturity_date": TERMS["GS2036"][1], "side": "buy", "face": 1e9, "trade_date": "2026-11-13",
         "price": price[(date(2026, 11, 13), "GS2036")], "liquidity": "liquid"},
        {"trade_id": "T2", "book": "c", "isin": "GS2054", "kind": "gsec", "coupon_pct": TERMS["GS2054"][0],
         "maturity_date": TERMS["GS2054"][1], "side": "buy", "face": 5e8, "trade_date": "2026-11-16",
         "price": price[(date(2026, 11, 16), "GS2054")], "liquidity": "liquid"}])
    trades.to_csv(root / "trades.csv", index=False)
    pd.DataFrame([{"date": "2026-11-13", "book": "b", "amount": 2e9},
                  {"date": "2026-11-13", "book": "c", "amount": 1e9}]).to_csv(root / "flows.csv", index=False)
    return run_days


@pytest.fixture(scope="module", params=["nbfc_non_pd", "unregulated_company"])
def engine(request, tmp_path_factory):
    """``(root, preset, run days)``: the inputs and ``cr-strat returns``' outputs under ``root/engine``."""
    root = tmp_path_factory.mktemp(f"risk_{request.param}")
    run_days = _inputs(root)
    code = main(["returns", "--trades", str(root / "trades.csv"), "--flows", str(root / "flows.csv"),
                 "--marks", str(root / "marks.csv"), "--nss-params", str(root / "nss.csv"),
                 "--access", request.param, "--end", END.isoformat(), "--out-dir", str(root / "engine")])
    assert code == 0
    return root, request.param, run_days


def _risk_args(root, preset, out, *extra):
    e = root / "engine"
    return ["risk", "--positions", str(e / "positions.csv"), "--nav", str(e / "nav.csv"), "--funding",
            str(e / "funding.csv"), "--trades", str(root / "trades.csv"), "--nss-params", str(root / "nss.csv"),
            "--marks", str(root / "marks.csv"), "--access", preset, "--min-obs-hard", "60", *extra,
            "--out-dir", str(out)]


def _read(path):
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def test_cli_risk_writes_every_table_with_its_documented_columns(engine, capsys):
    root, preset, _ = engine
    out = root / "out"
    assert main(_risk_args(root, preset, out, "--write-history")) == 0
    printed = capsys.readouterr().out
    assert "fund hs_sensitivity 1d: VaR 99.0%" in printed and "UNVERIFIED conventions listed in notes.txt" in printed
    assert "fund worst rates_irrbb:" in printed
    names = {p.name for p in out.iterdir()}
    assert names == {f"{n}.csv" for n in rk.RESULT_FILES} | {"factor_changes.csv", "notes.txt"}
    columns = {"position_risk": rk.POSITION_RISK_COLUMNS, "sensitivities": rk.SENSITIVITY_COLUMNS,
               "key_rates": rk.KEY_RATE_COLUMNS, "credit": rk.CREDIT_COLUMNS, "jtd": rk.JTD_COLUMNS,
               "risk_summary": rk.SUMMARY_COLUMNS, "var": rk.VAR_COLUMNS, "var_components": rk.VAR_COMPONENT_COLUMNS,
               "stress": st.STRESS_COLUMNS, "stress_positions": st.STRESS_POSITION_COLUMNS,
               "stress_worst": st.STRESS_WORST_COLUMNS, "liquidity": st.LIQUIDITY_COLUMNS,
               "liquidity_summary": st.LIQUIDITY_SUMMARY_COLUMNS, "funding_liquidity": st.FUNDING_LIQUIDITY_COLUMNS,
               "factor_changes": rk.FACTOR_CHANGE_COLUMNS}
    for name, cols in columns.items():
        assert list(_read(out / f"{name}.csv").columns) == list(cols), name
    # the parallel yield DV01 of each position is the engine's (I1)
    positions = pd.read_csv(root / "engine" / "positions.csv")
    mine = positions[positions["date"] == END.isoformat()].set_index("isin")["dv01"]
    pr = pd.read_csv(out / "position_risk.csv").set_index("isin")
    for isin in mine.index:
        assert pr.loc[isin, "dv01_yield"] == pytest.approx(mine[isin], rel=1e-9), isin
    # 89 changes of the fits public by 19 Nov 20:00; the window keeps them all, short of min_obs 250
    var = pd.read_csv(out / "var.csv")
    one_day = var[(var["scope_type"] == "fund") & (var["horizon_days"] == 1)]
    fund = one_day[one_day["alpha_var"] == 0.99].set_index("method")  # VaR 99%, ES 97.5%
    crossed = one_day[one_day["alpha_var"] == 0.975].set_index("method")  # VaR 97.5%, ES 99%
    assert set(fund.index) == set(crossed.index) == {"hs_sensitivity", "hs_full", "parametric"}
    assert (one_day["n_obs"] == 89).all() and (one_day["status"] == "short_history").all()
    assert (fund["var"] > 0).all() and (crossed["es"] >= fund["var"] - 1e-6).all()  # ES 99% >= VaR 99%
    assert (crossed["var"] <= fund["var"]).all()
    assert {"book", "fund"} <= set(var["scope_type"]) and {"b", "c"} <= set(var["scope"])
    notes = (out / "notes.txt").read_text()
    assert notes.count("UNVERIFIED") == len(rk.UNVERIFIED_RULES)
    funding = _read(out / "funding_liquidity.csv")
    if preset == "unregulated_company":
        assert (funding["applicable"] == "False").all() and funding["notes"].str.contains("not_applicable").all()
    else:
        assert (funding["applicable"] == "True").any()


def test_cli_risk_gives_the_same_bytes_twice_and_in_any_row_order(engine):
    root, preset, _ = engine
    assert main(_risk_args(root, preset, root / "one")) == 0
    assert main(_risk_args(root, preset, root / "two")) == 0
    shuffled = root / "shuffled"
    shuffled.mkdir(exist_ok=True)
    for name in ("positions.csv", "nav.csv", "funding.csv"):
        frame = _read(root / "engine" / name)
        frame.iloc[np.random.default_rng(7).permutation(len(frame))].to_csv(shuffled / name, index=False)
    nss = _read(root / "nss.csv")
    nss.iloc[::-1].to_csv(shuffled / "nss.csv", index=False)
    args = _risk_args(root, preset, root / "three")
    for name in ("positions.csv", "nav.csv", "funding.csv"):
        args[args.index(str(root / "engine" / name))] = str(shuffled / name)
    args[args.index(str(root / "nss.csv"))] = str(shuffled / "nss.csv")
    assert main(args) == 0
    for p in sorted((root / "one").iterdir()):
        assert p.read_bytes() == (root / "two" / p.name).read_bytes(), p.name
        assert p.read_bytes() == (root / "three" / p.name).read_bytes(), p.name


def test_cli_risk_as_of_an_earlier_time_sees_only_what_was_public(engine):
    root, preset, _ = engine
    assert main(_risk_args(root, preset, root / "late")) == 0
    assert main(_risk_args(root, preset, root / "early", "--as-of", "2026-11-19T18:30")) == 0
    late = pd.read_csv(root / "late" / "var.csv")
    early = pd.read_csv(root / "early" / "var.csv")
    pick = (late["scope_type"] == "fund") & (late["method"] == "hs_full") & (late["horizon_days"] == 1)
    assert int(late[pick]["n_obs"].iloc[0]) == 89
    pick = (early["scope_type"] == "fund") & (early["method"] == "hs_full") & (early["horizon_days"] == 1)
    assert int(early[pick]["n_obs"].iloc[0]) == 88  # 19 Nov's fit is public at 19:00
    assert (early["as_of"] == "2026-11-19T18:30").all()
    flags = pd.read_csv(root / "early" / "position_risk.csv")["flags"]
    assert flags.str.contains("stale_curve").all()  # 18 Nov's curve, the latest public at 18:30


def test_cli_risk_var_history_and_the_back_test(engine, capsys):
    root, preset, run_days = engine
    history = root / "var_history.csv"
    for d in run_days:
        assert main(_risk_args(root, preset, root / f"d{d:%m%d}", "--date", d.isoformat(), "--methods",
                               "hs_sensitivity,parametric", "--var-history", str(history))) == 0
    assert main(_risk_args(root, preset, root / "again", "--date", run_days[-1].isoformat(), "--methods",
                           "hs_sensitivity,parametric", "--var-history", str(history))) == 0  # replaces the day
    hist = _read(history)
    assert list(hist.columns) == list(rk.VAR_HISTORY_COLUMNS)
    assert sorted(set(hist["date"])) == [d.isoformat() for d in run_days]
    key = ["date", "scope_type", "scope", "method", "horizon_days", "alpha_var"]
    assert len(hist) == len(hist.drop_duplicates(key)) and set(hist["alpha_var"]) == {"0.99", "0.975"}
    capsys.readouterr()
    e = root / "engine"
    args = ["var-backtest", "--var-history", str(history), "--nav", str(e / "nav.csv"), "--attribution",
            str(e / "attribution.csv")]
    assert main([*args, "--out-dir", str(root / "bt")]) == 0
    printed = capsys.readouterr().out
    assert "fund fund hs_sensitivity:" in printed and "zone" in printed
    detail = pd.read_csv(root / "bt" / "backtest.csv")
    summary = pd.read_csv(root / "bt" / "backtest_summary.csv")
    assert list(detail.columns) == list(rk.BACKTEST_COLUMNS)
    assert list(summary.columns) == list(rk.BACKTEST_SUMMARY_COLUMNS)
    fund = detail[(detail["scope_type"] == "fund") & (detail["method"] == "hs_sensitivity")]
    assert list(fund["var_date"]) == [d.isoformat() for d in run_days[:-1]]  # each day's VaR against the next P&L
    assert list(fund["pnl_date"]) == [d.isoformat() for d in run_days[1:]]
    nav = pd.read_csv(e / "nav.csv")
    assert "fund" in set(nav["book"])  # the engine's own fund row
    by_day = nav[nav["book"] == "fund"].set_index("date")["pnl"]
    books = nav[nav["book"] != "fund"].groupby("date")["pnl"].sum()
    assert by_day[END.isoformat()] == pytest.approx(books[END.isoformat()], rel=1e-12)
    assert fund["pnl_actual"].tolist() == pytest.approx([by_day[d.isoformat()] for d in run_days[1:]], rel=1e-12)
    row = summary[(summary["scope_type"] == "fund") & (summary["method"] == "hs_sensitivity")].iloc[0]
    assert row["observations"] == len(run_days) - 1 and row["notes"].startswith("4 of 250 observations")
    assert row["zone"] == ("green" if row["exceptions"] == 0 else row["zone"])
    assert row["exceptions"] == max(row["exceptions_actual"], row["exceptions_hypothetical"])
    assert main([*args, "--method", "parametric", "--out-dir", str(root / "bt2")]) == 0
    assert set(pd.read_csv(root / "bt2" / "backtest.csv")["method"]) == {"parametric"}
    assert main([*args, "--end", "2026-11-13", "--out-dir", str(root / "bt3")]) == 1  # no pair by then
    assert main([*args[:2], str(root / "nope.csv"), *args[3:], "--out-dir", str(root / "bt4")]) == 2
    assert "not found" in capsys.readouterr().err


def test_cli_risk_refusals_and_exit_codes(engine, capsys):
    root, preset, _ = engine
    assert main(_risk_args(root, preset, root / "x1", "--date", "2026-11-12")) == 1
    assert "nothing to compute: no position on 2026-11-12" in capsys.readouterr().out
    bad = _risk_args(root, preset, root / "x2")
    bad[bad.index("--nss-params") + 1] = str(root / "missing.csv")
    assert main(bad) == 2
    assert "--nss-params" in capsys.readouterr().err
    assert main(_risk_args(root, preset, root / "x3", "--key-tenors", "1,ten")) == 2
    assert "--key-tenors" in capsys.readouterr().err
    assert main(_risk_args(root, preset, root / "x4", "--no-default-scenarios")) == 2
    assert "--no-default-scenarios needs --scenarios" in capsys.readouterr().err
    assert main(_risk_args(root, preset, root / "x5", "--date", "2026-11-13", "--as-of", "2026-11-12T20:00")) == 2
    capsys.readouterr()
    # a history shorter than min_obs_hard: the VaR rows say refused, the rest is written
    assert main(_risk_args(root, preset, root / "x6", "--min-obs-hard", "120")) == 0
    var = pd.read_csv(root / "x6" / "var.csv")
    assert (var["status"] == "refused").all() and var["var"].isna().all()
    assert var["flags"].str.contains("89 observation\\(s\\).*below min_obs_hard 120: refused").all()
    assert f"{len(var)} VaR figure(s) refused" in (root / "x6" / "notes.txt").read_text()
    assert len(pd.read_csv(root / "x6" / "stress.csv"))


def test_cli_risk_with_a_users_scenario_table_only(engine):
    root, preset, _ = engine
    mine = root / "my_scenarios.csv"
    pd.DataFrame([{"scenario_id": "MY_UP_50", "family": "rates", "component": "all_rate_curves",
                   "shape": "parallel", "size_bp": 50, "status": "U", "source": "the fund's own"}]).to_csv(
        mine, index=False)
    assert main(_risk_args(root, preset, root / "mine", "--scenarios", str(mine), "--no-default-scenarios",
                           "--methods", "parametric")) == 0
    stress = pd.read_csv(root / "mine" / "stress.csv")
    assert set(stress["scenario_id"]) == {"MY_UP_50"}
    total = stress[(stress["scope_type"] == "fund") & (stress["component"] == "total")]["pnl"].iloc[0]
    assert total < 0  # long bonds lose when rates rise
