#!/usr/bin/env python3
"""Prepare and verify a private MeshScan WSE-3 fabric-simulator experiment.

Run from macOS, after starting the existing cs_sdk Lima VM. The C++ oracle runs
locally; SDK compilation and SdkRuntime execute in Linux. Nothing is uploaded.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host.simulator_contract import (  # noqa: E402
    INT32_MAX, INT32_MIN, SEED, SUPPORTED_WIDTHS, ContractError,
    pack_case, suite_cases, validate_request, validate_response,
)
from tools.oracle_bridge import OracleError, run_oracle  # noqa: E402

DEFAULT_PRIVATE = Path.home() / ".local/share/cerebras-lab/private-runs"
DEFAULT_SDK = Path.home() / ".local/share/cerebras-lab/sdk/2.10.1/cs_sdk"
SOURCE_FILES = (
    "src/device/layout.csl", "src/device/pe.csl", "host/device_run.py",
    "host/simulator_contract.py", "tools/run_simulator.py", "tools/oracle_bridge.py",
    "src/reference.cpp", "include/meshscan/reference.hpp", "app/meshscan_reference.cpp",
)
DEVICE_SOURCE_FILES = ("src/device/layout.csl", "src/device/pe.csl",
                       "host/device_run.py", "host/simulator_contract.py")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        result = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def write_json(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def verify_file_hashes(directory: Path, expected: dict[str, str]) -> None:
    """Fail closed if an actual staged executable source is missing or changed."""
    for name, expected_digest in expected.items():
        if not (directory / name).is_file() or digest(directory / name) != expected_digest:
            raise ContractError(f"staged source changed: {directory / name}")


def build_request(binary: Path, width: int, chunk: int, random_count: int) -> tuple[dict, dict, list]:
    cases, oracles, fast_paths = [], {}, []
    for case in suite_cases(width, chunk, random_count):
        # This also rejects unsupported global or partition-local arithmetic
        # before an input ever reaches a wrapping int32 device instruction.
        oracle = run_oracle(binary, case["values"], width)
        if not case["values"]:
            if oracle["output"] != []:
                raise ContractError("C++ empty-input fast path was not empty")
            fast_paths.append({"name": case["name"], "length": 0,
                               "status": "passed", "execution": "host-fastpath-not-device"})
            continue
        packed = pack_case(case["name"], case["values"], width, chunk)
        cases.append(packed)
        oracles[case["name"]] = oracle
    request = {"schema": "meshscan-device-input-v1", "width": width,
               "chunk_size": chunk, "cases": cases}
    validate_request(request)
    return request, oracles, fast_paths


def verify_rejections(binary: Path) -> list[dict]:
    failures = [
        ("global-overflow", [INT32_MAX, 1], 1),
        ("global-underflow", [INT32_MIN, -1], 1),
        ("partition-local-overflow", [-INT32_MAX, 0, INT32_MAX, 1], 2),
    ]
    result = []
    for name, values, width in failures:
        try:
            run_oracle(binary, values, width)
        except OracleError as error:
            if "exceeds signed 32-bit range" not in str(error):
                raise ContractError(f"{name}: failed for an unexpected reason") from error
            result.append({"name": name, "values": values, "width": width,
                           "status": "rejected-before-device", "reason": str(error)})
        else:
            raise ContractError(f"{name}: C++ incorrectly accepted overflow")
    return result


def compile_command(width: int, chunk: int) -> list[str]:
    return ["cslc", "--arch=wse3", "./layout.csl", f"--fabric-dims={width + 9},3",
            "--fabric-offsets=4,1", f"--params=width:{width},chunk_size:{chunk}",
            "-o", "out", "--memcpy", "--channels", "1", "--warnings-as-errors", "--out-routes"]


def guest_command(vm: str, sdk: Path, directory: Path, command: list[str], timeout: int) -> list[str]:
    environment = [f"PATH={sdk}:/usr/local/bin:/usr/bin:/bin",
                   "APPTAINERENV_CSL_SUPPRESS_SIMFAB_TRACE=1",
                   "SINGULARITYENV_CSL_SUPPRESS_SIMFAB_TRACE=1"]
    invocation = ["env", *environment, "timeout", "--signal=TERM", "--kill-after=10s",
                  f"{timeout}s", *command]
    script = f"cd {shlex.quote(str(directory))} && exec {shlex.join(invocation)}"
    return ["limactl", "shell", vm, "--", "bash", "-lc", script]


def execute(command: list[str], log: Path, timeout: int) -> dict:
    with log.open("x", encoding="utf-8") as stream:
        stream.write(f"COMMAND {shlex.join(command)}\n")
        stream.flush()
        try:
            process = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT,
                                     text=True, timeout=timeout + 45, check=False)
        except subprocess.TimeoutExpired as error:
            raise ContractError(f"command timed out; see {log}") from error
    if process.returncode != 0:
        raise ContractError(f"command exited {process.returncode}; see {log}")
    return {"exit_code": process.returncode, "log": str(log), "sha256": digest(log)}


def prepare_run_directory(private_root: Path, requested: Path | None) -> Path:
    parent = private_root.expanduser().resolve()
    if any(character.isspace() for character in str(parent)):
        raise ContractError("private root must have no whitespace for the SDK wrapper")
    if parent == ROOT or ROOT in parent.parents:
        raise ContractError("SDK artifacts must remain outside the repository")
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if requested is None:
        return Path(tempfile.mkdtemp(prefix="meshscan-", dir=parent))
    target = requested.expanduser().resolve()
    if target.parent != parent or any(character.isspace() for character in str(target)):
        raise ContractError("run-dir must be a new direct child of private-root, with no whitespace")
    target.mkdir(mode=0o700, exist_ok=False)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "build/meshscan-reference")
    parser.add_argument("--widths", nargs="+", type=int, default=list(SUPPORTED_WIDTHS))
    parser.add_argument("--chunk-size", type=int, default=16)
    parser.add_argument("--random-cases", type=int, default=16)
    parser.add_argument("--sdk", type=Path, default=DEFAULT_SDK)
    parser.add_argument("--vm", default="cs_sdk")
    parser.add_argument("--private-root", type=Path, default=DEFAULT_PRIVATE)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=900)
    args = parser.parse_args()
    report = {"schema": "meshscan-private-validation-v1", "status": "preparing",
              "execution": "wse3-fabric-simulator", "hardware_executed": False,
              "created_utc": datetime.now(timezone.utc).isoformat(), "seed": SEED,
              "disclosure": "private local record; not a public performance benchmark", "layouts": []}
    run_dir = None
    try:
        if not args.widths or len(set(args.widths)) != len(args.widths) or any(width not in SUPPORTED_WIDTHS for width in args.widths):
            raise ContractError(f"widths must be unique members of {SUPPORTED_WIDTHS}")
        if not 30 <= args.timeout_seconds <= 3600:
            raise ContractError("timeout must be between 30 and 3600 seconds per SDK command")
        binary, sdk = args.binary.expanduser().resolve(), args.sdk.expanduser().resolve()
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise ContractError("build the C++ oracle before running this command")
        if not args.prepare_only and (not (sdk / "cslc").is_file() or shutil.which("limactl") is None):
            raise ContractError("existing SDK and limactl are required; no installation is performed")
        if shutil.disk_usage(args.private_root.parent).free < 4 * 1024**3:
            raise ContractError("less than 4 GiB of disk headroom; experiment not started")
        run_dir = prepare_run_directory(args.private_root, args.run_dir)
        print(f"PRIVATE_RUN {run_dir}", flush=True)
        report["run_directory"] = str(run_dir)
        report["source_sha256"] = {name: digest(ROOT / name) for name in SOURCE_FILES}
        report["oracle_binary_sha256"] = digest(binary)
        report["sdk"] = {"path": str(sdk), "expected_release": "2.10.1",
                         "expected_build": "202606181328-8faf87a26e"}
        sif_files = list(sdk.glob("*.sif"))
        if len(sif_files) == 1:
            report["sdk"]["sif_sha256"] = digest(sif_files[0])
        report["rejection_checks"] = verify_rejections(binary)
        snapshots = run_dir / "source"
        for name in SOURCE_FILES:
            destination = snapshots / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, destination)
            if digest(destination) != report["source_sha256"][name]:
                raise ContractError(f"source changed during snapshot: {name}")
        oracle_directory = run_dir / "oracles"
        oracle_directory.mkdir(mode=0o700)
        for width in args.widths:
            directory = run_dir / f"p{width}"
            directory.mkdir(mode=0o700)
            for name in DEVICE_SOURCE_FILES:
                shutil.copy2(snapshots / name, directory / Path(name).name)
            request, oracles, fast_paths = build_request(binary, width, args.chunk_size, args.random_cases)
            write_json(directory / "input.json", request)
            oracle_path = oracle_directory / f"p{width}.json"
            write_json(oracle_path, oracles)
            layout = {"width": width, "chunk_size": args.chunk_size,
                      "fabric_dimensions": [width + 9, 3], "fabric_offsets": [4, 1],
                      "host_fastpaths": fast_paths, "device_cases_requested": len(request["cases"]),
                      "status": "prepared",
                      "input_sha256": digest(directory / "input.json"),
                      "oracle_record": str(oracle_path.relative_to(run_dir)),
                      "oracle_sha256": digest(oracle_path)}
            layout["staged_source_sha256"] = {
                Path(name).name: report["source_sha256"][name] for name in DEVICE_SOURCE_FILES
            }
            verify_file_hashes(directory, layout["staged_source_sha256"])
            report["layouts"].append(layout)
            if args.prepare_only:
                continue
            print(f"COMPILE p{width}", flush=True)
            layout["compile"] = execute(guest_command(args.vm, sdk, directory,
                compile_command(width, args.chunk_size), args.timeout_seconds),
                directory / "compile.log", args.timeout_seconds)
            layout["compile_metadata_sha256"] = digest(directory / "out/out.json")
            print(f"SIMULATE p{width}: {len(request['cases'])} repeated launches", flush=True)
            layout["runtime"] = execute(guest_command(args.vm, sdk, directory,
                ["cs_python", "device_run.py", "--name", "out", "--input-json", "input.json",
                 "--output-json", "device.json"], args.timeout_seconds),
                directory / "runtime.log", args.timeout_seconds)
            verify_file_hashes(directory, layout["staged_source_sha256"])
            if digest(directory / "input.json") != layout["input_sha256"] or \
                    digest(oracle_path) != layout["oracle_sha256"]:
                raise ContractError("input/oracle record changed during execution")
            response = json.loads((directory / "device.json").read_text(encoding="utf-8"))
            layout["checks"] = validate_response(request, response, oracles)
            layout["device_output_sha256"] = digest(directory / "device.json")
            layout["status"] = "passed"
            print(f"VERIFIED p{width}: exact C++ output, carries, completion, and padding", flush=True)
        # Hashes identify the snapshot actually executed, not a possibly edited worktree.
        for name, expected in report["source_sha256"].items():
            if digest(snapshots / name) != expected:
                raise ContractError(f"snapshot changed during run: {name}")
        if digest(binary) != report["oracle_binary_sha256"]:
            raise ContractError("oracle binary changed during validation")
        report["status"] = "prepared-not-executed" if args.prepare_only else "passed"
        report["device_launches_verified"] = sum(len(layout.get("checks", [])) for layout in report["layouts"])
        report["host_fastpaths_verified"] = sum(len(layout["host_fastpaths"]) for layout in report["layouts"])
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(run_dir / "validation.json", report)
        print(f"{report['status'].upper()} {run_dir / 'validation.json'}", flush=True)
        return 0
    except (ContractError, OracleError, OSError, ValueError, KeyError) as error:
        report["status"] = "failed"
        report["error"] = str(error)
        if run_dir is not None:
            write_json(run_dir / "failure.json", report)
        print(f"FAILED: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
