"""SDK-independent packing and strict result validation for MeshScan.

The only scan implementation used as an oracle remains the C++ executable.
This module describes transport; it does not compute prefix sums.
"""

from __future__ import annotations

import random
from typing import Any

INT32_MIN = -(2**31)
INT32_MAX = 2**31 - 1
SEED = 0xCE4EB2A5
SUPPORTED_WIDTHS = (1, 2, 3, 4, 8)
METADATA = ("incoming_carry", "local_total", "outgoing_carry", "completed")


class ContractError(ValueError):
    """A request or device response violates the transport contract."""


def require_int(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ContractError(f"{name} outside [{minimum}, {maximum}]")
    return value


def pack_case(name: str, values: list[int], width: int, chunk_size: int) -> dict:
    """Pack contiguous balanced ranges, deliberately poisoning unused slots."""
    if not isinstance(name, str) or not name or len(name) > 128:
        raise ContractError("case name must be a nonempty short string")
    require_int(width, "width", 1, 8)
    require_int(chunk_size, "chunk_size", 1, 256)
    if not isinstance(values, list) or len(values) > width * chunk_size:
        raise ContractError("input exceeds compiled capacity")
    for index, value in enumerate(values):
        require_int(value, f"input[{index}]", INT32_MIN, INT32_MAX)
    base, extra = divmod(len(values), width)
    counts = [base + (pe < extra) for pe in range(width)]
    packed: list[int] = []
    cursor = 0
    for pe, count in enumerate(counts):
        packed.extend(values[cursor:cursor + count])
        packed.extend([123456789 + pe] * (chunk_size - count))
        cursor += count
    return {"name": name, "length": len(values), "values": packed,
            "valid_count": counts}


def suite_cases(width: int, chunk_size: int, random_count: int = 16) -> list[dict]:
    """Deterministic boundary, signed-int32 and varied-length random inputs."""
    require_int(width, "width", 1, 8)
    require_int(chunk_size, "chunk_size", 8, 256)
    require_int(random_count, "random_count", 0, 128)
    capacity = width * chunk_size
    lengths = sorted({0, 1, max(0, width - 1), width, width + 1,
                      2 * width + 1, capacity - 1, capacity})
    result = [{"name": f"boundary-{length}",
               "values": [((index * 7) % 17) - 8 for index in range(length)]}
              for length in lengths]
    result.extend([
        {"name": "all-zero", "values": [0] * capacity},
        {"name": "all-positive", "values": [1 + index % 3 for index in range(capacity)]},
        {"name": "all-negative", "values": [-1 - index % 3 for index in range(capacity)]},
        {"name": "int32-upper", "values": [INT32_MAX, -1]},
        {"name": "int32-lower", "values": [INT32_MIN, 1]},
        {"name": "int32-cancellation", "values": [INT32_MAX, -INT32_MAX, INT32_MIN, INT32_MAX]},
        {"name": "hand-worked", "values": [2, -1, 3, 4, 5, -2, 0, 1]},
    ])
    generator = random.Random(SEED + width)
    for index in range(random_count):
        length = generator.randint(1, capacity)
        result.append({"name": f"random-{index:02d}",
                       "values": [generator.randint(-1000, 1000) for _ in range(length)]})
    # Repeating a known input after unrelated launches catches stale local state.
    result.append({"name": "hand-worked-repeat", "values": [2, -1, 3, 4, 5, -2, 0, 1]})
    return result


def validate_request(request: Any) -> dict:
    if not isinstance(request, dict) or set(request) != {"schema", "width", "chunk_size", "cases"}:
        raise ContractError("invalid request schema")
    if request["schema"] != "meshscan-device-input-v1":
        raise ContractError("unknown input schema")
    width = require_int(request["width"], "width", 1, 8)
    chunk = require_int(request["chunk_size"], "chunk_size", 1, 256)
    cases = request["cases"]
    if not isinstance(cases, list) or not 1 <= len(cases) <= 256:
        raise ContractError("invalid case count")
    names: set[str] = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) != {"name", "length", "values", "valid_count"}:
            raise ContractError("invalid case schema")
        name = case["name"]
        if not isinstance(name, str) or not name or len(name) > 128 or name in names:
            raise ContractError("invalid or duplicate case name")
        names.add(name)
        length = require_int(case["length"], "length", 1, width * chunk)
        counts = case["valid_count"]
        if not isinstance(counts, list) or len(counts) != width:
            raise ContractError("valid_count must have one entry per PE")
        for count in counts:
            require_int(count, "valid_count", 0, chunk)
        base, extra = divmod(length, width)
        if counts != [base + (pe < extra) for pe in range(width)]:
            raise ContractError("valid_count does not describe balanced partitioning")
        if not isinstance(case["values"], list) or len(case["values"]) != width * chunk:
            raise ContractError("wrong physical buffer length")
        for value in case["values"]:
            require_int(value, "packed value", INT32_MIN, INT32_MAX)
    return request


