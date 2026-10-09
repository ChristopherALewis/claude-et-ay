# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Christopher Lewis
"""Reports: accuracy statistics, a health check and CSV export."""

from __future__ import annotations

import csv
import io
import math
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from . import __version__, config, paths, settings, tracker
from .render import approx, exact
from .storage import read_json


def _median(values: Sequence[float]) -> Optional[float]:
    ordered = sorted(values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _quantile(values: Sequence[float], q: float) -> Optional[float]:
    ordered = sorted(values)
    if not ordered:
        return None
    position = q * (len(ordered) - 1)
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def accuracy(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """How well past estimates matched what actually happened."""
    ratios: List[float] = []
    inside = 0
    scored = 0
    for record in records:
        if record.get("outcome") != "completed" or int(record.get("prompts") or 1) != 1:
            continue
        est = record.get("est") or {}
        actual = record.get("active_s", record.get("duration_s"))
        p50, p10, p90 = est.get("p50"), est.get("p10"), est.get("p90")
        if not isinstance(actual, (int, float)) or not isinstance(p50, (int, float)) or actual <= 0 or p50 <= 0:
            continue
        scored += 1
        ratios.append(math.log(actual / p50))
        if isinstance(p10, (int, float)) and isinstance(p90, (int, float)) and p10 <= actual <= p90:
            inside += 1
    if not scored:
        return {"scored": 0}
    typical = _median([abs(r) for r in ratios]) or 0.0
    bias = _median(ratios) or 0.0
    return {
        "scored": scored,
        "typical_factor": math.exp(typical),
        "bias_factor": math.exp(bias),
        "coverage": inside / scored,
    }


def _describe_bias(factor: float) -> str:
    if 0.9 <= factor <= 1.1:
        return "no consistent lean"
    if factor > 1:
        return f"turns run {factor:.1f}x longer than the estimate, typically"
    return f"turns finish in {factor:.1f}x the estimated time, typically"


def stats(data: Path, recent: int = 10) -> str:
    cfg = config.load()
    records = tracker.load_history(data, max(int(cfg["history_window"]), 100000))
    if not records:
        return (
            "No turns recorded yet. et-ay learns from every prompt you send, so "
            "estimates start rough and improve after a few dozen turns."
        )
    lines: List[str] = []
    outcomes: Dict[str, int] = {}
    for record in records:
        outcomes[str(record.get("outcome"))] = outcomes.get(str(record.get("outcome")), 0) + 1
    completed = [r for r in records if r.get("outcome") == "completed"]
    actives = [float(r.get("active_s") or 0) for r in completed]
    lines.append(f"et-ay {__version__}: {len(records)} turns recorded")
    lines.append(
        "Outcomes: " + ", ".join(f"{count} {name}" for name, count in sorted(outcomes.items(), key=lambda x: -x[1]))
    )
    if actives:
        lines.append(
            f"Turn length: median {exact(_median(actives) or 0)}, "
            f"90th percentile {exact(_quantile(actives, 0.9) or 0)}, longest {exact(max(actives))}"
        )
    waiting = sum(float(r.get("waiting_s") or 0) for r in records)
    if waiting > 0:
        lines.append(f"Time spent waiting on you (excluded from estimates): {exact(waiting)}")

    overall = accuracy(records)
    lines.append("")
    if overall["scored"]:
        lines.append(f"Accuracy over {overall['scored']} scored turns:")
        lines.append(f"  typical miss: a factor of {overall['typical_factor']:.2f} either way")
        lines.append(f"  inside the shown range: {overall['coverage'] * 100:.0f}% (the range aims for 80%)")
        lines.append(f"  lean: {_describe_bias(overall['bias_factor'])}")
        last50 = accuracy(records[-50:])
        if last50["scored"] >= 10 and overall["scored"] > last50["scored"]:
            lines.append(
                f"  last {last50['scored']} turns: factor {last50['typical_factor']:.2f}, "
                f"{last50['coverage'] * 100:.0f}% inside the range"
            )
    else:
        lines.append("Accuracy: nothing to score yet (needs completed single-prompt turns).")

    kinds: Dict[str, List[float]] = {}
    for record in completed:
        kinds.setdefault(str((record.get("f") or {}).get("kind") or "?"), []).append(float(record.get("active_s") or 0))
    if kinds:
        lines.append("")
        lines.append("By kind of prompt:")
        for kind, values in sorted(kinds.items(), key=lambda item: -len(item[1])):
            lines.append(f"  {kind:<13} {len(values):>5} turns, median {exact(_median(values) or 0)}")

    commands: Dict[str, List[float]] = {}
    for record in completed:
        name = (record.get("f") or {}).get("command")
        if name:
            commands.setdefault(name, []).append(float(record.get("active_s") or 0))
    if commands:
        lines.append("")
        lines.append("Slash commands:")
        for name, values in sorted(commands.items(), key=lambda item: -len(item[1]))[:8]:
            lines.append(f"  /{name:<20} {len(values):>4} runs, median {exact(_median(values) or 0)}")

    lines.append("")
    lines.append(f"Last {min(recent, len(records))} turns:")
    lines.append(f"  {'when':<12} {'kind':<12} {'actual':>9} {'estimate':>10}  result")
    for record in records[-recent:]:
        when = time.strftime("%d %b %H:%M", time.localtime(float(record.get("started_at") or 0)))
        kind = str((record.get("f") or {}).get("kind") or "?")
        est = record.get("est") or {}
        actual = float(record.get("active_s") or 0)
        estimate = approx(est["p50"]) if isinstance(est.get("p50"), (int, float)) else "-"
        if record.get("outcome") != "completed":
            result = str(record.get("outcome"))
        elif isinstance(est.get("p10"), (int, float)) and isinstance(est.get("p90"), (int, float)):
            result = (
                "in range" if est["p10"] <= actual <= est["p90"] else ("longer" if actual > est["p90"] else "shorter")
            )
        else:
            result = "-"
        if int(record.get("prompts") or 1) > 1:
            result += f" (+{int(record['prompts']) - 1} queued)"
        lines.append(f"  {when:<12} {kind:<12} {exact(actual):>9} {estimate:>10}  {result}")
    return "\n".join(lines)


def _ago(timestamp: Optional[float]) -> str:
    if not timestamp:
        return "never"
    return f"{exact(paths.now() - float(timestamp))} ago"


def doctor(data: Path) -> str:
    ok, warn = "[ok]", "[!!]"
    lines = [f"et-ay {__version__} health check", ""]
    version_ok = sys.version_info >= (3, 9)
    lines.append(f"{ok if version_ok else warn} Python {platform.python_version()} at {sys.executable}")
    lines.append(f"{ok} Platform: {platform.system()} {platform.release()}")

    root = paths.plugin_root()
    lines.append(f"{ok if (root / 'scripts' / 'run.sh').exists() else warn} Plugin root: {root}")
    lines.append(f"{ok} Data directory: {data}")

    pointer = paths.anchor_dir() / "plugin_root"
    try:
        pointed = Path(pointer.read_text(encoding="utf-8").strip())
    except OSError:
        pointed = None
    if pointed is None:
        lines.append(f"{warn} No plugin pointer yet. It is written at session start; start a new session.")
    elif not (pointed / "scripts" / "run.sh").exists():
        lines.append(f"{warn} Plugin pointer is stale ({pointed}). Start a new session to refresh it.")
    else:
        lines.append(f"{ok} Plugin pointer: {pointed}")

    state = settings.status()
    if state["parse_error"]:
        lines.append(f"{warn} {state['parse_error']}")
    elif state["installed"]:
        refresh = (state["status_line"] or {}).get("refreshInterval")
        lines.append(f"{ok} Status line installed in {state['settings_file']} (refresh every {refresh}s)")
    elif state["status_line"]:
        lines.append(f"{warn} A different status line is configured. Run /et-ay:setup to add et-ay to it.")
    else:
        lines.append(f"{warn} No status line configured. Run /et-ay:setup.")
    if state["installed"] and not state["shim_executable"]:
        lines.append(f"{warn} Shim missing or not executable at {paths.shim_path()}. Run /et-ay:setup again.")
    if state["disable_all_hooks"]:
        lines.append(f"{warn} disableAllHooks is on in your settings, so et-ay's hooks and status line cannot run.")
    wrapped = state.get("wrapped") or {}
    if isinstance(wrapped, dict) and isinstance(wrapped.get("statusLine"), dict):
        mode = "wrapped" if wrapped.get("wrap", True) else "saved, not shown"
        lines.append(f"{ok} Previous status line {mode}: {wrapped['statusLine'].get('command')}")

    beats = read_json(data / "heartbeat.json", default={}) or {}
    lines.append("")
    lines.append("Last hook activity:")
    for event in ("session-start", "prompt", "tool", "task-created", "stop", "stop-failure", "session-end"):
        lines.append(f"  {event:<14} {_ago(beats.get(event))}")
    if not beats:
        lines.append(f"{warn} No hook has fired yet. Check that the plugin is enabled in /plugin.")

    history = tracker.load_history(data, 1000000)
    lines.append("")
    lines.append(f"{ok} {len(history)} turns in history")
    errors = data / "errors.log"
    if errors.exists() and errors.stat().st_size > 0:
        tail = errors.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-3:]
        lines.append(f"{warn} Recent errors in {errors}:")
        lines.extend(f"    {line[:200]}" for line in tail)
    cfg = config.load()
    if cfg.get("debug"):
        lines.append(f"{ok} Debug logging is on: {data / 'debug.jsonl'}")
    if os.environ.get("NO_COLOR") is not None:
        lines.append(f"{ok} NO_COLOR is set, so the status line is uncoloured.")
    return "\n".join(lines)


EXPORT_FIELDS = [
    "turn_id",
    "started_at",
    "ended_at",
    "outcome",
    "duration_s",
    "active_s",
    "waiting_s",
    "prompts",
    "kind",
    "chars",
    "words",
    "command",
    "project",
    "model",
    "effort",
    "tools",
    "tool_s",
    "tasks_created",
    "tasks_completed",
    "est_p10",
    "est_p50",
    "est_p90",
    "est_n_eff",
]


def export_csv(data: Path) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_FIELDS, lineterminator="\n")
    writer.writeheader()
    for record in tracker.load_history(data, 10**9):
        features = record.get("f") or {}
        est = record.get("est") or {}
        row = {key: record.get(key) for key in EXPORT_FIELDS if key in record}
        row.update(
            {
                "kind": features.get("kind"),
                "chars": features.get("chars"),
                "words": features.get("words"),
                "command": features.get("command"),
                "est_p10": est.get("p10"),
                "est_p50": est.get("p50"),
                "est_p90": est.get("p90"),
                "est_n_eff": est.get("n_eff"),
            }
        )
        writer.writerow(row)
    return buffer.getvalue()
