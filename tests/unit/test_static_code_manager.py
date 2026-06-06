import base64

import pytest

from svc.static_code_manager import StaticCodeManager

# 32 null bytes → valid AES-256 key
_TEST_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
_OTHER_KEY = base64.urlsafe_b64encode(b"\x01" * 32).decode()


def _manager() -> StaticCodeManager:
    return StaticCodeManager(b64_key=_TEST_KEY)


def test_encrypt_decrypt_roundtrip():
    mgr = _manager()
    token = mgr._encrypt_code(12345)
    assert mgr.decrypt_code(token) == "12345"


def test_code_padded_to_5_digits():
    mgr = _manager()
    token = mgr._encrypt_code(7)
    assert mgr.decrypt_code(token) == "00007"


def test_random_code_decrypts_to_known_code():
    mgr = _manager()
    token = mgr.random_encrypted_access_code_id()
    code = int(mgr.decrypt_code(token))
    assert code in mgr.static_codes


def test_random_code_result_is_5_digits():
    mgr = _manager()
    token = mgr.random_encrypted_access_code_id()
    assert len(mgr.decrypt_code(token)) == 5


def test_different_encryptions_of_same_code_differ():
    # Each call uses a fresh random nonce, so ciphertext must differ.
    mgr = _manager()
    assert mgr._encrypt_code(11111) != mgr._encrypt_code(11111)


def test_wrong_key_cannot_decrypt():
    mgr = _manager()
    token = mgr._encrypt_code(12345)
    wrong_mgr = StaticCodeManager(b64_key=_OTHER_KEY)
    with pytest.raises(Exception):
        wrong_mgr.decrypt_code(token)


def test_missing_key_raises():
    with pytest.raises(ValueError, match="STATIC_CODE_B64_KEY"):
        StaticCodeManager(b64_key=None)


def test_static_codes_loaded_from_file():
    mgr = _manager()
    assert len(mgr.static_codes) > 0
    assert all(isinstance(c, int) for c in mgr.static_codes)
