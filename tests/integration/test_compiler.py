import gc
import unittest

import numpy as np
from qiskit import QuantumCircuit, transpile
from qiskit.quantum_info import Operator

from fastcircuit import UnsupportedSyntax

from support.blocks import as_block
from support.examples import array_angles, broadcast, ghz, layered


@as_block
def reverse(n: int, step: int):
    qc = QuantumCircuit(n)
    for i in range(n - 1, -1, step):
        qc.x(i)
    qc.z(-1)
    return qc


@as_block
def arithmetic(n: int, a: int, b: int, theta: float):
    qc = QuantumCircuit(n)
    qc.rx(theta / b, (a * b + 1) % n)
    return qc


@as_block
def indexed(n: int, angles: np.ndarray, index: int):
    qc = QuantumCircuit(n)
    qc.rz(angles[index], 0)
    return qc


@as_block
def pair(n: int, a: int, b: int):
    qc = QuantumCircuit(n)
    qc.cx(a, b)
    return qc


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


@as_block
def branches(n: int, theta: float = 0.2):
    qc = QuantumCircuit(n)
    for i in range(0, n, 2):
        if i < 3:
            qc.rx(theta * (i + 1), i)
        else:
            qc.ry(-theta, i)
    return qc


class Correctness(unittest.TestCase):
    def same(self, fn, *args):
        actual = fn(*args)
        expected = fn.python(*args)
        self.assertIsInstance(actual, QuantumCircuit)
        self.assertEqual(actual, expected)
        self.assertEqual(actual.qregs, expected.qregs)
        self.assertTrue(Operator(actual).equiv(Operator(expected)))

    def test_nested_loops(self):
        for n in (1, 2, 5):
            for depth in (0, 1, 4):
                self.same(layered, n, depth, 0.25)

    def test_array_and_branch(self):
        rng = np.random.default_rng(42)
        for n in (2, 3, 5):
            self.same(array_angles, n, rng.normal(size=23))

    def test_gates(self):
        self.same(all_gates, 0.37)

    def test_reverse_and_negative_qubits(self):
        self.same(reverse, 5, -2)

    def test_negative_modulo(self):
        self.same(arithmetic, 3, -3, 2, 0.3)

    def test_array_negative_index(self):
        self.same(indexed, 2, np.array([0.1, 0.3]), -1)

    def test_transpile(self):
        qc = ghz(5)
        self.assertTrue(
            Operator(transpile(qc, basis_gates=["rz", "sx", "x", "cx"])).equiv(Operator(qc))
        )

    def test_broadcast_benchmark_equivalence(self):
        self.assertTrue(Operator(broadcast(4, 3, 0.17)).equiv(Operator(layered.python(4, 3, 0.17))))

    def test_repeated_lifetime(self):
        for _ in range(100):
            self.assertEqual(layered(3, 2, 0.1), layered.python(3, 2, 0.1))
        gc.collect()

    def test_keywords(self):
        self.assertEqual(layered(layers=2, theta=0.1, n=3), layered.python(3, 2, 0.1))

    def test_cache(self):
        ghz(3)
        first = ghz.compiled
        ghz(5)  # same types: the same specialization
        self.assertIs(ghz.compiled, first)

    def test_defaults_else_and_step(self):
        self.same(branches, 5)

    def test_empty(self):
        self.assertEqual(branches(0), branches.python(0))
        self.assertEqual(array_angles(0, np.array([])), array_angles.python(0, np.array([])))

    def test_randomized(self):
        rng = np.random.default_rng(456)
        for _ in range(30):
            n = int(rng.integers(2, 6))
            depth = int(rng.integers(0, 8))
            angle = float(rng.normal())
            self.assertEqual(layered(n, depth, angle), layered.python(n, depth, angle))


class Rejections(unittest.TestCase):
    def test_zero_step(self):
        with self.assertRaises(ValueError):
            reverse(2, 0)

    def test_bounds(self):
        for a, b in ((0, 2), (-3, 0)):
            with self.assertRaises(IndexError):
                pair(2, a, b)

    def test_duplicate(self):
        with self.assertRaises(ValueError):
            pair(2, 0, -2)

    def test_array_bounds(self):
        for i in (-2, 1):
            with self.assertRaises(IndexError):
                indexed(1, np.array([0.1]), i)

    def test_zero_division(self):
        with self.assertRaises(ZeroDivisionError):
            arithmetic(2, 1, 0, 0.1)

    def test_overflow(self):
        with self.assertRaises(OverflowError):
            arithmetic(2, 2**62, 4, 0.1)
        with self.assertRaises(OverflowError):
            ghz(2**64)

    def test_types(self):
        with self.assertRaises(TypeError):
            ghz(True)
        with self.assertRaises(UnsupportedSyntax):
            ghz(2.0)  # a float where the block needs an int
        unsupported = (
            [0.1],
            np.array([1], dtype=np.int32),
            np.zeros((2, 2, 2)),
            np.array([1j]),
            np.arange(10.0)[::2],  # not contiguous
        )
        for a in unsupported:
            with self.assertRaises(TypeError):
                indexed(1, a, 0)
        with self.assertRaises(UnsupportedSyntax):  # a 2-D array needs two indices
            indexed(1, np.zeros((2, 2)), 0)

    def test_unsupported(self):
        def bad(n: int):
            qc = QuantumCircuit(n)
            print(n)
            return qc

        with self.assertRaises(UnsupportedSyntax):
            as_block(bad)

    def test_loop_variable_escape(self):
        def bad(n: int):
            qc = QuantumCircuit(n)
            for i in range(n):
                qc.h(i)
            qc.x(i)
            return qc

        with self.assertRaises(UnsupportedSyntax):
            as_block(bad)

    def test_float_index(self):
        def bad(n: int):
            qc = QuantumCircuit(n)
            qc.h(1.0)
            return qc

        with self.assertRaises(UnsupportedSyntax):
            as_block(bad)

    def test_shadow_range(self):
        def bad(n: int, range: int):
            qc = QuantumCircuit(n)
            for i in range(n):
                qc.h(i)
            return qc

        with self.assertRaises(UnsupportedSyntax):
            as_block(bad)

    def test_shadow_circuit(self):
        def bad(n: int):
            qc = QuantumCircuit(n)
            for qc in range(n):
                qc.h(0)
            return qc

        with self.assertRaises(UnsupportedSyntax):
            as_block(bad)


if __name__ == "__main__":
    unittest.main()
