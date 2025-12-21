from svc.jwt_manager import create_jwt_token
from svc.custom_types import TokenScope

TEST_SESSION_ID = "test_session_id"

jwt_token = create_jwt_token(
    {
        TokenScope.SESSION.value: {
            "session_id": TEST_SESSION_ID,
        },
    },
    TokenScope.SESSION,
    "your token secret",
)

print(jwt_token)
