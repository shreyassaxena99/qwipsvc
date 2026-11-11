"""Prepaid reservation API. Browser clients never receive provider credentials or door codes."""

import logging
import os
import secrets
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from postgrest.exceptions import APIError
from stripe import Event

from svc.database_accessor import create_supabase_client, get_pod_by_id
from svc.payments_manager import create_stripe_client
from svc.reservation_delivery import dispatch_reservation_deliveries
from svc.reservation_rules import APP_TEST_PRICE_PENCE_PER_HOUR, PRICE_PENCE_PER_HOUR, LONDON, reservation_window

logger = logging.getLogger(__name__)
router = APIRouter()
HOLD_MINUTES = 10


class ReservationRequest(BaseModel):
    pod_id: str = Field(min_length=1, max_length=100)
    start_at: datetime
    duration_minutes: int
    customer_name: str = Field(min_length=1, max_length=150)
    customer_email: EmailStr
    customer_phone: str | None = Field(default=None, max_length=30)


class RescheduleRequest(BaseModel):
    start_at: datetime


def _blocked(day: date) -> bool:
    # Server-side fallback until the website's blocked-date table is identified.
    return day.isoformat() in {item.strip() for item in os.getenv("RESERVATION_BLOCKED_DATES", "").split(",")}


def _booking(booking_id: UUID) -> dict:
    response = create_supabase_client().table("pod_reservations").select("*").eq("id", str(booking_id)).limit(1).execute()
    if not response.data:
        raise HTTPException(404, "Reservation not found")
    return response.data[0]


def _active_overlaps(pod_id: str, start: datetime, end: datetime) -> list[dict]:
    rows = (create_supabase_client().table("pod_reservations")
            .select("start_at,end_at,status,hold_expires_at")
            .eq("pod_id", pod_id).lt("start_at", end.isoformat())
            .gt("end_at", start.isoformat()).in_("status", ["pending", "paid"]).execute().data)
    now = datetime.now(timezone.utc)
    return [row for row in rows if row["status"] == "paid" or
            (row.get("hold_expires_at") and datetime.fromisoformat(row["hold_expires_at"]) > now)]


@router.get("/api/reservations/availability")
def availability(pod_id: str, day: date):
    return _availability(pod_id, day)


@router.get("/api/app-reservations/availability")
def app_availability(pod_id: str, day: date,
                     authorization: str | None = Header(default=None)):
    _verified_account_email(create_supabase_client(), authorization)
    return _availability(pod_id, day, APP_TEST_PRICE_PENCE_PER_HOUR)


def _availability(pod_id: str, day: date, price_pence_per_hour: int = PRICE_PENCE_PER_HOUR):
    get_pod_by_id(create_supabase_client(), pod_id)
    now = datetime.now(timezone.utc)
    local_today = now.astimezone(LONDON).date()
    if day < local_today or day > local_today + timedelta(days=21) or _blocked(day):
        return {"date": day, "slots": []}
    start_of_day = datetime.combine(day, time.min, LONDON).astimezone(timezone.utc)
    end_of_day = datetime.combine(day + timedelta(days=1), time.min, LONDON).astimezone(timezone.utc)
    overlaps = _active_overlaps(pod_id, start_of_day, end_of_day)
    slots = []
    for minutes in range(0, 22 * 60, 15):
        local_start = datetime.combine(day, time.min, LONDON) + timedelta(minutes=minutes)
        start = local_start.astimezone(timezone.utc)
        if start.astimezone(LONDON).date() != day or start.astimezone(LONDON).hour * 60 + start.astimezone(LONDON).minute != minutes:
            continue  # nonexistent local time during the DST change
        durations = []
        for duration in range(60, 241, 15):
            try:
                _, end, amount = reservation_window(start, duration, now,
                                                    price_pence_per_hour=price_pence_per_hour)
            except HTTPException:
                continue
            if any(start < datetime.fromisoformat(row["end_at"]) and
                   end > datetime.fromisoformat(row["start_at"]) for row in overlaps):
                continue
            durations.append({"minutes": duration, "amount_pence": amount})
        if durations:
            slots.append({"start_at": start.isoformat(), "durations": durations})
    return {"date": day, "slots": slots}


@router.get("/api/reservations/pods")
def reservable_pods():
    """Public catalogue for reservation clients; never expose lock or payment data."""
    rows = (create_supabase_client().table("pods")
            .select("id,name,address").order("name").execute().data)
    return {"pods": rows}


