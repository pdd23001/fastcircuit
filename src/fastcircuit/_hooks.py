"""How ``with fastcircuit:`` blocks get rewritten before they run.

``activate()`` runs when fastcircuit is first imported:

1. An import hook rewrites every module imported from then on that contains a
   block (pytest's assertion rewriting works the same way).
2. If the script being run (``__main__``) contains blocks, it is run again from
   the top with its blocks rewritten, and the original run ends when that run
   does. When fastcircuit was imported by a module the script is importing, every
   module on that import chain is first finished by running it again in its own
   namespace. This is done only when everything that already ran on the chain is
   safe to repeat (imports, literal assignments), so nothing visible happens
   twice: a repeated import finds the module already loaded and does nothing.
3. Otherwise, modules that are still executing at that moment, such as a
   library that imported fastcircuit, are rewritten when they finish loading: their
   functions get rewritten code objects. Blocks at their top level, or in
   functions called before they finish loading, cannot be rewritten this way and
   raise ``BlockNotCompiledError`` instead of silently running as Python.
4. In IPython and Jupyter, cells run after the import are rewritten.

``python -m fastcircuit run script.py`` rewrites the script before it starts; it
is a fallback for scripts that must run other code before importing fastcircuit.
"""

from __future__ import annotations

import ast
import builtins
import functools
import importlib.abc
import importlib.machinery
import sys
import sysconfig
import types
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from . import _block
from ._rewrite import RUNTIME, mentions_blocks, rewrite
from ._source import _code_signature

_STDLIB = tuple(
    str(Path(p).resolve())
    for p in {sysconfig.get_paths()["stdlib"], sysconfig.get_paths()["platstdlib"]}
)
_ACTIVE = False


def rewrite_source(
    source: str | bytes, filename: str
) -> tuple[types.CodeType, frozenset[str]] | None:
    """Compiled, rewritten module code and the functions changed, or None if no blocks."""
    if not mentions_blocks(source):
        return None
    tree = ast.parse(source, filename)
    result = rewrite(tree, filename, _block.register)
    if not result.blocks:
        return None
    return compile(result.tree, filename, "exec", dont_inherit=True), result.functions


# -- 1. import hook -----------------------------------------------------------
class _Loader(importlib.machinery.SourceFileLoader):
    """Rewrites modules containing blocks. Rewritten code is never written to .pyc."""

    def get_code(self, fullname: str) -> types.CodeType | None:
        path = self.get_filename(fullname)
        source = self.get_data(path)
        rewritten = rewrite_source(source, path)
        if rewritten is None:
            return super().get_code(fullname)
        return rewritten[0]

    def exec_module(self, module: types.ModuleType) -> None:
        module.__dict__[RUNTIME] = _block
        super().exec_module(module)


class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None,
        target: types.ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        if fullname == "fastcircuit" or fullname.startswith("fastcircuit."):
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        if (
            spec is None
            or not isinstance(spec.loader, importlib.machinery.SourceFileLoader)
            or spec.origin is None
            or str(Path(spec.origin).resolve()).startswith(_STDLIB)
        ):
            return None
        try:
            with open(spec.origin, "rb") as fh:
                if not mentions_blocks(fh.read()):
                    return None
        except OSError:
            return None
        spec.loader = _Loader(fullname, spec.origin)
        return spec


_FINDER = _Finder()


# -- 2. modules that were already executing ------------------------------------
def _executing_modules() -> Iterator[types.FrameType]:
    """Frames of module bodies on the stack (outermost last), excluding importlib's."""
    frame = sys._getframe(1)
    while frame is not None:
        code = frame.f_code
        if (
            code.co_name == "<module>"
            and not code.co_filename.startswith("<frozen")
            and frame.f_globals.get("__name__", "").split(".")[0] != "fastcircuit"
        ):
            yield frame
        frame = frame.f_back  # type: ignore[assignment]


_PENDING: dict[types.CodeType, Callable[[], None]] = {}  # module code -> on finish
_TOOL: int | None = None  # sys.monitoring tool id while anything is pending
_MONITORING: Any = getattr(sys, "monitoring", None)  # Python 3.12+


def _on_return(code: types.CodeType, offset: int, retval: object) -> Any:
    global _TOOL  # noqa: PLW0603
    callback = _PENDING.pop(code, None)
    if callback is None or _TOOL is None:
        return None
    monitoring = _MONITORING
    monitoring.set_local_events(_TOOL, code, 0)
    if not _PENDING:
        monitoring.register_callback(_TOOL, monitoring.events.PY_RETURN, None)
        monitoring.free_tool_id(_TOOL)
        _TOOL = None
    callback()
    return None


