"""The command line: ``python -m fastcircuit``."""

from __future__ import annotations

import pytest

import fastcircuit
from fastcircuit.__main__ import main


def test_version(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"fastcircuit {fastcircuit.__version__}"


@pytest.mark.parametrize(("name", "errno"), [("missing.py", 2), (".", 21)])
def test_run_reports_an_unopenable_script_like_python(tmp_path, monkeypatch, capsys, name, errno):
    monkeypatch.chdir(tmp_path)
    assert main(["run", name]) == 2  # python's own exit status for this
    err = capsys.readouterr().err
    assert err.startswith("python -m fastcircuit: can't open file ")
    assert f"[Errno {errno}]" in err
    assert "Traceback" not in err
