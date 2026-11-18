"""A daily store of RBI operations and money-market rates, fed from the press-release RSS feed.

Build-order item 4 of ``docs/rates_data_sources.md``. RBI publishes every
auction, liquidity operation and the daily Money Market Operations as a press
release, and its feed (``https://www.rbi.org.in/pressreleases_rss.xml``)
carries the 10 latest releases with their full tables. The feed holds no
history, so a store must poll it often enough that no release scrolls out
between two polls (about 9 releases a working day in Nov 2026, 13 at most;
the 10 items spanned one to three days), and must check for what it missed
instead of assuming it saw everything. Parsing is ``rbi_releases`` and
``rbi_operations``; this module keeps what they read, day after day.

RBI grants no reuse licence, and its disclaimer prohibits caching without
permission: a store, and above all its raw copies, may well be such a cache.
Ask RBI in writing before keeping one (``docs/rates_data_sources.md``; a
draft letter is in ``docs/data_enquiries.md``).

**Layout.** A store is a directory:

=============================  ===============================================================================
``tables/<kind>.csv``          parsed rows, one file per kind (``rbi_releases.COLUMNS[kind]``), keyed
                               ``(release_id, line)``; the format ``cr-strat rbi-releases`` writes, so
                               ``auctions.read_release_tables`` (``cr-strat auctions --releases``) reads them
``releases.csv``               one row per release id ever read or listed (``RELEASE_INDEX_COLUMNS``)
``revisions.csv``              every change to a stored release (``REVISION_COLUMNS``)
``gaps.csv``                   possible gaps: ids never seen, listed releases never read, days without Money
                               Market Operations
``derived/*.csv``              five tables rebuilt from ``tables/`` on every ingest (``DERIVED_COLUMNS``)
``raw/``                       every feed and saved page read, byte for byte: ``pr_feed_<UTC>_<h>.xml`` and
                               ``page_<UTC>_<h>.html``, named by the time of the ingest and the first 12 hex
                               digits of the SHA-256; bytes already kept are not saved again
``polls.csv``                  one row per request the poller sent (``POLL_COLUMNS``, times in UTC)
``state.json``                 the poller's state (last request and its outcome, validators, a cool-down or a
                               stop, the ids polls received) and a failure to rebuild the derived tables
``sovereign_master.csv``       a copy of the last security master given, for the OMO and switch ISINs
``policy_rates.csv``           a copy of the last table of policy rates given, entered by hand
                               (``POLICY_COLUMNS``)
``store.lock``                 present while a command runs (``StoreLockedError`` for anyone else)
=============================  ===============================================================================

Every write goes to a temporary file first. A command stages all its files,
records them in ``journal.json``, then renames them into place and deletes
the journal; the next command finishes the renames of an interrupted commit
(or, if the journal was never written, drops the temporary files), so the
store holds either all of a command's changes or none of them. Files whose
bytes would not change are not rewritten.

**Ingest.** ``ingest(store, sources, now=...)`` reads saved release pages,
feed copies and saved press-release listings (paths, bytes, or ``(name,
bytes)`` pairs; bare bytes are named by their content, ``<bytes
1a2b3c4d5e6f>``). Each copy of a release is parsed on its own with
``rbi_releases.parse_copy``, announcements first so that GoI results see the
greenshoe of their announcement (stored or in the batch). Then, per release
id:

* A release never seen is added, with ``first_seen`` the time of the ingest.
* Every copy read is remembered by a fingerprint (``copies`` in the index). A
  copy read before changes nothing unless it now parses into rows the store
  never held (its parser was fixed since): the same copies read again, under
  any name and in any order, leave every file byte-identical. The exception
  is an ingest that reads again every copy the store has read of a release
  (rereading ``raw/`` does): the release's entry then becomes what a store
  built from those copies alone would hold, so that a package update reaches
  the releases already kept (``updated`` in the report). Without rows, the
  highest-ranked copy says why (a title now recognised, a refusal reworded);
  with rows, the stored rows stay and only their notes and how the release
  was classified are taken again from their copies. Entries that would not
  change, the name of their copy apart, are left as they are.
* Different rows (RBI corrects releases; a saved page may carry more than its
  feed copy) replace the stored ones, and ``revisions.csv`` records when the
  store saw it, the old and new fingerprints (``rows_hash``) and the first
  cells that changed. A version the store has held before never comes back.
  Several new versions in one call are applied in a fixed order, feed copies
  by publication time and name, then saved pages by name, and the last one
  stays; so the order of ``sources`` does not matter.
* A release that failed to parse stays in the index as ``failed`` with the
  reason, and is parsed when a copy that parses arrives, or when its raw copy
  is read again after a parser fix: ``repaired``. A new copy that fails while
  the stored rows parsed is logged once as ``refused`` and the rows are kept.
* ``published`` is the earliest feed time seen for the release; it is filled
  in (``published`` in the log) when a feed copy follows a saved page.
* A saved press-release listing adds the ids, titles and dates it shows as
  ``listed`` (no copy was read); a copy read later takes the entry's place.

Comparisons ignore ``published`` and ``source_file``. A parser that raises
something other than its two refusals (a bug) fails that release, not the
ingest. An error while the derived tables are rebuilt keeps the previous
ones and is recorded in ``state.json``; the parsed rows and the raw copies
are committed all the same. An unattended poll must not stop on one release.

**Possible gaps** (``gaps.csv``, rebuilt on every ingest). Release ids were
consecutive in Nov 2026 (63920 to 63991, none missing), but whether they
always are is not known; RBI also publishes a few ids out of time order. So
the store lists, as possible gaps, the id ranges between the lowest and the
highest id it holds that it has never seen (``release_id``), the ids known
only from a listing whose kind has a parser (``listed``: the data are
missing), and the calendar days between the first and the last Money Market
Operations day it holds that have no such release (``mmo_day``; there is one
for every calendar day). ``in_feed_span`` marks the ranges inside the span of
ids the poller has received (``state.json``: from ``first_id``, where the
unbroken run of ids at the top of its first feed begins, to the highest id
any poll received): those are what polling missed. The first feed holds the
latest releases, so an id it lacks below its highest one was published before
polling began (or never), as was anything below such a hole, even an id that
feed holds (published out of order): a range below the span is history from
pages and feeds ingested by hand, and is expected. A possible gap
is filled by hand: find the ids on RBI's press-release listing in a browser,
save the listing and the pages that matter, a few a day, and ingest them; the
listing closes the gaps of releases that do not matter.

**Derived tables** (``derived/``), deterministic. Every row has
``public_from``: the time from which every value in the row was public, the
latest time of the releases it was built from. A release's time is its feed
time (India time) if the store ever saw it in a feed, else 23:59:59 on its
release date (``public_from_source``, of that latest part: ``feed`` or
``release_date``); blank if the time of one part is not known. ``revised`` is
when the store last saw the rows of one of those releases change (blank if
never). A back-test that asks what was known at time ``t`` keeps the rows
with ``public_from <= t``. A row joined from several releases (liquidity and
open market operations) also has ``first_public_from``, its earliest part,
and each part's own time (``announcement_public_from``,
``result_public_from``, ``mmo_public_from``): from ``first_public_from`` to
``public_from`` the row was known in part, and only the fields of the parts
public by then may be used. A ``revised`` row holds the latest version only
(old values are in ``revisions.csv`` and the raw copies). ``release_notes``
holds the notes ``releases.csv`` has for the releases a row was built from
(``<release id>: <notes>``, several separated by ``|``); ``notes`` holds the
derivation's own.

* ``money_market_daily``: one row per as-on date: volume, weighted average
  rate and range of the overnight total, call money, triparty repo, market
  repo and repo in corporate bonds, notice and term money and the three term
  repo segments; today's SDF, MSF, variable rate repo (``vrr``) and reverse
  repo (``vrrr``) summed over their rows, with their rate (one rate if the
  rows agree, else the amount-weighted mean and a note); net liquidity of the
  day, SLF, net outstanding, net total; cash balances, the cash reserve
  requirement, government surplus cash and net durable liquidity, each with
  its own reference date. ``business_day``, ``quarter_end_day`` and
  ``fy_end_day`` (the last business day of a quarter, of March) come from the
  Mumbai settlement calendar, blank in a year its holiday list does not
  cover. ``marks_changed`` names the lines whose footnote marks differ from
  those printed in Nov 2026 (RBI's sign of a changed definition: do not
  splice a series across it). ``outstanding_check`` compares the outstanding
  operations with the operations of the earlier days still running
  (``agrees``, ``differs`` with a note, blank when the earlier releases needed
  are not all stored). ``repo_rate_pct`` and ``policy_check`` come from the
  hand-entered policy rates: the repo rate in force, and whether the day's SDF
  and MSF rates are the table's. Weekend and holiday rows are kept: their
  money-market lines print ``0.00`` and no rate; never fill rates forward
  from them.
* ``liquidity_operations``: one row per variable rate repo or reverse repo
  auction, the announcement and the result joined on direction, auction date
  and tenor, with the auction's line in that day's Money Market Operations
  (``mmo_check``: ``agrees``, ``differs``, ``missing`` from a stored release,
  ``ambiguous``, ``no_result``). An announcement without a result, a result
  without an announcement, and an operation known only from Money Market
  Operations are rows too.
* ``omo_operations``: one row per security per open market operation, the
  announcement (ISIN, maturity, method, programme) joined to the result on
  the security's name key, the aggregate figures repeated; with a stored
  security master the ISIN is checked (``sovereign.match_isin``) or, for a
  result without its announcement, found there.
* ``supply_calendar``: what was announced to be auctioned and when it became
  public: the GoI calendar's allocation lines by week and tenor, the
  quarter's total of the state calendar, and every GoI, T-bill and SDL
  auction announcement parsed. ``tenor_bucket`` is one of ``issuance``'s
  buckets, by one rule for a line and the auctions held under it. A calendar
  line names a nominal tenor of N years and goes in the bucket that holds N
  with its lower bound left out (``lo < N <= hi``: 3 years in 1-3y, 5 in
  3-5y, 10 in 5-10y, 15 in 10-15y, 30 in 15-30y). So does an announced new
  issue, by its tenor: an SDL's as printed, a GoI security's (``New GS 2036``)
  the years from settlement to repayment, rounded (``tenor_years``). A
  reissue has less than N years to run and is bucketed by its residual
  maturity on the settlement date, as ``supply_monthly.csv`` buckets it (an
  SDL matures on the anniversary of its first issue). ``supply_monthly.csv``
  measures a new issue by residual maturity too, on 365.25-day years: a new
  N-year issue whose life spans more than N/4 leap days then has a little
  more than N years and lands one bucket up (3 or 15 years: 3 settlement
  years in 4; 10 or 30 years: 2 in 4; 5 years: 1 in 4); the row's note gives
  that bucket where it differs.
* ``switch_operations``: one row per pair of securities switched, from the
  switch results, with ISINs and maturity dates from the stored security
  master.

**Poller.** ``poll(store, now=..., transport=...)`` makes at most one GET of
the feed per call, never follows a redirect, and refuses RBI's attachment
hosts (``REFUSED_HOSTS``: bot challenges). Its arguments are checked first
(``check_url``, ``check_min_interval``, ``check_user_agent``,
``check_timeout``), including what the standard library would refuse only
once the request is under way (a space in the URL, a port out of range, a
client name outside visible ASCII, a timeout the socket layer overflows on):
a bad argument records no attempt. The store (index, tables, security
master, policy rates) and every value of ``state.json`` are read and checked
before anything is sent: a store that cannot take the answer costs no
request. No request if the last one, answered or not, was less than
``min_interval`` ago, during a cool-down, or once the poller has stopped. The
attempt is recorded in ``state.json`` before the request is sent, and the
answer in ``polls.csv``. HTTP 418, 403 or 429, a firewall page or a bot
challenge in place of the feed, or a second web page in a row where the feed
should be, is a block (spec: make no further request that day; resuming is
the operator's decision): no request before 08:00 India time the next day,
and a second block in a row stops the poller until it is resumed
(``resume=True``, ``cr-strat rbi-store-poll --resume``) after the site was
checked in a browser. A good answer resets the count. A 5xx or network error
is recorded; the next poll after ``min_interval`` tries again. A last request
recorded after ``now``, or a cool-down longer than its maximum, means the
clock moved back: no request, and an error says so instead of waiting
quietly. The request is conditional (``If-None-Match``,
``If-Modified-Since``: RBI's feed sends an ETag and a Last-Modified), but
"new" is decided from the ordered list of the items' ids and hashes, since
the server rewrites the file without new items and a 304 was never observed:
a feed whose items equal the last poll's is not ingested again. A poll that
worked but met something a person should look at (a release that failed to
parse, ids possibly missed since the previous poll, feed items without a
release id or with a ``pubDate`` not read, derived tables not rebuilt) says
so in ``PollResult.attention`` and exits ``EXIT_ATTENTION``; a feed none of
whose items has a release id is an error (``bad_feed``). A poll that got no
usable feed exits ``EXIT_ERROR`` (the command line's line then says
``error:``) or, blocked, ``EXIT_BLOCKED``, so that the daily check of the
poll log in ``data/README.md`` finds each one. The ``User-Agent``
is a generic client name (configurable); nothing in a request names the user
or the firm. ``urllib_transport`` (standard library, the environment's proxy
and certificates) is the default; tests pass a fake one. Nothing here reads
the clock: ``now`` (timezone-aware) is an argument, and the command line
reads the clock once per command.

**Times.** CSV times are India time without a zone (``2026-11-16T09:00:00``),
as RBI's feed gives them; ``state.json``, ``polls.csv`` and the raw file
names use UTC with ``Z``.
"""

from __future__ import annotations

import calendar as _calendar
import datetime as dt
import gzip
import hashlib
import http.client
import io
import json
import math
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from cr_strat.calendars import BusinessCalendar, CalendarCoverageError, mumbai_settlement_calendar
from cr_strat.htmlparse import BlockedPageError, check_not_blocked, parse_html
from cr_strat.issuance import TENOR_BUCKETS, tenor_bucket
from cr_strat.rbi_operations import (
    GSEC_CALENDAR,
    LAF_ANNOUNCEMENT,
    LAF_RESULT,
    MMO,
    MMO_ITEMS,
    OMO_ANNOUNCEMENT,
    OMO_RESULT,
    SDL_CALENDAR,
)
from cr_strat.rbi_releases import (
    COLUMNS,
    DEFAULT_LIMITS,
    IST,
    RELEASE_TYPES,
    CopyResult,
    ListedRelease,
    ParseContext,
    Release,
    ReleaseParseError,
    SanityLimits,
    classify,
    classify_title,
    column_type,
    feed_releases,
    greenshoe_lookup,
    is_feed,
    parse_copy,
    read_release_listing,
    read_release_page,
    read_rss_feed,
)
from cr_strat.sovereign import GREEN, GSEC, SDL, TBILL, match_isin, normalise_name, parse_security_name, read_master

FEED_URL = "https://www.rbi.org.in/pressreleases_rss.xml"
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; feed-reader/1.0)"
DEFAULT_MIN_INTERVAL = dt.timedelta(minutes=25)
MAX_MIN_INTERVAL = dt.timedelta(days=1)  # the feed's 10 items can span a single day
MAX_TIMEOUT = 600.0  # seconds: a poll holds the store's lock while it waits for the answer
MAX_USER_AGENT = 200  # characters of the client name
RESUME_HOUR = 8  # India time: after a block, no request before this hour of the next India day
MAX_COOLDOWN = dt.timedelta(hours=24 + RESUME_HOUR)  # a block at 00:00 India time waits until 08:00 the next day
STOP_AFTER_BLOCKS = 2  # blocks in a row after which the poller stops until the operator resumes it
BLOCK_STATUSES = frozenset({403, 418, 429})
REFUSED_HOSTS = frozenset({"rbidocs.rbi.org.in", "website.rbi.org.in"})
MAX_FEED_BYTES = 5_000_000
LOCK_STALE_AFTER = dt.timedelta(hours=1)
CLOCK_SLACK = dt.timedelta(minutes=5)  # a last request recorded this far after "now" means the clock moved back
STORE_FORMAT = 1

EXIT_OK, EXIT_ERROR, EXIT_BLOCKED, EXIT_ATTENTION = 0, 1, 3, 4

RELEASE_INDEX_COLUMNS = ("release_id", "kind", "status", "title", "release_date", "published", "rows", "reason",
                         "notes", "press_release_no", "source_file", "classified_by", "rows_hash", "first_seen",
                         "last_changed", "copies")
_INDEX_REQUIRED = RELEASE_INDEX_COLUMNS[:-1]  # ``copies`` was added later: an index without it still reads
REVISION_COLUMNS = ("seen", "release_id", "kind", "change", "detail", "old_hash", "new_hash", "source_file")
GAP_COLUMNS = ("gap", "first", "last", "count", "in_feed_span")
POLL_COLUMNS = ("time", "url", "http_status", "outcome", "bytes", "items", "ids", "new", "missed", "failed",
                "unreadable", "cooldown_until", "message")
