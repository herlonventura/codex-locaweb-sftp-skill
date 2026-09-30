"""Conservative deployment coordinator: preview, snapshot, backup, revalidate, write."""

from contextlib import contextmanager, suppress
import hashlib
import json
from pathlib import PurePosixPath
import time
import uuid

from ..backends.base import IntegrityError, UnsafeRemotePath
from ..config.schema import native_path
from ..local_files import checked_path
from ..logs import response, save_record
from .backup import backup_files
from .checksum import normalize_sha256
from .local import private_directory, read_local, snapshot_local
from .preview import make_preview
from .retention import KEEP_DEPLOYS, cleanup_successful_deploys
from .tokens import TokenStore


class OperationError(ValueError):
    """Messages here are static and suitable for the CLI, never server diagnostics."""


def publication_guard(site, settings, confirm):
    if confirm is not True or not site.publish_enabled or not settings.publish_enabled:
        raise OperationError("Publicação exige --confirm e publish_enabled=true no site e nas configurações.")
    if site.protocol == "ftps" and not site.ftps_write_preconditions_confirmed:
        raise OperationError("FTPS exige confirmação administrativa de confinamento e ausência de escritores concorrentes.")


def state_directory(site, path):
    root, path = checked_path(native_path(site.local_root)), checked_path(path)
    if root == path or root in path.parents or path in root.parents:
        raise OperationError("A pasta de registros/backups deve ficar separada da árvore publicada.")
    return private_directory(path)


@contextmanager
def operation_lock(state, site):
    # Serialize all roots/users/domains on this endpoint in this local state dir.
    # This cannot lock unrelated clients or web apps on the remote server.
    identity = json.dumps([site.protocol, site.host, site.port]).encode("utf-8")
    path = private_directory(state / "locks") / hashlib.sha256(identity).hexdigest()
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        raise OperationError("Outra operação usa este servidor, ou existe trava residual a revisar.") from None
    try:
        yield
    finally:
        checked_path(path).rmdir()


def create_run(state, domain):
    from .guards import normalize_domain
    domain = normalize_domain(domain)
    run = private_directory(state / "runs" / domain) / uuid.uuid4().hex
    run.mkdir(mode=0o700)
    return run


