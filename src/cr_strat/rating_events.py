"""Effective-rating timelines and threshold-crossing events.

Ratings are tracked per *rating key*, an (issuer_id, instrument_class) pair,
because CRAs rate instrument programmes ("NCDs of Rs 5,000 crore") rather than
individual ISINs. For each key and mandate the module keeps every agency's
latest long-term rating, combines them into the mandate's effective rating
after each day's actions, and records an event whenever that rating crosses
one of the mandate's thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from cr_strat.mandates import Mandate
from cr_strat.rating_actions import INSTRUMENT_CLASSES
from cr_strat.rating_scale import LIQUIDITY_LEVELS, LT_INVESTMENT_GRADE_FLOOR, LT_NOTCH, lt_grade

EVENT_COLUMNS = (
    "event_id", "issuer_id", "issuer_name", "instrument_class", "mandate", "threshold",
    "direction", "date", "prev_grade", "new_grade", "prev_notch", "new_notch", "cause",
    "trigger_agencies", "n_agencies", "agency_ratings", "split", "below_ig", "default",
    "issuer_not_cooperating", "worst_liquidity", "prior_signal", "first_in_window",
    "rule_unverified",
)
TIMELINE_COLUMNS = (
    "issuer_id", "instrument_class", "mandate", "date", "effective_grade", "effective_notch",
    "n_agencies", "agency_ratings",
)


@dataclass
class _AgencyState:
    notch: int
    outlook: str | None
    watch: str | None
    liquidity: str | None
    inc: bool


def _opt(value: object) -> object:
    return None if pd.isna(value) else value


def _describe(states: dict[str, _AgencyState]) -> str:
    return ";".join(f"{a}:{lt_grade(s.notch)}" for a, s in sorted(states.items()))


def build_rating_events(
    actions: pd.DataFrame,
    mandates: list[Mandate],
    *,
    dedup_days: int = 365,
    signal_lookback_days: int = 90,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return ``(timeline, events)`` from the output of ``load_rating_actions``.

    ``first_in_window`` is False when the same key crossed the same threshold
    in the same direction within ``dedup_days``. ``prior_signal`` is True when
    an agency had a matching outlook or watch (negative for downgrades,
    positive for upgrades) in the ``signal_lookback_days`` before the event.
    """
    classes = {c for m in mandates for c in m.instrument_classes}
    usable = actions[
        actions["parse_ok"]
        & actions["instrument_class"].isin(classes)
        & ((actions["scale"] == "LT") | actions["withdrawn"])
    ]
    lookback = pd.Timedelta(days=signal_lookback_days)
    timeline: list[dict] = []
    events: list[dict] = []

    for (issuer_id, cls), group in usable.groupby(["issuer_id", "instrument_class"], sort=True):
        key_mandates = [m for m in mandates if cls in m.instrument_classes]
        names = group["issuer_name"].dropna()
        issuer_name = names.iloc[-1] if len(names) else None
        states: dict[str, _AgencyState] = {}
        signals: list[tuple[pd.Timestamp, str | None, str | None]] = []
        prev_eff: dict[str, int | None] = {}
        last_event: dict[tuple[str, str, str], pd.Timestamp] = {}
        first_day = True

        for date, day in group.groupby("date", sort=True):
            changes: list[str] = []
            for agency, rows in day.groupby("agency", sort=True):
                live = rows[~rows["withdrawn"] & rows["notch"].notna()]
                prev = states.get(agency)
                if live.empty:
                    if prev is not None:
                        del states[agency]
                        changes.append(f"{agency}:withdrawal")
                    continue
                # Several instruments of one class rated the same day: keep the worst.
                row = live.loc[live["notch"].astype(int).idxmax()]
                liquidity = next((v for v in live["liquidity"] if not pd.isna(v)), None)
                state = _AgencyState(
                    notch=int(row["notch"]),
                    outlook=_opt(row["outlook"]),
                    watch=_opt(row["watch"]),
                    liquidity=liquidity or (prev.liquidity if prev else None),
                    inc=bool(live["issuer_not_cooperating"].any()),
                )
                states[agency] = state
                signals.append((date, state.outlook, state.watch))
                if prev is None:
                    changes.append(f"{agency}:new_rating")
                elif prev.notch != state.notch:
                    changes.append(f"{agency}:rating_action")

            agency_notches = {a: s.notch for a, s in states.items()}
            for m in key_mandates:
                eff = m.effective_notch(agency_notches)
                had = prev_eff.get(m.name)
                if first_day or eff != had:
                    timeline.append({
                        "issuer_id": issuer_id, "instrument_class": cls, "mandate": m.name,
                        "date": date, "effective_grade": lt_grade(eff), "effective_notch": eff,
                        "n_agencies": len(states), "agency_ratings": _describe(states),
                    })
                if not first_day:
                    for threshold, limit in m.threshold_notches():
                        was = had is not None and had <= limit
                        now = eff is not None and eff <= limit
                        if was == now:
                            continue
                        direction = "down" if was else "up"
                        events.append(_event(
                            issuer_id, issuer_name, cls, m, threshold, direction, date, had, eff,
                            changes, states, signals, lookback, last_event, dedup_days,
                        ))
                prev_eff[m.name] = eff
            first_day = False

    timeline_df = pd.DataFrame(timeline, columns=list(TIMELINE_COLUMNS))
    events_df = pd.DataFrame(events, columns=[c for c in EVENT_COLUMNS if c != "event_id"])
    events_df = events_df.sort_values(["date", "issuer_id", "mandate", "threshold"], kind="stable")
    events_df.insert(0, "event_id", range(len(events_df)))
    for col in ("effective_notch",):
        timeline_df[col] = timeline_df[col].astype("Int64")
    for col in ("prev_notch", "new_notch"):
        events_df[col] = events_df[col].astype("Int64")
    return timeline_df.reset_index(drop=True), events_df.reset_index(drop=True)


