# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Christopher Lewis
"""User configuration, stored as JSON in the anchor directory.

Every key has a default, so the file is optional and a damaged or partial file
never stops the status line from rendering.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from . import paths
from .storage import atomic_write_json, read_json

SEGMENTS = ("left", "range", "clock", "elapsed", "tasks", "queued", "confidence")

DEFAULTS: Dict[str, Any] = {
    # What the status line shows while a turn is running, in order.
    "segments": ["left", "range", "clock", "elapsed", "tasks", "queued"],
    # Text placed before the segments.
    "label": "ETA",
    # Separator between segments.
    "separator": " · ",
    # strftime format for the finish-time segment. British 24-hour clock by default.
    "clock_format": "%H:%M",
    # ANSI colours. NO_COLOR in the environment always wins.
    "color": True,
    # Restrict output to ASCII (swaps the separator and the approximation sign).
    "ascii": False,
    # When idle: "last" shows the previous turn's actual time against its estimate,
    # "none" shows nothing.
    "idle": "last",
    # How long after a turn ends the idle summary stays visible, in seconds (0 = forever).
    "idle_seconds": 900,
    # Where et-ay's segment goes relative to a wrapped status line: after, before or line.
    "position": "after",
    # Seconds to wait for a wrapped status line command before giving up on it.
    "wrapped_timeout": 3.0,
    # Estimator tuning.
    "half_life_days": 45.0,
    "history_window": 3000,
    "prior_strength": 3.0,
    # A turn left open longer than this without activity is treated as abandoned.
    "stale_minutes": 120,
    # Write redacted hook payloads to debug.jsonl in the data directory.
    "debug": False,
}

_TYPES: Dict[str, Tuple[type, ...]] = {
    "segments": (list,),
    "label": (str,),
    "separator": (str,),
    "clock_format": (str,),
    "color": (bool,),
    "ascii": (bool,),
    "idle": (str,),
    "idle_seconds": (int, float),
    "position": (str,),
    "wrapped_timeout": (int, float),
    "half_life_days": (int, float),
    "history_window": (int,),
    "prior_strength": (int, float),
    "stale_minutes": (int, float),
    "debug": (bool,),
}

_CHOICES = {"idle": ("last", "none"), "position": ("after", "before", "line")}


def validate(key: str, value: Any) -> Any:
    """Return ``value`` if it is acceptable for ``key``, otherwise raise ValueError."""
    if key not in DEFAULTS:
        raise ValueError(f"unknown setting {key!r}; known settings: {', '.join(sorted(DEFAULTS))}")
    expected = _TYPES[key]
    if isinstance(value, bool) and bool not in expected:
        raise ValueError(f"{key} must be a number, not true/false")
    if not isinstance(value, expected):
        names = " or ".join(t.__name__ for t in expected)
        raise ValueError(f"{key} must be {names}")
    if key in _CHOICES and value not in _CHOICES[key]:
        raise ValueError(f"{key} must be one of {', '.join(_CHOICES[key])}")
    if key == "segments":
        unknown = [s for s in value if s not in SEGMENTS]
        if unknown:
            raise ValueError(f"unknown segments {unknown}; choose from {', '.join(SEGMENTS)}")
    if key in ("half_life_days", "history_window", "wrapped_timeout", "stale_minutes", "prior_strength") and value <= 0:
        raise ValueError(f"{key} must be greater than zero")
    if key == "idle_seconds" and value < 0:
        raise ValueError("idle_seconds cannot be negative (0 means forever)")
    return value


def load() -> Dict[str, Any]:
    merged = dict(DEFAULTS)
    stored = read_json(paths.config_path(), default={})
    if isinstance(stored, dict):
        for key, value in stored.items():
            try:
                merged[key] = validate(key, value)
            except ValueError:
                continue
    return merged


def stored() -> Dict[str, Any]:
    value = read_json(paths.config_path(), default={})
    return value if isinstance(value, dict) else {}


def parse_value(raw: str) -> Any:
    """Interpret a command-line value: JSON if it parses, otherwise a plain string."""
    import json

    try:
        return json.loads(raw)
    except ValueError:
        return raw


def set_value(key: str, raw: str) -> Any:
    value = validate(key, parse_value(raw))
    current = stored()
    current[key] = value
    atomic_write_json(paths.config_path(), current, indent=2)
    return value


def unset_value(key: str) -> None:
    if key not in DEFAULTS:
        raise ValueError(f"unknown setting {key!r}")
    current = stored()
    current.pop(key, None)
    atomic_write_json(paths.config_path(), current, indent=2)


def describe() -> List[Tuple[str, Any, bool]]:
    """(key, effective value, is_custom) for every setting."""
    custom = stored()
    effective = load()
    return [(key, effective[key], key in custom) for key in DEFAULTS]
