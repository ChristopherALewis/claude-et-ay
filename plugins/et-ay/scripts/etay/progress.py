# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Christopher Lewis
"""Progress reported by background jobs through a file.

The session-start hook exports ``ET_AY_PROGRESS``, a per-session file path,
through ``CLAUDE_ENV_FILE``, so every Bash command Claude runs later in the
session can see it. A script reports progress by overwriting the file with one
line: ``3/7 Building``, ``42% Uploading`` or just a label. The status line reads
the first line and uses the file's mtime as the time of the last update.

The file is written by arbitrary scripts, so everything read from it is treated
as untrusted: only the first line is used, control characters and ANSI escapes
are removed so nothing can reach the terminal, and the text is capped.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional

from . import paths

ENV_VAR = "ET_AY_PROGRESS"
MAX_CHARS = 200
# Read no more than this much of a file a script may have filled with anything.
_READ_BYTES = 4096

# CSI and OSC sequences first, then any other two-byte escape.
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b[@-_]?")
# Control characters, line and paragraph separators, and bytes that failed to decode.
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029\ufffd]")
_COUNT = re.compile(r"(\d{1,9})\s*/\s*(\d{1,9})(?:\s+(.*))?")
_PERCENT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%(?:\s+(.*))?")


def progress_dir(data: Path) -> Path:
    return data / "progress"


def file_for(data: Path, session_id: str) -> Path:
    return progress_dir(data) / paths.safe_name(session_id)


def clean(text: str) -> str:
    """Strip escapes and control characters, collapse whitespace and cap the length."""
    text = _ANSI.sub("", text).replace("\t", " ")
    text = _CONTROL.sub("", text)
    return " ".join(text.split())[:MAX_CHARS].strip()


def parse(raw: str) -> Optional[Dict[str, Any]]:
    """Interpret one progress line, or return None if nothing usable is left.

    The result has ``fraction`` (0 to 1, or None for a bare label), ``count``
    (the ``3/7`` or ``42%`` text as shown) and ``label``.
    """
    first = raw.split("\n", 1)[0].split("\r", 1)[0]
    line = clean(first)
    if not line:
        return None
    match = _COUNT.fullmatch(line)
    if match and int(match.group(2)) > 0:
        done, total = int(match.group(1)), int(match.group(2))
        return {"fraction": min(1.0, done / total), "count": f"{done}/{total}", "label": match.group(3) or ""}
    match = _PERCENT.fullmatch(line)
    if match and float(match.group(1)) <= 100:
        value = float(match.group(1))
        return {"fraction": value / 100.0, "count": f"{value:g}%", "label": match.group(2) or ""}
    return {"fraction": None, "count": "", "label": line}


def read(data: Path, session_id: str) -> Optional[Dict[str, Any]]:
    """The session's current progress report with its ``updated_at`` time, if any."""
    target = file_for(data, session_id)
    try:
        with open(target, "rb") as handle:
            raw = handle.read(_READ_BYTES)
            updated_at = target.stat().st_mtime
    except OSError:
        return None
    report = parse(raw.decode("utf-8", "replace"))
    if report is not None:
        report["updated_at"] = updated_at
    return report


def remove(data: Path, session_id: str) -> None:
    try:
        file_for(data, session_id).unlink()
    except OSError:
        pass


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def export_line(data: Path, session_id: str) -> str:
    return f"export {ENV_VAR}={_shell_quote(str(file_for(data, session_id)))}"


def export(env_file: str, data: Path, session_id: str) -> None:
    """Add the export line to Claude Code's env file once, and create the directory.

    SessionStart fires again after compaction and on resume, so the line is
    only appended when it is not already present.
    """
    progress_dir(data).mkdir(parents=True, exist_ok=True)
    line = export_line(data, session_id)
    target = Path(env_file)
    try:
        existing = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = ""
    if line in existing.splitlines():
        return
    prefix = "\n" if existing and not existing.endswith("\n") else ""
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(prefix + line + "\n")
