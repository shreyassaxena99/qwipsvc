from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.testclient import TestClient

from svc.main import app
from svc.reservation_api import _whatsapp_job_payload
from svc.reservation_rules import reservation_window


def test_reservation_price_and_window():
    now = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
    start = datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)
    _, end, amount = reservation_window(start, 75, now)
    assert end - start == timedelta(minutes=75)
    assert amount == 1875


@pytest.mark.parametrize("duration", [45, 61, 255])
def test_invalid_reservation_duration(duration):
    now = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(HTTPException):
        reservation_window(now + timedelta(hours=2), duration, now)


def test_reservation_cannot_end_after_10pm_london_time():
    now = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(HTTPException):
        reservation_window(datetime(2026, 11, 10, 21, 15, tzinfo=timezone.utc), 60, now)


def test_public_status_never_returns_access_code():
    with patch("svc.reservation_api._booking", return_value={
        "status": "paid", "access_code": "12345", "code_emailed_at": None,
    }):
        response = TestClient(app).get("/api/reservations/00000000-0000-0000-0000-000000000001/status")
    assert response.status_code == 200
    assert "access_code" not in response.json()


def test_pod_catalogue_exposes_only_booking_display_fields():
    db = MagicMock()
    db.table.return_value.select.return_value.order.return_value.execute.return_value.data = [
        {"id": "pod_1", "name": "Westfield", "address": "London"}
    ]
    with patch("svc.reservation_api.create_supabase_client", return_value=db):
        response = TestClient(app).get("/api/reservations/pods")
    assert response.status_code == 200
    assert response.json() == {"pods": [{"id": "pod_1", "name": "Westfield", "address": "London"}]}
    db.table.return_value.select.assert_called_once_with("id,name,address")


def test_my_bookings_requires_authentication():
    response = TestClient(app).get("/api/reservations/me")
    assert response.status_code == 401


def test_my_bookings_use_verified_email_and_never_return_codes():
    db = MagicMock()
    db.auth.get_user.return_value = SimpleNamespace(user=SimpleNamespace(
        email="Ada@Example.com", email_confirmed_at="2026-11-01T00:00:00Z"))
    db.table.return_value.select.return_value.eq.return_value.order.return_value.execute.return_value.data = [{
        "id": "booking-1", "pod_id": "pod-1", "pod_label": "Westfield",
        "start_at": "2026-11-11T12:00:00+00:00", "end_at": "2026-11-11T13:00:00+00:00",
        "duration_minutes": 60, "amount_pence": 1500, "status": "paid",
        "hold_expires_at": None, "code_emailed_at": "2026-11-10T12:00:00+00:00",
        "access_code": "12345", "stripe_payment_intent_id": "pi_secret",
    }]
    with patch("svc.reservation_api.create_supabase_client", return_value=db):
        response = TestClient(app).get("/api/reservations/me",
                                       headers={"Authorization": "Bearer valid-token"})
    assert response.status_code == 200
    db.auth.get_user.assert_called_once_with("valid-token")
    db.table.return_value.select.return_value.eq.assert_called_once_with("customer_email", "ada@example.com")
    booking = response.json()["bookings"][0]
    assert booking["code_emailed"] is True
    assert "access_code" not in booking
    assert "stripe_payment_intent_id" not in booking


def test_my_bookings_rejects_invalid_token():
    db = MagicMock()
    db.auth.get_user.side_effect = ValueError("invalid token")
    with patch("svc.reservation_api.create_supabase_client", return_value=db):
        response = TestClient(app).get("/api/reservations/me",
                                       headers={"Authorization": "Bearer invalid-token"})
    assert response.status_code == 401
    db.table.assert_not_called()


def test_worker_requires_private_token():
    response = TestClient(app).post("/internal/reservations/dispatch")
    assert response.status_code == 401


def test_checkout_amount_is_calculated_on_server():
    start = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    db = MagicMock()
    db.rpc.return_value.execute.side_effect = [
        SimpleNamespace(data={"id": "00000000-0000-0000-0000-000000000001", "hold_expires_at": "2026-11-10T12:10:00+00:00"}),
        SimpleNamespace(data=True),
    ]
    stripe = MagicMock()
    stripe.payment_intents.create.return_value = SimpleNamespace(id="pi_test", client_secret="client_secret_test")
    with (patch("svc.reservation_api.create_supabase_client", return_value=db),
          patch("svc.reservation_api.get_pod_by_id", return_value={"name": "Westfield London"}),
          patch("svc.reservation_api.create_stripe_client", return_value=stripe)):
        response = TestClient(app).post("/api/reservations", json={
            "pod_id": "pod_1", "start_at": start.isoformat(), "duration_minutes": 75,
            "customer_name": "Test User", "customer_email": "test@example.com",
        })
    assert response.status_code == 201
    assert response.json()["amount_pence"] == 1875
    assert stripe.payment_intents.create.call_args.kwargs["params"]["amount"] == 1875
    assert "access_code" not in response.json()


