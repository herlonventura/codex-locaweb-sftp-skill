import json
import os
from pathlib import Path

from click.testing import CliRunner
import pytest
import yaml

from mcp_locaweb_sftp.cli import cli
from mcp_locaweb_sftp.config import Settings, Site, load_sites
from mcp_locaweb_sftp.credentials import CredentialKey


@pytest.fixture
def cli_environment(sftp_server, tmp_path, monkeypatch):
    local = tmp_path / "public_html"
    local.mkdir()
    options = sftp_server.options
    site = Site(host=options["host"], port=options["port"], user=options["username"], local_root=local.as_posix(),
                remote_root="/site", ssh_fingerprint=options["fingerprint"], credential_store="env", publish_enabled=True)
    sites = tmp_path / "sites.yaml"
    settings = tmp_path / "settings.yaml"
    sites.write_text(yaml.safe_dump({"example.com": site.model_dump(mode="json")}), encoding="utf-8")
    settings.write_text(yaml.safe_dump(Settings(publish_enabled=True).model_dump(mode="json")), encoding="utf-8")
    monkeypatch.setenv(CredentialKey.for_site("example.com", site).env_name, options["password"])
    args = ["--sites", str(sites), "--settings", str(settings), "--state-dir", str(tmp_path / "state")]
    return CliRunner(), args, sftp_server, local, sites, settings


