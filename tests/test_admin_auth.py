"""Admin authorization is enforced inside the service, not only in handlers."""

from __future__ import annotations

from datetime import date

import pytest

from slotly.booking_service import NotAuthorized
from slotly.timeutil import parse_hhmm
from tests.helpers import ADMIN_ID, ALICE, BOB

DAY = date(2026, 11, 3)
START, END = parse_hhmm("09:00"), parse_hhmm("10:00")


def test_admin_id_is_recognised(service):
    assert service.is_admin(ADMIN_ID) is True
    assert service.is_admin(ALICE) is False


def test_customers_cannot_use_any_admin_operation(service, add_slots):
    slots = add_slots()
    service.book_slot(ALICE, "Alice", slots[0].id)

    attempts = [
        lambda: service.preview_slots(ALICE, DAY, START, END),
        lambda: service.create_slots(ALICE, DAY, START, END),
        lambda: service.remove_slot(ALICE, slots[1].id),
        lambda: service.list_slots_for_date(ALICE, DAY),
        lambda: service.list_appointments_on(ALICE, DAY),
        lambda: service.list_upcoming_appointments(ALICE),
    ]
    for attempt in attempts:
        with pytest.raises(NotAuthorized):
            attempt()

    # Nothing happened as a side effect.
    assert len(service.list_available_slots(DAY)) == 5


@pytest.mark.parametrize("fake_id", [None, "1001", "admin", "@admin", 0, -1, True, 1001.0])
def test_non_integer_or_unknown_ids_are_never_admins(service, fake_id):
    # Usernames and look-alike values never count; only exact numeric IDs do.
    assert service.is_admin(fake_id) is False
    with pytest.raises(NotAuthorized):
        service.list_upcoming_appointments(fake_id)


def test_admin_can_list_appointments(service, add_slots):
    slots = add_slots()
    service.book_slot(ALICE, "Alice", slots[0].id)
    service.book_slot(BOB, "Bob", slots[1].id)

    on_day = service.list_appointments_on(ADMIN_ID, DAY)
    upcoming = service.list_upcoming_appointments(ADMIN_ID)

    assert [b.customer_id for b in on_day] == [ALICE, BOB]
    assert [b.customer_id for b in upcoming] == [ALICE, BOB]
    assert service.list_appointments_on(ADMIN_ID, date(2026, 11, 4)) == []


def test_cancelled_bookings_are_not_listed(service, add_slots):
    slots = add_slots()
    booking = service.book_slot(ALICE, "Alice", slots[0].id).booking
    service.cancel_booking_by_customer(ALICE, booking.id)

    assert service.list_appointments_on(ADMIN_ID, DAY) == []
    assert service.list_upcoming_appointments(ADMIN_ID) == []


def test_admin_slot_listing_shows_booking_state(service, add_slots):
    slots = add_slots()
    service.book_slot(ALICE, "Alice", slots[0].id)

    listing = service.list_slots_for_date(ADMIN_ID, DAY)

    assert len(listing) == 6
    assert listing[0].booking is not None and listing[0].booking.customer_id == ALICE
    assert all(item.booking is None for item in listing[1:])
