"""Create importable modules from source text (compiled functions need real files)."""

from __future__ import annotations

import importlib.util
import itertools
import sys
import textwrap
from pathlib import Path
from types import ModuleType

_counter = itertools.count()

HEADER = """\
import math
import numpy as np
from qiskit import QuantumCircuit
from support.blocks import as_block
"""


BLOCK_HEADER = """\
import math
import numpy as np
from qiskit import QuantumCircuit
import fastcircuit
"""


def import_module(directory: Path, source: str, header: str = BLOCK_HEADER) -> ModuleType:
    """Import through the regular import system, so fastcircuit's import hook runs.

    ``directory`` must be on ``sys.path`` (``monkeypatch.syspath_prepend``).
    """
    name = f"fc_imported_{next(_counter)}"
    (directory / f"{name}.py").write_text(header + "\n" + textwrap.dedent(source))
    importlib.invalidate_caches()
    return importlib.import_module(name)


def load_module(directory: Path, source: str, header: str = HEADER) -> ModuleType:
    name = f"fc_generated_{next(_counter)}"
    path = directory / f"{name}.py"
    path.write_text(header + "\n" + textwrap.dedent(source))
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
