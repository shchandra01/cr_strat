"""Risk limits: dated limit tables, the metrics they bind, daily checks and a breach log, the constraints that follow
from the fund's legal form, and the pre-trade gate a strategy's orders pass before the engine sees them.

**Limit tables** (L5.1-L5.2 of the risk specification of 24 Nov 2026). ``LIMIT_COLUMNS``: each row binds one
``metric`` (``METRICS``: DV01, key-rate DV01, CS01, exposure, share of an ISIN's stock, rating floor, longest tenor,
VaR, ES, stress loss, JTD, drawdown, 20-day loss, leverage, outside liabilities, total assets, borrowing, liquidity
shares and days, the OIS hedge tests, short face, when-issued shares, CRAR, Tier 1, single party and group, ALM
mismatches, net owned fund, settlement cover, corporate bond repo, the board resolution) over a ``scope`` (``fund``, a
``book``, a ``strategy``; ``*`` every one) and a ``selector`` (``key=value`` pairs joined by ``;``, alternatives by
``|`` as in ``stress``' scenario table; ``issuer=*`` each issuer separately; ``abs=yes`` a magnitude;
``min_assets_cr`` / ``max_assets_cr`` in force only at or above / below that size of the fund's total assets), in a
``unit``, with a ``direction`` (``max`` or ``min``), a ``warning_level`` (blank: the hard level) and a
``hard_level``, a ``legal_form`` (``any`` or a preset), dates (``effective_from``, ``effective_to`` excluded, and the
``approval_date``: a limit approved later is not in force earlier), a cure period, an ``action`` (``block_increase``,
``derisk``, ``report``, ``halt``), a ``policy_status`` (``EXAMPLE`` rows are checked and labelled, never mistaken for
the fund's policy; ``DRAFT`` reported, never enforced; ``APPROVED`` enforced), its source, status and whether it is a
question for counsel. A table is refused, naming the line, for a duplicate id, an unknown metric, unit or selector key,
a hard level on the wrong side of the warning level or blank, and two rows of the same limit in force on one day. A
rating floor's levels are grades (``A-``), read as notches of ``rating_scale.LT_NOTCH`` (a higher notch is worse).

**Shipped tables.** ``limits_example.csv`` (E01-E30: every row ``EXAMPLE``, levels in years of NAV duration, percent
of NAV or ratios so they scale; the SEBI mutual fund limits cited are references, not rules for this fund) and
``legal_form_limits.csv`` (L01-L24: the constraints that follow from each legal form, each with its source, all
``counsel = yes``; ``APPROVED`` only for rows that restate a rule, ``DRAFT`` for readings no source settles), with
``nbfc_risk_weights.csv`` (the middle layer's risk weights). Which legal form applies is a question for counsel: every
run names its access preset and the legal-form rows of the other preset are reported ``not_applicable``.

**Metrics** (L5.3). ``limit_metrics`` computes every metric a limit table needs, per scope and key, from the risk
run's tables (``risk.RiskResult`` or the CSVs ``cr-strat risk`` writes) and the engine's frames of the day
(positions, the NAV history, funding, fills), as of the run's ``as_of``; each row says whether its inputs were complete
(no stock for an ISIN share, a VaR below full coverage or refused, an unrated holding under a rating floor) and what
it used. Exposures, CS01 and JTD by issuer, group and sector count corporate bonds, CP and CD (GoI and state
securities are exempt, as both the SEBI references and RBI's concentration norms treat them) unless ``kind`` says
otherwise; a purchase still to settle counts at its market value (trade-date view, UNVERIFIED), and the borrowing a
book needs is projected from its cash and the settlements still due. The OIS hedge effect is the larger of the swaps'
effect on the scope's parametric VaR (EWMA, the risk run's factor changes) and on its sum of absolute key-rate DV01s.

**Checks and breaches** (L5.4-L5.5). ``check_limits``: utilisation ``value / hard`` for a maximum, ``hard / value``
for a minimum with a positive level (``value / hard`` for a negative one such as a drawdown, so that 1 is always the
boundary; UNVERIFIED refinement of F22; a zero denominator gives 0 or infinity); status ``ok``, ``warning``,
``breach``, ``incomplete`` (treated as a warning, UNVERIFIED) or ``not_applicable`` (a legal-form row of another
preset, a borrowing limit of the fund's policy under a preset that cannot borrow, a layer threshold not reached).
``update_breach_log`` keeps every breach episode: ``new``, ``continuing`` (business days counted), ``escalated``
(past its cure date; a legal-form breach at once: an incident for counsel), ``cured`` (kept for the record); a breach
that first appears on a day an order in its scope filled is ``active`` (the gate was bypassed), else ``passive``.
Cure periods (examples, UNVERIFIED): concentration, rating and tenor 30 business days (the period SEBI's Master
Circular for Mutual Funds, para 3.11, gives schemes after a passive breach: a reference), market risk and liquidity 5,
drawdown and stop-loss 1 (``derisk``), legal-form rules 0. A holding downgraded below the rating floor is a passive
breach, never a forced sale; the gate blocks only purchases.

**Sources** (cited in our own words): RBI's NBFC Registration and SBR, Capital Adequacy, Concentration, ALM and
Investment Portfolio Directions, the Repo Directions 2025, the Short Sale and When Issued Directions 2018 and the
Rupee IRD Directions 2025, as the verified notes record them (claim ids in each row's source); SEBI's Master Circular
for Mutual Funds of 20 Mar 2026 (investment restrictions and para 3.11) as a reference for the example levels.

**Limits.** Every level in ``limits_example.csv`` is an example to be replaced by the fund's approved policy; the
legal-form rows are a reading of the directions for counsel to confirm; owned fund, Tier 1 and capital are taken as
NAV (the accountant's mapping, UNVERIFIED). Tested on synthetic books only. Library code never reads the clock.
"""

from __future__ import annotations

import difflib
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np
import pandas as pd

from cr_strat import costs as cost_rules
from cr_strat import returns as rt
from cr_strat import risk as rk
from cr_strat import stress as st
from cr_strat.bonds import DiscountInstrument
from cr_strat.calendars import mumbai_settlement_calendar
from cr_strat.floating import GoiFloatingRateBond, index_dv01
from cr_strat.issuance import TENOR_BUCKETS
from cr_strat.ois import MIBOR_OIS, OisSwap, as_time, effective_date, ois_npv, ois_par_rate
from cr_strat.rating_scale import LT_NOTCH, parse_rating

DATA_DIR = Path(__file__).resolve().parent / "data"
EXAMPLE_LIMITS_CSV = DATA_DIR / "limits_example.csv"
LEGAL_FORM_LIMITS_CSV = DATA_DIR / "legal_form_limits.csv"
NBFC_RISK_WEIGHTS_CSV = DATA_DIR / "nbfc_risk_weights.csv"

LIMIT_COLUMNS = ("limit_id", "name", "metric", "scope_type", "scope", "selector", "unit", "direction",
                 "warning_level", "hard_level", "legal_form", "effective_from", "effective_to", "approved_by",
                 "approval_date", "approval_ref", "cure_business_days", "action", "policy_status", "source", "status",
                 "counsel", "notes")
METRIC_COLUMNS = ("date", "as_of", "metric", "scope_type", "scope", "selector", "key", "value", "unit",
                  "inputs_complete", "detail")
LIMIT_CHECK_COLUMNS = ("date", "as_of", "limit_id", "name", "metric", "scope_type", "scope", "selector", "key", "value",
                       "unit", "direction", "warning_level", "hard_level", "utilisation", "status", "policy_status",
                       "legal_form", "counsel", "detail")
BREACH_COLUMNS = ("limit_id", "scope_type", "scope", "key", "first_date", "last_date", "business_days", "status",
                  "origin", "worst_value", "hard_level", "cure_due", "action", "closed_date", "notes")
GATE_COLUMNS = ("decision_date", "as_of", "seq", "order_id", "book", "strategy", "isin", "side", "face", "months",
                "venue", "price_source", "decision", "limit_id", "metric", "scope", "pre_value", "post_value",
                "warning_level", "hard_level", "reason")
RISK_WEIGHT_COLUMNS = ("kind", "sector", "risk_weight_pct", "source", "status", "notes")

POLICY_STATUSES = ("EXAMPLE", "DRAFT", "APPROVED")
ACTIONS = ("block_increase", "derisk", "report", "halt")
CHECK_STATUSES = ("ok", "warning", "breach", "incomplete", "not_applicable")
BREACH_STATUSES = ("new", "continuing", "cured", "escalated")
OPEN_BREACHES = ("new", "continuing", "escalated")
DECISIONS = ("approved", "approved_with_warning", "refused")
LEGAL_FORMS = ("any", "unregulated_company", "nbfc_non_pd", "bank_or_pd")
SCOPE_TYPES = ("fund", "book", "strategy")
ROW_STATUSES = ("V", "P", "U")
DIRECTIONS = ("max", "min")
EXAMPLE_NOTE = "EXAMPLE limits: replace with the fund's approved risk policy before any real use"
COMMON_KEYS = ("abs", "min_assets_cr", "max_assets_cr")
ALM_KEYS = {"1-7d": "alm_1_7d", "8-14d": "alm_8_14d", "15-30d": "alm_15_30d"}
BUCKETS = tuple(label for _, _, label in TENOR_BUCKETS)
# the worst notch of each rating bucket, for a holding known only by its bucket (conservative)
BUCKET_WORST_NOTCH = {"AAA": 1, "AA+": 2, "AA": 3, "AA-": 4, "A+": 5, "A": 6, "A-": 7, "BBB": 10, "SUB_IG": 19,
                      "D": 20}
CREDIT_KINDS = tuple(sorted(rk.CREDIT_KINDS))
_YEAR = 365.0
_N = NormalDist()


@dataclass(frozen=True)
class Metric:
    """A metric limits can bind: its units (the first is the default), natural direction, selector keys, family
    (which sets the default cure period) and what it reads."""

    units: tuple[str, ...]
    direction: str
    keys: tuple[str, ...]
    family: str
    reads: str


METRICS: dict[str, Metric] = {
    "dv01": Metric(("rs_per_bp", "years"), "max", ("bucket",), "market",
                   "the yield DV01 of bonds and swaps as the engine sums it (years: over NAV x 1e-4)"),
    "krd": Metric(("rs_per_bp", "years"), "max", ("curve", "tenor"), "market", "key-rate DV01s (risk key_rates)"),
    "cs01": Metric(("rs_per_bp", "years"), "max", ("rating_bucket", "issuer", "group", "sector", "tenor_bucket",
                                                    "kind"), "market", "CS01 on the z-spread (risk position_risk)"),
    "exposure": Metric(("pct_nav", "rs"), "max", ("issuer", "group", "sector", "rating_bucket", "kind", "isin"),
                       "concentration", "market value, a purchase to settle at its market value"),
    "isin_share": Metric(("pct",), "max", ("isin", "kind"), "concentration", "face over the ISIN's outstanding stock"),
    "rating_floor": Metric(("notch", "pct_nav"), "max", ("below", "of", "isin", "kind"), "concentration",
                           "the worst rating held as known at as_of, or the share of NAV below a grade"),
    "max_tenor": Metric(("years",), "max", ("kind",), "concentration", "the longest residual maturity held"),
    "var": Metric(("rs", "pct_nav"), "max", ("method", "horizon", "alpha", "horizon_rule"), "market",
                  "the VaR table of the risk run (the gate: parametric)"),
    "es": Metric(("rs", "pct_nav"), "max", ("method", "horizon", "alpha", "window", "horizon_rule"), "market",
                 "the ES of the risk run (the gate: parametric)"),
    "stress_loss": Metric(("rs", "pct_nav"), "max", ("scenario", "family"), "market",
                          "the loss of a scenario, or the worst of a family, revalued in full"),
    "jtd": Metric(("pct_nav", "rs"), "max", ("issuer", "group"), "concentration", "jump to default by issuer or group"),
    "drawdown": Metric(("pct",), "min", (), "performance", "the NAV index over its running peak, less one"),
    "loss_window": Metric(("pct_nav",), "min", ("days", "base"), "performance",
                          "P&L over the last n business days over NAV at its start"),
    "leverage": Metric(("ratio",), "max", ("kind",), "market",
                       "gross bonds over NAV (trade-date view), or the borrowing over NAV (kind=repo)"),
    "outside_liabilities": Metric(("ratio",), "max", (), "legal",
                                  "borrowing plus payables of purchases to settle, over NAV (owned fund taken as NAV)"),
    "total_assets": Metric(("rs",), "max", (), "legal", "NAV plus borrowing plus payables"),
    "borrowing": Metric(("rs",), "max", (), "legal", "the borrowing a book needs once its settlements are paid"),
    "illiquid_share": Metric(("pct_nav",), "max", (), "liquidity", "illiquid positions over NAV (stress liquidity)"),
    "liquid_share": Metric(("pct_nav",), "min", ("days",), "liquidity", "NAV saleable within n business days"),
    "days_to_liquidate": Metric(("days",), "max", ("stat",), "liquidity", "the longest, or value-weighted, days"),
    "ois_hedge_ratio": Metric(("ratio",), "max", (), "legal",
                              "the engine's hedge test: |swaps' DV01| over |bonds'|, infinite with the same sign"),
    "ois_hedge_effect": Metric(("ratio",), "max", (), "legal",
                               "parametric VaR and the sum of absolute KRDs with the swaps over without"),
    "short_face": Metric(("rs",), "max", ("kind",), "legal", "face sold short (when-issued sales left out)"),
    "when_issued": Metric(("pct",), "max", ("side", "isin"), "legal", "when-issued face over the notified amount"),
    "crar": Metric(("pct",), "min", (), "legal", "NAV over risk-weighted assets (nbfc_risk_weights.csv)"),
    "tier1": Metric(("pct",), "min", (), "legal", "NAV over risk-weighted assets (Tier 1 taken as NAV)"),
    "single_party": Metric(("pct",), "max", ("issuer",), "legal", "non-sovereign exposure to one party over NAV"),
    "single_group": Metric(("pct",), "max", ("group",), "legal", "non-sovereign exposure to one group over NAV"),
    "alm_mismatch": Metric(("pct",), "max", ("bucket",), "legal", "cumulative negative mismatch over outflows"),
    "nof": Metric(("rs",), "min", (), "legal", "net owned fund, taken as NAV"),
    "settlement_cover": Metric(("ratio",), "min", (), "legal", "cash and sale proceeds over purchases to settle"),
    "corporate_repo": Metric(("rs",), "max", (), "legal", "corporate bonds funded in repo (positions.funded)"),
    "board_resolution": Metric(("flag",), "min", (), "legal", "1 with a board resolution in the financial year"),
}
CURE_DAYS = {"concentration": 30, "market": 5, "liquidity": 5, "performance": 1, "legal": 0}  # UNVERIFIED examples
SCALED_METRICS = ("dv01", "krd", "cs01", "var", "es")  # a derisk scale multiplies these limits' levels (P6.4)
BORROWING_METRICS = ("borrowing", "alm_mismatch", "corporate_repo")
UNVERIFIED_RULES = (
    "every level of limits_example.csv (EXAMPLE) and its warning-to-hard spacing; cure periods 30 business days "
    "for concentration, rating and tenor, 5 for market risk and liquidity, 1 for drawdown and stop-loss, 0 for "
    "legal-form rules",
    "an incomplete metric is checked as a warning, never a breach",
    "utilisation of a minimum: hard/value for a positive level, value/hard for a negative one",
    "exposures, CS01, JTD by issuer, group and sector over corporate bonds, CP and CD; GoI and states exempt",
    "a purchase still to settle counts at its market value (trade-date view); payables count as outside liabilities",
    "the borrowing a book needs: purchases to settle less sales to settle less its net cash",
    "owned fund, Tier 1 and capital taken as NAV; OIS counterparty risk not risk-weighted",
    "a holding known only by its rating bucket takes the bucket's worst notch",
    "the OIS hedge effect: the larger of the swaps' effect on parametric VaR and on the sum of absolute KRDs (each "
    "tenor's G-sec and OIS KRDs netted)",
    "the gate: VaR and ES parametric (EWMA) on the post-trade sensitivities; queued orders count as filled; a purchase "
    "under a preset that cannot borrow keeps 1% of its consideration in cash for the fill and costs",
)


# ---------------------------------------------------------------- small helpers


def _blank(value) -> bool:
    return rk._blank(value)


def _text(value) -> str:
    return rk._text(value)


def _closest(value: str, allowed: Iterable[str]) -> str:
    close = difflib.get_close_matches(value, list(allowed), n=1)
    return f" (did you mean {close[0]!r}?)" if close else ""


def _when(value) -> datetime:
    return as_time(value, "as_of")


def _fsum(values) -> float:
    return math.fsum(float(v) for v in values)


def _finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _float(value, blank: float = math.nan) -> float:
    if _blank(value):
        return blank
    try:
        return float(value)
    except (TypeError, ValueError):
        return blank


def _grade_notch(value, where: str) -> float:
    """A level of a rating floor: a long-term grade (``A-``) as its notch, or a notch number."""
    text = _text(value)
    if text.upper() in LT_NOTCH:
        return float(LT_NOTCH[text.upper()])
    parsed = parse_rating(text)
    if parsed.scale == "LT" and parsed.notch is not None:
        return float(parsed.notch)
    return rk._num(value, where)


def _fmt(value: float, unit: str) -> str:
    if not _finite(value):
        return str(value)
    if unit in ("rs", "rs_per_bp"):
        return f"Rs {value:,.2f}{' per bp' if unit == 'rs_per_bp' else ''}"
    if unit in ("pct", "pct_nav"):
        return f"{value:.4f}%{' of NAV' if unit == 'pct_nav' else ''}"
    if unit == "notch":
        grade = next((g for g, n in LT_NOTCH.items() if n == int(value)), "") if float(value).is_integer() else ""
        return f"notch {value:g}{f' ({grade})' if grade else ''}"
    return f"{value:.6g} {unit}"


# ---------------------------------------------------------------- selectors


def parse_selector(text, metric: str, where: str = "the selector") -> tuple[tuple[str, tuple[str, ...]], ...]:
    """``key=value`` pairs joined by ``;`` (alternatives by ``|``, ``*`` each value separately) checked against the
    metric's keys (``METRICS``) and the common ones (``abs``, ``min_assets_cr``, ``max_assets_cr``), sorted by key."""
    spec = METRICS[metric]
    allowed = (*spec.keys, *COMMON_KEYS)
    out: dict[str, tuple[str, ...]] = {}
    for part in [p.strip() for p in _text(text).split(";") if p.strip()]:
        key, sep, value = part.partition("=")
        key = key.strip().lower()
        values = tuple(v.strip() for v in value.split("|") if v.strip())
        if not sep or not values:
            raise ValueError(f"{where}: selector part {part!r} is not key=value")
        if key not in allowed:
            raise ValueError(f"{where}: selector key {key!r} does not apply to {metric} (keys: {', '.join(allowed)})"
                             f"{_closest(key, allowed)}")
        if key in out:
            raise ValueError(f"{where}: selector key {key!r} is given twice")
        if "*" in values and len(values) > 1:
            raise ValueError(f"{where}: {key}=* takes no alternatives")
        _check_selector_value(metric, key, values, where)
        out[key] = values
    return tuple(sorted(out.items()))


