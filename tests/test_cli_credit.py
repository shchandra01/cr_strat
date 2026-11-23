"""Tests of the command-line paths for corporate bonds and GoI FRBs: credit-spreads, trade-check, price-panel's
recomputed yields, asset-swaps with corporate terms and FRBs, and returns with corporate and FRB inputs.

Synthetic data only: the bonds, curves, quotes, T-bill results and marks are made up in code (the corporates spec's
T17-T19 vectors where a number is checked).
"""

from datetime import date

import pandas as pd
import pytest
from test_returns_credit import DAYS, FRB_ISIN, NCD_ISIN, NCD_TERMS, frb_tbills, ncd_marks

from cr_strat import asset_swaps as aw
from cr_strat import credit_spreads as cs
from cr_strat import ois
from cr_strat import returns as rt
from cr_strat import trades as tr
from cr_strat.calendars import mumbai_settlement_calendar
from cr_strat.cli import main
from cr_strat.curvefit import NssCurve
from cr_strat.price_panel import PANEL_COLUMNS, RECOMPUTED_COLUMNS

S = date(2026, 11, 16)
CURVE = NssCurve(valuation_date=S, t_min=0.25, t_max=40.0, method="nss", params=(7.6, -2.0, -1.0, 2.0, 1.5, 8.0),
                 curve_id="gsec:2026-11-13")
T17_TERMS = {"isin": "", "kind": "corporate", "issuer_id": "A", "coupon_pct": "8.95", "frequency": "1",
             "issue_date": "2023-12-14", "maturity_date": "2030-12-14"}
T17_CLEAN = 102.2591928939


def _csv(path, rows) -> str:
    pd.DataFrame(rows).to_csv(path, index=False)
    return str(path)


def test_credit_spreads_command(tmp_path, capsys, isin):
    t17 = isin("INE000T1701")
    terms = _csv(tmp_path / "terms.csv", [{**T17_TERMS, "isin": t17}])
    nss = _csv(tmp_path / "nss.csv", [rt.nss_params_row(CURVE, date(2026, 11, 13))])
    rows = [{"isin": t17, "clean_price": T17_CLEAN, "issuer_id": "A", "instrument_class": "BOND",
             "rating_bucket": "AA"},
            {"isin": isin("INE000NOT01"), "clean_price": 100.0}]
    prices = _csv(tmp_path / "prices.csv", rows)
    args = ["credit-spreads", "--prices", prices, "--terms", terms, "--nss-params", nss]
    assert main([*args, "--out", str(tmp_path / "s.csv")]) == 0
    out = capsys.readouterr().out
    assert "1 of 2 bonds measured against the gsec curve of 2026-11-13" in out and "refused" in out
    table = pd.read_csv(tmp_path / "s.csv")
    assert list(table.columns) == list(cs.SPREAD_COLUMNS)
    row = table.iloc[0]
    assert row["g_spread_bp"] == pytest.approx(106.859149, abs=1e-6)  # T17
    assert row["par_spread_bp"] == pytest.approx(106.504794, abs=1e-6)
    assert row["z_spread_bp"] == pytest.approx(100.0, abs=1e-6)
    assert "no terms" in table.iloc[1]["error"]
    # with a price panel's peers: three other issuers of the same class, bucket and tenor bucket on 13 Nov
    panel = pd.DataFrame([{"date": "2026-11-13", "isin": f"P{k}", "issuer_id": f"I{k}", "instrument_class": "BOND",
                           "rating_bucket": "AA", "tenor_bucket": "3-5y", "tenor_years": 4.0, "spread_bps": s,
                           "maturity_date": "2030-11-13"}
                          for k, s in enumerate((90.0, 100.0, 120.0))])
    panel_path = _csv(tmp_path / "panel.csv", panel.to_dict("records"))
    assert main([*args, "--peer-panel", panel_path, "--out", str(tmp_path / "p.csv")]) == 0
    peers = pd.read_csv(tmp_path / "p.csv")
    assert peers["tenor_bucket"].iloc[0] == "3-5y"  # 4.08 years to maturity
    assert peers["peer_spread_bp"].iloc[0] == 100.0 and peers["peer_issuers"].iloc[0] == 3
    assert peers["relative_spread_bp"].iloc[0] == pytest.approx(6.859149, abs=1e-6)
    # refusals: a curve not in the file, two dates without --date
    assert main([*args, "--curve", "sdl", "--out", str(tmp_path / "x.csv")]) == 2
    assert "--curve 'sdl' is not in" in capsys.readouterr().err
    two = _csv(tmp_path / "nss2.csv", [rt.nss_params_row(CURVE, date(2026, 11, 13)),
                                       rt.nss_params_row(CURVE, date(2026, 11, 12))])
    assert main(["credit-spreads", "--prices", prices, "--terms", terms, "--nss-params", two, "--out",
                 str(tmp_path / "y.csv")]) == 2
    assert "pass --date" in capsys.readouterr().err


