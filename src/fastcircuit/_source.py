"""Where compiled code comes from, for diagnostics, and how its free names resolve.

``FunctionSource`` carries a block's statements, its location (for error
messages) and a function object whose globals hold the values the block's free
names had (modules, builtins), so names resolve the way Python would.
"""

from __future__ import annotations

import ast
import builtins
import types
from dataclasses import dataclass
from typing import Any

from .errors import UnsupportedSyntax

MISSING: Any = object()


@dataclass(frozen=True)
class FunctionSource:
    fn: types.FunctionType
    node: ast.FunctionDef  # line numbers are absolute file lines
    filename: str
    qualname: str
    col_offset: int  # indentation removed by dedent, re-added to diagnostics

    def error(
        self, node: ast.AST | None, message: str, hint: str | None = None
    ) -> UnsupportedSyntax:
        lineno = getattr(node, "lineno", None)
        col = getattr(node, "col_offset", None)
        construct = None
        if node is not None and not isinstance(node, (ast.FunctionDef, ast.arguments)):
            construct = ast.unparse(node)
            if len(construct) > 200:
                construct = construct[:197] + "..."
        return UnsupportedSyntax(
            message,
            function=self.qualname,
            filename=self.filename,
            lineno=lineno,
            col=None if col is None else col + self.col_offset,
            construct=construct,
            hint=hint,
        )


def _code_signature(code: types.CodeType) -> tuple[Any, ...]:
    consts = tuple(
        ("<code>", c.co_name) if isinstance(c, types.CodeType) else (type(c).__name__, repr(c))
        for c in code.co_consts
    )
    return (
        code.co_code,
        consts,
        code.co_names,
        code.co_varnames,
        code.co_freevars,
        code.co_argcount,
        code.co_kwonlyargcount,
        code.co_posonlyargcount,
    )


def resolve_name(fn: types.FunctionType, name: str) -> Any:
    """Resolve a free name the way the function would: closure, globals, builtins."""
    code = fn.__code__
    if name in code.co_freevars and fn.__closure__ is not None:
        cell = fn.__closure__[code.co_freevars.index(name)]
        try:
            return cell.cell_contents
        except ValueError:
            return MISSING
    if name in fn.__globals__:
        return fn.__globals__[name]
    fn_builtins = getattr(fn, "__builtins__", None)
    if fn_builtins is None:
        fn_builtins = fn.__globals__.get("__builtins__", builtins)
    if isinstance(fn_builtins, types.ModuleType):
        fn_builtins = fn_builtins.__dict__
    return fn_builtins.get(name, MISSING)


def _resolve_expr(fn: types.FunctionType, node: ast.expr) -> Any:
    """Resolve ``name`` or ``name.attr.attr`` expressions at compile time."""
    if isinstance(node, ast.Name):
        return resolve_name(fn, node.id)
    if isinstance(node, ast.Attribute):
        base = _resolve_expr(fn, node.value)
        if base is MISSING:
            return MISSING
        return getattr(base, node.attr, MISSING)
    return MISSING


resolve_expr = _resolve_expr
