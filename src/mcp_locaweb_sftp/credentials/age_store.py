"""age CLI integration: only ciphertext reaches files; plaintext uses pipes."""

import os
from pathlib import Path
import re
import stat
import subprocess

from pydantic import SecretStr

from ..local_files import checked_path, read_limited, write_private
from .base import CredentialError, CredentialKey, decode_secret, encode_secret


class AgeStore:
    kind = "age"

    def __init__(self, *, directory: Path, executable: Path, recipient: str | None = None,
                 identity: Path | None = None):
        try:
            if not Path(executable).is_absolute():
                raise ValueError("An explicit trusted age executable is required.")
            self._executable = checked_path(executable)
            if not self._executable.is_file():
                raise ValueError("Missing executable.")
            self._directory = checked_path(directory)
            if not self._directory.is_dir():
                raise ValueError("Provision a private credential directory first.")
            if recipient is not None and not re.fullmatch(r"age1[0-9a-z]{58}", recipient):
                raise ValueError("Only native X25519 recipients are accepted; no plugins.")
            self._recipient = recipient
            self._identity = checked_path(identity) if identity is not None else None
            if self._identity is not None:
                data = read_limited(self._identity, 16384).decode("ascii")
                lines = [line.strip() for line in data.splitlines() if line.strip() and not line.startswith("#")]
                if len(lines) != 1 or not re.fullmatch(r"AGE-SECRET-KEY-1[0-9A-Z]{58}", lines[0]):
                    raise ValueError("Only a single native X25519 identity is supported.")
            if os.name != "nt":
                for path in (self._directory, self._identity):
                    if path is not None and stat.S_IMODE(path.stat().st_mode) & 0o077:
                        raise ValueError("Private directory/identity permissions are too broad.")
        except (OSError, ValueError):
            raise CredentialError("Invalid age setup; check executable, private directory, identity and recipient.") from None

    def _run(self, arguments: list[str], data: bytes) -> bytes:
        try:
            result = subprocess.run([str(self._executable), *arguments], input=data, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, timeout=15, check=False, shell=False,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            if result.returncode != 0 or len(result.stdout) > 256 * 1024:
                raise ValueError("age failed.")
            return result.stdout
        except (OSError, subprocess.SubprocessError, ValueError):
            # Process errors can include decrypted stdout. Never propagate them.
            raise CredentialError("age operation failed; no plaintext diagnostics were exposed.") from None

    def set(self, key: CredentialKey, value: SecretStr) -> None:
        if self._recipient is None:
            raise CredentialError("Configure an age recipient before storing a credential.")
        ciphertext = self._run(["--encrypt", "--recipient", self._recipient], encode_secret(key, value))
        if not ciphertext.startswith(b"age-encryption.org/v1\n"):
            raise CredentialError("age did not return the expected encrypted format.")
        try:
            write_private(self._directory / (key.digest + ".age"), ciphertext, replace=True)
        except (OSError, ValueError):
            raise CredentialError("Cannot persist the encrypted credential.") from None

    def get(self, key: CredentialKey) -> SecretStr:
        if self._identity is None:
            raise CredentialError("Configure an age identity before reading a credential.")
        try:
            checked_path(self._identity)
            ciphertext = read_limited(self._directory / (key.digest + ".age"), 256 * 1024)
        except (OSError, ValueError):
            raise CredentialError("Cannot read the encrypted credential.") from None
        payload = self._run(["--decrypt", "--identity", str(self._identity)], ciphertext)
        return decode_secret(key, payload)
