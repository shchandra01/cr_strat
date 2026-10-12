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
