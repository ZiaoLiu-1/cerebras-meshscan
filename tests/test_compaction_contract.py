#!/usr/bin/env python3
"""CPU contract/mutation/lifecycle tests, never represented as device execution."""

from __future__ import annotations

import copy
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from host.compaction_contract import (  # noqa: E402
    INT32_MAX, INT32_MIN, METADATA, OUTPUT_POISON, ContractError,
    output_initializers, pack_case, suite_cases, validate_request, validate_response,
)
from host.compaction_device_run import execute_runtime, validate_compiled_params  # noqa: E402


def fixture() -> tuple[dict, dict, dict]:
    """Hand-authored trace: two equal selected values make index checks necessary."""
    values = [2, -1, 5, 5, -4, 9, 0, 7]
    request = {"schema": "meshcompact-device-input-v1", "width": 4, "chunk_size": 3,
               "cases": [pack_case("example", values, 4, 3, 5)]}
    oracle = {"schema": "meshcompact-oracle-v1", "width": 4, "chunk_size": 3,
              "threshold": 5, "input": values, "output": [5, 5, 9, 7],
              "indices": [2, 3, 5, 7], "selected_count": 4,
              "pes": [
                  {"pe": 0, "input_start": 0, "valid_count": 2, "local_count": 0,
                   "offset": 0, "prefix_count": 0, "output_count": 3,
                   "received_count": 3, "forwarded_count": 0,
                   "output_values": [5, 5, 9], "output_indices": [2, 3, 5]},
                  {"pe": 1, "input_start": 2, "valid_count": 2, "local_count": 2,
                   "offset": 0, "prefix_count": 2, "output_count": 1,
                   "received_count": 2, "forwarded_count": 3,
                   "output_values": [7], "output_indices": [7]},
                  {"pe": 2, "input_start": 4, "valid_count": 2, "local_count": 1,
                   "offset": 2, "prefix_count": 3, "output_count": 0,
                   "received_count": 1, "forwarded_count": 2,
                   "output_values": [], "output_indices": []},
                  {"pe": 3, "input_start": 6, "valid_count": 2, "local_count": 1,
                   "offset": 3, "prefix_count": 4, "output_count": 0,
                   "received_count": 0, "forwarded_count": 1,
                   "output_values": [], "output_indices": []},
              ]}
    output = {"name": "example", "values": request["cases"][0]["values"].copy(),
              "output_values": [5, 5, 9, 7, OUTPUT_POISON + 1, OUTPUT_POISON + 1,
                                OUTPUT_POISON + 2, OUTPUT_POISON + 2, OUTPUT_POISON + 2,
                                OUTPUT_POISON + 3, OUTPUT_POISON + 3, OUTPUT_POISON + 3],
              "output_indices": [2, 3, 5, 7, -1, -1, -1, -1, -1, -1, -1, -1],
              "local_count": [0, 2, 1, 1], "offset": [0, 0, 2, 3],
              "prefix_count": [0, 2, 3, 4], "output_count": [3, 1, 0, 0],
              "received_count": [3, 2, 1, 0], "forwarded_count": [0, 3, 2, 1],
              "completed": [1, 1, 1, 1], "protocol_error": [0, 0, 0, 0]}
    response = {"schema": "meshcompact-device-output-v1", "execution": "wse3-fabric-simulator",
                "width": 4, "chunk_size": 3, "runtime_sessions": 1, "cases": [output]}
    return request, response, {"example": oracle}


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
    """Lifecycle/copy recorder only: no algorithm and no simulator claim."""

    def __init__(self, fail_at=None, fail_stop=False):
        self.fail_at, self.fail_stop = fail_at, fail_stop
        self.calls, self.transfers, self.launches = [], [], []

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
        self.launches.append((name, options))

    def memcpy_h2d(self, symbol, values, x, y, width, height, per_pe, **options):
        self.record("h2d")
        self.transfers.append((symbol, list(values), width, height, per_pe))
        # Mutating this ephemeral send array must not mutate the request or the
        # next launch's output sentinel initialization.
        if values:
            values[0] = -123

    def memcpy_d2h(self, *args, **options):
        self.record("d2h")

    def stop(self):
        self.record("stop")


