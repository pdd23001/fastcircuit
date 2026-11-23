"""'with fastcircuit:' blocks, compiled and run against real Qiskit circuits.

Each module is imported through the regular import system, so fastcircuit's
import hook rewrites it. The reference result comes from the same source with the
block turned into 'if True:' and run as plain Python.
"""

from __future__ import annotations

import ast
import textwrap
import types

import numpy as np
import pytest
from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister

import fastcircuit
from fastcircuit import (
    BlockNotCompiledError,
    QubitIndexError,
    UnsupportedSyntax,
    _block,
    _hooks,
)
from fastcircuit._rewrite import RUNTIME

from support.circuits import assert_same_structure
from support.modules import import_module, load_module


@pytest.fixture
def blocks(tmp_path, monkeypatch):
    """``(source) -> (rewritten module, plain-Python reference module)``."""
    monkeypatch.syspath_prepend(str(tmp_path))

    def factory(source: str):
        source = textwrap.dedent(source)
        reference = source.replace("with fastcircuit:", "if True:").replace(
            "with fastcircuit.block(debug=True):", "if True:"
        )
        return import_module(tmp_path, source), load_module(
            tmp_path,
            reference,
            header="import math\nimport numpy as np\nfrom qiskit import QuantumCircuit\n",
        )

    return factory


LOOPS = """
def build(n, theta):
    qc = QuantumCircuit(n, n)
    qc.h(0)
    with fastcircuit:
        for i in range(n - 1):
            qc.cx(i, i + 1)
            qc.rz(theta * i, i)
        for i in range(n):
            qc.measure(i, i)
    qc.barrier()
    return qc
"""


def test_block_matches_plain_python_and_keeps_surrounding_instructions(blocks):
    module, reference = blocks(LOOPS)
    for n in (1, 2, 5):
        assert_same_structure(module.build(n, 0.25), reference.build(n, 0.25))


def test_int_and_float_values_get_separate_specializations(blocks):
    module, reference = blocks(LOOPS)
    assert_same_structure(module.build(3, 2), reference.build(3, 2))
    assert_same_structure(module.build(3, 0.5), reference.build(3, 0.5))
    [site_id] = [i for i, s in enumerate(_block._SITES) if s.filename == module.__file__]
    kinds = sorted(key[1] for key in _block._STATES[site_id].compiled)
    assert kinds == [("int", "float"), ("int", "int")]


def test_arrays_constants_and_block_local_variables(blocks):
    module, reference = blocks(
        """
        def build(angles):
            n = len(angles)
            qc = QuantumCircuit(n)
            with fastcircuit:
                half = math.pi / 2
                for i in range(n):
                    qc.ry(angles[i] + half, i)
                    qc.rz(np.pi * i, i)
            return qc
        """
    )
    angles = np.linspace(0.0, 1.0, 4)
    assert_same_structure(module.build(angles), reference.build(angles))


def test_circuit_with_registers(blocks):
    module, reference = blocks(
        """
        def build(qc, n):
            with fastcircuit:
                for i in range(n):
                    qc.h(i)
                    qc.measure(i, i)
            return qc
        """
    )

    def circuit():
        return QuantumCircuit(
            QuantumRegister(2, "a"), QuantumRegister(1, "b"), ClassicalRegister(3, "m")
        )

    assert_same_structure(module.build(circuit(), 3), reference.build(circuit(), 3))


def test_method_and_nested_function(blocks):
    module, reference = blocks(
        """
        class Builder:
            def __init__(self, n):
                self.n = n

            def build(self):
                n = self.n
                qc = QuantumCircuit(n)
                with fastcircuit:
                    for i in range(n):
                        qc.x(i)
                return qc


        def outer(n):
            def inner(qc):
                with fastcircuit:
                    for i in range(n):
                        qc.h(i)
            qc = QuantumCircuit(n)
            inner(qc)
            return qc
        """
    )
    assert_same_structure(module.Builder(3).build(), reference.Builder(3).build())
    assert_same_structure(module.outer(4), reference.outer(4))


