"""Acceptance/rejection of syntax, scope rules and type analysis (no compiler).

Each case is a function whose body runs as a block (``support.blocks.as_block``),
analyzed when it is defined, with the types it would see at run time.
"""

from __future__ import annotations

import numpy as np
import pytest

from fastcircuit import UnsupportedSyntax, _block
from fastcircuit import _ir as ir

from support.blocks import as_block
from support.modules import import_module

ACCEPTED = {
    "layered": """
        def build(n: int, layers: int, theta: float):
            qc = QuantumCircuit(n)
            for layer in range(layers):
                for i in range(n):
                    qc.h(i)
                    qc.rx(theta, i)
                for i in range(n - 1):
                    qc.cx(i, i + 1)
            return qc
    """,
    "docstring_defaults_kwonly": '''
        def build(n: int, *, theta: float = 0.5):
            """Docstrings are allowed."""
            qc = QuantumCircuit(n)
            qc.rx(theta / 2, 0)
            return qc
    ''',
    "global_int_is_a_variable": """
        GLOBAL_INT = 3

        def build(n: int):
            qc = QuantumCircuit(n)
            qc.h(GLOBAL_INT)
            return qc
    """,
    "array_ops": """
        def build(n: int, angles: np.ndarray):
            qc = QuantumCircuit(n)
            for i in range(len(angles) - 1, -1, -1):
                if angles[i] > 0.0:
                    qc.ry(angles[-1 - i] * 2, i % n)
                else:
                    pass
            return qc
    """,
    "int_param_promoted": """
        def build(n: int):
            qc = QuantumCircuit(n)
            qc.rz(n, 0)
            return qc
    """,
}

