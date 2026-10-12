"""ISIN validation (ISO 6166)."""

from __future__ import annotations

import re

_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


def is_valid_isin(value: object) -> bool:
    """True if ``value`` is a well-formed ISIN with a correct check digit."""
    if not isinstance(value, str):
        return False
    isin = value.strip().upper()
    if not _ISIN_RE.match(isin):
        return False
    # Letters become two-digit numbers (A=10 ... Z=35), then Luhn applies,
    # doubling every other digit starting from the rightmost payload digit.
    digits = "".join(str(int(ch, 36)) for ch in isin[:-1])
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - total % 10) % 10 == int(isin[-1])
