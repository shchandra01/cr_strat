# Risk: sensitivities, VaR and ES, stress tests and liquidity

What [`risk.py`](../src/cr_strat/risk.py) and [`stress.py`](../src/cr_strat/stress.py) measure for the books the
return engine runs ([`analytics.md`](analytics.md), The return engine and costs), with `cr-strat risk` and
`cr-strat var-backtest`, as of 24 Nov 2026. Everything was built and tested on synthetic books and histories with
known answers and on the free auction table (labelled coarse); no licensed price, curve, OIS quote or spread history
has been through it. Not built here: risk limits, pre-trade checks, capital allocation and the daily report.

- [Running it](#running-it)
- [What each number means](#what-each-number-means)
- [Method](#method): the book, sensitivities, factor histories, VaR and ES, back-tests, stress scenarios, liquidity
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

## Look-ahead and determinism

Every input is cut at `as_of`: curves and factor levels by their `public_from` (a row without one counts from 18:00
on its date and is flagged), marks, ratings, exercised options and FRB coupons as the engine cuts them, the ADV
table by `public_from`, a scenario by its `available_from` and `effective_from` (a run as of 2019 cannot use the 2020
episode). Tested: a fit made public after `as_of` is not in the history, `--as-of 18:30` on a day whose fit is public
at 19:00 uses the day before's curve (flagged `stale_curve`) and one observation fewer, a rating action public after
`as_of` is not used, a strategy peeking through a snapshot leaves the engine's files unchanged. The same inputs give
the same bytes in any row order (tested on shuffled positions, NAV, funding and NSS rows, and in the demo); the risk
modules never read the clock.

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

Not checked: any real book, curve history, spread history or OIS quote; a stressed window on a real stress period;
the revaluation's speed on a large real book (the demo's six positions with 300 days of history take about 2.3 s
for every table, 3.6 s with the stressed and liquidity-horizon ES; 300 days for a swap book 0.4 s); FRTB's risk weights against a later revision; RBI's IRRBB guidelines for banks (not read);
AMFI's stress parameters (not found); Indian recovery rates for the LGDs; the hypothetical P&L against a real
attribution.

**Not built**: limits, pre-trade checks, allocation, the daily report (a later layer); an option model for callable
bonds; reverse stress tests; intraday margin; the mandate-threshold forced selling of S9 beyond a notch downgrade
with a multiplier; netting of JTD; a VaR of the OIS basis to the G-sec curve beyond the two curves' own key rates.

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
