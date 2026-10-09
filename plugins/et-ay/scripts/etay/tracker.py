# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Christopher Lewis
"""The turn tracker: a small state machine driven by Claude Code hook events.

One JSON file per session holds the turn in progress. Completed turns are
appended to ``history.jsonl``, which is what the estimator learns from.

Behaviour this relies on, observed on Claude Code 2.1.295:

* ``UserPromptSubmit`` carries a ``prompt_id``. Every later event in the same
  turn carries the same id.
* A message the user queues while Claude is busy is handed to Claude after the
  current tool calls finish, inside the same turn. ``UserPromptSubmit`` fires at
  that moment with the *same* ``prompt_id`` as the running turn, and the turn
  ends with a single ``Stop``. The tracker records it as a joined prompt.
* An interrupted turn (Esc) gets no ``Stop``. The next ``UserPromptSubmit``
  arrives with a *new* ``prompt_id``, which is how the tracker knows the
  previous turn was cut short.
* Hooks cannot see a queued message until Claude Code hands it over, so the
  queue itself is invisible until then.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import estimator, features, paths, transcript
from .storage import append_jsonl, atomic_write_json, atomic_write_text, locked, read_json, read_jsonl

STATE_VERSION = 1
RECORD_VERSION = 1
# Average bytes per history line, used to read only as much of the file as the
# training window needs.
_BYTES_PER_RECORD = 900
# A second Stop arriving this long after the first means another hook kept Claude going.
_REOPEN_GRACE_S = 3.0


def _effort_from(payload: Dict[str, Any]) -> Optional[str]:
    effort = payload.get("effort")
    if isinstance(effort, dict) and isinstance(effort.get("level"), str):
        return effort["level"]
    return None


def _project_key(payload: Dict[str, Any]) -> Optional[str]:
    root = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd")
    return paths.short_hash(str(root)) if root else None


def load_history(data: Path, window: int) -> List[Dict[str, Any]]:
    """Recent history records, latest version of each turn only."""
    records = read_jsonl(paths.history_path(data), max_bytes=max(window, 1) * _BYTES_PER_RECORD)
    latest: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for record in records:
        key = str(record.get("turn_id") or id(record))
        if key not in latest:
            order.append(key)
        latest[key] = record
    deduped = [latest[key] for key in order]
    return deduped[-window:]


class Tracker:
    def __init__(self, data: Path, cfg: Dict[str, Any], now: float) -> None:
        self.data = data
        self.cfg = cfg
        self.now = now

    # -- persistence ---------------------------------------------------------

    def _state_file(self, session_id: str) -> Path:
        return paths.sessions_dir(self.data) / f"{paths.safe_name(session_id)}.json"

    def _lock_file(self, session_id: str) -> Path:
        return paths.sessions_dir(self.data) / f"{paths.safe_name(session_id)}.lock"

    def _load(self, session_id: str) -> Dict[str, Any]:
        state = read_json(self._state_file(session_id), default=None)
        if not isinstance(state, dict) or state.get("v") != STATE_VERSION:
            state = {"v": STATE_VERSION, "session_id": session_id, "turn": None, "last": None}
        return state

    def _save(self, session_id: str, state: Dict[str, Any]) -> None:
        state["updated_at"] = self.now
        atomic_write_json(self._state_file(session_id), state)

    # -- entry point -----------------------------------------------------------

    def handle(self, event: str, payload: Dict[str, Any]) -> None:
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            return
        handler = getattr(self, "_on_" + event.replace("-", "_"), None)
        if handler is None:
            return
        with locked(self._lock_file(session_id)):
            existed = self._state_file(session_id).exists()
            state = self._load(session_id)
            changed = handler(state, payload) is not False
            # A late asynchronous event must not recreate a session that has ended.
            if changed and (existed or event in ("prompt", "session-start")):
                self._save(session_id, state)
        if event == "session-end":
            self._forget(session_id)

    # -- helpers -------------------------------------------------------------

    def _running(self, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        turn = state.get("turn")
        if isinstance(turn, dict) and turn.get("status") == "running":
            return turn
        return None

    @staticmethod
    def _matches(turn: Dict[str, Any], payload: Dict[str, Any]) -> bool:
        pid = payload.get("prompt_id")
        return not pid or not turn.get("prompt_id") or pid == turn.get("prompt_id")

    def _end_wait(self, turn: Dict[str, Any], at: float) -> None:
        since = turn.get("waiting_since")
        if since is not None:
            turn["waiting_s"] = round(float(turn.get("waiting_s") or 0.0) + max(0.0, at - float(since)), 3)
            turn["waiting_since"] = None

    def _touch(self, turn: Dict[str, Any]) -> None:
        turn["last_activity"] = max(float(turn.get("last_activity") or 0.0), self.now)

    def _current_context(self, state: Dict[str, Any], payload: Dict[str, Any], feats: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "f": feats,
            "project": _project_key(payload),
            "model": state.get("model"),
            "effort": _effort_from(payload) or state.get("effort") or os.environ.get("CLAUDE_EFFORT") or None,
        }

    def _estimate(self, context: Dict[str, Any]) -> Dict[str, Any]:
        history = load_history(self.data, int(self.cfg["history_window"]))
        return estimator.estimate(
            context,
            history,
            now=self.now,
            half_life_days=float(self.cfg["half_life_days"]),
            prior_strength=float(self.cfg["prior_strength"]),
        )

    def _finish(self, state: Dict[str, Any], outcome: str, ended_at: float, payload: Dict[str, Any]) -> None:
        turn = state["turn"]
        ended_at = max(ended_at, float(turn["started_at"]))
        self._end_wait(turn, ended_at)
        duration = ended_at - float(turn["started_at"])
        waiting = min(float(turn.get("waiting_s") or 0.0), duration)
        created = turn.get("tasks_created") or []
        completed = turn.get("tasks_completed") or []
        estimate = turn.get("estimate") or {}
        record = {
            "v": RECORD_VERSION,
            "turn_id": turn.get("turn_id"),
            "session": paths.short_hash(str(state.get("session_id"))),
            "project": turn.get("project"),
            "started_at": round(float(turn["started_at"]), 3),
            "ended_at": round(ended_at, 3),
            "duration_s": round(duration, 3),
            "active_s": round(duration - waiting, 3),
            "waiting_s": round(waiting, 3),
            "outcome": outcome,
            "prompts": 1 + len(turn.get("joined") or []),
            "f": turn.get("features") or {},
            "model": turn.get("model") or state.get("model"),
            "effort": turn.get("effort") or state.get("effort"),
            "permission_mode": turn.get("permission_mode"),
            "tools": int(turn.get("tools") or 0),
            "tool_s": round(float(turn.get("tool_ms") or 0) / 1000.0, 3),
            "tasks_created": len(created),
            "tasks_completed": len([t for t in completed if t in created]),
            "est": {k: estimate.get(k) for k in ("p10", "p50", "p90", "n_eff", "basis")},
        }
        if turn.get("reopened"):
            record["reopened"] = True
        with locked(self.data / "history.lock"):
            append_jsonl(paths.history_path(self.data), record)
        turn["status"] = "done"
        turn["outcome"] = outcome
        turn["ended_at"] = ended_at
        state["last"] = {
            "turn_id": turn.get("turn_id"),
            "outcome": outcome,
            "ended_at": ended_at,
            "active_s": record["active_s"],
            "p10": estimate.get("p10"),
            "p50": estimate.get("p50"),
            "p90": estimate.get("p90"),
            "prompts": record["prompts"],
        }

    def _forget(self, session_id: str) -> None:
        # The lock file stays: a late asynchronous hook may be waiting on it.
        # Housekeeping removes it once it is old.
        try:
            self._state_file(session_id).unlink()
        except OSError:
            pass

    # -- events --------------------------------------------------------------

    def _on_session_start(self, state: Dict[str, Any], payload: Dict[str, Any]) -> None:
        if isinstance(payload.get("model"), str):
            state["model"] = payload["model"]
        turn = self._running(state)
        # Compaction can happen mid-turn, so only a fresh start or a resume closes
        # a turn that was left open (for example by a crash or a closed terminal).
        if turn is not None and payload.get("source") in ("startup", "resume", "clear", "fork"):
            self._finish(state, "abandoned", float(turn.get("last_activity") or turn["started_at"]), payload)

    def _on_prompt(self, state: Dict[str, Any], payload: Dict[str, Any]) -> None:
        feats = features.extract(str(payload.get("prompt") or ""))
        pid = payload.get("prompt_id") if isinstance(payload.get("prompt_id"), str) else None
        turn = self._running(state)
        if turn is not None:
            idle = self.now - float(turn.get("last_activity") or turn["started_at"])
            if idle > float(self.cfg["stale_minutes"]) * 60.0:
                self._finish(state, "abandoned", float(turn.get("last_activity") or turn["started_at"]), payload)
            elif pid and pid == turn.get("prompt_id"):
                # A queued message handed to Claude mid-turn.
                est = self._estimate(self._current_context(state, payload, feats))
                turn.setdefault("joined", []).append(
                    {"at": self.now, "features": feats, "p10": est["p10"], "p50": est["p50"], "p90": est["p90"]}
                )
                self._end_wait(turn, self.now)
                self._touch(turn)
                return
            else:
                # A new prompt while the previous turn never stopped: it was interrupted
                # (Esc fires no hook). Its last sign of life is the best end time we have.
                self._finish(state, "interrupted", float(turn.get("last_activity") or turn["started_at"]), payload)

        context = self._current_context(state, payload, feats)
        turn_id = pid or f"{paths.safe_name(str(payload.get('session_id')))}-{int(self.now * 1000)}"
        previous = state.get("turn")
        if isinstance(previous, dict) and previous.get("turn_id") == turn_id:
            turn_id = f"{turn_id}-{int(self.now * 1000)}"
        state["turn"] = {
            "turn_id": turn_id,
            "prompt_id": pid,
            "status": "running",
            "started_at": self.now,
            "last_activity": self.now,
            "features": feats,
            "project": context["project"],
            "model": context["model"],
            "effort": context["effort"],
            "permission_mode": payload.get("permission_mode"),
            "estimate": self._estimate(context),
            "joined": [],
            "tools": 0,
            "tool_ms": 0,
            "tasks_created": [],
            "tasks_completed": [],
            "first_task_at": None,
            "waiting_since": None,
            "waiting_s": 0.0,
        }

    def _on_tool(self, state: Dict[str, Any], payload: Dict[str, Any]) -> Optional[bool]:
        # Tool events after Stop are ignored: they come from background agents or
        # shells that outlive the turn. A turn that genuinely carries on (because
        # another plugin's Stop hook blocked the stop) is extended by its next Stop.
        turn = self._running(state)
        if turn is None or not self._matches(turn, payload):
            return False
        turn["tools"] = int(turn.get("tools") or 0) + 1
        duration_ms = payload.get("duration_ms")
        if isinstance(duration_ms, (int, float)):
            turn["tool_ms"] = int(turn.get("tool_ms") or 0) + int(duration_ms)
        effort = _effort_from(payload)
        if effort:
            turn["effort"] = state["effort"] = effort
        # A subagent working in parallel says nothing about whether you have
        # answered a prompt in the main conversation.
        if not payload.get("agent_id"):
            self._end_wait(turn, self.now)
        self._touch(turn)
        return None

    def _on_wait(self, state: Dict[str, Any], payload: Dict[str, Any]) -> Optional[bool]:
        turn = self._running(state)
        if turn is None or not self._matches(turn, payload):
            return False
        if turn.get("waiting_since") is None:
            turn["waiting_since"] = self.now
        self._touch(turn)
        return None

    def _on_task_created(self, state: Dict[str, Any], payload: Dict[str, Any]) -> Optional[bool]:
        turn = self._running(state)
        task_id = payload.get("task_id")
        if turn is None or task_id is None or not self._matches(turn, payload):
            return False
        created = turn.setdefault("tasks_created", [])
        if str(task_id) not in created:
            created.append(str(task_id))
        if turn.get("first_task_at") is None:
            turn["first_task_at"] = self.now
        self._touch(turn)
        return None

    def _on_task_completed(self, state: Dict[str, Any], payload: Dict[str, Any]) -> Optional[bool]:
        turn = self._running(state)
        task_id = payload.get("task_id")
        if turn is None or task_id is None or not self._matches(turn, payload):
            return False
        completed = turn.setdefault("tasks_completed", [])
        if str(task_id) not in completed:
            completed.append(str(task_id))
        self._touch(turn)
        return None

    def _on_stop(self, state: Dict[str, Any], payload: Dict[str, Any]) -> Optional[bool]:
        turn = self._running(state)
        if turn is None:
            turn = state.get("turn")
            pid = payload.get("prompt_id")
            if (
                not isinstance(turn, dict)
                or turn.get("status") != "done"
                or turn.get("outcome") != "completed"
                or not pid
                or pid != turn.get("prompt_id")
                or self.now <= float(turn.get("ended_at") or 0) + _REOPEN_GRACE_S
            ):
                return False
            # A second Stop for the same prompt: another plugin's Stop hook kept
            # Claude working. Extend the turn; the new record supersedes the old.
            turn["status"] = "running"
            turn["reopened"] = True
            turn["waiting_since"] = None
        elif not self._matches(turn, payload):
            return False
        effort = _effort_from(payload)
        if effort:
            turn["effort"] = state["effort"] = effort
        try:
            model = transcript.last_model(payload.get("transcript_path"))
        except Exception:  # noqa: BLE001 - the model is a nice-to-have
            model = None
        if model:
            turn["model"] = turn.get("model") or model
            state["model"] = model
        background = payload.get("background_tasks")
        if isinstance(background, list):
            turn["background_tasks"] = len(background)
        self._finish(state, "completed", self.now, payload)
        return None

    def _on_stop_failure(self, state: Dict[str, Any], payload: Dict[str, Any]) -> Optional[bool]:
        turn = self._running(state)
        if turn is None or not self._matches(turn, payload):
            return False
        self._finish(state, "failed", self.now, payload)
        return None

    def _on_session_end(self, state: Dict[str, Any], payload: Dict[str, Any]) -> Optional[bool]:
        turn = self._running(state)
        if turn is not None:
            self._finish(state, "abandoned", float(turn.get("last_activity") or turn["started_at"]), payload)
        return False

    def _on_model(self, state: Dict[str, Any], payload: Dict[str, Any]) -> Optional[bool]:
        model = payload.get("to_model")
        if not isinstance(model, str):
            return False
        state["model"] = model
        turn = self._running(state)
        if turn is not None:
            turn["model"] = model
        return None


def housekeeping(data: Path, now: float, max_records: int = 20000, session_days: float = 14.0) -> None:
    """Drop session files for long-gone sessions and trim an oversized history.

    Runs at session start. Both jobs are cheap when there is nothing to do.
    """
    cutoff = now - session_days * 86400.0
    try:
        entries = list(paths.sessions_dir(data).iterdir())
    except OSError:
        entries = []
    for entry in entries:
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
        except OSError:
            continue

    history = paths.history_path(data)
    try:
        size = history.stat().st_size
    except OSError:
        return
    if size <= max_records * _BYTES_PER_RECORD * 1.5:
        return
    with locked(data / "history.lock"):
        records = read_jsonl(history)
        if len(records) <= max_records:
            return
        kept = records[-max_records:]
        atomic_write_text(
            history,
            "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in kept),
        )


def read_session(data: Path, session_id: str) -> Optional[Dict[str, Any]]:
    state = read_json(paths.sessions_dir(data) / f"{paths.safe_name(session_id)}.json", default=None)
    return state if isinstance(state, dict) else None
