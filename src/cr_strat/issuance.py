"""Issuance ledger: every change to the outstanding stock of each G-sec, SDL and T-bill, and the stock on any date.

``build_ledger(auctions, switches, ...)`` turns the auction table
(``auctions.build_auctions``) and RBI's switch results
(``auctions.normalise_switches``) into one row per security per event
(``LEDGER_COLUMNS``), with signed amounts in ₹ crore of face value:

==============  ===========================================================  =========================
Event           Source                                                       Dated on
==============  ===========================================================  =========================
``issue``       an auction row that is a new issue (``issue_type = 'new'``)  settlement
``reissue``     any other auction row that counts as issuance                settlement
``switch_out``  the source security of a switch: minus the amount accepted   settlement (T+1, checked)
``switch_in``   the destination: plus the amount issued                      settlement
``buyback``     a table of buybacks, if one is given (no parser yet)         its date
``maturity``    the maturity date (RBI's list, a name, or inferred)          redemption day
==============  ===========================================================  =========================

The key is the ISIN where one is known, else the name key (``7.68% GS 2023``
for a security matured before RBI's lists were saved), else, for a T-bill,
``<tenor>DTB <maturity>``. Auction rows that do not count as issuance
(rejected, or flagged as conversion legs) are left out; so are T-bills with
no maturity date. An issuance row with no settlement date is dated T+1 of
its auction (``date_source = 'assumed_t1'``); one with neither date is
refused. A maturity's amount is minus the stock just before it, from the
nearest RBI snapshot where one holds the security, else the sum of its
earlier events; it is dated on the redemption day (the previous business
day when the maturity falls on a holiday, on a lenient calendar beyond the
covered years). A maturity after the inputs' last date (the latest auction
or RBI list) is noted ``scheduled``: later issuance is not known. A key with
no maturity date but a year in its name (``FRB 2024``, a floating-rate bond
the yield cannot date, or a maturity the search refused) is redeemed by the
end of that year: its maturity event falls on the year's last business day,
``date_source = 'name_year'``, a placeholder date with ``maturity_date``
left blank.

``shortfall`` decides what an auction that accepted far less than the
notified amount adds (``auctions`` flags it ``shortfall``; it happened often
in 2017-2022). ``"devolved"`` (the default) books the notified amount: the
rest devolved on the primary dealers who underwrite every GoI auction, which
FIMMDA does not show. Against RBI's list of 16 Nov 2026 this brings 23 of the
24 affected securities closer and three to the exact amount (5.77% GS 2030:
₹1,23,000 crore against ₹64,545 crore accepted). ``"accepted"`` books
FIMMDA's accepted amount. Such rows say so in ``amount_basis``.

``competitive_only`` decides what a GoI auction known only by its
competitive amount adds (``accepted_basis = 'competitive_only'``: FIMMDA's
rows from FY2025-26 with no Handbook or RBI line, whose non-competitive
allotment is unknown). ``"notified"`` (the default) books the notified
amount when the competitive amount is 95% to 100% of it
(``notified_assumed_full``): non-competitive bids, up to 5% of the notified
amount, take the rest. In all 90 accepted Handbook auctions of FY2025-27
competitive plus non-competitive equals the notified amount to within ₹1
crore. ``"accepted"`` books the competitive amount. A greenshoe (more
accepted than notified) or a shortfall keeps its own reading.

**Coverage.** The ledger knows what it does not hold. ``coverage(ledger)``
(also ``ledger.attrs['coverage']``, written beside the ledger CSV as
``<name>_coverage.csv``) lists the periods each kind's auctions cover, split
wherever no auction was seen for more than ``max_gap_days`` (75: the gap
between one year's last auction and the next year's first is up to 69 days in
FIMMDA's files), and the period switch and buyback releases cover, which is
only what the caller declares with ``operations_from`` (nothing by default:
no history of switches or buybacks has been saved).

**Outstanding.** ``outstanding_on(ledger, date, snapshot=None)`` gives the stock
of every key at the end of ``date``. With an RBI snapshot (``sovereign.
load_goi_outstanding`` frames, one or several dates) a security in it is
reconstructed from the snapshot nearest the date: backward, ``snapshot -
events after the date``, or forward, ``snapshot + events up to the date``.
Every other key is summed forward from its first event. A key is zero from
its redemption day (the ledger's maturity event, else its maturity date):
``matured``, or ``matured_date_unknown`` for a ``name_year`` placeholder.
Each row says how it was reconstructed and whether its
history is complete: ``issuance_complete`` (the auctions between the anchor
and the date are covered, and for a forward sum the security's new issue is
in the ledger), ``operations_complete`` (switches and buybacks are covered
over the same period) and the reasons when not.

``reconcile(ledger, snapshot)`` compares the forward sum with RBI's list,
ISIN by ISIN. For a security first issued after the earliest auction file,
with no coverage gap and no switch or buyback since, the two should agree;
the differences show missing auctions, non-competitive amounts FIMMDA leaves
out, devolvement, switches and buybacks. ``supply_summary`` totals gross
issuance, switches, buybacks, redemptions and net supply by month (or any
pandas period) and tenor bucket, for the supply-cycle strategies.

RBI grants no reuse licence and its disclaimer prohibits caching without
permission; FIMMDA states no terms: ask both in writing before keeping a
history (``docs/rates_data_sources.md``).
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pandas as pd

from cr_strat.calendars import BusinessCalendar, CalendarCoverageError, mumbai_settlement_calendar
from cr_strat.sovereign import CENTRAL_KINDS, FRB, GREEN, GSEC, SDL, TBILL, parse_security_name

LEDGER_COLUMNS = (
    "key", "isin", "name_key", "security", "kind", "event", "date", "date_source", "amount_cr", "amount_basis",
    "auction_date", "auction_id", "maturity_date", "maturity_source", "coupon_pct", "maturity_year",
    "state_code", "source", "source_file", "source_row", "note",
)
EVENTS = ("issue", "reissue", "switch_in", "switch_out", "buyback", "maturity")
ISSUANCE_EVENTS = ("issue", "reissue")
COVERAGE_COLUMNS = ("source", "start", "end", "detail")
OUTSTANDING_COLUMNS = (
    "key", "isin", "name_key", "security", "kind", "maturity_date", "tenor_bucket", "date", "outstanding_cr",
    "method", "anchor_date", "first_event_date", "issuance_complete", "operations_complete", "reasons",
)
RECONCILE_COLUMNS = (
    "isin", "key", "security", "kind", "as_of", "rbi_cr", "ledger_cr", "difference_cr", "difference_pct",
    "first_issue_date", "first_event", "events", "issuance_complete", "operations_complete", "reasons",
)
TENOR_BUCKETS = ((0, 1, "0-1y"), (1, 3, "1-3y"), (3, 5, "3-5y"), (5, 10, "5-10y"), (10, 15, "10-15y"),
                 (15, 30, "15-30y"), (30, 1000, "30y+"))
MAX_GAP_DAYS = 75
SUPPLY_COLUMNS = ("period", "kind", "tenor_bucket", "gross_issuance_cr", "switch_in_cr", "switch_out_cr",
                  "buyback_cr", "redemption_cr", "net_cr", "events")
GOI_KINDS = frozenset({GSEC, GREEN, FRB})  # dated GoI auctions: non-competitive bids sit inside the notified amount
NONCOMPETITIVE_MAX = 0.05  # of the notified amount
MATURED_METHODS = ("matured", "matured_date_unknown")


def _ts(value) -> pd.Timestamp:
    if value is None or (not isinstance(value, str) and pd.isna(value)) or value == "":
        return pd.NaT
    return pd.Timestamp(value).normalize()


def _missing(value) -> bool:
    if value is None or (isinstance(value, str) and value == ""):
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def tenor_bucket(maturity, on, buckets=TENOR_BUCKETS) -> str:
    """Bucket of the years from ``on`` to ``maturity`` (``TENOR_BUCKETS``); ``'unknown'`` without a maturity."""
    maturity, on = _ts(maturity), _ts(on)
    if maturity is pd.NaT or on is pd.NaT:
        return "unknown"
    years = (maturity - on).days / 365.25
    for lo, hi, label in buckets:
        if lo <= years < hi:
            return label
    return buckets[-1][2] if years >= buckets[-1][1] else buckets[0][2]


def _key(isin, name_key, security) -> str:
    for value in (isin, name_key):
        if not _missing(value):
            return str(value)
    return str(security)


def _redemption_day(maturity: pd.Timestamp, calendar: BusinessCalendar | None) -> tuple[pd.Timestamp, str]:
    if calendar is None:
        return maturity, "maturity"
    day = maturity.date()
    try:
        paid = calendar.adjust(day, "preceding")
        source = "maturity" if paid == day else "redemption_preceding"
    except CalendarCoverageError:
        paid = calendar.lenient().adjust(day, "preceding")
        source = "maturity" if paid == day else "redemption_preceding_weekend_only"
    return pd.Timestamp(paid), source


def _assumed_t1(day: pd.Timestamp, calendar: BusinessCalendar | None) -> pd.Timestamp:
    """T+1 of ``day`` on ``calendar`` (lenient beyond its covered years): a settlement no source gave."""
    if calendar is None:
        return day + pd.offsets.BDay(1)
    try:
        return pd.Timestamp(calendar.settlement_date(day.date(), 1))
    except CalendarCoverageError:
        return pd.Timestamp(calendar.lenient().settlement_date(day.date(), 1))


def _year_end(year: int, calendar: BusinessCalendar | None) -> pd.Timestamp:
    """The last business day of ``year`` (31 December without a calendar)."""
    paid, _ = _redemption_day(pd.Timestamp(int(year), 12, 31), calendar)
    return paid


# --- Coverage ----------------------------------------------------------------------------------------

def auction_coverage(auctions: pd.DataFrame, *, max_gap_days: int = MAX_GAP_DAYS) -> pd.DataFrame:
    """Periods each kind's auctions cover (``COVERAGE_COLUMNS``), split where none was seen for ``max_gap_days``.

    A period runs from its first auction date to its last. A kind with no
    rows has no period. Sources are named ``auctions:<kind>``; G-secs, green
    bonds and FRBs share ``auctions:gsec`` (they come from the same files).
    """
    rows = []
    if auctions is None or not len(auctions):
        return pd.DataFrame(columns=list(COVERAGE_COLUMNS))
    group = auctions["kind"].map(lambda k: "sdl" if k == SDL else ("tbill" if k == TBILL else "gsec"))
    for name, dates in auctions.groupby(group)["auction_date"]:
        days = sorted(set(pd.to_datetime(dates).dt.normalize()))
        start = prev = days[0]
        for d in days[1:]:
            if (d - prev).days > max_gap_days:
                rows.append({"source": f"auctions:{name}", "start": start, "end": prev,
                             "detail": f"next auction seen {d:%Y-%m-%d}, {(d - prev).days} days later"})
                start = d
            prev = d
        rows.append({"source": f"auctions:{name}", "start": start, "end": prev, "detail": "last auction seen"})
    return pd.DataFrame(rows, columns=list(COVERAGE_COLUMNS))


def coverage(ledger: pd.DataFrame) -> pd.DataFrame:
    """The coverage table of a ledger (``ledger.attrs['coverage']``); empty if it has none."""
    cov = ledger.attrs.get("coverage")
    return cov if isinstance(cov, pd.DataFrame) else pd.DataFrame(columns=list(COVERAGE_COLUMNS))


def _covered(cov: pd.DataFrame, source: str, start: pd.Timestamp, end: pd.Timestamp) -> bool:
    """True if one period of ``source`` holds every day from ``start`` (exclusive) to ``end`` (inclusive)."""
    if start is pd.NaT or end is pd.NaT:
        return False
    lo, hi = min(start, end), max(start, end)
    rows = cov[cov["source"] == source]
    return any(r.start <= lo + pd.Timedelta(days=1) and hi <= r.end for r in rows.itertuples()) or lo == hi


def _coverage_source(kind: str) -> str:
    return "auctions:sdl" if kind == SDL else ("auctions:tbill" if kind == TBILL else "auctions:gsec")


# --- Ledger -----------------------------------------------------------------------------------------

def build_ledger(auctions: pd.DataFrame, switches: pd.DataFrame | None = None, *,
                 buybacks: pd.DataFrame | None = None, snapshots: pd.DataFrame | None = None,
                 master: pd.DataFrame | None = None, calendar: BusinessCalendar | None = None,
                 shortfall: str = "devolved", competitive_only: str = "notified", operations_from=None,
                 max_gap_days: int = MAX_GAP_DAYS) -> pd.DataFrame:
    """The ledger (``LEDGER_COLUMNS``) from an auction table, switches, buybacks and RBI snapshots.

    ``auctions``: ``AuctionBuild.auctions`` (or the CSV ``cr-strat auctions``
    writes, read with ``auctions.read_auctions``). ``switches``:
    ``auctions.normalise_switches``. ``buybacks``: a frame with ``date``,
    ``amount_cr`` and ``isin`` or ``security`` (optional ``source``).
    ``snapshots``: RBI's GoI-outstanding lists (``as_of``, ``isin``,
    ``outstanding_cr``), used for the maturity amounts. ``master``: gives the
    maturity of a key known only from switches or buybacks. ``shortfall``
    and ``competitive_only``: see the module docstring.
    ``operations_from``: the date from which every switch and buyback release
    is in the inputs. The coverage table is in ``attrs['coverage']``. Raises
    ``ValueError`` for a row that cannot be dated.
    """
    if shortfall not in ("accepted", "devolved"):
        raise ValueError("shortfall must be 'devolved' or 'accepted'")
    if competitive_only not in ("accepted", "notified"):
        raise ValueError("competitive_only must be 'notified' or 'accepted'")
    calendar = mumbai_settlement_calendar() if calendar is None else calendar
    rows: list[dict] = []
    for _, a in auctions.iterrows():
        if not bool(a["counts_as_issue"]):
            continue
        kind = a["kind"]
        maturity = _ts(a["maturity_date"])
        if kind == TBILL:
            if maturity is pd.NaT:
                continue
            key = f"{int(a['tenor_days'])}DTB {maturity:%Y-%m-%d}"
        else:
            key = _key(a["isin"], a["name_key"], a["security"])
        amount, basis, notes = _issued_amount(a, shortfall, competitive_only)
        event = "issue" if a["issue_type"] == "new" else "reissue"
        if _missing(a["issue_type"]):
            notes.append("issue type unknown")
        auction_day = _ts(a["auction_date"])
        date, date_source = _ts(a["settlement_date"]), a["settlement_source"]
        if date is pd.NaT:
            if auction_day is pd.NaT:
                raise ValueError(f"auction row {a['auction_id']!r} ({a['security']}) has neither a settlement nor "
                                 "an auction date")
            date, date_source = _assumed_t1(auction_day, calendar), "assumed_t1"
            notes.append("no settlement date in the auction table: T+1 of the auction assumed")
        rows.append({
            "key": key, "isin": a["isin"], "name_key": a["name_key"], "security": a["security"], "kind": kind,
            "event": event, "date": date, "date_source": date_source,
            "amount_cr": amount, "amount_basis": basis, "auction_date": auction_day,
            "auction_id": a["auction_id"], "maturity_date": maturity, "maturity_source": a["maturity_source"],
            "coupon_pct": a["coupon_pct"], "maturity_year": a["maturity_year"], "state_code": a["state_code"],
            "source": a["sources"], "source_file": a["source_file"], "source_row": a["source_row"],
            "note": "; ".join(notes),
        })
    rows.extend(_switch_rows(switches, calendar))
    rows.extend(_buyback_rows(buybacks))
    ledger = pd.DataFrame(rows, columns=list(LEDGER_COLUMNS))
    ledger = _fill_key_fields(ledger, master)
    known_until = _inputs_end(auctions, ledger, snapshots)
    ledger = pd.concat([ledger, _maturity_rows(ledger, snapshots, calendar, known_until)], ignore_index=True)
    ledger = _typed(ledger)
    ledger = ledger.sort_values(["date", "key", "event"], kind="stable").reset_index(drop=True)
    cov = auction_coverage(auctions, max_gap_days=max_gap_days)  # every auction seen, rejected ones too
    if operations_from is not None:
        end = max([ledger["date"].max(), pd.Timestamp(operations_from)]) if len(ledger) else pd.Timestamp(
            operations_from)
        cov = pd.concat([cov, pd.DataFrame([{"source": "operations", "start": pd.Timestamp(operations_from),
                                             "end": end, "detail": "switch and buyback releases complete"}])],
                        ignore_index=True)
    ledger.attrs["coverage"] = cov
    return ledger


def _issued_amount(a, shortfall: str, competitive_only: str) -> tuple[float, str, list[str]]:
    """What an auction row adds to the stock, its ``amount_basis`` and notes (the module docstring's rules)."""
    amount, basis, notes = float(a["accepted_cr"]), a["accepted_basis"], []
    flags = str(a["flags"] or "").split(";")
    notified = None if _missing(a["notified_cr"]) else float(a["notified_cr"])
    if notified is None:
        return amount, basis, notes
    if "shortfall" in flags:
        if shortfall == "devolved":
            notes.append(f"accepted {amount:g} of {notified:g} notified: the rest taken as devolved")
            amount, basis = notified, "notified_assumed_devolved"
        return amount, basis, notes
    if basis == "competitive_only" and competitive_only == "notified" and a["kind"] in GOI_KINDS:
        if (1 - NONCOMPETITIVE_MAX) * notified - 1e-6 <= amount <= notified + 1e-6:
            notes.append(f"competitive {amount:g} of {notified:g} notified: the rest taken as non-competitive "
                         "allotment")
            amount, basis = notified, "notified_assumed_full"
        elif amount > notified:
            notes.append("greenshoe: the non-competitive allotment is not known; competitive amount only")
    return amount, basis, notes


def _switch_rows(switches: pd.DataFrame | None, calendar: BusinessCalendar | None = None) -> list[dict]:
    rows = []
    if switches is None or not len(switches):
        return rows
    for _, s in switches.iterrows():
        legs = (("switch_out", "source", -1.0, "source_accepted_cr"),
                ("switch_in", "destination", 1.0, "destination_issued_cr"))
        date, date_source = _ts(s["settlement_date"]), s["settlement_source"]
        note = f"switch {s['source_security']} to {s['destination_security']}"
        if date is pd.NaT:
            if _ts(s["auction_date"]) is pd.NaT:
                raise ValueError(f"switch {s['auction_id']!r} has neither a settlement nor an auction date")
            date, date_source = _assumed_t1(_ts(s["auction_date"]), calendar), "assumed_t1"
            note += "; no settlement date in the switch table: T+1 of the auction assumed"
        for event, leg, sign, amount_col in legs:
            amount = s[amount_col]
            if _missing(amount) or float(amount) == 0:
                continue
            name = s[f"{leg}_security"]
            parsed = parse_security_name(name)
            rows.append({
                "key": _key(s[f"{leg}_isin"], s[f"{leg}_key"], name), "isin": s[f"{leg}_isin"],
                "name_key": s[f"{leg}_key"], "security": name, "kind": parsed.kind, "event": event,
                "date": date, "date_source": date_source,
                "amount_cr": sign * float(amount), "amount_basis": "face_value", "auction_date": _ts(s["auction_date"]),
                "auction_id": s["auction_id"], "maturity_date": pd.NaT, "maturity_source": "",
                "coupon_pct": parsed.coupon_pct, "maturity_year": parsed.maturity_year, "state_code": None,
                "source": "rbi_switch", "source_file": s["source_file"], "source_row": s["source_row"],
                "note": note,
            })
    return rows


def _buyback_rows(buybacks: pd.DataFrame | None) -> list[dict]:
    rows = []
    if buybacks is None or not len(buybacks):
        return rows
    missing = [c for c in ("date", "amount_cr") if c not in buybacks]
    if missing or not ({"isin", "security"} & set(buybacks.columns)):
        raise ValueError(f"buybacks need date, amount_cr and isin or security (missing {missing})")
    for i, b in buybacks.iterrows():
        name = b.get("security") if "security" in buybacks else None
        parsed = parse_security_name(name) if not _missing(name) else parse_security_name("")
        isin = b.get("isin") if "isin" in buybacks and not _missing(b.get("isin")) else None
        if _ts(b["date"]) is pd.NaT:
            raise ValueError(f"buyback row {i} ({name or isin}) has no date")
        rows.append({
            "key": _key(isin, parsed.key, name), "isin": isin, "name_key": parsed.key, "security": name or isin,
            "kind": parsed.kind, "event": "buyback", "date": _ts(b["date"]), "date_source": "buyback_table",
            "amount_cr": -abs(float(b["amount_cr"])), "amount_basis": "face_value", "auction_date": _ts(b["date"]),
            "auction_id": "", "maturity_date": pd.NaT, "maturity_source": "", "coupon_pct": parsed.coupon_pct,
            "maturity_year": parsed.maturity_year, "state_code": parsed.state_code,
            "source": str(b.get("source", "buyback_table")) if "source" in buybacks else "buyback_table",
            "source_file": "", "source_row": "", "note": "",
        })
    return rows


def _fill_key_fields(ledger: pd.DataFrame, master: pd.DataFrame | None = None) -> pd.DataFrame:
    """Give every row of a key the key's maturity, kind and coupon (switch rows lack them)."""
    if not len(ledger):
        return ledger
    out = ledger.copy()
    if master is not None and len(master):
        by_isin = master.drop_duplicates("isin").set_index("isin")["maturity_date"]
        blank = out["maturity_date"].isna() & out["isin"].isin(by_isin.index)
        out.loc[blank, "maturity_date"] = pd.to_datetime(out.loc[blank, "isin"].map(by_isin)).to_numpy()
        out.loc[blank & out["maturity_date"].notna(), "maturity_source"] = "master"
    for col in ("maturity_date", "maturity_source", "kind", "coupon_pct", "maturity_year", "state_code"):
        known = out.dropna(subset=[col])
        known = known[known[col].astype(str) != ""]
        if not len(known):
            continue  # no row knows it: nothing to spread
        first = known.groupby("key")[col].first()
        fill = out["key"].map(first)
        blank = out[col].isna() | (out[col].astype(str) == "")
        out.loc[blank, col] = fill[blank]
    return out


def _inputs_end(auctions: pd.DataFrame, ledger: pd.DataFrame, snapshots: pd.DataFrame | None) -> pd.Timestamp:
    """The last date the inputs speak for: the latest auction, event or RBI list (never the run date)."""
    dates = [pd.to_datetime(auctions["auction_date"]).max() if auctions is not None and len(auctions) else pd.NaT,
             ledger["auction_date"].max() if len(ledger) else pd.NaT,
             ledger["date"].max() if len(ledger) else pd.NaT]
    snap = _snapshot_frame(snapshots)
    if len(snap):
        dates.append(snap["as_of"].max())
    dates = [d for d in dates if not _missing(d)]
    return max(dates) if dates else pd.NaT


def _maturity_rows(ledger: pd.DataFrame, snapshots: pd.DataFrame | None, calendar: BusinessCalendar | None,
                   known_until: pd.Timestamp = pd.NaT) -> pd.DataFrame:
    """One maturity event per key with a maturity date or year: minus the stock just before redemption.

    A key with no maturity date but a maturity year is redeemed on the last
    business day of that year (``date_source = 'name_year'``, a placeholder).
    """
    snap = _snapshot_frame(snapshots)
    rows = []
    keys = ledger.groupby("key", sort=True) if len(ledger) else []
    for key, grp in keys:
        maturity = _ts(grp["maturity_date"].dropna().iloc[0]) if grp["maturity_date"].notna().any() else pd.NaT
        years = pd.to_numeric(grp["maturity_year"], errors="coerce").dropna()
        note = ""
        if maturity is not pd.NaT:
            paid, date_source = _redemption_day(maturity, calendar)
        elif len(years):
            year = int(years.iloc[0])
            paid, date_source = _year_end(year, calendar), "name_year"
            note = (f"maturity date unknown: redeemed by the end of {year}, the year in its name "
                    f"({paid:%Y-%m-%d} is a placeholder); ")
        else:
            continue
        amount, basis, stock_note = _stock_before(grp, snap, key, paid, known_until)
        head = grp.iloc[0]
        rows.append({
            "key": key, "isin": head["isin"], "name_key": head["name_key"], "security": head["security"],
            "kind": head["kind"], "event": "maturity", "date": paid, "date_source": date_source,
            "amount_cr": -amount, "amount_basis": basis, "auction_date": pd.NaT, "auction_id": "",
            "maturity_date": maturity, "maturity_source": head["maturity_source"] if maturity is not pd.NaT else "",
            "coupon_pct": head["coupon_pct"], "maturity_year": head["maturity_year"], "state_code": head["state_code"],
            "source": "maturity", "source_file": "", "source_row": "", "note": note + stock_note,
        })
    return pd.DataFrame(rows, columns=list(LEDGER_COLUMNS))


def _stock_before(grp: pd.DataFrame, snap: pd.DataFrame, key: str, paid: pd.Timestamp,
                  known_until: pd.Timestamp = pd.NaT) -> tuple[float, str, str]:
    flows = grp[(grp["event"] != "maturity") & (grp["date"] < paid)]
    held = snap[(snap["isin"] == key) & (snap["as_of"] < paid)] if len(snap) else snap
    scheduled = "; scheduled (later issuance not known)" if known_until is not pd.NaT and paid > known_until else ""
    if len(held):
        latest = held.sort_values("as_of").iloc[-1]
        after = flows[flows["date"] > latest["as_of"]]["amount_cr"].sum()
        note = f"RBI list of {latest['as_of']:%Y-%m-%d} plus later events" + scheduled
        return float(latest["outstanding_cr"]) + float(after), "snapshot", note
    note = "sum of the ledger's earlier events"
    if not (flows["event"] == "issue").any():
        note += "; incomplete: the new issue is not in the ledger"
    return float(flows["amount_cr"].sum()), "forward_sum", note + scheduled


def _snapshot_frame(snapshots: pd.DataFrame | None) -> pd.DataFrame:
    if snapshots is None or not len(snapshots):
        return pd.DataFrame(columns=["as_of", "isin", "outstanding_cr"])
    missing = [c for c in ("as_of", "isin", "outstanding_cr") if c not in snapshots]
    if missing:
        raise ValueError(f"snapshot frame lacks {missing}: use sovereign.load_goi_outstanding frames")
    out = pd.DataFrame({"as_of": pd.to_datetime(snapshots["as_of"]).dt.normalize(),
                        "isin": snapshots["isin"].astype(str).str.upper(),
                        "outstanding_cr": pd.to_numeric(snapshots["outstanding_cr"])})
    for col in ("name", "issue_date", "maturity_date"):
        if col in snapshots:
            out[col] = snapshots[col].to_numpy()
    return out


def _typed(ledger: pd.DataFrame) -> pd.DataFrame:
    out = ledger.copy()
    for col in ("date", "auction_date", "maturity_date"):
        out[col] = pd.to_datetime(out[col])
    for col in ("amount_cr", "coupon_pct"):
        out[col] = pd.to_numeric(out[col]).astype(float)
    out["maturity_year"] = pd.to_numeric(out["maturity_year"]).astype("Int64")
    return out


# --- Reading and writing ----------------------------------------------------------------------------

def coverage_path(path: str | Path) -> Path:
    """Where a ledger CSV's coverage table lives: ``<name>_coverage.csv`` beside it."""
    p = Path(path)
    return p.with_name(f"{p.stem}_coverage.csv")


def read_ledger(path: str | Path) -> pd.DataFrame:
    """A ledger CSV written by ``cr-strat auctions``, typed, with its coverage table in ``attrs``."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False).replace({"": None})
    missing = [c for c in LEDGER_COLUMNS if c not in df]
    if missing:
        raise ValueError(f"{path}: not a ledger (missing columns {missing})")
    df = _typed(df)
    cov_file = coverage_path(path)
    if cov_file.exists():
        cov = pd.read_csv(cov_file, dtype=str, keep_default_na=False)
        for col in ("start", "end"):
            cov[col] = pd.to_datetime(cov[col])
        df.attrs["coverage"] = cov
    else:
        df.attrs["coverage"] = pd.DataFrame(columns=list(COVERAGE_COLUMNS))
    return df


def read_snapshots(path: str | Path) -> pd.DataFrame:
    """GoI-outstanding snapshots from a CSV (``cr-strat sovereign-master --snapshots``) or a saved list page."""
    p = Path(path)
    if p.suffix.lower() in (".htm", ".html"):
        from cr_strat.sovereign import load_goi_outstanding

        return load_goi_outstanding(p)
    df = pd.read_csv(p, dtype=str, keep_default_na=False).replace({"": None})
    for col in ("as_of", "issue_date", "maturity_date"):
        if col in df:
            df[col] = pd.to_datetime(df[col])
    df["outstanding_cr"] = pd.to_numeric(df["outstanding_cr"])
    return df


# --- Outstanding ------------------------------------------------------------------------------------

def outstanding_on(ledger: pd.DataFrame, date, snapshot: pd.DataFrame | None = None, *,
                   include_zero: bool = False) -> pd.DataFrame:
    """Outstanding stock of every key at the end of ``date`` (``OUTSTANDING_COLUMNS``).

    A key in ``snapshot`` (RBI lists, one or several dates) is reconstructed
    from the list nearest ``date``; any other key is summed forward from its
    first event. A key is zero from its redemption day, the date of its
    maturity event in the ledger (else its maturity date): ``method =
    'matured'``, or ``'matured_date_unknown'`` when that event is a
    ``name_year`` placeholder. Keys that are zero on ``date`` are left out
    unless ``include_zero``.
    """
    t = _ts(date)
    if t is pd.NaT:
        raise ValueError("date is required")
    cov = coverage(ledger)
    snap = _snapshot_frame(snapshot)
    flows = ledger[ledger["event"] != "maturity"]
    info = _key_info(ledger, snap)
    rows = []
    for key, k in info.items():
        events = flows[flows["key"] == key]
        first_event = events["date"].min() if len(events) else pd.NaT
        reasons: list[str] = []
        maturity = k["maturity_date"]
        redeemed = k["redeemed"] if k["redeemed"] is not pd.NaT else maturity
        source = _coverage_source(k["kind"])
        held = snap[snap["isin"] == key] if len(snap) else snap
        if redeemed is not pd.NaT and redeemed <= t:
            value, anchor = 0.0, redeemed
            method = "matured_date_unknown" if k["placeholder"] else "matured"
            if k["placeholder"]:
                reasons.append(f"maturity date unknown: redeemed by the end of {k['maturity_year']}, "
                               "the year in its name")
            issuance_ok = operations_ok = True
        elif len(held):
            nearest = held.assign(dist=(held["as_of"] - t).abs())
            pick = nearest.sort_values(["dist", "as_of"], ascending=[True, False]).iloc[0]
            anchor = pick["as_of"]
            lo, hi = min(t, anchor), max(t, anchor)
            between = events[(events["date"] > lo) & (events["date"] <= hi)]["amount_cr"].sum()
            if t <= anchor:
                value, method = float(pick["outstanding_cr"]) - between, "backward"
            else:
                value, method = float(pick["outstanding_cr"]) + between, "forward_from_snapshot"
            issue = k["issue_date"]
            if issue is not pd.NaT and t < issue:
                value, method = 0.0, "before_issue"
            issuance_ok = method == "before_issue" or _covered(cov, source, t, anchor)
            operations_ok = method == "before_issue" or _covered(cov, "operations", t, anchor)
            if not issuance_ok:
                reasons.append(f"auctions between {min(t, anchor):%Y-%m-%d} and {max(t, anchor):%Y-%m-%d} "
                               "are not all in the inputs")
        else:
            past = events[events["date"] <= t]
            value, method, anchor = float(past["amount_cr"].sum()), "forward", first_event
            has_issue = (events["event"] == "issue").any()
            if first_event is pd.NaT or t < first_event:
                issuance_ok = operations_ok = True
            else:
                issuance_ok = bool(has_issue) and _covered(cov, source, first_event, t)
                operations_ok = _covered(cov, "operations", first_event, t)
                if not has_issue:
                    reasons.append("first issued before the first auction file: earlier issuance missing")
                elif not _covered(cov, source, first_event, t):
                    reasons.append(f"auctions between {first_event:%Y-%m-%d} and {t:%Y-%m-%d} "
                                   "are not all in the inputs")
        if k["placeholder"] and method not in MATURED_METHODS and t.year == k["maturity_year"]:
            reasons.append(f"matures on an unknown day of {t.year}: the stock may already be redeemed")
        if not operations_ok:
            reasons.append("switches and buybacks not covered")
        if abs(value) < 1e-9 and not include_zero:
            continue
        rows.append({
            "key": key, "isin": k["isin"], "name_key": k["name_key"], "security": k["security"], "kind": k["kind"],
            "maturity_date": maturity, "tenor_bucket": tenor_bucket(maturity, t), "date": t,
            "outstanding_cr": round(value, 6), "method": method, "anchor_date": anchor,
            "first_event_date": first_event, "issuance_complete": bool(issuance_ok),
            "operations_complete": bool(operations_ok), "reasons": "; ".join(reasons),
        })
    out = pd.DataFrame(rows, columns=list(OUTSTANDING_COLUMNS))
    for col in ("maturity_date", "date", "anchor_date", "first_event_date"):
        out[col] = pd.to_datetime(out[col])
    return out.sort_values(["kind", "maturity_date", "key"], kind="stable").reset_index(drop=True)


def _key_info(ledger: pd.DataFrame, snap: pd.DataFrame) -> dict[str, dict]:
    """Per key: names, kind, maturity date and year, first issue, and the ledger's redemption day."""
    info: dict[str, dict] = {}
    for key, grp in ledger.groupby("key", sort=True):
        head = grp.iloc[0]
        mats = grp["maturity_date"].dropna()
        years = pd.to_numeric(grp["maturity_year"], errors="coerce").dropna()
        redemption = grp[grp["event"] == "maturity"]
        info[key] = {"isin": head["isin"], "name_key": head["name_key"], "security": head["security"],
                     "kind": head["kind"], "maturity_date": _ts(mats.iloc[0]) if len(mats) else pd.NaT,
                     "maturity_year": int(years.iloc[0]) if len(years) else None, "issue_date": pd.NaT,
                     "redeemed": redemption["date"].min() if len(redemption) else pd.NaT,
                     "placeholder": bool((redemption["date_source"] == "name_year").any())}
    if len(snap):
        latest = snap.sort_values("as_of").groupby("isin").tail(1)
        for _, s in latest.iterrows():
            k = info.get(s["isin"])
            name = s.get("name") if "name" in snap else None
            parsed = parse_security_name(name) if name else None
            if k is None:
                k = info[s["isin"]] = {"isin": s["isin"], "name_key": parsed.key if parsed else None,
                                       "security": name, "kind": parsed.kind if parsed else "gsec",
                                       "maturity_date": pd.NaT, "maturity_year": None, "issue_date": pd.NaT,
                                       "redeemed": pd.NaT, "placeholder": False}
            if "maturity_date" in snap and k["maturity_date"] is pd.NaT:
                k["maturity_date"] = _ts(s["maturity_date"])
            if "issue_date" in snap:
                k["issue_date"] = _ts(s["issue_date"])
    for k in info.values():
        if k["placeholder"] and k["maturity_date"] is not pd.NaT:  # RBI's list dates what the ledger could not
            k["redeemed"], k["placeholder"] = pd.NaT, False
    return info


def reconcile(ledger: pd.DataFrame, snapshot: pd.DataFrame) -> pd.DataFrame:
    """The ledger's forward sum against RBI's list, ISIN by ISIN, on the list's date (``RECONCILE_COLUMNS``).

    ``snapshot`` is one RBI list (one ``as_of``; the latest is used if there
    are several). Every ISIN in the list is shown, and every central
    government security (the list's universe: no SDLs or T-bills) the ledger
    still holds on that date (``rbi_cr`` blank: a security the list does not
    have). ``difference_cr`` is ledger minus RBI.
    """
    snap = _snapshot_frame(snapshot)
    if not len(snap):
        raise ValueError("reconcile needs an RBI snapshot")
    as_of = snap["as_of"].max()
    snap = snap[snap["as_of"] == as_of]
    mine = outstanding_on(ledger, as_of, include_zero=True).set_index("key")
    flows = ledger[ledger["event"] != "maturity"]
    rows = []
    central = [k for k in mine.index if mine.loc[k, "kind"] in CENTRAL_KINDS and mine.loc[k, "kind"] != TBILL]
    keys = list(dict.fromkeys([*snap["isin"], *central]))
    for key in keys:
        listed = snap[snap["isin"] == key]
        rbi = float(listed["outstanding_cr"].iloc[0]) if len(listed) else None
        if key in mine.index:
            m = mine.loc[key]
            ledger_cr, kind, security = float(m["outstanding_cr"]), m["kind"], m["security"]
            issuance_ok, operations_ok, reasons = m["issuance_complete"], m["operations_complete"], m["reasons"]
            if m["method"] in MATURED_METHODS:
                if rbi is None:
                    continue
                reasons = "the ledger has it matured, RBI still lists it"
        else:
            ledger_cr, kind, security = 0.0, "gsec", listed["name"].iloc[0] if "name" in listed else key
            issuance_ok, operations_ok, reasons = False, False, "no event in the ledger"
        if rbi is None and abs(ledger_cr) < 1e-9:
            continue
        ev = flows[flows["key"] == key]
        first_issue = listed["issue_date"].iloc[0] if len(listed) and "issue_date" in listed else pd.NaT
        diff = None if rbi is None else ledger_cr - rbi
        rows.append({
            "isin": key if len(listed) or str(key).startswith("IN") else None, "key": key, "security": security,
            "kind": kind, "as_of": as_of, "rbi_cr": rbi, "ledger_cr": round(ledger_cr, 3),
            "difference_cr": None if diff is None else round(diff, 3),
            "difference_pct": None if diff is None or not rbi else round(100 * diff / rbi, 3),
            "first_issue_date": _ts(first_issue), "first_event": ev["event"].iloc[0] if len(ev) else "",
            "events": len(ev), "issuance_complete": bool(issuance_ok), "operations_complete": bool(operations_ok),
            "reasons": reasons if rbi is not None else "the ledger holds it but RBI's list does not",
        })
    out = pd.DataFrame(rows, columns=list(RECONCILE_COLUMNS))
    out["first_issue_date"] = pd.to_datetime(out["first_issue_date"])
    return out.sort_values(["kind", "key"], kind="stable").reset_index(drop=True)


# --- Summaries --------------------------------------------------------------------------------------

def supply_summary(ledger: pd.DataFrame, *, freq: str = "M", by_bucket: bool = True,
                   buckets=TENOR_BUCKETS, start=None, end=None) -> pd.DataFrame:
    """Gross issuance, switches, buybacks, redemptions and net supply per period, kind and tenor bucket.

    ``freq`` is a pandas period alias (``M`` month, ``Q`` quarter, ``W``
    week). The tenor bucket is the residual maturity on each event's date
    (``unknown`` without a maturity date). Amounts are ₹ crore of face
    value; redemptions and switches out are negative.
    """
    df = ledger.copy()
    if start is not None:
        df = df[df["date"] >= _ts(start)]
    if end is not None:
        df = df[df["date"] <= _ts(end)]
    if not len(df):
        return pd.DataFrame(columns=list(SUPPLY_COLUMNS))
    df["period"] = df["date"].dt.to_period(freq).astype(str)
    df["tenor_bucket"] = [tenor_bucket(m, d, buckets) for m, d in zip(df["maturity_date"], df["date"])] \
        if by_bucket else "all"
    columns = {"gross_issuance_cr": ISSUANCE_EVENTS, "switch_in_cr": ("switch_in",),
               "switch_out_cr": ("switch_out",), "buyback_cr": ("buyback",), "redemption_cr": ("maturity",)}
    for col, events in columns.items():
        df[col] = df["amount_cr"].where(df["event"].isin(events), 0.0)
    out = (df.groupby(["period", "kind", "tenor_bucket"], as_index=False)
           .agg(**{c: (c, "sum") for c in columns}, events=("event", "size")))
    out["net_cr"] = out[list(columns)].sum(axis=1)
    return out.loc[:, list(SUPPLY_COLUMNS)].sort_values(["period", "kind", "tenor_bucket"]).reset_index(drop=True)


def outstanding_by_bucket(outstanding: pd.DataFrame) -> pd.DataFrame:
    """Stock by kind and tenor bucket from ``outstanding_on``'s frame, with how much of it is complete."""
    if not len(outstanding):
        return pd.DataFrame(columns=["kind", "tenor_bucket", "outstanding_cr", "complete_cr", "securities"])
    df = outstanding.copy()
    df["complete_cr"] = df["outstanding_cr"].where(df["issuance_complete"] & df["operations_complete"], 0.0)
    return (df.groupby(["kind", "tenor_bucket"], as_index=False)
            .agg(outstanding_cr=("outstanding_cr", "sum"), complete_cr=("complete_cr", "sum"),
                 securities=("key", "size")))


def stock_history(ledger: pd.DataFrame, dates: Iterable, snapshot: pd.DataFrame | None = None) -> pd.DataFrame:
    """``outstanding_on`` for several dates, stacked."""
    frames = [outstanding_on(ledger, d, snapshot) for d in dates]
    frames = [f for f in frames if len(f)]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=list(OUTSTANDING_COLUMNS))
