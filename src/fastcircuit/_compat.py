"""Supported-environment checks, run before compiling a block."""

from __future__ import annotations

import importlib.util
import os
import re
import sys
import sysconfig

import qiskit

from .errors import IncompatibleEnvironmentError

# Qiskit's C API is experimental: function slots and names may change between
# minor releases. Keep this in sync with the dependency range in pyproject.toml
# and extend it only after the full test suite passes on the new minor version.
TESTED_QISKIT_MINOR = (2, 6)
SUPPORTED_PLATFORMS = ("linux", "darwin")
BACKENDS = ("llvm", "c")


def qiskit_minor(version: str) -> tuple[int, int] | None:
    match = re.match(r"^(\d+)\.(\d+)", version)
    return (int(match.group(1)), int(match.group(2))) if match else None


def _platform() -> str:
    return sys.platform  # a function so type checkers do not narrow per platform


def llvmlite_available() -> bool:
    return importlib.util.find_spec("llvmlite") is not None


def select_backend() -> tuple[str, str]:
    """``(backend, reason)``. ``FASTCIRCUIT_BACKEND`` wins; otherwise the in-process
    LLVM backend when llvmlite is installed, else the C compiler backend.

    pip installs llvmlite only where it publishes wheels (pyproject.toml markers):
    macOS arm64, Linux x86-64/aarch64, and Intel macOS up to Python 3.13. Elsewhere
    (Intel macOS on Python 3.14+, Linux ppc64le/s390x) the C backend is used, which
    needs a C compiler.
    """
    name = os.environ.get("FASTCIRCUIT_BACKEND", "").strip().lower()
    if name:
        if name not in BACKENDS:
            raise ValueError(
                f"FASTCIRCUIT_BACKEND must be one of {', '.join(BACKENDS)}, not {name!r}"
            )
        return name, "set by FASTCIRCUIT_BACKEND"
    if llvmlite_available():
        return "llvm", "llvmlite is installed"
    return "c", "llvmlite is not available for this platform/Python; using the C compiler"


def check_environment(backend: str = "llvm") -> None:
    if sys.platform not in SUPPORTED_PLATFORMS:
        raise IncompatibleEnvironmentError(
            f"fastcircuit supports Linux and macOS, not {sys.platform!r}"
        )
    if sysconfig.get_config_var("Py_GIL_DISABLED"):
        raise IncompatibleEnvironmentError(
            "free-threaded (no-GIL) Python builds are not supported; generated code relies "
            "on the GIL for buffer and circuit ownership"
        )
    if backend == "llvm" and not llvmlite_available():
        raise IncompatibleEnvironmentError(
            "the LLVM backend needs llvmlite, which is not installed; install it "
            "('pip install llvmlite') or set FASTCIRCUIT_BACKEND=c to use a C compiler"
        )
    version = qiskit.__version__
    if qiskit_minor(version) != TESTED_QISKIT_MINOR and not os.environ.get(
        "FASTCIRCUIT_ALLOW_UNTESTED_QISKIT"
    ):
        tested = ".".join(map(str, TESTED_QISKIT_MINOR))
        raise IncompatibleEnvironmentError(
            f"fastcircuit is tested with Qiskit {tested}.x, found {version}. Qiskit's C API "
            f"is experimental and may change between minor versions. Install "
            f"'qiskit>={tested},<{TESTED_QISKIT_MINOR[0]}.{TESTED_QISKIT_MINOR[1] + 1}', or set "
            "FASTCIRCUIT_ALLOW_UNTESTED_QISKIT=1 to try anyway."
        )
