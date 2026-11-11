"""Server-owned rules for prepaid pod reservations."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException

LONDON = ZoneInfo("Europe/London")
PRICE_PENCE_PER_HOUR = 1500
TEST_POD_ID = "25993183-3c63-420b-a8cd-be3ce1283226"
TEST_POD_PRICE_PENCE_PER_HOUR = 50
MAX_DAYS_AHEAD = 21


def price_for_pod(pod_id: str) -> int:
    return TEST_POD_PRICE_PENCE_PER_HOUR if pod_id == TEST_POD_ID else PRICE_PENCE_PER_HOUR


def reservation_window(start_at: datetime, duration_minutes: int, now: datetime | None = None,
                       price_pence_per_hour: int = PRICE_PENCE_PER_HOUR) -> tuple[datetime, datetime, int]:
    if start_at.tzinfo is None:
        raise HTTPException(422, "start_at must include a timezone")
    if duration_minutes < 60 or duration_minutes > 240 or duration_minutes % 15:
        raise HTTPException(422, "duration_minutes must be 60–240 in 15-minute steps")
    start = start_at.astimezone(timezone.utc)
    end = start + timedelta(minutes=duration_minutes)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if start < current + timedelta(hours=1):
        raise HTTPException(422, "Bookings require at least one hour's notice")
    local_start = start.astimezone(LONDON)
    local_end = end.astimezone(LONDON)
    if local_start.date() > current.astimezone(LONDON).date() + timedelta(days=MAX_DAYS_AHEAD):
        raise HTTPException(422, "Bookings are limited to the next 21 days")
    if local_end.date() != local_start.date() or local_end.hour > 22 or (local_end.hour == 22 and local_end.minute > 0):
        raise HTTPException(422, "Bookings must finish by 10 PM London time")
    if local_start.minute % 15 or local_start.second or local_start.microsecond:
        raise HTTPException(422, "Start time must be on a 15-minute boundary")
    # Round half-pence up so quarter-hour test bookings have a whole-penny price.
    return start, end, (duration_minutes * price_pence_per_hour + 30) // 60
