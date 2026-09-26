"""Test-only child process: terminate after a real loopback partial write."""

import io
import os
from pathlib import Path
import sys

from vhe_deploy.backends.ftps import FTPSBackend
from vhe_deploy.backends.sftp import SFTPBackend
from vhe_deploy.operations import Runtime


def main():
    sites, settings, state, digest, token = sys.argv[1:]
    runtime = Runtime(Path(sites), Path(settings), Path(state))
    _, _, site, _ = runtime.site("example.com")
    # This crash harness must never operate against a real hosting account.
    if site.host != "127.0.0.1" or site.user != "test-user" or site.remote_root != "/site":
        raise SystemExit(2)
    backend = SFTPBackend if site.protocol == "sftp" else FTPSBackend
    original = backend._write_existing

    def crash(self, absolute, source):
        original(self, absolute, io.BytesIO(b"partial-before-process-death"))
        os._exit(73)  # deliberately bypass finally, lock release and final journal

    backend._write_existing = crash
    runtime.deploy_site("example.com", digest, token, True)
    raise SystemExit(3)  # reaching here means the failure was not injected


if __name__ == "__main__":
    main()
