"""Strict subprocess boundary to the native C++ stable-compaction reference."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

from host.compaction_contract import ContractError, require_int


def run_oracle(binary: Path, values: list[int], width: int, chunk: int,
               threshold: int) -> dict:
    process = subprocess.run(
        [str(binary), "--pes", str(width), "--chunk-size", str(chunk),
         "--threshold", str(threshold), "--values", ",".join(map(str, values))],
        capture_output=True, text=True, timeout=15, check=False,
    )
    if process.returncode:
        raise ContractError(f"C++ oracle failed: {process.stderr.strip()[:1000]}")
    try:
        result = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        raise ContractError("C++ oracle emitted invalid JSON") from error
    fields = {"schema", "width", "chunk_size", "threshold", "input", "output",
              "indices", "selected_count", "pes"}
    if not isinstance(result, dict) or set(result) != fields:
        raise ContractError("unexpected C++ compaction schema")
    if result["schema"] != "meshcompact-oracle-v1" or result["input"] != values:
        raise ContractError("C++ provenance/input mismatch")
    for field, expected in (("width", width), ("chunk_size", chunk), ("threshold", threshold)):
        if type(result[field]) is not int or result[field] != expected:
            raise ContractError(f"C++ {field} mismatch")
    selected = require_int(result["selected_count"], "selected_count", 0, len(values))
    if not isinstance(result["output"], list) or not isinstance(result["indices"], list):
        raise ContractError("C++ output/indices must be arrays")
    if len(result["output"]) != selected or len(result["indices"]) != selected:
        raise ContractError("C++ selected count mismatch")
    previous = -1
    for value, index in zip(result["output"], result["indices"]):
        require_int(value, "output value", -(2**31), 2**31 - 1)
        require_int(index, "output index", previous + 1, len(values) - 1)
        if values[index] != value or value < threshold:
            raise ContractError("C++ selected record does not match input/predicate")
        previous = index
    if not isinstance(result["pes"], list) or len(result["pes"]) != width:
        raise ContractError("C++ PE trace mismatch")
    pe_fields = {"pe", "input_start", "valid_count", "local_count", "offset",
                 "prefix_count", "output_count", "received_count", "forwarded_count",
                 "output_values", "output_indices"}
    cursor = 0
    for pe, trace in enumerate(result["pes"]):
        if not isinstance(trace, dict) or set(trace) != pe_fields:
            raise ContractError("invalid C++ PE trace schema")
        for field in pe_fields - {"output_values", "output_indices"}:
            require_int(trace[field], field, 0, width * chunk)
        if trace["pe"] != pe or trace["output_count"] > chunk:
            raise ContractError("invalid C++ PE identity/capacity")
        count = trace["output_count"]
        if trace["output_values"] != result["output"][cursor:cursor + count] or \
                trace["output_indices"] != result["indices"][cursor:cursor + count]:
            raise ContractError("C++ PE output disagrees with serial result")
        cursor += count
    if cursor != selected:
        raise ContractError("C++ PE outputs do not cover serial result")
    return result
