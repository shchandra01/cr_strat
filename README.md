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

On top of these panels sits a **trading engine** for testing strategies the way
they would have traded in this market:

5. **Backtests.** A daily engine that marks every holding (matrix pricing off the
   last reported trade), fills orders only on days the bond actually traded and
   only up to a share of that day's reported value, charges rating-based bid-ask
   costs, impact and stamp duty, collects coupons and redemptions, and splits
   each day's P&L into income, roll, rates, spread and execution. A rates-hedged
   series isolates the credit return. Strategies see only what was public at each
   close, including MF portfolio disclosures only after their publication lag.
6. **Analytics.** Return, risk and significance statistics (Sharpe, Sortino,
   drawdowns, Newey-West t-stats, probabilistic Sharpe), trading statistics
   (turnover, costs, fill rates, stale marks), P&L attribution, exposures,
   holding episodes and a self-contained HTML tearsheet.
7. **Signal research.** Information coefficients and quantile spreads of any
   cross-sectional signal against forward excess returns, entered at the next
   reported trade so bid-ask bounce does not pass for alpha.
8. **Statistics and robustness.** Event regressions with clustered errors,
   parameter sweeps with in-sample/out-of-sample splits, and the deflated
   Sharpe ratio for the number of variants tried.

A synthetic market with planted effects (`cr-strat demo`) runs every step end
to end, so the machinery can be checked against a known answer before it sees
real data.

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

price_panel.csv ─┬──► backtest ──► nav, positions, fills, orders, episodes, summary, tearsheet.html
security master  │    (config/backtest_*.toml; + rating_events, event_ownership, TREPS)
  with coupons   ├──► sweep ──► one row per parameter set: in/out-of-sample stats, deflated Sharpe
G-sec curve ─────┴──► signals ──► signal_panel, signal_summary (IC, quantile spreads)
any research CSV ──► regress ──► OLS with HC1 or clustered errors
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

## Try it on synthetic data first

```bash
cr-strat demo --out-dir data/demo      # about 3 minutes
```

This simulates six years of an Indian corporate bond market (200 issuers, NSE/BSE
trade files, CRA rating actions, month-end MF portfolios, a G-sec curve), plants a
forced-selling overshoot after downgrades below AA+, and then runs every command
below on it. It writes tearsheets to `data/demo/out/backtests/*/tearsheet.html`,
signal research to `data/demo/out/signals/`, and the two forced-selling regressions
to `data/demo/out/regressions/`. Nothing in it is market evidence. It shows the
tools working and lets you check that the research recovers effects of known size
(`data/demo/raw/truth_events.csv`). `cr-strat synth` writes only the raw files.

## Backtesting

The engine needs the price panel, the security master **with coupon terms**
(`coupon_pct`, `maturity_date`, optionally `coupon_freq`, `issue_date`), the
benchmark curve, and for rating-aware strategies the rating actions and events.
Strategy and engine settings live in a TOML file. Data paths go on the command line:

```bash
cr-strat backtest --config config/backtest_event_reversal.toml \
    --panel data/out/price_panel.csv --securities data/raw/securities.csv \
    --benchmark data/raw/gsec_par_curve.csv --rating-actions data/raw/rating_actions.csv \
    --events data/out/rating_events.csv --ownership data/out/event_ownership.csv \
    --cash-rates data/raw/treps.csv --out-dir data/out/bt_event \
    --set strategy.hold_days=90 --set strategy.min_constrained_share=0.1
```

`--cash-rates` takes a CSV of `date, rate_pct` (TREPS weighted average or MIBOR);
without it cash earns the curve's shortest tenor. Outputs:

| File | Contents |
|---|---|
| `nav.csv` | Daily NAV and rates-hedged NAV, cash, P&L by component, portfolio duration, DV01, spread, stale-mark share |
| `positions.csv` | Daily holdings with marks, weights, risk and P&L by component |
| `fills.csv`, `orders.csv` | Every fill with mark, fill yield and cost; every order with its outcome (filled, expired, replaced, cancelled) |
| `episodes.csv` | One row per holding period: entry and exit spread, P&L, excess P&L over a duration-matched risk-free bond |
| `summary.csv` | All statistics, for the total-return and rates-hedged series, plus trading statistics |
| `attribution.csv`, `monthly_returns*.csv`, `drawdowns.csv`, `exposure_*.csv`, `concentration.csv` | Breakdowns |
| `tearsheet.html` | All of the above in one page, no external dependencies |

