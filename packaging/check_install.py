"""Verify wheel/pipx or native binaries without importing the checkout in children."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def run(args, **kwargs):
    subprocess.run([str(a) for a in args], check=True, timeout=300,
                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--frozen", action="store_true")
    options = parser.parse_args()
    suffix = ".exe" if os.name == "nt" else ""
    env = os.environ.copy() | {"PYTHONPATH": "", "PYTHONIOENCODING": "utf-8"}
    if options.frozen:
        bins = ROOT / "dist" / "vhe-bin"
        cli = bins / "vhe-deploy" / ("vhe-deploy" + suffix)
        mcp = bins / "vhe-deploy-mcp" / ("vhe-deploy-mcp" + suffix)
    else:
        wheels = list((ROOT / "dist").glob("vhe_deploy-*.whl"))
        assert len(wheels) == 1
        venv = ROOT / "build" / "installed-vhe"
        run([sys.executable, "-m", "venv", venv])
        bins = venv / ("Scripts" if os.name == "nt" else "bin")
        python = bins / ("python" + suffix)
        run([python, "-m", "pip", "install", "--force-reinstall", wheels[0]])
        run([python, "-m", "pip", "check"])
        cli = bins / ("vhe-deploy" + suffix)
        mcp = bins / ("vhe-deploy-mcp" + suffix)
        # pipx uses only an isolated build directory, never the user's installation.
        pipx_bin = ROOT / "build" / "vhe-pipx-bin"
        pipx_env = env | {"PIPX_HOME": str(ROOT / "build" / "vhe-pipx-home"), "PIPX_BIN_DIR": str(pipx_bin),
                          "PIPX_MAN_DIR": str(ROOT / "build" / "vhe-pipx-man")}
        run([sys.executable, "-m", "pipx", "install", "--force", "--python", sys.executable, wheels[0]], env=pipx_env)
        with tempfile.TemporaryDirectory() as temporary:
            run([pipx_bin / ("vhe-deploy" + suffix), "--help"], env=pipx_env, cwd=temporary)
        run([sys.executable, ROOT / "packaging" / "smoke.py", pipx_bin / ("vhe-deploy-mcp" + suffix)], env=pipx_env)
    with tempfile.TemporaryDirectory() as temporary:
        run([cli, "--help"], env=env, cwd=temporary)
    run([sys.executable, ROOT / "packaging" / "smoke.py", mcp], env=env)
    env |= {"MCP_TEST_DIST_CLI": str(cli), "MCP_TEST_DIST_MCP": str(mcp)}
    run([sys.executable, "-m", "pytest", "-q", "tests/test_distribution.py"], cwd=ROOT, env=env)


if __name__ == "__main__":
    main()
