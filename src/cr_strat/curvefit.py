"""Zero curves fitted to G-sec and T-bill prices, and each bond's rich/cheap residual (strategies S3 and S5).

``fit_curve(frame, settlement)`` fits a discount curve to one settlement date's cross-section of bonds
and bills and measures every row of ``frame`` against it. A bond's residual is reported in basis points
of yield, ``resid_yield_bp = 100 (y_market - y_model)`` (positive: the bond is cheap, its yield above the
curve's), and in price per 100 face, ``resid_price = P_market - P_model`` (positive: rich). Both yields
are ``bonds``' yields to maturity on the bond's own conventions, one solved from the market price and
one from the model price. Every cash flow, accrued interest, yield and duration comes from ``bonds``: a
G-sec, sovereign green bond or special security is a ``FixedRateBond`` on ``GSEC``, an SDL one on
``SDL``, a STRIP a ``ZeroCouponBond`` and a T-bill or CMB a ``DiscountInstrument`` whose single flow of
100 falls on its maturity. Nothing here builds a cash flow of its own. Floating-rate and inflation-indexed
bonds have no pricer in ``bonds`` and are reported without residuals. Any other ``bonds.Bond`` (a
``corporates.CorporateBond``, a ``floating.GoiFloatingRateBond``) is priced on a fitted curve by its own
``cash_flows`` (``bond_price``, ``bond_yield``): corporate bonds are measured against the curve in
``credit_spreads``, never fitted.

**Curves.** ``DF(t) = exp(-z(t) t / 100)`` with ``t`` in Act/365F years from the settlement date to each
flow's scheduled date (``flow_dates="payment"`` uses the holiday-adjusted dates; that needs a calendar
covering every date) and ``z`` the continuously compounded zero rate in percent. Each curve gives
discount factors, zero rates (continuous, semi-annual or annual), instantaneous forward rates and par
yields at any time, and prices any bond of ``bonds`` (``bond_price``, ``bond_yield``, ``bond_par_yield``).
Three families:

* ``nss`` (default): Nelson-Siegel-Svensson, ``z(t) = b0 + b1 (1 - e^-x1)/x1 + b2 ((1 - e^-x1)/x1 - e^-x1)
  + b3 ((1 - e^-x2)/x2 - e^-x2)``, ``x = t / tau``, so ``z(0+) = b0 + b1`` and ``z(inf) = b0``. Bounds:
  ``0 <= b0 <= 30``, ``0 <= b0 + b1 <= 30``, ``|b2|, |b3| <= 60``, ``0.05 <= tau1 <= 30``,
  ``0.05 <= tau2 <= 100`` and ``tau2 >= 1.2 tau1`` (``tau_ratio``), which keeps the two humps apart.
  Fitted by Levenberg-Marquardt with an analytic Jacobian, Marquardt's diagonal scaling, steps projected
  onto the bounds and parameters at a bound held there while the step points outward. Starts: every pair
  of ``tau1_grid`` and ``tau2_grid`` with ``tau2 >= 1.2 tau1`` (22 with the default grids) plus
  ``warm_start``, each with betas from a least-squares fit of the bonds' continuously compounded yields at
  their durations; the lowest objective wins, ties to the first start. Each start stops when the objective
  falls by less than 1e-12 relative or the step by less than 1e-10, or after ``max_iter`` (500) iterations.
  ``n_at_best`` counts the starts that reached the best objective (within 1e-9 relative, or an RMS
  difference of 1e-4 bp). NSS is the form CCIL publishes for the Indian sovereign curve (since 1 Sep 2009,
  Nelson-Siegel before; CCIL's methodology pages were read only through search summaries) and the ECB's for
  the euro area (Svensson, continuous compounding, yield errors: ECB data portal series
  ``YC.B.U2.EUR.4F.G_N_A.SV_C_YM``). Six parameters cannot follow every bond, but they do lean towards each
  one: a bond's in-fit residual is about ``1 - h`` of its out-of-sample one, ``h`` its leverage
  (``leverage``), small in the dense middle of the curve (about 0.1: a bond 5 bp cheap shows 4.5 bp) and
  large at the ends (0.3 to 1: the shortest bond of a set without bills shows almost none of its richness).
  Read end bonds on ``loo_resid_yield_bp``, where bills, an anchor or a close neighbour still pin the refit near
  them: the very shortest and longest bonds' out-of-sample residuals are extrapolations (Diagnostics). Beyond the
  data the curve tends to ``b0``, so the long end past the longest bond rests on one parameter, which can sit at
  its bound (``params_at_bound``) with the forward rate still rising at 50 years: read the 30-50 year points only
  where ``in_data_range``. Parameters jump between days, so compare curves, not parameters.
* ``pspline`` (the cross-check): a penalised cubic B-spline of the instantaneous forward rate,
  ``f(t) = sum_k c_k B_k(t)``, on a clamped knot vector over ``[0, T]``, ``T`` the longest maturity in
  the fit, with ``knots`` interior knots at ``T (k / (knots + 1))^2`` (denser at the short end) or at
  maturity quantiles. ``ln DF(t) = -sum_k c_k I_k(t)`` with the exact antiderivative of each B-spline
  (de Boor: the integral of ``B_k`` is ``(u_{k+4} - u_k) / 4`` times the sum of the following quartic
  B-splines on the knot vector extended by one knot at each end). The penalty is
  ``lam x PENALTY_UNIT x ||D2 c||^2`` (second differences of the coefficients, after Eilers and Marx), with
  ``lam`` chosen over ``lambdas`` (1e2 to 1e8) by leave-one-out cross-validation from the leverages ``h_ii``
  of the linearised fit, ``CV = mean((e_i / (1 - h_ii))^2)``; ``lam_criterion="gcv"`` uses generalised
  cross-validation, ``GCV = n RSS / (n - sum h_ii)^2``, instead. Both are scored over the bonds only: an
  overnight anchor stays in the fit, but its own leave-one-out error, how well the bonds extrapolate to
  ``t = 0``, would otherwise dominate the score and push ``lam`` to the top of the grid. The curve-fitting
  spec proposed GCV over 1e-2 to 1e4: on synthetic and SDL cross-sections without bonds under four years it
  chose penalties so weak that the forward rate below the shortest bond ran to tens or hundreds of percent,
  because averaging the leverages hides the end bonds whose leverage is near one; CV with the grid starting
  at 1e2 did not (the runs are in ``docs/analytics.md``). Fitted by Gauss-Newton with step halving from a
  flat forward curve at the median yield. Beyond ``T`` the forward rate is held at ``f(T)``. This is the
  smoothing-spline family of Fisher, Nychka and Zervos (1995) and Waggoner (1997), and close to FBIL's own
  method (a cubic spline on NDS-OM trades; FBIL's knots, weights and filters were not read). Where NSS's six
  parameters miss real local shape, the spline shows it as a gap: ``compare_curves`` reports par-yield
  differences inside the joint data range and flags those above 3 bp. The spline also bends towards a single
  mispriced bond, most at the ends, so ``model_risk_flags`` judges each bond on the gap at its maturity
  between the two curves refitted without it (``loo_par_pct`` of both fits): above 3 bp its residual is
  model risk rather than rich/cheap. A large mispricing elsewhere still sways both refits, differently, and
  opens gaps at its neighbours: on a day with suspect bonds (out of sample above 10 bp and four robust
  standard deviations) such a flag is judged again on both curves fitted afresh without the bond and the
  suspects (``refit=refitter(...)``), and stands only if the gap persists. A bond nearly alone at an end of
  the curve (leverage near one) is flagged when the two methods go different ways without it, which is
  right: nothing else pins the curve there.
* ``flat``: one continuously compounded zero rate. A degenerate diagnostic for tiny cross-sections (one
  bond is enough); never a rich/cheap curve.

**Objective.** ``objective="yield"`` (default, the ECB's choice) minimises approximate yield errors,
``e_i = (P_model,i - P_mkt,i) / (P_mkt,i D_i)``, ``D_i`` the modified duration at the market price, so a
long bond weighs no more than a short one per basis point. ``"price_over_duration"`` is CCIL's reported
form, squared price errors weighted by ``1 / D_i``; ``"price"`` is unweighted price errors. Weights
multiply the squared errors: ``"equal"`` (default) or ``"liquidity"`` (``sqrt(volume_cr)``, capped at four
times the median), times an optional ``weight`` column, normalised to a mean of one over the bonds in the
fit. ``anchor_pct`` adds a pseudo-observation ``z(0+) = anchor`` (an overnight rate) weighing
``anchor_weight`` average bonds, for curves without short bonds; off by default, as T-bills anchor the
short end. The reported residuals are exact: yields from ``bonds`` at the market and model prices.

**Universe** (``select_universe``; every threshold UNVERIFIED as a fitting rule): rows of ``include_kinds``
(default dated G-secs, T-bills and CMBs) that traded, with a daily volume of at least ``min_volume_cr``
(rows without a volume are not filtered), at least ``min_years`` to maturity (bills: ``min_days_bills``
days) and none of ``exclude_flags`` (when-issued and odd-lot prints); ``drop_benchmarks`` takes flagged
benchmarks out. A row without ``traded``, ``trade_count`` or ``volume_cr`` counts as traded; when no row
has any of them the warnings say so (``no_trade_data``). A row of no known kind (``unknown``) stays out
(``no_kind``) and is named. The settlement date must be a Mumbai business day. SDLs are a curve of their
own (fit them with ``include_kinds=("sdl",)``); fitted with G-secs they would mix state spreads into the
curve. Excluded rows keep a reason and, when they can be priced, a residual against the curve: an SDL's
residual against the G-sec curve is its spread. A curve fitted to valuation prices (FBIL's or CCIL's)
recovers their model, not the market: fit traded prices.

**Outliers.** ``loss="huber"`` down-weights bonds by ``min(1, huber_bp / |e|)`` and refits until the
weights settle (iteratively reweighted least squares). ``outlier_bp`` turns on rejection: after each fit
the bond with the largest approximate yield error leaves the fit if that error exceeds both
``outlier_bp`` and ``outlier_mads`` robust standard deviations (1.4826 x the median absolute deviation),
at most ``outlier_rounds`` bonds and never below the method's minimum; the final data set gets a full
multi-start fit again. Both are off by default (plain least squares), but residuals above both 10 bp and
four robust standard deviations are always named in the warnings. Rejected bonds keep their residual,
with ``excluded_reason = "outlier"``; Huber weights are in ``robust_weight``.

**Diagnostics** (``FitResult.diagnostics``, JSON-ready): counts; RMSE, weighted RMSE, mean absolute and
largest residual in bp of yield and RMSE in price over the bonds in the fit; the five largest residuals;
objective, convergence, iterations and stop reason; for NSS the parameters, the objective of every start,
``n_at_best`` and the warnings ``params_at_bound`` and ``unstable_optimum`` (one start alone at the
optimum and the next best curve more than 1 bp away in par yield inside the data range); for the spline
``lam``, ``edf`` and the score of every ``lam`` (``lam_scores``); and, with ``leave_one_out`` (default),
each bond refitted out of the fit: its out-of-sample residual (``loo_resid_yield_bp``) and the largest move
of the par curve inside the refit's own data range when it is dropped (``loo_shift_bp``), with their
summary (also the RMSE without the two bonds at the ends of the range, whose refits extrapolate), and the
refit's par yield at the bond's maturity (``loo_par_pct``). Leave-one-out refits start from the full fit
(NSS) or keep its knots and ``lam`` (spline), so they are local: an NSS refit can stay in a basin that a mispriced
bond pulled the full fit into (a 5 bp cheap bond on an exact curve came out 4.76 to 5.18 bp out of sample, against
5.00 from a multi-start refit), and the shortest and longest bonds' out-of-sample residuals are extrapolations
(without bills or an anchor the spline put a 5 bp cheap shortest bond at 7.89 bp). Multi-start refits are not used:
they cost 20 to 80 times as much, and at an extrapolated end their optimum is no better (without the 1-year bond of
29, the curve holding a 20 bp outlier, a multi-start refit 0.2% lower in objective put that fair bond at -81.4 bp out
of sample, the warm refit at 2.0 bp). Each bond in the fit also gets its ``leverage``. ``curve_id``
is the first 16 hex digits of the SHA-256 of the settlement date, the settings and the bonds in the fit
(sorted); ``settings_id`` of the settings alone, with the day's market inputs among them (``PER_DAY_SETTINGS``: the
anchor's rate, that day's overnight rate) hashed by whether they are set, not by value, so the fits of a history
anchored at each day's own rate share one ``settings_id``.

**Rich/cheap.** ``residual_zscores`` gives each bond's residual a z-score against its own previous
``window`` observations (60, at least ``min_obs`` = 20, the current one excluded, ddof 1) and a robust
cross-sectional z within maturity buckets (``(r - median) / (1.4826 MAD)``); ``rich_cheap_table`` scores
one day's residuals against a history the caller passes (refusing history on or after that day, and
history fitted with another method or settings, ``settings_id``), on the in-fit residual or the
out-of-sample one (``column="loo_resid_yield_bp"``); ``append_history`` keeps that history day by day.
``bond_butterflies`` gives yield, model and residual flies of chosen triples with DV01-neutral wing
amounts (``F_wing = F_body DV01_body / (2 DV01_wing)``), ``bond_pairs`` the spread of a pair with a
DV01-neutral shorter leg (S5's 10-year against 30-50 years); ``curve_spreads`` and ``curve_butterflies`` read
the fitted par curve (2s10s, 10s30s, 10s50s, 2s5s10s, ...). Everything is keyed by ISIN.
``curve_points`` lays the curve on FBIL's grid of 0.25 to 50 years; ``benchmark_frame`` turns fitted curves
into the par-curve frame ``curves.BenchmarkCurve`` reads, every point on the semi-annual basis (below half
a year the simple par rate is restated), so a fitted curve can stand in for FBIL's.

**Drivers.** A residual still holds what the curve does not model (see the limits). ``residual_drivers`` joins
the ones free data give, as of each row's date: coupon, issue date and age and the FAR flag from the security
master (the flag is RBI's latest list, not point in time), the price file's benchmark flag, reissues and days
since the latest issue or reissue from the issuance ledger, outstanding stock from ``issuance.outstanding_on``.
``control_residuals`` regresses a day's residuals on chosen drivers by least squares and keeps what they do not
explain (``resid_ctrl_bp``; UNVERIFIED form, opt-in, read beside the raw residual).

**Determinism.** No randomness, no clock: rows are sorted by (maturity, ISIN) before anything is summed, so
the same inputs in any row order give byte-identical outputs.

**Sources** (C numbers of the curve-fitting spec of 19 Nov 2026; nothing is quoted): cash flows, 30E/360
accrual and semi-annual yields from ``bonds`` (C18); the T-bill price ``100 / (1 + y d / 36500)`` (FIMMDA
Handbook of Market Practices 2026, 6.7.1; C19); flow times on scheduled dates in Act/365F years (C20,
UNVERIFIED: no Indian source found, the standard choice); continuously compounded NSS zero rates (C21:
the ECB's series; CCIL's inferred from one bond, as CCIL's 13 Nov 2026 parameters price 6.94% GS 2036 at
7.3534% read continuously against the auction's 7.3203%); yield-error objective (C22); universe (C23).
Methods: Nelson and Siegel, J. Business 60(4), 1987; Svensson, NBER WP 4871, 1994; Eilers and Marx,
Statistical Science 11(2), 1996; Fisher, Nychka and Zervos, FEDS 1995-1; Waggoner, Atlanta Fed WP 97-10;
multi-start for NSS after Gilli, Grosse and Schumann (COMISEF WP 31, 2010). The papers were not re-read
for this module.

**Limits.** Tested on synthetic bonds priced off known curves and on free auction cut-offs only: no
secondary-market price, CCIL trade or FBIL file has been fitted, and no history of fits exists. Auction
cut-offs are primary, morning, auction-specific prices of a few lines a day: a curve through them is a
smoke test, not a rich/cheap signal. Outside the data range every curve is an extrapolation (flagged
``in_data_range``); NSS and the spline can differ there by tens of basis points. NSS has local minima (the
multi-start grid is a heuristic, not a proof of the global one) and cannot follow a kink; the spline's
penalty scale is a choice. Short ends without bills or an anchor are not identified. Rich/cheap residuals
still carry what the curve does not model: coupon level, liquidity, benchmark status, FAR eligibility and
index inclusion, outstanding stock, age and recent reissues, special repo demand. ``residual_drivers`` joins
coupon, age, FAR, benchmark, reissues and stock; liquidity beyond the price file's trade counts, special repo
and index inclusion are not joined (they need CCIL's trades and repo by security, licensed, and the index
providers' lists), and FAR status is not point in time until FAR lists are kept as dated snapshots.

**Licence.** FBIL's Terms of Use (13 Oct 2026, clause 5.1) ask for a Derived Product License for any
valuation model that uses FBIL benchmark data: a curve fitted to FBIL prices, or checked against FBIL's
curves, is such a model on the clause's words. CCIL's website data are for non-commercial use, and its
trade history needs a paid Data Agreement. Settle both before feeding either here
(``docs/rates_data_sources.md``).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date
from itertools import pairwise
from typing import Protocol, runtime_checkable

import numpy as np
import pandas as pd

from cr_strat.bonds import Bond, DiscountInstrument, FixedRateBond, ZeroCouponBond
from cr_strat.calendars import BusinessCalendar, CalendarCoverageError, calendar_for
from cr_strat.conventions import CMB, GSEC, SDL, STRIPS, TBILL, as_date

KINDS = ("gsec", "sdl", "tbill", "cmb", "strips", "frb", "iib", "green", "special", "unknown")
KIND_CONVENTIONS = {"gsec": GSEC, "green": GSEC, "special": GSEC, "sdl": SDL, "strips": STRIPS, "tbill": TBILL,
                    "cmb": CMB}
COUPON_KINDS = frozenset({"gsec", "sdl", "green", "special"})
BILL_KINDS = frozenset({"tbill", "cmb"})
NO_PRICER = frozenset({"frb", "iib"})  # floating and inflation-indexed coupons: no pricer in bonds.py
NO_KIND = "unknown"  # a row whose kind neither the price file nor the security master gives: kept out, with a reason
METHODS = ("nss", "pspline", "flat")
OBJECTIVES = ("yield", "price_over_duration", "price")
LOSSES = ("squared", "huber")
WEIGHT_SCHEMES = ("equal", "liquidity")
KNOT_SPACINGS = ("quadratic", "quantile")
LAM_CRITERIA = ("cv", "gcv")
FLOW_DATES = ("scheduled", "payment")
ZERO_BASES = ("continuous", "semiannual", "annual")
MIN_BONDS = {"nss": 8, "pspline": 6, "flat": 1}  # NSS: six parameters plus two (spline: UNVERIFIED choice)
MIN_MATURITIES = {"nss": 4, "pspline": 3, "flat": 1}
NSS_PARAMS = ("beta0", "beta1", "beta2", "beta3", "tau1", "tau2")
# Bounds in the fitted coordinates (beta0, beta0 + beta1, beta2, beta3, tau1, tau2), plus tau2 >= tau_ratio tau1.
NSS_LOWER = (0.0, 0.0, -60.0, -60.0, 0.05, 0.05)
NSS_UPPER = (30.0, 30.0, 60.0, 60.0, 30.0, 100.0)
FLAT_BOUNDS = (0.0, 30.0)
LIQUIDITY_CAP = 4.0  # liquidity weights capped at this many times their median
PENALTY_UNIT = 1e-8  # spline penalty lam x PENALTY_UNIT x ||D2 c||^2, c the forward coefficients as decimals
QUOTE_MISMATCH_BP = 1.0  # a row's price and yield disagreeing by more than this is named in the warnings
SUSPECT_BP, SUSPECT_SD = 10.0, 4.0  # residuals above both are named in the warnings
UNSTABLE_BP = 1.0
MODEL_RISK_BP = 3.0  # UNVERIFIED: spline-NSS par-yield gap above which residuals are model risk
ZSCORE_WINDOW, ZSCORE_MIN_OBS = 60, 20  # UNVERIFIED defaults
ZSCORE_BUCKETS = (0.0, 1.0, 3.0, 5.0, 10.0, 20.0, math.inf)  # UNVERIFIED: maturity buckets for cross-sectional z

FIT_COLUMNS = ("isin", "kind", "coupon_pct", "maturity_date", "issue_date", "first_coupon_date", "clean_price",
               "traded", "trade_count", "volume_cr", "flags")
OPTIONAL_COLUMNS = ("yield_pct", "weight", "settlement_date")
QUOTE_COLUMNS = ("clean_price", "yield_pct")
RESIDUAL_COLUMNS = ("date", "isin", "kind", "maturity_date", "years_to_maturity", "in_fit", "excluded_reason",
                    "weight", "market_clean", "market_dirty", "market_yield_pct", "model_dirty", "model_clean",
                    "model_yield_pct", "resid_price", "resid_yield_bp", "modified_duration", "dv01", "method",
                    "curve_id")
RESIDUAL_EXTRA_COLUMNS = ("coupon_pct", "quote", "flags", "robust_weight", "loo_resid_yield_bp", "loo_shift_bp",
                          "loo_par_pct", "leverage", "settings_id", "note")
CURVE_POINT_COLUMNS = ("date", "tenor_years", "zero_cc_pct", "zero_sa_pct", "forward_cc_pct", "par_sa_pct",
                       "discount", "in_data_range", "method", "curve_id")
COMPARISON_COLUMNS = ("date", "tenor_years", "primary_par_pct", "check_par_pct", "diff_bp", "in_joint_range",
                      "model_risk", "threshold_bp", "joint_from_years", "joint_to_years", "primary_method",
                      "check_method")
ZSCORE_COLUMNS = ("n_prior", "prior_mean", "prior_sd", "z_ts", "bucket", "z_cs")
BUTTERFLY_COLUMNS = ("date", "short_isin", "body_isin", "long_isin", "yield_fly_bp", "model_fly_bp",
                     "resid_fly_bp", "body_face", "short_face", "long_face")
PAIR_COLUMNS = ("date", "short_isin", "long_isin", "yield_spread_bp", "model_spread_bp", "resid_spread_bp", "long_face",
                "short_face")
DEFAULT_TENORS = tuple(k / 4 for k in range(1, 201))  # 0.25 to 50 years, FBIL's layout
DEFAULT_PAIRS = ((2.0, 10.0), (5.0, 10.0), (10.0, 30.0), (10.0, 40.0), (10.0, 50.0))
DEFAULT_FLIES = ((2.0, 5.0, 10.0), (5.0, 10.0, 30.0))
PRICE_ALIASES = {"price": "clean_price", "clean": "clean_price", "yield": "yield_pct", "ytm": "yield_pct",
                 "ytm_pct": "yield_pct", "coupon": "coupon_pct", "maturity": "maturity_date",
                 "volume": "volume_cr", "trades": "trade_count", "settlement": "settlement_date"}
MASTER_FIELDS = ("kind", "coupon_pct", "maturity_date", "issue_date")
# FitSettings fields that hold a day's market input rather than a method choice: settings_id hashes only whether each
# is set, so a history of fits anchored at each day's own overnight rate is one history
PER_DAY_SETTINGS = ("anchor_pct",)

_TRUE = {"true", "yes", "y", "1", "t"}
_FALSE = {"false", "no", "n", "0", "f"}


def _choice(value, allowed, name: str) -> None:
    if value not in allowed:
        raise ValueError(f"{name} must be one of {allowed}, not {value!r}")


def _positive(value, name: str, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be a number, not {value!r}")
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise TypeError(f"{name} must be a number, not {value!r}") from None
    if not (math.isfinite(out) and (out >= 0 if allow_zero else out > 0)):
        raise ValueError(f"{name} must be {'zero or more' if allow_zero else 'above zero'} and finite, not {value!r}")
    return out


def _count(value, name: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise TypeError(f"{name} must be an integer, not {value!r}")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, not {value}")
    return int(value)


# ---------------------------------------------------------------- settings


@dataclass(frozen=True, kw_only=True)
class FitSettings:
    """How a curve is fitted. Defaults are the standard or conservative reading; see the module docstring.

    ``method``: ``nss`` | ``pspline`` | ``flat``. ``objective``: ``yield`` | ``price_over_duration`` | ``price``.
    ``loss``: ``squared`` | ``huber`` (threshold ``huber_bp``). ``outlier_bp``: iterative rejection (``None``
    off), with ``outlier_mads`` and ``outlier_rounds``. ``weights``: ``equal`` | ``liquidity``. Universe:
    ``include_kinds``, ``exclude_flags``, ``drop_benchmarks``, ``min_years``, ``min_days_bills``,
    ``min_volume_cr`` (``None`` off). ``anchor_pct`` and ``anchor_weight``: overnight anchor of ``z(0+)``.
    NSS: ``tau1_grid``, ``tau2_grid``, ``tau_ratio``, ``max_iter``. Spline: ``knots``, ``knot_spacing``,
    ``lambdas`` searched by ``lam_criterion`` (``cv`` leave-one-out, ``gcv``) or a fixed ``lam``.
    ``flow_dates``: ``scheduled`` | ``payment``.
    ``leave_one_out``: refit without each bond for the stability diagnostics.
    """

    method: str = "nss"
    objective: str = "yield"
    loss: str = "squared"
    huber_bp: float = 5.0
    outlier_bp: float | None = None
    outlier_mads: float = 4.0
    outlier_rounds: int = 5
    weights: str = "equal"
    min_years: float = 0.25  # UNVERIFIED: dated bonds and STRIPS within 3 months of maturity stay out
    min_days_bills: int = 7  # UNVERIFIED: bills within a week of maturity stay out
    min_volume_cr: float | None = 5.0  # UNVERIFIED as a fitting threshold (CCIL's MTM rule uses Rs 5 crore trades)
    include_kinds: tuple[str, ...] = ("gsec", "tbill", "cmb")
    exclude_flags: tuple[str, ...] = ("when_issued", "odd_lot")
    drop_benchmarks: bool = False
    anchor_pct: float | None = None
    anchor_weight: float = 1.0
    tau1_grid: tuple[float, ...] = (0.3, 0.75, 1.5, 3.0, 6.0)
    tau2_grid: tuple[float, ...] = (2.0, 5.0, 10.0, 20.0, 40.0)
    tau_ratio: float = 1.2
    max_iter: int = 500
    knots: int = 12
    knot_spacing: str = "quadratic"
    lambdas: tuple[float, ...] = tuple(10.0 ** k for k in range(2, 9))  # 1e2 to 1e8
    lam: float | None = None
    lam_criterion: str = "cv"
    flow_dates: str = "scheduled"
    leave_one_out: bool = True

    def __post_init__(self) -> None:
        _choice(self.method, METHODS, "method")
        _choice(self.objective, OBJECTIVES, "objective")
        _choice(self.loss, LOSSES, "loss")
        _choice(self.weights, WEIGHT_SCHEMES, "weights")
        _choice(self.knot_spacing, KNOT_SPACINGS, "knot_spacing")
        _choice(self.lam_criterion, LAM_CRITERIA, "lam_criterion")
        _choice(self.flow_dates, FLOW_DATES, "flow_dates")

        def set_(name: str, value) -> None:
            object.__setattr__(self, name, value)

        set_("huber_bp", _positive(self.huber_bp, "huber_bp"))
        if self.outlier_bp is not None:
            set_("outlier_bp", _positive(self.outlier_bp, "outlier_bp"))
        set_("outlier_mads", _positive(self.outlier_mads, "outlier_mads"))
        set_("outlier_rounds", _count(self.outlier_rounds, "outlier_rounds", 0))
        set_("min_years", _positive(self.min_years, "min_years", allow_zero=True))
        set_("min_days_bills", _count(self.min_days_bills, "min_days_bills", 0))
        if self.min_volume_cr is not None:
            set_("min_volume_cr", _positive(self.min_volume_cr, "min_volume_cr", allow_zero=True))
        kinds = tuple(str(k).strip().lower() for k in self.include_kinds)
        for k in kinds:
            if k not in KIND_CONVENTIONS:
                raise ValueError(f"include_kinds: {k!r} cannot be fitted; kinds with a pricer are "
                                 f"{tuple(KIND_CONVENTIONS)}")
        if not kinds:
            raise ValueError("include_kinds is empty: nothing could be fitted")
        set_("include_kinds", kinds)
        set_("exclude_flags", tuple(str(f).strip().lower() for f in self.exclude_flags))
        if not isinstance(self.drop_benchmarks, bool) or not isinstance(self.leave_one_out, bool):
            raise TypeError("drop_benchmarks and leave_one_out must be True or False")
        if self.anchor_pct is not None:
            value = float(self.anchor_pct)
            if not math.isfinite(value):
                raise ValueError(f"anchor_pct must be a finite rate in percent, not {self.anchor_pct!r}")
            set_("anchor_pct", value)
        set_("anchor_weight", _positive(self.anchor_weight, "anchor_weight"))
        set_("tau_ratio", _positive(self.tau_ratio, "tau_ratio"))
        if self.tau_ratio < 1:
            raise ValueError(f"tau_ratio must be at least 1 (tau2 >= tau_ratio x tau1), not {self.tau_ratio}")
        for name, lo, hi in (("tau1_grid", NSS_LOWER[4], NSS_UPPER[4]), ("tau2_grid", NSS_LOWER[5], NSS_UPPER[5])):
            grid = tuple(_positive(v, name) for v in getattr(self, name))
            if not grid or any(not lo <= v <= hi for v in grid):
                raise ValueError(f"{name} must hold values between {lo} and {hi}, not {getattr(self, name)!r}")
            set_(name, grid)
        if not any(t2 >= self.tau_ratio * t1 for t1 in self.tau1_grid for t2 in self.tau2_grid):
            raise ValueError("no pair of tau1_grid and tau2_grid has tau2 >= tau_ratio x tau1: no NSS start")
        set_("max_iter", _count(self.max_iter, "max_iter", 1))
        set_("knots", _count(self.knots, "knots", 1))
        lambdas = tuple(_positive(v, "lambdas") for v in self.lambdas)
        if not lambdas:
            raise ValueError("lambdas is empty")
        set_("lambdas", lambdas)
        if self.lam is not None:
            set_("lam", _positive(self.lam, "lam"))


DEFAULT_SETTINGS = FitSettings()


# ---------------------------------------------------------------- curve formulas


def _times(t) -> tuple[np.ndarray, tuple[int, ...]]:
    """``t`` as a flat float array of years (>= 0, finite) and its original shape."""
    try:
        arr = np.asarray(t, dtype=float)
    except (TypeError, ValueError):
        raise TypeError(f"times must be numbers of years, not {t!r}") from None
    flat = arr.reshape(-1)
    if flat.size:
        bad = ~np.isfinite(flat)
        if bad.any():
            raise ValueError(f"times must be finite numbers of years, not {flat[bad][0]}")
        if flat.min() < 0:
            raise ValueError(f"times must be zero or more years after the valuation date, not {flat.min()}")
    return flat, arr.shape


def _phi(x: np.ndarray) -> np.ndarray:
    """``(1 - e^-x) / x``, 1 at ``x = 0``."""
    out = np.ones_like(x)
    pos = x > 0
    out[pos] = -np.expm1(-x[pos]) / x[pos]
    return out


def _check_nss(params) -> tuple[float, ...]:
    try:
        p = tuple(float(v) for v in params)
    except (TypeError, ValueError):
        raise TypeError(f"NSS parameters must be six numbers, not {params!r}") from None
    if len(p) != 6:
        raise ValueError(f"NSS needs six parameters {NSS_PARAMS}, not {len(p)}")
    if not all(math.isfinite(v) for v in p):
        raise ValueError(f"NSS parameters must be finite, not {p}")
    if p[4] <= 0 or p[5] <= 0:
        raise ValueError(f"tau1 and tau2 must be above zero, not {p[4]} and {p[5]}")
    return p


def nss_loadings(t, tau1: float, tau2: float) -> np.ndarray:
    """The four NSS loadings at ``t`` (years), shape ``t.shape + (4,)``: 1, phi(x1), phi(x1) - e^-x1,
    phi(x2) - e^-x2, with ``x = t / tau`` and ``phi(x) = (1 - e^-x) / x``."""
    flat, shape = _times(t)
    _check_nss((0, 0, 0, 0, tau1, tau2))
    x1, x2 = flat / tau1, flat / tau2
    p1, p2 = _phi(x1), _phi(x2)
    out = np.stack([np.ones_like(flat), p1, p1 - np.exp(-x1), p2 - np.exp(-x2)], axis=-1)
    return out.reshape(*shape, 4)


def nss_zero(t, params) -> np.ndarray:
    """NSS zero rate (percent, continuously compounded) at ``t`` years; ``params`` = (b0, b1, b2, b3, tau1, tau2)."""
    p = _check_nss(params)
    return nss_loadings(t, p[4], p[5]) @ np.array(p[:4])


def nss_forward(t, params) -> np.ndarray:
    """NSS instantaneous forward rate (percent, continuous): ``b0 + b1 e^-x1 + b2 x1 e^-x1 + b3 x2 e^-x2``."""
    p = _check_nss(params)
    flat, shape = _times(t)
    x1, x2 = flat / p[4], flat / p[5]
    f = p[0] + p[1] * np.exp(-x1) + p[2] * x1 * np.exp(-x1) + p[3] * x2 * np.exp(-x2)
    return f.reshape(shape)


def _check_knots(knots, degree: int) -> np.ndarray:
    u = np.asarray(knots, dtype=float)
    if u.ndim != 1 or u.size < 2 * (degree + 1) or not np.all(np.isfinite(u)):
        raise ValueError(f"a degree-{degree} knot vector needs at least {2 * (degree + 1)} finite knots")
    if np.any(np.diff(u) < 0) or not u[-1] > u[0]:
        raise ValueError("knots must be non-decreasing and span a range")
    return u


def bspline_basis(t, knots, degree: int = 3) -> np.ndarray:
    """Every B-spline of ``degree`` on ``knots`` at ``t``, shape ``(len(t), len(knots) - degree - 1)`` (Cox-de Boor).

    ``t`` is clipped to ``[knots[0], knots[-1]]``; the right end belongs to the last non-empty knot interval, so
    on a clamped knot vector the basis sums to one on the whole closed range.
    """
    u = _check_knots(knots, degree)
    x = np.clip(np.asarray(t, dtype=float).reshape(-1), u[0], u[-1])
    intervals = len(u) - 1
    last = int(np.flatnonzero(u[1:] > u[:-1])[-1])
    idx = np.minimum(np.searchsorted(u, x, side="right") - 1, last)
    basis = np.zeros((x.size, intervals))
    basis[np.arange(x.size), idx] = 1.0
    for p in range(1, degree + 1):
        nxt = np.zeros((x.size, intervals - p))
        for i in range(intervals - p):
            left, right = u[i + p] - u[i], u[i + p + 1] - u[i + 1]
            term = np.zeros(x.size)
            if left > 0:
                term += (x - u[i]) / left * basis[:, i]
            if right > 0:
                term += (u[i + p + 1] - x) / right * basis[:, i + 1]
            nxt[:, i] = term
        basis = nxt
    return basis


def bspline_integral_basis(t, knots, degree: int = 3) -> np.ndarray:
    """``integral from knots[0] to t of B_k`` for every B-spline of ``degree`` on ``knots``, exactly.

    The integral of ``B_k`` is ``(u_{k+p+1} - u_k) / (p + 1)`` times the sum of the degree ``p + 1`` B-splines
    ``j > k`` on ``knots`` with its first and last knots repeated once more (de Boor). ``t`` beyond the last
    knot gives each B-spline's full integral.
    """
    u = _check_knots(knots, degree)
    n = len(u) - degree - 1
    extended = np.r_[u[0], u, u[-1]]
    higher = bspline_basis(t, extended, degree + 1)  # n + 1 columns
    tail = np.cumsum(higher[:, ::-1], axis=1)[:, ::-1]  # tail[:, j] = sum of columns j and beyond
    widths = (u[degree + 1:degree + 1 + n] - u[:n]) / (degree + 1)
    return tail[:, 1:n + 1] * widths


def _from_continuous(z: np.ndarray, basis: str) -> np.ndarray:
    """Decimal continuous zero rates to percent on ``basis``."""
    if basis == "continuous":
        return z * 100
    if basis == "semiannual":
        return 2 * np.expm1(z / 2) * 100
    if basis == "annual":
        return np.expm1(z) * 100
    raise ValueError(f"basis must be one of {ZERO_BASES}, not {basis!r}")


def _check_frequency(frequency) -> int:
    if isinstance(frequency, bool) or frequency not in (1, 2, 4, 12):
        raise ValueError(f"frequency must be 1, 2, 4 or 12 coupons a year, not {frequency!r}")
    return int(frequency)


def par_yields(curve, tenors, frequency: int = 2) -> np.ndarray:
    """Par yields (percent, ``frequency`` coupons a year) at ``tenors`` (Act/365F years from the curve's valuation
    date) of any curve with ``discount_t`` (an ``ois.OisCurve``, a curve seen from a later date): the formula of
    ``FittedCurve.par_yield``. NaN where the curve gives no discount factor."""
    m = _check_frequency(frequency)
    flat, shape = _times(tenors)
    if flat.size and flat.min() <= 0:
        raise ValueError(f"par yields need maturities above zero, not {flat.min()}")
    return _par_yields(curve, flat, m).reshape(shape)


def _par_yields(curve: DiscountCurveLike, tenors: np.ndarray, m: int) -> np.ndarray:
    """Par yields (percent, ``m`` coupons a year) at ``tenors``: ``100 (1 - DF(T)) / sum_k a_k DF(t_k)``."""
    if not tenors.size:
        return np.zeros(0)
    counts = np.maximum(np.ceil(tenors * m - 1e-9).astype(int), 1)
    owner = np.repeat(np.arange(tenors.size), counts)
    first = np.r_[0, np.cumsum(counts)[:-1]]
    k = np.arange(owner.size) - first[owner]  # 0 for the first (possibly short) period
    times = tenors[owner] - (counts[owner] - 1 - k) / m
    accrual = np.full(owner.size, 1.0 / m)
    accrual[first] = (tenors - (counts - 1) / m)  # fraction w of a period, as years
    df = np.asarray(curve.discount_t(times), dtype=float)
    annuity = np.add.reduceat(accrual * df, first)
    return (1 - df[first + counts - 1]) / annuity * 100


# ---------------------------------------------------------------- curves


@runtime_checkable
class DiscountCurveLike(Protocol):
    """What a discount curve offers (shared with the OIS module by structure, not by import)."""

    valuation_date: date
    t_max: float

    def discount_t(self, t) -> np.ndarray: ...  # Act/365F years from valuation_date

    def discount(self, when) -> np.ndarray: ...  # dates

    def zero_rate(self, t, basis: str = "continuous") -> np.ndarray: ...  # percent


@dataclass(frozen=True, kw_only=True)
class FittedCurve:
    """A discount curve on Act/365F years from ``valuation_date``; zero rates continuously compounded.

    ``t_min`` and ``t_max`` are the shortest and longest maturities (years) of the bonds it was fitted to:
    outside them it is an extrapolation (``in_data_range``). ``method`` and ``curve_id`` say how it was made.
    """

    valuation_date: date
    t_min: float
    t_max: float
    method: str
    curve_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "valuation_date", as_date(self.valuation_date))
        for name in ("t_min", "t_max"):
            object.__setattr__(self, name, _positive(getattr(self, name), name, allow_zero=True))
        if self.t_min > self.t_max:
            raise ValueError(f"t_min {self.t_min} is above t_max {self.t_max}")

    def _log_df(self, t: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    def _forward_dec(self, t: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    def discount_t(self, t) -> np.ndarray:
        """Discount factor ``t`` years (Act/365F) after the valuation date."""
        flat, shape = _times(t)
        return np.exp(self._log_df(flat)).reshape(shape)

    def discount(self, when) -> np.ndarray:
        """Discount factor on a date (or each of a sequence of dates), at ``(date - valuation_date) / 365`` years."""
        scalar = isinstance(when, (str, date, np.datetime64))
        days = []
        for item in ([when] if scalar else list(when)):
            d = as_date(item)
            if d < self.valuation_date:
                raise ValueError(f"{d} is before the curve's valuation date {self.valuation_date}")
            days.append((d - self.valuation_date).days)
        out = self.discount_t(np.array(days, dtype=float) / 365)
        return out.reshape(()) if scalar else out

    def zero_rate(self, t, basis: str = "continuous") -> np.ndarray:
        """Zero rate (percent) at ``t`` years, compounded on ``basis``; at ``t = 0`` the limit ``f(0)``."""
        _choice(basis, ZERO_BASES, "basis")
        flat, shape = _times(t)
        z = np.empty_like(flat)
        pos = flat > 0
        z[pos] = -self._log_df(flat[pos]) / flat[pos]
        if (~pos).any():
            z[~pos] = self._forward_dec(np.zeros(int((~pos).sum())))
        return _from_continuous(z, basis).reshape(shape)

    def forward_rate(self, t) -> np.ndarray:
        """Instantaneous forward rate at ``t`` years (percent, continuous): ``-d ln DF / dt``."""
        flat, shape = _times(t)
        return (self._forward_dec(flat) * 100).reshape(shape)

    def par_yield(self, t, frequency: int = 2) -> np.ndarray:
        """Par yield (percent, ``frequency`` coupons a year) of a bond issued now at par and maturing at ``t``.

        Coupons fall every ``1/frequency`` years counted back from ``t``; a short first period pays its share.
        ``y = 100 (1 - DF(t)) / sum_k a_k DF(t_k)``, ``a_k = 1/frequency`` (the first: its length in years); on
        whole periods that is ``200 (1 - DF(T)) / sum_{k=1..2T} DF(k/2)``.
        """
        m = _check_frequency(frequency)
        flat, shape = _times(t)
        if flat.size and flat.min() <= 0:
            raise ValueError(f"par yields need maturities above zero, not {flat.min()}")
        return _par_yields(self, flat, m).reshape(shape)

    def in_data_range(self, t) -> np.ndarray:
        """True where ``t`` lies between the shortest and longest maturity the curve was fitted to."""
        flat, shape = _times(t)
        return ((flat >= self.t_min - 1e-9) & (flat <= self.t_max + 1e-9)).reshape(shape)

    def _settlement(self, settlement) -> date:
        s = self.valuation_date if settlement is None else as_date(settlement)
        if s != self.valuation_date:
            raise ValueError(f"the curve is for settlement on {self.valuation_date}, not {s}")
        return s

    def bond_price(self, bond, settlement=None, *, flow_dates: str = "scheduled") -> tuple[float, float]:
        """(dirty, clean) price per 100 of a ``bonds`` instrument: its cash flows discounted on this curve."""
        s = self._settlement(settlement)
        times, amounts = _instrument_flows(bond, s, flow_dates)
        dirty = float(amounts @ self.discount_t(times))
        return dirty, dirty - _accrued(bond, s)

    def bond_yield(self, bond, settlement=None) -> float:
        """The bond's yield (percent, on its own conventions in ``bonds``) at its price on this curve."""
        s = self._settlement(settlement)
        return _yield_from_dirty(bond, self.bond_price(bond, s)[0], s)

    def bond_par_yield(self, bond, settlement=None) -> float:
        """Yield (``bonds``, the bond's own basis) of ``bond`` with the coupon that makes it worth 100 clean here.

        The coupon enters linearly: ``c = (100 - PV(redemption)) / (PV(unit coupons) - unit accrued)``; on a
        coupon date the yield equals ``c``. STRIPS, T-bills and CMBs have no coupon: their model yield.
        """
        s = self._settlement(settlement)
        if not isinstance(bond, FixedRateBond):
            return self.bond_yield(bond, s)
        pv0 = self.bond_price(replace(bond, coupon_pct=0.0), s)[0]
        unit = replace(bond, coupon_pct=1.0)
        pv1 = self.bond_price(unit, s)[0] - pv0
        denom = pv1 - unit.accrued_interest(s)
        if not denom > 0:
            raise ValueError(f"no par coupon for a bond maturing {bond.maturity}: its coupons are worth nothing")
        return replace(bond, coupon_pct=(100 - pv0) / denom).yield_from_price(100.0, s)


