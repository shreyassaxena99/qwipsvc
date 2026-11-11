from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.testclient import TestClient

from svc.main import app
from svc.reservation_api import _whatsapp_job_payload
from svc.reservation_rules import TEST_POD_ID, reservation_window


def test_reservation_price_and_window():
    now = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
    start = datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)
    _, end, amount = reservation_window(start, 75, now)
    assert end - start == timedelta(minutes=75)
    assert amount == 1875


@pytest.mark.parametrize(("minutes", "pence"), [
    (60, 50), (75, 63), (90, 75), (105, 88), (240, 200),
])
def test_test_pod_price_rounds_to_nearest_penny(minutes, pence):
    now = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
    start = datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)
    assert reservation_window(start, minutes, now, price_pence_per_hour=50)[2] == pence


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
        {"id": "pod_1", "name": "Westfield", "address": "London"},
        {"id": TEST_POD_ID, "name": "Test Pod", "address": "Test address"},
    ]
    with patch("svc.reservation_api.create_supabase_client", return_value=db):
        response = TestClient(app).get("/api/reservations/pods")
        app_unauthenticated = TestClient(app).get("/api/app-reservations/pods")
        db.auth.get_user.return_value = SimpleNamespace(user=SimpleNamespace(
            email="test@example.com", email_confirmed_at="2026-11-01T00:00:00Z"))
        app_response = TestClient(app).get("/api/app-reservations/pods",
                                           headers={"Authorization": "Bearer valid-token"})
    assert response.status_code == 200
    assert response.json() == {"pods": [{"id": "pod_1", "name": "Westfield", "address": "London"}]}
    assert app_unauthenticated.status_code == 401
    assert app_response.status_code == 200
    assert len(app_response.json()["pods"]) == 2
    db.table.return_value.select.assert_any_call("id,name,address")


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


