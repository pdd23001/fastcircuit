"""2-D arrays and int64 arrays in blocks.

``theta[layer, q]`` and ``theta[layer][q]`` read 2-D arrays; int64 arrays (edge
lists, qubit maps) give np.int64 elements, which index qubits and follow NumPy's
integer rules: arithmetic wraps on overflow, and // or % by zero gives 0, with
NumPy's RuntimeWarning. Every case compares with the same code run as Python.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from qiskit import QuantumCircuit

from fastcircuit import UnsupportedSyntax

from support.blocks import Array2, IntArray, IntArray2, as_block
from support.circuits import assert_same_outcome

MIN, MAX = -(2**63), 2**63 - 1


@as_block
def layered(theta: Array2, edges: IntArray2):
    qc = QuantumCircuit(4)
    for layer in range(theta.shape[0]):
        for q in range(theta.shape[1]):
            qc.ry(theta[layer, q], q)
        for k in range(len(edges)):
            qc.cx(edges[k, 0], edges[k][1])
        qc.rz(theta[layer][-1] * 2.0, edges[-1, -1])
    return qc


def test_2d_arrays_and_edge_lists():
    theta = np.linspace(0, 1, 12).reshape(3, 4)
    edges = np.array([[0, 1], [1, 2], [2, 3]])
    assert_same_outcome(layered, theta, edges)
    assert_same_outcome(layered, np.zeros((0, 4)), edges)
    assert_same_outcome(layered, theta, np.zeros((0, 2), dtype=np.int64))


@as_block
def indexing(theta: Array2, i: int, j: int, which: int):
    qc = QuantumCircuit(1)
    if which == 0:
        qc.rz(theta[i, j], 0)
    elif which == 1:
        qc.rz(theta[i][j], 0)
    elif which == 2:
        qc.rz(theta[i][j // 0], 0)  # row checked before the column is evaluated
    else:
        qc.rz(theta[i, j // 0], 0)  # both evaluated before either is checked
    return qc


@pytest.mark.parametrize("which", range(4))
def test_2d_index_errors_and_order(which):
    theta = np.arange(6.0).reshape(2, 3)
    for i in (-3, -2, -1, 0, 1, 2):
        for j in (-4, -3, 0, 2, 3):
            assert_same_outcome(indexing, theta, i, j, which)


@as_block
def int_ops(e: IntArray, k: int, x: float, which: int):
    qc = QuantumCircuit(1)
    if which == 0:
        qc.rz(e[0] + e[1] - k * e[0], 0)
    elif which == 1:
        qc.rz(e[0] * e[1], 0)
    elif which == 2:
        qc.rz(e[0] // e[1] + e[0] % k, 0)
    elif which == 3:
        qc.rz(-e[0] + abs(e[1]), 0)
    elif which == 4:
        qc.rz(e[0] ** 3, 0)
    elif which == 5:
        qc.rz(e[0] / e[1] + e[1] / k, 0)  # np.float64
    elif which == 6:
        qc.rz(e[0] + x, 0)
    elif which == 7:
        if e[0] == k:
            qc.x(0)
        if e[1] < x:
            qc.y(0)
    elif which == 8:
        qc.rz(int(e[0]) // 3 + float(e[1]), 0)  # Python int and float again
    elif which == 9:
        qc.rz(math.floor(e[0]) + math.sqrt(abs(e[1])) + np.sin(e[0]), 0)
    elif which == 10:
        qc.rz(min(e[0], e[1]), 0)
    return qc


INTS = [0, 1, -1, 7, -7, 2**31, 2**53 + 1, MIN, MAX]


@pytest.mark.parametrize("which", range(11))
def test_int64_elements_follow_numpy(which):
    outcomes = set()
    for p in INTS:
        for q in INTS[::2]:
            for k in (0, 3, -2):
                outcomes.add(
                    assert_same_outcome(int_ops, np.array([p, q], dtype=np.int64), k, 0.5, which)
                )
    assert "ok" in outcomes


@as_block
def np_int_variable(m: np.int64, n: int):
    qc = QuantumCircuit(n)
    qc.h(m)
    qc.rz(m * 2, m % n)
    return qc


def test_np_int64_variable_indexes_qubits():
    for m in (0, 2, -1, 5):
        assert_same_outcome(np_int_variable, np.int64(m), 3)


def test_rejected_forms():
    def row(theta: Array2):
        qc = QuantumCircuit(1)
        qc.rz(theta[0], 0)
        return qc

    with pytest.raises(UnsupportedSyntax, match="needs 2 index"):
        as_block(row)

    def trunc(e: IntArray):
        qc = QuantumCircuit(1)
        qc.rz(math.trunc(e[0]), 0)
        return qc

    with pytest.raises(UnsupportedSyntax, match=r"math\.trunc"):
        as_block(trunc)
