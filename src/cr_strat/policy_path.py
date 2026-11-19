"""The policy path implied by MIBOR-OIS (strategy S7): MPC dates, a step model of overnight MIBOR and its back-out.

**MPC dates** (``load_mpc_calendar``). A package data file, ``src/cr_strat/data/mpc_meetings.csv``, one row per
meeting: ``meeting_start``, ``decision_date``, ``fy``, ``kind`` (``scheduled`` or ``unscheduled``), ``announced``,
``source``, ``confirmed``; and beside it ``mpc_meetings_years.csv`` (``fy``, ``complete``, ``source``) declaring the
financial years whose schedule is complete, as the holiday list and its coverage file do. It holds FY2026-27 (6-8 Apr,
3-5 Jun, 3-5 Aug, 5-7 Oct, 2-4 Dec 2026 and 3-5 Feb 2027, decisions on the last day), from RBI's release of 23 Mar
2026 under section 45ZI of the RBI Act as reported by the press (partly verified: RBI's page was not fetched; the
February 2027 date rests on fewer reports and is ``confirmed = no``). ``announced`` is the day a meeting's date became
public: for scheduled meetings the day RBI published the year's schedule (23 Mar 2026 here), for an unscheduled one the
day it was called. A user may keep their own file in the same layout. Asking for meetings beyond the last complete
year raises unless ``project=True``, which adds the last complete year's scheduled meetings moved 52 weeks later
(rolled to the next business day; UNVERIFIED, flagged ``projected``) until RBI publishes the year (around March).
``as_of`` gives the calendar as known on a past day: unscheduled meetings called after it are left out, and a year
whose schedule was published after it counts as not yet published (projected on request). A rescheduled meeting keeps
only its final date (the file holds no versions). The frame's ``attrs["covered_until"]`` is the last day its
meetings are complete to, ``attrs["decision_dates"]`` the decisions it was loaded with and ``attrs["left_out"]`` those
``include_unconfirmed=False`` dropped. ``check_policy_changes`` matches each repo-rate change of a policy table
(``rbi_store``'s hand-kept one, or ``macro_store``'s ``policy_rates_bis``, which dates some changes a day late) with a
decision date.

**Step model** (analytics spec 5, F15). Overnight MIBOR on business day ``d`` is ``r(d) = p(d) + s``: ``p(d)`` the
repo rate after the last decision in force on ``d``, ``s`` the MIBOR-repo spread. A decision takes effect the
business day after it is announced (``effective_rule="next_business_day"``, UNVERIFIED): it is announced at about
10:00, and since 4 Aug 2025 MIBOR averages trades of 9:00 to 12:00, so the decision day's fixing mixes both rates
(before that the 9:00-10:00 window closed as the decision came out; ``ois.fixing_regime``). ``"same_day"`` is the
alternative; a misplaced day moves a one-month OIS by under 1 bp for a 25 bp step.

**Back-out** (``implied_policy_path``). The starting level is the repo rate known on the trade date
(``current_policy_pct``: the rate in force that day, after any decision announced that day, which comes out at about
10:00, well before FBIL's MIBOR-OIS session closes at 5 PM). Meetings decided on or before the trade date are in it;
the unknowns are the levels ``L_m`` after each meeting decided after the trade date that takes effect inside the
quotes' horizon, whatever its effective date (under ``"same_day"`` a decision on the swaps' effective date is one).
Meetings not yet announced on the trade date are not step dates: an unscheduled meeting called later is left out, and
a schedule published later is refused (load the calendar ``as_of`` the trade date). The meetings must cover the
quotes: a horizon past ``covered_until`` is refused rather than have a missing meeting's move land on another one.
The coverage is the calendar's declared years, ended the day before any meeting the frame given lacks after the trade
date (one dropped as unconfirmed, or filtered out of the frame after loading: pandas keeps ``attrs`` on a filtered
copy, so the frame's own dates are checked against ``attrs["decision_dates"]``); for a plain list of dates, or a frame
without that record, each decision vouches for ``MPC_GAP_DAYS`` (70, UNVERIFIED) after it, so a longer gap or the
end of the list ends it; ``covered_until=`` given is the caller's word. Each quote's model par rate is computed
exactly on the step path: the single-period rate ``(A(S0, E) - 1) 36500 / d`` from ``ois.compound_overnight``'s
formula up to a year, the semi-annual par formula on the path's discount factors beyond. Gauss-Newton with an
analytic Jacobian minimises ``sum_q (K_model,q - K_q)^2 + lam sum_m (L_m - L_m-1)^2`` in basis points
(``lam = 1e-6`` by default, UNVERIFIED: just enough to choose among paths the quotes cannot tell apart). A level is
``identified`` when the quotes alone pin it (it lies outside the null space of their Jacobian): the standard 1M-1Y
tenors on 19 Nov 2026 (synthetic quotes in the tests) pin December and February of FY2026-27 and April 2027, while
June, August and October 2027 move together. The path reports each level, its change and, as a two-state reading
only, ``change / 25 bp``. Beyond about six months the OIS curve also carries term premia (OIS moves on MPC days beyond
the policy surprise: Lakdawala and Sengupta 2021; IMF WP/22/209; via search, not read), so read far levels as an upper
bound on information, not an expectation.

**The MIBOR-repo spread** (``mibor_repo_spread``): liquidity sets it, the call rate near the SDF in surplus and up to
the MSF in deficit. Default estimate (UNVERIFIED): the median of (MIBOR - repo) over the 20 dealing days before the
valuation date within the current policy regime, using only rates public by ``as_of`` (default: the start of the
valuation date). Right after a repo-rate change the new regime has no public day yet (the decision day and the next
two business days with the store's next-morning publication): the estimate then falls back to the previous regime's
last 20 days (``fallback="previous_regime"``, UNVERIFIED, flagged in ``SpreadEstimate.fallback``), or raises with
``fallback=None``. The weighted average call rate (``funding.funding_rates(..., "call")``) stands in for MIBOR in
demonstrations only: it averages the whole day, where FBIL's MIBOR averages a morning window. A term structure of
the spread (quarter-end turns) is not built.

**Limits.** No real OIS quote is used: the path is shown on synthetic quotes with known steps. FY2027-28 dates are
projected until published; unscheduled meetings must be added by hand, with the day each was called. FBIL's MIBOR-OIS
rates average the whole session to 5 PM, so on a decision day they mix quotes struck before and after the
announcement. One spread for the whole horizon; no term premium model; levels are read as certainties, not
probabilities over outcomes.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from cr_strat.calendars import DATA_DIR, BusinessCalendar, calendar_for, coverage_path
from cr_strat.conventions import as_date
from cr_strat.ois import (
    MIBOR_OIS,
    OisConventions,
    add_months,
    as_time,
    beyond_calendar,
    effective_date,
    fixing_regime,
    ois_quotes,
    ois_schedule,
)

DEFAULT_MPC_CSV = DATA_DIR / "mpc_meetings.csv"
MPC_COLUMNS = ("meeting_start", "decision_date", "fy", "kind", "announced", "source", "confirmed")
MPC_YEARS_COLUMNS = ("fy", "complete", "source")
MEETING_COLUMNS = ("meeting_start", "decision_date", "fy", "kind", "announced", "confirmed", "projected", "source")
MEETING_KINDS = ("scheduled", "unscheduled")
EFFECTIVE_RULES = ("next_business_day", "same_day")
PATH_COLUMNS = ("decision_date", "effective_date", "kind", "projected", "confirmed", "in_horizon", "level_pct",
                "change_bp", "move_25bp_share", "identified")
QUOTE_FIT_COLUMNS = ("quote", "end_date", "quote_kind", "rate_pct", "model_pct", "error_bp")
STEP_COLUMNS = ("date", "next_date", "days", "regime", "policy_pct", "overnight_pct")
CHECK_COLUMNS = ("effective_date", "repo_pct", "previous_pct", "change_bp", "decision_date", "meeting_kind", "status",
                 "note")
PROJECTION_RULE = ("UNVERIFIED projection: the last complete year's scheduled meeting moved 52 weeks later and rolled "
                   "to the next business day; replace with RBI's schedule once published")
DEFAULT_LAM = 1e-6
SPREAD_WINDOW = 20
SPREAD_FALLBACKS = ("previous_regime",)
# UNVERIFIED rule of thumb: a list of decision dates without a declared coverage is taken as complete for this many
# days after its last decision (FY2026-27's scheduled meetings are 58 to 63 days apart).
MPC_GAP_DAYS = 70
_TRUE, _FALSE = {"yes", "y", "true", "1"}, {"no", "n", "false", "0"}
_YEAR = 365.0


def fy_of(day) -> str:
    """India's financial year of ``day`` as ``2026-27`` (April to March)."""
    d = as_date(day)
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def fy_end(fy: str) -> date:
    """31 March closing financial year ``fy`` (``2026-27`` ends 31 Mar 2027)."""
    start = _fy_start(fy)
    return date(start + 1, 3, 31)


