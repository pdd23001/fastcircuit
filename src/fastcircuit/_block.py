"""Run-time side of ``with fastcircuit:`` blocks (see ``_rewrite``).

Each rewritten block calls ``run(site_id, values)`` with the current value of every
free name the block reads. Numbers and arrays become parameters, the one
``QuantumCircuit`` the block calls methods on is the target, and everything else
(modules, builtins) is resolved like a global. The block is compiled once per
combination of parameter types and resolved objects, then reused.
"""

from __future__ import annotations

import ast
import sys
import threading
import time
import types
from dataclasses import dataclass
from typing import Any

import numpy as np
from qiskit import QuantumCircuit

from ._analysis import Analyzer
from ._compat import check_environment
from ._ir import ARRAYS
from ._native import _check_argument, backend_name, prepare
from ._rewrite import BlockSite
from ._source import FunctionSource

_SITES: list[BlockSite] = []
_SITES_LOCK = threading.Lock()


def register(site: BlockSite) -> int:
    with _SITES_LOCK:
        _SITES.append(site)
        return len(_SITES) - 1


@dataclass
class Compiled:
    native: Any  # entry point: (circuit, *args) -> circuit
    params: tuple[str, ...]
    resolved: tuple[Any, ...]  # held so the ids in the specialization key stay unique
    backend: str
    timings: dict[str, Any]
    key: str  # cache key of the backend's code
    c_source: str  # the generated C (always generated)
    llvm_ir: str | None  # the generated LLVM IR (LLVM backend)


@dataclass
class _State:
    lock: threading.Lock
    compiled: dict[tuple[Any, ...], Compiled]


_STATES: dict[int, _State] = {}

_KINDS: dict[type, str] = {
    int: "int",
    float: "float",
    np.float64: "float64",
    np.int64: "int64",
    np.ndarray: "array",  # refined by array_kind
}


def _classify(site: BlockSite, values: dict[str, Any]) -> tuple[str, list[str], list[str]]:
    """(circuit name, parameter names, their kinds); raises for unsupported values."""
    circuits = [n for n in site.receivers if isinstance(values[n], QuantumCircuit)]
    if not circuits:
        raise TypeError(
            f"the fastcircuit block at {site.filename}:{site.lineno} must append to a "
            "QuantumCircuit created before it, e.g. 'qc = QuantumCircuit(n)'"
        )
    if len(circuits) > 1:
        raise TypeError(
            f"the fastcircuit block at {site.filename}:{site.lineno} calls methods on "
            f"several circuits ({', '.join(sorted(circuits))}); a block appends to one"
        )
    circuit = circuits[0]
    if type(values[circuit]) is not QuantumCircuit:
        raise TypeError(
            f"{circuit!r} is a {type(values[circuit]).__name__}; fastcircuit blocks append "
            "to qiskit.QuantumCircuit itself, not subclasses"
        )
    params: list[str] = []
    kinds: list[str] = []
    for name in site.names:
        if name == circuit:
            continue
        value = values[name]
        kind = _KINDS.get(type(value))
        if kind == "array":
            kind = array_kind(value)
            if kind is None:
                raise TypeError(
                    f"array {name!r} used in the fastcircuit block at {site.filename}:"
                    f"{site.lineno} has dtype {value.dtype} and {value.ndim} dimension(s); "
                    "blocks can use 1-D and 2-D float64 or int64 arrays"
                )
        if kind is not None:
            params.append(name)
            kinds.append(kind)
        elif not _resolvable(value):
            raise TypeError(
                f"variable {name!r} used in the fastcircuit block at {site.filename}:"
                f"{site.lineno} is a {type(value).__name__}; blocks can use Python int "
                "and float values, np.float64 and np.int64 values, and 1-D and 2-D "
                "float64 or int64 NumPy arrays"
                + (" (convert with int(...) or float(...))" if _numeric(value) else "")
            )
    return circuit, params, kinds


def array_kind(value: np.ndarray) -> str | None:
    """The IR array kind of ``value`` (see ``_ir.ARRAYS``), or None if unsupported."""
    for kind, (element, ndim) in ARRAYS.items():
        if value.dtype == np.dtype(element) and value.ndim == ndim:
            return kind
    return None