def test_signed_payment_webhook_confirms_matching_booking():
    booking_id = "00000000-0000-0000-0000-000000000001"
    payment = SimpleNamespace(id="pi_test", amount_received=1875, currency="gbp",
                              metadata={"purpose": "pod_reservation", "reservation_id": booking_id})
    event = SimpleNamespace(type="payment_intent.succeeded",
                            data=SimpleNamespace(object=payment))
    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = "paid"
    with (patch("svc.main.create_stripe_event", return_value=event),
          patch("svc.reservation_api._booking", return_value={
              "stripe_payment_intent_id": "pi_test", "amount_pence": 1875,
          }),
          patch("svc.reservation_api._code_from_pool", return_value="12345"),
          patch("svc.reservation_api.create_supabase_client", return_value=db)):
        response = TestClient(app).post("/webhook/stripe", content=b"signed")
    assert response.status_code == 200
    db.rpc.assert_called_once_with("confirm_paid_reservation", {
        "p_reservation_id": booking_id, "p_payment_intent_id": "pi_test", "p_access_code": "12345",
    })


def test_shared_webhook_ignores_unrelated_payment_for_reservations():
    payment = SimpleNamespace(metadata={"purpose": "walk_up"})
    event = SimpleNamespace(type="payment_intent.succeeded",
                            data=SimpleNamespace(object=payment))
    with (patch("svc.main.create_stripe_event", return_value=event),
          patch("svc.main.process_event") as walkup_processor,
          patch("svc.reservation_api.create_supabase_client") as database):
        response = TestClient(app).post("/webhook/stripe", content=b"signed")
    assert response.status_code == 200
    walkup_processor.assert_called_once_with(event)
    database.assert_not_called()


def test_whatsapp_claim_requires_server_token():
    client = TestClient(app)
    response = client.post("/internal/reservations/whatsapp/claim", json={})
    assert response.status_code == 401


def test_whatsapp_claim_rejects_browser_origin_even_with_token():
    with patch.dict("os.environ", {"RESERVATION_LOVABLE_TOKEN": "private-test-token"}):
        response = TestClient(app).post("/internal/reservations/whatsapp/claim", json={},
                                        headers={"Authorization": "Bearer private-test-token",
                                                 "Origin": "https://qwip.co.uk"})
    assert response.status_code == 403


def test_whatsapp_claim_returns_only_approved_booking_fields():
    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = [{
        "id": "00000000-0000-0000-0000-000000000002",
        "reservation_id": "00000000-0000-0000-0000-000000000001",
        "lease_token": "00000000-0000-0000-0000-000000000003",
        "kind": "booking_whatsapp", "attempts": 1,
    }]
    db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [{
        "status": "paid", "customer_name": "Ada Lovelace", "customer_phone": "+447700900000",
        "start_at": "2026-11-10T12:00:00+00:00", "end_at": "2026-11-10T13:00:00+00:00",
        "access_code": "12345", "customer_email": "ada@example.com",
    }]
    with (patch.dict("os.environ", {"RESERVATION_LOVABLE_TOKEN": "private-test-token"}),
          patch("svc.reservation_api.create_supabase_client", return_value=db)):
        response = TestClient(app).post("/internal/reservations/whatsapp/claim", json={},
                                        headers={"Authorization": "Bearer private-test-token"})
    assert response.status_code == 200
    job = response.json()["jobs"][0]
    assert job["template"] == "pod_booking_details"
    assert job["parameters"] == ["Ada", "Tuesday 10 November 2026 at 12:00 PM"]
    assert "12345" not in str(job)
    assert "ada@example.com" not in str(job)


def test_whatsapp_ack_requires_valid_lease():
    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = "invalid"
    with (patch.dict("os.environ", {"RESERVATION_LOVABLE_TOKEN": "private-test-token"}),
          patch("svc.reservation_api.create_supabase_client", return_value=db)):
        response = TestClient(app).post("/internal/reservations/whatsapp/ack", json={
            "job_id": "00000000-0000-0000-0000-000000000002",
            "lease_token": "00000000-0000-0000-0000-000000000003", "success": True,
        }, headers={"Authorization": "Bearer private-test-token"})
    assert response.status_code == 409


def test_reminder_payload_has_end_time_and_no_access_code():
    payload = _whatsapp_job_payload(
        {"id": "job-1", "lease_token": "lease-1", "kind": "end_reminder"},
        {"customer_name": "Ada Lovelace", "customer_phone": "+447700900000",
         "start_at": "2026-11-10T12:00:00+00:00",
         "end_at": "2026-11-10T13:00:00+00:00", "access_code": "12345"},
    )
    assert payload["template"] == "session_end_reminder"
    assert payload["parameters"] == ["Ada", "Tuesday 10 November 2026 at 01:00 PM"]
    assert "12345" not in str(payload)


def test_admin_cancel_uses_atomic_database_operation():
    db = MagicMock()
    with (patch.dict("os.environ", {"RESERVATION_ADMIN_TOKEN": "private-admin-token"}),
          patch("svc.reservation_api._booking", return_value={"status": "paid"}),
          patch("svc.reservation_api.create_supabase_client", return_value=db)):
        response = TestClient(app).post(
            "/api/reservations/admin/00000000-0000-0000-0000-000000000001/cancel",
            headers={"Authorization": "Bearer private-admin-token"},
        )
    assert response.status_code == 200
    db.rpc.assert_called_once_with("cancel_pod_reservation", {
        "p_reservation_id": "00000000-0000-0000-0000-000000000001",
    })
