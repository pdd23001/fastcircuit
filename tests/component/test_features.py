"""Expanded language features: locals, arithmetic, conditions, gates, measurement.

Every case compares the compiled function with the original Python function:
bit-identical circuits, or the same exception type.
"""

from __future__ import annotations

import math
import math as m
from math import (
    acosh,
    asinh,
    atan2,
    atanh,
    cbrt,
    copysign,
    cosh,
    degrees,
    exp2,
    expm1,
    fabs,
    floor,
    log1p,
    log2,
    log10,
    radians,
    sinh,
    sqrt,
    tanh,
    trunc,
)

import numpy as np
import pytest
from qiskit import QuantumCircuit

from fastcircuit import QubitIndexError

from support.blocks import as_block
from support.circuits import assert_same_bits, assert_same_outcome

INF, NAN = math.inf, math.nan


@as_block
def accumulate(n: int, theta: float, step: float):
    qc = QuantumCircuit(n)
    angle = theta
    count = 0
    for i in range(n):
        angle += step
        angle *= 1.5
        count += i
        if count % 3 == 0:
            angle -= 0.25
        qc.rx(angle, i)
        qc.rz(count / 2, i)
    total: float = angle * 2.0
    last = n - 1
    last -= 0
    qc.p(total, last)
    return qc


@pytest.mark.parametrize("args", [(1, 0.1, 0.2), (5, -0.3, 0.7), (8, 1e300, 1e10), (3, NAN, 1.0)])
def test_locals_and_augmented_assignment(args):
    assert assert_same_outcome(accumulate, *args) == "ok"