def test_cli_real_preview_deploy_and_second_comparison(cli_environment):
    runner, args, server, local, *_ = cli_environment
    (local / "index.html").write_bytes(b"new")
    (server.storage / "site/index.html").write_bytes(b"old")
    os.utime(local / "index.html", (1700000010, 1700000010))
    os.utime(server.storage / "site/index.html", (1700000000, 1700000000))
    preview = runner.invoke(cli, args + ["previa", "example.com"])
    assert preview.exit_code == 0, preview.output
    digest = json.loads(preview.stdout)["data"]["preview_hash"]
    result = runner.invoke(cli, args + ["enviar", "example.com", "--preview-hash", digest, "--confirm"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "success"
    assert (server.storage / "site/index.html").read_bytes() == b"new"
    compared = runner.invoke(cli, args + ["comparar", "example.com"])
    assert json.loads(compared.stdout)["data"]["equal"] == ["index.html"]


@pytest.mark.parametrize("command", ["list", "listar", "info", "test", "testar", "backup", "credential"])
def test_cli_supported_read_operations(cli_environment, command):
    runner, args, _, _, _, _ = cli_environment
    tail = [command] if command in ("list", "listar") else [command, "example.com"]
    result = runner.invoke(cli, args + tail)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "success"
    assert "test-password-only" not in result.output


def test_cli_unknown_domain_and_invalid_arguments_never_echo_private_values(cli_environment):
    runner, args, server, *_ = cli_environment
    for tail in (["test", "unknown.com"], ["test", "example.com", "PRIVATE_MARKER"], ["--password", "PRIVATE_MARKER"]):
        result = runner.invoke(cli, args + tail)
        assert result.exit_code != 0
        assert "PRIVATE_MARKER" not in result.output
        assert json.loads(result.stdout)["status"] == "error"
    assert server.auth_attempts == 0


def test_disabled_publication_is_rejected_before_auth(cli_environment):
    runner, args, server, _, _, settings = cli_environment
    settings.write_text("publish_enabled: false\n", encoding="utf-8")
    result = runner.invoke(cli, args + ["deploy", "example.com", "--preview-hash", "a" * 64, "--confirm"])
    assert result.exit_code == 1
    assert server.auth_attempts == 0


def test_cli_conflict_exit_code_and_no_upload(cli_environment):
    runner, args, server, local, *_ = cli_environment
    (local / ".env").write_bytes(b"PRIVATE_MARKER")
    result = runner.invoke(cli, args + ["compare", "example.com"])
    assert result.exit_code == 3
    assert "PRIVATE_MARKER" not in result.output
    assert list((server.storage / "site").iterdir()) == []


def test_scan_key_does_not_authenticate_or_persist(cli_environment):
    runner, args, server, _, sites, _ = cli_environment
    before = sites.read_bytes()
    result = runner.invoke(cli, args + ["scan-key", "example.com"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["verified"] is False and data["fingerprint"] == server.options["fingerprint"]
    assert server.auth_attempts == 0 and sites.read_bytes() == before


def test_set_key_requires_confirmation_and_invalid_pin_does_not_modify_registry(cli_environment):
    runner, args, _, _, sites, _ = cli_environment
    before = sites.read_bytes()
    for tail in (["--fingerprint", "SHA256:" + "A" * 43], ["--fingerprint", "invalid", "--confirm"]):
        result = runner.invoke(cli, args + ["set-key", "example.com"] + tail)
        assert result.exit_code == 1
        assert sites.read_bytes() == before
    result = runner.invoke(cli, args + ["set-key", "example.com", "--fingerprint", "SHA256:" + "A" * 43, "--confirm"])
    assert result.exit_code == 0
    assert load_sites(sites)["example.com"].ssh_fingerprint == "SHA256:" + "A" * 43


def test_register_new_domain_is_disabled_and_cannot_replace(cli_environment, tmp_path):
    runner, args, _, _, sites, _ = cli_environment
    entry = tmp_path / "entry.yaml"
    original = load_sites(sites)["example.com"]
    entry.write_text(yaml.safe_dump({"new.example.com": original.model_dump(mode="json")}), encoding="utf-8")
    result = runner.invoke(cli, args + ["register", "new.example.com", "--entry-file", str(entry)])
    assert result.exit_code == 0, result.output
    assert not load_sites(sites)["new.example.com"].publish_enabled
    before = sites.read_bytes()
    assert runner.invoke(cli, args + ["register", "new.example.com", "--entry-file", str(entry)]).exit_code == 1
    assert sites.read_bytes() == before


def test_hidden_credential_prompt_does_not_print_password(cli_environment, monkeypatch):
    import mcp_locaweb_sftp.cli as module
    runner, args, *_ = cli_environment
    stored = []
    class Store:
        kind = "keyring"
        def set(self, key, value):
            stored.append(value.get_secret_value())
    monkeypatch.setattr(module, "selected_store", lambda *args: Store())
    result = runner.invoke(cli, args + ["credential", "example.com"], input="PRIVATE_MARKER\nPRIVATE_MARKER\n")
    assert result.exit_code == 0, result.output
    assert stored == ["PRIVATE_MARKER"] and "PRIVATE_MARKER" not in result.output
    assert json.loads(result.stdout)["status"] == "success"


def test_migrate_cli_uses_existing_logic_without_connection(tmp_path):
    source = tmp_path / "legacy"
    (source / "config").mkdir(parents=True)
    (source / "config/sites.json").write_text(json.dumps({"example.com": {"protocol": "sftp",
        "host": "sftp.example.com", "username": "test", "remoteRoot": "/site", "localRoot": "C:/Sites/test"}}))
    (source / "config/settings.json").write_text('{"publishEnabled":true}')
    result = CliRunner().invoke(cli, ["migrar", "--from", str(source), "--to", str(tmp_path / "new"),
                                   "--local-root", "example.com=" + tmp_path.as_posix()])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"]["pending_credentials"] == ["example.com"]
    assert load_sites(tmp_path / "new/sites.yaml")["example.com"].local_root == tmp_path.as_posix()


def test_help_lists_aliases_without_reading_configuration():
    for tail in (["--help"], ["help"], ["deploy", "--help"]):
        result = CliRunner().invoke(cli, tail)
        assert result.exit_code == 0
        assert "--preview-hash" in result.output if tail[0] == "deploy" else "enviar" in result.output


@pytest.fixture
def fresh_setup(tmp_path, monkeypatch):
    import mcp_locaweb_sftp.cli as module
    def forbidden(*args, **kwargs):
        pytest.fail("Setup must not connect or scan a host")
    monkeypatch.setattr(module, "open_site", forbidden)
    monkeypatch.setattr(module.socket, "create_connection", forbidden)
    sites, settings = tmp_path / "private/sites.yaml", tmp_path / "private/settings.yaml"
    args = ["--sites", str(sites), "--settings", str(settings)]
    def answers(protocol="sftp", provider="env", pin="SHA256:" + "A" * 43):
        values = ["example.com", protocol, "", "", "test-user", str(tmp_path / "public_html"), "", provider]
        if protocol == "sftp":
            values.append(pin)
        return "\n".join(values) + "\n"
    return CliRunner(), args, sites, settings, answers


@pytest.mark.parametrize("protocol", ["sftp", "ftps"])
def test_setup_from_scratch_never_connects_or_enables_publication(fresh_setup, protocol):
    from mcp_locaweb_sftp.config import load_settings
    runner, args, sites, settings, answers = fresh_setup
    result = runner.invoke(cli, args + ["configurar"], input=answers(protocol=protocol))
    assert result.exit_code == 0, result.output
    site = load_sites(sites)["example.com"]
    assert site.port == (22 if protocol == "sftp" else 21)
    assert site.host == "example.com" and site.remote_root == "/public_html"
    assert not site.publish_enabled and not site.ftps_write_preconditions_confirmed
    assert not load_settings(settings).publish_enabled
    data = json.loads(result.stdout)["data"]
    assert data["credential_status"] == "external_injection_required"
    assert data["environment_variable"] == CredentialKey.for_site("example.com", site).env_name
    assert data["connection_tested"] is False


def test_setup_without_pin_preserves_disabled_pending_registration(fresh_setup, monkeypatch):
    import mcp_locaweb_sftp.cli as module
    runner, args, sites, _, answers = fresh_setup
    monkeypatch.setattr(module, "selected_store", lambda *a: pytest.fail("No credential access before pin confirmation"))
    result = runner.invoke(cli, args + ["setup"], input=answers(provider="keyring", pin=""))
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"]["credential_status"] == "pending_fingerprint"
    assert load_sites(sites)["example.com"].ssh_fingerprint is None


@pytest.mark.parametrize("availability", ["stored", "unavailable", "declined"])
def test_setup_credential_prompt_is_hidden_and_no_plaintext_file(fresh_setup, monkeypatch, availability):
    import mcp_locaweb_sftp.cli as module
    from mcp_locaweb_sftp.credentials import CredentialError
    runner, args, sites, _, answers = fresh_setup
    stored = []
    class Store:
        def set(self, key, secret):
            stored.append(secret.get_secret_value())
    def factory(*a):
        if availability == "unavailable":
            raise CredentialError("cofre indisponível")
        return Store()
    monkeypatch.setattr(module, "selected_store", factory)
    suffix = "n\n" if availability == "declined" else "y\nPRIVATE_MARKER\nPRIVATE_MARKER\n"
    result = runner.invoke(cli, args + ["setup"], input=answers(provider="keyring") + suffix)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"]["credential_status"] == ("stored" if availability == "stored" else "pending")
    assert stored == (["PRIVATE_MARKER"] if availability == "stored" else [])
    assert "PRIVATE_MARKER" not in result.output
    assert all(b"PRIVATE_MARKER" not in p.read_bytes() for p in sites.parent.iterdir() if p.is_file())
    assert not load_sites(sites)["example.com"].publish_enabled


def test_setup_existing_domain_is_not_replaced(fresh_setup):
    runner, args, sites, settings, answers = fresh_setup
    assert runner.invoke(cli, args + ["setup"], input=answers()).exit_code == 0
    before = sites.read_bytes(), settings.read_bytes()
    result = runner.invoke(cli, args + ["setup"], input="example.com\n")
    assert result.exit_code == 1
    assert (sites.read_bytes(), settings.read_bytes()) == before


def test_setup_invalid_site_saves_nothing(fresh_setup):
    runner, args, sites, settings, answers = fresh_setup
    result = runner.invoke(cli, args + ["setup"], input=answers(pin="not-a-pin"))
    assert result.exit_code == 1
    assert not sites.exists() and not settings.exists()


def test_setup_age_metadata_and_deferred_password(fresh_setup, tmp_path, monkeypatch):
    import mcp_locaweb_sftp.cli as module
    from mcp_locaweb_sftp.config import load_settings
    from mcp_locaweb_sftp.credentials import CredentialError
    runner, args, sites, settings, answers = fresh_setup
    def unavailable(*args):
        raise CredentialError("pending identity")
    monkeypatch.setattr(module, "selected_store", unavailable)
    age_answers = [str(tmp_path / "vault"), str(tmp_path / "age.exe"), "", ""]
    result = runner.invoke(cli, args + ["setup"], input=answers(provider="age") + "\n".join(age_answers) + "\n")
    assert result.exit_code == 0, result.output
    assert load_sites(sites)["example.com"].credential_store == "age"
    assert load_settings(settings).age.directory == str(tmp_path / "vault")
    assert json.loads(result.stdout)["data"]["credential_status"] == "pending"


@pytest.mark.parametrize("unsafe_field", ["directory", "identity"])
def test_setup_rejects_existing_age_secrets_in_publication_root(fresh_setup, tmp_path, unsafe_field):
    runner, args, sites, settings, answers = fresh_setup
    settings.parent.mkdir()
    age = {"directory": str(tmp_path / "vault"), "identity": str(tmp_path / "identity"), "executable": str(tmp_path / "age.exe")}
    age[unsafe_field] = str(tmp_path / "public_html" / unsafe_field)
    settings.write_text(yaml.safe_dump({"age": age}), encoding="utf-8")
    before = settings.read_bytes()
    result = runner.invoke(cli, args + ["setup"], input=answers(provider="age"))
    assert result.exit_code == 1
    assert not sites.exists() and settings.read_bytes() == before


def test_setup_rejects_private_registration_inside_site(fresh_setup, tmp_path):
    runner, args, sites, settings, answers = fresh_setup
    args[1] = str(tmp_path / "public_html/sites.yaml")
    result = runner.invoke(cli, args + ["setup"], input=answers())
    assert result.exit_code == 1
    assert not Path(args[1]).exists() and not settings.exists()
