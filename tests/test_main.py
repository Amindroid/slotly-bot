"""main.py: log redaction and handler registration (needs python-telegram-bot)."""

from __future__ import annotations

import logging

import pytest

telegram = pytest.importorskip("telegram")
pytest.importorskip("dotenv")

from slotly.main import RedactingFormatter, build_application  # noqa: E402


def test_log_lines_never_contain_the_token():
    token = "123456:SECRET-TOKEN-VALUE"
    formatter = RedactingFormatter(token)
    record = logging.LogRecord(
        "httpx", logging.INFO, __file__, 1,
        "POST https://api.telegram.org/bot%s/getUpdates", (token,), None,
    )
    output = formatter.format(record)
    assert token not in output and "<BOT_TOKEN>" in output


def test_application_registers_the_expected_handlers(service):
    if not hasattr(telegram, "__version__"):
        pytest.skip("real python-telegram-bot is not installed")
    application = build_application(service.settings, service)

    assert application.bot_data["service"] is service
    assert len(application.handlers[0]) == 5       # /start /help /admin + 2 callback routers
    assert len(application.error_handlers) == 1
