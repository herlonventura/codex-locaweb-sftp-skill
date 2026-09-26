import json
import os
from pathlib import Path
import traceback

from pydantic import ValidationError
import pytest
import yaml

from vhe_deploy.config import ConfigError, Settings, Site, load_settings, load_sites
from vhe_deploy.config.loader import parse_document, sites_from_mapping
from vhe_deploy.config.migration import migrate_legacy
from vhe_deploy.config.schema import native_path
from vhe_deploy.core.guards import is_blocked_path
from vhe_deploy.local_files import checked_path, read_limited, write_private


@pytest.fixture
def site_values(tmp_path):
    return dict(host="server.example.com", user="test-user", remote_root="/site",
                local_root=tmp_path.as_posix(), ssh_fingerprint="SHA256:" + "A" * 43)


@pytest.fixture
def legacy_installation(tmp_path):
    source = tmp_path / "legacy"
    (source / "config").mkdir(parents=True)
    (source / "credentials").mkdir()
    (source / "credentials/example.com.dpapi").write_bytes(b"fictitious-ciphertext-do-not-decrypt")
    old = {"example.com": {"protocol": "sftp", "host": "server.example.com", "port": 22,
        "username": "test-user", "remoteRoot": "/site", "localRoot": "C:\\Sites\\example.com",
        "sshHostKeyFingerprint": "ssh-ed25519 256 SHA256:" + "A" * 43, "allowPlainFtp": False}}
    (source / "config/sites.json").write_text(json.dumps(old), encoding="utf-8")
    (source / "config/settings.json").write_text(json.dumps({"winscpDirectory": "C:/WinSCP", "publishEnabled": True}), encoding="utf-8")
    return source


def test_config_defaults_and_protocol_specific_port(site_values):
    site = Site(**site_values)
    assert site.port == 22 and not site.publish_enabled and site.credential_store == "keyring"
    ftps = Site(**(site_values | {"protocol": "ftps", "ssh_fingerprint": None}))
    assert ftps.port == 21
    assert not Settings().publish_enabled
    assert is_blocked_path(".env", site.blocked_paths)


@pytest.mark.parametrize("override", [
    {"port": "22"}, {"port": True}, {"port": 0}, {"port": 65536}, {"publish_enabled": "false"},
    {"protocol": "ftp"}, {"credential_store": "plaintext"}, {"password": "secret"},
    {"host": "https://example.com"}, {"user": "a\nb"}, {"user": ""},
    {"remote_root": "relative"}, {"remote_root": "/site/../other"},
    {"ssh_fingerprint": "*"}, {"ssh_fingerprint": "MD5:AA"},
    {"ca_file": "/private/cert.pem"}, {"protocol": "ftps"},
    {"blocked_paths": "*.sql"}, {"blocked_paths": ["**/*.sql"]},
    {"local_root": "relative/site"}, {"local_root": "C:relative"}, {"local_root": "C:/a/../b"},
    {"local_root": "//server/share"}, {"local_root": "C:/file:stream"}, {"local_root": "C:/NUL"},
])
def test_invalid_configuration_fails_closed(site_values, override):
    with pytest.raises(ValidationError):
        Site(**(site_values | override))


@pytest.mark.parametrize("values", [{"publish_enabled": 1}, {"publish_enabled": "true"},
    {"schema_version": True}, {"schema_version": 2}, {"timeout_seconds": float("nan")},
    {"timeout_seconds": 0}, {"timeout_seconds": 200}, {"password": "secret"}])
def test_invalid_settings(values):
    with pytest.raises(ValidationError):
        Settings(**values)