def _check_selector_value(metric: str, key: str, values: tuple[str, ...], where: str) -> None:
    def one() -> str:
        if len(values) != 1 or values[0] == "*":
            raise ValueError(f"{where}: {key} takes one value, not {'|'.join(values)}")
        return values[0]
    if key == "abs":
        if one().lower() not in ("yes", "no"):
            raise ValueError(f"{where}: abs is yes or no, not {values[0]!r}")
    elif key in ("min_assets_cr", "max_assets_cr", "alpha"):
        number = rk._num(one(), f"{where}: {key}")
        if key == "alpha" and not 0.5 < number < 1:
            raise ValueError(f"{where}: alpha must be between 0.5 and 1, not {number}")
    elif key in ("horizon", "days"):
        number = rk._num(one(), f"{where}: {key}")
        if number < 1 or number != int(number):
            raise ValueError(f"{where}: {key} must be a whole number of days of at least 1, not {values[0]!r}")
    elif key == "window" and one() not in rk.WINDOWS:
        raise ValueError(f"{where}: window is one of {rk.WINDOWS}, not {values[0]!r}")
    elif key == "method" and one() not in (*rk.METHODS, "preferred"):
        raise ValueError(f"{where}: method is one of {(*rk.METHODS, 'preferred')}, not {values[0]!r}")
    elif key == "horizon_rule" and one() not in (*rk.HORIZON_RULES, "liquidity_horizons"):
        raise ValueError(f"{where}: horizon_rule is one of {(*rk.HORIZON_RULES, 'liquidity_horizons')}")
    elif key == "stat" and one() not in ("max", "mean"):
        raise ValueError(f"{where}: stat is max or mean, not {values[0]!r}")
    elif key == "side" and one() not in ("long", "short"):
        raise ValueError(f"{where}: side is long or short, not {values[0]!r}")
    elif key == "of" and one() not in ("purchases", "holdings"):
        raise ValueError(f"{where}: of is purchases or holdings, not {values[0]!r}")
    elif key == "base" and one() not in ("fund", "own"):
        raise ValueError(f"{where}: base is fund or own, not {values[0]!r}")
    elif key == "below":
        _grade_notch(one(), f"{where}: below")
    elif key == "bucket":
        allowed = tuple(ALM_KEYS) if metric == "alm_mismatch" else BUCKETS
        bad = [v for v in values if v != "*" and v not in allowed]
        if bad:
            raise ValueError(f"{where}: bucket {bad[0]!r} is not one of {allowed}")
    elif key == "kind" and metric == "leverage" and one() not in ("gross", "repo"):
        raise ValueError(f"{where}: leverage kind is gross or repo, not {values[0]!r}")


def selector_text(selector: Sequence[tuple[str, tuple[str, ...]]]) -> str:
    """The canonical text of a parsed selector (keys sorted)."""
    return ";".join(f"{k}={'|'.join(v)}" for k, v in sorted(selector))


def _sel(selector, key: str) -> tuple[str, ...] | None:
    for k, v in selector:
        if k == key:
            return v
    return None


def _sel_one(selector, key: str, default: str | None = None) -> str | None:
    values = _sel(selector, key)
    return values[0] if values else default


# ---------------------------------------------------------------- the limit table (L5.2)


def _limit_frame(table, what: str) -> pd.DataFrame:
    frame = rk._frame(table, what)
    unknown = [c for c in frame.columns if c not in LIMIT_COLUMNS and c not in ("origin", "line")]
    if unknown:
        raise ValueError(f"{what} has unknown columns {unknown}{_closest(unknown[0], LIMIT_COLUMNS)} (columns: "
                         f"{', '.join(LIMIT_COLUMNS)})")
    rk._check_columns(frame, ("limit_id", "metric", "hard_level"), LIMIT_COLUMNS, what)
    return frame


def _origin_of(source) -> str:
    try:
        if not isinstance(source, (pd.DataFrame, list, tuple)) and Path(source).resolve() == \
                LEGAL_FORM_LIMITS_CSV.resolve():
            return "legal_form"
    except (TypeError, OSError):
        pass
    return "policy"


def read_limits(source, *, origin: str | None = None) -> pd.DataFrame:
    """A validated limit table (``LIMIT_COLUMNS`` plus ``origin``, ``legal_form`` for the shipped legal-form table
    else ``policy``, and ``line``): selectors in canonical form, levels as numbers (a blank warning is the hard level),
    dates as dates (blank: open), blank unit, direction, cure period, action and status filled from the metric's
    defaults. Refused, naming the line: a duplicate ``limit_id``, an unknown metric, unit, selector key, legal form,
    action or policy status, a blank or misplaced level, and two rows of one limit (metric, scope, selector, unit,
    direction, legal form) in force on one day."""
    what = "the limit table" if isinstance(source, (pd.DataFrame, list, tuple)) else f"the limit table {source}"
    frame = _limit_frame(source, what)
    kind = origin or (_text(frame["origin"].iloc[0]) if "origin" in frame and len(frame) else _origin_of(source))
    rows, ids = [], {}
    for n, rec in rk._lines(frame):
        where = f"{what} line {n}"
        lid = _text(rec.get("limit_id"))
        if not lid:
            raise ValueError(f"{where}: limit_id is blank")
        if lid in ids:
            raise ValueError(f"{where}: limit_id {lid} is used twice (line {ids[lid]})")
        ids[lid] = n
        metric = _text(rec.get("metric")).lower()
        if metric not in METRICS:
            raise ValueError(f"{where}: metric {rec.get('metric')!r} is not one the code knows"
                             f"{_closest(metric, METRICS)}: {', '.join(METRICS)}")
        spec = METRICS[metric]
        scope_type = _text(rec.get("scope_type")).lower() or "fund"
        if scope_type not in SCOPE_TYPES:
            raise ValueError(f"{where}: scope_type {rec.get('scope_type')!r} is not one of {SCOPE_TYPES}")
        scope = _text(rec.get("scope")) or ("fund" if scope_type == "fund" else "")
        if scope_type == "fund" and scope != "fund":
            raise ValueError(f"{where}: a fund limit's scope is fund, not {scope!r}")
        if not scope:
            raise ValueError(f"{where}: a {scope_type} limit needs a scope (a name, or * for every {scope_type})")
        selector = parse_selector(rec.get("selector"), metric, where)
        unit = _text(rec.get("unit")).lower() or spec.units[0]
        if unit not in spec.units:
            raise ValueError(f"{where}: unit {unit!r} does not apply to {metric} (one of {spec.units})")
        direction = _text(rec.get("direction")).lower() or spec.direction
        if direction not in DIRECTIONS:
            raise ValueError(f"{where}: direction must be max or min, not {rec.get('direction')!r}")
        level = (lambda v, w: _grade_notch(v, w)) if metric == "rating_floor" and unit == "notch" else \
            (lambda v, w: rk._num(v, w))
        if _blank(rec.get("hard_level")):
            raise ValueError(f"{where}: hard_level is blank (every limit needs one)")
        hard = level(rec.get("hard_level"), f"{where}: hard_level")
        warning = hard if _blank(rec.get("warning_level")) else level(rec.get("warning_level"),
                                                                       f"{where}: warning_level")
        if direction == "max" and warning > hard:
            raise ValueError(f"{where}: the warning level {warning:g} is above the hard level {hard:g} of a maximum")
        if direction == "min" and warning < hard:
            raise ValueError(f"{where}: the warning level {warning:g} is below the hard level {hard:g} of a minimum")
        legal_form = _text(rec.get("legal_form")).lower() or "any"
        if legal_form not in LEGAL_FORMS:
            raise ValueError(f"{where}: legal_form {rec.get('legal_form')!r} is not one of {LEGAL_FORMS}")
        start = rk._opt_date(rec.get("effective_from"), f"{where}: effective_from")
        end = rk._opt_date(rec.get("effective_to"), f"{where}: effective_to")
        if start is not None and end is not None and end <= start:
            raise ValueError(f"{where}: effective_to {end} is not after effective_from {start}")
        approval = rk._opt_date(rec.get("approval_date"), f"{where}: approval_date")
        cure = rk._num(rec.get("cure_business_days"), f"{where}: cure_business_days",
                       blank=float(CURE_DAYS[spec.family]))
        if cure < 0 or cure != int(cure):
            raise ValueError(f"{where}: cure_business_days must be a whole number of 0 or more, not {cure}")
        action = _text(rec.get("action")).lower() or "block_increase"
        if action not in ACTIONS:
            raise ValueError(f"{where}: action {rec.get('action')!r} is not one of {ACTIONS}")
        policy = _text(rec.get("policy_status")).upper()
        if policy not in POLICY_STATUSES:
            raise ValueError(f"{where}: policy_status {rec.get('policy_status')!r} must be one of {POLICY_STATUSES} "
                             "(an example is never to be mistaken for the fund's policy)")
        status = _text(rec.get("status")).upper() or "U"
        if status not in ROW_STATUSES:
            raise ValueError(f"{where}: status {rec.get('status')!r} must be one of {ROW_STATUSES}")
        counsel = _text(rec.get("counsel")).lower() or "no"
        if counsel not in ("yes", "no"):
            raise ValueError(f"{where}: counsel is yes or no, not {rec.get('counsel')!r}")
        rows.append({"limit_id": lid, "name": _text(rec.get("name")) or lid, "metric": metric,
                     "scope_type": scope_type, "scope": scope, "selector": selector_text(selector), "unit": unit,
                     "direction": direction, "warning_level": warning, "hard_level": hard, "legal_form": legal_form,
                     "effective_from": start, "effective_to": end, "approved_by": _text(rec.get("approved_by")),
                     "approval_date": approval, "approval_ref": _text(rec.get("approval_ref")),
                     "cure_business_days": int(cure), "action": action, "policy_status": policy,
                     "source": _text(rec.get("source")), "status": status, "counsel": counsel,
                     "notes": _text(rec.get("notes")), "origin": kind, "line": n})
    out = pd.DataFrame(rows, columns=[*LIMIT_COLUMNS, "origin", "line"])
    _refuse_overlaps(out, what)
    return out


def _refuse_overlaps(frame: pd.DataFrame, what: str) -> None:
    key = ["metric", "scope_type", "scope", "selector", "unit", "direction", "legal_form"]
    for _, group in frame.groupby(key, sort=True):
        spans = sorted((r["effective_from"] or date.min, r["effective_to"] or date.max, r["line"], r["limit_id"])
                       for r in group.to_dict("records"))
        for (a0, a1, la, ia), (b0, b1, lb, ib) in zip(spans, spans[1:]):
            if b0 < a1:
                raise ValueError(f"{what}: limits {ia} (line {la}) and {ib} (line {lb}) bind the same metric, scope "
                                 f"and selector and are both in force from {b0}: give one an effective_to")


def limit_table(*sources) -> pd.DataFrame:
    """Several limit tables read and joined (the fund's, the example, the legal-form one): ids must stay unique."""
    frames = [s if isinstance(s, pd.DataFrame) and "origin" in s and "line" in s else read_limits(s)
              for s in sources if s is not None]
    if not frames:
        return pd.DataFrame(columns=[*LIMIT_COLUMNS, "origin", "line"])
    out = pd.concat(frames, ignore_index=True)
    dup = out[out["limit_id"].duplicated(keep=False)]
    if len(dup):
        raise ValueError(f"limit_id {dup['limit_id'].iloc[0]} is in two of the limit tables given")
    return out


def example_limits() -> pd.DataFrame:
    """The shipped example table (E01-E30, every row EXAMPLE)."""
    return read_limits(EXAMPLE_LIMITS_CSV)


def monitoring_limits(user=None) -> pd.DataFrame:
    """The table the limit monitoring reads: the fund's table (``None``: none) joined with the whole legal-form table,
    every preset's rows, so that the checks list the other legal form's rules as ``not_applicable`` ("applies under
    ...": which rules depend on the form). The gate adds only its own preset's rows (``legal_form_limits``)."""
    return limit_table(user, read_limits(LEGAL_FORM_LIMITS_CSV, origin="legal_form"))


def legal_form_limits(access) -> pd.DataFrame:
    """The rows of the shipped legal-form table (``legal_form_limits.csv``) for an access preset (its ``name``), and
    those for ``any``."""
    name = getattr(access, "name", str(access))
    table = read_limits(LEGAL_FORM_LIMITS_CSV, origin="legal_form")
    return table[table["legal_form"].isin(("any", name))].reset_index(drop=True)


def limits_in_force(limits, *, as_of, legal_form) -> pd.DataFrame:
    """The rows in force at ``as_of`` for the preset ``legal_form`` (L5.2): ``effective_from <= day <
    effective_to``, ``approval_date <= day`` (a limit approved later is not in force earlier: rule 10) and
    ``legal_form`` ``any`` or the preset."""
    table = limits if isinstance(limits, pd.DataFrame) and "origin" in limits else read_limits(limits)
    day = _when(as_of).date()
    name = getattr(legal_form, "name", str(legal_form))
    keep = []
    for rec in table.to_dict("records"):
        start, end, approved = rec["effective_from"], rec["effective_to"], rec["approval_date"]
        keep.append((start is None or start <= day) and (end is None or day < end)
                    and (approved is None or approved <= day) and rec["legal_form"] in ("any", name))
    return table[keep].reset_index(drop=True)


def read_risk_weights(source=NBFC_RISK_WEIGHTS_CSV) -> pd.DataFrame:
    """The middle layer's risk weights by kind and sector (``RISK_WEIGHT_COLUMNS``; blank sector: any)."""
    frame = rk._frame(source, "the risk weights")
    rk._check_columns(frame, ("kind", "risk_weight_pct"), RISK_WEIGHT_COLUMNS, "the risk weights")
    rows = []
    for n, rec in rk._lines(frame):
        weight = rk._num(rec.get("risk_weight_pct"), f"the risk weights line {n}: risk_weight_pct")
        if weight < 0:
            raise ValueError(f"the risk weights line {n}: a risk weight cannot be negative")
        rows.append({"kind": _text(rec.get("kind")).lower(), "sector": _text(rec.get("sector")).lower(),
                     "risk_weight_pct": weight, "source": _text(rec.get("source")), "status": _text(rec.get("status")),
                     "notes": _text(rec.get("notes"))})
    return pd.DataFrame(rows, columns=list(RISK_WEIGHT_COLUMNS))


# ---------------------------------------------------------------- what metrics are computed from


@dataclass
class MetricInputs:
    """One day's book as the metrics read it, all as frames: the risk run's position rows (``POSITION_RISK_COLUMNS``,
    bonds and swaps), sensitivities, key rates, VaR, stress by position (with each scenario's family and whether it
    is funding only), liquidity and funding liquidity; the engine's NAV history, the day's NAV by scope and each book's
    net cash and borrowing; the engine's positions (``funded``); ratings, stock, issue dates and notified amounts for
    the concentration and when-issued metrics; the risk weights and the board resolution date. ``changes`` is the
    factor history (``risk.FACTOR_CHANGE_COLUMNS``) for the parametric VaR of the gate and the hedge-effect test."""

    day: date
    as_of: datetime
    access: rt.AccessPolicy
    positions: pd.DataFrame
    nav: dict[tuple[str, str], float]
    strategies: dict[str, str]
    sensitivities: pd.DataFrame | None = None
    key_rates: pd.DataFrame | None = None
    var: pd.DataFrame | None = None
    stress_positions: pd.DataFrame | None = None
    scenarios: dict[str, tuple[str, bool]] = field(default_factory=dict)
    liquidity: pd.DataFrame | None = None
    funding_liquidity: pd.DataFrame | None = None
    nav_history: pd.DataFrame | None = None
    balance: dict[str, dict[str, float]] = field(default_factory=dict)
    engine_positions: pd.DataFrame | None = None
    ratings: pd.DataFrame | None = None
    stock: pd.DataFrame | None = None
    issue_dates: dict[str, date] = field(default_factory=dict)
    notified: dict[str, float] = field(default_factory=dict)
    risk_weights: pd.DataFrame | None = None
    board_resolution: date | None = None
    changes: pd.DataFrame | None = None
    risk_settings: rk.RiskSettings = field(default_factory=rk.RiskSettings)
    var_method: str | None = None  # the gate: every VaR and ES by this method (parametric)
    notes: tuple[str, ...] = ()
    _rows: list | None = None
    _wide: Any = None

    @property
    def borrows(self) -> bool:
        return bool(self.access.repo or self.access.treps)

    def rows(self) -> list[dict]:
        """The position rows with ``mv`` (market value: a settled row's value, a pending row's face x dirty / 100, a
        swap's NPV) and ``is_swap``."""
        if self._rows is None:
            out = []
            for rec in self.positions.to_dict("records"):
                kind = _text(rec.get("kind"))
                state = _text(rec.get("state"))
                face = _float(rec.get("face"), 0.0)
                if kind == "ois" or state == "ois":
                    mv = _float(rec.get("value"), 0.0)
                elif state == "pending":
                    mv = face / 100 * _float(rec.get("dirty_price"), 0.0)
                else:
                    mv = _float(rec.get("value"), 0.0)
                out.append({**rec, "kind": kind, "state": state, "face": face, "mv": mv,
                            "is_swap": kind == "ois" or state == "ois", "book": _text(rec.get("book")),
                            "strategy": _text(rec.get("strategy")) or self.strategies.get(_text(rec.get("book")),
                                                                                          _text(rec.get("book")))})
            self._rows = out
        return self._rows

    def scopes(self) -> list[tuple[str, str]]:
        books = sorted({r["book"] for r in self.rows()} | {s for t, s in self.nav if t == "book"})
        strategies = sorted({self.strategies.get(b, b) for b in books} | {r["strategy"] for r in self.rows()})
        return [("fund", "fund"), *(("book", b) for b in books), *(("strategy", s) for s in strategies)]

    def books_of(self, scope_type: str, scope: str) -> list[str]:
        books = sorted({r["book"] for r in self.rows()} | {s for t, s in self.nav if t == "book"}
                       | set(self.balance))
        if scope_type == "fund":
            return books
        if scope_type == "book":
            return [scope] if scope in books else []
        return [b for b in books if self.strategies.get(b, b) == scope]

    def in_scope(self, rec: Mapping, scope_type: str, scope: str) -> bool:
        if scope_type == "fund":
            return True
        if scope_type == "book":
            return rec["book"] == scope
        return rec["strategy"] == scope

    def nav_of(self, scope_type: str, scope: str) -> float:
        return float(self.nav.get((scope_type, scope), math.nan))


def _bucket_of(rec: Mapping) -> str:
    return rec.get("rating_bucket") or ("sovereign" if _text(rec.get("rating_source")) == "sovereign" else "unrated")


def _dimension(rec: Mapping, key: str) -> str:
    if key == "issuer":
        return _text(rec.get("issuer_id"))
    if key == "group":
        return _text(rec.get("group_id")) or _text(rec.get("issuer_id"))
    if key == "sector":
        return _text(rec.get("sector")) or "unknown"
    if key == "rating_bucket":
        return _bucket_of(rec) if _text(rec.get("rating_bucket")) else _bucket_of({**rec, "rating_bucket": ""})
    if key in ("tenor_bucket", "bucket"):
        return _text(rec.get("tenor_bucket"))
    if key == "kind":
        return rec["kind"]
    if key == "isin":
        return _text(rec.get("isin"))
    raise KeyError(key)


def _select(rows: Iterable[dict], selector, dims: Sequence[str], *, default_kinds: Sequence[str] | None = None
            ) -> dict[str, list[dict]]:
    """Rows grouped by the selector: keys of ``dims`` given ``*`` group (the key joins their values with ``:``),
    other given values filter; ``default_kinds`` filter by kind when the selector names none."""
    groups: dict[str, list[dict]] = {}
    by = [k for k in dims if _sel(selector, k) == ("*",)]
    kinds = _sel(selector, "kind")
    for rec in rows:
        if kinds is None and default_kinds is not None and rec["kind"] not in default_kinds:
            continue
        ok = True
        for k in dims:
            values = _sel(selector, k)
            if values is None or values == ("*",):
                continue
            if _dimension(rec, k) not in values:
                ok = False
                break
        if not ok:
            continue
        key = ":".join(_dimension(rec, k) for k in by)
        groups.setdefault(key, []).append(rec)
    return groups


def _abs(selector, value: float) -> float:
    return abs(value) if _sel_one(selector, "abs", "no").lower() == "yes" and _finite(value) else value


