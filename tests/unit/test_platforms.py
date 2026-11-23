"""Platform policy: which llvmlite pip installs where, and which backend is chosen.

The llvmlite requirements in pyproject.toml are evaluated with pip's own marker
logic for every platform in the support table, so the published policy (README,
docs/platforms.md) and the packaging metadata cannot drift apart.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement

from fastcircuit import IncompatibleEnvironmentError, _compat

ROOT = Path(__file__).resolve().parents[2]


def _llvmlite_requirements() -> list[Requirement]:
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    return [r for r in map(Requirement, deps) if r.name == "llvmlite"]


def _llvmlite_for(sys_platform: str, machine: str, python: str) -> str | None:
    env = {
        "sys_platform": sys_platform,
        "platform_machine": machine,
        "python_version": python,
        "python_full_version": python + ".0",
        "platform_system": {"darwin": "Darwin", "linux": "Linux"}[sys_platform],
        "os_name": "posix",
        "implementation_name": "cpython",
        "platform_python_implementation": "CPython",
    }
    chosen = [str(r.specifier) for r in _llvmlite_requirements() if r.marker.evaluate(env)]
    assert len(chosen) <= 1, f"overlapping llvmlite markers for {env}: {chosen}"
    return chosen[0] if chosen else None


NEW = "<0.51,>=0.50"
OLD = "<0.46,>=0.45"


@pytest.mark.parametrize(
    ("sys_platform", "machine", "python", "expected"),
    [
        ("darwin", "arm64", "3.11", NEW),
        ("darwin", "arm64", "3.14", NEW),
        ("linux", "x86_64", "3.12", NEW),
        ("linux", "aarch64", "3.14", NEW),
        ("darwin", "x86_64", "3.11", OLD),  # Intel Mac: last llvmlite with wheels
        ("darwin", "x86_64", "3.13", OLD),
        ("darwin", "x86_64", "3.14", None),  # -> C backend, needs a compiler
        ("linux", "ppc64le", "3.12", None),  # -> C backend
        ("linux", "s390x", "3.12", None),  # -> C backend
    ],
)
def test_llvmlite_markers_match_platform_policy(sys_platform, machine, python, expected):
    assert _llvmlite_for(sys_platform, machine, python) == expected


@pytest.fixture
def platform(monkeypatch):
    def set_platform(name: str, *, llvmlite: bool) -> None:
        monkeypatch.setattr(_compat, "_platform", lambda: name)
        monkeypatch.setattr(_compat, "llvmlite_available", lambda: llvmlite)

    monkeypatch.delenv("FASTCIRCUIT_BACKEND", raising=False)
    return set_platform


def test_llvm_is_chosen_when_llvmlite_is_installed(platform):
    platform("linux", llvmlite=True)
    assert _compat.select_backend()[0] == "llvm"


def test_c_is_chosen_without_llvmlite(platform):
    platform("darwin", llvmlite=False)
    backend, reason = _compat.select_backend()
    assert backend == "c" and "C compiler" in reason


def test_environment_variable_overrides(platform, monkeypatch):
    platform("linux", llvmlite=True)
    monkeypatch.setenv("FASTCIRCUIT_BACKEND", "C")
    assert _compat.select_backend() == ("c", "set by FASTCIRCUIT_BACKEND")
    monkeypatch.setenv("FASTCIRCUIT_BACKEND", "gcc")
    with pytest.raises(ValueError, match="must be one of llvm, c"):
        _compat.select_backend()


def test_forcing_llvm_without_llvmlite_explains_the_c_option(platform):
    platform("linux", llvmlite=False)
    with pytest.raises(IncompatibleEnvironmentError, match="FASTCIRCUIT_BACKEND=c"):
        _compat.check_environment("llvm")
