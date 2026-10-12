"""Mutual fund scheme master and scheme-category matching.

Which mandate binds a fund depends on its SEBI category ("Debt Scheme -
Corporate Bond Fund", "Debt Scheme - Credit Risk Fund", ...). AMFI's daily
NAV file (NAVAll.txt) lists every scheme under its category heading, so it
doubles as a scheme master. Portfolio disclosures name schemes slightly
differently, so names are normalised (plan and option words removed) before
matching, with a manual override file for the rest.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path

import pandas as pd

_CATEGORY_RE = re.compile(r"^(?:open ended|close ended|interval fund)\s+schemes?\s*\((.+)\)\s*$", re.I)
_PLAN_WORDS = {
    "direct", "regular", "plan", "growth", "option", "options", "idcw", "dividend", "div",
    "payout", "reinvestment", "reinvest", "bonus", "monthly", "quarterly", "half", "yearly",
    "annual", "weekly", "daily", "fortnightly", "retail", "institutional", "inst", "the", "of",
}
_AMC_NOISE_RE = re.compile(
    r"\b(?:mutual fund|asset management company|asset management|amc|limited|ltd|pvt|private|company|co)\b"
)


def normalize_amc(name: object) -> str:
    text = re.sub(r"[^a-z0-9 ]+", " ", str(name).lower().replace("&", " and "))
    return " ".join(_AMC_NOISE_RE.sub(" ", text).split())


def normalize_scheme_name(name: object) -> str:
    text = str(name).lower().replace("&", " and ")
    text = re.sub(r"\(.*?\)", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(t for t in text.split() if t not in _PLAN_WORDS)


def parse_amfi_navall(source: str | Path) -> pd.DataFrame:
    """One row per scheme (plans collapsed) with its AMC and SEBI category."""
    text = Path(source).read_text(encoding="utf-8", errors="replace")
    rows, category, amc = [], None, None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.lower().startswith("scheme code"):
            continue
        m = _CATEGORY_RE.match(line)
        if m:
            category = m.group(1).strip()
            continue
        if ";" not in line:
            amc = line
            continue
        parts = line.split(";")
        if len(parts) < 4:
            continue
        rows.append({
            "amfi_code": parts[0].strip(),
            "amc": amc,
            "category": category,
            "scheme_name": parts[3].strip(),
        })
    df = pd.DataFrame(rows, columns=["amfi_code", "amc", "category", "scheme_name"])
    df["amc_key"] = df["amc"].map(normalize_amc)
    df["scheme_key"] = df["scheme_name"].map(normalize_scheme_name)
    return df.drop_duplicates(["amc_key", "scheme_key"]).reset_index(drop=True)


def attach_categories(
    holdings: pd.DataFrame,
    master: pd.DataFrame,
    overrides: pd.DataFrame | None = None,
    *,
    cutoff: float = 0.88,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fill ``category`` on MF holdings; return the holdings and a per-scheme match report.

    ``overrides`` has columns amc, scheme_name, category and wins over the master.
    """
    override_map = {}
    if overrides is not None:
        for row in overrides.itertuples(index=False):
            override_map[(normalize_amc(row.amc), normalize_scheme_name(row.scheme_name))] = row.category
    amc_keys = sorted(master["amc_key"].unique())
    report = []
    category_by_scheme = {}
    for amc, scheme in holdings[["amc", "holder_name"]].drop_duplicates().itertuples(index=False):
        amc_key, scheme_key = normalize_amc(amc), normalize_scheme_name(scheme)
        result = {"amc": amc, "scheme_name": scheme, "category": None, "match": "none", "matched_name": None}
        if (amc_key, scheme_key) in override_map:
            result.update(category=override_map[(amc_key, scheme_key)], match="override")
        else:
            if amc_key not in amc_keys:
                close = difflib.get_close_matches(amc_key, amc_keys, n=1, cutoff=0.8)
                amc_key = close[0] if close else None
            pool = master if amc_key is None else master[master["amc_key"] == amc_key]
            exact = pool[pool["scheme_key"] == scheme_key]
            if len(exact):
                hit, kind = exact.iloc[0], "exact"
            else:
                close = difflib.get_close_matches(scheme_key, pool["scheme_key"].tolist(), n=1, cutoff=cutoff)
                hit = pool[pool["scheme_key"] == close[0]].iloc[0] if close else None
                kind = "fuzzy"
            if hit is not None:
                result.update(category=hit["category"], match=kind, matched_name=hit["scheme_name"])
        category_by_scheme[(amc, scheme)] = result["category"]
        report.append(result)
    out = holdings.copy()
    out["category"] = [category_by_scheme.get((a, s)) for a, s in zip(out["amc"], out["holder_name"])]
    return out, pd.DataFrame(report, columns=["amc", "scheme_name", "category", "match", "matched_name"])
