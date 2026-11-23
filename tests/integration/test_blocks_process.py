"""'with fastcircuit:' blocks in real programs: scripts, imports, re-runs, the cache."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from support.processes import child_env

BACKEND = "FASTCIRCUIT_BACKEND"  # children use the backend this test run uses
SCRIPT = """\
import sys
from qiskit import QuantumCircuit
import fastcircuit

def build(n):
    qc = QuantumCircuit(n)
    with fastcircuit.block(debug=True):
        for i in range(n):
            qc.h(i)
    return qc

top = QuantumCircuit(2)
with fastcircuit:
    for i in range(2):
        top.x(i)

if __name__ == "__main__":
    qc = build(int(sys.argv[1]))
    print("ops", qc.count_ops()["h"], top.count_ops()["x"])
"""


def run(args: list[str], cwd: Path, cache: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=cwd,
        env=child_env(cache, **{k: v for k, v in os.environ.items() if k == BACKEND}),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def write(directory: Path, name: str, source: str) -> None:
    (directory / name).write_text(textwrap.dedent(source))


@pytest.mark.parametrize(
    "launch",
    [
        ["app.py"],  # plain Python: the script is re-run with its blocks compiled
        ["-m", "fastcircuit", "run", "app.py"],  # the fallback launcher
    ],
    ids=["python", "launcher"],
)
def test_script_with_blocks_runs_and_reuses_the_cache(tmp_path, launch):
    write(tmp_path, "app.py", SCRIPT)
    cache = tmp_path / "cache"

    first = run([*launch, "3"], tmp_path, cache)
    assert first.returncode == 0, first.stderr
    assert first.stdout.strip() == "ops 3 2"
    assert "code from: compiled" in first.stderr

    second = run([*launch, "4"], tmp_path, cache)
    assert second.returncode == 0, second.stderr
    assert second.stdout.strip() == "ops 4 2"
    assert "code from: persistent cache" in second.stderr


RERUN = """\
'''Docstring.'''
from qiskit import QuantumCircuit
import fastcircuit
print("once")

def ghz(n):
    qc = QuantumCircuit(n)
    with fastcircuit:
        for i in range(n - 1):
            qc.cx(i, i + 1)
    return qc

"""


def test_rerun_preserves_exit_code_output_and_main_module(tmp_path):
    write(
        tmp_path,
        "app.py",
        RERUN
        + "import sys\n"
        + "assert sys.modules['__main__'].ghz is ghz\n"
        + "print(ghz(4).count_ops()['cx'])\n"
        + "sys.exit(3)\n",
    )
    result = run(["app.py"], tmp_path, tmp_path / "cache")
    assert result.returncode == 3, result.stderr
    assert result.stdout.split() == ["once", "3"]


def test_rerun_reports_errors_like_python(tmp_path):
    write(tmp_path, "app.py", RERUN + "def boom():\n    raise ValueError('bad')\n\nboom()\n")
    result = run(["app.py"], tmp_path, tmp_path / "cache")
    assert result.returncode == 1, result.stderr
    lines = result.stderr.splitlines()
    assert lines[0] == "Traceback (most recent call last):", result.stderr
    assert lines[1].startswith(f'  File "{tmp_path / "app.py"}", line 16, in <module>'), (
        result.stderr
    )
    assert lines[-1] == "ValueError: bad", result.stderr
    assert "fastcircuit" not in result.stderr.replace("import fastcircuit", "")


def test_threads_can_import_fastcircuit_during_the_rerun(tmp_path):
    write(tmp_path, "helper.py", "import fastcircuit\n")
    write(
        tmp_path,
        "app.py",
        RERUN
        + "import threading\n"
        + "def work():\n    import helper\n"
        + "t = threading.Thread(target=work)\nt.start()\nt.join(60)\n"
        + "print('joined', not t.is_alive())\n",
    )
    result = run(["app.py"], tmp_path, tmp_path / "cache")
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["once", "joined", "True"]


def test_spawned_worker_processes_use_compiled_blocks(tmp_path):
    write(
        tmp_path,
        "app.py",
        RERUN
        + "def work(n):\n    return ghz(n).count_ops()['cx']\n\n"
        + "if __name__ == '__main__':\n"
        + "    import multiprocessing as mp\n"
        + "    with mp.get_context('spawn').Pool(1) as pool:\n"
        + "        print('pool', pool.map(work, [3, 5]))\n",
    )
    result = run(["app.py"], tmp_path, tmp_path / "cache")
    assert result.returncode == 0, result.stderr
    assert "pool [2, 4]" in result.stdout


SCRIPT_BLOCKS = """\
from qiskit import QuantumCircuit

