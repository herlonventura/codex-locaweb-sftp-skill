"""Short-lived, single-use preview receipts, shared by CLI and MCP processes."""

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import math
import os
import re
import secrets
import sqlite3
import stat
import time

from ..local_files import checked_path
from .checksum import normalize_sha256
from .guards import normalize_domain
from .local import exclusive_file, private_directory

TTL_SECONDS = 300


class TokenError(ValueError):
    def __init__(self):
        super().__init__("Token ausente, inválido, expirado, consumido ou vinculado a outra prévia. Gere nova prévia.")


@dataclass(frozen=True)
class PreviewLease:
    issued_at: float
    monotonic_at: float

    def require_fresh(self):
        # Both clocks must agree that the receipt is still fresh. A backward
        # wall-clock change or monotonic reset fails closed; never extend TTL.
        wall, monotonic = time.time() - self.issued_at, time.monotonic() - self.monotonic_at
        if not (math.isfinite(wall) and math.isfinite(monotonic)
                and 0 <= wall < TTL_SECONDS and 0 <= monotonic < TTL_SECONDS):
            raise TokenError()


class TokenStore:
    def __init__(self, state):
        self.directory = private_directory(state / "preview-tokens")
        self.path = self.directory / "receipts.sqlite3"

    @contextmanager
    def _database(self):
        path = checked_path(self.path)
        try:
            with exclusive_file(path):
                pass
        except FileExistsError:
            pass
        info = checked_path(path).stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise TokenError()
        if os.name != "nt" and info.st_mode & 0o077:
            raise TokenError()
        connection = sqlite3.connect(path, timeout=5, isolation_level=None)
        try:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, domain TEXT NOT NULL, "
                               "preview_hash TEXT NOT NULL, issued REAL NOT NULL, monotonic REAL NOT NULL, "
                               "used INTEGER NOT NULL DEFAULT 0)")
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _identifier(token):
        if not isinstance(token, str) or re.fullmatch(r"[A-Za-z0-9_-]{43}", token) is None:
            raise TokenError()
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    def issue(self, preview):
        if preview.comparison.has_blockers:
            raise TokenError()
        token = secrets.token_urlsafe(32)
        issued, monotonic = time.time(), time.monotonic()
        with self._database() as db:
            db.execute("DELETE FROM receipts WHERE issued <= ?", (issued - TTL_SECONDS,))
            db.execute("INSERT INTO receipts (id,domain,preview_hash,issued,monotonic) VALUES (?,?,?,?,?)",
                       (self._identifier(token), normalize_domain(preview.domain), normalize_sha256(preview.digest), issued, monotonic))
        return {"preview_token": token, "expires_at": issued + TTL_SECONDS, "valid_for_seconds": TTL_SECONDS}

    def _read(self, domain, preview_hash, token, *, consume):
        identifier = self._identifier(token)
        domain, preview_hash = normalize_domain(domain), normalize_sha256(preview_hash)
        with self._database() as db:
            # Serialization covers independent MCP/CLI processes using this
            # state directory. A consumed token stays consumed after a crash.
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT domain,preview_hash,issued,monotonic,used FROM receipts WHERE id=?", (identifier,)).fetchone()
            if consume and row is not None and not row[4]:
                db.execute("UPDATE receipts SET used=1 WHERE id=?", (identifier,))
            db.execute("COMMIT")
        if row is None or row[4] or row[:2] != (domain, preview_hash):
            raise TokenError()
        lease = PreviewLease(row[2], row[3])
        lease.require_fresh()
        return lease

    def validate(self, domain, preview_hash, token):
        """Early check before authentication; consumption still rechecks atomically."""
        self._read(domain, preview_hash, token, consume=False)

    def consume(self, domain, preview_hash, token):
        return self._read(domain, preview_hash, token, consume=True)