### How the engine trades

* **Timing.** Strategies run at the close of each rebalance date on data up to
  that close. Their orders go live `lag_days` business days later (at least 1),
  so a signal never trades on the trade that produced it.
* **Liquidity.** With `trade_days_only` (the default) an order fills only on a
  day the bond has a reported trade, and only up to `max_participation` of that
  day's reported traded value. Unfilled orders lapse after `order_ttl_days`.
  Sells are worked before buys, and buys never use more than the available cash.
  A position you want to exit can stay on the book for weeks if the bond does
  not trade, as it would in practice.
* **Prices.** Fills happen at the day's traded yield worsened by a half bid-ask
  spread for the rating bucket plus impact proportional to participation. Buys
  also pay stamp duty (0.0001%). Costs are applied in yield, so a 7-year bond
  pays more per basis point than a 1-year one. The default spreads are placeholders.
  Calibrate them to dealer quotes or the dispersion of reported trades.
* **Marks.** Between trades a bond keeps its last spread residual against its
  peer group and moves with the peer median (matrix pricing). Yield is the
  benchmark at its remaining tenor plus that spread. Price and risk follow
  SEBI's XIRR convention.
* **Attribution.** Every day's NAV change is exactly income (accrual and
  coupons) + roll (pull to par) + rates (duration x benchmark move) + spread
  (the rest of the yield move) + execution (costs against the mark) + cash
  interest. The tests hold this to under a rupee.
* **Hedged series.** `nav_hedged` replaces each bond's return with its excess
  over a duration-matched risk-free bond plus the cash rate: what a costless
  rates hedge would leave. Its return over cash is the strategy's credit return.

### Strategies

| Name | What it holds |
|---|---|
| `event_reversal` | Bonds of issuers that just crossed a mandate threshold downward, from `entry_delay_days` to `hold_days` after the public event. Filters: mandate, prior outlook/watch (`prior_signal`), landing grade, pre-event constrained share (once disclosed), dislocation versus new-rating peers at entry, early exit on a further downgrade. |
| `relative_value` | Bonds trading widest of matched peers (`rv_bps`), within a rating floor and tenor range, one per issuer. Exits when the bond trades back inside its peers. |
| `carry` | Widest-spread bonds above a rating floor, one per issuer, with a rank buffer so holdings are not churned. |
| `universe_index` | The most traded eligible bonds, equal or outstanding-weighted. Used as the benchmark for relative statistics. |

Each is a dataclass in `strategies.py`. To add one, subclass `Strategy` and implement
`target_weights(ctx)`, returning weights of NAV by ISIN. `ctx.snapshot()` gives
every recently traded bond with its spread, peer spread, liquidity and rating as of
`ctx.date`. `ctx.events` and `ctx.holdings` give the rating events and current
positions. Then pass the instance to `run_backtest`, or add it to `STRATEGIES` to
use it from a config file.

### Robustness

```bash
cr-strat sweep --config config/backtest_event_reversal.toml <data paths as above> \
    --grid strategy.hold_days=30,60,120,250 --grid strategy.prior_signal=with,without,any \
    --split-date 2022-12-31 --out data/out/sweep_event.csv
```

Every combination runs on the same market. The output has statistics for the
whole period and separately up to and after `--split-date`, plus each run's
**deflated Sharpe ratio**: the probability that its Sharpe beats the best
Sharpe expected from that many skill-less variants. Pick parameters on the first
period, judge them on the second, and distrust anything whose deflated Sharpe is
not well above 0.5.

## Signal research

