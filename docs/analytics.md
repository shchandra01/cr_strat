# Analytics: fitted curves, OIS, asset swaps, funding, the policy path, the return engine and the event ledger

Four parts, as of 19 Nov 2026:

- **[Curve fitting and rich/cheap](#curve-fitting-and-richcheap)**:
  what [`curvefit.py`](../src/cr_strat/curvefit.py) and `cr-strat curve-fit` do for strategies S3 (rich/cheap to
  a fitted G-sec curve) and S5 (the long end).
- **[OIS curves, asset swaps, funding and the policy path](#ois-curves-asset-swaps-funding-and-the-policy-path)**:
  [`ois.py`](../src/cr_strat/ois.py), [`asset_swaps.py`](../src/cr_strat/asset_swaps.py),
  [`funding.py`](../src/cr_strat/funding.py) and [`policy_path.py`](../src/cr_strat/policy_path.py), with
  `cr-strat ois-curve`, `asset-swaps`, `repo-carry` and `policy-path`, for S1 (G-secs against MIBOR-OIS, funded in
  repo) and S7 (the policy path against OIS); with
  [corporate bonds and GoI FRBs](#corporate-bonds-and-goi-frbs-asset-swaps-spreads-to-the-g-sec-curve-and-trade-checks):
  their asset swaps and discount margins, their spreads to a fitted G-sec curve
  ([`credit_spreads.py`](../src/cr_strat/credit_spreads.py), `cr-strat credit-spreads`) and the check of reported
  trade yields ([`trades.py`](../src/cr_strat/trades.py), `cr-strat trade-check`), for S2.
- **[The return engine and costs](#the-return-engine-and-costs)**: [`returns.py`](../src/cr_strat/returns.py) and
  [`costs.py`](../src/cr_strat/costs.py), with `cr-strat returns`: positions, daily valuation, funding, OIS hedges,
  costs, and a P&L attribution that adds up, for the strategies whose instruments `bonds`, `corporates` and `floating`
  price (S1, S3, S5 and S7; S2's CP, CD and corporate bonds; S4's auction trades; S6's SDLs; GoI FRBs).
- **[The S4 event ledger and supply measures](#the-s4-event-ledger-and-supply-measures)**:
  [`event_ledger.py`](../src/cr_strat/event_ledger.py), with `cr-strat event-ledger`: every auction, switch, buyback,
  open market and liquidity operation, calendar, MPC meeting and macro release with the time from which it was public,
  DV01 supply by week and bucket against the stock, auction metrics, forward supply, and event windows for S4.

Each part says how, on which conventions, and what was and was not checked. Everything was built and tested on
synthetic data with known answers (bonds priced off known curves, OIS quotes from known curves and step paths),
and run on free data only (auction cut-offs, the RBI store's money-market rates); no secondary-market price, OIS
quote or MIBOR fixing has been used. Bond cash flows, accrued interest, yields and risk come from
[`bonds.py`](../src/cr_strat/bonds.py) and its conventions ([`conventions.md`](conventions.md)); nothing here
re-implements them.

## Curve fitting and rich/cheap

### What it does

| Piece | Function | Output |
|---|---|---|
| Curve | `NssCurve`, `SplineCurve` | Discount factor, zero rate (continuous, semi-annual, annual), instantaneous forward and par yield at any time; any `bonds` instrument priced on the curve (`bond_price`, `bond_yield`, `bond_par_yield`) |
| Fit | `fit_curve(frame, settlement, settings)` | `FitResult`: the curve, one residual row per input row, diagnostics, the settings |
| Universe | `select_universe` | Which rows enter the fit and why not (`excluded_reason`) |
| Grid | `curve_points` | Zero, forward, par, discount on 0.25 to 50 years, with `in_data_range` |
| Benchmark | `benchmark_frame` | Par curves in the frame `curves.BenchmarkCurve` reads, every point semi-annual (below half a year the simple par rate is restated), so a fitted curve can replace FBIL's par curve as the credit benchmark |
| Cross-check | `compare_curves`, `model_risk_flags`, `suspect_bonds`, `refitter` | Par-yield gap between two curves inside their joint data range; a bond's residual flagged `model_risk` where the gap at its maturity between the two curves refitted without it exceeds 3 bp and, on a day with suspect bonds, persists on the two curves fitted without it and them |
| Rich/cheap | `residual_zscores`, `rich_cheap_table`, `append_history` | Time-series z-score of each bond's residual (in the fit, or out of sample) against its own history, robust cross-sectional z within maturity buckets; the daily residual panel kept, refusing a history fitted another way |
| Butterflies and pairs | `bond_butterflies`, `bond_pairs`, `curve_butterflies`, `curve_spreads` | Yield, model and residual flies of ISIN triples with DV01-neutral wings; the spread of an ISIN pair with a DV01-neutral shorter leg (S5's 10-year against 30-50 years); par-curve flies and spreads (2s10s, 10s30s, 10s40s, 10s50s) |
| Drivers | `residual_drivers`, `control_residuals` | Each residual with what free data say of its bond on the day (coupon, age, FAR flag, benchmark flag, reissues, days since the latest issue, outstanding stock); optionally the residual less what chosen drivers explain across the day's bonds (`resid_ctrl_bp`, UNVERIFIED form) |
| Price files | `prepare_prices` | A generic price file (ISIN plus clean price or yield, optional weight, flags, volume) joined to the security master; a row of no known kind kept out (`no_kind`) |

Residual signs: `resid_yield_bp = 100 (y_market - y_model)` is positive when the bond is cheap (its yield above
the curve's); `resid_price = P_market - P_model` is positive when it is rich. Both yields are `bonds`' yields
on the bond's own conventions (semi-annual for G-secs and SDLs, simple Act/365 for bills), from the market
price and from the model price.

```bash
cr-strat curve-fit --prices data/raw/prices_20261116.csv --master data/out/sovereign_master.csv \
    --settlement 2026-11-16 --out-dir data/out/curve_20261116 \
    --history data/out/residual_history.csv --residual loo --append-history data/out/residual_history_next.csv \
    --fly IN0020230051,IN0020240039,IN0020250052 --pair IN0020250052,IN0020250117 \
    --ledger data/out/ledger/ledger.csv --control coupon_pct,age_years,log_stock,far
```

The price file needs `isin` and a `clean_price` or a `yield_pct` per row (aliases `price`, `yield`, `ytm`); `kind`,
`coupon_pct`, `maturity_date` and `issue_date` come from the master when the file leaves them blank (a value in both
that differs is refused), and `weight`, `flags` (`;`-separated: `benchmark`, `when_issued`, `odd_lot`, ...),
`traded`, `trade_count` and `volume_cr` are optional: a row without them counts as traded, and when no row has any
the warnings say `no_trade_data`. A loader for a licensed trade file (CCIL's trades by security) only has to produce
these columns; FBIL's valuation sheets are a cross-check, not fit input (a curve fitted to them recovers FBIL's
model). A row whose kind neither the file nor the master gives is kept out of the fit (`no_kind`) and named. The
settlement date must be a Mumbai business day. The command writes `curve.csv`, `residuals.csv`, `benchmark_par.csv`,
`curve_spreads.csv`, `diagnostics.json` and, with the cross-check (default `--check pspline`, refitted without each
bond as the primary is), `curve_check.csv` and `crosscheck.csv`; with `--history`, `rich_cheap.csv` (on
`--residual in_fit`, the default, or `loo`); with `--fly`, `butterflies.csv`; with `--pair`, `pairs.csv`; with
`--master` or `--ledger` (the issuance ledger, for reissues and the stock on the settlement date), `drivers.csv`,
and with `--control` (drivers, comma-separated) its `resid_ctrl_bp` column and the day's coefficients in
`controls.csv`.
`--append-history FILE` adds the day's residuals to a history file (`HISTORY_COLUMNS`; a refit of the day replaces
its rows, a file fitted with other settings is refused): run it once a day after the fit and pass that file as the
next day's `--history`. A missing input file exits 2, as bad input does. The same inputs give the same bytes.

### Method

**Curves.** `DF(t) = exp(-z(t) t / 100)`, `t` in Act/365F years from settlement to each flow's scheduled
date, `z` continuously compounded in percent.

- **Nelson-Siegel-Svensson** (default). Six parameters; `z(0+) = b0 + b1`, `z(inf) = b0`. Bounds `0 <= b0 <= 30`,
  `0 <= b0 + b1 <= 30`, `|b2|, |b3| <= 60`, `0.05 <= tau1 <= 30`, `0.05 <= tau2 <= 100`, `tau2 >= 1.2 tau1`.
  Levenberg-Marquardt with an analytic Jacobian, Marquardt scaling, projected steps that hold a parameter at its
  bound while the step points outward, from 22 starts (every pair of tau1 in 0.3, 0.75, 1.5, 3, 6 and tau2 in 2, 5,
  10, 20, 40 with tau2 >= 1.2 tau1, betas from a least-squares fit of the bonds' continuous yields at their
  durations) plus an optional warm start; lowest objective wins. Why NSS: CCIL publishes the Indian sovereign curve
  in this form (since 1 Sep 2009, Nelson-Siegel before; CCIL's
  [parameters page](https://www.ccilindia.com/web/ccil/zcyc-parameters), methodology read only through search
  summaries), so a licensed CCIL history would be a direct cross-check. Six parameters cannot follow every bond, but
  they lean towards each one: a bond's in-fit residual is about `1 - h` of its out-of-sample one, `h` its leverage
  (the `leverage` column). In the dense middle of the curve `h` is about 0.1, so a bond 5 bp cheap shows 4.4 to 4.5
  bp; at the ends it is 0.3 to 1: on the 29 synthetic G-secs of the tests, a bond 5 bp cheap shows 0.06 bp in the
  fit when it is the shortest bond (no bills), 3.6 bp when it is the longest, and 1.9 bp as the longest of a 12-bond
  day, while its out-of-sample residual (`loo_resid_yield_bp`) is 4.76 to 5.18 bp in every case (a multi-start refit
  without it gives 5.00: the library's refits are local, below); with five T-bills in the fit the shortest bond
  shows 3.8 bp. So read end bonds on the out-of-sample residual where bills, an anchor or a close neighbour still
  pin the refit near them. The very shortest and longest bonds' out-of-sample residuals are extrapolations: under
  the spline, the shortest of the 29 bonds (no bills, no anchor) 5 bp cheap shows 7.89 bp, and with a 20 bp outlier
  elsewhere on the curve, NSS refits without a fair 1-year bond whose objectives differ by 0.2% put it at 2.0 bp
  (the library's) or -81.4 bp. Beyond the longest bond the curve tends to `b0`, one parameter, which can sit at its
  bound: on the SDL day of 19 Mar 2024 `b0` ends at 30% (`params_at_bound`) and the forward rate climbs from 7.15%
  at 30 years to 8.74% at 50. Read 40-50 year points only where `in_data_range` is true, and check
  `params_at_bound`. NSS has several local minima and badly conditioned regions (Gilli, Grosse and Schumann, COMISEF
  WP 31, 2010, read through its abstract): compare curves across days, not parameters.
- **Penalised B-spline of the forward curve** (the cross-check). Cubic B-splines on a clamped knot vector over
  `[0, T]`, `T` the longest maturity, 12 interior knots at `T (k/13)^2` (denser at the short end; maturity
  quantiles as an option). `ln DF` is the exact integral of the forward curve (each B-spline integrates to a
  sum of quartic B-splines, de Boor). Penalty `lam x 1e-8 x ||D2 c||^2` on second differences of the
  coefficients (Eilers and Marx), `lam` chosen over 1e2 to 1e8 by leave-one-out cross-validation from the
  leverages of the linearised fit (below), scored over the bonds only: an overnight anchor stays in the fit but
  is not scored, as its own leave-one-out error (how well the bonds extrapolate to `t = 0`) would otherwise
  dominate and push `lam` to the top of the grid (on 12 exact bonds from 3.5 to 30 years anchored at their own
  `z(0+)` it did: RMSE 4.86 bp and zero rates 15 bp off, against 0.0013 bp and 0.09 bp now). Gauss-Newton with
  step halving. Beyond `T` the forward rate is held flat. This is the smoothing-spline family of Fisher, Nychka
  and Zervos (1995) and Waggoner (1997), and close to FBIL's own method, a cubic spline on NDS-OM trades whose
  knots, weights and filters were not read. Where NSS's shape is wrong, the spline shows a gap; but the spline
  also bends towards a single mispriced bond, most at the ends (a bond 10 bp cheap at the long end of an exact
  NSS curve opened a 3.1 bp gap at its own maturity). So a bond's `model_risk` is judged on the gap at its
  maturity between the two curves refitted without it (`crosscheck_loo_diff_bp`, from both fits' `loo_par_pct`),
  which it cannot sway: there that bond's gap is 0.2 bp and it is not flagged, while a 10 bp bump in the true
  curve at 11 years, which NSS cannot follow, still flags bonds there. A large mispricing elsewhere still sways
  both refits, differently: a bond 20 bp cheap at the long end opened gaps of -4.9, -4.7 and +5.4 bp at its three
  neighbours, and one 20 bp cheap mid-curve -4.5 bp at the 35-day bill, whose refit extrapolates. So on a day with
  suspect bonds (out of sample above 10 bp and four robust standard deviations, `suspect_bonds`), each flagged
  bond is judged again on the two curves fitted afresh without it and the suspects (`refit=refitter(...)`, the
  command line's default; `crosscheck_suspect_diff_bp`, `model_risk_note`): those gaps fall to 0.00 to 0.05 bp
  and the flags go, while the bump, none of whose bonds is suspect, keeps its flags. A bond nearly alone at an end
  (leverage near one) is flagged when the two methods go different ways without it: rightly, as nothing else
  pins the curve there. Rows out of the fit keep the full curves' gap (`crosscheck_diff_bp`).
- **Flat**: one continuously compounded rate, for tiny cross-sections (one bond is enough). A diagnostic,
  never a rich/cheap curve.

**Objective.** Approximate yield errors, `e_i = (P_model - P_market) / (P_market D_i)`, `D_i` the modified
duration at the market price, so a long bond does not dominate (the ECB's Svensson curve minimises yield
errors: series `YC.B.U2.EUR.4F.G_N_A.SV_C_YM` on the [ECB data portal](https://data.ecb.europa.eu/)). CCIL's
reported form, squared price errors weighted by `1/D_i`, is `objective="price_over_duration"`; plain price
errors are `"price"`. Weights: equal (default) or `sqrt(volume_cr)` capped at four times the median, times
an optional `weight` column, normalised to mean one. An optional overnight anchor adds `z(0+) = rate` as a
pseudo-observation weighing one average bond (in the fit, not in the spline's penalty score).

**Universe.** Dated G-secs, T-bills and CMBs that traded, with a daily volume of at least ₹5 crore (rows
without a volume are kept), at least 3 months to maturity (bills: 7 days) and no when-issued or odd-lot flag;
optionally without flagged benchmarks. Out of the fit but with residuals where they can be priced: SDLs
(their residual to the G-sec curve is their spread; fit an SDL curve with `include_kinds=("sdl",)`), green
bonds, special securities, STRIPS. Floating-rate and inflation-indexed bonds have no pricer and get no
residual. A curve fitted to FBIL or CCIL valuation prices recovers their model, not the market
([`rates_data_sources.md`](rates_data_sources.md), Pitfalls): fit traded prices.

**Outliers.** Off by default (plain least squares). `loss="huber"` reweights by `min(1, 5 bp / |e|)` until the
weights settle. `outlier_bp` takes the worst bond out while its error exceeds both `outlier_bp` and four
robust standard deviations, at most five bonds and never below the method's minimum, then refits from all
starts. Residuals above 10 bp and four robust standard deviations are always named in the warnings.

**Diagnostics** (JSON in `diagnostics.json`): counts; RMSE, weighted RMSE, mean absolute and largest residual
in bp and RMSE in price; the five largest residuals; objective, convergence, iterations; NSS parameters, the
objective of every start and how many reached the best; spline `lam`, `edf` and the score of every `lam`;
warnings (`params_at_bound`, `unstable_optimum`, `short_end_extrapolated`, `not_converged`, `suspect_residuals`,
`quote_mismatch`, `lambda_at_grid_edge`, `outlier_rule_stopped`, `no_kind`, `no_trade_data`); and leave-one-out
stability: every bond in the fit is refitted out of it, giving its out-of-sample residual, the refit's par yield
at its maturity and the largest move of the par curve inside the refit's data range, summarised as an
out-of-sample RMSE (also without the two end bonds, whose refits extrapolate) and the most influential bond.
The refits are local: NSS's start from the full fit, the spline's keep its knots and `lam`. A refit can therefore
stay in the basin a mispriced bond pulled the full fit into (the 4.76 to 5.18 bp above); refitting each bond from
all 22 starts as well found the exact 5.00 there but costs 20 to 80 times as much, and at an extrapolated end its
optimum is no better (the -81.4 bp above). Each bond in the fit has its `leverage`. `curve_id` hashes the
settlement date, the settings and the bonds in the fit; `settings_id` the settings alone (equal across days
fitted the same way; the anchor's rate, that day's overnight rate, counts only as being set, so a history
anchored at each day's own call rate is one history).

**Rich/cheap.** `z_ts = (r_t - mean) / sd` over the same ISIN's previous 60 observations (at least 20; the
current one excluded; ddof 1); `z_cs = (r - median) / (1.4826 MAD)` across bonds of the same date and maturity
bucket (0-1, 1-3, 3-5, 5-10, 10-20, 20+ years). `rich_cheap_table` refuses history on or after the day it
scores and history fitted with another method or settings, names the ISINs without history, and scores the
in-fit residual (`resid_yield_bp`) or the out-of-sample one (`loo_resid_yield_bp`, `--residual loo`): which one
S3 and S5 trade is the user's decision (the in-fit residual understates the end bonds, the long end of S5 most;
the out-of-sample one measures each bond against a curve that never saw it). Only each ISIN's last 60 residuals
enter, so a 10-year daily history costs no more than a quarter's: 130,000 rows score in 0.4 s. Butterflies:
`2 y_body - y_short - y_long` in bp on market yields (positive: the body is cheap), the same on model yields,
and their difference, the residual fly; wings DV01-neutral at half the body's DV01 each. Pairs: `y_long -
y_short`, model and residual spreads, the shorter leg's face DV01-neutral; each leg's carry, the short one
included, comes from `funding.carry` (`side="short"`).

A residual is not yet a rich/cheap signal: it still holds what the curve does not model, namely coupon level
(premium against discount bonds), liquidity and benchmark status, FAR eligibility and index inclusion (FAR
from April 2020, index inclusion from June 2024), outstanding stock, age since issue and recent reissues, and
special repo demand. `residual_drivers` joins those free data give, as of each row's date: coupon, issue date
and age and the FAR flag from the security master, the price file's benchmark flag, reissues and days since the
latest issue or reissue from the issuance ledger (events dated after the row are not seen), and the outstanding
stock of `issuance.outstanding_on` on the row's date (`stock_complete` says whether the ledger held every event).
The master's FAR flag comes from RBI's latest FAR list, so a back-test day before that list can see a later
designation. `control_residuals` (opt-in, UNVERIFIED form) regresses one day's in-fit residuals on chosen drivers
(default coupon, age, log stock and FAR) with an intercept by least squares and keeps what they leave,
`resid_ctrl_bp`; a day with fewer rows than the drivers plus six is not controlled, a driver that does not vary is
dropped. With 20 to 60 bonds a day the drivers absorb any true richness that lines up with them, so read the
controlled residual beside the raw one. Not joined: liquidity beyond the price file's trade counts and volumes,
special repo and index inclusion (CCIL's trades and repo by security, licensed; the index providers' lists, not
kept).

### Conventions and their sources

| Rule | Engine | Source | Status |
|---|---|---|---|
| G-sec and SDL cash flows, 30E/360 accrual, semi-annual yield | `bonds.FixedRateBond`, `GSEC`, `SDL` | [`conventions.md`](conventions.md) | Verified there |
| T-bill price `100 / (1 + y d / 36500)`, one flow at maturity | `bonds.DiscountInstrument`, `TBILL`, `CMB` | FIMMDA Handbook 2026, 6.7.1 | Verified there |
| Time to each flow | Act/365F years from settlement to the scheduled date (`flow_dates="payment"` for holiday-adjusted dates) | None found | UNVERIFIED (the standard choice) |
| Zero rates continuously compounded | NSS and spline | ECB Svensson series titles (fetched 19 Nov 2026); CCIL inferred from one bond | Verified for the ECB; CCIL partly |
| NSS formula | `nss_zero` | ECB parameters and spot rates of 3 Jun 2024 reproduced to 6e-11 (test) | Verified |
| Objective in yield errors | `objective="yield"` | ECB series titles; CCIL's weighted price errors through search summaries | Choice |
| Universe filters: volume ₹5 crore, 3 months, bills 7 days, kinds and flags | `FitSettings` | CCIL's MTM rule uses trades of ₹5 crore or more (as a fitting threshold: none) | UNVERIFIED |
| Model-risk threshold 3 bp in par yield, judged at each bond on the two curves refitted without it | `MODEL_RISK_BP`, `model_risk_flags(check_residuals=)` | None | UNVERIFIED |
| On a day with suspect bonds (out of sample above 10 bp and four robust sd), a flag stands only if the gap persists on both curves fitted without the bond and the suspects | `suspect_bonds`, `model_risk_flags(refit=refitter(...))` | None (chosen on synthetic outliers and bumps, below) | UNVERIFIED |
| The anchor's rate is the day's data: `settings_id` hashes only whether it is set | `PER_DAY_SETTINGS` | None | Choice |
| Benchmark points below half a year restated from the simple par rate to semi-annual | `benchmark_frame` | The conversion is exact; the reading of a one-period par yield is a choice | Choice |
| An unclassified row kept out of the fit; a frame without trade data fitted whole, with a warning | `no_kind`, `no_trade_data` | None | Choice |
| Z-score window 60, minimum 20, maturity buckets | `ZSCORE_*` | None | UNVERIFIED |
| Par yield at tenors that are not whole half-years | A new issue at par whose short first period pays its share of the coupon (so 3 months gives the simple rate) | None | UNVERIFIED |

### Choices this build made beyond the curve-fitting proposal

The proposal behind this module (19 Nov 2026) left these open or set them otherwise; each was decided on runs
that are in the scratch notes of the job and summarised here.

- **The spline's penalty criterion is leave-one-out cross-validation, not GCV, and its grid starts at 1e2.**
  With the proposed GCV over 1e-2 to 1e4, SDL cross-sections without bonds under four years got forward rates
  of 48% (9 Nov 2026), 351% (27 Aug 2024) and 1,163% (6 Feb 2024) below the shortest bond, with out-of-sample
  RMSEs of 84 to 1,359 bp. On these data the penalty scale makes `lam` below about 100 almost no penalty, and
  GCV averages the leverages, so end bonds whose leverage is near one cannot object to a wild end. Leave-one-out
  CV, `mean((e_i / (1 - h_ii))^2)`, with the grid 1e2 to 1e8 kept every one of seven SDL days and five noisy
  synthetic sets without short bonds in bounds (out-of-sample RMSE 1.5 to 5 bp, forward rate at the start
  3.4% to 7.3%; GCV on the same grid still failed on two SDL days and two synthetic sets) and chose the same penalties as GCV on full synthetic cross-sections
  with 0, 2 and 5 bp of noise. GCV stays available (`lam_criterion="gcv"`). The grid's upper end moved from 1e4
  because GCV kept falling there on noisy synthetic data (it chose 1e6 and 1e7 at 2 and 5 bp of noise once
  allowed).
- **NSS iterations: 500, not 200.** On the SDL day of 19 Mar 2024 the best start crawled along a flat valley
  (all 22 starts within 2.4% of the best objective, `b0` from 0 to 30 across them) and stopped at 200
  iterations; it converged at 252 (rerun on 19 Nov 2026), moving the par curve by 0.004 bp inside the data
  range. The warning now says how fast the objective was still falling.
- **The spline's score leaves the anchor out** (a fix after review): see Method.
- **Model risk on leave-one-out curves** (a fix after review): the proposal compared the two full curves only.
- **Bills are filtered by days (7), not by the 3-month rule for bonds,** so the 91-day bill stays in.
- **Iterative outlier rejection and a flat one-rate method** were added (the proposal had Huber only), and the
  leave-one-out diagnostics.
- **A CLI command** (the proposal had none), so a price file can be fitted without code.
- **Drivers joined, controls opt-in** (a gap closed after review): `drivers.csv` beside the residuals whenever
  a master or ledger is given; the regression only on `--control`, because which drivers to control for, and
  whether one day's cross-section can carry them, is the owner's call.
- **Model risk judged again without the day's suspect bonds** (a fix after verification). Two other rules were
  tried on synthetic outliers (15 to 40 bp, one or two, exact or with 1 bp of noise) and bumps (10 to 30 bp at 11
  years): leaving out each flagged bond's largest-residual neighbour cleared every outlier's flags but also the
  10 bp bump's (gaps -2.61 and +1.67 bp once its most deviant bond was gone), and Huber refits kept the flag beside a
  high-leverage outlier (+8.66 bp at the bond next to a 20 bp cheap longest bond). Tying the second look to suspect
  bonds cleared every outlier's flags and kept every bump's, but for two edge bonds of a 30 bp bump whose core bond
  is itself suspect (5 of 7 flags kept).
- **Leave-one-out refits stay local** (a choice after verification): they start from the full fit. Refitting from
  all 22 starts as well would make the 5 bp cheap bond's out-of-sample residual exactly 5.00, at 20 to 80 times
  the cost, but where the refit extrapolates (the shortest or longest bond) its lower objective is no sign of a
  better value (-81.4 bp for a fair 1-year bond, above).
- **One history across anchored days** (a fix after verification): `settings_id` used to hash the anchor's rate,
  so a history anchored at each day's call rate (the remedy for SDL curves without bills) was refused from the
  second day.

### What was checked, with the numbers

All in [`tests/test_curvefit.py`](../tests/test_curvefit.py), synthetic or typed in with citation:

- ECB Svensson curve of 3 Jun 2024: four spot rates to 6e-11.
- 29 synthetic G-secs priced off NSS (7.6, -2, -1, 2, 1.5, 8), settlement 16 Nov 2026: parameters to 5e-12,
  residuals 5e-12 bp, zero rates 0.5-40 years 4e-12 bp, 8 of 22 starts at the optimum, 0.5 s. Inverted
  and humped curves (with five T-bills) and both price objectives recover to 1e-6.
- The spline on the same bonds: zero rates within 0.05 to 0.09 bp of the truth at `lam` 0.01, 1 and 100.
- Identities on both curves: `DF = exp(-z t)` to 5e-17; `z t` equals the Simpson integral of the forward curve
  to 6e-8 bp; the forward equals `-d ln DF / dt` to 4e-7 bp; par bonds price at 100 to 3e-14.
- A bond priced at the model price has a residual of 8e-13 bp; one priced 5 bp cheap shows +5.000 bp.
- The 182-day bill of 11 Nov 2026 on a flat 6.5% continuous curve: price 96.810865, yield 6.606483%,
  residual -8.82 bp against its 6.5183% cut-off.
- Z-scores: residuals 0 to 59 then 100 give z = 4.0368; the first 20 dates have none.
- Noise of 2 bp: RMSE 1 to 2.5 bp, zero rates within 6 bp of the truth for both curves, out-of-sample RMSE
  above in-sample. One bond 30 bp cheap: plain least squares moves its neighbours by more than 1 bp and names
  it; Huber keeps them within 1 bp; rejection measures it at 30.000 bp out of sample.
- Determinism: reruns and shuffled rows give the same bytes, diagnostics and `curve_id`; out-of-fit rows and
  scaled volumes leave the residuals unchanged (to 1e-6 bp under liquidity weights, whose weights then differ
  in the last bit).
- Refusals with the reason: fewer than 8 bonds or 4 distinct maturities (NSS; spline 6 and 3), duplicate ISINs,
  unknown kinds, unreadable cells, a bond in the fit without a usable price, rows of another settlement date,
  a settlement on a weekend or holiday, a strict calendar that does not cover a payment date, a history fitted
  with another method or settings.
- Fixes after review, each with a test that fails on the code before it: the anchored spline on sparse exact
  bonds (RMSE below 0.01 bp, zero rates within 0.5 bp, no model-risk flag); z-scores counting prior
  observations in one pass (26,000 rows with 30% missing in under 3 s, against 11 s before); benchmark points
  of 0.1 to 0.5 years on a flat 6.5% continuous curve read back as `e^0.065 - 1` annual to 1e-9; a bond 10 bp
  cheap at the long end: in-fit 7.06 bp, out of sample 9.75 bp (a local refit, above), flagged on the full curves
  and not on the refitted ones; in-fit equal to `(1 - leverage)` times out of sample within 0.01 bp for a 2 bp
  error, and NSS leverages summing to its six parameters; pairs DV01-neutral; `no_kind` and `no_trade_data` named;
  `append_history` replacing a refitted day and refusing other settings; `--residual loo`, `--append-history`,
  `--pair` and missing files (exit 2) on the command line; drivers joined as of the day (a reissue four days
  later unseen, another day's stock unused, a switch not counted as an issue); the controls recovering five
  known coefficients to 1e-9 on 30 synthetic bonds, an out-of-fit bond 10 bp cheap still 10 bp after them, a
  constant driver dropped and a 6-bond day left uncontrolled; `drivers.csv` and `controls.csv` from `--ledger`
  and `--control`.
- Fixes after verification, each with a test that fails on the code before it: a history of fits anchored at
  5.61% and then 5.65% appended and scored, in code and with `--anchor --append-history` then `--history` on the
  command line; one bond 20 bp cheap on the exact curve, at the long end and mid-curve, leaving no model-risk flag
  once judged without it (the three neighbours' and the bill's gaps then 0.00 to 0.05 bp), a 10 bp bump at 11
  years keeping its flags, a refit that fails keeping the flag, the command line naming the suspect and the flags
  cleared. A test that `rich_cheap_table` on 150 days equals `residual_zscores` on the whole panel now catches a
  history trimmed to 59 days, a mutation the earlier tests let through.
- The CLI end to end, twice, with identical bytes. Fourteen deliberate mutations of the module were each caught
  by a test: the residual's sign, the antiderivative's indexing, the volume filter, the look-ahead guard, the
  short first par period, the z-score window, the Huber weights, the outlier floor, the bill's flow date, the
  DV01 wings, the spline's default criterion, the leverage term of its CV score, one column of the NSS
  Jacobian and the range over which leave-one-out shifts are measured.

**Free data** (run in scratch, not in the repository): the auction ledger's cut-offs. On the 19 priced SDL
lines of 9 Nov 2026 (settlement 11 Nov, shortest 2030) NSS gives an RMSE of 3.10 bp, its short end on the
`b0 + b1 >= 0` bound; anchored at the call rate of 13 Nov 2026 (5.61%), 3.32 bp with `z(0+)` at 5.609%, and
the anchored spline (`lam` 1e7, out-of-sample RMSE 4.53 bp) agrees within 2.8 bp at 5 to 29 years. Left out of
the fit, the shortest line is missed by 49 bp: its leverage is 0.97. On the refitted curves, the two shortest
lines and the longest (leverages 0.79 to 0.97) differ by more than 3 bp, the lines of 10 to 25 years do not. The two
shortest are also suspect bonds (out of sample -48.6 and +19.1 bp, extrapolations rather than proven mispricings),
so the flags are judged again without them: the two shortest stay flagged (+41.8 and +25.0 bp), the longest line's
gap falls from 8.05 to 2.22 bp and its flag is cleared, through NSS's coupling of the two ends. Suspects are judged
out of sample so that an outlier at an end counts (its in-fit residual hides it); the price is that a bond alone
at an end can be a suspect too, as here. No cut-off carries trade data, so every fit warns `no_trade_data`.
Six one-day SDL cross-sections of 2024 (21 to 33 new issues at par) give RMSEs of 1.1 to 2.2 bp. G-sec auction
days have 2 to 7 lines; pooled over a quarter at unchanged yields (8 to 10 distinct ISINs) they give 3.9 to 9.1 bp,
mostly the curve's move between auction days. The three T-bills of 11 Nov 2026 and the 13 Nov 2026 ten-year,
four lines, are refused by NSS. None of this is a rich/cheap signal: state spreads, primary morning prices,
pooled days and no residual history.

**Not checked:** any secondary-market G-sec price or trade file; any fit on more than 34 bonds or over a
history of days (stability from day to day, mean reversion of residuals; only the z-scores' speed on a
synthetic 1,000-day panel); `flow_dates="payment"` beyond a lenient calendar; CCIL's or FBIL's own curves
against this one (CCIL's parameters were used only as a sanity check by the proposal: its website data are for
non-commercial use); the drivers' regression on real data (only on synthetic residuals with known
coefficients, recovered to 1e-9), a point-in-time FAR history, and the drivers nothing joins (liquidity beyond the
price file, special repo, index inclusion); the suspect-bond rule of model risk beyond 15 synthetic days (outliers of
15 to 40 bp, bumps of 10 to 30 bp, noise of 1 and 2 bp); out-of-sample residuals from multi-start refits, beyond the
208 refits of eight synthetic days compared in scratch.

## OIS curves, asset swaps, funding and the policy path

For S1 (buy a G-sec or SDL, pay fixed in MIBOR-OIS, fund the bond in repo) and S7 (the policy path the OIS curve
implies). Built ahead of any licensed data: FBIL's overnight MIBOR and MIBOR-OIS need a subscription to store or
back-test, and a curve bootstrapped from FBIL's quotes is a valuation model under clause 5.1 of FBIL's terms
([Licences](#licences)).

### What it does

| Piece | Function | Output |
|---|---|---|
| Contract | `MIBOR_OIS`, `SORR_OIS`, `FCS_MIBOR_OIS` (`OisConventions`) | The rules of each contract, with their source and status in the field comments; the offshore draft is a record only (its New York calendar and rate cut-off are not built, so its cash flows are refused) |
| Schedule | `effective_date`, `ois_schedule` | T+1 effective date; one period up to a year, semi-annual beyond, Modified Following on the Mumbai calendar |
| Floating leg | `compound_overnight`, `settlement_amounts` | Daily compounding of fixings over business days, simple across the others; the rate unrounded and rounded; a settlement statement (to the paisa, net to the rupee) |
| Curve | `bootstrap_ois` → `OisCurve` | Discount factors from one day's par quotes and the trade date's overnight fixing; zero rates (continuous, semi-annual, annual, simple), instantaneous and simple forwards, overnight forwards for any span, the curve seen from a later date (`rebase`), bumped quotes (`bumped`), a node table and a monthly table (`curve_table`) |
| Swaps | `OisSwap`, `ois_par_rate`, `ois_npv` | Par rate; value, legs, annuity, PV01, DV01 to zero rates and to par quotes, accrued interest of a running period from its fixings |
| Asset swaps | `asset_swap`, `asset_swap_table`, `z_spread`, `hedge_notional` | Yield-yield on a matched basis, par-par, proceeds and z-spreads (continuous and semi-annual) of G-secs, SDLs, T-bills, CMBs, CP and CD against any discount curve; the OIS notional matching a bond's DV01 |
| Funding | `funding_rates`, `fixings_from`, `fixing_times_from`, `fbil_published_at` | The RBI store's `money_market_daily` as one segment's daily rate (TREPS by default, market repo, call or all overnight), dealing days, corridor flags, quarter-end flags and the time each rate became public, cut at an `as_of` time (a row revised after it counts as not yet public) |
| Haircuts | `read_haircuts`, `haircut` | A dated haircut table kept by the user, looked up by ISIN, kind, residual maturity and liquidity class |
| TREPS caps | `treps_class`, `treps_shares` | CCIL's collateral concentration caps (RMD/SS/26/13 from 2 Jun 2026; before it the earlier rule as CCIL's FAQ summarises it, UNVERIFIED): what TREPS lends against each SDL and illiquid G-sec of a book |
| Carry | `carry`, `carry_table` | Coupon income, pull to par, repo interest (split between TREPS and another source where CCIL's caps bind, plus a funding spread), the haircut's equity cost and an OIS's fixed and floating accruals period by period, over a horizon, as known at an `as_of` time; long or short (a short borrows the bond at a special repo rate the user gives) |
| Policy path | `load_mpc_calendar`, `decision_effective_dates`, `step_overnight`, `par_rates_on_path`, `implied_policy_path`, `mibor_repo_spread`, `check_policy_changes` | The MPC dates (a shipped file with its years declared and the day each date became public, as known on any day, projection on request), the step model, model par rates on a path, the level and change after each meeting decided after the trade date with what the quotes identify, the MIBOR-repo spread from public rates (the previous regime's just after a change), repo-rate changes matched to decisions |

```bash
cr-strat ois-curve --quotes data/raw/ois_20261119.csv --trade-date 2026-11-19 --overnight 5.60 \
    --project-calendar --out-dir data/out/ois_20261119
cr-strat asset-swaps --quotes data/raw/ois_20261119.csv --trade-date 2026-11-19 --overnight 5.60 \
    --project-calendar --bonds data/raw/bonds_20261119.csv --master data/out/sovereign_master.csv \
    --out data/out/asset_swaps_20261119.csv
cr-strat repo-carry --store data/rbi_store --bonds data/raw/positions.csv --haircuts data/raw/haircuts.csv \
    --start 2026-11-20 --end 2026-12-21 --as-of 2026-11-20T09:30 --project --pay-fixed 6.13 --swap-tenor 4Y \
    --fixings data/raw/mibor.csv --out data/out/carry.csv
cr-strat policy-path --quotes data/raw/ois_20261119.csv --trade-date 2026-11-19 --policy-rates data/raw/policy_rates.csv \
    --store data/rbi_store --project --project-calendar --out-dir data/out/policy_20261119
```

Quotes: `tenor` and `rate_pct` (`1M` ... `5Y`, `7Y`, `10Y`), or the CSV `cr-strat fbil-mm --kind miborois` writes,
whose `rate_quoted_pct` and `quote_convention` are used (a file with only the converted `yield_pct` is refused);
`policy-path` also takes `end_date` and `rate_pct` for single periods. Bonds: `isin` (each once), `clean_price` (or
`yield_pct`), `kind`, `coupon_pct`, `maturity_date` (or `--master`), optional `settlement_date` (a business day);
positions add `face` (rupees), `liquidity` (`liquid`, `semi_liquid`, `illiquid`: CCIL's class, which also decides
its TREPS cap), `haircut_pct`, `side` (`long` or `short`) and, for a short, `special_repo_pct`. Haircuts:
`effective_date`, `isin` or `kind`, optional `min_years` and `max_years` (residual maturity in 365.25-day years,
lower bound excluded), `liquidity`, `haircut_pct`, `source`. Fixings: `date`, `rate_pct` (overnight MIBOR, each
date once) and optional `public_from` (a blank cell, or no column, is FBIL's publication time of that day; a blank
cell used to count as never public). The MPC file:
`meeting_start`, `decision_date`, `fy`, `kind`, `announced`, `source`, `confirmed`. The policy table is
`rbi_store`'s (`effective_date`, `repo_pct`, `sdf_pct`, `msf_pct`, `source`) or `macro_store`'s
`policy_rates_bis` (on an MPC decision day, a change it dates the next business day makes `policy-path` exit 2 and
ask for `--policy-rate`). `--project-calendar` (all four commands) is needed for anything past 2026 (the holiday list
ends there) and the outputs then say `calendar_projected`. A row that cannot be measured or carried keeps its
reason in `error`; a command exits 1 when no row could be, 2 on bad input or a missing file. The same inputs give
the same bytes.

### Method

**The contract** (FIMMDA's [Handbook of Market Practices 2026](https://www.fimmda.org/UploadPopupPageFiles/Fimmda_Handbook_2026_Final_Ver.pdf),
6.10.2, unless said otherwise). The floating leg compounds FBIL's overnight MIBOR on Mumbai business days,
`A = prod (1 + R_i n_i / 36500)`, `n_i` the calendar days from business day `i` to the next one (or the period's
end), so a Friday's fixing covers the weekend; the fixing of a day accrues from that day, with no lag or lookback
onshore. Both legs are Actual/365 fixed and the fixed rate is simple per period. The effective date is the next
Mumbai business day (never a Saturday); up to a year the swap has one exchange at maturity, beyond it semi-annual
periods on both legs; Modified Following applies. Saturdays are not MIBOR business days, so the Mumbai settlement
calendar serves (an equivalence reasoned, not stated by FBIL or FIMMDA). For days without a fixing the handbook
has rules only for unscheduled holidays (7.4, a section it says applies to trades not settled through CCIL): MIBOR
not published on a holiday known the working day before, the day accrues as a holiday (`missing="holiday"`); a
holiday declared on the day before MIBOR came out, the previous day's rate (`"previous"`). For FBIL not
publishing at all, 6.10.2(k)(1) points to the ISDA fallbacks, which are not built. The default `"raise"` names
the missing dates instead, because a gap in the fixings given is not evidence of either case. The compounded rate
is rounded to 0.0001% and amounts to the paisa, net to the rupee, but only in `settlement_amounts`: valuation never
rounds. The handbook's one-week example matches the unrounded rate, its text the rounded one (₹9,480 against ₹9,479
net); the default follows the text and `rate_decimals=None` the example.

**The curve.** Discount factors from the trade date, `t` in Act/365F years. The first node is the effective date,
`P(S0) = 1 / (1 + R_ON (S0 - T) / 36500)` with the trade date's overnight fixing; a single-period quote gives
`P(E) = P(S0) / (1 + K tau)`; a semi-annual quote solves `ln P(E_n)` by Newton inside a bracket (bisection when a
step leaves it) so that `K sum tau_k P(E_k) = P(S0) - P(E_n)`, the period ends between nodes interpolated. `ln P`
is linear between nodes (flat forwards). Each quote must reprice to 1e-8% or the bootstrap fails. FBIL's rates are
read as par rates of standard spot-starting swaps, simple up to a year and semi-annual beyond, the reading
[`fbil.py`](../src/cr_strat/fbil.py) already makes; the bootstrap uses the quoted rate on the real schedule dates,
never FBIL's rate converted to annual compounding on 365/12-day months. Past the last node the curve gives NaN
unless `max_extrapolation_years` lets the last forward rate run on (then `extrapolated` says so); the pricing
functions refuse dates it does not cover. Longer tenors (6 to 10 years and beyond, which FBIL does not publish) come
from the user. One curve forwards and discounts, as the handbook values OIS at the MIBOR-OIS rates (6.10.5).

**Valuation.** Fixed leg `N K sum tau_k P(E_k)`; floating leg `N (P(S_k) - P(E_k))` for a period to come and
`N (A_realised - P(E_k))` for the running one: the handbook's unwind rule (6.10.5), the floating leg with its
notional worth the notional compounded to date. Fixings before the valuation date are realised; the valuation
date's own is in the curve's first node. `dv01` is the value lost when every continuously compounded zero rate
rises 1 bp, `dv01_par` when every par quote does and the curve is rebuilt (the trade date's fixing, a published
rate, stays); both positive for a receiver, as a long bond's DV01 is.

**Asset swaps** (all per ₹100 face). Settlement `s`, dirty price `P` from `bonds`, the bond's flows on their
scheduled dates, `D(x) = DF(x) / DF(s)`; the asset swap floats on the bond's coupon dates, its first period a short
stub from `s`, Act/365F. `A = sum tau_j D(e_j)`. Yield-yield: the bond's yield less `K_match = (1 - D(T)) / A`,
the OIS par rate on that schedule. Par-par: `(sum CF_i D(t_i) - P) / (100 A)`. Proceeds: par-par `x 100 / P`.
Z-spread: the parallel shift of continuously compounded zero rates that prices the bond (Act/365F from `s`), and a
semi-annual variant. The curve can be any discount curve: against `curvefit`'s fitted curve the z-spread is the
bond's spread to the sovereign curve. The hedge notional matches the bond's DV01 with a spot swap's annuity DV01
(`N = F DV01 / 100 / (annuity x 1e-4)`).

**Funding and carry.** `money_market_daily` has a row for every calendar day; weekend and holiday rows have no rate
and are never filled forward. Each calendar day of the horizon accrues at the rate of the latest dealing day on or
before it (a Friday's rate covers the weekend; a Saturday on which money markets dealt is a dealing day of its own),
simple interest paid out at each roll by default (`repo_compounding="compound"` rolls it in). Cash borrowed is
`(1 - h) P F / 100`, the TREPS first leg; the haircut `h P F / 100` costs the same daily rates (`equity_rate_pct`
for another). `spread_bp` (`--funding-spread-bp`) adds a spread to every repo rate of a long: a fund below CCIL's
₹200 crore net-worth bar deals through a bank or primary dealer, at a spread no source gives (UNVERIFIED default 0).
It does not touch a short, whose reverse repo is at the special rate the user gives (any spread belongs in that
rate) and whose proceeds kept earn the plain rates; its notes say the spread was not applied (they used to say it
was). Income is the coupons due in the horizon plus the change in accrued interest, from `bonds`; the pull to par is
the clean price's move at the purchase yield. With an OIS, fixed accrues at `N K days / 36500` and floating
calculation period by period, `N (A(p.start, hi) - A(p.start, lo))` over each overlap `[lo, hi)` of a period with
the horizon: a running swap's interest compounds from its period's start (its fixings are needed), and interest paid
at a period's end is not compounded on. Before this was fixed, the carry of a 4Y swap struck on 20 Nov 2026 was 13
bp a year on face too low for 20 Apr to 20 May 2027 and 8 bp too high over a year across the 20 May payment. A rate
below the SDF or above the MSF is flagged, never clipped. `as_of` keeps only rows public by then (RBI publishes a
day's Money Market Operations the next working day at about 9:00; the one stored weekend came around noon), none
revised after it (the store keeps a revised row's latest values only) and nothing after the first row that was not;
and only the MIBOR fixings published by then (FBIL's 12:45 on the day, or a `public_from` per fixing: the call rate
standing in for MIBOR comes out the next morning), so an expected carry never sees a later rate or fixing. In the
library's `fixing_times` (`fixing_times_from` for the call rate) a day without a time counts as not known, never as
FBIL's 12:45, which would look ahead for a rate published the next morning; a fixings file's blank `public_from`
cell is FBIL's time, as the file layout says. A horizon past the last known row, or past the last known fixing, is
refused unless `projection="last"` holds the last dealing rate and the last fixing (counted and noted, quarter-ends
in the projection flagged); `projected_fixings` (a curve's `overnight_forwards`) can project the floating leg
instead. A missing row inside the table is always refused: it cannot be told from a dealing day.

**TREPS caps.** CCIL lends against liquid and semi-liquid G-secs in full, but from 2 Jun 2026 (RMD/SS/26/13, effective
at the end of 1 Jun) against illiquid G-secs and SDLs together only up to 50%, and against SDLs up to 30%, of the
haircut-adjusted value of the liquid and semi-liquid G-secs deposited, illiquid G-secs served first. `carry_table`
treats a book funded in TREPS as deposited whole and applies these caps to its long positions: each capped row gets
its class's share (`funding_share`) at TREPS rates and the rest at `excess_funding`'s (`repo-carry
--excess-source`, market repo by default), or is refused without one. An SDL-only book gets no TREPS funding at all.
A G-sec without a liquidity class counts as illiquid (the conservative reading); T-bills and CMBs, which CCIL lists
without a class, are taken as uncapped and outside the caps' base (UNVERIFIED); CP, CD and special securities,
absent from CCIL's list, are refused on TREPS. Before 2 Jun 2026 the earlier rule (RMD/SS/21/21 of 16 Apr 2021, not
read) is applied as CCIL's TREPS FAQ states it through a web-search summary (UNVERIFIED): illiquid G-secs and SDLs
together up to 20% of the same base, illiquid first, nothing on a stand-alone basis, from 16 Apr 2021; before that no
rule is known and TREPS is taken to lend nothing against them (the conservative reading). Before this was built,
every SDL and illiquid G-sec was funded in full in TREPS before June 2026, so a back-test across 2 Jun 2026 jumped
from full TREPS funding of an SDL-only book to none; now it has none on both sides, and an SDL beside a liquid G-sec
gets 21.5% of its cash from TREPS in May 2026 and 32.3% in June (the verifier's synthetic book).

**Shorts and pairs.** `side="short"` carries a bond borrowed in a reverse repo at a special repo rate the user gives
(`reverse_repo_rate_pct`; CCIL's repo by security is licensed): the coupons and the pull to par are paid away,
interest is earned on the cash lent, `(1 - h)` of the sale proceeds, and on the proceeds kept. A long and a short of
the same bond at the same rates net to zero. With a swap, a short receives fixed. Together with `curvefit`'s
`bond_pairs` this carries and sizes S3 switches and S5's 10-year against 30-50 years.

**Not in any carry** (the return engine charges them: [below](#the-return-engine-and-costs)): bid-offer; CCIL's
settlement charges (₹95 per crore of face for outright dated securities,
₹45 for T-bills, ₹10 per crore a repo leg and ₹10 per crore of TREPS repayment value, plus GST:
[`rates_data_sources.md`](rates_data_sources.md), CCIL); brokerage and taxes.

**The policy path.** The MPC file ([`src/cr_strat/data/mpc_meetings.csv`](../src/cr_strat/data/mpc_meetings.csv),
years declared in `mpc_meetings_years.csv`) holds FY2026-27: decisions on 8 Apr, 5 Jun, 5 Aug, 7 Oct and 4 Dec 2026
and 5 Feb 2027, from RBI's release of 23 Mar 2026 as the press reported it (the February date on fewer reports:
`confirmed = no`), each `announced` on 23 Mar 2026 (for an unscheduled meeting, the day it was called). A meeting
horizon past 31 Mar 2027 raises unless `project=True` adds FY2026-27's scheduled meetings moved 52 weeks later
(7 Apr, 4 Jun, 4 Aug, 6 Oct and 3 Dec 2027, 4 Feb 2028), flagged `projected`. `load_mpc_calendar(as_of=day)` gives
the calendar as known that day: unscheduled meetings called later are left out, and a year whose schedule came out
later is projected (on request) or refused; `attrs["covered_until"]` says how far the meetings are complete. Add
FY2027-28 when RBI publishes it (around March 2027): its six rows with the release's date as `announced`, and
`2027-28,yes,<source>` in the years file.

Overnight MIBOR is the repo rate after the last decision in force plus a spread; a decision takes effect the
business day after it is announced (the alternative: the same day). The starting level is the repo rate known on
the trade date, after any decision announced that day (it comes out at about 10:00, FBIL's MIBOR-OIS session runs
to 5 PM): `policy-path` takes it from `--policy-rates` on the trade date itself. It used to take the business day
after, which on the eve of a decision is that decision: with quotes on 3 Dec 2026 built from a 25 bp hike on 4 Dec,
the December move came out as -1.93 bp (quote error 0.68 bp) where the rate known on 3 Dec gives +25.00 bp. On a
decision day itself a table that dates that day's change on the next business day (BIS dates some so) would give
the pre-decision rate: the command now exits 2 and asks for `--policy-rate` (it used to start from 5.50% on 4 Dec
2026, put +81.25 bp on February with quote errors of 25.2 bp, and exit 0 with two notes). Every
meeting decided after the trade date is a step, also under `"same_day"` when it takes effect on the swaps'
effective date. The meetings must reach the last quote's end: the shipped file without `until` stops at 31 Mar
2027, and on 1Y quotes it used to put April's move on February's meeting (+27.1 and -19.7 bp for +25, 0). The
coverage is checked against the meetings actually given: a calendar's declared years end the day before any meeting
the frame lacks after the trade date, one dropped by `include_unconfirmed=False` (`attrs["left_out"]`) or filtered
out after loading (pandas keeps `attrs` on a filtered copy; the frame's dates are checked against
`attrs["decision_dates"]`); a plain list of dates, or a frame without that record, counts each decision as vouching
for `MPC_GAP_DAYS` (70, UNVERIFIED) after it, so a longer gap ends the coverage as the list's end does;
`covered_until=` given is the caller's word. Before this fix a filtered calendar gave +27.11 and -19.69 bp for +25
and 0 with a quote error of 3.37 bp, and `include_unconfirmed=False` +27.20, +26.52 and -36.59 bp, both reported as
identified and converged; both are now refused (past 6 Apr and 4 Feb 2027), and `diagnostics["meetings_coverage"]`
says why the coverage ends where it does. Effective dates are
computed only for decisions inside the horizon, so quotes ending in 2026 need no projected calendar. Each quote's
model par rate is exact on the step path (single period, or semi-annual on the path's discount factors), and
Gauss-Newton with an analytic Jacobian minimises the squared quote errors plus `lam` times the squared changes
between meetings, in basis points (`lam = 1e-6`). A level is `identified` when the quotes alone pin it: outside the
null space of their Jacobian. FBIL's MIBOR-OIS is the volume-weighted average of the whole session to 5 PM (spec
1.2): on a decision day it mixes quotes struck before and after the announcement, and set against a bond price of
another time of day (the morning auction cut-offs, say) it mixes sessions.

The spread is the median of the overnight rate less the repo rate over the 20 dealing days before the valuation
date in the current regime, which starts the business day after the last repo-rate change, using only rates
public by `as_of` (default: the start of the valuation date). On the decision day and the next two business days
the new regime has no public rate yet (the store's next-morning publication), so the estimate falls back to the
previous regime's last 20 days up to the day before the change (`fallback="previous_regime"`, UNVERIFIED, flagged
in the answer and printed); `fallback=None` refuses instead. FBIL's MIBOR is licensed, so the free stand-in is the
weighted average call rate, a whole-day average where MIBOR averages a morning window: for demonstrations only.

### Conventions and their sources

V verified (a primary source or a saved copy of one), P partly verified, U UNVERIFIED (no source: the standard
reading, a named parameter, a decision for the user). Sources are those of the spec behind this build (19 Nov
2026), read there; nothing was re-read for this build.

| Rule | Engine | Source | Status |
|---|---|---|---|
| Daily compounding of MIBOR on business days, simple across the others; a day's fixing accrues from that day | `compound_overnight` | Handbook 6.10.2(k)(1); FIMMDA's FCS-OIS draft (FIMCIR/2026-27/16): no fixing lag | V |
| Actual/365 fixed both legs; fixed rate simple per period | `MIBOR_OIS.day_count`, `year_days` | Handbook 6.10.2(c), (i) | V |
| Effective T+1 Mumbai business day, no Saturday | `effective_lag=1` | Handbook 6.10.2(e) | V |
| One period up to a year, semi-annual beyond | `single_period_max_months=12`, `period_months=6` | Handbook swap terminology; 6.16; the FCS-OIS draft | V |
| Modified Following on payment dates | `roll` | Handbook 6.10.2(f) | V |
| Accrual dates adjusted too | `adjust_accrual=True` | The FCS-OIS draft only | P |
| Payment on the period end | `payment_lag=0` (others refused) | Handbook confirmations; the FCS-OIS draft | P |
| Mumbai settlement calendar for MIBOR business days | `calendar_name` | Handbook 6.10.2(g), (h); equivalence reasoned | P |
| Periods counted forward from the effective date, no stub, no end-of-month rule; tenors above a year in whole 6-month periods | `ois_schedule` | None found | UNVERIFIED |
| Compounded rate to 0.0001%, amounts to the paisa, net to the rupee | `rate_decimals=4`, `amount_decimals=2`, `net_decimals=0` | Handbook 6.10.2(k)(1)(d)-(e); its example matches the unrounded rate | V (text), disputed by the example |
| Unscheduled holiday known the day before, no MIBOR: a holiday; declared on the day before publication: the previous day's rate (rules for trades not settled through CCIL); FBIL not publishing: ISDA fallbacks (not built) | `missing="holiday"`, `"previous"` | Handbook 7.4 (A row 6, B); 6.10.2(k)(1) | V; the default `"raise"` is a choice |
| FBIL's MIBOR-OIS rates are par rates of spot swaps, simple up to a year, semi-annual beyond | `ois_quotes` | FBIL's methodology (trades' volume-weighted average); the payment rule above | P |
| Flat forwards between nodes; no extrapolation past the last node | `OisCurve`, `max_extrapolation_years=0` | None (the standard choice) | UNVERIFIED |
| Discount OIS at MIBOR-OIS rates (one curve) | `bootstrap_ois` | Handbook 6.10.5 | V |
| Asset-swap floating leg on the bond's coupon dates, short first stub, Act/365F | `asset_swap` | None | UNVERIFIED |
| Z-spread on continuously compounded zero rates, Act/365F from settlement | `z_spread_bp` (semi-annual: `z_spread_sa_bp`) | None | UNVERIFIED |
| Matched yield-yield: the OIS par rate on the bond's schedule (simple with one coupon date left) | `k_match_pct` | None; the spec's ≤1-year rule set aside (below) | UNVERIFIED |
| TREPS interest simple Act/365; repo leg 2 = leg 1 plus interest | `carry` | CCIL's TREPS FAQ; RBI Repo Directions 2025 | V |
| Market repo interest Act/365 simple | `carry` | RBI's G-sec FAQ (money markets); 2004 LAF example | P |
| Repo interest paid out at each overnight roll | `repo_compounding="simple"` | None | UNVERIFIED |
| The haircut funded at the repo rate | `equity_rate_pct=None` | None (an opportunity cost) | UNVERIFIED |
| Haircuts from a dated table kept by the user; none assumed | `haircut` | RBI Repo Directions 2025 para 12; CCIL's TREPS list (restricted, not shipped) | V; market repo haircuts not found |
| TREPS caps: illiquid G-secs and SDLs 50%, SDLs 30% of the liquid and semi-liquid base, illiquid first, from 2 Jun 2026 | `treps_shares` | CCIL RMD/SS/26/13 of 29 Apr 2026 (saved copy) | V |
| Before 2 Jun 2026: illiquid G-secs and SDLs together 20% of that base, illiquid first, from 16 Apr 2021; nothing lent against them before | `TREPS_EARLIER_CAP`, `TREPS_EARLIER_FROM` | CCIL's TREPS FAQ through a web-search summary (19 Nov 2026); RMD/SS/21/21 of 16 Apr 2021 not read, its effective date taken as its date | UNVERIFIED |
| Each capped position its class's pro-rata share; T-bills uncapped and outside the base; a G-sec without a class illiquid | `treps_shares` | None (CCIL caps the member's total; T-bills carry no class in its list) | UNVERIFIED |
| A funding spread over a long's repo rates, default 0; none on a short's special rate | `spread_bp` | None (a constituent's spread is bilateral) | UNVERIFIED |
| A fixings file's blank `public_from` is FBIL's publication time that day; a day a `fixing_times` mapping lacks is not known | `cli._read_fixings`, `funding.fbil_published_at` | The file layout above; FBIL's time from `ois.fixing_regime` | Choice |
| A short borrows the bond at a special repo rate the user gives | `side="short"`, `reverse_repo_rate_pct` | Repo by security is CCIL's licensed data | Choice |
| Corridor SDF = repo - 25 bp, MSF = repo + 25 bp, symmetric since the SDF became the floor on 8 Apr 2022 (65 bp below repo before, in the pandemic years); kept by the 2025 framework: flagged, not clipped | `below_sdf`, `above_msf` | RBI press release 2022-2023/41 of 8 Apr 2022 ([prid 53539](https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=53539)) and RBI's Internal Working Group report on the liquidity framework (6 Aug 2025), saved copies; notifications of 7 Oct 2026 | V |
| MPC dates of FY2026-27; decision on the last day; announced on 23 Mar 2026 | `mpc_meetings.csv` | RBI's release of 23 Mar 2026 via press reports | P (`confirmed = no` for 5 Feb 2027) |
| Projected meetings: 52 weeks after the last complete year's | `project=True` | None | UNVERIFIED |
| The starting level is the rate known on the trade date; every meeting decided after it is a step | `implied_policy_path` | Follows from what is public when FBIL's session closes | Choice |
| Each decision of a list without declared coverage vouches for 70 days after it: a longer gap, or the list's end, ends the coverage | `MPC_GAP_DAYS` | FY2026-27's meetings are 58 to 63 days apart | UNVERIFIED |
| A meeting the frame lacks (dropped as unconfirmed, or filtered out) ends a calendar's coverage the day before it | `load_mpc_calendar` attrs, `implied_policy_path` | Follows from the MPC meeting then | Choice |
| On a decision day, a policy table's change dated the next business day is refused, not used or ignored | `policy-path` | BIS dates some changes a day late (`macro_store`) | Choice |
| A decision moves MIBOR from the next business day | `effective_rule="next_business_day"` | None (MIBOR's 9:00-12:00 window since 4 Aug 2025 straddles the 10:00 announcement) | UNVERIFIED |
| MIBOR-repo spread: the median of 20 dealing days in the regime; the previous regime's just after a change | `mibor_repo_spread`, `fallback` | None | UNVERIFIED |
| Penalty on changes between meetings | `lam=1e-6` | None | UNVERIFIED |

### Choices this build made beyond the proposal

- **Four modules, not one.** The proposal put everything in `ois.py`; contract and curve, asset swaps, funding
  and the policy path are separate modules (each with its tests), joined only by `ois.py`'s functions and, for the
  curve, by structure (`DiscountCurveLike`).
- **Yield-yield's matched rate is one formula at every maturity.** The proposal converted the single-period OIS
  rate to semi-annual compounding for bonds of a year or less. On bills priced exactly on the curve that gives -6.4
  to +8.3 bp (35 to 364 days) against 0 for the uniform rate, and on G-secs in their last coupon period, whose yield
  `bonds` gives as a simple rate, 2.4 to -4.8 bp against -3.2 to -5.8 bp. A bond on the curve still shows a
  yield-yield spread (0.03 to 2.2 bp beyond a year in those runs: its coupon and its 30E/360 day count) where
  par-par and z-spreads are zero.
- **Missing fixings raise by default**, not `"holiday"`: the handbook's rule is for days FBIL published nothing,
  which a gap in someone's fixings does not show.
- **No stub, no payment lag, no rate cut-off is guessed**: tenors above a year must be whole 6-month periods, and
  conventions with a payment lag, a rate cut-off or an unbuilt calendar are refused at use.
- **The policy path reports what the quotes identify.** The proposal read the 1-year quote as pinning October 2027;
  with June and August pinned only together (by the 9-month quote), October is not pinned either: the standard
  1M-1Y quotes identify December, February and April only (rank 5 of 6).
- **Exact recovery needs no penalty.** With `lam = 0` the exactly identified design (B10(a)) recovers the levels to
  2.8e-12 bp; the default `lam = 1e-6` moves them by up to 5.6e-4 bp.
- **Projection by 52 weeks**: the proposal's projected dates were picked by hand (6 Aug 2027 among them); the rule
  gives 4 Aug.
- **CLI commands** (the proposal had none), strict ISO times (`ois.as_time`: `23-11-2026` is refused, not read
  day-first), the MPC loader refusing rows of a year not declared complete, and both DV01s.
- **Carry reports the pull to par** as a line of its own, as the task asked; the proposal's example (B9) leaves it
  out, and the test checks its lines without it.
- **After review**: the floating leg accrued period by period (the proposal's `N (A - 1)` over the horizon holds only
  inside one period from its start), fixings cut at `as_of` like the funding rates, CCIL's TREPS caps applied to a
  book, shorts and a funding spread in carry; the policy path's start at the rate known on the trade date, its
  meetings point-in-time (`announced`, `as_of`) and checked for coverage, and a spread fallback after a change.
- **After verification**: the coverage checked against the meetings actually given (a filtered frame,
  `include_unconfirmed=False`, a list with a gap); a decision day with a policy table that dates the change a day
  late refused on the command line; a blank `public_from` cell read as FBIL's time; CCIL's earlier TREPS rule
  applied as its FAQ summarises it (UNVERIFIED) instead of full funding; no funding-spread note on a short.

### What was checked, with the numbers

In [`tests/test_ois.py`](../tests/test_ois.py), [`test_asset_swaps.py`](../tests/test_asset_swaps.py),
[`test_funding.py`](../tests/test_funding.py) and [`test_policy_path.py`](../tests/test_policy_path.py) (117 tests,
synthetic or typed in with citation):

- The handbook's one-week OIS (6.10.2): 6.9977269799%, floating ₹3,35,507.46 and fixed ₹3,26,027.40 unrounded,
  ₹9,480 net as the handbook prints; ₹3,35,506.16 and ₹9,479 with the rate rounded first. Its FRA (6.12.3):
  ₹22,43,836, ₹21,19,178, net ₹1,24,658, discounted ₹1,22,071.10.
- A bootstrap of synthetic quotes for 19 Nov 2026 (1M 5.62% to 5Y 6.18%, MIBOR 5.60%): ten discount factors within
  4.9e-13 of the spec's, every quote repricing to 1.8e-11 bp and every par swap to an NPV below 1e-12 per rupee,
  five-year zero rate 6.103835%, in 3.4 ms. Compounding the curve's overnight forwards over a year of business days
  gives its discount ratio to 7e-16; the unwind rule one month into a year's swap matches `ois_npv` to 6e-17.
- Identities: `DF = exp(-z t)` on four bases; the forward rate equals `-d ln DF / dt`; simple forwards compound back;
  the curve seen from a later date keeps its forwards; weekends, holidays (24 Nov 2026) and dealing Saturdays.
- 6.36% GS 2031 at 99.50 for 20 Nov 2026 on that curve: yield-yield 35.8876, par-par 34.7471, proceeds 34.3484,
  z-spreads 33.8574 (continuous) and 34.9258 bp (semi-annual), all within 5e-5 bp of the spec's; proceeds equals
  par-par x 100 / P exactly; a bond priced on the curve has zero par-par and z-spreads; on a flat 6% curve a bond
  priced at 6.42% has a z-spread of 42 bp (to 2e-13); against a fitted NSS curve plus 10 bp, 10 bp.
- Carry of ₹100 crore of 6.36% GS 2031 over 20 Nov to 20 Dec 2026, 4% haircut, repo 5.57%, paying 6.134480% on
  ₹100 crore against MIBOR at 5.60%: income ₹53,00,000.00, repo ₹44,45,969.73, equity ₹1,85,248.74, fixed
  ₹50,42,038.36, floating ₹46,12,662.99, net ₹4,24,654.90 before equity (51.67 bp a year), all within ₹0.004 of the
  spec's. A quarter-end spike of 95 bp on 30 Sep 2026 adds exactly its day's interest; the 2 Oct holiday and the
  weekend accrue at 1 Oct's rate.
- `public_from`: as of 17 Nov 10:00 the table ends 16 Nov; a row whose time is unknown cuts it; the expected carry
  without projection is refused and with it counts the projected days. The command line reads the store's CSV.
- The policy path on a known path (+25 bp in December 2026, -25 bp in April 2027, spread 10 bp): quotes within
  5e-7 of the spec's; exactly identified quotes recover the levels to 2.8e-12 bp; the standard 1M-1Y quotes reprice
  to 4.0e-4 bp, recover the path to 5.5e-3 bp and identify three levels; with `lam = 1e-2` the changes smooth to
  +25.18, -5.67, -12.37, -7.54, -0.65 and +0.27 bp, as the spec found. On an OIS curve built from a step path, the
  back-out reprices `ois_par_rate`'s quotes (1M to 2Y, semi-annual beyond a year) to 7e-12 bp.
- The MPC file loads, its decisions are business days, a horizon into FY2027-28 raises unless projected; the 7 Oct
  2026 change matches its decision. Refusals with the reason throughout (bad tenors, quotes of two dates, a converted
  yield, a strict calendar past 2026, a bond past the curve, a missing haircut, a gap in the funding table).
- Determinism: reruns and shuffled inputs give the same results and bytes. Twenty-one deliberate defects in a scratch
  copy of the modules as first built (weekend accrual, the effective date, the bootstrap's start and weights, the
  unwind rule, missing fixings, the proceeds scaling, the stub, both z-spreads' bases, the matched rate, weekend
  funding, the `as_of` cut, the haircut order, the equity base, coupon income, the effective-date rule, the spread,
  the null space, the spread regime, a Jacobian factor, the projection) were each caught by a test; the review then
  found two that were not (the policy-path command's starting date, the anchor in the spline's score), now tested.
- Fixes after review, each with a test that fails on the code before it: a running 4Y swap's floating leg one month
  inside its first period (₹47,20,804.80) and over a year across its 20 May 2027 payment (₹5,70,98,719.77), as
  computed independently by the review; fixings published after `as_of` (5.60% or 9.00%) leaving the carry
  unchanged, with seven business days projected; a row revised after `as_of` cut; CCIL's caps on base 200: illiquid
  40 lent in full and SDLs 60 of 90, then illiquid 100 of 150 and SDLs none, an SDL alone none (before 2 Jun 2026
  noted only at the time; the earlier rule now applies, below); an SDL-only book funded at market repo; CP and CD refused on TREPS; duplicate ISINs and weekend or
  holiday settlements refused; a long and a short of one bond at the same rates netting to zero; a funding spread
  of 15 bp adding its interest exactly; the eve of the 4 Dec 2026 decision under both effective rules (+25.00 bp
  from the rate known on 3 Dec) and on the command line; the shipped calendar refused for 1Y quotes and accepted
  projected; one-month quotes on the strict calendar; an unscheduled meeting called on 10 Dec left out on 19 Nov and
  used on 11 Dec; FY2026-27's schedule refused on 20 Mar 2026, before its release; the spread on 7, 8 and 9 Oct 2026
  from the previous regime; `repo-carry` into 2027 with `--project-calendar`, two rates for one fixing date refused,
  a fixings file without `date` named; missing files exiting 2 on every command.
- Fixes after verification, each with a test that fails on the code before it: a filtered calendar,
  `include_unconfirmed=False` and a list without the February meeting refused for 1Y quotes (past 6 Apr, 4 Feb and
  12 Feb 2027) and accepted where the quotes stop before the missing meeting or the trade date is after it; a
  policy table dating the 4 Dec 2026 decision on 7 Dec refused on the decision day (exit 2) and `--policy-rate 5.75`
  accepted; a fixings file whose `public_from` column has blank cells (counted from FBIL's 12:45) and one later time
  (honoured), its floating legs compounded by hand; CCIL's earlier caps on base 200 (illiquid 40 lent in full and
  SDLs none, SDLs 40 of 90 against a liquid 200, an SDL alone none, nothing before 16 Apr 2021); a short's carry
  unchanged by a 15 bp funding spread, its notes saying so. Two mutations the earlier tests let through are now
  caught: the command line ignoring `public_from`, and held fixings shifted by 0.25% (the held days' floating amount
  is compounded by hand: `5.60%` from the last fixing known).

**Free data** (scratch, not in the repository): the RBI store built from saved samples holds Money Market Operations
for 13 to 16 Nov 2026 (TREPS 5.65% and 5.57%, market repo 5.55% and 5.60%, call 5.61% and 5.65%, corridor
5.25-5.75%, all inside it). Realised carry from 13 to 17 Nov on TREPS per ₹100 crore face (prices at each line's
auction cut-off yield, stale for the June G-sec lines; haircuts from the spec's tabulation of CCIL's August list,
for the demonstration only), the five lines as one book under CCIL's caps: 6.36% GS 2031 ₹95,014 (86.7 bp a year),
7.43% GS 2076 ₹2,07,166 (189.0 bp), 7.61% Haryana SDL 2030 ₹2,18,533 (199.4 bp), the three SDLs together (₹269.13
crore of haircut-adjusted value against a G-sec base of ₹193.91 crore) funded 21.6% in TREPS and the rest at market
repo; an SDL alone gets no TREPS funding and is refused without market repo rates. On market repo rates throughout,
the two G-secs carry ₹7,472 (2031) and ₹7,470 (2076) more. Held at 5.57% for the rest of November (as known at 17
Nov 09:00), the same book carries 93.4 to 240.3 bp a year. As of 16 Nov 10:00 only the 13 Nov row was public, and
the carry to 17 Nov was refused without projection. The asset swap of 6.36% GS 2031, paying a synthetic 6.134480%
against the call rate as stand-in for MIBOR, carried ₹38,704 (35.3 bp a year); as known at 16 Nov 10:00, with the 16
Nov call rate (public the next morning) projected, ₹35,394. The call rate less the repo rate was +11 and +15 bp: a
spread estimate of 13 bp from two days. On synthetic quotes built with a 10 bp spread, backing out with that 13 bp
turns +25/0/-25 into +20.1/+6.5/-34.7 bp with quote errors of 0.8 bp, and quotes rounded to FBIL's two decimals move
the first three levels by under 1 bp and the later ones by 1.1 to 3.2 bp. BIS's repo-rate changes match the MPC file
on 7 Oct 2026; its 70 earlier changes predate the file. None of this is a market level: no OIS quote is free.

**Not checked:** any real OIS quote, MIBOR fixing or curve (FBIL's or CCIL's), any secondary-market bond price,
FBIL's own statement of its quote basis, a quarter-end or 31 March in real funding data, special repo or a
security's own repo rate, a real haircut table or CCIL liquidity classes, CCIL's caps on a real book (or how CCIL
allocates them between securities, or treats T-bills), the earlier caps rule in CCIL's own words (RMD/SS/21/21 and
its effective date; only a search summary of CCIL's FAQ was read), the offshore contract's cash flows,
a real unscheduled MPC meeting or a rescheduled one, RBI's MPC page itself, the term premium in OIS beyond six
months, and performance on a history of days.

### Corporate bonds and GoI FRBs: asset swaps, spreads to the G-sec curve and trade checks

For S2's corporate bonds, and GoI FRBs where a measure fits them. The bonds come from their terms tables through
[`corporates.py`](../src/cr_strat/corporates.py) and [`floating.py`](../src/cr_strat/floating.py) (conventions and
their sources in [`conventions.md`](conventions.md)); nothing here builds a cash flow. Everything was built and tested
on synthetic data; no corporate bond price, trade print or rating has been used.

| Piece | Function | Output |
|---|---|---|
| Asset swaps | `asset_swap`, `asset_swap_table(..., terms=, schedule=, options=, amortisation=)` | The four measures of a corporate bond against any discount curve, as for G-secs (`ASW_COLUMNS`, `kind` `corporate`): the buyer's flows on due or payment dates as the bond's conventions say, the matched rate restated to the bond's annual basis, an amortising bond's swap notional following its outstanding face; a `frb_corp` row is refused (its measure is a discount margin) |
| FRB measures | `frb_measures`, `frb_asset_swap_table` (`FRB_ASW_COLUMNS`) | A GoI FRB's discount margin over its index and over the curve (continuous and semi-annual), its spread and index DV01, its current and next coupon with their status, all as known at an `as_of` time; an FRB given to `asset_swap` is refused, pointing here |
| Spreads to a fitted curve | `credit_spreads.spread_table` (`SPREAD_COLUMNS`), `spread_measures`, `g_spread_bp`, `par_spread_bp`, `z_spread_bp`, `par_coupon_pct`, `par_yield_pct`, `curve_yield_pct`, `workout_bond` | A corporate bond's G-spread, matched par spread and z-spread to any `curvefit` curve, to maturity, to worst over its calls, or by SEBI's trigger rule for calls and puts |
| Peers | `build_peer_matrix`, `read_spread_matrix`, `matrix_spread_bp`, `matrix_yield`, `add_peer_spreads` | The price panel's peer median spread by group and tenor (`curves.SpreadBenchmark`), a matrix kept by the user, each bond's spread against its peers with its own issuer left out |
| Trade check | `trades.check_trade_yields` (`CHECK_COLUMNS`), `recomputed_trades`, `isin_daily_checked`; `price_panel.build_price_panel(..., yield_source="recomputed")` | Each reported trade's yield recomputed from its price and the bond's terms, the reading the reported yield matches, a flag beyond a tolerance; ISIN-day rows and the price panel on the recomputed yields with the reported ones beside (`CHECKED_DAILY_COLUMNS`, `RECOMPUTED_COLUMNS`) |

```bash
cr-strat asset-swaps --quotes data/raw/ois_20261119.csv --trade-date 2026-11-19 --overnight 5.60 \
    --project-calendar --bonds data/raw/bonds_20261119.csv --terms data/raw/corporate_terms.csv \
    --options data/raw/corporate_options.csv --frb-out data/out/frb_margins_20261119.csv --store data/rbi_store \
    --as-of 2026-11-19T20:00 --out data/out/asset_swaps_20261119.csv
cr-strat credit-spreads --prices data/raw/corporate_prices_20261113.csv --terms data/raw/corporate_terms.csv \
    --options data/raw/corporate_options.csv --nss-params data/out/nss_params.csv --date 2026-11-13 \
    --workout worst --peer-panel data/out/panel/price_panel.csv --out data/out/credit_spreads_20261113.csv
cr-strat trade-check --trades data/out/trades_nse.csv data/out/trades_bse.csv --terms data/raw/corporate_terms.csv \
    --out data/out/trade_checks.csv --daily-out data/out/trades_daily_checked.csv
cr-strat price-panel --trades data/out/trades_nse.csv --securities data/raw/securities.csv \
    --benchmark data/raw/gsec_par.csv --rating-actions data/out/rating_actions.csv \
    --yield-source recomputed --terms data/raw/corporate_terms.csv --out-dir data/out/panel
```

Inputs. `--terms`, `--options`, `--amortisation` and `--schedule-file` are the corporate tables `corporates` reads
(`corporate_terms.csv` and the rest, [`data/README.md`](../data/README.md)). In `asset-swaps`, a bond of `--bonds`
whose ISIN is in `--terms` is built from it (its `maturity_date` may be blank; a value given in both that differs is
refused), and with `--frb-out` the rows whose ISINs are GoI FRBs of the FRB terms (`--frb-terms`, default the shipped
table) are measured apart, their coupons from the announced coupons (`--frb-coupons`, default the shipped table;
`--no-frb-coupons` for none) and the 182-day T-bill results (`--tbills` CSV or an RBI store's `tbill_result`, each
result's time from its file, else `--tbill-time` on its auction day, a rule), as known at `--as-of` (without it,
everything in the inputs: a look-ahead for a past date, which the summary line says). Without `--frb-out` an FRB stays
in the main table, refused there, and a note says so. `credit-spreads` takes the prices (`isin`, `clean_price` or
`yield_pct`, optional `settlement_date`, default the curve's valuation date), one NSS curve of an `--nss-params` table
(`--curve`, default `gsec`; `--date` when the table holds several days) and, with `--peer-panel` (a `price-panel`
output), each bond's peer median and `relative_spread_bp` (the prices then need `issuer_id` and the peer columns;
`tenor_bucket` is computed from the years to the workout). `trade-check` reads `load-trades` CSVs and appends the check
columns; `--tolerance-bp` (default 1, UNVERIFIED) and `--workout` (`maturity` or `worst`) set the check. Each command
exits 1 when no row could be measured, 2 on bad input or a missing file; the same inputs give the same bytes.

**Asset swaps of corporate bonds.** The measures are the G-sec ones (yield-yield, par-par, proceeds and z-spread,
[Method](#method-1)), on the flows a buyer settling that day receives: a settlement after a record date leaves the
coming coupon out and pays a negative accrued interest, and the flows sit on due dates or on payment dates as the bond's
`flow_dates` rule says. A corporate bond's yield is annual on actual/actual (SEBI's day count), so the matched OIS rate
on the bond's schedule is restated to that basis before the yield-yield spread (`k_match_basis="bond"`, the default for
corporate bonds; `"schedule"` keeps the swap's basis): without it a semi-annual NCD priced on the curve shows 9.13 bp of
yield-yield spread (0.04 bp with it, on the synthetic OIS curve of the tests). An amortising bond's par-par and proceeds
spreads divide by an annuity whose notional is each period's outstanding face. Both are this build's readings of the
textbook definitions (UNVERIFIED: no market convention for Indian corporate asset swaps was found).

**GoI FRBs.** An FRB's coupon follows its index, so it has no fixed-coupon asset swap: its spread is a discount
margin. The index form (`floating.discount_margin_bp`) is the margin over a flat index that prices the bond, every
coupon not yet fixed projected at index plus the bond's spread; the curve form is the parallel shift of the curve's
continuously compounded zero rates that prices its flows (on an OIS curve, the FRB's spread over OIS), with a
semi-annual variant. The spread DV01 is the price change per bp of margin, the index DV01 per bp of the projected
index with the margin held (a floater's rate risk, small). The coupons are those `floating.goi_frb_rates` knows at
`as_of`: announced, computed from the three latest 182-day results public by then, else projected flat.

**Spreads to a fitted G-sec curve** (`credit_spreads`). The curve is any `curvefit` curve, here one row of the NSS table
`curve-fit --nss-params` writes. The G-spread is the bond's yield less the curve's par yield at the workout date, the
par yield (semi-annual) restated to the bond's basis (annual; a simple rate where the bond's last period is priced by
the money-market rule). The matched par spread compares the bond with its risk-free twin: the same dates, amortisation
and day count, redeemed at par, with the coupon that makes it worth its outstanding face clean on the curve, `c* = (F -
PV0) / (PV1 - PV0 - AI1)` (`PV1` and `PV0` the twin's curve values with the coupon set to 1% and to 0, `AI1` its accrued
interest at 1%, `F` the outstanding face); the twin's yield is the par yield and the spread is the bond's yield less it,
holding credit, liquidity and, for a callable bond, the option. A zero-coupon or cumulative bond has no par twin: its
own yield at its curve price stands in. The z-spread is `asset_swaps.z_spread` on the bond's due-date flows (continuous;
`z_spread_sa_bp` semi-annual). `workout`: `maturity`; `worst` (from a price: the call date giving the lowest yield, the
workout then fixed at that date); `sebi` (from a yield, SEBI's trigger rule for valuing bonds with calls and puts,
`CorporateBond.sebi_valuation`). The peer benchmark is `curves.SpreadBenchmark` on a price panel: by instrument class,
rating bucket and tenor bucket, the median over issuers in a five-day window, the bond's own issuer left out, NaN below
three issuers (UNVERIFIED choices, as in `curves`); the panel's spread is the yield less a benchmark curve's yield, the
G-spread's measure when that benchmark is the par curve on the bond's basis.

**Trade check** (`trades.check_trade_yields`). For each trade the settlement is the one reported (else T+1 on the
bond's calendar, noted), and the yield is recomputed from the price in every reading a reporter could have used: the
price as clean or as dirty, the flows on due or payment dates, to maturity or to each call and put date at its price,
and in the last coupon period the money-market rule or compounding. `recomputed_yield_pct` is the standard reading
(clean, the bond's own flow dates, to maturity or with `workout="worst"` to worst); `nearest_reading` and
`yield_diff_bp` say which reading the reported yield is closest to and by how much, and `yield_check` is `ok` within
`tolerance_bp` (1 bp, UNVERIFIED), else `mismatch`; `no_terms`, `no_price`, `no_yield` or `error` (with the reason in
`check_notes`, such as a settlement after the redemption record date). The trades keep their columns and order; only
the check columns are appended. `price_panel` keeps the reported yields by default, its output unchanged; with
`yield_source="recomputed"` its daily rows come from `isin_daily_checked` (the day's value-weighted recomputed yield,
the value-weighted reported one beside it, the day's checks as counts such as `mismatch:1;ok:3`, the commonest workout
date), and `RECOMPUTED_COLUMNS` are appended. The recomputation is upstream, trade by trade, so the day's yield is a
weighted mean of recomputed yields rather than the yield of the day's weighted price (a deviation from the
specification's wording, chosen because a weighted price of trades settling on different days has no single yield).

**What was checked, with the numbers** (`tests/test_credit_spreads.py`, `tests/test_trade_check.py`, the corporate and
FRB tests of `tests/test_asset_swaps.py`, `tests/test_cli_credit.py`). The corporates specification's vectors, computed
there by a scratch implementation independent of the repository's pricer, on the NSS curve (7.6, -2, -1, 2, 1.5, 8)
valued 16 Nov 2026: T17-T18, the 8.95% NCD of 14 Dec 2030 at a clean 102.2591928939, gives a G-spread of 106.859149
bp, a matched par spread of 106.504794 bp (par coupon 7.2065560908%, par yield 7.1961783336%), a z-spread of
100.000000 bp and 103.788758 bp semi-annual, all to 1e-6 bp. The z-spread recovers shifts of -40, 0, 25 and 250 bp to
1e-8 bp, and the par twin is worth par clean to 1e-10. T19 (the same bond and price): the payment-date reading is
+0.503979 bp from the standard one and the dirty-price reading +256.9724 bp, both recognised; a semi-annual yield is
-16.3916 bp from every reading, a mismatch at 1 bp and `ok` at 20 bp. T11's callable bond (calls at par in 2029 and
2030) at a dirty 103 on a coupon date: the reported 6.34690720% is recognised as the yield to the 2029 call, and the
recomputed yield to worst is 6.34690720%. T16, settlement 14 Jun 2030 at 100: 8.5676481619% on the money-market rule,
8.7506427110% compounded, both recognised. A corporate bond priced on the OIS curve has par-par, proceeds and z-spreads
below 1e-9 bp (also settling ex-interest, and amortising, whose annuity matches the face-weighted sum to 1e-14); a
semi-annual NCD on the curve shows under 0.1 bp of yield-yield spread on its basis and over 9 bp without the
restatement. FRB T22 (FRB 2033 at 7.70% current and 7.73% projected, index 6.51%): accrued 1.2405555556, yield
7.7250833578% and discount margin 121.508336 bp over the index at a clean 100. From the command line the same numbers
come back (the T17 row of `credit-spreads`; corporate rows of `asset-swaps` equal to the library's to 1e-9 bp), and on
G-sec-only inputs `asset-swaps` and `price-panel` write byte-identical files to the build before these changes (checked
against the committed code).

## The return engine and costs

For the strategies whose instruments `bonds` prices: S1 (asset swaps) first, then S3, S5 and S7; S2's CP and CD, and its
corporate bonds through [`corporates.py`](../src/cr_strat/corporates.py) (with GoI FRBs through
[`floating.py`](../src/cr_strat/floating.py): [Corporate bonds and GoI FRBs in the
engine](#corporate-bonds-and-goi-frbs-in-the-engine)); S4's auction trades; S6's SDLs.
[`returns.py`](../src/cr_strat/returns.py)
keeps a book of bond and OIS trades, values it every Mumbai business day from a table of marks, funds it overnight in
TREPS or market repo at the RBI store's rates, charges the costs of [`costs.py`](../src/cr_strat/costs.py), and splits
each day's change in NAV into lines that add up to it exactly; `cr-strat returns` runs it from files. Every price,
accrued interest, yield, DV01 and convexity comes from `bonds`, every OIS value from `ois.ois_npv` and
`ois.settlement_amounts`, every haircut and TREPS cap from `funding`, every curve from `curvefit` or `ois`: nothing here
builds a cash flow. The rules are those of the return-engine specification of 19 Nov 2026 (its section numbers in
brackets). Built ahead of any licensed mark, curve, fixing or OIS quote, and tested on synthetic data with known answers
only.

### What it does

| Piece | Function | Output |
|---|---|---|
| Book | `run_book(trades, swaps, flows, market=, settings=)`, `read_trades`, `read_swaps`, `read_flows` | Positions by book: bonds held from settlement, forwards before it, partial closes, shorts (where the access policy allows), redemptions and switches; OIS swaps; cash, overnight repo and reverse repo |
| Marks | `MarketData.marks` (`MARK_COLUMNS`), `marks_from_prices` | Each bond's mark for the standard settlement of the day, used only from its `public_from`: traded, valuation, model (curve plus last residual), stale (last clean price held) or the fund's own fill, flagged with its age |
| Curves | `read_nss_params`, `nss_params_row`, `append_nss_params`, `MarketData.curves`, `ois_curves`, `rolled`, `seen_from`, `roll_reference`, `ShiftedCurve`, `model_yield`, `factor_moves`, `factor_shapes` | The NSS table that `cr-strat curve-fit --nss-params` writes and the engine reads; the constant-curve roll, a curve seen from a later settlement, factor-shifted curves, a bond's model yield on any curve |
| Attribution | `attribute_bond`, `attribute_ois` (and the engine) | Long-format lines per date, book and instrument (`ATTRIBUTION_COLUMNS`), memo lines flagged |
| Funding | `FundingPolicy`, `funding_from_store` | Overnight deals per book (`FUNDING_COLUMNS`): borrowed, rate, interest, charges, limit, haircut equity, TREPS margin, reverse repo lent for shorts |
| Costs | `costs.read_cost_table`, `cost_rule`, `bond_trade_costs`, `ois_trade_costs`, `repo_deal_costs`, `treps_margin` | The dated cost table (defaults in `src/cr_strat/data/cost_defaults.csv`, each row with source and status), fills at mid plus or minus the half spread, CCIL charges, platform fees, GST, stamp duty |
| Access | `AccessPolicy`, `ACCESS_PRESETS` (`nbfc_non_pd`, `unregulated_company`, `bank_or_pd`) | Refusals of repo, shorts, OIS and when-issued trades the fund's legal form does not allow, naming the rule |
| Performance | `performance`, `drawdowns` | TWR, annualised return and volatility, excess over TREPS, Sharpe, drawdowns, leverage, DV01 by tenor bucket, turnover, costs as a share of gross P&L (`PERFORMANCE_COLUMNS`) |
| Back-tests | `run_backtest(strategy, market, settings, start=, end=, capital=)`, `Strategy`, `Order`, `Snapshot` | A strategy deciding each evening on a snapshot of what was public then (marks, curves, funding rates and fixings; through its accessors the event ledger, the auction table, the RBI store's tables, macro series, the MPC calendar, the residual history and the master), its orders filled the next business day at traded prices only, its auction bids at the cut-off within the allotment limits |
| Output | `BookResult`, `write_result`, `cr-strat returns` | `positions.csv`, `nav.csv`, `attribution.csv`, `fills.csv`, `funding.csv`, `checks.csv`, `performance.csv`, `drawdowns.csv`, `notes.txt`, column by column under [Output files](#output-files) |

**Which access preset fits the fund.** That is a question for the fund's counsel, and the engine never assumes an
answer: `--access` is required. The research notes behind the presets (Nov 2026, from RBI's directions and FAQs) read
as follows. A company trading only its own money without registering, under the exemption for Unregistered Type I
NBFCs in force from 1 Jul 2026, may not avail public funds, and RBI's FAQ counts any outside liability as public funds.
Repo and TREPS borrowing are not named there, but the RBI Act calls a repo an instrument for borrowing funds
(section 45U(c)), so on that reading a repo-funded book needs registration as a Type II NBFC (an inference, not a
ruling). Whether an exempt company is an NBFC non-retail user under the Rupee Interest Rate Derivatives Directions 2025
(para 5.2(ii)), and so may trade OIS for any purpose, is untested. For that own-money structure the conservative
preset is `unregulated_company`: no repo or TREPS, no short sales, OIS for hedging only. S1 then holds its bonds on
equity with the OIS as their hedge, and S7's OIS views are refused. `nbfc_non_pd` fits an RBI-registered (Type II)
NBFC, which may borrow in repo and TREPS and trade OIS for any purpose; `bank_or_pd` fits a bank or primary dealer. The
examples below use `nbfc_non_pd` only to show the funding lines.

```bash
cr-strat curve-fit --prices data/raw/prices_20261113.csv --master data/out/sovereign_master.csv \
    --settlement 2026-11-16 --nss-params data/out/nss_params.csv --public-from 2026-11-13T19:00 \
    --out-dir data/out/curve_20261116      # each day: adds (or replaces) that day's row of the NSS table
cr-strat returns --trades data/raw/trades.csv --swaps data/raw/swaps.csv --flows data/raw/flows.csv \
    --marks data/raw/marks.csv --nss-params data/out/nss_params.csv --ois-quotes data/raw/ois_quotes.csv \
    --fixings data/raw/mibor.csv --store data/rbi_store --haircuts data/raw/haircuts.csv \
    --costs data/raw/my_costs.csv --liquidity data/raw/ccil_classes.csv --master data/out/sovereign_master.csv \
    --access nbfc_non_pd --start 2026-11-13 --end 2026-12-14 --project-funding --out-dir data/out/returns
```

Inputs. Trades (`TRADE_COLUMNS`): `trade_id`, `book`, `isin`, `side` (`buy`, `sell`), `face` (rupees), `trade_date`; a
clean `price` or a `yield_pct` for the trade's settlement (both blank: filled at the day's traded mid plus or minus the
half spread); optional `kind`, `coupon_pct`, `maturity_date`, `issue_date`, `first_coupon_date` (else from `--master`; a
value in both that differs is refused), `settlement_date` (default T+1 on the Mumbai calendar; T+2 with an FPI; a
when-issued trade's, the new issue's issue date), `venue` (`ndsom`, `otc`, `rfq`, `auction`, `auction_nc`, `omo`,
`switch`, `when_issued`), `mid_price` (the price the fill is measured against; blank: the fill is its own mid, noted),
`liquidity` (CCIL's class), `broker`, `special_repo_pct` (a short's reverse repo rate), `notified_cr` (when-issued). OIS
trades (`SWAP_TRADE_COLUMNS`): `trade_id`, `book`, `trade_date`, `months`, `notional`, `pay_fixed`, optional
`fixed_rate_pct` (blank: the day's par rate plus or minus the half spread), `mid_rate_pct`, `effective` (default T+1),
`purpose` (`hedge` or `view`). Flows: `date`, `book`, `amount` (start of the day). Marks (`MARK_COLUMNS`): `date`,
`isin`, `clean_price` or `yield_pct` for the standard settlement of the date (or `settlement_date`, restated at its own
yield), `source` (`traded` or `valuation`), optional `vwap`, `last_price`, `volume_cr`, `trade_count`, `public_from`.
NSS curves: one row per mark `date` with `beta0` ... `tau2`, `t_min`, `t_max`, optional `settlement_date`, `curve`
(default `gsec`), `public_from`: `cr-strat curve-fit --method nss --nss-params FILE` adds the day's row (a refit
replaces it; `--trade-date` defaults to the business day before `--settlement`, `--curve-name` to `gsec`, and
`--public-from`, which should be no earlier than the latest `public_from` of the prices fitted, to the marks' default
time below). A spline curve has no parameters and reaches the engine only through the library (`MarketData.curves`).
Each kind is attributed against the curve `AttributionSettings.reference` names (`--reference KIND=CURVE`, repeatable:
`sdl=sdl` puts S6's SDLs on an SDL curve of the table); a curve of the table that no kind uses is named in the notes.
OIS quotes: `date`, `tenor`, `rate_pct`, `overnight_pct`, optional `public_from` (bootstrapped per date with
`ois.bootstrap_ois`). A licensed file plugs in through a loader that writes these columns: `marks_from_prices` turns the
generic price file `curvefit.prepare_prices` reads (CCIL's trades by security, FBIL's valuation sheets) into marks, and
a `curvefit.FitResult`'s curve goes into `MarketData.curves` as it is. All-blank rows (a spreadsheet's trailing `,,,`)
are dropped from every input, the haircuts, liquidity classes, cost table and OIS quotes as well as the trades, and
refusals name the file's line; a reference row (master, liquidity class, auction, stock, residual) given twice with
different values is refused, an exact repeat kept once. A missing file, bad input or an output directory that cannot be
written exits 2, a run with nothing to value 1, else 0; the same inputs give the same bytes, in any row order (a day's
trades: purchases without a `trade_time` first, then the timed trades by time, then sales without a time, each group in
the natural order of their ids, T9 before T10, so a sale whose time is not known is checked against all the day's
purchases; a day's flows summed exactly).

### Method

**The day** (A2.8), each Mumbai business day `d` after `d_prev`: capital flows at the start of the day; the night's repo
repaid with interest; coupons with scheduled dates in `(d_prev, d]` entitled to the holder of the previous close
(receivables until paid) and redemptions on their payment dates; settlements; the day's fills with their costs; OIS
periods ending on `d` settled; funding for the next night; marks, curves and valuation; attribution and the identity
check; a back-test's decision on what was public at its decision time.

**Positions and value** (A1.3). A bond is held from its settlement date; between trade and settlement it is a forward
worth `F/100 (P_dirty(y_d, S) - P_trade - AI(S))`, clean on clean for the usual T+1 trade. A settled bond is worth
`F/100 (clean mark for T+1 + AI(d))`, accrued interest to the mark date (UNVERIFIED book convention; ICE's indices
accrue to T+1). On the last days before redemption, when standard settlement would fall on or after maturity, the mark
is restated to settlement on the day itself. On the redemption's payment date (the previous working day when maturity
is a holiday) the position closes: `accrual = F/100 (C - AI(d_prev))`, `pull_to_par = F/100 (100 - clean_prev)`.
Trades settling in the one-day shut period before redemption (UNVERIFIED rule of `conventions`), on or after the
redemption's payment date (the working day before a maturity on a holiday: the holder is repaid at the start of that
day, so a trade settling then would deliver a bond already redeemed), on a non-business day, before issue or on or after
maturity are refused; a T+0 G-sec trade needs `allow_t0` (the handbook lists no T+0 window for G-secs: partly
verified). A when-issued trade is a forward to the auction's settlement (default: the new issue's issue date), marked
at the day's yield for that date, since the security cannot settle earlier.

**Marks** (A1.8). Per day: a `traded` row (its VWAP, else the last trade, else its price), else a `valuation` row, else
`model` (the day's reference curve plus the bond's last observed residual, from the engine's own days or
`curvefit.append_history`'s panel), else `stale` (the last clean price held), else the fund's own fill (`fill`), which
also values a position traded today when its only mark is older than the day. A row is used only from its `public_from`;
a mark or curve without one counts from 18:00 on its date (UNVERIFIED), and the day's NAV is struck at 20:00
(UNVERIFIED), so a mark published the next morning values nothing that day. Model and stale marks older than five
business days are flagged `stale_warning` (UNVERIFIED). Fills never use model or stale prices, and a valuation price
only with `MarkPolicy(fill_sources=("traded", "valuation"))`.

**Bond lines** (A2.2), `U = F/100`, `y0` and `y3` the yields of the two marks, `s_d` today's standard settlement:
`accrual = U (AI(d) - AI(d_prev) + coupons entitled)` (0 for a trade settling today), `pull_to_par = U (clean(y0, s_d) -
clean_prev)`, and the clean move at `s_d` split along `y3 - y0 = dy_roll + dy_curve + de`: `dy_roll = m(R) - m(C_prev)`
with `R` the previous curve moved to the valuation date of today's curve at unchanged zero rates by time and seen from
`s_d` (`roll_reference`: settlement to settlement for a G-sec curve, trade date to trade date for an OIS curve, as the
OIS lines roll, so a CP on a constant OIS curve books no curve line over a weekend; UNVERIFIED standard roll;
`roll_basis="forwards"` realises the forwards instead), `dy_curve = m(C_d) - m(R)` split into `curve_parallel`,
`curve_slope`, `curve_curvature` (Legendre polynomials `1`, `x`, `(3x^2 - 1)/2` of `x = 2 ln(T/0.5)/ln 80 - 1`, fitted
by least squares to the continuous zero-rate changes at 0.5, 1, 2, 3, 5, 7, 10, 15, 20, 30 and 40 years, each revalued
from the rolled curve: UNVERIFIED basis; a curve with fewer usable nodes than factors, such as money-market OIS quotes
to one year, fits only the leading shapes, parallel and slope on two nodes, parallel on one, none on none, the rest in
`curve_other`, and the notes count those days; `factor_basis="key_rates"` gives `krd_<tenor>` lines) and `curve_other`,
and `de` the residual's change (`rich_cheap`). Each first-order line is `-U DV01(y0, s_d) 100 dy`; `convexity = 0.5 U CX
P ((y3 - y0)/100)^2`; `unexplained` is the rest of the move (third order, plus what a stale mark holds) and is flagged
above `max(1e-3 |move|, Rs 1)` (UNVERIFIED threshold). Without a curve on either day the first-order move is
`price_unattributed`. Reference curves: G-secs, bills, STRIPS and SDLs against `gsec`, CP and CD against the OIS curve
seen from their settlement (`AttributionSettings.reference`; `--reference sdl=sdl` puts SDLs on an SDL curve).

**Trades** (A2.5): `trading = U side (close - mid)`, `spread_cost = U side (mid - fill)` (restated at their own yields
to the mark's settlement; the restatement of a T+0 or T+2 fill goes to `pull_to_par`); `fees` (CCIL, platform, custody),
`brokerage`, `gst` and `stamp_duty` in cash on the trade date. An OIS fill books `trading = NPV(mid rate)` and
`spread_cost = NPV(fill) - NPV(mid)`.

**OIS lines** (A2.7) by revaluation with `ois_npv`: `ois_carry` (the change in accrued floating less fixed, plus the
unrounded accrual of a period paid), `ois_roll_down` (the rest of the move on the rolled previous curve), one line per
curve factor on shifted rolled curves, `ois_curve_other`, and `ois_settlement_rounding` (the payment as
`ois.settlement_amounts` rounds it less its unrounded accrual: the rate to 0.0001%, amounts to the paisa, the net to the
rupee); on a short curve only the leading factors, as for bonds. A day without an OIS curve holds the last one rolled
forward (flagged `stale`); a swap traded on a day without one is refused.

**Funding** (A1.5). Each business day a book borrows for the night what its cash needs after the day's settlements,
coupons, costs and flows, rounded up to TREPS's lot of Rs 5 lakh (the lot from CCIL; the sizing UNVERIFIED;
`sizing="fixed"` keeps `(1 - h)` of each purchase's consideration borrowed, as `funding.carry` does). Interest is simple
Act/365, paid with the second leg, every night of a deal at its deal day's rate (`rate_basis="deal_day"`, the default:
the engine deals on Mumbai business days only, so a Saturday money markets dealt on does not reprice the Friday deal),
or each calendar day at the rate of the latest dealing day on or before it, as `funding.carry` computes it
(`"calendar_day"`, `--rate-basis calendar_day`; the choice is UNVERIFIED); a night past the end of a funding table, the
risk-free rate's included, is refused unless `projection="last"` holds the last rate (counted in the notes). The limit
is the haircut-adjusted value of the long bonds at the previous close (`funding.haircut`, the user's table) less TREPS's
initial margin (0.50% of the second leg from 5 Jul 2025, CCIL RMD/SS/25/22, taken as equity tied up: UNVERIFIED form);
CCIL's caps (`funding.treps_shares`) send what TREPS will not lend against SDLs and illiquid G-secs to market repo,
TREPS taken first (UNVERIFIED). A G-sec without a CCIL class counts as illiquid, so a book of such bonds alone, with no
liquid base for the caps, gets no TREPS money and is funded at market repo (refused if the store has no market repo
rates). A shortfall is refused (`funding_shortfall`), or funded at an overdraft rate the user gives. TREPS charges per
deal and market-repo charges for both legs, on the face of the collateral behind each segment, are booked on the deal
date (`repo_charges`, with GST; the night's charges on one row of `funding.csv`). A short borrows its bond in a reverse
repo at the special rate the user gives, lending `(1 - h)` of the bond's value (interest in `cash_interest`).
`memo_haircut_cost`, `memo_margin_cost` and `memo_cash_benchmark` (the NAV at TREPS rates) are memo lines;
`basis="excess_of_cash"` turns the last into a summed `cash_benchmark` line, and the identity then holds for excess P&L.
`positions.funded` is each long's share of the night's borrowing, pro rata of its lendable value `(1 - h) x value` at
the deal, so the column sums to the amount borrowed; the funding lines themselves stay at book level.

**Access** (A1.9). The preset decides repo and TREPS, shorts, the OIS purpose and the when-issued limits (25% long and
10% short of the notified amount; 25% short for banks and PDs). A when-issued sale is not a short sale: it is held to
those limits, and a when-issued short still open at the close of its auction day is refused unless the preset may carry
it (`when_issued_short_overnight`: banks and PDs, which the When Issued Directions 2018 let carry it as the verified
notes record them). A short carried past that close settles on the issue date as a short sale, so at the close it must
meet the Short Sale Directions as an ordinary short does: the preset's right to short, central government dated
securities only, the limit (the larger of 2% of the stock on the auction day, where the stock table gives one, and Rs
500 crore for a liquid security; 1% and Rs 250 crore for others) and a special repo rate given with the sale; a breach
is refused then, naming the rule. Under `hedge_only` the book's OIS must offset its bonds' DV01 (opposite sign, at most
105% of it, `HEDGE_TOLERANCE`, UNVERIFIED; swaps offsetting each other within 1% of their gross DV01 also pass): a swap
traded on a day it fails is refused, unless it reduces the swaps' DV01 (an unwind), and every later business day it
fails, after a bond sale say, writes a `hedge_only` breach row in `checks.csv` and a note.

**Identity I1.** For every book and day the non-memo lines sum to `NAV_d - NAV_prev - flow_d` within
`1e-12 x (gross value + 1)` rupees; each day's check is a row of `checks.csv`. `NAV = cash + bonds + forwards + OIS +
receivables - repo borrowed + reverse repo lent`. Each book keeps its own cash and funding; with several books a `fund`
row sums them.

**Costs** ([`costs.py`](../src/cr_strat/costs.py), A3). For each component, among the rows in force on the trade date
that match the trade (`kind`, `venue` and `liquidity` may each list several values with `;`), a row naming the ISIN
decides first and one naming the venue next, so the zero CCIL charge and spread of auctions, OMOs and switches survive
a newer schedule for all trades (UNVERIFIED ranking; a user overrides an exception with a row naming the same venue or
ISIN); then the latest `effective_from`, then the most specific (kind, maturity bucket, liquidity class), then a user
row over a default; equal rows that differ raise, as does no row at all. Fills: `mid + side x h x DV01(mid)` for a
bond, `mid + side x h / 100` for an OIS. Charges: `min(max(rate x base, minimum), maximum)` per trade and counterparty.
GST on charges, fees and brokerage; stamp duty on its payer: the seller of CP (FIMMDA's CP guidelines), the fund on
every CD trade, buy or sell (UNVERIFIED payer, the conservative reading).

**Performance** (A4). `r_d = (NAV_d - NAV_prev - F_d) / (NAV_prev + F_d)`, chained into a time-weighted return and an
index; excess over TREPS for each step's calendar days; `A = business days observed / (calendar days / 365.25)`
(UNVERIFIED annualisation); Sharpe `mean / sd x sqrt(A)` (ddof 1); drawdowns on the index; leverage (gross bonds, repo
and OIS notional over NAV); DV01 by `issuance.TENOR_BUCKETS`; turnover of bond trades in face and DV01, annualised;
costs over the P&L before them, and in bp of average NAV a year. A drawdown from the starting capital is dated from the
first day with a return (`drawdown_peak` that day, before its return; noted).

**Back-tests** (A5). The `Snapshot` a strategy sees at its decision time (20:00, UNVERIFIED) holds only rows public by
then: marks and curves by `public_from`, funding rates cut at the first row not yet public, fixings by their times
(FBIL's 12:45 when none). Its `nav` and `positions` are the day's valuation at `valuation_time`; a decision time before
the valuation time is refused (`EngineSettings`), since that valuation could hold a mark published after the decision.
Its accessors cut the other inputs at the same time: `events()` (the event ledger through `event_ledger.events_as_of`: a
result's fields, flags and notes blank until it was public, a new issue's coupon and ISIN too), `auctions()` (the market
data's auction lines announced by then, the cut-off, weighted average price and accepted amount blank until the result,
and the ISIN of a security not issued by the decision day too, as `events()` hides a new issue's; the line's `line`,
such as `new GS 2036`, names it meanwhile), `store_table(name)` (an RBI store table: rows public and not revised by
then), `macro(series_id)` (`macro_store.as_of`), `mpc_calendar()` (the MPC schedule as known at the end of the day
before), `residual_history()` (fits whose marks were public by then; a fit without a `public_from` at the marks' default
time on its trade date, the business day before its settlement date: an estimate) and `master()` (the securities issued
by the decision day, or with an auction result public by then, from the master's issue date or a line of the auction
table or the event ledger; a row without an issue date needs such a line or a mark of it public by then, while a mark
before an issue date still to come is a when-issued price and shows no terms. Only their fixed terms,
`MASTER_FIXED_COLUMNS`, are kept, with `matured` as of the day; the master's other columns, today's stock, RBI's lists,
FAR, the CCIL class, sources, come blank). A table the market data lacks raises, naming the field. Orders fill the next
business day at that day's VWAP (or close) plus or minus the half spread, only at traded prices by default, waiting up
to five business days (UNVERIFIED) and then cancelled and logged. An auction order (`venue` `auction` or `auction_nc`,
with its `auction_date`) must be a purchase decided before the auction day and after its announcement was public (the
auction table's `announcement_public_from`; a line without one is refused), and name the line as the snapshot showed it:
by its ISIN, or, while that is blank (a new issue before its result), by its `line` (an order naming such an ISIN is
refused, since the ISIN was not public); it fills on the auction day at the cut-off price, competitive bids up to
`auction_max_share` (10%, UNVERIFIED) of the amount accepted, or at the weighted average price, non-competitive bids up
to Rs 2 crore a bid in a GoI dated auction and 1% of the notified amount in an SDL's (RBI's notifications); T-bill
non-competitive bids are not modelled, and `fills.csv` says why an order was refused or cut. `auctions_from_events`
builds the auction table from the event ledger. A run may end on 31 Dec 2026, the holiday list's last day: the next
business day and T+1 then fall in 2027 and are taken as weekdays, the day flagged `calendar_projected` and noted.

### Corporate bonds and GoI FRBs in the engine

A trade whose ISIN is in `MarketData.corporate_terms` (`--corporate-terms`) is a corporate bond priced by
`corporates.CorporateBond`; one in the GoI FRB terms (`frb_terms`, by default the shipped table of the five outstanding
FRBs) with kind `frb` or none is priced by `floating.GoiFloatingRateBond`. Everything else is as for G-secs, with these
differences.

- **Settlement.** A corporate trade settles on the exchange's clearing corporation: T+1 unless its `settlement_date`
  says otherwise, T+0 allowed (FIMMDA handbook 3.5.1); an FRB settles as a G-sec. A settlement after a corporate
  bond's redemption record date is refused (trading is suspended until the redemption).
- **The bond as known.** Each day at the valuation time, and at each trade's settlement check, the bond is rebuilt as
  known then: a `frb_corp` bond's benchmark fixings, a rating action under its step rule and a call's or put's exercise
  only from their `public_from`; a GoI FRB's coupons as `floating.goi_frb_rates` knows them (announced, computed from
  the three latest 182-day T-bill results public by then, or projected flat and noted). A period whose coupon cannot be
  projected (fewer than three results) is refused unless `frb_allow_fewer` (`--frb-allow-fewer`). When the bond as
  known changes, the previous close's mark is restated on it (its yield from the same clean price), so the day's moves
  compare like with like.
- **The `coupon_reset` line.** When a coupon becomes known after its period began (a fixing or rating action published
  late), the interest accrued to the previous close is restated at the new rate:
  `coupon_reset = F/100 (AI_new(prev) - AI_old(prev))`. It is part of the NAV change and of the identity, and zero
  whenever every coupon was known before it began to accrue.
- **Record dates and ex-interest.** A corporate coupon goes to the holder at the close of its record date
  (`CorporateBond.record_date`: 15 days before the due date under the SEBI conventions, a holiday moved to the
  preceding day; at the latest the day before the due date; a record date on a holiday takes the previous business
  day's close) and is a receivable until its payment date. A settlement after the record date is ex-interest: its
  accrued interest is negative to the due date, in the position and in the fills.
- **Partial and final redemptions.** A partial redemption (amortisation) is entitled like a coupon: the principal is
  booked at face as a receivable and in `pull_to_par` against the clean price's fall. On its record date the mark's
  standard settlement is already ex the principal while the holder at the close is still owed it, so that day's mark is
  restated to settle on the day itself; a stale mark held across that record date holds its yield, not its price
  (which still holds the principal). Positions keep the original face (`corporates` prices per 100 original face; the
  notes give the outstanding face). The redemption closes the position at its record date's close into a receivable
  paid on its payment date; a mark whose standard settlement falls after the redemption record date is restated to
  settle on the day itself.
- **Calls and puts.** An exercised call or put redeems at its date and price once the exercise is public
  (`exercised_public_from` in `corporate_options`); before then, and for an option never exercised, the bond is valued
  to maturity and the mark carries the option.
- **Costs** (`costs`, cost table rows with their sources). Corporate bonds: the debenture stamp duty of 0.0001% of the
  consideration on the buyer (SD06), the exchange's fee of Rs 5 a crore of value, Rs 25 to Rs 500, GST extra (PF04), no
  CCIL charge (CS07), and the half spread by rating bucket (HS20-HS24: 4 bp of yield for AAA, 7.5 for AA+ and AA, 10
  for AA-, 20 below and without a bucket), the bucket from the trade's `rating_bucket`, else the terms', all UNVERIFIED
  guesses. GoI FRBs: the G-sec rows (CCIL's charge CS06, no stamp duty SD07, a half spread of 4.5 bp HS25,
  UNVERIFIED). The cost table's `rating_bucket` column ranks between the kind and the liquidity class in a rule's
  specificity; a user table without the column reads as before.
- **Funding.** Neither is TREPS collateral here: corporate bonds are not on CCIL's list, and GoI FRBs, which are GoI
  dated securities, are taken as ineligible too (UNVERIFIED and conservative: `treps_class` returns `ineligible`).
  With a row for its kind in the user's haircut table a bond is lent against in market repo (`excess_segment`); without
  one it is not lent against at all (equity-funded), and a shortfall says which bonds lend nothing and why.
- **Attribution.** A corporate bond is attributed against the G-sec curve (`DEFAULT_REFERENCE` maps `corporate` to
  `gsec`; `rich_cheap` is then its spread's change). An FRB has no default reference curve, so its price move is
  `price_unattributed`: its DV01 to its yield treats the projected coupons as fixed and would overstate its rate risk
  if split into curve factors (`--reference frb=gsec` maps one all the same).

Inputs (`cr-strat returns`): `--corporate-terms`, `--corporate-schedule`, `--corporate-options`,
`--corporate-amortisation` (the `corporates` tables), `--corporate-steps` with `--ratings` (agency, rating,
`public_from`, and `isin` or `issuer_id`), `--corporate-floating` with `--benchmark-fixings` (each fixing with its
`public_from`); for GoI FRBs `--frb-terms`, `--frb-coupons` or `--no-frb-coupons`, and the 182-day results from
`--tbills` (a CSV) or `--tbills-from-store` (the `--store`'s `tbill_result`), with `--tbill-time` for results without
a time. The access presets are unchanged.

**What was checked, with the numbers** (`tests/test_returns_credit.py`, the returns test of `tests/test_cli_credit.py`;
synthetic data, every run's identity checked).

- *A corporate bond through its record date.* A 9% annual NCD maturing 15 Dec 2030: its 15 Dec 2026 coupon's record
  date is 30 Nov. Rs 10 crore held from 20 Nov, bought settling on the record date itself (27 Nov), and bought settling
  20 Nov and sold settling 3 Dec all receive the Rs 90 lakh coupon as a receivable from 1 Dec, paid 15 Dec; a purchase
  settling 2 Dec does not, and pays the clean price plus a negative accrued interest of -9 x 13/365 per 100. Each
  book's accrual is the days held at 9% over 365 to 1e-12 relative.
- *An FRB through a reset.* FRB 2031 with synthetic weekly results: the 7 Jun-7 Dec coupon is 6.66% (20 May, 27 May
  and 3 Jun results, mean 5.66%, plus 1.00%), the coupon from 7 Dec 7.18% (18 Nov, 25 Nov and 2 Dec), the 7 Dec cash
  Rs 33.3 lakh on Rs 10 crore, the accrued interest 30E/360 to 1e-12 relative, and no `coupon_reset` (every coupon
  known before it accrued). With the 2 Dec result public only on 9 Dec, the new coupon is projected at 7.16% (11, 18
  and 25 Nov) until then, `coupon_reset` restates the one day accrued on 8 Dec by Rs 55.56 (0.02% for a day on 30E/360
  on Rs 10 crore) and is zero on every other day, and the notes name the projection. Published at 15:30 on 7 Dec, the
  2 Dec result is not used by that day's 15:00 valuation (7.16% projected) but is by the next day's (7.18%), and by a
  20:00 valuation on 7 Dec.
- *A partial redemption.* 50 of 100 redeemed on 15 Dec 2026: Rs 5.9 crore (coupon and principal) receivable from 1 Dec
  and paid 15 Dec, the position valued per 100 original face with the notes giving 50 outstanding. Testing this found a
  fault, now fixed: on the record date the T+1 mark settled ex the principal while the holder was owed it, so the NAV
  fell by the principal for a day (Rs 15.04 crore against Rs 20.02 crore); the mark is now restated to settle that day,
  and the next day's `pull_to_par` is the principal less the clean price's fall at the same yield, Rs 1.71 lakh, the
  15-day discount of the principal. A second fault of the same kind, also fixed: with no marks from 27 Nov to 2 Dec,
  the price held from 26 Nov still held the principal after it was booked as a receivable, so the NAV read Rs 25.02
  crore on 1 and 2 Dec instead of Rs 20.04 crore; the held mark now keeps its yield across the record date.
- *An exercised call.* A call at par on 15 Jan 2027 exercised with notice public at 17:00 on 1 Dec 2026: the DV01
  falls more than 40-fold on 1 Dec (valued to the call, noted), the position closes after the call's record date
  (31 Dec) into a receivable of Rs 10.8 crore (coupon and principal); with the notice a day later, 1 Dec still values
  to maturity.
- *A rating step.* A 25 bp step per notch below AA, effective from the event: the issuer's downgrade to AA- public at
  18:00 on 2 Dec 2026 accrues at 8.75% from 3 Dec (8.50% before), and the run is refused without the rating actions.
- *A corporate FRB.* A quarterly `frb_corp` bond at T-bill plus 2%: the 15 Sep-15 Dec coupon at 7.50% (91 days on
  actual/365), and the 15 Dec fixing (5.80%) published at 21:00 on 16 Dec, after that day's valuation: 16 Dec accrues
  at the flat projection of 7.50%, and 17 Dec restates that day at 7.80% (Rs 821.92 on Rs 10 crore) before accruing at
  it.
- *Funding, costs and refusals.* A corporate book on TREPS without a haircut row is refused, naming why; with a 10%
  row it borrows in `market_repo` only. A corporate purchase pays SD06's stamp duty (0.0001% of the consideration, the
  buyer), PF04's fee (Rs 5 a crore of value) and its GST at 18%, takes HS20 by its terms' AAA bucket or HS22 by the
  trade's AA-, and may settle T+0; refusals name a settlement after the redemption record date, an FRB with no terms
  ("no pricer in bonds.py"), a coupon or kind that differs from the terms, and an FRB coupon nothing fixes or projects.
- *G-secs unchanged.* A G-sec book run with corporate terms and T-bill results present gives byte-identical outputs
  to the same run without them, and `cr-strat returns` on the earlier G-sec CLI inputs writes the same bytes in all
  nine files as the committed code before these changes.

### Output files

`cr-strat returns` writes one CSV per table of `BookResult` (`write_result`) and `notes.txt`: dates `YYYY-MM-DD`,
amounts in rupees, rates and haircuts in percent, DV01 in rupees per basis point (positive for a long bond).

| File | One row per | Columns, where the name does not say it all |
|---|---|---|
| `positions.csv` (`POSITION_COLUMNS`) | position, book and day | `state` `settled` (held), `pending` (a forward between trade and settlement) or `ois`; `face` in rupees, negative short (for a swap the notional, negative when paying fixed); `clean_mark` the clean price used for standard settlement (a swap's fixed rate), `mark_source` (`traded`, `valuation`, `model`, `stale`, `fill`) and `mark_age_days` (business days since the price was seen); `yield_pct` (a swap's par rate of the day); `bucket` the residual maturity's `issuance.TENOR_BUCKETS` bucket; `liquidity` the CCIL class used and `haircut_pct` its haircut; `funded` the long's share of the night's borrowing; `extrapolated` the reference curve read beyond its data at the maturity; `calendar_projected` |
| `nav.csv` (`NAV_COLUMNS`) | book and day (a `fund` row sums the books) | `bonds`, `forwards`, `ois`, `cash`, `repo_borrowed` (the night's borrowing), `lent` (reverse repo lent for shorts), `receivables` (coupons and redemptions entitled, not yet paid); `flow` (capital at the start of the day), `pnl` (`nav - nav_prev - flow`), `return` (`pnl / (nav_prev + flow)`), `index` (chained); `rf_return` (TREPS over the step's calendar days, on the funding rate basis); `gross_long`, `gross_short`, `ois_notional`; `leverage` (gross bonds over NAV), `repo_leverage` (borrowed over NAV); `dv01` and `dv01_<bucket>` |
| `attribution.csv` (`ATTRIBUTION_COLUMNS`) | line, instrument, book and day | `line` (the lines of Method above), `amount`, `memo` (true for memo lines, left out of the identity); `instrument` blank for book-level lines (funding, charges, the benchmark) |
| `fills.csv` (`FILL_COLUMNS`) | trade or order | `order_id` (back-tests), `mid` and `fill` (prices; an OIS's rates), `settlement_date`, the costs in rupees (`spread_cost`, `fees`, `brokerage`, `gst`, `stamp_duty`), `rules` (the cost rules used, `rule_id:status`), `status` (`filled`, `refused`, `cancelled`, `open at the end`) and `notes` (why an order was refused or cut) |
| `funding.csv` (`FUNDING_COLUMNS`) | book, night and segment | `segment` (the funding source, `triparty` for TREPS or `market_repo`; `overdraft`; `reverse_repo` for a night with only shorts); `borrowed`, `rate_pct` (the deal day's rate plus spread), `repay_date`; `interest` at that rate for the deal's nights (under `rate_basis="calendar_day"` the interest booked, the funding lines of `attribution.csv`, can differ); `charges` (the night's CCIL and TREPS charges with GST, on one row); `limit` (what the night could borrow: haircut-adjusted collateral at the previous close less the TREPS margin); `haircut_equity` (the book's own money the haircuts tie up); `margin` (the TREPS initial margin); `lent_short`; `idle_cash` (cash left after the deal); `projected_days` (1 when the rate was held past its table) |
| `checks.csv` (`CHECK_COLUMNS`) | check | `check`: `identity` (every book and day: `value` the non-memo lines less the NAV change net of flows, `tolerance` `1e-12 x (gross + 1)`, `status` `ok` or `breach`); `attribution_check` (a position-day whose `unexplained` exceeds `max(1e-3 \|move\|, Rs 1)`, `flag`); `stale_warning` (a model or stale mark older than five business days, `value` its age, `flag`); `short_cover` (a short open beyond three months, Short Sale Directions 2018 para 5(b), `value` its days, `flag`); `hedge_only` (a day the book's OIS did not hedge its bonds under a hedging-only policy, `value` the swaps' DV01, `tolerance` 105% of the bonds', `breach`) |
| `performance.csv` (`PERFORMANCE_COLUMNS`) | book (and `fund`) | `business_days` (returns observed), `calendar_days`, `obs_per_year` (the annualisation factor `A`), `twr`, `annual_return`, `mean_daily`, `vol_daily`, `annual_vol`, `excess_twr` (the chained excess over `rf_return`), `sharpe`; `max_drawdown` with `drawdown_peak`, `drawdown_trough`, `drawdown_recovery` (dates), `drawdown_days` (business days from peak to trough) and `recovery_days` (from trough to recovery); `avg_nav`, `avg_leverage`, `max_leverage`, `avg_repo_leverage`, `avg_ois_leverage`, `avg_dv01` and by bucket; `traded_face` and `turnover` (over average NAV, annualised), `traded_dv01` and `dv01_turnover`; `pnl`, `costs` (the cost lines, positive), `costs_share` (of the P&L before costs), `costs_bp_nav` (bp of average NAV a year) |
| `drawdowns.csv` (`DRAWDOWN_COLUMNS`) | book and day | `index`, `peak` (its running maximum), `drawdown` (`index / peak - 1`) |
| `notes.txt` | note | projections (the calendar nights of funding and of the risk-free rate held at a table's last rate, each from its first night; calendar days), stale marks, curves of `--nss-params` no kind uses, days a short curve fitted fewer factors, `hedge_only` breaches, the UNVERIFIED cost rules used |

### What each strategy reads

As the specification's A0 sets it out; each book's lines sum to its NAV change, so a strategy's P&L splits along them.

| Strategy | Book | Lines that carry its thesis | Before real use |
|---|---|---|---|
| S1 asset swaps | long G-sec or SDL, paying fixed in MIBOR-OIS on a DV01-matched notional (`asset_swaps.hedge_notional`), funded in TREPS or market repo | the carry: `accrual` + `pull_to_par` + `funding_repo` (+ `funding_excess`) + `ois_carry`, as `funding.carry` computes it; the spread's move: `rich_cheap` plus the `curve_*` lines less the `ois_curve_*` lines, with `roll_down` against `ois_roll_down` | marks, MIBOR fixings and OIS quotes (licensed); the access preset's repo and OIS rights |
| S2 front end (CP and CD only) | CP or CD against the OIS curve | `accrual` + `pull_to_par` less funding (market repo: CP and CD are not TREPS collateral); `rich_cheap` against OIS | CP and CD marks and OIS curves (licensed); corporate bonds need a pricer first |
| S3 rich/cheap | long cheap against short rich bonds (DV01-neutral pairs, `curvefit.bond_pairs`), or long cheap against OIS | `rich_cheap`; the `curve_*` lines should net to about zero | traded marks and daily fitted curves; shorts (`bank_or_pd`) or an OIS leg |
| S4 auction cycle | auction purchases (`venue` `auction`, at the cut-off) sold into the market, when-issued sales | `trading` and `spread_cost` on the days around the auction, `rich_cheap` | the event ledger's auction table (`auctions_from_events`); marks |
| S5 long end | 10 years against 30 to 50 years, DV01-neutral | `curve_slope` and `curve_curvature`, `roll_down`, the long leg's `convexity` | as S3; curves to 40 or 50 years (`extrapolated` flags a maturity past them) |
| S6 SDLs | SDLs against G-secs or each other | `rich_cheap` against an SDL curve (`--reference sdl=sdl`) or the G-sec curve | SDL marks; an SDL curve fitted with `curve-fit --kinds sdl --curve-name sdl` |
| S7 policy path | OIS receivers or payers on MPC views | `ois_carry`, `ois_roll_down`, `ois_curve_*`; the MPC dates from the event ledger for event windows | OIS quotes and fixings (licensed); the preset's OIS purpose |

### Conventions and their sources

V verified (a primary source or a saved copy of one, read by the specification's job or an earlier one), P partly
verified, U UNVERIFIED (a named parameter; listed for the user to confirm).

| Rule | Engine | Source | Status |
|---|---|---|---|
| G-sec, SDL, T-bill T+1; T+2 with an FPI; CP and CD T+0 or T+1; TREPS T+0; OIS effective T+1 | `calendars.settlement_date`, `settlement_date` column | FIMMDA Handbook 2026 4.7.1, 3.5.1, 6.10.2(e); CP and CD guidelines | V |
| No T+0 G-sec outrights unless allowed | `allow_t0` | Handbook 3.5.1 (no T+0 window listed) | P |
| Held from settlement; a forward before it, discounting to the mark date ignored | `returns` | Follows from T+1 | V (T+1), U (valuation) |
| Book value: clean mark for T+1 plus accrued interest to the mark date | `returns` | Book practice | U |
| Coupons on a holiday paid the next working day, redemptions the previous; amounts unchanged | `bonds.cash_flows` | Handbook 4.10.1 | V |
| No ex-coupon period; one-day shut period before redemption | `bonds.in_shut_period` | `conventions.md` | P, U |
| Overnight interest simple Act/365, paid with the second leg | `_RateTable` (as `funding.carry`) | CCIL TREPS FAQ; RBI Repo Directions 2025 | V |
| Every night of a deal at its deal day's rate (default), or each calendar day at the latest dealing day's rate | `FundingPolicy.rate_basis` | None (the engine deals on business days only; money markets deal on some Saturdays) | U |
| Nights past a funding table refused, or held at its last rate when asked (the risk-free rate too) | `FundingPolicy.projection` | None | Choice |
| Cash-need sizing in Rs 5 lakh lots | `FundingPolicy.sizing`, `lot_rs` | CCIL (lot, verified notes C17) | V (lot), U (sizing) |
| Haircuts from the user's dated table; CCIL's TREPS caps; capped part at market repo | `funding.haircut`, `funding.treps_shares` | CCIL lists and RMD/SS/26/13 (earlier jobs) | V |
| TREPS margin 0.50% of the second leg from 5 Jul 2025, as equity | `costs.treps_margin` | CCIL RMD/SS/25/22 (verified notes C26) | V (rate), U (who, form) |
| TREPS before market repo when both lend; a G-sec without a CCIL class counts as illiquid | `_fund`, `funding.treps_class` | None (conservative) | U |
| Coupons on repo collateral accrue to the fund | `returns` | Repo Directions Annex II not read | U |
| OIS by `ois_npv`; settlements rounded per the handbook | `ois` | Handbook 6.10.2, 6.10.5 | V |
| Constant-curve roll-down, settlement to settlement for G-sec curves and trade date to trade date for OIS curves | `rolled`, `roll_reference` | None (standard definition) | U |
| Legendre factors in log tenor at 11 zero-rate nodes; only the leading shapes where fewer nodes are usable | `factor_moves`, `DEFAULT_NODES` | None | U |
| First-order lines at the carried state's DV01, one convexity line, explicit unexplained | `attribute_bond` | Standard duration-convexity attribution | Choice |
| Missing marks: curve plus last residual, then last clean price held, then the fill; warning after 5 business days | `MarkPolicy` | `rates_data_sources.md` Pitfalls | U |
| Fills at traded prices only, next business day's VWAP, 5 days' wait | `MarkPolicy.fill_sources`, `execution`, `max_wait_days` | None | U |
| Decision and valuation at 20:00, a decision before the valuation refused; marks and curves without a time at 18:00 | `EngineSettings`, `MarkPolicy.default_time` | Evening releases of Nov 2026 in the store | U |
| Access presets | `ACCESS_PRESETS` | RBI Repo Directions 2025, Short Sale and When Issued Directions 2018, Rupee IRD Directions 2025 (verified notes) | V |
| When-issued: 25% long and 10% short of the notified amount, a short closed by the auction day's close; banks and PDs 25% short and may carry it | `AccessPolicy.when_issued_*` | When Issued Directions 2018 (verified notes) | V |
| An OIS under `hedge_only` must offset the book's bond DV01, up to 105% of it, every business day; swaps within 1% of their gross DV01 offset each other | `HEDGE_TOLERANCE`, `_swap_values` | None | U |
| Auction orders: bought at the cut-off, competitive fills up to 10% of the amount accepted | `auction_max_share` | None (the fund's bid taken to be at the cut-off) | U |
| Non-competitive bids at the weighted average price, at most Rs 2 crore a bid (GoI dated) or 1% of the notified amount (SDL) | `auction_nc_limit_rs`, `_auction_allotment` | RBI PR 2026-2027/1474 annex, /1498 (saved) | V |
| An auction order needs the announcement public before the decision | `_auction_order_refusal` | Follows from the decision time | Choice |
| A run ending on 31 Dec 2026: the next business day and T+1 taken as weekdays, flagged | `calendar_projected` | None (no 2027 holiday list) | U |
| Bid-offer for G-secs by CCIL class: half spreads 0.625, 2.25, 4.5 bp; no class 4.5 | `cost_defaults.csv` HS01-HS04 | CCIL's monthly NDS-OM bid-ask, Aug-Oct 2026, through a search summary | P |
| Bid-offer SDL 5, T-bill and CMB 1, STRIPS 10, CP and CD 5, OIS 0.5 to 5 years and 1.5 beyond (bp) | HS05-HS10 | None | U |
| Auctions, OMOs and switches: no spread, no CCIL charge | HS11, CS05, CS15 | None | U |
| CCIL charges from 1 Oct 2019 (dated securities Rs 95 per crore, T-bills Rs 45, STRIPS Rs 45 on value, repo Rs 10 a leg, TREPS Rs 10 on repayment) | CS01-CS03, RL01, TR01 | CCIL's schedule read by an earlier probe | V (repo leg: a search summary gives Rs 12.50) |
| The 2019 charges held back to 2015 | CS11-CS13, RL11, TR11 | None | U |
| No NDS-OM dealing fee; custody 0 | PF01, CU01 | None | U (placeholders) |
| Exchange fee on CP and CD Rs 2.5 per crore of value | PF02 | NSE's page through a search summary | P |
| GST 18% from 1 Jul 2017; service tax before it | GS01-GS05 | Secondary sources; earlier rates from memory | P, U |
| Stamp duty: none on G-secs and their repo; 0.0001% of consideration on CP (the seller) and CD (charged to the fund on every trade); 0.0001% of an OIS's notional | SD01-SD05 | PIB 30 Jun 2020 (regime, read); rates through secondary sources; FIMMDA CP guidelines (CP payer, saved); NSE's investor page through a search summary (the buyer pays on exchange trades) | V, P, U (CD payer, OIS base and payer) |
| A cost row naming the ISIN, then one naming the venue, before any newer row naming neither | `cost_rule` | None | U |
| Income tax and TDS left out | none | Secondary sources | P |
| TWR with start-of-day flows; TREPS as the risk-free rate; annualisation by observed business days | `performance` | Standard | U (rate, A) |

### Choices this build made beyond the specification

- **Docs here, not in a `docs/returns.md`**, and the command is `cr-strat returns`, as the specification named it.
- **Two more cost-table columns**: `rule_id` (fills record `id:status` of each rule they used) and `isin`; `kind`,
  `venue` and `liquidity` may list several values with `;`. ISIN and venue exceptions rank before the dates, so the
  exceptions for auctions, OMOs and switches (CS05 from 1 Oct 2019, CS15 before it) survive a user's newer schedule
  for all trades; among other rows the latest `effective_from` decides before specificity, and a user row dated before
  a default row of its component is superseded from that date, so `read_cost_table(replace_components=...)` lets a
  user's table stand alone.
- **Pricing without the calendar.** Each instrument is priced by a calendar-free copy of its `bonds` object: with the
  calendar, `bonds.cash_flows` looks up every payment date to maturity, which a strict calendar refuses past 2026. The
  calendar copy gives payment dates only near the day asked (coupons up to `d`, the redemption within 31 days).
- **A trade's mid.** A price without a mid is its own mid (no spread measured, noted); no price at all fills at the
  day's traded mid plus or minus the half spread from the cost table; an OIS without a rate at the day's par rate.
- **A first mark from the fill.** A position whose security has no mark yet is valued at the fund's own fill
  (`mark_source = fill`).
- **Funding details**: TREPS first, then market repo; the haircut's equity as the borrowed amount times the book's
  ratio of haircut to lendable value; the margin as a reduction of the limit, not cash; charges on the deal date, on
  one row of the night; the funding lines at book level (`positions.funded` gives each bond's pro-rata share for
  reporting).
- **A decision before the valuation is refused**, not revalued: striking a second NAV at an earlier decision time
  would need a second pass over the day's marks; set `valuation_time` to the decision time or earlier instead.
- **The back-test's other inputs** reach a strategy only through the `Snapshot`'s accessors, each cut at the decision
  time (the master to the securities issued or with a public result, their fixed terms only); the auction table can be
  built from the event ledger (`auctions_from_events`), with its announcement and result times and each line's name,
  by which a strategy bids in a new issue whose ISIN is not yet public.
- **NSS parameters from `curve-fit`**: `--nss-params` appends a row per day in the layout `--nss-params` of `returns`
  reads (`nss_params_row`, `append_nss_params`); spline curves have no such table.
- **Speed.** Each bond's flows are cached per settlement date for its model yields: 590 position-days with factor
  attribution ran in 0.9 s (1.55 ms each), which puts ten years of 100 positions at about 6.4 minutes if it scales
  linearly; that size was not run.

### What was checked, with the numbers

In [`tests/test_returns.py`](../tests/test_returns.py) (67 tests) and [`tests/test_costs.py`](../tests/test_costs.py)
(12), on synthetic data and typed-in facts (the 13 Nov 2026 auction's 6.94% GS 2036 at 97.43, CCIL's charges, the
stamp duty rates, the shipped holidays):

- R2 and R3, through the engine: Rs 100 crore of 6.94% GS 2036 held at a constant yield of 7.3203088173% earns
  accrual Rs 5,78,333.3333 and pull to par Rs 2,010.2267 from Friday 13 to Monday 16 Nov 2026, and Rs 1,92,777.7778
  and Rs 2,049.1315 on 17 Nov, a day's P&L of Rs 1,94,826.9093. R3b: bought on 13 Nov at 97.43 for 16 Nov, the
  forward is worth nothing that evening and 16 Nov books Rs 7,00,000.00 with no accrual.
- R16: 8.15% GS 2026 maturing on the holiday of 24 Nov 2026, marked at 5.80%, is redeemed on Monday 23 Nov for
  Rs 8,46,816.0454, accrual Rs 9,05,555.5556 and pull to par -Rs 58,739.5102; a trade settling in its shut period is
  refused.
- R4 to R6 on NSS (7.6, -2, -1, 2, 1.5, 8): roll-down Rs 7,991.5938, convexity Rs 0.042931, unexplained -1.8e-6 on a
  constant curve; the unexplained of 1, 10, 25, 50 and 100 bp shifts -0.0875, -87.27, -1,358.38, -10,798.30 and
  -85,304.15 rupees (third order); factor moves of 10, 5 and -3 bp recovered exactly, the lines -Rs 66,08,084.93,
  -Rs 8,69,191.60 and -Rs 6,86,159.26. Through the engine: on a constant curve the curve and rich/cheap lines stay
  below Rs 1e-4 (solver noise) and convexity plus unexplained below Rs 0.05 a day per day of roll squared; a beta0
  shift is all parallel.
- I4 and R7: a same-day round trip of Rs 100 crore at a mid of 97.43 loses Rs 8,41,651.41 exactly (spread
  Rs 8,30,441.41, CCIL Rs 9,500, GST Rs 1,710), every market line zero.
- R8 and I5: with `sizing="fixed"` the book borrows Rs 94,60,05,972.22 against a 3% haircut and pays Rs 1,44,363.10 a
  night (Rs 4,33,089.31 Friday to Monday); over 16 Nov to 16 Dec 2026 accrual, repo and the haircut's memo equal
  `funding.carry`'s income, repo and equity cost within 1e-9 relative, and the pull to par its restated value. With
  the default sizing deals are multiples of Rs 5 lakh, the margin 0.50% of the second leg, the TREPS charge and GST
  booked; a quarter-end spike of 6.52% on 30 Sep 2026 costs exactly that night's interest and 1 Oct's rate covers the
  2 Oct holiday and the weekend (four nights); an SDL-only book gets no TREPS money and is funded at market repo; a
  shortfall is refused or overdrawn; the `unregulated_company` preset refuses repo.
- R9: paying 6.134480% on Rs 100 crore against the synthetic B3 curve books -Rs 5,08,077.5151 on its trade date and,
  from 20 to 23 Nov at a constant curve, carry -Rs 43,929.8630 and roll-down -Rs 4,470.6316 (-Rs 48,400.4946); R9b,
  a +10 bp parallel and +5 bp slope move, +Rs 35,97,301.50, -Rs 1,41,859.14 and an interaction of Rs 441.48. A one-month
  swap settles on 21 Dec 2026 (20 Dec a Sunday) with its rounding line equal to `settlement_amounts` less the unrounded
  accrual, and its NPV then leaves the book.
- An S1 asset swap: Rs 100 crore of the 2036 bond with a pay-fixed swap on `asset_swaps.hedge_notional` leaves the
  book's DV01 under 5% of the bond's; twice that notional is refused under a hedging-only policy.
- Coupons (12 Dec 2026, a Saturday, income and cash on Monday 14 Dec), a partial close, a T+0 CP purchase and a T+2
  G-sec purchase restated to the mark, switch legs without spread or CCIL charge; a short through a coupon under
  `bank_or_pd` (the coupon paid away, reverse repo interest on 97% of the consideration), refused under `nbfc_non_pd`,
  beyond Rs 500 crore, in a T-bill or without a special rate; a long and a short of one bond net to zero on every
  market line (I7).
- Marks: a stale price held with its age and a warning from the sixth business day; model marks at the curve plus the
  last residual; a mark published the next morning not used that day; rows without a time never public when told so.
- Back-tests (I9): a row published after the decision time changes nothing decided before it; an order fills the next
  business day at the VWAP plus the half spread; without a traded price it waits and is cancelled.
- R14: NAVs 100, 101, 111.5, 109 and 112 with 10 added on the third day give returns 1%, 0.4504505%, -2.2421525% and
  2.7522936%, a TWR of 1.9099099% and a drawdown of -2.2421525%.
- Determinism (I8): reruns and shuffled trades and marks give the same bytes; the command line twice, the same bytes;
  a missing file exits 2, nothing to value 1. Refusals name the input and the rule (nineteen cases in one test, more
  beside the features they guard).
- Look-ahead (I9 again): a decision time of 18:00 before the 20:00 valuation is refused; with both at 18:00 and marks
  public at 19:00, the positions a strategy sees carry the previous day's mark (the fund's own fill on the first day),
  never one published after the decision. The snapshot's accessors on 16 Nov at 20:00: a new issue announced that day at
  17:45 shows no coupon, ISIN or result flag (its key `new GS 2066`), an auction announced on 23 Nov is absent, the
  residual fit for 18 Nov settlement (public on 17 Nov) is absent, and of three money-market rows only the one neither
  published nor revised after 16 Nov is seen; a macro store not given raises. A new 7.71% GS 2066 auctioned on Friday 20
  Nov (result public at 23:59:59) and issued on Monday 23 Nov has no row in `master()` and no ISIN in `auctions()` (only
  its line, `new GS 2066`) on 16 Nov and at 20:00 on 20 Nov, and both on 23 Nov; the master's stock, list, FAR, class
  and source columns come blank (checked on 16 Nov, and the new issue's stock on 23 Nov), and a bond maturing on 17 Nov
  reads `matured` False on 16 Nov and True on 17 Nov, though the master was built with True. Without an issue date, a
  bill marked on 16 Nov is in the master that evening and a bond with no mark or auction line is not; a when-issued mark
  of the new issue on 16 Nov leaves it out.
- Auction orders: a bid of Rs 50,000 crore decided on 12 Nov for the 13 Nov auction of Rs 34,000 crore fills Rs 3,400
  crore at the cut-off of 97.43 (10%, noted); a non-competitive bid of Rs 2 crore fills at the weighted average 97.46; a
  non-competitive Rs 1,000 crore, an order decided on 6 Nov before the 9 Nov announcement, and an auction table without
  announcement times are refused, each with its reason in `fills.csv`. A bid decided on 16 Nov for the new GS 2066 of 20
  Nov is refused when it names the ISIN, not public before the result, and fills at the cut-off of 100.00 on 20 Nov when
  it names the line `new GS 2066`.
- When-issued: a when-issued sale of Rs 500 crore at 97.40 covered at the auction at 97.35 makes Rs 2,50,000 under
  `nbfc_non_pd` and `bank_or_pd`; left open past the close of the auction day (20 Nov) it is refused under
  `nbfc_non_pd`; Rs 3,500 crore is refused beyond 10% of the Rs 34,000 crore notified. A when-issued purchase is a
  forward to the issue date (23 Nov), valued at the day's yield restated to it. Carried past the auction day under
  `bank_or_pd`, an uncovered when-issued sale of Rs 5,000 crore (within the 25% when-issued limit of Rs 8,500 crore) is
  refused at the close of 20 Nov as a short sale beyond Rs 500 crore, one of Rs 100 crore without a special repo rate is
  refused then too (before, it ran to the issue date and failed there), a variant of the preset without short sales
  refuses it, and Rs 400 crore with a rate is carried and settles on 23 Nov as a short of Rs 400 crore lent against in a
  reverse repo.
- Funding: Thursday 1 to Monday 5 Oct 2026 (2 Oct a holiday, Saturday 3 Oct dealt at 6.00%) costs four nights at the
  deal day's 5.90% by default and 5.90 + 5.90 + 6.00 + 6.00 by `calendar_day`; `positions.funded` sums to the night's
  borrowing on every day of a three-bond book; the risk-free rate past its table is refused, or held and noted; a table
  ending Friday 20 Nov held to Friday 27 Nov counts four nights (23 to 26 Nov) for the funding and the risk-free rate
  alike, six from Saturday 21 Nov when each calendar day is priced alone, and four with two books borrowing over the
  same nights (the funding note counted book-nights before, 8 here); market-repo charges equal `costs.repo_deal_costs`
  on the collateral's face (above Rs 2,000 a night); a night split between market repo and an overdraft carries its
  charges on one row, equal to the `repo_charges` line.
- Payment dates: a G-sec maturing on Sunday 6 Dec 2026 is redeemed on Friday 4 Dec; a purchase or a sale settling on
  4 Dec is refused, as is a CP maturing that Sunday bought for 4 Dec. A run may end on 31 Dec 2026: that day is
  flagged `calendar_projected` (not 30 Dec) and every identity holds.
- Coupons on a business day: 6.92% GS 2039's coupon on Wednesday 18 Nov 2026 is counted once, accrual Rs 1,92,222.22 a
  day on Rs 100 crore from 17 to 20 Nov and Rs 3.46 crore of cash on 18 Nov.
- Short curves: a CP of Rs 50 crore against OIS curves to one year attributes a 2 bp parallel rise to `curve_parallel`
  with the short fit noted, curves to two years fit all three factors, a six-month swap on the one-year curve runs,
  and a curve with no usable node fits nothing (all in `curve_other`). A CP on a constant OIS curve books curve lines
  below Rs 1e-4 over the weekend of 14-15 Nov, its 2 bp a day over OIS in `rich_cheap`.
- Inputs: T9 (a buy) runs before T10 (a sale) on a day without times; a sale without a time runs after a purchase at
  10:00, a purchase without a time before a sale at 09:00, untimed purchases, timed trades and untimed sales in that
  order, and a sale at 10:00 before a purchase at 15:00 is still refused as a short sale under `nbfc_non_pd`; five flows
  of one day in three orders give the same NAV to the last bit; a master listing a bond twice with different coupons,
  two CCIL classes from one date and two cut-offs for one auction are refused, an exact repeat kept once; a bond whose
  only mark is of 1 Oct is valued at its 13 Nov fill of 97.40 that day, then held stale from it.
- Performance: NAVs 98, 97, 99, 101 and 100 on Rs 100 of capital give a drawdown of -3% from the start of 16 Nov (the
  first return day) to 17 Nov, two days, recovered on 19 Nov, noted.
- The command line: an all-blank row of a CSV is dropped, line numbers kept, in every input (trades, flows, marks,
  haircuts, CCIL classes, master, NSS parameters, residual history, fixings, OIS quotes and the cost table give the same
  nine files with a trailing `,,,` row as without), as in the library's stock, auction and residual-history tables; an
  output directory that cannot be written exits 2; `--reference sdl=sdl` puts an SDL's 5 bp curve shift in
  `curve_parallel` where without it the SDL curve is named unused in the notes; `curve-fit --nss-params` writes the row
  `--nss-params` reads, a refit of the day replacing it.
- Costs: a user's newer CCIL schedule (Rs 100 per crore) leaves auctions, OMOs and switches at zero (CS05) while an
  NDS-OM trade of Rs 100 crore pays Rs 5,000; a user row naming the auction venue charges Rs 1,000; a half spread for
  `semi_liquid;illiquid` applies to both classes; stamp duty of Rs 492.50 on a Rs 50 crore CP sale at 98.5 (none on
  the purchase) and on either side of a CD trade.
- Eighteen deliberate defects planted one at a time in `returns.py` (the accrual base, the coupon entitlement, the
  redemption's pull, repo interest, the mark's public time, the spread's sign, the roll, convexity, lots, the margin,
  the OIS carry's sign, a stale mark's age, short-sale access, TREPS caps, the snapshot's time, fills at stale prices,
  receivables, a forward's settlement) were each caught by a test. Each test added by the review's fix pass fails on
  the files as they were before it, except the business-day coupon test: the defect it was written against (a payment
  window of `p <= date`) cannot change a result, since `bonds.cash_flows` returns only flows after the date it is
  given, so the test was written to catch a coupon dropped on its payment day instead. The tests the second fix pass
  added or extended (the snapshot's master and auctions, a carried when-issued short, the projection notes, the order
  of a day's trades, blank rows, and in `tests/test_event_ledger.py` a duplicated line's redemption and stock) each
  fail on the files as they were before it, and seventeen deliberate defects in its fixes were each caught.

**Free data** (scratch, not in the repository): four G-secs with their real terms (6.75% GS 2029, 6.94% GS 2036,
6.92% GS 2039, 7.09% GS 2054; Rs 210 crore of face on Rs 10 crore of capital) marked for 13 Nov to 14 Dec 2026 off a
synthetic NSS curve, funded at the RBI store's TREPS rates of 13 and 16 Nov 2026 (5.65% and 5.57%, held flat for the
27 later nights). Over 21 business days every identity held (largest residual Rs 4.8e-7); the month's lines were
accrual Rs 1,03,66,444, funding -Rs 77,32,381, spread cost -Rs 27,17,078, curve parallel Rs 39,29,235, roll-down
Rs 4,53,354, unexplained Rs 1.52; borrowing ran from Rs 158.55 to 198.40 crore, all in TREPS (the illiquid 2039 bond
fitted inside CCIL's caps); the command line gave the library's NAV and the same bytes twice. Rerun twice after the
review's fixes, it gave the same numbers and bytes as before except `positions.funded`, which now sums to each night's
borrowing (before, the column missed it by up to Rs 11.57 lakh), and a new note that 27 nights of the risk-free rate
(17 Nov to 14 Dec, the funding's 27 nights) were held at the store's last rate from 17 Nov; that note first counted
the 18 business days whose nights were held, not the nights, and the funding note now names its first night too. A
carry demonstration, not a return: the marks are synthetic, the leverage about 17 times, the haircuts and classes
invented.

**Not checked:** any licensed mark, curve, MIBOR fixing or OIS quote; a real back-test or one longer than a month;
ten years of 100 positions (the timing above is extrapolated); a quarter-end or 31 March in real funding data; special
repo; CCIL's caps or haircuts on a real book; the default costs against a real contract note; the TREPS margin's form
for a constituent; the snapshot's `macro` accessor on a real macro store; auction allotment against real results.
An auction order fills on the auction day, so the positions a strategy sees at 20:00 that day show its allotment (the
new issue's ISIN and the cut-off as its mark) although the auction table's result may count as public only at
23:59:59 (the event ledger's `end_of_day` rule): RBI publishes results in the afternoon, but when a bidder may act on
its allotment is a choice not yet made (the master and the auction lines stay cut).

**Not built:** corporate bonds (S2's AAA bonds), FRBs and inflation-indexed bonds (no pricer: refused); CSA, clearing
margin or charges on OIS; security-wise repo rates (a short's special rate is the user's, constant over the short); a
key-rate DV01 table (DV01 is by tenor bucket); funding lines allocated to positions (they stay at book level;
`positions.funded` shares the borrowing for reporting); the tradable universe from the issuance ledger (a strategy's
own job); an auction's own allotment beyond the limits above (pro rata at the cut-off, the 5% non-competitive pool, a
bid's own yield, T-bill non-competitive bids); a back-test from the command line (`cr-strat returns` runs a list of
trades; `run_backtest` is the library's); a spline curve through the command line; intraday timing beyond the
decision time; tax.

**Before real use.** Every number above is plumbing until licensed prices are fed: traded marks need CCIL's trades by
security (a paid Data Agreement), valuation marks FBIL's prices (a subscription to store or back-test them, and its
Derived Product License for a curve or model fitted to them, clause 5.1 of its terms); S1's and S7's OIS legs and S2's
reference need FBIL's overnight MIBOR and MIBOR-OIS (a subscription; beyond five years CCIL's trade prints or a
broker). Funding needs a history of TREPS and market repo rates, which RBI publishes without a reuse licence (ask
before keeping one: `docs/data_enquiries.md`), the fund's own haircut table and CCIL classes (CCIL's list is
restricted), and its cost terms in place of the U rows; and the access preset must be the fund's. The smallest set for
S1 is CCIL's trades (or FBIL's valuation) for the bonds plus FBIL's MIBOR and MIBOR-OIS
([`rates_data_sources.md`](rates_data_sources.md), What each strategy needs; Licences below).

## The S4 event ledger and supply measures

For S4, the supply and RBI-operation cycle: [`event_ledger.py`](../src/cr_strat/event_ledger.py) puts every auction,
switch, buyback, open market and liquidity operation, issuance calendar, MPC meeting, macro release and index event in
one table, each with the time from which its announcement and its result were public and how each time was found;
measures the supply in face and in DV01 by week and tenor bucket against the outstanding stock; and builds business-day
windows around each event to read a later price panel on. `cr-strat event-ledger` runs it from files. It joins what the
repository already holds: the auction table and issuance ledger ([`auctions.py`](../src/cr_strat/auctions.py),
[`issuance.py`](../src/cr_strat/issuance.py)), the RBI store's tables ([`rbi_store.py`](../src/cr_strat/rbi_store.py)),
the MPC calendar ([`policy_path.py`](../src/cr_strat/policy_path.py)), MoSPI's release dates
([`mospi.py`](../src/cr_strat/mospi.py)) and the hand-kept index events
([`global_sources.py`](../src/cr_strat/global_sources.py)); every DV01 comes from `bonds`. The rules are those of part B
of the return-engine specification of 19 Nov 2026 (its section numbers in brackets). Built and tested on synthetic data
and a few typed-in facts, and run on the free data the scratch builds hold; no price panel exists yet.

### What it does

| Piece | Function | Output |
|---|---|---|
| Event rows | `auction_events`, `switch_events`, `buyback_events`, `redemption_events`, `operation_events`, `calendar_events`, `policy_events`, `macro_events`, `index_event_rows`; `collect_events` runs them all, `build_event_ledger` joins parts | One row per event and security (`EVENT_COLUMNS`): kind, security, tenor bucket, amounts, the auction's figures, DV01, the times of announcement and result with their basis, settlement, sources, the flags and notes known at the announcement and those the result decides (`result_flags`, `result_notes`); a new issue's id and key without its coupon (`new GS 2036`) |
| What was known | `events_as_of(events, t)` | The rows announced by `t` (or, lacking an announcement time, resulted by it), with what the result decides blank until it was public: the result's fields, flags and notes, and a new issue's coupon, ISIN, auction id and source reference |
| Supply | `supply_table`, `stock_on_dates` | Per period, kind and bucket (`SUPPLY_COLUMNS`): gross, switches, buybacks, OMOs and redemptions in face and DV01, net supply, the stock and its DV01 at the period's start, the shares of supply in it |
| Identities | `check_supply` (E1), `check_ledger_join` (E2) | The supply by settlement month against `issuance.supply_summary`, cell by cell; one auction row per ledger issuance row and back |
| Auction metrics | `auction_metrics` | Bid-cover (competitive and all bids), tails in yield and price, devolvement and non-competitive shares, underwriting commission; with a licensed panel, the concession and the residual's move into and out of the auction |
| Forward supply | `forward_supply`, `read_calendar_lines` | On each date, the calendars' plan and the auctions announced for the weeks ahead, beside what came, in face and DV01; the plan blank, and noted, for a kind no weekly calendar of the inputs plans (T-bill lines can come from a table of the user's) |
| Windows | `event_windows`, `overlaps`, `isolate`, `attach_panel` | Business-day windows (`WINDOW_COLUMNS`), the other events inside each window, isolated events, and a later (date, ISIN) panel's path around each event |
| Gaps | `input_gaps` | What the inputs do not cover: auction-file gaps, undeclared switch coverage, the store's possible gaps, results and announcements the auction table lacks, duplicated auction lines, times that are rules |

```bash
cr-strat event-ledger --auctions data/out/ledger/auctions.csv --ledger data/out/ledger/ledger.csv \
    --store data/rbi_store --switches data/out/ledger/switches.csv --snapshot data/out/goi_outstanding.csv \
    --master data/out/sovereign_master.csv --handbook data/raw/rbi/hbs_t221.html --macro \
    --calendar-lines data/raw/tbill_calendar_lines.csv --out-dir data/out/events
cr-strat event-ledger ... --panel data/raw/marks.csv --panel-columns yield_pct --tolerance 2   # licensed prices
```

Inputs: the auction table and the ledger the same `cr-strat auctions` run wrote (they go together; a ledger whose
issuance rows name auctions the table lacks is refused); the RBI store (release tables, derived tables, `releases.csv`,
`gaps.csv`) and/or a directory `cr-strat rbi-releases` wrote (with both, `merge_tables` joins them table by table and
keeps the store's copy of a release both hold); optionally Handbook Table 221 pages (bids, devolvement), the security
master (ISINs of auctions known only from an announcement), the switch table (destination cut-offs), RBI snapshots (the
stock), calendar lines of the user's (`--calendar-lines`, `CALENDAR_LINE_COLUMNS`: RBI's T-bill calendar, which the
store does not parse, typed in by week and tenor with the time it was public), an MPC meetings file (default: the
shipped FY2026-27 file; an optional `decision_time` column, `HH:MM`, gives a meeting's own announcement time) and a
policy-rate table, MoSPI's release table (`--macro`, `--macro-releases`; `--macro-from`/`--macro-to` add rule-dated
releases), an index-event table or a macro store, a price panel and `curvefit`'s residual history. It writes
`events.csv`, `supply_weekly.csv` (`supply_monthly.csv` with a month period), `supply_monthly_check.csv` (E1),
`ledger_join_check.csv` (E2), `auction_metrics.csv`, `forward_supply.csv`, `windows.csv`, `overlaps.csv`,
`isolated.csv`, `gaps.csv` and `notes.txt`, with `--panel` also `event_paths.csv`. Times are written
`2026-11-13T23:59:59` (India time, no zone), dates `2026-11-13`. `--start` and `--end` choose the events written (by
event date); the identities E1 and E2, the yields the stock is priced at, forward supply's trailing DV01 and the
overlaps still read every event of the inputs, so an event near the range's edge sees its neighbours, and a period the
range cuts counts only its events in the range (start on a Monday, or the 1st for months, for whole periods; the supply
table goes by its period's own date, the settlement date for a settlement period). A row repeated in one input table
(the auction table, `omo_operations`, `liquidity_operations`, `supply_calendar`) is refused, naming the table and the
rows. A missing file or bad input exits 2, no events 1, else 0; the same inputs give the same bytes.

### Method

**Rows and sources** (B7.1, B7.2). An auction row per line of the auction table, joined to the ledger by
`auction_id`: `booked_cr` is the ledger's amount (a shortfall booked as devolved, a competitive-only amount at the
notified one) and `accepted_cr` the source's; a line that does not count as issuance (rejected, a conversion line) is
kept with `booked_cr` 0 and flagged. Bids, weighted averages and greenshoes come from RBI's full results (the store's
`tables/` or `rbi-releases`' CSVs, joined on the auction date and name key, or tenor for T-bills), bids and devolvement
from Handbook Table 221; `devolvement_inferred_cr` is the notified amount less the accepted one on a shortfall the
ledger books as devolved. A result in the tables with no auction row (newer than the auction table) is a row flagged
`not_in_ledger;result_only`, an announcement with neither `announced_only`. Underwriting results are rows of their own
(the commission cut-off in paise per Rs 100). Switch legs, buybacks and redemptions are the ledger's rows (signed: minus
for a source leg, a buyback, a redemption), OMOs and variable rate repo and reverse repo auctions the store's derived
tables (OMO face accepted in `accepted_cr`, not in `booked_cr`: the ledger does not hold RBI's purchases), calendar
lines the store's `supply_calendar` (dated on their release, the planned week in `period_start`/`period_end`), MPC
decisions and minutes `policy_path.load_mpc_calendar`'s, macro releases `mospi.ReleaseCalendar`'s, index events the
hand-kept table's. What the result decides is kept apart from what the announcement said: `flags` and `notes` hold
the announcement's (a security's kind, an estimated time), `result_flags` and `result_notes` the result's (a
shortfall, a rejection, `not_counted`, a greenshoe, the devolvement note, the amounts accepted, the MPC's new rate). A
new issue's event id and key carry no coupon, which is its cut-off yield: `new GS 2036`, `new KA SGS 2034`, `new GOI
SGRB 2036`, `new FRB 2034` (`new_issue_key`; known at the announcement, unique in a day). A T-bill or CMB greenshoe is
the competitive amount accepted above the notified one (non-competitive bids sit outside the notified amount; blank
where the result prints no competitive amount); GoI dated and SDL lines keep the amount accepted above the notified.
Two auction lines of one security on one day under different auction ids (a source row read twice) are flagged
`duplicate_line`, as is the security's redemption, whose amount the issuance ledger built from the same table counts
the copy in (noted with the copy's amount): the supply table leaves the numbered copy out of the gross supply, the
redemption and the stock (`keep_duplicates=True` keeps it), identity E1 then shows the issuance ledger's double count
in the auction's month and the redemption's, and `input_gaps` lists both.

**Times** (B6, A5.1). Every time is India time without a zone. A result's time is the store's feed time of its release
where the store saw it in the feed (`feed`); else 23:59:59 on the release's date (`release_date`: a saved page); else
23:59:59 on the auction date (`end_of_day`, UNVERIFIED and conservative: a 20:00 decision sees it the next day).
`result_rule="rbi_fixed_time"` uses RBI's fixed result times where no feed time was seen, at the later of the two times
the search summaries give: T-bills 14:00 from 26 Aug 2016 and 15:30 from 14 Jan 2020, GoI dated securities 14:30 from 26
Aug 2016 to 13 Jan 2020 (partly verified: the circular of 25 Aug 2016 and the press release of 13 Jan 2020 were read
only through search summaries; the start days are UNVERIFIED); SDLs, and dated securities since 2020, keep the observed
rule. An announcement the store did not see is estimated: `conservative` (default) 18:00 on the business day before the
auction (UNVERIFIED); `typical` 18:00 on the Monday of the auction week for GoI dated auctions and the Friday before for
SDLs and T-bills, moved past a holiday but never past the business day before (partly verified on three releases of Nov
2026). Switches, buybacks and underwriting were never seen announced: estimated the same way. MPC decisions are public
at the meetings file's `decision_time` where it gives one, else at their era's time (`MPC_DECISION_TIMES`: 11:00 before
the MPC, 14:30 from its first decision on 4 Oct 2016, 11:45 from April 2019, 10:00 from 2024, read through search
summaries; December 2019 to 2023 not found and taken as 14:30, so that a decision is never seen early: UNVERIFIED), and
dated from the day their schedule was announced; minutes at 23:59:59 on the 14th day after (RBI Act section 45ZL, partly
verified; the time UNVERIFIED). A calendar line of the user's is public from its `public_from` (a date alone: 23:59:59
on it; basis `user_table`). MoSPI's times come from its release table, else its rules. A redemption's date is public
from its security's first auction in the inputs (`since_issue`), its amount from the last auction, switch or buyback
before it (`last_change`). `announcement_basis` and `result_basis` name the rule (`TIME_BASES`) and
`announcement_estimated` / `result_estimated` say whether the time is a rule rather than a print. Identity E4: no result
before its announcement (an observed announcement after its result is capped there and flagged).

**DV01** (B7.3). `dv01_cr_per_bp = booked_cr x dv01_per100 / 100`, `dv01_per100` from `bonds` at the cut-off yield for
the settlement date (`FixedRateBond` on G-sec or SDL conventions, an odd first coupon from the issue date, a new issue
at par with its coupon equal to the cut-off yield; `DiscountInstrument` for T-bills and CMBs); a line without a yield
at the weighted average yield, else its coupon (`dv01_basis`, flagged); FRBs have no pricer and no DV01 (counted in
`dv01_missing_cr`). A switch's destination legs are priced at their cut-off; its source legs, whose yield the result
does not print, at the latest yield of the security public before the switch (an auction, OMO or switch cut-off), else
the coupon (UNVERIFIED, flagged). Redemptions carry no DV01.

**Supply** (B7.3). Rows by period, kind (`issuance`'s kinds: `gsec`, `green`, `frb`, `sdl`, `tbill`) and tenor bucket
(residual maturity on the settlement date, as `supply_summary` buckets): gross issuance at the ledger's amounts,
switches in and out, buybacks, OMO purchases and sales (face accepted), redemptions, each with its DV01; `net_cr` as
`supply_summary` (gross plus switches, buybacks and redemptions), `market_net_cr` also less OMO purchases plus OMO
sales. The period is the Monday-Friday week of the auction date by default (`auction_week`, the GoI calendar's unit), or
`auction_month`, `settlement_week`, `settlement_month`. With the ledger, the stock at the end of the day before the
period starts, by `stock_on_dates` (the rules of `issuance.outstanding_on` on arrays: snapshots backward and forward,
forward sums, redemption days, completeness flags; it agrees with `outstanding_on` field by field in the tests and on
six dates of the real ledger, and takes 4.9 s for 608 weekly dates where `outstanding_on` takes 4.5 to 5.3 s a date),
its DV01 at each security's latest cut-off yield public by then, else its coupon (`stock_dv01_basis="last_cutoff"`,
UNVERIFIED; `stock_dv01` sums the known DV01s, and FRBs, securities without a maturity and bills with no yield in the
inputs are the face in `stock_no_dv01_cr`), and the shares `gross / stock` and `gross DV01 / stock DV01`, blank unless
every security of the cell has a complete history (`issuance_complete` and `operations_complete`). `auctions_covered`
says whether the ledger's auction files cover the period.

**Auction metrics.** `bid_cover = competitive bids / notified` (UNVERIFIED reading; `bid_cover_all` adds the
non-competitive bids, which for T-bills include the state governments' bids outside the notified amount), `tail_bp = 100
(cut-off - weighted average yield)` (positive when the cut-off was cheaper; for an OMO purchase or a variable rate repo,
where RBI accepts the highest yields or rates first, `100 (weighted average - cut-off)`, so a tail is never negative
when the operation ran by its rules), `tail_price = weighted average price - cut-off price`, the devolvement share
(reported, else inferred), `nc_share = non-competitive accepted / booked`, the commission. With a price panel
(licensed), `concession_bp = 100 (cut-off - the market yield on the business day before)` for a reissue and, for a new
issue, against the par yield at its maturity on a curve of that day; with `curvefit`'s residual history, `pre_bp = r(t0)
- r(t0
- 5)` and `recovery_bp = r(t0 + 5) - r(t0)` in business days (UNVERIFIED window).

**Forward supply.** On each date at 20:00 (or a time given), for the auction weeks from that date's own week on: the
weekly calendars' planned amounts public by then (the GoI calendar's; T-bill lines from a table of the user's), by kind
and bucket (the latest release covering a week replaces earlier ones: a revised calendar), the auctions announced by
then whose result was not yet public, and, known only later, the auctions held (`realised_notified_cr` and
`realised_cr`, at the booked amount, else the accepted one) and the gap `realised - planned`. An auction is compared in
the bucket a calendar line of its tenor would hold (a new issue by its nominal tenor, `rbi_store.nominal_tenor_bucket`;
a reissue by residual maturity), since a new N-year issue measured on 365.25-day years can land a bucket up. A planned
amount is priced at the trailing DV01 per crore of the bucket's auctions public by then (13 weeks, UNVERIFIED). A week
and kind no weekly calendar of the inputs plans (SDLs, whose calendar gives quarter totals; T-bills, unless their lines
come from a table of the user's) have `planned_cr` and the gaps blank, noted, not zero. The state calendar's quarter
totals give rows of the quarter with the part realised to date. `realised_covered` says whether the auction files cover
the period. Identity E5: a calendar never enters a date before its own `public_from`.

**Windows** (B8). Day 0 is the first Mumbai business day whose decision time (20:00, UNVERIFIED) is at or after the
anchor's time (`decision_day`, the rule the windows use): the result's (default; a calendar line's release and a
redemption's own day, since they have no result of their own), the announcement's, the event date, or the settlement
date. Offsets run `-pre` to `+post` business days. `others_day0` counts the other events whose day 0 is that date;
`overlaps` lists them with their offset and marks siblings, the other lines of one event (the securities of one GoI
auction with its green bonds, FRBs and underwriting, of one SDL or T-bill auction, the legs of one switch, one
calendar's lines); `isolate` flags events with no other day 0 within a radius, siblings and kinds asked (liquidity
operations, say) set aside. An event whose window reaches a year the holiday list does not cover is skipped unless
`project_calendar` (weekdays then, flagged). `attach_panel` joins any later (date, key) panel: the returns module's
marks, `price_panel`'s rows, or `curvefit`'s residual history (dated by settlement: `panel_dates="settlement"` moves
each row to its trade date), or a panel with one row per date (`key=None`) for curve-level events; nothing is
interpolated, `tolerance` business days let the nearest row on the same side of day 0 stand in (`event_prices`' rule),
and `panel_match` counts exact, nearest, missing and keyless rows; `<column>_chg` is the change from day -1.

### Conventions and their sources

V verified (a saved copy of a primary source, read by the specification's job or an earlier one), P partly verified,
U UNVERIFIED (a named parameter; listed for the user to confirm).

| Rule | Engine | Source | Status |
|---|---|---|---|
| GoI auctions on Fridays, announced the Monday before, T+1; SDLs on Tuesdays and T-bills on Wednesdays, announced the Friday before | `estimated_announcement(typical)`; settlement from the auction table | RBI press releases 2026-2027/1474, /1497, /1498 (saved) | V (three weeks) |
| An announcement not seen: 18:00 on the business day before | `announcement_rule="conservative"` | None | U |
| A result: feed time, else the release date's end, else the auction day's end | `result_rule="observed_or_end_of_day"` | The RBI store's rule; FIMMDA and the Handbook print no times | V (store), U (end of day) |
| RBI's fixed result times (T-bills 14:00 and from 2020 15:30; dated 14:30 to 2020) | `RBI_FIXED_TIMES` | RBI circular of 25 Aug 2016, press release of 13 Jan 2020 (search summaries) | P (start days U) |
| Switch, buyback and underwriting announcements: as an auction's | `estimated_announcement` | Not sampled | U |
| VRR/VRRR times and windows | the store's `liquidity_operations` | Revised liquidity framework (PR 2025-2026/1201, saved); store | V |
| MPC decision at its era's time (11:00 before 4 Oct 2016, 14:30, 11:45 from April 2019, 14:30 from December 2019 to 2023 for want of a source, 10:00 from 2024) or the meetings file's own; minutes 14 days later at 23:59:59 | `MPC_DECISION_TIMES`, `mpc_decision_time`, `minutes_days` | Search summaries (the era times); RBI Act s.45ZL through a search summary | P (Dec 2019 to 2023 and the minutes' time U) |
| A calendar line of the user's public from its `public_from`, a date alone at 23:59:59 | `read_calendar_lines` | None (RBI's T-bill calendar is not parsed) | Choice |
| Supply at the ledger's booked amounts | `booked_cr` | `issuance` (identity E1) | V |
| DV01 at the cut-off yield for settlement; FRBs without DV01 | `line_dv01` | `bonds` | Choice |
| A switch's source leg at the latest yield known before it, else its coupon | `switch_events` | None (the result prints no source yield) | U |
| Bid-cover on competitive bids over the notified amount; tail on yields, the cheaper side positive (for RBI's purchases and repo, the weighted average less the cut-off) | `bid_cover`, `tail_bp` | Market usage | U |
| A T-bill or CMB greenshoe: the competitive amount accepted above the notified one | `_greenshoe` | Non-competitive T-bill bids sit outside the notified amount (PR 2026-2027/1497, saved) | V (rule), Choice (formula) |
| What the result decides kept apart (`result_flags`, `result_notes`); a new issue's id and key without its coupon | `events_as_of`, `new_issue_key` | Its coupon is its cut-off yield | Choice |
| Two lines of one security on one day under different auction ids: flagged with the security's redemption, the copy left out of the supply, the redemption and the stock | `duplicate_line`, `keep_duplicates` | None | U |
| Period: the Monday-Friday auction week | `period="auction_week"` | The GoI calendar's weeks | V (unit) |
| Stock at the end of the day before the period; its DV01 at each security's latest cut-off | `stock_on_dates`, `stock_dv01_basis` | `issuance.outstanding_on`'s rules | V (rules), U (yield) |
| Day 0 at the first 20:00 decision at or after the anchor's time | `decision_time` | The return engine's decision time | U |
| A calendar line compared in its nominal tenor's bucket | `forward_supply` | `rbi_store.supply_calendar`'s rule | Choice |
| Planned DV01 at the trailing 13 weeks' DV01 per crore | `dv01_weeks` | None | U |
| Pre- and post-auction residual moves over 5 business days | `metric_days` | None | U |

### Choices this build made beyond the specification

- **The module is `event_ledger.py`**, the specification's name, with its tests in `tests/test_event_ledger.py`; the
  docs are this part, not a `docs/event_ledger.md`.
- **More columns than the specification's**: `supply_kind` (the ledger's kind, so the supply groups as
  `supply_summary` does), `coupon_pct`, `tenor_days`, `period_start`/`period_end` (a calendar line's week),
  `nc_bids_cr`, `announcement_estimated`/`result_estimated`, `auction_id` (for E2), `result_flags` and `result_notes`
  (what the result decides, hidden with it). The supply table adds buyback and
  OMO DV01, `market_net_cr`/`market_net_dv01`, `dv01_missing_cr`, `stock_date`, `stock_complete_cr`,
  `stock_no_dv01_cr`, `auctions_covered`; forward supply adds `realised_notified_cr`, `realised_to_date_cr` and
  `realised_covered`; windows add `event_date`, `key` and `others_day0`.
- **`stock_on_dates`** reimplements `issuance.outstanding_on`'s arithmetic on arrays for many dates, because
  `outstanding_on` loops over keys and would take about 50 minutes for the weekly stock of 2015-2026; the tests compare
  the two on every field.
- **Auctions the auction table lacks** are rows (`result_only`, `announced_only`), and **underwriting** rows of their
  own; supply counts only the ledger's amounts, so a result-only row adds nothing until the auction table is rebuilt.
- **Anchors**: the default `result` anchor uses a calendar line's release and a redemption's own day, which have no
  result of their own; **siblings** group the lines of one event by auction family.
- **Forward supply** starts with the date's own week (on a Monday the Friday auction counts), and reports the gap
  against the realised auctions' bucket as a calendar would hold them; T-bill weeks are planned only from a table of
  calendar lines the user types in (`--calendar-lines`), since RBI's T-bill calendar has no parser.

### What was checked, with the numbers

In [`tests/test_event_ledger.py`](../tests/test_event_ledger.py) (35 tests), on synthetic auction tables run through
`issuance.build_ledger`, synthetic store tables written in the store's layout, and typed-in facts (the 13 Nov 2026
auction of 6.94% GS 2036 and its underwriting, the T-bills of 11 Nov 2026, the switch of 16 Nov 2026, the shipped
holidays):

- E1 and E2 on a ledger with a new issue, reissues, a shortfall booked as devolved, a rejected line, an SDL reissue
  without a yield, a T-bill, a switch and a buyback: every settlement-month cell equals `supply_summary`, every ledger
  issuance row maps to one auction row and back; a duplicated or stray auction id is reported or refused.
- R1: 6.94% GS 2036 at 97.43 for 16 Nov 2026 yields 7.3202785589% with a DV01 of 0.0664353131 per 100, so the
  Rs 34,000 crore auction supplied 22.588 crore per bp; each row's DV01 is its booked face times `bonds`' DV01 / 100
  and a week's is their sum (E3).
- R11 from typed-in result rows: bid-cover 2.1475 (2.1491 on all bids), tail 0.45 bp, price tail 0.03, commission 0.54
  paise; T-bills 3.3808 and 0.87 bp, 2.5027 and 1.24 bp, 3.0784 and 2.18 bp (91 days on all bids 4.8327).
- R13: the 13 Nov 2026 result (23:59:59) has day 0 on Mon 16 Nov and offsets -5 to +6 on 6, 9, 11, 12, 13, 16, 17, 18,
  19, 20, 23 and 25 Nov (10 and 24 Nov holidays); anchored on the auction date, day 0 is 13 Nov.
- R15: the destination legs of the 16 Nov 2026 switch add 11.2998 crore per bp (2.8659, 2.4350, 3.2616, 0.1605,
  2.5767), 14,234.826 crore in and 13,806.936 out; a source leg is priced at its last known yield when there is one.
- Times: a feed time, a saved page's end of day, the auction day's end, RBI's fixed times (and a print beating them),
  the conservative and typical announcement rules across the holidays of 10 Nov 2026, 26 Jan 2026 (a Monday) and
  3 Apr 2026 (a Friday), and a projection beyond 2026; E4 on every row with both times.
- Look-ahead: an auction is invisible before its estimated announcement (12 Nov 18:00), visible with its notified
  amount and no cut-off at 20:00 on its day, complete after 23:59:59. A new issue seen before its result shows no
  coupon, ISIN, auction id or source reference, its key and name `new GS 2035` (an SDL's `new KA SGS 2045`, its 7.40%
  nowhere in the row); a shortfall's flag and devolvement note appear only with the result.
- Times of MPC decisions: 4 Oct 2016 at 14:30, 4 Oct 2019 at 11:45, 5 Feb 2021 at 14:30, 4 Dec 2026 at 10:00, and a
  meeting's own 14:15 from the meetings file; `mpc_decision_time` 09:00 replaces the era rule but not the meeting's
  own; `noon` is refused; the new repo rate of 6.25% is hidden at 12:00 on 4 Oct 2016 and shown at 14:30. Through the
  command line, a `--mpc` file's `decision_time` of 10:15 for 4 Dec 2026 times that decision (basis `meeting_time`)
  while 7 Oct 2026 keeps its era's 10:00, and `noon` exits 2.
- Greenshoes and tails: the T-bills of 11 Nov 2026 with competitive acceptances of 7,600, 7,600 and 6,650 crore have no
  greenshoe (500 crore when the 91-day accepts 8,500; blank when the result prints no competitive amount); a variable
  rate repo cut off at 5.30% with a weighted average of 5.35% tails +5 bp, a reverse repo at 5.49% and 5.45% +4 bp.
- Duplicates: a new-issue line read twice under two auction ids is flagged on both rows, the week's gross supply is
  30,000 crore, not 50,000 (`keep_duplicates=True` gives 50,000), E1 then shows the issuance ledger's double count and
  `input_gaps` lists it. The security's redemption on 6 Apr 2035, -57,400 crore in the issuance ledger, is flagged and
  noted with the copy's 20,000 crore; the supply table books -37,400 crore as without the copy (and a stock of 35,000
  crore on 30 Apr 2025 and 37,400 on 31 Mar 2035, not 55,000 and 57,400, which `keep_duplicates=True` gives), and E1
  differs in that month's redemption and net supply too. A row repeated in the auction table, two different rows under
  one auction id and a repeated `omo_operations` row are refused, each naming its table.
- Day 0: a result at 23:59:59 on Friday 13 Nov 2026 has day 0 on Monday 16 Nov, one at 19:00 on 13 Nov; 20:30 on
  9 Nov gives 11 Nov (10 Nov a holiday); Saturday 14 Nov gives 16 Nov; 21:00 on 31 Dec 2026 gives 1 Jan 2027, flagged
  projected; `event_windows` puts day 0 where `decision_day` does.
- Forward supply: a T-bill week with no calendar has its plan and gaps blank and a note, beside 9,000 crore realised;
  with T-bill lines typed in, 14,000 crore planned as of 3 Apr 2025, the revised calendar's 8,000 from 4 Apr at 17:00
  (a gap of +1,000), and through `--calendar-lines` 9,500 against 9,000 (-500); a line with a bad time, source, tenor
  or release id is refused with its line number.
- `stock_on_dates` equals `outstanding_on` on eleven dates with and without a snapshot (forward, backward, forward from
  a snapshot, before issue, matured); the shares appear only with a complete history; forward supply excludes a
  calendar before its release (E5) and lets a revised one replace it; overlaps, siblings and isolation on events two
  and three business days apart; panels exact, nearest (same side of day 0 only), missing, settlement-dated and keyless.
- Determinism (E6): reruns and shuffled auction, ledger and store rows give the same bytes; `write_events` and
  `read_events` round-trip. The command line end to end: the files, the same bytes twice, exit 1 with nothing in the
  span, 2 on a missing file, a bad time or an unknown kind; with `--start`, E1 and E2 still clean on the whole inputs,
  an event's nearest neighbour dated before the start still found, a bill priced at a cut-off before it, forward
  supply's trailing DV01 from an auction before it; the store and an `rbi-releases` directory joined (a result only
  the directory holds added, a release both hold counted once). Refusals name the input.
- Eighteen defects planted one at a time in `event_ledger.py` (the end-of-day time, the announcement day, booked
  against accepted, the DV01's scale, day 0's decision time, the stock's cut-off, the switch-out sign, results shown
  early, a calendar before its release, siblings, the panel's side of day 0, the tail's sign, the join check, the
  forward bucket of a new issue, the minutes' day, a redemption's public time, the stock DV01's yield, shares on an
  incomplete stock) were each caught, as were seven planted in the command line (the identities, the stock's
  yields, forward supply and the windows read on the range only, the windows not cut back to it, the store's tables
  replaced by the directory's). Each test the review's fix pass added fails on the files as they were
  before it, except the day-0 test, which passed there and catches a change of the windows' day-0 rule planted in
  them.

**Free data** (scratch, not in the repository: the ledger build of 17 Nov 2026 and the RBI store of saved samples).
3,623 events (1,534 G-sec, 14 green and 107 FRB auction lines, 867 SDL, 6 T-bill, 952 redemptions, 51 calendar lines,
58 MoSPI releases, the MPC's 6 meetings, 7 OMO lines, 3 VRRRs, the 16 Nov switch) in 1.7 s, the command line in 15 s.
E1 held in all 1,137 settlement-month cells (the 204 month-kind cells of gross issuance included, 11,469,412 crore), E2
for all 2,471 auction ids; 108 ledger rows book other than the accepted amount (309,065 crore: 74 shortfalls taken as
devolved, 34 competitive-only amounts at the notified amount), 74 shortfalls imply 308,579 crore of devolvement (31 of
them in 2021). G-sec and green DV01 supply from April 2025 to June 2026: 61 auction weeks, 1,869,000 crore, 1,542.7
crore per bp, 25.29 a week on average (sd 4.77, 16.64 to 36.87); the 30-year-plus bucket was 22.3% of the face and
34.5% of the DV01. FRBs (394,568 crore) have no DV01. No stock cell is complete, so no share is shown. Bid-cover on 138
lines (median 2.60 on the Handbook's 91 G-sec lines of Aug 2025 to Jun 2026), tails on 44. The SDLs of 9 Nov 2026:
21 lines, 19 accepted, 29,050 crore notified, bid-cover 2.8526, median tail 2.43 bp; the specification's R11 figures
for "9 Nov" (43 lines, 52,350 crore, 3.1680) are the 9 and 17 Nov auctions together. Windows of five business days
each side for 2,688 events (929 skipped beyond 2026, 6 without a result); 2,131 isolated within 3 business days with
liquidity operations, macro releases and redemptions aside (the command line's default, ten days after, keeps 2,687).
The store with the build's `rbi-releases` directory, from 1 Jan 2026: 1,154 events written, E1 and E2 clean on the
whole inputs, the 22 SDL lines of 17 Nov 2026 present only as the store's results (public at 16:40 by its feed).
Rerun twice after the review's fixes (the command line's outputs the same bytes both times, 15 s a run): the same
3,623 events, E1 (0 of 6,822 cells) and E2 (0 of 2,471 auction ids) clean; 906 new-issue lines now carry coupon-free
ids; result flags on 356 rows and result notes on 121 moved out of `flags` and `notes`; the T-bill greenshoes of
11 Nov 2026 are 0 (they were 600, 1,350 and 11,200 crore: the non-competitive bids outside the notified amount); the
six OMO purchase lines of 13 Mar 2026 tail +0.56 to +2.49 bp (they were negative); 38 forward-supply rows of SDLs and
T-bills have their plan blank, not 0; seven SDL cells of the supply table differ in the 16th significant digit (summed
in the new ids' order), and 20 isolated events name another nearest event at the same offset (a tie, broken by the new
ids). No auction line of the free data is duplicated.

**Not checked:** any price response (concession, pre- and post-auction moves, event paths): no licensed panel exists;
a share of supply in a complete stock on real data; RBI's result times since 2020 for dated securities and SDLs;
announcement times of switches, buybacks and OMOs; T-bill, switch and buyback history before the store; the state
calendar by state; MPC meetings before FY2026-27, and the era times of MPC decisions against RBI's own releases; a
real T-bill calendar typed into `--calendar-lines`.

**Not built:** trading by participant category, an S4 need of [`rates_data_sources.md`](rates_data_sources.md)
(CCIL's licensed data: no input or column yet; once it arrives it would join the windows by date and security, as the
price panel does); a parser for RBI's T-bill calendar (its lines are typed into a table); the price responses on real
prices (built, waiting for a panel); a pricer for FRBs (no DV01); parsers for switch and buyback announcements; the
state calendar by state.

**Before real use.** The ledger runs on free data, but keeping that data is not settled: RBI grants no reuse licence
for its releases and FIMMDA states no terms for its auction files, so ask both before keeping a history
([`data_enquiries.md`](data_enquiries.md), letters 3 and 9). The event paths, concessions and residual moves need a
licensed price panel (CCIL's trades by security under a Data Agreement, or FBIL's valuation prices under its
subscription), and participant-category trading needs CCIL's data; see Licences below.

## Licences

FBIL's [Terms of Use](https://www.fbil.org.in/uploads/FBIL_Terms_of_Use_and_Subscription_Disclaimer_for_FBIL_Benchmarks_97d336023d.pdf)
(13 Oct 2026, clause 5.1) ask for a Derived Product License for any valuation model that uses FBIL benchmark
data: a curve fitted to FBIL prices, or one checked against FBIL's curves, is such a model on the clause's
words, beyond the subscription that storing or back-testing needs. FBIL's free data is seven days late and
for delayed, non-commercial viewing only (clause 9.1), so even daily use that stores nothing needs the
subscription. CCIL's
[terms](https://www.ccilindia.com/documents/d/ccil/terms-of-use) allow website data for non-commercial use
only; its trade history needs a paid Data Agreement. Settle both before any licensed file is fed through
`curve-fit` ([`rates_data_sources.md`](rates_data_sources.md), CCIL and FBIL).

The same clause covers an OIS curve bootstrapped from FBIL's MIBOR-OIS rates, and asset-swap spreads or a policy
path read off it; storing FBIL's overnight MIBOR or MIBOR-OIS, or back-testing on them, needs the subscription
(₹1 lakh a year per money-market benchmark for valuation only and ₹2.5 lakh for other uses, before GST, above a
₹500 crore turnover threshold, with the tier FBIL's to decide: [`rates_data_sources.md`](rates_data_sources.md),
FBIL). OIS beyond five years exists
only as CCIL's trade prints (a Data Agreement), broker screens or offshore prints. The repo and call rates come from
RBI, which grants no reuse licence (ask before keeping a history: `docs/data_enquiries.md`); CCIL's TREPS haircut
list is marked restricted, so haircuts are a table the user keeps, never shipped.

The return engine (part 3) needs the same licences before real prices are fed: marks from CCIL's trades (its Data
Agreement) or FBIL's valuation prices (the subscription, and the Derived Product License for a curve or model fitted to
them, which model marks and the curve lines use); FBIL's MIBOR and MIBOR-OIS for any OIS line or OIS reference curve
(the subscription; OIS beyond five years as above); RBI's money-market rates for funding (no reuse licence: ask before
keeping a history); and the haircut table and CCIL classes the user keeps. The event ledger (part 4) keeps RBI's
releases and FIMMDA's compiled auction files, for which FIMMDA states no terms: ask both before keeping a history
(`docs/data_enquiries.md`, letters 3 and 9). Its price paths, concessions and residual moves need a CCIL or FBIL price
panel under the licences of the curve fit, and trading by participant category CCIL's data.

## Open questions

- FBIL's spline: knots, weights, filters and the long-end bucket change of Jun-Jul 2025 (not readable here).
- CCIL's current filters and weights, and the compounding of its zero rates (continuous, inferred from one bond).
- The fitting universe in practice: the volume and maturity thresholds, whether to keep benchmarks in, how to
  weigh thinly traded bonds (Dutta, Basu and Vaidyanathan 2005 propose liquidity weights).
- Whether S3 should trade residuals from NSS or from the spline, and where the 3 bp model-risk line belongs,
  once a history of daily fits on traded prices exists.
- OIS: the schedule rules no source states (stubs, an end-of-month rule, whether accrual dates are adjusted
  onshore), whether CCIL's settlement rounds the compounded rate (the handbook's text and example disagree), FBIL's
  own statement of its quote basis, and when its 1-3 month rates began (its current methodology statement lists
  1 month to 5 years, an older version of it 6 months to 5 years; its listing holds a MIBOR-OIS methodology
  document "Version 3" dated 15 May 2023, a lead not read).
- Which asset-swap measure S1 trades on: yield-yield, par-par, proceeds and z-spreads differ by up to 2 bp on one
  bond (33.9 to 35.9 bp for 6.36% GS 2031 in the tests).
- Funding: the market repo day count in RBI's or CCIL's own words; CROMS haircuts; whether the fund can deal in
  TREPS itself (CCIL's net-worth bar) or only as a constituent, and at what spread; how large quarter-end and
  31 March spikes are (no stored quarter-end yet); whether repo interest is paid out or rolled.
- The policy path: the effective day of a decision for MIBOR, the MIBOR-repo spread's model (a median of 20 days,
  no term structure; the previous regime's just after a change), the penalty, and the term premium in OIS beyond
  six months; the February 2027 MPC date in RBI's own words and FY2027-28's dates; how FBIL's whole-session average
  should be read on a decision day.
- Whether the fund may pay or receive OIS at all, and for what purpose (hedging its bonds or taking a view), under
  RBI's directions on rupee interest rate derivatives for a user of its kind (the "OIS purpose" item of
  [`rates_data_sources.md`](rates_data_sources.md), The firm's own access): S1 and S7 rest on it.
- Which residual S3 and S5 trade (in the fit or out of sample) and which asset-swap measure S1 trades; how CCIL
  applies its TREPS caps to a member's mix of securities, what its rule was before 2 Jun 2026 and from when, and
  what the fund's constituent spread is.
- The return engine's conventions no source settles (each UNVERIFIED in the code): the book value convention, the
  constant-curve roll, the factor basis and nodes, the missing-mark rules and the five-day warning, the cash-need
  sizing and TREPS-first split, the decision and mark times, next-day VWAP execution and the five-day wait, the
  hedging tolerance of 105% and the 1% within which swaps offset each other, the funding rate over a Saturday money
  markets dealt on (the deal day's rate by default, or each calendar day's), the share of an auction a competitive
  order is allotted (10% of the amount accepted), the ranking of ISIN and venue cost exceptions before dates.
- Costs: the Indian Stamp Act's Schedule I as amended (India Code returned errors): its rates, the base for
  derivatives and who pays on OTC corporate bond trades; CCIL's current charges (Rs 10 or Rs 12.50 a repo leg) and any
  clearing member's mark-up; bid-offer for SDLs, bills, STRIPS, CP, CD and OIS (no public source); the fund's custody,
  broker and TREPS terms; whether a constituent posts the TREPS margin and in what form; GST on CCIL charges passed
  through a member; who pays stamp duty on CP and CD (FIMMDA's CP guidelines name the transferor, NSE's investor page
  through a search summary the buyer on exchange-settled trades; no source read names the CD payer, so the fund is
  charged on every CD trade).
- Which access preset is the fund's (its legal form decides S1's repo, S3 and S5's shorts and S7's OIS purpose), and
  whether its OIS with a bank would be cleared, with what margin and CSA.
- The event ledger's times no source settles: RBI's result times since January 2020 for dated securities and SDLs
  (and when the 2016 fixed times began), the announcement leads of switches, buybacks and OMOs, the publication date of
  the April-September issuance calendar, the MPC minutes' time, the MPC's announcement time from December 2019 to 2023
  (taken as 14:30) and the exact days the time changed (October 2016, April 2019 and 2024 come from search summaries);
  MPC decisions before FY2026-27 (a meetings file of the user's own, with a `decision_time` column where RBI's release
  gives one); whether a 20:00 decision time suits S4 (most results come out in the afternoon); whether to parse RBI's
  T-bill calendar into the store rather than type its lines in.
- The event ledger's readings: bid-cover on competitive bids over the notified amount (or all bids), the tail on yields,
  a switch's source leg at its last known yield, the stock's DV01 at the last cut-off, the 13-week DV01 per crore for
  planned supply; FRBs' DV01 (a floating-rate pricer); T-bill, switch and buyback history before the RBI store and a
  complete stock history (the ledger's gaps), without which no share of supply in the stock can be shown.
- Corporate bonds and FRBs (each UNVERIFIED in the code): whether CCIL's TREPS takes GoI FRBs as collateral (the engine
  takes them as ineligible, the conservative reading; `funding.treps_class`); corporate bond bid-offer by rating bucket
  (the specification's guesses, no public source); the exchange fee's start date and whether NSE's or BSE's schedule
  applies; the matched OIS rate restated to a corporate bond's annual basis and the amortising swap notional in its
  asset swaps; the 1 bp tolerance of the trade check, and whether the exchanges' reported yields follow one reading
  (which the check, run on real prints, would show); whether a GoI FRB should carry a default reference curve for
  attribution (none: its price move stays unattributed).
