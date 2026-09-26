from datetime import timezone
import io
import socket
import ssl

import paramiko
import pytest

from mcp_locaweb_sftp.backends import FTPSBackend, IntegrityError, RemoteEntry, SFTPBackend, UnsafeRemotePath
from mcp_locaweb_sftp.backends.base import validate_connection
from mcp_locaweb_sftp.core.checksum import sha256_bytes


def test_real_roundtrip_inventory_and_nested_directories(backend):
    client, server = backend
    content = bytes(range(256)) * 1100 + b"\r\nexact bytes\x00"
    client.mkdir("assets")
    client.mkdir("assets/nested")
    expected = sha256_bytes(content)
    assert client.upload("assets/nested/café [1].bin", io.BytesIO(content), expected_sha256=expected) == expected
    assert (server.storage / "site/assets/nested/café [1].bin").read_bytes() == content
    output = io.BytesIO()
    assert client.download("assets/nested/café [1].bin", output) == expected
    assert output.getvalue() == content
    inventory = client.inventory()
    assert len(inventory) == 1
    assert inventory[0].path == "assets/nested/café [1].bin"
    assert inventory[0].sha256 == expected
    assert inventory[0].modified_at.tzinfo == timezone.utc


def test_empty_file_is_not_missing(backend):
    client, _ = backend
    digest = sha256_bytes(b"")
    assert client.upload("empty.txt", io.BytesIO(), expected_sha256=digest) == digest
    assert client.checksum("empty.txt") == digest


def test_existing_content_is_never_replaced_by_transport_stage(backend):
    client, server = backend
    path = server.storage / "site/index.html"
    path.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        client.upload("index.html", io.BytesIO(b"new"), expected_sha256=sha256_bytes(b"new"))
    assert path.read_bytes() == b"existing"
    with pytest.raises(FileExistsError):
        client.mkdir("index.html")


@pytest.mark.parametrize("path", ["../outside", "/outside", "a\\b", "a/../b", "a\r\nDELE index.html", "a//b"])
def test_invalid_paths_never_transfer(backend, path):
    client, server = backend
    with pytest.raises(ValueError):
        client.upload(path, io.BytesIO(b"x"), expected_sha256=sha256_bytes(b"x"))
    with pytest.raises(ValueError):
        client.download(path, io.BytesIO())
    assert list((server.storage / "site").iterdir()) == []


@pytest.mark.parametrize("path", [".env", "wp-config.php", "a.key", ".git/config", "backups/file.txt"])
def test_sensitive_uploads_are_blocked(backend, path):
    client, _ = backend
    with pytest.raises(UnsafeRemotePath):
        client.upload(path, io.BytesIO(b"x"), expected_sha256=sha256_bytes(b"x"))


def test_blocked_directory_and_missing_parent(backend):
    client, _ = backend
    with pytest.raises(UnsafeRemotePath):
        client.mkdir(".git")
    with pytest.raises(UnsafeRemotePath):
        client.upload("missing/page.html", io.BytesIO(b"x"), expected_sha256=sha256_bytes(b"x"))
    with pytest.raises(FileNotFoundError):
        client.download("missing.html", io.BytesIO())


def test_source_hash_mismatch_does_not_create_remote_file(backend):
    client, server = backend
    with pytest.raises(IntegrityError, match="Source"):
        client.upload("index.html", io.BytesIO(b"changed"), expected_sha256=sha256_bytes(b"original"))
    assert not (server.storage / "site/index.html").exists()


def test_server_corruption_is_detected_after_upload(backend, monkeypatch):
    client, server = backend
    write = client._write_new
    def corrupt(path, source):
        write(path, source)
        (server.storage / "site/index.html").write_bytes(b"corrupt")
    monkeypatch.setattr(client, "_write_new", corrupt)
    with pytest.raises(IntegrityError, match="Uploaded"):
        client.upload("index.html", io.BytesIO(b"original"), expected_sha256=sha256_bytes(b"original"))
    assert (server.storage / "site/index.html").read_bytes() == b"corrupt"  # no fake rollback/deletion


def test_interrupted_transfer_is_reported_and_not_removed(backend, monkeypatch):
    client, server = backend
    write = client._write_new
    def interrupted(path, source):
        write(path, io.BytesIO(b"partial"))
        raise ConnectionError("Simulated interruption")
    monkeypatch.setattr(client, "_write_new", interrupted)
    with pytest.raises(ConnectionError):
        client.upload("index.html", io.BytesIO(b"complete"), expected_sha256=sha256_bytes(b"complete"))
    assert (server.storage / "site/index.html").read_bytes() == b"partial"


