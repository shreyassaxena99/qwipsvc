from datetime import datetime, timezone

from svc.custom_types import DictWithStringKeys


def get_session_cost(pod: DictWithStringKeys, session: DictWithStringKeys) -> float:
    session_start_time = session["start_time"]
    session_minutes: float = (
        (
            datetime.now(timezone.utc) - datetime.fromisoformat(session_start_time)
        ).total_seconds()
    ) / 60
    return session_minutes * float(pod["price"])


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
