"""SHA-256 helpers for bytes supplied by the caller, not filesystem paths."""

from collections.abc import Iterable
import hashlib
import re


def normalize_sha256(digest: str) -> str:
    """Validate a complete hexadecimal digest, accepting either letter case."""
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        raise ValueError("Expected a complete SHA-256 hexadecimal digest.")
    return digest.lower()


def sha256_chunks(chunks: Iterable[bytes]) -> str:
    """Hash an iterable of byte chunks without buffering the entire content."""
    digest = hashlib.sha256()
    for chunk in chunks:
        if not isinstance(chunk, bytes):
            raise TypeError("Checksum input must contain bytes, not text or paths.")
        digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(content: bytes) -> str:
    """Hash exactly the given bytes; no encoding or newline normalization."""
    return sha256_chunks((content,))
