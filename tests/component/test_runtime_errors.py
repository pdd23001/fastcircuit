"""Runtime checks in generated code: bounds, duplicates, overflow, division, limits."""

from __future__ import annotations

import numpy as np
import pytest
from qiskit import QuantumCircuit
from qiskit.circuit.exceptions import CircuitError

from fastcircuit import DuplicateQubitError, QubitIndexError, _native

from support.blocks import as_block
from support.circuits import assert_same_structure


@as_block
def pair(n: int, a: int, b: int):
    qc = QuantumCircuit(n)
    qc.cx(a, b)
    return qc


@as_block
def triple(a: int, b: int, c: int):
    qc = QuantumCircuit(3)
    qc.ccx(a, b, c)
    return qc


@as_block
def arith(n: int, a: int, b: int, theta: float):
    qc = QuantumCircuit(n)
    qc.rx(theta / b, (a * b + 1) % n)
    return qc


@as_block
def ops(a: int, b: int, which: int):
    qc = QuantumCircuit(1)
    if which == 0:
        qc.rz(a + b, 0)
    if which == 1:
        qc.rz(a - b, 0)
    if which == 2:
        qc.rz(a * b, 0)
    if which == 3:
        qc.rz(-a, 0)
    if which == 4:
        qc.rz(a % b, 0)
    if which == 5:
        qc.rz(a / b, 0)
    return qc


@as_block
def indexed(n: int, angles: np.ndarray, index: int):
    qc = QuantumCircuit(n)
    qc.rz(angles[index], 0)
    return qc


@as_block
def stepped(n: int, start: int, stop: int, step: int):
    qc = QuantumCircuit(n)
    for i in range(start, stop, step):
        qc.x(i % n)
    return qc


@as_block
def eval_order(n: int, z: int):
    qc = QuantumCircuit(n)
    qc.cx(n, 1 % z)
    return qc


@as_block
def fma(x: float, c: float):
    qc = QuantumCircuit(1)
    qc.rz(x * x - c, 0)
    return qc


def _expect_same_error(fn, *args, native_type, python_type=None):
    with pytest.raises(native_type) as native:
        fn(*args)
    with pytest.raises(python_type or native_type):
        fn.python(*args)
    return native.value


@pytest.mark.parametrize(("a", "b"), [(0, 2), (2, 0), (-3, 0), (0, 10**6)])
def test_qubit_out_of_range(a, b):
    err = _expect_same_error(pair, 2, a, b, native_type=QubitIndexError, python_type=CircuitError)
    assert isinstance(err, IndexError) and isinstance(err, CircuitError)
    assert "out of range for size 2" in str(err)
    assert "test_runtime_errors.py, line" in str(err)


@pytest.mark.parametrize(("a", "b"), [(0, 0), (1, -1), (-2, 0)])
def test_duplicate_qubits(a, b):
    err = _expect_same_error(
        pair, 2, a, b, native_type=DuplicateQubitError, python_type=CircuitError
    )
    assert isinstance(err, ValueError)


@pytest.mark.parametrize("args", [(0, 0, 2), (0, 2, 2), (1, 2, -2), (0, -3, 2)])
def test_duplicate_qubits_three(args):
    _expect_same_error(triple, *args, native_type=DuplicateQubitError, python_type=CircuitError)


def test_evaluation_order_matches_python():
    # Regression: the prototype validated the first qubit before evaluating the
    # second argument, raising IndexError where Python raises ZeroDivisionError.
    _expect_same_error(eval_order, 2, 0, native_type=ZeroDivisionError)


@pytest.mark.parametrize(
    ("a", "b", "which", "exc"),
    [
        (2**62, 2**62, 0, OverflowError),
        (-(2**63) + 1, 2, 1, OverflowError),
        (2**32, 2**31, 2, OverflowError),
        (-(2**63), 0, 3, OverflowError),
        (5, 0, 4, ZeroDivisionError),
        (5, 0, 5, ZeroDivisionError),
        (2**53 + 1, 1, 5, OverflowError),  # inexact true division is refused
    ],
)
def test_int64_boundaries(a, b, which, exc):
    with pytest.raises(exc):
        ops(a, b, which)