@dataclass(frozen=True, kw_only=True)
class NssCurve(FittedCurve):
    """Nelson-Siegel-Svensson curve; ``params`` = (b0, b1, b2, b3, tau1, tau2), rates in percent."""

    params: tuple[float, ...]

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(self, "params", _check_nss(self.params))

    def _log_df(self, t: np.ndarray) -> np.ndarray:
        return -nss_zero(t, self.params) * t / 100

    def _forward_dec(self, t: np.ndarray) -> np.ndarray:
        return nss_forward(t, self.params) / 100

    @property
    def param_dict(self) -> dict[str, float]:
        return dict(zip(NSS_PARAMS, self.params))


@dataclass(frozen=True, kw_only=True)
class SplineCurve(FittedCurve):
    """Cubic B-spline forward curve: ``f(t) = sum c_k B_k(t)`` (``coefs`` as decimals) on clamped ``knots``.

    ``ln DF`` is the exact integral of ``f``; beyond the last knot the forward rate stays at ``f(T)``.
    ``lam`` and ``edf``: the penalty used and the effective degrees of freedom of the fit.
    """

    knots: tuple[float, ...]
    coefs: tuple[float, ...]
    lam: float = math.nan
    edf: float = math.nan

    def __post_init__(self) -> None:
        super().__post_init__()
        u = _check_knots(self.knots, 3)
        c = np.asarray(self.coefs, dtype=float)
        if c.shape != (len(u) - 4,) or not np.all(np.isfinite(c)):
            raise ValueError(f"{len(u)} knots need {len(u) - 4} finite coefficients, not {c.size}")
        if not (u[0] == 0 and np.all(u[:4] == u[0]) and np.all(u[-4:] == u[-1])):
            raise ValueError("spline knots must be clamped (four equal knots at each end), starting at 0")
        object.__setattr__(self, "knots", tuple(float(v) for v in u))
        object.__setattr__(self, "coefs", tuple(float(v) for v in c))
        object.__setattr__(self, "_u", u)
        object.__setattr__(self, "_c", c)
        object.__setattr__(self, "_end", float(u[-1]))
        object.__setattr__(self, "_log_end", float(-(bspline_integral_basis([u[-1]], u) @ c)[0]))

    def _log_df(self, t: np.ndarray) -> np.ndarray:
        out = np.empty_like(t)
        inside = t <= self._end
        out[inside] = -(bspline_integral_basis(t[inside], self._u) @ self._c)
        out[~inside] = self._log_end - self._c[-1] * (t[~inside] - self._end)
        return out

    def _forward_dec(self, t: np.ndarray) -> np.ndarray:
        out = np.full(t.shape, self._c[-1])
        inside = t <= self._end
        out[inside] = bspline_basis(t[inside], self._u) @ self._c
        return out


