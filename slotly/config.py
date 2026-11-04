"""Configuration: read and validate settings from environment variables.

`load_settings()` takes a mapping so tests can pass a plain dict. Only
`main.py` calls `load_dotenv()` to fill os.environ from the .env file.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Version 1 uses one fixed slot length. It is a constant on purpose.
SLOT_MINUTES = 30

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ConfigError(Exception):
    """Raised when the environment configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    # repr=False keeps the token out of logs and tracebacks.
    bot_token: str = field(repr=False)
    admin_ids: frozenset
    timezone_name: str
    database_path: str
    cancellation_cutoff_minutes: int = 120
    max_active_bookings: int = 3
    business_name: str = "Slotly Demo"
    busy_timeout_ms: int = 5000

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    def is_admin(self, user_id: object) -> bool:
        """Admin check by numeric Telegram user ID. Usernames are never used."""
        return isinstance(user_id, int) and not isinstance(user_id, bool) and user_id in self.admin_ids


def _get_int(env: Mapping[str, str], key: str, default: int, minimum: int) -> int:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{key} must be a whole number, got {raw!r}.") from None
    if value < minimum:
        raise ConfigError(f"{key} must be at least {minimum}, got {value}.")
    return value


def _parse_admin_ids(raw: str) -> frozenset:
    parts = [p for p in re.split(r"[,\s]+", raw.strip()) if p]
    if not parts:
        raise ConfigError(
            "ADMIN_IDS must contain at least one numeric Telegram user ID "
            "(example: ADMIN_IDS=123456789)."
        )
    ids = set()
    for part in parts:
        if not part.isdigit() or int(part) <= 0:
            raise ConfigError(
                f"ADMIN_IDS must contain only numeric Telegram user IDs, got {part!r}. "
                "Usernames are not accepted."
            )
        ids.add(int(part))
    return frozenset(ids)


def load_settings(env: Optional[Mapping[str, str]] = None) -> Settings:
    if env is None:
        env = os.environ

    token = env.get("BOT_TOKEN", "").strip()
    if not token:
        raise ConfigError("BOT_TOKEN is missing. Put it in the .env file (see .env.example).")
    if not re.fullmatch(r"\d+:\S+", token):
        raise ConfigError("BOT_TOKEN does not look like a BotFather token (expected 'digits:letters').")

    admin_ids = _parse_admin_ids(env.get("ADMIN_IDS", ""))

    timezone_name = env.get("BUSINESS_TIMEZONE", "").strip() or "Asia/Tehran"
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(
            f"BUSINESS_TIMEZONE {timezone_name!r} is not a known timezone. "
            "Use an IANA name such as 'Asia/Tehran' or 'Europe/Berlin'. "
            "On Windows make sure the 'tzdata' package is installed (pip install tzdata)."
        ) from None

    database_path = env.get("DATABASE_PATH", "").strip() or "slotly.db"
    if not Path(database_path).is_absolute():
        # A relative path is resolved from the project folder, not from the
        # folder you happened to start the terminal in.
        database_path = str(PROJECT_ROOT / database_path)

    return Settings(
        bot_token=token,
        admin_ids=admin_ids,
        timezone_name=timezone_name,
        database_path=database_path,
        cancellation_cutoff_minutes=_get_int(env, "CANCELLATION_CUTOFF_MINUTES", 120, 0),
        max_active_bookings=_get_int(env, "MAX_ACTIVE_BOOKINGS", 3, 1),
        business_name=env.get("BUSINESS_NAME", "").strip() or "Slotly Demo",
    )
