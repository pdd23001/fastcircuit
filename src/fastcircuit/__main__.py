"""Command line: ``python -m fastcircuit {doctor,run,cache {info,list,clear,path}}``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable

from . import _build, _cache
from ._compat import check_environment, select_backend
from ._version import __version__
from .errors import CacheError, FastCircuitError


def _llvm_check() -> str:
    from . import _llvm

    _llvm.load_object(_llvm.compile_object("define i32 @f() {\n  ret i32 0\n}\n"))
    return str(_llvm.compile_config()["toolchain"])


def _report(status: str, name: str, detail: object = None) -> None:
    """One check per line, status first; details (every line) indented below it."""
    first, *more = str(name).splitlines() or [""]
    print(f"  {status:<4}  {first}")
    for line in [*more, *(str(detail).splitlines() if detail else [])]:
        print(f"        {line}")


def _cache_check() -> str:
    cache = _cache.Cache()
    if not _cache.enabled():
        return f"{cache.root} (disabled by FASTCIRCUIT_NO_CACHE)"
    cache.compilers_dir()  # creates the directories, refusing unsafe ones
    if not os.access(cache.root, os.W_OK):
        raise CacheError(f"cache directory {cache.root} is not writable")
    return str(cache.root)


def _doctor() -> int:
    import qiskit

    ok = True
    try:
        backend, reason = select_backend()
    except ValueError as exc:  # an unknown FASTCIRCUIT_BACKEND
        print(f"fastcircuit {__version__}, Qiskit {qiskit.__version__}")
        _report("FAIL", f"backend: {exc}")
        return 1
    print(
        f"fastcircuit {__version__}, Qiskit {qiskit.__version__}, Python "
        f"{sys.version.split()[0]}, backend {backend} ({reason})"
    )
    # Each check raises on failure and may return a detail line to show.
    required: list[tuple[str, Callable[[], object]]] = [
        ("environment", lambda: check_environment(backend))
    ]
    c_checks: list[tuple[str, Callable[[], object]]] = [
        ("C compiler", lambda: " ".join(_build.find_toolchain().argv)),
        ("Python headers", _build.check_headers),
    ]
    if backend == "llvm":
        required.append(("LLVM (llvmlite) compiles and loads code", _llvm_check))
    else:
        required += c_checks
    required.append(("cache directory", _cache_check))
    for name, check in required:
        try:
            detail = check()
        except (FastCircuitError, RuntimeError, OSError, ImportError) as exc:
            ok = False
            _report("FAIL", f"{name}: {exc}")
        else:
            _report("ok", name, detail)
    if backend == "llvm":  # informational: only FASTCIRCUIT_BACKEND=c needs these
        for name, check in c_checks:
            try:
                detail = check()
            except FastCircuitError as exc:
                _report("--", f"{name}: unavailable (not needed for the default backend)", exc)
            else:
                _report("ok", f"{name} (optional, for FASTCIRCUIT_BACKEND=c)", detail)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    invoked = os.path.basename(sys.argv[0]) if sys.argv else ""
    prog = "fastcircuit" if invoked == "fastcircuit" else "python -m fastcircuit"
    parser = argparse.ArgumentParser(prog=prog)
    parser.add_argument("--version", action="version", version=f"fastcircuit {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="check the backend, Qiskit and cache setup")
    run = sub.add_parser(
        "run", help="run a script with its 'with fastcircuit:' blocks compiled, top level included"
    )
    run.add_argument("script", help="path of the Python script to run")
    run.add_argument("args", nargs=argparse.REMAINDER, help="arguments for the script")
    cache = sub.add_parser("cache", help="inspect or clear the compilation cache")
    cache.add_argument("action", choices=["info", "list", "clear", "path"])
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return _doctor()
    if args.command == "run":
        from ._hooks import run_script

        try:  # report a script that cannot be opened as ``python`` does, not as a traceback
            with open(args.script, "rb"):
                pass
        except OSError as exc:
            print(
                f"{parser.prog}: can't open file {os.path.abspath(args.script)!r}: "
                f"[Errno {exc.errno}] {exc.strerror}",
                file=sys.stderr,
            )
            return 2
        run_script(args.script, args.args)
        return 0
    c = _cache.Cache()
    if args.action == "path":
        print(c.root)
    elif args.action == "info":
        print(json.dumps(c.info(), indent=2))
    elif args.action == "list":
        for e in c.entries():
            print(f"{e.meta['key'][:16]}  {e.meta.get('function', '?')}  {e.path}")
    else:
        print(f"removed {c.clear()} cache entries from {c.root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
