"""Smoke test of an installed fastcircuit distribution, run from outside the repository.

    python -I ci/smoke_installed.py

``-I`` keeps the repository off ``sys.path``, so the installed package is used.
Checks: a compiled block builds the same circuit as plain Python; a second process loads
the result from the persistent cache without compiling; ``doctor`` passes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

MODULE = """\
from qiskit import QuantumCircuit
import fastcircuit


def build(n, layers, theta):
    qc = QuantumCircuit(n, n)
    with fastcircuit:
        for layer in range(layers):
            for i in range(n):
                qc.ry(theta * (layer + 1), i)
            for i in range(n - 1):
                qc.cx(i, i + 1)
        for i in range(n):
            qc.measure(i, i)
    return qc


def reference(n, layers, theta):
    qc = QuantumCircuit(n, n)
    for layer in range(layers):
        for i in range(n):
            qc.ry(theta * (layer + 1), i)
        for i in range(n - 1):
            qc.cx(i, i + 1)
    for i in range(n):
        qc.measure(i, i)
    return qc
"""

CHILD = """\
import json, sys
sys.path.insert(0, sys.argv[1])
import fastcircuit, smoke_mod
from fastcircuit import _block
qc = smoke_mod.build(6, 4, 0.3)
[(_, compiled)] = _block.compiled_blocks()
print(json.dumps({
    "package": fastcircuit.__file__,
    "backend": compiled.backend,
    "source": compiled.timings["source"],
    "equal": qc == smoke_mod.reference(6, 4, 0.3),
    "gates": len(qc),
}))
"""


def check(ok: object, message: object) -> None:
    if not ok:
        raise SystemExit(f"smoke test FAILED: {message}")


def child(directory: Path, env: dict[str, str]) -> dict[str, object]:
    out = subprocess.run(
        [sys.executable, "-I", "-c", CHILD, str(directory)],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    result: dict[str, object] = json.loads(out.strip().splitlines()[-1])
    return result


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        (directory / "smoke_mod.py").write_text(MODULE)
        env = {**os.environ, "FASTCIRCUIT_CACHE_DIR": str(directory / "cache")}
        first = child(directory, env)
        second = child(directory, env)
        print(json.dumps({"first": first, "second": second}, indent=2))
        package = Path(str(first["package"])).resolve()
        check(repo not in package.parents, f"imported the source tree, not the install: {package}")
        check(first["equal"] and second["equal"], "compiled circuit differs from Python")
        check(first["source"] == "compiled", first)
        check(second["source"] == "persistent cache", second)
        doctor = subprocess.run(
            [sys.executable, "-I", "-m", "fastcircuit", "doctor"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        print(doctor.stdout)
        check(doctor.returncode == 0, doctor.stderr)
    print("smoke test passed")


if __name__ == "__main__":
    main()