def test_change_during_download_is_rejected(backend, monkeypatch):
    client, server = backend
    path = server.storage / "site/index.html"
    path.write_bytes(b"original")
    read = client._read
    def changing(absolute, consume):
        read(absolute, consume)
        path.write_bytes(b"changed-size")
    monkeypatch.setattr(client, "_read", changing)
    with pytest.raises(IntegrityError, match="changed"):
        client.download("index.html", io.BytesIO())


def test_short_local_write_is_not_success(backend):
    client, server = backend
    (server.storage / "site/index.html").write_bytes(b"abc")
    class ShortWriter:
        def write(self, chunk):
            return 1
    with pytest.raises(OSError, match="Incomplete"):
        client.download("index.html", ShortWriter())


def test_untrusted_ssh_key_is_rejected_before_authentication(sftp_server):
    with pytest.raises(paramiko.SSHException, match="fingerprint"):
        SFTPBackend(**(sftp_server.options | {"fingerprint": "SHA256:" + "A" * 43}))
    assert sftp_server.auth_attempts == 0


def test_bad_sftp_password_and_missing_root(sftp_server):
    with pytest.raises(paramiko.AuthenticationException):
        SFTPBackend(**(sftp_server.options | {"password": "incorrect"}))
    assert sftp_server.auth_attempts == 1
    with pytest.raises(UnsafeRemotePath):
        SFTPBackend(**(sftp_server.options | {"root": "/missing"}))


@pytest.mark.parametrize("fingerprint", [None, "", "MD5:12:34", "SHA256:abc"])
def test_absent_or_invalid_pin_cannot_open_connection(sftp_server, fingerprint):
    with pytest.raises(ValueError):
        SFTPBackend(**(sftp_server.options | {"fingerprint": fingerprint}))
    assert sftp_server.auth_attempts == 0


def test_sftp_rejects_links_and_permission_errors(sftp_server):
    sftp_server.links["/site/link"] = "/outside"
    with SFTPBackend(**sftp_server.options) as client:
        with pytest.raises(UnsafeRemotePath):
            client.download("link", io.BytesIO())
        with pytest.raises(UnsafeRemotePath):
            client.download("link/file.html", io.BytesIO())
        with pytest.raises(UnsafeRemotePath):
            client.inventory()
        sftp_server.permission_denied.add("/site/secret.html")
        with pytest.raises(PermissionError):
            client.checksum("secret.html")
    with pytest.raises(UnsafeRemotePath):
        SFTPBackend(**(sftp_server.options | {"root": "/site/link"}))


def test_ftps_untrusted_certificate_and_wrong_hostname(ftps_server):
    with pytest.raises(ssl.SSLCertVerificationError):
        FTPSBackend(**(ftps_server.options | {"ca_file": None}))
    with pytest.raises(ssl.SSLCertVerificationError):
        FTPSBackend(**(ftps_server.options | {"host": "localhost"}))


def test_ftps_protects_both_connections(ftps_server):
    with FTPSBackend(**ftps_server.options) as client:
        assert isinstance(client._ftp.sock, ssl.SSLSocket)
        assert client._ftp._prot_p is True
        assert client._ftp.context.verify_mode == ssl.CERT_REQUIRED
        assert client._ftp.context.check_hostname is True


@pytest.mark.parametrize("facts", [
    {"type": "OS.unix=slink:/etc"}, {"type": "file", "unix.mode": "120777"}, {},
])
def test_ftps_link_or_unknown_type_is_unsafe(facts):
    assert FTPSBackend._entry(facts).kind == "unsafe"


@pytest.mark.parametrize("facts", [
    {"type": "file"}, {"type": "file", "size": "x", "modify": "20260101000000"},
    {"type": "file", "size": "1", "modify": "invalid"}, {"type": "dir", "unix.mode": "invalid"},
])
def test_ftps_incomplete_metadata_is_rejected(facts):
    with pytest.raises(UnsafeRemotePath):
        FTPSBackend._entry(facts)


@pytest.mark.parametrize("root", ["relative", "/site/", "/site/../other", "/site\r\nDELE x"])
def test_bad_root_is_rejected_without_network(root):
    with pytest.raises(ValueError):
        FTPSBackend(host="unused.invalid", username="test", password="test", root=root)


@pytest.mark.parametrize("host,port,timeout", [("", 22, 1), ("bad\nhost", 22, 1), ("host", 0, 1),
    ("host", True, 1), ("host", 22, 0), ("host", 22, float("inf")), ("host", 22, float("nan"))])
