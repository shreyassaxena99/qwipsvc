from pydantic import BaseModel


class EndSessionRequest(BaseModel):
    session_id: str


class GetPodResponse(BaseModel):
    name: str
    address: str
    price_per_minute: float
    in_use: bool


class CreateSetupIntentResponse(BaseModel):
    client_secret: str


class BookingDetails(BaseModel):
    booking_id: str
    address: str
    start_time: str  # ISO 8601 format
    access_code: int


class PodSession(BaseModel):
    id: str | None = None
    pod_id: str
    user_email: str
    start_time: str  # ISO 8601 format
    stripe_customer_id: str
    stripe_payment_method: str
    access_code: int
