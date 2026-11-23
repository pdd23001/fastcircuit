# fastcircuit

### Write Qiskit circuits in Python, as you always have. Build them at native speed.

[![Release](https://img.shields.io/pypi/v/fastcircuit.svg?label=Release)](https://pypi.org/project/fastcircuit/)
[![Python](https://img.shields.io/pypi/pyversions/fastcircuit?label=Python&logo=python)](https://pypi.org/project/fastcircuit/)
[![Qiskit](https://img.shields.io/badge/Qiskit-2.6-6133BD?logo=Qiskit)](https://github.com/Qiskit/qiskit)
![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20macOS-informational)
[![CI](https://github.com/pdd23001/fastcircuit/actions/workflows/ci.yml/badge.svg)](https://github.com/pdd23001/fastcircuit/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://github.com/pdd23001/fastcircuit/blob/main/LICENSE.txt)

Wrap your Qiskit code in `with fastcircuit:`. That is the only change.
fastcircuit compiles it to native code that appends every gate through
[Qiskit's C API](https://quantum.cloud.ibm.com/docs/api/qiskit-c), and you get
back the same ordinary `QuantumCircuit`, instruction for instruction.

```python
from qiskit import QuantumCircuit
import fastcircuit


def layered(n, layers, theta):
    qc = QuantumCircuit(n)
    with fastcircuit:  # the only new line
        for layer in range(layers):
            for i in range(n):
                qc.rx(theta * layer, i)
            for i in range(n - 1):
                qc.cx(i, i + 1)
    qc.measure_all()  # plain Qiskit, before and after
    return qc
```

- **Native speed, Python syntax.** No new language, no type annotations, no C to
  write. On an Apple M3 Pro, building circuits of 10,000 to a million gates was
  37–105× faster than the same Python loop (about 100× for a million gates).
  Results depend on the machine and the circuit.
- **Exactly the same circuit.** Every instruction, parameter and exception
  matches what plain Python builds, down to the line number in error messages.
  (The one difference: integers are 64-bit, so overflow raises `OverflowError`.)
- **Plain Qiskit in, plain Qiskit out.** The result is a normal `QuantumCircuit`:
  transpile it, draw it, run it.
- **Never silently wrong.** Code fastcircuit cannot compile is rejected with the
  file and line. It never quietly falls back to slow Python.
- **Compiled once.** Compiled code is cached, so later runs load it in
  milliseconds. On most machines no C compiler is needed.

> **Status: alpha.** fastcircuit compiles a typed subset of Python: loops,
> conditionals, arithmetic and 42 standard gates. The API may change before 1.0.
> Small circuits gain little: the first compilation of a block takes tens of
> milliseconds, or a few hundred the first time the compiler is loaded from disk.

## Contents

- [Installation](#installation)
- [Your first compiled circuit](#your-first-compiled-circuit)
- [What you can write](#what-you-can-write)
- [Documentation](#documentation)
- [Compatibility and stability](#compatibility-and-stability)
- [Contribution guidelines](#contribution-guidelines)
- [Citation](#citation)
- [Changelog](#changelog)
- [License](#license)

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

## Your first compiled circuit

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

## What you can write

- Variables holding Python `int` and `float` values, `np.float64` and `np.int64`
  values, and 1-D or 2-D `float64` or `int64` NumPy arrays (`theta[layer, q]`,
  edge lists)
- `for ... in range(...)`, `if`/`elif`/`else`, local variables, arithmetic,
  comparisons, `math` and NumPy math functions, and array indexing
- 42 standard gates, `measure` and `reset`

Results match Python exactly, exceptions included: the same exception types, with
the line number in your code. The one difference is that an integer result
outside 64 bits raises `OverflowError`. The full list is in
[Supported syntax](https://github.com/pdd23001/fastcircuit/blob/main/docs/supported-syntax.md).

## Documentation

- [User guide](https://github.com/pdd23001/fastcircuit/blob/main/docs/guide.md):
  where blocks work, errors, the cache, environment variables,
  troubleshooting
- [Supported syntax](https://github.com/pdd23001/fastcircuit/blob/main/docs/supported-syntax.md):
  exactly what compiled code may contain
- [Platforms](https://github.com/pdd23001/fastcircuit/blob/main/docs/platforms.md):
  operating systems, CPUs, Python versions, and which backend each uses
- [Architecture](https://github.com/pdd23001/fastcircuit/blob/main/docs/architecture.md):
  how the compiler works, for contributors

## Compatibility and stability

fastcircuit follows [semantic versioning](https://semver.org/). Before 1.0, a
minor release may change the API; every change is listed in the changelog.
Qiskit's C API is still experimental, so each fastcircuit release supports one
Qiskit minor series and refuses others with a clear error. Details are in the
[compatibility policy](https://github.com/pdd23001/fastcircuit/blob/main/docs/compatibility.md).

## Contribution guidelines

Contributions are welcome. Please read the
[contributing guide](https://github.com/pdd23001/fastcircuit/blob/main/CONTRIBUTING.md),
which covers setting up a development environment, running the checks, and
opening a pull request. By taking part, you agree to uphold the
[code of conduct](https://github.com/pdd23001/fastcircuit/blob/main/CODE_OF_CONDUCT.md).

We use [GitHub issues](https://github.com/pdd23001/fastcircuit/issues) for bugs,
feature requests and questions. Please report security problems privately, as
described in [SECURITY.md](https://github.com/pdd23001/fastcircuit/blob/main/SECURITY.md).

## Citation

If you use fastcircuit in your work, please cite it using the metadata in
[CITATION.cff](https://github.com/pdd23001/fastcircuit/blob/main/CITATION.cff)
("Cite this repository" on GitHub).

## Changelog

Notable changes in each release are listed in the
[changelog](https://github.com/pdd23001/fastcircuit/blob/main/CHANGELOG.md) and on
the [releases page](https://github.com/pdd23001/fastcircuit/releases).

## License

[Apache License 2.0](https://github.com/pdd23001/fastcircuit/blob/main/LICENSE.txt).
