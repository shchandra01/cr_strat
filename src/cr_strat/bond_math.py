"""Fixed-coupon bond cash flows, prices, yields and risk measures.

Conventions follow SEBI's 2013 circular on a uniform day count for corporate
bonds: yields are XIRR-style, so each cash flow is discounted by
``(1 + y) ** (days / 365)`` with ``days`` the actual days from settlement.
Semi-annual compounding (``(1 + y/2) ** (2 * days / 365)``) is available for
sources quoted that way. Accrued interest is Actual/Actual (ICMA): the
period's coupon times the fraction of the coupon period elapsed. SEBI's rule
computes each coupon on actual days over 365 (366 in leap years), so a coupon
here can differ from the amount paid by a day's interest; the effect on price
is well under a basis point of yield.

A cash flow falling on the settlement date belongs to the seller. Prices are
per 100 of face value, yields in percent, durations in years.

Not modelled: record dates and ex-interest trading, stub first coupons (the
first coupon is paid in full), amortising principal, floating coupons, and
call or put options.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd

COMPOUNDING_PERIODS = {"annual": 1, "semiannual": 2}
FREQUENCIES = (1, 2, 4, 12)
_NEVER = np.iinfo(np.int64).max // 4  # padding for absent cash flow dates, in days


def to_days(values) -> np.ndarray:
    """Dates as int64 days since 1970-01-01 (NaT becomes the int64 minimum)."""
    return np.atleast_1d(np.asarray(pd.to_datetime(values), dtype="datetime64[D]")).astype(np.int64)


def _periods(compounding: str) -> int:
    if compounding not in COMPOUNDING_PERIODS:
        raise ValueError(f"compounding must be one of {tuple(COMPOUNDING_PERIODS)}")
    return COMPOUNDING_PERIODS[compounding]


def coupon_schedule(maturity, freq: int = 1, *, issue_date=None, after=None, max_years: int = 60):
    """``(pay_days, accrual_start_days)`` for payments after ``issue_date`` and ``after``.

    Regular dates step back from maturity by ``12 / freq`` months, keeping the
    maturity's day of month where the month has it (31 March steps back to
    30 September).
    """
    if freq not in FREQUENCIES:
        raise ValueError(f"coupon frequency must be one of {FREQUENCIES}, got {freq}")
    mat = np.datetime64(pd.Timestamp(maturity).date(), "D")
    floors = [mat - np.timedelta64(int(366 * max_years), "D")]
    for d in (issue_date, after):
        if d is not None and not pd.isna(d):
            floors.append(np.datetime64(pd.Timestamp(d).date(), "D"))
    floor = max(floors)
    step = 12 // freq
    month = mat.astype("datetime64[M]")
    day_of_month = int((mat - month.astype("datetime64[D]")).astype(int)) + 1
    n = max(int((month - floor.astype("datetime64[M]")).astype(int)) // step + 2, 2)
    months = month - (np.arange(n)[::-1] * step).astype("timedelta64[M]")
    first = months.astype("datetime64[D]")
    length = ((months + np.timedelta64(1, "M")).astype("datetime64[D]") - first).astype(int)
    dates = first + (np.minimum(day_of_month, length) - 1).astype("timedelta64[D]")
    pays, starts = dates[1:], dates[:-1]
    keep = pays > floor
    pays, starts = pays[keep], starts[keep]
    if issue_date is not None and not pd.isna(issue_date):
        starts = np.maximum(starts, np.datetime64(pd.Timestamp(issue_date).date(), "D"))
    return pays.astype(np.int64), starts.astype(np.int64)


def _analytics(pay, start, coupon, amount, settle, y, m) -> dict[str, np.ndarray]:
    """Core pricing on (N x K) cash flow arrays; ``settle`` in days, ``y`` decimal, ``m`` periods a year."""
    days = pay - settle[:, None]
    live = (pay < _NEVER) & (days > 0)
    t = np.where(live, days, 0) / 365.0
    base = 1.0 + y / m
    with np.errstate(invalid="ignore", divide="ignore", over="ignore"):
        disc = np.where(live, base[:, None] ** (-m * t), 0.0)
        pv = amount * disc
        dirty = pv.sum(axis=1)
        any_live = live.any(axis=1)
        rows = np.arange(len(settle))
        j = live.argmax(axis=1)
        nxt_pay, nxt_start = pay[rows, j], start[rows, j]
        frac = np.clip((settle - nxt_start) / np.maximum(nxt_pay - nxt_start, 1), 0.0, 1.0)
        accrued = np.where(any_live, coupon[rows, j] * frac, 0.0)
        macaulay = (pv * t).sum(axis=1) / dirty
        convexity = (pv * t * (t + 1.0 / m)).sum(axis=1) / (dirty * base**2)
    mod = np.where(any_live, macaulay / base, 0.0)
    convexity = np.where(any_live, convexity, 0.0)
    bad = ~np.isfinite(y) | (base <= 0)
    dirty, mod, convexity = (np.where(bad, np.nan, a) for a in (dirty, mod, convexity))
    return {
        "dirty": dirty,
        "accrued": accrued,
        "clean": dirty - accrued,
        "mod_duration": mod,
        "convexity": convexity,
        "dv01": mod * dirty * 1e-4,
    }


class Bond:
    """Cash flows of one fixed-coupon bullet bond, per 100 of face value."""

    def __init__(self, maturity, coupon_pct: float, freq: int = 1, *, issue_date=None,
                 redemption_pct: float = 100.0, after=None):
        if not np.isfinite(coupon_pct) or coupon_pct < 0:
            raise ValueError(f"coupon must be a non-negative percentage, got {coupon_pct}")
        self.maturity = pd.Timestamp(maturity).normalize()
        self.coupon_pct, self.freq, self.redemption_pct = float(coupon_pct), int(freq), float(redemption_pct)
        self.pay_days, self.start_days = coupon_schedule(maturity, freq, issue_date=issue_date, after=after)
        self.coupons = np.full(len(self.pay_days), self.coupon_pct / self.freq)
        self.amounts = self.coupons.copy()
        if len(self.amounts):
            self.amounts[-1] += self.redemption_pct

    def analytics(self, settle, yield_pct, compounding: str = "annual") -> dict[str, np.ndarray]:
        """Dirty, accrued and clean price, modified duration, convexity and DV01 (per 100 face, per bp)."""
        s = to_days(settle)
        y = np.asarray(yield_pct, dtype=float) / 100
        s, y = np.broadcast_arrays(s, y)
        n = len(s)
        tile = lambda a: np.broadcast_to(a, (n, len(a)))
        return _analytics(tile(self.pay_days), tile(self.start_days), tile(self.coupons), tile(self.amounts),
                          s, y.astype(float), _periods(compounding))

    def yield_from_price(self, settle, clean_price, compounding: str = "annual") -> np.ndarray:
        """Yield in percent for a clean price, by Newton's method; NaN where it does not converge."""
        s = to_days(settle)
        clean = np.asarray(clean_price, dtype=float)
        s, clean = np.broadcast_arrays(s, clean)
        target = clean + self.analytics(s.astype("datetime64[D]"), 0.0, compounding)["accrued"]
        y = np.full(len(s), max(self.coupon_pct, 1.0))
        for _ in range(100):
            a = self.analytics(s.astype("datetime64[D]"), y, compounding)
            with np.errstate(invalid="ignore", divide="ignore"):
                step = (a["dirty"] - target) / (-a["mod_duration"] * a["dirty"]) * 100
            y = y - np.clip(np.nan_to_num(step), -5.0, 5.0)
            if np.nanmax(np.abs(step), initial=0.0) < 1e-10:
                break
        a = self.analytics(s.astype("datetime64[D]"), y, compounding)
        return np.where(np.abs(a["dirty"] - target) < 1e-6, y, np.nan)

    def cash_flows(self, after, upto) -> tuple[float, float]:
        """``(coupon, principal)`` per 100 face paid in ``(after, upto]``."""
        a, b = to_days(after)[0], to_days(upto)[0]
        paid = (self.pay_days > a) & (self.pay_days <= b)
        principal = self.redemption_pct if paid.any() and paid[-1] else 0.0
        return float(self.coupons[paid].sum()), principal


