import json
import os
from pathlib import Path

import pytest

from mcp_locaweb_sftp.config import Settings, Site
from mcp_locaweb_sftp.core.deploy import OperationError, backup_site, deploy, operation_lock, state_directory
from mcp_locaweb_sftp.core.local import local_inventory
from mcp_locaweb_sftp.core.preview import make_preview


@pytest.fixture
def flow(backend, tmp_path):
    connection, server = backend
    local = tmp_path / "public_html"
    local.mkdir()
    options = server.options
    protocol = "sftp" if "fingerprint" in options else "ftps"
    site = Site(protocol=protocol, host=options["host"], port=options["port"], user=options["username"],
                local_root=local.as_posix(), remote_root="/site", publish_enabled=True,
                ftps_write_preconditions_confirmed=True, credential_store="env",
                ssh_fingerprint=options.get("fingerprint"),
                ca_file=Path(options["ca_file"]).as_posix() if "ca_file" in options else None)
    return connection, server, local, site, Settings(publish_enabled=True), tmp_path / "state"


def seed(flow, *, nested=True):
    _, server, local, *_ = flow
    remote = server.storage / "site"
    (remote / "index.html").write_bytes(b"previous longer version")
    (remote / "keep.txt").write_bytes(b"do not delete")
    (local / "index.html").write_bytes(b"new")
    os.utime(remote / "index.html", (1700000000, 1700000000))
    os.utime(local / "index.html", (1700000010, 1700000010))
    if nested:
        (local / "assets/sub").mkdir(parents=True)
        (local / "assets/sub/new.bin").write_bytes(bytes(range(256)) * 300)


def execute(flow, preview_hash=None):
    backend, _, _, site, settings, state = flow
    if preview_hash is None:
        preview_hash = make_preview("example.com", site, settings, backend).digest
    return deploy("example.com", site, settings, backend, state=state, preview_hash=preview_hash, confirm=True)


def test_end_to_end_backup_precedes_replacement_and_nothing_is_deleted(flow, monkeypatch):
    seed(flow)
    backend, server, local, _, _, state = flow
    write = backend._write_existing
    def check_backup_first(path, source):
        manifests = list(state.rglob("backup-manifest.json"))
        assert len(manifests) == 1
        assert json.loads(manifests[0].read_text()) ["status"] == "complete"
        assert (manifests[0].parent / "files/index.html").read_bytes() == b"previous longer version"
        write(path, source)
    monkeypatch.setattr(backend, "_write_existing", check_backup_first)
    result = execute(flow)
    assert result["status"] == "success", result
    assert (server.storage / "site/index.html").read_bytes() == b"new"
    assert (server.storage / "site/assets/sub/new.bin").read_bytes() == (local / "assets/sub/new.bin").read_bytes()
    assert (server.storage / "site/keep.txt").read_bytes() == b"do not delete"
    assert result["data"]["uploaded"] == ["assets/sub/new.bin", "index.html"]
    run = Path(result["data"]["run_directory"])
    assert json.loads((run / "deploy-result.json").read_text())["status"] == "success"
    assert result["data"]["directories_created"] == ["assets", "assets/sub"]


def test_publication_requires_every_opt_in(flow):
    backend, _, _, site, settings, state = flow
    variants = [(site, settings, False), (Site(**(site.model_dump() | {"publish_enabled": False})), settings, True),
                (site, Settings(), True)]
    for current_site, current_settings, confirm in variants:
        with pytest.raises(OperationError):
            deploy("example.com", current_site, current_settings, backend, state=state, preview_hash="a" * 64, confirm=confirm)
    assert not state.exists()


def test_ftps_requires_administrative_preconditions(flow):
    backend, _, _, site, settings, state = flow
    if site.protocol != "ftps":
        return
    site = Site(**(site.model_dump() | {"ftps_write_preconditions_confirmed": False}))
    with pytest.raises(OperationError, match="FTPS"):
        deploy("example.com", site, settings, backend, state=state, preview_hash="a" * 64, confirm=True)


def test_remote_newer_and_sensitive_files_block_entire_batch(flow):
    seed(flow)
    _, server, local, *_ = flow
    (local / ".env").write_bytes(b"test-only-sensitive")
    os.utime(server.storage / "site/index.html", (1700000020, 1700000020))
    result = execute(flow)
    assert result["status"] == "conflict"
    assert result["data"]["blocked"] == (".env",)
    assert result["data"]["conflicts"] == ("index.html",)
    assert not (server.storage / "site/assets").exists()
    assert (server.storage / "site/index.html").read_bytes() == b"previous longer version"