def test_invalid_connection_parameters(host, port, timeout):
    with pytest.raises(ValueError):
        validate_connection(host, port, timeout)


def test_network_timeout_is_finite_for_both_protocols():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        options = dict(host="127.0.0.1", port=listener.getsockname()[1], username="test", password="test",
                       root="/", timeout=0.15)
        with pytest.raises((TimeoutError, paramiko.SSHException)):
            SFTPBackend(**options, fingerprint="SHA256:" + "A" * 43)
        with pytest.raises(TimeoutError):
            FTPSBackend(**options)


def test_sftp_exclusive_create_preserves_concurrent_file(sftp_server):
    with SFTPBackend(**sftp_server.options) as client:
        path = sftp_server.storage / "site/concurrent.html"
        path.write_bytes(b"other writer")
        with pytest.raises(OSError):
            client._write_new("/site/concurrent.html", io.BytesIO(b"my data"))
        assert path.read_bytes() == b"other writer"


def test_ftps_rechecks_existence_before_stor(ftps_server):
    with FTPSBackend(**ftps_server.options) as client:
        path = ftps_server.storage / "site/concurrent.html"
        path.write_bytes(b"other writer")
        with pytest.raises(FileExistsError):
            client._write_new("/site/concurrent.html", io.BytesIO(b"my data"))
        assert path.read_bytes() == b"other writer"


def test_ftps_connection_closed_after_failed_write(ftps_server):
    class BrokenStream:
        def read(self, size):
            raise OSError("Simulated source read failure")
    with FTPSBackend(**ftps_server.options) as client:
        with pytest.raises(OSError, match="source read"):
            client._write_new("/site/partial.html", BrokenStream())
        assert client._ftp.sock is None


def test_new_file_appearing_during_snapshot_is_preserved(backend):
    client, server = backend
    path = server.storage / "site/index.html"
    class ChangingSource(io.BytesIO):
        def read(self, size):
            path.write_bytes(b"concurrent")
            return super().read(size)
    with pytest.raises(FileExistsError):
        client.upload("index.html", ChangingSource(b"new"), expected_sha256=sha256_bytes(b"new"))
    assert path.read_bytes() == b"concurrent"


@pytest.mark.parametrize("names", [["same", "same"], ["nested/file"]])
def test_untrusted_directory_listing_is_rejected(backend, monkeypatch, names):
    client, _ = backend
    monkeypatch.setattr(client, "_names", lambda _: names)
    with pytest.raises(UnsafeRemotePath):
        client.inventory()


def test_file_changing_after_hash_cannot_enter_inventory(backend, monkeypatch):
    client, server = backend
    path = server.storage / "site/index.html"
    path.write_bytes(b"before")
    checksum = client.checksum
    def changing(relative):
        digest = checksum(relative)
        path.write_bytes(b"changed size")
        return digest
    monkeypatch.setattr(client, "checksum", changing)
    with pytest.raises(IntegrityError, match="inventory"):
        client.inventory()


def test_ftps_never_falls_back_to_list_when_mlsd_unavailable(ftps_server, monkeypatch):
    from ftplib import error_perm
    with FTPSBackend(**ftps_server.options) as client:
        def unsupported(*args, **kwargs):
            raise error_perm("500 MLSD unavailable")
        monkeypatch.setattr(client._ftp, "mlsd", unsupported)
        with pytest.raises(error_perm):
            client.inventory()


def test_ftps_rejects_duplicate_listing_and_ignores_only_dot_entries(ftps_server, monkeypatch):
    with FTPSBackend(**ftps_server.options) as client:
        monkeypatch.setattr(client._ftp, "mlsd", lambda _: iter([
            (".", {"type": "cdir"}), ("..", {"type": "pdir"}), ("same", {}), ("same", {})]))
        with pytest.raises(UnsafeRemotePath, match="Duplicate"):
            client._listing("/site")


def test_ftps_preserves_fractional_utc_timestamp():
    entry = FTPSBackend._entry({"type": "file", "size": "3", "modify": "20260101000000.25"})
    assert entry.modified % 1 == 0.25


def test_root_directory_itself_is_supported(sftp_server):
    with SFTPBackend(**(sftp_server.options | {"root": "/"})) as client:
        assert client.inventory() == ()


@pytest.mark.parametrize("entry", [RemoteEntry("file"), RemoteEntry("file", -1, 0),
                                   RemoteEntry("file", 1, float("nan")), RemoteEntry("unsafe")])
def test_invalid_file_metadata_fails_closed(entry):
    from mcp_locaweb_sftp.backends.base import Backend
    with pytest.raises(UnsafeRemotePath):
        Backend._regular(entry)
