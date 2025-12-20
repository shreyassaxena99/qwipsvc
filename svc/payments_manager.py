import logging
from datetime import datetime, timezone
from functools import cache

from stripe import Event, SetupIntent, StripeClient, Webhook

from svc.custom_types import DictWithStringKeys
from svc.database_accessor import (
    add_session,
    create_supabase_client,
    get_pod_by_id,
    update_pod_status,
)
from svc.email_manager import send_access_email
from svc.env import stripe_api_key, stripe_webhook_secret
from svc.models import SessionDetails, PodSession
from svc.seam_accessor import set_access_code, get_access_code

SETUP_INTENT_SUCCEDED_EVENT = "setup_intent.succeeded"

logger = logging.getLogger(__name__)


@cache
def create_stripe_client() -> StripeClient:
    logger.debug("Creating Stripe client")
    if not stripe_api_key:
        raise ValueError("Stripe API key not set")
    return StripeClient(stripe_api_key)


def create_setup_intent(pod_id: str) -> SetupIntent:
    """We create a setup intent for the user which we will use to save their metadata
    and payment methods for future billing.

    Args:
        pod_name (str): name of the pod being used

    Returns:
        SetupIntent: Stripe SetupIntent Object
    """
    logger.info(f"Creating setup intent for user attempting to use pod: {pod_id}")
    client = create_stripe_client()
    customer = client.customers.create()
    return client.setup_intents.create(
        params={
            "customer": customer.id,
            "usage": "off_session",
            "metadata": {"pod_id": pod_id},
        },
    )


def create_stripe_event(payload: dict, header: str | None) -> Event:
    """Once the user has registered their card details successfully, Stripe will send
    some kind of event to us over a webhook. This function creates that event server-side
    ready to be processed.

    Args:
        payload (dict): Event payload coming through the webhook
        header (str | None): Header of the event propagated over the webhook

    Returns:
        Event: A Stripe Event ready to be processed
    """
    return Webhook.construct_event(payload, header, stripe_webhook_secret)


def process_event(event: Event) -> None:
    logger.info(f"Unhandled event type: {event.type}")


def charge_user(session: DictWithStringKeys, cost_in_pence: int) -> None:
    """Charge the user for the amount that they have booked the pod for by creating a payment
    intent and confirming it at the same time.

    Args:
        session (DictWithStringKeys): Session Metadata
        cost_in_pence (int): Session Cost
    """
    if cost_in_pence < 30:
        logger.error(
            "Cost must be at least 30 pence to cover Stripe fees, not charging the user"
        )
        return

    logger.info(
        f"Charging user for session {session['id']} with cost {cost_in_pence} pence"
    )
    create_stripe_client().payment_intents.create(
        params={
            "customer": session["stripe_customer_id"],
            "payment_method": session["stripe_payment_method"],
            "amount": cost_in_pence,
            "currency": "gbp",
            "confirm": True,
            "off_session": True,
        }
    )
