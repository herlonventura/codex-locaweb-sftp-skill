"""SFTP with an explicitly provisioned OpenSSH SHA256 host-key pin."""

import base64
import errno
import hashlib
import hmac
import re
import stat

import paramiko

from .base import Backend, BLOCK_SIZE, RemoteEntry, validate_connection


class _PinnedHostKey(paramiko.MissingHostKeyPolicy):
    def __init__(self, fingerprint: str):
        if not isinstance(fingerprint, str) or not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", fingerprint):
            raise ValueError("A confirmed OpenSSH SHA256 fingerprint is required.")
        self.fingerprint = fingerprint

    def missing_host_key(self, client, hostname, key):
        observed = "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode("ascii").rstrip("=")
        if not hmac.compare_digest(observed, self.fingerprint):
            raise paramiko.SSHException("Server SSH host key does not match the confirmed fingerprint.")


class SFTPBackend(Backend):
    def __init__(self, *, host: str, username: str, password: str, root: str,
                 fingerprint: str, port: int = 22, timeout: float = 15):
        super().__init__(root)
        validate_connection(host, port, timeout)
        policy = _PinnedHostKey(fingerprint)
        self._client = paramiko.SSHClient()
        self._sftp = None
        self._client.set_missing_host_key_policy(policy)
        try:
            self._client.connect(host, port=port, username=username, password=password,
                                 allow_agent=False, look_for_keys=False, timeout=timeout,
                                 banner_timeout=timeout, auth_timeout=timeout, channel_timeout=timeout)
            self._sftp = self._client.open_sftp()
            self._sftp.get_channel().settimeout(timeout)
            self.check_connection()
        except BaseException:
            self.close()
            raise

    def close(self):
        try:
            if self._sftp is not None:
                self._sftp.close()
        finally:
            self._client.close()

    def _stat(self, absolute):
        try:
            attrs = self._sftp.lstat(absolute)
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                return None
            raise
        mode = attrs.st_mode or 0
        kind = "dir" if stat.S_ISDIR(mode) else "file" if stat.S_ISREG(mode) else "unsafe"
        return RemoteEntry(kind, attrs.st_size, attrs.st_mtime)

    def _names(self, absolute):
        return self._sftp.listdir(absolute)

    def _canonical_dir(self, absolute):
        return self._sftp.normalize(absolute)

    def _read(self, absolute, consume):
        with self._sftp.open(absolute, "rb") as stream:
            while chunk := stream.read(BLOCK_SIZE):
                consume(chunk)

    def _write_new(self, absolute, source):
        with self._sftp.open(absolute, "wx") as stream:
            while chunk := source.read(BLOCK_SIZE):
                stream.write(chunk)

    def _mkdir(self, absolute):
        self._sftp.mkdir(absolute)
