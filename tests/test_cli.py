from __future__ import annotations

import argparse
import subprocess
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from oracle_bridge import OracleError, run_oracle  # noqa: E402


class CliIntegrationTests(unittest.TestCase):
    binary: Path

    def test_known_json_contract(self) -> None:
        payload = run_oracle(self.binary, [3, -2, 5, 7, -4, 1], 4)
        self.assertEqual(payload["output"], [3, 1, 6, 13, 9, 10])
        self.assertEqual(
            [(trace["begin"], trace["end"]) for trace in payload["pes"]],
            [(0, 2), (2, 4), (4, 5), (5, 6)],
        )

    def test_empty_input(self) -> None:
        payload = run_oracle(self.binary, [], 4)
        self.assertEqual(payload["output"], [])
        self.assertTrue(all(trace["begin"] == 0 for trace in payload["pes"]))

    def test_more_pes_than_values(self) -> None:
        payload = run_oracle(self.binary, [1, 2, 3], 8)
        self.assertEqual(payload["output"], [1, 3, 6])
        self.assertEqual(len(payload["pes"]), 8)

    def test_invalid_pe_count(self) -> None:
        with self.assertRaisesRegex(OracleError, "positive integer"):
            run_oracle(self.binary, [1], 0)

    def test_pe_safety_limit(self) -> None:
        with self.assertRaisesRegex(OracleError, "safety limit"):
            run_oracle(self.binary, [1], 4097)

    def test_checked_overflow(self) -> None:
        with self.assertRaisesRegex(OracleError, "signed 32-bit range"):
            run_oracle(self.binary, [2**31 - 1, 1], 2)

    def test_help_and_unknown_argument(self) -> None:
        help_result = subprocess.run(
            [str(self.binary), "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("Usage:", help_result.stdout)

        bad_result = subprocess.run(
            [str(self.binary), "--unknown"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(bad_result.returncode, 2)
        self.assertIn("unknown argument", bad_result.stderr)


def _parse_binary_argument() -> Path:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--binary", type=Path, required=True)
    arguments, remaining = parser.parse_known_args()
    sys.argv[1:] = remaining
    return arguments.binary.resolve()


if __name__ == "__main__":
    CliIntegrationTests.binary = _parse_binary_argument()
    if not CliIntegrationTests.binary.is_file():
        raise SystemExit(f"missing test binary: {CliIntegrationTests.binary}")
    unittest.main()