def test_module_level_block_in_imported_module(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    module = import_module(
        tmp_path,
        """
        qc = QuantumCircuit(3)
        with fastcircuit:
            for i in range(3):
                qc.h(i)
        """,
    )
    assert [inst.operation.name for inst in module.qc.data] == ["h", "h", "h"]


def test_out_of_range_qubit_raises_with_the_block_line(blocks):
    module, _ = blocks(
        """
        def build(n):
            qc = QuantumCircuit(2)
            with fastcircuit:
                for i in range(n):
                    qc.h(i)
            return qc
        """
    )
    with pytest.raises(QubitIndexError) as info:
        module.build(3)
    message = str(info.value)
    assert "Index 2 out of range for size 2" in message
    # 4 header lines and 2 blank lines come before 'def build' (line 7)
    assert f"{module.__file__}, line 11, in build.<block line 9>" in message


@pytest.mark.parametrize(
    ("setup", "fragment"),
    [
        ("flag = True", "is a bool"),
        ("flag = [1, 2]", "is a list"),
        ("flag = np.int32(1)", "convert with int"),
    ],
)
def test_unsupported_values_raise_type_error(blocks, setup, fragment):
    module, _ = blocks(
        f"""
        def build():
            {setup}
            qc = QuantumCircuit(1)
            with fastcircuit:
                qc.rz(flag, 0)
            return qc
        """
    )
    with pytest.raises(TypeError, match=fragment):
        module.build()


def test_block_needs_exactly_one_plain_quantum_circuit(blocks):
    module, _ = blocks(
        """
        class Sub(QuantumCircuit):
            pass

        def none(n):
            with fastcircuit:
                n.bit_length()

        def two(a, b):
            with fastcircuit:
                a.h(0)
                b.h(0)

        def subclass():
            qc = Sub(1)
            with fastcircuit:
                qc.h(0)
        """
    )
    with pytest.raises(TypeError, match="created before it"):
        module.none(3)
    with pytest.raises(TypeError, match="several circuits"):
        module.two(QuantumCircuit(1), QuantumCircuit(1))
    with pytest.raises(TypeError, match="not subclasses"):
        module.subclass()


def test_unsupported_statement_fails_on_first_run(blocks):
    module, _ = blocks(
        """
        def build(n):
            qc = QuantumCircuit(n)
            with fastcircuit:
                while n > 0:
                    qc.h(0)
            return qc
        """
    )
    with pytest.raises(UnsupportedSyntax):
        module.build(1)


def test_assignment_to_outer_variable_fails_at_import(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(UnsupportedSyntax, match="assigned inside the block"):
        import_module(
            tmp_path,
            """
            def build(qc):
                k = 0
                with fastcircuit:
                    k = 1
                    qc.h(k)
                return k
            """,
        )


def test_debug_prints_compilation_report(blocks, capsys):
    module, _ = blocks(
        """
        def build(n):
            qc = QuantumCircuit(n)
            with fastcircuit.block(debug=True):
                for i in range(n):
                    qc.h(i)
            return qc
        """
    )
    module.build(2)
    err = capsys.readouterr().err
    assert "fastcircuit: block" in err
    assert "specialized for (n: int)" in err
    module.build(3)  # already compiled: no second report
    assert capsys.readouterr().err == ""


def test_block_not_rewritten_raises_instead_of_running_as_python(tmp_path):
    module = load_module(  # spec_from_file_location bypasses the import hook
        tmp_path,
        """
        def build():
            qc = QuantumCircuit(1)
            with fastcircuit:
                qc.h(0)
            return qc
        """,
        header="from qiskit import QuantumCircuit\nimport fastcircuit\n",
    )
    with pytest.raises(BlockNotCompiledError, match="was not compiled"):
        module.build()


def test_patch_module_rewrites_functions_of_a_loaded_module(tmp_path):
    module = load_module(
        tmp_path,
        """
        def build(n):
            qc = QuantumCircuit(n)
            with fastcircuit:
                for i in range(n):
                    qc.h(i)
            return qc
        """,
        header="from qiskit import QuantumCircuit\nimport fastcircuit\n",
    )
    assert _hooks.patch_module(vars(module), module.__file__) == 1
    assert [inst.operation.name for inst in module.build(2).data] == ["h", "h"]


def test_ipython_transformer_and_extension():
    cell = textwrap.dedent(
        """
        import fastcircuit
        qc = QuantumCircuit(2)
        with fastcircuit:
            for i in range(2):
                qc.x(i)
        """
    )
    tree = _hooks.IPythonTransformer().visit(ast.parse(cell))
    namespace = {"QuantumCircuit": QuantumCircuit, RUNTIME: _block}
    exec(compile(tree, "<cell>", "exec"), namespace)  # noqa: S102
    assert [inst.operation.name for inst in namespace["qc"].data] == ["x", "x"]

    shell = types.SimpleNamespace(ast_transformers=[], user_ns={})
    fastcircuit.load_ipython_extension(shell)
    fastcircuit.load_ipython_extension(shell)  # idempotent
    assert len(shell.ast_transformers) == 1
    assert shell.user_ns[RUNTIME] is _block
