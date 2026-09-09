#!/usr/bin/env cs_python
"""Simulator-only SdkRuntime transport. Run in the private staged directory.

This process receives inputs, not expected results. Independent C++ comparison
happens on macOS after all raw device outputs have been copied back.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

if __package__:
    from .simulator_contract import METADATA, ContractError, validate_request
else:
    from simulator_contract import METADATA, ContractError, validate_request


def run(name: str, request: dict) -> dict:
    import numpy as np
    from cerebras.sdk.runtime.sdkruntimepybind import (
        MemcpyDataType, MemcpyOrder, SdkRuntime,
    )

    validate_request(request)
    width, chunk = request["width"], request["chunk_size"]
    metadata = json.loads((Path(name) / "out.json").read_text(encoding="utf-8"))
    if int(metadata["params"]["width"]) != width or int(metadata["params"]["chunk_size"]) != chunk:
        raise ContractError("compiled layout does not match host request")
    # No cmaddr argument is exposed: this runner cannot silently target hardware.
    runner = SdkRuntime(name, suppress_simfab_trace=True, simfab_numthreads=4)
    copy_options = dict(streaming=False, order=MemcpyOrder.ROW_MAJOR,
                        data_type=MemcpyDataType.MEMCPY_32BIT, nonblock=False)
    return execute_runtime(runner, request, np, copy_options)


def execute_runtime(runner, request: dict, np, copy_options: dict) -> dict:
    """One reusable runtime session, including failure-safe lifecycle cleanup.

    Runtime and array dependencies are explicit so failure paths can be tested
    without importing or imitating the installed Cerebras runtime.
    """
    width, chunk = request["width"], request["chunk_size"]
    results = []
    try:
        symbols = {field: runner.get_id(field) for field in ("values", "valid_count", *METADATA)}
        runner.load()
        runner.run()
        for case in request["cases"]:
            values = np.asarray(case["values"], dtype=np.int32)
            counts = np.asarray(case["valid_count"], dtype=np.int32)
            runner.memcpy_h2d(symbols["values"], values, 0, 0, width, 1, chunk, **copy_options)
            runner.memcpy_h2d(symbols["valid_count"], counts, 0, 0, width, 1, 1, **copy_options)
            # A blocking launch is essential: every physical PE must unblock its
            # local command stream, including PEs owning no valid input values.
            runner.launch("compute", nonblock=False)
            output = np.empty(width * chunk, dtype=np.int32)
            runner.memcpy_d2h(output, symbols["values"], 0, 0, width, 1, chunk, **copy_options)
            result = {"name": case["name"], "values": output.tolist()}
            for field in METADATA:
                buffer = np.empty(width, dtype=np.int32)
                runner.memcpy_d2h(buffer, symbols[field], 0, 0, width, 1, 1, **copy_options)
                result[field] = buffer.tolist()
            results.append(result)
            print(f"DEVICE_COPY_COMPLETE {case['name']}", flush=True)
    except BaseException:
        # Loading itself may have partially started resources. Attempt cleanup
        # even then, but never replace the original diagnostic with stop's error.
        try:
            runner.stop()
        except Exception:
            pass
        raise
    else:
        # A failure to close an otherwise successful session is still a failed
        # run; main() must not emit a success artifact in that situation.
        runner.stop()
    return {"schema": "meshscan-device-output-v1", "execution": "wse3-fabric-simulator",
            "width": width, "chunk_size": chunk, "runtime_sessions": 1, "cases": results}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--input-json", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    args = parser.parse_args()
    result = run(args.name, json.loads(args.input_json.read_text(encoding="utf-8")))
    # Only a completed lifecycle emits a result artifact; no partial success file.
    with args.output_json.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
