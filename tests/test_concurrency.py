"""Competing bookings through independent connections to a FILE database.

Every service call opens its own sqlite3 connection (inside its own thread
here), so these tests exercise real SQLite locking, not a Python flag.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from slotly.booking_service import (
    BookingLimitReached,
    BookingResult,
    BookingService,
    SlotTaken,
)
from slotly.database import DatabaseBusy, connect
from tests.helpers import ADMIN_ID, ALICE, BOB, CAROL, run_in_threads


def test_two_customers_race_for_one_slot_exactly_one_wins(service, settings, add_slots):
    slots = add_slots("2026-11-03", "09:00", "15:00")   # 12 slots -> 12 independent races
    assert len(slots) == 12

    for index, slot in enumerate(slots):
        # A fresh pair of customers per race, so the per-customer booking
        # limit (3) can never be the reason a booking fails here.
        first, second = 10_000 + 2 * index, 10_001 + 2 * index
        results = run_in_threads([
            lambda s=slot, c=first: service.book_slot(c, "First", s.id),
            lambda s=slot, c=second: service.book_slot(c, "Second", s.id),
        ])
        winners = [r for kind, r in results if kind == "ok"]
        losers = [r for kind, r in results if kind == "err"]

        assert len(winners) == 1, results
        assert isinstance(winners[0], BookingResult) and winners[0].created is True
        assert len(losers) == 1 and isinstance(losers[0], SlotTaken)

    conn = connect(settings.database_path)
    try:
        active = conn.execute("SELECT COUNT(*) FROM bookings WHERE status = 'ACTIVE'").fetchone()[0]
        distinct = conn.execute("SELECT COUNT(DISTINCT slot_id) FROM bookings WHERE status = 'ACTIVE'").fetchone()[0]
    finally:
        conn.close()
    assert active == 12 and distinct == 12      # one active booking per slot, never two


def test_many_customers_race_for_one_slot(service, add_slots):
    slot = add_slots("2026-11-03", "09:00", "09:30")[0]
    customers = list(range(3000, 3008))
    results = run_in_threads([lambda c=c: service.book_slot(c, f"C{c}", slot.id) for c in customers])

    assert sum(1 for kind, _ in results if kind == "ok") == 1
    assert all(isinstance(r, SlotTaken) for kind, r in results if kind == "err")


def test_same_customer_double_click_race_creates_one_booking(service, settings, add_slots):
    slot = add_slots("2026-11-03", "09:00", "09:30")[0]
    results = run_in_threads([lambda: service.book_slot(ALICE, "Alice", slot.id) for _ in range(6)])

    assert all(kind == "ok" for kind, _ in results)
    assert sum(1 for _, r in results if r.created) == 1          # exactly one real insert
    assert len({r.booking.id for _, r in results}) == 1           # all got the same booking
    conn = connect(settings.database_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0] == 1
    finally:
        conn.close()


def test_booking_limit_holds_under_concurrency(service, add_slots):
    """Alice taps four different slots at once; the limit is 3. The count check
    and the insert share one transaction, so they cannot all pass the check."""
    slots = add_slots("2026-11-03", "09:00", "11:00")   # 4 slots
    results = run_in_threads([lambda s=s: service.book_slot(ALICE, "Alice", s.id) for s in slots])

    ok = [r for kind, r in results if kind == "ok"]
    errors = [r for kind, r in results if kind == "err"]
    assert len(ok) == 3
    assert len(errors) == 1 and isinstance(errors[0], BookingLimitReached)
    assert len(service.list_customer_bookings(ALICE)) == 3


def test_booking_and_slot_removal_race_never_removes_a_booked_slot(service, settings, add_slots):
    slots = add_slots("2026-11-03", "09:00", "12:00")
    for slot in slots:
        results = run_in_threads([
            lambda s=slot: service.book_slot(CAROL, "Carol", s.id),
            lambda s=slot: service.remove_slot(ADMIN_ID, s.id),
        ])
        conn = connect(settings.database_path)
        try:
            row = conn.execute(
                "SELECT s.removed_at, "
                "(SELECT COUNT(*) FROM bookings b WHERE b.slot_id = s.id AND b.status = 'ACTIVE') AS active "
                "FROM slots s WHERE s.id = ?", (slot.id,)
            ).fetchone()
        finally:
            conn.close()
        # Never both: a removed slot with an active booking would be a silent deletion.
        assert not (row["removed_at"] is not None and row["active"] == 1), results
        # Exactly one of the two operations won.
        assert sum(1 for kind, _ in results if kind == "ok") == 1, results
        # CAROL's limit is 3 and there are 3 slots, so a lost race never trips the limit.


def test_busy_database_gives_a_clean_error(settings, clock, add_slots, service):
    slot = add_slots("2026-11-03", "09:00", "09:30")[0]
    impatient = BookingService(replace(settings, busy_timeout_ms=200), now=clock)

    blocker = connect(settings.database_path)
    blocker.execute("BEGIN IMMEDIATE")            # another writer holds the lock
    try:
        with pytest.raises(DatabaseBusy):
            impatient.book_slot(ALICE, "Alice", slot.id)
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()

    # Once the lock is released the same request works.
    assert impatient.book_slot(ALICE, "Alice", slot.id).created is True
