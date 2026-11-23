# Supported syntax

fastcircuit compiles a typed subset of Python. The first time a
`with fastcircuit:` block runs, fastcircuit checks its body against this page.
Anything outside the subset raises `UnsupportedSyntax`, naming the file, line and
construct. Nothing falls back to running Python silently.

```text
UnsupportedSyntax: cannot compile 'build.<block line 8>' (circuits.py:9:9): unsupported statement (While)
  hint: supported statements are numeric assignments, gate/measure/reset calls on the
  circuit, 'for ... in range(...)', 'if'/'elif'/'else' and 'pass'; see ...
```

## Shape of a block

```python
def build(n, theta, angles):
    qc = QuantumCircuit(n)  # 1. create the circuit before the block
    with fastcircuit:
        ...  # 2. statements that append to it
    return qc  # 3. use it after the block, as usual
```

- The block appends to one `QuantumCircuit` created before it (the class itself,
  not a subclass). Code before and after the block is plain Python and can use
  any Qiskit feature.
- Variables assigned inside the block exist only inside it. Using the same name
  elsewhere in the enclosing function (or module) raises `UnsupportedSyntax` when
  the module is imported.
- A block cannot `return`, `yield`, `await`, or use `global` or `nonlocal`, and
  blocks cannot be nested.
