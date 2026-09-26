import json
from pathlib import Path
import sys
import threading

import anyio
from mcp import Client, StdioServerParameters
import pytest

from mcp_locaweb_sftp.config import load_sites
from mcp_locaweb_sftp.mcp_server import TOOL_DEFINITIONS, create_server


def data(result, status="success"):
    assert result.structured_content["status"] == status, result
    assert result.is_error == (status != "success")
    assert json.loads(result.content[0].text) == result.structured_content
    return result.structured_content["data"]


@pytest.mark.anyio
async def test_official_stdio_client_discovers_and_executes_verified_deploy(site_runtime):
    runtime, server, local, site, environment = site_runtime
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
async def test_mcp_strict_arguments_and_errors_do_not_echo_secrets(site_runtime):
    runtime, *_ = site_runtime
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
async def test_mcp_registers_disabled_metadata_but_cannot_change_trust_or_publish(site_runtime, tmp_path):
    runtime, _, _, site, _ = site_runtime
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
async def test_mcp_conflict_never_issues_token(site_runtime):
    runtime, server, local, *_ = site_runtime
    (local / ".env").write_bytes(b"PRIVATE_MARKER")
    async with Client(create_server(runtime)) as client:
        result = await client.call_tool("preview_deploy", {"domain": "example.com"})
        assert "preview_token" not in data(result, "conflict")
        assert "PRIVATE_MARKER" not in result.model_dump_json()
    assert not runtime.state.exists()
    assert (server.storage / "site/index.html").read_bytes() == b"old remote content"


@pytest.mark.anyio
async def test_mcp_changed_preview_is_consumed_and_cannot_be_reused(site_runtime):
    runtime, server, local, *_ = site_runtime
    async with Client(create_server(runtime)) as client:
        preview = data(await client.call_tool("preview_deploy", {"domain": "example.com"}))
        args = {"domain": "example.com", "preview_hash": preview["preview_hash"], "preview_token": preview["preview_token"], "confirm": True}
        (local / "added.txt").write_bytes(b"new file since approval")
        data(await client.call_tool("deploy_site", args), "conflict")
        data(await client.call_tool("deploy_site", args), "error")
    assert (server.storage / "site/index.html").read_bytes() == b"old remote content"


@pytest.mark.anyio
async def test_mcp_deploy_requires_confirmation_and_opt_in_before_connection(site_runtime, monkeypatch):
    import mcp_locaweb_sftp.operations as module
    runtime, *_ = site_runtime
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
async def test_mcp_partial_failure_preserves_journal_and_does_not_echo_exception(site_runtime, monkeypatch):
    from mcp_locaweb_sftp.backends.sftp import SFTPBackend
    from mcp_locaweb_sftp.backends.ftps import FTPSBackend
    runtime, server, _, site, _ = site_runtime
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
async def test_token_expires_during_backup_before_any_upload(site_runtime, monkeypatch):
    import mcp_locaweb_sftp.core.deploy as module
    from mcp_locaweb_sftp.core.tokens import TokenError
    runtime, server, *_ = site_runtime
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


@pytest.mark.anyio
async def test_mcp_cancel_does_not_release_lock_while_worker_can_still_write(site_runtime, monkeypatch):
    import mcp_locaweb_sftp.core.deploy as module
    import mcp_locaweb_sftp.operations as operations
    runtime, server, *_ = site_runtime
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original_backup, original_deploy = module.backup_files, operations.deploy
    def holding(*args):
        entered.set()
        if not release.wait(10):
            raise TimeoutError("fixture timeout")
        return original_backup(*args)
    def tracked(*args, **kwargs):
        try:
            return original_deploy(*args, **kwargs)
        finally:
            finished.set()
    monkeypatch.setattr(module, "backup_files", holding)
    monkeypatch.setattr(operations, "deploy", tracked)
    scopes = []
    async with Client(create_server(runtime)) as client:
        preview = data(await client.call_tool("preview_deploy", {"domain": "example.com"}))
        async def sending():
            with anyio.CancelScope() as scope:
                scopes.append(scope)
                await client.call_tool("deploy_site", {"domain": "example.com", "preview_hash": preview["preview_hash"],
                    "preview_token": preview["preview_token"], "confirm": True})
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(sending)
            try:
                assert await anyio.to_thread.run_sync(entered.wait, 10)
                scopes[0].cancel()
                await anyio.sleep(0)
                assert len(list((runtime.state / "locks").iterdir())) == 1
                assert not finished.is_set()
                assert (server.storage / "site/index.html").read_bytes() == b"old remote content"
            finally:
                release.set()
            assert await anyio.to_thread.run_sync(finished.wait, 10)
    journals = list(runtime.state.rglob("deploy-result.json"))
    assert len(journals) == 1
    record = json.loads(journals[0].read_text())
    assert record["status"] == "success" and record["data"]["uploaded"] == ["index.html"]
    assert not list((runtime.state / "locks").iterdir())


@pytest.mark.anyio
async def test_mcp_unavailable_keyring_never_falls_back_to_environment(site_runtime, monkeypatch):
    import mcp_locaweb_sftp.credentials.keyring_store as keyring
    from mcp_locaweb_sftp.credentials.env_store import EnvStore
    from mcp_locaweb_sftp.config import Site
    runtime, _, _, site, _ = site_runtime
    with runtime.edit_registry() as sites:
        sites["example.com"] = Site(**(site.model_dump() | {"credential_store": "keyring"}))
    def broken():
        raise RuntimeError("PRIVATE_MARKER")
    def no_fallback(*args):
        pytest.fail("No environment fallback is permitted")
    monkeypatch.setattr(keyring, "_native_backend", broken)
    monkeypatch.setattr(EnvStore, "get", no_fallback)
    async with Client(create_server(runtime)) as client:
        result = await client.call_tool("test_connection", {"domain": "example.com"})
        data(result, "error")
        assert "PRIVATE_MARKER" not in result.model_dump_json()
