from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from svc.reservation_delivery import dispatch_reservation_deliveries, send_reservation_email


def test_email_worker_completes_email_before_whatsapp_can_be_queued():
    db = MagicMock()
    job = {"id": "job-1", "reservation_id": "reservation-1",
           "lease_token": "lease-1", "attempts": 1}
    db.rpc.return_value.execute.side_effect = [
        SimpleNamespace(data=0),
        SimpleNamespace(data=[job]),
        SimpleNamespace(data=True),
    ]
    db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
        {"id": "reservation-1", "status": "paid", "access_code": "12345"}
    ]
    with (patch("svc.reservation_delivery.create_supabase_client", return_value=db),
          patch("svc.reservation_delivery.send_reservation_email") as send_email):
        result = dispatch_reservation_deliveries()
    assert result == {"sent": 1, "failed": 0}
    send_email.assert_called_once()
    assert db.rpc.call_args_list[-1].args == ("complete_reservation_email", {
        "p_job_id": "job-1", "p_lease_token": "lease-1",
    })


def test_rescheduled_confirmation_uses_updated_subject_and_time():
    booking = {
        "customer_name": "Ada", "customer_email": "ada@example.com",
        "pod_label": "Westfield", "start_at": "2026-11-12T12:00:00+00:00",
        "end_at": "2026-11-12T13:00:00+00:00", "amount_pence": 1500,
        "access_code": "12345", "rescheduled_at": "2026-11-10T12:00:00+00:00",
    }
    with (patch.dict("os.environ", {"RESEND_API_KEY": "test-key"}),
          patch("svc.reservation_delivery.resend.Emails.send", return_value={"id": "email-1"}) as send):
        send_reservation_email(booking)
    payload = send.call_args.args[0]
    assert payload["subject"] == "Your qwip pod booking has been updated"
    assert "12 Nov 2026" in payload["html"]
    assert "12345" in payload["html"]
