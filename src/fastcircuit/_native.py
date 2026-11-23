"""Native code for compiled blocks: generate it, then load or compile it.

``prepare`` turns a block's IR into backend code. Its ``obtain`` step returns the
native entry point: from this process, from the persistent cache, or by
compiling. ``_check_argument`` validates a value before it reaches native code.
"""

from __future__ import annotations

import functools
import sysconfig
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np

from . import _build, _cache
from . import _ir as ir
from ._codegen_c import generate_body, module_init
from ._compat import select_backend
from ._ir import ARRAYS
from .errors import CompilerNotFoundError, NativeBuildError

_LOADED: dict[str, ModuleType] = {}  # C backend: extension modules by cache key
_RUNNERS: dict[str, Callable[..., Any]] = {}  # LLVM backend: call wrappers by cache key
_LOADED_LOCK = threading.Lock()


def backend_name() -> str:
    """The backend new compilations use; see ``_compat.select_backend``."""
    return select_backend()[0]


_TEMP_DIRS: list[tempfile.TemporaryDirectory[str]] = []  # uncached builds live until exit


def _nothing() -> None:
    pass


def _obtain_module(
    key: str, module: str, c_source: str, label: str, timings: dict[str, Any]
) -> ModuleType:
    with _LOADED_LOCK:
        if key in _LOADED:
            timings["source"] = "process"
            return _LOADED[key]
        suffix = sysconfig.get_config_var("EXT_SUFFIX")
        on_bad_binary: Callable[[], None] = _nothing
        if not _cache.enabled():
            tool = _build.find_toolchain()
            _build.check_headers()
            work = tempfile.TemporaryDirectory(prefix="fastcircuit-")
            _TEMP_DIRS.append(work)
            path = Path(work.name)
            (path / "source.c").write_text(c_source)
            t = time.perf_counter()
            binary = path / (module + suffix)
            _build.compile_extension(tool, path / "source.c", binary, path / "build.log")
            timings["build_seconds"] = time.perf_counter() - t
            timings["source"] = "compiled (cache disabled)"
        else:
            cache, entry = _from_cache(key, module, c_source, suffix, label, timings)
            binary = entry.binary
            on_bad_binary = functools.partial(cache.discard, entry.path)
        t = time.perf_counter()
        try:
            ext = _build.load_extension(binary, module)
        except (ImportError, OSError) as exc:
            on_bad_binary()  # never retry a binary that failed to load
            raise NativeBuildError(
                f"compiled extension could not be loaded ({exc}). Remove it with "
                "'python -m fastcircuit cache clear' if this persists."
            ) from exc
        timings["load_seconds"] = time.perf_counter() - t
        if getattr(ext, "__fastcircuit_key__", None) != key:
            on_bad_binary()
            raise NativeBuildError(f"extension {binary} does not match its cache key")
        _LOADED[key] = ext
        return ext


def _from_cache(
    key: str, module: str, c_source: str, suffix: str, label: str, timings: dict[str, Any]
) -> tuple[_cache.Cache, _cache.Entry]:
    cache = _cache.Cache()
    tool: _build.Toolchain | None = None
    missing: CompilerNotFoundError | None = None
    try:
        tool = _build.find_toolchain()
    except CompilerNotFoundError as exc:
        missing = exc
    # Without a compiler, accept a valid build from any compiler: the C ABI does
    # not depend on which compiler produced the binary.
    compiler = tool.identity(cache.compilers_dir()) if tool else None
    entry = cache.lookup(key, compiler)
    if entry is None:
        if tool is None or compiler is None:
            raise missing or CompilerNotFoundError("no C compiler found")
        _build.check_headers()
        with cache.lock(key):
            entry = cache.lookup(key, compiler)  # another process may have finished
            if entry is None:
                staging = cache.staging_dir()
                c_file = staging / "source.c"
                c_file.write_text(c_source)
                binary = staging / (module + suffix)
                t = time.perf_counter()
                try:
                    _build.compile_extension(tool, c_file, binary, staging / "build.log")
                except Exception as exc:
                    kept = cache.keep_failed(staging, key)
                    if isinstance(exc, NativeBuildError):
                        raise NativeBuildError(str(exc).replace(str(staging), str(kept))) from exc
                    raise
                timings["build_seconds"] = time.perf_counter() - t
                entry = cache.publish(
                    staging,
                    key,
                    compiler,
                    module,
                    binary,
                    {"compiler_argv": list(tool.argv), "function": label},
                )
                timings["source"] = "compiled"
                return cache, entry
    timings["source"] = "persistent cache"
    return cache, entry