def deploy(domain, site, settings, backend, *, state, preview_hash, preview_token, confirm=False, files=None):
    publication_guard(site, settings, confirm)
    approved = normalize_sha256(preview_hash)
    state = state_directory(site, state)
    with operation_lock(state, site):
        lease = TokenStore(state).consume(domain, approved, preview_token)
        current = make_preview(domain, site, settings, backend, files=files)
        if current.comparison.has_blockers or current.digest != approved:
            return response("conflict", current.summary(), ["Prévia mudou ou contém arquivos bloqueados/conflitos; nenhum envio realizado."])
        if not current.comparison.upload_paths:
            lease.require_fresh()
            return response("success", current.summary(), ["Nenhuma alteração para enviar."])
        run = create_run(state, current.domain)
        journal = run / "deploy-result.json"
        data = {"domain": current.domain, "preview_hash": approved, "run_directory": str(run),
                "scope": "files" if current.files is not None else "full", "files": current.files,
                "planned": list(current.comparison.upload_paths), "uploaded": [], "directories_created": [],
                "active": None, "phase": "preparing", "backup_complete": False, "remote_mutation_started": False}
        record = response("partial", data, ["Operação iniciada; conclusão ainda não registrada."])
        save_record(journal, record)  # A writable journal is mandatory BEFORE mutation.
        try:
            local = {entry.path: entry for entry in current.local}
            remote = {entry.path: entry for entry in current.remote}
            candidates = tuple(local[path] for path in current.comparison.upload_paths)
            snapshot_local(native_path(site.local_root), candidates, run / "sources")
            data["phase"] = "backup"
            save_record(journal, record)
            originals = tuple(remote[path] for path in current.comparison.modified)
            backups = backup_files(backend, originals, run / "backup")
            data["backup_complete"] = True
            data["phase"] = "revalidate"
            save_record(journal, record)
            if make_preview(domain, site, settings, backend, files=files).digest != approved:
                raise IntegrityError("Preview changed after backup.")
            lease.require_fresh()  # Slow comparison/backup must not extend authorization.
            for entry in candidates:
                # Check approved bytes and the backup from disk before any
                # mutation for this file; do not rely on an in-memory receipt.
                if read_local(run / "sources", entry.path).sha256 != entry.sha256:
                    raise IntegrityError("Source snapshot changed.")
                previous = remote.get(entry.path)
                if previous is not None and read_local(backups, entry.path).sha256 != previous.sha256:
                    raise IntegrityError("Verified backup changed.")
                parents = list(reversed(PurePosixPath(entry.path).parents))
                for parent in parents:
                    relative = str(parent)
                    if relative == ".":
                        continue
                    info = backend.stat_path(relative)
                    if info is None:
                        if not data["remote_mutation_started"]:
                            lease.require_fresh()
                        data.update(active=relative, phase="mkdir", remote_mutation_started=True)
                        save_record(journal, record)
                        backend.mkdir(relative)
                        data["directories_created"].append(relative)
                        save_record(journal, record)
                    elif info.kind != "dir":
                        raise UnsafeRemotePath("A required directory is not a directory.")
                if not data["remote_mutation_started"]:
                    lease.require_fresh()
                data.update(active=entry.path, phase="upload", remote_mutation_started=True)
                save_record(journal, record)  # Write-ahead evidence survives a forced termination.
                with checked_path(run / "sources" / entry.path).open("rb") as source:
                    if previous is None:
                        backend.upload(entry.path, source, expected_sha256=entry.sha256)
                    else:
                        backend.replace(entry.path, source, expected_sha256=entry.sha256, previous=previous)
                data["uploaded"].append(entry.path)
                data["active"] = None
                save_record(journal, record)
            data["phase"] = "complete"
            data["completed_at_ns"] = time.time_ns()
            record = response("success", data, ["Envio verificado por SHA-256; nenhuma exclusão remota."])
            save_record(journal, record)
        except (Exception, KeyboardInterrupt):
            status = "partial" if data["remote_mutation_started"] else "error"
            record = response(status, data, ["Operação interrompida. Consulte os registros privados; não houve rollback automático."])
            # If the disk failed, the last durable write-ahead record remains.
            with suppress(OSError, ValueError):
                save_record(journal, record)
        if record["status"] == "success":
            # The success journal must already be durable. Cleanup failure must
            # never relabel a verified upload as partial or ask clients to resend.
            try:
                data["retention"] = cleanup_successful_deploys(state, current.domain, run)
                if data["retention"]["skipped"]:
                    record["messages"].append("Limpeza local incompleta; algumas cópias foram preservadas para revisão.")
            except (Exception, KeyboardInterrupt):
                data["retention"] = {"keep_successful_deploys": KEEP_DEPLOYS, "status": "incomplete"}
                record["messages"].append("Envio concluído; limpeza local não concluída. Revise os registros antes de remover cópias.")
            try:
                save_record(journal, record)
            except (OSError, ValueError):
                record["messages"].append("Não foi possível registrar o resultado da limpeza; o sucesso do envio já havia sido salvo.")
        return record


def backup_site(domain, site, backend, *, state):
    from ..core.guards import normalize_domain
    domain = normalize_domain(domain)
    state = state_directory(site, state)
    with operation_lock(state, site):
        run = create_run(state, domain)
        record = response("partial", {"domain": domain, "run_directory": str(run)}, ["Backup iniciado."])
        journal = run / "backup-result.json"
        save_record(journal, record)
        try:
            entries = backend.inventory()
            backup_files(backend, entries, run / "backup")
            if backend.inventory() != entries:
                raise IntegrityError("Remote inventory changed during backup.")
            record = response("success", record["data"] | {"file_count": len(entries)}, ["Backup verificado."])
            save_record(journal, record)
        except (Exception, KeyboardInterrupt):
            record = response("error", record["data"], ["Backup incompleto; não utilizar como cópia verificada do site."])
            with suppress(OSError, ValueError):
                save_record(journal, record)
        return record
