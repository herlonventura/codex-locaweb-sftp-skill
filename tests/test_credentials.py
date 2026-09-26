import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import traceback

from pydantic import SecretStr
import pytest

from mcp_locaweb_sftp.config import Settings, Site, load_sites
from mcp_locaweb_sftp.config.migration import migrate_legacy
from mcp_locaweb_sftp.connection import open_site
from mcp_locaweb_sftp.credentials import AgeStore, CredentialError, CredentialKey, EnvStore, KeyringStore
from mcp_locaweb_sftp.credentials.base import decode_secret, encode_secret
from mcp_locaweb_sftp.credentials.factory import selected_store


@pytest.fixture
def key():
    return CredentialKey.for_site("example.com", Site(host="sftp.example.com", user="test-user",
        remote_root="/site", local_root="/srv/site", ssh_fingerprint="SHA256:" + "A" * 43))


@pytest.fixture
def fake_keyring(monkeypatch):
    import mcp_locaweb_sftp.credentials.keyring_store as module
    class MemoryVault:
        priority = 1
        values = {}
        def get_password(self, service, user):
            return self.values.get((service, user))
        def set_password(self, service, user, value):
            self.values[(service, user)] = value
    vault = MemoryVault()
    monkeypatch.setattr(module, "_native_backend", lambda: vault)
    return vault


