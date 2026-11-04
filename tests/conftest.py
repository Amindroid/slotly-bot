from __future__ import annotations

from datetime import date

import pytest

from slotly.booking_service import BookingService
from slotly.database import init_db
from slotly.timeutil import parse_hhmm
from tests.helpers import ADMIN_ID, NOW, FakeClock, build_settings


@pytest.fixture
def clock():
    return FakeClock(NOW)


@pytest.fixture
def settings(tmp_path):
    """Settings pointing at a FILE-backed temporary database."""
    s = build_settings(tmp_path / "slotly_test.db")
    init_db(s.database_path, s.busy_timeout_ms)
    return s


@pytest.fixture
def service(settings, clock):
    return BookingService(settings, now=clock)


@pytest.fixture
def add_slots(service):
    """add_slots("2026-11-03", "09:00", "12:00") -> list of open Slot objects."""

    def _add(day="2026-11-03", start="09:00", end="12:00"):
        local_date = date.fromisoformat(day)
        service.create_slots(ADMIN_ID, local_date, parse_hhmm(start), parse_hhmm(end))
        return service.list_available_slots(local_date)

    return _add
