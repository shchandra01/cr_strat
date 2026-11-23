"""Asset-swap measures of G-secs, SDLs and bills against an OIS curve (strategy S1), or against any discount curve.

The S1 trade buys a G-sec (or SDL) and pays fixed in MIBOR-OIS: the spread it earns can be measured four ways,
and they differ by up to 2 bp on one bond (the 6.36% GS 2031 of the tests: 33.9 to 35.9 bp), so a strategy must
name the one it trades on. No Indian primary source defines a G-sec/OIS asset-swap spread (UNVERIFIED); desks
quote the five-year benchmark's yield less the five-year OIS rate (market reports, via search), and the
definitions below are the standard ones (O'Kane and Sen, "Credit Spreads Explained", 2005; not re-read here).
Every number is per 100 face, so invariant to the notional.

Notation: settlement ``s``; ``P`` the dirty price (clean plus accrued interest on the bond's own day count, from
``bonds``); the bond's cash flows ``CF_i`` on their scheduled dates ``t_i`` (``FixedRateBond.cash_flows``: nothing
here builds a cash flow); ``D(x) = DF(x) / DF(s)`` the curve's discount factor re-based to settlement. The floating
leg of the asset swap runs on the bond's scheduled coupon dates, its first period from ``s`` to the next coupon (a
short stub), with Act/365F year fractions ``tau_j`` (C24 of the analytics spec, UNVERIFIED: no Indian convention
found); ``A = sum_j tau_j D(e_j)`` is its annuity.

* ``yield_yield_bp = 100 (y - K_match)``: the bond's yield to maturity (``bonds``: semi-annual for a G-sec or SDL,
  simple for one flow left and for bills) less the par rate of an OIS on the asset swap's schedule,
  ``K_match = 100 (1 - D(T)) / A``. That rate compounds as the bond's yield does: semi-annually with two coupon
  dates or more, simply with one (then ``K_match = (1/D(T) - 1) / tau``, the single-period OIS rate). The spec
  proposed, for a bond of one year or less, the single-period OIS rate converted to semi-annual compounding. On
  bills priced exactly on the curve that rule gives -6.4 to +8.3 bp (35 to 364 days, the tests' curve) where this
  one gives 0; on G-secs in their last coupon period, whose yield ``bonds`` gives as a simple rate on 30E/360, the
  two give 2.4 to -4.8 bp and -3.2 to -5.8 bp. So the uniform formula is used (a decision for the user). Even so a
  bond priced on the curve shows a yield-yield spread (its 30E/360 day count, its coupon: 0.03 to 2.2 bp beyond a
  year in those runs) where par-par and z-spreads are zero. The desks' quote is the case of a five-year bond
  against a standard swap.
* ``par_par_bp``: buy the bond for par, pay its coupons and receive the floating leg plus a spread on 100:
  ``A_pp = (sum_i CF_i D(t_i) - P) / (100 A)``.
* ``proceeds_bp`` (market-value asset swap): the floating notional is the dirty price, ``A_mv = A_pp 100 / P``.
* ``z_spread_bp``: the parallel shift of the curve's continuously compounded zero rates that prices the bond,
  ``P = sum_i CF_i D(t_i) exp(-z t_i)``, ``t_i`` Act/365F years from ``s`` (C25, UNVERIFIED: continuous is the
  default); ``z_spread_sa_bp`` shifts semi-annual zero rates instead,
  ``P = sum_i CF_i (1 + (z2_i + z) / 2)^(-2 t_i)``, ``z2_i = 2 (D(t_i)^(-1 / (2 t_i)) - 1)``.

``hedge_notional`` gives the OIS notional whose DV01 matches the bond's: ``N = F DV01_bond / 100 /
(sum_k tau_k D(E_k) 1e-4)`` (the swap's annuity DV01 per rupee).

The curve is any ``DiscountCurveLike`` (``valuation_date``, ``t_max``, ``discount``): an ``ois.OisCurve`` for S1,
or ``curvefit``'s fitted G-sec curve, against which the same z-spread is a bond's spread to the sovereign curve. A
bond maturing past an OIS curve's last node (5 years for FBIL's) is refused unless the curve allows extrapolation,
and then ``extrapolated`` says so; a fitted curve extrapolates on its own and is flagged the same way.

**Limits.** Tested on synthetic curves and closed forms only: no OIS quote, bond price or published asset-swap
example with full inputs was used (FBIL's OIS rates need a licence; the FINCAD example the spec looked for was not
read). Bonds without a pricer in ``bonds`` (floating-rate, inflation-indexed) and STRIPS are refused; no
convexity or funding adjustment is made, and the bond's settlement is taken to be the swap's effective date. A
settlement that is not a business day of the curve's calendar (Mumbai's when the curve has none; weekdays past the
calendar's last listed year are accepted) is refused, as is an ISIN listed twice in ``asset_swap_table``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from cr_strat.bonds import DiscountInstrument, FixedRateBond, ZeroCouponBond
from cr_strat.calendars import BusinessCalendar, calendar_for
from cr_strat.conventions import CONVENTIONS, BondConventions, DiscountConventions, as_date, convert_yield
from cr_strat.corporates import (
    CorporateBond,
    corporate_from_terms,
    read_amortisation,
    read_corporate_options,
    read_corporate_schedule,
    read_corporate_terms,
)
from cr_strat.floating import (
    FRB_COUPONS_CSV,
    GoiFloatingRateBond,
    discount_margin_bp,
    goi_frb_bond,
    goi_frb_rates,
    index_dv01,
    load_frb_terms,
    spread_dv01,
    tbill_way_table,
)
from cr_strat.ois import MIBOR_OIS, OisConventions, ois_schedule

FLOAT_SCHEDULES = ("bond_coupons",)
ASW_COLUMNS = ("isin", "kind", "coupon_pct", "maturity_date", "settlement_date", "clean_price", "accrued",
               "dirty_price", "ytm_pct", "modified_duration", "dv01", "years_to_maturity", "pv_on_curve", "annuity",
               "k_match_pct", "yield_yield_bp", "par_par_bp", "proceeds_bp", "z_spread_bp", "z_spread_sa_bp",
               "extrapolated", "calendar_projected", "error")
TABLE_KINDS = ("gsec", "sdl", "green", "special", "tbill", "cmb", "cp", "cd", "corporate")
K_MATCH_BASES = ("schedule", "bond")
FRB_ASW_COLUMNS = ("isin", "name", "settlement_date", "clean_price", "accrued", "dirty_price", "frb_yield_pct",
                   "index_pct", "dm_index_bp", "dm_curve_bp", "z_spread_sa_bp", "spread_dv01", "index_dv01",
                   "years_to_maturity", "coupon_pct", "coupon_status", "next_coupon_pct", "next_status",
                   "extrapolated", "calendar_projected", "notes", "error")
_BASIS_OF = {1: "annual", 2: "semiannual", 4: "quarterly", 12: "monthly"}
_KIND_ALIASES = {"green": "gsec", "special": "gsec"}
_YEAR = 365.0


@dataclass(frozen=True)
class AssetSwapMeasures:
    """A bond's asset-swap measures against one curve, per 100 face (see the module docstring)."""

    settlement: date
    maturity: date
    clean_price: float
    accrued: float
    dirty_price: float
    ytm_pct: float
    modified_duration: float
    dv01: float
    years_to_maturity: float
    pv_on_curve: float
    annuity: float
    k_match_pct: float
    yield_yield_bp: float
    par_par_bp: float
    proceeds_bp: float
    z_spread_bp: float
    z_spread_sa_bp: float
    extrapolated: bool
    calendar_projected: bool


