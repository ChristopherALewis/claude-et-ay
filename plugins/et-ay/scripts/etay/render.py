# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Christopher Lewis
"""Turn session state into the status line segment."""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

from . import estimator

# ANSI styles. Kept to the basic 16 colours so every terminal theme copes.
_RESET = "\033[0m"
_STYLES = {
    "label": "\033[1;36m",
    "main": "\033[36m",
    "dim": "\033[2m",
    "warn": "\033[33m",
    "wait": "\033[35m",
    "good": "\033[32m",
}


# ---------------------------------------------------------------------------
# Duration formatting


def approx(seconds: float) -> str:
    """A deliberately coarse duration for estimates, which should not look precise."""
    s = max(0.0, float(seconds))
    if s < 10:
        return "<10s"
    if s < 55:
        return f"{round(s / 5.0) * 5}s"
    if s < 90:
        return "1m"
    if s < 3570:
        return f"{round(s / 60.0)}m"
    hours, rest = divmod(round(s / 60.0), 60)
    return f"{hours}h" if rest == 0 else f"{hours}h {rest}m"


def exact(seconds: float) -> str:
    """Elapsed time, shown precisely."""
    s = int(max(0.0, float(seconds)))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        minutes, secs = divmod(s, 60)
        return f"{minutes}m {secs:02d}s"
    hours, rest = divmod(s, 3600)
    return f"{hours}h {rest // 60:02d}m"


def span(low: float, high: float) -> str:
    a, b = approx(low), approx(high)
    if a == b:
        return a
    # "2m-6m" reads better as "2-6m" when both ends share a unit.
    for unit in ("m", "s"):
        if a.endswith(unit) and b.endswith(unit) and a[:-1].isdigit() and b[:-1].isdigit():
            return f"{a[:-1]}-{b}"
    return f"{a}-{b}"


# ---------------------------------------------------------------------------
# View model


def compute(state: Optional[Dict[str, Any]], now: float, cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(state, dict):
        return None
    turn = state.get("turn")
    if isinstance(turn, dict) and turn.get("status") == "running":
        return _running_view(turn, now)
    last = state.get("last")
    if cfg.get("idle") == "last" and isinstance(last, dict):
        limit = float(cfg.get("idle_seconds") or 0)
        if limit <= 0 or now - float(last.get("ended_at") or 0) <= limit:
            return {"mode": "idle", "last": last}
    return None


def _running_view(turn: Dict[str, Any], now: float) -> Dict[str, Any]:
    started = float(turn["started_at"])
    wall = max(0.0, now - started)
    waiting = float(turn.get("waiting_s") or 0.0)
    waiting_since = turn.get("waiting_since")
    if waiting_since is not None:
        waiting += max(0.0, now - float(waiting_since))
    active = max(0.0, wall - waiting)

    estimate = turn.get("estimate") or {}
    left = estimator.remaining(estimate.get("sample") or [], active)

    created = list(turn.get("tasks_created") or [])
    done = [t for t in (turn.get("tasks_completed") or []) if t in created]
    first_task_at = turn.get("first_task_at")
    if first_task_at is not None and created:
        left = estimator.blend_tasks(left, max(0.0, now - float(first_task_at)), len(created), len(done))

    joined = turn.get("joined") or []
    for item in joined:
        since = max(0.0, now - float(item.get("at") or now))
        for key in ("p10", "p50", "p90"):
            left[key] = float(left[key]) + max(0.0, float(item.get(key) or 0.0) - since)

    mode = "waiting" if waiting_since is not None else ("overdue" if left.get("overdue") else "running")
    return {
        "mode": mode,
        "wall": wall,
        "active": active,
        "left": left,
        "finish_at": now + float(left["p50"]),
        "confidence": estimate.get("confidence") or "learning",
        "tasks": (len(done), len(created)),
        "queued": len(joined),
    }


# ---------------------------------------------------------------------------
# Formatting


def _use_color(cfg: Dict[str, Any]) -> bool:
    return bool(cfg.get("color")) and "NO_COLOR" not in os.environ


def format_view(view: Optional[Dict[str, Any]], cfg: Dict[str, Any]) -> str:
    if view is None:
        return ""
    color = _use_color(cfg)
    ascii_only = bool(cfg.get("ascii"))
    sep = " | " if ascii_only else str(cfg.get("separator") or " · ")
    tilde = "~" if ascii_only else "≈"

    def paint(text: str, style: str) -> str:
        return f"{_STYLES[style]}{text}{_RESET}" if color and text else text

    label = str(cfg.get("label") or "")
    head = paint(label, "label") if label else ""

    if view["mode"] == "idle":
        last = view["last"]
        outcome = last.get("outcome")
        if outcome == "completed":
            body = f"last {exact(last.get('active_s') or 0)}"
            if last.get("p50") is not None:
                body += f" (est {approx(last['p50'])})"
        else:
            body = f"last turn {outcome}"
        return " ".join(part for part in (head, paint(body, "dim")) if part)

    segments: List[str] = list(cfg.get("segments") or [])
    left = view["left"]
    parts: List[str] = []
    for segment in segments:
        if segment == "left":
            if view["mode"] == "waiting":
                text = paint("paused, waiting for you", "wait")
            elif view["mode"] == "overdue":
                text = paint(f"running long, maybe {approx(left['p50'])} more", "warn")
            else:
                guess = "?" if view["confidence"] == "learning" else ""
                text = paint(f"~{approx(left['p50'])}{guess} left", "main")
            if "range" in segments and view["mode"] == "running":
                text += " " + paint(f"({span(left['p10'], left['p90'])})", "dim")
            parts.append(text)
        elif segment == "range":
            continue  # rendered alongside "left"
        elif segment == "clock" and view["mode"] == "running":
            when = time.strftime(str(cfg.get("clock_format") or "%H:%M"), time.localtime(view["finish_at"]))
            parts.append(paint(f"done {tilde}{when}", "dim"))
        elif segment == "elapsed":
            parts.append(paint(f"{exact(view['wall'])} in", "dim"))
        elif segment == "tasks" and view["tasks"][1] > 0:
            done, total = view["tasks"]
            parts.append(paint(f"{done}/{total} tasks", "good" if done == total else "dim"))
        elif segment == "queued" and view["queued"] > 0:
            parts.append(paint(f"+{view['queued']} queued", "dim"))
        elif segment == "confidence":
            parts.append(paint(f"{view['confidence']} confidence", "dim"))

    body = sep.join(parts)
    return " ".join(part for part in (head, body) if part)
