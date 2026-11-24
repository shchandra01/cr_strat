"""Synthetic inputs for the limits, gate and daily-process tests (tests/test_limits.py, test_daily.py,
test_cli_limits.py): a return-engine run of two books (two G-secs and an AA corporate bond) on NSS fits with known
parameters and public times, its risk run, OIS curves from the tests' B3 quotes, and helpers that build a gate's state
from positions rows without the engine. Nothing here is a real price; the bonds' terms are typed-in facts (6.94% GS
2036, 7.09% GS 2054, 6.36% GS 2031) and a made-up NCD."""

from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import risk_fixtures as fx

from cr_strat import limits as lm
from cr_strat import returns as rt
from cr_strat import risk as rk

END = date(2026, 11, 19)
N_FITS = 90  # business days of NSS fits to END: 89 changes
NCD = "INE000A07011"
NCD_TERMS = [{"isin": NCD, "issuer_id": "ISS1", "kind": "corporate", "coupon_pct": "9.0", "frequency": "1",
              "issue_date": "2023-12-15", "maturity_date": "2030-12-15", "rating_bucket": "AA",
              "sector": "financials"}]
BONDS = {"GS2036": fx.GS2036, "GS2054": fx.GS2054, "GS2031": fx.GS2031}
MASTER = pd.DataFrame([{"isin": k, "kind": v[0], "coupon_pct": v[1], "maturity_date": v[2]}
                       for k, v in fx.TERMS.items()])
_CACHE: dict = {}


def fits():
    """NSS fits on the 90 business days to END, the level moving by a cycled permutation of the T9 grid."""
    if "fits" not in _CACHE:
        days = fx.history_days(END, N_FITS)
        g = fx.grid()
        moves = [0.0] + [g[(7 * i) % 250] for i in range(N_FITS - 1)]
        _CACHE["fits"] = (days, moves, fx.nss_entries(days, level_moves_bp=moves))
    return _CACHE["fits"]


def ncd_bond():
    st = rt.EngineSettings(access=rt.ACCESS_PRESETS["nbfc_non_pd"])
    return rt.instruments_as_known(rt.MarketData(corporate_terms=pd.DataFrame(NCD_TERMS)), st, [NCD],
                                   datetime(2026, 11, 13, 20))[NCD].bond


def market_and_trades():
    """``(market, trades, flows, entries, run days)``: marks of every bond on each day from 13 Nov 2026 (the G-secs
    3 bp above the fitted curve, the NCD 100 bp above it), with traded volumes; book b buys Rs 100 crore of GS2036 on
    13 Nov, book c Rs 50 crore of GS2054 and Rs 20 crore of the NCD on 16 Nov; capital Rs 200 and 100 crore."""
    days, _, entries = fits()
    run_days = [d for d in days if d >= date(2026, 11, 13)]
    ncd = ncd_bond()
    marks = []
    for d in run_days:
        c = entries[d].curve
        s = c.valuation_date
        for isin, bond in BONDS.items():
            y = fx.on_curve_yield(bond, c, s) + 0.03
            marks.append({"date": d, "isin": isin, "clean_price": bond.clean_price(y, s),
                          "volume_cr": {"GS2036": 500.0, "GS2054": 40.0, "GS2031": 200.0}[isin],
                          "public_from": fx.at(d, 18).isoformat()})
        y = fx.on_curve_yield(ncd, c, s, 0.01)
        marks.append({"date": d, "isin": NCD, "clean_price": ncd.clean_price(y, s), "volume_cr": 2.0,
                      "public_from": fx.at(d, 18).isoformat()})
    marks = pd.DataFrame(marks)
    price = {(r["date"], r["isin"]): r["clean_price"] for r in marks.to_dict("records")}
    trades = pd.DataFrame([
        {"trade_id": "T1", "book": "b", "isin": "GS2036", "kind": "gsec", "coupon_pct": 6.94,
         "maturity_date": "2036-05-11", "side": "buy", "face": 1e9, "trade_date": "2026-11-13",
         "price": price[(date(2026, 11, 13), "GS2036")], "liquidity": "liquid"},
        {"trade_id": "T2", "book": "c", "isin": "GS2054", "kind": "gsec", "coupon_pct": 7.09,
         "maturity_date": "2054-08-05", "side": "buy", "face": 5e8, "trade_date": "2026-11-16",
         "price": price[(date(2026, 11, 16), "GS2054")], "liquidity": "liquid"},
        {"trade_id": "T3", "book": "c", "isin": NCD, "side": "buy", "face": 2e8, "trade_date": "2026-11-16",
         "price": price[(date(2026, 11, 16), NCD)]}])
    flows = pd.DataFrame([{"date": "2026-11-13", "book": "b", "amount": 2e9},
                          {"date": "2026-11-13", "book": "c", "amount": 1e9}])
    market = rt.MarketData(marks=marks, curves={"gsec": entries}, corporate_terms=pd.DataFrame(NCD_TERMS),
                           master=MASTER)
    return market, trades, flows, entries, run_days


