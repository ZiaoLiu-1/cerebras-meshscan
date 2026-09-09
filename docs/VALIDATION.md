# Validation

The reference tests run entirely on the CPU. SDK runs compile and execute CSL
in the WSE-3 fabric simulator, then compare readback with the C++ reference.
The browser viewer displays CPU traces. These are separate execution paths;
none supplies real-hardware timing or throughput measurements.

## CPU and host checks

```bash
make test
make sanitize
```

The C++ scan suite compares serial and partitioned inclusive scans, including
uneven blocks, empty PEs, random inputs, and int32 overflow. The compaction
suite compares distributed packing with a serial stable filter and checks
output ownership and record conservation.

Python tests exercise CLI parsing, JSON schemas, packed layouts, oracle
identity, padding, completion, and runtime cleanup. Fault-injection runtimes
and synthetic readbacks are test fixtures; they are never counted as SDK runs.
The suite also checks tutorial examples and the loopback viewer. Node.js checks
browser syntax when available. `make sanitize` runs both C++ suites with
AddressSanitizer and UndefinedBehaviorSanitizer.

## Scan checks on SDK readback

For each nonempty case, the host prechecks the values with the native C++
reference before H2D. It then validates:

- Exact logical output and unchanged padding.
- Every PE's incoming carry, local total, and outgoing carry.
- A completion marker from every PE, including empty PEs.
- The layout, case names, data types, and one reused runtime session.
- A complete oracle trace belonging to the same input and PE partition.

Whole empty inputs use a host fast path. They are listed separately from device
launches. Overflow cases must fail before device execution. Repeating a known
input after unrelated work checks state reset; repeated inputs are not unique
vectors.

MeshCompact has a separate [validation contract](COMPACTION_VALIDATION.md)
because it must also check stable indices and packet routing.

## Reproducing a run

See [SDK_RUN.md](SDK_RUN.md) for environment setup and commands. Each run saves
its input, source snapshot, C++ expectations, raw readback, compiler/runtime
logs, and hashes outside the repository. `validation.json` states the outcome
and verified launch count. `failure.json` records failures. Preparation alone
is labeled `prepared-not-executed`.

Historical SDK runs belong to their recorded source versions. The repository
contains the tools and checks needed to produce another record; it does not
bundle SDK binaries, traces, or raw simulator output. Interface limits are
not a claim that every width/capacity combination has been exercised.
