# cr_strat

Research tooling for Indian corporate bond strategies. The first strategy under
study is **rating-threshold forced selling**: when an issuer's rating crosses a
line that mutual funds, insurers, NPS or EPFO must respect (AA+ for corporate
bond funds, AA for insurers, two agencies at AA for NPS), constrained holders
have to sell regardless of price. The question is whether that selling
overshoots and reverts, and how to tell it apart from downgrades that carry
real bad news.

This repo builds the panels that research needs:

1. **Rating-event panel.** Every threshold crossing, by mandate, with its cause,
   the agencies involved, prior outlook/watch signals, split ratings and
   liquidity descriptors.
2. **Ownership panel.** For each event, the share of the issuer's outstanding
   paper held by the holders that mandate binds, and how those holdings changed
   30/60/90 days later (the "first stage": did they actually sell?), with
   unconstrained MF holders as a control.
3. **Price panel.** Exchange-reported trades turned into ISIN-day yields and
   spreads over a G-sec or OIS curve, each bond placed in a peer group by
   instrument class, as-of rating and tenor.
4. **Event price paths.** Each event bond's spread before and after the event
   against matched peers. The abnormal change is measured against bonds that
   were rated like it **before** the event; the gap is measured against bonds
   rated like it **now**. This follows the matched-portfolio approach of
   Bessembinder, Kahle, Maxwell & Xu (2009), "Measuring Abnormal Bond
   Performance".

The regressions themselves (reversal on constrained ownership, filters) come next.

## Pipeline

```
rating actions CSV ──► rating-events ──► rating_events.csv ──┬──► ownership ──► event_ownership.csv
                         (mandates.toml)                      │        ▲
AMC portfolio .xlsx ──► parse-mf ──► holdings.csv ────────────┼────────┤
AMFI NAVAll.txt ────────┘ (scheme categories)                 │        │
security master CSV ──────────────────────────────────────────┼────────┘
                                                              │
NSE/BSE trade files ──► load-trades ──► trades.csv            │
                             │                                ▼
G-sec / OIS curve ──► price-panel ──► price_panel.csv ──► event-prices ──► event_prices.csv
  (+ security master, rating actions)  peer_spreads_weekly.csv               event_bond_prices.csv
```

Input formats and where to get each file: [`data/README.md`](data/README.md).
Sources and their terms: [`docs/data_sources.md`](docs/data_sources.md) for this
pipeline, [`docs/rates_data_sources.md`](docs/rates_data_sources.md) for the rates
and credit strategies. Day counts, calendars and price-yield rules for G-secs,
SDLs, STRIPS, T-bills, CP and CD, listed corporate bonds (NCDs) and floating-rate
bonds, with their sources: [`docs/conventions.md`](docs/conventions.md). Risk of the return engine's books
(sensitivities, VaR and ES, stress tests, liquidity): [`docs/risk.md`](docs/risk.md).

## Quickstart

