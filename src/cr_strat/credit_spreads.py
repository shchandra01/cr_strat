"""Credit spreads of corporate bonds to a G-sec (or any discount) curve, and peer spread matrices (S2, S8, S9, S10).

A spread is only meaningful with its measure and basis named: on one synthetic 8.95% annual NCD priced on an NSS
curve plus 100 bp of continuous zero rate the four measures below give 100.00, 103.79, 106.86 and 106.50 bp (the
tests' vectors T17-T18). Every price, accrued interest and yield comes from the bond's own pricer
(``corporates.CorporateBond``, a ``bonds.Bond``: SEBI actual/actual coupons, record dates, the yield annual on actual
days over 365 with the money-market rule in the last year); the curve is any discount curve with ``valuation_date``,
``t_max``, ``discount`` and ``discount_t`` (``curvefit``'s fitted G-sec curve, an ``ois.OisCurve``). A curve valued
before the settlement date is seen from it with its forward rates kept (``D(t) = DF(a + t) / DF(a)``).

* ``g_spread_bp``: the bond's yield (to maturity, or to the workout asked) less the curve's par yield at the same
  tenor (Act/365F years from settlement to the workout date), the par yield semi-annual (``curvefit.par_yields``)
  restated to the bond's compounding (``conventions.convert_yield``: annual for NCDs). A bond whose yield is simple
  (one flow left within ``mm_final_days``: the money-market rule) is set against the curve's simple rate to that
  date, ``(1 / D(T) - 1) / T``. This is the spread ``price_panel`` takes against a ``curves.BenchmarkCurve`` of par
  yields, so it can be compared with the peer benchmark (``add_peer_spreads``).
* ``par_spread_bp``: the bond's yield less the yield of a risk-free bond with the same schedule, day count,
  amortisation and record dates whose coupon makes it worth its outstanding face clean on the curve,
  ``c* = (F - PV0) / (PV1 - AI1)`` (``PV0`` the curve value of its principal flows, ``PV1`` of a 1% coupon, ``AI1``
  that coupon's accrued interest, ``F`` the outstanding face per 100 original; for a bullet
  ``100 (1 - D(T)) / (sum yf_k D(b_k) - AI_unit)``), redeemed at par. A zero-coupon or cumulative bond has no coupon to
  match: its par yield is its own yield at its curve price.
* ``z_spread_bp``: the parallel shift of the curve's continuously compounded zero rates that prices the bond's
  flows, on their due dates by default (``asset_swaps.z_spread``); ``z_spread_sa_bp`` shifts semi-annual zero rates.
  Against an OIS curve the asset-swap measures of ``asset_swaps`` apply (yield-yield with the matched rate restated
  to the bond's annual basis, par-par, proceeds).

``spread_table`` measures every row of a prices frame from the corporate terms table (``corporates``' readers), to
maturity, to worst (the lowest yield over maturity and calls, from a price) or by SEBI's trigger rule (from a yield).
Floating-rate bonds are refused here: their spread is a discount margin (``floating.discount_margin_bp``,
``asset_swaps.frb_measures``).

**Peers and matrices.** The valuation agencies price untraded paper from traded yields of the same issuer, then of
similar issuers, then from a matrix of spreads by sector, rating and tenor over a base curve (SEBI's master circular
for mutual funds of 20 Mar 2026, 10.2.3, read; AMFI's waterfall and the agencies' matrices are licensed, never
shipped). The free substitute here is built from a price panel's traded spreads: ``build_peer_matrix`` takes the
median spread of each peer group (instrument class, rating bucket and tenor bucket by default) over a trailing window
through ``curves.SpreadBenchmark`` (one observation per issuer and day, at least ``min_issuers`` issuers), with the
group's median tenor; ``matrix_spread_bp`` and ``matrix_yield`` read any matrix of that layout (``read_spread_matrix``
for one kept by the user), linear in tenor within a group and flat beyond its ends; ``add_peer_spreads`` sets each
bond's spread against its peers' median, its own issuer left out.

**Limits.** Tested on synthetic curves and bonds with known answers only (no traded corporate price was available).
The G-spread's tenor is the workout's Act/365F years and its basis conversion is exact only for a regular par bond;
the matched par bond is risk-free by construction, so the par spread holds credit, liquidity and the option a
callable bond carries (to worst, the workout is fixed at the date the price implies). Peer matrices from traded
spreads carry the panel's selection: few trades, rating buckets as of the trade date, a five-day window by default
(UNVERIFIED choices, as in ``curves``).
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from cr_strat.asset_swaps import z_spread
from cr_strat.conventions import as_date, convert_yield
from cr_strat.corporates import (
    PRICE_BASES,
    CorporateBond,
    _as_of_for,
    corporate_from_terms,
    normalise_isin,
    price_factor,
    public_time,
    read_amortisation,
    read_corporate_options,
    read_corporate_schedule,
    read_corporate_terms,
)
from cr_strat.curvefit import par_yields
from cr_strat.curves import SpreadBenchmark

SPREAD_COLUMNS = ("isin", "settlement_date", "clean_price", "accrued", "dirty_price", "workout_date", "workout_kind",
                  "years_to_workout", "yield_pct", "curve_yield_pct", "g_spread_bp", "par_coupon_pct",
                  "par_yield_pct", "par_spread_bp", "z_spread_bp", "z_spread_sa_bp", "curve_id", "extrapolated",
                  "calendar_projected", "known_as_of", "error")
WORKOUTS = ("maturity", "worst", "sebi")
FLOW_DATES = ("due", "payment")
PEER_KEYS = ("instrument_class", "rating_bucket", "tenor_bucket")
MATRIX_COLUMNS = ("tenor_years", "spread_bp")
_YEAR = 365.0


# ---------------------------------------------------------------- the curve seen from settlement


class _SeenFrom:
    """A curve valued on an earlier date seen from ``day``, forward rates kept: ``D(t) = DF(a + t) / DF(a)``."""

    def __init__(self, curve, day: date):
        self.base = curve
        self.valuation_date = day
        self._a = (day - curve.valuation_date).days / _YEAR
        self._df_a = float(np.asarray(curve.discount_t(np.array([self._a])), dtype=float).reshape(-1)[0])
        if not (math.isfinite(self._df_a) and self._df_a > 0):
            raise ValueError(f"the curve of {curve.valuation_date} gives no discount factor on {day}")
        self.t_max = max(float(curve.t_max) - self._a, 0.0)
        self.t_min = max(float(getattr(curve, "t_min", 0.0)) - self._a, 0.0)
        self.curve_id = getattr(curve, "curve_id", "")

    def discount_t(self, t) -> np.ndarray:
        return np.asarray(self.base.discount_t(np.asarray(t, dtype=float) + self._a), dtype=float) / self._df_a

    def discount(self, when) -> np.ndarray:
        scalar = isinstance(when, (str, date, pd.Timestamp))
        dates = [as_date(when)] if scalar else [as_date(w) for w in when]
        t = np.array([(d - self.valuation_date).days for d in dates], dtype=float) / _YEAR
        if (t < 0).any():
            raise ValueError(f"a date before the curve's view date {self.valuation_date}")
        out = self.discount_t(t)
        return out.reshape(()) if scalar else out


def seen_from(curve, settlement):
    """``curve`` as seen from ``settlement``: itself on its valuation date, a rebased view after it (refused before)."""
    s = as_date(settlement)
    v = as_date(curve.valuation_date)
    if s == v:
        return curve
    if s < v:
        raise ValueError(f"the curve is valued on {v}, after the settlement {s}")
    return _SeenFrom(curve, s)


def _extrapolated(curve, settlement: date, when: date) -> bool:
    """Whether ``when`` lies beyond the data the curve was built from (its ``t_max`` or ``in_data_range``)."""
    t = (when - as_date(curve.valuation_date)).days / _YEAR
    inside = getattr(curve, "in_data_range", None)
    if inside is not None:
        return not bool(np.asarray(inside(t)).reshape(-1)[0])
    flag = getattr(curve, "extrapolated", None)
    if flag is not None:
        return bool(np.asarray(flag(t)).reshape(-1)[0])
    return t > float(curve.t_max) + 1e-9


# ---------------------------------------------------------------- measures of one bond


def _check(bond) -> None:
    if not isinstance(bond, CorporateBond):
        raise TypeError(f"bond must be a corporates.CorporateBond, not {type(bond).__name__} (a floating-rate bond's "
                        "spread is its discount margin: floating.discount_margin_bp)")


def _workout_price(bond: CorporateBond, s: date, kind: str, when: date) -> float:
    if kind in ("maturity",) and when == bond.maturity:
        return bond.redemption
    kind = {"first_call": "call", "deemed_maturity": "put"}.get(kind, kind)
    prices = {(k, d): p for k, d, p in bond.workouts(s)}
    if (kind, when) not in prices:
        raise ValueError(f"{bond.isin or bond.name}: no {kind} on {when} ahead of {s}")
    return prices[(kind, when)]


def workout_bond(bond: CorporateBond, settlement, clean_price: float, workout=None) -> tuple[CorporateBond, str, date]:
    """(the bond redeemed at the workout, its kind, its date) from a clean price: ``None`` or ``"maturity"`` (the
    bond itself), ``"worst"`` (the lowest yield over maturity and calls, ``CorporateBond.yield_to_worst``) or a date
    (an option of the bond on it, else a redemption at par there)."""
    _check(bond)
    s = as_date(settlement)
    if workout is None or workout == "maturity":
        return bond, "maturity", bond.maturity
    if workout == "worst":
        w = bond.yield_to_worst(s, float(clean_price))
        return (bond if w.date == bond.maturity else bond.to_workout(w.date, w.price)), w.kind, w.date
    if isinstance(workout, str) and workout in WORKOUTS:
        raise ValueError(f"workout {workout!r} needs a yield (spread_table with yield_pct); from a price give "
                         "'maturity', 'worst' or a date")
    when = as_date(workout)
    if when == bond.maturity:
        return bond, "maturity", when
    kinds = [k for k, d, _ in bond.workouts(s) if d == when]
    return bond.to_workout(when), (kinds[0] if kinds else "date"), when


def _flow_dates(bond, s: date, flow_dates: str) -> tuple[list[date], np.ndarray]:
    if flow_dates not in FLOW_DATES:
        raise ValueError(f"flow_dates must be one of {FLOW_DATES}, not {flow_dates!r}")
    by_date: dict[date, float] = {}
    for f in bond.cash_flows(s):
        d = f.date if flow_dates == "due" else f.payment_date
        by_date[d] = by_date.get(d, 0.0) + f.amount
    dates = sorted(by_date)
    if not dates:
        raise ValueError(f"no flow after settlement {s}")
    return dates, np.array([by_date[d] for d in dates])


def _pv(bond, s: date, curve_s) -> float:
    """The bond's flows after ``s`` on their due dates, discounted on ``curve_s`` (a curve seen from ``s``)."""
    dates, amounts = _flow_dates(bond, s, "due")
    df = np.asarray(curve_s.discount(dates), dtype=float).reshape(-1)
    if not np.all(np.isfinite(df)) or (df <= 0).any():
        raise ValueError(f"the curve gives no discount factor out to {dates[-1]} (beyond its range?)")
    return float(amounts @ df)


