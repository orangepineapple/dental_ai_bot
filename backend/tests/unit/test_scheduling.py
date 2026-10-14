from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from src.appointments.scheduling import WorkingWindow, candidate_starts, overlaps

NY = ZoneInfo("America/New_York")
MONDAY = date(2026, 11, 2)
MORNING = WorkingWindow(weekday=0, start=time(9), end=time(12))
AFTERNOON = WorkingWindow(weekday=0, start=time(13), end=time(17))


def test_slots_must_fit_wholly_inside_window() -> None:
    starts = candidate_starts(MONDAY, [MORNING], timedelta(minutes=60), timedelta(minutes=60), NY)

    # 09:00, 10:00, 11:00 local (EST = UTC-5); a 12:00 start would run past 12:00.
    assert [s.astimezone(NY).strftime("%H:%M") for s in starts] == ["09:00", "10:00", "11:00"]


def test_interval_controls_granularity() -> None:
    starts = candidate_starts(MONDAY, [MORNING], timedelta(minutes=30), timedelta(minutes=15), NY)

    assert len(starts) == 11  # 09:00 .. 11:30 every 15 minutes


def test_lunch_gap_between_windows_is_respected() -> None:
    starts = candidate_starts(
        MONDAY, [MORNING, AFTERNOON], timedelta(minutes=60), timedelta(minutes=60), NY
    )
    local = [s.astimezone(NY).strftime("%H:%M") for s in starts]

    assert "12:00" not in local
    assert local[:4] == ["09:00", "10:00", "11:00", "13:00"]


def test_other_weekdays_produce_nothing() -> None:
    tuesday = date(2026, 11, 3)

    assert candidate_starts(tuesday, [MORNING], timedelta(hours=1), timedelta(hours=1), NY) == []


def test_results_are_utc_and_respect_dst() -> None:
    before_change = date(2026, 3, 2)  # Monday, EST
    after_change = date(2026, 3, 9)  # Monday, EDT
    window = [WorkingWindow(0, time(9), time(10))]
    args = (window, timedelta(hours=1), timedelta(hours=1), NY)

    (est,) = candidate_starts(before_change, *args)
    (edt,) = candidate_starts(after_change, *args)

    assert est == datetime(2026, 3, 2, 14, 0, tzinfo=UTC)
    assert edt == datetime(2026, 3, 9, 13, 0, tzinfo=UTC)
    assert est.tzinfo is UTC


def test_overlap_is_half_open() -> None:
    nine = datetime(2026, 11, 2, 9, tzinfo=UTC)
    ten = nine + timedelta(hours=1)
    eleven = ten + timedelta(hours=1)

    assert overlaps(nine, ten, ten, eleven) is False  # back to back is fine
    assert overlaps(nine, eleven, ten, eleven) is True
    assert overlaps(nine, ten, nine + timedelta(minutes=30), eleven) is True