@pytest.mark.parametrize(
    ("a", "b", "which"),
    [
        (2**62, 2**62 - 1, 0),
        (-(2**62), 2**62, 1),
        (-(2**31), 2**31, 2),
        (-(2**63) + 1, 0, 3),
        (-(2**63), -1, 4),
        (-7, 3, 4),
        (7, -3, 4),
        (2**53, 3, 5),
        (-(2**53), 7, 5),
    ],
)
def test_int64_boundaries_that_succeed(a, b, which):
    assert_same_structure(ops(a, b, which), ops.python(a, b, which))


def test_zero_division_and_overflow_messages():
    where = r"\(.*line \d+, in arith\.<block line \d+>\)"
    with pytest.raises(ZeroDivisionError, match=r"division by zero " + where):
        arith(2, 1, 0, 0.1)
    with pytest.raises(OverflowError, match="int64"):
        arith(2, 2**62, 4, 0.1)


@pytest.mark.parametrize("index", [-2, 1, 2**62, -(2**63)])
def test_array_bounds(index):
    with pytest.raises(IndexError, match="is out of bounds for axis 0 with size 1"):
        indexed(1, np.array([0.1]), index)


def test_range_step_zero():
    _expect_same_error(stepped, 2, 0, 5, 0, native_type=ValueError)


@pytest.mark.parametrize(
    ("start", "stop", "step"),
    [
        (0, 0, 1),
        (5, 0, 1),
        (0, 5, -1),
        (5, -1, -2),
        (-3, 4, 3),
        (2**63 - 3, 2**63 - 1, 5),
        (-(2**63) + 2, -(2**63), -7),
    ],
)
def test_range_edges(start, stop, step):
    assert_same_structure(stepped(3, start, stop, step), stepped.python(3, start, stop, step))


@as_block
def spin(n: int):
    qc = QuantumCircuit(1)
    for i in range(n):
        pass
    qc.h(0)
    return qc


def test_iteration_limit():
    assert len(spin(1000)) == 1
    with pytest.raises(ValueError, match="iteration limit"):
        spin(30_000_000)


def test_gate_limit():
    with pytest.raises(ValueError, match="gate limit"):
        stepped(2, 0, 6_000_000, 1)


def test_negative_circuit_size_is_rejected_by_qiskit():
    _expect_same_error(pair, -1, 0, 1, native_type=CircuitError)


def test_no_fused_multiply_add():
    # Regression: clang contracts a*b-c into an FMA by default, which rounds once
    # instead of twice and so differs from Python.
    x, c = 1 + 2.0**-27, 1 + 2.0**-26
    assert x * x - c == 0.0
    assert fma(x, c).data[0].operation.params == [0.0]


@pytest.mark.parametrize(
    "backend",
    [
        pytest.param("llvm", marks=pytest.mark.needs_llvmlite),
        pytest.param("c", marks=pytest.mark.needs_c_backend),
    ],
)
def test_native_entry_point_validates_buffers(backend, monkeypatch):
    # Defence in depth: call the native entry point directly with objects that
    # bypass the Python-side argument checks.
    monkeypatch.setenv("FASTCIRCUIT_BACKEND", backend)
    fn = as_block(indexed.python)
    fn(1, np.array([0.5]), 0)
    if backend == "llvm":
        native = _native._RUNNERS[fn.cache_key]
    else:
        native = _native._LOADED[fn.cache_key].run
    # The block's parameters are its free names in sorted order: angles, index.
    qc = QuantumCircuit(1)
    with pytest.raises(TypeError, match="float64"):
        native(qc, np.zeros(2, dtype=np.int64), 0)
    with pytest.raises(TypeError, match="float64"):
        native(qc, np.zeros((2, 2)), 0)
    with pytest.raises(TypeError, match="float64"):
        native(qc, b"12345678", 0)
    with pytest.raises(TypeError, match="Python int"):
        native(qc, np.zeros(2), True)
    with pytest.raises(TypeError, match="QuantumCircuit"):
        native(1, np.zeros(2), 0)
    with pytest.raises(TypeError, match="expected 3 arguments"):
        native(qc, np.zeros(2))
    assert len(qc) == 0
