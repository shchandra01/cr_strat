"""Tests of policy_path: the MPC calendar, the step model of overnight MIBOR and the back-out of the implied path.

B10 and B12 are the analytics spec's vectors (19 Nov 2026); the quotes are synthetic, built from known steps.
"""

import json
import math
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from cr_strat import funding as fu
from cr_strat import ois
from cr_strat import policy_path as pp
from cr_strat.calendars import mumbai_settlement_calendar
from cr_strat.cli import main

CAL = mumbai_settlement_calendar()
LENIENT = CAL.lenient()
TRADE, EFFECTIVE = date(2026, 11, 19), date(2026, 11, 20)
DECISIONS = [date(2026, 12, 4), date(2027, 2, 5), date(2027, 4, 7), date(2027, 6, 4), date(2027, 8, 6),
             date(2027, 10, 6)]  # FY2026-27, then the spec's projected FY2027-28 dates
TRUE = [5.75, 5.75, 5.50, 5.50, 5.50, 5.50]
ENDS_A = [date(2027, 2, 5), date(2027, 4, 7), date(2027, 6, 4), date(2027, 8, 6), date(2027, 10, 6),
          date(2027, 11, 19)]
QUOTES_A = [5.829458, 5.882777, 5.844591, 5.834457, 5.839015, 5.847083]
QUOTES_B = [5.725875, 5.807408, 5.847467, 5.850912, 5.834602, 5.847732]
STANDARD = ["1M", "2M", "3M", "6M", "9M", "1Y"]


def modelled(quotes):
    return pp.par_rates_on_path(quotes, TRADE, current_policy_pct=5.50, levels=TRUE, meetings=DECISIONS,
                                spread_bp=10, calendar=LENIENT)["model_pct"].to_numpy()


def test_effective_dates_default_to_the_next_business_day():
    eff = pp.decision_effective_dates(DECISIONS, LENIENT)
    assert eff[:2] == [date(2026, 12, 7), date(2027, 2, 8)]  # Friday decisions take effect on Monday
    assert pp.decision_effective_dates(DECISIONS[:1], CAL, rule="same_day") == [date(2026, 12, 4)]
    with pytest.raises(ValueError, match="rule must be one of"):
        pp.decision_effective_dates(DECISIONS, CAL, rule="next_week")


def test_step_overnight():
    path = pp.step_overnight(date(2026, 12, 3), date(2026, 12, 9), base_pct=5.50, levels=[5.75],
                             effective_dates=[date(2026, 12, 7)], spread_bp=10, calendar=CAL)
    assert list(path["date"]) == [date(2026, 12, 3), date(2026, 12, 4), date(2026, 12, 7), date(2026, 12, 8)]
    assert list(path["days"]) == [1, 3, 1, 1] and list(path["overnight_pct"]) == pytest.approx([5.6, 5.6, 5.85, 5.85])
    with pytest.raises(ValueError, match="levels for"):
        pp.step_overnight(EFFECTIVE, date(2027, 1, 4), base_pct=5.5, levels=[5.75, 6.0], effective_dates=[EFFECTIVE],
                          spread_bp=0, calendar=LENIENT)


def test_quotes_on_the_known_path_match_the_spec_b10():
    assert np.abs(modelled(pd.DataFrame({"end_date": ENDS_A, "rate_pct": 0.0})) - QUOTES_A).max() < 1e-6
    assert np.abs(modelled(pd.DataFrame({"tenor": STANDARD, "rate_pct": 0.0})) - QUOTES_B).max() < 1e-6


def test_exactly_identified_quotes_recover_the_path_b10a():
    quotes = pd.DataFrame({"end_date": ENDS_A, "rate_pct": modelled(pd.DataFrame({"end_date": ENDS_A,
                                                                                 "rate_pct": 0.0}))})
    exact = pp.implied_policy_path(quotes, TRADE, current_policy_pct=5.50, meetings=DECISIONS, spread_bp=10,
                                   lam=0.0, calendar=LENIENT)
    assert np.abs(exact.path["level_pct"].to_numpy() - TRUE).max() * 100 < 1e-9
    assert np.round(exact.path["change_bp"].to_numpy(), 6).tolist() == [25, 0, -25, 0, 0, 0]
    assert exact.path["identified"].all() and exact.diagnostics["rank"] == 6 and exact.diagnostics["converged"]
    assert exact.path["move_25bp_share"].round(9).tolist() == [1, 0, -1, 0, 0, 0]
    smooth = pp.implied_policy_path(quotes, TRADE, current_policy_pct=5.50, meetings=DECISIONS, spread_bp=10,
                                    calendar=LENIENT)
    assert np.abs(smooth.path["level_pct"].to_numpy() - TRUE).max() * 100 < 1e-3  # the default penalty, 1e-6


