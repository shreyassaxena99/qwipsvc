from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from postgrest.exceptions import APIError

from svc.database_accessor import (
    SupabaseError,
    add_session,
    add_session_to_invalid_payment_attempts,
    end_session,
    get_pod_by_id,
    get_session,
    set_access_code_id_for_session,
    update_pod_status,
    update_session_checkout_photo,
)
from svc.models import PodSession


def _client():
    return MagicMock()


def _pod_row():
    return {
        "id": "pod_1",
        "name": "Test Pod",
        "address": "1 Test St",
        "price": 0.5,
        "in_use": False,
    }


def _session_row():
    return {
        "id": "sess_1",
        "pod_id": "pod_1",
        "user_email": "user@test.com",
        "start_time": "2025-01-01T10:00:00+00:00",
        "end_time": None,
        "stripe_customer_id": "cus_test",
        "stripe_payment_method": "pm_test",
        "access_code_id": None,
        "checkout_photo_url": None,
    }


def _new_session():
    return PodSession(
        pod_id="pod_1",
        user_email="user@test.com",
        start_time=datetime.now(timezone.utc),
        stripe_customer_id="cus_test",
        stripe_payment_method="pm_test",
        setup_intent_id="si_test",
    )


# --- get_pod_by_id ---


def test_get_pod_by_id_returns_data():
    client = _client()
    client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
        _pod_row()
    ]
    pod = get_pod_by_id(client, "pod_1")
    assert pod["name"] == "Test Pod"
    assert pod["price"] == 0.5


def test_get_pod_by_id_not_found_raises():
    client = _client()
    client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = (
        []
    )
    with pytest.raises(SupabaseError):
        get_pod_by_id(client, "missing")


def test_get_pod_by_id_api_error_raises():
    client = _client()
    client.table.return_value.select.return_value.eq.return_value.execute.side_effect = APIError(
        {}
    )
    with pytest.raises(SupabaseError):
        get_pod_by_id(client, "pod_1")


# --- get_session ---


def test_get_session_returns_data():
    client = _client()
    client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
        _session_row()
    ]
    session = get_session(client, "sess_1")
    assert session["id"] == "sess_1"
    assert session["pod_id"] == "pod_1"


def test_get_session_not_found_raises():
    client = _client()
    client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = (
        []
    )
    with pytest.raises(SupabaseError):
        get_session(client, "nonexistent")


# --- add_session ---


def test_add_session_populates_id():
    client = _client()
    client.table.return_value.insert.return_value.execute.return_value.data = [
        {"id": "new_id"}
    ]
    session = _new_session()
    add_session(client, session)
    assert session.id == "new_id"


def test_add_session_raises_on_empty_response():
    client = _client()
    client.table.return_value.insert.return_value.execute.return_value.data = []
    with pytest.raises(SupabaseError):
        add_session(client, _new_session())


# --- end_session ---


def test_end_session_sets_end_time():
    client = _client()
    end_session(client, "sess_1")
    update_call = client.table.return_value.update.call_args[0][0]
    assert "end_time" in update_call


# --- update_pod_status ---


def test_update_pod_status_in_use():
    client = _client()
    update_pod_status(client, "pod_1", True)
    client.table.return_value.update.assert_called_with({"in_use": True})


def test_update_pod_status_available():
    client = _client()
    update_pod_status(client, "pod_1", False)
    client.table.return_value.update.assert_called_with({"in_use": False})


# --- update_session_checkout_photo ---


def test_update_checkout_photo_writes_correct_field():
    client = _client()
    update_session_checkout_photo(client, "sess_1", "https://example.com/photo.jpg")
    client.table.return_value.update.assert_called_with(
        {"checkout_photo_url": "https://example.com/photo.jpg"}
    )


def test_update_checkout_photo_targets_correct_table():
    client = _client()
    update_session_checkout_photo(client, "sess_1", "https://example.com/photo.jpg")
    client.table.assert_called_with("pod_sessions")


def test_update_checkout_photo_api_error_raises():
    client = _client()
    client.table.return_value.update.return_value.eq.return_value.execute.side_effect = APIError(
        {}
    )
    with pytest.raises(SupabaseError):
        update_session_checkout_photo(client, "sess_1", "https://example.com/photo.jpg")


# --- set_access_code_id_for_session ---


def test_set_access_code_id_writes_correct_field():
    client = _client()
    set_access_code_id_for_session(client, "sess_1", "code_123")
    client.table.return_value.update.assert_called_with({"access_code_id": "code_123"})


# --- add_session_to_invalid_payment_attempts ---


def test_add_invalid_payment_inserts_correct_data():
    client = _client()
    add_session_to_invalid_payment_attempts(client, "sess_1", 500)
    client.table.return_value.insert.assert_called_with(
        {"session_id": "sess_1", "session_cost": 500}
    )
