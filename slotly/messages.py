"""All Persian text shown to users, in one place.

Rules:
* Every user-controlled value (names, etc.) goes through `esc()` because
  messages are sent with Telegram's HTML parse mode.
* Dates are Gregorian ("میلادی") and shown in the business timezone.
* "\\u200c" is the Persian half-space (ZWNJ) used inside compound words.
"""

from __future__ import annotations

import html
from datetime import date, datetime
from zoneinfo import ZoneInfo

from slotly import booking_service as bs
from slotly.config import SLOT_MINUTES, Settings
from slotly.timeutil import to_local

# Python weekday(): Monday=0 ... Sunday=6
WEEKDAYS_FA = (
    "دوشنبه",
    "سه‌شنبه",
    "چهارشنبه",
    "پنج‌شنبه",
    "جمعه",
    "شنبه",
    "یکشنبه",
)

STATUS_FA = {
    bs.STATUS_ACTIVE: "✅ تأیید شده",
    bs.STATUS_CANCELLED_BY_CUSTOMER: "❌ لغو شده توسط مشتری",
    bs.STATUS_CANCELLED_BY_ADMIN: "❌ لغو شده توسط مدیر",
}

NOT_ADMIN = "⛔ شما دسترسی مدیریتی ندارید."
INVALID_REQUEST = "این درخواست معتبر نیست یا منقضی شده است. لطفاً از منوی اصلی دوباره شروع کنید."
BUSY = "سیستم همین حالا مشغول است. لطفاً چند لحظه بعد دوباره تلاش کنید."
UNEXPECTED_ERROR = "متأسفانه مشکلی پیش آمد. لطفاً دوباره تلاش کنید."
PRIVATE_ONLY = "این ربات فقط در گفتگوی خصوصی کار می‌کند."


def esc(value: object) -> str:
    """Escape text for Telegram HTML. Always use it for user-controlled values."""
    return html.escape(str(value), quote=False)


# ------------------------------------------------------------ formatting ---

def fmt_date(day: date) -> str:
    return f"{day.isoformat()} ({WEEKDAYS_FA[day.weekday()]})"


def fmt_time(moment: datetime, tz: ZoneInfo) -> str:
    return to_local(moment, tz).strftime("%H:%M")


def fmt_range(start: datetime, end: datetime, tz: ZoneInfo) -> str:
    return f"{fmt_time(start, tz)} تا {fmt_time(end, tz)}"


def fmt_datetime(moment: datetime, tz: ZoneInfo) -> str:
    local = to_local(moment, tz)
    return f"{fmt_date(local.date())} ساعت {local:%H:%M}"


def fmt_duration(minutes: int) -> str:
    if minutes % 60 == 0 and minutes > 0:
        return f"{minutes // 60} ساعت"
    return f"{minutes} دقیقه"


def calendar_note(settings: Settings) -> str:
    return (
        "📌 تاریخ‌ها <b>میلادی</b> (Gregorian) هستند و بر اساس منطقه زمانی "
        f"<code>{esc(settings.timezone_name)}</code> نمایش داده می‌شوند."
    )


def cancellation_policy(settings: Settings) -> str:
    cutoff = settings.cancellation_cutoff_minutes
    if cutoff == 0:
        return "لغو نوبت تا لحظهٔ شروع آن ممکن است."
    return (
        f"لغو نوبت تا {fmt_duration(cutoff)} قبل از شروع آن ممکن است. "
        "پس از این مهلت، لغو از طریق ربات امکان‌پذیر نیست و باید مستقیم با ارائه‌دهنده هماهنگ کنید."
    )


# ----------------------------------------------------------- customer text ---

def welcome(name: str, settings: Settings) -> str:
    return (
        f"سلام {esc(name)} 👋\n"
        f"به <b>{esc(settings.business_name)}</b> خوش آمدید.\n\n"
        "از اینجا می‌توانید نوبت رزرو کنید، نوبت‌های خود را ببینید یا لغو کنید.\n"
        "یکی از گزینه‌های زیر را انتخاب کنید:"
    )