```bash
pip install -e ".[dev,xls]"
pytest

cr-strat rating-events --actions data/raw/rating_actions.csv --out-dir data/out
cr-strat parse-mf data/raw/mf/hdfc/*.xlsx --amc "HDFC Mutual Fund" \
    --navall data/raw/NAVAll.txt --out data/out/holdings_hdfc.csv
cr-strat ownership --events data/out/rating_events.csv \
    --holdings data/out/holdings_*.csv --securities data/raw/securities.csv \
    --out data/out/event_ownership.csv

cr-strat load-trades data/raw/trades/nse/*.csv --venue NSE --out data/out/trades_nse.csv
cr-strat load-trades data/raw/trades/bse/*.csv --venue BSE --out data/out/trades_bse.csv
cr-strat price-panel --trades data/out/trades_*.csv --securities data/raw/securities.csv \
    --benchmark data/raw/gsec_par_curve.csv --rating-actions data/raw/rating_actions.csv \
    --out-dir data/out
cr-strat event-prices --events data/out/rating_events.csv --panel data/out/price_panel.csv \
    --out-dir data/out

cr-strat bond-price --coupon 6.94 --maturity 2036-05-11 --settlement 2026-11-16 --price 97.43
cr-strat corp-price --coupon 8.95 --issue-date 2023-12-14 --maturity 2030-12-14 --settlement 2026-11-16 \
    --price 102.26 --cash-flows                         # an NCD from its terms: SEBI actual/actual, record dates
cr-strat corp-price --terms data/raw/corporate_terms.csv --isin INE000X01011 --options data/raw/corporate_options.csv \
    --settlement 2026-11-20 --yield 7.4 --valuation sebi    # or --prices prices.csv --out analytics.csv for many
cr-strat frb-price --isin IN0020200120 --store data/rbi_store --as-of 2026-11-19T20:00 --settlement 2026-11-20 \
    --price 100.25 --rates                              # a GoI FRB: coupons as known then, discount margin
cr-strat frb-coupons --store data/rbi_store --as-of 2026-11-19T20:00 \
    --out data/out/frb_coupons.csv                      # every GoI FRB's coupons as known then, period by period
cr-strat trade-check --trades data/out/trades_*.csv --terms data/raw/corporate_terms.csv \
    --out data/out/trade_checks.csv                    # each print's yield recomputed from its price and terms
cr-strat credit-spreads --prices data/raw/corporate_prices.csv --terms data/raw/corporate_terms.csv \
    --nss-params data/out/nss_params.csv --date 2026-11-13 --workout worst \
    --out data/out/credit_spreads.csv                   # G-spread, par spread, z-spread to a fitted G-sec curve
cr-strat curve-fit --prices data/raw/prices_20261116.csv --master data/out/sovereign_master.csv \
    --settlement 2026-11-16 --out-dir data/out/curve_20261116   # needs licensed traded prices (docs/analytics.md)
cr-strat curve-fit ... --nss-params data/out/nss_params.csv --public-from 2026-11-13T19:00   # the table returns reads
cr-strat ois-curve --quotes data/raw/ois_20261119.csv --trade-date 2026-11-19 --overnight 5.60 \
    --project-calendar --out-dir data/out/ois_20261119          # OIS quotes are licensed (FBIL): docs/analytics.md
cr-strat asset-swaps --quotes data/raw/ois_20261119.csv --trade-date 2026-11-19 --overnight 5.60 \
    --project-calendar --bonds data/raw/bonds_20261119.csv --master data/out/sovereign_master.csv \
    --out data/out/asset_swaps_20261119.csv
    # --terms data/raw/corporate_terms.csv: corporate bonds; --frb-out FILE --store DIR --as-of T: GoI FRBs' margins
cr-strat repo-carry --store data/rbi_store --bonds data/raw/positions.csv --haircuts data/raw/haircuts.csv \
    --start 2026-11-20 --end 2026-12-21 --as-of 2026-11-20T09:30 --project --out data/out/carry.csv
cr-strat policy-path --quotes data/raw/ois_20261119.csv --trade-date 2026-11-19 \
    --policy-rates data/raw/policy_rates.csv --store data/rbi_store --project --project-calendar \
    --out-dir data/out/policy_20261119
cr-strat returns --trades data/raw/trades.csv --flows data/raw/flows.csv --marks data/raw/marks.csv \
    --nss-params data/out/nss_params.csv --store data/rbi_store --haircuts data/raw/haircuts.csv \
    --access nbfc_non_pd --end 2026-12-14 --project-funding --out-dir data/out/returns  # marks are licensed
    # --reference sdl=sdl: SDLs against an SDL curve of --nss-params; --rate-basis calendar_day: repo-carry's basis
    # --corporate-terms data/raw/corporate_terms.csv (and its tables), --tbills-from-store: corporate bonds, GoI FRBs
cr-strat risk --positions data/out/returns/positions.csv --nav data/out/returns/nav.csv \
    --funding data/out/returns/funding.csv --trades data/raw/trades.csv --nss-params data/out/nss_params.csv \
    --access nbfc_non_pd --date 2026-12-14 --var-history data/out/var_history.csv \
    --out-dir data/out/risk_20261214     # key rates, CS01, JTD, VaR and ES, stress, liquidity (docs/risk.md)
cr-strat var-backtest --var-history data/out/var_history.csv --nav data/out/returns/nav.csv \
    --attribution data/out/returns/attribution.csv --out-dir data/out/backtest   # Kupiec, Christoffersen, zones

cr-strat rbi-releases data/raw/rbi/releases/*.html data/raw/rbi/feeds/*.xml --out-dir data/out/rbi
cr-strat rbi-store-poll --store data/rbi_store            # one request at most; run it from cron (data/README.md)
cr-strat rbi-store-ingest --store data/rbi_store data/raw/rbi/releases/*.html --master data/out/sovereign_master.csv
cr-strat rbi-store-status --store data/rbi_store
cr-strat macro-store-update --store data/macro_store       # New York Fed, H.10, BIS; MoSPI only with --sources mospi
cr-strat macro-store-ingest --store data/macro_store data/raw/macro/* --index-events data/raw/index_events.csv
cr-strat macro-store-status --store data/macro_store
cr-strat sovereign-master --goi-list data/raw/rbi/goi_outstanding_*.html --far data/raw/rbi/far.html \
    --snapshots data/out/goi_outstanding.csv --out data/out/sovereign_master.csv
cr-strat auctions --fimmda-gsec data/raw/fimmda/Gsec_Auction_data_*.xlsx --fimmda-sdl data/raw/fimmda/SDL_*.xlsx \
    --handbook data/raw/rbi/hbs_t221.html --releases data/out/rbi --master data/out/sovereign_master.csv \
    --snapshots data/out/goi_outstanding.csv --out-dir data/out/ledger
cr-strat outstanding --ledger data/out/ledger/ledger.csv --date 2024-03-31 \
    --snapshot data/out/goi_outstanding.csv --out data/out/outstanding_20240331.csv
cr-strat event-ledger --auctions data/out/ledger/auctions.csv --ledger data/out/ledger/ledger.csv \
    --store data/rbi_store --switches data/out/ledger/switches.csv --snapshot data/out/goi_outstanding.csv \
    --master data/out/sovereign_master.csv --macro --out-dir data/out/events   # S4: docs/analytics.md
    # --calendar-lines data/raw/tbill_calendar_lines.csv: the T-bill calendar's weekly lines, typed in
```

`event_prices.csv` and `event_ownership.csv` share `event_id`; joining them gives
the pressure variable and the price response for each event.

## Modules

