"""SQLite access: schema, connections and the write-transaction helper.

Design choices (simple on purpose):
* One short-lived connection per service call. Connections are never shared
  between threads, so SQLite's thread-safety check stays enabled.
* isolation_level=None puts Python's sqlite3 module in plain autocommit mode,
  so the transaction statements (BEGIN IMMEDIATE / COMMIT) are written
  explicitly and mean exactly what they say.
* Every write goes through `write_transaction`, which takes SQLite's write
  lock up front with BEGIN IMMEDIATE. Only one writer runs at a time, and
  other writers wait up to `busy_timeout_ms` instead of failing immediately.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator


class DatabaseBusy(Exception):
    """The database stayed locked longer than the busy timeout."""


# Schema version 1. Later versions are appended to MIGRATIONS (Stage 2 adds
# reminder columns this way); PRAGMA user_version remembers what was applied.
SCHEMA_V1 = """
CREATE TABLE slots (
    id          INTEGER PRIMARY KEY,
    start_utc   TEXT NOT NULL UNIQUE,   -- one row per start instant => no duplicate slots
    end_utc     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    removed_at  TEXT,                   -- soft delete: NULL means the slot is open
    CHECK (end_utc > start_utc)
);

CREATE TABLE bookings (
    id             INTEGER PRIMARY KEY,
    reference      TEXT NOT NULL UNIQUE,
    slot_id        INTEGER NOT NULL REFERENCES slots(id),
    customer_id    INTEGER NOT NULL,    -- Telegram numeric user ID
    customer_name  TEXT NOT NULL,       -- display name snapshot, no username/phone
    status         TEXT NOT NULL
                   CHECK (status IN ('ACTIVE', 'CANCELLED_BY_CUSTOMER', 'CANCELLED_BY_ADMIN')),
    created_at     TEXT NOT NULL,
    cancelled_at   TEXT,
    CHECK ((status = 'ACTIVE') = (cancelled_at IS NULL))
);

-- THE double-booking guard: at most ONE active booking per slot.
-- Cancelled rows are kept as history and are not covered by this index,
-- so a cancelled slot can be booked again.
CREATE UNIQUE INDEX ux_bookings_one_active_per_slot
    ON bookings(slot_id) WHERE status = 'ACTIVE';

CREATE INDEX ix_bookings_customer_status ON bookings(customer_id, status);
"""

MIGRATIONS = [SCHEMA_V1]


def connect(path: str, busy_timeout_ms: int = 5000) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=busy_timeout_ms / 1000, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # off by default in SQLite!
    conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    return conn


def init_db(path: str, busy_timeout_ms: int = 5000) -> None:
    """Create or upgrade the database. Safe to call on every start."""
    conn = connect(path, busy_timeout_ms)
    try:
        # WAL lets readers continue while one writer is working.
        conn.execute("PRAGMA journal_mode = WAL")
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        for version, script in enumerate(MIGRATIONS, start=1):
            if version <= current:
                continue
            try:
                conn.executescript(
                    f"BEGIN IMMEDIATE;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;"
                )
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
    finally:
        conn.close()


def _is_busy(exc: sqlite3.OperationalError) -> bool:
    text = str(exc).lower()
    return "locked" in text or "busy" in text


@contextmanager
def write_transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE ... COMMIT, or ROLLBACK if anything raises.

    Keep the body short: only SQL and plain Python. Never call the Telegram
    API inside it - the write lock would be held while waiting for the network.
    """
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        if _is_busy(exc):
            raise DatabaseBusy("database is busy") from exc
        raise
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException as exc:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        if isinstance(exc, sqlite3.OperationalError) and _is_busy(exc):
            raise DatabaseBusy("database is busy") from exc
        raise