def curve_yield_pct(bond: CorporateBond, settlement, curve) -> tuple[float, str]:
    """(the curve's yield at the bond's tenor on the bond's basis, the basis): the par yield restated to the bond's
    compounding, or the simple rate to the single flow of a bond under the money-market rule (module docstring)."""
    _check(bond)
    s = as_date(settlement)
    curve_s = seen_from(curve, s)
    flows = bond._flows(s)
    t = (bond.maturity - s).days / _YEAR
    if flows.simple:
        df = float(np.asarray(curve_s.discount_t(np.array([t])), dtype=float).reshape(-1)[0])
        if not (math.isfinite(df) and df > 0):
            raise ValueError(f"the curve gives no discount factor at {bond.maturity}")
        return (1 / df - 1) / t * 100, "simple"
    par = float(np.asarray(par_yields(curve_s, [t], 2), dtype=float).reshape(-1)[0])
    if not math.isfinite(par):
        raise ValueError(f"the curve gives no par yield at {t:.4f} years (beyond its range?)")
    basis = bond.conventions.compounding
    return float(convert_yield(par, "semiannual", basis)), basis


def g_spread_bp(bond: CorporateBond, settlement, clean_price: float, curve, *, workout=None) -> float:
    """The bond's yield to the workout less the curve's par yield at that tenor on the bond's basis, in bp."""
    target, _, _ = workout_bond(bond, settlement, clean_price, workout)
    s = as_date(settlement)
    y = target.yield_from_price(float(clean_price), s)
    return (y - curve_yield_pct(target, s, curve)[0]) * 100


