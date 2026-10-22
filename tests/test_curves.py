import numpy as np
import pandas as pd
import pytest

from cr_strat.curves import BenchmarkCurve, SpreadBenchmark, convert_yield, money_market_to_annual, tenor_bucket


def test_convert_yield_round_trip():
    assert convert_yield(8.0, "semiannual", "annual") == pytest.approx(8.16)
    assert convert_yield(convert_yield(7.5, "annual", "semiannual"), "semiannual", "annual") == pytest.approx(7.5)


def test_curve_interpolation_and_staleness():
    curve = BenchmarkCurve(pd.DataFrame({
        "date": ["2026-03-02"] * 3,
        "tenor_years": [1, 5, 10],
        "yield_pct": [6.0, 6.4, 6.8],
    }), compounding="annual", max_staleness_days=3)
    got = curve.yields(pd.Series(pd.to_datetime(["2026-03-02", "2026-03-04", "2026-03-10", "2026-03-01"])),
                       [3.0, 20.0, 3.0, 3.0])
    assert got[0] == pytest.approx(6.2)
    assert got[1] == pytest.approx(6.8)  # flat beyond the longest tenor
    assert np.isnan(got[2]) and np.isnan(got[3])  # stale, and before the first curve


def test_money_market_to_annual():
    assert money_market_to_annual(6.0, 365) == pytest.approx(6.0)
    assert money_market_to_annual(6.0, 182.5) == pytest.approx(6.09)  # (1.03^2 - 1)
    got = money_market_to_annual([5.0, 5.0], [7, 91.25])
    assert got[0] > got[1] > 5.0  # shorter terms compound more often


def _two_dates_curve(**kwargs) -> BenchmarkCurve:
    return BenchmarkCurve(pd.DataFrame({
        "date": ["2026-03-02"] * 3 + ["2026-03-03"] * 2,
        "tenor_years": [1, 3, 5, 1, 10],
        "yield_pct": [6.0, 6.2, 6.4, 6.0, 6.9],
    }), compounding="annual", **kwargs)


def test_max_extrapolation_years():
    dates = pd.Series(pd.to_datetime(["2026-03-02"] * 4 + ["2026-03-03"] * 2))
    tenors = [0.25, 5.0, 6.5, 7.5, 7.5, 13.0]
    flat = _two_dates_curve().yields(dates, tenors)
    assert flat.tolist() == pytest.approx([6.0, 6.4, 6.4, 6.4, 6.0 + 0.9 * 6.5 / 9, 6.9])
    capped = _two_dates_curve(max_extrapolation_years=2).yields(dates, tenors)
    assert capped[:3].tolist() == pytest.approx(flat[:3].tolist())  # short end stays flat
    assert np.isnan(capped[3])  # 7.5y is more than 2y past the 5y end of 2026-03-02
    assert capped[4] == pytest.approx(flat[4]) and np.isnan(capped[5])  # 2026-03-03 ends at 10y
    exact = _two_dates_curve(max_extrapolation_years=0).yields(dates, tenors)
    assert exact[1] == pytest.approx(6.4) and np.isnan(exact[2])
    with pytest.raises(ValueError, match="max_extrapolation_years"):
        _two_dates_curve(max_extrapolation_years=-1)


def test_curve_basis_column_must_match_compounding():
    frame = pd.DataFrame({"date": ["2026-03-02"] * 2, "tenor_years": [1, 5], "yield_pct": [6.0, 6.4],
                          "Basis": ["annual", None]})
    day = pd.Series(pd.to_datetime(["2026-03-02"]))
    assert BenchmarkCurve(frame, compounding="annual").yields(day, [1.0])[0] == pytest.approx(6.0)
    with pytest.raises(ValueError, match="basis"):
        BenchmarkCurve(frame)
    frame["Basis"] = ["annual", "semiannual"]
    with pytest.raises(ValueError, match="basis"):
        BenchmarkCurve(frame, compounding="annual")


def test_tenor_bucket():
    assert tenor_bucket(pd.Series([0.5, 1.0, 2.0, 7.0, 15.0, np.nan])).tolist()[:5] == ["0-1y", "0-1y", "1-3y", "5-10y", "10y+"]


def test_spread_benchmark():
    rows = []
    for day, issuer, spread in [(1, "P1", 100), (1, "P1", 110), (2, "P2", 120), (3, "P3", 140), (3, "E", 400), (9, "P1", 90)]:
        rows.append({"date": pd.Timestamp("2026-03-01") + pd.Timedelta(days=day), "issuer_id": issuer,
                     "instrument_class": "BOND", "rating_bucket": "AA", "tenor_bucket": "1-3y", "spread_bps": spread})
    bench = SpreadBenchmark(pd.DataFrame(rows), window_days=5, min_issuers=3)
    key = ("BOND", "AA", "1-3y")
    # P1's two bonds collapse to one issuer-day (105); window 2026-03-00..04 holds P1, P2, P3, E.
    assert bench.level(key, "2026-03-04") == (pytest.approx(np.median([105, 120, 140, 400])), 4)
    assert bench.level(key, "2026-03-04", exclude_issuer="E") == (pytest.approx(120), 3)
    level, n = bench.level(key, "2026-03-10")
    assert np.isnan(level) and n == 1
    assert bench.level(("BOND", "A", "1-3y"), "2026-03-04")[1] == 0
    assert set(bench.table().columns) >= {"rating_bucket", "spread_bps", "n_issuers"}
