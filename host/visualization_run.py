#!/usr/bin/env cs_python
"""Run one existing CSL example with private simulator traces enabled."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

if __package__:
    from . import compaction_contract, compaction_device_run, device_run, simulator_contract
else:
    import compaction_contract
    import compaction_device_run
    import device_run
    import simulator_contract


def run(algorithm: str, name: str, request: dict) -> dict:
    if algorithm not in ("scan", "compaction"):
        raise ValueError("algorithm must be scan or compaction")
    contract = simulator_contract if algorithm == "scan" else compaction_contract
    transport = device_run if algorithm == "scan" else compaction_device_run
    contract.validate_request(request)
    if request["width"] != 4 or len(request["cases"]) != 1:
        raise ValueError("visualization requires four PEs and exactly one launch")
    metadata = json.loads((Path(name) / "out.json").read_text(encoding="utf-8"))
    compaction_device_run.validate_compiled_params(
        metadata, request["width"], request["chunk_size"])

    import numpy as np
    from cerebras.sdk.runtime.sdkruntimepybind import (
        MemcpyDataType, MemcpyOrder, SdkRuntime,
    )

    runner = SdkRuntime(name, suppress_simfab_trace=False, simfab_numthreads=4)
    copy_options = dict(streaming=False, order=MemcpyOrder.ROW_MAJOR,
                        data_type=MemcpyDataType.MEMCPY_32BIT, nonblock=False)
    # The existing transport owns H2D, one blocking launch, D2H and cleanup.
    return transport.execute_runtime(runner, request, np, copy_options)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--algorithm", choices=("scan", "compaction"), default="scan")
    parser.add_argument("--name", required=True)
    parser.add_argument("--input-json", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    args = parser.parse_args()
    result = run(args.algorithm, args.name,
                 json.loads(args.input_json.read_text(encoding="utf-8")))
    with args.output_json.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
