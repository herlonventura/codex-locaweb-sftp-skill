# VHE Deploy — SFTP/FTPS deployment CLI & MCP server

[Português](README.md) · [Downloads](https://github.com/herlonventura/vhe-deploy-sftp-ftps-mcp/releases) · [MIT license](LICENSE)

**Update your website files with an AI assistant: review the preview, authorize the transfer, and keep a backup of replaced files.**

[![Illustrative VHE Deploy workflow: preview, backup and authorized deployment](docs/media/workflow.gif)](docs/demo.md#english)

[Get started](#quick-start) · [Watch the 42-second overview](docs/demo.md#english) · [Join the early testers](https://github.com/herlonventura/vhe-deploy-sftp-ftps-mcp/discussions/1)

*Illustrative graphics with fictional data, not a recording of an application's interface. This is an alpha: start with a test site.*

Deploy website files over SFTP or explicit FTPS from a CLI or an AI assistant through Model Context Protocol (MCP). Preview changes, back up files before replacement, and verify transfers with SHA-256. Works with compatible hosting providers; it does not manage DNS, email accounts, hosting panels, or databases.

**Pre-release: 0.1.0a2.** Python 3.11+ source installation, or portable native bundles for Windows X64, Linux X64 and macOS ARM64. Native bundles are unsigned development builds; no MSI/setup wizard, PyPI package or hosted MCP service is provided.

## Quick start

You can give this repository URL to an AI assistant with access to your computer and ask it to read the documentation and help install VHE Deploy for a test site, without publishing any files yet. Enter passwords in the terminal, never in the chat. Integration depends on the application's capabilities and permissions.

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

## Selective file batches

Use repeated `--file` options on `compare`, `preview`, and `deploy`, or a `files` array on MCP `compare_site`, `preview_deploy`, and `deploy_site`. Paths are exact relative files under the registered root; repeat the same list at deployment. Empty lists, duplicates, directories, globs, and traversal are rejected.

```shell
vhe-deploy preview example.com --file index.html --file assets/site.css
vhe-deploy deploy example.com --file index.html --file assets/site.css --preview-hash HASH --preview-token TOKEN --confirm
```

The list is bound to the approved hash. Only selected files are read, compared, revalidated, backed up when replaced, and verified. Unselected files are neither audited nor modified. The five-minute, single-use receipt and all existing protections remain enforced. Omit the list for a full inventory; explicit backup remains full-site.
