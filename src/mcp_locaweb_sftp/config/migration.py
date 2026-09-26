"""Migrate metadata only, without importing/exporting DPAPI plaintext."""

from dataclasses import dataclass
import json
from pathlib import Path

import yaml

from ..core.guards import normalize_domain
from ..local_files import checked_path, write_private
from .loader import ConfigError, load_settings, load_sites
from .schema import Settings, Site


@dataclass(frozen=True)
class MigrationResult:
    domains: tuple[str, ...]
    pending_credentials: tuple[str, ...]
    pending_ssh_fingerprints: tuple[str, ...]


def migrate_legacy(source: Path, destination: Path, *, local_roots: dict[str, str] | None = None) -> MigrationResult:
    """Copy legacy metadata into a NEW destination directory. Source is read-only.

    local_roots explicitly maps paths when moving between operating systems.
    Existing pins are retained only if they match the supported SHA256 format.
    Credentials must be re-enrolled; no DPAPI dependency or implicit decryption.
    """
    try:
        source = checked_path(source)
        destination = checked_path(destination)
        if destination.exists() or destination == source or source in destination.parents:
            raise ConfigError("Migration needs a new destination outside the source directory.")
        sites = load_sites(source / "config" / "sites.json")
        load_settings(source / "config" / "settings.json")  # validate, never carry opt-in
        overrides = {}
        for name, value in (local_roots or {}).items():
            domain = normalize_domain(name)
            if domain not in sites or domain in overrides:
                raise ConfigError("Path mapping contains an unknown or duplicate domain.")
            overrides[domain] = value
        for domain, site in sites.items():
            data = site.model_dump()
            data["publish_enabled"] = False
            if domain in overrides:
                data["local_root"] = overrides[domain]
            sites[domain] = Site.model_validate(data)
        result = MigrationResult(tuple(sorted(sites)), tuple(sorted(sites)), tuple(sorted(
            name for name, site in sites.items() if site.protocol == "sftp" and site.ssh_fingerprint is None)))
        payloads = {
            "sites.yaml": yaml.safe_dump({name: sites[name].model_dump(mode="json") for name in sorted(sites)},
                                         allow_unicode=True, sort_keys=False).encode("utf-8"),
            "settings.yaml": yaml.safe_dump(Settings().model_dump(mode="json")).encode("utf-8"),
            "migration.json": json.dumps({"status": "metadata_migrated_credentials_pending",
                "domains": result.domains, "pending_credentials": result.pending_credentials,
                "pending_ssh_fingerprints": result.pending_ssh_fingerprints,
                "publish_enabled": False}, indent=2).encode("utf-8"),
        }
        destination.mkdir(mode=0o700)  # parent must already exist; no broad mkdir
    except (OSError, ValueError):
        raise ConfigError("Migration preparation failed; check source, destination and explicit path mappings.") from None
    try:
        for name, content in payloads.items():
            path = destination / name
            write_private(path, content)
    except (OSError, ValueError):
        # Leave incomplete output for inspection. No destructive rollback, and
        # retries never overwrite this directory. migration.json is written last.
        raise ConfigError("Migration incomplete; destination retained for inspection, source unchanged.") from None
    return result