def _fy_start(fy: str) -> int:
    text = str(fy).strip()
    parts = text.split("-")
    if len(parts) != 2 or not (parts[0].isdigit() and len(parts[0]) == 4 and parts[1].isdigit() and len(parts[1]) == 2):
        raise ValueError(f"financial year {fy!r} must look like 2026-27")
    start = int(parts[0])
    if (start + 1) % 100 != int(parts[1]):
        raise ValueError(f"financial year {fy!r} must span two consecutive years, like 2026-27")
    return start


def _blank(value) -> bool:
    if value is None or (isinstance(value, str) and not value.strip()):
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def read_policy_table(policy: pd.DataFrame) -> list[tuple[date, float]]:
    """``(effective_date, repo_pct)`` rows of a policy-rate table, sorted: ``rbi_store``'s hand-kept table
    (``POLICY_COLUMNS``) or ``macro_store``'s ``policy_rates_bis`` (blank SDF and MSF are fine here). Each date once,
    each repo rate a number."""
    if not isinstance(policy, pd.DataFrame):
        raise TypeError("policy must be a DataFrame with effective_date and repo_pct (rbi_store's policy columns)")
    missing = [c for c in ("effective_date", "repo_pct") if c not in policy]
    if missing:
        raise ValueError(f"the policy table lacks {missing}")
    out = []
    for n, rec in enumerate(policy.to_dict("records"), start=1):
        try:
            rate = float(rec["repo_pct"])
        except (TypeError, ValueError):
            raise ValueError(f"policy row {n}: repo_pct {rec['repo_pct']!r} is not a number") from None
        if not math.isfinite(rate):
            raise ValueError(f"policy row {n}: repo_pct is missing")
        out.append((as_date(rec["effective_date"]), rate))
    days = [d for d, _ in out]
    if len(set(days)) != len(days):
        raise ValueError("the policy table lists an effective_date twice")
    return sorted(out)


def policy_rate_on(policy, day) -> float:
    """The repo rate in force on ``day`` (the last ``effective_date`` on or before it); NaN before the first.
    ``policy`` is a table or ``read_policy_table``'s rows."""
    rows = read_policy_table(policy) if isinstance(policy, pd.DataFrame) else policy
    d = as_date(day)
    rate = math.nan
    for effective, value in rows:
        if effective <= d:
            rate = value
    return rate


def _flag(value, where: str) -> bool:
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ValueError(f"{where}: must be yes or no, not {value!r}")


# ---------------------------------------------------------------- the MPC calendar


def _read(path: Path, columns: tuple[str, ...], what: str) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"{what} not found: {path}")
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    frame.columns = [str(c).strip().lower() for c in frame.columns]
    missing = [c for c in columns if c not in frame]
    if missing:
        raise ValueError(f"{path}: {what} lacks columns {missing}")
    return frame


def _projected_rows(base: list[dict], fys: list[str], calendar: BusinessCalendar) -> list[dict]:
    out = []
    first = _fy_start(base[0]["fy"])
    for fy in fys:
        weeks = 52 * (_fy_start(fy) - first)
        for row in base:
            if row["kind"] != "scheduled":
                continue
            decision = calendar.adjust(row["decision_date"] + timedelta(weeks=weeks), "following")
            start = row["meeting_start"] + (decision - row["decision_date"])
            out.append({"meeting_start": start, "decision_date": decision, "fy": fy_of(decision), "kind": "scheduled",
                        "announced": None, "confirmed": False, "projected": True,
                        "source": f"{PROJECTION_RULE} (from {row['decision_date'].isoformat()})"})
    return out


def _published(rows: list[dict]) -> dict[str, date]:
    """The day each financial year's schedule became public: the earliest ``announced`` of its scheduled meetings
    (of any meeting, for a year with unscheduled ones only)."""
    out: dict[str, date] = {}
    for kind in ("unscheduled", "scheduled"):  # scheduled meetings decide where a year has both
        days: dict[str, list[date]] = {}
        for r in rows:
            if r["kind"] == kind and r.get("announced") is not None:
                days.setdefault(r["fy"], []).append(r["announced"])
        out.update({fy: min(v) for fy, v in days.items()})
    return out