def context(market, preset, *, trades=None, as_of=None, project_calendar=False, **kw) -> rk.RiskContext:
    _, _, entries = fits()
    st = rt.EngineSettings(access=rt.ACCESS_PRESETS[preset], project_calendar=project_calendar)
    return rk.RiskContext(market=market, settings=st, risk=rk.RiskSettings(min_obs_hard=60),
                          as_of=as_of or fx.at(END, 20), terms=trades, nss_table={"gsec": entries}, **kw)


def engine_and_risk(preset="unregulated_company"):
    """``(market, trades, engine result, context, risk result)`` of the book on END under ``preset`` (cached)."""
    key = ("run", preset)
    if key not in _CACHE:
        market, trades, flows, entries, _ = market_and_trades()
        st = rt.EngineSettings(access=rt.ACCESS_PRESETS[preset])
        res = rt.run_book(trades, None, flows, market=market, settings=st, end=END)
        ctx = context(market, preset, trades=trades, nav=res.nav, funding=res.funding)
        ctx = lm.with_cost_table(ctx)
        day = res.positions[res.positions["date"] == END]
        _CACHE[key] = (market, trades, res, ctx, rk.run_risk(day, ctx, day=END))
    return _CACHE[key]


def nav_rows(day, books: dict) -> pd.DataFrame:
    """NAV rows of one day: ``{book: (nav, cash)}`` (flow and P&L 0)."""
    return pd.DataFrame([{"date": day, "book": b, "nav": n, "cash": c, "repo_borrowed": 0.0, "lent": 0.0,
                          "receivables": 0.0, "flow": 0.0, "pnl": 0.0} for b, (n, c) in books.items()])


def state(rows, books: dict, ctx, *, day=END, orders=None, scenarios=None):
    """A gate state from positions rows (``returns.POSITION_COLUMNS``, e.g. ``risk_fixtures.bond_row``) and
    ``{book: (nav, cash)}``, without the engine."""
    engine = {"positions": pd.DataFrame(rows, columns=list(rt.POSITION_COLUMNS)) if rows else
              pd.DataFrame(columns=list(rt.POSITION_COLUMNS)), "nav": nav_rows(day, books)}
    return lm.state_from_result(engine, day, ctx, orders=orders, scenarios=scenarios)


def gsec_row(isin, face, *, book="b", day=END, z=0.0):
    bond = BONDS[isin]
    _, _, entries = fits()
    s = fx.CAL.settlement_date(day, 1)
    y = fx.on_curve_yield(bond, entries[day].curve, s, z) + 0.03
    return fx.bond_row(bond, isin, "gsec", face, day, s, y, book=book)


def ois_entries():
    """OIS curves on the fits' days: the B3 curve of END moved in parallel by the same changes as the G-sec level."""
    days, moves, _ = fits()
    base = fx.b3_curve(END)
    return fx.ois_entries(days, base, moves_bp=moves)


