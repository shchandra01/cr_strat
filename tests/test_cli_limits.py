"""The commands of the limits layer (``cr-strat limits``, ``pre-trade``, ``allocate`` and ``daily-report``) under both
access presets: exit codes, the EXAMPLE line first, and the same bytes as the daily process for the same inputs (one
implementation: ``limits`` and ``pre-trade`` read the directories ``cr-strat returns`` and ``cr-strat risk`` write,
which the daily process writes too). Synthetic inputs only (``tests/limits_fixtures.py``)."""

import contextlib
import filecmp
import io
import re
from datetime import date

import limits_fixtures as lf
import numpy as np
import pandas as pd
import pytest

from cr_strat import cli
from cr_strat import limits as lm
from cr_strat import portfolio as pf
from cr_strat.calendars import mumbai_settlement_calendar

PRESETS = ("unregulated_company", "nbfc_non_pd")


def _main(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


def _market_args(root):
    return ["--marks", root / "marks.csv", "--nss-params", root / "nss.csv", "--corporate-terms",
            root / "corporate_terms.csv", "--trades", root / "trades.csv", "--board-resolution-date", "2026-04-02",
            "--min-obs-hard", "60"]


@pytest.mark.parametrize("preset", PRESETS)
def test_daily_report_limits_and_pre_trade_agree_under_both_presets(tmp_path, preset):
    root = lf.paper_fund(tmp_path / "fund", preset)
    code, out, err = _main(["daily-report", "--config", root / "config.toml", "--date", "2026-11-18", "--orders",
                            root / "orders.csv", "--out-dir", root])
    assert code == 0, err
    lines = out.splitlines()
    assert lines[0] == lm.EXAMPLE_NOTE and lines[-1].startswith("wrote 39 files under ")
    assert "orders: 0 approved, 1 approved_with_warning, 2 refused" in lines
    day = root / "2026-11-18"
    code, out, err = _main(["limits", "--risk-dir", day / "risk", "--engine-dir", day / "engine", "--date",
                            "2026-11-18", "--limits", "example", "--access", preset, *_market_args(root),
                            "--out-dir", tmp_path / "limits"])
    assert code == 0, err
    assert out.splitlines()[0] == lm.EXAMPLE_NOTE
    for name in ("metrics", "limit_checks", "breaches", "breach_log"):
        assert filecmp.cmp(day / "limits" / f"{name}.csv", tmp_path / "limits" / f"{name}.csv", shallow=False), name
    checks = pd.read_csv(tmp_path / "limits" / "limit_checks.csv", dtype=str, keep_default_na=False)
    other = "nbfc_non_pd" if preset == "unregulated_company" else "unregulated_company"
    theirs = checks[checks["legal_form"] == other]  # the other form's rules: listed, never measured
    assert len(theirs) and set(theirs["status"]) == {"not_applicable"}
    assert theirs["detail"].str.contains(f"applies under {other}, not {preset}", regex=False).all()
    pre = ["pre-trade", "--orders", root / "orders.csv", "--engine-dir", day / "engine", "--date", "2026-11-18",
           "--limits", "example", "--access", preset, *_market_args(root), "--out-dir", tmp_path / "gate"]
    code, out, err = _main(pre)
    assert code == 2 and "refuses to run on EXAMPLE limits" in err and not (tmp_path / "gate").exists()
    code, out, err = _main([*pre, "--allow-example-limits"])
    assert code == 0, err
    assert out.splitlines()[0] == lm.EXAMPLE_NOTE
    assert filecmp.cmp(day / "orders" / "gate.csv", tmp_path / "gate" / "gate.csv", shallow=False)
    assert any(x.startswith("order 3 (b sell GS2054) refused: ") and "short" in x for x in out.splitlines())


def test_the_breach_log_is_carried_by_the_command_as_by_the_daily_process(tmp_path):
    root = lf.paper_fund(tmp_path / "fund")
    for day, extra in (("2026-11-18", ["--orders", root / "orders.csv"]), ("2026-11-19", [])):
        code, _, err = _main(["daily-report", "--config", root / "config.toml", "--date", day, *extra,
                              "--out-dir", root])
        assert code == 0, err
        if day == "2026-11-18":
            lf.append_approved(root, lf.DAY1)
    day2 = root / "2026-11-19"
    code, out, err = _main(["limits", "--risk-dir", day2 / "risk", "--engine-dir", day2 / "engine", "--date",
                            "2026-11-19", "--limits", "example", "--access", "unregulated_company",
                            *_market_args(root), "--breach-log", root / "2026-11-18" / "limits" / "breach_log.csv",
                            "--out-dir", tmp_path / "limits"])
    assert code == 0, err
    for name in ("metrics", "limit_checks", "breaches", "breach_log"):
        assert filecmp.cmp(day2 / "limits" / f"{name}.csv", tmp_path / "limits" / f"{name}.csv", shallow=False), name
    log = pd.read_csv(tmp_path / "limits" / "breach_log.csv", dtype=str, keep_default_na=False)
    assert "continuing" in set(log["status"])


def test_daily_report_refusals_exit_2_and_write_nothing(tmp_path):
    root = lf.paper_fund(tmp_path / "fund")
    base = ["daily-report", "--config", root / "config.toml", "--out-dir", root]
    for extra, message in ((["--date", "2026-11-21"], "not a Mumbai business day"),
                           (["--date", "2026-11-18", "--orders", root / "none.csv"], "--orders: .*none.csv not found"),
                           (["--date", "18/11/2026"], "--date")):
        code, out, err = _main([*base, *extra])
        assert code == 2 and re.search(message, err), (extra, err)
    code, _, err = _main(["daily-report", "--config", root / "none.toml", "--date", "2026-11-18", "--out-dir", root])
    assert code == 2 and "the daily configuration not found" in err
    assert not any(p.is_dir() for p in root.iterdir())
    code, _, err = _main(["limits", "--risk-dir", tmp_path / "no", "--engine-dir", tmp_path / "no", "--date",
                          "2026-11-18", "--limits", "example", "--access", "unregulated_company", "--out-dir",
                          tmp_path / "out"])
    assert code == 2 and err.startswith("error: ")


def _nav_file(path, days, books=("S1_carry", "S7_policy")):
    """Two synthetic strategy books' NAV over ``days`` business days (seeded normal returns, the second twice as
    volatile and correlated 0.3 with the first)."""
    cal = mumbai_settlement_calendar()
    dates = [date(2026, 8, 3)]
    while len(dates) < days:
        dates.append(cal.add_business_days(dates[-1], 1))
    rng = np.random.default_rng(7)
    z = rng.standard_normal((days - 1, 2))
    r = np.column_stack([0.002 * z[:, 0], 0.004 * (0.3 * z[:, 0] + np.sqrt(1 - 0.09) * z[:, 1])])
    rows = []
    for k, book in enumerate(books):
        nav = 1e9
        rows.append({"date": dates[0], "book": book, "nav": nav, "flow": 1e9})
        for t, d in enumerate(dates[1:]):
            nav *= 1 + r[t, k]
            rows.append({"date": d, "book": book, "nav": nav, "flow": 0.0})
    pd.DataFrame(rows).to_csv(path, index=False)
    return dates


def test_allocate_combines_two_books_by_erc_and_says_what_a_preset_forbids(tmp_path):
    dates = _nav_file(tmp_path / "nav.csv", 61)
    as_of = dates[-1].isoformat()
    args = ["allocate", "--nav", tmp_path / "nav.csv", "--as-of", as_of]
    code, out, err = _main([*args, "--method", "erc_vol", "--access", "nbfc_non_pd", "--out", tmp_path / "erc.csv",
                            "--correlation", tmp_path / "corr.csv"])
    assert code == 0, err
    erc = pd.read_csv(tmp_path / "erc.csv").set_index("strategy")
    assert erc["allowed"].all() and erc["weight"].sum() == pytest.approx(1.0, abs=1e-12)
    assert erc["risk_share"].to_numpy() == pytest.approx([0.5, 0.5], abs=1e-9)  # equal risk contributions
    code, _, err = _main([*args, "--method", "inverse_vol", "--access", "nbfc_non_pd", "--out", tmp_path / "iv.csv"])
    assert code == 0, err
    iv = pd.read_csv(tmp_path / "iv.csv").set_index("strategy")
    assert erc["weight"].to_numpy() == pytest.approx(iv["weight"].to_numpy(), abs=1e-9)  # two strategies: the same
    assert erc.loc["S1_carry", "weight"] > erc.loc["S7_policy", "weight"]  # the calmer book gets more
    corr = pd.read_csv(tmp_path / "corr.csv")
    assert list(corr.columns) == list(pf.CORRELATION_COLUMNS) and len(corr) == 4
    assert (corr.loc[corr["strategy"] == corr["other"], "correlation"] == 1.0).all()
    code, out, err = _main([*args, "--method", "erc_vol", "--access", "unregulated_company", "--out",
                            tmp_path / "unreg.csv"])
    assert code == 0, err
    unreg = pd.read_csv(tmp_path / "unreg.csv", keep_default_na=False).set_index("strategy")
    assert unreg.loc["S7_policy", "weight"] == 0 and str(unreg.loc["S7_policy", "allowed"]) == "False"
    assert "allows OIS for hedging only" in unreg.loc["S7_policy", "notes"]
    assert unreg.loc["S1_carry", "weight"] == pytest.approx(1.0, abs=1e-12)
    assert "S7_policy: weight 0.000000" in out and "not allowed" in out


def test_allocate_refuses_short_histories_and_missing_files(tmp_path):
    dates = _nav_file(tmp_path / "nav.csv", 6)
    code, _, err = _main(["allocate", "--nav", tmp_path / "nav.csv", "--as-of", dates[-1].isoformat(), "--method",
                          "erc_vol", "--access", "nbfc_non_pd", "--out", tmp_path / "a.csv"])
    assert code == 2 and "20" in err and not (tmp_path / "a.csv").exists()
    code, _, err = _main(["allocate", "--nav", tmp_path / "none.csv", "--as-of", "2026-11-18", "--method",
                          "inverse_vol", "--access", "nbfc_non_pd", "--out", tmp_path / "a.csv"])
    assert code == 2 and "--nav: " in err and "not found" in err
