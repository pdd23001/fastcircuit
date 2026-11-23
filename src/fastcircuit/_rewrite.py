"""Import-time rewriting of ``with fastcircuit:`` blocks.

A ``with`` statement cannot skip its own body at run time, so blocks are replaced
in the syntax tree before the module is compiled, the same technique pytest uses
for assertion rewriting::

    with fastcircuit:                 __fastcircuit_blocks__.run(3, {"n": n,
        for i in range(n):     ──►        "qc": qc, "range": range})
            qc.h(i)

The runtime (``_block``) compiles the original statements, specialized on the
types of the values passed in, and runs them against the circuit. Every rule that
can be checked without those values is checked here, so mistakes fail at import.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass

from .errors import UnsupportedSyntax

RUNTIME = "__fastcircuit_blocks__"  # module global bound to fastcircuit._block
MODULE = "fastcircuit"
OPTIONS = ("debug",)

_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


@dataclass(frozen=True)
class BlockSite:
    filename: str
    lineno: int
    col: int
    body: tuple[ast.stmt, ...]
    names: tuple[str, ...]  # free names the block reads, passed in at run time
    receivers: frozenset[str]  # names used as `name.method(...)` (the circuit candidates)
    scope: str  # qualified name of the enclosing function, or "<module>"
    debug: bool

    @property
    def qualname(self) -> str:
        return f"{self.scope}.<block line {self.lineno}>"


def mentions_blocks(source: str | bytes) -> bool:
    """Cheap pre-filter before parsing: can this source contain a block at all?"""
    if isinstance(source, bytes):
        return b"fastcircuit" in source
    return "fastcircuit" in source


def aliases(tree: ast.AST) -> tuple[set[str], set[str]]:
    """Names bound to the fastcircuit module, and to ``fastcircuit.block``."""
    modules: set[str] = set()
    blocks: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == MODULE:
                    modules.add(alias.asname or MODULE)
        elif isinstance(node, ast.ImportFrom) and node.module == MODULE and node.level == 0:
            for alias in node.names:
                if alias.name == "block":
                    blocks.add(alias.asname or "block")
    return modules, blocks


class _Rewriter(ast.NodeTransformer):
    def __init__(
        self,
        tree: ast.Module,
        filename: str,
        register: Callable[[BlockSite], int],
    ) -> None:
        self.tree = tree
        self.filename = filename
        self.register = register
        self.modules, self.blocks = aliases(tree)
        self.stack: list[ast.AST] = []  # enclosing function/class nodes
        self.qualnames: list[str] = []
        self.changed: set[str] = set()  # qualnames of functions containing a block
        self.count = 0

    # -- scopes ------------------------------------------------------------
    def _scoped(self, node: ast.AST, name: str) -> ast.AST:
        parent = self.qualnames[-1] if self.qualnames else ""
        if parent and isinstance(self.stack[-1], (ast.FunctionDef, ast.AsyncFunctionDef)):
            parent += ".<locals>"
        self.stack.append(node)
        self.qualnames.append(f"{parent}.{name}" if parent else name)
        try:
            return self.generic_visit(node)
        finally:
            self.stack.pop()
            self.qualnames.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        return self._scoped(node, node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        return self._scoped(node, node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
        return self._scoped(node, node.name)

    def visit_Lambda(self, node: ast.Lambda) -> ast.AST:
        return self._scoped(node, "<lambda>")

    # -- blocks ------------------------------------------------------------
    def fail(self, node: ast.AST, message: str, hint: str | None = None) -> UnsupportedSyntax:
        scope = self.qualnames[-1] if self.qualnames else "<module>"
        return UnsupportedSyntax(
            message,
            function=f"{scope}.<block line {getattr(node, 'lineno', '?')}>",
            filename=self.filename,
            lineno=getattr(node, "lineno", None),
            col=getattr(node, "col_offset", None),
            hint=hint,
        )

    def block_options(self, node: ast.With) -> dict[str, bool] | None:
        """``None`` if this ``with`` is not a fastcircuit block, else its options."""
        exprs = [item.context_expr for item in node.items]
        ours = [e for e in exprs if self.is_marker(e)]
        if not ours:
            return None
        if len(node.items) != 1:
            raise self.fail(node, "a fastcircuit block cannot be combined with other contexts")
        if node.items[0].optional_vars is not None:
            raise self.fail(node, "'with fastcircuit as ...' is not supported")
        expr = exprs[0]
        if not isinstance(expr, ast.Call):
            return {}
        if expr.args:
            raise self.fail(expr, "fastcircuit.block() takes keyword arguments only")
        options: dict[str, bool] = {}
        for kw in expr.keywords:
            if kw.arg not in OPTIONS:
                raise self.fail(kw, f"unknown option {kw.arg!r}", "supported: debug=True")
            if not (isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, bool)):
                raise self.fail(kw, f"option {kw.arg!r} must be the literal True or False")
            options[kw.arg] = kw.value.value
        return options

    def is_marker(self, expr: ast.expr) -> bool:
        if isinstance(expr, ast.Name):
            return expr.id in self.modules
        if not isinstance(expr, ast.Call):
            return False
        func = expr.func
        if isinstance(func, ast.Name):
            return func.id in self.blocks
        return (
            isinstance(func, ast.Attribute)
            and func.attr == "block"
            and isinstance(func.value, ast.Name)
            and func.value.id in self.modules
        )

    def visit_With(self, node: ast.With) -> ast.AST:
        options = self.block_options(node)
        if options is None:
            return self.generic_visit(node)
        # Inner blocks are left alone: the compiler rejects a nested 'with'.
        stored, loaded, receivers = _block_names(node.body)
        for inner in _walk_block(node.body):
            if isinstance(inner, (ast.Return, ast.Yield, ast.YieldFrom, ast.Await)):
                raise self.fail(inner, "a fastcircuit block cannot return, yield or await")
            if isinstance(inner, (ast.Global, ast.Nonlocal)):
                raise self.fail(inner, "'global' and 'nonlocal' are not supported in a block")
        scope_node: ast.AST = self.stack[-1] if self.stack else self.tree
        if isinstance(scope_node, ast.ClassDef):
            raise self.fail(node, "fastcircuit blocks are not supported directly in a class body")
        outside = _names_outside(scope_node, node)
        clash = sorted(stored & outside)
        if clash:
            raise self.fail(
                node,
                f"variable {clash[0]!r} is assigned inside the block and also used outside it",
                "a block cannot change variables of the surrounding code; use a "
                "different name inside the block",
            )
        names = tuple(sorted(loaded - stored))
        scope = self.qualnames[-1] if self.qualnames else "<module>"
        site = BlockSite(
            filename=self.filename,
            lineno=node.lineno,
            col=node.col_offset,
            body=tuple(node.body),
            names=names,
            receivers=frozenset(receivers & set(names)),
            scope=scope,
            debug=options.get("debug", False),
        )
        site_id = self.register(site)
        self.count += 1
        for depth, scope_ast in enumerate(self.stack):
            if isinstance(scope_ast, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.changed.add(self.qualnames[depth])
        call = ast.Call(
            func=ast.Attribute(
                value=ast.Name(id=RUNTIME, ctx=ast.Load()), attr="run", ctx=ast.Load()
            ),
            args=[
                ast.Constant(site_id),
                ast.Dict(
                    keys=[ast.Constant(n) for n in names],
                    values=[ast.Name(id=n, ctx=ast.Load()) for n in names],
                ),
            ],
            keywords=[],
        )
        replacement = ast.Expr(call)
        ast.copy_location(replacement, node)
        ast.fix_missing_locations(replacement)
        for sub in ast.walk(replacement):  # tracebacks point at the 'with' line
            sub.lineno = node.lineno  # type: ignore[attr-defined]
            sub.end_lineno = node.lineno  # type: ignore[attr-defined]
        return replacement


def _walk_block(body: list[ast.stmt]) -> list[ast.AST]:
    """Every node of the block, not descending into nested scopes."""
    out: list[ast.AST] = []
    todo: list[ast.AST] = list(body)
    while todo:
        node = todo.pop()
        out.append(node)
        if not isinstance(node, _SCOPES):
            todo.extend(ast.iter_child_nodes(node))
    return out


def _block_names(body: list[ast.stmt]) -> tuple[set[str], set[str], set[str]]:
    stored: set[str] = set()
    loaded: set[str] = set()
    receivers: set[str] = set()
    for node in _walk_block(body):
        if isinstance(node, ast.Name):
            (loaded if isinstance(node.ctx, ast.Load) else stored).add(node.id)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
        ):
            receivers.add(node.func.value.id)
    return stored, loaded, receivers


def _names_outside(scope: ast.AST, block: ast.With) -> set[str]:
    """Every name used in ``scope`` outside ``block`` (conservative: nested scopes too)."""
    names: set[str] = set()
    todo: list[ast.AST] = [scope]
    while todo:
        node = todo.pop()
        if node is block:
            continue
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        todo.extend(ast.iter_child_nodes(node))
    return names


@dataclass(frozen=True)
class Rewritten:
    tree: ast.Module
    blocks: int  # number of blocks replaced
    functions: frozenset[str]  # qualnames of functions that contain a block


def rewrite(tree: ast.Module, filename: str, register: Callable[[BlockSite], int]) -> Rewritten:
    """Replace every block in ``tree`` (in place) with a call to the runtime."""
    rewriter = _Rewriter(tree, filename, register)
    if rewriter.modules or rewriter.blocks:
        rewriter.visit(tree)
    return Rewritten(tree, rewriter.count, frozenset(rewriter.changed))
