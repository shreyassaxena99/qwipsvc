import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from svc.custom_types import DictWithStringKeys
from svc.database_accessor import (create_supabase_client, end_session,
                                   get_pod_by_id, get_session,
                                   update_pod_status)
from svc.env import log_level
from svc.models import (CreateSetupIntentResponse, EndSessionRequest,
                        GetPodResponse)
from svc.payments_manager import (charge_user, create_stripe_event,
                                  get_user_data, process_event)
from svc.utils import get_session_cost

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


@app.post("/api/end-session")
def end_session_request(request: EndSessionRequest) -> DictWithStringKeys:
    try:
        supabase = create_supabase_client()

        session_metadata = get_session(supabase, request.session_id)
        logger.info("Retrieved session metadata for {request.session_id}")

        pod = get_pod_by_id(supabase, session_metadata["pod_id"])
        logger.info(f"Retrieved pod metadata for {session_metadata['pod_id']}")

        session_cost_pence = round(get_session_cost(pod, session_metadata) * 100)
        logger.info(f"Calculated session cost: {session_cost_pence} pence")

        logger.info("Attempting to charge user")
        charge_user(session_metadata, session_cost_pence)

        logger.info("Charging user successful, ending session")
        end_session(supabase, request.session_id)

        logger.info("Session ended successfully, updating pod status")
        update_pod_status(supabase, session_metadata["pod_id"], False)
        return {"status": RESPONSE_STATUS_SUCCESS}
    except Exception as e:
        return {"status": RESPONSE_STATUS_FAILED, "error": str(e)}
