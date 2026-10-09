# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Christopher Lewis
"""Small, dependency-free file helpers: atomic writes, locks and JSON lines.

Several hooks can run at once (tool hooks run asynchronously and in parallel),
so every read-modify-write of shared state goes through ``locked``.
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

try:  # POSIX
    import fcntl  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

try:  # Windows
    import msvcrt  # type: ignore[import-not-found]
except ImportError:
    msvcrt = None  # type: ignore[assignment]


def atomic_write_text(path: Path, text: str) -> None:
    """Write via a temporary file and rename, so readers never see half a file.

    A symlink is followed and its target rewritten, so a settings file managed
    from a dotfiles repository stays linked. An existing file keeps its
    permissions; a new one gets the usual umask-based mode.
    """
    path = Path(os.path.realpath(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        umask = os.umask(0)
        os.umask(umask)
        mode = 0o666 & ~umask
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def atomic_write_json(path: Path, data: Any, indent: Optional[int] = None) -> None:
    text = json.dumps(data, indent=indent, ensure_ascii=False, sort_keys=False)
    atomic_write_text(path, text + "\n")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


@contextlib.contextmanager
def locked(lock_file: Path) -> Iterator[None]:
    """Hold an exclusive advisory lock for the duration of the block.

    Falls back to no locking on platforms without fcntl or msvcrt; writes are
    still atomic, so the worst case is a lost counter update, never a corrupt
    file.
    """
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_file, "a+")  # noqa: SIM115 - closed in the finally block below
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        elif msvcrt is not None:  # pragma: no cover - Windows
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        yield
    finally:
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:  # pragma: no cover - Windows
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            handle.close()


def append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)


def read_jsonl(path: Path, max_bytes: Optional[int] = None) -> List[Dict[str, Any]]:
    """Read a JSON-lines file, skipping blank or damaged lines.

    With ``max_bytes`` only the tail of the file is read, which keeps the
    prompt hook fast however long the history grows.
    """
    try:
        with open(path, "rb") as handle:
            if max_bytes is not None:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                start = max(0, size - max_bytes)
                handle.seek(start)
                raw = handle.read()
                if start > 0:
                    newline = raw.find(b"\n")
                    raw = raw[newline + 1 :] if newline >= 0 else b""
            else:
                raw = handle.read()
    except OSError:
        return []
    records = []
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def append_capped(path: Path, text: str, cap_bytes: int) -> None:
    """Append to a log file, rotating it to ``<name>.1`` once it passes the cap."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.stat().st_size > cap_bytes:
            os.replace(path, path.with_name(path.name + ".1"))
    except OSError:
        pass
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(text if text.endswith("\n") else text + "\n")
