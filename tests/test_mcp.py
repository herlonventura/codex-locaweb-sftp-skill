import json
import os
from pathlib import Path
import sys

from mcp import Client, StdioServerParameters
import pytest
import yaml

from mcp_locaweb_sftp.config import Settings, Site, load_sites
from mcp_locaweb_sftp.credentials import CredentialKey
from mcp_locaweb_sftp.mcp_server import TOOL_DEFINITIONS, create_server
from mcp_locaweb_sftp.operations import Runtime


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(params=["sftp", "ftps"])
def mcp_environment(request, tmp_path, monkeypatch):
    protocol = request.param
    server = request.getfixturevalue(protocol + "_server")
    options = server.options
    local = tmp_path / "public_html"
    local.mkdir()
    site = Site(protocol=protocol, host=options["host"], port=options["port"], user=options["username"],
        local_root=local.as_posix(), remote_root="/site", publish_enabled=True, ftps_write_preconditions_confirmed=True,
        credential_store="env", ssh_fingerprint=options.get("fingerprint"),
        ca_file=Path(options["ca_file"]).as_posix() if "ca_file" in options else None)
    sites, settings = tmp_path / "sites.yaml", tmp_path / "settings.yaml"
    sites.write_text(yaml.safe_dump({"example.com": site.model_dump(mode="json")}), encoding="utf-8")
    settings.write_text(yaml.safe_dump(Settings(publish_enabled=True).model_dump(mode="json")), encoding="utf-8")
    environment = {CredentialKey.for_site("example.com", site).env_name: options["password"]}
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    (server.storage / "site/index.html").write_bytes(b"old remote content")
    (server.storage / "site/keep.txt").write_bytes(b"preserve")
    (local / "index.html").write_bytes(b"new")
    os.utime(server.storage / "site/index.html", (1700000000, 1700000000))
    os.utime(local / "index.html", (1700000010, 1700000010))
    return Runtime(sites, settings, tmp_path / "state"), server, local, site, environment


def data(result, status="success"):
    assert result.structured_content["status"] == status, result
    assert result.is_error == (status != "success")
    assert json.loads(result.content[0].text) == result.structured_content
    return result.structured_content["data"]


@pytest.mark.anyio
async def test_official_stdio_client_discovers_and_executes_verified_deploy(mcp_environment):
    runtime, server, local, site, environment = mcp_environment
    environment.update(PYTHONPATH=str(Path("src").resolve()), PYTHONIOENCODING="utf-8")
    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_locaweb_sftp.mcp_server",
        "--sites", str(runtime.sites_file), "--settings", str(runtime.settings_file), "--state-dir", str(runtime.state)], env=environment)
    async with Client(params, read_timeout_seconds=30) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert set(tools) == set(TOOL_DEFINITIONS)
        assert tools["deploy_site"].annotations.destructive_hint is True
        assert tools["preview_deploy"].annotations.read_only_hint is False
        assert tools["compare_site"].annotations.read_only_hint is True
        assert "preview_token" in tools["deploy_site"].input_schema["required"]
        assert data(await client.call_tool("list_sites"))["sites"][0]["domain"] == "example.com"
        data(await client.call_tool("test_connection", {"domain": "example.com"}))
        compared = data(await client.call_tool("compare_site", {"domain": "example.com"}))
        assert "preview_token" not in compared
        backup = data(await client.call_tool("backup_site", {"domain": "example.com"}))
        assert Path(backup["run_directory"], "backup/files/index.html").read_bytes() == b"old remote content"
        preview = data(await client.call_tool("preview_deploy", {"domain": "example.com"}))
        args = {"domain": "example.com", "preview_hash": preview["preview_hash"], "preview_token": preview["preview_token"], "confirm": True}
        deployed = data(await client.call_tool("deploy_site", args))
        assert deployed["uploaded"] == ["index.html"]
        assert (server.storage / "site/index.html").read_bytes() == b"new"
        assert (server.storage / "site/keep.txt").read_bytes() == b"preserve"
        assert Path(deployed["run_directory"], "backup/files/index.html").read_bytes() == b"old remote content"
        data(await client.call_tool("deploy_site", args), "error")
    for path in runtime.state.rglob("*"):
        if path.is_file():
            assert preview["preview_token"].encode() not in path.read_bytes()


@pytest.mark.anyio
async def test_mcp_strict_arguments_and_errors_do_not_echo_secrets(mcp_environment):
    runtime, *_ = mcp_environment
    async with Client(create_server(runtime)) as client:
        for name, args in [
            ("PRIVATE_MARKER", {}),
            ("list_sites", {"password": "PRIVATE_MARKER"}),
            ("test_connection", {"domain": "PRIVATE_MARKER"}),
            ("deploy_site", {"domain": "example.com", "preview_hash": "a" * 64, "preview_token": "PRIVATE_MARKER", "confirm": True}),
            ("deploy_site", {"domain": "example.com", "preview_hash": "a" * 64, "preview_token": "x" * 43, "confirm": "true"}),
            ("register_site", {"domain": "new.example.com", "config": {"password": "PRIVATE_MARKER"}}),
        ]:
            result = await client.call_tool(name, args)
            data(result, "error")
            assert "PRIVATE_MARKER" not in result.model_dump_json()


