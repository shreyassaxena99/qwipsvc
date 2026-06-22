from typing import Any, Dict
from enum import Enum 

class TokenScope(Enum):
    PROVISIONING = "provisioning"
    SESSION = "session"
    FEEDBACK = "feedback"

class ProvisionStatus(Enum):
    DRAFT = "draft"
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"
    EXPIRED = "expired"

DictWithStringKeys = Dict[str, Any]
