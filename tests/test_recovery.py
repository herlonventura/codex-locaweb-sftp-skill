from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

from click.testing import CliRunner
import pytest
import yaml

from vhe_deploy.cli import cli
from vhe_deploy.config import load_sites
from vhe_deploy.core.deploy import OperationError
from vhe_deploy.core.tokens import TokenError, TokenStore
from vhe_deploy.credentials import CredentialError, CredentialKey
from vhe_deploy.operations import Runtime


def approve(runtime):
    result = runtime.analyze("example.com", issue_token=True)
    assert result["status"] == "success", result
    return {key: result["data"][key] for key in ("domain", "preview_hash", "preview_token")} | {"confirm": True}


def test_process_death_preserves_originals_journal_consumption_and_stale_lock(site_runtime):
    runtime, server, _, _, credentials = site_runtime
    approved = approve(runtime)
    env = {name: value for name, value in os.environ.items() if name in
           ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA")}
    env.update(credentials, PYTHONPATH=str(Path("src").resolve()), PYTHONIOENCODING="utf-8")
    result = subprocess.run([sys.executable, str(Path(__file__).with_name("crash_worker.py")),
        str(runtime.sites_file), str(runtime.settings_file), str(runtime.state), approved["preview_hash"], approved["preview_token"]],
        env=env, capture_output=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 73, result.stderr.decode(errors="replace")
    assert not result.stdout
    journals = list(runtime.state.rglob("deploy-result.json"))
    assert len(journals) == 1
    journal = json.loads(journals[0].read_text())
    assert journal["status"] == "partial" and journal["data"]["active"] == "index.html"
    assert journal["data"]["uploaded"] == [] and journal["data"]["backup_complete"]
    assert (journals[0].parent / "backup/files/index.html").read_bytes() == b"old remote content"
    assert (server.storage / "site/index.html").read_bytes() == b"partial-before-process-death"
    assert (server.storage / "site/keep.txt").read_bytes() == b"preserve"
    with pytest.raises(TokenError):
        TokenStore(runtime.state).validate("example.com", approved["preview_hash"], approved["preview_token"])
    locks = list((runtime.state / "locks").iterdir())
    assert len(locks) == 1
    with pytest.raises(OperationError, match="trava"):
        runtime.backup_site("example.com")
    assert list((runtime.state / "locks").iterdir()) == locks
    # Simulate explicit operator review after the child is confirmed dead. Only
    # remove this exact empty fixture lock, never a user directory or a tree.
    assert locks[0].resolve().parent == (runtime.state / "locks").resolve()
    locks[0].rmdir()
    compared = runtime.analyze("example.com", issue_token=True)
    assert compared["status"] == "conflict" and "preview_token" not in compared["data"]
    assert compared["data"]["conflicts"] == ("index.html",)
    assert (journals[0].parent / "backup/files/index.html").read_bytes() == b"old remote content"


def test_second_domain_on_same_endpoint_cannot_deploy_during_backup(site_runtime, monkeypatch):
    import vhe_deploy.core.deploy as module
    runtime, server, _, site, _ = site_runtime
    sites = load_sites(runtime.sites_file)
    sites["other.example.com"] = site
    runtime.sites_file.write_text(yaml.safe_dump({k: v.model_dump(mode="json") for k, v in sites.items()}), encoding="utf-8")
    monkeypatch.setenv(CredentialKey.for_site("other.example.com", site).env_name, server.options["password"])
    first = approve(runtime)
    second_preview = runtime.analyze("other.example.com", issue_token=True)["data"]
    second = {key: second_preview[key] for key in ("domain", "preview_hash", "preview_token")} | {"confirm": True}
    entered, release = threading.Event(), threading.Event()
    original = module.backup_files
    def hold(*args):
        entered.set()
        if not release.wait(10):
            raise TimeoutError("fixture timeout")
        return original(*args)
    monkeypatch.setattr(module, "backup_files", hold)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(runtime.deploy_site, **first)
        try:
            assert entered.wait(10)
            with pytest.raises(OperationError, match="trava"):
                runtime.deploy_site(**second)
            assert (server.storage / "site/index.html").read_bytes() == b"old remote content"
            TokenStore(runtime.state).validate(second["domain"], second["preview_hash"], second["preview_token"])
        finally:
            release.set()
        result = pending.result(timeout=15)
    assert result["status"] == "success"
    stale = runtime.deploy_site(**second)
    assert stale["status"] == "conflict"
    assert (server.storage / "site/index.html").read_bytes() == b"new"
    assert len(list(runtime.state.rglob("deploy-result.json"))) == 1


def test_failure_in_last_original_backup_prevents_every_upload(site_runtime, monkeypatch):
    from vhe_deploy.backends.base import Backend
    runtime, server, local, *_ = site_runtime
    (server.storage / "site/z.html").write_bytes(b"second original")
    (local / "z.html").write_bytes(b"second update")
    os.utime(server.storage / "site/z.html", (1700000000, 1700000000))
    os.utime(local / "z.html", (1700000010, 1700000010))
    (local / "a-new.html").write_bytes(b"candidate sorted before originals")
    approved = approve(runtime)
    original = Backend.download
    def broken(self, path, destination):
        if path == "z.html" and hasattr(destination, "fileno"):
            backups = list(runtime.state.rglob("backup/files/index.html"))
            assert len(backups) == 1 and backups[0].read_bytes() == b"old remote content"
            raise OSError("PRIVATE_MARKER")
        return original(self, path, destination)
    monkeypatch.setattr(Backend, "download", broken)
    result = runtime.deploy_site(**approved)
    assert result["status"] == "error" and not result["data"]["remote_mutation_started"]
    assert result["data"]["uploaded"] == []
    assert not (server.storage / "site/a-new.html").exists()
    assert (server.storage / "site/index.html").read_bytes() == b"old remote content"
    assert (server.storage / "site/z.html").read_bytes() == b"second original"
    assert not list(Path(result["data"]["run_directory"]).rglob("backup-manifest.json"))
    assert "PRIVATE_MARKER" not in json.dumps(result)


def test_cli_reports_partial_with_exit_four_and_requires_new_preview(site_runtime, monkeypatch):
    from vhe_deploy.backends.ftps import FTPSBackend
    from vhe_deploy.backends.sftp import SFTPBackend
    runtime, server, _, site, _ = site_runtime
    approved = approve(runtime)
    backend = SFTPBackend if site.protocol == "sftp" else FTPSBackend
    def interrupted(*args):
        (server.storage / "site/index.html").write_bytes(b"partial")
        raise OSError("PRIVATE_MARKER")
    monkeypatch.setattr(backend, "_write_existing", interrupted)
    args = ["--sites", str(runtime.sites_file), "--settings", str(runtime.settings_file), "--state-dir", str(runtime.state),
        "enviar", "example.com", "--preview-hash", approved["preview_hash"], "--preview-token", approved["preview_token"], "--confirm"]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 4
    output = json.loads(result.stdout)
    assert output["status"] == "partial"
    assert "PRIVATE_MARKER" not in result.output and approved["preview_token"] not in result.output
    assert CliRunner().invoke(cli, args).exit_code == 1
    assert Path(output["data"]["run_directory"], "backup/files/index.html").read_bytes() == b"old remote content"


def test_registered_domains_do_not_share_credentials_implicitly(site_runtime, monkeypatch):
    import vhe_deploy.connection as connection
    runtime, _, _, site, _ = site_runtime
    with runtime.edit_registry() as sites:
        sites["other.example.com"] = site
    def must_not_authenticate(**kwargs):
        pytest.fail("Missing domain credential must be rejected before connecting")
    monkeypatch.setattr(connection, "SFTPBackend", must_not_authenticate)
    monkeypatch.setattr(connection, "FTPSBackend", must_not_authenticate)
    with pytest.raises(CredentialError):
        runtime.test_connection("other.example.com")


def test_simultaneous_registry_edit_does_not_lose_or_overwrite_entry(site_runtime):
    runtime, _, _, site, _ = site_runtime
    competing = Runtime(runtime.sites_file, runtime.settings_file, runtime.state)
    with runtime.edit_registry() as sites:
        with pytest.raises(OperationError, match="edição"):
            with competing.edit_registry():
                pytest.fail("Registry lock must reject another editor")
        sites["second.example.com"] = site
    assert set(load_sites(runtime.sites_file)) == {"example.com", "second.example.com"}
