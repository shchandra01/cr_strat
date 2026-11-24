"""Synthetic inputs for the risk-layer tests (tests/test_risk.py, test_stress.py, test_cli_risk.py): NSS curves with
known parameters, OIS curves from the tests' B3 quotes, factor histories with known changes, and positions rows in
the engine's layout. Nothing here is a real price; the bonds' terms are typed-in facts (6.94% GS 2036, 7.09% GS 2054,
6.36% GS 2031, FRB 2031 of the shipped terms)."""

from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from cr_strat import curvefit as cf
from cr_strat import ois
from cr_strat import returns as rt
from cr_strat import risk as rk
from cr_strat.bonds import FixedRateBond
from cr_strat.calendars import mumbai_settlement_calendar

CAL = mumbai_settlement_calendar()
LEN = CAL.lenient()
NSS = (7.6, -2.0, -1.0, 2.0, 1.5, 8.0)
S16 = date(2026, 11, 16)  # the settlement of 13 Nov 2026 (Friday)
D13 = date(2026, 11, 13)
B3 = {"1M": 5.62, "2M": 5.66, "3M": 5.70, "6M": 5.78, "9M": 5.84, "1Y": 5.88, "2Y": 5.98, "3Y": 6.06, "4Y": 6.12,
      "5Y": 6.18}
GS2036 = FixedRateBond(6.94, date(2036, 5, 11))
GS2054 = FixedRateBond(7.09, date(2054, 8, 5))
GS2031 = FixedRateBond(6.36, date(2031, 6, 23))
TERMS = {"GS2036": ("gsec", 6.94, "2036-05-11"), "GS2054": ("gsec", 7.09, "2054-08-05"),
         "GS2031": ("gsec", 6.36, "2031-06-23")}
MASTER = pd.DataFrame([{"isin": k, "kind": v[0], "coupon_pct": v[1], "maturity_date": v[2]} for k, v in TERMS.items()])
FRB2031 = "IN0020180041"
NCD_ISIN = "INE000A07011"
DV01_T1 = 0.066373117027  # central sum of the KRDs of 6.94% GS 2036 per 100 (spec T1)


def nss(settle: date, params=NSS, t_max: float = 50.0) -> cf.NssCurve:
    return cf.NssCurve(valuation_date=settle, t_min=0.25, t_max=t_max, method="nss", params=params)


def at(day: date, hour: int = 19, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute))


def b3_curve(day: date = date(2026, 11, 19)) -> ois.OisCurve:
    return ois.bootstrap_ois(B3, day, 5.60, calendar=LEN, source="synthetic B3")


def grid(n: int = 250) -> np.ndarray:
    """A permutation of -12.45 ... +12.45 bp by 0.1 (spec T9): ``((37 i) mod 250) / 10 - 12.45``."""
    return np.array([((37 * i) % 250) / 10 - 12.45 for i in range(n)])


def history_days(end: date, n: int) -> list[date]:
    """The ``n`` business days ending on ``end``."""
    days = CAL.business_days(end - timedelta(days=2 * n + 30), end)
    return days[-n:]


def nss_entries(days, *, level_moves_bp=None, params=NSS, public_hour: int = 19) -> dict:
    """``{mark date: CurveEntry}``: one NSS curve per day (valued on its T+1 settlement), ``beta0`` moved by the
    cumulative ``level_moves_bp`` (a parallel move of every zero rate, bp), public at ``public_hour`` on the day."""
    moves = np.zeros(len(days)) if level_moves_bp is None else np.asarray(level_moves_bp, dtype=float)
    level = np.cumsum(moves) - np.cumsum(moves)[-1]  # the last day's level is ``params``
    out = {}
    for k, d in enumerate(days):
        p = (params[0] + level[k] / 100, *params[1:])
        out[d] = rt.CurveEntry(nss(CAL.settlement_date(d, 1), p), at(d, public_hour))
    return out


def ois_entries(days, base: ois.OisCurve, *, moves_bp=None, public_hour: int = 19) -> dict:
    """OIS curves on each day: ``base`` rolled to the day and shifted by the cumulative ``moves_bp`` (the last day:
    ``base`` itself, with its quotes)."""
    moves = np.zeros(len(days)) if moves_bp is None else np.asarray(moves_bp, dtype=float)
    level = np.cumsum(moves) - np.cumsum(moves)[-1]
    out = {}
    for k, d in enumerate(days):
        curve = base if d == base.valuation_date else rt.rolled(base, d).shifted(float(level[k]))
        out[d] = rt.CurveEntry(curve, at(d, public_hour))
    return out


def bond_row(bond, isin: str, kind: str, face: float, day: date, settle: date, y: float, *, book: str = "b",
             state: str = "settled", liquidity: str = "liquid", source: str = "traded", **extra) -> dict:
    """A positions row (``returns.POSITION_COLUMNS``) as the engine writes it: value to the mark date's accrued
    interest for a settled row; the yield DV01 at the row's settlement."""
    clean = bond.clean_price(y, settle)
    value = face / 100 * (clean + bond.accrued_interest(day)) if state == "settled" else 0.0
    return {"date": day, "book": book, "isin": isin, "kind": kind, "state": state, "face": face,
            "settlement_date": settle, "clean_mark": clean, "mark_source": source, "mark_age_days": 0,
            "yield_pct": y, "accrued": bond.accrued_interest(day), "value": value,
            "dv01": face / 100 * bond.dv01(y, settle), "bucket": "", "liquidity": liquidity, "haircut_pct": "",
            "funded": "", "stale_warning": False, "extrapolated": False, "calendar_projected": False, "notes": "",
            **extra}


def on_curve_yield(bond, curve, settle: date, z: float = 0.0) -> float:
    t, a = rt._flows(bond, settle)
    dirty = float(a @ (np.asarray(curve.discount_t(t)) * np.exp(-z * t)))
    return bond.yield_from_dirty_price(dirty, settle)


def gsec_context(day: date = D13, *, entries=None, preset: str = "unregulated_company", risk=None, **kw
                 ) -> rk.RiskContext:
    curves = {"gsec": entries if entries is not None else {day: rt.CurveEntry(nss(CAL.settlement_date(day, 1)),
                                                                              at(day))}}
    market = kw.pop("market", None) or rt.MarketData(curves=curves, master=MASTER)
    settings = kw.pop("settings", None) or rt.EngineSettings(access=rt.ACCESS_PRESETS[preset])
    return rk.RiskContext(market=market, settings=settings, risk=risk or rk.RiskSettings(), as_of=at(day, 20), **kw)


def gs2036_positions(day: date = D13, face: float = 1e9, curve=None, z: float = 0.0, **kw) -> pd.DataFrame:
    settle = CAL.settlement_date(day, 1)
    c = curve if curve is not None else nss(settle)
    y = on_curve_yield(GS2036, c, settle, z)
    return pd.DataFrame([bond_row(GS2036, "GS2036", "gsec", face, day, settle, y, **kw)])


def parallel_history_context(n: int = 300, *, end: date = D13, moves=None, risk=None, preset="unregulated_company"):
    """A G-sec book's context with ``n`` business days of fits whose level moves by ``moves`` (default the T9 grid,
    cycled): every key rate moves by the same bp each day."""
    days = history_days(end, n)
    g = grid()
    mv = np.array([0.0] + [g[i % 250] for i in range(n - 1)]) if moves is None else np.asarray(moves)
    entries = nss_entries(days, level_moves_bp=mv)
    ctx = gsec_context(end, entries=entries, risk=risk, preset=preset)
    return ctx, days, mv
