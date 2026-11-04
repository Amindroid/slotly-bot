"""Booking rules. No Telegram code lives here, so everything is testable.

Every public method that changes data does its work inside ONE short
write transaction (BEGIN IMMEDIATE ... COMMIT) and returns plain objects.
Handlers call Telegram only AFTER the method has returned.

Admin methods take `actor_id` as their first argument and check it against
the configured admin IDs themselves, so a handler bug cannot skip the check.
"""

from __future__ import annotations

import secrets
import sqlite3
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Callable, Iterator, Optional

from slotly.config import SLOT_MINUTES, Settings
from slotly.database import DatabaseBusy, connect, write_transaction
from slotly.timeutil import (
    UTC,
    LocalTimeError,
    from_db,
    local_day_bounds,
    local_to_utc,
    to_db,
    to_local,
    utc_now,
)

__all__ = [
    "BookingService", "Slot", "Booking", "AdminSlot", "SlotPreview",
    "SlotCreationResult", "BookingResult", "CancelResult",
    "BookingError", "NotAuthorized", "InvalidTimeRange", "SlotNotFound",
    "SlotInPast", "SlotTaken", "BookingLimitReached", "BookingNotFound",
    "CancellationTooLate", "SlotHasActiveBooking", "DatabaseBusy",
    "STATUS_ACTIVE", "STATUS_CANCELLED_BY_CUSTOMER", "STATUS_CANCELLED_BY_ADMIN",
]

STATUS_ACTIVE = "ACTIVE"
STATUS_CANCELLED_BY_CUSTOMER = "CANCELLED_BY_CUSTOMER"
STATUS_CANCELLED_BY_ADMIN = "CANCELLED_BY_ADMIN"

SLOT_LENGTH = timedelta(minutes=SLOT_MINUTES)

# Reference codes avoid look-alike characters (0/O, 1/I/L).
_REFERENCE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


# ---------------------------------------------------------------- errors ---

class BookingError(Exception):
    """Base class for expected, user-facing failures."""


class NotAuthorized(BookingError):
    pass


class InvalidTimeRange(BookingError):
    """`code` is one of: not_aligned, end_not_after_start, in_past,
    nonexistent_local_time, ambiguous_local_time."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class SlotNotFound(BookingError):
    pass


class SlotInPast(BookingError):
    pass


class SlotTaken(BookingError):
    pass


class BookingLimitReached(BookingError):
    def __init__(self, limit: int):
        super().__init__(f"limit {limit} reached")
        self.limit = limit


class BookingNotFound(BookingError):
    pass


class CancellationTooLate(BookingError):
    def __init__(self, deadline: datetime):
        super().__init__("cancellation deadline passed")
        self.deadline = deadline


class SlotHasActiveBooking(BookingError):
    pass


# ----------------------------------------------------------- data objects ---

@dataclass(frozen=True)
class Slot:
    id: int
    start: datetime  # UTC, timezone-aware
    end: datetime


@dataclass(frozen=True)
class Booking:
    id: int
    reference: str
    customer_id: int
    customer_name: str
    status: str
    slot_id: int
    start: datetime
    end: datetime
    created_at: datetime
    cancelled_at: Optional[datetime]


@dataclass(frozen=True)
class AdminSlot:
    slot: Slot
    booking: Optional[Booking]  # the ACTIVE booking, if any


@dataclass(frozen=True)
class SlotPreview:
    local_date: date
    starts: tuple          # UTC start instants of every generated slot
    already_existing: int  # how many of them are already open in the database

    @property
    def total(self) -> int:
        return len(self.starts)

    @property
    def new_count(self) -> int:
        return self.total - self.already_existing


@dataclass(frozen=True)
class SlotCreationResult:
    created: int           # new slots, plus previously removed slots re-opened
    already_existing: int


@dataclass(frozen=True)
class BookingResult:
    booking: Booking
    created: bool          # False when this was a repeated confirmation


@dataclass(frozen=True)
class CancelResult:
    booking: Booking
    already_cancelled: bool


# ------------------------------------------------------------- SQL pieces ---

_NOT_BOOKED = (
    "NOT EXISTS (SELECT 1 FROM bookings b "
    "WHERE b.slot_id = s.id AND b.status = 'ACTIVE')"
)

_BOOKING_SELECT = """
SELECT b.id, b.reference, b.customer_id, b.customer_name, b.status,
       b.slot_id, b.created_at, b.cancelled_at, s.start_utc, s.end_utc
