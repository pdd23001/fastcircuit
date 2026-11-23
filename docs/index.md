---
title: fastcircuit
description: Compile Qiskit circuit-building code in 'with fastcircuit:' blocks to native code.
hide:
  - navigation
  - toc
---

<div class="fc-hero" markdown>

<div class="fc-hero__text" markdown>

# Build Qiskit circuits at native speed.

<p class="fc-lead">Wrap your Qiskit code in <code>with fastcircuit:</code>. It compiles to
native code and gives back the same <code>QuantumCircuit</code>.</p>

<p class="fc-actions" markdown>
[Get started](guide.md){ .md-button .md-button--primary }
[Supported syntax](supported-syntax.md){ .md-button }
</p>

</div>

<div class="fc-hero__code" markdown>

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

</div>

</div>

## Why fastcircuit

<div class="fc-features" markdown>

<div class="fc-feature" markdown>

### Python syntax, native speed

No new language and no annotations. On an Apple M3 Pro, circuits of 10,000 to
1,000,000 gates were built 37 to 105 times faster than by the same Python loop.

</div>

<div class="fc-feature" markdown>

### The same circuit, exactly

Every instruction, parameter and exception matches plain Python, down to the
line number. One difference: integers are 64-bit, so overflow raises
`OverflowError`.

</div>

<div class="fc-feature" markdown>

### Never silently wrong

Code that fastcircuit cannot compile is rejected with its file and line. It
never falls back to running as slow Python.

</div>

<div class="fc-feature" markdown>

### Compiled once

Compiled code is cached across processes. On most machines no C compiler is
needed: the default backend compiles in-process with LLVM.

</div>

</div>

## Install

```bash
pip install fastcircuit
python -m fastcircuit doctor     # checks the backend, Qiskit and the cache
```

fastcircuit needs Linux or macOS, Python 3.11 or newer, and Qiskit 2.6.
[Platforms](platforms.md) lists the few setups that need a C compiler.

!!! note "Alpha"

    fastcircuit compiles a typed subset of Python: loops, conditionals,
    arithmetic, `math` and NumPy math functions, arrays, and 42 standard gates.
    The API may change before 1.0. Small circuits gain little, because the first
    compilation of a block takes tens of milliseconds.

## Documentation

<ul class="fc-index">
  <li><a href="guide/"><strong>User guide</strong><span>Where blocks work, errors, the cache and troubleshooting</span></a></li>
  <li><a href="supported-syntax/"><strong>Supported syntax</strong><span>Statements, types, gates, math and NumPy functions</span></a></li>
  <li><a href="platforms/"><strong>Platforms</strong><span>Operating systems, CPUs, Python versions and backends</span></a></li>
  <li><a href="compatibility/"><strong>Compatibility</strong><span>Supported Qiskit versions and API stability</span></a></li>
  <li><a href="architecture/"><strong>Architecture</strong><span>How the compiler works, for contributors</span></a></li>
</ul>
