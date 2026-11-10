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

## Public endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /api/reservations/pods` | Public list of bookable pod IDs, names, and addresses for app selection. |
| `GET /api/reservations/availability?pod_id=...&day=YYYY-MM-DD` | Start times and allowed durations for one London date. |
| `POST /api/reservations` | Creates a held booking and Stripe PaymentIntent. Returns the booking ID, client secret, amount, and hold expiry. |
| `GET /api/reservations/{id}/status` | Returns status and whether the code email was sent. Never returns the code. |
| `POST /webhook/stripe` | Existing signed Stripe webhook. `payment_intent.succeeded` events for reservations confirm a booking or refund a payment arriving after hold expiry. |

`POST /api/reservations` accepts `pod_id`, an ISO 8601 `start_at` with timezone,
`duration_minutes`, `customer_name`, `customer_email`, and optional international-format `customer_phone`.
The server enforces one-hour notice, a 21-day booking window, 15-minute starts and durations,
one to four hours, a 10 PM London finish, blocked dates, and £15/hour.
The browser cannot set the price or mark a booking paid.
Email retry is handled by the backend worker. Browser code has no delivery controls.

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
