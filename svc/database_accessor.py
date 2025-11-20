import logging
from datetime import datetime, timezone
from functools import cache

from postgrest.exceptions import APIError
from supabase import Client, create_client

from svc.custom_types import DictWithStringKeys
from svc.env import supabase_key, supabase_url
from svc.models import PodSession


class SupabaseError(RuntimeError):
    pass


logger = logging.getLogger(__name__)


@cache
def create_supabase_client() -> Client:
    logger.debug("Creating Supabase client")
    return create_client(supabase_url, supabase_key)


def get_pod_by_name(client: Client, pod_name: str) -> DictWithStringKeys:
    try:
        logger.debug(f"Fetching pod with name: {pod_name}")
        pod_ids = client.table("pods").select("*").eq("name", pod_name).execute()
        if not pod_ids.data:
            raise SupabaseError(f"No pods found with {pod_name=}")
        return pod_ids.data[0]
    except APIError as e:
        logger.error(f"Error fetching pod {pod_name}: {e}")
        raise SupabaseError(f"Failed to find pod with {pod_name=}") from e


def get_pod_by_id(client: Client, pod_id: str) -> DictWithStringKeys:
    try:
        logger.debug(f"Fetching pod with ID: {pod_id}")
        pod_ids = client.table("pods").select("*").eq("id", pod_id).execute()
        if not pod_ids.data:
            raise SupabaseError(f"Failed to find pod with {pod_id=}")
        return pod_ids.data[0]
    except APIError as e:
        logger.error(f"Error creating {pod_id}: {e}")
        raise SupabaseError(f"Failed to find pod with {pod_id=}") from e


def add_session(client: Client, session: PodSession) -> None:
    logger.debug(
        f"Adding session for pod {session.pod_id} and user {session.user_email}"
    )
    try:
        result = (
            client.table("pod_sessions")
            .insert(
                {
                    "pod_id": session.pod_id,
                    "user_email": session.user_email,
                    "start_time": session.start_time.isoformat(),
                    "end_time": None,
                    "stripe_customer_id": session.stripe_customer_id,
                    "stripe_payment_method": session.stripe_payment_method,
                    "access_code_id": session.access_code_id,
                    "stripe_setup_intent_id": session.stripe_setup_intent_id,
                }
            )
            .execute()
        )
        if not result.data:
            raise SupabaseError("Failed to add session to the database")
        session.id = result.data[0]["id"]
    except APIError as e:
        logger.error(f"Error adding session: {e}")
        raise SupabaseError("Failed to add session to the database") from e


def end_session(client: Client, session_id: str) -> None:
    logger.debug(f"Ending session with ID: {session_id}")
    try:
        client.table("pod_sessions").update(
            {"end_time": datetime.now(timezone.utc).isoformat()}
        ).eq("id", session_id).execute()
    except APIError as e:
        logger.error(f"Error ending session {session_id}: {e}")
        raise SupabaseError(f"Failed to end session with {session_id=}") from e


def update_pod_status(client: Client, pod_id: str, in_use_status: bool) -> None:
    logger.debug(
        f"Updating pod {pod_id} status to {'in use' if in_use_status else 'available'}"
    )
    try:
        client.table("pods").update({"in_use": in_use_status}).eq(
            "id", pod_id
        ).execute()
    except APIError as e:
        logger.error(f"Error updating pod {pod_id} status: {e}")
        raise SupabaseError(f"Failed to update pod status with {pod_id=}") from e


def get_session(client: Client, session_id: str) -> DictWithStringKeys:
    try:
        matching_sessions = (
            client.table("pod_sessions").select("*").eq("id", session_id).execute()
        )

        if not matching_sessions.data:
            raise SupabaseError(f"No matching sessions found with {session_id=}")

        return matching_sessions.data[0]
    except APIError as e:
        logger.error(f"Error fetching session {session_id}: {e}")
        raise SupabaseError(f"Failed to find session with {session_id=}") from e

def get_access_code_id_for_setup_intent_id(client: Client, setup_intent_id: str) -> str:
    try:
        matching_sessions = (
            client.table("pod_sessions")
            .select("access_code_id")
            .eq("stripe_setup_intent_id", setup_intent_id)
            .execute()
        )

        if not matching_sessions.data:
            raise SupabaseError(
                f"No matching sessions found with {setup_intent_id=}"
            )

        return matching_sessions.data[0]["access_code_id"]
    except APIError as e:
        logger.error(f"Error fetching session for setup intent {setup_intent_id}: {e}")
        raise SupabaseError(
            f"Failed to find session with {setup_intent_id=}"
        ) from e