"""Randomized differential tests: random arithmetic in a block against plain Python.

A fixed, seeded set of programs (so every run checks the same code) mixes Python
``int``/``float`` and ``np.float64`` values with ``+ - * / // % **``, ``abs``,
``min``, ``max``, ``int``, ``float``, conditional expressions, ``math`` and NumPy
functions. Hypothesis chooses the arguments, leaning on edge cases (0, -0.0, NaN,
infinities, subnormals, int64 limits), and shrinks any failure to a small one.
Each program must give the same circuit, bit for bit, or raise the same exception
type, with the same warnings, as plain Python.
"""

from __future__ import annotations

import contextlib
import importlib.util
import math
import random
from typing import Any

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from fastcircuit.errors import UnsupportedSyntax

from support.blocks import as_block
from support.circuits import assert_same_outcome, same_outcome

SEED = 20261123
PROGRAMS = 40

_INT_LITS = ["0", "1", "2", "3", "7", "-1", "-5", "1024", "4611686018427387904"]
_FLOAT_LITS = ["0.0", "-0.0", "0.5", "-2.5", "3.0", "1e300", "1e-310", "math.pi"]
_MATH1 = ["sqrt", "sin", "cos", "tan", "exp", "log", "log2", "log10", "log1p", "expm1",
          "atan", "asin", "acos", "sinh", "cosh", "tanh", "fabs", "cbrt", "degrees",
          "radians", "floor", "ceil", "trunc", "asinh", "acosh", "atanh", "exp2"]  # fmt: skip
_MATH2 = ["atan2", "copysign", "fmod"]
_NP1 = ["sin", "sqrt", "log", "exp", "floor", "rint", "sign", "square", "reciprocal",
        "arctan", "cbrt", "absolute", "negative", "trunc", "deg2rad"]  # fmt: skip
_NP2 = ["arctan2", "hypot", "power", "maximum", "minimum", "fmax", "fmin", "fmod",
        "logaddexp", "copysign"]  # fmt: skip


def _expression(rng: random.Random, depth: int) -> str:
    if depth == 0 or rng.random() < 0.25:
        r = rng.random()
        if r < 0.25:
            return rng.choice(["a", "b"])
        if r < 0.5:
            return rng.choice(["x", "y"])
        if r < 0.6:
            return "u"
        return rng.choice(_INT_LITS if r < 0.8 else _FLOAT_LITS)
    sub = lambda: _expression(rng, depth - 1)
    r = rng.random()
    if r < 0.35:
        return f"({sub()} {rng.choice(['+', '-', '*', '/', '//', '%'])} {sub()})"
    if r < 0.42:
        return f"(-{sub()})"
    if r < 0.48:
        return f"({sub()} ** {rng.randint(0, 5)})"
    if r < 0.56:
        return f"{rng.choice(['abs', 'int', 'float'])}({sub()})"
    if r < 0.62:
        return f"{rng.choice(['min', 'max'])}({sub()}, {sub()})"
    if r < 0.72:
        return f"math.{rng.choice(_MATH1)}({sub()})"
    if r < 0.76:
        return f"math.{rng.choice(_MATH2)}({sub()}, {sub()})"
    if r < 0.84:
        return f"np.{rng.choice(_NP1)}(float({sub()}))"
    if r < 0.88:
        return f"np.{rng.choice(_NP2)}(float({sub()}), {sub()})"
    cmp = rng.choice(["<", "<=", ">", ">=", "==", "!="])
    return f"({sub()} if {sub()} {cmp} {sub()} else {sub()})"


def _program_source() -> str:
    rng = random.Random(SEED)  # noqa: S311 (reproducible programs, not secrets)
    lines = ["import math", "import numpy as np", "from qiskit import QuantumCircuit", ""]
    for i in range(PROGRAMS):
        lines += [
            f"def f{i}(a: int, b: int, x: float, y: float, u: np.float64):",
            "    qc = QuantumCircuit(1)",
            f"    t = {_expression(rng, 3)}",
            f"    s = {_expression(rng, 2)}",
            "    qc.rx(t, 0)",
            "    qc.rz(s, 0)",
            "    return qc",
            "",
        ]
    return "\n".join(lines)


@pytest.fixture(scope="module")
def blocks(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """The generated programs that are in the supported subset, by name."""
    path = tmp_path_factory.mktemp("programs") / "generated_programs.py"
    path.write_text(_program_source())
    spec = importlib.util.spec_from_file_location("generated_programs", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    compiled = {}
    for i in range(PROGRAMS):
        with contextlib.suppress(UnsupportedSyntax):  # mixed types the subset rejects
            compiled[f"f{i}"] = as_block(getattr(module, f"f{i}"))
    assert len(compiled) >= PROGRAMS // 2, "too few programs are in the supported subset"
    return compiled


_EDGE_INTS = [0, 1, -1, 2, 7, -7, 2**31, -(2**62), 2**63 - 1, -(2**63)]
_EDGE_FLOATS = [0.0, -0.0, 1.5, -2.5, 0.1, 1e308, -1e-310, math.inf, -math.inf, math.nan]
_ints = st.sampled_from(_EDGE_INTS) | st.integers(-(2**63), 2**63 - 1)
_floats = st.sampled_from(_EDGE_FLOATS) | st.floats(allow_nan=True, allow_infinity=True)


def _same_ignoring_nan_sign(actual: Any, expected: Any) -> bool:
    def params(qc: Any) -> list[float]:
        return [float(p) for inst in qc.data for p in inst.operation.params]

    return all(
        (math.isnan(p) and math.isnan(q)) or p == q
        for p, q in zip(params(actual), params(expected), strict=True)
    )


@settings(
    max_examples=40,
    deadline=None,
    derandomize=True,
    database=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(a=_ints, b=_ints, x=_floats, y=_floats, u=_floats.map(np.float64))
def test_blocks_match_python(blocks, a, b, x, y, u):
    for name, block in blocks.items():
        try:
            assert_same_outcome(block, a, b, x, y, u)
        except AssertionError as exc:
            # Documented: the sign of a NaN produced from two NaN operands is
            # build-dependent (docs/supported-syntax.md). Accept only that difference.
            expected, expected_error, actual, actual_error = same_outcome(block, a, b, x, y, u)
            if expected_error or actual_error or not _same_ignoring_nan_sign(actual, expected):
                raise AssertionError(f"program {name}: {exc}") from exc
