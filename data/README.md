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
| `auction_date`, `settlement_date` | As printed; an auction result page has no auction date, so it is the release date (SDLs: "held on") |
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
| `reserves.cash_balances`, `reserves.crr_requirement`, `government.surplus_cash_balance`, `liquidity.net_durable` | `amount_cr` and `ref_date`, each line's own date (only the cash balances are as on the release's day) |

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

Pages and feed items share one parser. A release whose labels do not match,
whose numbers fail a check, or whose layout was never sampled is listed in
`releases.csv` with the reason, never half-read: yields 0-20%, prices
50-150, accepted not above received or notified plus greenshoe, totals that
add up; for Money Market Operations the overnight total and the three net
liquidity lines must equal the rows they total, every maturity date must be
the auction date plus the tenor, and a filled "Fixed Rate" line is refused
(no sample had one, so its sign is unknown), as is a column no sample had.
Softer findings are `notes` in `releases.csv` and leave the rows in place: a
row or table that no parser knows (a row with its cells), an unknown column
of the other tables, changed footnote marks, a rate outside its range, a
release not published on the next business day. Read the notes after every
run. The parsers of repo (injection) auctions and OMO sales were
written from descriptions, not samples: such a release is read only if its
labels fit and always carries a note saying so.

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

RBI grants no reuse licence and its disclaimer prohibits caching without
permission. Ask RBI in writing before keeping a history of its releases
([`docs/rates_data_sources.md`](../docs/rates_data_sources.md)).

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
