from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
import multiprocessing
from pathlib import Path
import sqlite3

import pytest

from mcp_locaweb_sftp.core.compare import compare_inventories
from mcp_locaweb_sftp.core.preview import Preview
from mcp_locaweb_sftp.core.tokens import TokenError, TokenStore


@pytest.fixture
def receipt(tmp_path):
    preview = Preview("example.com", "a" * 64, (), (), compare_inventories([], []))
    store = TokenStore(tmp_path / "state")
    return store, preview


def test_receipt_survives_new_store_but_is_single_use_and_not_stored_in_clear(receipt):
    store, preview = receipt
    issued = store.issue(preview)
    token = issued["preview_token"]
    assert issued["valid_for_seconds"] == 300
    assert all(token.encode() not in path.read_bytes() for path in store.directory.iterdir() if path.is_file())
    other = TokenStore(store.directory.parent)
    other.validate(preview.domain, preview.digest, token)
    other.consume(preview.domain, preview.digest, token).require_fresh()
    for operation in (store.validate, store.consume):
        with pytest.raises(TokenError):
            operation(preview.domain, preview.digest, token)


@pytest.mark.parametrize("token", [None, "", "abc", "../" * 15, "x" * 43, "A" * 44, 123])
def test_invalid_or_unknown_token_is_rejected(receipt, token):
    store, preview = receipt
    with pytest.raises(TokenError):
        store.consume(preview.domain, preview.digest, token)


@pytest.mark.parametrize("scope", ["domain", "hash"])
def test_wrong_scope_cannot_be_used_and_consumes_attempt(receipt, scope):
    store, preview = receipt
    token = store.issue(preview)["preview_token"]
    with pytest.raises(TokenError):
        store.consume("other.example.com" if scope == "domain" else preview.domain,
                      "b" * 64 if scope == "hash" else preview.digest, token)
    with pytest.raises(TokenError):
        store.consume(preview.domain, preview.digest, token)


@pytest.mark.parametrize("wall,monotonic,valid", [(299,299,True), (300,300,False), (301,301,False),
    (-1,1,False), (1,-1,False), (1,300,False), (300,1,False), (float("nan"),1,False)])
def test_expiry_boundary_and_clock_changes_fail_closed(receipt, monkeypatch, wall, monotonic, valid):
    import mcp_locaweb_sftp.core.tokens as module
    clock = [1000.0, 1000.0]
    monkeypatch.setattr(module.time, "time", lambda: clock[0])
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[1])
    store, preview = receipt
    token = store.issue(preview)["preview_token"]
    clock[:] = [1000 + wall, 1000 + monotonic]
    if valid:
        store.consume(preview.domain, preview.digest, token)
    else:
        with pytest.raises(TokenError):
            store.consume(preview.domain, preview.digest, token)


def test_blocked_preview_never_issues_token(receipt):
    store, preview = receipt
    blocked = replace(preview, comparison=replace(preview.comparison, blocked=(".env",)))
    with pytest.raises(TokenError):
        store.issue(blocked)
    assert not store.path.exists()


def test_receipts_are_pruned_after_expiry(receipt, monkeypatch):
    import mcp_locaweb_sftp.core.tokens as module
    store, preview = receipt
    old = store.issue(preview)
    monkeypatch.setattr(module.time, "time", lambda: old["expires_at"] + 1)
    new = store.issue(preview)
    with pytest.raises(TokenError):
        store.consume(preview.domain, preview.digest, old["preview_token"])
    store.consume(preview.domain, preview.digest, new["preview_token"])


def _consume_in_process(state, token):
    try:
        TokenStore(Path(state)).consume("example.com", "a" * 64, token)
        return True
    except TokenError:
        return False


def test_exactly_one_independent_process_consumes_receipt(receipt):
    store, preview = receipt
    token = store.issue(preview)["preview_token"]
    with ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context("spawn")) as pool:
        futures = [pool.submit(_consume_in_process, str(store.directory.parent), token) for _ in range(2)]
        assert sorted(future.result(timeout=30) for future in futures) == [False, True]


def test_receipt_disk_error_does_not_authorize_deploy(receipt, monkeypatch):
    store, preview = receipt
    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("database failure")
    monkeypatch.setattr(sqlite3, "connect", broken)
    with pytest.raises(sqlite3.OperationalError):
        store.issue(preview)
