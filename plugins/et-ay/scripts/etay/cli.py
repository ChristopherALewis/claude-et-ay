# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Christopher Lewis
"""Command-line interface. Hooks, the status line and the slash commands all land here."""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__, config, paths, report, settings, statusline, tracker
from .storage import append_capped, atomic_write_json, locked, read_json

HOOK_EVENTS = (
    "session-start",
    "prompt",
    "tool",
    "wait",
    "task-created",
    "task-completed",
    "stop",
    "stop-failure",
    "session-end",
    "model",
)

# Fields copied into the debug log as they are. Everything else is either reduced
# to its length and a hash (free text) or left out entirely, so a new payload
# field can never leak content into the log by default.
_DEBUG_KEEP = frozenset(
    (
        "session_id",
        "prompt_id",
        "hook_event_name",
        "permission_mode",
        "effort",
        "source",
        "reason",
        "model",
        "from_model",
        "to_model",
        "tool_name",
        "tool_use_id",
        "duration_ms",
        "task_id",
        "stop_hook_active",
        "agent_id",
        "agent_type",
        "is_interrupt",
    )
)
_DEBUG_HASH = frozenset(("prompt", "last_assistant_message", "error", "task_subject", "task_description"))


def _redact(payload: Dict[str, Any]) -> Dict[str, Any]:
    clean: Dict[str, Any] = {}
    for key, value in payload.items():
        if key in _DEBUG_KEEP:
            clean[key] = value
        elif key in _DEBUG_HASH and isinstance(value, str):
            clean[key] = {"chars": len(value), "sha256": paths.short_hash(value)}
        elif isinstance(value, list):
            clean[key] = {"items": len(value)}
        else:
            clean[key] = "<omitted>"
    return clean


def _heartbeat(data: Path, event: str, now: float) -> None:
    beat_file = data / "heartbeat.json"
    with locked(data / "heartbeat.lock"):
        beats = read_json(beat_file, default={}) or {}
        beats[event] = now
        atomic_write_json(beat_file, beats)


def run_hook(event: str, data_arg: Optional[str]) -> int:
    """Handle one hook event. Never prints, never fails.

    Anything written to stdout by a UserPromptSubmit or SessionStart hook is fed
    to Claude as context, so this function must stay silent. Errors go to
    errors.log in the data directory instead.
    """
    data = paths.data_dir(data_arg)
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            return 0
        now = paths.now()
        cfg = config.load()
        data.mkdir(parents=True, exist_ok=True)
        if cfg.get("debug"):
            record = {"t": now, "event": event, "payload": _redact(payload)}
            append_capped(data / "debug.jsonl", json.dumps(record, ensure_ascii=False), 2 * 1024 * 1024)
        if event == "session-start":
            paths.write_pointers(paths.plugin_root(), data)
            tracker.housekeeping(data, now)
        tracker.Tracker(data, cfg, now).handle(event, payload)
        _heartbeat(data, event, now)
    except Exception:  # noqa: BLE001 - a hook must never disrupt Claude Code
        try:
            append_capped(data / "errors.log", f"[{event}] " + traceback.format_exc().strip(), 256 * 1024)
        except Exception:  # noqa: BLE001 - nowhere left to report to
            pass
    return 0


