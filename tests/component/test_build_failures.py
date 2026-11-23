"""Compiler failure injection with fake compilers (the real path is tested elsewhere)."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from fastcircuit import CompilerNotFoundError, NativeBuildError, _build

pytestmark = pytest.mark.needs_c_backend

SOURCE = """
@as_block
def build(n: int):
    qc = QuantumCircuit(n)
    qc.h(0)
    qc.rz({salt}, 0)
    return qc
"""


@pytest.fixture(autouse=True)
def c_backend(monkeypatch):
    """These tests exercise the C compiler path."""
    monkeypatch.setenv("FASTCIRCUIT_BACKEND", "c")


def _script(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def fresh(make_function, isolated_cache):
    counter = iter(range(1000))
    return lambda: make_function(SOURCE.format(salt=f"0.{next(counter)}1"))


def test_missing_compiler_is_actionable(fresh, monkeypatch):
    monkeypatch.setenv("CC", "/nonexistent/bin/cc")
    build = fresh()
    with pytest.raises(CompilerNotFoundError) as info:
        build(2)
    text = str(info.value)
    assert "no C compiler found" in text
    assert "python3-dev" in text and "xcode-select" in text and "doctor" in text


def test_missing_python_headers(fresh, monkeypatch, tmp_path):
    real = _build.include_dirs()
    monkeypatch.setattr(_build, "include_dirs", lambda: (str(tmp_path / "noinclude"), real[1]))
    with pytest.raises(CompilerNotFoundError, match="development headers"):
        fresh()(2)


def test_compiler_error_keeps_source_and_log(fresh, monkeypatch, tmp_path, isolated_cache):
    fake = _script(tmp_path, "badcc", 'echo "fake: error: something broke" >&2; exit 3')
    monkeypatch.setenv("CC", str(fake))
    with pytest.raises(NativeBuildError) as info:
        fresh()(2)
    text = str(info.value)
    assert "exit 3" in text and "something broke" in text
    failed = list((isolated_cache / "v1" / "failed").iterdir())
    assert len(failed) == 1
    assert (failed[0] / "source.c").exists()
    assert "something broke" in (failed[0] / "build.log").read_text()
    assert str(failed[0]) in text
    assert not (isolated_cache / "v1" / "entries").exists() or not any(
        (isolated_cache / "v1" / "entries").iterdir()
    )


def test_compiler_timeout(fresh, monkeypatch, tmp_path):
    fake = _script(
        tmp_path, "slowcc", 'case "$*" in *--version*) echo slow 1.0;; *) sleep 30;; esac'
    )
    monkeypatch.setenv("CC", str(fake))
    monkeypatch.setattr(_build, "COMPILE_TIMEOUT_SECONDS", 1)
    with pytest.raises(NativeBuildError, match="timed out"):
        fresh()(2)


def test_unloadable_binary_is_discarded(fresh, monkeypatch, tmp_path, isolated_cache):
    # Exit status 0 but garbage output: must not stay in the cache.
    fake = _script(
        tmp_path,
        "garbagecc",
        'case "$*" in *--version*) echo garbage 1.0; exit 0;; esac\n'
        'while [ $# -gt 0 ]; do if [ "$1" = "-o" ]; then echo junk > "$2"; fi; shift; done',
    )
    monkeypatch.setenv("CC", str(fake))
    build = fresh()
    with pytest.raises(NativeBuildError, match="could not be loaded"):
        build(2)
    entries = isolated_cache / "v1" / "entries"
    assert all(not any(d.iterdir()) for d in entries.iterdir())


def test_cc_with_arguments(fresh, monkeypatch):
    # CC may contain a launcher and flags, e.g. "ccache gcc"; it is split like a shell.
    real = _build.find_toolchain()
    monkeypatch.setenv("CC", f"env FASTCIRCUIT_TEST=1 {real.path}")
    build = fresh()
    assert build(2).num_qubits == 2
    assert build.timings["source"] == "compiled"


def test_doctor_reports_missing_compiler(monkeypatch, capsys):
    from fastcircuit.__main__ import main

    monkeypatch.setenv("CC", "/nonexistent/cc")
    assert main(["doctor"]) == 1
    assert "FAIL  C compiler" in capsys.readouterr().out
    monkeypatch.delenv("CC")
    assert main(["doctor"]) == 0
