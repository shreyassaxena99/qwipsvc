from datetime import datetime, timezone
from svc.database_accessor import (
    create_supabase_client,
    get_pod_by_id,
    get_session,
    increment_provisioning_attempts,
    set_access_code_id_for_session,
    set_provisioning_status_by_session_id,
    set_start_time_for_session,
    update_pod_status,
)
from svc.custom_types import ProvisionStatus
from svc.seam_accessor import delete_access_code, get_access_code, set_access_code
from svc.email_manager import send_access_email
from svc.models import (
    SessionDeprovisioningJobMetadata,
    SessionDetails,
    SessionProvisioningJobMetadata,
)

import logging

from svc.static_code_manager import StaticCodeManager

logger = logging.getLogger(__name__)


def provision_access_code_job(
    session_provisioning_metadata: SessionProvisioningJobMetadata,
) -> None:
    try:
        supabase = create_supabase_client()
        jwt_token = session_provisioning_metadata.jwt_token
        session_id = session_provisioning_metadata.session_id
        use_static_codes = session_provisioning_metadata.use_static_codes
        session = get_session(supabase, session_id)

        # idempotency check
        if session.get("access_code_id"):
            set_provisioning_status_by_session_id(
                supabase, session_id, ProvisionStatus.READY
            )
            return

        # provisioning logic
        logger.info(f"Provisioning access code for session {session_id}")
        access_code_id = (
            StaticCodeManager().random_encrypted_access_code_id()
            if use_static_codes
            else set_access_code(datetime.fromisoformat(session["start_time"]))
        )
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
        access_code = (
            StaticCodeManager().decrypt_code(access_code_id)
            if use_static_codes
            else get_access_code(access_code_id)
        )

        set_start_time_for_session(supabase, session_id, datetime.now(timezone.utc))

        booking = SessionDetails(
            session_token=jwt_token,
            pod_name=pod["name"],
            address=pod["address"],
            start_time=datetime.now(timezone.utc),
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


def deprovision_access_code_job(
    session_deprovisioning_metadata: SessionDeprovisioningJobMetadata,
) -> None:
    try:
        supabase = create_supabase_client()
        access_code_id = session_deprovisioning_metadata.access_code_id
        pod_id = session_deprovisioning_metadata.pod_id
        use_static_codes = session_deprovisioning_metadata.use_static_codes

        if not use_static_codes:
            logger.info(f"Deprovisioning access code {access_code_id} for pod {pod_id}")
            delete_access_code(access_code_id)
        else:
            logger.info(f"Static code used, no deprovisioning needed for pod {pod_id}")

        logger.info("Access code deleted successfully, updating pod status")
        update_pod_status(supabase, pod_id, False)
    except Exception as e:
        logger.error(f"Error deprovisioning access code for session: {e}")
        raise e
