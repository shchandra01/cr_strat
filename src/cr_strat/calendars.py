"""Business-day calendars: Mumbai settlement for G-secs, SDLs, T-bills, CMBs, CP and CD.

A calendar is a weekend plus a holiday list, and it knows which years that
list covers. Asking about a weekday in any other year raises
``CalendarCoverageError``, so a back-test never takes an unknown holiday for a
business day. Weekends need no list and never raise. Pass ``strict=False``, or
call ``lenient()``, to treat unlisted weekdays outside the covered years as
business days, e.g. to project payment dates years ahead.

The shipped list, ``src/cr_strat/data/holidays_mumbai.csv`` (package data, so
it installs with the code), holds the Maharashtra Government's holidays under
section 25 of the Negotiable Instruments Act for 2025 and 2026, plus the
1 April bank closing, which FIMMDA treats as a normal holiday (Handbook of
Market Practices 2026, 4.10.1 and 4.10.3). Saturdays and Sundays are never
settlement days. Exchange trading calendars differ (NSE trades on some of
these days but does not settle debt) and need a list of their own.

The CSV has one row per date: ``date`` (ISO), ``name``, ``source`` (a URL)
and an optional ``confirmed`` (``yes``/``no``) for holidays whose effect on
RBI settlement is not confirmed.

Coverage is declared, never inferred from the rows: one stray row must not
mark a year covered. Beside each holiday CSV, ``<name>_years.csv`` lists every
year the CSV has rows for, with ``complete`` (``yes`` once that year's full
official list is in) and its ``source``. A strict calendar covers only the
complete years; rows of an incomplete year are ignored. A year with rows but
no line in the coverage file is an error. In code, ``BusinessCalendar`` and
``calendar_from_holidays`` take the covered ``years`` explicitly.

``settlement_date(trade_date, lag)`` is the ``lag``-th business day after the
trade date (FIMMDA: "Value t + n means that settlement will be done on nth
settlement day after the trade date"). The trade date itself may be a
settlement holiday: exchanges trade on some. Lag 0 on a non-business day rolls
to the next business day. The module-level ``settlement_date(trade_date,
conventions)`` takes the lag and the calendar from an instrument's conventions
(``settlement_lag`` and ``calendar_name``).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from importlib.resources import files
from pathlib import Path

import numpy as np
import pandas as pd

from cr_strat.conventions import ROLLS, BondConventions, DiscountConventions, as_date, as_int

DATA_DIR = Path(str(files("cr_strat") / "data"))
DEFAULT_HOLIDAYS_CSV = DATA_DIR / "holidays_mumbai.csv"
HOLIDAY_COLUMNS = ("date", "name", "source", "confirmed")
COVERAGE_COLUMNS = ("year", "complete", "source")
_TRUE = {"yes", "y", "true", "1"}
_FALSE = {"no", "n", "false", "0"}
_MAX_ROLL_DAYS = 31


class CalendarCoverageError(ValueError):
    """A date falls in a year the calendar's holiday list does not cover."""