POLICY_COLUMNS = ("effective_date", "repo_pct", "sdf_pct", "msf_pct", "source")
STATUSES = ("parsed", "failed", "no_parser", "not_recognised", "listed")
CHANGES = ("rows", "repaired", "published", "refused")

_SEGMENTS = (  # money_market_daily prefix, MMO item
    ("overnight", "overnight.total"), ("call", "overnight.call_money"), ("triparty", "overnight.triparty_repo"),
    ("market_repo", "overnight.market_repo"), ("corp_bond_repo", "overnight.corporate_bond_repo"),
    ("notice", "term.notice_money"), ("term_money", "term.term_money"), ("term_triparty", "term.triparty_repo"),
    ("term_market_repo", "term.market_repo"), ("term_corp_bond_repo", "term.corporate_bond_repo"))
_FACILITIES = (("sdf", "laf.today.sdf"), ("msf", "laf.today.msf"), ("vrr", "laf.today.repo"),
               ("vrrr", "laf.today.reverse_repo"))
_NET_LINES = (("net_today_cr", "net.today"), ("slf_cr", "slf.availed"), ("net_outstanding_cr", "net.outstanding"),
              ("net_total_cr", "net.total"))
_RESERVES = (("cash_balances", "reserves.cash_balances"), ("crr_requirement", "reserves.crr_requirement"),
             ("gov_surplus_cash", "government.surplus_cash_balance"),
             ("net_durable_liquidity", "liquidity.net_durable"))
_PUBLIC = ("public_from", "public_from_source", "revised")
DERIVED_COLUMNS: dict[str, tuple[str, ...]] = {
    "money_market_daily": (
        "as_on_date", "business_day", "quarter_end_day", "fy_end_day",
        *(f"{p}_{f}" for p, _ in _SEGMENTS for f in ("volume_cr", "rate_pct", "low_pct", "high_pct")),
        *(f"{p}_{f}" for p, _ in _FACILITIES for f in ("cr", "rate_pct")),
        *(c for c, _ in _NET_LINES),
        *(f"{p}_{f}" for p, _ in _RESERVES for f in ("cr", "date")),
        "marks_changed", "outstanding_check", "repo_rate_pct", "policy_check",
        "release_id", "release_date", *_PUBLIC, "release_notes", "notes"),
    "liquidity_operations": (
        "operation", "direction", "auction_date", "tenor_days", "reversal_date",
        "announcement_id", "announcement_public_from", "notified_cr", "window_start", "window_end",
        "result_id", "result_public_from", "received_cr", "accepted_cr", "cutoff_rate_pct", "wa_rate_pct",
        "partial_pct", "liquidity_effect_cr",
        "mmo_release_id", "mmo_public_from", "mmo_amount_cr", "mmo_rate_pct", "mmo_check", "first_public_from",
        *_PUBLIC, "release_notes", "notes"),
    "omo_operations": (
        "operation", "auction_date", "settlement_date", "line", "security", "name_key", "isin", "isin_source",
        "isin_match", "maturity_date", "aggregate_notified_cr", "total_offered_cr", "total_accepted_cr",
        "auction_method", "programme_pr_no", "programme_date", "offers_received_count", "offered_cr",
        "offers_accepted_count", "accepted_cr", "cutoff_yield", "cutoff_price", "way", "wap",
        "partial_allotment_pct", "liquidity_effect_cr", "announcement_id", "announcement_public_from",
        "result_id", "result_public_from", "first_public_from", *_PUBLIC, "release_notes", "notes"),
    "supply_calendar": (
        "source", "kind", "period_start", "period_end", "auction_date", "settlement_date", "security", "name_key",
        "state_code", "tenor_years", "tenor_days", "tenor_bucket", "maturity_date", "amount_cr", "greenshoe_cr",
        "week_no", "allocation_no", "release_id", "line", *_PUBLIC, "release_notes", "notes"),
    "switch_operations": (
        "auction_date", "line", "source_security", "source_key", "source_isin", "source_maturity_date",
        "destination_security", "destination_key", "destination_isin", "destination_maturity_date",
        "notified_cr", "offers_received_count", "source_offered_cr", "offers_accepted_count", "source_accepted_cr",
        "destination_issued_cr", "dest_cutoff_price", "dest_cutoff_yield", "release_id", "release_date",
        *_PUBLIC, "release_notes", "notes"),
}
_TMP = ".tmp-rbistore"
_MMO_TITLE = re.compile(r"^money market operations as on ([a-z]+ \d{1,2}, \d{4})$", re.I)
_UNCOMPARED = ("published", "source_file")  # bookkeeping: a copy's own, not the release's content
_MAX_DIFF = 8  # changed cells listed in a revision's detail
_FINGERPRINT = 16  # hex digits of a copy's fingerprint in ``copies``


class StoreError(RuntimeError):
    """The store cannot be used as it is: a file is damaged, foreign or inconsistent with the others."""


class StoreLockedError(StoreError):
    """Another command holds the store's lock, or one left it behind (a stale lock is never broken here)."""


# --- Paths, times, text ------------------------------------------------------------------------------

@dataclass(frozen=True)
class StorePaths:
    """Where each part of a store lives."""

    root: Path

    @property
    def tables(self) -> Path:
        return self.root / "tables"

    @property
    def derived(self) -> Path:
        return self.root / "derived"

    @property
    def raw(self) -> Path:
        return self.root / "raw"

    @property
    def index(self) -> Path:
        return self.root / "releases.csv"

    @property
    def revisions(self) -> Path:
        return self.root / "revisions.csv"

    @property
    def gaps(self) -> Path:
        return self.root / "gaps.csv"

    @property
    def polls(self) -> Path:
        return self.root / "polls.csv"

    @property
    def state(self) -> Path:
        return self.root / "state.json"

    @property
    def master(self) -> Path:
        return self.root / "sovereign_master.csv"

    @property
    def policy(self) -> Path:
        return self.root / "policy_rates.csv"

    @property
    def lock(self) -> Path:
        return self.root / "store.lock"

    @property
    def journal(self) -> Path:
        return self.root / "journal.json"


