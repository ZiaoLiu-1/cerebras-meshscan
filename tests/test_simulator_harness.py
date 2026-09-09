#!/usr/bin/env python3
"""CPU-only tests of simulator transport; these are not simulator results."""

from __future__ import annotations

import argparse
import copy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from host.simulator_contract import (  # noqa: E402
    METADATA, SUPPORTED_WIDTHS, ContractError, pack_case, suite_cases,
    validate_request, validate_response,
)
from tools.oracle_bridge import run_oracle  # noqa: E402
from tools.run_simulator import (  # noqa: E402
    build_request, compile_command, guest_command, prepare_run_directory,
    digest, verify_file_hashes, verify_rejections,
)
from host.device_run import execute_runtime  # noqa: E402

BINARY = ROOT / "build/meshscan-reference"


class FakeArray(list):
    def tolist(self):
        return list(self)


class FakeArrays:
    int32 = "fake-int32"

    @staticmethod
    def asarray(values, dtype):
        return FakeArray(values)

    @staticmethod
    def empty(size, dtype):
        return FakeArray([0] * size)


class FakeRuntime:
    """Fault injection only; it does not simulate a kernel or compute a scan."""

    def __init__(self, fail_at=None, fail_stop=False):
        self.fail_at, self.fail_stop, self.calls = fail_at, fail_stop, []

    def record(self, name):
        self.calls.append(name)
        if self.fail_at == name or (name == "stop" and self.fail_stop):
            raise RuntimeError(f"injected-{name}")

    def get_id(self, name):
        self.record("get_id")
        return name

    def load(self):
        self.record("load")

    def run(self):
        self.record("run")

    def launch(self, name, **options):
        self.record("launch")

    def memcpy_h2d(self, *args, **options):
        self.record("h2d")

    def memcpy_d2h(self, *args, **options):
        self.record("d2h")

    def stop(self):
        self.record("stop")


def fixture_response(request: dict, oracles: dict) -> dict:
    """Shape known C++ results as synthetic transport data for mutation tests."""
    cases = []
    chunk = request["chunk_size"]
    for packed in request["cases"]:
        oracle = oracles[packed["name"]]
        actual = {"name": packed["name"], "values": packed["values"].copy(),
                  **{field: [] for field in METADATA}}
        for pe, trace in enumerate(oracle["pes"]):
            count = packed["valid_count"][pe]
            actual["values"][pe * chunk:pe * chunk + count] = trace["output"]
            actual["incoming_carry"].append(trace["incoming_carry"])
            actual["local_total"].append(trace["local_total"])
            actual["outgoing_carry"].append(trace["incoming_carry"] + trace["local_total"])
            actual["completed"].append(1)
        cases.append(actual)
    return {"schema": "meshscan-device-output-v1", "execution": "wse3-fabric-simulator",
            "width": request["width"], "chunk_size": chunk, "runtime_sessions": 1, "cases": cases}


