"""Tests of the OIS counterparty exposure added in the risk layer's review (the current exposure method of the NBFC
capital directions as saved: gross positive MTM plus an add-on of 0.5%, 1% or 3% of notional by residual maturity, a
CCP's exposure zero, weights 0%, 20% and 100%), its limit metric, its part in the NBFC's CRAR and the daily file.
Synthetic data only; the swaps' counterparties are the fund's own input."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pandas as pd
import pytest
import limits_fixtures as lf

from cr_strat import daily as dl
from cr_strat import limits as lm
from cr_strat import returns as rt

DAY = date(2026, 11, 19)


def _swap(trade_id, notional, mtm, end, book="b"):
    return {"book": book, "isin": f"OIS:{trade_id}", "kind": "ois", "state": "ois", "face": notional, "value": mtm,
            "maturity_date": end}


TABLE = pd.DataFrame([{"trade_id": "S1", "counterparty": "Bank A", "counterparty_type": "bank"},
                      {"trade_id": "S2", "counterparty": "Bank A", "counterparty_type": "bank"},
                      {"trade_id": "S3", "counterparty": "CCP", "counterparty_type": "ccp"},
                      {"trade_id": "S4", "counterparty": "Corp B", "counterparty_type": "other"}])


def test_the_current_exposure_method_by_counterparty():
    rows = [_swap("S1", 1e9, 5e6, date(2027, 5, 19)),  # 6 months: 0.5%
            _swap("S2", -1e9, -3e6, date(2029, 11, 19)),  # 3 years: 1%; a negative MTM is not netted
            _swap("S3", 2e9, 4e6, date(2031, 11, 19)),  # a CCP: zero
            _swap("S4", 5e8, 1e6, date(2034, 11, 20)),  # beyond 5 years: 3%
            _swap("NEW", 1e8, 0.0, date(2027, 11, 22))]  # no row: unknown at 100%
    out = lm.counterparty_exposure(rows, DAY, TABLE, nav={("fund", "fund"): 1e10}).set_index("counterparty")
    bank = out.loc["Bank A"]
    assert (bank["current_exposure"], bank["pfe"]) == pytest.approx((5e6, 1e9 * 0.005 + 1e9 * 0.01))
    assert bank["credit_equivalent"] == pytest.approx(2e7) and bank["risk_weighted"] == pytest.approx(4e6)
    assert bank["pct_nav"] == pytest.approx(0.2) and bank["swaps"] == 2
    assert out.loc["CCP", "credit_equivalent"] == 0.0 and out.loc["CCP", "risk_weight_pct"] == 0.0
    assert out.loc["Corp B", "pfe"] == pytest.approx(5e8 * 0.03)
    assert out.loc["Corp B", "risk_weighted"] == pytest.approx(1e6 + 1.5e7)
    unknown = out.loc[lm.UNKNOWN_COUNTERPARTY]
    assert unknown["counterparty_type"] == "other" and unknown["flags"] == "no_counterparty"
    assert unknown["credit_equivalent"] == pytest.approx(1e8 * 0.01)  # one year and three days: the 1% bucket


def test_the_counterparty_table_is_checked():
    with pytest.raises(ValueError, match="line 2: counterparty_type 'broker'"):
        lm.read_counterparties(pd.DataFrame([{"trade_id": "S1", "counterparty": "X", "counterparty_type": "broker"}]))
    with pytest.raises(ValueError, match="trade S1 is given twice"):
        lm.read_counterparties(pd.concat([TABLE, TABLE.iloc[:1]]))
    with pytest.raises(ValueError, match="lacks"):
        lm.read_counterparties(pd.DataFrame([{"trade_id": "S1"}]))


def _gated_view(preset, table=None):
    market, trades = lf.ois_market()
    ctx = lf.context(market, preset, trades=trades, project_calendar=True, counterparties=table)
    state = lf.state([lf.gsec_row("GS2031", 1e9)], {"b": (1.5e9, 5e8)}, ctx, scenarios=lf.scenario_rows())
    order = rt.Order(book="b", side="pay", face=1e9, months=12)
    return lm._view(state, [lm.order_risk(state, order, seq=1)], {})


def test_the_nbfc_crar_weights_the_swaps_credit_equivalent():
    view = _gated_view("nbfc_non_pd")
    (_, crar, ok, detail), = lm.metric_values(view, "crar", "fund", "fund", (), "pct")
    # the G-sec is weighted 0%; the new 12-month swap (forward start: over a year left) has a 1% add-on at 100%
    assert crar == pytest.approx(1.5e9 / 1e7 * 100) and ok  # was infinite: OIS not weighted
    assert "credit equivalent Rs 10,000,000.00" in detail and "without a counterparty row at 100%" in detail
    values = lm.metric_values(view, "counterparty_exposure", "fund", "fund",
                              lm.parse_selector("type=other", "counterparty_exposure"), "pct_nav")
    assert values[0][0] == lm.UNKNOWN_COUNTERPARTY and values[0][1] == pytest.approx(1e7 / 1.5e9 * 100)
    with pytest.raises(ValueError, match="type 'broker' is not one of"):
        lm.parse_selector("type=broker", "counterparty_exposure")


def test_the_daily_process_reports_the_counterparty_exposure(tmp_path):
    root = lf.paper_fund(tmp_path / "paper")
    result = dl.run_daily(dl.DailyConfig.read(root / "config.toml"), lf.DAY1, orders=None, out_dir=root)
    assert list(result.counterparty.columns) == list(lm.COUNTERPARTY_EXPOSURE_COLUMNS)
    assert len(result.counterparty) == 0 and "OIS counterparty exposure" not in result.report  # the fund has no swap
    assert not (root / lf.DAY1.isoformat() / "limits" / "counterparty_exposure.csv").exists()  # no new file
    report = dl.report_markdown(replace(result, counterparty=lm.counterparty_exposure(
        [_swap("S1", 1e9, 5e6, date(2027, 5, 19))], lf.DAY1, TABLE, nav={("fund", "fund"): 3e9})))
    lines = report.split("\n")
    at = lines.index("OIS counterparty exposure (current exposure method: gross positive MTM plus the add-on; CCPs "
                     "zero):")
    assert lines.index("## Credit") < at < lines.index("## VaR and ES")
    assert lines[at + 4].startswith("| Bank A | bank | 1 | 5,000,000.00 | 5,000,000.00 | 10,000,000.00 | 0.3333 |")
