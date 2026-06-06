import logging

from supabase import Client

logger = logging.getLogger(__name__)

_BUCKET = "checkout-photos"
_EXT_MAP = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


def upload_checkout_photo(
    client: Client, session_id: str, file_bytes: bytes, content_type: str
) -> str:
    path = f"{session_id}/checkout.{_EXT_MAP[content_type]}"
    logger.info(f"Uploading checkout photo for session {session_id} to {path}")
    client.storage.from_(_BUCKET).upload(
        path, file_bytes, {"content-type": content_type, "upsert": "true"}
    )
    return client.storage.from_(_BUCKET).get_public_url(path)
