# Prepaid pod reservations

The reservation API lives in this service alongside the separate walk-up session API.
The website should use only the public endpoints for prepaid bookings and Stripe.js for card collection.
Its Lovable **server function** keeps the existing Meta connector and calls only the private WhatsApp claim and acknowledgement endpoints. Browser code must not call Supabase reservation tables, Resend, Meta, or Stripe secret-key APIs directly.

## Database cutover

Apply `migrations/20261109_reservation_api.sql` during the website cutover. It:

- Cancels existing abandoned `pending` rows without a hold expiry.
- Adds 10-minute checkout holds and a database exclusion constraint that prevents overlapping active reservations for the same pod.
- Creates atomic Supabase functions for hold creation, payment attachment, confirmation, expiration, and delivery claims.
- Adds a delivery outbox for email, booking WhatsApp, and the five-minute end reminder. The booking WhatsApp job is queued only after the email job succeeds.
- Removes the old reminder trigger to prevent duplicate reminders. Disable the old reminder dispatcher and account for any reminders it already queued during cutover.
- Revokes direct `anon` and `authenticated` access to reservation data. `SUPABASE_KEY` for this service must be a service-role key.

Resolve any existing overlapping **paid** reservations before applying the exclusion constraint.
Back up the table first. The migration changes live security and messaging behavior, so switch the website to the API in the same release.

## Reservation endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /api/reservations/pods` | Public list of bookable pod IDs, names, and addresses; excludes the Test Pod. |
| `GET /api/reservations/availability?pod_id=...&day=YYYY-MM-DD` | Start times and allowed durations for one London date. |
| `POST /api/reservations` | Creates a held booking and Stripe PaymentIntent. Returns the booking ID, client secret, amount, and hold expiry. |
| `GET /api/app-reservations/pods` | Requires a verified Supabase account. Includes the Test Pod for app testing. |
| `GET /api/app-reservations/availability?pod_id=...&day=YYYY-MM-DD` | Requires a verified Supabase account. Slots use the selected pod's price. |
| `POST /api/app-reservations` | Requires a verified Supabase account and matching booking email. Creates an app hold and PaymentIntent at the selected pod's price. |
| `GET /api/reservations/{id}/status` | Returns status and whether the code email was sent. Never returns the code. |
| `GET /api/reservations/me` | Requires a Supabase access token. Lists active payment holds and confirmed booking history for the verified account email without access codes. |
| `POST /api/reservations/{id}/resume` | Requires a Supabase access token. Returns the existing PaymentIntent client secret only to the owner while the hold is active. |
| `POST /api/reservations/{id}/reschedule` | Requires a Supabase access token. Moves the account's paid booking to another time at the same pod and duration. |
| `POST /api/reservations/{id}/cancel` | Requires a Supabase access token. Cancels the account's paid booking until one hour before start and requests a full Stripe refund. |
| `POST /webhook/stripe` | Existing signed Stripe webhook. `payment_intent.succeeded` events for reservations confirm a booking or refund a payment arriving after hold expiry. |

`POST /api/reservations` accepts `pod_id`, an ISO 8601 `start_at` with timezone,
`duration_minutes`, `customer_name`, `customer_email`, and optional international-format `customer_phone`.
The server enforces one-hour notice, a 21-day booking window, 15-minute starts and durations,
one to four hours, a 10 PM London finish, and blocked dates. Westfield costs
£15/hour through both website and app endpoints. The Test Pod
(`25993183-3c63-420b-a8cd-be3ce1283226`) costs 50p/hour through the
authenticated app endpoints. Public catalogue, availability, and checkout
endpoints do not expose or accept the Test Pod.
The browser cannot set the price or mark a booking paid.
Email retry is handled by the backend worker. Browser code has no delivery controls.

`GET /api/reservations/me` returns active checkout holds, confirmed bookings, and
paid booking history for the verified account email. Expired or abandoned holds
are hidden. An active hold can be completed through `POST /api/reservations/{id}/resume`;
the original 10-minute deadline still applies. The resume endpoint returns a
client secret only after checking the verified account email and Stripe payment
state. Access codes remain server-side.

## Rescheduling

Apply `migrations/20261110_reschedule_paid_reservations.sql` **before** deploying the
reschedule endpoint. It adds a `rescheduled_at` timestamp and an atomic database function;
it does not create another booking table. The existing overlap constraint rejects a time
that has become unavailable.

Send `POST /api/reservations/{id}/reschedule` with
`Authorization: Bearer <Supabase access token>` and JSON body
`{"start_at":"2026-11-12T12:00:00+00:00"}`. The token must belong to a verified account
whose email matches the booking. Only paid bookings more than one hour before their
original start can move. The new start follows the ordinary booking rules, including
one-hour notice, 15-minute steps, the 21-day window, blocked dates, and a 10 PM London
finish. The pod, duration, amount, payment, and access code remain unchanged.
The endpoint returns `rescheduled` with the new times, `unchanged` if the time was the
same, 401 for missing/invalid authentication, 404 for another account's booking, 409
for an ineligible booking or occupied slot, and 422 for an invalid requested time.

Rescheduling requeues the confirmation email with the new time and updates the end
reminder's due time. Any booking WhatsApp still queued for the old time is cancelled;
an already sent WhatsApp cannot be retracted. The updated email is the authoritative
notice. Cancellation and refunds are separate and are not exposed by this endpoint.