def _when_finished(frame: types.FrameType, callback: Callable[[], None]) -> bool:
    """Call ``callback`` when ``frame`` (a module body) returns. False if impossible."""
    global _TOOL  # noqa: PLW0603
    target = frame.f_code
    monitoring = _MONITORING
    if monitoring is not None:  # Python 3.12+
        if _TOOL is None:
            for tool in (4, 3):  # ids 3 and 4 are not reserved for any standard tool
                try:
                    monitoring.use_tool_id(tool, "fastcircuit")
                except ValueError:
                    continue
                _TOOL = tool
                monitoring.register_callback(tool, monitoring.events.PY_RETURN, _on_return)
                break
            else:
                return False
        _PENDING[target] = callback
        monitoring.set_local_events(_TOOL, target, monitoring.events.PY_RETURN)
        return True

    previous = sys.getprofile()  # pragma: no cover - Python 3.11 only

    def profile(f: types.FrameType, event: Any, arg: object) -> object:  # pragma: no cover
        if previous is not None:
            previous(f, event, arg)
        if event == "return" and f is frame:
            sys.setprofile(previous)
            callback()
        return None

    sys.setprofile(profile)  # pragma: no cover
    return True  # pragma: no cover


def patch_module(namespace: dict[str, Any], filename: str) -> int:
    """Give the functions of an already-executed module their rewritten code.

    Returns the number of functions patched. Functions whose code no longer
    matches the file on disk are left alone; their blocks raise when reached.
    """
    try:
        source = Path(filename).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0
    rewritten = rewrite_source(source, filename)
    if rewritten is None:
        return 0
    new_code, changed = rewritten
    try:
        original = compile(source, filename, "exec", dont_inherit=True)
    except SyntaxError:
        return 0
    namespace[RUNTIME] = _block
    patched = 0
    for fn in _functions(namespace, filename):
        if fn.__qualname__ not in changed:
            continue
        old = _find(original, fn.__code__)
        new = _find(new_code, fn.__code__)
        if old is None or new is None or _code_signature(old) != _code_signature(fn.__code__):
            continue
        try:
            fn.__code__ = new
        except ValueError:  # closure layout differs; leave it to raise loudly
            continue
        patched += 1
    return patched


def _find(module_code: types.CodeType, like: types.CodeType) -> types.CodeType | None:
    todo = [module_code]
    while todo:
        code = todo.pop()
        for const in code.co_consts:
            if isinstance(const, types.CodeType):
                if (
                    const.co_qualname == like.co_qualname
                    and const.co_firstlineno == like.co_firstlineno
                ):
                    return const
                todo.append(const)
    return None


def _functions(namespace: dict[str, Any], filename: str) -> Iterator[types.FunctionType]:
    """Functions defined in this file: module level, in classes, and under decorators."""
    seen: set[int] = set()
    todo: list[Any] = list(namespace.values())
    while todo:
        obj = todo.pop()
        if id(obj) in seen:
            continue
        seen.add(id(obj))
        if isinstance(obj, (staticmethod, classmethod)):
            todo.append(obj.__func__)
        elif isinstance(obj, property):
            todo.extend(f for f in (obj.fget, obj.fset, obj.fdel) if f is not None)
        elif isinstance(obj, type) and getattr(obj, "__module__", None) == namespace.get(
            "__name__"
        ):
            todo.extend(vars(obj).values())
        elif isinstance(obj, types.FunctionType):
            if obj.__code__.co_filename == filename:
                yield obj
            wrapped = getattr(obj, "__wrapped__", None)
            if wrapped is not None:
                todo.append(wrapped)


def _patch_quietly(namespace: dict[str, Any], filename: str) -> None:
    """Runs inside a monitoring callback: an exception here must not escape."""
    try:
        patch_module(namespace, filename)
    except Exception as exc:  # e.g. UnsupportedSyntax in a block: reported on use
        namespace.setdefault("__fastcircuit_error__", exc)


HINT = "__fastcircuit_hint__"  # why the script's blocks could not be rewritten


@dataclass(frozen=True)
class _Step:
    """A module on the import chain, re-run in its own namespace."""

    namespace: dict[str, Any]
    code: types.CodeType
    spec: Any  # importlib ModuleSpec, None for the script


