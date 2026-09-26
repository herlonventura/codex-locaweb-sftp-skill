"""Explicit FTPS, verified TLS on control AND data connections, no FTP fallback.

MLSD metadata is mandatory. FTP cannot reliably expose symlinks on all servers;
the account MUST be jailed to its intended root by the server administrator.
"""

from datetime import datetime, timezone
from ftplib import FTP_TLS
import posixpath
import re
import ssl
import stat

from .base import Backend, BLOCK_SIZE, RemoteEntry, UnsafeRemotePath, validate_connection


class FTPSBackend(Backend):
    def __init__(self, *, host: str, username: str, password: str, root: str,
                 port: int = 21, timeout: float = 15, ca_file: str | None = None):
        super().__init__(root)
        validate_connection(host, port, timeout)
        # No API accepts CERT_NONE, disables hostname checks or downgrades TLS.
        context = ssl.create_default_context(cafile=ca_file)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        self._ftp = FTP_TLS(context=context, timeout=timeout, encoding="utf-8")
        try:
            self._ftp.connect(host, port)
            self._ftp.login(username, password)  # AUTH TLS precedes credentials.
            self._ftp.prot_p()
            self.check_connection()
        except BaseException:
            self.close()
            raise

    def close(self):
        self._ftp.close()

    def _listing(self, absolute):
        result = {}
        for name, facts in self._ftp.mlsd(absolute):
            if facts.get("type") in ("cdir", "pdir") and name in (".", ".."):
                continue
            if name in result:
                raise UnsafeRemotePath("Duplicate MLSD entry.")
            result[name] = facts
        return result

    @staticmethod
    def _entry(facts):
        kind = facts.get("type", "").lower()
        mode = facts.get("unix.mode")
        try:
            if mode is not None and stat.S_IFMT(int(mode, 8)) not in (0, stat.S_IFREG, stat.S_IFDIR):
                return RemoteEntry("unsafe")
            if kind == "dir":
                return RemoteEntry("dir")
            if kind != "file":
                return RemoteEntry("unsafe")
            if not re.fullmatch(r"[0-9]{14}(\.[0-9]+)?", facts["modify"]):
                raise ValueError("Invalid MLSD timestamp.")
            whole, _, fraction = facts["modify"].partition(".")
            modified = datetime.strptime(whole, "%Y%m%d%H%M%S").replace(
                tzinfo=timezone.utc, microsecond=int((fraction + "000000")[:6]))
            return RemoteEntry("file", int(facts["size"]), modified.timestamp())
        except (KeyError, ValueError, OverflowError) as exc:
            raise UnsafeRemotePath("Incomplete or invalid MLSD metadata.") from exc

    def _stat(self, absolute):
        if absolute == "/":
            self._ftp.cwd("/")
            return RemoteEntry("dir")
        parent, name = posixpath.split(absolute)
        facts = self._listing(parent).get(name)
        return None if facts is None else self._entry(facts)

    def _names(self, absolute):
        return list(self._listing(absolute))

    def _canonical_dir(self, absolute):
        self._ftp.cwd(absolute)
        return self._ftp.pwd()

    def _read(self, absolute, consume):
        try:
            self._ftp.retrbinary("RETR " + absolute, consume, blocksize=BLOCK_SIZE)
        except BaseException:
            # A failed callback can leave a pending FTP response. Do not reuse
            # a control connection whose request/response state is uncertain.
            self.close()
            raise

    def _write_new(self, absolute, source):
        # FTP has no portable create-exclusive equivalent. A concurrent writer
        # can race this preflight; deployments must use an exclusive site lock.
        if self._stat(absolute) is not None:
            raise FileExistsError("Remote path appeared before FTPS upload.")
        try:
            self._ftp.storbinary("STOR " + absolute, source, blocksize=BLOCK_SIZE)
        except BaseException:
            self.close()
            raise

    def _mkdir(self, absolute):
        self._ftp.mkd(absolute)

    def _write_existing(self, absolute, source):
        self._regular(self._stat(absolute))
        try:
            self._ftp.storbinary("STOR " + absolute, source, blocksize=BLOCK_SIZE)
        except BaseException:
            self.close()
            raise
