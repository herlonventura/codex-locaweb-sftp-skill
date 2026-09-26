import os

from ..backends.base import IntegrityError
from ..logs import save_record
from .compare import compare_inventories
from .local import exclusive_file, private_directory, read_local
from .preview import file_evidence


def backup_files(backend, entries, destination):
    """Download and verify every selected file before writing completion evidence."""
    compare_inventories(entries, entries)
    private_directory(destination)
    files_root = destination / "files"
    private_directory(files_root)
    for entry in entries:
        target = files_root / entry.path
        private_directory(target.parent)
        with exclusive_file(target) as output:
            digest = backend.download(entry.path, output)
            output.flush()
            os.fsync(output.fileno())
        if digest != entry.sha256 or read_local(files_root, entry.path).sha256 != entry.sha256:
            raise IntegrityError("Backup content differs from expected remote bytes.")
        if backend.file_state(entry.path) != entry:
            raise IntegrityError("Remote file changed during backup.")
    # This receipt describes only the selected bytes. The coordinator records
    # whether a full-site inventory remained stable in backup-result.json.
    save_record(destination / "backup-manifest.json", {"status": "complete", "scope": "selected_files", "files": file_evidence(entries)})
    return files_root