def _check_now(now: dt.datetime) -> dt.datetime:
    if not isinstance(now, dt.datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError(f"now must be a timezone-aware datetime, not {now!r}")
    return now


def _india(now: dt.datetime) -> str:
    """``now`` in India time, as the CSVs write times."""
    return now.astimezone(IST).replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S")


def _utc(now: dt.datetime) -> str:
    return now.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _from_utc(text: object, where: str = "state.json") -> dt.datetime | None:
    """A UTC time as ``state.json`` writes it (``2026-11-18T03:09:51Z``); ``None`` for null or ``""``."""
    if text is None or text == "":
        return None
    if not isinstance(text, str):
        raise StoreError(f"{where}: {text!r} is not a time like 2026-11-18T03:09:51Z (a string in UTC)")
    try:
        return dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        raise StoreError(f"{where}: {text!r} is not a time like 2026-11-18T03:09:51Z") from None


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _text(value, how: str) -> str:
    """One cell as the store's CSVs hold it (the format ``cr-strat rbi-releases`` writes)."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ""
    if how in ("date", "time"):
        stamp = pd.Timestamp(value)
        return stamp.strftime("%Y-%m-%dT%H:%M:%S" if how == "time" else "%Y-%m-%d")
    if how == "int":
        number = float(value)
        if not number.is_integer():
            raise ValueError(f"{value!r} is not a whole number")
        return str(int(number))
    if how == "float":
        return f"{float(value):.15g}"
    return value if isinstance(value, str) else str(value)


def text_rows(kind: str, rows: Iterable[dict]) -> list[dict[str, str]]:
    """Parsed rows (``parse_copy``'s) as the text the store writes, one dict per row over ``COLUMNS[kind]``."""
    columns = COLUMNS[kind]
    kinds = {c: column_type(c) for c in columns}
    return [{c: _text(row.get(c), kinds[c]) for c in columns} for row in rows]


def _rows_hash(kind: str, rows: list[dict[str, str]]) -> str:
    """Fingerprint of a release's rows: its kind and every compared cell, line by line."""
    columns = [c for c in COLUMNS[kind] if c not in _UNCOMPARED]
    digest = hashlib.sha256(kind.encode())
    for row in sorted(rows, key=lambda r: _int(r.get("line"), -1)):
        digest.update(b"\x1e" + "\x1f".join(row.get(c, "") for c in columns).encode("utf-8"))
    return digest.hexdigest()[:16]


def _int(text: object, default: int | None = None) -> int | None:
    try:
        return int(str(text))
    except (TypeError, ValueError):
        return default


def _num(text: object) -> float | None:
    if text is None or text == "":
        return None
    try:
        return float(str(text))
    except ValueError:
        return None


def _n(count: int, word: str, plural: str | None = None) -> str:
    """``count`` and ``word``, plural unless the count is one (``1 release``, ``3 releases``)."""
    return f"{count} {word}" if count == 1 else f"{count} {plural or word + 's'}"


def _fmt(value: float | None, digits: int = 2) -> str:
    return "" if value is None else f"{round(value, digits):.15g}"


def _csv_bytes(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(index=False, lineterminator="\n").encode("utf-8")


def _frame(rows: Iterable[dict], columns: Iterable[str]) -> pd.DataFrame:
    columns = list(columns)
    return pd.DataFrame([{c: r.get(c, "") for c in columns} for r in rows], columns=columns, dtype=object)


def _read_csv(path: Path, required: Iterable[str], what: str) -> list[dict[str, str]]:
    """Rows of a store CSV as text, or none if the file is missing; refused if it lacks a column."""
    if not path.exists():
        return []
    try:
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    except (pd.errors.ParserError, UnicodeDecodeError, pd.errors.EmptyDataError) as exc:
        raise StoreError(f"{path}: not a readable {what} CSV ({exc})") from None
    missing = [c for c in required if c not in frame.columns]
    if missing:
        raise StoreError(f"{path}: not a {what} (missing columns {missing})")
    return frame.to_dict("records")


def _span(ids: Iterable[int]) -> str:
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in _ranges(list(ids)))


# --- Atomic writes, the journal and the lock --------------------------------------------------------

def _write_atomic(path: Path, data: bytes) -> None:
    """Write ``data`` to a temporary file beside ``path``, flush it to disk, then rename it over ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + _TMP)
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class _Transaction:
    """Files staged by one command, written so that the store gets all of them or none."""

    def __init__(self, paths: StorePaths):
        self.paths = paths
        self.files: dict[Path, bytes] = {}

    def put(self, path: Path, data: bytes) -> None:
        self.files[path] = data

    def commit(self) -> list[str]:
        """Write the staged files whose bytes differ from what is on disk; their paths, relative to the store."""
        changed = {p: d for p, d in sorted(self.files.items()) if not (p.exists() and p.read_bytes() == d)}
        if not changed:
            return []
        relative = [str(p.relative_to(self.paths.root)) for p in changed]
        for path, data in changed.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path.with_name(path.name + _TMP), "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        _write_atomic(self.paths.journal, json.dumps(relative, indent=1).encode("utf-8"))  # the commit point
        for path in changed:
            os.replace(path.with_name(path.name + _TMP), path)
        self.paths.journal.unlink()
        return relative


def _recover(paths: StorePaths) -> list[str]:
    """Finish the renames of a commit that was interrupted after its journal was written; drop stray temps."""
    done = []
    if paths.journal.exists():
        try:
            relative = json.loads(paths.journal.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise StoreError(f"{paths.journal}: unreadable journal of an interrupted commit ({exc})") from None
        for rel in relative:
            target = paths.root / rel
            tmp = target.with_name(target.name + _TMP)
            if tmp.exists():
                os.replace(tmp, target)
                done.append(rel)
        paths.journal.unlink()
    for directory in (paths.root, paths.tables, paths.derived, paths.raw):
        if directory.is_dir():
            for stray in directory.glob(f"*{_TMP}"):
                stray.unlink()
    return done


def _lock_message(paths: StorePaths, now: dt.datetime | None) -> str:
    try:
        info = json.loads(paths.lock.read_text(encoding="utf-8"))
        if not isinstance(info, dict):
            raise ValueError("not an object")
    except (OSError, ValueError) as exc:
        return (f"store {paths.root} is locked ({paths.lock} exists but cannot be read: {exc}). If no "
                f"rbi-store command is running, delete {paths.lock} and run again.")
    since, pid, host = _from_utc_lenient(info.get("since")), info.get("pid"), info.get("host")
    held = f"by {info.get('command') or 'a command'}"
    if pid is not None or host:
        held += f" (pid {pid if pid is not None else 'unknown'} on {host or 'an unknown host'})"
    if info.get("since"):
        held += f" since {info['since']}"
    why = []
    if now is not None and since is not None and now - since > LOCK_STALE_AFTER:
        why.append(f"it is {(now - since).total_seconds() / 3600:.1f} hours old")
    if host == socket.gethostname() and isinstance(pid, int) and not _alive(pid):
        why.append(f"process {pid} is not running")
    if why:
        return (f"store {paths.root} has a stale lock {held}: {' and '.join(why)}. If no rbi-store command is "
                f"running, delete {paths.lock} and run again; the next command completes or drops an "
                "interrupted commit.")
    return (f"store {paths.root} is locked {held}. Wait for that command to end; if none is running, "
            f"delete {paths.lock}.")


def _from_utc_lenient(text: object) -> dt.datetime | None:
    try:
        return _from_utc(text)
    except StoreError:
        return None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


@contextmanager
def _locked(paths: StorePaths, now: dt.datetime, command: str) -> Iterator[None]:
    """Hold ``store.lock`` for one command; complete or drop an interrupted commit first."""
    paths.root.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(paths.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        raise StoreLockedError(_lock_message(paths, now)) from None
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"command": command, "pid": os.getpid(), "host": socket.gethostname(), "since": _utc(now)}, f)
    try:
        _recover(paths)
        yield
    finally:
        try:
            paths.lock.unlink()
        except FileNotFoundError:
            pass


# --- The stored state --------------------------------------------------------------------------------

@dataclass
class _Store:
    """A store's index, rows, revision log, security master and policy rates, between reading and writing."""

    paths: StorePaths
    index: dict[int, dict[str, str]]
    rows: dict[int, list[dict[str, str]]]
    extra: dict[str, list[str]]  # columns of an older table file that COLUMNS no longer has: kept
    revisions: list[dict[str, str]]
    master: pd.DataFrame | None = None
    policy: list[dict[str, str]] | None = None

    def versions(self, release_id: int) -> set[str]:
        """Fingerprints of every set of rows the store has held for a release."""
        held = {r[k] for r in self.revisions if _int(r["release_id"]) == release_id and r["change"] in ("rows",
                "repaired") for k in ("old_hash", "new_hash")}
        entry = self.index.get(release_id)
        if entry and entry["rows_hash"]:
            held.add(entry["rows_hash"])
        return held - {""}

    def refused(self, release_id: int) -> set[str]:
        return {r["new_hash"] for r in self.revisions if _int(r["release_id"]) == release_id
                and r["change"] == "refused"}


def _load(paths: StorePaths) -> _Store:
    """Everything a command needs from the store, checked: an inconsistent or damaged file is a ``StoreError``."""
    index: dict[int, dict[str, str]] = {}
    for n, row in enumerate(_read_csv(paths.index, _INDEX_REQUIRED, "store release index"), start=2):
        rid = _int(row["release_id"])
        if rid is None or rid in index or row["status"] not in STATUSES:
            raise StoreError(f"{paths.index}, line {n}: release id {row['release_id']!r} with status "
                             f"{row['status']!r} (a release id must be a number listed once; status one of "
                             f"{', '.join(STATUSES)})")
        index[rid] = {c: row.get(c, "") for c in RELEASE_INDEX_COLUMNS}
    revisions = [{c: r[c] for c in REVISION_COLUMNS}
                 for r in _read_csv(paths.revisions, REVISION_COLUMNS, "store revision log")]
    rows: dict[int, list[dict[str, str]]] = {}
    extra: dict[str, list[str]] = {}
    for kind in COLUMNS:
        path = paths.tables / f"{kind}.csv"
        table = _read_csv(path, ("release_id", "line"), f"{kind} table")
        if table:
            extra[kind] = [c for c in table[0] if c not in COLUMNS[kind]]
        for n, row in enumerate(table, start=2):
            rid = _int(row["release_id"])
            entry = index.get(rid) if rid is not None else None
            if entry is None or entry["status"] != "parsed" or entry["kind"] != kind:
                raise StoreError(f"{path}, line {n}: rows of release {row['release_id']!r}, which releases.csv does "
                                 f"not list as a parsed {kind} release; the store is inconsistent (restore it from "
                                 "a backup or build a new one from the raw copies)")
            rows.setdefault(rid, []).append({c: row.get(c, "") for c in (*COLUMNS[kind], *extra[kind])})
    for rid, entry in index.items():
        if entry["status"] == "parsed" and len(rows.get(rid, ())) != _int(entry["rows"], -1):
            raise StoreError(f"{paths.index}: release {rid} is listed with {entry['rows']} rows but "
                             f"tables/{entry['kind']}.csv holds {len(rows.get(rid, ()))}; the store is inconsistent")
    master = policy = None
    if paths.master.exists():
        try:
            master = read_master(paths.master)
        except (ValueError, TypeError, KeyError, UnicodeDecodeError, pd.errors.ParserError) as exc:
            raise StoreError(f"{paths.master}: the store's security master cannot be read ({exc}); give a good one "
                             "with rbi-store-ingest --master, or remove the file") from None
    if paths.policy.exists():
        try:
            policy = read_policy_rates(paths.policy)
        except (ValueError, UnicodeDecodeError) as exc:
            raise StoreError(f"{paths.policy}: the store's policy rates cannot be read ({exc}); give a good table "
                             "with rbi-store-ingest --policy-rates, or remove the file") from None
    return _Store(paths, index, rows, extra, revisions, master, policy)


_POLLER_TIMES = ("last_attempt", "last_success", "last_new", "last_block", "cooldown_until", "stopped", "resumed")
_POLLER_COUNTS = ("blocks", "errors", "html_answers", "first_id", "last_id", "last_http_status")
_POLLER_TEXTS = ("url", "etag", "last_modified", "last_outcome", "last_message", "last_block_reason")


def _check_state(paths: StorePaths) -> dict:
    """``state.json``, every value checked (a hand edit included), or a fresh state when there is none.

    Times are UTC strings (``2026-11-18T03:09:51Z``) or null; counts and ids
    whole numbers of at least 0 or null. A value of the wrong type is a
    ``StoreError`` naming the key, before any request is sent.
    """
    if not paths.state.exists():
        return {"format": STORE_FORMAT, "poller": {}}
    try:
        state = json.loads(paths.state.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise StoreError(f"{paths.state}: not readable JSON ({exc})") from None
    if not isinstance(state, dict) or not isinstance(state.get("poller"), dict):
        raise StoreError(f"{paths.state}: not a store state (no 'poller' object)")
    if state.get("format") != STORE_FORMAT:
        raise StoreError(f"{paths.state}: store format {state.get('format')!r}; this code reads {STORE_FORMAT}")
    poller = state["poller"]

    def bad(key: str, what: str) -> StoreError:
        return StoreError(f"{paths.state}: poller value {key!r} must be {what}, not {poller[key]!r} (fix or remove "
                          "it)")

    for key in _POLLER_TIMES:
        if key in poller:
            _from_utc(poller[key], f"{paths.state}: poller value {key!r}")
    for key in _POLLER_COUNTS:
        value = poller.get(key)
        if value is not None and (type(value) is not int or value < 0):
            raise bad(key, "a whole number of at least 0, or null")
    for key in _POLLER_TEXTS:
        if poller.get(key) is not None and not isinstance(poller[key], str):
            raise bad(key, "text or null")
    items = poller.get("items")
    if isinstance(items, dict):  # the first format: release id -> hash, order lost; the next feed counts as new
        poller["items"] = items = None
    if items is not None and not (isinstance(items, list) and all(
            isinstance(i, list) and len(i) == 2 and all(isinstance(x, str) for x in i) for i in items)):
        raise bad("items", "a list of [item, hash] pairs, or null")
    unreadable = poller.get("unreadable")
    if unreadable is not None and not (isinstance(unreadable, list) and all(isinstance(u, dict) for u in unreadable)):
        raise bad("unreadable", "a list of objects, or null")
    derived = state.get("derived")
    if derived is not None and not (isinstance(derived, dict) and isinstance(derived.get("error"), str)):
        raise StoreError(f"{paths.state}: 'derived' must be an object with an 'error', or null, not {derived!r}")
    if derived is not None:
        _from_utc(derived.get("since"), f"{paths.state}: derived 'since'")
    return state


def _state_bytes(state: dict) -> bytes:
    return (json.dumps(state, indent=1, sort_keys=True) + "\n").encode("utf-8")


def _poll_span(state: dict) -> tuple[int, int] | None:
    """The ids the poller has received: from the floor of its first poll to the highest id any poll held."""
    poller = state.get("poller", {})
    first, last = poller.get("first_id"), poller.get("last_id")
    return (first, last) if first is not None and last is not None else None


# --- Policy rates ------------------------------------------------------------------------------------

_RATE_RANGE = (0.0, 20.0)


def read_policy_rates(source: str | Path | bytes, *, name: str | None = None) -> list[dict[str, str]]:
    """A table of policy rates entered by hand: one row per change, ``POLICY_COLUMNS``.

    ``effective_date`` (ISO, once each) is the first day the rates apply;
    ``repo_pct``, ``sdf_pct`` and ``msf_pct`` are percent, with SDF <= repo
    <= MSF; ``source`` says where they come from (the MPC resolution's
    release id, say). The rows come back sorted by date, as text. A row that
    breaks a rule is refused, naming the file and the line.
    """
    if isinstance(source, bytes):
        data, read_name = source, "<bytes>"
    else:
        data, read_name = Path(source).read_bytes(), Path(source).name
    name = name or read_name
    try:
        frame = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)
    except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError) as exc:
        raise ValueError(f"{name}: not a readable CSV ({exc})") from None
    missing = [c for c in POLICY_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"{name}: not a policy-rates table (missing columns {missing}; columns "
                         f"{', '.join(POLICY_COLUMNS)})")
    out, seen = [], set()
    for n, row in enumerate(frame.to_dict("records"), start=2):
        where = f"{name}, line {n}"
        try:
            day = dt.date.fromisoformat(row["effective_date"].strip())
        except ValueError:
            raise ValueError(f"{where}: effective_date {row['effective_date']!r} is not a date like 2026-10-07") \
                from None
        if day in seen:
            raise ValueError(f"{where}: effective_date {day} is listed twice")
        seen.add(day)
        rates = {}
        for column in ("repo_pct", "sdf_pct", "msf_pct"):
            value = _num(row[column].strip())
            if value is None or not math.isfinite(value) or not _RATE_RANGE[0] < value < _RATE_RANGE[1]:
                raise ValueError(f"{where}: {column} {row[column]!r} is not a rate in percent between "
                                 f"{_RATE_RANGE[0]:g} and {_RATE_RANGE[1]:g}")
            rates[column] = value
        if not rates["sdf_pct"] <= rates["repo_pct"] <= rates["msf_pct"]:
            raise ValueError(f"{where}: the rates must satisfy SDF <= repo <= MSF, not {rates['sdf_pct']:g}, "
                             f"{rates['repo_pct']:g}, {rates['msf_pct']:g}")
        out.append({"effective_date": day.isoformat(), **{c: f"{v:.15g}" for c, v in rates.items()},
                    "source": " ".join(row["source"].split())})
    return sorted(out, key=lambda r: r["effective_date"])


def _policy_on(policy: list[dict[str, str]] | None, day: str) -> dict[str, str] | None:
    """The policy rates in force on ``day`` (ISO): the last row effective on or before it."""
    if not policy or not day:
        return None
    applies = [r for r in policy if r["effective_date"] <= day]
    return applies[-1] if applies else None


# --- Reading sources ---------------------------------------------------------------------------------

@dataclass
class _Copy:
    """One copy of a release (a saved page or a feed item) and what parsing it gave."""

    release: Release
    content: str  # hash of the copy as read: identical copies are parsed once
    kind: str
    how: str
    result: CopyResult | None = None
    rows: list[dict[str, str]] | None = None  # the parsed rows as text

    @property
    def rank(self) -> tuple:
        """Order in which new versions are applied (the last one stays): feed copies by time and name, then pages."""
        r = self.release
        return (not r.from_feed, r.published or dt.datetime.min, r.source_file, self.content)

    @property
    def fingerprint(self) -> str:
        """What ``copies`` keeps of the copy: the store has read it if the fingerprint is there."""
        return self.content[:_FINGERPRINT]


def _source(source) -> tuple[str, bytes]:
    """``(name, bytes)`` of one source; bare bytes are named by their content, so their order does not matter."""
    if isinstance(source, tuple):
        name, data = source
        return str(name), bytes(data)
    if isinstance(source, bytes):
        return f"<bytes {_sha(source)[:12]}>", source
    path = Path(source)
    return path.name, path.read_bytes()


def _feed_item_hash(item) -> str:
    published = item.published.isoformat() if item.published else ""
    return _sha("\x00".join((item.title, item.link, published, item.description_html, item.pubdate)).encode("utf-8"))


def _raw_name(now: dt.datetime, digest: str, feed: bool = True) -> str:
    stamp = f"{now.astimezone(dt.timezone.utc):%Y%m%dT%H%M%SZ}_{digest[:12]}"
    return f"pr_feed_{stamp}.xml" if feed else f"page_{stamp}.html"


def _raw_copy(paths: StorePaths, digest: str) -> Path | None:
    """The raw copy already holding these bytes (by SHA-256), if any."""
    if not paths.raw.is_dir():
        return None
    for candidate in sorted(paths.raw.glob(f"*_{digest[:12]}.*")):
        if candidate.name.endswith(_TMP):
            continue
        if _sha(candidate.read_bytes()) == digest:
            return candidate
    return None


@dataclass
class _Inputs:
    """What the inputs of one ingest hold."""

    copies: list[_Copy] = field(default_factory=list)
    kept: list[tuple[bool, bytes, str]] = field(default_factory=list)  # (a feed?, bytes, SHA-256) to keep in raw/
    listed: list[ListedRelease] = field(default_factory=list)


def _read_inputs(inputs: list[tuple[str, bytes]], report: IngestReport) -> _Inputs:
    """Release copies, listings and the bytes to keep from the inputs; what cannot be read goes to the report."""
    out = _Inputs()
    for name, data in inputs:
        try:
            if is_feed(data):
                feed = read_rss_feed(data, name=name)
                out.kept.append((True, data, _sha(data)))
                for item, release in zip(feed.items, feed_releases(feed), strict=True):
                    if release.release_id is None:
                        report.unreadable.append((release.source_file, f"no release id (prid=) in the link "
                                                                       f"{item.link!r} of {item.title!r}"))
                        continue
                    out.copies.append(_Copy(release, _feed_item_hash(item), *classify(release)))
                continue
            try:
                release = read_release_page(data, name=name)
            except ReleaseParseError as exc:
                try:
                    listed = read_release_listing(data, name=name)
                except ReleaseParseError:
                    raise exc from None
                out.listed.extend(listed)
                out.kept.append((False, data, _sha(data)))
                continue
            out.copies.append(_Copy(release, _sha(data), *classify(release)))
            out.kept.append((False, data, _sha(data)))
        except (BlockedPageError, ReleaseParseError, ValueError) as exc:
            report.unreadable.append((name, str(exc)))
    return out


# --- Ingest ------------------------------------------------------------------------------------------

@dataclass
class IngestReport:
    """What one ingest read and changed. Release ids are listed once each, in rising order."""

    inputs: int = 0
    copies: int = 0
    releases: int = 0
    new: list[int] = field(default_factory=list)
    revised: list[int] = field(default_factory=list)
    repaired: list[int] = field(default_factory=list)
    published: list[int] = field(default_factory=list)
    refused: list[int] = field(default_factory=list)
    listed: list[int] = field(default_factory=list)
    updated: list[int] = field(default_factory=list)  # entries changed without new rows (kind, reason, notes)
    failed: list[tuple[int, str, str]] = field(default_factory=list)  # (release id, kind, reason) still failed
    unreadable: list[tuple[str, str]] = field(default_factory=list)
    raw_saved: list[str] = field(default_factory=list)
    derive_error: str = ""  # why the derived tables were not rebuilt (the previous ones are kept)
    written: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{_n(self.releases, 'release')} in {_n(self.copies, 'copy', 'copies')} from "
                 f"{_n(self.inputs, 'input')}", f"{len(self.new)} new", f"{len(self.revised)} revised",
                 f"{len(self.repaired)} repaired", f"{len(self.failed)} failed"]
        if self.published:
            parts.append(f"{len(self.published)} given their feed time")
        if self.refused:
            parts.append(f"{_n(len(self.refused), 'release')} with a copy refused (rows kept)")
        if self.listed:
            parts.append(f"{len(self.listed)} listed (title and date only)")
        if self.updated:
            parts.append(f"{_n(len(self.updated), 'entry', 'entries')} updated without new rows")
        if self.unreadable:
            parts.append(f"{len(self.unreadable)} unreadable")
        if self.derive_error:
            parts.append(f"derived tables not rebuilt ({self.derive_error}; the previous ones are kept)")
        tail = f"; {_n(len(self.written), 'file')} written" if self.written else "; no file changed"
        return ", ".join(parts) + tail


def ingest(store: str | Path, sources: Iterable, *, now: dt.datetime, keep_raw: bool = True,
           master: str | Path | None = None, policy_rates: str | Path | None = None,
           limits: SanityLimits = DEFAULT_LIMITS, calendar: BusinessCalendar | None = None) -> IngestReport:
    """Parse saved pages, feed copies and listings into the store, rebuild the gaps and the derived tables.

    ``sources``: paths, bytes, or ``(name, bytes)`` pairs; the name is what
    ``source_file`` records. ``now`` (timezone-aware) is the time of the
    ingest: it names the raw copies and dates ``first_seen`` and the
    revisions. ``keep_raw=False`` saves no raw copy. ``master``: a security
    master CSV (``cr-strat sovereign-master``), copied into the store and
    used from then on for the OMO and switch ISINs. ``policy_rates``: a
    hand-entered table of policy rates (``read_policy_rates``), copied into
    the store likewise. ``calendar``: the settlement calendar for the
    parsers' date notes and the day flags (default: the shipped Mumbai
    calendar).
    """
    now = _check_now(now)
    inputs = [_source(s) for s in sources]
    master_bytes = policy_bytes = None
    if master is not None:
        read_master(master)  # refuse a file that is not a master before anything is written
        master_bytes = Path(master).read_bytes()
    if policy_rates is not None:
        policy_bytes = Path(policy_rates).read_bytes()
        read_policy_rates(policy_bytes, name=Path(policy_rates).name)
    paths = StorePaths(Path(store))
    with _locked(paths, now, "ingest"):
        report, tx = _ingest(paths, inputs, now=now, keep_raw=keep_raw, master_bytes=master_bytes,
                             policy_bytes=policy_bytes, limits=limits, calendar=calendar)
        report.written = tx.commit()
    return report


def _ingest(paths: StorePaths, inputs: list[tuple[str, bytes]], *, now: dt.datetime, keep_raw: bool,
            master_bytes: bytes | None, limits: SanityLimits, calendar: BusinessCalendar | None,
            policy_bytes: bytes | None = None, store: _Store | None = None,
            state: dict | None = None) -> tuple[IngestReport, _Transaction]:
    """The work of an ingest, with the lock held: everything it writes is staged in the returned transaction.

    ``state`` is the poller's state when a poll calls; the poll stages it.
    Otherwise ``state.json`` is staged here if the record of a failed rebuild
    of the derived tables changed.
    """
    own_state = state is None
    state = state if state is not None else _check_state(paths)
    derived_before = json.dumps(state.get("derived"), sort_keys=True)
    store = store if store is not None else _load(paths)
    tx = _Transaction(paths)
    report = IngestReport(inputs=len(inputs))
    if master_bytes is not None:
        tx.put(paths.master, master_bytes)
        store.master = read_master(io.BytesIO(master_bytes))
    if policy_bytes is not None:
        tx.put(paths.policy, policy_bytes)
        store.policy = read_policy_rates(policy_bytes, name=paths.policy.name)
    read = _read_inputs(inputs, report)
    if keep_raw:
        staged = set()
        for is_a_feed, data, digest in read.kept:
            if digest in staged or _raw_copy(paths, digest) is not None:
                continue
            staged.add(digest)
            path = paths.raw / _raw_name(now, digest, feed=is_a_feed)
            tx.put(path, data)
            report.raw_saved.append(path.name)
    calendar = calendar if calendar is not None else mumbai_settlement_calendar()
    groups: dict[int, list[_Copy]] = defaultdict(list)
    for copy in read.copies:
        groups[copy.release.release_id].append(copy)
    report.copies, report.releases = len(read.copies), len(groups)

    def order(release_id: int) -> tuple[int, int]:
        ranks = [RELEASE_TYPES[c.kind].order for c in groups[release_id]
                 if c.kind in RELEASE_TYPES and RELEASE_TYPES[c.kind].parser is not None]
        return min(ranks, default=99), release_id

    greenshoe = greenshoe_lookup(_announcement_frame(store.rows, store.index))
    seen = _india(now)
    for release_id in sorted(groups, key=order):
        _merge(store, release_id, groups[release_id], seen, report, limits, greenshoe, calendar)
    _list(store, read.listed, seen, report)
    for release_id in sorted(groups):
        entry = store.index[release_id]
        if entry["status"] == "failed":
            report.failed.append((release_id, entry["kind"], entry["reason"]))
    for name in ("new", "revised", "repaired", "published", "refused", "listed", "updated"):
        setattr(report, name, sorted(set(getattr(report, name))))
    _stage(store, tx, calendar, state, report, now)
    if own_state and json.dumps(state.get("derived"), sort_keys=True) != derived_before:
        tx.put(paths.state, _state_bytes(state))
    return report, tx


