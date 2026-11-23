"""Circuit comparison helpers shared by the native test layers."""

from __future__ import annotations

import math
import warnings

from qiskit import QuantumCircuit
from qiskit.quantum_info import Operator, Statevector


def assert_same_structure(actual: QuantumCircuit, expected: QuantumCircuit) -> None:
    """Registers, global phase and every ordered instruction, compared directly.

    ``QuantumCircuit.__eq__`` converts to DAGs, which is slow for large circuits
    and ignores instruction order between commuting gates.
    """
    assert type(actual) is QuantumCircuit
    assert actual.qregs == expected.qregs
    assert actual.cregs == expected.cregs
    assert actual.num_qubits == expected.num_qubits
    assert actual.num_clbits == expected.num_clbits
    assert actual.global_phase == expected.global_phase
    assert len(actual.data) == len(expected.data)
    for i, (a, b) in enumerate(zip(actual.data, expected.data, strict=True)):
        assert a.operation.name == b.operation.name, f"instruction {i}: {a} != {b}"
        assert a == b, f"instruction {i}: {a} != {b}"


def assert_equivalent_small(actual: QuantumCircuit, expected: QuantumCircuit) -> None:
    """Structure plus operator and statevector equivalence (small, unitary circuits)."""
    assert_same_structure(actual, expected)
    if actual.num_qubits <= 8 and actual.num_clbits == 0:
        assert Operator(actual).equiv(Operator(expected))
        assert Statevector(actual).equiv(Statevector(expected))


def _run(builder, args):
    """(circuit, error, warnings) of one run; warnings as (category, text, file, line)."""
    circuit = error = None
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            circuit = builder(*args)
        except Exception as exc:
            error = exc
    seen = [(w.category, str(w.message), w.filename, w.lineno) for w in caught]
    return circuit, error, seen


def same_outcome(fn, *args):
    """Run Python and native builders; return (circuit, error) of each.

    Both runs must issue the same warnings (NumPy's RuntimeWarnings), from the
    same source line, except those Python issues after native code stopped at
    the int64 restriction.
    """
    expected, expected_error, expected_warnings = _run(fn.python, args)
    actual, actual_error, actual_warnings = _run(fn, args)
    if is_int64_restriction(actual_error):
        # Native code stopped early (the documented int64 restriction); Python went
        # on and may warn later. Up to that point the warnings must agree.
        expected_warnings = expected_warnings[: len(actual_warnings)]
    assert actual_warnings == expected_warnings, (actual_warnings, expected_warnings)
    return expected, expected_error, actual, actual_error


def _same_float(a: float, b: float) -> bool:
    """Bitwise float equality: NaN == NaN and 0.0 != -0.0."""
    return math.copysign(1.0, a) == math.copysign(1.0, b) and (
        a == b or (math.isnan(a) and math.isnan(b))
    )


def assert_same_bits(actual: QuantumCircuit, expected: QuantumCircuit) -> None:
    """Like assert_same_structure, but parameters compare bit-for-bit (NaN, -0.0)."""
    assert type(actual) is QuantumCircuit
    assert actual.qregs == expected.qregs and actual.cregs == expected.cregs
    assert actual.global_phase == expected.global_phase
    assert len(actual.data) == len(expected.data)
    for i, (a, b) in enumerate(zip(actual.data, expected.data, strict=True)):
        where = f"instruction {i}: {a} != {b}"
        assert a.operation.name == b.operation.name, where
        assert [actual.find_bit(q).index for q in a.qubits] == [
            expected.find_bit(q).index for q in b.qubits
        ], where
        assert [actual.find_bit(c).index for c in a.clbits] == [
            expected.find_bit(c).index for c in b.clbits
        ], where
        pa, pb = a.operation.params, b.operation.params
        assert len(pa) == len(pb), where
        assert all(_same_float(float(x), float(y)) for x, y in zip(pa, pb, strict=True)), where


def is_int64_restriction(error: BaseException | None) -> bool:
    """Documented deviation: int64 arithmetic raises where Python grows a bigint."""
    return isinstance(error, OverflowError) and ("int64" in str(error) or "2**53" in str(error))


def assert_same_outcome(fn, *args) -> str:
    """Native and Python agree: identical circuits, or the same exception type.

    The only accepted difference is the documented int64 restriction (returns
    "int64-restriction"), when Python succeeded or failed later than native code.
    """
    expected, expected_error, actual, actual_error = same_outcome(fn, *args)
    if is_int64_restriction(actual_error) and not isinstance(expected_error, OverflowError):
        return "int64-restriction"
    if expected_error is not None or actual_error is not None:
        assert expected_error is not None, f"native raised {actual_error!r}, Python did not"
        assert actual_error is not None, f"Python raised {expected_error!r}, native did not"
        assert isinstance(actual_error, type(expected_error)), (
            f"{type(actual_error).__name__} vs Python {type(expected_error).__name__}: "
            f"{actual_error}"
        )
        return type(expected_error).__name__
    assert_same_bits(actual, expected)
    return "ok"
