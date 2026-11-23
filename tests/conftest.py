"""Shared pytest configuration.

Layers live in directories (unit/, component/, integration/, property/, e2e/) and
are marked automatically, so ``pytest tests/unit`` and ``pytest -m unit`` agree.
Every session uses a private persistent-cache directory unless
FASTCIRCUIT_CACHE_DIR is already set (CI points it at an artifact directory).
"""

from __future__ import annotations

import importlib.util
import os
import tempfile
from pathlib import Path

import pytest

_LAYERS = ("unit", "component", "integration", "property", "e2e")

if "FASTCIRCUIT_CACHE_DIR" not in os.environ:
    os.environ["FASTCIRCUIT_CACHE_DIR"] = tempfile.mkdtemp(prefix="fastcircuit-test-cache-")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    root = Path(__file__).parent
    # pip installs llvmlite only where it has wheels (e.g. not Intel macOS on 3.14+);
    # there the C backend is the default and LLVM-specific tests cannot run.
    no_llvmlite = importlib.util.find_spec("llvmlite") is None
    for item in items:
        if no_llvmlite and item.get_closest_marker("needs_llvmlite"):
            item.add_marker(pytest.mark.skip(reason="llvmlite is not installed on this platform"))
        try:
            layer = Path(item.path).relative_to(root).parts[0]
        except ValueError:
            continue
        if layer in _LAYERS:
            item.add_marker(getattr(pytest.mark, layer))


@pytest.fixture
def make_function(tmp_path: Path):
    """Write ``source`` to a real module file and return ``module.<name>``.

    Compiled functions must come from files (the compiler reads their source).
    """
    from support.modules import load_module

    def factory(source: str, name: str = "build"):
        return getattr(load_module(tmp_path, source), name)

    return factory


@pytest.fixture
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fresh cache directory, and no in-process reuse (either backend), for one test."""
    from fastcircuit import _native

    cache = tmp_path / "cache"
    monkeypatch.setenv("FASTCIRCUIT_CACHE_DIR", str(cache))
    monkeypatch.setattr(_native, "_LOADED", {})
    monkeypatch.setattr(_native, "_RUNNERS", {})
    return cache