def _event(
    issuer_id, issuer_name, cls, mandate, threshold, direction, date, had, eff,
    changes, states, signals, lookback, last_event, dedup_days,
) -> dict:
    kinds = {c.split(":", 1)[1] for c in changes}
    if kinds == {"withdrawal"}:
        cause = "withdrawal"
    elif kinds and kinds <= {"withdrawal", "new_rating"}:
        cause = "new_rating"
    else:
        cause = "rating_action"
    wanted = "NEGATIVE" if direction == "down" else "POSITIVE"
    prior_signal = any(
        date - lookback <= d < date and wanted in (outlook, watch) for d, outlook, watch in signals
    )
    key = (mandate.name, threshold, direction)
    last = last_event.get(key)
    first_in_window = last is None or (date - last).days > dedup_days
    last_event[key] = date
    notches = [s.notch for s in states.values()]
    liquidity = [s.liquidity for s in states.values() if s.liquidity]
    return {
        "issuer_id": issuer_id,
        "issuer_name": issuer_name,
        "instrument_class": cls,
        "mandate": mandate.name,
        "threshold": threshold,
        "direction": direction,
        "date": date,
        "prev_grade": lt_grade(had),
        "new_grade": lt_grade(eff),
        "prev_notch": had,
        "new_notch": eff,
        "cause": cause,
        "trigger_agencies": ";".join(sorted({c.split(":", 1)[0] for c in changes})),
        "n_agencies": len(states),
        "agency_ratings": _describe(states),
        "split": len(set(notches)) > 1,
        "below_ig": eff is not None and eff > LT_INVESTMENT_GRADE_FLOOR,
        "default": LT_NOTCH["D"] in notches,
        "issuer_not_cooperating": any(s.inc for s in states.values()),
        "worst_liquidity": max(liquidity, key=LIQUIDITY_LEVELS.index) if liquidity else None,
        "prior_signal": prior_signal,
        "first_in_window": first_in_window,
        "rule_unverified": mandate.verify,
    }


def rating_timeline(actions: pd.DataFrame) -> pd.DataFrame:
    """Most conservative long-term rating across agencies per issuer and instrument class over time.

    Columns: issuer_id, instrument_class, date, notch, grade. A None notch
    means every agency has withdrawn. Used to put bonds in rating peer groups.
    """
    worst = Mandate("worst_of_agencies", "worst", ("AAA",), instrument_classes=INSTRUMENT_CLASSES)
    timeline, _ = build_rating_events(actions, [worst])
    return timeline.rename(columns={"effective_notch": "notch", "effective_grade": "grade"})[
        ["issuer_id", "instrument_class", "date", "notch", "grade"]
    ]
