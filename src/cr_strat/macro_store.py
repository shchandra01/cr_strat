"""A store of macro and global series: every value ever seen, from when it was public, and tables built from them.

Build-order item 5 of ``docs/rates_data_sources.md``, for S7 (the policy path against OIS: CPI by group and item,
food, fuel, IIP, GDP, WPI, Brent, the rupee, US yields and SOFR, policy rates), S5 (the long end against US yields)
and the back-tests of every strategy, which must know when each value became public and must not see a later
revision early. ``mospi``, ``global_sources`` and ``macro_sources`` build the requests, parse the answers, date them
and fetch politely; this module keeps what they read, run after run.

**Sources and their terms** (``SOURCE_KEYS``; re-read the terms before a bulk download):

=========  ===========================================================  =============================================
``mospi``  MoSPI's eSankhyiki API: CPI, IIP, national accounts, WPI      No licence or terms found: whether a history
           (``api.mospi.gov.in``; WPI compiled by DPIIT's Office of     may be kept is for MoSPI to say (letter 12 of
           the Economic Adviser)                                        ``docs/data_enquiries.md``). Fetched only
                                                                        when named, with a reminder; no raw copies
                                                                        unless asked (``Settings.mospi_raw``); CPI
                                                                        down to groups unless asked.
                                                                        ``llms.txt`` asks to keep dataset, indicator,
                                                                        value, unit, period, source and update
                                                                        information: ``series.csv`` does
``ust``    US Treasury daily par yield curve, one CSV a year            No reuse clause; US government data. Off by
                                                                        default: it refused the probe of 18 Nov 2026
``nyfed``  New York Fed reference rates: SOFR, BGCR, TGCR, EFFR, OBFR,  A licence to download and store; the notice
           the SOFR averages and index                                  of ``series.csv``'s ``citation`` must go with
                                                                        any use of them
``eia``    EIA's Europe Brent spot price (the weekly history page)      Public domain with an acknowledgement, but
                                                                        material licensed from private companies is
                                                                        excluded and the series' copyright field was
                                                                        not read: off by default
``h10``    Federal Reserve H.10: rupees per US dollar, noon New York    Public domain; cite the Board
``bis``    BIS: India's and the US policy rates, the daily rupee rate   Unrestricted with citation, no extra charge in
                                                                        a commercial product; the latest API version
                                                                        asked (v2), but v1 for the rupee, whose v2
                                                                        layout was not sampled
=========  ===========================================================  =============================================

``update`` runs ``DEFAULT_SOURCES`` (``nyfed``, ``h10``, ``bis``) unless told which; ``ust`` and ``eia`` run when
named, and MoSPI only when named (``mospi`` or ``all``), each with a one-line reminder (``REMINDERS``).

**Layout.** A store is a directory:

==============================  ==================================================================================
``observations/<source>.csv``   every value ever seen, one row per series, period and vintage (``VINTAGE_COLUMNS``)
``series.csv``                  one row per series held: the parser's fields, the catalogue's name, unit, update
                                information, terms and the citation filled in, and what the store holds
``fetches.csv``                 one row per request sent (``FETCH_COLUMNS``; UTC): ``time``, when it was sent by
                                the run's clock (the run's start plus its pauses: no later than the real time);
                                ``latest``, the latest its answer can have come (that clock plus the timeout of
                                every request of the run so far); requests of a run killed before its source's
                                answers were kept are logged ``interrupted`` by the next
``revisions.csv``               one row per value that changed (``REVISION_COLUMNS``): old and new, when seen
``raw/<source>/``               the answers, byte for byte, named ``<UTC time>_<12 hex digits of SHA-256>.<ext>``;
                                the same bytes are not saved twice (``Settings.keep_raw``, ``mospi_raw``)
``state.json``                  per host the fetch's state (last request, day count, blocks, cool-down, stop); per
                                source its last update, the validators and the last read of each URL, the requests
                                left pending, the history queue and what is done of it, the requests given up on
                                (``failed``: each request failing now, once); the store's ``clock`` (the latest
                                time a command recorded); the settings of the last update; a failed rebuild of the
                                derived tables
``release_dates.csv``,          copies of the tables the user keeps by hand, if given (release dates override or
``index_events.csv``            extend the shipped ``macro_release_dates.csv``; index events are read only from here)
``derived/*.csv``               ``global_daily``, ``india_monthly``, ``india_quarterly``, ``index_events``,
                                ``policy_rates_bis``, rebuilt by every command (``DERIVED_COLUMNS``)
``store.lock``, ``journal.json``  present only while a command runs, or after one was killed
==============================  ==================================================================================

Every write goes to a temporary file first; a commit records its files in ``journal.json``, renames them into place
and deletes the journal, and the next command finishes an interrupted commit or drops its temporary files
(``storekit``). The lock is created exclusively, reported (who, since when, whether its process runs) and never
broken. An update commits the attempt before each request (``state.json``, with every request of the source sent
since its last commit, so that a run killed during a request leaves them all logged), then each source's answers
together, then the derived tables; an ingest commits once. A run killed during a source's requests loses the answers
of that source read in that run (they are asked again), not those of the sources before it.

**Vintages.** A vintage is a value of a series and period as first seen: its text as published, its marks
(``status``, ``revision``, ``footnote``, ``imputation``), ``first_seen`` (India time: for an update the latest time
the answer that first held it can have come, ``fetches.csv``'s ``latest``; for an ingest its ``now``; never before
the vintage it replaces), ``prior_seen`` (the latest earlier read of the same data that did not hold it: the same
query, page or year's file, or a New York Fed or BIS window that covered its date), and where it came from
(``source_file``: the raw copy, or the request or input). The same value seen again adds nothing; a different one
adds vintage 2, 3, ... and a row of ``revisions.csv``. The APIs serve the latest value only, so first prints exist
only if the store read them when they came out.

**From when a value was public** (``public_from``, India time without a zone, with ``public_from_basis``,
``public_from_rule`` and ``release_id``), recomputed from the facts on every commit so that a better release table
reaches every vintage; never later than ``first_seen``, and never earlier than the value could have been public as
far as the rules, the release table and the store's own reads know it:

* MoSPI: the last release that could have set the value, at or before ``first_seen``, by the release-date table
  (shipped, and the user's ``release_dates``) or the release rules (``mospi.ReleaseCalendar``: the stage ladder of
  each family, first print, revisions, final; a "next working day" skips weekends and the Mumbai settlement
  holidays, a proxy for the central government's), never before the base's launch (``mospi.BASE_LAUNCHES``). A
  release time is not taken if the store read the same data after it without the value (``prior_seen``), or read
  the family's headline after it without that release (``HEADLINES``: a probe's reads date every table of the
  release): the release came late, and the value is dated by its sighting.
* The US and BIS: the publication rules of ``global_sources`` (Treasury 23:59:59 New York time on the curve date;
  the New York Fed 10:00 or 11:00 New York time on the next date of the same rate, 17:00 if marked revised; EIA's
  Thursday after the first Wednesday, later after US holidays; H.10 the working day after its Monday release, 16:15;
  BIS policy rates 23:59:59 local time on the date in force; BIS's rupee figure a week after the weekly release that
  can carry it), or ``first_seen`` if earlier. A rule time at or before ``prior_seen`` is not taken (the publication
  came late), except for BIS's policy rates, public from the central bank's announcement. A change of a value the
  store held is dated by its sighting: these publishers keep no revision calendar.
* Where these can still be early: a release later than a rule or a planned date when the store was not polling
  then (history read later); a closure the holiday rules do not know (a central-government holiday that is not a
  Mumbai one, an unscheduled US closure); a past BIS rupee release later than its rule (one calendar and one
  observation); the US target before 4 Feb 1994 (not announced: an assumption). ``seen`` says which values the store
  read at publication.
* ``seen``: ``live`` if the store saw the value within six hours of ``public_from`` (``LIVE_WINDOW``), so that it is
  the value then published; ``later`` if it was read later (the history of a first update, a value seen late): it
  may include revisions made after ``public_from``. ``print``: ``first``, ``final`` (the last stage of its ladder) or
  ``revised``, and ``stage`` the release stage (``quick``, ``provisional``, ``first_revision``, ...), of the release
  that set the value as the store knows it: a value a later release confirmed unchanged keeps the label of the
  release that first set it (``first`` may already be final), while a value first read after its final is ``final``.

``as_of(store, series_id, t)`` gives each period's latest vintage public at ``t`` with these columns and
``not_seen_at_publication``; ``seen_only=True`` keeps only what the store itself had read by ``t``.

**What is kept** (``Settings``, the conservative choices by default). The US and BIS series whole. Of MoSPI, All
India only: base-2024 CPI down to groups (``cpi_levels="groups"``; ``items`` adds classes, sub-classes and items,
and base 2012's items), base 2012's groups and subgroups, rural, urban and combined; IIP's general index, sectors
and use-based groups; GDP and GVA by industry; WPI's headline and major groups; never the "RBI" tables (RBI's data,
fifteen months stale). ``states=True`` adds each state's general CPI index. The full state-level download is not
small (1.5 million rows of base-2024 CPI, about 15,000 requests), so it is not the default.

**Update** (``update(store, now=..., sources=..., transport=...)``). A ``now`` before the store's ``clock`` (or its
latest request) is a clock error: nothing is asked. Per source, in ``SOURCE_KEYS`` order: nothing if its last update
that asked something began less than ``min_interval`` ago (``too soon``); else the recent window (``recent_requests``:
MoSPI's three conditional probes and the national accounts; Treasury's current year; the New York Fed's last 14
days; EIA's and H.10's pages, conditional; BIS's last 60 observations; each window reaching back to the latest date
the store holds, so that an outage leaves no gap), the follow-ups their answers ask (``follow_ups``: a changed CPI
probe brings the latest two months' tables and every month since the tables' latest, an IIP probe the same, a WPI
probe the major groups; a first page the others), the requests an earlier run left pending, then the history queue
(``history_requests``, seeded on the source's first update and extended if the settings later ask more), in steps
within ``max_requests`` (the run), ``max_per_host`` and each host's daily cap. Every request goes through
``macro_sources.fetch``: a generic client name, conditional requests where the server honours them, at least ten
seconds between two requests to a host, no redirect followed, and the block rule (HTTP 403, 418 or 429, a refusal
page whatever its status, a demand for contact details, a second web page in a row: no request to that host before
08:00 India time the next day, counted from the latest time the block can have happened, and a second block in a
row stops it until ``resume`` after a person checked the site in a browser). A refused answer (kept in ``raw/``
where copies are kept) or a MoSPI page that does not fit its query keeps no validator, so that it is asked again
whole, and goes to ``failed``, as does a request answered with an HTTP 4xx: once per request (a recent window, whose
URL moves every day, once per window), until it or a later read of the same data answers; ``retry_failed`` queues
the history and pending ones again (a recent request is asked again by every run). A MoSPI table that still lacks
the month its headline showed stays pending, with the other tables of that release, for ``MAX_LAG_TRIES`` reads. A
5xx, a network error or a cap keeps a request for a later run. ``UpdateReport.exit_code`` is that of
``rbi-store-poll``: 0, 4 (needs a look), 3 (blocked, cooling down or stopped), 1 (an error).

**Ingest** (``ingest(store, files, now=...)``) reads saved answers by the same parsers, recognised by their content,
in the order of their full names (name them by time, or keep them in dated folders): the same files again change
nothing, and their order does not matter. A saved answer is dated by the ingest (``first_seen``), not by when it was
saved, which the store cannot know: ingest saved answers oldest first; a value equal to a vintage already superseded
is not taken as a change back; a ``now`` before the store's clock is refused. Base-2012 and 2010 CPI rows do not say
their series: an answer of them is read as the series of the store's own request (its raw copy), else as
``cpi_series``, else refused.

**Derived tables** (``derived/``, deterministic). Each value is the latest vintage; ``public_from`` is the latest of
the row's values, with a ``_public_from`` per group of values that became public together; ``seen`` is ``later`` if
any value of the row was read later than its publication; ``revised`` is when the store last saw a value of the row
change. Nothing is filled forward and bases are never spliced. For what was known at a time ``t`` use ``as_of``, or
the monthly and quarterly tables' rows (one per print) with ``public_from <= t``: ``global_daily`` holds each date's
latest vintage only, so a date whose value was revised is missing from its rows with ``public_from <= t`` until the
revision was public.

* ``global_daily``: one row per date any US or BIS series has (the observation date: trade date, curve date, spot
  date, rate-in-force date): Treasury par yields, SOFR, BGCR, TGCR, EFFR, OBFR, the FOMC target range, the SOFR
  averages and index, Brent, H.10's rupee, BIS's policy rates and rupee; ``notes`` marks a series' change of
  definition (BIS's India repo rate from 3 Apr 2001, the US target mid-point from 19 Dec 1985) on its first row.
* ``india_monthly``: one row per family (CPI, IIP, WPI), base and month, and per moment a value of it became
  public: every measure as known then (``print``, ``stage``). CPI general, rural and urban, food (base 2024's group
  01.1, base 2012's CFPI), food and beverages, fuel, housing, rent; IIP general and manufacturing; WPI all and food,
  with WPI's year-on-year change computed here on one base. ``2024back`` is base 2024's back series.
* ``india_quarterly``: GDP and GVA by base, quarter and moment, at current and constant prices, with year-on-year
  changes of constant prices computed from the same base as known then.
* ``index_events``: the table of index inclusion events given by the user (``index_events``), checked, with
  ``public_from`` in the provider's zone; none is shipped as a default (an example table with each fact's source:
  ``data/index_events_example.csv``).
* ``policy_rates_bis``: India's repo rate changes in BIS's series from 3 Apr 2001 in ``rbi_store``'s policy-rate
  columns, ``sdf_pct`` and ``msf_pct`` blank (BIS has no corridor): to be completed by hand before
  ``rbi-store-ingest --policy-rates``, which refuses blanks; BIS lags about a month and dates some changes a day
  after RBI's decision. ``policy_rate_check`` compares it with a table kept by hand.

``status(store)`` reports per source its series, periods, the history queue, pending and failed requests, the host's
last request, blocks and cool-downs, MoSPI's tables behind their headline, and the series the derived tables use;
``read_table(store, name)`` reads any table typed.

**Times.** CSV times are India time without a zone (``2026-11-12T16:00:00``), as in ``rbi_store``;
``fetches.csv``, ``state.json`` and the raw file names use UTC with ``Z``. Nothing here reads the clock: ``now`` is
an argument, and the command line reads the clock once per command.
"""

from __future__ import annotations

import copy
import csv
import datetime as dt
import hashlib
import io
import json
import re
import urllib.parse
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from cr_strat import global_sources as gs
from cr_strat import macro_sources as ms
from cr_strat import mospi
from cr_strat.macro_base import Observation, ParseResult, Request, SourceError, add_months, number_text, period_bounds
from cr_strat.storekit import (
    FeedResponse,
    Transaction,
    Transport,
    check_timeout,
    legacy_renegotiation_context,
    lock_message,
    recover,
    store_lock,
    urllib_transport,
    write_atomic,
)
from cr_strat.ustime import IST, UTC, utc_text

STORE_FORMAT = 1
SOURCE_KEYS = (mospi.SOURCE, gs.UST, gs.NYFED, gs.EIA, gs.H10, gs.BIS)  # the order an update runs them in
NAMED_ONLY = frozenset({mospi.SOURCE})  # fetched only when named: its licence is not settled
DEFAULT_SOURCES = tuple(k for k in SOURCE_KEYS if k not in NAMED_ONLY and ms.SOURCES[k].enabled_by_default)
REMINDERS = {
    mospi.SOURCE: "reminder: MoSPI's licence for a stored history is not settled (no terms found for the eSankhyiki "
                  "API; letter 12 of docs/data_enquiries.md asks): keep what is stored to internal research until "
                  "MoSPI answers",
    gs.EIA: "reminder: EIA's terms exclude material licensed from private companies, and the copyright field of the "
            "Brent series (PET.RBRTE.D) has not been read: read it before relying on a stored history",
    gs.UST: "reminder: Treasury refused the probe of 18 Nov 2026 (HTTP 403, on another path); a block stops requests "
            "to it until 08:00 India time the next day, a second one until it is resumed by hand",
}
DEFAULT_MIN_INTERVAL = dt.timedelta(minutes=25)
MAX_MIN_INTERVAL = dt.timedelta(days=7)
DEFAULT_MAX_REQUESTS = 60
DEFAULT_MAX_PER_HOST = 30
LIVE_WINDOW = mospi.LIVE_WINDOW  # a value seen this soon after its publication time counts as seen at publication
LOCK_STALE_AFTER = dt.timedelta(hours=2)
VALIDATOR_DAYS = 92  # validators (and the record of reads) of a URL not answered for this long are dropped
NYFED_DAYS = 14  # the New York Fed's recent window: revisions come the same day
BIS_OBSERVATIONS = 60  # BIS policy rates: the last 60 observations of each series (India lags about a month)
BIS_DAYS = 60
MAX_GAP_MONTHS = 24  # MoSPI: at most this many months between the latest held and a release are asked at once
MAX_LAG_TRIES = 6  # MoSPI: a table still lacking the month its headline showed is asked again this many runs
HISTORY_FROM = 2013  # MoSPI history requests start here (spec decision 4.2: nothing earlier)
CPI_2012_LAST_YEAR = 2025  # base 2012 ended with December 2025
CPI_2024_FIRST = (2025, 1)
IIP_2022_23_FIRST = (2023, 4)
CPI_LEVELS = ("items", "groups")
MOSPI_HISTORY = ("derived", "full")
STATE_CODES = tuple(range(2, 38))  # base-2024 CPI: 1 is All India, 2 to 37 the states (MoSPI's filter list)
CLOCK_SLACK = dt.timedelta(minutes=5)
_TMP = ".tmp-macrostore"
_EXT = {mospi.SOURCE: "json", gs.UST: "csv", gs.NYFED: "json", gs.EIA: "htm", gs.H10: "htm", gs.BIS: "csv"}
_KIND_EXT = {gs.NYFED_CSV: "csv", gs.H10_DDP: "csv"}

VINTAGE_FACTS = ("series_id", "period", "vintage", "value", "unit", "status", "revision", "footnote", "imputation",
                 "first_seen", "prior_seen", "source_file", "notes")
VINTAGE_COLUMNS = ("series_id", "period", "period_start", "period_end", "vintage", "value", "unit", "status",
                   "revision", "footnote", "imputation", "first_seen", "public_from", "public_from_basis", "seen",
                   "print", "stage", "release_id", "public_from_rule", "prior_seen", "source_file", "notes")
SERIES_FACTS = ("series_id", "source", "dataset", "indicator", "base", "series_type", "area", "sector", "level",
                "code", "label", "unit", "period_type", "source_url", "series_notes", "last_release")
SERIES_COLUMNS = ("series_id", "source", "publisher", "name", "dataset", "indicator", "base", "series_type", "area",
                  "sector", "level", "code", "label", "unit", "frequency", "period_type", "period_format",
                  "first_period", "last_period", "periods", "vintages", "revised_periods", "last_value",
                  "last_public_from", "last_first_seen", "last_release", "update_info", "citation", "terms",
                  "terms_url", "source_url", "in_catalogue", "aliases", "series_notes", "catalogue_notes")
_OPTIONAL_COLUMNS = frozenset({"latest", "last_release"})  # added later: a store written before has none
FETCH_COLUMNS = ("time", "latest", "source", "host", "url", "http_status", "outcome", "bytes", "rows", "kept", "new",
                 "changed", "raw", "etag", "last_modified", "message")
REVISION_COLUMNS = ("seen", "source", "series_id", "period", "old_vintage", "new_vintage", "old_value", "new_value",
                    "old_status", "new_status", "old_revision", "new_revision", "old_first_seen", "prior_seen",
                    "source_file")
_IDENTITY = ("value", "status", "revision", "footnote", "imputation")
_PRINT_RANK = {"first": 0, "revised": 1, "final": 2}