def _trade(isin, price, yld, settle="2026-11-16", day="2026-11-13", amount=5e7):
    return {"trade_date": day, "isin": isin, "price": price, "yield_pct": yld, "amount_inr": amount, "n_trades": 1,
            "venue": "NSE", "settlement_date": settle, "source_file": "synthetic.csv"}


def test_trade_check_command(tmp_path, capsys, isin):
    t17 = isin("INE000T1701")
    terms = _csv(tmp_path / "terms.csv", [{**T17_TERMS, "isin": t17}])
    trades = _csv(tmp_path / "trades.csv", [_trade(t17, T17_CLEAN, 8.2612262724, amount=1e8),
                                            _trade(t17, T17_CLEAN, 8.2662660611, amount=3e8),
                                            _trade(t17, T17_CLEAN, 8.0973101915, amount=1e8),
                                            _trade(isin("INE000NOT01"), 100.0, 8.0)])
    code = main(["trade-check", "--trades", trades, "--terms", terms, "--out", str(tmp_path / "c.csv"),
                 "--daily-out", str(tmp_path / "d.csv")])
    assert code == 0
    out = capsys.readouterr().out
    assert "yield checks: ok 2, mismatch 1, no_terms 1, no_price 0, no_yield 0, error 0 (tolerance 1 bp" in out
    assert f"mismatch {t17} on 2026-11-13: reported 8.09731%" in out and "(-16.39 bp)" in out
    checked = pd.read_csv(tmp_path / "c.csv")
    direct = tr.check_trade_yields(tr.read_trades(trades), terms)
    assert checked["yield_check"].tolist() == direct["yield_check"].tolist() == ["ok", "ok", "mismatch", "no_terms"]
    assert checked["recomputed_yield_pct"].iloc[0] == pytest.approx(8.2612262724, abs=1e-9)
    daily = pd.read_csv(tmp_path / "d.csv")
    assert daily["yield_check"].iloc[0] == "mismatch:1;ok:2" and len(daily) == 1
    loose = main(["trade-check", "--trades", trades, "--terms", terms, "--tolerance-bp", "20",
                  "--out", str(tmp_path / "l.csv")])
    assert loose == 0 and "ok 3, mismatch 0" in capsys.readouterr().out
    only_unknown = _csv(tmp_path / "u.csv", [_trade(isin("INE000NOT01"), 100.0, 8.0)])
    assert main(["trade-check", "--trades", only_unknown, "--terms", terms, "--out", str(tmp_path / "n.csv")]) == 1
    assert main(["trade-check", "--trades", trades, "--terms", str(tmp_path / "missing.csv"), "--out",
                 str(tmp_path / "m.csv")]) == 2


