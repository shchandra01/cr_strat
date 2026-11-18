# Input data

Put downloaded files under `data/raw/` and generated files under `data/out/`.
Both directories are git-ignored. Dates are ISO (`YYYY-MM-DD`) everywhere.

## Rating actions: `rating_actions.csv`

One row per rating action. Build it from CRA press releases (CRISIL, ICRA,
CARE, India Ratings, Acuité, Infomerics, Brickwork all publish rating rationales
with the instrument table) or from rating-change disclosures that listed issuers
file with NSE/BSE. A vendor feed works too.

| Column | Required | Notes |
|---|---|---|
| `date` | yes | First **public** date of the action (press release or exchange filing), not the rating committee date. Using the committee date leaks information. |
| `issuer_id` | yes | Stable key shared with the security master. CIN is a good choice. |
| `rating` | yes | Rating string as published, e.g. `CRISIL AA+/Stable`, `[ICRA]AA (Negative)`, `Withdrawn` |
| `issuer_name` | no | |
| `agency` | no | Read from the rating string when blank |
| `instrument` | no | e.g. `Non-convertible debentures`, `Commercial paper`, `Tier II bonds`. Blank is taken to mean bonds. |
| `instrument_class` | no | Overrides the inferred class: `BOND`, `BANK_LOAN`, `ST`, `SUB`, `PERP`, `STRUCTURED`, `MLD`, `FD`, `OTHER` |
| `outlook` | no | If the outlook or watch is in a separate column |
| `action` | no | `Upgraded`, `Downgraded`, `Withdrawn`... (any text containing "withdraw" marks a withdrawal) |
| `liquidity` | no | CRA liquidity descriptor: Superior, Strong, Adequate, Stretched, Poor |
| `isin`, `source_url` | no | Carried through |

Keep withdrawals and outlook-only changes. Withdrawals change effective ratings,
and outlook/watch changes feed the `prior_signal` flag. Rows that cannot be
parsed go to `rating_parse_failures.csv`.

## MF portfolio disclosures: AMC `.xlsx` / `.xls`

Each AMC website's statutory disclosures section has monthly portfolios for all
schemes and fortnightly portfolios for debt schemes. Download one AMC's files
into one folder and run `cr-strat parse-mf` on them with `--amc` set to the AMC
name as AMFI spells it. `.xls` files need the `xls` extra (`xlrd`).

If a layout hides the date or value unit, pass `--as-of` or `--mv-unit`. Check
`<out>_report.csv` after every run.

## AMFI scheme master: `NAVAll.txt`

`https://www.amfiindia.com/spages/NAVAll.txt` lists every live scheme under its
SEBI category heading. Pass it as `--navall` to attach categories. Schemes it
cannot match land in `<out>_scheme_matches.csv` with `match = none`. Map those
in a CSV of `amc, scheme_name, category` and pass it as `--scheme-overrides`.
Closed or merged schemes are missing from the current file, so older periods
need overrides or an archived copy.

## Security master: `securities.csv`

| Column | Required | Notes |
|---|---|---|
| `isin` | yes | |
| `issuer_id` | yes | Same key as the rating actions |
| `instrument_class` | yes | Same vocabulary as above; plain NCDs are `BOND` |
| `face_value_inr` | no | Per unit. Needed to value holdings at face (quantity × face value); otherwise market value is used. Note the denomination changed over time (₹10 lakh, ₹1 lakh, ₹10,000). |
| `outstanding_face_inr` | no | Needed for ownership shares |
| `issue_date`, `maturity_date` | no | Used to drop ISINs not live at the event or maturing within a horizon. `maturity_date` is required for the price panel (tenor and spread). |
| `sector` | no | e.g. PSU, NBFC, HFC, bank, corporate. Add `sector` to `--match-on` to match peers on it. |

Sources: NSDL ISIN search, the information memorandum or key information
document filed with NSE/BSE, or a vendor (Bloomberg, LSEG, CMIE Prowess).

## Other holders: same CSV format as `parse-mf` output

`as_of_date, holder_type, holder_id, isin` plus optional `category, quantity,
market_value_inr`. Use `holder_type` `INSURER`, `NPS` or `EPFO` to match
`config/mandates.toml`. Insurers' quarterly public disclosures include a
statement of downgraded investments. NPS pension fund managers publish monthly
scheme portfolios.

## Reported trades: NSE / BSE files

OTC corporate bond trades are reported on the NSE and BSE reporting platforms,
and both exchanges publish them daily, as individual trades or as ISIN-day
summaries. Download each venue's files into its own folder and run
`cr-strat load-trades` per venue.

The loader finds the header row by its ISIN and price/yield columns, and prefers
weighted-average columns when a summary file has several. It reads the value
unit from the value header ("Rs. Lakhs", "Rs. Cr"). If a file has no date
column, pass `--trade-date`. If the value header has no unit, pass
`--amount-unit`. The `_report.csv` lists the header it mapped to each field;
check it on the first file from each venue.

Prices are per 100 of face value and yields are in percent, as reported.

## Benchmark curve: `gsec_par_curve.csv` (or an OIS curve)

Long format, one row per date and tenor:

| Column | Notes |
|---|---|
| `date` | ISO date |
| `tenor_years` | e.g. 0.25, 0.5, 1, 2, 3, 5, 7, 10, 15, 30 |
| `yield_pct` | Percent |

FBIL publishes daily G-sec par yields; an OIS curve can come from a vendor or
dealer marks. A curve date is used for up to 7 days. Set `--benchmark-compounding`
to the curve's convention (G-sec yields are semi-annual) and `--bond-compounding`
to the trade yields' convention. Spreads are computed on the bond's basis.

## RBI press releases: saved pages and `pressreleases_rss.xml`

