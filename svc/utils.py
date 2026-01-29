from datetime import datetime, timezone
from logger import logging

from svc.custom_types import DictWithStringKeys

logger = logging.getLogger(__name__)


def get_session_cost(
    pod: DictWithStringKeys, session: DictWithStringKeys, promo_mode: bool = False
) -> float:
    session_start_time = session["start_time"]
    if not session.get("end_time"):
        session_end_time = datetime.now(timezone.utc)
    else:
        session_end_time = datetime.fromisoformat(session["end_time"])
    session_minutes: float = (
        (session_end_time - datetime.fromisoformat(session_start_time)).total_seconds()
    ) / 60
    billable_minutes = (
        promo_mode and max(0.0, session_minutes - 10.0) or session_minutes
    )  # first 10 minutes free if in promo_mode
    logger.info(
        f"promo_mode = {promo_mode} BILLABLE duration in minutes: {billable_minutes}"
    )
    return billable_minutes * float(pod["price"])


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def format_datetime_for_email(iso_str: str) -> str:
    dt = datetime.fromisoformat(iso_str)
    day_with_suffix = _ordinal(dt.day)
    month = dt.strftime("%B")
    year = dt.year
    hour_str = (
        dt.strftime("%-I%p")
        if hasattr(dt, "strftime")
        else dt.strftime("%I%p").lstrip("0")
    )  # Handles 12-hour format
    return f"{day_with_suffix} {month} {year} @ {hour_str}"
