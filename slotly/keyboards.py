"""Inline keyboards and the callback-data format.

Callback data is "action:arg:arg" and must stay under 64 bytes (Telegram's limit).
IMPORTANT: callback data comes from the user's device and can be forged or
stale. It only says "which thing the user pressed"; every handler re-checks
permissions and the current database state before acting on it.

Customer actions            Admin actions (prefix "a")
  home                        a                    admin menu
  help                        a:td / a:up          today / upcoming appointments
  bk                          a:n                  new slots: pick date
  bt:<date>                   a:nd:<date>          pick start
  bs:<slot_id>                a:ns:<date>:<HHMM>   pick end
  bc:<slot_id>                a:ne:<date>:<HHMM>:<HHMM>  preview
  my                          a:nc:<date>:<HHMM>:<HHMM>  save
  mc:<booking_id>             a:r                  remove: pick date
  mx:<booking_id>             a:rd:<date>          list slots of a day
                              a:rs:<slot_id>       confirm removal
                              a:rx:<slot_id>       remove
"""

from __future__ import annotations

from datetime import date, time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from slotly import messages
from slotly.timeutil import format_hhmm_compact, to_local


# Which handler receives which callback data (used by main.py, checked by tests).
CUSTOMER_PATTERN = r"^(home|help|bk|my)$|^(bt|bs|bc|mc|mx):"
ADMIN_PATTERN = r"^a(:|$)"


def _button(text: str, data: str) -> InlineKeyboardButton:
    if len(data.encode("utf-8")) > 64:
        raise ValueError(f"callback data too long: {data!r}")
    return InlineKeyboardButton(text, callback_data=data)


def _grid(buttons: list, per_row: int) -> list:
    return [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]


HOME_ROW = [_button("🏠 منوی اصلی", "home")]


# -------------------------------------------------------------- customer ---

def main_menu(show_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [_button("📅 رزرو نوبت", "bk")],
        [_button("🗂 نوبت‌های من", "my")],
        [_button("ℹ️ راهنما", "help")],
    ]
    if show_admin:  # convenience only; admin handlers re-check the permission
        rows.append([_button("🛠 مدیریت", "a")])
    return InlineKeyboardMarkup(rows)


def home_only() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([HOME_ROW])


def date_list(dates: list) -> InlineKeyboardMarkup:
    """dates: [(date, free_slot_count)]"""
    buttons = [
        _button(f"{messages.fmt_date(day)} · {count} زمان", f"bt:{day.isoformat()}")
        for day, count in dates
    ]
    rows = _grid(buttons, 1)
    rows.append([_button("❌ انصراف", "home")])
    return InlineKeyboardMarkup(rows)


def time_list(slots: list, tz) -> InlineKeyboardMarkup:
    buttons = [_button(to_local(s.start, tz).strftime("%H:%M"), f"bs:{s.id}") for s in slots]
    rows = _grid(buttons, 4)
    rows.append([_button("⬅️ بازگشت", "bk"), _button("❌ انصراف", "home")])
    return InlineKeyboardMarkup(rows)


def booking_summary(slot_id: int, local_date: date) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_button("✅ تأیید و رزرو", f"bc:{slot_id}")],
        [_button("⬅️ بازگشت", f"bt:{local_date.isoformat()}"), _button("❌ انصراف", "home")],
    ])


def after_booking() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_button("🗂 نوبت‌های من", "my")],
        HOME_ROW,
    ])


def error_back_to_dates() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_button("📅 انتخاب زمان دیگر", "bk")],
        HOME_ROW,
    ])


def my_appointments(cancellable: list, tz) -> InlineKeyboardMarkup:
    rows = []
    for booking in cancellable:
        local = to_local(booking.start, tz)
        rows.append([_button(f"❌ لغو {local:%Y-%m-%d %H:%M}", f"mc:{booking.id}")])
    rows.append(HOME_ROW)
    return InlineKeyboardMarkup(rows)


def cancel_confirm(booking_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_button("✅ بله، لغو شود", f"mx:{booking_id}")],
        [_button("⬅️ بازگشت", "my"), _button("🏠 منوی اصلی", "home")],
    ])


def back_to_my() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_button("🗂 نوبت‌های من", "my")],
        HOME_ROW,
    ])


# ----------------------------------------------------------------- admin ---

def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_button("📅 نوبت‌های امروز", "a:td")],
        [_button("📋 نوبت‌های آینده", "a:up")],
        [_button("➕ افزودن زمان آزاد", "a:n")],
        [_button("🗑 حذف زمان آزاد", "a:r")],
        HOME_ROW,
    ])


def admin_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[_button("⬅️ پنل مدیریت", "a")]])


def admin_date_picker(days: list, prefix: str) -> InlineKeyboardMarkup:
    """prefix is the callback action, e.g. "a:nd" -> "a:nd:2026-11-03"."""
    buttons = [_button(messages.fmt_date(day), f"{prefix}:{day.isoformat()}") for day in days]
    rows = _grid(buttons, 2)
    rows.append([_button("⬅️ پنل مدیریت", "a")])
    return InlineKeyboardMarkup(rows)


def admin_time_picker(options: list, day: date, action: str, carry: str = "") -> InlineKeyboardMarkup:
    """options: list of datetime.time. Callback: a:<action>:<date>[:<carry>]:<HHMM>"""
    buttons = []
    for option in options:
        compact = format_hhmm_compact(option)
        parts = ["a", action, day.isoformat()]
        if carry:
            parts.append(carry)
        parts.append(compact)
        buttons.append(_button(f"{option:%H:%M}", ":".join(parts)))
    rows = _grid(buttons, 4)
    rows.append([_button("⬅️ روزها", "a:n"), _button("⬅️ پنل مدیریت", "a")])
    return InlineKeyboardMarkup(rows)


def admin_preview(day: date, start: time, end: time, can_save: bool) -> InlineKeyboardMarkup:
    rows = []
    if can_save:
        tail = f"{day.isoformat()}:{format_hhmm_compact(start)}:{format_hhmm_compact(end)}"
        rows.append([_button("💾 ذخیره", f"a:nc:{tail}")])
    rows.append([_button("⬅️ بازگشت", f"a:nd:{day.isoformat()}"), _button("❌ انصراف", "a")])
    return InlineKeyboardMarkup(rows)


def admin_after_save() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_button("➕ افزودن زمان دیگر", "a:n")],
        [_button("⬅️ پنل مدیریت", "a")],
    ])


def admin_slot_list(day: date, items: list, tz) -> InlineKeyboardMarkup:
    buttons = [
        _button(f"🗑 {to_local(item.slot.start, tz):%H:%M}", f"a:rs:{item.slot.id}")
        for item in items
        if item.booking is None
    ]
    rows = _grid(buttons, 4)
    rows.append([_button("⬅️ روزها", "a:r"), _button("⬅️ پنل مدیریت", "a")])
    return InlineKeyboardMarkup(rows)


def admin_confirm_remove(slot_id: int, day: date) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_button("✅ بله، حذف شود", f"a:rx:{slot_id}")],
        [_button("⬅️ بازگشت", f"a:rd:{day.isoformat()}"), _button("❌ انصراف", "a")],
    ])