def help_text(settings: Settings) -> str:
    return (
        "ℹ️ <b>راهنما</b>\n\n"
        f"📅 <b>رزرو نوبت:</b> ابتدا تاریخ، سپس ساعت آزاد را انتخاب و رزرو را تأیید کنید. "
        f"هر نوبت {SLOT_MINUTES} دقیقه است.\n"
        "🗂 <b>نوبت‌های من:</b> نوبت‌های آیندهٔ شما را نشان می‌دهد و می‌توانید آن‌ها را لغو کنید.\n\n"
        f"❌ <b>سیاست لغو:</b> {cancellation_policy(settings)}\n"
        f"🔢 هر نفر حداکثر {settings.max_active_bookings} نوبت آینده می‌تواند داشته باشد.\n\n"
        f"{calendar_note(settings)}"
    )


def no_free_slots() -> str:
    return "در حال حاضر زمان آزادی وجود ندارد. لطفاً بعداً دوباره سر بزنید."


def choose_date(settings: Settings) -> str:
    return f"📅 <b>رزرو نوبت</b>\nتاریخ مورد نظر را انتخاب کنید:\n\n{calendar_note(settings)}"


def choose_time(day: date) -> str:
    return f"📅 <b>{fmt_date(day)}</b> (میلادی)\nساعت مورد نظر را انتخاب کنید:"


def no_times_on_date(day: date) -> str:
    return f"برای {fmt_date(day)} دیگر زمان آزادی وجود ندارد. تاریخ دیگری را انتخاب کنید."


def booking_summary(slot: bs.Slot, settings: Settings) -> str:
    tz = settings.timezone
    return (
        "📋 <b>خلاصهٔ نوبت</b>\n\n"
        f"📆 تاریخ: {fmt_date(to_local(slot.start, tz).date())} (میلادی)\n"
        f"🕒 ساعت: {fmt_range(slot.start, slot.end, tz)}\n"
        f"🌍 منطقه زمانی: <code>{esc(settings.timezone_name)}</code>\n\n"
        f"❌ {cancellation_policy(settings)}\n\n"
        "برای ثبت نهایی روی «تأیید و رزرو» بزنید."
    )


def booking_done(result: bs.BookingResult, settings: Settings) -> str:
    booking = result.booking
    tz = settings.timezone
    headline = "✅ <b>نوبت شما ثبت شد.</b>" if result.created else "✅ <b>این نوبت قبلاً برای شما ثبت شده است.</b>"
    return (
        f"{headline}\n\n"
        f"📆 {fmt_datetime(booking.start, tz)} (میلادی)\n"
        f"🕒 {fmt_range(booking.start, booking.end, tz)}\n"
        f"🔖 کد پیگیری: <code>{esc(booking.reference)}</code>\n\n"
        f"❌ {cancellation_policy(settings)}"
    )


def my_appointments(bookings: list, service: bs.BookingService) -> str:
    settings = service.settings
    tz = settings.timezone
    if not bookings:
        return "🗂 <b>نوبت‌های من</b>\n\nشما نوبت آینده‌ای ندارید."
    now = service.now()
    lines = ["🗂 <b>نوبت‌های آیندهٔ شما</b>", ""]
    for booking in bookings:
        deadline = service.cancellation_deadline(booking.start)
        cancel_note = (
            f"قابل لغو تا {fmt_datetime(deadline, tz)}" if now <= deadline else "⛔ مهلت لغو گذشته است"
        )
        lines.append(
            f"📆 {fmt_datetime(booking.start, tz)}\n"
            f"   وضعیت: {STATUS_FA[booking.status]}\n"
            f"   🔖 <code>{esc(booking.reference)}</code>\n"
            f"   {cancel_note}"
        )
        lines.append("")
    lines.append(f"❌ {cancellation_policy(settings)}")
    lines.append("")
    lines.append(calendar_note(settings))
    return "\n".join(lines)