FROM bookings b JOIN slots s ON s.id = b.slot_id
"""


def _row_to_slot(row: sqlite3.Row) -> Slot:
    return Slot(id=row["id"], start=from_db(row["start_utc"]), end=from_db(row["end_utc"]))


def _row_to_booking(row: sqlite3.Row) -> Booking:
    return Booking(
        id=row["id"],
        reference=row["reference"],
        customer_id=row["customer_id"],
        customer_name=row["customer_name"],
        status=row["status"],
        slot_id=row["slot_id"],
        start=from_db(row["start_utc"]),
        end=from_db(row["end_utc"]),
        created_at=from_db(row["created_at"]),
        cancelled_at=from_db(row["cancelled_at"]) if row["cancelled_at"] else None,
    )


def _new_reference() -> str:
    return "SL-" + "".join(secrets.choice(_REFERENCE_ALPHABET) for _ in range(6))


def _clean_name(name: str) -> str:
    """Telegram display names are untrusted text: drop control characters,
    collapse spaces and cap the length. (Escaping happens when displaying.)"""
    kept = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C" or ch == " ")
    kept = " ".join(kept.split())[:64]
    return kept or "مشتری"


# ---------------------------------------------------------------- service ---

class BookingService:
    def __init__(self, settings: Settings, now: Callable[[], datetime] = utc_now):
        self.settings = settings
        self._now = now  # injectable clock, so tests are deterministic

    # -- helpers -------------------------------------------------------------

    def now(self) -> datetime:
        moment = self._now()
        if moment.tzinfo is None:
            raise ValueError("The clock must return a timezone-aware datetime.")
        return moment.astimezone(UTC)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = connect(self.settings.database_path, self.settings.busy_timeout_ms)
        try:
            yield conn
        finally:
            conn.close()

    def is_admin(self, user_id: object) -> bool:
        return self.settings.is_admin(user_id)

    def _require_admin(self, actor_id: object) -> None:
        if not self.settings.is_admin(actor_id):
            raise NotAuthorized("admin access required")

    def cancellation_deadline(self, start: datetime) -> datetime:
        return start - timedelta(minutes=self.settings.cancellation_cutoff_minutes)

    # -- customer: browsing --------------------------------------------------

    def list_available_dates(self) -> list:
        """[(local_date, number_of_free_future_slots), ...] in date order."""
        tz = self.settings.timezone
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT s.start_utc FROM slots s "
                f"WHERE s.removed_at IS NULL AND s.start_utc > ? AND {_NOT_BOOKED} "
                f"ORDER BY s.start_utc",
                (to_db(self.now()),),
            ).fetchall()
        counts: dict = {}
        for row in rows:
            day = to_local(from_db(row["start_utc"]), tz).date()
            counts[day] = counts.get(day, 0) + 1
        return sorted(counts.items())

    def list_available_slots(self, local_date: date) -> list:
        tz = self.settings.timezone
        lo = datetime.combine(local_date - timedelta(days=1), time.min, tzinfo=UTC)
        hi = datetime.combine(local_date + timedelta(days=2), time.min, tzinfo=UTC)
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT s.id, s.start_utc, s.end_utc FROM slots s "
                f"WHERE s.removed_at IS NULL AND s.start_utc > ? "
                f"AND s.start_utc >= ? AND s.start_utc < ? AND {_NOT_BOOKED} "
                f"ORDER BY s.start_utc",
                (to_db(self.now()), to_db(lo), to_db(hi)),
            ).fetchall()
        slots = [_row_to_slot(r) for r in rows]
        # The UTC window is wider than one local day; keep only the exact day.
        return [s for s in slots if to_local(s.start, tz).date() == local_date]

    def get_bookable_slot(self, slot_id: int) -> Slot:
        """Used for the confirmation summary. Raises if the slot cannot be booked now."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT id, start_utc, end_utc FROM slots WHERE id = ? AND removed_at IS NULL",
                (slot_id,),
            ).fetchone()
            if row is None:
                raise SlotNotFound()
            slot = _row_to_slot(row)
            if slot.start <= self.now():
                raise SlotInPast()
            taken = conn.execute(
                "SELECT 1 FROM bookings WHERE slot_id = ? AND status = 'ACTIVE'", (slot_id,)
            ).fetchone()
            if taken is not None:
                raise SlotTaken()
            return slot

    # -- customer: booking and cancelling ------------------------------------

    def book_slot(self, customer_id: int, customer_name: str, slot_id: int) -> BookingResult:
        """Book one slot for one customer, atomically.

        Inside ONE BEGIN IMMEDIATE transaction we re-check everything and
        insert. BEGIN IMMEDIATE means only one writer is in here at a time,
        so the checks and the INSERT cannot be interleaved with another
        booking. The unique partial index is the last line of defence.
        """
        name = _clean_name(customer_name)
        with self._connection() as conn, write_transaction(conn):
            now = self.now()  # read AFTER the lock is held (waiting may take time)

            slot_row = conn.execute(
                "SELECT id, start_utc, end_utc FROM slots WHERE id = ? AND removed_at IS NULL",
                (slot_id,),
            ).fetchone()
            if slot_row is None:
                raise SlotNotFound()

            active = conn.execute(
                _BOOKING_SELECT + " WHERE b.slot_id = ? AND b.status = 'ACTIVE'", (slot_id,)
            ).fetchone()
            if active is not None:
                if active["customer_id"] == customer_id:
                    # Repeated confirmation click: return the same booking.
                    return BookingResult(_row_to_booking(active), created=False)
                raise SlotTaken()

            if from_db(slot_row["start_utc"]) <= now:
                raise SlotInPast()

            upcoming = conn.execute(
                "SELECT COUNT(*) FROM bookings b JOIN slots s ON s.id = b.slot_id "
                "WHERE b.customer_id = ? AND b.status = 'ACTIVE' AND s.start_utc > ?",
                (customer_id, to_db(now)),
            ).fetchone()[0]
            if upcoming >= self.settings.max_active_bookings:
                raise BookingLimitReached(self.settings.max_active_bookings)

            booking_id = None
            for _ in range(5):  # a reference collision is very unlikely; retry anyway
                try:
                    cursor = conn.execute(
                        "INSERT INTO bookings "
                        "(reference, slot_id, customer_id, customer_name, status, created_at) "
                        "VALUES (?, ?, ?, ?, 'ACTIVE', ?)",
                        (_new_reference(), slot_id, customer_id, name, to_db(now)),
                    )
                    booking_id = cursor.lastrowid
                    break
                except sqlite3.IntegrityError as exc:
                    message = str(exc)
                    if "bookings.reference" in message:
                        continue
                    if "bookings.slot_id" in message:
                        raise SlotTaken() from exc  # the unique index fired
                    raise
            if booking_id is None:
                raise RuntimeError("Could not generate a unique booking reference.")

            row = conn.execute(_BOOKING_SELECT + " WHERE b.id = ?", (booking_id,)).fetchone()
            return BookingResult(_row_to_booking(row), created=True)

    def list_customer_bookings(self, customer_id: int) -> list:
        """Upcoming ACTIVE bookings of ONE customer (filtered by their Telegram ID)."""
        with self._connection() as conn:
            rows = conn.execute(
                _BOOKING_SELECT
                + " WHERE b.customer_id = ? AND b.status = 'ACTIVE' AND s.start_utc > ?"
                  " ORDER BY s.start_utc",
                (customer_id, to_db(self.now())),
            ).fetchall()
        return [_row_to_booking(r) for r in rows]

    def get_customer_booking(self, customer_id: int, booking_id: int) -> Booking:
        """Fetch a booking only if it belongs to this customer."""
        with self._connection() as conn:
            row = conn.execute(
                _BOOKING_SELECT + " WHERE b.id = ? AND b.customer_id = ?",
                (booking_id, customer_id),
            ).fetchone()
        if row is None:
            raise BookingNotFound()
        return _row_to_booking(row)

    def cancel_booking_by_customer(self, customer_id: int, booking_id: int) -> CancelResult:
        with self._connection() as conn, write_transaction(conn):
            now = self.now()
            row = conn.execute(
                _BOOKING_SELECT + " WHERE b.id = ? AND b.customer_id = ?",
                (booking_id, customer_id),   # ownership is part of the query itself
            ).fetchone()
            if row is None:
                # Same answer for "does not exist" and "belongs to someone else".
                raise BookingNotFound()
            booking = _row_to_booking(row)
            if booking.status != STATUS_ACTIVE:
                return CancelResult(booking, already_cancelled=True)

            deadline = self.cancellation_deadline(booking.start)
            if now > deadline:
                raise CancellationTooLate(deadline)

            cursor = conn.execute(
                "UPDATE bookings SET status = ?, cancelled_at = ? "
                "WHERE id = ? AND customer_id = ? AND status = 'ACTIVE'",
                (STATUS_CANCELLED_BY_CUSTOMER, to_db(now), booking_id, customer_id),
            )
            if cursor.rowcount != 1:  # defensive: cannot happen while we hold the lock
                raise BookingNotFound()
            fresh = conn.execute(_BOOKING_SELECT + " WHERE b.id = ?", (booking_id,)).fetchone()
            return CancelResult(_row_to_booking(fresh), already_cancelled=False)

    # -- admin: slot generation ----------------------------------------------

    def _plan_range(self, local_date: date, start: time, end: time) -> list:
        """Validate a local time range and return the UTC slot start instants."""
        for value in (start, end):
            if value.second or value.microsecond or value.minute % SLOT_MINUTES:
                raise InvalidTimeRange("not_aligned")
        if end <= start:
            raise InvalidTimeRange("end_not_after_start")
        tz = self.settings.timezone
        try:
            start_utc = local_to_utc(local_date, start, tz)
            end_utc = local_to_utc(local_date, end, tz)
        except LocalTimeError as exc:
            raise InvalidTimeRange(exc.code) from exc
        if start_utc <= self.now():
            raise InvalidTimeRange("in_past")
        if (end_utc - start_utc) % SLOT_LENGTH:
            raise InvalidTimeRange("not_aligned")
        # Step in real (UTC) time, so a range that spans a daylight-saving
        # change still produces correct, non-overlapping 30-minute slots.
        starts = []
        cursor = start_utc
        while cursor < end_utc:
            starts.append(cursor)
            cursor += SLOT_LENGTH
        return starts

    def preview_slots(self, actor_id: int, local_date: date, start: time, end: time) -> SlotPreview:
        self._require_admin(actor_id)
        starts = self._plan_range(local_date, start, end)
        existing = 0
        with self._connection() as conn:
            for moment in starts:
                row = conn.execute(
                    "SELECT 1 FROM slots WHERE start_utc = ? AND removed_at IS NULL",
                    (to_db(moment),),
                ).fetchone()
                existing += row is not None
        return SlotPreview(local_date, tuple(starts), existing)

    def create_slots(self, actor_id: int, local_date: date, start: time, end: time) -> SlotCreationResult:
        """Create the slots of a range. Repeating the same request is safe:
        existing slots are skipped, previously removed ones are re-opened."""
        self._require_admin(actor_id)
        with self._connection() as conn, write_transaction(conn):
            # Validation happens inside the transaction so "is it still in the
            # future?" is judged at the moment of saving.
            starts = self._plan_range(local_date, start, end)
            created = existing = 0
            stamp = to_db(self.now())
            for moment in starts:
                row = conn.execute(
                    "SELECT id, removed_at FROM slots WHERE start_utc = ?", (to_db(moment),)
                ).fetchone()
                if row is None:
                    conn.execute(
                        "INSERT INTO slots (start_utc, end_utc, created_at) VALUES (?, ?, ?)",
                        (to_db(moment), to_db(moment + SLOT_LENGTH), stamp),
                    )
                    created += 1
                elif row["removed_at"] is not None:
                    conn.execute("UPDATE slots SET removed_at = NULL WHERE id = ?", (row["id"],))
                    created += 1
                else:
                    existing += 1
            return SlotCreationResult(created=created, already_existing=existing)

    def list_slots_for_date(self, actor_id: int, local_date: date) -> list:
        """Open future slots of a day, each with its active booking (if any)."""
        self._require_admin(actor_id)
        tz = self.settings.timezone
        lo = datetime.combine(local_date - timedelta(days=1), time.min, tzinfo=UTC)
        hi = datetime.combine(local_date + timedelta(days=2), time.min, tzinfo=UTC)
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT id, start_utc, end_utc FROM slots "
                "WHERE removed_at IS NULL AND start_utc > ? AND start_utc >= ? AND start_utc < ? "
                "ORDER BY start_utc",
                (to_db(self.now()), to_db(lo), to_db(hi)),
            ).fetchall()
            result = []
            for row in rows:
                slot = _row_to_slot(row)
                if to_local(slot.start, tz).date() != local_date:
                    continue
                booked = conn.execute(
                    _BOOKING_SELECT + " WHERE b.slot_id = ? AND b.status = 'ACTIVE'", (slot.id,)
                ).fetchone()
                result.append(AdminSlot(slot, _row_to_booking(booked) if booked else None))
        return result

    def get_open_slot(self, actor_id: int, slot_id: int) -> Slot:
        """Look up one open (not removed) slot by ID. Admin only."""
        self._require_admin(actor_id)
        with self._connection() as conn:
            row = conn.execute(
                "SELECT id, start_utc, end_utc FROM slots WHERE id = ? AND removed_at IS NULL",
                (slot_id,),
            ).fetchone()
        if row is None:
            raise SlotNotFound()
        return _row_to_slot(row)

    def remove_slot(self, actor_id: int, slot_id: int) -> None:
        """Remove an UNBOOKED slot. A slot with an active booking is refused:
        the admin must cancel the booking explicitly first."""
        self._require_admin(actor_id)
        with self._connection() as conn, write_transaction(conn):
            row = conn.execute(
                "SELECT id FROM slots WHERE id = ? AND removed_at IS NULL", (slot_id,)
            ).fetchone()
            if row is None:
                raise SlotNotFound()
            active = conn.execute(
                "SELECT 1 FROM bookings WHERE slot_id = ? AND status = 'ACTIVE'", (slot_id,)
            ).fetchone()
            if active is not None:
                raise SlotHasActiveBooking()
            # Soft delete: booking history keeps pointing at a real slot row.
            conn.execute(
                "UPDATE slots SET removed_at = ? WHERE id = ?", (to_db(self.now()), slot_id)
            )

    # -- admin: appointment lists --------------------------------------------

    def _query_active_bookings(self, start_from: datetime, start_before: Optional[datetime], limit: int) -> list:
        sql = _BOOKING_SELECT + " WHERE b.status = 'ACTIVE' AND s.start_utc >= ?"
        params: list = [to_db(start_from)]
        if start_before is not None:
            sql += " AND s.start_utc < ?"
            params.append(to_db(start_before))
        sql += " ORDER BY s.start_utc LIMIT ?"
        params.append(limit)
        with self._connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_booking(r) for r in rows]

    def list_appointments_on(self, actor_id: int, local_date: date, limit: int = 50) -> list:
        """Active bookings that start on one local calendar day."""
        self._require_admin(actor_id)
        day_start, day_end = local_day_bounds(local_date, self.settings.timezone)
        return self._query_active_bookings(day_start, day_end, limit)

    def list_upcoming_appointments(self, actor_id: int, limit: int = 20) -> list:
        """Active bookings that have not started yet."""
        self._require_admin(actor_id)
        return self._query_active_bookings(self.now() + timedelta(seconds=1), None, limit)
