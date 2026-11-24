"""The daily paper process (``daily``, D7 of the risk specification): the configuration and its refusals, a two-day run
and the D7.2 files, the report's first line and sections, the manifest's hashes, the breach log carried from one day to
the next, the reconciliation after a revised mark, an idempotent rerun, a shuffled limit table, both presets, and the
gate's refusal of EXAMPLE limits before anything is written. Synthetic inputs only (``tests/limits_fixtures.py``)."""

import hashlib
import json
import shutil
from datetime import date

import limits_fixtures as lf
import pandas as pd
import pytest

import cr_strat
from cr_strat import daily as dl
from cr_strat import limits as lm
from cr_strat import returns as rt
from cr_strat import risk as rk

D72 = ({f"engine/{n}.csv" for n in rt.RESULT_FILES} | {"engine/notes.txt"}
       | {f"risk/{n}.csv" for n in rk.RESULT_FILES} | {"risk/notes.txt"}
       | {f"limits/{n}.csv" for n in ("metrics", "limit_checks", "breaches", "breach_log")}
       | {f"orders/{n}.csv" for n in ("proposed", "gate", "approved_trades", "approved_swaps", "open_orders")}
       | {f"backtest/{n}.csv" for n in ("var_history", "backtest", "backtest_summary")}
       | {"reconciliation.csv", "report.md", "manifest.json"})
SECTIONS = ("## Escalated and new breaches", "## NAV and P&L", "## Rates: DV01 and key rates", "## Credit",
            "## VaR and ES", "## Stress (fund, worst first)", "## Liquidity and funding", "## Limits",
            "## Tomorrow's orders", "## Back-test", "## Reconciliation with the previous report", "## Data quality",
            "## UNVERIFIED rules used")


def _run(root, day, *, orders=True, config="config.toml"):
    cfg = dl.DailyConfig.read(root / config)
    return dl.run_daily(cfg, day, orders=root / "orders.csv" if orders else None, out_dir=root)


@pytest.fixture(scope="module")
def two_days(tmp_path_factory):
    """Day 1 with three proposed orders, the operator's append of the approved one, then day 2 without orders."""
    root = lf.paper_fund(tmp_path_factory.mktemp("paper"))
    r1 = _run(root, lf.DAY1)
    first = lf.files_under(root / lf.DAY1.isoformat())
    lf.append_approved(root, lf.DAY1)
    r2 = _run(root, lf.DAY2, orders=False)
    return root, r1, r2, first


# ---------------------------------------------------------------- the configuration


def test_the_configuration_is_read_and_checked(tmp_path):
    root = lf.paper_fund(tmp_path)
    cfg = dl.DailyConfig.read(root / "config.toml")
    assert (cfg.fund, cfg.access, cfg.inception, cfg.board_resolution_date) == (
        "paper", "unregulated_company", date(2026, 11, 13), date(2026, 4, 2))
    assert cfg.inputs["marks"] == root / "marks.csv" and cfg.allow_example_limits is True and cfg.limits == "example"
    assert cfg.risk == {"min_obs_hard": 60}
    assert cfg.sha256 == hashlib.sha256((root / "config.toml").read_bytes()).hexdigest()
    text = (root / "config.toml").read_text()
    cases = [
        (text.replace("access = ", "acces = "), ValueError, "unknown key 'acces' \\(did you mean 'access'\\?\\)"),
        (text.replace('access = "unregulated_company"\n', ""), ValueError, "access is required"),
        (text.replace('access = "unregulated_company"', 'access = "hedge_fund"'), ValueError,
         "no access preset 'hedge_fund'"),
        (text.replace('inception = "2026-11-13"\n', ""), ValueError, "inception .* is required"),
        (text.replace('marks = "marks.csv"', 'marks = "nowhere.csv"'), FileNotFoundError,
         "\\[inputs\\] marks: .*nowhere.csv not found"),
        (text.replace('marks = "marks.csv"', 'mark = "marks.csv"'), ValueError,
         "\\[inputs\\]: unknown key 'mark' \\(did you mean 'marks'\\?\\)"),
        (text.replace("min_obs_hard = 60", "min_obs_hrd = 60"), ValueError, "\\[risk\\]: unknown key 'min_obs_hrd'"),
        (text + "\n[gate]\nbuffer = 1\n", ValueError, "\\[gate\\]: unknown key 'buffer'"),
        (text.replace("allow_example_limits = true", 'allow_example_limits = "yes"'), ValueError,
         "allow_example_limits must be true or false"),
        (text.replace('limits = "example"', 'limits = "example"\ndecision_time = "8pm"'), ValueError,
         "decision_time '8pm' is not HH:MM"),
        (text.replace('limits = "example"', 'limits = "mine.csv"'), FileNotFoundError, "limits .*mine.csv not found"),
        ("access = [", ValueError, "is not valid TOML"),
    ]
    for body, error, message in cases:
        (root / "bad.toml").write_text(body, encoding="utf-8")
        with pytest.raises(error, match=message):
            dl.DailyConfig.read(root / "bad.toml")
    with pytest.raises(FileNotFoundError, match="the daily configuration not found"):
        dl.DailyConfig.read(root / "none.toml")
    with pytest.raises(ValueError, match="not a Mumbai business day"):
        _run(root, date(2026, 11, 21))  # a Saturday
    with pytest.raises(ValueError, match="before the paper book's inception 2026-11-13"):
        _run(root, date(2026, 11, 12))
    assert not any(p.is_dir() for p in root.iterdir())  # nothing written by a refused day