def _numeric(value: Any) -> bool:
    return isinstance(value, (bool, np.generic))


def _resolvable(value: Any) -> bool:
    """Objects the compiler may resolve by identity: modules, functions, classes."""
    return not _numeric(value) and isinstance(
        value, (types.ModuleType, type, types.BuiltinFunctionType, types.FunctionType, np.ufunc)
    )


def run(site_id: int, values: dict[str, Any]) -> None:
    site = _SITES[site_id]
    circuit, params, kinds = _classify(site, values)
    resolved = tuple(values[n] for n in site.names if n != circuit and n not in params)
    key = (circuit, tuple(kinds), tuple(map(id, resolved)))
    state = _STATES.get(site_id)
    if state is None:
        state = _STATES.setdefault(site_id, _State(threading.Lock(), {}))
    compiled = state.compiled.get(key)
    if compiled is None:
        with state.lock:
            compiled = state.compiled.get(key)
            if compiled is None:
                compiled = _compile(site, circuit, params, kinds, values)
                state.compiled[key] = compiled
    args = [values[n] for n in compiled.params]
    for name, kind, value in zip(compiled.params, kinds, args, strict=True):
        _check_argument(name, kind, value)
    compiled.native(values[circuit], *args)


def _compile(
    site: BlockSite, circuit: str, params: list[str], kinds: list[str], values: dict[str, Any]
) -> Compiled:
    backend = backend_name()
    check_environment(backend)
    t0 = time.perf_counter()
    namespace: dict[str, Any] = {
        n: values[n] for n in site.names if n != circuit and n not in params
    }
    namespace["__builtins__"] = {}  # every name the block reads is in `namespace`
    fn = types.FunctionType(_EMPTY_CODE, namespace, "block")
    node = ast.FunctionDef(
        name="block",
        args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=list(site.body),
        decorator_list=[],
        lineno=site.lineno,
        col_offset=site.col,
    )
    src = FunctionSource(fn, node, site.filename, site.qualname, 0)
    program = Analyzer(src, dict(zip(params, kinds, strict=True))).run_block(  # type: ignore[arg-type]
        circuit, list(site.body), site.lineno
    )
    t1 = time.perf_counter()
    native_code = prepare(program, backend)
    t2 = time.perf_counter()
    timings: dict[str, Any] = {
        "analysis_seconds": t1 - t0,
        "codegen_seconds": t2 - t1,
        "build_seconds": None,
        "load_seconds": None,
        "source": None,
    }
    native = native_code.obtain(timings)
    compiled = Compiled(
        native=native,
        params=tuple(params),
        resolved=tuple(namespace.values()),
        backend=backend,
        timings=timings,
        key=native_code.key,
        c_source=native_code.c_source,
        llvm_ir=native_code.llvm_ir,
    )
    if site.debug:
        _report(site, compiled, kinds, time.perf_counter() - t0)
    return compiled


def _report(site: BlockSite, compiled: Compiled, kinds: list[str], total: float) -> None:
    signature = ", ".join(f"{n}: {k}" for n, k in zip(compiled.params, kinds, strict=True))
    t = compiled.timings

    def ms(value: float | None) -> str:
        return "-" if value is None else f"{value * 1e3:.1f} ms"

    print(
        f"fastcircuit: block {site.filename}:{site.lineno} ({site.scope}) "
        f"specialized for ({signature}), {compiled.backend} backend\n"
        f"  analysis {ms(t['analysis_seconds'])}, codegen {ms(t['codegen_seconds'])}, "
        f"build {ms(t['build_seconds'])}, load {ms(t['load_seconds'])}; "
        f"total {ms(total)}; code from: {t['source']}",
        file=sys.stderr,
    )


def _empty() -> None:
    pass


_EMPTY_CODE = _empty.__code__


def compiled_blocks() -> list[tuple[BlockSite, Compiled]]:
    """Every block compiled in this process with its specializations (for debugging)."""
    with _SITES_LOCK:
        sites = list(enumerate(_SITES))
    return [
        (site, compiled)
        for site_id, site in sites
        for compiled in (_STATES[site_id].compiled.values() if site_id in _STATES else ())
    ]