def _schedule_running_modules() -> list[_Step] | None:
    """Arrange for executing modules to be rewritten; return the re-run plan, if any.

    ``import fastcircuit`` may run directly in the script, or deeper in a chain of
    imports (the script imports ``circuits``, which imports fastcircuit). If the
    script contains blocks, every module on that chain is re-run in its own
    namespace, innermost first, which finishes it; then the script is re-run.
    """
    frames = list(_executing_modules())  # innermost first
    main = getattr(sys.modules.get("__main__"), "__dict__", None)
    if frames and frames[-1].f_globals is main and _has_blocks(frames[-1]):
        plan = _rerun_plan(frames)
        if plan is not None:
            return plan
    for frame in frames:
        if _has_blocks(frame):
            frame.f_globals[RUNTIME] = _block
            callback = functools.partial(_patch_quietly, frame.f_globals, frame.f_code.co_filename)
            _when_finished(frame, callback)
    return None


def _has_blocks(frame: types.FrameType) -> bool:
    try:
        return mentions_blocks(Path(frame.f_code.co_filename).read_bytes())
    except OSError:
        return False


def _rerun_plan(frames: list[types.FrameType]) -> list[_Step] | None:
    """Steps to finish the import chain and re-run the script, or None.

    None when re-running is unsafe; then the reason is stored in the script's
    globals, where its first block's error finds it.
    """
    script = frames[-1]
    steps: list[_Step] = []
    for frame in frames:
        filename = frame.f_code.co_filename
        try:
            source = Path(filename).read_text(encoding="utf-8")
            tree = ast.parse(source, filename)
            original = compile(tree, filename, "exec", dont_inherit=True)
        except (OSError, UnicodeDecodeError, SyntaxError):
            return None  # e.g. a module created from a string: cannot be re-run
        module = sys.modules.get(frame.f_globals.get("__name__", ""))
        if _code_signature(original) != _code_signature(frame.f_code):
            problem: str | None = f"{filename} changed on disk after it started running."
        elif frame is not script and getattr(module, "__dict__", None) is not frame.f_globals:
            problem = f"{filename} is being imported in an unusual way."
        else:
            problem = _rerun_problem(tree, frame.f_lineno, filename, frame is script)
        if problem is not None:
            if frame is not script:
                problem += (
                    f" It is imported, directly or indirectly, by line {script.f_lineno} of "
                    f"the script; put 'import fastcircuit' above that line."
                )
            script.f_globals[HINT] = problem
            return None
        rewritten = rewrite_source(source, filename)  # UnsupportedSyntax: raised at import
        if frame is script and rewritten is None:
            return None  # mentions fastcircuit, but has no blocks: nothing to re-run
        frame.f_globals[RUNTIME] = _block
        spec = None if frame is script else getattr(module, "__spec__", None)
        steps.append(_Step(frame.f_globals, original if rewritten is None else rewritten[0], spec))
    return steps


def _rerun_problem(tree: ast.Module, lineno: int, filename: str, script: bool) -> str | None:
    """Why re-running this module from the top is unsafe, or None.

    The module is executing the top-level statement at ``lineno``. That statement
    and everything before it run again, so all of it must be safe to repeat:
    imports, docstrings, assignments of literal values, and ``if``/``try``
    statements made only of those (``try: import x`` / ``except ImportError``).
    """
    where = "the script" if script else filename
    for stmt in tree.body:
        current = stmt.lineno <= lineno <= (stmt.end_lineno or stmt.lineno)
        if current and not isinstance(stmt, (ast.Import, ast.ImportFrom, ast.If, ast.Try)):
            return (
                f"fastcircuit was imported by line {lineno} of {where}, which is not an "
                "import statement at its top level, so the script cannot be run again "
                "with its blocks compiled."
            )
        if not _repeatable(stmt):
            if script:
                return (
                    "To compile the blocks in the script, fastcircuit runs it again from "
                    "the top once fastcircuit is imported, so the lines before "
                    f"'import fastcircuit' run twice, and line {stmt.lineno} is not safe to "
                    "run twice. Only imports and assignments of literal values may come "
                    f"first: move 'import fastcircuit' above line {stmt.lineno}."
                )
            return (
                f"fastcircuit was first imported by {filename}, which would have to run "
                f"again, and its line {stmt.lineno} is not safe to run twice."
            )
        if current:
            return None
    return None