class CompactionContractTests(unittest.TestCase):
    def test_balanced_input_slots_starts_and_nonzero_padding(self):
        case = pack_case("short", [2, -3, 8, 4, 1], 3, 3, 4)
        self.assertEqual(case["valid_count"], [2, 2, 1])
        self.assertEqual(case["input_start"], [0, 2, 4])
        self.assertEqual(case["values"], [2, -3, 123456789, 8, 4, 123456790,
                                          1, 123456791, 123456791])
        self.assertEqual(case["threshold"], 4)

    def test_zero_input_and_empty_pes_have_real_transport_slots(self):
        for values, expected in (([], [0] * 8), ([7], [1] + [0] * 7)):
            request = {"schema": "meshcompact-device-input-v1", "width": 8, "chunk_size": 1,
                       "cases": [pack_case("empty", values, 8, 1)]}
            self.assertIs(validate_request(request), request)
            self.assertEqual(request["cases"][0]["valid_count"], expected)
            self.assertEqual(len(request["cases"][0]["values"]), 8)

    def test_invalid_packing_types_bounds_and_capacity_fail(self):
        for values in ([True], [1.0], [INT32_MAX + 1], [INT32_MIN - 1], [1] * 7, "1,2"):
            with self.subTest(values=values), self.assertRaises(ContractError):
                pack_case("bad", values, 2, 3)
        for keyword, value in (("width", True), ("width", 0), ("width", 9),
                               ("chunk_size", True), ("chunk_size", 0), ("chunk_size", 65),
                               ("threshold", True), ("threshold", 1.0), ("threshold", INT32_MAX + 1)):
            options = {"width": 2, "chunk_size": 3, "threshold": 0, keyword: value}
            with self.subTest(keyword=keyword, value=value), self.assertRaises(ContractError):
                pack_case("bad", [1], **options)

    def test_request_schema_rejects_wrong_counts_starts_poison_and_answers(self):
        original, _, _ = fixture()
        mutations = ("schema", "extra", "duplicate", "name", "length", "threshold",
                     "values", "valid_count", "input_start", "padding", "imbalanced", "shifted")
        for mutation in mutations:
            request = copy.deepcopy(original)
            case = request["cases"][0]
            if mutation == "schema":
                request["schema"] = "meshscan-device-input-v1"
            elif mutation == "extra":
                request["expected"] = [5, 5, 9, 7]
            elif mutation == "duplicate":
                request["cases"].append(copy.deepcopy(case))
            elif mutation == "name":
                case["name"] = ""
            elif mutation in ("length", "threshold"):
                case[mutation] = True
            elif mutation in ("values", "valid_count", "input_start"):
                case[mutation][0] = True
            elif mutation == "padding":
                case["values"][2] = 0
            elif mutation == "imbalanced":
                case["valid_count"] = [1, 3, 2, 2]
            else:
                case["input_start"][1] += 1
            with self.subTest(mutation=mutation), self.assertRaises(ContractError):
                validate_request(request)

    def test_suite_deterministic_and_fits_smallest_through_largest_capacity(self):
        for width in range(1, 9):
            for chunk in (1, 3, 16, 64):
                with self.subTest(width=width, chunk=chunk):
                    cases = suite_cases(width, chunk)
                    self.assertEqual(cases, suite_cases(width, chunk))
                    self.assertEqual(len({case["name"] for case in cases}), len(cases))
                    request = {"schema": "meshcompact-device-input-v1", "width": width,
                               "chunk_size": chunk, "cases": [pack_case(case["name"], case["values"],
                                    width, chunk, case["threshold"]) for case in cases]}
                    validate_request(request)
                    by_name = {case["name"]: case for case in cases}
                    self.assertEqual(by_name["hand-worked"]["values"], by_name["hand-worked-repeat"]["values"])
                    self.assertEqual(cases[-1]["values"], [])
                    self.assertTrue(all(len(case["values"]) <= width * chunk for case in cases))

    def test_known_stable_result_and_counter_witnesses_pass(self):
        request, response, oracles = fixture()
        checks = validate_response(request, response, oracles)
        self.assertEqual(checks[0]["output"], [5, 5, 9, 7])
        self.assertEqual(checks[0]["indices"], [2, 3, 5, 7])
        self.assertEqual(checks[0]["selected_count"], 4)
        self.assertEqual(checks[0]["status"], "passed")

    def test_response_output_indices_input_and_poison_mutations_fail(self):
        request, original, oracles = fixture()
        for mutation in ("value", "order", "duplicate-index", "input", "input-padding",
                         "output-padding", "index-padding", "bool", "float", "short"):
            response = copy.deepcopy(original)
            case = response["cases"][0]
            if mutation == "value":
                case["output_values"][0] += 1
            elif mutation == "order":
                case["output_indices"][0:2] = [3, 2]  # Equal values cannot expose this.
            elif mutation == "duplicate-index":
                case["output_indices"][1] = 2
            elif mutation == "input":
                case["values"][0] += 1
            elif mutation == "input-padding":
                case["values"][2] = 0
            elif mutation == "output-padding":
                case["output_values"][4] = 0
            elif mutation == "index-padding":
                case["output_indices"][4] = 0
            elif mutation == "bool":
                case["output_indices"][0] = True
            elif mutation == "float":
                case["output_values"][0] = 5.0
            else:
                case["values"].pop()
            with self.subTest(mutation=mutation), self.assertRaises(ContractError):
                validate_response(request, response, oracles)

    def test_every_counter_completion_and_error_mutation_fails(self):
        request, original, oracles = fixture()
        for field in METADATA:
            for value in (True, original["cases"][0][field][0] + 1):
                response = copy.deepcopy(original)
                response["cases"][0][field][0] = value
                with self.subTest(field=field, value=value), self.assertRaises(ContractError):
                    validate_response(request, response, oracles)

    def test_response_provenance_shape_order_and_oracle_identity_fail_closed(self):
        request, original, oracles = fixture()
        for mutation in ("schema", "execution", "width", "chunk_size", "runtime_sessions",
                         "count", "name", "extra", "missing"):
            response = copy.deepcopy(original)
            if mutation in ("schema", "execution"):
                response[mutation] = "cpu-only-fixture"
            elif mutation in ("width", "chunk_size", "runtime_sessions"):
                response[mutation] = True
            elif mutation == "count":
                response["cases"] = []
            elif mutation == "name":
                response["cases"][0]["name"] = "other"
            elif mutation == "extra":
                response["cases"][0]["unrecognized"] = 0
            else:
                del response["cases"][0]["completed"]
            with self.subTest(mutation=mutation), self.assertRaises(ContractError):
                validate_response(request, response, oracles)
        for field in ("schema", "input", "threshold", "pes"):
            invalid = copy.deepcopy(oracles)
            invalid["example"][field] = None
            with self.subTest(oracle_field=field), self.assertRaises(ContractError):
                validate_response(request, original, invalid)
        with self.assertRaises(ContractError):
            validate_response(request, original, {})

    def test_stale_previous_launch_output_is_detected(self):
        request, response, oracles = fixture()
        # Same input, stricter threshold: all previously selected values are now
        # stale, even if the previous transfer packet happened to look valid.
        request["cases"][0]["threshold"] = 10
        oracle = oracles["example"]
        oracle.update(threshold=10, output=[], indices=[], selected_count=0)
        for trace in oracle["pes"]:
            for field in METADATA[:-2]:
                trace[field] = 0
            trace["output_values"], trace["output_indices"] = [], []
        with self.assertRaises(ContractError):
            validate_response(request, response, oracles)

    def test_compiled_metadata_exact_strings_or_integers_only(self):
        validate_compiled_params({"params": {"width": "4", "chunk_size": "3"}}, 4, 3)
        validate_compiled_params({"params": {"width": 4, "chunk_size": 3}}, 4, 3)
        for value in (True, 4.0, "04", "4 ", "3", None):
            with self.subTest(value=value), self.assertRaises(ContractError):
                validate_compiled_params({"params": {"width": value, "chunk_size": 3}}, 4, 3)
        with self.assertRaises(ContractError):
            validate_compiled_params({}, 4, 3)

    def test_runtime_single_session_blocking_launches_empty_input_and_output_reset(self):
        request, _, _ = fixture()
        request["cases"].append(pack_case("empty-after-work", [], 4, 3, INT32_MIN))
        before = copy.deepcopy(request)
        runner = FakeRuntime()
        with patch("builtins.print"):
            result = execute_runtime(runner, request, FakeArrays, {})
        self.assertEqual(request, before)
        self.assertEqual(len(result["cases"]), 2)
        for operation in ("load", "run", "stop"):
            self.assertEqual(runner.calls.count(operation), 1)
        self.assertEqual(runner.calls.count("launch"), 2)
        self.assertEqual(runner.launches, [("compute", {"nonblock": False})] * 2)
        self.assertEqual(runner.calls[-1], "stop")
        expected_values, expected_indices = output_initializers(4, 3)
        sent = [transfer for transfer in runner.transfers if transfer[0] == "output_values"]
        self.assertEqual([item[1] for item in sent], [expected_values] * 2)
        self.assertTrue(all(item[2:] == (4, 1, 3) for item in sent))
        self.assertEqual([item[1] for item in runner.transfers if item[0] == "output_indices"],
                         [expected_indices] * 2)
        self.assertEqual([item[1] for item in runner.transfers if item[0] == "threshold"],
                         [[5] * 4, [INT32_MIN] * 4])

    def test_runtime_failure_cleanup_preserves_primary_error(self):
        request, _, _ = fixture()
        for failure in ("get_id", "load", "run", "h2d", "launch", "d2h"):
            runner = FakeRuntime(fail_at=failure, fail_stop=True)
            with self.subTest(failure=failure), self.assertRaisesRegex(RuntimeError, f"injected-{failure}"):
                execute_runtime(runner, request, FakeArrays, {})
            self.assertEqual(runner.calls[-1], "stop")
            self.assertEqual(runner.calls.count("stop"), 1)

    def test_stop_failure_after_complete_work_still_fails(self):
        request, _, _ = fixture()
        with patch("builtins.print"), self.assertRaisesRegex(RuntimeError, "injected-stop"):
            execute_runtime(FakeRuntime(fail_stop=True), request, FakeArrays, {})

    def test_help_does_not_import_numpy_or_sdk(self):
        result = subprocess.run([sys.executable, "-B", str(ROOT / "host/compaction_device_run.py"), "--help"],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--input-json", result.stdout)


if __name__ == "__main__":
    unittest.main()
