# Platforms and backends

fastcircuit supports Linux and macOS.

fastcircuit has two backends that compile the same checked intermediate
representation and pass the same tests:

- **LLVM (default):** compiles in-process with [llvmlite](https://llvmlite.readthedocs.io/).
  No C compiler or Python headers are needed.
- **C:** generates C and compiles it with the system C compiler. Needs a C11 compiler
  and the Python development headers.

pip installs llvmlite only where llvmlite publishes wheels (markers in
`pyproject.toml`). fastcircuit then picks the backend automatically:
`FASTCIRCUIT_BACKEND` if set; otherwise LLVM when llvmlite is installed; otherwise C.
`python -m fastcircuit doctor` prints the choice and the reason.

| Platform | Python | Backend | Needs a C compiler? |
|---|---|---|---|
| macOS arm64 (Apple Silicon) | 3.11+ | LLVM (llvmlite 0.50) | no |
| macOS x86-64 (Intel) | 3.11–3.13 | LLVM (llvmlite 0.45, the last release with Intel wheels) | no |
| macOS x86-64 (Intel) | 3.14+ | C | yes (`xcode-select --install`) |
| Linux x86-64, glibc 2.28+ | 3.11+ | LLVM (llvmlite 0.50) | no |
| Linux aarch64, glibc 2.28+ | 3.11+ | LLVM (llvmlite 0.50) | no |
| Linux ppc64le / s390x | 3.11+ | C | yes |

Every platform above is tested on both backends, except ppc64le and s390x, which
are not tested.

Not supported:
- **Python 3.10 and older:** Python 3.10 reached end-of-life in October 2026.
- **Alpine/musl and glibc older than 2.28:** Qiskit publishes no wheel for them.
- **32-bit platforms, PyPy, and free-threaded CPython:** Generated code relies on the
  GIL and CPython's C API.

## Notes per platform

- **Intel Macs:** llvmlite 0.46 dropped Intel macOS wheels. fastcircuit uses
  llvmlite 0.45 (bundling LLVM 20 instead of 22) up to Python 3.13, the newest
  Python it supports. On Python 3.14+ pip does not install llvmlite there, and
  fastcircuit uses the C backend automatically.
- **ppc64le / s390x** use the C backend. llvmlite has no wheels there, and the LLVM
  backend's calling-convention details (64-bit pointers, a widened 8-bit gate
  argument) are verified only on x86-64 and arm64. The C compiler handles both
  platforms' conventions itself.

## Results are bit-identical across backends

Both backends call the same libm functions as CPython. The C backend compiles
with `-fno-builtin`, and the LLVM backend declares libm functions under private
names, so neither compiler can substitute different arithmetic (for example,
rewriting `pow(x, 2.0)` as `x*x`, which differs from x86-64 macOS libm in the last
bit). The feature tests compare float results bit for bit against Python.
