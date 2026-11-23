"""Cache-key construction, entry validation and environment checks (no compiler)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess

import pytest

from fastcircuit import CacheError, IncompatibleEnvironmentError, _build, _cache, _compat

CONFIG = {"cflags": ["-O2"], "ldflags": ["-shared"], "ext_suffix": ".so", "qiskit_headers": "x"}


def test_key_is_deterministic():
    assert _cache.make_key("int main;", CONFIG) == _cache.make_key("int main;", dict(CONFIG))


@pytest.mark.parametrize(
    "change",
    [
        lambda src, cfg: (src + " ", cfg),
        lambda src, cfg: (src, {**cfg, "cflags": ["-O2", "-fsanitize=undefined"]}),
        lambda src, cfg: (src, {**cfg, "qiskit_headers": "y"}),
        lambda src, cfg: (src, {**cfg, "ext_suffix": ".cpython-313-x86_64-linux-gnu.so"}),
    ],
    ids=["source", "cflags", "headers", "abi_suffix"],
)
def test_key_changes_with_inputs(change):
    src, cfg = change("int main;", CONFIG)
    assert _cache.make_key(src, cfg) != _cache.make_key("int main;", CONFIG)


@pytest.mark.parametrize(
    ("attr", "value"),
    [
        ("qiskit.__version__", "2.6.99"),
        ("platform.machine", lambda: "riscv64"),
        ("sys.platform", "sunos"),
        ("fastcircuit._cache.CODEGEN_VERSION", 999),
        ("fastcircuit._cache.__version__", "9.9.9"),
    ],
)
def test_key_changes_with_environment(monkeypatch, attr, value):
    before = _cache.make_key("x", CONFIG)
    monkeypatch.setattr(attr, value)
    assert _cache.make_key("x", CONFIG) != before


def test_compile_config_contains_flags_and_headers(monkeypatch):
    monkeypatch.setenv("FASTCIRCUIT_EXTRA_CFLAGS", "-fsanitize=undefined -g")
    config = _build.compile_config()
    assert "-ffp-contract=off" in config["cflags"]
    assert config["cflags"][-2:] == ["-fsanitize=undefined", "-g"]
    assert len(config["qiskit_headers"]) == 64


def _fake_entry(root, key="k" * 64, compiler="c" * 64, payload=b"binary"):
    entry = root / _cache.LAYOUT / "entries" / key / compiler
    entry.mkdir(parents=True)
    (entry / "mod.so").write_bytes(payload)
    meta = {
        "layout": _cache.LAYOUT,
        "key": key,
        "compiler": compiler,
        "module": "mod",
        "binary": "mod.so",
        "sha256": hashlib.sha256(b"binary").hexdigest(),
    }
    (entry / "meta.json").write_text(json.dumps(meta))
    return entry, key, compiler


def test_lookup_validates_checksum(tmp_path):
    entry, key, compiler = _fake_entry(tmp_path)
    cache = _cache.Cache(tmp_path)
    found = cache.lookup(key, compiler)
    assert found is not None and found.module == "mod"
    assert cache.lookup(key, None) is not None  # any compiler (no compiler installed)
    (entry / "mod.so").write_bytes(b"tampered")
    assert cache.lookup(key, compiler) is None
    assert not entry.exists()  # discarded, never loaded


@pytest.mark.parametrize("damage", ["truncate_meta", "delete_binary", "wrong_key", "escape_path"])
def test_lookup_discards_damaged_entries(tmp_path, damage):
    entry, key, compiler = _fake_entry(tmp_path)
    meta = json.loads((entry / "meta.json").read_text())
    if damage == "truncate_meta":
        (entry / "meta.json").write_text('{"layout": ')
    elif damage == "delete_binary":
        (entry / "mod.so").unlink()
    elif damage == "wrong_key":
        meta["key"] = "other"
        (entry / "meta.json").write_text(json.dumps(meta))
    else:
        meta["binary"] = "../../../../evil.so"
        (entry / "meta.json").write_text(json.dumps(meta))
    assert _cache.Cache(tmp_path).lookup(key, compiler) is None
    assert not entry.exists()


def test_cache_rejects_shared_writable_directory(tmp_path):
    root = tmp_path / "shared"
    root.mkdir()
    os.chmod(root, 0o777)  # noqa: S103 - the unsafe mode under test
    with pytest.raises(CacheError, match="writable by other users"):
        _cache.Cache(root).staging_dir()


def test_cache_rejects_foreign_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "getuid", lambda: os.stat(tmp_path).st_uid + 1)
    with pytest.raises(CacheError, match="owned by another user"):
        _cache.ensure_private_dir(tmp_path / "x")


def test_default_root(monkeypatch, tmp_path):
    monkeypatch.setenv("FASTCIRCUIT_CACHE_DIR", str(tmp_path / "c"))
    assert _cache.default_root() == tmp_path / "c"
    monkeypatch.delenv("FASTCIRCUIT_CACHE_DIR")
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert _cache.default_root() == tmp_path / "xdg" / "fastcircuit"


def test_clear_and_info(tmp_path):
    _fake_entry(tmp_path)
    cache = _cache.Cache(tmp_path)
    assert cache.info()["entries"] == 1
    assert cache.clear() == 1
    assert cache.info()["entries"] == 0


def test_compiler_identity_is_memoised_on_disk(tmp_path, monkeypatch):
    tool = _build.Toolchain(("cc",), "/usr/bin/true")
    calls = []
    real_run = subprocess.run

    def counting_run(*args, **kwargs):
        calls.append(args)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(_build.subprocess, "run", counting_run)
    monkeypatch.setattr(_build, "_IDENTITIES", {})
    first = _build.compiler_identity(tool, tmp_path)
    monkeypatch.setattr(_build, "_IDENTITIES", {})  # simulate a new process
    second = _build.compiler_identity(tool, tmp_path)
    assert first == second
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("version", "expected"), [("2.6.2", (2, 6)), ("2.7.0rc1", (2, 7)), ("dev", None)]
)
def test_qiskit_minor(version, expected):
    assert _compat.qiskit_minor(version) == expected


def test_untested_qiskit_is_rejected(monkeypatch):
    monkeypatch.setattr("qiskit.__version__", "2.7.0")
    monkeypatch.delenv("FASTCIRCUIT_ALLOW_UNTESTED_QISKIT", raising=False)
    # The C backend: the Qiskit check is the same for both, and the LLVM backend's
    # own check (llvmlite installed) would fire first where llvmlite has no wheels.
    with pytest.raises(IncompatibleEnvironmentError, match=r"tested with Qiskit 2\.6"):
        _compat.check_environment("c")
    monkeypatch.setenv("FASTCIRCUIT_ALLOW_UNTESTED_QISKIT", "1")
    _compat.check_environment("c")


def test_free_threaded_build_is_rejected(monkeypatch):
    real = _compat.sysconfig.get_config_var
    monkeypatch.setattr(
        _compat.sysconfig,
        "get_config_var",
        lambda name: 1 if name == "Py_GIL_DISABLED" else real(name),
    )
    with pytest.raises(IncompatibleEnvironmentError, match="free-threaded"):
        _compat.check_environment()