def test_example_limits_are_refused_before_anything_is_written(tmp_path):
    root = lf.paper_fund(tmp_path, allow_example=False)
    with pytest.raises(ValueError, match="refuses to run on EXAMPLE limits"):
        _run(root, lf.DAY1)
    assert not (root / lf.DAY1.isoformat()).exists()
    result = _run(root, lf.DAY1, orders=False)  # monitoring alone runs, every row marked EXAMPLE
    assert result.report.splitlines()[0] == "# Daily risk report: paper, 2026-11-18 (EXAMPLE limits)"
    example = result.checks[result.checks["policy_status"] == "EXAMPLE"]
    assert len(example) and example["detail"].str.startswith("EXAMPLE").all()
    assert dl.summary_lines(result)[0] == lm.EXAMPLE_NOTE and len(result.gate) == 0


# ---------------------------------------------------------------- two days of the paper process


def test_two_days_write_the_d72_files_and_the_report(two_days):
    root, r1, r2, first = two_days
    assert set(first) == D72
    assert set(lf.files_under(root / lf.DAY2.isoformat())) == D72
    lines = r1.report.splitlines()
    assert lines[0] == "# Daily risk report: paper, 2026-11-18 (EXAMPLE limits)"
    assert lines[2] == f"**{lm.EXAMPLE_NOTE}.**"
    assert "As of 2026-11-18 20:00 (India), access preset `unregulated_company`" in r1.report
    assert any(x.startswith("Limits in force: ") and "EXAMPLE" in x and "DRAFT" in x for x in lines)
    positions = [lines.index(s) for s in SECTIONS]
    assert positions == sorted(positions)  # every D7.2 section, in order
    assert "(no previous report)" in r1.report and "(no difference)" in r2.report
    assert (root / "2026-11-18" / "report.md").read_text(encoding="utf-8") == r1.report
    assert "Funding liquidity: not applicable" in r1.report  # the unregulated preset does not borrow


def test_the_manifest_hashes_every_input_and_counts_every_output(two_days):
    root, r1, _, first = two_days
    man = json.loads(first["manifest.json"])
    assert man == r1.manifest | {} and man["as_of"] == "2026-11-18T20:00" and man["access"] == "unregulated_company"
    assert man["package"] == {"name": "cr-strat", "version": cr_strat.__version__}
    assert man["config"]["path"] == "config.toml"
    assert set(man["inputs"]) == {"marks", "nss_params", "corporate_terms", "flows", "trades", "orders"}
    for key, entry in man["inputs"].items():
        data = (root / entry["path"]).read_bytes() if key != "trades" else None
        if data is not None:  # trades.csv was appended to after day 1
            assert entry["sha256"] == hashlib.sha256(data).hexdigest(), key
            assert entry["rows"] == data.count(b"\n") - 1, key
    assert set(man["outputs"]) == D72 - {"manifest.json"}
    for name, entry in man["outputs"].items():
        if name.endswith(".csv"):
            assert entry["rows"] == first[name].count(b"\n") - 1, name
    assert man["settings"]["allow_example_limits"] is True and man["settings"]["limits"] == "example"


def test_the_gate_writes_tomorrows_paper_trades(two_days):
    root, r1, r2, _ = two_days
    g = r1.gate
    decided = g.drop_duplicates("seq").set_index("seq")["decision"].to_dict()
    assert decided == {1: "approved_with_warning", 2: "refused", 3: "refused"}
    refused2 = g[g["seq"] == 2]
    blocking = refused2[~refused2["reason"].str.startswith(lm.WARNING_ONLY)]
    assert list(blocking["limit_id"]) == ["E09"] and "above the hard 8.0000% of NAV" in blocking["reason"].iloc[0]
    assert (refused2["reason"].str.startswith(lm.WARNING_ONLY)).sum() >= 1  # its warnings say they did not refuse
    short = g[g["seq"] == 3]
    assert len(short) == 1 and "allows no short sales (Short Sale Directions 2018 para 3)" in short["reason"].iloc[0]
    trades = pd.read_csv(root / "2026-11-18" / "orders" / "approved_trades.csv", dtype=str, keep_default_na=False)
    assert trades[["trade_id", "book", "isin", "side", "trade_date", "price"]].to_dict("records") == [
        {"trade_id": "P20261118-001", "book": "b", "isin": "GS2036", "side": "buy", "trade_date": "2026-11-19",
         "price": ""}]
    assert float(trades["face"].iloc[0]) == 1e8 and trades["kind"].iloc[0] == "gsec"
    assert len(pd.read_csv(root / "2026-11-18" / "orders" / "open_orders.csv")) == 1
    fills = r2.engine.fills
    mine = fills[fills["trade_id"].astype(str) == "P20261118-001"] if "trade_id" in fills else fills.iloc[0:0]
    assert len(mine) == 1 and str(mine["date"].iloc[0]) == "2026-11-19"  # the engine filled it at the day's mid
    summary = dl.summary_lines(r1)
    assert summary[0] == lm.EXAMPLE_NOTE
    assert [x for x in summary if x.startswith("refused order 2")] == [
        f"refused order 2 (c buy {lf.NCD}): {blocking['reason'].iloc[0]}"]


