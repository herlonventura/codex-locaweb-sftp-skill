"""Build on the target OS; never cross-compile or include private configuration."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    backend = {"win32": "Windows", "darwin": "macOS", "linux": "SecretService"}[sys.platform]
    for name, entry in (("vhe-deploy", "cli_entry.py"), ("vhe-deploy-mcp", "mcp_entry.py")):
        subprocess.run([
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir", "--name", name,
            "--specpath", str(ROOT / "build"), "--workpath", str(ROOT / "build" / "pyinstaller"),
            "--distpath", str(ROOT / "dist" / "vhe-bin"), "--paths", str(ROOT / "src"),
            "--collect-submodules", "mcp.server", "--collect-submodules", "mcp.shared",
            "--hidden-import", "anyio._backends._asyncio", "--hidden-import", f"keyring.backends.{backend}",
            str(ROOT / "packaging" / entry),
        ], check=True, cwd=ROOT,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)


if __name__ == "__main__":
    main()
