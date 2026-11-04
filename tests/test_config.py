from __future__ import annotations

import pytest

from slotly.config import ConfigError, load_settings

VALID = {"BOT_TOKEN": "123456:ABC-fake_token_for_tests", "ADMIN_IDS": "111, 222"}


def test_loads_valid_configuration_with_defaults():
    s = load_settings(VALID)

    assert s.admin_ids == frozenset({111, 222})
    assert s.timezone_name == "Asia/Tehran"
    assert s.cancellation_cutoff_minutes == 120
    assert s.max_active_bookings == 3
    assert s.is_admin(111) and not s.is_admin(333)


def test_custom_values_are_read(tmp_path):
    db_path = str(tmp_path / "x.db")

    s = load_settings({
        **VALID,
        "BUSINESS_TIMEZONE": "Europe/Berlin",
        "CANCELLATION_CUTOFF_MINUTES": "60",
        "MAX_ACTIVE_BOOKINGS": "5",
        "DATABASE_PATH": db_path,
    })

    assert (
        s.timezone_name,
        s.cancellation_cutoff_minutes,
        s.max_active_bookings,
    ) == ("Europe/Berlin", 60, 5)

    assert s.database_path == db_path


def test_relative_database_path_is_resolved_from_the_project_folder():
    s = load_settings({**VALID, "DATABASE_PATH": "data/slotly.db"})
    assert s.database_path.replace("\\", "/").endswith("data/slotly.db")
    assert s.database_path.startswith(("/", "C:", "c:")) or ":" in s.database_path


@pytest.mark.parametrize(
    "overrides",
    [
        {"BOT_TOKEN": ""},
        {"BOT_TOKEN": "not-a-token"},
        {"ADMIN_IDS": ""},
        {"ADMIN_IDS": "@my_username"},
        {"ADMIN_IDS": "123,abc"},
        {"ADMIN_IDS": "-5"},
        {"BUSINESS_TIMEZONE": "Mars/Olympus"},
        {"CANCELLATION_CUTOFF_MINUTES": "soon"},
        {"CANCELLATION_CUTOFF_MINUTES": "-1"},
        {"MAX_ACTIVE_BOOKINGS": "0"},
    ],
)
def test_invalid_configuration_is_rejected(overrides):
    with pytest.raises(ConfigError):
        load_settings({**VALID, **overrides})


def test_token_is_not_shown_in_repr():
    s = load_settings(VALID)
    assert "ABC-fake_token" not in repr(s)
