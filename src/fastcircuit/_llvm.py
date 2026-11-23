"""In-process native compilation with llvmlite: the default backend (docs/architecture.md).

Generated LLVM IR is optimised (O2) and turned into an object file in this process,
so no C compiler or Python headers are needed. Object files go into the persistent
cache (same key discipline, locking and validation as the C backend's extensions)
and are loaded with MCJIT. Kernels reach Qiskit's C API through the function-pointer
table Qiskit publishes for Python extensions (``qiskit._accelerate.capi``), whose slot
numbers and gate enum values are read from the installed headers. Kernels are called
through ``ctypes.PYFUNCTYPE``: the GIL stays held (they mutate a Python-owned circuit
and run Python signal handlers), and an exception raised by a signal handler during
the call propagates to the caller.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import functools
import hashlib
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import llvmlite
import llvmlite.binding as llvm
from qiskit import QuantumCircuit, _accelerate
from qiskit.capi import get_include

from . import _cache, _fperror, _npufunc
from . import _ir as ir
from ._codegen_c import MAX_CLBITS, MAX_QUBITS
from ._codegen_llvm import (
    API_BORROW,
    API_CHECK_SIGNALS,
    API_GATE,
    API_MEASURE,
    API_REPORT,
    API_REPORT_INT,
    API_RESET,
    API_SLOTS,
    API_UFUNCS,
    ARRAY_INDEX,
    DUPLICATE,
    INTERRUPT,
    LIBM,
    LIBM_PREFIX,
    OVERFLOW,
    PENDING,
    QISKIT,
    QUBIT,
    VALUE,
    ZERO,
    Site,
)
from ._codegen_llvm import generate as _generate
from ._native import _check_argument
from .errors import (
    DuplicateQubitError,
    IncompatibleEnvironmentError,
    NativeBuildError,
    QubitIndexError,
)

_EXCEPTIONS: dict[int, type[Exception]] = {
    QUBIT: QubitIndexError,
    DUPLICATE: DuplicateQubitError,
    ZERO: ZeroDivisionError,
    OVERFLOW: OverflowError,
    ARRAY_INDEX: IndexError,
    QISKIT: RuntimeError,
    VALUE: ValueError,
}
_INCLUDE = Path(get_include()) / "qiskit"
_INIT_LOCK = threading.Lock()
_STATE: dict[str, Any] = {}


# -- Qiskit C API table ----------------------------------------------------------
def _header(name: str) -> str:
    return (_INCLUDE / name).read_text()


def _slots() -> dict[str, int]:
    pattern = r"#define (qk_\w+) .*_Qk_API_Circuit\[(\d+)\]"
    return {m[1]: int(m[2]) for m in re.finditer(pattern, _header("funcs_py_generated.h"))}


@functools.cache
def gate_enum() -> dict[str, int]:
    return {m[1]: int(m[2]) for m in re.finditer(r"\bQkGate_(\w+) = (\d+)", _header("types.h"))}


def _headers_version() -> int:
    text = _header("version.h")
    parts = []
    for p in ("MAJOR", "MINOR", "PATCH"):
        match = re.search(rf"#define QISKIT_VERSION_{p} (\d+)", text)
        if match is None:
            raise IncompatibleEnvironmentError("cannot read Qiskit's C API version header")
        parts.append(int(match[1]))
    return (parts[0] << 24) | (parts[1] << 16) | (parts[2] << 8)


def _api_table() -> Any:
    pyapi = ctypes.pythonapi
    pyapi.PyCapsule_GetPointer.restype = ctypes.c_void_p
    pyapi.PyCapsule_GetPointer.argtypes = [ctypes.py_object, ctypes.c_char_p]
    name = b"qiskit._accelerate.capi.QK_FFI_CIRCUIT"
    slots = _slots()
    table = pyapi.PyCapsule_GetPointer(_accelerate.capi.QK_FFI_CIRCUIT, name)
    entries = (ctypes.c_void_p * (max(slots.values()) + 1)).from_address(table)

    def address(fn: str) -> int:
        value = entries[slots[fn]]
        if not value:
            raise IncompatibleEnvironmentError(f"Qiskit C API function {fn} is unavailable")
        return int(value)

    runtime = ctypes.CFUNCTYPE(ctypes.c_uint32)(address("qk_api_version"))()
    # Same rule as the C backend's PyInit check: the running Qiskit must match the
    # headers the slot numbers were read from (release level/serial ignored).
    if runtime & ~0xFF != _headers_version():
        raise IncompatibleEnvironmentError(
            f"Qiskit C API version {runtime:#x} does not match its headers "
            f"({_headers_version():#x}); reinstall qiskit"
        )
    api = (ctypes.c_uint64 * API_SLOTS)()
    api[API_GATE] = address("qk_circuit_gate")
    api[API_MEASURE] = address("qk_circuit_measure")
    api[API_RESET] = address("qk_circuit_reset")
    api[API_CHECK_SIGNALS] = ctypes.cast(pyapi.PyErr_CheckSignals, ctypes.c_void_p).value or 0
    api[API_BORROW] = address("qk_circuit_borrow_from_python")
    api[API_REPORT] = ctypes.cast(_REPORT, ctypes.c_void_p).value or 0
    api[API_REPORT_INT] = ctypes.cast(_REPORT_INT, ctypes.c_void_p).value or 0
    missing = [i for i in range(API_UFUNCS) if not api[i]]
    if missing:  # a kernel would jump to address 0
        raise IncompatibleEnvironmentError(f"API table slots {missing} are not filled")
    return api


def _fill_ufuncs(api: Any) -> None:
    """Put NumPy's float64 loops in the API table (only kernels that call them need it)."""
    with _INIT_LOCK:
        if api[API_UFUNCS]:
            return
        for i, (func, data) in enumerate(_npufunc.loop_table()):
            api[API_UFUNCS + 2 * i + 1] = data
            api[API_UFUNCS + 2 * i] = func