def _check_oracle(packed: dict, oracle: Any, width: int, chunk: int) -> None:
    """Bind the C++ result to this input and check its trace structure."""
    if not isinstance(oracle, dict) or set(oracle) != {"model", "input", "output", "pes"}:
        raise ContractError("invalid C++ oracle schema")
    if oracle["model"] != "cpu-logical-pe":
        raise ContractError("unknown C++ oracle model")

    def int32_array(value: Any, name: str, length: int) -> list[int]:
        if not isinstance(value, list) or len(value) != length:
            raise ContractError(f"{name} must contain exactly {length} integers")
        for item in value:
            require_int(item, name, INT32_MIN, INT32_MAX)
        return value

    original = [value for pe, count in enumerate(packed["valid_count"])
                for value in packed["values"][pe * chunk:pe * chunk + count]]
    if int32_array(oracle["input"], "C++ oracle input", packed["length"]) != original:
        raise ContractError("C++ oracle input identity mismatch")
    output = int32_array(oracle["output"], "C++ oracle output", packed["length"])
    traces = oracle["pes"]
    if not isinstance(traces, list) or len(traces) != width:
        raise ContractError("C++ oracle PE count mismatch")

    trace_fields = {"pe", "begin", "end", "incoming_carry", "local_total",
                    "local_prefix", "output"}
    cursor, previous_carry = 0, 0
    flattened = []
    for pe, trace in enumerate(traces):
        if not isinstance(trace, dict) or set(trace) != trace_fields:
            raise ContractError(f"C++ oracle PE {pe} invalid trace schema")
        count = packed["valid_count"][pe]
        for field, expected in (("pe", pe), ("begin", cursor), ("end", cursor + count)):
            if type(trace[field]) is not int or trace[field] != expected:
                raise ContractError(f"C++ oracle PE {pe} {field} mismatch")
        prefixes = int32_array(trace["local_prefix"], "C++ local_prefix", count)
        values = int32_array(trace["output"], "C++ PE output", count)
        incoming = require_int(trace["incoming_carry"], "C++ incoming_carry", INT32_MIN, INT32_MAX)
        total = require_int(trace["local_total"], "C++ local_total", INT32_MIN, INT32_MAX)
        if incoming != previous_carry or total != (prefixes[-1] if prefixes else 0):
            raise ContractError(f"C++ oracle PE {pe} inconsistent carry metadata")
        outgoing = require_int(incoming + total, "C++ outgoing_carry", INT32_MIN, INT32_MAX)
        if values and values[-1] != outgoing:
            raise ContractError(f"C++ oracle PE {pe} final output differs from its carry")
        flattened.extend(values)
        previous_carry = outgoing
        cursor += count
    if flattened != output:
        raise ContractError("C++ oracle traces do not reconstruct its output")


def validate_response(request: dict, response: Any, oracles: dict[str, dict]) -> list[dict]:
    """Require exact output, PE carries, padding, and completion on every launch."""
    validate_request(request)
    if not isinstance(response, dict) or set(response) != {
        "schema", "execution", "width", "chunk_size", "runtime_sessions", "cases"
    }:
        raise ContractError("invalid response schema")
    if response["schema"] != "meshscan-device-output-v1" or response["execution"] != "wse3-fabric-simulator":
        raise ContractError("response execution provenance mismatch")
    width, chunk = request["width"], request["chunk_size"]
    for field, expected in (("width", width), ("chunk_size", chunk), ("runtime_sessions", 1)):
        if type(response[field]) is not int or response[field] != expected:
            raise ContractError(f"response {field} mismatch")
    actual_cases = response["cases"]
    if not isinstance(actual_cases, list) or len(actual_cases) != len(request["cases"]):
        raise ContractError("response case count mismatch")
    checks = []
    for packed, actual in zip(request["cases"], actual_cases):
        name = packed["name"]
        if not isinstance(actual, dict) or set(actual) != {"name", "values", *METADATA}:
            raise ContractError(f"{name}: invalid result schema")
        if actual["name"] != name:
            raise ContractError(f"{name}: result order/name mismatch")
        if name not in oracles:
            raise ContractError(f"{name}: missing independent C++ result")
        oracle = oracles[name]
        _check_oracle(packed, oracle, width, chunk)
        values = actual["values"]
        if not isinstance(values, list) or len(values) != width * chunk:
            raise ContractError(f"{name}: wrong physical output length")
        for value in values:
            require_int(value, "device value", INT32_MIN, INT32_MAX)
        for field in METADATA:
            if not isinstance(actual[field], list) or len(actual[field]) != width:
                raise ContractError(f"{name}: invalid {field} length")
            for value in actual[field]:
                require_int(value, field, INT32_MIN, INT32_MAX)
        flattened = []
        for pe, trace in enumerate(oracle["pes"]):
            count = packed["valid_count"][pe]
            begin = pe * chunk
            output = values[begin:begin + count]
            if output != trace["output"]:
                raise ContractError(f"{name}: PE {pe} output differs from C++ oracle")
            flattened.extend(output)
            if values[begin + count:begin + chunk] != packed["values"][begin + count:begin + chunk]:
                raise ContractError(f"{name}: PE {pe} poisoned padding was modified")
            expected = {"incoming_carry": trace["incoming_carry"],
                        "local_total": trace["local_total"],
                        "outgoing_carry": trace["incoming_carry"] + trace["local_total"],
                        "completed": 1}
            for field, value in expected.items():
                if actual[field][pe] != value:
                    raise ContractError(f"{name}: PE {pe} {field} mismatch")
        if flattened != oracle["output"]:
            raise ContractError(f"{name}: flattened output mismatch")
        checks.append({"name": name, "length": packed["length"], "status": "passed",
                       "empty_pes": sum(count == 0 for count in packed["valid_count"]),
                       "output": flattened})
    return checks