def test_the_breach_log_is_carried_and_the_var_history_grows(two_days):
    _, r1, r2, _ = two_days
    open1 = r1.breach_log[r1.breach_log["status"].isin(lm.OPEN_BREACHES)]
    open2 = r2.breach_log[r2.breach_log["status"].isin(lm.OPEN_BREACHES)]
    assert set(open1["limit_id"]) == set(open2["limit_id"]) and len(open1)
    assert set(open1["status"]) == {"new"} and set(open2["status"]) == {"continuing"}
    assert set(open2["first_date"].astype(str)) == {"2026-11-18"}
    assert set(open2["last_date"].astype(str)) == {"2026-11-19"}
    days = sorted(set(r2.var_history["date"].astype(str)))
    assert days == ["2026-11-18", "2026-11-19"] and len(r2.reconciliation) == 0


# ---------------------------------------------------------------- revisions, reruns and row order


def test_a_revised_mark_is_listed_never_absorbed(tmp_path):
    root = lf.paper_fund(tmp_path)
    _run(root, lf.DAY1, orders=False)
    marks = pd.read_csv(root / "marks.csv")
    hit = (marks["date"] == "2026-11-18") & (marks["isin"] == "GS2036")
    marks.loc[hit, "clean_price"] += 0.10  # the vendor's corrected file for day 1
    marks.to_csv(root / "marks.csv", index=False)
    result = _run(root, lf.DAY2, orders=False)
    rec = result.reconciliation
    assert list(rec.columns) == list(dl.RECONCILIATION_COLUMNS) and len(rec)
    assert set(rec["date"].astype(str)) == {"2026-11-18"} and {"positions", "nav"} <= set(rec["file"])
    marked = rec[(rec["file"] == "positions") & (rec["column"] == "clean_mark")]
    assert len(marked) == 1 and "GS2036" in marked["key"].iloc[0]
    assert float(marked["replayed"].iloc[0]) - float(marked["stored"].iloc[0]) == pytest.approx(0.10, abs=1e-9)
    assert "## Reconciliation with the previous report" in result.report and "(no difference)" not in result.report
    assert any("difference(s) with the previous report" in x for x in dl.summary_lines(result))


def test_a_rerun_writes_the_same_bytes_and_a_shuffled_limit_table_too(tmp_path):
    a = lf.paper_fund(tmp_path / "a", limits="mine.csv")
    shutil.copy(lm.EXAMPLE_LIMITS_CSV, a / "mine.csv")
    _run(a, lf.DAY1)
    once = lf.files_under(a / "2026-11-18")
    _run(a, lf.DAY1)
    assert lf.files_under(a / "2026-11-18") == once  # idempotent: the process never appends to its own inputs
    b = lf.paper_fund(tmp_path / "b", limits="mine.csv")
    pd.read_csv(lm.EXAMPLE_LIMITS_CSV, dtype=str, keep_default_na=False).sample(frac=1.0, random_state=11).to_csv(
        b / "mine.csv", index=False)
    _run(b, lf.DAY1)
    other = lf.files_under(b / "2026-11-18")
    assert {k: v for k, v in other.items() if k != "manifest.json"} == {
        k: v for k, v in once.items() if k != "manifest.json"}
    ma, mb = json.loads(once["manifest.json"]), json.loads(other["manifest.json"])
    assert ma["inputs"]["limits"]["sha256"] != mb["inputs"]["limits"]["sha256"]  # the shuffled file is not the same
    ma["inputs"].pop("limits"), mb["inputs"].pop("limits")
    assert ma == mb


def test_the_nbfc_preset_checks_its_own_rows(tmp_path):
    root = lf.paper_fund(tmp_path, preset="nbfc_non_pd")
    result = _run(root, lf.DAY1)
    checks = result.checks.set_index("limit_id")
    assert checks.loc["L09", "status"] == "ok" and checks.loc["L01", "status"] == "not_applicable"
    assert checks.loc["L09", "value"] > 1e8  # net owned fund (NAV, UNVERIFIED) above Rs 10 crore
    assert "applies under unregulated_company, not nbfc_non_pd" in checks.loc["L04", "detail"]
    assert result.report.splitlines()[0] == "# Daily risk report: paper, 2026-11-18 (EXAMPLE limits)"
    assert "access preset `nbfc_non_pd`" in result.report
    decided = result.gate.drop_duplicates("seq").set_index("seq")["decision"].to_dict()
    assert decided[3] == "refused"  # no short sale for a non-PD NBFC either (Short Sale Directions 2018 para 3(d))
