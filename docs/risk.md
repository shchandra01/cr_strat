# Risk: sensitivities, VaR and ES, stress tests, liquidity, limits, allocation and the daily report

What [`risk.py`](../src/cr_strat/risk.py) and [`stress.py`](../src/cr_strat/stress.py) measure for the books the
return engine runs ([`analytics.md`](analytics.md), The return engine and costs), with `cr-strat risk` and
`cr-strat var-backtest`, as of 24 Nov 2026. Everything was built and tested on synthetic books and histories with
known answers and on the free auction table (labelled coarse); no licensed price, curve, OIS quote or spread history
has been through it. On these numbers, [`limits.py`](../src/cr_strat/limits.py),
[`portfolio.py`](../src/cr_strat/portfolio.py) and [`daily.py`](../src/cr_strat/daily.py) build the limits, the
pre-trade gate, capital allocation and the daily paper-trading report (`cr-strat limits`, `pre-trade`, `allocate` and
`daily-report`), tested the same way; every limit level shipped is an EXAMPLE for the fund's own risk policy to
replace, and the rules that depend on the fund's legal form (unsettled: a question for counsel) are marked for counsel.

- [Running it](#running-it)
- [What each number means](#what-each-number-means)
- [Method](#method): the book, sensitivities, factor histories, VaR and ES, back-tests, stress scenarios, liquidity
- [Limits](#limits): the table, the metrics, the checks, the breach log, de-risking, what depends on the legal form
- [The pre-trade gate](#the-pre-trade-gate): the preset's rules, the order's risk, the limits; back-tests through it
- [Allocation and sizing](#allocation-and-sizing): weights, budgets, rebalancing, sizing, hedges, contributions
- [The daily process](#the-daily-process): the paper fund's configuration, files, report and daily routine
- [Look-ahead and determinism](#look-ahead-and-determinism)
- [Conventions and what the fund must decide](#conventions-and-what-the-fund-must-decide)
- [Data needed before real use](#data-needed-before-real-use)
- [What was checked, and what was not](#what-was-checked-and-what-was-not)
- [Sources](#sources)

## Running it

```bash
cr-strat returns ... --out-dir data/out/returns          # the engine's positions.csv, nav.csv, funding.csv
cr-strat risk --positions data/out/returns/positions.csv --nav data/out/returns/nav.csv \
    --funding data/out/returns/funding.csv --trades data/raw/trades.csv --nss-params data/out/nss_params.csv \
    --marks data/raw/marks.csv --access nbfc_non_pd --date 2026-12-14 --var-history data/out/var_history.csv \
    --out-dir data/out/risk_20261214
    # --as-of 2026-12-14T18:30: everything as known then; --stressed, --liquidity-horizons: more ES rows
    # --ois-quotes, --fixings, --swaps: OIS hedges; --corporate-terms (and its tables), --ratings, --issuers:
    # corporate bonds; --factor-history: spread factor levels; --scenarios: your own scenario rows
cr-strat var-backtest --var-history data/out/var_history.csv --nav data/out/returns/nav.csv \
    --attribution data/out/returns/attribution.csv --out-dir data/out/backtest
```

`cr-strat risk` reads one day of the engine's positions (`--date`, default the last in the file) as of a time
(`--as-of`, default that day at the preset's valuation time, 20:00) and writes the tables below to `--out-dir`, each
with the documented columns, plus `notes.txt` (what was flagged, refused or not applicable, and every UNVERIFIED
convention used); `--write-history` adds `factor_changes.csv` (the factor changes the VaR used) and `--var-history`
adds the day's VaR rows to a history file (re-running a day replaces its rows). Inputs the positions file lacks come
from the flags the engine takes (`--trades` or `--master` for bond terms, `--nss-params` with `public_from` for the
G-sec curve and its history, `--ois-quotes` and `--fixings` for swaps, the corporate and FRB tables) and from tables
you keep ([data/README.md](../data/README.md), Risk inputs). `--access` must be given: it decides whether funding
stresses apply. Exit codes: 0 written (a VaR refused for a short history is written as a `refused` row, with the
reason, and still exits 0), 1 no position on the date, 2 bad input (the message names the file, flag or row).

`cr-strat var-backtest` pairs each day's one-day VaR with the next business day's P&L and writes `backtest.csv` and
`backtest_summary.csv`; exit 0 with at least one pair, 1 with none, 2 on bad input.

The library route is the same: `risk.run_risk(positions, RiskContext(...))` returns a `RiskResult` whose tables
`risk.write_risk` writes; `risk.book_from_snapshot(snapshot, ctx)` builds the book from a back-test's
`returns.Snapshot` at its decision time, so a strategy can read its own risk inside `run_backtest` without changing
the engine's outputs (tested: the back-test's files are byte-identical with and without a strategy reading it).

## What each number means

Signs: a **sensitivity** is the rupees lost when its factor rises 1 bp, positive for a long bond (a payer swap's is
negative). A **P&L** is the rupee gain (a loss is negative). **VaR** and **ES** are positive losses. `*_years` is a
sensitivity over 0.01% of NAV (duration-equivalent years of NAV); `*_pct_nav` is percent of NAV; both are blank
without `--nav`. Every table has `date` (the positions' day) and `as_of`. A scope is the `fund`, a `book` or a
`strategy` (books mapped by `--strategy-books`, else each book its own strategy).

| File | One row per | Columns and meaning |
|---|---|---|
| `position_risk.csv` | position (settled, or pending to its settlement) | `face`, `settlement_date`, `yield_pct`, `dirty_price`, `value` (the engine's); `reference_curve` and `z_spread_bp` (to it); `dv01_yield` (the engine's yield DV01, recomputed), `dv01_curve` (the sum of its key rates), `cs01` (corporate bonds, CP, CD), `frb_index_dv01`, `frb_spread_dv01` and `next_reset` (GoI FRBs), `convexity_yield` and `convexity_curve` (rupees per bp squared); `maturity_date`, `tenor_bucket`, `issuer_id`, `group_id`, `sector`, `rating_bucket` and `rating_source` (`ratings` as known at `as_of`, `static` from the terms, `sovereign`, `unrated`), `seniority`, `lgd`, `jtd`; `liquidity`, `mark_source`, `mark_age_days`; `flags` |
| `sensitivities.csv` | position and factor | `factor` (e.g. `gsec:10y`, `ois:2y`, `ois_par:2y`, `corp:AA:3-5y`, `sdl_spread:3-5y`, `frb_dm:<isin>`, `tbill182`, `resid:<isin>`), `factor_class`, `curve`, `tenor` (years), `sensitivity` (Rs per bp), `method` (`analytic`, `central`, `par_quote`, `frb_index`, `frb_spread`, `yield_proxy`, `yield`), `basis` (`zero`, `par`, `spread`, `yield`) |
| `key_rates.csv` | scope, curve and key tenor | `krd` (Rs per bp; OIS par-quote DV01s under curve `ois_par`), `krd_years` |
| `credit.csv` | scope, dimension and key | CS01 of the credit positions by `issuer`, `group`, `sector`, `rating_bucket`, `tenor_bucket` and `rating_tenor`: `cs01`, `cs01_years`, `market_value`, `pct_nav`, `jtd`, `jtd_pct_nav`, `positions` |
| `jtd.csv` | scope and issuer | `face`, `market_value`, `lgd`, `jtd` (the loss if the issuer defaulted now), `jtd_pct_nav` |
| `risk_summary.csv` | scope, dimension and key | every number summed by `total`, `kind`, `tenor_bucket`, `issuer`, `group`, `sector`, `rating_bucket`: `positions`, `face`, `market_value`, `pct_nav`, `dv01_yield`, `dv01_years`, `dv01_curve`, `cs01`, FRB DV01s, convexities, `jtd` |
| `var.csv` | scope, method, horizon, window and level | `method` (`hs_sensitivity`, `hs_full`, `parametric`), `horizon_days` and `horizon_rule` (`overlapping`, `sqrt_time`, `liquidity_horizons`), `window` (`current`, `stressed`), `window_start`, `window_end`, `n_obs`; `alpha_var`, `var`, `var_pct_nav`, `alpha_es`, `es`, `es_pct_nav` (two rows per figure: VaR 99% with ES 97.5%, and VaR 97.5% with ES 99%); `coverage` (the share of the scope's absolute sensitivity whose factors have a history), `uncovered_rs_per_bp`; `status` (`ok`, `short_history`, `incomplete`, `refused`) and `flags` (the reason of a refusal, `coarse`, `proxied`, `ewma:<n>`) |
| `var_components.csv` | VaR figure and component | Euler contributions of each position (`book\|isin\|state`), book, strategy and factor class: `var_contribution`, `es_contribution` (summing to the figure) |
| `stress.csv` | scenario, scope and component | `component` `total`, `rates`, `spread`, `frb`, `funding`, `liquidity` and `interaction` (total less the sum of the components alone): `pnl`, `pnl_pct_nav`, `positions`; `status` (the statuses of its rows, e.g. `P;V;coarse`); `flags` (`not_applicable: ...` with a NaN P&L where a funding scenario cannot apply) |
| `stress_positions.csv` | scenario and position | `pnl` and its parts, `interaction`, `method` (`full`: a bond on its curve or a swap; `yield`; `yield_proxy`; `frb_full`) |
| `stress_worst.csv` | scope and scenario family | the scenario with the lowest total: `scenario_id`, `pnl`, `pnl_pct_nav` |
| `liquidity.csv` | position | `adv` (Rs a day) and `adv_source` (`marks`, `table`, `proxy_class`, `proxy_issue`), `participation`, `days_to_liquidate` (`inf` with no ADV, flagged `no_adv`), `half_spread_bp`, `liquidation_cost`, `stressed_cost`, `illiquid` |
| `liquidity_summary.csv` | scope | `share_1d`, `share_5d`, `share_20d` (shares of NAV saleable within 1, 5, 20 business days), `max_days`, `mean_days` (value-weighted), `illiquid_share`, `liquidation_cost`, `stressed_cost`, `lvar` (one-day 99% VaR plus the stressed cost) |
| `funding_liquidity.csv` | book (and funding scenario) | `legal_form`, `applicable`; `borrowed`, `limit`, `headroom`, `haircut_equity`, `margin`, `idle_cash` (the night's funding), `pending_purchases`, `settlement_cover` (cash over purchases still to settle); per scenario `extra_interest`, `extra_equity`, `treps_lost`, `shortfall`; `alm_1_7d`, `alm_8_14d`, `alm_15_30d` (cumulative mismatch over cumulative outflows) |
| `factor_changes.csv` | date and factor | `previous_date`, `change_bp`, `gap_days`, `flag` (`scaled_gap`, `extrapolated_node`, `default_time`, `coarse`) |
| `var_history.csv` (`--var-history`) | date, scope, method, horizon, level | `alpha_var`, `var`, `alpha_es`, `es`, `n_obs`, `coverage`, `status` |
| `backtest.csv` | VaR day | `var_date`, `pnl_date` (the next business day), `var`, `pnl_actual`, `pnl_hypothetical`, `exception_actual`, `exception_hypothetical`, `missing` |
| `backtest_summary.csv` | scope and method | `observations`, `exceptions_actual`, `exceptions_hypothetical`, `exceptions` (the larger), `expected`, `kupiec_lr`, `kupiec_p`, `n00`-`n11`, `christoffersen_lr`, `christoffersen_p`, `lr_cc`, `lr_cc_p`, `cumulative_probability`, `zone` (`green`, `amber`, `red`), `multiplier` (at exactly 250 observations), `notes` |

## Method

### The book

The positions file is the engine's (`returns.POSITION_COLUMNS`). Each row's bond is rebuilt as it was known at
`as_of` (`returns.instruments_as_known`: its terms, the announced FRB coupons, rating actions and exercised options
public by then) and priced for the row's settlement at the row's yield; a purchase still to settle is measured as the
bond it will be. OIS rows come with their trades (`--swaps`) and are valued on the day's OIS curve public by `as_of`,
rolled to the day as the engine rolls it. A row whose terms are missing or whose bond cannot be priced as known then
is refused, naming the row and input. A bond whose reference curve is missing is measured at its own yield
(`yield_proxy`, flagged `no_curve`); a curve from an earlier day is used and flagged `stale_curve`; a recomputed
yield DV01 that differs from the engine's is flagged `dv01_differs` (the positions were valued on other terms).

### Sensitivities

- **Key rates**: the zero rates of the position's reference curve seen from its settlement (the G-sec NSS fit; SDLs,
  corporate bonds and bills on the curve the attribution's reference map names; swaps on the OIS curve) at the key
  tenors 0.25, 0.5, 1, 2, 3, 5, 7, 10, 15, 20, 30, 40 and 50 years, each moved with a triangle weight between its
  neighbours and held flat beyond the first and last, so that the key rates sum to the parallel curve DV01. Fixed
  flows are differentiated analytically (`t x PV x 1e-4`); swaps by central differences of +-0.5 bp of the zero rates,
  and also by +-0.5 bp of each OIS par quote with the curve rebuilt (`ois_par`).
- **Yield and curve DV01**: the yield DV01 is the engine's, on the bond's own compounding; the curve DV01 is on
  continuous zero rates, so it is larger by about `1 + y/f` (3.9% for a semi-annual G-sec at 7.7%). Both are reported.
- **CS01**: the z-spread to the reference curve moved +-0.5 bp, for corporate bonds, CP and CDs, mapped to the
  factor `corp:<rating bucket>:<tenor bucket>` (CDs `cd:<tenor bucket>`); an SDL on the G-sec curve has an
  `sdl_spread:<tenor bucket>` sensitivity instead (`sdl_mode own_curve` puts it on an SDL curve). Callable bonds are
  valued to maturity: there is no option model.
- **GoI FRBs**: the index DV01 (`floating.index_dv01`) placed on the G-sec key rates at the next reset (or on a
  `tbill182` factor when its history is given) and the discount-margin DV01 (`floating.spread_dv01`) on
  `frb_dm:<isin>`; an FRB's risk is mostly the margin (about 94 times its index DV01 for FRB 2031 in November 2026).
- **Convexity** at the yield and on the curve, rupees per bp squared.
- **Jump to default**: `max(LGD x face + (market value - face), 0)` per position, LGD 75% senior, 100%
  subordinated (AT1 and tier 2 from the terms), 25% covered, senior when neither the seniority table nor the issuer
  table says otherwise; for corporate bonds, CP and CDs (SDLs with `jtd_states`; GoI securities never); a short's JTD
  is negative and is never netted against a long.

### Factor histories

The VaR's factors are the key rates of each curve, the spread factors and the optional residual factors. Their daily
changes come from levels public by `as_of`: the history of NSS fits in `--nss-params` (each fit counted from its
`public_from`, else 18:00 on its date, flagged `default_time`; a key tenor outside a fit's range takes the nearest
in-range key rate's change, flagged `extrapolated_node`), OIS curves at the OIS key tenors (no fill), and the factor
levels you keep (`--factor-history`). A change over a gap of 2 to 5 business days is kept, scaled by `1/sqrt(g)`
(`--gap-rule scale`; or `drop`, `raise`); a longer gap breaks the series.

### VaR and ES

For each scope and method:

- **`hs_sensitivity`**: each historical day's factor changes times today's sensitivities (`--delta-gamma` adds half
  the curve convexity times the square of the day's duration-weighted parallel move).
- **`hs_full`**: each historical day revalued in full: bonds on their curve shifted by the day's key-rate changes
  (`--scenario-shape full_curve`: by the whole fitted change) and their spread factor; FRBs at the shifted index and
  margin; swaps on the shifted OIS curve.
- **`parametric`**: normal, `sigma^2 = s' Sigma s`, with `Sigma` the EWMA covariance of the factor changes (lambda
  0.94, at most 550 observations, the weights normalised to sum to one) or the sample covariance.

The window is the latest 250 dates (`--window`) on which every factor of the scope with at least 60 observations
(`--min-obs-hard`) has a change; a factor with fewer is left out and the figure is `incomplete` (coverage below one)
unless the proxy table maps it to a factor with history (`proxied`). With fewer than 60 observations the figure is
refused, with the count; below 250 (`--min-obs`) it is flagged `short_history`. VaR at level `a` is the
`ceil(n(1 - a))`-th largest loss (`--quantile-rule`; `floor_plus_one` and `interpolate` also offered) and ES the mean
of the worst `m = n(1 - a)` losses, a fractional last one counted in part (the tail size held as an exact fraction).
The 10-day figures sum overlapping runs of 10 daily changes (`--horizon-rule sqrt_time` scales the one-day figure
instead); the parametric method always scales by the square root of time.

**Stressed ES** (`--stressed`): the window of `window` observations, searched over the whole history, in which the
book's 10-day ES on the reduced set of factors (those whose history starts within 30 days of the earliest) is
largest; the stressed ES is the full set's current ES scaled by the ratio of the reduced set's stressed to current ES,
the ratio floored at one (Basel's MAR33.6 rule). **Liquidity horizons** (`--liquidity-horizons`): the 10-day ES
cascaded over the horizons of each factor class (G-sec, SDL and OIS rates 10 days, FRB margins 20, CD and corporate
spreads 40, below-investment-grade 60) as MAR33.4 does. **Components**: each figure split by position, book, strategy
and factor class (Euler: parametric exactly; historical ES by the tail scenarios' weights exactly; historical VaR by a
band of five scenarios around the VaR's, rescaled).

### Back-tests

The one-day VaR of business day `d` against the P&L of `d + 1`: the actual P&L is the engine's `nav.csv` `pnl`, the
hypothetical one the attribution's market lines on positions held from the previous close (accrual, pull to par,
roll-down, curve factors, rich/cheap, convexity, OIS carry; trades, costs, funding and the benchmark left out). An
exception is a P&L strictly below minus the VaR; a missing VaR or P&L counts as one. Actual and hypothetical
exceptions are counted apart and the larger count used. Over the last 250 pairs: Kupiec's proportion-of-failures test
(chi-squared, one degree), Christoffersen's independence and conditional-coverage tests, and the Basel traffic light:
green while the cumulative binomial probability of the count is below 95%, amber below 99.99%, red above (green 0-4
exceptions at 250 observations, amber 5-9 with multipliers 1.70 to 1.92, red from 10); with fewer than six
observations no exception is always green (the probability of none is already above 95% there).

### Stress scenarios

A scenario is a set of rows of the scenario table (`src/cr_strat/data/stress_scenarios.csv`, columns
`scenario_id, name, family, component, selector, shape, size_bp, tenor_years, multiplier, horizon_days, floor_pct,
available_from, effective_from, source, status, notes`), each a **component** applied with the others:

| Component | What moves | Shapes |
|---|---|---|
| `all_rate_curves`, `gsec_curve`, `ois_curve` | the zero curves (all together with their basis held, or one) | `parallel`, `short`, `long`, `steepener`, `flattener`, `key_rate`, `yield_parallel` (each bond's own yield) |
| `sdl_spread`, `corp_spread` | the z-spread of SDLs or of corporate bonds, CP and CDs | `add` (bp), `scale` (a multiple of the bond's spread) |
| `rating_migration` | notches down, the spread moved to the new rating's (a peer matrix, else the ladder) | `notch` |
| `frb_index`, `frb_dm` | a GoI FRB's index and discount margin | `add` |
| `repo_rate`, `haircut`, `treps_cap`, `market_repo` | the night's funding (presets that borrow) | `add`, `scale`, `earlier_rule`, `off` |
| `bid_offer`, `adv` | exit costs and volumes | `scale` |

The IRRBB shapes are BCBS's: `short = size e^(-t/4)`, `long = size (1 - e^(-t/4))`, the steepener
`-0.65 |short| + 0.9 |long|` and the flattener `+0.8 |short| - 0.6 |long|`, on the INR sizes (parallel 400 bp, short
500, long 300). Key-rate points of one scenario are joined by the triangles and held flat beyond the ends. A shock
never takes a zero rate below `floor_pct` (0%). A `selector` (`key=value` pairs joined by `;`, alternatives by `|`:
`rating_bucket=AA+|AA`, `sector=financials`, `kind`, `tenor_bucket`, `frtb_sector`, `seniority`, `issuer`, `book`,
`isin`) narrows a row. Your table (`--scenarios`) replaces shipped scenarios with the same id and adds the rest
(`--no-default-scenarios`: yours only). A row with an unknown component, shape, family or selector key is refused
naming the closest known one.

Each scenario is **revalued in full**: bonds on their curve seen from their settlement, `P' = sum CF_i D(t_i)
e^(-shift(t_i) t_i) e^(-(z + dz) t_i)`; a bond without its curve at its yield moved by the shift at its Macaulay
duration (`yield_proxy`); an FRB with its projected coupons and discount rate moved by the shift at its next reset;
swaps through `ois.ois_npv` on the shifted OIS curve. Funding P&L is the extra interest of a repo-rate shock on each
long's share of the night's borrowing (`positions.funded`, `horizon_days` nights); liquidity P&L the exit cost at the
scenario's half spreads. Under a preset that cannot borrow (`unregulated_company`), funding rows are
`not_applicable`, with a NaN P&L and the reason, never a zero.

Shipped scenarios (37, every row with its source and status):

| Family | Scenarios | Status |
|---|---|---|
| `rates_irrbb` | `IRRBB_INR_PARALLEL_UP`, `_PARALLEL_DOWN`, `_STEEPENER`, `_FLATTENER`, `_SHORT_UP`, `_SHORT_DOWN` (BCBS's six, available from 30 Apr 2016) | V |
| `rates` | `RATES_PAR_UP_100`, `RATES_PAR_DOWN_100`, `RATES_PAR_UP_50`, `YIELD_UP_100`, `GSEC_OIS_BASIS_25`, `MPC_SURPRISE_25`, `MPC_SURPRISE_50`, `SDL_SPREAD_UP_25` | U examples |
| `credit` | `CS_RATING_LADDER` (AAA 50 bp, AA+ and AA 100, AA- 150, A 200, BBB 300, below 500), `_HALF`, `CS_FINANCIALS_X2`, `DOWNGRADE_1_NOTCH`, `DOWNGRADE_2_NOTCH_FORCED` | U examples |
| `credit` | `FRTB_CSR` (FRTB's credit spread risk weights by sector and grade, read as spread shocks; from 31 Jan 2019) | V |
| `frb` | `FRB_INDEX_IRRBB` (V), `FRB_INDEX_JUL2013` (P), `FRB_DM_25`, `FRB_DM_50` (U) | V, P, U |
| `funding` | `REPO_UP_200_10D`, `HAIRCUT_UP_2PP`, `HAIRCUT_X2`, `TREPS_CAP_EARLIER`, `NO_MARKET_REPO` | U examples |
| `liquidity` | `BIDASK_X3`, `BIDASK_CORP_X5`, `ADV_HALF` | U examples |
| `historical` | `H2013_JUL_TIGHTENING`, `H2013_TAPER`, `H2018_SELLOFF_ILFS`, `H2020_COVID_CREDIT`, `H2022_HIKES` (typed-in moves, each available only from its episode's end) | P, V, coarse |

The historical episodes are moves typed in from secondary sources (press reports through search summaries, BIS's
policy-rate series) and the free auction table's same-security cut-off changes (coarse); they are not a history of
fitted curves. AMFI's stress parameters for debt funds were not found.

### Liquidity

Days to liquidate `|face| / (participation x ADV)`, 20% of a day's volume; ADV from the marks' `volume_cr` over the
last 20 business days (days without a traded mark count as zero volume), else your ADV table (`--adv`, its latest row
public by `as_of`), else a proxy by kind and CCIL class (corporate bonds a share of the issue's outstanding by
rating), flagged `proxy_adv`; none at all gives `inf` days (`no_adv`). The exit cost is `|face| / 100 x half spread x
DV01` with the half spread of the cost table's fill rule (`costs.cost_rule`), stressed three times. A position is
illiquid when it is a G-sec of CCIL's illiquid class or none, an SDL, a corporate bond below AA- or more than 20
days to liquidate. **Funding liquidity**: for a preset that cannot borrow, the settlement cover of purchases still to
settle; for one that borrows, the night's borrowing against its limit, the haircut equity, the funding stresses and the
three structural liquidity ratios of RBI's ALM Directions for NBFCs (cumulative mismatches at most 10%, 10% and 20% of
cumulative outflows in 1-7, 8-14 and 15-30 days), securities slotted by their days to liquidate.

## Limits

[`limits.py`](../src/cr_strat/limits.py) reads a dated table of limits, measures each limit's metric on a day's book,
checks it and carries a breach log from one day to the next. The same code runs in `cr-strat limits` (on the
directories `cr-strat risk` and `cr-strat returns` write), in the daily process and in the pre-trade gate (on the book
with the proposed orders added).

```bash
cr-strat limits --risk-dir data/out/risk_20261214 --engine-dir data/out/returns --date 2026-12-14 \
    --limits data/raw/limits.csv --access nbfc_non_pd --marks data/raw/marks.csv \
    --nss-params data/out/nss_params.csv --trades data/raw/trades.csv \
    --breach-log data/out/limits_20261211/breach_log.csv --out-dir data/out/limits_20261214
    # --limits example: the shipped examples (every row EXAMPLE); --board-resolution-date: L03;
    # --stock, --auctions, --ratings, --corporate-terms: the concentration and when-issued metrics
```

It writes `metrics.csv` (`METRIC_COLUMNS`: each metric, scope and key the limits in force ask for, with
`inputs_complete` and a detail), `limit_checks.csv` (`LIMIT_CHECK_COLUMNS`: one row per limit and key with the value,
levels, utilisation and status), `breaches.csv` (the day's open and cured episodes) and `breach_log.csv` (the whole
log: the next day's `--breach-log`). Exit 0 written, 1 no NAV on the date, 2 bad input; with EXAMPLE rows in force the
first line printed is the EXAMPLE warning.

**The table** (`LIMIT_COLUMNS`; the format is in [data/README.md](../data/README.md), Limits, orders and allocation
tables). One row per limit: `limit_id` (unique across the tables used together), `metric` (one of the 33 below),
`scope_type` (`fund`, `book` or `strategy`) and `scope` (a name, or `*` for each scope of the type), `selector`
(`key=value` pairs joined by `;`, alternatives by `|`, `*` to check each value separately, as in
`sector=*;kind=corporate|cp`; besides each metric's own keys, `abs=yes` compares absolute values and `min_assets_cr`
and `max_assets_cr` make a row apply only from, or below, a size of total assets in crore), `unit`, `direction` (`max`
or `min`), `warning_level` and `hard_level` (a blank warning is the hard level), `legal_form` (`any` or a preset),
`effective_from`, `effective_to`, `approved_by`, `approval_date`, `approval_ref`, `cure_business_days`, `action`
(`block_increase`, `derisk`, `report` or `halt`), `policy_status` (`EXAMPLE`, `DRAFT` or `APPROVED`), `source`,
`status` (V, P or U), `counsel` and `notes`. A table is refused, naming the line, for a duplicate id, an unknown
metric, unit or selector key (with the closest name), a warning level beyond the hard one, a blank hard level, or two
rows of one limit (metric, scope, selector, unit, direction and legal form) in force on the same day. A row is in
force on day `d` when `effective_from <= d < effective_to`, its `approval_date` is on or before `d` and its legal form
is `any` or the run's preset, so a limit approved later never applies to an earlier day.

**The metrics** (`limits.METRICS` gives each one's keys, units, direction and what it reads):

| Family | Metrics |
|---|---|
| Market | `dv01` (Rs per bp or years of NAV, by tenor `bucket`), `krd` (by `curve` and `tenor`), `cs01` (by rating, issuer, group, sector, tenor bucket, kind), `var` and `es` (the risk run's; the gate's parametric), `stress_loss` (a scenario, or the worst of a family), `leverage` (gross bonds or repo over NAV) |
| Concentration | `exposure` (% of NAV or Rs, by issuer, group, sector, rating, kind or ISIN), `isin_share` (face over the outstanding stock), `rating_floor` (the worst notch held, or the share of NAV below a grade; `of=purchases` reads each credit purchase), `max_tenor`, `jtd` |
| Liquidity | `liquid_share` (NAV saleable within `days`), `illiquid_share`, `days_to_liquidate` |
| Performance | `drawdown` (the NAV index below its peak), `loss_window` (P&L over `days` business days over NAV at the start) |
| Legal form | `borrowing`, `total_assets`, `outside_liabilities`, `nof`, `crar`, `tier1`, `single_party`, `single_group`, `alm_mismatch`, `short_face`, `when_issued`, `ois_hedge_ratio`, `ois_hedge_effect`, `settlement_cover`, `corporate_repo`, `board_resolution` |

Exposures, CS01 and JTD by issuer, group and sector count corporate bonds, CP and CD (GoI and state bonds exempt). A
purchase still to settle counts at its market value (the trade-date view) and its payable as an outside liability;
owned fund, Tier 1 and capital are taken as NAV (the accountant's mapping); all UNVERIFIED.

**The checks.** Utilisation is `value / hard` for a maximum and, for a minimum, `hard / value` with a positive level
or `value / hard` with a negative one (a drawdown), so that 1 is the boundary either way (UNVERIFIED). Status:
`breach` past the hard level, `warning` past the warning level, `ok`, `incomplete` when the metric's inputs are
missing (a warning, never a breach: UNVERIFIED), and `not_applicable` for the other legal form's rows (listed, never
measured, so that the checks show which rules depend on the form) and for the fund's own borrowing limits under a
preset that cannot borrow. Every row of an `EXAMPLE` limit says `EXAMPLE` in its detail.

**The breach log.** A breach opens an episode (`new`, its cure date `cure_business_days` business days on); still in
breach the next day it is `continuing`, past its cure date (at once for a legal-form rule) `escalated`, and back
within the level `cured` (kept in the log). `origin` is `active` when the breach first appears on a day an order in
its scope filled, `passive` when the market moved. The example cure periods (UNVERIFIED): 30 business days for
concentration, ratings and tenor, 5 for market risk and liquidity, 1 for drawdown and stop-loss, 0 for legal-form
rules.

**De-risking.** While a `derisk` row is in force (E24, the drawdown limit of the example table), the fund's drawdown
scale (`portfolio.drawdown_scale` on its NAV index: 0.5 at -3%, 0.25 at -5%, back to 1 only above -1.5%) times a
strategy's stop-loss scale (a 20-day P&L at -2% of the fund's NAV halves it) multiplies the hard and warning levels of
the DV01, KRD, CS01, VaR and ES maxima in its scope (example levels, UNVERIFIED).

**The shipped tables.** `limits_example.csv` (E01-E30) holds **examples only**: every row is `EXAMPLE`, its note
says to replace it with the fund's approved risk policy before any real use, and no level in it is a recommendation
(round numbers; SEBI's investment restrictions for mutual funds were a reference for some). `legal_form_limits.csv`
(L01-L24) is this project's reading of RBI's directions for each legal form, for counsel to confirm: every row says
`counsel=yes` and gives its paragraph and the claim ids of the verified research notes; it is always added to the
fund's table. `nbfc_risk_weights.csv` holds the middle layer's risk weights (Central Government securities and SDLs
0, corporate bonds, CP and CD 100%, bonds of public sector banks 20% when the issuer table gives the sector
`psu_bank`).

**What depends on the legal form.** The fund's legal form is unsettled and a question for counsel. Everything runs
under either preset; these rules change with it:

| Rule | `unregulated_company` | `nbfc_non_pd` |
|---|---|---|
| Borrowing (repo, TREPS) | none: L01 and the gate's cash rule (V) | allowed; below Rs 1,000 crore of assets, outside liabilities at most 7 times owned fund (L10, V) |
| Short sales | none (L04, V) | none for a non-PD NBFC (L18, V) |
| OIS | hedging only: the engine's DV01 test (L05, V) and the swaps must not raise the VaR or the sum of absolute key rates (L06: the definition V, this reading U) | any purpose; OIS views of a base-layer NBFC (L19, DRAFT, P: warns) |
| Size | assets below Rs 1,000 crore for the exemption (L02, V) | net owned fund at least Rs 10 crore (L09, V); from Rs 1,000 crore of assets CRAR 15%, Tier 1 10%, single party 25% and single group 40% (L11-L14, V) |
| ALM | not applicable (nothing borrowed) | from Rs 100 crore of assets, negative mismatches at most 10%, 10% and 20% in 1-7, 8-14 and 15-30 days (L15-L17, V) |
| Settlement payables | leverage at most 1 and purchases covered by cash (L07-L08, DRAFT: warn) | counted as outside liabilities (U) |
| Corporate bond repo | none | base layer (L20, DRAFT, P: warns) |
| Board resolution | each financial year (L03, V: reported) | none |
| When-issued | at most 25% long and 10% short of the notified amount (L21-L22, V) | the same (L23-L24, V) |
| Strategies (allocation) | S7's OIS views weight 0; S3 and S5 long-only against paid OIS; S1 and S2 unfunded; leverage at most 1 | every strategy, within the base layer's leverage and, from Rs 1,000 crore of assets, CRAR |

DRAFT rows are measured and reported but never block an order; each becomes `APPROVED`, or is deleted, once counsel
has read it.

## The pre-trade gate

`limits.PreTradeGate(limits, ctx, allow_example_limits=False)` decides proposed orders (`returns.Order`) against the
book as of the decision time: `limits.state_from_result` builds the state from a day of an engine run or a
`cr-strat returns` directory, `limits.state_from_snapshot` from a back-test's `Snapshot` (read only through its
accessors). `check(state, orders)` returns the approved orders, the same objects in the same order, and
`GATE_COLUMNS` rows: one per order, or one per failed limit. Each order is decided against the state plus the orders
already approved in the same decision and the queued ones (counted as filled, UNVERIFIED):

1. **The preset's rules first**, refusing whatever the limits say: an OIS under a preset with none, or with
   `purpose=view` under `hedge_only` (Rupee IRD Directions 2025 para 5.4(i)); a sale that would leave a book short
   without the right to short (Short Sale Directions 2018 para 3); a when-issued position beyond the preset's share of
   the notified amount (When Issued Directions 2018 para 6); a purchase that would need borrowing under a preset that
   cannot borrow, keeping 1% of its consideration in cash for the fill and costs (`cash_buffer_pct`, UNVERIFIED).
2. **The order's risk**: a pending position priced at the day's traded mark, else the valuation mark, else a model
   price from the curve and the bond's residual (refused, with the reason, with none); an OIS as a par swap on the
   day's OIS curve. Every metric is recomputed on the book with it (sensitivities, key rates, CS01, exposures,
   liquidity and funding liquidity, each book's cash); VaR and ES are parametric (EWMA) on the post-trade
   sensitivities, the gate's approximation (UNVERIFIED); stresses are revalued in full (the state once, each order
   once).
3. **The limits in force**, in `limit_id` order: `refused` when, for the hard level of an enforced limit (`APPROVED`;
   `EXAMPLE` only with `allow_example_limits`), the post-trade value is past it **and worse than before** (a deeper or
   a new breach); an order that shrinks a breach passes, and keys it does not change never block it.
   `approved_with_warning` when only a warning level fails, a `DRAFT` row fails or a metric is incomplete. A `halt`
   limit in breach refuses every order in its scope; a rating floor `of=purchases` reads each credit purchase's own
   rating as known then; `report` rows have no effect here.

A failure row names the limit, the metric, the scope, the pre- and post-trade values and the levels, with the reason
in words, for example `limit E01 dv01 (fund): post-trade 5.34781 years above the hard 4 years (pre-trade 5.28255
years)`; on a refused order the rows that only warn begin `warning only:`. The gate refuses to run at all on EXAMPLE
limits unless told they are for testing (`allow_example_limits=True`, `--allow-example-limits`).

```bash
cr-strat pre-trade --orders data/raw/orders_20261215.csv --engine-dir data/out/returns --date 2026-12-14 \
    --limits data/raw/limits.csv --access nbfc_non_pd --marks data/raw/marks.csv \
    --nss-params data/out/nss_params.csv --trades data/raw/trades.csv --out-dir data/out/gate_20261214
```

writes `gate.csv`, `approved_trades.csv` and `approved_swaps.csv` (the next business day's trades, prices blank) and
prints each decision; exit 0, or 2 on bad input or EXAMPLE limits without the flag.

**Back-tests through the gate.** `returns.run_backtest(limits.GatedStrategy(strategy, gate,
recorder=limits.risk_recorder(ctx)), ...)` runs a strategy through the same gate each evening: a refused order never
reaches the engine and stays in `GatedStrategy.log_frame()`, keyed by `(decision_date, seq)` with its reasons; the
recorder keeps each evening's risk numbers. The engine did not change: an ungated run is today's code path, and a gate
that approves everything, with or without the recorder, writes the same bytes as the ungated run.

## Allocation and sizing

[`portfolio.py`](../src/cr_strat/portfolio.py) (numpy only, no random starts) weights strategy books, scales them to
risk budgets, and sizes and hedges trades; every trade it suggests still goes through the gate.

```bash
cr-strat allocate --nav data/out/returns/nav.csv --strategy-books data/raw/strategy_books.csv --method erc_vol \
    --as-of 2026-12-14 --access nbfc_non_pd --out data/out/allocation_20261214.csv --correlation data/out/corr.csv
    # --method fixed --fixed weights.csv | inverse_vol | erc_es | budget --budgets budgets.csv --risk-dir ...
    # --bounds bounds.csv; --algorithm newton; --cov-method sample
```

- **Returns and covariance**: each strategy's daily return from the engine's NAV by book, `(NAV - NAV_prev - flow) /
  (NAV_prev + flow)`, books mapped to strategies as dated in `strategy_books`; EWMA (lambda 0.94) or sample
  covariance, pairwise over the days both strategies had capital, refused below 20 joint observations, shrunk towards
  its diagonal in steps of 0.05 until positive definite (UNVERIFIED).
- **Methods** (`ALLOCATION_COLUMNS`): `fixed` (a dated table of weights), `inverse_vol`, `erc_vol` (equal risk
  contributions by cyclical coordinate descent, checked by Newton's method), `erc_es` (equal contributions to
  historical ES by a damped fixed point, refused with the last error when it does not converge: exact ES parity need
  not exist), `budget` (each strategy's scale `min(1, budget / |metric|)` for a DV01, CS01, VaR or ES budget, above 1
  only if asked). Bounds are applied by clipping and re-solving (UNVERIFIED). A strategy the preset forbids gets
  weight 0, with the reason (S7's OIS views under `unregulated_company`); `cr-strat allocate` prints each
  strategy's weight, risk share and note.
- **Rebalancing and de-risking**: `rebalance_due` (the first business day of a month, or a drift of 5 percentage
  points or 20% of the target, never while a hard breach in scope is open) and `rebalance_cost`; `drawdown_scale` and
  `stop_loss_scale` as above.
- **Sizing**: `size_to_dv01` and `size_to_cs01` (`face = target / (sensitivity per 100 / 100)`), `size_to_vol`
  (capped at 2 times), `round_to_lot` (down to Rs 5 crore, a parameter).
- **Hedges**: `hedge_ois_notional` (the DV01 over the swap's annuity, the rule of `asset_swaps.hedge_notional`),
  `hedge_gsec_face`, and `key_rate_hedge` by least squares or, with each hedge in its allowed direction, non-negative
  least squares (Lawson and Hanson's active-set method, written in numpy).
- **Correlation and contributions**: `correlation_table`; Euler components of parametric VaR, of historical ES (the
  Acerbi-Tasche tail weights, summing to the ES exactly) and of historical VaR (a band of 5 scenarios around the
  VaR's, rescaled to sum to it: UNVERIFIED).

An annuity-sized OIS hedge can fail the engine's 105% DV01 test when the swap is short: in the tests a 1-year payer
sized by its annuity against 6.94% GS 2036 carries 1.0593 times the bond's DV01 as the engine measures it (a 4-year
one 1.0251). Size the swap by the engine's DV01 when the hedge must pass that test.

## The daily process

[`daily.py`](../src/cr_strat/daily.py) is paper trading: once a day, after the evening's data is public, it runs the
code of a back-test with that day as the last, as of 20:00 (the engine's decision time; nothing reads the clock).

```bash
cr-strat daily-report --config paper/fund/config.toml --date 2026-12-14 --orders paper/fund/orders_20261215.csv \
    --out-dir paper/fund
```

It replays the paper book from inception with `returns.run_book`, runs the day's risk, checks the limits (carrying the
previous report's breach log), gates the proposed orders for the next business day, appends the day's VaR to its
history and back-tests it, compares the replay of the previous report's day with what that report stored, and writes
`<out-dir>/<date>/`: `engine/`, `risk/`, `limits/`, `orders/` (`proposed`, `gate`, `approved_trades`,
`approved_swaps`, `open_orders`), `backtest/`, `reconciliation.csv`, `report.md` and `manifest.json` (every input's
path, sha256 and rows, the configuration's sha256, the settings, `as_of`, the package version and each output's rows).
Its limit and gate files are computed from the `engine/` and `risk/` files it has just written, so `cr-strat limits`
and `cr-strat pre-trade` on those directories reproduce them byte for byte. Exit 0 written, 2 bad input; a weekend or
holiday, a day before inception, or EXAMPLE limits for the gate without `allow_example_limits` are refused before
anything is written.

The configuration is a TOML file read with the standard library's `tomllib` (unknown keys refused, naming the
closest; the full list in [data/README.md](../data/README.md)):

```toml
fund = "paper"
access = "nbfc_non_pd"              # required: the legal form is a question for counsel
inception = "2026-12-01"
board_resolution_date = "2026-04-02"
limits = "limits.csv"               # or "example" (every row EXAMPLE: then allow_example_limits = true, tests only)

[inputs]                            # paths relative to this file
marks = "marks.csv"
nss_params = "nss_params.csv"       # with public_from
flows = "flows.csv"                 # the capital
trades = "trades.csv"               # the paper book: each day's approved_trades.csv appended
corporate_terms = "corporate_terms.csv"

[risk]
min_obs_hard = 60

[gate]
cash_buffer_pct = 1.0
```

`report.md` carries `(EXAMPLE limits)` in its first line while any EXAMPLE row is in force, then the date, `as_of`,
the preset and the limits in force by policy status, then escalated and new breaches, NAV and P&L (the day, the month
and the financial year to date, the attribution by line), DV01 and key rates, credit (CS01 by rating, the five
largest issuers, groups and sectors with JTD), VaR and ES with coverage, the stress table worst first, liquidity and
funding liquidity (or why it does not apply), every limit's status, the next day's orders with each refusal's
reasons, the back-test, the reconciliation, data quality and the UNVERIFIED rules used.

**The daily routine of paper trading:**

1. When the day's prices, curve fit (`cr-strat curve-fit --nss-params ... --public-from`) and other inputs are
   public, add them to the input files with their `public_from`.
2. Write the strategy's proposed orders for the next business day (`ORDER_COLUMNS`) and run `cr-strat daily-report`
   for the day.
3. Read `report.md`: escalated and new breaches first; then the reconciliation, where any row means an input of an
   earlier day changed (a revised mark or fit) and is to be understood before going on; then the refused orders and
   their reasons.
4. Append the day's `orders/approved_trades.csv` (and `approved_swaps.csv`) to the paper book's trades (and swaps)
   file; the engine fills them the next day at that day's traded mid plus or minus the half spread (an OIS at the
   day's par rate). The process never writes its own inputs, so rerunning a day writes the same bytes.
5. Keep each day's folder: the next day reads its breach log, VaR history and engine files.

Not built: freezing a report as the official record, intraday checks, an approval workflow for refused orders (each
is listed with its reasons for a person to act on), and a library `Strategy` in the daily command (a strategy object
runs through `run_backtest(GatedStrategy(...))` instead).

## Look-ahead and determinism

Every input is cut at `as_of`: curves and factor levels by their `public_from` (a row without one counts from 18:00
on its date and is flagged), marks, ratings, exercised options and FRB coupons as the engine cuts them, the ADV
table by `public_from`, a scenario by its `available_from` and `effective_from` (a run as of 2019 cannot use the 2020
episode). Tested: a fit made public after `as_of` is not in the history, `--as-of 18:30` on a day whose fit is public
at 19:00 uses the day before's curve (flagged `stale_curve`) and one observation fewer, a rating action public after
`as_of` is not used, a strategy peeking through a snapshot leaves the engine's files unchanged. The same inputs give
the same bytes in any row order (tested on shuffled positions, NAV, funding and NSS rows, and in the demo); the risk
modules never read the clock.

The limits apply by their effective and approval dates (a row approved after `as_of` is not in force; tested), the
ratings, outstanding stock and notified amounts as known at `as_of`, and the gate's prices and factor history as
public at the decision time. The gate reads its limits in `limit_id` order and writes the same bytes for a shuffled
table; the daily process writes the same bytes when a day is rerun and for a shuffled limit table (only the
manifest's hash of the table differs); `limits`, `portfolio` and `daily` never read the clock.

## Conventions and what the fund must decide

Status: **V** verified against the source named, **P** partly (a standard form, or secondary sources), **U**
UNVERIFIED (a choice with no source: listed in every run's `notes.txt`, settable, to be confirmed by the fund).

| Convention | Default | Status |
|---|---|---|
| VaR 99% one-day (back-tested), ES 97.5%, 250 observations, traffic-light zones and multipliers | MAR32, MAR33 | V |
| Key tenors | FRTB's 0.25-30 years plus 40 and 50 (GoI issues 50-year bonds); OIS to its curve's last node | U |
| Bump | zero rates, analytic for fixed flows, central +-0.5 bp otherwise; OIS par quotes central (`up`: one-sided) | U |
| SDLs | on the G-sec curve plus an `sdl_spread` factor (`--sdl-mode own_curve`) | U |
| CS01 | z-spread to the reference curve; callables to maturity | U |
| FRB | index risk on the G-sec key rates at the next reset; margin on `frb_dm:<isin>` | U |
| JTD | Basel LGDs (MAR22: V), senior by default, GoI and SDLs excluded, no netting | V / U |
| Quantile | `ceil(n(1-a))`-th largest loss; ES with the tail counted exactly (Acerbi-Tasche) | U / P |
| History | gaps of 2-5 business days scaled by `1/sqrt(g)`; nearest in-range node for a tenor outside a fit; at least 60 observations | U |
| Window | latest dates with every factor of at least 60 observations; the rest uncovered | U |
| EWMA | lambda 0.94, 550 observations (RiskMetrics: V), weights normalised (U); sample covariance with ddof 1 | V / U |
| Stressed window | searched by the sensitivity method on the factors starting within 30 days of the earliest | U |
| Liquidity | 20% participation, 20-day ADV, example proxies, stressed spreads 3 times, LVaR = VaR + stressed cost | U |
| Scenario sizes | every row marked U in the table: examples for the fund's policy to replace | U |
| IRRBB shapes and INR sizes, decay 4 years, rotations | BCBS IRRBB, April 2016, Annex 2 | V |
| Floor of shocked zero rates | 0% | U (national discretion) |
| Traffic light below six observations | no exception is green | U |
| Access preset | must be chosen (`unregulated_company` cannot borrow: funding stresses not applicable) | a question for counsel |
| Limit levels and their warning-to-hard spacing | `limits_example.csv`: EXAMPLE rows, for the fund's approved risk policy to replace | U |
| Legal-form rows | `legal_form_limits.csv`: a reading of RBI's directions; DRAFT rows warn only | for counsel |
| Cure periods | 30 business days for concentration, ratings and tenor; 5 market risk and liquidity; 1 drawdown; 0 legal form | U |
| Incomplete metric | a warning, never a breach | U |
| Owned fund, Tier 1, capital | NAV; payables of purchases to settle as outside liabilities; purchases at market value | U (the accountant's mapping) |
| The gate | parametric VaR and ES on post-trade sensitivities; queued orders as filled; 1% cash buffer on purchases without borrowing | U |
| De-risking | drawdown 0.5 at -3%, 0.25 at -5%, back above -1.5%; a strategy's 20-day loss of 2% of NAV halves it | U |
| Allocation | 20 joint observations; shrinkage in steps of 0.05; bounds by clipping; rebalancing bands 5 points or 20%; Rs 5 crore lots; volatility scale at most 2 | U |

## Data needed before real use

- A daily **history of fitted G-sec curves** with the time each became public: `cr-strat curve-fit --nss-params` on
  licensed traded prices (NDS-OM or the exchanges, under their terms) for at least 250 business days, longer for the
  stressed window (none of the free data reaches a stress period: the coarse auction history starts in 2015, is
  fortnightly and has a gap from Feb 2024 to Apr 2025).
- **OIS quotes and MIBOR fixings** (FBIL's, licensed) for the swaps and the OIS key rates.
- **Spread histories** for `corp:`, `cd:`, `sdl_spread:` and `frb_dm:` factors (agency valuations or the price panel,
  under their terms), else the credit VaR is incomplete or proxied.
- The **issuer table** (group, sector, seniority), **rating actions** with public times, the **ADV table** or marks
  with volumes, the **haircut** and CCIL class tables, the **NAV** and funding of the engine run.
- The fund's own **scenario policy** in place of the U examples, and its decisions on the U conventions above.
- For the limits and the gate: the fund's **approved limit table** in place of every EXAMPLE row, counsel's reading
  of the legal form and of each DRAFT row, the **board resolution** date, the **strategy-book map**, budgets and
  fixed weights, and the **outstanding stock** and **auction table** for the ISIN-share and when-issued limits.

## What was checked, and what was not

Checked by running, on synthetic data with known answers (tests `test_risk.py`, `test_stress.py`,
`test_cli_risk.py`): the key rates of 6.94% GS 2036 sum to its curve DV01 (analytic against central within 1.1e-9
per 100; yield DV01 Rs 6,42,828.93 on Rs 100 crore); CS01 equals the curve DV01 for fixed flows; a payer swap's zero
key rates sum to -Rs 3,60,997.58 and its par-quote DV01s to -Rs 3,50,989.26 (central); FRB 2031's index and margin
DV01s 0.0004527838 and 0.0425031888 per 100 at its 7 Dec 2026 reset; JTD Rs 7.70 and 4.80 crore with Basel's LGDs;
the IRRBB shapes at twelve tenors and BCBS's example; the six IRRBB scenarios on Rs 100 crore of GS 2036 (parallel
up -Rs 22,56,44,876.70); the yield mode equal to the price at the yield plus 1%; key-rate points joined by the
triangles (-Rs 5,29,08,651.12); a payer swap +Rs 13,38,42,034.38 under the parallel up shock; the floor binding on a
low-rate curve; VaR and ES on a known P&L grid with `m` exact; EWMA against its definition; the three methods on a
parallel history; Kupiec, Christoffersen and the traffic light against MAR99's binomial table; the stressed window
finding a volatile block; Euler components summing to the figures; the back-test pairing each VaR with the next day's
P&L and counting strictly; the funding stress (Rs 8,68,767.12 for +200 bp over 10 nights on Rs 158.55 crore) and its
`not_applicable` rows; days to liquidate and exit costs (Rs 4,01,768.08 at 0.625 bp, three times stressed); every
refusal named; look-ahead cuts; same bytes twice and in any row order; the engine's existing commands write the same
bytes as before this layer (compared file by file on a reference run). The free demo also reproduced the coarse VaR
of the specification: Rs 49,88,500 for Rs 100 crore of GS 2036 on the auction table's 154 same-security changes in
the 9-11-year band (labelled coarse: primary-market cut-offs, pooled, fortnightly).

Checked for the limits, gate, allocation and daily process (tests `test_limits.py`, `test_portfolio.py`,
`test_daily.py`, `test_cli_limits.py`, on synthetic books): the specification's vectors T26-T32 (ERC weights
0.3836125063, 0.1918062531, 0.2426178518 and 0.1819633888 by both solvers, coordinate descent in 15 sweeps and Newton
in 5 at a tolerance of 1e-12, risk contributions equal within 1e-12; the drawdown scales 1, 1, 1, 0.5, 0.5, 0.25,
0.25, 0.25, 1, 1, 1; Rs 77,78,11,917.83 of face for Rs 5 lakh per bp of GS 2036; the least-squares and non-negative
key-rate hedges; Euler components Rs 5,31,018.9919, 18,66,262.3991 and 4,15,580.0806 summing to the VaR; ES
components 5.6 and 0.2 summing to the ES; the hedge-effect VaRs Rs 62,81,140, 68,16,636 and 1,84,648); every refusal
of the limit table and of the configuration; limits in force by dates, approval and legal form; statuses,
utilisation and the breach log through new, continuing, escalated and cured; the preset's rules refusing first; the
worse-than-before rule; EXAMPLE and DRAFT rows; queued orders; a gate with the same bytes in any row order; a gated
back-test whose refused order is logged and absent from the fills while its pass-all twin writes the same bytes; a
gated NBFC back-test whose purchases count each book's cash (a regression test); a two-day paper run with its 39
files, the report's first line and the manifest's hashes, the breach log carried, a revised mark listed in the
reconciliation, an idempotent rerun and both presets; the four commands' exit codes, and `limits` and `pre-trade`
writing the same bytes as the daily process on its own directories under both presets. In scratch: the existing
commands write the same bytes as at commit 80f9e8b (7 runs, 63 files, by sha256), and a pass-all gate around that
commit's back-test, with and without a risk recorder, writes its 9 files byte for byte.

Not checked: any real book, curve history, spread history or OIS quote; a stressed window on a real stress period;
the revaluation's speed on a large real book (the demo's six positions with 300 days of history take about 2.3 s
for every table, 3.6 s with the stressed and liquidity-horizon ES; 300 days for a swap book 0.4 s); FRTB's risk weights against a later revision; RBI's IRRBB guidelines for banks (not read);
AMFI's stress parameters (not found); Indian recovery rates for the LGDs; the hypothetical P&L against a real
attribution; any fund's real limit policy, and the legal-form rows against counsel's reading; the gate's speed on a
large book (about 0.6 s for 7 orders on the test book); the de-risking and allocation parameters against any real
strategy history (none exists yet).

**Not built**: an option model for callable bonds; reverse stress tests; intraday margin; the mandate-threshold forced
selling of S9 beyond a notch downgrade with a multiplier; netting of JTD; a VaR of the OIS basis to the G-sec curve
beyond the two curves' own key rates; for the limits layer, see [The daily process](#the-daily-process) (official
records, intraday checks, an approval workflow).

## Sources

Stated in our own words; nothing is copied.

- BCBS, *Interest rate risk in the banking book*, April 2016, https://www.bis.org/bcbs/publ/d368.pdf (Annex 2: the
  six shock shapes, the INR sizes, the decay and rotation weights).
- The Basel Framework's market risk standard, https://www.bis.org/committees/bcbs/basel-framework/MAR/standard.pdf
  (MAR21: key tenors and credit spread risk weights; MAR22: LGDs; MAR32: back-testing, zones and multipliers; MAR33:
  ES, liquidity horizons, the stressed window; MAR99: the back-testing framework for other sample sizes).
- J.P. Morgan/Reuters, *RiskMetrics Technical Document*, 4th ed., 1996, section 5.3 (EWMA, lambda 0.94, 550
  observations).
- P. Kupiec, "Techniques for verifying the accuracy of risk measurement models", *Journal of Derivatives*, 1995;
  P. Christoffersen, "Evaluating interval forecasts", *International Economic Review*, 1998; C. Acerbi and
  D. Tasche, "On the coherence of expected shortfall", *Journal of Banking & Finance*, 2002.
- RBI's ALM Directions for NBFCs (the structural liquidity limits), as summarised in the analytics notes
  ([`analytics.md`](analytics.md)); CCIL's TREPS caps through `funding.treps_shares`.
- The scenario table's `source` column names the source of each row.
- S. Maillard, T. Roncalli and J. Teiletche, "The properties of equally weighted risk contribution portfolios",
  *Journal of Portfolio Management* 36(4), 2010; T. Griveau-Billion, J.-C. Richard and T. Roncalli, "A fast algorithm
  for computing high-dimensional risk parity portfolios", 2013; F. Spinu, "An algorithm for computing risk parity
  weights", 2013 (ERC and its two solvers).
- C. Lawson and R. Hanson, *Solving Least Squares Problems*, 1974 (non-negative least squares); D. Tasche, "Risk
  contributions and performance measurement", 1999, and "Capital allocation to business units and sub-portfolios:
  the Euler principle", 2008 (Euler contributions).
- RBI's directions as the legal-form table cites them by paragraph (NBFC Registration and Scale Based Regulation,
  Capital Adequacy, Concentration, ALM and Investment Portfolio Directions; the Repo Directions 2025; the Short Sale
  and When Issued Directions 2018; the Rupee Interest Rate Derivatives Directions 2025), as recorded in the verified
  research notes (the claim ids in each row's `source`); SEBI's Master Circular for Mutual Funds (its investment
  restrictions) as a reference for some example levels.