def _announcement_frame(rows: dict[int, list[dict[str, str]]], index: dict[int, dict[str, str]]) -> pd.DataFrame:
    """The stored GoI announcement rows, with blanks as ``None``, for ``greenshoe_lookup``."""
    found = [r for rid, rs in rows.items() if index[rid]["kind"] == "gsec_announcement" for r in rs]
    return pd.DataFrame(found).replace({"": None}) if found else pd.DataFrame()


def _parse(copy: _Copy, limits: SanityLimits, greenshoe: dict,
           calendar: BusinessCalendar) -> tuple[CopyResult, list[dict[str, str]] | None]:
    """A copy's parse result and its rows as text; a parser bug fails this copy, not the whole ingest."""
    ctx = ParseContext(limits, greenshoe, calendar=calendar)
    try:
        result = parse_copy(copy.release, ctx, copy.kind, copy.how)
        return result, (text_rows(result.kind, result.rows) if result.status == "parsed" else None)
    except Exception as exc:
        return CopyResult(copy.kind, copy.how, "failed", reason=f"parser error: {type(exc).__name__}: {exc}",
                          notes=list(ctx.notes)), None


def _merge(store: _Store, release_id: int, copies: list[_Copy], seen: str, report: IngestReport,
           limits: SanityLimits, greenshoe: dict, calendar: BusinessCalendar) -> None:
    """Fold the copies of one release read now into the store (see the module docstring)."""
    copies = sorted(copies, key=lambda c: c.rank)
    old = store.index.get(release_id)
    listed = None
    if old is not None and old["status"] == "listed":  # known from a listing only: this is its first copy
        listed, old = store.index.pop(release_id), None
    known = set(old["copies"].split()) if old else set()
    results: dict[str, tuple[CopyResult, list[dict[str, str]] | None]] = {}
    for copy in copies:
        if copy.content not in results:
            results[copy.content] = _parse(copy, limits, greenshoe, calendar)
        copy.result, copy.rows = results[copy.content]
    versions: dict[str, list[_Copy]] = {}
    texts: dict[str, list[dict[str, str]]] = {}
    for copy in copies:
        if copy.result.status == "parsed":
            digest = _rows_hash(copy.result.kind, copy.rows)
            versions.setdefault(digest, []).append(copy)
            texts.setdefault(digest, copy.rows)
    held = store.versions(release_id)
    fresh = [h for h in sorted(versions, key=lambda h: max(c.rank for c in versions[h])) if h not in held]
    was = dict(old) if old else None
    for digest in fresh:
        representative = min(versions[digest], key=lambda c: (c.release.from_feed, c.release.source_file, c.content))
        current = store.index.get(release_id)
        if current is not None and current["status"] == "parsed":
            change, detail = "rows", _diff(current["kind"], store.rows[release_id], representative.result.kind,
                                           texts[digest])
            report.revised.append(release_id)
        elif current is not None:
            change, detail = "repaired", f"was {current['status']}: {current['reason']}"
            report.repaired.append(release_id)
        else:
            change = None
            report.new.append(release_id)
        if change is not None:
            store.revisions.append({"seen": seen, "release_id": str(release_id), "kind": representative.result.kind,
                                    "change": change, "detail": detail, "old_hash": current["rows_hash"],
                                    "new_hash": digest, "source_file": representative.release.source_file})
        _set_entry(store, release_id, representative, digest, texts[digest], seen, revised=change is not None)
    entry = store.index.get(release_id)
    unread = [c for c in copies if c.fingerprint not in known]  # copies the store has never read
    # Every copy the store has read of the release is read again (as rereading raw/ does): its entry becomes what a
    # store built from these copies holds, so that a package update reaches the releases already kept.
    complete = known <= {c.fingerprint for c in copies}
    if entry is not None and entry["status"] == "parsed":
        refused = store.refused(release_id)
        for copy in unread:
            if copy.result.status != "parsed" and copy.content not in refused:
                refused.add(copy.content)
                store.revisions.append({"seen": seen, "release_id": str(release_id), "kind": copy.kind,
                                        "change": "refused", "detail": f"{copy.result.status}: {copy.result.reason}",
                                        "old_hash": entry["rows_hash"], "new_hash": copy.content,
                                        "source_file": copy.release.source_file})
                report.refused.append(release_id)
        if complete and not fresh and entry["rows_hash"] in versions:  # the rows stay; their notes may not
            _renote(entry, versions[entry["rows_hash"]], release_id, report)
        if entry["kind"] == "gsec_announcement" and fresh:  # later results in this ingest use its greenshoe
            greenshoe.update(greenshoe_lookup(_announcement_frame({release_id: store.rows[release_id]},
                                                                  store.index)))
    elif complete or unread:  # no rows: the highest-ranked copy says why, of all of them or of those never read
        best = (copies if complete else unread)[-1]
        replacement = _entry(best.release, best.result, "", 0, first_seen=old["first_seen"] if old else seen,
                             last_changed=old["last_changed"] if old else "",
                             published=old["published"] if old else "")
        if entry is None or _said(replacement) != _said(entry):  # else the entry stays, its source_file too
            store.index[release_id] = replacement
            if entry is not None:
                report.updated.append(release_id)
        if old is None:
            report.new.append(release_id)
    # else: no rows, and only some of the copies read before were read again: the entry stays as it is
    entry = store.index[release_id]
    entry["copies"] = " ".join(sorted(known | {c.fingerprint for c in copies}))
    if listed is not None:
        entry["first_seen"] = listed["first_seen"]
    _set_published(store, release_id, copies, seen, was, report)


def _said(entry: dict[str, str]) -> tuple[str, ...]:
    """What an entry without rows says of its release, the name of its copy left out (a reason may quote it)."""
    name = entry["source_file"]

    def plain(text: str) -> str:
        return text.replace(name, "<copy>") if name else text

    return (entry["kind"], entry["status"], entry["title"], entry["release_date"], entry["press_release_no"],
            entry["classified_by"], plain(entry["reason"]), plain(entry["notes"]))


def _renote(entry: dict[str, str], copies: list[_Copy], release_id: int, report: IngestReport) -> None:
    """The notes and classification of a parsed release from its stored rows' copies, as ``_set_entry`` takes them.

    The notes name the copy the rows came from (``source_file``), whichever
    copy of the same rows gave them.
    """
    copy = min(copies, key=lambda c: (c.release.from_feed, c.release.source_file, c.content))
    notes = "; ".join(copy.result.notes)
    if copy.release.source_file and entry["source_file"]:
        notes = notes.replace(copy.release.source_file, entry["source_file"])
    if (notes, copy.result.classified_by) != (entry["notes"], entry["classified_by"]):
        entry.update(notes=notes, classified_by=copy.result.classified_by)
        report.updated.append(release_id)


def _entry(release: Release, result: CopyResult, digest: str, rows: int, *, first_seen: str, last_changed: str,
           published: str) -> dict[str, str]:
    return {"release_id": str(release.release_id), "kind": result.kind, "status": result.status,
            "title": release.title, "release_date": _text(release.release_date, "date"), "published": published,
            "rows": str(rows), "reason": result.reason, "notes": "; ".join(result.notes),
            "press_release_no": release.press_release_no or "", "source_file": release.source_file,
            "classified_by": result.classified_by, "rows_hash": digest, "first_seen": first_seen,
            "last_changed": last_changed, "copies": ""}


def _set_entry(store: _Store, release_id: int, copy: _Copy, digest: str, rows: list[dict[str, str]], seen: str,
               *, revised: bool) -> None:
    old = store.index.get(release_id)
    store.index[release_id] = _entry(
        copy.release, copy.result, digest, len(rows), first_seen=old["first_seen"] if old else seen,
        last_changed=seen if revised else (old["last_changed"] if old else ""),
        published=old["published"] if old else "")
    store.rows[release_id] = [dict(r) for r in rows]


def _set_published(store: _Store, release_id: int, copies: list[_Copy], seen: str, was: dict | None,
                   report: IngestReport) -> None:
    """Keep the earliest feed time seen for the release, in the index and on its rows."""
    entry = store.index[release_id]
    times = [_text(c.release.published, "time") for c in copies if c.release.from_feed and c.release.published]
    earliest = min([t for t in (entry["published"], *times) if t], default="")
    if earliest == entry["published"]:
        return
    if was is not None and was["status"] == "parsed" and entry["status"] == "parsed":
        store.revisions.append({"seen": seen, "release_id": str(release_id), "kind": entry["kind"],
                                "change": "published",
                                "detail": f"published {earliest} (was {entry['published'] or 'blank'})",
                                "old_hash": entry["rows_hash"], "new_hash": entry["rows_hash"],
                                "source_file": next(c.release.source_file for c in copies
                                                    if _text(c.release.published, "time") == earliest)})
        report.published.append(release_id)
    entry["published"] = earliest


def _list(store: _Store, listed: list[ListedRelease], seen: str, report: IngestReport) -> None:
    """Add the ids a saved listing shows and the store does not hold, as ``listed``: title and date, no rows."""
    for item in sorted(listed, key=lambda x: (x.release_id, x.source_file)):
        if item.release_id in store.index:
            continue
        kind = classify_title(item.title)
        store.index[item.release_id] = {
            "release_id": str(item.release_id), "kind": kind, "status": "listed", "title": item.title,
            "release_date": item.release_date.isoformat(), "published": "", "rows": "0",
            "reason": f"listed in {item.source_file}: title and date only, no copy read", "notes": "",
            "press_release_no": "", "source_file": item.source_file, "classified_by": "title" if kind != "other"
            else "", "rows_hash": "", "first_seen": seen, "last_changed": "", "copies": ""}
        report.listed.append(item.release_id)


def _diff(old_kind: str, old: list[dict[str, str]], new_kind: str, new: list[dict[str, str]]) -> str:
    """What changed between two sets of rows of a release, in a few words and the first cells that differ."""
    out = []
    if old_kind != new_kind:
        out.append(f"kind {old_kind} -> {new_kind}")
    if len(old) != len(new):
        out.append(f"rows {len(old)} -> {len(new)}")
    columns = [c for c in COLUMNS[new_kind] if c not in _UNCOMPARED]
    by_line = {_int(r.get("line")): r for r in old}
    cells = []
    for row in new:
        line = _int(row.get("line"))
        before = by_line.get(line)
        if before is None:
            cells.append(f"line {line} added")
            continue
        for c in columns:
            if before.get(c, "") != row.get(c, ""):
                cells.append(f"line {line} {c} {before.get(c, '') or 'blank'} -> {row.get(c, '') or 'blank'}")
    lines = {_int(r.get("line")) for r in new}
    cells += [f"line {line} removed" for line in sorted(by_line, key=lambda x: (x is None, x)) if line not in lines]
    if len(cells) > _MAX_DIFF:
        cells = cells[:_MAX_DIFF] + [f"and {len(cells) - _MAX_DIFF} more"]
    return "; ".join(out + cells) or "no cell differs"


# --- Writing the store -------------------------------------------------------------------------------

def _stage(store: _Store, tx: _Transaction, calendar: BusinessCalendar, state: dict, report: IngestReport,
           now: dt.datetime) -> None:
    """Stage the tables, the index, the log, the gaps and the derived tables (kept as they were if derive fails)."""
    paths = store.paths
    by_kind: dict[str, list[int]] = defaultdict(list)
    for rid, entry in store.index.items():
        if entry["status"] == "parsed":
            by_kind[entry["kind"]].append(rid)
    for kind in COLUMNS:
        path = paths.tables / f"{kind}.csv"
        if not by_kind.get(kind) and not path.exists():
            continue
        tx.put(path, _csv_bytes(_table_frame(store, kind, by_kind.get(kind, []))))
    tx.put(paths.index, _csv_bytes(_frame((store.index[r] for r in sorted(store.index)), RELEASE_INDEX_COLUMNS)))
    revisions = sorted(store.revisions, key=lambda r: (r["seen"], _int(r["release_id"], -1)))
    tx.put(paths.revisions, _csv_bytes(_frame(revisions, REVISION_COLUMNS)))
    tx.put(paths.gaps, _csv_bytes(_frame(possible_gaps(store.index, _poll_span(state)), GAP_COLUMNS)))
    try:
        tables = derive(store.index, store.rows, calendar=calendar, master=store.master, policy=store.policy)
    except Exception as exc:  # a bug in a derived table must not cost the rows read, nor the raw copies
        report.derive_error = f"{type(exc).__name__}: {exc}"
        previous = state.get("derived") or {}
        state["derived"] = {"error": report.derive_error, "since": previous.get("since") or _utc(now)}
        return
    state.pop("derived", None)
    for name, rows in tables.items():
        tx.put(paths.derived / f"{name}.csv", _csv_bytes(_frame(rows, DERIVED_COLUMNS[name])))


def _table_frame(store: _Store, kind: str, release_ids: list[int]) -> pd.DataFrame:
    columns = [*COLUMNS[kind], *store.extra.get(kind, [])]
    rows = []
    for rid in release_ids:
        entry = store.index[rid]
        for row in store.rows[rid]:
            rows.append({**row, "published": entry["published"], "source_file": entry["source_file"]})
    rows.sort(key=lambda r: (r.get("release_date", ""), _int(r["release_id"], -1), _int(r["line"], -1)))
    return _frame(rows, columns)


# --- Possible gaps -----------------------------------------------------------------------------------