def load_mpc_calendar(path: str | Path = DEFAULT_MPC_CSV, *, years_path: str | Path | None = None, until=None,
                      project: bool = False, include_unconfirmed: bool = True,
                      calendar: BusinessCalendar | None = None, as_of=None) -> pd.DataFrame:
    """MPC meetings (``MEETING_COLUMNS``, sorted by decision date) from a meetings file and its years file.

    Every row is checked: dates in order (the meeting at most a week long), ``fy`` the decision's financial year,
    ``kind`` in ``MEETING_KINDS``, ``announced`` a date on or before the decision, a ``source``, ``confirmed`` yes or
    no; a decision date once. Every year with rows must be declared complete in the years file (``<name>_years.csv``
    beside it). ``as_of`` (a date): the calendar as known that day, i.e. unscheduled meetings called after it left
    out and a year whose schedule was published after it treated as not yet published. ``until``: the last date the
    caller needs; beyond the last complete (published) year it raises unless ``project=True`` adds projected meetings
    (on ``calendar``, lenient beyond its years; ``announced`` blank). ``include_unconfirmed=False`` drops the file's
    rows with ``confirmed = no``; projected rows, unconfirmed by nature, stay. ``attrs["covered_until"]`` holds the
    last day the meetings are complete to (the end of the last complete or projected year), ``attrs["as_of"]`` the day
    they are as known on (``None``: today's file), ``attrs["decision_dates"]`` the decisions the frame was loaded with
    and ``attrs["left_out"]`` the unconfirmed ones dropped. A back-out checks the frame it is given against both: a
    meeting dropped, or filtered out of the frame afterwards (pandas keeps ``attrs`` on a filtered copy), ends the
    coverage the day before it, since the MPC still meets then and its move would land on another meeting.
    """
    path = Path(path)
    years_file = Path(years_path) if years_path is not None else coverage_path(path)
    frame = _read(path, MPC_COLUMNS, "MPC meeting file")
    years = _read(years_file, MPC_YEARS_COLUMNS, "MPC years file")
    known_on = None if as_of is None else as_date(as_of)
    complete = {}
    for n, rec in enumerate(years.to_dict("records"), start=2):
        where = f"{years_file} line {n}"
        _fy_start(rec["fy"])
        if rec["fy"] in complete:
            raise ValueError(f"{where}: {rec['fy']} is listed twice")
        if not rec["source"].strip():
            raise ValueError(f"{where}: source is empty")
        complete[rec["fy"]] = _flag(rec["complete"], f"{where}: complete")
    rows, seen = [], set()
    for n, rec in enumerate(frame.to_dict("records"), start=2):
        where = f"{path} line {n}"
        try:
            start, decision = date.fromisoformat(rec["meeting_start"].strip()), date.fromisoformat(
                rec["decision_date"].strip())
        except ValueError:
            raise ValueError(f"{where}: meeting_start and decision_date must be dates like 2026-12-04") from None
        if not start <= decision <= start + timedelta(days=7):
            raise ValueError(f"{where}: the decision {decision} must fall on or within a week after the meeting starts "
                             f"({start})")
        try:
            announced = date.fromisoformat(rec["announced"].strip())
        except ValueError:
            raise ValueError(f"{where}: announced must be the date the meeting's date became public, like 2026-03-23 "
                             f"(the schedule's release, or the day an unscheduled meeting was called), not "
                             f"{rec['announced']!r}") from None
        if announced > decision:
            raise ValueError(f"{where}: announced {announced} is after the decision {decision}")
        fy = rec["fy"].strip()
        _fy_start(fy)
        if fy_of(decision) != fy:
            raise ValueError(f"{where}: a decision on {decision} falls in {fy_of(decision)}, not {fy}")
        kind = rec["kind"].strip().lower()
        if kind not in MEETING_KINDS:
            raise ValueError(f"{where}: kind must be one of {MEETING_KINDS}, not {rec['kind']!r}")
        if not rec["source"].strip():
            raise ValueError(f"{where}: source is empty; say where the date comes from")
        if decision in seen:
            raise ValueError(f"{where}: decision date {decision} is listed twice")
        seen.add(decision)
        if fy not in complete:
            raise ValueError(f"{where}: {fy} has meetings but no line in {years_file}; declare it complete once its "
                             "schedule is in")
        if not complete[fy]:
            raise ValueError(f"{where}: {fy} is not declared complete in {years_file}; list a year's meetings once its "
                             "whole schedule is published")
        rows.append({"meeting_start": start, "decision_date": decision, "fy": fy, "kind": kind,
                     "announced": announced, "confirmed": _flag(rec["confirmed"], f"{where}: confirmed"),
                     "projected": False, "source": rec["source"].strip()})
    published = _published(rows)
    empty = sorted(fy for fy, ok in complete.items() if ok and fy not in published)
    if empty:
        raise ValueError(f"{years_file} declares {empty[0]} complete, but {path} lists no meeting in it (the MPC meets "
                         "at least four times a year): add its meetings or mark it not complete")
    covered = sorted(fy for fy, ok in complete.items() if ok)
    if known_on is not None:
        covered = [fy for fy in covered if published[fy] <= known_on]
        rows = [r for r in rows if (r["announced"] <= known_on if r["kind"] == "unscheduled"
                                    else r["fy"] in covered)]
    if not covered:
        when = f" as of {known_on}" if known_on is not None else ""
        first = min(published.values(), default=None)
        raise ValueError(f"{years_file} declares no complete year with meetings{when}"
                         + (f" (the earliest schedule in {path} was published on {first})" if first else ""))
    covered_until = fy_end(covered[-1])
    if until is not None:
        horizon = as_date(until)
        if horizon > covered_until:
            if not project:
                when = f" as known on {known_on}" if known_on is not None else ""
                raise ValueError(f"meetings are listed up to {covered[-1]} (to {covered_until}){when}, not to "
                                 f"{horizon}: pass project=True for projected dates (UNVERIFIED), or add the year's "
                                 "schedule")
            cal = (calendar or calendar_for("mumbai_settlement")).lenient()
            base = sorted((r for r in rows if r["fy"] == covered[-1]), key=lambda r: r["decision_date"])
            if not any(r["kind"] == "scheduled" for r in base):
                raise ValueError(f"{covered[-1]} has no scheduled meetings to project from")
            wanted = []
            fy = covered[-1]
            while fy_end(fy) < horizon:
                start = _fy_start(fy) + 1
                fy = f"{start}-{(start + 1) % 100:02d}"
                wanted.append(fy)
            taken = {r["decision_date"] for r in rows}  # an unscheduled meeting already called keeps its day
            rows.extend(r for r in _projected_rows(base, wanted, cal) if r["decision_date"] not in taken)
            covered_until = fy_end(wanted[-1])
    left_out: tuple[date, ...] = ()
    if not include_unconfirmed:
        left_out = tuple(sorted(r["decision_date"] for r in rows if not (r["confirmed"] or r["projected"])))
        rows = [r for r in rows if r["confirmed"] or r["projected"]]
    out = pd.DataFrame(rows, columns=list(MEETING_COLUMNS))
    out = out.sort_values("decision_date", kind="mergesort").reset_index(drop=True)
    out.attrs["covered_until"] = covered_until
    out.attrs["as_of"] = known_on
    # what the frame was loaded with: pandas keeps attrs on a filtered copy, so a back-out checks the frame's own
    # dates against these rather than trust covered_until alone
    out.attrs["decision_dates"] = tuple(out["decision_date"])
    out.attrs["left_out"] = left_out
    return out