def test_standard_tenors_need_the_penalty_and_say_what_they_pin_b10b():
    quotes = pd.DataFrame({"tenor": STANDARD, "rate_pct": modelled(pd.DataFrame({"tenor": STANDARD,
                                                                                "rate_pct": 0.0}))})
    r = pp.implied_policy_path(quotes, TRADE, current_policy_pct=5.50, meetings=DECISIONS, spread_bp=10,
                               calendar=LENIENT)
    assert r.quotes["error_bp"].abs().max() <= 4e-4
    assert np.abs(r.path["level_pct"].to_numpy() - TRUE).max() * 100 < 0.02
    # 1M and 2M see December, 3M February, 6M April; 9M sees June and August together, so they and October move
    # together: the quotes alone pin the first three levels only
    assert r.path["identified"].tolist() == [True, True, True, False, False, False] and r.diagnostics["rank"] == 5
    heavy = pp.implied_policy_path(quotes, TRADE, current_policy_pct=5.50, meetings=DECISIONS, spread_bp=10,
                                   lam=1e-2, calendar=LENIENT)
    assert heavy.quotes["error_bp"].abs().max() > 0.5  # the spec's run: changes smoothed to +25.18, -5.67, ...
    assert heavy.path["change_bp"].iloc[0] == pytest.approx(25.18, abs=0.01)


def _step_curve(levels, decisions, spread_bp, end):
    """An OIS curve whose overnight forward on every business day is the step path's rate (nodes on each day)."""
    eff = pp.decision_effective_dates(decisions, LENIENT)
    on = 5.50 + spread_bp / 100
    path = pp.step_overnight(EFFECTIVE, end, base_pct=5.50, levels=levels, effective_dates=eff, spread_bp=spread_bp,
                             calendar=LENIENT)
    log = [0.0, -math.log1p(on * (EFFECTIVE - TRADE).days / 36500)]
    dates = [TRADE, EFFECTIVE]
    for row in path.itertuples():
        log.append(log[-1] - math.log1p(row.overnight_pct * row.days / 36500))
        dates.append(row.next_date)
    return ois.OisCurve(valuation_date=TRADE, times=tuple((d - TRADE).days / 365 for d in dates), log_dfs=tuple(log),
                        node_dates=tuple(dates), calendar=LENIENT)


def test_a_synthetic_ois_curve_with_known_steps_gives_its_steps_back():
    decisions = [*DECISIONS, date(2027, 12, 3), date(2028, 4, 7)]
    levels = [5.75, 5.75, 5.50, 5.50, 5.50, 5.50, 5.25, 5.25]
    curve = _step_curve(levels, decisions, 12.0, date(2029, 1, 31))
    tenors = [*STANDARD, "18M", "2Y"]
    quotes = pd.DataFrame({"tenor": tenors, "rate_pct": [ois.ois_par_rate(curve, EFFECTIVE, t) for t in tenors]})
    # these are the curve's only step dates, so the list is declared complete to the 2Y quote's end
    r = pp.implied_policy_path(quotes, TRADE, current_policy_pct=5.50, meetings=decisions, spread_bp=12.0, lam=0.0,
                               calendar=LENIENT, covered_until=date(2028, 11, 30))
    # the back-out's par formulas (single period and semi-annual) agree with ois.ois_par_rate on the step curve
    assert r.quotes["error_bp"].abs().max() < 1e-8
    assert r.quotes["quote_kind"].tolist()[-2:] == ["semiannual", "semiannual"]
    # the quotes pin December, February and April, and (by the 2Y) April 2028; June to December 2027 move together
    identified = r.path["identified"].to_numpy()
    assert identified.tolist() == [True, True, True, False, False, False, False, True] and r.diagnostics["rank"] == 7
    assert r.path["level_pct"].to_numpy()[identified] == pytest.approx(np.array(levels)[identified], abs=1e-6)


def test_meetings_after_the_last_quote_have_no_level_and_earlier_ones_are_known():
    quotes = pd.DataFrame({"tenor": ["1M", "3M"], "rate_pct": modelled(pd.DataFrame({"tenor": ["1M", "3M"],
                                                                                    "rate_pct": 0.0}))})
    r = pp.implied_policy_path(quotes, TRADE, current_policy_pct=5.50, meetings=[date(2026, 10, 7), *DECISIONS],
                               spread_bp=10, lam=0.0, calendar=LENIENT)
    assert r.path["decision_date"].tolist()[0] == date(2026, 12, 4)  # 7 Oct 2026 is already in the current rate
    assert r.path["in_horizon"].tolist() == [True, True, False, False, False, False]
    assert r.path["level_pct"].iloc[:2].to_numpy() == pytest.approx([5.75, 5.75], abs=1e-6)


