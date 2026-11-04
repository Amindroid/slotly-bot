"""Entry point: `python -m slotly.main` (from the project folder).

Startup order: read .env -> validate settings -> create/upgrade the database
-> build the Telegram application -> long polling.
"""

from __future__ import annotations

import logging
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, filters

from slotly import keyboards, messages
from slotly.booking_service import BookingService
from slotly.config import PROJECT_ROOT, ConfigError, Settings, load_settings
from slotly.database import init_db
from slotly.handlers import admin, customer

logger = logging.getLogger("slotly")

class RedactingFormatter(logging.Formatter):
    """Removes the bot token from every log line, including tracebacks.

    Some HTTP libraries log full URLs, and Telegram's URLs contain the token.
    """

    def __init__(self, secret: str):
        super().__init__("%(asctime)s %(levelname)s %(name)s: %(message)s")
        self._secret = secret

    def format(self, record: logging.LogRecord) -> str:
        return super().format(record).replace(self._secret, "<BOT_TOKEN>")


def configure_logging(token: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter(token))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    # httpx logs every request URL at INFO level; keep it quiet as a second safeguard.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Last-resort handler: log the problem and tell the user politely."""
    # Only the exception is logged, not the update (it contains personal data).
    logger.error("Unhandled exception while processing an update", exc_info=context.error)
    if isinstance(update, Update):
        try:
            if update.callback_query is not None:
                await update.callback_query.answer(messages.UNEXPECTED_ERROR, show_alert=True)
            elif update.effective_message is not None:
                await update.effective_message.reply_text(messages.UNEXPECTED_ERROR)
        except Exception:  # noqa: BLE001 - never let the error handler crash
            logger.warning("Could not notify the user about an error")


def build_application(settings: Settings, service: BookingService) -> Application:
    application = Application.builder().token(settings.bot_token).build()
    application.bot_data["service"] = service      # handlers read it from here

    private = filters.ChatType.PRIVATE
    application.add_handler(CommandHandler("start", customer.cmd_start, filters=private))
    application.add_handler(CommandHandler("help", customer.cmd_help, filters=private))
    application.add_handler(CommandHandler("admin", admin.cmd_admin, filters=private))
    application.add_handler(CallbackQueryHandler(customer.customer_callback, pattern=keyboards.CUSTOMER_PATTERN))
    application.add_handler(CallbackQueryHandler(admin.admin_callback, pattern=keyboards.ADMIN_PATTERN))
    application.add_error_handler(on_error)
    return application


def main() -> None:
    # utf-8-sig tolerates a BOM, which some Windows editors add to .env files.
    load_dotenv(Path(PROJECT_ROOT) / ".env", encoding="utf-8-sig")
    try:
        settings = load_settings()
    except ConfigError as exc:
        raise SystemExit(f"Configuration error: {exc}") from None

    configure_logging(settings.bot_token)
    init_db(settings.database_path, settings.busy_timeout_ms)
    service = BookingService(settings)
    application = build_application(settings, service)

    logger.info("Slotly is starting (timezone: %s). Press Ctrl+C to stop.", settings.timezone_name)
    application.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
