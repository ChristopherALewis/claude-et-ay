# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Christopher Lewis
"""Turn a prompt into a handful of numbers and labels.

The prompt text itself is never stored. Only the features below reach disk, and
none of them can reconstruct what was typed.
"""

from __future__ import annotations

import re
from typing import Any, Dict

from . import paths

_PASTE = re.compile(r"<pasted_content\b")
_FENCE = re.compile(r"^\s*```", re.MULTILINE)
_MENTION = re.compile(r"(?:^|\s)@[\w./~-]+")
_URL = re.compile(r"https?://\S+")
_SLASH = re.compile(r"^\s*/([A-Za-z0-9][\w:.-]*)")
_NOTIFICATION = re.compile(r"^\s*<(task-notification|system-reminder|scheduled-task|agent-message|cross-session)", re.I)

# Rough intent cues. They only nudge which past turns count as similar.
_QUESTION_START = re.compile(
    r"^\s*(what|why|how|when|where|which|who|is|are|can|could|does|do|should|would|explain|tell me)\b",
    re.I,
)
_TASK_WORDS = re.compile(
    r"\b(implement|build|create|add|write|refactor|fix|migrate|rename|update|upgrade|port|"
    r"convert|delete|remove|set up|setup|configure|deploy|test|debug|optimi[sz]e|rewrite|"
    r"run|install|review|analy[sz]e|investigate|generate|make|use|move|clean up|tidy)\b",
    re.I,
)
_BIG_WORDS = re.compile(r"\b(all|every|entire|whole|across|codebase|repo|repository|project-wide)\b", re.I)


_COMMAND_NAME = re.compile(r"^[a-z0-9][a-z0-9:_-]{0,63}$")


def _command_name(raw: str) -> str:
    """The slash command name, or a hash of it if it does not look like one.

    Commands are lower-case kebab names. Anything else (a sentence that happens
    to start with a slash, say) is stored only as a hash, which still lets
    repeated runs be matched without keeping the words.
    """
    return raw if _COMMAND_NAME.match(raw) else "#" + paths.short_hash(raw, 10)


def extract(prompt: str) -> Dict[str, Any]:
    text = prompt or ""
    stripped = text.strip()
    slash = _SLASH.match(stripped)
    if _NOTIFICATION.match(stripped):
        kind = "notification"
    elif slash:
        kind = "command"
    elif _TASK_WORDS.search(stripped):
        kind = "task"
    elif stripped.endswith("?") or _QUESTION_START.match(stripped):
        kind = "question"
    else:
        kind = "task" if len(stripped) > 280 else "chat"
    return {
        "chars": len(text),
        "words": len(stripped.split()),
        "lines": text.count("\n") + 1 if text else 0,
        "code_blocks": len(_FENCE.findall(text)) // 2,
        "mentions": len(_MENTION.findall(text)),
        "urls": len(_URL.findall(text)),
        "pasted": bool(_PASTE.search(text)),
        "broad": bool(_BIG_WORDS.search(stripped)),
        "command": _command_name(slash.group(1)) if slash else None,
        "kind": kind,
    }
