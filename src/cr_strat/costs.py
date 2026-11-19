"""Trading costs of the rates strategies: bid-offer, CCIL charges, platform and custody fees, brokerage, GST, stamp
duty.

Every cost the return engine (``returns``) books comes from a dated table of rules. The defaults ship as package
data, ``src/cr_strat/data/cost_defaults.csv`` (``DEFAULT_COSTS_CSV``), each row with its ``source`` and ``status``
(``V`` verified in a primary source or a saved copy of one, ``P`` partly verified: a secondary source or a search
summary of a page that could not be read, ``U`` UNVERIFIED: no source, a conservative or standard reading for the
user to confirm). A table the user keeps in the same layout overrides them (``read_cost_table(overrides=...)``).

**Layout** (``COST_COLUMNS``): ``rule_id``, ``component`` (``COMPONENTS``), ``kind`` (an instrument kind, ``ois``,
``treps`` or ``market_repo``; several separated by ``;``; blank for all), ``venue`` (``ndsom``, ``otc``, ``rfq``,
``auction``, ``auction_nc``, ``omo``, ``switch``, ``when_issued``; several with ``;``; blank for any),
``liquidity`` (CCIL's class, ``liquid``, ``semi_liquid`` or ``illiquid``; several with ``;``; blank for any),
``isin``, ``min_years``
and ``max_years`` (residual maturity, or an OIS's tenor, in 365.25-day years; lower bound excluded as in
``funding``'s haircut table), ``effective_from`` and ``effective_to`` (inclusive; blank: open), ``value`` and
``unit`` (``UNITS``), ``minimum_rs`` and ``maximum_rs`` (bounds of a charge in rupees), ``per`` (``trade``,
``leg``, ``deal``), ``payer`` (``buyer``, ``seller``, ``both``, ``fund``: who pays a charge or duty), ``gst``
(``yes``: the charge bears GST), ``source`` and ``status``.

**Which rule applies** (``cost_rule``): among the rows of a component that match the instrument, venue, class,
maturity and date, an exception naming the ISIN wins, then one naming the venue (so the zero CCIL charge and spread of
auctions, OMOs and switches survive a user's newer schedule for all trades: a user overrides an exception only with a
row naming the same venue or ISIN; UNVERIFIED ranking, for the user to confirm); among the rest the latest
``effective_from`` wins; then the most specific row (a kind over a maturity bucket over a liquidity class: scores 4,
2 and 1); then a user row over a default one. Two rows left equal that differ raise, naming both; no matching row at
all raises, naming the component. A user row dated before a default row of the same component is superseded from the
default's date: date the user's row on or after it, or pass ``replace_components`` to drop the defaults of the
components the user's table gives.

**Amounts** (``CostBreakdown``, rupees the fund pays, positive):

* half spread: ``fill = mid + side x h x DV01(mid)`` for a bond (``side`` +1 buy, -1 sell; ``h`` in bp of yield;
  ``DV01`` per 100 face per bp from ``bonds`` at the mid's yield and the trade's settlement), so the spread cost is
  ``F/100 x h x DV01``; for an OIS ``fill_rate = mid_rate + side x h / 100`` (``side`` +1 paying fixed) and the cost
  ``h x pv01`` (``pv01``: rupees per bp of fixed rate, ``ois.ois_npv``). A fill given by the user is measured against
  its mid instead.
* charges (CCIL settlement, platform, custody, brokerage, the repo and TREPS charges): ``min(max(value x base /
  1 crore, minimum), maximum)`` per trade (per leg, twice, for a market repo), the base the face, the settlement
  value or the repayment value as ``unit`` says; nothing when the value is 0.
* GST: ``gst_rate`` percent of the charges whose rule says ``gst = yes``; never on interest, prices or stamp duty.
* stamp duty: ``value`` percent of the consideration (or of an OIS's notional), on the payer's side only.

**Defaults and their status** (the CSV has each source): half spreads for G-secs by CCIL liquidity class 0.625,
2.25 and 4.5 bp (P: CCIL's monthly NDS-OM bid-ask by class, Aug-Oct 2026, read through a search summary), a G-sec
without a class 4.5 bp, SDLs 5, T-bills and CMBs 1, STRIPS 10, CP and CD 5, OIS 0.5 bp of rate to 5 years and 1.5
beyond (all U); primary allotments and RBI's OMO and switch operations no spread and no CCIL charge (U). CCIL's
settlement charges from 1 Oct 2019 (V, an earlier probe of CCIL's page): dated securities and SDLs Rs 95 per crore of
face (minimum 20, maximum 4,750 a trade), T-bills Rs 45, STRIPS Rs 45 per crore of settlement value (maximum 2,250),
market repo Rs 10 per crore of face a leg and TREPS Rs 10 per crore of repayment value a deal (minimum 10, maximum
1,500; a search summary gives Rs 12.50 a repo leg, a conflict left open); held back to 2015 as U. No NDS-OM dealing
fee (U); exchange fees on CP and CD Rs 2.5 per crore (P); custody 0 (a U placeholder); no brokerage default (a
brokered trade without a user rate is refused). GST 18% from 1 Jul 2017 (P), service tax before it from memory (U).
Stamp duty (the regime in force from 1 Jul 2020, PIB, V; the rates through secondary sources, P): none on government
securities or on repo in them; 0.0001% of the consideration on CP paid by the transferor (FIMMDA's CP guidelines; a
search summary of NSE's page says the buyer pays on exchange-settled trades, a conflict left open) and on CD charged to
the fund on every trade, bought or sold (payer U: FIMMDA's CD guidelines name none); 0.0001% of an OIS's notional
charged to the fund on every OIS trade (base and payer U); before 1 Jul 2020 each state's act applied and the 2020
rates are held back (U). TREPS initial margin 0.50% of the net second-leg consideration from 5 Jul 2025 (V;
held back before as U; who posts it and how is U). Income tax and TDS are left out: the P&L is pre-tax.

**Limits.** No cost here was read off a published schedule for every date: CCIL's pages were read once by an earlier
probe, the stamp duty schedule only through secondary sources (India Code returned errors), and no public source
gives bid-offer for SDLs, bills, STRIPS, CP, CD or OIS. Market impact beyond the half spread is not modelled (a
size-dependent spread is a user row per liquidity class). The fund's own custodian, clearing member and broker terms
must be entered by the user.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from cr_strat.calendars import DATA_DIR
from cr_strat.conventions import as_date

DEFAULT_COSTS_CSV = DATA_DIR / "cost_defaults.csv"
COST_COLUMNS = ("rule_id", "component", "kind", "venue", "liquidity", "isin", "min_years", "max_years",
                "effective_from", "effective_to", "value", "unit", "minimum_rs", "maximum_rs", "per", "payer", "gst",
                "source", "status")
COMPONENTS = ("half_spread_bp", "ccil_settlement", "repo_leg_charge", "treps_charge", "platform_fee", "custody",
              "brokerage", "gst_rate", "stamp_duty", "treps_margin_pct")
UNITS = ("bp_yield", "bp_rate", "rs_per_crore_face", "rs_per_crore_value", "rs_per_crore_repayment",
         "pct_consideration", "pct_notional", "pct")
CHARGE_UNITS = ("rs_per_crore_face", "rs_per_crore_value", "rs_per_crore_repayment", "pct_consideration",
                "pct_notional")
PERS = ("trade", "leg", "deal")
PAYERS = ("buyer", "seller", "both", "fund")
STATUSES = ("V", "P", "U", "user")
VENUES = ("ndsom", "otc", "rfq", "auction", "auction_nc", "omo", "switch", "when_issued")
LIQUIDITY_CLASSES = ("liquid", "semi_liquid", "illiquid")
REPO_SEGMENTS = ("treps", "market_repo")
CRORE = 1e7
_SCORE = {"isin": 100, "venue": 10, "kind": 4, "bucket": 2, "liquidity": 1}
_RULE_FIELDS = ("value", "unit", "minimum_rs", "maximum_rs", "per", "payer", "gst")


def _blank(value) -> bool:
    if value is None or (isinstance(value, str) and not value.strip()):
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _text(value) -> str:
    return "" if _blank(value) else str(value).strip()


def _number(value, where: str, *, blank_ok: bool = True) -> float:
    if _blank(value):
        if blank_ok:
            return math.nan
        raise ValueError(f"{where} is blank")
    if isinstance(value, bool):
        raise ValueError(f"{where}: {value!r} is not a number")
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{where}: {value!r} is not a number") from None
    if not math.isfinite(out):
        raise ValueError(f"{where}: {value!r} is not a finite number")
    return out


def _choices(cell: str, allowed: tuple[str, ...] | None, where: str) -> str:
    """A ``;``-separated cell, lower case, each part checked against ``allowed``, sorted and joined again."""
    parts = sorted({p.strip().lower().replace("-", "_") for p in cell.split(";") if p.strip()})
    if allowed is not None:
        bad = [p for p in parts if p not in allowed]
        if bad:
            raise ValueError(f"{where}: {bad[0]!r} is not one of {allowed}")
    return ";".join(parts)


@dataclass(frozen=True)
class CostRule:
    """One row of the cost table, as ``cost_rule`` chose it."""

    rule_id: str
    component: str
    value: float
    unit: str
    minimum_rs: float
    maximum_rs: float
    per: str
    payer: str
    gst: bool
    source: str
    status: str
    origin: str

    @property
    def tag(self) -> str:
        """``rule_id:status``, as fills record the rules they used."""
        return f"{self.rule_id}:{self.status}"


@dataclass(frozen=True)
class CostBreakdown:
    """The costs of one trade or deal, in rupees the fund pays (positive), with the rules used.

    ``fill``: the price (per 100) or rate (percent) the trade was done at; ``half_spread``: in bp of yield or rate
    (NaN when a given fill was measured against its mid). ``fees`` = ``ccil + platform + custody``; ``total`` the
    explicit costs ``fees + brokerage + gst + stamp_duty`` (the spread cost is in the price and left out of it).
    ``rules`` holds ``rule_id:status`` of each rule used; ``unverified`` the ids of the default rules marked U.
    """

    fill: float
    half_spread: float
    spread_cost: float
    ccil: float
    platform: float
    custody: float
    brokerage: float
    gst: float
    stamp_duty: float
    rules: tuple[str, ...] = ()
    unverified: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    extra: dict = field(default_factory=dict)

    @property
    def fees(self) -> float:
        return self.ccil + self.platform + self.custody

    @property
    def total(self) -> float:
        return self.fees + self.brokerage + self.gst + self.stamp_duty


def _read_frame(table, what: str) -> pd.DataFrame:
    if isinstance(table, pd.DataFrame):
        frame = table.copy()
    else:
        path = Path(table)
        if not path.exists():
            raise FileNotFoundError(f"{what} not found: {path}")
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    frame.columns = [str(c).strip().lower() for c in frame.columns]
    return frame


def _check_rows(frame: pd.DataFrame, origin: str, what: str) -> list[dict]:
    need = ("component", "effective_from", "value", "unit")
    missing = [c for c in need if c not in frame]
    if missing:
        raise ValueError(f"{what} lacks {missing} (columns: {', '.join(COST_COLUMNS)})")
    rows = []
    for n, rec in enumerate(frame.to_dict("records"), start=2):
        if all(_blank(v) for v in rec.values()):  # a spreadsheet's trailing ,,, line: dropped, its line still counted
            continue
        where = f"{what} line {n}"
        component = _text(rec.get("component")).lower()
        if component not in COMPONENTS:
            raise ValueError(f"{where}: component {rec.get('component')!r} is not one of {COMPONENTS}")
        unit = _text(rec.get("unit")).lower()
        if unit not in UNITS:
            raise ValueError(f"{where}: unit {rec.get('unit')!r} is not one of {UNITS}")
        try:
            start = as_date(rec["effective_from"])
        except (TypeError, ValueError):
            raise ValueError(f"{where}: effective_from {rec.get('effective_from')!r} is not a date") from None
        end = None
        if not _blank(rec.get("effective_to")):
            try:
                end = as_date(rec["effective_to"])
            except (TypeError, ValueError):
                raise ValueError(f"{where}: effective_to {rec.get('effective_to')!r} is not a date") from None
            if end < start:
                raise ValueError(f"{where}: effective_to {end} is before effective_from {start}")
        value = _number(rec.get("value"), f"{where}: value", blank_ok=False)
        if value < 0:
            raise ValueError(f"{where}: value must be zero or more, not {value}")
        lo = _number(rec.get("minimum_rs"), f"{where}: minimum_rs")
        hi = _number(rec.get("maximum_rs"), f"{where}: maximum_rs")
        if not math.isnan(lo) and not math.isnan(hi) and lo > hi:
            raise ValueError(f"{where}: minimum_rs {lo:g} is above maximum_rs {hi:g}")
        y0 = _number(rec.get("min_years"), f"{where}: min_years")
        y1 = _number(rec.get("max_years"), f"{where}: max_years")
        if not math.isnan(y0) and not math.isnan(y1) and not y0 < y1:
            raise ValueError(f"{where}: min_years {y0:g} must be below max_years {y1:g}")
        per = _text(rec.get("per")).lower() or "trade"
        if per not in PERS:
            raise ValueError(f"{where}: per {rec.get('per')!r} is not one of {PERS}")
        payer = _text(rec.get("payer")).lower() or "fund"
        if payer not in PAYERS:
            raise ValueError(f"{where}: payer {rec.get('payer')!r} is not one of {PAYERS}")
        gst_text = _text(rec.get("gst")).lower() or "no"
        if gst_text not in ("yes", "no"):
            raise ValueError(f"{where}: gst must be yes or no, not {rec.get('gst')!r}")
        status = _text(rec.get("status")) or ("user" if origin == "user" else "")
        if status not in STATUSES:
            raise ValueError(f"{where}: status must be one of V, P, U (or blank in a user table), not "
                             f"{rec.get('status')!r}")
        source = _text(rec.get("source"))
        if not source:
            raise ValueError(f"{where}: source is empty; say where the cost comes from")
        rule_id = _text(rec.get("rule_id")) or f"{origin}{n}"
        rows.append({
            "rule_id": rule_id, "component": component, "kind": _choices(_text(rec.get("kind")), None, where),
            "venue": _choices(_text(rec.get("venue")), VENUES, where),
            "liquidity": _choices(_text(rec.get("liquidity")), LIQUIDITY_CLASSES, where),
            "isin": _text(rec.get("isin")).upper(), "min_years": y0, "max_years": y1, "effective_from": start,
            "effective_to": end, "value": value, "unit": unit, "minimum_rs": lo, "maximum_rs": hi, "per": per,
            "payer": payer, "gst": gst_text == "yes", "source": source, "status": status, "origin": origin})
    return rows


def read_cost_table(path=DEFAULT_COSTS_CSV, *, overrides=None, replace_components=()) -> pd.DataFrame:
    """The cost table: the defaults (``path``, a CSV or a DataFrame) and, optionally, a user table that overrides them.

    ``replace_components`` drops every default row of those components (use it when the user's table should stand
    alone for them, whatever its dates). Rule ids must be unique across both tables. The frame has ``COST_COLUMNS``
    plus ``origin`` (``default`` or ``user``), sorted by component, effective date and rule id.
    """
    rows = _check_rows(_read_frame(path, "the cost table"), "default", "the cost table")
    unknown = [c for c in replace_components if c not in COMPONENTS]
    if unknown:
        raise ValueError(f"replace_components: {unknown[0]!r} is not one of {COMPONENTS}")
    if replace_components:
        rows = [r for r in rows if r["component"] not in set(replace_components)]
    if overrides is not None:
        rows += _check_rows(_read_frame(overrides, "the user cost table"), "user", "the user cost table")
    ids = [r["rule_id"] for r in rows]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError(f"rule ids used twice in the cost tables: {dupes[:5]}")
    frame = pd.DataFrame(rows, columns=[*COST_COLUMNS, "origin"])
    return frame.sort_values(["component", "effective_from", "rule_id"], kind="mergesort").reset_index(drop=True)


class CostTable:
    """A cost table (``read_cost_table``'s frame) with its rows grouped by component, for repeated lookups."""

    def __init__(self, frame: pd.DataFrame):
        if not isinstance(frame, pd.DataFrame) or "origin" not in frame:
            raise TypeError("CostTable needs read_cost_table's frame")
        self.frame = frame
        self.by_component: dict[str, list[dict]] = {c: [] for c in COMPONENTS}
        for rec in frame.to_dict("records"):
            if rec["effective_to"] is not None and pd.isna(rec["effective_to"]):
                rec["effective_to"] = None
            self.by_component[rec["component"]].append(rec)


def _as_table(table) -> CostTable:
    """A ``CostTable`` from one, from ``read_cost_table``'s frame, or from a CSV path of defaults."""
    if isinstance(table, CostTable):
        return table
    if isinstance(table, pd.DataFrame) and "origin" in table:
        return CostTable(table)
    return CostTable(read_cost_table(table))


def _score(rec: dict) -> int:
    return (_SCORE["isin"] * bool(rec["isin"]) + _SCORE["venue"] * bool(rec["venue"])
            + _SCORE["kind"] * bool(rec["kind"])
            + _SCORE["bucket"] * (not (math.isnan(rec["min_years"]) and math.isnan(rec["max_years"])))
            + _SCORE["liquidity"] * bool(rec["liquidity"]))


def _tier(rec: dict) -> tuple[bool, bool]:
    """An exception's rank: a row naming an ISIN, then one naming a venue, before any other row of any date."""
    return bool(rec["isin"]), bool(rec["venue"])


def cost_rule(table, component: str, *, kind: str, on, venue: str | None = None, liquidity: str | None = None,
              years: float | None = None, isin: str | None = None) -> CostRule:
    """The rule of ``component`` for a ``kind`` (and ``venue``, ``liquidity``, residual ``years``, ``isin``) on ``on``.

    Among the rows in force, an ISIN exception first and a venue exception next (a newer row that names neither
    does not cancel an older exception: only a row naming the same ISIN or venue, or the exception's own
    ``effective_to``, ends it); then the latest ``effective_from``, then the most specific row, then a user row over a
    default one; equal rows that differ raise, and so does the absence of any matching row. A ``kind``, ``venue`` or
    ``liquidity`` cell may list several values with ``;``.
    """
    frame = _as_table(table)
    if component not in COMPONENTS:
        raise ValueError(f"component must be one of {COMPONENTS}, not {component!r}")
    d = as_date(on)
    kind = str(kind).strip().lower()
    venue = None if venue is None or not str(venue).strip() else str(venue).strip().lower()
    liquidity = None if liquidity is None or not str(liquidity).strip() else str(liquidity).strip().lower().replace(
        "-", "_")
    isin = None if isin is None or not str(isin).strip() else str(isin).strip().upper()
    matches = []
    for rec in frame.by_component[component]:
        if rec["effective_from"] > d or (rec["effective_to"] is not None and rec["effective_to"] < d):
            continue
        if rec["isin"] and rec["isin"] != isin:
            continue
        if rec["kind"] and kind not in rec["kind"].split(";"):
            continue
        if rec["venue"] and (venue is None or venue not in rec["venue"].split(";")):
            continue
        if rec["liquidity"] and (liquidity is None or liquidity not in rec["liquidity"].split(";")):
            continue
        if not (math.isnan(rec["min_years"]) and math.isnan(rec["max_years"])):
            if years is None or not math.isfinite(years):
                continue
            if not math.isnan(rec["min_years"]) and not years > rec["min_years"]:
                continue
            if not math.isnan(rec["max_years"]) and not years <= rec["max_years"]:
                continue
        matches.append(rec)
    what = (f"{component} for a {kind}" + (f" ({isin})" if isin else "") + (f" on venue {venue}" if venue else "")
            + (f", class {liquidity}" if liquidity else "") + (f", {years:.2f} years" if years is not None else "")
            + f" on {d}")
    if not matches:
        raise ValueError(f"no cost rule gives the {what}: add a row to the cost table (the defaults start on "
                         "1 Jan 2015 and hold no brokerage)")
    tier = max(_tier(r) for r in matches)  # an ISIN or venue exception outranks rows that do not name one
    pool = [r for r in matches if _tier(r) == tier]
    latest = max(r["effective_from"] for r in pool)
    best = max(_score(r) for r in pool if r["effective_from"] == latest)
    tied = [r for r in pool if r["effective_from"] == latest and _score(r) == best]
    if any(r["origin"] == "user" for r in tied):
        tied = [r for r in tied if r["origin"] == "user"]
    distinct = {tuple(r[f] if not (isinstance(r[f], float) and math.isnan(r[f])) else None for f in _RULE_FIELDS)
                for r in tied}
    if len(distinct) > 1:
        ids = ", ".join(sorted(r["rule_id"] for r in tied))
        raise ValueError(f"the cost table gives the {what} several equally specific rules that differ ({ids}): keep "
                         "one, or make one more specific")
    rec = sorted(tied, key=lambda r: r["rule_id"])[0]
    return CostRule(rule_id=rec["rule_id"], component=component, value=float(rec["value"]), unit=rec["unit"],
                    minimum_rs=float(rec["minimum_rs"]), maximum_rs=float(rec["maximum_rs"]), per=rec["per"],
                    payer=rec["payer"], gst=bool(rec["gst"]), source=rec["source"], status=rec["status"],
                    origin=rec["origin"])


def _side(side) -> int:
    if isinstance(side, str):
        text = side.strip().lower()
        if text in ("buy", "pay", "pay_fixed", "long"):
            return 1
        if text in ("sell", "receive", "receive_fixed", "short"):
            return -1
    elif side in (1, -1) and not isinstance(side, bool):
        return int(side)
    raise ValueError(f"side must be buy or sell (+1 or -1; for an OIS pay or receive), not {side!r}")


def charge(rule: CostRule, *, face: float = math.nan, value: float = math.nan, repayment: float = math.nan,
           notional: float = math.nan) -> float:
    """A charge in rupees under ``rule``: ``min(max(rate x base, minimum), maximum)``; 0 when the rate is 0.

    The base follows the rule's unit: face, settlement value (or consideration), repayment value (per crore), or a
    percentage of the consideration or notional.
    """
    if rule.unit not in CHARGE_UNITS:
        raise ValueError(f"rule {rule.rule_id}: unit {rule.unit} is not a charge")
    if rule.value == 0:
        return 0.0
    base = {"rs_per_crore_face": face, "rs_per_crore_value": value, "rs_per_crore_repayment": repayment,
            "pct_consideration": value, "pct_notional": notional}[rule.unit]
    if not math.isfinite(base):
        raise ValueError(f"rule {rule.rule_id} ({rule.unit}) needs its base amount")
    amount = rule.value * abs(base) / (CRORE if rule.unit.startswith("rs_") else 100)
    if not math.isnan(rule.minimum_rs):
        amount = max(amount, rule.minimum_rs)
    if not math.isnan(rule.maximum_rs):
        amount = min(amount, rule.maximum_rs)
    return amount


def _pays(rule: CostRule, side: int) -> bool:
    return rule.payer in ("both", "fund") or (rule.payer == "buyer" and side > 0) or (rule.payer == "seller"
                                                                                       and side < 0)


def fill_price(mid: float, side, half_spread_bp: float, dv01_per100: float) -> float:
    """``mid + side x half_spread_bp x dv01_per100``: a buy pays up, a sell gives up, by the half spread in price."""
    s = _side(side)
    for name, v in (("mid", mid), ("half_spread_bp", half_spread_bp), ("dv01_per100", dv01_per100)):
        if not math.isfinite(float(v)):
            raise ValueError(f"{name} must be a finite number, not {v!r}")
    if half_spread_bp < 0 or dv01_per100 < 0:
        raise ValueError("half_spread_bp and dv01_per100 must be zero or more")
    return float(mid) + s * float(half_spread_bp) * float(dv01_per100)


class _Ledger:
    """Collects the rules a breakdown used."""

    def __init__(self, table):
        self.table = _as_table(table)
        self.rules: list[str] = []
        self.unverified: list[str] = []

    def rule(self, component: str, **kw) -> CostRule:
        rule = cost_rule(self.table, component, **kw)
        if rule.tag not in self.rules:
            self.rules.append(rule.tag)
        if rule.status == "U" and rule.origin == "default" and rule.rule_id not in self.unverified:
            self.unverified.append(rule.rule_id)
        return rule


def bond_trade_costs(table, *, kind: str, side, face: float, mid: float, dv01_per100: float, trade_date,
                     venue: str = "ndsom", liquidity: str | None = None, years: float | None = None,
                     accrued: float = 0.0, broker: str | None = None, isin: str | None = None,
                     fill: float | None = None) -> CostBreakdown:
    """Costs of a bond, bill, STRIP, CP or CD trade of ``face`` rupees at ``mid`` (clean, per 100) on ``trade_date``.

    Without ``fill`` the fill is ``mid`` plus or minus the half spread of the table (``fill_price``); with it, the
    spread cost is measured against ``mid`` (``F/100 x side x (fill - mid)``, a cost when positive). The
    consideration is ``F/100 x (fill + accrued)``. A trade with a ``broker`` needs a brokerage rule.
    """
    s = _side(side)
    f = _number(face, "face", blank_ok=False)
    if not f > 0:
        raise ValueError(f"face must be a positive number of rupees, not {face!r}")
    ledger = _Ledger(table)
    common = {"kind": kind, "on": trade_date, "venue": venue, "liquidity": liquidity, "years": years, "isin": isin}
    notes = []
    if fill is None:
        h = ledger.rule("half_spread_bp", **common)
        if h.unit != "bp_yield":
            raise ValueError(f"rule {h.rule_id}: a bond's half spread must be in bp_yield, not {h.unit}")
        price = fill_price(mid, s, h.value, dv01_per100)
        half = h.value
    else:
        price = _number(fill, "fill", blank_ok=False)
        half = math.nan
    spread = f / 100 * s * (price - _number(mid, "mid", blank_ok=False))
    consideration = f / 100 * (price + float(accrued))
    ccil_rule = ledger.rule("ccil_settlement", **common)
    platform_rule = ledger.rule("platform_fee", **common)
    custody_rule = ledger.rule("custody", **common)
    amounts = {}
    for name, rule in (("ccil", ccil_rule), ("platform", platform_rule), ("custody", custody_rule)):
        amounts[name] = charge(rule, face=f, value=consideration) if _pays(rule, s) else 0.0
    brokerage, brokerage_rule = 0.0, None
    if broker is not None and str(broker).strip():
        try:
            brokerage_rule = ledger.rule("brokerage", **common)
        except ValueError:
            raise ValueError(f"a trade through broker {broker!r} needs a brokerage rule in the cost table (no default "
                             f"is assumed): {kind} on {as_date(trade_date)}") from None
        brokerage = charge(brokerage_rule, face=f, value=consideration)
    taxable = sum(amounts[n] for n, r in (("ccil", ccil_rule), ("platform", platform_rule),
                                          ("custody", custody_rule)) if r.gst)
    if brokerage_rule is not None and brokerage_rule.gst:
        taxable += brokerage
    gst = 0.0
    if taxable:
        gst = taxable * ledger.rule("gst_rate", kind=kind, on=trade_date).value / 100
    stamp_rule = ledger.rule("stamp_duty", **common)
    stamp = charge(stamp_rule, face=f, value=consideration) if _pays(stamp_rule, s) else 0.0
    if fill is not None and spread == 0:
        notes.append("fill given without a separate mid: no spread cost measured")
    return CostBreakdown(fill=price, half_spread=half, spread_cost=spread, ccil=amounts["ccil"],
                         platform=amounts["platform"], custody=amounts["custody"], brokerage=brokerage, gst=gst,
                         stamp_duty=stamp, rules=tuple(ledger.rules), unverified=tuple(ledger.unverified),
                         notes=tuple(notes), extra={"consideration": consideration})


def ois_trade_costs(table, *, side, notional: float, pv01: float, tenor_years: float, trade_date,
                    mid_rate_pct: float, fill_rate_pct: float | None = None, venue: str = "otc") -> CostBreakdown:
    """Costs of an OIS of ``notional`` rupees (``side`` pay or receive fixed): half spread in bp of rate, fees, stamp
    duty.

    ``fill_rate = mid + side x h / 100`` unless ``fill_rate_pct`` is given; the spread cost is ``|fill - mid| x 100 x
    pv01`` (``pv01`` rupees per bp of fixed rate, positive).
    """
    s = _side(side)
    n = _number(notional, "notional", blank_ok=False)
    if not n > 0:
        raise ValueError(f"notional must be a positive number of rupees, not {notional!r}")
    ledger = _Ledger(table)
    common = {"kind": "ois", "on": trade_date, "venue": venue, "years": float(tenor_years)}
    mid = _number(mid_rate_pct, "mid_rate_pct", blank_ok=False)
    if fill_rate_pct is None:
        h = ledger.rule("half_spread_bp", **common)
        if h.unit != "bp_rate":
            raise ValueError(f"rule {h.rule_id}: an OIS half spread must be in bp_rate, not {h.unit}")
        rate, half = mid + s * h.value / 100, h.value
    else:
        rate, half = _number(fill_rate_pct, "fill_rate_pct", blank_ok=False), math.nan
    spread = s * (rate - mid) * 100 * abs(float(pv01))
    platform_rule = ledger.rule("platform_fee", **common)
    platform = charge(platform_rule, face=n, value=n, notional=n)
    gst = platform * ledger.rule("gst_rate", kind="ois", on=trade_date).value / 100 if platform and \
        platform_rule.gst else 0.0
    stamp_rule = ledger.rule("stamp_duty", **common)
    stamp = charge(stamp_rule, notional=n, value=n, face=n) if _pays(stamp_rule, s) else 0.0
    return CostBreakdown(fill=rate, half_spread=half, spread_cost=spread, ccil=0.0, platform=platform, custody=0.0,
                         brokerage=0.0, gst=gst, stamp_duty=stamp, rules=tuple(ledger.rules),
                         unverified=tuple(ledger.unverified))


def repo_deal_costs(table, *, segment: str, face: float, cash: float, repayment: float, deal_date) -> CostBreakdown:
    """CCIL's charge on one overnight deal: TREPS on the repayment value per deal, market repo on face per leg (both
    legs booked on the deal date), and GST on it. Repo in government securities bears no stamp duty."""
    if segment not in REPO_SEGMENTS:
        raise ValueError(f"segment must be one of {REPO_SEGMENTS}, not {segment!r}")
    ledger = _Ledger(table)
    if segment == "treps":
        rule = ledger.rule("treps_charge", kind="treps", on=deal_date)
        amount = charge(rule, repayment=repayment, value=cash, face=face)
    else:
        rule = ledger.rule("repo_leg_charge", kind="market_repo", on=deal_date)
        legs = 2 if rule.per == "leg" else 1
        amount = legs * charge(rule, face=face, value=cash, repayment=repayment)
    gst = amount * ledger.rule("gst_rate", kind=segment, on=deal_date).value / 100 if amount and rule.gst else 0.0
    stamp_rule = ledger.rule("stamp_duty", kind=segment, on=deal_date)
    stamp = charge(stamp_rule, face=face, value=cash, repayment=repayment, notional=cash)
    return CostBreakdown(fill=math.nan, half_spread=math.nan, spread_cost=0.0, ccil=amount, platform=0.0, custody=0.0,
                         brokerage=0.0, gst=gst, stamp_duty=stamp, rules=tuple(ledger.rules),
                         unverified=tuple(ledger.unverified))


def treps_margin(table, *, repayment: float, on) -> tuple[float, CostRule]:
    """TREPS initial margin (rupees) on a borrowing whose second leg repays ``repayment``: ``treps_margin_pct`` of
    it."""
    rule = cost_rule(_as_table(table), "treps_margin_pct", kind="treps", on=on)
    return rule.value / 100 * abs(float(repayment)), rule


def unverified_rules(table) -> pd.DataFrame:
    """The default rules marked U, for a run's notes and the docs."""
    frame = _as_table(table).frame
    return frame[(frame["status"] == "U") & (frame["origin"] == "default")].reset_index(drop=True)


def years_between(start, end) -> float:
    """365.25-day years from ``start`` to ``end`` (the residual maturity the table's buckets use)."""
    return (as_date(end) - as_date(start)).days / 365.25


__all__ = ["CHARGE_UNITS", "COMPONENTS", "COST_COLUMNS", "CRORE", "DEFAULT_COSTS_CSV", "LIQUIDITY_CLASSES", "PAYERS",
           "PERS", "REPO_SEGMENTS", "STATUSES", "UNITS", "VENUES", "CostBreakdown", "CostRule", "CostTable",
           "bond_trade_costs",
           "charge", "cost_rule", "fill_price", "ois_trade_costs", "read_cost_table", "repo_deal_costs",
           "treps_margin", "unverified_rules", "years_between"]

