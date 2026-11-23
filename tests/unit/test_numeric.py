"""Numeric boundary rules enforced in Python (argument binding, literals)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from fastcircuit._codegen_c import _c_string, _float_literal
from fastcircuit._native import _check_argument


@pytest.mark.parametrize("value", [0, 1, -1, 2**63 - 1, -(2**63)])
def test_int_arguments_in_range(value):
    _check_argument("n", "int", value)


@pytest.mark.parametrize("value", [2**63, -(2**63) - 1, 10**30])
def test_int_arguments_out_of_range(value):
    with pytest.raises(OverflowError, match="int64"):
        _check_argument("n", "int", value)


@pytest.mark.parametrize("value", [True, np.int64(3), 3.0, "3", None])
def test_int_arguments_wrong_type(value):
    with pytest.raises(TypeError, match="must be a Python int"):
        _check_argument("n", "int", value)


@pytest.mark.parametrize("value", [0.0, -0.0, math.inf, math.nan, 5e-324])
def test_float_arguments(value):
    _check_argument("theta", "float", value)


@pytest.mark.parametrize("value", [1, np.float64(0.5), np.float32(0.5), True])
def test_float_arguments_are_exact_type(value):
    # int is rejected rather than converted: float(theta) would change the
    # semantics of integer operations that the Python function performs.
    with pytest.raises(TypeError, match="must be a Python float"):
        _check_argument("theta", "float", value)


def _misaligned() -> np.ndarray:
    raw = np.zeros(17, dtype=np.uint8)
    view = np.frombuffer(raw.data, dtype=np.float64, count=2, offset=1)
    assert not view.flags.aligned
    return view


@pytest.mark.parametrize(
    "factory",
    [
        lambda: [0.1, 0.2],
        lambda: np.zeros(3, dtype=np.float32),
        lambda: np.zeros(3, dtype=np.int64),
        lambda: np.zeros(3, dtype=">f8"),
        lambda: np.zeros((2, 2)),
        lambda: np.arange(10.0)[::2],
        lambda: np.zeros(3).view(np.matrix),
        _misaligned,
    ],
    ids=["list", "float32", "int64", "big_endian", "2d", "strided", "subclass", "misaligned"],
)
def test_array_arguments_rejected(factory):
    with pytest.raises(TypeError, match=r"float64 numpy\.ndarray"):
        _check_argument("angles", "array", factory())


def test_array_arguments_accepted():
    _check_argument("angles", "array", np.zeros(0))
    _check_argument("angles", "array", np.arange(4.0))
    readonly = np.arange(4.0)
    readonly.flags.writeable = False
    _check_argument("angles", "array", readonly)


@pytest.mark.parametrize(
    "value", [0.1, -0.0, 5e-324, 1.7976931348623157e308, 1 / 3, 2.0**-1074, 123456789.125]
)
def test_float_literals_are_exact(value):
    literal = _float_literal(value)
    assert float.fromhex(literal.strip("()")) == value
    assert math.copysign(1, float.fromhex(literal.strip("()"))) == math.copysign(1, value)


def test_c_string_escaping():
    assert _c_string('a"b') == '"a\\"b"'
    assert _c_string("a\\b") == '"a\\\\b"'
    assert _c_string("??/") == '"\\?\\?/"'  # no trigraphs in -std=c11 mode
    assert _c_string("é\n") == '"\\303\\251\\012"'
