from __future__ import annotations

import sqlite3
import threading

import pytest

from slotly.database import connect, init_db


def test_init_db_is_safe_to_repeat_and_sets_the_schema_version(tmp_path):
    path = str(tmp_path / "fresh.db")
    init_db(path)
    init_db(path)   # second start of the bot: must not fail or recreate tables

    conn = connect(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"slots", "bookings"} <= tables
    finally:
        conn.close()


def test_unique_partial_index_exists_and_is_partial(tmp_path):
    path = str(tmp_path / "idx.db")
    init_db(path)
    conn = connect(path)
    try:
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'ux_bookings_one_active_per_slot'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert "UNIQUE" in sql and "WHERE status = 'ACTIVE'" in sql


def test_status_values_are_restricted_by_the_database(tmp_path):
    path = str(tmp_path / "chk.db")
    init_db(path)
    conn = connect(path)
    try:
        conn.execute("INSERT INTO slots (id, start_utc, end_utc, created_at) VALUES (1, 'a', 'b', 'c')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO bookings (reference, slot_id, customer_id, customer_name, status, created_at) "
                "VALUES ('R', 1, 1, 'x', 'SOMETHING_ELSE', 't')"
            )
    finally:
        conn.close()


def test_connections_keep_sqlite_thread_safety_checks_enabled(tmp_path):
    """We never pass check_same_thread=False: a connection must stay in its thread."""
    path = str(tmp_path / "threads.db")
    init_db(path)
    conn = connect(path)
    errors = []

    def use_from_another_thread():
        try:
            conn.execute("SELECT 1")
        except sqlite3.ProgrammingError as exc:
            errors.append(exc)

    thread = threading.Thread(target=use_from_another_thread)
    thread.start()
    thread.join()
    conn.close()
    assert len(errors) == 1