class CashflowTable:
    """Many bonds' cash flows in padded arrays, so a batch of (bond, date, yield) prices in one pass."""

    def __init__(self, bonds: Mapping[str, Bond]):
        self.bonds = dict(bonds)
        self.index = {isin: i for i, isin in enumerate(self.bonds)}
        width = max((len(b.pay_days) for b in self.bonds.values()), default=1) or 1
        n = len(self.bonds)
        self.pay = np.full((n, width), _NEVER, dtype=np.int64)
        self.start = np.full((n, width), _NEVER, dtype=np.int64)
        self.coupon = np.zeros((n, width))
        self.amount = np.zeros((n, width))
        self.principal = np.zeros((n, width))
        for i, b in enumerate(self.bonds.values()):
            k = len(b.pay_days)
            self.pay[i, :k], self.start[i, :k] = b.pay_days, b.start_days
            self.coupon[i, :k], self.amount[i, :k] = b.coupons, b.amounts
            if k:
                self.principal[i, k - 1] = b.redemption_pct

    def __contains__(self, isin: object) -> bool:
        return isin in self.index

    def rows(self, isins) -> np.ndarray:
        return np.array([self.index[i] for i in isins], dtype=np.int64)

    def analytics(self, isins, settle, yield_pct, compounding: str = "annual") -> dict[str, np.ndarray]:
        r = self.rows(isins)
        s = to_days(settle)
        y = np.asarray(yield_pct, dtype=float) / 100
        s, y = np.broadcast_arrays(s, y)
        if not len(r):
            return {k: np.array([]) for k in ("dirty", "accrued", "clean", "mod_duration", "convexity", "dv01")}
        pay = self.pay[r]
        # Only columns some row still needs: a real payment after the earliest settlement.
        keep = ((pay < _NEVER) & (pay > s.min())).any(axis=0)
        cols = np.flatnonzero(keep) if keep.any() else np.array([0])
        return _analytics(pay[:, cols], self.start[r][:, cols], self.coupon[r][:, cols],
                          self.amount[r][:, cols], s, y.astype(float), _periods(compounding))

    def cash_flows(self, isins, after, upto) -> tuple[np.ndarray, np.ndarray]:
        """Coupon and principal per 100 face paid in ``(after, upto]`` for each bond."""
        r = self.rows(isins)
        if not len(r):
            return np.array([]), np.array([])
        a = to_days(after)
        b = to_days(upto)
        a, b = np.broadcast_to(a, len(r)), np.broadcast_to(b, len(r))
        pay = self.pay[r]
        paid = (pay > a[:, None]) & (pay <= b[:, None])
        return (self.coupon[r] * paid).sum(axis=1), (self.principal[r] * paid).sum(axis=1)


