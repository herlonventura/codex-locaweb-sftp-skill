"""Real loopback protocol servers. All keys, certificates and files are temporary."""

import base64
from datetime import datetime, timedelta, timezone
import errno
import hashlib
import ipaddress
import os
from pathlib import PurePosixPath
import socket
import stat
import threading
from types import SimpleNamespace

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import paramiko
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import TLS_FTPHandler
from pyftpdlib.ioloop import IOLoop
from pyftpdlib.servers import FTPServer
import pytest


@pytest.fixture
def sftp_server(tmp_path):
    """Reusable paramiko.ServerInterface fixture for configuration/CLI/MCP tests."""
    storage = tmp_path / "sftp"
    (storage / "site").mkdir(parents=True)
    host_key = paramiko.RSAKey.generate(2048)
    fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(host_key.asbytes()).digest()).decode().rstrip("=")
    state = SimpleNamespace(storage=storage, auth_attempts=0, links={}, permission_denied=set())

    class Auth(paramiko.ServerInterface):
        def check_auth_password(self, username, password):
            state.auth_attempts += 1
            return paramiko.AUTH_SUCCESSFUL if (username, password) == ("test-user", "test-password-only") else paramiko.AUTH_FAILED

        def get_allowed_auths(self, username):
            return "password"

        def check_channel_request(self, kind, chanid):
            return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    class Files(paramiko.SFTPServerInterface):
        def local(self, path):
            parts = PurePosixPath(path).parts
            if not path.startswith("/") or ".." in parts or "\\" in path:
                raise PermissionError(errno.EACCES, "Invalid simulated path")
            if path in state.permission_denied:
                raise PermissionError(errno.EACCES, "Simulated denial")
            return storage.joinpath(*parts[1:])

        def canonicalize(self, path):
            return state.links.get(path, path)

        def lstat(self, path):
            if path in state.links:
                attrs = paramiko.SFTPAttributes()
                attrs.st_mode = stat.S_IFLNK | 0o777
                return attrs
            try:
                return paramiko.SFTPAttributes.from_stat(self.local(path).lstat())
            except OSError as exc:
                return paramiko.SFTPServer.convert_errno(exc.errno)

        stat = lstat

        def list_folder(self, path):
            try:
                entries = []
                for child in self.local(path).iterdir():
                    attrs = paramiko.SFTPAttributes.from_stat(child.lstat())
                    attrs.filename = child.name
                    entries.append(attrs)
                for link in state.links:
                    if str(PurePosixPath(link).parent) == path:
                        attrs = self.lstat(link)
                        attrs.filename = PurePosixPath(link).name
                        entries.append(attrs)
                return entries
            except OSError as exc:
                return paramiko.SFTPServer.convert_errno(exc.errno)

        def open(self, path, flags, attr):
            try:
                if path in state.links:
                    return paramiko.SFTP_PERMISSION_DENIED
                descriptor = os.open(self.local(path), flags | getattr(os, "O_BINARY", 0), 0o600)
                mode = "r+b" if flags & os.O_RDWR else "wb" if flags & os.O_WRONLY else "rb"
                stream = os.fdopen(descriptor, mode)
                class Handle(paramiko.SFTPHandle):
                    def chattr(self, attributes):
                        try:
                            if attributes.st_size is not None:
                                stream.truncate(attributes.st_size)
                            return paramiko.SFTP_OK
                        except OSError as error:
                            return paramiko.SFTPServer.convert_errno(error.errno)
                handle = Handle(flags)
                if flags & (os.O_WRONLY | os.O_RDWR):
                    handle.writefile = stream
                if not flags & os.O_WRONLY:
                    handle.readfile = stream
                return handle
            except OSError as exc:
                return paramiko.SFTPServer.convert_errno(exc.errno)

        def mkdir(self, path, attr):
            try:
                self.local(path).mkdir()
                return paramiko.SFTP_OK
            except OSError as exc:
                return paramiko.SFTPServer.convert_errno(exc.errno)

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(0.1)
    state.options = dict(host="127.0.0.1", port=listener.getsockname()[1], username="test-user",
                         password="test-password-only", fingerprint=fingerprint, root="/site", timeout=3)
    stopping = threading.Event()
    transports = []

    def serve():
        while not stopping.is_set():
            try:
                client, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            transport = paramiko.Transport(client)
            transports.append(transport)
            transport.add_server_key(host_key)
            transport.set_subsystem_handler("sftp", paramiko.SFTPServer, Files)
            try:
                transport.start_server(server=Auth())
            except (EOFError, paramiko.SSHException, OSError):
                transport.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        stopping.set()
        listener.close()
        for transport in transports:
            transport.close()
        thread.join(timeout=5)
        assert not thread.is_alive(), "Simulated SFTP listener did not stop"


@pytest.fixture
def ftps_server(tmp_path):
    storage = tmp_path / "ftps"
    (storage / "site").mkdir(parents=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fixture.invalid")])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                   .public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
                   .add_extension(x509.SubjectAlternativeName([
                       x509.IPAddress(ipaddress.ip_address("127.0.0.1")), x509.DNSName("fixture.invalid")]), critical=False)
                   .sign(key, hashes.SHA256()))
    ca = tmp_path / "test-certificate.pem"
    ca.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    private = tmp_path / "test-private.key"
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
    authorizer = DummyAuthorizer()
    authorizer.add_user("test-user", "test-password-only", str(storage), perm="elradfmwMT")

    class Handler(TLS_FTPHandler):
        certfile = str(ca)
        keyfile = str(private)
        tls_control_required = True
        tls_data_required = True

    Handler.authorizer = authorizer
    loop = IOLoop()
    server = FTPServer(("127.0.0.1", 0), Handler, ioloop=loop)
    stopping = threading.Event()
    # Poll the private loop without touching the process-global event loop.
    def serve():
        while not stopping.is_set():
            loop.loop(timeout=0.02, blocking=False)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield SimpleNamespace(storage=storage, options=dict(host="127.0.0.1", port=server.socket.getsockname()[1],
                              username="test-user", password="test-password-only", root="/site", timeout=3,
                              ca_file=str(ca)))
    finally:
        stopping.set()
        thread.join(timeout=5)
        server.close_all()
        loop.close()
        assert not thread.is_alive(), "Simulated FTPS listener did not stop"


@pytest.fixture(params=["sftp", "ftps"])
def backend(request):
    from mcp_locaweb_sftp.backends import FTPSBackend, SFTPBackend
    server = request.getfixturevalue(request.param + "_server")
    cls = SFTPBackend if request.param == "sftp" else FTPSBackend
    with cls(**server.options) as connection:
        yield connection, server
