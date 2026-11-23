# Compatibility policy

## Qiskit

fastcircuit calls Qiskit's C API, and Qiskit documents that API as experimental:
it "might change between minor version releases". Each fastcircuit release
therefore supports **one Qiskit minor series**, the one its full test suite ran
against:

| fastcircuit | Qiskit |
|---|---|
| 0.2.x | 2.6.x |

The dependency is pinned (`qiskit>=2.6.0,<2.7`). As a second line of defence,
fastcircuit checks the installed Qiskit version before compiling a block and raises
`IncompatibleEnvironmentError` for any other minor series. To experiment
anyway, set `FASTCIRCUIT_ALLOW_UNTESTED_QISKIT=1`. If the C API changed, that can
produce wrong circuits or crash the process, so never use it in production.

A scheduled CI job builds against the newest Qiskit, pre-releases included, so
an incompatible change is found before a new minor release ships.

**When Qiskit declares the C API stable** (planned upstream for Qiskit 2.7),
fastcircuit will widen its Qiskit range to whatever the stability guarantee
covers, drop the exact-minor check, and test the oldest and newest supported
Qiskit in CI. That change will be announced in the [changelog](https://github.com/pdd23001/fastcircuit/blob/main/CHANGELOG.md).

## Python

CPython 3.11, 3.12, 3.13 and 3.14 on Linux and macOS ([Platforms](platforms.md)).

- A new CPython version is supported once Qiskit, NumPy and llvmlite publish
  wheels for it and the full test suite passes.
- A CPython version is dropped no earlier than its upstream end-of-life.
- Free-threaded builds and PyPy are not supported. The generated code relies on
  the GIL and CPython's C API.

## Other dependencies

| Package | Range | Notes |
|---|---|---|
| NumPy | `>=2,<3` | |
| llvmlite | `0.50.x`; `0.45.x` on Intel macOS (Python ≤ 3.13) | Installed only where it publishes wheels; elsewhere the C backend is used |

## fastcircuit's own API

fastcircuit is **alpha** (`0.x`). Until 1.0:

- The public API is what `fastcircuit.__all__` exports, plus the attributes
  documented in the [user guide](guide.md). Modules whose names start with `_`
  are internal and can change at any time.
- A minor release (`0.3`) may change the public API. Every change is listed in
  the [changelog](https://github.com/pdd23001/fastcircuit/blob/main/CHANGELOG.md), and when it is practical, a release first
  deprecates the old behavior with a `DeprecationWarning`.
- Patch releases (`0.2.1`) only fix bugs.
- Accepting more syntax is not a breaking change. Rejecting syntax that used to
  compile is, unless that syntax used to give results that differ from Python;
  that is a bug fix.

## Cache

Cache entries are keyed by the fastcircuit, Python and Qiskit versions, the
generated code and the backend's toolchain. After any upgrade, functions
recompile on their first call instead of loading stale code. Old entries are
never loaded; remove them with `python -m fastcircuit cache clear`.