def _ranges(values: list[int]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for v in sorted(values):
        if out and v == out[-1][1] + 1:
            out[-1] = (out[-1][0], v)
        else:
            out.append((v, v))
    return out


def mmo_day(title: str) -> dt.date | None:
    """The as-on date in a Money Market Operations title (``... as on November 13, 2026``), else ``None``."""
    m = _MMO_TITLE.match(" ".join(title.split()))
    if not m:
        return None
    try:
        return dt.datetime.strptime(m.group(1).title(), "%B %d, %Y").date()
    except ValueError:
        return None


def _has_parser(kind: str) -> bool:
    return kind in RELEASE_TYPES and RELEASE_TYPES[kind].parser is not None


def possible_gaps(index: dict[int, dict[str, str]], span: tuple[int, int] | None = None) -> list[dict[str, str]]:
    """Release ids never seen, listed releases of a parsed kind never read, days with no Money Market Operations.

    ``span`` is ``(first, last)``, the ids the poller has received
    (``state.json``). ``in_feed_span`` is ``True`` for a range inside it:
    what polling missed, rather than history before the first poll.
    """
    out = []
    ids = sorted(index)

    def inside(lo: int, hi: int) -> str:
        return str(span is not None and span[0] < lo and hi < span[1])

    if ids:
        held = set(ids)
        missing = [i for i in range(ids[0], ids[-1] + 1) if i not in held]
        for lo, hi in _ranges(missing):
            out.append({"gap": "release_id", "first": str(lo), "last": str(hi), "count": str(hi - lo + 1),
                        "in_feed_span": inside(lo, hi)})
    unread = [r for r in ids if index[r]["status"] == "listed" and _has_parser(index[r]["kind"])]
    for lo, hi in _ranges(unread):
        out.append({"gap": "listed", "first": str(lo), "last": str(hi), "count": str(hi - lo + 1),
                    "in_feed_span": inside(lo, hi)})
    days = {}
    for rid in ids:
        entry = index[rid]
        day = mmo_day(entry["title"]) if entry["kind"] == MMO else None
        if day is not None:
            days[day] = days.get(day, False) or (span is not None and span[0] <= rid <= span[1])
    if days:
        first, last = min(days), max(days)
        fed = sorted(d for d, f in days.items() if f)
        missing_days = [first + dt.timedelta(days=n) for n in range((last - first).days + 1)
                        if first + dt.timedelta(days=n) not in days]
        for lo, hi in _ranges([d.toordinal() for d in missing_days]):
            a, b = dt.date.fromordinal(lo), dt.date.fromordinal(hi)
            within = bool(fed) and fed[0] < a and b < fed[-1]
            out.append({"gap": "mmo_day", "first": a.isoformat(), "last": b.isoformat(), "count": str(hi - lo + 1),
                        "in_feed_span": str(within)})
    return out


# --- Derived tables ----------------------------------------------------------------------------------

def _public(entry: dict[str, str]) -> dict[str, str]:
    """When a release became public: its feed time, else the end of its release date."""
    if entry["published"]:
        when, source = entry["published"], "feed"
    elif entry["release_date"]:
        when, source = f"{entry['release_date']}T23:59:59", "release_date"
    else:
        when, source = "", ""
    return {"public_from": when, "public_from_source": source, "revised": entry["last_changed"]}


def _row_times(parts: list[dict[str, str]]) -> dict[str, str]:
    """Row-level times of a row built from several releases (``_public`` of each part).

    ``public_from`` is the latest part, when every value of the row was public
    (blank if the time of a part is not known); ``first_public_from`` the
    earliest; ``revised`` the latest revision of any part.
    """
    known = [p for p in parts if p["public_from"]]
    if parts and len(known) == len(parts):
        latest = max(known, key=lambda p: p["public_from"])
    else:
        latest = {"public_from": "", "public_from_source": ""}
    return {"first_public_from": min((p["public_from"] for p in known), default=""),
            "public_from": latest["public_from"], "public_from_source": latest["public_from_source"],
            "revised": max((p["revised"] for p in parts if p["revised"]), default="")}


def _release_notes(index: dict[int, dict[str, str]], release_ids: Iterable[int | None]) -> str:
    """The notes ``releases.csv`` holds for the releases a row was built from: ``<id>: <notes> | ...``."""
    out, seen = [], set()
    for rid in release_ids:
        if rid is None or rid in seen:
            continue
        seen.add(rid)
        if index[rid]["notes"]:
            out.append(f"{rid}: {index[rid]['notes']}")
    return " | ".join(out)


def _business_day(calendar: BusinessCalendar, day: str) -> str:
    if not day:
        return ""
    d = dt.date.fromisoformat(day)
    try:
        if not calendar.covers(d):
            return ""
        return str(calendar.is_business_day(d))
    except CalendarCoverageError:
        return ""


def _period_end_days(calendar: BusinessCalendar, day: str) -> tuple[str, str]:
    """Whether ``day`` is the last business day of a quarter, and of March; blank where the calendar is silent."""
    if not day:
        return "", ""
    d = dt.date.fromisoformat(day)
    try:
        if not calendar.covers(d):
            return "", ""
        if d.month % 3:
            return "False", "False"
        end = calendar.adjust(dt.date(d.year, d.month, _calendar.monthrange(d.year, d.month)[1]), "preceding")
    except (CalendarCoverageError, ValueError):
        return "", ""
    return str(d == end), str(d == end and d.month == 3)


def _releases_of(index: dict[int, dict[str, str]], rows: dict[int, list[dict[str, str]]],
                 kind: str) -> list[tuple[int, list[dict[str, str]]]]:
    return [(rid, rows[rid]) for rid in sorted(index)
            if index[rid]["status"] == "parsed" and index[rid]["kind"] == kind]


def _mmo_by_day(index, rows) -> dict[str, tuple[int, list[int]]]:
    """For each as-on date, the Money Market Operations release used (the highest id) and all releases for it."""
    days: dict[str, list[int]] = defaultdict(list)
    for rid, rs in _releases_of(index, rows, MMO):
        days[rs[0]["as_on_date"]].append(rid)
    return {day: (max(rids), sorted(rids)) for day, rids in days.items()}


def _facility(prefix: str, ops: list[dict[str, str]], notes: list[str]) -> dict[str, str]:
    if not ops:
        return {f"{prefix}_cr": "", f"{prefix}_rate_pct": ""}
    amounts = [_num(o["amount_cr"]) or 0.0 for o in ops]
    rates = [(_num(o["rate_pct"]), a) for o, a in zip(ops, amounts, strict=True) if _num(o["rate_pct"]) is not None]
    distinct = sorted({r for r, _ in rates})
    if len(distinct) <= 1:
        rate = distinct[0] if distinct else None
    else:
        weight = sum(a for _, a in rates)
        rate = sum(r * a for r, a in rates) / weight if weight else sum(r for r, _ in rates) / len(rates)
        notes.append(f"{prefix} rates differ: {', '.join(f'{r:g}' for r in distinct)} (amount-weighted mean used)")
    return {f"{prefix}_cr": _fmt(sum(amounts)), f"{prefix}_rate_pct": _fmt(rate, 4)}


def _marks_changed(rows: list[dict[str, str]]) -> str:
    """The printed lines whose footnote marks differ from those of Nov 2026 (``rbi_operations.MMO_ITEMS``)."""
    changed = []
    for r in sorted(rows, key=lambda x: _int(x["line"], -1)):
        expected = MMO_ITEMS.get(r["item"], (None, None))[1]
        if expected is not None and r["marks"] != expected and r["item"] not in changed:
            changed.append(r["item"])
    return " ".join(changed)


def _operations(rows: list[dict[str, str]], block: str) -> list[dict]:
    """The facility rows of one block (``today`` or ``outstanding``) of a Money Market Operations release."""
    prefix = f"laf.{block}."
    return [{"facility": r["item"][len(prefix):], "auction": r["auction_date"], "tenor": _int(r["tenor_days"]),
             "maturity": r["maturity_date"], "amount": _num(r["amount_cr"]), "rate": _num(r["rate_pct"])}
            for r in rows if r["item"].startswith(prefix)]


def _op_key(op: dict) -> tuple:
    return op["facility"], op["auction"], op["tenor"], op["maturity"]


def _shown(ops: list[dict]) -> str:
    return ", ".join(f"{_fmt(o['amount']) or 'blank'} at {_fmt(o['rate'], 4) or 'blank'}" for o in ops)


def _outstanding_check(day: str, rows: list[dict[str, str]], public: str, days: dict, today_ops: dict,
                       longest: int, index: dict, notes: list[str]) -> str:
    """Compare a day's outstanding operations with the operations of the earlier days still running on it.

    The earlier releases count only if they were public by the time this one
    was, so the check uses nothing published later (their latest version, as
    everywhere in the store). The result is ``differs`` (each difference is a
    note), ``agrees`` (every release of the days whose operations could still
    run was there to compare with), or blank.
    """
    d = dt.date.fromisoformat(day)
    window = [(d - dt.timedelta(days=k)).isoformat() for k in range(1, max(longest, 1))]
    stored: dict[str, int] = {}
    for a in window:
        if a in days:
            rid = days[a][0]
            when = _public(index[rid])["public_from"]
            if when and public and when <= public:
                stored[a] = rid
    expected: dict[tuple, list[dict]] = defaultdict(list)
    for a in stored:
        for op in today_ops[a]:
            if op["auction"] == a and op["maturity"] and op["maturity"] > day:
                expected[_op_key(op)].append(op)
    got: dict[tuple, list[dict]] = defaultdict(list)
    for op in _operations(rows, "outstanding"):
        got[_op_key(op)].append(op)
    differs, unchecked = False, False
    for key, ops in sorted(got.items(), key=lambda kv: tuple(str(x) for x in kv[0])):
        facility, auction, tenor, _ = key
        if auction not in stored:
            unchecked = True
            continue
        found = expected.get(key, [])
        if not found:
            notes.append(f"laf.outstanding.{facility}: an operation of {auction} for {tenor} days ({_shown(ops)}) that "
                         f"Money Market Operations as on {auction} (release {stored[auction]}) does not show")
            differs = True
            continue
        pairs = list(zip(sorted(ops, key=lambda o: (o["amount"] or 0, o["rate"] or 0)),
                         sorted(found, key=lambda o: (o["amount"] or 0, o["rate"] or 0)), strict=False))
        if len(ops) != len(found) or any(
                a["amount"] is None or b["amount"] is None or abs(a["amount"] - b["amount"]) > 0.005
                or (a["rate"] is None) != (b["rate"] is None)
                or (a["rate"] is not None and abs(a["rate"] - b["rate"]) > 0.00005) for a, b in pairs):
            notes.append(f"laf.outstanding.{facility} of {auction} for {tenor} days: {_shown(ops)} outstanding, "
                         f"{_shown(found)} taken on {auction} (release {stored[auction]})")
            differs = True
    for key, found in sorted(expected.items(), key=lambda kv: tuple(str(x) for x in kv[0])):
        if key not in got:
            facility, auction, tenor, maturity = key
            notes.append(f"laf.today.{facility} of {auction} for {tenor} days ({_shown(found)}, release "
                         f"{stored[auction]}) runs to {maturity} but is not among the outstanding operations")
            differs = True
    if differs:
        return "differs"
    if not unchecked and len(stored) == len(window):
        return "agrees"
    return ""


def money_market_daily(index, rows, calendar: BusinessCalendar, policy: list[dict[str, str]] | None = None
                       ) -> list[dict[str, str]]:
    """One row per as-on date of the stored Money Market Operations (see the module docstring)."""
    days = _mmo_by_day(index, rows)
    today_ops = {day: _operations(rows[rid], "today") for day, (rid, _) in days.items()}
    longest = max((op["tenor"] for ops in today_ops.values() for op in ops if op["tenor"]), default=1)
    out = []
    for day, (rid, rids) in sorted(days.items()):
        notes: list[str] = []
        if len(rids) > 1:
            notes.append(f"several releases as on {day}: {', '.join(map(str, rids))}; {rid} used")
        by_item: dict[str, list[dict[str, str]]] = defaultdict(list)
        for r in rows[rid]:
            by_item[r["item"]].append(r)
        quarter, march = _period_end_days(calendar, day)
        rec = {"as_on_date": day, "business_day": _business_day(calendar, day), "quarter_end_day": quarter,
               "fy_end_day": march}
        for prefix, item in _SEGMENTS:
            line = by_item[item][0] if by_item[item] else {}
            for field_name, column in (("volume_cr", "amount_cr"), ("rate_pct", "rate_pct"),
                                       ("low_pct", "rate_low_pct"), ("high_pct", "rate_high_pct")):
                rec[f"{prefix}_{field_name}"] = line.get(column, "")
        for prefix, item in _FACILITIES:
            rec.update(_facility(prefix, by_item[item], notes))
        for column, item in _NET_LINES:
            rec[column] = by_item[item][0]["amount_cr"] if by_item[item] else ""
        for prefix, item in _RESERVES:
            line = by_item[item][0] if by_item[item] else {}
            rec[f"{prefix}_cr"], rec[f"{prefix}_date"] = line.get("amount_cr", ""), line.get("ref_date", "")
        own = _public(index[rid])
        rec["marks_changed"] = _marks_changed(rows[rid])
        rec["outstanding_check"] = _outstanding_check(day, rows[rid], own["public_from"], days, today_ops, longest,
                                                      index, notes)
        rec.update(_policy_check(policy, day, rec, notes))
        rec.update(release_id=str(rid), release_date=index[rid]["release_date"], **own,
                   release_notes=_release_notes(index, [rid]), notes="; ".join(notes))
        out.append(rec)
    return out


def _policy_check(policy, day: str, rec: dict, notes: list[str]) -> dict[str, str]:
    """The repo rate in force on ``day`` from the hand-entered table, and whether the day's corridor agrees."""
    rule = _policy_on(policy, day)
    if rule is None:
        return {"repo_rate_pct": "", "policy_check": ""}
    differ = []
    for name, column, expected in (("SDF", "sdf_rate_pct", rule["sdf_pct"]), ("MSF", "msf_rate_pct", rule["msf_pct"])):
        value = _num(rec[column])
        if value is not None and abs(value - float(expected)) > 1e-9:
            differ.append(f"{name} {value:g} against {float(expected):g}")
    if differ:
        notes.append(f"the corridor differs from the policy rates effective {rule['effective_date']} "
                     f"({rule['source'] or 'no source given'}): {', '.join(differ)}")
    checked = rec["sdf_rate_pct"] != "" or rec["msf_rate_pct"] != ""
    return {"repo_rate_pct": rule["repo_pct"], "policy_check": "differs" if differ else ("agrees" if checked else "")}


_OPERATION = {"repo": "variable_rate_repo", "reverse_repo": "variable_rate_reverse_repo"}


def _laf_key(row: dict[str, str]) -> tuple[str, str, int]:
    return row["direction"], row["auction_date"], _int(row["tenor_days"], -1)


def liquidity_operations(index, rows, policy: list[dict[str, str]] | None = None) -> list[dict[str, str]]:
    """One row per variable rate repo or reverse repo auction (see the module docstring)."""
    announcements: dict[tuple, list[tuple[int, dict]]] = defaultdict(list)
    results: dict[tuple, list[tuple[int, dict]]] = defaultdict(list)
    for rid, rs in _releases_of(index, rows, LAF_ANNOUNCEMENT):
        for r in rs:
            announcements[_laf_key(r)].append((rid, r))
    for rid, rs in _releases_of(index, rows, LAF_RESULT):
        for r in rs:
            results[_laf_key(r)].append((rid, r))
    mmo_days = _mmo_by_day(index, rows)
    lines: dict[tuple, list[tuple[int, dict]]] = defaultdict(list)
    for rid, _ in mmo_days.values():
        for r in rows[rid]:
            if r["item"] in ("laf.today.repo", "laf.today.reverse_repo"):
                direction = "repo" if r["item"] == "laf.today.repo" else "reverse_repo"
                lines[(direction, r["auction_date"], _int(r["tenor_days"], -1))].append((rid, r))
    out = []
    for key in sorted(set(announcements) | set(results) | set(lines), key=lambda k: (k[1], k[0], k[2])):
        direction, day, tenor = key
        anns, ress, mmos = announcements.get(key, []), results.get(key, []), lines.get(key, [])
        notes: list[str] = []
        ann = max(anns, key=lambda x: x[0]) if anns else None
        if len(anns) > 1:
            notes.append(f"announced in releases {', '.join(str(a[0]) for a in anns)}; {ann[0]} used")
        if len(ress) > 1:
            notes.append(f"{len(ress)} results for this direction, date and tenor (releases "
                         f"{', '.join(str(r[0]) for r in ress)}): each is a row")
        for res in (ress or [None]):
            row_notes = list(notes)
            rec = {"operation": _OPERATION.get(direction, direction), "direction": direction, "auction_date": day,
                   "tenor_days": str(tenor) if tenor >= 0 else ""}
            parts = []
            if ann is not None:
                rid, a = ann
                parts.append(_public(index[rid]))
                rec.update(announcement_id=str(rid), announcement_public_from=parts[-1]["public_from"],
                           notified_cr=a["notified_cr"], window_start=a["window_start"], window_end=a["window_end"],
                           reversal_date=a["reversal_date"])
            if res is not None:
                rid, r = res
                parts.append(_public(index[rid]))
                rec.update(result_id=str(rid), result_public_from=parts[-1]["public_from"],
                           received_cr=r["received_cr"], accepted_cr=r["accepted_cr"],
                           cutoff_rate_pct=r["cutoff_rate_pct"], wa_rate_pct=r["wa_rate_pct"],
                           partial_pct=r["partial_pct"], liquidity_effect_cr=r["liquidity_effect_cr"])
                if ann is None:
                    rec["notified_cr"] = r["notified_cr"]
                elif _num(r["notified_cr"]) != _num(ann[1]["notified_cr"]):
                    row_notes.append(f"notified {ann[1]['notified_cr']} in the announcement, {r['notified_cr']} in "
                                     "the result")
                _cutoff_against_policy(policy, direction, day, r, row_notes)
            rec["mmo_check"] = _mmo_check(res, mmos, mmo_days.get(day), rec, parts, index, row_notes,
                                          several=len(ress) > 1)
            if not rec.get("reversal_date") and len(mmos) == 1 and mmos[0][1]["maturity_date"]:
                rec["reversal_date"] = mmos[0][1]["maturity_date"]
            elif not rec.get("reversal_date") and day and tenor >= 0:
                rec["reversal_date"] = (dt.date.fromisoformat(day) + dt.timedelta(days=tenor)).isoformat()
                row_notes.append("reversal date: auction date plus tenor (no announcement)")
            if ann is None and res is None:
                row_notes.append(f"known only from Money Market Operations (release {mmos[0][0]})")
            rec.update(_row_times(parts), notes="; ".join(row_notes),
                       release_notes=_release_notes(index, [ann[0] if ann else None, res[0] if res else None]))
            out.append(rec)
    return out


def _cutoff_against_policy(policy, direction: str, day: str, result: dict[str, str], notes: list[str]) -> None:
    """Note a reverse repo cut-off above, or a repo cut-off below, the policy repo rate in force (spec 2.1)."""
    rule, cutoff = _policy_on(policy, day), _num(result["cutoff_rate_pct"])
    if rule is None or cutoff is None:
        return
    repo = float(rule["repo_pct"])
    if direction == "reverse_repo" and cutoff > repo + 1e-9:
        notes.append(f"cut-off {cutoff:g} above the repo rate {repo:g} in force (policy rates of "
                     f"{rule['effective_date']})")
    elif direction == "repo" and cutoff < repo - 1e-9:
        notes.append(f"cut-off {cutoff:g} below the repo rate {repo:g} in force (policy rates of "
                     f"{rule['effective_date']})")


def _mmo_check(res, mmos, day_release, rec: dict, parts: list, index, notes: list[str], *, several: bool) -> str:
    """Compare a result with its line in that day's Money Market Operations, filling the ``mmo_*`` fields.

    Whenever the day's release is consulted, it is one of the row's parts:
    its time counts for the row's ``public_from``.
    """
    def consulted(rid: int) -> None:
        parts.append(_public(index[rid]))
        rec.update(mmo_release_id=str(rid), mmo_public_from=parts[-1]["public_from"])

    if len(mmos) > 1 or (several and mmos):
        what = f"{len(mmos)} lines" if len(mmos) > 1 else "one line for several results"
        notes.append(f"{what} of this direction and tenor in Money Market Operations release {mmos[0][0]}: "
                     "not matched")
        consulted(mmos[0][0])
        return "ambiguous"
    if mmos:
        rid, m = mmos[0]
        consulted(rid)
        rec.update(mmo_amount_cr=m["amount_cr"], mmo_rate_pct=m["rate_pct"])
    if res is None:
        return "no_result" if mmos and rec.get("announcement_id") else ""
    if not mmos:
        if day_release is None:
            return ""
        notes.append(f"Money Market Operations as on {rec['auction_date']} (release {day_release[0]}) shows no such "
                     "operation")
        consulted(day_release[0])
        return "missing"
    r = res[1]
    amount, accepted = _num(rec["mmo_amount_cr"]), _num(r["accepted_cr"])
    rate, cutoff = _num(rec["mmo_rate_pct"]), _num(r["cutoff_rate_pct"])
    differs = []
    if amount is None or accepted is None or abs(amount - accepted) > 0.005:
        differs.append(f"amount {rec['mmo_amount_cr'] or 'blank'} against {r['accepted_cr'] or 'blank'} accepted")
    if rate is None or cutoff is None or abs(rate - cutoff) > 0.00005:
        differs.append(f"rate {rec['mmo_rate_pct'] or 'blank'} against cut-off {r['cutoff_rate_pct'] or 'blank'}")
    if differs:
        notes.append("Money Market Operations differ: " + ", ".join(differs))
        return "differs"
    return "agrees"


def _security_key(row: dict[str, str]) -> str:
    return row["name_key"] or normalise_name(row["security"])


def omo_operations(index, rows, master: pd.DataFrame | None = None) -> list[dict[str, str]]:
    """One row per security per open market operation (see the module docstring)."""
    announced: dict[tuple, list[int]] = defaultdict(list)
    resulted: dict[tuple, list[int]] = defaultdict(list)
    for kind, into in ((OMO_ANNOUNCEMENT, announced), (OMO_RESULT, resulted)):
        for rid, rs in _releases_of(index, rows, kind):
            into[(rs[0]["operation"], rs[0]["auction_date"])].append(rid)
    out = []
    for auction in sorted(set(announced) | set(resulted)):
        operation, day = auction
        notes: list[str] = []
        ann_id = max(announced[auction]) if announced.get(auction) else None
        res_id = max(resulted[auction]) if resulted.get(auction) else None
        for what, ids in (("announced", announced.get(auction, [])), ("resulted", resulted.get(auction, []))):
            if len(ids) > 1:
                notes.append(f"{what} in releases {', '.join(map(str, ids))}; {max(ids)} used")
        ann_rows = {_security_key(r): r for r in rows[ann_id]} if ann_id else {}
        res_rows = {_security_key(r): r for r in rows[res_id]} if res_id else {}
        keys = sorted(set(ann_rows) | set(res_rows), key=lambda k: (
            _int(ann_rows[k]["line"]) if k in ann_rows else 10_000 + (_int(res_rows[k]["line"]) or 0)))
        for key in keys:
            a, r = ann_rows.get(key), res_rows.get(key)
            row_notes = list(notes)
            parts = ([_public(index[ann_id])] if a is not None else []) + ([_public(index[res_id])] if r is not None
                                                                          else [])
            rec = {"operation": operation, "auction_date": day, "security": (a or r)["security"],
                   "name_key": (a or r)["name_key"], "line": (a or r)["line"]}
            if a is not None:
                rec.update(settlement_date=a["settlement_date"], isin=a["isin"], isin_source="announcement",
                           maturity_date=a["maturity_date"], aggregate_notified_cr=a["aggregate_notified_cr"],
                           auction_method=a["auction_method"], programme_pr_no=a["programme_pr_no"],
                           programme_date=a["programme_date"], announcement_id=str(ann_id),
                           announcement_public_from=_public(index[ann_id])["public_from"])
            elif ann_id is not None:
                row_notes.append(f"not in the announcement (release {ann_id})")
            if r is not None:
                rec.update({c: r[c] for c in ("offers_received_count", "offered_cr", "offers_accepted_count",
                                              "accepted_cr", "cutoff_yield", "cutoff_price", "way", "wap",
                                              "partial_allotment_pct", "total_offered_cr", "total_accepted_cr",
                                              "liquidity_effect_cr")},
                           result_id=str(res_id), result_public_from=_public(index[res_id])["public_from"])
                if a is None:
                    rec.update(settlement_date=r["settlement_date"], aggregate_notified_cr=r["aggregate_notified_cr"])
                else:
                    if r["settlement_date"] != a["settlement_date"]:
                        row_notes.append(f"settlement {a['settlement_date']} in the announcement, "
                                         f"{r['settlement_date']} in the result")
                    if _num(r["aggregate_notified_cr"]) != _num(a["aggregate_notified_cr"]):
                        row_notes.append(f"aggregate notified {a['aggregate_notified_cr']} in the announcement, "
                                         f"{r['aggregate_notified_cr']} in the result")
            elif res_id is not None:
                row_notes.append(f"not in the result (release {res_id})")
            _omo_isin(rec, master, row_notes)
            rec.update(_row_times(parts), notes="; ".join(row_notes),
                       release_notes=_release_notes(index, [ann_id if a is not None else None,
                                                            res_id if r is not None else None]))
            out.append(rec)
    return out


def _omo_isin(rec: dict, master: pd.DataFrame | None, notes: list[str]) -> None:
    if master is None:
        if not rec.get("isin"):
            notes.append("no ISIN: the announcement was not seen and no security master is stored")
        return
    found = match_isin(master, rec["security"], on=rec["auction_date"] or None,
                       maturity=rec.get("maturity_date") or None)
    rec["isin_match"] = found.status
    if rec.get("isin"):
        if found.isin is not None and found.isin != rec["isin"]:
            notes.append(f"the security master gives {found.isin} for {rec['security']}")
        elif found.isin is None:
            notes.append(f"the security master has no single ISIN for {rec['security']} ({found.status})")
    elif found.isin is not None:
        rec.update(isin=found.isin, isin_source="master")
    else:
        notes.append(f"no ISIN: the security master finds {found.status} for {rec['security']}")


def nominal_tenor_bucket(years: float | None) -> str:
    """``issuance.TENOR_BUCKETS`` label for a nominal tenor of ``years``: the bucket with ``lo < years <= hi``.

    A reissue auctioned under a calendar line of N years has less than N years
    to run once it settles, so its residual maturity falls in the bucket whose
    upper bound is N or above N: 5 years in 3-5y, 10 years in 5-10y, 40 years
    in 30y+. A new issue of N years is put there too, by its tenor
    (``_new_issue_bucket``): measured on 365.25-day years its residual maturity
    is a little more than N as often as not.
    """
    if years is None:
        return "unknown"
    for lo, hi, label in TENOR_BUCKETS:
        if lo < years <= hi:
            return label
    return TENOR_BUCKETS[0][2] if years <= TENOR_BUCKETS[0][0] else TENOR_BUCKETS[-1][2]


def _anniversary(start: dt.date, years: int) -> dt.date:
    """``start`` moved ``years`` years on; 29 February becomes 28 February in a year without it."""
    day = 28 if (start.month, start.day) == (2, 29) else start.day
    return dt.date(start.year + years, start.month, day)


def _sdl_maturity(row: dict[str, str]) -> tuple[str, str]:
    """An SDL's maturity (not printed) and how it was found: the anniversary of its first issue.

    A reissue: of the first issue date its tenor cell prints, in the maturity
    year of its name. A new issue: of the settlement date, its tenor on.
    """
    year, first = _int(row.get("maturity_year")), row.get("first_issue_date")
    if row.get("security") and year is not None and first:
        issued = dt.date.fromisoformat(first)
        return _anniversary(issued, year - issued.year).isoformat(), "the first issue's anniversary"
    tenor, settle = _int(row.get("tenor_years")), row.get("settlement_date")
    if not row.get("security") and tenor is not None and settle:
        return _anniversary(dt.date.fromisoformat(settle), tenor).isoformat(), "the anniversary of the settlement"
    return "", ""


def _goi_new_issue_tenor(row: dict[str, str]) -> int | None:
    """The tenor in whole years of a GoI announcement line for a new issue (``New GS 2036``: no coupon yet).

    The years from settlement to repayment on 365.25-day years, rounded: the
    tenor a calendar line names. ``None`` for a reissue, or without the dates.
    """
    name = parse_security_name(row["security"])
    new = name.new_issue or (name.coupon_pct is None and row["security"].strip().lower().startswith("new "))
    start = row["settlement_date"] or row["auction_date"]
    if not new or not row["repayment_date"] or not start:
        return None
    years = (dt.date.fromisoformat(row["repayment_date"]) - dt.date.fromisoformat(start)).days / 365.25
    tenor = math.floor(years + 0.5)
    return tenor if tenor >= 1 else None


def _new_issue_bucket(tenor: int, maturity: str, start: str) -> tuple[str, str]:
    """A new issue's ``tenor_bucket``, that of a calendar line of its tenor, and the note that says so.

    Measured on 365.25-day years, as ``supply_monthly.csv`` does, a new N-year
    issue has a little more or a little less than N years to run, as its life
    spans more or fewer leap days than N/4, and lands on either side of a
    bucket bound at N (3, 5, 10, 15, 30 years) from one settlement year to the
    next; the planned line of the same auction would not move. The note gives
    that other bucket where it differs.
    """
    bucket = nominal_tenor_bucket(tenor)
    note = f"new issue: tenor bucket of its {tenor}-year tenor, as for a calendar line"
    residual = tenor_bucket(maturity, start) if maturity and start else "unknown"
    if residual not in (bucket, "unknown"):
        note += f" (on residual maturity, as supply_monthly.csv counts it: {residual})"
    return bucket, note


def supply_calendar(index, rows) -> list[dict[str, str]]:
    """What was announced to be auctioned, and when that became public (see the module docstring)."""
    out = []

    def add(source: str, rid: int, row: dict, **fields) -> None:
        out.append({"source": source, "release_id": str(rid), "line": row["line"], **fields, **_public(index[rid]),
                    "release_notes": _release_notes(index, [rid])})

    for rid, rs in _releases_of(index, rows, GSEC_CALENDAR):
        for r in rs:
            add(GSEC_CALENDAR, rid, r, kind=GREEN if r["security_type"] == "SGrB" else GSEC,
                period_start=r["week_start"], period_end=r["week_end"], tenor_years=r["tenor_years"],
                tenor_bucket=nominal_tenor_bucket(_num(r["tenor_years"])), amount_cr=r["amount_cr"],
                week_no=r["week_no"], allocation_no=r["allocation_no"])
    for rid, rs in _releases_of(index, rows, SDL_CALENDAR):
        for r in rs:
            add(SDL_CALENDAR, rid, r, kind=SDL, period_start=r["quarter_start"], period_end=r["quarter_end"],
                amount_cr=r["total_cr"], tenor_bucket="all")
    for rid, rs in _releases_of(index, rows, "gsec_announcement"):
        for r in rs:
            start = r["settlement_date"] or r["auction_date"]
            tenor = _goi_new_issue_tenor(r)
            if tenor is not None:
                bucket, note = _new_issue_bucket(tenor, r["repayment_date"], start)
            else:  # a reissue: its residual maturity, below the tenor of the line it is auctioned under
                bucket = tenor_bucket(r["repayment_date"], start) if r["repayment_date"] and start else "unknown"
                note = ""
            add("gsec_announcement", rid, r, kind=parse_security_name(r["security"]).kind,
                period_start=r["auction_date"], period_end=r["auction_date"], auction_date=r["auction_date"],
                settlement_date=r["settlement_date"], security=r["security"], name_key=r["name_key"],
                tenor_years="" if tenor is None else str(tenor), maturity_date=r["repayment_date"],
                amount_cr=r["notified_cr"], greenshoe_cr=r["greenshoe_cr"], tenor_bucket=bucket, notes=note)
    for rid, rs in _releases_of(index, rows, "tbill_announcement"):
        for r in rs:
            start = r["settlement_date"] or r["auction_date"]
            add("tbill_announcement", rid, r, kind=TBILL, period_start=r["auction_date"],
                period_end=r["auction_date"], auction_date=r["auction_date"], settlement_date=r["settlement_date"],
                security=r["security"], tenor_days=r["tenor_days"], maturity_date=r["maturity_date"],
                amount_cr=r["notified_cr"],
                tenor_bucket=tenor_bucket(r["maturity_date"], start) if r["maturity_date"] and start else "unknown")
    for rid, rs in _releases_of(index, rows, "sdl_announcement"):
        for r in rs:
            start = r["settlement_date"] or r["auction_date"]
            maturity, how = _sdl_maturity(r)
            tenor = _int(r["tenor_years"])
            if r["issue_type"] == "new" and tenor is not None:  # its tenor is printed, as a calendar line's is
                bucket, note = _new_issue_bucket(tenor, maturity, start)
                if maturity:
                    note += f"; matures {maturity} if on {how} (maturity not printed)"
            elif maturity and start:
                bucket = tenor_bucket(maturity, start)
                note = f"tenor bucket from {how}, {maturity} (maturity not printed)"
            else:
                bucket, note = nominal_tenor_bucket(_num(r["tenor_years"])), ""
            add("sdl_announcement", rid, r, kind=SDL, period_start=r["auction_date"], period_end=r["auction_date"],
                auction_date=r["auction_date"], settlement_date=r["settlement_date"], security=r["security"],
                name_key=r["name_key"], state_code=r["state_code"], tenor_years=r["tenor_years"],
                amount_cr=r["notified_cr"], tenor_bucket=bucket, notes=note)
    out.sort(key=lambda r: (r.get("period_start", ""), r["source"], _int(r["release_id"], -1), _int(r["line"], -1)))
    return out


def _leg(master: pd.DataFrame | None, security: str, day: str, leg: str, notes: list[str]) -> tuple[str, str]:
    """ISIN and maturity date of one leg of a switch, from the stored security master."""
    if master is None:
        return "", ""
    found = match_isin(master, security, on=day or None)
    if found.isin is None:
        notes.append(f"{leg} {security}: the security master finds {found.status}")
        return "", ""
    maturity = master.loc[master["isin"] == found.isin, "maturity_date"].iloc[0]
    return found.isin, ("" if pd.isna(maturity) else pd.Timestamp(maturity).strftime("%Y-%m-%d"))


def switch_operations(index, rows, master: pd.DataFrame | None = None) -> list[dict[str, str]]:
    """One row per pair of securities in a switch result: both legs with ISINs, amounts and the cut-off."""
    out = []
    for rid, rs in _releases_of(index, rows, "switch_result"):
        for r in rs:
            notes = [] if master is not None else ["no ISINs: no security master is stored"]
            rec = {c: r[c] for c in ("auction_date", "line", "source_security", "source_key",
                                     "destination_security", "destination_key", "notified_cr",
                                     "offers_received_count", "source_offered_cr", "offers_accepted_count",
                                     "source_accepted_cr", "destination_issued_cr", "dest_cutoff_price",
                                     "dest_cutoff_yield")}
            for leg in ("source", "destination"):
                rec[f"{leg}_isin"], rec[f"{leg}_maturity_date"] = _leg(master, r[f"{leg}_security"],
                                                                       r["auction_date"], leg, notes)
            rec.update(release_id=str(rid), release_date=index[rid]["release_date"], **_public(index[rid]),
                       release_notes=_release_notes(index, [rid]), notes="; ".join(notes))
            out.append(rec)
    out.sort(key=lambda r: (r["auction_date"], _int(r["release_id"], -1), _int(r["line"], -1)))
    return out


def derive(index: dict[int, dict[str, str]], rows: dict[int, list[dict[str, str]]], *,
           calendar: BusinessCalendar, master: pd.DataFrame | None = None,
           policy: list[dict[str, str]] | None = None) -> dict[str, list[dict[str, str]]]:
    """The five derived tables from a store's index and rows (text in, text out; deterministic)."""
    return {"money_market_daily": money_market_daily(index, rows, calendar, policy),
            "liquidity_operations": liquidity_operations(index, rows, policy),
            "omo_operations": omo_operations(index, rows, master),
            "supply_calendar": supply_calendar(index, rows),
            "switch_operations": switch_operations(index, rows, master)}


# --- Reading the store back --------------------------------------------------------------------------

_TIME_COLUMNS = frozenset({"published", "public_from", "revised", "first_seen", "last_changed", "seen"})
_UTC_COLUMNS = frozenset({"time", "cooldown_until"})  # polls.csv: UTC, like state.json
_BOOL_COLUMNS = frozenset({"business_day", "quarter_end_day", "fy_end_day", "in_feed_span"})
_INT_COLUMNS = frozenset({"rows", "count", "mmo_release_id", "http_status", "bytes", "items", "unreadable"})
_TEXT_COLUMNS = frozenset({"first", "last", "rows_hash", "old_hash", "new_hash", "copies", "ids", "new", "missed",
                           "failed"})
_ROOT_TABLES = ("releases", "revisions", "gaps", "polls")


def store_column_type(name: str) -> str:
    """How a column of the store's CSVs is typed: ``rbi_releases.column_type`` plus the store's own columns.

    One of ``date``, ``time`` (India time, no zone), ``utc`` (``polls.csv``),
    ``int``, ``float``, ``bool`` and ``text``.
    """
    if name in _TEXT_COLUMNS:
        return "text"
    if name in _UTC_COLUMNS:
        return "utc"
    if name in _TIME_COLUMNS or name.endswith("_public_from"):
        return "time"
    if name in _BOOL_COLUMNS:
        return "bool"
    if name in _INT_COLUMNS or name.endswith("_id"):
        return "int"
    if name.endswith("_date"):
        return "date"
    return column_type(name)


def read_table(store: str | Path, name: str) -> pd.DataFrame:
    """A store CSV, typed: a kind (``tables/``), a derived table, or ``releases``, ``revisions``, ``gaps``, ``polls``.

    Dates and times become pandas Timestamps (India time with no zone; UTC in
    ``polls.csv``), whole numbers ``Int64``, amounts and rates floats, flags
    booleans; blank cells are missing values in every column, text included.
    """
    paths = StorePaths(Path(store))
    if name in COLUMNS:
        path = paths.tables / f"{name}.csv"
    elif name in DERIVED_COLUMNS:
        path = paths.derived / f"{name}.csv"
    elif name in _ROOT_TABLES:
        path = paths.root / f"{name}.csv"
    else:
        raise ValueError(f"no store table {name!r}: a release kind, one of {sorted(DERIVED_COLUMNS)}, or "
                         f"{', '.join(_ROOT_TABLES)}")
    if not path.exists():
        raise FileNotFoundError(f"{path}: not written yet")
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    for column in frame.columns:
        values = frame[column].replace("", None)
        how = store_column_type(column)
        if how in ("date", "time"):
            frame[column] = pd.to_datetime(values)
        elif how == "utc":
            frame[column] = pd.to_datetime(values, utc=True)
        elif how == "int":
            frame[column] = pd.to_numeric(values).astype("Int64")
        elif how == "float":
            frame[column] = pd.to_numeric(values).astype(float)
        elif how == "bool":
            frame[column] = values.map({"True": True, "False": False}).astype("boolean")
        else:
            frame[column] = values
    return frame


# --- The poller --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class FeedResponse:
    """What a transport got back: HTTP status (``None`` when no answer came), headers (lower-case names), body."""

    status: int | None
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    error: str = ""


Transport = Callable[[str, dict[str, str], float], FeedResponse]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Turn a redirect into its HTTP error: following it would be a second request, perhaps to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _lower(headers) -> dict[str, str]:
    return {k.lower(): v for k, v in headers.items()} if headers is not None else {}


def urllib_transport(url: str, headers: dict[str, str], timeout: float, *, opener=None) -> FeedResponse:
    """One GET with the standard library (the environment's proxy and certificates); no redirect is followed."""
    opener = opener if opener is not None else urllib.request.build_opener(_NoRedirect())
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with opener.open(request, timeout=timeout) as response:
            return FeedResponse(response.status, _lower(response.headers), response.read(MAX_FEED_BYTES + 1))
    except urllib.error.HTTPError as exc:  # an answer with an error status: 304, 418, 403, 5xx, a redirect
        try:
            body = exc.read(MAX_FEED_BYTES + 1)
        except (OSError, http.client.HTTPException):
            body = b""
        return FeedResponse(exc.code, _lower(exc.headers), body)
    except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError, OverflowError) as exc:
        return FeedResponse(None, error=f"{type(exc).__name__}: {getattr(exc, 'reason', exc)}")