RBI publishes every auction and liquidity operation as a press release: GoI
dated securities, T-bills and SDLs (announcement, then full result),
underwriting and switch results, the daily Money Market Operations, variable
rate repo and reverse repo auctions, open market operations and the issuance
calendars. Save a release from
`https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=<id>`
(browser "save page", HTML only), or save the
[press-release RSS feed](https://www.rbi.org.in/pressreleases_rss.xml), which
holds the 10 latest releases with their full tables; put the date and time in
each copy's name. The site rate-limits (HTTP 418): a few requests, well apart.
RBI writes the feed's `pubDate` as `Mon, 16 Nov 2026 19:25:00`, India time
with no zone; that form, or the same with an explicit zone (`GMT`, `+0530`,
...), is read. Any other form gives no publication time and a note, rather
than a time that could be hours off; so does a numeric zone no place uses
(minutes above 59, more than 14 hours east or 12 west of UTC: `+0560`,
`+2400`). The note is on that item only: the feed's other items are read.

```bash
cr-strat rbi-releases data/raw/rbi/releases/*.html data/raw/rbi/feeds/*.xml --out-dir data/out/rbi
```

This writes one CSV per release kind (below) and `releases.csv`, which lists
every release read with its kind, status (`parsed`, `skipped`, `duplicate`),
the reason and any notes. Running again replaces a release's rows, so
overlapping feed copies add no duplicates. A row's key is
`(release_id, line)`. Copies of one release (a saved page and a feed item)
are tried in a fixed order, whatever the order of the files: saved pages
first, then by file name. A copy that fails is skipped with its reason and
the next one is tried; the rows keep the feed copy's `published` time, also
when a later run reads the saved page alone.

| File | One row per |
|---|---|
| `gsec_result.csv`, `tbill_result.csv`, `sdl_result.csv` | Security in a full auction result |
| `underwriting_result.csv`, `switch_result.csv` | Security underwritten; pair of securities switched |
| `gsec_announcement.csv`, `tbill_announcement.csv`, `sdl_announcement.csv` | Line of an auction announcement |
| `money_market_operations.csv` | Printed line of the daily Money Market Operations release |
| `laf_auction_announcement.csv`, `laf_auction_result.csv` | Variable rate repo or reverse repo auction |
| `omo_announcement.csv`, `omo_result.csv` | Security in an open market purchase or sale |
| `gsec_calendar.csv` | Allocation line (week and tenor) of the half-yearly GoI issuance calendar |
| `sdl_calendar.csv` | Quarter of the indicative calendar of state borrowings |

Columns the tables share:

| Column | Notes |
|---|---|
| `release_id` | RBI's `prid`; from the page's form action, else from a 4-7 digit number in the file name |
| `release_date`, `published` | The page's `Date :` line; `published` (feed only) is `pubDate`, India time: when the market was told |
| `auction_date`, `settlement_date` | As printed: LAF and OMO results take the auction date from their title, SDL results from "held on"; a GoI or T-bill full result prints none, so it is the release date |
| `security`, `name_key` | Name as printed, and the key `sovereign.parse_security_name` gives it for joins |
| `*_cr`, `*_count` | ₹ crore; numbers of bids or offers |
| `cutoff_price`, `cutoff_yield`, `wap`, `way`, `*_pct` | Per ₹100 face value; percent |
| `total_accepted_cr` | Competitive plus non-competitive (SDL: the printed total allotment) |
| `greenshoe_cr` | From the GoI announcement for that auction, if read in this or an earlier run |
| `liquidity_effect_cr` | Liquidity put into the banking system, RBI's sign: plus for a repo, MSF, SLF or OMO purchase, minus for a reverse repo, SDF or OMO sale |

Amounts are face value in the auction, OMO and calendar tables and cash in
the money-market and liquidity tables. An empty cell is a blank ("-", "NA" or
nothing printed), never a zero; a printed `0.00` is 0.

**`money_market_operations.csv`.** One release for every calendar day,
published on the next Mumbai business day; after a weekend three arrive in
one morning. `as_on_date` is the day the figures refer to (from the title);
`release_date` and `published` are the publication. `item` names the printed
line, `block` its table (`money_market`, `rbi_operations`,
`reserve_position`), `label` and `marks` the label as printed and the
footnote marks at its end (a changed mark is how RBI signals a changed
definition).

| `item` | Fields |
|---|---|
| `overnight.total`, `overnight.call_money`, `overnight.triparty_repo`, `overnight.market_repo`, `overnight.corporate_bond_repo` | `amount_cr`: volume, one leg; `rate_pct`: weighted average rate; `rate_low_pct`, `rate_high_pct`: the range. The total is the sum of the four parts |
| `term.notice_money`, `term.term_money`, `term.triparty_repo`, `term.market_repo`, `term.corporate_bond_repo` | The same; term money prints a volume and a range but no rate |
| `laf.today.repo`, `laf.today.reverse_repo`, `laf.today.msf`, `laf.today.sdf` | One row per operation of the day (`seq` counts them): `auction_date`, `tenor_days`, `maturity_date`, `amount_cr`, `rate_pct` (the facility's rate or the auction's cut-off), `liquidity_effect_cr` |
| `laf.outstanding.repo`, `laf.outstanding.reverse_repo`, `laf.outstanding.msf`, `laf.outstanding.sdf` | The same for earlier operations that have not matured |
| `net.today`, `net.outstanding`, `net.total` | Lines 5, E and F, signed as printed. They total the rows above: never add them to those rows |
| `slf.availed` | Line D: `amount_cr`, `liquidity_effect_cr` |
| `reserves.cash_balances`, `reserves.crr_requirement`, `government.surplus_cash_balance`, `liquidity.net_durable` | `amount_cr` and `ref_date`, each line's own date as printed. Only the cash balances are always dated the as-on day (a note says when they are not); the other lines may be dated that day or another |

The policy corridor of the day is `rate_pct` of `laf.today.sdf` and
`laf.today.msf`; the weighted average call rate is `rate_pct` of
`overnight.call_money`. On a weekend the money-market lines print `0.00` and
no rate, and the standing facilities still have rows: keep those days and
never fill rates forward from them. A facility line with nothing printed
gives no row. The sum of `liquidity_effect_cr` over a release is its line F.

**Liquidity auctions.** `laf_auction_announcement.csv`: `direction` (`repo`
injects, `reverse_repo` absorbs), `auction_date`, `tenor_days`,
`notified_cr`, `window_start` and `window_end` (`HH:MM`, India time),
`reversal_date`. `laf_auction_result.csv`: `notified_cr`, `received_cr`,
`accepted_cr`, `cutoff_rate_pct`, `wa_rate_pct`, `partial_pct`,
`liquidity_effect_cr`. The result's cut-off, amount and tenor reappear in
that day's `laf.today.*` row.

**Open market operations.** `omo_announcement.csv`: `operation` (`purchase`
or `sale`), `auction_date`, `settlement_date`, and per security `isin`,
`security`, `maturity_date`; `aggregate_notified_cr` is one amount for the
whole auction, repeated on every row; `programme_pr_no` and `programme_date`
name the earlier release that announced the programme. `omo_result.csv`: per
security `offers_received_count`, `offered_cr`, `offers_accepted_count`,
`accepted_cr`, `cutoff_yield`, `cutoff_price`, `way`, `wap`,
`partial_allotment_pct`, with the auction's `aggregate_notified_cr`,
`total_offered_cr` and `total_accepted_cr` repeated. The result prints no
ISIN: the announcement lists the same securities in the same order.

**Calendars.** `gsec_calendar.csv`: `period_start`, `period_end`, and per
allocation line `week_no`, `week_start`, `week_end` (Monday to Friday),
`week_amount_cr`, `allocation_no`, `tenor_years`, `security_type` (`GS`, or
`SGrB` for a green bond), `amount_cr`; `total_cr` is the Total row. It is the
calendar as first published: revisions come as later releases.
`sdl_calendar.csv`: `quarter_start`, `quarter_end`, `total_cr` and
`annex_urls`. The per-state amounts are in two PDFs on rbidocs.rbi.org.in,
whose addresses are kept for a person to open in a browser; nothing here
fetches them.

Pages and feed items share one parser. A release whose labels do not match
a layout that was sampled, or whose numbers fail a check, is listed in
`releases.csv` with the reason, never half-read: yields 0-20%, prices
50-150, accepted not above received or notified plus greenshoe, totals that
add up; for Money Market Operations the overnight total and the three net
liquidity lines must equal the rows they total, every maturity date must be
the auction date plus the tenor, and a filled "Fixed Rate" line is refused
(no sample had one, so its sign is unknown), as is a column no sample had.
Softer findings are `notes` in `releases.csv` and leave the rows in place: a
row or table that no parser knows (a row with its cells), an unknown column
of the other tables, changed footnote marks, a rate outside its range, a
release not published on the next business day, a `pubDate` that could not
be read (the release then has no publication time). Read the notes after
every run. Four layouts were never sampled and are read from descriptions of
such releases: repo (injection) auction results and announcements, and OMO
sale results and announcements. One is read only if its labels fit those
descriptions, is refused otherwise, and always carries a note saying so.

Recognised but not parsed (the title is known; the layout was not sampled,
or the full result follows the same day): cut-off quick results of GoI,
T-bill, SDL and OMO auctions, underwriting and switch announcements, buyback
announcements and results, the T-bill auction calendar, MPC resolutions and
USD/INR swap auctions. `releases.csv` still gives each one's kind and
publication time. Any other title is skipped as not recognised.

Two notes use the Mumbai holiday list, which ends in 2026 (the publication
day of Money Market Operations, the reversal day of an announced auction).
For a date in 2027 or later the check is skipped and the note says so, until
that year's holidays are added to `src/cr_strat/data/holidays_mumbai.csv`
and the year is declared complete in `holidays_mumbai_years.csv`.

RBI grants no reuse licence, and its disclaimer prohibits caching without
permission. Ask RBI in writing before keeping a history of its releases
([`docs/rates_data_sources.md`](../docs/rates_data_sources.md); a draft
letter is in [`docs/data_enquiries.md`](../docs/data_enquiries.md#3-rbi)).

## RBI operations store: `cr-strat rbi-store-poll`, `rbi-store-ingest`, `rbi-store-status`

RBI's feed holds only the 10 latest releases, so a history has to be
collected as it goes by. A store is a directory that keeps every release
the poller or an ingest has seen, parsed with the parsers above, and
rebuilds five daily tables from them for funding (S1), the supply and
operations cycle (S4) and the policy path (S7).

**Terms first.** RBI grants no reuse licence, and its disclaimer prohibits
caching and framing of its site's contents without permission. A store, its
tables, its index and above all its `raw/` copies may well be such a cache;
whether that clause reaches a private research history is for RBI to say.
Ask RBI in writing before keeping one: a draft letter is in
[`docs/data_enquiries.md`](../docs/data_enquiries.md#3-rbi), the terms as
read in [`docs/rates_data_sources.md`](../docs/rates_data_sources.md).
`--no-raw` keeps the parsed tables without the copies, at a cost (below).

```bash
cr-strat rbi-store-poll --store data/rbi_store                  # at most one request; for cron
cr-strat rbi-store-ingest --store data/rbi_store data/raw/rbi/releases/*.html data/raw/rbi/feeds/*.xml
cr-strat rbi-store-ingest --store data/rbi_store --master data/out/sovereign_master.csv
cr-strat rbi-store-ingest --store data/rbi_store --policy-rates data/raw/policy_rates.csv
cr-strat rbi-store-status --store data/rbi_store
```

The directory is created on first use. `rbi-store-ingest` takes saved pages
(a release, or RBI's press-release listing) and feed copies, as
`rbi-releases` does; with no files it only rebuilds the derived tables
(after `--master` or `--policy-rates`, or after an update of this package).
`--master` copies a security master (`cr-strat sovereign-master`) into the
store, `--policy-rates` a table of policy rates kept by hand (below); every
later ingest uses them.

| Path | Contents |
|---|---|
| `tables/<kind>.csv` | Parsed rows, one file per release kind, in the format `rbi-releases` writes (columns above): `cr-strat auctions --releases data/rbi_store/tables` reads them |
| `releases.csv` | One row per release id ever read, parsed or not, or seen in a saved listing |
| `revisions.csv` | Every change to a release the store already held |
| `gaps.csv` | Possible gaps (below) |
| `derived/*.csv` | Five tables rebuilt from `tables/` on every ingest (below) |
| `raw/` | Every feed and saved page read, byte for byte: `pr_feed_<UTC time>_<hash>.xml` and `page_<UTC time>_<hash>.html`, named by the time of the ingest and the first 12 hex digits of the SHA-256; bytes already kept are not saved again. A feed is about 100 to 170 kB, one per poll that finds a new item; a saved page or listing 110 to 160 kB |
| `polls.csv` | One row per request the poller sent: `time` (UTC), `url`, `http_status`, `outcome`, `bytes`, `items`, `ids` (in the feed), `new`, `missed`, `failed` (release ids), `unreadable` (items not read in full), `cooldown_until`, `message`; a run killed during its request is logged `interrupted` by the next |
| `state.json` | The poller's state (times in UTC); a failure to rebuild the derived tables |
| `sovereign_master.csv`, `policy_rates.csv` | The last `--master` and `--policy-rates` given |
| `store.lock`, `journal.json` | Present only while a command runs, or after one was killed |

Times in the CSVs are India time without a zone (`2026-11-16T09:00:00`), as
RBI's feed gives them; `polls.csv` and `state.json` are in UTC. Read a table
back typed with `rbi_store.read_table(store, name)`: dates and times as
Timestamps, blanks as missing values in every column.

**What an ingest does.** Each copy of a release (a saved page, or the item
of a feed) is parsed on its own. A release never seen is added. Every copy
read is remembered by a fingerprint (`copies` in `releases.csv`): a copy the
store has read before changes nothing, under any file name, unless it now
parses into rows the store never held (a parser fixed since), or the ingest
reads again every copy the store has of that release (below). So the same
inputs read again, in any order, leave every file byte-identical, and the
copy that first gave the rows stays their `source_file`. Different rows (RBI
corrects a release; a saved page may hold more than its feed item) replace
the stored ones and are logged in `revisions.csv`. A version the store has
held before is never taken back. Several new versions in one ingest are
applied in a fixed order (feed copies by publication time and file name,
then saved pages by file name; bytes passed without a name are named by
their content) and the last one stays. A release that fails to parse is
listed as `failed` with the reason. It is parsed when a copy that parses
arrives, or when a kept raw copy is read again after a parser fix (below);
a new copy that fails after the release was stored is logged once as
`refused` and the rows are kept. A parser that raises an unexpected error
fails that release only. A saved listing adds the ids, titles and dates it
shows as `listed` (no data; a copy read later takes the entry's place).
Every write goes to a temporary file first and a command is committed
through `journal.json`: the store holds all of it or none of it, and the
next command finishes an interrupted commit. If a derived table cannot be
rebuilt (a bug), the previous derived tables are kept, the error is recorded
in `state.json` and shown by `rbi-store-status`, the rest is committed, and
every later ingest tries again.

`releases.csv`:

| Column | Notes |
|---|---|
| `release_id`, `kind`, `title`, `release_date`, `press_release_no` | As `rbi-releases` reads them (for a `listed` release: the listing's title and date heading) |
| `status` | `parsed`, `failed` (its parser refused every copy), `no_parser` (a kind recognised but not parsed), `not_recognised` (a title no kind takes), `listed` (seen only in a saved listing) |
| `published` | The earliest feed time seen for the release; blank if it was only ever read from a saved page |
| `rows`, `reason`, `notes`, `classified_by` | Rows stored; why there are none; the parser's notes |
| `source_file` | The copy the rows came from (`<feed>#<item>` for a feed item) |
| `rows_hash` | Fingerprint of the stored rows (every column but `published` and `source_file`) |
| `first_seen`, `last_changed` | When the store first read (or listed) the release, and last changed its rows (blank if never) |
| `copies` | Fingerprints of every copy of the release read so far |

`revisions.csv`: `seen`, `release_id`, `kind`, `change` (`rows`, `repaired`,
`published`, `refused`), `detail` (the first 8 cells that changed and how
many more, or the reason of a refusal), `old_hash`, `new_hash` (for
`refused`, the hash of the refused copy) and `source_file`. The tables hold
the latest version only; the earlier one is in `raw/`, if raw copies are
kept.

**Repairs and raw copies.** After a parser fix, or any update of this
package, read the kept copies again:
`cr-strat rbi-store-ingest --store data/rbi_store data/rbi_store/raw/*`.
Copies whose rows do not change leave the rows as they are; a failed release
whose copy now parses is `repaired`. A release whose copies are all read
again gets the index entry a store built from those copies would hold: a
release without rows the kind, status and reason of the last of its copies
in the order above (a saved page, if there is one), so a title now
recognised or a refusal reworded reaches it; a parsed release the notes of
its stored rows' copies (a note added or dropped). The ingest's line counts
these as `updated without new rows`; nothing goes to `revisions.csv`, and an
entry that would not change, the name of its copy apart, is left as it is.
Pages ingested by hand are kept in `raw/` too. With `--no-raw` nothing is
kept: a release the parser refuses is lost once the feed has moved on (one
to three days), unless the page was saved by hand, and its entry stays as
first written; keep saved pages wherever they are.

**Possible gaps** (`gaps.csv`: `gap`, `first`, `last`, `count`,
`in_feed_span`, rebuilt on every ingest). Release ids were consecutive in
November 2026 (63920 to 63991), and there is a Money Market Operations
release for every calendar day, published on the next Mumbai business day.
So the store lists the ids between the lowest and the highest it holds that
it has never seen (`gap` `release_id`), the ids known only from a saved
listing whose kind has a parser (`listed`: the data are missing), and the
days between the first and the last Money Market Operations day it holds
that have no release (`mmo_day`). They are possible gaps: whether ids are
always consecutive is not known, and RBI publishes a few ids out of time
order. `in_feed_span` is `True` for a range inside the span of ids the
poller has received, from where the unbroken run of ids at the top of its
first feed begins (`first_id` in `state.json`) to the highest id any poll
received: what polling missed. The first feed holds the latest releases, so
an id it lacks below its highest one was published before polling began (or
never), and so was anything below such a hole, even an id that feed holds
(published out of order). Ranges below the span are that history, or pages
and feeds ingested by hand, and are expected. `rbi-store-status` lists the
ranges inside the span, or says there are none and lists those outside it.
`rbi-store-poll` also names, in its line, the ids above the previous poll's
highest that neither the new feed nor the store holds (the feed overflowed,
or ids were skipped); a first poll names none. To fill a gap by hand: open RBI's
[press-release listing](https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx)
in a browser (it shows about twelve days), save it, save the releases that
matter as pages (`BS_PressReleaseDisplay.aspx?prid=<id>`), a few a day and
well apart, and run `rbi-store-ingest` on all of them. The listing records
the ids, titles and dates it shows, which closes the gaps of releases that
do not matter; a listed release of a kind with a parser stays a `listed`
gap until its page is read.

**Derived tables** (`derived/`). Every row carries `public_from`: the time
from which every value in the row was public, the latest time of the
releases it was built from. A release's time is its feed time if the store
ever saw it in a feed, else 23:59:59 on its release date
(`public_from_source`, of that latest part: `feed` or `release_date`); blank
if one part's time is unknown. `revised` is when the store last saw the rows
of one of those releases change. A back-test asking what was known at time
`t` keeps the rows with `public_from <= t`. A row of
`liquidity_operations.csv` or `omo_operations.csv` is joined from several
releases: `first_public_from` is its earliest part, and each part keeps its
own time (`announcement_public_from`, `result_public_from`,
`mmo_public_from`). From `first_public_from` to `public_from` the row was
known in part: use only the fields of the parts public by then (the
announcement's amount, tenor and window before the result's cut-off, say).
A revised row holds the latest version, which may have become public later
than `public_from` (RBI does not say when it corrects a release).
`release_notes` holds the notes `releases.csv` has for the releases a row
was built from (`<release id>: <notes>`, several separated by ` | `): a
changed footnote mark, a layout read from descriptions, a `pubDate` not
read. `notes` holds the derivation's own.

| File | One row per | Columns |
|---|---|---|
| `money_market_daily.csv` | As-on date of Money Market Operations | `as_on_date`; `business_day`, `quarter_end_day` and `fy_end_day` (the last business day of a quarter, of March), from the Mumbai settlement calendar, blank in a year its holiday list does not cover; for `overnight` (the total), `call`, `triparty`, `market_repo`, `corp_bond_repo`, `notice`, `term_money`, `term_triparty`, `term_market_repo`, `term_corp_bond_repo`: `<segment>_volume_cr`, `_rate_pct` (weighted average), `_low_pct`, `_high_pct`; today's `sdf_cr`, `sdf_rate_pct`, `msf_cr`, `msf_rate_pct`, `vrr_cr`, `vrr_rate_pct` (variable rate repo), `vrrr_cr`, `vrrr_rate_pct` (reverse repo), each summed over its rows with one rate (the amount-weighted mean, noted, if the rows differ); `net_today_cr`, `slf_cr`, `net_outstanding_cr`, `net_total_cr`; `cash_balances_cr`, `crr_requirement_cr`, `gov_surplus_cash_cr`, `net_durable_liquidity_cr`, each with its own `_date`; `marks_changed` (the lines whose footnote marks differ from those of Nov 2026); `outstanding_check`; `repo_rate_pct` and `policy_check` (from the policy rates kept by hand); `release_id`, `release_date` |
| `liquidity_operations.csv` | Variable rate repo or reverse repo auction | `operation`, `direction`, `auction_date`, `tenor_days`, `reversal_date`; the announcement's `announcement_id`, `announcement_public_from`, `notified_cr`, `window_start`, `window_end`; the result's `result_id`, `result_public_from`, `received_cr`, `accepted_cr`, `cutoff_rate_pct`, `wa_rate_pct`, `partial_pct`, `liquidity_effect_cr`; the Money Market Operations of the auction day: `mmo_release_id`, `mmo_public_from`, `mmo_amount_cr`, `mmo_rate_pct` and `mmo_check` (`agrees`, `differs` with a note, `missing` from that release, `ambiguous`, `no_result`); `first_public_from` |
| `omo_operations.csv` | Security in an open market operation | `operation`, `auction_date`, `settlement_date`, `line`, `security`, `name_key`, `isin` with `isin_source` (`announcement` or `master`) and `isin_match` (the stored master's `match_isin` status), `maturity_date`, the aggregate figures, method and programme, the security's offers, amounts, cut-off and weighted average yield and price, `liquidity_effect_cr`, both releases' ids and times, `first_public_from` |
| `supply_calendar.csv` | Planned or announced auction line | `source` (`gsec_calendar`, `sdl_calendar`, `gsec_announcement`, `tbill_announcement`, `sdl_announcement`), `kind` (`gsec`, `green`, `tbill`, `sdl`), `period_start`, `period_end` (the auction week, the quarter, or the auction day), `auction_date`, `settlement_date`, `security`, `name_key`, `state_code`, `tenor_years` (a calendar line's, a new issue's), `tenor_days`, `tenor_bucket`, `maturity_date`, `amount_cr`, `greenshoe_cr`, `week_no`, `allocation_no`, `release_id`, `line` |
| `switch_operations.csv` | Pair of securities in a switch result | `auction_date`, `line`, `source_security`, `source_key`, `source_isin`, `source_maturity_date`, `destination_security`, `destination_key`, `destination_isin`, `destination_maturity_date` (ISINs and maturities from the stored security master), `notified_cr`, offers, `source_offered_cr`, `source_accepted_cr` (bought back), `destination_issued_cr`, `dest_cutoff_price`, `dest_cutoff_yield`, `release_id`, `release_date` |

All five have `public_from`, `public_from_source`, `revised`,
`release_notes` and `notes`. An announcement without a result, a result
without an announcement, and an auction seen only in Money Market
Operations are rows of `liquidity_operations.csv` too. A result checked
against a Money Market Operations release counts that release among its
parts, also when the auction is `missing` from it.

`tenor_bucket` in `supply_calendar.csv` is one of the buckets
`supply_monthly.csv` uses, given by one rule to a calendar line and to the
auctions held under it. A calendar line names a nominal tenor of N years,
not a security: it goes in the bucket that holds N with its lower bound left
out (3 years in `1-3y`, 5 in `3-5y`, 10 in `5-10y`, 15 in `10-15y`, 30 in
`15-30y`, 40 in `30y+`). An announced new issue goes in the bucket of its
tenor by the same rule: an SDL's as printed, a GoI security's (`New GS
2036`) the years from settlement to repayment, rounded (in `tenor_years`;
noted). A reissue has less than N years to run and is bucketed by its
residual maturity on the settlement date, which falls in the same bucket; an
SDL prints no maturity, so a reissue matures on the anniversary of its first
issue (the date its tenor cell prints; noted). `supply_monthly.csv` buckets
every issue by residual maturity on 365.25-day years, so there a new N-year
issue whose life spans more than N/4 leap days has a little more than N
years and lands one bucket up: for 3 and 15 years on 3 settlement days in 4,
for 10 and 30 years on 2 in 4, for 5 years on 1 in 4 (a new 10-year bond
settling on 27 Nov 2026 has 10.0014 years and is in `10-15y` there, `5-10y`
here). The row's note gives that other bucket where it differs.

`outstanding_check` in `money_market_daily.csv` compares the day's
outstanding operations with the earlier days' operations still running
(auction day < as-on day < maturity), using only releases public before
this one (spec 1.8): `differs` with a note naming the line, the auction day
and both amounts (or a missing or extra operation); `agrees` when every
release of the days whose operations could still run is stored and all
agree; blank otherwise.

**Policy rates by hand.** MPC resolutions are prose and not parsed. Keep the
rates in a CSV of `effective_date, repo_pct, sdf_pct, msf_pct, source` (one
row per change; `source` says where the row comes from, for example the
resolution's release id) and pass it with `--policy-rates`; a row with
SDF > repo or repo > MSF, a rate outside 0 to 20 or a date listed twice is
refused, naming the file and the line. `money_market_daily.csv` then has
the repo rate in force on each day (`repo_rate_pct`) and `policy_check`:
`agrees` when the day's SDF and MSF rates are the table's, `differs` with a
note otherwise (a change missing from the table, or one RBI applied on
another day), blank where no row applies. A reverse repo cut-off above, or
a repo cut-off below, the repo rate in force is noted in
`liquidity_operations.csv`. The history is only what is typed in: nothing
here reads RBI's policy pages.

**Polling.** `rbi-store-poll` makes at most one request: none if the last
one, answered or not, was less than `--min-interval` minutes ago (default
25), none during a cool-down or after a stop. The store and every value of
`state.json` are read and checked before anything is sent, so a damaged
store or state costs no request. The attempt is recorded before the request
is sent, and the answer in `polls.csv`. It asks conditionally
(`If-None-Match`, `If-Modified-Since`: the feed sends an ETag and a
Last-Modified) but decides "new" from the ordered list of the items' ids
and contents, because the server rewrites the file without new items: an
unchanged feed is not ingested and not saved again. The client name it
sends is generic (`--user-agent` changes it; nothing in a request names you
or the firm), no redirect is followed, and RBI's attachment hosts are
refused.

Poll every 30 minutes from 08:30 to 21:00 India time, every day, and once at
23:30: 27 requests a day of 11 to 22 kB compressed (100 to 170 kB of XML).
Why: a release is lost only if more than 10 appear between two polls. In
November 2026 a publishing day had about 9 releases and 13 at most; the 17
whose time is known came between 09:00 and 19:25, never more than 4 in two
hours; the feed's 10 items spanned one to three days. Hourly polling would
have lost nothing on the days seen; 30 minutes leaves room for a missed poll
or a burst. A policy day or a day of many enforcement orders may still bring
more than 10 releases between two polls: that is what the gap checks are
for. Weekend polls cost little. Schedule it on your own machine, for
example with cron. Cron runs a job with a short `PATH` (often
`/usr/bin:/bin`), so give the full path of `cr-strat` (`command -v cr-strat`
shows it; in a virtual environment it is `<venv>/bin/cr-strat`) or set
`PATH` in the crontab, and set `HTTPS_PROXY` there too if this machine
reaches the web through a proxy. `CRON_TZ` sets the zone of the times in
cronie and some other crons (see `man 5 crontab`); where it is not
supported, write the times in the machine's own zone (in UTC: 03:00 to
15:30, and 18:00):

```
CRON_TZ=Asia/Kolkata
PATH=/path/to/venv/bin:/usr/bin:/bin
30 8      * * *  cd /path/to/data && cr-strat rbi-store-poll --store rbi_store >> rbi_store_poll.log 2>&1
0,30 9-20 * * *  cd /path/to/data && cr-strat rbi-store-poll --store rbi_store >> rbi_store_poll.log 2>&1
0 21      * * *  cd /path/to/data && cr-strat rbi-store-poll --store rbi_store >> rbi_store_poll.log 2>&1
30 23     * * *  cd /path/to/data && cr-strat rbi-store-poll --store rbi_store >> rbi_store_poll.log 2>&1
```

Each run prints one line, and a line per thing that needs a look. Exit
codes: `0` for a normal outcome (new items, nothing new, not modified, too
soon); `4` when the poll worked but stored or met something a person should
look at (a release that did not parse, ids possibly missed since the
previous poll, feed items without a release id or with a `pubDate` not read,
derived tables not rebuilt), with `needs a look:` lines on stderr; `3` when
blocked, cooling down or stopped; `1` for any other error, whose line says
`error:` (network, a 5xx or other HTTP status, a redirect, an answer that is
not a readable feed, a feed none of whose items has a release id, a store
locked by another run or damaged, a damaged `state.json`, a clock that moved
back: a last request recorded in the future makes no request and says so);
`2` for bad arguments, checked before anything is recorded: `--min-interval`
from 1 to 1440 minutes, `--timeout` above 0 and up to 600 seconds (a run
holds the store's lock while it waits), a `--user-agent` of one line of
visible ASCII, a `--url` without spaces whose port, if it names one, is from
1 to 65535.

**Every morning, check the store**, before the feed's one to three days run
out: `cr-strat rbi-store-status --store rbi_store` (or mail it from cron
with `MAILTO`), or `grep -E 'needs a look|error|blocked|cooling|stopped'
rbi_store_poll.log`, which finds every run that did not exit 0. What each
line needs:

| Line | What to do |
|---|---|
| `blocked`, `cooling down`, `stopped` (status: `poller: stopped`, `poller: cool-down`) | Check the site in a browser (below) |
| `error: store ... is locked` | A stale lock: remove it (below) |
| `error: HTTP 301 to ...` or another HTTP status (`http_error`) | The feed may have moved: open its address in a browser. If RBI moved it, pass the new address with `--url` in the crontab once you have checked it is RBI's feed; until then every poll says the same |
| `error: not a readable feed` (`bad_feed`) | Open the feed in a browser. Once (a maintenance page, an answer cut short) needs nothing: the next poll tries again, and a second web page in a row counts as a block. If every poll says it, RBI changed the feed's format: this package needs an update |
| `error: network error`, `error: server error` | Nothing once: the next poll tries again. If they go on (status: `poller: N requests in a row without a usable answer since the last good one`), check this machine's network and proxy, then the site in a browser; after a day the feed has moved on and the gaps need filling |
| `error: clock error` | Check the machine's clock, then remove from `state.json` the time the line names |
| other `error:` lines (a damaged store or `state.json`, a bug) | Read the message; restore the store from its backup, or report the bug |
| `needs a look:` a release that did not parse (status: `failed:`) | A saved page of it, or a parser fix and then `raw/` read again (above) |
| `needs a look:` ids never seen (status: `possible gaps ... inside the span polls received`) | A saved listing and the pages that matter (above) |
| `needs a look:` a feed item without a release id, or with a `pubDate` not read | Look at the item in the feed: RBI may have changed its links or its dates |
| `derived tables: not rebuilt` | An update of this package, or a bug report |

The press-release listing in a browser reaches back about twelve days, so a
gap found within the week can still be filled. Back up the store directory
(it is plain files; copy it while no `rbi-store-*` command runs, for example
right after a poll): what the feed showed cannot be collected again.

**When RBI blocks.** HTTP 418, 403 or 429, a firewall page ("Unauthorised
Access ... Support ID"), a bot challenge (a page of script that asks for
JavaScript) or a second web page in a row in place of the feed is a block.
Nothing from it is stored as data. RBI blocks by Internet address, so a
block the poller causes blocks every browser behind the same address too.
The poller makes no further request that India day: the next comes at 08:00
India time the next day at the earliest. If that request is blocked again,
the poller stops and asks nothing until a person resumes it: check the site
in a browser first, then run `cr-strat rbi-store-poll --store rbi_store
--resume` by hand (never from cron), which ends the stop or cool-down and
makes one request if the minimum interval has passed; a block of it stops
the poller again at once. A good answer resets the count. RBI does not say
what its throttle counts or for how long it blocks (a block on 17 Nov 2026
cleared within minutes, others lasted longer). A 5xx or a network error is
recorded and the next scheduled poll tries again.

**A stale lock.** A run that was killed leaves `store.lock`; the next run
refuses to start, says who held it, since when, and whether that process
still runs, and never deletes it. If no `rbi-store-*` command is running,
delete the file; the next command completes or drops the interrupted commit.

## RBI security lists: GoI outstanding, FAR, SDLs

Save these pages from www.rbi.org.in by hand:

| Page | Loader | Notes |
|---|---|---|
| [GoI securities outstanding](https://www.rbi.org.in/scripts/bs_viewcontent.aspx?Id=1956) | `sovereign.load_goi_outstanding` | Re-dated daily and lists live securities only: save a copy each day. Refused unless it has a Total row and the rows sum to it |
| [FAR specified securities](https://www.rbi.org.in/Scripts/bs_viewcontent.aspx?Id=3876) | `sovereign.load_far_list` | Outstanding and matured FAR securities; the page has no date |
| [SDLs outstanding](https://www.rbi.org.in/scripts/bs_viewcontent.aspx?Id=4433) | `sovereign.read_sdl_outstanding_index` | The page holds only the as-of date and links to an XLS and a PDF on rbidocs.rbi.org.in, which serves a bot challenge. No SDL rows |
| [STRIPS outstanding](https://www.rbi.org.in/scripts/bs_viewcontent.aspx?Id=3973) | none | The saved copy is a firewall refusal ("Unauthorised Access ... Support ID"); such pages are refused |

```bash
cr-strat sovereign-master --goi-list data/raw/rbi/goi_outstanding_*.html --far data/raw/rbi/far.html \
    --snapshots data/out/goi_outstanding.csv --out data/out/sovereign_master.csv
```

`--snapshots` keeps every list read, one block of rows per as-of date
(`as_of, sl_no, isin, name, issue_date, maturity_date, outstanding_cr,
maturity_fy, source_file`); reading a date again replaces its block.
`--extra` adds other listings as CSVs of `isin, name` and optional
`issue_date, maturity_date, source`, e.g. SDL ISINs from FIMMDA's SDL file.

The master has one row per ISIN: `name` (the latest list's), `name_key`,
`kind`, `coupon_pct`, `maturity_year`, `maturity_date`, `issue_date` (first
issue), `state_code`, `far`, `far_name`, `in_latest_list`, `outstanding_cr`
and `outstanding_as_of` (latest snapshot holding it), `first_listed`,
`last_listed`, `matured`, `isin_fy` (characters 5-8, the financial year of
first issue), `isin_fy_matches`, `sources` and `conflicts` (dates or names
that disagree between sources). A row with no ISIN or an invalid one is
refused, naming its file and line. Matured securities appear only if a
stored snapshot or the FAR list holds them.

RBI's terms apply as above: ask RBI in writing before keeping these
snapshots as a history.

## FIMMDA auction files: G-sec yearly workbooks and the SDL workbook

FIMMDA's [auction data page](https://www.fimmda.org/PageContent.aspx?Iid=MTQ3)
links one workbook per financial year of central government auctions
("Central Government Auction Data: 2024-25" and so on) and the latest SDL
file ("State Development Loans Auction Data: Latest Month"). Download them by
hand, once each; FIMMDA states no terms, so ask before keeping a history.
Links commented out of the page are dead.

Each G-sec workbook repeats the older years' sheets, so pass all of them:
rows are merged on auction date and security, and copies that disagree are
listed, not dropped. File and sheet names do not say what a file holds (the
2024-25 file holds FY2025-26 rows; the 2026-27 file's sheet "2025-2026" holds
FY2026-27), so each row's financial year comes from its auction date. In
the files saved on 17 Nov 2026 no file holds FY2024-25.

"Amount Accepted" is the competitive amount from FY2025-26 and the total
accepted before; neither includes devolvement on primary dealers. Rows with
a non-integer notified amount are conversions or switches listed as auction
lines; they and the short security converted out that day do not count as
issuance (`non_round_notified`, `probable_switch_source`). Nor do the lines
of a day whose prices fit only settlement on or before the auction date (29
Sep 2016: `auction_day_pricing`). None of these lines is used to infer a
maturity, since their settlement basis is not T+1.

The SDL workbook holds one sheet per month (ISIN, description, maturity
date, weighted average yield, NEW/REISSUE, amount accepted; no prices).
Sheets that repeat an older month with the year moved (the 11 Feb 2025
file's "February 2025" and "March 2025") are found and dropped.

## RBI Handbook of Statistics, Table 221

"Details of Central Government Market Borrowings": one financial year or so
of GoI auctions per edition, with the date of issue (settlement) and the
non-competitive amounts FIMMDA lacks. Open the
[Handbook](https://www.rbi.org.in/Scripts/AnnualPublications.aspx?head=Handbook%20of%20Statistics%20on%20Indian%20Economy),
find Table 221 and save its HTML page; the XLSX and PDF sit on rbidocs,
behind a bot challenge. Amounts are rounded to the crore.

## Auction table and issuance ledger: `cr-strat auctions`

```bash
cr-strat auctions --fimmda-gsec data/raw/fimmda/Gsec_Auction_data_*.xlsx \
    --fimmda-sdl data/raw/fimmda/SDL_Auction_Data_*.xlsx --handbook data/raw/rbi/hbs_t221_*.html \
    --releases data/out/rbi --master data/out/sovereign_master.csv \
    --snapshots data/out/goi_outstanding.csv --out-dir data/out/ledger
```

`--releases` is the directory `rbi-releases` writes; `--master` and
`--snapshots` are the files `sovereign-master` writes. Every input is
optional. The command writes:

| File | Contents |
|---|---|
| `auctions.csv` | One row per security per auction, all sources merged (below) |
| `switches.csv` | RBI switch results with ISINs for both legs and a settlement date checked by the destination's yield |
| `ledger.csv` | One row per security per event: `issue`, `reissue`, `switch_in`, `switch_out`, `buyback`, `maturity`, signed ₹ crore of face value |
| `ledger_coverage.csv` | The periods the inputs cover, per kind; gaps longer than 75 days are not covered |
| `supply_monthly.csv` | Gross issuance, switches, buybacks, redemptions and net supply by month, kind and tenor bucket |
| `settlement_evidence.csv` | What each auction says about the settlement calendar |
| `reconciliation.csv` | With `--snapshots`: the ledger against RBI's latest list, ISIN by ISIN |
| `validation.csv` | Every check: severity, auction, detail and source row |

Main columns of `auctions.csv`:

| Column | Notes |
|---|---|
| `auction_date`, `settlement_date`, `settlement_source` | Settlement from an RBI announcement, the Handbook's date of issue, a new issue's first issue date in RBI's list, else T+1 on the Mumbai calendar (`calendar_t1`), or the yield (`yield`, `yield_rolled`) |
| `kind`, `security`, `name_key`, `coupon_pct`, `maturity_year`, `state_code`, `tenor_days` | The name as printed and its parsed fields |
| `isin`, `isin_match` | `matched`, `matched_by_yield` (one of two ISINs reproduces the yield), `printed` (FIMMDA's SDL file), `ambiguous`, `none` (not in the master) |
| `maturity_date`, `maturity_source` | `master`, `name`, `fimmda_sdl`, `announcement`, `tenor`, or inferred: `issue_anniversary`, `yield_search` |
| `notified_cr`, `comp_accepted_cr`, `nc_accepted_cr`, `accepted_cr`, `accepted_basis` | `accepted_cr` is the amount issued: `total`, `competitive_only` or `as_printed` |
| `cutoff_price_text`, `cutoff_yield_text` | As printed, so their decimals are known |
| `issue_type`, `issue_type_source` | `new` or `reissue`, and how that was decided |
| `repricing`, `repriced_yield`, `repricing_note`, `settlement_candidates` | The yield from the cut-off price at the settlement date against the printed yield: `reproduced`, `settlement_inferred`, `par_issue`, `not_reproduced` (with the likely cause), `not_checked` |
| `counts_as_issue`, `flags` | `greenshoe`, `shortfall`, `non_round_notified`, `probable_switch_source`, `auction_day_pricing`, `bad_price_yield`, `state_conflict`... |

`--shortfall devolved` (the default) books the notified amount for an
auction that accepted far less, as devolved on the underwriters;
`accepted` books FIMMDA's figure. `--competitive-only notified` (the
default) books the notified amount for a GoI auction known only by its
competitive amount (FIMMDA's FY2025-26 rows with no Handbook line) when that
amount is 95% to 100% of the notified one: non-competitive bids took the
rest (`notified_assumed_full`; in all 90 accepted Handbook auctions of
FY2025-27 the two parts add up to the notified amount within ₹1 crore).
`accepted` books the competitive amount. `--operations-from DATE` declares
that every switch and buyback release from that date is in `--releases`;
without it no switch or buyback history is complete.

A ledger row's `date_source` says how it was dated. `assumed_t1`: the
auction table gave no settlement date, so T+1 of the auction is used.
`name_year`: a security with no known maturity date (a floating-rate bond,
or a maturity the yield search refused) is redeemed on the last business day
of the year in its name, a placeholder; its `maturity_date` stays blank. A
maturity after the inputs' last date (the latest auction or RBI list) is
noted `scheduled`: later issuance is not known.

## Outstanding stock: `cr-strat outstanding`

```bash
cr-strat outstanding --ledger data/out/ledger/ledger.csv --date 2024-03-31 \
    --snapshot data/out/goi_outstanding.csv --by-bucket --out data/out/outstanding_20240331.csv
```

The stock of every security at the end of the date: reconstructed from the
nearest RBI snapshot where one holds the ISIN (backward: snapshot minus later
events), else summed forward from the ledger. A security is zero from its
redemption day (`matured`, or `matured_date_unknown` after a `name_year`
placeholder). `issuance_complete`, `operations_complete` and `reasons` say
whether every event in between is in the inputs.

RBI grants no reuse licence and FIMMDA states no terms: ask both in writing
before keeping the auction table, the ledger or the snapshots as a history.