_CATCH_ALL = {"BaseException", "SystemExit"}  # would stop the original run from ending


def _repeatable(stmt: ast.stmt) -> bool:
    """Running ``stmt`` a second time has no effect the program can see."""
    if isinstance(stmt, (ast.Import, ast.ImportFrom, ast.Pass)):
        return True
    if isinstance(stmt, ast.Expr):  # docstrings and other bare strings
        return isinstance(stmt.value, ast.Constant)
    if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
        if stmt.value is None or not all(isinstance(t, ast.Name) for t in targets):
            return False
        try:
            ast.literal_eval(stmt.value)
        except (ValueError, TypeError, SyntaxError, RecursionError):
            return False
        return True
    if isinstance(stmt, ast.If):
        return not any(isinstance(n, (ast.Call, ast.NamedExpr)) for n in ast.walk(stmt.test)) and (
            all(map(_repeatable, stmt.body + stmt.orelse))
        )
    if isinstance(stmt, ast.Try):
        for handler in stmt.handlers:
            names = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
            if any(n is None or (isinstance(n, ast.Name) and n.id in _CATCH_ALL) for n in names):
                return False
            if not all(map(_repeatable, handler.body)):
                return False
        return all(map(_repeatable, stmt.body + stmt.orelse + stmt.finalbody))
    return False


def _mark_loaded(spec: Any) -> None:
    """Let other threads import this module without waiting for its import lock.

    The import that started it never returns: the program runs inside it.
    """
    if spec is not None:
        spec._initializing = False


def _rerun(plan: list[_Step]) -> NoReturn:
    """Finish the modules on the import chain, run the script again, end the program."""
    _mark_loaded(getattr(sys.modules.get("fastcircuit"), "__spec__", None))
    *chain, script = plan
    for step in chain:
        exec(step.code, step.namespace)  # noqa: S102
        _mark_loaded(step.spec)
    try:
        exec(script.code, script.namespace)  # noqa: S102
    except (SystemExit, KeyboardInterrupt):
        raise
    except BaseException as exc:
        # Report it as Python would: the traceback starts at this frame, and the
        # next entry is the script's own top level.
        # (The default excepthook prints exc.__traceback__, not its argument.)
        tb = exc.__traceback__
        if tb is not None:
            exc = exc.with_traceback(tb.tb_next)
        sys.excepthook(type(exc), exc, exc.__traceback__)
        raise SystemExit(1) from None
    raise SystemExit(0)


# -- 3. IPython -------------------------------------------------------------
class IPythonTransformer(ast.NodeTransformer):
    """Rewrites blocks in each cell; registered in ``shell.ast_transformers``."""

    def visit(self, node: ast.AST) -> ast.AST:
        if isinstance(node, ast.Module):
            return rewrite(node, "<cell>", _block.register).tree
        return node


def load_ipython_extension(shell: Any) -> None:
    if not any(isinstance(t, IPythonTransformer) for t in shell.ast_transformers):
        shell.ast_transformers.append(IPythonTransformer())
    shell.user_ns[RUNTIME] = _block


def _ipython_shell() -> Any:
    ipython = sys.modules.get("IPython")
    get = getattr(ipython, "get_ipython", None)
    return get() if get is not None else None


# -- entry points ---------------------------------------------------------
def activate() -> None:
    """Called once, when fastcircuit is first imported."""
    global _ACTIVE  # noqa: PLW0603
    if _ACTIVE:
        return
    _ACTIVE = True
    sys.meta_path.insert(0, _FINDER)
    rerun = _schedule_running_modules()
    shell = _ipython_shell()
    if shell is not None:
        load_ipython_extension(shell)
    if rerun is not None:
        _rerun(rerun)


def run_script(path: str, argv: list[str]) -> None:
    """``python -m fastcircuit run script.py args...``: rewrite the script, then run it."""
    activate()
    source = Path(path).read_text(encoding="utf-8")
    filename = str(Path(path).resolve())
    rewritten = rewrite_source(source, filename)
    code = (
        rewritten[0]
        if rewritten is not None
        else compile(source, filename, "exec", dont_inherit=True)
    )
    main = types.ModuleType("__main__")
    main.__file__ = filename
    main.__dict__.update({"__builtins__": builtins, RUNTIME: _block})
    sys.modules["__main__"] = main
    sys.argv = [path, *argv]
    sys.path[0] = str(Path(filename).parent)
    exec(code, main.__dict__)  # noqa: S102
