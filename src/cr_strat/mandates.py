"""Rating-threshold mandates: who must buy or sell when a rating crosses a line.

A mandate combines the ratings from several agencies into one *effective*
rating (the rule differs by holder: SEBI uses the most conservative rating for
MF credit events, NPS has required two agencies at AA or above) and lists the
thresholds at which eligibility changes. Mandates are loaded from a TOML file;
see ``config/mandates.toml``.
"""

from __future__ import annotations

import math
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from cr_strat.rating_scale import LT_NOTCH

AGGREGATIONS = ("worst", "best", "kth_best", "agency")


def _is_missing(value: object) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


@dataclass(frozen=True)
class HolderRule:
    """Holders a mandate binds: a holder type plus a regex on the holder's category."""

    holder_type: str
    category_pattern: str = ".*"

    def matches(self, holder_type: object, category: object) -> bool:
        if _is_missing(holder_type) or str(holder_type).upper() != self.holder_type.upper():
            return False
        text = "" if _is_missing(category) else str(category)
        return re.search(self.category_pattern, text, flags=re.IGNORECASE) is not None


@dataclass(frozen=True)
class Mandate:
    name: str
    aggregation: str
    thresholds: tuple[str, ...]
    instrument_classes: tuple[str, ...] = ("BOND",)
    k: int = 1
    agency: str | None = None
    holders: tuple[HolderRule, ...] = ()
    description: str = ""
    verify: bool = True
    source: str = ""

    def __post_init__(self) -> None:
        if self.aggregation not in AGGREGATIONS:
            raise ValueError(f"{self.name}: aggregation must be one of {AGGREGATIONS}")
        if self.aggregation == "kth_best" and self.k < 1:
            raise ValueError(f"{self.name}: k must be >= 1")
        if self.aggregation == "agency" and not self.agency:
            raise ValueError(f"{self.name}: aggregation 'agency' needs an agency")
        bad = [t for t in self.thresholds if t not in LT_NOTCH]
        if bad or not self.thresholds:
            raise ValueError(f"{self.name}: thresholds must be long-term grades, got {bad or 'none'}")

    def threshold_notches(self) -> list[tuple[str, int]]:
        return [(t, LT_NOTCH[t]) for t in self.thresholds]

    def effective_notch(self, agency_notches: Mapping[str, int | None]) -> int | None:
        """Combine per-agency notches into the effective notch; None means not eligibly rated."""
        if self.aggregation == "agency":
            return agency_notches.get(self.agency)
        notches = sorted(n for n in agency_notches.values() if n is not None)
        if not notches:
            return None
        if self.aggregation == "worst":
            return notches[-1]
        if self.aggregation == "best":
            return notches[0]
        return notches[self.k - 1] if len(notches) >= self.k else None

    def applies_to(self, holder_type: object, category: object) -> bool:
        return any(rule.matches(holder_type, category) for rule in self.holders)


def load_mandates(path: str | Path) -> list[Mandate]:
    with open(path, "rb") as fh:
        config = tomllib.load(fh)
    mandates = []
    for entry in config.get("mandate", []):
        holders = tuple(HolderRule(**h) for h in entry.get("holders", []))
        mandates.append(
            Mandate(
                name=entry["name"],
                aggregation=entry["aggregation"],
                thresholds=tuple(entry["thresholds"]),
                instrument_classes=tuple(entry.get("instrument_classes", ["BOND"])),
                k=int(entry.get("k", 1)),
                agency=entry.get("agency"),
                holders=holders,
                description=entry.get("description", ""),
                verify=bool(entry.get("verify", True)),
                source=entry.get("source", ""),
            )
        )
    names = [m.name for m in mandates]
    if len(names) != len(set(names)):
        raise ValueError("mandate names must be unique")
    return mandates
