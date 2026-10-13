"""Execution assumptions for an illiquid OTC market: when an order fills, how much, and at what cost.

Indian corporate bonds trade bilaterally and most ISINs go days or weeks
without a reported trade, so the default model is strict:

* an order can fill only on a day the bond has a reported trade
  (``trade_days_only``), and only up to ``max_participation`` of that day's
  reported traded value;
* orders become live ``lag_days`` business days after the signal, so a signal
  built from a day's trades is never filled at those same trades, and they
  expire after ``order_ttl_days`` business days if still unfilled;
* the fill yield is the day's traded yield (or the mark, off trade days)
  worsened by a half bid-ask spread for the bond's rating bucket plus a linear
  impact term in the participation rate. Fills off trade days pay
  ``off_trade_multiplier`` times that;
* buys pay stamp duty on the consideration: 0.0001% for transfers of debt
  securities since the Indian Stamp Act amendments of July 2020.

Costs are applied in yield and converted to price through the bond's own
price function, so a long bond pays more per basis point than a short one, as
it does in practice. The default spreads are placeholders; calibrate them
against dealer quotes or the dispersion of reported trades in each bucket.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

DEFAULT_HALF_SPREAD_BPS = {
    "AAA": 3.0, "AA+": 5.0, "AA": 7.5, "AA-": 10.0, "A+": 12.5, "A": 15.0, "A-": 20.0,
    "BBB": 30.0, "SUB_IG": 75.0, "D": 150.0,
}


@dataclass
class ExecutionModel:
    lag_days: int = 1
    order_ttl_days: int = 10
    trade_days_only: bool = True
    max_participation: float = 0.25
    half_spread_bps: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_HALF_SPREAD_BPS))
    default_half_spread_bps: float = 20.0
    impact_bps: float = 10.0
    off_trade_multiplier: float = 2.0
    stamp_duty_pct: float = 0.0001

    def __post_init__(self) -> None:
        if self.lag_days < 1:
            raise ValueError("lag_days must be at least 1: a signal cannot trade on the data that produced it")
        if not 0 < self.max_participation <= 1:
            raise ValueError("max_participation must be in (0, 1]")
        if self.order_ttl_days < 1:
            raise ValueError("order_ttl_days must be at least 1")

    def cost_bps(self, bucket: str | None, participation: float, on_trade_day: bool) -> float:
        """Yield give-up in bps for one fill. Unknown participation (no reported volume) is charged at the cap."""
        half = self.half_spread_bps.get(bucket, self.default_half_spread_bps) if bucket \
            else self.default_half_spread_bps
        if participation is None or not math.isfinite(participation):
            participation = self.max_participation
        cost = half + self.impact_bps * participation
        return cost if on_trade_day else cost * self.off_trade_multiplier

    def max_fill_value(self, volume_inr: float) -> float:
        """Largest fill value allowed today; unlimited when the day's volume is unknown."""
        if volume_inr is None or not math.isfinite(volume_inr) or volume_inr <= 0:
            return math.inf
        return self.max_participation * volume_inr
