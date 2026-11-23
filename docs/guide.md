# User guide

## Install

```bash
python -m pip install fastcircuit
python -m fastcircuit doctor
```

`doctor` reports the backend in use and why, and checks Qiskit and the cache
directory. On most machines nothing else is needed. [Platforms](platforms.md)
lists the few setups that need a C compiler.

## Compile a block

Wrap your Qiskit code in `with fastcircuit:`:

```python
# circuits.py
from qiskit import QuantumCircuit
import fastcircuit


def layered(n, layers, theta):
    qc = QuantumCircuit(n)
    qc.h(range(n))  # plain Python, as before
    with fastcircuit:  # compiled
        for layer in range(layers):
            for i in range(n - 1):
                qc.cx(i, i + 1)
                qc.rz(theta * layer, i)
    qc.measure_all()  # plain Python again
    return qc
```

```python
from circuits import layered

qc = layered(100, 1000, 0.25)  # an ordinary qiskit.QuantumCircuit
```

The result is a normal `QuantumCircuit`. Transpile it, draw it, compose it or run
it as you would any other circuit.

The block appends to a circuit created before it, so the surrounding code can use
any Qiskit feature. Types come from the values the block sees at run time. The
block is compiled the first time it runs with a given combination of types (in
the example, `theta` as an `int` and as a `float` are two compilations), then
reused. Compiled code is also stored in a persistent [cache](#the-cache), so
later processes skip the compilation.

Inside the block you can write the statements, expressions and gates listed in
[Supported syntax](supported-syntax.md). The block can read:

- exactly one `QuantumCircuit`, the one it calls methods on (not a subclass);
- variables holding Python `int` and `float` values, `np.float64` and `np.int64`
  values, and 1-D or 2-D `float64` or `int64` NumPy arrays;
- modules and builtins such as `math`, `np` and `range`.

Variables assigned inside the block belong to the block. Using the same name
anywhere else in the enclosing function (or module, for a block at top level)
is an error, because the block cannot
change the surrounding code's variables. A block cannot `return`, `yield`,
`await` or use `global` or `nonlocal`, and blocks cannot be nested.

### Where blocks work

`with` cannot skip its own body, so fastcircuit rewrites blocks in the source
before they run, as pytest does for `assert` statements.

**Scripts.** Run them as usual, with `python script.py`. Imports can come in any
order. The one rule, for a script that contains blocks: before fastcircuit is
first imported, directly or through one of your modules, the script may only
import modules and assign literal values.

```python
"""My experiment."""

import circuits  # imports fastcircuit itself: fine
import numpy as np
import fastcircuit

SHOTS = 1000  # a literal value: fine anywhere
print("starting")  # fine here, after the imports
```

When fastcircuit is first imported, it runs the script again from the top with
its blocks compiled, and the program ends when that run ends. If the import
happened inside one of your modules, that module is first finished the same way.
The lines that ran before the import therefore run twice, which is why they may
only be imports (a repeated import finds the module already loaded and does
nothing), literal assignments, and `if`/`try` statements made of those, such as
`try: import scipy` / `except ImportError: ...`. Output, exit codes and error
tracebacks are what they would be without fastcircuit. If anything else comes
first, the first block that runs raises `BlockNotCompiledError`, naming the line
to move.

**Modules.** Blocks in any module you import work, whether or not the importing
file uses fastcircuit. The exception: a module cannot run its own blocks while it
is still being imported, so a module that imports fastcircuit itself cannot have
a block at its top level, or call its block functions at its top level.

**Notebooks.** In IPython, Jupyter, VS Code notebooks and Colab, blocks work in
cells run after `import fastcircuit` (or `%load_ext fastcircuit`), but not in
the cell that imports it. The plain `python` interactive prompt is not supported.

For a script that must run other code before fastcircuit is imported, start it
with `python -m fastcircuit run script.py [args]`
instead. That rewrites the script before it starts, so nothing runs twice.

A block that was not rewritten never runs as plain Python: it raises
`BlockNotCompiledError`, which says why and what to do.

Rewritten modules are not stored as `.pyc` files, so a module containing blocks
is parsed every time it is imported. Other modules imported after fastcircuit
are only scanned for the word `fastcircuit`.

## When the work happens

| When | What happens | Typical cost |
|---|---|---|
| Import (`import circuits`) | Blocks found and rewritten. A misplaced block, an assignment to an outside variable or a `return` in a block raises `UnsupportedSyntax` here. | about the cost of parsing the module |
| First run of a block with given types | Checked against the supported subset (unsupported code raises `UnsupportedSyntax` here), translated, then loaded from the persistent cache or compiled and stored | LLVM: about 20 ms from the cache, 30–50 ms to compile (a few hundred when LLVM is first read from disk after a reboot). C: about 3 ms from the cache, a few hundred to compile |
| Every run | Variables checked in Python, then native code appends every gate through Qiskit's C API | microseconds plus the gates themselves |

Compilation pays off for circuits with many gates built in Python loops. For a
circuit of a handful of gates built once, plain Qiskit is just as good.

## Inspecting a block

`with fastcircuit.block(debug=True):` prints, to stderr, each compilation of the
block: the types it was specialized for, the backend, how long each step took,
and whether the code came from the cache. For the example above, in a first and
a second process:

```text
fastcircuit: block /home/me/circuits.py:8 (layered) specialized for (layers: int, n: int, theta: float), llvm backend
  analysis 0.1 ms, codegen 17.4 ms, build 13.5 ms, load 0.6 ms; total 32.8 ms; code from: compiled
fastcircuit: block /home/me/circuits.py:8 (layered) specialized for (layers: int, n: int, theta: float), llvm backend
  analysis 0.1 ms, codegen 17.6 ms, build -, load 1.6 ms; total 19.4 ms; code from: persistent cache
```

The generated code is kept in the block's cache entry (`kernel.ll` for the LLVM
backend, `source.c` for the C backend); `python -m fastcircuit cache list` shows
the entries.

