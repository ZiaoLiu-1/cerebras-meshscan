"""Deterministic cases and exact-output checks for the future SDK host.

This module deliberately does not implement prefix scan in Python. Expected
values come from the schema-checked C++ oracle so the device runner has one
independent reference implementation.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from tools.oracle_bridge import INT32_MAX, INT32_MIN, run_oracle

PE_COUNT = 2
VALUES_PER_PE = 4
CASE_LENGTH = PE_COUNT * VALUES_PER_PE
RANDOM_SEED = 0xCE4EB2A5
RANDOM_CASE_COUNT = 16


@dataclass(frozen=True)
class ValidationCase:
    """One fixed-width input sent across the two-PE MVP layout."""

    name: str
    values: tuple[int, ...]


class DeviceValidationError(RuntimeError):
    """Raised when a device result violates the MVP output contract."""


def validation_cases() -> tuple[ValidationCase, ...]:
    """Return one hand-worked example and 16 reproducible random cases."""

    cases = [
        ValidationCase(
            name="hand-worked-mixed-sign",
            values=(2, -1, 3, 4, 5, -2, 0, 1),
        )
    ]
    generator = random.Random(RANDOM_SEED)
    for index in range(RANDOM_CASE_COUNT):
        values = tuple(generator.randint(-16, 16) for _ in range(CASE_LENGTH))
        cases.append(ValidationCase(name=f"seeded-{index:02d}", values=values))
    return tuple(cases)


def expected_output(binary: Path, case: ValidationCase) -> tuple[int, ...]:
    """Ask the C++ oracle for the exact expected output of one case."""

    payload = run_oracle(binary, list(case.values), PE_COUNT)
    return tuple(payload["output"])


def validate_device_output(
    case: ValidationCase,
    expected: Sequence[int],
    actual: Sequence[Any],
) -> None:
    """Require one signed-int32 device value per input and exact equality."""

    if len(actual) != len(case.values):
        raise DeviceValidationError(
            f"{case.name}: device returned {len(actual)} values; "
            f"expected {len(case.values)}"
        )
    if len(expected) != len(case.values):
        raise DeviceValidationError(
            f"{case.name}: oracle returned {len(expected)} values; "
            f"expected {len(case.values)}"
        )

    for index, value in enumerate(actual):
        if isinstance(value, bool) or not isinstance(value, int):
            raise DeviceValidationError(
                f"{case.name}: output[{index}] is not an integer"
            )
        if value < INT32_MIN or value > INT32_MAX:
            raise DeviceValidationError(
                f"{case.name}: output[{index}] falls outside signed 32-bit range"
            )
        if value != expected[index]:
            raise DeviceValidationError(
                f"{case.name}: output[{index}] was {value}; "
                f"expected {expected[index]}"
            )