@pytest.fixture
def age_setup(tmp_path):
    executable = os.environ.get("MCP_LOCAWEB_TEST_AGE") or shutil.which("age")
    if executable is None:
        pytest.skip("Real age executable not installed/configured for this test run")
    executable = Path(executable).resolve()
    keygen = executable.with_name("age-keygen.exe" if os.name == "nt" else "age-keygen")
    identity = tmp_path / "test.identity"
    subprocess.run([str(keygen), "-o", str(identity)], capture_output=True, check=True, timeout=15,
                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    public = subprocess.run([str(keygen), "-y", str(identity)], capture_output=True, check=True, timeout=15,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0).stdout.decode().strip()
    directory = tmp_path / "vault"
    directory.mkdir(mode=0o700)
    return dict(directory=directory, executable=executable, identity=identity, recipient=public)


def test_binding_changes_for_connection_identity_but_not_local_layout():
    values = dict(host="sftp.example.com", user="test-user", remote_root="/site", local_root="/srv/site",
                  ssh_fingerprint="SHA256:" + "A" * 43)
    first = CredentialKey.for_site("example.com", Site(**values))
    assert first == CredentialKey.for_site("EXAMPLE.COM", Site(**(values | {"local_root": "C:/Sites/test"})))
    for change in ({"host": "other.example.com"}, {"user": "other"}, {"port": 2222},
                   {"remote_root": "/other"}, {"ssh_fingerprint": "SHA256:" + "B" * 43}):
        assert first != CredentialKey.for_site("example.com", Site(**(values | change)))
    assert first != CredentialKey.for_site("other.com", Site(**values))
    with pytest.raises(ValueError):
        CredentialKey("../unsafe")


def test_environment_is_explicit_bound_and_read_only(key, monkeypatch):
    monkeypatch.setenv("MCP_LOCAWEB_PASSWORD", "do-not-use-global-secret")
    monkeypatch.delenv(key.env_name, raising=False)
    with pytest.raises(CredentialError):
        EnvStore().get(key)
    monkeypatch.setenv(key.env_name, "fictional-password")
    value = EnvStore().get(key)
    assert value.get_secret_value() == "fictional-password"
    assert "fictional-password" not in str(value) and "fictional-password" not in repr(value)
    assert not hasattr(EnvStore(), "set")


@pytest.mark.parametrize("value", ["", "x\ny", "x\ry", "x\x00y", pytest.param("x" * 16385, id="oversized-secret")])
def test_invalid_secret_is_rejected(value):
    from mcp_locaweb_sftp.credentials.base import secret
    with pytest.raises(CredentialError):
        secret(value)


def test_keyring_roundtrip_and_missing_credential(fake_keyring, key):
    store = KeyringStore()
    with pytest.raises(CredentialError):
        store.get(key)
    store.set(key, SecretStr("fictional-password"))
    assert store.get(key).get_secret_value() == "fictional-password"
    with pytest.raises(CredentialError):
        store.set(key, "plaintext-argument")


def test_keyring_errors_do_not_fall_back_or_leak(fake_keyring, key, monkeypatch):
    marker = "PRIVATE_MARKER"
    def failing(*args):
        raise RuntimeError(marker)
    monkeypatch.setattr(fake_keyring, "get_password", failing)
    monkeypatch.setattr(fake_keyring, "set_password", failing)
    monkeypatch.setenv(key.env_name, marker)
    store = KeyringStore()
    for operation in (lambda: store.get(key), lambda: store.set(key, SecretStr(marker))):
        with pytest.raises(CredentialError) as result:
            operation()
        assert marker not in "".join(traceback.format_exception(result.value))


def test_unavailable_vault_fails_closed(monkeypatch):
    import mcp_locaweb_sftp.credentials.keyring_store as module
    def unavailable():
        raise ImportError("not installed")
    monkeypatch.setattr(module, "_native_backend", unavailable)
    with pytest.raises(CredentialError, match="no fallback"):
        KeyringStore()


def test_credential_envelope_cannot_be_moved_between_connections(key):
    payload = encode_secret(key, SecretStr("fictional-password"))
    assert decode_secret(key, payload).get_secret_value() == "fictional-password"
    with pytest.raises(CredentialError):
        decode_secret(CredentialKey("b" * 64), payload)
    for data in (b"[]", b"invalid", b'{"version":1}', b"null"):
        with pytest.raises(CredentialError):
            decode_secret(key, data)


def test_age_real_roundtrip_only_ciphertext_on_disk(age_setup, key):
    store = AgeStore(**age_setup)
    value = "fictional-UNICODE-senha-á-123"
    store.set(key, SecretStr(value))
    assert store.get(key).get_secret_value() == value
    files = list(age_setup["directory"].iterdir())
    assert [p.name for p in files] == [key.digest + ".age"]
    assert files[0].read_bytes().startswith(b"age-encryption.org/v1\n")
    assert value.encode() not in files[0].read_bytes()
    store.set(key, SecretStr("rotated-test-password"))
    assert store.get(key).get_secret_value() == "rotated-test-password"


def test_age_swapped_envelope_and_corruption_fail(age_setup, key):
    store = AgeStore(**age_setup)
    store.set(key, SecretStr("fictional-password"))
    path = age_setup["directory"] / (key.digest + ".age")
    other = CredentialKey("b" * 64)
    (age_setup["directory"] / (other.digest + ".age")).write_bytes(path.read_bytes())
    with pytest.raises(CredentialError, match="another connection"):
        store.get(other)
    path.write_bytes(path.read_bytes()[:-10] + b"corruption")
    with pytest.raises(CredentialError):
        store.get(key)


def test_age_missing_files_and_required_options(age_setup, key):
    store = AgeStore(**age_setup)
    with pytest.raises(CredentialError, match="read"):
        store.get(key)
    with pytest.raises(CredentialError, match="recipient"):
        AgeStore(**(age_setup | {"recipient": None})).set(key, SecretStr("test"))
    with pytest.raises(CredentialError, match="identity"):
        AgeStore(**(age_setup | {"identity": None})).get(key)
    for options in ({"recipient": "age-plugin-test"}, {"executable": Path("age")},
                    {"directory": age_setup["directory"] / "absent"}, {"identity": age_setup["identity"] / "absent"}):
        with pytest.raises(CredentialError):
            AgeStore(**(age_setup | options))


def test_age_timeout_suppresses_plaintext_process_output(age_setup, key, monkeypatch):
    store = AgeStore(**age_setup)
    marker = "PRIVATE_MARKER"
    def failing(command, **kwargs):
        assert marker not in " ".join(command)
        assert kwargs["shell"] is False
        assert marker.encode() in kwargs["input"]
        raise subprocess.TimeoutExpired(command, 15, output=marker.encode(), stderr=marker.encode())
    monkeypatch.setattr(subprocess, "run", failing)
    with pytest.raises(CredentialError) as result:
        store.set(key, SecretStr(marker))
    assert marker not in "".join(traceback.format_exception(result.value))
    assert list(age_setup["directory"].iterdir()) == []


def test_age_rejects_plaintext_result_from_wrong_executable(age_setup, key, monkeypatch):
    store = AgeStore(**age_setup)
    monkeypatch.setattr(store, "_run", lambda *args: b"plaintext")
    with pytest.raises(CredentialError, match="encrypted format"):
        store.set(key, SecretStr("fictional"))
    assert list(age_setup["directory"].iterdir()) == []


def test_open_registered_sftp_with_environment_credentials(sftp_server, tmp_path, monkeypatch):
    options = sftp_server.options
    site = Site(host=options["host"], port=options["port"], user=options["username"], remote_root="/site",
                local_root=tmp_path.as_posix(), ssh_fingerprint=options["fingerprint"], credential_store="env")
    monkeypatch.setenv(CredentialKey.for_site("example.com", site).env_name, options["password"])
    (sftp_server.storage / "site/index.html").write_bytes(b"fictional site")
    with open_site({"example.com": site}, "EXAMPLE.COM") as connection:
        output = io.BytesIO()
        connection.download("index.html", output)
        assert output.getvalue() == b"fictional site"
    with pytest.raises(ValueError):
        open_site({"example.com": site}, "unknown.com", EnvStore())


def test_open_ftps_with_age_credentials(ftps_server, age_setup, tmp_path):
    options = ftps_server.options
    site = Site(protocol="ftps", host=options["host"], port=options["port"], user=options["username"],
                remote_root="/site", local_root=tmp_path.as_posix(), ca_file=Path(options["ca_file"]).as_posix(),
                credential_store="age")
    store = AgeStore(**age_setup)
    store.set(CredentialKey.for_site("example.com", site), SecretStr(options["password"]))
    with open_site({"example.com": site}, "example.com", store) as connection:
        assert connection.inventory() == ()


def test_wrong_provider_and_missing_pin_never_fetch_secrets(tmp_path):
    class DoNotRead:
        kind = "env"
        def get(self, key):
            pytest.fail("Credential accessed before readiness/provider checks")
    site = Site(host="example.com", user="test", remote_root="/site", local_root=tmp_path.as_posix())
    with pytest.raises(ValueError, match="fingerprint"):
        open_site({"example.com": site}, "example.com", DoNotRead())
    site = Site(**(site.model_dump() | {"ssh_fingerprint": "SHA256:" + "A" * 43}))
    with pytest.raises(CredentialError, match="provider"):
        open_site({"example.com": site}, "example.com", DoNotRead())


def test_wrong_password_connection_error_is_redacted(sftp_server, tmp_path, monkeypatch):
    options = sftp_server.options
    site = Site(host=options["host"], port=options["port"], user=options["username"], remote_root="/site",
                local_root=tmp_path.as_posix(), ssh_fingerprint=options["fingerprint"], credential_store="env")
    marker = "NEVER_PRINT_THIS_PASSWORD"
    monkeypatch.setenv(CredentialKey.for_site("example.com", site).env_name, marker)
    with pytest.raises(ConnectionError) as result:
        open_site({"example.com": site}, "example.com", EnvStore())
    assert marker not in "".join(traceback.format_exception(result.value))


def test_migrate_enroll_and_connect_without_dpapi_dependency(sftp_server, fake_keyring, tmp_path):
    options = sftp_server.options
    source = tmp_path / "legacy"
    (source / "config").mkdir(parents=True)
    (source / "config/sites.json").write_text(json.dumps({"example.com": {
        "protocol": "sftp", "host": options["host"], "port": options["port"], "username": options["username"],
        "localRoot": "C:/Sites/example.com", "remoteRoot": "/site",
        "sshHostKeyFingerprint": "ssh-rsa 2048 " + options["fingerprint"]}}), encoding="utf-8")
    (source / "config/settings.json").write_text('{"publishEnabled":true}', encoding="utf-8")
    result = migrate_legacy(source, tmp_path / "migrated")
    assert result.pending_credentials == ("example.com",)
    sites = load_sites(tmp_path / "migrated/sites.yaml")
    store = KeyringStore()
    store.set(CredentialKey.for_site("example.com", sites["example.com"]), SecretStr(options["password"]))
    with open_site(sites, "example.com", store) as connection:
        assert connection.inventory() == ()
    assert sites["example.com"].publish_enabled is False


def test_factory_uses_only_explicit_provider(age_setup, key, fake_keyring, tmp_path):
    base = dict(host="example.com", user="test", remote_root="/site", local_root=tmp_path.as_posix())
    assert isinstance(selected_store(Site(**base), Settings()), KeyringStore)
    assert isinstance(selected_store(Site(**base, credential_store="env"), Settings()), EnvStore)
    age_site = Site(**base, credential_store="age")
    with pytest.raises(CredentialError, match="settings"):
        selected_store(age_site, Settings())
    values = {name: path.as_posix() if isinstance(path, Path) else path for name, path in age_setup.items()}
    store = selected_store(age_site, Settings(age=values))
    store.set(key, SecretStr("fictional-password"))
    assert store.get(key).get_secret_value() == "fictional-password"


def test_native_keyring_selection_ignores_configured_plaintext_plugin(fake_keyring, monkeypatch):
    import keyring
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyrings.alt.file.PlaintextKeyring")
    monkeypatch.setattr(keyring, "get_keyring", lambda: pytest.fail("Generic plugin discovery is forbidden"))
    assert KeyringStore().kind == "keyring"


def test_age_invalid_identity_and_disk_error_are_sanitized(age_setup, key, monkeypatch):
    import mcp_locaweb_sftp.credentials.age_store as module
    store = AgeStore(**age_setup)
    def failing(*args, **kwargs):
        raise OSError("simulated private path or value")
    monkeypatch.setattr(module, "write_private", failing)
    with pytest.raises(CredentialError, match="persist"):
        store.set(key, SecretStr("fictional"))
    age_setup["identity"].write_text("AGE-PLUGIN-UNSUPPORTED", encoding="utf-8")
    with pytest.raises(CredentialError, match="setup"):
        AgeStore(**age_setup)
