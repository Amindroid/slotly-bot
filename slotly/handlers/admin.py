"""Admin handlers (everything under /admin and the "a" callbacks).

Authorization happens TWICE on purpose:
  1. `admin_only` below checks the numeric Telegram ID before the handler runs;
  2. every BookingService admin method checks `actor_id` again by itself.
Neither check ever looks at a username or at anything inside callback data.
"""

from __future__ import annotations

import functools
import logging
from datetime import date, datetime, time, timedelta

from slotly import keyboards, messages
from slotly.booking_service import BookingError, DatabaseBusy, NotAuthorized
from slotly.config import SLOT_MINUTES
from slotly.handlers.common import (
    get_service,
    parse_compact_time,
    parse_iso_date,
    parse_positive_int,
    private_only,
    show,
    show_invalid_request,
)
from slotly.timeutil import to_local

logger = logging.getLogger(__name__)

UI_FIRST_TIME = time(6, 0)     # earliest time offered in the time pickers
UI_LAST_TIME = time(22, 0)     # latest END time offered
DATE_CHOICES = 14              # how many days ahead the admin can pick
APPOINTMENT_LIST_LIMIT = 20

EXPECTED_ERRORS = (BookingError, DatabaseBusy)


def admin_only(handler):
    """Run the handler only for configured administrators (numeric ID check)."""

    @functools.wraps(handler)
    async def wrapper(update, context):
        service = get_service(context)
        user = update.effective_user
        if user is None or not service.is_admin(user.id):
            if update.callback_query is not None:
                await update.callback_query.answer(messages.NOT_ADMIN, show_alert=True)
            else:
                await update.effective_message.reply_text(messages.NOT_ADMIN)
            return None
        return await handler(update, context)

    return wrapper


def _slot_times(first: time, last: time) -> list:
    """All times from `first` to `last` (inclusive) in slot-length steps."""
    step = timedelta(minutes=SLOT_MINUTES)
    cursor = datetime.combine(date.min, first)
    stop = datetime.combine(date.min, last)
    result = []
    while cursor <= stop:
        result.append(cursor.time())
        cursor += step
    return result


def _local_today(service) -> date:
    return to_local(service.now(), service.settings.timezone).date()


@private_only
@admin_only
async def cmd_admin(update, context) -> None:
    await show(update, messages.ADMIN_MENU, keyboards.admin_menu())