def mine(n):
    qc = QuantumCircuit(n)
    with fastcircuit:
        for i in range(n):
            qc.x(i)
    return qc

top = QuantumCircuit(2)
with fastcircuit:
    top.z(0)
print("main", mine(3).count_ops()["x"], top.count_ops()["z"])
"""


@pytest.mark.parametrize(
    ("imports", "check"),
    [
        ("import circuits\nimport fastcircuit\n", "circuits.ghz(4)"),
        ("from circuits import ghz\nimport numpy as np\nimport fastcircuit\n", "ghz(4)"),
        ("import pkg\nimport fastcircuit\n", "pkg.ghz(4)"),
        ("try:\n    import fastcircuit\nexcept ImportError:\n    pass\nimport pkg\n", "pkg.ghz(4)"),
    ],
    ids=["module-first", "from-import-first", "package-first", "optional-import"],
)
def test_script_blocks_work_with_any_import_order(tmp_path, imports, check):
    write(tmp_path, "circuits.py", RERUN)  # imports fastcircuit itself; prints "once"
    (tmp_path / "pkg").mkdir()
    write(
        tmp_path, "pkg/__init__.py", '"""Package."""\n__version__ = "1.0"\nfrom .core import ghz\n'
    )
    write(tmp_path, "pkg/core.py", RERUN)
    script = imports + SCRIPT_BLOCKS + f"print('lib', {check}.count_ops()['cx'])\n"
    write(tmp_path, "app.py", script)
    result = run(["app.py"], tmp_path, tmp_path / "cache")
    assert result.returncode == 0, result.stderr
    assert result.stdout.split("\n")[-3:] == ["main 3 1", "lib 3", ""]
    assert result.stdout.count("once") == 1  # the library's own output, not repeated


@pytest.mark.parametrize(
    ("prefix", "fragment"),
    [
        ('print("side effect")\n', "move 'import fastcircuit' above line 1"),
        ("import helper\n", "helper.py, which would have to run again, and its line 1"),
    ],
    ids=["statement-before-import", "statement-before-import-in-imported-module"],
)
def test_script_that_cannot_be_rerun_fails_loudly(tmp_path, prefix, fragment):
    write(tmp_path, "helper.py", 'print("side effect")\nimport fastcircuit\n')
    write(tmp_path, "app.py", prefix + RERUN + "ghz(3)\n")
    result = run(["app.py"], tmp_path, tmp_path / "cache")
    assert result.returncode == 1
    assert "BlockNotCompiledError" in result.stderr
    assert fragment in result.stderr
    assert result.stdout.count("side effect") == 1  # never twice


def test_unsupported_block_in_script_fails_at_import(tmp_path):
    body = "def bad(qc):\n    k = 0\n    with fastcircuit:\n        k = 1\n    return k\n"
    write(tmp_path, "app.py", RERUN + body)
    result = run(["app.py"], tmp_path, tmp_path / "cache")
    assert result.returncode == 1
    assert "UnsupportedSyntax" in result.stderr
    assert "'k' is assigned inside the block" in result.stderr
    assert "once" not in result.stdout


@pytest.mark.parametrize(
    "main",
    [
        "import circuits\n",  # circuits imports fastcircuit itself: patched when loaded
        "import fastcircuit\nimport circuits\n",  # rewritten by the import hook
    ],
)
def test_blocks_in_an_imported_module_work_under_plain_python(tmp_path, main):
    write(
        tmp_path,
        "circuits.py",
        """
        from qiskit import QuantumCircuit
        import fastcircuit

        def ghz(n):
            qc = QuantumCircuit(n)
            qc.h(0)
            with fastcircuit:
                for i in range(n - 1):
                    qc.cx(i, i + 1)
            return qc
        """,
    )
    write(tmp_path, "main.py", main + "print(circuits.ghz(5).count_ops()['cx'])\n")
    result = run(["main.py"], tmp_path, tmp_path / "cache")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "4"


LATIN1_SCRIPT = """\
# -*- coding: latin-1 -*-
from qiskit import QuantumCircuit
import fastcircuit

qc = QuantumCircuit(2)
with fastcircuit:
    for i in range(2):
        qc.h(i)
print("caf\xe9", qc.count_ops()["h"])
"""


@pytest.mark.parametrize("launch", [[], ["-m", "fastcircuit", "run"]], ids=["python", "launcher"])
def test_script_with_a_coding_declaration_is_decoded_like_python(tmp_path, launch):
    (tmp_path / "app.py").write_bytes(LATIN1_SCRIPT.encode("latin-1"))
    result = run([*launch, "app.py"], tmp_path, tmp_path / "cache")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "caf\xe9 2"