# Called by kernels with the GIL held; must stay alive as long as kernels can run.
_REPORT = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_int32, ctypes.c_double, ctypes.c_double,
                           ctypes.c_int32)(_fperror.report_from_callback)  # fmt: skip
_REPORT_INT = ctypes.CFUNCTYPE(
    ctypes.c_int32, ctypes.c_int32, ctypes.c_int64, ctypes.c_int64, ctypes.c_int32
)(_fperror.report_int_from_callback)


def _register_libm() -> None:
    """Generated code calls libm (as Python's math module does); tell MCJIT where it is."""
    process = ctypes.CDLL(None)  # CPython links libm, so the process usually has it
    libm = None
    for name in LIBM:
        fn = getattr(process, name, None)
        if fn is None:
            if libm is None:  # find_library may start a subprocess on Linux: only if needed
                found = ctypes.util.find_library("m")
                if not found:
                    raise IncompatibleEnvironmentError("the C math library (libm) was not found")
                libm = ctypes.CDLL(found)
            fn = getattr(libm, name, None)
            if fn is None:
                raise IncompatibleEnvironmentError(f"libm function {name} not found")
        llvm.add_symbol(LIBM_PREFIX + name, ctypes.cast(fn, ctypes.c_void_p).value)


def _state() -> dict[str, Any]:
    with _INIT_LOCK:
        if not _STATE:
            llvm.initialize_native_target()
            llvm.initialize_native_asmprinter()
            _register_libm()
            target = llvm.Target.from_triple(llvm.get_process_triple())
            tm = target.create_target_machine(opt=2)
            api = _api_table()
            _STATE.update(
                target=target,
                tm=tm,
                api=api,
                toolchain=(
                    f"llvmlite {llvmlite.__version__}; LLVM "
                    f"{'.'.join(map(str, llvm.llvm_version_info))}; {tm.triple}; O2"
                ),
            )
        return _STATE


