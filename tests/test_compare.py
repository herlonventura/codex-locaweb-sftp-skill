from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import builtins
import socket

import pytest

from mcp_locaweb_sftp.core.checksum import sha256_bytes
from mcp_locaweb_sftp.core.compare import FileState, compare_inventories
from mcp_locaweb_sftp.core.guards import require_registered_domain


STAMP = datetime(2026, 1, 1, tzinfo=timezone.utc)


def file(path, content=b"old", seconds=0):
    return FileState(path, sha256_bytes(content), STAMP + timedelta(seconds=seconds))


def test_comparison_classifies_content_without_planning_remote_deletion():
    remote = [file("equal"), file("changed"), file("remote-only")]
    local = [file("new"), file("changed", b"new", 10), file("equal", seconds=-20)]
    result = compare_inventories(local, remote)
    assert result.equal == ("equal",)
    assert result.modified == ("changed",)
    assert result.new_local == ("new",)
    assert result.remote_only == ("remote-only",)
    assert result.conflicts == result.blocked == ()
    assert result.upload_paths == ("changed", "new")
    assert "remote-only" not in result.upload_paths
    assert compare_inventories([], remote).upload_paths == ()


@pytest.mark.parametrize(("seconds", "conflict"), [
    (-10, True), (0, True), (1.999999, True), (2, True), (2.000001, False), (10, False),
])
def test_differing_file_requires_strictly_more_than_two_seconds(seconds, conflict):
    result = compare_inventories([file("index.html", b"new", seconds)], [file("index.html")])
    assert bool(result.conflicts) is conflict
    assert result.upload_paths == (() if conflict else ("index.html",))


def test_equal_hash_with_newer_remote_needs_no_overwrite_or_conflict():
    result = compare_inventories([file("index.html")], [file("index.html", seconds=999)])
    assert result.equal == ("index.html",)
    assert not result.has_blockers
    assert result.upload_paths == ()


def test_timezones_are_compared_as_instants():
    remote = FileState("index.html", "a" * 64, datetime(2026, 1, 1, 12, tzinfo=timezone.utc))
    local = FileState("index.html", "b" * 64, datetime(
        2026, 1, 1, 9, 0, 3, tzinfo=timezone(timedelta(hours=-3))
    ))
    assert compare_inventories([local], [remote]).upload_paths == ("index.html",)
    assert local.modified_at.hour == 12


@pytest.mark.parametrize("timestamp", [datetime(2026, 1, 1), "2026-01-01", None])
def test_unknown_timezone_or_date_cannot_enter_inventory(timestamp):
    with pytest.raises(ValueError):
        FileState("index.html", "a" * 64, timestamp)


def test_bad_hash_or_path_cannot_enter_inventory():
    with pytest.raises(ValueError):
        FileState("index.html", "", STAMP)
    with pytest.raises(ValueError):
        FileState("../index.html", "a" * 64, STAMP)


def test_metadata_is_immutable_and_uppercase_digest_is_normalized():
    record = FileState("index.html", "A" * 64, STAMP)
    assert record.sha256 == "a" * 64
    with pytest.raises(FrozenInstanceError):
        record.path = "../secret"


def test_a_conflict_prevents_even_unrelated_new_files_from_uploading():
    result = compare_inventories([file("new.css"), file("index.html", b"new")], [file("index.html")])
    assert result.new_local == ("new.css",)
    assert result.conflicts == ("index.html",)
    assert result.has_blockers
    assert result.upload_paths == ()


@pytest.mark.parametrize("scenario", ["new", "modified", "conflict"])
def test_a_sensitive_candidate_blocks_entire_batch(scenario):
    remote = [] if scenario == "new" else [file(".env")]
    seconds = 0 if scenario == "conflict" else 10
    local = [file("index.html"), file(".env", b"secret-example", seconds)]
    result = compare_inventories(local, remote)
    assert result.blocked == (".env",)
    assert result.has_blockers
    assert result.upload_paths == ()


def test_untouched_sensitive_files_do_not_block_unrelated_changes():
    local = [file(".env"), file("index.html")]
    remote = [file(".env"), file(".git/config")]
    result = compare_inventories(local, remote)
    assert result.blocked == ()
    assert result.upload_paths == ("index.html",)
    assert result.remote_only == (".git/config",)


def test_per_site_rule_blocks_new_file():
    result = compare_inventories([file("private/info.json")], [], extra_blocked_patterns=("private/",))
    assert result.blocked == ("private/info.json",)
    assert result.upload_paths == ()


@pytest.mark.parametrize("remote_side", [False, True])
def test_duplicate_inventory_entries_are_not_silently_overwritten(remote_side):
    records = [file("index.html"), file("index.html", b"different", 10)]
    with pytest.raises(ValueError, match="Duplicate"):
        compare_inventories([] if remote_side else records, records if remote_side else [])


@pytest.mark.parametrize(("left", "right"), [
    ("Index.html", "index.html"), ("café.txt", "cafe\u0301.txt"),
    ("assets", "assets/style.css"), ("Assets/a.css", "assets/b.css"),
    ("straße/a.css", "STRASSE/b.css"),
])
@pytest.mark.parametrize("across_sides", [False, True])
def test_ambiguous_names_and_file_directory_collisions_block_comparison(left, right, across_sides):
    local = [file(left)] if across_sides else [file(left), file(right)]
    remote = [file(right)] if across_sides else []
    with pytest.raises(ValueError):
        compare_inventories(local, remote)


def test_bad_inventory_data_and_invalid_rules_do_not_produce_a_plan():
    with pytest.raises(ValueError):
        compare_inventories([{"path": "index.html"}], [])
    with pytest.raises(ValueError):
        compare_inventories([], [], extra_blocked_patterns=("../bad",))


def test_result_is_deterministic_for_iterators_and_empty_inventories():
    records = [file("z.html"), file("a.html")]
    before = list(records)
    assert compare_inventories(iter(records), []).upload_paths == ("a.html", "z.html")
    assert compare_inventories(reversed(records), []) == compare_inventories(records, [])
    assert records == before
    assert compare_inventories([], []).upload_paths == ()


def test_core_can_run_with_file_and_network_access_disabled(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Pure core attempted I/O")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    assert require_registered_domain("example.com", {"example.com"}) == "example.com"
    assert compare_inventories([file("index.html")], []).upload_paths == ("index.html",)
