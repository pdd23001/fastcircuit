"""Syntax validation, scope rules and type analysis: Python AST -> typed IR.

Everything not explicitly accepted here is rejected with a located diagnostic.
There is no fallback to executing Python.
"""

from __future__ import annotations

import ast
import builtins
import math
from typing import Any, NoReturn

import numpy as np

from . import _ir as ir
from . import _npufunc
from ._gates import GATES
from ._source import MISSING, FunctionSource, resolve_expr, resolve_name

INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1

_BINOPS: dict[type[ast.operator], ir.BinOpName] = {
    ast.Add: "add",
    ast.Sub: "sub",
    ast.Mult: "mul",
    ast.Div: "truediv",
    ast.FloorDiv: "floordiv",
    ast.Mod: "mod",
    ast.Pow: "pow",
}
_CMPOPS: dict[type[ast.cmpop], ir.CmpOpName] = {
    ast.Lt: "lt",
    ast.LtE: "le",
    ast.Gt: "gt",
    ast.GtE: "ge",
    ast.Eq: "eq",
    ast.NotEq: "ne",
}

_SUPPORTED_HINT = (
    "supported statements are numeric assignments, gate/measure/reset calls on the "
    "circuit, 'for ... in range(...)', 'if'/'elif'/'else' and 'pass'; see "
    "https://github.com/pdd23001/fastcircuit/blob/main/docs/supported-syntax.md"
)

# math functions of one float argument, with CPython's error rules: a NaN result
# from a non-NaN argument raises ValueError; an infinite result from a finite
# argument raises OverflowError if the function "can overflow", else ValueError.
MATH_1 = {
    "sqrt": False,
    "cbrt": False,
    "sin": False,
    "cos": False,
    "tan": False,
    "asin": False,
    "acos": False,
    "atan": False,
    "sinh": True,
    "cosh": True,
    "tanh": False,
    "asinh": False,
    "acosh": False,
    "atanh": False,
    "exp": True,
    "exp2": True,
    "expm1": True,
    "log": False,
    "log2": False,
    "log10": False,
    "log1p": False,
    "fabs": False,
}
# math functions of two float arguments (CPython's math_2 rules: a NaN result from
# non-NaN arguments raises ValueError, an infinite one from finite arguments
# OverflowError).
MATH_2 = ("atan2", "copysign", "fmod")
MATH_TO_INT = ("floor", "ceil", "trunc")
# math.degrees/radians multiply by these constants (CPython's radToDeg/degToRad).
MATH_SCALE = {"degrees": 180.0 / math.pi, "radians": math.pi / 180.0}
# Recognized by identity, so 'from math import sqrt' and 'import math as m' work.
MATH_FUNCTIONS: dict[int, tuple[Any, str]] = {
    id(f): (f, name)
    for name in (*MATH_1, *MATH_2, *MATH_TO_INT, *MATH_SCALE)
    for f in (getattr(math, name),)
}
MATH_CONSTANTS = ("pi", "e", "tau")
NUMPY_CONSTANTS = ("pi", "e")

FLOATS = ir.FLOATS
INTS = ir.INTS
NUMERIC = (*INTS, *FLOATS)
_SHOWN = {
    "float64": "np.float64",
    "anyfloat": "float or np.float64",
    "int64": "np.int64",
    "array": "1-D float64 array",
    "array2": "2-D float64 array",
    "intarray": "1-D int64 array",
    "intarray2": "2-D int64 array",
}


def shown(t: str) -> str:
    """A type as users know it."""
    return _SHOWN.get(t, t)


def float_result(a: ir.Type, b: ir.Type) -> ir.Type:
    """Type of float arithmetic: np.float64 wins over float, as in NumPy."""
    if "float64" in (a, b):
        return "float64"
    return "anyfloat" if "anyfloat" in (a, b) else "float"


def join(a: ir.Type, b: ir.Type) -> ir.Type | None:
    """Type of a local assigned values of types a and b; None if not allowed."""
    if a == b:
        return a
    return "anyfloat" if a in FLOATS and b in FLOATS else None


