"""Run explicitly selected installed/frozen commands against loopback SFTP and FTPS."""
import json
import os
from pathlib import Path
import subprocess

from mcp import Client, StdioServerParameters
import pytest


@pytest.fixture
def distribution_commands():
    cli = os.environ.get("MCP_TEST_DIST_CLI")
    mcp = os.environ.get("MCP_TEST_DIST_MCP")
    if not cli or not mcp:
        pytest.skip("Distribution commands are tested separately after packaging")
    return str(Path(cli).resolve(strict=True)), str(Path(mcp).resolve(strict=True))


@pytest.mark.anyio
async def test_distribution_cli_preview_and_mcp_deploy(site_runtime, distribution_commands, tmp_path):
    runtime, server, local, site, environment = site_runtime
    cli, mcp = distribution_commands
    # A cwd outside the checkout and empty PYTHONPATH prevent accidental source imports.
    environment = os.environ.copy() | environment | {"PYTHONPATH": "", "PYTHONIOENCODING": "utf-8"}
    args = ["--sites", str(runtime.sites_file), "--settings", str(runtime.settings_file), "--state-dir", str(runtime.state)]
    result = subprocess.run([cli, *args, "preview", "example.com"], cwd=tmp_path, env=environment,
                            capture_output=True, text=True, timeout=45,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert result.returncode == 0, result.stderr + result.stdout
    preview = json.loads(result.stdout)["data"]
    params = StdioServerParameters(command=mcp, args=args, cwd=str(tmp_path), env=environment)
    async with Client(params, read_timeout_seconds=45) as client:
        result = await client.call_tool("deploy_site", {"domain": "example.com", "preview_hash": preview["preview_hash"],
                                      "preview_token": preview["preview_token"], "confirm": True})
        assert not result.is_error, result
        deployed = result.structured_content["data"]
        assert deployed["uploaded"] == ["index.html"]
        assert (server.storage / "site/index.html").read_bytes() == b"new"
        assert Path(deployed["run_directory"], "backup/files/index.html").read_bytes() == b"old remote content"
        reused = await client.call_tool("deploy_site", {"domain": "example.com", "preview_hash": preview["preview_hash"],
                                     "preview_token": preview["preview_token"], "confirm": True})
        assert reused.is_error