def par_coupon_pct(bond: CorporateBond, settlement, curve) -> float:
    """The coupon (percent) of the risk-free twin of ``bond`` (same schedule, amortisation and day count, redeemed at
    par) that is worth its outstanding face clean on ``curve``; NaN for a zero-coupon or cumulative bond."""
    _check(bond)
    s = as_date(settlement)
    if not bond.frequency:
        return math.nan
    curve_s = seen_from(curve, s)
    zero = replace(bond, coupon_pct=0.0, rate_steps=(), redemption=100.0)
    unit = replace(bond, coupon_pct=1.0, rate_steps=(), redemption=100.0)
    pv0 = _pv(zero, s, curve_s)
    denom = _pv(unit, s, curve_s) - pv0 - unit.accrued_interest(s)
    if not denom > 0:
        raise ValueError(f"{bond.isin or bond.name}: no par coupon (its coupons are worth nothing on the curve)")
    return (bond.outstanding_face(s) - pv0) / denom


def par_yield_pct(bond: CorporateBond, settlement, curve) -> tuple[float, float]:
    """(the yield of the par twin on the bond's basis, its coupon): ``par_coupon_pct``; a zero-coupon or cumulative
    bond's own yield at its curve price (and NaN)."""
    s = as_date(settlement)
    c = par_coupon_pct(bond, s, curve)
    if math.isnan(c):
        return bond.yield_from_dirty_price(_pv(bond, s, seen_from(curve, s)), s), c
    twin = replace(bond, coupon_pct=c, rate_steps=(), redemption=100.0)
    return twin.yield_from_price(bond.outstanding_face(s), s), c