def _flows(bond, s: date) -> tuple[list[date], np.ndarray]:
    """Scheduled dates and amounts (per 100) of the payments after ``s``, one per date. A corporate bond's flows fall
    on the dates it is priced on: due dates, or payment dates for a settlement before SEBI's due-date rule
    (``CorporateBond.uses_due_dates``)."""
    if isinstance(bond, DiscountInstrument):
        bond.days_to_maturity(s)  # refuses a settlement on or after maturity
        return [bond.maturity], np.array([100.0])
    due = bond.uses_due_dates(s) if isinstance(bond, CorporateBond) else True
    by_date: dict[date, float] = {}
    for flow in bond.cash_flows(s):
        when = flow.date if due else flow.payment_date
        by_date[when] = by_date.get(when, 0.0) + flow.amount
    dates = sorted(by_date)
    return dates, np.array([by_date[d] for d in dates])


def _check_bond(bond) -> None:
    if isinstance(bond, GoiFloatingRateBond):
        raise ValueError("an FRB's coupons reset with its index: it has no fixed-coupon asset swap; measure its "
                         "discount margin instead (asset_swaps.frb_measures, floating.discount_margin_bp)")
    if isinstance(bond, ZeroCouponBond):
        raise ValueError("STRIPS are not measured: a zero-coupon bond's asset swap has no coupon schedule to float on")
    if not isinstance(bond, (FixedRateBond, DiscountInstrument, CorporateBond)):
        raise TypeError(f"bond must be a bonds.FixedRateBond (G-sec, SDL), DiscountInstrument (T-bill, CMB, CP, CD) "
                        f"or corporates.CorporateBond, not {type(bond).__name__}")


