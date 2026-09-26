# VHE Deploy — SFTP/FTPS deployment CLI & MCP server

[Português](README.md) · [Downloads](https://github.com/herlonventura/vhe-deploy-sftp-ftps-mcp/releases) · [MIT license](LICENSE)

Deploy website files over SFTP or explicit FTPS from a CLI or an AI assistant through Model Context Protocol (MCP). Preview changes, back up files before replacement, and verify transfers with SHA-256. Works with compatible hosting providers; it does not manage DNS, email accounts, hosting panels, or databases.

**Pre-release: 0.1.0a2.** Python 3.11+ source installation, or portable native bundles for Windows X64, Linux X64 and macOS ARM64. Native bundles are unsigned development builds; no MSI/setup wizard, PyPI package or hosted MCP service is provided.

## Quick start

```sh
git clone https://github.com/herlonventura/vhe-deploy-sftp-ftps-mcp.git
cd vhe-deploy-sftp-ftps-mcp
pipx install .
vhe-deploy setup
```

Install pipx first, or follow the [venv installation guide](docs/install.md). Setup asks for the exact domain, protocol, host, port, username, local folder, remote folder and credential provider. No FileZilla installation is required. Passwords are entered only in a hidden local terminal prompt; never send them to the assistant or put them in YAML.

For an existing registration, use `vhe-deploy credential example.com` to enroll a password. `test example.com` tests a credential already enrolled in VHE Deploy; it does not import passwords from another program. Confirm the SFTP host fingerprint independently before authenticating.

```sh
vhe-deploy list
vhe-deploy test example.com
vhe-deploy compare example.com
vhe-deploy preview example.com
```

Configure your MCP client to launch `vhe-deploy-mcp` with its absolute path. [Client examples](docs/mcp-clients.md): Codex, Claude Desktop, Cursor, VS Code, Continue and Zed. The transport is local stdio, not HTTP/SSE. Tool discovery in Codex App Server was validated; approval interfaces in those desktop clients are not fully validated.

## Deployment controls

- Publication is disabled until explicitly enabled for both the site and global settings.
- Every deployment requires a reviewed preview, its hash, a single-use token valid for five minutes, and explicit confirmation. Client-side human approval remains necessary.
- Automatic backup covers only remote files that will be replaced. The three latest successful deployments per domain are retained. Explicit full backups and failed/partial runs are preserved.
- There is no remote deletion or automatic rollback. Replacing an active file is not atomic at site level; inspect a partial run before retrying.
- A content difference with a local timestamp not more than two seconds newer than remote blocks the batch. Review both versions and clock settings; waiting alone cannot resolve a genuinely newer remote edit.
- Private configuration, keys, credentials, state and backups belong outside Git and outside the published folder.

[Full CLI reference](docs/cli.md) · [Distribution and validation](docs/distribution.md) · [Recovery](docs/recovery.md) · [Release guide](docs/releases.md)

Licensed under MIT, copyright 2026 Herlon Ventura. Third-party dependencies retain their own licenses.