@dataclass(frozen=True, repr=False)
class BusinessCalendar:
    """Weekend plus holidays, for the years in ``years``.

    ``years`` must be given whenever ``holidays`` is not empty: the years whose
    full holiday list ``holidays`` holds. ``weekend`` holds ``date.weekday()``
    numbers (5 = Saturday, 6 = Sunday). With ``strict`` (the default) a weekday
    outside ``years`` raises ``CalendarCoverageError``.
    """

    name: str
    holidays: frozenset[date] = frozenset()
    years: frozenset[int] | None = None
    weekend: frozenset[int] = field(default_factory=lambda: frozenset({5, 6}))
    strict: bool = True

    def __post_init__(self) -> None:
        holidays = frozenset(as_date(d) for d in self.holidays)
        if self.years is None:
            if holidays:
                raise ValueError("pass years=: the years whose full holiday list is given "
                                 "(coverage is not inferred from the holidays)")
            years = frozenset()
        else:
            years = frozenset(as_int(y, "year") for y in self.years)
        outside = sorted(d for d in holidays if d.year not in years)
        if outside:
            raise ValueError(f"holidays outside the covered years {sorted(years)}: {outside[:3]}")
        weekend = frozenset(as_int(w, "weekend day") for w in self.weekend)
        if not weekend <= set(range(7)) or len(weekend) == 7:
            raise ValueError("weekend must be weekday numbers 0-6, leaving at least one working day")
        object.__setattr__(self, "holidays", holidays)
        object.__setattr__(self, "years", years)
        object.__setattr__(self, "weekend", weekend)

    def __repr__(self) -> str:
        span = f"{min(self.years)}-{max(self.years)}" if self.years else "none"
        mode = "strict" if self.strict else "lenient"
        return f"BusinessCalendar({self.name!r}, {len(self.holidays)} holidays, years {span}, {mode})"

    def covers(self, d) -> bool:
        """True if the holiday list covers ``d``'s year."""
        return as_date(d).year in self.years

    def lenient(self) -> BusinessCalendar:
        """The same calendar, treating unlisted weekdays outside ``years`` as business days."""
        return replace(self, strict=False)

    def _check(self, d: date) -> None:
        if self.strict and d.year not in self.years:
            span = ", ".join(str(y) for y in sorted(self.years)) or "none"
            raise CalendarCoverageError(
                f"{d.isoformat()} is in {d.year}, but calendar {self.name!r} has a complete holiday list "
                f"only for {span}. Load that year's list and declare it complete, or use "
                "calendar.lenient() (strict=False) to treat its weekdays as business days."
            )

    def is_holiday(self, d) -> bool:
        """True if ``d`` is a listed holiday (weekends are not listed holidays)."""
        d = as_date(d)
        if d.weekday() not in self.weekend:
            self._check(d)
        return d in self.holidays

    def is_business_day(self, d) -> bool:
        d = as_date(d)
        if d.weekday() in self.weekend:
            return False
        self._check(d)
        return d not in self.holidays

    def _roll(self, d: date, step: int) -> date:
        for _ in range(_MAX_ROLL_DAYS):
            if self.is_business_day(d):
                return d
            d += timedelta(days=step)
        raise ValueError(f"no business day within {_MAX_ROLL_DAYS} days of {d}")

    def adjust(self, d, rule: str = "following") -> date:
        """Move ``d`` to a business day: following, preceding, modified_following,
        modified_preceding (stay in the month), or unadjusted."""
        if rule not in ROLLS:
            raise ValueError(f"rule must be one of {ROLLS}, not {rule!r}")
        d = as_date(d)
        if rule == "unadjusted":
            return d
        forward = rule in ("following", "modified_following")
        out = self._roll(d, 1 if forward else -1)
        if rule.startswith("modified") and out.month != d.month:
            out = self._roll(d, -1 if forward else 1)
        return out

    def add_business_days(self, d, n: int) -> date:
        """The ``n``-th business day after ``d`` (before it if ``n`` < 0).

        ``d`` itself need not be a business day. ``n = 0`` returns ``d`` rolled
        to the next business day. ``n`` must be a whole number.
        """
        d = as_date(d)
        n = as_int(n, "n")
        if n == 0:
            return self.adjust(d, "following")
        step = 1 if n > 0 else -1
        remaining = abs(n)
        while remaining:
            d += timedelta(days=step)
            if self.is_business_day(d):
                remaining -= 1
        return d

    def settlement_date(self, trade_date, lag: int = 1) -> date:
        """Settlement date of a trade done on ``trade_date`` for value T+``lag`` (a whole number >= 0)."""
        return self.add_business_days(trade_date, as_int(lag, "lag", minimum=0))

    def business_days(self, start, end) -> list[date]:
        """Business days from ``start`` to ``end``, both included."""
        d, end = as_date(start), as_date(end)
        out = []
        while d <= end:
            if self.is_business_day(d):
                out.append(d)
            d += timedelta(days=1)
        return out


def _parse_flag(value, row, column: str = "confirmed") -> bool:
    text = str(value).strip().lower()
    if text in _TRUE or text == "":
        return True
    if text in _FALSE:
        return False
    raise ValueError(f"row {row}: {column} must be yes or no, not {value!r}")