def _decisions(meetings) -> list[date]:
    if isinstance(meetings, pd.DataFrame):
        if "decision_date" not in meetings:
            raise ValueError("meetings need a decision_date column (load_mpc_calendar's layout)")
        values = list(meetings["decision_date"])
    else:
        values = list(meetings)
    days = sorted(as_date(v) for v in values)
    if len(set(days)) != len(days):
        raise ValueError("a decision date is listed twice")
    return days


@dataclass(frozen=True)
class _Meetings:
    """The meetings of a calendar as a back-out on ``trade_date`` may use them."""

    decisions: list[date]  # every meeting given, sorted (``levels`` of ``par_rates_on_path`` follow this order)
    usable: list[date]  # those known on the trade date: unscheduled meetings called later left out
    info: dict[date, tuple[str, bool, bool]]  # kind, projected, confirmed
    covered_until: date
    not_yet_called: tuple[date, ...]
    coverage: str = ""  # why the coverage ends at covered_until
    remedy: str = ""  # what lets a longer horizon through


def _gap_coverage(decisions: list[date], trade_date: date) -> tuple[date, str]:
    """The coverage of decision dates without a declared one (UNVERIFIED rule of thumb): each decision vouches for
    ``MPC_GAP_DAYS`` after it, counted from the last one on or before the trade date (or the trade date itself), so
    the coverage ends ``MPC_GAP_DAYS`` after the last decision before the first longer gap, or after the last one."""
    before = [d for d in decisions if d <= trade_date]
    anchor = before[-1] if before else trade_date
    for d in (x for x in decisions if x > trade_date):
        if (d - anchor).days > MPC_GAP_DAYS:
            return (anchor + timedelta(days=MPC_GAP_DAYS),
                    f"no meeting listed between {anchor} and {d}, {(d - anchor).days} days apart: more than "
                    f"MPC_GAP_DAYS ({MPC_GAP_DAYS}), so a meeting may be missing")
        anchor = d
    return (anchor + timedelta(days=MPC_GAP_DAYS),
            f"MPC_GAP_DAYS ({MPC_GAP_DAYS}) after the last decision listed, {anchor}")


def _coverage(meetings, decisions: list[date], trade_date: date, covered_until) -> tuple[date, str, str]:
    """The last day ``meetings`` are complete to for a back-out on ``trade_date``, why it ends there and what would
    let a longer horizon through.

    ``covered_until`` given is the caller's word; else a frame's ``attrs["covered_until"]`` (``load_mpc_calendar``'s
    declared years), held to the frame's own dates by the gap rule when the frame does not say which decisions it was
    loaded with; else the gap rule (``_gap_coverage``). Either way a meeting the frame lacks, after the trade date,
    ends the coverage the day before it: one ``load_mpc_calendar`` left out as unconfirmed (``attrs["left_out"]``), or
    one it loaded that the frame no longer holds (``attrs["decision_dates"]``: a filtered copy keeps ``attrs``).
    """
    attrs = meetings.attrs if isinstance(meetings, pd.DataFrame) else {}
    as_list = "or pass the decision dates as a list with covered_until= if you know them to be complete"
    absent: dict[date, tuple[str, str]] = {}
    for d in attrs.get("left_out") or ():
        absent[as_date(d)] = ("left out as unconfirmed (include_unconfirmed=False)",
                              f"load the calendar with its unconfirmed meetings, {as_list}")
    recorded = attrs.get("decision_dates")
    if recorded is not None:
        for d in sorted({as_date(v) for v in recorded} - set(decisions)):
            absent.setdefault(d, ("in the calendar as loaded but not in the frame given (filtered out?)",
                                  f"pass the calendar as loaded, not a filtered copy, {as_list}"))
    extend = ("load the calendar with until= the horizon (project=True past the last published year), or pass "
              "covered_until= for a list of step dates you know to be complete")
    if covered_until is not None:
        cover, why = as_date(covered_until), "covered_until given"
    elif attrs.get("covered_until") is not None:
        cover, why = as_date(attrs["covered_until"]), "the end of the calendar's last complete or projected year"
        if recorded is None:  # coverage without the dates it was declared for: hold it to the frame's own dates
            by_gap = _gap_coverage(decisions, trade_date)
            if by_gap[0] < cover:
                cover, why = by_gap
    else:
        cover, why = _gap_coverage(decisions, trade_date)
    later = sorted(d for d in absent if d > trade_date)
    if later and later[0] - timedelta(days=1) < cover:
        reason, remedy = absent[later[0]]
        return later[0] - timedelta(days=1), f"the meeting of {later[0]} is {reason}", remedy
    return cover, why, extend


def _meetings_at(meetings, trade_date: date, covered_until=None) -> _Meetings:
    """The meetings known on ``trade_date`` and the last day they are complete to (``_coverage``).

    A frame with ``announced`` (``load_mpc_calendar``'s) drops unscheduled meetings called after the trade date and
    refuses scheduled ones of a year whose schedule was published after it.
    """
    decisions = _decisions(meetings)
    if not decisions:
        raise ValueError("no MPC meeting given")
    info = _meeting_info(meetings, decisions)
    later: list[date] = []
    if isinstance(meetings, pd.DataFrame) and "announced" in meetings:
        rows = []
        for rec in meetings.to_dict("records"):
            announced = None if _blank(rec.get("announced")) else as_date(rec["announced"])
            rows.append({"decision_date": as_date(rec["decision_date"]), "fy": fy_of(rec["decision_date"]),
                         "kind": str(rec.get("kind", "scheduled")), "announced": announced})
        published = _published(rows)
        for r in rows:
            if r["kind"] == "unscheduled":
                if r["announced"] is not None and r["announced"] > trade_date:
                    later.append(r["decision_date"])
            elif r["announced"] is not None and published.get(r["fy"], r["announced"]) > trade_date:
                raise ValueError(f"the {r['fy']} schedule (the meeting of {r['decision_date']}) was published on "
                                 f"{published[r['fy']]}, after the trade date {trade_date}: its dates were not known "
                                 f"then; load the calendar as known on the trade date "
                                 f"(load_mpc_calendar(as_of={trade_date.isoformat()!r}, project=True))")
    cover, why, remedy = _coverage(meetings, decisions, trade_date, covered_until)
    usable = [d for d in decisions if d not in set(later)]
    return _Meetings(decisions, usable, info, cover, tuple(sorted(later)), why, remedy)