def _per_nav(value: float, nav: float, unit: str, *, years: bool = False) -> tuple[float, bool]:
    """``value`` in ``unit`` (``years``: over NAV x 1e-4; ``pct_nav``: percent of NAV); False when the NAV needed is
    not known."""
    if unit in ("years", "pct_nav") and (years or unit == "pct_nav"):
        if not (_finite(nav) and nav > 0):
            return math.nan, False
        return (value / (nav * 1e-4), True) if unit == "years" else (value / nav * 100, True)
    return value, True


# one metric value: (key, value, inputs complete, detail)
Value = tuple[str, float, bool, str]


def _m_dv01(view: MetricInputs, st: str, sc: str, sel, unit: str) -> list[Value]:
    rows = [r for r in view.rows() if view.in_scope(r, st, sc)]
    if _sel(sel, "bucket") is None:
        groups = {"": rows}
    else:
        groups = _select(rows, sel, ("bucket",))
        if _sel(sel, "bucket") == ("*",):
            for b in BUCKETS:
                groups.setdefault(b, [])
    out = []
    for key, members in sorted(groups.items()):
        raw = _fsum(_float(r.get("dv01_yield"), 0.0) for r in members)
        value, ok = _per_nav(_abs(sel, raw), view.nav_of(st, sc), unit, years=True)
        out.append((key, value, ok, f"DV01 Rs {raw:,.2f} per bp of {len(members)} position(s)"
                    + ("" if ok else "; no NAV for years")))
    return out


def _m_krd(view: MetricInputs, st: str, sc: str, sel, unit: str) -> list[Value]:
    table = view.key_rates
    if table is None:
        return [("", math.nan, False, "no key-rate table")]
    mine = table[(table["scope_type"].astype(str) == st) & (table["scope"].astype(str) == sc)]
    curves = _sel(sel, "curve") or ("*",)
    tenors = _sel(sel, "tenor") or ("*",)
    sums: dict[str, float] = {}
    for rec in mine.to_dict("records"):
        curve = _text(rec.get("curve"))
        label = rk.tenor_label(float(rec["tenor"]))
        if curves != ("*",) and curve not in curves:
            continue
        if tenors != ("*",) and label not in tenors:
            continue
        key = ":".join(([curve] if curves == ("*",) else []) + ([label] if tenors == ("*",) else []))
        sums[key] = sums.get(key, 0.0) + _float(rec.get("krd"), 0.0)
    if not sums:
        return [("", 0.0, True, "no key-rate DV01 in scope")]
    out = []
    for key, raw in sorted(sums.items()):
        value, ok = _per_nav(_abs(sel, raw), view.nav_of(st, sc), unit, years=True)
        out.append((key, value, ok, f"KRD Rs {raw:,.2f} per bp"))
    return out


def _m_cs01(view: MetricInputs, st: str, sc: str, sel, unit: str) -> list[Value]:
    rows = [r for r in view.rows() if view.in_scope(r, st, sc) and not r["is_swap"] and _finite(r.get("cs01"))]
    dims = ("rating_bucket", "issuer", "group", "sector", "tenor_bucket", "kind")
    groups = _select(rows, sel, dims)
    if not groups:
        groups = {"": []}
    out = []
    for key, members in sorted(groups.items()):
        raw = _fsum(float(r["cs01"]) for r in members)
        value, ok = _per_nav(_abs(sel, raw), view.nav_of(st, sc), unit, years=True)
        out.append((key, value, ok, f"CS01 Rs {raw:,.2f} per bp of {len(members)} position(s)"))
    return out


def _m_exposure(view: MetricInputs, st: str, sc: str, sel, unit: str, *, kinds=CREDIT_KINDS) -> list[Value]:
    rows = [r for r in view.rows() if view.in_scope(r, st, sc) and not r["is_swap"]]
    groups = _select(rows, sel, ("issuer", "group", "sector", "rating_bucket", "kind", "isin"), default_kinds=kinds)
    if not groups:
        groups = {"": []}
    out = []
    nav = view.nav_of(st, sc)
    for key, members in sorted(groups.items()):
        raw = _fsum(r["mv"] for r in members)
        value, ok = _per_nav(raw, nav, unit)
        out.append((key, value, ok, f"market value Rs {raw:,.2f} of {len(members)} position(s)"
                    + ("" if members else ": none held")))
    return out


def _stock_of(view: MetricInputs, isin: str) -> float | None:
    table = view.stock
    if table is None or not len(table) or "isin" not in table:
        return None
    best = None
    for rec in table[table["isin"].map(_text).str.upper() == isin].to_dict("records"):
        day = rk._date(rec.get("date"), "the stock table's date")
        if day <= view.day and (best is None or day > best[0]):
            best = (day, rk._num(rec.get("outstanding_cr"), "the stock table's outstanding_cr") * 1e7)
    return None if best is None else best[1]


def _m_isin_share(view: MetricInputs, st: str, sc: str, sel, unit: str) -> list[Value]:
    rows = [r for r in view.rows() if view.in_scope(r, st, sc) and not r["is_swap"]]
    sel2 = sel if _sel(sel, "isin") is not None else ((*sel, ("isin", ("*",))))
    groups = _select(rows, tuple(sorted(sel2)), ("isin", "kind"))
    if not groups:
        return [("", 0.0, True, "no position in scope")]
    out = []
    for key, members in sorted(groups.items()):
        face = _fsum(r["face"] for r in members)
        isin = _text(members[0].get("isin"))
        stock = _stock_of(view, isin)
        if stock is None or stock <= 0:
            out.append((key, math.nan, False, f"no outstanding stock for {isin} by {view.day} (the stock table)"))
            continue
        out.append((key, face / stock * 100, True, f"face Rs {face:,.0f} of a stock of Rs {stock:,.0f}"))
    return out


def _notch_of(view: MetricInputs, rec: Mapping) -> tuple[float, str]:
    """(notch, how known) of a credit position: the ratings as known at ``as_of``, else its bucket's worst notch."""
    if view.ratings is not None:
        _, notch = rk.rating_as_known(view.ratings, _text(rec.get("isin")).upper(), _text(rec.get("issuer_id")),
                                      view.as_of)
        if notch is not None:
            return float(notch), "ratings"
    bucket = _text(rec.get("rating_bucket"))
    if bucket in BUCKET_WORST_NOTCH:
        return float(BUCKET_WORST_NOTCH[bucket]), f"bucket {bucket}"
    return math.nan, "unrated"


def _net_by_isin(rows: Iterable[dict]) -> dict[tuple[str, str], list[dict]]:
    out: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        out.setdefault((r["book"], _text(r.get("isin"))), []).append(r)
    return out


def _m_rating_floor(view: MetricInputs, st: str, sc: str, sel, unit: str) -> list[Value]:
    rows = [r for r in view.rows() if view.in_scope(r, st, sc) and not r["is_swap"] and r["kind"] in CREDIT_KINDS]
    held = [members for members in _net_by_isin(rows).values() if _fsum(m["face"] for m in members) > 0]
    note = " (holdings: the gate checks each purchase)" if _sel_one(sel, "of") == "purchases" else ""
    kinds = _sel(sel, "kind")
    if kinds:
        held = [m for m in held if m[0]["kind"] in kinds]
    if unit == "pct_nav":
        below = _grade_notch(_sel_one(sel, "below", "BBB-"), "below")
        total, unknown = 0.0, []
        for members in held:
            notch, _ = _notch_of(view, members[0])
            if not _finite(notch) or notch > below:
                total += _fsum(m["mv"] for m in members)
                if not _finite(notch):
                    unknown.append(_text(members[0].get("isin")))
        value, ok = _per_nav(total, view.nav_of(st, sc), unit)
        return [("", value, ok, f"market value Rs {total:,.2f} rated below notch {below:g}"
                 + (f" (unrated counted: {', '.join(sorted(unknown))})" if unknown else "") + note)]
    by_isin = _sel(sel, "isin") == ("*",)
    out: list[Value] = []
    worst, where, unrated = -math.inf, "", []
    for members in sorted(held, key=lambda m: _text(m[0].get("isin"))):
        notch, how = _notch_of(view, members[0])
        isin = _text(members[0].get("isin"))
        if not _finite(notch):
            unrated.append(isin)
            if by_isin:
                out.append((isin, math.nan, False, f"{isin} has no rating as known at {view.as_of:%Y-%m-%d %H:%M}"))
            continue
        if by_isin:
            out.append((isin, notch, True, f"{isin} at notch {notch:g} ({how}){note}"))
        if notch > worst:
            worst, where = notch, f"{isin} ({how})"
    if by_isin:
        return out or [("", 0.0, True, "no credit position held")]
    if worst == -math.inf:
        return [("", math.nan if unrated else 0.0, not unrated,
                 f"unrated: {', '.join(unrated)}" if unrated else "no credit position held")]
    return [("", worst, not unrated, f"worst {where}" + (f"; unrated: {', '.join(unrated)}" if unrated else "")
             + note)]


def _m_max_tenor(view: MetricInputs, st: str, sc: str, sel, unit: str) -> list[Value]:
    rows = [r for r in view.rows() if view.in_scope(r, st, sc) and not r["is_swap"]]
    kinds = _sel(sel, "kind")
    groups: dict[str, list] = {}
    for (book, isin), members in _net_by_isin(rows).items():
        if abs(_fsum(m["face"] for m in members)) <= 1e-6:
            continue
        kind = members[0]["kind"]
        if kinds not in (None, ("*",)) and kind not in kinds:
            continue
        key = kind if kinds == ("*",) else ""
        maturity = rk._opt_date(members[0].get("maturity_date"), "maturity_date")
        if maturity is not None:
            groups.setdefault(key, []).append(((maturity - view.day).days / _YEAR, isin))
    if not groups:
        return [("", 0.0, True, "no position held")]
    out = []
    for key, items in sorted(groups.items()):
        years, isin = max(items)
        out.append((key, years, True, f"{isin} matures in {years:.4f} years"))
    return out


def _var_row(view: MetricInputs, st: str, sc: str, sel, what: str) -> tuple[float, float, bool, str]:
    """(rupees, NAV, complete, detail) of the VaR or ES a selector names, from the risk run's VaR table."""
    horizon = int(float(_sel_one(sel, "horizon", "1")))
    alpha = float(_sel_one(sel, "alpha", "0.99" if what == "var" else "0.975"))
    window = _sel_one(sel, "window", "current")
    rule = _sel_one(sel, "horizon_rule")
    nav = view.nav_of(st, sc)
    if view.var_method is not None:
        return _gate_var(view, st, sc, what, horizon, alpha, nav)
    table = view.var
    if table is None or not len(table):
        return math.nan, nav, False, "no VaR table"
    mine = table[(table["scope_type"].astype(str) == st) & (table["scope"].astype(str) == sc)
                 & (pd.to_numeric(table["horizon_days"]) == horizon) & (table["window"].astype(str) == window)]
    rules = mine["horizon_rule"].fillna("").astype(str)
    mine = mine[rules == rule] if rule else mine[rules != "liquidity_horizons"]
    column = "alpha_var" if what == "var" else "alpha_es"
    mine = mine[np.isclose(pd.to_numeric(mine[column]), alpha, rtol=0, atol=1e-12)]
    method = _sel_one(sel, "method", "preferred")
    order = [method] if method != "preferred" else ["hs_full", "hs_sensitivity", "parametric"]
    for m in order:
        hit = mine[mine["method"].astype(str) == m]
        if not len(hit):
            continue
        rec = hit.iloc[0]
        value = _float(rec.get(what))
        status = _text(rec.get("status"))
        coverage = _float(rec.get("coverage"))
        if method == "preferred" and not _finite(value) and m != order[-1]:
            continue
        complete = status in ("ok", "short_history") and _finite(value) and _finite(coverage) and \
            coverage >= 1 - 1e-12
        detail = (f"{m} {horizon}d {window} {what} {alpha:g}: status {status}, {int(_float(rec.get('n_obs'), 0))} "
                  f"obs, coverage {coverage:.4f}" + (f"; {_text(rec.get('flags'))}" if _text(rec.get("flags"))
                                                      else ""))
        return value, nav, complete, detail
    return math.nan, nav, False, f"no {method} {horizon}d {window} {what} row at {alpha:g} for {st} {sc}"


def _m_var(view: MetricInputs, st: str, sc: str, sel, unit: str, what: str = "var") -> list[Value]:
    value, nav, complete, detail = _var_row(view, st, sc, sel, what)
    out, ok = _per_nav(value, nav, unit)
    return [("", out, complete and ok, detail)]


def _m_es(view: MetricInputs, st: str, sc: str, sel, unit: str) -> list[Value]:
    return _m_var(view, st, sc, sel, unit, "es")


def _scenario_losses(view: MetricInputs, st: str, sc: str) -> dict[str, tuple[float, str]]:
    """``{scenario: (loss, not-applicable reason)}`` of a scope (loss = minus the P&L summed over its positions)."""
    table = view.stress_positions
    if table is None or not len(table):
        return {}
    out = {}
    for sid, group in table.groupby("scenario_id", sort=True):
        sid = str(sid)
        family, funding_only = view.scenarios.get(sid, ("", False))
        if funding_only and not view.borrows:
            out[sid] = (math.nan, f"not_applicable: {view.access.name} does not borrow (a funding scenario)")
            continue
        mine = [r for r in group.to_dict("records") if st == "fund" or (st == "book" and _text(r["book"]) == sc)
                or (st == "strategy" and _text(r["strategy"]) == sc)]
        out[sid] = (-_fsum(_float(r["pnl"], 0.0) for r in mine), "")
    return out


def _m_stress_loss(view: MetricInputs, st: str, sc: str, sel, unit: str) -> list[Value]:
    losses = _scenario_losses(view, st, sc)
    nav = view.nav_of(st, sc)
    scenario, family = _sel(sel, "scenario"), _sel_one(sel, "family")
    if scenario == ("*",):
        out = []
        for sid, (loss, na) in losses.items():
            value, ok = _per_nav(loss, nav, unit)
            out.append((sid, value, ok and not na, na or f"scenario {sid}"))
        return out or [("", math.nan, False, "no stress scenario")]
    if scenario:
        out = []
        for sid in scenario:
            if sid not in losses:
                out.append((sid, math.nan, False, f"scenario {sid} was not run (not in force or unknown)"))
                continue
            loss, na = losses[sid]
            value, ok = _per_nav(loss, nav, unit)
            out.append((sid if len(scenario) > 1 else "", value, ok and not na, na or f"scenario {sid}"))
        return out
    pool = {s: v for s, v in losses.items() if family is None or view.scenarios.get(s, ("",))[0] == family}
    if not pool:
        return [("", math.nan, False, f"no {family or ''} scenario in force".replace("  ", " "))]
    live = {s: v for s, v in pool.items() if not v[1]}
    if not live:
        return [("", math.nan, True, next(iter(pool.values()))[1])]
    worst = sorted(live.items(), key=lambda kv: (-kv[1][0], kv[0]))[0]
    value, ok = _per_nav(worst[1][0], nav, unit)
    return [("", value, ok, f"worst {family or 'scenario'}: {worst[0]} (loss Rs {worst[1][0]:,.2f})")]


def _m_jtd(view: MetricInputs, st: str, sc: str, sel, unit: str) -> list[Value]:
    rows = [r for r in view.rows() if view.in_scope(r, st, sc) and not r["is_swap"] and _finite(r.get("jtd"))]
    groups = _select(rows, sel, ("issuer", "group"))
    if not groups:
        groups = {"": []}
    nav = view.nav_of(st, sc)
    out = []
    for key, members in sorted(groups.items()):
        raw = _fsum(float(r["jtd"]) for r in members)
        value, ok = _per_nav(raw, nav, unit)
        out.append((key, value, ok, f"JTD Rs {raw:,.2f} of {len(members)} position(s)"))
    return out


def _scope_series(view: MetricInputs, st: str, sc: str) -> pd.DataFrame | None:
    """The scope's NAV, flow and P&L by date (its books summed) up to the day, from the engine's NAV history."""
    table = view.nav_history
    if table is None or not len(table):
        return None
    books = set(view.books_of(st, sc))
    rows = []
    for rec in table.to_dict("records"):
        book = _text(rec.get("book"))
        if book == "fund" or book not in books:
            continue
        day = rk._date(rec.get("date"), "the NAV history's date")
        if day > view.day:
            continue
        rows.append((day, book, _float(rec.get("nav"), 0.0), _float(rec.get("flow"), 0.0),
                     _float(rec.get("pnl"), 0.0)))
    if not rows:
        return None
    frame = pd.DataFrame(rows, columns=["date", "book", "nav", "flow", "pnl"])
    out = frame.groupby("date", sort=True).agg({"nav": lambda s: _fsum(s), "flow": lambda s: _fsum(s),
                                                "pnl": lambda s: _fsum(s)})
    return out


def _index(series: pd.DataFrame) -> pd.Series:
    prev, index, out = 0.0, 1.0, []
    for nav, flow in zip(series["nav"], series["flow"]):
        base = prev + flow
        ret = (nav - prev - flow) / base if base > 0 else math.nan
        if math.isfinite(ret):
            index *= 1 + ret
        out.append(index)
        prev = nav
    return pd.Series(out, index=series.index, dtype=float)


def _m_drawdown(view: MetricInputs, st: str, sc: str, sel, unit: str) -> list[Value]:
    series = _scope_series(view, st, sc)
    if series is None:
        return [("", math.nan, False, "no NAV history for the scope")]
    index = _index(series)
    peak = float(index.max())
    dd = (float(index.iloc[-1]) / peak - 1) * 100
    return [("", dd, True, f"index {float(index.iloc[-1]):.6f} against its peak {peak:.6f} "
             f"({len(index)} days to {index.index[-1]})")]


def _m_loss_window(view: MetricInputs, st: str, sc: str, sel, unit: str) -> list[Value]:
    days = int(float(_sel_one(sel, "days", "20")))
    base = _sel_one(sel, "base", "own")
    series = _scope_series(view, st, sc)
    if series is None:
        return [("", math.nan, False, "no NAV history for the scope")]
    window = series.iloc[-days:]
    pnl = _fsum(window["pnl"])
    ref = series if base == "own" else _scope_series(view, "fund", "fund")
    if ref is None or not len(ref):
        return [("", math.nan, False, "no fund NAV history")]
    first = window.index[0]
    before = ref[ref.index < first]
    start = float(before["nav"].iloc[-1]) if len(before) else float(ref.loc[first, "nav"] - ref.loc[first, "pnl"]) \
        if first in ref.index else math.nan
    if not (_finite(start) and start > 0):
        return [("", math.nan, False, "no NAV at the window's start")]
    return [("", pnl / start * 100, True, f"P&L Rs {pnl:,.2f} over {len(window)} day(s) from {first} against "
             f"{'the fund' if base == 'fund' else 'its'} NAV Rs {start:,.2f}")]


def _book_cash(view: MetricInputs, book: str) -> tuple[float, float, float, float]:
    """(net cash, borrowed now, purchases to settle, sales to settle) of a book: net cash is cash less borrowing plus
    money lent and receivables (the engine's NAV row, else NAV less the positions' values)."""
    rows = [r for r in view.rows() if r["book"] == book and not r["is_swap"] and r["state"] == "pending"]
    buys = _fsum(r["mv"] for r in rows if r["face"] > 0)
    sells = _fsum(-r["mv"] for r in rows if r["face"] < 0)
    bal = view.balance.get(book, {})
    net = float(bal.get("net_cash", math.nan))
    borrowed = float(bal.get("borrowed", 0.0))
    return net, borrowed, buys, sells


def _scope_balance(view: MetricInputs, st: str, sc: str) -> dict[str, float]:
    totals = {"net_cash": 0.0, "borrowed": 0.0, "buys": 0.0, "sells": 0.0, "need": 0.0, "complete": True}
    for book in view.books_of(st, sc):
        net, borrowed, buys, sells = _book_cash(view, book)
        if not _finite(net):
            totals["complete"] = False
            net = 0.0
        totals["net_cash"] += net
        totals["borrowed"] += borrowed
        totals["buys"] += buys
        totals["sells"] += sells
        totals["need"] += max(0.0, buys - sells - net)
    return totals