# (source, message fragment). Each case is rejected when the function is defined: by the
# import-time rewrite ("is assigned inside the block", "cannot return") or the analysis.
REJECTED = {
    "print": ("qc = QuantumCircuit(n)\nprint(n)", "only gate, measure and reset calls"),
    "while": ("qc = QuantumCircuit(n)\nwhile n:\n    pass", "unsupported statement (While)"),
    "break": ("qc = QuantumCircuit(n)\nfor i in range(n):\n    break", "unsupported statement"),
    "for_else": (
        "qc = QuantumCircuit(n)\nfor i in range(n):\n    pass\nelse:\n    pass",
        "for ... else",
    ),
    "iterate_list": ("qc = QuantumCircuit(n)\nfor i in [1, 2]:\n    qc.h(i)", "range(...)"),
    "range_kwargs": ("qc = QuantumCircuit(n)\nfor i in range(stop=n):\n    pass", "range() takes"),
    "range_float": ("qc = QuantumCircuit(n)\nfor i in range(1.0):\n    pass", "integer expression"),
    "tuple_target": ("qc = QuantumCircuit(n)\nfor i, j in range(n):\n    pass", "single name"),
    "loop_var_escape": (
        "qc = QuantumCircuit(n)\nfor i in range(n):\n    qc.h(i)\nqc.x(i)",
        "only available inside its loop",
    ),
    "loop_var_reuse_nested": (
        "qc = QuantumCircuit(n)\nfor i in range(n):\n    for i in range(n):\n        pass",
        "fresh name",
    ),
    "loop_var_is_param": (
        "qc = QuantumCircuit(n)\nfor n in range(3):\n    pass",
        "'n' is assigned",
    ),
    "loop_var_is_circuit": (
        "qc = QuantumCircuit(n)\nfor qc in range(n):\n    pass",
        "'qc' is assigned",
    ),
    "float_qubit": ("qc = QuantumCircuit(n)\nqc.h(1.0)", "integer expression"),
    "gate_keyword": ("qc = QuantumCircuit(n)\nqc.h(qubit=0)", "keyword"),
    "gate_arity": ("qc = QuantumCircuit(n)\nqc.cx(0)", "positional arguments"),
    "gate_starred": ("qc = QuantumCircuit(n)\nqc.cx(*[0, 1])", "starred arguments"),
    "unknown_method": (
        "qc = QuantumCircuit(n)\nqc.append(None, [0])",
        "unsupported circuit method",
    ),
    "global_name": ("qc = QuantumCircuit(n)\nqc.h(GLOBAL_INDEX)", "unknown name 'GLOBAL_INDEX'"),
    "circuit_as_value": ("qc = QuantumCircuit(n)\nqc.h(qc)", "receiver of gate calls"),
    "big_constant": ("qc = QuantumCircuit(n)\nqc.h(9223372036854775808)", "int64 range"),
    "bool_constant": ("qc = QuantumCircuit(n)\nqc.h(True)", "unsupported constant"),
    "string_constant": ("qc = QuantumCircuit(n)\nqc.h('0')", "unsupported constant"),
    "inf_literal": ("qc = QuantumCircuit(n)\nqc.rx(1e400, 0)", "non-finite"),
    "return_early": ("qc = QuantumCircuit(n)\nif n > 1:\n    return qc", "cannot return"),
    "walrus": ("qc = QuantumCircuit(n)\nqc.h((k := 1))", "unsupported expression"),
    "other_receiver": ("qc = QuantumCircuit(n)\nother = qc", "receiver of gate calls"),
    "power_variable_exponent": (
        "qc = QuantumCircuit(n)\nqc.rx(2.0 ** n, 0)",
        "integer literal exponent",
    ),
    "power_negative_exponent": (
        "qc = QuantumCircuit(n)\nqc.rx(2.0 ** -1, 0)",
        "integer literal exponent",
    ),
    "type_change": ("qc = QuantumCircuit(n)\nx = 1\nx = 0.5", "is int elsewhere"),
    "aug_type_change": ("qc = QuantumCircuit(n)\nx = 1\nx /= 2", "is int elsewhere"),
    "param_reassigned": ("qc = QuantumCircuit(n)\nn = 0.5", "'n' is assigned inside the block"),
    "use_before_assign": ("qc = QuantumCircuit(n)\nqc.rx(x, 0)\nx = 0.5", "may be used before"),
    "assigned_in_loop_only": (
        "qc = QuantumCircuit(n)\nfor i in range(n):\n    x = 1.0\nqc.rx(x, 0)",
        "may be used before",
    ),
    "assigned_in_one_branch": (
        "qc = QuantumCircuit(n)\nif n > 1:\n    x = 1.0\nqc.rx(x, 0)",
        "may be used before",
    ),
    "assign_loop_var": ("qc = QuantumCircuit(n)\nfor i in range(n):\n    i = 2", "loop variable"),
    "assign_circuit": ("qc = QuantumCircuit(n)\nqc = QuantumCircuit(1)", "'qc' is assigned"),
    "tuple_assign": ("qc = QuantumCircuit(n)\na, b = 1, 2", "single name"),
    "chained_assign": ("qc = QuantumCircuit(n)\na = b = 1", "chained assignment"),
    "mixed_min": ("qc = QuantumCircuit(n)\nqc.rx(min(1, 2.0), 0)", "all be int, all float or"),
    "ifexp_mixed": ("qc = QuantumCircuit(n)\nqc.rx(1 if n else 2.0, 0)", "same type"),
    "annotated_mismatch": (
        "qc = QuantumCircuit(n)\nx: float = 0",
        "annotated float but the value is int",
    ),
    "annotated_list": ("qc = QuantumCircuit(n)\nx: list = 0", "must be int or float"),
    "measure_arity": ("qc = QuantumCircuit(n, n)\nqc.measure(0)", "qubit index and a clbit"),
    "quantum_if": ("qc = QuantumCircuit(n, 1)\nqc.if_test((0, 1))", "quantum control flow"),
    "with_block": (
        "qc = QuantumCircuit(n, 1)\nwith qc.if_test((0, 1)):\n    qc.x(0)",
        "unsupported statement (With)",
    ),
    "math_unknown": (
        "qc = QuantumCircuit(n)\nqc.rx(math.gamma(1.0), 0)",
        "unsupported function call",
    ),
    "math_attribute": ("qc = QuantumCircuit(n)\nqc.rx(math.inf, 0)", "unsupported attribute"),
    "compare_as_value": ("qc = QuantumCircuit(n)\nx = n < 3", "only supported in conditions"),
    "not_as_value": ("qc = QuantumCircuit(n)\nx = not n", "only supported in conditions"),
    "len_of_int": ("qc = QuantumCircuit(n)\nqc.h(len(n))", "np.ndarray variable"),
    "abs_kwargs": ("qc = QuantumCircuit(n)\nqc.h(abs(x=n))", "keyword arguments"),
}


