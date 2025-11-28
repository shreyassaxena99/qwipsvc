from datetime import datetime, timedelta, timezone

from svc.utils import get_session_cost


def test_cost_for_exact_minute():
    start = datetime.now(timezone.utc) - timedelta(minutes=10)
    session = {"start_time": start.isoformat()}
    pod = {"price": 0.50}

    cost = get_session_cost(pod, session)
    assert round(cost, 2) == 5.00


def test_cost_for_completed_booking():
    start = datetime.now(timezone.utc) - timedelta(minutes=10)
    session = {
        "start_time": start.isoformat(),
        "end_time": (start + timedelta(minutes=10)).isoformat(),
    }
    pod = {"price": 0.50}

    cost = get_session_cost(pod, session)
    assert cost == 5.0


def test_fractional_cost():
    start = datetime.now(timezone.utc) - timedelta(seconds=90)
    session = {"start_time": start.isoformat()}
    pod = {"price": 1.00}

    cost = get_session_cost(pod, session)
    assert round(cost, 2) == 1.50


def test_zero_rate():
    start = datetime.now(timezone.utc) - timedelta(minutes=10)
    session = {"start_time": start.isoformat()}
    pod = {"price": 0.0}

    cost = get_session_cost(pod, session)
    assert cost == 0.0