def _k_on_bond_basis(bond: CorporateBond, k_pct: float, s: date, dates: Sequence[date]) -> float:
    """``K_match`` (simple per floating period) restated to the corporate bond's yield basis: unchanged when the bond's
    yield is simple (one flow under the money-market rule), from the coupon frequency's compounding to the bond's
    otherwise (``convert_yield``; one period of ``tau`` years: ``(1 + K tau)^(1/tau) - 1`` first)."""
    flows = bond._flows(s)
    if flows.simple:
        return k_pct
    basis = bond.conventions.compounding
    if len(dates) == 1 or not bond.frequency:
        tau = (dates[-1] - s).days / _YEAR
        annual = ((1 + k_pct / 100 * tau) ** (1 / tau) - 1) * 100
        return float(convert_yield(annual, "annual", basis))
    return float(convert_yield(k_pct, _BASIS_OF[bond.frequency], basis))


def _rebased(curve, s: date, dates: Sequence[date], what: str) -> tuple[np.ndarray, bool]:
    """``D(x) = DF(x) / DF(s)`` on ``dates``, and whether any lies past the curve's last node."""
    if s < curve.valuation_date:
        raise ValueError(f"{what}: settlement {s} is before the curve's valuation date {curve.valuation_date}")
    values = np.asarray(curve.discount([s, *dates]), dtype=float).reshape(-1)
    bad = [d for d, v in zip([s, *dates], values) if not (math.isfinite(v) and v > 0)]
    if bad:
        allowed = getattr(curve, "max_extrapolation_years", 0.0)
        raise ValueError(f"{what}: the payment of {bad[0]} is beyond the curve ({curve.t_max:.4f} years from "
                         f"{curve.valuation_date}, {allowed:g} years of extrapolation allowed); give longer OIS "
                         "quotes or allow extrapolation")
    last = max(dates)
    extrapolated = (last - curve.valuation_date).days / _YEAR > curve.t_max + 1e-12
    return values[1:] / values[0], extrapolated


def _solve(price, target: float, lo: float, hi: float) -> float:
    """Root of the decreasing function ``price(z) = target`` (z decimal): Newton inside a bracket, else bisection."""
    for _ in range(80):
        if price(lo)[0] > target:
            break
        lo = lo - (hi - lo)
    else:
        raise ValueError(f"no spread prices the bond at {target}")
    for _ in range(80):
        if price(hi)[0] < target:
            break
        hi = hi + (hi - lo)
    else:
        raise ValueError(f"no spread prices the bond at {target}")
    z = 0.0 if lo < 0.0 < hi else 0.5 * (lo + hi)
    for _ in range(200):
        value, slope = price(z)
        diff = value - target
        if diff == 0:
            return z
        if diff > 0:
            lo = z
        else:
            hi = z
        step = z - diff / slope if slope < 0 and math.isfinite(slope) else math.nan
        nxt = step if lo < step < hi else 0.5 * (lo + hi)
        if abs(nxt - z) < 1e-15:
            return nxt
        z = nxt
    raise RuntimeError("the spread did not converge")  # pragma: no cover


def z_spread(dates: Sequence[date], amounts, settlement, dirty_price: float, curve, *,
             compounding: str = "continuous") -> float:
    """The z-spread (decimal) of flows ``amounts`` on ``dates`` worth ``dirty_price`` at ``settlement`` on ``curve``.

    ``compounding="continuous"`` shifts continuously compounded zero rates, ``"semiannual"`` semi-annual ones
    (module docstring).
    """
    if compounding not in ("continuous", "semiannual"):
        raise ValueError(f"compounding must be 'continuous' or 'semiannual', not {compounding!r}")
    s = as_date(settlement)
    dates = [as_date(d) for d in dates]
    cf = np.asarray(amounts, dtype=float)
    if len(dates) != cf.size or not cf.size or not np.all(np.isfinite(cf)) or (cf <= 0).any():
        raise ValueError("the flows need one positive amount per date")
    d, _ = _rebased(curve, s, dates, "z-spread")
    t = np.array([(x - s).days for x in dates], dtype=float) / _YEAR
    if (t <= 0).any():
        raise ValueError("every flow must fall after settlement")
    target = float(dirty_price)
    if not (math.isfinite(target) and target > 0):
        raise ValueError(f"dirty price must be a positive number, not {dirty_price!r}")
    if compounding == "continuous":
        def price(z: float) -> tuple[float, float]:
            v = cf * d * np.exp(-z * t)
            return float(v.sum()), float(-(v * t).sum())
        return _solve(price, target, -0.05, 0.05)
    z2 = 2.0 * (d ** (-1.0 / (2.0 * t)) - 1.0)
    floor = float((-2.0 - z2).max())  # 1 + (z2 + z) / 2 must stay above zero

    def price(z: float) -> tuple[float, float]:
        base = 1.0 + (z2 + z) / 2.0
        if (base <= 0).any():
            return math.inf, -math.inf
        v = cf * base ** (-2.0 * t)
        return float(v.sum()), float(-(v * t / base).sum())
    return _solve(price, target, max(-0.05, floor + 1e-9), 0.05)