def test_yaml_loading_no_value_coercion_and_immutable_config(tmp_path, site_values):
    path = tmp_path / "sites.yaml"
    path.write_text(yaml.safe_dump({"EXAMPLE.com": site_values | {"blocked_paths": ["*.sql"]}}), encoding="utf-8")
    site = load_sites(path)["example.com"]
    assert site.blocked_paths == ("*.sql",)
    with pytest.raises(ValidationError):
        site.host = "other.example.com"
    settings = tmp_path / "settings.yaml"
    settings.write_text("schema_version: 1\npublish_enabled: false\ntimeout_seconds: 3\n", encoding="utf-8")
    assert load_settings(settings).timeout_seconds == 3


@pytest.mark.parametrize("document", [
    b"a: 1\na: 2", b"a: &value {b: 1}\nc: *value", b"a: !!python/object/apply:os.system ['cmd']",
    b"1: x", b"a: {<<: {b: c}}", b"[a, b]", b"", b"\xff", b"a: [",
    b"x: " + b"[" * 40 + b"0" + b"]" * 40,
    pytest.param(b"a: " + b"x" * (1024 * 1024), id="oversized-document"),
])
def test_unsafe_yaml_input_is_rejected(document):
    with pytest.raises(ConfigError):
        parse_document(document)


@pytest.mark.parametrize("document", [b'{"a": 1, "a": 2}', b'{"a": NaN}', b'[]', b'{'])
def test_unsafe_json_input_is_rejected(document):
    with pytest.raises(ConfigError):
        parse_document(document, json_format=True)


def test_domain_collisions_and_empty_registry(site_values):
    for data in ({}, {"example.com": site_values, "EXAMPLE.COM": site_values}, {"wrong/path": site_values}):
        with pytest.raises(ConfigError):
            sites_from_mapping(data)


def test_loader_error_does_not_echo_accidental_password(tmp_path, site_values):
    marker = "PRIVATE_VALUE_NOT_FOR_TRACEBACK"
    path = tmp_path / "sites.yaml"
    path.write_text(yaml.safe_dump({"example.com": site_values | {"password": marker}}), encoding="utf-8")
    try:
        load_sites(path)
    except ConfigError as error:
        assert marker not in str(error)
        assert marker not in "".join(traceback.format_exception(error))
    else:
        pytest.fail("Unexpectedly accepted a plaintext secret")


def test_missing_config_and_directory_are_rejected(tmp_path):
    for path in (tmp_path / "absent.yaml", tmp_path):
        with pytest.raises(ConfigError):
            load_sites(path)


def test_native_path_never_interprets_foreign_drive_as_relative(tmp_path):
    assert native_path(tmp_path.as_posix()) == tmp_path
    foreign = "/srv/site" if os.name == "nt" else "C:/Sites/example.com"
    with pytest.raises(ValueError, match="another OS"):
        native_path(foreign)


def test_missing_pin_is_registration_only(site_values):
    site = Site(**(site_values | {"ssh_fingerprint": None}))
    with pytest.raises(ValueError, match="Confirm"):
        site.require_connection_ready()


def test_migration_is_isolated_preserves_source_and_disables_publication(legacy_installation, tmp_path):
    before = {p.relative_to(legacy_installation): p.read_bytes() for p in legacy_installation.rglob("*") if p.is_file()}
    destination = tmp_path / "new"
    result = migrate_legacy(legacy_installation, destination, local_roots={"example.com": tmp_path.as_posix()})
    assert result.domains == result.pending_credentials == ("example.com",)
    assert result.pending_ssh_fingerprints == ()
    site = load_sites(destination / "sites.yaml")["example.com"]
    assert site.local_root == tmp_path.as_posix()
    assert site.ssh_fingerprint == "SHA256:" + "A" * 43
    assert not site.publish_enabled and not load_settings(destination / "settings.yaml").publish_enabled
    assert (destination / "migration.json").is_file()
    assert {p.relative_to(legacy_installation): p.read_bytes() for p in legacy_installation.rglob("*") if p.is_file()} == before
    assert sorted(p.name for p in destination.iterdir()) == ["migration.json", "settings.yaml", "sites.yaml"]
    with pytest.raises(ConfigError):
        migrate_legacy(legacy_installation, destination)


