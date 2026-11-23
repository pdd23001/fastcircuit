"""Example builders shared by the tests, plus a plain-Qiskit reference construction."""

import numpy as np
from qiskit import QuantumCircuit

from support.blocks import as_block


@as_block
def layered(n: int, layers: int, theta: float):
    qc = QuantumCircuit(n)
    for layer in range(layers):
        for i in range(n):
            qc.h(i)
            qc.rx(theta, i)
        for i in range(n - 1):
            qc.cx(i, i + 1)
    return qc


@as_block
def array_angles(n: int, angles: np.ndarray):
    qc = QuantumCircuit(n)
    for i in range(len(angles)):
        qc.ry(angles[i], i % n)
        if i % 2 == 0:
            qc.cz(i % n, (i + 1) % n)
    return qc


@as_block
def ghz(n: int):
    qc = QuantumCircuit(n)
    qc.h(0)
    for i in range(n - 1):
        qc.cx(i, i + 1)
    return qc


def broadcast(n: int, layers: int, theta: float) -> QuantumCircuit:
    """Qiskit broadcasting: the same gates as ``layered`` in a different order (all H,
    then all RX, per layer). Mathematically equivalent, not instruction-identical."""
    qc = QuantumCircuit(n)
    for _ in range(layers):
        qc.h(range(n))
        qc.rx(theta, range(n))
        qc.cx(range(n - 1), range(1, n))
    return qc
