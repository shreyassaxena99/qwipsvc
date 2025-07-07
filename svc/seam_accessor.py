import uuid


def get_access_code() -> str:
    return str(uuid.uuid4())[:6]
