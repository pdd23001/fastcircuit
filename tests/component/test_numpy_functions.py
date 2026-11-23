"""NumPy math functions in blocks (np.sin, np.arctan2, ...).

Compiled code calls each function's own float64 loop, so results are the bits
NumPy computes on this machine, and NumPy's warnings (or np.errstate errors) are
reported for the same line. Every case compares with the same code run as Python.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from numpy import arctan2, sqrt
from qiskit import QuantumCircuit

from fastcircuit import UnsupportedSyntax
from fastcircuit._npufunc import UFUNCS

from support.blocks import as_block
from support.circuits import assert_same_outcome

INF, NAN = math.inf, math.nan
VALUES = [0.0, -0.0, 0.5, -0.5, 1.0, -1.0, 2.0, 1e-310, 710.0, -745.5, 1e300, INF, -INF, NAN]


@pytest.mark.parametrize("ufunc", UFUNCS, ids=lambda u: u.__name__)
def test_numpy_function_matches_numpy(ufunc, tmp_path, monkeypatch):
    path = tmp_path / f"np_{ufunc.__name__}.py"
    if ufunc.nin == 1:
        body = (
            f"    qc.rz(np.{ufunc.__name__}(x), 0)\n"
            f"    qc.rz(np.{ufunc.__name__}(a[0]), 0)\n"
            f"    qc.rz(np.{ufunc.__name__}(a[1] * 2.0), 0)\n"
        )
    else:
        body = (
            f"    qc.rz(np.{ufunc.__name__}(x, a[1]), 0)\n"
            f"    qc.rz(np.{ufunc.__name__}(a[0], k), 0)\n"
        )
    path.write_text(
        "import numpy as np\nfrom qiskit import QuantumCircuit\n\n\n"
        "def build(x: float, k: int, a: np.ndarray):\n"
        "    qc = QuantumCircuit(1)\n" + body + "    return qc\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    module = __import__(path.stem)
    build = as_block(module.build)
    outcomes = set()
    for i, x in enumerate(VALUES):
        for y in VALUES[::2]:
            outcomes.add(assert_same_outcome(build, x, 3 - i, np.array([y, x])))
    assert "ok" in outcomes


@as_block
def imported_names(x: float, a: np.ndarray):
    qc = QuantumCircuit(1)
    angle = np.pi * 0.0  # a float
    angle = sqrt(abs(x)) + arctan2(a[0], 1.0)  # np.float64 from here on
    qc.ry(angle, 0)
    if np.cos(x) > 0:
        qc.x(0)
    return qc


def test_imported_numpy_functions_and_results():
    for x in VALUES:
        for y in VALUES:
            assert_same_outcome(imported_names, x, np.array([y]))


def test_numpy_function_on_ints_that_returns_int64_is_rejected():
    def floor_of_int(n: int):
        qc = QuantumCircuit(n)
        qc.rz(np.floor(n), 0)
        return qc

    with pytest.raises(UnsupportedSyntax, match=r"np.floor\(\) of int arguments returns int64"):
        as_block(floor_of_int)(1)
