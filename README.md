# MeshScan

MeshScan computes integer prefix sums across a row of Cerebras processing
elements. Its second algorithm, MeshCompact, filters values by a threshold and
packs the selected records into contiguous output buffers while preserving
their original order. Both use CSL for device computation, Python for SDK
transfers, and a C++20 reference for checking results.

For example, filtering `[2, -1, 5, 5, -4, 9, 0, 7]` at `>= 5` produces
`[5, 5, 9, 7]` with original indices `[2, 3, 5, 7]`. Records move between PEs
during the device invocation; the host reads back the packed result.

## Run the CPU reference

The CPU tools need a C++20 compiler, Make, and Python 3.10 or later. They use
the C++ and Python standard libraries. Node.js is optional for JavaScript
syntax checks.

From the repository root:

```bash
make test
./build/meshscan-reference --pes 2 --values 2,-1,3,4,5,-2,0,1
./build/meshcompact-reference --pes 4 --chunk-size 3 --threshold 5 --values 2,-1,5,5,-4,9,0,7
make sanitize
```

The scan returns `[2,1,4,8,13,11,11,12]`. Each command writes JSON containing
the output and intermediate PE state. In the compaction example, PE0 holds
`[5,5,9]` and PE1 holds `[7]`; PE2 and PE3 have no output records.

`make viewer` starts an interactive scan trace at
[127.0.0.1:8765](http://127.0.0.1:8765/). It runs the C++ reference and displays
its state as a **CPU logical model — not WSE simulator**. `make tutorial`
serves the Chinese guides at [127.0.0.1:8766](http://127.0.0.1:8766/).
Both servers listen on loopback and are accessible from the same machine.

## How the algorithms work

The scan splits the input into balanced contiguous blocks. Each PE scans its
block locally, receives the sum of all preceding blocks from its west neighbor,
adds that carry to its local prefixes, and forwards the new total east. Empty
PEs forward the carry too. Alternating route colors make every middle PE consume
the incoming carry before sending the next one. The carry chain is sequential.

MeshCompact first counts selected records locally. An eastbound count prefix
assigns each selected record a global output position. Packets then travel west,
carrying a count header followed by `(rank, value, original_index)` triples.
Each PE retains records belonging to its output slots and forwards the rest.
Stable filtering can only move a record toward an earlier position, so the
current layout needs no eastbound record route. All stages run in one blocking
device invocation.

The Python host packs inputs, launches the kernel, and reads results. The C++
reference checks exact values and PE metadata; compaction also checks indices
and record conservation. Poisoned padding detects unintended reads or writes,
and repeated launches check that state is reset. See the
[scan design](docs/DESIGN.md), [compaction walkthrough](docs/COMPACTION.md), and
[validation guide](docs/VALIDATION.md).

## Run with the Cerebras SDK

The CSL and host code target SDK 2.10.1 and the WSE-3 fabric simulator. The SDK
must be obtained and installed separately. The supplied orchestration scripts
run on macOS and invoke an existing Lima VM containing the Linux SDK; see
[SDK setup and commands](docs/SDK_RUN.md) for paths and options.

With that environment configured and running:

```bash
make simulator
make simulator-compaction
```

`make trace-scan` and `make trace-compaction` run one fixed example with traces
enabled for the [official SDK GUI](docs/SDK_GUI.md). The correctness suites
suppress traces. Generated binaries, SDK assets, logs, and traces stay outside
the repository.

## Source guide

- [src/reference.cpp](src/reference.cpp) and
  [src/compaction.cpp](src/compaction.cpp): checked CPU models and PE traces.
- [src/device/](src/device/README.md) and
  [src/compaction/](src/compaction/pe.csl): CSL layouts, routes, and tasks.
- [host/](host/): packing, SDK transfers, lifecycle handling, and response checks.
- [tools/](tools/): C++ oracle bridges and SDK run orchestration.
- [docs/WALKTHROUGH.md](docs/WALKTHROUGH.md): Chinese scan code-reading guide.

## Limits and troubleshooting

These are bounded batch implementations. Scan requires every serial prefix,
local prefix, and carry-corrected result to fit signed int32; a different PE
partition can change whether an input is accepted. Compaction accepts full-range
int32 values because it sums counts, not values. Its reverse forwarding can
require O(P × N) record transfers and O(N) buffer space per PE.

The scan suite supports widths 1/2/3/4/8 and 8–256 slots per PE. The compaction
suite accepts widths 1–8 and 1–64 slots. Accepted parameter ranges do not imply
that every combination has been run on the SDK. Validation is for simulator
correctness; there are no hardware timing, throughput, or speedup results.

If compilation cannot find a C++20 compiler, select one with
`make CXX=clang++ test`. An arithmetic rejection means the input is outside the
scan's supported domain; reduce magnitudes or change the partition. For SDK
errors, check the VM and SDK paths and use a single width to reproduce the
failure. Preserve the run directory when a compile, timeout, or output check
fails. The [SDK run guide](docs/SDK_RUN.md) explains the logs and directory layout.

The SDK API background is documented in Cerebras'
[routing tutorial](https://sdk.cerebras.ai/csl/tutorials/gemv-06-routes-1) and
[SdkRuntime reference](https://sdk.cerebras.ai/api-docs/sdkruntime-api).
The repository does not bundle the SDK or its licensed assets.