def _wrap(body: str, signature: str = "n: int") -> str:
    lines = "\n".join("    " + line for line in body.splitlines())
    return f"@as_block\ndef build({signature}):\n{lines}\n    return qc\n"


@pytest.mark.parametrize("name", sorted(ACCEPTED))
def test_accepts(make_function, name):
    import textwrap

    source = textwrap.dedent(ACCEPTED[name]).replace("def build", "@as_block\ndef build", 1)
    build = make_function(source)
    assert build.program is not None  # analyzed when defined
    assert all(site not in _block._STATES for site in build.sites)  # nothing compiled yet


@pytest.mark.parametrize("name", sorted(REJECTED))
def test_rejects(make_function, name):
    body, fragment = REJECTED[name]
    with pytest.raises(UnsupportedSyntax) as info:
        make_function(_wrap(body))
    assert fragment in str(info.value), str(info.value)
    assert info.value.lineno is not None


@pytest.mark.parametrize(
    "prelude", ["def helper(x):\n    return x\n\n", "from math import gamma as helper\n\n", ""]
)
def test_rejects_calls_to_non_builtin_functions(make_function, prelude):
    # Regression: a call to a global function that is not a builtin (or to an
    # undefined name) raised AttributeError from the builtin check.
    with pytest.raises(UnsupportedSyntax, match="unsupported function call"):
        make_function(prelude + _wrap("qc = QuantumCircuit(n)\nqc.rx(helper(1.0), 0)"))


def test_shadowed_range_parameter(make_function):
    with pytest.raises(UnsupportedSyntax, match="range"):
        make_function(
            _wrap("qc = QuantumCircuit(n)\nfor i in range(n):\n    pass", "n: int, range: int")
        )


def test_shadowed_range_global(make_function):
    source = "range = lambda n: [0]\n" + _wrap(
        "qc = QuantumCircuit(n)\nfor i in range(n):\n    pass"
    )
    with pytest.raises(UnsupportedSyntax, match="range"):
        make_function(source)


def test_shadowed_range_closure():
    # Regression: the prototype only checked module globals, so a closure variable
    # named 'range' was ignored and the compiled loop used the real builtin.
    from qiskit import QuantumCircuit

    def outer():
        range = lambda n: [0]

        def build(n: int):
            qc = QuantumCircuit(n)
            for i in range(n):
                qc.h(i)
            return qc

        return build

    with pytest.raises(UnsupportedSyntax, match="range"):
        as_block(outer())


def test_shadowed_len_closure(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    module = import_module(
        tmp_path,
        """
        def outer():
            len = lambda a: 1

            def build(n, a):
                qc = QuantumCircuit(n)
                with fastcircuit:
                    for i in range(len(a)):
                        qc.h(i)
                return qc

            return build
        """,
    )
    with pytest.raises(UnsupportedSyntax, match="len"):
        module.outer()(2, np.zeros(2))


def test_ir_types_for_mixed_arithmetic(make_function):
    build = make_function(
        """
        @as_block
        def build(n: int, theta: float):
            qc = QuantumCircuit(n)
            qc.rx(n / 2, 0)
            qc.ry(theta * n, 0)
            return qc
        """
    )
    first, second = build.program.body
    assert isinstance(first, ir.Gate) and isinstance(second, ir.Gate)
    # int / int keeps int operands so the backend can apply the exactness rule.
    (div,) = first.params
    assert isinstance(div, ir.Binary) and div.op == "truediv"
    assert div.left.type == div.right.type == "int" and div.type == "float"
    # float * int converts the int operand exactly like Python's float().
    (mul,) = second.params
    assert isinstance(mul, ir.Binary) and isinstance(mul.right, ir.ToFloat)
