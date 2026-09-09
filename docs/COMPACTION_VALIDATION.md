# MeshCompact validation

MeshCompact is checked against a C++ serial stable filter and a separate
partitioned packet-routing model. The SDK host transfers inputs and reads
results; it does not select or reorder records to manufacture the answer.

Run the CPU and host tests with:

```bash
make test-compaction
make sanitize
```

For SDK execution, use `make simulator-compaction` in the configured environment
or select a smaller layout as described in [SDK_RUN.md](SDK_RUN.md).

## What must match

Each SDK response must contain the exact selected values and original indices,
with increasing indices and dense output ownership. Per-PE metadata must match
the C++ model:

- Selected count, exclusive offset, and inclusive count prefix.
- Output count and received/forwarded record counts.
- `local_count + received_count = output_count + forwarded_count`.
- `completed = 1` and `protocol_error = 0`.
- Unchanged input, including poison padding, and untouched unused output slots.

The host also verifies case names, schema, integer types, dimensions, and oracle
input identity. A nonzero protocol error fails the run, but a zero flag alone
does not prove correctness: arbitrary payload corruption still requires exact
comparison with the reference.

## Cases

The suite covers empty inputs, none/all selected, predicate equality, duplicate
values, front-only and tail-only selection, uneven partitions, capacity
boundaries, and full-range int32 values. Fixed-seed random cases extend those
examples. Known inputs are repeated after other work, including an empty launch,
to detect stale output or protocol state.

Every compaction case, including an empty input, uses a device launch. There is
no host empty-input shortcut. Repeated cases count as launches, not unique
vectors.

A useful long-packet case is P8/C64 with `[-1] × 256 + [9] × 256` and threshold
zero. Its CPU model predicts a 256-record packet at the central return link.
Follow it with empty input and an all-selected batch in the same session to
exercise buffer reuse. This is a correctness stress case, not a throughput
measurement.

## Records and scope

Run records bind exact source, input, oracle, output, and log identities. Oracle
files sit in `oracles/`, outside each SDK-bound `pN/` directory. The harness
checks that the input/oracle records and executable do not change during
execution. Saved results apply to the source snapshots they name.

The interface accepts 1–8 PEs and 1–64 slots per PE. That bound does not imply
coverage of every combination. The algorithm has a sequential count chain and
worst-case O(P × N) record forwarding; no hardware latency, throughput, speedup,
or production deployment is established by simulator correctness tests.
