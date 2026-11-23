# Architecture

This page is for contributors. It explains how a `with fastcircuit:` block
becomes native code, which module owns each step, and the rules every change must
keep.

## Pipeline

```text
import (_hooks.py)
      │
      ▼
_rewrite.py     find blocks, check what needs no values  ──►  UnsupportedSyntax
                (at import), replace each block with a call to _block.run
      │
      ▼  the first time a block runs with a given combination of types
_block.py       classify the values the block reads: the circuit, int/float/np.float64/array
                variables, and modules and builtins resolved by identity
      │
      ▼
_analysis.py    check the supported subset, scopes and types  ──►  UnsupportedSyntax
      │
      ▼
_ir.py          typed intermediate representation (frozen dataclasses)
      │
      ├────────────────────────────┐
      ▼                            ▼
_codegen_llvm.py  IR → LLVM IR    _codegen_c.py  IR → C extension source
_llvm.py          compile in      _build.py      compile with the system C
                  process, JIT                   compiler, import the module
      │                            │
      └──────────────┬─────────────┘
                     ▼
_cache.py       persistent per-user cache of object code / extension modules
                     │
                     ▼
_block.py       on every run: validate the values, call the native code with the
                circuit, which appends every gate to it
```

| Module | Responsibility |
|---|---|
| `_hooks.py` | When rewriting happens: the import hook, re-running the main script, patching modules that were already executing, IPython, and the `python -m fastcircuit run` fallback |
| `_rewrite.py` | Find `with fastcircuit:` blocks in a module's syntax tree, check what can be checked without values, and replace each block with a call to `_block.run` |
| `_block.py` | Blocks at run time: classify the values passed in, compile once per combination of types, validate the values and call the native code with the target circuit |
| `_source.py` | A block's location for diagnostics, and resolution of the names it reads the way Python would |
| `_analysis.py` | Accept or reject every AST node, infer each local's type, and produce the IR. Everything not explicitly accepted is rejected with a located diagnostic |
| `_ir.py` | IR node types. Both code generators consume only the IR |
| `_gates.py` | Gate method name → Qiskit C enum, number of parameters and qubits |
| `_codegen_llvm.py` | IR → LLVM IR text, via llvmlite's IR builder |
| `_llvm.py` | LLVM runtime: target setup, optimization, object emission, MCJIT loading, the Qiskit function table, the ctypes entry point |
| `_codegen_c.py` | IR → C source for a CPython extension module |
| `_build.py` | Find a C compiler, compile and link, import the extension |
| `_native.py` | Generate code for a block's IR on the chosen backend, load it from the cache or compile it, and validate values before native code sees them |
| `_fperror.py` | NumPy floating-point error reporting: compiled code calls it when an `np.float64` result is infinite, NaN or tiny, and it reports what NumPy would (warning, error or nothing) for the block's source line |
| `_npufunc.py` | NumPy math functions: finds the float64 routine (inner loop) each supported function runs, checks it against the function itself, and gives its address to compiled code |
| `_cache.py` | Cache keys, the on-disk layout, locking, atomic publication and checksums |
| `_compat.py` | Platform, Python and Qiskit checks, and the choice of backend |
| `errors.py` | Public exception types |
| `__main__.py` | `python -m fastcircuit doctor`, `cache` and `run` |

Blocks are rewritten in the source before Python compiles it, because a `with`
statement cannot skip its body. A block that was not rewritten raises
`BlockNotCompiledError` from `fastcircuit.__enter__`; it never runs as Python.
`_hooks.py` explains which situations can be rewritten.

The generated code appends to the circuit it is given, reading the circuit's
qubit and classical bit counts at run time.

## Backends

Both backends compile the **same IR** and must give **identical results**. The
test suite runs against each one.

**LLVM (default):** Compiles in-process with llvmlite (O2), with no C compiler or
Python headers. Qiskit's C functions are called through the function-pointer
table that Qiskit exports for Python extensions
(`qiskit._accelerate.capi.QK_FFI_CIRCUIT`). Slot numbers and gate enum values
are read from the headers Qiskit installs, and the table is passed to the kernel
at run time, so object files contain no process-specific addresses and can be
cached. libm functions are called under private symbol names, so LLVM cannot
replace them with different arithmetic (for example, `pow(x, 2.0)` → `x*x`).

**C:** Generates a CPython extension module and compiles it with the system C
compiler (`-O2 -fno-builtin`, warnings as errors in tests). It is used where
llvmlite has no wheels, and on request (`FASTCIRCUIT_BACKEND=c`).

Why these two: a C-only design needs a compiler on every user's machine. Numba
was measured and was slower to import and to load from its cache. Cython added
a runtime dependency and slower first calls without simplifying the semantics we
must enforce. llvmlite gives in-process compilation with full control over the
generated code.

### Kernel contract (LLVM)

```text
i32 kernel(ptr api, ptr circuit_data, i64 nq, i64 nc, ptr err, args...)
```

`int` arguments are `i64`, `float` arguments are `double`, and an array is a
`(ptr, i64)` pair. On failure, a kernel writes `[kind, a, b, site]` to `err` and
returns 1. The Python side turns that into the exception Python would have raised,
with the source line of `site`.

## Invariants

Changes must keep these. Most have dedicated tests.

1. **Same circuit as Python:** Same instructions in the same order, same
   exceptions. Differences are bugs, except for 64-bit integer overflow
   (`OverflowError`), which is documented.
2. **No silent fallback:** Anything the compiler does not understand is rejected
   at import, or the first time the block runs. It is never run as Python
   instead.
3. **Python semantics, exactly:** Checked int64 arithmetic; Python's `//` and `%`
   sign rules; CPython's float `%` and `//`; exact mixed int/float comparisons;
   CPython's `math` error rules; left-to-right evaluation.
4. **Validated inputs:** Values are type-checked before native code runs.
   Arrays are never copied and are bounds-checked on every access.
5. **Bounded and interruptible:** Hard limits on qubits, gates and iterations, and
   `PyErr_CheckSignals` every 16,384 iterations.
6. **The cache never serves stale or foreign code:** Keys cover the generated
   code, the toolchain and every version that affects meaning. Entries are
   checksummed, written atomically, compiled once under a lock, and loaded only
   from a directory owned by the user and not writable by others.
7. **Bump `CODEGEN_VERSION`** (`_version.py`) whenever generated code or its
   compilation changes meaning without changing the generated text.

## Adding a gate

1. Add it to `_gates.py` with its Qiskit C enum name and its parameter and qubit
   counts.
2. The component tests compare every gate in the table against the Python
   method, instruction for instruction. Run them on both backends.
3. Update the gate list in [supported-syntax.md](supported-syntax.md).

## Adding syntax

1. Accept it in `_analysis.py`, emitting existing IR nodes where possible.
2. If you need a new IR node, implement it in **both** code generators.
3. Add acceptance and rejection cases to `tests/unit/test_analysis.py`, a
   differential test against Python in `tests/component/test_features.py`, and a
   case to `tests/support/programs.py` (compiled with warnings as errors and under
   the sanitizers).
4. Document it in [supported-syntax.md](supported-syntax.md).
