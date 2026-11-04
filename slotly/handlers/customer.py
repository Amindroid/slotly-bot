"""Customer-facing handlers: menu, booking flow, my appointments, cancellation.

Pattern for every action:
  1. answer the callback (stops Telegram's loading spinner);
  2. parse the callback data defensively;
  3. call the BookingService with the REQUESTING user's Telegram ID
     (taken from `update.effective_user`, never from callback data);
  4. only after the service returned (its DB transaction is already closed),
     talk to Telegram to show the result.
"""

from __future__ import annotations

import logging

from slotly import keyboards, messages
from slotly.booking_service import BookingError, DatabaseBusy
from slotly.handlers.common import (
    display_name,
    get_service,
    parse_iso_date,
    parse_positive_int,
    private_only,
    show,
    show_invalid_request,
)
from slotly.timeutil import to_local

logger = logging.getLogger(__name__)

MAX_DATE_BUTTONS = 14   # keeps the keyboard small and the callback list short

EXPECTED_ERRORS = (BookingError, DatabaseBusy)


async def _show_home(update, context) -> None:
    service = get_service(context)
    user = update.effective_user
    await show(
        update,
        messages.welcome(display_name(user), service.settings),
        keyboards.main_menu(show_admin=service.is_admin(user.id)),
    )


@private_only
async def cmd_start(update, context) -> None:
    await _show_home(update, context)


@private_only
async def cmd_help(update, context) -> None:
    service = get_service(context)
    await show(update, messages.help_text(service.settings), keyboards.home_only())


@private_only
async def customer_callback(update, context) -> None:
    query = update.callback_query
    await query.answer()
    service = get_service(context)
    settings = service.settings
    user = update.effective_user
    action, _, arg = (query.data or "").partition(":")

    try:
        if action == "home":
            await _show_home(update, context)

        elif action == "help":
            await show(update, messages.help_text(settings), keyboards.home_only())

        elif action == "bk":                                   # choose a date
            dates = service.list_available_dates()[:MAX_DATE_BUTTONS]
            if not dates:
                await show(update, messages.no_free_slots(), keyboards.home_only())
            else:
                await show(update, messages.choose_date(settings), keyboards.date_list(dates))

        elif action == "bt":                                   # choose a time
            day = parse_iso_date(arg)
            if day is None:
                await show_invalid_request(update, keyboards.home_only())
                return
            slots = service.list_available_slots(day)
            if not slots:
                await show(update, messages.no_times_on_date(day), keyboards.error_back_to_dates())
            else:
                await show(update, messages.choose_time(day), keyboards.time_list(slots, settings.timezone))

        elif action == "bs":                                   # confirmation summary
            slot_id = parse_positive_int(arg)
            if slot_id is None:
                await show_invalid_request(update, keyboards.home_only())
                return
            slot = service.get_bookable_slot(slot_id)          # re-checked: may raise
            local_date = to_local(slot.start, settings.timezone).date()
            await show(
                update,
                messages.booking_summary(slot, settings),
                keyboards.booking_summary(slot.id, local_date),
            )

        elif action == "bc":                                   # confirmed: book it
            slot_id = parse_positive_int(arg)
            if slot_id is None:
                await show_invalid_request(update, keyboards.home_only())
                return
            result = service.book_slot(user.id, display_name(user), slot_id)
            await show(update, messages.booking_done(result, settings), keyboards.after_booking())

        elif action == "my":                                   # my appointments
            bookings = service.list_customer_bookings(user.id)
            now = service.now()
            cancellable = [b for b in bookings if now <= service.cancellation_deadline(b.start)]
            await show(
                update,
                messages.my_appointments(bookings, service),
                keyboards.my_appointments(cancellable, settings.timezone),
            )

        elif action == "mc":                                   # ask to confirm cancellation
            booking_id = parse_positive_int(arg)
            if booking_id is None:
                await show_invalid_request(update, keyboards.home_only())
                return
            booking = service.get_customer_booking(user.id, booking_id)   # scoped to this user
            if booking.status != "ACTIVE":
                await show(update, messages.cancel_done(booking, settings, already=True), keyboards.back_to_my())
            else:
                await show(update, messages.cancel_confirmation(booking, settings), keyboards.cancel_confirm(booking.id))

        elif action == "mx":                                   # cancellation confirmed
            booking_id = parse_positive_int(arg)
            if booking_id is None:
                await show_invalid_request(update, keyboards.home_only())
                return
            result = service.cancel_booking_by_customer(user.id, booking_id)
            await show(
                update,
                messages.cancel_done(result.booking, settings, already=result.already_cancelled),
                keyboards.back_to_my(),
            )

        else:
            await show_invalid_request(update, keyboards.home_only())

    except EXPECTED_ERRORS as exc:
        # Slot just taken, slot in the past, limit reached, too late to cancel...
        markup = keyboards.back_to_my() if action in ("mc", "mx") else keyboards.error_back_to_dates()
        await show(update, messages.customer_error(exc, settings), markup)
