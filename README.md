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
SDLs, STRIPS, T-bills, CP and CD, with their sources:
[`docs/conventions.md`](docs/conventions.md).

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

cr-strat rbi-releases data/raw/rbi/releases/*.html data/raw/rbi/feeds/*.xml --out-dir data/out/rbi
cr-strat sovereign-master --goi-list data/raw/rbi/goi_outstanding_*.html --far data/raw/rbi/far.html \
    --snapshots data/out/goi_outstanding.csv --out data/out/sovereign_master.csv
cr-strat auctions --fimmda-gsec data/raw/fimmda/Gsec_Auction_data_*.xlsx --fimmda-sdl data/raw/fimmda/SDL_*.xlsx \
    --handbook data/raw/rbi/hbs_t221.html --releases data/out/rbi --master data/out/sovereign_master.csv \
    --snapshots data/out/goi_outstanding.csv --out-dir data/out/ledger
cr-strat outstanding --ledger data/out/ledger/ledger.csv --date 2024-03-31 \
    --snapshot data/out/goi_outstanding.csv --out data/out/outstanding_20240331.csv
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
| `trades` | Loads NSE/BSE reported-trade files (trade-level or ISIN-day summaries), flags yield outliers, aggregates to value-weighted ISIN-day yields |
| `curves` | Benchmark curve interpolation with compounding conversion; matched-peer spread benchmark |
| `conventions` | Day counts (30E/360 for G-secs and SDLs, 30/360 US, Act/365F, Act/Act ICMA), yield-basis conversions, and each instrument type's conventions with their sources |
| `calendars` | Mumbai settlement calendar from the holiday list shipped in the package (`src/cr_strat/data/`): business days, date rolls, T+n settlement dates from an instrument's conventions; refuses years not declared complete |
| `bonds` | G-sec, SDL, STRIPS, T-bill, CMB, CP and CD pricing: coupon schedules, accrued interest, price and yield, duration, DV01, convexity |
| `htmlparse` | Tables, text and links from saved HTML pages with the standard library: nested tables kept apart, colspan and rowspan expanded; strict number and date cells; firewall-page detection |
| `rbi_releases` | RBI press releases from saved pages and the RSS feed: GoI, T-bill and SDL auction results, underwriting and switch results, auction announcements. A registry of release types; checks that refuse a release rather than misread it |
| `rbi_operations` | The other RBI releases of that registry: the daily Money Market Operations (one row per printed line: call, triparty and market repo rates and volumes, the SDF-MSF corridor, every liquidity operation, net liquidity, reserves), variable rate repo and reverse repo auctions, open market operations, the GoI and state issuance calendars |
| `sovereign` | Sovereign security names (G-sec, SDL, T-bill, FRB, green bond) parsed to a join key; state codes; RBI's GoI-outstanding and FAR lists; a master with one row per ISIN; name-to-ISIN matching |
| `auctions` | One table of G-sec, SDL and T-bill auctions from FIMMDA's yearly and SDL workbooks, RBI's Handbook Table 221 and RBI's releases: ISINs, settlement dates, and a check that the cut-off price reproduces the printed yield, which infers settlement and missing maturity dates |
| `issuance` | Ledger of issues, reissues, switches, buybacks and maturities per ISIN; outstanding stock on any date, backward from RBI's lists or forward, with completeness flags; reconciliation with RBI's list; supply by month and tenor bucket |
| `price_panel` | ISIN-day yields, spreads, tenor and as-of rating buckets |
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
- **Spreads use reported yields to maturity.** Bonds that trade to a call or put
  date will show a distorted spread, and nothing corrects for coupon effects or
  embedded options yet.
- **Compounding defaults are assumptions.** Bond yields default to annual
  compounding and the benchmark to semi-annual. Set both to match your sources.
- **Price paths are in spread terms, not total return.** There is no carry or
  duration-hedged P&L yet.
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
  broken-period rule is not known. Corporate bonds and floating-rate bonds
  are not priced yet.
- **The issuance ledger has gaps.** The saved FIMMDA files hold no FY2024-25
  G-sec auctions and stop in May 2026, the Handbook edition in June 2026, the
  SDL file in January 2025; no history of switches or buybacks exists. The
  ledger marks each security's history complete or not, and reconciles with
  RBI's list (18 of 122 G-secs agree to the crore on 16 Nov 2026). A
  security with no known maturity date (FRB 2024) is redeemed on a
  placeholder date, the last business day of the year in its name.
- **The RBI parsers have seen one or two saved releases per type** (Nov
  2026; Money Market Operations on four consecutive days, Friday to Monday).
  A GoI result with several securities or for a new issue, an FRB result and
  the feed titles of full results were not sampled; a page whose labels
  differ is refused, not guessed. Not sampled either: Money Market
  Operations for a weekday holiday, with a repo auction, with a filled
  fixed-rate line or from before 30 Sep 2025; any repo (injection) auction
  or OMO sale (read only if the labels fit descriptions of such releases,
  and noted); the T-bill calendar, switch and buyback announcements and
  buyback results, which have no parser. RBI's SDL and STRIPS lists sit
  behind rbidocs' bot challenge and have no loader, so the master holds
  G-secs unless SDL ISINs are added with `--extra`.
