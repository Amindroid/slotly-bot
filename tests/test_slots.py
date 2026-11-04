"""Slot generation: validation, duplicates, daylight saving time."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

import pytest

from slotly.booking_service import BookingService, InvalidTimeRange
from slotly.database import connect
from slotly.timeutil import UTC, parse_hhmm
from tests.helpers import ADMIN_ID, ALICE, FakeClock

DAY = date(2026, 11, 3)  # tomorrow relative to the fake clock (2026-11-02 09:00 Berlin)


def count_slots(settings) -> int:
    conn = connect(settings.database_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM slots").fetchone()[0]
    finally:
        conn.close()


def test_generates_30_minute_slots_stored_in_utc(service, add_slots):
    slots = add_slots("2026-11-03", "09:00", "12:00")

    assert len(slots) == 6
    # 09:00 Berlin (CET, UTC+1) is 08:00 UTC.
    assert slots[0].start == datetime(2026, 11, 3, 8, 0, tzinfo=UTC)
    assert slots[0].end == datetime(2026, 11, 3, 8, 30, tzinfo=UTC)
    assert slots[-1].end == datetime(2026, 11, 3, 11, 0, tzinfo=UTC)
    assert all(s.end - s.start == timedelta(minutes=30) for s in slots)


def test_preview_does_not_save_anything(service, settings):
    preview = service.preview_slots(ADMIN_ID, DAY, parse_hhmm("09:00"), parse_hhmm("11:00"))

    assert preview.total == 4
    assert preview.already_existing == 0
    assert preview.new_count == 4
    assert count_slots(settings) == 0


def test_repeating_the_same_request_is_safe(service, settings):
    first = service.create_slots(ADMIN_ID, DAY, parse_hhmm("09:00"), parse_hhmm("11:00"))
    second = service.create_slots(ADMIN_ID, DAY, parse_hhmm("09:00"), parse_hhmm("11:00"))

    assert (first.created, first.already_existing) == (4, 0)
    assert (second.created, second.already_existing) == (0, 4)
    assert count_slots(settings) == 4


def test_overlapping_ranges_do_not_duplicate_slots(service, settings):
    service.create_slots(ADMIN_ID, DAY, parse_hhmm("09:00"), parse_hhmm("11:00"))
    result = service.create_slots(ADMIN_ID, DAY, parse_hhmm("10:00"), parse_hhmm("12:00"))

    assert result.created == 2 and result.already_existing == 2
    assert count_slots(settings) == 6


def test_preview_reports_existing_slots(service):
    service.create_slots(ADMIN_ID, DAY, parse_hhmm("09:00"), parse_hhmm("10:00"))
    preview = service.preview_slots(ADMIN_ID, DAY, parse_hhmm("09:00"), parse_hhmm("11:00"))

    assert preview.total == 4 and preview.already_existing == 2 and preview.new_count == 2


@pytest.mark.parametrize(
    "day, start, end, code",
    [
        (date(2026, 11, 1), "09:00", "10:00", "in_past"),            # yesterday
        (date(2026, 11, 2), "08:00", "10:00", "in_past"),            # starts before 09:00 "now"
        (date(2026, 11, 2), "09:00", "10:00", "in_past"),            # starts exactly now
        (date(2026, 11, 3), "10:00", "10:00", "end_not_after_start"),
        (date(2026, 11, 3), "11:00", "10:00", "end_not_after_start"),
        (date(2026, 11, 3), "09:15", "10:00", "not_aligned"),
        (date(2026, 11, 3), "09:00", "10:10", "not_aligned"),
    ],
)
def test_rejects_invalid_ranges(service, settings, day, start, end, code):
    with pytest.raises(InvalidTimeRange) as info:
        service.create_slots(ADMIN_ID, day, parse_hhmm(start), parse_hhmm(end))
    assert info.value.code == code
    assert count_slots(settings) == 0


def test_range_starting_later_today_is_accepted(service):
    result = service.create_slots(ADMIN_ID, date(2026, 11, 2), parse_hhmm("09:30"), parse_hhmm("10:30"))
    assert result.created == 2


def test_removed_slot_is_reopened_by_generation(service, add_slots):
    slots = add_slots("2026-11-03", "09:00", "10:00")
    service.remove_slot(ADMIN_ID, slots[0].id)
    assert len(service.list_available_slots(DAY)) == 1

    result = service.create_slots(ADMIN_ID, DAY, parse_hhmm("09:00"), parse_hhmm("10:00"))

    assert result.created == 1 and result.already_existing == 1
    assert len(service.list_available_slots(DAY)) == 2


def test_available_dates_group_slots_by_local_date(service, add_slots):
    add_slots("2026-11-03", "09:00", "10:00")
    add_slots("2026-11-05", "09:00", "11:00")

    assert service.list_available_dates() == [(date(2026, 11, 3), 2), (date(2026, 11, 5), 4)]


def test_late_evening_slot_belongs_to_its_local_date(service, add_slots):
    # 23:00 Berlin is 22:00 UTC: same date. 00:00 Berlin is 23:00 UTC of the
    # day BEFORE - the local date must still be used, not the UTC date.
    add_slots("2026-11-03", "23:00", "23:30")
    assert [d for d, _ in service.list_available_dates()] == [date(2026, 11, 3)]
    assert len(service.list_available_slots(date(2026, 11, 3))) == 1
    assert service.list_available_slots(date(2026, 11, 4)) == []


# ------------------------------------------------------------ daylight saving

@pytest.fixture
def dst_service(settings):
    """Clock set to January 2027 so both 2027 Berlin transitions are in the future."""
    clock = FakeClock(datetime(2027, 1, 15, 12, 0, tzinfo=UTC))
    return BookingService(settings, now=clock)


def test_nonexistent_local_time_is_rejected(dst_service):
    # 2027-03-28: clocks jump from 02:00 to 03:00 in Berlin, so 02:30 never exists.
    with pytest.raises(InvalidTimeRange) as info:
        dst_service.create_slots(ADMIN_ID, date(2027, 3, 28), parse_hhmm("02:30"), parse_hhmm("04:00"))
    assert info.value.code == "nonexistent_local_time"


def test_ambiguous_local_time_is_rejected(dst_service):
    # 2027-10-31: clocks go from 03:00 back to 02:00, so 02:30 happens twice.
    with pytest.raises(InvalidTimeRange) as info:
        dst_service.create_slots(ADMIN_ID, date(2027, 10, 31), parse_hhmm("02:30"), parse_hhmm("04:00"))
    assert info.value.code == "ambiguous_local_time"


def test_range_spanning_spring_forward_has_one_hour_fewer(dst_service):
    # 00:00-06:00 local on the short day is only 5 real hours = 10 slots.
    result = dst_service.create_slots(ADMIN_ID, date(2027, 3, 28), parse_hhmm("00:00"), parse_hhmm("06:00"))
    assert result.created == 10


def test_range_spanning_fall_back_has_one_hour_more(dst_service):
    # 00:00-06:00 local on the long day is 7 real hours = 14 slots.
    result = dst_service.create_slots(ADMIN_ID, date(2027, 10, 31), parse_hhmm("00:00"), parse_hhmm("06:00"))
    assert result.created == 14
    # Slots must not overlap or repeat: the UNIQUE start_utc column would have
    # raised if two had the same instant.


def test_unique_start_is_enforced_by_the_database(settings, service, add_slots):
    add_slots("2026-11-03", "09:00", "09:30")
    conn = connect(settings.database_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO slots (start_utc, end_utc, created_at) "
                "VALUES ('2026-11-03T08:00:00Z', '2026-11-03T08:30:00Z', 'x')"
            )
    finally:
        conn.close()
