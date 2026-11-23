"""Persistent, process-safe cache of compiled extension modules.

Layout (``LAYOUT`` is bumped on incompatible changes)::

    <root>/v1/entries/<key>/<compiler>/   meta.json, <module><EXT_SUFFIX>, source.c, build.log
    <root>/v1/locks/<key>.lock            flock() while a process compiles <key>
    <root>/v1/tmp/<random>/               in-progress builds (never loaded)
    <root>/v1/failed/<key>/               last failed build, kept for diagnostics
    <root>/v1/compilers/*.json            memoised compiler identities

``<key>`` hashes the generated C, compile configuration, Python ABI, platform,
architecture, Qiskit version and C-header digest, and the fastcircuit code
generator version. ``<compiler>`` hashes the compiler command, executable and
``--version``. Entries are published by renaming a fully written directory, and
validated (metadata and SHA-256 of the binary) before loading, so partial or
corrupt entries are never executed; they are discarded and rebuilt.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import platform
import shutil
import stat
import sys
import sysconfig
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import qiskit

from ._version import CODEGEN_VERSION, __version__
from .errors import CacheError

LAYOUT = "v1"
STALE_TMP_SECONDS = 24 * 3600


def _platform() -> str:
    return sys.platform  # a function so type checkers do not narrow per platform


def default_root() -> Path:
    env = os.environ.get("FASTCIRCUIT_CACHE_DIR")
    if env:
        return Path(env).expanduser()
    if _platform() == "darwin":
        return Path.home() / "Library" / "Caches" / "fastcircuit"
    xdg = os.environ.get("XDG_CACHE_HOME")
    return (Path(xdg) if xdg else Path.home() / ".cache") / "fastcircuit"


def enabled() -> bool:
    return os.environ.get("FASTCIRCUIT_NO_CACHE", "") not in ("1", "true", "yes")


def environment_fingerprint() -> dict[str, Any]:
    return {
        "fastcircuit": __version__,
        "codegen": CODEGEN_VERSION,
        "python": sys.version,
        "implementation": sys.implementation.cache_tag,
        "abiflags": getattr(sys, "abiflags", ""),
        "gil_disabled": bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
        "platform": sys.platform,
        "machine": platform.machine(),
        "qiskit": qiskit.__version__,
    }


def make_key(c_body: str, config: dict[str, Any]) -> str:
    payload = json.dumps(
        {"source": c_body, "config": config, "env": environment_fingerprint()},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_private_dir(path: Path) -> Path:
    """Create ``path`` (0700) and refuse directories other users could write."""
    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise CacheError(
            f"cannot create cache directory {path} ({exc.strerror}). Set "
            "FASTCIRCUIT_CACHE_DIR to a directory you can write, or FASTCIRCUIT_NO_CACHE=1."
        ) from None
    st = path.stat()
    if st.st_uid != os.getuid():
        raise CacheError(
            f"cache directory {path} is owned by another user; refusing to load code from it. "
            "Set FASTCIRCUIT_CACHE_DIR to a directory you own."
        )
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise CacheError(
            f"cache directory {path} is writable by other users; refusing to load code from "
            f"it. Run 'chmod go-w {path}' or set FASTCIRCUIT_CACHE_DIR."
        )
    return path


@dataclass(frozen=True)
class Entry:
    path: Path
    module: str
    binary: Path
    meta: dict[str, Any]


class Cache:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or default_root()) / LAYOUT

    # -- directories -----------------------------------------------------------
    def _dir(self, name: str) -> Path:
        ensure_private_dir(self.root.parent)
        ensure_private_dir(self.root)
        return ensure_private_dir(self.root / name)

    def compilers_dir(self) -> Path:
        return self._dir("compilers")

    # -- lookup ----------------------------------------------------------------
    def lookup(self, key: str, compiler: str | None) -> Entry | None:
        """Return a validated entry. ``compiler=None`` accepts any compiler's build."""
        base = self.root / "entries" / key
        if not base.is_dir():
            return None
        candidates = [base / compiler] if compiler else sorted(base.iterdir())
        for path in candidates:
            entry = self._validate(path, key)
            if entry is not None:
                return entry
        return None

    def _validate(self, path: Path, key: str) -> Entry | None:
        if not path.is_dir():
            return None
        try:
            meta = json.loads((path / "meta.json").read_text())
            module = meta["module"]
            binary = path / meta["binary"]
            ok = (
                meta.get("layout") == LAYOUT
                and meta.get("key") == key
                and meta.get("compiler") == path.name
                and binary.parent == path
                and _sha256_file(binary) == meta["sha256"]
            )
        except (OSError, ValueError, KeyError, TypeError):
            ok = False
        if not ok:
            self.discard(path)
            return None
        return Entry(path, module, binary, meta)

    def discard(self, path: Path) -> None:
        """Move a corrupt entry out of the way (atomic), then delete it."""
        try:
            trash = Path(tempfile.mkdtemp(prefix="discard-", dir=self._dir("tmp")))
            os.replace(path, trash / "entry")
            shutil.rmtree(trash, ignore_errors=True)
        except OSError:
            pass

    # -- build coordination ----------------------------------------------------
    @contextlib.contextmanager
    def lock(self, key: str) -> Iterator[None]:
        lock_path = self._dir("locks") / f"{key}.lock"
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def staging_dir(self) -> Path:
        tmp = self._dir("tmp")
        self._clean_stale(tmp)
        return Path(tempfile.mkdtemp(prefix="build-", dir=tmp))

    def _clean_stale(self, tmp: Path) -> None:
        now = time.time()
        for child in tmp.iterdir():
            try:
                if now - child.stat().st_mtime > STALE_TMP_SECONDS:
                    shutil.rmtree(child, ignore_errors=True)
            except OSError:
                pass

    def publish(
        self,
        staging: Path,
        key: str,
        compiler: str,
        module: str,
        binary: Path,
        extra: dict[str, Any],
    ) -> Entry:
        meta = {
            "layout": LAYOUT,
            "key": key,
            "compiler": compiler,
            "module": module,
            "binary": binary.name,
            "sha256": _sha256_file(binary),
            "created": time.time(),
            **extra,
        }
        meta_tmp = staging / "meta.json.tmp"
        with meta_tmp.open("w") as fh:
            json.dump(meta, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(meta_tmp, staging / "meta.json")
        target_parent = self._dir("entries") / key
        target_parent.mkdir(exist_ok=True, mode=0o700)
        target = target_parent / compiler
        try:
            os.rename(staging, target)  # atomic; fails if another process won
        except OSError:
            shutil.rmtree(staging, ignore_errors=True)
            existing = self._validate(target, key)
            if existing is None:
                raise
            return existing
        return Entry(target, module, target / binary.name, meta)

    def keep_failed(self, staging: Path, key: str) -> Path:
        failed = self._dir("failed") / key
        shutil.rmtree(failed, ignore_errors=True)
        try:
            os.replace(staging, failed)
        except OSError:
            return staging
        return failed

    # -- inspection ------------------------------------------------------------
    def entries(self) -> list[Entry]:
        out: list[Entry] = []
        base = self.root / "entries"
        if not base.is_dir():
            return out
        for key_dir in sorted(base.iterdir()):
            for comp_dir in sorted(key_dir.iterdir()) if key_dir.is_dir() else []:
                try:
                    meta = json.loads((comp_dir / "meta.json").read_text())
                    out.append(Entry(comp_dir, meta["module"], comp_dir / meta["binary"], meta))
                except (OSError, ValueError, KeyError):
                    continue
        return out

    def info(self) -> dict[str, Any]:
        size = 0
        files = 0
        if self.root.exists():
            for p in self.root.rglob("*"):
                if p.is_file():
                    files += 1
                    size += p.stat().st_size
        return {
            "path": str(self.root),
            "entries": len(self.entries()),
            "files": files,
            "bytes": size,
            "enabled": enabled(),
        }

    def clear(self) -> int:
        """Remove every entry; returns the number of entries removed."""
        count = len(self.entries())
        if self.root.exists():
            with contextlib.suppress(OSError):
                ensure_private_dir(self.root)
            trash = self.root.parent / f".{LAYOUT}-trash-{os.getpid()}-{time.time_ns()}"
            os.replace(self.root, trash)
            shutil.rmtree(trash, ignore_errors=True)
        return count