def test_legacy_load_cannot_carry_publish_flag(legacy_installation):
    assert not load_settings(legacy_installation / "config/settings.json").publish_enabled
    assert not load_sites(legacy_installation / "config/sites.json")["example.com"].publish_enabled


@pytest.mark.parametrize("override", [{"password": "do-not-export"}, {"protocol": "ftp"},
    {"allowPlainFtp": True}, {"sshHostKeyFingerprint": "*"}, {"sshHostKeyFingerprint": "SHA256:A;SHA256:B"}])
def test_unsupported_legacy_data_does_not_create_destination(legacy_installation, tmp_path, override):
    path = legacy_installation / "config/sites.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["example.com"].update(override)
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigError):
        migrate_legacy(legacy_installation, tmp_path / "new")
    assert not (tmp_path / "new").exists()


def test_migration_marks_missing_pin_pending(legacy_installation, tmp_path):
    path = legacy_installation / "config/sites.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["example.com"]["sshHostKeyFingerprint"] = ""
    path.write_text(json.dumps(data), encoding="utf-8")
    result = migrate_legacy(legacy_installation, tmp_path / "new")
    assert result.pending_ssh_fingerprints == ("example.com",)


def test_migration_rejects_unknown_mapping_and_output_inside_source(legacy_installation, tmp_path):
    with pytest.raises(ConfigError):
        migrate_legacy(legacy_installation, tmp_path / "new", local_roots={"other.example.com": "/srv/site"})
    with pytest.raises(ConfigError):
        migrate_legacy(legacy_installation, legacy_installation / "output")


def test_failed_migration_keeps_incomplete_output_without_completion_marker(legacy_installation, tmp_path, monkeypatch):
    import vhe_deploy.config.migration as module
    original = module.write_private
    def failing(path, data):
        if path.name == "settings.yaml":
            raise OSError("Simulated disk error")
        original(path, data)
    monkeypatch.setattr(module, "write_private", failing)
    with pytest.raises(ConfigError, match="incomplete"):
        migrate_legacy(legacy_installation, tmp_path / "new")
    assert (tmp_path / "new/sites.yaml").is_file()
    assert not (tmp_path / "new/migration.json").exists()


def test_private_write_does_not_overwrite_and_checks_size(tmp_path):
    path = tmp_path / "data"
    write_private(path, b"original")
    with pytest.raises(FileExistsError):
        write_private(path, b"other")
    assert path.read_bytes() == b"original"
    write_private(path, b"replacement", replace=True)
    assert path.read_bytes() == b"replacement"
    assert list(tmp_path.glob(".write-*")) == []
    with pytest.raises(ValueError):
        read_limited(path, 2)
    if os.name != "nt":
        assert path.stat().st_mode & 0o077 == 0


def test_linked_private_path_is_rejected_without_following(tmp_path, monkeypatch):
    from types import SimpleNamespace
    original = Path.lstat
    target = tmp_path / "link"
    def fake_lstat(self, *args, **kwargs):
        if self == target:
            return SimpleNamespace(st_mode=0o100600, st_file_attributes=0x400)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, "lstat", fake_lstat)
    with pytest.raises(ValueError, match="reparse"):
        checked_path(target / "credential")


@pytest.mark.parametrize("content", [b'{"publishEnabled":"true"}', b'{"password":"private"}'])
def test_invalid_legacy_settings_are_rejected(tmp_path, content):
    path = tmp_path / "settings.json"
    path.write_bytes(content)
    with pytest.raises(ConfigError):
        load_settings(path)


def test_age_settings_contain_only_paths_and_public_recipient(tmp_path):
    values = dict(directory=tmp_path.as_posix(), executable=(tmp_path / "age.exe").as_posix())
    assert Settings(age=values).age.identity is None
    for extra in ({"password": "private"}, {"recipient": "not-an-age-key"}, {"identity": "relative.key"}):
        with pytest.raises(ValidationError):
            Settings(age=values | extra)