# ---------------------------------------------------------------- inputs and cash flows


def _blank(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _text(value) -> str:
    return "" if _blank(value) else str(value).strip()


def _opt_float(value, where: str, column: str) -> float | None:
    if _blank(value):
        return None
    if isinstance(value, bool):
        raise TypeError(f"{where}: {column} {value!r} is not a number")
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{where}: {column} {value!r} is not a number") from None
    if not math.isfinite(out):
        raise ValueError(f"{where}: {column} must be finite, not {value!r}")
    return out


def _opt_date(value, where: str, column: str) -> date | None:
    if _blank(value):
        return None
    try:
        return as_date(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{where}: {column} {value!r} is not a date ({exc})") from None


def _opt_bool(value, where: str, column: str, default: bool) -> bool:
    if _blank(value):
        return default
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    text = str(value).strip().lower()
    if isinstance(value, (int, float, np.integer, np.floating)) and float(value) in (0.0, 1.0):
        return bool(value)
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ValueError(f"{where}: {column} {value!r} is not true or false")


def _flags(value) -> str:
    parts = {p.strip().lower() for p in re.split(r"[;,|]", _text(value))}
    return ";".join(sorted(p for p in parts if p))


def _prepare(frame: pd.DataFrame, settlement) -> tuple[pd.DataFrame, date]:
    """Typed copy of a fit frame, one row per ISIN, sorted by (maturity_date, isin)."""
    if not isinstance(frame, pd.DataFrame):
        raise TypeError(f"expected a DataFrame of bonds, not {type(frame).__name__}")
    s = as_date(settlement)
    df = frame.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    missing = [c for c in ("isin", "kind", "maturity_date") if c not in df]
    if missing:
        raise ValueError(f"the bond frame is missing columns {missing} (expected {FIT_COLUMNS})")
    if not any(c in df for c in QUOTE_COLUMNS):
        raise ValueError("the bond frame needs a clean_price or a yield_pct column")
    if df.empty:
        raise ValueError("the bond frame has no rows")
    records = []
    for pos, rec in enumerate(df.to_dict("records")):
        isin = _text(rec.get("isin"))
        if not isin:
            raise ValueError(f"row {pos} has no isin")
        where = f"ISIN {isin}"
        kind = _text(rec.get("kind")).lower()
        if kind not in KINDS:
            raise ValueError(f"{where}: kind {rec.get('kind')!r} is not one of {KINDS}")
        maturity = _opt_date(rec.get("maturity_date"), where, "maturity_date")
        if maturity is None and kind not in NO_PRICER and kind != NO_KIND:
            raise ValueError(f"{where}: maturity_date is missing")
        coupon = _opt_float(rec.get("coupon_pct"), where, "coupon_pct")
        if kind in COUPON_KINDS and coupon is None:
            raise ValueError(f"{where}: coupon_pct is missing for a {kind}")
        settle = _opt_date(rec.get("settlement_date"), where, "settlement_date")
        if settle is not None and settle != s:
            raise ValueError(f"{where}: settlement_date {settle} differs from the fit's settlement {s}; "
                             "fit one settlement date at a time")
        weight = _opt_float(rec.get("weight"), where, "weight")
        if weight is not None and weight < 0:
            raise ValueError(f"{where}: weight must be zero or more, not {weight}")
        volume = _opt_float(rec.get("volume_cr"), where, "volume_cr")
        if volume is not None and volume < 0:
            raise ValueError(f"{where}: volume_cr must be zero or more, not {volume}")
        def nan(v):
            return math.nan if v is None else v

        out = {
            "isin": isin, "kind": kind, "coupon_pct": nan(coupon), "maturity_date": maturity,
            "issue_date": _opt_date(rec.get("issue_date"), where, "issue_date"),
            "first_coupon_date": _opt_date(rec.get("first_coupon_date"), where, "first_coupon_date"),
            "clean_price": nan(_opt_float(rec.get("clean_price"), where, "clean_price")),
            "yield_pct": nan(_opt_float(rec.get("yield_pct"), where, "yield_pct")),
            "traded": _opt_bool(rec.get("traded"), where, "traded", True),
            "trade_count": nan(_opt_float(rec.get("trade_count"), where, "trade_count")),
            "volume_cr": nan(volume), "flags": _flags(rec.get("flags")), "weight": 1.0 if weight is None else weight,
            "trade_info": any(not _blank(rec.get(c)) for c in ("traded", "trade_count", "volume_cr")),
        }
        if "in_fit" in df:
            out["in_fit"] = _opt_bool(rec.get("in_fit"), where, "in_fit", False)
            out["excluded_reason"] = _text(rec.get("excluded_reason"))
        records.append(out)
    rows = pd.DataFrame(records)
    dup = rows["isin"][rows["isin"].duplicated()].unique()
    if len(dup):
        raise ValueError(f"duplicate ISINs in the bond frame: {sorted(dup)[:10]}")
    order = sorted(range(len(rows)), key=lambda i: (rows["maturity_date"].iat[i] is None,
                                                    rows["maturity_date"].iat[i] or date.min, rows["isin"].iat[i]))
    return rows.iloc[order].reset_index(drop=True), s


def _instrument(rec: dict, calendar: BusinessCalendar | None):
    conv = KIND_CONVENTIONS[rec["kind"]]
    if rec["kind"] in COUPON_KINDS:
        return FixedRateBond(rec["coupon_pct"], rec["maturity_date"], conv, issue_date=rec["issue_date"],
                             first_coupon_date=rec["first_coupon_date"], calendar=calendar)
    if rec["kind"] == "strips":
        return ZeroCouponBond(rec["maturity_date"], conv, issue_date=rec["issue_date"], calendar=calendar)
    return DiscountInstrument(rec["maturity_date"], conv, issue_date=rec["issue_date"], calendar=calendar)


def _instrument_flows(inst, s: date, flow_dates: str = "scheduled") -> tuple[np.ndarray, np.ndarray]:
    """Act/365F times and amounts (per 100) of a ``bonds`` instrument's flows after ``s``."""
    _choice(flow_dates, FLOW_DATES, "flow_dates")
    if flow_dates == "payment" and getattr(inst, "calendar", None) is None:
        raise ValueError("flow_dates='payment' needs the instrument to carry a calendar")
    if isinstance(inst, DiscountInstrument):
        inst.days_to_maturity(s)  # refuses a settlement on or after maturity or before issue
        when = inst.payment_date() if flow_dates == "payment" else inst.maturity
        times, amounts = np.array([(when - s).days / 365]), np.array([100.0])
    elif isinstance(inst, Bond):  # G-secs, SDLs, STRIPS and any other Bond (corporates, floating): by cash_flows
        flows = inst.cash_flows(s)
        when = [f.payment_date if flow_dates == "payment" else f.date for f in flows]
        times = np.array([(d - s).days / 365 for d in when])
        amounts = np.array([f.amount for f in flows], dtype=float)
    else:
        raise TypeError(f"not a bonds instrument: {inst!r}")
    if not times.size or times.min() <= 0:
        raise ValueError(f"a payment falls on or before settlement {s}")
    return times, amounts


def _accrued(inst, s: date) -> float:
    return 0.0 if isinstance(inst, DiscountInstrument) else inst.accrued_interest(s)


def _yield_from_dirty(inst, dirty: float, s: date) -> float:
    if isinstance(inst, DiscountInstrument):
        return inst.yield_from_price(dirty, s)
    return inst.yield_from_dirty_price(dirty, s)


def _market(inst, s: date, clean: float | None, yld: float | None) -> dict:
    """Market price, yield and risk from a clean price (preferred) or a yield, all through ``bonds``."""
    if isinstance(inst, DiscountInstrument):
        if clean is not None:
            price, y = clean, inst.yield_from_price(clean, s)
        else:
            y = yld
            price = inst.price(y, s, rounded=False)
        days = inst.days_to_maturity(s)
        return {"market_clean": price, "market_dirty": price, "accrued": 0.0, "market_yield_pct": y,
                "modified_duration": inst.modified_duration(y, s), "macaulay": days / 365,
                "dv01": inst.dv01(y, s)}
    a = inst.analytics(s, clean_price=clean) if clean is not None else inst.analytics(s, yield_pct=yld)
    return {"market_clean": a.clean_price, "market_dirty": a.dirty_price, "accrued": a.accrued_interest,
            "market_yield_pct": a.yield_pct, "modified_duration": a.modified_duration,
            "macaulay": a.macaulay_duration, "dv01": a.dv01}


@dataclass(frozen=True, eq=False)
class FlowTable:
    """Bonds of one settlement date with their ``bonds`` instruments, market data and cash flows.

    ``rows``: one row per input row, sorted by (maturity_date, isin), typed, with ``years_to_maturity``,
    ``priced`` (a usable market quote), ``quote``, the market columns and ``note`` (why a row has no flows or
    no market data). ``times``, ``amounts`` and ``owner`` (row position) list the flows of every row that
    could be built, grouped by row in order.
    """

    settlement: date
    rows: pd.DataFrame
    instruments: tuple
    times: np.ndarray
    amounts: np.ndarray
    owner: np.ndarray
    flow_dates: str = "scheduled"

    @property
    def market_dirty(self) -> np.ndarray:
        return self.rows["market_dirty"].to_numpy(dtype=float)

    @property
    def modified_duration(self) -> np.ndarray:
        return self.rows["modified_duration"].to_numpy(dtype=float)


_MARKET_COLUMNS = ("market_clean", "market_dirty", "accrued", "market_yield_pct", "modified_duration", "macaulay",
                   "dv01")


def flow_table(frame: pd.DataFrame, settlement, *, calendar: BusinessCalendar | None = None,
               flow_dates: str = "scheduled") -> FlowTable:
    """Instruments, market prices and cash flows for every row of a bond frame (``FIT_COLUMNS``).

    Each row needs ``isin``, ``kind`` and ``maturity_date``, a ``coupon_pct`` for coupon bonds, and a
    ``clean_price`` or a ``yield_pct`` to have market data (the price wins when both are given; a yield more
    than ``QUOTE_MISMATCH_BP`` away from the price's is noted). Rows that cannot be priced (floating-rate or
    inflation-indexed bonds, matured rows, a bad description or quote) keep a ``note`` instead of raising;
    duplicate ISINs, unknown kinds, unreadable cells and a ``settlement_date`` other than ``settlement``
    raise ``ValueError``. ``flow_dates="payment"`` discounts holiday-adjusted payment dates and needs
    ``calendar``; a strict calendar that does not cover a date raises ``CalendarCoverageError``.
    """
    _choice(flow_dates, FLOW_DATES, "flow_dates")
    if flow_dates == "payment" and calendar is None:
        raise ValueError("flow_dates='payment' needs a calendar covering every payment date")
    if calendar is not None and not isinstance(calendar, BusinessCalendar):
        raise TypeError("calendar must be a BusinessCalendar")
    rows, s = _prepare(frame, settlement)
    check_settlement(s, calendar)
    instruments, times, amounts, owner = [], [], [], []
    extra: dict[str, list] = {c: [] for c in ("years_to_maturity", "priced", "quote", *_MARKET_COLUMNS, "note")}
    for pos, rec in enumerate(rows.to_dict("records")):
        inst, note, market, quote = None, "", None, ""
        maturity = rec["maturity_date"]
        if rec["kind"] in NO_PRICER:
            note = f"no pricer for kind {rec['kind']}"
        elif rec["kind"] == NO_KIND:
            note = "no kind: neither the price file nor the security master says what the security is"
        elif maturity <= s:
            note = f"matured on {maturity}"
        else:
            try:
                inst = _instrument(rec, calendar if flow_dates == "payment" else None)
                t, a = _instrument_flows(inst, s, flow_dates)
            except CalendarCoverageError:
                raise
            except (ValueError, TypeError) as exc:
                inst, note = None, f"cannot price: {exc}"
            else:
                times.append(t)
                amounts.append(a)
                owner.append(np.full(t.size, pos))
        if inst is not None:
            clean = None if math.isnan(rec["clean_price"]) else rec["clean_price"]
            yld = None if math.isnan(rec["yield_pct"]) else rec["yield_pct"]
            if clean is not None or yld is not None:
                try:
                    market = _market(inst, s, clean, yld)
                    quote = "price" if clean is not None else "yield"
                    if clean is not None and yld is not None \
                            and abs(market["market_yield_pct"] - yld) * 100 > QUOTE_MISMATCH_BP:
                        note = (f"quote_mismatch: the price gives {market['market_yield_pct']:.4f}%, "
                                f"the yield column {yld:.4f}%")
                except ValueError as exc:
                    market, note = None, f"bad quote: {exc}"
            else:
                note = "no price or yield"
        instruments.append(inst)
        extra["years_to_maturity"].append((maturity - s).days / 365 if maturity is not None else math.nan)
        extra["priced"].append(market is not None)
        extra["quote"].append(quote if market is not None else "")
        for col in _MARKET_COLUMNS:
            extra[col].append(market[col] if market is not None else math.nan)
        extra["note"].append(note)
    for col, values in extra.items():
        rows[col] = values
    def cat(parts, dtype):
        return np.concatenate(parts) if parts else np.zeros(0, dtype=dtype)

    return FlowTable(s, rows, tuple(instruments), cat(times, float), cat(amounts, float), cat(owner, int),
                     flow_dates)


def check_settlement(settlement, calendar: BusinessCalendar | None = None) -> date:
    """``settlement`` if it is a business day (G-secs, SDLs and bills settle on no other), else ``ValueError``.

    ``calendar`` defaults to the Mumbai settlement calendar; in a year its holiday list does not cover only weekends
    are known, so only they are refused there.
    """
    s = as_date(settlement)
    cal = calendar if calendar is not None else calendar_for("mumbai_settlement")
    if not (cal if cal.covers(s) else cal.lenient()).is_business_day(s):
        what = "a weekend" if s.weekday() >= 5 else "a holiday"
        raise ValueError(f"settlement {s} is not a business day ({what} on calendar {cal.name!r}): G-secs, SDLs and "
                         "bills settle on business days")
    return s


def _exclusion(rec: dict, settings: FitSettings, s: date) -> str:
    """Why a row stays out of the fit ('' if it is in)."""
    kind = rec["kind"]
    if kind in NO_PRICER:
        return "no_pricer"
    if kind == NO_KIND:
        return "no_kind"
    if rec["maturity_date"] <= s:
        return "matured"
    if kind not in settings.include_kinds:
        return f"kind:{kind}"
    flags = set(rec["flags"].split(";")) if rec["flags"] else set()
    for flag in settings.exclude_flags:
        if flag in flags:
            return f"flag:{flag}"
    if settings.drop_benchmarks and "benchmark" in flags:
        return "benchmark"
    if not rec["traded"]:
        return "not_traded"
    volume = rec["volume_cr"]
    if settings.min_volume_cr is not None and not math.isnan(volume) and volume < settings.min_volume_cr:
        return "volume"
    days = (rec["maturity_date"] - s).days
    if (days < settings.min_days_bills) if kind in BILL_KINDS else (days / 365 < settings.min_years):
        return "short"
    if rec["weight"] == 0:
        return "zero_weight"
    return ""


def select_universe(frame: pd.DataFrame, settings: FitSettings, settlement) -> pd.DataFrame:
    """The typed bond frame (sorted by maturity, ISIN) with ``in_fit`` and ``excluded_reason`` from the rules.

    Reasons: ``no_pricer``, ``matured``, ``kind:<kind>``, ``flag:<flag>``, ``benchmark``, ``not_traded``,
    ``volume``, ``short``, ``zero_weight``; the fit adds ``outlier``.
    """
    rows, s = _prepare(frame, settlement)
    reasons = [_exclusion(rec, settings, s) for rec in rows.to_dict("records")]
    rows["in_fit"] = [r == "" for r in reasons]
    rows["excluded_reason"] = reasons
    return rows


# ---------------------------------------------------------------- the optimiser


def _project(x: np.ndarray, lo: np.ndarray, hi: np.ndarray, pair, ratio: float) -> np.ndarray:
    """``x`` clipped into the box, then (for ``pair = (i, j)``) moved onto ``x[j] = ratio x[i]`` if below it."""
    x = np.clip(x, lo, hi)
    if pair is not None:
        i, j = pair
        if x[j] < ratio * x[i]:
            a = (x[i] + ratio * x[j]) / (1 + ratio * ratio)  # nearest point of the line x[j] = ratio x[i]
            a = min(max(a, lo[i], lo[j] / ratio), hi[i], hi[j] / ratio)
            x[i], x[j] = a, ratio * a
    return x


def _step_basis(n: int, fixed: np.ndarray, coupled: bool, pair) -> list[np.ndarray]:
    cols = []
    for k in range(n):
        if not fixed[k] and not (coupled and k in pair):
            e = np.zeros(n)
            e[k] = 1.0
            cols.append(e)
    return cols


def _lm_step(J: np.ndarray, r: np.ndarray, x: np.ndarray, lam: float, lo: np.ndarray, hi: np.ndarray,
             pair, ratio: float) -> np.ndarray:
    """Damped Gauss-Newton step, holding parameters at a bound when the step would leave it."""
    n = x.size
    col = np.sqrt(np.einsum("ij,ij->j", J, J))
    floor = 1e-8 * (col.max() if col.max() > 0 else 1.0)
    scale = np.maximum(col, floor)
    at_lo, at_hi = x <= lo, x >= hi
    fixed = np.zeros(n, dtype=bool)
    coupled = False
    delta = np.zeros(n)
    for _ in range(n + 2):
        cols = _step_basis(n, fixed, coupled, pair)
        if coupled and not fixed[pair[0]] and not fixed[pair[1]]:
            e = np.zeros(n)
            e[pair[0]], e[pair[1]] = 1.0, ratio
            cols.append(e)
        if not cols:
            return np.zeros(n)
        Z = np.array(cols).T
        A = np.vstack([J @ Z, math.sqrt(lam) * (scale[:, None] * Z)])
        b = np.r_[-r, np.zeros(n)]
        delta = Z @ np.linalg.lstsq(A, b, rcond=None)[0]
        newly = ~fixed & ((at_lo & (delta < 0)) | (at_hi & (delta > 0)))
        grow = bool(newly.any())
        fixed |= newly
        if pair is not None and not coupled:
            i, j = pair
            if x[j] <= ratio * x[i] and delta[j] - ratio * delta[i] < 0:
                coupled = grow = True
        if not grow:
            break
    return delta


@dataclass(frozen=True)
class _Run:
    x: np.ndarray
    objective: float
    iterations: int
    converged: bool
    reason: str
    start: int = 0
    last_change: float = math.nan  # relative fall of the objective in the last accepted step


def _lm(fun, x0, lo, hi, *, pair=None, ratio: float = 1.0, max_iter: int = 200, ftol: float = 1e-12,
        xtol: float = 1e-10, start: int = 0) -> _Run:
    """Levenberg-Marquardt for ``min ||r(x)||^2`` inside ``lo <= x <= hi`` (and ``x[j] >= ratio x[i]``).

    ``fun(x)`` returns the residuals and their Jacobian. Stops when the objective falls by less than ``ftol``
    relative, the step is below ``xtol`` (relative to ``1 + max|x|``), no damping finds a descent, or after
    ``max_iter`` iterations (the only stop that counts as not converged).
    """
    x = _project(np.array(x0, dtype=float), lo, hi, pair, ratio)
    r, J = fun(x)
    f = float(r @ r)
    if not math.isfinite(f):
        return _Run(x, math.inf, 0, False, "start_not_finite", start)
    lam, it = 1e-3, 0
    while it < max_iter:
        it += 1
        if f == 0.0:
            return _Run(x, f, it, True, "exact", start)
        while True:
            if lam > 1e16:
                return _Run(x, f, it, True, "no_descent", start)
            x_new = _project(x + _lm_step(J, r, x, lam, lo, hi, pair, ratio), lo, hi, pair, ratio)
            if np.max(np.abs(x_new - x)) <= xtol * (1 + np.max(np.abs(x))):
                return _Run(x, f, it, True, "small_step", start)
            r_new, J_new = fun(x_new)
            f_new = float(r_new @ r_new)
            if math.isfinite(f_new) and f_new < f:
                break
            lam *= 4
        decrease = f - f_new
        x, r, J, f = x_new, r_new, J_new, f_new
        lam = max(lam / 3, 1e-12)
        change = decrease / (f + decrease)
        if change <= ftol:
            return _Run(x, f, it, True, "small_change", start, change)
    return _Run(x, f, it, False, "max_iter", start, change)


# ---------------------------------------------------------------- the fitting problem


class _Problem:
    """The bonds in a fit as arrays: flows grouped by bond, market dirty prices, error scales and weights."""

    def __init__(self, ft: FlowTable, rows: np.ndarray, weights: np.ndarray, objective: str,
                 anchor: tuple[float, float] | None, basis: np.ndarray | None = None):
        self.rows = np.asarray(rows, dtype=int)
        sel = np.isin(ft.owner, self.rows)
        owner = ft.owner[sel]
        self.t = ft.times[sel]
        self.cf = ft.amounts[sel]
        self.starts = np.flatnonzero(np.r_[True, owner[1:] != owner[:-1]])
        self.market = ft.market_dirty[self.rows]
        self.duration = ft.modified_duration[self.rows]
        self.yield_scale = 1.0 / (self.market * self.duration)  # price error -> approximate yield error
        if objective == "yield":
            scale = self.yield_scale
        elif objective == "price":
            scale = np.ones(self.rows.size)
        else:
            scale = 1.0 / np.sqrt(self.duration)
        self.weights = np.asarray(weights, dtype=float)
        self.factor = np.sqrt(self.weights) * scale
        self.anchor = anchor
        self.basis = None if basis is None else basis[sel]  # spline: integrated basis at each flow
        self.n_obs = self.rows.size + (anchor is not None)

    def prices(self, df: np.ndarray) -> np.ndarray:
        return np.add.reduceat(self.cf * df, self.starts)

    def flat_yields(self) -> tuple[np.ndarray, np.ndarray]:
        """Each bond's continuously compounded yield (percent) and the time to its flows weighted by value."""
        counts = np.diff(np.r_[self.starts, self.t.size])
        z = np.full(self.rows.size, 0.07)
        for _ in range(60):
            zf = np.repeat(z, counts)
            v = self.cf * np.exp(-zf * self.t)
            price = np.add.reduceat(v, self.starts)
            slope = -np.add.reduceat(v * self.t, self.starts)
            step = (price - self.market) / slope
            z = np.clip(z - step, -0.5, 1.0)
            if np.max(np.abs(step)) < 1e-13:
                break
        v = self.cf * np.exp(-np.repeat(z, counts) * self.t)
        return z * 100, np.add.reduceat(v * self.t, self.starts) / np.add.reduceat(v, self.starts)


def _nss_residuals(prob: _Problem, theta: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Residuals and Jacobian in the coordinates (b0, b0 + b1, b2, b3, tau1, tau2)."""
    b0, s, b2, b3, tau1, tau2 = theta
    b1 = s - b0
    t = prob.t
    x1, x2 = t / tau1, t / tau2
    e1, e2 = np.exp(-x1), np.exp(-x2)
    p1, p2 = _phi(x1), _phi(x2)
    l2, l3 = p1 - e1, p2 - e2
    z = b0 + b1 * p1 + b2 * l2 + b3 * l3
    v = prob.cf * np.exp(-z * t / 100)
    r = prob.factor * (np.add.reduceat(v, prob.starts) - prob.market)
    dz = np.empty((t.size, 6))
    dz[:, 0] = 1 - p1
    dz[:, 1] = p1
    dz[:, 2] = l2
    dz[:, 3] = l3
    dz[:, 4] = (b1 * (p1 - e1) + b2 * (p1 - e1 - x1 * e1)) / tau1
    dz[:, 5] = b3 * (p2 - e2 - x2 * e2) / tau2
    J = np.add.reduceat((v * (-t / 100))[:, None] * dz, prob.starts, axis=0) * prob.factor[:, None]
    if prob.anchor is not None:
        rate, k = prob.anchor
        row = np.zeros(6)
        row[1] = k / 100
        r, J = np.r_[r, k * (s - rate) / 100], np.vstack([J, row])
    return r, J


def _flat_residuals(prob: _Problem, theta: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    z = theta[0]
    v = prob.cf * np.exp(-z * prob.t / 100)
    r = prob.factor * (np.add.reduceat(v, prob.starts) - prob.market)
    J = (np.add.reduceat(v * (-prob.t / 100), prob.starts) * prob.factor)[:, None]
    if prob.anchor is not None:
        rate, k = prob.anchor
        r, J = np.r_[r, k * (z - rate) / 100], np.vstack([J, [[k / 100]]])
    return r, J


def _spline_residuals(prob: _Problem, c: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    v = prob.cf * np.exp(-(prob.basis @ c))
    r = prob.factor * (np.add.reduceat(v, prob.starts) - prob.market)
    J = np.add.reduceat(-v[:, None] * prob.basis, prob.starts, axis=0) * prob.factor[:, None]
    if prob.anchor is not None:
        rate, k = prob.anchor
        row = np.zeros(c.size)
        row[0] = k
        r, J = np.r_[r, k * (c[0] - rate / 100)], np.vstack([J, row])
    return r, J


@dataclass(frozen=True)
class _State:
    """A solved fit: parameters, objective and what the method reports about it."""

    x: np.ndarray
    objective: float
    iterations: int
    converged: bool
    reason: str
    runs: tuple[_Run, ...] = ()
    lam: float = math.nan
    edf: float = math.nan
    gcv: tuple[tuple[float, float], ...] = ()


class _Nss:
    name = "nss"

    def __init__(self, settings: FitSettings):
        self.settings = settings
        self.lo, self.hi = np.array(NSS_LOWER), np.array(NSS_UPPER)

    def residuals(self, prob: _Problem, x: np.ndarray):
        return _nss_residuals(prob, x)

    def _run(self, prob: _Problem, x0: np.ndarray, start: int) -> _Run:
        return _lm(lambda x: _nss_residuals(prob, x), x0, self.lo, self.hi, pair=(4, 5),
                   ratio=self.settings.tau_ratio, max_iter=self.settings.max_iter, start=start)

    def starts(self, prob: _Problem, warm) -> list[np.ndarray]:
        st = self.settings
        yc, tm = prob.flat_yields()
        w = np.sqrt(prob.weights)
        out = []
        for tau1 in st.tau1_grid:
            for tau2 in st.tau2_grid:
                if tau2 < st.tau_ratio * tau1:
                    continue
                L = nss_loadings(tm, tau1, tau2) * w[:, None]
                y = yc * w
                if prob.anchor is not None:
                    L, y = np.vstack([L, [1.0, 1.0, 0.0, 0.0]]), np.r_[y, prob.anchor[0]]
                beta = np.linalg.solve(L.T @ L + 1e-6 * np.eye(4), L.T @ y)
                out.append(np.array([beta[0], beta[0] + beta[1], beta[2], beta[3], tau1, tau2]))
        if warm is not None:
            p = _check_nss(warm.params if isinstance(warm, NssCurve) else warm)
            out.append(np.array([p[0], p[0] + p[1], p[2], p[3], p[4], p[5]]))
        return out

    def solve(self, prob: _Problem, *, previous: _State | None = None, warm=None, multistart: bool = True) -> _State:
        if multistart or previous is None:
            runs = tuple(self._run(prob, x0, k) for k, x0 in enumerate(self.starts(prob, warm)))
        else:
            runs = (self._run(prob, previous.x, 0),)
        best = min(runs, key=lambda run: (run.objective, run.start))
        return _State(best.x, best.objective, best.iterations, best.converged, best.reason, runs)

    def curve(self, state: _State, **kw) -> NssCurve:
        b0, s, b2, b3, tau1, tau2 = (float(v) for v in state.x)
        return NssCurve(params=(b0, s - b0, b2, b3, tau1, tau2), method="nss", **kw)


class _Flat:
    name = "flat"

    def __init__(self, settings: FitSettings):
        self.settings = settings
        self.lo, self.hi = np.array([FLAT_BOUNDS[0]]), np.array([FLAT_BOUNDS[1]])

    def residuals(self, prob: _Problem, x: np.ndarray):
        return _flat_residuals(prob, x)

    def solve(self, prob: _Problem, *, previous: _State | None = None, warm=None, multistart: bool = True) -> _State:
        x0 = previous.x if previous is not None else np.array([float(np.median(prob.flat_yields()[0]))])
        run = _lm(lambda x: _flat_residuals(prob, x), x0, self.lo, self.hi, max_iter=self.settings.max_iter)
        return _State(run.x, run.objective, run.iterations, run.converged, run.reason, (run,))

    def curve(self, state: _State, **kw) -> NssCurve:
        return NssCurve(params=(float(state.x[0]), 0.0, 0.0, 0.0, 1.0, 2.0), method="flat", **kw)


class _Spline:
    name = "pspline"

    def __init__(self, settings: FitSettings, ft: FlowTable, rows: np.ndarray, obj_scale: float):
        self.settings = settings
        sel = np.isin(ft.owner, rows)
        end = float(ft.times[sel].max())
        k = settings.knots
        if settings.knot_spacing == "quadratic":
            inner = end * (np.arange(1, k + 1) / (k + 1)) ** 2
        else:
            years = ft.rows["years_to_maturity"].to_numpy(dtype=float)[rows]
            inner = np.unique(np.quantile(years, np.arange(1, k + 1) / (k + 1)))
            inner = inner[(inner > 0) & (inner < end)]
        self.knots = np.r_[np.zeros(4), inner, np.full(4, end)]
        self.basis = np.zeros((ft.times.size, self.knots.size - 4))
        self.basis[sel] = bspline_integral_basis(ft.times[sel], self.knots)
        n = self.knots.size - 4
        d2 = np.diff(np.eye(n), 2, axis=0)
        self.penalty = d2.T @ d2
        self.unit = PENALTY_UNIT * obj_scale ** 2

    def residuals(self, prob: _Problem, x: np.ndarray):
        return _spline_residuals(prob, x)

    def _solve_lam(self, prob: _Problem, lam: float, c0: np.ndarray) -> tuple[np.ndarray, float, float, int, bool]:
        """Gauss-Newton with step halving on ``||r||^2 + lam' c'Pc``; returns (c, rss, edf, iterations, converged).

        ``self.last_scores`` keeps the scores of the solution in ``LAM_CRITERIA`` order: the leave-one-out score
        ``CV = mean((r_i / (1 - h_ii))^2)``, ``h_ii`` the leverages of the linearised fit, and
        ``GCV = n RSS / (n - sum h_ii)^2``, both over the bonds only: an overnight anchor stays in the fit but is not
        a price to predict, and its own leave-one-out error (how well the bonds extrapolate to ``t = 0``) would
        otherwise dominate the score. ``self.last_leverage`` keeps the bonds' leverages.
        """
        weight = lam * self.unit
        P = self.penalty
        c = c0.copy()
        r, J = _spline_residuals(prob, c)
        phi = float(r @ r + weight * c @ P @ c)
        converged, it = False, 0
        for it in range(1, 51):
            A = J.T @ J + weight * P
            step = np.linalg.solve(A, -(J.T @ r + weight * (P @ c)))
            for _ in range(30):
                c_new = c + step
                r_new, J_new = _spline_residuals(prob, c_new)
                phi_new = float(r_new @ r_new + weight * c_new @ P @ c_new)
                if math.isfinite(phi_new) and phi_new <= phi:
                    break
                step = step / 2
            else:
                converged = True  # no further descent along the Gauss-Newton direction
                break
            c, r, J, phi = c_new, r_new, J_new, phi_new
            if np.max(np.abs(step)) < 1e-12:
                converged = True
                break
        A = J.T @ J + weight * P
        leverage = np.einsum("ij,ji->i", J, np.linalg.solve(A, J.T))
        edf = float(leverage.sum())
        rss = float(r @ r)
        n = prob.rows.size  # the bonds: an anchor row, if any, comes last
        rb, hb = r[:n], leverage[:n]
        dof = n - float(hb.sum())
        gcv = n * float(rb @ rb) / dof ** 2 if dof > 1e-9 else math.inf
        cv = float(np.mean((rb / (1 - hb)) ** 2)) if np.all(hb < 1 - 1e-9) else math.inf
        self.last_scores = {"cv": cv, "gcv": gcv}
        self.last_leverage = hb.copy()
        return c, rss, edf, it, converged

    def solve(self, prob: _Problem, *, previous: _State | None = None, warm=None, multistart: bool = True) -> _State:
        n = self.knots.size - 4
        if previous is not None and not multistart:
            c, rss, edf, it, conv = self._solve_lam(prob, previous.lam, previous.x)
            return _State(c, rss, it, conv, "gauss_newton", (), previous.lam, edf, previous.gcv)
        c0 = np.full(n, float(np.median(prob.flat_yields()[0])) / 100)
        lams = (self.settings.lam,) if self.settings.lam is not None else self.settings.lambdas
        table, best = [], None
        for lam in lams:
            c, rss, edf, it, conv = self._solve_lam(prob, lam, c0)
            score = self.last_scores[self.settings.lam_criterion]
            table.append((lam, score))
            if best is None or score < best[0]:
                best = (score, lam, c, rss, edf, it, conv)
        _, lam, c, rss, edf, it, conv = best
        return _State(c, rss, it, conv, "gauss_newton", (), lam, edf, tuple(table))

    def curve(self, state: _State, **kw) -> SplineCurve:
        return SplineCurve(knots=tuple(self.knots), coefs=tuple(float(v) for v in state.x), lam=float(state.lam),
                           edf=float(state.edf), method="pspline", **kw)


# ---------------------------------------------------------------- fitting


@dataclass(frozen=True)
class FitResult:
    """A fitted curve, the residual of every input row against it (``RESIDUAL_COLUMNS`` plus
    ``RESIDUAL_EXTRA_COLUMNS``, sorted by maturity and ISIN), diagnostics and the settings used."""

    curve: FittedCurve
    residuals: pd.DataFrame
    diagnostics: dict
    settings: FitSettings


def _base_weights(rows: pd.DataFrame, fit_rows: np.ndarray, settings: FitSettings) -> np.ndarray:
    user = rows["weight"].to_numpy(dtype=float)[fit_rows]
    base = np.ones(fit_rows.size)
    if settings.weights == "liquidity":
        volume = rows["volume_cr"].to_numpy(dtype=float)[fit_rows]
        ok = np.isfinite(volume) & (volume > 0)
        if not ok.any():
            raise ValueError("weights='liquidity' needs volume_cr for the bonds in the fit; none has one")
        root = np.sqrt(np.where(ok, volume, np.nan))
        median = float(np.nanmedian(root))
        base = np.where(ok, np.minimum(root, LIQUIDITY_CAP * median), median)
    w = base * user
    return w / w.mean()


def _curve_id(ft: FlowTable, fit_rows: np.ndarray, weights: np.ndarray, settings: FitSettings) -> str:
    rows = ft.rows
    bonds = []
    for k, pos in enumerate(fit_rows):
        rec = rows.iloc[pos]
        bonds.append([rec["isin"], rec["kind"], repr(float(rec["coupon_pct"])), str(rec["maturity_date"]),
                      str(rec["issue_date"] or ""), str(rec["first_coupon_date"] or ""),
                      repr(float(rec["market_dirty"])), repr(float(weights[k]))])
    payload = {"settlement": ft.settlement.isoformat(), "flow_dates": ft.flow_dates,
               "settings": asdict(settings), "bonds": bonds}
    text = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _range_grid(t_min: float, t_max: float) -> np.ndarray:
    inner = np.arange(math.ceil(t_min * 4) / 4, t_max, 0.25)
    grid = np.unique(np.r_[t_min, inner, t_max])
    return grid[grid > 0]


def _robust_sd(values: np.ndarray) -> float:
    if values.size < 2:
        return math.nan
    return 1.4826 * float(np.median(np.abs(values - np.median(values))))


def _finite(value):
    """A JSON-ready number: NaN and infinities become None."""
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _json_ready(obj):
    """``obj`` with numpy scalars as Python numbers and non-finite floats as None, recursively."""
    if isinstance(obj, dict):
        return {str(k): _json_ready(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_ready(v) for v in obj]
    if isinstance(obj, (bool, np.bool_)):
        return bool(obj)
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        return _finite(obj)
    return obj


def _check_counts(ft: FlowTable, fit_rows: np.ndarray, method: str) -> None:
    n = fit_rows.size
    need = MIN_BONDS[method]
    if n < need:
        names = list(ft.rows["isin"].iloc[fit_rows])
        isins = (", ".join(names[:10]) + (", ..." if len(names) > 10 else "")) or "none"
        raise ValueError(f"{n} bond(s) in the fit ({isins}); method {method!r} needs at least {need}. "
                         "Widen include_kinds or the filters, or use method='flat' for a single rate")
    distinct = len(set(ft.rows["maturity_date"].iloc[fit_rows]))
    if distinct < MIN_MATURITIES[method]:
        raise ValueError(f"the {n} bonds in the fit have {distinct} distinct maturity date(s); method {method!r} "
                         f"needs at least {MIN_MATURITIES[method]} to identify a curve")


def _fit(ft: FlowTable, settings: FitSettings, warm_start=None) -> FitResult:
    s, rows, method = ft.settlement, ft.rows, settings.method
    recs = rows.to_dict("records")
    reasons = [_exclusion(rec, settings, s) for rec in recs]
    for pos, reason in enumerate(reasons):
        if reason == "" and not rows["priced"].iat[pos]:
            raise ValueError(f"ISIN {rows['isin'].iat[pos]} is in the fit but has no usable market price "
                             f"({rows['note'].iat[pos] or 'no price or yield'}): give a positive clean_price or a "
                             "yield_pct, or mark it traded=false")
    fit_rows = np.array([p for p, reason in enumerate(reasons) if reason == ""], dtype=int)
    _check_counts(ft, fit_rows, method)
    base = _base_weights(rows, fit_rows, settings)
    market, duration = ft.market_dirty[fit_rows], ft.modified_duration[fit_rows]
    obj_scale = {"yield": 1.0, "price": float(np.median(market * duration)),
                 "price_over_duration": float(np.median(market * np.sqrt(duration)))}[settings.objective]
    anchor = None if settings.anchor_pct is None else (settings.anchor_pct,
                                                       math.sqrt(settings.anchor_weight) * obj_scale)
    if method == "nss":
        model = _Nss(settings)
    elif method == "flat":
        model = _Flat(settings)
    else:
        model = _Spline(settings, ft, fit_rows, obj_scale)
    basis = getattr(model, "basis", None)
    cid = _curve_id(ft, fit_rows, base, settings)

    def problem(active: np.ndarray, robust: np.ndarray) -> _Problem:
        return _Problem(ft, fit_rows[active], (base * robust)[active], settings.objective, anchor, basis)

    def yield_errors_bp(state: _State, active: np.ndarray) -> np.ndarray:
        prob = problem(active, np.ones(fit_rows.size))
        r, _ = model.residuals(prob, state.x)
        r = r[:prob.rows.size] / prob.factor  # price errors
        return r * prob.yield_scale * 1e4

    warnings: list[str] = []
    active = np.ones(fit_rows.size, dtype=bool)
    robust = np.ones(fit_rows.size)
    outliers: list[dict] = []
    huber_converged = True

    def irls(state: _State) -> tuple[_State, np.ndarray, bool]:
        weights = robust.copy()
        for _ in range(50):
            e = np.abs(yield_errors_bp(state, active))
            new = np.ones(fit_rows.size)
            new[active] = np.minimum(1.0, settings.huber_bp / np.maximum(e, 1e-300))
            if np.max(np.abs(new - weights)) < 1e-9:
                return state, new, True
            weights = new
            state = model.solve(_Problem(ft, fit_rows[active], (base * weights)[active], settings.objective,
                                         anchor, basis), previous=state, multistart=False)
        return state, weights, False

    state = model.solve(problem(active, robust), warm=warm_start, multistart=True)
    while True:
        if settings.loss == "huber":
            state, robust, huber_converged = irls(state)
        if settings.outlier_bp is None or len(outliers) >= settings.outlier_rounds:
            break
        e = yield_errors_bp(state, active)
        threshold = max(settings.outlier_bp, settings.outlier_mads * _robust_sd(e))
        worst = int(np.argmax(np.abs(e)))
        if not abs(e[worst]) > threshold:
            break
        if active.sum() - 1 < MIN_BONDS[method]:
            warnings.append(f"outlier_rule_stopped: removing {rows['isin'].iat[fit_rows[active][worst]]} "
                            f"({e[worst]:+.2f} bp) would leave fewer than {MIN_BONDS[method]} bonds")
            break
        pos = np.flatnonzero(active)[worst]
        outliers.append({"isin": rows["isin"].iat[fit_rows[pos]], "round": len(outliers) + 1,
                         "approx_resid_bp": float(e[worst]), "threshold_bp": float(threshold)})
        active[pos] = False
        robust[pos] = 1.0
        state = model.solve(problem(active, robust), previous=state, multistart=False)
    if outliers:
        state = model.solve(problem(active, robust), previous=state, warm=None, multistart=True)
        if settings.loss == "huber":
            state, robust, huber_converged = irls(state)
    final_rows = fit_rows[active]
    years = rows["years_to_maturity"].to_numpy(dtype=float)
    t_min, t_max = float(years[final_rows].min()), float(years[final_rows].max())
    curve = model.curve(state, valuation_date=s, t_min=t_min, t_max=t_max, curve_id=cid)

    loo_resid = np.full(len(rows), math.nan)
    loo_shift = np.full(len(rows), math.nan)
    loo_par = np.full(len(rows), math.nan)
    leverage = np.full(len(rows), math.nan)
    leverage[final_rows] = _leverages(model, problem(active, robust), state)
    grid = _range_grid(t_min, t_max)
    par = curve.par_yield(grid)
    if settings.leave_one_out:
        if active.sum() - 1 < MIN_BONDS[method]:
            warnings.append(f"leave_one_out_skipped: {int(active.sum())} bonds, {method!r} needs "
                            f"{MIN_BONDS[method] + 1} to refit without one")
        else:
            for k in np.flatnonzero(active):
                keep = active.copy()
                keep[k] = False
                sub = model.solve(problem(keep, robust), previous=state, multistart=False)
                lo, hi = float(years[fit_rows[keep]].min()), float(years[fit_rows[keep]].max())
                other = model.curve(sub, valuation_date=s, t_min=lo, t_max=hi, curve_id=cid)
                pos = fit_rows[k]
                loo_resid[pos] = 100 * (rows["market_yield_pct"].iat[pos] - other.bond_yield(ft.instruments[pos], s))
                inner = _range_grid(lo, hi)  # the move of the par curve where the refit still has data
                loo_shift[pos] = float(np.max(np.abs(other.par_yield(inner) - curve.par_yield(inner))) * 100)
                loo_par[pos] = float(other.par_yield(years[pos]))  # for a cross-check the bond cannot sway

    in_fit = np.zeros(len(rows), dtype=bool)
    in_fit[final_rows] = True
    for o in outliers:
        reasons[int(np.flatnonzero(rows["isin"].to_numpy() == o["isin"])[0])] = "outlier"
    weight_col = np.zeros(len(rows))
    weight_col[final_rows] = base[active]
    robust_col = np.full(len(rows), math.nan)
    robust_col[final_rows] = robust[active]
    residuals = _residual_frame(curve, ft, in_fit=in_fit, reasons=reasons, weights=weight_col, robust=robust_col,
                                loo_resid=loo_resid, loo_shift=loo_shift, loo_par=loo_par, leverage=leverage,
                                settings_id=settings_id(settings))
    diagnostics = _diagnostics(ft, settings, model, state, curve, residuals, in_fit, outliers, warnings, robust,
                               fit_rows, active, huber_converged, grid, par, obj_scale)
    return FitResult(curve, residuals, diagnostics, settings)


def settings_id(settings: FitSettings) -> str:
    """The first 16 hex digits of the SHA-256 of how the curve is fitted: equal for fits made the same way on any day
    (``curve_id`` also hashes the day's bonds and inputs), so a residual history can be checked for a change of method.
    The fields of ``PER_DAY_SETTINGS`` are the day's market data, like the prices: only whether each is set enters (an
    anchor at each day's overnight rate is one method; its weight is part of it), not its value."""
    payload = asdict(settings)
    for name in PER_DAY_SETTINGS:
        if payload[name] is not None:
            payload[name] = "set each day"  # an unset field hashes as before, so unanchored ids do not change
    text = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _leverages(model, prob: _Problem, state: _State) -> np.ndarray:
    """Each bond's leverage ``h_ii`` in the linearised fit at the solution (``Z (Z'Z)^+ Z'``, ``Z`` the weighted
    Jacobian of the free parameters; the spline's includes its penalty). A bond's in-fit residual is about
    ``1 - h_ii`` of its out-of-sample one: a residual near a leverage of one says little about richness."""
    n = prob.rows.size
    if isinstance(model, _Spline):
        model._solve_lam(prob, state.lam, state.x)
        return model.last_leverage[:n]
    _, J = model.residuals(prob, state.x)
    lo, hi = model.lo, model.hi
    x = state.x
    cols = [k for k in range(x.size) if lo[k] < x[k] < hi[k]]
    Z = [J[:, k] for k in cols]
    if isinstance(model, _Nss):
        ratio = model.settings.tau_ratio
        if x[5] <= ratio * x[4] * (1 + 1e-12):  # tau2 on its ratio to tau1: the two move together
            Z = [J[:, k] for k in cols if k not in (4, 5)]
            if 4 in cols and 5 in cols:
                Z.append(J[:, 4] + ratio * J[:, 5])
    if not Z:
        return np.zeros(n)
    u, sv, _ = np.linalg.svd(np.column_stack(Z), full_matrices=False)
    keep = sv > sv.max() * 1e-10
    return np.sum(u[:, keep] ** 2, axis=1)[:n]


def _diagnostics(ft, settings, model, state, curve, residuals, in_fit, outliers, warnings, robust, fit_rows, active,
                 huber_converged, grid, par, obj_scale) -> dict:
    fit = residuals[residuals["in_fit"]]
    r = fit["resid_yield_bp"].to_numpy(dtype=float)
    w = fit["weight"].to_numpy(dtype=float) * fit["robust_weight"].to_numpy(dtype=float)
    rp = fit["resid_price"].to_numpy(dtype=float)
    order = np.argsort(-np.abs(r), kind="stable")[:5]
    largest = [{"isin": fit["isin"].iat[i], "resid_yield_bp": _finite(r[i]), "resid_price": _finite(rp[i])}
               for i in order]
    method = settings.method
    if not state.converged:
        best_run = min(state.runs, key=lambda run: (run.objective, run.start)) if state.runs else None
        tail = (f", the objective still falling by {best_run.last_change:.1e} relative per step; raise max_iter"
                if best_run is not None and math.isfinite(best_run.last_change) else "")
        warnings.append(f"not_converged: stopped after {state.iterations} iterations ({state.reason}){tail}")
    if settings.loss == "huber" and not huber_converged:
        warnings.append("huber_not_converged: robust weights still moving after 50 rounds")
    params = None
    n_starts = n_at_best = None
    start_objectives = None
    if method in ("nss", "flat"):
        params = dict(zip(NSS_PARAMS, curve.params)) if method == "nss" else {"zero_cc_pct": curve.params[0]}
        runs = state.runs
        n_obs = int(active.sum()) + (settings.anchor_pct is not None)
        tol_abs = n_obs * (1e-8 * obj_scale) ** 2  # an RMS difference of 1e-4 bp of yield
        best = state.objective
        at_best = {run.start for run in runs if run.objective <= best * (1 + 1e-9) + tol_abs}
        n_starts, n_at_best = len(runs), len(at_best)
        start_objectives = [_finite(run.objective) for run in runs]
        if method == "nss":
            at_bound = _nss_at_bound(state.x, settings.tau_ratio)
            if at_bound:
                warnings.append("params_at_bound: " + ", ".join(at_bound))
            others = [run for run in runs if run.start not in at_best and math.isfinite(run.objective)]
            if n_at_best == 1 and others:
                runner = min(others, key=lambda run: (run.objective, run.start))
                alt = model.curve(_State(runner.x, runner.objective, 0, True, ""), valuation_date=curve.valuation_date,
                                  t_min=curve.t_min, t_max=curve.t_max)
                gap = float(np.max(np.abs(alt.par_yield(grid) - par)) * 100)
                if gap > UNSTABLE_BP:
                    warnings.append(f"unstable_optimum: one of {n_starts} starts reached the best objective; the "
                                    f"next best differs by up to {gap:.2f} bp in par yield inside the data range")
    if method == "pspline" and state.gcv and settings.lam is None:
        lams = [lam for lam, _ in state.gcv]
        if state.lam in (lams[0], lams[-1]) and len(lams) > 1:
            warnings.append(f"lambda_at_grid_edge: {settings.lam_criterion.upper()} chose lam={state.lam:g}, an end "
                            "of the grid")
    if curve.t_min > 1.0 and settings.anchor_pct is None:
        warnings.append(f"short_end_extrapolated: the shortest bond in the fit matures in {curve.t_min:.2f} years "
                        "and no anchor is set")
    sd = _robust_sd(r)
    suspect = [fit["isin"].iat[i] for i in range(len(r))
               if abs(r[i]) > SUSPECT_BP and (not math.isfinite(sd) or abs(r[i]) > SUSPECT_SD * sd)]
    if suspect:
        warnings.append(f"suspect_residuals: above {SUSPECT_BP:g} bp and {SUSPECT_SD:g} robust sd: "
                        + ", ".join(suspect))
    mismatch = [row.isin for row in residuals.itertuples() if row.note.startswith("quote_mismatch")]
    if mismatch:
        warnings.append("quote_mismatch: price and yield disagree for " + ", ".join(mismatch))
    kindless = [row.isin for row in residuals.itertuples() if row.kind == NO_KIND]
    if kindless:
        warnings.append("no_kind: out of the fit, the master does not say what they are: " + ", ".join(kindless))
    if not ft.rows["trade_info"].any():
        warnings.append("no_trade_data: no row has traded, trade_count or volume_cr, so every priced row was taken as "
                        "traded; fit traded prices (a valuation sheet recovers its maker's model)")
    loo = None
    lr = fit["loo_resid_yield_bp"].to_numpy(dtype=float)
    ls = fit["loo_shift_bp"].to_numpy(dtype=float)
    if np.isfinite(lr).any():
        k = int(np.nanargmax(ls))
        years = fit["years_to_maturity"].to_numpy(dtype=float)
        inner = (years > years.min()) & (years < years.max())  # without the bonds at the ends of the range
        loo = {"rmse_bp": _finite(math.sqrt(np.nanmean(lr ** 2))), "max_abs_bp": _finite(np.nanmax(np.abs(lr))),
               "rmse_inner_bp": _finite(math.sqrt(np.nanmean(lr[inner] ** 2))) if inner.any() else None,
               "max_shift_bp": _finite(ls[k]), "max_shift_isin": fit["isin"].iat[k]}
    anchor = None
    if settings.anchor_pct is not None:
        anchor = {"rate_pct": settings.anchor_pct, "weight": settings.anchor_weight,
                  "model_pct": _finite(curve.zero_rate(0.0))}
    return _json_ready({
        "settlement": ft.settlement.isoformat(), "method": method, "curve_id": curve.curve_id,
        "n_input": len(ft.rows), "n_priced": int(ft.rows["priced"].sum()), "n_fit": int(in_fit.sum()),
        "n_outliers": len(outliers),
        "rmse_bp": _finite(math.sqrt(np.mean(r ** 2))), "wrmse_bp": _finite(math.sqrt(np.sum(w * r ** 2) / np.sum(w))),
        "mae_bp": _finite(np.mean(np.abs(r))), "max_abs_bp": _finite(np.max(np.abs(r))),
        "rmse_price": _finite(math.sqrt(np.mean(rp ** 2))), "max_abs_price": _finite(np.max(np.abs(rp))),
        "largest": largest, "t_min": curve.t_min, "t_max": curve.t_max,
        "objective": _finite(state.objective), "objective_kind": settings.objective, "loss": settings.loss,
        "converged": bool(state.converged), "iterations": int(state.iterations), "stop_reason": state.reason,
        "n_starts": n_starts, "n_at_best": n_at_best, "start_objectives": start_objectives, "params": params,
        "edf": _finite(state.edf), "lam": _finite(state.lam),
        "lam_criterion": settings.lam_criterion if method == "pspline" else None,
        "lam_scores": [[_finite(lam), _finite(g)] for lam, g in state.gcv] or None,
        "knots": [float(v) for v in curve.knots] if isinstance(curve, SplineCurve) else None,
        "anchor": anchor, "outliers": outliers,
        "huber_downweighted": [ft.rows["isin"].iat[p] for p, rw in zip(fit_rows, robust) if rw < 1.0],
        "leave_one_out": loo, "warnings": warnings,
    })


def _nss_at_bound(x: np.ndarray, ratio: float) -> list[str]:
    names = ("beta0", "beta0+beta1", "beta2", "beta3", "tau1", "tau2")
    out = []
    for k, name in enumerate(names):
        if x[k] <= NSS_LOWER[k]:
            out.append(f"{name} at its lower bound {NSS_LOWER[k]:g}")
        elif x[k] >= NSS_UPPER[k]:
            out.append(f"{name} at its upper bound {NSS_UPPER[k]:g}")
    if x[5] <= ratio * x[4]:
        out.append(f"tau2 at {ratio:g} x tau1")
    return out


def _residual_frame(curve: DiscountCurveLike, ft: FlowTable, *, in_fit, reasons, weights, robust, loo_resid,
                    loo_shift, loo_par=None, leverage=None, settings_id: str = "") -> pd.DataFrame:
    s, rows = ft.settlement, ft.rows
    model = np.full(len(rows), math.nan)
    if ft.times.size:
        values = ft.amounts * np.asarray(curve.discount_t(ft.times), dtype=float)
        first = np.flatnonzero(np.r_[True, ft.owner[1:] != ft.owner[:-1]])
        model[ft.owner[first]] = np.add.reduceat(values, first)
    method = getattr(curve, "method", type(curve).__name__)
    curve_id = getattr(curve, "curve_id", "")
    out = []
    for pos, rec in enumerate(rows.to_dict("records")):
        inst = ft.instruments[pos]
        model_dirty = model[pos]
        model_clean = model_yield = math.nan
        note = rec["note"]
        if inst is not None and math.isfinite(model_dirty):
            model_clean = model_dirty - _accrued(inst, s)
            try:
                model_yield = _yield_from_dirty(inst, model_dirty, s)
            except ValueError as exc:
                note = (note + "; " if note else "") + f"no model yield: {exc}"
        out.append({
            "date": s, "isin": rec["isin"], "kind": rec["kind"], "maturity_date": rec["maturity_date"],
            "years_to_maturity": rec["years_to_maturity"], "in_fit": bool(in_fit[pos]),
            "excluded_reason": reasons[pos], "weight": float(weights[pos]),
            "market_clean": rec["market_clean"], "market_dirty": rec["market_dirty"],
            "market_yield_pct": rec["market_yield_pct"], "model_dirty": model_dirty, "model_clean": model_clean,
            "model_yield_pct": model_yield, "resid_price": rec["market_dirty"] - model_dirty,
            "resid_yield_bp": 100 * (rec["market_yield_pct"] - model_yield),
            "modified_duration": rec["modified_duration"], "dv01": rec["dv01"], "method": method,
            "curve_id": curve_id, "coupon_pct": rec["coupon_pct"], "quote": rec["quote"], "flags": rec["flags"],
            "robust_weight": float(robust[pos]), "loo_resid_yield_bp": float(loo_resid[pos]),
            "loo_shift_bp": float(loo_shift[pos]),
            "loo_par_pct": math.nan if loo_par is None else float(loo_par[pos]),
            "leverage": math.nan if leverage is None else float(leverage[pos]), "settings_id": settings_id,
            "note": note,
        })
    return pd.DataFrame(out, columns=[*RESIDUAL_COLUMNS, *RESIDUAL_EXTRA_COLUMNS])


def fit_nss(flows: FlowTable, settings: FitSettings | None = None, *, warm_start=None) -> FitResult:
    """Nelson-Siegel-Svensson fit to a flow table, on ``settings`` (its universe rules decide the bonds).

    ``warm_start``: an ``NssCurve`` or six parameters (b0, b1, b2, b3, tau1, tau2), e.g. the previous day's,
    added to the grid of starts.
    """
    return _fit(flows, replace(settings or DEFAULT_SETTINGS, method="nss"), warm_start)


def fit_pspline(flows: FlowTable, settings: FitSettings | None = None) -> FitResult:
    """Penalised B-spline fit of the forward curve to a flow table, on ``settings``."""
    return _fit(flows, replace(settings or DEFAULT_SETTINGS, method="pspline"))


def fit_curve(frame: pd.DataFrame, settlement, settings: FitSettings | None = None, *, warm_start=None,
              calendar: BusinessCalendar | None = None) -> FitResult:
    """Fit a curve to one settlement date's bonds (``FIT_COLUMNS``) and measure every row against it.

    Raises ``ValueError`` naming the input for: rows of another settlement date, duplicate ISINs, unknown
    kinds, unreadable cells, a bond in the fit without a usable price, and fewer bonds (or distinct maturity
    dates) in the fit than the method needs (``MIN_BONDS``, ``MIN_MATURITIES``).
    """
    settings = settings or DEFAULT_SETTINGS
    ft = flow_table(frame, settlement, calendar=calendar, flow_dates=settings.flow_dates)
    return _fit(ft, settings, warm_start)


def residual_table(curve: DiscountCurveLike, frame: pd.DataFrame, settlement=None, *,
                   calendar: BusinessCalendar | None = None, flow_dates: str = "scheduled") -> pd.DataFrame:
    """Every row of ``frame`` priced on ``curve`` (any ``DiscountCurveLike``), with residuals.

    ``in_fit`` and ``excluded_reason`` are taken from ``frame`` when present (``select_universe`` output);
    otherwise every row is reported out of the fit.
    """
    s = as_date(curve.valuation_date if settlement is None else settlement)
    if s != as_date(curve.valuation_date):
        raise ValueError(f"the curve is for settlement on {curve.valuation_date}, not {s}")
    ft = flow_table(frame, s, calendar=calendar, flow_dates=flow_dates)
    rows = ft.rows
    in_fit = rows["in_fit"].to_numpy(dtype=bool) if "in_fit" in rows else np.zeros(len(rows), dtype=bool)
    reasons = list(rows["excluded_reason"]) if "excluded_reason" in rows else [""] * len(rows)
    weights = np.where(in_fit, rows["weight"].to_numpy(dtype=float), 0.0)
    nan = np.full(len(rows), math.nan)
    return _residual_frame(curve, ft, in_fit=in_fit, reasons=reasons, weights=weights, robust=nan, loo_resid=nan,
                           loo_shift=nan)


# ---------------------------------------------------------------- curve outputs


def curve_points(curve: FittedCurve, tenors: Sequence[float] = DEFAULT_TENORS) -> pd.DataFrame:
    """The curve on a tenor grid (``CURVE_POINT_COLUMNS``): zero rates continuous and semi-annual, the
    instantaneous forward, the semi-annual par yield, the discount factor and whether the tenor is inside
    the data range."""
    t, _ = _times(tenors)
    if not t.size or t.min() <= 0:
        raise ValueError("tenors must be a non-empty list of years above zero")
    return pd.DataFrame({
        "date": curve.valuation_date, "tenor_years": t, "zero_cc_pct": curve.zero_rate(t),
        "zero_sa_pct": curve.zero_rate(t, "semiannual"), "forward_cc_pct": curve.forward_rate(t),
        "par_sa_pct": curve.par_yield(t), "discount": curve.discount_t(t), "in_data_range": curve.in_data_range(t),
        "method": curve.method, "curve_id": curve.curve_id,
    }, columns=list(CURVE_POINT_COLUMNS))


def benchmark_frame(curves: Sequence[FittedCurve], tenors: Sequence[float] = DEFAULT_TENORS) -> pd.DataFrame:
    """Par curves for ``curves.BenchmarkCurve``: ``date, tenor_years, yield_pct, basis = "semiannual"``
    (plus ``in_data_range``, ``method``, ``curve_id``), one curve per date.

    Every point is a semi-annual rate. Below half a year a par bond has one short period, so its par yield is a
    simple rate ``y``; it is restated on the semi-annual basis, ``2 ((1 + y t)^(1 / (2 t)) - 1)``, so that
    ``BenchmarkCurve``'s conversions hold there too.
    """
    seen = {}
    parts = []
    for curve in curves:
        if curve.valuation_date in seen:
            raise ValueError(f"two curves for {curve.valuation_date}: give one curve per date")
        seen[curve.valuation_date] = True
        p = curve_points(curve, tenors)
        t = p["tenor_years"].to_numpy(dtype=float)
        y = p["par_sa_pct"].to_numpy(dtype=float)
        short = t < 0.5 - 1e-12  # one short period: the par yield is simple
        y = np.where(short, 200 * (np.power(1 + y / 100 * t, 1 / (2 * np.where(short, t, 1.0))) - 1), y)
        parts.append(pd.DataFrame({"date": p["date"], "tenor_years": p["tenor_years"], "yield_pct": y,
                                   "basis": "semiannual", "in_data_range": p["in_data_range"],
                                   "method": p["method"], "curve_id": p["curve_id"]}))
    if not parts:
        return pd.DataFrame(columns=["date", "tenor_years", "yield_pct", "basis", "in_data_range", "method",
                                     "curve_id"])
    out = pd.concat(parts, ignore_index=True)
    return out.sort_values(["date", "tenor_years"], kind="stable").reset_index(drop=True)


def compare_curves(primary: FittedCurve, check: FittedCurve, tenors: Sequence[float] = DEFAULT_TENORS,
                   threshold_bp: float = MODEL_RISK_BP) -> pd.DataFrame:
    """Par-yield gap ``check - primary`` (bp) on ``tenors``; ``model_risk`` where it exceeds ``threshold_bp``
    inside the joint data range (outside it both curves extrapolate and the gap is reported, not flagged)."""
    if primary.valuation_date != check.valuation_date:
        raise ValueError(f"curves of different dates: {primary.valuation_date} and {check.valuation_date}")
    threshold = _positive(threshold_bp, "threshold_bp")
    t, _ = _times(tenors)
    if not t.size or t.min() <= 0:
        raise ValueError("tenors must be a non-empty list of years above zero")
    lo, hi = max(primary.t_min, check.t_min), min(primary.t_max, check.t_max)
    p, c = primary.par_yield(t), check.par_yield(t)
    diff = (c - p) * 100
    joint = (t >= lo - 1e-9) & (t <= hi + 1e-9)
    return pd.DataFrame({
        "date": primary.valuation_date, "tenor_years": t, "primary_par_pct": p, "check_par_pct": c, "diff_bp": diff,
        "in_joint_range": joint, "model_risk": joint & (np.abs(diff) > threshold), "threshold_bp": threshold,
        "joint_from_years": lo, "joint_to_years": hi, "primary_method": primary.method, "check_method": check.method,
    }, columns=list(COMPARISON_COLUMNS))


def model_risk_flags(residuals: pd.DataFrame, comparison: pd.DataFrame, *,
                     check_residuals: pd.DataFrame | None = None,
                     refit: Callable[[Sequence[str]], tuple[FittedCurve, FittedCurve]] | None = None) -> pd.DataFrame:
    """``residuals`` with ``crosscheck_diff_bp`` (the comparison's par gap interpolated at each bond's maturity,
    held at the nearest tenor near the ends; NaN outside the joint data range), ``crosscheck_loo_diff_bp``,
    ``crosscheck_suspect_diff_bp``, ``model_risk`` and ``model_risk_note``.

    A flexible check curve bends towards a single mispriced bond, most at the ends of the data, so a bond that is
    simply cheap would show up as a gap at its own maturity. With ``check_residuals`` (the check fit's residuals,
    fitted with ``leave_one_out``) each bond in both fits is judged on ``crosscheck_loo_diff_bp`` instead: the gap
    at its maturity between the two curves refitted without it (``loo_par_pct``), which it cannot sway; other rows
    keep the full curves' gap. ``model_risk``: the gap used above the comparison's threshold.

    A large mispricing elsewhere still sways both refits, differently (NSS over the whole curve, the spline near the
    bond), and opens gaps at its neighbours or at an extrapolated end: one bond 20 bp cheap on an exact NSS curve
    flagged three neighbours at the long end, or the 35-day bill for a mid-curve bond. ``refit`` (``refitter``'s
    callable: both curves fitted afresh without the ISINs it is given) judges such flags again: on a day with suspect
    bonds (out of sample above ``SUSPECT_BP`` and ``SUSPECT_SD`` robust standard deviations, ``suspect_bonds``), each
    bond flagged on its leave-one-out gap keeps its flag only if the gap at its maturity between the two curves
    fitted without it and without the suspects (``crosscheck_suspect_diff_bp``) also exceeds the threshold. A shape
    NSS cannot follow shows in several bonds, none of them suspect on its own (a 10 or 20 bp bump at 11 years keeps
    every flag), and where a bump is large enough to make one of its bonds suspect its core stays flagged; a flag
    whose refit fails (too few bonds left) stands. ``attrs["notes"]`` says what was cleared.
    """
    joint = comparison[comparison["in_joint_range"]].sort_values("tenor_years")
    out = residuals.copy()
    years = out["years_to_maturity"].to_numpy(dtype=float)
    gap = np.full(len(out), math.nan)
    threshold = math.inf
    if len(joint):
        x, y = joint["tenor_years"].to_numpy(dtype=float), joint["diff_bp"].to_numpy(dtype=float)
        lo, hi = float(joint["joint_from_years"].iloc[0]), float(joint["joint_to_years"].iloc[0])
        inside = np.isfinite(years) & (years >= lo - 1e-9) & (years <= hi + 1e-9)
        gap[inside] = np.interp(years[inside], x, y)
        threshold = float(comparison["threshold_bp"].iloc[0])
    out["crosscheck_diff_bp"] = gap
    loo_gap = np.full(len(out), math.nan)
    if check_residuals is not None:
        for name, frame in (("residuals", out), ("check_residuals", check_residuals)):
            if "loo_par_pct" not in frame:
                raise ValueError(f"{name} have no loo_par_pct: fit both curves with leave_one_out=True")
        theirs = check_residuals.assign(_k=check_residuals["isin"].astype(str).str.strip()).set_index("_k")
        theirs = theirs["loo_par_pct"].astype(float)
        mine = out["loo_par_pct"].to_numpy(dtype=float)
        other = out["isin"].astype(str).str.strip().map(theirs).to_numpy(dtype=float)
        loo_gap = (other - mine) * 100
    out["crosscheck_loo_diff_bp"] = loo_gap
    used = np.where(np.isfinite(loo_gap), loo_gap, gap)
    flags = np.isfinite(used) & (np.abs(used) > threshold)
    again = np.full(len(out), math.nan)
    notes = [""] * len(out)
    summary: list[str] = []
    suspects = suspect_bonds(out) if refit is not None and check_residuals is not None and flags.any() else []
    if suspects:
        isins = out["isin"].astype(str).str.strip().tolist()
        cleared = []
        for pos in np.flatnonzero(flags & np.isfinite(loo_gap)):
            left_out = sorted({isins[pos], *suspects})
            try:
                primary, check = refit(left_out)
            except ValueError as exc:
                notes[pos] = f"kept: the curves could not be refitted without {', '.join(left_out)} ({exc})"
                continue
            again[pos] = float(check.par_yield(years[pos]) - primary.par_yield(years[pos])) * 100
            if abs(again[pos]) > threshold:
                notes[pos] = f"kept: {again[pos]:+.2f} bp without the suspect bond(s) too"
            else:
                flags[pos] = False
                cleared.append(isins[pos])
                notes[pos] = f"cleared: {again[pos]:+.2f} bp without the suspect bond(s) {', '.join(suspects)}"
        summary.append(f"model risk judged again without the suspect bond(s) {', '.join(suspects)}: "
                       f"{len(cleared)} flag(s) cleared" + (f" ({', '.join(cleared)})" if cleared else ""))
    out["crosscheck_suspect_diff_bp"] = again
    out["model_risk"] = flags
    out["model_risk_note"] = notes
    out.attrs["notes"] = summary
    return out


def suspect_bonds(residuals: pd.DataFrame) -> list[str]:
    """The ISINs in the fit whose out-of-sample residual (``loo_resid_yield_bp``) exceeds both ``SUSPECT_BP`` and
    ``SUSPECT_SD`` robust standard deviations (1.4826 x the median absolute deviation) of the fit's out-of-sample
    residuals: the bonds that can sway a curve refitted without another one. Out of sample because an end bond's
    in-fit residual understates it (its leverage). Sorted; empty without ``loo_resid_yield_bp``."""
    if "loo_resid_yield_bp" not in residuals or "in_fit" not in residuals:
        return []
    inside = residuals[residuals["in_fit"].astype(bool)]
    r = inside["loo_resid_yield_bp"].to_numpy(dtype=float)
    ok = np.isfinite(r)
    sd = _robust_sd(r[ok])
    big = ok & (np.abs(np.where(ok, r, 0.0)) > SUSPECT_BP)
    if math.isfinite(sd):
        big &= np.abs(np.where(ok, r, 0.0)) > SUSPECT_SD * sd
    return sorted(inside["isin"].astype(str).str.strip()[big])


def refitter(frame: pd.DataFrame, settlement, settings: FitSettings,
             check_settings: FitSettings) -> Callable[[Sequence[str]], tuple[FittedCurve, FittedCurve]]:
    """For ``model_risk_flags(refit=...)``: a callable that fits both curves afresh (``fit_curve``, multi-start for
    NSS, the spline's penalty chosen again, no leave-one-out) to ``frame`` without the ISINs it is given and returns
    (primary curve, check curve). A fit it cannot make raises ``ValueError`` (the flag then stands)."""
    keys = frame["isin"].astype(str).str.strip()
    quick, quick_check = replace(settings, leave_one_out=False), replace(check_settings, leave_one_out=False)

    def refit(isins: Sequence[str]) -> tuple[FittedCurve, FittedCurve]:
        sub = frame[~keys.isin({str(i).strip() for i in isins})]
        return fit_curve(sub, settlement, quick).curve, fit_curve(sub, settlement, quick_check).curve

    return refit


def curve_spreads(curve: FittedCurve, pairs: Sequence[tuple[float, float]] = DEFAULT_PAIRS) -> pd.DataFrame:
    """Par-curve spreads ``long - short`` in bp (2s10s, 10s30s, ...), with whether both ends are in the data."""
    rows = []
    for short, long in pairs:
        ys, yl = (float(v) for v in curve.par_yield([short, long]))
        inside = bool(np.all(curve.in_data_range([short, long])))
        rows.append({"date": curve.valuation_date, "short_years": float(short), "long_years": float(long),
                     "short_par_pct": ys, "long_par_pct": yl, "spread_bp": (yl - ys) * 100,
                     "in_data_range": inside, "method": curve.method, "curve_id": curve.curve_id})
    return pd.DataFrame(rows)


def curve_butterflies(curve: FittedCurve, triples: Sequence[tuple[float, float, float]] = DEFAULT_FLIES
                      ) -> pd.DataFrame:
    """Par-curve butterflies ``2 y_body - y_short - y_long`` in bp (2s5s10s, 5s10s30s, ...)."""
    rows = []
    for short, body, long in triples:
        if not short < body < long:
            raise ValueError(f"a butterfly needs short < body < long tenors, not {(short, body, long)}")
        ys, yb, yl = (float(v) for v in curve.par_yield([short, body, long]))
        rows.append({"date": curve.valuation_date, "short_years": float(short), "body_years": float(body),
                     "long_years": float(long), "fly_bp": (2 * yb - ys - yl) * 100,
                     "in_data_range": bool(np.all(curve.in_data_range([short, body, long]))),
                     "method": curve.method, "curve_id": curve.curve_id})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- rich/cheap


def _panel(frame: pd.DataFrame, column: str, what: str) -> pd.DataFrame:
    missing = [c for c in ("date", "isin", column) if c not in frame]
    if missing:
        raise ValueError(f"{what} is missing columns {missing}")
    df = frame.copy()
    df["date"] = [as_date(d) for d in df["date"]]
    df["isin"] = df["isin"].astype(str).str.strip()
    df[column] = pd.to_numeric(df[column], errors="raise").astype(float)
    dup = df[df.duplicated(["date", "isin"], keep=False)]
    if len(dup):
        first = dup.iloc[0]
        raise ValueError(f"{what} has more than one row for ISIN {first['isin']} on {first['date']}")
    return df


def residual_zscores(panel: pd.DataFrame, window: int = ZSCORE_WINDOW, min_obs: int = ZSCORE_MIN_OBS, *,
                     column: str = "resid_yield_bp", bucket_edges: Sequence[float] = ZSCORE_BUCKETS) -> pd.DataFrame:
    """Time-series and cross-sectional z-scores of residuals in a panel (``date``, ``isin``, ``column``).

    ``z_ts = (r_t - mean) / sd`` of the same ISIN's previous ``window`` observations (rows with a finite
    residual, however far apart), ddof 1, NaN with fewer than ``min_obs`` of them or a zero sd; the current
    observation is never in its own window. ``z_cs = (r - median) / (1.4826 MAD)`` across the ISINs of the same
    date and maturity bucket (``years_to_maturity`` cut at ``bucket_edges``; one bucket without that column),
    NaN with fewer than three or a zero MAD. Sorted by (isin, date).
    """
    window = _count(window, "window", 1)
    min_obs = _count(min_obs, "min_obs", 1)
    if min_obs > window:
        raise ValueError(f"min_obs {min_obs} is above window {window}")
    df = _panel(panel, column, "the residual panel")
    df = df.sort_values(["isin", "date"], kind="stable").reset_index(drop=True)
    finite = np.isfinite(df[column].to_numpy(dtype=float))
    mean = np.full(len(df), math.nan)
    sd = np.full(len(df), math.nan)
    for idx in df[finite].groupby("isin", sort=True).groups.values():
        values = df.loc[idx, column]
        prior = values.shift(1)
        mean[idx] = prior.rolling(window, min_periods=min_obs).mean().to_numpy()
        sd[idx] = prior.rolling(window, min_periods=min_obs).std(ddof=1).to_numpy()
    # the finite observations of the same ISIN before each row (dates are unique per ISIN and sorted), capped at
    # the window: one pass, for rows with and without a residual alike
    seen = pd.Series(finite.astype(int)).groupby(df["isin"].to_numpy(), sort=False).cumsum().to_numpy() - finite
    n_prior = np.minimum(seen, window).astype(float)
    value = df[column].to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        z = np.where(sd > 0, (value - mean) / sd, math.nan)
    df["n_prior"] = n_prior.astype(int)
    df["prior_mean"], df["prior_sd"], df["z_ts"] = mean, sd, z
    edges = tuple(float(e) for e in bucket_edges)
    if "years_to_maturity" in df:
        years = pd.to_numeric(df["years_to_maturity"], errors="coerce")
        labels = [f"{_edge(a)}-{_edge(b)}y" for a, b in pairwise(edges)]
        df["bucket"] = pd.cut(years, list(edges), labels=labels, right=False).astype(object)
    else:
        df["bucket"] = "all"
    z_cs = np.full(len(df), math.nan)
    for idx in df[finite].groupby(["date", "bucket"], sort=True, dropna=True).groups.values():
        values = df.loc[idx, column].to_numpy(dtype=float)
        if values.size < 3:
            continue
        mad = 1.4826 * float(np.median(np.abs(values - np.median(values))))
        if mad > 0:
            z_cs[np.asarray(idx)] = (values - np.median(values)) / mad
    df["z_cs"] = z_cs
    return df


def _edge(value: float) -> str:
    return "inf" if math.isinf(value) else f"{value:g}"


HISTORY_COLUMNS = ("date", "isin", "years_to_maturity", "in_fit", "resid_yield_bp", "loo_resid_yield_bp", "leverage",
                   "method", "settings_id", "curve_id")
RESIDUAL_CHOICES = {"in_fit": "resid_yield_bp", "loo": "loo_resid_yield_bp"}


def _one_value(frame: pd.DataFrame, column: str, what: str) -> set[str]:
    if column not in frame:
        return set()
    return {str(v).strip() for v in frame[column] if not _blank(v) and str(v).strip()}


def _same_method(residuals: pd.DataFrame, history: pd.DataFrame) -> None:
    """Refuse a history made another way: z-scores across methods or settings compare different things."""
    for column in ("method", "settings_id"):
        mine, theirs = _one_value(residuals, column, "residuals"), _one_value(history, column, "history")
        if mine and theirs and theirs != mine:
            raise ValueError(f"the history's {column} is {sorted(theirs)}, today's residuals' {sorted(mine)}: score a "
                             "day against residuals fitted the same way (one history per method and settings)")


def append_history(history: pd.DataFrame | None, residuals: pd.DataFrame) -> pd.DataFrame:
    """``history`` with one day's residuals (``FitResult.residuals``) added in ``HISTORY_COLUMNS``, sorted by date
    and ISIN: the daily panel ``rich_cheap_table`` scores against. Rows of that day already in it are replaced (a
    refit of the day), and a history fitted with another method or settings (``settings_id``) is refused."""
    day = _panel(residuals, "resid_yield_bp", "the residuals")
    if len(set(day["date"])) != 1:
        raise ValueError("append one day's residuals at a time")
    new = pd.DataFrame({c: residuals[c] if c in residuals else None for c in HISTORY_COLUMNS})
    new["date"] = [as_date(d) for d in new["date"]]
    if history is None or not len(history):
        out = new
    else:
        old = history.copy()
        missing = [c for c in ("date", "isin", "resid_yield_bp") if c not in old]
        if missing:
            raise ValueError(f"the history lacks {missing} (columns: {', '.join(HISTORY_COLUMNS)})")
        _same_method(new, old)
        old["date"] = [as_date(d) for d in old["date"]]
        old = old[old["date"] != new["date"].iat[0]]
        out = pd.concat([old.reindex(columns=list(HISTORY_COLUMNS)), new], ignore_index=True)
    out = out.assign(_i=out["isin"].astype(str)).sort_values(["date", "_i"], kind="stable").drop(columns="_i")
    return out.reset_index(drop=True)


def rich_cheap_table(residuals: pd.DataFrame, history: pd.DataFrame | None = None, *, window: int = ZSCORE_WINDOW,
                     min_obs: int = ZSCORE_MIN_OBS, column: str = "resid_yield_bp") -> pd.DataFrame:
    """One day's residuals (``FitResult.residuals``) with z-scores against ``history``, a panel of earlier days.

    ``column``: the residual scored, ``resid_yield_bp`` (in the fit) or ``loo_resid_yield_bp`` (each bond against
    the curve refitted without it, which a bond near the ends of the data cannot pull towards itself). History on
    or after the residuals' date is refused (it would look ahead), and so is history fitted with another method or
    settings (``method``, ``settings_id``). Rows keep the residual table's order; ``n_prior``, ``prior_mean``,
    ``prior_sd``, ``z_ts``, ``bucket`` and ``z_cs`` are added; ``attrs["notes"]`` names the ISINs without history.
    """
    today = _panel(residuals, column, "the residuals")
    days = sorted(set(today["date"]))
    if len(days) != 1:
        raise ValueError(f"the residuals cover {len(days)} dates; score one day at a time")
    day = days[0]
    cols = ("date", "isin", column, "years_to_maturity")
    parts = [today[[c for c in cols if c in today]]]
    notes = []
    if history is not None and len(history):
        past = _panel(history, column, "the history")
        _same_method(residuals, past)
        late = past[past["date"] >= day]
        if len(late):
            raise ValueError(f"the history has {len(late)} row(s) on or after {day} (first: ISIN "
                             f"{late['isin'].iat[0]} on {late['date'].iat[0]}): it would look ahead")
        fresh = sorted(set(today["isin"]) - set(past["isin"]))
        if fresh:
            notes.append(f"{len(fresh)} ISIN(s) without history: " + ", ".join(fresh[:10])
                         + (" ..." if len(fresh) > 10 else ""))
        # only each ISIN's last `window` residuals can enter a z-score of the day: the rest is dropped before scoring
        past = past[np.isfinite(past[column].to_numpy(dtype=float))].sort_values(["isin", "date"], kind="stable")
        past = past.groupby("isin", sort=False).tail(int(window))
        parts.insert(0, past[[c for c in cols if c in past]])
    scored = residual_zscores(pd.concat(parts, ignore_index=True), window, min_obs, column=column)
    scored = scored[scored["date"] == day].set_index("isin")[list(ZSCORE_COLUMNS)]
    out = residuals.copy()
    key = out["isin"].astype(str).str.strip()
    for col in ZSCORE_COLUMNS:
        out[col] = key.map(scored[col]).to_numpy()
    out.attrs["notes"] = notes
    return out


def bond_butterflies(residuals: pd.DataFrame, triples: Sequence[tuple[str, str, str]], *,
                     body_face: float = 100.0) -> pd.DataFrame:
    """Butterflies of chosen ISIN triples (short, body, long) on each date where all three have residuals.

    ``yield_fly_bp = 100 (2 y_body - y_short - y_long)`` on market yields (positive: the body is cheap to its
    wings), ``model_fly_bp`` the same on model yields and ``resid_fly_bp = 2 r_body - r_short - r_long`` (=
    yield fly - model fly: what the curve does not explain). Wing faces are DV01-neutral, half the body's DV01
    each: ``F_wing = body_face DV01_body / (2 DV01_wing)``.
    """
    need = ("date", "isin", "maturity_date", "market_yield_pct", "model_yield_pct", "resid_yield_bp", "dv01")
    missing = [c for c in need if c not in residuals]
    if missing:
        raise ValueError(f"the residuals are missing columns {missing}")
    face = _positive(body_face, "body_face")
    data = residuals.copy()
    data["date"] = [as_date(d) for d in data["date"]]
    data["isin"] = data["isin"].astype(str).str.strip()
    rows = []
    for triple in triples:
        legs = tuple(str(i).strip() for i in triple)
        if len(legs) != 3 or len(set(legs)) != 3:
            raise ValueError(f"a butterfly needs three different ISINs (short, body, long), not {triple!r}")
        for day, group in data.groupby("date", sort=True):
            by = group.set_index("isin")
            if not all(i in by.index for i in legs):
                continue
            leg = [by.loc[i] for i in legs]
            if any(not np.isfinite(float(x[c])) for x in leg for c in need[3:]):
                continue
            maturities = [as_date(x["maturity_date"]) for x in leg]
            if not maturities[0] < maturities[1] < maturities[2]:
                raise ValueError(f"butterfly {legs}: maturities {maturities} are not short < body < long")
            ys, yb, yl = (float(x["market_yield_pct"]) for x in leg)
            ms, mb, ml = (float(x["model_yield_pct"]) for x in leg)
            rs, rb, rl = (float(x["resid_yield_bp"]) for x in leg)
            ds, db, dl = (float(x["dv01"]) for x in leg)
            rows.append({"date": day, "short_isin": legs[0], "body_isin": legs[1], "long_isin": legs[2],
                         "yield_fly_bp": (2 * yb - ys - yl) * 100, "model_fly_bp": (2 * mb - ms - ml) * 100,
                         "resid_fly_bp": 2 * rb - rs - rl, "body_face": face,
                         "short_face": face * db / (2 * ds), "long_face": face * db / (2 * dl)})
    return pd.DataFrame(rows, columns=list(BUTTERFLY_COLUMNS))


def bond_pairs(residuals: pd.DataFrame, pairs: Sequence[tuple[str, str]], *, long_face: float = 100.0) -> pd.DataFrame:
    """Spreads of chosen ISIN pairs (shorter, longer) on each date where both have residuals (``PAIR_COLUMNS``):
    S5's 10-year against a 30- to 50-year bond, or an S3 switch between neighbours.

    ``yield_spread_bp = 100 (y_long - y_short)`` on market yields, ``model_spread_bp`` the same on model yields and
    ``resid_spread_bp = r_long - r_short`` (= yield spread - model spread: positive when the longer bond is cheap to
    the shorter one against the curve). The shorter leg's face is DV01-neutral: ``F_short = F_long DV01_long /
    DV01_short``. Carry of each leg, the short one included, is ``funding.carry`` (``side="short"``).
    """
    need = ("date", "isin", "maturity_date", "market_yield_pct", "model_yield_pct", "resid_yield_bp", "dv01")
    missing = [c for c in need if c not in residuals]
    if missing:
        raise ValueError(f"the residuals are missing columns {missing}")
    face = _positive(long_face, "long_face")
    data = residuals.copy()
    data["date"] = [as_date(d) for d in data["date"]]
    data["isin"] = data["isin"].astype(str).str.strip()
    rows = []
    for pair in pairs:
        legs = tuple(str(i).strip() for i in pair)
        if len(legs) != 2 or len(set(legs)) != 2:
            raise ValueError(f"a pair needs two different ISINs (shorter, longer), not {pair!r}")
        for day, group in data.groupby("date", sort=True):
            by = group.set_index("isin")
            if not all(i in by.index for i in legs):
                continue
            leg = [by.loc[i] for i in legs]
            if any(not np.isfinite(float(x[c])) for x in leg for c in need[3:]):
                continue
            maturities = [as_date(x["maturity_date"]) for x in leg]
            if not maturities[0] < maturities[1]:
                raise ValueError(f"pair {legs}: maturities {maturities} are not shorter < longer")
            (ys, ms, rs, ds), (yl, ml, rl, dl) = [[float(x[c]) for c in need[3:]] for x in leg]
            rows.append({"date": day, "short_isin": legs[0], "long_isin": legs[1],
                         "yield_spread_bp": (yl - ys) * 100, "model_spread_bp": (ml - ms) * 100,
                         "resid_spread_bp": rl - rs, "long_face": face, "short_face": face * dl / ds})
    return pd.DataFrame(rows, columns=list(PAIR_COLUMNS))


# ---------------------------------------------------------------- what a residual still holds

DRIVER_COLUMNS = ("issue_date", "age_years", "far", "benchmark", "reissues", "last_issue_date", "days_since_issue",
                  "outstanding_cr", "log_stock", "stock_complete")
CONTROL_DRIVERS = ("coupon_pct", "age_years", "log_stock", "far")  # UNVERIFIED: the free part of the spec's list
CONTROL_MIN_DOF = 5  # UNVERIFIED: degrees of freedom a day's regression must keep after its coefficients
CONTROL_COLUMNS = ("date", "driver", "coef", "n", "r2", "note")
_BOOLEAN_DRIVERS = ("far", "benchmark", "stock_complete")


def _bool_or_none(value) -> bool | None:
    if _blank(value):
        return None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    text = str(value).strip().lower()
    return True if text in _TRUE else False if text in _FALSE else None


def _events_by_key(ledger: pd.DataFrame) -> dict[str, list[tuple[date, str]]]:
    need = ("key", "event", "date")
    missing = [c for c in need if c not in ledger]
    if missing:
        raise ValueError(f"the ledger is missing columns {missing} (issuance.LEDGER_COLUMNS)")
    out: dict[str, list[tuple[date, str]]] = {}
    for key, event, day in zip(ledger["key"], ledger["event"], ledger["date"]):
        if _blank(key) or _blank(day) or str(event) not in ("issue", "reissue"):
            continue
        out.setdefault(str(key).strip(), []).append((as_date(day), str(event)))
    return {k: sorted(v) for k, v in out.items()}


def residual_drivers(residuals: pd.DataFrame, master: pd.DataFrame | None = None, *,
                     ledger: pd.DataFrame | None = None, stock: pd.DataFrame | None = None) -> pd.DataFrame:
    """Each residual row with the drivers of richness that free data give, as of the row's date (``DRIVER_COLUMNS``
    added, and ``coupon_pct`` and ``issue_date`` filled from the master where the row has none): what a residual
    holds besides mispricing, beside it.

    From ``master`` (``sovereign.read_master``): ``issue_date`` and ``age_years`` (365.25-day years from it to the
    row's date) and ``far`` (the master's flag, from RBI's latest FAR list: not point in time, so a back-test day
    before that list can see a later designation). ``benchmark`` is the price file's ``benchmark`` flag. From
    ``ledger`` (``issuance.read_ledger``), events dated on or before the row's date only: ``reissues`` (reissue
    events), ``last_issue_date`` and ``days_since_issue`` (to the latest issue or reissue: a recent reissue
    cheapens a bond); its first issue stands in for a missing ``issue_date``. From ``stock``
    (``issuance.outstanding_on`` on the row's date; a row of another date is not used): ``outstanding_cr``,
    ``log_stock`` (natural log) and ``stock_complete`` (the ledger held every event in between). Ledger and stock
    rows join through their ``key``, the ISIN where known (SDLs the ledger keys by name do not join). A driver no
    input gives stays blank. Liquidity beyond the price file's trade counts, special repo and index inclusion need
    licensed or unsaved data (CCIL's trades and repo by security, the index providers' lists) and are not here.
    """
    for column in ("date", "isin"):
        if column not in residuals:
            raise ValueError(f"the residuals are missing column {column!r}")
    out = residuals.copy().reset_index(drop=True)
    days = [as_date(d) for d in out["date"]]
    isins = [str(i).strip() for i in out["isin"]]
    n = len(out)
    coupon: list[float | None] = [None] * n
    if "coupon_pct" in out:
        coupon = [_opt_float(v, f"residual row {k}", "coupon_pct") for k, v in enumerate(out["coupon_pct"])]
    issue: list[date | None] = [None] * n
    if "issue_date" in out:
        issue = [_opt_date(v, f"residual row {k}", "issue_date") for k, v in enumerate(out["issue_date"])]
    far: list[bool | None] = [None] * n
    if master is not None:
        if "isin" not in master:
            raise ValueError("the security master has no isin column")
        rows = {str(r["isin"]).strip(): r for r in master.to_dict("records") if not _blank(r.get("isin"))}
        for k, isin in enumerate(isins):
            rec = rows.get(isin)
            if rec is None:
                continue
            if coupon[k] is None:
                coupon[k] = _opt_float(rec.get("coupon_pct"), f"ISIN {isin} in the master", "coupon_pct")
            if issue[k] is None:
                issue[k] = _opt_date(rec.get("issue_date"), f"ISIN {isin} in the master", "issue_date")
            far[k] = _bool_or_none(rec.get("far"))
    reissues: list[float] = [math.nan] * n
    last_issue: list[date | None] = [None] * n
    if ledger is not None:
        events = _events_by_key(ledger)
        for k, isin in enumerate(isins):
            seen = [(d, e) for d, e in events.get(isin, ()) if d <= days[k]]
            if not seen:
                continue
            reissues[k] = float(sum(e == "reissue" for _, e in seen))
            last_issue[k] = seen[-1][0]
            firsts = [d for d, e in seen if e == "issue"]
            if issue[k] is None and firsts:
                issue[k] = firsts[0]
    held: dict[tuple[str, date], tuple[float, bool | None]] = {}
    if stock is not None:
        missing = [c for c in ("key", "date", "outstanding_cr") if c not in stock]
        if missing:
            raise ValueError(f"the stock table is missing columns {missing} (issuance.outstanding_on's output)")
        for rec in stock.to_dict("records"):
            if _blank(rec["key"]) or _blank(rec["date"]):
                continue
            complete = None
            if "issuance_complete" in rec and "operations_complete" in rec:
                a, b = _bool_or_none(rec["issuance_complete"]), _bool_or_none(rec["operations_complete"])
                complete = None if a is None or b is None else (a and b)
            where = f"the stock of {rec['key']} on {rec['date']}"
            value = _opt_float(rec["outstanding_cr"], where, "outstanding_cr")
            held[(str(rec["key"]).strip(), as_date(rec["date"]))] = (math.nan if value is None else value, complete)
    flags = [_flag_set(v) for v in out["flags"]] if "flags" in out else [set()] * n
    stock_cr = [held.get((isins[k], days[k]), (math.nan, None)) for k in range(n)]
    if "coupon_pct" in out or master is not None:
        out["coupon_pct"] = [math.nan if c is None else c for c in coupon]
    out["issue_date"] = issue
    out["age_years"] = [(days[k] - issue[k]).days / 365.25 if issue[k] is not None else math.nan for k in range(n)]
    out["far"] = pd.array(far, dtype="boolean")
    out["benchmark"] = ["benchmark" in f for f in flags]
    out["reissues"] = reissues
    out["last_issue_date"] = last_issue
    out["days_since_issue"] = [float((days[k] - last_issue[k]).days) if last_issue[k] is not None else math.nan
                               for k in range(n)]
    out["outstanding_cr"] = [v for v, _ in stock_cr]
    out["log_stock"] = [math.log(v) if v > 0 else math.nan for v, _ in stock_cr]
    out["stock_complete"] = pd.array([c for _, c in stock_cr], dtype="boolean")
    return out


def _flag_set(value) -> set[str]:
    return {f.strip().lower() for f in str(value or "").split(";") if f.strip()} if not _blank(value) else set()


def _driver_values(series: pd.Series, name: str) -> np.ndarray:
    if name in _BOOLEAN_DRIVERS:
        return np.array([math.nan if (b := _bool_or_none(v)) is None else float(b) for v in series])
    return pd.to_numeric(series, errors="coerce").to_numpy(dtype=float)


def control_residuals(frame: pd.DataFrame, drivers: Sequence[str] = CONTROL_DRIVERS, *,
                      column: str = "resid_yield_bp", min_dof: int = CONTROL_MIN_DOF) -> pd.DataFrame:
    """The residual less what its drivers explain across the day's bonds (``resid_ctrl_bp`` added), from
    ``residual_drivers``'s frame.

    For each date, the in-fit rows with ``column`` and every driver known regress ``column`` on an intercept and
    ``drivers`` by least squares (``far``, ``benchmark``, ``stock_complete`` as 0/1); every row of that date with its
    drivers known gets ``resid_ctrl_bp = column - fitted``. A day with fewer such rows than the drivers plus one
    plus ``min_dof`` is not regressed and its rows stay blank; a driver that does not vary among the day's rows is
    dropped from its regression. ``attrs["coefficients"]`` holds each day's coefficients (``CONTROL_COLUMNS``) and
    ``attrs["notes"]`` what was dropped or skipped. UNVERIFIED as a control: linear, one day at a time, equal
    weights, the drivers of ``CONTROL_DRIVERS`` by default (the spec asks for controls, not this form). With 20 to
    60 bonds a day a few drivers absorb any true richness that lines up with them: read the controlled residual
    beside the raw one, not instead of it.
    """
    names = tuple(drivers)
    if not names or len(set(names)) != len(names):
        raise ValueError(f"drivers must be distinct column names, not {drivers!r}")
    missing = [c for c in ("date", column, *names) if c not in frame]
    if missing:
        raise ValueError(f"the frame is missing columns {missing}: join the drivers with residual_drivers first")
    if isinstance(min_dof, bool) or not isinstance(min_dof, (int, np.integer)) or min_dof < 1:
        raise ValueError(f"min_dof must be a whole number of at least 1, not {min_dof!r}")
    out = frame.copy().reset_index(drop=True)
    x = np.column_stack([_driver_values(out[c], c) for c in names])
    y = pd.to_numeric(out[column], errors="coerce").to_numpy(dtype=float)
    fit_rows = (np.array([bool(_bool_or_none(v)) for v in out["in_fit"]]) if "in_fit" in out
                else np.ones(len(out), dtype=bool))
    known = np.isfinite(x).all(axis=1) & np.isfinite(y)
    days = np.array([as_date(d) for d in out["date"]], dtype=object)
    ctrl = np.full(len(out), math.nan)
    coefficients, notes = [], []
    for day in sorted(set(days)):
        today = days == day
        use = today & known & fit_rows
        need = len(names) + 1 + int(min_dof)
        if use.sum() < need:
            notes.append(f"{day}: {int(use.sum())} in-fit row(s) with every driver known, fewer than the {need} "
                         f"that {len(names)} driver(s), an intercept and min_dof={min_dof} need: not controlled")
            continue
        keep = [j for j in range(len(names)) if np.ptp(x[use, j]) > 0]
        dropped = [names[j] for j in range(len(names)) if j not in keep]
        design = np.column_stack([np.ones(int(use.sum())), x[use][:, keep]])
        beta, *_ = np.linalg.lstsq(design, y[use], rcond=None)
        resid = y[use] - design @ beta
        total = float(((y[use] - y[use].mean()) ** 2).sum())
        r2 = 1.0 - float((resid ** 2).sum()) / total if total > 0 else math.nan
        apply = today & known
        ctrl[apply] = y[apply] - np.column_stack([np.ones(int(apply.sum())), x[apply][:, keep]]) @ beta
        count = int(use.sum())
        coefficients.append({"date": day, "driver": "intercept", "coef": float(beta[0]), "n": count, "r2": r2,
                             "note": ""})
        coefficients += [{"date": day, "driver": names[j], "coef": float(b), "n": count, "r2": r2, "note": ""}
                         for j, b in zip(keep, beta[1:])]
        coefficients += [{"date": day, "driver": name, "coef": math.nan, "n": count, "r2": r2,
                          "note": "the same for every row of the day: dropped"} for name in dropped]
        if dropped:
            notes.append(f"{day}: {', '.join(dropped)} the same for every in-fit row: dropped from the regression")
    out["resid_ctrl_bp"] = ctrl
    out.attrs["coefficients"] = pd.DataFrame(coefficients, columns=list(CONTROL_COLUMNS))
    out.attrs["notes"] = notes
    return out


# ---------------------------------------------------------------- generic price files


def prepare_prices(prices: pd.DataFrame, master: pd.DataFrame | None = None) -> pd.DataFrame:
    """A generic price file joined to a security master: the frame ``fit_curve`` reads.

    ``prices``: one row per ISIN with ``isin`` and a ``clean_price`` or ``yield_pct`` (aliases: price, yield,
    ytm), and optionally ``kind``, ``coupon_pct``, ``maturity_date``, ``issue_date``, ``first_coupon_date``,
    ``weight``, ``flags``, ``traded``, ``trade_count``, ``volume_cr``, ``settlement_date``. ``master``
    (``sovereign.read_master``) fills ``kind``, ``coupon_pct``, ``maturity_date`` and ``issue_date`` where the
    price file leaves them blank; a value in both that differs raises ``ValueError`` naming the ISIN. A row whose
    kind neither gives (the master's ``unknown``, an unparsed name) gets ``kind = "unknown"``: the fit keeps it out
    with ``excluded_reason = "no_kind"`` and names it in the warnings. A loader for a licensed trade file (CCIL's
    trades by security) only has to produce these columns; valuation sheets (FBIL's) are a cross-check, not fit
    input: a curve fitted to them recovers FBIL's model.
    """
    df = prices.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    for alias, name in PRICE_ALIASES.items():
        if alias in df:
            if name in df:
                raise ValueError(f"the price file has both {alias!r} and {name!r}: keep one")
            df = df.rename(columns={alias: name})
    if "isin" not in df:
        raise ValueError("the price file needs an isin column")
    if not any(c in df for c in QUOTE_COLUMNS):
        raise ValueError("the price file needs a clean_price or a yield_pct column")
    df = df.astype(object).where(df.notna(), None)
    df["isin"] = [_text(v) for v in df["isin"]]
    for col in MASTER_FIELDS:
        if col not in df:
            df[col] = None
    if master is not None and len(master):
        m = master.copy()
        m["isin"] = [_text(v) for v in m["isin"]]
        dup = m["isin"][m["isin"].duplicated()].unique()
        if len(dup):
            raise ValueError(f"the security master lists ISINs more than once: {sorted(dup)[:10]}")
        m = m.set_index("isin")
        for pos in range(len(df)):
            isin = df["isin"].iat[pos]
            if isin not in m.index:
                continue
            for col in MASTER_FIELDS:
                if col not in m:
                    continue
                theirs = m.at[isin, col]
                if _blank(theirs) or (col == "kind" and str(theirs) == "unknown"):
                    continue
                ours = df[col].iat[pos]
                if _blank(ours):
                    df.iloc[pos, df.columns.get_loc(col)] = theirs
                elif not _same(col, ours, theirs, isin):
                    raise ValueError(f"ISIN {isin}: {col} is {ours!r} in the price file but {theirs!r} in the "
                                     "security master")
    for pos in range(len(df)):
        if not df["isin"].iat[pos]:
            raise ValueError(f"row {pos} of the price file has no isin")
        if _blank(df["kind"].iat[pos]):
            df.iloc[pos, df.columns.get_loc("kind")] = NO_KIND  # out of the fit (no_kind), named in the warnings
    return df.reset_index(drop=True)


def _same(col: str, a, b, isin: str) -> bool:
    where = f"ISIN {isin}"
    if col in ("maturity_date", "issue_date"):
        return _opt_date(a, where, col) == _opt_date(b, where, col)
    if col == "coupon_pct":
        return abs(_opt_float(a, where, col) - _opt_float(b, where, col)) < 1e-9
    return _text(a).lower() == _text(b).lower()