def _steps(m: _Meetings, t0: date, s0: date, last_end: date, cal: BusinessCalendar,
           rule: str) -> tuple[list[date], dict[date, date]]:
    """The decisions after the trade date (the path's steps) and the effective date of those inside the horizon.

    Decisions on or before the trade date are in the current rate; one of them taking effect after the swaps'
    effective date cannot be, and is refused. Effective dates are computed only for decisions before ``last_end``,
    so a strict calendar is asked only about the horizon.
    """
    if last_end - timedelta(days=1) > m.covered_until:
        raise ValueError(f"the quotes run to {last_end}, past {m.covered_until}, the last day the MPC meetings are "
                         f"complete to ({m.coverage}): a later meeting's move would land on another one. To go on, "
                         f"{m.remedy}")
    known = [d for d in m.usable if d <= t0]
    if known:
        eff_known = decision_effective_dates([known[-1]], cal, rule)[0]
        if eff_known > s0:
            raise ValueError(f"the decision of {known[-1]}, known on the trade date {t0}, takes effect for MIBOR on "
                             f"{eff_known}, after the swaps' effective date {s0}: current_policy_pct cannot stand for "
                             "the days before it (an effective lag of 0 with effective_rule='next_business_day')")
    future = [d for d in m.usable if d > t0]
    inside = [d for d in future if d < last_end]
    effective = dict(zip(inside, decision_effective_dates(inside, cal, rule))) if inside else {}
    return future, effective


def decision_effective_dates(meetings, calendar: BusinessCalendar | None = None,
                             rule: str = "next_business_day") -> list[date]:
    """The first day each decision's rate applies to MIBOR: the next business day (default) or the same day."""
    if rule not in EFFECTIVE_RULES:
        raise ValueError(f"rule must be one of {EFFECTIVE_RULES}, not {rule!r}")
    cal = calendar if calendar is not None else calendar_for("mumbai_settlement")
    days = _decisions(meetings)
    if rule == "same_day":
        return [cal.adjust(d, "following") for d in days]
    return [cal.add_business_days(d, 1) for d in days]


# ---------------------------------------------------------------- the step model


def step_overnight(start, end, *, base_pct: float, levels: Sequence[float], effective_dates: Sequence,
                   spread_bp: float, calendar: BusinessCalendar | None = None) -> pd.DataFrame:
    """Overnight MIBOR on each business day of ``[start, end)`` under a step path (``STEP_COLUMNS``).

    ``policy_pct`` is ``base_pct`` until the first of ``effective_dates`` and ``levels[m]`` from the ``m``-th on;
    ``overnight_pct`` adds ``spread_bp``. ``days`` runs to the next business day (the last to ``end``).
    """
    cal = calendar if calendar is not None else calendar_for("mumbai_settlement")
    s, e = as_date(start), as_date(end)
    if not e > s:
        raise ValueError(f"end {e} must be after start {s}")
    eff = [as_date(d) for d in effective_dates]
    lv = [float(v) for v in levels]
    if len(eff) != len(lv):
        raise ValueError(f"{len(lv)} levels for {len(eff)} effective dates")
    if any(b <= a for a, b in zip(eff, eff[1:])):
        raise ValueError("effective dates must increase")
    days = cal.business_days(s, e - timedelta(days=1))
    rows = []
    for k, day in enumerate(days):
        nxt = days[k + 1] if k + 1 < len(days) else e
        regime = sum(d <= day for d in eff)
        policy = float(base_pct) if regime == 0 else lv[regime - 1]
        rows.append({"date": day, "next_date": nxt, "days": (nxt - day).days, "regime": regime, "policy_pct": policy,
                     "overnight_pct": policy + float(spread_bp) / 100})
    return pd.DataFrame(rows, columns=list(STEP_COLUMNS))


@dataclass(frozen=True)
class _Quote:
    label: str
    kind: str
    rate: float  # percent; NaN for a quote only to be modelled
    ends: tuple[date, ...]  # period ends (one for a single period)
    taus: tuple[float, ...]


def _quote_specs(quotes, s0: date, conv: OisConventions, cal: BusinessCalendar, trade_date: date) -> list[_Quote]:
    if isinstance(quotes, pd.DataFrame) and "end_date" in [str(c).strip().lower() for c in quotes.columns]:
        frame = quotes.copy()
        frame.columns = [str(c).strip().lower() for c in frame.columns]
        if "rate_pct" not in frame:
            raise ValueError("end-dated quotes need a rate_pct column")
        out = []
        for rec in frame.to_dict("records"):
            end = as_date(rec["end_date"])
            if not end > s0:
                raise ValueError(f"a quote ending {end} does not end after the effective date {s0}")
            if not cal.is_business_day(end):
                raise ValueError(f"a quote ending {end} does not end on a business day")
            if end > add_months(s0, conv.single_period_max_months) + timedelta(days=7):
                raise ValueError(f"a quote ending {end} is longer than a single period ({conv.single_period_max_months}"
                                 " months): give longer quotes by tenor")
            try:
                rate = float(rec["rate_pct"])
            except (TypeError, ValueError):
                raise ValueError(f"the quote ending {end} is not a number: {rec['rate_pct']!r}") from None
            if not math.isfinite(rate):
                raise ValueError(f"the quote ending {end} is missing")
            out.append(_Quote(end.isoformat(), "single_period", rate, (end,), ((end - s0).days / conv.year_days,)))
        if len({q.ends[-1] for q in out}) != len(out):
            raise ValueError("two quotes end on the same date")
        return sorted(out, key=lambda q: q.ends[-1])
    table = ois_quotes(quotes, trade_date=trade_date, conventions=conv)
    out = []
    for row in table.itertuples(index=False):
        periods = ois_schedule(s0, row.months, conv, cal)
        out.append(_Quote(row.tenor, row.quote_kind, row.rate_pct, tuple(p.end for p in periods),
                          tuple(p.year_fraction for p in periods)))
    return out


