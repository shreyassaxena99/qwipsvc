from datetime import datetime, timezone
from functools import cache

from supabase import Client, create_client

from svc.env import supabase_key, supabase_url
from svc.models import PodSession
from svc.custom_types import DictWithStringKeys


class SupabaseError(RuntimeError):
    pass


@cache
def create_supabase_client() -> Client:
    return create_client(supabase_url, supabase_key)


def get_pod_by_name(client: Client, pod_name: str) -> DictWithStringKeys:
    pod_ids = client.table("pods").select("*").eq("name", pod_name).execute()
    if not pod_ids.data:
        raise SupabaseError(f"Failed to find pod with {pod_name=}")
    return pod_ids.data[0]


def get_pod_by_id(client: Client, pod_id: str) -> DictWithStringKeys:
    pod_ids = client.table("pods").select("*").eq("id", pod_id).execute()
    if not pod_ids.data:
        raise SupabaseError(f"Failed to find pod with {pod_id=}")
    return pod_ids.data[0]


def add_session(client: Client, session: PodSession) -> None:
    result = client.table("pod_sessions").insert(
        {
            "id": session.id,
            "pod_id": session.pod_id,
            "user_email": session.user_email,
            "start_time": session.start_time,
            "end_time": None,
            "stripe_customer_id": session.stripe_customer_id,
            "stripe_payment_method": session.stripe_payment_method,
            "access_code": session.access_code,
        }
    ).execute()
    session.id = result.data[0]["id"] if result.data else None


def end_session(client: Client, session_id: str) -> None:
    client.table("pod_sessions").update({"end_time": datetime.now(timezone.utc)}).eq(
        "id", session_id
    ).execute()


def update_pod_status(client: Client, pod_id: str, in_use_status: bool) -> None:
    client.table("pods").update({"in_use": in_use_status}).eq(
        "pod_id", pod_id
    ).execute()


def get_session(client: Client, session_id: str) -> DictWithStringKeys:
    matching_sessions = (
        client.table("pod_sessions").select("*").eq("session_id", session_id).execute()
    )

    if not matching_sessions.data:
        raise SupabaseError(f"No matching sessions found with {session_id=}")

    return matching_sessions.data[0]
