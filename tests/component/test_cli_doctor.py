"""``python -m fastcircuit doctor`` output (it compiles a small program, hence component)."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from fastcircuit.__main__ import main

STATUS = ("  ok    ", "  FAIL  ", "  --    ", "        ")  # or an indented detail line


def assert_report_layout(out: str) -> None:
    lines = out.splitlines()[1:]
    assert lines, "doctor printed no checks"
    for line in lines:
        assert line.startswith(STATUS), line


def test_doctor_puts_each_status_first(capsys):
    main(["doctor"])
    assert_report_layout(capsys.readouterr().out)


def test_doctor_reports_an_unknown_backend(monkeypatch, capsys):
    monkeypatch.setenv("FASTCIRCUIT_BACKEND", "gpu")
    assert main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "  FAIL  backend: FASTCIRCUIT_BACKEND must be one of" in out
    assert_report_layout(out)


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
def test_doctor_reports_a_cache_directory_it_cannot_create(tmp_path, monkeypatch, capsys):
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    monkeypatch.setenv("FASTCIRCUIT_CACHE_DIR", str(locked / "cache"))
    try:
        assert main(["doctor"]) == 1
        assert "  FAIL  cache directory: cannot create cache directory" in capsys.readouterr().out
    finally:
        locked.chmod(0o700)


def test_doctor_reports_an_unreadable_cc_without_a_traceback(monkeypatch, capsys):
    monkeypatch.delenv("FASTCIRCUIT_BACKEND", raising=False)
    monkeypatch.setenv("CC", 'gcc "')
    main(["doctor"])
    out = capsys.readouterr().out
    assert "cannot read CC=" in out
    assert_report_layout(out)


def test_doctor_reports_a_missing_llvmlite_without_a_traceback(tmp_path):
    # In a fresh process, so llvmlite is not already imported.
    child = "import sys; sys.modules['llvmlite'] = None\nfrom fastcircuit.__main__ import main\n"
    child += "sys.exit(main(['doctor']))\n"
    result = subprocess.run(
        [sys.executable, "-c", child],
        env={**os.environ, "FASTCIRCUIT_BACKEND": "llvm", "FASTCIRCUIT_CACHE_DIR": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 1, result.stderr
    assert "Traceback" not in result.stderr
    assert "  FAIL  environment: the LLVM backend needs llvmlite" in result.stdout
    assert_report_layout(result.stdout)
