"""Public exception types.

Compile-time errors identify the block, source location, the rejected construct
and a likely correction. Runtime errors raised by compiled code name the source line
that failed. Several runtime errors subclass both Qiskit's ``CircuitError`` (which
the ordinary Python builder raises) and a built-in type, so existing ``except``
clauses for either keep working.
"""

from __future__ import annotations

from qiskit.circuit.exceptions import CircuitError

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
]


class FastCircuitError(Exception):
    """Base class for fastcircuit-specific errors."""


class UnsupportedSyntax(FastCircuitError, ValueError):
    """A block uses something outside the supported subset."""

    def __init__(
        self,
        message: str,
        *,
        function: str | None = None,
        filename: str | None = None,
        lineno: int | None = None,
        col: int | None = None,
        construct: str | None = None,
        hint: str | None = None,
    ) -> None:
        self.message = message
        self.function = function
        self.filename = filename
        self.lineno = lineno
        self.col = col
        self.construct = construct
        self.hint = hint
        super().__init__(self._format())

    def _format(self) -> str:
        where = ""
        if self.filename is not None and self.lineno is not None:
            where = f"{self.filename}:{self.lineno}"
            if self.col is not None:
                where += f":{self.col + 1}"
        head = "cannot compile"
        if self.function:
            head += f" {self.function!r}"
        if where:
            head += f" ({where})"
        text = f"{head}: {self.message}"
        if self.construct:
            text += f"\n    {self.construct}"
        if self.hint:
            text += f"\n  hint: {self.hint}"
        return text


class BlockNotCompiledError(FastCircuitError, RuntimeError):
    """A ``with fastcircuit:`` block was reached without having been rewritten."""


class IncompatibleEnvironmentError(FastCircuitError, RuntimeError):
    """Interpreter, platform or Qiskit version is outside the tested range."""


class CompilerNotFoundError(FastCircuitError, RuntimeError):
    """Native compilation is required but no usable C compiler/headers were found."""


class NativeBuildError(FastCircuitError, RuntimeError):
    """The C compiler rejected generated code (a fastcircuit bug) or failed to run."""


class CacheError(FastCircuitError, RuntimeError):
    """The persistent cache directory is unusable or unsafe."""


class QubitIndexError(CircuitError, IndexError):  # type: ignore[misc]
    """A qubit or clbit index was out of range at circuit-construction time."""


class DuplicateQubitError(CircuitError, ValueError):  # type: ignore[misc]
    """A multi-qubit gate received the same qubit more than once."""