def par_spread_bp(bond: CorporateBond, settlement, clean_price: float, curve, *, workout=None) -> float:
    """The bond's yield to the workout less its par twin's yield on the same basis, in bp (module docstring)."""
    target, _, _ = workout_bond(bond, settlement, clean_price, workout)
    s = as_date(settlement)
    return (target.yield_from_price(float(clean_price), s) - par_yield_pct(target, s, curve)[0]) * 100


def z_spread_bp(bond: CorporateBond, settlement, clean_price: float, curve, *, compounding: str = "continuous",
                flow_dates: str = "due", workout=None) -> float:
    """The z-spread (bp) of the bond's flows (on their ``due`` or ``payment`` dates) at its dirty price on ``curve``:
    ``asset_swaps.z_spread``, continuously compounded zero rates shifted, or semi-annual ones."""
    target, _, _ = workout_bond(bond, settlement, clean_price, workout)
    s = as_date(settlement)
    dates, amounts = _flow_dates(target, s, flow_dates)
    dirty = float(clean_price) + target.accrued_interest(s)
    return z_spread(dates, amounts, s, dirty, curve, compounding=compounding) * 1e4


def spread_measures(bond: CorporateBond, settlement, clean_price: float, curve, *, workout=None) -> dict:
    """Every measure of one bond at once (the columns of ``SPREAD_COLUMNS`` but ``isin`` and ``error``)."""
    s = as_date(settlement)
    clean = float(clean_price)
    if not (math.isfinite(clean) and clean > 0):
        raise ValueError(f"clean price must be a positive number, not {clean_price!r}")
    target, kind, when = workout_bond(bond, s, clean, workout)
    a = target.analytics(s, clean_price=clean)
    curve_y, _ = curve_yield_pct(target, s, curve)
    par_y, coupon = par_yield_pct(target, s, curve)
    dates, amounts = _flow_dates(target, s, "due")
    return {"settlement_date": s, "clean_price": clean, "accrued": a.accrued_interest, "dirty_price": a.dirty_price,
            "workout_date": when, "workout_kind": kind, "years_to_workout": (when - s).days / _YEAR,
            "yield_pct": a.yield_pct, "curve_yield_pct": curve_y, "g_spread_bp": (a.yield_pct - curve_y) * 100,
            "par_coupon_pct": coupon, "par_yield_pct": par_y, "par_spread_bp": (a.yield_pct - par_y) * 100,
            "z_spread_bp": z_spread(dates, amounts, s, a.dirty_price, curve) * 1e4,
            "z_spread_sa_bp": z_spread(dates, amounts, s, a.dirty_price, curve, compounding="semiannual") * 1e4,
            "curve_id": str(getattr(curve, "curve_id", "") or ""), "extrapolated": _extrapolated(curve, s, when)}


