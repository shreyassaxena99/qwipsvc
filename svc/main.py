from datetime import datetime, timezone
import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from svc.custom_types import DictWithStringKeys
from svc.database_accessor import (
    add_session,
    create_supabase_client,
    end_session,
    get_pod_by_id,
    get_session,
    update_pod_status,
    get_access_code_id_for_setup_intent_id,
)
from svc.email_manager import send_access_email
from svc.env import log_level
from svc.models import (
    BookingDetails,
    CreateSetupIntentResponse,
    EndSessionRequest,
    GetPodResponse,
    ConfirmBookingRequest,
    PodSession,
)
from svc.payments_manager import (
    charge_user,
    create_stripe_client,
    create_stripe_event,
    get_user_data,
    process_event,
)
from svc.utils import get_session_cost
from svc.seam_accessor import delete_access_code, get_access_code, set_access_code

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


@app.get("/api/create-setup-intent")
def create_setup_intent_request(pod_id: str) -> CreateSetupIntentResponse:
    try:
        setup_intent = get_user_data(pod_id)
        if not setup_intent.client_secret:
            raise RuntimeError("Failed to create setup intent")
        return CreateSetupIntentResponse(client_secret=setup_intent.client_secret)
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
            event = create_stripe_event(payload, signature_header)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        process_event(event)

        return {"status": RESPONSE_STATUS_SUCCESS}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


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
        access_code_id = set_access_code(start_time)

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

        access_code = get_access_code(access_code_id)

        booking = BookingDetails(
            booking_id=session.id,
            pod_name=pod["name"],
            address=pod["address"],
            start_time=start_time,
            access_code=access_code,
        )

        logger.info(
            f"Sending access email to {customer_email} for booking {booking.booking_id}"
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
            "pod_name": pod["name"],
            "start_time": session_metadata["start_time"],
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

        return {"session_status": session_metadata["end_time"] is not None}
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.post("/api/end-session")
def end_session_request(request: EndSessionRequest) -> DictWithStringKeys:
    try:
        supabase = create_supabase_client()

        session_metadata = get_session(supabase, request.session_id)
        logger.info(f"Retrieved session metadata for {request.session_id}")

        pod = get_pod_by_id(supabase, session_metadata["pod_id"])
        logger.info(f"Retrieved pod metadata for {session_metadata['pod_id']}")

        session_cost_pence = round(get_session_cost(pod, session_metadata) * 100)
        logger.info(f"Calculated session cost: {session_cost_pence} pence")

        logger.info("Attempting to charge user")
        charge_user(session_metadata, session_cost_pence)

        logger.info("Charging user successful, ending session on database-side")
        end_session(supabase, request.session_id)

        logger.info(
            "Session ended on database-side successfully, now deleting access code"
        )
        delete_access_code(session_metadata["access_code_id"])

        logger.info("Access code deleted successfully, updating pod status")
        update_pod_status(supabase, session_metadata["pod_id"], False)
        return {"status": RESPONSE_STATUS_SUCCESS}
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
