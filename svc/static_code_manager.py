import base64
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from svc.env import static_code_b64_key
import random


class StaticCodeManager:
    """
    Class for managing Static Codes which will be used during popups. Long term solution is to use seam
    for provisioning and deprovisioning access codes live
    """

    def __init__(self, b64_key: str | None = static_code_b64_key):
        if b64_key is None:
            raise ValueError(
                "STATIC_CODE_B64_KEY environment variable must be set to use StaticCodeManager"
            )
        self.key = base64.urlsafe_b64decode(b64_key)
        self.static_codes = [14231, 33421, 21443, 14243, 34211, 12344]

    def _encrypt_code(self, code: int) -> str:
        """
        Returns a token we can store as access_code_id.
        """
        code_str = f"{code:05d}"  # ensure 5 digits
        aesgcm = AESGCM(self.key)
        nonce = os.urandom(12)  # 96-bit nonce recommended for GCM
        ciphertext = aesgcm.encrypt(
            nonce, code_str.encode("utf-8"), associated_data=None
        )
        token = base64.urlsafe_b64encode(nonce + ciphertext).decode("utf-8")
        return token

    def random_encrypted_access_code_id(self) -> str:
        """
        Picks a random code from pre-defined list of static codes and returns an encrypted access_code_id (base64url).

        - `codes`: unencrypted list like [14231, 33421, ...]
        - `key_b64`: optional base64url key override (otherwise reads ACCESS_CODE_KEY_B64 env var)
        """
        if not self.static_codes:
            raise ValueError("codes list is empty")

        chosen = random.choice(self.static_codes)
        code_str = f"{int(chosen):05d}"  # always 5 digits

        aesgcm = AESGCM(self.key)
        nonce = os.urandom(12)  # 96-bit nonce
        ciphertext = aesgcm.encrypt(
            nonce, code_str.encode("utf-8"), associated_data=None
        )

        # Store nonce + ciphertext as URL-safe token
        return base64.urlsafe_b64encode(nonce + ciphertext).decode("utf-8")

    def decrypt_code(self, token: str) -> str:
        """
        Given an access_code_id token, returns the original code.
        """
        data = base64.urlsafe_b64decode(token.encode("utf-8"))
        nonce = data[:12]
        ciphertext = data[12:]
        aesgcm = AESGCM(self.key)
        decrypted_data = aesgcm.decrypt(nonce, ciphertext, associated_data=None)
        return decrypted_data.decode("utf-8")
