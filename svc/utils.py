from datetime import datetime, timezone

from svc.types import DictWithStringKeys


def get_session_cost(pod: DictWithStringKeys, session: DictWithStringKeys) -> float:
    session_start_time = session["start_time"]
    session_minutes: float = (
        (datetime.now(timezone.utc) - session_start_time).total_seconds()
    ) / 60
    return session_minutes * float(pod["price"])