- Blocks work in modules, scripts and notebook cells; see
  [Where blocks work](guide.md#where-blocks-work).

## Variables

Each variable the block reads takes its type from its value when the block runs:

| Value | Inside the block |
|---|---|
| Python `int` | checked 64-bit integer |
| Python `float` | 64-bit float |
| `np.float64` | 64-bit float with NumPy's rules (see [NumPy values](#numpy-values)) |
| `np.int64` | 64-bit integer with NumPy's rules |
| NumPy array: 1-D or 2-D, `float64` or `int64`, C-contiguous, aligned | read-only; never copied; elements are `np.float64` or `np.int64` |

- The block is compiled once for each combination of types it sees; `theta = 1`
  and `theta = 1.0` are two compilations.
- `bool` and other NumPy scalar types (`np.int32`, `np.float32`, ...) raise
  `TypeError` rather than being converted silently. Use `int(x)` or `float(x)`.
- Integers outside the 64-bit range raise `OverflowError`.
- Modules and functions are recognized by identity, so `import numpy as np`,
  `import math as m` and `from math import sqrt` all work.

## Statements

| Statement | Notes |
|---|---|
| `x = expr`, `x: int = expr`, `x: float = expr` | Each local variable keeps one type, and an annotation must match the value (`x: float = 1.0`, not `= 1`). A variable must be assigned on every path before it is used. |
| `x += expr` (and `-= *= /= //= %= **=`) | Same rules as `x = x op expr` |
| `for i in range(stop)`, `range(start, stop)`, `range(start, stop, step)` | Negative steps work. A step of zero raises `ValueError`, as in Python. The loop variable exists only inside its loop. |
| `if` / `elif` / `else` | Decided while the circuit is built (see below) |
| `qc.<gate>(...)`, `qc.measure(q, c)`, `qc.reset(q)` | See [Circuit operations](#circuit-operations) |
| `pass` | |

Not supported: `while`, `break`/`continue`, `try`, `with`, nested functions, calls
to your own helper functions, lists, tuples or dicts, and assigning to an array
element.

## Expressions

- **Arithmetic:** `+ - * / // %`, unary `+`/`-`, and `**` with a literal integer
  exponent from 0 to 1024.
- **Comparisons:** `< <= > >= == !=`, including chains (`0 <= i < n`) and mixed
  `int`/`float` comparisons, which are exact, as in Python (for `np.float64`, see
  [NumPy values](#numpy-values)).
- **Logic:** `and`, `or`, `not`, and conditional expressions (`a if cond else b`).
- **Arrays:** `angles[i]`, `theta[i, j]` and `theta[i][j]` (negative indices too),
  `len(a)` and `a.shape[k]`. Out-of-range indices raise `IndexError` with NumPy's
  message. Slices and whole rows (`theta[i]` on its own) are not supported.
- **Built-ins:** `abs`, `min`, `max`, `int`, `float`, `len`.
- **`math`:** `sqrt cbrt sin cos tan asin acos atan atan2 sinh cosh tanh asinh
  acosh atanh exp exp2 expm1 log log2 log10 log1p fabs copysign fmod degrees
  radians floor ceil trunc`, and the constants `math.pi`, `math.e`, `math.tau`.
  `math.log` takes one argument.
- **NumPy functions** of one argument: `sin cos tan arcsin arccos arctan sinh
  cosh tanh arcsinh arccosh arctanh exp exp2 expm1 log log2 log10 log1p sqrt cbrt
  square reciprocal fabs absolute (abs) negative positive floor ceil trunc rint
  sign deg2rad rad2deg degrees radians`; of two: `arctan2 hypot copysign fmod
  power maximum minimum fmax fmin logaddexp logaddexp2`. They return `np.float64`
  and give NumPy's exact results, because compiled code calls the same compiled
  routine NumPy runs. A call that NumPy would answer with an integer
  (`np.floor(3)`, `np.maximum(1, 2)`, all-int arguments) is rejected; pass a float.
- **NumPy constants:** `np.pi`, `np.e`.

## Circuit operations

42 standard gates, with qubit indices and angles given as expressions:

```text
h x y z s sdg t tdg sx sxdg id
rx ry rz p r u
cx cy cz swap ch cs csdg csx dcx ecr iswap
crx cry crz cp rxx ryy rzz rzx cu
ccx ccz cswap rccx rcccx
```

plus `qc.measure(qubit, clbit)` and `qc.reset(qubit)`. One call appends one
instruction. Broadcasting a gate over ranges or lists (`qc.h(range(n))`) is not
supported; write a loop instead.

## Building the circuit vs. running it

Python `if` and `for` in a block choose **which gates are appended** while the
circuit is built, exactly as when the code runs as Python.

Qiskit's quantum control flow, such as `qc.if_test`, `qc.while_loop` and
`qc.for_loop`, puts control flow *into* the circuit. It is rejected.
Build such circuits with ordinary Qiskit, or use fastcircuit for the parts without
quantum control flow and `compose` the results.

## Results match Python

A block appends the same instructions to the circuit, one for one, as the same
code run as plain Python. Every rule below is
covered by tests that compare against Python:

- `//` and `%` follow Python's sign rules. Float arithmetic, `math` functions and
  `int`/`float` conversions give bit-identical results.
- Division by zero, math domain errors, bad array indices, out-of-range qubits and
  duplicate qubits in one gate raise the same exception types as Python and
  Qiskit, with the line number in your source.
- **One difference:** Python integers grow without bound. Where a Python result
  would leave the 64-bit range, fastcircuit raises `OverflowError` instead.
- **NaN results** have the same sign bit as in Python, except when both operands
  of `+` or `*` are NaN: the hardware then returns one of them, and which one
  depends on how CPython itself was compiled.
- **Limits per call:** 1,000,000 qubits, 5,000,000 gates, 20,000,000 loop
  iterations. Ctrl-C interrupts a long build.

### NumPy values

Array elements (`angles[i]`) and `np.float64` variables are NumPy scalars, and
arithmetic on them follows NumPy's rules, as in Python:

- Any arithmetic involving an `np.float64` gives an `np.float64`.
- Division by zero, overflow and invalid operations give `inf` or `nan` instead
  of raising, and NumPy reports them according to `np.errstate`. By default
  that is a `RuntimeWarning` such as `divide by zero encountered in scalar
  divide`, attributed to the line in your source. `np.errstate(divide="raise")`
  raises `FloatingPointError`, and warning filters apply as usual.
- Comparing with an `int` converts the int to a float first, so
  `angles[i] == 2**53 + 1` can be true.
- `float(angles[i])` gives a Python float again, with Python's rules.
- Elements of an `int64` array are `np.int64`. They can index qubits, clbits and
  arrays, and bound `range`. Arithmetic with ints wraps around on overflow and
  `//` or `%` by zero gives 0, each with NumPy's `RuntimeWarning`; `/`, or
  arithmetic with a float, gives an `np.float64`. `int(e[k])` gives a Python int.
  `math.trunc` does not accept an `np.int64` (Python raises `TypeError`), and a
  variable cannot hold an `int` on one path and an `np.int64` on another.
- A variable can hold a float on one path and an `np.float64` on another
  (`total = 0.0`, then `total += angles[i]` in a loop); the compiled code tracks
  which it holds. Such a variable cannot be used with `min`, `max` or a
  conditional expression; convert it with `float(...)` first.

These checks make results trustworthy. They are not a sandbox: compile only
code you would run anyway.
