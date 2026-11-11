# qwipsvc

FastAPI backend for Qwip pods. It contains two distinct booking flows:

| Flow | Payment | Access code | Data |
| --- | --- | --- | --- |
| Walk-up session | Card saved up front; usage charged when the session ends | Seam code or encrypted static code | `pod_sessions` |
| Scheduled reservation | Fixed £15/hour amount paid before the booking is confirmed | Code selected from `svc/random_static_codes.txt` and emailed | `pod_reservations` |

The scheduled reservation API is implemented in this repository, but its [database migration](migrations/20261109_reservation_api.sql), existing Stripe webhook subscription, schedulers, and Lovable integration must be configured during cutover. Repository code alone does not establish that the new flow is live. See the [reservation API guide](docs/reservation-api.md) for the full handoff contract.

**Stack:** Python 3.12 · FastAPI · Supabase/PostgreSQL · Stripe · Seam · Resend. Lovable's server function uses its existing Meta connector for reservation WhatsApp messages.

<details>
<summary>Walk-up session flow and API</summary>

1. `POST /api/setup-intent` creates a Stripe customer and SetupIntent. The browser collects the card with Stripe Elements; a provisioning JWT lasts 10 minutes.
2. `POST /api/booking/finalize` verifies the SetupIntent, creates a `pod_sessions` row, marks the pod in use, and starts access-code provisioning. It returns a session JWT lasting three hours. Retrying with the same SetupIntent returns the existing session.
3. The client polls provisioning status, then can read the session and access code with the session JWT. The code is also emailed through Resend.
4. The customer uploads a checkout photo before `POST /api/end-session`. The service calculates elapsed usage from the pod's per-minute rate, attempts an off-session GBP charge, ends the session, and starts code deprovisioning. Charges below 30p are skipped. Failed charges are recorded and emailed to management.

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| `POST` | `/api/setup-intent` | Public | Create payment setup and provisioning JWT. |
| `GET` | `/api/pod` | Public | Pod name, address, rate, and in-use status. |
| `POST` | `/api/booking/finalize` | Provisioning JWT | Start a walk-up session. |
| `GET` | `/api/provisioning-status` | Session JWT | Poll code readiness; returns the code when ready. |
| `GET` | `/api/get-session-data` | Session JWT | Session and pod details, including the access code. |
| `GET` | `/api/end-session-preview` | Session ID | Estimated current cost. |
| `GET` | `/api/is-session-complete` | Session ID | Whether the session has ended. |
| `GET` | `/api/lock-status` | Public | Seam lock status, or locked in static-code mode. |
| `POST` | `/api/session/upload-checkout-photo` | Session JWT | Upload required checkout photo. |
| `POST` | `/api/end-session` | Session JWT | End session and attempt final charge. |
| `POST` | `/webhook/stripe` | Stripe signature | Routes reservation payments; the walk-up event processor currently only logs other events. |

`GET /api/create-setup-intent` and `POST /api/confirm-booking` remain for the older flow and are deprecated.

`USE_STATIC_CODES=true` selects a random code from the static pool, encrypts it with AES-GCM, and stores the encrypted value on the session. With `USE_STATIC_CODES=false`, Seam provisions a time-bound code and deletes it when the session ends. These modes apply to walk-up sessions; scheduled reservations use the static pool.

</details>

<details>
<summary>Scheduled reservation flow and API</summary>