@as_block
def float_ops(x: float, y: float, which: int):
    qc = QuantumCircuit(1)
    if which == 0:
        qc.rz(x // y, 0)
    elif which == 1:
        qc.rz(x % y, 0)
    elif which == 2:
        qc.rz(x**2, 0)
    elif which == 3:
        qc.rz(x**3 * y**0, 0)
    elif which == 4:
        qc.rz(abs(x) + min(x, y) - max(x, y, 0.5), 0)
    elif which == 5:
        qc.rz(int(x), 0)
    elif which == 6:
        qc.rz(math.floor(x) + math.ceil(y), 0)
    elif which == 7:
        qc.rz(math.sqrt(x), 0)
    elif which == 8:
        qc.rz(math.sin(x) + math.cos(y) + math.tan(x), 0)
    elif which == 9:
        qc.rz(math.asin(x) + math.acos(y) + math.atan(x), 0)
    elif which == 10:
        qc.rz(math.exp(x), 0)
    elif which == 11:
        qc.rz(math.log(x), 0)
    elif which == 12:
        qc.rz(x * math.pi + y / math.tau - math.e + np.pi - np.e, 0)
    elif which == 13:
        qc.rz(x / y, 0)
    elif which == 14:
        qc.rz(-x - +y, 0)
    return qc


FLOATS = [
    0.0,
    -0.0,
    1.0,
    -1.0,
    0.5,
    -2.5,
    7.0,
    1e-300,
    1e300,
    1e308,
    -1e308,
    INF,
    -INF,
    NAN,
    5e-324,
    0.1,
    3.0,
    -7.25,
]


@pytest.mark.parametrize("which", range(15))
def test_float_operations_match_python(which):
    rng = np.random.default_rng(which)
    outcomes = set()
    pairs = [(x, y) for x in FLOATS for y in FLOATS[::3]]
    pairs += [(float(a), float(b)) for a, b in rng.normal(scale=10, size=(40, 2))]
    for x, y in pairs:
        outcomes.add(assert_same_outcome(float_ops, x, y, which))
    assert "ok" in outcomes


@as_block
def int_ops(a: int, b: int, which: int):
    qc = QuantumCircuit(1)
    if which == 0:
        qc.rz(a // b, 0)
    elif which == 1:
        qc.rz(a % b, 0)
    elif which == 2:
        qc.rz(a**2, 0)
    elif which == 3:
        qc.rz(a**0 + b**1, 0)
    elif which == 4:
        qc.rz(abs(a) + min(a, b) - max(a, b, 0), 0)
    elif which == 5:
        qc.rz(float(a) / 3, 0)
    elif which == 6:
        qc.rz(math.floor(a) - math.ceil(b), 0)
    elif which == 7:
        qc.rz(a // b * b + a % b, 0)
    elif which == 8:
        qc.rz(math.sqrt(a), 0)
    return qc


INTS = [0, 1, -1, 2, -2, 3, -7, 10, 2**31, -(2**31), 2**53, 2**62, -(2**62)]


@pytest.mark.parametrize("which", range(9))
def test_int_operations_match_python(which):
    for a in INTS:
        for b in INTS:
            assert_same_outcome(int_ops, a, b, which)


@as_block
def more_math(x: float, y: float, which: int):
    qc = QuantumCircuit(1)
    if which < 15:
        f = 0.0
        if which == 0:
            f = cbrt(x)
        elif which == 1:
            f = sinh(x)
        elif which == 2:
            f = cosh(x)
        elif which == 3:
            f = tanh(x)
        elif which == 4:
            f = asinh(x)
        elif which == 5:
            f = acosh(x)
        elif which == 6:
            f = atanh(x)
        elif which == 7:
            f = exp2(x)
        elif which == 8:
            f = expm1(x)
        elif which == 9:
            f = log2(x)
        elif which == 10:
            f = log10(x)
        elif which == 11:
            f = log1p(x)
        elif which == 12:
            f = fabs(x)
        elif which == 13:
            f = degrees(x)
        else:
            f = radians(x)
        qc.rz(f, 0)
    elif which == 15:
        qc.rz(atan2(x, y), 0)
    elif which == 16:
        qc.rz(copysign(x, y), 0)
    elif which == 17:
        qc.rz(m.fmod(x, y), 0)  # attribute of an aliased module
    elif which == 18:
        qc.rz(trunc(x) + m.trunc(y), 0)
    elif which == 19:
        qc.rz(sqrt(x) + floor(y), 0)
    return qc


@pytest.mark.parametrize("which", range(20))
def test_more_math_functions_match_python(which):
    rng = np.random.default_rng(100 + which)
    outcomes = set()
    pairs = [(x, y) for x in FLOATS for y in FLOATS[::2]]
    pairs += [(float(a), float(b)) for a, b in rng.normal(scale=3, size=(200, 2))]
    for x, y in pairs:
        outcomes.add(assert_same_outcome(more_math, x, y, which))
    assert "ok" in outcomes


@as_block
def math_on_ints(a: int, b: int):
    qc = QuantumCircuit(1)
    qc.rz(atan2(a, b) + log10(a) + degrees(b) + trunc(a), 0)
    return qc


def test_math_functions_convert_int_arguments():
    for a in INTS:
        for b in INTS:
            assert_same_outcome(math_on_ints, a, b)


@pytest.mark.parametrize(
    ("a", "b", "which"),
    [
        (-(2**63), -1, 0),  # LLONG_MIN // -1 = 2**63
        (2**32, 0, 2),  # (2**32)**2
        (-(2**63), 0, 4),  # abs(LLONG_MIN)
        (2**62, 2, 3),  # 1 + 2**62 fits; checked below
    ],
)
def test_int64_restriction_raises_where_python_grows(a, b, which):
    if which == 3:
        assert_same_outcome(int_ops, a, b, which)
        return
    int_ops.python(a, b, which)  # Python succeeds with an arbitrary-precision int
    with pytest.raises(OverflowError):
        int_ops(a, b, which)


def test_float_conversion_errors():
    for x, exc in [(NAN, ValueError), (INF, OverflowError), (1e19, OverflowError)]:
        with pytest.raises(exc):
            float_ops(x, 1.0, 5)


@as_block
def conds(a: int, x: float, angles: np.ndarray, k: int):
    qc = QuantumCircuit(1)
    if a < x:
        qc.x(0)
    if a <= x:
        qc.y(0)
    if a > x:
        qc.z(0)
    if a >= x:
        qc.h(0)
    if a == x:
        qc.s(0)
    if a != x:
        qc.t(0)
    if x < a <= 2 * a:
        qc.sdg(0)
    if x >= a:
        qc.tdg(0)
    if 0 <= k < len(angles) and angles[k] > 0.0:  # short-circuit guards the index
        qc.sx(0)
    if k >= len(angles) or angles[k] < 0.0:
        qc.sxdg(0)
    if not a:
        qc.id(0)
    if x:
        qc.rx(1.0, 0)
    if not (a or x) and k:
        qc.ry(1.0, 0)
    qc.rz(x if a > 0 else -x, 0)
    qc.rz(1.5 if x != x else 2.5, 0)  # noqa: PLR0124 -- deliberate NaN self-comparison
    return qc


PAIRS = [
    (0, 0.0),
    (0, -0.0),
    (1, 1.0),
    (1, 0.999999),
    (-1, -0.5),
    (3, NAN),
    (3, INF),
    (-3, -INF),
    (2**53 + 1, 2.0**53),
    (2**53, 2.0**53),
    (2**63 - 1, 9.223372036854775807e18),
    (-(2**63), -9.223372036854775808e18),
    (-(2**63), -9.223372036854777e18),
    (5, 4.5),
]


@pytest.mark.parametrize(("a", "x"), PAIRS)
def test_conditions_match_python(a, x):
    angles = np.array([0.5, -0.5, 0.0])
    for k in (-1, 0, 1, 2, 3, 10):
        assert_same_outcome(conds, a, x, angles, k)


@as_block
def measured(n: int, m: int, q: int, c: int):
    qc = QuantumCircuit(n, m)
    for i in range(n):
        qc.h(i)
        if i % 2 == 1:
            qc.reset(i)
        qc.measure(i, i % m)
    qc.measure(q, c)
    return qc


@pytest.mark.parametrize(
    "args", [(3, 2, 0, 0), (4, 4, -1, -1), (2, 1, 1, 0), (2, 1, 2, 0), (2, 1, 0, 1), (2, 1, -3, 0)]
)
def test_measure_and_reset(args):
    result = assert_same_outcome(measured, *args)
    if result != "ok":
        with pytest.raises(QubitIndexError):
            measured(*args)
        assert result == "CircuitError"


def test_measured_circuit_registers():
    qc = measured(3, 2, 0, 1)
    assert [r.name for r in qc.cregs] == ["c"] and qc.num_clbits == 2
    assert qc.count_ops() == measured.python(3, 2, 0, 1).count_ops()


def test_zero_clbits_measure_raises_like_python():
    assert assert_same_outcome(measured, 2, 0, 0, 0) == "ZeroDivisionError"  # i % m
    assert assert_same_outcome(measured, 0, 0, 0, 0) == "CircuitError"  # no qubit 0


@as_block
def qft_like(n: int):
    qc = QuantumCircuit(n)
    for j in range(n - 1, -1, -1):
        qc.h(j)
        angle = math.pi
        for k in range(j - 1, -1, -1):
            angle /= 2
            qc.cp(angle, j, k)
    return qc


def test_qft_with_locals_matches_python():
    for n in (0, 1, 2, 5, 9):
        assert_same_bits(qft_like(n), qft_like.python(n))


def test_shadowing_builtins_by_assignment_is_rejected(make_function):
    # Assigning 'abs' anywhere makes it a local in Python, so the call is not the builtin.
    from fastcircuit import UnsupportedSyntax

    with pytest.raises(UnsupportedSyntax):
        make_function(
            """
            @as_block
            def build(n: int):
                qc = QuantumCircuit(n)
                qc.h(abs(n))
                abs = 3
                return qc
            """
        )
