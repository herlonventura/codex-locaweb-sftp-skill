import io
import os

import pytest

from mcp_locaweb_sftp.backends.base import IntegrityError
from mcp_locaweb_sftp.core.local import read_local, snapshot_local


def test_source_changed_after_preview_cannot_be_snapshotted_as_approved(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "page.html").write_bytes(b"approved")
    approved = read_local(source, "page.html")
    (source / "page.html").write_bytes(b"unapproved")
    with pytest.raises(IntegrityError, match="approved"):
        snapshot_local(source, (approved,), tmp_path / "snapshot")


def test_short_snapshot_write_is_rejected(tmp_path):
    (tmp_path / "page.html").write_bytes(b"whole source")
    class ShortWrite(io.BytesIO):
        def write(self, data):
            return super().write(data[:-1])
    with pytest.raises(OSError, match="Incomplete"):
        read_local(tmp_path, "page.html", ShortWrite())


def test_source_mutation_through_another_handle_during_read_is_detected(tmp_path):
    path = tmp_path / "page.html"
    path.write_bytes(b"first")
    class MutatingSink(io.BytesIO):
        changed = False
        def write(self, data):
            if not self.changed:
                self.changed = True
                with path.open("ab") as stream:
                    stream.write(b"more")
            return super().write(data)
    with pytest.raises(IntegrityError, match="changed"):
        read_local(tmp_path, "page.html", MutatingSink())


def test_snapshot_already_present_is_never_overwritten(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "page.html").write_bytes(b"new")
    (target / "page.html").write_bytes(b"existing receipt")
    with pytest.raises(FileExistsError):
        snapshot_local(source, (read_local(source, "page.html"),), target)
    assert (target / "page.html").read_bytes() == b"existing receipt"


def test_file_replaced_between_stat_and_open_is_rejected(tmp_path, monkeypatch):
    from pathlib import Path
    path = tmp_path / "page.html"
    path.write_bytes(b"old")
    replacement = tmp_path / "other.html"
    replacement.write_bytes(b"new version")
    original = Path.open
    replaced = False
    def switched(self, *args, **kwargs):
        nonlocal replaced
        if self == path and not replaced:
            replaced = True
            os.replace(replacement, path)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, "open", switched)
    with pytest.raises(IntegrityError, match="before opening"):
        read_local(tmp_path, "page.html")