def cancel_confirmation(booking: bs.Booking, settings: Settings) -> str:
    return (
        "❓ <b>آیا مطمئن هستید که می‌خواهید این نوبت را لغو کنید؟</b>\n\n"
        f"📆 {fmt_datetime(booking.start, settings.timezone)} (میلادی)\n"
        f"🔖 <code>{esc(booking.reference)}</code>\n\n"
        f"{cancellation_policy(settings)}"
    )


def cancel_done(booking: bs.Booking, settings: Settings, already: bool) -> str:
    headline = "این نوبت قبلاً لغو شده بود." if already else "✅ نوبت شما لغو شد."
    return (
        f"{headline}\n\n"
        f"📆 {fmt_datetime(booking.start, settings.timezone)} (میلادی)\n"
        f"🔖 <code>{esc(booking.reference)}</code>"
    )


def customer_error(exc: Exception, settings: Settings) -> str:
    """Friendly Persian text for an expected booking failure."""
    if isinstance(exc, bs.SlotTaken):
        return "این زمان همین حالا رزرو شد. لطفاً زمان دیگری را انتخاب کنید."
    if isinstance(exc, (bs.SlotInPast, bs.SlotNotFound)):
        return "این زمان دیگر در دسترس نیست. لطفاً زمان دیگری را انتخاب کنید."
    if isinstance(exc, bs.BookingLimitReached):
        return (
            f"شما همین حالا {exc.limit} نوبت آینده دارید که حداکثر تعداد مجاز است. "
            "برای رزرو نوبت جدید، ابتدا یکی از نوبت‌های خود را لغو کنید."
        )
    if isinstance(exc, bs.CancellationTooLate):
        return (
            "⛔ مهلت لغو این نوبت گذشته است.\n"
            f"{cancellation_policy(settings)}"
        )
    if isinstance(exc, bs.BookingNotFound):
        return "این نوبت پیدا نشد."
    if isinstance(exc, bs.DatabaseBusy):
        return BUSY
    return UNEXPECTED_ERROR


# ------------------------------------------------------------- admin text ---

ADMIN_MENU = "🛠 <b>پنل مدیریت</b>\nیکی از گزینه‌ها را انتخاب کنید:"


def admin_pick_date(title: str, settings: Settings) -> str:
    return f"{title}\nروز مورد نظر را انتخاب کنید:\n\n{calendar_note(settings)}"


def admin_pick_start(day: date) -> str:
    return f"➕ <b>افزودن زمان آزاد</b>\n📆 {fmt_date(day)}\n\nساعت <b>شروع</b> را انتخاب کنید:"


def admin_pick_end(day: date, start_label: str) -> str:
    return (
        f"➕ <b>افزودن زمان آزاد</b>\n📆 {fmt_date(day)}\n🕒 شروع: {start_label}\n\n"
        "ساعت <b>پایان</b> را انتخاب کنید:"
    )


def admin_no_start_options(day: date) -> str:
    return f"برای {fmt_date(day)} ساعت قابل انتخابی باقی نمانده است. روز دیگری را انتخاب کنید."


def admin_preview(preview: bs.SlotPreview, start_label: str, end_label: str, settings: Settings) -> str:
    tz = settings.timezone
    times = " · ".join(fmt_time(s, tz) for s in preview.starts)
    return (
        "👀 <b>پیش‌نمایش</b>\n\n"
        f"📆 {fmt_date(preview.local_date)} (میلادی)\n"
        f"🕒 بازه: {start_label} تا {end_label}\n"
        f"🔢 تعداد: {preview.total} نوبت {SLOT_MINUTES} دقیقه‌ای\n"
        f"✨ جدید: {preview.new_count}   ♻️ از قبل موجود: {preview.already_existing}\n\n"
        f"{times}\n\n"
        "هنوز چیزی ذخیره نشده است. برای ذخیره روی «ذخیره» بزنید."
    )


def admin_slots_saved(result: bs.SlotCreationResult) -> str:
    return (
        "✅ <b>ذخیره شد.</b>\n"
        f"✨ نوبت جدید: {result.created}\n"
        f"♻️ از قبل موجود: {result.already_existing}"
    )


