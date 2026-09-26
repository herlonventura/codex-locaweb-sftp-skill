import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from vhe_deploy.core.retention import cleanup_successful_deploys, _remove_run


def completed(state, number, domain='example.com', **changes):
    run = state / 'runs' / domain / f'{number:032x}'
    (run / 'backup/files').mkdir(parents=True)
    (run / 'backup/files/index.html').write_text('old')
    (run / 'sources').mkdir()
    (run / 'sources/index.html').write_text('new')
    data = dict(domain=domain, run_directory=str(run), phase='complete', active=None,
                backup_complete=True, remote_mutation_started=True, planned=['index.html'],
                uploaded=['index.html'], completed_at_ns=number * 1_000_000_000)
    data.update(changes)
    (run / 'deploy-result.json').write_text(json.dumps({'status': 'success', 'data': data}))
    return run


def test_keep_three_per_domain_preserves_failed_full_unknown_and_legacy(tmp_path):
    runs = [completed(tmp_path, n) for n in range(1, 6)]
    # A legacy success uses its journal mtime; also eligible for cleanup.
    journal = runs[0] / 'deploy-result.json'
    record = json.loads(journal.read_text())
    del record['data']['completed_at_ns']
    journal.write_text(json.dumps(record))
    os.utime(journal, ns=(1_000_000_000, 1_000_000_000))
    other = completed(tmp_path, 1, domain='other.example.com')
    failed = completed(tmp_path, 6)
    (failed / 'deploy-result.json').write_text(json.dumps({'status': 'partial'}))
    full = completed(tmp_path, 7)
    (full / 'backup-result.json').write_text(json.dumps({'status': 'success'}))
    unknown = completed(tmp_path, 8)
    (unknown / 'personal.txt').write_text('preserve')
    result = cleanup_successful_deploys(tmp_path, 'example.com', runs[-1])
    assert set(result['removed_runs']) == {run.name for run in runs[:2]}
    assert result['skipped'] == 0
    assert not any(run.exists() for run in runs[:2])
    assert all(run.exists() for run in [*runs[2:], other, failed, full, unknown])


@pytest.mark.parametrize('content', ['invalid json', '[]', '{"status":"error"}',
                                    '{"status":"success","data":[]}'])
def test_unrecognized_journal_is_preserved(tmp_path, content):
    runs = [completed(tmp_path, n) for n in range(1, 6)]
    (runs[0] / 'deploy-result.json').write_text(content)
    cleanup_successful_deploys(tmp_path, 'example.com', runs[-1])
    assert (runs[0] / 'backup/files/index.html').read_text() == 'old'
    assert not runs[1].exists()


@pytest.mark.parametrize('changes', [dict(domain='other.example.com'), dict(run_directory='/elsewhere'),
                                    dict(phase='upload'), dict(backup_complete=False),
                                    dict(uploaded=[]), dict(completed_at_ns='bad')])
def test_incomplete_or_mismatched_success_is_not_deleted(tmp_path, changes):
    old = completed(tmp_path, 1, **changes)
    rest = [completed(tmp_path, n) for n in range(2, 6)]
    cleanup_successful_deploys(tmp_path, 'example.com', rest[-1])
    assert old.exists()


def test_current_run_required_and_kept_when_clock_regresses(tmp_path):
    runs = [completed(tmp_path, n) for n in range(10, 14)]
    current = completed(tmp_path, 1)
    result = cleanup_successful_deploys(tmp_path, 'example.com', current)
    assert set(result['removed_runs']) == {run.name for run in runs[:2]}
    assert current.exists() and all(run.exists() for run in runs[2:])
    (current / 'deploy-result.json').write_text('{"status":"partial"}')
    with pytest.raises(ValueError):
        cleanup_successful_deploys(tmp_path, 'example.com', current)


def test_hardlink_preflight_preserves_whole_old_run(tmp_path):
    runs = [completed(tmp_path, n) for n in range(1, 5)]
    outside = tmp_path / 'outside.txt'
    outside.write_text('never delete')
    os.link(outside, runs[0] / 'sources/alias.txt')
    result = cleanup_successful_deploys(tmp_path, 'example.com', runs[-1])
    assert result['skipped'] == 1 and not result['removed_runs']
    assert outside.read_text() == 'never delete'
    assert (runs[0] / 'backup/files/index.html').read_text() == 'old'


def test_symlink_preflight_preserves_external_files(tmp_path):
    runs = [completed(tmp_path, n) for n in range(1, 5)]
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'keep.txt').write_text('keep')
    try:
        (runs[0] / 'sources/link').symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('Creating symlinks requires OS permission')
    result = cleanup_successful_deploys(tmp_path, 'example.com', runs[-1])
    assert result['skipped'] == 1
    assert (outside / 'keep.txt').read_text() == 'keep'
    assert (runs[0] / 'backup/files/index.html').exists()


def test_reparse_point_is_rejected_before_removing_any_file(tmp_path, monkeypatch):
    runs = [completed(tmp_path, n) for n in range(1, 5)]
    target = runs[0] / 'sources'
    original = Path.lstat
    def reparse(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400) if path == target else info
    monkeypatch.setattr(Path, 'lstat', reparse)
    result = cleanup_successful_deploys(tmp_path, 'example.com', runs[-1])
    assert result['skipped'] == 1
    assert (runs[0] / 'backup/files/index.html').exists()


def test_partial_local_cleanup_preserves_journal_and_can_retry(tmp_path, monkeypatch):
    runs = [completed(tmp_path, n) for n in range(1, 5)]
    original = Path.unlink
    def denied(path, *args, **kwargs):
        if path == runs[0] / 'sources/index.html':
            raise PermissionError('simulated')
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'unlink', denied)
        result = cleanup_successful_deploys(tmp_path, 'example.com', runs[-1])
        assert result['skipped'] == 1
        assert (runs[0] / 'deploy-result.json').exists()
    result = cleanup_successful_deploys(tmp_path, 'example.com', runs[-1])
    assert result['removed_runs'] == [runs[0].name]
    assert not runs[0].exists()


def test_outside_target_and_concurrent_cleanup_are_rejected(tmp_path):
    runs = [completed(tmp_path, n) for n in range(1, 5)]
    outside = completed(tmp_path, 1, domain='other.example.com')
    with pytest.raises(ValueError):
        _remove_run(runs[0].parent, outside, 'example.com', 1)
    lock = tmp_path / 'retention-locks/example.com'
    lock.mkdir(parents=True)
    with pytest.raises(FileExistsError):
        cleanup_successful_deploys(tmp_path, 'example.com', runs[-1])
    assert all(run.exists() for run in [*runs, outside])
    assert lock.exists()
