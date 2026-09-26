"""Resolve a registered site and its selected provider before connecting."""

from .backends import FTPSBackend, SFTPBackend
from .config.schema import Settings, Site, native_path
from .core.guards import require_registered_domain
from .credentials.base import CredentialError, CredentialKey, CredentialStore
from .credentials.factory import selected_store


def open_site(sites: dict[str, Site], domain: str, store: CredentialStore | None = None, settings: Settings | None = None):
    domain = require_registered_domain(domain, sites)
    site = sites[domain]
    site.require_connection_ready()
    settings = settings or Settings()
    store = store if store is not None else selected_store(site, settings)
    if store.kind != site.credential_store:
        raise CredentialError("Selected provider does not match site configuration; no fallback permitted.")
    key = CredentialKey.for_site(domain, site)
    password = store.get(key)
    options = dict(host=site.host, port=site.port, username=site.user, password=password.get_secret_value(),
                   root=site.remote_root, timeout=settings.timeout_seconds)
    try:
        if site.protocol == "sftp":
            return SFTPBackend(**options, fingerprint=site.ssh_fingerprint)
        return FTPSBackend(**options, ca_file=str(native_path(site.ca_file)) if site.ca_file else None)
    except Exception:
        raise ConnectionError("Connection failed; check identity, credentials, permissions and network.") from None
