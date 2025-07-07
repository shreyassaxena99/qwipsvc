# pylint: disable=unexpected-keyword-arg, no-value-for-parameter
import uuid
from datetime import datetime, timezone
from functools import cache

from stripe import Customer, Event, SetupIntent, StripeClient, Webhook

from svc.database_accessor import (add_session, create_supabase_client,
                                   get_pod_by_name, update_pod_status)
from svc.email_manager import send_access_email
from svc.env import stripe_api_key, stripe_webhook_secret
from svc.models import BookingDetails, PodSession
from svc.seam_accessor import get_access_code
from svc.types import DictWithStringKeys

SETUP_INTENT_SUCCEDED_EVENT = "setup_intent.succeeded"


@cache
def _create_stripe_client() -> StripeClient:
    return StripeClient(stripe_api_key)


def get_user_data(pod_name: str) -> SetupIntent:
    """We create a setup intent for the user which we will use to save their metadata
    and payment methods for future billing.

    Args:
        pod_name (str): name of the pod being used

    Returns:
        SetupIntent: Stripe SetupIntent Object
    """
    client = _create_stripe_client()
    customer = client.customers.create()
    return client.setup_intents.create(
        customer=customer.id, usage="off_session", metadata={"pod_name": pod_name}
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
    event_metadata = event.data.object
    supabase = create_supabase_client()

    customer_id: str = event_metadata["customer"]
    customer: Customer = client.customers.retrieve(customer_id)

    pod_name: str = event_metadata["metadata"].get("pod_name")
    pod: DictWithStringKeys = get_pod_by_name(supabase, pod_name)

    payment_method: str = event_metadata["payment_method"]

    access_code = get_access_code()

    start_time = datetime.now(timezone.utc)

    session = PodSession(
        pod_id=pod["id"],
        user_email=customer.email,
        start_time=start_time,
        stripe_customer_id=customer_id,
        stripe_payment_method=payment_method,
        access_code=access_code,
    )

    add_session(supabase, session)

    if not session.id:
        raise RuntimeError("Failed to add session to the database")

    update_pod_status(supabase, session.pod_id, True)

    booking = BookingDetails(
        booking_id=session.session_id,
        address=pod["address"],
        start_time=start_time,
        access_code=access_code,
    )

    send_access_email(customer.email, booking)


def charge_user(session: DictWithStringKeys, cost_in_pence: int) -> None:
    """Charge the user for the amount that they have booked the pod for by creating a payment
    intent and confirming it at the same time.

    Args:
        session (DictWithStringKeys): Session Metadata
        cost_in_pence (int): Session Cost
    """
    _create_stripe_client().payment_intents.create(
        customer=session["stripe_customer_id"],
        payment_method=session["stripe_payment_method"],
        amount=cost_in_pence,
        currency="gbp",
        confirm=True,
        off_session=True,
    )
