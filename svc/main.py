from datetime import datetime, timezone
from deprecated import deprecated
import logging

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from svc.custom_types import DictWithStringKeys, ProvisionStatus, TokenScope
from svc.database_accessor import (
    add_provisioning,
    add_session,
    create_supabase_client,
    end_session,
    get_pod_by_id,
    get_provisioning_by_session_id,
    get_session,
    get_session_by_setup_intent_id,
    update_pod_status,
)
from svc.email_manager import send_access_email
from svc.env import log_level, use_static_codes
from svc.jwt_manager import create_jwt_token, verify_jwt_token
from svc.models import (
    EndSessionResponse,
    GetLockStatusResponse,
    PodData,
    ProvisioningStatusResponse,
    SessionData,
    SessionDataResponse,
    SessionDeprovisioningJobMetadata,
    SessionDetails,
    CreateSetupIntentResponse,
    EndSessionRequest,
    FinalizeBookingResponse,
    GetPodResponse,
    ConfirmBookingRequest,
    PodSession,
    SessionProvision,
    SessionProvisioningJobMetadata,
    SetupIntentRequest,
    SetupIntentResponse,
)
from svc.payments_manager import (
    charge_user,
    create_stripe_client,
    create_stripe_event,
    create_setup_intent,
    process_event,
)
from svc.provisioning_manager import (
    deprovision_access_code_job,
    provision_access_code_job,
)
from svc.static_code_manager import StaticCodeManager
from svc.utils import get_session_cost
from svc.seam_accessor import (
    delete_access_code,
    get_access_code,
    is_device_locked,
    set_access_code,
)

import uuid

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=log_level)

RESPONSE_STATUS_SUCCESS = "success"
RESPONSE_STATUS_FAILED = "failed"

logger = logging.getLogger(__name__)


def get_token(creds: HTTPAuthorizationCredentials = Depends(HTTPBearer())) -> str:
    return creds.credentials


@deprecated(
    reason="We are moving to a provisioning flow that uses JWTs to manage sessions"
)
@app.get("/api/create-setup-intent")
def create_setup_intent_request(pod_id: str) -> CreateSetupIntentResponse:
    try:
        setup_intent = create_setup_intent(pod_id)
        if not setup_intent.client_secret:
            raise RuntimeError("Failed to create setup intent")
        return CreateSetupIntentResponse(client_secret=setup_intent.client_secret)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/setup-intent")
