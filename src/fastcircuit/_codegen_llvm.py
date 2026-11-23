"""Typed IR -> LLVM IR (via llvmlite) for in-process compilation.

The default backend (docs/architecture.md). Semantics match the C backend exactly: Python
evaluation order, checked int64 arithmetic (LLVM overflow intrinsics), Python ``//``
and ``%`` signs (CPython's float_rem / float_floor_div), exact int/int ``/`` within
+-2**53, exact mixed int/float comparison, CPython's ``math_1`` error rules (libm is
called, so results are bit-identical to CPython's), negative-index wrapping, the
same limits, and a ``PyErr_CheckSignals`` call every 16384 loop iterations.

One function per block, C ABI:

    i32 kernel(ptr api, ptr data, i64 nq, i64 nc, ptr err, args...)

``data`` is the ``_data`` object of the circuit the block appends to, and ``nq`` and
``nc`` are its sizes; the kernel borrows its Rust circuit through the API table (the
caller keeps ``data`` alive for the call).

Arguments: int -> i64, float -> double, array -> (ptr data, i64 length). On error a
function stores ``err = [kind, a, b, site]`` and returns 1; the caller raises the
exception described by ``sites[site]``. Kind ``INTERRUPT`` means a Python signal
handler already set an exception. External addresses (Qiskit C API, signal check)
arrive through the ``api`` table at run time, so object code is cacheable. Kind
``PENDING`` means ``_fperror`` holds the exception to raise (NumPy error reporting).
"""

from __future__ import annotations

from typing import Any, NamedTuple

from llvmlite import ir as L

from . import _ir as ir
from ._analysis import MATH_1, MATH_2
from ._codegen_c import MAX_GATES, MAX_ITERATIONS
from ._fperror import ABS, NEG, UFUNC_BASE
from ._npufunc import UFUNCS

QUBIT, DUPLICATE, ZERO, OVERFLOW, ARRAY_INDEX, QISKIT, VALUE, INTERRUPT, PENDING = range(1, 10)
API_GATE, API_MEASURE, API_RESET, API_CHECK_SIGNALS, API_BORROW, API_REPORT = range(6)
API_REPORT_INT = 6
API_UFUNCS = 7  # then (loop, data) per _npufunc.UFUNCS entry, filled when first needed
API_SLOTS = API_UFUNCS + 2 * len(UFUNCS)

I1, I32, I64, F64, PTR = (
    L.IntType(1),
    L.IntType(32),
    L.IntType(64),
    L.DoubleType(),
    L.PointerType(),
)
INT64_MIN = -(2**63)
TWO63 = 9223372036854775808.0
_ICMP = {"lt": "<", "le": "<=", "gt": ">", "ge": ">=", "eq": "==", "ne": "!="}
# Mixed int/float comparison through a three-way result c in {-1, 0, 1, 2=NaN}.
_CMP3 = {
    "lt": [-1],
    "le": [-1, 0],
    "gt": [1],
    "ge": [1, 0],
    "eq": [0],
    "ne": [-1, 1, 2],
}
GATE_FN = L.FunctionType(I32, [PTR, I32, PTR, PTR])  # QkGate (uint8_t) widened, zero upper bits
MEASURE_FN = L.FunctionType(I32, [PTR, I32, I32])
RESET_FN = L.FunctionType(I32, [PTR, I32])
CHECK_SIGNALS_FN = L.FunctionType(I32, [])
BORROW_FN = L.FunctionType(PTR, [PTR])  # QkCircuit *(PyObject *)
# _fperror.report_from_callback(op, a, b, line) -> 0, or 1 with a pending exception
REPORT_FN = L.FunctionType(I32, [I32, F64, F64, I32])
REPORT_INT_FN = L.FunctionType(I32, [I32, I64, I64, I32])  # report_int_from_callback
# a NumPy inner loop: void (char **args, npy_intp *dims, npy_intp *steps, void *data)
LOOP_FN = L.FunctionType(L.VoidType(), [PTR, PTR, PTR, PTR])
NP_OPS = {"add": 0, "sub": 1, "mul": 2, "truediv": 3, "floordiv": 4, "mod": 5, "pow": 6}
DBL_MIN = 2.2250738585072014e-308
# libm functions called by generated code. They are declared under private names
# (LIBM_PREFIX + name) that the loader maps to libm's own addresses: LLVM does not
# recognise those names, so it can never rewrite or constant-fold the calls (for
# example pow(x, 2.0) -> x*x, which can differ from libm in the last bit). This is
# the IR counterpart of the C backend's -fno-builtin.
LIBM_2 = ("pow", *MATH_2)  # two arguments (MATH_2 includes fmod)
LIBM = (*MATH_1, *LIBM_2)
LIBM_PREFIX = "fc_libm_"


class Site(NamedTuple):
    kind: int
    line: int
    message: str  # may contain {a} and {b}


def c64(v: int) -> Any:
    return L.Constant(I64, v)


def f64(v: float) -> Any:
    return L.Constant(F64, v)


class LLVMGenerator:
    def __init__(self, fn: ir.Function, gate_enum: dict[str, int]) -> None:
        self.fn = fn
        self.gate_enum = gate_enum
        self.module = L.Module(name=fn.qualname)
        self.sites: list[Site] = []
        self.floor = self._intrinsic("llvm.floor", 1)
        self.ceil = self._intrinsic("llvm.ceil", 1)
        self.trunc = self._intrinsic("llvm.trunc", 1)
        self.fabs = self._intrinsic("llvm.fabs", 1)
        self.copysign = self._intrinsic("llvm.copysign", 2)
        self.libm = {
            name: L.Function(
                self.module,
                L.FunctionType(F64, [F64] * (2 if name in LIBM_2 else 1)),
                name=LIBM_PREFIX + name,
            )
            for name in LIBM
        }
        self._cmp_ll_d: Any = None
        # Float arithmetic uses constrained intrinsics, which the optimizer leaves as
        # written. Plain fadd/fmul could be rewritten ((-a) + b -> b - a, x * -1.0 ->
        # -x), which changes the sign of a NaN result; Python runs each operation
        # on its own, so the hardware decides it.
        md = L.MetaDataType()
        self.constrained = {
            op: L.Function(
                self.module,
                L.FunctionType(F64, [F64, F64, md, md]),
                name=f"llvm.experimental.constrained.{op}.f64",
            )
            for op in ("fadd", "fsub", "fmul", "fdiv")
        }
        self.fp_args = [
            L.MetaDataString(self.module, "round.tonearest"),
            L.MetaDataString(self.module, "fpexcept.strict"),
        ]

    def neg(self, x: Any) -> Any:
        """Python's float negation: a sign flip, fenced so code generation cannot
        merge it into a neighbouring operation (fadd (fneg a), b -> fsub b, a)."""
        fence = self.module.declare_intrinsic(
            "llvm.arithmetic.fence", [F64], L.FunctionType(F64, [F64])
        )
        return self.b.call(fence, [self.b.fneg(self.b.call(fence, [x]))])

    def _farith(self, op: str, x: Any, y: Any) -> Any:
        return self.b.call(self.constrained[op], [x, y, *self.fp_args])

    def fadd(self, x: Any, y: Any) -> Any:
        return self._farith("fadd", x, y)

    def fsub(self, x: Any, y: Any) -> Any:
        return self._farith("fsub", x, y)

    def fmul(self, x: Any, y: Any) -> Any:
        return self._farith("fmul", x, y)

    def fdiv(self, x: Any, y: Any) -> Any:
        return self._farith("fdiv", x, y)

    def libcall(self, name: str, args: list[Any]) -> Any:
        """Call libm exactly as CPython does (see LIBM_PREFIX)."""
        return self.b.call(self.libm[name], args)

    def _intrinsic(self, name: str, arity: int) -> Any:
        # llvmlite guesses a one-argument signature for some intrinsics; spell it out.
        return self.module.declare_intrinsic(name, [F64], L.FunctionType(F64, [F64] * arity))

    # -- function scaffolding -------------------------------------------------
    def begin(self, name: str, leading: list[Any]) -> list[Any]:
        argtypes = list(leading)
        for p in self.fn.params:
            if p.type in ir.ARRAYS:  # data, then the size of each dimension
                argtypes += [PTR] + [I64] * ir.ARRAYS[p.type][1]
            else:
                argtypes.append(I64 if p.type in ir.INTS else F64)
        self.func = L.Function(self.module, L.FunctionType(I32, argtypes), name=name)
        # Allocas live in their own entry block: llvmlite builders track their
        # position by index, so two builders must never insert into the same block.
        entry = self.func.append_basic_block("allocas")
        self.body = self.func.append_basic_block("body")
        self.alloca_builder = L.IRBuilder(entry)
        self.b = L.IRBuilder(self.body)
        args = list(self.func.args)
        lead, rest = args[: len(leading)], args[len(leading) :]
        self.arrays: dict[str, Any] = {}
        # Scalar parameters and locals live in stack slots (promoted to registers by
        # mem2reg); Python allows assigning to parameters.
        self.slots: dict[tuple[str, str], Any] = {}
        for p in self.fn.params:
            if p.type in ir.ARRAYS:
                self.arrays[p.name] = tuple(rest.pop(0) for _ in range(1 + ir.ARRAYS[p.type][1]))
            else:
                slot = self.alloca(I64 if p.type in ir.INTS else F64, name=p.name)
                self.b.store(rest.pop(0), slot)
                self.slots[("arg", p.name)] = slot
        self.loop_slots: dict[str, list[Any]] = {}
        return lead

    def finish(self) -> None:
        self.alloca_builder.branch(self.body)

    def alloca(self, ty: Any, name: str = "", count: int | None = None) -> Any:
        size = None if count is None else c64(count)
        return self.alloca_builder.alloca(ty, size=size, name=name)

    def block(self, name: str = "") -> Any:
        return self.func.append_basic_block(name)

    def fail_if(
        self, cond: Any, kind: int, line: int, message: str, a: Any = None, b: Any = None
    ) -> None:
        self.sites.append(Site(kind, line, message))
        site = len(self.sites) - 1
        fail, ok = self.block(f"fail{site}"), self.block(f"ok{site}")
        self.b.cbranch(cond, fail, ok)
        self.b.position_at_end(fail)
        for i, v in enumerate(
            (c64(kind), a if a is not None else c64(0), b if b is not None else c64(0), c64(site))
        ):
            self.b.store(v, self.b.gep(self.err, [c64(i)], source_etype=I64))
        self.b.ret(L.Constant(I32, 1))
        self.b.position_at_end(ok)

    def api_call(self, index: int, fnty: Any, args: list[Any]) -> Any:
        addr = self.b.load(self.b.gep(self.api, [c64(index)], source_etype=I64), typ=I64)
        return self.b.call(self.b.inttoptr(addr, fnty.as_pointer()), args)

    # -- expressions ---------------------------------------------------------
    def var(self, v: ir.Var) -> Any:
        if v.kind == "loop":
            return self.b.load(self.loop_slots[v.name][-1], typ=I64)
        if v.type in ir.ARRAYS:
            return self.arrays[v.name]
        return self.b.load(self.slots[(v.kind, v.name)], typ=I64 if v.type in ir.INTS else F64)

    def expr(self, e: ir.Expr) -> Any:
        b = self.b
        if isinstance(e, ir.Const):
            return c64(int(e.value)) if e.type == "int" else f64(float(e.value))
        if isinstance(e, ir.Var):
            return self.var(e)
        if isinstance(e, ir.ToFloat):
            return b.sitofp(self.expr(e.operand), F64)
        if isinstance(e, ir.Len):
            return self.var(e.array)[1 + e.axis]
        if isinstance(e, ir.Unary):
            v = self.expr(e.operand)
            if e.op == "not":
                return b.not_(v)
            if e.type in ir.FLOATS:
                return self.neg(v)
            if e.type == "int64":  # wraps; NumPy reports the overflow
                self.np_int_report(b.icmp_signed("==", v, c64(INT64_MIN)), NEG, v, c64(0), e.line)
                return b.sub(c64(0), v)
            self.overflow_if(b.icmp_signed("==", v, c64(INT64_MIN)), e.line)
            return b.sub(c64(0), v)
        if isinstance(e, ir.Binary):
            return self.binary(e)
        if isinstance(e, ir.Index):
            return self.index(e)
        if isinstance(e, ir.Compare):
            return self.compare(e)
        if isinstance(e, ir.Truth):
            v = self.expr(e.operand)
            if e.operand.type in ir.INTS:
                return b.icmp_signed("!=", v, c64(0))
            return b.fcmp_unordered("!=", v, f64(0.0))  # NaN is truthy
        if isinstance(e, ir.BoolOp):
            return self.boolop(e)
        if isinstance(e, ir.IfExp):
            return self.ifexp(e)
        if isinstance(e, ir.Call):
            return self.call(e)
        if isinstance(e, ir.UFunc):
            return self.ufunc(e)
        raise NotImplementedError(f"LLVM backend: {type(e).__name__}")  # pragma: no cover

    def overflow_if(self, cond: Any, line: int) -> None:
        self.fail_if(cond, OVERFLOW, line, "int64 arithmetic overflow")

    def checked(self, op: str, x: Any, y: Any, line: int) -> Any:
        res = getattr(self.b, f"s{op}_with_overflow")(x, y)
        self.overflow_if(self.b.extract_value(res, 1), line)
        return self.b.extract_value(res, 0)

    def tag(self, e: ir.Expr) -> Any:
        """i1, true if ``e`` is an np.float64 (computed from slots, no side effects)."""
        if e.type != "anyfloat":
            return L.Constant(I1, int(e.type == "float64"))
        if isinstance(e, ir.Var):
            return self.b.load(self.tag_slots[e.name], typ=I1)
        if isinstance(e, ir.Binary):
            return self.b.or_(self.tag(e.left), self.tag(e.right))
        if isinstance(e, ir.Unary):
            return self.tag(e.operand)
        if isinstance(e, ir.Call):  # abs
            return self.tag(e.args[0])
        raise NotImplementedError(f"LLVM backend: anyfloat {type(e).__name__}")  # pragma: no cover

    def np_int_report(self, cond: Any, code: int, x: Any, y: Any, line: int) -> None:
        """If ``cond``, let NumPy report np.int64 operation ``code``."""
        b = self.b
        report, after = self.block(), self.block()
        b.cbranch(cond, report, after)
        b.position_at_end(report)
        args = [L.Constant(I32, code), x, y, L.Constant(I32, line)]
        rc = self.api_call(API_REPORT_INT, REPORT_INT_FN, args)
        self.fail_if(b.icmp_signed("!=", rc, L.Constant(I32, 0)), PENDING, line, "")
        b.branch(after)
        b.position_at_end(after)

    def np_int_binary(self, e: ir.Binary, x: Any, y: Any) -> Any:
        """np.int64 arithmetic (as np_int_binary in the C backend): wraps on overflow,
        // and % by zero give 0; NumPy reports overflow and division by zero."""
        b = self.b
        code = NP_OPS[e.op]
        if e.op in ("add", "sub", "mul"):
            res = getattr(b, f"s{e.op}_with_overflow")(x, y)
            self.np_int_report(b.extract_value(res, 1), code, x, y, e.line)
            return b.extract_value(res, 0)
        if e.op == "pow":
            exponent = int(e.right.value) if isinstance(e.right, ir.Const) else 0
            result: Any = c64(1)
            base = x
            while exponent > 0:  # wrapping square-and-multiply
                if exponent & 1:
                    result = b.mul(result, base)
                exponent >>= 1
                if exponent > 0:
                    base = b.mul(base, base)
            return result
        zero = b.icmp_signed("==", y, c64(0))
        minus_one = b.icmp_signed("==", y, c64(-1))
        if e.op == "floordiv":
            overflow = b.and_(b.icmp_signed("==", x, c64(INT64_MIN)), minus_one)
            special = b.or_(zero, overflow)
            safe_y = b.select(special, c64(1), y)
            q, r = b.sdiv(x, safe_y), b.srem(x, safe_y)
            adjust = b.and_(
                b.icmp_signed("!=", r, c64(0)), b.icmp_signed("<", b.xor(r, safe_y), c64(0))
            )
            q = b.select(adjust, b.sub(q, c64(1)), q)
            value = b.select(zero, c64(0), b.select(overflow, c64(INT64_MIN), q))
            self.np_int_report(special, code, x, y, e.line)
            return value
        # mod: by zero gives 0; y == -1 gives 0 (srem INT64_MIN, -1 is undefined)
        safe_y = b.select(b.or_(zero, minus_one), c64(1), y)
        r = b.srem(x, safe_y)
        adjust = b.and_(
            b.icmp_signed("!=", r, c64(0)), b.icmp_signed("<", b.xor(r, safe_y), c64(0))
        )
        r = b.select(adjust, b.add(r, safe_y), r)
        value = b.select(b.or_(zero, minus_one), c64(0), r)
        self.np_int_report(zero, code, x, y, e.line)
        return value

    def binary(self, e: ir.Binary) -> Any:
        x, y = self.expr(e.left), self.expr(e.right)
        if e.type == "int64":
            return self.np_int_binary(e, x, y)
        if e.type == "float64":
            return self.np_binary(e, x, y)
        if e.type == "anyfloat":  # NumPy's rules if either operand holds an np.float64
            slot = self.alloca(F64)
            numpy, python, end = self.block(), self.block(), self.block()
            self.b.cbranch(self.tag(e), numpy, python)
            for blk, gen in ((numpy, self.np_binary), (python, self.py_binary)):
                self.b.position_at_end(blk)
                self.b.store(gen(e, x, y), slot)
                self.b.branch(end)
            self.b.position_at_end(end)
            return self.b.load(slot, typ=F64)
        return self.py_binary(e, x, y)

    def np_binary(self, e: ir.Binary, x: Any, y: Any) -> Any:
        """np.float64 arithmetic: never raises; unusual results go to NumPy to report
        (as fc_np_unusual in the C backend)."""
        b = self.b
        finite, tiny, nonzero = self.finite, self.tiny, self.nonzero
        if e.op == "pow":
            y = b.sitofp(y, F64)
            r = self.libcall("pow", [x, y])
            zero_counts = True
        elif e.op in ("floordiv", "mod"):
            # numpy's npy_floor_divide / npy_remainder: like CPython except b == 0.
            divmod_ = self.float_divmod(x, y, e.op)
            by_zero = self.fdiv(x, y) if e.op == "floordiv" else self.libcall("fmod", [x, y])
            r = b.select(b.fcmp_ordered("==", y, f64(0.0)), by_zero, divmod_)
            q = self.fdiv(x, y)  # NumPy computes this quotient too
            unusual = b.or_(
                b.not_(finite(r)),
                b.or_(b.not_(finite(q)), b.and_(tiny(q), nonzero(x))),
            )
        else:
            op = {"add": self.fadd, "sub": self.fsub, "mul": self.fmul, "truediv": self.fdiv}[e.op]
            r = op(x, y)
            zero_counts = e.op in ("mul", "truediv")
        if e.op not in ("floordiv", "mod"):
            unusual = self.np_unusual(r, x, y, zero_counts)
        self.np_report(unusual, NP_OPS[e.op], x, y, e.line)
        return r

    def finite(self, v: Any) -> Any:
        return self.b.fcmp_ordered("<", self.b.call(self.fabs, [v]), f64(float("inf")))

    def tiny(self, v: Any) -> Any:
        return self.b.fcmp_ordered("<", self.b.call(self.fabs, [v]), f64(DBL_MIN))

    def nonzero(self, v: Any) -> Any:
        return self.b.fcmp_unordered("!=", v, f64(0.0))

    def np_unusual(self, r: Any, x: Any, y: Any, zero_counts: bool) -> Any:
        """As fc_np_unusual: r is infinite, NaN or tiny (or, with zero_counts, an
        exact zero from nonzero operands)."""
        b = self.b
        small = b.and_(self.tiny(r), self.nonzero(r))
        if zero_counts:
            underflow = b.and_(b.not_(self.nonzero(r)), b.and_(self.nonzero(x), self.nonzero(y)))
            small = b.or_(small, b.and_(self.tiny(r), underflow))
        return b.or_(b.not_(self.finite(r)), small)

    def np_report(self, unusual: Any, code: int, x: Any, y: Any, line: int) -> None:
        """If ``unusual``, let NumPy report what operation ``code`` raised."""
        b = self.b
        report, after = self.block(), self.block()
        b.cbranch(unusual, report, after)
        b.position_at_end(report)
        args = [L.Constant(I32, code), x, y, L.Constant(I32, line)]
        rc = self.api_call(API_REPORT, REPORT_FN, args)
        self.fail_if(b.icmp_signed("!=", rc, L.Constant(I32, 0)), PENDING, line, "")
        b.branch(after)
        b.position_at_end(after)

    def ufunc(self, e: ir.UFunc) -> Any:
        """A NumPy math function through its own float64 loop (as fc_ufunc1/2)."""
        b = self.b
        args = [self.expr(a) for a in e.args]  # Python evaluates every argument first
        n = len(args)
        for k, v in enumerate(args):
            b.store(v, b.gep(self.uvals, [c64(k)], source_etype=F64))
        for k in range(n + 1):
            b.store(
                b.gep(self.uvals, [c64(k)], source_etype=F64),
                b.gep(self.uptrs, [c64(k)], source_etype=PTR),
            )
            b.store(c64(0), b.gep(self.usteps, [c64(k)], source_etype=I64))  # as for 0-d
        b.store(c64(1), self.udims)
        slot = API_UFUNCS + 2 * e.index
        fn = b.load(b.gep(self.api, [c64(slot)], source_etype=I64), typ=I64)
        data = b.load(b.gep(self.api, [c64(slot + 1)], source_etype=I64), typ=I64)
        b.call(
            b.inttoptr(fn, LOOP_FN.as_pointer()),
            [self.uptrs, self.udims, self.usteps, b.inttoptr(data, PTR)],
        )
        r = b.load(b.gep(self.uvals, [c64(n)], source_etype=F64), typ=F64)
        x, y = args[0], args[1] if n == 2 else f64(0.0)
        nan_arg = b.or_(b.fcmp_unordered("!=", x, x), b.fcmp_unordered("!=", y, y))
        unusual = b.or_(nan_arg, self.np_unusual(r, x, y if n == 2 else f64(1.0), True))
        self.np_report(unusual, UFUNC_BASE + e.index, x, y, e.line)
        return r

    def py_binary(self, e: ir.Binary, x: Any, y: Any) -> Any:
        """Python int and float arithmetic, raising where Python raises."""
        b = self.b
        if e.op == "pow":
            exponent = int(e.right.value) if isinstance(e.right, ir.Const) else -1
            if not 0 <= exponent <= 1024:  # guaranteed by the analyzer
                raise NotImplementedError("'**' needs a literal exponent in 0..1024")
            if e.type == "int":
                return self.int_pow(x, exponent, e.line)
            t = self.libcall("pow", [x, f64(float(exponent))])
            is_inf = b.fcmp_ordered("==", b.call(self.fabs, [t]), f64(float("inf")))
            finite = b.fcmp_ordered("<", b.call(self.fabs, [x]), f64(float("inf")))
            self.fail_if(b.and_(is_inf, finite), OVERFLOW, e.line, "float power overflow")
            return t
        if e.type == "int":
            if e.op in ("add", "sub", "mul"):
                return self.checked(e.op, x, y, e.line)
            self.fail_if(
                b.icmp_signed("==", y, c64(0)), ZERO, e.line, "integer division or modulo by zero"
            )
            if e.op == "floordiv":
                both = b.and_(
                    b.icmp_signed("==", x, c64(INT64_MIN)), b.icmp_signed("==", y, c64(-1))
                )
                self.overflow_if(both, e.line)
                q, r = b.sdiv(x, y), b.srem(x, y)
                adjust = b.and_(
                    b.icmp_signed("!=", r, c64(0)), b.icmp_signed("<", b.xor(r, y), c64(0))
                )
                return b.select(adjust, b.sub(q, c64(1)), q)
            # mod: y == -1 gives 0 (srem INT64_MIN, -1 is undefined in LLVM)
            safe_y = b.select(b.icmp_signed("==", y, c64(-1)), c64(1), y)
            r = b.srem(x, safe_y)
            adjust = b.and_(
                b.icmp_signed("!=", r, c64(0)), b.icmp_signed("<", b.xor(r, safe_y), c64(0))
            )
            return b.select(adjust, b.add(r, safe_y), r)
        if e.op in ("add", "sub", "mul"):
            return {"add": self.fadd, "sub": self.fsub, "mul": self.fmul}[e.op](x, y)
        if e.op == "truediv" and e.left.type == "int":
            self.fail_if(b.icmp_signed("==", y, c64(0)), ZERO, e.line, "division by zero")
            limit = 2**53
            outside = b.or_(
                b.or_(b.icmp_signed("<", x, c64(-limit)), b.icmp_signed(">", x, c64(limit))),
                b.or_(b.icmp_signed("<", y, c64(-limit)), b.icmp_signed(">", y, c64(limit))),
            )
            self.fail_if(
                outside,
                OVERFLOW,
                e.line,
                "int operands of '/' must lie within +-2**53 for exact division",
            )
            return self.fdiv(b.sitofp(x, F64), b.sitofp(y, F64))
        message = {
            "truediv": "division by zero",
            "floordiv": "float floor division by zero",
            "mod": "float modulo by zero",
        }[e.op]
        self.fail_if(b.fcmp_ordered("==", y, f64(0.0)), ZERO, e.line, message)
        if e.op == "truediv":
            return self.fdiv(x, y)
        return self.float_divmod(x, y, e.op)

    def int_pow(self, base: Any, exponent: int, line: int) -> Any:
        """Square-and-multiply unrolled over the literal exponent (as fc_ipow)."""
        result: Any = c64(1)
        e = exponent
        while e > 0:
            if e & 1:
                result = self.checked("mul", result, base, line)
            e >>= 1
            if e > 0:
                base = self.checked("mul", base, base, line)
        return result

    def float_divmod(self, vx: Any, wx: Any, op: str) -> Any:
        """CPython's float_rem / float_floor_div (Objects/floatobject.c), branch-free."""
        b = self.b
        mod = self.libcall("fmod", [vx, wx])
        nonzero = b.fcmp_unordered("!=", mod, f64(0.0))
        signs_differ = b.xor(b.fcmp_ordered("<", wx, f64(0.0)), b.fcmp_ordered("<", mod, f64(0.0)))
        adjust = b.and_(nonzero, signs_differ)
        if op == "mod":
            zero_mod = b.call(self.copysign, [f64(0.0), wx])
            return b.select(nonzero, b.select(adjust, self.fadd(mod, wx), mod), zero_mod)
        div = self.fdiv(self.fsub(vx, mod), wx)
        div = b.select(adjust, self.fsub(div, f64(1.0)), div)
        floordiv = b.call(self.floor, [div])
        floordiv = b.select(
            b.fcmp_ordered(">", self.fsub(div, floordiv), f64(0.5)),
            self.fadd(floordiv, f64(1.0)),
            floordiv,
        )
        div_nonzero = b.fcmp_unordered("!=", div, f64(0.0))
        return b.select(div_nonzero, floordiv, b.call(self.copysign, [f64(0.0), self.fdiv(vx, wx)]))

    def index(self, e: ir.Index) -> Any:
        b = self.b
        data, *sizes = self.var(e.array)

        def check(axis: int, orig: Any) -> Any:
            n = sizes[axis]
            k = b.select(b.icmp_signed("<", orig, c64(0)), b.add(orig, n), orig)
            bad = b.or_(b.icmp_signed("<", k, c64(0)), b.icmp_signed(">=", k, n))
            message = f"index {{a}} is out of bounds for axis {axis} with size {{b}}"
            self.fail_if(bad, ARRAY_INDEX, e.line, message, orig, n)
            return k

        if e.chained:  # a[i][j]: row i is checked before j is evaluated
            ks = [check(0, self.expr(e.indices[0])), check(1, self.expr(e.indices[1]))]
        else:
            originals = [self.expr(index) for index in e.indices]
            ks = [check(axis, orig) for axis, orig in enumerate(originals)]
        offset = ks[0] if len(ks) == 1 else b.add(b.mul(ks[0], sizes[1]), ks[1])
        ty = F64 if e.type == "float64" else I64
        return b.load(b.gep(data, [offset], source_etype=ty), typ=ty)

    def cmp_ll_d(self) -> Any:
        """Module-level helper, as fc_cmp_ll_d: exact int64 vs float64, -1/0/1, 2 for NaN."""
        if self._cmp_ll_d is not None:
            return self._cmp_ll_d
        fn = L.Function(self.module, L.FunctionType(I32, [I64, F64]), name="fc_cmp_ll_d")
        fn.linkage = "internal"
        fn.attributes.add("alwaysinline")
        a, x = fn.args
        b = L.IRBuilder(fn.append_basic_block())

        def k(v: int) -> Any:
            return L.Constant(I32, v)

        fb = b.call(self.floor, [x])
        in_range = b.and_(b.fcmp_ordered("<", x, f64(TWO63)), b.fcmp_ordered(">=", x, f64(-TWO63)))
        t = b.fptosi(b.select(in_range, fb, f64(0.0)), I64)  # clamped: never poison
        r = b.select(b.fcmp_ordered(">", x, fb), k(-1), k(0))
        r = b.select(b.icmp_signed(">", a, t), k(1), r)
        r = b.select(b.icmp_signed("<", a, t), k(-1), r)
        r = b.select(b.fcmp_ordered("<", x, f64(-TWO63)), k(1), r)
        r = b.select(b.fcmp_ordered(">=", x, f64(TWO63)), k(-1), r)
        r = b.select(b.fcmp_unordered("!=", x, x), k(2), r)
        b.ret(r)
        self._cmp_ll_d = fn
        return fn

    def fcmp(self, op: str, x: Any, y: Any) -> Any:
        if op == "ne":
            return self.b.fcmp_unordered("!=", x, y)  # NaN != anything
        return self.b.fcmp_ordered(_ICMP[op], x, y)

    def cmp(self, op: str, left: ir.Expr, x: Any, right: ir.Expr, y: Any) -> Any:
        b = self.b
        int_left, int_right = left.type in ir.INTS, right.type in ir.INTS
        if int_left and int_right:
            return b.icmp_signed(_ICMP[op], x, y)
        if not int_left and not int_right:
            return self.fcmp(op, x, y)
        # an int type vs a float type. Python compares an int exactly; NumPy (an
        # np.int64, or an np.float64 on the other side) converts to double first.
        integer, other = (left, right) if int_left else (right, left)
        if integer.type == "int" and other.type == "float":
            return self.exact_cmp(op, left, x, y)
        if int_left:
            numpy = self.fcmp(op, b.sitofp(x, F64), y)
        else:
            numpy = self.fcmp(op, x, b.sitofp(y, F64))
        if integer.type == "int64" or other.type == "float64":
            return numpy
        return b.select(self.tag(other), numpy, self.exact_cmp(op, left, x, y))

    def exact_cmp(self, op: str, left: ir.Expr, x: Any, y: Any) -> Any:
        b = self.b
        if left.type == "int":
            c = b.call(self.cmp_ll_d(), [x, y])
        else:  # float op int: negate the int-first result (NaN stays 2)
            raw = b.call(self.cmp_ll_d(), [y, x])
            c = b.select(
                b.icmp_signed("==", raw, L.Constant(I32, 2)), raw, b.sub(L.Constant(I32, 0), raw)
            )
        hits = [b.icmp_signed("==", c, L.Constant(I32, v)) for v in _CMP3[op]]
        out = hits[0]
        for h in hits[1:]:
            out = b.or_(out, h)
        return out

    def compare(self, e: ir.Compare) -> Any:
        first = e.operands[0]
        x = self.expr(first)
        if len(e.ops) == 1:
            right = e.operands[1]
            return self.cmp(e.ops[0], first, x, right, self.expr(right))
        # Chained: later operands are evaluated only while the chain holds.
        slot = self.alloca(I1)
        self.b.store(L.Constant(I1, 0), slot)
        end = self.block()
        left = first
        for i, (op, right) in enumerate(zip(e.ops, e.operands[1:], strict=True)):
            y = self.expr(right)
            r = self.cmp(op, left, x, right, y)
            self.b.store(r, slot)
            if i < len(e.ops) - 1:
                nxt = self.block()
                self.b.cbranch(r, nxt, end)
                self.b.position_at_end(nxt)
            left, x = right, y
        self.b.branch(end)
        self.b.position_at_end(end)
        return self.b.load(slot, typ=I1)

    def boolop(self, e: ir.BoolOp) -> Any:
        slot = self.alloca(I1)
        end = self.block()
        for i, value in enumerate(e.values):
            r = self.expr(value)
            self.b.store(r, slot)
            if i < len(e.values) - 1:
                nxt = self.block()
                if e.op == "and":
                    self.b.cbranch(r, nxt, end)
                else:
                    self.b.cbranch(r, end, nxt)
                self.b.position_at_end(nxt)
        self.b.branch(end)
        self.b.position_at_end(end)
        return self.b.load(slot, typ=I1)

    def ifexp(self, e: ir.IfExp) -> Any:
        ty = I64 if e.type in ir.INTS else F64
        slot = self.alloca(ty)
        then, other, end = self.block(), self.block(), self.block()
        self.b.cbranch(self.expr(e.test), then, other)
        for blk, value in ((then, e.body), (other, e.orelse)):
            self.b.position_at_end(blk)
            self.b.store(self.expr(value), slot)
            self.b.branch(end)
        self.b.position_at_end(end)
        return self.b.load(slot, typ=ty)

    def call(self, e: ir.Call) -> Any:
        b = self.b
        args = [self.expr(a) for a in e.args]  # Python evaluates every argument first
        if e.func == "abs":
            v = args[0]
            if e.type in ir.FLOATS:
                return b.call(self.fabs, [v])
            if e.type == "int64":  # wraps; NumPy reports the overflow
                self.np_int_report(b.icmp_signed("==", v, c64(INT64_MIN)), ABS, v, c64(0), e.line)
                return b.select(b.icmp_signed("<", v, c64(0)), b.sub(c64(0), v), v)
            self.overflow_if(b.icmp_signed("==", v, c64(INT64_MIN)), e.line)
            return b.select(b.icmp_signed("<", v, c64(0)), b.sub(c64(0), v), v)
        if e.func in ("min", "max"):
            # Python keeps the first item unless a later one compares strictly
            # smaller (min) or larger (max); this also fixes NaN behaviour.
            r = args[0]
            op = "<" if e.func == "min" else ">"
            for a in args[1:]:
                compare = b.icmp_signed if e.type in ir.INTS else b.fcmp_ordered
                better = compare(op, a, r)
                r = b.select(better, a, r)
            return r
        if e.func == "int" and e.args[0].type == "int64":
            return args[0]  # int(np.int64)
        if e.func in ("int", "floor", "ceil"):
            fn = {"int": self.trunc, "floor": self.floor, "ceil": self.ceil}[e.func]
            return self.to_int(b.call(fn, [args[0]]), e.line)
        # CPython's math_1 / math_2 error rules.
        r = self.libcall(e.func, args)
        inf = f64(float("inf"))
        any_nan = b.fcmp_unordered("!=", args[0], args[0])
        for a in args[1:]:
            any_nan = b.or_(any_nan, b.fcmp_unordered("!=", a, a))
        r_nan = b.fcmp_unordered("!=", r, r)
        self.fail_if(b.and_(r_nan, b.not_(any_nan)), VALUE, e.line, "math domain error")
        r_inf = b.fcmp_ordered("==", b.call(self.fabs, [r]), inf)
        x_finite = b.fcmp_ordered("<", b.call(self.fabs, [args[0]]), inf)
        if len(args) == 2:
            y_finite = b.fcmp_ordered("<", b.call(self.fabs, [args[1]]), inf)
            both = b.and_(r_inf, b.and_(x_finite, y_finite))
            self.fail_if(both, OVERFLOW, e.line, "math range error")
        elif MATH_1[e.func]:
            self.fail_if(b.and_(r_inf, x_finite), OVERFLOW, e.line, "math range error")
        else:
            self.fail_if(b.and_(r_inf, x_finite), VALUE, e.line, "math domain error")
        return r

    def to_int(self, x: Any, line: int) -> Any:
        """float (already trunc/floor/ceil'd) -> int64 with Python's error types (fc_to_ll)."""
        b = self.b
        self.fail_if(
            b.fcmp_unordered("!=", x, x), VALUE, line, "cannot convert float NaN to integer"
        )
        is_inf = b.fcmp_ordered("==", b.call(self.fabs, [x]), f64(float("inf")))
        self.fail_if(is_inf, OVERFLOW, line, "cannot convert float infinity to integer")
        outside = b.or_(b.fcmp_ordered("<", x, f64(-TWO63)), b.fcmp_ordered(">=", x, f64(TWO63)))
        self.fail_if(outside, OVERFLOW, line, "integer result does not fit in int64")
        return b.fptosi(x, I64)

    # -- statements ----------------------------------------------------------
    def stmts(self, body: tuple[ir.Stmt, ...]) -> None:
        for s in body:
            self.stmt(s)

    def stmt(self, s: ir.Stmt) -> None:
        if isinstance(s, ir.For):
            self.for_loop(s)
        elif isinstance(s, ir.If):
            then, other, end = self.block(), self.block(), self.block()
            self.b.cbranch(self.expr(s.test), then, other)
            for blk, body in ((then, s.body), (other, s.orelse)):
                self.b.position_at_end(blk)
                self.stmts(body)
                self.b.branch(end)
            self.b.position_at_end(end)
        elif isinstance(s, ir.Assign):
            value = self.expr(s.value)
            if s.target.type == "anyfloat":
                tag = self.tag(s.value)  # read before either store
                self.b.store(tag, self.tag_slots[s.target.name])
            self.b.store(value, self.slots[(s.target.kind, s.target.name)])
        elif isinstance(s, ir.Gate):
            self.gate(s)
        elif isinstance(s, (ir.Measure, ir.Reset)):
            self.measure_or_reset(s)
        else:  # pragma: no cover
            raise NotImplementedError(f"LLVM backend: {type(s).__name__}")

    def for_loop(self, s: ir.For) -> None:
        b = self.b
        # Python evaluates all range() arguments before validating the step.
        start, stop, step = self.expr(s.start), self.expr(s.stop), self.expr(s.step)
        self.fail_if(
            self.b.icmp_signed("==", step, c64(0)), VALUE, s.line, "range() arg 3 must not be zero"
        )
        slot = self.alloca(I64, name=s.var)
        self.b.store(start, slot)
        cond, body, exit_ = self.block(), self.block(), self.block()
        self.b.branch(cond)
        b = self.b
        b.position_at_end(cond)
        i = b.load(slot, typ=I64)
        go = b.select(
            b.icmp_signed(">", step, c64(0)),
            b.icmp_signed("<", i, stop),
            b.icmp_signed(">", i, stop),
        )
        b.cbranch(go, body, exit_)
        b.position_at_end(body)
        count = b.add(b.load(self.iterations, typ=I64), c64(1))
        b.store(count, self.iterations)
        self.fail_if(
            b.icmp_signed(">", count, c64(MAX_ITERATIONS)),
            VALUE,
            s.line,
            "loop iteration limit exceeded",
        )
        check, after = self.block(), self.block()
        b.cbranch(b.icmp_signed("==", b.and_(count, c64(16383)), c64(0)), check, after)
        b.position_at_end(check)
        rc = self.api_call(API_CHECK_SIGNALS, CHECK_SIGNALS_FN, [])
        self.fail_if(b.icmp_signed("<", rc, L.Constant(I32, 0)), INTERRUPT, s.line, "interrupted")
        b.branch(after)
        b.position_at_end(after)
        self.loop_slots.setdefault(s.var, []).append(slot)
        self.stmts(s.body)
        self.loop_slots[s.var].pop()
        res = b.sadd_with_overflow(b.load(slot, typ=I64), step)
        cont = self.block()
        b.cbranch(b.extract_value(res, 1), exit_, cont)  # the next value lies beyond any int64 stop
        b.position_at_end(cont)
        b.store(b.extract_value(res, 0), slot)
        b.branch(cond)
        b.position_at_end(exit_)

    def bit_index(self, v: Any, size: Any, line: int) -> Any:
        b = self.b
        k = b.select(b.icmp_signed("<", v, c64(0)), b.add(v, size), v)
        bad = b.or_(b.icmp_signed("<", k, c64(0)), b.icmp_signed(">=", k, size))
        self.fail_if(bad, QUBIT, line, "Index {a} out of range for size {b}", v, size)
        return k

    def count_gate(self, line: int) -> None:
        count = self.b.add(self.b.load(self.gates, typ=I64), c64(1))
        self.b.store(count, self.gates)
        self.fail_if(
            self.b.icmp_signed(">", count, c64(MAX_GATES)), VALUE, line, "gate limit exceeded"
        )

    def check_rc(self, rc: Any, line: int, what: str) -> None:
        self.fail_if(
            self.b.icmp_unsigned("!=", rc, L.Constant(I32, 0)),
            QISKIT,
            line,
            f"Qiskit rejected {what} (exit code {{a}})",
            self.b.zext(rc, I64),
        )

    def gate(self, s: ir.Gate) -> None:
        b = self.b
        # Evaluate every argument first, in order, as Python does before Qiskit
        # validates the qubits.
        params = [self.expr(e) for e in s.params]
        qubits = [self.expr(e) for e in s.qubits]
        qubits = [self.bit_index(q, self.nq, s.line) for q in qubits]
        for j, qj in enumerate(qubits):
            for qk in qubits[:j]:
                self.fail_if(
                    b.icmp_signed("==", qj, qk), DUPLICATE, s.line, "duplicate qubit arguments"
                )
        self.count_gate(s.line)
        for j, q in enumerate(qubits):
            b.store(b.trunc(q, I32), b.gep(self.qs, [c64(j)], source_etype=I32))
        for j, p in enumerate(params):
            b.store(p, b.gep(self.ps, [c64(j)], source_etype=F64))
        ps = self.ps if params else L.Constant(PTR, None)
        enum = L.Constant(I32, self.gate_enum[s.enum])
        rc = self.api_call(API_GATE, GATE_FN, [self.circuit, enum, self.qs, ps])
        self.check_rc(rc, s.line, f"gate {s.name}")

    def measure_or_reset(self, s: ir.Measure | ir.Reset) -> None:
        q = self.expr(s.qubit)
        c = self.expr(s.clbit) if isinstance(s, ir.Measure) else None
        q = self.bit_index(q, self.nq, s.line)
        if c is not None:
            c = self.bit_index(c, self.nc, s.line)
        self.count_gate(s.line)
        if c is not None:
            rc = self.api_call(
                API_MEASURE, MEASURE_FN, [self.circuit, self.b.trunc(q, I32), self.b.trunc(c, I32)]
            )
        else:
            rc = self.api_call(API_RESET, RESET_FN, [self.circuit, self.b.trunc(q, I32)])
        self.check_rc(rc, s.line, "measure" if c is not None else "reset")

    # -- module --------------------------------------------------------------
    def generate(self) -> tuple[str, list[Site]]:
        fn = self.fn
        self.api, data, self.nq, self.nc, self.err = self.begin("kernel", [PTR, PTR, I64, I64, PTR])
        self.circuit = self.api_call(API_BORROW, BORROW_FN, [data])
        # NULL means Qiskit set a Python exception; the caller re-raises it.
        null = self.b.icmp_unsigned("==", self.b.ptrtoint(self.circuit, I64), c64(0))
        self.fail_if(null, INTERRUPT, fn.line, "circuit borrow failed")
        self.tag_slots: dict[str, Any] = {}  # anyfloat locals: true while holding an np.float64
        for name, t in fn.locals.items():
            slot = self.alloca(I64 if t in ir.INTS else F64, name=name)
            self.b.store(c64(0) if t in ir.INTS else f64(0.0), slot)
            self.slots[("local", name)] = slot
            if t == "anyfloat":
                self.tag_slots[name] = self.alloca(I1, name=name + ".np")
                self.b.store(L.Constant(I1, 0), self.tag_slots[name])
        self.iterations, self.gates = self.alloca(I64), self.alloca(I64)
        self.b.store(c64(0), self.iterations)
        self.b.store(c64(0), self.gates)
        # Scratch buffers live in this stack frame for the whole call.
        self.qs = self.alloca(I32, name="qs", count=4)
        self.ps = self.alloca(F64, name="ps", count=4)
        self.uvals = self.alloca(F64, name="uvals", count=3)  # NumPy loop calls
        self.uptrs = self.alloca(PTR, name="uptrs", count=3)
        self.usteps = self.alloca(I64, name="usteps", count=3)
        self.udims = self.alloca(I64, name="udims")
        self.stmts(fn.body)
        self.b.ret(L.Constant(I32, 0))
        self.finish()
        header = f"; Generated by fastcircuit from {fn.qualname} ({fn.filename}, line {fn.line}).\n"
        return header + str(self.module), self.sites


def generate(fn: ir.Function, gate_enum: dict[str, int]) -> tuple[str, list[Site]]:
    """Returns (LLVM IR text, error sites)."""
    return LLVMGenerator(fn, gate_enum).generate()