def test_policy_path_is_deterministic():
    quotes = pd.DataFrame({"tenor": STANDARD, "rate_pct": QUOTES_B})
    runs = [pp.implied_policy_path(q, TRADE, current_policy_pct=5.50, meetings=DECISIONS, spread_bp=10,
                                   calendar=LENIENT) for q in (quotes, quotes.iloc[::-1])]
    assert runs[0].path.equals(runs[1].path) and runs[0].quotes.equals(runs[1].quotes)
    assert runs[0].diagnostics == runs[1].diagnostics


def test_policy_path_refusals():
    quotes = pd.DataFrame({"tenor": STANDARD, "rate_pct": QUOTES_B})
    with pytest.raises(ValueError, match="lam must be zero or more"):
        pp.implied_policy_path(quotes, TRADE, current_policy_pct=5.5, meetings=DECISIONS, spread_bp=10, lam=-1,
                               calendar=LENIENT)
    with pytest.raises(ValueError, match="not end on a business day"):
        pp.implied_policy_path(pd.DataFrame({"end_date": [date(2027, 2, 6)], "rate_pct": [5.8]}), TRADE,
                               current_policy_pct=5.5, meetings=DECISIONS, spread_bp=10, calendar=LENIENT)
    with pytest.raises(ValueError, match="longer than a single period"):
        pp.implied_policy_path(pd.DataFrame({"end_date": [date(2028, 2, 7)], "rate_pct": [5.8]}), TRADE,
                               current_policy_pct=5.5, meetings=DECISIONS, spread_bp=10, calendar=LENIENT)
    with pytest.raises(ValueError, match="listed twice"):
        pp.implied_policy_path(quotes, TRADE, current_policy_pct=5.5, meetings=[*DECISIONS, DECISIONS[0]],
                               spread_bp=10, calendar=LENIENT)
    with pytest.raises(ValueError, match="quote ending 2027-02-05 is missing"):
        pp.implied_policy_path(pd.DataFrame({"end_date": [date(2027, 2, 5)], "rate_pct": [float("nan")]}), TRADE,
                               current_policy_pct=5.5, meetings=DECISIONS, spread_bp=10, calendar=LENIENT)


# ---------------------------------------------------------------- the MPC file


def test_the_shipped_mpc_file_b12():
    m = pp.load_mpc_calendar()
    assert list(m.columns) == list(pp.MEETING_COLUMNS) and len(m) == 6
    assert m["decision_date"].tolist() == [date(2026, 4, 8), date(2026, 6, 5), date(2026, 8, 5), date(2026, 10, 7),
                                           date(2026, 12, 4), date(2027, 2, 5)]
    assert all(LENIENT.is_business_day(d) for d in m["decision_date"])
    assert m["confirmed"].tolist() == [True] * 5 + [False] and set(m["fy"]) == {"2026-27"}
    assert all("prid=62422" in s for s in m["source"])
    with pytest.raises(ValueError, match="project=True"):
        pp.load_mpc_calendar(until=date(2027, 11, 22))
    projected = pp.load_mpc_calendar(until=date(2027, 11, 22), project=True)
    extra = projected[projected["projected"]]
    assert extra["decision_date"].tolist() == [date(2027, 4, 7), date(2027, 6, 4), date(2027, 8, 4), date(2027, 10, 6),
                                               date(2027, 12, 3), date(2028, 2, 4)]
    assert not extra["confirmed"].any() and extra["source"].str.startswith("UNVERIFIED").all()
    assert len(pp.load_mpc_calendar(include_unconfirmed=False)) == 5


def test_policy_changes_are_matched_with_decisions_b12():
    policy = pd.DataFrame({"effective_date": ["2025-12-05", "2026-10-07", "2026-11-02", "2026-12-07", "2027-01-15"],
                           "repo_pct": [5.25, 5.50, 5.75, 6.00, 6.00], "sdf_pct": [5.0, 5.25, 5.5, 5.75, 5.70],
                           "msf_pct": [5.5, 5.75, 6.0, 6.25, 6.30], "source": ["x"] * 5})
    check = pp.check_policy_changes(policy, pp.load_mpc_calendar()).set_index("effective_date")
    assert check.loc[date(2026, 10, 7), "status"] == "decision_day"
    assert check.loc[date(2026, 10, 7), "decision_date"] == date(2026, 10, 7)
    assert check.loc[date(2026, 10, 7), "change_bp"] == pytest.approx(25)
    assert check.loc[date(2026, 11, 2), "status"] == "no_meeting"
    assert check.loc[date(2026, 12, 7), "status"] == "day_after_decision"  # dated a business day late, as BIS does
    assert date(2025, 12, 5) not in check.index  # the first row has no change to compare
    assert date(2027, 1, 15) not in check.index  # the corridor alone moved


