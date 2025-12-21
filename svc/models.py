from datetime import datetime
from pydantic import BaseModel

from svc.custom_types import ProvisionStatus


class EndSessionRequest(BaseModel):
    session_id: str


class ConfirmBookingRequest(BaseModel):
    setup_intent_id: str


class GetPodResponse(BaseModel):
    name: str
    address: str
    price_per_minute: float
    in_use: bool


class FinalizeBookingResponse(BaseModel):
    session_jwt_token: str


class SetupIntentRequest(BaseModel):
    pod_id: str


class ProvisioningStatusResponse(BaseModel):
    status: str
    access_code: int | None = None


class SetupIntentResponse(BaseModel):
    client_secret: str
    provisioning_jwt_token: str


class CreateSetupIntentResponse(BaseModel):
    client_secret: str


class SessionDetails(BaseModel):
    session_token: str
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
    access_code_id: str | None = None
    setup_intent_id: str


class SessionProvision(BaseModel):
    provision_id: str
    session_id: str
    status: ProvisionStatus


class SessionProvisioningJobMetadata(BaseModel):
    jwt_token: str
    session_id: str


class SessionData(BaseModel):
    start_dt: str
    end_dt: str | None
    access_code: int | None


class PodData(BaseModel):
    name: str
    address: str
    price_per_minute: float


class SessionDataResponse(BaseModel):
    session_data: SessionData
    pod_data: PodData