def setup_intent_request(request: SetupIntentRequest) -> SetupIntentResponse:
    try:
        setup_intent = create_setup_intent(request.pod_id)
        if not setup_intent.client_secret:
            raise RuntimeError("Failed to create setup intent")
        jwt_token = create_jwt_token(
            {
                TokenScope.PROVISIONING.value: {
                    "setup_intent_id": setup_intent.id,
                    "pod_id": request.pod_id,
                    "provisioning_id": str(uuid.uuid4()),
                },
            },
            TokenScope.PROVISIONING,
        )
        logger.info(f"Generated JWT token for setup intent {setup_intent.id}")
        return SetupIntentResponse(
            client_secret=setup_intent.client_secret, provisioning_jwt_token=jwt_token
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/pod")
def get_pod_request(pod_id: str) -> GetPodResponse:
    try:
        supabase = create_supabase_client()
        pod = get_pod_by_id(supabase, pod_id)
        return GetPodResponse(
            name=pod["name"],
            address=pod["address"],
            price_per_minute=pod["price"],
            in_use=pod["in_use"],
        )
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.post("/webhook/stripe")
async def stripe_webhook(request: Request) -> DictWithStringKeys:
    try:
        payload = await request.body()
        signature_header = request.headers.get("stripe-signature")
        try:
            event = create_stripe_event(payload, signature_header)  # type: ignore
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        process_event(event)

        return {"status": RESPONSE_STATUS_SUCCESS}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/booking/finalize")
def finalize_booking_request(
    background_tasks: BackgroundTasks, token: str = Depends(get_token)
) -> FinalizeBookingResponse:
    try:
        payload = verify_jwt_token(
            token, TokenScope.PROVISIONING
        )  # return the data INSIDE the token and scope
        logging.info(f"Token verified, payload: {payload}")
        stripe = create_stripe_client()
        supabase = create_supabase_client()
        setup_intent_metadata = stripe.setup_intents.retrieve(
            payload["setup_intent_id"]
        )

        if setup_intent_metadata.status != "succeeded":
            raise HTTPException(status_code=409, detail="Setup intent not succeeded")

        # for retries (idempotency) - if the setup intent has already been used, return preprocessed session id and
        # provisioning status
        pre_existing_session = get_session_by_setup_intent_id(
            supabase, payload["setup_intent_id"]
        )
        if len(pre_existing_session) > 0:
            logger.info(
                "Previous session found for setup intent, returning existing session ID"
            )
            session_jwt_token = create_jwt_token(
                {
                    TokenScope.SESSION.value: {
                        "session_id": pre_existing_session["id"],
                    },
                },
                TokenScope.SESSION,
            )
            return FinalizeBookingResponse(session_jwt_token=session_jwt_token)

        # now in the new provisioning flow we first create the PodSession object and add it to the database
        customer_id: str = setup_intent_metadata["customer"]
        payment_method: str = setup_intent_metadata["payment_method"]
        payment_method_data = stripe.payment_methods.retrieve(payment_method)
        customer_email = payment_method_data["billing_details"]["email"]

        start_time = datetime.now(timezone.utc)

        session = PodSession(
            pod_id=payload["pod_id"],
            user_email=customer_email,
            start_time=start_time,
            stripe_customer_id=customer_id,
            stripe_payment_method=payment_method,
            access_code_id=None,  # we will set this later
            setup_intent_id=setup_intent_metadata["id"],
        )

        add_session(supabase, session)

        if not session.id:
            raise RuntimeError("Failed to add session to the database")

        provision = SessionProvision(
            provision_id=payload["provisioning_id"],
            session_id=session.id,
            status=ProvisionStatus.PENDING,
        )

        add_provisioning(supabase, provision)

        # pod should now be blocked from bookings while we generate the
        # access code to prevent anyone else from booking it
        update_pod_status(supabase, session.pod_id, True)

        logger.info(
            f"Pod {session.pod_id} marked as in use, queuing access code provisioning job"
        )
        # queue the background job to generate the access code and send the email
        session_jwt_token = create_jwt_token(
            {
                TokenScope.SESSION.value: {
                    "session_id": session.id,
                },
            },
            TokenScope.SESSION,
        )

        session_metadata = SessionProvisioningJobMetadata(
            jwt_token=session_jwt_token,
            session_id=session.id,
            use_static_codes=use_static_codes,
        )

        background_tasks.add_task(provision_access_code_job, session_metadata)
        return FinalizeBookingResponse(session_jwt_token=session_jwt_token)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/provisioning-status")
def get_provisioning_status_request(
    token: str = Depends(get_token),
) -> ProvisioningStatusResponse:
    try:
        payload = verify_jwt_token(
            token, TokenScope.SESSION
        )  # return the data INSIDE the token and scope
        logging.info(f"Token verified, payload: {payload}")
        supabase = create_supabase_client()
        provisioning = get_provisioning_by_session_id(supabase, payload["session_id"])
        if not provisioning:
            raise RuntimeError("Provisioning not found for session ID")

        if ProvisionStatus(provisioning["status"]) in {
            ProvisionStatus.PENDING,
            ProvisionStatus.FAILED,
        }:
            return ProvisioningStatusResponse(
                status=provisioning["status"], access_code=None
            )

        # only here if the provision status is READY
        session = get_session(supabase, payload["session_id"])
        if not session.get("access_code_id"):
            raise RuntimeError("Access code ID not found for session")
        access_code = int(get_access_code(session["access_code_id"]))
        return ProvisioningStatusResponse(
            status=provisioning["status"], access_code=access_code
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/get-session-data")
def get_session_data_request(token: str = Depends(get_token)) -> SessionDataResponse:
    try:
        payload = verify_jwt_token(token, TokenScope.SESSION)
        supabase = create_supabase_client()
        session_metadata = get_session(supabase, payload["session_id"])
        pod = get_pod_by_id(supabase, session_metadata["pod_id"])
        session_data = SessionData(
            start_dt=session_metadata["start_time"],
            end_dt=session_metadata.get("end_time"),
            access_code=(
                None
                if session_metadata.get("end_time")
                else int(
                    StaticCodeManager().decrypt_code(session_metadata["access_code_id"])
                    if use_static_codes
                    else get_access_code(session_metadata["access_code_id"])
                )
            ),
        )
        pod_data = PodData(
            name=pod["name"], address=pod["address"], price_per_minute=pod["price"]
        )
        return SessionDataResponse(session_data=session_data, pod_data=pod_data)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@deprecated(
    "We are now following a provisioning flow that uses JWTs to manage sessions"
)
@app.post("/api/confirm-booking")
def confirm_booking_request(request: ConfirmBookingRequest) -> DictWithStringKeys:
    try:
        client = create_stripe_client()
        supabase = create_supabase_client()
        setup_intent_metadata = client.setup_intents.retrieve(request.setup_intent_id)

        customer_id: str = setup_intent_metadata["customer"]
        pod_id: str = setup_intent_metadata["metadata"].get("pod_id")
        pod: DictWithStringKeys = get_pod_by_id(supabase, pod_id)

        payment_method: str = setup_intent_metadata["payment_method"]
        payment_method_data = client.payment_methods.retrieve(payment_method)
        customer_email = payment_method_data["billing_details"]["email"]

        logger.info(f"Creating session for pod {pod_id} with customer {customer_email}")

        start_time = datetime.now(timezone.utc)
        access_code_id = (
            StaticCodeManager().random_encrypted_access_code_id()
            if use_static_codes
            else set_access_code(start_time)
        )

        session = PodSession(
            pod_id=pod["id"],
            user_email=customer_email,
            start_time=start_time,
            stripe_customer_id=customer_id,
            stripe_payment_method=payment_method,
            access_code_id=access_code_id,
            setup_intent_id=setup_intent_metadata["id"],
        )

        logger.info(
            f"Attempting to add session for customer {customer_email} to pod {pod_id}"
        )

        add_session(supabase, session)

        if not session.id:
            raise RuntimeError("Failed to add session to the database")

        update_pod_status(supabase, session.pod_id, True)

        logger.info(f"Updated pod status for {session.pod_id} to in use")

        access_code = (
            StaticCodeManager().decrypt_code(access_code_id)
            if use_static_codes
            else get_access_code(access_code_id)
        )

        booking = SessionDetails(
            session_token=session.id,
            pod_name=pod["name"],
            address=pod["address"],
            start_time=start_time,
            access_code=access_code,
        )

        logger.info(
            f"Sending access email to {customer_email} for booking {booking.session_token}"
        )

        send_access_email(customer_email, booking)

        return {"status": RESPONSE_STATUS_SUCCESS, "access_code": access_code}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/end-session-preview")
def end_session_preview_request(session_id: str) -> DictWithStringKeys:
    try:
        supabase = create_supabase_client()
        session_metadata = get_session(supabase, session_id)
        pod = get_pod_by_id(supabase, session_metadata["pod_id"])
        session_cost = get_session_cost(pod, session_metadata)

        return {
            "is_complete": session_metadata["end_time"] is not None,
            "pod_name": pod["name"],
            "start_time": session_metadata["start_time"],
            "end_time": session_metadata.get("end_time"),
            "cost": round(session_cost, 2),
        }
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.get("/api/is-session-complete")
def get_session_status_request(session_id: str) -> DictWithStringKeys:
    try:
        supabase = create_supabase_client()
        logger.info(f"Fetching session metadata for session ID: {session_id}")
        session_metadata = get_session(supabase, session_id)

        return {"is_complete": session_metadata["end_time"] is not None}
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.get("/api/lock-status")
def get_lock_status_request(device_id: str | None) -> GetLockStatusResponse:
    try:
        status = False
        if use_static_codes:
            # for supporting static door codes for MVP
            return GetLockStatusResponse(is_locked=True)
        if device_id:
            status = is_device_locked(device_id)
        else:
            status = is_device_locked()
        return GetLockStatusResponse(is_locked=status)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.post("/api/end-session")
def end_session_request(
    background_tasks: BackgroundTasks, token: str = Depends(get_token)
) -> EndSessionResponse:
    try:
        payload = verify_jwt_token(token, TokenScope.SESSION)
        session_id = payload["session_id"]
        logger.info(f"Ending session for session ID: {session_id}")
        supabase = create_supabase_client()

        session_metadata = get_session(supabase, session_id)
        logger.info(f"Retrieved session metadata for {session_id}")

        pod = get_pod_by_id(supabase, session_metadata["pod_id"])
        logger.info(f"Retrieved pod metadata for {session_metadata['pod_id']}")

        session_cost_pence = round(get_session_cost(pod, session_metadata) * 100)
        logger.info(f"Calculated session cost: {session_cost_pence} pence")

        logger.info("Attempting to charge user")
        charge_user(session_metadata, session_cost_pence)

        logger.info("Charging user successful, ending session on database-side")
        end_session(supabase, session_id)

        logger.info(
            "Session ended on database-side successfully, now kicking off background task to delete access code"
        )
        if not use_static_codes:
            background_job_metadata = SessionDeprovisioningJobMetadata(
                access_code_id=session_metadata["access_code_id"],
                pod_id=session_metadata["pod_id"],
            )
            background_tasks.add_task(
                deprovision_access_code_job, background_job_metadata
            )

        return EndSessionResponse(status=RESPONSE_STATUS_SUCCESS)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
