"""Install the pinned test-only age release, checking official asset digests."""
import hashlib
import io
import os
from pathlib import Path
import platform
import tarfile
import urllib.request
import zipfile

VERSION = "1.3.2"
DIGESTS = {
    "windows-amd64": "f48d8f8f9ebe903ab5027ed067652f2cc1db94bc206976430133b905dcd8e8c7",
    "windows-arm64": "fae351336c8d5f30f93cc3ccdec16d1ed7851d68cdd6656d41955cc31a447c9d",
    "linux-amd64": "cbe24006683f8eb669266162894b9a522a1af52f2665fbc63a4bb032ed26ac10",
    "linux-arm64": "6b8dc4333c53a5a57c9e5834e3a48f92605d7154014cd07269ff3327db5d37f4",
    "darwin-amd64": "1d1e4bc66e1427edad7739ae7616157de0e79db8b6d2a1497d7d9925fb06a539",
    "darwin-arm64": "e2020b073c44f692685a24d6abc378817eb81ffaaf49fd0531ef8565f767f2f5",
}


def main():
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = {"amd64": "amd64", "x86_64": "amd64", "arm64": "arm64", "aarch64": "arm64"}[machine]
    key = f"{system}-{arch}"
    windows = system == "windows"
    ext = "zip" if windows else "tar.gz"
    url = f"https://github.com/FiloSottile/age/releases/download/v{VERSION}/age-v{VERSION}-{key}.{ext}"
    with urllib.request.urlopen(url, timeout=60) as response:
        content = response.read()
    if hashlib.sha256(content).hexdigest() != DIGESTS[key]:
        raise RuntimeError("age release checksum mismatch")
    target = Path(__file__).resolve().parents[1] / "build" / "test-age"
    target.mkdir(parents=True, exist_ok=True)
    # Copy only the two expected regular files; never extract arbitrary archive paths.
    for stem in ("age", "age-keygen"):
        name = stem + (".exe" if windows else "")
        member = f"age/{name}"
        if windows:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                data = archive.read(member)
        else:
            with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
                info = archive.getmember(member)
                if not info.isfile():
                    raise RuntimeError("Unexpected age archive member")
                data = archive.extractfile(info).read()
        (target / name).write_bytes(data)
        (target / name).chmod(0o755)
    executable = target / ("age.exe" if windows else "age")
    if "GITHUB_ENV" in os.environ:
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as stream:
            stream.write(f"VHE_DEPLOY_TEST_AGE={executable}\n")
    print(executable)


if __name__ == "__main__":
    main()