1. Availability covers today through 21 days ahead. The server enforces at least one hour's notice, starts on 15-minute boundaries, durations of 1–4 hours in 15-minute steps, a 10 PM London finish, blocked dates, and the £15/hour website price. Signed-in app reservations use a temporary 50p/hour live-payment test rate through separate endpoints.
2. Checkout creates a 10-minute database hold and a Stripe PaymentIntent for the server-calculated amount. A PostgreSQL exclusion constraint prevents overlapping held or paid reservations for the same pod.
3. The signed reservation Stripe webhook verifies the payment ID, amount, and GBP currency, then atomically marks the booking paid, assigns one code, and queues an email. A payment arriving after hold expiry is refunded.
4. A private worker sends the code by email. Once that email succeeds, the database queues the booking WhatsApp job. A reminder job is due five minutes before the end.
5. Lovable's **server function** claims WhatsApp jobs, sends through its Meta connector, and acknowledges success or failure. The booking template receives only first name and booking date/time; WhatsApp never receives the access code. Reminder sends have at most three attempts.

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/reservations/availability` | Public | Available starts and durations for a pod and London date. |
| `POST` | `/api/reservations` | Public | Create hold and payment; returns a Stripe client secret. |
| `GET` | `/api/app-reservations/availability` | Verified Supabase account | App availability priced at 50p/hour for live-payment testing. |
| `POST` | `/api/app-reservations` | Verified Supabase account | Create an app hold and payment at the app test rate. |
| `GET` | `/api/reservations/{id}/status` | Public | Booking status and whether the code email was sent; never the code. |
| `GET` | `/api/reservations/me` | Verified Supabase account | Active checkout holds and confirmed booking history; excludes expired holds. |
| `POST` | `/api/reservations/{id}/resume` | Verified Supabase account | Resume the owner's unexpired checkout with its existing Stripe PaymentIntent. |
| `POST` | `/webhook/stripe` | Stripe signature | Confirm a paid reservation from `payment_intent.succeeded`. |
| `POST` | `/internal/reservations/dispatch` | Worker bearer token | Expire holds and send due email jobs. |
| `POST` | `/internal/reservations/whatsapp/claim` | Lovable bearer token | Lease due WhatsApp jobs to the Lovable server function. |
| `POST` | `/internal/reservations/whatsapp/ack` | Lovable bearer token | Record success or failure and schedule retries. |
| `GET` | `/api/reservations/admin` | Admin bearer token | List reservations. |
| `POST` | `/api/reservations/admin/{id}/cancel` | Admin bearer token | Cancel booking and queued messages; does not refund Stripe or invalidate a static code. |
| `POST` | `/api/reservations/admin/{id}/code` | Admin bearer token | Change code and queue an updated email. |
| `POST` | `/api/reservations/admin/{id}/resend` | Admin bearer token | Queue confirmation email again. |

Private endpoints require server-held tokens and reject browser `Origin` requests. The delivery queue is not available to the browser. The [reservation API guide](docs/reservation-api.md) describes request bodies, job leases, retries, and cutover order.

</details>

<details>
<summary>Configuration and database</summary>

Copy [`.env.example`](.env.example) to `.env` for local development and replace placeholders. Keep secret values outside the repository.

| Setting | Purpose |
| --- | --- |
| `SUPABASE_URL`, `SUPABASE_KEY` | Database access; reservation functions require a service-role key. |
| `STRIPE_SECRET` | Server-side Stripe API. |
| `STRIPE_WEBHOOK_SECRET` | Signing secret for the shared `/webhook/stripe` endpoint. |
| `RESEND_API_KEY` | Access and reservation emails. |
| `JWT_SECRET_KEY` | Walk-up session and provisioning JWTs. |
| `SEAM_API_KEY` | Seam smart-lock access in dynamic-code mode. |
| `USE_STATIC_CODES`, `STATIC_CODE_B64_KEY` | Walk-up static-code mode and encryption key. |
| `RESERVATION_WORKER_TOKEN` | Email worker endpoint. |
| `RESERVATION_LOVABLE_TOKEN` | Lovable server-to-server WhatsApp claim/ack endpoints. |
| `RESERVATION_ADMIN_TOKEN` | Server-side reservation administration. |
| `RESERVATION_BLOCKED_DATES` | Optional comma-separated London dates in `YYYY-MM-DD` format. |
| `RESERVATION_DIRECTIONS`, `RESERVATION_MAPS_URL`, `RESERVATION_SUPPORT_URL` | Reservation email content. |
| `LOG_LEVEL` | Logging level; defaults to `INFO`. |

Walk-up tables include `pods`, `pod_sessions`, `session_provisionings`, and `invalid_payment_attempts`. Scheduled bookings use `pod_reservations` plus `reservation_delivery_jobs` created by the [migration](migrations/20261109_reservation_api.sql). Apply that migration before routing live reservation traffic to this API. It revokes direct browser roles from those tables and removes the previous database reminder trigger; switch the website and reminder dispatcher in the same cutover.

</details>

<details>
<summary>Local development, tests, and deployment</summary>

Use Python 3.12. From this directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill in the required settings in .env.
make service
```

`make service` starts Uvicorn with reload at `http://localhost:8000`. `make test` runs the test suite; `make format` runs Ruff, Black, isort, and pylint. The current unit suite uses mocks and does not verify live Stripe, Supabase, Resend, Seam, or Meta integration.

The [Procfile](Procfile) defines the Railway web command with `$PORT`. The existing Stripe destination for `/webhook/stripe` must subscribe to `payment_intent.succeeded` with a snapshot payload; no second destination is needed. Run the reservation email dispatcher once per minute, and schedule the Lovable server function to claim due WhatsApp jobs at least once per minute.

</details>