# -- compilation -------------------------------------------------------------------
def generate(program: ir.Function) -> tuple[str, list[Site]]:
    return _generate(program, gate_enum())


def compile_config() -> dict[str, object]:
    """Every compilation input except the IR itself (part of the cache key)."""
    return {"backend": "llvm", "toolchain": _state()["toolchain"]}


def toolchain_label() -> str:
    """Cache subdirectory name: object code is specific to the LLVM build and target."""
    return "llvm-" + hashlib.sha256(_state()["toolchain"].encode()).hexdigest()[:16]


def compile_object(ir_text: str) -> bytes:
    state = _state()
    tm = state["tm"]
    try:
        mod = llvm.parse_assembly(ir_text)
        mod.triple = tm.triple
        mod.data_layout = str(tm.target_data)
        mod.verify()
    except RuntimeError as exc:  # generator bug: report, never run unverified code
        raise NativeBuildError(f"generated LLVM IR is invalid: {exc}") from exc
    pb = llvm.create_pass_builder(tm, llvm.create_pipeline_tuning_options(speed_level=2))
    pb.getModulePassManager().run(mod, pb)
    obj: bytes = tm.emit_object(mod)
    return obj


def load_object(obj: bytes) -> Any:
    state = _state()
    # An MCJIT engine takes ownership of the target machine it is given and frees it
    # with the engine, so every engine gets its own.
    engine = llvm.create_mcjit_compiler(
        llvm.parse_assembly(""), state["target"].create_target_machine(opt=2)
    )
    engine.add_object_file(llvm.ObjectFileRef.from_data(obj))
    engine.finalize_object()
    return engine


# -- calling -----------------------------------------------------------------------
_CTYPE = {
    "int": ctypes.c_int64,
    "int64": ctypes.c_int64,
    "float": ctypes.c_double,
    "float64": ctypes.c_double,
}


def make_runner(
    engine: Any, sites: list[Site], program: ir.Function
) -> Callable[..., QuantumCircuit]:
    state = _state()
    api = state["api"]
    if ir.uses(program, ir.UFunc):
        _fill_ufuncs(api)
    argtypes: list[Any] = []
    for p in program.params:
        if p.type in ir.ARRAYS:  # data, then the size of each dimension
            argtypes += [ctypes.c_void_p] + [ctypes.c_int64] * ir.ARRAYS[p.type][1]
        else:
            argtypes.append(_CTYPE[p.type])
    i32, vp, i64 = ctypes.c_int32, ctypes.c_void_p, ctypes.c_int64
    kernel = ctypes.PYFUNCTYPE(i32, vp, ctypes.py_object, i64, i64, vp, *argtypes)(
        engine.get_function_address("kernel")
    )
    kinds = [p.type for p in program.params]
    simple = all(k in ("int", "float", "float64") for k in kinds)  # passed through as is
    nparams = len(program.params)
    location = f"{program.filename}, line {{line}}, in {program.qualname}"
    buffer = ctypes.c_int64 * 4  # err[4], one allocation per call

    def raise_error(err: Any) -> None:
        kind, a, b, site = err
        if kind == PENDING:
            raise _fperror.take_pending()
        if kind == INTERRUPT:  # pragma: no cover - PYFUNCTYPE raises the pending exception
            raise RuntimeError("native code reported a Python exception, but none was set")
        info = sites[site]
        raise _EXCEPTIONS[kind](
            info.message.format(a=a, b=b) + " (" + location.format(line=info.line) + ")"
        )

    def limit(message: str) -> ValueError:
        return ValueError(f"{message} ({location.format(line=program.line)})")

    def execute(args: Any, target: QuantumCircuit) -> QuantumCircuit:
        """Append to ``target``; arrays already flattened to (address, length) and
        arguments already validated."""
        buf = buffer()
        nq, nc = target.num_qubits, target.num_clbits
        if nq > MAX_QUBITS or nc > MAX_CLBITS:
            raise limit("circuit size exceeds fastcircuit limit")
        data = target._data  # referenced until return, so the borrowed pointer stays valid
        if kernel(api, data, nq, nc, ctypes.addressof(buf), *args):
            raise_error(buf[:4])
        return target

    def split(args: Any) -> tuple[QuantumCircuit, Any]:
        if not args or type(args[0]) is not QuantumCircuit:
            raise TypeError(f"expected a QuantumCircuit first (in {program.qualname})")
        return args[0], args[1:]

    def flatten(args: Any) -> Any:
        """Arrays become (address, sizes...); np.int64 values become ints for ctypes."""
        if simple:
            return args
        flat: list[Any] = []
        for kind, value in zip(kinds, args, strict=True):
            if kind in ir.ARRAYS:
                # The caller's reference keeps the buffer alive for the call.
                flat += (value.ctypes.data, *value.shape)
            else:
                flat.append(int(value) if kind == "int64" else value)
        return tuple(flat)

    def unchecked(*args: Any) -> QuantumCircuit:
        """Entry point for blocks, which have validated every argument."""
        target, args = split(args)
        return execute(flatten(args), target)

    def run(*args: Any) -> QuantumCircuit:
        target, args = split(args)
        if len(args) != nparams:
            raise TypeError(  # counts the circuit, as the C backend does
                f"expected {nparams + 1} arguments, got {len(args) + 1} (in {program.qualname})"
            )
        # Defence in depth (blocks already validated): the kernel reads arrays
        # through their sizes, so never hand it anything but the expected layout.
        for p, value in zip(program.params, args, strict=True):
            _check_argument(p.name, p.type, value)
        args = flatten(args)
        return execute(args, target)

    # JIT memory must outlive the function pointers held by both entry points.
    run.engine = engine  # type: ignore[attr-defined]
    run.unchecked = unchecked  # type: ignore[attr-defined]
    return run


