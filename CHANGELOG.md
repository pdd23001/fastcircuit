# Changelog

All notable changes and releases of fastcircuit are listed here.

## v0.1.0

<p class="fc-release-date">November 23, 2026</p>

First public release.

- `with fastcircuit:` compiles a block of Qiskit code, written in a typed subset
  of Python (loops, conditionals, local variables, arithmetic and `math`), to
  native code that appends every gate to an ordinary `qiskit.QuantumCircuit`
  through Qiskit's C API. Types come from run-time values, and each combination
  is compiled once. Blocks work in scripts run with plain `python`, with imports
  in any order (the script is re-run with its blocks compiled when only imports
  and literal assignments ran before fastcircuit was first imported), in
  imported modules, and in IPython and Jupyter cells.
  A block that cannot be rewritten raises `BlockNotCompiledError` instead of
  running as Python.
- 42 standard gates, `measure` and `reset`.
- 30 `math` functions and 47 NumPy math functions (`np.sin`, `np.arctan2`, ...),
  however they are imported (`from math import sqrt` works). NumPy functions
  run NumPy's own compiled routines, so results match NumPy on the same machine.
- `np.float64` values, including array elements, follow NumPy's rules: division
  by zero and overflow give `inf`/`nan` with NumPy's `RuntimeWarning` (or as
  `np.errstate` says) instead of raising.
- 1-D and 2-D `float64` and `int64` arrays (`theta[layer, q]`, `theta[layer][q]`,
  `a.shape[k]`); `int64` elements are `np.int64`, index qubits, and wrap on
  overflow with NumPy's warning.
- Default LLVM backend (llvmlite), with no C compiler needed, plus a C backend
  for platforms without llvmlite wheels, selectable with `FASTCIRCUIT_BACKEND`.
- Results identical to the same code run as Python, including exception types
  and source lines. Checked 64-bit integer arithmetic.
- Persistent, process-safe compilation cache, and `python -m fastcircuit cache`.
- `python -m fastcircuit doctor` environment check.
- Support for Linux and macOS, CPython 3.11–3.14, Qiskit 2.6.x.
