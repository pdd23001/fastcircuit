"""Blocks + compiler + persistent cache + real Qiskit, end to end in-repo."""

from __future__ import annotations

import gc

import numpy as np
import pytest
from qiskit import QuantumCircuit, transpile
from qiskit.quantum_info import Operator

from fastcircuit import _build

from support import examples
from support.blocks import as_block
from support.circuits import assert_equivalent_small, assert_same_structure
from support.processes import (
    child_env,
    compile_invocations,
    logging_compiler,
    run_child,
    write_module,
)


@pytest.mark.needs_c_backend
def test_c_backend_cold_compile_then_cross_process_reuse_without_compiler(tmp_path):
    cache, log = tmp_path / "cache", tmp_path / "cc.log"
    module = write_module(tmp_path)
    cc = logging_compiler(tmp_path, log, _build.find_toolchain().path)
    first = run_child(tmp_path, module, child_env(cache, CC=str(cc), FASTCIRCUIT_BACKEND="c"))
    assert first["source"] == "compiled" and first["equal"]
    assert first["build_seconds"] > 0
    invocations = log.read_text()
    second = run_child(tmp_path, module, child_env(cache, CC=str(cc), FASTCIRCUIT_BACKEND="c"))
    assert second["source"] == "persistent cache" and second["equal"]
    assert second["build_seconds"] is None
    assert log.read_text() == invocations  # not even 'cc --version' was run
    assert len(compile_invocations(log)) == 1
    # With no compiler at all, a valid cached build is still usable.
    third = run_child(
        tmp_path, module, child_env(cache, CC="/nonexistent/cc", FASTCIRCUIT_BACKEND="c")
    )
    assert third["source"] == "persistent cache" and third["equal"]


def test_in_process_reuse(make_function, isolated_cache):
    source = """
    @as_block
    def build(n: int):
        qc = QuantumCircuit(n)
        qc.h(0)
        qc.rz(0.987654, 0)
        return qc
    """

    first = make_function(source)
    first(2)
    assert first.timings["source"] == "compiled"
    second = as_block(first.python)  # the same block again: a new site, the same code
    second(2)
    assert second.timings["source"] == "process"
    assert second.cache_key == first.cache_key
    # The same code in another file is a different key: diagnostics name the file.
    third = make_function(source)
    third(2)
    assert third.cache_key != first.cache_key


@pytest.mark.parametrize(
    ("fn", "args"),
    [
        (examples.layered, (5, 3, 0.25)),
        (examples.array_angles, (4, np.linspace(-1, 1, 9))),
        (examples.ghz, (6,)),
    ],
)
def test_examples_match_python(fn, args):
    assert_equivalent_small(fn(*args), fn.python(*args))


@pytest.mark.parametrize("level", [0, 1, 2, 3])
def test_transpiler_interoperability(level):
    qc = examples.layered(4, 2, 0.3)
    out = transpile(
        qc, basis_gates=["rz", "sx", "x", "cx"], optimization_level=level, seed_transpiler=7
    )
    assert set(out.count_ops()) <= {"rz", "sx", "x", "cx"}
    assert Operator(out).equiv(Operator(qc))


def test_compiled_circuit_supports_normal_operations():
    qc = examples.layered(3, 2, 0.1)
    ref = examples.layered.python(3, 2, 0.1)
    assert qc.copy() == ref
    assert qc.inverse() == ref.inverse()
    assert qc.depth() == ref.depth()
    qc.measure_all()
    ref.measure_all()
    assert qc == ref
    composed = QuantumCircuit(3).compose(examples.ghz(3))
    assert composed == QuantumCircuit(3).compose(examples.ghz.python(3))


def test_returned_circuit_survives_other_builds_and_deletions():
    keep = examples.layered(4, 3, 0.7)
    snapshot = examples.layered.python(4, 3, 0.7)
    for i in range(200):
        tmp = examples.layered(4, 3, float(i))
        del tmp
        if i % 50 == 0:
            gc.collect()
    others = [examples.array_angles(4, np.full(10, float(i))) for i in range(20)]
    del others
    gc.collect()
    assert_same_structure(keep, snapshot)


def test_large_circuit_ordered_instructions():
    qc = examples.layered(50, 40, 0.11)
    assert len(qc) == 50 * 40 * 2 + 49 * 40
    assert_same_structure(qc, examples.layered.python(50, 40, 0.11))
