from database_accessor import (
    create_supabase_client,
    get_pod_by_id,
    get_session,
    increment_provisioning_attempts,
    set_access_code_id_for_session,
    set_provisioning_status_by_session_id,
)
from custom_types import ProvisionStatus
from seam_accessor import get_access_code, set_access_code
from svc.email_manager import send_access_email
from svc.models import BookingDetails


def provision_access_code_job(session_id: str) -> None:
    supabase = create_supabase_client()

    session = get_session(supabase, session_id)

    # idempotency check
    if session.get("access_code_id"):
        set_provisioning_status_by_session_id(
            supabase, session_id, ProvisionStatus.READY
        )
        return

    # provisioning logic
    try:
        access_code_id = set_access_code(session["start_time"], session["device_id"])
        set_access_code_id_for_session(supabase, session_id, access_code_id)
        set_provisioning_status_by_session_id(
            supabase, session_id, ProvisionStatus.READY
        )
        increment_provisioning_attempts(supabase, session_id, ProvisionStatus.READY)

        # send access email
        pod = get_pod_by_id(supabase, session["pod_id"])
        access_code = get_access_code(access_code_id)

        booking = BookingDetails(
            booking_id=session["id"],
            pod_name=pod["name"],
            address=pod["address"],
            start_time=session["start_time"],
            access_code=access_code,
        )

        customer_email = session["user_email"]
        send_access_email(customer_email, booking)
    except Exception as e:
        set_provisioning_status_by_session_id(
            supabase, session_id, ProvisionStatus.FAILED
        )
        increment_provisioning_attempts(supabase, session_id, ProvisionStatus.FAILED)
        raise e