_UST_TENORS = ("1m", "1_5m", "2m", "3m", "4m", "6m", "1y", "2y", "3y", "5y", "7y", "10y", "20y", "30y")
GLOBAL_GROUPS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("ust_public_from", tuple((f"ust_par_{t}", f"ust_par_{t}") for t in _UST_TENORS)),
    ("nyfed_secured_public_from", (("sofr", "sofr"), ("bgcr", "bgcr"), ("tgcr", "tgcr"))),
    ("nyfed_unsecured_public_from", (("effr", "effr"), ("obfr", "obfr"), ("fomc_target_low", "fomc_target_low"),
                                     ("fomc_target_high", "fomc_target_high"))),
    ("sofrai_public_from", (("sofr_avg_30d", "sofr_avg_30d"), ("sofr_avg_90d", "sofr_avg_90d"),
                            ("sofr_avg_180d", "sofr_avg_180d"), ("sofr_index", "sofr_index"))),
    ("brent_public_from", (("brent_usd_bbl", "brent_spot_usd_bbl"),)),
    ("usdinr_h10_public_from", (("usdinr_h10", "usdinr_h10_noon"),)),
    ("bis_policy_public_from", (("policy_rate_in_bis", "policy_rate_in_bis"),
                                ("policy_rate_us_bis", "policy_rate_us_bis"))),
    ("usdinr_bis_public_from", (("usdinr_bis", "usdinr_bis_daily"),)),
)
_ROW_TAIL = ("public_from", "seen", "revised", "notes")
GLOBAL_DAILY_COLUMNS = ("date", *(c for group, cols in GLOBAL_GROUPS for c in (*(v for v, _ in cols), group)),
                        *_ROW_TAIL)

# india_monthly: per (family, base) the measures and their series (index, published year-on-year change or None)
_CPI_MEASURES = ("combined", "rural", "urban", "food", "food_bev", "fuel", "housing", "rent")
_MONTHLY_MEASURES: dict[tuple[str, str], dict[str, tuple[str, str | None]]] = {}
for _base, _stem in (("2024", "cpi_2024_all_india"), ("2012", "cpi_2012_all_india"), ("2010", "cpi_2010_all_india")):
    _m = {s: (f"{_stem}_{s}_general", f"{_stem}_{s}_general_yoy") for s in ("combined", "rural", "urban")}
    if _base == "2024":
        _m.update(food=(f"{_stem}_combined_g01_1", f"{_stem}_combined_g01_1_yoy"),
                  food_bev=(f"{_stem}_combined_d01", f"{_stem}_combined_d01_yoy"),
                  fuel=(f"{_stem}_combined_g04_5", f"{_stem}_combined_g04_5_yoy"),
                  housing=(f"{_stem}_combined_d04", f"{_stem}_combined_d04_yoy"),
                  rent=(f"{_stem}_combined_g04_1", None))
    else:
        _m.update(food=(f"{_stem}_combined_cfpi", f"{_stem}_combined_cfpi_yoy"),
                  food_bev=(f"{_stem}_combined_food_and_beverages", f"{_stem}_combined_food_and_beverages_yoy"),
                  fuel=(f"{_stem}_combined_fuel_and_light", f"{_stem}_combined_fuel_and_light_yoy"),
                  housing=(f"{_stem}_combined_housing", f"{_stem}_combined_housing_yoy"))
    _MONTHLY_MEASURES[("cpi", _base)] = _m
_MONTHLY_MEASURES[("cpi", "2024back")] = {s: (f"cpi_2024back_all_india_{s}_general",
                                             f"cpi_2024back_all_india_{s}_general_yoy")
                                         for s in ("combined", "rural", "urban")}
for _base in ("2022-23", "2011-12"):
    _b = _base.replace("-", "_")
    _MONTHLY_MEASURES[("iip", _base)] = {"iip": (f"iip_{_b}_general", f"iip_{_b}_general_yoy"),
                                         "iip_manufacturing": (f"iip_{_b}_manufacturing",
                                                               f"iip_{_b}_manufacturing_yoy")}
_MONTHLY_MEASURES[("wpi", "2022-23")] = {"wpi": ("wpi_2022_23_all", None), "wpi_food": ("wpi_2022_23_food_index", None)}
_MONTHLY_MEASURES[("wpi", "2011-12")] = {"wpi": ("wpi_2011_12_all", None)}
_COMPUTED_YOY = {"wpi"}  # WPI publishes no inflation: computed here on one base

_MONTHLY_VALUES = (
    *(c for s in ("combined", "rural", "urban", "food", "food_bev", "fuel", "housing")
      for c in (f"cpi_{s}_index", f"cpi_{s}_yoy", f"cpi_{s}_public_from")),
    "cpi_rent_index", "cpi_rent_public_from",
    "iip_index", "iip_yoy", "iip_public_from", "iip_manufacturing_index", "iip_manufacturing_yoy",
    "iip_manufacturing_public_from",
    "wpi_index", "wpi_yoy", "wpi_public_from", "wpi_food_index", "wpi_food_public_from")
_ROW_HEAD = ("print", "stage", "status", "public_from", "first_public_from", "public_from_basis", "release_id", "seen",
             "revised")
INDIA_MONTHLY_COLUMNS = ("month", "family", "base", *_ROW_HEAD, *_MONTHLY_VALUES, "notes")
_QUARTERLY_MEASURES = {base: {"gdp": (f"gdp_{base.replace('-', '_')}_q_current_cr",
                                      f"gdp_{base.replace('-', '_')}_q_constant_cr"),
                              "gva": (f"gva_{base.replace('-', '_')}_q_total_current_cr",
                                      f"gva_{base.replace('-', '_')}_q_total_constant_cr")}
                       for base in ("2022-23", "2011-12")}
INDIA_QUARTERLY_COLUMNS = ("quarter", "base", *_ROW_HEAD, "gdp_current_cr", "gdp_constant_cr", "gdp_public_from",
                           "gva_current_cr", "gva_constant_cr", "gva_public_from", "gdp_constant_yoy",
                           "gva_constant_yoy", "notes")
INDEX_EVENT_DERIVED_COLUMNS = (*gs.INDEX_EVENT_COLUMNS, "public_from", "public_from_basis", "public_from_rule")
POLICY_EXPORT_COLUMNS = ("effective_date", "repo_pct", "sdf_pct", "msf_pct", "source", "public_from")
REPO_FROM = dt.date(2001, 4, 3)  # BIS's India series: the repo rate from this date, the bank rate before
DERIVED_COLUMNS: dict[str, tuple[str, ...]] = {
    "global_daily": GLOBAL_DAILY_COLUMNS, "india_monthly": INDIA_MONTHLY_COLUMNS,
    "india_quarterly": INDIA_QUARTERLY_COLUMNS, "index_events": INDEX_EVENT_DERIVED_COLUMNS,
    "policy_rates_bis": POLICY_EXPORT_COLUMNS,
}

EXIT_OK, EXIT_ERROR, EXIT_BLOCKED, EXIT_ATTENTION = ms.EXIT_OK, ms.EXIT_ERROR, ms.EXIT_BLOCKED, ms.EXIT_ATTENTION


class StoreError(RuntimeError):
    """The store cannot be used as it is: a file is damaged, foreign or inconsistent with the others."""


class StoreLockedError(StoreError):
    """Another command holds the store's lock, or one left it behind (a stale lock is never broken here)."""


# --- Paths, settings, text ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class StorePaths:
    """Where each part of a store lives."""

    root: Path

    @property
    def observation_dir(self) -> Path:
        return self.root / "observations"

    def observations(self, source: str) -> Path:
        return self.observation_dir / f"{source}.csv"

    @property
    def series(self) -> Path:
        return self.root / "series.csv"

    @property
    def fetches(self) -> Path:
        return self.root / "fetches.csv"

    @property
    def revisions(self) -> Path:
        return self.root / "revisions.csv"

    @property
    def state(self) -> Path:
        return self.root / "state.json"

    @property
    def raw(self) -> Path:
        return self.root / "raw"

    @property
    def derived(self) -> Path:
        return self.root / "derived"

    @property
    def release_dates(self) -> Path:
        return self.root / "release_dates.csv"

    @property
    def index_events(self) -> Path:
        return self.root / "index_events.csv"

    @property
    def lock(self) -> Path:
        return self.root / "store.lock"

    @property
    def journal(self) -> Path:
        return self.root / "journal.json"

    def directories(self) -> list[Path]:
        """Every directory a commit writes into (searched for temporary files a crash left)."""
        return [self.root, self.observation_dir, self.derived, self.raw, *(self.raw / k for k in SOURCE_KEYS)]


