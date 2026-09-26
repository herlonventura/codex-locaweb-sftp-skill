"""Select a native OS vault directly, avoiding plaintext/Chainer plugins."""

import sys

from pydantic import SecretStr

from .base import CredentialError, CredentialKey, decode_secret, encode_secret

SERVICE = "vhe-deploy/v1"


def _native_backend():
    if sys.platform == "win32":
        from keyring.backends.Windows import WinVaultKeyring
        return WinVaultKeyring()
    if sys.platform == "darwin":
        from keyring.backends.macOS import Keyring
        return Keyring()
    if sys.platform.startswith("linux"):
        from keyring.backends.SecretService import Keyring
        return Keyring()
    raise CredentialError("No supported native keyring for this OS; explicitly choose age or environment.")


class KeyringStore:
    kind = "keyring"

    def __init__(self):
        try:
            self._backend = _native_backend()
            if self._backend.priority <= 0:
                raise CredentialError("Native keyring unavailable.")
        except Exception:
            raise CredentialError("Native keyring unavailable; no fallback was attempted.") from None

    def get(self, key: CredentialKey) -> SecretStr:
        try:
            payload = self._backend.get_password(SERVICE, key.digest)
            if not isinstance(payload, str):
                raise ValueError("Missing.")
            return decode_secret(key, payload.encode("utf-8"))
        except Exception:
            raise CredentialError("Cannot read this credential from the native keyring.") from None

    def set(self, key: CredentialKey, value: SecretStr) -> None:
        payload = encode_secret(key, value)
        try:
            self._backend.set_password(SERVICE, key.digest, payload.decode("utf-8"))
        except Exception:
            raise CredentialError("Cannot store credential in the native keyring.") from None
