# MeshScan CSL kernel

`layout.csl` places a row of 1–8 PEs; `pe.csl` scans each contiguous local
partition, consumes one carry from the west, adds it to valid local prefixes,
then forwards the accumulated total east. Alternating link colors prevent a
middle PE from routing a carry past its own local computation. Empty PEs still
receive and forward a carry. Padding is untouched.

## Host contract

Compile-time parameters: `width` (1–8) and `chunk_size` (1–1024 physical i32
slots per PE). The standard harness uses widths 1/2/3/4/8 and 16 slots per PE;
allowed bounds alone do not establish simulator coverage. Compile for WSE-3; the source's WSE-2 output-DSD
branch follows the release API but is not a claim of WSE-2 validation.

| Export | Elements per PE | Direction / purpose |
| --- | ---: | --- |
| `values` | `chunk_size` | H2D input; D2H in-place inclusive-scan output |
| `valid_count` | 1 | H2D number of valid slots; 0 is allowed |
| `incoming_carry` | 1 | D2H diagnostic: total west of this PE |
| `local_total` | 1 | D2H diagnostic: sum before applying the west carry |
| `outgoing_carry` | 1 | D2H diagnostic: total through this PE |
| `completed` | 1 | D2H diagnostic: 1 immediately before local unblock |
| `compute` | — | Host-launched no-argument function |

All data exports use signed int32. The host must validate counts, capacity,
and every global/local prefix plus carry correction with the checked C++
oracle **before H2D**. The kernel does not implement an overflow protocol or
device-side input validation. Each repeated launch requires fresh H2D values
and counts; internal metadata is reset by `compute`.

Example compile command, from a private run directory containing both CSL files:

```sh
cslc --arch=wse3 layout.csl --fabric-dims=11,3 --fabric-offsets=4,1 \
  --params=width:2,chunk_size:16 -o out --memcpy --channels 1 \
  --warnings-as-errors --out-routes
```

Use `width + 9` for the fabric x dimension with this one-channel configuration.
Do not store compiler output, simulator artifacts, SDK assets, or credentials in
the repository. The validation harness records the source identity and outcome of each run.

The SDK API patterns were checked against the installed 2.10.1 routes tutorial
and alternating-color GEMV layout; the scan algorithm, valid-count handling,
per-launch metadata, and host contract are specific to MeshScan. The routing API background is available in the official
[routes tutorial](https://sdk.cerebras.ai/csl/tutorials/gemv-06-routes-1).
Simulator validation checks the tested inputs and supplies no hardware
throughput measurements.
