"""Compile Qiskit circuit-building code to native code.

Wrap the code in ``with fastcircuit:`` (or ``with fastcircuit.block(debug=True):``).
The block appends to an existing circuit. Blocks are rewritten when their module
is loaded; see ``_hooks`` for when that is possible.
"""

import sys
import types
from typing import Any

from ._version import __version__
from .errors import (
    BlockNotCompiledError,
    CacheError,
    CompilerNotFoundError,
    DuplicateQubitError,
    FastCircuitError,
    IncompatibleEnvironmentError,
    NativeBuildError,
    QubitIndexError,
    UnsupportedSyntax,
)

__all__ = [
    "BlockNotCompiledError",
    "CacheError",
    "CompilerNotFoundError",
    "DuplicateQubitError",
    "FastCircuitError",
    "IncompatibleEnvironmentError",
    "NativeBuildError",
    "QubitIndexError",
    "UnsupportedSyntax",
    "__version__",
    "block",
]


def _not_compiled() -> BlockNotCompiledError:
    """A block ran as plain Python: it was not rewritten. Never run it uncompiled."""
    frame = sys._getframe(2)
    where = f"{frame.f_code.co_filename}:{frame.f_lineno}"
    if frame.f_code.co_name == "<module>":
        how = (
            "A module that imports fastcircuit itself cannot have blocks at its top "
            "level, because it was compiled before fastcircuit was loaded. Move the "
            "block into a function, or import fastcircuit before importing this module."
        )
    else:
        how = (
            "This function ran before its module finished loading, or its module was "
            "loaded before fastcircuit was imported. Import fastcircuit before the "
            "module, or call the function after the module has loaded."
        )
    hint = frame.f_globals.get("__fastcircuit_hint__")
    if isinstance(hint, str):  # the script could not be re-run with its blocks compiled
        how = hint
    if sys.modules.get("IPython") is not None and frame.f_code.co_filename.startswith("<"):
        how = (
            "In IPython and Jupyter, blocks work in cells run after 'import fastcircuit' "
            "(not in the cell that imports it)."
        )
    cause = frame.f_globals.get("__fastcircuit_error__")
    if isinstance(cause, Exception):  # rewriting this module failed: that is the reason
        how = f"Rewriting its module failed:\n{cause}"
    error = BlockNotCompiledError(f"the fastcircuit block at {where} was not compiled. {how}")
    error.__cause__ = cause if isinstance(cause, Exception) else None
    return error


class _Block:
    """``fastcircuit.block(...)``: options for a block; only valid when rewritten."""

    def __init__(self, debug: bool) -> None:
        self.debug = debug

    def __enter__(self) -> None:
        raise _not_compiled()

    def __exit__(self, *exc: object) -> None:
        return None


def block(*, debug: bool = False) -> Any:
    """Options for a compiled block: ``with fastcircuit.block(debug=True):``.

    ``debug=True`` prints, to stderr, what was compiled for the block, with which
    backend and how long each step took.
    """
    return _Block(debug)


class _Module(types.ModuleType):
    """Lets the module itself be used as ``with fastcircuit:``."""

    def __enter__(self) -> None:
        raise _not_compiled()

    def __exit__(self, *exc: object) -> None:
        return None


sys.modules[__name__].__class__ = _Module


def load_ipython_extension(shell: Any) -> None:
    """``%load_ext fastcircuit``: rewrite blocks in later cells."""
    from ._hooks import load_ipython_extension as load  # noqa: PLC0415

    load(shell)


from ._hooks import activate as _activate  # noqa: E402

_activate()