class Analyzer:
    def __init__(self, src: FunctionSource, types: dict[str, ir.Type]) -> None:
        self.src = src
        self.fn = src.fn
        self.args = types
        self.loops: list[str] = []  # active loop variables, innermost last
        self.circuit = ""
        self.local_names: set[str] = set()  # every name assigned anywhere (function scope)
        self.loop_names: set[str] = set()  # every name used as a loop target
        self.local_types: dict[str, ir.Type] = {}
        self.assigned: set[str] = set()  # locals definitely assigned at this point

    # -- diagnostics -------------------------------------------------------
    def fail(self, node: ast.AST | None, message: str, hint: str | None = None) -> NoReturn:
        raise self.src.error(node, message, hint)

    # -- names -------------------------------------------------------------
    def is_local_name(self, name: str) -> bool:
        """Names Python would treat as local to the function."""
        return (
            name in self.args
            or name in self.local_names
            or name in self.loop_names
            or name == self.circuit
        )

    def is_builtin(self, name: str) -> bool:
        if self.is_local_name(name):
            return False
        builtin = getattr(builtins, name, MISSING)
        return builtin is not MISSING and resolve_name(self.fn, name) is builtin

    def global_object(self, node: ast.expr) -> Any:
        """Resolve a free name/attribute chain (math, np.pi...) at compile time."""
        root = node
        while isinstance(root, ast.Attribute):
            root = root.value
        if not isinstance(root, ast.Name) or self.is_local_name(root.id):
            return MISSING
        return resolve_expr(self.fn, node)

    # -- entry point -------------------------------------------------------
    def run_block(self, circuit: str, body: list[ast.stmt], line: int) -> ir.Function:
        """A 'with fastcircuit:' block: statements appended to an existing circuit.

        Every free name of the block is a parameter (``self.args``) or resolved
        through ``self.fn``; the circuit is passed in at run time, so its sizes are
        unknown here and every index is checked against the real circuit.
        """
        self.circuit = circuit
        self.collect_names(body)
        # A local assigned both a float and an np.float64 becomes "anyfloat"; that
        # changes the type of earlier uses, so analyse again until types are stable.
        while True:
            self.widened = False
            self.assigned = set()
            stmts = self.statements(body)
            if not self.widened:
                break
        return ir.Function(
            name=self.fn.__name__,
            qualname=self.src.qualname,
            filename=self.src.filename,
            params=tuple(ir.Param(n, t) for n, t in self.args.items()),
            body=stmts,
            locals=dict(self.local_types),
            line=line,
        )

    def collect_names(self, nodes: list[ast.stmt]) -> None:
        """Python scoping is per function: find every assigned name up front."""
        for stmt in nodes:
            for node in ast.walk(stmt):
                if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target, ast.Name):
                            self.check_assignable(target.id, target)
                            if target.id not in self.args:
                                self.local_names.add(target.id)
                elif isinstance(node, ast.For) and isinstance(node.target, ast.Name):
                    self.loop_names.add(node.target.id)
        clash = self.local_names & self.loop_names
        if clash:
            name = sorted(clash)[0]
            self.fail(
                self.src.node,
                f"{name!r} is used both as a loop variable and as an assigned variable",
                "use distinct names for loop variables",
            )

    def check_assignable(self, name: str, node: ast.AST) -> None:
        if name == self.circuit:
            self.fail(node, "the circuit variable cannot be reassigned")
        if self.args.get(name) in ir.ARRAYS:
            self.fail(node, f"array parameter {name!r} cannot be reassigned")

    # -- statements --------------------------------------------------------
    def statements(self, nodes: list[ast.stmt]) -> tuple[ir.Stmt, ...]:
        out: list[ir.Stmt] = []
        for node in nodes:
            stmt = self.statement(node)
            if stmt is not None:
                out.append(stmt)
        return tuple(out)

    def statement(self, node: ast.stmt) -> ir.Stmt | None:
        if isinstance(node, ast.For):
            return self.for_loop(node)
        if isinstance(node, ast.If):
            return self.if_stmt(node)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            return self.circuit_call(node.value)
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            return self.assign(node)
        if isinstance(node, ast.AugAssign):
            return self.aug_assign(node)
        if isinstance(node, ast.Pass):
            return None
        if isinstance(node, ast.Return):
            self.fail(node, "'return' is only supported as the final statement")
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            return None  # stray string/number expression: no effect
        self.fail(node, f"unsupported statement ({type(node).__name__})", _SUPPORTED_HINT)

    def assign_target(self, target: ast.expr, value: ir.Expr, node: ast.stmt) -> ir.Assign:
        if not isinstance(target, ast.Name):
            self.fail(target, "only assignment to a single name is supported")
        name = target.id
        if name in self.loops or name in self.loop_names:
            self.fail(target, f"loop variable {name!r} cannot be assigned")
        if value.type not in NUMERIC:
            self.fail(node, "only int and float values can be assigned")
        if name in self.args:
            expected = self.args[name]
            kind: ir.VarKind = "arg"
            ok = value.type == expected
        else:
            kind = "local"
            current = self.local_types.setdefault(name, value.type)
            expected = join(current, value.type) or current
            ok = join(current, value.type) is not None
            if expected != current:
                self.local_types[name] = expected
                self.widened = True
        if not ok:
            self.fail(
                node,
                f"{name!r} is {shown(expected)} elsewhere but this assigns a {shown(value.type)}",
                "a variable keeps one type; convert explicitly with float(...) or int(...)",
            )
        self.assigned.add(name)
        return ir.Assign(ir.Var(kind, name, expected), value, node.lineno)

    def assign(self, node: ast.Assign | ast.AnnAssign) -> ir.Assign:
        if isinstance(node, ast.Assign):
            if len(node.targets) != 1:
                self.fail(node, "chained assignment is not supported")
            target = node.targets[0]
        else:
            target = node.target
        if not isinstance(target, ast.Name):
            self.fail(target, "only assignment to a single name is supported")
        if node.value is None:
            self.fail(node, "annotation without a value is not supported")
        value = self.numeric(node.value)
        if isinstance(node, ast.AnnAssign):
            annotation = resolve_expr(self.fn, node.annotation)
            declared = {int: "int", float: "float"}.get(annotation)
            if declared is None:
                self.fail(node.annotation, "local annotations must be int or float")
            if declared != value.type:
                self.fail(
                    node,
                    f"annotated {declared} but the value is {shown(value.type)}",
                    "Python does not convert on annotated assignment; write e.g. 0.0 "
                    "for a float or use float(...)",
                )
        return self.assign_target(target, value, node)

    def aug_assign(self, node: ast.AugAssign) -> ir.Assign:
        if not isinstance(node.target, ast.Name):
            self.fail(node.target, "only assignment to a single name is supported")
        current = self.name(
            ast.Name(node.target.id, ast.Load(), lineno=node.lineno, col_offset=node.col_offset)
        )
        value = self.binary(type(node.op), current, self.numeric(node.value), node, node.value)
        return self.assign_target(node.target, value, node)

    def for_loop(self, node: ast.For) -> ir.For:
        call = node.iter
        if node.orelse:
            self.fail(node, "'for ... else' is not supported")
        if not isinstance(node.target, ast.Name):
            self.fail(node.target, "loop target must be a single name")
        target = node.target.id
        if not (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "range"
            and self.is_builtin("range")
        ):
            self.fail(
                node.iter,
                "only 'for name in range(...)' loops are supported",
                "iterate over indices, e.g. 'for i in range(len(angles))'",
            )
        if call.keywords or not 1 <= len(call.args) <= 3:
            self.fail(call, "range() takes one to three positional integer arguments")
        if target in self.args or target == self.circuit or target in self.loops:
            self.fail(
                node.target,
                f"loop variable {target!r} must be a fresh name",
                "it may not reuse a parameter, the circuit or an enclosing loop variable",
            )
        values = [self.integer(a) for a in call.args]
        one: ir.Expr = ir.Const("int", 1)
        start: ir.Expr
        stop: ir.Expr
        step: ir.Expr
        if len(values) == 1:
            start, stop, step = ir.Const("int", 0), values[0], one
        else:
            start, stop = values[0], values[1]
            step = values[2] if len(values) == 3 else one
        before = set(self.assigned)
        self.loops.append(target)
        try:
            body = self.statements(node.body)
        finally:
            self.loops.pop()
        # The loop may run zero times: nothing assigned inside is definite after it.
        self.assigned = before
        return ir.For(target, start, stop, step, body, node.lineno)

    def if_stmt(self, node: ast.If) -> ir.If:
        test = self.condition(node.test)
        before = set(self.assigned)
        body = self.statements(node.body)
        after_body = self.assigned
        self.assigned = set(before)
        orelse = self.statements(node.orelse)
        self.assigned = after_body & self.assigned
        return ir.If(test, body, orelse, node.lineno)

    def circuit_call(self, call: ast.Call) -> ir.Stmt:
        func = call.func
        if not (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == self.circuit
        ):
            self.fail(
                call,
                "only gate, measure and reset calls on the circuit are supported as "
                "expression statements",
                _SUPPORTED_HINT,
            )
        method = func.attr
        if call.keywords:
            self.fail(call, f"keyword arguments to {method}() are not supported")
        if any(isinstance(a, ast.Starred) for a in call.args):
            self.fail(call, "starred arguments are not supported")
        if method == "measure":  # clbit indices are checked against the circuit at run time
            if len(call.args) != 2:
                self.fail(call, "measure() takes a qubit index and a clbit index")
            qubit, clbit = (self.integer(a) for a in call.args)
            return ir.Measure(qubit, clbit, call.lineno)
        if method == "reset":
            if len(call.args) != 1:
                self.fail(call, "reset() takes one qubit index")
            return ir.Reset(self.integer(call.args[0]), call.lineno)
        if method in ("if_test", "while_loop", "for_loop", "switch", "box"):
            self.fail(
                func,
                f"quantum control flow ({method}) is not supported",
                "Python 'if'/'for' in a block run while the circuit is "
                "being built; they do not create runtime control flow",
            )
        if method not in GATES:
            self.fail(
                func,
                f"unsupported circuit method {method!r}",
                "supported: measure reset " + " ".join(sorted(GATES)),
            )
        spec = GATES[method]
        expected = spec.num_params + spec.num_qubits
        if len(call.args) != expected:
            self.fail(
                call,
                f"{method}() takes {spec.num_params} parameter(s) and {spec.num_qubits} "
                f"qubit index(es) as {expected} positional arguments",
                "broadcasting over ranges/lists is not supported; use a loop",
            )
        params = tuple(self.as_float(self.numeric(a)) for a in call.args[: spec.num_params])
        qubits = tuple(self.integer(a) for a in call.args[spec.num_params :])
        return ir.Gate(method, spec.enum, params, qubits, call.lineno)

    # -- conditions --------------------------------------------------------
    def condition(self, node: ast.expr) -> ir.Expr:
        """A value used for its truth (if/elif tests, and/or/not, x if c else y)."""
        if isinstance(node, ast.BoolOp):
            op = "and" if isinstance(node.op, ast.And) else "or"
            return ir.BoolOp(op, tuple(self.condition(v) for v in node.values))  # type: ignore[arg-type]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return ir.Unary("not", self.condition(node.operand), "bool", node.lineno)
        if isinstance(node, ast.Compare):
            return self.compare(node)
        value = self.expr(node)
        if value.type not in NUMERIC:
            self.fail(node, f"cannot use a {shown(value.type)} as a condition")
        return ir.Truth(value)

    def compare(self, node: ast.Compare) -> ir.Expr:
        ops = []
        for op in node.ops:
            name = _CMPOPS.get(type(op))
            if name is None:
                self.fail(node, f"unsupported comparison operator ({type(op).__name__})")
            ops.append(name)
        operands = (self.numeric(node.left), *(self.numeric(c) for c in node.comparators))
        return ir.Compare(tuple(ops), operands)

    # -- expressions -------------------------------------------------------
    def integer(self, node: ast.expr) -> ir.Expr:
        value = self.expr(node)
        if value.type not in INTS:
            self.fail(
                node,
                f"integer expression required, got {shown(value.type)}",
                "qubit indices, sizes, range() bounds and array indices must be int; "
                "use int(...) to truncate a float",
            )
        return value

    def as_float(self, value: ir.Expr) -> ir.Expr:
        """Python ``float(value)`` (also what math functions and gates do)."""
        return value if value.type == "float" else ir.ToFloat(value)

    def as_double(self, value: ir.Expr) -> ir.Expr:
        """An arithmetic operand: ints convert, float types keep their type."""
        return ir.ToFloat(value) if value.type in INTS else value

    def numeric(self, node: ast.expr) -> ir.Expr:
        value = self.expr(node)
        if value.type not in NUMERIC:
            self.fail(node, f"numeric expression required, got {shown(value.type)}")
        return value

    def expr(self, node: ast.expr) -> ir.Expr:
        if isinstance(node, ast.Constant):
            return self.constant(node)
        if isinstance(node, ast.Name):
            return self.name(node)
        if isinstance(node, ast.Attribute):
            return self.attribute(node)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            operand = self.numeric(node.operand)
            if isinstance(node.op, ast.UAdd):
                return operand
            return ir.Unary("neg", operand, operand.type, node.lineno)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            self.fail(node, "'not' is only supported in conditions")
        if isinstance(node, ast.BinOp):
            return self.binary(type(node.op), self.numeric(node.left), None, node, node.right)
        if isinstance(node, ast.IfExp):
            test = self.condition(node.test)
            body, orelse = self.numeric(node.body), self.numeric(node.orelse)
            if body.type == "anyfloat":
                self.fail(
                    node,
                    "a conditional expression cannot use a variable that is a float on some "
                    "paths and an np.float64 on others",
                    "convert it with float(...)",
                )
            if body.type != orelse.type:
                self.fail(
                    node,
                    "both branches of a conditional expression must have the same type",
                    "convert one side with float(...)",
                )
            return ir.IfExp(test, body, orelse, body.type)
        if isinstance(node, (ast.Compare, ast.BoolOp)):
            self.fail(node, "comparisons and and/or are only supported in conditions")
        if isinstance(node, ast.Call):
            return self.call(node)
        if isinstance(node, ast.Subscript):
            return self.subscript(node)
        self.fail(node, f"unsupported expression ({type(node).__name__})")

    def constant(self, node: ast.Constant) -> ir.Expr:
        value = node.value
        if type(value) is int:
            if not INT64_MIN <= value <= INT64_MAX:
                self.fail(node, "integer constant outside the supported int64 range")
            return ir.Const("int", value)
        if type(value) is float:
            if not math.isfinite(value):
                self.fail(node, "non-finite float literal")
            return ir.Const("float", value)
        self.fail(node, f"unsupported constant of type {type(value).__name__}")

    def name(self, node: ast.Name) -> ir.Expr:
        name = node.id
        if name in self.loops:
            return ir.Var("loop", name, "int")
        if name in self.args:
            kind = self.args[name]
            if kind in ir.ARRAYS:
                self.fail(
                    node,
                    f"array {name!r} can only be indexed, passed to len() or used as "
                    f"{name}.shape[k]",
                )
            return ir.Var("arg", name, kind)
        if name == self.circuit:
            self.fail(node, "the circuit can only be used as the receiver of gate calls")
        if name in self.local_names:
            if name not in self.assigned:
                self.fail(
                    node,
                    f"{name!r} may be used before it is assigned",
                    "assign it before this point on every path (assignments inside a "
                    "loop body or a single if-branch do not count after them)",
                )
            return ir.Var("local", name, self.local_types[name])
        if name in self.loop_names:
            self.fail(
                node,
                f"loop variable {name!r} is only available inside its loop",
                "assign it to a variable inside the loop if you need it afterwards",
            )
        obj = resolve_name(self.fn, name)
        if obj is not MISSING and type(obj) in (int, float):
            self.fail(
                node,
                f"global variable {name!r} is not supported",
                "pass it as a parameter; globals could change after compilation",
            )
        self.fail(node, f"unknown name {name!r}", "only parameters and local variables are visible")

    def attribute(self, node: ast.Attribute) -> ir.Expr:
        base = self.global_object(node.value)
        if base is math and node.attr in MATH_CONSTANTS:
            return ir.Const("float", float(getattr(math, node.attr)))
        if base is np and node.attr in NUMPY_CONSTANTS:
            return ir.Const("float", float(getattr(np, node.attr)))
        self.fail(
            node,
            "unsupported attribute",
            "supported constants: math.pi, math.e, math.tau, np.pi, np.e",
        )

    def binary(
        self,
        op_type: type[ast.operator],
        left: ir.Expr,
        right: ir.Expr | None,
        node: ast.AST,
        right_node: ast.expr,
    ) -> ir.Expr:
        op = _BINOPS.get(op_type)
        if op is None:
            self.fail(node, f"unsupported operator {op_type.__name__}")
        line = getattr(node, "lineno", 0)
        if op == "pow":
            exponent = right_node
            if not (
                isinstance(exponent, ast.Constant)
                and type(exponent.value) is int
                and 0 <= exponent.value <= 1024
            ):
                self.fail(
                    node,
                    "'**' needs a non-negative integer literal exponent (0..1024)",
                    "Python's result type for other exponents depends on run-time "
                    "values; use math.sqrt/math.exp or repeated multiplication",
                )
            return ir.Binary("pow", left, ir.Const("int", exponent.value), left.type, line)
        if right is None:
            right = self.numeric(right_node)
        if "int64" in (left.type, right.type):
            if op != "truediv" and left.type in INTS and right.type in INTS:
                # np.int64 arithmetic: wraps on overflow, and NumPy reports overflow
                # and division by zero instead of raising.
                return ir.Binary(op, left, right, "int64", line)
            # with a float, or '/': NumPy converts to float64
            return ir.Binary(op, self.as_double(left), self.as_double(right), "float64", line)
        if left.type == right.type == "int":
            # int / int keeps int operands: Python divides exactly, which the
            # backend reproduces only within +-2**53 (checked at run time).
            result: ir.Type = "float" if op == "truediv" else "int"
            return ir.Binary(op, left, right, result, line)
        # Mixed int/float: Python (and NumPy) convert the int operand to a double,
        # exactly like the C conversion for int64 values.
        result = float_result(left.type, right.type)
        return ir.Binary(op, self.as_double(left), self.as_double(right), result, line)

    def call(self, node: ast.Call) -> ir.Expr:
        if node.keywords:
            self.fail(node, "keyword arguments are not supported in calls")
        if any(isinstance(a, ast.Starred) for a in node.args):
            self.fail(node, "starred arguments are not supported")
        func = node.func
        line = node.lineno
        if isinstance(func, ast.Name) and self.is_builtin(func.id):
            name = func.id
            if name == "len":
                if len(node.args) != 1:
                    self.fail(node, "len() takes exactly one array argument")
                return ir.Len(self.array(node.args[0]))
            if name in ("abs", "int", "float"):
                if len(node.args) != 1:
                    self.fail(node, f"{name}() takes exactly one numeric argument")
                arg = self.numeric(node.args[0])
                if name == "abs":
                    return ir.Call("abs", (arg,), arg.type, line)
                if name == "float":
                    return self.as_float(arg)
                return arg if arg.type == "int" else ir.Call("int", (arg,), "int", line)

            if name in ("min", "max"):
                if len(node.args) < 2:
                    self.fail(node, f"{name}() needs at least two numeric arguments")
                args = tuple(self.numeric(a) for a in node.args)
                if len({a.type for a in args}) != 1 or args[0].type == "anyfloat":
                    self.fail(
                        node,
                        f"{name}() arguments must all be int, all float or all of one NumPy type",
                        "Python returns one of the original objects, so mixed types would "
                        "change the result type at run time; convert with float(...)",
                    )
                return ir.Call(name, args, args[0].type, line)
        index = (
            _npufunc.lookup(self.global_object(func))
            if isinstance(func, (ast.Name, ast.Attribute))
            else None
        )
        if index is not None:
            return self.numpy_call(node, index)
        fname = self.math_function(func)
        if fname is not None:
            arity = 2 if fname in MATH_2 else 1
            if len(node.args) != arity:
                self.fail(node, f"math.{fname}() takes exactly {arity} argument(s)")
            margs = tuple(self.numeric(a) for a in node.args)
            if fname in MATH_TO_INT:
                if margs[0].type == "int":
                    return margs[0]  # math.floor/ceil/trunc return int arguments unchanged
                if margs[0].type == "int64":
                    if fname == "trunc":  # np.int64 has no __trunc__
                        self.fail(node, "math.trunc() does not accept an np.int64", "use int(x)")
                    # np.int64 has no __floor__/__ceil__: Python goes through float
                    margs = (ir.ToFloat(margs[0]),)
                return ir.Call("int" if fname == "trunc" else fname, margs, "int", line)
            floats = tuple(self.as_float(a) for a in margs)
            if fname in MATH_SCALE:
                scale = ir.Const("float", MATH_SCALE[fname])
                return ir.Binary("mul", floats[0], scale, "float", line)
            return ir.Call(fname, floats, "float", line)
        self.fail(
            node,
            "unsupported function call",
            "supported: len abs min max int float, math."
            + ", math.".join([*MATH_1, *MATH_2, *MATH_TO_INT, *MATH_SCALE])
            + ", and np."
            + ", np.".join(u.__name__ for u in _npufunc.UFUNCS),
        )

    def numpy_call(self, node: ast.Call, index: int) -> ir.Expr:
        ufunc = _npufunc.UFUNCS[index]
        name = ufunc.__name__
        if len(node.args) != ufunc.nin:
            self.fail(node, f"np.{name}() takes exactly {ufunc.nin} argument(s) here")
        args = tuple(self.numeric(a) for a in node.args)
        kinds = tuple("int" if a.type in INTS else "float" for a in args)
        returns = _npufunc.result_type(index, kinds)
        if returns is not np.float64:
            self.fail(
                node,
                f"np.{name}() of int arguments returns {returns.__name__}, which blocks "
                "do not support",
                f"pass a float, e.g. np.{name}(float(x))",
            )
        return ir.UFunc(index, name, tuple(self.as_double(a) for a in args), node.lineno)

    def math_function(self, func: ast.expr) -> str | None:
        """The name of the math function ``func`` refers to, however it was imported."""
        if not isinstance(func, (ast.Name, ast.Attribute)):
            return None
        obj = self.global_object(func)
        entry = MATH_FUNCTIONS.get(id(obj))
        return entry[1] if entry is not None and entry[0] is obj else None

    def array(self, node: ast.expr) -> ir.Var:
        if isinstance(node, ast.Name) and self.args.get(node.id) in ir.ARRAYS:
            return ir.Var("arg", node.id, self.args[node.id])
        self.fail(node, "expected an np.ndarray variable")

    def subscript(self, node: ast.Subscript) -> ir.Expr:
        if isinstance(node.slice, ast.Slice) or (
            isinstance(node.slice, ast.Tuple)
            and any(isinstance(e, ast.Slice) for e in node.slice.elts)
        ):
            self.fail(node, "slicing is not supported; index single elements")
        value = node.value
        if isinstance(value, ast.Attribute) and value.attr == "shape":
            return self.shape(node, self.array(value.value))
        if isinstance(value, ast.Subscript) and not isinstance(value.slice, ast.Tuple):
            # a[i][j] on a 2-D array: row i (checked first), then element j
            array = self.array(value.value)
            if ir.ARRAYS[array.type][1] != 2:
                self.fail(node, f"{shown(array.type)} {array.name!r} has one index")
            i = self.integer(value.slice)
            j = self.integer(node.slice)
            return ir.Index(array, (i, j), node.lineno, ir.ARRAYS[array.type][0], chained=True)
        array = self.array(value)
        element, ndim = ir.ARRAYS[array.type]
        nodes = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
        if len(nodes) != ndim:
            self.fail(
                node,
                f"{shown(array.type)} {array.name!r} needs {ndim} index(es), got {len(nodes)}",
                "a row of a 2-D array is not supported; index an element with a[i, j]"
                if ndim == 2
                else None,
            )
        indices = tuple(self.integer(n) for n in nodes)
        return ir.Index(array, indices, node.lineno, element)

    def shape(self, node: ast.Subscript, array: ir.Var) -> ir.Expr:
        """``a.shape[k]`` with a literal k."""
        k = node.slice
        negative = isinstance(k, ast.UnaryOp) and isinstance(k.op, ast.USub)
        literal = k.operand if negative and isinstance(k, ast.UnaryOp) else k
        ndim = ir.ARRAYS[array.type][1]
        if not (isinstance(literal, ast.Constant) and type(literal.value) is int):
            self.fail(node, "a.shape[k] needs an integer literal k")
        axis = -literal.value if negative else literal.value
        if not -ndim <= axis < ndim:
            self.fail(node, f"{array.name}.shape has {ndim} entries")
        return ir.Len(array, axis % ndim)