def _print(text: str) -> None:
    sys.stdout.write(text.rstrip("\n") + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="et-ay", description="Estimated time to completion for Claude Code.")
    parser.add_argument("--data-dir", help="data directory (defaults to the plugin data directory)")
    sub = parser.add_subparsers(dest="command")

    hook = sub.add_parser("hook", help="handle a Claude Code hook event (reads JSON on stdin)")
    hook.add_argument("event", choices=HOOK_EVENTS)

    sub.add_parser("statusline", help="render the status line (reads JSON on stdin)")

    setup = sub.add_parser("setup", help="add et-ay to your Claude Code status line")
    setup.add_argument("--refresh", type=int, default=1, help="refresh interval in seconds (default 1)")
    setup.add_argument("--position", choices=("after", "before", "line"), help="where to put et-ay's segment")
    setup.add_argument("--no-wrap", action="store_true", help="replace an existing status line instead of extending it")

    remove = sub.add_parser("remove", help="restore the status line you had before et-ay")
    remove.add_argument("--purge", action="store_true", help="also delete the shim and config")

    stats = sub.add_parser("stats", help="show history and estimate accuracy")
    stats.add_argument("--recent", type=int, default=10, help="how many recent turns to list")

    sub.add_parser("doctor", help="check that everything is wired up")

    export = sub.add_parser("export", help="export history as CSV")
    export.add_argument("--output", help="file to write (default: stdout)")

    reset = sub.add_parser("reset", help="delete recorded history")
    reset.add_argument("--yes", action="store_true", help="confirm deletion")

    cfg = sub.add_parser("config", help="show or change settings")
    cfg_sub = cfg.add_subparsers(dest="action")
    cfg_sub.add_parser("show")
    cfg_set = cfg_sub.add_parser("set")
    cfg_set.add_argument("key")
    cfg_set.add_argument("value", help="JSON value, or a bare string")
    cfg_unset = cfg_sub.add_parser("unset")
    cfg_unset.add_argument("key")

    sub.add_parser("version", help="print the version")
    return parser


def _config_command(args: argparse.Namespace) -> int:
    action = args.action or "show"
    if action == "show":
        _print(f"Settings file: {paths.config_path()}")
        for key, value, custom in config.describe():
            marker = "*" if custom else " "
            _print(f" {marker} {key:<16} {json.dumps(value, ensure_ascii=False)}")
        _print("(* = changed from the default)")
        return 0
    try:
        if action == "set":
            value = config.set_value(args.key, args.value)
            _print(f"{args.key} = {json.dumps(value, ensure_ascii=False)}")
        elif action == "unset":
            config.unset_value(args.key)
            _print(f"{args.key} reset to its default")
    except ValueError as error:
        _print(f"Error: {error}")
        return 2
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    command = args.command
    if command == "hook":
        return run_hook(args.event, args.data_dir)
    if command == "statusline":
        return statusline.main(args.data_dir)

    data = paths.data_dir(args.data_dir)
    if command == "setup":
        try:
            notes = settings.setup(
                paths.plugin_root(), data, refresh=args.refresh, position=args.position, wrap=not args.no_wrap
            )
        except settings.SetupError as error:
            _print(f"Setup stopped: {error}")
            return 1
        for note in notes:
            _print(f"- {note}")
        _print("The status line picks this up on its next refresh; no restart is needed.")
        return 0
    if command == "remove":
        try:
            notes = settings.remove(purge=args.purge)
        except settings.SetupError as error:
            _print(f"Remove stopped: {error}")
            return 1
        for note in notes:
            _print(f"- {note}")
        return 0
    if command == "stats":
        _print(report.stats(data, recent=max(1, args.recent)))
        return 0
    if command == "doctor":
        _print(report.doctor(data))
        return 0
    if command == "export":
        text = report.export_csv(data)
        if args.output:
            Path(args.output).expanduser().write_text(text, encoding="utf-8")
            _print(f"Wrote {args.output}")
        else:
            sys.stdout.write(text)
        return 0
    if command == "reset":
        if not args.yes:
            _print("This deletes all recorded turns. Run again with --yes to confirm.")
            return 1
        removed = 0
        with locked(data / "history.lock"):
            for name in ("history.jsonl", "history.jsonl.1"):
                target = data / name
                if target.exists():
                    target.unlink()
                    removed += 1
        _print("History deleted." if removed else "There was no history to delete.")
        return 0
    if command == "config":
        return _config_command(args)
    if command == "version":
        _print(__version__)
        return 0
    build_parser().print_help()
    return 0
