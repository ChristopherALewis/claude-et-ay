"""Shared test helpers: import path, an isolated environment and a hook runner."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

REPO = Path(__file__).resolve().parent.parent
PLUGIN = REPO / "plugins" / "et-ay"
SCRIPTS = PLUGIN / "scripts"
RUN = SCRIPTS / "run.sh"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

_ISOLATED_VARS = (
    "HOME",
    "CLAUDE_CONFIG_DIR",
    "CLAUDE_PLUGIN_DATA",
    "CLAUDE_PLUGIN_ROOT",
    "CLAUDE_PROJECT_DIR",
    "ET_AY_NOW",
    "NO_COLOR",
    "CLAUDE_EFFORT",
)


@contextlib.contextmanager
def isolated_env() -> Iterator[Dict[str, Path]]:
    """A throwaway home, Claude config dir and data dir, restored afterwards."""
    saved = {name: os.environ.get(name) for name in _ISOLATED_VARS}
    with tempfile.TemporaryDirectory(prefix="et-ay-test-") as tmp:
        root = Path(tmp)
        env = {
            "home": root / "home",
            "config": root / "home" / ".claude",
            "data": root / "data",
        }
        for path in env.values():
            path.mkdir(parents=True, exist_ok=True)
        os.environ["HOME"] = str(env["home"])
        os.environ["CLAUDE_CONFIG_DIR"] = str(env["config"])
        os.environ["CLAUDE_PLUGIN_DATA"] = str(env["data"])
        os.environ["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN)
        for name in ("CLAUDE_PROJECT_DIR", "ET_AY_NOW", "NO_COLOR", "CLAUDE_EFFORT"):
            os.environ.pop(name, None)
        try:
            yield env
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def run_cli(args, stdin: Any = "", now: Optional[float] = None, extra_env: Optional[Dict[str, str]] = None):
    """Run the real launcher in a subprocess, the way Claude Code does."""
    env = os.environ.copy()
    if now is not None:
        env["ET_AY_NOW"] = repr(float(now))
    if extra_env:
        env.update(extra_env)
    if not isinstance(stdin, (str, bytes)):
        stdin = json.dumps(stdin)
    if isinstance(stdin, str):
        stdin = stdin.encode("utf-8")
    return subprocess.run(
        ["sh", str(RUN), *args],
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=60,
    )


def hook(event: str, payload: Dict[str, Any], now: float):
    return run_cli(["hook", event], stdin=payload, now=now)
