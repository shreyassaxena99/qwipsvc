import logging
from datetime import datetime, timezone
from functools import cache

from stripe import Event, SetupIntent, StripeClient, Webhook

from svc.custom_types import DictWithStringKeys
from svc.database_accessor import (add_session, create_supabase_client,
                                   get_pod_by_id, update_pod_status)
from svc.email_manager import send_access_email
from svc.env import stripe_api_key, stripe_webhook_secret
from svc.models import BookingDetails, PodSession
from svc.seam_accessor import get_access_code

SETUP_INTENT_SUCCEDED_EVENT = "setup_intent.succeeded"

logger = logging.getLogger(__name__)


@cache
def _create_stripe_client() -> StripeClient:
    logger.debug("Creating Stripe client")
    return StripeClient(stripe_api_key)


def get_user_data(pod_id: str) -> SetupIntent:
    """We create a setup intent for the user which we will use to save their metadata
    and payment methods for future billing.

    Args:
        pod_name (str): name of the pod being used

    Returns:
        SetupIntent: Stripe SetupIntent Object
    """
    logger.info(f"Creating setup intent for user attempting to use pod: {pod_id}")
    client = _create_stripe_client()
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
    client = _create_stripe_client()
    if event.type == SETUP_INTENT_SUCCEDED_EVENT:
        _process_setup_intent_success(client, event)
    else:
        raise ValueError(f"Unhandled event type: {event.type}")


def _process_setup_intent_success(client: StripeClient, event: Event) -> None:
    """Logic for processing a successful setup intent event. Once received we take event metadata
    including customer email and make some updates to the database, generate access code and
    send email to the user on the email address provided via Stripe.
    """
    logger.info("Processing setup intent success event")
    event_metadata = event.data.object
    supabase = create_supabase_client()

    customer_id: str = event_metadata["customer"]

    pod_id: str = event_metadata["metadata"].get("pod_id")
    pod: DictWithStringKeys = get_pod_by_id(supabase, pod_id)

    payment_method: str = event_metadata["payment_method"]
    payment_method_data = client.payment_methods.retrieve(payment_method)
    customer_email = payment_method_data["billing_details"]["email"]

    logger.info(f"Creating session for pod {pod_id} with customer {customer_email}")

    access_code = get_access_code()

    start_time = datetime.now(timezone.utc)

    session = PodSession(
        pod_id=pod["id"],
        user_email=customer_email,
        start_time=start_time,
        stripe_customer_id=customer_id,
        stripe_payment_method=payment_method,
        access_code=access_code,
    )

    logger.info(
        f"Attempting to add session for customer {customer_email} to pod {pod_id}"
    )

    add_session(supabase, session)

    if not session.id:
        raise RuntimeError("Failed to add session to the database")

    update_pod_status(supabase, session.pod_id, True)

    logger.info(f"Updated pod status for {session.pod_id} to in use")

    booking = BookingDetails(
        booking_id=session.id,
        address=pod["address"],
        start_time=start_time,
        access_code=access_code,
    )

    logger.info(
        f"Sending access email to {customer_email} for booking {booking.booking_id}"
    )

    send_access_email(customer_email, booking)


def charge_user(session: DictWithStringKeys, cost_in_pence: int) -> None:
    """Charge the user for the amount that they have booked the pod for by creating a payment
    intent and confirming it at the same time.

    Args:
        session (DictWithStringKeys): Session Metadata
        cost_in_pence (int): Session Cost
    """
    logger.info(
        f"Charging user for session {session['id']} with cost {cost_in_pence} pence"
    )
    _create_stripe_client().payment_intents.create(
        params={
            "customer": session["stripe_customer_id"],
            "payment_method": session["stripe_payment_method"],
            "amount": cost_in_pence,
            "currency": "gbp",
            "confirm": True,
            "off_session": True,
        }
    )
