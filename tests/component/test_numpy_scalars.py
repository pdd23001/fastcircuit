"""np.float64 values in blocks: array elements and np.float64 variables.

They follow NumPy's rules, not Python float's: division by zero and overflow give
inf/NaN and a RuntimeWarning (or whatever np.errstate says) instead of raising,
and comparison with an int converts the int to float. Every case compares the
compiled block with the same code run as Python, including the warnings.
"""

from __future__ import annotations

import math
import warnings

import numpy as np
import pytest
from qiskit import QuantumCircuit

from support.blocks import as_block
from support.circuits import assert_same_outcome

INF, NAN = math.inf, math.nan
FLOATS = [0.0, -0.0, 1.0, -2.5, 0.1, 1e-300, 1e300, 1.7976931348623157e308, 5e-324,
          INF, -INF, NAN]  # fmt: skip
INTS = [0, 1, -3, 2**53 + 1]


@as_block
def element_ops(a: np.ndarray, x: float, k: int, which: int):
    qc = QuantumCircuit(1)
    if which == 0:
        qc.rz(a[0] + x - a[1] * x, 0)
    elif which == 1:
        qc.rz(a[0] / x, 0)
    elif which == 2:
        qc.rz(x / a[0], 0)
    elif which == 3:
        qc.rz(a[0] // x, 0)  # separate gates: at most one NaN per +, see test_nan_sign
        qc.rz(k // a[1], 0)
    elif which == 4:
        qc.rz(a[0] % x, 0)
    elif which == 5:
        qc.rz(k % a[0], 0)
    elif which == 6:
        qc.rz(a[0] ** 2 + a[1] ** 3, 0)
    elif which == 7:
        qc.rz(-a[0] + 1.5, 0)  # at most one NaN operand: see test_nan_sign
        qc.rz(abs(a[1]) * k, 0)
    elif which == 8:
        qc.rz(k / a[0], 0)
    elif which == 9:
        if a[0] == k:
            qc.x(0)
        if a[1] < k or k >= a[0]:
            qc.y(0)
    elif which == 10:
        qc.rz(float(a[0]) / x, 0)  # a Python float again: raises on zero
    elif which == 11:
        qc.rz(math.sqrt(a[0]) + int(a[1]), 0)
    elif which == 12:
        qc.rz(min(a[0], a[1]) - max(a[1], a[0]), 0)
    elif which == 13:
        qc.rz(a[0] * a[1] * x, 0)
    return qc


@pytest.mark.parametrize("which", range(14))
def test_array_elements_follow_numpy(which):
    outcomes = set()
    for p in FLOATS:
        for q in FLOATS[::2]:
            for x in FLOATS[::3]:
                for k in INTS:
                    a = np.array([p, q])
                    outcomes.add(assert_same_outcome(element_ops, a, x, k, which))
    assert "ok" in outcomes


@as_block
def mixed_local(a: np.ndarray, x: float, k: int):
    qc = QuantumCircuit(1)
    acc = 0.0  # a float, until it is assigned an np.float64
    for i in range(k):
        acc += a[i]
    qc.rz(acc / x, 0)  # Python's rules if k == 0, NumPy's after that
    if acc == 2**53 + 1:
        qc.x(0)
    acc = x * 2.0  # a float again
    qc.rz(acc / x + abs(acc), 0)
    return qc


@pytest.mark.parametrize("k", [0, 1, 2])
def test_local_that_holds_float_or_numpy_value(k):
    for p in FLOATS:
        for x in FLOATS:
            assert_same_outcome(mixed_local, np.array([p, 2.0**53]), x, k)


@as_block
def scalar_variable(y: np.float64, x: float):
    qc = QuantumCircuit(1)
    qc.rz(y / x + y * y, 0)
    return qc


def test_np_float64_variable():
    for y in FLOATS:
        for x in FLOATS:
            assert_same_outcome(scalar_variable, np.float64(y), x)


def test_errstate_and_warning_filters_apply():
    a = np.array([1.0, 2.0])
    with np.errstate(divide="raise"), pytest.raises(FloatingPointError, match="divide by zero"):
        element_ops(a, 0.0, 1, 1)
    with np.errstate(divide="ignore"), warnings.catch_warnings():
        warnings.simplefilter("error")
        element_ops(a, 0.0, 1, 1)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with pytest.raises(RuntimeWarning, match="divide by zero encountered in scalar divide"):
            element_ops(a, 0.0, 1, 1)
