from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from host.validation_cases import (  # noqa: E402
    CASE_LENGTH,
    RANDOM_CASE_COUNT,
    DeviceValidationError,
    expected_output,
    validate_device_output,
    validation_cases,
)


class ValidationCaseTests(unittest.TestCase):
    binary: Path

    def test_suite_is_fixed_width_unique_and_reproducible(self) -> None:
        first = validation_cases()
        second = validation_cases()
        self.assertEqual(first, second)
        self.assertEqual(len(first), RANDOM_CASE_COUNT + 1)
        self.assertEqual(len({case.name for case in first}), len(first))
        self.assertTrue(all(len(case.values) == CASE_LENGTH for case in first))

    def test_hand_worked_case_comes_from_cpp_oracle(self) -> None:
        case = validation_cases()[0]
        expected = expected_output(self.binary, case)
        self.assertEqual(expected, (2, 1, 4, 8, 13, 11, 11, 12))
        validate_device_output(case, expected, list(expected))

    def test_all_seeded_cases_resolve_through_cpp_oracle(self) -> None:
        for case in validation_cases()[1:]:
            expected = expected_output(self.binary, case)
            self.assertEqual(len(expected), CASE_LENGTH)
            validate_device_output(case, expected, expected)

    def test_mismatch_reports_case_index_actual_and_expected(self) -> None:
        case = validation_cases()[0]
        expected = expected_output(self.binary, case)
        actual = list(expected)
        actual[5] += 1
        with self.assertRaisesRegex(
            DeviceValidationError,
            r"hand-worked-mixed-sign: output\[5\] was 12; expected 11",
        ):
            validate_device_output(case, expected, actual)

    def test_wrong_shape_and_non_int32_values_fail(self) -> None:
        case = validation_cases()[0]
        expected = expected_output(self.binary, case)
        with self.assertRaisesRegex(DeviceValidationError, "returned 7 values"):
            validate_device_output(case, expected, list(expected[:-1]))

        invalid_type = list(expected)
        invalid_type[0] = True
        with self.assertRaisesRegex(DeviceValidationError, "not an integer"):
            validate_device_output(case, expected, invalid_type)

        too_large = list(expected)
        too_large[0] = 2**31
        with self.assertRaisesRegex(DeviceValidationError, "outside signed 32-bit"):
            validate_device_output(case, expected, too_large)


def _parse_binary_argument() -> Path:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--binary", type=Path, required=True)
    arguments, remaining = parser.parse_known_args()
    sys.argv[1:] = remaining
    return arguments.binary.resolve()


if __name__ == "__main__":
    ValidationCaseTests.binary = _parse_binary_argument()
    if not ValidationCaseTests.binary.is_file():
        raise SystemExit(f"missing test binary: {ValidationCaseTests.binary}")
    unittest.main()
