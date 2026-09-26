"""Inventory and snapshot regular local files, without following links/junctions."""

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import stat

from ..backends.base import BLOCK_SIZE, IntegrityError
from ..local_files import checked_path
from .compare import FileState, compare_inventories
from .guards import validate_relative_path


def private_directory(path: Path) -> Path:
    path = checked_path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    checked_path(path)
    return path


def exclusive_file(path: Path):
    checked_path(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    return os.fdopen(descriptor, "wb")


def _signature(info):
    # Windows stat/fstat can expose different ctime semantics. Compare ctime
    # only between two fstat calls on the same open descriptor below.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def read_local(root: Path, relative: str, destination=None) -> FileState:
    validate_relative_path(relative)
    path = checked_path(root / relative)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError("Only regular files without hard-link aliases are accepted.")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        opened = os.fstat(source.fileno())
        if _signature(opened) != _signature(before):
            raise IntegrityError("Local file changed before opening.")
        while chunk := source.read(BLOCK_SIZE):
            digest.update(chunk)
            if destination is not None and destination.write(chunk) != len(chunk):
                raise OSError("Incomplete local snapshot write.")
        finished = os.fstat(source.fileno())
        if _signature(finished) != _signature(opened) or finished.st_ctime_ns != opened.st_ctime_ns:
            raise IntegrityError("Local file changed through the open handle.")
    if _signature(checked_path(path).stat()) != _signature(before):
        raise IntegrityError("Local file changed while reading.")
    return FileState(relative, digest.hexdigest(), datetime.fromtimestamp(before.st_mtime, timezone.utc))


def local_inventory(root: Path) -> tuple[FileState, ...]:
    root = checked_path(root)
    if not root.is_dir():
        raise ValueError("Local root must be an existing directory.")
    result, pending = [], [root]
    while pending:
        directory = pending.pop()
        for path in sorted(checked_path(directory).iterdir()):
            relative = path.relative_to(root).as_posix()
            validate_relative_path(relative)
            path = checked_path(path)
            if stat.S_ISDIR(path.stat().st_mode):
                pending.append(path)
            else:
                result.append(read_local(root, relative))
    # Reuse cross-platform name collision checks before making any copies.
    compare_inventories(result, result)
    return tuple(sorted(result, key=lambda entry: entry.path))


def snapshot_local(root: Path, entries: tuple[FileState, ...], destination: Path) -> None:
    for entry in entries:
        target = destination / entry.path
        private_directory(target.parent)
        with exclusive_file(target) as stream:
            actual = read_local(root, entry.path, stream)
            stream.flush()
            os.fsync(stream.fileno())
        if actual != entry or read_local(destination, entry.path).sha256 != entry.sha256:
            raise IntegrityError("Local snapshot differs from approved source.")
