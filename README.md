# Slotly — Appointment Booking Bot for a Small Business

A compact Telegram bot that lets customers of **one independent service
provider** (for example a private tutor) book, view and cancel 30-minute
appointments, and lets the provider manage the schedule from Telegram.

> **Status: Stage 1 (core).** Booking, cancellation, slot management and tests
> are implemented. Reminders, admin cancellation, statistics, CSV export and the
> final documentation are Stage 2 and are **not** implemented yet — see
> [Roadmap](#roadmap).

The Telegram interface is in Persian. Code, comments and this README are in English.

## The business problem

A one-person service business usually books appointments through chat
messages. That causes double bookings, forgotten changes and a lot of
back-and-forth. Slotly gives customers a self-service menu and gives the
provider one source of truth for the schedule — with the single most important
rule enforced by the database: **a time slot can never be booked twice.**

## Implemented features (Stage 1)

Customer (private chat):
- `/start` main menu: book an appointment, my appointments, help.
- Booking flow: date → time → confirmation summary → confirm → booking reference.
- My appointments: upcoming bookings with date, time and status.
- Cancellation with confirmation and a clearly stated policy
  (configurable cutoff, default 2 hours before the appointment).
- Limit on upcoming bookings per customer (configurable, default 3).
- Back / Cancel buttons in every guided flow.

Admin (numeric Telegram IDs from configuration):
- `/admin` panel.
- Add free slots for a day from a start/end time range, with a **preview** before saving.
- Today's and upcoming appointments.
- Remove an unbooked slot (a booked slot cannot be removed).

Scheduling rules: UTC storage, business-timezone display, Gregorian dates
(labelled as such), fixed 30-minute slots, past/invalid/unaligned ranges
rejected, repeated generation is safe, daylight-saving gaps/overlaps rejected.

## Architecture

```
slotly/
  main.py             start-up, handler registration, log redaction
  config.py           environment configuration + validation
  database.py         schema, connections, write_transaction()
  booking_service.py  ALL booking rules (no Telegram code)  <- the core
  timeutil.py         UTC <-> local conversion, DST handling
  messages.py         every Persian text (HTML-escaped)
  keyboards.py        inline keyboards and callback-data format
  handlers/
    common.py         private-chat guard, safe message editing
    customer.py       customer buttons -> service calls
    admin.py          admin buttons -> service calls (+ admin_only)
tests/                pytest, temporary SQLite files, mocked Telegram
```

Handlers are thin: parse the button, call `BookingService`, show the result.
Business rules live only in `booking_service.py`, so they are tested without
Telegram. The service opens one short-lived SQLite connection per call, and
handlers talk to Telegram only **after** the service call (and its
transaction) has finished.

## Database model

```
slots     id, start_utc (UNIQUE), end_utc, created_at, removed_at
bookings  id, reference (UNIQUE), slot_id -> slots.id, customer_id,
          customer_name, status, created_at, cancelled_at
```

- `status` is `ACTIVE`, `CANCELLED_BY_CUSTOMER` or `CANCELLED_BY_ADMIN`.
  Cancelled rows stay as history.
- Slots are soft-deleted (`removed_at`), so booking history never points at a
  missing row. Foreign keys are enabled on every connection.
- Only the Telegram numeric ID and the display name are stored. No username,
  phone number or notes.

## How double booking is prevented

Two layers, both in the database/transaction, neither in Python memory:

1. **A unique partial index** allows at most one `ACTIVE` booking per slot:
   ```sql
   CREATE UNIQUE INDEX ux_bookings_one_active_per_slot
       ON bookings(slot_id) WHERE status = 'ACTIVE';
   ```
   Even a bug in the application code cannot insert a second active booking
   for the same slot. Cancelled rows are not covered by the index, so a
   cancelled slot can be booked again.
2. **One short `BEGIN IMMEDIATE` transaction** per booking: re-check the slot,
   check for an existing active booking, re-check "is it still in the future?",
   count the customer's upcoming bookings (the limit), then insert. `BEGIN
   IMMEDIATE` takes SQLite's write lock first, so two bookings never run these
   steps interleaved. The loser waits briefly, then sees the slot taken and
   gets: "This time was just booked. Please choose another time."

Related behavior: pressing "confirm" twice returns the same booking; a
cancellation query includes the requesting user's ID, so it can never cancel
someone else's booking; cancelling twice is handled gracefully.

## Setup on Windows (PowerShell, VS Code)

You need Python 3.11 or newer (`python --version`).

```powershell
# 1. Open the project folder in VS Code, then open a terminal (PowerShell)
cd path\to\slotly-bot

# 2. Create and activate a virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1
# If PowerShell refuses to run the script, run this once and try again:
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

# 3. Install dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt

# 4. Create your .env file and edit it
Copy-Item .env.example .env
code .env

# 5. Run the bot (leave this window open while the bot is in use)
python -m slotly.main

# 6. Run the tests (in a second terminal, venv activated)
python -m pytest
```

### Create the bot with BotFather

1. In Telegram, open **@BotFather** and send `/newbot`.
2. Choose a display name and a username ending in `bot`.
3. BotFather replies with a token (`123456789:AA...`). Put it in `.env` as
   `BOT_TOKEN=...`. **Never share it or commit it.** If it leaks, send
   `/revoke` to BotFather.
4. Find your numeric Telegram ID (for example with a bot such as
   @userinfobot) and put it in `.env` as `ADMIN_IDS=123456789`
   (comma-separated for several admins).

### Configuration (`.env`)

| Variable | Meaning | Default |
|---|---|---|
| `BOT_TOKEN` | Token from BotFather | required |
| `ADMIN_IDS` | Numeric admin Telegram IDs, comma-separated | required |
| `BUSINESS_TIMEZONE` | IANA timezone name | `Asia/Tehran` |
| `BUSINESS_NAME` | Name shown in the welcome message | `Slotly Demo` |
| `CANCELLATION_CUTOFF_MINUTES` | Cancel until N minutes before start | `120` |
| `MAX_ACTIVE_BOOKINGS` | Upcoming bookings per customer | `3` |
| `DATABASE_PATH` | SQLite file (relative to project folder) | `slotly.db` |

## Tests

```powershell
python -m pytest
```

Tests use temporary SQLite **files**, an injectable fake clock and mocked
Telegram calls; no token or internet is needed. They cover slot generation
(including daylight saving), duplicate generation, booking, past slots, two
independent connections racing for one slot, repeated confirmation, the
booking limit (also under concurrency), user isolation, admin authorization,
cancellation before/after the cutoff, rebooking a cancelled slot and refusing
to remove a booked slot. The handler tests need `python-telegram-bot` installed
and are skipped without it.

## Security and privacy notes

- Token only in the environment/`.env` (git-ignored). Log output redacts it.
- Admin access is decided only by numeric Telegram IDs from configuration —
  never by username — and checked in the handler **and** inside the service.
- Callback data is untrusted: IDs in it are parsed defensively, and ownership,
  permissions and current state are re-checked on every action.
- All SQL is parameterized. User-controlled text is HTML-escaped before it is
  sent to Telegram.
- Only private chats are served.
- Stored personal data: Telegram numeric ID and display name (needed to
  identify bookings and let the admin contact the customer).

## Daylight saving time

Slots are typed in the business timezone and stored in UTC. A start/end time
that does not exist (clocks jump forward) or that happens twice (clocks go
back) is **rejected** and the admin picks another time. A range that spans a
change produces slots by real elapsed time (one hour fewer/more slots on those
days). The default zone, `Asia/Tehran`, currently has no daylight saving time.

## Verification status (honest summary)

Verified by running them (details in the delivery notes): the booking core and
its tests against a temporary SQLite file. **Not verified by the author:**
installing and running the real `python-telegram-bot` package, a live run
against Telegram, and a run on Windows. Dependency version ranges in
`requirements.txt` are therefore unverified; if `pip install` reports a
conflict, tell your assistant the exact error message.

## Known limitations (Stage 1)

- One provider, one business, fixed 30-minute slots, private chats only.
- The Python process must be running for the bot to respond.
- The admin can only add/remove slots here; cancelling a booked appointment as
  admin, reminders, statistics and CSV export arrive in Stage 2.
- SQLite calls run directly in the bot's event loop. They take milliseconds at
  this scale, but this is not designed for heavy traffic.
- Keep the database on a normal local disk (not a network or synced folder).
- Dates are shown only in Gregorian format.

## Roadmap

Stage 2: persistent reminders (one repeating JobQueue check), explicit admin
cancellation with customer notification, statistics for a date range, CSV
export, additional tests, final README and demo script.