def _write_mpc(tmp_path, rows, years=("2026-27,yes,x",)):
    (tmp_path / "m.csv").write_text("meeting_start,decision_date,fy,kind,announced,source,confirmed\n"
                                    + "\n".join(rows) + "\n")
    (tmp_path / "m_years.csv").write_text("fy,complete,source\n" + "\n".join(years) + "\n")
    return tmp_path / "m.csv"


@pytest.mark.parametrize("row, message", [
    ("2026-12-05,2026-12-04,2026-27,scheduled,2026-03-23,x,yes", "within a week after the meeting starts"),
    ("2026-12-02,2026-12-04,2025-26,scheduled,2026-03-23,x,yes", "falls in 2026-27, not 2025-26"),
    ("2026-12-02,2026-12-04,2026-27,emergency,2026-03-23,x,yes", "kind must be one of"),
    ("2026-12-02,2026-12-04,2026-27,scheduled,2026-03-23,,yes", "source is empty"),
    ("2026-12-02,2026-12-04,2026-27,scheduled,2026-03-23,x,maybe", "must be yes or no"),
    ("2026-12-02,04-12-2026,2026-27,scheduled,2026-03-23,x,yes", "must be dates"),
    ("2027-04-05,2027-04-07,2027-28,scheduled,2027-03-20,x,yes", "no line in"),
    ("2026-12-02,2026-12-04,2026-27,scheduled,,x,yes", "announced must be the date"),
    ("2026-12-02,2026-12-04,2026-27,unscheduled,2026-12-05,x,yes", "announced 2026-12-05 is after the decision"),
])
def test_mpc_file_refusals(tmp_path, row, message):
    with pytest.raises(ValueError, match=message):
        pp.load_mpc_calendar(_write_mpc(tmp_path, [row]))


def test_mpc_file_refuses_rows_of_an_incomplete_year_and_duplicates(tmp_path):
    rows = ["2026-12-02,2026-12-04,2026-27,scheduled,2026-03-23,x,yes"]
    with pytest.raises(ValueError, match="not declared complete"):
        pp.load_mpc_calendar(_write_mpc(tmp_path, rows, years=("2026-27,no,x",)))
    with pytest.raises(ValueError, match="listed twice"):
        pp.load_mpc_calendar(_write_mpc(tmp_path, rows * 2))
    with pytest.raises(ValueError, match="declares 2027-28 complete, but .* lists no meeting in it"):
        pp.load_mpc_calendar(_write_mpc(tmp_path, rows, years=("2026-27,yes,x", "2027-28,yes,x")))
    unscheduled = pp.load_mpc_calendar(_write_mpc(tmp_path, [*rows, "2026-05-04,2026-05-04,2026-27,unscheduled,"
                                                                     "2026-05-01,x,yes"]))
    assert unscheduled["kind"].tolist() == ["unscheduled", "scheduled"]


# ---------------------------------------------------------------- the MIBOR-repo spread


def call_rates(start, end, rate):
    rows, d = [], start
    while d <= end:
        business = CAL.is_business_day(d)
        nxt = CAL.add_business_days(d, 1)
        rows.append({"as_on_date": d, "call_rate_pct": rate(d) if business else None,
                     "call_volume_cr": 1e4 if business else 0.0,
                     "public_from": datetime(nxt.year, nxt.month, nxt.day, 9 if business else 12)})
        d += timedelta(days=1)
    return fu.funding_rates(pd.DataFrame(rows), "call")


POLICY = pd.DataFrame({"effective_date": ["2025-12-05", "2026-10-07"], "repo_pct": [5.25, 5.50]})


def test_spread_is_the_median_of_public_days_in_the_current_regime():
    rates = call_rates(date(2026, 9, 1), date(2026, 11, 20), lambda d: 5.50 + (d.day % 5) / 100)
    est = pp.mibor_repo_spread(rates, POLICY, TRADE, calendar=CAL)
    # 18 Nov's rate is public only at 9:00 on 19 Nov: by the start of 19 Nov the last usable day is 17 Nov
    assert est.last == date(2026, 11, 17) and est.n_obs == 20 and est.regime_start == date(2026, 10, 8)
    expected = [(d.day % 5) for d in CAL.business_days(date(2026, 10, 8), date(2026, 11, 17))][-20:]
    assert est.spread_bp == pytest.approx(float(np.median(expected)), abs=1e-9)
    later = pp.mibor_repo_spread(rates, POLICY, TRADE, as_of=datetime(2026, 11, 19, 17, 45), calendar=CAL)
    assert later.last == date(2026, 11, 18)
    short = pp.mibor_repo_spread(rates, POLICY, date(2026, 10, 12), as_of=datetime(2026, 10, 12, 18), calendar=CAL)
    assert (short.first, short.n_obs) == (date(2026, 10, 8), 2)  # 7 Oct mixes both rates: the regime starts the 8th
    assert short.fallback == "" and short.note == ""
    with pytest.raises(ValueError, match="0 usable day"):
        pp.mibor_repo_spread(rates, POLICY, date(2026, 10, 8), calendar=CAL, fallback=None)


