"""Booking, repeated confirmation, limits, isolation and database invariants."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

import pytest

from slotly.booking_service import (
    BookingLimitReached,
    BookingNotFound,
    SlotHasActiveBooking,
    SlotInPast,
    SlotNotFound,
    SlotTaken,
)
from slotly.database import connect
from slotly.timeutil import UTC
from tests.helpers import ADMIN_ID, ALICE, BOB, CAROL

DAY = date(2026, 11, 3)


def fetch_all(settings, sql, params=()):
    conn = connect(settings.database_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def test_successful_booking(service, add_slots):
    slots = add_slots()
    result = service.book_slot(ALICE, "Alice Demo", slots[0].id)

    assert result.created is True
    booking = result.booking
    assert booking.status == "ACTIVE"
    assert booking.customer_id == ALICE
    assert booking.reference.startswith("SL-") and len(booking.reference) == 9
    assert booking.start == slots[0].start
    # The booked slot disappears from the available list; the others remain.
    remaining = service.list_available_slots(DAY)
    assert slots[0].id not in [s.id for s in remaining]
    assert len(remaining) == 5
    # And it shows up in the customer's own list.
    assert [b.id for b in service.list_customer_bookings(ALICE)] == [booking.id]


def test_rejects_slot_in_the_past(service, clock, add_slots):
    slots = add_slots()
    clock.set(datetime(2026, 11, 3, 8, 0, 1, tzinfo=UTC))  # one second after the first slot started
    with pytest.raises(SlotInPast):
        service.book_slot(ALICE, "Alice", slots[0].id)


def test_future_check_is_repeated_at_confirmation(service, clock, add_slots):
    """The slot was bookable when shown, but time passed before the user confirmed."""
    slots = add_slots()
    assert service.get_bookable_slot(slots[0].id).id == slots[0].id   # summary screen: fine
    clock.set(datetime(2026, 11, 3, 8, 5, tzinfo=UTC))                 # user waits...
    with pytest.raises(SlotInPast):
        service.book_slot(ALICE, "Alice", slots[0].id)                 # confirm click: rejected


def test_unknown_slot_is_rejected(service):
    with pytest.raises(SlotNotFound):
        service.book_slot(ALICE, "Alice", 424242)


def test_repeated_confirmation_creates_one_booking(service, settings, add_slots):
    slots = add_slots()
    first = service.book_slot(ALICE, "Alice", slots[0].id)
    second = service.book_slot(ALICE, "Alice", slots[0].id)   # double click
    third = service.book_slot(ALICE, "Alice", slots[0].id)

    assert first.created is True
    assert second.created is False and third.created is False
    assert second.booking.id == first.booking.id
    assert second.booking.reference == first.booking.reference
    rows = fetch_all(settings, "SELECT * FROM bookings")
    assert len(rows) == 1


def test_second_customer_loses_the_slot(service, add_slots):
    slots = add_slots()
    service.book_slot(ALICE, "Alice", slots[0].id)
    with pytest.raises(SlotTaken):
        service.book_slot(BOB, "Bob", slots[0].id)
    assert service.list_customer_bookings(BOB) == []


def test_booking_limit_is_enforced(service, add_slots):
    slots = add_slots()  # 6 slots
    for slot in slots[:3]:
        service.book_slot(ALICE, "Alice", slot.id)
    with pytest.raises(BookingLimitReached) as info:
        service.book_slot(ALICE, "Alice", slots[3].id)
    assert info.value.limit == 3
    # Another customer is not affected by Alice's limit.
    service.book_slot(BOB, "Bob", slots[3].id)


def test_limit_frees_up_after_cancellation(service, add_slots):
    slots = add_slots()
    bookings = [service.book_slot(ALICE, "Alice", s.id).booking for s in slots[:3]]
    service.cancel_booking_by_customer(ALICE, bookings[0].id)
    service.book_slot(ALICE, "Alice", slots[3].id)   # allowed again


def test_past_bookings_do_not_count_toward_the_limit(service, clock, add_slots):
    slots = add_slots("2026-11-03", "09:00", "10:30")        # 3 slots tomorrow
    for slot in slots:
        service.book_slot(ALICE, "Alice", slot.id)
    later = add_slots("2026-11-10", "09:00", "10:00")
    clock.set(datetime(2026, 11, 4, 8, 0, tzinfo=UTC))        # tomorrow's appointments are over
    service.book_slot(ALICE, "Alice", later[0].id)            # does not raise


def test_customers_only_see_their_own_bookings(service, add_slots):
    slots = add_slots()
    service.book_slot(ALICE, "Alice", slots[0].id)
    service.book_slot(BOB, "Bob", slots[1].id)

    assert [b.customer_id for b in service.list_customer_bookings(ALICE)] == [ALICE]
    assert [b.customer_id for b in service.list_customer_bookings(BOB)] == [BOB]
    assert service.list_customer_bookings(CAROL) == []


def test_customer_cannot_read_or_cancel_another_customers_booking(service, add_slots):
    slots = add_slots()
    alices = service.book_slot(ALICE, "Alice", slots[0].id).booking

    with pytest.raises(BookingNotFound):
        service.get_customer_booking(BOB, alices.id)
    with pytest.raises(BookingNotFound):
        service.cancel_booking_by_customer(BOB, alices.id)   # Bob guesses Alice's booking id

    still = service.get_customer_booking(ALICE, alices.id)
    assert still.status == "ACTIVE"


def test_rebooking_a_cancelled_slot(service, settings, add_slots):
    slots = add_slots()
    first = service.book_slot(ALICE, "Alice", slots[0].id).booking
    service.cancel_booking_by_customer(ALICE, first.id)

    assert slots[0].id in [s.id for s in service.list_available_slots(DAY)]
    second = service.book_slot(BOB, "Bob", slots[0].id).booking

    assert second.id != first.id and second.status == "ACTIVE"
    statuses = sorted(r["status"] for r in fetch_all(settings, "SELECT status FROM bookings"))
    assert statuses == ["ACTIVE", "CANCELLED_BY_CUSTOMER"]    # history is preserved


def test_customer_name_is_cleaned(service, add_slots):
    slots = add_slots()
    booking = service.book_slot(ALICE, "  Al\x00ice\n  <b>Demo</b>  " + "x" * 200, slots[0].id).booking
    assert "\x00" not in booking.customer_name and "\n" not in booking.customer_name
    assert len(booking.customer_name) <= 64
    # Stored as-is (minus control characters); escaping is done when displaying.
    assert "<b>" in booking.customer_name


# ------------------------------------------------- removing slots / invariants

def test_booked_slot_cannot_be_removed(service, add_slots):
    slots = add_slots()
    service.book_slot(ALICE, "Alice", slots[0].id)

    with pytest.raises(SlotHasActiveBooking):
        service.remove_slot(ADMIN_ID, slots[0].id)

    assert service.list_customer_bookings(ALICE)[0].status == "ACTIVE"


def test_unbooked_slot_can_be_removed_and_is_then_unbookable(service, add_slots):
    slots = add_slots()
    service.remove_slot(ADMIN_ID, slots[0].id)

    assert slots[0].id not in [s.id for s in service.list_available_slots(DAY)]
    with pytest.raises(SlotNotFound):
        service.book_slot(ALICE, "Alice", slots[0].id)
    with pytest.raises(SlotNotFound):
        service.remove_slot(ADMIN_ID, slots[0].id)   # removing twice is reported, not crashed


def test_database_refuses_deleting_a_slot_that_has_bookings(service, settings, add_slots):
    """Foreign keys are enforced: even raw SQL cannot delete a slot with booking history."""
    slots = add_slots()
    service.book_slot(ALICE, "Alice", slots[0].id)
    conn = connect(settings.database_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM slots WHERE id = ?", (slots[0].id,))
    finally:
        conn.close()


def test_database_itself_refuses_two_active_bookings_for_one_slot(service, settings, add_slots):
    """The invariant lives in the database, not only in Python code."""
    slots = add_slots()
    conn = connect(settings.database_path)
    insert = (
        "INSERT INTO bookings (reference, slot_id, customer_id, customer_name, status, created_at, cancelled_at) "
        "VALUES (?, ?, ?, 'x', ?, '2026-11-02T08:00:00Z', ?)"
    )
    try:
        conn.execute(insert, ("R1", slots[0].id, ALICE, "ACTIVE", None))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, ("R2", slots[0].id, BOB, "ACTIVE", None))
        # A CANCELLED row for the same slot is fine (that is the history).
        conn.execute(insert, ("R3", slots[0].id, BOB, "CANCELLED_BY_CUSTOMER", "2026-11-02T09:00:00Z"))
    finally:
        conn.close()


def test_foreign_keys_are_enabled_on_every_connection(settings):
    conn = connect(settings.database_path)
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO bookings (reference, slot_id, customer_id, customer_name, status, created_at) "
                "VALUES ('R9', 999, 1, 'x', 'ACTIVE', 't')"
            )
    finally:
        conn.close()