def bonds_from_securities(securities: pd.DataFrame, *, after=None) -> tuple[dict[str, Bond], pd.DataFrame]:
    """Build a ``Bond`` for every security with a coupon and maturity; returns ``(bonds, skipped)``.

    Columns used: ``coupon_pct`` (required), ``maturity_date`` (required),
    ``coupon_freq`` (payments a year, default 1), ``issue_date`` and
    ``redemption_pct`` (default 100). ``skipped`` lists ISINs without usable
    terms and why.
    """
    num = lambda c, default: (pd.to_numeric(securities[c], errors="coerce") if c in securities
                              else pd.Series(default, index=securities.index))
    coupon, freq, redemption = num("coupon_pct", np.nan), num("coupon_freq", 1), num("redemption_pct", 100.0)
    maturity = pd.to_datetime(securities["maturity_date"], errors="coerce") if "maturity_date" in securities \
        else pd.Series(pd.NaT, index=securities.index)
    issue = pd.to_datetime(securities["issue_date"], errors="coerce") if "issue_date" in securities \
        else pd.Series(pd.NaT, index=securities.index)
    bonds, skipped = {}, []
    for i, isin in securities["isin"].items():
        reason = None
        if pd.isna(coupon[i]):
            reason = "no coupon_pct"
        elif pd.isna(maturity[i]):
            reason = "no maturity_date"
        elif int(freq.fillna(1)[i]) not in FREQUENCIES:
            reason = f"coupon_freq {freq[i]} not in {FREQUENCIES}"
        if reason is None:
            try:
                bonds[isin] = Bond(maturity[i], float(coupon[i]), int(freq.fillna(1)[i]),
                                   issue_date=None if pd.isna(issue[i]) else issue[i],
                                   redemption_pct=float(redemption.fillna(100.0)[i]), after=after)
                continue
            except ValueError as exc:
                reason = str(exc)
        skipped.append({"isin": isin, "reason": reason})
    return bonds, pd.DataFrame(skipped, columns=["isin", "reason"])