def test_per_site_blocking_is_applied(flow):
    seed(flow)
    backend, server, local, site, settings, state = flow
    site = Site(**(site.model_dump() | {"blocked_paths": ["*.bin"]}))
    result = execute((backend, server, local, site, settings, state))
    assert result["status"] == "conflict"
    assert result["data"]["blocked"] == ("assets/sub/new.bin",)


@pytest.mark.parametrize("change", ["local", "remote", "settings", "site"])
def test_changed_preview_is_rejected_before_upload(flow, change):
    seed(flow, nested=False)
    backend, server, local, site, settings, state = flow
    digest = make_preview("example.com", site, settings, backend).digest
    if change == "local":
        (local / "index.html").write_bytes(b"different local")
    elif change == "remote":
        (server.storage / "site/keep.txt").write_bytes(b"different remote")
    elif change == "settings":
        settings = Settings(publish_enabled=True, timeout_seconds=20)
    else:
        site = Site(**(site.model_dump() | {"blocked_paths": ["*.sql"]}))
    result = execute((backend, server, local, site, settings, state), digest)
    assert result["status"] == "conflict"
    assert not list(state.rglob("deploy-result.json"))


def test_backup_failure_prevents_all_uploads(flow, monkeypatch):
    import mcp_locaweb_sftp.core.backup as module
    seed(flow)
    backend, server, *_ = flow
    def disk_failure(*args, **kwargs):
        raise OSError("simulated backup disk error PRIVATE_MARKER")
    monkeypatch.setattr(module, "exclusive_file", disk_failure)
    result = execute(flow)
    assert result["status"] == "error"
    assert "PRIVATE_MARKER" not in json.dumps(result)
    assert not (server.storage / "site/assets").exists()
    assert (server.storage / "site/index.html").read_bytes() == b"previous longer version"


def test_corrupted_backup_is_rechecked_before_replacement(flow, monkeypatch):
    import mcp_locaweb_sftp.core.deploy as module
    seed(flow, nested=False)
    original = module.backup_files
    def corrupt(*args, **kwargs):
        files = original(*args, **kwargs)
        (files / "index.html").write_bytes(b"corrupt")
        return files
    monkeypatch.setattr(module, "backup_files", corrupt)
    result = execute(flow)
    assert result["status"] == "error"
    assert (flow[1].storage / "site/index.html").read_bytes() == b"previous longer version"


def test_change_after_backup_stops_before_first_remote_mutation(flow, monkeypatch):
    import mcp_locaweb_sftp.core.deploy as module
    seed(flow)
    original = module.backup_files
    def change(*args, **kwargs):
        files = original(*args, **kwargs)
        (flow[2] / "index.html").write_bytes(b"changed while preparing")
        return files
    monkeypatch.setattr(module, "backup_files", change)
    result = execute(flow)
    assert result["status"] == "error"
    assert not (flow[1].storage / "site/assets").exists()


def test_interrupted_second_upload_records_partial_without_rollback(flow, monkeypatch):
    seed(flow)
    backend, server, *_ = flow
    def interrupted(absolute, source):
        (server.storage / "site/index.html").write_bytes(b"partial")
        raise ConnectionError("fictitious server error PRIVATE_MARKER")
    monkeypatch.setattr(backend, "_write_existing", interrupted)
    result = execute(flow)
    assert result["status"] == "partial"
    assert result["data"]["uploaded"] == ["assets/sub/new.bin"]
    assert result["data"]["active"] == "index.html"
    run = Path(result["data"]["run_directory"])
    persisted = json.loads((run / "deploy-result.json").read_text())
    assert persisted["status"] == "partial"
    assert "PRIVATE_MARKER" not in json.dumps(persisted)
    assert (run / "backup/files/index.html").read_bytes() == b"previous longer version"
    assert (server.storage / "site/index.html").read_bytes() == b"partial"
    assert (server.storage / "site/keep.txt").exists()


def test_post_upload_corruption_is_not_reported_as_success(flow, monkeypatch):
    seed(flow, nested=False)
    backend, server, *_ = flow
    original = backend._write_existing
    def corrupt(path, source):
        original(path, source)
        (server.storage / "site/index.html").write_bytes(b"server corruption")
    monkeypatch.setattr(backend, "_write_existing", corrupt)
    result = execute(flow)
    assert result["status"] == "partial"
    assert result["data"]["uploaded"] == []


