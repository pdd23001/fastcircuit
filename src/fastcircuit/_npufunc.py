"""NumPy math functions in compiled blocks: their own float64 inner loops.

``np.sin(x)`` on a scalar runs the ufunc's ``d->d`` inner loop once. Compiled code
calls that same loop directly, so results are bit-identical to NumPy on every CPU
(NumPy uses its own SIMD implementations for several functions, which can differ
from the C library's). The loop addresses are read from the ufunc object
(``PyUFuncObject`` in numpy/ufuncobject.h, a layout unchanged since NumPy 1.x),
checked against the ufunc's Python attributes, and self-tested against the ufunc
itself before first use.
"""

from __future__ import annotations

import ctypes
import functools
import struct
import warnings
from typing import Any

import numpy as np

from .errors import IncompatibleEnvironmentError

_NAMES = (  # noqa: SIM905 - a word list reads better than 47 string literals
    # one argument
    "sin cos tan arcsin arccos arctan sinh cosh tanh arcsinh arccosh arctanh "
    "exp exp2 expm1 log log2 log10 log1p sqrt cbrt square reciprocal fabs absolute "
    "negative positive floor ceil trunc rint sign deg2rad rad2deg degrees radians "
    # two arguments
    "arctan2 hypot copysign fmod power maximum minimum fmax fmin logaddexp logaddexp2"
).split()
UFUNCS: tuple[Any, ...] = tuple(getattr(np, n) for n in _NAMES)  # index = code in generated code
BY_ID: dict[int, int] = {id(u): i for i, u in enumerate(UFUNCS)}  # np.abs is np.absolute

_P = ctypes.sizeof(ctypes.c_void_p)
_LOOP = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)


def lookup(obj: Any) -> int | None:
    """Index of the supported ufunc ``obj`` is, or None."""
    i = BY_ID.get(id(obj))
    return i if i is not None and UFUNCS[i] is obj else None


def result_type(index: int, kinds: tuple[str, ...]) -> type:
    """Python type NumPy returns for arguments of these kinds ("int" or a float)."""
    samples = [3 if k == "int" else 0.5 for k in kinds]
    with np.errstate(all="ignore"):
        return type(UFUNCS[index](*samples))


def _loop(ufunc: Any) -> tuple[int, int]:
    base = id(ufunc)
    head = object.__basicsize__  # PyObject_HEAD
    nin, nout, nargs = struct.unpack("3i", ctypes.string_at(base + head, 12))
    functions = ctypes.c_void_p.from_address(base + head + 16).value
    data = ctypes.c_void_p.from_address(base + head + 16 + _P).value
    ntypes = ctypes.c_int.from_address(base + head + 16 + 2 * _P).value
    if (nin, nout, nargs, ntypes) != (ufunc.nin, ufunc.nout, ufunc.nargs, ufunc.ntypes) or (
        not functions
    ):
        raise IncompatibleEnvironmentError(
            f"cannot read the inner loops of np.{ufunc.__name__} in NumPy {np.__version__}"
        )
    i = ufunc.types.index("d" * ufunc.nin + "->d")
    func = ctypes.c_void_p.from_address(functions + _P * i).value or 0
    datum = (ctypes.c_void_p.from_address(data + _P * i).value or 0) if data else 0
    return func, datum


def _call(func: int, datum: int, *xs: float) -> float:
    n = len(xs) + 1
    values = (ctypes.c_double * n)(*xs, 0.0)
    args = (ctypes.c_void_p * n)(*(ctypes.addressof(values) + 8 * k for k in range(n)))
    steps = (ctypes.c_ssize_t * n)(*([8] * n))
    _LOOP(func)(args, ctypes.byref(ctypes.c_ssize_t(1)), steps, datum or None)
    return float(values[n - 1])


_SAMPLES = (0.0, -0.0, 0.3, -1.7, 2.5, 1e-310, 7e2, -7.5e2, float("inf"), float("nan"))


@functools.cache
def loop_table() -> tuple[tuple[int, int], ...]:
    """(function, data) address of each ufunc's float64 loop, in ``UFUNCS`` order."""
    table = tuple(_loop(u) for u in UFUNCS)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for u, (func, datum) in zip(UFUNCS, table, strict=True):
            for k, x in enumerate(_SAMPLES):
                xs = (x,) if u.nin == 1 else (x, _SAMPLES[(k + 3) % len(_SAMPLES)])
                want = struct.pack("d", float(u(*map(np.float64, xs))))
                if struct.pack("d", _call(func, datum, *xs)) != want:
                    raise IncompatibleEnvironmentError(
                        f"the float64 loop of np.{u.__name__} does not reproduce "
                        f"np.{u.__name__} in NumPy {np.__version__}"
                    )
    return table
