"""The rule that decides whether modules may be re-run from the top (syntax only)."""

from __future__ import annotations

import ast
import textwrap

import pytest

from fastcircuit._hooks import _rerun_problem


def check(source: str) -> str | None:
    """Pretend the module is executing the first line that mentions fastcircuit."""
    source = textwrap.dedent(source)
    lineno = next(i for i, line in enumerate(source.splitlines(), 1) if "fastcircuit" in line)
    return _rerun_problem(ast.parse(source), lineno, "mod.py", script=True)


@pytest.mark.parametrize(
    "source",
    [
        "import fastcircuit\n",
        '"""Doc."""\nfrom __future__ import annotations\nimport os, sys\nimport fastcircuit\n',
        "import numpy as np\nfrom qiskit import (\n    QuantumCircuit,\n)\nimport fastcircuit\n",
        "from fastcircuit import block\n",
        "import os\nimport fastcircuit; import sys\n",
        '__version__ = "1.0"\n__all__ = ["a", "b"]\nLIMIT: int = -3\nimport fastcircuit\n',
        "try:\n    import scipy\nexcept ImportError:\n    scipy = None\nimport fastcircuit\n",
        "try:\n    import fastcircuit\nexcept (ImportError, OSError):\n    pass\n",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import x\nimport fastcircuit\n",
        "import sys\nif sys.version_info >= (3, 12):\n    import fastcircuit\n",
    ],
)
def test_safe_to_repeat(source):
    assert check(source) is None


@pytest.mark.parametrize(
    ("source", "fragment"),
    [
        ("import sys\nsys.path.insert(0, '.')\nimport fastcircuit\n", "above line 2"),
        ("x = compute()\nimport fastcircuit\n", "above line 1"),
        ("print('hi')\nimport fastcircuit\n", "above line 1"),
        ("items = []\nitems.append(1)\nimport fastcircuit\n", "above line 2"),
        ("if check():\n    import os\nimport fastcircuit\n", "above line 1"),
        ("try:\n    import os\nexcept:\n    pass\nimport fastcircuit\n", "above line 1"),
        ("try:\n    import fastcircuit\nexcept BaseException:\n    pass\n", "above line 1"),
        ("def f():\n    pass\nimport fastcircuit\n", "above line 1"),
        ("with open('f') as fh:\n    import fastcircuit\n", "not an import statement"),
    ],
)
def test_unsafe_to_repeat(source, fragment):
    reason = check(source)
    assert reason is not None
    assert fragment in reason


def test_module_on_the_import_chain_is_named():
    tree = ast.parse("print('loading')\nimport fastcircuit\n")
    reason = _rerun_problem(tree, 2, "lib/helper.py", script=False)
    assert reason is not None
    assert "lib/helper.py" in reason
    assert "line 1" in reason
