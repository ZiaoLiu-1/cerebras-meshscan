#!/usr/bin/env python3
"""Compile and verify one four-PE example, retaining private SDK GUI traces.

Uses the existing Lima SDK installation. No GUI, VM, download or hardware is
started automatically. All generated artifacts stay outside this repository.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host import compaction_contract, simulator_contract  # noqa: E402
from tools import compaction_oracle_bridge, oracle_bridge  # noqa: E402
from tools.run_simulator import (  # noqa: E402
    DEFAULT_PRIVATE, DEFAULT_SDK, compile_command, digest, execute,
    prepare_run_directory, verify_file_hashes, write_json,
)

SUPPRESSION_VARIABLES = (
    "CSL_SUPPRESS_SIMFAB_TRACE", "APPTAINERENV_CSL_SUPPRESS_SIMFAB_TRACE",
    "SINGULARITYENV_CSL_SUPPRESS_SIMFAB_TRACE",
)
HOST_FILES = (
    "host/visualization_run.py", "host/device_run.py", "host/simulator_contract.py",
    "host/compaction_device_run.py", "host/compaction_contract.py",
)
COMMON_FILES = (*HOST_FILES, "tools/run_visualization.py", "tools/run_simulator.py",
                "tools/oracle_bridge.py", "tools/compaction_oracle_bridge.py")


def guest_command(vm: str, sdk: Path, directory: Path, command: list[str],
                  timeout: int | None = None) -> list[str]:
    # A runtime constructor flag alone cannot defeat inherited container env.
    invocation = ["env"]
    for variable in SUPPRESSION_VARIABLES:
        invocation.extend(("-u", variable))
    invocation.append(f"PATH={sdk}:/usr/local/bin:/usr/bin:/bin")
    if timeout is not None:
        invocation.extend(("timeout", "--signal=TERM", "--kill-after=10s", f"{timeout}s"))
    invocation.extend(command)
    script = f"cd {shlex.quote(str(directory))} && exec {shlex.join(invocation)}"
    return ["limactl", "shell", vm, "--", "bash", "-lc", script]


def build_example(algorithm: str, binary: Path) -> tuple[dict, dict]:
    name = f"visualization-{algorithm}"
    if algorithm == "scan":
        values, chunk = [2, -1, 3, 4, 5, -2, 0, 1], 2
        contract = simulator_contract
        oracle = oracle_bridge.run_oracle(binary, values, 4)
        case = contract.pack_case(name, values, 4, chunk)
        schema = "meshscan-device-input-v1"
    else:
        values, chunk = [2, -1, 5, 5, -4, 9, 0, 7], 3
        contract = compaction_contract
        oracle = compaction_oracle_bridge.run_oracle(binary, values, 4, chunk, 5)
        case = contract.pack_case(name, values, 4, chunk, 5)
        schema = "meshcompact-device-input-v1"
    request = {"schema": schema, "width": 4, "chunk_size": chunk, "cases": [case]}
    contract.validate_request(request)
    return request, {name: oracle}


def headroom(path: Path) -> int:
    while not path.exists():
        path = path.parent
    free = shutil.disk_usage(path).free
    if free < 4 * 1024**3:
        raise ValueError("less than 4 GiB free; no next SDK command started")
    return free


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--algorithm", choices=("scan", "compaction"), default="scan")
    parser.add_argument("--binary", type=Path, help="existing native C++ oracle")
    parser.add_argument("--sdk", type=Path, default=DEFAULT_SDK)
    parser.add_argument("--vm", default="cs_sdk")
    parser.add_argument("--private-root", type=Path, default=DEFAULT_PRIVATE)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=900)
    args = parser.parse_args()
    report = {"schema": "meshscan-private-visualization-v1", "status": "preparing",
              "algorithm": args.algorithm, "created_utc": datetime.now(timezone.utc).isoformat(),
              "execution": "wse3-fabric-simulator", "hardware_executed": False,
              "disclosure": "private local SDK trace; not a performance benchmark",
              "device_launches_requested": 1, "device_launches_verified": 0,
              "trace_enabled": True, "simfab_numthreads": 4}
    run_dir = None
    try:
        if not 30 <= args.timeout_seconds <= 3600:
            raise ValueError("timeout must be 30..3600 seconds")
        compact = args.algorithm == "compaction"
        stem = "meshcompact" if compact else "meshscan"
        binary = (args.binary or ROOT / f"build/{stem}-reference").expanduser().resolve()
        sdk = args.sdk.expanduser().resolve()
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise ValueError(f"build the C++ oracle first: {binary}")
        if not args.prepare_only and (not (sdk / "cslc").is_file() or not shutil.which("limactl")):
            raise ValueError("existing SDK and limactl required; nothing is installed")
        report["free_bytes_before"] = headroom(args.private_root.expanduser())
        run_dir = prepare_run_directory(args.private_root, args.run_dir)
        print(f"PRIVATE_RUN {run_dir}", flush=True)
        report["run_directory"] = str(run_dir)
        device_folder = "compaction" if compact else "device"
        device_files = (f"src/{device_folder}/layout.csl", f"src/{device_folder}/pe.csl", *HOST_FILES)
        source_files = (*COMMON_FILES, *device_files[:2],
                        "src/compaction.cpp" if compact else "src/reference.cpp",
                        "include/meshscan/compaction.hpp" if compact else "include/meshscan/reference.hpp",
                        f"app/{stem}_reference.cpp")
        report["source_sha256"] = {name: digest(ROOT / name) for name in source_files}
        report["oracle_binary_sha256"] = digest(binary)
        report["sdk"] = {"path": str(sdk), "expected_release": "2.10.1",
                         "expected_build": "202606181328-8faf87a26e"}
        sif_files = list(sdk.glob("*.sif"))
        if not args.prepare_only and len(sif_files) != 1:
            raise ValueError("SDK must contain exactly one identifiable SIF")
        if len(sif_files) == 1:
            report["sdk"]["sif_sha256"] = digest(sif_files[0])
        snapshot = run_dir / "source"
        for name in source_files:
            destination = snapshot / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, destination)
        verify_file_hashes(snapshot, report["source_sha256"])
        directory = run_dir / "sdk-run"
        directory.mkdir(mode=0o700)
        for name in device_files:
            shutil.copy2(snapshot / name, directory / Path(name).name)
        staged = {Path(name).name: report["source_sha256"][name] for name in device_files}
        report["staged_source_sha256"] = staged
        verify_file_hashes(directory, staged)
        request, oracles = build_example(args.algorithm, binary)
        input_path, oracle_path = directory / "input.json", run_dir / "oracle.json"
        write_json(input_path, request)
        write_json(oracle_path, oracles)  # Outside the SDK's bound current directory.
        report["input_sha256"], report["oracle_sha256"] = digest(input_path), digest(oracle_path)
        report["layout"] = {"width": 4, "chunk_size": request["chunk_size"],
                            "fabric_dimensions": [13, 3], "fabric_offsets": [4, 1]}
        gui = ["python3", "-B", str(ROOT / "tools/open_sdk_gui.py"), str(directory),
               "--port", "8001" if compact else "8000"]
        report["gui_command"] = shlex.join(gui)
        if not args.prepare_only:
            headroom(run_dir)
            print(f"COMPILE {args.algorithm}: four PEs", flush=True)
            report["compile"] = execute(guest_command(args.vm, sdk, directory,
                compile_command(4, request["chunk_size"]), args.timeout_seconds),
                directory / "compile.log", args.timeout_seconds)
            report["compile_metadata_sha256"] = digest(directory / "out/out.json")
            headroom(run_dir)
            print(f"SIMULATE {args.algorithm}: one launch with traces", flush=True)
            report["runtime"] = execute(guest_command(args.vm, sdk, directory,
                ["cs_python", "visualization_run.py", "--algorithm", args.algorithm,
                 "--name", "out", "--input-json", "input.json", "--output-json", "device.json"],
                args.timeout_seconds), directory / "runtime.log", args.timeout_seconds)
            response = json.loads((directory / "device.json").read_text(encoding="utf-8"))
            contract = compaction_contract if compact else simulator_contract
            report["checks"] = contract.validate_response(request, response, oracles)
            report["device_launches_verified"] = len(report["checks"])
            traces = [path for folder in directory.rglob("simfab_traces") if folder.is_dir()
                      for path in folder.rglob("*") if path.is_file() and path.stat().st_size]
            if not traces:
                raise ValueError("device verified but no nonempty simfab_traces were produced")
            report["trace_file_count"] = len(traces)
            report["trace_bytes"] = sum(path.stat().st_size for path in traces)
            report["artifact_sha256"] = {
                str(path.relative_to(directory)): digest(path)
                for path in sorted(directory.rglob("*")) if path.is_file()}
        verify_file_hashes(snapshot, report["source_sha256"])
        verify_file_hashes(directory, staged)
        if digest(input_path) != report["input_sha256"] or digest(oracle_path) != report["oracle_sha256"]:
            raise ValueError("input/oracle record changed during execution")
        if digest(binary) != report["oracle_binary_sha256"]:
            raise ValueError("C++ oracle changed during execution")
        report["status"] = "prepared-not-executed" if args.prepare_only else "passed"
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(run_dir / "validation.json", report)
        print(f"{report['status'].upper()} {run_dir / 'validation.json'}", flush=True)
        if not args.prepare_only:
            print(f"GUI_COMMAND {report['gui_command']}", flush=True)
        return 0
    except (OSError, ValueError, KeyError, TypeError, oracle_bridge.OracleError,
            subprocess.TimeoutExpired) as error:
        report["status"], report["error"] = "failed", str(error)
        if run_dir is not None:
            write_json(run_dir / "failure.json", report)
        print(f"FAILED: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
