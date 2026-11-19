# Analytics: fitted curves, OIS, asset swaps, funding and the policy path

Two parts, as of 19 Nov 2026:

- **[Curve fitting and rich/cheap](#curve-fitting-and-richcheap)**:
  what [`curvefit.py`](../src/cr_strat/curvefit.py) and `cr-strat curve-fit` do for strategies S3 (rich/cheap to
  a fitted G-sec curve) and S5 (the long end).
- **[OIS curves, asset swaps, funding and the policy path](#ois-curves-asset-swaps-funding-and-the-policy-path)**:
  [`ois.py`](../src/cr_strat/ois.py), [`asset_swaps.py`](../src/cr_strat/asset_swaps.py),
  [`funding.py`](../src/cr_strat/funding.py) and [`policy_path.py`](../src/cr_strat/policy_path.py), with
  `cr-strat ois-curve`, `asset-swaps`, `repo-carry` and `policy-path`, for S1 (G-secs against MIBOR-OIS, funded in
  repo) and S7 (the policy path against OIS).

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

**Not in any carry:** bid-offer; CCIL's settlement charges (₹95 per crore of face for outright dated securities,
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