def _period_faces(bond, s: date, dates: Sequence[date]) -> np.ndarray:
    """Face outstanding (per 100 original) in each floating period ending on ``dates``: 100 for every bond but an
    amortising corporate bond, whose asset swap's notional amortises with it."""
    if not isinstance(bond, CorporateBond) or not bond.amortisation:
        return np.full(len(dates), 100.0)
    due = bond.uses_due_dates(s)
    face_at = {}
    for p in bond.periods():
        face_at.setdefault(p.due if due else p.payment, p.face)
    return np.array([face_at.get(d, bond.outstanding_face(s)) for d in dates], dtype=float)


def asset_swap(bond, settlement, clean_price: float, curve, *, float_schedule: str = "bond_coupons",
               k_match_basis: str | None = None) -> AssetSwapMeasures:
    """Asset-swap measures of ``bond`` bought at ``clean_price`` (per 100; a bill's price) for ``settlement``.

    ``bond``: a ``bonds.FixedRateBond`` (G-sec, SDL), ``DiscountInstrument`` (T-bill, CMB, CP, CD) or
    ``corporates.CorporateBond`` (an NCD: its flows on the dates it is priced on, an amortising bond's floating
    notional amortising with it; an FRB is refused: ``frb_measures``). ``curve``: any ``DiscountCurveLike``.
    ``float_schedule``: the asset swap's floating schedule (only ``"bond_coupons"``). ``k_match_basis``:
    ``schedule`` (the matched OIS rate as a par rate on the floating schedule, the default for G-secs and bills)
    or ``bond`` (restated to the bond's own yield basis before the yield-yield spread: annual compounding for an
    NCD, its default; a simple rate under the money-market rule is left simple).
    """
    _check_bond(bond)
    if float_schedule not in FLOAT_SCHEDULES:
        raise ValueError(f"float_schedule must be one of {FLOAT_SCHEDULES}, not {float_schedule!r}")
    basis = k_match_basis if k_match_basis is not None else ("bond" if isinstance(bond, CorporateBond) else
                                                             "schedule")
    if basis not in K_MATCH_BASES:
        raise ValueError(f"k_match_basis must be one of {K_MATCH_BASES}, not {k_match_basis!r}")
    s = as_date(settlement)
    clean = float(clean_price)
    if not (math.isfinite(clean) and clean > 0):
        raise ValueError(f"clean price must be a positive number, not {clean_price!r}")
    cal = getattr(curve, "calendar", None) or calendar_for("mumbai_settlement")
    if not (cal if cal.covers(s) else cal.lenient()).is_business_day(s):
        raise ValueError(f"settlement {s} is not a business day ({'a weekend' if s.weekday() >= 5 else 'a holiday'} "
                         f"on calendar {cal.name!r}): bonds and bills settle on business days")
    dates, cf = _flows(bond, s)
    what = f"the bond maturing {bond.maturity}"
    d, extrapolated = _rebased(curve, s, dates, what)
    if isinstance(bond, DiscountInstrument):
        accrued, dirty = 0.0, clean
        ytm = bond.yield_from_price(clean, s)
        duration, dv01 = bond.modified_duration(ytm, s), bond.dv01(ytm, s)
    else:
        a = bond.analytics(s, clean_price=clean)
        accrued, dirty, ytm, duration, dv01 = (a.accrued_interest, a.dirty_price, a.yield_pct, a.modified_duration,
                                               a.dv01)
    pv = float(cf @ d)
    starts = [s, *dates[:-1]]
    tau = np.array([(e - b).days for b, e in zip(starts, dates)], dtype=float) / _YEAR
    faces = _period_faces(bond, s, dates)
    if (faces == 100.0).all():
        annuity = float(tau @ d)
        k_match = float((1.0 - d[-1]) / annuity * 100)
    else:  # an amortising notional: the par rate of an OIS whose notional follows the bond's face
        d_start = np.r_[1.0, d[:-1]]
        annuity = float((tau * faces / 100) @ d)
        k_match = float((faces / 100) @ (d_start - d) / annuity * 100)
    if basis == "bond" and isinstance(bond, CorporateBond):  # a G-sec's or bill's rate is on its own basis already
        k_match = _k_on_bond_basis(bond, k_match, s, dates)
    par_par = (pv - dirty) / (100.0 * annuity)
    return AssetSwapMeasures(
        settlement=s, maturity=bond.maturity, clean_price=clean, accrued=accrued, dirty_price=dirty, ytm_pct=ytm,
        modified_duration=duration, dv01=dv01, years_to_maturity=(bond.maturity - s).days / _YEAR, pv_on_curve=pv,
        annuity=annuity, k_match_pct=k_match, yield_yield_bp=(ytm - k_match) * 100, par_par_bp=par_par * 1e4,
        proceeds_bp=par_par * 1e4 * 100.0 / dirty,
        z_spread_bp=z_spread(dates, cf, s, dirty, curve) * 1e4,
        z_spread_sa_bp=z_spread(dates, cf, s, dirty, curve, compounding="semiannual") * 1e4,
        extrapolated=extrapolated, calendar_projected=bool(getattr(curve, "calendar_projected", False)))


