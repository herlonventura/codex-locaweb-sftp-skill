from dataclasses import dataclass
import hashlib
import json
import re
from typing import Protocol

from pydantic import SecretStr

from ..config.schema import Site
from ..core.guards import normalize_domain


class CredentialError(RuntimeError):
    """Public provider errors must not contain secret values or raw stderr."""


@dataclass(frozen=True)
class CredentialKey:
    digest: str

    def __post_init__(self):
        if not re.fullmatch(r"[a-f0-9]{64}", self.digest):
            raise ValueError("Invalid credential binding.")

    @classmethod
    def for_site(cls, domain: str, site: Site):
        fields = [normalize_domain(domain), site.protocol, site.host, site.port, site.user,
                  site.remote_root, site.ssh_fingerprint, site.ca_file]
        encoded = json.dumps(fields, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        return cls(hashlib.sha256(encoded).hexdigest())

    @property
    def env_name(self):
        return "MCP_LOCAWEB_PASSWORD_" + self.digest.upper()


class CredentialStore(Protocol):
    kind: str

    def get(self, key: CredentialKey) -> SecretStr: ...


def secret(value: str | None) -> SecretStr:
    if not isinstance(value, str) or not value or len(value) > 16384 or any(c in value for c in "\r\n\x00"):
        raise CredentialError("Credential missing or invalid; enroll it in the selected provider.")
    return SecretStr(value)


def encode_secret(key: CredentialKey, value: SecretStr) -> bytes:
    if not isinstance(value, SecretStr):
        raise CredentialError("Supply a SecretStr; plaintext arguments are not accepted by the store.")
    validated = secret(value.get_secret_value())
    return json.dumps({"version": 1, "binding": key.digest, "password": validated.get_secret_value()},
                      ensure_ascii=True, separators=(",", ":")).encode("utf-8")


def decode_secret(key: CredentialKey, payload: bytes) -> SecretStr:
    try:
        data = json.loads(payload)
        if set(data) != {"version", "binding", "password"} or type(data["version"]) is not int or data["version"] != 1 or data["binding"] != key.digest:
            raise ValueError("Binding mismatch.")
        return secret(data["password"])
    except (ValueError, TypeError, RecursionError, CredentialError):
        raise CredentialError("Credential envelope is invalid or belongs to another connection.") from None
