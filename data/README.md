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

Prices are per 100 of face value and yields are in percent, as reported. A
header that names a type next to the yield or the deal ("Yield Type", "Deal
Type", as in SEBI's reporting format) is kept as `yield_type` (YTM, YTC, YTP)
or `deal_type` (Direct, Brokered, IST), never read as the yield itself; the
daily rows leave inter-scheme transfers (IST) out, as the mutual-fund master
circular leaves them out of its weighted average yield, and `trade-check`
reads only the workouts a declared yield type names. `trade-check` and
`price-panel --yield-source recomputed` read a partly redeemed bond's prints
per 100 of the face still outstanding (`--price-basis outstanding`,
UNVERIFIED: exchange part-redemption notices say trading continues on the
reduced face value, read only through a search summary; `original` for the
other reading), and prices as clean (a print that matches only as a dirty
price is flagged `dirty_price`).

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

## Analytics inputs: prices, OIS quotes, positions, haircuts, fixings, MPC dates

`cr-strat curve-fit`, `ois-curve`, `asset-swaps`, `repo-carry` and
`policy-path` read files you prepare. Their layouts, with the defaults and
the conventions each command assumes, are in
[`docs/analytics.md`](../docs/analytics.md) (each part's "What it does"):

| File | Read by | Main columns |
|---|---|---|
| Price file (one day) | `curve-fit --prices` | `isin`, `clean_price` or `yield_pct`; optional `kind`, `coupon_pct`, `maturity_date`, `weight`, `flags`, `traded`, `trade_count`, `volume_cr` |
| Residual history | `curve-fit --history`, written by `--append-history` | `HISTORY_COLUMNS` of `curvefit`; a file fitted with other settings is refused (an `--anchor` at each day's own rate counts as the same settings) |
| Issuance ledger | `curve-fit --ledger` (reissues and stock beside each residual) | the `cr-strat auctions` ledger, below |
| OIS quotes (one day) | `ois-curve`, `asset-swaps`, `policy-path --quotes` | `tenor`, `rate_pct`, or the `fbil-mm --kind miborois` output |
| Bonds and positions | `asset-swaps --bonds`, `repo-carry --bonds` | `isin` (each once), `clean_price` or `yield_pct`; positions add `face`, `liquidity` (CCIL's class), `side`, `special_repo_pct` |
| Haircut table (kept by you) | `repo-carry --haircuts` | `effective_date`, `isin` or `kind`, `min_years`, `max_years`, `liquidity`, `haircut_pct`, `source` |
| Overnight MIBOR fixings | `repo-carry --fixings` | `date` (each once), `rate_pct`, optional `public_from` (a blank cell, like a file without the column: FBIL's publication time that day) |
| MPC dates | shipped as `src/cr_strat/data/mpc_meetings.csv`; `policy-path --meetings` for your own | `meeting_start`, `decision_date`, `fy`, `kind`, `announced`, `source`, `confirmed` |
| Policy rates | `policy-path --policy-rates`, `repo-carry --policy-rates` | the RBI store's policy-rate table, or the macro store's `policy_rates_bis` (on a decision day, a change it dates the next business day, as BIS dates some, makes `policy-path` ask for `--policy-rate`) |

CCIL's haircut list and liquidity classes are restricted: type your own table
from what your clearing member gives you. FBIL's MIBOR and MIBOR-OIS need a
subscription before they are kept as a history
([`docs/rates_data_sources.md`](../docs/rates_data_sources.md)).

## Risk inputs: `cr-strat risk` and `cr-strat var-backtest`

`cr-strat risk` reads the return engine's own outputs (`positions.csv`, `nav.csv`, `funding.csv` of
`cr-strat returns`), the market files the engine reads (`--nss-params` with `public_from`: today's curve and the
history the VaR reads; `--ois-quotes`, `--fixings`, `--marks` with `volume_cr`; the corporate and FRB tables above)
and tables you keep. Every table is read as of `--as-of`: a row public later is left out. Layouts, units and what
each number means: [`docs/risk.md`](../docs/risk.md).

| File (kept by you) | Read by | Main columns |
|---|---|---|
| Issuers | `risk --issuers` | `issuer_id`, `name`, `group_id`, `group_name`, `sector` (FRTB's names: `financials`, `basic_materials_energy_industrials`, `consumer_transport`, `technology_telecoms`, `health_care_utilities`, `covered`; any other counts as FRTB's `other`, its largest weight, in `FRTB_CSR`), `seniority` (`senior`, `subordinated`, `covered`), `effective_from`, `effective_to`, `source` |
| Book to strategy | `risk --strategy-books` | `book`, `strategy`, `effective_from` (default: each book its own strategy) |
| Seniority by ISIN | `risk --seniority` | `isin`, `seniority`, `source` (else the issuer's, else the terms' tier, else senior) |
| Factor levels | `risk --factor-history` | `date`, `factor` (`corp:<rating bucket>:<tenor bucket>`, `cd:<tenor bucket>`, `sdl_spread:<tenor bucket>`, `frb_dm:<isin>`, `tbill182`, `resid:<isin>`), `value`, `unit` (`bp` or `pct`), `public_from`, `source` (a source naming `coarse` flags the factor's VaR) |
| Proxies | `risk --proxies` | `factor` (one without enough history), `proxy` (one with it), `scale`, `source` |
| Average daily volumes | `risk --adv` | `date`, `isin` (or blank with `kind` and `liquidity` for a class), `adv_cr` (Rs crore a day), `public_from`, `source` |
| Peer spread matrix | `risk --spread-matrix` | `credit_spreads.read_spread_matrix`'s layout: `rating_bucket` (optionally `instrument_class`, `sector`, `tenor_bucket`), `tenor_years`, `spread_bp`: the spreads a rating migration moves to (else the rating ladder's step) |
| Scenarios | `risk --scenarios` (with `--no-default-scenarios`: yours only) | `stress.SCENARIO_COLUMNS`: `scenario_id`, `name`, `family`, `component`, `selector`, `shape`, `size_bp`, `tenor_years`, `multiplier`, `horizon_days`, `floor_pct`, `available_from`, `effective_from`, `source`, `status`, `notes`; a row replaces the shipped scenario of the same id (`src/cr_strat/data/stress_scenarios.csv`) |
| VaR history | written by `risk --var-history`, read by `var-backtest --var-history` | `risk.VAR_HISTORY_COLUMNS`: one row per day, scope, method, horizon and level |

`var-backtest` also reads the engine's `nav.csv` (the actual P&L) and `attribution.csv` (the hypothetical P&L).
Spread and curve histories come from licensed prices (the exchanges, agency valuations, CCIL's NDS-OM data): keep
them under their terms; the free auction table is only a coarse stand-in ([`docs/risk.md`](../docs/risk.md)).

## Corporate bond and FRB terms: `cr-strat corp-price`, `frb-price` and the commands that use them

Typed in by you from term sheets or, for listed ISINs from 18 Aug 2025,
SEBI's centralised corporate bond database, or from the depositories' security
data (NSDL's India Bond Info, under its terms: `docs/data_enquiries.md` letter
10); none is shipped except the GoI FRB files. All are CSV with ISO dates and
one row per key: a key given twice with different values is refused, an exact
repeat kept once, blank rows and blank unnamed columns dropped, ISINs read in
capitals (`corporates.read_term_table`). Only the columns listed below are
read: any other column is refused, naming the closest listed one (a misspelt
`redemption_pct` would otherwise leave the bond redeeming at 100). The terms
table may also carry `rating_bucket`, which the return engine's costs read. The rules each column switches are in
[`docs/conventions.md`](../docs/conventions.md#corporate-bonds-and-floating-rate-bonds).

| File | Key | Columns (required in bold) |
|---|---|---|
| `corporate_terms.csv` | isin | **isin**, issuer_id, name, **kind** (`corporate`, `perpetual`, `frb_corp`), instrument_class, **coupon_pct**, **frequency** (0, 1, 2, 4, 12), schedule (`forward`, `backward`, `month_start`), first_period (`separate`, `clubbed`), **issue_date** (deemed allotment), maturity_date (blank only for a perpetual: 100 years), first_coupon_date, redemption_per100, face_value_rs, conventions (`ncd`, `ncd_2013`, `ncd_pre2013`; blank: by the issue date), record_days, record_roll, leap_year_rule, postponed_coupon, redemption_interest, flow_dates, tier (`at1`, `tier2`), sector, source, status |
| `corporate_schedule.csv` | isin, due_date, kind | **isin**, **due_date**, payment_date, record_date, kind (`coupon`, `principal`, `redemption`), amount_per100 (checked against the rules: a difference above 1e-6 per 100 is refused), source. Rows of a bond that are all `principal` amortise its generated schedule (as `corporate_amortisation.csv`); otherwise the rows are its whole schedule and must list every due date, one period of `frequency` apart (a first or last stub allowed, 3 days' slack), with payment and record dates in order and each record date before its payment date |
| `corporate_options.csv` | isin, kind, date | **isin**, **kind** (`call`, `put`), **date**, price_per100 (100), notice_days, portion (1), exercised (`yes`, `no`, blank), exercised_public_from (needed with exercised; a `no` too counts only from then), record_date (the record date the exercise notice sets; without it, 15 days before the date by the bond's rule, and a notice public after that is refused), source |
| `corporate_amortisation.csv` | isin, date | **isin**, **date** (a due date), **principal_per100**, basis (`face`, `quantity`), source |
| `corporate_steps.csv` | isin | **isin**, **threshold** (a grade such as `AA`), **step_bp_per_notch**, max_step_bp, agencies (`;` list), combine (`worst`, `best`, `second_best`), effective (`next_period`, `from_event`), step_down, source |
| `corporate_floating.csv` | isin | **isin**, **benchmark**, **spread_pct**, **reset_months**, fixing_lag_days, lookback, floor_pct, cap_pct, decimals, source |
| `fixings.csv` | benchmark, date | **benchmark**, **date**, **value_pct**, **public_from** (a bare date counts from 23:59:59), source. Keep each benchmark's every published fixing: a reset is fixed only on the fixing due on or before its fixing date (a daily benchmark's that day; a weekly one's on its schedule, every usual interval of the table from the latest fixing), so a missing row leaves it projected |
| T-bill results for `frb-price --tbills` | auction date | **auction_date**, **tenor_days**, way (or wap), cutoff_yield or cutoff_price, public_from (or the RBI store's published and release_date; else `--tbill-time` as a rule) |
| `src/cr_strat/data/goi_frb_terms.csv` (shipped) | isin | **isin**, **name**, **issue_date**, **maturity_date**, first_coupon_date, **index** (`tbill182_way_avg3`, `tbill182_cutoff_avg3`, `gsec5y_reset5y`), spread_pct, decimals, reset_months, source, status, notes |
| `src/cr_strat/data/goi_frb_coupons.csv` (shipped) | isin, period_start | **isin**, **period_start**, period_end (exclusive), **rate_pct**, announced (RBI's release time or date; blank: estimated as 00:00 on the fixing day), source, status |

Add RBI's coupon releases to `goi_frb_coupons.csv` (or a copy passed with
`--coupons`) as they come. The shipped file is thin: of the half-years begun by
19 Nov 2026 it holds FRB 2028's coupon for 2 of 11, FRB 2031's for 3 of 18, FRB
2033's for 3 of 14 and FRB 2034's for 7 of 12 (FRB 2035's two five-yearly
coupons cover 14 of its 44), none of the coupons set from June to October 2026
for the first four among them, and the RBI store holds the 182-day results
needed to compute coupons only from its first poll. A period with neither is
`unknown`, and a back-test across it is refused unless allowed to project from
an older base (`--frb-allow-stale`, flagged; a `returns` run whose trades hold
an FRB across such a period is refused before it starts). An announced time
more than 14 days before the fixing day, and a T-bill result public before its
auction, are refused as typos. `cr-strat frb-coupons --as-of T` writes every
FRB's coupon for each period as known at `T` (`floating.FRB_RATE_COLUMNS`; its
`stale_base` names the old base or proxy a rate stands on, blank when fresh;
`--projection forward` projects the T-bill FRBs forward and FRB 2035 flat).

The same tables feed `asset-swaps --terms` (corporate bonds) and `--frb-out`
(GoI FRBs), `credit-spreads --terms`, `trade-check --terms`, `price-panel
--yield-source recomputed --terms`, and `returns` (`--corporate-terms`,
`--corporate-schedule`, `--corporate-options`, `--corporate-amortisation`,
`--corporate-steps`, `--corporate-floating`, `--benchmark-fixings`, `--frb-terms`,
`--frb-coupons`, `--tbills` or `--tbills-from-store`). Two more inputs:

| File | Read by | Columns |
|---|---|---|
| Rating actions with times | `returns --ratings` (for `--corporate-steps`) | **agency**, **rating** (as `rating_scale` parses it, e.g. `CRISIL AA-/Negative`), **public_from** (each action used only from then), and **isin** or **issuer_id** (matching the terms' `issuer_id`) |
| Corporate bond prices (one day) | `credit-spreads --prices`, `corp-price --prices` | **isin**, **clean_price** or **yield_pct** (each row from whichever it has), settlement_date (default the curve's valuation date), trade_date and as_of (the time the terms are known at, default 00:00 on the trade date, else on the settlement less T+1); with `--peer-panel` also issuer_id, instrument_class, rating_bucket (`tenor_bucket` is computed). Prices of a partly redeemed bond are per 100 of the original face unless `--price-basis outstanding` |

A trade's `rating_bucket` column (returns' trades file) picks a corporate
bond's half spread in the cost table (rows HS20-HS24; the cost table's optional
`rating_bucket` column, `;` lists allowed); without it, the worst of each
agency's latest rating public before the trade day (`--ratings`), else the
terms' `rating_bucket`, noted (a rating typed in later then prices earlier
trades).

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
`error:` (network, a 5xx or other HTTP status that does not come with a
refusal page, a redirect, an answer that is not a readable feed, a feed none
of whose items has a release id, a store locked by another run or damaged, a
damaged `state.json`, a clock that moved back: a last request recorded in
the future makes no request and says so); `2` for bad arguments, checked
before anything is recorded: `--min-interval` from 1 to 1440 minutes,
`--timeout` above 0 and up to 600 seconds (a run holds the store's lock
while it waits), a `--user-agent` of one line of visible ASCII, a `--url`
without spaces whose port, if it names one, is from 1 to 65535.

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
JavaScript) or a second web page in a row in place of the feed is a block; a
firewall page or a challenge is one whatever HTTP status it comes with (a
challenge with 503, a firewall page with 404, as much as with 200). Nothing
from it is stored as data. RBI blocks by Internet address, so a block the
poller causes blocks every browser behind the same address too. The poller
makes no further request that India day: the next comes at 08:00 India time
the next day at the earliest. If that request is blocked again, the poller
stops and asks nothing until a person resumes it: check the site in a
browser first, then run `cr-strat rbi-store-poll --store rbi_store --resume`
by hand (never from cron), which ends the stop or cool-down and makes one
request if the minimum interval has passed; a block of it stops the poller
again at once. A good answer resets the count. RBI does not say what its
throttle counts or for how long it blocks (a block on 17 Nov 2026 cleared
within minutes, others lasted longer). A 5xx or a network error (without a
refusal page) is recorded and the next scheduled poll tries again.

**A stale lock.** A run that was killed leaves `store.lock`; the next run
refuses to start, says who held it, since when, and whether that process
still runs, and never deletes it. If no `rbi-store-*` command is running,
delete the file; the next command completes or drops the interrupted commit.

## Macro and global store: `cr-strat macro-store-update`, `macro-store-ingest`, `macro-store-status`

The policy path (S7), the long end (S5) and every back-test need CPI, IIP,
GDP and WPI, US yields and SOFR, Brent, the rupee and policy rates, each
with the time from which it was public. The APIs serve the latest value
only, and overwrite a first print when it is revised, so a store keeps
every value it reads, run after run: a value it has seen is never lost, a
change is a new vintage, and nothing is dated before it could have been
public as far as the release rules, the release-date table and the store's
own reads know (where that can still fail is listed below).

**Terms first.** What each publisher allows, as read on 16 to 18 Nov 2026
(re-read the terms before a bulk download):

| Source (`--sources`) | What is fetched | Terms | Default |
|---|---|---|---|
| `mospi`: MoSPI's eSankhyiki API (`api.mospi.gov.in`) | CPI (bases 2024, 2012), IIP, GDP and GVA, WPI (compiled by DPIIT's Office of the Economic Adviser) | No licence, terms of use or rate limit was found for the API; its site's legacy copyright page reportedly asks for permission by mail. Whether a commercial firm may keep a history is for MoSPI to say ([letter 12](../docs/data_enquiries.md#12-mospi)). Its `llms.txt` asks to keep the dataset name, indicator, value, unit, reference period, source and update information (`series.csv` does), and to give attribution and, where possible, a link: `series.csv`'s `citation` (a format of this package) reads "Source: Ministry of Statistics and Programme Implementation (MoSPI), Government of India, eSankhyiki (<dataset>), <URL>, retrieved <date>", for WPI "Source: Office of the Economic Adviser, DPIIT, Government of India, via MoSPI eSankhyiki (...)" | **Only when named** (`--sources mospi` or `all`), with a one-line reminder that its licence is not settled; no raw copies unless `--mospi-raw`; CPI down to groups unless `--cpi-levels items` |
| `ust`: US Treasury daily par yield curve (`home.treasury.gov`) | One CSV a year | No reuse, copyright or automated-access clause on the Site Policies and Notices page; US government data. Cite "U.S. Department of the Treasury, Daily Treasury Par Yield Curve Rates" | Off: it refused the probe of 18 Nov 2026 (HTTP 403 on another path); name it once you have opened a year's file in a browser from the same network |
| `nyfed`: New York Fed reference rates (`markets.newyorkfed.org`) | SOFR, BGCR, TGCR, EFFR, OBFR, the SOFR averages and index | A licence to access the content by an automated process and to download, store and use it. Any use of reference-rate data must carry the notice in `series.csv`'s `citation` ("The <rate> is subject to the Terms of Use posted at newyorkfed.org. The New York Fed is not responsible for publication of the <rate> by <publisher>, does not sanction or endorse any particular republication, and has no liability for your use.") | On |
| `eia`: EIA's Europe Brent spot price (`www.eia.gov`) | The weekly history page | Public domain, cited with the publication date ("Source: U.S. Energy Information Administration (<month year>)": the month of the page's "Release Date"), but material licensed from private companies is excluded, and the series' own copyright field (`PET.RBRTE.D`) was not read | Off until that field has been read |
| `h10`: Federal Reserve H.10, rupees per US dollar (`www.federalreserve.gov`) | The 1990s and the 2000-present history pages | Public domain unless indicated; cite "Board of Governors of the Federal Reserve System, H.10 Foreign Exchange Rates". A noon New York rate, not FBIL's fixing | On |
| `bis`: BIS SDMX API (`stats.bis.org`) | India's and the US policy rates, the daily rupee figure | Use unrestricted if the BIS is cited ("Bank for International Settlements, <dataset>, data.bis.org") and, in a commercial product, its statistics add no charge; the API may limit an IP address. Its terms ask users to use the latest API version: the policy rates are asked on v2, the rupee (`WS_XRU`) still on v1, as no v2 answer of it has been sampled (one request would settle it; your call) | On |

Not used: FRED and ALFRED (their terms prohibit storing), FBIL's rupee
reference rate (storing its delayed data needs a subscription), MoSPI's MCP
server, and any RBI host (the RBI store has its own rules, above).

```bash
cr-strat macro-store-update --store data/macro_store                       # New York Fed, H.10, BIS; for cron
cr-strat macro-store-update --store data/macro_store --sources mospi       # MoSPI too: only once you decide to
cr-strat macro-store-update --store data/macro_store --sources ust,eia     # once checked (above)
cr-strat macro-store-ingest --store data/macro_store data/raw/macro/*      # saved answers
cp data/index_events_example.csv data/raw/index_events.csv                 # check every row first (below)
cr-strat macro-store-ingest --store data/macro_store --index-events data/raw/index_events.csv
cr-strat macro-store-ingest --store data/macro_store --release-dates data/raw/release_dates.csv
cr-strat macro-store-status --store data/macro_store [--series '^cpi_2024_all_india_combined']
cr-strat macro-store-status --store data/macro_store --policy-rates data/raw/policy_rates.csv  # BIS vs your table
```

The directory is created on first use. `macro-store-ingest` reads saved
answers of any of the six sources (MoSPI JSON, Treasury CSV, the New York
Fed's JSON or CSV, EIA's and H.10's pages, H.10's Data Download CSV, BIS
CSV), recognised by their content; with no files it only rebuilds the
derived tables (after `--index-events` or `--release-dates`, or an update of
this package).

| Path | Contents |
|---|---|
| `observations/<source>.csv` | Every value ever seen, one row per series, period and vintage (below) |
| `series.csv` | One row per series held: the parser's fields (`dataset`, `indicator`, `base`, `series_type`, `area`, `sector`, `level`, `code`, `label`, `unit`, `period_type`), the catalogue's `name`, `frequency`, `update_info` (its family's release rule), `terms`, `terms_url`, `aliases`, the `citation` filled in, `source_url` (the endpoint), `last_release` (the latest "Release Date" an EIA or H.10 page printed), and what the store holds (`first_period`, `last_period`, `periods`, `vintages`, `revised_periods`, `last_value`, `last_public_from`, `last_first_seen`) |
| `fetches.csv` | One row per request sent: `time` (UTC: when it was sent by the run's clock, the run's start plus its pauses, so no later than the real time), `latest` (the latest its answer can have come: that clock plus the timeout of every request of the run so far), `source`, `host`, `url`, `http_status`, `outcome`, `bytes` (on the wire), `rows` (values in the answer), `kept`, `new`, `changed`, `raw`, `etag`, `last_modified`, `message`; the requests of a run killed before its source's answers were kept are logged `interrupted` by the next |
| `revisions.csv` | One row per value that changed: `seen`, `source`, `series_id`, `period`, `old_` and `new_vintage`, `_value`, `_status`, `_revision`, `old_first_seen`, `prior_seen`, `source_file` |
| `raw/<source>/` | The answers byte for byte, named `<UTC time>_<12 hex digits of the SHA-256>.<ext>`; the same bytes are not saved twice. US and BIS copies are on by default (`--no-raw`), MoSPI's off (`--mospi-raw`) |
| `state.json` | Per host the fetch's state (last request, requests that India day, blocks in a row, cool-down, stop, a demand for contact details); per source its last update, the validators and last read of each URL, the requests left pending, the history queue and what is done of it, the requests given up on (`failed`, each request failing now once); the store's `clock` (the latest time a command recorded); the settings of the last update; a failed rebuild of the derived tables (times in UTC) |
| `release_dates.csv`, `index_events.csv` | The last `--release-dates` and `--index-events` given |
| `derived/*.csv` | `global_daily`, `india_monthly`, `india_quarterly`, `index_events`, `policy_rates_bis`, rebuilt by every command (below) |
| `store.lock`, `journal.json` | Present only while a command runs, or after one was killed |

Times in the CSVs are India time without a zone (`2026-11-12T16:00:00`), as
in the RBI store; `fetches.csv` and `state.json` are in UTC. Read a table
back typed with `macro_store.read_table(store, name)` (`observations`, a
source's key, `series`, `fetches`, `revisions` or a derived table): times
as Timestamps, counts `Int64`, values floats, blanks missing.

**Vintages** (`observations/<source>.csv`). One row per value as first
seen: `series_id`, `period` (`2026-11-17` a day, `2026-10` a month,
`2026-27Q1` a financial-year quarter, Q1 being April to June, `2025-26` a
financial year), `period_start`, `period_end`, `vintage` (1, 2, ...),
`value` (the publisher's text: `3.90` stays `3.90`), `unit`, the
publisher's marks (`status`, such as `F` or a national-accounts estimate
type; `revision`, the New York Fed's `r`; `footnote`; `imputation`),
`first_seen` (when the store had it at the latest: for an update, the
`latest` of the first answer of the run that held it; for an ingest, its
time; never before the vintage it replaces), `public_from`,
`public_from_basis`, `seen`, `print`, `stage`, `release_id`,
`public_from_rule`, `prior_seen` (the latest earlier read of the same data
that did not hold this value: the same query, page or year's file, or a New
York Fed or BIS window that covered its date), `source_file` and `notes`.
The same value seen again adds nothing; a different one (or a changed mark)
adds the next vintage and a row of `revisions.csv`.

**From when a value was public** (`public_from`), recomputed by every
command from the facts, so that a better release table reaches every
vintage; never later than `first_seen`:

| Source | `public_from` |
|---|---|
| MoSPI | The last release, at or before `first_seen`, that could have set the value: the shipped release-date table (`src/cr_strat/data/macro_release_dates.csv`, 2025-26 and 2026-27) and yours (`--release-dates`), else the release rules: CPI the 12th of the next month; IIP the 28th of the next month from March 2025 data, the 12th of the second month after (previous working day) before; GDP the last working day of the second month after the quarter, first advance estimates on 7 January; at 16:00 (CPI and IIP from 12 Nov 2024, GDP from 29 Nov 2024; 17:30 before, and 11:30 for CPI and IIP before July 2013); WPI the 14th of the next month at 12:00. A "next working day" (and a planned date in the table) skips weekends and the Mumbai settlement holidays of 2015 to 2026, a proxy for the central government's holidays that can only make a date later; a "previous working day" knows weekends only: on a weekday holiday its date is a day after the real release, late rather than early. Each family's stages run from first print through revisions to final. Never before the base's launch (CPI 2024 on 12 Feb 2026 16:00, national accounts 2022-23 on 27 Feb 2026, IIP 2022-23 on 1 Jun 2026, WPI 2022-23 on 15 Jun 2026; older bases from memory, at the end of the month believed). A release time is not taken, and the value is dated by its sighting, if the store read the same data after it without the value (`prior_seen`), or read the family's headline after it without the new month that release brings: the release came late |
| Treasury | 23:59:59 New York time on the curve date |
| New York Fed | 10:00 New York time on the next date the same rate has (SOFR, BGCR, TGCR, published about 08:00), 11:00 for EFFR and OBFR (about 09:00), 17:00 for a row marked revised (the averages and index: 10:00 on their own date, 17:00 if revised); EFFR and OBFR before 1 Mar 2016: 23:59:59 on the next date, revised or not; the latest row: its sighting |
| EIA | 23:59:59 New York time on the Thursday after the first Wednesday after the price's date, or later when the days from that Monday to that Wednesday on which Washington may be closed (the federal holidays, computed from the law's rules, and 24 December, and 26 December on a Friday, given off in some years) move the release: to Friday 27 Dec 2024, a week of Christmas on a Wednesday |
| H.10 | 16:15 New York time on the working day after the release that carries the rate (weekly on Mondays, with rates to the Friday before; the next working day after a Monday holiday) |
| BIS | Policy rates 23:59:59 local time on the date in force, public from the central bank's announcement whatever BIS's lag; the US values before 4 Feb 1994 at 23:59:59 New York time on the second weekday after their date (before 19 Dec 1985 the effective rate, published the next business day; then a target the FOMC did not announce: an assumption, not checked). The rupee figure is BIS's own (six decimals, not H.10's rate; BIS names no source), public when BIS publishes it: 23:59:59 Basel time (taken as UTC+1) a week after the first Thursday release at least two days after its date, two weeks for dates from 15 December to 7 January (a rule from BIS's 2026-27 calendar and one observation). Use `usdinr_h10` for the rupee in back-tests |

New York time comes from the US daylight-saving rules, not from time-zone
data. A rule time later than `first_seen` gives `first_seen`
(`public_from_basis` `first_seen`); so does a rule time at or before
`prior_seen` (the store read the same data then without the value: the
publication came late), except for BIS's policy rates, and any change of a
US or BIS value the store held, as these publishers keep no revision
calendar. `seen` is `live` if the store read the value within six hours of
`public_from` (it is the value then published) and `later` otherwise: a
history read, or a value seen late, which may include revisions made after
`public_from`. `print` is `first`, `final` (the last stage of its family)
or `revised`, and `stage` names the release stage (`first`, `quick`,
`provisional`, `first_revision`, `quarter`, `revised`, `fae`, `pe`,
`final`, ...) of the release that set the value as the store knows it: a
value a later release confirmed unchanged keeps the label of the release
that first set it, so `first` may already be final, while a value first
read after its final release is `final` (a row saying when a value was
confirmed is not built: it would need the store to keep the time of each
later read that found the value unchanged).

**Where `public_from` can still be early.** A release later than its rule or
planned date while the store was not polling (history read later: the
2025-26 rows of the shipped table are planned dates, and every date before
April 2025 and every WPI date come from rules; type in actual dates from the
updated calendars or PIB's "Posted On" stamps with `--release-dates`); a
holiday the proxies do not know (a central-government holiday that is not a
Mumbai one, a US closure not in the rules such as a national day of
mourning, any holiday outside 2015 to 2026 for MoSPI); a past BIS rupee
release later than its rule; the US target before 4 Feb 1994; an answer
saved elsewhere (not a copy of one in the store's `raw/`) that holds a value
the store's own read after its rule's time lacked: the ingest does not know
which of its reads would have held it, so the value is dated by the rules
and the release table alone (ingest the store's own raw copies, which are
dated by its request log). Detail tables are assumed to reach MoSPI's API
with their headline unless the store read them without it (then they wait,
below).

**From when history is known.** History read after the fact is the latest
vintage, visible only from the release that set it, so a back-test sees
little of MoSPI's history before the store's first update:

| Family and base | Visible in a back-test from |
|---|---|
| CPI 2024, Current and back series | 12 Feb 2026 16:00, the base's launch |
| CPI 2012 | 28 Feb 2015 23:59:59 (the end of the month of its believed launch, not checked), each month from its final release after that |
| CPI 2010 | Not fetched (its parser exists: your call) |
| IIP 2011-12, WPI 2011-12 | 31 May 2017 23:59:59 (believed launched in May 2017, not checked), then each month from its final release |
| IIP 2022-23, WPI 2022-23 | 1 Jun 2026 16:00 and 15 Jun 2026 12:00, their launches |
| GDP and GVA 2011-12, by quarter | Each quarter of a closed year from that year's final estimates: 35 months after the year ends to FY2022-23 (so as of mid-2019 the latest quarter visible is 2015-16 Q4), 23 months after for FY2023-24; later quarters from their latest revision |
| GDP and GVA 2022-23 | 27 Feb 2026 16:00, the base's launch, then by stage |

So a back-test gets from this store no Indian CPI before 28 Feb 2015, no
IIP or WPI before the end of May 2017, and each GDP quarter of a closed
year 23 to 44 months after the quarter ended (the GDP dates checked on the
store of the saved samples). First prints from before the store's start
are on PIB's release pages (a loader is not built).

**As known at a time.** `macro_store.as_of(store, series_id, t)` gives, per
period, the latest vintage public at `t` (`t` aware, or naive India time),
with `seen` and `not_seen_at_publication`; `seen_only=True` gives only what
the store itself had read by `t`. A back-test asking what was known at `t`
uses `as_of`, or the rows of `india_monthly` and `india_quarterly` (one per
print) with `public_from <= t`; `global_daily` holds each date's latest
vintage only (below).

**What is kept.** The US and BIS series whole. Of MoSPI, All India only:
base-2024 CPI for rural, urban and combined down to groups (general index,
12 divisions, 43 groups; `--cpi-levels items` adds classes, sub-classes and
358 items, and base 2012's items, once MoSPI's terms allow), base 2012's
groups and subgroups; IIP's general index, sectors and use-based groups;
GDP and GVA by industry; WPI's headline and major groups; never the "RBI"
tables (RBI's data, 15 months stale). `--states` adds each state's general
CPI index (36 requests a release): the full state-level download is about
1.5 million rows of base-2024 CPI (15,000 requests), so it is not the
default.

**An update.** A `now` more than five minutes before the store's clock
(the latest time a command recorded, or the latest request logged) is a
clock error: nothing is asked or written. Per source, in the order mospi,
ust, nyfed, eia, h10, bis: nothing if its last update that asked something
began less than `--min-interval` minutes ago (default 25: `too soon`), or
while its host is cooling down or stopped. Otherwise the recent window,
then what it asks next, then what an earlier run left pending, then the
history queue, as far as `--max-requests` (default 60 a run),
`--max-per-host` (default 30 a run) and each host's daily cap (MoSPI 60,
the others 10 an India day) allow:

| Source | Recent window | Asked next | History (queued on the first update) |
|---|---|---|---|
| MoSPI | Three probes, each conditional (`If-None-Match`): general CPI of this year and last, IIP's general index, WPI's headline; GDP and GVA by quarter, GDP by year | A changed CPI probe: the latest month in it and the month before, by sector, and every month since the latest month the store's tables hold (after an outage), the first page of each (`--cpi-levels items`: every page); IIP: the same months' tables; WPI: the four major groups. A table that lacks the month its headline showed is asked again at the next runs, with the other tables of that release, for 6 reads | Default (`--mospi-history derived`): what the derived tables use, from 2013: base-2024 CPI by month and sector from Jan 2025 (the first page), the back series, base 2012 by year, IIP's general index and sectors on both bases, the 2011-12 national accounts, WPI 2011-12's headline: 82 requests and their pages. `full` adds IIP's use-based groups (and base-2012 items and every page of base-2024 CPI with `--cpi-levels items`) |
| Treasury | This year's file (last year's too in the first week of January, and every year since the latest date held) | | One file a year, 2025 back to 1990 |
| New York Fed | Both groups, from 14 days ago or the latest date held, whichever is earlier | | Both groups from their first day (two requests) |
| EIA | The history page, conditional (`If-None-Match`, `If-Modified-Since`) | | None: the page holds 1987 on |
| H.10 | The 2000-present page, conditional (`If-Modified-Since`) | | The 1990s page |
| BIS | The policy rates' last 60 observations (from the latest date held if that is older), the rupee from 60 days ago or the latest date held | | Both series whole |

Every request names a generic client (`--user-agent`; one with an e-mail or
web address, or this package's name, is refused), asks for gzip, follows no
redirect, waits at least 10 seconds after the previous request to the same
host, and is recorded in `state.json` before it is sent. The answers of each
source are committed when it is done, the derived tables at the end; a run
killed during a source's requests loses that source's answers of the run
(the next run logs them `interrupted` and asks again), not those of the
sources before it. An answer its parser refuses, or a MoSPI page that is not
the one asked or whose query changed size since its first page (that query
is then asked again from page 1), keeps no validator, so that the next
request asks for it whole (the time of that URL's last good read is kept: a
value a later answer brings is not dated before it); it goes to `failed`, as
does a request answered with an HTTP 4xx: once per request (a recent window,
whose dates move every day, once per window), and off the list as soon as
it, or a later read of the same data, answers; `--retry-failed` queues the
history and pending ones again (a recent request is asked again by every
run). A history step that the caps cut short waits for the next run;
`macro-store-status` says how far each history got (`history: 9 requests
done, 27 to go, 2 given up`) and what is pending. `--no-backfill` fetches no
history; a changed plan (`--mospi-history full`, `--states`) adds what it
lacks.

Each source prints one line, and a line per thing that needs a look; every
line starts with the run's start time in UTC, so that a log appended to
says which run wrote it. The reminders of MoSPI, Treasury and EIA come first
when they run. Exit codes, as `rbi-store-poll`: `0` normal (new values,
nothing new, not modified, too soon, a cap reached); `4` the run worked but
something needs a look (an answer its parser refused, a MoSPI page that did
not fit, an HTTP 4xx such as a page limit, a table given up after waiting
for its release, a run killed during a request, derived tables not
rebuilt), with `needs a look:` lines on stderr; `3` a host blocked, cooling
down or stopped (HTTP 403, 418 or 429, a refusal page whatever its status,
a demand for contact details, a second web page in a row); `1` any other
error (`error:`: a 5xx, a network error, a redirect, an answer not
readable, a clock that moved back, a store locked or damaged); `2` bad
arguments, checked before anything is recorded.

**Schedule** it on your own machine, for example with cron (nothing here
schedules itself). Give the full path of `cr-strat` or set `PATH`, and
`HTTPS_PROXY` if the machine reaches the web through a proxy. `CRON_TZ`
sets the zone of the times in cronie and some other crons (see `man 5
crontab`); where it is not supported, write the times in the machine's own
zone (in UTC: 01:45 and 15:15; MoSPI 04:00, 06:35 and 07:35, 10:35 and
11:10, 12:30; the days stay the same as long as no time crosses midnight):

```
CRON_TZ=Asia/Kolkata
PATH=/path/to/venv/bin:/usr/bin:/bin
# New York Fed, H.10, BIS: every morning (after New York's evening), and after the New York Fed's 09:00 rates
15 7  * * *  cd /path/to/data && cr-strat macro-store-update --store macro_store >> macro_store.log 2>&1
45 20 * * *  cd /path/to/data && cr-strat macro-store-update --store macro_store --sources nyfed >> macro_store.log 2>&1
# Treasury and EIA once checked (above): add them to the morning run, --sources default,ust,eia
# MoSPI once you have decided to keep it: every morning, and around its release times on the days they can fall
30 9  * * *                   cd /path/to/data && cr-strat macro-store-update --store macro_store --sources mospi >> macro_store.log 2>&1
5 12,13 14-18 * *             cd /path/to/data && cr-strat macro-store-update --store macro_store --sources mospi >> macro_store.log 2>&1
5,40 16 1-9,12-18,26-31 * *   cd /path/to/data && cr-strat macro-store-update --store macro_store --sources mospi >> macro_store.log 2>&1
0 18  1-9,12-18,26-31 * *     cd /path/to/data && cr-strat macro-store-update --store macro_store --sources mospi >> macro_store.log 2>&1
```

The days cover the rules' dates moved by weekends and holidays: CPI from the
12th, WPI from the 14th, IIP from the 28th to the first days of the next
month (2 Mar 2026 for January's), GDP at the end of the month or early in
the next (5 Jun 2026), first advance estimates from 7 January (8 Jan 2029).
A MoSPI run with nothing new costs six conditional requests (MoSPI answered
its ETag with HTTP 304 on 18 Nov 2026); a CPI release brings six more (the
latest month and the month before, by sector: the first page of each), 42
with `--cpi-levels items` (seven pages each); an IIP release two; a WPI
release four, spread over the runs of that day by the caps. A first update
with its history: New York Fed, H.10 and BIS finish in one run; Treasury
takes four days at its cap of ten a day; MoSPI's default history (82
requests and their pages, while the recent window uses part of the daily
cap of 60) a few days.

**Once a month, the release calendar.** MoSPI re-issues its advance release
calendar about monthly and moves dates in it (IIP to 1 Jun and GDP to 5 Jun
in 2026); this package cannot read the PDF, and nothing polls it. Open
https://www.mospi.gov.in/api/documents/get-latest-release-calender in a
browser (it names the current PDF), or save it and ingest it (the ingest
reports the document and its date), and add the dates that moved, or the
actual dates and times once released, to your `--release-dates` table.

**Every morning, check the store**: `cr-strat macro-store-status --store
macro_store`, or `grep -E 'needs a look|error|blocked|cooling|stopped'
macro_store.log` (each line carries its run's UTC time). What each line
needs:

| Line | What to do |
|---|---|
| `blocked`, `cooling down`, `stopped` (status: `host ...: cool-down until`, `stopped since`) | HTTP 403, 418 or 429, a refusal page or bot challenge whatever its HTTP status, a demand for contact details (none is ever given) or a second web page in a row where data were expected is a block. That host gets no request before 08:00 India time the next day (counted from the latest time the block can have happened); a second block in a row stops it until you check the site in a browser and run `cr-strat macro-store-update --store macro_store --sources <source> --resume` by hand (never from cron). Read a site's terms again before resuming one that asked for contact details |
| `needs a look: ... refused` (status: `failed ...`) | The parser refused an answer (a layout never seen, a page that does not fit its query, an answer cut short). The request is given up (`failed` in `state.json`) and asked whole next time; the US and BIS answers are in `raw/`. After an update of this package, read them again with `macro-store-ingest --store macro_store macro_store/raw/<source>/<file>` (a copy of the store's own answer is dated by its request log, as the update would have dated it), or queue the requests again with `--retry-failed`; a recent request is asked again by every run anyway |
| `needs a look: ... had not reached this table` | A MoSPI table still lacked the month its headline showed after 6 reads: check the table in the API's answer by hand; `--retry-failed` asks again. Status lines `tables behind the headline` say the same of what the store holds |
| `needs a look: ... HTTP 404` (or another 4xx) | A request this package should not have made (a page limit, a moved address): no more requests to that host in the run; report it |
| `error: server error`, `error: network error` | Nothing once: the next run tries again. If it goes on (status: `requests in a row without a usable answer`), check this machine's network, then the site. A network error at MoSPI's TLS handshake (`SSL`, `handshake` or `renegotiation` in the message) is what MoSPI's own client says its server needs legacy renegotiation for: add `--legacy-tls` to the MoSPI lines (certificates are still checked) |
| `error: ... not followed` | The address moved: check it in a browser before anything else |
| `needs a look: ... the run ended ...` | A run was killed: nothing to do, the next run asked again |
| `error: clock error` | Check the machine's clock. If a source's `last_update` or a host's `last_request` in `state.json` is the problem, clear the time the line names; if the store's own clock is ahead (a clock that ran fast), wait until the real time passes it |
| `needs a look: derived tables not rebuilt` (an ingest: `error: derived tables not rebuilt`) | An update of this package, or a bug report: the values read were kept, and the previous tables too |

**Saved answers and repairs.** `macro-store-ingest` reads files in the order
of their full names as given (then their content), so the same files in any
order give the same store, and the same files again change nothing: name
saved answers by the time they were saved, or keep them in dated folders. A
saved answer is dated by the ingest (`first_seen`), not by when it was
saved, which the store cannot know: ingest saved answers oldest first; a
value equal to one the store has already superseded is not taken as a change
back; an ingest with a clock behind the store's is refused. Two inputs of
one ingest that disagree on a value keep the later one by name, and say so,
naming both. A copy of one of the store's own answers (the bytes of a file
in its `raw/` that `fetches.csv` logs as the answer to a request: a refused
answer, say, read again after an update of this package, from `raw/` or from
a copy elsewhere) is of known age. Such copies are read first, in the order
the store received them, and each value is dated as the update would have
dated it: first seen at the `latest` of the request that first brought it,
and bounded by the store's reads of the same data logged before that request
(`prior_seen`); a change back is taken like any change, and a value older
than what the store holds (a vintage first seen after the copy came, or a
read of the same data since) is not taken (the ingest's line says how many).
A copy whose file is gone from `raw/` is dated as any saved answer.
Base-2012 and 2010 CPI rows do not say their series (Current or Back): a raw
copy of the store's own request is read as its URL says, any other such
answer only with `--cpi-series Current` or `Back` (refused otherwise, so
that a back series is never taken for a revision of the current one).

**Dropping a source** (MoSPI says no, say): with no command running, delete
`observations/<source>.csv` and `raw/<source>/`, delete that source's rows
from `revisions.csv` (its `source` column) and its entry under `sources` in
`state.json`, then run `macro-store-ingest --store macro_store` with no
files: `series.csv` and the derived tables are rebuilt without it (checked
on a copy of a store of the saved samples: no MoSPI series left, the Indian
tables empty). `fetches.csv` keeps the log of the requests made.

**Derived tables** (`derived/`, deterministic: the same store gives the
same bytes). Each value is the latest vintage; `public_from` is the latest
of the row's values, with a `_public_from` per group of values that became
public together, `seen` is `later` if any value was read later than its
publication, `revised` is when the store last saw a value of the row change.
Nothing is filled forward, and bases are never spliced: a back-test uses the
base that was current at `t` (CPI 2012 before 12 Feb 2026 16:00, then 2024;
IIP 2011-12 before 1 Jun 2026; WPI 2011-12 before 15 Jun 2026).

| File | One row per | Columns |
|---|---|---|
| `global_daily.csv` | Date any US or BIS series has (the observation date: trade date, curve date, spot date, rate-in-force date) | `date`; `ust_par_1m` ... `ust_par_30y`, `ust_public_from`; `sofr`, `bgcr`, `tgcr`, `nyfed_secured_public_from`; `effr`, `obfr`, `fomc_target_low`, `fomc_target_high` (blank before 16 Dec 2008, when the target was a point), `nyfed_unsecured_public_from`; `sofr_avg_30d`, `sofr_avg_90d`, `sofr_avg_180d`, `sofr_index`, `sofrai_public_from`; `brent_usd_bbl`, `brent_public_from`; `usdinr_h10`, `usdinr_h10_public_from`; `policy_rate_in_bis` (the bank rate before 3 Apr 2001, the repo rate from then), `policy_rate_us_bis` (the effective federal funds rate before 19 Dec 1985, the target's mid-point from then), `bis_policy_public_from`; `usdinr_bis`, `usdinr_bis_public_from`; `public_from`, `seen`, `revised`, `notes` (values the New York Fed marked revised or footnoted; a series' change of definition on its first row: BIS's two, EFFR's layout of 1 Mar 2016, the target range of 16 Dec 2008). The latest vintage of each date only: a date whose value was revised has its row's `public_from` at the revision, so for what was known at `t` use `as_of` or the per-group `_public_from` columns, not the row's |
| `india_monthly.csv` | Family (`cpi`, `iip`, `wpi`), base and month, and each moment a value of it became public (a print) | `month`, `family`, `base` (`2024`, `2024back` for base 2024's back series, `2012`, `2010`, `2022-23`, `2011-12`), `print`, `stage`, `status`, `public_from`, `first_public_from`, `public_from_basis`, `release_id`, `seen`, `revised`; CPI `cpi_<m>_index`, `cpi_<m>_yoy`, `cpi_<m>_public_from` for `combined`, `rural`, `urban`, `food` (base 2024's group 01.1, the CFPI of MoSPI's release; base 2012's CFPI), `food_bev`, `fuel` (group 04.5; base 2012's fuel and light), `housing` (division 04; base 2012's housing), and `cpi_rent_index` (group 04.1); IIP `iip_index`, `iip_yoy`, `iip_manufacturing_index`, `_yoy`, each with `_public_from`; WPI `wpi_index`, `wpi_yoy` (computed here, on one base, from the month a year before as known at `public_from`: WPI publishes no inflation), `wpi_food_index`; `notes`. Every measure is as known at the row's `public_from`: a final row carries the measures that did not change |
| `india_quarterly.csv` | Base, quarter and moment | `quarter`, `base`, the same columns as above, `gdp_current_cr`, `gdp_constant_cr`, `gdp_public_from`, `gva_current_cr`, `gva_constant_cr`, `gva_public_from`, `gdp_constant_yoy`, `gva_constant_yoy` (computed from the same base as known then), `notes` |
| `index_events.csv` | Event of the table you gave | Its columns (below) and `public_from`, `public_from_basis`, `public_from_rule` |
| `policy_rates_bis.csv` | Change of India's repo rate in BIS's series, from 3 Apr 2001 (the bank rate before) | `effective_date`, `repo_pct`, `sdf_pct`, `msf_pct`, `source`, `public_from`: the RBI store's policy-rate columns, with `sdf_pct` and `msf_pct` **blank**, as BIS has no corridor. `rbi-store-ingest --policy-rates` refuses the file until you fill them in from RBI's resolutions; check the dates too (BIS lags about a month and dates some 2019 changes a day after RBI's decision): `macro-store-status --policy-rates <your table>` lists the changes of the repo rate BIS and your table date differently (within a week) or one of them lacks (a row of your table that leaves the repo rate as the row before had it, the corridor alone moving, is counted and not compared) |

**Index events**, kept by hand: none is shipped as a default. An example
with the decisions found on 17 and 18 Nov 2026 is
[`data/index_events_example.csv`](index_events_example.csv): 13 rows, 3
verified on LSEG's pages, the rest from news reports, each with the page it
was read on or reported in, except Bloomberg's review of November 2026,
whose source is a 5paisa.com report named by its title (no URL was found);
two rows of an earlier example that named no source document were dropped.
Copy it, check each row, pass the copy with `--index-events`; the store
keeps a copy and every later command uses it. Columns: `event_id`
(lower-case letters, digits, `_`, once each), `provider`, `index_name`,
`event_type` (`watch_list`, `consultation`, `announcement`,
`inclusion_start`, `tranche`, `inclusion_complete`, `deferral`,
`review_no_change`, `scheduled_review`), `announced_date` (`YYYY-MM-DD`, or `YYYY-MM` when only
the month is known), `announced_time` (`HH:MM`, blank if unknown),
`announced_tz` (`America/New_York`, `Europe/London`, `Asia/Kolkata`),
`effective_start`, `effective_end`, `phase_in`, `target_weight_pct`,
`bonds`, `status` (`verified` or `unverified`), `source_url`, `notes`. A
row that breaks a rule (an unknown status or zone, an end before its start,
an id used twice, an empty source) is refused, naming the file and the
line, before anything is written. An event is public from 23:59:59 (or its
time) in the provider's zone on its announcement date (the last day of its
month when only the month is known).

**Release dates**, kept by hand: MoSPI's calendar is a PDF this package
cannot read. The shipped table holds the 2025-26 calendar's planned dates
and the 2026-27 calendar's actual and planned ones, each with its source.
Add actual dates and times as you read them (PIB's "Posted On" stamp, the
updated calendar: the 2025-26 actual dates need the updated 2025-26
calendar, not read here) in a file of the same columns and pass it with
`--release-dates`: its rows override or extend the shipped ones, and every
vintage is dated again.

**Backups and a stale lock.** Back up the store directory (plain files)
while no `macro-store-*` command runs: what the APIs served cannot be
fetched again once revised. A killed run leaves `store.lock`; the next run
refuses to start, says who held it and since when, and never deletes it.
If no `macro-store-*` command is running, delete the file; the next command
completes or drops the interrupted commit.

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