class SimulatorHarnessTests(unittest.TestCase):
    def test_balanced_pack_and_poison_padding(self):
        packed = pack_case("example", [3, -1, 4, 2, 5], 3, 4)
        self.assertEqual(packed["valid_count"], [2, 2, 1])
        self.assertEqual(packed["values"], [3, -1, 123456789, 123456789,
            4, 2, 123456790, 123456790, 5, 123456791, 123456791, 123456791])

    def test_empty_pes_still_occupy_physical_slots(self):
        packed = pack_case("short", [-4], 8, 2)
        self.assertEqual(packed["valid_count"], [1, 0, 0, 0, 0, 0, 0, 0])
        self.assertEqual(len(packed["values"]), 16)

    def test_input_type_capacity_and_range_rejected(self):
        for values in ([True], [1.0], [2**31], [-(2**31) - 1], [1] * 9):
            with self.subTest(values=values), self.assertRaises(ContractError):
                pack_case("bad", values, 2, 4)

    def test_matrix_deterministic_and_cpp_accepted(self):
        for width in SUPPORTED_WIDTHS:
            with self.subTest(width=width):
                self.assertEqual(suite_cases(width, 16), suite_cases(width, 16))
                request, oracles, fastpaths = build_request(BINARY, width, 16, 16)
                self.assertEqual(len(fastpaths), 1)
                self.assertEqual(fastpaths[0]["execution"], "host-fastpath-not-device")
                self.assertTrue(any(case["length"] < width for case in request["cases"]) or width == 1)
                checks = validate_response(request, fixture_response(request, oracles), oracles)
                self.assertEqual(len(checks), len(request["cases"]))
                self.assertEqual(oracles["hand-worked"]["output"], oracles["hand-worked-repeat"]["output"])

    def test_arithmetic_rejections_are_predevice(self):
        checks = verify_rejections(BINARY)
        self.assertEqual(len(checks), 3)
        self.assertTrue(all(check["status"] == "rejected-before-device" for check in checks))

    def test_request_schema_rejects_bad_counts_and_empty_device_input(self):
        good = {"schema": "meshscan-device-input-v1", "width": 2, "chunk_size": 4,
                "cases": [pack_case("case", [1, 2, 3], 2, 4)]}
        for modification in ("imbalance", "boolean", "empty", "duplicate", "extra"):
            broken = copy.deepcopy(good)
            if modification == "imbalance":
                broken["cases"][0]["valid_count"] = [1, 2]
            elif modification == "boolean":
                broken["cases"][0]["valid_count"][0] = True
            elif modification == "empty":
                broken["cases"][0]["length"] = 0
            elif modification == "duplicate":
                broken["cases"].append(broken["cases"][0])
            else:
                broken["expected"] = [1, 3, 6]
            with self.subTest(modification=modification), self.assertRaises(ContractError):
                validate_request(broken)

    def test_output_metadata_provenance_and_padding_mutations_fail_closed(self):
        request = {"schema": "meshscan-device-input-v1", "width": 4, "chunk_size": 4,
                   "cases": [pack_case("single", [7], 4, 4)]}
        oracles = {"single": run_oracle(BINARY, [7], 4)}
        good = fixture_response(request, oracles)
        mutations = ["output", "padding", "type", "missing", "name", "count", "provenance",
                     "runtime_sessions", *METADATA]
        for mutation in mutations:
            broken = copy.deepcopy(good)
            case = broken["cases"][0]
            if mutation == "output":
                case["values"][0] += 1
            elif mutation == "padding":
                case["values"][4] = 0
            elif mutation == "type":
                case["values"][0] = 7.0
            elif mutation == "missing":
                del case["completed"]
            elif mutation == "name":
                case["name"] = "other"
            elif mutation == "count":
                broken["cases"] = []
            elif mutation == "provenance":
                broken["execution"] = "cpu-logical-pe"
            elif mutation == "runtime_sessions":
                broken[mutation] = True
            else:
                case[mutation][3] += 1
            with self.subTest(mutation=mutation), self.assertRaises(ContractError):
                validate_response(request, broken, oracles)

    def test_stale_oracle_cannot_validate_another_input(self):
        request = {"schema": "meshscan-device-input-v1", "width": 2, "chunk_size": 2,
                   "cases": [pack_case("case", [7], 2, 2)]}
        stale = {"case": run_oracle(BINARY, [8], 2)}
        # The readback agrees with the stale oracle, but not with this request.
        response = fixture_response(request, stale)
        with self.assertRaisesRegex(ContractError, "input identity mismatch"):
            validate_response(request, response, stale)

    def test_empty_oracle_cannot_skip_device_checks(self):
        request = {"schema": "meshscan-device-input-v1", "width": 2, "chunk_size": 2,
                   "cases": [pack_case("case", [7], 2, 2)]}
        response = {"schema": "meshscan-device-output-v1", "execution": "wse3-fabric-simulator",
                    "width": 2, "chunk_size": 2, "runtime_sessions": 1,
                    "cases": [{"name": "case", "values": [0, 0, 0, 0],
                               **{field: [0, 0] for field in METADATA}}]}
        with self.assertRaises(ContractError):
            validate_response(request, response, {"case": {"pes": [], "output": []}})

    def test_oracle_structure_must_match_balanced_input(self):
        request = {"schema": "meshscan-device-input-v1", "width": 2, "chunk_size": 2,
                   "cases": [pack_case("case", [1], 2, 2)]}
        good = {"case": run_oracle(BINARY, [1], 2)}
        response = fixture_response(request, good)
        for mutation in ("model", "input-type", "output-size", "pe-count", "pe-type",
                         "bounds", "local-prefix", "trace-output", "carry-chain", "local-total"):
            broken = copy.deepcopy(good)
            oracle = broken["case"]
            if mutation == "model":
                oracle["model"] = "other"
            elif mutation == "input-type":
                oracle["input"][0] = True
            elif mutation == "output-size":
                oracle["output"] = []
            elif mutation == "pe-count":
                oracle["pes"] = oracle["pes"][:1]
            elif mutation == "pe-type":
                oracle["pes"][0]["pe"] = False
            elif mutation == "bounds":
                oracle["pes"][0]["end"] = 0
            elif mutation == "local-prefix":
                oracle["pes"][0]["local_prefix"] = []
            elif mutation == "trace-output":
                oracle["pes"][0]["output"][0] = True
            elif mutation == "carry-chain":
                oracle["pes"][1]["incoming_carry"] = 0
            else:
                oracle["pes"][0]["local_total"] = 0
            with self.subTest(mutation=mutation), self.assertRaises(ContractError):
                validate_response(request, response, broken)

    def test_compile_and_guest_command_bound_to_simulator(self):
        command = compile_command(4, 16)
        self.assertIn("--fabric-dims=13,3", command)
        self.assertIn("--params=width:4,chunk_size:16", command)
        self.assertNotIn("--cmaddr", command)
        wrapped = guest_command("cs_sdk", Path("/private/sdk"), Path("/private/run"), command, 120)
        self.assertEqual(wrapped[:6], ["limactl", "shell", "cs_sdk", "--", "bash", "-lc"])
        self.assertIn("--kill-after=10s 120s", wrapped[-1])
        self.assertIn("APPTAINERENV_CSL_SUPPRESS_SIMFAB_TRACE=1", wrapped[-1])

    def test_private_run_directory_is_new_and_outside_repo(self):
        with tempfile.TemporaryDirectory(prefix="meshscan-contract-") as folder:
            private = Path(folder).resolve()
            target = private / "run"
            self.assertEqual(prepare_run_directory(private, target), target)
            with self.assertRaises(FileExistsError):
                prepare_run_directory(private, target)
            with self.assertRaises(ContractError):
                prepare_run_directory(private, private / "bad space")
            with self.assertRaises(ContractError):
                prepare_run_directory(private, private.parent / "escape")
        with self.assertRaises(ContractError):
            prepare_run_directory(ROOT / "build", None)

    def test_cli_help_does_not_require_sdk_import(self):
        result = subprocess.run([sys.executable, str(ROOT / "tools/run_simulator.py"), "--help"],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--prepare-only", result.stdout)

    def test_actual_staged_source_mutation_and_missing_file_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix="meshscan-digest-") as folder:
            directory = Path(folder)
            source = directory / "layout.csl"
            source.write_text("initial source\n", encoding="utf-8")
            expected = {source.name: digest(source)}
            verify_file_hashes(directory, expected)
            source.write_text("changed source\n", encoding="utf-8")
            with self.assertRaises(ContractError):
                verify_file_hashes(directory, expected)
            with self.assertRaises(ContractError):
                verify_file_hashes(directory, {"missing.csl": expected[source.name]})

    def test_runtime_reuses_one_session_and_stops_once(self):
        request = {"width": 2, "chunk_size": 4, "cases": [
            pack_case("one", [1, 2], 2, 4), pack_case("two", [-1], 2, 4)]}
        runner = FakeRuntime()
        with patch("builtins.print"):
            result = execute_runtime(runner, request, FakeArrays, {})
        self.assertEqual(len(result["cases"]), 2)
        self.assertEqual(runner.calls.count("load"), 1)
        self.assertEqual(runner.calls.count("run"), 1)
        self.assertEqual(runner.calls.count("launch"), 2)
        self.assertEqual(runner.calls.count("stop"), 1)
        self.assertEqual(runner.calls[-1], "stop")

    def test_runtime_failure_cleanup_preserves_primary_exception(self):
        request = {"width": 2, "chunk_size": 4,
                   "cases": [pack_case("one", [1, 2], 2, 4)]}
        for failure in ("get_id", "load", "run", "h2d", "launch", "d2h"):
            runner = FakeRuntime(fail_at=failure, fail_stop=True)
            with self.subTest(failure=failure), self.assertRaisesRegex(RuntimeError, f"injected-{failure}"):
                execute_runtime(runner, request, FakeArrays, {})
            self.assertEqual(runner.calls[-1], "stop")
            self.assertEqual(runner.calls.count("stop"), 1)

    def test_successful_computation_but_failed_stop_is_not_success(self):
        request = {"width": 2, "chunk_size": 4,
                   "cases": [pack_case("one", [1, 2], 2, 4)]}
        with patch("builtins.print"), self.assertRaisesRegex(RuntimeError, "injected-stop"):
            execute_runtime(FakeRuntime(fail_stop=True), request, FakeArrays, {})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, default=BINARY)
    args, remaining = parser.parse_known_args()
    BINARY = args.binary.resolve()
    unittest.main(argv=[sys.argv[0], *remaining])