class _Model:
    """Par rates of the quotes on a step path, and their derivatives in the levels (percent per percent)."""

    def __init__(self, specs: list[_Quote], s0: date, effective: list[date], spread_bp: float, base_pct: float,
                 cal: BusinessCalendar, year_days: int):
        last = max(q.ends[-1] for q in specs)
        self.days = cal.business_days(s0, last - timedelta(days=1))
        nxt = self.days[1:] + [last]
        self.n = np.array([(b - a).days for a, b in zip(self.days, nxt)], dtype=float)
        self.regime = np.array([sum(d <= day for d in effective) for day in self.days], dtype=int)
        self.m = len(effective)
        self.specs, self.s0, self.spread, self.base, self.year = specs, s0, spread_bp / 100, base_pct, year_days
        index = {d: k for k, d in enumerate(self.days)}
        index[last] = len(self.days)
        self.cut = [[index[e] for e in q.ends] for q in specs]

    def evaluate(self, levels: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        lv = np.r_[self.base, levels]
        r = lv[self.regime] + self.spread
        x = r * self.n / (100 * self.year)
        if (x <= -1).any():
            raise ValueError("a level leaves no positive growth factor")
        lng = np.r_[0.0, np.cumsum(np.log1p(x))]  # log1p: log(1 + x) loses ~1e-12 at x ~ 1e-4
        dl = self.n / (100 * self.year) / (1.0 + x)  # d ln g / d r, per percent
        onehot = np.zeros((len(self.days), self.m + 1))
        onehot[np.arange(len(self.days)), self.regime] = 1.0
        grad = np.vstack([np.zeros(self.m + 1), np.cumsum(onehot * dl[:, None], axis=0)])[:, 1:]  # d lnA / dL
        values, jac = np.empty(len(self.specs)), np.empty((len(self.specs), self.m))
        for q, (spec, cut) in enumerate(zip(self.specs, self.cut)):
            if spec.kind == "single_period":
                a = math.exp(lng[cut[0]])
                d = (spec.ends[0] - self.s0).days
                values[q] = math.expm1(lng[cut[0]]) * 100 * self.year / d
                jac[q] = 100 * self.year / d * a * grad[cut[0]]
            else:
                p = np.exp(-lng[cut])
                dp = -p[:, None] * grad[cut]
                tau = np.asarray(spec.taus)
                s = float(tau @ p)
                ds = tau @ dp
                values[q] = 100 * (1.0 - p[-1]) / s
                jac[q] = 100 * (-dp[-1] * s - (1.0 - p[-1]) * ds) / s ** 2
        return values, jac


def par_rates_on_path(quotes, trade_date, *, current_policy_pct: float, levels: Sequence[float], meetings,
                      spread_bp: float, calendar: BusinessCalendar | None = None,
                      conventions: OisConventions = MIBOR_OIS,
                      effective_rule: str = "next_business_day", covered_until=None) -> pd.DataFrame:
    """Model par rates of ``quotes`` (tenors with ``rate_pct``, or ``end_date``s) on a known step path.

    ``levels`` holds the repo rate after each of ``meetings`` (all of them, in date order); ``current_policy_pct``
    is the rate known on the trade date, so the levels of meetings decided on or before it, and of unscheduled ones
    not yet called, are not used. The meetings must cover the quotes, as in ``implied_policy_path``. Quotes' own
    rates are ignored but must be numbers (use 0). For building synthetic quotes with known steps and for checking a
    path.
    """
    if effective_rule not in EFFECTIVE_RULES:
        raise ValueError(f"effective_rule must be one of {EFFECTIVE_RULES}, not {effective_rule!r}")
    cal = calendar if calendar is not None else calendar_for(conventions.calendar_name)
    t0 = as_date(trade_date)
    s0 = effective_date(t0, conventions, cal)
    specs = _quote_specs(quotes, s0, conventions, cal, t0)
    m = _meetings_at(meetings, t0, covered_until)
    if len(levels) != len(m.decisions):
        raise ValueError(f"{len(levels)} levels for {len(m.decisions)} meetings")
    level_of = dict(zip(m.decisions, (float(v) for v in levels)))
    last_end = max(q.ends[-1] for q in specs)
    _, effective = _steps(m, t0, s0, last_end, cal, effective_rule)
    steps = [d for d in effective if effective[d] < last_end]
    model = _Model(specs, s0, [effective[d] for d in steps], spread_bp, float(current_policy_pct), cal,
                   conventions.year_days)
    values, _ = model.evaluate(np.array([level_of[d] for d in steps]))
    return pd.DataFrame({"quote": [q.label for q in specs], "end_date": [q.ends[-1] for q in specs],
                         "quote_kind": [q.kind for q in specs], "model_pct": values})


@dataclass(frozen=True, eq=False)
class PolicyPath:
    """``implied_policy_path``'s answer: ``path`` (``PATH_COLUMNS``, one row per meeting), ``quotes``
    (``QUOTE_FIT_COLUMNS``: each quote against its model rate) and ``diagnostics`` (a dict)."""

    path: pd.DataFrame
    quotes: pd.DataFrame
    diagnostics: dict


def implied_policy_path(quotes, trade_date, *, current_policy_pct: float, meetings, spread_bp: float,
                        lam: float = DEFAULT_LAM, calendar: BusinessCalendar | None = None,
                        conventions: OisConventions = MIBOR_OIS, effective_rule: str = "next_business_day",
                        max_iter: int = 50, covered_until=None) -> PolicyPath:
    """The repo-rate level after each MPC meeting that best reprices one day's OIS par ``quotes``.

    ``quotes``: tenors (``ois.ois_quotes``' layouts) or ``end_date`` and ``rate_pct`` (single periods).
    ``current_policy_pct``: the repo rate known on the trade date, i.e. in force that day after any decision
    announced that day (never a rate decided later, such as the one a policy table dates on the business day after
    the trade date). Meetings decided on or before the trade date are in it; every meeting decided after it is a
    step, whatever its effective date. ``meetings``: ``load_mpc_calendar``'s frame (best loaded ``as_of`` the trade
    date) or the decision dates; unscheduled meetings called after the trade date are left out
    (``diagnostics["not_yet_called"]``). They must cover the quotes: a coverage (``covered_until`` given, else the
    frame's declared years cut before any meeting it lacks, else ``MPC_GAP_DAYS`` after each decision up to the first
    longer gap; ``diagnostics["meetings_coverage"]`` says which) ending before the last quote ends is refused.
    ``spread_bp``:
    MIBOR minus repo (``mibor_repo_spread``). Meetings taking effect after the last quote ends are reported with no
    level (``in_horizon`` False; no effective date for those decided after it ends).
    """
    if effective_rule not in EFFECTIVE_RULES:
        raise ValueError(f"effective_rule must be one of {EFFECTIVE_RULES}, not {effective_rule!r}")
    cal = calendar if calendar is not None else calendar_for(conventions.calendar_name)
    t0 = as_date(trade_date)
    lam = float(lam)
    if not (math.isfinite(lam) and lam >= 0):
        raise ValueError(f"lam must be zero or more, not {lam}")
    base = float(current_policy_pct)
    spread = float(spread_bp)
    if not (math.isfinite(base) and math.isfinite(spread)):
        raise ValueError("current_policy_pct and spread_bp must be finite numbers")
    s0 = effective_date(t0, conventions, cal)
    specs = _quote_specs(quotes, s0, conventions, cal, t0)
    listed = _meetings_at(meetings, t0, covered_until)
    info = listed.info
    last_end = max(q.ends[-1] for q in specs)
    decisions, eff_of = _steps(listed, t0, s0, last_end, cal, effective_rule)
    effective = [eff_of.get(d) for d in decisions]
    unknown = [k for k, d in enumerate(effective) if d is not None and d < last_end]
    model = _Model(specs, s0, [effective[k] for k in unknown], spread, base, cal, conventions.year_days)
    quoted = np.array([q.rate for q in specs])
    m = len(unknown)
    x = np.full(m, base)
    diff = np.eye(m) - np.eye(m, k=-1)
    root = math.sqrt(lam)
    converged, iterations = m == 0, 0
    while not converged and iterations < max_iter:
        iterations += 1
        values, jac = model.evaluate(x)
        res = np.r_[(values - quoted) * 100, root * 100 * (diff @ x - np.r_[base, np.zeros(m - 1)])]
        big = np.vstack([jac * 100, root * 100 * diff])
        step = np.linalg.lstsq(big, res, rcond=None)[0]
        x = x - step
        if np.abs(step).max() < 1e-11:  # percent: 1e-9 bp
            converged = True
            break
    values, jac = model.evaluate(x)
    identified = np.ones(m, dtype=bool)
    rank = m
    if m:
        sv, vt = np.linalg.svd(jac * 100)[1:]
        tol = max(sv.max(), 1.0) * 1e-9 if sv.size else 1.0
        rank = int((sv > tol).sum())
        null = vt[rank:] if rank < m else np.zeros((0, m))
        identified = np.all(np.abs(null) < 1e-6, axis=0) if null.size else np.ones(m, dtype=bool)
    rows, prev = [], base
    position = {k: j for j, k in enumerate(unknown)}
    for k, (decision, eff) in enumerate(zip(decisions, effective)):
        kind, projected, confirmed = info[decision]
        if k in position:
            level = float(x[position[k]])
            change = (level - prev) * 100
            rows.append({"decision_date": decision, "effective_date": eff, "kind": kind, "projected": projected,
                         "confirmed": confirmed, "in_horizon": True, "level_pct": level, "change_bp": change,
                         "move_25bp_share": change / 25.0, "identified": bool(identified[position[k]])})
            prev = level
        else:
            rows.append({"decision_date": decision, "effective_date": eff, "kind": kind, "projected": projected,
                         "confirmed": confirmed, "in_horizon": False, "level_pct": math.nan, "change_bp": math.nan,
                         "move_25bp_share": math.nan, "identified": False})
    fit = pd.DataFrame({"quote": [q.label for q in specs], "end_date": [q.ends[-1] for q in specs],
                        "quote_kind": [q.kind for q in specs], "rate_pct": quoted, "model_pct": values,
                        "error_bp": (values - quoted) * 100}, columns=list(QUOTE_FIT_COLUMNS))
    dates_used = [s0, last_end, *(d for d in effective if d is not None)]
    diagnostics = {"trade_date": t0.isoformat(), "effective_date": s0.isoformat(), "current_policy_pct": base,
                   "spread_bp": spread, "lam": lam, "effective_rule": effective_rule, "levels": m, "rank": rank,
                   "iterations": iterations, "converged": bool(converged),
                   "max_abs_error_bp": float(np.abs(fit["error_bp"]).max()),
                   "calendar_projected": beyond_calendar(cal, dates_used),
                   "projected_meetings": int(sum(info[decisions[k]][1] for k in unknown)),
                   "meetings_covered_until": listed.covered_until.isoformat(), "meetings_coverage": listed.coverage,
                   "not_yet_called": [d.isoformat() for d in listed.not_yet_called]}
    return PolicyPath(pd.DataFrame(rows, columns=list(PATH_COLUMNS)), fit, diagnostics)


def _meeting_info(meetings, decisions: list[date]) -> dict[date, tuple[str, bool, bool]]:
    if isinstance(meetings, pd.DataFrame):
        out = {}
        for rec in meetings.to_dict("records"):
            d = as_date(rec["decision_date"])
            out[d] = (str(rec.get("kind", "scheduled")), bool(rec.get("projected", False)),
                      bool(rec.get("confirmed", True)))
        return out
    return {d: ("scheduled", False, True) for d in decisions}


# ---------------------------------------------------------------- the MIBOR-repo spread


@dataclass(frozen=True)
class SpreadEstimate:
    """``mibor_repo_spread``'s answer: the median spread (bp) of ``n_obs`` days from ``first`` to ``last``, in the
    regime that began ``regime_start`` at a repo rate of ``repo_pct``, with rates public by ``as_of``. ``fallback`` is
    ``""`` for the current regime, or ``"previous_regime"`` when it had too few public days and the regime before it
    gave the estimate (``note`` says why)."""

    spread_bp: float
    n_obs: int
    first: date
    last: date
    regime_start: date
    repo_pct: float
    as_of: datetime
    values_bp: tuple[float, ...]
    fallback: str = ""
    note: str = ""


def _spread_days(frame: pd.DataFrame, rules, start: date, stop: date, cutoff: datetime,
                 cal: BusinessCalendar) -> list[tuple[date, float]]:
    """(day, overnight rate - repo) of the dealing days in ``[start, stop)`` public by ``cutoff``, sorted."""
    rows = []
    for rec in frame.to_dict("records"):
        day = as_date(rec["date"])
        if _blank(rec["rate_pct"]) or not (start <= day < stop) or not cal.is_business_day(day):
            continue  # not a dealing day (no rate), outside the window, or a Saturday MIBOR never has
        public = rec.get("public_from")
        if _blank(public):
            regime = fixing_regime(day)
            if "public_from" in frame or not regime["published_by"]:
                continue  # no publication time known: not used
            hour, minute = (int(v) for v in regime["published_by"].split(":"))
            public = datetime.combine(day, time(hour, minute))
        if as_time(public, f"public_from of {day}") > cutoff:
            continue
        rows.append((day, float(rec["rate_pct"]) - policy_rate_on(rules, day)))
    return sorted(rows)


def mibor_repo_spread(rates: pd.DataFrame, policy: pd.DataFrame, on, *, window: int = SPREAD_WINDOW, as_of=None,
                      calendar: BusinessCalendar | None = None, effective_rule: str = "next_business_day",
                      min_obs: int = 1, fallback: str | None = "previous_regime") -> SpreadEstimate:
    """Median of (overnight rate - repo rate) over the last ``window`` dealing days before ``on`` in its regime.

    ``rates``: ``date`` and ``rate_pct`` (a ``funding.funding_rates`` frame of ``call`` for the WACR stand-in, or a
    MIBOR series), with ``public_from``; without it a day's rate counts as public at FBIL's publication time of that
    day (``ois.fixing_regime``). ``policy``: ``effective_date`` and ``repo_pct``. The regime starts on the business
    day after the last repo change on or before ``on`` (``effective_rule="same_day"``: on it). ``as_of`` defaults to
    the start of ``on``. With fewer than ``min_obs`` public days in it, ``fallback="previous_regime"`` (UNVERIFIED)
    takes the last ``window`` days of the regime before, up to the day before the change (that day's fixing may mix
    both rates), flagged in the answer; ``fallback=None`` raises, as does a previous regime that is short too.
    """
    cal = calendar if calendar is not None else calendar_for("mumbai_settlement")
    if effective_rule not in EFFECTIVE_RULES:
        raise ValueError(f"effective_rule must be one of {EFFECTIVE_RULES}, not {effective_rule!r}")
    if fallback is not None and fallback not in SPREAD_FALLBACKS:
        raise ValueError(f"fallback must be None or one of {SPREAD_FALLBACKS}, not {fallback!r}")
    d = as_date(on)
    cutoff = datetime.combine(d, time()) if as_of is None else as_time(as_of, "as_of")
    rules = read_policy_table(policy)
    changes = [(e, r) for k, (e, r) in enumerate(rules) if k == 0 or r != rules[k - 1][1]]
    in_force = [(e, r) for e, r in changes if e <= d]
    if not in_force:
        raise ValueError(f"the policy table has no repo rate in force on {d}")
    if not isinstance(rates, pd.DataFrame):
        raise TypeError("rates must be a DataFrame with date and rate_pct")
    frame = rates.rename(columns={"as_on_date": "date"}) if "date" not in rates else rates
    missing = [c for c in ("date", "rate_pct") if c not in frame]
    if missing:
        raise ValueError(f"rates lack {missing}")
    need = max(int(min_obs), 1)

    def regime_start(effective: date) -> date:
        return cal.add_business_days(effective, 1) if effective_rule == "next_business_day" else effective

    effective, repo = in_force[-1]
    start = regime_start(effective)
    rows = _spread_days(frame, rules, start, d, cutoff, cal)[-int(window):]
    used, note = "", ""
    if len(rows) < need:
        short = (f"{len(rows)} usable day(s) in the regime from {start} to before {d} (rates public by {cutoff}); at "
                 f"least {need} needed")
        if fallback is None or len(in_force) < 2:
            raise ValueError(short + ("" if fallback is None else "; no earlier regime in the policy table"))
        before, repo_before = in_force[-2]
        start_before = regime_start(before)
        rows = _spread_days(frame, rules, start_before, min(effective, d), cutoff, cal)[-int(window):]
        if len(rows) < need:
            raise ValueError(f"{short}; the previous regime, from {start_before} to before {effective}, has "
                             f"{len(rows)}")
        used = "previous_regime"
        note = (f"{short}: the regime at {repo_before:g}% before the change of {effective} gave the estimate "
                "(previous_regime fallback, UNVERIFIED)")
        start, repo = start_before, repo_before
    values = tuple(v * 100 for _, v in rows)
    return SpreadEstimate(float(np.median(values)), len(rows), rows[0][0], rows[-1][0], start, repo, cutoff, values,
                          used, note)


# ---------------------------------------------------------------- checks


def check_policy_changes(policy: pd.DataFrame, meetings: pd.DataFrame, *,
                         calendar: BusinessCalendar | None = None) -> pd.DataFrame:
    """Each repo-rate change of ``policy`` against the MPC decisions of ``meetings`` (``CHECK_COLUMNS``).

    ``status``: ``decision_day`` (a decision on the change's date), ``day_after_decision`` (one the business day
    before, as BIS dates some changes), ``no_meeting`` (none, in a year the meetings cover: an unscheduled meeting
    missing from the file, or a wrong date) or ``outside_calendar``. Rows that move only the corridor are skipped.
    """
    cal = (calendar if calendar is not None else calendar_for("mumbai_settlement")).lenient()
    rules = read_policy_table(policy)
    listed = meetings[~meetings["projected"].astype(bool)] if "projected" in meetings else meetings
    decisions = {as_date(r["decision_date"]): str(r.get("kind", "scheduled")) for r in listed.to_dict("records")}
    covered = {fy_of(d) for d in decisions}
    rows = []
    for k in range(1, len(rules)):
        day, rate = rules[k]
        before = rules[k - 1][1]
        if rate == before:
            continue
        prior = cal.add_business_days(day, -1)
        if day in decisions:
            status, decision = "decision_day", day
        elif prior in decisions:
            status, decision = "day_after_decision", prior
        elif fy_of(day) in covered:
            status, decision = "no_meeting", None
        else:
            status, decision = "outside_calendar", None
        note = {"decision_day": "", "day_after_decision": "dated the business day after the decision",
                "no_meeting": f"no MPC decision on {day} or {prior} in a year the meetings cover",
                "outside_calendar": f"{fy_of(day)} is not in the meetings"}[status]
        rows.append({"effective_date": day, "repo_pct": rate, "previous_pct": before,
                     "change_bp": (rate - before) * 100,
                     "decision_date": decision, "meeting_kind": decisions.get(decision) if decision else None,
                     "status": status, "note": note})
    return pd.DataFrame(rows, columns=list(CHECK_COLUMNS))


__all__ = ["CHECK_COLUMNS", "DEFAULT_LAM", "DEFAULT_MPC_CSV", "EFFECTIVE_RULES", "MEETING_COLUMNS", "MEETING_KINDS",
           "MPC_COLUMNS", "MPC_GAP_DAYS", "MPC_YEARS_COLUMNS", "PATH_COLUMNS", "PROJECTION_RULE", "QUOTE_FIT_COLUMNS",
           "SPREAD_FALLBACKS", "SPREAD_WINDOW", "STEP_COLUMNS", "PolicyPath", "SpreadEstimate", "check_policy_changes",
           "decision_effective_dates",
           "fy_end", "fy_of", "implied_policy_path", "load_mpc_calendar", "mibor_repo_spread", "par_rates_on_path",
           "policy_rate_on", "read_policy_table", "step_overnight"]
