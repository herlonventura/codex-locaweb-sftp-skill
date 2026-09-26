"""Small local file helpers; reject links/reparse points, never recurse-delete."""

import os
from pathlib import Path
import stat
import tempfile


def checked_path(path: Path) -> Path:
    path = Path(os.path.abspath(path.expanduser()))
    for component in (*reversed(path.parents), path):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Links and reparse points are not allowed for private files.")
    return path


def read_limited(path: Path, limit: int = 1024 * 1024) -> bytes:
    path = checked_path(path)
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("Expected a regular file.")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("File exceeds the configured size limit.")
    return data


def write_private(path: Path, data: bytes, *, replace: bool = False) -> None:
    """Publish complete bytes; mode 0600 on POSIX, inherited ACL on Windows.

    Hard-link publication with replace=False refuses an existing destination.
    Filesystems without hard links fail closed instead of overwriting a file.
    """
    path = checked_path(path)
    descriptor, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        checked_path(path)
        if replace:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
