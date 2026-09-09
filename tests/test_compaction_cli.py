from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import unittest
from pathlib import Path


class CompactionCliTests(unittest.TestCase):
    binary: Path

    def invoke(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(self.binary), *args], capture_output=True,
                              text=True, check=False, timeout=10)

    def run_case(self, values: list[int], width: int = 4, chunk: int = 16,
                 threshold: int = 0) -> dict:
        completed = self.invoke("--pes", str(width), "--chunk-size", str(chunk),
                                "--threshold", str(threshold), "--values",
                                ",".join(map(str, values)))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        data = json.loads(completed.stdout)
        self.assertEqual(data["schema"], "meshcompact-oracle-v1")
        self.assertEqual(data["input"], values)
        self.assertEqual(data["width"], width)
        self.assertEqual(data["chunk_size"], chunk)
        self.assertEqual(data["threshold"], threshold)
        indices = [i for i, value in enumerate(values) if value >= threshold]
        self.assertEqual(data["indices"], indices)
        self.assertEqual(data["output"], [values[i] for i in indices])
        self.assertEqual(data["selected_count"], len(indices))
        self.assertEqual(len(data["pes"]), width)
        return data

    def reject(self, *args: str) -> None:
        completed = self.invoke(*args)
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertIn("meshcompact-reference:", completed.stderr)

    @staticmethod
    def args(**overrides: str) -> list[str]:
        fields = {"pes": "4", "chunk-size": "16", "threshold": "0", "values": "1,-1,2"}
        fields.update(overrides)
        return [part for name, value in fields.items() for part in ("--" + name, value)]

    def test_exact_schema_and_cross_pe_trace(self) -> None:
        data = self.run_case([0, 0, 0, 0, 5, 6, 7, 8], 4, 2, 1)
        self.assertEqual(set(data), {"schema", "width", "chunk_size", "threshold", "input",
                                     "output", "indices", "selected_count", "pes"})
        self.assertEqual([pe["received_count"] for pe in data["pes"]], [2, 4, 2, 0])
        self.assertEqual([pe["forwarded_count"] for pe in data["pes"]], [0, 2, 4, 2])
        self.assertEqual([pe["output_values"] for pe in data["pes"]], [[5, 6], [7, 8], [], []])
        self.assertEqual([pe["output_indices"] for pe in data["pes"]], [[4, 5], [6, 7], [], []])
        keys = {"pe", "input_start", "valid_count", "local_count", "offset", "prefix_count",
                "output_count", "received_count", "forwarded_count", "output_values", "output_indices"}
        for pe in data["pes"]:
            self.assertEqual(set(pe), keys)

    def test_empty_input_and_empty_pes(self) -> None:
        data = self.run_case([], 8, 1)
        for pe in data["pes"]:
            for key in ("input_start", "valid_count", "local_count", "offset", "prefix_count",
                        "output_count", "received_count", "forwarded_count"):
                self.assertEqual(pe[key], 0)
        data = self.run_case([9, -9], 8, 1)
        self.assertEqual([pe["valid_count"] for pe in data["pes"]], [1, 1, 0, 0, 0, 0, 0, 0])

    def test_none_all_and_threshold_inclusive(self) -> None:
        self.run_case([5, 5, 5], threshold=6)
        self.run_case([5, 5, 5], threshold=5)
        self.run_case([4, 5, 6], threshold=5)

    def test_int32_extremes_do_not_sum(self) -> None:
        minimum, maximum = -(2**31), 2**31 - 1
        self.run_case([maximum] * 64, threshold=maximum)
        self.run_case([minimum, maximum, minimum, maximum], threshold=minimum)
        self.run_case([minimum, maximum, minimum, maximum], threshold=maximum)

    def test_duplicates_keep_distinct_original_indices(self) -> None:
        data = self.run_case([8, -2, 8, -2, 8, -2, 8], 3, 3, 8)
        self.assertEqual(data["indices"], [0, 2, 4, 6])

    def test_maximum_capacity(self) -> None:
        self.run_case(list(range(512)), 8, 64, 255)

    def test_fixed_seed_random_cases(self) -> None:
        rng = random.Random(0xC04FAC7)
        for _ in range(40):
            width, chunk = rng.randint(1, 8), rng.randint(1, 64)
            values = [rng.randint(-(2**31), 2**31 - 1) for _ in range(rng.randint(0, width * chunk))]
            self.run_case(values, width, chunk, rng.randint(-(2**31), 2**31 - 1))

    def test_invalid_width_and_chunk(self) -> None:
        for key, values in (("pes", ("0", "9", "-1", "18446744073709551616")),
                            ("chunk-size", ("0", "65", "-1", "18446744073709551616"))):
            for value in values:
                with self.subTest(key=key, value=value):
                    self.reject(*self.args(**{key: value}))

    def test_invalid_value_and_threshold_ranges(self) -> None:
        for key in ("values", "threshold"):
            for value in ("2147483648", "-2147483649", "999999999999999999999"):
                with self.subTest(key=key, value=value):
                    self.reject(*self.args(**{key: value}))

    def test_malformed_integer_syntax(self) -> None:
        for key in ("pes", "chunk-size", "threshold"):
            for value in ("", " 1", "1 ", "+1", "1.0", "0x10", "NaN", "1x"):
                with self.subTest(key=key, value=value):
                    self.reject(*self.args(**{key: value}))

    def test_malformed_comma_separated_values(self) -> None:
        for value in (",1", "1,", "1,,2", "1, 2", "1,2 ", "1,+2", "1,2.0", "1,NaN", "1,0x2"):
            with self.subTest(value=value):
                self.reject(*self.args(values=value))

    def test_input_capacity_rejected(self) -> None:
        self.reject(*self.args(pes="1", **{"chunk-size": "2", "values": "1,2,3"}))
        self.reject(*self.args(pes="8", **{"chunk-size": "64", "values": ",".join(["0"] * 513)}))

    def test_all_flags_required_and_values_missing(self) -> None:
        self.reject()
        args = self.args()
        for i in range(0, len(args), 2):
            self.reject(*(args[:i] + args[i + 2:]))
        for flag in ("--pes", "--chunk-size", "--threshold", "--values"):
            self.reject(flag)

    def test_duplicate_and_unknown_flags(self) -> None:
        self.reject(*self.args(), "--pes", "2")
        self.reject(*self.args(), "--threshold", "2")
        self.reject(*self.args(), "--unknown", "2")

    def test_help(self) -> None:
        completed = self.invoke("--help")
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stderr, "")
        self.assertIn("Usage:", completed.stdout)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--binary", type=Path, required=True)
    arguments, remaining = parser.parse_known_args()
    sys.argv[1:] = remaining
    CompactionCliTests.binary = arguments.binary.resolve()
    if not CompactionCliTests.binary.is_file():
        raise SystemExit(f"missing test binary: {CompactionCliTests.binary}")
    unittest.main()
