"""The sign of a NaN result matches Python's.

Python runs each float operation on its own, so the hardware decides the sign of
a NaN result. Compiled code must not let the optimizer combine operations
((-a) + b -> b - a, x * -1.0 -> -x), which would change it.

One case is not reproducible: when both operands of a commutative operation (+, *)
are NaN, the hardware returns one of them, and which one depends on the operand
order CPython's own C compiler chose for float_add/float_mul. Those pairs are
skipped.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from qiskit import QuantumCircuit

from support.blocks import as_block
from support.circuits import assert_same_outcome

COMMUTATIVE_ON_TWO_NANS = {0, 3, 4, 6, 8}
VALUES = [math.nan, -math.nan, 0.0, -0.0, 1.5, -2.0, math.inf, -math.inf]


@as_block
def combined(x: float, y: float, a: np.ndarray, which: int):
    qc = QuantumCircuit(1)
    if which == 0:
        qc.rz(-x + y, 0)
    elif which == 1:
        qc.rz(x * -1.0, 0)
    elif which == 2:
        qc.rz(x - -y, 0)
    elif which == 3:
        qc.rz(-(x * y), 0)
    elif which == 4:
        qc.rz(-x * -y, 0)
    elif which == 5:
        qc.rz(-(x / y) + 0.0, 0)
    elif which == 6:
        qc.rz(-a[0] + a[1], 0)
    elif which == 7:
        qc.rz(a[0] * -1.0 - y, 0)
    elif which == 8:
        t = -x
        qc.rz(t + y, 0)
    return qc


@pytest.mark.parametrize("which", range(9))
def test_nan_sign_matches_python(which):
    for x in VALUES:
        for y in VALUES:
            if math.isnan(x) and math.isnan(y) and which in COMMUTATIVE_ON_TWO_NANS:
                continue
            assert_same_outcome(combined, x, y, np.array([x, y]), which)