def _m_borrowing(view: MetricInputs, st: str, sc: str, sel, unit: str) -> list[Value]:
    b = _scope_balance(view, st, sc)
    return [("", b["need"], bool(b["complete"]),
             f"purchases to settle Rs {b['buys']:,.2f}, sales Rs {b['sells']:,.2f}, net cash Rs {b['net_cash']:,.2f}"
             + ("" if b["complete"] else "; a book's cash is not known"))]


def _m_leverage(view: MetricInputs, st: str, sc: str, sel, unit: str) -> list[Value]:
    nav = view.nav_of(st, sc)
    if not (_finite(nav) and nav > 0):
        return [("", math.nan, False, "no NAV for the scope")]
    if _sel_one(sel, "kind", "gross") == "repo":
        b = _scope_balance(view, st, sc)
        return [("", b["need"] / nav, bool(b["complete"]), f"borrowing needed Rs {b['need']:,.2f}")]
    rows = [r for r in view.rows() if view.in_scope(r, st, sc) and not r["is_swap"]]
    gross = _fsum(abs(_fsum(m["mv"] for m in members)) for members in _net_by_isin(rows).values())
    return [("", gross / nav, True, f"gross bonds Rs {gross:,.2f} (trade-date view) over NAV Rs {nav:,.2f}")]


def _liabilities(view: MetricInputs, st: str, sc: str) -> tuple[float, bool, str]:
    b = _scope_balance(view, st, sc)
    borrowed = max(b["borrowed"], max(0.0, -b["net_cash"])) if view.borrows else b["need"]
    return borrowed + b["buys"], bool(b["complete"]), (f"borrowing Rs {borrowed:,.2f} and payables of purchases to "
                                                        f"settle Rs {b['buys']:,.2f}")


def _m_outside_liabilities(view: MetricInputs, st: str, sc: str, sel, unit: str) -> list[Value]:
    nav = view.nav_of(st, sc)
    total, ok, detail = _liabilities(view, st, sc)
    if not (_finite(nav) and nav > 0):
        return [("", math.nan, False, "no NAV (owned fund) for the scope")]
    return [("", total / nav, ok, f"{detail} over owned fund (NAV) Rs {nav:,.2f}")]


def _m_total_assets(view: MetricInputs, st: str, sc: str, sel, unit: str) -> list[Value]:
    nav = view.nav_of(st, sc)
    total, ok, detail = _liabilities(view, st, sc)
    if not _finite(nav):
        return [("", math.nan, False, "no NAV for the scope")]
    return [("", nav + total, ok, f"NAV Rs {nav:,.2f} plus {detail}")]


def _liq_rows(view: MetricInputs, st: str, sc: str) -> list[dict] | None:
    table = view.liquidity
    if table is None:
        return None
    return [r for r in table.to_dict("records") if _text(r.get("kind")) != "ois"
            and (st == "fund" or (st == "book" and _text(r["book"]) == sc)
                 or (st == "strategy" and _text(r["strategy"]) == sc))]


def _m_illiquid_share(view: MetricInputs, st: str, sc: str, sel, unit: str) -> list[Value]:
    rows = _liq_rows(view, st, sc)
    if rows is None:
        return [("", math.nan, False, "no liquidity table")]
    flag = [r for r in rows if str(r.get("illiquid")).strip().lower() in ("true", "1", "yes")]
    raw = _fsum(abs(_float(r.get("value"), 0.0)) for r in flag)
    value, ok = _per_nav(raw, view.nav_of(st, sc), unit)
    return [("", value, ok, f"{len(flag)} illiquid position(s) worth Rs {raw:,.2f}")]


def _m_liquid_share(view: MetricInputs, st: str, sc: str, sel, unit: str) -> list[Value]:
    rows = _liq_rows(view, st, sc)
    if rows is None:
        return [("", math.nan, False, "no liquidity table")]
    n = float(_sel_one(sel, "days", "5"))
    total = 0.0
    for r in rows:
        days, value = _float(r.get("days_to_liquidate"), math.inf), abs(_float(r.get("value"), 0.0))
        total += value if days <= 0 else min(1.0, n / days) * value if math.isfinite(days) else 0.0
    value, ok = _per_nav(total, view.nav_of(st, sc), unit)
    return [("", value, ok, f"Rs {total:,.2f} saleable within {n:g} business days")]


def _m_days_to_liquidate(view: MetricInputs, st: str, sc: str, sel, unit: str) -> list[Value]:
    rows = _liq_rows(view, st, sc)
    if rows is None:
        return [("", math.nan, False, "no liquidity table")]
    if not rows:
        return [("", 0.0, True, "no position")]
    days = [(_float(r.get("days_to_liquidate"), math.inf), abs(_float(r.get("value"), 0.0)), _text(r.get("isin")))
            for r in rows]
    if _sel_one(sel, "stat", "max") == "mean":
        weight = _fsum(w for _, w, _ in days)
        if any(not math.isfinite(d) for d, w, _ in days if w > 0):
            return [("", math.inf, True, "a position with no volume (no ADV) has infinite days")]
        return [("", _fsum(d * w for d, w, _ in days) / weight if weight else 0.0, True, "value-weighted mean")]
    worst = max(days, key=lambda x: (x[0], x[2]))
    return [("", worst[0], True, f"longest: {worst[2]}")]


def _m_ois_hedge_ratio(view: MetricInputs, st: str, sc: str, sel, unit: str) -> list[Value]:
    rows = [r for r in view.rows() if view.in_scope(r, st, sc)]
    swaps = [_float(r.get("dv01_yield"), 0.0) for r in rows if r["is_swap"]]
    bonds = _fsum(_float(r.get("dv01_yield"), 0.0) for r in rows if not r["is_swap"])
    if not swaps:
        return [("", 0.0, True, "no swaps")]
    s = _fsum(swaps)
    if abs(s) <= 0.01 * _fsum(abs(x) for x in swaps):
        return [("", 0.0, True, f"swaps offset each other (DV01 Rs {s:,.2f} per bp)")]
    if bonds != 0 and s * bonds <= 0:
        return [("", abs(s) / abs(bonds), True, f"swaps' DV01 Rs {s:,.2f} against the bonds' Rs {bonds:,.2f}")]
    return [("", math.inf, True, f"swaps' DV01 Rs {s:,.2f} per bp "
             + ("with no bond DV01 to hedge" if bonds == 0 else f"of the same sign as the bonds' Rs {bonds:,.2f}"))]


def _wide_changes(view: MetricInputs) -> pd.DataFrame | None:
    """The factor history as a date x factor frame of changes (bp), dates up to the day."""
    if view._wide is None and view.changes is not None and len(view.changes):
        frame = view.changes
        days = [rk._date(v, "the factor changes' date") for v in frame["date"]]
        long = pd.DataFrame({"date": days, "factor": frame["factor"].astype(str),
                             "change": pd.to_numeric(frame["change_bp"], errors="coerce")})
        long = long[long["date"] <= view.day]
        view._wide = long.pivot_table(index="date", columns="factor", values="change", aggfunc="first").sort_index()
    return view._wide


def parametric_from_frames(sens: pd.DataFrame, changes: pd.DataFrame, settings: rk.RiskSettings, *,
                           horizon_days: int = 1, alpha_var: float | None = None, alpha_es: float | None = None
                           ) -> tuple[float, float, float, int, str]:
    """``(VaR, ES, coverage, observations, detail)`` of a set of sensitivity rows (``SENSITIVITY_COLUMNS``; par-quote
    rows left out) on a wide factor history (date x factor, bp): the factors with at least ``min_obs_hard`` changes,
    the dates on which all of them have one, the EWMA (or sample) covariance of the latest of them, as
    ``risk.parametric_var`` computes it (no proxies). Raises below ``min_obs_hard`` observations."""
    a_var = settings.var_alpha if alpha_var is None else alpha_var
    a_es = settings.es_alpha if alpha_es is None else alpha_es
    use = sens[sens["basis"].astype(str) != "par"] if len(sens) else sens
    s_by: dict[str, float] = {}
    for f, v in zip(use["factor"].astype(str), pd.to_numeric(use["sensitivity"])):
        s_by[f] = s_by.get(f, 0.0) + float(v)
    total = _fsum(abs(v) for v in s_by.values())
    counts = {f: int(changes[f].notna().sum()) for f in changes.columns} if changes is not None else {}
    used = sorted(f for f in s_by if counts.get(f, 0) >= settings.min_obs_hard)
    covered = _fsum(abs(s_by[f]) for f in used)
    coverage = covered / total if total > 0 else 1.0
    if not used:
        if total > 0:
            raise ValueError("no factor of the scope has a history of min_obs_hard changes")
        return 0.0, 0.0, 1.0, 0, "no sensitivity"
    block = changes[used].to_numpy(dtype=float)
    rows = np.flatnonzero(np.all(np.isfinite(block), axis=1))
    if settings.cov_method == "ewma":
        rows = rows[-settings.ewma_max_obs:]
    else:
        rows = rows[-settings.window:]
    if len(rows) < settings.min_obs_hard:
        raise ValueError(f"{len(rows)} observation(s) with every factor, below min_obs_hard {settings.min_obs_hard}")
    x = block[rows]
    if settings.cov_method == "ewma":
        cov, n = rk.ewma_covariance(x, lam=settings.ewma_lambda, max_obs=settings.ewma_max_obs)
    else:
        cov, n = rk.sample_covariance(x)
    s = np.array([s_by[f] for f in used])
    sigma = math.sqrt(max(float(s @ cov @ s), 0.0))
    var, es = rk.normal_var_es(sigma, a_var, a_es, horizon_days)
    return var, es, coverage, n, f"parametric {settings.cov_method}:{n} on {len(used)} factor(s)"


def _sens_in_scope(view: MetricInputs, st: str, sc: str, *, swaps: bool = True) -> pd.DataFrame:
    sens = view.sensitivities
    if sens is None or not len(sens):
        return pd.DataFrame(columns=list(rk.SENSITIVITY_COLUMNS))
    keep = []
    for rec in sens.to_dict("records"):
        book = _text(rec.get("book"))
        strategy = _text(rec.get("strategy")) or view.strategies.get(book, book)
        inside = st == "fund" or (st == "book" and book == sc) or (st == "strategy" and strategy == sc)
        is_swap = _text(rec.get("isin")).startswith("OIS:")
        keep.append(inside and (swaps or not is_swap))
    return sens[keep]


def _gate_var(view: MetricInputs, st: str, sc: str, what: str, horizon: int, alpha: float, nav: float
              ) -> tuple[float, float, bool, str]:
    wide = _wide_changes(view)
    if wide is None:
        return math.nan, nav, False, "no factor history for the parametric VaR"
    try:
        if what == "var":
            value, _, coverage, n, detail = parametric_from_frames(_sens_in_scope(view, st, sc), wide,
                                                                   view.risk_settings, horizon_days=horizon,
                                                                   alpha_var=alpha)
        else:
            _, value, coverage, n, detail = parametric_from_frames(_sens_in_scope(view, st, sc), wide,
                                                                   view.risk_settings, horizon_days=horizon,
                                                                   alpha_es=alpha)
    except ValueError as exc:
        return math.nan, nav, False, f"parametric {what} refused: {exc}"
    return value, nav, coverage >= 1 - 1e-12, f"{detail}, coverage {coverage:.4f} (the gate's approximation)"


def _abs_krd_sum(sens: pd.DataFrame) -> float:
    """The sum over key tenors of the absolute zero-rate KRD, each tenor's G-sec and OIS (and any other curve's)
    KRDs netted (UNVERIFIED: the basis between the curves is left to the VaR half of the hedge-effect test), so that a
    swap at the bond's own tenor reduces it and one at another tenor raises it (T26)."""
    use = sens[(sens["basis"].astype(str) == "zero")] if len(sens) else sens
    sums: dict[float, float] = {}
    for tenor, value in zip(pd.to_numeric(use["tenor"]), pd.to_numeric(use["sensitivity"])):
        if math.isfinite(float(tenor)):
            sums[float(tenor)] = sums.get(float(tenor), 0.0) + float(value)
    return _fsum(abs(v) for v in sums.values())


def _ratio(a: float, b: float) -> float:
    if b == 0:
        return 0.0 if a == 0 else math.inf
    return a / b


def _m_ois_hedge_effect(view: MetricInputs, st: str, sc: str, sel, unit: str) -> list[Value]:
    with_s = _sens_in_scope(view, st, sc)
    if not len(with_s) or not with_s["isin"].astype(str).str.startswith("OIS:").any():
        return [("", 1.0, True, "no swaps in scope")]
    without = _sens_in_scope(view, st, sc, swaps=False)
    k_with, k_without = _abs_krd_sum(with_s), _abs_krd_sum(without)
    ratio_k = 1.0 if k_with == k_without else _ratio(k_with, k_without)
    detail = f"sum of absolute KRDs Rs {k_with:,.2f} with the swaps, Rs {k_without:,.2f} without"
    wide = _wide_changes(view)
    if wide is None:
        return [("", ratio_k, False, f"{detail}; no factor history: the VaR test not computed")]
    try:
        v_with = parametric_from_frames(with_s, wide, view.risk_settings)[0]
        v_without = parametric_from_frames(without, wide, view.risk_settings)[0]
    except ValueError as exc:
        return [("", ratio_k, False, f"{detail}; the VaR test refused: {exc}")]
    ratio_v = 1.0 if v_with == v_without else _ratio(v_with, v_without)
    return [("", max(ratio_k, ratio_v), True, f"{detail}; parametric VaR Rs {v_with:,.2f} with, Rs {v_without:,.2f} "
             "without")]


def _when_issued(view: MetricInputs, rec: Mapping) -> bool:
    issue = view.issue_dates.get(_text(rec.get("isin")).upper())
    return rec["state"] == "pending" and issue is not None and issue > view.day


def _m_short_face(view: MetricInputs, st: str, sc: str, sel, unit: str) -> list[Value]:
    rows = [r for r in view.rows() if view.in_scope(r, st, sc) and not r["is_swap"] and not _when_issued(view, r)]
    kinds = _sel(sel, "kind")
    total, names = 0.0, []
    for (book, isin), members in sorted(_net_by_isin(rows).items()):
        if kinds and members[0]["kind"] not in kinds:
            continue
        net = _fsum(m["face"] for m in members)
        if net < -1e-6:
            total += -net
            names.append(f"{book} {isin}")
    return [("", total, True, f"short: {', '.join(names)}" if names else "no short position")]


def _m_when_issued(view: MetricInputs, st: str, sc: str, sel, unit: str) -> list[Value]:
    side = _sel_one(sel, "side", "long")
    rows = [r for r in view.rows() if view.in_scope(r, st, sc) and not r["is_swap"] and _when_issued(view, r)]
    by: dict[str, float] = {}
    for r in rows:
        isin = _text(r.get("isin")).upper()
        by[isin] = by.get(isin, 0.0) + r["face"]
    out = []
    for isin, net in sorted(by.items()):
        amount = net if side == "long" else -net
        if amount <= 0:
            continue
        notified = view.notified.get(isin)
        if notified is None or notified <= 0:
            out.append((isin, math.nan, False, f"no notified amount for {isin} (the auction table)"))
            continue
        out.append((isin, amount / (notified * 1e7) * 100, True, f"when-issued {side} Rs {amount:,.0f} of a "
                    f"notified Rs {notified:,.2f} crore"))
    return out or [("", 0.0, True, f"no when-issued {side} position")]


def _risk_weight(view: MetricInputs, kind: str, sector: str) -> tuple[float, bool]:
    table = view.risk_weights if view.risk_weights is not None else read_risk_weights()
    exact = table[(table["kind"] == kind) & (table["sector"] == (sector or "").lower())]
    if len(exact):
        return float(exact["risk_weight_pct"].iloc[0]), True
    plain = table[(table["kind"] == kind) & (table["sector"] == "")]
    if len(plain):
        return float(plain["risk_weight_pct"].iloc[0]), True
    return 100.0, False


def _m_crar(view: MetricInputs, st: str, sc: str, sel, unit: str) -> list[Value]:
    nav = view.nav_of(st, sc)
    rows = [r for r in view.rows() if view.in_scope(r, st, sc) and not r["is_swap"]]
    rwa, unknown = 0.0, []
    for members in _net_by_isin(rows).values():
        mv = _fsum(m["mv"] for m in members)
        if mv <= 0:
            continue
        weight, known = _risk_weight(view, members[0]["kind"], _text(members[0].get("sector")))
        if not known:
            unknown.append(members[0]["kind"])
        rwa += weight / 100 * mv
    if not _finite(nav):
        return [("", math.nan, False, "no NAV (capital) for the scope")]
    value = nav / rwa * 100 if rwa > 0 else math.inf
    return [("", value, True, f"capital (NAV) Rs {nav:,.2f} over risk-weighted assets Rs {rwa:,.2f}; OIS not "
             "weighted" + (f"; kinds without a weight taken at 100%: {sorted(set(unknown))}" if unknown else ""))]


def _m_single(view: MetricInputs, st: str, sc: str, sel, unit: str, dim: str) -> list[Value]:
    sel2 = sel if _sel(sel, dim) is not None else tuple(sorted((*sel, (dim, ("*",)))))
    out = _m_exposure(view, st, sc, sel2, "pct_nav")
    return [(k, v, ok, d.replace("market value", "exposure") + " over Tier 1 (NAV)") for k, v, ok, d in out]


def _m_alm(view: MetricInputs, st: str, sc: str, sel, unit: str) -> list[Value]:
    if not view.borrows:
        return [("", math.nan, True, f"not_applicable: {view.access.name} does not borrow (no ALM statement)")]
    table = view.funding_liquidity
    if table is None or not len(table):
        return [("", math.nan, False, "no funding liquidity table")]
    rows = [r for r in table.to_dict("records") if not _text(r.get("scenario_id"))
            and str(r.get("applicable")).strip().lower() in ("true", "1", "yes")
            and _text(r.get("book")) in set(view.books_of(st, sc))]
    if not rows:
        return [("", math.nan, False, "no ALM row for the scope")]
    buckets = _sel(sel, "bucket") or ("*",)
    names = list(ALM_KEYS) if buckets == ("*",) else list(buckets)
    out = []
    for name in names:
        values = [_float(r.get(ALM_KEYS[name])) for r in rows]
        finite = [v for v in values if _finite(v)]
        value = max(finite) * 100 if finite else 0.0
        key = name if buckets == ("*",) or len(names) > 1 else ""
        out.append((key, value, True, f"{name}: the largest over {len(rows)} book(s)" if len(rows) > 1 else name))
    return out


def _m_nof(view: MetricInputs, st: str, sc: str, sel, unit: str) -> list[Value]:
    nav = view.nav_of(st, sc)
    return [("", nav, _finite(nav), "net owned fund taken as NAV (UNVERIFIED)")]


def _m_settlement_cover(view: MetricInputs, st: str, sc: str, sel, unit: str) -> list[Value]:
    b = _scope_balance(view, st, sc)
    if b["buys"] <= 0:
        return [("", math.inf, bool(b["complete"]), "no purchase to settle")]
    return [("", (b["net_cash"] + b["sells"]) / b["buys"], bool(b["complete"]),
             f"net cash Rs {b['net_cash']:,.2f} and sales Rs {b['sells']:,.2f} over purchases Rs {b['buys']:,.2f}")]


def _m_corporate_repo(view: MetricInputs, st: str, sc: str, sel, unit: str) -> list[Value]:
    table = view.engine_positions
    if table is None or "funded" not in table:
        return [("", 0.0, True, "no funded column in the engine's positions")]
    books = set(view.books_of(st, sc))
    total = _fsum(_float(r.get("funded"), 0.0) for r in table.to_dict("records")
                  if _text(r.get("kind")) == "corporate" and _text(r.get("book")) in books)
    return [("", total, True, f"corporate bonds funded in repo Rs {total:,.2f}")]


