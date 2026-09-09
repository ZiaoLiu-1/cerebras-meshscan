# Running the SDK suites

The orchestration tools run the native C++ reference on macOS and send CSL and
Python transport source to a Linux SDK installation in Lima. They target SDK
2.10.1 and WSE-3. The CPU build and tests do not need the SDK.

## Environment

Install the SDK separately under its own agreement. The scripts expect a Lima
VM named `cs_sdk`, with the SDK directory and external run directory mounted at
the same absolute paths on macOS and in the guest. The guest must be able to run
`cslc` and `cs_python`, including their container runtime. The SDK's Python
environment supplies NumPy and `cerebras.sdk.runtime`.

Default paths are:

- SDK wrappers: `~/.local/share/cerebras-lab/sdk/2.10.1/cs_sdk`
- Run storage: `~/.local/share/cerebras-lab/private-runs`

Use `--sdk`, `--vm`, and `--private-root` to select an existing installation.
The scripts do not install software or start the VM. Keep SDK and run paths
free of whitespace: the SDK container wrappers have problems with such paths.
The project source path itself may contain spaces because it is staged before
execution. Keep at least 4 GiB of free space for the run.

## Run a suite

From the repository root, after configuring the environment:

```bash
make all
limactl start cs_sdk
python3 -B tools/run_simulator.py --widths 2 --random-cases 2
python3 -B tools/run_compaction.py --widths 2 --random-cases 0
```

For the full default matrices, use `make simulator` and
`make simulator-compaction`. Scan uses widths 1/2/3/4/8, 16 slots per PE, and
16 random cases per layout. Compaction uses the same widths and slots with
eight random cases per layout. Both add fixed boundary cases and repeated
inputs. These defaults describe requested coverage, not a completed run.

The following command stages a scan suite without invoking Lima or the SDK:

```bash
python3 -B tools/run_simulator.py --prepare-only --widths 2 --random-cases 0
```

Its result is `prepared-not-executed` with zero verified device launches.
The C++ oracle must already be built. `--run-dir` optionally names a new direct
child of `--private-root`; existing directories are never reused.

## Files and validation

Each suite creates a fresh directory and prints `PRIVATE_RUN` followed by its
path. The layout is:

```text
run/
  source/              source snapshot used by this run
  oracles/p2.json      C++ expectations, outside the SDK working directory
  p2/
    input.json         packed input and valid counts
    layout.csl         staged device source
    pe.csl
    ...                staged Python contract and transport
    out/               compiler output
    compile.log
    runtime.log
    device.json        raw readback
  validation.json      written after successful validation or preparation
```

Additional widths get their own `pN/` directory. The SDK binds that layout's
working directory, which contains input and transport source but no oracle
answer file. This is a data-separation convention, not an adversarial sandbox.

The report records source, input, oracle, and output digests. A staged-source or
input/oracle change, compiler failure, timeout, cleanup error, or mismatch fails
the run. Failures write `failure.json`; they do not produce a passed report.
The default timeout is 900 seconds per SDK command, configurable from 30 to
3600 seconds with `--timeout-seconds`.

Raw records and generated artifacts stay outside Git. A saved result applies
to its recorded source snapshot. A later source edit does not update the old
result or establish a new simulator pass.

## Troubleshooting

- **Missing SDK or Lima:** check `--sdk`, confirm it contains `cslc`, and verify
  that `limactl` is on the macOS PATH. The cloud inference Python package does
  not supply this compiler/runtime.
- **VM stopped or files missing in the guest:** start the configured VM and
  check its mounts. Host and guest paths must match.
- **Whitespace error:** choose an external SDK/run path without spaces.
- **Low disk space:** free space outside the run or choose another mounted run
  location. Preserve failed run records while diagnosing the problem.
- **Compile/runtime failure:** read the corresponding log. Reproduce with one
  width and fewer random cases before changing the kernel.
- **Overflow rejection:** the scan prechecks serial and partition-local int32
  arithmetic. The same values may be valid for one layout and rejected for
  another; do not remove the precheck.
- **Readback mismatch:** compare the exact input, raw response, and source
  snapshot. Padding and completion failures are correctness failures too.

After finishing all SDK work, stop any GUI processes, then run
`limactl stop cs_sdk`. See [SDK_GUI.md](SDK_GUI.md) for trace inspection.
