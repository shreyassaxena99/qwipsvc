from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from svc.database_accessor import (create_supabase_client, end_session,
                                   get_pod_by_id, get_session,
                                   update_pod_status)
from svc.models import EndSessionRequest
from svc.payments_manager import (charge_user, create_stripe_event,
                                  get_user_data, process_event)
from svc.custom_types import DictWithStringKeys
from svc.utils import get_session_cost

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

RESPONSE_STATUS_SUCCESS = "success"
RESPONSE_STATUS_FAILED = "failed"


@app.get("/api/create-setup-intent")
def create_setup_intent_request(pod_name: str) -> DictWithStringKeys:
    return {"client_secret": get_user_data(pod_name).client_secret}


@app.get("/api/pod")
def get_pod_request(pod_id: str) -> DictWithStringKeys:
    supabase = create_supabase_client()
    pod = get_pod_by_id(supabase, pod_id)
    return {
        "name": pod["name"],
        "address": pod["address"],
        "price_per_hour": pod["price"],
        "in_use": pod["in_use"],
    }

@app.post("/webhook/stripe")
async def stripe_webhook(request: Request) -> DictWithStringKeys:
    payload = await request.body()
    signature_header = request.headers.get("stripe-signature")
    try:
        event = create_stripe_event(payload, signature_header)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    process_event(event)

    return {"status": RESPONSE_STATUS_SUCCESS}


@app.get("/api/end-session-preview")
def end_session_preview_request(session_id: str) -> DictWithStringKeys:
    supabase = create_supabase_client()
    session_metadata = get_session(supabase, session_id)
    pod = get_pod_by_id(supabase, session_metadata["pod_id"])
    session_cost = get_session_cost(pod, session_metadata)

    return {
        "pod_name": pod["name"],
        "start_time": session_metadata["start_time"],
        "cost": round(session_cost, 2),
    }


@app.post("/api/end-session")
def end_session_request(request: EndSessionRequest) -> DictWithStringKeys:
    supabase = create_supabase_client()
    session_metadata = get_session(supabase, request.session_id)
    pod = get_pod_by_id(supabase, session_metadata["pod_id"])
    session_cost_pence = get_session_cost(pod, session_metadata) * 100

    try:
        charge_user(session_metadata, session_cost_pence)
    except Exception as e:
        return {"status": RESPONSE_STATUS_FAILED, "error": str(e)}

    end_session(supabase, request.session_id)
    update_pod_status(supabase, session_metadata["pod_id"], False)
    return {"status": RESPONSE_STATUS_SUCCESS}
