"""Small helpers shared by the customer and admin handlers."""

from __future__ import annotations

import functools
import logging
from datetime import date
from typing import Optional

from telegram.constants import ParseMode
from telegram.error import BadRequest

from slotly import messages
from slotly.booking_service import BookingService
from slotly.timeutil import parse_hhmm

logger = logging.getLogger(__name__)


def get_service(context) -> BookingService:
    return context.application.bot_data["service"]


def display_name(user) -> str:
    """The name Telegram shows for the user (never the @username)."""
    parts = [getattr(user, "first_name", "") or "", getattr(user, "last_name", "") or ""]
    return " ".join(p for p in parts if p).strip() or "مشتری"


def private_only(handler):
    """Ignore everything that does not come from a private chat with a human."""

    @functools.wraps(handler)
    async def wrapper(update, context):
        chat = update.effective_chat
        user = update.effective_user
        if chat is None or user is None or chat.type != "private" or getattr(user, "is_bot", False):
            if update.callback_query is not None:
                await update.callback_query.answer()
            return None
        return await handler(update, context)

    return wrapper


async def show(update, text: str, markup=None) -> None:
    """Edit the message the user just pressed a button on, or send a new one."""
    query = update.callback_query
    if query is not None:
        try:
            await query.edit_message_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
        except BadRequest as exc:
            # Pressing the same button twice produces an identical message: harmless.
            if "not modified" not in str(exc).lower():
                raise
        return
    await update.effective_message.reply_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)


async def show_invalid_request(update, markup) -> None:
    await show(update, messages.INVALID_REQUEST, markup)


def parse_positive_int(text: str) -> Optional[int]:
    """Parse an ID from callback data. Returns None for anything suspicious."""
    if not text or not text.isdigit() or len(text) > 12:
        return None
    value = int(text)
    return value if value > 0 else None


def parse_iso_date(text: str) -> Optional[date]:
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def parse_compact_time(text: str):
    """"0930" -> time(9, 30), or None if invalid."""
    try:
        return parse_hhmm(text)
    except (TypeError, ValueError):
        return None