@dataclass(frozen=True)
class NativeCode:
    """Generated code for one program, and how to get its native entry point."""

    key: str  # cache key of the backend's code
    c_source: str  # always generated (inspection, cache key of the C backend)
    llvm_ir: str | None  # LLVM backend only
    obtain: Callable[[dict[str, Any]], Callable[..., Any]]


def prepare(program: ir.Function, backend: str) -> NativeCode:
    """Generate code for ``program``; ``obtain(timings)`` loads or compiles it.

    The entry point it returns takes the target circuit and the already-validated
    arguments, and returns the circuit.
    """
    body = generate_body(program)
    c_key = _cache.make_key(body, _build.compile_config())
    module = "_fc_" + c_key[:32]
    c_source = body + module_init(module, c_key)
    if backend != "llvm":

        def obtain_module(timings: dict[str, Any]) -> Callable[..., Any]:
            ext = _obtain_module(c_key, module, c_source, program.qualname, timings)
            return ext.run  # type: ignore[no-any-return]

        return NativeCode(c_key, c_source, None, obtain_module)

    # Deferred so 'import fastcircuit' and the C backend never load LLVM.
    from . import _llvm  # noqa: PLC0415

    llvm_ir, sites = _llvm.generate(program)
    key = _cache.make_key(llvm_ir, _llvm.compile_config())

    def obtain_runner(timings: dict[str, Any]) -> Callable[..., Any]:
        # _RUNNERS holds the checked entry point; callers have already validated
        # their arguments, so they get the unchecked one.
        with _LOADED_LOCK:
            if key in _RUNNERS:
                timings["source"] = "process"
                return _RUNNERS[key].unchecked  # type: ignore[attr-defined,no-any-return]
        runner = _llvm.obtain(key, llvm_ir, sites, program, program.qualname, timings)
        with _LOADED_LOCK:
            return _RUNNERS.setdefault(key, runner).unchecked  # type: ignore[attr-defined,no-any-return]

    return NativeCode(key, c_source, llvm_ir, obtain_runner)


def _check_argument(name: str, kind: str, value: Any) -> None:
    if kind == "int":
        if type(value) is not int:
            raise TypeError(
                f"{name} must be a Python int, got {type(value).__name__}"
                + (" (bool is not accepted)" if type(value) is bool else "")
            )
        if not -(2**63) <= value < 2**63:
            raise OverflowError(f"{name}={value} does not fit in int64")
    elif kind == "float":
        if type(value) is not float:
            raise TypeError(
                f"{name} must be a Python float, got {type(value).__name__}; "
                f"pass float({name}) explicitly"
            )
    elif kind in ("float64", "int64"):
        if type(value) is not np.dtype(kind).type:
            raise TypeError(f"{name} must be an np.{kind}, got {type(value).__name__}")
    else:
        element, ndim = ARRAYS[kind]
        if (
            type(value) is not np.ndarray
            or value.dtype != np.dtype(element)
            or value.ndim != ndim
            or not value.flags.c_contiguous
            or not value.flags.aligned
        ):
            raise TypeError(
                f"{name} must be an aligned, C-contiguous {ndim}-D {element} numpy.ndarray; "
                f"use np.ascontiguousarray({name}, dtype=np.{element})"
            )