def invalid_range(exc: bs.InvalidTimeRange) -> str:
    texts = {
        "not_aligned": f"ساعت‌ها باید مضرب {SLOT_MINUTES} دقیقه باشند (مثل 09:00 یا 09:30).",
        "end_not_after_start": "ساعت پایان باید بعد از ساعت شروع باشد.",
        "in_past": "ساعت شروع باید در آینده باشد. این بازه گذشته است یا همین حالا شروع شده است.",
        "nonexistent_local_time": (
            "این ساعت به‌خاطر تغییر ساعت تابستانی در آن روز وجود ندارد. ساعت دیگری انتخاب کنید."
        ),
        "ambiguous_local_time": (
            "این ساعت به‌خاطر تغییر ساعت تابستانی در آن روز دو بار تکرار می‌شود و مبهم است. "
            "ساعت دیگری انتخاب کنید."
        ),
    }
    return "⚠️ " + texts.get(exc.code, "بازهٔ زمانی معتبر نیست.")


def _customer_link(booking: bs.Booking) -> str:
    # tg://user?id=... opens the customer's profile for the admin.
    return f'<a href="tg://user?id={int(booking.customer_id)}">{esc(booking.customer_name)}</a>'


def admin_appointments(title: str, bookings: list, settings: Settings, limit: int) -> str:
    tz = settings.timezone
    if not bookings:
        return f"{title}\n\nنوبتی ثبت نشده است."
    lines = [title, ""]
    for booking in bookings:
        lines.append(
            f"🕒 {fmt_datetime(booking.start, tz)}\n"
            f"   👤 {_customer_link(booking)}   🔖 <code>{esc(booking.reference)}</code>"
        )
    if len(bookings) >= limit:
        lines.append("")
        lines.append(f"فقط {limit} مورد اول نمایش داده شد.")
    lines.append("")
    lines.append(calendar_note(settings))
    return "\n".join(lines)


def admin_slot_list(day: date, items: list, settings: Settings) -> str:
    tz = settings.timezone
    if not items:
        return f"🗑 <b>حذف زمان آزاد</b>\n📆 {fmt_date(day)}\n\nزمان بازی برای این روز وجود ندارد."
    lines = ["🗑 <b>حذف زمان آزاد</b>", f"📆 {fmt_date(day)} (میلادی)", ""]
    for item in items:
        label = fmt_time(item.slot.start, tz)
        if item.booking is None:
            lines.append(f"🟢 {label} — آزاد")
        else:
            lines.append(
                f"🔒 {label} — رزرو شده ({esc(item.booking.customer_name)}، "
                f"<code>{esc(item.booking.reference)}</code>)"
            )
    lines.append("")
    lines.append("برای حذف یک زمان آزاد، روی آن بزنید. زمان‌های رزرو شده با حذف زمان از بین نمی‌روند؛ "
                 "ابتدا باید نوبت صریحاً لغو شود.")
    return "\n".join(lines)


def admin_confirm_remove(slot: bs.Slot, settings: Settings) -> str:
    return (
        "❓ <b>این زمان آزاد حذف شود؟</b>\n\n"
        f"📆 {fmt_datetime(slot.start, settings.timezone)} (میلادی)"
    )


def admin_slot_removed(slot: bs.Slot, settings: Settings) -> str:
    return f"✅ زمان {fmt_datetime(slot.start, settings.timezone)} حذف شد."


def admin_error(exc: Exception, settings: Settings) -> str:
    if isinstance(exc, bs.NotAuthorized):
        return NOT_ADMIN
    if isinstance(exc, bs.InvalidTimeRange):
        return invalid_range(exc)
    if isinstance(exc, bs.SlotHasActiveBooking):
        return (
            "⛔ این زمان رزرو فعال دارد و حذف نمی‌شود. "
            "ابتدا باید نوبت به‌صورت صریح لغو شود."
        )
    if isinstance(exc, bs.SlotNotFound):
        return "این زمان پیدا نشد یا قبلاً حذف شده است."
    if isinstance(exc, bs.DatabaseBusy):
        return BUSY
    return UNEXPECTED_ERROR