def obtain(
    key: str,
    ir_text: str,
    sites: list[Site],
    program: ir.Function,
    label: str,
    timings: dict[str, Any],
) -> Callable[..., QuantumCircuit]:
    """Load from the persistent cache or compile; returns the call wrapper."""
    toolchain = toolchain_label()
    on_bad: Callable[[], None] = lambda: None  # noqa: E731
    if not _cache.enabled():
        t = time.perf_counter()
        obj = compile_object(ir_text)
        timings["build_seconds"] = time.perf_counter() - t
        timings["source"] = "compiled (cache disabled)"
    else:
        cache = _cache.Cache()
        entry = cache.lookup(key, toolchain)
        if entry is None:
            with cache.lock(key):
                entry = cache.lookup(key, toolchain)  # another process may have finished
                if entry is None:
                    staging = cache.staging_dir()
                    (staging / "kernel.ll").write_text(ir_text)  # inspectable source
                    t = time.perf_counter()
                    try:
                        obj = compile_object(ir_text)
                    except Exception:
                        cache.keep_failed(staging, key)
                        raise
                    (staging / "kernel.o").write_bytes(obj)
                    timings["build_seconds"] = time.perf_counter() - t
                    entry = cache.publish(
                        staging,
                        key,
                        toolchain,
                        "llvm",
                        staging / "kernel.o",
                        {"toolchain": _state()["toolchain"], "function": label},
                    )
                    timings["source"] = "compiled"
        if timings.get("source") != "compiled":
            timings["source"] = "persistent cache"
        obj = entry.binary.read_bytes()
        on_bad = functools.partial(cache.discard, entry.path)
    t = time.perf_counter()
    try:
        engine = load_object(obj)
        runner = make_runner(engine, sites, program)
    except (RuntimeError, OSError, ValueError) as exc:
        on_bad()  # never retry an object file that failed to load
        raise NativeBuildError(
            f"compiled code could not be loaded ({exc}). Remove it with "
            "'python -m fastcircuit cache clear' if this persists."
        ) from exc
    timings["load_seconds"] = time.perf_counter() - t
    return runner