def test_spread_from_a_mibor_series_without_publication_times_uses_fbils():
    frame = pd.DataFrame({"date": CAL.business_days(date(2026, 11, 2), date(2026, 11, 19)), "rate_pct": 5.62})
    est = pp.mibor_repo_spread(frame, POLICY, TRADE, calendar=CAL)
    assert est.last == date(2026, 11, 18) and est.spread_bp == pytest.approx(12.0)  # published 12:45 on the day


# ---------------------------------------------------------------- command line


def test_cli_policy_path(tmp_path, capsys):
    quotes = pd.DataFrame({"tenor": STANDARD, "rate_pct": QUOTES_B})
    quotes.to_csv(tmp_path / "q.csv", index=False)
    args = ["policy-path", "--quotes", str(tmp_path / "q.csv"), "--trade-date", "2026-11-19", "--policy-rate", "5.50",
            "--spread-bp", "10", "--project-calendar"]
    assert main([*args, "--out-dir", str(tmp_path / "x")]) == 2
    assert "project=True" in capsys.readouterr().err  # the 1Y quote reaches into FY2027-28
    assert main([*args, "--project", "--out-dir", str(tmp_path / "out")]) == 0
    printed = capsys.readouterr().out
    assert "2026-12-04: 5.7500% (+25.00 bp)" in printed and "projected" in printed
    path = pd.read_csv(tmp_path / "out" / "path.csv")
    assert len(path) == 8 and path["in_horizon"].sum() == 6
    diagnostics = json.loads((tmp_path / "out" / "diagnostics.json").read_text())
    assert diagnostics["spread_bp"] == 10 and diagnostics["calendar_projected"]
    # the spread estimated from an RBI store's call rate, with the policy table
    store = tmp_path / "store"
    (store / "derived").mkdir(parents=True)
    rates = call_rates(date(2026, 10, 1), date(2026, 11, 18), lambda d: 5.63)
    frame = rates.rename(columns={"date": "as_on_date", "rate_pct": "call_rate_pct", "volume_cr": "call_volume_cr"})
    frame.assign(public_from=frame["public_from"].map(lambda t: t.strftime("%Y-%m-%dT%H:%M:%S"))).to_csv(
        store / "derived" / "money_market_daily.csv", index=False)
    POLICY.to_csv(tmp_path / "policy.csv", index=False)
    est = ["policy-path", "--quotes", str(tmp_path / "q.csv"), "--trade-date", "2026-11-19", "--project",
           "--project-calendar", "--store", str(store), "--policy-rates", str(tmp_path / "policy.csv")]
    assert main([*est, "--out-dir", str(tmp_path / "est")]) == 0
    assert "MIBOR-repo spread +13.00 bp" in capsys.readouterr().out
    diagnostics = json.loads((tmp_path / "est" / "diagnostics.json").read_text())
    assert diagnostics["current_policy_pct"] == 5.50 and diagnostics["spread_estimate"]["last"] == "2026-11-17"
    assert main([*args[:-3], "--project", "--out-dir", str(tmp_path / "none")]) == 2
    assert "give --spread-bp" in capsys.readouterr().err


# ---------------------------------------------------------------- what is known on the trade date


EVE = date(2026, 12, 3)  # the business day before the 4 Dec 2026 decision


def _eve_quotes(rule):
    """1M-1Y quotes on 3 Dec 2026 from a path with +25 bp at the 4 Dec decision (MIBOR = repo + 10 bp)."""
    meetings = pp.load_mpc_calendar(until=date(2027, 12, 31), project=True, calendar=LENIENT)
    levels = [5.75 if d >= date(2026, 12, 4) else 5.50 for d in meetings["decision_date"]]
    tenors = pd.DataFrame({"tenor": STANDARD, "rate_pct": 0.0})
    model = pp.par_rates_on_path(tenors, EVE, current_policy_pct=5.50, levels=levels, meetings=meetings, spread_bp=10,
                                 calendar=LENIENT, effective_rule=rule)
    return meetings, tenors.assign(rate_pct=model["model_pct"].to_numpy())