def _blank(value) -> bool:
    if value is None or (isinstance(value, str) and not value.strip()):
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def spread_table(frame: pd.DataFrame, terms, curve, *, workout: str = "maturity", settlement=None, schedule=None,
                 options=None, amortisation=None, rates: dict | None = None, calendar="default", as_of=None,
                 price_basis: str = "original") -> pd.DataFrame:
    """``spread_measures`` for every row of ``frame`` (``SPREAD_COLUMNS``, in the frame's order).

    ``frame``: ``isin``, ``clean_price`` or ``yield_pct``, optional ``settlement_date`` (default ``settlement``).
    ``terms``: the corporate terms table (``corporates.read_corporate_terms``' layout, a frame or CSV), with the
    optional schedule, options and amortisation tables; ``rates``: rate steps by ISIN for ``frb_corp`` rows (their
    spread is then the fixed-coupon measure of the coupons as known: a discount margin is the usual measure).
    ``workout``: ``maturity``, ``worst`` (from a price) or ``sebi`` (SEBI's trigger rule, from a yield). Each bond
    is taken as known at ``as_of`` (``corporates.CorporateBond.as_known``: an exercise public by then redeems it at
    the option, workout kind ``exercised_call`` or ``exercised_put``; a decision not to exercise drops the option):
    one time for every row, else the row's ``as_of`` cell, else 00:00 on its trade date (``corporates.default_as_of``;
    never everything known). ``price_basis``: a clean price per 100 ``original`` face (default) or per 100 of the
    face ``outstanding`` at settlement (``corporates.price_factor``); the outputs stay per 100 original face. A row
    that cannot be measured keeps NaN and the reason in ``error``.
    """
    if workout not in WORKOUTS:
        raise ValueError(f"workout must be one of {WORKOUTS}, not {workout!r}")
    if price_basis not in PRICE_BASES:
        raise ValueError(f"price_basis must be one of {PRICE_BASES}, not {price_basis!r}")
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a DataFrame")
    if "isin" not in frame or not ({"clean_price", "yield_pct"} & set(frame.columns)):
        raise ValueError("the prices need isin and clean_price or yield_pct")
    when_all = None if as_of is None else public_time(as_of, "as_of")
    table = read_corporate_terms(terms)
    sched = None if schedule is None else read_corporate_schedule(schedule)
    opts = None if options is None else read_corporate_options(options)
    amort = None if amortisation is None else read_amortisation(amortisation)
    by_isin = {r["isin"]: r for r in table.to_dict("records")}
    rates = None if rates is None else {normalise_isin(k): v for k, v in rates.items()}
    bonds: dict[str, CorporateBond | Exception] = {}
    rows = []
    for rec in frame.to_dict("records"):
        isin = normalise_isin(rec.get("isin"))
        out = dict.fromkeys(SPREAD_COLUMNS, math.nan) | {"isin": isin, "workout_kind": "", "curve_id": "",
                                                         "extrapolated": None, "calendar_projected": None,
                                                         "known_as_of": None, "error": ""}
        try:
            if isin not in bonds:
                try:
                    if isin not in by_isin:
                        raise ValueError(f"{isin}: no terms")
                    bond = corporate_from_terms(by_isin[isin], schedule=sched, options=opts, amortisation=amort,
                                                calendar=calendar)
                    if by_isin[isin]["kind"] == "frb_corp":
                        if not rates or isin not in rates:
                            raise ValueError(f"{isin}: a floating-rate bond: its spread is a discount margin "
                                             "(floating.discount_margin_bp); give rates= to measure the coupons as "
                                             "known")
                        bond = bond.with_rates(rates[isin])
                    bonds[isin] = bond
                except (ValueError, TypeError, NotImplementedError) as exc:
                    bonds[isin] = exc
            base = bonds[isin]
            if isinstance(base, Exception):
                raise base
            given = rec.get("settlement_date")
            if _blank(given) and settlement is None:
                raise ValueError("no settlement date: give settlement_date or settlement=")
            s = as_date(settlement if _blank(given) else given)
            out["settlement_date"] = s
            when = _as_of_for(base, rec, s, when_all)
            out["known_as_of"] = when
            bond = base.as_known(when)
            exercised = ""
            if bond.maturity != base.maturity:
                done = [o.kind for o in base.options if o.date == bond.maturity and o.exercised]
                exercised = f"exercised_{done[0]}" if done else "exercised"
            clean = rec.get("clean_price")
            y = rec.get("yield_pct")
            if workout == "sebi":
                if _blank(y):
                    raise ValueError("the SEBI valuation works from a yield: give yield_pct")
                v = bond.sebi_valuation(s, float(y))
                target = bond.to_workout(v.date, _workout_price(bond, s, v.kind, v.date))
                m = spread_measures(target, s, v.clean_price, curve)
                m.update(workout_kind=v.kind, workout_date=v.date)
            else:
                if _blank(clean):
                    if _blank(y):
                        raise ValueError("no clean_price or yield_pct")
                    if workout == "worst":
                        clean = bond.price_to_worst(s, float(y))[0]
                    else:
                        clean = bond.clean_price(float(y), s)
                else:
                    clean = float(clean) * price_factor(bond, s, price_basis)
                m = spread_measures(bond, s, float(clean), curve, workout=workout)
            if exercised and m["workout_kind"] == "maturity":
                m["workout_kind"] = exercised
            out.update(m)
            out["calendar_projected"] = bond.calendar_projected(s)
        except (ValueError, TypeError, NotImplementedError) as exc:
            out["error"] = str(exc)
        rows.append(out)
    return pd.DataFrame(rows, columns=list(SPREAD_COLUMNS), index=frame.index)