def _read_csv(path: str | Path, required: tuple[str, ...], what: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df.columns = [str(c).strip().lower() for c in df.columns]
    missing = [c for c in required if c not in df]
    if missing:
        raise ValueError(f"{path}: {what} CSV missing columns {missing}")
    return df


def load_holidays(path: str | Path) -> pd.DataFrame:
    """Read a holiday CSV into ``date, name, source, confirmed`` (``date`` as ``datetime.date``).

    ``date``, ``name`` and ``source`` are required on every row; ``confirmed``
    is optional and defaults to yes. Each date may appear once.
    """
    df = _read_csv(path, HOLIDAY_COLUMNS[:3], "holiday")
    if "confirmed" not in df:
        df["confirmed"] = ""
    rows = []
    for i, rec in enumerate(df.to_dict("records"), start=2):  # row 1 is the header
        try:
            day = date.fromisoformat(rec["date"].strip())
        except ValueError:
            raise ValueError(f"{path} row {i}: date must be YYYY-MM-DD, not {rec['date']!r}") from None
        for col in ("name", "source"):
            if not rec[col].strip():
                raise ValueError(f"{path} row {i}: {col} is empty")
        try:
            confirmed = _parse_flag(rec["confirmed"], i)
        except ValueError as exc:
            raise ValueError(f"{path} {exc}") from None
        rows.append({"date": day, "name": rec["name"].strip(), "source": rec["source"].strip(),
                     "confirmed": confirmed})
    out = pd.DataFrame(rows, columns=list(HOLIDAY_COLUMNS))
    dupes = sorted(out.loc[out["date"].duplicated(), "date"])
    if dupes:
        raise ValueError(f"{path}: dates listed twice: {dupes}; put both names on one row")
    return out.sort_values("date").reset_index(drop=True)


def coverage_path(holidays_path: str | Path) -> Path:
    """Where a holiday CSV declares its years: ``<name>_years.csv`` in the same folder."""
    path = Path(holidays_path)
    return path.with_name(f"{path.stem}_years.csv")


def load_coverage(path: str | Path) -> pd.DataFrame:
    """Read a coverage CSV into ``year, complete, source``: one row per year, each listed once.

    ``complete`` is ``yes`` once the holiday CSV holds that year's full official
    list, ``no`` while it holds only part of it.
    """
    df = _read_csv(path, COVERAGE_COLUMNS, "coverage")
    rows = []
    for i, rec in enumerate(df.to_dict("records"), start=2):
        text = rec["year"].strip()
        if not (text.isdigit() and len(text) == 4):
            raise ValueError(f"{path} row {i}: year must be a four-digit year, not {rec['year']!r}")
        if not rec["source"].strip():
            raise ValueError(f"{path} row {i}: source is empty")
        if not rec["complete"].strip():
            raise ValueError(f"{path} row {i}: complete must be yes or no, not empty")
        try:
            complete = _parse_flag(rec["complete"], i, "complete")
        except ValueError as exc:
            raise ValueError(f"{path} {exc}") from None
        rows.append({"year": int(text), "complete": complete, "source": rec["source"].strip()})
    out = pd.DataFrame(rows, columns=list(COVERAGE_COLUMNS))
    dupes = sorted(out.loc[out["year"].duplicated(), "year"])
    if dupes:
        raise ValueError(f"{path}: years listed twice: {dupes}")
    return out.sort_values("year").reset_index(drop=True)


def _confirmed_flags(values: pd.Series) -> list[bool]:
    out = []
    for i, value in values.items():
        if isinstance(value, (bool, np.bool_)):
            out.append(bool(value))
        elif value is None or (not isinstance(value, str) and pd.isna(value)):
            out.append(True)  # blank, as in load_holidays: confirmed
        elif isinstance(value, str):
            out.append(_parse_flag(value, i))
        else:
            raise ValueError(f"row {i}: confirmed must be a boolean or yes/no, not {value!r}")
    return out


def calendar_from_holidays(
    holidays: pd.DataFrame,
    *,
    name: str,
    years: Iterable[int],
    include_unconfirmed: bool = True,
    strict: bool = True,
) -> BusinessCalendar:
    """Calendar from a holiday frame with a ``date`` column (``load_holidays``'s layout).

    ``years``: the years whose full list the frame holds. It is required, never
    inferred from the rows; rows outside it are dropped. ``confirmed`` is
    optional: booleans, or ``yes``/``no`` text as in the CSV (blank is yes).
    ``include_unconfirmed=False`` drops the unconfirmed rows.
    """
    if "date" not in holidays:
        raise ValueError(f"holiday frame needs a 'date' column; it has {list(holidays.columns)}")
    days = [as_date(d) for d in holidays["date"]]
    confirmed = _confirmed_flags(holidays["confirmed"]) if "confirmed" in holidays else [True] * len(days)
    covered = frozenset(as_int(y, "year") for y in years)
    keep = frozenset(d for d, ok in zip(days, confirmed) if d.year in covered and (ok or include_unconfirmed))
    return BusinessCalendar(name, keep, covered, strict=strict)


def _declared_years(holidays: pd.DataFrame, path: Path, coverage: Path,
                    years: Iterable[int] | None) -> frozenset[int]:
    """Years a calendar from ``holidays`` covers: those ``coverage`` marks complete, narrowed by ``years``."""
    table = load_coverage(coverage)
    listed = {d.year for d in holidays["date"]}
    undeclared = sorted(listed - set(table["year"]))
    if undeclared:
        raise ValueError(f"{path} has rows for {undeclared}, which {coverage} does not list. Add a row "
                         "per year with complete = yes once its full list is in (no while it is partial).")
    complete = set(table.loc[table["complete"], "year"])
    empty = sorted(complete - listed)
    if empty:
        raise ValueError(f"{coverage} marks {empty} complete, but {path} has no holidays for them")
    if years is None:
        return frozenset(complete)
    wanted = frozenset(as_int(y, "year") for y in years)
    if not wanted <= complete:
        raise ValueError(f"years {sorted(wanted - complete)} are not marked complete in {coverage}")
    return wanted


def load_calendar(
    path: str | Path = DEFAULT_HOLIDAYS_CSV,
    *,
    name: str | None = None,
    years: Iterable[int] | None = None,
    coverage: str | Path | None = None,
    include_unconfirmed: bool = True,
    strict: bool = True,
) -> BusinessCalendar:
    """Calendar from a holiday CSV (default: the shipped Mumbai settlement list).

    The covered years are those ``coverage`` (default: ``coverage_path(path)``)
    marks complete; ``years`` narrows them. With no coverage file, ``years``
    must say which years the CSV lists in full.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"holiday CSV not found: {path}")
    holidays = load_holidays(path)
    explicit = coverage is not None
    coverage = Path(coverage) if explicit else coverage_path(path)
    if coverage.exists():
        covered = _declared_years(holidays, path, coverage, years)
    elif explicit:
        raise FileNotFoundError(f"coverage CSV not found: {coverage}")
    elif years is None:
        raise FileNotFoundError(
            f"{path}: no coverage file {coverage}. Write one (year, complete, source: a row per year "
            "the CSV lists) or pass years= with the years the CSV lists in full."
        )
    else:
        covered = frozenset(as_int(y, "year") for y in years)
    return calendar_from_holidays(
        holidays, name=name or path.stem, years=covered,
        include_unconfirmed=include_unconfirmed, strict=strict,
    )


def mumbai_settlement_calendar(**kwargs) -> BusinessCalendar:
    """The shipped Mumbai settlement calendar (``data/holidays_mumbai.csv`` in the package)."""
    return load_calendar(DEFAULT_HOLIDAYS_CSV, name="mumbai_settlement", **kwargs)


# Calendars that conventions name in ``calendar_name``.
CALENDARS: dict[str, Callable[..., BusinessCalendar]] = {"mumbai_settlement": mumbai_settlement_calendar}


def calendar_for(name: str, **kwargs) -> BusinessCalendar:
    """The calendar called ``name`` in ``CALENDARS`` (keyword arguments go to its loader)."""
    try:
        loader = CALENDARS[name]
    except KeyError:
        raise ValueError(f"no calendar named {name!r}; known: {sorted(CALENDARS)}") from None
    return loader(**kwargs)


def settlement_date(trade_date, conventions: BondConventions | DiscountConventions, *,
                    calendar: BusinessCalendar | None = None, lag: int | None = None) -> date:
    """Settlement date of a trade on ``trade_date`` under an instrument's conventions.

    T+``conventions.settlement_lag`` (or ``lag``) on ``calendar``, by default
    the one ``conventions.calendar_name`` names. Pass ``calendar`` when calling
    in a loop, to load the holiday list once.
    """
    if not isinstance(conventions, (BondConventions, DiscountConventions)):
        raise TypeError("conventions must be BondConventions or DiscountConventions")
    if calendar is None:
        calendar = calendar_for(conventions.calendar_name)
    elif not isinstance(calendar, BusinessCalendar):
        raise TypeError("calendar must be a BusinessCalendar")
    return calendar.settlement_date(trade_date, conventions.settlement_lag if lag is None else lag)
