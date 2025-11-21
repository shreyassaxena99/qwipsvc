from datetime import datetime
from pydantic import BaseModel


class EndSessionRequest(BaseModel):
    session_id: str


class ConfirmBookingRequest(BaseModel):
    setup_intent_id: str


class GetPodResponse(BaseModel):
    name: str
    address: str
    price_per_minute: float
    in_use: bool


class CreateSetupIntentResponse(BaseModel):
    client_secret: str


class BookingDetails(BaseModel):
    booking_id: str
    pod_name: str
    address: str
    start_time: datetime
    access_code: str


class PodSession(BaseModel):
    id: str | None = None
    pod_id: str
    user_email: str
    start_time: datetime
    stripe_customer_id: str
    stripe_payment_method: str
    access_code_id: str
    setup_intent_id: str
