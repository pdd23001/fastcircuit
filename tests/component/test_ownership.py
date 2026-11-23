"""Native/Python ownership: references, buffers and cleanup on success and failure."""

from __future__ import annotations

import gc
import sys
import weakref

import numpy as np
import pytest
from qiskit import QuantumCircuit

from fastcircuit import QubitIndexError

from support.blocks import as_block


@as_block
def from_array(n: int, angles: np.ndarray, bad_qubit: int):
    qc = QuantumCircuit(n)
    for i in range(len(angles)):
        qc.ry(angles[i], i % n)
    qc.h(bad_qubit)
    return qc


def _live_circuits() -> int:
    gc.collect()
    return sum(1 for o in gc.get_objects() if type(o) is QuantumCircuit)


def test_result_has_exactly_one_owner():
    qc = from_array(3, np.arange(6.0), 0)
    reference = from_array.python(3, np.arange(6.0), 0)
    # Compare with a circuit Python built in the same frame: absolute counts differ
    # between Python versions, and Qiskit's circuit holds itself in a reference cycle.
    assert sys.getrefcount(qc) == sys.getrefcount(reference)
    del reference
    ref = weakref.ref(qc)
    del qc
    gc.collect()
    assert ref() is None


def test_circuit_data_not_leaked():
    native = from_array(3, np.arange(6.0), 0)
    python = from_array.python(3, np.arange(6.0), 0)
    assert sys.getrefcount(native._data) == sys.getrefcount(python._data)


def test_buffers_released_on_success_and_failure():
    angles = np.arange(50.0)
    base = sys.getrefcount(angles)
    for _ in range(500):
        from_array(4, angles, 1)
    assert sys.getrefcount(angles) == base
    for _ in range(500):
        with pytest.raises(QubitIndexError):
            from_array(4, angles, 99)  # fails after the buffer was acquired
    assert sys.getrefcount(angles) == base
    angles.resize(10, refcheck=True)  # would raise BufferError if still exported


def test_partial_circuits_are_freed_on_failure():
    angles = np.arange(200.0)
    from_array(4, angles, 0)  # warm up (load the module)
    before = _live_circuits()
    for _ in range(300):
        with pytest.raises(QubitIndexError):
            from_array(4, angles, 99)
    assert _live_circuits() == before


def test_many_circuits_are_independent():
    circuits = [from_array(3, np.full(4, float(i)), 0) for i in range(50)]
    expected = [from_array.python(3, np.full(4, float(i)), 0) for i in range(50)]
    del circuits[::2], expected[::2]
    gc.collect()
    for actual, reference in zip(circuits, expected, strict=True):
        assert actual == reference
    circuits[0].x(0)  # mutating one circuit must not affect another
    assert circuits[1] == expected[1]


def test_input_array_mutation_after_return_does_not_affect_circuit():
    angles = np.array([0.1, 0.2, 0.3])
    qc = from_array(3, angles, 0)
    angles[:] = 9.0
    assert [inst.operation.params[0] for inst in qc.data[:3]] == [0.1, 0.2, 0.3]
