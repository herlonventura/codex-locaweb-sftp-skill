"""Keep three successful deployments per domain; never clean full/failed backups."""

import json
from pathlib import Path
import re
import stat

from ..local_files import checked_path, read_limited
from .guards import normalize_domain
from .local import private_directory

KEEP_DEPLOYS = 3
_RUN_ID = re.compile(r"[a-f0-9]{32}")


def _regular(path):
    info = checked_path(path).stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("Retention requires regular files without hard links.")
    return info


def _completed(run, domain):
    """Return completion order, or None for a record we must preserve."""
    run = checked_path(run)
    if not run.is_dir() or not _RUN_ID.fullmatch(run.name):
        return None
    children = {child.name for child in run.iterdir()}
    if 'deploy-result.json' not in children or children - {'deploy-result.json', 'backup', 'sources'}:
        return None  # Includes backup-result.json and unrecognized user files.
    journal = run / 'deploy-result.json'
    info = _regular(journal)
    record = json.loads(read_limited(journal))
    if not isinstance(record, dict) or record.get('status') != 'success':
        return None
    data = record.get('data')
    if not isinstance(data, dict):
        return None
    if (data.get('domain') != domain or data.get('run_directory') != str(run)
            or data.get('phase') != 'complete' or data.get('active') is not None
            or data.get('backup_complete') is not True or data.get('remote_mutation_started') is not True
            or not isinstance(data.get('planned'), list) or not data['planned']
            or data.get('uploaded') != data['planned']):
        return None
    # Older versions have no completion field. Never rewrite their journals.
    completed = data.get('completed_at_ns', info.st_mtime_ns)
    if type(completed) is not int or completed <= 0:
        return None
    return completed


def _remove_run(root, run, domain, expected_order):
    """Preflight the entire tree, then remove exact paths; journal goes last."""
    root, run = checked_path(root), checked_path(run)
    if run.parent != root or run.resolve().parent != root.resolve() or not _RUN_ID.fullmatch(run.name):
        raise ValueError("Retention target is outside this domain's run directory.")
    if _completed(run, domain) != expected_order:
        raise ValueError("Retention candidate changed.")
    files, directories, pending = [], [], [run]
    while pending:
        directory = checked_path(pending.pop())
        directories.append(directory)
        for child in directory.iterdir():
            child = checked_path(child)
            if child.is_dir():
                pending.append(child)
            else:
                _regular(child)
                files.append(child)
    # Reject links, junctions, special files and hard links before deleting ANY file.
    journal = run / 'deploy-result.json'
    for path in files:
        if path == journal:
            continue
        _regular(path)
        checked_path(path).unlink()
    for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        if directory != run:
            checked_path(directory).rmdir()
    _regular(journal)
    checked_path(journal).unlink()
    checked_path(run).rmdir()


def cleanup_successful_deploys(state: Path, domain: str, current_run: Path):
    domain = normalize_domain(domain)
    root = checked_path(state / 'runs' / domain)
    current_run = checked_path(current_run)
    if current_run.parent != root or _completed(current_run, domain) is None:
        raise ValueError("Cleanup requires a persisted successful deployment in this domain.")
    # Also serialize cleanup if two configurations map this domain to different endpoints.
    lock = private_directory(state / 'retention-locks') / domain
    lock.mkdir(mode=0o700)
    try:
        candidates, skipped = [], 0
        for run in root.iterdir():
            try:
                order = _completed(run, domain)
                if order is not None:
                    candidates.append((order, run.name, run))
            except (OSError, ValueError, RecursionError):
                skipped += 1
        candidates.sort(reverse=True)
        # Always retain this just-completed run, even if the system clock moved backwards.
        others = [item for item in candidates if item[2] != current_run]
        removed = []
        for order, _, run in others[KEEP_DEPLOYS - 1:]:
            try:
                _remove_run(root, run, domain, order)
                removed.append(run.name)
            except (OSError, ValueError, RecursionError):
                skipped += 1
        return {'keep_successful_deploys': KEEP_DEPLOYS, 'removed_runs': removed, 'skipped': skipped}
    finally:
        checked_path(lock).rmdir()
