"""New York, London and India time from the published rules, without time-zone data.

The macro sources date US publications (Treasury yields, the New York Fed's
reference rates, EIA's Brent price, the Federal Reserve's H.10 rupee rate) in
New York time, FTSE Russell's index decisions in London time, and every
``public_from`` in India time without a zone (``2026-11-12T16:00:00``), as
``rbi_store`` writes it. ``zoneinfo`` needs the IANA database, which some
machines lack (Windows without the ``tzdata`` package), so the offsets here
come from the rules themselves:

* **New York.** Eastern Standard Time is UTC-5, Eastern Daylight Time UTC-4.
  From 2007 (Energy Policy Act of 2005) daylight time runs from 02:00 local
  standard time on the second Sunday of March to 02:00 local daylight time on
  the first Sunday of November; from 1987 to 2006 (Uniform Time Act as amended
  in 1986) from the first Sunday of April to the last Sunday of October, at
  02:00 both times. Before 1987 this module assumes standard time all year: a
  local time then converts to the later of the two instants it could have
  been, so a publication time built from it is never too early. The macro
  sources reach before 1987 only with BIS's US policy rate (from 1954).
* **London.** GMT (UTC+0) and British Summer Time (UTC+1). From 1996 summer
  time runs from 01:00 UTC on the last Sunday of March to 01:00 UTC on the last
  Sunday of October (the EU rule the UK follows). Before 1996 GMT is assumed
  all year, the later instant, as for New York. Index events start in 2021.
* **India.** India Standard Time, UTC+05:30, without daylight saving since
  1945.

A local time that does not exist (02:00 to 02:59:59 on the US spring-forward
Sunday, 01:00 to 01:59:59 on the UK one) is refused with
``NonexistentTimeError``. One that occurs twice (01:00 to 01:59:59 on the US
fall-back Sunday, 01:00 to 01:59:59 on the UK one) takes the standard-time
reading, the later instant. The rules were checked in scratch against
``zoneinfo`` at every UTC hour and every local half hour of 1987 to 2030
(New York) and 1996 to 2030 (London), with no difference; the tests pin the
change days.

Nothing here reads the clock.
"""

from __future__ import annotations

import datetime as dt

UTC = dt.timezone.utc
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
NEW_YORK = "America/New_York"
LONDON = "Europe/London"
KOLKATA = "Asia/Kolkata"
ZONES = (NEW_YORK, LONDON, KOLKATA, "UTC")

_HOUR = dt.timedelta(hours=1)
_STANDARD = {NEW_YORK: dt.timedelta(hours=-5), LONDON: dt.timedelta(0), KOLKATA: dt.timedelta(hours=5, minutes=30),
             "UTC": dt.timedelta(0)}
FIRST_US_RULE_YEAR = 1987  # before it, standard time is assumed (the later instant)
FIRST_UK_RULE_YEAR = 1996


class NonexistentTimeError(ValueError):
    """A local time skipped when the clocks go forward (02:30 on the US spring-forward Sunday)."""


def _check_zone(zone: str) -> str:
    if zone not in _STANDARD:
        raise ValueError(f"time zone {zone!r} is not one of {', '.join(ZONES)} (no time-zone data is used here)")
    return zone


def nth_sunday(year: int, month: int, n: int) -> dt.date:
    """The ``n``-th Sunday of a month (``n`` from 1); ``n = -1`` for the last one."""
    if n == -1:
        last = (dt.date(year + month // 12, month % 12 + 1, 1) - dt.timedelta(days=1))
        return last - dt.timedelta(days=(last.weekday() + 1) % 7)
    if not 1 <= n <= 5:
        raise ValueError(f"n must be 1 to 5, or -1 for the last Sunday, not {n!r}")
    first = dt.date(year, month, 1)
    day = first + dt.timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))
    if day.month != month:
        raise ValueError(f"{year}-{month:02d} has no Sunday number {n}")
    return day


def summer_time(zone: str, year: int) -> tuple[dt.datetime, dt.datetime] | None:
    """The UTC instants at which daylight (summer) time starts and ends in ``year``; ``None`` if not modelled.

    ``None`` for India and UTC (no daylight saving), and for years before the
    rule this module applies (New York before 1987, London before 1996), where
    standard time is assumed.
    """
    _check_zone(zone)
    if zone == NEW_YORK and year >= 2007:  # 02:00 EST = 07:00 UTC; 02:00 EDT = 06:00 UTC
        start, end = nth_sunday(year, 3, 2), nth_sunday(year, 11, 1)
        return (dt.datetime.combine(start, dt.time(7), UTC), dt.datetime.combine(end, dt.time(6), UTC))
    if zone == NEW_YORK and year >= FIRST_US_RULE_YEAR:
        start, end = nth_sunday(year, 4, 1), nth_sunday(year, 10, -1)
        return (dt.datetime.combine(start, dt.time(7), UTC), dt.datetime.combine(end, dt.time(6), UTC))
    if zone == LONDON and year >= FIRST_UK_RULE_YEAR:  # 01:00 UTC both times
        start, end = nth_sunday(year, 3, -1), nth_sunday(year, 10, -1)
        return (dt.datetime.combine(start, dt.time(1), UTC), dt.datetime.combine(end, dt.time(1), UTC))
    return None


