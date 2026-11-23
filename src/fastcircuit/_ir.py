"""Typed intermediate representation of a compiled ``with fastcircuit:`` block.

The analyzer produces this after all name resolution, typing and scope checks, so
backends never consult Python objects or the AST. Types are ``"int"`` (checked
int64), ``"float"`` (Python float), ``"float64"`` (``np.float64``: the same values
with NumPy's error handling), ``"anyfloat"`` (a local holding a float on some paths
and an ``np.float64`` on others; backends track which at run time), ``"int64"``
(``np.int64``: wraps on overflow, NumPy's error handling), ``"bool"`` (conditions
only) and the array kinds in ``ARRAYS`` (read-only C-contiguous buffers, arguments
only).
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from typing import Literal

Type = Literal[
    "int", "float", "float64", "anyfloat", "int64", "bool", "array", "array2", "intarray",
    "intarray2",
]  # fmt: skip
FLOATS = ("float", "float64", "anyfloat")  # types whose values are C doubles
INTS = ("int", "int64")  # types whose values are C long longs
# array kind -> (element type, number of dimensions)
ARRAYS: dict[str, tuple[Type, int]] = {
    "array": ("float64", 1),
    "array2": ("float64", 2),
    "intarray": ("int64", 1),
    "intarray2": ("int64", 2),
}
BinOpName = Literal["add", "sub", "mul", "truediv", "floordiv", "mod", "pow"]
CmpOpName = Literal["lt", "le", "gt", "ge", "eq", "ne"]
VarKind = Literal["arg", "local", "loop"]


@dataclass(frozen=True)
class Const:
    type: Type
    value: int | float


@dataclass(frozen=True)
class Var:
    kind: VarKind
    name: str
    type: Type


@dataclass(frozen=True)
class ToFloat:
    """Python ``float(x)``: exact for int64 and ``np.float64`` operands."""

    operand: Expr
    type: Type = "float"


@dataclass(frozen=True)
class Unary:
    op: Literal["neg", "not"]
    operand: Expr
    type: Type
    line: int = 0


@dataclass(frozen=True)
class Binary:
    op: BinOpName
    left: Expr
    right: Expr
    type: Type
    line: int


@dataclass(frozen=True)
class Compare:
    """Chained comparison ``a op b op c``; middle operands evaluated once."""

    ops: tuple[CmpOpName, ...]
    operands: tuple[Expr, ...]
    type: Type = "bool"


@dataclass(frozen=True)
class BoolOp:
    op: Literal["and", "or"]
    values: tuple[Expr, ...]
    type: Type = "bool"


@dataclass(frozen=True)
class Truth:
    """Python truthiness of a numeric value (only in condition positions)."""

    operand: Expr
    type: Type = "bool"


@dataclass(frozen=True)
class IfExp:
    test: Expr
    body: Expr
    orelse: Expr
    type: Type


@dataclass(frozen=True)
class Index:
    """``a[i]``, ``a[i, j]`` (all indices evaluated, then checked) or ``a[i][j]``
    (``chained``: ``i`` checked before ``j`` is evaluated)."""

    array: Var
    indices: tuple[Expr, ...]
    line: int
    type: Type
    chained: bool = False


@dataclass(frozen=True)
class Len:
    """``len(a)``, or ``a.shape[axis]``."""

    array: Var
    axis: int = 0
    type: Type = "int"


@dataclass(frozen=True)
class Call:
    """Builtin/math call with Python semantics: abs min max int float math.*."""

    func: str
    args: tuple[Expr, ...]
    type: Type
    line: int


@dataclass(frozen=True)
class UFunc:
    """A NumPy math function (``_npufunc.UFUNCS[index]``) on doubles; np.float64 result."""

    index: int
    name: str
    args: tuple[Expr, ...]
    line: int
    type: Type = "float64"


Expr = (
    Const
    | Var
    | ToFloat
    | Unary
    | Binary
    | Compare
    | BoolOp
    | Truth
    | IfExp
    | Index
    | Len
    | Call
    | UFunc
)


@dataclass(frozen=True)
class For:
    var: str
    start: Expr
    stop: Expr
    step: Expr
    body: tuple[Stmt, ...]
    line: int


@dataclass(frozen=True)
class If:
    test: Expr
    body: tuple[Stmt, ...]
    orelse: tuple[Stmt, ...]
    line: int


@dataclass(frozen=True)
class Gate:
    name: str
    enum: str
    params: tuple[Expr, ...]  # all float-typed
    qubits: tuple[Expr, ...]  # all int-typed
    line: int


@dataclass(frozen=True)
class Measure:
    qubit: Expr
    clbit: Expr
    line: int


@dataclass(frozen=True)
class Reset:
    qubit: Expr
    line: int


@dataclass(frozen=True)
class Assign:
    target: Var
    value: Expr
    line: int


Stmt = For | If | Gate | Measure | Reset | Assign


@dataclass(frozen=True)
class Param:
    name: str
    type: Type


@dataclass(frozen=True)
class Function:
    """One compiled block: it appends to a circuit passed in at run time, after
    the parameters' values (the block's variables) have been checked."""

    name: str
    qualname: str
    filename: str
    params: tuple[Param, ...]
    body: tuple[Stmt, ...]
    locals: dict[str, Type] = field(default_factory=dict)
    line: int = 0


def uses(fn: Function, cls: type) -> bool:
    """Whether any node in ``fn``'s body is a ``cls``."""

    def walk(node: object) -> bool:
        if isinstance(node, cls):
            return True
        if isinstance(node, tuple):
            return any(walk(n) for n in node)
        if is_dataclass(node):
            return any(walk(getattr(node, f.name)) for f in fields(node))
        return False

    return walk(fn.body)