def hedge_notional(bond, settlement, clean_price: float, curve, months, *, face: float = 100.0,
                   conventions: OisConventions | None = None, calendar: BusinessCalendar | None = None) -> float:
    """OIS notional (rupees) whose DV01 matches ``face`` rupees of ``bond``: ``F DV01 / 100 / (annuity x 1e-4)``.

    The swap starts at the bond's settlement (a business day: the spot swap's effective date when both trade the
    same day) and runs ``months`` (or a tenor) on its conventions; its annuity is re-based to settlement, as the
    bond's DV01 (``bonds``, at the market price) is a settlement-date value.
    """
    _check_bond(bond)
    s = as_date(settlement)
    f = float(face)
    if not (math.isfinite(f) and f > 0):
        raise ValueError(f"face must be a positive number, not {face!r}")
    clean = float(clean_price)
    if isinstance(bond, DiscountInstrument):
        dv01 = bond.dv01(bond.yield_from_price(clean, s), s)
    else:
        dv01 = bond.analytics(s, clean_price=clean).dv01
    conv = conventions if conventions is not None else getattr(curve, "conventions", MIBOR_OIS)
    cal = calendar if calendar is not None else getattr(curve, "calendar", None)
    periods = ois_schedule(s, months, conv, cal)
    d, _ = _rebased(curve, s, [p.end for p in periods], f"the hedge swap from {s}")
    annuity = float(sum(p.year_fraction * v for p, v in zip(periods, d)))
    return f * dv01 / 100.0 / (annuity * 1e-4)


