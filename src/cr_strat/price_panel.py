"""ISIN-day price panel: yields, spreads to the benchmark curve, and peer groups.

Each row is one ISIN on one day it traded. No prices are filled in for days
without trades. Illiquidity is part of what the event study measures, so a
missing day stays missing.

By default the yields are the reported ones (``trades.isin_daily``). With
``yield_source="recomputed"`` they are recomputed from each trade's price and
the bond's terms (``trades.check_trade_yields``, then ``recomputed_trades``,
``clean_trades`` and ``isin_daily_checked``): ``yield_pct`` and the spread
then hold the value-weighted recomputed yield, and four columns are appended
(``RECOMPUTED_COLUMNS``: the recomputed and the reported yield, the day's
check of the reported yields, the workout date). The spread is then measured
at the workout's tenor: ``tenor_years``, ``tenor_bucket`` and the benchmark
yield run to ``workout_date`` (a call, when the yield is to worst), not to
maturity. Bonds without terms are left out of a recomputed panel; with
``yield_source="recomputed_where_known"`` they stay on their reported yields
(``recomputed_trades(without_terms="reported")``: their ``recomputed_yield_pct``
blank, the check ``no_terms``), to maturity. The default output is unchanged.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from cr_strat.curves import BenchmarkCurve, tenor_bucket
from cr_strat.rating_scale import rating_bucket

PANEL_COLUMNS = (
    "date", "isin", "issuer_id", "instrument_class", "sector", "maturity_date", "tenor_years",
    "tenor_bucket", "yield_pct", "benchmark_yield_pct", "spread_bps", "price", "volume_inr",
    "n_trades", "rating_notch", "rating_grade", "rating_bucket",
)
RECOMPUTED_COLUMNS = ("recomputed_yield_pct", "yield_check", "workout_date", "reported_yield_pct")
YIELD_SOURCES = ("reported", "recomputed", "recomputed_where_known")


def build_price_panel(
    daily: pd.DataFrame,
    securities: pd.DataFrame,
    curve: BenchmarkCurve,
    ratings: pd.DataFrame,
    *,
    bond_compounding: str = "annual",
    min_tenor_years: float = 0.1,
    yield_source: str = "reported",
) -> pd.DataFrame:
    """Join ISIN-day trades to the security master, benchmark curve and as-of ratings.

    ``daily`` comes from ``trades.isin_daily``, ``securities`` from
    ``ownership.load_securities`` and ``ratings`` from
    ``rating_events.rating_timeline``. ISINs missing from the security master
    are dropped. Spreads are left empty below ``min_tenor_years`` of remaining
    maturity, where yields are dominated by money-market noise.
    ``yield_source="recomputed"`` (or ``"recomputed_where_known"``): ``daily``
    comes from ``trades.isin_daily_checked``, ``RECOMPUTED_COLUMNS`` are
    appended and the tenor, bucket and benchmark run to ``workout_date``.
    """
    if yield_source not in YIELD_SOURCES:
        raise ValueError(f"yield_source must be one of {YIELD_SOURCES}, not {yield_source!r}")
    if yield_source != "reported":
        missing = [c for c in RECOMPUTED_COLUMNS if c not in daily]
        if missing:
            raise ValueError(f"recomputed yields need trades.isin_daily_checked's daily frame (lacks {missing})")
    sec_cols = ["isin", "issuer_id", "instrument_class", "maturity_date"]
    sec = securities[sec_cols + (["sector"] if "sector" in securities else [])]
    df = daily.merge(sec, on="isin", how="inner")
    if "sector" not in df:
        df["sector"] = None

    end = df["maturity_date"]
    if yield_source != "reported":  # a yield to a call is measured against the curve at the call's tenor
        workout = pd.to_datetime(df["workout_date"], errors="coerce")
        end = workout.where(workout.notna(), df["maturity_date"])
    df["tenor_years"] = (end - df["date"]).dt.days / 365.25
    df.loc[df["tenor_years"] <= 0, "tenor_years"] = np.nan
    df["tenor_bucket"] = tenor_bucket(df["tenor_years"])
    df["benchmark_yield_pct"] = curve.yields(df["date"], df["tenor_years"], to_basis=bond_compounding)
    spread = (df["yield_pct"] - df["benchmark_yield_pct"]) * 100
    df["spread_bps"] = spread.where(df["tenor_years"] >= min_tenor_years)

    df["date"] = df["date"].astype("datetime64[ns]")
    df = df.sort_values("date")
    rt = ratings.dropna(subset=["date"]).rename(columns={"notch": "rating_notch", "grade": "rating_grade"})
    rt["date"] = rt["date"].astype("datetime64[ns]")
    rt = rt.sort_values("date")
    df = pd.merge_asof(
        df, rt[["issuer_id", "instrument_class", "date", "rating_notch", "rating_grade"]],
        on="date", by=["issuer_id", "instrument_class"], direction="backward",
    )
    df["rating_bucket"] = [rating_bucket(None if pd.isna(n) else int(n)) for n in df["rating_notch"]]
    columns = list(PANEL_COLUMNS) + (list(RECOMPUTED_COLUMNS) if yield_source != "reported" else [])
    return df.sort_values(["date", "isin"]).reset_index(drop=True)[columns]


def read_price_panel(path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"isin": str, "issuer_id": str, "instrument_class": str,
                                  "sector": str, "tenor_bucket": str, "rating_bucket": str,
                                  "rating_grade": str})
    for col in ("date", "maturity_date"):
        df[col] = pd.to_datetime(df[col], format="ISO8601", errors="coerce")
    return df
