"""The S4 event ledger: every auction, switch, buyback, open market and liquidity operation, issuance calendar, MPC
meeting, macro release and index event, each with the time from which it was public; supply measures; event windows.

Strategy S4 of ``docs/rates_data_sources.md`` trades the supply and RBI-operation cycle. It needs one table of what
happened, when it became known and how much duration it put into the market, and a way to read prices around each
event. This module builds that table from what the repository already holds; it reads no file of its own beyond the
frames it is given, fetches nothing and reads no clock. The rules are those of part B of the return-engine and
event-ledger specification of 19 Nov 2026 (its section numbers in brackets); a rule no source confirms is a named
parameter marked UNVERIFIED here and in ``docs/analytics.md``.

**Rows** (B7.1, ``EVENT_COLUMNS``). One row per event and security: ``event_id`` (``<kind>:<event_date>:<key>``, unique;
a new issue's key part is coupon-free, ``new GS 2036`` or ``new KA SGS 2034``, since its coupon is its cut-off yield:
``new_issue_key``), ``kind`` (``EVENT_KINDS``), the security (ISIN, the issuance ledger's key, name, state), its tenor
bucket (``issuance.tenor_bucket`` on the settlement date, as ``issuance.supply_summary`` buckets it; a calendar line by
its nominal tenor, as the RBI store buckets it), amounts in rupees crore of face value, the auction's figures, the DV01
it added, the time from which its announcement and its result were public and how each time was found, the settlement
date, the sources, and flags and notes in two pairs: ``flags`` and ``notes`` known at the announcement, ``result_flags``
and ``result_notes`` that the result decides (a shortfall, a rejection, a greenshoe, the ledger's devolvement note, the
amounts accepted, the MPC's new rate). Builders, one per source:

==========================  ===========================================================================================
``auction_events``          the auction table (``auctions.build_auctions``) joined to the issuance ledger by
                            ``auction_id``: ``booked_cr`` is the ledger's amount (shortfalls booked as devolved,
                            competitive-only amounts at the notified amount), ``accepted_cr`` the source's; bids,
                            weighted averages and greenshoes from RBI's result tables (``rbi_releases``, or the RBI
                            store's ``tables/``), bids and devolvement from Handbook Table 221; announcement times
                            from the announcement tables; underwriting results as rows of their own (the commission
                            cut-off); announced auctions not yet in the auction table, and results not in it, as rows
                            flagged ``announced_only`` and ``not_in_ledger``
``switch_events``           the ledger's ``switch_in`` and ``switch_out`` rows, timed and priced from the store's
                            ``switch_operations`` or the switch table (``auctions.normalise_switches``)
``buyback_events``          the ledger's ``buyback`` rows (a table kept by hand: no times)
``redemption_events``       the ledger's ``maturity`` rows (negative amounts)
``operation_events``        the store's ``omo_operations`` (one row per security) and ``liquidity_operations``
                            (variable rate repo and reverse repo), with their announcement and result times
``calendar_events``         the store's ``supply_calendar``: the GoI calendar's lines by week and tenor, the state
                            calendar's quarter totals; a calendar revised by a later release is a row of that release;
                            T-bill calendar lines from a table of the user's (``read_calendar_lines``: the store has
                            no parser for RBI's T-bill calendar)
``policy_events``           MPC decisions and minutes from ``policy_path.load_mpc_calendar``, the repo rate after
                            each decision from a policy table (``rbi_store``'s hand-kept one, or ``macro_store``'s
                            ``policy_rates_bis``)
``macro_events``            MoSPI's releases (``mospi.ReleaseCalendar``: its table's dates, else each family's rule)
``index_event_rows``        index inclusion events from a table kept by hand (``global_sources``, ``macro_store``)
==========================  ===========================================================================================

``collect_events`` runs them all on whatever inputs are given; ``build_event_ledger`` joins parts, checks the ids and
sorts: a row repeated in one input table is refused naming the table, and two auction lines of one security on one day
under different auction ids are flagged ``duplicate_line``, with the security's redemption (``supply_table`` leaves
the numbered copy out of the supply, the redemption and the stock, so identity E1 shows the issuance ledger's double
count; ``input_gaps`` lists them). ``events_as_of(events, t)`` gives what was
known at ``t``: rows whose announcement (or, lacking one, result) was public by then, with whatever the result decides
blank until the result was: ``RESULT_FIELDS``, the result's flags and notes, and a new issue's coupon, ISIN, auction id
and source reference (its key and name shown coupon-free).

**Times** (B6, A5.1). India time without a zone, as the stores write it. ``announcement_basis`` and ``result_basis`` say
how each time was found (``TIME_BASES``) and ``announcement_estimated`` / ``result_estimated`` whether it is a rule
rather than a print. A result's time is the store's feed time of its release when the store saw it in the feed
(``feed``), else 23:59:59 on the release's date (``release_date``: a saved page), else 23:59:59 on the auction date
(``end_of_day``: FIMMDA's files and the Handbook print no times; UNVERIFIED and conservative: a 20:00 decision sees the
result the next day). ``result_rule="rbi_fixed_time"`` (``RBI_FIXED_TIMES``) uses RBI's fixed result times where no feed
time was seen: T-bills from 26 Aug 2016 and from 14 Jan 2020, GoI dated securities from 26 Aug 2016 to 13 Jan 2020, each
at the later of the two times the search summaries give (partly verified: RBI's circular of 25 Aug 2016 and press
release of 13 Jan 2020 were read only through search summaries; the start days are UNVERIFIED); SDLs and dated
securities since 2020 keep the observed rule. An announcement the store did not see is estimated:
``announcement_rule="conservative"`` (default) 18:00 on the business day before the auction (UNVERIFIED); ``"typical"``
18:00 on the Monday of the auction week for GoI dated auctions and the Friday before for SDLs and T-bills (partly
verified: three releases of Nov 2026). Times of the derived store tables are the store's. MPC decisions are public at
the meetings file's ``decision_time`` where it gives one, else at their era's time (``MPC_DECISION_TIMES``: 11:00 before
the MPC, 14:30 from its first decision on 4 Oct 2016, 11:45 from Apr 2019, 10:00 from 2024, through search summaries;
Dec 2019 to 2023 not found, taken at 14:30 so a decision is never seen early, UNVERIFIED), their minutes at 23:59:59 on
the 14th day after (RBI Act section 45ZL through a search summary; time UNVERIFIED); a meeting's date from the day its
schedule was announced. MoSPI's times come from its release table, else its rules (``mospi``). A redemption's date is
public from its security's first auction in the inputs, its amount from the last auction, switch or buyback before it.

**DV01** (B7.3). ``dv01_cr_per_bp = booked_cr x dv01_per100 / 100`` (crore per basis point; ``dv01_per100`` from
``bonds`` at the cut-off yield for the settlement date: ``FixedRateBond`` for G-secs, SDLs and green bonds with an odd
first coupon from the issue date, a new issue at par with its coupon equal to the cut-off yield, ``DiscountInstrument``
for T-bills and CMBs). A row without a cut-off yield is priced at its weighted average yield where a result gives one,
else at its coupon (FIMMDA's SDL reissues: ``dv01_basis="coupon"``, flagged). FRBs have no pricer and no DV01
(``no_pricer``, counted in the supply table's ``dv01_missing_cr``). A switch's destination legs are priced at their
cut-off; its source legs, whose yield the result does not print, at the latest yield of the security known before the
switch (an auction, OMO or switch cut-off; else its coupon; UNVERIFIED). Redemptions carry no DV01 (a bond at maturity
has none).

**Supply** (B7.3, ``supply_table``, ``SUPPLY_COLUMNS``). Per period (default ``auction_week``, the Monday-Friday week
of the auction date, the GoI calendar's unit; ``settlement_month`` reproduces ``issuance.supply_summary``), kind and
tenor bucket: gross face and DV01 at the ledger's booked amounts, switches in and out, buybacks, open market purchases
and sales (face and DV01), redemptions, net supply (``net_cr`` as ``supply_summary`` computes it, gross plus switches,
buybacks and redemptions; ``market_net_cr`` also less OMO purchases plus OMO sales), and the stock outstanding at the
end of the day before the period starts with its DV01 (each security's face times its DV01 at its latest cut-off yield
known by then, else its coupon: ``stock_dv01_basis="last_cutoff"``, UNVERIFIED), and the shares ``gross / stock`` and
``dv01 / stock_dv01``, blank unless every security of the cell has a complete history (``issuance``'s
``issuance_complete`` and ``operations_complete``). ``stock_on_dates`` gives ``issuance.outstanding_on`` for many dates
at once by the same rules (checked against it in the tests; ``outstanding_on`` takes seconds per date on a full
ledger). ``check_supply`` tests identity E1 cell by cell against ``issuance.supply_summary``; ``check_ledger_join`` E2
(every ledger issuance row maps to exactly one auction row, and back).

**Auction metrics** (``auction_metrics``, ``METRIC_COLUMNS``): bid-cover (competitive bids over the notified amount,
UNVERIFIED reading; and all bids), the tail in yield (cut-off less the weighted average yield: zero or positive when an
auction ran by its rules; an event row's ``tail_bp`` is signed the same way for every kind, the weighted average less
the cut-off where RBI buys or lends, OMO purchases and repos) and in price, the devolvement share (reported or
inferred), the non-competitive share, the underwriting commission; a greenshoe from the result, else for T-bills the
competitive amount accepted above the notified one (non-competitive bids sit outside it), else the amount accepted
above it; with a price panel (licensed), the concession to the previous business day's market yield
(for a new issue the curve's par yield at its maturity), and the residual's move into and out of the auction.

**Forward supply** (``forward_supply``): on each date, at the decision time, what the calendars public by then plan for
the auction weeks from that date's own week within a horizon (a later release replaces an earlier one for the weeks it
covers), the auctions announced by then and not yet resulted, and beside them the realised supply of those weeks (known
only later; flagged where the auction files do not cover the period) and the gaps, in face and in DV01 at the trailing
DV01 per crore of the bucket's auctions public by then. Auctions are compared in the bucket a calendar line of their
tenor would hold (a new issue by its nominal tenor), since a new N-year issue can land a bucket up on residual years.
A week and kind no calendar of the inputs plans (T-bills, unless their calendar's lines are given as a table of the
user's, since RBI's T-bill calendar has no parser; SDLs, whose calendar gives quarter totals) has ``planned_cr`` and the
gaps blank, noted, not zero.

**Event windows** (B8). Day 0 is the first business day whose decision time (20:00, UNVERIFIED) is at or after the
anchor's time (``decision_day``, the rule ``event_windows`` uses) (``result`` by default, which for a calendar line is
its release and for a redemption its own day; ``announcement``; ``event_date``; ``settlement``); offsets count Mumbai
business days from there. ``overlaps`` lists for each event the other events whose day 0 falls in its window (siblings,
the other lines of the same event, marked: the securities of one GoI auction with its green bonds, FRBs and
underwriting, of one SDL or T-bill auction, the legs of one switch, one calendar's lines), ``isolate`` flags events with
none within a radius, and ``attach_panel`` joins any later (date, ISIN) panel to the windows: the returns module's
marks, ``price_panel``'s rows, or ``curvefit``'s residual history (dated by settlement: ``panel_dates="settlement"``),
the nearest observation within a tolerance on the same side of day 0 as ``event_prices`` does, never interpolated, a
missing day left missing and counted.

**Limits.** No secondary-market price is free, so concessions, recoveries and price paths are built but run on
synthetic panels only. The ledger's gaps are the event ledger's (``input_gaps`` lists them): FIMMDA's G-sec files
lack FY2024-25 and June to November 2026, its SDL file covers January 2024 to January 2025, there is no history of
switches, buybacks or T-bill auctions before the RBI store's first poll, and every announcement and result time before
that poll is a rule. The stock is incomplete wherever a security was issued before the first auction file, so the
shares are blank on the free data. The state calendar has quarter totals only. MPC meetings ship for FY2026-27 only.
RBI grants no reuse licence and FIMMDA states no terms: ask both before keeping a history
(``docs/rates_data_sources.md``).
"""

from __future__ import annotations

import datetime as dt
import math
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

from cr_strat import global_sources as gs
from cr_strat import mospi
from cr_strat.bonds import DiscountInstrument, FixedRateBond
from cr_strat.calendars import BusinessCalendar, CalendarCoverageError, mumbai_settlement_calendar
from cr_strat.conventions import CMB as CMB_CONVENTIONS
from cr_strat.conventions import GSEC as GSEC_CONVENTIONS
from cr_strat.conventions import SDL as SDL_CONVENTIONS
from cr_strat.conventions import TBILL as TBILL_CONVENTIONS
from cr_strat.issuance import ISSUANCE_EVENTS, TENOR_BUCKETS, coverage, supply_summary, tenor_bucket
from cr_strat.macro_base import SourceError, add_months, fy_name
from cr_strat.policy_path import check_policy_changes, policy_rate_on, read_policy_table
from cr_strat.rbi_store import nominal_tenor_bucket
from cr_strat.sovereign import (
    CMB,
    FRB,
    GREEN,
    GSEC,
    SDL,
    TBILL,
    match_isin,
    normalise_name,
    parse_security_name,
    with_par_coupon,
)

# --- Vocabulary ---------------------------------------------------------------------------------------------------

AUCTION_KINDS = {GSEC: "gsec_auction", GREEN: "green_auction", FRB: "frb_auction", SDL: "sdl_auction",
                 TBILL: "tbill_auction", CMB: "cmb_auction"}
AUCTION_EVENT_KINDS = tuple(AUCTION_KINDS.values())
CALENDAR_KINDS = ("gsec_calendar", "tbill_calendar", "sdl_calendar")
# calendars whose lines plan a week and a tenor (the state calendar gives quarter totals); RBI's T-bill calendar has no
# parser in the RBI store, so its lines come from a table of the user's (read_calendar_lines)
WEEKLY_CALENDAR_KINDS = ("gsec_calendar", "tbill_calendar")
# a user's table of calendar lines (read_calendar_lines): the columns it must or may hold
CALENDAR_LINE_COLUMNS = ("source", "kind", "period_start", "period_end", "tenor_years", "tenor_days", "tenor_bucket",
                         "amount_cr", "release_id", "line", "public_from")
EVENT_KINDS = (*AUCTION_EVENT_KINDS, "underwriting", "switch_out", "switch_in", "buyback", "omo_purchase", "omo_sale",
               "vrr", "vrrr", *CALENDAR_KINDS, "mpc_decision", "mpc_minutes", "macro_release", "index_event",
               "redemption")
# the kinds that change the stock or move it between holders (supply_table's columns)
SUPPLY_EVENT_KINDS = (*AUCTION_EVENT_KINDS, "switch_in", "switch_out", "buyback", "omo_purchase", "omo_sale",
                      "redemption")

EVENT_COLUMNS = (
    "event_id", "kind", "subkind", "event_date", "isin", "key", "security", "state_code", "supply_kind",
    "tenor_bucket", "maturity_date", "coupon_pct", "tenor_days", "period_start", "period_end", "notified_cr",
    "booked_cr", "accepted_cr", "amount_basis", "greenshoe_cr", "nc_accepted_cr", "devolvement_cr",
    "devolvement_inferred_cr", "bids_cr", "nc_bids_cr", "bid_cover", "cutoff_yield", "cutoff_price", "way", "wap",
    "tail_bp", "commission_paise", "dv01_per100", "dv01_cr_per_bp", "dv01_basis", "liquidity_effect_cr", "rate_pct",
    "announcement_public_from", "announcement_basis", "announcement_estimated", "result_public_from", "result_basis",
    "result_estimated", "settlement_date", "reversal_date", "auction_id", "source", "source_ref", "flags", "notes",
    "result_flags", "result_notes",
)
DATE_FIELDS = ("event_date", "maturity_date", "period_start", "period_end", "settlement_date", "reversal_date")
TIME_FIELDS = ("announcement_public_from", "result_public_from")
FLOAT_FIELDS = (
    "coupon_pct", "notified_cr", "booked_cr", "accepted_cr", "greenshoe_cr", "nc_accepted_cr", "devolvement_cr",
    "devolvement_inferred_cr", "bids_cr", "nc_bids_cr", "bid_cover", "cutoff_yield", "cutoff_price", "way", "wap",
    "tail_bp", "commission_paise", "dv01_per100", "dv01_cr_per_bp", "liquidity_effect_cr", "rate_pct",
)
BOOL_FIELDS = ("announcement_estimated", "result_estimated")
INT_FIELDS = ("tenor_days",)
# what an event's result says: blank in events_as_of until result_public_from (flags and notes the result decides are
# kept apart in result_flags and result_notes; a new issue's coupon, and the names that carry it, are masked too)
RESULT_FIELDS = (
    "booked_cr", "accepted_cr", "amount_basis", "greenshoe_cr", "nc_accepted_cr", "devolvement_cr",
    "devolvement_inferred_cr", "bids_cr", "nc_bids_cr", "bid_cover", "cutoff_yield", "cutoff_price", "way", "wap",
    "tail_bp", "commission_paise", "dv01_per100", "dv01_cr_per_bp", "dv01_basis", "liquidity_effect_cr", "rate_pct",
    "result_flags", "result_notes",
)
# new issues of these kinds take their coupon (the cut-off yield) from the result: their ids are coupon-free
COUPON_AUCTION_KINDS = ("gsec_auction", "green_auction", "sdl_auction", "frb_auction")
NEW_ISSUE_HIDDEN = ("coupon_pct", "isin", "auction_id", "source_ref")

# How a time was found: (a rule rather than a print, what it means). Spec B6, B7.1.
TIME_BASES: dict[str, tuple[bool, str]] = {
    "feed": (False, "the RBI store saw the release in RBI's feed at this time"),
    "release_date": (True, "23:59:59 on the release's date: the store read a saved page, not the feed"),
    "end_of_day": (True, "23:59:59 on the event date: no release time is known (UNVERIFIED, conservative)"),
    "fixed_time": (True, "RBI's fixed result times (circular of 25 Aug 2016, press release of 13 Jan 2020: search "
                         "summaries only, partly verified)"),
    "estimated": (True, "18:00 on the business day before the event (UNVERIFIED, conservative)"),
    "typical": (True, "18:00 on the Monday of the auction week (GoI dated) or the Friday before (SDL, T-bill); partly "
                      "verified on three releases of Nov 2026"),
    "schedule": (True, "23:59:59 on the day the meeting's date was announced (the MPC schedule's release, or the day "
                       "an unscheduled meeting was called)"),
    "decision_time": (True, "the MPC's announcement time in the decision's era (MPC_DECISION_TIMES: 11:00 before 4 Oct "
                            "2016, 14:30 from then, 11:45 from Apr 2019, 14:30 from Dec 2019 to 2023 where it is not "
                            "known, 10:00 from 2024; search summaries, partly verified), or "
                            "EventSettings.mpc_decision_time"),
    "meeting_time": (True, "the decision_time the meetings file gives for the meeting (the user's)"),
    "user_table": (True, "the public_from of the user's table of calendar lines (a date alone: 23:59:59 on it)"),
    "minutes_rule": (True, "23:59:59 on the 14th day after the decision (RBI Act section 45ZL through a search "
                           "summary; the time UNVERIFIED)"),
    "release_table": (False, "MoSPI's release table: the actual date at its stated release time"),
    "release_table_planned": (True, "MoSPI's planned date (moved to the next working day) at its stated time"),
    "release_rule": (True, "MoSPI's release rule for the family (mospi.rule_release)"),
    "publication_rule": (True, "23:59:59 in the provider's zone on the announcement date (global_sources)"),
    "announced_time": (False, "the announcement time kept in the index-event table"),
    "since_issue": (True, "a redemption's date: 23:59:59 on the day of its security's first auction in the inputs"),
    "last_change": (True, "a redemption's amount: 23:59:59 on the day of the last auction, switch or buyback before "
                          "it"),
    "store": (False, "the time the RBI store's derived table gives (feed time, or the end of the release date)"),
    "none": (True, "not known"),
}

# RBI's fixed result times (result_rule="rbi_fixed_time"): (kind group, first day, last day, time). Partly verified:
# the circular of 25 Aug 2016 (results at a stipulated time: T-bills 13:30, GoI dated securities 14:00, else 14:00
# and 14:30 or half-hourly after) and the press release of 13 Jan 2020 (prid 49133: T-bills 15:00, else 15:30; the
# dated-security and SDL times were not readable) were read only through search summaries. The later, fallback time
# of each is used, from the day after each document (UNVERIFIED start days).
RBI_FIXED_TIMES = (
    ("tbill", dt.date(2016, 8, 26), dt.date(2020, 1, 13), dt.time(14, 0)),
    ("dated", dt.date(2016, 8, 26), dt.date(2020, 1, 13), dt.time(14, 30)),
    ("tbill", dt.date(2020, 1, 14), None, dt.time(15, 30)),
)

# When an MPC decision was public on its day, by era, for meetings whose file gives no decision_time: (first day, last
# day, time, status). Read through web-search summaries only: before the MPC the policy was announced at 11:00; the
# first MPC decision (4 Oct 2016) at 14:30 (press reports); RBI's releases of Apr, Aug and Oct 2019 put the resolution
# on its website at 11:45; 10:00 in 2024-2026. When the 14:30 era ended (between Feb and Apr 2019) and what the time
# was from Dec 2019 to 2023 were not found: those spans take the latest time seen, 14:30, so that a decision is never
# visible before it was public (UNVERIFIED; a meetings file's decision_time column overrides).
MPC_DECISION_TIMES = (
    (None, dt.date(2016, 10, 3), dt.time(11, 0), "P"),
    (dt.date(2016, 10, 4), dt.date(2019, 3, 31), dt.time(14, 30), "P"),
    (dt.date(2019, 4, 1), dt.date(2019, 11, 30), dt.time(11, 45), "P"),
    (dt.date(2019, 12, 1), dt.date(2023, 12, 31), dt.time(14, 30), "U"),
    (dt.date(2024, 1, 1), None, dt.time(10, 0), "P"),
)


def mpc_decision_time(day) -> dt.time:
    """The time an MPC decision of ``day`` was public by the dated rule ``MPC_DECISION_TIMES``."""
    d = _day(day).date()
    for first, last, time, _ in MPC_DECISION_TIMES:
        if (first is None or first <= d) and (last is None or d <= last):
            return time
    raise ValueError(f"no MPC decision time covers {d}")


ANNOUNCEMENT_RULES = ("conservative", "typical")
RESULT_RULES = ("observed_or_end_of_day", "rbi_fixed_time")
PERIODS = ("auction_week", "auction_month", "settlement_week", "settlement_month")
BID_COVER_BASES = ("competitive", "all")
STOCK_DV01_BASES = ("last_cutoff", "coupon")
ANCHORS = ("result", "announcement", "event_date", "settlement")
PANEL_DATES = ("trade", "settlement")


