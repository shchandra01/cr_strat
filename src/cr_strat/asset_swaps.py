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
from cr_strat.conventions import CONVENTIONS, BondConventions, DiscountConventions, as_date
from cr_strat.ois import MIBOR_OIS, OisConventions, ois_schedule

FLOAT_SCHEDULES = ("bond_coupons",)
ASW_COLUMNS = ("isin", "kind", "coupon_pct", "maturity_date", "settlement_date", "clean_price", "accrued",
               "dirty_price", "ytm_pct", "modified_duration", "dv01", "years_to_maturity", "pv_on_curve", "annuity",
               "k_match_pct", "yield_yield_bp", "par_par_bp", "proceeds_bp", "z_spread_bp", "z_spread_sa_bp",
               "extrapolated", "calendar_projected", "error")
TABLE_KINDS = ("gsec", "sdl", "green", "special", "tbill", "cmb", "cp", "cd")
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
    """Scheduled dates and amounts (per 100) of the payments after ``s``, one per date."""
    if isinstance(bond, DiscountInstrument):
        bond.days_to_maturity(s)  # refuses a settlement on or after maturity
        return [bond.maturity], np.array([100.0])
    by_date: dict[date, float] = {}
    for flow in bond.cash_flows(s):
        by_date[flow.date] = by_date.get(flow.date, 0.0) + flow.amount
    dates = sorted(by_date)
    return dates, np.array([by_date[d] for d in dates])


def _check_bond(bond) -> None:
    if isinstance(bond, ZeroCouponBond):
        raise ValueError("STRIPS are not measured: a zero-coupon bond's asset swap has no coupon schedule to float on")
    if not isinstance(bond, (FixedRateBond, DiscountInstrument)):
        raise TypeError(f"bond must be a bonds.FixedRateBond (G-sec, SDL) or DiscountInstrument (T-bill, CMB, CP, CD), "
                        f"not {type(bond).__name__}")


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


def asset_swap(bond, settlement, clean_price: float, curve, *,
               float_schedule: str = "bond_coupons") -> AssetSwapMeasures:
    """Asset-swap measures of ``bond`` bought at ``clean_price`` (per 100; a bill's price) for ``settlement``.

    ``bond``: a ``bonds.FixedRateBond`` (G-sec, SDL) or ``DiscountInstrument`` (T-bill, CMB, CP, CD). ``curve``:
    any ``DiscountCurveLike``. ``float_schedule``: the asset swap's floating schedule (only ``"bond_coupons"``).
    """
    _check_bond(bond)
    if float_schedule not in FLOAT_SCHEDULES:
        raise ValueError(f"float_schedule must be one of {FLOAT_SCHEDULES}, not {float_schedule!r}")
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
    annuity = float(tau @ d)
    k_match = float((1.0 - d[-1]) / annuity * 100)
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
    bills, CP and CD as ``DiscountInstrument`` (``TABLE_KINDS``)."""
    kind = str(rec.get("kind") or "gsec").strip().lower()
    if kind not in TABLE_KINDS:
        raise ValueError(f"{where}: kind {kind!r} has no pricer here (one of {TABLE_KINDS})")
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


def asset_swap_table(frame: pd.DataFrame, curve, settlement=None) -> pd.DataFrame:
    """``asset_swap`` for every row of ``frame``: one row per bond (``ASW_COLUMNS``), sorted by maturity and ISIN.

    Columns: ``isin``, ``maturity_date``, ``clean_price`` (or ``yield_pct``, priced with ``bonds``), ``kind``
    (default ``gsec``; ``TABLE_KINDS``), ``coupon_pct`` for coupon bonds, optional ``issue_date``,
    ``first_coupon_date`` and ``settlement_date`` (default ``settlement``, else the curve's ``effective`` date). A
    row that cannot be measured keeps NaN and the reason in ``error`` (a bond past the curve, say).
    """
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a DataFrame")
    missing = [c for c in ("isin", "maturity_date") if c not in frame]
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
            out["maturity_date"] = as_date(rec["maturity_date"])
            given = rec.get("settlement_date")
            given = None if _blank(given) else given
            if given is None and default is None:
                raise ValueError("no settlement date: give settlement_date, settlement= or a curve with an effective "
                                 "date")
            s = as_date(given if given is not None else default)
            out["settlement_date"] = s
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
        except (ValueError, TypeError) as exc:
            out["error"] = str(exc)
        rows.append(out)
    table = pd.DataFrame(rows, columns=list(ASW_COLUMNS))
    order = table.assign(_m=pd.to_datetime(table["maturity_date"], errors="coerce"), _i=table["isin"].astype(str))
    return table.loc[order.sort_values(["_m", "_i"], kind="mergesort").index].reset_index(drop=True)


__all__ = ["ASW_COLUMNS", "FLOAT_SCHEDULES", "MASTER_FIELDS", "TABLE_KINDS", "AssetSwapMeasures", "asset_swap",
           "asset_swap_table", "fill_from_master", "hedge_notional", "instrument_from_row", "z_spread"]