def _blank(value) -> bool:
    """True for None, NaN, NaT and empty text."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def instrument_from_row(rec: dict, where: str = "row"):
    """A ``bonds`` instrument from a row (``kind``, ``maturity_date``, ``coupon_pct``, optional ``issue_date`` and
    ``first_coupon_date``) and its kind: G-secs, green bonds, special securities and SDLs as ``FixedRateBond``,
    bills, CP and CD as ``DiscountInstrument`` (``TABLE_KINDS``); a ``corporate`` row is read as a row of the
    corporate terms table (``corporates.corporate_from_terms``: ``issue_date`` and ``frequency`` too, payment and
    record dates on the default lenient settlement calendar)."""
    kind = str(rec.get("kind") or "gsec").strip().lower()
    if kind not in TABLE_KINDS:
        raise ValueError(f"{where}: kind {kind!r} has no pricer here (one of {TABLE_KINDS})")
    if kind == "corporate":
        return corporate_from_terms({k: v for k, v in rec.items() if not _blank(v)} | {"kind": "corporate"},
                                    where=where), kind
    conv = CONVENTIONS[_KIND_ALIASES.get(kind, kind)]
    if _blank(rec.get("maturity_date")):
        raise ValueError(f"{where}: maturity_date is missing")
    maturity = as_date(rec["maturity_date"])
    if isinstance(conv, DiscountConventions):
        return DiscountInstrument(maturity, conv), kind
    assert isinstance(conv, BondConventions)
    coupon = rec.get("coupon_pct")
    if _blank(coupon):
        raise ValueError(f"{where}: a {kind} needs coupon_pct")

    def optional(name: str):
        value = rec.get(name)
        return None if _blank(value) else as_date(value)

    return FixedRateBond(float(coupon), maturity, conv, issue_date=optional("issue_date"),
                         first_coupon_date=optional("first_coupon_date")), kind


MASTER_FIELDS = ("kind", "coupon_pct", "maturity_date", "issue_date")


def fill_from_master(frame: pd.DataFrame, master: pd.DataFrame) -> pd.DataFrame:
    """``frame`` with blank ``MASTER_FIELDS`` filled from a security master by ISIN (``sovereign.read_master``).

    A value given in both that differs is refused, naming the ISIN; an ISIN the master lacks keeps what it has.
    """
    if "isin" not in frame or "isin" not in master:
        raise ValueError("both the bonds and the security master need an isin column")
    lookup = {str(r["isin"]).strip(): r for r in master.to_dict("records") if not _blank(r.get("isin"))}
    out = frame.copy()
    for column in MASTER_FIELDS:
        if column not in out:
            out[column] = None
        out[column] = out[column].astype(object)
    for k, rec in zip(out.index, out.to_dict("records")):
        row = lookup.get(str(rec["isin"]).strip())
        if row is None:
            continue
        for column in MASTER_FIELDS:
            given, known = rec.get(column), row.get(column)
            if _blank(known):
                continue
            if _blank(given):
                out.at[k, column] = known
            elif not _same(given, known):
                raise ValueError(f"bond {rec['isin']}: {column} is {given!r} in the bonds but {known!r} in the "
                                 "security master")
    return out


def _same(a, b) -> bool:
    try:
        return math.isclose(float(a), float(b), rel_tol=0, abs_tol=1e-9)
    except (TypeError, ValueError):
        pass
    try:
        return as_date(a) == as_date(b)
    except (TypeError, ValueError):
        return str(a).strip().lower() == str(b).strip().lower()


def asset_swap_table(frame: pd.DataFrame, curve, settlement=None, *, terms=None, schedule=None, options=None,
                     amortisation=None) -> pd.DataFrame:
    """``asset_swap`` for every row of ``frame``: one row per bond (``ASW_COLUMNS``), sorted by maturity and ISIN.

    Columns: ``isin``, ``maturity_date``, ``clean_price`` (or ``yield_pct``, priced with ``bonds``), ``kind``
    (default ``gsec``; ``TABLE_KINDS``), ``coupon_pct`` for coupon bonds, optional ``issue_date``,
    ``first_coupon_date`` and ``settlement_date`` (default ``settlement``, else the curve's ``effective`` date). A
    row that cannot be measured keeps NaN and the reason in ``error`` (a bond past the curve, say). A row whose ISIN
    is in ``terms`` (the corporate terms table, with its ``schedule``, ``options`` and ``amortisation`` tables) is a
    corporate bond built from them (its ``maturity_date`` may then be blank; ``k_match_pct`` is restated to its
    annual basis); a floating-rate one is refused, pointing to ``frb_asset_swap_table``.
    """
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a DataFrame")
    corp = read_corporate_terms(terms) if terms is not None else None
    by_isin = {} if corp is None else {r["isin"]: r for r in corp.to_dict("records")}
    sched = None if schedule is None else read_corporate_schedule(schedule)
    opts = None if options is None else read_corporate_options(options)
    amort = None if amortisation is None else read_amortisation(amortisation)
    missing = [c for c in ("isin",) + (() if by_isin else ("maturity_date",)) if c not in frame]
    if missing or not ({"clean_price", "yield_pct"} & set(frame.columns)):
        raise ValueError("the bonds need isin, maturity_date and clean_price or yield_pct; missing "
                         f"{missing or 'a price'}")
    names = frame["isin"].astype(str).str.strip()
    dup = sorted(set(names[names.duplicated()]))
    if dup:
        raise ValueError(f"the bonds list ISIN {dup[0]} more than once: give each bond once")
    default = settlement if settlement is not None else getattr(curve, "effective", None)
    rows = []
    for rec in frame.to_dict("records"):
        where = f"bond {rec.get('isin')}"
        out = dict.fromkeys(ASW_COLUMNS, math.nan)
        out.update(isin=rec.get("isin"), kind=rec.get("kind") or "gsec", coupon_pct=rec.get("coupon_pct", math.nan),
                   extrapolated=None, calendar_projected=None, error="")
        try:
            isin = str(rec.get("isin") or "").strip()
            if isin in by_isin:
                row = by_isin[isin]
                if row["kind"] == "frb_corp":
                    raise ValueError(f"{isin} is a floating-rate bond: its spread is a discount margin "
                                     "(asset_swaps.frb_asset_swap_table, floating.discount_margin_bp)")
                bond, kind = corporate_from_terms(row, schedule=sched, options=opts, amortisation=amort,
                                                  where=where), "corporate"
                out["coupon_pct"] = bond.coupon_pct
                if not _blank(rec.get("maturity_date")) and as_date(rec["maturity_date"]) != bond.maturity:
                    raise ValueError(f"maturity_date {rec['maturity_date']} differs from the terms' {bond.maturity}")
                out["maturity_date"] = bond.maturity
            elif "maturity_date" not in rec:
                raise ValueError("maturity_date is missing (and the ISIN is not in the corporate terms)")
            else:
                out["maturity_date"] = as_date(rec["maturity_date"])
            given = rec.get("settlement_date")
            given = None if _blank(given) else given
            if given is None and default is None:
                raise ValueError("no settlement date: give settlement_date, settlement= or a curve with an effective "
                                 "date")
            s = as_date(given if given is not None else default)
            out["settlement_date"] = s
            if isin not in by_isin:
                bond, kind = instrument_from_row(rec, where)
            out["kind"] = kind
            clean = rec.get("clean_price")
            if _blank(clean):
                y = rec.get("yield_pct")
                if _blank(y):
                    raise ValueError("no clean_price or yield_pct")
                clean = (bond.price(float(y), s, rounded=False) if isinstance(bond, DiscountInstrument)
                         else bond.clean_price(float(y), s))
            m = asset_swap(bond, s, float(clean), curve)
            for name in ASW_COLUMNS:
                if hasattr(m, name):
                    out[name] = getattr(m, name)
            out.update(clean_price=m.clean_price, settlement_date=m.settlement)
        except (ValueError, TypeError, NotImplementedError) as exc:
            out["error"] = str(exc)
        rows.append(out)
    table = pd.DataFrame(rows, columns=list(ASW_COLUMNS))
    order = table.assign(_m=pd.to_datetime(table["maturity_date"], errors="coerce"), _i=table["isin"].astype(str))
    return table.loc[order.sort_values(["_m", "_i"], kind="mergesort").index].reset_index(drop=True)


@dataclass(frozen=True)
class FrbMeasures:
    """A GoI FRB's spread measures against one curve, per 100 face (``frb_measures``)."""

    settlement: date
    maturity: date
    clean_price: float
    accrued: float
    dirty_price: float
    frb_yield_pct: float
    index_pct: float
    dm_index_bp: float
    dm_curve_bp: float
    z_spread_sa_bp: float
    spread_dv01: float
    index_dv01: float
    years_to_maturity: float
    coupon_pct: float
    coupon_status: str
    extrapolated: bool
    calendar_projected: bool


def frb_measures(bond: GoiFloatingRateBond, settlement, clean_price: float, curve, *,
                 index_pct: float | None = None) -> FrbMeasures:
    """The measures of a GoI FRB (``floating.GoiFloatingRateBond``) bought at ``clean_price`` against ``curve``.

    An FRB's coupons follow its index, so it has no fixed-coupon asset swap: its spread is a discount margin.
    ``dm_curve_bp``: the parallel shift of the curve's continuously compounded zero rates that prices its flows, the
    coupons not yet fixed at their projected rates (``floating.discount_margin_bp`` in curve form; on an OIS curve,
    the FRB's spread over OIS); ``z_spread_sa_bp`` the semi-annual variant; ``dm_index_bp`` the index form over
    ``index_pct`` (default the bond's: the base its flat projection stands on), NaN without one. ``spread_dv01``:
    the price change per bp of discount margin (index form when an index is known, else curve form);
    ``index_dv01``: per bp of the projected index, margin held (index form; NaN without an index).
    """
    if not isinstance(bond, GoiFloatingRateBond):
        raise TypeError(f"bond must be a floating.GoiFloatingRateBond, not {type(bond).__name__}")
    s = as_date(settlement)
    clean = float(clean_price)
    if not (math.isfinite(clean) and clean > 0):
        raise ValueError(f"clean price must be a positive number, not {clean_price!r}")
    cal = getattr(curve, "calendar", None) or calendar_for("mumbai_settlement")
    if not (cal if cal.covers(s) else cal.lenient()).is_business_day(s):
        raise ValueError(f"settlement {s} is not a business day on calendar {cal.name!r}")
    dates, cf = _flows(bond, s)
    _, extrapolated = _rebased(curve, s, dates, f"the FRB maturing {bond.maturity}")
    accrued = bond.accrued_interest(s)
    dirty = clean + accrued
    index = index_pct if index_pct is not None else bond.index_pct
    dm_curve = z_spread(dates, cf, s, dirty, curve) * 1e4
    if index is not None:
        dm_index = discount_margin_bp(bond, s, clean, index_pct=index)
        dv01 = spread_dv01(bond, s, dm_index, index_pct=index)
        idx_dv01 = index_dv01(bond, s, dm_index, index_pct=index)
    else:
        dm_index, idx_dv01 = math.nan, math.nan
        dv01 = spread_dv01(bond, s, dm_curve, curve=curve, form="curve")
    return FrbMeasures(
        settlement=s, maturity=bond.maturity, clean_price=clean, accrued=accrued, dirty_price=dirty,
        frb_yield_pct=bond.yield_from_price(clean, s), index_pct=math.nan if index is None else float(index),
        dm_index_bp=dm_index, dm_curve_bp=dm_curve,
        z_spread_sa_bp=z_spread(dates, cf, s, dirty, curve, compounding="semiannual") * 1e4, spread_dv01=dv01,
        index_dv01=idx_dv01, years_to_maturity=(bond.maturity - s).days / _YEAR, coupon_pct=bond.coupon_rate(s),
        coupon_status=bond.rate_status(s), extrapolated=extrapolated,
        calendar_projected=bool(getattr(curve, "calendar_projected", False)))


def frb_asset_swap_table(frame: pd.DataFrame, curve, settlement=None, *, terms=None, announced=FRB_COUPONS_CSV,
                         tbills=None, as_of=None, allow_fewer: bool = False) -> pd.DataFrame:
    """``frb_measures`` for every GoI FRB row of ``frame`` (``FRB_ASW_COLUMNS``), sorted by maturity and ISIN.

    ``frame``: ``isin``, ``clean_price`` (or ``yield_pct``), optional ``settlement_date`` (default ``settlement``,
    else the curve's ``effective`` date). ``terms``: the GoI FRB terms (default the shipped table),
    ``announced``: announced coupons (default the shipped table; ``None`` for none), ``tbills``: 182-day T-bill
    results (``floating.tbill_way_table`` or its input). Each FRB's coupons are those known at ``as_of``
    (``floating.goi_frb_rates``; ``None``: everything in the inputs, a look-ahead for a past date): a back-test must
    pass the time it decides at. A row that cannot be measured keeps the reason in ``error``.
    """
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a DataFrame")
    if "isin" not in frame or not ({"clean_price", "yield_pct"} & set(frame.columns)):
        raise ValueError("the FRBs need isin and clean_price or yield_pct")
    table = load_frb_terms() if terms is None else load_frb_terms(terms)
    by_isin = {r["isin"]: r for r in table.to_dict("records")}
    bills = None if tbills is None else tbill_way_table(tbills)
    default = settlement if settlement is not None else getattr(curve, "effective", None)
    rows = []
    for rec in frame.to_dict("records"):
        isin = str(rec.get("isin") or "").strip()
        out = dict.fromkeys(FRB_ASW_COLUMNS, math.nan) | {"isin": isin, "name": "", "coupon_status": "",
                                                          "next_status": "", "extrapolated": None,
                                                          "calendar_projected": None, "notes": "", "error": ""}
        try:
            if isin not in by_isin:
                raise ValueError(f"{isin} is not in the GoI FRB terms (floating.load_frb_terms)")
            row = by_isin[isin]
            out["name"] = row["name"]
            given = rec.get("settlement_date")
            if _blank(given) and default is None:
                raise ValueError("no settlement date: give settlement_date, settlement= or a curve with an effective "
                                 "date")
            s = as_date(default if _blank(given) else given)
            out["settlement_date"] = s
            rates = goi_frb_rates(row, bills, announced, as_of=as_of, allow_fewer=allow_fewer)
            bond = goi_frb_bond(row, rates)
            out["maturity_date"] = bond.maturity
            clean = rec.get("clean_price")
            if _blank(clean):
                y = rec.get("yield_pct")
                if _blank(y):
                    raise ValueError("no clean_price or yield_pct")
                clean = bond.clean_price(float(y), s)
            m = frb_measures(bond, s, float(clean), curve)
            for name in FRB_ASW_COLUMNS:
                if hasattr(m, name):
                    out[name] = getattr(m, name)
            ahead = rates[rates["period_start"] > s].head(1)
            current = rates[(rates["period_start"] <= s) & (rates["period_end"] > s)]
            if not ahead.empty:
                out.update(next_coupon_pct=float(ahead["rate_pct"].iloc[0]), next_status=ahead["status"].iloc[0])
            out["notes"] = "; ".join(n for n in (*current["notes"].tolist(), *ahead["notes"].tolist()) if n)
        except (ValueError, TypeError, NotImplementedError) as exc:
            out["error"] = str(exc)
        rows.append(out)
    result = pd.DataFrame(rows, columns=list(FRB_ASW_COLUMNS) + ["maturity_date"])
    order = result.assign(_m=pd.to_datetime(result["maturity_date"], errors="coerce"), _i=result["isin"].astype(str))
    result = result.loc[order.sort_values(["_m", "_i"], kind="mergesort").index].reset_index(drop=True)
    return result[list(FRB_ASW_COLUMNS)]


__all__ = ["ASW_COLUMNS", "FLOAT_SCHEDULES", "FRB_ASW_COLUMNS", "K_MATCH_BASES", "MASTER_FIELDS", "TABLE_KINDS",
           "AssetSwapMeasures", "FrbMeasures", "asset_swap", "asset_swap_table", "fill_from_master",
           "frb_asset_swap_table", "frb_measures", "hedge_notional", "instrument_from_row", "z_spread"]
