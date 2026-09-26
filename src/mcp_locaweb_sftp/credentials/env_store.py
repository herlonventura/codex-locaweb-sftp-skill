"""Read-only secrets injected by an external process/CI secret manager."""

import os

from .base import CredentialKey, secret


class EnvStore:
    kind = "env"

    def get(self, key: CredentialKey):
        # Do not read .env files or guess shorter domain aliases.
        return secret(os.environ.get(key.env_name))
