"""Time helpers.

Rules used everywhere in Slotly:
* Instants are stored in SQLite as UTC text: "YYYY-MM-DDTHH:MM:SSZ".
  This fixed format sorts correctly as plain text, so SQL can compare it.
* Every datetime object in the code is timezone-aware.
* Local (business) time is used only for admin input and for display.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc
DB_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_db(moment: datetime) -> str:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("Naive datetimes are not allowed; use timezone-aware values.")
    return moment.astimezone(UTC).strftime(DB_FORMAT)


def from_db(text: str) -> datetime:
    return datetime.strptime(text, DB_FORMAT).replace(tzinfo=UTC)


def to_local(moment: datetime, tz: ZoneInfo) -> datetime:
    return moment.astimezone(tz)


def local_day_bounds(local_date: date, tz: ZoneInfo) -> tuple:
    """Return (start, end) of a local calendar day as UTC datetimes."""
    start = datetime.combine(local_date, time.min, tzinfo=tz).astimezone(UTC)
    end = datetime.combine(local_date + timedelta(days=1), time.min, tzinfo=tz).astimezone(UTC)
    return start, end


class LocalTimeError(ValueError):
    """A local wall-clock time cannot be mapped to exactly one instant."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code  # "nonexistent_local_time" or "ambiguous_local_time"


def local_to_utc(local_date: date, local_time: time, tz: ZoneInfo) -> datetime:
    """Convert a local date + wall-clock time to a UTC instant.

    Daylight saving rules (documented in the README):
    * A time that does NOT exist (clocks jump forward) is rejected.
    * A time that happens TWICE (clocks go back) is rejected as ambiguous.
    The admin simply picks another time. Rejecting is simpler and safer than
    silently guessing which of the two instants was meant.
    """
    naive = datetime.combine(local_date, local_time)
    first = naive.replace(tzinfo=tz, fold=0)
    second = naive.replace(tzinfo=tz, fold=1)
    if first.utcoffset() != second.utcoffset():
        # Offsets differ, so this is either a gap or an overlap. Round-trip
        # through UTC: in a gap the wall time changes, in an overlap it does not.
        round_trip = first.astimezone(UTC).astimezone(tz).replace(tzinfo=None)
        if round_trip != naive:
            raise LocalTimeError("nonexistent_local_time")
        raise LocalTimeError("ambiguous_local_time")
    return first.astimezone(UTC)


def parse_hhmm(text: str) -> time:
    """Parse "HH:MM" or "HHMM" into a time. Raises ValueError if invalid."""
    cleaned = text.strip().replace(":", "")
    if len(cleaned) != 4 or not cleaned.isdigit():
        raise ValueError(f"Invalid time: {text!r}")
    return time(int(cleaned[:2]), int(cleaned[2:]))


def format_hhmm_compact(value: time) -> str:
    """09:30 -> "0930" (used inside Telegram callback data)."""
    return f"{value.hour:02d}{value.minute:02d}"