def test_price_panel_recomputed_yields(tmp_path, capsys, isin):
    t17 = isin("INE000T1701")
    terms = _csv(tmp_path / "terms.csv", [{**T17_TERMS, "isin": t17}])
    trades = _csv(tmp_path / "trades.csv", [_trade(t17, T17_CLEAN, 8.2612262724, amount=1e8),
                                            _trade(t17, T17_CLEAN, 8.40, amount=1e8)])
    securities = [{"isin": t17, "issuer_id": "A", "instrument_class": "BOND", "maturity_date": "2030-12-14",
                   "sector": "NBFC"}]
    bench = [{"date": "2026-11-13", "tenor_years": 1, "yield_pct": 6.0},
             {"date": "2026-11-13", "tenor_years": 5, "yield_pct": 6.4}]
    actions = [{"date": "2025-01-01", "issuer_id": "A", "rating": "CRISIL AA+/Stable"}]
    common = ["price-panel", "--trades", trades, "--securities", _csv(tmp_path / "sec.csv", securities),
              "--benchmark", _csv(tmp_path / "bench.csv", bench),
              "--rating-actions", _csv(tmp_path / "ra.csv", actions)]
    assert main([*common, "--out-dir", str(tmp_path / "plain")]) == 0
    plain = pd.read_csv(tmp_path / "plain" / "price_panel.csv")
    assert list(plain.columns) == list(PANEL_COLUMNS)
    assert not (tmp_path / "plain" / "trade_checks.csv").exists()
    assert main([*common, "--yield-source", "recomputed", "--terms", terms, "--out-dir", str(tmp_path / "rec")]) == 0
    assert "2 of 2 trades have a recomputed yield" in capsys.readouterr().out
    panel = pd.read_csv(tmp_path / "rec" / "price_panel.csv")
    assert list(panel.columns) == [*PANEL_COLUMNS, *RECOMPUTED_COLUMNS]
    assert panel["yield_pct"].iloc[0] == pytest.approx(8.2612262724, abs=1e-9)
    assert panel["reported_yield_pct"].iloc[0] == pytest.approx((8.2612262724 + 8.40) / 2, abs=1e-9)
    assert pd.read_csv(tmp_path / "rec" / "trade_checks.csv")["yield_check"].tolist() == ["ok", "mismatch"]
    assert main([*common, "--yield-source", "recomputed", "--out-dir", str(tmp_path / "bad")]) == 2
    assert "needs --terms" in capsys.readouterr().err


QUOTES = {"1M": 5.62, "2M": 5.66, "3M": 5.70, "6M": 5.78, "9M": 5.84, "1Y": 5.88, "2Y": 5.98, "3Y": 6.06, "4Y": 6.12,
          "5Y": 6.18}


def test_asset_swaps_command_with_corporate_bonds_and_frbs(tmp_path, capsys):
    quotes = _csv(tmp_path / "q.csv", [{"tenor": k, "rate_pct": v} for k, v in QUOTES.items()])
    ncd = {"isin": "INE000A07011", "kind": "corporate", "coupon_pct": "7.4", "frequency": "1",
           "issue_date": "2024-06-15", "maturity_date": "2029-06-15"}
    terms = _csv(tmp_path / "terms.csv", [ncd])
    rows = [{"isin": "INE000A07011", "clean_price": "99.0"},
            {"isin": "GS2031", "kind": "gsec", "coupon_pct": "6.36", "maturity_date": "2031-02-16",
             "clean_price": "100.0"},
            {"isin": FRB_ISIN, "clean_price": "100.25"}]
    bonds = _csv(tmp_path / "bonds.csv", rows)
    tbills = _csv(tmp_path / "tbills.csv", frb_tbills().to_dict("records"))
    common = ["asset-swaps", "--quotes", quotes, "--trade-date", "2026-11-19", "--overnight", "5.60",
              "--max-extrapolation-years", "6", "--project-calendar", "--bonds", bonds, "--terms", terms]
    assert main([*common, "--out", str(tmp_path / "a.csv"), "--frb-out", str(tmp_path / "f.csv"), "--tbills", tbills,
                 "--as-of", "2026-11-19T20:00"]) == 0
    out = capsys.readouterr().out
    assert "2 of 2 bonds measured" in out and "1 of 1 GoI FRBs measured, their coupons as known at 2026-11-19T20:00" \
        in out
    curve = ois.bootstrap_ois(pd.DataFrame([{"tenor": k, "rate_pct": str(v)} for k, v in QUOTES.items()]),
                              date(2026, 11, 19), 5.60, calendar=mumbai_settlement_calendar().lenient(),
                              max_extrapolation_years=6.0)
    table = pd.read_csv(tmp_path / "a.csv")
    direct = aw.asset_swap_table(pd.DataFrame(rows[:2]), curve, terms=pd.DataFrame([ncd]))
    by_isin = table.set_index("isin")
    for rec in direct.to_dict("records"):
        assert by_isin.loc[rec["isin"], "par_par_bp"] == pytest.approx(rec["par_par_bp"], abs=1e-9)
    assert by_isin.loc["INE000A07011", "z_spread_bp"] > 0
    frb = pd.read_csv(tmp_path / "f.csv")
    assert list(frb.columns) == list(aw.FRB_ASW_COLUMNS)
    expect = aw.frb_asset_swap_table(pd.DataFrame(rows[2:]), curve, tbills=frb_tbills(),
                                     as_of=pd.Timestamp("2026-11-19T20:00").to_pydatetime())
    assert frb["dm_index_bp"].iloc[0] == pytest.approx(expect["dm_index_bp"].iloc[0], abs=1e-9)
    assert frb["coupon_pct"].iloc[0] == pytest.approx(6.66, abs=1e-12) and frb["coupon_status"].iloc[0] == "computed"
    # without --frb-out the FRB stays in the main table, refused there, and a note points to --frb-out
    assert main([*common, "--out", str(tmp_path / "b.csv")]) == 0
    out = capsys.readouterr().out
    assert "1 of the bonds are GoI FRBs" in out and "2 of 3 bonds measured" in out


