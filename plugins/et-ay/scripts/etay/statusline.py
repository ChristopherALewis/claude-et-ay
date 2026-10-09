# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Christopher Lewis
"""The status line command.

Claude Code pipes session JSON to this on stdin and shows whatever it prints.
If the user already had a status line, ``setup`` saved it and this module runs
it first, with the same stdin, and combines the two outputs, so installing
et-ay never costs anyone their existing status line.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any, Dict, Optional

from . import config, paths, render, tracker
from .storage import atomic_write_json, locked, read_json


def _wrapped_command() -> Optional[str]:
    saved = read_json(paths.wrapped_path(), default=None)
    if not isinstance(saved, dict) or saved.get("wrap") is False:
        return None
    line = saved.get("statusLine")
    if isinstance(line, dict) and line.get("type", "command") == "command":
        command = line.get("command")
        if isinstance(command, str) and command.strip():
            return command
    return None


def run_wrapped(command: str, stdin_bytes: bytes, timeout: float) -> str:
    try:
        result = subprocess.run(
            command,
            shell=True,
            input=stdin_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            env=os.environ.copy(),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.decode("utf-8", "replace").rstrip("\n")


def combine(wrapped: str, ours: str, position: str, separator: str) -> str:
    if not wrapped:
        return ours
    if not ours:
        return wrapped
    if position == "line":
        return f"{wrapped}\n{ours}"
    lines = wrapped.split("\n")
    if position == "before":
        lines[0] = f"{ours}{separator}{lines[0]}"
    else:
        lines[-1] = f"{lines[-1]}{separator}{ours}"
    return "\n".join(lines)


def _remember_session_facts(data_dir, session_id: str, payload: Dict[str, Any]) -> None:
    """Keep the session's model and effort current for the next estimate.

    Hooks only see the model at session start and on a switch, while the status
    line sees it on every refresh, so it fills the gap. It writes only when a
    value actually changes.
    """
    model = (payload.get("model") or {}).get("id") if isinstance(payload.get("model"), dict) else None
    effort = (payload.get("effort") or {}).get("level") if isinstance(payload.get("effort"), dict) else None
    if not model and not effort:
        return
    state_file = paths.sessions_dir(data_dir) / f"{paths.safe_name(session_id)}.json"
    state = read_json(state_file, default=None)
    if not isinstance(state, dict):
        return
    if (not model or state.get("model") == model) and (not effort or state.get("effort") == effort):
        return
    with locked(paths.sessions_dir(data_dir) / f"{paths.safe_name(session_id)}.lock"):
        state = read_json(state_file, default=None)
        if not isinstance(state, dict):
            return
        if model:
            state["model"] = model
        if effort:
            state["effort"] = effort
        atomic_write_json(state_file, state)


def main(data_arg: Optional[str] = None) -> int:
    stdin_bytes = sys.stdin.buffer.read() if not sys.stdin.isatty() else b""
    try:
        payload = json.loads(stdin_bytes.decode("utf-8", "replace") or "{}")
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    cfg = config.load()
    ours = ""
    try:
        data_dir = paths.data_dir(data_arg)
        session_id = payload.get("session_id")
        if isinstance(session_id, str) and session_id:
            state = tracker.read_session(data_dir, session_id)
            ours = render.format_view(render.compute(state, paths.now(), cfg), cfg)
            try:
                _remember_session_facts(data_dir, session_id, payload)
            except OSError:
                pass
    except Exception:  # noqa: BLE001 - the status line must always render something
        ours = ""

    wrapped = ""
    command = _wrapped_command()
    # The guard stops a status line that somehow wraps et-ay from recursing.
    if command and not os.environ.get("ET_AY_IN_STATUSLINE"):
        os.environ["ET_AY_IN_STATUSLINE"] = "1"
        wrapped = run_wrapped(command, stdin_bytes, float(cfg.get("wrapped_timeout") or 3.0))

    separator = " | " if cfg.get("ascii") else str(cfg.get("separator") or " · ")
    output = combine(wrapped, ours, str(cfg.get("position") or "after"), separator)
    if output:
        sys.stdout.write(output + "\n")
    return 0
