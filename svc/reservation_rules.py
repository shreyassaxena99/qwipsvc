"""Server-owned rules for prepaid pod reservations."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException

LONDON = ZoneInfo("Europe/London")
PRICE_PENCE_PER_HOUR = 1500
MAX_DAYS_AHEAD = 21


def reservation_window(start_at: datetime, duration_minutes: int, now: datetime | None = None) -> tuple[datetime, datetime, int]:
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
    return start, end, duration_minutes * PRICE_PENCE_PER_HOUR // 60
