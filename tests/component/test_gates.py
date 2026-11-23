"""Each supported gate, compiled and compared with the ordinary Python method."""

from __future__ import annotations

import pytest
from qiskit import QuantumCircuit

from fastcircuit._gates import GATES

from support.blocks import as_block
from support.circuits import assert_equivalent_small


def _gate_selector_source() -> str:
    """One function with an if-chain selecting each gate (one compilation)."""
    lines = [
        "@as_block",
        "def apply(which: int, a: float, b: float, c: float, d: float, q0: int, q1: int, "
        "q2: int, q3: int):",
        "    qc = QuantumCircuit(4)",
    ]
    params = ["a", "b", "c", "d"]
    qubits = ["q0", "q1", "q2", "q3"]
    for i, (name, spec) in enumerate(sorted(GATES.items())):
        args = params[: spec.num_params] + qubits[: spec.num_qubits]
        lines.append(f"    if which == {i}:")
        lines.append(f"        qc.{name}({', '.join(args)})")
    lines.append("    return qc")
    return "\n".join(lines) + "\n"


@pytest.fixture(scope="module")
def apply(tmp_path_factory):
    from support.modules import load_module

    module = load_module(tmp_path_factory.mktemp("gates"), _gate_selector_source())
    return module.apply


@pytest.mark.parametrize(("index", "name"), list(enumerate(sorted(GATES))))
def test_gate_matches_python(apply, index, name):
    spec = GATES[name]
    for qubits in ((0, 1, 2, 3), (3, 1, 0, 2), (-1, -2, -3, -4)):
        args = (index, 0.37, -1.2, 2.5, 0.0, *qubits)
        actual, expected = apply(*args), apply.python(*args)
        assert len(actual.data) == 1
        assert actual.data[0].operation.name == expected.data[0].operation.name
        assert actual.data[0].operation.num_qubits == spec.num_qubits
        assert_equivalent_small(actual, expected)


def test_every_gate_in_one_circuit():
    @as_block
    def all_gates(theta: float):
        qc = QuantumCircuit(3)
        qc.h(0)
        qc.x(0)
        qc.y(0)
        qc.z(0)
        qc.s(0)
        qc.sdg(0)
        qc.t(0)
        qc.tdg(0)
        qc.sx(0)
        qc.sxdg(0)
        qc.rx(theta, 0)
        qc.ry(theta, 1)
        qc.rz(theta, 2)
        qc.p(theta, 0)
        qc.cx(0, 1)
        qc.cy(0, 1)
        qc.cz(0, 1)
        qc.swap(0, 1)
        qc.crx(theta, 0, 1)
        qc.cry(theta, 0, 1)
        qc.crz(theta, 0, 1)
        qc.cp(theta, 0, 1)
        qc.rxx(theta, 0, 1)
        qc.ryy(theta, 0, 1)
        qc.rzz(theta, 0, 1)
        qc.rzx(theta, 0, 1)
        qc.ccx(0, 1, 2)
        return qc

    assert_equivalent_small(all_gates(0.123), all_gates.python(0.123))
