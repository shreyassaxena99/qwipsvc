from unittest.mock import MagicMock

import pytest

from svc.storage_accessor import upload_checkout_photo


def _client(
    url="https://test.supabase.co/storage/v1/object/public/checkout-photos/s/checkout.jpg",
):
    mock = MagicMock()
    mock.storage.from_.return_value.get_public_url.return_value = url
    return mock


def test_upload_uses_correct_bucket():
    client = _client()
    upload_checkout_photo(client, "sess_1", b"img", "image/jpeg")
    client.storage.from_.assert_called_with("checkout-photos")


def test_jpeg_maps_to_jpg_extension():
    client = _client()
    upload_checkout_photo(client, "sess_1", b"img", "image/jpeg")
    client.storage.from_.return_value.upload.assert_called_once_with(
        "sess_1/checkout.jpg",
        b"img",
        {"content-type": "image/jpeg", "upsert": "true"},
    )


def test_png_maps_to_png_extension():
    client = _client()
    upload_checkout_photo(client, "sess_2", b"img", "image/png")
    client.storage.from_.return_value.upload.assert_called_once_with(
        "sess_2/checkout.png",
        b"img",
        {"content-type": "image/png", "upsert": "true"},
    )


def test_webp_maps_to_webp_extension():
    client = _client()
    upload_checkout_photo(client, "sess_3", b"img", "image/webp")
    client.storage.from_.return_value.upload.assert_called_once_with(
        "sess_3/checkout.webp",
        b"img",
        {"content-type": "image/webp", "upsert": "true"},
    )


def test_returns_url_from_get_public_url():
    url = "https://example.com/photo.jpg"
    client = _client(url=url)
    result = upload_checkout_photo(client, "sess_4", b"img", "image/jpeg")
    assert result == url


def test_upsert_is_set():
    client = _client()
    upload_checkout_photo(client, "sess_5", b"img", "image/jpeg")
    _, _, options = client.storage.from_.return_value.upload.call_args[0]
    assert options.get("upsert") == "true"


def test_unknown_content_type_raises():
    client = _client()
    with pytest.raises(KeyError):
        upload_checkout_photo(client, "sess_6", b"img", "application/pdf")