def _verified_account_email(db, authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Sign in to view your bookings")
    try:
        response = db.auth.get_user(authorization[7:])
        user = response.user if response else None
    except Exception as exc:
        raise HTTPException(401, "Your session has expired; please sign in again") from exc
    if not user or not user.email or not user.email_confirmed_at:
        raise HTTPException(401, "A verified email is required to view bookings")
    return user.email.lower()


@router.get("/api/reservations/me")
def my_reservations(authorization: str | None = Header(default=None)):
    """List active payment holds, confirmed bookings, and paid history without door codes."""
    db = create_supabase_client()
    email = _verified_account_email(db, authorization)
    rows = (db.table("pod_reservations")
            .select("id,pod_id,pod_label,start_at,end_at,duration_minutes,amount_pence,status,hold_expires_at,stripe_payment_intent_id,access_code,code_emailed_at")
            .eq("customer_email", email)
            .order("start_at", desc=True).execute().data)
    now = datetime.now(timezone.utc)
    bookings = []
    for row in rows:
        status = row["status"]
        active_hold = (status == "pending" and row.get("stripe_payment_intent_id") and
                       row.get("hold_expires_at") and
                       datetime.fromisoformat(row["hold_expires_at"]) > now)
        if not active_hold and status not in ("paid", "confirmed") and not (
            status in ("cancelled", "refunded") and row.get("access_code")
        ):
            continue
        bookings.append({
            "id": row["id"], "pod_id": row["pod_id"], "pod_label": row["pod_label"],
            "start_at": row["start_at"], "end_at": row["end_at"],
            "duration_minutes": row["duration_minutes"], "amount_pence": row["amount_pence"],
            "status": status, "hold_expires_at": row.get("hold_expires_at") if active_hold else None,
            "code_emailed": row.get("code_emailed_at") is not None,
        })
    return {"bookings": bookings}


@router.post("/api/reservations/{reservation_id}/resume")
def resume_reservation_payment(reservation_id: UUID,
                               authorization: str | None = Header(default=None)):
    """Return the existing checkout secret only to the verified booking owner."""
    db = create_supabase_client()
    email = _verified_account_email(db, authorization)
    rows = (db.table("pod_reservations")
            .select("customer_email,status,hold_expires_at,stripe_payment_intent_id,amount_pence")
            .eq("id", str(reservation_id)).limit(1).execute().data)
    if not rows or rows[0]["customer_email"].lower() != email:
        raise HTTPException(404, "Reservation not found")
    booking = rows[0]
    if (booking["status"] != "pending" or not booking.get("hold_expires_at") or
            datetime.fromisoformat(booking["hold_expires_at"]) <= datetime.now(timezone.utc)):
        raise HTTPException(409, "This payment hold has expired; please choose a time again")
    payment_id = booking.get("stripe_payment_intent_id")
    if not payment_id:
        raise HTTPException(409, "Payment is still being prepared; please try again")
    payment = create_stripe_client().payment_intents.retrieve(payment_id)
    if payment.status not in ("requires_payment_method", "requires_confirmation", "requires_action"):
        raise HTTPException(409, "Payment is already processing; please check the booking status")
    logger.info("Reservation checkout resumed reservation=%s payment_intent=%s", reservation_id, payment_id)
    return {"reservation_id": str(reservation_id), "client_secret": payment.client_secret,
            "amount_pence": booking["amount_pence"], "currency": "gbp",
            "hold_expires_at": booking["hold_expires_at"]}


@router.post("/api/reservations/{reservation_id}/reschedule")
def reschedule_reservation(reservation_id: UUID, body: RescheduleRequest,
                           authorization: str | None = Header(default=None)):
    """Move a paid booking at the same pod and duration, with no new charge."""
    db = create_supabase_client()
    email = _verified_account_email(db, authorization)
    rows = (db.table("pod_reservations")
            .select("id,customer_email,status,duration_minutes,start_at,end_at")
            .eq("id", str(reservation_id)).limit(1).execute().data)
    if not rows or rows[0]["customer_email"].lower() != email:
        raise HTTPException(404, "Reservation not found")
    booking = rows[0]
    if booking["status"] != "paid" or datetime.fromisoformat(booking["start_at"]) <= datetime.now(timezone.utc) + timedelta(hours=1):
        raise HTTPException(409, "This booking can no longer be changed")
    start, end, _ = reservation_window(body.start_at, booking["duration_minutes"])
    if _blocked(start.astimezone(LONDON).date()):
        raise HTTPException(422, "This date is unavailable")
    if start == datetime.fromisoformat(booking["start_at"]):
        return {"status": "unchanged", "reservation_id": str(reservation_id),
                "start_at": booking["start_at"], "end_at": booking["end_at"]}
    try:
        outcome = db.rpc("reschedule_paid_pod_reservation", {
            "p_reservation_id": str(reservation_id), "p_customer_email": email,
            "p_start_at": start.isoformat(), "p_end_at": end.isoformat(),
        }).execute().data
    except APIError as exc:
        if exc.code == "23P01":
            raise HTTPException(409, "This time is no longer available") from exc
        raise
    if outcome == "not_found":
        raise HTTPException(404, "Reservation not found")
    if outcome != "moved":
        raise HTTPException(409, "This booking can no longer be changed")
    return {"status": "rescheduled", "reservation_id": str(reservation_id),
            "start_at": start, "end_at": end}


@router.post("/api/reservations", status_code=201)
def create_reservation(body: ReservationRequest):
    return _create_reservation(body)


@router.post("/api/app-reservations", status_code=201)
def create_app_reservation(body: ReservationRequest,
                           authorization: str | None = Header(default=None)):
    db = create_supabase_client()
    email = _verified_account_email(db, authorization)
    if str(body.customer_email).lower() != email:
        raise HTTPException(403, "Reservation email must match your account")
    return _create_reservation(body, APP_TEST_PRICE_PENCE_PER_HOUR, db)


def _create_reservation(body: ReservationRequest, price_pence_per_hour: int = PRICE_PENCE_PER_HOUR,
                        db=None):
    start, end, amount = reservation_window(body.start_at, body.duration_minutes,
                                             price_pence_per_hour=price_pence_per_hour)
    if not body.customer_name.strip():
        raise HTTPException(422, "Customer name is required")
    if _blocked(start.astimezone(LONDON).date()):
        raise HTTPException(422, "This date is unavailable")
    if body.customer_phone and (not body.customer_phone.startswith("+") or not body.customer_phone[1:].isdigit()):
        raise HTTPException(422, "Phone number must be in international format")
    db = db or create_supabase_client()
    pod = get_pod_by_id(db, body.pod_id)
    try:
        booking = db.rpc("create_pod_reservation", {
            "p_pod_id": body.pod_id, "p_pod_label": pod["name"],
            "p_start_at": start.isoformat(), "p_end_at": end.isoformat(),
            "p_duration_minutes": body.duration_minutes, "p_amount_pence": amount,
            "p_customer_name": body.customer_name.strip(),
            "p_customer_email": str(body.customer_email).lower(),
            "p_customer_phone": body.customer_phone,
            "p_hold_expires_at": (datetime.now(timezone.utc) + timedelta(minutes=HOLD_MINUTES)).isoformat(),
        }).execute().data
        logger.info("Reservation hold created reservation=%s expires_at=%s", booking["id"], booking["hold_expires_at"])
    except APIError as exc:
        if exc.code == "23P01":
            raise HTTPException(409, "This time is no longer available") from exc
        raise
    try:
        payment = create_stripe_client().payment_intents.create(
            params={"amount": amount, "currency": "gbp", "receipt_email": str(body.customer_email),
                    "metadata": {"reservation_id": booking["id"], "purpose": "pod_reservation"}},
            options={"idempotency_key": f"reservation-{booking['id']}"},
        )
        attached = db.rpc("attach_reservation_payment", {
            "p_reservation_id": booking["id"], "p_payment_intent_id": payment.id,
        }).execute().data
        if not attached:
            logger.warning("Reservation payment attachment failed reservation=%s payment_intent=%s", booking["id"], payment.id)
            create_stripe_client().payment_intents.cancel(payment.id)
            raise HTTPException(409, "The checkout hold expired; please choose a time again")
        logger.info("Reservation payment ready reservation=%s payment_intent=%s", booking["id"], payment.id)
    except Exception:
        logger.exception("Reservation checkout setup failed reservation=%s", booking["id"])
        db.table("pod_reservations").update({"status": "cancelled"}).eq("id", booking["id"]).eq("status", "pending").execute()
        raise
    return {"reservation_id": booking["id"], "amount_pence": amount,
            "currency": "gbp", "hold_expires_at": booking["hold_expires_at"],
            "client_secret": payment.client_secret}


@router.get("/api/reservations/{reservation_id}/status")
def reservation_status(reservation_id: UUID):
    booking = _booking(reservation_id)
    status = booking["status"]
    if status == "pending" and booking.get("hold_expires_at") and datetime.fromisoformat(booking["hold_expires_at"]) <= datetime.now(timezone.utc):
        status = "expired"
    return {"status": status, "code_emailed": booking.get("code_emailed_at") is not None}


def _code_from_pool() -> str:
    path = Path(__file__).with_name("random_static_codes.txt")
    codes = [line.strip() for line in path.read_text().splitlines() if line.strip()]
    if not codes:
        raise RuntimeError("Reservation code pool is empty")
    return secrets.choice(codes)


def process_reservation_stripe_event(event: Event) -> bool:
    """Handle prepaid reservation payments after the shared webhook verifies Stripe's signature."""
    if event.type != "payment_intent.succeeded":
        return False
    payment = event.data.object
    if payment.metadata.get("purpose") != "pod_reservation":
        return False
    try:
        booking_id = UUID(payment.metadata["reservation_id"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, "Invalid reservation metadata") from exc
    booking = _booking(booking_id)
    if (booking["stripe_payment_intent_id"] != payment.id or
            booking["amount_pence"] != payment.amount_received or payment.currency.lower() != "gbp"):
        logger.error("Reservation payment mismatch for %s", booking_id)
        raise HTTPException(409, "Reservation payment mismatch")
    outcome = create_supabase_client().rpc("confirm_paid_reservation", {
        "p_reservation_id": str(booking_id), "p_payment_intent_id": payment.id,
        "p_access_code": _code_from_pool(),
    }).execute().data
    logger.info("Reservation payment webhook reservation=%s payment_intent=%s booking_status=%s outcome=%s",
                booking_id, payment.id, booking.get("status"), outcome)
    if outcome == "expired":
        create_stripe_client().refunds.create(
            params={"payment_intent": payment.id, "reason": "requested_by_customer"},
            options={"idempotency_key": f"expired-reservation-{booking_id}"},
        )
        create_supabase_client().table("pod_reservations").update({"status": "cancelled"}).eq("id", str(booking_id)).eq("status", "pending").execute()
        logger.warning("Refunded payment for expired reservation %s", booking_id)
        return True
    if outcome not in ("paid", "already_paid"):
        raise HTTPException(409, "Reservation could not be confirmed")
    return True


def _require_token(provided: str | None, setting: str) -> None:
    expected = os.getenv(setting)
    if not expected or not provided or not provided.startswith("Bearer ") or not secrets.compare_digest(provided[7:], expected):
        raise HTTPException(401, "Unauthorized")


def _require_private(request: Request, authorization: str | None, setting: str) -> None:
    # A browser always sends Origin for a cross-origin request. The shared token
    # must remain only in Lovable's server-side function configuration.
    if request.headers.get("origin"):
        raise HTTPException(403, "Server-to-server only")
    _require_token(authorization, setting)


def _require_lovable(request: Request, authorization: str | None) -> None:
    _require_private(request, authorization, "RESERVATION_LOVABLE_TOKEN")


class WhatsAppClaimRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=50)


def _whatsapp_job_payload(job: dict, booking: dict) -> dict:
    local_time = datetime.fromisoformat(
        booking["start_at"] if job["kind"] == "booking_whatsapp" else booking["end_at"]
    ).astimezone(LONDON)
    first_name = booking["customer_name"].strip().split()[0]
    date_time = local_time.strftime("%A %d %B %Y at %I:%M %p")
    return {
        "job_id": job["id"],
        "lease_token": job["lease_token"],
        "to": booking["customer_phone"],
        "template": "pod_booking_details" if job["kind"] == "booking_whatsapp" else "session_end_reminder",
        "parameters": [first_name, date_time],
    }


@router.post("/internal/reservations/whatsapp/claim")
def claim_whatsapp_jobs(body: WhatsAppClaimRequest, request: Request,
                        authorization: str | None = Header(default=None)):
    _require_lovable(request, authorization)
    db = create_supabase_client()
    jobs = db.rpc("claim_reservation_whatsapp_jobs", {"p_limit": body.limit}).execute().data or []
    payloads = []
    for job in jobs:
        rows = (db.table("pod_reservations")
                .select("status,customer_name,customer_phone,start_at,end_at")
                .eq("id", job["reservation_id"]).limit(1).execute().data)
        if rows and rows[0]["status"] == "paid" and rows[0]["customer_phone"]:
            payloads.append(_whatsapp_job_payload(job, rows[0]))
        else:
            db.rpc("ack_reservation_whatsapp_job", {
                "p_job_id": job["id"], "p_lease_token": job["lease_token"],
                "p_success": False, "p_error": "Reservation unavailable",
            }).execute()
    return {"jobs": payloads}


class WhatsAppAckRequest(BaseModel):
    job_id: UUID
    lease_token: UUID
    success: bool
    error: str | None = Field(default=None, max_length=500)


@router.post("/internal/reservations/whatsapp/ack")
def acknowledge_whatsapp_job(body: WhatsAppAckRequest, request: Request,
                             authorization: str | None = Header(default=None)):
    _require_lovable(request, authorization)
    outcome = create_supabase_client().rpc("ack_reservation_whatsapp_job", {
        "p_job_id": str(body.job_id), "p_lease_token": str(body.lease_token),
        "p_success": body.success, "p_error": body.error,
    }).execute().data
    if outcome == "invalid":
        raise HTTPException(409, "Job lease is invalid or expired")
    return {"status": outcome}


@router.post("/internal/reservations/dispatch")
def dispatch_deliveries(request: Request, authorization: str | None = Header(default=None)):
    _require_private(request, authorization, "RESERVATION_WORKER_TOKEN")
    return dispatch_reservation_deliveries()


@router.get("/api/reservations/admin")
def admin_list(request: Request, authorization: str | None = Header(default=None), limit: int = 100):
    _require_private(request, authorization, "RESERVATION_ADMIN_TOKEN")
    return (create_supabase_client().table("pod_reservations").select("*")
            .order("created_at", desc=True).limit(max(1, min(limit, 500))).execute().data)


@router.post("/api/reservations/admin/{reservation_id}/cancel")
def admin_cancel(reservation_id: UUID, request: Request, authorization: str | None = Header(default=None)):
    _require_private(request, authorization, "RESERVATION_ADMIN_TOKEN")
    booking = _booking(reservation_id)
    if booking["status"] != "cancelled":
        create_supabase_client().rpc("cancel_pod_reservation", {
            "p_reservation_id": str(reservation_id),
        }).execute()
    return {"status": "cancelled", "refund_issued": False}


class AdminCodeRequest(BaseModel):
    access_code: str


@router.post("/api/reservations/admin/{reservation_id}/code")
def admin_set_code(reservation_id: UUID, body: AdminCodeRequest, request: Request,
                   authorization: str | None = Header(default=None)):
    _require_private(request, authorization, "RESERVATION_ADMIN_TOKEN")
    codes = [line.strip() for line in Path(__file__).with_name("random_static_codes.txt").read_text().splitlines()]
    if body.access_code not in codes:
        raise HTTPException(422, "Code is not in the configured pool")
    booking = _booking(reservation_id)
    if booking["status"] != "paid":
        raise HTTPException(409, "Only paid reservations can have a code")
    create_supabase_client().table("pod_reservations").update({"access_code": body.access_code, "code_emailed_at": None}).eq("id", str(reservation_id)).execute()
    create_supabase_client().table("reservation_delivery_jobs").update({"status": "pending", "due_at": datetime.now(timezone.utc).isoformat()}).eq("reservation_id", str(reservation_id)).eq("kind", "email").execute()
    return {"status": "queued"}


@router.post("/api/reservations/admin/{reservation_id}/resend")
def admin_resend(reservation_id: UUID, request: Request, authorization: str | None = Header(default=None)):
    _require_private(request, authorization, "RESERVATION_ADMIN_TOKEN")
    booking = _booking(reservation_id)
    if booking["status"] != "paid" or not booking.get("access_code"):
        raise HTTPException(409, "Only paid reservations with codes can be resent")
    create_supabase_client().table("reservation_delivery_jobs").update({"status": "pending", "due_at": datetime.now(timezone.utc).isoformat(), "attempts": 0}).eq("reservation_id", str(reservation_id)).eq("kind", "email").execute()
    return {"status": "queued"}