@pytest.mark.parametrize("rule", pp.EFFECTIVE_RULES)
def test_the_eve_of_a_decision_backs_out_the_coming_move_from_the_rate_known_that_day(rule):
    meetings, quotes = _eve_quotes(rule)
    r = pp.implied_policy_path(quotes, EVE, current_policy_pct=5.50, meetings=meetings, spread_bp=10, calendar=LENIENT,
                               effective_rule=rule)
    first = r.path.iloc[0]
    # the 4 Dec decision is a step even under same_day, where it takes effect on the swaps' effective date (4 Dec)
    assert first["decision_date"] == date(2026, 12, 4)
    assert first["effective_date"] == (date(2026, 12, 4) if rule == "same_day" else date(2026, 12, 7))
    assert first["change_bp"] == pytest.approx(25.0, abs=1e-3) and first["identified"]
    assert r.path["change_bp"].iloc[1:4].abs().max() < 1e-3 and r.quotes["error_bp"].abs().max() < 1e-3


def test_cli_policy_path_takes_the_rate_known_on_the_trade_date(tmp_path, capsys):
    _, quotes = _eve_quotes("next_business_day")
    quotes.to_csv(tmp_path / "q.csv", index=False)
    # the policy table dates the hike on the decision day, the business day after the trade date
    pd.DataFrame({"effective_date": ["2026-10-07", "2026-12-04"], "repo_pct": [5.50, 5.75]}).to_csv(
        tmp_path / "policy.csv", index=False)
    args = ["policy-path", "--quotes", str(tmp_path / "q.csv"), "--trade-date", "2026-12-03", "--policy-rates",
            str(tmp_path / "policy.csv"), "--spread-bp", "10", "--project", "--project-calendar"]
    assert main([*args, "--out-dir", str(tmp_path / "out")]) == 0
    printed = capsys.readouterr().out
    assert "2026-12-04: 5.7500% (+25.00 bp)" in printed  # not -1.93 bp from a base of the 4 Dec decision
    assert "dates a repo-rate change on 2026-12-04, after the trade date: not used" in printed
    diagnostics = json.loads((tmp_path / "out" / "diagnostics.json").read_text())
    assert diagnostics["current_policy_pct"] == 5.50 and diagnostics["max_abs_error_bp"] < 1e-3
    # on the decision day itself the table's row of that day is the rate known
    assert main([*args[:4], "2026-12-04", *args[5:], "--out-dir", str(tmp_path / "day")]) == 0
    assert "2026-12-04 is an MPC decision day: the starting rate 5.75%" in capsys.readouterr().out
    assert json.loads((tmp_path / "day" / "diagnostics.json").read_text())["current_policy_pct"] == 5.75
    # a table dating that decision a business day late (BIS style) would start the path from 5.50%: refused
    pd.DataFrame({"effective_date": ["2026-10-08", "2026-12-07"], "repo_pct": [5.50, 5.75]}).to_csv(
        tmp_path / "bis.csv", index=False)
    bis = [*args[:4], "2026-12-04", "--policy-rates", str(tmp_path / "bis.csv"), *args[7:]]
    assert main([*bis, "--out-dir", str(tmp_path / "bis")]) == 2
    err = capsys.readouterr().err
    assert "dates a repo-rate change to 5.75% on 2026-12-07, the next business day" in err and "--policy-rate" in err
    given = [*args[:4], "2026-12-04", "--policy-rate", "5.75", *args[7:]]
    assert main([*given, "--out-dir", str(tmp_path / "given")]) == 0
    assert json.loads((tmp_path / "given" / "diagnostics.json").read_text())["current_policy_pct"] == 5.75


def test_meetings_must_cover_the_quotes():
    quotes = pd.DataFrame({"tenor": STANDARD, "rate_pct": QUOTES_B})
    shipped = pp.load_mpc_calendar()  # FY2026-27 only: complete to 31 Mar 2027
    assert shipped.attrs["covered_until"] == date(2027, 3, 31)
    with pytest.raises(ValueError, match="the quotes run to 2027-11-22, past 2027-03-31"):
        pp.implied_policy_path(quotes, TRADE, current_policy_pct=5.5, meetings=shipped, spread_bp=10, calendar=LENIENT)
    with pytest.raises(ValueError, match="past 2027-03-31"):
        pp.par_rates_on_path(quotes, TRADE, current_policy_pct=5.5, levels=[5.5] * 6, meetings=shipped, spread_bp=10,
                             calendar=LENIENT)
    # a plain list of dates counts as complete for MPC_GAP_DAYS after its last decision
    dates = list(shipped["decision_date"])
    with pytest.raises(ValueError, match="past 2027-04-16"):
        pp.implied_policy_path(quotes, TRADE, current_policy_pct=5.5, meetings=dates, spread_bp=10, calendar=LENIENT)
    short = quotes[quotes["tenor"].isin(["1M", "2M", "3M"])]  # to 22 Feb 2027: inside FY2026-27
    r = pp.implied_policy_path(short, TRADE, current_policy_pct=5.5, meetings=shipped, spread_bp=10, calendar=LENIENT)
    assert r.diagnostics["meetings_covered_until"] == "2027-03-31"
    projected = pp.load_mpc_calendar(until=date(2027, 11, 30), project=True)
    assert projected.attrs["covered_until"] == date(2028, 3, 31)
    assert pp.implied_policy_path(quotes, TRADE, current_policy_pct=5.5, meetings=projected, spread_bp=10,
                                  calendar=LENIENT).diagnostics["converged"]