def ois_market(*, fixings=True):
    market, trades, flows, entries, run_days = market_and_trades()
    fix = {d: 5.60 for d in fx.CAL.business_days(date(2026, 10, 1), END)} if fixings else {}
    return replace(market, ois_curves=ois_entries(), fixings=fix), trades


def scenario_rows(ids=("IRRBB_INR_PARALLEL_UP",)):
    from cr_strat import stress as st
    return [r for r in st.read_scenarios() if r.scenario_id in ids]


def limit_frame(rows) -> pd.DataFrame:
    """A limit table from dicts (missing columns blank)."""
    return pd.DataFrame([{c: r.get(c, "") for c in lm.LIMIT_COLUMNS} for r in rows])


def approved(**kw) -> dict:
    base = {"limit_id": "X1", "name": "test", "metric": "dv01", "scope_type": "fund", "scope": "fund", "selector": "",
            "unit": "rs_per_bp", "direction": "max", "warning_level": "", "hard_level": "1e9", "legal_form": "any",
            "policy_status": "APPROVED", "status": "U", "counsel": "no", "action": "block_increase"}
    base.update(kw)
    return base


ORDERS = pd.DataFrame([{"book": "b", "isin": "GS2036", "side": "buy", "face": 1e8},
                       {"book": "c", "isin": NCD, "side": "buy", "face": 3e7},
                       {"book": "b", "isin": "GS2054", "side": "sell", "face": 1e8}])
DAY1, DAY2 = date(2026, 11, 18), date(2026, 11, 19)


def paper_fund(root, preset="unregulated_company", *, allow_example=True, limits="example", extra="") -> Path:
    """A paper fund's folder for ``daily.run_daily``: the marks, NSS fits and NCD terms of ``market_and_trades``, the
    capital flows, the paper trades (13 and 16 Nov), an orders file for DAY1 and ``config.toml`` (``extra`` added to
    its top-level keys)."""
    market, trades, flows, entries, _ = market_and_trades()
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    market.marks.to_csv(root / "marks.csv", index=False)
    pd.DataFrame([rt.nss_params_row(e.curve, d, public_from=e.public_from) for d, e in entries.items()]).to_csv(
        root / "nss.csv", index=False)
    pd.DataFrame(NCD_TERMS).to_csv(root / "corporate_terms.csv", index=False)
    flows.to_csv(root / "flows.csv", index=False)
    trades.to_csv(root / "trades.csv", index=False)
    ORDERS.to_csv(root / "orders.csv", index=False)
    (root / "config.toml").write_text(f"""fund = "paper"
access = "{preset}"
inception = "2026-11-13"
board_resolution_date = "2026-04-02"
allow_example_limits = {str(allow_example).lower()}
limits = "{limits}"
{extra}
[inputs]
marks = "marks.csv"
nss_params = "nss.csv"
corporate_terms = "corporate_terms.csv"
flows = "flows.csv"
trades = "trades.csv"

[risk]
min_obs_hard = 60
""", encoding="utf-8")
    return root


def append_approved(root, day) -> None:
    """The operator's step: the day's approved trades appended to the paper trades file."""
    root = Path(root)
    book = pd.read_csv(root / "trades.csv", dtype=str, keep_default_na=False)
    new = pd.read_csv(root / day.isoformat() / "orders" / "approved_trades.csv", dtype=str, keep_default_na=False)
    pd.concat([book, new], ignore_index=True).to_csv(root / "trades.csv", index=False)


def files_under(root) -> dict[str, bytes]:
    root = Path(root)
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


__all__ = ["END", "NCD", "NCD_TERMS", "BONDS", "MASTER", "fits", "market_and_trades", "context", "engine_and_risk",
           "nav_rows", "state", "gsec_row", "ois_entries", "ois_market", "scenario_rows", "limit_frame", "approved",
           "ORDERS", "DAY1", "DAY2", "paper_fund", "append_approved", "files_under", "np", "fx"]
