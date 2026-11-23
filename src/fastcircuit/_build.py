"""C compiler discovery, invocation and extension loading.

The compiler is chosen from ``$CC`` (split like a shell word list) or the
compiler Python was built with, falling back to ``cc``. Generated code is
compiled with ``-ffp-contract=off`` so float expressions round exactly like
Python (no fused multiply-add), and Qiskit's headers are included as system
headers so their warnings do not mask warnings in generated code.
"""

from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from qiskit.capi import get_include

from .errors import CompilerNotFoundError, NativeBuildError

COMPILE_TIMEOUT_SECONDS = 300
# -ffp-contract=off: no fused multiply-add, so float expressions round like Python.
# -fno-builtin: libm calls (sin, pow, ...) are never constant-folded by the compiler,
#   so results come from the same libm that Python's math module uses.
BASE_CFLAGS = (
    "-std=c11",
    "-O2",
    "-fPIC",
    "-fvisibility=hidden",
    "-ffp-contract=off",
    "-fno-builtin",
)


@dataclass(frozen=True)
class Toolchain:
    argv: tuple[str, ...]  # compiler command, e.g. ("ccache", "gcc")
    path: str  # resolved executable of argv[0]

    def identity(self, memo_dir: Path | None) -> str:
        return compiler_identity(self, memo_dir)


def extra_cflags() -> tuple[str, ...]:
    return tuple(shlex.split(os.environ.get("FASTCIRCUIT_EXTRA_CFLAGS", "")))


def link_flags() -> tuple[str, ...]:
    platform_name: str = sys.platform  # not narrowed by type checkers
    if platform_name == "darwin":
        # Python symbols resolve against the host interpreter at load time.
        return ("-bundle", "-undefined", "dynamic_lookup")
    return ("-shared", "-lm")


def include_dirs() -> tuple[str, str]:
    return sysconfig.get_paths()["include"], get_include()


def compile_config() -> dict[str, object]:
    """Every compilation input except the compiler itself (part of the cache key)."""
    python_include, qiskit_include = include_dirs()
    return {
        "cflags": [*BASE_CFLAGS, *extra_cflags()],
        "ldflags": list(link_flags()),
        "ext_suffix": sysconfig.get_config_var("EXT_SUFFIX"),
        "qiskit_headers": _headers_digest(qiskit_include),
        "python_include": python_include,
    }


_HEADER_DIGESTS: dict[str, str] = {}


def _headers_digest(root: str) -> str:
    cached = _HEADER_DIGESTS.get(root)
    if cached is not None:
        return cached
    digest = hashlib.sha256()
    for path in sorted(Path(root).rglob("*.h")):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    _HEADER_DIGESTS[root] = result = digest.hexdigest()
    return result


def find_toolchain() -> Toolchain:
    env = os.environ.get("CC")
    if env:
        try:
            argv = tuple(shlex.split(env))
        except ValueError as exc:  # e.g. an unbalanced quote
            raise CompilerNotFoundError(
                f"cannot read CC={env!r} ({exc}). Set CC to a compiler path, optionally "
                "followed by flags, quoted as in a shell."
            ) from None
        source = "$CC"
    else:
        configured = shlex.split(sysconfig.get_config_var("CC") or "")
        argv = (configured[0],) if configured and shutil.which(configured[0]) else ("cc",)
        source = "default"
    path = shutil.which(argv[0]) if argv else None
    if not path:
        raise CompilerNotFoundError(
            f"no C compiler found (looked for {argv[0] if argv else '<empty>'!r} via {source}). "
            "The C backend compiles each 'with fastcircuit:' block on its first run and needs "
            "a C11 compiler plus Python development headers. Install one (Debian/Ubuntu: "
            "'apt install gcc python3-dev'; Fedora: 'dnf install gcc python3-devel'; macOS: "
            "'xcode-select --install') or set CC to a compiler path. Run "
            "'python -m fastcircuit doctor' to check the setup."
        )
    return Toolchain(argv, path)


