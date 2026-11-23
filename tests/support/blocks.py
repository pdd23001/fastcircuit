"""Test helper: run a circuit-building function's body as a ``with fastcircuit:`` block.

Most compiler tests are written as plain Python functions::

    @as_block
    def build(n: int, theta: float):
        qc = QuantumCircuit(n)      # created in Python
        for i in range(n):          # everything in between becomes the block
            qc.rx(theta, i)
        return qc                   # returned in Python

``as_block`` rewrites the function, in its own file and at its own line numbers,
into the same code with the middle statements inside ``with fastcircuit:``, exactly
as fastcircuit's import hook would. ``build.python`` is the original function, the
reference every test compares against.

The block is also analyzed immediately, with the types it would see at run time
(parameters from their annotations, other names from their current values), so
unsupported code is reported when the test module loads, as a block's
``UnsupportedSyntax`` would be on its first run.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
import types
from collections.abc import Callable
from typing import Annotated, Any, get_origin

import numpy as np

from fastcircuit import _block
from fastcircuit import _ir as ir
from fastcircuit._analysis import Analyzer
from fastcircuit._rewrite import RUNTIME, BlockSite, rewrite
from fastcircuit._source import MISSING, FunctionSource, resolve_name

_KINDS: dict[Any, str] = {
    int: "int",
    float: "float",
    np.float64: "float64",
    np.int64: "int64",
    np.ndarray: "array",
}
# Annotations for the other array kinds (a plain np.ndarray is 1-D float64).
Array2 = Annotated[np.ndarray, "array2"]
IntArray = Annotated[np.ndarray, "intarray"]
IntArray2 = Annotated[np.ndarray, "intarray2"]


def _kind(annotation: Any) -> str | None:
    if get_origin(annotation) is Annotated:
        return str(annotation.__metadata__[0])
    return _KINDS.get(annotation)


class BlockFunction:
    """The rewritten function, plus what was compiled for its block."""

    def __init__(
        self,
        fn: Callable[..., Any],
        python: Callable[..., Any],
        sites: list[int],
        program: ir.Function | None,
    ):
        self._fn = fn
        self.python = python
        self.sites = sites
        self.program = program  # the IR from the eager analysis (None without a block)
        self.__name__ = python.__name__
        self.__qualname__ = python.__qualname__

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._fn(*args, **kwargs)

    @property
    def compiled(self) -> _block.Compiled:
        """The most recent specialization of the block (it must have run)."""
        found = [c for site_id in self.sites for c in _specializations(site_id)]
        if not found:
            raise AssertionError(f"the block in {self.__qualname__} has not run yet")
        return found[-1]

    @property
    def backend(self) -> str:
        return self.compiled.backend

    @property
    def timings(self) -> dict[str, Any]:
        return self.compiled.timings

    @property
    def c_source(self) -> str:
        return self.compiled.c_source

    @property
    def llvm_ir(self) -> str | None:
        return self.compiled.llvm_ir

    @property
    def cache_key(self) -> str:
        return self.compiled.key


def _specializations(site_id: int) -> list[_block.Compiled]:
    state = _block._STATES.get(site_id)
    return list(state.compiled.values()) if state is not None else []


def as_block(fn: Callable[..., Any]) -> BlockFunction:
    assert isinstance(fn, types.FunctionType)
    filename = fn.__code__.co_filename
    lines, first = inspect.getsourcelines(fn)
    tree = ast.parse(textwrap.dedent("".join(lines)))
    ast.increment_lineno(tree, first - 1)
    func = tree.body[0]
    assert isinstance(func, ast.FunctionDef), "as_block needs a 'def' function"
    func.decorator_list = []
    body = list(func.body)
    doc = body[:1] if _is_docstring(body[0]) else []
    create, *middle, ret = body[len(doc) :]
    assert isinstance(create, ast.Assign) and isinstance(create.targets[0], ast.Name)
    assert isinstance(ret, ast.Return), "as_block functions end with 'return qc'"
    circuit = create.targets[0].id

    sites: list[int] = []
    found: list[BlockSite] = []

    def register(site: BlockSite) -> int:
        site_id = _block.register(site)
        sites.append(site_id)
        found.append(site)
        return site_id

    if middle:
        block = ast.With(items=[ast.withitem(ast.Name("fastcircuit", ast.Load()))], body=middle)
        ast.copy_location(block, middle[0])
        func.body = [*doc, create, block, ret]
    module = ast.Module([ast.Import([ast.alias("fastcircuit")]), func], [])
    ast.fix_missing_locations(module)
    rewrite(module, filename, register)  # the import-time checks of a real block
    program = _analyze(fn, found[0], circuit) if found else None  # first-run checks, now
    namespace = dict(fn.__globals__)
    namespace[RUNTIME] = _block
    exec(compile(module, filename, "exec", dont_inherit=True), namespace)  # noqa: S102
    return BlockFunction(namespace[func.name], fn, sites, program)


def _analyze(fn: types.FunctionType, site: BlockSite, circuit: str) -> ir.Function:
    annotations = inspect.get_annotations(fn, eval_str=True)
    params = set(inspect.signature(fn).parameters)
    kinds: dict[str, str] = {}
    for name in site.names:
        if name == circuit:
            continue
        if name in params:
            kind = _kind(annotations.get(name))
        else:
            value = resolve_name(fn, name)
            kind = None if value is MISSING else _KINDS.get(type(value))
        if kind is not None:
            kinds[name] = kind
    node = ast.FunctionDef(
        name="block",
        args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=list(site.body),
        decorator_list=[],
        lineno=site.lineno,
        col_offset=site.col,
    )
    src = FunctionSource(fn, node, site.filename, site.qualname, 0)
    return Analyzer(src, kinds).run_block(circuit, list(site.body), site.lineno)  # type: ignore[arg-type]


def _is_docstring(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )
