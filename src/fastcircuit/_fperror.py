"""NumPy floating-point error reporting for compiled blocks.

Arithmetic on ``np.float64`` values never raises in Python; NumPy instead reports
division by zero, overflow, underflow and invalid operations according to
``np.errstate`` (by default a ``RuntimeWarning``). Compiled code computes the same
values and, when a result is infinite, NaN or tiny (a superset of the cases where
the CPU raises a floating-point flag), calls ``report`` here. ``report`` asks NumPy
which flags the operation raises and reports them as NumPy would, with warnings
attributed to the block's source line.
"""

from __future__ import annotations

import operator
import sys
import threading
import warnings
from collections.abc import Callable
from typing import Any

import numpy as np

from . import _npufunc

# Operation codes shared with the code generators.
ADD, SUB, MUL, TRUEDIV, FLOORDIV, MOD, POW = range(7)
_OPS: tuple[tuple[Callable[[Any, Any], Any], str], ...] = (
    (operator.add, "add"),
    (operator.sub, "subtract"),
    (operator.mul, "multiply"),
    (operator.truediv, "divide"),
    (operator.floordiv, "floor_divide"),
    (operator.mod, "remainder"),
    (operator.pow, "power"),
)
# NumPy checks the flags in this order (numpy/_core/src/umath/extobj.c).
_FLAGS = (("divide", "divide by zero"), ("over", "overflow"), ("under", "underflow"),
          ("invalid", "invalid value"))  # fmt: skip


UFUNC_BASE = 16  # op codes from here on: _npufunc.UFUNCS[op - UFUNC_BASE](a[, b])


def report(op: int, a: float, b: float, line: int) -> None:
    """Report ``a <op> b`` (or ``ufunc(a[, b])``) on np.float64 values as NumPy
    would; may raise."""
    func: Callable[[Any, Any], Any]
    if op >= UFUNC_BASE:
        ufunc = _npufunc.UFUNCS[op - UFUNC_BASE]
        func = ufunc if ufunc.nin == 2 else (lambda u, _: ufunc(u))
        name = ufunc.__name__
    else:
        func, name = _OPS[op]
        name = "scalar " + name
    _report(func, np.float64(a), np.float64(b), name, line)


NEG, ABS = 7, 8  # np.int64 only, besides the operation codes above
_INT_OPS = (*_OPS, (lambda x, _: -x, "negative"), (lambda x, _: abs(x), "absolute"))


def report_int(op: int, a: int, b: int, line: int) -> None:
    """Report ``a <op> b`` on np.int64 values (overflow, division by zero)."""
    func, name = _INT_OPS[op]
    _report(func, np.int64(a), np.int64(b), "scalar " + name, line)


def _report(func: Callable[[Any, Any], Any], x: Any, y: Any, name: str, line: int) -> None:
    raised = []
    for flag, text in _FLAGS:
        with np.errstate(all="ignore", **{flag: "raise"}):  # type: ignore[arg-type]
            try:
                func(x, y)
            except FloatingPointError:
                raised.append((flag, text))
    if not raised:
        return
    modes: Any = np.geterr()  # indexed by flag name
    if any(modes[flag] not in ("warn", "ignore") for flag, _ in raised):
        func(x, y)  # 'raise', 'call', 'print' or 'log': let NumPy act itself
        return
    frame = _caller()
    g = frame.f_globals
    for flag, text in raised:
        if modes[flag] == "warn":
            warnings.warn_explicit(
                RuntimeWarning(f"{text} encountered in {name}"),
                RuntimeWarning,
                frame.f_code.co_filename,
                line,
                g.get("__name__", "<string>"),
                g.setdefault("__warningregistry__", {}),
                g,
            )


def _caller() -> Any:
    """The frame that ran the block (the first one outside fastcircuit)."""
    frame = sys._getframe(1)
    while frame.f_back is not None and frame.f_globals.get("__name__", "").split(".")[0] == (
        "fastcircuit"
    ):
        frame = frame.f_back
    return frame


# The LLVM backend calls ``report`` through a ctypes callback, which cannot raise:
# the exception is kept here and re-raised by the caller of the kernel.
_PENDING = threading.local()


def report_from_callback(op: int, a: float, b: float, line: int) -> int:
    try:
        report(op, a, b, line)
    except BaseException as exc:  # re-raised by take_pending()
        _PENDING.exc = exc
        return 1
    return 0


def report_int_from_callback(op: int, a: int, b: int, line: int) -> int:
    try:
        report_int(op, a, b, line)
    except BaseException as exc:  # re-raised by take_pending()
        _PENDING.exc = exc
        return 1
    return 0


def take_pending() -> BaseException:
    exc: BaseException = _PENDING.__dict__.pop("exc")
    return exc