```bash
cr-strat signals --panel data/out/price_panel.csv --securities data/raw/securities.csv \
    --benchmark data/raw/gsec_par_curve.csv --rating-actions data/raw/rating_actions.csv \
    --out-dir data/out/signals --horizons 20,60,120 --neutralize-by rating_bucket
```

Every Friday, every bond that traded that week gets its signals: spread, spread
over matched peers (`rv_bps`), spread change over 20 and 60 days, and trading
activity. The command then measures forward total return, excess return over a
duration-matched risk-free bond, and spread change at each horizon. Returns
start at the bond's **next reported trade**, as the backtester would enter.
Starting at the signal's own trade would let bid-ask bounce pose as
mean-reversion alpha (`--entry mark` shows how much). End marks staler than
`--max-end-age-days` are dropped. The summary reports the mean rank IC with
Newey-West t-statistics that allow for overlapping horizons, plus
quantile returns and the top-minus-bottom spread. `--neutralize-by rating_bucket`
ranks within rating buckets, so a signal is not rewarded just for picking lower ratings.

## Regressions

`cr-strat regress` runs OLS on any research CSV, with HC1 or clustered errors:

```bash
cr-strat regress --data data/out/event_prices.csv --merge data/out/event_ownership.csv \
    --where "mandate == 'mf_corporate_bond_fund' and direction == 'down'" \
    --assign "reversal_bps = first_post_abn_bps - p120_abn_bps" \
    --y reversal_bps --x constrained_share,prior_signal --cluster issuer_id
```

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
| `bond_math` | Fixed-coupon cash flows, XIRR prices and yields (SEBI convention), Act/Act accrued, duration, convexity, DV01; vectorised across bonds |
| `market` | Point-in-time market view: snapshots of recently traded bonds with peer spreads, and matrix marks for any bond on any date |
| `execution` | When orders fill, how much, and at what cost |
| `strategies` | Strategy interface, point-in-time context, and the built-in strategies |
| `backtest` | Daily engine: cash, coupons, marks, P&L attribution, orders and fills |
| `analytics` | Performance, risk, trading, attribution, exposure and episode statistics |
| `research` | Signal panels, forward returns, information coefficients, quantile spreads |
| `stats` | OLS with robust/clustered errors, Newey-West, probabilistic and deflated Sharpe, block bootstrap |
| `runner` | Backtest config files, output writing, parameter sweeps |
| `report` | Self-contained HTML tearsheet |
| `synthetic`, `demo` | Synthetic market with planted effects, and the end-to-end demo |

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
- **Event price paths are in spread terms.** Total and duration-hedged returns
  come from the backtester and from `signals` (`tr`, `xr`), not from `event-prices`.
- **Bond terms are plain fixed-coupon bullets.** Calls, puts, floaters,
  amortisation, step-ups, record dates and ex-interest trading are not modelled.
  Coupons are a fixed share of the annual rate per period (Act/Act ICMA), where
  SEBI's rule pays actual days over 365. Marks and fills settle on the trade date,
  not T+1. Leave instruments with these features out of the security master,
  or give `coupon_pct` only for bonds whose terms are plain.
- **Marks between trades are model prices.** A bond's peer group is fixed at its
  last trade, so a downgrade reaches its mark only when it trades again. `nav.csv`
  reports how much of the book was marked off trades older than `stale_days`.
  Read performance in illiquid stretches with that in mind.
- **Execution assumes reported trades mean you could have traded.** You could only
  have traded on days the bond printed, at the day's average yield less the
  modelled costs. The default half-spreads and impact are placeholders, not
  estimates.
- **Long only, no repo.** No shorting, and no financing beyond `max_gross` with a
  flat borrow spread. The rates hedge is an idealised overlay with no cost or basis.
- **Defaults are not modelled specially.** A defaulted bond is valued from its
  reported yields like any other. Recoveries, write-offs and side-pocketing are
  not simulated.
- **Survivorship comes from your security master.** Include matured,
  redeemed and defaulted ISINs, or the universe will look safer than it was.
- **No taxes.** TDS on coupons and capital gains are ignored.
- **Synthetic results are not evidence.** The demo's numbers come from a
  simulation with planted effects. They test the code, not the strategy.
