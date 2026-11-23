"""Setup problems are reported as fastcircuit errors that say what to change."""

from __future__ import annotations

import os

import pytest

from fastcircuit import _build, _cache
from fastcircuit.errors import CacheError, CompilerNotFoundError


def test_an_unreadable_cc_is_a_compiler_error(monkeypatch):
    monkeypatch.setenv("CC", 'gcc "')
    with pytest.raises(CompilerNotFoundError, match=r"cannot read CC=.*No closing quotation"):
        _build.find_toolchain()


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
def test_a_cache_directory_that_cannot_be_created_is_a_cache_error(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    try:
        with pytest.raises(CacheError, match=r"cannot create cache directory .*_CACHE_DIR"):
            _cache.ensure_private_dir(locked / "cache")
    finally:
        locked.chmod(0o700)
