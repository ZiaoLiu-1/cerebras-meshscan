# MeshScan design

This document specifies the preserved inclusive-scan baseline. The second
algorithm, MeshCompact stable threshold selection with reverse record scatter,
has its own [design and worked example](COMPACTION.md) and
[validation record](COMPACTION_VALIDATION.md).

## Problem

Given signed 32-bit values `x[0..n)`, produce the inclusive prefix scan
`y[i] = x[0] + ... + x[i]`. The experiment maps contiguous blocks to a
one-dimensional row of `P` processing elements.

The 32-bit contract matches the natural width of WSE inter-PE wavelets and the
element types exposed by the host runtime. The reference model uses checked
addition. An overflow is a failed input, not a wrapped or silently accepted
result; 64-bit transport is explicitly outside this implementation because it would require
a multi-word protocol.

For a chosen PE layout, every serial prefix, partition-local prefix, and
eastbound carry must fit in signed 32-bit range. This intentionally makes the
scan's accepted input domain layout-dependent: an input can have representable
global prefixes but still be rejected if reassociation makes a local prefix
overflow. Such a rejection is an unsupported input, not a device/oracle
correctness mismatch. A wider local accumulator can be evaluated as a later
extension.

## Implementation mapping

The C++ reference and CSL kernel implement the same mapping. The
[validation guide](VALIDATION.md) describes the checks at their boundary.

1. Split `n` values into balanced contiguous ranges. Earlier PEs receive one
   extra value when `n` is not divisible by `P`.
2. Each PE computes a local inclusive scan in local memory.
3. PE 0 begins with carry zero. Every later PE receives an accumulated total
   from its west neighbor.
4. A PE adds the incoming carry to each local prefix, then forwards
   `incoming + local_total` east.
5. Every PE completes its local send/receive path and explicitly unblocks its
   own command stream; the blocking host launch must finish across the whole PE
   rectangle before D2H begins.
6. The host copies results back and compares every valid element with the C++
   oracle.

The correctness invariant after PE `k` completes is: all output positions owned
by PEs `0..k` equal the serial inclusive scan, and the forwarded carry equals
the sum of every input owned by PEs `0..k`.

## Implementation boundary

- Device: Cerebras CSL, explicit local buffers, a west-to-east communication
  route, and task activation driven by data arrival.
- SDK host: Cerebras Python `SdkRuntime` for H2D, blocking launch and D2H.
- Mac orchestrator: C++ preflight and strict validation of raw device results;
  expected values are not supplied to the SDK host.
- Oracle: the existing C++20 implementation, invoked by the Python host through
  the schema-checking `tools/oracle_bridge.py` subprocess boundary. The host
  must not substitute a separate Python/NumPy scan and still call it the same
  oracle.
- Inspection: correctness in the fabric simulator; optional private, qualitative
  debugger inspection only when the accepted SDK terms permit it.

The implementation uses release-matched SDK 2.10.1 APIs: alternating colors
0/1 for neighbor links, explicitly initialized input/output queue 2, one-word
`@mov32` async DSD transfers and local task IDs 9/10 for finish/apply. Middle
PEs consume one color and inject another, preventing a through-route from
bypassing local carry application. No color rebinding occurs between launches.

## CPU trace viewer boundary

The project-native viewer is a presentation layer over the existing CPU model,
not another implementation of the algorithm:

```text
browser inputs
    -> loopback-only viewer/server.py
    -> tools/oracle_bridge.py
    -> build/meshscan-reference
    -> schema-validated CPU PeTrace JSON
    -> browser step rendering
```

`viewer/server.py` binds to `127.0.0.1`, serves an exact static-file allowlist,
and uses only the Python standard library. It accepts at most 256 signed int32
values and 1–16 visible logical PEs, then delegates the calculation to the C++
CLI through the existing bridge. The browser shows partition, parallel local
scan, one carry/application step per PE (including empty-PE forwarding), and
final verification. Empty input receives its own CPU-model fast-path
explanation. Browser JavaScript never computes a serial or partitioned scan.

The viewer uses no remote assets or third-party web dependencies and keeps the
model boundary visible as `CPU logical model — not WSE simulator`. It displays
C++ trace data; SDK traces use the separate official GUI.

`SdkRuntime` copy mode uses one `elem_per_pe` across the selected PE region.
For `n > 0`, the host allocates the compile-time `chunk_size` physical slots per
PE, with `ceil(n / P) <= chunk_size`, and sends each PE's explicit valid count.
Unused slots are deliberately filled with large nonzero poison values; the
device must ignore them. Validation requires both every valid output to match
and every padded slot to remain unchanged. The default chunk capacity is 16.
For `n == 0`, the host takes a fast path and does not rely on a zero-length
device copy.

Fabric propagation is asynchronous. Every PE must activate its local exit task
only after its final send/receive action and unblock its own runtime command
stream (the current SDK tutorials use `sys_mod.unblock_cmd_stream()`). PEs with
`valid_count == 0` still need an explicit forwarding/no-work completion path so
they cannot leave the launch blocked. The last physical PE's completion closes
the eastbound dependency, and the Python host must wait for the blocking launch
across the full rectangle before D2H. A per-PE `completed` word, incoming/local/
outgoing sums and exact outputs are copied back and checked. This witnesses
tested correctness, not hardware timing or a global fabric barrier.

## Validation cases

- PE counts: 1, 2, 3, 4, and 8.
- Lengths: 0, 1, `P - 1`, `P`, `P + 1`, and several non-multiples of `P`.
- Padded physical slots never affect valid output; `n == 0` uses the host fast
  path.
- Values: zeros, all positive, mixed signs, and fixed-seed random values.
- Repeated launches in one runtime session, including an empty PE after work.
- Per-PE completion proves D2H begins only after the eastbound chain has
  finished and every command stream, including an empty PE's, is unblocked.
- Exact element-by-element equality with the reference output.
- A failure path for unsupported size, layout, or arithmetic range.
- The CPU CLI rejects more than 4,096 logical PEs as a local allocation guard;
  this number is not a WSE hardware limit.
- A regression case where global prefixes fit but a partition-local prefix
  overflows, proving that the layout-dependent input contract is enforced.

## Run records

The SDK harness saves the exact input, C++ expectations, raw response, source
snapshot, compiler/runtime logs, SDK identity, and SHA-256 digests in a new
external run directory. A failed compile, timeout, cleanup failure, or mismatch
produces a failure record. `--prepare-only` records preparation without claiming
a device launch. See [SDK_RUN.md](SDK_RUN.md) for the directory layout.

## Algorithm limits

A row-wide carry chain is useful for checking routing and completion, but it
serializes inter-PE propagation. A tree scan or a two-pass design would require
a different protocol. This implementation contains no hardware timing or
performance comparison. It also has no 64-bit carry protocol, multi-row layout,
or unbounded input stream.
