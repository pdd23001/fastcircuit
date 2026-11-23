"""C backend: persistent cache with a real C compiler (corruption, concurrency, interruption).

The LLVM backend's counterparts are in test_cache_llvm.py.
"""

from __future__ import annotations

import os
import signal
import time

import pytest

from fastcircuit import _build

from support.processes import (
    child_env as _child_env,
)
from support.processes import (
    compile_invocations,
    logging_compiler,
    run_child,
    start_child,
    write_module,
)

pytestmark = pytest.mark.needs_c_backend


def child_env(cache, **extra):
    return _child_env(cache, FASTCIRCUIT_BACKEND="c", **extra)


@pytest.fixture
def setup(tmp_path):
    cache = tmp_path / "cache"
    log = tmp_path / "cc.log"
    real = _build.find_toolchain().path
    module = write_module(tmp_path)
    return tmp_path, cache, log, real, module


def _entry_binaries(cache):
    return sorted((cache / "v1" / "entries").glob("*/*/*.so"))


def test_corrupted_binary_is_rebuilt(setup):
    directory, cache, log, real, module = setup
    env = child_env(cache, CC=str(logging_compiler(directory, log, real)))
    assert run_child(directory, module, env)["source"] == "compiled"
    (binary,) = _entry_binaries(cache)
    data = bytearray(binary.read_bytes())
    data[len(data) // 2] ^= 0xFF  # same size, different content
    binary.write_bytes(bytes(data))
    result = run_child(directory, module, env)
    assert result == {**result, "source": "compiled", "equal": True}
    assert len(compile_invocations(log)) == 2


def test_truncated_metadata_is_rebuilt(setup):
    directory, cache, log, real, module = setup
    env = child_env(cache, CC=str(logging_compiler(directory, log, real)))
    run_child(directory, module, env)
    (meta,) = (cache / "v1" / "entries").glob("*/*/meta.json")
    meta.write_text(meta.read_text()[:20])
    assert run_child(directory, module, env)["source"] == "compiled"


def test_concurrent_first_calls_compile_once(setup):
    directory, cache, log, real, module = setup
    env = child_env(cache, CC=str(logging_compiler(directory, log, real, delay=1)))
    procs = [start_child(directory, module, env) for _ in range(4)]
    outputs = []
    for p in procs:
        out, err = p.communicate(timeout=300)
        assert p.returncode == 0, err
        outputs.append(out)
    assert len(compile_invocations(log)) == 1
    sources = sorted(o.split('"source": "')[1].split('"')[0] for o in outputs)
    assert sources == ["compiled", "persistent cache", "persistent cache", "persistent cache"]
    assert len(_entry_binaries(cache)) == 1


def test_killed_compilation_leaves_no_entry(setup):
    directory, cache, log, real, module = setup
    slow = logging_compiler(directory, log, real, delay=5)
    proc = start_child(directory, module, child_env(cache, CC=str(slow)))
    deadline = time.time() + 120
    while not compile_invocations(log):
        assert time.time() < deadline, "compiler never started"
        time.sleep(0.05)
    os.kill(proc.pid, signal.SIGKILL)  # interrupted mid-compilation
    proc.wait()
    assert _entry_binaries(cache) == []
    staging = list((cache / "v1" / "tmp").iterdir())
    assert staging  # the partial build stays in tmp/, never in entries/
    # The lock died with the process; a new process compiles normally.
    fast = logging_compiler(directory, directory / "second.log", real)
    result = run_child(directory, module, child_env(cache, CC=str(fast)))
    assert result["source"] == "compiled" and result["equal"]
    # Stale staging directories are removed once they are old.
    old = time.time() - 2 * 24 * 3600
    for path in staging:
        os.utime(path, (old, old))
    from fastcircuit._cache import Cache

    Cache(cache).staging_dir()
    assert not any(p.exists() for p in staging)


def test_interrupted_write_in_staging_is_never_loaded(setup):
    directory, cache, log, real, module = setup
    env = child_env(cache, CC=str(logging_compiler(directory, log, real)))
    # Simulate an interrupted build: a partial binary left in tmp/.
    tmp = cache / "v1" / "tmp" / "build-interrupted"
    tmp.mkdir(parents=True)
    (tmp / "partial.so").write_bytes(b"\x7fELF partial")
    os.chmod(cache, 0o700)
    os.chmod(cache / "v1", 0o700)
    os.chmod(cache / "v1" / "tmp", 0o700)
    result = run_child(directory, module, env)
    assert result["source"] == "compiled" and result["equal"]


def test_cache_disabled_compiles_every_process(setup):
    directory, cache, log, real, module = setup
    env = child_env(cache, CC=str(logging_compiler(directory, log, real)), FASTCIRCUIT_NO_CACHE="1")
    assert run_child(directory, module, env)["source"] == "compiled (cache disabled)"
    assert run_child(directory, module, env)["source"] == "compiled (cache disabled)"
    assert len(compile_invocations(log)) == 2
    assert not cache.exists()
