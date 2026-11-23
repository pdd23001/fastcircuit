"""Representative compiled programs covering every supported construct.

Used by the strict-warning check and the sanitizer job; extend when adding syntax.
"""

from __future__ import annotations

import numpy as np
from qiskit import QuantumCircuit

from support.blocks import as_block


@as_block
def arithmetic(n: int, a: int, b: int, theta: float, angles: np.ndarray):
    qc = QuantumCircuit(n)
    for i in range(n - 1, -1, -1):
        if i % 2 == 0:
            qc.rx(theta / b + a * i - (-a), i)
        else:
            qc.ry(angles[i % len(angles)] * 2.5 + a / b, i)
        qc.cx(i, (i + 1) % n)
    return qc


def calls():
    """(function, args) pairs that exercise success paths."""
    return [(arithmetic, (4, 3, 2, 0.5, np.arange(5.0)))]