def _m_board_resolution(view: MetricInputs, st: str, sc: str, sel, unit: str) -> list[Value]:
    start = date(view.day.year if view.day.month >= 4 else view.day.year - 1, 4, 1)
    when = view.board_resolution
    if when is None:
        return [("", 0.0, True, "no board resolution date recorded (board_resolution_date)")]
    ok = start <= when <= view.day
    return [("", 1.0 if ok else 0.0, True, f"board resolution of {when}; the financial year began {start}")]


_METRIC_FUNCTIONS: dict[str, Callable] = {
    "dv01": _m_dv01, "krd": _m_krd, "cs01": _m_cs01, "exposure": _m_exposure, "isin_share": _m_isin_share,
    "rating_floor": _m_rating_floor, "max_tenor": _m_max_tenor, "var": _m_var, "es": _m_es,
    "stress_loss": _m_stress_loss, "jtd": _m_jtd, "drawdown": _m_drawdown, "loss_window": _m_loss_window,
    "leverage": _m_leverage, "outside_liabilities": _m_outside_liabilities, "total_assets": _m_total_assets,
    "borrowing": _m_borrowing, "illiquid_share": _m_illiquid_share, "liquid_share": _m_liquid_share,
    "days_to_liquidate": _m_days_to_liquidate, "ois_hedge_ratio": _m_ois_hedge_ratio,
    "ois_hedge_effect": _m_ois_hedge_effect, "short_face": _m_short_face, "when_issued": _m_when_issued,
    "crar": _m_crar, "tier1": _m_crar, "single_party": lambda v, t, s, sel, u: _m_single(v, t, s, sel, u, "issuer"),
    "single_group": lambda v, t, s, sel, u: _m_single(v, t, s, sel, u, "group"), "alm_mismatch": _m_alm,
    "nof": _m_nof, "settlement_cover": _m_settlement_cover, "corporate_repo": _m_corporate_repo,
    "board_resolution": _m_board_resolution,
}


def metric_values(view: MetricInputs, metric: str, scope_type: str, scope: str, selector, unit: str
                  ) -> list[Value]:
    """The values of one metric for one scope (``(key, value, inputs complete, detail)``), the selector parsed or as
    text; a layer threshold (``min_assets_cr``, ``max_assets_cr``) not met gives one ``not_applicable`` value."""
    sel = parse_selector(selector, metric) if isinstance(selector, str) else tuple(selector)
    lo, hi = _sel_one(sel, "min_assets_cr"), _sel_one(sel, "max_assets_cr")
    if lo is not None or hi is not None:
        assets = _m_total_assets(view, "fund", "fund", (), "rs")[0][1]
        crore = assets / 1e7 if _finite(assets) else math.nan
        if not _finite(crore):
            return [("", math.nan, False, "the fund's total assets are not known (layer threshold)")]
        if lo is not None and crore < float(lo):
            return [("", math.nan, True, f"not_applicable: total assets Rs {crore:,.2f} crore are below Rs "
                     f"{float(lo):g} crore")]
        if hi is not None and crore >= float(hi):
            return [("", math.nan, True, f"not_applicable: total assets Rs {crore:,.2f} crore are not below Rs "
                     f"{float(hi):g} crore")]
    return _METRIC_FUNCTIONS[metric](view, scope_type, scope, sel, unit)


def _scopes_for(view: MetricInputs, scope_type: str, scope: str) -> list[tuple[str, str]]:
    if scope_type == "fund":
        return [("fund", "fund")]
    known = [s for t, s in view.scopes() if t == scope_type]
    return [(scope_type, s) for s in known] if scope == "*" else [(scope_type, scope)]


def metrics_frame(view: MetricInputs, limits) -> pd.DataFrame:
    """``METRIC_COLUMNS``: every (metric, scope, selector, unit) the limit rows ask for (all their rows, in force or
    not), each key's value, sorted by metric, scope and key."""
    table = limits if isinstance(limits, pd.DataFrame) and "origin" in limits else read_limits(limits)
    wanted = sorted({(r["metric"], r["scope_type"], r["scope"], r["selector"], r["unit"])
                     for r in table.to_dict("records")})
    rows, seen = [], set()
    for metric, scope_type, scope, selector, unit in wanted:
        for stype, sc in _scopes_for(view, scope_type, scope):
            if (metric, stype, sc, selector, unit) in seen:
                continue
            seen.add((metric, stype, sc, selector, unit))
            for key, value, complete, detail in metric_values(view, metric, stype, sc, selector, unit):
                rows.append({"date": view.day, "as_of": rk._iso(view.as_of), "metric": metric, "scope_type": stype,
                             "scope": sc, "selector": selector, "key": key, "value": value, "unit": unit,
                             "inputs_complete": bool(complete), "detail": detail})
    frame = pd.DataFrame(rows, columns=list(METRIC_COLUMNS))
    order = {t: k for k, t in enumerate(SCOPE_TYPES)}
    frame = frame.assign(_s=frame["scope_type"].map(order))
    frame = frame.sort_values(["metric", "_s", "scope", "selector", "unit", "key"], kind="mergesort")
    return frame.drop(columns=["_s"]).reset_index(drop=True)


# ---------------------------------------------------------------- building the inputs from a day's frames


RISK_FRAMES = ("position_risk", "sensitivities", "key_rates", "var", "stress", "stress_positions", "liquidity",
               "funding_liquidity")
ENGINE_FRAMES = ("positions", "nav", "funding", "fills", "drawdowns", "attribution")


def _frames_of(source, names: Sequence[str], what: str) -> dict[str, pd.DataFrame | None]:
    """Frames by name from an object with those attributes (``RiskResult``, ``BookResult``), a mapping, or a
    directory of ``<name>.csv`` files (missing files: None)."""
    out: dict[str, pd.DataFrame | None] = {}
    for name in names:
        value = None
        if isinstance(source, Mapping):
            value = source.get(name)
        elif isinstance(source, (str, Path)):
            path = Path(source) / f"{name}.csv"
            if path.exists():
                value = pd.read_csv(path, dtype=str, keep_default_na=False)
        elif source is not None:
            value = getattr(source, name, None)
        out[name] = value
    if isinstance(source, (str, Path)) and not Path(source).is_dir():
        raise FileNotFoundError(f"{what} directory not found: {source}")
    return out


def _numeric(frame: pd.DataFrame | None, columns: Sequence[str]) -> pd.DataFrame | None:
    if frame is None:
        return None
    out = frame.copy()
    for c in columns:
        if c in out:
            out[c] = pd.to_numeric(out[c].replace("", np.nan), errors="coerce")
    return out


def _balance_from_nav(nav_rows: Sequence[Mapping]) -> dict[str, dict[str, float]]:
    out = {}
    for rec in nav_rows:
        book = _text(rec.get("book"))
        if book == "fund":
            continue
        cash = _float(rec.get("cash"), 0.0)
        borrowed = _float(rec.get("repo_borrowed"), 0.0)
        out[book] = {"net_cash": cash - borrowed + _float(rec.get("lent"), 0.0)
                     + _float(rec.get("receivables"), 0.0), "borrowed": borrowed}
    return out


def _issue_dates(ctx: rk.RiskContext | None) -> dict[str, date]:
    out: dict[str, date] = {}
    if ctx is None:
        return out
    for table in (ctx.market.master, ctx.terms if isinstance(ctx.terms, pd.DataFrame) else None):
        if table is None or not len(table) or "issue_date" not in table or "isin" not in table:
            continue
        for rec in table.to_dict("records"):
            when = rk._opt_date(rec.get("issue_date"), "an issue_date") if not _blank(rec.get("issue_date")) else None
            if when is not None:
                out.setdefault(_text(rec.get("isin")).upper(), when)
    return out


def _notified(ctx: rk.RiskContext | None, day: date) -> dict[str, float]:
    out: dict[str, float] = {}
    table = ctx.market.auctions if ctx is not None else None
    if table is None or not len(table) or "notified_cr" not in table:
        return out
    for rec in table.to_dict("records"):
        isin = _text(rec.get("isin")).upper()
        when = rk._opt_date(rec.get("auction_date"), "an auction_date")
        if isin and when is not None and when >= day and not _blank(rec.get("notified_cr")):
            out.setdefault(isin, _float(rec.get("notified_cr")))
    return out


def _scenario_map(stress_frame: pd.DataFrame | None, rows=None) -> dict[str, tuple[str, bool]]:
    out: dict[str, tuple[str, bool]] = {}
    if rows is not None:
        for sid, srows in st.scenarios_by_id(rows).items():
            out[sid] = (srows[0].family, all(r.component in st.FUNDING_COMPONENTS for r in srows))
        return out
    if stress_frame is None or not len(stress_frame):
        return out
    for sid, group in stress_frame.groupby("scenario_id", sort=True):
        comps = set(group["component"].astype(str))
        funding_only = bool(comps <= {"total", "funding", "interaction"} and "funding" in comps)
        out[str(sid)] = (_text(group["family"].iloc[0]), funding_only)
    return out


def metric_inputs(risk, engine, ctx: rk.RiskContext, *, day=None, board_resolution_date=None,
                  risk_weights=None, changes=None) -> MetricInputs:
    """The ``MetricInputs`` of a day from the risk run (``risk.RiskResult``, a mapping of its frames or the directory
    ``cr-strat risk`` wrote) and the engine's frames (``returns.BookResult``, a mapping or a ``cr-strat returns``
    directory), as of ``ctx.as_of``; ``changes``: the factor history (``FACTOR_CHANGE_COLUMNS``; default the risk
    run's own, or its ``factor_changes.csv``)."""
    rf = _frames_of(risk, (*RISK_FRAMES, "factor_changes"), "the risk run's")
    ef = _frames_of(engine, ENGINE_FRAMES, "the engine's")
    positions = _numeric(rf["position_risk"], ("face", "yield_pct", "dirty_price", "value", "z_spread_bp",
                                               "dv01_yield", "dv01_curve", "cs01", "frb_index_dv01",
                                               "frb_spread_dv01", "lgd", "jtd", "mark_age_days"))
    if positions is None:
        positions = pd.DataFrame(columns=list(rk.POSITION_RISK_COLUMNS))
    days = sorted({rk._date(v, "the risk run's date") for v in positions["date"]}) if len(positions) else []
    d = rk.as_date(day) if day is not None else (days[-1] if days else ctx.as_of.date())
    positions = positions[[rk._date(v, "date") == d for v in positions["date"]]] if len(positions) else positions
    nav_hist = ef["nav"]
    nav_rows = []
    if nav_hist is not None and len(nav_hist):
        nav_rows = [r for r in nav_hist.to_dict("records") if rk._date(r["date"], "the NAV date") == d]
    books = sorted({_text(r.get("book")) for r in positions.to_dict("records")} |
                   {_text(r.get("book")) for r in nav_rows if _text(r.get("book")) != "fund"})
    strategies = rk._strategy_map(ctx.strategy_books, books, d)
    for rec in positions.to_dict("records"):
        if _text(rec.get("strategy")):
            strategies.setdefault(_text(rec.get("book")), _text(rec.get("strategy")))
    nav = rk._nav_map(pd.DataFrame(nav_rows) if nav_rows else None, d, books, strategies) if nav_rows else {}
    if not nav and nav_rows == [] and ctx.nav is not None:
        nav = rk._nav_map(ctx.nav, d, books, strategies)
    hist = rf["factor_changes"] if changes is None else changes
    if hist is None and hasattr(risk, "history"):
        hist = risk.history.frame()
    ratings = rk._ratings_frame(ctx.market.ratings)
    funding_liq = rf["funding_liquidity"]
    engine_positions = ef["positions"]
    if engine_positions is not None and len(engine_positions):
        engine_positions = engine_positions[[rk._date(v, "date") == d for v in engine_positions["date"]]]
    return MetricInputs(
        day=d, as_of=ctx.as_of, access=ctx.settings.access, positions=positions.reset_index(drop=True), nav=nav,
        strategies=strategies, sensitivities=_numeric(rf["sensitivities"], ("tenor", "sensitivity")),
        key_rates=_numeric(rf["key_rates"], ("tenor", "krd", "krd_years")),
        var=_numeric(rf["var"], ("horizon_days", "n_obs", "alpha_var", "var", "var_pct_nav", "alpha_es", "es",
                                 "es_pct_nav", "coverage", "uncovered_rs_per_bp")),
        stress_positions=_numeric(rf["stress_positions"], ("pnl",)), scenarios=_scenario_map(rf["stress"]),
        liquidity=_numeric(rf["liquidity"], ("face", "value", "adv", "days_to_liquidate")),
        funding_liquidity=_numeric(funding_liq, ("alm_1_7d", "alm_8_14d", "alm_15_30d")),
        nav_history=nav_hist, balance=_balance_from_nav(nav_rows), engine_positions=engine_positions,
        ratings=ratings, stock=rk._frame(ctx.market.stock, "the stock table") if ctx.market.stock is not None
        else None, issue_dates=_issue_dates(ctx), notified=_notified(ctx, d),
        risk_weights=read_risk_weights(risk_weights) if risk_weights is not None else read_risk_weights(),
        board_resolution=rk._opt_date(board_resolution_date, "board_resolution_date")
        if board_resolution_date is not None else None,
        changes=hist, risk_settings=ctx.risk)


def limit_metrics(risk, engine, ctx: rk.RiskContext, *, limits=None, day=None, board_resolution_date=None,
                  risk_weights=None) -> pd.DataFrame:
    """``METRIC_COLUMNS`` for every metric, scope and selector the ``limits`` ask for (default: the example table and
    the legal-form rows of ``ctx``'s preset), from the risk run and the engine's frames of the day (``metric_inputs``),
    as of ``ctx.as_of``: only the rows in force then for the preset are computed (another preset's rows and rows not
    yet effective are listed by ``check_limits``, never measured)."""
    table = limits if limits is not None else limit_table(example_limits(), legal_form_limits(ctx.settings.access))
    table = limits_in_force(table, as_of=ctx.as_of, legal_form=ctx.settings.access.name)
    view = metric_inputs(risk, engine, ctx, day=day, board_resolution_date=board_resolution_date,
                         risk_weights=risk_weights)
    return metrics_frame(view, table)


# ---------------------------------------------------------------- checks (L5.4)


def utilisation(value: float, hard: float, direction: str) -> float:
    """``value / hard`` for a maximum; for a minimum ``hard / value`` with a positive level and ``value / hard``
    with a negative one (a drawdown), so that 1 is the boundary either way (F22, refined: UNVERIFIED); a zero
    denominator gives 0 when the numerator is 0 and infinity otherwise."""
    if not (_finite(value) or (isinstance(value, float) and math.isinf(value))):
        return math.nan
    if direction == "max" or hard < 0:
        num, den = value, hard
    else:
        num, den = hard, value
    if den == 0:
        return 0.0 if num == 0 else math.inf
    if math.isinf(num) and math.isinf(den):
        return math.nan
    return num / den + 0.0  # never -0.0


def past(value: float, level: float, direction: str) -> bool:
    """Whether ``value`` is past ``level`` (above it for a maximum, below for a minimum); exact on floats."""
    if not (_finite(value) or (isinstance(value, float) and math.isinf(value))):
        return False
    return value > level if direction == "max" else value < level


def _scaled(level: float, rec: Mapping, scope_type: str, scope: str, scales: Mapping | None) -> float:
    if not scales or rec["metric"] not in SCALED_METRICS or rec["direction"] != "max":
        return level
    return level * float(scales.get((scope_type, scope), 1.0))


def check_limits(metrics: pd.DataFrame, limits, *, as_of, legal_form, scales: Mapping | None = None
                 ) -> pd.DataFrame:
    """``LIMIT_CHECK_COLUMNS``: one row per limit and key (L5.4). Limits in force at ``as_of`` for ``legal_form``
    (``limits_in_force``) are checked against their metric rows (``METRIC_COLUMNS``); a legal-form row of another
    preset is one ``not_applicable`` row; a borrowing limit of the fund's own policy under a preset that cannot borrow
    reads ``not_applicable`` while nothing is borrowed. ``scales`` (``{(scope_type, scope): scale}``, the drawdown
    de-risking of P6.4) multiply the levels of the DV01, KRD, CS01, VaR and ES maxima of their scope."""
    when = _when(as_of)
    table = limits if isinstance(limits, pd.DataFrame) and "origin" in limits else read_limits(limits)
    access = legal_form if isinstance(legal_form, rt.AccessPolicy) else rt.access_policy(str(legal_form))
    name, borrows = access.name, bool(access.repo or access.treps)
    live = limits_in_force(table, as_of=when, legal_form=name)
    other = table[~table["legal_form"].isin(("any", name))]
    rows = []
    base = {"date": when.date(), "as_of": rk._iso(when)}
    for rec in other.to_dict("records"):
        rows.append({**base, **{k: rec[k] for k in ("limit_id", "name", "metric", "scope_type", "scope", "selector",
                                                     "unit", "direction", "warning_level", "hard_level",
                                                     "policy_status", "legal_form", "counsel")},
                     "key": "", "value": math.nan, "utilisation": math.nan, "status": "not_applicable",
                     "detail": ("EXAMPLE; " if rec["policy_status"] == "EXAMPLE" else "")
                     + f"applies under {rec['legal_form']}, not {name}"})
    m = metrics
    for rec in live.to_dict("records"):
        hit = m[(m["metric"] == rec["metric"]) & (m["selector"] == rec["selector"]) & (m["unit"] == rec["unit"])
                & (m["scope_type"] == rec["scope_type"])]
        if rec["scope"] != "*":
            hit = hit[hit["scope"] == rec["scope"]]
        head = {**base, **{k: rec[k] for k in ("limit_id", "name", "metric", "scope_type", "selector", "unit",
                                                "direction", "policy_status", "legal_form", "counsel")}}
        if not len(hit):
            if rec["scope"] == "*":
                continue  # no scope of that type exists today
            rows.append({**head, "scope": rec["scope"], "key": "", "value": math.nan,
                         "warning_level": rec["warning_level"], "hard_level": rec["hard_level"],
                         "utilisation": math.nan, "status": "incomplete",
                         "detail": ("EXAMPLE; " if rec["policy_status"] == "EXAMPLE" else "")
                         + "no metric computed for it"})
            continue
        for mrec in hit.to_dict("records"):
            hard = _scaled(rec["hard_level"], rec, mrec["scope_type"], mrec["scope"], scales)
            warning = _scaled(rec["warning_level"], rec, mrec["scope_type"], mrec["scope"], scales)
            value = float(mrec["value"]) if not _blank(mrec["value"]) else math.nan
            detail = _text(mrec["detail"])
            complete = str(mrec["inputs_complete"]).strip().lower() in ("true", "1", "yes")
            if detail.startswith("not_applicable"):
                status = "not_applicable"
            elif rec["metric"] in BORROWING_METRICS or (rec["metric"] == "leverage" and "kind=repo" in rec["selector"]):
                status = None
                if not borrows and rec["origin"] != "legal_form" and value == 0:
                    status, detail = "not_applicable", f"not_applicable: {name} does not borrow; {detail}"
            else:
                status = None
            if status is None:
                if not complete or not (_finite(value) or math.isinf(value)):
                    status = "incomplete"
                elif past(value, hard, rec["direction"]):
                    status = "breach"
                elif past(value, warning, rec["direction"]):
                    status = "warning"
                else:
                    status = "ok"
            if rec["policy_status"] == "EXAMPLE":
                detail = f"EXAMPLE; {detail}"
            if hard != rec["hard_level"]:
                detail = f"{detail}; levels scaled by {hard / rec['hard_level'] if rec['hard_level'] else 1:g} " \
                         "(derisk)"
            rows.append({**head, "scope": mrec["scope"], "key": mrec["key"], "value": value,
                         "warning_level": warning, "hard_level": hard,
                         "utilisation": utilisation(value, hard, rec["direction"]), "status": status,
                         "detail": detail})
    frame = pd.DataFrame(rows, columns=list(LIMIT_CHECK_COLUMNS))
    order = {t: k for k, t in enumerate(SCOPE_TYPES)}
    frame = frame.assign(_s=frame["scope_type"].map(order))
    frame = frame.sort_values(["limit_id", "_s", "scope", "key"], kind="mergesort")
    return frame.drop(columns=["_s"]).reset_index(drop=True)


