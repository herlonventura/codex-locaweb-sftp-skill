"""Shared stream interface and conservative remote filesystem checks.

No deletion, shell execution, local path selection or credential persistence.
Server-side path races cannot be eliminated by these protocols alone.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import math
from tempfile import SpooledTemporaryFile
from typing import BinaryIO, Callable

from ..core.checksum import normalize_sha256
from ..core.compare import FileState
from ..core.guards import is_blocked_path, validate_relative_path

BLOCK_SIZE = 65536


class UnsafeRemotePath(ValueError):
    """Missing/untrusted metadata or an unsupported filesystem object."""


class IntegrityError(OSError):
    """Content changed or a transfer did not match its expected digest."""


@dataclass(frozen=True)
class RemoteEntry:
    kind: str  # file, dir, or unsafe (including links and missing type)
    size: int | None = None
    modified: int | float | None = None


def validate_connection(host: str, port: int, timeout: float) -> None:
    if not isinstance(host, str) or not host or any(c.isspace() or ord(c) < 32 for c in host):
        raise ValueError("A host without whitespace or controls is required.")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("Invalid port.")
    if isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("A finite positive timeout is required.")


class Backend(ABC):
    def __init__(self, root: str):
        if not isinstance(root, str) or not root.startswith("/"):
            raise ValueError("Remote root must be an absolute POSIX path.")
        if root != "/":
            validate_relative_path(root[1:])
        self.root = root

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def _stat(self, absolute: str) -> RemoteEntry | None: ...

    @abstractmethod
    def _names(self, absolute: str) -> list[str]: ...

    @abstractmethod
    def _canonical_dir(self, absolute: str) -> str: ...

    @abstractmethod
    def _read(self, absolute: str, consume: Callable[[bytes], None]) -> None: ...

    @abstractmethod
    def _write_new(self, absolute: str, source: BinaryIO) -> None: ...

    @abstractmethod
    def _mkdir(self, absolute: str) -> None: ...

    def _write_existing(self, absolute: str, source: BinaryIO) -> None:
        raise NotImplementedError("Replacement is not supported by this backend.")

    def stat_path(self, path: str) -> RemoteEntry | None:
        return self._stat(self._path(path))

    def file_state(self, path: str) -> FileState:
        before = self._regular(self.stat_path(path))
        digest = self.checksum(path)
        if self.stat_path(path) != before:
            raise IntegrityError("Remote file changed while checking evidence.")
        return FileState(path, digest, datetime.fromtimestamp(before.modified, timezone.utc))

    def _directory(self, absolute: str) -> None:
        info = self._stat(absolute)
        if info is None or info.kind != "dir" or self._canonical_dir(absolute) != absolute:
            raise UnsafeRemotePath("Remote directory is missing, aliased or not a regular directory.")

    def check_connection(self) -> None:
        """Validate the configured root and every ancestor, without creating it."""
        self._directory("/")
        current = ""
        for part in self.root.strip("/").split("/") if self.root != "/" else ():
            current += "/" + part
            self._directory(current)

    def _path(self, relative: str) -> str:
        validate_relative_path(relative)
        self.check_connection()
        absolute = self.root.rstrip("/") + "/" + relative
        current = self.root.rstrip("/")
        for part in relative.split("/")[:-1]:
            current += "/" + part
            self._directory(current)
        return absolute

    @staticmethod
    def _regular(info: RemoteEntry | None) -> RemoteEntry:
        if info is None:
            raise FileNotFoundError("Remote file does not exist.")
        if info.kind != "file" or info.size is None or info.size < 0 or info.modified is None or not math.isfinite(info.modified):
            raise UnsafeRemotePath("A regular file with size and modification time is required.")
        return info

    def download(self, path: str, destination: BinaryIO) -> str:
        """Write exact bytes to a caller-owned stream; return their SHA-256.

        On error, destination may contain partial data and must be discarded.
        """
        absolute = self._path(path)
        before = self._regular(self._stat(absolute))
        digest = hashlib.sha256()
        count = 0

        def consume(chunk):
            nonlocal count
            if destination.write(chunk) != len(chunk):
                raise OSError("Incomplete destination write.")
            digest.update(chunk)
            count += len(chunk)

        self._read(absolute, consume)
        after = self._regular(self._stat(self._path(path)))
        if before != after or count != before.size:
            raise IntegrityError("Remote file changed during download.")
        return digest.hexdigest()

    def checksum(self, path: str) -> str:
        """Portable fallback: stream the complete file without retaining it."""
        class Sink:
            def write(self, chunk):
                return len(chunk)
        return self.download(path, Sink())

    def selected_inventory(self, paths) -> tuple[FileState, ...]:
        """Read only named files, validating existing parents without listing directories."""
        self.check_connection()
        files = []
        for path in paths:
            validate_relative_path(path)
            current = self.root.rstrip("/")
            missing_parent = False
            for part in path.split("/")[:-1]:
                current += "/" + part
                if self._stat(current) is None:
                    missing_parent = True
                    break
                self._directory(current)
            if not missing_parent and self.stat_path(path) is not None:
                files.append(self.file_state(path))
        return tuple(files)

    def inventory(self) -> tuple[FileState, ...]:
        self.check_connection()
        pending = [""]
        files = []
        while pending:
            relative = pending.pop()
            absolute = self.root if not relative else self._path(relative)
            self._directory(absolute)
            names = self._names(absolute)
            if len(names) != len(set(names)):
                raise UnsafeRemotePath("Duplicate remote directory entries.")
            for name in sorted(names):
                validate_relative_path(name)
                if "/" in name:
                    raise UnsafeRemotePath("Directory listing contains a path instead of a name.")
                path = relative + "/" + name if relative else name
                info = self._stat(self._path(path))
                if info is not None and info.kind == "dir":
                    pending.append(path)
                else:
                    info = self._regular(info)
                    digest = self.checksum(path)
                    if self._stat(self._path(path)) != info:
                        raise IntegrityError("Remote file changed while building inventory.")
                    files.append(FileState(path, digest, datetime.fromtimestamp(info.modified, timezone.utc)))
        return tuple(sorted(files, key=lambda item: item.path))

    def mkdir(self, path: str) -> None:
        """Create one directory; parents must already exist and be verified."""
        if is_blocked_path(path):
            raise UnsafeRemotePath("Creation of a blocked directory is forbidden.")
        absolute = self._path(path)
        if self._stat(absolute) is not None:
            raise FileExistsError("Remote path already exists.")
        self._mkdir(absolute)
        self._directory(absolute)

    def upload(self, path: str, source: BinaryIO, *, expected_sha256: str) -> str:
        """Create a NEW file and verify its contents by reading it back.

        An interrupted upload may leave a partial remote file;
        this primitive never removes it or claims a rollback succeeded.
        """
        return self._upload(path, source, expected_sha256=expected_sha256, previous=None)

    def replace(self, path: str, source: BinaryIO, *, expected_sha256: str, previous: FileState) -> str:
        """Low-level replacement; the deploy coordinator must verify backup first."""
        if previous.path != path:
            raise ValueError("Previous file evidence belongs to another path.")
        return self._upload(path, source, expected_sha256=expected_sha256, previous=previous)

    def _upload(self, path, source, *, expected_sha256, previous):
        expected = normalize_sha256(expected_sha256)
        if is_blocked_path(path):
            raise UnsafeRemotePath("Upload of a blocked file is forbidden.")
        absolute = self._path(path)
        if previous is None and self._stat(absolute) is not None:
            raise FileExistsError("Remote path already exists.")
        if previous is not None and self.file_state(path) != previous:
            raise IntegrityError("Remote file differs from approved previous state.")
        # Snapshot and validate BEFORE any write. Large streams spill to a
        # temporary file automatically removed when this context exits.
        with SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b") as snapshot:
            digest = hashlib.sha256()
            while chunk := source.read(BLOCK_SIZE):
                digest.update(chunk)
                snapshot.write(chunk)
            if digest.hexdigest() != expected:
                raise IntegrityError("Source does not match the expected SHA-256.")
            snapshot.seek(0)
            absolute = self._path(path)
            if previous is None and self._stat(absolute) is not None:
                raise FileExistsError("Remote path appeared before upload.")
            if previous is None:
                self._write_new(absolute, snapshot)
            else:
                if self.file_state(path) != previous:
                    raise IntegrityError("Remote file changed before replacement.")
                self._write_existing(absolute, snapshot)
        if self.checksum(path) != expected:
            raise IntegrityError("Uploaded content does not match the expected SHA-256.")
        return expected
