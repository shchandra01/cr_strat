import numpy as np
import pandas as pd
import pytest

from cr_strat.bond_math import Bond, CashflowTable, bonds_from_securities, coupon_schedule


def test_par_bond_prices_at_par_on_coupon_date():
    # No leap day in either period, so XIRR discounting gives exactly par.
    bond = Bond("2027-06-30", 8.0, 1, issue_date="2024-06-30")
    a = bond.analytics("2025-06-30", 8.0)
    assert a["dirty"][0] == pytest.approx(100.0)
    assert a["accrued"][0] == 0.0


def test_accrued_is_actual_actual_icma():
    bond = Bond("2027-06-30", 8.0, 1, issue_date="2024-06-30")
    a = bond.analytics("2025-12-31", 7.5)
    assert a["accrued"][0] == pytest.approx(8.0 * 184 / 365)
    assert a["clean"][0] == pytest.approx(a["dirty"][0] - a["accrued"][0])


def test_zero_coupon_and_matured():
    bond = Bond("2027-01-15", 0.0)
    a = bond.analytics(["2026-01-15", "2027-01-15", "2027-03-01"], 7.0)
    assert a["dirty"][0] == pytest.approx(100 / 1.07)
    assert a["mod_duration"][0] == pytest.approx(1 / 1.07)
    assert list(a["dirty"][1:]) == [0.0, 0.0]  # redemption on the settlement date belongs to the seller


def test_duration_and_convexity_match_finite_differences():
    bond = Bond("2031-03-31", 7.25, 2, issue_date="2021-03-31")
    for comp in ("annual", "semiannual"):
        p = lambda y: bond.analytics("2026-11-17", y, comp)["dirty"][0]
        a = bond.analytics("2026-11-17", 7.9, comp)
        h = 0.01
        assert a["mod_duration"][0] == pytest.approx((p(7.9 - h) - p(7.9 + h)) / (2 * h / 100 * p(7.9)), rel=1e-6)
        assert a["convexity"][0] == pytest.approx((p(7.9 + h) + p(7.9 - h) - 2 * p(7.9)) / (p(7.9) * (h / 100) ** 2),
                                                  rel=1e-4)
        assert a["dv01"][0] == pytest.approx(a["mod_duration"][0] * a["dirty"][0] * 1e-4)


def test_yield_from_price_round_trip():
    bond = Bond("2030-09-15", 9.1, 1)
    settle = ["2025-02-03", "2027-09-15", "2029-12-31"]
    for comp in ("annual", "semiannual"):
        clean = bond.analytics(settle, [6.5, 9.1, 12.0], comp)["clean"]
        assert bond.yield_from_price(settle, clean, comp) == pytest.approx([6.5, 9.1, 12.0], abs=1e-8)


def test_schedule_steps_back_from_maturity():
    pays, starts = coupon_schedule("2030-03-31", 2, issue_date="2028-03-31")
    dates = pd.to_datetime(pays, unit="D").strftime("%Y-%m-%d").tolist()
    assert dates == ["2028-09-30", "2029-03-31", "2029-09-30", "2030-03-31"]
    assert pd.to_datetime(starts[0], unit="D") == pd.Timestamp("2028-03-31")
    with pytest.raises(ValueError):
        coupon_schedule("2030-03-31", 3)


def test_cash_flows_between_dates():
    bond = Bond("2027-06-30", 8.0, 1, issue_date="2024-06-30")
    assert bond.cash_flows("2025-06-29", "2025-06-30") == (8.0, 0.0)
    assert bond.cash_flows("2025-06-30", "2026-06-29") == (0.0, 0.0)
    assert bond.cash_flows("2026-01-01", "2027-12-31") == (16.0, 100.0)


def test_cashflow_table_matches_single_bonds():
    bonds = {"A": Bond("2027-06-30", 8.0, 1), "B": Bond("2031-03-31", 7.25, 2), "Z": Bond("2026-01-15", 0.0)}
    table = CashflowTable(bonds)
    settle = ["2025-08-01", "2025-08-01", "2025-08-01"]
    batch = table.analytics(["A", "B", "Z"], settle, [7.0, 7.5, 6.8])
    for k, (isin, y) in enumerate((("A", 7.0), ("B", 7.5), ("Z", 6.8))):
        one = bonds[isin].analytics(settle[k], y)
        for col in ("dirty", "accrued", "mod_duration", "convexity"):
            assert batch[col][k] == pytest.approx(one[col][0])
    cpn, prin = table.cash_flows(["A", "B", "Z"], "2025-08-01", "2026-06-30")
    assert list(cpn) == [8.0, 7.25, 0.0] and list(prin) == [0.0, 0.0, 100.0]


def test_bonds_from_securities_reports_skips():
    sec = pd.DataFrame({"isin": ["A", "B", "C"], "coupon_pct": [8.0, np.nan, 7.0],
                        "maturity_date": ["2030-01-01", "2030-01-01", "2030-01-01"], "coupon_freq": [1, 1, 3]})
    bonds, skipped = bonds_from_securities(sec)
    assert list(bonds) == ["A"]
    assert skipped.set_index("isin")["reason"].to_dict()["B"] == "no coupon_pct"
    assert "coupon_freq" in skipped.set_index("isin")["reason"]["C"]
