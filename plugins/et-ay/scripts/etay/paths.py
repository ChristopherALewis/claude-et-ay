# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Christopher Lewis
"""Where et-ay keeps things.

Two locations are involved, and the split is deliberate:

* The data directory holds history and per-session state. Hooks receive it as
  ``CLAUDE_PLUGIN_DATA``, which Claude Code keeps across plugin updates and
  deletes when the plugin is uninstalled.
* The anchor directory, ``<claude config dir>/et-ay``, holds the status line
  shim, the user's config file and a pointer to the current plugin root and data
  directory. The status line command is not a hook, so it never receives the
  plugin variables, and the plugin root changes on every update. The anchor gives
  the status line a stable path to call and a way to find everything else.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from pathlib import Path
from typing import Optional

ANCHOR_NAME = "et-ay"


def now() -> float:
    """Current time, overridable with ET_AY_NOW so tests can drive the clock."""
    fake = os.environ.get("ET_AY_NOW")
    if fake:
        try:
            return float(fake)
        except ValueError:
            pass
    return time.time()


def claude_config_dir() -> Path:
    custom = os.environ.get("CLAUDE_CONFIG_DIR")
    if custom:
        return Path(custom).expanduser()
    return Path.home() / ".claude"


def settings_path() -> Path:
    return claude_config_dir() / "settings.json"


def anchor_dir() -> Path:
    return claude_config_dir() / ANCHOR_NAME


def plugin_root() -> Path:
    """The installed plugin directory (two levels above this file)."""
    env = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent.parent


def _read_pointer(name: str) -> Optional[Path]:
    try:
        text = (anchor_dir() / name).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return Path(text) if text else None


def data_dir(explicit: Optional[str] = None) -> Path:
    """Resolve the data directory.

    Order of preference: an explicit ``--data-dir`` argument, the
    ``CLAUDE_PLUGIN_DATA`` variable that hooks receive, the pointer the last
    SessionStart hook left in the anchor directory, and finally a fallback
    inside the anchor directory so the code never has nowhere to write.
    """
    for candidate in (explicit, os.environ.get("CLAUDE_PLUGIN_DATA")):
        # Skills substitute ${CLAUDE_PLUGIN_DATA} textually; an unsubstituted
        # placeholder means the variable was not available.
        if candidate and "${" not in candidate:
            return Path(candidate).expanduser()
    pointed = _read_pointer("data_dir")
    if pointed is not None:
        return pointed
    return anchor_dir() / "data"


def sessions_dir(data: Path) -> Path:
    return data / "sessions"


def history_path(data: Path) -> Path:
    return data / "history.jsonl"


def config_path() -> Path:
    return anchor_dir() / "config.json"


def wrapped_path() -> Path:
    return anchor_dir() / "wrapped.json"


def shim_path() -> Path:
    return anchor_dir() / "statusline.sh"


_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


def safe_name(value: str) -> str:
    """A filesystem-safe version of a session id."""
    cleaned = _SAFE.sub("-", value)[:96]
    return cleaned or "unknown"


def short_hash(value: str, length: int = 12) -> str:
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:length]


def write_pointers(root: Path, data: Path) -> None:
    """Record the current plugin root and data directory for the status line."""
    from .storage import atomic_write_text

    anchor = anchor_dir()
    anchor.mkdir(parents=True, exist_ok=True)
    for name, value in (("plugin_root", str(root)), ("data_dir", str(data))):
        target = anchor / name
        try:
            if target.read_text(encoding="utf-8").strip() == value:
                continue
        except OSError:
            pass
        atomic_write_text(target, value + "\n")
