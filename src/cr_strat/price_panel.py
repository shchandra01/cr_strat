"""ISIN-day price panel: yields, spreads to the benchmark curve, and peer groups.

Each row is one ISIN on one day it traded. No prices are filled in for days
without trades. Illiquidity is part of what the event study measures, so a
missing day stays missing.
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


def build_price_panel(
    daily: pd.DataFrame,
    securities: pd.DataFrame,
    curve: BenchmarkCurve,
    ratings: pd.DataFrame,
    *,
    bond_compounding: str = "annual",
    min_tenor_years: float = 0.1,
) -> pd.DataFrame:
    """Join ISIN-day trades to the security master, benchmark curve and as-of ratings.

    ``daily`` comes from ``trades.isin_daily``, ``securities`` from
    ``ownership.load_securities`` and ``ratings`` from
    ``rating_events.rating_timeline``. ISINs missing from the security master
    are dropped. Spreads are left empty below ``min_tenor_years`` of remaining
    maturity, where yields are dominated by money-market noise.
    """
    sec_cols = ["isin", "issuer_id", "instrument_class", "maturity_date"]
    sec = securities[sec_cols + (["sector"] if "sector" in securities else [])]
    df = daily.merge(sec, on="isin", how="inner")
    if "sector" not in df:
        df["sector"] = None

    df["tenor_years"] = (df["maturity_date"] - df["date"]).dt.days / 365.25
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
    return df.sort_values(["date", "isin"]).reset_index(drop=True)[list(PANEL_COLUMNS)]


def read_price_panel(path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"isin": str, "issuer_id": str, "instrument_class": str,
                                  "sector": str, "tenor_bucket": str, "rating_bucket": str,
                                  "rating_grade": str})
    for col in ("date", "maturity_date"):
        df[col] = pd.to_datetime(df[col], format="ISO8601", errors="coerce")
    return df
