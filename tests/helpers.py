"""Shared test helpers (plain functions and constants, no fixtures)."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from slotly.config import Settings

UTC = timezone.utc

ADMIN_ID = 1001
ALICE = 2001
BOB = 2002
CAROL = 2003

# 2026-11-02 09:00 in Berlin (CET, UTC+1). Deterministic "now" for most tests.
NOW = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)


class FakeClock:
    """A clock the test controls. Pass it as `now=` to BookingService."""

    def __init__(self, start: datetime):
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def set(self, moment: datetime) -> None:
        self._now = moment

    def advance(self, **kwargs) -> None:
        self._now += timedelta(**kwargs)


def build_settings(db_path, **overrides) -> Settings:
    values = dict(
        bot_token="123456:TEST-TOKEN-NOT-REAL",
        admin_ids=frozenset({ADMIN_ID}),
        timezone_name="Europe/Berlin",
        database_path=str(db_path),
        cancellation_cutoff_minutes=120,
        max_active_bookings=3,
        business_name="Test Studio",
        busy_timeout_ms=3000,
    )
    values.update(overrides)
    return Settings(**values)


def run_in_threads(functions):
    """Start all callables at (almost) the same instant, one thread each.

    Returns a list of ("ok", value) or ("err", exception) in input order.
    """
    barrier = threading.Barrier(len(functions))
    results = [None] * len(functions)

    def worker(index, function):
        barrier.wait()
        try:
            results[index] = ("ok", function())
        except Exception as exc:  # noqa: BLE001 - we want to inspect any failure
            results[index] = ("err", exc)

    threads = [threading.Thread(target=worker, args=(i, f)) for i, f in enumerate(functions)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return results
