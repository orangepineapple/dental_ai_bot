"""Pure availability maths (no DB, no clock) so it can be unit tested exhaustively."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True, slots=True)
class WorkingWindow:
    weekday: int  # Monday=0
    start: time
    end: time


def candidate_starts(day: date, windows: list[WorkingWindow], duration: timedelta, interval: timedelta, tz: ZoneInfo) -> list[datetime]:
    """UTC start times on clinic-local `day` where `duration` fits wholly inside a window."""
    starts: list[datetime] = []
    for window in windows:
        if window.weekday != day.weekday():
            continue
        cursor = datetime.combine(day, window.start, tzinfo=tz)
        window_end = datetime.combine(day, window.end, tzinfo=tz)
        # Compare in UTC so a DST change inside a window cannot skew the arithmetic.
        cursor_utc = cursor.astimezone(UTC)
        end_utc = window_end.astimezone(UTC)
        while cursor_utc + duration <= end_utc:
            starts.append(cursor_utc)
            cursor_utc += interval
    return sorted(set(starts))


def overlaps(start: datetime, end: datetime, other_start: datetime, other_end: datetime) -> bool:
    """Half-open interval overlap: back-to-back appointments do not overlap."""
    return start < other_end and other_start < end
