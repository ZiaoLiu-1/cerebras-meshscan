"""SDK-independent transport and exact validation for stable stream compaction.

This module packs inputs and checks C++ oracle records. It does not implement a
second selection/scan/scatter algorithm. Expected values never enter the device
request. Empty inputs intentionally exercise the actual device protocol.
"""

from __future__ import annotations

import random
from typing import Any

INT32_MIN = -(2**31)
INT32_MAX = 2**31 - 1
SEED = 0xC04FAC7
SUPPORTED_WIDTHS = (1, 2, 3, 4, 8)
METADATA = ("local_count", "offset", "prefix_count", "output_count",
            "forwarded_count", "received_count", "completed", "protocol_error")
INPUT_POISON = 123456789
OUTPUT_POISON = 987654321
INDEX_POISON = -1


class ContractError(ValueError):
    """A request or response violates the fixed-buffer transport contract."""


def require_int(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ContractError(f"{name} outside [{minimum}, {maximum}]")
    return value


def _name(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ContractError("case name must be a nonempty short string")
    return value


def _integers(value: Any, name: str, length: int,
              minimum: int = INT32_MIN, maximum: int = INT32_MAX) -> list[int]:
    if not isinstance(value, list) or len(value) != length:
        raise ContractError(f"{name} must contain exactly {length} integers")
    for item in value:
        require_int(item, name, minimum, maximum)
    return value


def pack_case(name: str, values: list[int], width: int, chunk_size: int,
              threshold: int = 0) -> dict:
    """Map balanced input ranges to physical slots; keep all padding nonzero."""
    _name(name)
    require_int(width, "width", 1, 8)
    require_int(chunk_size, "chunk_size", 1, 64)
    require_int(threshold, "threshold", INT32_MIN, INT32_MAX)
    if not isinstance(values, list) or len(values) > width * chunk_size:
        raise ContractError("input exceeds compiled capacity")
    _integers(values, "input", len(values))
    base, extra = divmod(len(values), width)
    counts = [base + int(pe < extra) for pe in range(width)]
    packed, starts = [], []
    cursor = 0
    for pe, count in enumerate(counts):
        starts.append(cursor)
        packed.extend(values[cursor:cursor + count])
        packed.extend([INPUT_POISON + pe] * (chunk_size - count))
        cursor += count
    return {"name": name, "length": len(values), "threshold": threshold,
            "values": packed, "valid_count": counts, "input_start": starts}


def output_initializers(width: int, chunk_size: int) -> tuple[list[int], list[int]]:
    """Per-launch sentinels distinguish untouched output slots from valid data."""
    require_int(width, "width", 1, 8)
    require_int(chunk_size, "chunk_size", 1, 64)
    return ([OUTPUT_POISON + pe for pe in range(width) for _ in range(chunk_size)],
            [INDEX_POISON] * (width * chunk_size))


def suite_cases(width: int, chunk_size: int, random_count: int = 8) -> list[dict]:
    """Deterministic shape, selectivity, stability and repeated-launch cases."""
    require_int(width, "width", 1, 8)
    require_int(chunk_size, "chunk_size", 1, 64)
    require_int(random_count, "random_count", 0, 128)
    capacity = width * chunk_size
    lengths = sorted({value for value in (0, 1, width - 1, width, width + 1,
                      2 * width + 1, capacity - 1, capacity) if 0 <= value <= capacity})
    cases = [{"name": f"boundary-{length}", "threshold": 0,
              "values": [((index * 7) % 17) - 8 for index in range(length)]}
             for length in lengths]
    examples = [
        ("none-selected", [-1] * capacity, 0),
        ("all-selected", list(range(capacity)), INT32_MIN),
        ("equal-to-threshold", [5] * capacity, 5),
        ("threshold-just-above", [5] * capacity, 6),
        ("last-pe-only", [-1] * ((width - 1) * chunk_size) + [9] * chunk_size, 1),
        ("front-pe-only", [9] * chunk_size + [-1] * ((width - 1) * chunk_size), 1),
        ("alternating-holes", [8 if index % 2 else -4 for index in range(capacity)], 0),
        ("duplicate-values", [7 if index % 3 else -7 for index in range(capacity)], 7),
        ("int32-extremes", [INT32_MIN, INT32_MAX, 0, INT32_MIN, INT32_MAX][:capacity], 0),
        ("int32-min-threshold", [INT32_MIN, INT32_MAX, -1, 0][:capacity], INT32_MIN),
        ("int32-max-threshold", [INT32_MIN, INT32_MAX, -1, INT32_MAX][:capacity], INT32_MAX),
        ("hand-worked", [2, -1, 5, 5, -4, 9, 0, 7][:capacity], 5),
    ]
    cases.extend({"name": name, "values": values, "threshold": threshold}
                 for name, values, threshold in examples)
    generator = random.Random(SEED + width * 131 + chunk_size)
    for index in range(random_count):
        length = generator.randint(0, capacity)
        cases.append({"name": f"random-{index:02d}",
                      "values": [generator.randint(-20, 20) for _ in range(length)],
                      "threshold": generator.randint(-22, 22)})
    # Reusing an earlier input after unrelated launches checks reset semantics.
    cases.append({"name": "hand-worked-repeat", "values": [2, -1, 5, 5, -4, 9, 0, 7][:capacity],
                  "threshold": 5})
    cases.append({"name": "empty-after-work", "values": [], "threshold": INT32_MIN})
    return cases


def validate_request(request: Any) -> dict:
    if not isinstance(request, dict) or set(request) != {"schema", "width", "chunk_size", "cases"}:
        raise ContractError("invalid request schema")
    if request["schema"] != "meshcompact-device-input-v1":
        raise ContractError("unknown input schema")
    width = require_int(request["width"], "width", 1, 8)
    chunk = require_int(request["chunk_size"], "chunk_size", 1, 64)
    if not isinstance(request["cases"], list) or not 1 <= len(request["cases"]) <= 256:
        raise ContractError("invalid case count")
    names: set[str] = set()
    for case in request["cases"]:
        if not isinstance(case, dict) or set(case) != {
            "name", "length", "threshold", "values", "valid_count", "input_start"
        }:
            raise ContractError("invalid case schema")
        name = _name(case["name"])
        if name in names:
            raise ContractError("duplicate case name")
        names.add(name)
        length = require_int(case["length"], "length", 0, width * chunk)
        require_int(case["threshold"], "threshold", INT32_MIN, INT32_MAX)
        counts = _integers(case["valid_count"], "valid_count", width, 0, chunk)
        starts = _integers(case["input_start"], "input_start", width, 0, length)
        values = _integers(case["values"], "packed values", width * chunk)
        base, extra = divmod(length, width)
        if counts != [base + int(pe < extra) for pe in range(width)]:
            raise ContractError("valid_count does not describe balanced partitioning")
        cursor = 0
        for pe, count in enumerate(counts):
            if starts[pe] != cursor:
                raise ContractError("input_start does not describe contiguous ranges")
            if values[pe * chunk + count:(pe + 1) * chunk] != [INPUT_POISON + pe] * (chunk - count):
                raise ContractError("input padding does not contain the prescribed poison")
            cursor += count
    return request


def _check_oracle(packed: dict, oracle: Any, width: int, chunk: int) -> None:
    """Check oracle identity and record shapes without recomputing its algorithm."""
    if not isinstance(oracle, dict) or oracle.get("schema") != "meshcompact-oracle-v1":
        raise ContractError("missing independent C++ compaction oracle schema")
    for field, expected in (("width", width), ("chunk_size", chunk), ("threshold", packed["threshold"])):
        if type(oracle.get(field)) is not int or oracle[field] != expected:
            raise ContractError(f"C++ oracle {field} mismatch")
    original = [value for pe, count in enumerate(packed["valid_count"])
                for value in packed["values"][pe * chunk:pe * chunk + count]]
    if _integers(oracle.get("input"), "oracle input", packed["length"]) != original:
        raise ContractError("C++ oracle input identity mismatch")
    selected = require_int(oracle.get("selected_count"), "oracle selected_count", 0, packed["length"])
    _integers(oracle.get("output"), "oracle output", selected)
    _integers(oracle.get("indices"), "oracle indices", selected, 0, max(0, packed["length"] - 1))
    pes = oracle.get("pes")
    if not isinstance(pes, list) or len(pes) != width:
        raise ContractError("C++ oracle PE count mismatch")
    for pe, trace in enumerate(pes):
        if not isinstance(trace, dict):
            raise ContractError("C++ oracle invalid PE record")
        for field, expected in (("pe", pe), ("input_start", packed["input_start"][pe]),
                                ("valid_count", packed["valid_count"][pe])):
            if type(trace.get(field)) is not int or trace[field] != expected:
                raise ContractError(f"C++ oracle PE {pe} {field} mismatch")
        count = require_int(trace.get("output_count"), "oracle output_count", 0, chunk)
        _integers(trace.get("output_values"), "oracle PE output_values", count)
        _integers(trace.get("output_indices"), "oracle PE output_indices", count,
                  0, max(0, packed["length"] - 1))
        for field in METADATA[:-2]:
            require_int(trace.get(field), f"oracle {field}", 0, packed["length"])


def validate_response(request: dict, response: Any, oracles: dict[str, dict]) -> list[dict]:
    """Check every returned byte's int32 value, original index and protocol witness."""
    validate_request(request)
    if not isinstance(response, dict) or set(response) != {
        "schema", "execution", "width", "chunk_size", "runtime_sessions", "cases"
    }:
        raise ContractError("invalid response schema")
    if response["schema"] != "meshcompact-device-output-v1" or response["execution"] != "wse3-fabric-simulator":
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
        if not isinstance(actual, dict) or set(actual) != {
            "name", "values", "output_values", "output_indices", *METADATA
        }:
            raise ContractError(f"{name}: invalid result schema")
        if actual["name"] != name:
            raise ContractError(f"{name}: result order/name mismatch")
        if name not in oracles:
            raise ContractError(f"{name}: missing independent C++ result")
        oracle = oracles[name]
        _check_oracle(packed, oracle, width, chunk)
        for field in ("values", "output_values", "output_indices"):
            _integers(actual[field], f"{name}: {field}", width * chunk)
        if actual["values"] != packed["values"]:
            raise ContractError(f"{name}: input or input padding was modified")
        for field in METADATA:
            _integers(actual[field], f"{name}: {field}", width, 0, width * chunk)
        flattened, indices = [], []
        for pe, trace in enumerate(oracle["pes"]):
            count, begin = trace["output_count"], pe * chunk
            for field, suffix, poison in (("output_values", "output_values", OUTPUT_POISON + pe),
                                           ("output_indices", "output_indices", INDEX_POISON)):
                expected = trace[suffix] + [poison] * (chunk - count)
                if actual[field][begin:begin + chunk] != expected:
                    raise ContractError(f"{name}: PE {pe} {field} or padding differs from C++ oracle")
            flattened.extend(actual["output_values"][begin:begin + count])
            indices.extend(actual["output_indices"][begin:begin + count])
            for field in METADATA:
                expected = 1 if field == "completed" else 0 if field == "protocol_error" else trace[field]
                if actual[field][pe] != expected:
                    raise ContractError(f"{name}: PE {pe} {field} mismatch")
        if flattened != oracle["output"] or indices != oracle["indices"]:
            raise ContractError(f"{name}: flattened output/index mismatch")
        checks.append({"name": name, "length": packed["length"], "threshold": packed["threshold"],
                       "selected_count": oracle["selected_count"], "status": "passed",
                       "empty_pes": sum(count == 0 for count in packed["valid_count"]),
                       "output": flattened, "indices": indices})
    return checks