## Customer cancellation and refund

Apply `migrations/20261111_customer_reservation_cancellation.sql` before deploying
this endpoint. It records the customer cancellation request and Stripe refund state.
The database function checks the verified account email, paid status, Stripe payment
reference, and one-hour cutoff while locking the booking row. It also cancels queued
delivery jobs and releases the slot immediately.

Send `POST /api/reservations/{id}/cancel` with the account's Supabase bearer token.
For an eligible booking, the server creates a full refund against the original Stripe
PaymentIntent and returns `status`, `refund_status`, and `refund_issued`. Refund
settlement can take longer than the API response. A repeated call reuses the same
refund, including after a lost database response. If Stripe is unavailable, the
booking remains cancelled; the app offers a retry from My Bookings. A failed Stripe
refund needs support review. `GET /api/reservations/me` includes the cancellation
request timestamp and last recorded refund status so the app can show that state.

The website's booking and pricing endpoints are unchanged. This cancellation endpoint
is also available to a verified website account using the same reservation email.
Cancelling a booking does not revoke a static physical access code already sent.

## Pod occupancy

Apply `migrations/20261111_sync_pod_occupancy.sql` before deploying backend code
that updates walk-up occupancy. The migration adds `pods.walkup_in_use`, preserves
the existing walk-up flag, and makes `pods.in_use` the combined state of walk-up
occupancy and a paid reservation whose time window currently contains `now()`.
It runs one reconciliation immediately and schedules the same database function
every minute using Supabase Cron. The walk-up backend calls a separate database
function so ending a walk-up session cannot clear an active reservation's flag.
Old `pod_sessions` rows with no end time are deliberately not treated as live
occupancy; the existing walk-up flag remains the source for those sessions.

## Private endpoints

`POST /internal/reservations/dispatch` requires `Authorization: Bearer <RESERVATION_WORKER_TOKEN>`.
Call it once per minute from a trusted scheduler. It expires abandoned holds and sends due
email jobs. Email failures are retried with backoff, up to ten attempts.

The Lovable server function uses `Authorization: Bearer <RESERVATION_LOVABLE_TOKEN>` on:

| Endpoint | Purpose |
| --- | --- |
| `POST /internal/reservations/whatsapp/claim` | Claims up to `limit` due jobs. Each job has a lease token, recipient phone, approved template name, and exactly two template parameters. |
| `POST /internal/reservations/whatsapp/ack` | Reports `job_id`, `lease_token`, `success`, and optional `error`. The backend updates delivery state and schedules a retry when appropriate. |

Call claim from a Lovable server-side scheduler at least once per minute. For each job,
send through the existing Meta connector, then acknowledge success or failure. Keep the
shared token in Lovable server secrets. These routes reject browser `Origin` requests and
never return a booking code. `pod_booking_details` receives only the customer's first name
and booking start date/time. `session_end_reminder` receives first name and booking end
date/time. The backend schedules reminders five minutes before the end, allows at most
three total send attempts, and cancels queued jobs when the booking is cancelled.

Example claim request: `POST /internal/reservations/whatsapp/claim` with
`{"limit": 10}`. A returned job has `job_id`, `lease_token`, `to`, `template`, and
`parameters`. For `pod_booking_details`, `parameters` is exactly
`["Ada", "Tuesday 10 November 2026 at 12:00 PM"]`. After Meta returns, call
`POST /internal/reservations/whatsapp/ack` with
`{"job_id":"...","lease_token":"...","success":true}` or set `success` to
`false` and include an `error`. The acknowledgement returns `sent`, `pending`,
`failed`, or `cancelled`. A stale lease returns HTTP 409.

Claims have a two-minute lease. If the server function crashes after Meta accepts a message
but before acknowledgement, that message may be retried, so the integration is at-least-once.

`/api/reservations/admin` routes require `Authorization: Bearer <RESERVATION_ADMIN_TOKEN>`:
call these through a trusted server function to list, cancel, set code, or resend confirmation email.
Browser requests are rejected. Cancelling does **not** issue a refund
or disable a static physical code. Handle refunds separately in Stripe.

## Configuration

Required: `SUPABASE_URL`, service-role `SUPABASE_KEY`, `STRIPE_SECRET`,
`STRIPE_WEBHOOK_SECRET`, `RESEND_API_KEY`, `RESERVATION_WORKER_TOKEN`,
`RESERVATION_LOVABLE_TOKEN`, and `RESERVATION_ADMIN_TOKEN`. Keep the existing
`/webhook/stripe` destination and ensure it subscribes to `payment_intent.succeeded`
with a snapshot payload. The same Stripe API key and endpoint signing secret serve both
flows; the handler routes reservation payments by their `purpose=pod_reservation` metadata.

Meta credentials and template configuration stay in the Lovable server function. The
backend determines template names and parameters and records delivery outcomes. If Meta
rejects a message, the paid booking remains valid and the backend retry policy applies.

`RESERVATION_BLOCKED_DATES` is a comma-separated set of `YYYY-MM-DD` London dates.
`RESERVATION_DIRECTIONS`, `RESERVATION_MAPS_URL`, and `RESERVATION_SUPPORT_URL` customize
the confirmation email. Set them to the actual Westfield pod details before cutover.

The access-code pool is `svc/random_static_codes.txt`. Codes are selected only after a
verified payment and assigned once in the same database transaction that marks a booking paid.