@pytest.mark.anyio
async def test_mcp_registers_disabled_metadata_but_cannot_change_trust_or_publish(mcp_environment, tmp_path):
    runtime, _, _, site, _ = mcp_environment
    config = site.model_dump(mode="json") | {"publish_enabled": False, "ftps_write_preconditions_confirmed": False,
        "ssh_fingerprint": None, "ca_file": None, "local_root": str(tmp_path / "new-site")}
    async with Client(create_server(runtime)) as client:
        for change in ({"publish_enabled": True}, {"ftps_write_preconditions_confirmed": True},
                       {"ssh_fingerprint": "SHA256:" + "A" * 43}, {"ca_file": str(tmp_path / "untrusted.pem")}):
            data(await client.call_tool("register_site", {"domain": "new.example.com", "config": config | change}), "error")
        result = data(await client.call_tool("register_site", {"domain": "new.example.com", "config": config}))
        assert result["publish_enabled"] is False and result["credential_status"] == "pending"
        before = runtime.sites_file.read_bytes()
        data(await client.call_tool("register_site", {"domain": "new.example.com", "config": config}), "error")
        assert runtime.sites_file.read_bytes() == before
    assert not load_sites(runtime.sites_file)["new.example.com"].publish_enabled


@pytest.mark.anyio
async def test_mcp_conflict_never_issues_token(mcp_environment):
    runtime, server, local, *_ = mcp_environment
    (local / ".env").write_bytes(b"PRIVATE_MARKER")
    async with Client(create_server(runtime)) as client:
        result = await client.call_tool("preview_deploy", {"domain": "example.com"})
        assert "preview_token" not in data(result, "conflict")
        assert "PRIVATE_MARKER" not in result.model_dump_json()
    assert not runtime.state.exists()
    assert (server.storage / "site/index.html").read_bytes() == b"old remote content"


@pytest.mark.anyio
async def test_mcp_changed_preview_is_consumed_and_cannot_be_reused(mcp_environment):
    runtime, server, local, *_ = mcp_environment
    async with Client(create_server(runtime)) as client:
        preview = data(await client.call_tool("preview_deploy", {"domain": "example.com"}))
        args = {"domain": "example.com", "preview_hash": preview["preview_hash"], "preview_token": preview["preview_token"], "confirm": True}
        (local / "added.txt").write_bytes(b"new file since approval")
        data(await client.call_tool("deploy_site", args), "conflict")
        data(await client.call_tool("deploy_site", args), "error")
    assert (server.storage / "site/index.html").read_bytes() == b"old remote content"


@pytest.mark.anyio
async def test_mcp_deploy_requires_confirmation_and_opt_in_before_connection(mcp_environment, monkeypatch):
    import mcp_locaweb_sftp.operations as module
    runtime, *_ = mcp_environment
    async with Client(create_server(runtime)) as client:
        preview = data(await client.call_tool("preview_deploy", {"domain": "example.com"}))
        def forbidden(*args, **kwargs):
            pytest.fail("No connection without all preconditions")
        monkeypatch.setattr(module, "open_site", forbidden)
        args = {"domain": "example.com", "preview_hash": preview["preview_hash"], "preview_token": preview["preview_token"]}
        data(await client.call_tool("deploy_site", args), "error")
        data(await client.call_tool("deploy_site", args | {"confirm": True, "preview_token": "x" * 43}), "error")
        runtime.settings_file.write_text("publish_enabled: false\n", encoding="utf-8")
        data(await client.call_tool("deploy_site", args | {"confirm": True}), "error")


@pytest.mark.anyio
async def test_mcp_partial_failure_preserves_journal_and_does_not_echo_exception(mcp_environment, monkeypatch):
    from mcp_locaweb_sftp.backends.sftp import SFTPBackend
    from mcp_locaweb_sftp.backends.ftps import FTPSBackend
    runtime, server, _, site, _ = mcp_environment
    def interrupted(*args):
        (server.storage / "site/index.html").write_bytes(b"partial")
        raise OSError("PRIVATE_MARKER")
    monkeypatch.setattr(SFTPBackend if site.protocol == "sftp" else FTPSBackend, "_write_existing", interrupted)
    async with Client(create_server(runtime)) as client:
        preview = data(await client.call_tool("preview_deploy", {"domain": "example.com"}))
        result = await client.call_tool("deploy_site", {"domain": "example.com", "preview_hash": preview["preview_hash"],
            "preview_token": preview["preview_token"], "confirm": True})
        output = data(result, "partial")
        assert "PRIVATE_MARKER" not in result.model_dump_json()
        assert output["active"] == "index.html"
        assert json.loads(Path(output["run_directory"], "deploy-result.json").read_text())["status"] == "partial"


@pytest.mark.anyio
async def test_token_expires_during_backup_before_any_upload(mcp_environment, monkeypatch):
    import mcp_locaweb_sftp.core.deploy as module
    from mcp_locaweb_sftp.core.tokens import TokenError
    runtime, server, *_ = mcp_environment
    original = module.backup_files
    def slow_backup(*args):
        files = original(*args)
        def expired(self):
            raise TokenError()
        monkeypatch.setattr("mcp_locaweb_sftp.core.tokens.PreviewLease.require_fresh", expired)
        return files
    monkeypatch.setattr(module, "backup_files", slow_backup)
    async with Client(create_server(runtime)) as client:
        preview = data(await client.call_tool("preview_deploy", {"domain": "example.com"}))
        result = await client.call_tool("deploy_site", {"domain": "example.com", "preview_hash": preview["preview_hash"],
            "preview_token": preview["preview_token"], "confirm": True})
        output = data(result, "error")
        assert output["backup_complete"] is True and output["remote_mutation_started"] is False
    assert (server.storage / "site/index.html").read_bytes() == b"old remote content"
