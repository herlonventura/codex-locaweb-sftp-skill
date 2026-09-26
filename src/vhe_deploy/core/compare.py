"""Compare validated snapshots in memory. This module performs no transfers."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import unicodedata

from .checksum import normalize_sha256
from .guards import is_blocked_path, validate_blocked_patterns, validate_relative_path


TIMESTAMP_TOLERANCE = timedelta(seconds=2)


@dataclass(frozen=True)
class FileState:
    """Metadata of a regular file; callers must exclude links when collecting it."""

    path: str
    sha256: str
    modified_at: datetime

    def __post_init__(self) -> None:
        validate_relative_path(self.path)
        object.__setattr__(self, "sha256", normalize_sha256(self.sha256))
        if (not isinstance(self.modified_at, datetime)
                or self.modified_at.tzinfo is None
                or self.modified_at.utcoffset() is None):
            raise ValueError("File timestamps must include an explicit timezone.")
        object.__setattr__(self, "modified_at", self.modified_at.astimezone(timezone.utc))


@dataclass(frozen=True)
class Comparison:
    equal: tuple[str, ...]
    modified: tuple[str, ...]
    new_local: tuple[str, ...]
    remote_only: tuple[str, ...]
    conflicts: tuple[str, ...]
    blocked: tuple[str, ...]

    @property
    def has_blockers(self) -> bool:
        return bool(self.conflicts or self.blocked)

    @property
    def upload_paths(self) -> tuple[str, ...]:
        """Candidates, never an authorization; a single blocker stops the batch."""
        if self.has_blockers:
            return ()
        return tuple(sorted(self.modified + self.new_local))


def _index(files: Iterable[FileState]) -> dict[str, FileState]:
    result: dict[str, FileState] = {}
    for file in files:
        if not isinstance(file, FileState):
            raise ValueError("Inventory must contain validated FileState objects.")
        if file.path in result:
            raise ValueError("Duplicate file path in inventory.")
        result[file.path] = file
    return result


def _validate_namespace(paths: Iterable[str]) -> None:
    # Check implicit directory names as well as files: Assets/a vs assets/b
    # collide on some filesystems even though the full filenames differ.
    seen: dict[str, str] = {}
    file_keys: set[str] = set()
    directory_keys: set[str] = set()
    for path in paths:
        parts = path.split("/")
        for length in range(1, len(parts) + 1):
            prefix = "/".join(parts[:length])
            key = unicodedata.normalize("NFC", prefix).casefold()
            if key in seen and seen[key] != prefix:
                raise ValueError("Case or Unicode path collision across inventories.")
            seen[key] = prefix
            if length == len(parts):
                file_keys.add(key)
            else:
                directory_keys.add(key)
    if file_keys & directory_keys:
        raise ValueError("A path is used as both file and directory.")


def compare_inventories(
    local: Iterable[FileState],
    remote: Iterable[FileState],
    *,
    extra_blocked_patterns: Iterable[str] = (),
) -> Comparison:
    """Plan from supplied hashes and UTC instants; never infer missing evidence.

    Equal hashes need no upload, regardless of timestamp. For differing hashes,
    local must be strictly more than two seconds newer than remote. Remote-only
    paths are informational: there is deliberately no deletion plan.
    """
    extra = validate_blocked_patterns(extra_blocked_patterns)
    local_by_path, remote_by_path = _index(local), _index(remote)
    _validate_namespace(local_by_path.keys() | remote_by_path.keys())
    equal, modified, conflicts = [], [], []
    new_local = sorted(local_by_path.keys() - remote_by_path.keys())
    remote_only = sorted(remote_by_path.keys() - local_by_path.keys())
    for path in sorted(local_by_path.keys() & remote_by_path.keys()):
        source, target = local_by_path[path], remote_by_path[path]
        if source.sha256 == target.sha256:
            equal.append(path)
        elif source.modified_at - target.modified_at <= TIMESTAMP_TOLERANCE:
            conflicts.append(path)
        else:
            modified.append(path)
    blocked = sorted(
        path for path in new_local + modified + conflicts if is_blocked_path(path, extra)
    )
    return Comparison(
        tuple(equal), tuple(modified), tuple(new_local), tuple(remote_only),
        tuple(conflicts), tuple(blocked),
    )
