"""Handler tests with a mocked Telegram API (no token, no internet).

Updates, queries and contexts are small fake objects; the Telegram methods
(`answer`, `edit_message_text`, `reply_text`) are AsyncMocks, so we can check
exactly what the bot would have sent.

These tests need the python-telegram-bot package installed (for the keyboard
classes). Without it they are skipped.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("telegram")

from slotly import keyboards, messages  # noqa: E402
from slotly.database import connect  # noqa: E402
from slotly.handlers import admin as admin_handlers  # noqa: E402
from slotly.handlers import customer as customer_handlers  # noqa: E402
from slotly.timeutil import parse_hhmm  # noqa: E402
from tests.helpers import ADMIN_ID, ALICE, BOB  # noqa: E402

DAY = date(2026, 11, 3)


def run(coro):
    return asyncio.run(coro)


def make_update(user_id, data=None, *, chat_type="private", first_name="Alice", is_bot=False):
    user = SimpleNamespace(id=user_id, first_name=first_name, last_name=None, is_bot=is_bot)
    chat = SimpleNamespace(type=chat_type, id=user_id)
    message = SimpleNamespace(reply_text=AsyncMock())
    query = None
    if data is not None:
        query = SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock())
    return SimpleNamespace(
        effective_user=user, effective_chat=chat, effective_message=message, callback_query=query,
    )


def make_context(service):
    return SimpleNamespace(application=SimpleNamespace(bot_data={"service": service}))


def press(handler, service, user_id, data, **kwargs):
    """Simulate a button press; returns (update, text_that_was_shown)."""
    update = make_update(user_id, data, **kwargs)
    run(handler(update, make_context(service)))
    shown = None
    if update.callback_query.edit_message_text.await_args is not None:
        shown = update.callback_query.edit_message_text.await_args.args[0]
    return update, shown


def customer_press(service, user_id, data, **kwargs):
    return press(customer_handlers.customer_callback, service, user_id, data, **kwargs)


def admin_press(service, user_id, data, **kwargs):
    return press(admin_handlers.admin_callback, service, user_id, data, **kwargs)


def rows(settings, sql, params=()):
    conn = connect(settings.database_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


# ------------------------------------------------------------ customer flow

def test_start_shows_menu_and_escapes_the_name(service):
    update = make_update(ALICE, first_name="<b>Eve</b>")
    run(customer_handlers.cmd_start(update, make_context(service)))

    text = update.effective_message.reply_text.await_args.args[0]
    assert "&lt;b&gt;Eve&lt;/b&gt;" in text and "<b>Eve</b>" not in text


def test_full_booking_flow_through_buttons(service, settings, add_slots):
    slots = add_slots()

    update, text = customer_press(service, ALICE, "bk")
    assert "میلادی" in text                                   # Gregorian label is shown
    labels = [b.text for row in update.callback_query.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard for b in row]
    assert any("2026-11-03" in label and "6" in label for label in labels)   # date + free-slot count

    _, text = customer_press(service, ALICE, "bt:2026-11-03")
    assert "2026-11-03" in text

    _, text = customer_press(service, ALICE, f"bs:{slots[0].id}")
    assert "خلاصهٔ نوبت" in text and "09:00" in text

    _, text = customer_press(service, ALICE, f"bc:{slots[0].id}")
    reference = rows(settings, "SELECT reference FROM bookings")[0]["reference"]
    assert reference in text and "ثبت شد" in text

    # Repeating the confirmation shows the same booking and creates nothing new.
    _, text = customer_press(service, ALICE, f"bc:{slots[0].id}")
    assert reference in text and "قبلاً" in text
    assert len(rows(settings, "SELECT id FROM bookings")) == 1


def test_losing_customer_sees_the_friendly_message(service, add_slots):
    slots = add_slots()
    customer_press(service, ALICE, f"bc:{slots[0].id}")

    _, text = customer_press(service, BOB, f"bc:{slots[0].id}", first_name="Bob")

    assert text == "این زمان همین حالا رزرو شد. لطفاً زمان دیگری را انتخاب کنید."


def test_my_appointments_and_cancellation_flow(service, settings, add_slots):
    slots = add_slots()
    customer_press(service, ALICE, f"bc:{slots[0].id}")
    booking_id = rows(settings, "SELECT id FROM bookings")[0]["id"]

    _, text = customer_press(service, ALICE, "my")
    assert "2026-11-03" in text and "تأیید شده" in text and "قابل لغو تا" in text

    _, text = customer_press(service, ALICE, f"mc:{booking_id}")
    assert "مطمئن" in text

    _, text = customer_press(service, ALICE, f"mx:{booking_id}")
    assert "لغو شد" in text
    assert rows(settings, "SELECT status FROM bookings")[0]["status"] == "CANCELLED_BY_CUSTOMER"

    _, text = customer_press(service, ALICE, f"mx:{booking_id}")   # pressed twice
    assert "قبلاً لغو شده" in text


def test_customer_cannot_cancel_someone_elses_booking_via_callback(service, settings, add_slots):
    slots = add_slots()
    customer_press(service, ALICE, f"bc:{slots[0].id}")
    booking_id = rows(settings, "SELECT id FROM bookings")[0]["id"]

    _, text = customer_press(service, BOB, f"mx:{booking_id}", first_name="Bob")   # forged ID

    assert text == "این نوبت پیدا نشد."
    assert rows(settings, "SELECT status FROM bookings")[0]["status"] == "ACTIVE"
    _, text = customer_press(service, BOB, f"mc:{booking_id}", first_name="Bob")
    assert text == "این نوبت پیدا نشد."


@pytest.mark.parametrize(
    "data",
    ["bs:abc", "bs:-5", "bs:0", "bs:99999999999999999999", "bt:not-a-date", "bt:", "bc:", "mx:x", "zzz",
     "bc:1;DROP TABLE slots", "bt:2026-13-45"],
)
def test_garbage_callback_data_never_crashes(service, settings, data):
    update, text = customer_press(service, ALICE, data)
    update.callback_query.answer.assert_awaited()
    assert text  # the user always gets some answer
    assert rows(settings, "SELECT name FROM sqlite_master WHERE name = 'slots'")


def test_group_chats_are_ignored(service, add_slots):
    slots = add_slots()
    update, text = customer_press(service, ALICE, f"bc:{slots[0].id}", chat_type="group")

    assert text is None
    update.callback_query.edit_message_text.assert_not_awaited()
    assert service.list_customer_bookings(ALICE) == []


def test_bots_are_ignored(service):
    update, text = customer_press(service, ALICE, "bk", is_bot=True)
    assert text is None


def test_telegram_failure_after_booking_does_not_leave_the_database_locked(service, settings, add_slots):
    """Telegram is only called AFTER the transaction is committed and closed."""
    slots = add_slots()
    update = make_update(ALICE, f"bc:{slots[0].id}")
    update.callback_query.edit_message_text.side_effect = RuntimeError("network down")

    with pytest.raises(RuntimeError):
        run(customer_handlers.customer_callback(update, make_context(service)))

    assert len(rows(settings, "SELECT id FROM bookings")) == 1       # the booking was committed
    other = connect(settings.database_path, busy_timeout_ms=100)     # and no write lock is held
    try:
        other.execute("BEGIN IMMEDIATE")
        other.execute("ROLLBACK")
    finally:
        other.close()


# ----------------------------------------------------------------- admin

ADMIN_CALLBACKS = [
    "a", "a:td", "a:up", "a:n", "a:nd:2026-11-03", "a:ns:2026-11-03:0900",
    "a:ne:2026-11-03:0900:1000", "a:nc:2026-11-03:0900:1000", "a:r", "a:rd:2026-11-03",
    "a:rs:1", "a:rx:1",
]


@pytest.mark.parametrize("data", ADMIN_CALLBACKS)
def test_non_admin_is_rejected_for_every_admin_callback(service, settings, data):
    update, text = admin_press(service, ALICE, data)

    update.callback_query.answer.assert_awaited_once_with(messages.NOT_ADMIN, show_alert=True)
    update.callback_query.edit_message_text.assert_not_awaited()
    assert rows(settings, "SELECT id FROM slots") == []             # nothing was created


def test_non_admin_cannot_open_admin_command(service):
    update = make_update(ALICE)
    run(admin_handlers.cmd_admin(update, make_context(service)))
    update.effective_message.reply_text.assert_awaited_once_with(messages.NOT_ADMIN)


def test_admin_usernames_in_text_are_irrelevant(service):
    # Only the numeric ID counts: a user whose *name* looks like an admin is still a customer.
    update = make_update(ALICE, "a:nc:2026-11-03:0900:1000", first_name=str(ADMIN_ID))
    run(admin_handlers.admin_callback(update, make_context(service)))
    update.callback_query.edit_message_text.assert_not_awaited()


def test_admin_adds_slots_with_preview_then_save(service, settings):
    _, text = admin_press(service, ADMIN_ID, "a:ne:2026-11-03:0900:1100")
    assert "4 نوبت" in text and "ذخیره" in text                      # preview text + save hint
    assert rows(settings, "SELECT id FROM slots") == []              # preview saved nothing

    _, text = admin_press(service, ADMIN_ID, "a:nc:2026-11-03:0900:1100")
    assert "ذخیره شد" in text and len(rows(settings, "SELECT id FROM slots")) == 4

    _, text = admin_press(service, ADMIN_ID, "a:nc:2026-11-03:0900:1100")   # repeated request
    assert len(rows(settings, "SELECT id FROM slots")) == 4


def test_admin_sees_a_friendly_error_for_a_past_range(service, settings):
    _, text = admin_press(service, ADMIN_ID, "a:nc:2026-11-01:0900:1000")
    assert "آینده" in text
    assert rows(settings, "SELECT id FROM slots") == []


def test_admin_lists_escape_customer_names(service, add_slots):
    slots = add_slots()
    update = make_update(ALICE, f"bc:{slots[0].id}", first_name="<b>Evil</b> & Co")
    run(customer_handlers.customer_callback(update, make_context(service)))

    _, text = admin_press(service, ADMIN_ID, "a:up")

    assert "&lt;b&gt;Evil&lt;/b&gt; &amp; Co" in text
    assert "<b>Evil</b>" not in text


def test_admin_cannot_remove_a_booked_slot_through_the_ui(service, settings, add_slots):
    slots = add_slots()
    customer_press(service, ALICE, f"bc:{slots[0].id}")

    _, text = admin_press(service, ADMIN_ID, f"a:rx:{slots[0].id}")

    assert "رزرو فعال" in text
    assert rows(settings, "SELECT status FROM bookings")[0]["status"] == "ACTIVE"
    assert rows(settings, "SELECT removed_at FROM slots WHERE id = ?", (slots[0].id,))[0]["removed_at"] is None


def test_admin_removes_a_free_slot(service, settings, add_slots):
    slots = add_slots()
    _, text = admin_press(service, ADMIN_ID, f"a:rs:{slots[1].id}")
    assert "حذف شود" in text
    _, text = admin_press(service, ADMIN_ID, f"a:rx:{slots[1].id}")
    assert "حذف شد" in text
    assert slots[1].id not in [s.id for s in service.list_available_slots(DAY)]


@pytest.mark.parametrize("data", ["a:nd:garbage", "a:ns:2026-11-03:99", "a:ne:2026-11-03:0900", "a:rs:abc", "a:zzz"])
def test_admin_garbage_callback_data_is_handled(service, data):
    update, text = admin_press(service, ADMIN_ID, data)
    assert text == messages.INVALID_REQUEST


# --------------------------------------------------- keyboards and routing

def test_every_button_fits_telegram_limits_and_is_routed_exactly_once(service, add_slots):
    slots = add_slots()
    tz = service.settings.timezone
    booking = service.book_slot(ALICE, "Alice", slots[0].id).booking
    items = service.list_slots_for_date(ADMIN_ID, DAY)
    markups = [
        keyboards.main_menu(True), keyboards.home_only(),
        keyboards.date_list(service.list_available_dates()),
        keyboards.time_list(service.list_available_slots(DAY), tz),
        keyboards.booking_summary(slots[1].id, DAY), keyboards.after_booking(),
        keyboards.error_back_to_dates(), keyboards.my_appointments([booking], tz),
        keyboards.cancel_confirm(booking.id), keyboards.back_to_my(),
        keyboards.admin_menu(), keyboards.admin_back(),
        keyboards.admin_date_picker([DAY], "a:nd"),
        keyboards.admin_time_picker([parse_hhmm("09:00")], DAY, "ns"),
        keyboards.admin_time_picker([parse_hhmm("10:00")], DAY, "ne", carry="0900"),
        keyboards.admin_preview(DAY, parse_hhmm("09:00"), parse_hhmm("10:00"), True),
        keyboards.admin_after_save(), keyboards.admin_slot_list(DAY, items, tz),
        keyboards.admin_confirm_remove(slots[1].id, DAY),
    ]
    seen = 0
    for markup in markups:
        for row in markup.inline_keyboard:
            for button in row:
                data = button.callback_data
                assert len(data.encode("utf-8")) <= 64
                customer = bool(re.match(keyboards.CUSTOMER_PATTERN, data))
                administrative = bool(re.match(keyboards.ADMIN_PATTERN, data))
                assert customer != administrative, f"{data!r} must match exactly one handler"
                seen += 1
    assert seen > 30