def _in_summer(zone: str, instant: dt.datetime) -> bool:
    bounds = summer_time(zone, instant.year)
    return bounds is not None and bounds[0] <= instant < bounds[1]


def _aware(when: dt.datetime, what: str = "when") -> dt.datetime:
    if not isinstance(when, dt.datetime) or when.tzinfo is None or when.utcoffset() is None:
        raise ValueError(f"{what} must be a timezone-aware datetime, not {when!r}")
    return when


def utc_offset(zone: str, when: dt.datetime) -> dt.timedelta:
    """The offset from UTC in ``zone`` at the instant ``when`` (timezone-aware)."""
    instant = _aware(when).astimezone(UTC)
    offset = _STANDARD[_check_zone(zone)]
    return offset + _HOUR if _in_summer(zone, instant) else offset


def localize(zone: str, local: dt.datetime) -> dt.datetime:
    """The UTC instant of a local (naive) time in ``zone``.

    A local time skipped by the spring change raises ``NonexistentTimeError``;
    one repeated by the autumn change takes the standard-time reading (the
    later instant).
    """
    if not isinstance(local, dt.datetime) or local.tzinfo is not None:
        raise ValueError(f"local must be a naive datetime (a local time without a zone), not {local!r}")
    standard = _STANDARD[_check_zone(zone)]
    as_standard = (local - standard).replace(tzinfo=UTC)
    as_summer = (local - standard - _HOUR).replace(tzinfo=UTC)
    standard_ok = not _in_summer(zone, as_standard)
    summer_ok = _in_summer(zone, as_summer)
    if standard_ok:  # also when both are possible (the repeated hour): the later instant
        return as_standard
    if summer_ok:
        return as_summer
    raise NonexistentTimeError(f"{local.isoformat()} does not exist in {zone}: the clocks went forward over it")


def to_utc(zone: str, day: dt.date, time: dt.time) -> dt.datetime:
    """The UTC instant of ``time`` on ``day`` in ``zone`` (see ``localize``)."""
    if not isinstance(day, dt.date) or isinstance(day, dt.datetime):
        raise ValueError(f"day must be a date, not {day!r}")
    if not isinstance(time, dt.time) or time.tzinfo is not None:
        raise ValueError(f"time must be a naive time of day, not {time!r}")
    return localize(zone, dt.datetime.combine(day, time))


def to_local(zone: str, when: dt.datetime) -> dt.datetime:
    """The local time (naive) in ``zone`` of the instant ``when`` (timezone-aware)."""
    instant = _aware(when).astimezone(UTC)
    return (instant + utc_offset(zone, instant)).replace(tzinfo=None)


def to_ist(when: dt.datetime) -> dt.datetime:
    """India time (naive) of the instant ``when`` (timezone-aware)."""
    return _aware(when).astimezone(IST).replace(tzinfo=None)


def ist_text(when: dt.datetime) -> str:
    """India time of ``when`` as the CSVs write it: ``2026-11-12T16:00:00``."""
    return to_ist(when).strftime("%Y-%m-%dT%H:%M:%S")


def utc_text(when: dt.datetime) -> str:
    """``when`` in UTC as ``state.json`` writes it: ``2026-11-18T03:09:51Z``."""
    return _aware(when).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def zone_to_ist(zone: str, day: dt.date, time: dt.time) -> dt.datetime:
    """India time (naive) of ``time`` on ``day`` in ``zone``."""
    return to_ist(to_utc(zone, day, time))


def ny_offset(when: dt.datetime) -> dt.timedelta:
    """New York's offset from UTC: at an instant (aware), or for a local New York time (naive)."""
    if when.tzinfo is None:
        instant = localize(NEW_YORK, when)
        return utc_offset(NEW_YORK, instant)
    return utc_offset(NEW_YORK, when)


def ny_to_utc(day: dt.date, time: dt.time) -> dt.datetime:
    """The UTC instant of a New York local time."""
    return to_utc(NEW_YORK, day, time)


def ny_to_ist(day: dt.date, time: dt.time) -> dt.datetime:
    """India time (naive) of a New York local time."""
    return zone_to_ist(NEW_YORK, day, time)


def utc_to_ist(when: dt.datetime) -> dt.datetime:
    """India time (naive) of a UTC (or any aware) instant."""
    return to_ist(when)