| Module | What it does |
|---|---|
| `rating_scale` | Parses CRA rating strings (`CRISIL AA+/Stable`, `[ICRA]AA (Negative)`, `IND AA/RWN`, `PP-MLD`, `(CE)`, FD scales, issuer-not-cooperating, withdrawals) onto one notch scale |
| `rating_actions` | Loads and normalises rating actions; infers instrument class (bond, bank loan, CP, Tier II, AT1, MLD, structured, FD) |
| `mandates` | Mandate rules from `config/mandates.toml`: how agency ratings combine (worst, k-th best, single agency) and the thresholds |
| `rating_events` | Effective-rating timelines per issuer × instrument class × mandate, and threshold-crossing events |
| `mf_portfolio` | Parses AMC portfolio workbooks (one sheet per scheme or stacked), finding scheme name, date and value units |
| `schemes` | AMFI NAVAll.txt as a scheme master; attaches SEBI categories to holdings by normalised name, with manual overrides |
| `ownership` | Constrained ownership at each event and holder selling after it |
| `trades` | Loads NSE/BSE reported-trade files (trade-level or ISIN-day summaries; the deal type and the yield type of SEBI's reporting format kept when a file has them), flags yield outliers, aggregates to value-weighted ISIN-day yields (inter-scheme transfers left out); checks each corporate bond print's reported yield against the yield recomputed from its price and the bond's terms as known on its trade date (clean or dirty, per 100 of the face outstanding or original, due or payment dates, maturity or each call and put, money-market or compounded), flagging those beyond a tolerance or matching only as dirty prices or on the other face basis, and gives ISIN-day rows on recomputed yields; `cr-strat trade-check` |
| `curves` | Benchmark curve interpolation with compounding conversion; matched-peer spread benchmark |
| `conventions` | Day counts (30E/360 for G-secs and SDLs, 30/360 US, Act/365F, Act/Act ICMA, SEBI's actual/actual for NCDs), yield-basis conversions (annual, semi-annual, quarterly, monthly), and each instrument type's conventions with their sources (`NCD`, `NCD_2013`, `NCD_PRE2013` and `GOI_FRB` in `CORPORATE_CONVENTIONS`) |
| `calendars` | Mumbai settlement calendar from the holiday list shipped in the package (`src/cr_strat/data/`): business days, date rolls, T+n settlement dates from an instrument's conventions; refuses years not declared complete |
| `bonds` | G-sec, SDL, STRIPS, T-bill, CMB, CP and CD pricing: coupon schedules, accrued interest, price and yield, duration, DV01, convexity |
| `corporates` | Listed corporate bonds (NCDs) as `bonds.Bond` subclasses: schedules counted forward from the deemed allotment (or backward, monthly on the 1st with a clubbed first period, or explicit from the term sheet or SEBI's database), SEBI's actual/actual with its leap-year rule (three readings), coupons on holidays and early redemptions by SEBI's 2016 and 2013 rules, record dates and ex-interest pricing, the redemption record window refused, zero-coupon, deep-discount and cumulative bonds, amortisation, rating-linked coupon steps used only from when the rating was public, calls and puts (yields to each, to worst, prices to worst and best put, SEBI's mutual-fund trigger rule, AT1 to first call, perpetuals deemed 100 years), exercises and decisions not to exercise counted from their notice (a batch takes the terms as known at 00:00 on each row's trade date), prices per 100 of the original or the outstanding face; yields annual on actual/365 with the money-market rule in the last year, any compounding basis; term tables (`corporate_terms.csv` and its schedule, options, amortisation and step tables; an unknown column refused) and batch pricing; `cr-strat corp-price` ([`docs/conventions.md`](docs/conventions.md)) |
| `floating` | GoI FRBs: 182-day T-bill weighted average yields from WAPs with each result's public time, the coupon rule (mean of the last three before the fixing day, rounded, plus the spread), each period's coupon as known at a time (announced, computed, projected flat or forward, unfixed when a period ended still projected, or unknown; a base more than a period old refused unless allowed), the bond as an unchanged `FixedRateBond` with a 1% coupon scaled by each period's rate, discount margin in index or curve form and back, spread DV01, spread duration, index DV01 (the coupon in force held); benchmark-linked corporate FRBs (floors, caps, look-back, fixings with `public_from`, fixed only on the reset's own published fixing); the five outstanding GoI FRBs' terms and known coupons shipped with sources (`data/goi_frb_terms.csv`, `data/goi_frb_coupons.csv`); `cr-strat frb-price`, `cr-strat frb-coupons` |
| `curvefit` | Zero curves fitted to one day's G-sec and T-bill prices or yields (Nelson-Siegel-Svensson by multi-start Levenberg-Marquardt; a penalised B-spline of the forward curve as cross-check), every bond priced through `bonds`; rich/cheap residuals in bp and price, in the fit and out of sample (each bond against the curve refitted without it), with each bond's leverage; z-scores against a daily residual history it keeps; each residual's free drivers (coupon, age, FAR, reissues, stock as of the day) with an optional regression on them; butterflies, DV01-neutral pairs, par-curve spreads, par yields at any maturity (`par_yields`); a model-risk flag where the two curves refitted without a bond differ at its maturity (on a day with suspect bonds, also when fitted without them); `cr-strat curve-fit` for a generic price file, with `--nss-params` keeping the day's NSS parameters in the table `cr-strat returns` reads ([`docs/analytics.md`](docs/analytics.md)) |
| `ois` | MIBOR-OIS (and SORR-OIS) conventions from FIMMDA's handbook with each rule's status, T+1 schedules on the Mumbai calendar, daily compounding of overnight fixings (weekends, holidays, missing days by the handbook's rules) and settlement statements; a discount curve bootstrapped from one day's par quotes (FBIL's 1M-5Y as `fbil` loads them, longer tenors given by the user; flat forwards, no extrapolation unless allowed); par rates, value, PV01 and DV01 of any OIS, accrued interest from fixings; `cr-strat ois-curve` ([`docs/analytics.md`](docs/analytics.md)) |
| `asset_swaps` | Yield-yield (matched basis), par-par, proceeds and z-spreads of G-secs, SDLs, bills, CP, CD and corporate bonds (from their terms: flows on due or payment dates, the matched rate on the bond's annual basis, an amortising notional) against an OIS curve or any discount curve (a fitted G-sec curve too), from `bonds`' and `corporates`' cash flows; GoI FRBs' discount margins over their index and the curve, with spread and index DV01, on the coupons known at a time; the OIS notional matching a bond's DV01; `cr-strat asset-swaps` |
| `funding` | The RBI store's daily TREPS, market repo, corporate bond repo and call rates as funding tables (dealing days, corridor and quarter-end flags, only rates public and unrevised by a given time), a dated haircut table kept by the user, CCIL's TREPS concentration caps on SDLs and illiquid G-secs (the rule of June 2026, and the earlier one as summarised, UNVERIFIED), and the carry of a repo-funded bond or asset swap, long or short: coupon income, pull to par, repo interest (with a funding spread), the haircut's cost, the OIS legs period by period on the fixings known at that time (corporate bonds on their record-date coupons and principal, also as rows of their terms in `repo-carry`; GoI FRBs on their coupons as known, from the library only); `cr-strat repo-carry` |
| `costs` | The trading costs of the rates strategies as a dated table (defaults shipped in `src/cr_strat/data/cost_defaults.csv`, each row with its source and status V, P or UNVERIFIED; a user table overrides them): half bid-offer by instrument, CCIL liquidity class and, for corporate bonds, rating bucket, CCIL settlement, repo and TREPS charges, platform, exchange and custody fees, brokerage (a brokered trade needs a rate), GST, stamp duty (debentures 0.0001% on the buyer), the TREPS margin; fills at mid plus or minus the half spread |
| `returns` | The return engine for the instruments `bonds`, `corporates` and `floating` price (G-secs, SDLs, bills, STRIPS, CP, CD, corporate bonds and GoI FRBs; corporate bonds with the exchange's settlement, record dates and ex-interest accrual, amortisation, calls and puts once their exercise is public, rating steps and `frb_corp` fixings only from when public; FRB coupons only as public, a `coupon_reset` line when a coupon becomes known after it began to accrue): books of bond and OIS trades held from settlement (forwards before it, when-issued trades to the issue date, partial closes, shorts where the access policy allows, redemptions on holidays), valued daily from marks used only from when they were public (stale and model fall-backs flagged), funded overnight in TREPS, market repo or (corporate bonds) corporate bond repo at the RBI store's rates within haircuts, CCIL's caps and the TREPS margin, charged `costs`, with each day's NAV change split into accrual, pull to par, roll-down, curve factors, rich/cheap, convexity, trading, costs, funding and OIS lines that sum to it exactly (an explicit unexplained line); performance and risk; a back-test loop deciding at 20:00 on a snapshot of what was public then (marks, curves, funding, fixings, and through its accessors the event ledger, auctions, the RBI store's tables, macro series, the MPC calendar, the residual history and the master's securities and fixed terms, each cut at that time), with auction bids within RBI's limits; access presets for the fund's legal form, the when-issued and hedging-only rules checked every day; `cr-strat returns` ([`docs/analytics.md`](docs/analytics.md)) |
| `risk` | Risk of one day's positions of the return engine as known at a time: yield and curve DV01, key-rate DV01s on the fitted G-sec curve (and OIS zero rates and par quotes for swaps) summing to the parallel figure, CS01 on the z-spread, GoI FRB index and margin DV01s, convexity, jump to default with Basel's LGDs, each by position and summed by book, strategy, kind, tenor bucket, key rate, issuer, group, sector and rating bucket; factor histories from the NSS fits and the user's spread levels cut at their public times; historical (sensitivity and full revaluation) and parametric EWMA VaR and ES at 99% and 97.5%, one and ten days, refused or flagged on short or gappy histories, with the stressed ES, the liquidity-horizon ES and Euler components; back-tests against the next day's actual and hypothetical P&L (Kupiec, Christoffersen, the Basel traffic light); `cr-strat risk`, `cr-strat var-backtest` ([`docs/risk.md`](docs/risk.md)) |
| `stress` | Stress scenarios revalued in full: a shipped table of 37 scenarios with each row's source and status (BCBS's six IRRBB shocks for INR, FRTB's credit spread weights, rating ladders and migrations, FRB index and margin shocks, funding and bid-offer shocks, the 2013, 2018, 2020 and 2022 Indian episodes, each usable only from its end) extended or replaced by the user's; P&L by rates, spread, FRB, funding and liquidity with their interaction; days to liquidate and exit costs, normal and stressed, shares of NAV saleable in 1, 5 and 20 days; funding liquidity and the ALM ratios for a preset that borrows, not applicable for one that cannot ([`docs/risk.md`](docs/risk.md)) |
| `policy_path` | MPC meeting dates shipped as package data (FY2026-27 with the day each became public, years declared complete, the calendar as known on any day, projection on request), a step model of overnight MIBOR as repo plus a spread, the repo-rate level after each meeting decided after the trade date backed out of one day's OIS quotes, starting from the rate known that day, with what the quotes identify; the MIBOR-repo spread from public rates, repo-rate changes matched to decisions; `cr-strat policy-path` |
| `htmlparse` | Tables, text and links from saved HTML pages with the standard library: nested tables kept apart, colspan and rowspan expanded; strict number and date cells; firewall-page detection |
| `rbi_releases` | RBI press releases from saved pages and the RSS feed: GoI, T-bill and SDL auction results, underwriting and switch results, auction announcements. A registry of release types; checks that refuse a release rather than misread it |
| `rbi_operations` | The other RBI releases of that registry: the daily Money Market Operations (one row per printed line: call, triparty and market repo rates and volumes, the SDF-MSF corridor, every liquidity operation, net liquidity, reserves), variable rate repo and reverse repo auctions, open market operations, the GoI and state issuance calendars |
| `rbi_store` | A daily store fed by RBI's press-release feed: a poller that makes one conditional request at most, never within its minimum interval, logs every request, asks nothing more that India day after a block and stops after a second block in a row until resumed by hand; ingest that keeps each release and every copy read once, logs corrected rows, repairs failed releases (also from its raw copies after a parser fix, which bring every entry up to date), reads RBI's press-release listing and lists possible gaps (ids never seen, listed releases never read, days without Money Market Operations); derived tables of daily money-market rates and liquidity (with quarter-end flags, changed footnote marks, a check of outstanding operations across days and policy rates kept by hand), liquidity auctions joined across announcement, result and Money Market Operations, open market operations and switches with ISINs, and a supply calendar, each row with the time from which all its values were public. Atomic, locked writes |
| `storekit` | What the stores share: one GET with the standard library (no redirect followed, a byte cap, an optional TLS context that keeps certificate checks), gzip decoding, checks of the client name and timeout, the block rule's clock (no request before 08:00 India time the next day), and the writes: atomic files, a commit journal that the next command completes or drops, and a lock that is reported and never broken |
| `ustime` | New York, London and India time from the published daylight-saving rules, without time-zone data (checked against `zoneinfo`: New York 1987 to 2030, London 1996 to 2030) |
| `macro_base` | The long format every macro and global source parses into (one row per value, kept as the publisher's text, with base, series, sector, level, code, status, revision), the refusal, and India's financial-year periods |
| `mospi` | MoSPI's eSankhyiki API: CPI (bases 2024, 2012, 2010), IIP, national accounts and WPI. Request builders, parsers that refuse an answer they cannot read whole (pages that do not add up, a changed unit, a layout never sampled), the release-date table shipped in `src/cr_strat/data/macro_release_dates.csv`, and the time from which each value was public (release rules, revision ladders, base launches) |
| `global_sources` | US Treasury par yields, the New York Fed's reference rates, EIA's Brent price, the Fed's H.10 rupee rate, BIS policy and rupee rates, and index inclusion events from a table kept by hand (an example with each fact's source: `data/index_events_example.csv`, never a default): requests, parsers that refuse an answer cut short, and publication-time rules in New York, London and India time that move past US federal holidays |
| `macro_sources` | The macro sources together: the hosts that may be asked (RBI, FRED, FBIL, CCIL, the exchanges and others never), a polite fetch (generic client name, conditional requests where honoured, a pause between requests to a host, caps per run and per day, the block rule), parsing by kind, and the series catalogue with each publisher's terms and citation |
| `macro_store` | A store of the macro and global series: every value ever seen as a vintage with the time from which it was public (release calendar and rules for MoSPI, publication rules in New York time for the US, BIS's dates in force; never before the store's own read of the same data, after the rule's time, that lacked it) and whether the store saw it at publication; `as_of` for what was known at a time. Updates that ask the recent window (reaching back over any outage), follow-ups and a history in steps within caps per run, per host and per day (MoSPI only when named), with the block rule; a refused answer is asked again whole and kept in `failed` until it reads; ingest of saved answers in any order, copies of the store's own answers dated by its request log; derived tables of global daily rates, Indian monthly prices and output and quarterly GDP by print, the index events kept by hand and BIS's repo rate in the RBI store's policy-rate columns, with a check of its changes against a table kept by hand. Atomic, locked writes |
| `sovereign` | Sovereign security names (G-sec, SDL, T-bill, FRB, green bond) parsed to a join key; state codes; RBI's GoI-outstanding and FAR lists; a master with one row per ISIN; name-to-ISIN matching |
| `auctions` | One table of G-sec, SDL and T-bill auctions from FIMMDA's yearly and SDL workbooks, RBI's Handbook Table 221 and RBI's releases: ISINs, settlement dates, and a check that the cut-off price reproduces the printed yield, which infers settlement and missing maturity dates |
| `issuance` | Ledger of issues, reissues, switches, buybacks and maturities per ISIN; outstanding stock on any date, backward from RBI's lists or forward, with completeness flags; reconciliation with RBI's list; supply by month and tenor bucket |
| `event_ledger` | The S4 event ledger: every auction line (joined to the issuance ledger, with RBI's bids, weighted averages and greenshoes, Handbook bids and devolvement, underwriting commissions), switch leg, buyback, redemption, open market and liquidity operation, issuance-calendar line, MPC decision and minutes, MoSPI release and index event, each with the time from which its announcement and its result were public and whether that time is a print or a rule (MPC decisions at their era's time); what was known at any time, with what a result decides (its fields, flags and notes, a new issue's coupon and ISIN) hidden until it was public; DV01 supply (from `bonds` at the cut-off yield) by auction week, kind and tenor bucket against the stock and its DV01 (`issuance.outstanding_on`'s rules for many dates at once), with the ledger identities checked; bid-cover, tails, devolvement; forward supply as the calendars stood on a date against what came (T-bill calendar lines typed in by the user; blank where no weekly calendar plans a kind); business-day event windows with overlaps, isolated events and a later price panel's path; `cr-strat event-ledger` ([`docs/analytics.md`](docs/analytics.md)) |
| `price_panel` | ISIN-day yields, spreads, tenor and as-of rating buckets; on request (`--yield-source recomputed`) on yields recomputed from prices and terms, the reported ones beside, the spread and tenor at the workout date (`recomputed_where_known` keeps bonds without terms on their reported yields) |
| `credit_spreads` | Corporate bond spreads to a fitted G-sec curve (`curvefit`): G-spread to the par curve on the bond's basis, the matched par spread against a risk-free twin with the bond's own dates and amortisation, z-spreads; to maturity, to worst or by SEBI's trigger rule; peer spread matrices from the price panel and each bond's spread against its peers; `cr-strat credit-spreads` ([`docs/analytics.md`](docs/analytics.md)) |
| `event_prices` | Spread paths around each event: pre-event level, first trade after, and anchors at t-60/+10/+60/+120/+250 |

## Known limits

- **Mandate rules are unverified.** Every rule in `config/mandates.toml` has
  `verify = true`. They come from secondary sources and have changed over time
  (SEBI relaxed the corporate bond and credit risk fund thresholds in 2020).
  Check each one against the current primary regulation before relying on results.
- **Ratings are tracked per issuer and instrument class, not per ISIN.** CRAs rate
  programmes, not individual ISINs. Credit-enhanced, market-linked and
  subordinated ratings are kept separate from plain bonds.
- **The MF parser is heuristic and so far tested only on synthetic layouts.**
  Real AMC files will turn up layouts it misses. The `_report.csv` written next
  to each holdings file lists every block it skipped or could not date.
- **Only MF holdings have a parser.** Insurer, NPS and EPFO holdings can be
  loaded in the same CSV format, but have no loader yet.
- **The trade loader maps columns by pattern.** It has not yet seen a real NSE or
  BSE file. The `_report.csv` from `load-trades` shows which header it used for
  each field. Check it on the first file from each venue, and check whether the
  same trade appears on both venues before combining them
  (`--dedupe-across-venues`).
- **Spreads use reported yields to maturity by default.** Bonds that trade to a call or put
  date will show a distorted spread in the price panel. `trade-check` recomputes each print's
  yield from its price and the bond's terms (and says which reading the reported yield
  matches), `price-panel --yield-source recomputed` builds the panel on those yields, with
  the spread, benchmark and tenor bucket at the workout date (a call date for a bond priced
  to worst), and `credit-spreads` measures spreads to maturity or to worst against a fitted
  curve; all need the bonds' terms tables, and none has seen a real print yet. A recomputed
  panel leaves out every bond without terms, so S8 and S9 peer groups shrink to the bonds
  typed in; `--yield-source recomputed_where_known` keeps those on their reported yields,
  their check `no_terms`.
- **Compounding defaults are assumptions.** Bond yields default to annual
  compounding and the benchmark to semi-annual. Set both to match your sources.
- **Price paths are in spread terms, not total return.** The event paths of
  `event_prices` have no carry or duration-hedged P&L; the return engine
  (`returns`) values what `bonds`, `corporates` and `floating` price, from
  marks the user supplies.
- **Some pricing rules have no primary source yet.** Interest in a G-sec's last
  coupon period, the STRIPS formula and the shut periods are named parameters
  set to the market-standard reading ([`docs/conventions.md`](docs/conventions.md)).
  Accrued interest is not capped in the 181-182 day half-years FIMMDA's
  30E/360 gives bonds paying on 29-31 August, so it can exceed the coupon.
  Whether CCIL caps it is an open question. The only priced SDL pairs are
  the 12 reissues of one RBI result (9 Nov 2026), with maturities taken as the
  anniversary of the first issue. The holiday list covers 2015 to 2026,
  declared complete in `holidays_mumbai_years.csv`; the calendar refuses other
  years unless told to treat their weekdays as business days. Reissues
  settling before the first coupon of a security issued off its coupon
  schedule (96 FIMMDA rows, 2015-2021) do not reproduce RBI's yields: the
  broken-period rule is not known. Corporate bonds and FRBs: next item.
- **The corporate bond and FRB pricer has seen no traded price.** It is tested on SEBI's worked examples
  (the NCS master circular's cash flows, SEBI's 2013 and 2016 illustrations), one issuer's monthly table,
  two issuers' printed yields and amounts, the RBI store's one 182-day T-bill result and synthetic bonds
  with answers computed independently. Its readings no source settles are named parameters marked
  UNVERIFIED (`docs/conventions.md`): which 366-day year SEBI's leap-year rule means (the allotment
  anniversary reproduces SEBI's illustration; one issuer's table follows calendar years), interest on a
  redemption paid early (full coupon by default, to the payment date for issues that say so), the payment
  calendar (every Saturday off: no banking calendar with working Saturdays is built), record dates rolled to
  the preceding day on the payment calendar (exchange and depository calendars are not built), pricing in
  the ex-interest window (negative accrued interest), due dates as flow dates from 18 Aug 2025 (SEBI's rule
  for trades on the RFQ platform, applied also to OTC reports, valuation and the engine) and payment dates
  before, yield to worst over calls only, rating steps from the next period, actual/365 for issues before
  Dec 2013 with postponed coupons and early redemptions accruing to the payment date, an allotment on 29
  February counting a year 366 days only when it holds a 29 February, terms as known at 00:00 on each row's
  trade date, reported prices clean and, for a partly redeemed bond, per 100 of the face outstanding (exchange
  part-redemption notices, read only through a search summary). FRBs: the base rounded half up from WAYs to
  4 decimals, an auction on the fixing day left out, a coupon computed once its last auction is within a week
  of the fixing day or a week has passed since that day, an unknown release time taken as 00:00 on the fixing
  day, the flat projection and the index-form discount margin, a projection base more than a period old
  refused unless allowed. The shipped coupon table is thin: of the half-years begun by 19 Nov 2026 it holds
  FRB 2028's coupons for 2 of 11, FRB 2031's for 3 of 18, FRB 2033's for 3 of 14 and FRB 2034's for 7 of 12,
  and the saved T-bill data hold one 182-day result, so most past periods are `unknown` (or, with
  `--frb-allow-stale`, projected from an old base and flagged: `stale_base` in `frb-coupons`, the positions'
  and the run's notes in `returns`) and the current coupons are projected: a
  back-test needs RBI's coupon history typed in, or the 182-day results the RBI store collects from its first
  poll (RBI grants no reuse licence: ask, `docs/data_enquiries.md` letter 3). Not built: FRB DV01 in the
  event ledger, a parser for RBI's FRB coupon releases, AT1 coupon discretion, partial exercises, irregular
  explicit schedules (a first or last stub is accepted).
- **Corporate spreads, trade checks and the corporate and FRB parts of the return engine have seen no real
  print or mark.** They are tested on synthetic bonds with answers computed independently (the corporates
  specification's vectors T17-T19 and T22) and demonstrated in scratch on typed-in bonds. UNVERIFIED choices,
  named parameters in the code: the trade check's 1 bp tolerance; the matched OIS rate restated to a corporate
  bond's annual basis and the amortising swap notional; the peer matrix's window and minimum issuers (as in
  `curves`); corporate bid-offer by rating bucket (the specification's guesses) and the exchange fee's start
  date, and the static `rating_bucket` of the terms when no rating public before a trade is given; GoI FRBs
  taken as ineligible for TREPS (corporate bonds are not on CCIL's list), so with a haircut row of yours a
  corporate bond is funded in corporate bond repo (the RBI store's segment, 15 bp over G-sec market repo on 13
  and 16 Nov 2026) and an FRB in market repo, else not at all; an FRB's engine DV01 is its rate DV01 (the
  spread DV01 is not reported there), bucketed at its next reset (FRB 2035's five-yearly one); a `run_book`
  whose FRB coupons cannot all be known while it holds them is refused before the run (a back-test's own FRB
  orders are checked as it reaches them); no default reference curve for an FRB (its price move stays
  unattributed). Not modelled: a corporate bond's default or missed payment, a partial call or put, a
  `frb_corp` bond with rating steps as well. Not built: S8's new-issue half (no loader for the electronic
  bidding platform's results, no new-issue concession measure). Before real use they need licensed data:
  exchange trades (NSE's Corporate Bond Market Data or BSE's, `docs/data_enquiries.md` letters 4 and 5),
  agency valuations for marks (ICRA Analytics, CRISIL, NSE Data & Analytics, FIMMDA: letters 6, 7 and 9),
  rating actions with times under each agency's terms (ICRA and India Ratings forbid building a database;
  CARE, Brickwork and Acuité need consent: `docs/data_sources.md`), FBIL's OIS and MIBOR, and the terms
  tables kept by the user (`docs/analytics.md`, Before real use).
- **The curve fit has seen no secondary-market price.** It is tested on
  synthetic bonds priced off known curves and run on free auction cut-offs
  (one-day SDL auctions, G-sec auctions pooled over a quarter), which show
  the fitter working but no rich/cheap signal. Its universe thresholds,
  the 3 bp model-risk line and the z-score window are unverified defaults;
  the spline cross-check picks its penalty by leave-one-out cross-validation
  because GCV failed on cross-sections without short bonds
  ([`docs/analytics.md`](docs/analytics.md)). In-fit residuals understate
  how rich or cheap the end bonds are (a bond made 5 bp cheap shows 0.06 bp
  in the NSS fit as the shortest of a set without bills, 3.6 bp as the
  longest): read them out of sample (`--residual loo`) where bills, an
  anchor or a close neighbour pin the refit near them. The very shortest
  and longest bonds' out-of-sample residuals are extrapolations: the
  spline's put that shortest bond at 7.89 bp, and NSS refits of all but
  equal objective can differ by tens of basis points there. The refits are
  local (started from the full fit): 4.76 to 5.18 bp for a 5 bp cheap
  bond where a multi-start refit gives 5.00. A large mispricing sways the
  cross-check at its neighbours; on a day with such a suspect bond (out of
  sample above 10 bp) a model-risk flag stands only if it persists without
  it (UNVERIFIED rule, chosen on synthetic outliers and bumps).
  Residuals still hold coupon, liquidity, FAR, stock, age and special repo
  effects: `drivers.csv` joins the free ones (coupon, age, FAR, reissues,
  stock) and `--control` regresses a day on them (UNVERIFIED form); liquidity
  and special repo need CCIL's licensed data. A curve fitted to FBIL data
  needs FBIL's Derived Product License (clause 5.1 of its terms).
- **OIS, asset swaps and the policy path have seen no OIS quote or MIBOR fixing.**
  FBIL's MIBOR and MIBOR-OIS need a subscription to store or back-test, and
  a curve bootstrapped from them a Derived Product License (clause 5.1), so
  everything is tested on synthetic quotes with known answers; OIS beyond
  5 years has no free source. Unverified rules are named parameters: the
  schedule's roll and stub rules, flat forwards between nodes, the asset
  swap's floating schedule and z-spread basis, simple repo interest, the
  haircut's cost at the repo rate, a decision moving MIBOR the next business
  day, the MIBOR-repo spread as a 20-day median (the previous regime's just
  after a change), the projected FY2027-28 MPC dates (52 weeks after
  FY2026-27's; add RBI's when published, around March 2027, with its date in
  `announced`), the share of CCIL's TREPS caps each position gets and T-bills
  outside them, a G-sec without a liquidity class taken as illiquid, CCIL's
  rule before 2 Jun 2026 as its FAQ summarises it (illiquid G-secs and SDLs
  together 20% of the liquid base from 16 Apr 2021, nothing before; the
  notice itself was not read), a funding spread of 0 (and none on a
  short's special rate). Haircuts and liquidity classes come only from a table
  you keep (CCIL's list is restricted); CCIL's caps leave an SDL-only book no
  TREPS funding, so the rest is funded at market repo. No bid-offer, CCIL
  charge or tax enters a carry. Whether the fund may pay OIS at all is open.
  Funding was run on the RBI store's four days of 13-16 Nov 2026, so no
  quarter-end, special repo or security repo rate has been seen; the call
  rate stands in for MIBOR in demonstrations only. The four asset-swap
  measures differ by up to 2 bp on one bond: S1 must pick one.
- **The return engine has seen no real price.** Marks, curves, MIBOR fixings and OIS quotes are licensed (CCIL,
  FBIL), so `returns` is tested on bonds priced off known synthetic curves and demonstrated on a synthetic month funded
  at the RBI store's four days of rates (13-16 Nov 2026, held flat after): plumbing, not a return. Its conventions
  without a source are named parameters marked UNVERIFIED: book value as the T+1 clean mark plus accrued interest to
  the mark date, the constant-curve roll-down, Legendre curve factors at 11 nodes, the missing-mark rules (curve plus
  last residual, then the last price held, a warning after five business days), overnight funding sized to the cash
  need in Rs 5 lakh lots with TREPS taken before market repo and its margin as equity, each night of a deal at its
  deal day's rate, decision and valuation at 20:00 (a decision before the valuation refused), next-day VWAP fills with
  a five-day wait, competitive auction orders filled up to 10% of the amount accepted, swaps under a hedging-only
  policy allowed up to 105% of their bonds' DV01. Costs default to CCIL's 2019 schedule (read by an earlier probe),
  bid-offer by CCIL class from a search summary of CCIL's page, stamp duty from secondary sources, and UNVERIFIED
  placeholders for SDL, bill, STRIPS, CP, CD and OIS spreads, NDS-OM fees and custody; income tax and TDS are left out.
  An access preset must be chosen: it decides whether S1 may repo, S3 and S5 may short and S7 may pay OIS for a view.
  For a fund trading only its own money without registering (an Unregistered Type I NBFC), the conservative preset is
  `unregulated_company`, with no repo, no shorts and OIS for hedging only, since RBI counts any outside liability as
  public funds; `nbfc_non_pd` assumes an RBI-registered NBFC ([`docs/analytics.md`](docs/analytics.md), Which access
  preset fits the fund; a question for counsel).
  Not built: CSA or margin on OIS, security-wise repo rates, the
  universe from the issuance ledger, an auction's own allotment beyond RBI's bid limits, back-tests from the command
  line, spline curves through the command line (docs/analytics.md lists what each strategy needs before real use).
  Key-rate DV01s, VaR and stress tests of its books are `cr-strat risk`'s ([`docs/risk.md`](docs/risk.md)).
- **The risk layer has seen no real history.** `cr-strat risk` is tested on synthetic books and histories with known
  answers and on the free auction table's coarse cut-off changes only: no licensed curve, spread or OIS history, no
  stress period. Its VaR needs 250 days of fitted curves with their public times (60 at the least: fewer is refused);
  credit VaR needs spread histories the fund keeps, else it is incomplete. The scenario sizes marked U (most but
  BCBS's IRRBB shocks and FRTB's weights) are examples for the fund's own policy, and the conventions marked
  UNVERIFIED (key tenors, the quantile rule, gap scaling, liquidity participation and spreads, the 0% floor) are
  listed in every run's `notes.txt`. Callable bonds are valued to maturity; JTD is not netted. Not built: limits,
  pre-trade checks, allocation, the daily report ([`docs/risk.md`](docs/risk.md)).
- **The event ledger has seen no price.** Its supply, metrics and windows run on the free auction files and the RBI
  store's four days of Nov 2026; concessions, residual moves and event paths need a licensed price panel and are tested
  on synthetic ones only. It inherits the issuance ledger's gaps (below): no G-sec auctions from 17 Feb 2024 to 3 Apr
  2025 or 26 Jun to 12 Nov 2026, no SDLs from 29 Jan 2025 to 8 Nov 2026, T-bills, switches and buybacks only from the
  store, so the stock is never complete and no share of supply in it is shown on the free data. Times before the store's
  first poll are rules, marked in each row: results at the end of the auction day, announcements at 18:00 the business
  day before (UNVERIFIED), MPC decisions at their era's time (from search summaries; December 2019 to 2023 not found,
  taken as 14:30) and minutes 14 days later; RBI's fixed result times are an option read through search summaries. Its
  readings (bid-cover on competitive bids, a switch's source leg at its last known yield, the stock's DV01 at its last
  cut-off, day 0 at a 20:00 decision) are named parameters, UNVERIFIED. FRBs have no DV01, the state calendar has
  quarter totals only, RBI's T-bill calendar has no parser (its lines can be typed in), MPC meetings ship for FY2026-27
  only, and trading by participant category (CCIL, licensed) is not built.
- **The issuance ledger has gaps.** The saved FIMMDA files hold no FY2024-25
  G-sec auctions and stop in May 2026, the Handbook edition in June 2026, the
  SDL file in January 2025; no history of switches or buybacks exists. The
  ledger marks each security's history complete or not, and reconciles with
  RBI's list (18 of 122 G-secs agree to the crore on 16 Nov 2026). A
  security with no known maturity date (FRB 2024) is redeemed on a
  placeholder date, the last business day of the year in its name.
- **The RBI parsers have seen one or two saved releases per type** (Nov
  2026, but the OMO releases are of Mar 2026, the GoI calendar of Sep 2026
  and the state calendar of Oct 2026; Money Market Operations on four
  consecutive days, Friday to Monday).
  A GoI result with several securities or for a new issue, an FRB result and
  the feed titles of full results were not sampled; a page whose labels
  differ is refused, not guessed. Not sampled either: Money Market
  Operations for a weekday holiday, with a repo auction, with a filled
  fixed-rate line or from before 30 Sep 2025; any repo (injection) auction
  or OMO sale (read only if the labels fit descriptions of such releases,
  and noted); the T-bill calendar, switch and buyback announcements,
  buyback results, MPC resolutions and USD/INR swap auction results, which
  are recognised but have no parser; MPC minutes and FRB coupon resets,
  which are not even recognised. RBI's SDL and STRIPS lists sit
  behind rbidocs' bot challenge and have no loader, so the master holds
  G-secs unless SDL ISINs are added with `--extra`.
- **The macro store keeps only what it reads.** MoSPI's API serves the
  latest value only and overwrites a first print when it is revised, so
  first prints exist only for releases the store polled; history read later
  is dated by the release table or rules and flagged `later` (it may include
  revisions), and is visible only from the release that set its latest
  value: quarterly GDP only from its year's final estimates, CPI 2012 from
  28 Feb 2015, IIP and WPI 2011-12 from 31 May 2017 (data/README.md).
  MoSPI's terms for a stored history are not known (letter 12 of
  `docs/data_enquiries.md`): it is fetched only when named, without raw
  copies, CPI down to groups unless `--cpi-levels items`. Treasury (it
  refused a probe on 18 Nov 2026, then answered the per-year path the same
  day) and EIA (the Brent series' copyright field is unread) are off by
  default. Release dates before April 2025, and every WPI date, come from
  rules: a "next working day" skips the Mumbai settlement holidays as a
  proxy for the central government's, a "previous working day" (GDP, IIP
  before 2025) knows weekends only; the 2025-26 dates are planned ones;
  launch dates of the older bases are from memory. BIS's rupee figure is
  dated by a rule from one calendar (weekly releases, a week of margin):
  H.10 is the rupee for back-tests. The parsers were checked on answers
  saved on 17 and 18 Nov 2026 and two live runs on 18 Nov 2026 (43
  requests) made by an earlier version of the store; the fixes after them
  were checked on the saved answers and with fake servers only. MoSPI's
  history queue, WPI's major-group filters, IIP's one-month tables and
  release-day polling were not run against the real API, and no block was
  met. Not built: consensus forecasts, the CGA fiscal accounts, GST, trade,
  PIB's first prints, FBIL's reference rate, a core CPI, Treasury's real
  yields, bills and long-term rates (their files were never sampled: the
  parser refuses them), the base-2010 CPI history (its parser exists),
  polling of MoSPI's release-calendar pointer.
- **The RBI store's history starts at its first poll.** The feed holds 10
  releases; earlier ones come only from pages saved by hand, a few a day.
  Possible gaps are listed, not filled: nothing fetches a release page, and
  a saved listing only closes the gaps of releases that do not matter. A
  newer version of a release replaces the stored one (old values are in
  `revisions.csv`, the first 8 cells, and in the raw copies); one the store
  held before is never taken back, and a revised row keeps the first
  publication time, flagged by `revised`. After a block the poller asks
  nothing more that India day and stops after a second block in a row, so
  collection depends on someone reading `rbi-store-status` every morning;
  with `--no-raw` a release the parser refuses is lost once the feed moves
  on. The poller was tried only against saved feeds through a fake
  transport: whether RBI answers its conditional request with 304, and
  whether its throttle counts feed requests, are not known (`polls.csv`
  will show both). Policy rates have a history only as far as someone types
  it in (`--policy-rates`; the MPC resolution is prose, not parsed); the
  corridor is the SDF and MSF rates of Money Market Operations, from the
  first day stored. Not built: the check that each OMO price reproduces its
  yield, and parsers for the kinds above that have none. RBI's permission
  to keep the history has not been asked.