@dataclass(frozen=True)
class EventSettings:
    """The event ledger's rules (spec B6-B8); every default no source settles is UNVERIFIED (``docs/analytics.md``).

    ``announcement_rule``: how an announcement the store did not see is dated (``ANNOUNCEMENT_RULES``; default
    ``conservative``, 18:00 on the business day before the auction, UNVERIFIED). ``result_rule``:
    ``observed_or_end_of_day`` (default) or ``rbi_fixed_time`` (``RBI_FIXED_TIMES``, partly verified).
    ``decision_time``: when a back-test decides each business day, which sets day 0 of a window (20:00, UNVERIFIED,
    as the return engine). ``announcement_time``:
    the time of day of an estimated announcement (18:00, UNVERIFIED). ``period``: the supply table's period
    (``PERIODS``). ``bid_cover``: ``competitive`` bids over the notified amount (UNVERIFIED reading) or ``all`` bids.
    ``stock_dv01_basis``: the stock's DV01 at each security's latest cut-off yield known by then (``last_cutoff``,
    UNVERIFIED) or at its coupon. ``mpc_decision_time``: ``None`` (default) times each decision by its era
    (``MPC_DECISION_TIMES``, partly verified) unless the meetings frame gives a ``decision_time``; a time given
    here applies to every meeting. ``minutes_days``: 14
    (RBI Act section 45ZL, partly verified). ``dv01_weeks``: the trailing auction weeks whose DV01 per crore prices a
    planned amount in ``forward_supply`` (13, UNVERIFIED). ``metric_days``: the business days before and after an
    auction over which ``auction_metrics`` measures the residual's move (5, UNVERIFIED).
    """

    announcement_rule: str = "conservative"
    result_rule: str = "observed_or_end_of_day"
    decision_time: dt.time = dt.time(20, 0)
    announcement_time: dt.time = dt.time(18, 0)
    period: str = "auction_week"
    bid_cover: str = "competitive"
    stock_dv01_basis: str = "last_cutoff"
    mpc_decision_time: dt.time | None = None
    minutes_days: int = 14
    dv01_weeks: int = 13
    metric_days: int = 5

    def __post_init__(self) -> None:
        for name, allowed in (("announcement_rule", ANNOUNCEMENT_RULES), ("result_rule", RESULT_RULES),
                              ("period", PERIODS), ("bid_cover", BID_COVER_BASES),
                              ("stock_dv01_basis", STOCK_DV01_BASES)):
            value = getattr(self, name)
            if value not in allowed:
                raise ValueError(f"EventSettings.{name} must be one of {', '.join(allowed)}, not {value!r}")
        for name in ("decision_time", "announcement_time", "mpc_decision_time"):
            value = getattr(self, name)
            if not isinstance(value, dt.time) and not (name == "mpc_decision_time" and value is None):
                raise TypeError(f"EventSettings.{name} must be a datetime.time, not {value!r}")
        for name, low in (("minutes_days", 0), ("dv01_weeks", 1), ("metric_days", 1)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < low:
                raise ValueError(f"EventSettings.{name} must be a whole number of at least {low}, not {value!r}")


DEFAULT_SETTINGS = EventSettings()


# --- Small helpers ------------------------------------------------------------------------------------------------

def _missing(value) -> bool:
    if value is None or (isinstance(value, str) and not value.strip()):
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _text(value) -> str:
    return "" if _missing(value) else str(value).strip()


def _num(value) -> float:
    if _missing(value):
        return math.nan
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def _day(value) -> pd.Timestamp:
    """A date as a normalised Timestamp, or NaT."""
    if _missing(value):
        return pd.NaT
    try:
        return pd.Timestamp(value).normalize()
    except (TypeError, ValueError):
        return pd.NaT


def _when(value) -> pd.Timestamp:
    """A time (India time, no zone) as a Timestamp, or NaT."""
    if _missing(value):
        return pd.NaT
    try:
        out = pd.Timestamp(value)
    except (TypeError, ValueError):
        return pd.NaT
    return out.tz_localize(None) if out.tzinfo is not None else out


def _at(day, time: dt.time) -> pd.Timestamp:
    return pd.Timestamp(dt.datetime.combine(_day(day).date(), time))


def _end_of_day(day) -> pd.Timestamp:
    return _at(day, dt.time(23, 59, 59))


def _iso(value) -> str:
    d = _day(value)
    return "" if d is pd.NaT else f"{d:%Y-%m-%d}"


_RELEASE_REF = re.compile(r"release (\d+) line (\d+)")


def _release_ref(text) -> tuple[int, int] | None:
    """``(release_id, line)`` from a ``source_row`` such as ``goi_full_63970.html:release 63970 line 1``."""
    m = _RELEASE_REF.search(_text(text))
    return (int(m.group(1)), int(m.group(2))) if m else None


def _shift(calendar: BusinessCalendar, day, n: int) -> tuple[dt.date, bool]:
    """``calendar.add_business_days(day, n)``; on its lenient copy beyond its years (``True`` then)."""
    d = _day(day).date()
    try:
        return calendar.add_business_days(d, n), False
    except CalendarCoverageError:
        return calendar.lenient().add_business_days(d, n), True


def _is_business_day(calendar: BusinessCalendar, day) -> tuple[bool, bool]:
    d = _day(day).date()
    try:
        return calendar.is_business_day(d), False
    except CalendarCoverageError:
        return calendar.lenient().is_business_day(d), True


def _month_end(text: str) -> pd.Timestamp:
    """The last day of a ``YYYY-MM`` month (an index event known to the month only)."""
    return (pd.Period(text, freq="M").end_time).normalize()


def _monday(day) -> pd.Timestamp:
    d = _day(day)
    return d - pd.Timedelta(days=d.weekday())


def _join(items: Iterable[str]) -> str:
    return "; ".join(dict.fromkeys(i for i in items if i))


def _flags(*parts) -> str:
    out: list[str] = []
    for part in parts:
        for flag in (part if isinstance(part, (list, tuple)) else _text(part).split(";")):
            flag = flag.strip()
            if flag and flag not in out:
                out.append(flag)
    return ";".join(out)


def _need(frame: pd.DataFrame | None, columns: Iterable[str], what: str) -> None:
    if frame is None:
        return
    if not isinstance(frame, pd.DataFrame):
        raise TypeError(f"{what} must be a DataFrame, not {type(frame).__name__}")
    missing = [c for c in columns if c not in frame.columns]
    if missing:
        raise ValueError(f"{what} lacks the columns {missing}")


def _refuse_repeats(frame: pd.DataFrame, what: str, describe: Sequence[str]) -> None:
    """Refuse a row given twice in one input table (every cell equal), naming the table and the row: one auction
    or operation is not held twice, and summing both would double it."""
    if frame is None or not len(frame):
        return
    text = frame.map(_sort_text)
    twice = text.duplicated(keep=False)
    if twice.any():
        rec = frame[twice].iloc[0]
        lines = [int(i) + 2 for i in frame.index[twice][:3]]
        label = ", ".join(f"{c} {_text(rec[c]) if not isinstance(rec[c], pd.Timestamp) else _iso(rec[c])}"
                          for c in describe if c in frame)
        raise ValueError(f"{what} repeats a row ({label}; rows {lines}): drop the repeated row")


def _sorted(frame: pd.DataFrame, columns: Sequence[str]) -> pd.DataFrame:
    """``frame`` in a fixed order of ``columns`` (text keys), whatever order its rows came in."""
    if not len(frame):
        return frame.reset_index(drop=True)
    keys = pd.DataFrame({f"_k{i}": frame[c].map(_sort_text) for i, c in enumerate(columns)}, index=frame.index)
    order = keys.sort_values(list(keys.columns), kind="mergesort").index
    return frame.loc[order].reset_index(drop=True)


def _sort_text(value) -> str:
    if _missing(value):
        return ""
    if isinstance(value, (pd.Timestamp, dt.datetime, dt.date)):
        return pd.Timestamp(value).isoformat()
    if isinstance(value, float):
        return f"{value:030.10f}" if value >= 0 else f"-{-value:029.10f}"
    if isinstance(value, (int, np.integer)):
        return f"{int(value):020d}" if value >= 0 else f"-{-int(value):019d}"
    return str(value)


# --- DV01 ---------------------------------------------------------------------------------------------------------

def line_dv01(kind: str, coupon, maturity, yield_pct, settlement, *, issue_date=None) -> tuple[float, str]:
    """DV01 per 100 face (the fall in price for a one basis point rise) of one auction line, from ``bonds``.

    G-secs, green bonds and SDLs by ``FixedRateBond`` on their conventions (a coupon left blank, a new issue at par,
    takes the yield: status ``par_coupon``; ``issue_date`` on or before the settlement gives an odd first coupon its
    length); T-bills and CMBs by ``DiscountInstrument``. Returns ``(dv01, status)``: ``ok``, ``par_coupon``, or why
    there is none (``no_pricer`` for FRBs and other kinds, ``no_maturity``, ``no_settlement``, ``no_yield``,
    ``matured``, ``error: ...`` from ``bonds``) with NaN.
    """
    if kind not in (GSEC, GREEN, SDL, TBILL, CMB):
        return math.nan, "no_pricer"
    m, s, y = _day(maturity), _day(settlement), _num(yield_pct)
    if m is pd.NaT:
        return math.nan, "no_maturity"
    if s is pd.NaT:
        return math.nan, "no_settlement"
    if not math.isfinite(y):
        return math.nan, "no_yield"
    if s >= m:
        return math.nan, "matured"
    try:
        if kind in (TBILL, CMB):
            inst = DiscountInstrument(m.date(), TBILL_CONVENTIONS if kind == TBILL else CMB_CONVENTIONS)
            return float(inst.dv01(y, s.date())), "ok"
        c, status = _num(coupon), "ok"
        if not math.isfinite(c):
            c, status = y, "par_coupon"
        issue = _day(issue_date)
        bond = FixedRateBond(c, m.date(), SDL_CONVENTIONS if kind == SDL else GSEC_CONVENTIONS,
                             issue_date=issue.date() if issue is not pd.NaT and issue <= s else None)
        return float(bond.dv01(y, s.date())), status
    except (ValueError, NotImplementedError) as exc:
        return math.nan, f"error: {exc}"


# --- Times --------------------------------------------------------------------------------------------------------

def _release_time(published, release_date) -> tuple[pd.Timestamp, str]:
    """A release's time: its feed time if seen, else 23:59:59 on its release date (the store's rule)."""
    when = _when(published)
    if when is not pd.NaT:
        return when, "feed"
    if _day(release_date) is not pd.NaT:
        return _end_of_day(release_date), "release_date"
    return pd.NaT, "none"


def _release_index(releases: pd.DataFrame | None) -> dict[int, tuple[pd.Timestamp, str, str]]:
    """release id -> (time, basis, kind) from a release index (the store's ``releases.csv`` or ``rbi-releases``')."""
    out: dict[int, tuple[pd.Timestamp, str, str]] = {}
    if releases is None or not len(releases):
        return out
    _need(releases, ("release_id", "release_date"), "the release index")
    published = releases["published"] if "published" in releases else pd.Series(None, index=releases.index)
    kinds = releases["kind"] if "kind" in releases else pd.Series("", index=releases.index)
    for rid, pub, day, kind in zip(releases["release_id"], published, releases["release_date"], kinds):
        if _missing(rid):
            continue
        when, basis = _release_time(pub, day)
        out[int(rid)] = (when, basis, _text(kind))
    return out


def _fixed_time(kind: str, day: pd.Timestamp) -> dt.time | None:
    group = "tbill" if kind in (TBILL, CMB) else ("dated" if kind in (GSEC, GREEN, FRB) else "")
    d = day.date()
    for g, first, last, time in RBI_FIXED_TIMES:
        if g == group and first <= d and (last is None or d <= last):
            return time
    return None


def _result_time(kind: str, auction_day: pd.Timestamp, observed: tuple[pd.Timestamp, str] | None,
                 settings: EventSettings) -> tuple[pd.Timestamp, str]:
    """When an auction's result was public: a feed time seen; else RBI's fixed time (if asked and known); else the
    release's date at 23:59:59; else 23:59:59 on the auction date."""
    if observed is not None and observed[1] == "feed":
        return observed
    if settings.result_rule == "rbi_fixed_time" and auction_day is not pd.NaT:
        fixed = _fixed_time(kind, auction_day)
        if fixed is not None:
            return _at(auction_day, fixed), "fixed_time"
    if observed is not None and observed[1] == "release_date":
        return observed
    if auction_day is pd.NaT:
        return pd.NaT, "none"
    return _end_of_day(auction_day), "end_of_day"


def estimated_announcement(kind: str, auction_day, calendar: BusinessCalendar,
                           settings: EventSettings = DEFAULT_SETTINGS) -> tuple[pd.Timestamp, str, bool]:
    """When an auction's announcement is taken as public when the store did not see it: ``(time, basis, projected)``.

    ``conservative``: ``announcement_time`` (18:00) on the business day before the auction (UNVERIFIED).
    ``typical``: 18:00 on the Monday of the auction week for GoI dated auctions, the Friday before for SDLs and
    T-bills, moved to the next business day when that day is a holiday but never past the business day before the
    auction (partly verified). ``projected`` is ``True`` when the calendar's holiday list does not cover the days used.
    """
    day = _day(auction_day)
    if day is pd.NaT:
        return pd.NaT, "none", False
    before, projected = _shift(calendar, day, -1)
    if settings.announcement_rule == "conservative":
        return _at(before, settings.announcement_time), "estimated", projected
    if kind in (GSEC, GREEN, FRB):
        target = _monday(day)
    else:
        target = day - pd.Timedelta(days=(day.weekday() - 4) % 7 or 7)  # the Friday before
    ok, more = _is_business_day(calendar, target)
    if not ok:
        nxt, more2 = _shift(calendar, target, 1)
        target, more = pd.Timestamp(nxt), more or more2
    if target.date() > before:
        target = pd.Timestamp(before)
    return _at(target, settings.announcement_time), "typical", projected or more


def _day0_index(days: np.ndarray, when: pd.Timestamp, decision_time: dt.time) -> int:
    """The position in ``days`` (sorted business days, ``datetime64[D]``) of day 0 for an anchor at ``when``: the first
    business day whose decision time is at or after ``when`` (B8). ``len(days)`` when beyond them."""
    i = int(np.searchsorted(days, np.datetime64(when.date(), "D"), side="left"))
    if i < len(days) and _at(pd.Timestamp(days[i]), decision_time) < when:
        i += 1
    return i


def decision_day(when, calendar: BusinessCalendar, decision_time: dt.time = dt.time(20, 0)) -> tuple[dt.date, bool]:
    """Day 0 of a window: the first business day whose ``decision_time`` is at or after ``when`` (B8), by the rule
    ``event_windows`` uses.

    Returns ``(day, projected)``: ``projected`` when the calendar's holiday list does not cover the days used (its
    lenient copy was used). Raises ``ValueError`` without a time.
    """
    t = _when(when)
    if t is pd.NaT:
        raise ValueError("decision_day needs a time")
    d = t.normalize()
    bdays = _BusinessDays(calendar, d - pd.Timedelta(days=1), d + pd.Timedelta(days=31), True)
    i = _day0_index(bdays.days, t, decision_time)
    if i >= len(bdays.days):
        raise ValueError(f"no business day within a month after {t}")
    day = pd.Timestamp(bdays.days[i]).date()
    projected = not bool(bdays.covered[i]) or not calendar.covers(d.date())
    return day, projected


# --- Rows ---------------------------------------------------------------------------------------------------------

def _event(**fields) -> dict:
    row = dict.fromkeys(EVENT_COLUMNS)
    unknown = set(fields) - set(EVENT_COLUMNS)
    if unknown:
        raise KeyError(f"not event columns: {sorted(unknown)}")
    row.update(fields)
    for side in ("announcement", "result"):
        basis = row.get(f"{side}_basis") or "none"
        row[f"{side}_basis"] = basis
        if row.get(f"{side}_estimated") is None:
            row[f"{side}_estimated"] = None if _when(row.get(f"{side}_public_from")) is pd.NaT else TIME_BASES[basis][0]
    return row


def empty_events() -> pd.DataFrame:
    """An event frame with no rows, typed as the builders type theirs."""
    return _events_frame([])


def _events_frame(rows: list[dict]) -> pd.DataFrame:
    out = pd.DataFrame(rows, columns=list(EVENT_COLUMNS))
    for c in DATE_FIELDS:
        out[c] = pd.to_datetime(out[c].map(_day)).astype("datetime64[ns]")
    for c in TIME_FIELDS:
        out[c] = pd.to_datetime(out[c].map(_when)).astype("datetime64[ns]")
    for c in FLOAT_FIELDS:
        out[c] = pd.to_numeric(out[c].map(_num)).astype(float)
    for c in INT_FIELDS:
        out[c] = pd.to_numeric(out[c].map(_num)).round().astype("Int64")
    for c in BOOL_FIELDS:
        out[c] = out[c].map(lambda v: None if _missing(v) else bool(v)).astype("boolean")
    for c in EVENT_COLUMNS:
        if c not in (*DATE_FIELDS, *TIME_FIELDS, *FLOAT_FIELDS, *INT_FIELDS, *BOOL_FIELDS):
            out[c] = out[c].map(_text).astype(object)
    return out


def _event_id(kind: str, day, key: str) -> str:
    return f"{kind}:{_iso(day)}:{key}"


# --- Auctions -----------------------------------------------------------------------------------------------------

_AUCTION_NEEDS = ("auction_id", "auction_date", "kind", "security", "name_key", "coupon_pct", "state_code",
                  "tenor_days", "isin", "maturity_date", "first_issue_date", "settlement_date", "notified_cr",
                  "nc_accepted_cr", "devolvement_cr", "accepted_cr", "cutoff_price", "cutoff_yield", "issue_type",
                  "status", "counts_as_issue", "flags", "sources", "source_row")
_LEDGER_NEEDS = ("key", "isin", "security", "kind", "event", "date", "amount_cr", "amount_basis", "auction_date",
                 "auction_id", "maturity_date", "coupon_pct", "state_code", "source", "source_row", "note")


def _table(tables: Mapping[str, pd.DataFrame] | None, name: str) -> pd.DataFrame | None:
    if tables is None:
        return None
    frame = tables.get(name)
    return frame if isinstance(frame, pd.DataFrame) and len(frame) else None


def _row_time(rec: Mapping, rel: dict) -> tuple[pd.Timestamp, str]:
    rid = _num(rec.get("release_id"))
    if math.isfinite(rid) and int(rid) in rel and rel[int(rid)][0] is not pd.NaT:
        return rel[int(rid)][0], rel[int(rid)][1]
    return _release_time(rec.get("published"), rec.get("release_date"))


def _rank(rec: Mapping) -> tuple[float, float]:
    rid, line = _num(rec.get("release_id")), _num(rec.get("line"))
    return (rid if math.isfinite(rid) else -1.0, line if math.isfinite(line) else -1.0)


def _goi_key(rec: Mapping) -> str:
    parsed = with_par_coupon(parse_security_name(rec.get("security")), _num(rec.get("cutoff_price")),
                             _num(rec.get("cutoff_yield")))
    return parsed.key or _text(rec.get("name_key"))


def _tenor_years(maturity, start) -> int | None:
    m, s = _day(maturity), _day(start)
    if m is pd.NaT or s is pd.NaT:
        return None
    years = math.floor((m - s).days / 365.25 + 0.5)
    return years if years >= 1 else None


def _result_index(tables, rel: dict) -> dict[tuple, dict]:
    """RBI's full auction results by (segment, auction date, key); the latest release wins."""
    out: dict[tuple, dict] = {}
    for name in ("gsec_result", "tbill_result", "sdl_result"):
        frame = _table(tables, name)
        if frame is None:
            continue
        for rec in sorted(frame.to_dict("records"), key=_rank):
            day = _day(rec.get("auction_date"))
            if name == "tbill_result":
                tenor = _num(rec.get("tenor_days"))
                key = ("tbill", day, int(tenor) if math.isfinite(tenor) else None)
            elif name == "gsec_result":
                key = ("goi", day, _goi_key(rec))
            else:
                key = ("sdl", day, _text(rec.get("name_key")))
            out[key] = dict(rec, _time=_row_time(rec, rel), _table=name)
    return out


def _announcement_index(tables, rel: dict) -> tuple[dict[tuple, dict], list[dict]]:
    """Auction announcements by every key a result or an auction row may look them up with, and all of them.

    A key two lines of one auction day share is ambiguous and joins nothing (``auctions`` reports such keys).
    """
    index: dict[tuple, dict | None] = {}
    every: list[dict] = []

    def put(key, rec):
        if key is None or any(part is None or part == "" for part in key[2:]):
            return
        if key in index and index[key] is not None and index[key]["_id"] != rec["_id"]:
            index[key] = None  # ambiguous
        elif key not in index:
            index[key] = rec

    for name in ("gsec_announcement", "tbill_announcement", "sdl_announcement"):
        frame = _table(tables, name)
        if frame is None:
            continue
        for rec in sorted(frame.to_dict("records"), key=_rank):
            day = _day(rec.get("auction_date"))
            rec = dict(rec, _time=_row_time(rec, rel), _table=name, _id=(name, *_rank(rec)))
            every.append(rec)
            if name == "gsec_announcement":
                parsed = parse_security_name(rec.get("security"))
                put(("goi", day, _text(rec.get("name_key")) or parsed.key), rec)
                put(("goi_name", day, normalise_name(rec.get("security"))), rec)
                if parsed.new_issue or (parsed.coupon_pct is None and _text(rec.get("security")).lower()
                                        .startswith("new ")):
                    put(("goi_new", day, parsed.maturity_year), rec)
            elif name == "tbill_announcement":
                tenor = _num(rec.get("tenor_days"))
                put(("tbill", day, int(tenor) if math.isfinite(tenor) else None), rec)
            else:
                if _text(rec.get("issue_type")) == "new":
                    tenor = _num(rec.get("tenor_years"))
                    put(("sdl_new", day, _text(rec.get("state_code")), int(tenor) if math.isfinite(tenor) else None),
                        rec)
                else:
                    put(("sdl", day, _text(rec.get("name_key"))), rec)
    return {k: v for k, v in index.items() if v is not None}, every


def _announcement_for(index: dict, segment: str, day, *, name_key: str, security: str, new: bool,
                      maturity_year=None, tenor_days=None, state_code: str = "", tenor_years=None) -> dict | None:
    if segment == "tbill":
        return index.get(("tbill", day, tenor_days))
    if segment == "sdl":
        if new:
            return index.get(("sdl_new", day, state_code, tenor_years)) or index.get(("sdl", day, name_key))
        return index.get(("sdl", day, name_key))
    found = index.get(("goi", day, name_key)) or index.get(("goi_name", day, normalise_name(security)))
    if found is None and new and maturity_year is not None:
        found = index.get(("goi_new", day, int(maturity_year)))
    return found


def _segment(kind: str) -> str:
    return "tbill" if kind in (TBILL, CMB) else ("sdl" if kind == SDL else "goi")


def _tail(cutoff: float, average: float, *, rbi_buys: bool = False) -> float:
    """The tail in bp, rounded to 8 decimals (the inputs print 4), signed to be zero or positive when the auction ran
    as its rules say: the distance from the weighted average to the cut-off, the marginal accepted bid or offer.
    Where RBI sells or borrows (auctions, OMO sales, reverse repos) the cut-off is the highest yield or rate accepted:
    ``100 (cut-off - weighted average)``; where RBI buys or lends (OMO purchases, repos) it accepts the cheapest offers
    or highest rates first and the cut-off is the lowest yield or rate accepted: ``100 (weighted average - cut-off)``
    (``rbi_buys``). A negative tail flags a result whose figures do not fit its rules."""
    if not (math.isfinite(cutoff) and math.isfinite(average)):
        return math.nan
    return round(100 * ((average - cutoff) if rbi_buys else (cutoff - average)), 8)


def _bid_cover(bids: float, nc_bids: float, notified: float, settings: EventSettings) -> float:
    if not (math.isfinite(bids) and math.isfinite(notified) and notified > 0):
        return math.nan
    if settings.bid_cover == "all":
        return (bids + (nc_bids if math.isfinite(nc_bids) else 0.0)) / notified
    return bids / notified


def _greenshoe(result: Mapping | None, accepted: float, notified: float, kind: str = GSEC) -> float:
    """The greenshoe exercised (crore): the result's own figure; for T-bills and CMBs the competitive amount accepted
    above the notified one (non-competitive bids are allotted outside the notified amount, so the total says nothing
    of a greenshoe; NaN when the result prints no competitive amount); else the amount accepted above the notified one
    (a dated or SDL auction's non-competitive bids sit inside the notified amount)."""
    given = _num(result.get("greenshoe_cr")) if result else math.nan
    if math.isfinite(given):
        return given
    if kind in (TBILL, CMB):
        competitive = _num(result.get("comp_accepted_cr")) if result else math.nan
        if math.isfinite(competitive) and math.isfinite(notified):
            return max(0.0, competitive - notified)
        return math.nan
    if math.isfinite(accepted) and math.isfinite(notified) and accepted > notified + 1e-6:
        return accepted - notified
    return math.nan


def _dv01_fields(kind: str, coupon, maturity, settlement, booked: float, *, cutoff, way, issue_date=None
                 ) -> dict:
    """``dv01_per100``, ``dv01_cr_per_bp`` and ``dv01_basis``: at the cut-off yield, else the weighted average
    yield, else the coupon (flagged)."""
    y, basis = _num(cutoff), "cutoff"
    if not math.isfinite(y):
        y, basis = _num(way), "way"
    if not math.isfinite(y):
        y, basis = _num(coupon), "coupon"
    dv01, status = line_dv01(kind, coupon, maturity, y, settlement, issue_date=issue_date)
    if not math.isfinite(dv01):
        basis = status
    elif status == "par_coupon":
        basis += ";par_coupon"
    cr = booked * dv01 / 100 if math.isfinite(booked) and math.isfinite(dv01) else math.nan
    return {"dv01_per100": dv01, "dv01_cr_per_bp": cr, "dv01_basis": basis}


def _times(announcement: tuple[pd.Timestamp, str] | None, result: tuple[pd.Timestamp, str] | None,
           flags: list[str]) -> dict:
    """The row's time fields; an announcement after its result is capped at it (identity E4) and flagged in
    ``flags`` (the caller's result-time flags: the flag compares the two times)."""
    a_time, a_basis = announcement if announcement is not None else (pd.NaT, "none")
    r_time, r_basis = result if result is not None else (pd.NaT, "none")
    if a_time is not pd.NaT and r_time is not pd.NaT and a_time > r_time:
        flags.append("announcement_after_result")
        a_time = r_time
    return {"announcement_public_from": a_time, "announcement_basis": a_basis if a_time is not pd.NaT else "none",
            "result_public_from": r_time, "result_basis": r_basis if r_time is not pd.NaT else "none"}


_LEADING_COUPON = re.compile(r"^\s*\d{1,2}(?:\.\d+)?\s*%?\s*")


def new_issue_key(kind: str, state_code: str, maturity, fallback: str) -> str:
    """A new issue's id key without its coupon, which the result decides (``new GS 2036``, ``new KA SGS 2034``,
    ``new GOI SGRB 2036``, ``new FRB 2034``): known at the announcement, unique in a day (one issuer does not offer two
    new securities maturing in one year on one day). T-bills and CMBs have no coupon and keep ``fallback``."""
    m = _day(maturity)
    year = f" {m.year}" if m is not pd.NaT else ""
    if kind == GSEC:
        return f"new GS{year}" if year else "new " + _LEADING_COUPON.sub("", fallback)
    if kind == GREEN:
        return f"new GOI SGRB{year}" if year else "new " + _LEADING_COUPON.sub("", fallback)
    if kind == FRB:
        return f"new FRB{year}" if year else "new " + _LEADING_COUPON.sub("", fallback)
    if kind == SDL:
        state = _text(state_code)
        return f"new {state} SGS{year}".replace("  ", " ") if year else "new " + _LEADING_COUPON.sub("", fallback)
    return fallback


def _auction_key(rec: Mapping, maturity: pd.Timestamp) -> str:
    if rec.get("kind") == TBILL:
        tenor = _num(rec.get("tenor_days"))
        tenor_text = f"{int(tenor)}DTB" if math.isfinite(tenor) else "DTB"
        return f"{tenor_text} {maturity:%Y-%m-%d}" if maturity is not pd.NaT else tenor_text
    for value in (rec.get("isin"), rec.get("name_key"), rec.get("security")):
        if not _missing(value):
            return str(value)
    return ""


def auction_events(auctions: pd.DataFrame, ledger: pd.DataFrame, *, tables: Mapping[str, pd.DataFrame] | None = None,
                   handbook: pd.DataFrame | None = None, releases: pd.DataFrame | None = None,
                   master: pd.DataFrame | None = None, calendar: BusinessCalendar | None = None,
                   settings: EventSettings = DEFAULT_SETTINGS) -> pd.DataFrame:
    """One row per auction line (B7.2), plus underwriting results and auctions known only from RBI's releases.

    ``auctions``: the auction table (``auctions.build_auctions(...).auctions`` or ``auctions.read_auctions``);
    ``ledger``: the issuance ledger built from it (``issuance.build_ledger`` or ``read_ledger``). Every ledger issue
    or reissue row must name an auction row (``auction_id``); a ledger from another build is refused. ``tables``:
    RBI release tables by kind (``auctions.read_release_tables`` or the RBI store's ``tables/``): the full results
    give bids, weighted averages and greenshoes, the announcements their times, the underwriting results their rows.
    ``handbook``: ``auctions.load_handbook_auctions``' frame (bids and devolvement). ``releases``: a release index
    (the store's ``releases.csv``) for the times of releases named in ``source_row``. ``master``: finds ISINs for
    auctions known only from an announcement.

    Every auction row is kept: one that does not count as issuance (rejected, a conversion line) has ``booked_cr``
    0 and its flags (``not_counted``); one that counts but has no ledger row (a T-bill without a maturity date) has
    none (``not_in_ledger``). A result in ``tables`` with no auction row (a release newer than the auction table) is a
    row flagged ``not_in_ledger;result_only``; an announcement with neither, ``announced_only``.
    """
    _need(auctions, _AUCTION_NEEDS, "the auction table")
    _need(ledger, _LEDGER_NEEDS, "the issuance ledger")
    _refuse_repeats(auctions, "the auction table", ("auction_id", "auction_date", "security"))
    ids_twice = auctions["auction_id"].map(_text)
    ids_twice = sorted(set(ids_twice[ids_twice.duplicated()]))
    if ids_twice:
        raise ValueError(f"the auction table gives the auction id {ids_twice[0]!r} to two different rows: ids must be "
                         "unique (rebuild it with cr-strat auctions)")
    calendar = mumbai_settlement_calendar() if calendar is None else calendar
    rel = _release_index(releases)
    issuance = ledger[ledger["event"].isin(ISSUANCE_EVENTS)]
    ids = issuance["auction_id"].map(_text)
    doubled = sorted(set(ids[ids.duplicated()]))
    if doubled:
        raise ValueError(f"the ledger books auction rows more than once: {doubled[:3]}")
    known = set(auctions["auction_id"].map(_text))
    stray = sorted(set(ids) - known)
    if stray:
        raise ValueError(f"the ledger's issuance rows name auctions the auction table lacks ({stray[:3]}, "
                         f"{len(stray)} in all): the ledger and the auction table are not from the same build")
    booked_rows = {i: rec for i, rec in zip(ids, issuance.to_dict("records"))}
    results = _result_index(tables, rel)
    ann_index, announcements = _announcement_index(tables, rel)
    hb = {}
    if handbook is not None and len(handbook):
        _need(handbook, ("auction_date", "name_key", "comp_received_cr", "nc_received_cr", "devolvement_cr"),
              "the Handbook frame")
        for rec in handbook.to_dict("records"):
            hb[(_day(rec["auction_date"]), _text(rec["name_key"]))] = rec
    uw = {}
    uw_frame = _table(tables, "underwriting_result")
    if uw_frame is not None:
        for rec in sorted(uw_frame.to_dict("records"), key=_rank):
            uw[(_day(rec.get("auction_date")), _text(rec.get("name_key")))] = rec
    used_results: set[tuple] = set()
    used_announcements: set[tuple] = set()
    rows: list[dict] = []
    keys_by_line: dict[tuple, str] = {}
    for a in _sorted(auctions, ["auction_id"]).to_dict("records"):
        kind = a["kind"]
        if kind not in AUCTION_KINDS:
            raise ValueError(f"auction row {a['auction_id']!r} has kind {kind!r}, which the event ledger does not "
                             f"know ({', '.join(AUCTION_KINDS)})")
        flags: list[str] = []  # known at the announcement
        rflags = [f for f in _text(a["flags"]).split(";") if f]  # the auction table's flags come from its results
        notes: list[str] = []
        rnotes: list[str] = []
        day = _day(a["auction_date"])
        led = booked_rows.get(_text(a["auction_id"]))
        maturity = _day(led["maturity_date"]) if led is not None and _day(led["maturity_date"]) is not pd.NaT \
            else _day(a["maturity_date"])
        key = _text(led["key"]) if led is not None else _auction_key(a, maturity)
        settlement = _day(led["date"]) if led is not None else _day(a["settlement_date"])
        if settlement is pd.NaT and day is not pd.NaT:
            nxt, projected = _shift(calendar, day, 1)
            settlement = pd.Timestamp(nxt)
            flags.append("settlement_assumed_t1")
            if projected:
                flags.append("calendar_projected")
        if led is not None:
            booked, basis = _num(led["amount_cr"]), _text(led["amount_basis"])
            rnotes.append(_text(led["note"]))
        elif not bool(a["counts_as_issue"]):
            booked, basis = 0.0, ""
            rflags.append("not_counted")
        else:
            booked, basis = math.nan, ""
            rflags.append("not_in_ledger")
        if _text(a["status"]) == "rejected":
            rflags.append("rejected")
        segment = _segment(kind)
        tenor = _num(a["tenor_days"])
        tenor_days = int(tenor) if math.isfinite(tenor) else None
        name_key = _text(a["name_key"])
        rkey = ("tbill", day, tenor_days) if segment == "tbill" else (segment, day, name_key)
        res = results.get(rkey)
        if res is not None:
            used_results.add(rkey)
        new = _text(a["issue_type"]) == "new"
        ann = _announcement_for(ann_index, segment, day, name_key=name_key, security=_text(a["security"]), new=new,
                                maturity_year=_num(a.get("maturity_year")) if math.isfinite(
                                    _num(a.get("maturity_year"))) else None,
                                tenor_days=tenor_days, state_code=_text(a["state_code"]),
                                tenor_years=_tenor_years(maturity, settlement))
        if ann is not None:
            used_announcements.add(ann["_id"])
            announcement = ann["_time"]
            if math.isfinite(_num(ann.get("greenshoe_cr"))):
                notes.append(f"greenshoe option up to {_num(ann.get('greenshoe_cr')):g} crore")
        else:
            when, how, projected = estimated_announcement(kind, day, calendar, settings)
            announcement = (when, how)
            if projected:
                flags.append("calendar_projected")
        observed = None
        ref = _release_ref(a["source_row"])
        if ref is not None and ref[0] in rel and rel[ref[0]][0] is not pd.NaT:
            observed = rel[ref[0]][:2]
        elif res is not None and res["_time"][0] is not pd.NaT:
            observed = res["_time"]
        result = _result_time(kind, day, observed, settings)
        hb_rec = hb.get((day, name_key))
        bids = _num(res.get("comp_received_cr")) if res is not None else math.nan
        nc_bids = _num(res.get("nc_received_cr")) if res is not None else math.nan
        if not math.isfinite(bids) and hb_rec is not None:
            bids, nc_bids = _num(hb_rec["comp_received_cr"]), _num(hb_rec["nc_received_cr"])
        notified, accepted = _num(a["notified_cr"]), _num(a["accepted_cr"])
        devolvement = _num(a["devolvement_cr"])
        if not math.isfinite(devolvement) and res is not None:
            devolvement = _num(res.get("devolvement_cr"))
        if not math.isfinite(devolvement) and hb_rec is not None:
            devolvement = _num(hb_rec["devolvement_cr"])
        inferred = notified - accepted if basis == "notified_assumed_devolved" and math.isfinite(notified) \
            and math.isfinite(accepted) else math.nan
        way = _num(res.get("way")) if res is not None else math.nan
        wap = _num(res.get("wap")) if res is not None else math.nan
        cutoff = _num(a["cutoff_yield"])
        uw_rec = uw.get((day, name_key))
        issue = _day(a["first_issue_date"])
        if issue is pd.NaT and new:
            issue = settlement
        dv = _dv01_fields(kind, a["coupon_pct"], maturity, settlement, booked, cutoff=cutoff, way=way,
                          issue_date=issue)
        if dv["dv01_basis"].startswith(("coupon", "way")) and not booked == 0:
            rflags.append(f"dv01_at_{dv['dv01_basis'].split(';')[0]}")
        times = _times(announcement, result, rflags)
        source_ref = _join([f"auction {a['auction_id']}", _text(a["source_row"])])
        id_key = new_issue_key(kind, _text(a["state_code"]), maturity, key) if new else key
        rows.append(_event(
            event_id=_event_id(AUCTION_KINDS[kind], day, id_key), kind=AUCTION_KINDS[kind],
            subkind=_text(a["issue_type"]), event_date=day, isin=_text(a["isin"]), key=key,
            security=_text(a["security"]), state_code=_text(a["state_code"]), supply_kind=kind,
            tenor_bucket=tenor_bucket(maturity, settlement), maturity_date=maturity, coupon_pct=_num(a["coupon_pct"]),
            tenor_days=tenor_days, notified_cr=notified, booked_cr=booked, accepted_cr=accepted, amount_basis=basis,
            greenshoe_cr=_greenshoe(res, accepted, notified, kind), nc_accepted_cr=_num(a["nc_accepted_cr"]),
            devolvement_cr=devolvement, devolvement_inferred_cr=inferred, bids_cr=bids, nc_bids_cr=nc_bids,
            bid_cover=_bid_cover(bids, nc_bids, notified, settings), cutoff_yield=cutoff,
            cutoff_price=_num(a["cutoff_price"]), way=way, wap=wap,
            tail_bp=_tail(cutoff, way),
            commission_paise=_num(uw_rec.get("acu_commission_paise")) if uw_rec is not None else math.nan,
            **dv, **times, settlement_date=settlement, auction_id=_text(a["auction_id"]), source=_text(a["sources"]),
            source_ref=source_ref, flags=_flags(flags), notes=_join(notes), result_flags=_flags(rflags),
            result_notes=_join(rnotes)))
        keys_by_line[(day, name_key)] = key
    rows.extend(_release_only_rows(results, used_results, ann_index, announcements, used_announcements, master,
                                   calendar, settings))
    for rec in (uw_frame.to_dict("records") if uw_frame is not None else []):
        rows.append(_underwriting_row(rec, keys_by_line, rel, calendar, settings))
    return _events_frame(rows)


def _release_only_rows(results: dict, used_results: set, ann_index: dict, announcements: list[dict],
                       used_announcements: set, master: pd.DataFrame | None, calendar: BusinessCalendar,
                       settings: EventSettings) -> list[dict]:
    """Auctions known only from RBI's releases: results without an auction row, announcements without either."""
    rows = []
    for rkey in sorted(set(results) - used_results, key=lambda k: tuple(_sort_text(p) for p in k)):
        res = results[rkey]
        segment, day = rkey[0], rkey[1]
        security = _text(res.get("security"))
        if segment == "tbill":
            kind = TBILL
        elif segment == "sdl":
            kind = SDL
        else:
            kind = parse_security_name(security).kind
            kind = kind if kind in AUCTION_KINDS else GSEC
        new = _text(res.get("issue_type")) == "new"
        ann = _announcement_for(
            ann_index, segment, day, name_key=_text(res.get("name_key")), security=security, new=new,
            maturity_year=_num(res.get("maturity_year")) if math.isfinite(_num(res.get("maturity_year"))) else None,
            tenor_days=rkey[2] if segment == "tbill" else None, state_code=_text(res.get("state_code")),
            tenor_years=int(_num(res.get("tenor_years"))) if math.isfinite(_num(res.get("tenor_years"))) else None)
        if ann is not None:
            used_announcements.add(ann["_id"])
        rows.append(_release_row(kind, day, res, ann, master, calendar, settings, [],
                                 ["not_in_ledger", "result_only"]))
    for ann in announcements:
        if ann["_id"] in used_announcements:
            continue
        name = ann["_table"]
        day = _day(ann.get("auction_date"))
        if name == "tbill_announcement":
            kind = TBILL
        elif name == "sdl_announcement":
            kind = SDL
        else:
            kind = parse_security_name(ann.get("security")).kind
            kind = kind if kind in AUCTION_KINDS else GSEC
        used_announcements.add(ann["_id"])
        rows.append(_release_row(kind, day, None, ann, master, calendar, settings, ["announced_only"], []))
    return rows


def _release_row(kind: str, day: pd.Timestamp, res: Mapping | None, ann: Mapping | None,
                 master: pd.DataFrame | None, calendar: BusinessCalendar, settings: EventSettings,
                 flags: list[str], rflags: list[str]) -> dict:
    src = res if res is not None else ann
    security = _text(src.get("security"))
    name_key = _text(src.get("name_key"))
    settlement = _day(ann.get("settlement_date")) if ann is not None else pd.NaT
    maturity = pd.NaT
    if ann is not None:
        maturity = _day(ann.get("repayment_date")) if "repayment_date" in ann else _day(ann.get("maturity_date"))
    tenor = _num(src.get("tenor_days"))
    tenor_days = int(tenor) if math.isfinite(tenor) else None
    notes = []
    if kind == SDL and maturity is pd.NaT:
        years = _num(src.get("tenor_years"))
        first = _day(src.get("first_issue_date"))
        start = settlement if settlement is not pd.NaT else pd.NaT
        year = _num(src.get("maturity_year"))
        if first is not pd.NaT and math.isfinite(year):
            maturity = _anniversary(first, int(year) - first.year)
            notes.append("maturity: the first issue's anniversary (not printed)")
        elif math.isfinite(years) and start is not pd.NaT and _text(src.get("issue_type")) == "new":
            maturity = _anniversary(start, int(years))
            notes.append("maturity: the settlement's anniversary (not printed)")
    isin = ""
    if master is not None and len(master) and kind != TBILL and security:
        found = match_isin(master, security, on=day.date() if day is not pd.NaT else None)
        isin = found.isin or ""
        if isin and maturity is pd.NaT:
            m = master.loc[master["isin"] == isin, "maturity_date"]
            maturity = _day(m.iloc[0]) if len(m) else pd.NaT
    if kind == TBILL:
        key = f"{tenor_days}DTB {maturity:%Y-%m-%d}" if maturity is not pd.NaT else f"{tenor_days}DTB"
    else:
        key = isin or name_key or security or (f"{_text(src.get('state_code'))} SDL new "
                                               f"{_text(src.get('tenor_years'))}y")
    if settlement is pd.NaT and day is not pd.NaT:
        nxt, projected = _shift(calendar, day, 1)
        settlement = pd.Timestamp(nxt)
        flags = [*flags, "settlement_assumed_t1"] + (["calendar_projected"] if projected else [])
    notified = _num(src.get("notified_cr"))
    if ann is not None:
        announcement = ann["_time"]
    else:
        when, how, projected = estimated_announcement(kind, day, calendar, settings)
        announcement = (when, how)
        if projected:
            flags = [*flags, "calendar_projected"]
    fields: dict = {}
    if res is not None:
        accepted = _num(res.get("total_accepted_cr"))
        cutoff, way = _num(res.get("cutoff_yield")), _num(res.get("way"))
        bids, nc_bids = _num(res.get("comp_received_cr")), _num(res.get("nc_received_cr"))
        dv = _dv01_fields(kind, src.get("coupon_pct") if "coupon_pct" in src else parse_security_name(
            security).coupon_pct, maturity, settlement, math.nan, cutoff=cutoff, way=way)
        fields.update(accepted_cr=accepted, nc_accepted_cr=_num(res.get("nc_accepted_cr")),
                      devolvement_cr=_num(res.get("devolvement_cr")), bids_cr=bids, nc_bids_cr=nc_bids,
                      bid_cover=_bid_cover(bids, nc_bids, notified, settings), cutoff_yield=cutoff,
                      cutoff_price=_num(res.get("cutoff_price")), way=way, wap=_num(res.get("wap")),
                      tail_bp=_tail(cutoff, way),
                      greenshoe_cr=_greenshoe(res, accepted, notified, kind), **dv)
        result = _result_time(kind, day, res["_time"] if res["_time"][0] is not pd.NaT else None, settings)
    else:
        result = None
    flags, rflags = list(flags), list(rflags)
    times = _times(announcement, result, rflags)
    refs = [f"release {int(_num(r.get('release_id')))} line {int(_num(r.get('line')))}"
            for r in (res, ann) if r is not None and math.isfinite(_num(r.get("release_id")))
            and math.isfinite(_num(r.get("line")))]
    coupon = _num(src.get("coupon_pct")) if "coupon_pct" in src else _num(parse_security_name(security).coupon_pct)
    new = _text(src.get("issue_type")) == "new"
    id_key = new_issue_key(kind, _text(src.get("state_code")), maturity, key) if new else key
    return _event(
        event_id=_event_id(AUCTION_KINDS[kind], day, id_key), kind=AUCTION_KINDS[kind],
        subkind=_text(src.get("issue_type")), event_date=day, isin=isin, key=key, security=security,
        state_code=_text(src.get("state_code")), supply_kind=kind, tenor_bucket=tenor_bucket(maturity, settlement),
        maturity_date=maturity, coupon_pct=coupon, tenor_days=tenor_days, notified_cr=notified, **fields, **times,
        settlement_date=settlement, source="rbi_release", source_ref=_join(refs), flags=_flags(flags),
        notes=_join(notes), result_flags=_flags(rflags))


def _anniversary(start: pd.Timestamp, years: int) -> pd.Timestamp:
    """``start`` moved ``years`` years on (29 February becomes 28 February), as the RBI store dates SDLs."""
    s = _day(start).date()
    day = 28 if (s.month, s.day) == (2, 29) else s.day
    return pd.Timestamp(dt.date(s.year + years, s.month, day))


def _underwriting_row(rec: Mapping, keys_by_line: dict, rel: dict, calendar: BusinessCalendar,
                      settings: EventSettings) -> dict:
    day = _day(rec.get("auction_date"))
    name_key = _text(rec.get("name_key"))
    key = keys_by_line.get((day, name_key)) or name_key or _text(rec.get("security"))
    flags: list[str] = []
    when, how, projected = estimated_announcement(GSEC, day, calendar,
                                                  replace(settings, announcement_rule="conservative"))
    if projected:
        flags.append("calendar_projected")
    observed = _row_time(rec, rel)
    result = observed if observed[0] is not pd.NaT else ((_end_of_day(day), "end_of_day") if day is not pd.NaT
                                                         else (pd.NaT, "none"))
    rflags: list[str] = []
    times = _times((when, how), result, rflags)
    rnotes = [f"MUC {_num(rec.get('muc_cr')):g} crore, ACU accepted {_num(rec.get('acu_accepted_cr')):g} crore"
              if math.isfinite(_num(rec.get("muc_cr"))) else ""]
    notes = ["announced the business day before the auction (UNVERIFIED time)"]
    ref = (f"release {int(_num(rec.get('release_id')))} line {int(_num(rec.get('line')))}"
           if math.isfinite(_num(rec.get("release_id"))) and math.isfinite(_num(rec.get("line"))) else "")
    return _event(
        event_id=_event_id("underwriting", day, key), kind="underwriting", event_date=day,
        isin=key if key.startswith("IN") else "", key=key, security=_text(rec.get("security")),
        notified_cr=_num(rec.get("notified_cr")), accepted_cr=_num(rec.get("total_underwritten_cr")),
        commission_paise=_num(rec.get("acu_commission_paise")), **times, source="rbi_underwriting", source_ref=ref,
        flags=_flags(flags), notes=_join(notes), result_flags=_flags(rflags), result_notes=_join(rnotes))


# --- Switches, buybacks, redemptions ------------------------------------------------------------------------------

def _yield_history(events: pd.DataFrame | None) -> dict[str, list[tuple[pd.Timestamp, float, str]]]:
    """Per key, the yields its rows printed and when each was public (auction, OMO and switch cut-offs)."""
    out: dict[str, list[tuple[pd.Timestamp, float, str]]] = defaultdict(list)
    if events is None or not len(events):
        return out
    for rec in events.to_dict("records"):
        y = _num(rec.get("cutoff_yield"))
        when = _when(rec.get("result_public_from"))
        if not math.isfinite(y) or when is pd.NaT or not _text(rec.get("key")):
            continue
        out[_text(rec["key"])].append((when, y, f"{rec['kind']} of {_iso(rec['event_date'])}"))
    for key in out:
        out[key].sort(key=lambda x: (x[0], x[1], x[2]))
    return out


def _latest_yield(history: dict, key: str, before: pd.Timestamp) -> tuple[float, str]:
    """The latest yield of ``key`` public before ``before``, and where it came from; NaN without one."""
    found = [h for h in history.get(key, []) if h[0] < before]
    if not found:
        return math.nan, ""
    when, y, what = found[-1]
    return y, what


def switch_events(ledger: pd.DataFrame, *, switches: pd.DataFrame | None = None,
                  tables: Mapping[str, pd.DataFrame] | None = None, releases: pd.DataFrame | None = None,
                  yields: pd.DataFrame | None = None, calendar: BusinessCalendar | None = None,
                  settings: EventSettings = DEFAULT_SETTINGS) -> pd.DataFrame:
    """The ledger's ``switch_out`` and ``switch_in`` rows (B7.2), dated on the switch auction, with times and DV01.

    ``booked_cr`` is the ledger's signed amount (minus for the source, plus for the destination). The result's time
    and the destination's cut-off come from the RBI store's ``switch_operations`` (in ``tables``), else the switch
    table (``auctions.normalise_switches``: ``switches``) and the release index; a switch announcement was never
    sampled, so its time is estimated. The destination's DV01 is at its cut-off yield; the source's, which the result
    does not print, at the latest yield of that security in ``yields`` (an event frame: its auction, OMO and switch
    cut-offs) public before the switch's result, else at its coupon (both flagged; UNVERIFIED).
    """
    _need(ledger, _LEDGER_NEEDS, "the issuance ledger")
    calendar = mumbai_settlement_calendar() if calendar is None else calendar
    releases = releases if releases is not None else (tables or {}).get("releases")
    rel = _release_index(releases)
    by_id = {}
    if switches is not None and len(switches):
        _need(switches, ("auction_id", "dest_cutoff_yield", "dest_cutoff_price", "notified_cr"), "the switch table")
        by_id = {_text(r["auction_id"]): r for r in switches.to_dict("records")}
    ops = {}
    op_frame = _table(tables, "switch_operations")
    if op_frame is not None:
        for r in op_frame.to_dict("records"):
            if math.isfinite(_num(r.get("release_id"))) and math.isfinite(_num(r.get("line"))):
                ops[(int(_num(r["release_id"])), int(_num(r["line"])))] = r
    history = _yield_history(yields)
    legs = ledger[ledger["event"].isin(("switch_in", "switch_out"))]
    rows = []
    for rec in _sorted(legs, ["auction_id", "event", "key"]).to_dict("records"):
        flags: list[str] = []
        rflags: list[str] = []
        notes = [_text(rec["note"])]
        rnotes: list[str] = []
        day = _day(rec["auction_date"]) if _day(rec["auction_date"]) is not pd.NaT else _day(rec["date"])
        ref = _release_ref(rec["source_row"])
        op = ops.get(ref) if ref is not None else None
        sw = by_id.get(_text(rec["auction_id"]))
        observed = None
        if op is not None and _when(op.get("public_from")) is not pd.NaT:
            observed = (_when(op["public_from"]), "feed" if _text(op.get("public_from_source")) == "feed"
                        else "release_date")
        elif ref is not None and ref[0] in rel and rel[ref[0]][0] is not pd.NaT:
            observed = rel[ref[0]][:2]
        result = observed if observed is not None else (_end_of_day(day), "end_of_day")
        when, how, projected = estimated_announcement(GSEC, day, calendar, settings)
        if projected:
            flags.append("calendar_projected")
        times = _times((when, how), result, rflags)
        booked = _num(rec["amount_cr"])
        kind = _text(rec["kind"]) or GSEC
        maturity, settlement = _day(rec["maturity_date"]), _day(rec["date"])
        if rec["event"] == "switch_in":
            y = _num((op or {}).get("dest_cutoff_yield"))
            if not math.isfinite(y):
                y = _num((sw or {}).get("dest_cutoff_yield"))
            price = _num((op or {}).get("dest_cutoff_price"))
            if not math.isfinite(price):
                price = _num((sw or {}).get("dest_cutoff_price"))
            dv = _dv01_fields(kind, rec["coupon_pct"], maturity, settlement, booked, cutoff=y, way=math.nan)
            cutoff = y
        else:
            price = math.nan
            y, what = _latest_yield(history, _text(rec["key"]), times["result_public_from"]
                                    if times["result_public_from"] is not pd.NaT else _end_of_day(day))
            cutoff = math.nan
            dv = _dv01_fields(kind, rec["coupon_pct"], maturity, settlement, -booked if math.isfinite(booked)
                              else booked, cutoff=y, way=math.nan)
            if math.isfinite(dv["dv01_cr_per_bp"]):
                dv["dv01_cr_per_bp"] = -dv["dv01_cr_per_bp"]
            if math.isfinite(y):
                dv["dv01_basis"] = "last_known_yield"
                rnotes.append(f"source leg priced at {y:g}%, the yield of its {what} (the result prints none)")
                rflags.append("dv01_at_last_known_yield")
            elif dv["dv01_basis"].startswith("coupon"):
                rflags.append("dv01_at_coupon")
        notified = _num((op or {}).get("notified_cr"))
        if not math.isfinite(notified):
            notified = _num((sw or {}).get("notified_cr"))
        source_ref = _join([f"auction {rec['auction_id']}", _text(rec["source_row"])])
        rows.append(_event(
            event_id=_event_id(rec["event"], day, _text(rec["key"])), kind=rec["event"], event_date=day,
            isin=_text(rec["isin"]), key=_text(rec["key"]), security=_text(rec["security"]),
            state_code=_text(rec["state_code"]), supply_kind=kind, tenor_bucket=tenor_bucket(maturity, settlement),
            maturity_date=maturity, coupon_pct=_num(rec["coupon_pct"]), notified_cr=notified, booked_cr=booked,
            accepted_cr=abs(booked) if math.isfinite(booked) else math.nan, amount_basis=_text(rec["amount_basis"]),
            cutoff_yield=cutoff, cutoff_price=price, **dv, **times, settlement_date=settlement,
            auction_id=_text(rec["auction_id"]), source=_text(rec["source"]), source_ref=source_ref,
            flags=_flags(flags), notes=_join(notes), result_flags=_flags(rflags), result_notes=_join(rnotes)))
    return _events_frame(rows)


def buyback_events(ledger: pd.DataFrame, *, yields: pd.DataFrame | None = None) -> pd.DataFrame:
    """The ledger's ``buyback`` rows (a table kept by hand: no parser, no times; B7.2).

    The result is taken as public at 23:59:59 on the buyback's date (``end_of_day``, UNVERIFIED); the announcement is
    not known. DV01 at the latest yield of the security in ``yields`` before that, else its coupon (flagged).
    """
    _need(ledger, _LEDGER_NEEDS, "the issuance ledger")
    history = _yield_history(yields)
    rows = []
    for rec in _sorted(ledger[ledger["event"] == "buyback"], ["date", "key", "amount_cr"]).to_dict("records"):
        day = _day(rec["date"])
        flags: list[str] = []
        times = _times(None, (_end_of_day(day), "end_of_day"), flags)
        booked = _num(rec["amount_cr"])
        y, what = _latest_yield(history, _text(rec["key"]), times["result_public_from"])
        maturity = _day(rec["maturity_date"])
        dv = _dv01_fields(_text(rec["kind"]) or GSEC, rec["coupon_pct"], maturity, day,
                          -booked if math.isfinite(booked) else booked, cutoff=y, way=math.nan)
        if math.isfinite(dv["dv01_cr_per_bp"]):
            dv["dv01_cr_per_bp"] = -dv["dv01_cr_per_bp"]
        rflags = ["dv01_at_last_known_yield" if math.isfinite(y) else "dv01_at_coupon"]
        if math.isfinite(y):
            dv["dv01_basis"] = "last_known_yield"
        rows.append(_event(
            event_id=_event_id("buyback", day, _text(rec["key"])), kind="buyback", event_date=day,
            isin=_text(rec["isin"]), key=_text(rec["key"]), security=_text(rec["security"]),
            state_code=_text(rec["state_code"]), supply_kind=_text(rec["kind"]),
            tenor_bucket=tenor_bucket(maturity, day), maturity_date=maturity, coupon_pct=_num(rec["coupon_pct"]),
            booked_cr=booked, accepted_cr=abs(booked) if math.isfinite(booked) else math.nan,
            amount_basis=_text(rec["amount_basis"]), **dv, **times, settlement_date=day,
            source=_text(rec["source"]), source_ref=_text(rec["source_row"]), flags=_flags(flags),
            notes=_text(rec["note"]), result_flags=_flags(rflags),
            result_notes=f"priced at {y:g}%, the yield of its {what}" if what else ""))
    return _events_frame(rows)


_SNAPSHOT_NOTE = re.compile(r"RBI list of (\d{4}-\d{2}-\d{2})")


def redemption_events(ledger: pd.DataFrame) -> pd.DataFrame:
    """The ledger's ``maturity`` rows (B7.2): ``booked_cr`` negative, dated on the redemption day.

    The date is public from the security's first auction in the ledger (``since_issue``); the amount from the last
    auction, switch or buyback before it, or the RBI list it was read from (``last_change``). A redemption after the
    inputs' last date is flagged ``scheduled`` (later reissues are not known), a placeholder date ``name_year``.
    """
    _need(ledger, _LEDGER_NEEDS, "the issuance ledger")
    rows = []
    flows = ledger[ledger["event"] != "maturity"]
    first_auction: dict[str, pd.Timestamp] = {}
    changes: dict[str, list[pd.Timestamp]] = defaultdict(list)
    for rec in flows.to_dict("records"):
        key = _text(rec["key"])
        when = _day(rec["auction_date"]) if _day(rec["auction_date"]) is not pd.NaT else _day(rec["date"])
        if when is pd.NaT:
            continue
        changes[key].append(when)
        if rec["event"] in ISSUANCE_EVENTS and (key not in first_auction or when < first_auction[key]):
            first_auction[key] = when
    date_source = ledger["date_source"] if "date_source" in ledger else pd.Series("", index=ledger.index)
    maturities = ledger.assign(_ds=date_source)[ledger["event"] == "maturity"]
    for rec in _sorted(maturities, ["date", "key"]).to_dict("records"):
        key = _text(rec["key"])
        day = _day(rec["date"])
        flags: list[str] = []
        note = _text(rec["note"])
        if "scheduled" in note:
            flags.append("scheduled")
        if _text(rec["_ds"]) == "name_year":
            flags.append("name_year")
        first = first_auction.get(key)
        announcement = (_end_of_day(first), "since_issue") if first is not None else None
        last = max(changes.get(key, []), default=None)
        snap = _SNAPSHOT_NOTE.search(note)
        if snap is not None:
            listed = _day(snap.group(1))
            last = listed if last is None or listed > last else last
        result = None
        if last is not None:
            result = (min(_end_of_day(last), _end_of_day(day)), "last_change")
        times = _times(announcement, result, flags)
        maturity = _day(rec["maturity_date"])
        rows.append(_event(
            event_id=_event_id("redemption", day, key), kind="redemption", subkind=_text(rec["kind"]),
            event_date=day, isin=_text(rec["isin"]), key=key, security=_text(rec["security"]),
            state_code=_text(rec["state_code"]), supply_kind=_text(rec["kind"]),
            tenor_bucket=tenor_bucket(maturity, day), maturity_date=maturity, coupon_pct=_num(rec["coupon_pct"]),
            booked_cr=_num(rec["amount_cr"]), amount_basis=_text(rec["amount_basis"]), dv01_basis="redemption",
            **times, settlement_date=day, source="issuance_ledger", source_ref=f"ledger maturity of {key}",
            flags=_flags(flags), notes=note))
    return _events_frame(rows)


# --- Open market and liquidity operations -------------------------------------------------------------------------

def _part_time(when, rid, rel: dict) -> tuple[pd.Timestamp, str]:
    """A derived row's part time with its basis: from the release index when it knows the release, else read off
    the time (23:59:59 is the store's end-of-release-date rule)."""
    t = _when(when)
    if t is pd.NaT:
        return pd.NaT, "none"
    rid = _num(rid)
    if math.isfinite(rid) and int(rid) in rel:
        return t, rel[int(rid)][1] if rel[int(rid)][0] == t else ("feed" if t.time() != dt.time(23, 59, 59)
                                                                  else "release_date")
    return t, "release_date" if t.time() == dt.time(23, 59, 59) else "feed"


def operation_events(tables: Mapping[str, pd.DataFrame], *, releases: pd.DataFrame | None = None) -> pd.DataFrame:
    """Open market operations (one row per security) and variable rate repo and reverse repo auctions (B7.2).

    From the RBI store's derived ``omo_operations`` and ``liquidity_operations`` (``tables``). OMO rows carry the
    face accepted (``accepted_cr``; ``booked_cr`` blank: the issuance ledger does not hold the RBI's purchases),
    offers (``bids_cr``), cut-off and weighted averages, the liquidity effect (positive injects) and the DV01 of the
    face accepted at the cut-off yield. LAF rows carry the notified, received and accepted amounts, the cut-off rate
    (``rate_pct``) and weighted average rate (``way``), the liquidity effect and the reversal date; their result time
    is the result's release, else the Money Market Operations line it was known from.
    """
    releases = releases if releases is not None else (tables or {}).get("releases")
    rel = _release_index(releases)
    rows = []
    omo = _table(tables, "omo_operations")
    if omo is not None:
        _need(omo, ("operation", "auction_date", "settlement_date", "security", "name_key", "isin", "maturity_date",
                    "accepted_cr", "offered_cr", "cutoff_yield", "cutoff_price", "way", "wap", "liquidity_effect_cr",
                    "announcement_id", "announcement_public_from", "result_id", "result_public_from"),
              "omo_operations")
        _refuse_repeats(omo, "omo_operations", ("auction_date", "operation", "security", "isin"))
        for rec in _sorted(omo, ["auction_date", "operation", "line", "security"]).to_dict("records"):
            flags: list[str] = []
            rflags: list[str] = []
            op = _text(rec["operation"])
            kind = "omo_purchase" if op == "purchase" else ("omo_sale" if op == "sale" else "")
            if not kind:
                raise ValueError(f"omo_operations row of {_iso(rec['auction_date'])} ({rec['security']}): operation "
                                 f"{op!r} is neither purchase nor sale")
            day, settlement, maturity = _day(rec["auction_date"]), _day(rec["settlement_date"]), _day(
                rec["maturity_date"])
            parsed = parse_security_name(rec["security"])
            key = _text(rec["isin"]) or _text(rec["name_key"]) or _text(rec["security"])
            accepted = _num(rec["accepted_cr"])
            dv = _dv01_fields(parsed.kind if parsed.kind in AUCTION_KINDS else GSEC, parsed.coupon_pct, maturity,
                              settlement, accepted, cutoff=rec["cutoff_yield"], way=rec["way"])
            times = _times(_part_time(rec["announcement_public_from"], rec["announcement_id"], rel),
                           _part_time(rec["result_public_from"], rec["result_id"], rel), rflags)
            notes = [f"aggregate notified {_num(rec.get('aggregate_notified_cr')):g} crore"
                     if math.isfinite(_num(rec.get("aggregate_notified_cr"))) else ""]
            rnotes = [f"offered {_num(rec.get('total_offered_cr')):g}, accepted {_num(rec.get('total_accepted_cr')):g} "
                      "crore in all" if math.isfinite(_num(rec.get("total_accepted_cr"))) else "",
                      _text(rec.get("notes"))]
            refs = [f"release {int(_num(rec[c]))}" for c in ("announcement_id", "result_id")
                    if math.isfinite(_num(rec[c]))]
            cutoff, way = _num(rec["cutoff_yield"]), _num(rec["way"])
            rows.append(_event(
                event_id=_event_id(kind, day, key), kind=kind, subkind=_text(rec.get("auction_method")),
                event_date=day, isin=_text(rec["isin"]), key=key, security=_text(rec["security"]),
                supply_kind=parsed.kind if parsed.kind in AUCTION_KINDS else GSEC,
                tenor_bucket=tenor_bucket(maturity, settlement), maturity_date=maturity, coupon_pct=_num(
                    parsed.coupon_pct), accepted_cr=accepted, bids_cr=_num(rec["offered_cr"]), cutoff_yield=cutoff,
                cutoff_price=_num(rec["cutoff_price"]), way=way, wap=_num(rec["wap"]),
                tail_bp=_tail(cutoff, way, rbi_buys=kind == "omo_purchase"),
                liquidity_effect_cr=_num(rec["liquidity_effect_cr"]), **dv, **times, settlement_date=settlement,
                source="rbi_store:omo_operations", source_ref=_join(refs), flags=_flags(flags), notes=_join(notes),
                result_flags=_flags(rflags), result_notes=_join(rnotes)))
    laf = _table(tables, "liquidity_operations")
    if laf is not None:
        _need(laf, ("direction", "auction_date", "tenor_days", "reversal_date", "announcement_id",
                    "announcement_public_from", "notified_cr", "result_id", "result_public_from", "received_cr",
                    "accepted_cr", "cutoff_rate_pct", "wa_rate_pct", "liquidity_effect_cr", "mmo_release_id",
                    "mmo_public_from", "mmo_check"), "liquidity_operations")
        _refuse_repeats(laf, "liquidity_operations", ("auction_date", "direction", "tenor_days", "result_id"))
        counts: dict[tuple, int] = defaultdict(int)
        for rec in _sorted(laf, ["auction_date", "direction", "tenor_days", "result_id"]).to_dict("records"):
            direction = _text(rec["direction"])
            kind = {"repo": "vrr", "reverse_repo": "vrrr"}.get(direction)
            if kind is None:
                raise ValueError(f"liquidity_operations row of {_iso(rec['auction_date'])}: direction {direction!r} is "
                                 "neither repo nor reverse_repo")
            day = _day(rec["auction_date"])
            tenor = _num(rec["tenor_days"])
            key = f"{int(tenor)}d" if math.isfinite(tenor) else "d"
            counts[(kind, day, key)] += 1
            if counts[(kind, day, key)] > 1:
                key = f"{key}#{counts[(kind, day, key)]}"
            flags: list[str] = []
            rflags = [f"mmo_{_text(rec['mmo_check'])}"] if _text(rec["mmo_check"]) else []
            result = _part_time(rec["result_public_from"], rec["result_id"], rel)
            if result[0] is pd.NaT:
                result = _part_time(rec["mmo_public_from"], rec["mmo_release_id"], rel)
                if result[0] is not pd.NaT:
                    rflags.append("result_from_mmo")
            times = _times(_part_time(rec["announcement_public_from"], rec["announcement_id"], rel), result, rflags)
            cutoff, wa = _num(rec["cutoff_rate_pct"]), _num(rec["wa_rate_pct"])
            refs = [f"release {int(_num(rec[c]))}" for c in ("announcement_id", "result_id", "mmo_release_id")
                    if math.isfinite(_num(rec[c]))]
            rows.append(_event(
                event_id=_event_id(kind, day, key), kind=kind, subkind=key.split("#")[0], event_date=day, key=key,
                tenor_days=int(tenor) if math.isfinite(tenor) else None, notified_cr=_num(rec["notified_cr"]),
                accepted_cr=_num(rec["accepted_cr"]), bids_cr=_num(rec["received_cr"]), rate_pct=cutoff, way=wa,
                tail_bp=_tail(cutoff, wa, rbi_buys=kind == "vrr"),
                liquidity_effect_cr=_num(rec["liquidity_effect_cr"]), **times, settlement_date=day,
                reversal_date=_day(rec["reversal_date"]), source="rbi_store:liquidity_operations",
                source_ref=_join(refs), flags=_flags(flags), result_flags=_flags(rflags),
                result_notes=_text(rec.get("notes"))))
    return _events_frame(rows)


# --- Calendars ----------------------------------------------------------------------------------------------------

_CALENDAR_SOURCES = {"gsec_calendar": "gsec_calendar", "sdl_calendar": "sdl_calendar",
                     "tbill_calendar": "tbill_calendar"}


def calendar_events(supply_calendar: pd.DataFrame | None, *, releases: pd.DataFrame | None = None) -> pd.DataFrame:
    """The issuance calendars' lines (B7.2): GoI weeks by tenor, state quarter totals, from ``supply_calendar``.

    One row per line, dated on its calendar's release (``event_date``) with the planned period in ``period_start``
    and ``period_end`` and the planned amount in ``notified_cr``; public from the release (``announcement_*``). A
    revised calendar is a new release, so its lines are rows of their own. Single auctions' announcements in the
    same table are auction rows (``auction_events``), not calendar lines. T-bill calendar lines
    (``tbill_calendar``), which the store does not parse, come from a user's table (``read_calendar_lines``) and are
    weekly lines like the GoI calendar's, keyed by the bill's tenor in days.
    """
    rel = _release_index(releases)
    rows = []
    if supply_calendar is None or not len(supply_calendar):
        return _events_frame(rows)
    _need(supply_calendar, ("source", "kind", "period_start", "period_end", "tenor_years", "tenor_bucket",
                            "amount_cr", "release_id", "line", "public_from", "public_from_source"),
          "supply_calendar")
    lines = supply_calendar[supply_calendar["source"].isin(_CALENDAR_SOURCES)]
    _refuse_repeats(lines, "supply_calendar", ("source", "release_id", "line", "period_start"))
    for rec in _sorted(lines, ["release_id", "line", "period_start"]).to_dict("records"):
        kind = _CALENDAR_SOURCES[rec["source"]]
        flags: list[str] = []
        when = _when(rec["public_from"])
        given = _text(rec["public_from_source"])
        basis = "none" if when is pd.NaT else {"feed": "feed", "user": "user_table"}.get(given, "release_date")
        rid = _num(rec["release_id"])
        released = pd.NaT
        if math.isfinite(rid) and int(rid) in rel:
            released = rel[int(rid)][0].normalize() if rel[int(rid)][0] is not pd.NaT else pd.NaT
        if released is pd.NaT and when is not pd.NaT:
            released = when.normalize()
        start, end = _day(rec["period_start"]), _day(rec["period_end"])
        tenor, days = _num(rec["tenor_years"]), _num(rec.get("tenor_days"))
        security_kind = _text(rec["kind"]) or {"sdl_calendar": SDL, "tbill_calendar": TBILL}.get(kind, GSEC)
        if kind in WEEKLY_CALENDAR_KINDS:
            alloc = _num(rec.get("allocation_no"))
            if kind == "tbill_calendar" and math.isfinite(days):  # a bill's tenor in days (91, 182, 364)
                term = subkind = f"{int(days)}d"
            else:
                term, subkind = f"{_text(rec['tenor_years'])}y", f"{tenor:g}y" if math.isfinite(tenor) else ""
            key = f"{_iso(start)}/{term}/{security_kind}/{int(alloc) if math.isfinite(alloc) else _text(rec['line'])}"
        else:
            key, subkind = f"{_iso(start)}/{_iso(end)}", "quarter"
        times = _times((when, basis) if when is not pd.NaT else None, None, flags)
        ref = f"release {int(rid)} line {int(_num(rec['line']))}" if math.isfinite(rid) else ""
        rows.append(_event(
            event_id=_event_id(kind, released, key), kind=kind, subkind=subkind, event_date=released, key=key,
            security=security_kind, supply_kind=security_kind, tenor_bucket=_text(rec["tenor_bucket"]),
            period_start=start, period_end=end, notified_cr=_num(rec["amount_cr"]), **times,
            source=f"rbi_store:supply_calendar:{rec['source']}", source_ref=ref, flags=_flags(flags),
            notes=_text(rec.get("notes"))))
    return _events_frame(rows)


def read_calendar_lines(source: str | Path | pd.DataFrame) -> pd.DataFrame:
    """Issuance calendar lines of the user's own (``CALENDAR_LINE_COLUMNS``), typed as the RBI store's
    ``supply_calendar`` so ``collect_events(calendar_lines=...)`` adds them to the store's.

    For calendars the store does not parse, RBI's T-bill calendar above all (the quarter's weekly notified amounts by
    tenor). One row per line: ``source`` ``tbill_calendar`` or ``gsec_calendar`` (a week's line) or ``sdl_calendar``
    (a quarter's total); ``kind`` the securities' kind (default ``tbill``, ``gsec``, ``sdl``); ``period_start`` and
    ``period_end`` the week (Monday to Friday) or quarter; ``tenor_days`` or ``tenor_years`` for a week's line;
    ``tenor_bucket`` (blank: the bucket of the nominal tenor, ``rbi_store.nominal_tenor_bucket``, where a T-bill of
    91 to 364 days is 0-1y); ``amount_cr``; ``release_id``, a whole number naming the calendar, a new one for each
    revision (the latest release covering a week replaces the earlier ones in ``forward_supply``); ``line``, a whole
    number; ``public_from``, when the calendar was public (India time; a date alone is taken as 23:59:59 on it).
    Blank rows are dropped; a missing column or a bad cell is refused, naming its line.
    """
    from cr_strat import rbi_store

    name = "calendar lines" if isinstance(source, pd.DataFrame) else str(source)
    if isinstance(source, pd.DataFrame):
        raw = source.astype(object).where(source.notna(), "").astype(str)
    else:
        if not Path(source).exists():
            raise FileNotFoundError(f"{source}: no such calendar-line table")
        raw = pd.read_csv(source, dtype=str, keep_default_na=False)
    raw = raw.map(str.strip)
    need = ("source", "period_start", "period_end", "amount_cr", "release_id", "line", "public_from")
    missing = [c for c in need if c not in raw]
    if missing or not ("tenor_years" in raw or "tenor_days" in raw):
        raise ValueError(f"{name}: needs the columns {', '.join(need)} and tenor_years or tenor_days "
                         f"(missing: {', '.join(missing) or 'a tenor'}); CALENDAR_LINE_COLUMNS lists the layout")
    unknown = sorted(set(raw.columns) - set(CALENDAR_LINE_COLUMNS))
    if unknown:
        raise ValueError(f"{name}: unknown column(s) {', '.join(unknown)} (CALENDAR_LINE_COLUMNS lists the layout)")
    buckets = {label for _lo, _hi, label in TENOR_BUCKETS} | {"all"}
    defaults = {"tbill_calendar": TBILL, "gsec_calendar": GSEC, "sdl_calendar": SDL}
    rows = []
    for i, rec in raw.iterrows():
        if not any(rec.values):
            continue
        where = f"{name} line {int(i) + 2}"
        src = rec["source"]
        if src not in _CALENDAR_SOURCES:
            raise ValueError(f"{where}: source {src!r} is not one of {', '.join(sorted(_CALENDAR_SOURCES))}")
        start, end = _day(rec["period_start"]), _day(rec["period_end"])
        if start is pd.NaT or end is pd.NaT or end < start:
            raise ValueError(f"{where}: period {rec['period_start']!r} to {rec['period_end']!r} is not a span of dates")
        when = _when(rec["public_from"])
        if when is pd.NaT:
            raise ValueError(f"{where}: public_from {rec['public_from']!r} is not a time; a calendar counts only "
                             "from when it was public")
        if not any(ch in rec["public_from"] for ch in "T: "):  # a date alone: the end of that day
            when = _end_of_day(when)
        amount = _num(rec["amount_cr"])
        if not math.isfinite(amount) or amount < 0:
            raise ValueError(f"{where}: amount_cr {rec['amount_cr']!r} is not an amount in crore")
        whole = {}
        for col in ("release_id", "line"):
            value = _num(rec[col])
            if not math.isfinite(value) or value != int(value) or value < 0:
                raise ValueError(f"{where}: {col} {rec[col]!r} is not a whole number")
            whole[col] = str(int(value))
        years = _num(rec.get("tenor_years", ""))
        days = _num(rec.get("tenor_days", ""))
        bucket = rec.get("tenor_bucket", "")
        if src in WEEKLY_CALENDAR_KINDS:
            if not math.isfinite(years) and not math.isfinite(days):
                raise ValueError(f"{where}: a week's line needs tenor_years or tenor_days")
            if math.isfinite(days) and days != int(days):
                raise ValueError(f"{where}: tenor_days {rec['tenor_days']!r} is not a whole number of days")
            bucket = bucket or nominal_tenor_bucket(years if math.isfinite(years) else days / 365)
        else:
            bucket = bucket or "all"
        if bucket not in buckets:
            raise ValueError(f"{where}: tenor_bucket {bucket!r} is not one of {', '.join(sorted(buckets))}")
        rows.append({"source": src, "kind": rec.get("kind", "") or defaults[src], "period_start": _iso(start),
                     "period_end": _iso(end), "tenor_years": "" if not math.isfinite(years) else f"{years:g}",
                     "tenor_days": "" if not math.isfinite(days) else str(int(days)), "tenor_bucket": bucket,
                     "amount_cr": f"{amount:.6f}".rstrip("0").rstrip("."), **whole,
                     "public_from": when.strftime("%Y-%m-%dT%H:%M:%S"), "public_from_source": "user"})
    columns = rbi_store.DERIVED_COLUMNS["supply_calendar"]
    frame = pd.DataFrame([{c: r.get(c, "") for c in columns} for r in rows], columns=list(columns))
    for column in frame.columns:  # typed as rbi_store.read_table types the store's copy
        values = frame[column].replace("", None)
        how = rbi_store.store_column_type(column)
        if how in ("date", "time"):
            frame[column] = pd.to_datetime(values)
        elif how == "int":
            frame[column] = pd.to_numeric(values).astype("Int64")
        elif how == "float":
            frame[column] = pd.to_numeric(values).astype(float)
        elif how == "bool":
            frame[column] = values.map({"True": True, "False": False}).astype("boolean")
        else:
            frame[column] = values
    return frame


def attach_decision_times(meetings: pd.DataFrame, path: str | Path) -> pd.DataFrame:
    """``meetings`` (``policy_path.load_mpc_calendar``'s frame, which keeps only its own columns) with the optional
    ``decision_time`` column (``HH:MM``, India time) of the meetings file at ``path`` joined by ``decision_date``, for
    ``policy_events``; the frame unchanged when the file has no such column."""
    raw = pd.read_csv(path, dtype=str, keep_default_na=False)
    raw.columns = [str(c).strip().lower() for c in raw.columns]
    if "decision_time" not in raw:
        return meetings
    times: dict[pd.Timestamp, str] = {}
    for n, rec in enumerate(raw.to_dict("records"), start=2):
        text, day = rec["decision_time"].strip(), _day(rec.get("decision_date"))
        if not text:
            continue
        if day is pd.NaT:
            raise ValueError(f"{path} line {n}: a decision_time needs the meeting's decision_date")
        times[day] = text
    out = meetings.copy()
    out["decision_time"] = [times.get(_day(d), "") for d in out["decision_date"]]
    return out

# --- MPC, macro releases, index events ----------------------------------------------------------------------------

def policy_events(meetings: pd.DataFrame, policy: pd.DataFrame | None = None, *,
                  calendar: BusinessCalendar | None = None, settings: EventSettings = DEFAULT_SETTINGS) -> pd.DataFrame:
    """MPC decisions and minutes (B6, B7.2) from ``policy_path.load_mpc_calendar``'s frame.

    A decision is public on its day at the meetings frame's ``decision_time`` (``HH:MM``; ``attach_decision_times``
    joins a meetings file's column to ``load_mpc_calendar``'s frame, which leaves it out) where it gives one, else at
    ``settings.mpc_decision_time`` where set, else at its era's time (``MPC_DECISION_TIMES``: 14:30 for the first MPC
    decisions, 10:00 since 2024; partly verified, UNVERIFIED where an era's edge is not known); its date is public from
    the day the schedule (or an unscheduled meeting) was announced; projected meetings
    (``load_mpc_calendar(project=True)``) have no announcement and are flagged. Minutes come ``minutes_days`` (14)
    days after the decision, at 23:59:59 (UNVERIFIED time). With ``policy`` (a policy-rate table: ``rbi_store``'s
    hand-kept one or ``macro_store``'s ``policy_rates_bis``) each decision gets the repo rate in force the business
    day after it (``rate_pct``) and ``policy_path.check_policy_changes``' match of the table's changes in
    ``result_notes`` (hidden by ``events_as_of`` until the decision was public).
    """
    _need(meetings, ("decision_date", "kind", "announced", "confirmed", "projected", "source"), "the MPC meetings")
    calendar = mumbai_settlement_calendar() if calendar is None else calendar
    rates, checks = None, {}
    if policy is not None:
        rates = read_policy_table(policy)
        for rec in check_policy_changes(policy, meetings, calendar=calendar).to_dict("records"):
            if not _missing(rec["decision_date"]):
                checks[_day(rec["decision_date"])] = rec
    rows = []
    for rec in _sorted(meetings, ["decision_date"]).to_dict("records"):
        day = _day(rec["decision_date"])
        flags = []
        if bool(rec["projected"]):
            flags.append("projected")
        if not bool(rec["confirmed"]):
            flags.append("unconfirmed")
        announced = _day(rec["announced"])
        announcement = (_end_of_day(announced), "schedule") if announced is not pd.NaT else None
        rate, notes, rnotes = math.nan, [_text(rec["source"])], []
        if rates is not None:
            after, _ = _shift(calendar, day, 1)
            rate = policy_rate_on(rates, after)
            change = checks.get(day)
            if change is not None:
                rnotes.append(f"repo rate {change['previous_pct']:g} -> {change['repo_pct']:g} from "
                              f"{_iso(change['effective_date'])} ({change['status']})")
            elif math.isfinite(rate):
                rnotes.append(f"no repo-rate change in the policy table; {rate:g} in force on {after}")
        own = _text(rec.get("decision_time"))
        if own:
            try:
                clock, basis = dt.time.fromisoformat(own), "meeting_time"
            except ValueError:
                raise ValueError(f"the MPC meeting decided {_iso(day)}: decision_time {own!r} is not HH:MM") from None
        elif settings.mpc_decision_time is not None:
            clock, basis = settings.mpc_decision_time, "decision_time"
        else:
            clock, basis = mpc_decision_time(day), "decision_time"
        decision_rflags: list[str] = []
        times = _times(announcement, (_at(day, clock), basis), decision_rflags)
        rows.append(_event(
            event_id=_event_id("mpc_decision", day, "mpc"), kind="mpc_decision", subkind=_text(rec["kind"]),
            event_date=day, key="mpc", security="MPC decision", rate_pct=rate, **times, source="policy_path",
            source_ref=f"MPC meeting decided {_iso(day)}", flags=_flags(flags), notes=_join(notes),
            result_flags=_flags(decision_rflags), result_notes=_join(rnotes)))
        minutes_day = day + pd.Timedelta(days=settings.minutes_days)
        minutes_rflags: list[str] = []
        times = _times(announcement, (_end_of_day(minutes_day), "minutes_rule"), minutes_rflags)
        rows.append(_event(
            event_id=_event_id("mpc_minutes", minutes_day, "mpc"), kind="mpc_minutes", subkind=_text(rec["kind"]),
            event_date=minutes_day, key="mpc", security="MPC minutes", **times, source="policy_path",
            source_ref=f"minutes of the MPC meeting decided {_iso(day)}", flags=_flags(flags),
            notes=f"{settings.minutes_days} days after the decision (RBI Act section 45ZL, partly verified)",
            result_flags=_flags(minutes_rflags)))
    return _events_frame(rows)


def _periods_between(family: str, start: pd.Timestamp, end: pd.Timestamp) -> list[str]:
    """Reference periods whose first release could fall from ``start`` to ``end``."""
    if family == "gdp":
        out = []
        for year in range(start.year - 3, end.year + 1):
            fy = fy_name(year)
            out += [f"{fy}Q1", f"{fy}Q2", fy, f"{fy}Q3", f"{fy}Q4"]
        return out
    months = []
    y, m = start.year, start.month
    y, m = add_months(y, m, -4)
    while (y, m) <= (end.year, end.month):
        months.append(f"{y}-{m:02d}")
        y, m = add_months(y, m, 1)
    return months


def macro_events(release_dates=None, *, families: Sequence[str] = mospi.FAMILIES, start=None, end=None,
                 shipped: bool = True) -> pd.DataFrame:
    """MoSPI's releases (B7.2): one row per release, public at its date and time (``mospi.ReleaseCalendar``).

    Without ``start`` and ``end``: the releases the table lists (the shipped table, ``mospi.RELEASE_DATES_CSV``,
    extended or overridden by ``release_dates``, a path or bytes in the same layout). With them: every release of the
    families from ``start`` to ``end``, from the table where it lists one and by the family's rule otherwise
    (``result_basis`` ``release_table``, ``release_table_planned`` or ``release_rule``).
    """
    unknown = [f for f in families if f not in mospi.FAMILIES]
    if unknown:
        raise ValueError(f"families must be among {', '.join(mospi.FAMILIES)}, not {unknown}")
    cal = mospi.ReleaseCalendar.load(release_dates, shipped=shipped)
    lo, hi = _day(start), _day(end)
    if (lo is pd.NaT) != (hi is pd.NaT):
        raise ValueError("give both start and end, or neither")
    wanted: dict[str, mospi.Release] = {}
    if lo is pd.NaT:
        for family, period in sorted(cal.rows):
            if family in families:
                r = cal.release(family, period)
                wanted[r.release_id] = r
    else:
        if lo > hi:
            raise ValueError(f"start {_iso(lo)} is after end {_iso(hi)}")
        for family in families:
            for period in _periods_between(family, lo, hi):
                try:
                    r = cal.release(family, period)
                except (ValueError, SourceError):
                    continue
                if lo.date() <= r.day <= hi.date():
                    wanted[r.release_id] = r
    rows = []
    for rid in sorted(wanted):
        r = wanted[rid]
        row = cal.rows.get((r.family, r.reference_period))
        carries = "; ".join(f"{stage} {period}" for stage, period in row.content) if row is not None else ""
        rows.append(_event(
            event_id=_event_id("macro_release", r.day, rid), kind="macro_release", subkind=r.family,
            event_date=pd.Timestamp(r.day), key=rid, security=f"{r.family} {r.reference_period}",
            result_public_from=pd.Timestamp(r.public_from), result_basis=r.basis, source="mospi",
            source_ref=r.source or "release rule", notes=_join([f"carries {carries}" if carries else "", r.rule,
                                                                r.notes])))
    return _events_frame(rows)


def index_event_rows(index_events) -> pd.DataFrame:
    """Index inclusion events (B7.2) from a table kept by hand: a path or bytes (``global_sources``' layout), or
    ``macro_store``'s derived ``index_events`` frame (with ``public_from``). Each event is public from its
    announcement (``global_sources.index_event_public_from``)."""
    rows = []
    if index_events is None:
        return _events_frame(rows)
    if isinstance(index_events, pd.DataFrame):
        _need(index_events, (*gs.INDEX_EVENT_COLUMNS, "public_from", "public_from_basis"), "the index events")
        records = []
        for rec in index_events.to_dict("records"):
            announced = _text(rec["announced_date"])
            day = _month_end(announced) if re.fullmatch(r"\d{4}-\d{2}", announced) else _day(announced)
            records.append((rec, day, _when(rec["public_from"]), _text(rec["public_from_basis"])))
    else:
        records = []
        for event in gs.load_index_events(index_events):
            public = gs.index_event_public_from(event)
            rec = {c: getattr(event, c, "") for c in ("event_id", "provider", "index_name", "event_type",
                                                       "effective_start", "effective_end", "phase_in",
                                                       "target_weight_pct", "bonds", "status", "source_url",
                                                       "notes")}
            rec["announced_time"] = event.announced_time
            records.append((rec, pd.Timestamp(event.announced_day), _when(public.time), public.basis))
    for rec, day, when, basis in sorted(records, key=lambda x: (_sort_text(x[1]), _text(x[0]["event_id"]))):
        basis = "announced_time" if not _missing(rec.get("announced_time")) else (basis or "publication_rule")
        flags = ["unverified"] if _text(rec["status"]) != "verified" else []
        times = _times((when, basis) if when is not pd.NaT else None, (when, basis) if when is not pd.NaT else None,
                       flags)
        details = _join([_text(rec["provider"]), f"effective {_text(rec['effective_start'])}"
                         + (f" to {_text(rec['effective_end'])}" if _text(rec["effective_end"]) else "")
                         if _text(rec["effective_start"]) else "", _text(rec["phase_in"]),
                         f"target weight {_text(rec['target_weight_pct'])}%" if _text(rec["target_weight_pct"])
                         else "", _text(rec["bonds"]), _text(rec["notes"])])
        rows.append(_event(
            event_id=_event_id("index_event", day, _text(rec["event_id"])), kind="index_event",
            subkind=_text(rec["event_type"]), event_date=day, key=_text(rec["event_id"]),
            security=_text(rec["index_name"]), **times, source="index_events", source_ref=_text(rec["source_url"]),
            flags=_flags(flags), notes=details))
    return _events_frame(rows)


# --- The ledger ---------------------------------------------------------------------------------------------------

def build_event_ledger(*parts: pd.DataFrame) -> pd.DataFrame:
    """The parts (each in ``EVENT_COLUMNS``) as one ledger: ids unique, sorted by event date, kind, key and id.

    A row given twice (the same values in every column) is refused. Rows that share an id but differ get ``#2``,
    ``#3`` ... in a fixed order, noted: the legs of one switch that share a security, say. Auction lines that share an
    id (one security auctioned twice on one day under two auction ids: a line repeated in the auction table, as the
    issuance ledger built from it repeats it too) are flagged ``duplicate_line``, as is the security's redemption,
    whose amount the issuance ledger counts the copies in (noted with their amount); ``supply_table`` leaves the
    numbered copies out of the supply, the redemption and the stock unless told to keep them, so identity E1 shows the
    double count in the ledger, and ``input_gaps`` lists them.
    """
    frames = []
    for n, part in enumerate(parts, start=1):
        if part is None:
            continue
        _need(part, EVENT_COLUMNS, f"part {n} of the event ledger")
        if len(part):
            frames.append(part.loc[:, list(EVENT_COLUMNS)])
    if not frames:
        return empty_events()
    out = pd.concat(frames, ignore_index=True)
    bad = sorted(set(out["kind"]) - set(EVENT_KINDS))
    if bad:
        raise ValueError(f"unknown event kinds {bad}: EVENT_KINDS lists {', '.join(EVENT_KINDS)}")
    text = out.map(_sort_text)
    twice = text.duplicated(keep=False)
    if twice.any():
        raise ValueError(f"the same event is given twice ({sorted(set(out.loc[twice, 'event_id']))[:3]}): a row "
                         "repeated in an input table, or a part passed twice")
    out = _sorted(out, ["event_id", "source_ref", "auction_id", "security", "notified_cr", "booked_cr", "notes"])
    seen: dict[str, int] = defaultdict(int)
    ids, notes = [], []
    for eid, note in zip(out["event_id"], out["notes"]):
        seen[eid] += 1
        if seen[eid] > 1:
            ids.append(f"{eid}#{seen[eid]}")
            notes.append(_join([note, f"another row of {eid} exists: id numbered"]))
        else:
            ids.append(eid)
            notes.append(note)
    out["event_id"], out["notes"] = ids, notes
    base = out["event_id"].str.replace(r"#\d+$", "", regex=True)
    lines = out["kind"].isin(AUCTION_EVENT_KINDS) & (out["auction_id"].map(_text) != "")  # the auction table's rows
    dup = lines & base.map(base[lines].value_counts().to_dict()).fillna(0).gt(1)
    if dup.any():
        out.loc[dup, "flags"] = out.loc[dup, "flags"].map(lambda f: _flags(f, "duplicate_line"))
        out.loc[dup, "notes"] = out.loc[dup, "notes"].map(
            lambda n: _join([n, "the same security's line twice on one day under different auction ids: numbered "
                                "copies left out of supply_table"]))
        # the issuance ledger built from that table counts the copies in the security's redemption too
        copies = _duplicate_copies(out)
        extra = _copy_amounts(copies)
        count = copies.groupby("key").size()
        red = (out["kind"] == "redemption") & out["key"].isin(set(copies["key"]))
        for i in out.index[red]:
            key = out.at[i, "key"]
            out.at[i, "flags"] = _flags(out.at[i, "flags"], "duplicate_line")
            out.at[i, "notes"] = _join([out.at[i, "notes"], (
                f"the issuance ledger's amount counts {int(count[key])} numbered copy(ies) of a duplicated auction "
                f"line of this security (Rs {extra[key]:,.2f} crore): supply_table takes them out unless "
                "keep_duplicates")])
    out = _sorted(out, ["event_date", "kind", "key", "event_id"])
    return _events_frame(out.to_dict("records"))


def _duplicate_copies(events: pd.DataFrame) -> pd.DataFrame:
    """The numbered copies (``#2`` ...) of auction lines flagged ``duplicate_line``: what ``supply_table`` leaves out
    unless told to keep them."""
    numbered = events["event_id"].map(_text).str.contains(r"#\d+$", regex=True)
    flagged = events["flags"].map(lambda f: "duplicate_line" in _text(f).split(";"))
    return events[numbered & flagged & events["kind"].isin(AUCTION_EVENT_KINDS)]


def _copy_amounts(copies: pd.DataFrame) -> pd.Series:
    """The booked amount (crore) of a security's numbered copies, by key, summed exactly."""
    return copies.groupby("key", sort=True)["booked_cr"].agg(
        lambda v: math.fsum(sorted(x for x in map(_num, v) if math.isfinite(x))))


def collect_events(*, auctions: pd.DataFrame | None = None, ledger: pd.DataFrame | None = None,
                   tables: Mapping[str, pd.DataFrame] | None = None, handbook: pd.DataFrame | None = None,
                   master: pd.DataFrame | None = None, switches: pd.DataFrame | None = None,
                   meetings: pd.DataFrame | None = None, policy: pd.DataFrame | None = None,
                   macro_releases=None, macro_range: tuple | None = None, macro: bool = False, index_events=None,
                   calendar_lines: pd.DataFrame | None = None, calendar: BusinessCalendar | None = None,
                   settings: EventSettings = DEFAULT_SETTINGS) -> pd.DataFrame:
    """Every builder on the inputs given, as one ledger (``build_event_ledger``).

    ``tables`` is one mapping of the RBI store's tables (``read_store_tables``; ``merge_tables`` joins another
    source's, such as ``cr-strat rbi-releases``' CSVs): release kinds, derived tables,
    ``releases`` and ``gaps``. ``auctions`` and ``ledger`` go together. Macro releases come with ``macro=True`` (the
    shipped table) or ``macro_releases`` (a user's table), from ``macro_range`` ``(start, end)`` by rule where given.
    ``calendar_lines`` (``read_calendar_lines``: the T-bill calendar, say) are added to the store's calendar lines; a
    line both give is refused as a repeat.
    """
    if (auctions is None) != (ledger is None):
        raise ValueError("give the auction table and the ledger built from it together")
    calendar = mumbai_settlement_calendar() if calendar is None else calendar
    tables = tables or {}
    releases = tables.get("releases")
    parts = []
    if auctions is not None:
        auction_part = auction_events(auctions, ledger, tables=tables, handbook=handbook, releases=releases,
                                      master=master, calendar=calendar, settings=settings)
        ops = operation_events(tables, releases=releases)
        yields = pd.concat([f for f in (auction_part, ops) if len(f)], ignore_index=True) if len(auction_part) or len(
            ops) else empty_events()
        switch_part = switch_events(ledger, switches=switches, tables=tables, releases=releases, yields=yields,
                                    calendar=calendar, settings=settings)
        yields = pd.concat([f for f in (yields, switch_part) if len(f)], ignore_index=True) if len(
            switch_part) else yields
        parts += [auction_part, ops, switch_part, buyback_events(ledger, yields=yields), redemption_events(ledger)]
    else:
        parts.append(operation_events(tables, releases=releases))
    lines = tables.get("supply_calendar")
    if calendar_lines is not None and len(calendar_lines):
        _need(calendar_lines, ("source", "public_from_source"), "the calendar lines")
        lines = calendar_lines if lines is None or not len(lines) else pd.concat([lines, calendar_lines],
                                                                                 ignore_index=True)
    parts.append(calendar_events(lines, releases=releases))
    if meetings is not None:
        parts.append(policy_events(meetings, policy, calendar=calendar, settings=settings))
    if macro or macro_releases is not None:
        lo, hi = macro_range if macro_range is not None else (None, None)
        parts.append(macro_events(macro_releases, start=lo, end=hi))
    if index_events is not None:
        parts.append(index_event_rows(index_events))
    return build_event_ledger(*parts)


def events_as_of(events: pd.DataFrame, t) -> pd.DataFrame:
    """What was known at ``t`` (India time): rows announced by then (or, with no announcement time, whose result was
    public by then), with what the result decides blank until ``result_public_from``: the result's fields
    (``RESULT_FIELDS``, the result's own flags and notes among them) and, for a new issue whose coupon is its cut-off
    yield (``COUPON_AUCTION_KINDS``), its coupon, ISIN, auction id and source reference, its key and name shown as the
    coupon-free id key (``new GS 2036``, the key part of its ``event_id``).

    A row with neither time is never visible. ``t`` may be a date (the end of that day) or a time.
    """
    _need(events, EVENT_COLUMNS, "the event ledger")
    when = _when(t)
    if when is pd.NaT:
        raise ValueError(f"events_as_of needs a time, not {t!r}")
    if isinstance(t, dt.date) and not isinstance(t, dt.datetime):
        when = _end_of_day(when)
    ann, res = events["announcement_public_from"], events["result_public_from"]
    visible = (ann.notna() & (ann <= when)) | (ann.isna() & res.notna() & (res <= when))
    out = events[visible].copy()
    hidden = out["result_public_from"].isna() | (out["result_public_from"] > when)
    for c in RESULT_FIELDS:
        if c in FLOAT_FIELDS:
            out.loc[hidden, c] = math.nan
        else:
            out.loc[hidden, c] = ""
    new = hidden & (out["subkind"] == "new") & out["kind"].isin(COUPON_AUCTION_KINDS)
    if new.any():
        out.loc[new, "coupon_pct"] = math.nan
        for c in NEW_ISSUE_HIDDEN[1:]:
            out.loc[new, c] = ""
        free = out.loc[new, "event_id"].map(lambda e: str(e).split(":", 2)[2] if str(e).count(":") >= 2 else "")
        out.loc[new, "key"] = free
        out.loc[new, "security"] = free
    return out.reset_index(drop=True)


def _all_flags(frame: pd.DataFrame) -> pd.Series:
    """Each row's flags, announcement-time and result-time together, as sets."""
    return pd.Series([set(_text(a).split(";")) | set(_text(b).split(";"))
                      for a, b in zip(frame["flags"], frame["result_flags"])], index=frame.index, dtype=object)


# --- Stock on many dates ------------------------------------------------------------------------------------------

STOCK_COLUMNS = ("date", "key", "isin", "kind", "maturity_date", "tenor_bucket", "outstanding_cr", "method",
                 "anchor_date", "first_event_date", "issuance_complete", "operations_complete")
_BUCKET_LOWS = np.array([lo for lo, _, _ in TENOR_BUCKETS], dtype=float)
_BUCKET_LABELS = [label for _, _, label in TENOR_BUCKETS]


def _buckets(maturity: np.ndarray, on: np.ndarray) -> list[str]:
    """``issuance.tenor_bucket`` for arrays of maturities and dates (datetime64[D]); ``unknown`` without a maturity."""
    out = []
    years = (maturity - on).astype("timedelta64[D]").astype(float) / 365.25
    idx = np.clip(np.searchsorted(_BUCKET_LOWS, years, side="right") - 1, 0, len(_BUCKET_LABELS) - 1)
    for m, i in zip(np.isnat(maturity), idx):
        out.append("unknown" if m else _BUCKET_LABELS[int(i)])
    return out


def _snapshot_rows(snapshot: pd.DataFrame | None) -> pd.DataFrame:
    if snapshot is None or not len(snapshot):
        return pd.DataFrame(columns=["as_of", "isin", "outstanding_cr"])
    _need(snapshot, ("as_of", "isin", "outstanding_cr"), "the RBI snapshot (sovereign.load_goi_outstanding)")
    out = pd.DataFrame({"as_of": pd.to_datetime(snapshot["as_of"]).dt.normalize(),
                        "isin": snapshot["isin"].astype(str).str.upper(),
                        "outstanding_cr": pd.to_numeric(snapshot["outstanding_cr"])})
    for col in ("name", "issue_date", "maturity_date"):
        if col in snapshot:
            out[col] = snapshot[col].to_numpy()
    return out


def _coverage_periods(cov: pd.DataFrame) -> dict[str, list[tuple[np.datetime64, np.datetime64]]]:
    out: dict[str, list[tuple[np.datetime64, np.datetime64]]] = defaultdict(list)
    for source, s, e in zip(cov["source"], pd.to_datetime(cov["start"]), pd.to_datetime(cov["end"])):
        out[source].append((np.datetime64(s.date(), "D"), np.datetime64(e.date(), "D")))
    return out


def _covered_many(periods: dict, source: str, start: np.ndarray, end: np.ndarray) -> np.ndarray:
    """``issuance._covered`` for arrays: one period of ``source`` holds every day from start (exclusive) to end."""
    lo, hi = np.minimum(start, end), np.maximum(start, end)
    out = lo == hi
    one = np.timedelta64(1, "D")
    for s, e in periods.get(source, ()):
        out |= (s <= lo + one) & (hi <= e)
    return out & ~np.isnat(start) & ~np.isnat(end)


def _upto(cum: np.ndarray, event_dates: np.ndarray, days: np.ndarray) -> np.ndarray:
    """The sum of a key's events dated on or before each day (``cum`` holds 0 and then the running sums)."""
    return cum[np.searchsorted(event_dates, days, side="right")]


def stock_on_dates(ledger: pd.DataFrame, dates: Iterable, snapshot: pd.DataFrame | None = None, *,
                   include_zero: bool = False) -> pd.DataFrame:
    """``issuance.outstanding_on`` for many dates at once (``STOCK_COLUMNS``, no ``reasons``), by its rules.

    A key in ``snapshot`` (RBI lists) is reconstructed from the list nearest each date, backward or forward; any other
    key is summed forward from its first event; a key is zero from its redemption day (``matured``, or
    ``matured_date_unknown`` for a placeholder) and, in a snapshot, before its issue date (``before_issue``). The
    completeness flags follow the ledger's coverage table as ``outstanding_on``'s do. ``outstanding_on`` loops over
    keys with pandas filters and takes seconds per date on a full ledger; this does the same arithmetic on arrays
    (``tests/test_event_ledger.py`` checks the two agree).
    """
    _need(ledger, ("key", "isin", "kind", "event", "date", "date_source", "amount_cr", "maturity_date",
                   "maturity_year", "name_key", "security"), "the issuance ledger")
    days = sorted({_day(d) for d in dates})
    if any(d is pd.NaT for d in days):
        raise ValueError("stock_on_dates: every date must be a date")
    out_cols = list(STOCK_COLUMNS)
    if not days or not len(ledger):
        return pd.DataFrame(columns=out_cols)
    t = np.array([np.datetime64(d.date(), "D") for d in days])
    cov = _coverage_periods(coverage(ledger))
    snap = _snapshot_rows(snapshot)
    info: dict[str, dict] = {}
    first_rows = ledger.drop_duplicates("key", keep="first").set_index("key")
    mats = ledger.dropna(subset=["maturity_date"]).drop_duplicates("key", keep="first").set_index("key")[
        "maturity_date"]
    years = ledger.assign(_y=pd.to_numeric(ledger["maturity_year"], errors="coerce")).dropna(subset=["_y"])
    years = years.drop_duplicates("key", keep="first").set_index("key")["_y"]
    red = ledger[ledger["event"] == "maturity"]
    redeemed = red.groupby("key")["date"].min()
    placeholder = red.assign(_p=red["date_source"] == "name_year").groupby("key")["_p"].any()
    for key in sorted(first_rows.index):
        head = first_rows.loc[key]
        info[key] = {"isin": head["isin"], "kind": head["kind"],
                     "maturity_date": _day(mats.get(key)) if key in mats.index else pd.NaT,
                     "maturity_year": int(years[key]) if key in years.index else None, "issue_date": pd.NaT,
                     "redeemed": _day(redeemed.get(key)) if key in redeemed.index else pd.NaT,
                     "placeholder": bool(placeholder.get(key, False))}
    if len(snap):
        latest = snap.sort_values("as_of", kind="mergesort").groupby("isin").tail(1)
        for s in latest.to_dict("records"):
            k = info.get(s["isin"])
            name = s.get("name") if "name" in snap else None
            parsed = parse_security_name(name) if name else None
            if k is None:
                k = info[s["isin"]] = {"isin": s["isin"], "kind": parsed.kind if parsed else "gsec",
                                       "maturity_date": pd.NaT, "maturity_year": None, "issue_date": pd.NaT,
                                       "redeemed": pd.NaT, "placeholder": False}
            if "maturity_date" in snap and k["maturity_date"] is pd.NaT:
                k["maturity_date"] = _day(s["maturity_date"])
            if "issue_date" in snap:
                k["issue_date"] = _day(s["issue_date"])
    for k in info.values():
        if k["placeholder"] and k["maturity_date"] is not pd.NaT:
            k["redeemed"], k["placeholder"] = pd.NaT, False
    flows = ledger[ledger["event"] != "maturity"]
    by_key = {key: grp for key, grp in flows.groupby("key", sort=True)}
    snap_by = {isin: grp for isin, grp in snap.groupby("isin")} if len(snap) else {}
    nat = np.datetime64("NaT", "D")
    frames = []
    for key in sorted(info):
        k = info[key]
        events = by_key.get(key)
        if events is not None and len(events):
            ev = events.sort_values("date", kind="mergesort")
            e_dates = np.array([np.datetime64(d.date(), "D") for d in pd.to_datetime(ev["date"])])
            cum = np.concatenate([[0.0], np.cumsum(ev["amount_cr"].to_numpy(dtype=float))])
            first_event = e_dates.min()
            has_issue = bool((ev["event"] == "issue").any())
        else:
            e_dates, cum, first_event, has_issue = np.array([], dtype="datetime64[D]"), np.array([0.0]), nat, False

        source = "auctions:sdl" if k["kind"] == SDL else ("auctions:tbill" if k["kind"] == TBILL else "auctions:gsec")
        redeemed_day = k["redeemed"] if k["redeemed"] is not pd.NaT else k["maturity_date"]
        value = np.zeros(len(t))
        method = np.empty(len(t), dtype=object)
        anchor = np.full(len(t), nat)
        iss_ok = np.ones(len(t), dtype=bool)
        ops_ok = np.ones(len(t), dtype=bool)
        matured = np.zeros(len(t), dtype=bool)
        if redeemed_day is not pd.NaT:
            r = np.datetime64(redeemed_day.date(), "D")
            matured = r <= t
            anchor[matured] = r
            method[matured] = "matured_date_unknown" if k["placeholder"] else "matured"
        rest = ~matured
        held = snap_by.get(key)
        if held is not None and rest.any():
            held = held.sort_values("as_of", ascending=False, kind="mergesort")
            s_dates = np.array([np.datetime64(d.date(), "D") for d in held["as_of"]])
            s_vals = held["outstanding_cr"].to_numpy(dtype=float)
            dist = np.abs((t[:, None] - s_dates[None, :]).astype("timedelta64[D]").astype(np.int64))
            pick = dist.argmin(axis=1)
            a_dates, a_vals = s_dates[pick], s_vals[pick]
            v = a_vals + _upto(cum, e_dates, t) - _upto(cum, e_dates, a_dates)
            m = np.where(t <= a_dates, "backward", "forward_from_snapshot").astype(object)
            issue = k["issue_date"]
            before = (t < np.datetime64(issue.date(), "D")) if issue is not pd.NaT else np.zeros(len(t), dtype=bool)
            v = np.where(before, 0.0, v)
            m = np.where(before, "before_issue", m)
            ok_i = before | _covered_many(cov, source, t, a_dates)
            ok_o = before | _covered_many(cov, "operations", t, a_dates)
            value[rest], method[rest], anchor[rest] = v[rest], m[rest], a_dates[rest]
            iss_ok[rest], ops_ok[rest] = ok_i[rest], ok_o[rest]
        elif rest.any():
            v = _upto(cum, e_dates, t)
            value[rest], method[rest] = v[rest], "forward"
            anchor[rest] = first_event
            if not np.isnat(first_event):
                started = rest & (t >= first_event)
                fe = np.full(len(t), first_event)
                ok_i = has_issue & _covered_many(cov, source, fe, t)
                ok_o = _covered_many(cov, "operations", fe, t)
                iss_ok[started], ops_ok[started] = ok_i[started], ok_o[started]
        keep = np.ones(len(t), dtype=bool) if include_zero else np.abs(value) >= 1e-9
        if not keep.any():
            continue
        mat = np.full(len(t), np.datetime64(k["maturity_date"].date(), "D") if k["maturity_date"] is not pd.NaT
                      else nat)
        frames.append(pd.DataFrame({
            "date": t[keep], "key": key, "isin": k["isin"], "kind": k["kind"], "maturity_date": mat[keep],
            "tenor_bucket": _buckets(mat[keep], t[keep]), "outstanding_cr": np.round(value[keep], 6),
            "method": method[keep], "anchor_date": anchor[keep],
            "first_event_date": np.full(int(keep.sum()), first_event), "issuance_complete": iss_ok[keep],
            "operations_complete": ops_ok[keep]}))
    if not frames:
        return pd.DataFrame(columns=out_cols)
    out = pd.concat(frames, ignore_index=True)
    for c in ("date", "maturity_date", "anchor_date", "first_event_date"):
        out[c] = pd.to_datetime(out[c]).astype("datetime64[ns]")
    return out.sort_values(["date", "kind", "maturity_date", "key"], kind="mergesort").reset_index(drop=True)


# --- Supply -------------------------------------------------------------------------------------------------------

SUPPLY_COLUMNS = (
    "period", "period_start", "period_end", "kind", "tenor_bucket", "gross_cr", "gross_dv01", "switch_in_cr",
    "switch_in_dv01", "switch_out_cr", "switch_out_dv01", "buyback_cr", "buyback_dv01", "omo_purchase_cr",
    "omo_purchase_dv01", "omo_sale_cr", "omo_sale_dv01", "redemption_cr", "net_cr", "net_dv01", "market_net_cr",
    "market_net_dv01", "dv01_missing_cr", "stock_date", "stock_cr", "stock_complete_cr", "stock_dv01",
    "stock_no_dv01_cr", "stock_complete", "gross_share", "dv01_share", "auctions_covered", "events",
)
_SUPPLY_PARTS = {  # column prefix -> (event kinds, amount column); signs are the rows' own
    "gross": (AUCTION_EVENT_KINDS, "booked_cr"), "switch_in": (("switch_in",), "booked_cr"),
    "switch_out": (("switch_out",), "booked_cr"), "buyback": (("buyback",), "booked_cr"),
    "omo_purchase": (("omo_purchase",), "accepted_cr"), "omo_sale": (("omo_sale",), "accepted_cr"),
}


def _period(day: pd.Timestamp, freq: str) -> tuple[str, pd.Timestamp, pd.Timestamp]:
    if freq == "week":
        start = _monday(day)
        return f"{start:%Y-%m-%d}", start, start + pd.Timedelta(days=4)
    start = day.replace(day=1)
    return f"{start:%Y-%m}", start, (start + pd.offsets.MonthEnd(0)).normalize()


def _coverage_text(cov: pd.DataFrame, kind: str, start: pd.Timestamp, end: pd.Timestamp) -> str:
    source = "auctions:sdl" if kind == SDL else ("auctions:tbill" if kind in (TBILL, CMB) else "auctions:gsec")
    rows = cov[cov["source"] == source]
    best = "no"
    for s, e in zip(pd.to_datetime(rows["start"]), pd.to_datetime(rows["end"])):
        if s <= start and end <= e:
            return "yes"
        if s <= end and start <= e:
            best = "partly"
    return best


def _stock_dv01(stock: pd.DataFrame, ledger: pd.DataFrame, history: dict, basis: str) -> pd.DataFrame:
    """Each stock row's DV01 (crore per bp) at its key's latest yield public by the end of the stock date, else its
    coupon (``stock_dv01_basis``); NaN where ``bonds`` has no pricer (FRBs), no maturity is known or no yield (a bill
    with no cut-off in the inputs): ``supply_table`` sums the rest and counts that face in ``stock_no_dv01_cr``."""
    coupons = ledger.dropna(subset=["coupon_pct"]).drop_duplicates("key", keep="first").set_index("key")["coupon_pct"]
    bonds: dict[str, object] = {}
    values = []
    for rec in stock.to_dict("records"):
        key, kind = rec["key"], rec["kind"]
        face = float(rec["outstanding_cr"])
        settle = _day(rec["date"]) + pd.Timedelta(days=1)
        maturity = _day(rec["maturity_date"])
        y = math.nan
        if basis == "last_cutoff":
            y, _ = _latest_yield(history, key, _end_of_day(rec["date"]) + pd.Timedelta(seconds=1))
        coupon = _num(coupons.get(key)) if key in coupons.index else math.nan
        if not math.isfinite(y):
            y = coupon
        if kind not in (GSEC, GREEN, SDL, TBILL, CMB) or maturity is pd.NaT or not math.isfinite(y) or \
                settle >= maturity:
            values.append(math.nan)
            continue
        try:
            if kind in (TBILL, CMB):
                dv = DiscountInstrument(maturity.date(), TBILL_CONVENTIONS if kind == TBILL else CMB_CONVENTIONS) \
                    .dv01(y, settle.date())
            else:
                bond = bonds.get(key)
                if bond is None:
                    bond = bonds[key] = FixedRateBond(coupon if math.isfinite(coupon) else y, maturity.date(),
                                                      SDL_CONVENTIONS if kind == SDL else GSEC_CONVENTIONS)
                dv = bond.dv01(y, settle.date())
        except (ValueError, NotImplementedError):
            values.append(math.nan)
            continue
        values.append(face * dv / 100)
    return stock.assign(dv01_cr=values)


def supply_table(events: pd.DataFrame, *, ledger: pd.DataFrame | None = None, snapshot: pd.DataFrame | None = None,
                 period: str | None = None, by_bucket: bool = True, settings: EventSettings = DEFAULT_SETTINGS,
                 start=None, end=None, keep_duplicates: bool = False) -> pd.DataFrame:
    """Supply per period, kind and tenor bucket (B7.3, ``SUPPLY_COLUMNS``), from an event ledger.

    ``period`` (default ``settings.period``): ``auction_week`` (the Monday-Friday week of the auction date),
    ``auction_month``, ``settlement_week`` or ``settlement_month`` (by the ledger's dates: ``check_supply`` compares it
    with ``issuance.supply_summary``). Gross issuance, switches and buybacks at the ledger's booked amounts (rows not
    in the ledger count nothing), OMOs at the face accepted, all with their DV01 (crore per bp); ``dv01_missing_cr``
    is the face whose DV01 is not known (FRBs, no maturity). With ``ledger`` (and an RBI ``snapshot``), the stock at
    the end of the day before each period (``stock_on_dates``) with its DV01 (``_stock_dv01``) and the shares, blank
    unless every security of the cell has a complete history. ``auctions_covered``: whether the ledger's auction
    files cover the period for the kind (``yes``, ``partly``, ``no``). The numbered copies of a ``duplicate_line``
    (``build_event_ledger``) count nothing unless ``keep_duplicates``: not in the gross supply, nor in their security's
    redemption and stock, which the issuance ledger built from the same auction table counts them in.
    """
    _need(events, EVENT_COLUMNS, "the event ledger")
    stock_ledger = ledger
    if not keep_duplicates:
        copies = _duplicate_copies(events)
        if len(copies):
            events = events.drop(index=copies.index).copy()
            # the issuance ledger counts the copies in the security's redemption and stock too: take them out
            extra = _copy_amounts(copies)
            red = (events["kind"] == "redemption") & events["key"].isin(set(extra.index))
            events.loc[red, "booked_cr"] = events.loc[red, "booked_cr"] + events.loc[red, "key"].map(extra)
            if ledger is not None and "auction_id" in ledger:
                ids = {a for a in copies["auction_id"].map(_text) if a}
                stock_ledger = ledger[~ledger["auction_id"].map(_text).isin(ids)]
    period = settings.period if period is None else period
    if period not in PERIODS:
        raise ValueError(f"period must be one of {', '.join(PERIODS)}, not {period!r}")
    basis, freq = period.split("_")
    rows = events[events["kind"].isin(SUPPLY_EVENT_KINDS)].copy()
    if not len(rows):
        return pd.DataFrame(columns=list(SUPPLY_COLUMNS))
    rows = _sorted(rows, ["event_id"])
    when = rows["event_date"] if basis == "auction" else rows["settlement_date"].fillna(rows["event_date"])
    rows = rows[when.notna()]
    when = when[when.notna()]
    lo, hi = _day(start), _day(end)
    keep = pd.Series(True, index=rows.index)
    if lo is not pd.NaT:
        keep &= when >= lo
    if hi is not pd.NaT:
        keep &= when <= hi
    rows, when = rows[keep], when[keep]
    if not len(rows):
        return pd.DataFrame(columns=list(SUPPLY_COLUMNS))
    labels = [_period(d, freq) for d in when]
    rows["period"] = [p for p, _, _ in labels]
    rows["period_start"] = [s for _, s, _ in labels]
    rows["period_end"] = [e for _, _, e in labels]
    rows["kind_"] = rows["supply_kind"].replace("", "unknown")
    rows["bucket_"] = rows["tenor_bucket"].replace("", "unknown") if by_bucket else "all"
    for name, (kinds, column) in _SUPPLY_PARTS.items():
        mine = rows["kind"].isin(kinds)
        rows[f"{name}_cr"] = rows[column].where(mine, 0.0).fillna(0.0)
        rows[f"{name}_dv01"] = rows["dv01_cr_per_bp"].where(mine, 0.0).fillna(0.0)
    rows["redemption_cr"] = rows["booked_cr"].where(rows["kind"] == "redemption", 0.0).fillna(0.0)
    face = rows["booked_cr"].where(rows["kind"] != "omo_purchase", rows["accepted_cr"])
    face = face.where(~rows["kind"].isin(("omo_sale",)), rows["accepted_cr"])
    missing = rows["dv01_cr_per_bp"].isna() & (rows["kind"] != "redemption") & face.notna() & (face != 0)
    rows["dv01_missing_cr"] = face.abs().where(missing, 0.0).fillna(0.0)
    sums = [f"{n}_{p}" for n in _SUPPLY_PARTS for p in ("cr", "dv01")] + ["redemption_cr", "dv01_missing_cr"]
    out = (rows.groupby(["period", "period_start", "period_end", "kind_", "bucket_"], as_index=False, sort=True)
           .agg(**{c: (c, "sum") for c in sums}, events=("event_id", "size"))
           .rename(columns={"kind_": "kind", "bucket_": "tenor_bucket"}))
    out["net_cr"] = out[["gross_cr", "switch_in_cr", "switch_out_cr", "buyback_cr", "redemption_cr"]].sum(axis=1)
    out["net_dv01"] = out[["gross_dv01", "switch_in_dv01", "switch_out_dv01", "buyback_dv01"]].sum(axis=1)
    out["market_net_cr"] = out["net_cr"] - out["omo_purchase_cr"] + out["omo_sale_cr"]
    out["market_net_dv01"] = out["net_dv01"] - out["omo_purchase_dv01"] + out["omo_sale_dv01"]
    out["stock_date"] = out["period_start"] - pd.Timedelta(days=1)
    for c in ("stock_cr", "stock_complete_cr", "stock_dv01", "stock_no_dv01_cr", "gross_share", "dv01_share"):
        out[c] = math.nan
    out["stock_complete"] = pd.Series([pd.NA] * len(out), dtype="boolean")
    out["auctions_covered"] = ""
    if ledger is not None:
        cov = coverage(ledger)
        out["auctions_covered"] = [_coverage_text(cov, k, s, e) for k, s, e in
                                   zip(out["kind"], out["period_start"], out["period_end"])]
        stock = stock_on_dates(stock_ledger, sorted(set(out["stock_date"])), snapshot)
        if len(stock):
            stock = stock.assign(tenor_bucket=stock["tenor_bucket"] if by_bucket else "all")
            cells = out[["stock_date", "kind", "tenor_bucket"]].rename(columns={"stock_date": "date"})
            stock = stock.merge(cells.drop_duplicates(), on=["date", "kind", "tenor_bucket"], how="inner")
            stock = _stock_dv01(stock, ledger, _yield_history(events), settings.stock_dv01_basis)
            stock["complete"] = stock["issuance_complete"] & stock["operations_complete"]
            stock["complete_cr"] = stock["outstanding_cr"].where(stock["complete"], 0.0)
            stock["no_dv01_cr"] = stock["outstanding_cr"].where(stock["dv01_cr"].isna(), 0.0)
            agg = stock.groupby(["date", "kind", "tenor_bucket"], as_index=False, sort=True).agg(
                stock_cr=("outstanding_cr", "sum"), stock_complete_cr=("complete_cr", "sum"),
                stock_dv01=("dv01_cr", "sum"), stock_no_dv01_cr=("no_dv01_cr", "sum"),
                stock_complete=("complete", "all"))
            merged = out.drop(columns=["stock_cr", "stock_complete_cr", "stock_dv01", "stock_no_dv01_cr",
                                       "stock_complete"]).merge(
                agg.rename(columns={"date": "stock_date"}), on=["stock_date", "kind", "tenor_bucket"], how="left")
            merged["stock_complete"] = merged["stock_complete"].astype("boolean")
            full = merged["stock_complete"].fillna(False).astype(bool)
            merged["gross_share"] = (merged["gross_cr"] / merged["stock_cr"]).where(full & (merged["stock_cr"] > 0))
            merged["dv01_share"] = (merged["gross_dv01"] / merged["stock_dv01"]).where(
                full & (merged["stock_dv01"] > 0) & (merged["stock_no_dv01_cr"] == 0))
            out = merged
    out = out.loc[:, list(SUPPLY_COLUMNS)]
    return out.sort_values(["period_start", "kind", "tenor_bucket"], kind="mergesort").reset_index(drop=True)


SUPPLY_CHECK_COLUMNS = ("period", "kind", "tenor_bucket", "column", "events_cr", "ledger_cr", "difference_cr",
                        "status")
_CHECKED = (("gross_cr", "gross_issuance_cr"), ("switch_in_cr", "switch_in_cr"), ("switch_out_cr", "switch_out_cr"),
            ("buyback_cr", "buyback_cr"), ("redemption_cr", "redemption_cr"), ("net_cr", "net_cr"))


def check_supply(events: pd.DataFrame, ledger: pd.DataFrame, *, tolerance: float = 1e-6) -> pd.DataFrame:
    """Identity E1 (B9), cell by cell: the event ledger's supply by settlement month, kind and tenor bucket against
    ``issuance.supply_summary(ledger)`` for gross issuance, switches, buybacks, redemptions and net supply
    (``SUPPLY_CHECK_COLUMNS``; ``status`` ``ok`` within ``tolerance`` crore, else ``differs``)."""
    mine = supply_table(events, period="settlement_month")
    theirs = supply_summary(ledger, freq="M")
    keys = ["period", "kind", "tenor_bucket"]
    left = mine[keys + [m for m, _ in _CHECKED]] if len(mine) else pd.DataFrame(columns=keys + [m for m, _ in
                                                                                               _CHECKED])
    right = theirs[keys + [t for _, t in _CHECKED]].rename(columns={t: f"{t}__ledger" for _, t in _CHECKED}) \
        if len(theirs) else pd.DataFrame(columns=keys + [f"{t}__ledger" for _, t in _CHECKED])
    both = left.merge(right, on=keys, how="outer")
    rows = []
    for rec in _sorted(both, keys).to_dict("records"):
        for mine_col, their_col in _CHECKED:
            a, b = _num(rec.get(mine_col)), _num(rec.get(f"{their_col}__ledger"))
            a, b = (0.0 if not math.isfinite(a) else a), (0.0 if not math.isfinite(b) else b)
            rows.append({"period": rec["period"], "kind": rec["kind"], "tenor_bucket": rec["tenor_bucket"],
                         "column": mine_col, "events_cr": a, "ledger_cr": b, "difference_cr": a - b,
                         "status": "ok" if abs(a - b) <= tolerance else "differs"})
    return pd.DataFrame(rows, columns=list(SUPPLY_CHECK_COLUMNS))


JOIN_COLUMNS = ("auction_id", "ledger_rows", "event_rows", "status")


def check_ledger_join(events: pd.DataFrame, ledger: pd.DataFrame) -> pd.DataFrame:
    """Identity E2 (B9): every ledger issue or reissue row maps to exactly one auction row of the event ledger, and
    every auction row that counts as issuance to exactly one ledger row (``JOIN_COLUMNS``; ``status`` ``ok``,
    ``not_in_ledger``, ``not_in_events`` or ``duplicated``)."""
    _need(events, EVENT_COLUMNS, "the event ledger")
    issuance = ledger[ledger["event"].isin(ISSUANCE_EVENTS)]
    ledger_ids = issuance["auction_id"].map(_text).value_counts()
    auctions = events[events["kind"].isin(AUCTION_EVENT_KINDS)]
    counting = auctions[~_all_flags(auctions).map(lambda f: bool({"not_counted", "result_only", "announced_only"}
                                                                 & f))]
    event_ids = counting["auction_id"].map(_text).value_counts()
    rows = []
    for aid in sorted(set(ledger_ids.index) | set(event_ids.index)):
        n_l, n_e = int(ledger_ids.get(aid, 0)), int(event_ids.get(aid, 0))
        status = "ok" if n_l == 1 and n_e == 1 else ("not_in_ledger" if n_l == 0 else (
            "not_in_events" if n_e == 0 else "duplicated"))
        rows.append({"auction_id": aid, "ledger_rows": n_l, "event_rows": n_e, "status": status})
    return pd.DataFrame(rows, columns=list(JOIN_COLUMNS))


# --- Auction metrics ----------------------------------------------------------------------------------------------

METRIC_COLUMNS = ("event_id", "kind", "event_date", "key", "notified_cr", "bid_cover", "bid_cover_all", "tail_bp",
                  "tail_price", "devolvement_share", "nc_share", "commission_paise", "concession_bp", "pre_bp",
                  "recovery_bp", "basis")


def _panel_lookup(panel: pd.DataFrame | None, column: str, key: str, calendar: BusinessCalendar,
                  panel_dates: str) -> dict:
    """(trade date, key) -> value of ``column`` in a (date, key) panel; a settlement-dated panel (``curvefit``'s
    residual history) is moved to its trade date, the business day before."""
    if panel is None or not len(panel):
        return {}
    _need(panel, ("date", key, column), "the panel")
    if panel_dates not in PANEL_DATES:
        raise ValueError(f"panel_dates must be one of {', '.join(PANEL_DATES)}, not {panel_dates!r}")
    out = {}
    for d, k, v in zip(panel["date"], panel[key], panel[column]):
        day = _day(d)
        if day is pd.NaT or _missing(k) or not math.isfinite(_num(v)):
            continue
        if panel_dates == "settlement":
            day = pd.Timestamp(_shift(calendar, day, -1)[0])
        if (day, _text(k)) in out:
            raise ValueError(f"the panel has two rows for {_text(k)} on {_iso(day)}: give one row per key and date")
        out[(day, _text(k))] = _num(v)
    return out


def auction_metrics(events: pd.DataFrame, *, panel: pd.DataFrame | None = None, panel_column: str = "yield_pct",
                    panel_key: str = "isin", panel_dates: str = "trade", curves: Mapping | None = None,
                    residuals: pd.DataFrame | None = None, residual_column: str = "resid_yield_bp",
                    residual_dates: str = "settlement", calendar: BusinessCalendar | None = None,
                    settings: EventSettings = DEFAULT_SETTINGS) -> pd.DataFrame:
    """Per auction row (``METRIC_COLUMNS``): bid-cover (competitive bids over notified, UNVERIFIED reading; and all
    bids), the tail in yield (cut-off less the weighted average, bp) and in price (weighted average price less
    cut-off), the devolvement share (reported, else inferred from a shortfall), the non-competitive share of the
    booked amount, the underwriting commission (paise per Rs 100).

    With a market ``panel`` (``date``, ``panel_key``, ``panel_column``: licensed prices, e.g. the returns module's
    marks), ``concession_bp = 100 (cut-off - market yield on the business day before)`` for a reissue; for a new
    issue, ``curves`` (trade date -> a ``curvefit`` curve for that day's settlement) give the par yield at its
    maturity. With ``residuals`` (``curvefit``'s residual history, dated by settlement), ``pre_bp = r(t0) -
    r(t0 - k)`` and ``recovery_bp = r(t0 + k) - r(t0)``, ``k = settings.metric_days`` business days.
    """
    _need(events, EVENT_COLUMNS, "the event ledger")
    calendar = mumbai_settlement_calendar() if calendar is None else calendar
    market = _panel_lookup(panel, panel_column, panel_key, calendar, panel_dates)
    resid = _panel_lookup(residuals, residual_column, "isin", calendar, residual_dates)
    rows = []
    k = settings.metric_days
    for rec in _sorted(events[events["kind"].isin(AUCTION_EVENT_KINDS)], ["event_id"]).to_dict("records"):
        notified, booked = _num(rec["notified_cr"]), _num(rec["booked_cr"])
        bids, nc_bids = _num(rec["bids_cr"]), _num(rec["nc_bids_cr"])
        basis = []
        cover = bids / notified if math.isfinite(bids) and notified > 0 else math.nan
        cover_all = (bids + (nc_bids if math.isfinite(nc_bids) else 0.0)) / notified if math.isfinite(cover) \
            else math.nan
        if math.isfinite(cover):
            basis.append("bids: " + ("rbi_result" if "rbi_result" in _text(rec["source"]) or
                                     _text(rec["source"]) == "rbi_release" else "handbook"))
        dev = _num(rec["devolvement_cr"])
        if not (math.isfinite(dev) and dev > 0) and math.isfinite(_num(rec["devolvement_inferred_cr"])):
            dev = _num(rec["devolvement_inferred_cr"])
            basis.append("devolvement inferred")
        cutoff, day = _num(rec["cutoff_yield"]), _day(rec["event_date"])
        concession = math.nan
        key = _text(rec["isin"]) or _text(rec["key"])
        if math.isfinite(cutoff) and day is not pd.NaT and (market or curves):
            prev = pd.Timestamp(_shift(calendar, day, -1)[0])
            if _text(rec["subkind"]) == "new" and curves:
                curve = curves.get(prev) or curves.get(prev.date())
                maturity = _day(rec["maturity_date"])
                if curve is not None and maturity is not pd.NaT:
                    bond = FixedRateBond(cutoff, maturity.date(), SDL_CONVENTIONS if rec["supply_kind"] == SDL
                                         else GSEC_CONVENTIONS)
                    concession = 100 * (cutoff - float(curve.bond_par_yield(bond, curve.valuation_date)))
                    basis.append("concession: curve par yield")
            elif (prev, key) in market:
                concession = 100 * (cutoff - market[(prev, key)])
                basis.append("concession: panel")
        pre = recovery = math.nan
        if resid and day is not pd.NaT:
            r0 = resid.get((day, key))
            before = resid.get((pd.Timestamp(_shift(calendar, day, -k)[0]), key))
            after = resid.get((pd.Timestamp(_shift(calendar, day, k)[0]), key))
            if r0 is not None and before is not None:
                pre = r0 - before
            if r0 is not None and after is not None:
                recovery = after - r0
            if math.isfinite(pre) or math.isfinite(recovery):
                basis.append(f"residuals over {k} business days")
        wap, price = _num(rec["wap"]), _num(rec["cutoff_price"])
        nc = _num(rec["nc_accepted_cr"])
        rows.append({
            "event_id": rec["event_id"], "kind": rec["kind"], "event_date": rec["event_date"], "key": rec["key"],
            "notified_cr": notified, "bid_cover": cover, "bid_cover_all": cover_all, "tail_bp": _num(rec["tail_bp"]),
            "tail_price": wap - price if math.isfinite(wap) and math.isfinite(price) else math.nan,
            "devolvement_share": dev / notified if math.isfinite(dev) and notified > 0 else math.nan,
            "nc_share": nc / booked if math.isfinite(nc) and math.isfinite(booked) and booked > 0 else math.nan,
            "commission_paise": _num(rec["commission_paise"]), "concession_bp": concession, "pre_bp": pre,
            "recovery_bp": recovery, "basis": "; ".join(basis)})
    out = pd.DataFrame(rows, columns=list(METRIC_COLUMNS))
    out["event_date"] = pd.to_datetime(out["event_date"]).astype("datetime64[ns]")
    return out


# --- Forward supply -----------------------------------------------------------------------------------------------

FORWARD_COLUMNS = (
    "as_of", "period", "period_start", "period_end", "kind", "tenor_bucket", "planned_cr", "planned_release",
    "planned_public_from", "announced_cr", "announced_public_from", "realised_notified_cr", "realised_cr",
    "realised_to_date_cr", "realised_dv01", "realised_public_from", "realised_covered", "dv01_per_cr", "planned_dv01",
    "calendar_gap_cr", "calendar_gap_dv01", "notes",
)


def _calendar_bucket(rec: Mapping) -> str:
    """An auction's bucket as a calendar line would hold it: a new issue by its nominal tenor, else its residual
    maturity on settlement (``rbi_store``'s rule for announcements)."""
    if _text(rec["subkind"]) == "new" and rec["supply_kind"] not in (TBILL, CMB):
        tenor = _tenor_years(rec["maturity_date"], rec["settlement_date"])
        if tenor is not None:
            return nominal_tenor_bucket(tenor)
    return _text(rec["tenor_bucket"]) or "unknown"


def forward_supply(events: pd.DataFrame, dates: Iterable, *, horizon_weeks: int = 4,
                   coverage_table: pd.DataFrame | None = None,
                   settings: EventSettings = DEFAULT_SETTINGS) -> pd.DataFrame:
    """Supply as known on each date against what came (B7.3, ``FORWARD_COLUMNS``).

    On each date, at ``settings.decision_time`` (a date; or at the time given, as a Timestamp with a clock time or ISO
    text with a ``T``), for ``horizon_weeks`` auction weeks from that date's own week (the auctions later that week
    count): the weekly calendars' planned amounts public by then (``WEEKLY_CALENDAR_KINDS``: the GoI calendar's and the
    T-bill lines of a user's table; the latest release covering the week replaces earlier ones), by kind and bucket,
    blank (and noted) for a kind no weekly calendar plans; the auctions announced by then whose result was not yet
    public (``announced_cr``); and, known only later, the auctions held (``realised_notified_cr``, ``realised_cr`` at
    the ledger's booked amount, else the amount accepted, with DV01) and the gaps ``realised - planned``.
    ``coverage_table`` (the ledger's, ``issuance.coverage``) says in ``realised_covered`` whether the auction files
    cover the period: where they do not, the realised amounts and the gaps are incomplete. A planned amount is priced in
    DV01 at the trailing DV01 per crore of the bucket's auctions public by then (``dv01_weeks`` weeks, UNVERIFIED). The
    state calendar's quarter totals give rows of ``period`` ``quarter`` for quarters not yet over, with
    ``realised_to_date_cr``, the quarter's auctions whose result was public by then. A calendar never enters a date
    before its own ``public_from`` (identity E5).
    """
    _need(events, EVENT_COLUMNS, "the event ledger")
    if isinstance(horizon_weeks, bool) or not isinstance(horizon_weeks, int) or horizon_weeks < 1:
        raise ValueError(f"horizon_weeks must be a whole number of at least 1, not {horizon_weeks!r}")
    as_ofs = []
    for d in dates:
        w = _when(d)
        if w is pd.NaT:
            raise ValueError(f"forward_supply: {d!r} is not a date")
        has_time = isinstance(d, (dt.datetime, pd.Timestamp)) and (w.hour, w.minute, w.second) != (0, 0, 0) or (
            isinstance(d, str) and "T" in d)
        as_ofs.append(w if has_time else _at(w, settings.decision_time))
    lines = events[events["kind"].isin(WEEKLY_CALENDAR_KINDS)].to_dict("records")
    quarters = events[events["kind"] == "sdl_calendar"].to_dict("records")
    auctions = []
    for rec in events[events["kind"].isin(AUCTION_EVENT_KINDS)].to_dict("records"):
        booked = _num(rec["booked_cr"])
        amount = booked if math.isfinite(booked) else _num(rec["accepted_cr"])
        dv = _num(rec["dv01_cr_per_bp"])
        if not math.isfinite(dv) and math.isfinite(amount) and math.isfinite(_num(rec["dv01_per100"])):
            dv = amount * _num(rec["dv01_per100"]) / 100
        auctions.append(dict(rec, _bucket=_calendar_bucket(rec), _amount=amount, _dv01=dv,
                             _not_in_ledger=not math.isfinite(booked)))
    out = []
    for as_of in sorted(set(as_ofs)):
        week0 = _monday(as_of)
        weeks = [week0 + pd.Timedelta(days=7 * k) for k in range(horizon_weeks)]
        horizon_end = weeks[-1] + pd.Timedelta(days=6)
        # the trailing DV01 per crore by (kind, bucket), from results public by as_of
        trail_from = as_of - pd.Timedelta(weeks=settings.dv01_weeks)
        trail: dict[tuple, list[float]] = defaultdict(lambda: [0.0, 0.0])
        for a in auctions:
            if _when(a["result_public_from"]) is not pd.NaT and a["result_public_from"] <= as_of and \
                    a["event_date"] >= trail_from and math.isfinite(a["_dv01"]) and math.isfinite(a["_amount"]) \
                    and a["_amount"] > 0:
                cell = trail[(a["supply_kind"], a["_bucket"])]
                cell[0] += a["_dv01"]
                cell[1] += a["_amount"]
        cells: dict[tuple, dict] = {}
        planned_weeks: set[tuple] = set()  # (week, kind) a calendar public by then plans
        for week in weeks:
            friday = week + pd.Timedelta(days=4)
            known = [r for r in lines if _when(r["announcement_public_from"]) is not pd.NaT and
                     r["announcement_public_from"] <= as_of and r["period_start"] <= friday and r["period_end"] >= week]
            for kind in sorted({r["supply_kind"] for r in known}):
                planned_weeks.add((week, kind))
                mine = [r for r in known if r["supply_kind"] == kind]
                latest = max(mine, key=lambda r: (r["announcement_public_from"], r["source_ref"]))
                release = _text(latest["source_ref"]).split(" line")[0]
                for r in mine:
                    if _text(r["source_ref"]).split(" line")[0] != release:
                        continue
                    c = _forward_cell(cells, as_of, "week", week, friday, kind, _text(r["tenor_bucket"]) or "unknown")
                    c["planned_cr"] = (c["planned_cr"] if math.isfinite(c["planned_cr"]) else 0.0) + (
                        _num(r["notified_cr"]) if math.isfinite(_num(r["notified_cr"])) else 0.0)
                    c["planned_release"], c["planned_public_from"] = release, latest["announcement_public_from"]
            for a in auctions:
                if not (week <= a["event_date"] <= week + pd.Timedelta(days=6)) or not _forward_counts(a, as_of):
                    continue
                c = _forward_cell(cells, as_of, "week", week, friday, a["supply_kind"], a["_bucket"])
                _forward_add(c, a, as_of)
        for q in quarters:
            if _when(q["announcement_public_from"]) is pd.NaT or q["announcement_public_from"] > as_of:
                continue
            start, end = q["period_start"], q["period_end"]
            if start is pd.NaT or end is pd.NaT or end < as_of.normalize() or start > horizon_end:
                continue
            c = _forward_cell(cells, as_of, "quarter", start, end, q["supply_kind"] or SDL, "all")
            c["planned_cr"] = (c["planned_cr"] if math.isfinite(c["planned_cr"]) else 0.0) + (
                _num(q["notified_cr"]) if math.isfinite(_num(q["notified_cr"])) else 0.0)
            c["planned_release"] = _text(q["source_ref"]).split(" line")[0]
            c["planned_public_from"] = q["announcement_public_from"]
            for a in auctions:
                if a["supply_kind"] == (q["supply_kind"] or SDL) and start <= a["event_date"] <= end and \
                        _forward_counts(a, as_of):
                    _forward_add(c, a, as_of)
        cov = coverage_table if coverage_table is not None else pd.DataFrame(columns=["source", "start", "end"])
        for c in cells.values():
            c["realised_covered"] = _coverage_text(cov, c["kind"], c["period_start"], c["period_end"]) \
                if coverage_table is not None else ""
            if c["realised_covered"] in ("no", "partly"):
                c["_notes"].add("the auction files do not cover the whole period: realised amounts incomplete")
            if c["period"] == "week" and not math.isfinite(c["planned_cr"]):
                if (c["period_start"], c["kind"]) in planned_weeks:
                    c["planned_cr"] = 0.0  # the week's calendar plans nothing in this bucket
                else:
                    c["_notes"].add(f"no weekly calendar of {c['kind']} auctions in the inputs (the GoI calendar "
                                    "covers dated securities only; RBI's T-bill calendar has no parser, its lines "
                                    "can be given as a table of your own; the state calendar gives quarter "
                                    "totals): planned and the gaps left blank")
            per = trail.get((c["kind"], c["tenor_bucket"]))
            rate = per[0] / per[1] if per is not None and per[1] > 0 else math.nan
            planned = c["planned_cr"]
            c["dv01_per_cr"] = rate
            c["planned_dv01"] = planned * rate if math.isfinite(rate) and math.isfinite(planned) else math.nan
            c["calendar_gap_cr"] = c["realised_cr"] - planned if math.isfinite(planned) else math.nan
            c["calendar_gap_dv01"] = c["realised_dv01"] - c["planned_dv01"] if math.isfinite(c["planned_dv01"]) \
                else math.nan
            c["notes"] = _join(sorted(c.pop("_notes")))
            out.append(c)
    frame = pd.DataFrame(out, columns=list(FORWARD_COLUMNS))
    for col in ("as_of", "planned_public_from", "announced_public_from", "realised_public_from"):
        frame[col] = pd.to_datetime(frame[col]).astype("datetime64[ns]")
    for col in ("period_start", "period_end"):
        frame[col] = pd.to_datetime(frame[col]).astype("datetime64[ns]")
    return _sorted(frame, ["as_of", "period_start", "period", "kind", "tenor_bucket"]) if len(frame) else frame


def _forward_cell(cells: dict, as_of: pd.Timestamp, period: str, start, end, kind: str, bucket: str) -> dict:
    """The forward-supply row of a period, kind and bucket as of ``as_of``, made empty on first use."""
    key = (period, start, kind, bucket)
    if key not in cells:
        cells[key] = {"as_of": as_of, "period": period, "period_start": start, "period_end": end, "kind": kind,
                      "tenor_bucket": bucket, "planned_cr": math.nan, "planned_release": "",
                      "planned_public_from": pd.NaT,
                      "announced_cr": 0.0, "announced_public_from": pd.NaT, "realised_notified_cr": 0.0,
                      "realised_cr": 0.0, "realised_to_date_cr": 0.0, "realised_dv01": 0.0,
                      "realised_public_from": pd.NaT, "_notes": set()}
    return cells[key]


def _forward_counts(a: Mapping, as_of: pd.Timestamp) -> bool:
    """Whether an auction row adds to a forward cell: held (a result in the inputs) or announced by ``as_of``."""
    if "announced_only" in _text(a["flags"]).split(";"):
        return _when(a["announcement_public_from"]) is not pd.NaT and a["announcement_public_from"] <= as_of
    return True


def _forward_add(c: dict, a: Mapping, as_of: pd.Timestamp) -> None:
    announced = _when(a["announcement_public_from"]) is not pd.NaT and a["announcement_public_from"] <= as_of
    resulted = _when(a["result_public_from"]) is not pd.NaT and a["result_public_from"] <= as_of
    if announced and not resulted:
        c["announced_cr"] += _num(a["notified_cr"]) if math.isfinite(_num(a["notified_cr"])) else 0.0
        if c["announced_public_from"] is pd.NaT or a["announcement_public_from"] > c["announced_public_from"]:
            c["announced_public_from"] = a["announcement_public_from"]
    if _text(a["flags"]) and "announced_only" in _text(a["flags"]).split(";"):
        c["_notes"].add("announced auctions without a result in the inputs count as not held")
        return
    c["realised_notified_cr"] += _num(a["notified_cr"]) if math.isfinite(_num(a["notified_cr"])) else 0.0
    c["realised_cr"] += a["_amount"] if math.isfinite(a["_amount"]) else 0.0
    c["realised_dv01"] += a["_dv01"] if math.isfinite(a["_dv01"]) else 0.0
    if resulted:
        c["realised_to_date_cr"] += a["_amount"] if math.isfinite(a["_amount"]) else 0.0
    if a["_not_in_ledger"]:
        c["_notes"].add("auctions not in the ledger at the amount accepted")
    if _when(a["result_public_from"]) is not pd.NaT and (c["realised_public_from"] is pd.NaT or
                                                        a["result_public_from"] > c["realised_public_from"]):
        c["realised_public_from"] = a["result_public_from"]


# --- Event windows ------------------------------------------------------------------------------------------------

WINDOW_COLUMNS = ("event_id", "kind", "event_date", "isin", "key", "offset", "date", "day0", "anchor",
                  "anchor_public_from", "others_day0", "calendar_projected")
OVERLAP_COLUMNS = ("event_id", "kind", "day0", "other_event_id", "other_kind", "other_offset", "other_day0",
                   "sibling")
ISOLATE_COLUMNS = ("event_id", "kind", "day0", "isolated", "others_within", "nearest_event_id", "nearest_kind",
                   "nearest_offset", "radius")


class _BusinessDays:
    """The business days of a span on a calendar, projected on its lenient copy beyond its years when allowed."""

    def __init__(self, calendar: BusinessCalendar, start: pd.Timestamp, end: pd.Timestamp, project: bool):
        lenient = calendar.lenient()
        self.days = np.array([np.datetime64(d, "D") for d in lenient.business_days(start.date(), end.date())])
        self.covered = np.array([calendar.covers(pd.Timestamp(d).date()) for d in self.days], dtype=bool)
        self.project = project

    def index_on_or_after(self, day: pd.Timestamp) -> int:
        return int(np.searchsorted(self.days, np.datetime64(day.date(), "D"), side="left"))

    def ordinal(self, day: pd.Timestamp) -> int | None:
        i = self.index_on_or_after(day)
        return i if i < len(self.days) and self.days[i] == np.datetime64(day.date(), "D") else None


def _anchor_time(rec: Mapping, anchor: str) -> tuple[pd.Timestamp, str]:
    """The anchor's time and the anchor used: ``result`` is a calendar line's release (it has no result) and a
    redemption's own day (its amount is final earlier, but the payment is the event)."""
    if anchor == "result" and rec["kind"] in CALENDAR_KINDS:
        anchor = "announcement"
    elif anchor == "result" and rec["kind"] == "redemption":
        anchor = "event_date"
    if anchor == "result":
        return _when(rec["result_public_from"]), anchor
    if anchor == "announcement":
        return _when(rec["announcement_public_from"]), anchor
    day = _day(rec["event_date"] if anchor == "event_date" else rec["settlement_date"])
    return (day if day is not pd.NaT else pd.NaT), anchor


def event_windows(events: pd.DataFrame, *, calendar: BusinessCalendar | None = None, pre: int = 5, post: int = 10,
                  anchor: str = "result", kinds: Sequence[str] | None = None, project_calendar: bool = False,
                  settings: EventSettings = DEFAULT_SETTINGS) -> pd.DataFrame:
    """Business-day windows around each event (B8, ``WINDOW_COLUMNS``): offsets ``-pre`` to ``+post``.

    Day 0 is the first Mumbai business day whose decision time (``settings.decision_time``, 20:00, UNVERIFIED) is at or
    after the anchor's time: ``result`` (``result_public_from``, the default; for a calendar line, which has no result,
    its release, and for a redemption its own day: ``anchor`` says which was used), ``announcement``, ``event_date``
    or ``settlement`` (that day itself when it is a business day). ``others_day0`` counts the other events of the frame
    whose day 0 is that date (``overlaps`` names them). An event without the anchor's time is left out; so is one whose
    window falls in a year the calendar's holiday list does not cover, unless ``project_calendar`` (weekdays as
    business days there, flagged ``calendar_projected``). ``attrs["skipped"]`` counts both and
    ``attrs["skipped_ids"]`` names them.
    """
    _need(events, EVENT_COLUMNS, "the event ledger")
    if anchor not in ANCHORS:
        raise ValueError(f"anchor must be one of {', '.join(ANCHORS)}, not {anchor!r}")
    for name, value in (("pre", pre), ("post", post)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a whole number of business days, 0 or more, not {value!r}")
    calendar = mumbai_settlement_calendar() if calendar is None else calendar
    rows_in = events if kinds is None else events[events["kind"].isin(list(kinds))]
    skipped: dict[str, list[str]] = {"no_anchor": [], "beyond_calendar": []}
    anchors = []
    for rec in _sorted(rows_in, ["event_id"]).to_dict("records"):
        when, used = _anchor_time(rec, anchor)
        if when is pd.NaT:
            skipped["no_anchor"].append(rec["event_id"])
            continue
        anchors.append((rec, when, used))
    out_cols = list(WINDOW_COLUMNS)

    def finish(frame: pd.DataFrame) -> pd.DataFrame:
        frame.attrs["skipped"] = {k: len(v) for k, v in skipped.items()}
        frame.attrs["skipped_ids"] = {k: tuple(v) for k, v in skipped.items()}
        return frame

    if not anchors:
        return finish(pd.DataFrame(columns=out_cols))
    lo = min(w for _, w, _ in anchors).normalize() - pd.Timedelta(days=2 * pre + 31)
    hi = max(w for _, w, _ in anchors).normalize() + pd.Timedelta(days=2 * post + 31)
    bdays = _BusinessDays(calendar, lo, hi, project_calendar)
    picked = []
    for rec, when, used in anchors:
        i = _day0_index(bdays.days, when, settings.decision_time)
        first, last = i - pre, i + post
        if first < 0 or last >= len(bdays.days):
            skipped["beyond_calendar"].append(rec["event_id"])
            continue
        projected = not bool(bdays.covered[first:last + 1].all())
        if projected and not project_calendar:
            skipped["beyond_calendar"].append(rec["event_id"])
            continue
        picked.append((rec, when, used, i))
    day0_count: dict[np.datetime64, int] = defaultdict(int)
    for _, _, _, i in picked:
        day0_count[bdays.days[i]] += 1
    rows = []
    for rec, when, used, i in picked:
        day0 = bdays.days[i]
        for k in range(-pre, post + 1):
            d = bdays.days[i + k]
            rows.append({"event_id": rec["event_id"], "kind": rec["kind"], "event_date": rec["event_date"],
                         "isin": rec["isin"], "key": rec["key"], "offset": k, "date": d, "day0": day0,
                         "anchor": used, "anchor_public_from": when,
                         "others_day0": day0_count.get(d, 0) - (1 if k == 0 else 0),
                         "calendar_projected": not bool(bdays.covered[i + k])})
    frame = pd.DataFrame(rows, columns=out_cols)
    for c in ("event_date", "date", "day0"):
        frame[c] = pd.to_datetime(frame[c]).astype("datetime64[ns]")
    frame["anchor_public_from"] = pd.to_datetime(frame["anchor_public_from"]).astype("datetime64[ns]")
    return finish(frame)


# Kinds that are lines of one event when they share a date: the securities of one GoI auction (with its
# underwriting), of one SDL or T-bill auction, the legs of one switch, the lines of one calendar.
_FAMILIES = {"gsec_auction": "goi_auction", "green_auction": "goi_auction", "frb_auction": "goi_auction",
             "underwriting": "goi_auction", "cmb_auction": "tbill_auction", "switch_in": "switch",
             "switch_out": "switch"}


def overlaps(windows: pd.DataFrame, *, kinds: Sequence[str] | None = None) -> pd.DataFrame:
    """For each event of ``windows``, the other events whose day 0 falls in its window, with that offset (B8,
    ``OVERLAP_COLUMNS``). ``kinds`` keeps only others of those kinds. ``sibling`` marks another line of the same
    event: the same date and the same auction family (the securities of one GoI auction, green bonds, FRBs and its
    underwriting included; of one SDL or T-bill auction; the legs of one switch; the lines of one calendar)."""
    _need(windows, WINDOW_COLUMNS, "the windows")
    if not len(windows):
        return pd.DataFrame(columns=list(OVERLAP_COLUMNS))
    zero = windows[windows["offset"] == 0][["event_id", "kind", "event_date", "date"]].rename(
        columns={"event_id": "other_event_id", "kind": "other_kind", "event_date": "other_event_date",
                 "date": "other_day0"})
    if kinds is not None:
        zero = zero[zero["other_kind"].isin(list(kinds))]
    days0 = windows[windows["offset"] == 0][["event_id", "date"]].rename(columns={"date": "day0"})
    mine = windows[["event_id", "kind", "event_date", "offset", "date"]].merge(days0, on="event_id")
    both = mine.merge(zero, left_on="date", right_on="other_day0")
    both = both[both["other_event_id"] != both["event_id"]]
    family, other_family = both["kind"].map(lambda k: _FAMILIES.get(k, k)), both["other_kind"].map(
        lambda k: _FAMILIES.get(k, k))
    both = both.assign(other_offset=both["offset"],
                       sibling=(family == other_family) & (both["event_date"] == both["other_event_date"]))
    out = both.loc[:, list(OVERLAP_COLUMNS)]
    return _sorted(out, ["event_id", "other_offset", "other_event_id"]) if len(out) else out.reset_index(drop=True)


def isolate(windows: pd.DataFrame, overlap: pd.DataFrame, *, exclude_kinds: Sequence[str] = (), radius: int = 3,
            ignore_siblings: bool = True) -> pd.DataFrame:
    """Whether each event has no other event's day 0 within ``radius`` business days of its own (B8,
    ``ISOLATE_COLUMNS``), other events of ``exclude_kinds`` (daily liquidity operations, say) and, by default,
    its siblings left aside. ``radius`` must fit inside the windows (``pre`` and ``post`` at least ``radius``)."""
    _need(windows, WINDOW_COLUMNS, "the windows")
    _need(overlap, OVERLAP_COLUMNS, "the overlaps")
    if isinstance(radius, bool) or not isinstance(radius, int) or radius < 0:
        raise ValueError(f"radius must be a whole number of business days, 0 or more, not {radius!r}")
    if len(windows):
        reach = windows.groupby("event_id")["offset"].agg(["min", "max"])
        short = reach[(reach["min"] > -radius) | (reach["max"] < radius)]
        if len(short):
            raise ValueError(f"radius {radius} reaches beyond the windows of {len(short)} events (e.g. "
                             f"{short.index[0]}): build the windows with pre and post of at least {radius}")
    near = overlap[overlap["other_offset"].abs() <= radius]
    near = near[~near["other_kind"].isin(list(exclude_kinds))]
    if ignore_siblings:
        near = near[~near["sibling"].astype(bool)]
    by_event = {e: g for e, g in near.groupby("event_id")}
    rows = []
    for rec in windows[windows["offset"] == 0].to_dict("records"):
        g = by_event.get(rec["event_id"])
        if g is None or not len(g):
            rows.append({"event_id": rec["event_id"], "kind": rec["kind"], "day0": rec["date"], "isolated": True,
                         "others_within": 0, "nearest_event_id": "", "nearest_kind": "", "nearest_offset": math.nan,
                         "radius": radius})
            continue
        g = g.assign(_d=g["other_offset"].abs()).sort_values(["_d", "other_offset", "other_event_id"],
                                                               kind="mergesort")
        first = g.iloc[0]
        rows.append({"event_id": rec["event_id"], "kind": rec["kind"], "day0": rec["date"], "isolated": False,
                     "others_within": int(len(g)), "nearest_event_id": first["other_event_id"],
                     "nearest_kind": first["other_kind"], "nearest_offset": float(first["other_offset"]),
                     "radius": radius})
    out = pd.DataFrame(rows, columns=list(ISOLATE_COLUMNS))
    return _sorted(out, ["event_id"]) if len(out) else out


def attach_panel(windows: pd.DataFrame, panel: pd.DataFrame, *, columns: Sequence[str], key: str | None = "isin",
                 window_key: str = "isin", tolerance: int = 0, panel_dates: str = "trade", ref_offset: int = -1,
                 calendar: BusinessCalendar | None = None, project_calendar: bool = False) -> pd.DataFrame:
    """The windows with a later panel's values on each window date (B8): the price or yield path around each event.

    ``panel``: one row per ``date`` and ``key`` (ISIN by default; ``key=None`` for a panel with one row per date, a
    curve's par yields by tenor, say), holding ``columns``: the returns module's marks (``clean_price``,
    ``yield_pct``), ``price_panel``'s rows (``yield_pct``, ``spread_bps``), or ``curvefit``'s residual history
    (``resid_yield_bp``; dated by settlement: ``panel_dates="settlement"`` moves each row to its trade date, the
    business day before). ``window_key`` is the window column matched to ``key`` (``isin``; ``key`` for the ledger's
    keys). Nothing is interpolated: a day without a row stays empty, unless ``tolerance`` business days lets the
    nearest row on the same side of day 0 stand in (``event_prices``' rule), recorded in ``panel_date`` and
    ``panel_gap_days``. ``panel_match`` says ``exact``, ``nearest``, ``missing`` or ``no_key``; count it.
    ``<column>_chg`` is the value less the event's value at ``ref_offset`` (the day before day 0 by default).
    """
    _need(windows, WINDOW_COLUMNS, "the windows")
    if isinstance(tolerance, bool) or not isinstance(tolerance, int) or tolerance < 0:
        raise ValueError(f"tolerance must be a whole number of business days, 0 or more, not {tolerance!r}")
    if panel_dates not in PANEL_DATES:
        raise ValueError(f"panel_dates must be one of {', '.join(PANEL_DATES)}, not {panel_dates!r}")
    columns = list(columns)
    if not columns:
        raise ValueError("attach_panel needs at least one panel column")
    _need(panel, ["date", *([key] if key else []), *columns], "the panel")
    if key is not None and window_key not in windows:
        raise ValueError(f"the windows have no column {window_key!r} to match the panel's {key!r}")
    calendar = mumbai_settlement_calendar() if calendar is None else calendar
    out = windows.copy()
    for c in ("panel_date", "panel_gap_days", "panel_match", *columns, *(f"{c}_chg" for c in columns)):
        out[c] = None
    if not len(out):
        return out
    pdays = pd.to_datetime(panel["date"]).dt.normalize()
    lo = min(pdays.min(), out["date"].min()) - pd.Timedelta(days=40)
    hi = max(pdays.max(), out["date"].max()) + pd.Timedelta(days=40)
    bdays = _BusinessDays(calendar, lo, hi, project_calendar)
    keys = panel[key].map(_text) if key else pd.Series("", index=panel.index)
    values: dict[str, dict[int, tuple[pd.Timestamp, list]]] = defaultdict(dict)
    dropped = 0
    for d, k, vals in zip(pdays, keys, panel[columns].itertuples(index=False, name=None)):
        if d is pd.NaT:
            dropped += 1
            continue
        if panel_dates == "settlement":
            i = bdays.ordinal(d)
            d = pd.Timestamp(bdays.days[i - 1]) if i is not None and i > 0 else pd.NaT
            if d is pd.NaT:
                dropped += 1
                continue
        i = bdays.ordinal(d)
        if i is None:
            dropped += 1
            continue
        if i in values[k]:
            raise ValueError(f"the panel has two rows for {k or 'the date'} on {_iso(d)}: give one row per "
                             "key and date")
        values[k][i] = (d, list(vals))
    sorted_ords = {k: np.array(sorted(v)) for k, v in values.items()}
    match, pdate, gaps, filled = [], [], [], []
    for rec in out[["date", "day0", window_key if key else "date"]].itertuples(index=False, name=None):
        d, day0, kval = rec[0], rec[1], (_text(rec[2]) if key else "")
        if key and not kval:
            match.append("no_key"), pdate.append(pd.NaT), gaps.append(math.nan), filled.append([math.nan] * len(
                columns))
            continue
        i, i0 = bdays.ordinal(d), bdays.ordinal(day0)
        mine = values.get(kval, {})
        if i is not None and i in mine:
            match.append("exact"), pdate.append(mine[i][0]), gaps.append(0.0), filled.append(mine[i][1])
            continue
        hit = None
        if tolerance and i is not None and kval in sorted_ords and i0 is not None:
            ords = sorted_ords[kval]
            near = ords[(np.abs(ords - i) <= tolerance) & ((ords < i0) if i < i0 else (ords >= i0))]
            if len(near):
                hit = int(near[np.lexsort((near, np.abs(near - i)))][0])
        if hit is not None:
            match.append("nearest"), pdate.append(mine[hit][0]), gaps.append(float(hit - i)), filled.append(
                mine[hit][1])
        else:
            match.append("missing"), pdate.append(pd.NaT), gaps.append(math.nan), filled.append([math.nan] * len(
                columns))
    out["panel_match"], out["panel_date"], out["panel_gap_days"] = match, pd.to_datetime(pdate), gaps
    grid = np.array(filled, dtype=float).reshape(len(out), len(columns))
    for j, c in enumerate(columns):
        out[c] = grid[:, j]
    ref = out[out["offset"] == ref_offset].set_index("event_id")
    for c in columns:
        base = out["event_id"].map(ref[c]) if len(ref) else pd.Series(math.nan, index=out.index)
        out[f"{c}_chg"] = out[c] - base.astype(float)
    out["panel_date"] = pd.to_datetime(out["panel_date"]).astype("datetime64[ns]")
    out.attrs["dropped_panel_rows"] = dropped
    return out


# --- What the inputs lack -----------------------------------------------------------------------------------------

GAP_COLUMNS = ("source", "start", "end", "detail")


def input_gaps(events: pd.DataFrame | None = None, *, ledger: pd.DataFrame | None = None,
               tables: Mapping[str, pd.DataFrame] | None = None, meetings: pd.DataFrame | None = None,
               index_events_given: bool = True) -> pd.DataFrame:
    """What the inputs do not cover (``GAP_COLUMNS``): the periods between the ledger's auction files, switches and
    buybacks without declared coverage, the RBI store's possible gaps, results and announcements the auction table
    lacks, times that are rules, the MPC calendar's years, a missing index-event table."""
    rows: list[dict] = []
    if ledger is not None:
        cov = coverage(ledger)
        for source in sorted(set(cov["source"])):
            spans = cov[cov["source"] == source].sort_values("start")
            starts, ends = list(pd.to_datetime(spans["start"])), list(pd.to_datetime(spans["end"]))
            rows.append({"source": source, "start": starts[0], "end": ends[-1],
                         "detail": f"auctions seen in {len(starts)} period(s)"})
            for e, s in zip(ends[:-1], starts[1:]):
                rows.append({"source": source, "start": e + pd.Timedelta(days=1), "end": s - pd.Timedelta(days=1),
                             "detail": "no auction in the inputs: the files miss this period, so its supply and the "
                                       "stock after it are incomplete"})
        if "operations" not in set(cov["source"]):
            rows.append({"source": "operations", "start": pd.NaT, "end": pd.NaT,
                         "detail": "no switch or buyback coverage declared (issuance.build_ledger operations_from): "
                                   "switches and buybacks outside the releases given are missing, the stock is "
                                   "incomplete"})
    store_gaps = (tables or {}).get("gaps")
    if isinstance(store_gaps, pd.DataFrame) and len(store_gaps):
        for rec in store_gaps.to_dict("records"):
            first, last = rec.get("first"), rec.get("last")
            rows.append({"source": f"rbi_store:{_text(rec.get('gap'))}", "start": pd.NaT, "end": pd.NaT,
                         "detail": f"{_text(first)} to {_text(last)}: {_text(rec.get('count'))} possible gap(s)"
                                   + (" inside the poller's span" if _text(rec.get("in_feed_span")) in ("True", "true")
                                      else "")})
    if events is not None and len(events):
        flags = _all_flags(events)
        for flag, detail in (("result_only", "results in the RBI tables with no auction row: rebuild the auction "
                                             "table with these releases (cr-strat auctions --releases)"),
                             ("announced_only", "auctions announced with no result in the inputs"),
                             ("duplicate_line", "duplicate auction lines, one security twice on one day under "
                                                "different auction ids (a line repeated in the auction table?): the "
                                                "numbered copies are left out of the supply table, and the issuance "
                                                "ledger built from that table counts both")):
            mine = events[flags.map(lambda s, f=flag: f in s)]
            for kind, g in mine.groupby("kind"):
                what = detail if kind != "redemption" else (
                    "redemptions of securities with a duplicated auction line: the issuance ledger's amount counts "
                    "the numbered copies, which the supply table takes out of the redemption and the stock")
                rows.append({"source": f"events:{flag}", "start": g["event_date"].min(), "end": g["event_date"].max(),
                             "detail": f"{len(g)} {kind} rows: {what}"})
        for side in ("announcement", "result"):
            ruled = events[events[f"{side}_estimated"].fillna(False).astype(bool)]
            for (kind, basis), g in ruled.groupby(["kind", f"{side}_basis"]):
                rows.append({"source": f"times:{side}", "start": g["event_date"].min(), "end": g["event_date"].max(),
                             "detail": f"{len(g)} {kind} {side} times by rule {basis}: "
                                       f"{TIME_BASES[basis][1]}"})
    if meetings is not None and len(meetings):
        covered = meetings.attrs.get("covered_until")
        rows.append({"source": "mpc", "start": _day(meetings["decision_date"].min()), "end": _day(covered),
                     "detail": f"{len(meetings)} MPC meetings: earlier decisions need a meetings file of your own "
                               "(policy_path's layout)"})
    if not index_events_given:
        rows.append({"source": "index_events", "start": pd.NaT, "end": pd.NaT,
                     "detail": "no index-event table given (none ships: data/index_events_example.csv is an "
                               "example)"})
    out = pd.DataFrame(rows, columns=list(GAP_COLUMNS))
    for c in ("start", "end"):
        out[c] = pd.to_datetime(out[c]).astype("datetime64[ns]")
    return out


# --- Reading and writing ------------------------------------------------------------------------------------------

def read_store_tables(store: str | Path) -> dict[str, pd.DataFrame]:
    """The RBI store's tables the event ledger reads (``rbi_store.read_table``): the release kinds, the derived
    tables, ``releases`` and ``gaps``; tables the store has not written are left out."""
    from cr_strat import rbi_store
    from cr_strat.rbi_releases import COLUMNS

    root = Path(store)
    if not root.is_dir():
        raise FileNotFoundError(f"{root}: no such store directory")
    out = {}
    for name in (*COLUMNS, *rbi_store.DERIVED_COLUMNS, "releases", "gaps"):
        try:
            out[name] = rbi_store.read_table(root, name)
        except FileNotFoundError:
            continue
    return out


def merge_tables(*sources: Mapping[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Several sources' tables as one mapping (``read_store_tables``' and ``auctions.read_release_tables``', say): a
    table two sources hold is joined, leaving out the rows of a release an earlier source already gives, so the first
    source's copy of a release is the one kept; a table without ``release_id`` keeps each distinct row once."""
    out: dict[str, pd.DataFrame] = {}
    for source in sources:
        for name in sorted(source):
            frame, mine = source[name], out.get(name)
            if mine is None or not len(mine):
                out[name] = frame
            elif not len(frame):
                continue
            elif "release_id" in frame and "release_id" in mine:
                seen = set(pd.to_numeric(mine["release_id"], errors="coerce").dropna())
                new = frame[~pd.to_numeric(frame["release_id"], errors="coerce").isin(seen)]
                if len(new):
                    out[name] = pd.concat([mine, new], ignore_index=True)
            else:
                out[name] = pd.concat([mine, frame], ignore_index=True).drop_duplicates(ignore_index=True)
    return out


def _text_frame(frame: pd.DataFrame, times: Iterable[str] = ()) -> pd.DataFrame:
    """``frame`` with dates as ``YYYY-MM-DD``, the ``times`` columns as ``YYYY-MM-DDTHH:MM:SS``, booleans as
    ``True``/``False``, missing values blank: the stores' CSV forms."""
    out = frame.copy()
    times = set(times)
    for c in out.columns:
        col = out[c]
        if pd.api.types.is_datetime64_any_dtype(col):
            fmt = "%Y-%m-%dT%H:%M:%S" if c in times else "%Y-%m-%d"
            out[c] = col.dt.strftime(fmt).fillna("")
        elif isinstance(col.dtype, pd.BooleanDtype):
            out[c] = col.map(lambda v: "" if pd.isna(v) else str(bool(v)))
    return out


def write_table(frame: pd.DataFrame, path: str | Path, *, times: Iterable[str] = ()) -> Path:
    """Write a frame of this module as CSV (``_text_frame``'s forms, ``\\n`` line ends); the same frame gives the
    same bytes."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _text_frame(frame, times).to_csv(path, index=False, lineterminator="\n")
    return path


def write_events(events: pd.DataFrame, path: str | Path) -> Path:
    """Write an event ledger (``EVENT_COLUMNS``) as CSV, times with their clock time."""
    _need(events, EVENT_COLUMNS, "the event ledger")
    return write_table(events.loc[:, list(EVENT_COLUMNS)], path, times=TIME_FIELDS)


def read_events(path: str | Path) -> pd.DataFrame:
    """An event ledger CSV written by ``write_events`` (or ``cr-strat event-ledger``), typed as the builders type it."""
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = [c for c in EVENT_COLUMNS if c not in frame]
    if missing:
        raise ValueError(f"{path}: not an event ledger (missing columns {missing})")
    rows = frame.loc[:, list(EVENT_COLUMNS)].to_dict("records")
    for rec in rows:
        for c in BOOL_FIELDS:
            rec[c] = {"True": True, "False": False}.get(rec[c])
    return _events_frame(rows)


__all__ = [
    "ANCHORS", "ANNOUNCEMENT_RULES", "AUCTION_EVENT_KINDS", "AUCTION_KINDS", "BID_COVER_BASES", "BOOL_FIELDS",
    "CALENDAR_KINDS", "CALENDAR_LINE_COLUMNS", "COUPON_AUCTION_KINDS", "DATE_FIELDS", "DEFAULT_SETTINGS",
    "EVENT_COLUMNS", "EVENT_KINDS", "FLOAT_FIELDS", "FORWARD_COLUMNS", "GAP_COLUMNS", "INT_FIELDS", "ISOLATE_COLUMNS",
    "JOIN_COLUMNS", "METRIC_COLUMNS", "MPC_DECISION_TIMES", "OVERLAP_COLUMNS", "PANEL_DATES", "PERIODS",
    "RBI_FIXED_TIMES", "RESULT_FIELDS", "RESULT_RULES", "STOCK_COLUMNS", "STOCK_DV01_BASES", "SUPPLY_CHECK_COLUMNS",
    "SUPPLY_COLUMNS", "SUPPLY_EVENT_KINDS", "TIME_BASES", "TIME_FIELDS", "WEEKLY_CALENDAR_KINDS", "WINDOW_COLUMNS",
    "EventSettings", "attach_decision_times", "attach_panel", "auction_events", "auction_metrics", "build_event_ledger",
    "buyback_events", "calendar_events", "check_ledger_join", "check_supply", "collect_events", "decision_day",
    "empty_events", "estimated_announcement", "event_windows", "events_as_of", "forward_supply", "index_event_rows",
    "input_gaps", "isolate", "line_dv01", "macro_events", "merge_tables", "mpc_decision_time", "new_issue_key",
    "operation_events", "overlaps", "policy_events", "read_calendar_lines", "read_events", "read_store_tables",
    "redemption_events", "stock_on_dates", "supply_table", "switch_events", "write_events", "write_table",
]