# ---------------------------------------------------------------- the breach log (L5.5)


def _read_log(previous) -> list[dict]:
    if previous is None:
        return []
    if isinstance(previous, (str, Path)) and not Path(previous).exists():
        return []
    frame = rk._frame(previous, "the breach log")
    if not len(frame):
        return []
    rk._check_columns(frame, ("limit_id", "scope_type", "scope", "key", "first_date", "status"), BREACH_COLUMNS,
                      "the breach log")
    out = []
    for n, rec in rk._lines(frame):
        where = f"the breach log line {n}"
        status = _text(rec.get("status"))
        if status not in BREACH_STATUSES:
            raise ValueError(f"{where}: status {status!r} is not one of {BREACH_STATUSES}")
        out.append({"limit_id": _text(rec.get("limit_id")), "scope_type": _text(rec.get("scope_type")),
                    "scope": _text(rec.get("scope")), "key": _text(rec.get("key")),
                    "first_date": rk._date(rec.get("first_date"), f"{where}: first_date"),
                    "last_date": rk._date(rec.get("last_date"), f"{where}: last_date"),
                    "business_days": int(rk._num(rec.get("business_days"), f"{where}: business_days", blank=0)),
                    "status": status, "origin": _text(rec.get("origin")) or "passive",
                    "worst_value": _float(rec.get("worst_value")), "hard_level": _float(rec.get("hard_level")),
                    "cure_due": rk._opt_date(rec.get("cure_due"), f"{where}: cure_due"),
                    "action": _text(rec.get("action")),
                    "closed_date": rk._opt_date(rec.get("closed_date"), f"{where}: closed_date"),
                    "notes": _text(rec.get("notes"))})
    return out


def _filled_scopes(fills, day: date, strategies: Mapping[str, str]) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    if fills is None:
        return out
    frame = rk._frame(fills, "the fills")
    if not len(frame) or "status" not in frame:
        return out
    for rec in frame.to_dict("records"):
        if _text(rec.get("status")) != "filled" or rk._date(rec.get("date"), "the fills' date") != day:
            continue
        book = _text(rec.get("book"))
        out |= {("fund", "fund"), ("book", book), ("strategy", strategies.get(book, book))}
    return out


def update_breach_log(previous, checks: pd.DataFrame, *, as_of, calendar=None, fills=None, limits=None,
                      strategies: Mapping[str, str] | None = None) -> pd.DataFrame:
    """Today's breach log (``BREACH_COLUMNS``) from yesterday's (``previous``: a frame, a path that may not exist, or
    None) and today's checks (L5.5): a new breach opens an episode (``new``, its cure date ``cure_business_days``
    business days on), one still in breach the next day ``continuing`` with its business days counted and its worst
    value kept, past its cure date (or at once for a cure period of 0: a legal-form incident) ``escalated``; an
    episode no longer in breach is ``cured`` and kept. ``origin`` is ``active`` when the breach first appears on a day
    an order in its scope filled (``fills``, the engine's ``FILL_COLUMNS``), else ``passive``. ``limits`` gives each
    limit's cure period and action (default: the examples', then the legal-form table's)."""
    when = _when(as_of)
    day = when.date()
    cal = (calendar if calendar is not None else mumbai_settlement_calendar()).lenient()
    table = limits if isinstance(limits, pd.DataFrame) else limit_table(example_limits(), read_limits(
        LEGAL_FORM_LIMITS_CSV, origin="legal_form")) if limits is None else read_limits(limits)
    by_id = {r["limit_id"]: r for r in table.to_dict("records")}
    log = _read_log(previous)
    active_scopes = _filled_scopes(fills, day, strategies or {})
    breaches = checks[checks["status"] == "breach"]
    today = {(r["limit_id"], r["scope_type"], r["scope"], _text(r["key"])): r for r in breaches.to_dict("records")}
    out, seen = [], set()
    for entry in log:
        key = (entry["limit_id"], entry["scope_type"], entry["scope"], entry["key"])
        if entry["status"] not in OPEN_BREACHES or entry["last_date"] > day:
            out.append(entry)  # a cured episode, or a later day's row: kept as it is
            continue
        if key in today and key not in seen:
            rec = today[key]
            seen.add(key)
            direction = _text(rec["direction"])
            value = float(rec["value"])
            worst = entry["worst_value"]
            worse = (max(worst, value) if direction == "max" else min(worst, value)) if _finite(worst) else value
            cure_days = int(by_id.get(entry["limit_id"], {}).get("cure_business_days", 0))
            if cure_days == 0:
                status = "escalated"
            elif entry["first_date"] == day:
                status = "new"  # the same day run again
            else:
                status = "escalated" if entry["cure_due"] is not None and day > entry["cure_due"] else "continuing"
            out.append({**entry, "last_date": day, "business_days": len(cal.business_days(entry["first_date"], day)),
                        "status": status, "worst_value": worse, "hard_level": float(rec["hard_level"])})
        else:
            out.append({**entry, "status": "cured", "closed_date": day})
    for key, rec in sorted(today.items()):
        if key in seen:
            continue
        limit = by_id.get(key[0], {})
        cure_days = int(limit.get("cure_business_days", 0))
        due = cal.add_business_days(day, cure_days) if cure_days else day
        scope = (key[1], key[2])
        origin = "active" if scope in active_scopes else "passive"
        notes = _text(limit.get("notes"))
        if origin == "active":
            notes = "an order in its scope filled the day it first appeared: the pre-trade gate was bypassed" + (
                f"; {notes}" if notes else "")
        out.append({"limit_id": key[0], "scope_type": key[1], "scope": key[2], "key": key[3], "first_date": day,
                    "last_date": day, "business_days": 1, "status": "escalated" if cure_days == 0 else "new",
                    "origin": origin, "worst_value": float(rec["value"]), "hard_level": float(rec["hard_level"]),
                    "cure_due": due, "action": _text(limit.get("action")) or "block_increase", "closed_date": None,
                    "notes": notes})
    frame = pd.DataFrame(out, columns=list(BREACH_COLUMNS))
    return frame.sort_values(["first_date", "limit_id", "scope_type", "scope", "key"], kind="mergesort").reset_index(
        drop=True)


def breaches_of_day(log: pd.DataFrame, day) -> pd.DataFrame:
    """The log's rows touched on ``day``: open on it, or cured on it."""
    d = rk.as_date(day)
    keep = [(r["last_date"] == d and r["status"] in OPEN_BREACHES) or (r["closed_date"] == d)
            for r in log.to_dict("records")]
    return log[keep].reset_index(drop=True)


def write_limits(metrics: pd.DataFrame, checks: pd.DataFrame, breaches: pd.DataFrame, out_dir, *,
                 breach_log: pd.DataFrame | None = None) -> list[Path]:
    """Write ``metrics.csv``, ``limit_checks.csv``, ``breaches.csv`` (the day's open and cured episodes) and
    ``breach_log.csv`` (the whole log) to ``out_dir``; the same frames give the same bytes. Returns the paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, frame in (("metrics", metrics), ("limit_checks", checks), ("breaches", breaches),
                        ("breach_log", breach_log if breach_log is not None else breaches)):
        path = out / f"{name}.csv"
        frame.to_csv(path, index=False, date_format="%Y-%m-%d")
        paths.append(path)
    return paths


# ---------------------------------------------------------------- the pre-trade gate (L5.6)


ORDER_COLUMNS = ("order_id", "book", "isin", "side", "face", "months", "venue", "limit", "purpose", "special_repo_pct",
                 "auction_date")
WARNING_ONLY = "warning only: "  # the prefix of a refused order's rows that warn without refusing it
CASH_BUFFER_PCT = 1.0  # UNVERIFIED: a purchase must leave this share of its consideration in cash (fill and costs)
_DEFAULT_SCENARIOS = None


@dataclass(eq=False)
class PortfolioState:
    """The book at a decision (L5.6 step 1): the risk layer's book as of ``as_of`` (``risk.RiskBook``: positions,
    swaps and the NAV by scope), each book's net cash and borrowing, the engine's NAV history (drawdowns, the
    stop-loss), the queued orders (counted as filled, UNVERIFIED and conservative: ``queued`` holds them as risked
    items), the factor history (``FACTOR_CHANGE_COLUMNS``, for the parametric VaR), the stress scenarios in force, the
    marks public by ``as_of`` (``date, isin, source, price, yield_pct, public_from``) and, in a back-test, the
    ``returns.Snapshot`` the instruments come from."""

    as_of: datetime
    day: date
    book: rk.RiskBook
    ctx: rk.RiskContext
    balance: dict[str, dict[str, float]]
    nav_history: pd.DataFrame | None = None
    open_orders: pd.DataFrame | None = None
    changes: pd.DataFrame | None = None
    scenarios: Sequence[Any] = ()
    marks: pd.DataFrame | None = None
    snapshot: Any = None
    engine_positions: pd.DataFrame | None = None
    funding: pd.DataFrame | None = None
    board_resolution: date | None = None
    queued: list = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def access(self) -> rt.AccessPolicy:
        return self.ctx.settings.access

    @property
    def strategies(self) -> dict[str, str]:
        return dict(self.book.strategies)

    def strategy_of(self, book: str) -> str:
        return self.book.strategies.get(book, book)


@dataclass(frozen=True, eq=False)
class OrderRisk:
    """An order as risk sees it (L5.6 steps 2-3): its price at its standard settlement and where the price came from,
    the item it adds to the book (a pending ``risk.RiskPosition`` with every sensitivity of R1, or a
    ``risk.RiskSwap``), its signed consideration, the rating notch of a credit purchase, and why it cannot be risked
    (``refusal``, else blank)."""

    order: rt.Order
    seq: int
    order_id: str
    strategy: str
    item: Any = None
    price_source: str = ""
    price: float = math.nan
    settlement: date | None = None
    consideration: float = 0.0
    notch: float = math.nan
    kind: str = ""
    refusal: str = ""
    flags: tuple[str, ...] = ()

    @property
    def isin(self) -> str:
        return self.item.isin if self.item is not None else (self.order.isin.strip().upper() or "OIS")


def _empty_book(ctx: rk.RiskContext, day: date, nav: Mapping, strategies: Mapping[str, str]) -> rk.RiskBook:
    return rk.RiskBook(date=day, as_of=ctx.as_of, positions=(), swaps=(), nav=dict(nav), ois_curve=None, notes=(),
                       curves={}, settings=ctx.risk, calendar=ctx.calendar, strategies=dict(strategies),
                       access=ctx.settings.access.name)


def _marks_frame(ctx: rk.RiskContext, day: date) -> pd.DataFrame:
    """The market data's marks dated on or before ``day`` and public by ``ctx.as_of`` (a row without a time from the
    marks' default time on its date), as ``Snapshot.marks`` shows them."""
    cols = ["date", "isin", "source", "price", "yield_pct", "public_from"]
    marks = ctx.market.marks
    if marks is None or not len(marks):
        return pd.DataFrame(columns=cols)
    frame = rk._frame(marks, "the marks")
    default = ctx.default_time
    rows = []
    for _, rec in rk._lines(frame):
        d = rk._date(rec.get("date"), "the marks' date")
        if d > day:
            continue
        public = rk.public_time(rec.get("public_from"), "the marks' public_from") if not _blank(
            rec.get("public_from")) else (datetime.combine(d, default) if default is not None else None)
        if public is None or public > ctx.as_of:
            continue
        source = _text(rec.get("source")).lower() or "traded"
        vwap, last, clean = (_float(rec.get(k)) for k in ("vwap", "last_price", "clean_price"))
        price = (vwap if _finite(vwap) else last if _finite(last) else clean) if source == "traded" else clean
        rows.append({"date": d, "isin": _text(rec.get("isin")).upper(), "source": source,
                     "price": price if _finite(price) else math.nan, "yield_pct": _float(rec.get("yield_pct")),
                     "public_from": public})
    return pd.DataFrame(rows, columns=cols)


def _scenario_rows(ctx: rk.RiskContext, scenarios):
    if scenarios is None:
        return st.read_scenarios(as_of=ctx.as_of)
    if isinstance(scenarios, (list, tuple)) and all(isinstance(r, st.ScenarioRow) for r in scenarios):
        day = ctx.as_of.date()
        return [r for r in scenarios if (r.available_from is None or r.available_from <= day)
                and (r.effective_from is None or r.effective_from <= day)]
    return st.read_scenarios(scenarios, as_of=ctx.as_of)


def with_cost_table(ctx: rk.RiskContext) -> rk.RiskContext:
    """``ctx`` with its engine settings' cost table parsed once (``costs.CostTable``: the same rows), so that the
    stress and liquidity code does not read and check the cost file for every position and scenario."""
    c = ctx.settings.costs
    if isinstance(c, cost_rules.CostTable):
        return ctx
    table = cost_rules._as_table(cost_rules.DEFAULT_COSTS_CSV if c is None else c)
    return replace(ctx, settings=replace(ctx.settings, costs=table))


def _changes(ctx: rk.RiskContext) -> pd.DataFrame | None:
    try:
        history = rk.factor_history(ctx, None)
    except ValueError:
        return None
    frame = history.frame()
    return frame if len(frame) else None


def state_from_snapshot(snapshot: rt.Snapshot, ctx: rk.RiskContext, *, scenarios=None, nav_history=None,
                        board_resolution_date=None) -> PortfolioState:
    """The state of a back-test's ``Snapshot`` at its decision (``risk.book_from_snapshot``: positions, swaps,
    instruments as known, the NAV by book), as of the snapshot's ``as_of``: each book's net cash taken as its NAV less
    its positions' values (receivables counted as cash, UNVERIFIED), the engine's queue (``Snapshot.open_orders``)
    counted as filled, the factor history and marks public then. Reads the snapshot only through its accessors."""
    c = with_cost_table(replace(ctx, as_of=snapshot.as_of))
    book = rk.book_from_snapshot(snapshot, c)
    positions = snapshot.positions
    balance = {}
    for name, nav in sorted(snapshot.nav.items()):
        mine = positions[positions["book"] == name] if len(positions) else positions
        value = _fsum(pd.to_numeric(mine["value"], errors="coerce").fillna(0.0)) if len(mine) else 0.0
        balance[name] = {"net_cash": float(nav) - value, "borrowed": 0.0}
    cash = pd.DataFrame([{"date": snapshot.day, "book": name, "nav": float(nav), "cash": balance[name]["net_cash"]}
                         for name, nav in sorted(snapshot.nav.items())], columns=["date", "book", "nav", "cash"])
    c = replace(c, nav=cash)  # the day's cash by book, which the ALM and settlement cover read (the snapshot's own)
    marks = snapshot.marks()
    marks = marks.assign(isin=marks["isin"].astype(str).str.upper()) if len(marks) else marks
    state = PortfolioState(as_of=snapshot.as_of, day=snapshot.day, book=book, ctx=c, balance=balance,
                           nav_history=nav_history, open_orders=snapshot.open_orders(), changes=_changes(c),
                           scenarios=_scenario_rows(c, scenarios), marks=marks, snapshot=snapshot,
                           engine_positions=positions,
                           board_resolution=rk._opt_date(board_resolution_date, "board_resolution_date")
                           if board_resolution_date is not None else None)
    _queue(state)
    return state


def state_from_result(engine, day, ctx: rk.RiskContext, *, orders=None, risk=None, scenarios=None,
                      board_resolution_date=None) -> PortfolioState:
    """The state of an engine run's day (``returns.BookResult``, a mapping of its frames or a ``cr-strat returns``
    directory) as of ``ctx.as_of``: the risk book of the day's positions (``risk.risk_book``, or ``risk.book`` of a
    ``RiskResult`` already run), each book's cash from the NAV row, the queued ``orders`` (``ORDER_COLUMNS``, orders
    decided but not yet booked) counted as filled."""
    d = rk.as_date(day)
    ctx = with_cost_table(ctx)
    ef = _frames_of(engine, ENGINE_FRAMES, "the engine's")
    if ctx.nav is None or ctx.funding is None:  # each book's cash and borrowing: the ALM and settlement cover read them
        ctx = replace(ctx, nav=ef["nav"] if ctx.nav is None else ctx.nav,
                      funding=ef["funding"] if ctx.funding is None else ctx.funding)
    positions = ef["positions"] if ef["positions"] is not None else pd.DataFrame(columns=list(rt.POSITION_COLUMNS))
    mine = positions[[rk._date(v, "date") == d for v in positions["date"]]] if len(positions) else positions
    nav_rows = [r for r in ef["nav"].to_dict("records") if rk._date(r["date"], "date") == d] \
        if ef["nav"] is not None and len(ef["nav"]) else []
    books = sorted({_text(r.get("book")) for r in nav_rows if _text(r.get("book")) != "fund"}
                   | {_text(b) for b in mine["book"]} if len(mine) else
                   {_text(r.get("book")) for r in nav_rows if _text(r.get("book")) != "fund"})
    strategies = rk._strategy_map(ctx.strategy_books, books, d)
    nav = rk._nav_map(pd.DataFrame(nav_rows), d, books, strategies) if nav_rows else {}
    if risk is not None and getattr(risk, "book", None) is not None:
        book = risk.book
    elif len(mine):
        book = rk.risk_book(mine, ctx, day=d)
    else:
        book = _empty_book(ctx, d, nav, strategies)
    changes = risk.history.frame() if risk is not None and getattr(risk, "history", None) is not None else \
        _changes(ctx)
    funding = ef["funding"]
    if funding is not None and len(funding):
        funding = funding[[rk._date(v, "date") == d for v in funding["date"]]]
    state = PortfolioState(as_of=ctx.as_of, day=d, book=book, ctx=ctx, balance=_balance_from_nav(nav_rows),
                           nav_history=ef["nav"], open_orders=read_orders(orders) if orders is not None else None,
                           changes=changes if changes is not None and len(changes) else None,
                           scenarios=_scenario_rows(ctx, scenarios), marks=_marks_frame(ctx, d),
                           engine_positions=mine, funding=funding,
                           board_resolution=rk._opt_date(board_resolution_date, "board_resolution_date")
                           if board_resolution_date is not None else None)
    _queue(state)
    return state


def read_orders(table) -> pd.DataFrame:
    """Orders (``ORDER_COLUMNS``, the fields of ``returns.Order``): ``book``, ``side`` (buy or sell a bond; pay or
    receive fixed for an OIS, ``isin`` blank) and ``face`` (rupees, positive) required; checked line by line."""
    frame = rk._frame(table, "the orders")
    rk._check_columns(frame, ("book", "side", "face"), ORDER_COLUMNS, "the orders")
    unknown = [c for c in frame.columns if c not in ORDER_COLUMNS]
    if unknown:
        raise ValueError(f"the orders have unknown columns {unknown}{_closest(unknown[0], ORDER_COLUMNS)}")
    rows = []
    for n, rec in rk._lines(frame):
        where = f"the orders line {n}"
        side = _text(rec.get("side")).lower()
        isin = _text(rec.get("isin")).upper()
        if isin and side not in ("buy", "sell"):
            raise ValueError(f"{where}: a bond order's side is buy or sell, not {rec.get('side')!r}")
        if not isin and side not in ("pay", "receive"):
            raise ValueError(f"{where}: an OIS order (isin blank) is pay or receive fixed, not {rec.get('side')!r}")
        face = rk._num(rec.get("face"), f"{where}: face")
        if not face > 0:
            raise ValueError(f"{where}: face must be a positive number of rupees, not {rec.get('face')!r}")
        months = rk._num(rec.get("months"), f"{where}: months", blank=math.nan)
        if not isin and not (months >= 1 and months == int(months)):
            raise ValueError(f"{where}: an OIS order needs months (a whole number of at least 1)")
        if not _text(rec.get("book")):
            raise ValueError(f"{where}: book is blank")
        rows.append({"order_id": _text(rec.get("order_id")), "book": _text(rec.get("book")), "isin": isin,
                     "side": side, "face": face, "months": int(months) if math.isfinite(months) else None,
                     "venue": _text(rec.get("venue")).lower() or "ndsom",
                     "limit": rk._num(rec.get("limit"), f"{where}: limit", blank=math.nan),
                     "purpose": _text(rec.get("purpose")).lower() or "hedge",
                     "special_repo_pct": rk._num(rec.get("special_repo_pct"), f"{where}: special_repo_pct",
                                                 blank=math.nan),
                     "auction_date": rk._opt_date(rec.get("auction_date"), f"{where}: auction_date")})
    return pd.DataFrame(rows, columns=list(ORDER_COLUMNS))