@dataclass
class PollResult:
    """What one poll did. ``outcome``: new, unchanged, not_modified, too_soon (no request), cooling_down (no
    request), stopped (no request), blocked, server_error, network_error, http_error, bad_feed or clock_error (no
    request). ``attention`` lists what a person should look at although the poll worked."""

    outcome: str
    message: str
    requested: bool = False
    http_status: int | None = None
    report: IngestReport | None = None
    missed: list[int] = field(default_factory=list)
    attention: list[str] = field(default_factory=list)
    feed_items: int | None = None
    feed_ids: list[int] = field(default_factory=list)
    unreadable: list[dict] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        """0 for a normal outcome (nothing new and too soon included), 4 for one that needs a look, 3 when blocked,
        cooling down or stopped, 1 for an error."""
        if self.outcome in ("blocked", "cooling_down", "stopped"):
            return EXIT_BLOCKED
        if self.outcome in ("new", "unchanged", "not_modified", "too_soon"):
            return EXIT_ATTENTION if self.attention else EXIT_OK
        return EXIT_ERROR


def _printable(text: str) -> bool:
    """Visible ASCII characters and spaces only: what a URL or a header value may carry unencoded."""
    return all(" " <= c <= "~" for c in text)


def check_url(url: str) -> str:
    """``url`` if the poller may request it: http(s), and not a host or file RBI guards with a bot challenge.

    Checked here, before anything is recorded, is also everything the standard
    library would refuse only once the request is under way: a character that
    is not visible ASCII (a space included), a port that is not a number from
    1 to 65535.
    """
    if not isinstance(url, str) or not url or not _printable(url) or " " in url:
        raise ValueError(f"the feed URL must be visible ASCII characters without spaces, not {url!r}")
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme not in ("https", "http") or not host:
        raise ValueError(f"the feed URL must be http or https, not {url!r}")
    try:
        port = parts.port
    except ValueError:
        port = 0
    if port == 0:
        raise ValueError(f"the feed URL's port must be a number from 1 to 65535, not {url!r}")
    if host in REFUSED_HOSTS:
        raise ValueError(f"{host} serves a bot challenge to programs; the store never requests it")
    if host.endswith("rbi.org.in") and parts.path.lower().rstrip("/") == "/robots.txt":
        raise ValueError("RBI's robots.txt answers programs with HTTP 418; the store never requests it")
    return url


