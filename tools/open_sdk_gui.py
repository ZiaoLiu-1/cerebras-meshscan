#!/usr/bin/env python3
"""Open the installed SDK GUI backend for one private visualization run.

Run on this Mac while the existing cs_sdk Lima VM is running. The official
csviz backend stays in the SDK container and binds only to guest loopback;
Lima forwards it to Mac loopback. Ctrl-C stops the foreground backend.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.run_simulator import DEFAULT_PRIVATE, DEFAULT_SDK


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    directory = args.run_directory.expanduser().resolve()
    if not directory.is_dir() or DEFAULT_PRIVATE.resolve() not in directory.parents:
        parser.error("select an existing run inside the private-runs directory")
    if any(c.isspace() for c in str(directory)):
        parser.error("SDK wrapper requires a directory with no whitespace")
    if not 1024 <= args.port <= 65535:
        parser.error("port must be 1024..65535")
    # sdk_debug_shell visualize uses this same bundled csviz executable. Calling
    # it through cs_python lets us explicitly choose loopback and a stable port.
    code = "import os,sys; os.execvp('csviz', ['csviz', '--host', '127.0.0.1', '--port', sys.argv[1], '--noretry', '.'])"
    invocation = [str(DEFAULT_SDK / "cs_python"), "-c", code, str(args.port)]
    script = f"cd {shlex.quote(str(directory))} && exec {shlex.join(invocation)}"
    print(f"SDK GUI: http://127.0.0.1:{args.port}/sdk-gui", flush=True)
    print("Keep this terminal open. Ctrl-C stops this GUI; limactl stop cs_sdk stops the VM.", flush=True)
    try:
        return subprocess.call(["limactl", "shell", "cs_sdk", "--", "bash", "-lc", script])
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
