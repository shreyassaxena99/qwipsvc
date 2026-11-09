"""Durable email worker for prepaid reservations."""

import html
import logging
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import resend

from svc.database_accessor import create_supabase_client

logger = logging.getLogger(__name__)
LONDON = ZoneInfo("Europe/London")


def _details(booking: dict) -> dict[str, str]:
    start = datetime.fromisoformat(booking["start_at"]).astimezone(LONDON)
    end = datetime.fromisoformat(booking["end_at"]).astimezone(LONDON)
    return {
        "name": str(booking["customer_name"]),
        "pod": str(booking["pod_label"]),
        "start": start.strftime("%a %d %b %Y, %I:%M %p"),
        "end": end.strftime("%I:%M %p"),
        "price": f"£{booking['amount_pence'] / 100:.2f}",
        "code": str(booking["access_code"]),
        "maps_url": os.getenv("RESERVATION_MAPS_URL", "https://maps.google.com/?q=Westfield+London"),
        "directions": os.getenv("RESERVATION_DIRECTIONS", "Westfield London"),
        "support_url": os.getenv("RESERVATION_SUPPORT_URL", "https://qwip.co.uk/support"),
    }


def send_reservation_email(booking: dict) -> None:
    if not os.getenv("RESEND_API_KEY"):
        raise RuntimeError("RESEND_API_KEY is not configured")
    resend.api_key = os.environ["RESEND_API_KEY"]
    values = {key: html.escape(value, quote=True) for key, value in _details(booking).items()}
    content = (
        f"<p>Hi {values['name']}, your booking at {values['pod']} is confirmed.</p>"
        f"<p>{values['start']}–{values['end']} · {values['price']}</p>"
        f"<p>Access code: <strong>{values['code']}</strong></p>"
        f"<p>Directions: {values['directions']}</p>"
        f"<p><a href=\"{values['maps_url']}\">Maps</a> · "
        f"<a href=\"{values['support_url']}\">Support</a></p>"
    )
    result = resend.Emails.send({"from": "hello@qwip.co.uk", "to": booking["customer_email"],
                                 "subject": "Your qwip pod booking is confirmed", "html": content})
    if not result.get("id"):
        raise RuntimeError("Email provider did not confirm delivery request")


def dispatch_reservation_deliveries(limit: int = 20) -> dict[str, int]:
    db = create_supabase_client()
    db.rpc("expire_pending_pod_reservations", {}).execute()
    jobs = db.rpc("claim_reservation_delivery_jobs", {"p_limit": limit}).execute().data or []
    sent = failed = 0
    for job in jobs:
        booking_rows = (db.table("pod_reservations").select("*")
                        .eq("id", job["reservation_id"]).limit(1).execute().data)
        booking = booking_rows[0] if booking_rows else None
        try:
            if not booking or booking["status"] != "paid":
                db.table("reservation_delivery_jobs").update({"status": "cancelled"}).eq("id", job["id"]).execute()
                continue
            send_reservation_email(booking)
            if not db.rpc("complete_reservation_email", {
                "p_job_id": job["id"], "p_lease_token": job["lease_token"],
            }).execute().data:
                raise RuntimeError("Email job could not be completed")
            sent += 1
        except Exception as exc:
            logger.exception("Reservation delivery failed for job %s", job["id"])
            delay = min(60, 2 ** min(job["attempts"], 6))
            exhausted = job["attempts"] >= 10
            db.table("reservation_delivery_jobs").update({
                "status": "failed" if exhausted else "pending", "leased_until": None,
                "due_at": (datetime.now(timezone.utc) + timedelta(minutes=delay)).isoformat(),
                "last_error": str(exc)[:500],
            }).eq("id", job["id"]).eq("lease_token", job["lease_token"]).execute()
            failed += 1
    return {"sent": sent, "failed": failed}
