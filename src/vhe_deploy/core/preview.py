from dataclasses import asdict, dataclass
import hashlib
import json

from ..config.schema import Settings, Site, native_path
from ..core.guards import normalize_domain
from ..local_files import checked_path
from .compare import Comparison, FileState, compare_inventories
from .local import local_inventory


def file_evidence(entries):
    return [dict(path=item.path, sha256=item.sha256, modified_at=item.modified_at.isoformat()) for item in entries]


@dataclass(frozen=True)
class Preview:
    domain: str
    digest: str
    local: tuple[FileState, ...]
    remote: tuple[FileState, ...]
    comparison: Comparison

    def summary(self):
        return {"domain": self.domain, "preview_hash": self.digest, **asdict(self.comparison),
                "would_upload": self.comparison.upload_paths, "would_delete": []}


def make_preview(domain: str, site: Site, settings: Settings, backend) -> Preview:
    domain = normalize_domain(domain)
    root = checked_path(native_path(site.local_root))
    local, remote = local_inventory(root), backend.inventory()
    comparison = compare_inventories(local, remote, extra_blocked_patterns=site.blocked_paths)
    evidence = {"version": 1, "domain": domain, "site": site.model_dump(mode="json"),
                "settings": settings.model_dump(mode="json"), "resolved_local_root": str(root),
                "local": file_evidence(local), "remote": file_evidence(remote)}
    digest = hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":"),
                                       ensure_ascii=True).encode("utf-8")).hexdigest()
    return Preview(domain, digest, local, remote, comparison)
