"""Exact-file scopes; never expand folders or interpret wildcard patterns."""

from .guards import validate_relative_path


def normalize_files(files):
    if files is None:
        return None
    if not isinstance(files, (list, tuple)) or not files:
        raise ValueError("Provide a nonempty list of relative file paths.")
    for path in files:
        validate_relative_path(path)
    if len(set(files)) != len(files):
        raise ValueError("Duplicate selected file.")
    return tuple(sorted(files))