def test_coverage_is_checked_against_the_meetings_actually_given():
    quotes = pd.DataFrame({"tenor": STANDARD, "rate_pct": QUOTES_B})
    kw = {"current_policy_pct": 5.5, "spread_bp": 10, "calendar": LENIENT}
    full = pp.load_mpc_calendar(until=date(2027, 12, 31), project=True)
    assert full.attrs["decision_dates"] == tuple(full["decision_date"]) and full.attrs["left_out"] == ()
    # a filtered copy keeps attrs, covered_until 2028-03-31 among them: its own dates end the coverage
    cut = full[full["decision_date"] <= date(2027, 2, 5)]
    assert cut.attrs["covered_until"] == date(2028, 3, 31)
    with pytest.raises(ValueError, match="past 2027-04-06, .*meeting of 2027-04-07 is in the calendar as loaded"):
        pp.implied_policy_path(quotes, TRADE, meetings=cut, **kw)
    short = quotes[quotes["tenor"].isin(["1M", "2M", "3M"])]  # to 22 Feb 2027
    assert pp.implied_policy_path(short, TRADE, meetings=cut,
                                  **kw).diagnostics["meetings_covered_until"] == "2027-04-06"
    # include_unconfirmed=False drops 5 Feb 2027: the MPC still meets then, so the meetings are complete to 4 Feb
    conf = pp.load_mpc_calendar(until=date(2027, 12, 31), project=True, include_unconfirmed=False)
    assert conf.attrs["left_out"] == (date(2027, 2, 5),) and date(2027, 2, 5) not in set(conf["decision_date"])
    with pytest.raises(ValueError, match="past 2027-02-04, .*2027-02-05 is left out as unconfirmed"):
        pp.implied_policy_path(quotes, TRADE, meetings=conf, **kw)
    with pytest.raises(ValueError, match="past 2027-02-04"):
        pp.par_rates_on_path(quotes, TRADE, levels=[5.5] * len(conf), meetings=conf, **kw)
    two = quotes[quotes["tenor"].isin(["1M", "2M"])]  # to 20 Jan 2027: before the meeting left out
    assert pp.implied_policy_path(two, TRADE, meetings=conf, **kw).diagnostics["converged"]
    after = pp.implied_policy_path({"1M": 5.85, "3M": 5.85}, date(2027, 3, 1), meetings=conf,
                                   **(kw | {"current_policy_pct": 5.75}))
    assert after.diagnostics["meetings_covered_until"] == "2028-03-31"  # a meeting left out before the trade date
    # a plain list missing a meeting in the middle: each decision vouches for MPC_GAP_DAYS only
    gap = [d for d in DECISIONS if d != date(2027, 2, 5)]
    with pytest.raises(ValueError, match="past 2027-02-12, .*no meeting listed between 2026-12-04 and 2027-04-07"):
        pp.implied_policy_path(quotes, TRADE, meetings=gap, **kw)
    # a frame whose covered_until was set by hand, without the dates it was declared for, is held to its own dates
    bare = pd.DataFrame({"decision_date": gap})
    bare.attrs["covered_until"] = date(2028, 3, 31)
    with pytest.raises(ValueError, match="past 2027-02-12"):
        pp.implied_policy_path(quotes, TRADE, meetings=bare, **kw)
    # covered_until given is the caller's word for a list of step dates
    assert pp.implied_policy_path(quotes, TRADE, meetings=gap, covered_until=date(2027, 12, 31),
                                  **kw).diagnostics["meetings_coverage"] == "covered_until given"


def test_effective_dates_are_needed_only_inside_the_horizon():
    # one-month quotes end in 2026: the strict calendar suffices although the file lists a decision in 2027
    r = pp.implied_policy_path({"1M": 5.6}, TRADE, current_policy_pct=5.5, meetings=pp.load_mpc_calendar(),
                               spread_bp=10, calendar=CAL)
    assert r.path["decision_date"].tolist() == [date(2026, 12, 4), date(2027, 2, 5)]
    assert r.path["effective_date"].tolist()[0] == date(2026, 12, 7) and pd.isna(r.path["effective_date"].iloc[1])
    assert r.path["in_horizon"].tolist() == [True, False] and not r.diagnostics["calendar_projected"]


