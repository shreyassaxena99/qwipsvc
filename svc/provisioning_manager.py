from svc.database_accessor import (
    create_supabase_client,
    get_pod_by_id,
    get_session,
    increment_provisioning_attempts,
    set_access_code_id_for_session,
    set_provisioning_status_by_session_id,
)
from svc.custom_types import ProvisionStatus, TokenScope
from svc.jwt_manager import verify_jwt_token
from svc.seam_accessor import get_access_code, set_access_code
from svc.email_manager import send_access_email
from svc.models import SessionDetails

import logging

logger = logging.getLogger(__name__)


def provision_access_code_job(session_jwt_token: str) -> None:
    supabase = create_supabase_client()
    payload = verify_jwt_token(session_jwt_token, TokenScope.SESSION)

    session_id = payload["session_id"]
    session = get_session(supabase, session_id)

    # idempotency check
    if session.get("access_code_id"):
        set_provisioning_status_by_session_id(
            supabase, session_id, ProvisionStatus.READY
        )
        return

    # provisioning logic
    try:
        logger.info(f"Provisioning access code for session {session_id}")
        access_code_id = set_access_code(session["start_time"], session["device_id"])
        logger.info(
            f"Access code {access_code_id} provisioned for session {session_id}"
        )
        set_access_code_id_for_session(supabase, session_id, access_code_id)
        set_provisioning_status_by_session_id(
            supabase, session_id, ProvisionStatus.READY
        )
        increment_provisioning_attempts(supabase, session_id, ProvisionStatus.READY)

        # send access email
        pod = get_pod_by_id(supabase, session["pod_id"])
        access_code = get_access_code(access_code_id)

        booking = SessionDetails(
            session_token=session_jwt_token,
            pod_name=pod["name"],
            address=pod["address"],
            start_time=session["start_time"],
            access_code=access_code,
        )

        customer_email = session["user_email"]
        logger.info(f"Sending access email to {customer_email}")
        send_access_email(customer_email, booking)
    except Exception as e:
        logger.error(f"Error provisioning access code for session {session_id}: {e}")
        set_provisioning_status_by_session_id(
            supabase, session_id, ProvisionStatus.FAILED
        )
        increment_provisioning_attempts(supabase, session_id, ProvisionStatus.FAILED)
        raise e
