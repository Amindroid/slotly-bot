"""Customer cancellation and the cancellation cutoff."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from slotly.booking_service import BookingNotFound, CancellationTooLate
from slotly.timeutil import UTC
from tests.helpers import ALICE

# First slot of add_slots(): 2026-11-03 09:00 Berlin = 08:00 UTC.
# Cutoff is 120 minutes, so the last moment to cancel is 06:00:00 UTC.
SLOT_START = datetime(2026, 11, 3, 8, 0, tzinfo=UTC)
DEADLINE = SLOT_START - timedelta(hours=2)


@pytest.fixture
def booking(service, add_slots):
    slots = add_slots()
    return service.book_slot(ALICE, "Alice", slots[0].id).booking


def test_cancel_well_before_the_cutoff(service, booking):
    result = service.cancel_booking_by_customer(ALICE, booking.id)

    assert result.already_cancelled is False
    assert result.booking.status == "CANCELLED_BY_CUSTOMER"
    assert result.booking.cancelled_at is not None
    assert service.list_customer_bookings(ALICE) == []


def test_cancel_exactly_at_the_cutoff_is_allowed(service, clock, booking):
    clock.set(DEADLINE)
    assert service.cancel_booking_by_customer(ALICE, booking.id).booking.status == "CANCELLED_BY_CUSTOMER"


def test_cancel_one_second_after_the_cutoff_is_rejected(service, clock, booking):
    clock.set(DEADLINE + timedelta(seconds=1))
    with pytest.raises(CancellationTooLate) as info:
        service.cancel_booking_by_customer(ALICE, booking.id)

    assert info.value.deadline == DEADLINE
    assert service.get_customer_booking(ALICE, booking.id).status == "ACTIVE"   # unchanged


def test_cancel_after_the_appointment_started_is_rejected(service, clock, booking):
    clock.set(SLOT_START + timedelta(minutes=5))
    with pytest.raises(CancellationTooLate):
        service.cancel_booking_by_customer(ALICE, booking.id)


def test_repeated_cancellation_is_graceful(service, booking):
    first = service.cancel_booking_by_customer(ALICE, booking.id)
    second = service.cancel_booking_by_customer(ALICE, booking.id)

    assert first.already_cancelled is False
    assert second.already_cancelled is True
    assert second.booking.status == "CANCELLED_BY_CUSTOMER"


def test_cancelling_an_unknown_booking(service):
    with pytest.raises(BookingNotFound):
        service.cancel_booking_by_customer(ALICE, 99999)


def test_cutoff_is_configurable(settings, clock, add_slots):
    from dataclasses import replace
    from slotly.booking_service import BookingService

    strict = BookingService(replace(settings, cancellation_cutoff_minutes=24 * 60), now=clock)
    slots = add_slots()
    booked = strict.book_slot(ALICE, "Alice", slots[0].id).booking
    clock.advance(hours=1)
    # "Now" is 23 hours before the slot, but the cutoff is 24 hours.
    with pytest.raises(CancellationTooLate):
        strict.cancel_booking_by_customer(ALICE, booked.id)