def check_headers() -> None:
    python_include, _ = include_dirs()
    if not (Path(python_include) / "Python.h").exists():
        raise CompilerNotFoundError(
            f"Python development headers not found ({python_include}/Python.h). "
            "Install them (Debian/Ubuntu: 'apt install python3-dev'; Fedora: "
            "'dnf install python3-devel'), then retry."
        )


_IDENTITIES: dict[tuple[str, ...], str] = {}


def compiler_identity(tool: Toolchain, memo_dir: Path | None) -> str:
    """Hash of compiler command, executable and ``--version``; memoised on disk.

    The on-disk memo is keyed by the executable's path, size and mtime so cache
    hits in new processes do not need to start the compiler.
    """
    real = os.path.realpath(tool.path)
    try:
        st = os.stat(real)
        stamp = f"{real}|{st.st_size}|{st.st_mtime_ns}|{' '.join(tool.argv)}"
    except OSError:
        stamp = f"{real}|?|{' '.join(tool.argv)}"
    memo_key = (stamp,)
    if memo_key in _IDENTITIES:
        return _IDENTITIES[memo_key]
    memo_file = None
    if memo_dir is not None:
        memo_file = memo_dir / (hashlib.sha256(stamp.encode()).hexdigest()[:32] + ".json")
        try:
            data = json.loads(memo_file.read_text())
            if data.get("stamp") == stamp:
                _IDENTITIES[memo_key] = data["identity"]
                return str(data["identity"])
        except (OSError, ValueError, KeyError):
            pass
    try:
        proc = subprocess.run(
            [*tool.argv, "--version"], capture_output=True, text=True, timeout=60, check=False
        )
        version = (proc.stdout or proc.stderr).strip().splitlines()[0:1]
    except (OSError, subprocess.SubprocessError):
        version = []
    identity = hashlib.sha256(
        json.dumps({"argv": tool.argv, "real": real, "version": version}).encode()
    ).hexdigest()
    _IDENTITIES[memo_key] = identity
    if memo_file is not None:
        try:
            tmp = memo_file.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps({"stamp": stamp, "identity": identity, "version": version}))
            os.replace(tmp, memo_file)
        except OSError:
            pass
    return identity


def compile_extension(tool: Toolchain, c_file: Path, output: Path, log_file: Path) -> None:
    python_include, qiskit_include = include_dirs()
    cmd = [
        *tool.argv,
        *BASE_CFLAGS,
        *extra_cflags(),
        "-I",
        python_include,
        "-isystem",
        qiskit_include,
        str(c_file),
        *link_flags(),
        "-o",
        str(output),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=COMPILE_TIMEOUT_SECONDS, check=False
        )
    except FileNotFoundError as exc:
        raise CompilerNotFoundError(f"C compiler disappeared: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise NativeBuildError(
            f"C compiler timed out after {COMPILE_TIMEOUT_SECONDS}s; generated source: {c_file}"
        ) from exc
    log_file.write_text(
        "$ " + shlex.join(cmd) + "\n" + proc.stdout + proc.stderr + f"\nexit={proc.returncode}\n"
    )
    if proc.returncode != 0 or not output.exists():
        tail = (proc.stderr or proc.stdout).strip()[-4000:]
        raise NativeBuildError(
            f"native compilation failed (exit {proc.returncode}).\n{tail}\n"
            f"Generated C and the full compiler log were kept in {c_file.parent}. "
            "Errors in generated code are fastcircuit bugs; please report them with that "
            "directory. Missing headers usually mean Python development headers are not "
            "installed."
        )


def load_extension(path: Path, module: str) -> ModuleType:
    loader = importlib.machinery.ExtensionFileLoader(module, str(path))
    spec = importlib.util.spec_from_file_location(module, path, loader=loader)
    if spec is None:  # pragma: no cover - only for unsupported suffixes
        raise NativeBuildError(f"cannot load extension {path}")
    ext = importlib.util.module_from_spec(spec)
    loader.exec_module(ext)
    return ext