## Errors

A block raises the same exception types as the same code in plain Python, with
the line in your source:

| Situation | Exception |
|---|---|
| Unsupported construct, misplaced block | `UnsupportedSyntax` (a `ValueError`), at import or on the first run |
| No circuit, several circuits, or a variable of another type (`bool`, `list`, other NumPy scalar or array types) | `TypeError` |
| Qubit index out of range | `QubitIndexError` (a Qiskit `CircuitError` and an `IndexError`) |
| Same qubit twice in one gate | `DuplicateQubitError` (a Qiskit `CircuitError` and a `ValueError`) |
| Division by zero, `range` step of zero, math domain error | `ZeroDivisionError`, `ValueError`, as in Python |
| Division by zero or overflow with `np.float64` values (array elements) | `inf`/`nan` and a `RuntimeWarning`, or as `np.errstate` says, as in Python |
| Integer result outside 64 bits | `OverflowError` |
| Ctrl-C during a long build | `KeyboardInterrupt` |
| A block that ran without being rewritten | `BlockNotCompiledError`, saying why |

Setup problems raise subclasses of `fastcircuit.FastCircuitError`:
`IncompatibleEnvironmentError`, `CompilerNotFoundError` (C backend only),
`NativeBuildError` and `CacheError`. The message says what to install or change.

## The cache

Compiled code is cached per user, so later processes skip compilation:

| Platform | Default location |
|---|---|
| macOS | `~/Library/Caches/fastcircuit` |
| Linux | `$XDG_CACHE_HOME/fastcircuit` (usually `~/.cache/fastcircuit`) |

Writes are atomic, and when several processes compile the same block at the
same time, only one compiles. Damaged entries are detected by checksum and
rebuilt. A cache entry is keyed by the generated code, the backend's toolchain,
and the Python, Qiskit and fastcircuit versions, so upgrading any of them
recompiles instead of loading stale code. The cache directory must belong to you
and must not be writable by others; fastcircuit refuses to load from one that is.

```bash
python -m fastcircuit cache info    # location, size, number of entries
python -m fastcircuit cache list
python -m fastcircuit cache clear
python -m fastcircuit cache path
```

## Environment variables

| Variable | Effect |
|---|---|
| `FASTCIRCUIT_BACKEND` | `llvm` or `c`. Default: `llvm` if llvmlite is installed, otherwise `c` |
| `FASTCIRCUIT_CACHE_DIR` | cache location |
| `FASTCIRCUIT_NO_CACHE=1` | compile in every process and store nothing |
| `FASTCIRCUIT_EXTRA_CFLAGS` | extra compiler flags for the C backend (for example sanitizers) |
| `FASTCIRCUIT_ALLOW_UNTESTED_QISKIT=1` | try a Qiskit minor version this release was not tested with (see [Compatibility](compatibility.md)) |

## Troubleshooting

Start with `python -m fastcircuit doctor`; it names the failing check.

- **`IncompatibleEnvironmentError: fastcircuit is tested with Qiskit 2.6.x`.**
  Install the Qiskit series named in the message. See
  [Compatibility](compatibility.md).
- **`CompilerNotFoundError`.** You are on the C backend (doctor says why). On
  macOS run `xcode-select --install`. On Linux install `gcc` or `clang` and the
  Python development headers (`python3-dev` / `python3-devel`).
- **`BlockNotCompiledError`.** The block ran without being rewritten; the
  message says why and what to change. See [Where blocks work](#where-blocks-work).
- **First run slow in every process.** With `fastcircuit.block(debug=True)`,
  check that the second process says `code from: persistent cache`. If not, check
  `FASTCIRCUIT_NO_CACHE` and `python -m fastcircuit cache info`.

## Trust

Compiled code runs natively inside your Python process, with the same rights as
the Python code it replaces. fastcircuit's checks make results match Python;
they are not a sandbox. Compile only code you would run anyway. fastcircuit never
sends your source code or compiled code anywhere.
