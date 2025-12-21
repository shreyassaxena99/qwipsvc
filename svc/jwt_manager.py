import jwt
import os
from datetime import datetime, timedelta, timezone

import logging

from svc.custom_types import DictWithStringKeys, TokenScope

SECRET_KEY = os.getenv("JWT_SECRET_KEY")
DECRYPT_ALGORITHM = "HS256"

logger = logging.getLogger(__name__)

def get_expiration_time(scope: TokenScope) -> int:
    if scope == TokenScope.PROVISIONING:
        return 10 * 60  # 10 minutes
    elif scope == TokenScope.SESSION:
        return 3 * 60 * 60  # 3 hours
    else:
        raise ValueError("Invalid token scope")


def create_jwt_token(
    payload: DictWithStringKeys, scope: TokenScope, secret_key=SECRET_KEY
) -> str:
    payload["exp"] = datetime.now(timezone.utc) + timedelta(
        seconds=get_expiration_time(scope)
    )
    token = jwt.encode(payload, secret_key, algorithm=DECRYPT_ALGORITHM)
    return token


def verify_jwt_token(
    token: str, scope: TokenScope, secret_key=SECRET_KEY
) -> DictWithStringKeys:
    try:
        logging.info(f"Verifying {token=} with scope: {scope}")
        payload = jwt.decode(token, secret_key, algorithms=[DECRYPT_ALGORITHM])
        logger.info(f"Decoded payload: {payload}")
        # sanity checks on the payload scope
        if scope.value not in payload:
            raise jwt.InvalidTokenError(
                f"Invalid token scope - expected: {scope.value}, got: {payload.get('scope')}"
            )

        # check expiration time of JWT token based on scope
        if datetime.now(timezone.utc)  > datetime.fromtimestamp(
            payload["exp"], tz=timezone.utc
        ):
            raise jwt.ExpiredSignatureError("Token has expired")
        return payload[scope.value] # return the data INSIDE THE SCOPE NAME
    except jwt.ExpiredSignatureError:
        raise ValueError("Token has expired")
    except jwt.InvalidTokenError:
        raise ValueError("Invalid token")