def check_user_agent(user_agent: str) -> str:
    """``user_agent`` if it can be sent as the client name: one non-empty line of visible ASCII and spaces.

    Anything else (a tab, a character outside ASCII) the standard library
    would refuse, or garble, only once the request is under way.
    """
    if not isinstance(user_agent, str) or not user_agent.strip() or not _printable(user_agent) \
            or len(user_agent) > MAX_USER_AGENT:
        raise ValueError(f"user_agent must be one non-empty line of visible ASCII characters and spaces, at most "
                         f"{MAX_USER_AGENT} long, not {user_agent!r}")
    return user_agent


def check_timeout(timeout: float) -> float:
    """``timeout`` if a poll may wait that long for the answer: more than 0 and at most ``MAX_TIMEOUT`` seconds."""
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) \
            or not 0 < timeout <= MAX_TIMEOUT:
        raise ValueError(f"timeout must be a positive number of seconds, at most {MAX_TIMEOUT:g} (a poll holds the "
                         f"store's lock while it waits), not {timeout!r}")
    return timeout


def check_min_interval(min_interval: dt.timedelta) -> dt.timedelta:
    """``min_interval`` if it is a duration from 0 to ``MAX_MIN_INTERVAL``."""
    if not isinstance(min_interval, dt.timedelta) or not dt.timedelta(0) <= min_interval <= MAX_MIN_INTERVAL:
        raise ValueError(f"min_interval must be a duration from 0 to {MAX_MIN_INTERVAL.total_seconds() / 3600:g} "
                         f"hours (the feed's 10 items can span a single day), not {min_interval!r}")
    return min_interval


def _minutes(delta: dt.timedelta) -> str:
    return f"{delta.total_seconds() / 60:.0f} min"


def _read_polls(paths: StorePaths) -> list[dict[str, str]]:
    return [{c: r[c] for c in POLL_COLUMNS} for r in _read_csv(paths.polls, POLL_COLUMNS, "store poll log")]


def _commit_state(paths: StorePaths, state: dict, polls: list[dict[str, str]]) -> list[str]:
    """Write ``state.json`` and ``polls.csv`` together (a commit of their own)."""
    tx = _Transaction(paths)
    tx.put(paths.state, _state_bytes(state))
    if polls or paths.polls.exists():
        tx.put(paths.polls, _csv_bytes(_frame(polls, POLL_COLUMNS)))
    return tx.commit()


def poll(store: str | Path, *, now: dt.datetime, url: str = FEED_URL,
         min_interval: dt.timedelta = DEFAULT_MIN_INTERVAL, transport: Transport | None = None,
         user_agent: str = DEFAULT_USER_AGENT, timeout: float = 60.0, keep_raw: bool = True,
         limits: SanityLimits = DEFAULT_LIMITS, calendar: BusinessCalendar | None = None,
         resume: bool = False) -> PollResult:
    """At most one GET of the feed, and an ingest of its items if they changed (see the module docstring).

    ``now`` (timezone-aware) is when the poll runs: it decides "too soon" and
    the cool-down, and dates what is stored. ``transport`` sends the request
    (default ``urllib_transport``); tests pass a fake one. ``resume`` is the
    operator's decision to end a stop or a cool-down after a block (the
    minimum interval still holds).
    """
    now = _check_now(now)
    check_url(url)  # every argument checked before anything is recorded
    check_min_interval(min_interval)
    check_user_agent(user_agent)
    check_timeout(timeout)
    paths = StorePaths(Path(store))
    with _locked(paths, now, "poll"):
        state = _check_state(paths)  # every value checked, and
        stored = _load(paths)  # the store read: a store that cannot take the answer costs no request
        polls = _read_polls(paths)
        poller = state["poller"]
        until = _from_utc(poller.get("cooldown_until"))
        last = _from_utc(poller.get("last_attempt"))
        skew = _clock_error(now, last, until)
        if skew:
            return PollResult("clock_error", skew)
        changed = _log_interrupted(poller, polls)
        if resume and (poller.get("stopped") or until is not None):
            poller.update(stopped=None, cooldown_until=None, resumed=_utc(now))
            until, changed = None, True
        early = _no_request(poller, now, until, last, min_interval)
        if early is not None:
            if changed:
                _commit_state(paths, state, polls)
            return early
        headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip",
                   "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.9, */*;q=0.1"}
        if poller.get("url") == url:  # validators belong to the URL that sent them
            if poller.get("etag"):
                headers["If-None-Match"] = poller["etag"]
            if poller.get("last_modified"):
                headers["If-Modified-Since"] = poller["last_modified"]
        poller.update(url=url, last_attempt=_utc(now), last_outcome="requesting", last_http_status=None,
                      last_message="request sent; no answer recorded")
        _commit_state(paths, state, polls)  # recorded first: a crash during the request still counts
        response = (transport if transport is not None else urllib_transport)(url, headers, timeout)
        try:
            result, tx = _answer(paths, stored, state, response, now=now, keep_raw=keep_raw, limits=limits,
                                 calendar=calendar)
        except Exception as exc:  # recorded, so that the next poll knows what this request got
            message = f"error after the answer (HTTP {response.status}): {type(exc).__name__}: {exc}"
            poller.update(last_outcome="error", last_http_status=response.status, last_message=message)
            polls.append(_poll_row(now, url, response, PollResult("error", message), poller))
            _commit_state(paths, state, polls)
            raise
        poller.update(last_outcome=result.outcome, last_http_status=response.status, last_message=result.message)
        polls.append(_poll_row(now, url, response, result, poller))
        tx = tx if tx is not None else _Transaction(paths)
        tx.put(paths.state, _state_bytes(state))
        tx.put(paths.polls, _csv_bytes(_frame(polls, POLL_COLUMNS)))
        written = tx.commit()
        if result.report is not None:
            result.report.written = written
        return result


def _log_interrupted(poller: dict, polls: list[dict[str, str]]) -> bool:
    """Log the previous attempt in ``polls.csv`` if it never recorded its answer (the run ended during it)."""
    if poller.get("last_outcome") != "requesting" or not poller.get("last_attempt"):
        return False
    message = "no answer recorded: the run ended during the request"
    polls.append({"time": poller["last_attempt"], "url": poller.get("url") or "", "outcome": "interrupted",
                  "message": message})
    poller.update(last_outcome="interrupted", last_message=message)
    return True


def _no_request(poller: dict, now: dt.datetime, until: dt.datetime | None, last: dt.datetime | None,
                min_interval: dt.timedelta) -> PollResult | None:
    """Why this poll sends nothing (stopped, cooling down, too soon), or ``None`` if it may send its request."""
    if poller.get("stopped"):
        return PollResult("stopped", f"stopped after {_n(poller.get('blocks') or 0, 'block')} in a row (the last: "
                                     f"{poller.get('last_block_reason') or 'a block'} at {poller.get('last_block')}):"
                                     " no request until the poller is resumed; check the site in a browser first, "
                                     "then run rbi-store-poll --resume")
    if until is not None and now < until:
        return PollResult("cooling_down", f"cooling down after {poller.get('last_block_reason', 'a block')} at "
                                          f"{poller.get('last_block')}: no request before "
                                          f"{poller['cooldown_until']}")
    if last is not None and now - last < min_interval:
        return PollResult("too_soon", f"too soon: the last request was at {poller['last_attempt']} "
                                      f"({_minutes(now - last)} ago); the minimum interval is "
                                      f"{_minutes(min_interval)}")
    return None


def _poll_row(now: dt.datetime, url: str, response: FeedResponse, result: PollResult,
              poller: dict) -> dict[str, str]:
    """One line of ``polls.csv`` for a request and what came of it."""
    report = result.report
    return {"time": _utc(now), "url": url, "http_status": "" if response.status is None else str(response.status),
            "outcome": result.outcome, "bytes": str(len(response.body)),
            "items": "" if result.feed_items is None else str(result.feed_items), "ids": _span(result.feed_ids),
            "new": _span(report.new) if report else "", "missed": _span(result.missed),
            "failed": _span(r for r, _, _ in report.failed) if report else "",
            "unreadable": str(len(result.unreadable)) if result.feed_items is not None else "",
            "cooldown_until": poller.get("cooldown_until") or "" if result.outcome == "blocked" else "",
            "message": result.message}


def _clock_error(now: dt.datetime, last: dt.datetime | None, until: dt.datetime | None) -> str:
    """Why the recorded times cannot be right for ``now`` (a clock moved back after a request), or nothing.

    Left alone, such a state would keep every poll "too soon" or "cooling
    down", quietly, for as long as the clock was wrong.
    """
    if last is not None and last - now > CLOCK_SLACK:
        return (f"clock error: the last request is recorded at {_utc(last)}, after now ({_utc(now)}); no request. "
                "Check the machine's clock, then remove last_attempt from state.json")
    if until is not None and until - now > MAX_COOLDOWN + CLOCK_SLACK:
        hours = MAX_COOLDOWN.total_seconds() / 3600
        return (f"clock error: the cool-down runs until {_utc(until)}, more than {hours:g} h after now "
                f"({_utc(now)}); no request. Check the machine's clock, then remove cooldown_until from state.json")
    return ""


def _decoded(response: FeedResponse) -> bytes:
    body = response.body
    if len(body) > MAX_FEED_BYTES:
        raise ValueError(f"the answer is larger than {MAX_FEED_BYTES} bytes")
    encoding = response.headers.get("content-encoding", "").strip().lower()
    if encoding in ("", "identity"):
        return body
    if encoding in ("gzip", "x-gzip"):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(body)) as f:
                data = f.read(MAX_FEED_BYTES + 1)
        except (OSError, EOFError) as exc:
            raise ValueError(f"the gzip body cannot be read ({exc})") from None
        if len(data) > MAX_FEED_BYTES:
            raise ValueError(f"the answer is larger than {MAX_FEED_BYTES} bytes once decompressed")
        return data
    raise ValueError(f"content-encoding {encoding!r} cannot be decoded (only gzip is asked for)")