def _with_unscheduled(tmp_path, called):
    src = pd.read_csv(pp.DEFAULT_MPC_CSV, dtype=str, keep_default_na=False)
    extra = pd.DataFrame([{"meeting_start": "2026-12-15", "decision_date": "2026-12-15", "fy": "2026-27",
                           "kind": "unscheduled", "announced": called, "source": "hypothetical off-cycle meeting",
                           "confirmed": "yes"}])
    pd.concat([src, extra], ignore_index=True).to_csv(tmp_path / "m.csv", index=False)
    (tmp_path / "m_years.csv").write_text(pp.DEFAULT_MPC_CSV.with_name("mpc_meetings_years.csv").read_text())
    return tmp_path / "m.csv"


def test_an_unscheduled_meeting_called_after_the_trade_date_is_not_a_step(tmp_path):
    quotes = {"1M": 5.62, "2M": 5.66, "3M": 5.70}
    meetings = pp.load_mpc_calendar(_with_unscheduled(tmp_path, "2026-12-10"))
    r = pp.implied_policy_path(quotes, TRADE, current_policy_pct=5.5, meetings=meetings, spread_bp=10, calendar=LENIENT)
    assert date(2026, 12, 15) not in set(r.path["decision_date"])
    assert r.diagnostics["not_yet_called"] == ["2026-12-15"]
    as_known = pp.load_mpc_calendar(_with_unscheduled(tmp_path, "2026-12-10"), as_of=TRADE)
    assert date(2026, 12, 15) not in set(as_known["decision_date"])
    later = pp.implied_policy_path(quotes, date(2026, 12, 11), current_policy_pct=5.5, meetings=meetings,
                                   spread_bp=10, calendar=LENIENT)
    assert date(2026, 12, 15) in set(later.path["decision_date"])  # called on 10 Dec: known on 11 Dec
    rows = later.path.set_index("decision_date")
    assert rows.loc[date(2026, 12, 15), "kind"] == "unscheduled" and later.diagnostics["not_yet_called"] == []


def test_a_schedule_published_after_the_trade_date_is_refused_or_projected():
    quotes = {"1M": 5.62, "2M": 5.66, "3M": 5.70}
    meetings = pp.load_mpc_calendar()  # FY2026-27's schedule came out on 23 Mar 2026
    with pytest.raises(ValueError, match="published on 2026-03-23, after the trade date 2026-03-20"):
        pp.implied_policy_path(quotes, date(2026, 3, 20), current_policy_pct=5.25, meetings=meetings, spread_bp=10,
                               calendar=LENIENT)
    with pytest.raises(ValueError, match="no complete year with meetings as of 2026-03-20"):
        pp.load_mpc_calendar(as_of=date(2026, 3, 20))
    known = pp.load_mpc_calendar(as_of=TRADE)
    assert len(known) == 6 and known.attrs["as_of"] == TRADE and known["announced"].eq(date(2026, 3, 23)).all()


def test_spread_falls_back_to_the_previous_regime_after_a_change():
    rates = call_rates(date(2026, 9, 1), date(2026, 11, 20), lambda d: 5.25 + 0.12 if d < date(2026, 10, 7)
                       else 5.50 + 0.10)
    # the decision day and the next two business days: the new regime has no rate public yet (next morning, 9:00)
    for day, last in ((date(2026, 10, 7), date(2026, 10, 5)), (date(2026, 10, 8), date(2026, 10, 6)),
                      (date(2026, 10, 9), date(2026, 10, 6))):
        est = pp.mibor_repo_spread(rates, POLICY, day, calendar=CAL)
        assert est.fallback == "previous_regime" and est.repo_pct == 5.25
        assert est.last == last and est.spread_bp == pytest.approx(12.0)  # never the decision day's mixed fixing
        assert "previous_regime fallback" in est.note
        with pytest.raises(ValueError, match="0 usable day"):
            pp.mibor_repo_spread(rates, POLICY, day, calendar=CAL, fallback=None)
    current = pp.mibor_repo_spread(rates, POLICY, date(2026, 10, 12), calendar=CAL)
    assert (current.fallback, current.repo_pct, current.spread_bp) == ("", 5.50, pytest.approx(10.0))
    with pytest.raises(ValueError, match="fallback must be"):
        pp.mibor_repo_spread(rates, POLICY, date(2026, 10, 12), calendar=CAL, fallback="pooled")