# ---------------------------------------------------------------- peers and matrices


def build_peer_matrix(panel: pd.DataFrame, *, date, window_days: int = 5, match_on=PEER_KEYS, min_issuers: int = 3,
                      spread_column: str = "spread_bps") -> pd.DataFrame:
    """The peer spread matrix of one ``date`` from a price panel (``price_panel.build_price_panel``'s layout).

    One row per peer group seen in the trailing window: its median spread (``curves.SpreadBenchmark``: one value
    per issuer and day, the median over them, NaN below ``min_issuers`` issuers), the number of issuers and the
    group's median ``tenor_years`` in the window. ``spread_column``: the panel's spread (``spread_bps``, against
    its benchmark curve). Columns: ``match_on``, ``tenor_years``, ``spread_bp``, ``n_issuers``, ``date``.
    """
    keys = tuple(match_on)
    need = {*keys, "date", "issuer_id", "tenor_years", spread_column}
    missing = sorted(need - set(panel.columns))
    if missing:
        raise ValueError(f"the panel lacks {missing}")
    d = pd.Timestamp(as_date(date))
    work = panel.rename(columns={spread_column: "spread_bps"}) if spread_column != "spread_bps" else panel
    work = work.assign(date=pd.to_datetime(work["date"]))
    bench = SpreadBenchmark(work, match_on=keys, window_days=window_days, min_issuers=min_issuers)
    window = work[(work["date"] > d - pd.Timedelta(days=window_days)) & (work["date"] <= d)]
    window = window.dropna(subset=["spread_bps", "issuer_id", *keys])
    rows = []
    for key, group in window.groupby(list(keys), sort=True, observed=True):
        key = key if isinstance(key, tuple) else (key,)
        level, n = bench.level(key, d)
        rows.append({**dict(zip(keys, key)), "tenor_years": float(group["tenor_years"].median()), "spread_bp": level,
                     "n_issuers": n, "date": d.date()})
    return pd.DataFrame(rows, columns=[*keys, "tenor_years", "spread_bp", "n_issuers", "date"])