@dataclass(frozen=True)
class Settings:
    """What a store keeps and fetches; the conservative choices are the defaults (``docs``: decisions for the user).

    ``cpi_levels``: ``groups`` (the default, decision 4.1 of the spec while MoSPI's terms are not settled) keeps
    base-2024 CPI for All India down to groups (base 2012's subgroups), what ``macro_sources.stored_by_default``
    names; ``items`` every node (general index, divisions, groups, classes, sub-classes, items; base 2012's items
    too), what S7 wants once the terms allow it. ``states``: also each state's general CPI index (base 2024), fetched
    one request a state. Of IIP,
    national accounts and WPI the store keeps what ``macro_sources.stored_by_default`` names (All India: IIP's
    general index, sectors and use-based groups; GDP and GVA by industry; WPI's headline and major groups); never
    the "RBI" tables. ``keep_raw``: keep a copy of every US and BIS answer; ``mospi_raw``: of MoSPI's too (off: its
    terms are not known). ``backfill``: on its first update a source queues its history, fetched in steps under the
    request caps; ``mospi_history`` ``derived`` fetches the history of what the derived tables use, from 2013, and
    ``full`` of everything kept.
    """

    cpi_levels: str = "groups"
    states: bool = False
    keep_raw: bool = True
    mospi_raw: bool = False
    backfill: bool = True
    mospi_history: str = "derived"

    def __post_init__(self):
        if self.cpi_levels not in CPI_LEVELS:
            raise ValueError(f"cpi_levels must be one of {', '.join(CPI_LEVELS)}, not {self.cpi_levels!r}")
        if self.mospi_history not in MOSPI_HISTORY:
            raise ValueError(f"mospi_history must be one of {', '.join(MOSPI_HISTORY)}, not {self.mospi_history!r}")
        for name in ("states", "keep_raw", "mospi_raw", "backfill"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be True or False, not {getattr(self, name)!r}")

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    def keeps_raw(self, source: str) -> bool:
        return self.keep_raw and (source != mospi.SOURCE or self.mospi_raw)

    def history_plan(self, source: str) -> str:
        """What decides a source's history plan: a different one later adds the requests the new plan needs."""
        if source != mospi.SOURCE:
            return "full"
        return f"{self.mospi_history}/{self.cpi_levels}/{'states' if self.states else 'all_india'}"


def _check_now(now: dt.datetime, what: str = "now") -> dt.datetime:
    if not isinstance(now, dt.datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError(f"{what} must be a timezone-aware datetime, not {now!r}")
    return now


def _ist(when: dt.datetime) -> str:
    """An aware instant as the CSVs write times: India time without a zone."""
    return when.astimezone(IST).replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S")


def _naive_text(when: dt.datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%S")


def _parse_ist(text: str, where: str) -> dt.datetime:
    try:
        return dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%S")
    except (TypeError, ValueError):
        raise StoreError(f"{where}: {text!r} is not a time like 2026-11-12T16:00:00 (India time)") from None


def _utc_of(naive_ist: dt.datetime) -> dt.datetime:
    return naive_ist.replace(tzinfo=IST).astimezone(UTC)


def _from_utc(text: object, where: str) -> dt.datetime | None:
    """A UTC time as ``state.json`` writes it (``2026-11-18T03:09:51Z``); ``None`` for null or ``""``."""
    if text is None or text == "":
        return None
    if not isinstance(text, str):
        raise StoreError(f"{where}: {text!r} is not a time like 2026-11-18T03:09:51Z")
    try:
        return dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        raise StoreError(f"{where}: {text!r} is not a time like 2026-11-18T03:09:51Z") from None


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _n(count: int, word: str, plural: str | None = None) -> str:
    return f"{count:,} {word}" if count == 1 else f"{count:,} {plural or word + 's'}"


def _csv_bytes(rows: Iterable[Mapping[str, str]], columns: Iterable[str]) -> bytes:
    columns = list(columns)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([row.get(c, "") for c in columns])
    return out.getvalue().encode("utf-8")


def _read_rows(path: Path, required: Iterable[str], what: str) -> list[dict[str, str]]:
    """Rows of a store CSV as text, or none if the file is missing; refused if it lacks a column."""
    if not path.exists():
        return []
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StoreError(f"{path}: not a readable {what} (not UTF-8: {exc})") from None
    reader = csv.DictReader(io.StringIO(text))
    missing = [c for c in required if c not in (reader.fieldnames or [])]
    if missing:
        raise StoreError(f"{path}: not a {what} (missing columns {missing})")
    try:
        return [{k: (v if v is not None else "") for k, v in row.items() if k is not None} for row in reader]
    except csv.Error as exc:
        raise StoreError(f"{path}: not a readable {what} ({exc})") from None


def _endpoint(url: str) -> str:
    """The address a series comes from, without the query (dates, pages, filters): ``series.csv``'s ``source_url``."""
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


# --- Writes, the journal and the lock (storekit, with this store's names) ------------------------------------

def _transaction(paths: StorePaths) -> Transaction:
    return Transaction(paths.root, paths.journal, _TMP)


def _recover(paths: StorePaths) -> list[str]:
    return recover(paths.root, paths.journal, _TMP, paths.directories(), StoreError)


@contextmanager
def _locked(paths: StorePaths, now: dt.datetime, command: str) -> Iterator[None]:
    """Hold ``store.lock`` for one command; finish or drop an interrupted commit first. A lock is never broken."""
    with store_lock(paths.root, paths.lock, now, command, product="macro-store", stale_after=LOCK_STALE_AFTER,
                    locked_error=StoreLockedError, recover_first=lambda: _recover(paths)):
        yield


# --- state.json ----------------------------------------------------------------------------------------------

_SOURCE_TEXTS = ("last_outcome", "last_message")
_FAILED_KEYS = ("time", "reason", "raw", "origin")


def _fresh_state() -> dict:
    return {"format": STORE_FORMAT, "hosts": {}, "sources": {}, "settings": None, "derived": None,
            "derived_stale": None, "clock": None}


_EXTRA_TEXTS = ("expect", "group", "total", "parent_role")  # what a request carries for the checks of its answer


def _request_dict(request: Request, role: str, extra: Mapping | None = None) -> dict:
    """A request as ``state.json`` keeps it; ``query`` (the parameters a parser reads) as ``name=value&...``;
    ``extra``: what its answer is checked against (the month a MoSPI table must hold, its group, a page's query
    size) and how many reads it has had (``tries``)."""
    query = "&".join(f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}" for k, v in request.query)
    out = {"kind": request.kind, "url": request.url, "accept": request.accept, "query": query, "note": request.note,
           "role": role}
    for key, value in (extra or {}).items():
        if key in _EXTRA_TEXTS and value or key == "tries" and value:
            out[key] = value
    return out


def _extra_of(item: Mapping) -> dict:
    return {k: item[k] for k in (*_EXTRA_TEXTS, "tries") if k in item}


_ROLES = ("plain", "all", "top", "page", "probe_cpi", "probe_iip", "probe_wpi")


def _request_from(data: object, source: str, where: str) -> tuple[Request, str]:
    if not isinstance(data, dict) or not all(isinstance(data.get(k), str) for k in ("kind", "url", "accept", "query",
                                                                                   "note", "role")):
        raise StoreError(f"{where}: not a request (kind, url, accept, query, note and role, all text)")
    query = [tuple(urllib.parse.unquote(x) for x in pair.split("=", 1)) for pair in data["query"].split("&")
             if pair]
    if not all(len(pair) == 2 for pair in query):
        raise StoreError(f"{where}: query {data['query']!r} is not name=value pairs joined by &")
    if data["role"] not in _ROLES:
        raise StoreError(f"{where}: role {data['role']!r} is not one of {', '.join(_ROLES)}")
    for key in _EXTRA_TEXTS:
        if not isinstance(data.get(key, ""), str):
            raise StoreError(f"{where}: {key} must be text")
    if "tries" in data and (type(data["tries"]) is not int or data["tries"] < 0):
        raise StoreError(f"{where}: tries must be a whole number of at least 0")
    try:
        _, info = ms.check_request_url(data["url"])
    except ValueError as exc:
        raise StoreError(f"{where}: {exc}") from None
    if info.key != source or data["kind"] not in info.kinds:
        raise StoreError(f"{where}: {data['url']} with kind {data['kind']!r} does not belong to source {source}")
    return Request(source, data["kind"], data["url"], data["accept"], tuple(query), data["note"]), data["role"]


def _check_state(paths: StorePaths) -> dict:
    """``state.json``, every value checked (a hand edit included), or a fresh state when there is none."""
    if not paths.state.exists():
        return _fresh_state()
    try:
        state = json.loads(paths.state.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise StoreError(f"{paths.state}: not readable JSON ({exc})") from None
    where = str(paths.state)
    if not isinstance(state, dict) or state.get("format") != STORE_FORMAT:
        raise StoreError(f"{where}: not a macro store state of format {STORE_FORMAT}")
    for key in ("hosts", "sources"):
        if not isinstance(state.get(key), dict):
            raise StoreError(f"{where}: {key!r} must be an object")
    for host, data in state["hosts"].items():
        try:
            parsed = ms.HostState.from_dict(data, where=f"{where}: host {host}")
        except ValueError as exc:
            raise StoreError(str(exc)) from None
        if parsed.host != host:
            raise StoreError(f"{where}: host {host} holds the state of {parsed.host}")
    for source, data in state["sources"].items():
        at = f"{where}: source {source}"
        if source not in SOURCE_KEYS or not isinstance(data, dict):
            raise StoreError(f"{at}: not a source of this store ({', '.join(SOURCE_KEYS)})")
        _from_utc(data.get("last_update"), f"{at}, last_update")
        for key in _SOURCE_TEXTS:
            if not isinstance(data.get(key, ""), str):
                raise StoreError(f"{at}, {key}: must be text")
        for flight in _flights(data.get("in_flight")):
            if not isinstance(flight, dict) or not isinstance(flight.get("url"), str):
                raise StoreError(f"{at}, in_flight: must be null or a list of objects with a url and a time")
            _from_utc(flight.get("time"), f"{at}, in_flight time")
        validators = data.get("validators", {})
        if not isinstance(validators, dict):
            raise StoreError(f"{at}, validators: must be an object")
        for url, v in validators.items():
            if not isinstance(v, dict) or not all(isinstance(v.get(k, ""), str) for k in ("etag", "last_modified")):
                raise StoreError(f"{at}, validators of {url}: etag and last_modified must be text")
            _from_utc(v.get("last_ok"), f"{at}, validators of {url}, last_ok")
        if not isinstance(data.get("pending", []), list):
            raise StoreError(f"{at}, pending: must be a list of requests")
        for i, item in enumerate(data.get("pending", [])):
            _request_from(item, source, f"{at}, pending item {i + 1}")
        backfill = data.get("backfill")
        if backfill is not None:
            if not isinstance(backfill, dict) or not isinstance(backfill.get("plan"), str) \
                    or type(backfill.get("planned")) is not int or backfill["planned"] < 0 \
                    or not isinstance(backfill.get("done"), list) \
                    or not all(isinstance(u, str) for u in backfill["done"]) \
                    or not isinstance(backfill.get("queue"), list):
                raise StoreError(f"{at}, backfill: must hold plan, planned (a count), done (URLs) and queue")
            for i, item in enumerate(backfill["queue"]):
                _request_from(item, source, f"{at}, backfill queue item {i + 1}")
            _from_utc(backfill.get("seeded"), f"{at}, backfill seeded")
            _from_utc(backfill.get("finished"), f"{at}, backfill finished")
        failed = data.get("failed", [])
        if not isinstance(failed, list) or not all(isinstance(f, dict) and all(isinstance(f.get(k), str)
                                                                                for k in _FAILED_KEYS)
                                                   for f in failed):
            raise StoreError(f"{at}, failed: must be a list of objects with {', '.join(_FAILED_KEYS)} and a request")
        for i, item in enumerate(failed):
            _request_from(item.get("request"), source, f"{at}, failed item {i + 1}")
    derived = state.get("derived")
    if derived is not None:
        if not isinstance(derived, dict) or not isinstance(derived.get("error"), str):
            raise StoreError(f"{where}: 'derived' must be null or an object with an 'error'")
        _from_utc(derived.get("since"), f"{where}: derived since")
    _from_utc(state.get("derived_stale"), f"{where}: derived_stale")
    _from_utc(state.get("clock"), f"{where}: clock")
    if state.get("settings") is not None and not isinstance(state["settings"], dict):
        raise StoreError(f"{where}: 'settings' must be null or an object")
    return state


def _flights(value: object) -> list:
    """The requests a source's run had sent and not yet committed (``in_flight``: a list; one object in a state
    written before the list, or null)."""
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]


def _state_bytes(state: dict) -> bytes:
    return (json.dumps(state, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _source_state(state: dict, source: str) -> dict:
    data = state["sources"].setdefault(source, {})
    for key, default in (("last_update", None), ("last_outcome", ""), ("last_message", ""), ("in_flight", None),
                         ("validators", {}), ("pending", []), ("backfill", None), ("failed", [])):
        data.setdefault(key, copy.deepcopy(default))
    return data


# --- The stored data -----------------------------------------------------------------------------------------

@dataclass
class _Data:
    """A store between reading and writing: vintages by key, series facts, the logs, the state and the tables."""

    paths: StorePaths
    vintages: dict[tuple[str, str], list[dict[str, str]]]
    series: dict[str, dict[str, str]]
    revisions: list[dict[str, str]]
    fetches: list[dict[str, str]]
    state: dict
    release_bytes: bytes | None = None
    events_bytes: bytes | None = None
    calendar: mospi.ReleaseCalendar | None = None
    raw_shas: set[str] = field(default_factory=set)
    dating: dict[str, dict[tuple[str, str, int], _Dating]] = field(default_factory=dict)  # per source, cached
    _periods: dict[str, list[str]] | None = None

    def keys_of(self, source: str) -> list[tuple[str, str]]:
        return sorted(k for k in self.vintages if self.series[k[0]]["source"] == source)

    def periods_of(self, series_id: str) -> list[str]:
        """The periods the store holds for a series (an index rebuilt after new keys are added)."""
        if self._periods is None:
            index: dict[str, list[str]] = defaultdict(list)
            for sid, period in self.vintages:
                index[sid].append(period)
            self._periods = dict(index)
        return self._periods.get(series_id, [])


def _load(paths: StorePaths) -> _Data:
    """Everything a command needs, checked: a damaged or inconsistent file is a ``StoreError`` naming it."""
    state = _check_state(paths)
    series: dict[str, dict[str, str]] = {}
    for line, row in enumerate(_read_rows(paths.series, _required(SERIES_FACTS), "series table"), start=2):
        sid = row["series_id"]
        if not sid or sid in series or row["source"] not in SOURCE_KEYS:
            raise StoreError(f"{paths.series}, line {line}: series {sid!r} of source {row['source']!r} (a series must "
                             f"be listed once, with a source of this store)")
        if row["period_type"] not in ("day", "month", "fy_quarter", "fy"):
            raise StoreError(f"{paths.series}, line {line}: period_type {row['period_type']!r}")
        series[sid] = {c: row.get(c, "") for c in SERIES_FACTS}
    vintages: dict[tuple[str, str], list[dict[str, str]]] = {}
    for source in SOURCE_KEYS:
        path = paths.observations(source)
        for line, row in enumerate(_read_rows(path, VINTAGE_FACTS, "observation table"), start=2):
            where = f"{path}, line {line}"
            info = series.get(row["series_id"])
            if info is None or info["source"] != source:
                raise StoreError(f"{where}: series {row['series_id']!r} is not a {source} series of series.csv; the "
                                 "store is inconsistent (restore it from a backup, or build a new one from raw/)")
            try:
                period_bounds(row["period"], info["period_type"])
                number_text(row["value"], "value")
            except (ValueError, SourceError) as exc:
                raise StoreError(f"{where}: {exc}") from None
            key = (row["series_id"], row["period"])
            held = vintages.setdefault(key, [])
            if row["vintage"] != str(len(held) + 1):
                raise StoreError(f"{where}: vintage {row['vintage']!r} of {key[0]} {key[1]}; vintages must run 1, 2, "
                                 "... in order")
            _parse_ist(row["first_seen"], f"{where}, first_seen")
            if held and row["first_seen"] < held[-1]["first_seen"]:
                raise StoreError(f"{where}: vintage {row['vintage']} of {key[0]} {key[1]} was first seen at "
                                 f"{row['first_seen']}, before the vintage it replaces ({held[-1]['first_seen']}); "
                                 "the store is inconsistent (a clock moved back?)")
            if row["prior_seen"]:
                _parse_ist(row["prior_seen"], f"{where}, prior_seen")
            held.append({c: row.get(c, "") for c in VINTAGE_FACTS})
    revisions = _read_rows(paths.revisions, REVISION_COLUMNS, "revision log")
    fetches = _read_rows(paths.fetches, _required(FETCH_COLUMNS), "request log")
    data = _Data(paths, vintages, series, [{c: r[c] for c in REVISION_COLUMNS} for r in revisions],
                 [{c: r.get(c, "") for c in FETCH_COLUMNS} for r in fetches], state)
    if paths.release_dates.exists():
        data.release_bytes = paths.release_dates.read_bytes()
    if paths.index_events.exists():
        data.events_bytes = paths.index_events.read_bytes()
    try:
        data.calendar = _calendar(data.release_bytes)
    except (ValueError, SourceError) as exc:
        raise StoreError(f"{paths.release_dates}: the store's release dates cannot be read ({exc}); give a good table "
                         "with --release-dates, or remove the file") from None
    if data.events_bytes is not None:
        try:
            gs.read_index_events(data.events_bytes, name=str(paths.index_events))
        except (ValueError, SourceError) as exc:
            raise StoreError(f"{paths.index_events}: the store's index events cannot be read ({exc}); give a good "
                             "table with --index-events, or remove the file") from None
    if paths.raw.is_dir():
        data.raw_shas = {p.name.split("_")[-1].split(".")[0] for p in paths.raw.rglob("*_*.*")
                         if not p.name.endswith(_TMP)}
    return data


def _required(columns: Iterable[str]) -> tuple[str, ...]:
    """The columns a store file must have: all but those added after the first stores were written."""
    return tuple(c for c in columns if c not in _OPTIONAL_COLUMNS)


def _calendar(user: bytes | None) -> mospi.ReleaseCalendar:
    return mospi.ReleaseCalendar.load(user) if user is not None else mospi.ReleaseCalendar.load()


# --- From when each vintage was public --------------------------------------------------------------------

@dataclass(frozen=True)
class _Dating:
    """When a vintage was public (India time, naive), how that was found, and what the store saw."""

    public_from: dt.datetime
    basis: str
    rule: str
    release_id: str
    stage: str
    print: str
    seen: str  # live (seen within LIVE_WINDOW of public_from) or later (read later: may include revisions)


def _observation(info: dict[str, str], facts: dict[str, str]) -> Observation:
    kind, start, end = period_bounds(facts["period"], info["period_type"])
    return Observation(info["source"], facts["series_id"], facts["period"], kind, start.isoformat(), end.isoformat(),
                       facts["value"], facts["unit"], dataset=info["dataset"], indicator=info["indicator"],
                       base=info["base"], series_type=info["series_type"], area=info["area"], sector=info["sector"],
                       level=info["level"], code=info["code"], label=info["label"], status=facts["status"],
                       revision=facts["revision"], footnote=facts["footnote"], imputation=facts["imputation"],
                       notes=info["series_notes"])


def _sighting(seen: dt.datetime, rule: str, release_id: str, stage: str, first: bool) -> _Dating:
    return _Dating(seen, "first_seen", rule, release_id, stage, "first" if first else "revised", "live")


# The series a MoSPI probe answers, per family and base: the store's reads of them say when a release had not yet
# reached the API, for every table of that release (each release brings a new month into the headline)
HEADLINES: dict[tuple[str, str], tuple[str, ...]] = {
    ("cpi", "2024"): tuple(f"cpi_2024_all_india_{s}_general{y}" for s in ("combined", "rural", "urban")
                           for y in ("", "_yoy")),
    ("iip", "2022-23"): ("iip_2022_23_general", "iip_2022_23_general_yoy"),
    ("wpi", "2022-23"): ("wpi_2022_23_all",),
}


def _ladder(obs: Observation, calendar: mospi.ReleaseCalendar, ladders: dict) -> list | None:
    key = mospi.timing_key(obs)
    if key is None:
        return None
    if key not in ladders:
        family, base, period, estimate = key
        ladders[key] = calendar.stages(family, period, base=base, estimate=estimate)
    return ladders[key]


def _mospi_dating(obs: Observation, seen: dt.datetime, prior: dt.datetime | None, first: bool,
                  calendar: mospi.ReleaseCalendar, ladders: dict,
                  absent: Mapping[str, dt.datetime] | None = None) -> _Dating:
    """``prior``: the latest time the store read, without this value, an answer that would have held it (the same
    query, or for a change the earlier value itself); ``absent``: per release, the latest time the store read its
    family's headline without that release. A release time at or before either is not taken: the release came
    later than the calendar says (or the table lagged its headline), and the value is dated by its sighting."""
    key = mospi.timing_key(obs)
    if key is None:
        return _sighting(seen, "no release rule (RBI's data republished by MoSPI): dated by its sighting", "", "",
                         first)
    family, base, _, _ = key
    ladder = _ladder(obs, calendar, ladders)
    released = [(s, r) for s, r in ladder if r.public_from <= seen]
    if not released:
        return _sighting(seen, "read before every release the calendar or the rules give for it: dated by its "
                         "sighting", ladder[0][1].release_id, "", first)
    stage, release = released[-1]
    time, basis, rule, release_id = release.public_from, release.basis, f"{stage}: {release.rule}", release.release_id
    launch = mospi.BASE_LAUNCHES.get((family, base))
    if launch is not None and launch.time > time:
        if launch.time > seen:
            return _sighting(seen, f"read before the base's launch as recorded ({launch.note}): dated by its "
                             "sighting", release_id, stage, first)
        time, basis = launch.time, "release_rule"
        rule, release_id = f"{stage}, not before the base's launch: {launch.note}", launch.release_id or release_id
    headline = (absent or {}).get(release.release_id)
    if prior is not None and time <= prior:
        what = ("a change seen after the store last read the earlier value" if not first else
                "first seen after the store had read the same data without it")
        return _Dating(seen, "first_seen", f"{stage}: {what} ({_naive_text(prior)}), so not dated by the release of "
                       f"{_naive_text(time)}: dated by its sighting", release_id, stage,
                       "first" if first else "revised", "live")
    if headline is not None and time <= headline:
        return _Dating(seen, "first_seen", f"{stage}: the store read the family's headline at {_naive_text(headline)}"
                       f" without the release of {_naive_text(time)} ({release.release_id}): the release came later; "
                       "dated by its sighting", release_id, stage, "first" if first else "revised", "live")
    if stage == ladder[-1][0]:
        printed = "final"
    elif first and stage == ladder[0][0]:
        printed = "first"
    else:
        printed = "revised"
    return _Dating(time, basis, rule, release_id, stage, printed, "live" if seen - time <= LIVE_WINDOW else "later")


def _rule_dating(obs: Observation, seen: dt.datetime, first: bool, next_day: dt.date | None,
                 prior: dt.datetime | None = None) -> _Dating:
    """``prior``: the latest time the store read, without this value, an answer that would have held it (the same
    page or year's file, or a New York Fed or BIS window that covered its date)."""
    if not first:
        return _Dating(seen, "first_seen", "a change to a value the store held: dated by its sighting (no revision "
                       "calendar for this source)", "", "", "revised", "live")
    rule = gs.public_from_rule(obs, next_day=next_day)
    if rule.time is None or rule.time > seen:
        return _sighting(seen, f"read before the rule's time ({rule.text or 'none'}: {rule.rule}): dated by its "
                         "sighting", rule.release_id, "", True)
    if prior is not None and rule.time <= prior and gs.public_when_published(obs):
        return _sighting(seen, f"the store read the same data at {_naive_text(prior)}, after the rule's time "
                         f"({rule.text}: {rule.rule}), without this value: the publication came later; dated by its "
                         "sighting", rule.release_id, "", True)
    return _Dating(rule.time, rule.basis, rule.rule, rule.release_id, "", "first",
                   "live" if seen - rule.time <= LIVE_WINDOW else "later")


def _nyfed_next_days(data: _Data) -> dict[tuple[str, str], dt.date | None]:
    """For every New York Fed key, the next date the same rate has in the store (``None`` for the latest)."""
    days: dict[str, set[str]] = defaultdict(set)
    rates = {}
    for sid, period in data.vintages:
        if data.series[sid]["source"] == gs.NYFED:
            rates[(sid, period)] = rate = gs.nyfed_rate_of(sid)
            days[rate].add(period)
    following: dict[str, dict[str, dt.date | None]] = {}
    for rate, values in days.items():
        ordered = sorted(values)
        following[rate] = {d: (dt.date.fromisoformat(n) if n else None)
                           for d, n in zip(ordered, [*ordered[1:], None], strict=True)}
    return {key: following[rate][key[1]] for key, rate in rates.items()}


def _headline_absence(data: _Data) -> dict[str, dt.datetime]:
    """Per MoSPI release (``<family>_<month>``), the latest time the store read the family's headline without it:
    the ``prior_seen`` of the first vintage of that month's headline (each release brings a new month into the
    headline, and nothing else does)."""
    out: dict[str, dt.datetime] = {}
    for (family, _), ids in HEADLINES.items():
        for sid in ids:
            for period in data.periods_of(sid):
                first = data.vintages[(sid, period)][0]
                if first["prior_seen"]:
                    prior = _parse_ist(first["prior_seen"], "prior_seen")
                    release_id = f"{family}_{period}"
                    out[release_id] = max(out.get(release_id, prior), prior)
    return out


def _date_source(data: _Data, source: str) -> dict[tuple[str, str, int], _Dating]:
    """The dating of every vintage of one source (recomputed from the facts, so a better calendar reaches them all)."""
    out: dict[tuple[str, str, int], _Dating] = {}
    nexts = _nyfed_next_days(data) if source == gs.NYFED else {}
    ladders: dict = {}
    absent = _headline_absence(data) if source == mospi.SOURCE else {}
    for key in data.keys_of(source):
        info = data.series[key[0]]
        previous_seen: dt.datetime | None = None
        for n, facts in enumerate(data.vintages[key], start=1):
            seen = _parse_ist(facts["first_seen"], "first_seen")
            prior = previous_seen
            if facts["prior_seen"]:
                bound = _parse_ist(facts["prior_seen"], "prior_seen")
                prior = bound if prior is None else max(prior, bound)
            obs = _observation(info, facts)
            if source == mospi.SOURCE:
                dating = _mospi_dating(obs, seen, prior, n == 1, data.calendar, ladders, absent)
            else:
                dating = _rule_dating(obs, seen, n == 1, nexts.get(key), prior)
            out[(key[0], key[1], n)] = dating
            previous_seen = seen
    return out


def _dating_of(data: _Data, source: str) -> dict[tuple[str, str, int], _Dating]:
    if source not in data.dating:
        data.dating[source] = _date_source(data, source)
    return data.dating[source]


def _vintage_rows(data: _Data, source: str) -> list[dict[str, str]]:
    dating = _dating_of(data, source)
    rows = []
    for key in data.keys_of(source):
        info = data.series[key[0]]
        _, start, end = period_bounds(key[1], info["period_type"])
        for n, facts in enumerate(data.vintages[key], start=1):
            d = dating[(key[0], key[1], n)]
            rows.append({**facts, "period_start": start.isoformat(), "period_end": end.isoformat(),
                         "public_from": _naive_text(d.public_from), "public_from_basis": d.basis, "seen": d.seen,
                         "print": d.print, "stage": d.stage, "release_id": d.release_id, "public_from_rule": d.rule})
    return rows


# --- What is kept ----------------------------------------------------------------------------------------

_CPI_2024 = re.compile(r"cpi_2024(?:back)?_(?P<area>[a-z0-9_]+?)_(?:rural|urban|combined)_(?P<node>general|d\d{2}|"
                       r"g\d{2}_\d+|c\d{2}_\d+_\d+|sc[\d_]+|i[\d_]+)(?:_yoy)?")
_CPI_OLD_ITEM = re.compile(r"cpi_(?:2012|2010)(?:back)?_all_india_item_[a-z0-9_]+(?:_yoy)?")


def in_scope(obs: Observation, settings: Settings) -> bool:
    """Whether the store keeps ``obs`` under ``settings`` (see ``Settings``); the US and BIS series always."""
    if obs.source != mospi.SOURCE:
        return True
    sid = obs.series_id
    m = _CPI_2024.fullmatch(sid)
    if m is not None:
        if m.group("area") != "all_india" and not settings.states:
            return False
        if m.group("area") != "all_india":
            return m.group("node") == "general"
        return settings.cpi_levels == "items" or m.group("node").startswith(("general", "d", "g"))
    if _CPI_OLD_ITEM.fullmatch(sid):
        return settings.cpi_levels == "items"
    return ms.stored_by_default(sid)


# --- Absorbing parsed answers --------------------------------------------------------------------------------

@dataclass
class _Answer:
    """One parsed answer to keep: where it came from (``name``, the raw copy or the request; ``label`` for messages:
    the input's name or the URL), when the store had it at the latest (``seen``, aware), and the store's earlier reads
    of the same data (``reads``: what each covered and when, newest first) that bound a new value's time.

    An ingested copy of an answer the store itself received has ``received``: the rows of ``fetches.csv`` that
    received these bytes (row, ``latest``), in the log's order, and ``logged``: the store's good reads of the same
    data (row, window, time), so that each value is dated as an update would have dated it (``received_at``)."""

    source: str
    name: str
    result: ParseResult
    seen: dt.datetime
    url: str = ""
    label: str = ""
    reads: list[tuple[dt.date | None, dt.date | None, dt.datetime]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=lambda: {"rows": 0, "kept": 0, "new": 0, "changed": 0})
    received: list[tuple[int, dt.datetime]] = field(default_factory=list)
    logged: list[tuple[int, dt.date | None, dt.date | None, dt.datetime]] = field(default_factory=list)

    def prior(self, obs: Observation) -> dt.datetime | None:
        """The latest earlier read that covered ``obs`` (its date in the read's window, if it had one): India time,
        without a zone."""
        for start, end, when in self.reads:
            if _covers(start, end, obs):
                return when
        return None

    def received_at(self, obs: Observation, held: list[dict[str, str]] | None
                    ) -> tuple[dt.datetime, dt.datetime | None] | None:
        """For a received copy: when its value of ``obs`` came, as an update would have dated it, and the latest good
        read of the same data before that (``prior``); ``None`` if what the store holds is newer.

        A new value of a key is first seen at the first receipt of these bytes. A value that differs from the store's
        latest vintage is older than it (``None``) if that vintage was first seen after the last receipt, or the
        store read the same data again since; else it came with the first receipt after both that vintage and the
        store's last read of the same data before the last receipt (each later read showed the store's value)."""
        covering = [(row, when) for row, start, end, when in self.logged if _covers(start, end, obs)]
        last, latest = self.received[-1]
        first = self.received[0]
        if held and _identity(held[-1]) != _identity(obs.as_dict()):
            if held[-1]["first_seen"] > _ist(latest) or any(row > last for row, _ in covering):
                return None
            floor = max((row for row, _ in covering if row < last), default=-1)
            first = next(r for r in self.received if r[0] > floor and _ist(r[1]) >= held[-1]["first_seen"])
        return first[1], max((when for row, when in covering if row < first[0]), default=None)


def _covers(start: dt.date | None, end: dt.date | None, obs: Observation) -> bool:
    """Whether a read with this date window (``None``: open) covered ``obs``: always for a period that is not a day."""
    day = dt.date.fromisoformat(obs.period) if obs.period_type == "day" else None
    return day is None or ((start is None or start <= day) and (end is None or day <= end))


def _scope(url: str) -> tuple[str, dt.date | None, dt.date | None]:
    """What a read of ``url`` covers: the request without its date window (the New York Fed's ``startDate`` and
    ``endDate``, BIS's ``startPeriod``), and that window. Two reads cover the same data if their first parts are
    equal and a date falls in the window: a MoSPI query, a Treasury year, an EIA or H.10 page has no window."""
    parts = urllib.parse.urlsplit(url)
    rest, window = [], {}
    for name, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True):
        if name in ("startDate", "endDate", "startPeriod"):
            window[name] = value
        else:
            rest.append((name, value))
    start, end = window.get("startDate") or window.get("startPeriod"), window.get("endDate")

    def day(text: str | None) -> dt.date | None:
        try:
            return dt.date.fromisoformat(text) if text else None
        except ValueError:
            return None
    return f"{parts.netloc}{parts.path}?{urllib.parse.urlencode(rest)}", day(start), day(end)


@dataclass
class _Absorbed:
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    out_of_scope: int = 0
    older: int = 0  # values equal to an earlier vintage, seen again by an ingest: not taken as a change back
    superseded: int = 0  # values of a received copy older than what the store holds or read since: not taken
    conflicts: list[str] = field(default_factory=list)
    sources: set[str] = field(default_factory=set)


def _identity(values: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(values.get(k, "") for k in _IDENTITY)


def _series_facts(obs: Observation, url: str, held: dict[str, str] | None, release: str = "") -> dict[str, str]:
    """A series' facts from its latest answer; ``last_release``: the latest release date an answer printed (EIA's
    and H.10's "Release Date"), for the citation EIA asks for."""
    return {"series_id": obs.series_id, "source": obs.source, "dataset": obs.dataset, "indicator": obs.indicator,
            "base": obs.base, "series_type": obs.series_type, "area": obs.area, "sector": obs.sector,
            "level": obs.level, "code": obs.code, "label": obs.label, "unit": obs.unit, "period_type": obs.period_type,
            "source_url": (held or {}).get("source_url") or (_endpoint(url) if url else ""),
            "series_notes": obs.notes, "last_release": max(release, (held or {}).get("last_release", ""))}


def _absorb(data: _Data, answers: list[_Answer], *, settings: Settings, live: bool) -> _Absorbed:
    """Add the values of ``answers`` (in order: a later answer's value of a key wins) as new vintages where they differ.

    A new vintage is first seen at its answer's ``seen`` (the first answer of the run that held the value), never
    before the vintage it replaces; ``prior_seen`` is the latest earlier read that would have held it (``_Answer
    .prior``). ``live``: the answers were fetched now (an update). An ingest (saved answers of unknown age) does not
    take a value equal to an earlier, superseded vintage as a change back: it is counted in ``older``, nothing added.
    A copy of an answer the store received (``_Answer.received``) is of known age and dated value by value as the
    update would have dated it (``_Answer.received_at``): a value older than what the store holds is not taken
    (``superseded``), and a change back is taken like any change.
    """
    out = _Absorbed()
    incoming: dict[tuple[str, str], list] = {}  # key -> [observation, first answer with it, last answer with it]
    dated: dict[tuple[int, tuple[str, str]], tuple[dt.datetime, dt.datetime | None]] = {}  # received copies' values
    facts_by_series: dict[str, tuple[Observation, int]] = {}
    releases: dict[str, str] = {}
    for i, answer in enumerate(answers):
        answer.counts["rows"] = len(answer.result.observations)
        for obs in answer.result.observations:
            if not in_scope(obs, settings):
                out.out_of_scope += 1
                continue
            answer.counts["kept"] += 1
            if answer.received:
                when = answer.received_at(obs, data.vintages.get(obs.key))
                if when is None:
                    out.superseded += 1
                    continue
                dated[(i, obs.key)] = when
            held = incoming.get(obs.key)
            if held is None or _identity(held[0].as_dict()) != _identity(obs.as_dict()):
                if held is not None:
                    out.conflicts.append(f"{obs.series_id} {obs.period}: {held[0].value} in "
                                         f"{answers[held[2]].label}, {obs.value} in {answer.label} (the later kept)")
                incoming[obs.key] = [obs, i, i]
            else:
                held[2] = i
            facts_by_series[obs.series_id] = (obs, i)
            releases[obs.series_id] = max(releases.get(obs.series_id, ""), answer.result.meta.get("release_date", ""))
    for sid, (obs, i) in sorted(facts_by_series.items()):
        held = data.series.get(sid)
        if held is not None and held["source"] != obs.source:
            raise StoreError(f"series {sid} comes from {obs.source} in {answers[i].label} but is a {held['source']} "
                             "series in the store")
        if held is not None and held["period_type"] != obs.period_type:
            raise StoreError(f"series {sid} has {obs.period_type} periods in {answers[i].label} but "
                             f"{held['period_type']} periods in the store")
        data.series[sid] = _series_facts(obs, answers[i].url, held, releases[sid])
    for key in sorted(incoming):
        obs, first, _ = incoming[key]
        answer = answers[first]
        held = data.vintages.setdefault(key, [])
        values = obs.as_dict()
        if held and _identity(held[-1]) == _identity(values):
            out.unchanged += 1
            continue
        if not live and not answer.received and any(_identity(v) == _identity(values) for v in held[:-1]):
            out.older += 1
            continue
        conflict = next((c for c in out.conflicts if c.startswith(f"{key[0]} {key[1]}:")), "")
        seen, prior = dated.get((first, key)) or (answer.seen, answer.prior(obs))
        seen_text = max(_ist(seen), held[-1]["first_seen"]) if held else _ist(seen)
        facts = {"series_id": key[0], "period": key[1], "vintage": str(len(held) + 1), "value": obs.value,
                 "unit": obs.unit, "status": obs.status, "revision": obs.revision, "footnote": obs.footnote,
                 "imputation": obs.imputation, "first_seen": seen_text,
                 "prior_seen": _naive_text(prior) if prior is not None else "", "source_file": answer.name,
                 "notes": f"two answers disagreed: {conflict.split(': ', 1)[1]}" if conflict else ""}
        if held:
            last = held[-1]
            data.revisions.append({"seen": seen_text, "source": obs.source, "series_id": key[0], "period": key[1],
                                   "old_vintage": last["vintage"], "new_vintage": facts["vintage"],
                                   "old_value": last["value"], "new_value": obs.value, "old_status": last["status"],
                                   "new_status": obs.status, "old_revision": last["revision"],
                                   "new_revision": obs.revision, "old_first_seen": last["first_seen"],
                                   "prior_seen": facts["prior_seen"], "source_file": answer.name})
            out.changed += 1
            answer.counts["changed"] += 1
        else:
            out.new += 1
            answer.counts["new"] += 1
        held.append(facts)
        out.sources.add(obs.source)
    for source in out.sources:
        data.dating.pop(source, None)
    data._periods = None
    return out


def _raw_name(source: str, kind: str, when: dt.datetime, digest: str) -> str:
    ext = _KIND_EXT.get(kind, _EXT[source])
    return f"raw/{source}/{when.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}_{digest[:12]}.{ext}"


def _raw_path_of(data: _Data, digest: str) -> str:
    """The stored raw copy with this content (by the first 12 hex digits of its SHA-256), or ``""``."""
    if digest[:12] not in data.raw_shas:
        return ""
    for path in sorted(data.paths.raw.rglob(f"*_{digest[:12]}.*")):
        return str(path.relative_to(data.paths.root))
    return ""


# --- Requests: the recent window, the follow-ups of an answer, the history --------------------------------

def _today(now: dt.datetime) -> dt.date:
    return now.astimezone(IST).date()


def recent_requests(source: str, now: dt.datetime, settings: Settings | None = None,
                    held: Mapping[str, dt.date] | None = None) -> list[tuple[Request, str]]:
    """The requests an update of ``source`` sends every time (with their roles), before any follow-up or history.

    MoSPI: three cheap probes (the general CPI index of this year and last, IIP's general index, WPI's headline), each
    conditional, whose change brings the latest months' tables (``follow_ups``), and the national accounts (GDP and
    GVA by quarter, GDP by year: a page each). Treasury: this year's file (and last year's in the first week of
    January). New York Fed: the last 14 days of both groups. EIA, H.10: their history page (conditional). BIS: the
    last 60 observations of the policy rates, the last 60 days of the rupee rate.

    ``held``: the latest date the store holds (``_held_latest``): a window starts no later than it, so a run after
    an outage asks every day since (the New York Fed and the rupee from that date, the policy rates from it when it
    is more than 60 days old, Treasury's files from its year).
    """
    settings = settings or Settings()
    held = held or {}
    today = _today(now)
    if source == mospi.SOURCE:
        years = [today.year - 1, today.year]
        return [(mospi.cpi_2024_request(series="Current", year=years, division_code=0), "probe_cpi"),
                (mospi.iip_request(base_year="2022-23", type="General", year=years), "probe_iip"),
                (mospi.wpi_request(base_year="2022-23", major_group_code="1000000000"), "probe_wpi"),
                (mospi.nas_request(indicator_code=5, frequency="Quarterly"), "all"),
                (mospi.nas_request(indicator_code=1, frequency="Quarterly"), "all"),
                (mospi.nas_request(indicator_code=5, frequency="Annually"), "all")]
    if source == gs.UST:
        years = {today.year} | ({today.year - 1} if (today.month, today.day) <= (1, 7) else set())
        if held.get("ust") is not None:
            years |= set(range(max(held["ust"].year, gs.UST_FIRST_YEAR), today.year + 1))
        return [(gs.ust_request(year), "plain") for year in sorted(years, reverse=True)]
    if source == gs.NYFED:
        out = []
        for group in ("secured", "unsecured"):
            start = today - dt.timedelta(days=NYFED_DAYS)
            if held.get(group) is not None:
                start = min(start, held[group])
            out.append((gs.nyfed_search_request(group, start, today), "plain"))
        return out
    if source == gs.EIA:
        return [(gs.eia_brent_request(), "plain")]
    if source == gs.H10:
        return [(gs.h10_history_request("dat00_in"), "plain")]
    if source == gs.BIS:
        policy = held.get("cbpol")
        if policy is not None and policy < today - dt.timedelta(days=BIS_OBSERVATIONS):
            rates = gs.bis_request("WS_CBPOL", "D.IN+US", start_period=policy)
        else:
            rates = gs.bis_request("WS_CBPOL", "D.IN+US", last_n=BIS_OBSERVATIONS)
        start = today - dt.timedelta(days=BIS_DAYS)
        if held.get("xru") is not None:
            start = min(start, held["xru"])
        return [(rates, "plain"), (gs.bis_request("WS_XRU", "D.IN..", start_period=start), "plain")]
    raise ValueError(f"no source {source!r}; known: {', '.join(SOURCE_KEYS)}")


_HELD_SERIES = {"ust": ("ust_par_10y",), "secured": ("sofr",), "unsecured": ("effr",), "xru": ("usdinr_bis_daily",),
                "cbpol": ("policy_rate_in_bis", "policy_rate_us_bis")}


def _held_latest(data: _Data) -> dict[str, dt.date]:
    """The latest date the store holds per window of ``recent_requests`` (the earlier one of two series)."""
    out = {}
    for name, sids in _HELD_SERIES.items():
        latest = [max(periods) for periods in (data.periods_of(sid) for sid in sids) if periods]
        if len(latest) == len(sids):
            out[name] = dt.date.fromisoformat(min(latest))
    return out


def history_requests(source: str, now: dt.datetime, settings: Settings | None = None) -> list[tuple[Request, str]]:
    """The requests of a source's history, queued on its first update and fetched in steps (``Settings``).

    Treasury: one file a year from last year back to 1990 (newest first). New York Fed: both groups from their first
    day. H.10: the 1990s page. BIS: both series whole. EIA: none (its page holds the whole history). MoSPI
    (``mospi_history``): base-2024 CPI by month and sector from January 2025 (the first page of each, whose top-level
    nodes are checked, or every page for ``full`` with ``items``), the base-2024 back series, base-2012 CPI by year
    from 2013, IIP's general index and sectors on both bases, the 2011-12 national accounts, WPI's 2011-12 headline;
    ``full`` adds base-2012 items and IIP's use-based groups; ``states`` each state's general index.
    """
    settings = settings or Settings()
    today = _today(now)
    if source == mospi.SOURCE:
        return _mospi_history(today, settings)
    if source == gs.UST:
        return [(gs.ust_request(year), "plain") for year in range(today.year - 1, gs.UST_FIRST_YEAR - 1, -1)]
    if source == gs.NYFED:
        return [(gs.nyfed_search_request("secured", dt.date(2018, 4, 2), today), "plain"),
                (gs.nyfed_search_request("unsecured", dt.date(2000, 1, 1), today), "plain")]
    if source == gs.EIA:
        return []
    if source == gs.H10:
        return [(gs.h10_history_request("dat96_in"), "plain")]
    if source == gs.BIS:
        return [(gs.bis_request("WS_CBPOL", "D.IN+US"), "plain"), (gs.bis_request("WS_XRU", "D.IN.."), "plain")]
    raise ValueError(f"no source {source!r}; known: {', '.join(SOURCE_KEYS)}")


def _mospi_history(today: dt.date, settings: Settings) -> list[tuple[Request, str]]:
    full = settings.mospi_history == "full"
    cpi_role = "all" if full and settings.cpi_levels == "items" else "top"
    out = [(mospi.cpi_2024_request(series="Back"), "all")]
    year, month = CPI_2024_FIRST
    last = add_months(today.year, today.month, -3)  # the latest two months are the recent window's
    while (year, month) <= last:
        for sector in (1, 2, 3):
            out.append((mospi.cpi_2024_request(series="Current", year=year, month=month, sector_code=sector),
                        cpi_role))
        year, month = add_months(year, month, 1)
    for year in range(HISTORY_FROM, CPI_2012_LAST_YEAR + 1):
        out.append((mospi.cpi_index_request(base_year=2012, year=year), "all"))
    if full and settings.cpi_levels == "items":
        for year in range(HISTORY_FROM, CPI_2012_LAST_YEAR + 1):
            out.append((mospi.cpi_item_request(base_year=2012, year=year), "all"))
    for base in ("2022-23", "2011-12"):
        for kind in ("General", "Sectoral", *(("Use-based category",) if full else ())):
            out.append((mospi.iip_request(base_year=base, type=kind), "all"))
    for indicator, frequency in ((5, "Quarterly"), (1, "Quarterly"), (5, "Annually")):
        out.append((mospi.nas_request(base_year="2011-12", indicator_code=indicator, frequency=frequency), "all"))
    out.append((mospi.wpi_request(base_year="2011-12", major_group_code="1000000000"), "all"))
    if settings.states:
        out += [(mospi.cpi_2024_request(series="Current", state_code=s, division_code=0), "all") for s in STATE_CODES]
    return out


DETAIL_SERIES = {"probe_cpi": "cpi_2024_all_india_combined_g01_1", "probe_iip": "iip_2022_23_manufacturing"}


def _months_to_ask(latest: tuple[int, int], held: str | None, first: tuple[int, int]) -> list[tuple[int, int]]:
    """The latest month, the month before, and every month after ``held`` (the latest the store's tables hold) up to
    them, at most ``MAX_GAP_MONTHS``: an outage leaves no month of the tables unasked."""
    months = [latest, add_months(*latest, -1)]
    if held:
        month = tuple(int(x) for x in held.split("-"))
        while month < months[1] and len(months) < 2 + MAX_GAP_MONTHS:
            months.append(month)
            month = add_months(*month, 1)
    return [m for m in dict.fromkeys(months) if m >= first]


def follow_ups(request: Request, role: str, result: ParseResult, now: dt.datetime,
               settings: Settings | None = None, held: str | None = None) -> list[tuple[Request, str]]:
    """What an answer of ``role`` asks next: a changed probe the latest months' tables, a first page the other pages.

    CPI probe: base-2024 CPI of the latest month in the answer and the month before, by sector (every page, or for
    ``groups`` the first page if it holds every top-level node), and each state's general index with ``states``.
    IIP probe: every IIP row (``All``) of those two months. WPI probe: the four major groups' indices. ``held``: the
    latest month the store's CPI or IIP tables hold (``DETAIL_SERIES``): the months after it are asked too. A first
    page (``all``) asks the rest of its query; a ``top`` page only if it lacks a top-level node.
    """
    settings = settings or Settings()
    if role == "probe_cpi":
        months = sorted({o.period for o in result.observations if o.series_id.startswith("cpi_2024_all_india_")})
        if not months:
            return []
        latest = tuple(int(x) for x in months[-1].split("-"))
        detail = "all" if settings.cpi_levels == "items" else "top"
        out = []
        for y, m in _months_to_ask(latest, held, CPI_2024_FIRST):
            out += [(mospi.cpi_2024_request(series="Current", year=y, month=m, sector_code=s), detail)
                    for s in (1, 2, 3)]
        if settings.states:
            years = [_today(now).year - 1, _today(now).year]
            out += [(mospi.cpi_2024_request(series="Current", year=years, state_code=s, division_code=0), "all")
                    for s in STATE_CODES]
        return out
    if role == "probe_iip":
        months = sorted({o.period for o in result.observations if o.series_id == "iip_2022_23_general"})
        if not months:
            return []
        latest = tuple(int(x) for x in months[-1].split("-"))
        return [(mospi.iip_request(base_year="2022-23", type="All", year=y, month=m), "all")
                for y, m in _months_to_ask(latest, held, IIP_2022_23_FIRST)]
    if role == "probe_wpi":
        return [(mospi.wpi_request(base_year="2022-23", major_group_code=code), "all")
                for code in ("1100000000", "1200000000", "1300000000", "1400000000")]
    if role == "top" and not mospi.cpi_2024_top_levels(result):
        return []
    if role in ("all", "top"):
        return [(r, "page") for r in mospi.more_pages(request, result)]
    return []


def _probe_month(role: str, result: ParseResult) -> str:
    """The latest month a probe's answer holds (its headline), or ``""``."""
    prefix = {"probe_cpi": "cpi_2024_all_india_", "probe_iip": "iip_2022_23_general", "probe_wpi": "wpi_2022_23_all"}
    if role not in prefix:
        return ""
    return max((o.period for o in result.observations if o.series_id.startswith(prefix[role])), default="")


def _asks_month(request: Request, month: str) -> bool:
    """Whether a follow-up asks for ``month`` itself (CPI and IIP tables by month); WPI's ask every month."""
    params = dict(request.query)
    if "month_code" not in params:
        return request.kind == mospi.WPI
    return f"{params.get('year')}-{int(params['month_code']):02d}" == month


def resolve_sources(names: Iterable[str] | None) -> tuple[str, ...]:
    """Source keys from names (``all``, ``default`` or keys), in the order an update runs them; ``ValueError``
    for an unknown name."""
    if names is None:
        return DEFAULT_SOURCES
    if isinstance(names, str):
        names = [names]
    chosen = set()
    for name in names:
        name = str(name).strip().lower()
        if name == "all":
            chosen.update(SOURCE_KEYS)
        elif name == "default":
            chosen.update(DEFAULT_SOURCES)
        elif name in SOURCE_KEYS:
            chosen.add(name)
        else:
            raise ValueError(f"no source {name!r}: name some of {', '.join(SOURCE_KEYS)}, or all, or default")
    if not chosen:
        raise ValueError("no source named")
    return tuple(k for k in SOURCE_KEYS if k in chosen)


# --- The update ------------------------------------------------------------------------------------------

_STOPPING = frozenset({"blocked", "server_error", "network_error", "http_error", "client_error", "bad_answer",
                       "clock_error", "stopped", "cooling_down"})
_RETRY = frozenset({"run_cap", "daily_cap", "cooling_down", "stopped", "halted", "clock_error", "blocked",
                    "server_error", "network_error", "http_error", "bad_answer"})
_ERRORS = frozenset({"server_error", "network_error", "http_error", "bad_answer", "clock_error"})


@dataclass
class SourceResult:
    """What an update did for one source. ``outcome``: updated (new or changed values), unchanged, too_soon (no
    request), capped (no request: the run's cap was used), blocked, cooling_down, stopped, error or clock_error."""

    source: str
    outcome: str
    message: str
    requests: int = 0
    outcomes: dict[str, int] = field(default_factory=dict)
    values: int = 0
    new: int = 0
    changed: int = 0
    attention: list[str] = field(default_factory=list)
    reminder: str = ""

    @property
    def problem(self) -> str:
        """The exit-code class of the outcome: blocked, error or ''."""
        if self.outcome in ("blocked", "cooling_down", "stopped"):
            return "blocked"
        return "error" if self.outcome in ("error", "clock_error") else ""


@dataclass
class UpdateReport:
    """What one update did: a result per source, the reminders, the files written, a failed rebuild of the tables."""

    sources: list[SourceResult] = field(default_factory=list)
    written: list[str] = field(default_factory=list)
    derive_error: str = ""
    interrupted: list[str] = field(default_factory=list)
    started: str = ""  # the run's now, UTC: the command line puts it on every line of the log

    @property
    def exit_code(self) -> int:
        """3 if a source is blocked, cooling down or stopped; 1 for an error; 4 if something needs a look; else 0."""
        problems = {s.problem for s in self.sources}
        if "blocked" in problems:
            return EXIT_BLOCKED
        if "error" in problems:
            return EXIT_ERROR
        if self.derive_error or self.interrupted or any(s.attention for s in self.sources):
            return EXIT_ATTENTION
        return EXIT_OK

    def lines(self) -> list[tuple[bool, str]]:
        """``(is_problem, text)`` per line: a reminder, a line per source, what needs a look, the files written."""
        out: list[tuple[bool, str]] = []
        for s in self.sources:
            if s.reminder:
                out.append((False, f"{s.source}: {s.reminder}"))
        for text in self.interrupted:
            out.append((True, f"needs a look: {text}"))
        for s in self.sources:
            tag = "error: " if s.problem == "error" else ""
            out.append((bool(s.problem), f"{s.source}: {tag}{s.message}"))
            out += [(True, f"needs a look: {s.source}: {line}") for line in s.attention]
        if self.derive_error:
            out.append((True, f"needs a look: derived tables not rebuilt: {self.derive_error} (the previous ones are "
                              "kept; the values read were saved)"))
        sent = sum(s.requests for s in self.sources)
        new, changed = sum(s.new for s in self.sources), sum(s.changed for s in self.sources)
        out.append((False, f"done: {_n(sent, 'request')}, {_n(new, 'new value')}, {changed:,} changed; "
                           + (f"{_n(len(self.written), 'file')} written" if self.written else "no file changed")))
        return out


class _Run:
    """One update: the clock (the run's ``now`` plus its pauses), the request caps, the host states, the roles."""

    def __init__(self, data: _Data, *, now: dt.datetime, settings: Settings, transport: Transport | None,
                 user_agent: str, timeout: float, max_requests: int, max_per_host: int, min_interval: dt.timedelta,
                 resume: bool, sleep: Callable[[float], None] | None, legacy_tls: bool):
        self.data, self.now, self.settings = data, now, settings
        self.transport, self.user_agent, self.timeout = transport, user_agent, timeout
        self.max_requests, self.max_per_host, self.min_interval = max_requests, max_per_host, min_interval
        self.resume, self.legacy_tls = resume, legacy_tls
        self._sleep = sleep
        self.slept = 0.0
        self.sent = 0
        self.per_source: dict[str, int] = defaultdict(int)
        self.hosts = {h: ms.HostState.from_dict(d) for h, d in data.state["hosts"].items()}
        self.live: dict[str, ms.HostState] = {}
        self.committed = copy.deepcopy(data.state)
        self.roles: dict[str, str] = {}
        self.origin: dict[str, str] = {}
        self.extra: dict[str, dict] = {}  # per URL: what its answer is checked against (``_request_dict``)
        self.requested: set[str] = set()
        self.resumed: set[str] = set()
        self.parsed: dict[tuple[str, str], ParseResult | str] = {}
        self.attention: list[str] = []
        self.flights: list[dict] = []  # the requests the source being updated has sent and not yet committed
        self.written: set[str] = set()

    def clock(self) -> dt.datetime:
        return self.now + dt.timedelta(seconds=self.slept)

    def sleep(self, seconds: float) -> None:
        if self._sleep is None:
            import time
            time.sleep(seconds)
        else:
            self._sleep(seconds)
        self.slept += seconds

    def left(self, source: str) -> int:
        return min(self.max_requests - self.sent, self.max_per_host - self.per_source[source])

    def parse(self, request: Request, body: bytes) -> ParseResult | str:
        """The answer parsed (cached), or the refusal's text."""
        key = (request.url, _sha(body))
        if key not in self.parsed:
            try:
                self.parsed[key] = ms.parse(request, body)
            except SourceError as exc:
                self.parsed[key] = str(exc)
        return self.parsed[key]

    def record(self, host: str, state: ms.HostState) -> None:
        self.live[host] = state

    def attempt(self, source: str, url: str) -> None:
        """Commit the attempt before the request is sent (``state.json`` as last committed, the hosts as now, and
        every request of the source sent since its last commit, so that a run killed now leaves them logged)."""
        state = copy.deepcopy(self.committed)
        for host, hs in {**self.hosts, **self.live}.items():
            state["hosts"][host] = hs.to_dict()
        host = ms.check_request_url(url)[0]
        sent = (self.live.get(host) or self.hosts.get(host) or ms.HostState(host)).last_request
        self.flights.append({"url": url, "time": utc_text(sent or self.clock())})
        _source_state(state, source)["in_flight"] = list(self.flights)
        write_atomic(self.data.paths.state, _state_bytes(state), _TMP)

    def _transport(self, source: str) -> Transport:
        info = ms.SOURCES[source]
        context = legacy_renegotiation_context() if self.legacy_tls and source == mospi.SOURCE else None

        def default(url: str, headers: dict[str, str], timeout: float) -> FeedResponse:
            return urllib_transport(url, headers, timeout, ssl_context=context, max_bytes=info.max_wire_bytes)

        base = self.transport or default

        def send(url: str, headers: dict[str, str], timeout: float) -> FeedResponse:
            self.attempt(source, url)
            return base(url, headers, timeout)
        return send

    def _follow(self, source: str):
        def follow(request: Request, exchange) -> list[Request]:
            role = self.roles.get(request.url, "plain")
            if request.source != mospi.SOURCE or role in ("plain", "page"):
                return []
            result = self.parse(request, exchange.body)
            if isinstance(result, str):
                return []
            parent = self.origin.get(request.url, "recent")
            month = _probe_month(role, result)
            held = DETAIL_SERIES.get(role)
            held_month = max(self.data.periods_of(held), default=None) if held else None
            out = []
            for extra, extra_role in follow_ups(request, role, result, self.now, self.settings, held_month):
                if extra.url in self.requested:
                    continue
                self.roles.setdefault(extra.url, extra_role)
                self.origin.setdefault(extra.url, "history" if parent == "history" else "follow")
                if role.startswith("probe_") and month:
                    self.extra.setdefault(extra.url, {"group": f"{role}:{month}",
                                                      "expect": month if _asks_month(extra, month) else ""})
                elif extra_role == "page":
                    self.extra.setdefault(extra.url, {
                        "total": f"{result.meta.get('total_records')}/{result.meta.get('total_pages')}",
                        "parent_role": role})
                out.append(extra)
            return out
        return follow

    def fetch(self, source: str, requests: list[Request], validators: dict) -> list:
        left = self.left(source)
        if left <= 0:
            return [ms.Exchange(r, "run_cap", f"the run's cap is reached ({self.max_requests} requests, "
                                f"{self.max_per_host} to a host)") for r in requests]
        host = next(iter(ms.SOURCES[source].hosts))
        resume = [host] if self.resume and host not in self.resumed else []
        self.resumed.add(host)
        known = {u: (v.get("etag", ""), v.get("last_modified", "")) for u, v in validators.items()}
        report = ms.fetch(requests, now=self.clock(), hosts=self.hosts, transport=self._transport(source),
                          user_agent=self.user_agent, timeout=self.timeout, max_requests=left, enabled={source},
                          validators=known, follow=self._follow(source), sleep=self.sleep, resume=resume,
                          record=self.record, sent_before=self.sent)
        self.hosts.update(report.hosts)
        self.live.clear()
        sent = report.sent()
        self.sent += len(sent)
        self.per_source[source] += len(sent)
        self.requested.update(e.request.url for e in sent)
        self.attention += [f"{source}: {a}" for a in report.attention]
        return report.exchanges


def update(store: str | Path, *, now: dt.datetime, sources: Iterable[str] | None = None,
           transport: Transport | None = None, settings: Settings | None = None,
           max_requests: int = DEFAULT_MAX_REQUESTS, max_per_host: int = DEFAULT_MAX_PER_HOST,
           min_interval: dt.timedelta = DEFAULT_MIN_INTERVAL, user_agent: str = ms.DEFAULT_USER_AGENT,
           timeout: float = 60.0, resume: bool = False, retry_failed: bool = False,
           release_dates: str | Path | bytes | None = None, index_events: str | Path | bytes | None = None,
           sleep: Callable[[float], None] | None = None, legacy_tls: bool = False) -> UpdateReport:
    """Fetch what is new for each named source, within the caps and each host's interval, and keep it.

    ``sources``: keys of ``SOURCE_KEYS``, ``all`` or ``default`` (``DEFAULT_SOURCES`` when not given: MoSPI only
    when named). Per source, nothing if its last update began less than ``min_interval`` ago; else the recent window
    (``recent_requests``), the follow-ups their answers ask (``follow_ups``), what an earlier run left pending, then
    the history queue (``history_requests``, seeded on the source's first update), as far as ``max_requests`` (the
    run) and ``max_per_host`` allow. Every request goes through ``macro_sources.fetch`` (generic client name,
    conditional where honoured, the host's interval, its daily cap, the block rule); the attempt is committed before
    each request, the answers after each source, the derived tables at the end. ``resume`` ends a stop or a
    cool-down of the named sources' hosts (a person's decision, after checking the site in a browser);
    ``retry_failed`` queues again the history and pending requests an earlier run gave up on. ``release_dates`` and
    ``index_events``: tables kept by hand, checked and copied into the store before anything is sent. ``now``
    (timezone-aware) is when the run starts; ``sleep`` waits between two requests to a host (default
    ``time.sleep``); ``transport`` sends one GET (default the standard library's); tests pass fakes. A ``now``
    earlier than the store's own clock (the latest time a command recorded, ``state.json``'s ``clock``, or the
    latest request logged) by more than ``CLOCK_SLACK`` is a clock error: nothing is asked or written.
    """
    now = _check_now(now)
    keys = resolve_sources(sources)
    settings = settings or Settings()
    ms.check_generic_user_agent(user_agent)
    check_timeout(timeout)
    for name, value in (("max_requests", max_requests), ("max_per_host", max_per_host)):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= ms.MAX_REQUESTS:
            raise ValueError(f"{name} must be a whole number from 1 to {ms.MAX_REQUESTS}, not {value!r}")
    if not isinstance(min_interval, dt.timedelta) or not dt.timedelta(0) <= min_interval <= MAX_MIN_INTERVAL:
        raise ValueError(f"min_interval must be a duration from 0 to 7 days, not {min_interval!r}")
    release_bytes, events_bytes = _user_tables(release_dates, index_events)
    paths = StorePaths(Path(store))
    with _locked(paths, now, "update"):
        data = _load(paths)
        report = UpdateReport(started=utc_text(now))
        skew = _clock_skew(data, now)
        if skew:
            report.sources = [SourceResult(key, "clock_error", skew) for key in keys]
            return report
        _adopt_tables(data, None, release_bytes, events_bytes)
        _log_interrupted(data, report, now)
        if report.interrupted or release_bytes is not None or events_bytes is not None:
            tx = _transaction(paths)
            _stage_tables(data, tx, release_bytes, events_bytes)
            tx.put(paths.fetches, _csv_bytes(data.fetches, FETCH_COLUMNS))
            tx.put(paths.state, _state_bytes(data.state))
            report.written += tx.commit()
        run = _Run(data, now=now, settings=settings, transport=transport, user_agent=user_agent, timeout=timeout,
                   max_requests=max_requests, max_per_host=max_per_host, min_interval=min_interval, resume=resume,
                   sleep=sleep, legacy_tls=legacy_tls)
        for key in keys:
            result = _update_source(run, key, retry_failed)
            if key in NAMED_ONLY or not ms.SOURCES[key].enabled_by_default:
                result.reminder = REMINDERS.get(key, "")
            report.sources.append(result)
        data.state["settings"] = settings.as_dict()
        tx = _transaction(paths)
        report.derive_error = _stage_all(data, tx, now)
        data.state["derived_stale"] = None
        tx.put(paths.state, _state_bytes(data.state))
        report.written = sorted(set(report.written) | run.written | set(tx.commit()))
        return report


def _clock_floor(data: _Data) -> dt.datetime | None:
    """The latest time the store has recorded as reached: ``state.json``'s ``clock`` (each command's ``now``, or an
    update's clock at its last commit), or the latest request logged (its send time, no later than the real one)."""
    times = [_from_utc(data.state.get("clock"), f"{data.paths.state}: clock")]
    times += [_from_utc(r["time"], f"{data.paths.fetches}: time") for r in data.fetches if r.get("time")]
    known = [t for t in times if t is not None]
    return max(known) if known else None


def _clock_skew(data: _Data, now: dt.datetime) -> str:
    """A clock error's message if ``now`` is before what the store has recorded (more than ``CLOCK_SLACK``)."""
    floor = _clock_floor(data)
    if floor is None or floor - now <= CLOCK_SLACK:
        return ""
    return (f"clock error: the store has recorded {utc_text(floor)}, after now ({utc_text(now)}): values read now "
            "would be dated before values the store already holds; nothing done. Check the machine's clock")


def _advance_clock(state: dict, when: dt.datetime) -> None:
    held = _from_utc(state.get("clock"), "state.json: clock")
    state["clock"] = utc_text(max(held, when) if held is not None else when)


def _user_tables(release_dates, index_events) -> tuple[bytes | None, bytes | None]:
    """The bytes of a release-date table and an index-event table given by the user, checked before anything is
    written (``ValueError`` names the file and the line)."""
    out = []
    for source, reader in ((release_dates, mospi.read_release_dates), (index_events, gs.read_index_events)):
        if source is None:
            out.append(None)
            continue
        data, name = (source, "<bytes>") if isinstance(source, bytes) else (Path(source).read_bytes(), str(source))
        reader(data, name=name)
        out.append(data)
    return out[0], out[1]


def _adopt_tables(data: _Data, _tx, release_bytes: bytes | None, events_bytes: bytes | None) -> None:
    if release_bytes is not None:
        data.release_bytes = release_bytes
        data.calendar = _calendar(release_bytes)
        data.dating.pop(mospi.SOURCE, None)
    if events_bytes is not None:
        data.events_bytes = events_bytes


def _stage_tables(data: _Data, tx: Transaction, release_bytes: bytes | None, events_bytes: bytes | None) -> None:
    if release_bytes is not None:
        tx.put(data.paths.release_dates, release_bytes)
    if events_bytes is not None:
        tx.put(data.paths.index_events, events_bytes)


def _log_interrupted(data: _Data, report: UpdateReport, now: dt.datetime) -> None:
    """Log in ``fetches.csv`` the requests of a run that ended before their source's answers were kept, and clear
    them: the last one during its request, the others answered but not kept (each source's answers are kept
    together when it is done). This run asks them again."""
    for source in SOURCE_KEYS:
        src = data.state["sources"].get(source)
        flights = _flights(src.get("in_flight")) if src else []
        for n, flight in enumerate(flights, start=1):
            host = ms.check_request_url(flight["url"])[0]
            message = ("no answer recorded: the run ended during the request" if n == len(flights) else
                       "answered, but the run ended before the answers of its source were kept")
            data.fetches.append({"time": flight.get("time") or "", "source": source, "host": host,
                                 "url": flight["url"], "outcome": "interrupted", "message": message})
            report.interrupted.append(f"{source}: the request to {flight['url']} at {flight.get('time')}: {message}")
        if src:
            src["in_flight"] = None


def _update_source(run: _Run, source: str, retry_failed: bool) -> SourceResult:
    data, settings = run.data, run.settings
    src = _source_state(data.state, source)
    last = _from_utc(src["last_update"], f"{data.paths.state}: source {source}, last_update")
    if last is not None and last - run.now > CLOCK_SLACK:
        return SourceResult(source, "clock_error", f"clock error: the last update of {source} is recorded at "
                            f"{utc_text(last)}, after now ({utc_text(run.now)}); no request. Check the clock, then "
                            "clear last_update of that source in state.json")
    host = next(iter(ms.SOURCES[source].hosts))
    held = run.hosts.get(host)
    if held is not None and not run.resume:  # as rbi-store-poll: a stop or a cool-down is said before "too soon"
        if held.stopped is not None:
            return SourceResult(source, "stopped", f"{host} stopped after {held.blocks} blocks in a row (the last: "
                                f"{held.last_block_reason} at {utc_text(held.last_block) if held.last_block else '?'})"
                                ": no request until a person checks the site in a browser and runs macro-store-update "
                                f"--sources {source} --resume")
        if held.cooldown_until is not None and run.now < held.cooldown_until:
            return SourceResult(source, "cooling_down", f"{host} is cooling down after {held.last_block_reason}: no "
                                f"request before {utc_text(held.cooldown_until)}")
    if last is not None and run.now - last < run.min_interval:
        minutes = (run.now - last).total_seconds() / 60
        return SourceResult(source, "too_soon", f"too soon: its last update began at {utc_text(last)} "
                            f"({minutes:.0f} min ago); the minimum interval is "
                            f"{run.min_interval.total_seconds() / 60:.0f} min")
    if retry_failed and src["failed"]:
        for item in src["failed"]:
            if item["origin"] == "recent":
                continue  # asked again by every run
            target = src["backfill"]["queue"] if src["backfill"] and item["origin"] == "history" else src["pending"]
            retry = {k: v for k, v in item["request"].items() if k != "tries"}
            if retry["url"] not in {d["url"] for d in target}:
                target.append(retry)
            _ask_whole(src["validators"], retry["url"])
        src["failed"] = []
    recent = recent_requests(source, run.now, settings, _held_latest(data))
    recent_urls = {r.url for r, _ in recent}
    for request, role in recent:
        run.roles[request.url] = role
        run.origin[request.url] = "recent"
    pending = []
    for i, item in enumerate(src["pending"]):
        request, role = _request_from(item, source, f"state.json: source {source}, pending item {i + 1}")
        if request.url in recent_urls or request.url in run.origin:
            continue
        run.roles.setdefault(request.url, role)
        run.origin.setdefault(request.url, "pending")
        run.extra.setdefault(request.url, _extra_of(item))
        pending.append(request)
    backfill = _seed_history(src, source, run, recent_urls) if settings.backfill else None
    validators = src["validators"]
    run.flights = []
    exchanges = run.fetch(source, [r for r, _ in recent] + pending, validators)
    stop = any(e.outcome in _STOPPING for e in exchanges)
    queue = list(backfill["queue"]) if backfill else []
    for i, item in enumerate(queue):
        if stop or run.left(source) <= 0:
            break
        request, role = _request_from(item, source, f"state.json: source {source}, backfill queue item {i + 1}")
        if request.url in run.requested:
            continue
        run.roles.setdefault(request.url, role)
        run.origin.setdefault(request.url, "history")
        run.extra.setdefault(request.url, _extra_of(item))
        got = run.fetch(source, [request], validators)
        exchanges += got
        stop = any(e.outcome in _STOPPING for e in got)
    return _finish_source(run, source, src, backfill, exchanges)


def _seed_history(src: dict, source: str, run: _Run, recent_urls: set[str]) -> dict | None:
    """The source's history queue: seeded on its first update; a changed plan (settings) adds what it lacks."""
    backfill = src["backfill"]
    plan = run.settings.history_plan(source)
    if backfill is not None and backfill["plan"] == plan:
        return backfill
    done = set(backfill["done"]) if backfill else set()
    queue = list(backfill["queue"]) if backfill else []
    queued = {d["url"] for d in queue}
    new = [_request_dict(r, role) for r, role in history_requests(source, run.now, run.settings)
           if r.url not in recent_urls and r.url not in done and r.url not in queued]
    queue += new
    backfill = {"plan": plan, "planned": (backfill["planned"] if backfill else 0) + len(new), "done": sorted(done),
                "queue": queue, "seeded": backfill["seeded"] if backfill else utc_text(run.now),
                "finished": (backfill or {}).get("finished") if not new and not queue else None}
    if not queue and backfill["finished"] is None:
        backfill["finished"] = utc_text(run.now)
    src["backfill"] = backfill
    return backfill


def _page_problem(request: Request, result: ParseResult, extra: Mapping) -> str:
    """Why a MoSPI page does not fit its query: another page than the one asked, or a query whose size changed since
    its first page (a release between two pages: rows have moved from one page to another); ``""`` if it fits."""
    if request.source != mospi.SOURCE or "page" not in result.meta:
        return ""
    asked = dict(request.query).get("page", "1")
    if result.meta["page"] != asked:
        return f"MoSPI answered page {result.meta['page']} to a request for page {asked}"
    size = f"{result.meta.get('total_records')}/{result.meta.get('total_pages')}"
    if extra.get("total") and extra["total"] != size:
        return (f"the query's size changed between its pages (records/pages {extra['total']} on page 1, {size} "
                "now: a release came between them?)")
    return ""


def _ist_naive(when: dt.datetime) -> dt.datetime:
    return when.astimezone(IST).replace(tzinfo=None)


def _ask_whole(validators: dict[str, dict], url: str) -> None:
    """Make the next request of ``url`` unconditional (its etag and Last-Modified forgotten) and keep ``last_ok``,
    the store's record of its last good read of ``url``: that read bounds the time of what a later answer brings
    (``_reads_before``), so a refused answer or a retry must not erase it."""
    held = validators.get(url)
    if held is None:
        return
    if held.get("last_ok"):
        validators[url] = {"etag": "", "last_modified": "", "last_ok": held["last_ok"]}
    else:
        del validators[url]


def _reads_before(validators: Mapping[str, Mapping]) -> dict[str, list]:
    """The store's earlier reads by what they cover (``_scope``): ``(start, end, time)`` (India time), newest first.
    A URL's ``last_ok`` is the latest time its last good answer can have come (kept when a refused answer or a retry
    makes the next request unconditional: ``_ask_whole``)."""
    out: dict[str, list] = defaultdict(list)
    for url, v in validators.items():
        when = _from_utc(v.get("last_ok"), "validators")
        if when is not None:
            key, start, end = _scope(url)
            out[key].append((start, end, _ist_naive(when)))
    for reads in out.values():
        reads.sort(key=lambda r: r[2], reverse=True)
    return out


def _finish_source(run: _Run, source: str, src: dict, backfill: dict | None, exchanges: list) -> SourceResult:
    """Keep the answers of one source's exchanges, update its state and commit it.

    Each answer's values are first seen at the latest time it can have come (``Exchange.latest``), and bounded by
    the store's earlier reads of the same data (``_Answer.reads``). An answer the parser refuses, or a MoSPI page
    that does not fit its query, keeps no validator (the next request asks for it whole) and goes to ``failed``,
    whatever its origin (a recent request is asked again by every run anyway): it is not counted done in the
    history. A MoSPI table that lacks the month its headline showed stays pending, with the other tables of the same
    release (its ``group``), for up to ``MAX_LAG_TRIES`` reads.
    """
    data, settings = run.data, run.settings
    tx = _transaction(data.paths)
    validators = src["validators"]
    earlier = _reads_before(validators)
    answers: list[_Answer] = []
    by_url: dict[str, _Answer] = {}
    log_rows: list[dict[str, str]] = []
    pending, front, failed, done = [], [], [], set()
    outcomes: dict[str, int] = defaultdict(int)
    attention: list[str] = []
    staged: dict[str, str] = {}
    discovered = 0
    queued = {d["url"] for d in (backfill or {}).get("queue", [])}
    this_run: dict[str, list] = defaultdict(list)
    groups: dict[str, list] = defaultdict(list)  # group -> [(exchange, role, origin, lagging)]
    refused_urls: set[str] = set()
    for e in exchanges:
        url = e.request.url
        origin, role = run.origin.get(url, "follow"), run.roles.get(url, "plain")
        extra = run.extra.get(url, {})
        if origin == "history" and url not in queued:
            queued.add(url)
            discovered += 1
        if e.sent:
            outcomes[e.outcome] += 1
        row = {"time": utc_text(e.time) if e.time else "", "latest": utc_text(e.latest) if e.latest else "",
               "source": source, "host": e.request.host, "url": url,
               "http_status": "" if e.http_status is None else str(e.http_status), "outcome": e.outcome,
               "bytes": str(e.wire_bytes), "etag": e.etag, "last_modified": e.last_modified, "message": e.message}
        scope, start, end = _scope(url)
        read = None
        if e.outcome == "ok":
            digest = _sha(e.body)
            raw = ""
            if settings.keeps_raw(source):
                raw = _raw_path_of(data, digest) or staged.get(digest, "")
                if not raw:
                    raw = staged[digest] = _raw_name(source, e.request.kind, e.time, digest)
                    tx.put(data.paths.root / raw, e.body)
                    data.raw_shas.add(digest[:12])
            row["raw"] = raw
            result = run.parse(e.request, e.body)
            reason = result if isinstance(result, str) else _page_problem(e.request, result, extra)
            if reason:
                row["message"] = f"refused: {reason}"
                attention.append(f"{url}: the answer was refused: {reason}" + (f" (kept as {raw})" if raw else ""))
                # its own validator is never kept; the next request asks for it whole, not "if changed", and the
                # record of the last good read of this URL (last_ok) stays: it bounds what a later answer brings
                _ask_whole(validators, url)
                refused_urls.add(url)
                failed.append({"time": utc_text(e.time), "reason": reason, "raw": raw, "origin": origin,
                               "request": _request_dict(e.request, role, extra)})
                if not isinstance(result, str) and "size changed" in reason:  # read the query again from page 1
                    first = mospi.page_request(e.request, 1)
                    _ask_whole(validators, first.url)
                    target = front if origin == "history" else pending
                    if first.url not in {d["url"] for d in target}:
                        target.append(_request_dict(first, extra.get("parent_role") or "all"))
            else:
                reads = sorted(earlier.get(scope, []) + this_run[scope], key=lambda r: r[2], reverse=True)
                answer = _Answer(source, raw or f"{utc_text(e.time)} {url}", result, e.latest, url, url, reads)
                answers.append(answer)
                by_url[url] = answer
                validators[url] = {"etag": e.etag, "last_modified": e.last_modified, "last_ok": utc_text(e.latest)}
                read = result
        elif e.outcome == "not_modified":
            validators.setdefault(url, {"etag": e.etag, "last_modified": e.last_modified})
            validators[url]["last_ok"] = utc_text(e.latest)
            read = True  # the same content as the last read
        elif e.outcome == "duplicate":
            done.add(url)
        elif e.outcome == "client_error":
            failed.append({"time": utc_text(e.time) if e.time else "", "reason": e.message, "raw": "",
                           "origin": origin, "request": _request_dict(e.request, role, extra)})
        elif e.outcome in _RETRY and origin != "recent":
            target = front if origin == "history" else pending
            if url not in {d["url"] for d in target}:
                target.append(_request_dict(e.request, role, extra))
        if read is not None:
            this_run[scope].append((start, end, _ist_naive(e.latest)))
            lagging = bool(extra.get("expect")) and (read is True or not any(o.period == extra["expect"]
                                                                              for o in read.observations))
            if extra.get("group"):
                groups[extra["group"]].append((e, role, origin, lagging))
            else:
                done.add(url)
        if e.sent:
            log_rows.append(row)
    for group, members in groups.items():
        lags = any(lagging for *_, lagging in members)
        waiting = any(d.get("group") == group and d.get("expect") for d in pending)  # not asked yet (the caps)
        for e, role, origin, lagging in members:
            extra = dict(run.extra.get(e.request.url, {}))
            if not lagging and (extra.get("expect") or not (lags or waiting)):
                done.add(e.request.url)  # it holds the release's month, or the release has reached every table
                continue
            # the release has not reached every table yet (or that is not known yet): ask this one again; a table of
            # an earlier month too, as its revision may lag with it
            extra["tries"] = int(extra.get("tries") or 0) + (1 if lags else 0)
            if extra["tries"] >= MAX_LAG_TRIES:
                reason = (f"the release of {group.split(':', 1)[1]} had not reached this table after "
                          f"{extra['tries']} reads (its headline had): given up")
                attention.append(f"{e.request.url}: {reason}; --retry-failed asks again")
                failed.append({"time": utc_text(e.time), "reason": reason, "raw": "", "origin": origin,
                               "request": _request_dict(e.request, role, extra)})
            elif e.request.url not in {d["url"] for d in pending}:
                pending.append(_request_dict(e.request, role, extra))
    absorbed = _absorb(data, answers, settings=settings, live=True)
    for row in log_rows:
        answer = by_url.get(row["url"])
        if answer is not None and row["outcome"] == "ok":
            row.update({k: str(v) for k, v in answer.counts.items()})
    data.fetches += log_rows
    attention += [f"two answers disagreed on {c}" for c in absorbed.conflicts]
    attention += [a.split(": ", 1)[1] for a in run.attention if a.startswith(f"{source}: ")]
    run.attention = [a for a in run.attention if not a.startswith(f"{source}: ")]
    cutoff = run.now - dt.timedelta(days=VALIDATOR_DAYS)
    src["validators"] = {u: v for u, v in sorted(validators.items())
                         if (_from_utc(v.get("last_ok"), "validators") or run.now) >= cutoff}
    src["pending"] = pending
    failed_urls = {f["request"]["url"] for f in failed}
    pending_urls = {d["url"] for d in pending}
    if backfill is not None:
        front_urls = {f["url"] for f in front}
        rest = [d for d in backfill["queue"] if d["url"] not in done and d["url"] not in failed_urls
                and d["url"] not in pending_urls and d["url"] not in front_urls]
        had = bool(backfill["queue"])
        history = {u for u in done if run.origin.get(u) == "history"}
        history |= {d["url"] for d in backfill["queue"] if d["url"] in done}  # answered as a recent request too
        backfill["queue"] = front + rest
        backfill["done"] = sorted((set(backfill["done"]) | history) - failed_urls)
        backfill["planned"] += discovered
        if had and not backfill["queue"]:
            backfill["finished"] = utc_text(run.now)
        src["backfill"] = backfill
    answered_now = {e.request.url for e in exchanges if e.outcome in ("ok", "not_modified")} - refused_urls
    # A recent window (the New York Fed's dates, BIS's startPeriod) moves every day, so a refused one is never asked
    # again by its URL: it leaves the list when a later read of the same data answers, or a later window fails too.
    superseded = {_scope(u)[0] for u in answered_now} | {_scope(f["request"]["url"])[0] for f in failed
                                                         if f["origin"] == "recent"}
    kept = [f for f in src["failed"] if f["request"]["url"] not in answered_now | failed_urls
            and not (f["origin"] == "recent" and _scope(f["request"]["url"])[0] in superseded)]
    src["failed"] = kept + failed  # one entry per request failing now, none dropped: every one can be retried
    src["in_flight"] = None
    run.flights = []
    if any(e.sent for e in exchanges):  # the minimum interval runs from an update that asked something
        src["last_update"] = utc_text(run.now)
        _advance_clock(data.state, run.clock())
    host = next(iter(ms.SOURCES[source].hosts))
    if host in run.hosts:
        data.state["hosts"][host] = run.hosts[host].to_dict()
    result = _source_result(source, exchanges, outcomes, absorbed, attention, src)
    src["last_outcome"], src["last_message"] = result.outcome, result.message
    data.state["derived_stale"] = data.state.get("derived_stale") or utc_text(run.now)
    tx.put(data.paths.observations(source), _csv_bytes(_vintage_rows(data, source), VINTAGE_COLUMNS))
    tx.put(data.paths.series, _csv_bytes(_series_rows(data), SERIES_COLUMNS))
    tx.put(data.paths.revisions, _csv_bytes(data.revisions, REVISION_COLUMNS))
    tx.put(data.paths.fetches, _csv_bytes(data.fetches, FETCH_COLUMNS))
    tx.put(data.paths.state, _state_bytes(data.state))
    run.written.update(tx.commit())
    run.committed = copy.deepcopy(data.state)
    return result


def _source_result(source: str, exchanges: list, outcomes: dict[str, int], absorbed: _Absorbed,
                   attention: list[str], src: dict) -> SourceResult:
    sent = sum(outcomes.values())
    skipped = [e for e in exchanges if not e.sent]
    history = src["backfill"]
    progress = ""
    given_up = sum(f["origin"] == "history" for f in src["failed"])
    if history is not None and (history["planned"] or history["queue"]):
        progress = (f"; history: {_n(len(history['done']), 'request')} done, {len(history['queue']):,} to go"
                    + (f", {given_up:,} given up (--retry-failed asks them again)" if given_up else "")
                    + (" (finished)" if not history["queue"] and not given_up else ""))
    if src["pending"]:
        progress += f"; {_n(len(src['pending']), 'request')} pending"
    counts = ", ".join(f"{n} {o.replace('_', ' ')}" for o, n in sorted(outcomes.items()))
    blocked = next((e for e in exchanges if e.outcome == "blocked"), None)
    if blocked is not None:
        return SourceResult(source, "blocked", blocked.message, sent, dict(outcomes), absorbed.new + absorbed.changed,
                            absorbed.new, absorbed.changed, attention)
    errors = [e for e in exchanges if e.sent and e.outcome in _ERRORS]
    values = f"{_n(absorbed.new, 'new value')}, {absorbed.changed:,} changed"
    if errors:
        return SourceResult(source, "error", f"{errors[0].message} ({_n(sent, 'request')}: {counts}; {values})"
                            f"{progress}", sent, dict(outcomes), absorbed.new + absorbed.changed, absorbed.new,
                            absorbed.changed, attention)
    if not sent:
        for outcome in ("stopped", "cooling_down", "clock_error"):
            hit = next((e for e in skipped if e.outcome == outcome), None)
            if hit is not None:
                return SourceResult(source, "error" if outcome == "clock_error" else outcome, hit.message, 0, {}, 0,
                                    0, 0, attention)
        why = skipped[0].message if skipped else "nothing to ask"
        return SourceResult(source, "capped" if skipped else "unchanged", f"no request: {why}{progress}", 0, {}, 0,
                            0, 0, attention)
    outcome = "updated" if absorbed.new or absorbed.changed else "unchanged"
    capped = sum(1 for e in skipped if e.outcome in ("run_cap", "daily_cap"))
    tail = f"; {capped} left for a later run (caps)" if capped else ""
    return SourceResult(source, outcome, f"{_n(sent, 'request')} ({counts}): {values}{tail}{progress}", sent,
                        dict(outcomes), absorbed.new + absorbed.changed, absorbed.new, absorbed.changed, attention)


# --- Writing everything: observations, series, the derived tables -----------------------------------------

def _stage_all(data: _Data, tx: Transaction, now: dt.datetime) -> str:
    """Stage every observation table, ``series.csv``, the logs and the derived tables; the error of a failed rebuild
    (the previous derived tables are then kept and the error recorded in ``state.json``), or ``""``."""
    for source in SOURCE_KEYS:
        rows = _vintage_rows(data, source)
        if rows or data.paths.observations(source).exists():
            tx.put(data.paths.observations(source), _csv_bytes(rows, VINTAGE_COLUMNS))
    tx.put(data.paths.series, _csv_bytes(_series_rows(data), SERIES_COLUMNS))
    tx.put(data.paths.revisions, _csv_bytes(data.revisions, REVISION_COLUMNS))
    tx.put(data.paths.fetches, _csv_bytes(data.fetches, FETCH_COLUMNS))
    try:
        tables = derive(data)
    except Exception as exc:  # a bug: keep the values read and the previous tables, and say so
        error = f"{type(exc).__name__}: {exc}"
        held = data.state.get("derived")
        data.state["derived"] = {"error": error, "since": held["since"] if held else utc_text(now)}
        return error
    for name, rows in tables.items():
        tx.put(data.paths.derived / f"{name}.csv", _csv_bytes(rows, DERIVED_COLUMNS[name]))
    data.state["derived"] = None
    return ""


def _citation(info: dict[str, str], entry: ms.Series, last_seen: str) -> str:
    text = entry.citation
    if info["source"] == mospi.SOURCE:
        return text.format(dataset=info["dataset"], url=info["source_url"] or mospi.API, date=last_seen[:10])
    if info["source"] == gs.NYFED:
        return text.format(data=info["label"] or entry.name, publisher="[the publisher of your presentation]")
    if info["source"] == gs.EIA:  # EIA asks for the publication date: its page's "Release Date", else the sighting
        day = info.get("last_release") or last_seen[:10]
        month = dt.datetime.strptime(day[:10], "%Y-%m-%d").strftime("%b %Y") if day else "date"
        return text.format(date=month)
    if info["source"] == gs.BIS:
        return text.format(dataset=info["dataset"])
    return text


def _series_rows(data: _Data) -> list[dict[str, str]]:
    """``series.csv``: per series held, its facts (from the latest answer), catalogue entry and what the store holds."""
    periods: dict[str, list[str]] = defaultdict(list)
    for sid, period in data.vintages:
        periods[sid].append(period)
    rows = []
    for sid in sorted(periods):
        info = data.series[sid]
        held = sorted(periods[sid], key=lambda p: period_bounds(p, info["period_type"])[1])
        dating = _dating_of(data, info["source"])
        last = data.vintages[(sid, held[-1])]
        last_seen = max(v["first_seen"] for p in held for v in data.vintages[(sid, p)])
        entry = ms.describe(_observation(info, last[-1]))
        public = dating[(sid, held[-1], len(last))].public_from
        rows.append({**info, "publisher": entry.publisher, "name": entry.name, "frequency": entry.frequency,
                     "period_format": entry.period_format, "first_period": held[0], "last_period": held[-1],
                     "periods": str(len(held)), "vintages": str(sum(len(data.vintages[(sid, p)]) for p in held)),
                     "revised_periods": str(sum(len(data.vintages[(sid, p)]) > 1 for p in held)),
                     "last_value": last[-1]["value"], "last_public_from": _naive_text(public),
                     "last_first_seen": last_seen, "update_info": entry.update_info,
                     "citation": _citation(info, entry, last_seen), "terms": entry.terms, "terms_url": entry.terms_url,
                     "in_catalogue": str(ms.series_info(sid) is not None), "aliases": entry.aliases,
                     "catalogue_notes": entry.notes if ms.series_info(sid) is not None else ""})
    return rows


@dataclass(frozen=True)
class _Vintage:
    facts: dict[str, str]
    dating: _Dating
    number: int


def _vintages_of(data: _Data, sid: str) -> dict[str, list[_Vintage]]:
    """Every vintage of a series by period, with its dating (empty if the store has none)."""
    info = data.series.get(sid)
    if info is None:
        return {}
    dating = _dating_of(data, info["source"])
    return {period: [_Vintage(v, dating[(sid, period, n)], n) for n, v in enumerate(data.vintages[(sid, period)],
                                                                                     start=1)]
            for period in data.periods_of(sid)}


def _known_at(vintages: list[_Vintage], t: dt.datetime) -> _Vintage | None:
    """The latest vintage public at ``t`` (India time): public_from rises from one vintage to the next."""
    best = None
    for v in vintages:
        if v.dating.public_from <= t:
            best = v
    return best


_BREAK = re.compile(r"[Ff]rom (\d{1,2} [A-Z][a-z]{2} \d{4}) onwards")
NYFED_BREAKS = (("effr", "2016-03-01", "the New York Fed's EFFR rows change layout from 2016-03-01 (percentiles and "
                 "volume from then, the intraday range and standard deviation before): check before splicing across"),
                ("fomc_target_low", "2008-12-16", "the FOMC target is a range from 2008-12-16 (fomc_target_low and "
                 "fomc_target_high), a point before (fomc_target_low alone)"))


def _breaks(data: _Data) -> dict[str, list[tuple[str, str]]]:
    """Per column of ``global_daily``, the dates from which its series has a new definition, and the note to make:
    BIS's own (its ``COMPILATION`` text, "From 3 Apr 2001 onwards: ..."), and the New York Fed's layout changes."""
    out: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for _, columns in GLOBAL_GROUPS:
        for column, sid in columns:
            info = data.series.get(sid)
            for m in _BREAK.finditer(info["series_notes"] if info else ""):
                try:
                    day = dt.datetime.strptime(m.group(1), "%d %b %Y").date().isoformat()
                except ValueError:
                    continue
                out[column].append((day, f"{column}: a new definition from {day} (BIS: {info['series_notes']}): not "
                                          "comparable with the rows before"))
    for column, day, note in NYFED_BREAKS:
        out[column].append((day, note))
    return out


def global_daily(data: _Data) -> list[dict[str, str]]:
    """One row per date any US or BIS series has: the latest value of each, when each group was public, the row's.

    The values are each date's latest vintage: what was known at a time ``t`` is ``as_of`` (a row's ``public_from``
    is that of its latest values, so a row whose value was revised drops out of ``public_from <= t`` until the
    revision is public, though its first value was public before). A row on or just after a series' change of
    definition (BIS's repo rate for India from 3 Apr 2001, the bank rate before; BIS's US target mid-point from 19
    Dec 1985, the effective rate before; the New York Fed's layouts) says so in ``notes``.
    """
    cells: dict[str, dict[str, _Vintage]] = defaultdict(dict)
    for _, columns in GLOBAL_GROUPS:
        for column, sid in columns:
            for period, held in _vintages_of(data, sid).items():
                cells[period][column] = held[-1]
    marks: dict[str, list[str]] = defaultdict(list)
    for column, breaks in _breaks(data).items():
        days = sorted(d for d in cells if column in cells[d])
        for day, note in breaks:
            first = next((d for d in days if d >= day), None)
            if first is not None and first <= (dt.date.fromisoformat(day) + dt.timedelta(days=31)).isoformat():
                marks[first].append(note)
    rows = []
    for day in sorted(cells):
        row: dict[str, str] = {"date": day}
        times, seen, revised, marked, footnotes = [], set(), [], [], []
        for group, columns in GLOBAL_GROUPS:
            group_times = []
            for column, _ in columns:
                v = cells[day].get(column)
                if v is None:
                    continue
                row[column] = v.facts["value"]
                group_times.append(v.dating.public_from)
                seen.add(v.dating.seen)
                if v.number > 1:
                    revised.append(v.facts["first_seen"])
                if v.facts["revision"] == "r":
                    marked.append(column)
                if v.facts["footnote"]:
                    footnotes.append(f"{column} {v.facts['footnote']}")
            if group_times:
                row[group] = _naive_text(max(group_times))
                times += group_times
        notes = ([f"marked revised (r) by the New York Fed: {', '.join(marked)}"] if marked else []) + \
            ([f"the New York Fed's footnote: {', '.join(footnotes)}"] if footnotes else []) + marks.get(day, [])
        row.update(public_from=_naive_text(max(times)), seen="later" if "later" in seen else "live",
                   revised=max(revised, default=""), notes="; ".join(notes))
        rows.append(row)
    return rows


def _measure_columns(family: str, measure: str) -> tuple[str, str, str]:
    prefix = f"cpi_{measure}" if family == "cpi" else measure
    return f"{prefix}_index", f"{prefix}_yoy", f"{prefix}_public_from"


def _snapshots(entries: dict[str, list[_Vintage]]) -> list[dt.datetime]:
    return sorted({v.dating.public_from for held in entries.values() for v in held})


def _row_head(used: list[_Vintage], t: dt.datetime) -> dict[str, str]:
    """The columns a row of a monthly or quarterly table shares: from the vintages it holds, public at ``t``."""
    at_t = [v for v in used if v.dating.public_from == t]
    top = max(at_t, key=lambda v: _PRINT_RANK.get(v.dating.print, 0))
    statuses = {v.facts["status"] for v in used}
    revised = [v.facts["first_seen"] for v in used if v.number > 1]
    return {"print": top.dating.print, "stage": top.dating.stage, "status": statuses.pop() if len(statuses) == 1
            else "", "public_from": _naive_text(t), "first_public_from": _naive_text(min(v.dating.public_from
                                                                                            for v in used)),
            "public_from_basis": "/".join(sorted({v.dating.basis for v in at_t})),
            "release_id": "+".join(sorted({v.dating.release_id for v in at_t if v.dating.release_id})),
            "seen": "later" if any(v.dating.seen == "later" for v in used) else "live",
            "revised": max(revised, default="")}


def _pct_change(new: str, old: str) -> str:
    return f"{(float(new) / float(old) - 1) * 100:.2f}"


def india_monthly(data: _Data) -> list[dict[str, str]]:
    """One row per family, base, month and moment a value of it became public (a print): CPI, IIP, WPI as known then.

    Each row holds every measure as known at its ``public_from`` (the latest vintage public by then); a measure's own
    ``_public_from`` says when its values were. WPI's year-on-year change is computed here, on one base, from the
    indices of the month and of the month a year before as known at ``public_from``. Bases are never spliced.
    """
    rows = []
    for (family, base), measures in _MONTHLY_MEASURES.items():
        series = {(measure, part): _vintages_of(data, sid) for measure, (index, yoy) in measures.items()
                  for part, sid in (("index", index), ("yoy", yoy)) if sid is not None}
        months = sorted({p for held in series.values() for p in held})
        for month in months:
            entries = {k: held[month] for k, held in series.items() if month in held}
            for t in _snapshots(entries):
                used, row, notes = [], {"month": month, "family": family, "base": base}, []
                for measure in measures:
                    index_col, yoy_col, public_col = _measure_columns(family, measure)
                    times = []
                    for part, column in (("index", index_col), ("yoy", yoy_col)):
                        v = _known_at(entries.get((measure, part), []), t)
                        if v is not None:
                            row[column] = v.facts["value"]
                            used.append(v)
                            times.append(v.dating.public_from)
                    if family in ("wpi",) and measure in _COMPUTED_YOY and row.get(index_col):
                        y, m = (int(x) for x in month.split("-"))
                        earlier = f"{y - 1}-{m:02d}"
                        old = _known_at(series[(measure, "index")].get(earlier, []), t)
                        if old is not None:
                            row[yoy_col] = _pct_change(row[index_col], old.facts["value"])
                            times.append(old.dating.public_from)
                            notes.append(f"{yoy_col} computed here from the indices of {month} and {earlier} on base "
                                         f"{base} as known at public_from")
                    if times:
                        row[public_col] = _naive_text(max(times))
                if not used:
                    continue
                if family == "iip" and base == "2022-23" and "2023-04" <= month <= "2024-03":
                    notes.append("iip_yoy: none published for FY2023-24 on base 2022-23 (no year-ago value on it)")
                later = sorted({_measure_columns(family, m)[2] for m in measures
                                if row.get(_measure_columns(family, m)[2], "") not in ("", _naive_text(t))})
                if later:
                    notes.append(f"public earlier than the row: {', '.join(later)}")
                row.update(_row_head(used, t))
                row["notes"] = "; ".join(notes)
                rows.append(row)
    return sorted(rows, key=lambda r: (r["month"], r["family"], r["base"], r["public_from"]))


def _year_before(quarter: str) -> str:
    start = int(quarter[:4]) - 1
    return f"{start}-{(start + 1) % 100:02d}{quarter[7:]}"


def india_quarterly(data: _Data) -> list[dict[str, str]]:
    """One row per base, quarter and moment a value of it became public: GDP and GVA, current and constant prices.

    Year-on-year changes of the constant-price values come from the same base as known at the row's
    ``public_from``. A quarter's estimate is revised at several releases: ``stage`` says which one dated the row.
    """
    rows = []
    for base, measures in _QUARTERLY_MEASURES.items():
        series = {(measure, part): _vintages_of(data, sid) for measure, sids in measures.items()
                  for part, sid in zip(("current", "constant"), sids, strict=True)}
        quarters = sorted({p for held in series.values() for p in held})
        for quarter in quarters:
            entries = {k: held[quarter] for k, held in series.items() if quarter in held}
            for t in _snapshots(entries):
                used, row, notes = [], {"quarter": quarter, "base": base}, []
                for measure in measures:
                    times = []
                    for part in ("current", "constant"):
                        v = _known_at(entries.get((measure, part), []), t)
                        if v is not None:
                            row[f"{measure}_{part}_cr"] = v.facts["value"]
                            used.append(v)
                            times.append(v.dating.public_from)
                    if times:
                        row[f"{measure}_public_from"] = _naive_text(max(times))
                    old = _known_at(series[(measure, "constant")].get(_year_before(quarter), []), t)
                    if old is not None and row.get(f"{measure}_constant_cr"):
                        row[f"{measure}_constant_yoy"] = _pct_change(row[f"{measure}_constant_cr"],
                                                                     old.facts["value"])
                if not used:
                    continue
                if any(row.get(f"{m}_constant_yoy") for m in measures):
                    notes.append(f"year-on-year changes computed here from {_year_before(quarter)} on base {base} as "
                                 "known at public_from")
                row.update(_row_head(used, t))
                row["notes"] = "; ".join(notes)
                rows.append(row)
    return sorted(rows, key=lambda r: (r["quarter"], r["base"], r["public_from"]))


def index_event_rows(data: _Data) -> list[dict[str, str]]:
    """The index-event table the user gave, checked, each event with the time from which it was public."""
    if data.events_bytes is None:
        return []
    rows = []
    for event in gs.load_index_events(data.events_bytes, name=str(data.paths.index_events)):
        public = gs.index_event_public_from(event)
        rows.append({"event_id": event.event_id, "provider": event.provider, "index_name": event.index_name,
                     "event_type": event.event_type, "announced_date": event.announced,
                     "announced_time": event.announced_time.strftime("%H:%M") if event.announced_time else "",
                     "announced_tz": event.announced_tz, "effective_start": event.effective_start,
                     "effective_end": event.effective_end, "phase_in": event.phase_in,
                     "target_weight_pct": event.target_weight_pct, "bonds": event.bonds, "status": event.status,
                     "source_url": event.source_url, "notes": event.notes, "public_from": public.text,
                     "public_from_basis": public.basis,
                     "public_from_rule": public.rule + (f" ({public.notes})" if public.notes else "")})
    return rows


POLICY_EXPORT_SOURCE = ("BIS WS_CBPOL D.IN (data.bis.org): the repo rate in force from this date; BIS gives no SDF or "
                        "MSF rate: fill them in from RBI's resolution before rbi-store-ingest --policy-rates")


def policy_rates_bis(data: _Data) -> list[dict[str, str]]:
    """India's repo rate changes in BIS's series, from 3 Apr 2001, in ``rbi_store``'s policy-rate columns.

    ``sdf_pct`` and ``msf_pct`` are left blank (BIS has no corridor), so ``rbi_store.read_policy_rates`` refuses the
    file until they are filled in by hand; BIS dates some changes a day after RBI's decision and lags about a month.
    """
    held = _vintages_of(data, "policy_rate_in_bis")
    rows, previous = [], None
    for day in sorted(held):
        if dt.date.fromisoformat(day) < REPO_FROM:
            continue
        v = held[day][-1]
        if previous is None or float(v.facts["value"]) != float(previous):
            rows.append({"effective_date": day, "repo_pct": v.facts["value"], "sdf_pct": "", "msf_pct": "",
                         "source": POLICY_EXPORT_SOURCE, "public_from": _naive_text(v.dating.public_from)})
            previous = v.facts["value"]
    return rows


def derive(data: _Data) -> dict[str, list[dict[str, str]]]:
    """Every derived table, from the store's vintages (deterministic: the same store gives the same bytes)."""
    return {"global_daily": global_daily(data), "india_monthly": india_monthly(data),
            "india_quarterly": india_quarterly(data), "index_events": index_event_rows(data),
            "policy_rates_bis": policy_rates_bis(data)}


# --- Ingest ------------------------------------------------------------------------------------------------

@dataclass
class IngestReport:
    """What one ingest read and changed."""

    inputs: int = 0
    read: int = 0
    duplicates: int = 0
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    out_of_scope: int = 0
    older: int = 0
    received: int = 0  # inputs that are copies of answers the store received (dated by fetches.csv)
    superseded: int = 0  # their values older than what the store holds or read since (not taken)
    refused: list[tuple[str, str]] = field(default_factory=list)
    unreadable: list[tuple[str, str]] = field(default_factory=list)
    other: list[tuple[str, str]] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    raw_saved: list[str] = field(default_factory=list)
    derive_error: str = ""
    written: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{_n(self.inputs, 'input')}: {self.read} read, {len(self.refused)} refused, "
                 f"{len(self.unreadable)} not recognised"]
        if self.received:
            parts.append(f"{self.received} of them {'a copy' if self.received == 1 else 'copies'} of the store's own "
                         "answers (dated by its request log)")
        if self.other:
            parts.append(f"{len(self.other)} not data")
        if self.duplicates:
            parts.append(f"{self.duplicates} given twice")
        parts.append(f"{_n(self.new, 'new value')}, {self.changed:,} changed, {self.unchanged:,} unchanged, "
                     f"{self.out_of_scope:,} not kept (out of scope)")
        if self.older:
            parts.append(f"{self.older:,} equal to an earlier vintage (not taken as a change back)")
        if self.superseded:
            parts.append(f"{self.superseded:,} older than what the store holds or read since (not taken)")
        tail = f"; {_n(len(self.written), 'file')} written" if self.written else "; no file changed"
        return ", ".join(parts) + tail


def _input(source) -> tuple[str, bytes]:
    if isinstance(source, tuple):
        name, data = source
        return str(name), bytes(data)
    if isinstance(source, (bytes, bytearray)):
        return f"<bytes {_sha(bytes(source))[:12]}>", bytes(source)
    path = Path(source)
    return str(path), path.read_bytes()


def ingest(store: str | Path, sources: Iterable, *, now: dt.datetime, settings: Settings | None = None,
           release_dates: str | Path | bytes | None = None,
           index_events: str | Path | bytes | None = None, cpi_series: str | None = None) -> IngestReport:
    """Read saved answers of the macro sources into the store (the same parsers as an update), then rebuild the tables.

    ``sources``: paths, bytes, or ``(name, bytes)`` pairs, recognised by their content
    (``macro_sources.detect_kind``). ``now`` (timezone-aware) is the time of the ingest: what is new is dated by it
    (``first_seen``), and the release rules date it back (``public_from``); a ``now`` before the store's own clock is
    refused (``StoreError``). The inputs are read in the order of their names as given (the full path: name saved
    answers by time, or keep them in dated folders), then their content, so the order given does not matter; the
    same inputs again change nothing. An answer is not dated by when it was saved, which the store cannot know:
    ingest saved answers oldest first, and a value equal to a vintage the store already superseded is not taken as a
    change back. Base-2012 and 2010 CPI rows do not say their series: an answer of them is read as the series in the
    URL of the store's own request (its raw copy), else as ``cpi_series`` (``Current`` or ``Back``), else refused.
    ``release_dates`` and ``index_events`` are tables kept by hand, checked and copied into the store; with no
    inputs the ingest only rebuilds the tables (after a new table or an update of this package).

    A copy of an answer the store received itself (the bytes of a raw copy that ``fetches.csv`` logs as saved by a
    request, its file in ``raw/`` compared byte for byte; a refused answer, say, read again after an update of this
    package) is dated as an update would have dated it: first seen at the ``latest`` of the request that first
    saved it, bounded by the store's reads of the same data logged before that request (``prior_seen``); a value of
    it older than what the store holds (a vintage first seen after it, or a read of the same data since) is not
    taken. These copies are read first, in the order the store received them, then the other inputs.
    """
    now = _check_now(now)
    settings = settings or Settings()
    if cpi_series not in (None, "Current", "Back"):
        raise ValueError(f"cpi_series must be Current or Back, not {cpi_series!r}")
    items = [_input(s) for s in sources]
    release_bytes, events_bytes = _user_tables(release_dates, index_events)
    paths = StorePaths(Path(store))
    with _locked(paths, now, "ingest"):
        data = _load(paths)
        skew = _clock_skew(data, now)
        if skew:
            raise StoreError(skew)
        report = IngestReport(inputs=len(items))
        tx = _transaction(paths)
        _adopt_tables(data, tx, release_bytes, events_bytes)
        _stage_tables(data, tx, release_bytes, events_bytes)
        answers, digests, staged = [], set(), {}
        fetched = _fetched_urls(data)
        received = _received_copies(data)
        logged = _logged_reads(data) if received else {}
        inputs = []
        for name, body in items:
            digest = _sha(body)
            rows = received.get(digest[:12])
            if rows is not None and not _same_bytes(paths.root / data.fetches[rows[0]]["raw"], body):
                rows = None
            inputs.append((rows, name, digest, body))
        # the store's own answers first, in the order it last received them, then the others by name and content
        inputs.sort(key=lambda e: (e[0] is None, e[0][-1] if e[0] is not None else 0, e[1], e[2]))
        for rows, name, digest, body in inputs:
            if digest in digests:
                report.duplicates += 1
                continue
            digests.add(digest)
            try:
                kind = ms.detect_kind(body, name)
            except SourceError as exc:
                report.unreadable.append((name, str(exc)))
                continue
            if kind in (mospi.CALENDAR_POINTER, gs.BIS_REL_CAL):
                report.other.append((name, _not_data(kind, body, name)))
                continue
            query: tuple[tuple[str, str], ...] = ()
            if kind in (mospi.CPI_GROUP, mospi.CPI_ITEM):
                series = _series_param(fetched.get(digest[:12], "")) or cpi_series
                if series is None:
                    report.refused.append((name, "base-2012 and 2010 CPI rows do not say their series (Current or "
                                                 "Back), and this is not a copy of the store's own request: give "
                                                 "--cpi-series Current or Back"))
                    continue
                query = (("series", series),)
            try:
                result = ms.parse_kind(kind, body, name, query=query)
            except SourceError as exc:
                report.refused.append((name, str(exc)))
                continue
            report.read += 1
            if rows is not None and all(data.fetches[r]["source"] == result.source for r in rows):
                answers.append(_received_answer(data, rows, result, name, logged))
                report.received += 1
                continue
            raw = ""
            if settings.keeps_raw(result.source):
                raw = _raw_path_of(data, digest) or staged.get(digest, "")
                if not raw:
                    raw = staged[digest] = _raw_name(result.source, kind, now, digest)
                    tx.put(paths.root / raw, body)
                    data.raw_shas.add(digest[:12])
                    report.raw_saved.append(raw)
            answers.append(_Answer(result.source, raw or name, result, now, label=name))
        absorbed = _absorb(data, answers, settings=settings, live=False)
        report.new, report.changed, report.unchanged = absorbed.new, absorbed.changed, absorbed.unchanged
        report.out_of_scope, report.older, report.conflicts = absorbed.out_of_scope, absorbed.older, absorbed.conflicts
        report.superseded = absorbed.superseded
        derived_before = json.dumps(data.state.get("derived"), sort_keys=True)
        report.derive_error = _stage_all(data, tx, now)
        dated = bool(absorbed.new or absorbed.changed)  # values first seen now: the store's clock is now at least
        if dated:
            _advance_clock(data.state, now)
        if dated or json.dumps(data.state.get("derived"), sort_keys=True) != derived_before \
                or data.state.get("derived_stale"):
            data.state["derived_stale"] = None
            tx.put(paths.state, _state_bytes(data.state))
        report.written = tx.commit()
    return report


def _raw_digest(raw: str) -> str:
    """The 12 hex digits of SHA-256 a raw copy's name carries (``raw/<source>/<UTC time>_<digits>.<ext>``)."""
    return raw.rsplit("_", 1)[-1].split(".")[0]


def _fetched_urls(data: _Data) -> dict[str, str]:
    """The URL each raw copy of the store answered (by the 12 hex digits of its SHA-256), from ``fetches.csv``."""
    out = {}
    for row in data.fetches:
        if row.get("raw"):
            out[_raw_digest(row["raw"])] = row["url"]
    return out


def _received_copies(data: _Data) -> dict[str, list[int]]:
    """Per raw copy of an answer to a request (by the 12 hex digits of its SHA-256), the rows of ``fetches.csv``
    that received those bytes, in the log's order (the same bytes are saved once): each by its ``latest``."""
    out: dict[str, list[int]] = defaultdict(list)
    for i, row in enumerate(data.fetches):
        if row["raw"] and row["outcome"] == "ok" and row["latest"]:
            out[_raw_digest(row["raw"])].append(i)
    return dict(out)


def _same_bytes(path: Path, body: bytes) -> bool:
    try:
        return path.read_bytes() == body
    except OSError:
        return False


def _logged_reads(data: _Data) -> dict[str, list[tuple[int, dt.date | None, dt.date | None, dt.datetime]]]:
    """The store's good reads in ``fetches.csv`` by what they cover (``_scope``): ``(row, start, end, time)``, the
    time the latest its answer can have come (India time, without a zone), in the log's order. A good read is an
    answer kept (``ok`` and not refused) or a ``not_modified`` (the same content as the last one kept)."""
    out: dict[str, list] = defaultdict(list)
    for i, row in enumerate(data.fetches):
        if row["outcome"] == "not_modified" or (row["outcome"] == "ok" and not row["message"].startswith("refused")):
            when = _from_utc(row["latest"] or row["time"], f"{data.paths.fetches}, line {i + 2}: latest")
            if when is not None:
                key, start, end = _scope(row["url"])
                out[key].append((i, start, end, _ist_naive(when)))
    return out


def _received_answer(data: _Data, rows: list[int], result: ParseResult, label: str,
                     logged: Mapping[str, list[tuple[int, dt.date | None, dt.date | None, dt.datetime]]]) -> _Answer:
    """An ingested copy of the answer the store received at ``rows`` of ``fetches.csv``, its values to be dated as
    the update would have dated them (``_Answer.received_at``), with the store's good reads of the same data."""
    fetch = data.fetches[rows[0]]
    times = [(r, _from_utc(data.fetches[r]["latest"], f"{data.paths.fetches}, line {r + 2}: latest")) for r in rows]
    return _Answer(fetch["source"], fetch["raw"], result, times[0][1], fetch["url"], label, received=times,
                   logged=logged.get(_scope(fetch["url"])[0], []))


def _series_param(url: str) -> str | None:
    series = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query)).get("series") if url else None
    return series if series in ("Current", "Back") else None


def _not_data(kind: str, body: bytes, name: str) -> str:
    try:
        if kind == mospi.CALENDAR_POINTER:
            p = mospi.parse_release_calendar_pointer(body, name)
            return (f"MoSPI's release calendar pointer: document {p.document_id} ({p.filename}), created "
                    f"{p.created_at}: compare its dates with the release-date table")
        rows = gs.parse_bis_release_calendar(body, name)
        return f"BIS's release calendar: {len(rows)} release dates (not used by the store)"
    except SourceError as exc:
        return f"not read: {exc}"


# --- Reading the store back --------------------------------------------------------------------------------

_TIME_COLUMNS = frozenset({"first_seen", "prior_seen", "public_from", "first_public_from", "revised",
                           "last_public_from", "last_first_seen", "seen_at", "old_first_seen"})
_DATE_COLUMNS = frozenset({"period_start", "period_end", "date", "effective_date", "last_release"})
_INT_COLUMNS = frozenset({"vintage", "http_status", "bytes", "rows", "kept", "new", "changed", "periods", "vintages",
                          "revised_periods", "old_vintage", "new_vintage"})
_TEXT_COLUMNS = frozenset({"series_id", "period", "unit", "status", "revision", "footnote", "imputation",
                           "public_from_basis", "seen", "print", "stage", "release_id", "public_from_rule",
                           "source_file", "notes", "month", "family", "base", "quarter", "source", "host", "url",
                           "outcome", "raw", "etag", "last_modified", "message", "old_status", "new_status",
                           "old_revision", "new_revision", "event_id", "provider", "index_name", "event_type",
                           "announced_date", "announced_time", "announced_tz", "effective_start", "effective_end",
                           "phase_in", "bonds", "source_url", "publisher", "name", "dataset", "indicator",
                           "series_type", "area", "sector", "level", "code", "label", "frequency", "period_type",
                           "period_format", "first_period", "last_period", "update_info", "citation", "terms",
                           "terms_url", "aliases", "series_notes", "catalogue_notes", "last_value", "in_catalogue"})


_TABLE_TYPES = {("revisions", "seen"): "time", ("fetches", "time"): "utc", ("fetches", "latest"): "utc"}


def store_column_type(name: str, table: str | None = None) -> str:
    """How a column of the store's CSVs is typed by ``read_table``: ``time`` (India time, no zone), ``utc``
    (``fetches.csv``'s ``time`` and ``latest``), ``date``, ``int``, ``float`` (values and rates) or ``text``.
    ``table`` names the table where a column means another thing there (``revisions``' ``seen`` is a time, the
    observations' ``seen`` is ``live`` or ``later``)."""
    if (table, name) in _TABLE_TYPES:
        return _TABLE_TYPES[(table, name)]
    if name in _TEXT_COLUMNS:
        return "text"
    if name in _TIME_COLUMNS or name.endswith("_public_from"):
        return "time"
    if name in _DATE_COLUMNS:
        return "date"
    if name in _INT_COLUMNS:
        return "int"
    return "float"


TABLES = ("observations", *SOURCE_KEYS, "series", "fetches", "revisions", *DERIVED_COLUMNS)


def read_table(store: str | Path, name: str) -> pd.DataFrame:
    """A store CSV, typed: ``observations`` (every source), one source's observations (its key), ``series``,
    ``fetches``, ``revisions``, or a derived table. Times become Timestamps (India time without a zone; UTC in
    ``fetches.csv``), counts ``Int64``, values and rates floats; blank cells are missing values in every column."""
    paths = StorePaths(Path(store))
    if name == "observations":
        files = [paths.observations(s) for s in SOURCE_KEYS if paths.observations(s).exists()]
    elif name in SOURCE_KEYS:
        files = [paths.observations(name)]
    elif name in ("series", "fetches", "revisions"):
        files = [paths.root / f"{name}.csv"]
    elif name in DERIVED_COLUMNS:
        files = [paths.derived / f"{name}.csv"]
    else:
        raise ValueError(f"no store table {name!r}: one of {', '.join(TABLES)}")
    missing = [f for f in files if not f.exists()]
    if missing or not files:
        raise FileNotFoundError(f"{missing[0] if missing else paths.observation_dir}: not written yet")
    frame = pd.concat([pd.read_csv(f, dtype=str, keep_default_na=False) for f in files], ignore_index=True)
    for column in frame.columns:
        values = frame[column].replace("", None)
        how = store_column_type(column, name)
        if how in ("time", "date"):
            frame[column] = pd.to_datetime(values)
        elif how == "utc":
            frame[column] = pd.to_datetime(values, utc=True)
        elif how == "int":
            frame[column] = pd.to_numeric(values).astype("Int64")
        elif how == "float":
            frame[column] = pd.to_numeric(values).astype(float)
        else:
            frame[column] = values.astype(object)
    return frame


AS_OF_COLUMNS = ("period", "value", "value_text", "vintage", "first_seen", "public_from", "public_from_basis", "seen",
                 "not_seen_at_publication", "print", "stage", "release_id", "status", "revision")


def as_of(store: str | Path, series_id: str, t: dt.datetime | pd.Timestamp, *, seen_only: bool = False
          ) -> pd.DataFrame:
    """A series as known at ``t``: per period, the latest vintage public by then (``public_from <= t``).

    ``t`` is a timezone-aware time, or a naive one in India time (as the CSVs write times). A vintage the store saw
    within six hours of its publication (``seen`` ``live``) is the value then published; one read later
    (``later``: a history read, or a value seen late) is dated by the release rule or calendar of its stage and
    flagged ``not_seen_at_publication``: it may include revisions made after that time (the APIs serve the latest
    value only). Nothing is visible before its ``public_from``, which is never earlier than the value could have
    been public as far as the rules, the release table and the store's own reads know (the module docstring lists
    where it can still be early). ``seen_only=True`` keeps only what the store itself had read by ``t``
    (``first_seen <= t``).
    """
    if isinstance(t, pd.Timestamp):
        t = t.to_pydatetime()
    if not isinstance(t, dt.datetime):
        raise ValueError(f"t must be a datetime, not {t!r}")
    when = t.astimezone(IST).replace(tzinfo=None) if t.tzinfo is not None else t
    paths = StorePaths(Path(store))
    series = {r["series_id"]: r for r in _read_rows(paths.series, _required(SERIES_FACTS), "series table")}
    info = series.get(series_id)
    if info is None:
        raise ValueError(f"{paths.series}: no series {series_id!r} in the store")
    rows: dict[str, dict[str, str]] = {}
    for row in _read_rows(paths.observations(info["source"]), VINTAGE_COLUMNS, "observation table"):
        if row["series_id"] != series_id:
            continue
        stamp = row["first_seen"] if seen_only else row["public_from"]
        if _parse_ist(stamp, "observation table") <= when:
            held = rows.get(row["period"])
            if held is None or int(row["vintage"]) > int(held["vintage"]):
                rows[row["period"]] = row
    ordered = sorted(rows.values(), key=lambda r: period_bounds(r["period"], info["period_type"])[1])
    frame = pd.DataFrame([{"period": r["period"], "value": float(r["value"]), "value_text": r["value"],
                           "vintage": int(r["vintage"]), "first_seen": pd.Timestamp(r["first_seen"]),
                           "public_from": pd.Timestamp(r["public_from"]), "public_from_basis": r["public_from_basis"],
                           "seen": r["seen"], "not_seen_at_publication": r["seen"] == "later", "print": r["print"],
                           "stage": r["stage"], "release_id": r["release_id"], "status": r["status"],
                           "revision": r["revision"]} for r in ordered], columns=list(AS_OF_COLUMNS))
    return frame.astype({"vintage": "Int64"}) if len(frame) else frame


# --- Status ------------------------------------------------------------------------------------------------

def _derived_series() -> list[str]:
    out = [sid for _, columns in GLOBAL_GROUPS for _, sid in columns]
    out += [sid for measures in _MONTHLY_MEASURES.values() for pair in measures.values() for sid in pair if sid]
    out += [sid for measures in _QUARTERLY_MEASURES.values() for pair in measures.values() for sid in pair]
    return list(dict.fromkeys(out))


@dataclass
class StoreStatus:
    """What a store holds and how its sources fare (``lines()`` for the report)."""

    root: Path
    lock: str | None
    interrupted: bool
    state: dict
    sources: list[dict]
    series: list[dict]
    revisions: int
    index_events: tuple[int, int] | None
    policy: list[dict]

    def lines(self, pattern: str | None = None) -> list[str]:
        out = [f"store {self.root}"]
        if self.lock:
            out.append(f"locked: {self.lock}")
        if self.interrupted:
            out.append("an interrupted commit waits in journal.json: the next macro-store command completes it (for "
                       "example macro-store-ingest with no files); until then the figures below may be incomplete")
        values = sum(s["values"] for s in self.sources)
        vintages = sum(s["vintages"] for s in self.sources)
        out.append(f"values: {values:,} periods in {len(self.series):,} series ({vintages:,} vintages); revisions "
                   f"seen: {self.revisions:,}")
        derived, stale = self.state.get("derived"), self.state.get("derived_stale")
        if derived:
            out.append(f"derived tables: not rebuilt since {derived.get('since')}: {derived.get('error')} (the "
                       "previous ones are kept; every command tries again)")
        elif stale:
            out.append(f"derived tables: out of date since {stale} (a run ended before rebuilding them; the next "
                       "command does)")
        for s in self.sources:
            out += _source_lines(s)
        if self.index_events is not None:
            out.append(f"index events: {self.index_events[0]} in the table given ({self.index_events[1]} verified on "
                       "a provider's page)")
        else:
            out.append("index events: none given (pass a table with --index-events; an example with its sources: "
                       "data/index_events_example.csv)")
        if self.policy:
            last = self.policy[-1]
            out.append(f"policy rates (BIS, India): {len(self.policy)} repo rate changes from 2001-04-03, the last "
                       f"{last['effective_date']} to {last['repo_pct']} (derived/policy_rates_bis.csv: SDF and MSF "
                       "to be filled in by hand)")
        chosen = [s for s in self.series if re.search(pattern, s["series_id"])] if pattern else \
            [s for s in self.series if s["series_id"] in set(_derived_series())]
        if chosen:
            out.append("series" + (f" matching {pattern!r}:" if pattern else " the derived tables use:"))
            for s in chosen:
                out.append(f"  {s['series_id']}: {_n(int(s['periods']), 'period')} {s['first_period']} to "
                           f"{s['last_period']} ({_n(int(s['vintages']), 'vintage')}); last {s['last_period']} = "
                           f"{s['last_value']}, public from {s['last_public_from']}")
        elif pattern:
            out.append(f"series matching {pattern!r}: none")
        return out


def _source_lines(s: dict) -> list[str]:
    head = f"{s['source']} ({s['mode']}): "
    if not s["last_update"]:
        head += "never updated"
    else:
        head += f"last update {s['last_update']}: {s['last_outcome']}: {s['last_message']}"
    out = [head]
    if s["series"]:
        out.append(f"  {_n(s['series'], 'series', 'series')}, {s['values']:,} periods ({s['vintages']:,} vintages, "
                   f"{s['revised']:,} periods revised), {s['first']} to {s['last']}")
    history = s["history"]
    if history is not None:
        done, queue = len(history["done"]), len(history["queue"])
        out.append(f"  history: {_n(done, 'request')} done, {queue:,} to go"
                   + (f", finished {history['finished']}" if history.get("finished") and not queue else "")
                   + f" (plan {history['plan']}, seeded {history['seeded']})")
    if s["pending"]:
        out.append(f"  pending: {_n(s['pending'], 'request')} left by an earlier run (caps or errors)")
    host = s["host"]
    if host:
        line = (f"  host {host['host']}: last request {host.get('last_request') or 'never'}: "
                f"{host.get('last_outcome') or '-'}"
                + (f" (HTTP {host['last_status']})" if host.get("last_status") else "")
                + f"; {host.get('day_requests', 0)} requests on {host.get('day') or '-'} (India day)")
        out.append(line)
        if host.get("stopped"):
            out.append(f"  host {host['host']}: stopped since {host['stopped']} after {host.get('blocks')} blocks in a "
                       f"row ({host.get('last_block_reason')}): no request until resumed; check the site in a browser, "
                       "then run macro-store-update --sources <source> --resume")
        elif host.get("cooldown_until"):
            out.append(f"  host {host['host']}: cool-down until {host['cooldown_until']} after "
                       f"{host.get('last_block_reason')}")
        if host.get("errors"):
            out.append(f"  host {host['host']}: {_n(int(host['errors']), 'request')} in a row without a usable answer")
        if host.get("contact_demand"):
            out.append(f"  host {host['host']}: asked automated clients for contact details ({host['contact_demand']});"
                       " none was given")
    flights = _flights(s["in_flight"])
    if flights:
        out.append(f"  a run ended before keeping the answers of its {_n(len(flights), 'request')} (the last to "
                   f"{flights[-1]['url']} at {flights[-1].get('time')}): the next update logs them and asks again")
    out += [f"  {line}" for line in s.get("lags", [])]
    for f in s["failed"][-5:]:
        out.append(f"  failed {f['time']}: {f['request']['url']}: {f['reason']}"
                   + (f" (raw copy {f['raw']})" if f.get("raw") else ""))
    return out


def policy_rate_check(store: str | Path, policy_rates: str | Path | bytes, *, window_days: int = 7) -> list[str]:
    """BIS's repo rate changes for India against a policy-rate table kept by hand (``rbi_store``'s columns, as
    ``rbi-store-ingest --policy-rates`` reads it): a cross-check, never a fill. Each line says what agrees, what BIS
    dates differently (within ``window_days``), and the changes one has that the other lacks, over the span both
    cover. BIS lags about a month and has dated some changes a day after RBI's decision; the table is the source."""
    from cr_strat import rbi_store  # a table in its format, read by its own checks
    kept = rbi_store.read_policy_rates(policy_rates)
    data = _load(StorePaths(Path(store)))
    bis = policy_rates_bis(data)
    held = data.periods_of("policy_rate_in_bis")
    if not bis or not kept:
        return ["policy rates: nothing to compare (" + ("no BIS repo rate in the store" if not bis else
                                                       "the table is empty") + ")"]
    first, last = max(bis[0]["effective_date"], kept[0]["effective_date"]), max(held)
    span = [r for r in bis if first < r["effective_date"] <= last]  # changes after the start both cover
    table = [r for r in kept if first < r["effective_date"] <= last]
    lines, matched, agree = [], set(), 0
    for row in span:
        day = dt.date.fromisoformat(row["effective_date"])
        near = [r for r in table if abs((dt.date.fromisoformat(r["effective_date"]) - day).days) <= window_days
                and float(r["repo_pct"]) == float(row["repo_pct"]) and r["effective_date"] not in matched]
        if not near:
            lines.append(f"policy rates: BIS shows the repo rate at {row['repo_pct']}% from {day}, a change the table "
                         "lacks")
            continue
        match = min(near, key=lambda r: abs((dt.date.fromisoformat(r["effective_date"]) - day).days))
        matched.add(match["effective_date"])
        if match["effective_date"] == row["effective_date"]:
            agree += 1
        else:
            lines.append(f"policy rates: BIS dates the change to {row['repo_pct']}% {day}, the table "
                         f"{match['effective_date']}")
    for row in table:
        if row["effective_date"] not in matched:
            lines.append(f"policy rates: the table changes the repo rate to {row['repo_pct']}% on "
                         f"{row['effective_date']}, a change BIS lacks")
    return [f"policy rates: BIS and the table agree on {_n(agree, 'change')} from {first} to {last} (BIS's latest "
            "date)"] + lines


def status(store: str | Path, *, now: dt.datetime | None = None) -> StoreStatus:
    """What the store holds, read without the lock (``now`` only dates a lock's age in the report)."""
    paths = StorePaths(Path(store))
    interrupted = paths.journal.exists()
    if not interrupted and not paths.state.exists() and not paths.series.exists():
        raise StoreError(f"{paths.root}: no macro store here (neither series.csv nor state.json)")
    try:
        data = _load(paths)
    except StoreError:
        if not interrupted:
            raise
        data = _Data(paths, {}, {}, [], [], _check_state(paths) if paths.state.exists() else _fresh_state())
    series_rows = _read_rows(paths.series, _required(SERIES_COLUMNS), "series table") if paths.series.exists() else []
    sources = []
    for source in SOURCE_KEYS:
        src = data.state["sources"].get(source) or {}
        rows = [r for r in series_rows if r["source"] == source]
        keys = [k for k in data.vintages if data.series[k[0]]["source"] == source]
        firsts = [r["first_period"] for r in rows if r["first_period"]]
        lasts = [r["last_period"] for r in rows if r["last_period"]]
        host = next(iter(ms.SOURCES[source].hosts))
        mode = ("named only: licence not settled" if source in NAMED_ONLY else
                "off by default" if not ms.SOURCES[source].enabled_by_default else "on by default")
        lags = []
        if source == mospi.SOURCE:
            for role, detail in DETAIL_SERIES.items():
                headline = {"probe_cpi": "cpi_2024_all_india_combined_general",
                            "probe_iip": "iip_2022_23_general"}[role]
                top, low = max(data.periods_of(headline), default=""), max(data.periods_of(detail), default="")
                if top and low < top:
                    lags.append(f"tables behind the headline: {detail} to {low or 'none'}, {headline} to {top} (the "
                                "next release's follow-ups ask every month after the tables' latest)")
        sources.append({"source": source, "mode": mode, "last_update": src.get("last_update"), "lags": lags,
                        "last_outcome": src.get("last_outcome", ""), "last_message": src.get("last_message", ""),
                        "series": len(rows), "values": len(keys),
                        "vintages": sum(len(data.vintages[k]) for k in keys),
                        "revised": sum(len(data.vintages[k]) > 1 for k in keys),
                        "first": min(firsts, default=""), "last": max(lasts, default=""),
                        "history": src.get("backfill"), "pending": len(src.get("pending", [])),
                        "failed": src.get("failed", []), "in_flight": src.get("in_flight"),
                        "host": data.state["hosts"].get(host)})
    events = None
    if data.events_bytes is not None:
        parsed = gs.read_index_events(data.events_bytes, name=str(paths.index_events))
        events = (len(parsed), sum(e.status == "verified" for e in parsed))
    policy = _read_rows(paths.derived / "policy_rates_bis.csv", POLICY_EXPORT_COLUMNS, "policy rates export")
    return StoreStatus(paths.root, lock_message(paths.root, paths.lock, now, "macro-store", LOCK_STALE_AFTER)
                       if paths.lock.exists() else None, interrupted, data.state, sources, series_rows,
                       len(data.revisions), events, policy)
