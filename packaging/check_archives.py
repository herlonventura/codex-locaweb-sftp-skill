"""Allowlist source/wheel members: no local configuration or build leftovers."""
from pathlib import Path, PurePosixPath
import configparser
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def check():
    wheels = list((ROOT / "dist").glob("vhe_deploy-*.whl"))
    sources = list((ROOT / "dist").glob("vhe_deploy-*.tar.gz"))
    assert len(wheels) == len(sources) == 1, "Expected exactly one wheel and source archive"
    source_files = {p.relative_to(ROOT).as_posix() for p in (ROOT / "src").rglob("*.py")}
    with zipfile.ZipFile(wheels[0]) as archive:
        files = {n for n in archive.namelist() if not n.endswith("/")}
        entrypoints = configparser.ConfigParser()
        entrypoints.read_string(archive.read(next(n for n in files if n.endswith('/entry_points.txt'))).decode())
        assert set(entrypoints['console_scripts']) == {'vhe-deploy', 'vhe-deploy-mcp'}, 'Unexpected command names'
        expected = {n.removeprefix("src/") for n in source_files}
        assert expected <= files, "Missing Python source in wheel"
        for name in files - expected:
            parts = PurePosixPath(name).parts
            assert len(parts) == 2 and parts[0].endswith(".dist-info") and parts[1] in {
                "METADATA", "WHEEL", "RECORD", "entry_points.txt"}, f"Unexpected wheel member: {name}"
    with tarfile.open(sources[0]) as archive:
        files = set()
        for member in archive.getmembers():
            assert member.isfile() or member.isdir(), "Source archive contains link or special file"
            if member.isfile():
                files.add("/".join(PurePosixPath(member.name).parts[1:]))
        expected = source_files | {"pyproject.toml", "requirements.txt", "README.md", "PKG-INFO", ".gitignore"}
        assert files == expected, f"Unexpected or missing source members: {files ^ expected}"
    print("Wheel and source archive contain only allowed package files and metadata.")


if __name__ == "__main__":
    check()