def orders_from_frame(table) -> list[rt.Order]:
    """``returns.Order`` objects from an orders table (``read_orders``), in its row order."""
    out = []
    for rec in read_orders(table).to_dict("records"):
        out.append(rt.Order(book=rec["book"], side=rec["side"], face=float(rec["face"]), isin=rec["isin"],
                            months=rec["months"], venue=rec["venue"],
                            limit=None if not _finite(rec["limit"]) else float(rec["limit"]), purpose=rec["purpose"],
                            special_repo_pct=None if not _finite(rec["special_repo_pct"])
                            else float(rec["special_repo_pct"]),
                            auction_date=rec["auction_date"], order_id=rec["order_id"]))
    return out


def _queue(state: PortfolioState) -> None:
    """The queued orders as risked items (counted as filled)."""
    table = state.open_orders
    if table is None or not len(table):
        return
    for k, rec in enumerate(table.to_dict("records"), start=1):
        months = rec.get("months")
        order = rt.Order(book=_text(rec.get("book")), side=_text(rec.get("side")), face=float(rec.get("face")),
                         isin=_text(rec.get("isin")), months=None if _blank(months) else int(float(months)),
                         venue=_text(rec.get("venue")) or "ndsom", purpose=_text(rec.get("purpose")) or "hedge",
                         order_id=_text(rec.get("order_id")) or f"Q{k:06d}",
                         auction_date=rk._opt_date(rec.get("auction_date"), "auction_date")
                         if not _blank(rec.get("auction_date")) else None)
        risked = order_risk(state, order, seq=-k)
        if risked.refusal:
            state.notes.append(f"queued order {risked.order_id} not counted: {risked.refusal}")
        else:
            state.queued.append(risked)


def _instrument(state: PortfolioState, isin: str):
    if state.snapshot is not None:
        return state.snapshot.instrument(isin)
    ctx = state.ctx
    return rt.instruments_as_known(ctx.market, ctx.settings, [isin], ctx.as_of, terms=ctx.terms)[isin]


def _price_for(inst, y: float, s: date) -> tuple[float, float]:
    """(clean, dirty) of the bond at yield ``y`` for settlement ``s``."""
    bond = inst.bond
    if isinstance(bond, DiscountInstrument):
        p = bond.price(y, s, rounded=False)
        return p, p
    return bond.clean_price(y, s), bond.dirty_price(y, s)


def _yield_of(inst, clean: float, s: date) -> float:
    bond = inst.bond
    if isinstance(bond, DiscountInstrument):
        return bond.yield_from_price(clean, s)
    return bond.yield_from_price(clean, s)


def _mark_yield(state: PortfolioState, inst) -> tuple[float, str, date] | None:
    """(yield, source, mark date) of the latest traded or valuation mark of ``inst`` public by ``as_of``, restated
    from its standard settlement (a security not yet issued: its issue date), or None."""
    marks = state.marks
    if marks is None or not len(marks):
        return None
    rows = marks[marks["isin"] == inst.isin]
    if not len(rows):
        return None
    rows = rows.assign(_o=rows["source"].map({"traded": 0, "valuation": 1}).fillna(2))
    rows = rows.sort_values(["date", "_o"], ascending=[False, True], kind="mergesort")
    cal = state.ctx.calendar.lenient()
    for rec in rows.to_dict("records"):
        if rec["source"] not in ("traded", "valuation"):
            continue
        d = rec["date"] if isinstance(rec["date"], date) else rk._date(rec["date"], "the marks' date")
        s_m = cal.settlement_date(d, inst.lag)
        if inst.issue_date is not None and s_m < inst.issue_date:
            s_m = inst.issue_date
        try:
            if _finite(rec.get("price")):
                return _yield_of(inst, float(rec["price"]), s_m), rec["source"], d
            if _finite(rec.get("yield_pct")):
                return float(rec["yield_pct"]), rec["source"], d
        except ValueError:
            continue
    return None


def _model_yield(state: PortfolioState, inst, s: date) -> tuple[float, str] | None:
    """The model yield of ``inst`` for settlement ``s`` (its reference curve of the day public by ``as_of``, plus its
    last residual public then, from the residual history), or None."""
    ctx = state.ctx
    name = rk._reference_name(inst.kind, ctx)
    if name is None or ctx.market.residual_history is None:
        return None
    table = ctx.market.ois_curves if name == "ois" else (ctx.market.curves or {}).get(name)
    curve, _, _ = rk._curve_of_day(table, state.day, ctx.as_of, ctx.default_time)
    if curve is None or rk.as_date(curve.valuation_date) > s:
        return None
    frame = rk._frame(ctx.market.residual_history, "the residual history")
    if not {"date", "isin", "resid_yield_bp"} <= set(frame.columns):
        return None
    best = None
    cal = ctx.calendar.lenient()
    for rec in frame.to_dict("records"):
        if _text(rec.get("isin")).upper() != inst.isin or _blank(rec.get("resid_yield_bp")):
            continue
        settle = rk._date(rec.get("date"), "the residual history's date")
        public = rk.public_time(rec.get("public_from"), "public_from") if not _blank(rec.get("public_from")) else (
            datetime.combine(cal.add_business_days(settle, -1), ctx.default_time) if ctx.default_time else None)
        if public is None or public > ctx.as_of or settle > s:
            continue
        if best is None or settle > best[0]:
            best = (settle, float(rec["resid_yield_bp"]) / 100)
    if best is None:
        return None
    try:
        m = rt.model_yield(rt.seen_from(curve, s), inst.bond, s)
    except ValueError:
        return None
    return m + best[1], f"model (curve {name} plus the residual of {best[0]})"


def _liquidity_of(ctx: rk.RiskContext, inst, day: date) -> str:
    table = ctx.market.liquidity
    best = None
    if table is not None and len(table):
        frame = rk._frame(table, "the liquidity classes")
        for rec in frame.to_dict("records"):
            if _text(rec.get("isin")).upper() != inst.isin:
                continue
            when = rk._date(rec.get("effective_date"), "the liquidity classes' effective_date")
            if when <= day and (best is None or when > best[0]):
                best = (when, _text(rec.get("liquidity")).lower().replace("-", "_"))
    return best[1] if best is not None else (inst.liquidity or "")


def _assembled(state: PortfolioState, bond_rows: list, swap_rows: list, insts: Mapping, terms: Mapping):
    return rk._assemble(state.day, state.ctx, bond_rows, swap_rows, insts, terms, state.book.strategies,
                        state.book.nav)


def order_risk(state: PortfolioState, order: rt.Order, *, seq: int = 0) -> OrderRisk:
    """The order as risk sees it at the decision (L5.6 steps 2-3): a bond order priced at its standard settlement
    (the business day after the decision day, then the bond's settlement lag; an auction order the auction's
    settlement) at the yield of its latest traded or valuation mark public by ``as_of``, else at the model yield (the
    day's reference curve plus its last public residual, flagged), else refused (no public price); an OIS at the par
    rate of the day's OIS curve public by ``as_of``. The order becomes a pending position (or a swap) of its book
    with every sensitivity of the risk layer (``risk._assemble``, the code the end-of-day run uses)."""
    if not isinstance(order, rt.Order):
        raise TypeError(f"an order must be a returns.Order, not {type(order).__name__}")
    ctx = state.ctx
    oid = order.order_id or ""
    strategy = state.strategy_of(order.book)
    side = order.side.strip().lower()
    base = {"order": order, "seq": seq, "order_id": oid, "strategy": strategy}
    if not (isinstance(order.face, (int, float)) and math.isfinite(order.face) and order.face > 0):
        return OrderRisk(**base, refusal=f"face must be a positive number of rupees, not {order.face!r}")
    cal = ctx.calendar.lenient()
    fill_day = cal.add_business_days(state.day, 1)
    if not order.isin:
        return _swap_order(state, order, base, side, fill_day)
    isin = order.isin.strip().upper()
    if side not in ("buy", "sell"):
        return OrderRisk(**base, refusal=f"a bond order's side is buy or sell, not {order.side!r}")
    try:
        inst = _instrument(state, isin)
    except (ValueError, TypeError, KeyError) as exc:
        return OrderRisk(**base, refusal=f"{isin} cannot be risked as known at {ctx.as_of:%Y-%m-%d %H:%M}: {exc}")
    sign = 1 if side == "buy" else -1
    if order.venue in ("auction", "auction_nc"):
        settle = _auction_settlement(ctx, isin, order.auction_date)
        if settle is None:
            return OrderRisk(**base, kind=inst.kind, refusal=f"no auction of {isin} on {order.auction_date} in the "
                                                             "market data's auction table")
    else:
        settle = cal.settlement_date(fill_day, inst.lag)
        if inst.issue_date is not None and settle < inst.issue_date:
            settle = inst.issue_date
    if settle >= inst.maturity:
        return OrderRisk(**base, kind=inst.kind, refusal=f"{isin} matures on {inst.maturity}: nothing settles on "
                                                         f"{settle}")
    flags: list[str] = []
    found = _mark_yield(state, inst)
    if found is not None:
        y, source, mark_day = found
        age = max(len(cal.business_days(mark_day, state.day)) - 1, 0)
        if age:
            flags.append(f"mark_age_{age}d")
    else:
        model = _model_yield(state, inst, settle)
        if model is None:
            return OrderRisk(**base, kind=inst.kind, refusal=f"no public price for {isin} by "
                             f"{ctx.as_of:%Y-%m-%d %H:%M} (no traded or valuation mark, and no model price: curve and "
                             "residual)")
        y, source = model
        age = 0
        flags.append("model_price")
    try:
        clean, dirty = _price_for(inst, y, settle)
    except ValueError as exc:
        return OrderRisk(**base, kind=inst.kind, refusal=f"{isin} cannot be priced for {settle}: {exc}")
    face = sign * float(order.face)
    units = face / 100
    dv01 = units * (index_dv01(inst.bond, settle, 0.0, index_pct=y) if isinstance(inst.bond, GoiFloatingRateBond)
                    else inst.bond.dv01(y, settle))
    rec = {"date": state.day, "book": order.book, "isin": isin, "kind": inst.kind, "state": "pending", "face": face,
           "settlement_date": settle, "clean_mark": clean, "mark_source": source.split(" ")[0], "mark_age_days": age,
           "yield_pct": y, "accrued": 0.0, "value": 0.0, "dv01": dv01, "bucket": "",
           "liquidity": _liquidity_of(ctx, inst, state.day), "haircut_pct": "", "funded": "", "stale_warning": False,
           "extrapolated": False, "calendar_projected": False, "notes": f"order {oid or seq}"}
    try:
        item = _assembled(state, [rec], [], {isin: inst}, {}).positions[0]
    except ValueError as exc:
        return OrderRisk(**base, kind=inst.kind, refusal=f"{isin} cannot be risked: {exc}")
    notch = math.nan
    if inst.kind in rk.CREDIT_KINDS:
        notch, _ = _notch_of(_rating_view(state), {"isin": isin, "issuer_id": item.issuer_id,
                                                   "rating_bucket": item.rating_bucket or ""})
    return OrderRisk(**base, item=item, price_source=source, price=clean, settlement=settle,
                     consideration=units * dirty, notch=notch, kind=inst.kind, flags=tuple(flags))


def _rating_view(state: PortfolioState) -> MetricInputs:
    return MetricInputs(day=state.day, as_of=state.as_of, access=state.access, positions=pd.DataFrame(),
                        nav={}, strategies={}, ratings=rk._ratings_frame(state.ctx.market.ratings))


def _auction_settlement(ctx: rk.RiskContext, isin: str, day) -> date | None:
    table = ctx.market.auctions
    if table is None or not len(table) or day is None:
        return None
    for rec in table.to_dict("records"):
        if _text(rec.get("isin")).upper() == isin and rk._opt_date(rec.get("auction_date"), "auction_date") == \
                rk.as_date(day):
            return rk._opt_date(rec.get("settlement_date"), "settlement_date")
    return None


def _swap_order(state: PortfolioState, order: rt.Order, base: dict, side: str, fill_day: date) -> OrderRisk:
    ctx = state.ctx
    if side not in ("pay", "receive"):
        return OrderRisk(**base, kind="ois", refusal=f"an OIS order's side is pay or receive fixed, not "
                                                     f"{order.side!r}")
    if not order.months or int(order.months) < 1:
        return OrderRisk(**base, kind="ois", refusal="an OIS order needs months (its tenor)")
    curve, day, _ = rk._curve_of_day(ctx.market.ois_curves, state.day, ctx.as_of, ctx.default_time)
    if curve is None:
        return OrderRisk(**base, kind="ois", refusal=f"no OIS curve of {state.day} or before public by "
                                                     f"{ctx.as_of:%Y-%m-%d %H:%M} to price the swap")
    if day is not None and day < state.day:
        curve = rt.rolled(curve, state.day)
    cal = ctx.calendar
    effective = effective_date(fill_day, MIBOR_OIS, cal.lenient())
    try:
        rate = ois_par_rate(curve, effective, int(order.months), MIBOR_OIS, cal.lenient())
    except ValueError as exc:
        return OrderRisk(**base, kind="ois", refusal=f"the OIS curve cannot price a {order.months}-month swap: {exc}")
    pay = side == "pay"
    tid = base["order_id"] or f"G{abs(base['seq']):06d}"
    swap = OisSwap(effective=effective, months=int(order.months), fixed_rate_pct=rate, notional=float(order.face),
                   pay_fixed=pay)
    try:
        val = ois_npv(rk._plain(curve), swap, fixings=rk._fixings_as_of(ctx), calendar=cal)
    except ValueError as exc:
        return OrderRisk(**base, kind="ois", refusal=f"the swap cannot be valued on the run's calendar: {exc} (the "
                                                     "engine's project_calendar extends it past its holiday list)")
    rec = {"date": state.day, "book": order.book, "isin": f"OIS:{tid}", "kind": "ois", "state": "ois",
           "face": -float(order.face) if pay else float(order.face), "settlement_date": effective,
           "clean_mark": rate, "mark_source": "par_rate", "mark_age_days": 0, "yield_pct": rate, "accrued": 0.0,
           "value": val.npv, "dv01": val.dv01}
    terms = {tid: {"trade_id": tid, "months": int(order.months), "pay_fixed": pay,
                   "purpose": (order.purpose or "hedge").lower()}}
    try:
        item = _assembled(state, [], [rec], {}, terms).swaps[0]
    except ValueError as exc:
        return OrderRisk(**base, kind="ois", refusal=f"the swap cannot be risked: {exc}")
    return OrderRisk(**base, item=item, price_source="par_rate", price=rate, settlement=effective,
                     consideration=0.0, kind="ois", flags=("priced_at_the_decision_day_par_rate",))


def _book_with(book: rk.RiskBook, items: Sequence[OrderRisk]) -> rk.RiskBook:
    positions = [*book.positions, *(o.item for o in items if isinstance(o.item, rk.RiskPosition))]
    swaps = [*book.swaps, *(o.item for o in items if isinstance(o.item, rk.RiskSwap))]
    positions.sort(key=lambda p: (p.book, p.isin, p.state, p.settlement))
    swaps.sort(key=lambda s: (s.book, s.isin))
    curves = dict(book.curves)
    for o in items:
        if isinstance(o.item, rk.RiskPosition) and o.item.reference and o.item.reference not in curves:
            curves[o.item.reference] = o.item.curve
    return replace(book, positions=tuple(positions), swaps=tuple(swaps), curves=curves)


def _stress_rows(state: PortfolioState, book: rk.RiskBook) -> pd.DataFrame:
    sctx = st.StressContext(risk=state.ctx)
    rows = []
    for _, srows in st.scenarios_by_id(state.scenarios).items():
        rows.extend(st.apply_scenario(book, srows, sctx))
    return pd.DataFrame(rows, columns=list(st.STRESS_POSITION_COLUMNS))


def _view(state: PortfolioState, items: Sequence[OrderRisk], cache: dict) -> MetricInputs:
    """The ``MetricInputs`` of the state with ``items`` (queued and approved orders, and the one being checked)
    added: every table recomputed on the book with them, the stress P&L of the state revalued in full once and each
    order's added in full, VaR and ES by the parametric method (the gate's approximation)."""
    book = _book_with(state.book, items)
    if "stress" not in cache:
        cache["stress"] = _stress_rows(state, state.book)
    extra = [o for o in items if o.item is not None]
    stress_parts = [cache["stress"]]
    for o in extra:
        key = ("stress", id(o))
        if key not in cache:
            mini = rk.RiskBook(date=state.day, as_of=state.as_of,
                               positions=(o.item,) if isinstance(o.item, rk.RiskPosition) else (),
                               swaps=(o.item,) if isinstance(o.item, rk.RiskSwap) else (), nav=state.book.nav,
                               ois_curve=state.book.ois_curve, notes=(), curves=book.curves,
                               settings=state.ctx.risk, calendar=state.book.calendar,
                               strategies=state.book.strategies, access=state.book.access)
            cache[key] = _stress_rows(state, mini)
        stress_parts.append(cache[key])
    stress_frame = pd.concat([p for p in stress_parts if len(p)], ignore_index=True) if any(
        len(p) for p in stress_parts) else stress_parts[0]
    liquidity, _ = st.liquidity(book, state.ctx)
    funding_liq = st.funding_liquidity(state.funding, None, book, state.ctx, state.scenarios) \
        if state.access.repo or state.access.treps else None
    return MetricInputs(
        day=state.day, as_of=state.as_of, access=state.access, positions=rk.position_risk(book),
        nav=dict(book.nav), strategies=dict(book.strategies), sensitivities=rk.sensitivities(book),
        key_rates=rk.key_rate_table(book), var=None, stress_positions=stress_frame,
        scenarios=_scenario_map(None, state.scenarios), liquidity=liquidity, funding_liquidity=funding_liq,
        nav_history=state.nav_history, balance=state.balance, engine_positions=state.engine_positions,
        ratings=rk._ratings_frame(state.ctx.market.ratings),
        stock=rk._frame(state.ctx.market.stock, "the stock table") if state.ctx.market.stock is not None else None,
        issue_dates=_issue_dates(state.ctx), notified=_notified(state.ctx, state.day),
        risk_weights=read_risk_weights(), board_resolution=state.board_resolution, changes=state.changes,
        risk_settings=state.ctx.risk, var_method="parametric")


def _cached_values(view: MetricInputs, cache: dict, metric: str, st: str, sc: str, selector: str, unit: str
                   ) -> dict[str, tuple[float, bool, str]]:
    key = (id(view), metric, st, sc, selector, unit)
    if key not in cache:
        cache[key] = {k: (v, ok, d) for k, v, ok, d in metric_values(view, metric, st, sc, selector, unit)}
    return cache[key]


def _gate_row(state: PortfolioState, risked: OrderRisk, decision: str, **extra) -> dict:
    o = risked.order
    return {"decision_date": state.day, "as_of": rk._iso(state.as_of), "seq": risked.seq, "order_id": risked.order_id,
            "book": o.book, "strategy": risked.strategy, "isin": risked.isin, "side": o.side.strip().lower(),
            "face": float(o.face) if isinstance(o.face, (int, float)) else math.nan,
            "months": o.months if o.months is not None else math.nan, "venue": o.venue,
            "price_source": risked.price_source, "decision": decision, "limit_id": "", "metric": "", "scope": "",
            "pre_value": math.nan, "post_value": math.nan, "warning_level": math.nan, "hard_level": math.nan,
            "reason": "", **extra}


