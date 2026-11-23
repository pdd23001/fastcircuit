"""Supported ``QuantumCircuit`` gate methods and their Qiskit C API enums.

Python argument order is ``(*params, *qubits)``; every entry is verified against the
ordinary Python method by the component tests (exact instruction equality).
"""

from __future__ import annotations

from typing import NamedTuple


class GateSpec(NamedTuple):
    enum: str  # suffix of the C enum constant QkGate_<enum>
    num_params: int
    num_qubits: int


def _table() -> dict[str, GateSpec]:
    table: dict[str, GateSpec] = {}

    def add(names: str, num_params: int, num_qubits: int, **enums: str) -> None:
        for name in names.split():
            table[name] = GateSpec(enums.get(name, name.upper()), num_params, num_qubits)

    add("h x y z s sdg t tdg sx sxdg id", 0, 1, sdg="Sdg", tdg="Tdg", sxdg="SXdg", id="I")
    add("rx ry rz p", 1, 1, p="Phase")
    add("r", 2, 1)
    add("u", 3, 1)
    add("cx cy cz swap ch cs csdg csx dcx ecr iswap", 0, 2, swap="Swap", csdg="CSdg", iswap="ISwap")
    add("crx cry crz cp rxx ryy rzz rzx", 1, 2, cp="CPhase")
    add("cu", 4, 2)
    add("ccx ccz cswap rccx", 0, 3, cswap="CSwap")
    add("rcccx", 0, 4, rcccx="RC3X")
    return table


GATES: dict[str, GateSpec] = _table()
