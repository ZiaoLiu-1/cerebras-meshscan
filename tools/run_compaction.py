#!/usr/bin/env python3
"""Run the private stable-compaction experiment in the existing SDK VM.

The Mac executes the C++ reference; only inputs are passed to the SDK runner.
Oracle records are kept outside the SDK-bound execution directory.
Both count-prefix and reverse-scatter phases execute in one device invocation.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host.compaction_contract import (  # noqa: E402
    ContractError, pack_case, suite_cases, validate_request, validate_response,
)
from tools.compaction_oracle_bridge import run_oracle  # noqa: E402
from tools.run_simulator import (  # noqa: E402
    DEFAULT_PRIVATE, DEFAULT_SDK, compile_command, digest, execute, guest_command,
    prepare_run_directory, verify_file_hashes, write_json,
)

DEVICE_FILES = ("src/compaction/layout.csl", "src/compaction/pe.csl",
                "host/compaction_contract.py", "host/compaction_device_run.py")
SOURCE_FILES = (*DEVICE_FILES, "src/compaction.cpp", "include/meshscan/compaction.hpp",
                "app/meshcompact_reference.cpp", "tools/compaction_oracle_bridge.py",
                "tools/run_compaction.py", "tools/run_simulator.py",
                "tools/oracle_bridge.py", "host/simulator_contract.py")


def build_request(binary: Path, width: int, chunk: int, random_count: int) -> tuple[dict, dict]:
    cases, oracles = [], {}
    for case in suite_cases(width, chunk, random_count):
        name, values, threshold = case["name"], case["values"], case["threshold"]
        oracles[name] = run_oracle(binary, values, width, chunk, threshold)
        cases.append(pack_case(name, values, width, chunk, threshold))
    request = {"schema": "meshcompact-device-input-v1", "width": width,
               "chunk_size": chunk, "cases": cases}
    validate_request(request)
    return request, oracles


def headroom(path: Path) -> int:
    available = shutil.disk_usage(path).free
    if available < 4 * 1024**3:
        raise ContractError("less than 4 GiB free; no next SDK command started")
    return available


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "build/meshcompact-reference")
    parser.add_argument("--widths", nargs="+", type=int, default=[1, 2, 3, 4, 8])
    parser.add_argument("--chunk-size", type=int, default=16)
    parser.add_argument("--random-cases", type=int, default=8)
    parser.add_argument("--sdk", type=Path, default=DEFAULT_SDK)
    parser.add_argument("--vm", default="cs_sdk")
    parser.add_argument("--private-root", type=Path, default=DEFAULT_PRIVATE)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=900)
    args = parser.parse_args()
    report = {"schema": "meshcompact-private-validation-v1", "status": "preparing",
              "execution": "wse3-fabric-simulator", "hardware_executed": False,
              "created_utc": datetime.now(timezone.utc).isoformat(),
              "disclosure": "private correctness record, not a performance benchmark",
              "layouts": []}
    run_dir = None
    try:
        if not args.widths or len(set(args.widths)) != len(args.widths) or any(
                type(width) is not int or not 1 <= width <= 8 for width in args.widths):
            raise ContractError("widths must be distinct integers in [1,8]")
        if not 1 <= args.chunk_size <= 64 or not 0 <= args.random_cases <= 128:
            raise ContractError("chunk-size must be 1..64; random-cases 0..128")
        if not 30 <= args.timeout_seconds <= 3600:
            raise ContractError("timeout must be 30..3600 seconds")
        binary, sdk = args.binary.expanduser().resolve(), args.sdk.expanduser().resolve()
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise ContractError("build the C++ compaction reference first")
        if not args.prepare_only and (not (sdk / "cslc").is_file() or not shutil.which("limactl")):
            raise ContractError("existing SDK and limactl required; nothing installed")
        headroom(args.private_root.expanduser().parent)
        run_dir = prepare_run_directory(args.private_root, args.run_dir)
        print(f"PRIVATE_RUN {run_dir}", flush=True)
        report["run_directory"] = str(run_dir)
        report["source_sha256"] = {name: digest(ROOT / name) for name in SOURCE_FILES}
        report["oracle_binary_sha256"] = digest(binary)
        report["sdk"] = {"path": str(sdk), "expected_release": "2.10.1",
                         "expected_build": "202606181328-8faf87a26e"}
        sif_files = list(sdk.glob("*.sif"))
        if not args.prepare_only and len(sif_files) != 1:
            raise ContractError("SDK must contain exactly one identifiable SIF")
        if len(sif_files) == 1:
            report["sdk"]["sif_sha256"] = digest(sif_files[0])
        snapshot = run_dir / "source"
        for name, expected in report["source_sha256"].items():
            destination = snapshot / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, destination)
            if digest(destination) != expected:
                raise ContractError(f"source changed while snapshotting: {name}")
        oracle_directory = run_dir / "oracles"
        oracle_directory.mkdir(mode=0o700)
        for width in args.widths:
            directory = run_dir / f"p{width}"
            directory.mkdir(mode=0o700)
            for name in DEVICE_FILES:
                shutil.copy2(snapshot / name, directory / Path(name).name)
            request, oracles = build_request(binary, width, args.chunk_size, args.random_cases)
            write_json(directory / "input.json", request)
            oracle_path = oracle_directory / f"p{width}.json"
            write_json(oracle_path, oracles)
            layout = {"width": width, "chunk_size": args.chunk_size,
                      "fabric_dimensions": [width + 9, 3], "fabric_offsets": [4, 1],
                      "device_cases_requested": len(request["cases"]),
                      "host_fastpaths": 0, "status": "prepared",
                      "staged_source_sha256": {Path(name).name: report["source_sha256"][name]
                                               for name in DEVICE_FILES},
                      "input_sha256": digest(directory / "input.json"),
                      "oracle_record": str(oracle_path.relative_to(run_dir)),
                      "oracle_sha256": digest(oracle_path)}
            report["layouts"].append(layout)
            verify_file_hashes(directory, layout["staged_source_sha256"])
            if args.prepare_only:
                continue
            layout["disk_free_before_compile"] = headroom(run_dir)
            print(f"COMPILE p{width}", flush=True)
            layout["compile"] = execute(guest_command(args.vm, sdk, directory,
                compile_command(width, args.chunk_size), args.timeout_seconds),
                directory / "compile.log", args.timeout_seconds)
            layout["compile_metadata_sha256"] = digest(directory / "out/out.json")
            layout["disk_free_before_runtime"] = headroom(run_dir)
            print(f"SIMULATE p{width}: {len(request['cases'])} launches, including empty input", flush=True)
            layout["runtime"] = execute(guest_command(args.vm, sdk, directory,
                ["cs_python", "compaction_device_run.py", "--name", "out",
                 "--input-json", "input.json", "--output-json", "device.json"], args.timeout_seconds),
                directory / "runtime.log", args.timeout_seconds)
            verify_file_hashes(directory, layout["staged_source_sha256"])
            if digest(directory / "input.json") != layout["input_sha256"] or \
                    digest(oracle_path) != layout["oracle_sha256"]:
                raise ContractError("input/oracle record changed during execution")
            response = json.loads((directory / "device.json").read_text(encoding="utf-8"))
            layout["checks"] = validate_response(request, response, oracles)
            layout["device_output_sha256"] = digest(directory / "device.json")
            layout["status"] = "passed"
            print(f"VERIFIED p{width}: stable values/indices, packet counts, padding, completion", flush=True)
        verify_file_hashes(snapshot, report["source_sha256"])
        # Prevent unnoticed source/binary edits from being called the verified version.
        verify_file_hashes(ROOT, report["source_sha256"])
        if digest(binary) != report["oracle_binary_sha256"]:
            raise ContractError("C++ executable changed during run")
        report["status"] = "prepared-not-executed" if args.prepare_only else "passed"
        report["device_launches_verified"] = sum(len(x.get("checks", [])) for x in report["layouts"])
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(run_dir / "validation.json", report)
        print(f"{report['status'].upper()} {run_dir / 'validation.json'}", flush=True)
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        report["status"], report["error"] = "failed", str(error)
        if run_dir is not None:
            write_json(run_dir / "failure.json", report)
        print(f"FAILED: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