@private_only
@admin_only
async def admin_callback(update, context) -> None:
    query = update.callback_query
    await query.answer()
    service = get_service(context)
    settings = service.settings
    actor = update.effective_user.id
    parts = (query.data or "").split(":")          # ["a", "nd", "2026-11-03", ...]
    action = parts[1] if len(parts) > 1 else "menu"
    args = parts[2:]
    tz = settings.timezone

    try:
        if action == "menu":
            await show(update, messages.ADMIN_MENU, keyboards.admin_menu())

        elif action == "td":                                   # today's appointments
            today = _local_today(service)
            bookings = service.list_appointments_on(actor, today, APPOINTMENT_LIST_LIMIT)
            title = f"📅 <b>نوبت‌های امروز</b> — {messages.fmt_date(today)}"
            await show(update, messages.admin_appointments(title, bookings, settings, APPOINTMENT_LIST_LIMIT),
                       keyboards.admin_back())

        elif action == "up":                                   # upcoming appointments
            bookings = service.list_upcoming_appointments(actor, APPOINTMENT_LIST_LIMIT)
            title = "📋 <b>نوبت‌های آینده</b>"
            await show(update, messages.admin_appointments(title, bookings, settings, APPOINTMENT_LIST_LIMIT),
                       keyboards.admin_back())

        # ---- add slots: date -> start -> end -> preview -> save ------------
        elif action == "n":
            today = _local_today(service)
            days = [today + timedelta(days=i) for i in range(DATE_CHOICES)]
            await show(update, messages.admin_pick_date("➕ <b>افزودن زمان آزاد</b>", settings),
                       keyboards.admin_date_picker(days, "a:nd"))

        elif action == "nd":
            day = parse_iso_date(args[0]) if args else None
            if day is None:
                await show_invalid_request(update, keyboards.admin_back())
                return
            options = _slot_times(UI_FIRST_TIME, time(21, 30))
            now_local = to_local(service.now(), tz)
            if day == now_local.date():
                options = [t for t in options if t > now_local.time()]   # hide times already past
            if not options:
                await show(update, messages.admin_no_start_options(day), keyboards.admin_back())
                return
            await show(update, messages.admin_pick_start(day),
                       keyboards.admin_time_picker(options, day, "ns"))

        elif action == "ns":
            day = parse_iso_date(args[0]) if len(args) == 2 else None
            start = parse_compact_time(args[1]) if len(args) == 2 else None
            if day is None or start is None:
                await show_invalid_request(update, keyboards.admin_back())
                return
            later = _slot_times(UI_FIRST_TIME, UI_LAST_TIME)
            options = [t for t in later if t > start]
            await show(update, messages.admin_pick_end(day, f"{start:%H:%M}"),
                       keyboards.admin_time_picker(options, day, "ne", carry=args[1]))

        elif action == "ne":                                   # preview (nothing saved yet)
            day, start, end = _parse_range_args(args)
            if day is None:
                await show_invalid_request(update, keyboards.admin_back())
                return
            try:
                preview = service.preview_slots(actor, day, start, end)
            except BookingError as exc:
                if isinstance(exc, NotAuthorized):
                    raise
                await show(update, messages.admin_error(exc, settings),
                           keyboards.admin_preview(day, start, end, can_save=False))
                return
            await show(update, messages.admin_preview(preview, f"{start:%H:%M}", f"{end:%H:%M}", settings),
                       keyboards.admin_preview(day, start, end, can_save=preview.new_count > 0))

        elif action == "nc":                                   # save
            day, start, end = _parse_range_args(args)
            if day is None:
                await show_invalid_request(update, keyboards.admin_back())
                return
            result = service.create_slots(actor, day, start, end)
            await show(update, messages.admin_slots_saved(result), keyboards.admin_after_save())

        # ---- remove an unbooked slot --------------------------------------
        elif action == "r":
            today = _local_today(service)
            days = [today + timedelta(days=i) for i in range(DATE_CHOICES)]
            await show(update, messages.admin_pick_date("🗑 <b>حذف زمان آزاد</b>", settings),
                       keyboards.admin_date_picker(days, "a:rd"))

        elif action == "rd":
            day = parse_iso_date(args[0]) if args else None
            if day is None:
                await show_invalid_request(update, keyboards.admin_back())
                return
            items = service.list_slots_for_date(actor, day)
            await show(update, messages.admin_slot_list(day, items, settings),
                       keyboards.admin_slot_list(day, items, tz))

        elif action == "rs":                                   # confirm removal
            slot_id = parse_positive_int(args[0]) if args else None
            if slot_id is None:
                await show_invalid_request(update, keyboards.admin_back())
                return
            slot = service.get_open_slot(actor, slot_id)
            day = to_local(slot.start, tz).date()
            await show(update, messages.admin_confirm_remove(slot, settings),
                       keyboards.admin_confirm_remove(slot.id, day))

        elif action == "rx":                                   # do the removal
            slot_id = parse_positive_int(args[0]) if args else None
            if slot_id is None:
                await show_invalid_request(update, keyboards.admin_back())
                return
            slot = service.get_open_slot(actor, slot_id)
            service.remove_slot(actor, slot_id)                # refuses booked slots
            await show(update, messages.admin_slot_removed(slot, settings), keyboards.admin_back())

        else:
            await show_invalid_request(update, keyboards.admin_back())

    except EXPECTED_ERRORS as exc:
        await show(update, messages.admin_error(exc, settings), keyboards.admin_back())


def _parse_range_args(args: list):
    """["2026-11-03", "0900", "1200"] -> (date, time, time) or (None, None, None)."""
    if len(args) != 3:
        return None, None, None
    day, start, end = parse_iso_date(args[0]), parse_compact_time(args[1]), parse_compact_time(args[2])
    if day is None or start is None or end is None:
        return None, None, None
    return day, start, end
