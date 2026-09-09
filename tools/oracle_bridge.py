"""Subprocess bridge from a future Python SdkRuntime host to the C++ oracle."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

INT32_MIN = -(2**31)
INT32_MAX = 2**31 - 1


class OracleError(RuntimeError):
    """Raised when the C++ oracle fails or violates its JSON contract."""


def _require_int32(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise OracleError(f"{field} must be an integer")
    if value < INT32_MIN or value > INT32_MAX:
        raise OracleError(f"{field} falls outside signed 32-bit range")
    return value


def _validate_payload(
    payload: Any, *, values: list[int], pe_count: int
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise OracleError("oracle JSON root must be an object")
    if payload.get("model") != "cpu-logical-pe":
        raise OracleError("oracle JSON has an unknown model")
    if payload.get("input") != values:
        raise OracleError("oracle JSON input does not match the request")

    output = payload.get("output")
    traces = payload.get("pes")
    if not isinstance(output, list) or len(output) != len(values):
        raise OracleError("oracle output has the wrong length")
    for index, value in enumerate(output):
        _require_int32(value, f"output[{index}]")
    if not isinstance(traces, list) or len(traces) != pe_count:
        raise OracleError("oracle trace count does not match the PE count")

    cursor = 0
    flattened_output: list[int] = []
    previous_carry = 0
    required_trace_keys = {
        "pe",
        "begin",
        "end",
        "incoming_carry",
        "local_total",
        "local_prefix",
        "output",
    }
    for pe_index, trace in enumerate(traces):
        if not isinstance(trace, dict) or set(trace) != required_trace_keys:
            raise OracleError(f"PE {pe_index} trace has an invalid schema")
        if trace["pe"] != pe_index or trace["begin"] != cursor:
            raise OracleError(f"PE {pe_index} trace is not contiguous")
        end = trace["end"]
        if isinstance(end, bool) or not isinstance(end, int):
            raise OracleError(f"PE {pe_index} end must be an integer")
        if end < cursor or end > len(values):
            raise OracleError(f"PE {pe_index} has invalid bounds")

        local_prefix = trace["local_prefix"]
        trace_output = trace["output"]
        local_size = end - cursor
        if not isinstance(local_prefix, list) or len(local_prefix) != local_size:
            raise OracleError(f"PE {pe_index} local prefix has the wrong length")
        if not isinstance(trace_output, list) or len(trace_output) != local_size:
            raise OracleError(f"PE {pe_index} output has the wrong length")

        incoming = _require_int32(
            trace["incoming_carry"], f"PE {pe_index} incoming_carry"
        )
        local_total = _require_int32(
            trace["local_total"], f"PE {pe_index} local_total"
        )
        if incoming != previous_carry:
            raise OracleError(f"PE {pe_index} incoming carry breaks the chain")
        for item_index, value in enumerate(local_prefix):
            _require_int32(value, f"PE {pe_index} local_prefix[{item_index}]")
        for item_index, value in enumerate(trace_output):
            _require_int32(value, f"PE {pe_index} output[{item_index}]")

        outgoing = incoming + local_total
        _require_int32(outgoing, f"PE {pe_index} outgoing carry")
        if trace_output and trace_output[-1] != outgoing:
            raise OracleError(f"PE {pe_index} final output differs from its carry")

        flattened_output.extend(trace_output)
        previous_carry = outgoing
        cursor = end

    if cursor != len(values) or flattened_output != output:
        raise OracleError("PE traces do not reconstruct the oracle output")
    return payload


def run_oracle(
    binary: Path, values: list[int], pe_count: int
) -> dict[str, Any]:
    """Run the C++ oracle and return a schema-checked JSON object.

    The future SDK host should compare device output with this returned C++
    result. It should not quietly replace the oracle with a second Python scan.
    """

    command = [
        str(binary),
        "--pes",
        str(pe_count),
        "--values",
        ",".join(str(value) for value in values),
    ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"exit code {completed.returncode}"
        raise OracleError(f"C++ oracle failed: {detail}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise OracleError("C++ oracle returned invalid JSON") from error
    return _validate_payload(payload, values=values, pe_count=pe_count)
