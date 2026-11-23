# fastcircuit

[![Release](https://img.shields.io/pypi/v/fastcircuit.svg?label=Release)](https://pypi.org/project/fastcircuit/)
[![Python](https://img.shields.io/pypi/pyversions/fastcircuit?label=Python&logo=python)](https://pypi.org/project/fastcircuit/)
[![Qiskit](https://img.shields.io/badge/Qiskit-2.6-6133BD?logo=Qiskit)](https://github.com/Qiskit/qiskit)
![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20macOS-informational)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://github.com/pdd23001/fastcircuit/blob/main/LICENSE.txt)

### Write Python. Run your Qiskit code at native speed.

[**Documentation**](https://pdd23001.github.io/fastcircuit/) |
[**PyPI**](https://pypi.org/project/fastcircuit/) |
[**Changelog**](https://github.com/pdd23001/fastcircuit/blob/main/CHANGELOG.md)

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/pdd23001/fastcircuit/main/docs/assets/benchmark-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/pdd23001/fastcircuit/main/docs/assets/benchmark-light.svg">
    <img alt="Bar chart: adding 996,866 gates takes 1.52 seconds in plain Python and 15.1 milliseconds with fastcircuit." src="https://raw.githubusercontent.com/pdd23001/fastcircuit/main/docs/assets/benchmark-light.svg">
  </picture>
</p>

<p align="center">
  <i>Adding 996,866 gates to a circuit on an Apple M3 Pro. <a href="#benchmarks">More benchmarks</a>.</i>
</p>

---

**Python is easy to write and slow to run:** A Python loop that adds a million
gates to a circuit takes 1.5 seconds, and almost all of that time goes to the
Python interpreter, not to adding the gates: the same gates added from native
code take 15 milliseconds. Qiskit's core is written in Rust for exactly this
reason, and its [C API](https://quantum.cloud.ibm.com/docs/api/qiskit-c) lets
compiled programs use Qiskit at native speed. But to use it, you have to leave
Python.

**fastcircuit gives you that speed without leaving Python:** Add one line,
`with fastcircuit:`, and the code inside it is compiled to machine code that
calls Qiskit's C API directly. You keep writing ordinary Python. It runs up to
100× faster, with exactly the same results.

```python
from qiskit import QuantumCircuit
import fastcircuit

n, layers, theta = 100, 1000, 0.25
qc = QuantumCircuit(n)

with fastcircuit:  # the only new line
    for layer in range(layers):
        for i in range(n):
            qc.rx(theta * layer, i)
        for i in range(n - 1):
            qc.cx(i, i + 1)

qc.measure_all()  # plain Qiskit, before and after
```

---

## Why fastcircuit?

- **Up to 100× faster:** Python loops that call Qiskit run as native machine
  code instead of interpreted bytecode. See the [benchmarks](#benchmarks).
- **One line to adopt:** No new language, no type annotations, no C. Wrap the
  slow part; everything around it stays plain Python and plain Qiskit.
- **Identical results:** Every value and every exception matches plain Python,
  down to the line number in the traceback, and the test suite checks this
  against real Qiskit on every change. Code fastcircuit cannot compile is
  rejected with its file and line, never quietly run as slow Python. (The one
  difference: integers are 64-bit, so overflow raises `OverflowError`.)
- **Works with the rest of Qiskit:** You get back an ordinary `QuantumCircuit`:
  transpile it, draw it, run it on a simulator or on hardware.
- **Nothing to set up:** `pip install fastcircuit`. On most machines no C
  compiler is needed, and compiled code is cached, so later runs start in
  milliseconds.

---

## Benchmarks

The same code run as plain Python and inside `with fastcircuit:`:

| Workload | Gates | Plain Python | fastcircuit | Speedup |
|---|---:|---:|---:|---:|
| Layers of `h`, `rx` and `cx` | 996,866 | 1.52 s | 15.1 ms | **101×** |
| Rotation angles read from a NumPy array | 225,000 | 377 ms | 4.5 ms | **84×** |
| Hardware-efficient ansatz (2-D angle array, `np.cos`) | 300,000 | 523 ms | 7.8 ms | **67×** |
| QFT-style controlled phases | 180,300 | 358 ms | 9.6 ms | **37×** |

Apple M3 Pro, Python 3.14, Qiskit 2.6.2, default (LLVM) backend; median of 5
runs, with every circuit checked instruction by instruction against the plain
Python result. The gain grows with the number of gates: about 9× at 300 gates,
and between 37× and 105× from 10,000 gates up in these workloads. Compiling a
block takes tens of milliseconds once; after that it is loaded from the cache.
Results depend on your machine and your code.

---

## What does it speed up?

fastcircuit speeds up **the Python you write around Qiskit**: the loops,
arithmetic and Qiskit calls that build your circuits, which Python otherwise runs
one instruction at a time. Today that means circuit construction.

It does not change Qiskit itself. The transpiler, the simulators and the rest of
Qiskit's core are already compiled Rust, and they run exactly as before on the
circuit you get back. If your time goes to building circuits in Python loops,
fastcircuit makes that faster; if it goes to `transpile` or to running circuits,
it does not.

> **Alpha:** The API may change before 1.0. fastcircuit is built for code that
> adds many gates in loops; for a handful of gates, plain Qiskit is just as fast.

---

## Contents

- [Installation](#installation)
- [Quick start](#quick-start)
- [What runs natively today](#what-runs-natively-today)
- [Documentation](#documentation)
- [Compatibility and stability](#compatibility-and-stability)
- [Contribution guidelines](#contribution-guidelines)
- [Citation](#citation)
- [Changelog](#changelog)
- [License](#license)

---

## Installation

```bash
pip install fastcircuit
python -m fastcircuit doctor     # checks the backend, Qiskit and the cache
```

fastcircuit needs Linux or macOS, Python 3.11 or newer, and Qiskit 2.6. On most
machines no C compiler is needed: the default backend compiles in-process with
LLVM, through [llvmlite](https://llvmlite.readthedocs.io/).

| Platform | No compiler needed | C compiler needed |
|---|---|---|
| macOS, Apple Silicon | Python 3.11+ | — |
| macOS, Intel | Python 3.11–3.13 | Python 3.14+ |
| Linux x86-64 and aarch64 (glibc 2.28+) | Python 3.11+ | — |
| Linux ppc64le and s390x | — | Python 3.11+ |

To work on fastcircuit itself, see the [contributing guide](https://github.com/pdd23001/fastcircuit/blob/main/CONTRIBUTING.md#setting-up-a-development-environment).

---

## Quick start

Put `with fastcircuit:` around your Qiskit code. The block appends to a circuit
created before it, and takes the types of its variables from their values:

```python
from qiskit import QuantumCircuit
import fastcircuit


def ghz(n):
    qc = QuantumCircuit(n)
    qc.h(0)
    with fastcircuit:
        for i in range(n - 1):
            qc.cx(i, i + 1)
    qc.measure_all()
    return qc


print(ghz(5))
```

Run it with plain `python`, import it from other modules, or use it in Jupyter,
with your imports in any order. In a script that contains blocks, only imports
and literal assignments may run before fastcircuit is first imported. The
[user guide](https://github.com/pdd23001/fastcircuit/blob/main/docs/guide.md#compile-a-block)
has the details.

---

## What runs natively today

- Variables holding Python `int` and `float` values, `np.float64` and `np.int64`
  values, and 1-D or 2-D `float64` or `int64` NumPy arrays (`theta[layer, q]`,
  edge lists)
- `for ... in range(...)`, `if`/`elif`/`else`, local variables, arithmetic,
  comparisons, `math` and NumPy math functions, and array indexing
- 42 standard gates, `measure` and `reset`

More of Qiskit is planned, starting with `Parameter` support.

Results match Python exactly, exceptions included: the same exception types, with
the line number in your code. The one difference is that an integer result
outside 64 bits raises `OverflowError`. The full list is in
[Supported syntax](https://github.com/pdd23001/fastcircuit/blob/main/docs/supported-syntax.md).

---

## Documentation

- [User guide](https://github.com/pdd23001/fastcircuit/blob/main/docs/guide.md):
  where blocks work, errors, the cache, environment variables,
  troubleshooting
- [Supported syntax](https://github.com/pdd23001/fastcircuit/blob/main/docs/supported-syntax.md):
  exactly what compiled code may contain
- [Platforms](https://github.com/pdd23001/fastcircuit/blob/main/docs/platforms.md):
  operating systems, CPUs, Python versions, and which backend each uses
- [Architecture](https://github.com/pdd23001/fastcircuit/blob/main/ARCHITECTURE.md):
  how the compiler works, for contributors

---

## Compatibility and stability

fastcircuit follows [semantic versioning](https://semver.org/). Before 1.0, a
minor release may change the API; every change is listed in the changelog.
Qiskit's C API is still experimental, so each fastcircuit release supports one
Qiskit minor series and refuses others with a clear error. Details are in the
[compatibility policy](https://github.com/pdd23001/fastcircuit/blob/main/docs/compatibility.md).

---

## Contribution guidelines

Contributions are welcome. Please read the
[contributing guide](https://github.com/pdd23001/fastcircuit/blob/main/CONTRIBUTING.md),
which covers setting up a development environment, running the checks, and
opening a pull request. By taking part, you agree to uphold the
[code of conduct](https://github.com/pdd23001/fastcircuit/blob/main/CODE_OF_CONDUCT.md).

We use [GitHub issues](https://github.com/pdd23001/fastcircuit/issues) for bugs,
feature requests and questions. Please report security problems privately, as
described in [SECURITY.md](https://github.com/pdd23001/fastcircuit/blob/main/SECURITY.md).

---

## Citation

If you use fastcircuit in your work, please cite it using the metadata in
[CITATION.cff](https://github.com/pdd23001/fastcircuit/blob/main/CITATION.cff)
("Cite this repository" on GitHub).

---

## Changelog

Notable changes in each release are listed in the
[changelog](https://github.com/pdd23001/fastcircuit/blob/main/CHANGELOG.md) and on
the [releases page](https://github.com/pdd23001/fastcircuit/releases).

---

## License

[Apache License 2.0](https://github.com/pdd23001/fastcircuit/blob/main/LICENSE.txt).