class PreTradeGate:
    """The pre-trade gate (L5.6): ``check(state, orders)`` decides each order in the strategy's order, against the
    state plus the orders already approved in the same decision, and returns ``(approved orders, GATE_COLUMNS
    rows)``: the approved orders are the same objects in the same order.

    First the legal form's own rules (the access preset; they refuse whatever the limits say): a sale that would leave
    a book short under a preset without short sales (Short Sale Directions 2018 para 3), or of a kind it may not short;
    an OIS under a preset with none, or a ``view`` under ``hedge_only`` (Rupee IRD Directions 2025 para 5.4(i)); a
    purchase that would need borrowing under a preset that cannot borrow (no repo or TREPS; RBI counts any outside
    liability as public funds), keeping ``cash_buffer_pct`` of its consideration for the fill and costs (UNVERIFIED,
    1%); a when-issued position past the preset's share of the notified amount (When Issued Directions 2018 para 6).
    Then every limit in force at ``as_of`` for the preset (the fund's table and the legal-form rows of its preset,
    added always): an order is ``refused`` when, for a hard level of an enforced limit (``APPROVED``; ``EXAMPLE``
    only with ``allow_example_limits``, else the gate refuses to run on them) and any key, its post-trade value is past
    the level and worse than before it (a deeper or a new breach; an order leaving a passive breach unchanged or
    smaller passes, and keys it does not change never block it); ``approved_with_warning`` when only a warning level
    fails, a ``DRAFT`` limit fails, or a metric is incomplete (UNVERIFIED: missing data warns, it never blocks); a
    rating floor ``of=purchases`` reads each credit purchase's own rating; a ``halt`` limit in breach refuses every
    order in its scope; ``report`` limits have no gate effect. ``scales`` (``{(scope_type, scope): scale}``, the
    drawdown de-risking of P6.4) multiply the levels of DV01, KRD, CS01, VaR and ES maxima. VaR and ES are parametric
    on the post-trade sensitivities (EWMA, as of ``as_of``); stresses revalue the state in full once and each order in
    full (more exact than the second-order approximation the specification allows)."""

    def __init__(self, limits, ctx: rk.RiskContext, *, allow_example_limits: bool = False, scales=None,
                 cash_buffer_pct: float = CASH_BUFFER_PCT, legal_form_rows: bool = True):
        table = limit_table(limits) if limits is not None else pd.DataFrame(columns=[*LIMIT_COLUMNS, "origin",
                                                                                       "line"])
        if legal_form_rows:
            extra = legal_form_limits(ctx.settings.access)
            mine = table[table["origin"] != "legal_form"] if len(table) else table
            clash = sorted(set(mine["limit_id"]) & set(read_limits(LEGAL_FORM_LIMITS_CSV)["limit_id"]))
            if clash:
                raise ValueError(f"limit_id {clash[0]} is in the fund's limit table and in the legal-form table "
                                 "(legal_form_limits.csv), which the gate always adds: give the fund's row another id")
            extra = extra[~extra["limit_id"].isin(set(table["limit_id"]))]  # rows already given with the table
            table = pd.concat([table, extra], ignore_index=True) if len(table) else extra.reset_index(drop=True)
        examples = table[table["policy_status"] == "EXAMPLE"]
        if len(examples) and not allow_example_limits:
            raise ValueError(f"the pre-trade gate refuses to run on EXAMPLE limits ({', '.join(examples['limit_id'])}"
                             "): they are not the fund's policy. Give the fund's approved table, or pass "
                             "allow_example_limits=True to test with them")
        if not (_finite(cash_buffer_pct) and cash_buffer_pct >= 0):
            raise ValueError(f"cash_buffer_pct must be zero or more, not {cash_buffer_pct}")
        self.limits = table
        self.ctx = ctx
        self.allow_example_limits = allow_example_limits
        self.scales = dict(scales or {})
        self.cash_buffer_pct = float(cash_buffer_pct)

    def _enforced(self, rec: Mapping) -> bool:
        if rec["policy_status"] == "APPROVED":
            return True
        return rec["policy_status"] == "EXAMPLE" and self.allow_example_limits

    @staticmethod
    def _preset_first(state: PortfolioState, o: rt.Order) -> dict | None:
        """The preset's OIS rules, checked before an OIS order is priced: none allowed, or a view under
        ``hedge_only``."""
        access = state.access
        if o.isin:
            return None
        if access.ois == "none":
            return {"limit_id": "preset:ois", "reason": f"the access preset {access.name!r} allows no OIS"}
        if access.ois == "hedge_only" and (o.purpose or "hedge").strip().lower() != "hedge":
            return {"limit_id": "preset:ois_purpose",
                    "reason": f"the access preset {access.name!r} allows OIS for hedging only (Rupee IRD Directions "
                              f"2025 para 5.4(i)); this order says purpose {o.purpose!r}"}
        return None

    def _legal(self, state: PortfolioState, pre: MetricInputs, post: MetricInputs, risked: OrderRisk) -> list[dict]:
        """The access preset's own rules (L5.6 step 6) as refusal reasons."""
        access = state.access
        o = risked.order
        out = []
        if risked.kind == "ois":
            early = self._preset_first(state, o)
            return [early] if early else []
        item = risked.item
        if o.side.strip().lower() == "sell" and not _when_issued_item(post, item):
            net = _fsum(r["face"] for r in post.rows() if r["book"] == o.book and _text(r.get("isin")) == item.isin
                        and not r["is_swap"])
            if net < -1e-6:
                if not access.short_sale:
                    out.append({"limit_id": "preset:short_sale",
                                "reason": f"selling {o.face:,.0f} of {item.isin} would leave book {o.book} short "
                                          f"{-net:,.0f}; the access preset {access.name!r} allows no short sales "
                                          "(Short Sale Directions 2018 para 3)"})
                elif item.kind not in access.short_kinds:
                    out.append({"limit_id": "preset:short_kind",
                                "reason": f"{item.isin} is a {item.kind}; short sales are allowed only in "
                                          f"{access.short_kinds} (Short Sale Directions 2018 para 1(b))"})
        if not (access.repo or access.treps) and o.side.strip().lower() == "buy":
            buffer = abs(risked.consideration) * self.cash_buffer_pct / 100
            net, _, buys, sells = _book_cash(post, o.book)
            need = buys + buffer - sells - (net if _finite(net) else 0.0)
            pre_net, _, pre_buys, pre_sells = _book_cash(pre, o.book)
            pre_need = pre_buys - pre_sells - (pre_net if _finite(pre_net) else 0.0)
            if need > 1e-6 and need > pre_need:
                out.append({"limit_id": "preset:no_borrowing",
                            "reason": f"the purchase would need Rs {need:,.2f} of borrowing in book {o.book} (net cash "
                                      f"Rs {net:,.2f}, purchases to settle Rs {buys:,.2f} with a "
                                      f"{self.cash_buffer_pct:g}% buffer on this one, sales Rs {sells:,.2f}); the "
                                      f"access preset {access.name!r} cannot borrow (no repo or TREPS: Repo "
                                      "Directions 2025 para 4(1); RBI counts any outside liability as public funds)"})
        if _when_issued_item(post, item):
            notified = post.notified.get(item.isin)
            if notified is None:
                out.append({"limit_id": "preset:when_issued",
                            "reason": f"a when-issued order in {item.isin} needs the auction's notified amount (the "
                                      "market data's auction table) to check the When Issued Directions' limits"})
            else:
                net = _fsum(r["face"] for r in post.rows() if _text(r.get("isin")) == item.isin and
                            _when_issued(post, r))
                cap = (access.when_issued_long_pct if net > 0 else access.when_issued_short_pct) / 100 * notified * 1e7
                if abs(net) > cap + 1e-6:
                    out.append({"limit_id": "preset:when_issued",
                                "reason": f"a when-issued position of Rs {net / 1e7:,.2f} crore in {item.isin} exceeds "
                                          f"Rs {cap / 1e7:,.2f} crore (When Issued Directions 2018 para 6)"})
        return out

    def check(self, state: PortfolioState, orders: Sequence[rt.Order], *, scales=None
              ) -> tuple[list[rt.Order], list[dict]]:
        """``(approved orders, GATE_COLUMNS rows)`` of a decision (see the class)."""
        scale_of = {**self.scales, **(scales or {})}
        live = limits_in_force(self.limits, as_of=state.as_of, legal_form=state.access.name)
        live = live[live["action"] != "report"]
        live = live.sort_values("limit_id", kind="mergesort").to_dict("records")  # ids are unique: any row order
        cache: dict = {}
        approved: list[rt.Order] = []
        accepted: list[OrderRisk] = list(state.queued)
        rows: list[dict] = []
        pre = _view(state, accepted, cache)
        for seq, order in enumerate(orders, start=1):
            if not isinstance(order, rt.Order):
                raise TypeError(f"a strategy must return returns.Order objects, not {type(order).__name__}")
            early = self._preset_first(state, order)
            if early:
                risked = OrderRisk(order=order, seq=seq, order_id=order.order_id or "",
                                   strategy=state.strategy_of(order.book), kind="ois")
                rows.append(_gate_row(state, risked, "refused", limit_id=early["limit_id"], reason=early["reason"]))
                continue
            risked = order_risk(state, order, seq=seq)
            if risked.refusal:
                rows.append(_gate_row(state, risked, "refused", limit_id="order", reason=risked.refusal))
                continue
            post = _view(state, [*accepted, risked], cache)
            failures = [{**f, "hard": True} for f in self._legal(state, pre, post, risked)]
            if not failures:
                failures = self._limit_failures(state, live, pre, post, risked, scale_of, cache)
            hard = [f for f in failures if f["hard"]]
            decision = "refused" if hard else ("approved_with_warning" if failures else "approved")
            if not failures:
                rows.append(_gate_row(state, risked, decision))
            for f in failures:
                why = f["reason"] if f["hard"] or not hard else f"{WARNING_ONLY}{f['reason']}"
                rows.append(_gate_row(state, risked, decision, limit_id=f.get("limit_id", ""),
                                      metric=f.get("metric", ""), scope=f.get("scope", ""),
                                      pre_value=f.get("pre", math.nan), post_value=f.get("post", math.nan),
                                      warning_level=f.get("warning", math.nan), hard_level=f.get("hard_level",
                                                                                                 math.nan),
                                      reason=why))
            if not hard:
                approved.append(order)
                accepted.append(risked)
                pre = post
        return approved, rows

    def _limit_failures(self, state: PortfolioState, live: list[dict], pre: MetricInputs, post: MetricInputs,
                        risked: OrderRisk, scales: Mapping, cache: dict) -> list[dict]:
        o = risked.order
        touched = {("fund", "fund"), ("book", o.book), ("strategy", risked.strategy)}
        out = []
        for rec in live:
            scopes = [s for s in touched if s[0] == rec["scope_type"] and (rec["scope"] in ("*", s[1]))]
            if not scopes:
                continue
            enforced = self._enforced(rec)
            tag = "" if enforced else f"{rec['policy_status']} (not enforced): "
            for stype, sc in sorted(scopes):
                hard = _scaled(rec["hard_level"], rec, stype, sc, scales)
                warning = _scaled(rec["warning_level"], rec, stype, sc, scales)
                label = f"limit {rec['limit_id']} {rec['metric']} ({stype} {sc})" if stype != "fund" else \
                    f"limit {rec['limit_id']} {rec['metric']} (fund)"
                common = {"limit_id": rec["limit_id"], "metric": rec["metric"], "scope": f"{stype} {sc}",
                          "warning": warning, "hard_level": hard}
                if rec["action"] == "halt":
                    now = _cached_values(pre, cache, rec["metric"], stype, sc, rec["selector"], rec["unit"])
                    if any(ok and past(v, hard, rec["direction"]) for v, ok, _ in now.values()):
                        out.append({**common, "hard": enforced, "reason": f"{tag}{label} is in breach and halts "
                                                                          "every new order in its scope"})
                        continue
                if "of=purchases" in rec["selector"] and rec["metric"] == "rating_floor":
                    if o.side.strip().lower() != "buy" or risked.kind not in rk.CREDIT_KINDS:
                        continue
                    notch = risked.notch
                    if not _finite(notch):
                        out.append({**common, "hard": False, "post": notch,
                                    "reason": f"{tag}{label}: {risked.isin} has no rating as known at "
                                              f"{state.as_of:%Y-%m-%d %H:%M}: the floor cannot be checked "
                                              "(incomplete: a warning)"})
                    elif past(notch, hard, rec["direction"]):
                        out.append({**common, "hard": enforced, "post": notch,
                                    "reason": f"{tag}{label}: the purchase is rated {_fmt(notch, 'notch')}, past the "
                                              f"hard floor {_fmt(hard, 'notch')}"})
                    elif past(notch, warning, rec["direction"]):
                        out.append({**common, "hard": False, "post": notch,
                                    "reason": f"{tag}{label}: the purchase is rated {_fmt(notch, 'notch')}, past the "
                                              f"warning floor {_fmt(warning, 'notch')}"})
                    continue
                before = _cached_values(pre, cache, rec["metric"], stype, sc, rec["selector"], rec["unit"])
                after = _cached_values(post, cache, rec["metric"], stype, sc, rec["selector"], rec["unit"])
                for key, (value, ok, detail) in sorted(after.items()):
                    if detail.startswith("not_applicable"):
                        continue
                    old = before.get(key)
                    where = label + (f" [{key}]" if key else "")
                    item = {**common, "pre": old[0] if old else math.nan, "post": value,
                            "scope": f"{stype} {sc}" + (f" [{key}]" if key else "")}
                    if not ok:
                        if old is not None and not old[1] and _same(old[0], value):
                            continue  # incomplete before and unchanged: reported by the daily checks
                        out.append({**item, "hard": False, "reason": f"{tag}{where}: incomplete ({detail}): a "
                                                                     "warning, never a block"})
                        continue
                    if old is not None and old[1] and _same(old[0], value):
                        continue  # the order does not change this key
                    worse = old is None or not old[1] or (value > old[0] if rec["direction"] == "max"
                                                          else value < old[0])
                    if not worse:
                        continue
                    pre_text = f" (pre-trade {_fmt(old[0], rec['unit'])})" if old is not None and old[1] else \
                        " (none before)"
                    side = "above" if rec["direction"] == "max" else "below"
                    if past(value, hard, rec["direction"]):
                        out.append({**item, "hard": enforced,
                                    "reason": f"{tag}{where}: post-trade {_fmt(value, rec['unit'])} {side} the hard "
                                              f"{_fmt(hard, rec['unit'])}{pre_text}"})
                    elif past(value, warning, rec["direction"]):
                        out.append({**item, "hard": False,
                                    "reason": f"{tag}{where}: post-trade {_fmt(value, rec['unit'])} {side} the "
                                              f"warning {_fmt(warning, rec['unit'])}{pre_text}"})
        return out


def _same(a: float, b: float) -> bool:
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    return a == b


def _when_issued_item(view: MetricInputs, item) -> bool:
    if item is None or isinstance(item, rk.RiskSwap):
        return False
    issue = view.issue_dates.get(item.isin)
    return issue is not None and issue > view.day


def gate_frame(rows: Sequence[Mapping]) -> pd.DataFrame:
    """The gate's rows as a ``GATE_COLUMNS`` frame, in decision order."""
    return pd.DataFrame(list(rows), columns=list(GATE_COLUMNS))


class GatedStrategy:
    """A ``returns.Strategy`` whose orders pass a ``PreTradeGate`` before the engine sees them (D7.3): ``decide``
    calls the inner strategy, gives its orders to the gate on the snapshot's state (``state_from_snapshot``) and
    returns the approved ones, the same objects in the same order, so a gate that approves everything leaves every
    engine output byte-identical. ``log`` holds every decision (``GATE_COLUMNS`` rows keyed by ``decision_date`` and
    ``seq``, the order's place in the strategy's list that day; ``order_id`` is the strategy's own when it set one),
    ``records`` the rows of an optional ``recorder(snapshot)`` (the day's risk, say). It reads the snapshot only
    through its public accessors and keeps its own NAV history for the drawdown and stop-loss limits (a back-test's
    only flow is its starting capital, UNVERIFIED reading: the first NAV seen is taken as capital). A state that
    cannot be built refuses every order of the day, saying why; the run goes on."""

    def __init__(self, inner, gate: PreTradeGate, *, recorder: Callable | None = None, board_resolution_date=None,
                 scales_fn: Callable | None = None):
        if not hasattr(inner, "decide"):
            raise TypeError("inner must be a returns.Strategy (an object with decide(snapshot))")
        self.inner = inner
        self.gate = gate
        self.recorder = recorder
        self.board_resolution_date = board_resolution_date
        self.scales_fn = scales_fn
        self.log: list[dict] = []
        self.records: list = []
        self._navs: list[dict] = []
        self._last: dict[str, float] = {}

    def _record_nav(self, snapshot) -> None:
        for name, nav in sorted(snapshot.nav.items()):
            prev = self._last.get(name)
            flow = float(nav) if prev is None else 0.0
            pnl = 0.0 if prev is None else float(nav) - prev
            self._navs.append({"date": snapshot.day, "book": name, "nav": float(nav), "flow": flow, "pnl": pnl})
            self._last[name] = float(nav)

    def decide(self, snapshot) -> list:
        orders = list(self.inner.decide(snapshot) or [])
        if self.recorder is not None:
            self.records.extend(self.recorder(snapshot) or [])
        self._record_nav(snapshot)
        if not orders:
            return []
        nav_history = pd.DataFrame(self._navs, columns=["date", "book", "nav", "flow", "pnl"])
        try:
            state = state_from_snapshot(snapshot, self.gate.ctx, nav_history=nav_history,
                                        board_resolution_date=self.board_resolution_date)
        except (ValueError, TypeError, KeyError) as exc:
            reason = f"the book cannot be risked at {snapshot.as_of:%Y-%m-%d %H:%M}: {exc}"
            for seq, order in enumerate(orders, start=1):
                strategy = order.book
                self.log.append({"decision_date": snapshot.day, "as_of": rk._iso(snapshot.as_of), "seq": seq,
                                 "order_id": order.order_id, "book": order.book, "strategy": strategy,
                                 "isin": order.isin or "OIS", "side": order.side, "face": float(order.face),
                                 "months": order.months if order.months is not None else math.nan,
                                 "venue": order.venue, "price_source": "", "decision": "refused", "limit_id": "state",
                                 "metric": "", "scope": "", "pre_value": math.nan, "post_value": math.nan,
                                 "warning_level": math.nan, "hard_level": math.nan, "reason": reason})
            return []
        scales = self.scales_fn(snapshot) if self.scales_fn is not None else None
        approved, rows = self.gate.check(state, orders, scales=scales)
        self.log.extend(rows)
        return approved

    def log_frame(self) -> pd.DataFrame:
        """Every decision so far (``GATE_COLUMNS``)."""
        return gate_frame(self.log)


def risk_recorder(ctx: rk.RiskContext) -> Callable:
    """A ``GatedStrategy`` recorder that risks the snapshot's book each evening (``risk.book_from_snapshot``) and
    records the fund's and each book's yield DV01, CS01 and JTD (``date, scope_type, scope, dv01, cs01, jtd``)."""
    def record(snapshot) -> list[dict]:
        book = rk.book_from_snapshot(snapshot, ctx)
        summary = rk.summary_table(book)
        total = summary[summary["dimension"] == "total"]
        return [{"date": snapshot.day, "scope_type": r["scope_type"], "scope": r["scope"], "dv01": r["dv01_yield"],
                 "cs01": r["cs01"], "jtd": r["jtd"]} for r in total.to_dict("records")]
    return record