def test_preexisting_lock_blocks_and_is_not_removed(flow):
    backend, _, _, site, settings, state = flow
    state = state_directory(site, state)
    with operation_lock(state, site):
        locks = list((state / "locks").iterdir())
        with pytest.raises(OperationError, match="trava"):
            execute(flow)
        assert list((state / "locks").iterdir()) == locks
    assert list((state / "locks").iterdir()) == []


def test_state_cannot_be_inside_publication_tree(flow):
    _, _, local, site, *_ = flow
    with pytest.raises(OperationError):
        state_directory(site, local / "backups")
    assert not (local / "backups").exists()


def test_initial_journal_error_prevents_mutations(flow, monkeypatch):
    import mcp_locaweb_sftp.core.deploy as module
    seed(flow)
    def fail(*args):
        raise OSError("disk failure")
    monkeypatch.setattr(module, "save_record", fail)
    with pytest.raises(OSError):
        execute(flow)
    assert not (flow[1].storage / "site/assets").exists()


def test_full_backup_includes_remote_only_files_without_remote_changes(flow):
    seed(flow)
    backend, server, _, site, _, state = flow
    before = {p.name: p.read_bytes() for p in (server.storage / "site").iterdir()}
    result = backup_site("example.com", site, backend, state=state)
    assert result["status"] == "success"
    run = Path(result["data"]["run_directory"])
    assert (run / "backup/files/keep.txt").read_bytes() == b"do not delete"
    assert {p.name: p.read_bytes() for p in (server.storage / "site").iterdir()} == before


def test_full_backup_rejects_inventory_changed_after_download(flow, monkeypatch):
    import mcp_locaweb_sftp.core.deploy as module
    seed(flow)
    backend, server, _, site, _, state = flow
    original = module.backup_files
    def changed(*args):
        result = original(*args)
        (server.storage / "site/added.txt").write_bytes(b"external change")
        return result
    monkeypatch.setattr(module, "backup_files", changed)
    result = backup_site("example.com", site, backend, state=state)
    assert result["status"] == "error"
    run = Path(result["data"]["run_directory"])
    assert json.loads((run / "backup-result.json").read_text())["status"] == "error"
    assert json.loads((run / "backup/backup-manifest.json").read_text())["scope"] == "selected_files"


def test_journal_failure_after_upload_retains_write_ahead_evidence(flow, monkeypatch):
    import mcp_locaweb_sftp.core.deploy as module
    seed(flow, nested=False)
    original = module.save_record
    def fail_after_upload(path, record):
        if record["data"]["uploaded"]:
            raise OSError("disk full PRIVATE_MARKER")
        return original(path, record)
    monkeypatch.setattr(module, "save_record", fail_after_upload)
    result = execute(flow)
    assert result["status"] == "partial"
    run = Path(result["data"]["run_directory"])
    persisted = json.loads((run / "deploy-result.json").read_text())
    assert persisted["status"] == "partial" and persisted["data"]["active"] == "index.html"
    assert "PRIVATE_MARKER" not in json.dumps(result)
    assert (flow[1].storage / "site/index.html").read_bytes() == b"new"
    assert (run / "backup/files/index.html").read_bytes() == b"previous longer version"


def test_remote_change_just_before_replace_is_not_overwritten(flow, monkeypatch):
    seed(flow, nested=False)
    backend, server, *_ = flow
    original = backend.replace
    def changed(*args, **kwargs):
        (server.storage / "site/index.html").write_bytes(b"external new content")
        return original(*args, **kwargs)
    monkeypatch.setattr(backend, "replace", changed)
    result = execute(flow)
    assert result["status"] == "partial"  # intent was persisted; no claim of rollback
    assert result["data"]["uploaded"] == []
    assert (server.storage / "site/index.html").read_bytes() == b"external new content"


def test_no_changes_create_no_deployment_run(flow):
    result = execute(flow)
    assert result["status"] == "success"
    assert not list(flow[5].rglob("deploy-result.json"))


def test_local_hardlinks_are_rejected(tmp_path):
    (tmp_path / "original").write_bytes(b"original")
    os.link(tmp_path / "original", tmp_path / "alias")
    with pytest.raises(ValueError, match="hard-link"):
        local_inventory(tmp_path)


def test_local_reparse_points_are_rejected(tmp_path, monkeypatch):
    from types import SimpleNamespace
    original = Path.lstat
    path = tmp_path / "junction"
    path.mkdir()
    def fake(self, *args, **kwargs):
        if self == path:
            return SimpleNamespace(st_mode=0o040700, st_file_attributes=0x400)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, "lstat", fake)
    with pytest.raises(ValueError, match="reparse"):
        local_inventory(tmp_path)
