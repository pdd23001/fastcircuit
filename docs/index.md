---
title: fastcircuit
description: Write Python. Run your Qiskit code at native speed.
hide:
  - navigation
  - toc
---

<div class="fc-hero" markdown>

<div class="fc-hero__text" markdown>

# Write Python. Run your Qiskit code at native speed.

<p class="fc-lead">Python is easy to write and slow to run. Add one line,
<code>with fastcircuit:</code>, and your Qiskit code runs as machine code instead.</p>

<p class="fc-actions" markdown>
[Get started](guide.md){ .md-button .md-button--primary }
[See the benchmarks](#benchmarks){ .md-button }
</p>

</div>

<div class="fc-hero__code" markdown>

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

</div>

</div>

## Python is the bottleneck

<p class="fc-section-lead">The same 996,866 gates, added two ways.</p>

<div class="fc-race" role="img" aria-label="Adding 996,866 gates: 1.52 seconds in a plain Python loop, 15 milliseconds with fastcircuit">
  <div class="fc-race__row">
    <div class="fc-race__label">Plain Python loop</div>
    <div class="fc-race__value">1.52 s</div>
    <div class="fc-race__bar" style="--fc-w: 100%; --fc-t: 1520ms"></div>
  </div>
  <div class="fc-race__row fc-race__row--fast">
    <div class="fc-race__label"><code>with fastcircuit:</code></div>
    <div class="fc-race__value">15 ms</div>
    <div class="fc-race__bar" style="--fc-w: 1%; --fc-t: 15ms"></div>
  </div>
  <p class="fc-race__caption">Each bar fills in its measured time. Apple M3 Pro, Python 3.14, Qiskit 2.6.2.</p>
</div>

Almost all of that time goes to the Python interpreter, not to adding the gates.
Qiskit's core is written in Rust for exactly this reason, and its C API lets
compiled programs use Qiskit at native speed. But to use it, you have to leave
Python. fastcircuit closes the gap: the Python you already write is compiled to machine code that calls the C API
directly.

## Why fastcircuit?

<div class="fc-features" markdown>

<div class="fc-feature" markdown>

### Up to 100× faster

Python loops that call Qiskit run as native machine code instead of interpreted
bytecode, through Qiskit's own C API.

</div>

<div class="fc-feature" markdown>

### One line to adopt

No new language, no type annotations, no C. Wrap the slow part; everything around
it stays plain Python and plain Qiskit.

</div>

<div class="fc-feature" markdown>

### Identical results

Every value and exception matches plain Python, down to the traceback line.
Anything fastcircuit cannot compile is rejected, never silently run as slow Python.

</div>

<div class="fc-feature" markdown>

### Nothing to set up

`pip install fastcircuit`. No C compiler on most machines, and compiled code is
cached, so later runs start in milliseconds.

</div>

</div>

## Benchmarks

The same code run as plain Python and inside `with fastcircuit:`.

| Workload | Gates | Plain Python | fastcircuit | Speedup |
|---|---:|---:|---:|---:|
| Layers of `h`, `rx` and `cx` | 996,866 | 1.52 s | 15.1 ms | **101×** |
| Rotation angles read from a NumPy array | 225,000 | 377 ms | 4.5 ms | **84×** |
| Hardware-efficient ansatz (2-D angle array, `np.cos`) | 300,000 | 523 ms | 7.8 ms | **67×** |
| QFT-style controlled phases | 180,300 | 358 ms | 9.6 ms | **37×** |

<p class="fc-note">Apple M3 Pro, Python 3.14, Qiskit 2.6.2, default (LLVM) backend; median of
5 runs, every circuit checked instruction by instruction against plain Python. The
gain grows with the number of gates: about 9× at 300 gates, 37× to 105× from 10,000
gates up. Compiling a block takes tens of milliseconds once, then it loads from the
cache. Results depend on your machine and your code.</p>

## What it speeds up

fastcircuit speeds up the Python you write around Qiskit, not Qiskit itself.

<div class="fc-scope" markdown>

<div markdown>

### Faster with fastcircuit

- Python loops that build circuits
- Arithmetic, `math` and NumPy functions on angles and indices
- Every gate, `measure` and `reset` call inside the block

</div>

<div markdown>

### Already native, unchanged

- `transpile` and the rest of Qiskit's Rust core
- Simulators and primitives that run your circuits
- Code outside `with fastcircuit:`

</div>

</div>

If your time goes to building circuits in Python loops, fastcircuit makes that
faster. If it goes to transpiling or running circuits, it does not: that work is
already compiled.

## Install

```bash
pip install fastcircuit
python -m fastcircuit doctor     # checks the backend, Qiskit and the cache
```

Linux or macOS, Python 3.11 or newer, and Qiskit 2.6. [Platforms](platforms.md)
lists the few setups that need a C compiler.

!!! note "Alpha"

    Today fastcircuit compiles loops, conditionals, arithmetic, `math` and NumPy
    math functions, NumPy arrays, 42 standard gates, `measure` and `reset`. More of
    Qiskit is planned, starting with `Parameter` support. The API may change
    before 1.0. It is built for code that adds many gates in loops; for a handful
    of gates, plain Qiskit is just as fast.

## Documentation

<ul class="fc-index">
  <li><a href="guide/"><strong>User guide</strong><span>Where blocks work, errors, the cache and troubleshooting</span></a></li>
  <li><a href="supported-syntax/"><strong>Supported syntax</strong><span>Statements, types, gates, math and NumPy functions</span></a></li>
  <li><a href="platforms/"><strong>Platforms</strong><span>Operating systems, CPUs, Python versions and backends</span></a></li>
  <li><a href="compatibility/"><strong>Compatibility</strong><span>Supported Qiskit versions and API stability</span></a></li>
  <li><a href="architecture/"><strong>Architecture</strong><span>How the compiler works, for contributors</span></a></li>
</ul>