def test_returns_command_with_a_corporate_bond_and_an_frb(tmp_path, capsys):
    frb_marks = pd.DataFrame([{"date": d, "isin": FRB_ISIN, "clean_price": 100.25} for d in DAYS])
    marks = pd.concat([ncd_marks(), frb_marks], ignore_index=True)
    trades = [{"trade_id": "N1", "book": "b", "isin": NCD_ISIN, "side": "buy", "face": 1e8,
               "trade_date": "2026-11-20"},
              {"trade_id": "F1", "book": "b", "isin": FRB_ISIN, "kind": "frb", "side": "buy", "face": 1e8,
               "trade_date": "2026-11-20"}]
    flows = [{"date": "2026-11-20", "book": "b", "amount": 4e8}]
    args = ["returns", "--trades", _csv(tmp_path / "t.csv", trades), "--flows", _csv(tmp_path / "fl.csv", flows),
            "--marks", _csv(tmp_path / "m.csv", marks.to_dict("records")), "--access", "nbfc_non_pd",
            "--end", str(DAYS[-1]), "--corporate-terms", _csv(tmp_path / "ct.csv", [NCD_TERMS]),
            "--tbills", _csv(tmp_path / "tb.csv", frb_tbills().to_dict("records"))]
    assert main([*args, "--out-dir", str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    assert "identity breaches: 0" in out
    nav = pd.read_csv(tmp_path / "out" / "nav.csv")
    market = rt.MarketData(marks=marks, corporate_terms=pd.DataFrame([NCD_TERMS]), tbills=frb_tbills())
    direct = rt.run_book(trades, flows=pd.DataFrame(flows), market=market,
                         settings=rt.EngineSettings(access=rt.ACCESS_PRESETS["nbfc_non_pd"]), end=DAYS[-1])
    assert nav["nav"].tolist() == pytest.approx(direct.nav["nav"].tolist(), rel=1e-12)
    pos = pd.read_csv(tmp_path / "out" / "positions.csv")
    assert set(pos["kind"]) == {"corporate", "frb"}
    assert (pos[pos["isin"] == FRB_ISIN].set_index("date").loc["2026-12-08", "notes"] == "coupon 7.1800% computed")
    # no T-bill results and no announced coupons: the FRB's coupons are unknown, refused with the remedy
    bare = [a for a in args if a not in ("--tbills", str(tmp_path / "tb.csv"))]
    assert main([*bare, "--no-frb-coupons", "--out-dir", str(tmp_path / "bare")]) == 2
    assert "cannot be projected" in capsys.readouterr().err
    assert main([*args, "--tbills-from-store", "--out-dir", str(tmp_path / "x")]) == 2
    assert "not both" in capsys.readouterr().err
    assert main([*args, "--corporate-options", str(tmp_path / "missing.csv"), "--out-dir", str(tmp_path / "y")]) == 2
    assert "--corporate-options" in capsys.readouterr().err


# ---------------------------------------------------------------- fixes of the review


def _lines(text: str) -> dict:
    return {line[:19].strip(): line[19:].strip() for line in text.splitlines() if line[:19].strip()}


def test_frb_commands_default_to_the_trade_days_start(tmp_path, capsys):
    """Without --as-of the FRB commands used every coupon in the inputs, RBI's announcement of 29 Apr 2026 included,
    for a settlement of 2 Jun 2025 (a 49.5 bp look-ahead in asset-swaps' curve margin)."""
    args = ["frb-price", "--isin", "IN0020210137", "--settlement", "2025-06-02", "--price", "100"]
    assert main(args) == 0
    lines = _lines(capsys.readouterr().out)
    assert lines["as of"].startswith("2025-05-30 00:00") and lines["yield (%)"].startswith("6.988717")
    assert lines["discount margin"].startswith("97.8717 bp")
    assert main([*args, "--as-of", "all"]) == 0  # everything known only when asked, and said so
    out = capsys.readouterr().out
    assert "--as-of all: a look-ahead" in out and _lines(out)["discount margin"].startswith("104.6462 bp")
    assert main(["frb-price", "--isin", "IN0020210160", "--settlement", "2026-01-15", "--price", "100"]) == 2
    assert "too old to project from" in capsys.readouterr().err  # FRB 2028's only base then is from 2021
    assert main(["frb-price", "--isin", "IN0020210160", "--settlement", "2026-01-15", "--price", "100",
                 "--allow-stale"]) == 0
    assert "a stale base" in capsys.readouterr().out


def test_asset_swaps_frbs_only_and_their_default_as_of(tmp_path, capsys):
    quotes = _csv(tmp_path / "q.csv", [{"tenor": k, "rate_pct": v} for k, v in QUOTES.items()])
    bonds = _csv(tmp_path / "bonds.csv", [{"isin": "IN0020210137", "clean_price": "100"}])  # no maturity_date
    args = ["asset-swaps", "--quotes", quotes, "--trade-date", "2025-05-30", "--overnight", "5.80",
            "--max-extrapolation-years", "10", "--project-calendar", "--bonds", bonds, "--out",
            str(tmp_path / "a.csv"), "--frb-out", str(tmp_path / "f.csv")]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "1 of 1 GoI FRBs measured, their coupons as known at 2025-05-30 00:00 (00:00 on the trade date" in out
    frb = pd.read_csv(tmp_path / "f.csv").iloc[0]
    assert (frb["coupon_pct"], frb["next_coupon_pct"]) == (6.99, 6.99)  # not the coupon announced in Apr 2026


def test_corp_price_prices_mixed_rows_and_fails_when_nothing_is_priced(tmp_path, capsys, isin):
    t17 = isin("INE000T1701")
    terms = _csv(tmp_path / "terms.csv", [{**T17_TERMS, "isin": t17}])
    prices = _csv(tmp_path / "p.csv", [{"isin": t17, "settlement_date": "2026-11-16", "clean_price": T17_CLEAN,
                                        "yield_pct": ""},
                                       {"isin": t17, "settlement_date": "2026-11-16", "clean_price": "",
                                        "yield_pct": "8.2612262724"}])
    assert main(["corp-price", "--terms", terms, "--prices", prices, "--out", str(tmp_path / "o.csv")]) == 0
    out = pd.read_csv(tmp_path / "o.csv")
    assert out["error"].fillna("").tolist() == ["", ""]
    assert out["clean_price"].tolist() == pytest.approx([T17_CLEAN] * 2, abs=1e-8)
    assert "2 of 2 rows priced" in capsys.readouterr().out
    refused = _csv(tmp_path / "r.csv", [{"isin": isin("INE000NOT01"), "settlement_date": "2026-11-16",
                                         "clean_price": 100}])
    assert main(["corp-price", "--terms", terms, "--prices", refused, "--out", str(tmp_path / "x.csv")]) == 1
    assert "refused" in capsys.readouterr().out


def test_frb_coupons_command(tmp_path, capsys):
    out_path = tmp_path / "coupons.csv"
    assert main(["frb-coupons", "--as-of", "2026-11-19T20:00", "--isin", "IN0020180041", "--out",
                 str(out_path)]) == 0
    assert "1 GoI FRBs" in capsys.readouterr().out
    rates = pd.read_csv(out_path)
    assert set(rates["isin"]) == {"IN0020180041"} and rates.columns.tolist()[:4] == ["isin", "period_start",
                                                                                        "period_end", "fixing_day"]
    row = rates.set_index("period_start").loc["2026-06-07"]
    assert (row["status"], row["rate_pct"]) == ("projected", 6.52)
    assert main(["frb-coupons", "--out", str(out_path)]) == 2  # a time is required
    assert "--as-of is required" in capsys.readouterr().err
    nss = _csv(tmp_path / "nss.csv", [rt.nss_params_row(CURVE, date(2026, 11, 13))])
    assert main(["frb-coupons", "--as-of", "2026-11-19T20:00", "--isin", "IN0020180041", "--projection", "forward",
                 "--nss-params", nss, "--out", str(out_path)]) == 0
    capsys.readouterr()
    forward = pd.read_csv(out_path).set_index("period_start")
    assert forward.loc["2027-06-07", "public_from_source"] == "forward"