def read_spread_matrix(source) -> pd.DataFrame:
    """A spread matrix kept by the user (or ``build_peer_matrix``'s): key columns (any of ``instrument_class``,
    ``sector``, ``rating_bucket``, ``tenor_bucket``), ``tenor_years`` and ``spread_bp``; blank rows dropped, a key
    and tenor given twice with different spreads refused."""
    frame = source.copy() if isinstance(source, pd.DataFrame) else pd.read_csv(Path(source), dtype=str,
                                                                               keep_default_na=False)
    frame.columns = [str(c).strip().lower() for c in frame.columns]
    missing = [c for c in MATRIX_COLUMNS if c not in frame]
    if missing:
        raise ValueError(f"the spread matrix lacks {missing}")
    frame = frame[~frame.apply(lambda r: all(_blank(v) for v in r), axis=1)].reset_index(drop=True)
    for c in MATRIX_COLUMNS:
        frame[c] = pd.to_numeric(frame[c], errors="raise")
    keys = [c for c in frame.columns if c not in (*MATRIX_COLUMNS, "n_issuers", "date", "source")]
    dup = frame[frame.duplicated([*keys, "tenor_years"], keep=False)]
    if not dup.empty and dup.groupby([*keys, "tenor_years"])["spread_bp"].nunique().max() > 1:
        raise ValueError("the spread matrix gives one group and tenor two spreads")
    return frame.drop_duplicates([*keys, "tenor_years"]).reset_index(drop=True)


def matrix_spread_bp(matrix: pd.DataFrame, *, tenor_years: float, **key) -> float:
    """The matrix spread (bp) of the group ``key`` (e.g. ``rating_bucket="AA"``) at ``tenor_years``: linear between
    the group's tenors, flat beyond its ends; NaN when the group has no finite spread."""
    rows = matrix
    for column, value in key.items():
        if column not in rows:
            raise ValueError(f"the matrix has no column {column!r}")
        rows = rows[rows[column].astype(str) == str(value)]
    rows = rows[np.isfinite(pd.to_numeric(rows["spread_bp"], errors="coerce"))].sort_values("tenor_years")
    if rows.empty:
        return math.nan
    return float(np.interp(float(tenor_years), rows["tenor_years"].astype(float), rows["spread_bp"].astype(float)))


def matrix_yield(matrix: pd.DataFrame, *, tenor_years: float, base_yield_pct: float, **key) -> float:
    """A matrix yield (percent): ``base_yield_pct`` (the base curve's yield at the tenor, on the bond's basis) plus
    the group's matrix spread there (``matrix_spread_bp``)."""
    return float(base_yield_pct) + matrix_spread_bp(matrix, tenor_years=tenor_years, **key) / 100


def add_peer_spreads(table: pd.DataFrame, benchmark: SpreadBenchmark, *, date_column: str = "settlement_date",
                     spread_column: str = "g_spread_bp", issuer_column: str = "issuer_id") -> pd.DataFrame:
    """``table`` with each row's peer median spread on its date (``benchmark.level`` on the benchmark's peer keys,
    the row's own issuer left out), the number of peer issuers and ``relative_spread_bp`` (its spread less the
    peers'). The table needs the benchmark's ``match_on`` columns and ``issuer_column``; the benchmark's spreads
    must be on the same measure (``price_panel``'s spread against par yields restated to annual is the G-spread)."""
    missing = [c for c in (*benchmark.match_on, date_column, spread_column, issuer_column) if c not in table]
    if missing:
        raise ValueError(f"the table lacks {missing} for the peer benchmark")
    levels, counts = [], []
    for rec in table.to_dict("records"):
        key = tuple(rec[c] for c in benchmark.match_on)
        if any(_blank(v) for v in key) or _blank(rec[date_column]):
            levels.append(math.nan)
            counts.append(0)
            continue
        level, n = benchmark.level(key, pd.Timestamp(as_date(rec[date_column])),
                                   exclude_issuer=None if _blank(rec[issuer_column]) else str(rec[issuer_column]))
        levels.append(level)
        counts.append(n)
    out = table.copy()
    out["peer_spread_bp"] = levels
    out["peer_issuers"] = counts
    out["relative_spread_bp"] = pd.to_numeric(out[spread_column], errors="coerce") - out["peer_spread_bp"]
    return out


__all__ = ["FLOW_DATES", "MATRIX_COLUMNS", "PEER_KEYS", "SPREAD_COLUMNS", "WORKOUTS", "add_peer_spreads",
           "build_peer_matrix", "curve_yield_pct", "g_spread_bp", "matrix_spread_bp", "matrix_yield",
           "par_coupon_pct", "par_spread_bp", "par_yield_pct", "read_spread_matrix", "seen_from", "spread_measures",
           "spread_table", "workout_bond", "z_spread_bp"]
