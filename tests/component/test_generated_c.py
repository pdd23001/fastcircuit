"""Generated C compiles cleanly with strict warnings (-Werror).

Suppressions are documented: Qiskit headers are included with -isystem, and the
single use of Qiskit's prototype-less ``qk_api_version`` macro is wrapped in a
diagnostic pragma inside the generated code itself.
"""

from __future__ import annotations

import subprocess

import numpy as np
import pytest

from fastcircuit import _block, _build, _native

pytestmark = pytest.mark.needs_c_backend

STRICT = [
    "-Wall",
    "-Wextra",
    "-Wpedantic",
    "-Wshadow",
    "-Wstrict-prototypes",
    "-Wmissing-prototypes",
    "-Wconversion",
    "-Wno-sign-conversion",
    "-Werror",
]


def compiled_sources() -> dict[str, str]:
    from support import examples, programs

    funcs = [examples.layered, examples.array_angles, examples.ghz]
    funcs += [fn for fn, _ in programs.calls()]
    # Generated from each block's analysis; nothing is compiled or run.
    return {f.__name__: _native.prepare(f.program, "c").c_source for f in funcs}


def strict_compile(tmp_path, name: str, source: str) -> subprocess.CompletedProcess[str]:
    tool = _build.find_toolchain()
    py_inc, qk_inc = _build.include_dirs()
    c_file = tmp_path / f"{name}.c"
    c_file.write_text(source)
    cmd = [
        *tool.argv,
        *_build.BASE_CFLAGS,
        "-c",
        *STRICT,
        "-I",
        py_inc,
        "-isystem",
        qk_inc,
        str(c_file),
        "-o",
        str(tmp_path / "out.o"),
    ]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


@pytest.mark.parametrize("name", sorted(compiled_sources()))
def test_strict_warnings(tmp_path, name):
    proc = strict_compile(tmp_path, name, compiled_sources()[name])
    assert proc.returncode == 0, proc.stderr


def test_strict_warnings_for_a_block(tmp_path, monkeypatch):
    """The C of a block as compiled at run time, from the values it saw."""
    from support.modules import import_module

    monkeypatch.setenv("FASTCIRCUIT_BACKEND", "c")
    monkeypatch.syspath_prepend(str(tmp_path))
    module = import_module(
        tmp_path,
        """
        def build(n, angles):
            qc = QuantumCircuit(n, n)
            with fastcircuit:
                for i in range(n):
                    qc.ry(angles[i], i)
                    qc.measure(i, i)
            return qc
        """,
    )
    module.build(2, np.array([0.1, 0.2]))
    [(_, compiled)] = [
        (site, c) for site, c in _block.compiled_blocks() if site.filename == module.__file__
    ]
    assert compiled.backend == "c"
    proc = strict_compile(tmp_path, "block", compiled.c_source)
    assert proc.returncode == 0, proc.stderr
