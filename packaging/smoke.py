"""Check an installed/frozen MCP entrypoint outside the source tree, without credentials."""
import argparse
import tempfile

import anyio
from mcp import Client, StdioServerParameters


async def smoke(command, args):
    with tempfile.TemporaryDirectory(prefix="mcp-distribution-") as temporary:
        params = StdioServerParameters(command=command, args=args, cwd=temporary,
                                      env={"PYTHONPATH": "", "PYTHONIOENCODING": "utf-8"})
        async with Client(params, read_timeout_seconds=45) as client:
            assert client.server_info is not None and client.server_info.name == 'vhe-deploy'
            tools = {t.name: t for t in (await client.list_tools()).tools}
            assert set(tools) == {"list_sites", "test_connection", "compare_site", "preview_deploy",
                                  "backup_site", "deploy_site", "register_site"}
            assert "preview_token" in tools["deploy_site"].input_schema["required"]
            result = await client.call_tool("deploy_site", {"domain": "example.com"})
            assert result.is_error, "Incomplete deployment request must be rejected"
    print("Distribution MCP stdio: initialization, seven tools and invalid-deploy rejection passed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command")
    parser.add_argument("args", nargs=argparse.REMAINDER)
    options = parser.parse_args()
    anyio.run(smoke, options.command, options.args)
