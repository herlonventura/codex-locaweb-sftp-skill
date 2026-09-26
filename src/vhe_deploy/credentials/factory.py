"""Explicit validated selection; a failing provider is never silently replaced."""

from ..config.schema import Settings, Site, native_path
from .age_store import AgeStore
from .base import CredentialError, CredentialStore
from .env_store import EnvStore
from .keyring_store import KeyringStore


def selected_store(site: Site, settings: Settings) -> CredentialStore:
    if site.credential_store == "keyring":
        return KeyringStore()
    if site.credential_store == "env":
        return EnvStore()
    if settings.age is None:
        raise CredentialError("The selected age provider requires explicit settings.")
    config = settings.age
    try:
        return AgeStore(directory=native_path(config.directory), executable=native_path(config.executable),
                        identity=native_path(config.identity) if config.identity else None, recipient=config.recipient)
    except ValueError:
        raise CredentialError("age paths are not valid for this operating system.") from None