def test_my_bookings_show_only_active_holds_and_confirmed_history():
    db = MagicMock()
    db.auth.get_user.return_value = SimpleNamespace(user=SimpleNamespace(
        email="ada@example.com", email_confirmed_at="2026-11-01T00:00:00Z"))
    base = {
        "pod_id": "pod-1", "pod_label": "Westfield",
        "start_at": "2026-11-11T12:00:00+00:00", "end_at": "2026-11-11T13:00:00+00:00",
        "duration_minutes": 60, "amount_pence": 1500, "code_emailed_at": None,
    }
    db.table.return_value.select.return_value.eq.return_value.order.return_value.execute.return_value.data = [
        {**base, "id": "hold", "status": "pending", "access_code": None,
         "stripe_payment_intent_id": "pi_hold",
         "hold_expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()},
        {**base, "id": "expired", "status": "pending", "access_code": None,
         "stripe_payment_intent_id": "pi_expired",
         "hold_expires_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()},
        {**base, "id": "preparing", "status": "pending", "access_code": None,
         "stripe_payment_intent_id": None,
         "hold_expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()},
        {**base, "id": "abandoned", "status": "cancelled", "access_code": None},
        {**base, "id": "paid", "status": "paid", "access_code": "12345"},
        {**base, "id": "paid-cancelled", "status": "cancelled", "access_code": "54321"},
    ]
    with patch("svc.reservation_api.create_supabase_client", return_value=db):
        response = TestClient(app).get("/api/reservations/me",
                                       headers={"Authorization": "Bearer valid-token"})
    assert response.status_code == 200
    bookings = response.json()["bookings"]
    assert [booking["id"] for booking in bookings] == ["hold", "paid", "paid-cancelled"]
    assert bookings[0]["hold_expires_at"] is not None
    assert all("access_code" not in booking for booking in bookings)
    assert all("stripe_payment_intent_id" not in booking for booking in bookings)


def test_my_bookings_rejects_invalid_token():
    db = MagicMock()
    db.auth.get_user.side_effect = ValueError("invalid token")
    with patch("svc.reservation_api.create_supabase_client", return_value=db):
        response = TestClient(app).get("/api/reservations/me",
                                       headers={"Authorization": "Bearer invalid-token"})
    assert response.status_code == 401
    db.table.assert_not_called()


def _resume_db(email="ada@example.com", expires_in_minutes=5, status="pending"):
    db = MagicMock()
    db.auth.get_user.return_value = SimpleNamespace(user=SimpleNamespace(
        email="ada@example.com", email_confirmed_at="2026-11-01T00:00:00Z"))
    db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [{
        "customer_email": email, "status": status,
        "hold_expires_at": (datetime.now(timezone.utc) + timedelta(minutes=expires_in_minutes)).isoformat(),
        "stripe_payment_intent_id": "pi_test", "amount_pence": 1500,
    }]
    return db


def test_resume_payment_requires_verified_owner_and_active_hold():
    path = "/api/reservations/00000000-0000-0000-0000-000000000001/resume"
    assert TestClient(app).post(path).status_code == 401
    for db, expected in [(_resume_db(email="other@example.com"), 404),
                         (_resume_db(expires_in_minutes=-1), 409),
                         (_resume_db(status="paid"), 409)]:
        with patch("svc.reservation_api.create_supabase_client", return_value=db):
            response = TestClient(app).post(path, headers={"Authorization": "Bearer valid-token"})
        assert response.status_code == expected


def test_resume_payment_returns_existing_intent_to_owner_without_code():
    db = _resume_db()
    stripe = MagicMock()
    stripe.payment_intents.retrieve.return_value = SimpleNamespace(
        status="requires_payment_method", client_secret="pi_test_secret_example")
    with (patch("svc.reservation_api.create_supabase_client", return_value=db),
          patch("svc.reservation_api.create_stripe_client", return_value=stripe)):
        response = TestClient(app).post(
            "/api/reservations/00000000-0000-0000-0000-000000000001/resume",
            headers={"Authorization": "Bearer valid-token"})
    assert response.status_code == 200
    assert response.json()["client_secret"] == "pi_test_secret_example"
    assert "access_code" not in response.json()
    stripe.payment_intents.retrieve.assert_called_once_with("pi_test")


def test_resume_payment_rejects_intent_already_processing():
    db = _resume_db()
    stripe = MagicMock()
    stripe.payment_intents.retrieve.return_value = SimpleNamespace(
        status="processing", client_secret="pi_test_secret_example")
    with (patch("svc.reservation_api.create_supabase_client", return_value=db),
          patch("svc.reservation_api.create_stripe_client", return_value=stripe)):
        response = TestClient(app).post(
            "/api/reservations/00000000-0000-0000-0000-000000000001/resume",
            headers={"Authorization": "Bearer valid-token"})
    assert response.status_code == 409
    assert "client_secret" not in response.json()


def _reschedule_db(email="ada@example.com", status="paid"):
    db = MagicMock()
    db.auth.get_user.return_value = SimpleNamespace(user=SimpleNamespace(
        email="ada@example.com", email_confirmed_at="2026-11-01T00:00:00Z"))
    current_start = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
        hour=12, minute=0, second=0, microsecond=0)
    db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [{
        "id": "00000000-0000-0000-0000-000000000001", "customer_email": email,
        "status": status, "duration_minutes": 60, "start_at": current_start.isoformat(),
        "end_at": (current_start + timedelta(hours=1)).isoformat(),
    }]
    db.rpc.return_value.execute.return_value.data = "moved"
    return db, current_start + timedelta(hours=2)


def test_reschedule_requires_verified_account():
    response = TestClient(app).post(
        "/api/reservations/00000000-0000-0000-0000-000000000001/reschedule",
        json={"start_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()},
    )
    assert response.status_code == 401


def test_reschedule_rejects_other_customers_booking():
    db, new_start = _reschedule_db(email="someone-else@example.com")
    with patch("svc.reservation_api.create_supabase_client", return_value=db):
        response = TestClient(app).post(
            "/api/reservations/00000000-0000-0000-0000-000000000001/reschedule",
            json={"start_at": new_start.isoformat()},
            headers={"Authorization": "Bearer valid-token"},
        )
    assert response.status_code == 404
    db.rpc.assert_not_called()


def test_reschedule_keeps_pod_duration_and_payment_unchanged():
    db, new_start = _reschedule_db()
    with patch("svc.reservation_api.create_supabase_client", return_value=db):
        response = TestClient(app).post(
            "/api/reservations/00000000-0000-0000-0000-000000000001/reschedule",
            json={"start_at": new_start.isoformat()},
            headers={"Authorization": "Bearer valid-token"},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "rescheduled"
    assert "access_code" not in response.json()
    db.rpc.assert_called_once()
    assert db.rpc.call_args.args[0] == "reschedule_paid_pod_reservation"
    assert db.rpc.call_args.args[1]["p_customer_email"] == "ada@example.com"


def test_reschedule_reports_slot_conflict():
    db, new_start = _reschedule_db()
    from postgrest.exceptions import APIError
    db.rpc.return_value.execute.side_effect = APIError({
        "message": "overlap", "code": "23P01", "details": "", "hint": ""})
    with patch("svc.reservation_api.create_supabase_client", return_value=db):
        response = TestClient(app).post(
            "/api/reservations/00000000-0000-0000-0000-000000000001/reschedule",
            json={"start_at": new_start.isoformat()},
            headers={"Authorization": "Bearer valid-token"},
        )
    assert response.status_code == 409


def test_reschedule_rejects_unpaid_booking():
    db, new_start = _reschedule_db(status="pending")
    with patch("svc.reservation_api.create_supabase_client", return_value=db):
        response = TestClient(app).post(
            "/api/reservations/00000000-0000-0000-0000-000000000001/reschedule",
            json={"start_at": new_start.isoformat()},
            headers={"Authorization": "Bearer valid-token"},
        )
    assert response.status_code == 409
    db.rpc.assert_not_called()


def _cancel_db(outcome="cancelled", refund_id=None):
    db = MagicMock()
    db.auth.get_user.return_value = SimpleNamespace(user=SimpleNamespace(
        email="ada@example.com", email_confirmed_at="2026-11-01T00:00:00Z"))
    db.rpc.return_value.execute.return_value.data = outcome
    db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [{
        "stripe_payment_intent_id": "pi_test", "stripe_refund_id": refund_id,
        "amount_pence": 50,
    }]
    return db


def test_customer_cancel_requires_verified_account_and_eligible_booking():
    path = "/api/reservations/00000000-0000-0000-0000-000000000001/cancel"
    assert TestClient(app).post(path).status_code == 401
    for outcome, expected in [("not_found", 404), ("ineligible", 409)]:
        db = _cancel_db(outcome)
        with patch("svc.reservation_api.create_supabase_client", return_value=db):
            response = TestClient(app).post(path, headers={"Authorization": "Bearer valid-token"})
        assert response.status_code == expected
        assert db.rpc.call_args.args[0] == "request_customer_cancel_pod_reservation"
        assert db.rpc.call_args.args[1]["p_customer_email"] == "ada@example.com"


def test_customer_cancel_creates_one_full_refund_and_records_it():
    db = _cancel_db()
    stripe = MagicMock()
    stripe.refunds.list.return_value.data = []
    stripe.refunds.create.return_value = SimpleNamespace(id="re_test", status="pending")
    path = "/api/reservations/00000000-0000-0000-0000-000000000001/cancel"
    with (patch("svc.reservation_api.create_supabase_client", return_value=db),
          patch("svc.reservation_api.create_stripe_client", return_value=stripe)):
        response = TestClient(app).post(path, headers={"Authorization": "Bearer valid-token"})
    assert response.status_code == 200
    assert response.json() == {"status": "cancelled", "refund_status": "pending", "refund_issued": True}
    kwargs = stripe.refunds.create.call_args.kwargs
    assert kwargs["params"]["payment_intent"] == "pi_test"
    assert "amount" not in kwargs["params"]
    assert kwargs["options"]["idempotency_key"] == "customer-reservation-cancel-00000000-0000-0000-0000-000000000001"
    db.table.return_value.update.assert_called_once_with({
        "stripe_refund_id": "re_test", "stripe_refund_status": "pending",
    })


def test_customer_cancel_retry_reuses_existing_refund():
    db = _cancel_db("already_requested", refund_id="re_test")
    stripe = MagicMock()
    stripe.refunds.retrieve.return_value = SimpleNamespace(id="re_test", status="succeeded")
    with (patch("svc.reservation_api.create_supabase_client", return_value=db),
          patch("svc.reservation_api.create_stripe_client", return_value=stripe)):
        response = TestClient(app).post(
            "/api/reservations/00000000-0000-0000-0000-000000000001/cancel",
            headers={"Authorization": "Bearer valid-token"})
    assert response.status_code == 200
    assert response.json()["refund_status"] == "succeeded"
    stripe.refunds.create.assert_not_called()


def test_customer_cancel_recovers_refund_after_database_write_failure():
    db = _cancel_db("already_requested")
    stripe = MagicMock()
    stripe.refunds.list.return_value.data = [SimpleNamespace(
        id="re_test", status="pending",
        metadata={"customer_cancel_reservation_id": "00000000-0000-0000-0000-000000000001"})]
    with (patch("svc.reservation_api.create_supabase_client", return_value=db),
          patch("svc.reservation_api.create_stripe_client", return_value=stripe)):
        response = TestClient(app).post(
            "/api/reservations/00000000-0000-0000-0000-000000000001/cancel",
            headers={"Authorization": "Bearer valid-token"})
    assert response.status_code == 200
    stripe.refunds.create.assert_not_called()
    db.table.return_value.update.assert_called_once()


def test_worker_requires_private_token():
    response = TestClient(app).post("/internal/reservations/dispatch")
    assert response.status_code == 401


@pytest.mark.parametrize(("path", "pod_id", "expected_pence"), [
    ("/api/reservations", "pod_1", 1875),
    ("/api/app-reservations", "pod_1", 1875),
    ("/api/app-reservations", TEST_POD_ID, 63),
])
def test_checkout_amount_is_calculated_on_server(path, pod_id, expected_pence):
    start = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    db = MagicMock()
    db.auth.get_user.return_value = SimpleNamespace(user=SimpleNamespace(
        email="test@example.com", email_confirmed_at="2026-11-01T00:00:00Z"))
    db.rpc.return_value.execute.side_effect = [
        SimpleNamespace(data={"id": "00000000-0000-0000-0000-000000000001", "hold_expires_at": "2026-11-10T12:10:00+00:00"}),
        SimpleNamespace(data=True),
    ]
    stripe = MagicMock()
    stripe.payment_intents.create.return_value = SimpleNamespace(id="pi_test", client_secret="client_secret_test")
    with (patch("svc.reservation_api.create_supabase_client", return_value=db),
          patch("svc.reservation_api.get_pod_by_id", return_value={"name": "Westfield London"}),
          patch("svc.reservation_api.create_stripe_client", return_value=stripe)):
        response = TestClient(app).post(path, json={
            "pod_id": pod_id, "start_at": start.isoformat(), "duration_minutes": 75,
            "customer_name": "Test User", "customer_email": "test@example.com",
        }, headers={"Authorization": "Bearer valid-token"})
    assert response.status_code == 201
    assert response.json()["amount_pence"] == expected_pence
    assert stripe.payment_intents.create.call_args.kwargs["params"]["amount"] == expected_pence
    assert "access_code" not in response.json()


def test_app_checkout_requires_verified_matching_email():
    path = "/api/app-reservations"
    start = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    body = {"pod_id": "pod_1", "start_at": start.isoformat(), "duration_minutes": 60,
            "customer_name": "Test User", "customer_email": "other@example.com"}
    assert TestClient(app).post(path, json=body).status_code == 401
    db = MagicMock()
    db.auth.get_user.return_value = SimpleNamespace(user=SimpleNamespace(
        email="test@example.com", email_confirmed_at="2026-11-01T00:00:00Z"))
    with patch("svc.reservation_api.create_supabase_client", return_value=db):
        response = TestClient(app).post(path, json=body,
                                        headers={"Authorization": "Bearer valid-token"})
    assert response.status_code == 403
    db.rpc.assert_not_called()


def test_availability_uses_pod_price_for_both_app_and_website():
    day = (datetime.now(timezone.utc) + timedelta(days=2)).date().isoformat()
    db = MagicMock()
    db.auth.get_user.return_value = SimpleNamespace(user=SimpleNamespace(
        email="test@example.com", email_confirmed_at="2026-11-01T00:00:00Z"))
    with (patch("svc.reservation_api.create_supabase_client", return_value=db),
          patch("svc.reservation_api.get_pod_by_id", return_value={"name": "Westfield"}),
          patch("svc.reservation_api._active_overlaps", return_value=[])):
        website = TestClient(app).get("/api/reservations/availability",
                                       params={"pod_id": "pod_1", "day": day})
        app_response = TestClient(app).get("/api/app-reservations/availability",
                                            params={"pod_id": "pod_1", "day": day},
                                            headers={"Authorization": "Bearer valid-token"})
    assert website.status_code == app_response.status_code == 200
    website_hour = next(duration for slot in website.json()["slots"]
                        for duration in slot["durations"] if duration["minutes"] == 60)
    app_hour = next(duration for slot in app_response.json()["slots"]
                    for duration in slot["durations"] if duration["minutes"] == 60)
    assert website_hour["amount_pence"] == 1500
    assert app_hour["amount_pence"] == 1500

    with (patch("svc.reservation_api.create_supabase_client", return_value=db),
          patch("svc.reservation_api.get_pod_by_id", return_value={"name": "Test Pod"}),
          patch("svc.reservation_api._active_overlaps", return_value=[])):
        test_pod_public = TestClient(app).get("/api/reservations/availability",
                                              params={"pod_id": TEST_POD_ID, "day": day})
        test_pod_app = TestClient(app).get("/api/app-reservations/availability",
                                           params={"pod_id": TEST_POD_ID, "day": day},
                                           headers={"Authorization": "Bearer valid-token"})
    assert test_pod_public.status_code == 404
    test_hour = next(duration for slot in test_pod_app.json()["slots"]
                     for duration in slot["durations"] if duration["minutes"] == 60)
    assert test_hour["amount_pence"] == 50


def test_public_checkout_cannot_book_test_pod():
    start = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
        hour=12, minute=0, second=0, microsecond=0)
    with patch("svc.reservation_api.create_supabase_client") as db:
        response = TestClient(app).post("/api/reservations", json={
            "pod_id": TEST_POD_ID, "start_at": start.isoformat(), "duration_minutes": 60,
            "customer_name": "Test User", "customer_email": "test@example.com",
        })
    assert response.status_code == 404
    db.assert_not_called()


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
