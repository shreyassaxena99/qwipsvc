from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest

from svc.custom_types import TokenScope
from svc.jwt_manager import create_jwt_token, get_expiration_time, verify_jwt_token

_SECRET = "test-jwt-secret-key-for-unit-tests-only"


def _session_token(session_id: str = "sess_abc") -> str:
    return create_jwt_token(
        {TokenScope.SESSION.value: {"session_id": session_id}},
        TokenScope.SESSION,
        secret_key=_SECRET,
    )


def _provisioning_token(
    setup_intent_id="si_test", pod_id="pod_1", provisioning_id="prov_1"
) -> str:
    return create_jwt_token(
        {
            TokenScope.PROVISIONING.value: {
                "setup_intent_id": setup_intent_id,
                "pod_id": pod_id,
                "provisioning_id": provisioning_id,
            }
        },
        TokenScope.PROVISIONING,
        secret_key=_SECRET,
    )


def test_session_token_round_trip():
    token = _session_token("my-session-id")
    payload = verify_jwt_token(token, TokenScope.SESSION, secret_key=_SECRET)
    assert payload["session_id"] == "my-session-id"


def test_provisioning_token_round_trip():
    token = _provisioning_token()
    payload = verify_jwt_token(token, TokenScope.PROVISIONING, secret_key=_SECRET)
    assert payload["setup_intent_id"] == "si_test"
    assert payload["pod_id"] == "pod_1"
    assert payload["provisioning_id"] == "prov_1"


def test_wrong_scope_raises():
    session_token = _session_token()
    with pytest.raises(ValueError, match="Invalid token"):
        verify_jwt_token(session_token, TokenScope.PROVISIONING, secret_key=_SECRET)


def test_expired_token_raises():
    payload = {
        TokenScope.SESSION.value: {"session_id": "x"},
        "exp": datetime.now(timezone.utc) - timedelta(seconds=1),
    }
    token = pyjwt.encode(payload, _SECRET, algorithm="HS256")
    with pytest.raises(ValueError, match="expired"):
        verify_jwt_token(token, TokenScope.SESSION, secret_key=_SECRET)


def test_wrong_secret_raises():
    token = _session_token()
    with pytest.raises(ValueError):
        verify_jwt_token(token, TokenScope.SESSION, secret_key="totally-wrong-secret")


def test_tampered_token_raises():
    token = _session_token()
    bad = token[:-5] + "XXXXX"
    with pytest.raises(ValueError):
        verify_jwt_token(bad, TokenScope.SESSION, secret_key=_SECRET)


def test_provisioning_ttl_is_10_minutes():
    assert get_expiration_time(TokenScope.PROVISIONING) == 600


def test_session_ttl_is_3_hours():
    assert get_expiration_time(TokenScope.SESSION) == 10800


def test_unknown_scope_raises():
    with pytest.raises(ValueError):
        get_expiration_time("not_a_scope")  # type: ignore
