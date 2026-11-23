"""Run compiled blocks in child processes and observe compiler invocations."""

from __future__ import annotations

import json
import os
import shlex
import stat
import subprocess
import sys
from pathlib import Path

MODULE = """\
from qiskit import QuantumCircuit
import fastcircuit


def build(n, layers, theta):
    qc = QuantumCircuit(n)
    with fastcircuit:
        for layer in range(layers):
            for i in range(n):
                qc.ry(theta * (layer + 1), i)
            for i in range(n - 1):
                qc.cz(i, i + 1)
    return qc


def reference(n, layers, theta):
    qc = QuantumCircuit(n)
    for layer in range(layers):
        for i in range(n):
            qc.ry(theta * (layer + 1), i)
        for i in range(n - 1):
            qc.cz(i, i + 1)
    return qc
"""

CHILD = """\
import json, sys
sys.path.insert(0, sys.argv[1])
import fastcircuit
from fastcircuit import _block
import {module} as m
qc = m.build(5, 3, 0.25)
[(_, compiled)] = _block.compiled_blocks()
print(json.dumps({{"source": compiled.timings["source"], "equal": qc == m.reference(5, 3, 0.25),
                  "build_seconds": compiled.timings["build_seconds"]}}))
"""


def write_module(directory: Path, name: str = "cached_mod", source: str = MODULE) -> str:
    (directory / f"{name}.py").write_text(source)
    return name


def logging_compiler(directory: Path, log: Path, real: str, delay: float = 0) -> Path:
    """A CC wrapper that records every invocation, optionally slowing compiles."""
    path = directory / "logging-cc"
    path.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> {shlex.quote(str(log))}\n'
        f'case "$*" in *" -o "*) sleep {delay};; esac\n'
        f'exec {shlex.quote(real)} "$@"\n'
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def compile_invocations(log: Path) -> list[str]:
    if not log.exists():
        return []
    return [line for line in log.read_text().splitlines() if " -o " in f" {line} "]


def child_env(cache: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("FASTCIRCUIT_")}
    # Plain output whatever the parent's settings (CI sets FORCE_COLOR, which makes
    # Python 3.13+ colour tracebacks), so tests can compare it as text.
    env.pop("FORCE_COLOR", None)
    env["PYTHON_COLORS"] = "0"
    env["FASTCIRCUIT_CACHE_DIR"] = str(cache)
    env.update(extra)
    return env


def start_child(directory: Path, module: str, env: dict[str, str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", CHILD.format(module=module), str(directory)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def run_child(directory: Path, module: str, env: dict[str, str]) -> dict:
    proc = start_child(directory, module, env)
    out, err = proc.communicate(timeout=300)
    if proc.returncode != 0:
        raise AssertionError(f"child failed ({proc.returncode}):\n{err}")
    return json.loads(out.strip().splitlines()[-1])
