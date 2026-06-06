import io
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from svc.custom_types import TokenScope
from svc.jwt_manager import create_jwt_token

_JWT_SECRET = "test-jwt-secret-key-for-unit-tests-only"


def _session_jwt(session_id: str = "test_sess_id") -> str:
    return create_jwt_token(
        {TokenScope.SESSION.value: {"session_id": session_id}},
        TokenScope.SESSION,
        secret_key=_JWT_SECRET,
    )


def _provisioning_jwt(
    setup_intent_id: str = "si_test",
    pod_id: str = "pod_1",
    provisioning_id: str = "prov_1",
) -> str:
    return create_jwt_token(
        {
            TokenScope.PROVISIONING.value: {
                "setup_intent_id": setup_intent_id,
                "pod_id": pod_id,
                "provisioning_id": provisioning_id,
            }
        },
        TokenScope.PROVISIONING,
        secret_key=_JWT_SECRET,
    )


@pytest.fixture(scope="module")
def client():
    from svc.main import app

    return TestClient(app)


_POD_ROW = {
    "id": "pod_1",
    "name": "Test Pod",
    "address": "1 Test Lane, London",
    "price": 0.5,
    "in_use": False,
}

_SESSION_ROW = {
    "id": "test_sess_id",
    "pod_id": "pod_1",
    "user_email": "user@test.com",
    "start_time": "2025-01-01T10:00:00+00:00",
    "end_time": None,
    "stripe_customer_id": "cus_test",
    "stripe_payment_method": "pm_test",
    "access_code_id": "encrypted_code",
    "stripe_setup_intent_id": "si_test",
    "checkout_photo_url": None,
}


# ---------------------------------------------------------------------------
# GET /api/pod
# ---------------------------------------------------------------------------


def test_get_pod_returns_data(client):
    with (
        patch("svc.main.create_supabase_client"),
        patch("svc.main.get_pod_by_id", return_value=_POD_ROW),
    ):
        resp = client.get("/api/pod?pod_id=pod_1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Test Pod"
    assert data["price_per_minute"] == 0.5
    assert data["in_use"] is False


def test_get_pod_not_found_returns_404(client):
    with (
        patch("svc.main.create_supabase_client"),
        patch("svc.main.get_pod_by_id", side_effect=Exception("not found")),
    ):
        resp = client.get("/api/pod?pod_id=missing")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/session/upload-checkout-photo
# ---------------------------------------------------------------------------


def test_upload_checkout_photo_success(client):
    with (
        patch("svc.main.create_supabase_client"),
        patch(
            "svc.main.upload_checkout_photo",
            return_value="https://storage.test/photo.jpg",
        ),
        patch("svc.main.update_session_checkout_photo") as mock_update_db,
    ):
        token = _session_jwt()
        photo_bytes = b"\xff\xd8\xff" + b"\x00" * 100
        resp = client.post(
            "/api/session/upload-checkout-photo",
            files={"photo": ("photo.jpg", io.BytesIO(photo_bytes), "image/jpeg")},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    assert resp.json()["photo_url"] == "https://storage.test/photo.jpg"
    mock_update_db.assert_called_once()


def test_upload_checkout_photo_invalid_content_type(client):
    with patch("svc.main.create_supabase_client"):
        token = _session_jwt()
        resp = client.post(
            "/api/session/upload-checkout-photo",
            files={"photo": ("file.pdf", io.BytesIO(b"PDF"), "application/pdf")},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 400
    assert "Invalid file type" in resp.json()["detail"]


def test_upload_checkout_photo_too_large(client):
    with patch("svc.main.create_supabase_client"):
        token = _session_jwt()
        large = b"\x00" * (10 * 1024 * 1024 + 1)
        resp = client.post(
            "/api/session/upload-checkout-photo",
            files={"photo": ("big.jpg", io.BytesIO(large), "image/jpeg")},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 400
    assert "too large" in resp.json()["detail"].lower()


def test_upload_checkout_photo_no_auth_returns_403(client):
    resp = client.post(
        "/api/session/upload-checkout-photo",
        files={"photo": ("photo.jpg", io.BytesIO(b"img"), "image/jpeg")},
    )
    assert resp.status_code == 403


def test_upload_checkout_photo_accepts_png(client):
    with (
        patch("svc.main.create_supabase_client"),
        patch(
            "svc.main.upload_checkout_photo",
            return_value="https://storage.test/photo.png",
        ),
        patch("svc.main.update_session_checkout_photo"),
    ):
        token = _session_jwt()
        resp = client.post(
            "/api/session/upload-checkout-photo",
            files={"photo": ("photo.png", io.BytesIO(b"\x89PNG"), "image/png")},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200


def test_upload_checkout_photo_accepts_webp(client):
    with (
        patch("svc.main.create_supabase_client"),
        patch(
            "svc.main.upload_checkout_photo",
            return_value="https://storage.test/photo.webp",
        ),
        patch("svc.main.update_session_checkout_photo"),
    ):
        token = _session_jwt()
        resp = client.post(
            "/api/session/upload-checkout-photo",
            files={"photo": ("photo.webp", io.BytesIO(b"WEBP"), "image/webp")},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /api/end-session
# ---------------------------------------------------------------------------


def test_end_session_blocked_without_photo(client):
    with (
        patch("svc.main.create_supabase_client"),
        patch(
            "svc.main.get_session",
            return_value={**_SESSION_ROW, "checkout_photo_url": None},
        ),
        patch("svc.main.charge_user", return_value=0),
    ):
        resp = client.post(
            "/api/end-session",
            headers={"Authorization": f"Bearer {_session_jwt()}"},
        )
    assert resp.status_code == 400
    assert "Checkout photo required" in resp.json()["detail"]


def test_end_session_succeeds_with_photo(client):
    session_with_photo = {
        **_SESSION_ROW,
        "checkout_photo_url": "https://storage.test/photo.jpg",
    }
    with (
        patch("svc.main.create_supabase_client"),
        patch("svc.main.get_session", return_value=session_with_photo),
        patch("svc.main.get_pod_by_id", return_value=_POD_ROW),
        patch("svc.main.charge_user", return_value=0) as mock_charge,
        patch("svc.main.end_session") as mock_end,
        patch("svc.main.add_session_to_invalid_payment_attempts"),
        patch("svc.main.deprovision_access_code_job"),
    ):
        resp = client.post(
            "/api/end-session",
            headers={"Authorization": f"Bearer {_session_jwt()}"},
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
    mock_charge.assert_called_once()
    mock_end.assert_called_once()


def test_end_session_no_auth_returns_403(client):
    resp = client.post("/api/end-session")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/end-session-preview
# ---------------------------------------------------------------------------


def test_end_session_preview_returns_expected_fields(client):
    with (
        patch("svc.main.create_supabase_client"),
        patch("svc.main.get_session", return_value=_SESSION_ROW),
        patch("svc.main.get_pod_by_id", return_value=_POD_ROW),
    ):
        resp = client.get("/api/end-session-preview?session_id=test_sess_id")
    assert resp.status_code == 200
    data = resp.json()
    assert "cost" in data
    assert "start_time" in data
    assert data["is_complete"] is False


# ---------------------------------------------------------------------------
# GET /api/lock-status (static code mode)
# ---------------------------------------------------------------------------


def test_lock_status_static_mode_returns_locked(client):
    with patch("svc.main.use_static_codes", True):
        resp = client.get("/api/lock-status?device_id=test_device")
    assert resp.status_code == 200
    assert resp.json()["is_locked"] is True