def _resume_time(now: dt.datetime) -> dt.datetime:
    """08:00 India time on the day after ``now``'s India day: the first time a poll may ask after a block."""
    india = now.astimezone(IST)
    return dt.datetime.combine(india.date() + dt.timedelta(days=1), dt.time(RESUME_HOUR), tzinfo=IST)


def _block(poller: dict, now: dt.datetime, why: str, status: int | None) -> PollResult:
    """Record a block: no request for the rest of the India day, and a stop after a second block in a row."""
    blocks = (poller.get("blocks") or 0) + 1
    poller.update(blocks=blocks, last_block=_utc(now), last_block_reason=why, html_answers=0)
    if blocks >= STOP_AFTER_BLOCKS:
        poller.update(stopped=_utc(now), cooldown_until=None)
        return PollResult("blocked", f"blocked: {why}; {blocks} blocks in a row, so the poller has stopped: no "
                                     "request until it is resumed (check the site in a browser first, then run "
                                     "rbi-store-poll --resume)", requested=True, http_status=status)
    until = _resume_time(now)
    poller.update(cooldown_until=_utc(until))
    return PollResult("blocked", f"blocked: {why}; no request before {_utc(until)} (08:00 India time the next day); "
                                 "a block of the next request stops the poller", requested=True, http_status=status)


def _floor(ids: list[int]) -> int:
    """Where the span of ids polling received begins: the lowest id of the unbroken run at the top of the first feed.

    The feed holds the latest releases. An id it lacks below its highest one
    was published before its oldest item, or never: not what polling missed.
    Nor is anything below such a hole, even an id the feed holds (published
    out of order, after the ids of the hole), so the span starts above it.
    """
    held = set(ids)
    floor = max(held)
    while floor - 1 in held:
        floor -= 1
    return floor


def _answer(paths: StorePaths, stored: _Store, state: dict, response: FeedResponse, *, now: dt.datetime,
            keep_raw: bool, limits: SanityLimits,
            calendar: BusinessCalendar | None) -> tuple[PollResult, _Transaction | None]:
    """Classify the answer to a poll, update the poller state, and stage the ingest of new items."""
    poller, status = state["poller"], response.status

    def failed(outcome: str, message: str, html: int = 0) -> tuple[PollResult, None]:
        poller.update(errors=(poller.get("errors") or 0) + 1, html_answers=html)
        return PollResult(outcome, message, requested=True, http_status=status), None

    if status is None:
        return failed("network_error", f"network error: {response.error or 'no answer'}; the next poll tries again")
    if status in BLOCK_STATUSES:
        return _block(poller, now, f"HTTP {status}", status), None
    if 500 <= status <= 599:
        return failed("server_error", f"server error: HTTP {status}; the next poll tries again")
    if status == 304:
        poller.update(last_success=_utc(now), blocks=0, errors=0, html_answers=0, cooldown_until=None)
        return PollResult("not_modified", "not modified (HTTP 304): nothing new", requested=True,
                          http_status=status), None
    if status != 200:
        where = response.headers.get("location")
        detail = f" to {where}, not followed" if where and 300 <= status < 400 else ""
        return failed("http_error", f"HTTP {status}{detail}; the next poll tries again")
    try:
        body = _decoded(response)
    except ValueError as exc:
        return failed("bad_feed", f"not a readable feed: {exc}")
    if not is_feed(body):
        page = parse_html(body.decode("utf-8-sig", errors="replace"))
        try:
            check_not_blocked(page, "the answer")
        except BlockedPageError as exc:
            return _block(poller, now, f"a refusal page in place of the feed ({exc})", status), None
        pages = (poller.get("html_answers") or 0) + 1
        what = page.title or page.text[:60] or "an empty page"
        if pages >= 2:  # a web page twice in a row where the feed should be: a refusal of a kind not known here
            return _block(poller, now, f"a web page in place of the feed, {pages} answers in a row ({what!r})",
                          status), None
        return failed("bad_feed", f"not a readable feed: the answer is a web page, not an RSS feed ({what!r}); "
                                  "another one in a row counts as a block", html=pages)
    try:
        feed = read_rss_feed(body, name="the answer")
        if not feed.items:
            raise ValueError("the feed has no items")
    except ValueError as exc:
        return failed("bad_feed", f"not a readable feed: {exc}")
    poller.update(last_success=_utc(now), blocks=0, errors=0, html_answers=0, cooldown_until=None, stopped=None,
                  etag=response.headers.get("etag"), last_modified=response.headers.get("last-modified"))
    unreadable = [{"item": f"#{i.index}", "title": i.title[:120], "reason": f"no release id (prid=) in the link "
                                                                             f"{i.link!r}"}
                  for i in feed.items if i.release_id is None]
    unreadable += [{"item": str(i.release_id), "title": i.title[:120], "reason": i.pubdate_problem}
                   for i in feed.items if i.release_id is not None and i.pubdate_problem]
    poller["unreadable"] = unreadable
    ids = sorted({i.release_id for i in feed.items if i.release_id is not None})
    items = [[str(i.release_id) if i.release_id is not None else f"#{i.index}", _feed_item_hash(i)[:16]]
             for i in feed.items]
    common = dict(requested=True, http_status=status, feed_items=len(feed.items), feed_ids=ids,
                  unreadable=unreadable)
    attention = [f"feed item {u['item']} ({u['title']!r}): {u['reason']}" for u in unreadable]
    if state.get("derived"):
        attention.append(f"derived tables not rebuilt since {state['derived'].get('since')}: "
                         f"{state['derived']['error']} (the previous ones are kept)")
    without_id = sum(1 for i in feed.items if i.release_id is None)
    raw = [(_raw_name(now, _sha(body)), body)]
    if not ids:  # nothing can be stored; the raw copy is kept for a reader that knows the new links
        poller["errors"] = (poller.get("errors") or 0) + 1
        report, tx = _ingest(paths, raw, now=now, keep_raw=keep_raw, master_bytes=None, limits=limits,
                             calendar=calendar, store=stored, state=state)
        kept = "; its raw copy is kept" if keep_raw else ""
        return PollResult("bad_feed", f"not a readable feed: none of its {_n(len(feed.items), 'item')} has a release "
                                      f"id (prid=) in its link (have RBI's links changed?); nothing stored{kept}",
                          report=report, attention=attention, **common), tx
    tail = f"; {without_id} without a release id" if without_id else ""
    if len(unreadable) > without_id:
        tail += f"; {len(unreadable) - without_id} with a pubDate not read"
    if items == poller.get("items"):
        return PollResult("unchanged", f"unchanged: the feed holds the same {_n(len(items), 'item')} as at "
                                       f"{poller.get('last_new') or 'the last poll'}{tail}", attention=attention,
                          **common), None
    previous = [int(k) for k, _ in poller.get("items") or [] if not k.startswith("#")]
    if poller.get("first_id") is None:
        poller["first_id"] = _floor(ids)
    poller["last_id"] = max(poller.get("last_id") or 0, ids[-1])
    poller.update(items=items, last_new=_utc(now))
    report, tx = _ingest(paths, raw, now=now, keep_raw=keep_raw, master_bytes=None, limits=limits,
                         calendar=calendar, store=stored, state=state)
    feed_ids = set(ids)
    # ids above the previous poll's highest that neither this feed nor the store holds: the feed may have overflowed
    missed = [i for i in range(max(previous) + 1, ids[-1]) if i not in feed_ids and i not in stored.index] \
        if previous else []
    message = (f"new: {_n(len(report.new), 'new release')}" + (f" ({_span(report.new)})" if report.new else "")
               + f", {len(report.revised)} revised, {len(report.repaired)} repaired, {len(report.failed)} failed; "
               f"{_n(len(items), 'item')} in the feed{tail}")
    if missed:
        message += (f"; possible gap: ids {_span(missed)} were never seen (more than {len(items)} releases since "
                    "the last poll?)")
        attention.append(f"ids {_span(missed)} were never seen: find them on RBI's press-release listing and save "
                         "those that matter (data/README.md)")
    attention += [f"release {rid} ({kind}) did not parse: {reason}" for rid, kind, reason in report.failed]
    if report.derive_error and not any(a.startswith("derived tables") for a in attention):
        attention.append(f"derived tables not rebuilt: {report.derive_error} (the previous ones are kept)")
    return PollResult("new", message, missed=missed, report=report, attention=attention, **common), tx


# --- Status ------------------------------------------------------------------------------------------

_COVERS = {MMO: ("as_on_date", "as_on_date"), GSEC_CALENDAR: ("week_start", "week_end"),
           SDL_CALENDAR: ("quarter_start", "quarter_end")}
_GAP_WORDS = {"release_id": "release ids never seen",
              "listed": "releases known only from a listing (a kind with a parser: no data read)",
              "mmo_day": "days without Money Market Operations"}


def _shown_ranges(gaps: list[dict[str, str]], most: int = 6) -> str:
    """The first ``most`` ranges of ``gaps`` as ``a to b`` (or ``a``), and ``...`` if there are more."""
    shown = ", ".join(g["first"] if g["first"] == g["last"] else f"{g['first']} to {g['last']}" for g in gaps[:most])
    return shown + (" ..." if len(gaps) > most else "")


@dataclass
class StoreStatus:
    """What a store holds: releases by status and kind, rows and dates covered, gaps, failures, the poller."""

    root: Path
    releases: dict[str, int]
    kinds: list[dict]
    gaps: list[dict[str, str]]
    failed: list[dict[str, str]]
    revisions: int
    poller: dict
    first_id: int | None
    last_id: int | None
    last_ingest: str
    lock: str | None = None
    interrupted: bool = False
    derived_error: dict | None = None
    polls: list[dict[str, str]] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = [f"store {self.root}"]
        if self.lock:
            out.append(f"locked: {self.lock}")
        if self.interrupted:
            out.append("an interrupted commit waits in journal.json: the next rbi-store command completes it (for "
                       "example rbi-store-ingest with no files); until then the figures below may be incomplete")
        total = sum(self.releases.values())
        by_status = ", ".join(f"{n} {s}" for s, n in self.releases.items() if n)
        span = f"; ids {self.first_id} to {self.last_id}" if total else ""
        out.append(f"releases: {total} ({by_status or 'none'}){span}; last change {self.last_ingest or 'never'}")
        for k in self.kinds:
            dates = f", {k['first']} to {k['last']}" if k["first"] else ""
            out.append(f"  {k['kind']}: {_n(k['releases'], 'release')} ({k['statuses']}), {_n(k['rows'], 'row')}"
                       f"{dates}")
        out.append(f"revisions: {self.revisions}")
        if self.derived_error:
            out.append(f"derived tables: not rebuilt since {self.derived_error.get('since')}: "
                       f"{self.derived_error.get('error')} (the previous ones are kept; every ingest tries again)")
        for gap in ("release_id", "listed", "mmo_day"):
            found = [g for g in self.gaps if g["gap"] == gap]
            if not found and gap == "listed":
                continue
            inside = [g for g in found if g["in_feed_span"] == "True"]
            what = _GAP_WORDS[gap]
            if not found:
                out.append(f"possible gaps, {what}: none")
                continue
            if inside:  # what polling missed: these need a look
                tail = (f"{len(inside)} ranges ({sum(int(g['count']) for g in inside)}) inside the span polls "
                        f"received: {_shown_ranges(inside)}")
            elif self.poller.get("first_id") is not None:
                tail = f"none inside the span polls received; outside it: {_shown_ranges(found)}"
            else:
                tail = f"no poll has received ids yet: {_shown_ranges(found)}"
            out.append(f"possible gaps, {what}: {len(found)} ranges, {sum(int(g['count']) for g in found)} in all; "
                       f"{tail}")
        for f in self.failed:
            out.append(f"failed: {f['release_id']} {f['kind']} ({f['title']}): {f['reason']}")
        p = self.poller
        if not p:
            out.append("poller: never run")
        else:
            out.append(f"poller: last request {p.get('last_attempt')}: {p.get('last_outcome')}"
                       f"{' (HTTP ' + str(p['last_http_status']) + ')' if p.get('last_http_status') else ''}; "
                       f"last good answer {p.get('last_success') or 'never'}; last new items "
                       f"{p.get('last_new') or 'never'}")
            if p.get("errors"):  # a network, server or HTTP error, or an answer that was not a readable feed
                out.append(f"poller: {_n(int(p['errors']), 'request')} in a row without a usable answer since the last "
                           f"good one ({p.get('last_success') or 'never'}): see the daily routine in data/README.md")
            if p.get("stopped"):
                out.append(f"poller: stopped since {p['stopped']} after {_n(int(p.get('blocks') or 0), 'block')} in a "
                           f"row ({p.get('last_block_reason')}): no request until resumed; check the site in a "
                           "browser, then run rbi-store-poll --resume")
            elif p.get("cooldown_until"):
                out.append(f"poller: cool-down until {p['cooldown_until']} after {p.get('last_block_reason')} "
                           f"({_n(int(p.get('blocks') or 0), 'block')} in a row)")
            if p.get("first_id") is not None:
                out.append(f"poller: ids received by polls {p['first_id']} to {p.get('last_id')}")
            for u in p.get("unreadable") or []:
                out.append(f"poller: last feed's item {u.get('item')} ({u.get('title')!r}) not read in full: "
                           f"{u.get('reason')}")
            if p.get("last_message"):
                out.append(f"poller: {p['last_message']}")
        if self.polls:
            outcomes: dict[str, int] = defaultdict(int)
            statuses: dict[str, int] = defaultdict(int)
            for row in self.polls:
                outcomes[row["outcome"]] += 1
                statuses[row["http_status"] or "none"] += 1
            out.append(f"polls: {_n(len(self.polls), 'request')} from {self.polls[0]['time']} to "
                       f"{self.polls[-1]['time']}; outcomes "
                       + ", ".join(f"{n} {o}" for o, n in sorted(outcomes.items()))
                       + "; HTTP " + ", ".join(f"{n} {s}" for s, n in sorted(statuses.items())))
        return out


def status(store: str | Path, *, now: dt.datetime | None = None) -> StoreStatus:
    """What the store holds, read without the lock (``now`` only dates a lock's age in the report)."""
    paths = StorePaths(Path(store))
    interrupted = paths.journal.exists()
    if not interrupted and not paths.index.exists() and not paths.state.exists():
        raise StoreError(f"{paths.root}: no store here (neither releases.csv nor state.json)")
    state = _check_state(paths)
    try:
        loaded = _load(paths)
        polls = _read_polls(paths)
    except StoreError:
        if not interrupted:
            raise
        loaded, polls = _Store(paths, {}, {}, {}, []), []  # half renamed: only the interruption can be reported
    counts = {s: 0 for s in STATUSES}
    kinds: dict[str, dict] = {}
    for rid, entry in sorted(loaded.index.items()):
        counts[entry["status"]] += 1
        k = kinds.setdefault(entry["kind"], {"kind": entry["kind"], "releases": 0, "rows": 0, "by": {},
                                             "first": "", "last": ""})
        k["releases"] += 1
        k["by"][entry["status"]] = k["by"].get(entry["status"], 0) + 1
        rows = loaded.rows.get(rid, [])
        k["rows"] += len(rows)
        if rows:
            lo, hi = _COVERS.get(entry["kind"], ("auction_date" if "auction_date" in COLUMNS[entry["kind"]]
                                                 else "release_date",) * 2)
            firsts = [r[lo] for r in rows if r.get(lo)] or [entry["release_date"]]
            lasts = [r[hi] for r in rows if r.get(hi)] or [entry["release_date"]]
            k["first"] = min([d for d in (k["first"], *firsts) if d], default="")
            k["last"] = max([d for d in (k["last"], *lasts) if d], default="")
    for k in kinds.values():
        k["statuses"] = ", ".join(f"{n} {s}" for s, n in sorted(k.pop("by").items()))
    failed = [{"release_id": str(rid), "kind": e["kind"], "title": e["title"], "reason": e["reason"]}
              for rid, e in sorted(loaded.index.items()) if e["status"] == "failed"]
    times = [t for e in loaded.index.values() for t in (e["first_seen"], e["last_changed"]) if t]
    ids = sorted(loaded.index)
    return StoreStatus(paths.root, counts, [kinds[k] for k in sorted(kinds)],
                       possible_gaps(loaded.index, _poll_span(state)), failed, len(loaded.revisions),
                       state["poller"], ids[0] if ids else None, ids[-1] if ids else None, max(times, default=""),
                       _lock_message(paths, now) if paths.lock.exists() else None, interrupted,
                       state.get("derived"), polls)
