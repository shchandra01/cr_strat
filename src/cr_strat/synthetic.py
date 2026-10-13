"""Synthetic Indian corporate bond market for exercising the pipeline and checking research code.

Everything here is simulated; nothing is market evidence. The files use the
same formats as the real inputs (``data/README.md``) so every command runs on
them, and the simulation plants effects of known size:

* a G-sec par curve (semi-annual) driven by level and slope factors, with one
  credit stress episode;
* issuers rated by one or two agencies whose ratings migrate, with a negative
  outlook ahead of some downgrades (the market prices part of the next notch
  from the outlook on);
* bonds whose fair spread is a rating level, a tenor term and a sector term,
  plus a common credit factor, an issuer factor and a mean-reverting
  bond-specific factor (a genuine, small relative-value effect);
* **forced-selling overshoot**: when an issuer's worst rating falls below AA+,
  the corporate bond fund line, its spreads widen by an extra
  ``overshoot_bps_per_share`` times the share of its bonds those funds held,
  peaking about a week later and decaying with ``overshoot_half_life_days``
  (business days). The funds sell out over the following weeks, and trading
  picks up meanwhile;
* reported trades on random days with yield noise, split between NSE and BSE;
* month-end portfolios of six MF schemes, two of them corporate bond funds.

``generate_market`` returns the tables plus ``truth``: one row per planted
crossing with the constrained share and the peak overshoot.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from cr_strat.bond_math import Bond
from cr_strat.curves import convert_yield
from cr_strat.isin import is_valid_isin
from cr_strat.rating_scale import LT_GRADES, LT_NOTCH

TENORS = (0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 15.0, 30.0)
BASE_SPREAD_BPS = {
    "AAA": 55, "AA+": 85, "AA": 115, "AA-": 145, "A+": 185, "A": 230, "A-": 280,
    "BBB+": 350, "BBB": 420, "BBB-": 500, "BB+": 650, "BB": 800,
}
SECTOR_BPS = {"PSU": -15, "Bank": -5, "Corporate": 0, "NBFC": 15, "HFC": 20}
SECTOR_MIX = {"PSU": 0.2, "Bank": 0.1, "Corporate": 0.3, "NBFC": 0.25, "HFC": 0.15}
INITIAL_RATING = {
    "PSU": {"AAA": 0.8, "AA+": 0.2},
    "Bank": {"AAA": 0.4, "AA+": 0.3, "AA": 0.2, "AA-": 0.1},
    "Corporate": {"AAA": 0.2, "AA+": 0.25, "AA": 0.2, "AA-": 0.15, "A+": 0.1, "A": 0.05, "A-": 0.05},
    "NBFC": {"AAA": 0.1, "AA+": 0.3, "AA": 0.25, "AA-": 0.15, "A+": 0.1, "A": 0.1},
    "HFC": {"AAA": 0.1, "AA+": 0.3, "AA": 0.25, "AA-": 0.15, "A+": 0.1, "A": 0.1},
}
# Annual hazard of a downgrade / upgrade by grade.
DOWN_HAZARD = {"AAA": 0.03, "AA+": 0.12, "AA": 0.12, "AA-": 0.14, "A+": 0.16, "A": 0.18, "A-": 0.2,
               "BBB+": 0.22, "BBB": 0.24, "BBB-": 0.26, "BB+": 0.28, "BB": 0.0}
UP_HAZARD = 0.04
AGENCIES = ("CRISIL", "ICRA", "CARE", "IND")
SCHEMES = (
    ("ALPHA-CBF", "Alpha Corporate Bond Fund", "Synthetic Alpha Mutual Fund", "Debt Scheme - Corporate Bond Fund"),
    ("BETA-CBF", "Beta Corporate Bond Fund", "Synthetic Beta Mutual Fund", "Debt Scheme - Corporate Bond Fund"),
    ("ALPHA-SDF", "Alpha Short Duration Fund", "Synthetic Alpha Mutual Fund", "Debt Scheme - Short Duration Fund"),
    ("ALPHA-CRF", "Alpha Credit Risk Fund", "Synthetic Alpha Mutual Fund", "Debt Scheme - Credit Risk Fund"),
    ("BETA-BPSU", "Beta Banking and PSU Fund", "Synthetic Beta Mutual Fund", "Debt Scheme - Banking and PSU Fund"),
    ("BETA-MDF", "Beta Medium Duration Fund", "Synthetic Beta Mutual Fund", "Debt Scheme - Medium Duration Fund"),
)
_CBF_LIMIT = LT_NOTCH["AA+"]
_FLOOR = LT_NOTCH["BB"]


@dataclass
class SyntheticConfig:
    start: str = "2019-01-01"
    end: str = "2024-12-31"
    n_issuers: int = 200
    seed: int = 7
    overshoot_bps_per_share: float = 300.0
    overshoot_half_life_days: float = 30.0
    sell_down_days: int = 40
    base_trade_prob: float = 0.12
    trade_noise_bps: float = 2.5


def _check_digit(payload: str) -> str:
    return next(payload + d for d in "0123456789" if is_valid_isin(payload + d))


def _base36(n: int, width: int) -> str:
    digits = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    out = ""
    for _ in range(width):
        n, r = divmod(n, 36)
        out = digits[r] + out
    return out


def _rating_string(agency: str, grade: str, outlook: str) -> str:
    return {"CRISIL": f"CRISIL {grade}/{outlook}", "ICRA": f"[ICRA]{grade} ({outlook})",
            "CARE": f"CARE {grade}; {outlook}", "IND": f"IND {grade}/{outlook}"}[agency]


def _ou(rng, n_steps: int, n: int, vol: float, speed: float, x0=0.0) -> np.ndarray:
    x = np.empty((n_steps, n))
    cur = np.full(n, x0, dtype=float)
    shocks = rng.normal(0.0, vol, (n_steps, n))
    for t in range(n_steps):
        cur = cur - speed * cur + shocks[t]
        x[t] = cur
    return x


def _curve(rng, days: pd.DatetimeIndex) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    n = len(days)
    level = 6.0 + _ou(rng, n, 1, 0.035, 1 / 250)[:, 0]
    slope = 1.2 + _ou(rng, n, 1, 0.015, 1 / 250)[:, 0]
    tenors = np.array(TENORS)
    grid = level[:, None] + slope[:, None] * (1 - np.exp(-tenors[None, :] / 3.0))
    curve = pd.DataFrame({"date": np.repeat(days, len(tenors)), "tenor_years": np.tile(tenors, n),
                          "yield_pct": np.round(grid.ravel(), 4)})
    return curve, level, slope


def _par_yield(level, slope, tenor):
    return level + slope * (1 - np.exp(-np.maximum(tenor, 0.0) / 3.0))


def generate_market(cfg: SyntheticConfig | None = None) -> dict[str, pd.DataFrame]:
    cfg = cfg or SyntheticConfig()
    rng = np.random.default_rng(cfg.seed)
    days = pd.bdate_range(cfg.start, cfg.end)
    n_days = len(days)
    curve, level, slope = _curve(rng, days)

    # -- issuers and rating paths ----------------------------------------------------------------
    sectors = rng.choice(list(SECTOR_MIX), size=cfg.n_issuers, p=list(SECTOR_MIX.values()))
    issuers = []
    for k, sector in enumerate(sectors):
        mix = INITIAL_RATING[sector]
        grade = rng.choice(list(mix), p=list(mix.values()))
        n_ag = 1 + int(rng.random() < 0.5)
        issuers.append({
            "issuer_id": f"SYN{k + 1:04d}", "issuer_name": f"Synthetic Issuer {k + 1:03d} Ltd", "sector": sector,
            "notch0": LT_NOTCH[grade], "agencies": tuple(rng.choice(AGENCIES, size=n_ag, replace=False)),
            "cbf_share": rng.uniform(0.05, 0.45) if LT_NOTCH[grade] <= _CBF_LIMIT else rng.uniform(0.0, 0.03),
            "other_share": rng.uniform(0.0, 0.25),
        })

    worst = np.zeros((n_days, cfg.n_issuers), dtype=int)
    anticipation = np.zeros((n_days, cfg.n_issuers))
    actions, crossings = [], []
    first_public = days[0] - pd.Timedelta(days=30)
    for j, iss in enumerate(issuers):
        notch, last, lead_path = iss["notch0"], -10_000, []
        for d in range(n_days):
            if d - last < 60:
                continue
            grade = LT_GRADES[notch - 1]
            u = rng.random()
            if u < DOWN_HAZARD[grade] / 252:
                step = int(rng.choice([1, 2, 3], p=[0.75, 0.2, 0.05]))
                new = min(notch + step, _FLOOR)
            elif u < (DOWN_HAZARD[grade] + (UP_HAZARD if notch > 1 else 0.0)) / 252:
                new = notch - 1
            else:
                continue
            if new != notch:
                lead_path.append((d, new))
                notch, last = new, d
        per_agency = []
        for a, agency in enumerate(iss["agencies"]):
            path = np.full(n_days, iss["notch0"])
            actions.append({"date": first_public, "issuer_id": iss["issuer_id"], "issuer_name": iss["issuer_name"],
                            "agency": agency, "rating": _rating_string(agency, LT_GRADES[iss["notch0"] - 1], "Stable")})
            prev_d = -1
            for d, new in lead_path:
                lag = 0 if a == 0 or rng.random() < 0.3 else int(rng.integers(1, 26))
                d_a = max(min(d + lag, n_days - 1), prev_d + 1)
                if d_a >= n_days:
                    break
                path[d_a:] = new
                prev_d = d_a
                downgrade = d_a > 0 and new > path[d_a - 1]
                outlook = "Negative" if downgrade and rng.random() < 0.25 else "Stable"
                actions.append({"date": days[d_a], "issuer_id": iss["issuer_id"], "issuer_name": iss["issuer_name"],
                                "agency": agency, "rating": _rating_string(agency, LT_GRADES[new - 1], outlook)})
            per_agency.append(path)
        worst[:, j] = np.max(per_agency, axis=0)
        # Negative outlooks ahead of half the downgrades, by the lead agency; the market prices
        # part of the next notch from then on.
        prev_d, cur = -1, iss["notch0"]
        for d, new in lead_path:
            if new > cur and rng.random() < 0.5:
                o = d - int(rng.integers(20, 61))
                if o > prev_d + 1 and o > 0:
                    agency = iss["agencies"][0]
                    actions.append({"date": days[o], "issuer_id": iss["issuer_id"],
                                    "issuer_name": iss["issuer_name"], "agency": agency,
                                    "rating": _rating_string(agency, LT_GRADES[cur - 1], "Negative")})
                    gap = BASE_SPREAD_BPS[LT_GRADES[min(cur, _FLOOR - 1)]] - BASE_SPREAD_BPS[LT_GRADES[cur - 1]]
                    anticipation[o:d, j] = 0.3 * gap
            prev_d, cur = d, new
        for d in np.flatnonzero((worst[1:, j] > _CBF_LIMIT) & (worst[:-1, j] <= _CBF_LIMIT)) + 1:
            crossings.append((j, int(d)))

    # -- bonds -----------------------------------------------------------------------------------
    start, end = days[0], days[-1]
    bonds = []
    for j, iss in enumerate(issuers):
        n_b = int(rng.integers(1, 5)) + (1 if iss["sector"] == "PSU" else 0)
        for b in range(n_b):
            tenor = int(rng.choice([3, 5, 7, 10], p=[0.35, 0.35, 0.15, 0.15]))
            issue = start - pd.Timedelta(days=int(rng.integers(0, 5 * 365))) if rng.random() < 0.55 \
                else start + pd.Timedelta(days=int(rng.integers(0, max((end - start).days - 365, 1))))
            maturity = issue + pd.DateOffset(years=tenor)
            if maturity <= start + pd.Timedelta(days=60):
                continue
            payload = f"INE{j + 1:03d}S07{_base36(b, 2)}"
            bonds.append({
                "isin": _check_digit(payload), "issuer_idx": j, "issuer_id": iss["issuer_id"],
                "issue_date": issue.normalize(), "maturity_date": maturity.normalize(),
                "face_value_inr": 1e6 if issue < pd.Timestamp("2022-10-01") else 1e5,
                "outstanding_face_inr": float(rng.integers(20, 300)) * 1e8,
                "liquidity": float(np.clip(rng.lognormal(0.0, 0.5), 0.2, 3.0)),
            })
    sec = pd.DataFrame(bonds)
    n_bonds = len(sec)
    bi = sec["issuer_idx"].to_numpy()

    # -- spreads -----------------------------------------------------------------------------------
    t_days = days.to_numpy(dtype="datetime64[D]")
    maturity = sec["maturity_date"].to_numpy(dtype="datetime64[D]")
    tenor = (maturity[None, :] - t_days[:, None]).astype(float) / 365.25
    base = np.vectorize(lambda n: BASE_SPREAD_BPS[LT_GRADES[n - 1]])(worst)[:, bi]
    sector_bps = np.array([SECTOR_BPS[issuers[j]["sector"]] for j in bi])
    market = _ou(rng, n_days, 1, 1.5, 1 / 120)[:, 0]
    stress_at = int(n_days * 0.35)
    market[stress_at:] += 60.0 * np.exp(-np.arange(n_days - stress_at) / 90.0)
    issuer_f = _ou(rng, n_days, cfg.n_issuers, 2.0, 1 / 60)[:, bi]
    bond_f = _ou(rng, n_days, n_bonds, 1.5, 1 / 20)
    overshoot = np.zeros((n_days, cfg.n_issuers))
    hot = np.zeros((n_days, cfg.n_issuers), dtype=bool)
    truth, seen = [], set()
    for j, d0 in crossings:
        # Corporate bond funds sell out after the first crossing and do not come back.
        share = 0.0 if j in seen else issuers[j]["cbf_share"]
        seen.add(j)
        peak = cfg.overshoot_bps_per_share * share * max(0.2, 1 + 0.3 * rng.normal())
        k = np.arange(n_days - d0)
        decay = 0.5 ** (np.maximum(k - 4, 0) / cfg.overshoot_half_life_days)
        overshoot[d0:, j] += peak * np.minimum(1.0, (k + 1) / 5) * decay
        hot[d0:d0 + 20, j] = True
        truth.append({"issuer_id": issuers[j]["issuer_id"], "date": days[d0], "constrained_share_planted": share,
                      "peak_overshoot_bps": peak, "prev_grade": LT_GRADES[worst[d0 - 1, j] - 1],
                      "new_grade": LT_GRADES[worst[d0, j] - 1]})
    spread = (base * (1 + 0.06 * (tenor - 3)) + sector_bps[None, :] + market[:, None] * base / 100
              + issuer_f + bond_f + anticipation[:, bi] + overshoot[:, bi])
    spread = np.maximum(spread, 10.0)
    gsec = convert_yield(_par_yield(level[:, None], slope[:, None], tenor), "semiannual", "annual")
    fair_yield = gsec + spread / 100

    # Coupons near the fair yield at issue (or on day one for bonds issued before the sample).
    issue_idx = np.clip(np.searchsorted(t_days, sec["issue_date"].to_numpy(dtype="datetime64[D]")), 0, n_days - 1)
    pre = sec["issue_date"] < start
    coupon = fair_yield[issue_idx, np.arange(n_bonds)] + np.where(pre, rng.normal(0, 0.4, n_bonds), 0.0)
    sec["coupon_pct"] = np.round(coupon, 2)
    sec["coupon_freq"] = 1
    sec["instrument_class"] = "BOND"
    sec["sector"] = [issuers[j]["sector"] for j in bi]
    sec["issuer_name"] = [issuers[j]["issuer_name"] for j in bi]

    # -- trades ------------------------------------------------------------------------------------
    alive = (t_days[:, None] >= sec["issue_date"].to_numpy(dtype="datetime64[D]")[None, :]) & (tenor > 7 / 365)
    prob = cfg.base_trade_prob * sec["liquidity"].to_numpy()[None, :] * np.where(hot[:, bi], 4.0, 1.0)
    traded = alive & (rng.random((n_days, n_bonds)) < np.minimum(prob, 0.95))
    di, bj = np.nonzero(traded)
    n_per = 1 + rng.poisson(0.8, len(di))
    di, bj = np.repeat(di, n_per), np.repeat(bj, n_per)
    yields = fair_yield[di, bj] + rng.normal(0, cfg.trade_noise_bps / 100, len(di))
    lakhs = np.round(np.exp(rng.normal(np.log(800), 0.8, len(di))) / 10) * 10
    trades = pd.DataFrame({"date": days[di], "bond": bj, "yield_pct": np.round(yields, 4),
                           "amount_lakhs": np.maximum(lakhs, 10)})
    trades["price"] = np.nan
    for b, g in trades.groupby("bond"):
        row = sec.iloc[b]
        bond = Bond(row["maturity_date"], row["coupon_pct"], 1, issue_date=row["issue_date"])
        trades.loc[g.index, "price"] = np.round(bond.analytics(g["date"], g["yield_pct"])["clean"], 4)
    trades["isin"] = sec["isin"].to_numpy()[trades["bond"]]
    names = [f"SYN{n.split()[2]} NCD {m.year}" for n, m in zip(sec["issuer_name"], sec["maturity_date"])]
    labels = np.array([f"{c:.2f}% {n}" for c, n in zip(sec["coupon_pct"], names)])
    trades["description"] = labels[trades["bond"].to_numpy()]
    trades["venue"] = np.where(rng.random(len(trades)) < 0.7, "NSE", "BSE")
    trades = trades.sort_values(["date", "isin"], kind="stable").reset_index(drop=True)

    # -- MF holdings at month ends -----------------------------------------------------------------
    month_ends = pd.Series(days).groupby(days.to_period("M")).max().to_numpy()
    me_idx = np.searchsorted(days, month_ends)
    cbf_split = rng.uniform(0.3, 0.7, cfg.n_issuers)
    other_split = rng.dirichlet(np.ones(4), cfg.n_issuers)
    first_cross = {}
    for j, d0 in crossings:
        first_cross.setdefault(j, d0)
    hold_rows = []
    for b in range(n_bonds):
        row = sec.iloc[b]
        j = int(row["issuer_idx"])
        iss = issuers[j]
        bond = Bond(row["maturity_date"], row["coupon_pct"], 1, issue_date=row["issue_date"])
        live = me_idx[alive[me_idx, b]]
        if not len(live):
            continue
        clean = bond.analytics(days[live], fair_yield[live, b])["clean"]
        for d, px in zip(live, clean):
            cbf = iss["cbf_share"]
            d0 = first_cross.get(j)
            if d0 is not None and d >= d0:
                cbf *= max(0.0, 1 - (d - d0) / cfg.sell_down_days)
            shares = [cbf * cbf_split[j], cbf * (1 - cbf_split[j]), *(iss["other_share"] * other_split[j])]
            for (sid, sname, amc, cat), share in zip(SCHEMES, shares):
                units = int(share * row["outstanding_face_inr"] / row["face_value_inr"])
                if units <= 0:
                    continue
                face = units * row["face_value_inr"]
                hold_rows.append({"as_of_date": days[d], "holder_type": "MF", "holder_id": sid, "holder_name": sname,
                                  "amc": amc, "category": cat, "isin": row["isin"], "quantity": units,
                                  "market_value_inr": round(face * px / 100, 2)})

    actions_df = pd.DataFrame(actions)
    actions_df["instrument"] = "Non-convertible debentures"
    actions_df = actions_df.sort_values(["date", "issuer_id", "agency"], kind="stable").reset_index(drop=True)
    sec_out = sec[["isin", "issuer_id", "issuer_name", "instrument_class", "sector", "face_value_inr",
                   "outstanding_face_inr", "issue_date", "maturity_date", "coupon_pct", "coupon_freq"]]
    return {
        "curve": curve,
        "rating_actions": actions_df[["date", "issuer_id", "issuer_name", "agency", "instrument", "rating"]],
        "securities": sec_out,
        "trades": trades[["date", "isin", "description", "amount_lakhs", "price", "yield_pct", "venue"]],
        "holdings": pd.DataFrame(hold_rows),
        "truth": pd.DataFrame(truth, columns=["issuer_id", "date", "constrained_share_planted",
                                              "peak_overshoot_bps", "prev_grade", "new_grade"]),
    }


def write_market(market: dict[str, pd.DataFrame], out_dir: str | Path) -> dict[str, Path]:
    """Write the synthetic tables as raw input files; returns the paths by name."""
    out = Path(out_dir)
    (out / "trades" / "nse").mkdir(parents=True, exist_ok=True)
    (out / "trades" / "bse").mkdir(parents=True, exist_ok=True)
    paths = {
        "curve": out / "gsec_par_curve.csv",
        "rating_actions": out / "rating_actions.csv",
        "securities": out / "securities.csv",
        "holdings": out / "holdings_mf.csv",
        "truth": out / "truth_events.csv",
    }
    for name, path in paths.items():
        market[name].to_csv(path, index=False, date_format="%Y-%m-%d")
    trades = market["trades"]
    for (venue, year), g in trades.groupby([trades["venue"], trades["date"].dt.year]):
        settle = g["date"] + pd.offsets.BDay(1)
        lines = pd.DataFrame({
            "Trade Date": g["date"].dt.strftime("%d-%b-%Y"), "ISIN": g["isin"], "Description": g["description"],
            "Deal Size (Rs. Lakhs)": g["amount_lakhs"], "Price": g["price"], "Yield": g["yield_pct"],
            "Settlement Date": settle.dt.strftime("%d-%b-%Y"),
        })
        path = out / "trades" / venue.lower() / f"{venue.lower()}_trades_{year}.csv"
        with open(path, "w") as fh:
            fh.write(f"Corporate Bonds - Reported Trades ({venue}, synthetic)\n\n")
            lines.to_csv(fh, index=False)
        paths[f"trades_{venue.lower()}_{year}"] = path
    (out / "SYNTHETIC.txt").write_text(
        "Every file in this folder is simulated by cr_strat.synthetic. Nothing here is market data.\n")
    return paths
