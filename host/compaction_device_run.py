#!/usr/bin/env cs_python
"""Simulator-only stable-compaction transport; it receives no expected output."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

if __package__:
    from .compaction_contract import METADATA, ContractError, output_initializers, validate_request
else:
    from compaction_contract import METADATA, ContractError, output_initializers, validate_request


def validate_compiled_params(metadata: dict, width: int, chunk: int) -> None:
    """CSLC stores parameters as decimal strings; reject bools/coercion surprises."""
    if not isinstance(metadata, dict) or not isinstance(metadata.get("params"), dict):
        raise ContractError("compiled layout lacks parameter metadata")
    for field, expected in (("width", width), ("chunk_size", chunk)):
        value = metadata["params"].get(field)
        if not ((type(value) is int and value == expected) or
                (type(value) is str and value == str(expected))):
            raise ContractError("compiled layout does not match host request")


def run(name: str, request: dict) -> dict:
    import numpy as np
    from cerebras.sdk.runtime.sdkruntimepybind import (
        MemcpyDataType, MemcpyOrder, SdkRuntime,
    )

    validate_request(request)
    metadata = json.loads((Path(name) / "out.json").read_text(encoding="utf-8"))
    validate_compiled_params(metadata, request["width"], request["chunk_size"])
    # No cmaddr option: a caller cannot accidentally dispatch to real hardware.
    runner = SdkRuntime(name, suppress_simfab_trace=True, simfab_numthreads=4)
    copy_options = dict(streaming=False, order=MemcpyOrder.ROW_MAJOR,
                        data_type=MemcpyDataType.MEMCPY_32BIT, nonblock=False)
    return execute_runtime(runner, request, np, copy_options)


def execute_runtime(runner, request: dict, np, copy_options: dict) -> dict:
    """Reuse one runtime; inject arrays/runtime only for CPU lifecycle tests."""
    validate_request(request)
    width, chunk = request["width"], request["chunk_size"]
    results = []
    array_fields = ("values", "output_values", "output_indices")
    try:
        symbols = {field: runner.get_id(field) for field in (
            *array_fields, "valid_count", "input_start", "threshold", *METADATA)}
        runner.load()
        runner.run()
        for case in request["cases"]:
            output_values, output_indices = output_initializers(width, chunk)
            transfers = {"values": case["values"], "output_values": output_values,
                         "output_indices": output_indices, "valid_count": case["valid_count"],
                         "input_start": case["input_start"], "threshold": [case["threshold"]] * width}
            for field, values in transfers.items():
                buffer = np.asarray(values, dtype=np.int32)
                per_pe = chunk if field in array_fields else 1
                runner.memcpy_h2d(symbols[field], buffer, 0, 0, width, 1, per_pe, **copy_options)
            # Empty input is not a host shortcut: all PEs execute count/scatter
            # termination, and must explicitly unblock their command stream.
            runner.launch("compute", nonblock=False)
            result = {"name": case["name"]}
            for field in (*array_fields, *METADATA):
                per_pe = chunk if field in array_fields else 1
                buffer = np.empty(width * per_pe, dtype=np.int32)
                runner.memcpy_d2h(buffer, symbols[field], 0, 0, width, 1, per_pe, **copy_options)
                result[field] = buffer.tolist()
            results.append(result)
            print(f"DEVICE_COPY_COMPLETE {case['name']}", flush=True)
    except BaseException:
        try:
            runner.stop()
        except Exception:
            pass
        raise
    else:
        # Failed cleanup means the run failed; no successful output is emitted.
        runner.stop()
    return {"schema": "meshcompact-device-output-v1", "execution": "wse3-fabric-simulator",
            "width": width, "chunk_size": chunk, "runtime_sessions": 1, "cases": results}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--input-json", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    args = parser.parse_args()
    result = run(args.name, json.loads(args.input_json.read_text(encoding="utf-8")))
    with args.output_json.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
